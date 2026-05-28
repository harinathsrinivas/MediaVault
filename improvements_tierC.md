# Improvements — Tier C · Robustness & Reliability

> The category that `usage_commands.txt` actually screams for. Most repeated commands in your history are re-runs after a partial failure. This tier addresses the specific failure modes that cause those re-runs.

> **Cross-cutting context:**
> - Auto-pilots today stop on first failure (`break` in `cmd_prep_push_rep_season` line 1338 "to prevent mess"). User then re-types the command with a narrower `episodes A-B` range. Examples in your history: Mr Robot S02 `episodes 1-10` then `episodes 11-13`; Battlestar Galactica `episodes 1-10` then `episodes 11-11`.
> - The 3-retry pattern in `cmd_replace` (lines 781-794) for `PermissionError` is the only place in the codebase with retries. ADB push and Selenium operations have zero retry logic.
> - The Aindham Vedham orphan ([[project_followup_library_integrity]]) is the only known library integrity gap. Today's code would not produce it, but no command exists to AUDIT for similar drift.
> - `cmd_set_uploaded` (lines 451-464) is a pure metadata override with no ADB-side sanity check.
> - mainfetch's `init_driver` returns None on failure and `cmd_fetch_route` exits cleanly, but trigger_download swallows per-chunk exceptions without escalating to "the session is dead, stop".

---

## IMP-C1: Auto-resume from last completed episode in cmd_prep_push_rep_season

- Category: other
- Priority: high
- Files: `main.py` — `cmd_prep_push_rep_season` (1279-1340); new `progress.json` schema written into the season folder or under `~/.mediavault/state/`
- Current behavior: On any push failure, the loop breaks at line 1338 ("Stopping Auto-Pilot to prevent mess") and the user has to manually re-issue the command with a narrower `episodes A-B` range corresponding to the un-pushed episodes. This pattern recurs throughout `usage_commands.txt` (Mr Robot S02, Battlestar Galactica S01, The Wire S01/S02/S03/S04, Peaky Blinders S05).
- Proposed change:
  - At each step in `cmd_prep_push_rep_season`, before processing episode `mid`, write `<season_folder>/.mediavault_progress.json` with `{ "base_id": ..., "last_completed_ep": "<mid>", "started_at": "...", "status": "in_progress" }`.
  - On any failure, write `status: "failed", last_attempt: <ep>, failure_reason: "<message>"` and exit.
  - On invocation, BEFORE prep_season, check for an existing `.mediavault_progress.json`:
    - If found AND `status="in_progress"` or `"failed"`: print a banner ("Resuming from episode N, last attempt failed because Y"), and start the loop AFTER the last completed episode.
    - If found AND `status="complete"`: archive the file (rename with timestamp) and start fresh.
    - Add `--restart` flag to ignore the progress file and run from the user's specified range.
  - On final success of the whole season, write `status: "complete"`.
- Rationale: This is the SINGLE highest-frequency pain point in usage_commands.txt. Eliminating manual range-narrowing after failures saves the user 30+ seconds of typing per failure event and removes the cognitive load of "which episodes were already done".
- Goal: A failed batch run is automatically resumable by re-running the SAME command, no edits.
- Effort estimate: medium
- Status: pending

---

## IMP-C2: Exponential-backoff retry logic for ADB and Selenium ops

- Category: other
- Priority: high
- Files: `main.py` — `cmd_push` (around line 668 `adb push` call), `cmd_replace` (already retries — extend pattern); `mainfetch.py` — `trigger_download` (148-214), driver attach in `init_driver` (139-145)
- Current behavior:
  - `adb push` (line 668): single attempt, on `CalledProcessError` the loop breaks and the whole push fails. Transient USB hiccups (phone screen locks, USB cable reseat) take down a whole season auto-pilot.
  - `trigger_download` (line 212): catches all exceptions, prints "⚠️ Error:", returns False. The harvester loop then has nothing to harvest for that chunk. No retry.
  - `cmd_replace` (line 782-794): already retries 3 times with 1-second sleep for `PermissionError`. This is the only retry pattern in the codebase.
- Proposed change:
  - Add a shared `retry(callable, attempts=3, backoff=(1, 4, 16), retry_on=(SubprocessError, TimeoutError))` decorator/helper in `mvcommon.py`.
  - Wrap `adb push` calls with retry. On transient `CalledProcessError`, retry with exponential backoff (1 s, 4 s, 16 s). Total worst-case extra time = 21 s per chunk before final failure.
  - Wrap the inner body of `trigger_download` similarly — if the search returns 0 thumbnails OR clicking fails, retry once after 5 s before returning False.
  - All retry counts/backoffs configurable under IMP-A5 config.
