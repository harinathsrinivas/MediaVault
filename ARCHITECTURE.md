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
|-- main.py                          ACTIVE — local pipeline + ADB push + restore (3081 lines as of 2026-06-12)
|-- mainfetch.py                     ACTIVE — Selenium fetch from Google Photos (491 lines)
|-- mvcommon.py                      ACTIVE — shared library I/O + hashing constants/helpers imported by both entry points (168 lines)
|-- requirements.txt                 ACTIVE — pymediainfo / undetected-chromedriver / selenium (requests + webdriver-manager still missing — §16)
|-- requirements-dev.txt             ACTIVE — pytest
|-- ARCHITECTURE.md                  this document
|-- README.md                        user-facing overview
|-- improvements/                    the backlog + direction "brain" (start at improvements/README.md):
|   |-- improvement_details.md         IMP-XN operating manual
|   |-- improvements_tier{A..H,R,S,U,X}.md  tracked improvement tasks
|   |-- PRIORITY.md                    always-current task ordering ("what to do next"); visual twin docs/priority-graph/priority-graph.html
|   |-- ROADMAP_END_GOAL.md            the phased couch-vault roadmap
|   |-- RESEARCH_*.md, JELLYFIN_SETUP_GUIDE.md, BLOCKERS_AND_MOONSHOTS.md  durable research/direction
|-- apple_tv_ui_roadmap.md           2026-05 Jellyfin-plugin UI design (partially superseded — see improvements/ROADMAP_END_GOAL.md)
|-- .gitignore                       excludes a.json, PLAN.md, resources/, Obsidian vault, transcript dumps
|
|-- tools/
|   |-- migrate_lib.py               AUX (one-shot) — splits legacy library.json into the 3 category files
|   `-- migrate_rehash_flag.py       AUX (one-shot, PR #20) — stamps re_hashed=false onto pre-existing split entries
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
|-- docs/                            per-feature design/plan/decision artifacts (auto-rollback, split-hash,
|                                    multi-episode, video-dummy, adb-device-select, fable-review, ...)
|                                    plus git-pr-conventions.md and testing-strategy.md; docs/README.md is the master index
|-- assets/                          placeholder (.gitkeep only)
|-- tests/                           pytest suite — 15 files (rollback, push partial/retry/verify/mock-device,
|                                    replace, restore-quarantine, rehash, prep_season parsing, recover CLI,
|                                    trigger retry, mvcommon, baseline happy path, alias consumers, entry-schema
|                                    guard) + conftest fixtures; see §13
|   `-- smoke/                        fast (~8-10s) full-command smoke suite — drives every command + major
|                                    options against tiny fixtures/stubs incl. a multi_ep_alias library sweep;
|                                    the mandated pre-PR cross-command gate (`pytest tests/smoke -q`)
|-- .candidates/                     multi-candidate pipeline artifacts (judge DECISION.md files committed per step)
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
    |-- ChromeProfile_TV\            Selenium-attached Chrome user data dir for TV (series account)
    |-- ChromeProfile_Anime\         Selenium-attached Chrome user data dir for anime
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
| `prep_push_rep` | `prep_push_rep [id] [filepath] [SIZE_MB/SIZE_GB/COUNT val] [device <id_or_name>] [rehash] [tempdir <path>]` | `cmd_prep_push_rep` — full pipeline on one movie |
| `prep_push_rep_season` | `prep_push_rep_season [id] [folder] [SIZE_MB/SIZE_GB/COUNT val] [episodes <range>] [device <id_or_name>] [rehash] [tempdir <path>]` | `cmd_prep_push_rep_season` — sequential pipeline for a season |
| `fetch_restore` | `fetch_restore [id] [OPT: episodes <range>]` | `cmd_fetch_restore` — dispatch fetch then restore |
| `set_search` | `set_search [id] [term]` | `cmd_set_search` |
| `set_poster` | `set_poster [id] [url]` | `cmd_set_poster` |
| `set_fanart` | `set_fanart [id] [url]` | `cmd_set_fanart` |
| `set_uploaded` | `set_uploaded [id]` | `cmd_set_uploaded` — force `onboarded` (multi-part rescue) |
| `scan_unprepped` | `scan_unprepped` | `cmd_scan_unprepped` — find video files on disk not in any library |
| `check` | `check [id]` | `cmd_check` — re-hash file and compare to library entry |
| `local_status` | `local_status [limit_size]` | `cmd_local_status` — show pending uploads + greedy bin-packing into `limit_size` |
| `push` | `push [id] [SIZE_MB/SIZE_GB/COUNT val] [chunks 1-4] [device <id_or_name>] [rehash] [tempdir <path>]` | `cmd_push` (`rehash` = eager canonical re-hash; `tempdir` redirects `_parts/` off-volume) |
| `push_group` | `push_group [id] [SIZE_..] [episodes 1-3] [device <id_or_name>] [rehash] [tempdir <path>]` | `cmd_push_group` |
| `replace` | `replace [id]` | `cmd_replace` — swap original with a tiny valid video placeholder generated by ffmpeg (`make_video_dummy`); an unknown id now prints `❌ Error: '<id>' not found in library.` before returning `False` (IMP-C14) |
| `replace_group` | `replace_group [id]` | `cmd_replace_group` |
| `repair_dummies` | `repair_dummies [optional: id_prefix]` | `cmd_repair_dummies` — walk all `status=="archived"` entries and upgrade legacy text-blob dummies to valid video dummies |
| `verify_restore` | `verify_restore [id]` | `cmd_verify_restore` — dry-run hash check of files in `restore/` |
| `restore` | `restore [id]` | `cmd_restore` — re-merge chunks + verify + move into place |
| `restore_group` | `restore_group [id]` | `cmd_restore_group` |
| `sort` | `sort` | `cmd_sort` — re-order JSONs by lang -> year -> size |
| `web` | `web [--port N] [--host H] [--no-browser]` | `cmd_web` — launch the local FastAPI operations console (Disk Reclaim view) at `http://127.0.0.1:8765`; lazy-imports fastapi/uvicorn so importing `main` never hard-requires them (IMP-E12) |
| `fetch` | `fetch [id] [OPT: episodes <range>]` | `cmd_dispatch_fetch` — spawns `python mainfetch.py fetch ...` |
| `recover` | `recover [id\|folder]` / `recover --scan` | `cmd_recover` — finish an interrupted rollback (calls `recover_journal`); `--scan` lists leftover journals read-only |

> **`episodes` keyword is a required literal trigger.** For `fetch`,
> `fetch_restore`, and `prep_push_rep_season`, the word `episodes` must
> appear as its own argument immediately before the range value.  
> ✅ `fetch tv-TheBoys episodes 1-3` → `epr = "1-3"`  
> ❌ `fetch tv-TheBoys 1-3` → `epr` stays `None`, range silently ignored  
> Parsing: `fetch`/`fetch_restore` use a fixed positional check
> (`sys.argv[3] == "episodes"`, `main.py:1606,1619`); `prep_push_rep_season`
> uses a token-scanner loop (`main.py:1464-1479`) so the keyword can appear
> anywhere after the folder path.

> **The `episodes <range>` filter is season-aware (IMP-C18).** All five
> range-filter sites — `mainfetch.resolve_targets`, `cmd_push_group`,
> `cmd_restore_group`, `cmd_prep_push_rep_season`, and `_season_resume_cmd`
> — read each child's episode number through the single shared helper
> `mvcommon.episode_num_from_id(child_id, base_id)`. It strips `base_id`
> as a prefix first, THEN matches an anchored `^[eExX]?(\d+(?:\.\d+)?)$`,
> so for a glued anime season id like
> `ani-ja-2013-kurokosbasketball-s0202` (base `…-s02`) the leftover `02`
> reads as episode 2 and `episodes 2-3` correctly selects episodes 2-3.
> Previously the first three sites used an unanchored fallback
> (`re.search(r'(\d+(?:\.\d+)?)$', child_id)`) that read `0202` as
> episode **202**, so `episodes 2-3` matched nothing while the auto-pilot
> still printed a green success banner. The glued-number workaround
> `episodes 202-203` RELIED on that bug and no longer works.

> **0-match guard (IMP-C18).** When an `episodes <range>` selects 0 items
> from a NON-EMPTY season, the tools print a `⚠️` warning naming the
> parsed range and a sample child id, and the `cmd_fetch_restore`
> auto-pilot SUPPRESSES the green `✅✅✅ FETCH & RESTORE COMPLETE.`
> banner — printing a `⚠️ … 0 items` summary instead. The run continues:
> no error, no non-zero exit (an empty range is informative, not a
> failure). `cmd_restore_group` now returns its int restored-count so the
> auto-pilot can make this call (`main.py:2910`).

> **CLI parsing seams (IMP-C14).** `push_group`'s argv parsing is now an
> extracted pure function `main.parse_push_group_args(args)` →
> `(group_id, method, val, ep_range, dev, eager, tdir)`. It mirrors the
> sibling `push` parser's fail-fast arms: a value-taking keyword
> (`SIZE_MB`/`SIZE_GB`/`COUNT`, `episodes`, `device`) with no following
> value now prints `❌ Error: Missing value for ...` and `sys.exit(1)`
> instead of spinning `while i < len(args)` forever (the prior trailing-
> value-keyword console hang). Unknown/typo'd tokens are still silently
> skipped, matching `push`. `mainfetch`'s bare-invoke parsing is likewise
> extracted into `mainfetch.parse_fetch_args(argv)` → `(mid, epr)`
> (Selenium-free, so it is unit-testable); see the subsection below.

### `python mainfetch.py fetch <id> [episodes <range>]`

Single entry: `cmd_fetch_route(manual_id, ep_range)` at `mainfetch.py:455`.
Profile selection is now data-driven via `profile_for_id(manual_id)` (IMP-C16).

