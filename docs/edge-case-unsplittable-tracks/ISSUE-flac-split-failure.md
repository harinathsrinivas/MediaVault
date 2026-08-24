# Issue — `push` dies with `exit status 2` when the source carries a FLAC track

> **Incident date:** 2026-08-24 · **Status:** root-caused, operational fix applied, code fix NOT yet implemented
> **Affected command:** `cmd_push` (and therefore `prep_push_rep`, `prep_push_rep_season`, `push_group`) whenever a split is requested
> **Tooling:** `mkvmerge v97.0 ('You Don't Have A Clue') 64-bit`
>
> Companion docs in this folder: [`CODEC-SPLIT-MATRIX.md`](CODEC-SPLIT-MATRIX.md) (what *can* be split) ·
> [`RUNBOOK-remux-before-split.md`](RUNBOOK-remux-before-split.md) (how to get an affected file archived today) ·
> [`CODE-GAPS.md`](CODE-GAPS.md) (the MediaVault-side fixes still outstanding) · [`README.md`](README.md)

---

## 1. Symptom

A 4K Korean BluRay REMUX was pushed with a 6000 MB split. `prep` completed, `push` split-failed, and
auto-rollback did its job — but the printed reason was useless:

```
(.venv) PS C:\Users\harin\PycharmProjects\MediaVault> python main.py prep_push_rep mov-kor-2003-ataleoftwosisters  "C:\Media\Movies\Korean\A Tale of Two Sisters (2003) {tmdb-4552}\A.Tale.of.Two.Sisters.2003.Ita.Kor.2160p.BluRay.REMUX.HDR.DV.HEVC-NAHOM.mkv"  SIZE_MB 6000
=== 🚀 AUTO-PILOT: PREP -> PUSH -> REPLACE for mov-kor-2003-ataleoftwosisters ===

>>> STEP 1: PREP
--- PREPPING: mov-kor-2003-ataleoftwosisters ---
  🔍 A.Tale.of.Two.Sisters.2003.Ita.Kor.2160p.BluRay.REMUX.HDR.DV.HEVC-NAHOM.mkv  [████████████████████████] 58.26 GB / 58.26 GB B
   > Scanning Tech Specs (Deep Scan)...
✅ Library Entry Created & Linked (Search Key: A.Tale.of.Two.Sisters.2003.Ita.Kor.2160p.BluRay.REMUX.HDR.DV.HEVC-NAHOM [7cdcd3].mkv).


>>> STEP 2: PUSH
--- PUSHING: mov-kor-2003-ataleoftwosisters ---
   > Target: /sdcard/Media/Movies/Korean/A Tale of Two Sisters (2003) {tmdb-4552}
   > ✂️ Splitting...
   > ⚖️  Balanced Split: 10 chunks of ~5976MB each.
❌ Error running mkvmerge for splitting: Command '['C:\\Program Files\\MKVToolNix\\mkvmerge.exe', '-o', 'C:\\Media\\Movies\\Korean\\A Tale of Two Sisters (2003) {{tmdb-4552}}\\_parts\\A.Tale.of.Two.Sisters.2003.Ita.Kor.2160p.BluRay.REMUX.HDR.DV.HEVC-NAHOM [7cdcd3].chunk.%03d.mkv', '--split', 'size:5976M', 'C:\\Media\\Movies\\Korean\\A Tale of Two Sisters (2003) {tmdb-4552}\\A.Tale.of.Two.Sisters.2003.Ita.Kor.2160p.BluRay.REMUX.HDR.DV.HEVC-NAHOM.mkv']' returned non-zero exit status 2.
✅ Rollback complete — back to exact pre-command state.

⚠️ Auto-Pilot Paused: Push did not complete (see the resume hint above).
```

Everything in that output is correct behaviour **except** the diagnosis. Auto-rollback worked exactly as
specified (pre-PONR push failure → full revert, entry removed, `_parts/` cleaned, no orphans left on disk).
The user was simply given no way to know *why*.

## 2. Root cause

**mkvmerge cannot split a FLAC track.** Re-running the identical argv by hand surfaced the message that
`cmd_push` had swallowed:

```
Error: The track ID 4 from the file 'C:\Media\Movies\Korean\A Tale of Two Sisters (2003) {tmdb-4552}\A.Tale.of.Two.Sisters.2003.Ita.Kor.2160p.BluRay.REMUX.HDR.DV.HEVC-NAHOM.mkv' cannot be split. Splitting tracks of this type is not supported.
```