- Rationale: USB and browser-automation are inherently flaky. Three quick retries catches the vast majority of transient blips ("phone screen locked during push") that today require human intervention.
- Goal: 95% of transient failures self-heal without user touch.
- Effort estimate: medium
- Status: pending

---

## IMP-C3: Pre-flight health check command `doctor`

- Category: other
- Priority: high
- Files: new `cmd_doctor` in `main.py`; new subcommand in argparse (IMP-A2)
- Current behavior: There is no way to verify the environment is sane before starting a long operation. A 3-hour `prep_push_rep_season` can fail 5 minutes in because `mkvmerge` is missing, because ADB doesn't see the phone, because the Chrome profile is logged out, or because `C:\` is full.
- Proposed change:
  - New `python main.py doctor` command that runs in <5 seconds and prints PASS/FAIL/WARN for each check:
    1. `mkvmerge --version` succeeds at `MKVMERGE_PATH`.
    2. `adb devices` lists exactly one device with state `device` (not `unauthorized`/`offline`).
    3. The three library JSONs exist, parse as JSON, and are < 1 day stale on mtime.
    4. The Chrome profile directories (`ChromeProfile`, `ChromeProfile_TV`) exist.
    5. Free disk space on `C:\Media` (>10 GB warn, >50 GB pass for active prep_push_rep).
    6. Free disk space on `D:\` if any library entry's folder_path starts with `D:\`.
    7. No leftover `_parts/` folders containing chunks for entries marked `archived` (orphaned post-failure state — directly actionable).
    8. No dummy files (<1 KB) for entries marked `local_ready` (means a half-archived file).
    9. The system Downloads folder is not currently full of `.crdownload` files from another tool.
    10. Optional integrity quick-checks: `verify_library` summary (IMP-D4) — count orphans, missing parents.
  - Exit code: 0 if all PASS or WARN, non-zero if any FAIL.
  - `--json` output supported.
- Rationale: Cheap pre-flight catches the failure modes that account for most "ran for 30 minutes, failed at chunk 7" stories.
- Goal: Run `doctor` before any big batch. 80% of mid-run failures become surfaced as pre-flight errors instead.
- Effort estimate: medium
- Status: pending

---

## IMP-C4: ADB device serial pinning

- Category: other
- Priority: medium
- Files: `main.py` — `cmd_push` (`adb shell mkdir` line 569, `adb push` line 668), future ADB-using commands; config key in IMP-A5
- Current behavior: `subprocess.run(["adb", "shell", "mkdir", ...])` and `["adb", "push", ...]` use ADB's default device-selection logic. If exactly one device is connected, it picks that. If multiple devices are connected, ADB fails with "error: more than one device". There is no support for explicitly choosing a device by serial.
- Proposed change:
  - Add config key `adb.device_serial` to `mvconfig.json` (IMP-A5), default `null`.
  - Add `--device <serial>` flag to `push`, `push_group`, `prep_push_rep`, `prep_push_rep_season` (IMP-A2).
  - When set, all `adb` invocations become `adb -s <serial> ...`.
  - `python main.py doctor` (IMP-C3) lists detected devices and recommends a serial to pin.
- Rationale: Today the system happens to work because there is one phone. The moment a second device is plugged in (a tablet, a developer phone, a friend's phone), every ADB command fails. Pinning prevents that and is also the precondition for multi-device push (IMP-E7).
- Goal: Deterministic device selection regardless of how many devices ADB sees.
- Effort estimate: small
- Status: pending

---

## IMP-C5: Real fallback search query (strip UID and extension)

- Category: bug
- Priority: high
- Files: `mainfetch.py` — `fetch_single_entry` (lines 244-277), `trigger_download` (lines 148-214)
- Current behavior: The two-attempt structure (lines 287-365) reuses `entry["search_term"]` for both attempts when the file is non-chunked, just changing the click index. `search_term` is built in `cmd_prep` (line 360) as `<name> [<short_id>]<ext>` — verbose, long, and includes the bracketed UID and extension. Google Photos search is fuzzy and SHORTER queries often find results when the verbatim filename misses (e.g., punctuation differences). Today's "fallback" is not really a fallback.
- Proposed change:
  - Add a `fallback_query_strip()` helper that produces a broader query from a leaf filename:
    - Strip `[<short_id>]` (the bracketed UID).
    - Strip the file extension.
    - Strip trailing tags like `-FraMeSToR`, `[rartv]`, `[Ben The Men]`, `-FGT`, release-group identifiers.
    - Squash dots / underscores / hyphens into spaces.
    - Truncate to ~40 chars.
  - Use this broader query as the SECOND attempt's `fallback_query`. Keep attempt 1 as the precision (full search_term) attempt.
  - Document examples in the codebase: `"F1.The.Movie.2025.2160p.UHD.BluRay.Remux.DV.P7.HDR.MULTi[Ben The Men] [68b7b8].mkv"` → fallback `"F1 The Movie 2025"`.
- Rationale: Today's fallback is identical to the precision query, so it never recovers from a real query failure. A genuinely shorter query has a much higher hit rate on Google Photos' search.
- Goal: Significantly increased fetch success rate on the second attempt, especially for files with verbose filenames or release-group tags.
- Effort estimate: small
- Status: pending

---

## IMP-C6: Detect Google Photos session expiry early

- Category: bug
- Priority: high
- Files: `mainfetch.py` — `cmd_fetch_route` (453-492), `trigger_download` (148-214)
- Current behavior: When `trigger_download` finds 0 thumbnails (line 195-197), it returns False silently. For a multi-chunk movie, this can cascade into "0 of 10 chunks succeeded" with no clear cause. The most likely real-world cause: the Chrome profile's Google session has expired and `photos.google.com` is showing a login screen rather than search results.
- Proposed change:
  - In `trigger_download`, after `driver.get("https://photos.google.com")` and wait, check the page URL or title.
    - If redirected to an `accounts.google.com` login page, raise a `SessionExpiredError`.
    - If the page body contains "Sign in" text without "Photos" content, raise similarly.
  - In `cmd_fetch_route`, catch `SessionExpiredError` at the top level and abort with a clear message: "Chrome profile `{profile}` is logged out. Open Chrome with `--user-data-dir={path}` and log in to Google Photos, then re-run."
  - Additionally: heuristic backstop. If 3 consecutive `trigger_download` calls return 0 thumbnails on the SAME profile, raise the same error.
- Rationale: Silent session-expiry is the failure mode that wastes the most user time — they wait 90 minutes for a fetch that never had a chance.
- Goal: Session-expiry fails fast with a clear remediation. No 90-minute wait on a doomed run.
- Effort estimate: small
- Status: pending

---

## IMP-C7: cmd_set_uploaded should verify via ADB

- Category: bug
- Priority: medium
- Files: `main.py` — `cmd_set_uploaded` (451-464)
- Current behavior: Pure metadata override. Sets `uploaded=True, status="onboarded"` with no check that the chunks are actually on the phone (or in Google Photos). If misused, the user can mark something uploaded that isn't, then run `replace` and delete the only copy.
- Proposed change:
  - Before flipping the flags, do a single `adb shell ls /sdcard/Media/<rel_path>/` and parse the listing.
  - For a split entry, confirm that at least N-1 of N expected chunks are listed remotely (allow one missing chunk because Photos may have already cleaned up after upload).
  - For a single-file entry, confirm the renamed file `<name> [<short_id>]<ext>` exists remotely.
  - If the check fails, abort with an error. Add `--force` flag to skip the check for the genuine emergency-rescue case.
- Rationale: This command is the most dangerous in the codebase — it short-circuits the upload-confirmation safety. A misclick or wrong-id paste could make `replace` delete the only copy of a 70 GB file. A 1-second ADB check is cheap insurance.
- Goal: Convert `set_uploaded` from a foot-cannon into a verified, safe override.
- Effort estimate: small
- Status: pending

---

## IMP-C8: Post-push remote verification

- Category: other
- Priority: medium
- Files: `main.py` — `cmd_push` (after successful `adb push`, ~line 668-690)
- Current behavior: After `adb push -p <local> <remote>` returns success, the local file is deleted (if in `_parts/`) and the loop continues. There is no verification that the remote file's bytes match the local file's bytes. ADB push uses ADB's own integrity checks but is not bulletproof against certain USB cable / driver issues.
- Proposed change:
  - After each successful `adb push`, optionally run `adb shell md5sum <remote_path>` (or `sha256sum` if available on the device).
  - Compare the device-side hash to the local chunk hash (already computed and stored in `split_info.chunks[i].hash`).
  - On mismatch, treat the push as failed and retry under IMP-C2.
  - Gate behind a config flag `push.verify_remote` (default false today; future-default true).
- Rationale: Defensive measure for the rare but real case of silent corruption in transit. The cost is one extra hash computation on the phone per chunk — significant for big chunks but on a modern Pixel still much faster than the push itself.
- Goal: Catches the silent-corruption class of failure that would otherwise only surface during restore weeks later.
- Effort estimate: small
- Status: pending

---

## IMP-C9: Atomic cmd_replace via two-rename pattern

- Category: bug
- Priority: medium
- Files: `main.py` — `cmd_replace` (755-806)
- Current behavior: Sequence is: (1) write `<original>.temp_dummy` (line 770-773); (2) delete `<original>` with retries (line 781-794); (3) rename dummy to original (line 801). Between step 2 and step 3, the disk has NEITHER the original NOR the renamed dummy. A power-loss or process-kill in that window leaves no file at the expected path. The 3-retry loop in step 2 is robust against locked-by-Plex scenarios but does not address the atomicity gap.
- Proposed change:
  - Reorder to a SAFE three-step pattern:
    1. Write `<original>.temp_dummy` (same as today).
    2. Rename `<original>` → `<original>.tobedeleted` (atomic on Windows NTFS).
    3. Rename `<original>.temp_dummy` → `<original>` (atomic).
    4. Delete `<original>.tobedeleted` (no rush; can also be done lazily by a `prune_dummies` sweep — IMP-D6).
  - On startup of `cmd_replace`, sweep for any leftover `.tobedeleted` files in the target folder and clean them up (idempotent re-entry).
- Rationale: The current sequence has a theoretical data-loss window. The window is small but real. The user has 70 GB files — losing one to a power blip is catastrophic.
- Goal: Power-loss-safe replace. At any moment of an in-progress replace, the disk state has either the original or the dummy at the expected name.
- Effort estimate: small
- Status: done (fix/atomic_replace, PR to main 2026-05-29)

---

## IMP-C10: Sidecar reconciliation command

- Category: other
- Priority: low
- Files: new `cmd_reconcile_sidecars` in `main.py`; uses sidecar files `uid` and `<short_id>.sha256` written by `cmd_prep` (lines 319-322) and `<chunk>.sha256` files in `checksums/` (line 611)
- Current behavior: Sidecar files are written but NEVER read by any code path. They exist as "belt and suspenders backup" per the architecture, but no command actually uses them. If the library JSONs are destroyed, the sidecars contain enough information to partially reconstruct them — but reconstruction is manual.
- Proposed change:
  - New `python main.py reconcile_sidecars [folder_or_root]` command.
  - Walks the folder, finds all `uid` and `<short_id>.sha256` files.
  - For each:
    - If the library has an entry matching this `short_id`: verify the stored hash matches the sidecar's hash. Report any mismatches.
    - If no entry: report it as "orphan sidecar" — file on disk but no library entry. Offer to re-prep it under a suggested ID.
  - For `checksums/<chunk>.sha256`: cross-check against `entry["split_info"]["chunks"]`.
  - Read-only by default; `--repair` flag to attempt fixes (e.g., re-create library entry from sidecar).
- Rationale: Sidecars are written religiously but never used. Either delete them entirely (they cost disk writes per prep) or actually use them as the disaster-recovery layer they were designed to be. This task picks "use them".
- Goal: Sidecar files serve a real purpose. Library JSON destruction becomes recoverable from disk state.
- Effort estimate: medium
- Status: pending

---

## IMP-C11: Hash-mismatch quarantine in cmd_restore

- Category: bug
- Priority: medium
- Files: `main.py` — `cmd_restore` (888-977), especially the standard-restore hash check around line 950
- Current behavior: When a downloaded restore-folder file fails its SHA256 check (line 950 for single-file, the chunk check is implicit during merge), `cmd_restore` returns False and leaves the bad file in `<folder>/restore/`. The next fetch attempt sees the file already exists and may skip re-downloading it (the `os.path.exists` check at line 253-254 of mainfetch). User has to manually delete the bad file to retry.
- Proposed change:
  - On hash mismatch in `cmd_restore`: move the bad file to `<folder>/restore/quarantine/<filename>.<timestamp>` instead of leaving it in place.
  - Print a clear diagnostic ("❌ Hash mismatch. Bad file quarantined at <path>. A fresh fetch will re-download.").
  - On the fetch side, `fetch_single_entry` already creates `restore/` via `os.makedirs(restore_folder, exist_ok=True)` — no change needed there. The next fetch will see the original filename absent and re-trigger.
  - Periodically the user can manually inspect/clean `restore/quarantine/`. Or a `cleanup_quarantine` command (IMP-D extension) can purge files older than N days.
- Rationale: Today a bad chunk traps the user. With quarantine, the system self-recovers on retry without manual cleanup.
- Goal: Hash mismatches are recoverable by simply re-running fetch. The user no longer has to manually delete bad files.
- Effort estimate: small
- Status: pending