Note the argv parsing (extracted as `mainfetch.parse_fetch_args(argv)`)
requires `fetch` as `argv[1]` and the ID at `argv[2]`; episodes go at
positions 3-4. The guard is now `len(argv) < 3 or argv[1] != "fetch"` →
prints `Usage: fetch [id] [episodes] [range]` and `sys.exit(1)`, so a bare
`python mainfetch.py fetch` (no id) prints usage and exits cleanly instead
of raising `IndexError` on `argv[2]` as it did previously.
`main.py:cmd_dispatch_fetch` (line 1350) constructs exactly that argv shape.

### `python main.py web` — the local operations console (IMP-E12)

`cmd_web(host="127.0.0.1", port=8765, open_browser=True)` lazily imports
`uvicorn` + `webui.server.create_app` *inside the function* (so importing
`main` — and the whole test suite — never hard-requires fastapi/uvicorn; a
missing dep degrades to a clear `pip install -r requirements.txt` message).
It binds **localhost only**.

- **`webui/` package** (the seed of the Tier-S daemon, IMP-S2): `server.py`'s
  `create_app()` returns a FastAPI app with a **serialized single-worker job
  queue** — one daemon thread drains a `queue.Queue` and runs each action
  in-process one at a time, so the two mutating actions (`push`/`replace`)
  can never collide on the single ADB device and per-job stdout capture is
  race-free by construction. The worker catches `SystemExit` first (a corrupt
  library makes `load_library` call `sys.exit(1)`) so it can never wedge.
  Routes: `GET /api/reclaim` (the reclaim scan), `GET /api/library` (status
  counts by category), `POST /api/action/{name}` (allow-list
  `{prep,push,replace,sort,prep_push_rep,fetch_restore}`; `replace` requires
  `confirm:true` or returns **409**; returns **202** + a `job_id`),
  `GET /api/job/{id}` (polling), `GET /api/items` (see below),
  `GET /api/mode` (returns `{"demo":true|false}`). `webui/static/` is a
  no-build card-grid SPA mounted via `StaticFiles`.
  - **Worker incremental progress (IMP-E14 Phase 2):** the job worker publishes
    a running job's partial `output` (stdout captured via a stdout-tee on the
    running function) and a parsed `progress {done, total}` (chunk units) inside
    the job record. Clients learn these by **polling `GET /api/job/{id}`** — NOT
    via SSE or WebSocket (the future streaming upgrade is tracked as IMP-F10;
    this polling mechanism is the down-payment on IMP-S2's serialized worker
    performing `fetch_restore` with live progress). The job schema gained two
    new fields: `progress` (object `{done, total}`) and `progress_unit`
    (string, e.g. `"chunks"`).
  - **`cmd_dispatch_fetch` subprocess streaming:** `cmd_dispatch_fetch` now
    spawns `python mainfetch.py fetch …` via `subprocess.Popen` (line-by-line
    stdout stream) with `PYTHONIOENCODING=utf-8` in the child environment, so
    the worker captures real download progress lines as they arrive. The prior
    `subprocess.run` call collected output only after the subprocess returned,
    bypassing capture.
  - **Demo / safe mode (`--demo`):** `python main.py web --demo` sets a
    process-level flag that makes EVERY web action simulated — no real `cmd_*`
    calls, no library mutations, no Selenium. The flag is exposed via
    `GET /api/mode` so the SPA can show a sticky "DEMO MODE" banner. Default
    `python main.py web` is unchanged and fires real actions.
- **Pure read-only data layer in `main.py`** (near `cmd_scan_unprepped`):
  `collect_reclaimable`, `classify_entry_state`, `guess_manual_id`,
  `suggest_target_folder`, `suggest_next_command`. They only READ existing
  keys and the on-disk size; they never mutate the library or touch media.
  `collect_reclaimable` is a whole-library iterator, so it is
  **alias/`season_map`-safe** (skips `season_map`/`multi_ep_alias` before
  dereferencing `folder_path`/`filename`) — the IMP-C12/PR#21 crash class,
  guarded by the `TestAliasSweep` entry in `tests/smoke`.
- **Four reclaim badges** (`classify_entry_state`): `UNPREPPED` (on disk, not
  in library), `LOCAL_NOT_PUSHED` (`local_ready`, not uploaded),
  `PUSHED_NOT_ARCHIVED` (`onboarded`, uploaded, original still on disk),
  `RESTORED_REPLACE_AGAIN` (`restored_local`, uploaded). Reclaimability is
  decided by **actual on-disk size** (real ⇔ `size >= DUMMY_MAX_BYTES`), not
  status alone, so an already-dummied entry never shows phantom GB; `archived`
  + dummy is excluded.
- **`GET /api/items` — media-type inventory endpoint (IMP-E14 Phase 1):** returns
  `main.items_payload()` = `{"items":[...], "by_category":{"movies":...,"series":...,"anime":...,"other":...}}`.
  Iterates every PHYSICAL leaf (skips `season_map`/`multi_ep_alias`); each item
  carries `id, category, state, size_bytes, path, title, year, tmdb_id,
  poster_available, chunk_count` (plus `parent_id` for episodic leaves). Unlike
  `/api/reclaim`, which excludes `archived` entries, **`/api/items` includes ALL
  states including `archived`** — it is the complete library inventory for the
  media-type UI. `items_payload` and `collect_reclaimable` share a `_classify_item`
  helper so the two endpoints can't drift on state semantics; `/api/reclaim` output
  is unchanged.
- **Media-type SPA (IMP-E14 Phases 1+2):** the front end gained a **media-type tab
  rail** (Movies / TV series / Anime / Others) — category derived from the entry's
  id prefix via `_category_of`. Within each tab a **sub-view rail** offers the
  disk-state groups: Unprepped / Local·not-pushed / Pushed·not-archived /
  Fetched·not-archived / Archived. Data source: `/api/items` (library entries by
  state) unioned with `/api/reclaim` (the only source of UNPREPPED rows, which have
  no library entry). No build step — vanilla ES modules, same no-build constraint
  as the existing SPA. Phase 2 adds: a **Fetch & Restore button** on Archived cards
  (fires `fetch_restore` via `POST /api/action/fetch_restore`); an SVG
  `stroke-dashoffset` **chunk-% progress border** that grows as chunks complete and
  snaps to a glowing loop on done; **auto-flip** of the card from Archived →
  Fetched·not-archived via an `/api/items` refresh on job completion; a
  **default size-descending sort** with a **Size / Title / Year sort bar**;
  **readable titles** (humanized id now; real `metadata.title` once Phase-5 TMDB
  lands) with the raw id at the card foot; an **expandable full-screen terminal**
  (⤢) showing the equivalent CLI command + live progress + full captured output;
  and a **cursor-following card glow** (mouse/trackpad; disabled on touch +
  `prefers-reduced-motion`). Posters and mobile/Tailscale/auth are tracked in
  later phases (E14 Phases 4–5 and IMP-E3/U3).
- **CSS motion layer — no build step (IMP-E14 Phase 3):** the SPA gained a
  **continuous hover border**: a rotating conic-gradient accent arc (`.card::after`,
  `@property --ring-angle`) travels around each card on hover, complementing the
  existing cursor-follow glow and yielding to the SVG fetch-progress ring.
  An `@supports`-gated `mask` clip gives a clean arc on supporting browsers; iOS
  Safari (which mis-renders mask+conic) falls back to a box-shadow ring instead.
  A `prefers-reduced-motion` static fallback and touch-device guard are applied
  throughout — all pure CSS, no JS, no build pipeline change.
- **PWA / installable console (IMP-E14 Phase 3):** `webui/static/manifest.webmanifest`
  (`display:standalone`, `theme_color:#0b0f17`) + self-generated branded PNG icons
  (192×192 / 512×512 / apple-touch-icon 180×180) + iOS meta tags
  (`apple-mobile-web-app-capable`, `apple-mobile-web-app-status-bar-style`,
  `theme-color`, `viewport-fit=cover`). The console can be "Added to Home Screen"
  on iPhone/iPad and runs as a standalone app without the browser chrome.
- **Grouped (hierarchical) folder view + Grouped/Decluttered toggle (IMP-E14
  Phase 3+):** within each media-type tab the SPA can render the **on-disk folder
  hierarchy** — show → season → episode for series/anime, collection → movie for
  movies. The state rail has an **"All" segment** (default, shows every item
  regardless of lifecycle state) plus the 5 per-state filters. In grouped mode,
  selecting a state **prunes** the folder tree so a folder only appears when at
  least one descendant leaf matches that state; the folder's shown size is the
  aggregate of visible matching leaves ("All" uses the real Windows folder size).
  Recursive sort (size/title/year) applies at every tree level. The toggle switches
  between the flat Decluttered card-grid and the hierarchical Grouped tree view.
- **New backend endpoints (IMP-E14 Phase 3+):**
  - `GET /api/tree` — returns the per-category folder hierarchy spanning **all
    lifecycle states** including un-prepped on-disk files; folder sizes are real
    Windows sizes obtained via `os.scandir`; each folder node carries `has_image`.
    Read-only; alias-safe (skips `season_map` / `multi_ep_alias` before dereferencing
    `folder_path`).
  - `GET /api/folder-image?path=<folder>` — serves a folder's `poster.jpg` or
    `fanart.jpg`, or the first match found in any descendant. The requested path is
    **realpath-contained to `C:\Media`**; only `poster.jpg` / `fanart.jpg` are
    served (no arbitrary file access).
  - `POST /api/open-folder` — opens the specified folder in Windows Explorer via
    `subprocess`. **Localhost-only** (non-loopback callers receive **403**; path is
    realpath-contained to `C:\Media`). Simulated in demo mode without spawning
    Explorer.
- **Open-in-Explorer button:** every folder card and item card in the SPA has a
  button that fires `POST /api/open-folder`. The button is visible but shows a
  tooltip (and the request returns 403) when accessed over Tailscale or any
  non-localhost connection.
- **Procedural animated space/galaxy background (`background.js`):** a self-contained
  Canvas-based starfield + nebula rendered procedurally at startup. Perf-capped
  (targets 30 fps, halts when the tab is hidden), respects `prefers-reduced-motion`
  (static starfield only), and requires no external assets.
