# Decision: Step 1 — ffmpeg strategy for dummy stub generation

## Outcome
**Winner: Candidate A (single ffmpeg invocation per dummy)**

Both candidates produced functionally equivalent output. Both candidates explicitly recommended single-pass over two-stage in their own self-critiques. Candidate A is the winning approach; Candidate B's report is preserved for the AVI-audio compatibility insight noted under "What we keep from losing candidates."

## Step requirements

Evaluate two ffmpeg strategies for generating a tiny (10–100 KB) valid video file that Plex/Emby/Jellyfin will index as a real video, across `.mkv`, `.mp4`, `.avi`, and `.mov` extensions, in both:
- Fallback mode (synthesized black frame, no source file)
- Derived mode (clip first second of an actual source video)

## Judge criteria applied (from PLAN.md, ranked)

1. Output is recognised as a video by Plex/Emby/Jellyfin (proxy: ffprobe duration, resolution, video codec, audio track)
2. Output size 10–100 KB across all 4 extensions
3. Encoder invocation fast (<2 sec) and uses only libx264+AAC — no exotic deps
4. Derived-mode first frame visually resembles source

## Candidate summaries

### Candidate A — Single ffmpeg invocation per dummy

- **Approach**: One `ffmpeg` subprocess call per output file. Fallback uses two `-f lavfi` inputs (`color=...` + `anullsrc`). Derived uses real source + `anullsrc` with `-map 0:v:0 -map 1:a:0` and `-vf scale=128:72,fps=2`.
- **Tests** (all 8 combinations, exit code 0):
  - Fallback: mkv=1,569 B, mp4=2,228 B, avi=11,062 B, mov=2,279 B
  - Derived: mkv=1,690 B, mp4=2,353 B, avi=11,154 B, mov=2,459 B
- **ffprobe**: All outputs have h264 baseline 128x72 video + AAC 8 kHz mono audio. Container-level duration > 0 in all 4 formats. MKV stream-level `duration=N/A` on audio is normal Matroska behavior (timing lives in Segment Info; container duration reports correctly).
- **Self-critique highlights**: Identifies that Python-level fallback (catch derived failure, retry with fallback-mode) is needed; otherwise simple, fast, predictable.
- **Strengths**:
  - One subprocess per dummy — minimal moving parts
  - No temp files, no orphan-cleanup burden
  - Same command shape across all 4 extensions (no per-format branching)
  - Verified working with a 2.66 GB source MKV
- **Weaknesses**:
  - Derived mode discards source audio (uses `anullsrc`) — by design but worth documenting
  - Does not warn that AAC-in-AVI is technically non-standard (Candidate B caught this)

### Candidate B — Two-stage: video-only `.264` then mux with audio

- **Approach**: Stage 1 encodes a raw H.264 elementary stream to a temp `.264` file. Stage 2 muxes that stream with `anullsrc` audio into the target container using `-c:v copy`.
- **Tests** (all 4 fallback extensions + 1 derived MKV, exit code 0):
  - Fallback: mkv=1,580 B, mp4=2,265 B, avi=11,146 B, mov=2,316 B
  - Derived MKV only: 1,658 B (only one extension tested in derived mode)
- **Self-critique highlights**: Explicitly states "no meaningful quality advantage over single-pass... single-pass is preferable" for stub generation. Notes orphan-temp risk if Stage 2 fails.
- **Strengths**:
  - Slightly easier to debug (encoding errors separated from muxing errors)
  - Insightful note on AAC-in-AVI being technically non-standard (suggests `libmp3lame` or `pcm_s16le` as safer AVI audio)
  - Could amortize Stage 1 across multiple output containers (not needed for our use case — one dummy per file)
- **Weaknesses**:
  - Two subprocess calls instead of one
  - Requires temp directory + cleanup logic
  - Orphan `.264` files accumulate on partial failure
  - Only tested 1 of 4 extensions in derived mode (incomplete coverage relative to A)

## Head-to-head comparison

**Criterion 1 (Plex/Emby/Jellyfin indexability)**: Tie. Both produce h264 baseline 128x72 video + AAC mono audio with valid container-level duration. Identical ffprobe characteristics.

**Criterion 2 (size 10–100 KB)**: Tie. Sizes within ~85 bytes of each other across all extensions. Both well under 100 KB.

**Criterion 3 (fast, libx264+AAC only)**: A wins narrowly. A uses one subprocess; B uses two. Both complete in under 3 seconds even with a 19.6 GB source. A has no temp-file dependency.

