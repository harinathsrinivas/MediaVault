# Task: Replace text-blob dummy with a tiny, valid, Plex-indexable video on `replace`

Suggested branch: feature/video_dummy

## Context

`cmd_replace` (main.py:755-806) currently writes a ~80 byte plain-text file (just `Original Hash: ...\n` and optional `Status: SPLIT ...`) and renames it onto the original filename so Plex/Emby/Jellyfin keep their library row pointed at the now-archived path. The problem: Plex/Emby/Jellyfin sometimes drop the item on rescan because the placeholder is not a parseable video container. The user has confirmed that replacing the dummy with a real (trailer-sized) video that has proper headers makes all three media servers happy again. The catalog-side dummy-detection threshold today is `os.path.getsize(...) < 1024` (used in `cmd_prep` line 303 and `cmd_check` line 524) — that threshold must be raised once dummies become real videos in the 10-100 KB range.

## Goal

After `replace` (and every command that calls `cmd_replace`), the file left in place of the archived original is a **valid, tiny (target 10-100 KB) video container** of the same filename and extension as the original, with proper headers/duration/resolution metadata so Plex/Emby/Jellyfin index it. When the original is still on disk at replace time it is used as the source for the tiny dummy (so server-generated thumbnails resemble the real content); otherwise a generic black-frame fallback is used. A new `repair_dummies` subcommand walks already-archived entries on disk and upgrades their old text-blob dummies to the new video format. `ffmpeg` is the tool; if it's missing, the commands fail clearly without falling back to the broken old behavior. The 1024-byte dummy-sniff threshold is updated to accommodate the new dummy size while still distinguishing dummies from real media.

## Files affected

- `C:\Users\harin\PycharmProjects\MediaVault\main.py` — add `FFMPEG_PATH` constant, add `make_video_dummy(...)` helper, rewrite `cmd_replace` to call it, add `cmd_repair_dummies`, raise the dummy-size threshold in `cmd_prep` and `cmd_check`, wire the new subcommand into the argv dispatcher and the usage block.
- `C:\Users\harin\PycharmProjects\MediaVault\ARCHITECTURE.md` — section 7.6 (dummy/replace flow) and section 14 (constants table: dummy threshold, new `FFMPEG_PATH`), plus section 5 subcommand table and section 18 quick-reference row. Final step.
- `C:\Users\harin\PycharmProjects\MediaVault\README.md` — subcommand reference table gets `repair_dummies`, the external-dependencies table gets `ffmpeg`. Final step.

No changes to `mainfetch.py`, library JSON schemas, sidecar files, push, restore, fetch, split/merge, or any other command path.

## Approach

`replace` already has the right shape: build a sibling temp file, then atomically rename onto the original. We keep that envelope and only swap the "what goes in the temp file" implementation: instead of `open(dummy, 'w').write("Original Hash: ...")`, call a new `make_video_dummy(source_path, output_path, extension)` helper that shells out to ffmpeg. The helper has two modes:

1. **Derived-from-source** (when the original is still on disk): clip the first 1 second of the source, re-encode to a very small H.264 + silent AAC at low resolution/bitrate, mux into a container matching the target extension. Server-side thumbnail generation will then produce a frame that looks like the real movie/episode.
2. **Generic fallback** (when there is no source — repair scenario): synthesize a 1-second black 128x72 H.264 frame + silent AAC track and mux into the target extension.

Both modes target ~10-100 KB. The .mkv/.mp4 container can hold H.264 + AAC; for .avi/.mov, the same H.264+AAC stream still produces a valid file that Plex parses. ffmpeg is detected at command entry (`shutil.which("ffmpeg")` with a hardcoded Windows fallback) and the command bails clearly if missing — no silent fallback to the old text-blob dummy.

`cmd_repair_dummies` walks the merged library, picks entries whose `status == "archived"` and whose on-disk file is `< NEW_DUMMY_THRESHOLD` (i.e. is a dummy), and rewrites that file using the generic-fallback path (the original 80 GB file is by definition gone for archived entries). It does not touch library state — only the placeholder file on disk changes. It supports an optional ID prefix filter so the user can scope it (e.g., `repair_dummies tv-en-2016-strangerthings`).

The dummy-size sniff threshold (currently 1024 in `cmd_prep` and `cmd_check`) is raised to `DUMMY_MAX_BYTES = 200_000` (200 KB). Real media is always orders of magnitude larger (smallest realistic episode is hundreds of MB); the new ceiling cleanly separates dummies (10-100 KB target, with headroom for container overhead) from real video.