- **Static no-cache policy + global JS error banner:** a `_NoCacheStaticFiles`
  subclass of `StaticFiles` sets `Cache-Control: no-cache` on every static response
  (ETag kept for revalidation) — this fixes the **iOS Safari stale-ES-module blank-
  page bug** where Safari served cached modules after a server restart. A
  `/favicon.ico` handler is also registered. A global `window.onerror` +
  `unhandledrejection` listener surfaces any uncaught JS error as a visible banner
  so the SPA can never silently blank.
- **The web tier calls the existing `cmd_*` UNCHANGED** — no copy of their
  logic. `replace` reuses `cmd_replace` verbatim, so the **auto-rollback
  change-gate is NOT tripped** (journal/PONR/`RollbackHardFail` contract
  untouched) and **`ENTRY_TYPE_KEYS` is unchanged** (no new entry type, no
  shared-field change). The console suggests folders/commands but never moves
  or renames files (move = IMP-D8).

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
  would collide. **Range filtering on this shape is now season-aware
  (IMP-C18):** `mvcommon.episode_num_from_id` strips the `…-s02` base
  before reading the episode number, so `episodes 2-3` on
  `ani-ja-2013-kurokosbasketball-s02` selects the glued
  `…-s0202`/`…-s0203` children as episodes 2-3 (the old unanchored
  fallback read `0202` as 202 and silently matched nothing). **This shape
  is only safe via `cmd_prep_season`**,
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

There are **three entry types** per library: leaf entries, season-map
parents, and multi-episode aliases.

#### Leaf entry (one per file)