Track 4 is the Italian dub: `A_FLAC`, 6 channels, 16-bit, 48 000 Hz, `language=ita`, `BPS=730976`.
mkvmerge aborts at the split-capability check **before writing a single byte**, which is why the failure
was instant and left nothing behind.

> **Aside worth noting for future triage:** 730 976 bps for a 5.1 / 16-bit / 48 kHz *lossless* track is
> implausible — genuine 5.1 FLAC of that spec runs ~2.5–3.5 Mbps. A ratio that good means the content was
> already band-limited, i.e. the Italian dub was sourced from a **lossy** track and re-wrapped as FLAC by
> the release group. This is the common shape of the problem: the FLAC wrapper buys no real fidelity, but
> it costs you the ability to split. It also means the fidelity stakes of re-encoding that particular
> track are far lower than "lossless" suggests.

At ~631 MB of a 62 556 971 814-byte file, the offending track is **~1 %** of the payload — and it blocked
100 % of the archive.

## 3. Why the operator saw nothing useful

> ✅ **Fixed by IMP-C19 (`1af16a3`).** This section records the state at the time of the incident. See
> [`CODE-GAPS.md`](CODE-GAPS.md) Gap 1 for what shipped.

At the time, `split_video_file` destroyed the diagnosis twice over:

```python
# as it stood on 2026-08-24, before IMP-C19
    try:
        # Added stderr capture to output real error messages
        subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        ...
    except subprocess.CalledProcessError as e:
        print(f"❌ Error running mkvmerge for splitting: {e}");
        return []
```

| Loss | Detail |
|---|---|
| **1. Wrong stream discarded** | mkvmerge writes its `Error:` lines to **stdout**, not stderr. `stdout=subprocess.DEVNULL` threw them away. |
| **2. Captured stream never printed** | The handler printed `{e}` — the `CalledProcessError` repr, i.e. argv + exit code. `e.stderr` (which *was* captured, and was empty here anyway) was never shown. |

The comment — *"Added stderr capture to output real error messages"* — described an intention the code
did not carry out. Confirmed empirically at the time: running the same command through
`subprocess.run(..., capture_output=True)` puts the `Error:` line in `r.stdout` and leaves `r.stderr`
empty.

Both streams are now captured and echoed through `_print_mkvmerge_failure()` (`main.py:291`), from the
split path at `main.py:388` and the merge path at `main.py:418`. The same failure today prints
mkvmerge's own sentence naming track 4.

## 4. Red herrings — already ruled out, do not re-investigate

### 4a. The doubled braces `{{tmdb-4552}}` in the argv are CORRECT

The `-o` path in the failing argv reads `...A Tale of Two Sisters (2003) {{tmdb-4552}}\_parts\...` while the
input path reads `{tmdb-4552}`. That asymmetry is **deliberate** — it is the libfmt escape installed at
`main.py:372-378` and applied at `main.py:378`:

```python
mkv_out = output_pattern.replace("{", "{{").replace("}", "}}")
```

Verified this session against mkvmerge v97.0 with a synthetic file in a folder literally named
`A Tale of Two Sisters (2003) {tmdb-4552}`:

| `-o` argument | Result |
|---|---|
| escaped (`{{tmdb-4552}}`) | **rc=0** — files written to the real single-brace folder as `…chunk.001.mkv`, `…chunk.002.mkv` |
| raw (`{tmdb-4552}`) | **rc=3** — `terminate called after throwing an instance of 'fmt::v11::format_error' / what(): argument not found` |

Note the different exit code: the brace bug is **exit 3** (an uncaught C++ exception), this incident is
**exit 2** (a normal mkvmerge error). They are unrelated failures. The escape must NOT be removed — and
must NOT be added to plain merges, per `merge_video_files`.

### 4b. Not a disk-space failure

The `_free_space_ok` pre-flight at `main.py:4715` passed, and it was right to: C: had ≈70.5 GiB free
against a 58.26 GiB source needing 1X for a deferred split. mkvmerge also failed *instantly*, before
opening any output file — a space failure would surface partway through chunk N.

## 5. The source file, for reference

`mkvmerge -J` reported the container as fully supported, with `errors: []` and `warnings: []`. Duration
`6904.792 s`. **No chapters, no container title** (so nothing extra to preserve across a remux).