## Steps

### 1. [model: sonnet] [candidates: 2] Investigate ffmpeg encoder parameters that reliably produce a 10-100 KB valid dummy

- Files: none (research step; outcomes feed step 2 — written as a comment block / docstring in `make_video_dummy`)
- Details:
  - Goal: nail down exact ffmpeg argument vectors for two cases — (a) derive from source file, (b) generic black-frame fallback — that produce a Plex/Emby/Jellyfin-indexable container under 100 KB in `.mkv`, `.mp4`, `.avi`, and `.mov`.
  - For each extension, decide: video codec + profile + level (likely H.264 baseline), pixel format (yuv420p), resolution (likely 128x72 or 160x90), framerate (1 fps or 2 fps), GOP/keyint, audio codec (likely AAC LC, mono, 8 kHz, 16 kbps), audio source (silence via `anullsrc` for fallback; copy + downmix + reduce for derived), duration (1 second), `-movflags +faststart` for mp4/mov, `-y` to overwrite.
  - Test each candidate by running the ffmpeg invocation locally against (i) a real `.mkv` from `C:\Media\Movies` and (ii) a synthesized black frame, then verify the output: file exists and `ffprobe -v error <out>` returns no errors, has `Video: h264` and `Audio: aac` streams visible to ffprobe, size between 5 KB and 150 KB.
  - Record final parameter strings in a comment block at the top of `make_video_dummy` for future reference.
- Acceptance: Concrete, tested ffmpeg argv lists for both modes and all 4 extensions, each producing a sub-100 KB file that ffprobe accepts as a valid container with video+audio streams. Document the realistic floor if 100 KB cannot be hit for any specific extension.
- Judge criteria (ranked):
  1. Output file is recognised as a video by Plex/Emby/Jellyfin's library-scan heuristics (proxy: ffprobe reports a duration, a resolution, a video codec, and a non-empty audio track).
  2. Output file size is 10-100 KB across `.mkv`, `.mp4`, `.avi`, `.mov`.
  3. Encoder invocation is fast (<2 seconds) and works on the user's existing ffmpeg without unusual codec dependencies (i.e., uses only libx264 + AAC, no esoteric encoders).
  4. Derived-mode output's first frame visually resembles the source (so server-generated thumbnails still look like the real movie).
- Candidate approaches:
  - A: **Single ffmpeg invocation per dummy** that handles container, video, and audio in one pass. Derived mode: `ffmpeg -ss 0 -i <src> -f lavfi -i anullsrc=cl=mono:r=8000 -t 1 -map 0:v:0 -map 1:a:0 -c:v libx264 -preset ultrafast -tune zerolatency -profile:v baseline -level 3.0 -pix_fmt yuv420p -vf "scale=128:72,fps=2" -c:a aac -b:a 16k -shortest <out>`. Fallback mode: `ffmpeg -f lavfi -i color=c=black:s=128x72:d=1:r=2 -f lavfi -i anullsrc=cl=mono:r=8000 -shortest -c:v libx264 -profile:v baseline -pix_fmt yuv420p -c:a aac -b:a 16k -t 1 <out>`. One subprocess call per dummy.
  - B: **Two-stage approach**: stage 1 produces a video-only minimal stream into a temp file with ffmpeg, stage 2 muxes the temp video + an `anullsrc`-generated AAC into the final container with a second ffmpeg invocation. Slower but each stage is independently inspectable and the per-stage failure modes are easier to debug if a specific container rejects the combination.

### 2. [x] [model: sonnet] Add ffmpeg detection and the `make_video_dummy` helper

