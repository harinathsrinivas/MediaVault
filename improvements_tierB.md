# Improvements — Tier B · Performance Optimizations

> Tiny in code, visibly faster in lived experience. Order matters: B1 alone gives ~10× speedup on big season ops; the rest are smaller multipliers.

> **Cross-cutting context:**
> - User regularly runs `prep_push_rep_season` for 8-24 episode seasons (Mr Robot S04 has 13 eps; The Wire S01 has 13 eps). Each episode currently triggers 3 separate `load_library` + `save_library` round-trips.
> - Each library JSON is small individually (movies ~250KB, series ~480KB, anime ~180KB) but always loaded as a set of three.
> - Anime files have never been chunked in production (0 of 140 leaves have `split_info`). Optimizations targeting the chunk path benefit movies (70 split) and series (60 split) only.
> - The user's typical chunk size is `SIZE_MB 9900` (~9.66 GB), close-but-under 10 GB. A 60 GB movie produces ~7 chunks; a 4K BluRay remux can hit 80+ GB and 10 chunks.

---

## IMP-B1: Cache library handle across cmd_* calls in auto-pilots

- Category: performance
- Priority: high
- Files: `main.py` — `cmd_prep_push_rep` (1237-1276), `cmd_prep_push_rep_season` (1279-1340), `cmd_fetch_restore` (1360-1384); also touches `cmd_prep`, `cmd_push`, `cmd_replace`, `cmd_restore` to accept an optional `library` arg
- Current behavior: Each `cmd_*` function calls `load_library()` and `save_library()` independently. For `cmd_prep_push_rep_season` with 24 episodes, that's roughly 24 × (cmd_prep + cmd_push + cmd_replace) ≈ 72 cmd_* invocations, each doing 3-file read and 3-file write. So ~216 reads + ~216 writes of the JSON triplet for one season run. With ~22k JSON lines total per pass, on a fast SSD this is single-digit-second total, but it amplifies disk-write wear and dwarfs everything else when ADB push is the actual long pole.
- Proposed change:
  - Refactor `cmd_prep`, `cmd_push`, `cmd_replace`, `cmd_restore`, etc. to accept an optional `library: dict | None = None` parameter.
  - If passed, mutate the caller-supplied dict and skip the internal load/save.
  - The auto-pilot functions (`cmd_prep_push_rep_season` etc.) `load_library()` once at the top, pass it down through every step, and `save_library()` once at the end (and at strategic checkpoints — see IMP-C1).
  - If the parameter is None (today's behaviour), preserve the load-mutate-save shape for backwards compatibility with single-cmd CLI invocations.
- Rationale: Disk I/O isn't the bottleneck today, but the JSON re-parse overhead is real, and more importantly: with IMP-A1 in place, a shared library handle is the natural pattern. It also enables IMP-B2 (dirty save) cleanly.
- Goal: One read at the start, one write at the end of a season auto-pilot. ~95% reduction in JSON I/O per batch run.
- Effort estimate: medium
- Status: pending

---

## IMP-B2: Per-category dirty-save in save_library

- Category: performance
- Priority: medium
- Files: `mvcommon.py` (or `main.py` 57-85 today)
- Current behavior: `save_library(data)` splits the merged dict into mov/tv/ani and writes ALL THREE files atomically every call, regardless of whether they actually changed. A `cmd_prep mov-en-X ...` rewrites `library_series.json` and `library_anime.json` even though those didn't change.
- Proposed change:
  - Track a `dirty` set on the library handle (`library._dirty = {"movies"}`, `{"series", "anime"}`, etc.).
  - `cmd_*` functions mark the appropriate category dirty when they mutate an entry. For an entry with prefix `mov-*`, mark `"movies"` dirty; the prefix-routing logic is already in `save_library`.
  - `save_library(data, dirty=None)` writes only the dirty files. With `dirty=None` (default), preserve current write-everything behaviour as a safety fallback.
- Rationale: Most operations touch one category. The ~2/3 reduction in disk writes per call is cheap to implement and compounds with IMP-B1.
- Goal: Touch only what changed. Reduces SSD wear and shaves measurable time off big batch operations.
- Effort estimate: small
- Status: pending

---

## IMP-B3: Parallel chunk hashing during cmd_push

- Category: performance
- Priority: medium
- Files: `main.py` — `cmd_push` lines 605-617 (the hash loop after split)
- Current behavior: After `split_video_file` produces N chunks (often 8-10 for a 4K UHD movie, ~9 GB each), `cmd_push` hashes them serially: `for chunk_path in files_to_upload_paths: ... calculate_file_hash(chunk_path)`. On a fast NVMe SSD, SHA256 of 9 GB takes ~30-60 seconds per chunk depending on CPU. 10 chunks × 45 s = ~7.5 min just hashing before the actual ADB push starts.
- Proposed change:
  - Use `concurrent.futures.ThreadPoolExecutor(max_workers=4)` to compute chunk hashes in parallel.
  - Hash computation is I/O-bound (disk reads) plus a small CPU cost; on NVMe disks the disk can usually feed 2-4 readers before saturating.
  - Preserve ordering: collect results into a list keyed by chunk_path, then build `chunk_metadata` in chunk-number order.
  - Add config key `hash_concurrency` (default 4) under IMP-A5.
- Rationale: Today the user stares at "Hashing chunk 1...Done. Hashing chunk 2...Done." for 5+ minutes before push starts. Even a 2× speedup is felt.
- Goal: Halve the pre-push hashing latency for split files on fast SSDs.
- Effort estimate: small
- Status: pending

---

## IMP-B4: Replace hardcoded sleeps with WebDriverWait in trigger_download

- Category: performance
- Priority: medium
- Files: `mainfetch.py` — `trigger_download` lines 148-214
- Current behavior: Three hardcoded sleeps per chunk trigger: `time.sleep(1.5)` after page load (line 160), `time.sleep(3)` for search results (line 170), `time.sleep(2)` after click for player to open (line 200), `time.sleep(1)` after Shift+D (line 206). Total ~7.5 s of dead time per chunk. For a 10-chunk movie fetch, that's ~75 s of pure sleep before any download even starts. The sleeps are sized for worst-case slow loads on Google's side; most loads are much faster.
- Proposed change:
  - Replace `time.sleep(N)` with `WebDriverWait(driver, N).until(EC.<condition>)` patterns:
    - After `driver.get(...)`: wait until search bar element appears.
    - After Enter: wait until the result grid populates (`presence_of_element_located` for a result tile).
    - After clicking a thumbnail: wait until the player container is visible.
    - After Shift+D: keep a small fixed sleep (the keyboard event itself has no observable confirmation in Photos).
  - Keep a max-timeout fallback at the current sleep duration so behaviour degrades gracefully on slow networks.
- Rationale: Selenium's explicit waits return AS SOON AS the condition is met (often <500 ms on fast networks). Today every step pays the worst-case sleep regardless. For a series fetch this compounds — 24 episodes × 1 chunk each × 7.5 s = ~3 minutes of pure sleep.
- Goal: Cut chunk-trigger latency from a fixed ~7.5 s to a typical ~1-2 s.
- Effort estimate: small
- Status: pending

---

## IMP-B5: Smart re-listing of ~/Downloads in the harvester loop

- Category: performance
- Priority: low
- Files: `mainfetch.py` — `fetch_single_entry` harvester loop (~lines 308-364)
- Current behavior: Every 5 seconds (`time.sleep(5)` at line 364), the harvester does a full `os.listdir(SYSTEM_DOWNLOADS_FOLDER)` (line 310 for the .crdownload check, line 328 for the .mkv/.mp4 check). For a 90-minute fetch with a heavily-used Downloads folder (lots of small files), this listing has measurable cost AND triggers Windows Explorer thumbnail cache thrashing visible to the user as folder lag.
- Proposed change:
  - Cache the result of `os.listdir(DOWNLOADS)` per iteration; only re-list when:
    - The previous iteration's `.crdownload` set CHANGED (a download just finished or started), OR
    - More than N seconds elapsed since last full listing (heartbeat).
  - Use `os.scandir` instead of `os.listdir` to get filename + entry-type in one syscall (avoids the implicit `os.path.isfile` cost downstream).
- Rationale: Quality-of-life on the Windows side; reduces UI jank when the user wants to use Explorer during a long fetch. Also slightly reduces the chance of a race where a partially-renamed file is observed mid-transition.
- Goal: Smoother system behaviour during long fetches without changing correctness.
- Effort estimate: small
- Status: pending

---

## IMP-B6: Skip hashing in cmd_check for known-good files

- Category: performance
- Priority: low
- Files: `main.py` — `cmd_check` (514-532), plus schema addition (`entry["last_verified_at"]`, `entry["last_verified_mtime"]`)
- Current behavior: `cmd_check <id>` always recomputes the SHA256 of the file in place. For a 70 GB movie on a SATA SSD this takes ~5-10 minutes. There's no fast-path for "this file hasn't changed since last verify".
- Proposed change:
  - Add optional fields to the entry schema: `last_verified_at` (ISO date), `last_verified_mtime` (float, file mtime when last verified passed).
  - In `cmd_check`: stat the file. If `os.path.getmtime(file) == entry.get("last_verified_mtime")` AND the size matches `tech_spec.size_bytes`, fast-path-skip the hash and print "✅ PASS (cached verify on YYYY-MM-DD)".
  - Add `--force-rehash` flag to always recompute.
  - On successful hash match, update both fields.
- Rationale: This is a precondition for a future `check_all` batch (verify every non-archived entry monthly). Without caching, `check_all` on a 100-file library would take many hours.
- Goal: Re-verification of a known-good file becomes instant on the no-change path. Enables periodic integrity sweeps.
- Effort estimate: small
- Status: pending

---

## IMP-B7: Memoize known_paths in cmd_scan_unprepped

- Category: performance
- Priority: low
- Files: `main.py` — `cmd_scan_unprepped` (1155-1234)
- Current behavior: Every invocation re-reads the three library JSONs (separately from `load_library`, opening each file again at lines 1173-1178) and rebuilds a `known_paths` set. Then walks every directory under `Movies/`, `Series/`, `Anime/`. Today this is sub-second; would degrade past ~50k entries or with slow network-mounted media disks.
- Proposed change:
  - Use the in-memory library handle from `load_library` (after IMP-A1) rather than re-reading the files.
  - Optional: cache the `known_paths` set under `~/.mediavault/cache/known_paths.pkl` keyed by `(library_mtime_movies, library_mtime_series, library_mtime_anime)`; invalidate when any of those change.
  - Cache the walk result (filesystem snapshot of media root) similarly, invalidated by directory mtimes.
- Rationale: Diminishing returns today but cheap to implement when refactoring under IMP-A1. Future-proofs the command for larger libraries and slower disks.
- Goal: `scan_unprepped` stays sub-second as the library grows past 1k+ entries.
- Effort estimate: small
- Status: pending

---

## IMP-B8: Skip MediaInfo.parse in cmd_prep for already-prepped entries

- Category: performance
- Priority: low
- Files: `main.py` — `cmd_prep` (289-381), specifically the `get_tech_specs` call at line 315
- Current behavior: `cmd_prep` short-circuits early if the entry exists AND `uploaded=True`/`status=archived` (lines 296-300), but if the entry exists and is `local_ready`, `cmd_prep` re-runs the FULL pipeline including a fresh `MediaInfo.parse(filepath)` (line 132-173). `MediaInfo.parse` on a 70 GB file takes 5-15 seconds. Re-prepping a 24-episode season for any reason re-parses 24 files unnecessarily.
- Proposed change:
  - In `cmd_prep`: if the entry already exists AND the hash matches AND `tech_spec` is non-error, skip the `get_tech_specs` call and reuse the cached spec.
  - Add `--force-rescan` flag to bypass the cache.
- Rationale: Re-prep is a normal operation when the user is iterating on an ID convention or fixing search terms. Wasting 5-15s per file on metadata that hasn't changed is pure overhead.
- Goal: Re-prep of unchanged files is fast. Encourages users to re-prep liberally without dreading the wait.
- Effort estimate: small
- Status: pending
