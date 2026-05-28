# MediaVault — Architecture Reference

> Definitive engineering reference for the `MediaVault` codebase at
> `C:\Users\harin\PycharmProjects\MediaVault`. Read this before making any
> changes to `main.py` or `mainfetch.py`.

---

## 1. Project Overview

**MediaVault** is a single-user, Windows-only command-line system for
archiving and restoring large ripped video files (movies, TV series, anime).
It treats a Google Pixel phone's Google Photos auto-upload as free cold
storage: large `.mkv` files are pushed to the phone via ADB, the phone's
Google Photos app uploads them at "original quality" to the cloud, and the
local file is then replaced with a tiny dummy placeholder to free disk
space. Restore is the reverse — Selenium drives Chrome on the desktop to
download the originals back from `photos.google.com`, then `mkvmerge`
re-merges any chunks that were split before upload.

```
prep -> split -> push (PC -> Phone) -> phone auto-uploads to Google Photos
                                              |
                                              v
                                      archived state: local file is
                                      replaced with a tiny dummy to free
                                      disk space; library JSON keeps
                                      integrity metadata

When the user wants to watch:
fetch (Google Photos -> Downloads via Selenium) -> restore (merge + verify -> back in place)
```

- **User**: solo owner-operator (the developer).
- **Target platform**: Windows 10/11 only (hardcoded paths like
  `C:\Media`, `C:\Program Files\MKVToolNix\mkvmerge.exe`,
  `C:\Program Files\Google\Chrome\Application\chrome.exe`).
- **External hardware**: Google Pixel phone connected over USB ADB; the
  phone's Google Photos app does the actual upload to the cloud (MediaVault
  never authenticates against Google APIs server-side).
- **Language/runtime**: Python 3.7+ (relies on dict insertion order; uses
  f-strings; type hints absent).
- **Invocation model**: each user action is a separate
  `python main.py <subcommand> ...` or `python mainfetch.py fetch ...`
  invocation. There is no long-running daemon; the only shared state is
  three JSON files on disk plus sidecar `.sha256` files next to media.

The two canonical entry points are **`main.py`** (everything local + push +
restore) and **`mainfetch.py`** (Selenium download from Google Photos). All
other `.py` files at the project root are historical snapshots and must be
ignored when reasoning about current behavior.

---

## 2. Tech Stack

### Runtime / interpreter
- **Python 3.7+** (must preserve dict insertion order — relied on by
  `cmd_sort` in `main.py`). The `.venv/` at the project root is the
  development interpreter.

### Direct Python dependencies (`requirements.txt`)
| Package | Used by | Purpose |
|---|---|---|
| `pymediainfo` | `main.py` only | Wraps `MediaInfo.dll` to extract resolution / codec / HDR / audio / subtitle tracks during `cmd_prep` — dead import in `mainfetch.py` was removed |
| `selenium` | `mainfetch.py` | Drives Chrome via the DevTools remote-debugging protocol to navigate Google Photos and trigger downloads |
| `undetected-chromedriver` | listed in requirements but **not imported anywhere** — leftover from an earlier iteration |

### Additional Python imports actually used (not in `requirements.txt`)
- `requests` — `cmd_set_poster` / `cmd_set_fanart` in `main.py` for
  downloading `poster.jpg` / `fanart.jpg` artwork into media folders.
- `webdriver_manager.chrome.ChromeDriverManager` — `mainfetch.py` calls
  this to auto-fetch the matching `chromedriver` binary.
- Standard library: `os`, `json`, `sys`, `hashlib`, `subprocess`, `shutil`,
  `re`, `math`, `time`, `stat`, `datetime`, `pathlib`.