- Files: `C:\Users\harin\PycharmProjects\MediaVault\main.py`
- Details:
  - Add a config constant near `MKVMERGE_PATH` (around line 26): `FFMPEG_PATH = r"C:\Program Files\ffmpeg\bin\ffmpeg.exe"`. Add a small `resolve_ffmpeg()` utility that returns `FFMPEG_PATH` if it exists, else `shutil.which("ffmpeg")`, else `None`. Do NOT raise from this resolver — callers decide what to do on None.
  - Add `DUMMY_MAX_BYTES = 200_000` constant near `VIDEO_EXTENSIONS`.
  - Add `make_video_dummy(output_path, extension, source_path=None)` near the existing `split_video_file` / `merge_video_files` helpers (around line 270). It:
    1. Resolves ffmpeg via `resolve_ffmpeg()`. Returns `False` and prints a clear error if ffmpeg is missing (caller decides whether to abort the whole command).
    2. Writes to a sibling temp path (e.g., `<output_path> + ".dummy_tmp" + <ext>`) so a half-written file never leaks if ffmpeg crashes.
    3. If `source_path` is provided and the file exists and is larger than `DUMMY_MAX_BYTES` (i.e., it's a real video, not a dummy itself), runs the **derived-mode** ffmpeg argv chosen in step 1.
    4. Else runs the **generic-fallback** ffmpeg argv chosen in step 1.
    5. On non-zero exit or missing output, prints the ffmpeg stderr tail and returns `False`. On success, `os.replace`s the temp path to `output_path` and returns `True`.
  - Use `subprocess.run([...], capture_output=True, text=True)` — do not stream stdout. Pass `-loglevel error -nostdin -y`.
  - Match existing style: emoji prints (`🎬`, `✅`, `❌`), no logging module.
- Acceptance: Calling `make_video_dummy("test.mkv", ".mkv", source_path=<real mkv>)` produces a 10-100 KB `.mkv` that `ffprobe` accepts (manual smoke test). Calling it with `source_path=None` produces a similar-size valid file. Calling with a missing ffmpeg returns False and prints a clear error.

### 3. [model: sonnet] Rewrite `cmd_replace` to use the video dummy

- Files: `C:\Users\harin\PycharmProjects\MediaVault\main.py` (lines 755-806)
- Details:
  - Replace the `open(dummy, 'w').write(...)` block with a call to `make_video_dummy(temp_path, extension_of_filename, source_path=original)`.
  - The temp filename stays inside `local_folder` so it shares the volume with the eventual rename target (atomic `os.rename` requires same volume). Use `<filename> + ".dummy_tmp" + <ext>` (where ext is taken from the original filename) so ffmpeg uses the right container.
  - If `make_video_dummy` returns False (ffmpeg missing or failed), print the error and return `False` immediately — DO NOT fall back to the old text-blob dummy. Leave the original file untouched. This is intentional: a broken dummy is worse than no dummy because Plex would then drop the entry.
  - Source preference: when `os.path.exists(original) and os.path.getsize(original) >= DUMMY_MAX_BYTES`, pass it as `source_path`. Otherwise pass `source_path=None` (the original is already a dummy from an earlier replace, so derive-mode would loop).
  - **Build the dummy BEFORE deleting the original** — derive-mode needs the source. Current code creates the (text) dummy first too, but the new flow makes this ordering load-bearing. Keep the 3-retry delete loop as-is; keep the final `os.rename(temp_path, original)` as the atomic swap step.
  - Keep `library[manual_id]["status"] = "archived"` and `save_library(library)`. The library hash field is NOT updated — it still holds the original file's pre-archive SHA256 (which is what `restore` will verify against later). This matches the existing contract (the dummy has never matched `entry["hash"]`).
- Acceptance: `python main.py replace <id>` on a real local_ready (then force-uploaded) test entry produces a 10-100 KB video file at the original path; `ffprobe` reports it as valid; library status flips to `archived`; running `check` on the same id afterward correctly identifies it as a dummy (after step 5 raises the threshold).

### 4. [model: sonnet] Add `cmd_repair_dummies` and wire it into argv dispatch

- Files: `C:\Users\harin\PycharmProjects\MediaVault\main.py`
- Details:
  - Add `cmd_repair_dummies(prefix_filter=None)` next to `cmd_replace_group` (around line 820).
  - It loads the merged library, iterates entries skipping `type == "season_map"` and entries whose ID doesn't start with `prefix_filter` (when provided).
  - For each candidate leaf entry where `entry.get("status") == "archived"`:
    - Build `current_path = os.path.join(entry['folder_path'], entry['filename'])`.
    - Skip if the file doesn't exist (already missing — print a warning, don't crash).
    - Skip if `os.path.getsize(current_path) >= DUMMY_MAX_BYTES` — this is either a real restored file or already a healthy new-style dummy that crossed the threshold by accident; either way, do not touch.
    - Old-text-blob dummies are < 1 KB; new-video dummies are 10-100 KB. Both are below `DUMMY_MAX_BYTES`. To distinguish them, read the first 16 bytes of the file: if they look like ASCII text starting with `b"Original Hash"`, it's an old-style dummy and needs upgrade. Otherwise leave it alone.
    - For old-style dummies: extract extension from filename, call `make_video_dummy(tmp_path, ext, source_path=None)` (no source — original is gone), then `os.remove(current_path)` and `os.rename(tmp_path, current_path)`.
  - Print a final summary: scanned N, upgraded M, skipped K (already video), missing F.
  - Wire into the usage block (line ~1390) as `repair_dummies [optional: id_prefix]` and into the `if/elif` chain (around line 1518, between `replace_group` and `verify_restore`) as a new `elif cmd == "repair_dummies":` that reads `sys.argv[2]` as the optional prefix filter (None if absent).