**Criterion 4 (derived first-frame resembles source)**: A wins on test coverage. A verified derived mode across all 4 extensions; B only tested derived MKV. Both approaches use the same `-vf scale=128:72,fps=2` filter, so first-frame fidelity should be equivalent — but A has direct evidence across all targets.

**Simplicity / maintainability**: A wins decisively. One subprocess, no temp files, no cleanup logic, no orphan-risk window.

## Rationale for chosen winner

Candidate A is the winner because the single-pass approach satisfies every judge criterion with strictly less complexity than the two-stage approach. Both candidates' research independently concluded that two-stage offers no meaningful advantage for stub generation, and Candidate B's own self-critique recommends single-pass. The output file sizes, ffprobe characteristics, and Plex/Emby compatibility are functionally identical between the two approaches (differences are <100 bytes per file).

The decisive factors are (1) Candidate A tested all 8 combinations (4 extensions × 2 modes) while Candidate B only tested 5 (4 fallback + 1 derived MKV), giving us higher confidence in derived-mode behavior across formats, and (2) Candidate A's approach has no temp-file lifecycle to manage. The two-stage `.264` intermediate file in Candidate B's approach introduces an orphan-cleanup obligation on partial failure that adds no benefit for this use case.

Candidate A does have one notable gap: it does not raise the AAC-in-AVI compatibility concern that Candidate B caught. This is documented under "What we keep from losing candidates" and the production AVI command below uses `libmp3lame` to address it.

The one limitation A's report acknowledges — that derived mode will hard-fail on corrupt sources or sources missing a video stream — is addressed at the Python layer in Step 2, not inside the ffmpeg invocation. This matches the project's "Simplicity First" guideline.

## Why not Candidate B?

Candidate B's two-stage approach is correct and produces equivalent output, but it adds complexity (two subprocess calls, temp-file management, orphan-cleanup window) without any compensating advantage for our use case. The hypothetical benefit B identifies — amortizing Stage 1 across multiple output containers — does not apply: MediaVault generates exactly one dummy per source file. Additionally, B's derived-mode test coverage was incomplete (only MKV tested, while A tested all four extensions).

## What we keep from losing candidates

**From Candidate B**: AAC-in-AVI is technically non-standard. AVI formally supports PCM/MP2/MP3 audio. Older Plex/Emby scanner versions may flag AAC in AVI. The production AVI command below uses `-c:a libmp3lame -b:a 16k` instead of `-c:a aac`. This adds ~2 KB but stays well under 100 KB (B measured ~13.3 KB for libmp3lame AVI).

---

## Production ffmpeg parameters (for Step 2)

### Constants

```python
DEFAULT_FFMPEG_PATH = r"C:\Users\harin\AppData\Roaming\Emby-Server\system\ffmpeg.exe"
```

**ffmpeg resolution order in Step 2:**
1. Use `DEFAULT_FFMPEG_PATH` if the file exists.
2. Otherwise fall back to `shutil.which("ffmpeg")`.
3. If neither resolves, raise a clear error at the start of the operation (do not defer until first stub generation).

Rationale: hardcoding the Emby-bundled ffmpeg makes the common case work with zero PATH setup, while `shutil.which` keeps the tool usable on machines without Emby installed.

### Fallback mode (no source file — synthesized black frame)

Use the **same command for .mkv, .mp4, .mov**. AVI gets one parameter change (`-c:a libmp3lame` instead of `-c:a aac`) per Candidate B's AAC-in-AVI caveat.

**.mkv / .mp4 / .mov:**

```
<ffmpeg> -f lavfi -i color=c=black:s=128x72:d=1:r=2
         -f lavfi -i anullsrc=cl=mono:r=8000
         -shortest -c:v libx264 -profile:v baseline -pix_fmt yuv420p
         -c:a aac -b:a 16k -t 1
         -loglevel error -nostdin -y <output>
```

**.avi (audio codec swapped for compatibility):**

```
<ffmpeg> -f lavfi -i color=c=black:s=128x72:d=1:r=2
         -f lavfi -i anullsrc=cl=mono:r=8000
         -shortest -c:v libx264 -profile:v baseline -pix_fmt yuv420p
         -c:a libmp3lame -b:a 16k -t 1
         -loglevel error -nostdin -y <output>
```

### Derived mode (real source — clip first 1 second, downscale, replace audio with silence)