### External binaries (must be on disk in known locations)
| Tool | Path (hardcoded) | Used for |
|---|---|---|
| `mkvmerge` (MKVToolNix) | `C:\Program Files\MKVToolNix\mkvmerge.exe` | Splitting an `.mkv` into ~10 GB chunks; merging chunks back during restore |
| `adb` (Android Platform Tools) | resolved from `PATH` | `adb shell mkdir`, `adb push -p` to copy chunks to `/sdcard/Media/...` on the Pixel |
| `chrome.exe` | `C:\Program Files\Google\Chrome\Application\chrome.exe` (falls back to `Program Files (x86)`) | Launched as a child process with `--remote-debugging-port=9222` so Selenium can attach |
| `chromedriver.exe` | managed by `ChromeDriverManager` (cached under `%USERPROFILE%\.wdm\`) | Selenium WebDriver client/server protocol |
| `MediaInfo.dll` | bundled with `pymediainfo` wheel | Reading container/track metadata |

### External systems / services
- **Pixel phone** over USB-debugging ADB (`/sdcard/Media` on device).
- **Google Photos** web UI (`https://photos.google.com`) — accessed via a
  user-logged-in persistent Chrome profile; no API key.

---

## 3. Repository Layout

```
C:\Users\harin\PycharmProjects\MediaVault\
|
|-- main.py                          ACTIVE — local pipeline + ADB push + restore (1621 lines)
|-- mainfetch.py                     ACTIVE — Selenium fetch from Google Photos (507 lines)
|-- requirements.txt                 ACTIVE — pymediainfo / undetected-chromedriver / selenium
|-- ARCHITECTURE.md                  this document
|-- README.md                        user-facing overview
|-- .gitignore                       see below; excludes a.json, PLAN.md, resources/
|
|-- tools/
|   `-- migrate_lib.py               AUX (one-shot) — splits legacy library.json into the 3 category files
|
|-- archive/                         git-tracked history; NOT used at runtime
|   |-- main/
|   |   |-- main_clean.py            byte-identical backup of main.py
|   |   |-- main_newww.py            earlier main.py (combined push/replace, single library.json)
|   |   |-- main_old.py              earliest prep-only prototype
|   |   |-- main_perfect.py          pre-balanced-split version
|   |   |-- main_workingprep.py      minimal prep-only snapshot
|   |   `-- mainneww.py              early push integration
|   |-- mainfetch/
|   |   |-- mainfetch_clean.py       byte-identical backup of mainfetch.py
|   |   `-- mainfetch_<variant>.py   8 historical snapshots (singleworking, batchworking, workingserial, etc.)
|   |-- legacy/
|   |   |-- index_file.py            old standalone SHA256 indexer; predates the manual-ID design
|   |   `-- media_library.json       stale output of index_file.py (one entry); unused
|   |-- unrelated/
|   |   `-- a.json                   gitignored; openclaw-tool API keys (NOT part of MediaVault)
|   `-- transcripts/                 captured session artifacts
|
|-- resources/                       gitignored — local snapshots of the three library JSONs +
|                                    usage_commands.txt for offline analysis; NOT the source of truth
|-- docs/                            placeholder (.gitkeep only)
|-- assets/                          placeholder (.gitkeep only)
|-- tests/                           placeholder (.gitkeep only); no test suite exists
|
|-- .venv\                           dev virtualenv (Python interpreter + site-packages)
|-- .claude\                         Claude Code settings (settings.json, settings.local.json, agents/)
|-- .idea\                           PyCharm project files
```

The runtime-relevant scripts are `main.py` and `mainfetch.py` at the
project root. Everything under `archive/` is for git history only and
must be ignored when reasoning about current behaviour.

Library JSON files live **outside** the repo, under `C:\Media\`:

```
C:\Media\
|-- library_movies.json              ACTIVE  (keys: mov-*)
|-- library_series.json              ACTIVE  (keys: tv-* + season_map entries)
|-- library_anime.json               ACTIVE  (keys: ani-* + season_map entries)
|-- library.json                     LEGACY  pre-migration combined file (read-only backup)
|-- library - Copy.json              LEGACY  hand-made backup of legacy library.json
|-- library - Copy (2).json          LEGACY  hand-made backup of legacy library.json
|
|-- Movies\                          media root for `scan_unprepped` Movies category
|-- Series\                          media root for Series category
|-- Anime\                           media root for Anime category
|-- Utils\
    |-- ChromeProfile\               Selenium-attached Chrome user data dir for movies
    |-- ChromeProfile_TV\            Selenium-attached Chrome user data dir for TV + anime
```

> The current `main.py` and `mainfetch.py` read/write
> `library_movies.json`, `library_series.json`, `library_anime.json`
> from `C:\Media\`. The older single `C:\Media\library.json` is no
> longer the source of truth and is only consumed by
> `tools/migrate_lib.py`. `archive/legacy/media_library.json` is
> unrelated to the live system and unused.

---

## 4. Active vs Legacy File Inventory

| File | Status | Purpose |
|---|---|---|
| `main.py` | **ACTIVE** | Single CLI entry for prep/split/push/replace/restore/scan/sort/local_status and dispatching fetch |
| `mainfetch.py` | **ACTIVE** | Selenium-driven Google Photos download + hash-matched routing into per-entry `restore/` folder |
| `tools/migrate_lib.py` | **AUX (one-shot)** | Migrates legacy `library.json` into the three category files; safe to re-run, idempotent |
| `requirements.txt` | **ACTIVE** | Lists `pymediainfo`, `undetected-chromedriver`, `selenium` (the active codebase also imports `requests` and `webdriver-manager` which are missing from this file) |
| `archive/main/*.py` | LEGACY | Six historical snapshots of `main.py` (e.g. `main_old.py`, `main_perfect.py`, `main_newww.py`). `main_clean.py` is byte-identical to the active `main.py`. |
| `archive/mainfetch/*.py` | LEGACY | Nine historical snapshots of `mainfetch.py`. `mainfetch_clean.py` is byte-identical to the active `mainfetch.py`. All older variants use the pre-split single `library.json` and predate the dual Chrome-profile + parallel-trigger-and-harvester logic. |
| `archive/legacy/index_file.py` | LEGACY | Standalone SHA256 indexer that writes `media_library.json`. Not invoked by `main.py`; predates the manual-ID library design. |
| `archive/legacy/media_library.json` | LEGACY | Output of `index_file.py`; contains one stale entry; do not edit. |
| `archive/unrelated/a.json` | UNRELATED | API keys / bot tokens for an unrelated "openclaw" tool. Gitignored. NOT loaded by MediaVault. |

When the task says "two active files", it means **`main.py` and
`mainfetch.py` are the only scripts a user ever invokes**. Everything
under `archive/` is either a backup, a migration aid, or an unrelated
artifact preserved for git history.

---

## 5. Entry Points

Both entry points use stdlib `argparse`-style positional parsing via
manual `sys.argv` walking (no `argparse`/`click`).

### `python main.py <subcommand> ...`

All subcommands and their argument shapes (see `main.py:1397` onward).
Brackets denote optional args; `[id]` is the manual library ID like
`mov-en-2024-inception` or `tv-en-2016-strangerthings-s01e03`.

| Subcommand | Signature | Function |
|---|---|---|
| `prep` | `prep [id] [filepath]` | `cmd_prep` — index a new local file |
| `prep_season` | `prep_season [base_id] [folder]` | `cmd_prep_season` — batch-prep an entire season folder |
| `prep_push_rep` | `prep_push_rep [id] [filepath] [SIZE_MB/SIZE_GB/COUNT val]` | `cmd_prep_push_rep` — full pipeline on one movie |
| `prep_push_rep_season` | `prep_push_rep_season [id] [folder] [SIZE_MB/SIZE_GB/COUNT val] [episodes <range>]` | `cmd_prep_push_rep_season` — sequential pipeline for a season |
| `fetch_restore` | `fetch_restore [id] [OPT: episodes <range>]` | `cmd_fetch_restore` — dispatch fetch then restore |
| `set_search` | `set_search [id] [term]` | `cmd_set_search` |
| `set_poster` | `set_poster [id] [url]` | `cmd_set_poster` |
| `set_fanart` | `set_fanart [id] [url]` | `cmd_set_fanart` |
| `set_uploaded` | `set_uploaded [id]` | `cmd_set_uploaded` — force `onboarded` (multi-part rescue) |
| `scan_unprepped` | `scan_unprepped` | `cmd_scan_unprepped` — find video files on disk not in any library |
| `check` | `check [id]` | `cmd_check` — re-hash file and compare to library entry |
| `local_status` | `local_status [limit_size]` | `cmd_local_status` — show pending uploads + greedy bin-packing into `limit_size` |
| `push` | `push [id] [SIZE_MB/SIZE_GB/COUNT val] [chunks 1-4]` | `cmd_push` |
| `push_group` | `push_group [id] [SIZE_..] [episodes 1-3]` | `cmd_push_group` |
| `replace` | `replace [id]` | `cmd_replace` — swap original with a tiny valid video placeholder generated by ffmpeg (`make_video_dummy`) |
| `replace_group` | `replace_group [id]` | `cmd_replace_group` |
| `repair_dummies` | `repair_dummies [optional: id_prefix]` | `cmd_repair_dummies` — walk all `status=="archived"` entries and upgrade legacy text-blob dummies to valid video dummies |
| `verify_restore` | `verify_restore [id]` | `cmd_verify_restore` — dry-run hash check of files in `restore/` |
| `restore` | `restore [id]` | `cmd_restore` — re-merge chunks + verify + move into place |
| `restore_group` | `restore_group [id]` | `cmd_restore_group` |
| `sort` | `sort` | `cmd_sort` — re-order JSONs by lang -> year -> size |
| `fetch` | `fetch [id] [OPT: episodes <range>]` | `cmd_dispatch_fetch` — spawns `python mainfetch.py fetch ...` |

> **`episodes` keyword is a required literal trigger.** For `fetch`,
> `fetch_restore`, and `prep_push_rep_season`, the word `episodes` must
> appear as its own argument immediately before the range value.  
> ✅ `fetch tv-TheBoys episodes 1-3` → `epr = "1-3"`  
> ❌ `fetch tv-TheBoys 1-3` → `epr` stays `None`, range silently ignored  
> Parsing: `fetch`/`fetch_restore` use a fixed positional check
> (`sys.argv[3] == "episodes"`, `main.py:1606,1619`); `prep_push_rep_season`
> uses a token-scanner loop (`main.py:1464-1479`) so the keyword can appear
> anywhere after the folder path.

### `python mainfetch.py fetch <id> [episodes <range>]`

Single entry: `cmd_fetch_route(manual_id, ep_range)` at `mainfetch.py:455`.

Note the argv parsing in `mainfetch.py:497-507` requires `fetch` as
`sys.argv[1]` and the ID at `sys.argv[2]`; episodes go at positions 3-4.
`main.py:cmd_dispatch_fetch` (line 1350) constructs exactly that argv shape.

---

## 6. Data Model and Library State

### 6.1 The three library JSON files

`main.py` and `mainfetch.py` both define:

```python
LIBRARY_MOVIES = r'C:\Media\library_movies.json'
LIBRARY_SERIES = r'C:\Media\library_series.json'
LIBRARY_ANIME  = r'C:\Media\library_anime.json'
```

`load_library()` reads all three and merges into a single dict in memory
keyed by manual ID. `save_library()` splits back by ID prefix:

- key starts with `mov`  -> movies file
- key starts with `tv`   -> series file
- key starts with `ani`  -> anime file
- otherwise -> fallback to movies file (legacy keys without a prefix)

This split-by-prefix is hardcoded at `main.py:75-84`.

### 6.2 Manual ID format

IDs are human-typed (or generated by `cmd_prep_season`) free-form
strings. `cmd_prep` does NOT validate them — anything goes — but the
first three characters select which library JSON the entry lands in.

**Canonical shapes (the ones the README documents)**

- Movies: `mov-<lang2>-<year>-<slug>` e.g. `mov-en-2025-f1`,
  `mov-ta-2024-maharaja`, `mov-ma-2025-eko`.
- TV series: `tv-<lang2>-<year>-<slug>-s<NN>e<MM>` e.g.
  `tv-en-2016-strangerthings-s01e03`. Parent ID is the same string
  minus the `eMM` suffix (`tv-en-2016-strangerthings-s01`).
- Anime: `ani-<lang2>-<year>-<slug><EE>` (no `e` separator before the
  episode number) e.g. `ani-ja-2006-deathnote07`. Parent ID strips the
  trailing digits (`ani-ja-2006-deathnote`).
- Half-episodes are floats appended to the episode segment: `...e16.5`
  (series convention) or `...165.5`/`...16.5` (anime). Live data has
  3 half-eps (all anime: `ani-ja-2012-kurokosbasketball-s0122.5`,
  `-s0216.5`, `-s0325.5`).
- Lang codes seen in production: `en`, `ta` (Tamil), `hi` (Hindi),
  `ja` (Japanese, anime), `te` (Telugu), `ma` (Malayalam), `kor`
  (Korean). The `cmd_sort` priority map only covers `en/ta/hi`;
  everything else sorts at priority 99.

**Non-canonical shapes that exist in production**

These work today but step around the documented conventions:

- **Mini-series with NO `-sNN-` segment** (the "Chernobyl" pattern):
  `tv-en-2019-chernobyle01..e05`. The auto-parent regex strips `eNN$`
  directly so the parent is `tv-en-2019-chernobyl`. Safe for
  one-season shows; would collide if a second season were added.
- **Anime WITH an explicit `-sNN` season segment** (the "Kuroko's
  Basketball" pattern): `ani-ja-2012-kurokosbasketball-s0101..s0125`.
  Anime library prefix (`ani-`) + season segment + 2-digit episode with
  no `e`. Used for multi-season anime where pure absolute numbering
  would collide. **This shape is only safe via `cmd_prep_season`**,
  which passes `parent_id` explicitly. Calling `python main.py prep
  ani-ja-2012-kurokosbasketball-s0125 ...` directly on such an ID would
  trip the anime auto-parent regex (`^(ani-.*?)[\d\.]+$`) and produce a
  junk parent `ani-ja-2012-kurokosbasketball-s` (just `-s`). See
  Section 16 for details.

**Typos that have slipped through**

`cmd_prep` writes anything you give it. Live data contains
`mov-en-20013-conjuring` (5-digit year — typo for 2013).
`parse_metadata_from_id` only accepts 4-digit years, so
`metadata.year = None` and the entry sorts at `year=0` (i.e. at the
bottom). No code today flags this kind of malformed ID.

**Live-data snapshot (2026-05-25) — counts per library**

| Library | Leaves | Season maps | local_ready | archived |
|---|---:|---:|---:|---:|
| `library_movies.json` | 102 | 0 | 2 | 100 |
| `library_series.json` | 290 | 28 | 86 | 204 |
| `library_anime.json` | 140 | 5 | 32 | 108 |

Two notable observations from the live data:

- **0 anime entries have `split_info`** — every anime episode in
  production has fit under the user's typical `SIZE_MB 9900` chunk
  threshold. The chunk path in `mainfetch.fetch_single_entry` has
  effectively never run against the TV Chrome profile.
- **0 duplicate file hashes and 0 duplicate chunk hashes** across all
  three libraries. The fetch-side hash-routing is provably collision-free
  for this corpus.

### 6.3 Entry schemas

There are **two entry types** per library: leaf entries and season-map
parents.

#### Leaf entry (one per file)

```jsonc
"mov-en-2025-f1": {
  "short_id":      "68b7b8",                  // 6-char md5 of manual ID
  "filename":      "F1.The.Movie....mkv",
  "folder_path":   "C:\\Media\\Movies\\English\\Racing\\F1...",
  "status":        "archived",                // see state machine below
  "uploaded":      true,                      // independent boolean
  "search_term":   "F1.The.Movie.... [68b7b8].mkv",  // Google Photos search query
  "hash":          "e2a0221b...d92d",         // SHA256 of ORIGINAL file
  "metadata": {
    "title":      "<the manual ID>",
    "year":       2025,
    "genre":      [],
    "added_date": "2026-01-02"
  },
  "tech_spec": {
    "resolution":     "2160p" | "1080p" | "720p" | "<H>p",
    "width_height":   "3840x2160",
    "video_codec":    "HEVC" | "AVC" | ...,
    "hdr":            "Dolby Vision / SMPTE ST 2086" | "SDR" | ...,
    "frame_rate":     "Unknown" | string,
    "audio":          "Dolby TrueHD with Dolby Atmos" | ...,
    "audio_channels": 8,
    "audio_language": "en" | "ta" | ...,
    "subtitles":      ["en", "es", ...],
    "duration_mins":  155,
    "size_bytes":     76210749463
  },
  "split_info": {                              // OPTIONAL — present only if push split the file
    "is_split":     true,
    "method":       "SIZE_MB" | "SIZE_GB" | "COUNT",
    "val":          "8000",
    "total_chunks": 10,
    "chunks": [
      { "filename": "F1...chunk.001.mkv", "hash": "a768bb08..." },
      ...
    ]
  },
  "parent_id":    "tv-ta-2024-aindhamvedham-s01"  // OPTIONAL — present if part of a season
}
```

#### Season-map parent (one per series/anime season)

```jsonc
"ani-ja-2006-deathnote": {
  "type":            "season_map",
  "folder_path":     "C:\\Media\\Anime\\Classic\\Death Note (Complete Series)...",
  "total_episodes":  37,
  "children": [
    "ani-ja-2006-deathnote01",
    "ani-ja-2006-deathnote02",
    ...
  ]
}
```

Season maps have no `hash`, no `filename`, no `tech_spec`. They are
recognised throughout the code by `entry.get("type") == "season_map"` and
deliberately skipped in `scan_unprepped`, `local_status`, and `sort`.

### 6.4 Status state machine

`status` and `uploaded` are tracked separately. The transitions actually
written by the code:

```
                cmd_prep
        (none) --------------> status="local_ready", uploaded=False
                                       |
                                       v
                                  cmd_push (all chunks uploaded successfully and no chunk_range filter)
                                       |
                                       v
                              status="onboarded", uploaded=True
                                       |
                                       v
                                  cmd_replace
                                       |
                                       v
                              status="archived"   (original deleted, dummy on disk;
                                                    uploaded stays True)
                                       |
                                       v
                                  cmd_restore (after mainfetch.py populated `restore/`)
                                       |
                                       v
                              status="restored_local"
                                       |
                                       |  (if user re-pushes, the cycle restarts via cmd_push)
                                       v
                                  cmd_set_uploaded  -- force-override to onboarded
                                                       (used when ADB upload was multi-part
                                                        and library got out of sync)
```

Observed in live data as of 2026-05-25: only `local_ready` (120 entries
across the three libraries) and `archived` (412 entries) appear at rest.
`onboarded` and `restored_local` are transient — `push → replace` and
`fetch → restore` usually run back-to-back, so the intermediate state
is rarely captured in a snapshot. See Section 6.2 for the per-library
breakdown.

Important quirks:

- `cmd_push` only marks `uploaded=True` / `status="onboarded"` when
  **all** chunks succeed AND no `chunk_range` filter was applied
  (`main.py:699-708`). Partial uploads explicitly do **not** change state.
- `cmd_restore` rewrites `hash` after merging chunks because `mkvmerge`
  produces a new container with a different SHA256
  (`main.py:927`). Chunk hashes in `split_info` are NOT touched.
- `cmd_prep` short-circuits if the entry exists and is already
  `uploaded`/`archived`, OR if the file on disk is < 1 KB (dummy detected)
  — `main.py:303-312`.

### 6.5 Sidecar files (on disk, next to each media file)

`cmd_prep` writes, in the media's `folder_path`:
- `uid` (no extension) — contains the 6-char short_id
- `<short_id>.sha256` — line in the format `<hash> *<filename>` (the
  asterisk follows standard `sha256sum` "binary mode" syntax)

`cmd_push` also writes per-chunk hashes into a permanent `checksums/`
subfolder inside `folder_path`:
- `<chunk_name>.sha256` (one file per chunk)

These sidecars are belt-and-suspenders backup of what's in the JSON
libraries — they are NEVER read by `main.py` itself. They exist for manual
recovery if the JSON files are ever lost or corrupted.

---

## 7. main.py Deep Dive

File: `C:\Users\harin\PycharmProjects\MediaVault\main.py` (1621 lines).

### 7.1 Configuration block (lines 15-32)
Hardcoded constants: library paths, `LOCAL_ROOT = "C:\\Media"`,
`REMOTE_ROOT = "/sdcard/Media"`, `MKVMERGE_PATH`, `MAINFETCH_SCRIPT`,
folder names (`_parts`, `checksums`, `restore`), and the recognised
extension tuple `('.mkv', '.mp4', '.avi', '.mov')`.

### 7.2 Utility functions

| Function | Lines | Behaviour |
|---|---|---|
| `load_library()` | 38-52 | Reads all 3 library JSONs and `dict.update()`s into one dict; **crashes with `sys.exit(1)` on corrupt/unreadable file** to prevent silent data-loss |
| `save_library(data)` | 55-71 | Splits dict by ID prefix (`mov`/`tv`/`ani`/fallback->mov); **writes atomically via `tempfile.mkstemp` + `os.replace()`** to survive mid-save kills |
| `generate_short_id(long_id)` | 95-98 | `hashlib.md5(long_id.encode()).hexdigest()[:6]` — deterministic 6-char ID |
| `calculate_file_hash(filepath, block_size=65536)` | 101-115 | SHA256 streaming hash, 64 KB blocks; prints inline progress |
| `human_readable_size(size_bytes)` | 118-124 | Standard "B/KB/MB/GB/TB/PB" formatter |
| `parse_size_str(size_str)` | 127-136 | Regex parser for `"40gb"`, `"500mb"` -> bytes |
| `get_tech_specs(filepath)` | 139-180 | Walks `MediaInfo.parse(...).tracks`, classifies resolution by width, picks HDR/codec/first-audio/all-subtitle-languages |
| `parse_metadata_from_id(manual_id)` | 183-190 | Naive parser that pulls the first 4-digit number it finds as `year` |

### 7.3 The balanced-split algorithm (lines 196-269)

`split_video_file(input_path, output_dir, method, value_str, file_id="")`

This is the most subtle piece of logic in the codebase. The naive approach
would be `mkvmerge --split size:10GB` against a 25 GB file, expecting 3
chunks of ~10 GB. In practice `mkvmerge` cuts at keyframes, and any
"leftover" past the last hard-limit cut becomes a tiny 3rd or 4th chunk
that wastes a whole Google Photos upload slot.

The balanced split avoids this by **pre-computing the number of chunks
needed** and asking mkvmerge for a *softer* per-chunk size such that the
remainder is folded back into the previous chunk:

```
total_size_bytes  = os.path.getsize(input_path)
limit_bytes       = val * 1024**2 (MB) or val * 1024**3 (GB)
num_chunks        = math.ceil(total_size_bytes / limit_bytes)
total_size_mb     = total_size_bytes / (1024*1024)
split_size_mb     = int(math.ceil(total_size_mb / num_chunks)) + 10
split_arg         = f"{split_size_mb}M"
subprocess.run([mkvmerge, "-o", pattern, "--split", f"size:{split_arg}", input_path])
```

Worked example for `SIZE_GB=10` on a 15 GB file:

1. `limit_bytes = 10 GB`.
2. `num_chunks = ceil(15/10) = 2`.
3. `split_size_mb = ceil(15360 / 2) + 10 = 7690 MB`.
4. mkvmerge tries to cut at ~7.69 GB. The first chunk is ~7.69 GB. The
   "leftover" is the remaining ~7.31 GB, which fits as the second (and
   final) chunk. No tiny third chunk.

The `+10 MB` buffer is the critical fudge. Without it, keyframe drift can
push the first cut slightly past the target, leaving a near-zero-byte
sliver as a third chunk. Reference: `main.py:228-231`.

Same logic with `COUNT` method (lines 238-251): given an explicit `parts`
count, compute `approx_size_mb = ceil(total_size_mb / parts) + 10` and
hand that to mkvmerge.

The output pattern includes the UID tag so chunks have unique names even
across the user's whole library:

```
"<filename_base> [<short_id>].chunk.%03d.mkv"
```

`mkvmerge` substitutes `%03d` with 001, 002, ....

### 7.4 UID (short_id) system

- **Source of truth**: `short_id = md5(manual_id)[:6]` — deterministic,
  derived only from the user-provided manual ID. Same manual ID always
  produces the same UID.
- **Why MD5**: short, fast, collision-resistant enough for ~thousands of
  entries (~2^24 buckets; ~16 million possible UIDs vs ~1000s of files in
  practice).
- **Where it's embedded**:
  - In the chunk filename pattern: `... [68b7b8].chunk.001.mkv`.
  - In the renamed remote filename for non-chunked single-file uploads:
    `<name> [<short_id>]<ext>` is written to the phone
    (`main.py:666-670`).
  - In the `search_term` field: `"<original_filename_base> [<short_id>]<ext>"`
    — this is what `mainfetch.py` types into Google Photos' search box
    later.
  - In sidecar files: `uid` and `<short_id>.sha256` in the folder.
- **Why include it in the filename**: Google Photos' search is fuzzy and
  multiple ripped copies of the same title can collide. The 6-char UID
  acts as a hard discriminator so the Selenium fetcher can land on
  exactly the right file.

### 7.5 ADB push flow (`cmd_push`, lines 542-711)

High-level sequence inside one `cmd_push` call:

1. **Load library**, locate entry, print parent-season info if any.
2. **Compute remote target dir** — with a cross-drive failsafe
   (`main.py:562-566`):
   ```python
   try:
       rel_path = os.path.relpath(local_folder, LOCAL_ROOT)
   except:
       rel_path = os.path.basename(local_folder)
   remote_target_dir = f"{REMOTE_ROOT}/{rel_path}".replace("\\", "/")
   ```
   `os.path.relpath()` raises `ValueError` on Windows when `local_folder`
   and `LOCAL_ROOT` are on **different drive letters** (e.g. `D:\Movies D`
   vs `C:\Media`).  The bare `except` silently catches this and falls back
   to `os.path.basename(local_folder)`, so a file hosted on `D:\Movies D`
   is pushed to `/sdcard/Media/Movies D` instead of a full relative path.
   This is the primary mechanism that makes the split-drive setup
   (`C:\Media`, `D:\`, SSD) work without any per-drive configuration.
3. **Shell-escape single quotes** in the path before passing to
   `adb shell mkdir` (`main.py:572-576`) — handles titles like
   `Sorcerer's Apprentice`.
4. **Resume vs new split branch (`main.py:585-630`)**:
   - If `<folder>/_parts/` exists and has chunks, treat them as
     pre-existing chunks and resume (no re-split, no re-hash).
   - Else if `split_method` and `split_val` were passed AND the file is
     bigger than the target chunk size, call `split_video_file` and then
     immediately SHA256-hash every chunk into `checksums/<chunk>.sha256`
     and into `entry["split_info"]["chunks"]`. The library is saved
     **before** any upload begins, so an interrupted push can resume
     against verified hashes.
   - Else upload the single file as-is.
5. **Optional chunk-range filter** (`chunks 1-4`): walks the file list
   and keeps only chunks whose 3-digit number is in range
   (`main.py:632-657`). Used for re-pushing specific failed parts.
6. **Upload loop** (`main.py:659-693`):
   - For each file path, compute the remote filename. Chunks keep their
     names. Non-chunk (single-file) uploads are renamed to embed the
     short_id: `<name> [<short_id>]<ext>`.
   - Run `adb push -p <local> <remote>` — the `-p` flag enables ADB's
     built-in progress meter, which is left visible to the user (no
     custom progress bar).
   - **Critical safety check**: after a successful chunk upload, the
     local chunk is deleted *only if* its path contains the
     `_parts` segment (`main.py:680`). This protects against accidentally
     deleting non-chunk source files if logic is ever rearranged.
   - On any failure, break the loop and leave `_parts/` populated for
     resume.
7. **Post-loop bookkeeping**:
   - Remove `_parts/` if it's empty.
   - If all chunks succeeded AND no `chunk_range` filter was active,
     set `uploaded=True`, `status="onboarded"`, save library.
   - Partial uploads return success but leave state untouched.

ADB device detection is implicit: the first `adb shell mkdir` either
succeeds (device connected) or raises, and the function bails. There is
no explicit `adb devices` check.

### 7.6 Placeholder / dummy-file system (`cmd_replace`, `make_video_dummy`, `cmd_repair_dummies`)

When the user is confident the upload is done, `cmd_replace` swaps the
large original for a tiny **valid video** placeholder produced by
ffmpeg. The placeholder is a real playable container so Plex, Kodi, and
file-manager thumbnailers don't choke on a text blob masquerading as
`.mkv`.

#### Per-container recipe table (`DUMMY_RECIPE_BY_EXT`)

Every container gets a single ffmpeg invocation with these shared video
parameters: `color=c=black:s=128x72:r=24`, `libx264` baseline /
`yuv420p` / `-b:v 50k` / `preset veryfast`. The audio side differs by
extension:

| Extension | Audio codec | Audio source | Duration | Typical size |
|---|---|---|---|---|
| `.mkv` | `pcm_s16le` | `anullsrc=cl=stereo:r=44100` (silence) | 0.05 s | 9,672 bytes |
| `.avi` | `pcm_s16le` | `anullsrc=cl=stereo:r=44100` (silence) | 0.05 s | 18,978 bytes |
| `.mp4` | `aac -b:a 64k` | `sine=frequency=440:sample_rate=44100` (440 Hz tone) | 0.5 s | 6,650 bytes |
| `.mov` | `aac -b:a 64k` | `sine=frequency=440:sample_rate=44100` (440 Hz tone) | 0.5 s | 6,701 bytes |

**Why the recipes differ:**

- PCM `s16le` is incompatible with ISO-BMFF containers (`.mp4`, `.mov`),
  so those containers use AAC.
- AAC compresses silence near-perfectly (~2 KB regardless of bitrate
  at short durations), so the `.mp4` / `.mov` recipe drives the encoder
  with a 440 Hz sine tone to produce real entropy and land above a
  plausible audio payload size.
- `.mov` mirrors the `.mp4` recipe for cross-tool consistency (QuickTime,
  Plex, and ffprobe all parse it cleanly).
- `.avi` uses PCM to avoid the MP3 framing overhead that previously
  bloated AVI dummies. At 0.05 s the raw PCM is still under 20 KB.

All four recipes produce containers that ffprobe parses and Plex indexes
(verified against live library).

#### `make_video_dummy(output_path, extension)` (`main.py:334`)

Single helper used by both `cmd_replace` and `cmd_repair_dummies`.
There is one code path — no derived-mode or fallback-mode distinction.

1. **Resolve ffmpeg** via `resolve_ffmpeg()` (`main.py:325`): first
   checks the hardcoded `FFMPEG_PATH` constant (the bundled Emby
   Server ffmpeg), then falls back to `shutil.which("ffmpeg")`. If
   neither is available the function prints an error and returns
   `False`. Callers must handle the `False` return.
2. **Look up recipe**: `DUMMY_RECIPE_BY_EXT.get(ext_lower,
   DUMMY_RECIPE_BY_EXT[".mp4"])` — unknown extensions fall back to the
   `.mp4` recipe.
3. **Build command**: two `-f lavfi` inputs (video color source + audio
   source from the recipe), one output with the codec parameters from
   the recipe. No `-movflags +faststart` (irrelevant at these file
   sizes).
4. **Atomic write**: ffmpeg writes to a sibling
   `<output_path>.dummy_tmp<ext>` temp file. On success the helper
   `os.replace()`s it into `output_path`. If ffmpeg fails (non-zero
   exit or missing tmp file), the last 5 lines of stderr are printed
   and the helper returns `False`.

#### `cmd_replace(manual_id)` flow

1. Refuse to act if `uploaded != True`.
2. Call `make_video_dummy(original_path, ext)` — produces the dummy
   in a temp file via the recipe table above.
3. If `make_video_dummy` returns `False`, abort with an error message
   — **no legacy text-blob fallback**.
4. Delete the original with **3 retries** and a `chmod S_IWRITE` to
   clear any read-only flag. Retries exist because Plex, Windows
   Search, or a video player may be holding the file open.
5. `os.rename(tmp_path, original)` — the dummy takes the exact name
   of the original.
6. Set `status = "archived"`. Library JSON mutation is identical to
   what was planned; only the content of the temp file changed.

#### `cmd_repair_dummies(prefix_filter=None)`

Generic regenerator that brings every archived dummy on disk up to the
current recipe spec. Walks every leaf entry with `status ==
"archived"`, optionally filtered by a manual-ID prefix.

For each candidate:

1. Skip if file is missing (`missing` counter).
2. Skip if file extension is not in `VIDEO_EXTENSIONS` (`skipped`
   counter — non-video filenames have no applicable recipe).
3. Skip if file size ≥ `DUMMY_MAX_BYTES` (`skipped` — looks like a
   real video, not a dummy).
4. Call `make_video_dummy(file_path, ext)` using the per-container
   recipe. Prints `Regenerating dummy: <path>` for each file.
   On failure, increment `failed` and continue.
5. Replace the existing dummy with the regenerated video dummy.

There is no magic-header sniff — any archived-entry video file under
`DUMMY_MAX_BYTES` is treated as a dummy and regenerated. This makes the
command **idempotent in spec**: running it twice leaves files at the
correct recipe. It is not byte-identical across runs due to mild ffmpeg
jitter (timestamps, encoder state), but functionally equivalent.

Prints a final summary: `scanned / regenerated / skipped / missing /
failed`.

Live bulk run (2026-05-27): scanned 424, regenerated 423, skipped 1
(non-video filename), missing 0, failed 0. Every regenerated `.mkv`
landed at exactly 9,672 bytes.

#### Dummy-sniff threshold

The `DUMMY_MAX_BYTES` constant (200,000) is reused by `cmd_prep` and
`cmd_check` as a sniff test: any file smaller than 200 KB is treated as
a dummy and skipped. This threshold comfortably exceeds the largest
video dummy (~19 KB AVI) while remaining far below the smallest real
archived video.

### 7.7 Restore flow

#### `cmd_verify_restore(manual_id)` (lines 838-892) — DRY RUN

Re-reads each file in `<folder>/restore/` and compares SHA256 against
either:
- For split: each chunk's hash in `entry["split_info"]["chunks"]`.
- For single: the entry's `hash`.

Prints pass/fail per file. Does not write or move anything.

#### `cmd_restore(manual_id)` (lines 895-984) — DESTRUCTIVE

For split entries:
1. Verify all chunk filenames exist in `<folder>/restore/`. If any
   missing, skip (so partial fetches don't half-restore).
2. Call `merge_video_files(chunks, target_path)` which runs
   `mkvmerge -o <target> chunk1 +chunk2 +chunk3 ...`. The `+` syntax
   tells mkvmerge to append, not multiplex.
3. Re-hash the merged file (its SHA256 differs from the original — the
   container has been re-built) and overwrite `entry["hash"]`.
4. Delete each chunk file. Remove the now-empty `restore/` folder.
5. Set `status = "restored_local"`.

For single-file entries:
1. SHA256 the file in `restore/` and refuse to proceed if it doesn't
   match `entry["hash"]`.
2. `shutil.move` it back into `folder_path` (overwriting the dummy).
3. Clean up empty `restore/`.
4. Set `status = "restored_local"`.

`cmd_restore_group` (lines 987-1022) iterates a season's children with
optional `episodes 1-3` filter and is tolerant: each child's
`cmd_restore` self-checks for missing chunks, so a partial fetch just
results in some children skipping cleanly.

### 7.8 Other commands

- `cmd_check(manual_id)` (521-539) — re-hash the file in place and
  compare to library `hash`. Bails on dummy.
- `cmd_set_search`, `cmd_set_poster`, `cmd_set_fanart` (391-455) —
  manual overrides. `set_poster`/`set_fanart` use `requests` to download
  with a `Mozilla/5.0` User-Agent and save as `poster.jpg`/`fanart.jpg`
  inside `folder_path`.
- `cmd_set_uploaded(manual_id)` (458-471) — emergency override; forces
  `uploaded=True`, `status="onboarded"` so the user can run `replace`.
  Used when chunks were pushed in multiple sessions and the library
  state got stuck.
- `cmd_prep_season(base_id, folder_path)` (474-518) — walks a folder of
  videos and runs `cmd_prep` for each, auto-deriving episode numbers
  from filenames using two strategies:
  - **Strategy 1**: regex `[sS]\d+[eE](\d+(?:\.\d+)?)` (SxxExx) then
    `\d+[xX](\d+(?:\.\d+)?)` (XxYY anime convention).
  - **Strategy 2** (only when `base_id.startswith("ani-")`): a looser
    regex looking for any 1-4-digit number surrounded by `[ ._\-[]]`
    delimiters, with a guard against parsing release years
    (`19xx`/`20xx`) as episode numbers.
- `cmd_push_group(group_id, ...)` (714-759) — same logic as
  `cmd_restore_group` but for pushing: season-map mode OR prefix-match
  mode, with optional `episodes A-B` filter, skipping items already
  marked uploaded.
- `cmd_scan_unprepped()` (1162-1241) — walks `C:\Media\{Movies,Series,
  Anime}` recursively (excluding `_parts`, `checksums`, `restore`,
  `.git`, `.idea`, `__pycache__`, `Utils`) and lists every `.mkv/.mp4/
  .avi/.mov` file whose normalized path is NOT in any library entry's
  `folder_path + filename`. Prints sorted-by-size results per category.
- `cmd_local_status(limit_arg)` (1078-1159) — lists not-yet-uploaded
  items. When a `limit_arg` like `40gb` is given, runs a greedy
  first-fit-descending bin-packing pass and prints which items "fit" in
  one Pixel storage batch. Also emits ready-to-copy
  `python main.py push <id>` lines for each selected item.
- `cmd_sort()` (1025-1075) — re-orders all three JSONs by
  `(language_priority, -year, -size_bytes)` where language priority is
  `en=1, ta=2, hi=3, other=99`. Relies on Python 3.7+ dict insertion
  order being preserved.
- `cmd_prep_push_rep(...)` (1244-1283) — atomic auto-pilot for a single
  movie: prep -> push -> replace, with cleanup-on-failure that wipes
  `_parts/` so the user is back in `local_ready` state.
- `cmd_prep_push_rep_season(...)` (1286-1347) — same auto-pilot for an
  entire season, **sequential** (one episode at a time end-to-end) with
  optional `episodes A-B` filter; stops the whole batch on any push
  failure to "prevent mess".
- `cmd_dispatch_fetch(manual_id, episode_range)` (1350-1364) — shells
  out: `subprocess.run(["python", "mainfetch.py", "fetch", id, "episodes", range])`.
- `cmd_fetch_restore(manual_id, episode_range)` (1367-1391) — dispatch
  fetch, then call `cmd_restore_group`/`cmd_restore` depending on entry
  type.

---

## 8. mainfetch.py Deep Dive

File: `C:\Users\harin\PycharmProjects\MediaVault\mainfetch.py` (507 lines).

### 8.1 Configuration (lines 25-48)

Same library paths as `main.py`. Adds:

```python
CHROME_PROFILES = {
    "default": r"C:\Media\Utils\ChromeProfile",
    "tv":      r"C:\Media\Utils\ChromeProfile_TV"
}
CHROME_PROFILE_NAME       = "Default"
SYSTEM_DOWNLOADS_FOLDER   = os.path.join(os.path.expanduser("~"), "Downloads")
```

Two separate Chrome user-data directories exist because the user has two
distinct Google accounts: one with the movie collection in Photos and
another with TV/anime. Routing is decided in `cmd_fetch_route`:

```python
if manual_id.startswith("tv") or manual_id.startswith("ani"):
    active_profile = "tv"
else:
    active_profile = "default"
```

### 8.2 Selenium + Chrome attach-mode setup (`init_driver`, lines 105-147)

Rather than letting Selenium spawn Chrome with `--user-data-dir`
(Chrome refuses to load some Google sessions when launched by Selenium
in headful mode), `init_driver`:

1. `subprocess.Popen([chrome.exe, "--user-data-dir=<profile>",
   "--profile-directory=Default", "--remote-debugging-port=9222",
   "--disable-gpu", "--window-size=1920,1080", "--no-first-run",
   "--no-default-browser-check", "--disable-session-crashed-bubble",
   "about:blank"])`
2. `time.sleep(3)` — give Chrome time to bind port 9222.
3. Build a Selenium `Options` with
   `add_experimental_option("debuggerAddress", "127.0.0.1:9222")`.
4. `ChromeDriverManager().install()` to fetch/cache a matching driver.
5. `webdriver.Chrome(service=service, options=options)` — Selenium
   attaches to the already-running Chrome over the DevTools protocol.

This means **all cookies, saved logins, and Google Photos session state
live in `C:\Media\Utils\ChromeProfile*`** and persist across runs. The
user must log in once manually in each profile; thereafter the script
inherits that login.

### 8.3 Search-by-UID flow (`trigger_download`, lines 150-216)

For each file to download, fire-and-forget triggers:

1. `driver.get("https://photos.google.com")`, wait for `<body>`.
2. Send keystrokes via ActionChains:
   - `"/"` — Google Photos' "open search" shortcut.
   - `<query>` — the search term, typically
     `<filename> [<short_id>].mkv`. Falls back to `entry["search_term"]`
     or `entry["filename"]` if precision search fails (attempt 2).
   - `ENTER` — execute search.
3. Wait 3 seconds for results.
4. Click the Nth thumbnail (`index` argument). Locator strategy:
   - Primary: `driver.find_elements(By.CSS_SELECTOR, "a[href*='./photo/']")`
     filtered to visible elements wider than 50 px.
   - Fallback: XPath `//div[contains(@style, 'background-image')]` with
     same visibility/size filter.
   - Click via `driver.execute_script("arguments[0].click();", el)` to
     dodge interception by overlays.
5. Sleep 2 s for the photo player to open.
6. Send `Shift+D` — Google Photos' built-in "download original" shortcut.
   This is what actually puts a `.crdownload` into the Downloads folder.
7. Sleep 1 s, then `Escape` to close the player so the next trigger can
   navigate back to search cleanly.

The function returns `True` if the trigger was sent (it does NOT wait
for the download to complete).

### 8.4 Parallel-trigger + harvester (`fetch_single_entry`, lines 233-372)

The clever bit. For a single entry (possibly split into N chunks), the
function:

1. Builds a queue of pending downloads (each has
   `specific_query`, `fallback_query`, `fallback_index`, expected `hash`
   and `dest` folder).
2. Skips chunks/files already present in the entry's `restore/` folder.
3. Runs up to **2 attempts**:
   - **Attempt 1**: precision search — query is the chunk filename
     (`xxx.chunk.001.mkv`), index 0.
   - **Attempt 2**: fallback — query is the broader `search_term`, index
     is the chunk index `i` (so different chunks click different
     thumbnails in the search-result grid).
4. Within each attempt, the function **fires all triggers first**
   (with 2 s pauses between them) so Chrome ends up with multiple
   parallel downloads going. Then it enters the **harvester loop**.

#### The harvester loop (lines 304-366)

```
start = now()
base_timeout = 300 seconds
processed = set()
while True:
    active = files ending in .crdownload in ~/Downloads
    if time - start > base_timeout:
        if any .crdownload active:
            print "extending..." and continue (sleep 5)
        else:
            break (timeout)
    if all queue items status == "done": break

    for each completed .mkv / .mp4 file in ~/Downloads (skipping .crdownload):
        if size == 0: skip
        sleep 0.5     # stability
        hash = SHA256(file)
        match queue item by hash == queue[*].hash AND status == "pending"
        if matched:
            move file -> matched.dest/matched.filename (overwrite if exists)
            mark matched.status = "done"
        elif hash matches an already-done item:
            delete duplicate
    if no new found this iteration: sleep 5
```

Key properties:
- **Hash-based routing**: downloads land in `~/Downloads` with whatever
  filename Google Photos chose (often the original name, sometimes with
  a `(1)` suffix on duplicates). Routing relies entirely on the SHA256
  matching the expected hash from the library — not on filename
  matching. This makes the system robust to renames, mixed case, etc.
- **Self-extending timeout**: the 5-minute timer is suspended as long as
  at least one `.crdownload` exists. So a 60 GB movie download won't
  fail just because the timer expired — only true stalls (no progress
  AND no active download) trigger a real timeout.
- **Two-phase search**: precision attempt first, broader fallback
  second. The fallback uses `fallback_index = i` so that chunk N of a
  movie hits the Nth tile in the search results.

### 8.5 Routing into restore folder

For every successful match, the file is moved to:

```
<entry["folder_path"]> / restore / <expected_filename>
```

`restore/` is `mkdir`ed at the top of `build_download_queue` / `fetch_single_entry`.
The `expected_filename` is exactly what `cmd_restore` later expects to
find — either the original filename or the per-chunk
`xxx.chunk.001.mkv` name. This is what gives the restore step
deterministic input regardless of how Google Photos named the
downloaded blob.

### 8.6 Entry resolution and batch mode

- `resolve_targets(manual_id, ep_range)` (375-410): if the ID is a
  season_map, returns the list of leaf children (filtered by
  `ep_range` if provided), else returns a 1-element list with the leaf
  entry itself. The episode-range parsing supports `.5` half-episodes:
  `re.search(r'[eE](\d+(?:\.\d+)?)$', id)` then `r'x(\d+(?:\.\d+)?)$'`
  then trailing digits for anime.
- `cmd_fetch_route(manual_id, ep_range)` (455-494): picks Chrome
  profile, calls `init_driver`, iterates targets calling
  `fetch_single_entry`. Wraps in try/except/finally so KeyboardInterrupt
  closes the driver cleanly.

### 8.7 Unused / kept-for-compat stubs

- `wait_for_download(filename_snippet, timeout=300)` at line 219 — empty,
  returns None.
- `automation_download_file(driver, ...)` at line 224 — empty, returns
  False.
- `build_download_queue(entries)` at line 413 — implemented but
  effectively superseded by the inline queue building inside
  `fetch_single_entry`. Not called from `cmd_fetch_route`.

These are leftover scaffolding from earlier iterations; safe to delete
in a future cleanup.

---

## 9. Auxiliary Scripts

### 9.1 `tools/migrate_lib.py` (one-shot, 68 lines)

Reads `C:\Media\library.json` (the old combined file) and splits it
into `library_movies.json` / `library_series.json` / `library_anime.json`
by ID prefix (`tv-` -> series, `ani-` -> anime, everything else ->
movies). Writes with `indent=4`. Idempotent in the sense that re-running
it overwrites the three files with the same content (provided
`library.json` itself hasn't changed). Not invoked by anything else.

### 9.2 `archive/legacy/index_file.py` (legacy, 123 lines)

Pre-dates the manual-ID system. Indexes a file by SHA256 and writes to
`archive/legacy/media_library.json` using the full file path as the
dict key:

```jsonc
{ "C:\\Media\\Movies\\English\\Oceans.Twelve...mkv": {
    "uid":             "ff2c68d1658e",   // first 12 chars of SHA256, not the md5 UID
    "filename":        "...",
    "category":        "Movie" | "Series" | "Unknown",
    "full_path":       "...",
    "rel_path":        "...",
    "checksum_sha256": "...",
    "size_bytes":      ...,
    "status":          "indexed"
  } }
```

This file is **not** read or written by `main.py` or `mainfetch.py`. It
appears to be a debugging/exploration script that was never integrated.
The `archive/legacy/media_library.json` file contains a single stale
entry.

---

## 10. Core Workflow Walkthrough

Below is the end-to-end happy path for a single movie, with code
references.

### Stage 1 — PREP (`python main.py prep mov-en-2024-inception "C:\Media\Movies\English\Inception\Inception.mkv"`)

1. `main.py:1425` -> `cmd_prep(manual_id, filepath)`.
2. `load_library()` merges all 3 JSONs. If entry already exists with
   `uploaded=True` or `status="archived"`, OR file size < 1 KB, skip.
3. `generate_short_id(manual_id)` -> 6-char MD5 UID.
4. `calculate_file_hash(filepath)` -> streaming SHA256.
5. `get_tech_specs(filepath)` -> calls `MediaInfo.parse` and packs
   resolution / codec / HDR / audio / subtitles / duration / size.
6. Write sidecars `uid` and `<uid>.sha256` next to the file.
7. **Auto-parent detection** (lines 333-346): regex tries
   `^(.*)[eE|xX](\d+(?:\.\d+)?)$` and (for `ani-`)
   `^(ani-.*?)[\d\.]+$`. If a parent is detected, ensure a
   `season_map` parent entry exists and append this child to its
   `children` list (sorted).
8. Build `search_term` as `<filename_base> [<short_id>]<ext>`.
9. Insert entry with `status="local_ready"`, `uploaded=False`. Save.

### Stage 2 — SPLIT + PUSH (`python main.py push mov-en-2024-inception SIZE_GB 10`)

1. `main.py:1536` -> `cmd_push(mid, "SIZE_GB", "10", None)`.
2. `adb shell mkdir -p '/sdcard/Media/Movies/English/Inception'`.
3. No `_parts/` yet -> compute `should_split`. 18 GB file vs 10 GB
   limit -> split.
4. `split_video_file` with balanced algorithm
   (see Section 7.3): num_chunks = ceil(18/10) = 2; per-chunk size
   ~9226 MB. Two output chunks created in `<folder>/_parts/`.
5. Hash each chunk; write `<chunk>.sha256` into `<folder>/checksums/`;
   build `entry["split_info"]`.
6. `save_library(library)` — **persisted before any upload**, so an
   interrupted push has a verified manifest to resume against.
7. Loop chunks: `adb push -p <local> /sdcard/Media/.../<chunk>`.
   After each success, delete the local chunk (only if its path
   contains `_parts`).
8. After all chunks done, `os.rmdir(_parts/)` (empty).
9. Set `uploaded=True`, `status="onboarded"`. Save.

Meanwhile, on the phone, Google Photos auto-upload picks up the new
files in `/sdcard/Media/...` (the user has configured the Pixel's
Photos app to back up that folder) and uploads them at original
quality to the cloud. **MediaVault does not orchestrate this step.**

### Stage 3 — REPLACE (`python main.py replace mov-en-2024-inception`)

1. `main.py:1521` -> `cmd_replace(mid)`.
2. Refuse if `uploaded != True`.
3. Write `<original>.temp_dummy` containing the hash and split info.
4. Delete original (retry 3x with `chmod` -> tolerates Plex locks).
5. Rename dummy to original's name.
6. Set `status="archived"`.

The file path still exists on disk; Plex won't lose its library entry.
Hash stays in JSON for the day the user wants to restore.

### Stage 4 — FETCH (`python main.py fetch mov-en-2024-inception`)

1. `main.py:1599` -> `cmd_dispatch_fetch(mid, None)` -> spawns
   `python mainfetch.py fetch mov-en-2024-inception`.
2. `mainfetch.py:cmd_fetch_route` picks `default` profile (since ID
   starts with `mov`).
3. `init_driver("default")` launches Chrome at
   `C:\Media\Utils\ChromeProfile` on port 9222 and attaches Selenium.
4. `resolve_targets` returns `[entry]` (no children).
5. `fetch_single_entry(driver, entry)` builds queue of 2 chunks (since
   `split_info.total_chunks == 2`).
6. **Attempt 1** — for each chunk, `trigger_download(driver,
   chunk_filename, 0)`: open photos.google.com, press `/`, type the
   filename, press Enter, click first result, press Shift+D, press Esc.
7. Harvester loop watches `~/Downloads`. As each downloaded file
   finishes (`.crdownload` disappears), SHA256-hash it and look for a
   match in the pending queue. On match: move to
   `<folder>/restore/<expected_name>`, mark done.
8. If any chunks still pending after Attempt 1, **Attempt 2**: same
   loop but `query = entry["search_term"]` and `index = chunk_index`.
9. Quit driver, return.

### Stage 5 — RESTORE (`python main.py restore mov-en-2024-inception`)

1. `main.py:1530` -> `cmd_restore(mid)`.
2. `restore/` exists; entry has `split_info`. Verify all chunks present.
3. `merge_video_files(chunks, target_path)`: `mkvmerge -o target chunk1 +chunk2`.
4. SHA256 the new merged file -> overwrite `entry["hash"]` (the merged
   container has a different SHA256 than the original).
5. Delete chunks; `rmdir restore/`.
6. Set `status="restored_local"`. Save.

### Combined auto-pilots

- `prep_push_rep` (line 1244) chains 1->2->3 for a single movie with
  rollback of `_parts/` on push failure.
- `prep_push_rep_season` (1286) chains for a whole season,
  episode-by-episode sequentially, stopping the whole batch on any
  failure.
- `fetch_restore` (1367) chains 4->5.

---

## 11. External Integrations Summary

| System | Integration mechanism | File / function |
|---|---|---|
| **Filesystem** (Windows NTFS) | direct `os` / `shutil` / `open` calls | throughout |
| **MediaInfo** | `pymediainfo.MediaInfo.parse(filepath)`, walking `.tracks` | `main.py:get_tech_specs` (139) |
| **mkvmerge** | `subprocess.run([MKVMERGE_PATH, ...])` | `main.py:split_video_file` (256), `main.py:merge_video_files` (275) |
| **ADB / Pixel phone** | `subprocess.run(["adb", "shell", ...])` and `subprocess.run(["adb", "push", "-p", ...])` | `main.py:cmd_push` (576, 675) |
| **Chrome (DevTools)** | `subprocess.Popen([chrome.exe, "--remote-debugging-port=9222", ...])` then Selenium `add_experimental_option("debuggerAddress", "127.0.0.1:9222")` | `mainfetch.py:init_driver` (105) |
| **Google Photos (web UI)** | `driver.get("https://photos.google.com")` + simulated keyboard via `ActionChains` (`/`, query, Enter, Shift+D, Esc) | `mainfetch.py:trigger_download` (150) |
| **Downloads folder watcher** | `os.listdir(SYSTEM_DOWNLOADS_FOLDER)` polling every 5 s | `mainfetch.py:fetch_single_entry` (310-366) |
| **HTTP** (poster/fanart) | `requests.get(url, headers={"User-Agent": ...}, stream=True)` | `main.py:cmd_set_poster` (402), `cmd_set_fanart` (430) |
| **Subprocess to fetcher** | `subprocess.run(["python", MAINFETCH_SCRIPT, "fetch", id, ...])` | `main.py:cmd_dispatch_fetch` (1350) |

---

## 12. Error Handling and Edge Cases

- **Bare `except: pass`** throughout `load_library`, file deletion,
  rmdir cleanup, and several inner cleanup paths. Suppresses
  `JSONDecodeError`, `FileNotFoundError`, `PermissionError` silently.
  Acceptable for the cleanup paths, risky in `load_library` (a corrupt
  library file is silently treated as empty — could clobber data on
  next save).
- **Resume semantics for push**: surviving artifacts of an interrupted
  push (`_parts/` populated; `entry["split_info"]` written but
  `uploaded=False`) are detected on the next `cmd_push` call and the
  chunks are re-uploaded without re-splitting/re-hashing. This is the
  primary fault tolerance mechanism — there is no transaction log.
- **Replace under load**: 3-retry loop with 1 s back-off and explicit
  `os.chmod(stat.S_IWRITE)` for files Plex/Windows Search have open.
  If all 3 retries fail, leaves both `original` and `original.temp_dummy`
  on disk; user is told to close players.
- **Hash mismatch on restore**: blocks the move into place; the bad file
  remains in `restore/` for inspection.
- **Auto-pilot rollback**: `cmd_prep_push_rep` deletes `_parts/` on push
  failure but does NOT delete the library entry. Re-running starts from
  `local_ready`.
- **Season auto-pilot stops on failure**: `cmd_prep_push_rep_season`
  breaks the loop on first push failure "to prevent mess".
- **Hash collision on restore harvester**: the harvester routes by
  SHA256, so if two queued chunks somehow have the same hash, the
  second one to arrive will be deleted as a "duplicate". This is a
  theoretical bug but practically impossible (SHA256 over multi-GB
  chunks).
- **Filename collisions in `~/Downloads`**: a `.crdownload` arriving for
  a name already on disk gets ` (1)`, ` (2)`, ... suffixes by Chrome.
  Since routing is hash-based, this doesn't matter; the harvester picks
  up whatever new file appears.
- **Lost session state**: if Selenium's debug-port attach fails (e.g.,
  9222 already in use by another Chrome), `init_driver` returns None
  and the fetch route exits cleanly.

---

## 13. Testing Approach

**No automated tests exist.** No `tests/`, `pytest`, `unittest`,
`tox.ini`, or CI config of any kind. The project is "tested by use" —
each pipeline run is a manual integration test. The legacy snapshots
(`main_workingprep.py`, `mainfetchWorking.py`, etc.) are the de-facto
regression baselines: when something breaks, the user can `diff` the
active file against the most recent "working" snapshot to triage.

Implicit safety checks that act as runtime tests:
- Hash verification at every stage transition (prep stores; check
  re-verifies; restore verifies before placing; harvester routes by
  hash).
- Per-chunk sidecar `.sha256` files written into `checksums/` for
  out-of-band recovery.
- `verify_restore` is an explicit dry-run command the user invokes
  before committing a real `restore`.

---

## 14. Configuration

All configuration is **hardcoded Python constants at the top of each
file**. There are no env vars, `.env`, `config.json`, `settings.py`, or
CLI flags for paths. To re-target the system, you edit the source.

### Active configuration constants

| Constant | Defined in | Value |
|---|---|---|
| `LIBRARY_MOVIES` | `main.py:19`, `mainfetch.py:29` | `C:\Media\library_movies.json` |
| `LIBRARY_SERIES` | `main.py:20`, `mainfetch.py:30` | `C:\Media\library_series.json` |
| `LIBRARY_ANIME` | `main.py:21`, `mainfetch.py:31` | `C:\Media\library_anime.json` |
| `LOCAL_ROOT` | `main.py:23`, `mainfetch.py:33` | `C:\Media` |
| `REMOTE_ROOT` | `main.py:24` | `/sdcard/Media` |
| `MKVMERGE_PATH` | `main.py:25`, `mainfetch.py:34` | `C:\Program Files\MKVToolNix\mkvmerge.exe` |
| `MAINFETCH_SCRIPT` | `main.py:26` | `mainfetch.py` |
| `SPLIT_DIR_NAME` | both | `_parts` |
| `CHECKSUM_DIR_NAME` | both | `checksums` |
| `RESTORE_DIR_NAME` | both | `restore` |
| `VIDEO_EXTENSIONS` | both | `('.mkv', '.mp4', '.avi', '.mov')` |
| `CHROME_PROFILES["default"]` | `mainfetch.py:38` | `C:\Media\Utils\ChromeProfile` |
| `CHROME_PROFILES["tv"]` | `mainfetch.py:39` | `C:\Media\Utils\ChromeProfile_TV` |
| `CHROME_PROFILE_NAME` | `mainfetch.py:41` | `Default` (sub-profile inside the user-data-dir) |
| `SYSTEM_DOWNLOADS_FOLDER` | `mainfetch.py:42` | `~/Downloads` |
| `CHROME_PATH` | `mainfetch.py:112` | `C:\Program Files\Google\Chrome\Application\chrome.exe` (falls back to `(x86)`) |
| Debug port | `mainfetch.py:120` | `9222` |
| Language priority (sort) | `main.py:1035` | `en=1, ta=2, hi=3, default=99` |
| Hash block size | `main.py:101`, `mainfetch.py:85`, `archive/legacy/index_file.py:22` | 65536 (64 KB) |
| Balanced-split buffer | `main.py:231, 245` | `+10 MB` |
| Fetch base timeout | `mainfetch.py:306` | 300 s (self-extends while .crdownload active) |
| `DUMMY_MAX_BYTES` | `main.py:34` | `200_000` (200 KB) — sniff threshold in `cmd_prep`, `cmd_check`, and `cmd_repair_dummies` |
| `FFMPEG_PATH` | `main.py:33` | `C:\Users\harin\AppData\Roaming\Emby-Server\system\ffmpeg.exe` (falls back to `shutil.which`) |
| `DUMMY_RECIPE_BY_EXT` | `main.py:41` | Per-container ffmpeg recipe for the ~10 KB dummy (audio codec / source / duration); four entries: `.mkv`, `.avi`, `.mp4`, `.mov` |
| Replace retry count | `main.py:789` | 3 |
| ADB push flag | `main.py:675` | `-p` (built-in progress) |

### Operational assumptions

- The user has logged into both Chrome profiles manually at least once
  and the Google Photos sessions are still valid.
- The Pixel phone is connected via USB, debugging is authorised, and
  the Photos app is configured to back up `/sdcard/Media`.
- MKVToolNix is installed at the default location.
- The Downloads folder contains no other large `.mkv`/`.mp4` files that
  could be confused with active fetches (the hash check protects
  against collisions but the file is still hashed once).

---

## 15. Patterns and Conventions

- **Single-file scripts, no packaging**: no `setup.py`, `pyproject.toml`,
  or `__init__.py`. Each script is run directly with the system
  Python or the `.venv` interpreter.
- **Procedural style**: free functions named `cmd_*` for each
  subcommand. No classes (except implicitly inherited Selenium types).
- **Print-driven UX**: emoji-decorated stdout (`✅`, `❌`, `⚠️`, `🔍`,
  `⚖️`, `🎯`, `🚀`, etc.) acts as both progress UI and log. No
  `logging` module.
- **Manual argv parsing**: each entry point walks `sys.argv` with a
  custom while-loop and keyword-style flags
  (`SIZE_MB`/`SIZE_GB`/`COUNT`, `episodes`, `chunks`).
- **Stateful files in `folder_path`**: each media folder collects
  `uid`, `<short_id>.sha256`, optionally `_parts/`, `checksums/`,
  `restore/`, `poster.jpg`, `fanart.jpg`, plus the original (or its
  dummy).
- **Two-stage matching for restore**: precision-by-filename then
  fallback-by-fuzzy-search-term, with positional indexing for
  multi-chunk fallback.
- **Hash-first routing**: when in doubt about a file's identity (in
  Downloads, in restore/), SHA256 it and look it up. Filenames are
  treated as hints, not identity.
- **Buffer constants over precision**: the `+10 MB` split buffer and the
  `time.sleep(3)` Chrome stabilisation are pragmatic fudges. Resist
  the urge to "optimise" them away without testing on a real upload.

---

## 16. Observations, Concerns, and Tech Debt

### Bugs / latent issues

- ~~**Silent JSON corruption**~~: **FIXED** — `load_library` now calls
  `sys.exit(1)` on any read/parse failure instead of swallowing exceptions.
  `save_library` now uses `tempfile.mkstemp` + `os.replace()` for atomic
  writes — a mid-save OS kill can no longer produce a 0-byte corrupt file.
- ~~**`a.json` checked into the repo**~~: **FIXED** — the file was
  moved to `archive/unrelated/a.json` and both that path and bare
  `a.json` are listed in `.gitignore`. The file contains API keys for
  Gemini, OpenRouter, OpenAI, Notion, Telegram bot token, Perplexity —
  it is unrelated to MediaVault. **Rotate those keys if the repo was
  ever pushed to a remote before the gitignore landed.**
- **`undetected-chromedriver` listed but unused**: clean up
  `requirements.txt` or actually start using it (its anti-bot evasion
  could matter if Google starts blocking the default Chromedriver on
  Photos).
- **`requests` not listed in requirements.txt** but imported by
  `main.py` and used by `set_poster`/`set_fanart`. Add it.
- ~~**`pymediainfo` dead import in `mainfetch.py`**~~: **FIXED** —
  `from pymediainfo import MediaInfo` removed from `mainfetch.py`.
- **`build_download_queue` in `mainfetch.py:413` is unreachable** —
  defined but never called. Either wire it up (replacing the inline
  duplicate in `fetch_single_entry`) or delete.
- **`fallback_query` is the same as `specific_query` for non-chunked
  files** (`mainfetch.py:269-279`) — both default to
  `entry["search_term"]` or `entry["filename"]`. The second attempt is
  effectively the same search, just with `fallback_index = 0`. A real
  fallback (e.g., dropping the bracketed UID) would be more useful.
- **`get_tech_specs` swallows all parse exceptions** to a single
  `{"error": "Could not parse file"}`, which then gets stored into
  `entry["tech_spec"]` as a 1-key dict. Downstream code (e.g.,
  `cmd_sort`'s `entry.get('tech_spec', {}).get('size_bytes', 0)`)
  handles this by defaulting to 0, but it's still surprising.
- **No transactional save**: `save_library` opens each file with `'w'`
  and writes. A crash mid-write leaves the JSON truncated and
  unreadable (only partially mitigated by the manual `library - Copy*.json`
  backups in `C:\Media\`). Writing to a temp file then `os.replace` would
  fix this.
- **Hardcoded `CHROME_PROFILE_NAME = "Default"`** assumes the sub-profile
  inside the user-data-dir is always literally called "Default". Fine
  for fresh profiles but breaks if the user creates a second Chrome
  profile inside the same data dir.
- **Race in chunk-rename on remote**: `cmd_push` renames non-chunk files
  with the UID *only* on the remote side. The local file keeps its
  original name. If the file is later re-prepped after restore, the
  UID is regenerated (deterministic from manual_id, so it's the same),
  but the Google Photos search term must still match the *uploaded*
  remote filename. Today this works because `search_term` is computed
  off the local filename + UID and the remote rename uses the same
  formula. Fragile if the formula ever changes.

### Issues found in the 2026-05-25 live-data audit

- **No library integrity / orphan-parent check.** Discovered
  2026-05-25: 8 children `tv-ta-2024-aindhamvedham-s01e01..e08` had
  `parent_id = "tv-ta-2024-aindhamvedham-s01"` but the matching
  `season_map` row was MISSING from `library_series.json`. These were
  the first series the user ever prepped (one episode at a time, with
  an older `main.py` that did not yet auto-create season_maps from a
  detected parent). The season_map was inserted manually on
  2026-05-25 (no other orphans exist in the live data). Today's
  `cmd_prep` would not reproduce this state, but a `verify_library`
  command to detect orphan parent_ids, missing children-listed-in-a-
  season_map, or stale `total_episodes` is still missing.
- **No ID-format validation in `cmd_prep`.** Live data contains
  `mov-en-20013-conjuring` (5-digit year typo for 2013).
  `parse_metadata_from_id` only accepts 4-digit years, so
  `metadata.year` is `None` and `cmd_sort` sinks the entry to the
  bottom with `year=0`. A `--force`-gated strict mode that warns on
  unknown lang codes, non-4-digit years, etc., would have caught it.
- **Direct `cmd_prep` on hybrid `ani-<show>-sNN<EE>` IDs would create
  a junk season_map.** The anime auto-parent regex
  (`^(ani-.*?)[\d\.]+$` at `main.py:336`) strips ALL trailing digits.
  For `ani-ja-2012-kurokosbasketball-s0125` it yields parent
  `ani-ja-2012-kurokosbasketball-s` (just `-s`) instead of the
  intended `ani-...-s01`. Today this never fires in practice because
  Kuroko-style IDs are only ever created via `cmd_prep_season`, which
  passes `parent_id` explicitly and bypasses the regex.
- **`cmd_push_group` is missing the `x`-separator regex** that its
  siblings have. `main.py:731-732` checks only `[eE]\d+$` then a bare
  trailing-digit fallback. By contrast `cmd_restore_group`
  (`main.py:997-998`), `cmd_prep_push_rep_season` (`main.py:1303`),
  and `mainfetch.resolve_targets` (`mainfetch.py:391-392`) also handle
  `x\d+$`. Harmless today (no production ID uses the `x` convention),
  but inconsistent.
- **Anime chunk path is unproven in production.** 0 of 140 anime leaf
  entries have `split_info` — every anime episode has fit under the
  user's typical `SIZE_MB 9900` threshold. The first large anime
  (4K BD anime film, long OAV, etc.) would be the first real
  exercise of `split_video_file` → `mainfetch.fetch_single_entry`'s
  chunk branch under the TV Chrome profile.
- **`mainfetch.load_library` swallows errors silently** with bare
  `except: pass` (`mainfetch.py:52-80`), asymmetric with
  `main.load_library` which now `sys.exit(1)`s on corruption. A
  corrupt library would cause mainfetch to "find" zero entries and
  exit cleanly, masking the failure.
- **`cmd_dispatch_fetch` invokes `"python"` literally**
  (`main.py:1345`) instead of `sys.executable`. On a machine where
  `PATH` resolves `python` to a different interpreter than the one
  running `main.py`, the spawned `mainfetch.py` will fail to import
  `selenium`.

### Code smells

- Heavy code duplication between `main.py` and `mainfetch.py`
  (`load_library`, `calculate_file_hash`, library path constants). A
  shared `mvcommon.py` module would DRY this without changing
  behaviour and would have prevented the asymmetric error-handling
  between the two `load_library` copies.
- Manual argv parsing across both entry points reimplements features
  `argparse` gives for free (help text, type validation, default
  values).
- Inline emoji `print` statements make it hard to capture clean logs
  for debugging long-running batch operations.
- `build_download_queue` in `mainfetch.py:413` is unreachable scaffolding.
- `wait_for_download` and `automation_download_file` in
  `mainfetch.py:219, 224` are no-op stubs kept "for compatibility".

### Security / privacy concerns

- The `archive/unrelated/a.json` file contains API keys / bot tokens
  for an unrelated tool. It is gitignored, but **rotate those keys if
  the repo was ever pushed to a remote before the gitignore landed.**
- Selenium attaches to a debug port without any auth on
  `127.0.0.1:9222` — anything local on the machine can issue commands
  to the browser session while it's running. Acceptable for a
  single-user PC; risky on a shared machine.

### Scalability

- `load_library` reads all 3 JSONs into memory on every command. With
  the current ~25k lines total that's milliseconds; would scale poorly
  past tens of thousands of entries.
- `cmd_scan_unprepped` walks every media folder on every run. Fine
  today; could be slow on very large collections.
- The fetcher serialises across entries (one entry's chunks in
  parallel, but entries themselves processed sequentially via
  `for entry in targets`). For a 20-episode season this is intentional
  (Google Photos rate-limits parallel sessions), but it's a hard
  ceiling on throughput.

---

## 17. Future Work / Natural Extension Points

These are not requested changes, just well-formed seams where future
work would slot in cleanly:

1. **Extract a shared `mvcommon.py`** with `load_library`,
   `save_library`, `calculate_file_hash`, `generate_short_id`, and the
   library path constants. Both `main.py` and `mainfetch.py` import it.
   Eliminates the duplicated `load_library` and the divergence risk.
2. **Atomic JSON saves**: write to `<file>.tmp` and `os.replace`. Also
   write a rotating backup `<file>.bak` before save.
3. **Real CLI**: migrate to `argparse` with subcommands. Self-documenting
   help, less manual indexing.
4. **Config file**: lift the hardcoded paths into a `mvconfig.json` or
   `.env` so the tool is portable to another machine without code
   edits.
5. **Plugin a 4th category**: today the only ID prefixes recognised by
   `save_library` are `mov`/`tv`/`ani` (else -> movies). Adding e.g.
   `doc-*` for documentaries would require touching `save_library`,
   `cmd_scan_unprepped`'s `categories` list, and `cmd_fetch_route`'s
   profile selection.
6. **Multi-device push**: today `REMOTE_ROOT` is a single string. A
   small refactor of `cmd_push` to take a device serial (and call
   `adb -s <serial> push ...`) would unlock pushing to multiple
   phones for redundancy.
7. **Better fallback search**: today's "fallback_query" is essentially
   the same query as the precision search. A real fallback would strip
   the UID/extension and try the bare title.
8. **Progress tracking for long batches**: write a `progress.json`
   per-batch so a Ctrl-C in `cmd_prep_push_rep_season` can resume from
   the last completed episode rather than re-evaluating every child.
9. **Encrypt-at-rest before upload**: today the `.mkv` lands in Google
   Photos in the clear. A simple AES-CTR pass during chunking (with the
   key stored in a sidecar) would address the privacy concern.
10. **Replace Selenium key-shortcut hack with the Photos API**:
    Google Photos has an official API for downloads. Using it would
    remove the Chrome dependency entirely and let the fetcher run
    headless on a server. Trade-off: Photos API doesn't return raw
    "original quality" video without a separate Picker workflow as of
    the latest documentation.

---

## 18. Quick Reference: where to find things

| If you want to change... | Look at... |
|---|---|
| What constitutes a "split" / chunk size | `main.py:split_video_file` (196-269) |
| How chunks are named | `main.py:204` (output_pattern) |
| How upload progress is shown | `main.py:675` (`adb push -p`) |
| How dummies are formatted | `main.py:778-780` |
| How the merged-file hash is updated | `main.py:923-929` |
| Which Chrome profile is chosen | `mainfetch.py:cmd_fetch_route` (459-464) |
| How Photos search is performed | `mainfetch.py:trigger_download` (150-216) |
| How downloaded files are routed | `mainfetch.py:fetch_single_entry` harvester loop (310-366) |
| What ID prefix goes to which library file | `main.py:save_library` (75-84), `tools/migrate_lib.py:38-45` |
| Auto-parent / season-map creation | `main.py:cmd_prep` (333-364) |
| Episode-number auto-detection from filenames | `main.py:cmd_prep_season` (489-507) |
| Episode-range filter (group ops) | `main.py:cmd_push_group` (731-732), `cmd_restore_group` (997-998), `cmd_prep_push_rep_season` (1303); `mainfetch.py:resolve_targets` (391-392). Note: `cmd_push_group` lacks the `x\d+$` pattern — see §16. |
| Chrome profile per category | `mainfetch.py:cmd_fetch_route` (457-462). Movies → `default`; series & anime → `tv`. |
| Half-episode (`.5`) regex support | `main.py:336, 491, 738, 1004, 1310`; `mainfetch.py:393` |
| Adding a new subcommand | Append to the `if/elif` chain in `main.py:1397-1622` |

---

*Last updated 2026-05-25 — reflects `main.py` (1621 lines, atomic
save_library, balanced-split, anime auto-parent, half-episode support,
dual Chrome profiles, parallel trigger-and-harvester restore) and the
live-data audit of `library_movies/series/anime.json` (102 / 290+28 /
140+5 entries). Repo layout now uses `archive/` (legacy snapshots),
`tools/` (migrate_lib.py), and gitignored `resources/` (offline
library copies).*