- Acceptance: Running `repair_dummies` on a library with at least one known old-text-blob dummy upgrades that file to a 10-100 KB valid video, leaves real archived dummies (new format) untouched on a re-run (idempotent), and prints accurate counts. Running with a prefix filter only touches matching IDs.

### 5. [x] [model: haiku] Raise the dummy-size sniff threshold in `cmd_prep` and `cmd_check`

- Files: `C:\Users\harin\PycharmProjects\MediaVault\main.py` (lines 303, 524)
- Details:
  - At line 303, replace `if os.path.getsize(filepath) < 1024:` with `if os.path.getsize(filepath) < DUMMY_MAX_BYTES:`.
  - At line 524, replace `if os.path.getsize(file_path) < 1024:` with `if os.path.getsize(file_path) < DUMMY_MAX_BYTES:`.
  - Do not change any other `1024` literals — they are size-unit math (`1024 * 1024`, etc.), unrelated.
- Acceptance: Grep for `< 1024` shows zero remaining hits in dummy-detection paths; only multiplier usage remains. `cmd_prep` correctly skips a new-style 50 KB video dummy with the "Dummy file detected" message. `cmd_check` does the same.

### 6. [x] [model: haiku] Update the in-script usage block

- Files: `C:\Users\harin\PycharmProjects\MediaVault\main.py` (lines 1390-1414)
- Details: Add a single line `print("  repair_dummies [optional: id_prefix]")` immediately after the `replace_group [id]` line. No other changes to the usage block.
- Acceptance: `python main.py` with no args lists `repair_dummies` between `replace_group` and `verify_restore`.

### 7. [model: sonnet] End-to-end smoke verification

- Files: none (manual verification step)
- Details:
  - Pick a small `local_ready` entry, run `set_uploaded`, then `replace`. Confirm the on-disk file is between 10 KB and 100 KB, ffprobe parses it.
  - Run `python main.py check <id>` and confirm it correctly reports "Dummy file detected" with the new threshold.
  - Run `python main.py repair_dummies <prefix that matches one old-style entry>` (if any old-style dummies still exist on the live system) and verify the file is upgraded; run it again and confirm it's a no-op (idempotent).
  - Run `python main.py replace <id-with-ffmpeg-missing>` after temporarily renaming the resolved ffmpeg binary to confirm clean failure (no broken dummy left on disk; original still in place).
- Acceptance: All four sub-checks pass on the user's actual environment.

### 8. [model: sonnet] Ask architect agent to update `ARCHITECTURE.md` and update `README.md`

- Files: `C:\Users\harin\PycharmProjects\MediaVault\ARCHITECTURE.md`, `C:\Users\harin\PycharmProjects\MediaVault\README.md`
- Details:
  - Orchestrator: dispatch to the architect agent with a summary of what changed (`cmd_replace` now writes a valid tiny video via ffmpeg, new `repair_dummies` subcommand, new `FFMPEG_PATH` and `DUMMY_MAX_BYTES` constants, dummy threshold raised from 1024 to 200 000). Architect should update:
    - Section 5 (subcommand table): add `repair_dummies` row.
    - Section 7.6 (Placeholder / dummy-file system): rewrite to describe the new ffmpeg-based dummy generation, the derived-vs-fallback mode selection, and the explicit no-fallback-on-ffmpeg-missing policy.
    - Section 11 (External Integrations Summary): add ffmpeg row.
    - Section 14 (Configuration constants table): add `FFMPEG_PATH`, `DUMMY_MAX_BYTES`; replace the old "Dummy-file threshold: 1024 bytes" row.
    - Section 18 (Quick reference): update the "How dummies are formatted" row to point at `make_video_dummy`.
  - README update: add `repair_dummies` to the subcommand table; add `ffmpeg` to the external-dependencies table with note that it is required by `replace` and `repair_dummies` and must be on PATH or at the hardcoded `FFMPEG_PATH`. Update the `replace` row's description to "Swap original with a tiny valid video placeholder" instead of "tiny dummy placeholder".