```jsonc
"mov-en-2025-f1": {
  "short_id":      "68b7b8",                  // 6-char md5 of manual ID
  "filename":      "F1.The.Movie....mkv",
  "folder_path":   "C:\\Media\\Movies\\English\\Racing\\F1...",
  "status":        "archived",                // see state machine below
  "uploaded":      true,                      // independent boolean
  "search_term":   "F1.The.Movie.... [68b7b8].mkv",  // Google Photos search query
  "hash":          "e2a0221b...d92d",         // SHA256 of ORIGINAL file; for split entries becomes the deterministic CANONICAL merged hash once blessed (see re_hashed, §6.4a)
  "re_hashed":     false,                      // OPTIONAL — split entries only; true once the canonical merged hash is blessed
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
    ],
    "merge_seed":   "f6b674",                  // OPTIONAL — = short_id; the --deterministic seed (set at bless/eager-push)
    "merge_tool":   "mkvmerge v97.0",          // OPTIONAL — tool captured at bless (version-drift triage)
    "rehashed_at":  "2026-06-07T14:03:22Z",    // OPTIONAL — when re_hashed flipped true
    "canonical_hash": "a0b239a1..."            // OPTIONAL/TRANSIENT — eager only: blessed at push, promoted into "hash" at replace then dropped
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

#### Multi-episode alias (one per secondary episode of a combined file)

```jsonc
"tv-en-2009-bsg-s04e20": {
  "type":      "multi_ep_alias",
  "alias_of":  "tv-en-2009-bsg-s04e19",
  "parent_id": "tv-en-2009-bsg-s04"
}
```

A single physical file covering multiple episodes (e.g. `S04E19E20.mkv`) is
registered once as a normal leaf entry under the **first** (lowest) episode
number. Each additional episode number gets a thin alias entry carrying only
`type`, `alias_of`, and `parent_id` — no `hash`, `filename`, `tech_spec`, or
`split_info`. Both the primary and all aliases are listed in the season_map's
`children`.

`_resolve_alias(lib, mid)` in `main.py` (and a local mirror in `mainfetch.py`)
resolves an alias to its primary in one hop. All group push/replace/restore loops
and `mainfetch.resolve_targets` call this helper to collapse aliases to their
primaries before processing, so the underlying file is pushed/replaced/fetched
exactly once regardless of how many episode numbers share it.

Season maps have no `hash`, no `filename`, no `tech_spec`. They are
recognised throughout the code by `entry.get("type") == "season_map"` and
deliberately skipped in `scan_unprepped`, `local_status`, `sort`, and
`collect_reclaimable` (the `web` console's reclaim scan — IMP-E12/D16).

> **`ENTRY_TYPE_KEYS` is the authoritative source of truth for entry-type
> key shapes.** `main.py` (top-level config block, ~`main.py:114`) defines a
> registry mapping each type — `leaf` / `season_map` / `multi_ep_alias` — to its
> minimal `required` key set and a `physical` flag (`True` only for `leaf`, the
> one type that owns a file via `folder_path` + `filename`). Only `leaf` is
> physical; `season_map` and `multi_ep_alias` are non-physical. **Any whole-library
> iterator (`.values()`/`.items()` over the merged dict) MUST skip non-physical
> types or de-alias them first** — i.e. `if entry.get("type") in ("season_map",
> "multi_ep_alias"): continue`, or resolve via `_resolve_alias` — before touching
> `folder_path`/`filename`/`hash`. Dereferencing those keys on a non-physical entry
> is the PR #21 / IMP-C12 crash class (`KeyError: 'folder_path'`). The registry is
> documentation/guard-only (not wired into any `cmd_*` path); it is enforced by
> `tests/test_entry_schema_guard.py` and the `tests/smoke/` alias sweep (§13).

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
- `cmd_restore` now treats a **deterministic** merged hash as the
  canonical whole-file hash for split entries (`main.py:cmd_restore`,
  ~`2042-2101`). A spike proved that mkvmerge's *default* merge is
  non-deterministic (two merges of the same chunks gave `8595b46b…` vs
  `5f007b6e…` from a random segment UID + mux timestamp), but
  `mkvmerge --deterministic <seed>` yields a byte-identical merge
  (`a0b239a1…` twice). The merge therefore passes `seed = short_id`, and
  the old blind `entry["hash"] = new_hash` overwrite is replaced by
  **verify-or-bless** (`bless_or_verify_merged_hash`, `main.py:286`): the
  first restore of a not-yet-`re_hashed` entry BLESSES `entry["hash"]` to
  the merged hash and records `re_hashed`/`merge_seed`/`merge_tool`/
  `rehashed_at`; a later restore VERIFIES the re-merge against the stored
  canonical and, on mismatch, raises a corruption/tool-drift alarm and
  RETURNS BEFORE the restore PONR (chunks kept) instead of overwriting.
  Chunk hashes in `split_info` are NOT touched. See §6.4a, §7.7, §10
  Stage 5 for the full mechanism and schema.
- `cmd_prep` short-circuits if the entry exists and is already
  `uploaded`/`archived`, OR if the file on disk is < 1 KB (dummy detected)
  — `main.py:303-312`.
- **Upload-state integrity guard (IMP-D4, 2026-06-23):** `cmd_prep`'s guard
  was widened to refuse re-prepping ANY entry that is cloud-bearing: it now
  checks `uploaded` truthy OR status in `{onboarded, archived, restored_local}`.
  This closes the re-prep clobber path that stranded entries (the 107-entry +
  battlestar danglers were produced by re-running prep after upload). Separately,
  `cmd_verify_library` now flags `possibly_dangling` leaves — entries whose
  status is `local_ready` or whose `uploaded` flag is false, yet whose folder
  contains on-disk evidence of cloud chunks (e.g. a `checksums/` dir or a
  `_parts/` remnant). See `improvements/improvements_tierD.md` IMP-D4 and
  `docs/feature-legacy-reconcile/REPORT.md` for the full integrity audit story.

### 6.4a Split-file canonical hash (deterministic re-merge)

Split entries gain a verifiable whole-file hash via `mkvmerge --deterministic
<seed>` (seed = the entry's `short_id`). New schema fields:

- `re_hashed` (bool, entry top level) — has the canonical merged hash been
  blessed for this split entry. Migration stamps `false`; flips `true` at bless.
- `merge_seed` (string, under `split_info`) — the `--deterministic` seed,
  `= short_id`, reused verbatim on every future merge.
- `merge_tool` (string, under `split_info`) — e.g. `"mkvmerge v97.0"`, captured
  at bless so a future MKVToolNix upgrade degrades to a graceful re-bless rather
  than a false corruption alarm.
- `rehashed_at` (ISO-8601 UTC string, under `split_info`) — when `re_hashed`
  flipped `true`.

Two modes:

- **DEFERRED (default):** unchanged disk profile at push. The canonical is
  blessed at the FIRST `cmd_restore` (verify-or-bless, `main.py:286` /
  `~2073-2101`). A mismatch on an already-`re_hashed` entry alarms and returns
  BEFORE the restore PONR (chunks kept). Inherited end-to-end by `fetch_restore`
  / `restore_group` (they call `cmd_restore`).
- **EAGER (opt-in `rehash` token):** `cmd_push` merges the fresh chunks once,
  blesses the canonical into the transient `split_info.canonical_hash` (master
  still on disk; `main.py:~1365-1380`), and PROMOTES it into `entry["hash"]` at
  `cmd_replace` (`main.py:~1784-1790`) so `cmd_check` stays correct in the
  pre-replace window. An eager-merge failure falls back to deferred (never aborts
  the push).

**Re-split reset:** a re-push that performs a NEW split clears the stale
canonical — `re_hashed=False` and the old `merge_seed`/`merge_tool`/
`rehashed_at`/`canonical_hash` are dropped (`main.py:~1352`) — so the next
restore re-blesses against the new chunks instead of false-alarming. A RESUME of
an existing `_parts/` does NOT reset (same chunks → canonical still valid).

**Hard disk pre-flight (`main.py:_free_space_ok` / `_required_extra_bytes` /
`_disk_buffer`):** a push/season/eager run that would exceed the target volume's
free space STOPS before the split with a remedy message — deferred needs 1X,
eager 2X, plus a `max(1% of need, 2 GB)` buffer. Season/group sizes to the
LARGEST single splitting item (sequential per-item `_parts/` cleanup ⇒ peak =
largest, not sum). The optional `tempdir <path>` token (`_parts_base`,
`main.py:370`) redirects the chunks + the eager merge temp to
`temp_dir/<safe-id>/` on another volume; `checksums/` and the `RollbackJournal`
always stay in `local_folder`. Resume requires re-passing the same `tempdir`.

**Rollback contract is UNCHANGED.** The `cmd_restore` verify-or-bless change and
the `tempdir` `_parts/` relocation do NOT alter the rollback PONR locations, the
journal format/durability (fsync + `os.replace`), or created-this-run scoping
(the only pre-authorized rollback-adjacent changes are the blind-overwrite
reversal and the `_parts` path value) — see §12a and
`docs/feature-auto-rollback/ROLLBACK_MECHANISM.md` §10.

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

**Remote `.mvmeta.json` sidecar (lives on the phone, not on disk).** Unlike
the `uid`/`.sha256` sidecars above, this one is pushed to the Pixel. On a
fully successful `cmd_push` (all chunks uploaded, no `chunk_range` filter),
`write_remote_mvmeta` writes `<base> [<short_id>].mvmeta.json` into the same
remote dir as the chunks. It mirrors the entry's `split_info` plus key
metadata (schema `version`, `manual_id`, `short_id`, `original_hash`,
`is_split`/`method`/`val`/`total_chunks`, a `chunks` list of
`{filename, hash}`, `folder_path`/`remote_target_dir`, `tech_spec`,
`metadata`). Non-split single-file uploads also get one, with a 1-element
`chunks` list referencing the renamed `<name> [<short_id>]<ext>` remote name.
The write is **best-effort**: a failure logs a WARNING (`⚠️ mvmeta sidecar
write failed (chunks are safe): ...`) and returns `False` but never raises and
never flips a successful push to a failure — the chunks are the source of
truth, the sidecar is disaster-recovery redundancy for rebuilding the library
from the remote. It is written for new pushes only; the ~412 existing archived
remotes are not back-filled.

---

## 7. main.py Deep Dive

File: `C:\Users\harin\PycharmProjects\MediaVault\main.py` (3081 lines as of 2026-06-12).

> **Line-number caveat:** inline `main.py:NNN` references in this document are
> point-in-time. The §12a PONR table was re-verified 2026-06-12; older sections
> may cite pre-rollback/pre-split-hash positions. Function names are stable —
> prefer grep-by-name over line numbers.

### 7.1 Configuration block (lines 15-32)
Hardcoded constants: library paths, `LOCAL_ROOT = "C:\\Media"`,
`REMOTE_ROOT = "/sdcard/Media"`, `MKVMERGE_PATH`, `MAINFETCH_SCRIPT`,
folder names (`_parts`, `checksums`, `restore`), and the recognised
extension tuple `('.mkv', '.mp4', '.avi', '.mov')`.

### 7.2 Utility functions

> **As of IMP-A1**, the shared constants and the six helpers
> `load_library`, `save_library`, `generate_short_id`, `calculate_file_hash`,
> `human_readable_size`, and `parse_size_str` now live in `mvcommon.py` and are
> imported by both `main.py` and `mainfetch.py` via `from mvcommon import ...`
> (no local copies remain in either entry point). Both entry points' `load_library`
> is now the loud/strict version (`sys.exit(1)` on a corrupt library) — the prior
> drift where `mainfetch.load_library` silently treated a corrupt file as zero
> entries is eliminated. `calculate_file_hash` additionally gained a live progress
> bar. The line numbers in the table below are historical (pre-extraction).

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

`cmd_push` accepts an optional `device_id` kwarg (default `None`). When
set, every ADB subprocess call in the function runs as
`adb -s <device_id> ...`; when `None`, every call is bare `adb ...` and
the argv shape is byte-identical to pre-feature behaviour. The kwarg is
plumbed through `cmd_push_group`, `cmd_prep_push_rep`, and
`cmd_prep_push_rep_season`; each forwards it unchanged into the nested
`cmd_push`. Aliases (`movies`, `series`) defined in `DEVICE_ALIASES`
(see §14) are resolved to serials by `resolve_device()` at the CLI
parser layer, so the internal kwarg is always already a serial or
`None`.

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
6. **Upload loop** (`.partial` + atomic remote rename):
   - For each file path, compute the remote filename. Chunks keep their
     names. Non-chunk (single-file) uploads are renamed to embed the
     short_id: `<name> [<short_id>]<ext>`.
   - Run `adb push -p <local> <remote>.partial` — the chunk is first
     uploaded to a temporary name carrying the `PARTIAL_SUFFIX`
     (`.partial`). The `-p` flag enables ADB's built-in progress meter,
     which is left visible to the user (no custom progress bar).
   - **Atomic remote rename**: on push success, run
     `adb shell mv '<remote>.partial' '<remote>'` with `check=True`,
     single-quote-escaping both paths exactly like the `mkdir` path. This
     is the rclone-`chunker` pattern: Google Photos never indexes a
     `.partial` as a complete chunk, so a mid-push death leaves at most a
     `.partial` remnant and never a complete-named partial transfer. A
     `mv` failure is treated identically to a push failure.
   - **Critical safety check**: after a successful upload *and* rename,
     the local chunk is deleted *only if* its path contains the `_parts`
     segment. The chunk counts as "done" only once it sits at its final
     name. This protects against accidentally deleting non-chunk source
     files if logic is ever rearranged.
   - On any push or `mv` failure, break the loop and leave `_parts/`
     populated for resume. Resume re-pushes to `.partial`, which
     overwrites any stale partial on the phone (no remote `ls` needed).
   - **Retry (IMP-C2)**: the push + atomic `mv` pair is wrapped in
     `mvcommon.retry()` with 1/4/16 s backoff plus randomized jitter, so a
     transient `CalledProcessError` is re-attempted up to 3 times. Before each
     re-attempt the stale `<remote>.partial` is removed (`adb shell rm`,
     best-effort) and a single `⏳ Retry N/3` line is printed. After exhaustion
     `retry()` re-raises the last `CalledProcessError`, so the existing
     break/return-`False` failure contract is unchanged.
   - **Post-push verification (IMP-C8)**: gated on the module-level
     `PUSH_VERIFY_REMOTE` flag (default `False`). When `True`, after the
     push + atomic `mv` succeed, `_verify_chunk_hash` runs
     `adb shell sha256sum '<remote>'` on the device and compares the result
     to the stored chunk hash (`split_info.chunks[i].hash`). A mismatch raises
     `CalledProcessError` *inside* the retried closure, so the same C2 retry
     wrapper re-runs push→mv→verify; on exhaustion the push fails normally.
     Warn-and-skip (one warning, `return`, push stays alive) covers two
     non-fatal cases: (1) `sha256sum` itself is unavailable (non-zero exit),
     and (2) EMPTY or GARBLED device stdout — the verifier requires a
     well-formed 64-hex first token from the `sha256sum` output, so empty or
     non-hex output is skipped rather than crashing on an `IndexError`. Only a
     well-formed 64-hex token that *differs* from the expected hash still
     raises `CalledProcessError` and is retried under IMP-C2, so the documented
     promise matches the code. With `PUSH_VERIFY_REMOTE=False` the verify body never runs
     and the happy path is byte-for-byte unchanged. Toggling the flag without
     editing source is deferred to IMP-A5 (config file).
7. **Post-loop bookkeeping**:
   - Remove `_parts/` if it's empty.
   - If all chunks succeeded AND no `chunk_range` filter was active,
     write the remote `<base> [<short_id>].mvmeta.json` sidecar via
     `write_remote_mvmeta` (best-effort — see §6.5), then set
     `uploaded=True`, `status="onboarded"`, save library. A failure to
     write the sidecar logs a WARNING but does NOT change the `True`
     return — the chunks are the source of truth.
   - Partial uploads return success but leave state untouched.

ADB device selection is now an opt-in CLI flag (`device <id_or_name>`)
that resolves to an `adb -s <serial>` prefix on every subprocess call.
When the flag is omitted the call shape is bare `adb ...` exactly as
before, and the first `adb shell mkdir` is what implicitly tests
connectivity — there is still no explicit `adb devices` check.

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
4. Stale sweep: if `<original>.tobedeleted` exists from a prior
   interrupted run, restore the original from it (if the original path
   is empty) or delete it (if redundant). Ensures idempotent re-entry.
5. Rename `original → <original>.tobedeleted` (atomic on NTFS, same
   volume) with **3 retries** and `chmod S_IWRITE`. This is the
   point-of-no-return (`# ROLLBACK SEAM`): after this rename the
   original bytes are always on disk, either at the original path or at
   `.tobedeleted` — never absent.
6. `os.rename(tmp_path, original)` — the dummy takes the exact name
   of the original (atomic).