| ID | Type | Codec ID | Detail | `BPS` |
|---|---|---|---|---|
| 0 | video | `V_MPEGH/ISO/HEVC` | HEVC/H.265, Dolby Vision profile 7 (`rpu_present=1`, `el_present=1`, `bl_present=1`, `bl_signal_compatibility_id=6`) | 67 106 190 |
| 1 | audio | `A_DTS` | DTS-HD Master Audio, 6 ch, 24-bit, 48 kHz, `kor` | 4 319 848 |
| 2 | audio | `A_AC3` | 2 ch, `kor`, title *"Commentary by Director Kim Jee-woon and Director, of lighting and Cinematographer"* | 192 000 |
| 3 | audio | `A_AC3` | 2 ch, `kor`, title *"Commentary by Director Kim Jee-woon and Cast Members Im Soo-jung, Moon Geun-young"* | 192 000 |
| **4** | **audio** | **`A_FLAC`** | **6 ch, 16-bit, 48 kHz, `ita` — the blocker** | **730 976** |
| 5 | subtitles | `S_HDMV/PGS` | `eng` | 10 503 |
| 6 | subtitles | `S_HDMV/PGS` | `fre` | 6 356 |
| 7 | subtitles | `S_HDMV/PGS` | `fre` | 316 |
| 8 | subtitles | `S_TEXT/UTF8` | SubRip, `und` | 14 |

## 6. Blast radius

This is **not** a one-file oddity:

- **Any** source carrying a FLAC track hits it. Lossless-wrapped European dub tracks (ita / ger / fre) are
  routine on BluRay REMUX releases, so the population of affected files in a media library is
  non-trivial and grows with every REMUX added.
- **It used to fail late and expensively.** `prep` runs first and completes: deep tech-spec scan plus a
  whole-file SHA-256 of — in this case — 58 GiB. All of that work was done, then thrown away by the
  rollback, before the split even attempted to start, because the cheap check (reading the track list)
  happened nowhere. IMP-C20 (`e2b799c`) now performs exactly that check in `cmd_push` before splitting;
  the waste that remains is the `prep` scan preceding it.
- **Every split entry point is affected**, since they all funnel into `split_video_file`: `push`,
  `push_group`, `prep_push_rep`, `prep_push_rep_season`.
- **No workaround exists inside mkvmerge.** All six split modes refuse identically — see
  [`CODEC-SPLIT-MATRIX.md` §2](CODEC-SPLIT-MATRIX.md). The track must be converted or dropped.
- **The obvious escapes are closed by MediaVault's own architecture.** Pushing unsplit is not an option
  (chunks exist to stay under the Google Photos per-video ceiling — see `ARCHITECTURE.md` §1), and
  raw-byte splitting is not an option either (chunks must remain playable videos for the phone's Photos
  app to ingest them — the same reason "Way B" was rejected in
  [`../feature-split-hash-deterministic/DECISIONS.md`](../feature-split-hash-deterministic/DECISIONS.md) D-1).

## 7. Resolution

| Layer | Status |
|---|---|
| **This file** | ✅ Remuxed with track 4 converted FLAC → WavPack (lossless, splittable), then archived normally. Procedure and verification: [`RUNBOOK-remux-before-split.md`](RUNBOOK-remux-before-split.md). |
| **Diagnosis gap** | ✅ Closed by **IMP-C19** (`1af16a3`) — mkvmerge's own `Error:` line is now printed by both `split_video_file` and `merge_video_files`. Tests: `tests/test_mkvmerge_error_surfacing.py`. |
| **Late-failure gap** | ✅ Closed by **IMP-C20** (`e2b799c`) — `cmd_push` probes the track list with `mkvmerge -J` and refuses up front, naming the track. Tests: `tests/test_unsplittable_preflight.py`. |
| **Assisted remux** | ⏸️ Open, and **opt-in only** by user decision — MediaVault must never convert or drop a track on its own. [`CODE-GAPS.md`](CODE-GAPS.md) Gap 3. |

## 8. How to recognise this failure in future

Since IMP-C19 the failure explains itself: mkvmerge's
`Error: The track ID N … cannot be split.` is printed under the `❌` line. Since IMP-C20 you will
usually not reach the split at all — `cmd_push` refuses first and names the track.

To check a source before starting anything:

```powershell
& "C:\Program Files\MKVToolNix\mkvmerge.exe" -J "<source.mkv>"     # cheap: list tracks, look for A_FLAC
```

On an older build without IMP-C19, recover the real message by re-running the argv by hand and reading
**stdout** rather than stderr.