- Acceptance: ARCHITECTURE.md and README.md both reflect the new behavior; the old "1024 bytes" / "text dummy" descriptions no longer appear; ffmpeg appears in both dependency tables.

## Risks and edge cases

- **ffmpeg parameter choice can over-produce.** Some container/extension combinations may refuse very-low-bitrate AAC or push past 100 KB due to fixed container overhead (especially `.mov` with `+faststart`). Step 1 explicitly tests all 4 extensions; if `.avi` cannot get under 100 KB at the chosen H.264 settings, document the realistic floor in the helper's docstring rather than failing the step.
- **Atomic-rename across volume boundaries.** `make_video_dummy` writes the temp file in the same folder as the target, so the final `os.rename` stays within one volume. The 3-retry delete-then-rename pattern from the existing code is preserved.
- **Plex/Emby/Jellyfin index timing.** A scan in flight while replace runs could see the dummy mid-rename. This is the same exposure as today (the existing flow also has a brief window). Out of scope for this change.
- **Identifying old-style dummies in step 4.** The "first 16 bytes start with `Original Hash`" check is safe because every old dummy `cmd_replace` ever wrote begins with that literal string. Real video containers all start with binary magic bytes (`\x1A\x45\xDF\xA3` for MKV, `....ftyp` for MP4/MOV, `RIFF` for AVI) — none collide with ASCII `Original Hash`. A near-zero-byte truncated dummy would also be flagged as old-style, which is fine because we want to upgrade those too.
- **Derive-mode source might itself be a dummy.** Guarded by `os.path.getsize(original) >= DUMMY_MAX_BYTES` before passing as `source_path`. If somehow exceeded by a malformed dummy, ffmpeg would error out and the command would abort cleanly without writing.
- **The library hash never matches the dummy.** Already true today; no code path expects it to. `cmd_restore` reads from `restore/`, not from the in-place dummy. `cmd_check` short-circuits on dummy. Confirmed by re-reading `cmd_check` (main.py:524) and `cmd_restore` (main.py:895+).
- **`prep_push_rep` and `prep_push_rep_season` cover.** Both call `cmd_replace` internally (lines 1270, 1322, 1335). No additional wiring needed.
- **Windows file-locking on ffmpeg output.** Plex/Windows Search opening the file mid-encode shouldn't matter because we encode to a `.dummy_tmp` sibling that no scanner is watching, then atomically `os.rename` over the original. The existing 3-retry chmod loop handles the delete-original case.
- **`.temp_dummy` legacy filter in `cmd_scan_unprepped`** (line 1199) still skips files ending in `.temp_dummy`. The new code uses `.dummy_tmp<ext>` (not `.temp_dummy`), and we don't add a new filter — the temp file only exists during a single replace call and is renamed away. This is intentional; no new exclusion is needed.

## Verification

Run from the repo root after all steps complete:

```
python main.py
```
(should list `repair_dummies` in the usage)

```
python main.py replace <some-archived-id-after-set_uploaded-from-a-local_ready-test-fixture>
```
Then in PowerShell:
```
Get-Item "<path-to-archived-file>" | Select-Object Length
ffprobe -v error -show_streams "<path-to-archived-file>"
```
Expect: Length between 10240 and 102400; ffprobe shows one video stream and one audio stream and exits with code 0.

```
python main.py check <same-id>
```
Expect: "Dummy file detected" message; no hash mismatch error.

```
python main.py repair_dummies
```
On a library with at least one known old-text-blob dummy, expect it to be upgraded; running again should report 0 upgraded.

Negative test:
```
# Temporarily rename ffmpeg.exe out of PATH and out of the hardcoded FFMPEG_PATH
python main.py replace <some-other-test-id>
```
Expect: clear "ffmpeg not found" error; original file still present; library status unchanged.

## Out of scope

- No changes to push, fetch, restore, split/merge, library schema, sidecar files, or hash bookkeeping.
- No automated test suite (the project has none; "verification" is manual smoke per the README).
- No `--dry-run` flag for `repair_dummies`, no config-file or CLI override for `FFMPEG_PATH` (matches the codebase convention of hardcoded constants).
- No removal of the legacy 1024 literal in any other context where 1024 is unit math.
- No changes to how `entry["hash"]` is stored or compared (per the mkvmerge-hash-divergence memo, that contract is unchanged).
- No `verify_library` / `repair_library` orphan-parent work — that is the separate deferred task tracked in `project_followup_library_integrity.md`.
- No update to `requirements.txt` (ffmpeg is a system binary, not a pip package).
- No changes to `mainfetch.py`.