7. Delete `<original>.tobedeleted` (best-effort; logs a WARNING on
   failure, does not abort — the next run's stale sweep removes it).
8. Set `status = "archived"`. Library JSON mutation is identical to
   what was planned; only the content of the temp file changed.

#### `cmd_repair_dummies(prefix_filter=None)`

Generic regenerator that brings every archived dummy on disk up to the
current recipe spec. Walks every leaf entry with `status ==
"archived"`, optionally filtered by a manual-ID prefix. The whole-library
iterator explicitly skips both `season_map` and `multi_ep_alias` entries
(an explicit `continue` for each) so it is alias-safe by design rather than
by accident — consistent with the `ENTRY_TYPE_KEYS` alias-safety guardrail.

For each candidate:

1. Skip if file is missing (`missing` counter).
2. Skip if file extension is not in `VIDEO_EXTENSIONS` (`skipped`
   counter — non-video filenames have no applicable recipe).
3. Skip if file size ≥ `DUMMY_MAX_BYTES` (`skipped` — looks like a
   real video, not a dummy).
4. Call `make_video_dummy(file_path, ext)` using the per-container
   recipe. Prints `Regenerating dummy: <path>` for each file.
   On failure, increment `failed` and continue.
5. Replace the existing dummy with the regenerated video dummy via a
   SINGLE ATOMIC `os.replace(tmp, current)` — there is no window in which
   the path has no file (the previous `os.remove` + `os.rename` pair left
   such a gap on a crash or lock). This mirrors `make_video_dummy`'s own
   atomic write (§7.6 above) and the IMP-C9 atomic-swap lesson.

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
2. **Pre-merge per-chunk verification**: SHA256 each chunk and compare
   to its stored hash in `entry["split_info"]["chunks"]` *before*
   merging (mkvmerge is lenient and would otherwise silently fold a
   corrupt chunk into a bad merged file). If any chunk fails, move only
   the offending chunk(s) to `restore/quarantine/<chunk>.<timestamp>`
   via the `quarantine_restore_file` helper, leave the clean chunks in
   `restore/` (so a targeted re-fetch refills just the bad ones), delete
   any stale partial merged output at `target_path`, print a greppable
   diagnostic, and return False — the merge does not run.
3. Call `merge_video_files(chunks, target_path, seed=merge_seed)` which runs
   `mkvmerge --deterministic <seed> -o <target> chunk1 +chunk2 ...` (seed =
   stored `split_info.merge_seed` or the entry's `short_id`). The `+` syntax
   tells mkvmerge to append, not multiplex; `--deterministic` makes the merged
   container byte-identical across runs so its hash is verifiable.
4. Re-hash the merged file and **verify-or-bless** (`bless_or_verify_merged_hash`):
   if the entry is not yet `re_hashed`, BLESS — set `entry["hash"]` to the
   deterministic merged hash, set `re_hashed=true`, and store
   `merge_seed`/`merge_tool`/`rehashed_at`; if already `re_hashed`, VERIFY the
   merged hash against the stored canonical and, on mismatch, raise a
   corruption/tool-drift alarm and return BEFORE the PONR (chunks kept) without
   touching `hash`. This replaces the old blind overwrite (the default mkvmerge
   merge was non-deterministic, so the old hash was never verifiable).
5. Delete each chunk file. Remove the now-empty `restore/` folder.
6. Set `status = "restored_local"`.

For single-file entries:
1. SHA256 the file in `restore/`. On mismatch, instead of leaving the
   bad file in place, move it to `restore/quarantine/<filename>.<timestamp>`
   via the centralized `quarantine_restore_file` helper, print a
   greppable diagnostic, and return False. Because the original filename
   is now absent from `restore/`, mainfetch's `os.path.exists` skip no
   longer traps the user and a fresh fetch self-heals (re-downloads).
   The `quarantine_restore_file` helper is the single seam for "where a
   bad restore file goes", reused by the auto-rollback feature.
2. On a match, `shutil.move` it back into `folder_path` (overwriting the
   dummy).
3. Clean up empty `restore/`.
4. Set `status = "restored_local"`.

`cmd_restore_group` (lines 987-1022) iterates a season's children with
optional `episodes 1-3` filter and is tolerant: each child's
`cmd_restore` self-checks for missing chunks, so a partial fetch just
results in some children skipping cleanly. The episode-range filter
reads each child's number via the shared
`mvcommon.episode_num_from_id(child_id, group_id)` (strip the base/season
id, then anchored `^[eExX]?(\d+(?:\.\d+)?)$`), so glued anime `sSSEE`
ids filter correctly (IMP-C18). When a NON-EMPTY season is reduced to 0
by the range, it prints a `⚠️` naming the parsed range + a sample id and
restores nothing. The function now RETURNS its int restored-count so
`cmd_fetch_restore` can suppress the success banner on a 0-via-range run.

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
  - **Strategy 1**: regex `[sS]\d+[eE](\d+)` (SxxExx — deliberately does NOT
    capture decimals since PR #19: in `Fringe S03E20.6.02.AM.EST.mkv` the `.6`
    is the start of the episode TITLE, not a half-episode; SxxE-style `.5`
    episodes are not used in production) then `\d+[xX](\d+(?:\.\d+)?)` (XxYY
    convention, still decimal-capable).
  - **Strategy 2** (only when `base_id.startswith("ani-")`): a looser
    regex looking for any 1-4-digit number surrounded by `[ ._\-[]]`
    delimiters, with a guard against parsing release years
    (`19xx`/`20xx`) as episode numbers.
  For TV files where the SxxExx cluster contains two or more episode numbers (e.g. S04E19E20), the detector emits the first as the primary and creates a thin `multi_ep_alias` entry for each additional number — see §6.3.
- `cmd_push_group(group_id, ...)` (714-759) — same logic as
  `cmd_restore_group` but for pushing: season-map mode OR prefix-match
  mode, with optional `episodes A-B` filter, skipping items already
  marked uploaded. The range filter reads episode numbers via the shared
  `mvcommon.episode_num_from_id` (IMP-C18), so glued anime `sSSEE` ids
  filter the same way as restore.
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
  optional `episodes A-B` filter (resolved via the shared
  `mvcommon.episode_num_from_id`, IMP-C18); stops the whole batch on any
  push failure to "prevent mess".
- `cmd_dispatch_fetch(manual_id, episode_range)` (1350-1364) — shells
  out: `subprocess.run(["python", "mainfetch.py", "fetch", id, "episodes", range])`.
- `cmd_fetch_restore(manual_id, episode_range)` (`main.py:2879`) —
  dispatch fetch, then call `cmd_restore_group`/`cmd_restore` depending
  on entry type. **0-match guard (IMP-C18):** for a season_map run with
  an `episode_range`, if `cmd_restore_group` returns 0 it suppresses the
  green `✅✅✅ FETCH & RESTORE COMPLETE.` banner and prints a
  `⚠️ … 0 items (range … selected nothing)` summary instead (`main.py:2910`).
  Single-item / no-range runs keep the original banner; exit code is
  unchanged (the function returns `None` throughout).

---

## 8. mainfetch.py Deep Dive

File: `C:\Users\harin\PycharmProjects\MediaVault\mainfetch.py` (491 lines).

### 8.1 Configuration (lines 25-48)

Same library paths as `main.py`. Adds:

```python
CHROME_PROFILES = {
    "movies": r"C:\Media\Utils\ChromeProfile",
    "tv":     r"C:\Media\Utils\ChromeProfile_TV",
    "anime":  r"C:\Media\Utils\ChromeProfile_Anime",
}
CHROME_PROFILE_NAME       = "Default"
SYSTEM_DOWNLOADS_FOLDER   = os.path.join(os.path.expanduser("~"), "Downloads")
```

Three separate Chrome user-data directories exist because the user has three
distinct Google accounts: movies, TV series, and anime. Routing is decided
in `cmd_fetch_route` via a data-driven map (IMP-C16; IMP-A5 will source
this from `mvconfig.json`):

```python
ID_PREFIX_PROFILE = [("ani", "anime"), ("tv", "tv"), ("mov", "movies")]
DEFAULT_PROFILE = "movies"

def profile_for_id(manual_id):
    for prefix, key in ID_PREFIX_PROFILE:
        if manual_id.startswith(prefix):
            return key
    return DEFAULT_PROFILE
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

**Retry (IMP-C2)**: the whole attempt body is retried once after a 5 s wait
when the first pass returns `False` (0 thumbnails / index out of range) **or**
raises a Selenium fault, printing one `⏳ Retry 2/2 after 5s` line. The second
pass's result is final, so the `True`/`False` return contract is unchanged.

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
  entry itself. The episode-range parsing now reads each child's number
  via the shared `mvcommon.episode_num_from_id(child_id, base_id)`
  (IMP-C18) — strip `base_id` as a prefix, then anchored
  `^[eExX]?(\d+(?:\.\d+)?)$` (still `.5`-half-episode capable). This
  replaced the old unanchored ladder (`[eE](…)$` → `x(…)$` → trailing
  digits) whose final arm read glued anime `…-s0202` as episode 202.
  When a NON-EMPTY season is reduced to 0 by the range, `resolve_targets`
  prints a `⚠️` naming the parsed range + a sample child id and returns
  `[]` (the caller fetches nothing — no error).
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

### 8.8 Session-alive detector + keep-alive tooling (IMP-C17 / IMP-C6)

#### `SessionExpiredError` and `check_session_alive`

`mainfetch.py` defines:

```python
class SessionExpiredError(Exception): ...

def check_session_alive(driver) -> bool:
    """Return True if the session looks alive (or the check is uncertain,
    e.g. a Selenium fault); raise SessionExpiredError on a confident logout
    (current URL host is accounts.google.com or is not photos.google.com
    after a navigation attempt)."""
```

The detector is **shared** by two callers:

- **Live fetch path (`cmd_fetch_route` / `trigger_download`)** — called before
  each trigger to fast-fail with a clear `SessionExpiredError` rather than
  silently burning minutes waiting for downloads that will never arrive (IMP-C6).
  A backstop of **3 consecutive zero-result trigger rounds** also raises
  `SessionExpiredError` so a subtler mid-session expiry is caught too.
- **Warm-up runner (`tools/warm_profiles.py`)** — calls `check_session_alive`
  after navigating to Google Photos to determine the `OK` / `LOGGED_OUT` /
  `LAUNCH_FAIL` status for each profile.

`check_session_alive` returns `True` on alive or uncertain (Selenium fault,
unexpected page structure) and raises `SessionExpiredError` only when it is
confident the session is gone — specifically when the browser URL host is
`accounts.google.com` (redirected to login) or is not `photos.google.com`
after a navigation attempt to `https://photos.google.com`.

> **IMP-X5 reuse seam**: the planned account-health canary command will import
> `check_session_alive` directly. The ban-sentinel logic is out of scope for
> IMP-C17.

#### Single-flight session lock (`mvcommon.fetch_session_lock`)

`mvcommon.py` exposes a `fetch_session_lock` context manager backed by a lock
file at `~/.mediavault/locks/fetch_session.lock`.

- **`cmd_fetch_route`** acquires it in blocking mode for the entire batch so
  only one fetch session owns Chrome's CDP port 9222 at a time.
- **The warm-up runner** tries it in **non-blocking** mode before launching
  Chrome; if the lock is held (a fetch is running) it skips that profile's
  warm-up and logs accordingly.
- `LockHeldError` is raised by `mvcommon` when a non-blocking acquire fails;
  callers catch it and decide whether to skip or abort.

---

## 9. Auxiliary Scripts

### 9.1 `tools/migrate_lib.py` (one-shot, 68 lines)

Reads `C:\Media\library.json` (the old combined file) and splits it
into `library_movies.json` / `library_series.json` / `library_anime.json`
by ID prefix (`tv-` -> series, `ani-` -> anime, everything else ->
movies). Writes with `indent=4`. Idempotent in the sense that re-running
it overwrites the three files with the same content (provided
`library.json` itself hasn't changed). Not invoked by anything else.

### 9.1a `tools/migrate_rehash_flag.py` (one-shot, PR #20)

Stamps `re_hashed: false` onto every pre-existing split entry so the
verify-or-bless logic (§6.4a) has an explicit unblessed marker to key on.
Idempotent; already run against the live libraries at PR #20 time. Not invoked
by anything else.

### 9.1b Fetch-session tooling (IMP-C17)

Three files added under `tools/`:

| File | Purpose |
|------|---------|
| `tools/warm_profiles.py` | Daily keep-alive runner. Launches Chrome for each profile (or a single `--profile <key>`), calls `check_session_alive`, logs `OK` / `LOGGED_OUT` / `LAUNCH_FAIL` to `~/.mediavault/logs/warm_profiles.log`, fires a Windows desktop toast on failure, and exits non-zero if any profile is degraded. |
| `tools/notify_toast.py` | Dependency-free Windows toast helper. Calls the WinRT `ToastNotification` API via `ctypes` / `winrt` if available; falls back to a `print` so non-Windows or headless environments don't crash. `send_toast(title, body)` is the only public function. |
| `tools/mediavault_warm_profiles.xml` | Windows Task Scheduler definition. Runs `warm_profiles.py` daily at ~03:00 as the current interactive user, only when the machine has been idle for 5 minutes, without requiring elevated privileges. Register with `schtasks /create /xml "tools\mediavault_warm_profiles.xml" /tn "MediaVault Warm Profiles"`; remove with `schtasks /delete /tn "MediaVault Warm Profiles" /f`. |

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
7. Loop chunks: `adb push -p <local> /sdcard/Media/.../<chunk>.partial`,
   then `adb shell mv '<chunk>.partial' '<chunk>'` to atomically rename
   on success (so Google Photos never sees a partial as complete).
   After a successful push+rename, delete the local chunk (only if its
   path contains `_parts`).
8. After all chunks done, `os.rmdir(_parts/)` (empty).
9. Write the remote `<base> [<short_id>].mvmeta.json` sidecar
   (best-effort), then set `uploaded=True`, `status="onboarded"`. Save.

Meanwhile, on the phone, Google Photos auto-upload picks up the new
files in `/sdcard/Media/...` (the user has configured the Pixel's
Photos app to back up that folder) and uploads them at original
quality to the cloud. **MediaVault does not orchestrate this step.**

### Stage 3 — REPLACE (`python main.py replace mov-en-2024-inception`)

1. `main.py:1521` -> `cmd_replace(mid)`.
2. Refuse if `uploaded != True`.
3. Write `<original>.temp_dummy` containing the hash and split info.
4. Stale sweep: recover from any prior interrupted replace.
5. Rename `original → <original>.tobedeleted` (atomic commit,
   ROLLBACK SEAM, 3-retry with `chmod`).
6. Rename dummy → original's name (atomic, dummy goes live).
7. Delete `.tobedeleted` (best-effort, non-fatal).
8. Set `status="archived"`.

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
3. `merge_video_files(chunks, target_path, seed=merge_seed)`:
   `mkvmerge --deterministic <seed> -o target chunk1 +chunk2` (seed = short_id).
4. SHA256 the new (byte-identical) merged file -> **verify-or-bless**: first
   restore BLESSES `entry["hash"]` to the deterministic merged hash and sets
   `re_hashed`/`merge_seed`/`merge_tool`/`rehashed_at`; a later restore VERIFIES
   it (alarm + return pre-PONR on mismatch). Replaces the old blind overwrite.
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
- **Transient-failure retry (IMP-C2)**: a shared `mvcommon.retry()` helper
  adds an exponential-backoff retry layer at two call sites. `cmd_push` wraps
  the push + atomic `mv` pair (1/4/16 s backoff + jitter, up to 3 attempts,
  with a pre-retry `.partial` rm and a `⏳ Retry N/3` print); `trigger_download`
  retries its whole body once after 5 s on a 0-thumbnail miss or a caught
  Selenium fault. Both preserve the existing failure contract: an exhausted
  push re-raises the last `CalledProcessError` (so the loop still breaks and
  returns `False`, leaving `_parts/` for resume) and an exhausted trigger still
  returns `False`. First-attempt success is byte-for-byte unchanged.
- **Hash mismatch on restore**: the bad file (single-file path) or
  offending chunk(s) (split path) are moved to
  `restore/quarantine/<name>.<timestamp>` via the centralized
  `quarantine_restore_file` helper instead of being left in `restore/`,
  and a greppable diagnostic is printed
  (`Hash mismatch. Bad file quarantined at <path>. A fresh fetch will
  re-download.`). In the split path any stale partial merged output is
  deleted and clean chunks are left in place. This leaves `restore/`
  self-healing — because the original filename is gone, mainfetch's
  existence skip no longer traps the user and a re-fetch re-downloads
  automatically. The defensive fallback (move blocked by a file lock)
  reverts to the prior leave-in-place behavior so restore is never made
  worse than before.
- **Auto-rollback (unified failure handling)**: every multi-step command is
  wrapped by the auto-rollback mechanism (§12a). A reversible failure restores
  the exact pre-command state; an irreversible (post-PONR) failure hard-fails
  with an actionable message naming an existing command. The two former ad-hoc
  paths — `cmd_prep_push_rep` deleting `_parts/` on push failure, and
  `cmd_prep_push_rep_season` breaking the loop "to prevent mess" — have been
  replaced by this single mechanism (push failure is now an O-1 resume-message;
  the season loop keeps completed episodes and prints a resume-range command).
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

## 12a. Auto-Rollback for Multi-Step Commands

MediaVault's multi-step commands (`prep` → `push` → `replace`, the
`prep_push_rep` / `prep_push_rep_season` orchestrators, and the
`fetch` → `restore` side) can fail half-way. Auto-rollback (feature branch
`feature/auto_rollback`) unifies failure handling into **one** mechanism so a
failure never leaves an undocumented half-finished state. There is exactly one
rollback mechanism in the codebase — the two former ad-hoc paths are gone.

> **Deep-dive** (lifecycle + recovery diagrams, the full failure-scenario matrix,
> storage analysis, and the change-gate) lives in
> [`docs/feature-auto-rollback/ROLLBACK_MECHANISM.md`](docs/feature-auto-rollback/ROLLBACK_MECHANISM.md).
> ⚠️ This mechanism is **change-gated** — see "Change-gate" at the end of this
> section before editing anything that touches it.

### The mechanism — `RollbackJournal` (on-disk operation journal)

The chosen architecture (Step 3 bake-off winner, see `DECISIONS.md` N-6 —
**Candidate C, selected wholesale for all operations**) is a durable on-disk
operation journal in `main.py`:

- `class RollbackJournal` (`main.py:550`) — one journal per command, per id. It
  writes `<folder>/.mediavault_txn.json` (constant `TXN_JOURNAL_NAME`,
  `main.py:535`) and **records each intended mutation BEFORE performing it**, so a
  crash mid-action still leaves a replayable inverse on disk. The record vocabulary
  is small and fixed (`record_create_file`, `record_create_dir`, `record_set_field`,
  `record_create_entry`, `record_link_child`, …). The write is fsync-flushed +
  `os.replace`'d so it is durable.
- On a reversible failure, `journal.rollback(library)` replays the recorded
  inverses LIFO, reverts the in-memory library dict, calls `save_library()`, and
  deletes the journal. On clean success `journal.commit()` deletes the journal.
  Inverse failures (e.g. a Windows file lock) are reported as a **partial rollback
  honestly** and the journal is kept for retry.
- `mark_point_of_no_return()` (`main.py:621`) writes a `crossed_ponr` marker into
  the journal; thereafter a failure raises `RollbackHardFail` (`main.py:538`)
  rather than attempting a (now-impossible) rollback.
- `class RollbackHardFail(Exception)` (`main.py:538`) carries `(state, reason,
  resume_cmd)` — the structured hard-fail that the orchestrators turn into the
  user-facing actionable message. The `resume_cmd` always names an **existing**
  command (never a new "fetch-to-fix" command — decision N-2).
- `recover_journal(folder_path)` (`main.py:701`) is the crash-recovery entry point:
  if a journal survives a hard process kill **and** never crossed its PONR, it
  replays the inverses to finish the interrupted rollback. A journal that crossed
  the PONR is left in place for inspection. This is **not** called on the happy
  path, so unrelated commands stay byte-for-byte identical (decision D-4). This
  durable crash-recovery of the rollback itself is the property that distinguished
  Candidate C from the in-memory alternatives (A: transaction context-manager;
  B: compensating-action stack — see `docs/feature-auto-rollback/`).
  `recover_journal` is now reachable from the CLI via `python main.py recover <id|folder>` (and `recover --scan` for a read-only sweep of all media roots); the function's semantics and journal format are unchanged.

### Point-of-no-return (PONR) table — verified against current `main.py`

The master/original video is the source of truth: as long as it exists on disk the
operation is reversible. It is destroyed in **exactly two** places (the only true
PONRs). Push is reversible/resumable (O-1) — the master always survives a push
failure.

| Command (def line) | PONR | On failure |
|---|---|---|
| `cmd_prep` (`main.py:795`) | none — fully reversible | auto-rollback this-run entry / sidecars / parent child-link (early-skips create no artifacts and never roll back) |
| `cmd_push` (`main.py:1217`) | **none (O-1)** — resumable | resume-message: leave the partial upload, entry stays `local_ready`/`uploaded=False`, print `push <id>`. Roll back this-run `_parts`/`checksums`/`split_info` only if created this run AND failure is pre-any-upload; a pre-existing `_parts/` (resume) is never deleted |
| `cmd_replace` (`main.py:1741`) | **commit rename `os.rename(original, tobedeleted)` (`main.py:1804`)**, marked at `main.py:1806` | pre-PONR: roll back the dummy temp. At/after PONR: `RollbackHardFail` naming `fetch_restore <id>` (`main.py:1866`). C9 stale-sweep self-heals a torn crash on the next `replace` |
| `cmd_restore` (`main.py:2032`) | **split-path merged-chunk delete**, marked at `main.py:2185` | pre-PONR: reuse C11 `quarantine_restore_file` + reproducible-output cleanup. At/after PONR: `RollbackHardFail` naming `fetch_restore <id>`. Standard path is a single `shutil.move` — no torn window |

*(Line numbers re-verified 2026-06-12.)*

### O-1 resume-message vs O-2 hard-fail split

- **O-1 (push = resume-message).** A failed multi-chunk push is reversible/resumable
  because the master survives; `cmd_push` already auto-resumes from a surviving
  `_parts/`. So a push failure is NOT a PONR — it leaves the partial upload and
  prints the exact `push <id>` resume command.
- **O-2 (the two true PONRs).** `cmd_replace` after the commit rename, and
  `cmd_restore` (split) after the merged-chunk delete. Both hard-fail with a
  message naming the existing `fetch_restore <id>` pipeline (the bytes are in the
  cloud / need a re-fetch). No new command is invented.

### Orchestrator unification + season resume-range messaging

- `cmd_prep_push_rep` (`main.py:2515`) — the ad-hoc cleanup block ("Reverting
  temporary files", `_parts` rmtree, "run 'push' manually") is gone; it relies on
  the wrapped `cmd_push`'s O-1 resume-message and the wrapped `cmd_replace`'s
  rollback-or-hard-fail. It catches `RollbackHardFail` (`main.py:2545`).
- `cmd_prep_push_rep_season` (`main.py:2553`) — the bare `break` "to prevent mess"
  is gone. Completed episodes stay; the in-flight item has already rolled itself
  back (reversible) or hard-failed (irreversible) via the wrapped commands; the
  orchestrator computes and prints a **resume-range** command (failing episode →
  end of the range-filtered ids), reconstructing the exact `SIZE_*` / `device` /
  `episodes` args and handling a `.5` episode in the range filter. It catches
  `RollbackHardFail` at `main.py:2680` / `main.py:2703`. Messaging only — there is
  no progress-file dependency (C1 is not merged).

### Constraints honored

- **D-4** happy path byte-for-byte identical — commands are *wrapped*, not
  rewritten; the journal is removed on clean success and `recover_journal()` is not
  on the happy path.
- **D-6** snapshot-before — rollback removes only the set-difference created this
  run; pre-existing artifacts (resume `_parts/`, pre-existing `split_info`) are
  never touched.
- **D-7** season parent `season_map` — deleted only if this run created it AND
  rolling back its child leaves 0 children; otherwise only the child-link is removed
  and `total_episodes` recomputed.
- **D-9** the empty remote `adb mkdir` dir is left in place on rollback.
- **C9 / C11 seams reused** — replace stale-sweep and restore quarantine are not
  duplicated.

Production changes are confined to `main.py` + `tests/` (`mainfetch.py` and
`mvcommon.py` are untouched). The full scenario matrix lives in
`tests/test_rollback.py`, including a durable-journal crash-recovery test.

### Change-gate (load-bearing)

This mechanism is depended on by every multi-step command. **Any task that would
change its behavior MUST pause before implementing, state exactly what differs from
the behavior documented here, and ask the user as an explicit decision** — see
`CLAUDE.md` ("Auto-rollback is load-bearing — change-gate") and
`docs/feature-auto-rollback/ROLLBACK_MECHANISM.md` §10. "Affecting rollback"
includes the journal format/durability, the PONR locations, the created-this-run
scoping (D-6/D-7), the `cmd_*` wrapping, `recover_journal` semantics, the season
resume-range messaging, and the `RollbackHardFail` contract. Forward-looking
rollback/storage work is tracked in `improvements/improvements_tierR.md`.

> **Split-hash-deterministic feature (§6.4a):** the `cmd_restore` verify-or-bless
> change and the `tempdir` `_parts/` relocation do NOT alter the rollback PONR
> locations, the journal format/durability, or created-this-run scoping. They are
> the ONLY two user-pre-authorized rollback-adjacent changes (the blind
> hash-overwrite reversal and the `_parts` path value); everything else above
> stays frozen. See `docs/feature-auto-rollback/ROLLBACK_MECHANISM.md` §10 and
> `docs/feature-split-hash-deterministic/`.

---

## 13. Testing Approach

**A real pytest suite now exists** (bootstrapped by PR #14, grown by every PR
since). As of 2026-06-13: **163 passed** in the full suite (`pytest -q`) plus a
separate **50-test smoke suite** (`pytest tests/smoke -q`). Test files under
`tests/`:

| File | Covers |
|---|---|
| `test_rollback.py` | The full auto-rollback scenario matrix incl. durable-journal crash recovery (and, since IMP-H3, a genuine split-during-push → pre-upload rollback via the `ffmpeg_splittable_master_mkv` fixture) |
| `test_baseline_happy_path.py` | Pre-rollback behavior characterization (happy paths) |
| `test_cmd_push_partial.py` / `test_cmd_push_retry.py` / `test_cmd_push_verify.py` / `test_cmd_push_mock_device.py` | `.partial`+mv protocol, C2 retry, C8 remote verify, data-integrity round-trip |
| `test_cmd_replace.py` | C9 atomic replace + stale sweep |
| `test_cmd_restore_quarantine.py` | C11 quarantine + split-restore guards |
| `test_rehash.py` | PR #20 verify-or-bless / eager / re-split reset |
| `test_prep_season_episode_parse.py` | PR #19 dotted-title parsing + PR #21 combined-episode aliases |
| `test_recover_cli.py` | IMP-R2 `recover` / `recover --scan` |
| `test_trigger_download_retry.py` | C2 Selenium one-retry |
| `test_mvcommon.py` | Pure helpers + library round-trip |
| `test_alias_consumers.py` | IMP-C12/C13 alias regression — every fixed `multi_ep_alias` consumer (#1-#8) + non-alias controls, on the `sandbox_alias` fixture |
| `test_entry_schema_guard.py` | IMP-H3 — round-trips one entry of every `ENTRY_TYPE_KEYS` type through `save`/`load`, and asserts the whole-library read commands tolerate non-physical entries (the registry-driven guard) |

The **smoke suite** (`tests/smoke/`, IMP-H3) is a fast (~8-10s) full-command
gate: `TestEachCommand` drives every user-facing command + its major options
against tiny fixtures and the existing stubs (asserting the top-level effect),
and `TestAliasSweep` runs every command over a `multi_ep_alias`-bearing library
(asserting no crash) — the anti-PR-#21 cross-command gate. It is the **mandated
pre-PR cross-command check** (`pytest tests/smoke -q`), enforced by the agent
pipeline (§19).

Mocking philosophy, fixture catalogue (`sandbox` — now also redirects
`LOCAL_ROOT` so tests never touch real `C:\Media`; `sandbox_alias` — a Series
library seeded with a full combined-episode alias chain; `sandbox_entry`,
`fake_dummy`, `mock_device`, `FakeAdb`, `ffmpeg_splittable_master_mkv` — a
genuinely-splittable ~60 MB MKV reusing production `main.resolve_ffmpeg()`), the
dual-binding patch hazard, and Windows gotchas are documented in
[`docs/testing-strategy.md`](docs/testing-strategy.md). There is still no CI
config — the suite runs locally via `pytest -q` (and `pytest tests/smoke -q`).
Code paths outside the listed areas (Selenium fetch against real Google Photos,
MediaInfo parsing) remain "tested by use"; the legacy snapshots under `archive/`
serve as informal regression baselines.

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

> **As of IMP-A1**, the shared library/path constants (`LIBRARY_MOVIES`,
> `LIBRARY_SERIES`, `LIBRARY_ANIME`, `LOCAL_ROOT`, `MKVMERGE_PATH`,
> `SPLIT_DIR_NAME`, `CHECKSUM_DIR_NAME`, `RESTORE_DIR_NAME`,
> `VIDEO_EXTENSIONS`) are now defined once in `mvcommon.py` and imported by both
> entry points. The "Defined in" column below reflects the historical
> per-file locations; edit `mvcommon.py` to re-target any shared constant.

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
| `DEVICE_ALIASES` | `main.py` near line 68 | `{"movies": "FA69H0300200", "series": "FA75V0303405"}` — hardcoded user-edited mapping from CLI alias to ADB serial; consumed by `resolve_device()` |
| `SPLIT_DIR_NAME` | both | `_parts` |
| `CHECKSUM_DIR_NAME` | both | `checksums` |
| `RESTORE_DIR_NAME` | both | `restore` |
| `VIDEO_EXTENSIONS` | both | `('.mkv', '.mp4', '.avi', '.mov')` |
| `PARTIAL_SUFFIX` | `main.py` | `.partial` — chunks upload to `<final>.partial` then `adb shell mv` to final (atomic remote rename); auto-rollback seam |
| `MVMETA_SUFFIX` | `main.py` | `.mvmeta.json` — remote disaster-recovery sidecar name (`<base> [<short_id>].mvmeta.json`) written on full push success |
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
- ~~**No transactional save**~~: **FIXED** — duplicate of the first bullet;
  `save_library` writes via `tempfile.mkstemp` + `os.replace` (mvcommon.py).
  Only the rotating `<file>.bak` idea from §17.2 remains unimplemented.
- **`multi_ep_alias` iteration gaps (found 2026-06-12 review)**: PR #21
  de-aliased the four group loops and `mainfetch.resolve_targets`, but
  whole-library iterators were missed — `cmd_scan_unprepped` KeyErrors on
  `entry['folder_path']` and `cmd_local_status` TypeErrors on a `None`
  filename when any alias exists; direct single-id commands (`push`,
  `replace`, `restore`, `check`, `verify_restore`, `fetch_restore`) crash
  with raw tracebacks when given an alias id. Tracked as IMP-C12/IMP-C13;
  full analysis in `docs/feature-fable-review/REVIEW_NOTES.md` §A.
- ~~**`push_group` argv parser can infinite-loop**~~: **FIXED** (IMP-C14) —
  `push_group` parsing is now the extracted `main.parse_push_group_args`
  with the same fail-fast "Missing value" arms as `push`, so a trailing
  value-keyword (`SIZE_MB`, `episodes`, `device`) exits with usage instead
  of hanging. Same change extracted `mainfetch.parse_fetch_args` (fixing the
  bare-`fetch` `IndexError`) and made `cmd_replace(<unknown_id>)` print
  `❌ Error: '<id>' not found in library.` instead of silently returning
  `False`. See §5.
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
- ~~**`mainfetch.load_library` swallows errors silently**~~: **FIXED** by
  IMP-A1 — both entry points import the strict `mvcommon.load_library`
  (`sys.exit(1)` on corruption); the silent-zero-entries behavior is gone.
- **`cmd_dispatch_fetch` invokes `"python"` literally**
  (`main.py:2719`) instead of `sys.executable`. On a machine where
  `PATH` resolves `python` to a different interpreter than the one
  running `main.py`, the spawned `mainfetch.py` will fail to import
  `selenium`.

### Code smells

- ~~Heavy code duplication between `main.py` and `mainfetch.py`~~ **DONE** —
  IMP-A1 extracted `mvcommon.py` (constants + 6 shared helpers + `retry()`);
  no local copies remain in either entry point.
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

1. ~~**Extract a shared `mvcommon.py`**~~ — **DONE** (IMP-A1, PR #8).
2. **Atomic JSON saves** — **DONE** (mkstemp + `os.replace` in mvcommon);
   the rotating `<file>.bak` backup before save remains open.
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
6. **Multi-device push** — **DONE** in its single-device-select form
   (PR #2: `device <id_or_name>` + `DEVICE_ALIASES` on all four push
   commands). True *parallel* multi-device orchestration (the user runs
   4 Pixel 1 XLs) remains open — see IMP-E7 and the end-goal roadmap.
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
11. **Opus 4.8 "dynamic workflows" for the agent pipeline**: a session
    can plan a task then spin up hundreds of verified parallel subagents.
    It could replace the sequential orchestrator + multi-candidate
    worktrees, but it is a main-session capability and our `orchestrator`
    is itself a subagent (subagents can't spawn subagents). Exploiting it
    means running the orchestrator as the main session (`--agent`). Deferred;
    see §19 and `improvements/improvements_tierH.md` (IMP-H2).

---

## 18. Quick Reference: where to find things

| If you want to change... | Look at... |
|---|---|
| What constitutes a "split" / chunk size | `main.py:split_video_file` (196-269) |
| How chunks are named | `main.py:204` (output_pattern) |
| How upload progress is shown | `main.py:675` (`adb push -p`) |
| How to pin a push to a specific phone | `main.py:resolve_device()` + `cmd_push(... device_id=...)`; alias dict at `main.py:DEVICE_ALIASES` |
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

## 19. Agentic Development Workflow (Opus 4.8 effort tiers)

Non-trivial changes to MediaVault are not hand-written; they are produced by a
multi-agent Claude Code pipeline defined under `.claude/agents/`. This section
documents that pipeline so a reader knows how the repo is actually changed and
how to tune it. (The agent files are dev tooling — they do not ship in or affect
the `main.py` / `mainfetch.py` runtime.)

### 19.1 Agent roster

| Agent | Role | Model | Effort |
|---|---|---|---|
| `architect` | One-time deep read → writes/refreshes `ARCHITECTURE.md` (read-only on code) | opus | high |
| `planner` | Decomposes a task into `PLAN.md`; tags each step `[model:…][effort:…]` | opus | high |
| `orchestrator` | Drives `PLAN.md` end-to-end; routes steps, handles multi-candidate, triggers git ops | opus | high |
| `executor-haiku` | Executes mechanical steps (`[model: haiku]`) | haiku | low |
| `executor-sonnet` | Executes standard implementation/refactor/test steps (`[model: sonnet]`) | sonnet | medium |
| `executor-opus` | Executes the hardest reasoning steps (`[model: opus]`) | opus | max |
| `git-agent` | The only agent that runs git — branch/commit/worktree/merge/push | haiku | low |
| `judge` | Compares candidates of a multi-candidate step → `DECISION.md` | opus | high |

`model: opus` is kept as an **alias** (resolves to the latest Opus — 4.8 today),
so the agents auto-track future Opus releases rather than being pinned.

### 19.2 Flow

```
   task
    |
    v
 [architect]  (occasional / first pass)  --->  ARCHITECTURE.md
    |
    v
 [planner]  --->  PLAN.md   (each step: [model: ...] [effort: ...])
    |
    v
 [orchestrator]  <----- CREATE_BRANCH / COMMIT_STEP / PUSH ----->  [git-agent]
    |
    |  routes each step by its [model: ...] tag
    +--------------------+--------------------+
    v                    v                    v
 [executor-haiku]   [executor-sonnet]   [executor-opus]
   effort: low        effort: medium       effort: max
    |                    |                    |
    +--------- writes step outcome --> STATUS.md
    |
    |  (multi-candidate steps only)
    v
 candidates A/B/C built in isolated git worktrees
    |
    v
 [judge]  --->  DECISION.md   --->  orchestrator squash-merges the winner
```

Artifacts the pipeline reads/writes: `PLAN.md` (the plan), `STATUS.md` (per-step
execution log), `CRITIQUE.md` (per-candidate self-review), `DECISION.md` (judge
verdict). All are plain Markdown at the repo root / under `.candidates/`.

### 19.3 Effort tiers

Effort controls how much a model deliberates. On Opus 4.8 the tiers are far
"hotter" than the same-named 4.7 tiers (per the system card, Opus 4.8 `low` ≈ 4.7
`max` capability), so even modest tiers are strong:

| Tier | Use for |
|---|---|
| `low` | mechanical / high-volume / latency-sensitive work |
| `medium` | standard coding following an existing pattern (Sonnet's default) |
| `high` | tricky logic, ambiguous or security-sensitive steps |
| `xhigh` | genuinely hard reasoning that `high` doesn't cover |
| `max` | hardest, highest-stakes, hard-to-reverse steps (self-tests its own code) |

### 19.4 How effort is applied — "hybrid advisory"

The Task/Agent tool has **no per-invocation effort parameter** (open upstream:
`anthropics/claude-code` issues #25669, #43083, #31536). Effort can only be set
statically via an agent's `effort:` frontmatter or the session-level `/effort`.
Consequence for this pipeline:

- Each executor's effort is **fixed in frontmatter** (haiku→low, sonnet→medium, opus→max).
- The planner's per-step `[effort: …]` tag is therefore **advisory**: it documents
  intended effort and is the basis for the model choice. The orchestrator routes by
  `[model: …]`, notes any mismatch, and flags under-powered steps in its final summary.
- **Practical rule:** to actually deliver high/xhigh/max thinking today, assign
  `[model: opus]` (which runs at `max`). The advisory tag future-proofs the plan for
  when per-call effort lands upstream.

See `.claude/AGENT_WORKFLOW_NOTES.md` for the full migration record and the
pre-migration backup at `.claude/agents_pre_opus48/`, and `improvements/improvements_tierH.md`
(IMP-H1/H2) for the tracked task and the deferred "dynamic workflows" follow-up.

### 19.5 Pipeline hardening (IMP-H3)

Two cross-cutting rules were added to close the PR #21 failure class (a new
shared data shape silently breaking a distant consumer, with no gate to catch
it):

- **Smoke gate.** Any plan whose steps touch `main.py` / `mainfetch.py` /
  `mvcommon.py` must run `pytest tests/smoke -q` as a final gate. The planner
  mandates it in the plan's Verification, and the orchestrator/executors enforce
  it at the per-step commit point, on the merged multi-candidate result, and
  pre-PR. The planner also mandates a **Consumer Impact Analysis** that consults
  `ENTRY_TYPE_KEYS` and greps every consumer of any changed shared data contract.
- **Out-of-band DATA_REQUEST protocol.** Web tools (`WebSearch` / `WebFetch`) are
  granted only to `planner`, `orchestrator`, and `architect`. The three executors
  are web-less; when one needs an external fact it raises a fenced `DATA_REQUEST`
  block, which the orchestrator services and returns as a `DATA_RESPONSE` before
  re-dispatching the same step.

---

*Last updated 2026-06-12 (fable-review session, branch `feature_fable_review`):
refreshed line counts (`main.py` 3081 / `mainfetch.py` 491 / `mvcommon.py` 168),
repo layout (docs/ + tests/ + .candidates/ real contents, migrate_rehash_flag),
§7.8 PR #19 regex, §12a anchors re-verified, §13 rewritten for the 13-file test
suite, §16 fixed-item strikethroughs + the 2026-06-12 multi_ep_alias iteration
findings (IMP-C12/C13/C14), §17 done-markers. Companion review artifacts live in
`docs/feature-fable-review/` (REVIEW_NOTES, PR_REVIEW, research dossier,
end-goal roadmap). Graph views: `ARCHITECTURE_GRAPH.md` +
`docs/architecture-graph/graph.html`. Earlier milestones: 2026-05-30 agent
pipeline → Opus 4.8 effort tiers (§19); 2026-05-25 live-data audit
(102 / 290+28 / 140+5 entries).*