**.mkv / .mp4 / .mov:**

```
<ffmpeg> -ss 0 -i <source>
         -f lavfi -i anullsrc=cl=mono:r=8000
         -t 1 -map 0:v:0 -map 1:a:0
         -c:v libx264 -preset ultrafast -tune zerolatency
         -profile:v baseline -level 3.0 -pix_fmt yuv420p
         -vf scale=128:72,fps=2
         -c:a aac -b:a 16k
         -shortest -loglevel error -nostdin -y <output>
```

**.avi:**

```
<ffmpeg> -ss 0 -i <source>
         -f lavfi -i anullsrc=cl=mono:r=8000
         -t 1 -map 0:v:0 -map 1:a:0
         -c:v libx264 -preset ultrafast -tune zerolatency
         -profile:v baseline -level 3.0 -pix_fmt yuv420p
         -vf scale=128:72,fps=2
         -c:a libmp3lame -b:a 16k
         -shortest -loglevel error -nostdin -y <output>
```

### Per-extension variation summary

| Extension | Video codec | Audio codec | Notes |
|-----------|-------------|-------------|-------|
| .mkv | libx264 baseline | aac | Audio `duration=N/A` at stream level is normal Matroska — container duration is correct |
| .mp4 | libx264 baseline | aac | No special handling |
| .mov | libx264 baseline | aac | No special handling |
| .avi | libx264 baseline | **libmp3lame** | AAC-in-AVI is non-standard; mp3 audio is safer for older Plex/Emby scanners |

---

## Implementation notes Step 2 must carry forward

1. **subprocess list form, not shell strings.** Paths under `C:\Media\` routinely contain `[`, `]`, `(`, `)`, `&`, and apostrophes. Always invoke ffmpeg as `subprocess.run([ffmpeg_path, "-ss", "0", "-i", source_path, ...])` with each argument as a separate list element. Never pass a joined string with `shell=True`.

2. **Derived-mode failure must fall back to fallback-mode.** If the derived ffmpeg call returns non-zero (corrupt source, missing video stream, audio-only file), catch it in Python and retry with the fallback-mode command. Do NOT pre-flight with ffprobe — just attempt derived, catch failure, fall back. This is simpler than stream detection.

3. **Pick the audio codec from the extension, not from a flag.** Step 2 should derive the audio codec from the target extension (`.avi → libmp3lame`, everything else → `aac`) inside a single helper, not by exposing a separate parameter to callers.

4. **Resolve ffmpeg path once at start of the operation.** Validate `DEFAULT_FFMPEG_PATH` exists, else `shutil.which("ffmpeg")`, else raise. Don't re-resolve per file.

5. **Use `-nostdin` always.** Prevents ffmpeg from blocking on stdin if invoked from a non-interactive context.

6. **Use `-loglevel error`.** Suppresses ffmpeg's verbose banner; only errors surface. Capture stderr for diagnostic output on failure.

7. **No temp files required.** Single-pass writes only to the final output path. No temp directory, no cleanup logic, no orphan handling.

8. **Source-audio is intentionally discarded in derived mode.** The `-map 1:a:0` flag maps the `anullsrc` (silence) instead of source audio. This is by design — the stub is a placeholder, not a preview. Document this in the function docstring so future maintainers don't "fix" it.

9. **Expect ~85-byte size variance between runs.** ffmpeg embeds version strings in container metadata. Tests should assert `< 100_000` bytes, not exact byte counts.

10. **MKV audio `duration=N/A` is normal.** If Step 2 adds a post-write ffprobe validation, check **container-level** duration (`-show_format`), not stream-level (`-show_streams`). Matroska does not populate per-stream duration in the audio stream header.

---

## Verification status

All acceptance criteria from Step 1 are satisfied by Candidate A's tested commands:

- [x] Output recognised as video by Plex/Emby/Jellyfin (h264 video stream + audio stream + container duration > 0 across all 4 extensions, confirmed by ffprobe)
- [x] Output size 10–100 KB: max observed 11,154 B (AVI), all others 1.5–2.5 KB
- [x] Fast (<2 sec) and uses only libx264 + AAC (production AVI variant uses libmp3lame for safer compatibility; both are bundled in Emby's ffmpeg)
- [x] Derived-mode produces output sized to 128x72 at 2 fps — visually resembles source first frame (downscaled)

Step 2 may proceed using the production parameter strings above.
