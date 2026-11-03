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
fetch (Google Photos -> <folder>\restore\ via Selenium, exact item) -> restore (merge + verify -> back in place)
```

Since IMP-C25 the fetch finds the **exact** Google Photos item of every uploaded object (its stored
`photo_id`, its content key = SHA-1 = Google's `dedupKey`, the second it was taken, then names), and
downloads it straight into the entry's `restore\` folder. Nothing lands in `~/Downloads` any more. The
identity of every object is captured at prep/push and later confirmed in Google Photos (`map_photos`,
`check_photos`, the auto-confirm after the next push). See §8 and §6.3 ("Optional `gphotos` block").

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
|-- main.py                          ACTIVE — local pipeline + ADB push + restore + the Google Photos identity commands
|                                    (18294 lines as of 2026-10-20)
|-- mainfetch.py                     ACTIVE — Selenium fetch from Google Photos: exact-item locator, download hygiene,
|                                    `locate`, the `check` engine (6313 lines as of 2026-10-20)
|-- mvcommon.py                      ACTIVE — shared library I/O + hashing constants/helpers imported by both entry points;
|                                    since IMP-C25 also the Google Photos identity primitives (gphotos block, learned
|                                    sidecar, failed list, hints, resolve log) (2510 lines as of 2026-10-20)
|-- gpweb.py                         ACTIVE (IMP-C25) — stdlib-only Google Photos parsers: item-page record, batchexecute
|                                    envelope, one typed parser per allowlisted rpcid, locale-tolerant panel/tile text,
|                                    name normalization, query builders, shared page scripts
|-- gprpc.py                         ACTIVE (IMP-C25) — the ONE read-only Google Photos RPC client (allowlist, page-context
|                                    calls, pacing, retries, circuit breaker, redacted download links) — §8.9
|-- gpcapture.py                     ACTIVE (IMP-C25) — post-commit identity capture at prep/push (`<short_id>.gpcapture.json`,
|                                    capture v2) + the mvmeta v2 key vocabulary — §6.5
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
|   |-- migrate_rehash_flag.py       AUX (one-shot, PR #20) — stamps re_hashed=false onto pre-existing split entries
|   |-- gp_inventory.py              AUX (IMP-C25) — read-only Google Photos inventory crawler, one account at a time
|   |                                (rpc mode default, dom fallback) → D:\MediaVault_gp_inventory\ — §9.3
|   `-- warm_profiles.py             AUX (IMP-C17) — daily Chrome-profile keep-alive; closes the Chrome it launched — §9.1b
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
|-- library_others.json              ACTIVE  (keys: oth-* + season_map entries) — IMP-D18 "Others" category (sports now; documentaries later)
|-- library.json                     LEGACY  pre-migration combined file (read-only backup)
|-- library - Copy.json              LEGACY  hand-made backup of legacy library.json
|-- library - Copy (2).json          LEGACY  hand-made backup of legacy library.json
|
|-- Movies\                          media root for `scan_unprepped` Movies category
|-- Series\                          media root for Series category
|-- Anime\                           media root for Anime category
|-- Sports\                          media root for the "other" category (CATEGORY_ROOTS["other"] = ["Sports"]; list-capable — append "Documentary" later)
|-- Utils\
    |-- ChromeProfile\               Selenium-attached Chrome user data dir for movies
    |-- ChromeProfile_TV\            Selenium-attached Chrome user data dir for TV (series account)
    |-- ChromeProfile_Anime\         Selenium-attached Chrome user data dir for anime
    |-- ChromeProfile_Others\        Selenium-attached Chrome user data dir for the Others/sports account (IMP-D18)
                                     — a prerequisite the user creates and signs in; fetch/check/crawl refuse to
                                     launch Chrome on a missing profile folder (`mainfetch.profile_preflight`)
```

IMP-C25 state that lives outside the repo (private: photo ids, account data — never committed, P-15):

```
~\.mediavault\
|-- locks\fetch_session.lock         single-flight lock (fetch, check, crawl, warm-up, resolve, locate)
|-- fetch_journal\<run_id>.json      one per fetch run: launched Chrome pid + profile, incoming folders,
|                                    per-object states, pending deletes (never a URL) — §8.5
`-- gphotos\
    |-- learned_<account>.jsonl      append-only learned records (P-10) — folded into the library by the apply engine
    |-- failed.jsonl                 append-only failed list (P-18)
    |-- failed_objects.csv           its regenerated Excel view (UTF-8 BOM, CRLF; editable decision columns)
    |-- hints.jsonl                  manual resolutions from map_photos --resolutions (check_photos hinted)
    |-- resolved.jsonl               resolve_photos' answer log (resume + the "not uploaded" list)
    |-- map\<UTC ts>\                one map_photos run: summary.txt, mapping.jsonl/.csv, inconsistencies.csv,
    |                                resolutions_todo.csv, unmatched_<account>.csv, devices.csv
    |-- check\<UTC ts>\              one check_photos run: run.json, results.jsonl, progress.json, report.txt,
    |                                outliers.csv, applied.json
    `-- mvmeta\<UTC ts>\             mvmeta_upgrade --apply: originals\NNNN.mvmeta.json + backup.jsonl + report.txt
D:\MediaVault_gp_inventory\          crawler output: <account>.tiles.jsonl / .items.jsonl / .runs.jsonl / .log
```

> The current `main.py` and `mainfetch.py` read/write
> `library_movies.json`, `library_series.json`, `library_anime.json`,
> and `library_others.json` (IMP-D18) from `C:\Media\`. The older single
> `C:\Media\library.json` is no
> longer the source of truth and is only consumed by
> `tools/migrate_lib.py`. `archive/legacy/media_library.json` is
> unrelated to the live system and unused.

---

## 4. Active vs Legacy File Inventory

| File | Status | Purpose |
|---|---|---|
| `main.py` | **ACTIVE** | Single CLI entry for prep/split/push/replace/restore/scan/sort/local_status and dispatching fetch |
| `mainfetch.py` | **ACTIVE** | Selenium-driven Google Photos download: exact-item locator (IMP-C25), downloads straight into the per-entry `restore\.mv_incoming\`, SHA-256-verified rename into `restore\`; also the read-only `locate` diagnostic and the `check` child of `check_photos` |
| `gpweb.py` / `gprpc.py` / `gpcapture.py` | **ACTIVE** (IMP-C25) | Google Photos parsers / the read-only RPC client / prep-push identity capture |
| `tools/gp_inventory.py` | **AUX** (IMP-C25) | Read-only per-account Google Photos inventory crawler (input of `map_photos`) |
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

All subcommands and their argument shapes (the CLI dispatcher starts at `main.py:17646`).
Brackets denote optional args; `[id]` is the manual library ID like
`mov-en-2024-inception` or `tv-en-2016-strangerthings-s01e03`.

| Subcommand | Signature | Function |
|---|---|---|
| `prep` | `prep [id] [filepath] [--extras "<f1>;<f2>"] [--extras-size <v>]` | `cmd_prep` — index a new local file; `--extras` **registers** bonus folders (scan+hash+merge only — prep never uploads; IMP-D19, see the extras note below) |
| `prep_season` | `prep_season [base_id] [folder] [--extras "<f1>;<f2>"] [--extras-size <v>]` | `cmd_prep_season` — batch-prep an entire season folder; `--extras` registers bonus folders onto the `season_map` title |
| `prep_push_rep` | `prep_push_rep [id] [filepath] [SIZE_MB/SIZE_GB/COUNT val] [device <id_or_name>] [rehash] [tempdir <path>] [--extras "<f1>;<f2>"] [--extras-size <v>]` | `cmd_prep_push_rep` — full pipeline on one movie; with `--extras` it also pushes AND dummies the title's registered extras after the main content |
| `prep_push_rep_season` | `prep_push_rep_season [id] [folder] [SIZE_MB/SIZE_GB/COUNT val] [episodes <range>] [device <id_or_name>] [rehash] [tempdir <path>] [--extras "<f1>;<f2>"] [--extras-size <v>]` | `cmd_prep_push_rep_season` — sequential pipeline for a season; with `--extras` it also pushes AND dummies the season's registered extras after the episode loop |
| `prep_push_rep_enrich` | `prep_push_rep_enrich [id] [filepath] [SIZE_MB/SIZE_GB/COUNT val] [device <id_or_name>] [rehash] [tempdir <path>] [--extras "<f1>;<f2>"] [--extras-size <v>] [-tmdbid <id>] [--yes\|--no-rename] [--nfo] [--no-web]` | `cmd_prep_push_rep_enrich` (IMP-D22) — calls `cmd_prep_push_rep` **unmodified**, then TMDB-enriches the just-archived movie; `-tmdbid` presets the id via `cmd_set_tmdb` (no title search), no id ⇒ the same resolve waterfall `enrich_metadata` uses; `-tvdbid` is REFUSED before anything runs (§6.3a); the `{tmdb-…}` folder rename is confirmation-gated (`--yes` = auto-confirm, `--no-rename` = auto-decline, non-interactive ⇒ do not rename) |
| `prep_push_rep_season_enrich` | `prep_push_rep_season_enrich [id] [folder] [SIZE_MB/SIZE_GB/COUNT val] [episodes <range>] [device <id_or_name>] [rehash] [tempdir <path>] [--extras "<f1>;<f2>"] [--extras-size <v>] [-tmdbid <id>] [--yes\|--no-rename] [--nfo] [--no-web]` | `cmd_prep_push_rep_season_enrich` (IMP-D22) — same, over `cmd_prep_push_rep_season` **unmodified**; enrich runs only once every id THIS RUN targeted (range-scoped when `episodes` is given) reads `status == "archived"`, and is scoped by the season's OWN `base_id`, so `-tmdbid` is the primary per-season mechanism (§6.3a) |
| `fetch_restore` | `fetch_restore [id] [OPT: episodes <range>] [tempdir <path>] [--fetchExtras] [--manual]` | `cmd_fetch_restore` (`main.py:10461`) — dispatch fetch then restore; with `tempdir` the downloads land in `<tempdir>\<safe-id>\restore\` (fetch) and restore reads chunks from there; `--fetchExtras` also fetches the title's cloud-resident extras (flag-only, no prompt); `--manual` is forwarded to the fetch only when given (IMP-C25) |
| `set_search` | `set_search [id] [term]` | `cmd_set_search` |
| `set_poster` | `set_poster [id] [url]` | `cmd_set_poster` |
| `set_fanart` | `set_fanart [id] [url]` | `cmd_set_fanart` |
| `set_uploaded` | `set_uploaded [id]` | `cmd_set_uploaded` — force `onboarded` (multi-part rescue) |
| `scan_unprepped` | `scan_unprepped` | `cmd_scan_unprepped` — find video files on disk not in any library |
| `check` | `check [id]` | `cmd_check` — re-hash file and compare to library entry |
| `local_status` | `local_status [limit_size]` | `cmd_local_status` — show pending uploads + greedy bin-packing into `limit_size` |
| `push` | `push [id] [SIZE_MB/SIZE_GB/COUNT val] [chunks 1-4] [device <id_or_name>] [rehash] [tempdir <path>] [--extras "<f1>;<f2>"] [--extras-size <v>]` | `cmd_push` (`rehash` = eager canonical re-hash; `tempdir` redirects `_parts/` off-volume); `--extras` also runs the extras **upload** phase — on an already-archived main it pushes ONLY the extras (`main.py:18262`) |
| `push_group` | `push_group [id] [SIZE_..] [episodes 1-3] [device <id_or_name>] [rehash] [tempdir <path>] [--extras "<f1>;<f2>"] [--extras-size <v>]` | `cmd_push_group`; `--extras` uploads the season's registered extras after the episode loop |
| `replace` | `replace [id]` | `cmd_replace` — swap original with a tiny valid video placeholder generated by ffmpeg (`make_video_dummy`); an unknown id now prints `❌ Error: '<id>' not found in library.` before returning `False` (IMP-C14) |
| `replace_group` | `replace_group [id]` | `cmd_replace_group` |
| `repair_dummies` | `repair_dummies [optional: id_prefix]` | `cmd_repair_dummies` — walk all `status=="archived"` entries and upgrade legacy text-blob dummies to valid video dummies |
| `verify_restore` | `verify_restore [id] [tempdir <path>]` | `cmd_verify_restore` (`main.py:8233`) — dry-run hash check of files in `restore/`; `tempdir` checks `<tempdir>\<safe-id>\restore\` (the same `_parts_base` rule as `restore`); no id prints its usage (IMP-C25 Step 11 h) |
| `restore` | `restore [id] [tempdir <path>]` | `cmd_restore` — re-merge chunks + verify + move into place; `tempdir` reads chunks/holder from `<tempdir>/<safe-id>/restore` while the merged output stays on the media volume |
| `restore_group` | `restore_group [id]` | `cmd_restore_group` |
| `sort` | `sort` | `cmd_sort` — re-order JSONs by lang -> year -> size |
| `web` | `web [--port N] [--host H] [--no-browser]` | `cmd_web` — launch the local FastAPI operations console (Disk Reclaim view) at `http://127.0.0.1:8765`; lazy-imports fastapi/uvicorn so importing `main` never hard-requires them (IMP-E12) |
| `fetch` | `fetch [id] [OPT: episodes <range>] [tempdir <path>] [--fetchExtras] [--manual]` | `cmd_dispatch_fetch` (`main.py:10397`) — spawns `python mainfetch.py fetch ...` (UTF-8 + unbuffered child env, `_mainfetch_child_env`), forwarding `--fetchExtras`, `tempdir` and `--manual` (only when given); after the child exits it folds the learned sidecar into the library (`merge_learned_gphotos`, `main.py:10347`) |
| `fetch_cleanup` | `fetch_cleanup [--apply]` | `cmd_fetch_cleanup` (`main.py:10510`, rules in `mainfetch.fetch_cleanup_plan` / `fetch_cleanup_apply`) — one-time cleanup of fetch leftovers: files in `~/Downloads` named like a library object's upload (from fetches before IMP-C25), files in marked `.mv_incoming` folders, unfinished fetch journals. Dry run lists them and writes nothing; `--apply` deletes exactly that list under the fetch lock, each file re-checked first |
| `map_photos` | `map_photos [all\|<id>\|<prefix>] [--account movies\|tv\|anime\|others] [--devices] [--devices-only] [--resolutions <csv>] [--repair split-info\|names] [--accept-probable] [--apply] [--out <dir>]` | `cmd_map_photos` (`main.py:12830`) — backfill: maps every uploaded object to its item in the crawler's inventory (offline; no Chrome), dry run by default (run dir only); `--apply` records learned facts and runs the apply engine. Phones only with `--devices` (§8.10) |
| `check_photos` | `check_photos <id \| season id \| series:<season id\|show folder> \| id prefix \| account:<a> \| pending \| hinted \| all> [--approaches best\|all] [--account a] [--recheck] [--apply \| --apply-run <run dir\|latest>] [--limit N] [--pace S] [--max-minutes M] [--min-age-h H] [--devices] [--list]` | `cmd_check_photos` (`main.py:14263`) — download-free verification in Google Photos (spawns `python mainfetch.py check --run <dir>`); dry run by default; `pending --list` is offline; `--apply-run` applies a reviewed run without Chrome; `--min-age-h H` looks up only objects pushed at least H hours ago (the auto-confirm passes 24) (§8.10) |
| `resolve_photos` | `resolve_photos [failed\|<csv>] [--account a] [--id <manual_id>]` | `cmd_resolve_photos` (`main.py:14834`) — interactive (terminal only) walk of the failed list: paste the right item's URL, give a search term, or mark "not uploaded"; library changes only after a final `Apply these N changes to the library? [y/N]` (§8.10) |
| `mvmeta_upgrade` | `mvmeta_upgrade [all\|<id>\|<prefix>] [device <alias\|serial>] [--apply]` | `cmd_mvmeta_upgrade` (`main.py:15720`) — brings the phones' existing `.mvmeta.json` sidecars to v2 with proven facts only; dry run by default; `--apply` keeps every original locally first (§6.5) |
| `recover` | `recover [id\|folder]` / `recover --scan` | `cmd_recover` — finish an interrupted rollback (calls `recover_journal`); `--scan` lists leftover journals read-only |
| `set_tmdb` | `set_tmdb [id] [tmdb_id]` | `cmd_set_tmdb` — sets the OPTIONAL additive leaf field `metadata.tmdb_id`; zero-byte (no rehash); alias-safe |
| `rename_folder` | `rename_folder [old_id_or_path] "<new_name>"` | `cmd_rename_folder` — crash-safe cascading folder rename: renames the on-disk dir + atomically rewrites `folder_path` for every descendant (season_map + leaves; `multi_ep_alias` skipped); uses the existing `RollbackJournal` (journal in parent dir; `os.rename` = PONR) — additive, does NOT change the rollback contract (journal format/PONR/`RollbackHardFail` unchanged); hash-safe (moves a dir + rewrites JSON; no rehash; `uid`/`.sha256` sidecars move with the folder); works on archived dummies (cross-ref §12a) |
| `migrate_provider_tokens` | `migrate_provider_tokens [id_or_prefix] [--apply] [--library movies\|series\|anime\|others]` | `cmd_migrate_provider_tokens` (IMP-U6) — ancestor-aware, deepest-first rename of every folder still carrying an old-style tmdb token (`[tmdbid-…]`/`[tmdb-…]`/`[tmdbid=…]`/wrong casing) to the canonical `{tmdb-<id>}`, driving each rename through the unmodified `cmd_rename_folder` (rollback contract untouched); dry-run by default, provably read-only; `--apply` writes a JSON audit report under `<LOCAL_ROOT>/migration_reports/`; a mistyped `--library` is a hard refusal, not a silent whole-library widen |
| `normalize_season_folders` | `normalize_season_folders [id_or_prefix] [--apply]` | `cmd_normalize_season_folders` (IMP-U6) — two-phase STRUCTURAL rename of series/anime `season_map` entries: **Phase A** stamps the show folder with `{tmdb-<id>}` (stripping any stale non-tmdb token so exactly one remains), **Phase B** renames each season folder to `<Show Name> Season <NN> (<season air year>)` with **NO id**; gated by the two-half category-folder guard (`_season_parent_is_exclusive_to_show`); dry-run by default; `--apply` backfills `metadata.tmdb_id` on every season in a group, renames via the unmodified `cmd_rename_folder`, and writes a JSON report under `<LOCAL_ROOT>/migration_reports/`; no `--library` filter (series/anime only by construction) |
| `add_extras` | `add_extras <title_id> "<f1>;<f2>" [--extras-size <v\|none>] [device <id_or_name>] [no-replace]` | `cmd_add_extras` (IMP-D19) — one-shot extras lifecycle on an EXISTING/archived/local-only title: scan+merge → push → dummy, **never touching main content**; the folder list is POSITIONAL (semicolon-separated, not a `--extras` flag); `no-replace` = upload-only |
| `enrich_metadata` | `enrich_metadata [id_or_prefix] [--apply] [--library X] [--nfo] [--no-web]` | `cmd_enrich_metadata` — local-first TMDB backfill (show-centric): resolves each show/movie once, writes `metadata.tmdb_id` + `metadata.title`/`year`/`overview`/`episode_title`, stamps the `{tmdb-…}` token once per show via `rename_folder` (seasons inherit), downloads show + per-season `poster.jpg`/`fanart.jpg` — NEVER overwriting a local image, NEVER fetching media, cached, dry-run default; `--nfo` writes Kodi/Jellyfin `movie.nfo`/`tvshow.nfo`; `--no-web` disables EXA auto-resolve (IMP-E16: on TMDB API miss → EXA searches themoviedb.org → validates candidate id; resolves hard/regional titles without manual pinning); ambiguous → listed not guessed |
| `refresh_online` | `refresh_online [id_or_prefix] [--force] [--library X]` | `cmd_refresh_online` (IMP-E16) — bulk OMDb ratings fetch for entries that have a `tmdb_id`; writes gitignored `mvonline.json` keyed by `tmdb_id`; requires `omdb.api_key` in `mvconfig.json`; `--force` re-fetches even if cached |
| `fetch_trivia` | `fetch_trivia [id_or_prefix] [--force] [--library X]` | `cmd_fetch_trivia` (IMP-E16) — EXA web-search → GROQ-distilled `[source]`-tagged trivia facts; writes gitignored `mvextra.json`; requires `exa.api_key` + `groq.api_key` in `mvconfig.json`; `--force` re-fetches even if cached |

> **Auto-confirm after a push (IMP-C25 Step 13b, P-19).** The six push-family branches of the CLI
> dispatcher (`push` incl. its extras-only path, `push_group`, `prep_push_rep`, `prep_push_rep_season`,
> `prep_push_rep_enrich`, `prep_push_rep_season_enrich`) keep their command's return value and then call
> `after_push_command(<verb>, result, <id>[, ep_range])` (`main.py:15091`). It runs only when the command
> RETURNED successfully — `False`, any exception (`RollbackHardFail`, `SystemExit`, Ctrl+C) or a usage error
> gets nothing; for the three commands that return `None` on success and on handled failures
> (`push_group`, `prep_push_rep`, `prep_push_rep_season`) success is read from the end state in the saved
> library (entry archived / every target archived / every target uploaded). Then `maybe_auto_confirm()`
> (`main.py:14973`): config `auto_confirm_after_push: false` → silent; nothing pending → silent; a web job
> (`MEDIAVAULT_WEB_JOB` set), only pushes younger than `AUTO_CONFIRM_MIN_AGE_H = 24` h, no Chrome profile for
> any due account, or the fetch lock held → the one reminder line
> `🔎 N pushed object(s) await Google Photos confirmation — run: main.py check_photos pending --apply` (N = every
> pending object); otherwise one header line — N = the due objects only, `, M newer skipped` for the younger ones —
> and the child `python main.py check_photos pending --apply --min-age-h 24 --max-minutes 15` (streamed), which looks
> up only the due objects: never the push that just ran (IMP-C25 follow-up, 2026-10-27 — until then the child checked
> every pending object). A failing child or any error → one `⚠️  Auto-confirm did not finish (…)` line. It never
> raises, so the push's own output and exit code never change. No `cmd_*` body is touched (rollback-neutral,
> §12a).

> **`episodes` keyword is a required literal trigger.** For `fetch`,
> `fetch_restore`, and `prep_push_rep_season`, the word `episodes` must
> appear as its own argument immediately before the range value.  
> ✅ `fetch tv-TheBoys episodes 1-3` → `epr = "1-3"`  
> ❌ `fetch tv-TheBoys 1-3` → `epr` stays `None`, range silently ignored  
> Parsing: `fetch`/`fetch_restore` scan their tokens for `episodes <range>` and
> `tempdir <path>` in any position after the id (`main.py:18425` / `:18447`);
> `prep_push_rep_season` uses a token-scanner loop (`main.py:17809`) so the keyword
> can appear anywhere after the folder path.

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
> auto-pilot can make this call (`main.py:10503`).

> **CLI parsing seams (IMP-C14).** `push_group`'s argv parsing is now an
> extracted pure function `main.parse_push_group_args(args)` →
> `(group_id, method, val, ep_range, dev, eager, tdir)`. It mirrors the
> sibling `push` parser's fail-fast arms: a value-taking keyword
> (`SIZE_MB`/`SIZE_GB`/`COUNT`, `episodes`, `device`) with no following
> value now prints `❌ Error: Missing value for ...` and `sys.exit(1)`
> instead of spinning `while i < len(args)` forever (the prior trailing-
> value-keyword console hang). Unknown/typo'd tokens are still silently
> skipped, matching `push`. `mainfetch`'s bare-invoke parsing is likewise
> extracted into `mainfetch.parse_fetch_args(argv)` → `(mid, epr)` — since IMP-D19
> `(mid, epr, fetch_extras)` (Selenium-free, so it is unit-testable); see the
> subsection below.

> **Extras options — `--extras` / `--extras-size` / `--fetchExtras` (IMP-D19).**
> Bonus-content folders (`Specials\`, `Extra\`, `Trailers\`) get the same
> push → dummy → fetch → restore lifecycle as main content, stored in an OPTIONAL
> nested `extras` block on the TITLE entry (§6.3).
> - **`--extras` / `-extras "<f1>;<f2>"`** — semicolon-separated **and** repeatable
>   (`--extras A --extras B`); both forms are additive and flatten into one ordered
>   list (`parse_extras_tokens`, `main.py:7051`). Each command's own argv walker
>   forwards only the extras tokens to that parser, so the pre-existing tokens
>   (`SIZE_*`, `episodes`, `device`, `rehash`, `tempdir`) parse unchanged.
> - **Register vs upload.** `prep` / `prep_season` *register* the folders — a
>   recursive `VIDEO_EXTENSIONS` scan + SHA256 + MediaInfo, merged idempotently per
>   group (`_extras_scan_merge_and_save`, `main.py:5583`) — and never upload. On
>   `push` / `push_group` the flag is purely the opt-in switch for the extras
>   **upload** phase over the extras **already registered** on that title; those two
>   do NOT re-scan the given paths, so register first with `prep --extras` — or use
>   `add_extras`, which scans, pushes and dummies in one shot. The two autopilots
>   `prep_push_rep` / `prep_push_rep_season` are one-shot: they FORWARD `--extras`
>   into their prep leg (`cmd_prep` / `cmd_prep_season`, `main.py:9253` / `:9318`), so
>   the same run registers, uploads AND dummies — no separate `prep --extras` needed
>   on a fresh title. Their prep leg keeps `cmd_prep`'s cloud-bearing early-skip, so
>   on an already-pushed/archived title nothing is registered (`add_extras` is the
>   command for that case).
> - **`--extras-size` / `-extras-size`** — independent extras chunking: `none`
>   (whole-file), a size with unit (`9900mb`, `8gb`), or the triplet
>   `SIZE_MB|SIZE_GB|COUNT <val>`. Omitted ⇒ **inherit the command's main split**;
>   no main split either ⇒ whole-file (`push_title_extras`, `main.py:6341`).
> - **`--fetchExtras`** (aliases `--fetch-extras`, `--extras`, `--extra`) on `fetch` /
>   `fetch_restore` is a boolean flag with **NO prompt anywhere**; absent ⇒ no extras
>   are queried and the spawned argv is byte-for-byte the pre-IMP-D19 one. An
>   `episodes <range>` filters only episodes — extras are all-or-nothing. The flag
>   gates the **fetching**: extras already staged in a `restore/` folder are placed
>   back by ANY successful `restore` / `restore_group` / `fetch_restore`
>   (`restore_title_extras` is wired unconditionally on those success paths), which is
>   what lets an interrupted run heal itself on the next call.
> - **`replace` / `replace_group`** likewise dummy the title's uploaded extras
>   unconditionally (no flag) — `replace_title_extras` is a silent no-op when the
>   title carries no extras or none are pending.

### `python mainfetch.py fetch <id> [episodes <range>] [tempdir <path>] [--fetchExtras] [--manual]`

`mainfetch.cli(argv)` (`mainfetch.py:6610`) is the script's entry; it dispatches three verbs:

| Verb | Parser | Runs |
|---|---|---|
| `fetch <id> [episodes <range>] [tempdir <path>] [--fetchExtras] [--manual]` | `parse_fetch_args` (`:5573`, the pinned 4-tuple `(mid, epr, fetch_extras, temp_dir)`) + `parse_fetch_options` (`:5609`, `{"manual": bool}`) | `cmd_fetch_route` (`:5435`) |
| `locate <id> [episodes <range>] [--fetchExtras] [--probe-direct]` | `parse_locate_args` (`:5620`) | `cmd_locate` (`:5695`) — READ-ONLY diagnostic (§8.4) |
| `check --run <run dir> [--limit N] [--max-minutes M] [--pace S]` | `parse_check_args` (`:5851`) | `cmd_check` (`:6548`) — the browser half of `main.py check_photos`; not meant to be run by hand |

Run as a script, the module registers itself as `mainfetch` in `sys.modules` (`:6634`) so the lazy
`import mainfetch` inside `gprpc` / `tools.gp_inventory` never loads a second copy (whose
`SessionExpiredError` the script would not catch).

Profile selection is data-driven via `profile_for_id(manual_id)` (IMP-C16; IMP-C26 overrides first) and,
since IMP-C25, per OBJECT (§8.1).

The fetch argv parsing (`mainfetch.parse_fetch_args(argv)`) requires `fetch` as `argv[1]` and the ID at
`argv[2]`; `episodes <range>`, `tempdir <path>` and the flags may follow in any order. The guard
`len(argv) < 3 or argv[1] != "fetch"` prints
`Usage: fetch [id] [episodes] [range] [tempdir <path>] [--fetchExtras] [--manual]` and `sys.exit(1)`.
`main.py:cmd_dispatch_fetch` (`main.py:10397`) constructs exactly that argv shape (`--manual` only when the
user gave it — the web UI never does).

`parse_fetch_args` also accepts the IMP-D19 boolean `--fetchExtras` (aliases
`--fetch-extras`/`--extras`/`--extra`) anywhere after the id. When set, `cmd_fetch_route` ALSO queues the title's
cloud-resident extras: `resolve_title_extras` → `build_extras_entries`
(`mainfetch.py:5135`) flattens each `uploaded=True` extras item into a synthetic
leaf-shaped entry whose `folder_path` is the extra's OWN on-disk folder (since IMP-C25 it also forwards the
item's `gphotos` block, `manual_id` = the title id, `group_rel`, `sub_rel`, label and `tech_spec`), so the
unchanged `fetch_single_entry` stages it into
`<title folder_path>/<group_rel>/restore/` — exactly where the extras restore reads
from. `mainfetch` stays flag-driven and non-interactive.

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
  is unchanged. **Phase 5 additions:** `poster_available` is a live
  `resolve_artwork_path` check (not a stored field); `tmdb_id` is read from
  `metadata.tmdb_id` (OPTIONAL additive leaf field set by `set_tmdb` /
  `enrich_metadata`); `title` now returns the real `metadata.title` (set by
  `enrich_metadata`) rather than the humanized id slug — so the SPA shows real
  titles and real posters (gradient fallback when neither exists).
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
  and a **cursor-following card glow** (mouse/trackpad; enabled whenever
  `any-pointer: fine` is available; animates regardless of `prefers-reduced-motion`
  — a deliberate user-requested effect). Posters and mobile/Tailscale/auth are
  tracked in later phases (E14 Phases 4–5 and IMP-E3/U3).
- **CSS motion layer — no build step (IMP-E14 Phase 3):** the SPA gained a
  **continuous hover border**: a rotating conic-gradient accent arc (`.card::after`,
  `@property --ring-angle`) travels around each card on hover, complementing the
  existing cursor-follow glow and yielding to the SVG fetch-progress ring.
  An `@supports`-gated `mask` clip gives a clean arc on supporting browsers; iOS
  Safari (which mis-renders mask+conic) falls back to a box-shadow ring instead.
  Both the glow and the rotating border gate on `(any-hover: hover) and (any-pointer: fine)`
  and animate regardless of `prefers-reduced-motion` (other components — modals,
  the space background — retain their reduced-motion fallbacks) — all pure CSS, no
  JS, no build pipeline change.
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
  - `GET /api/media-image/{id}?kind=poster|fanart` — **season-inheritance artwork
    resolver** (`resolve_artwork_path`): look in the entry's own `folder_path` for
    `poster.jpg` / `fanart.jpg`; if absent, check the `season_map`'s
    `folder_path`; if still absent, walk UP the directory tree to the nearest
    tmdb-tokened (`{tmdb-…}`) show folder. LOCAL copy ALWAYS wins over TMDB-stamped path. Security:
    only `poster.jpg`/`fanart.jpg`, `realpath`-contained under `LOCAL_ROOT`, derived
    from the library entry (no traversal); gated by the `/api/*` token guard. Used
    by the SPA to display real posters on media-type cards; a gradient is the
    fallback when no artwork exists.
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
- **mvconfig.json minimal config (IMP-E15 / IMP-A5 first slice):** `mvcommon.py`
  loads `mvconfig.json` at startup (gitignored; `mvconfig.example.json` is
  checked in). The schema is `web.host`, `web.port`, and `tmdb.api_key`. There is
  **no `web.token`** — auth is no longer a static config key (see below). An
  absent or malformed file silently falls back to defaults (`127.0.0.1:8765`).
  Full config migration of all constants remains in IMP-A5 (pending).
- **Admin-minted, expiring, revocable token auth on `/api/*` (IMP-E15):** access
  tokens are NOT a static config value. They live in a gitignored `mvtokens.json`
  (separate from `mvconfig.json`), stored as sha256 hashes — the raw token is
  shown to the owner exactly once at mint time and never persisted. Each record:
  `{id, label, hash, created_at, expires_at}` (null expires = never). The store is
  read fresh per request (no cache). Minting/listing/revoking is owner-only, two
  ways: the CLI (`python main.py token create [--label X]
  [--ttl 1h|8h|12h|1d|3d|7d|30d|never]`, `token list`, `token revoke <id>`) and
  the localhost-only **"Access" panel** in the web UI (mint with label + TTL,
  see live tokens with countdowns, copy a share link, revoke). Every `/api/*`
  request (except `GET /api/whoami`) is allowed iff **either** the request is the
  **genuine local admin** OR it presents a **valid non-expired minted token** via
  `mv_token` cookie, `X-MediaVault-Token` header, or `?token=` query.
  - **Genuine-local-admin detection — the security hinge:** a request is admin iff
    `request.client.host` is loopback (127.0.0.1 / ::1 / localhost) AND none of
    the proxy/identity headers (`x-forwarded-*`, `tailscale-user-*`, `forwarded`)
    are present. `tailscale serve` proxies remote tailnet peers to 127.0.0.1 but
    injects those headers, so a proxied peer is never mistaken for the owner.
    `GET /api/whoami` (unauthenticated) returns `{is_admin, authed}` so the SPA
    shows the Access panel only to the owner and a token prompt to remote devices.
  - **Secure-by-default — empty store locks remote:** auth is ALWAYS enforced.
    With no tokens minted, the genuine-local admin still has full, token-free
    access (frictionless local/dev), but every remote request gets 401 — remote is
    locked until the owner mints and shares a token.
  - **`POST /api/open-folder` keeps its own genuine-local-admin guard** (403 for
    non-admin) — a valid minted token can never widen this to a remote peer.
  - **No startup guard removed:** `cmd_web` no longer refuses to start when bound
    non-localhost with no token; the always-enforce rule makes that guard
    unnecessary — remote is simply locked until a token is minted.
- **Card cursor-glow + rotating hover border (IMP-E14 Phase 3):** the glow
  (`glow.js`) and rotating conic hover border (`.card::after`) gate on
  `(any-hover: hover) and (any-pointer: fine)` — the `any-*` variants so a mouse
  on a touch-capable Windows desktop works (a pure-touch phone still gets no
  effect). Both animate **regardless of `prefers-reduced-motion`** (a deliberate
  user-requested effect; reduced-motion fallbacks for other components — modals,
  the space background — are kept). Pinned by `tests/test_web_glow_motion_guard.py`.
- **Remote access model (IMP-E15):** the app can bind `0.0.0.0` to be reachable
  on the LAN IP and the Tailscale IP over plain HTTP. For an encrypted HTTPS
  tailnet URL, run `tailscale serve` in front (see
  `tools/tailscale_serve_setup.ps1` and `docs/feature-web-media-ui/REMOTE_ACCESS.md`).
  The owner mints a token (Access panel or `python main.py token create`) and
  shares the `?token=<raw>` link; the device opens it once — `auth.js` captures
  the raw token from `?token=`, stores it in a `mv_token` cookie + `sessionStorage`,
  strips it from the URL, and sends it as `X-MediaVault-Token` on every subsequent
  fetch. A 401 response re-prompts. The local browser is auto-opened at the plain
  `127.0.0.1` URL (no `?token=` needed — the genuine-local admin never needs a
  token). One-time Tailscale admin setup: enable MagicDNS and HTTPS in the
  Tailscale admin console.
- **Cinematic hover/long-press dossier — IMP-E16 (`preview.js`):** resting a
  pointer (desktop) or long-pressing (touch) on any card opens a large
  translucent glass "dossier" overlay — backdrop hero image, real title, year,
  episode info, synopsis, rating, runtime, genres, tagline, top cast,
  directors/creators, status, IMDb/TMDb links, and merged OMDb
  (IMDb/RT/Metacritic) scores. **Invariant:** `pointer-events` are gated on the
  `.is-open` class — a closed dossier is inert and never traps hover or click
  events on the card beneath it. Lazy-fetches `GET /api/detail/{id}`.
- **`GET /api/detail/{id}` (IMP-E16):** cached, `/api/*`-auth-gated, read-only
  endpoint. Returns full TMDB detail (rating/votes, runtime, genres, tagline,
  full overview, top cast, directors/creators, status, IMDb+TMDb links; TV
  seasons/episodes/networks; episode title/air-date/S·E) PLUS merged OMDb
  ratings (`rated`, IMDb/RT/Metacritic), `awards`, `boxoffice` (read from
  gitignored `mvonline.json` keyed by `tmdb_id`) PLUS `trivia`
  (`[{text, source}]` read from gitignored `mvextra.json`). **Cache-read only**
  — the request path makes no live API calls; all data is pre-populated by
  `refresh_online` / `fetch_trivia` / `enrich_metadata`.
- **New commands — IMP-E16:**
  - `refresh_online [id_or_prefix] [--force] [--library X]` — bulk OMDb ratings
    fetch, writes gitignored `mvonline.json` keyed by `tmdb_id`.
  - `fetch_trivia [id_or_prefix] [--force] [--library X]` — EXA web-search →
    GROQ-distilled `[source]`-tagged trivia facts, writes gitignored `mvextra.json`.
- **EXA auto-resolve waterfall in `enrich_metadata` (IMP-E16):** on TMDB API
  miss → EXA searches `themoviedb.org` → extracts a candidate TMDB id →
  validates with a by-id TMDB fetch. `--no-web` disables the web leg. Resolves
  hard/regional/concatenated titles without manual `set_tmdb` pinning.
- **`items_payload` additions (IMP-E16):** each item now also carries
  `overview`, `episode_title`, `backdrop_available`, `actual_size_bytes`, `tech`
  (resolution/codec/audio chips), and `release_name`.
- **Archived tile tech chips (IMP-E16):** cards display the real fetched size
  (`tech_spec.size_bytes`) plus print/tech chips — resolution, Dolby-Vision
  profile, REMUX, IMAX, codec, audio format.
- **Grouped GRID / drill-down view (IMP-E16):** a List|Grid toggle (persisted)
  switches between the flat card grid and a hierarchical drill-down view
  (language/folder boxes → Show → Seasons → Episodes) with breadcrumb + Back
  navigation; state-prune + sort + leaf-card reuse carry over.
- **Poster-driven ambient glow (`swatch.js`, IMP-E16):** each card's glow,
  border, and scrim tint to its poster's dominant color (mint fallback when no
  poster is available).
- **⌘K / Ctrl-K command palette (`palette.js`, IMP-E16):** fuzzy-jump to any
  title + run global actions.
- **View-Transitions morphs (IMP-E16):** `document.startViewTransition` scoped
  to `#panel` on tab/filter/view switches; graceful no-op fallback for browsers
  that do not support the API.
- **Cinematic parallax hero (`hero.js`, IMP-E16):** per-tab backdrop band of
  archived/featured titles with Ken-Burns animation, scroll parallax,
  auto-rotate, and click-jump; `prefers-reduced-motion` static fallback.
- **Lazy-load perf (IMP-E16):** admin, palette, and terminal modules are
  lazy-loaded on demand; the critical-path modules are
  `<link rel="modulepreload">`-preloaded.
- **Config extensions (IMP-E16):** `mvconfig.json` (gitignored) extended with
  `omdb.api_key`, `exa.api_key`, `groq.api_key`, `rapidapi.*`. New gitignored
  caches: `mvonline.json`, `mvextra.json`, `~/.mediavault/cache/exa/`.

---

## 6. Data Model and Library State

### 6.1 The four library JSON files

The shared path constants live in `mvcommon.py` (imported by both `main.py`
and `mainfetch.py` as of IMP-A1):

```python
LIBRARY_MOVIES = r'C:\Media\library_movies.json'
LIBRARY_SERIES = r'C:\Media\library_series.json'
LIBRARY_ANIME  = r'C:\Media\library_anime.json'
LIBRARY_OTHERS = r'C:\Media\library_others.json'   # IMP-D18 "Others" category
```

`load_library()` reads all four and merges into a single dict in memory
keyed by manual ID. `save_library()` splits back by ID prefix:

- key starts with `mov`  -> movies file
- key starts with `tv`   -> series file
- key starts with `ani`  -> anime file
- key starts with `oth`  -> others file (IMP-D18)
- otherwise -> fallback to movies file (legacy keys without a prefix), and the
  unrouted key is **warned to stderr** so a future unknown/typo'd prefix can
  never vanish silently (the hardened `else→movies` trap).

This split-by-prefix lives in `mvcommon.save_library()`. `load_library()`
`os.path.exists`-guards each file, and `save_library()` always writes all four
(even an empty `library_others.json`), so the Others file is created on the
first `oth-` save and is never a special case.

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
- Others (sports): `oth-<sport>-<year>-<competition>-s01e<NN>` e.g.
  `oth-football-2026-fifaworldcup-s01e01` (IMP-D18). Sport and competition are
  spelled out (`football`, `cricket`). A tournament edition is modeled as ONE TV
  season — it **reuses `season_map` + leaf, adding no `ENTRY_TYPE_KEYS` type**:
  each match-half is one episode and a match is two adjacent episodes
  (`e01`+`e02` = the first match). `cmd_prep_season` position-numbers `oth-`
  files `e01..eNN` by **filename sort order** (a dedicated `is_other` branch that
  bypasses the SxxExx / anime episode regex, because Tivimate-derived sports
  filenames carry no episode marker). On-disk naming convention: name the
  halves/periods so they sort in play order (`First`<`Second`, or `1`<`2`, or
  `Q1`..`Q4`). The `-s01` segment is REQUIRED so `mvcommon.episode_num_from_id`
  strips a clean `-s01` and reads `e01`→1.0 (a bare `oth-…-e01` would strip to
  `-e01`, which the anchored `^[eExX]?(\d+…)$` regex rejects → silent 0-match).
  Because the id parses cleanly, every push/replace/restore/season command — and
  its rollback journaling — operates on `oth-` ids verbatim: **no
  rollback-contract change**.
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
parents, and multi-episode aliases. OPTIONAL *nested blocks* (`metadata`,
`tech_spec`, `split_info`, the IMP-D19 `extras` block and the IMP-C25 `gphotos`
identity block, both documented at the end of this section) add no entry type and
appear in no `required` set.

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
    "title":      "<the manual ID>",              // real title once enrich_metadata runs; raw id slug before
    "year":       2025,
    "genre":      [],
    "added_date": "2026-01-02",
    "tmdb_id":    12345                           // OPTIONAL — set by set_tmdb / enrich_metadata; zero-byte (no rehash); alias-safe
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
    "carried_out_tracks": [                    // OPTIONAL — FLAC carry-out (feature/flac-carryout)
      {
        "track_id": 2, "codec": "A_FLAC", "language": "eng", "channels": 8,
        "position": 2, "default": false, "forced": false, "enabled": true, "name": null,
        "holder_filename": "F1... [f6b674].holder.mkv", "holder_hash": "…",
        "original_tracks": [ ... ordered per-track manifest (type/codec_id/language/
                               default/forced/enabled/name/uid), the generic re-add source ... ]
      }
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
`collect_reclaimable` (the `web` console's reclaim scan — IMP-E12/D16). Since
IMP-D19 the two disk-walking consumers (`scan_unprepped`, `collect_reclaimable`)
read a season_map's optional `extras` block *before* that type skip — the block is
the one thing a virtual entry can own on disk (see below).

> **`ENTRY_TYPE_KEYS` is the authoritative source of truth for entry-type
> key shapes.** `main.py` (top-level config block, `main.py:235`; its comment `main.py:130-234`) defines a
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

#### Optional `extras` block on a title entry (IMP-D19)

`extras` is an **optional nested block** — exactly like `split_info` — carrying a
title's bonus content (a `Specials\` / `Extra\` / `Trailers\` folder) so it runs the
same push → dummy → fetch → restore lifecycle as main content. It nests on the
**TITLE** entry: the `season_map` for a series/anime/others title, the movie **leaf**
for a movie. It adds **no new entry type**, is in no `required` set, and round-trips
through `save_library`/`load_library` untouched. Authoritative spec: the
`ENTRY_TYPE_KEYS` comment (`main.py:130-234`) plus the in-code `EXTRAS DATA LAYER`
section (`main.py:5243`); the locked shape is decision A2 in
`docs/feature-extras/DECISIONS.md`.

```jsonc
"tv-en-2016-strangerthings-s01": {
  "type":        "season_map",
  "folder_path": "C:\\Media\\Series\\...\\Stranger Things Season 01 (2016)",
  "children":    [ ... ],
  "extras": {
    "groups": {
      "Specials": {                      // group_rel — the extra folder's path RELATIVE to folder_path
        "added_date": "2026-06-29",      //             (e.g. "Specials", "Extra", nested "Bonus/Trailers")
        "items": [
          {
            "filename":    "Behind.The.Scenes.mkv",
            "sub_rel":     "Behind.The.Scenes.mkv",  // path WITHIN the group (nested: "Reels/Clip.mkv")
            "short_id":    "9c3f01",                 // generate_short_id("<title_id>::<group_rel>/<sub_rel>")
            "hash":        "sha256...",              // canonical hash (blessed merged hash once split+restored)
            "status":      "local_ready",            // -> onboarded -> archived -> restored_local
            "uploaded":    false,
            "search_term": "Behind.The.Scenes [9c3f01].mkv",
            "tech_spec":   { ... },                  // same get_tech_specs dict as a leaf
            "split_info":  { ... },                  // OPTIONAL — written by push_one_extra when chunked
            "re_hashed":   true                      // OPTIONAL — first split restore blessed the merged hash
          }
        ]
      }
    }
  }
}
```

- **Identity and paths.** An item's on-disk (and restore) path is
  `<title folder_path>/<group_rel>/<sub_rel>`, composed by `_extras_item_paths`
  (`main.py:5276`) — the single seam every consumer uses, so nothing derives that path
  independently. Both keys are stored forward-slashed (`_extras_rel_posix`) and are
  **relative**, so extras are rename-safe: `rename_folder` rewrites only `folder_path`
  and needs no extras change (covered by `tests/test_extras.py`). The converse is a real
  hazard: renaming a **group** folder is NOT tracked anywhere — the stored `group_rel`
  still names the old folder, so a restore would recreate it and the renamed files would
  read as unknown to `scan_unprepped`. Rename a bonus folder BEFORE registering it (the
  media-server naming rules that make this matter are in
  `improvements/JELLYFIN_SETUP_GUIDE.md` §3.6).
- **Grouped per source folder.** One `--extras` folder = one group; the `"groups"`
  wrapper keeps an arbitrary folder name from ever colliding with a reserved key. Each
  group is scanned recursively for `VIDEO_EXTENSIONS`, skipping `_parts` /
  `checksums` / `restore` / `.git` / `.idea` / `__pycache__` (`scan_extras_folders`,
  `main.py:5324`). A folder that is not under the title folder falls back to its
  basename as `group_rel`.
- **Additive / idempotent de-dup** is per group by normalized `sub_rel`
  (`merge_extras_into_title`, `main.py:5460`): a new `sub_rel` is appended, an
  unchanged one (same hash) is a no-op, and a **changed-byte** one is refreshed and
  reset to the unblessed state (`uploaded=false`, `status="local_ready"`, stale
  `split_info`/`re_hashed` dropped). So adding `Specials` now and `Trailers` later
  equals adding both at once. **Layered dummy guard (Step 12c, D19-B1):** a
  changed-hash candidate that is dummy-sized (`< DUMMY_MAX_BYTES`) can NOT reset a
  cloud-bearing item (`uploaded` truthy or status onboarded/archived/restored_local —
  the extras analogue of `cmd_prep`'s early-skip), and `push_one_extra` refuses to
  upload ANY dummy-sized file, so re-scanning an archived group (re-run `add_extras`
  / `prep_season --extras` / the season autopilot) never pushes a dummy over the
  real cloud copy; a NEW dummy-sized file is skipped at registration with a warning.
- **Lifecycle mirrors a leaf's.** `status` walks `local_ready` → `onboarded` (pushed) →
  `archived` (dummied) → `restored_local` (fetched back); `uploaded` flips true at push
  and STAYS true, so a `restored_local` extra can be re-dummied later. Every item is
  pushed / dummied / restored individually through the same primitives main content
  uses (`push_one_extra` `main.py:6050`, `replace_one_extra` `main.py:7508`,
  `restore_one_extra` `main.py:8732`), with per-title drivers
  (`push_title_extras` / `replace_title_extras` / `restore_title_extras`) that never
  raise and are silent no-ops for an extras-less title. One bad extra never blocks the
  rest.
- **Independent chunking.** `--extras-size` wins (`none` ⇒ whole-file); omitted ⇒
  inherit the command's main split; no main split ⇒ whole-file. A split item's
  `split_info` uses the leaf vocabulary (`is_split`/`method`/`val`/`total_chunks`/
  `chunks[{filename,hash}]`) and gains `merge_seed`/`merge_tool`/`rehashed_at` +
  item-level `re_hashed` when the first restore blesses the deterministic merged hash —
  the §6.4a verify-or-bless policy applied per extra.
- **Cross-command consumers (IMP-D19 Step 7).** Extras ARE library content, so the
  whole-library iterators index them *before* the physical-leaf type skip:
  `cmd_scan_unprepped` never mis-flags a registered extra as UNPREPPED, and
  `collect_reclaimable` (the `web` console's Disk Reclaim view) classifies an extra by
  **its own item status** through the shared `classify_entry_state` — same badge set as
  main content — and emits `add_extras <title_id> "<group folder>"` as the suggested
  reclaim command. `_extras_item_paths` dereferences no leaf-only key (it reads only
  `extras` + `folder_path`, present on both title shapes), so it is safe on every entry
  type and yields nothing for a `multi_ep_alias`. `/api/items` (the media-type card
  grid) does not surface extras as separate cards.

#### Optional `gphotos` block on each uploaded object (IMP-C25)

`gphotos` is the **Google Photos identity of ONE uploaded object** (PLAN P-1). It nests on each
object a push actually uploads: the unsplit leaf itself, each `split_info.chunks[i]`, each FLAC holder
`split_info.carried_out_tracks[i]`, an unsplit extras item, and each `split_info.chunks[i]` of a split
extras item. Never on a split entry itself (P-3 — its chunks and holders were uploaded, not the whole
file); a `season_map` holds blocks only inside its `extras`; a `multi_ep_alias` holds none. Like `extras`
it adds no entry type, is in no `required` set and changes no `physical` flag; the whole-library read
commands ignore it (pinned by `tests/test_entry_schema_guard.py` section (d), which runs every reader
with and without blocks and requires identical output). Authoritative spec: the `ENTRY_TYPE_KEYS`
comment (`main.py:171-220`) and mvcommon's "GOOGLE PHOTOS IDENTITY" section (`new_gphotos_block`,
`block_trusted`, `iter_uploaded_objects`; key tuples `mvcommon._BLOCK_KEYS` / `_ACCOUNT_KEYS`).

```jsonc
"gphotos": {
  "v": 1,
  "sha256": "<hex64>",                  // PIN: must equal the owning object's CURRENT hash (P-2)
  "sha1": "<hex40>", "dedup_key": "<27 chars>",   // the content key Google Photos dedups on
  "size_bytes": 0, "width": 0, "height": 0, "duration_s": 0.0,
  "date_utc": "YYYY-MM-DDTHH:MM:SSZ",   // the file's Matroska DateUTC = the time Google Photos shows
  "uploaded_name": "<exact name pushed to the phone>",
  "pushed": {"at": "…Z", "device_id": "<serial>", "device_tz": "<tz>", "device_tz_offset_min": 0},
  "accounts": {                          // keyed by account: movies | tv | anime | others
    "<account>": {
      "photo_id": "<id>", "taken_ms": 0, "tz_offset_ms": 0, "upload_ms": 0, "dedup_key": "…",
      "gp_filename": "…", "width": 0, "height": 0,
      "verified_at": "…Z", "verified_by": "dedupkey|sha256|filename|manual",
      "source": "map|check|fetch|confirm|manual",
      "approach": {"best": "photo_id", "ok": [...], "failed": [...], "at": "…Z", "via": "rpc|ui"},
      "alternates": ["<other photo_id with the same name or bytes>"],
      "original_quality": true, "partial_upload": false,
      "confirmed": true                  // seen there with upload_ms AND a verified identity
    }
  },
  "home_account": "<account>",           // stored only when an account holds a verified / user-vouched copy
  "search_override": {"query": "<the user's own search text>", "at": "…Z"}
}
```

- **Only known facts are written** (never `null`); times are UTC `…Z` strings, or epoch ms in
  `taken_ms` / `upload_ms`.
- **Pin rule (P-2).** A block is trusted only while `gphotos.sha256` equals the owning object's current
  hash (leaf `hash` / chunk `hash` / `holder_hash` / extras item `hash`). An untrusted block (an extras
  item re-mastered after its push) is ignored by every reader and replaced by the apply engine. A re-split
  replaces `split_info` wholesale, so the old chunks' blocks go with it; their learned records become
  orphans (reported, never applied).
- **One writer (P-10).** Only the apply engine `main.gphotos_apply(records, capture=True, dry_run=True,
  names=None)` (`main.py:10240`) writes blocks: one short load → patch → save, guarded by a fingerprint
  (size, `mtime_ns`, SHA-256 of all four library files) taken before the load and re-checked right before
  the save; a change → reload and re-patch, at most `GPHOTOS_APPLY_ATTEMPTS = 3` tries, then
  `⚠️  Google Photos identity not applied: library changed by another process — re-run when idle.`. No lock,
  no rollback journal (OD-2 ruling: direct overwrite is fine when no other process writes). Everything else
  (mainfetch, the check child, the crawler) is library-read-only and appends to the learned sidecar.
- **Proof ladder (P-5).** `dedupkey` (the item's dedupKey equals our SHA-1 — content proof, no download) >
  `sha256` (the downloaded bytes matched) > `filename` (exact or P-6-normalized uploaded name) > `manual`
  (the user's word; never confirms by itself). A legacy object (archived before IMP-C25 — its local copy is a
  dummy) can only reach `filename`; new pushes reach `dedupkey`. A mapped cloud dedupKey is never turned into
  the block's own content key.
- **`confirmed`** is set only from sources `map | check | fetch | confirm` with a verified proof; never from
  the push-time capture alone (P-11). `archived` keeps meaning "pushed + replaced"; `confirmed` means "seen
  in Google Photos with that identity". No `status` value was added.
- **Never a download URL** (P-20): only the permanent `photo_id`; a fresh link is minted at every fetch.
- `map_photos --repair split-info` (IMP-C27 (a)) is the one other IMP-C25 writer: it can put a lost
  `split_info` (`{is_split, total_chunks, chunks: [{filename, hash}]}`, without `method`/`val`) back onto a
  leaf or extras item, through its own fingerprint-guarded writer.

### 6.3a TMDB enrichment conventions (Phase 5 — IMP-E3 / IMP-D17)

**TMDB-for-everything (no TVDB / AniDB client exists).** MediaVault resolves
**movies, series AND anime exclusively against TMDB**. There is no TVDB client,
no AniDB client, and no HTTP call to any provider other than TMDB (plus OMDb for
ratings and EXA/GROQ for the web fallback and trivia) anywhere in the codebase —
series and anime go through the TMDB *TV* endpoints (`/3/search/tv`,
`/3/tv/{id}`, `/3/tv/{id}/credits`, `/3/tv/{id}/external_ids`,
`/3/tv/{id}/season/{n}`, `/3/tv/{id}/season/{n}/episode/{m}`), exactly as movies
go through `/3/search/movie` and `/3/movie/{id}`. Consequences that follow from
this and are relied on everywhere:
- `metadata.tmdb_id` is the **only** provider-id field a leaf ever carries. No
  `tvdb_id` / `anidb_id` field exists in any schema (§6.3) and none is ever
  written.
- The folder token is `{tmdb-…}` for **every** category — `enrich_metadata`
  stamps it with no branch on `unit["kind"]`, so a series folder looks like
  `Dark (2017) {tmdb-70523}`, never `[tvdbid-…]`. Confirmed empirically, not
  assumed: a `tmdb` token matches TV shows on Plex, Emby AND Jellyfin (the
  12-show series/anime matrix, D11 below), so one provider covers movies,
  series and anime alike.
- A TVDB id is a **different numbering space** from a TMDB id. Writing one into
  `metadata.tmdb_id` would make `_resolve_unit_by_id` fetch a *different title*
  and download its artwork — silent library corruption with no error. This is
  why `prep_push_rep_enrich` / `prep_push_rep_season_enrich` **refuse** a
  `-tvdbid` argument outright (`_refuse_tvdbid`, `main.py`), before the archive
  runs, rather than accepting and best-effort-converting it (IMP-D22 Decision 1).

> **The one apparent contradiction — resolved by IMP-U6.** `suggest_target_folder`
> (`main.py:9775`, web console) used to propose a `[tvdbid-000000]` **placeholder
> string** in the suggested folder name for a NEW (unprepped) series/anime item —
> a user-editable placeholder in a suggestion box, never looked up and never
> stored, inherited from IMP-E12 (`docs/feature-web-console/DECISIONS.md`) before
> the enricher existed. It now proposes
> `mvcommon.CANONICAL_TMDB_TOKEN_FMT.format(id="0000000")` — i.e. `{tmdb-0000000}`
> — for **every** category (`main.py:9826`), because offering a `tvdb` placeholder
> invited the user to type an id this tool refuses outright and can never resolve.
> Pinned by `tests/test_web_datafns.py`. If TVDB/AniDB integration is ever added
> (IMP-E3/U3 breadth, PRIORITY.md Band 1), *this paragraph* is the statement that
> changes.

**`{tmdb-<id>}` folder token** (IMP-U6). The canonical emit format is curly
braces with the **bare `tmdb` keyword** — one form for movies, series and anime
alike, rendered from the single constant `mvcommon.CANONICAL_TMDB_TOKEN_FMT`
(`mvcommon.py:712`) so changing what we WRITE stays a one-line edit. The format
was corrected **twice** on server evidence: `[tmdbid-<id>]` (D1) →
`[tmdb-<id>]` (D11) → the current `{tmdb-<id>}`. **Note when reading
`docs/feature-token-brackets/DECISIONS.md`: D11 carries the full evidence matrix
but its headline still names the square `[tmdb-<id>]`, because the final flip to
curly was made after it and no D12 was written. The constant in `mvcommon.py`
is the authority, not D11's headline.**

**Settled empirically against the user's REAL Plex, Emby and Jellyfin installs**
(2026-09-22), not from vendor docs or community lore, which disagree with each
other and with the observed behaviour. A 20-folder matrix was scanned by all
three servers using nonsense titles and **deliberately wrong years**, so only a
token — never fuzzy title/year matching — could produce a correct match:

| Token form | Plex | Emby | Jellyfin |
|---|---|---|---|
| `{tmdb-680}` | ✅ | ✅ | ✅ |
| `[tmdb-27205]` | ✅ | ✅ | ✅ |
| `[tmdbid-603]` | ❌ | ✅ | ✅ |

The rule this establishes: **Plex rejects the `id` SUFFIX** (`tmdbid`, `tvdbid`)
**and the `=` separator, and is indifferent to bracket style** — it reads
`{tmdb-…}`, `[tmdb-…]` and `(tmdb-…)` alike. The widely-repeated claim that
**Plex ignores square brackets is false**; the load-bearing half is `tmdb` vs
`tmdbid`, not the bracket. A parallel **12-show series/anime matrix** confirmed
the identical rule for TV and that `[tmdb-…]`/`{tmdb-…}` match TV shows on all
three — which is why TMDB serves every category and MediaVault refuses `-tvdbid`
(IMP-D22). Curly was chosen from the two forms that work everywhere, for one
consistent shape across categories.

Two more results from the same matrix, both retired options — **do not
re-propose either**:
- **Parentheses are NOT a third option.** `(tmdb-105)` matched Plex and Jellyfin
  but **failed on Emby**. Only the two bracket families work on all three.
- **Dual-token emission is dead.** A folder carrying `{tmdb-12} [tmdbid-12]`
  matched Plex and Emby, and **vanished from the Jellyfin library entirely** —
  strictly worse than either single format. It had been floated as "the only way
  to satisfy all three" back when the Plex premise was wrong; it is not needed
  (one `tmdb` token satisfies all three) and it is actively harmful.

`enrich_metadata` stamps a `{tmdb-<id>}` suffix onto the show's top-level folder
exactly once via `rename_folder` — e.g. `Death Note (Complete Series)` →
`Death Note (Complete Series) {tmdb-12345}`. Seasons inside the folder inherit
the token automatically (their `folder_path` is under the parent directory and
does not repeat the token — and `normalize_season_folders` actively removes one
if it is there; see below).

**Push-safety — the brace escape is now on the HAPPY path.** `split_video_file`'s
mkvmerge v97 `-o` argument is rendered through libfmt as a format string, so a
literal `{`/`}` anywhere in the path is read as a format field and mkvmerge dies
with `fmt::format_error: argument not found` (exit 3). Those braces are escaped
as `{{`/`}}` for that argument ONLY (`main.py:409`), and mkvmerge renders them
back to single braces when it writes. Before D11 this mattered only for *legacy*
curly folders; now that **every** canonically-tokened folder contains braces, it
is load-bearing on every push out of a tokened folder. Pinned by
`tests/test_split_brace_escape.py::test_split_output_escapes_curly_braces_for_mkvmerge`
(a plain `merge -o` is taken literally and must NOT be escaped — see
`merge_video_files`).

**Detection is a deliberate SUPERSET of emission.**
`mvcommon.find_provider_tokens` (`mvcommon.py:763`) accepts four spellings
case-insensitively — `{tmdb-…}`, `[tmdb-…]`, `[tmdbid-…]`, and the Emby `=`
variant `[tmdbid=…]` — so a folder carrying any of them is recognized as already
tokened and never gets a second token, while only `{tmdb-<id>}` is ever newly
EMITTED. This asymmetry is **required, not legacy tolerance**: the live library
is still full of the older spellings until `migrate_provider_tokens --apply`
runs, and detection is what every idempotency guard, the artwork-inheritance
walk, and both migration commands' re-detection depend on in the meantime.

**`metadata.tmdb_id` OPTIONAL leaf field.** Set by `set_tmdb` or
`enrich_metadata`. Additive: adding it is zero-byte (no rehash, no `folder_path`
change). Alias-safe: `cmd_set_tmdb` skips `multi_ep_alias` entries (aliases carry
no `metadata` dict). Read by `items_payload` → `/api/items` → the SPA.

**Season-inheritance artwork resolution order (`resolve_artwork_path`).**
Given an entry id, the resolver checks in order:
1. Entry's own `folder_path` for `poster.jpg` / `fanart.jpg`.
2. The `season_map` parent's `folder_path` (via `parent_id`).
3. Walk UP the directory tree from the entry's folder to find the nearest
   ancestor whose name carries a tmdb token (the show-level folder), tested via
   `mvcommon.has_tmdb_token` — the SAME shared helper the stamping idempotency
   guard uses, so the two predicates cannot drift apart (this vocabulary had
   already drifted three times across IMP-C18/C22/C23). Before IMP-U6 this
   walk's predicate was curly-brace-only while a prior external migration had
   already renamed most of the real library to `[tmdbid-…]`, so the walk
   matched almost nothing and season/episode artwork inheritance was silently
   broken library-wide — a live regression fixed as a byproduct of unifying on
   `has_tmdb_token`. The fix was to share the SUPERSET detector, not to pick a
   bracket: that is why IMP-U6 later settling on curly `{tmdb-…}` **emission**
   did not reintroduce the bug — the walk matches every recognized spelling
   regardless of what is currently emitted.
A LOCAL image always wins over a TMDB-downloaded one — `enrich_metadata` never
overwrites an existing file. Security: only `poster.jpg` / `fanart.jpg` are
served; the resolved path is `realpath`-contained under `LOCAL_ROOT`; the path is
derived entirely from the library entry (no user traversal).

**Two series folder layouts — the flat one is the common case.** `_show_folder_of`
(`main.py`) picks the folder the `{tmdb-…}` token is stamped onto and the show
artwork is written into, from a show's distinct season folders. It has three
branches, and the third is NOT a corner case:
1. **≥2 season folders** → `os.path.commonpath` — the parent is the show folder.
2. **1 season folder with a season-shaped basename** (`Season 04`, `S04`,
   `Season_4`) → climb to its PARENT (the classic `<Show>/Season NN/` layout —
   the season `Dark Season 01 (2017)` under the show `Dark (2017) {tmdb-70523}`;
   the token is on the PARENT, never on the season).
3. **1 season folder that is NOT season-shaped** → that folder already IS the
   show folder. This is a flat release folder holding the episodes directly
   (e.g. `Peaky.Blinders.S06.2022… {tmdb-60574}`, `Devs.S01.2020.2160p.WEB.HDR
   {tmdb-81349}`) and a 2026-08-28 audit of the live library found it to be the
   **dominant real-world shape — 46 of the user's shows**. A flat show is left
   STRUCTURALLY untouched by `normalize_season_folders` (it is both show and
   season, so there is no season folder to rename); `migrate_provider_tokens`
   still brings its token to the canonical form.

In case 3 the show folder and the season folder are the SAME path, so the
show-level poster write and the per-season poster write target the identical
file: the show `poster.jpg` is written first and the season poster is then
correctly skipped as already-present ("kept"). That is graceful degradation by
design — not an error, not a second download, not a duplicate. Both layouts are
first-class scenarios and are exercised end-to-end by IMP-D22
(`tests/test_prep_push_rep_season_enrich.py`). Case 3 is also why
`cmd_prep_push_rep_season_enrich` **suppresses** its "this is the SHOW folder —
the parent of the season you just archived" rename note there: the resolved unit
folder equals the `season_map`'s own `folder_path`, so the note would be
factually wrong.

**wordninja runtime dependency.** `enrich_metadata`'s slug splitter imports
`wordninja` for concatenated-slug decomposition (e.g. `kurokosbasketball` →
`kuroko's basketball`). Imported via `try/except`; if absent the multi-variant
query degrades to the raw slug only.

**NFO emission (`--nfo` flag).** `enrich_metadata --nfo` — and, since IMP-D22,
`prep_push_rep_enrich --nfo` / `prep_push_rep_season_enrich --nfo` — writes
`movie.nfo` / `tvshow.nfo` next to the media with Kodi/Jellyfin-compatible XML,
via the single shared `_write_nfo` (`main.py`). `.nfo` is NOT in
`VIDEO_EXTENSIONS` so it is invisible to `scan_unprepped` and the push pipeline.
`--nfo` is **OFF by default on all three commands** (a 2026-08-28 scan of the
live library found zero `movie.nfo`/`tvshow.nfo` files across all 173 library
folders — off-by-default matches actual usage).

Elements emitted (IMP-D22 Decision 4 extended the set; the first four plus the
tmdb ids are unconditional, everything below them is written only when an
`api_key` is passed AND TMDB actually has a value — a missing value **omits the
element** rather than writing an empty one):

| Element | Source |
|---|---|
| `<title>` `<year>` `<plot>` `<rating>` | the already-resolved TMDB result (`rating` only when `vote_average` is not `None`) |
| `<uniqueid type="tmdb" default="true">` **and** plain `<tmdbid>` | the resolved `tmdb_id`; the plain element is what the user's own reference NFOs carry |
| `<imdbid>` **and** `<uniqueid type="imdb">` | `_resolve_imdb_id` — `/3/movie/{id}.imdb_id` for a movie, `/3/tv/{id}/external_ids.imdb_id` for a show; both omitted together when it returns `None` |
| `<genre>` (repeatable) | `_tmdb_genre_names(detail["genres"])` |
| `<runtime>` | movie: `detail["runtime"]`; show: first entry of `detail["episode_run_time"]` |
| `<premiered>` | movie: `release_date`; show: `first_air_date` |
| `<studio>` (repeatable) | movie: `_tmdb_company_names(production_companies)`; show: `_tmdb_network_names(networks)` |
| `<director>` (≤3) | movie: `_tmdb_directors_from_crew(credits)` (`job == "Director"`); **show: `_tmdb_created_by_names(detail)` — TMDB carries a series' creators in the `/3/tv/{id}` payload's `created_by` array, NOT as series-level crew (which is always empty for a show), so reading a show's director from `credits` silently wrote nothing** |
| `<actor><name>` (≤8) | `_tmdb_cast_names(credits)` |

**`<tvdbid>` is NEVER emitted**, at any tier, for any kind — there is no TVDB
source in this codebase to derive a correct one from (see the TMDB-for-everything
paragraph at the top of this section), and fabricating one is exactly the
silent-corruption class that convention refuses. Tests assert its absence.

`_write_nfo`'s **"NEVER raises"** contract is unchanged: the whole enrichment
block is wrapped, so a TMDB/network failure degrades to the base NFO with a
printed warning, and an IO/permission failure on the write itself is likewise a
warning. Because `_write_nfo` is one shared function, the richer element set
**also changes `enrich_metadata --nfo`'s existing output** — that is a
deliberate, desirable extension, NOT a regression, and it is explicitly carved
out of IMP-D22's "existing behaviour byte-for-byte unchanged" guarantee (which
covers the autopilots and the archive pipeline, not `_write_nfo`'s element set).

**`rename_folder` + rollback contract (cross-ref §12a).** `rename_folder` uses
the existing `RollbackJournal` (journal written in the parent directory of the
folder being renamed; `os.rename` on disk = PONR; `folder_path` JSON rewrite is
post-PONR). It is ADDITIVE to the rollback mechanism: it does NOT change the
journal format, the `fsync`+`os.replace` durability, the `RollbackHardFail`
contract, or any PONR location in other commands. See §12a and
`docs/feature-auto-rollback/ROLLBACK_MECHANISM.md` §10 (change-gate). IMP-U6's
`migrate_provider_tokens` **and** `normalize_season_folders` (both below) change
NEITHER the journal format NOR any PONR — they only change the STRING
`cmd_rename_folder` is CALLED WITH.

**`migrate_provider_tokens` (IMP-U6, `main.py:cmd_migrate_provider_tokens`).**
Whole-library format migration: every on-disk folder still carrying an
old-style tmdb token (`[tmdbid-…]`, `[tmdb-…]`, `[tmdbid=…]`, or wrong casing) is
renamed to the canonical `{tmdb-<id>}`. Discovery is **ancestor-aware** — for
every in-scope physical entry it climbs from the entry's own `folder_path` up
to `LOCAL_ROOT`, deduplicating every directory in the chain, because a prior
external migration tool had already renamed most LEAF folders but never
climbed to a parent show folder no entry's `folder_path` names directly.
Candidates are renamed **deepest-first** (required, not an optimization — a
shallower rename first would move an unprocessed deeper candidate's path out
from under it) and each rename goes through the unmodified `cmd_rename_folder`,
so it inherits its journal/PONR/self-heal for free and needs no state of its
own — a run interrupted mid-way is simply resumed by re-running the same
command; already-canonical folders are no longer recognized as candidates.
**Dry-run by default** (prints every `OLD -> NEW`, mutates nothing, provably
read-only); `--apply` performs the renames and writes a JSON audit report
under `<LOCAL_ROOT>/migration_reports/`. Only the tmdb token's own text span is
replaced — a coexisting `[tvdbid-…]` or other bracketed tag on the same folder
is carried through byte-identical. A mistyped `--library` value is a hard
refusal (unlike `enrich_metadata`/`refresh_online`, which silently widen to the
whole library) — deliberately, because this command mutates real folders under
`--apply` and a silently widened scope is exactly the surprise a destructive
command must not allow. A stale `folder_path` (directory no longer on disk) is
not pre-filtered; `cmd_rename_folder` refuses it and the refusal is recorded in
the report's `errors` array rather than silently dropped or aborting the run.
Live dry run over the real library: `scanned=317 would-rename=236
already-canonical=1`.

**`normalize_season_folders` (IMP-U6, `main.py:cmd_normalize_season_folders`).**
The STRUCTURAL follow-up to the format flip above. `migrate_provider_tokens`
fixes the *spelling* of a token already on a folder; this command fixes *which
folder carries one at all*, because season folders are uploaded to a phone
individually and must be self-identifying by NAME. Two phases, per show, in this
order:

- **Phase A — the show folder gets the id.** If a season's parent is a genuine,
  untokened show folder, it is renamed to `<existing name> {tmdb-<id>}` — e.g.
  `Peaky Blinders` → `Peaky Blinders {tmdb-60574}`. The id is resolved locally
  only (parent's own token → the season's own token → `metadata.tmdb_id`); no
  TMDB call is needed, so Phase A works with no API key. **One token per
  folder:** any stale NON-tmdb token is stripped first, by its own span, so the
  rest of the name survives byte-identically — `Dark (2017) [tvdbid-334824]` →
  `Dark (2017) {tmdb-70523}`, `Fringe (2008) [tvdbid-82066]` →
  `Fringe (2008) {tmdb-1705}`. Both ids name the same show, but MediaVault is
  TMDB-for-everything and Plex ignores the `tvdbid` spelling entirely, so the
  tvdb one is dead weight (pinned by
  `tests/test_normalize_season_folders.py::test_phase_a_strips_a_stale_non_tmdb_token`).
- **Phase B — the season folder gets a NAME, and NO id.** Renamed to
  `<Show Name> Season <NN> (<season air year>)` from ONE cached
  `GET /tv/{tmdb_id}` per show. The show name is sanitized for Windows-illegal
  characters (`_sanitize_folder_name_component` — this is the first place in the
  codebase a raw external title becomes a folder name; `Star Wars: Andor` →
  `Star Wars - Andor`). A season with no `air_date`, or a failed `/tv/{id}` call,
  is SKIPPED rather than given an invented year.

**Why a season folder must NEVER carry an id.** Season ids and show ids share
ONE numeric TMDB namespace. Friends S01's own TMDB *season* id is `4573`, and
`4573` as a *show* id is "Late Night with Conan O'Brien" — a season-level token
can therefore only mislead a scanner, and no media server reads one anyway. The
id belongs on the show folder; seasons inherit it by living underneath.

**THE CATEGORY-FOLDER GUARD has two independent halves**
(`_season_parent_is_exclusive_to_show`), and both are required. A season's
parent is treated as that show's own folder only if:
1. **Content half** — every tracked direct child resolves to the SAME TMDB id
   (an untracked sibling is ignored: it has no library entry to misattribute and
   rides along for free when the parent is renamed — the real Mr.Robot S04
   shape). A different id, or tracked content with *no* discoverable id at all
   (ambiguous), refuses the whole group.
2. **Structural half** — the parent must NOT be a direct child of a
   `CATEGORY_ROOT`. This exists because `C:\Media\Series\Tamil` holds exactly one
   show whose *folder name contains `S01`*, so the content half and the
   season-marker gate both passed it and Phase A proposed renaming the user's
   **language folder** to `Tamil {tmdb-274276}`. "Coincidentally one occupant
   today" is not "exclusively this show's folder". **Depth cannot decide this** —
   the tree is not uniform: `Series/English/Classic/<Show>` is three levels,
   `Series/Tamil/<Show>` is two. Only structure can tell them apart.

**Ownership is keyed on the TMDB id, never the library key**
(`_folder_tmdb_owner_ids`). MediaVault's library ids embed the SEASON's own air
year, so Peaky Blinders' six seasons derive six different `tv-en-<year>-…` show
keys while sharing ONE TMDB id, `60574`. The first version of the guard compared
library keys, which made a show's own sibling seasons look like foreign titles
and **refused 52 of 64 real seasons**.

**`--apply` backfills `metadata.tmdb_id`** onto every season in a group *before*
renaming anything. Only 41 of the 64 real season entries carry it — Friends and
The X-Files carry it on **none** — and once Phase B strips the folder token that
field is their only remaining id source. Without the backfill, a later run's
category-folder guard would see "tracked content, no discoverable TMDB id" and
wrongly refuse the whole show, breaking the idempotency guarantee.

Like `migrate_provider_tokens`: **dry-run by default**, idempotent by
re-detection with no state file (Phase A skips a parent that already has any
recognized tmdb token; Phase B skips a season already at its target name), every
rename through the unmodified `cmd_rename_folder`, and a JSON report under
`<LOCAL_ROOT>/migration_reports/` on `--apply`. Ordering is **shallowest-first
per show** — the opposite of `migrate_provider_tokens`' deepest-first — and this
is about ID SOURCING, not the rename cascade: Phase B's whole point is to strip
the season's token, so Phase A must read the id off it FIRST or it is lost for
every show whose only surviving id lives on a season folder. A Phase A failure
aborts that group's Phase B (the parent may be torn) and lists the untouched
seasons on the error record's `seasons_not_attempted`; other groups continue.
Live dry run over the real library: `scanned=64 show_folders_would_token=11
seasons_would_rename=60 skipped=4`.

**Hash safety of folder renames.** `rename_folder` moves the on-disk directory
and rewrites `folder_path` in JSON. It does NOT change any file's bytes — the
SHA256 hash stored in each leaf's `"hash"` key is over the file's BYTES, not its
path (§7.4). Chunk hashes in `split_info` are also byte-hashes and are unchanged.
`uid` / `.sha256` sidecars travel with the folder and remain valid.

**Enrichment skips the Others category (IMP-D18).** `enrich_metadata`,
`refresh_online`, and `fetch_trivia` all gather their work-set through the single
`_gather_enrich_units` chokepoint, which drops every entry whose
`category_of_id(...)` is `"other"` before bucketing — so no `oth-` id ever
reaches a movie/show unit. Sports/Others content is not on TMDB/OMDb, so
enriching it would mis-match a title, stamp a wrong `metadata.tmdb_id`, rename
the real Sports folder with a bogus `{tmdb-…}` token, and download wrong artwork.
The guard is unconditional (one place covers all three commands), so even a
no-arg whole-library `enrich_metadata` run is safe.

**Combined archive + enrich autopilots (IMP-D22).** `prep_push_rep_enrich` and
`prep_push_rep_season_enrich` fold enrichment into the two archive autopilots.
Each one calls the existing `cmd_prep_push_rep` / `cmd_prep_push_rep_season`
with **every argument forwarded unmodified** (those two functions are byte-for-byte
untouched by IMP-D22), then runs an enrich leg over the just-archived unit.
Shape, in order:

1. **`-tvdbid` refusal** — if a `tvdb_id` was supplied, `_refuse_tvdbid()` prints
   the reason and returns `False` immediately: no archive, no enrich, nothing
   touches disk (see TMDB-for-everything, top of this section).
2. **Archive** — the untouched autopilot runs.
3. **Completion check** — the movie command requires the entry to exist and read
   `status == "archived"`; the season command reconstructs *this run's* target-id
   list with `_season_run_target_ids` (honouring `episode_range`) and requires
   every one of them to be `archived`. Anything short of that prints a warning
   and skips the enrich leg, returning `False`.
4. **`-tmdbid` preset** — passed through `cmd_set_tmdb` exactly as a user would
   type it, so the resolve waterfall short-circuits into a fetch-by-id with no
   title search. `cmd_set_tmdb` refuses a `season_map`, so the season command
   lands the preset on the first episode LEAF under `base_id` instead.
5. **Enrich** — `_enrich_after_archive` resolves, applies, gates the folder
   rename, downloads artwork, applies episode overviews (shows) and optionally
   writes the NFO.

Two behaviours worth knowing:

- **The rename is confirmation-gated** (`_make_rename_confirm`). `--yes`
  auto-confirms, `--no-rename` auto-declines, and with neither flag the prompt is
  reached **only** when `sys.stdin.isatty()` is true — any script, cron job, CI
  run or the smoke suite falls through to "do not rename". That `input()` call is
  the **only one in the entire codebase**; the CLI is otherwise fully
  non-interactive, and keeping it unreachable off a TTY is what stops it hanging
  the smoke gate. Declining is never destructive — the metadata is still written
  and `rename_folder` can be run later.
- **The season variant scopes its enrich by the season's OWN `base_id`**, not by
  a derived show id. The real id convention embeds a per-season year
  (`tv-en-2022-peakyblinders-s06` vs `tv-en-2019-peakyblinders-s05`) and
  `_show_id_of` strips only the trailing `-sNN`, so sibling seasons already land
  in **separate** `_gather_enrich_units` buckets. A sibling season's
  `metadata.tmdb_id` preset is therefore NOT auto-discovered, which makes
  `-tmdbid` the primary per-season mechanism. The upside is a small blast radius:
  a season run cannot reach another season.

An enrich-leg failure **warns and continues** — the archive already succeeded, so
no TMDB miss, ambiguous match, missing API key or post-PONR `RollbackHardFail`
from the rename is allowed to turn a completed archive into a failure. The return
value reflects the ARCHIVE only; the enrich leg reports through prints.

> **🔁 STANDING SYNC OBLIGATION — `_enrich_after_archive` duplicates
> `cmd_enrich_metadata`.** IMP-D22 Step 1 was a two-way bake-off and the user
> chose the **isolated** candidate: `_enrich_after_archive` (`main.py`) is a
> deliberate, self-contained copy of three pieces of `cmd_enrich_metadata` —
> (a) the per-unit **resolve waterfall** (preset id → `_resolve_unit` search →
> EXA `_exa_resolve_tmdb_id` fallback) *including* its defensive `try/except`,
> (b) the **apply block** (the `tmdb_id` / `title` / `year` / `overview` write
> loop over `unit["ids"]`, with the `_title_is_id_shaped` guard), and (c) the
> **"no TMDB API key" guard**. It calls `cmd_enrich_metadata` zero times, so a
> change there does NOT propagate.
>
> **Whenever `cmd_enrich_metadata`'s resolve or apply logic changes,
> `_enrich_after_archive` MUST be updated in lockstep.** Nothing enforces this
> mechanically. Blast-radius safety (the existing bulk command cannot be broken
> by this feature) was traded for DRY, knowingly. The rejected zero-duplication
> alternative — which refactored the shared logic out of `cmd_enrich_metadata` —
> is preserved verbatim as the git tag
> `candidates/imp-d22/step-1/b-rejected` if the trade is ever revisited.
> Rationale: `docs/feature-prep-push-rep-enrich/DECISIONS.md`.

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
- **Re-prep keeps the split record of an unchanged file (IMP-C33, 2026-10-03):**
  an entry that is still local (`local_ready`, not uploaded) is rebuilt on
  re-prep. When it records a split and the freshly computed hash equals the
  stored one, the rebuilt entry keeps `split_info` and `re_hashed`, so an
  interrupted split push still resumes after an autopilot re-run. This is the
  rule `merge_extras_into_title` applies to an unchanged extra. A changed hash
  drops both, with a notice; the stale chunks are left in place and the next
  push refuses them (IMP-C32). Nothing is journalled for this.

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
from the remote.

**mvmeta v2 (IMP-C25 Step 5, `MVMETA_SCHEMA_VERSION = 2`, `main.py:5767`).** Every v1 key is unchanged;
each chunk additionally carries, when known, `sha1` / `dedup_key` / `date_utc` / `uploaded_name`
(`gpcapture.MVMETA_KEYS`), taken from the pre-upload snapshot and completed from the entry's capture file.
The lookup is read-only and wrapped so it can never raise at the (pre-commit) write site.

**`mvmeta_upgrade` — v2 for the existing sidecars (Step 13c, OD-4 + G0 q2 "Yes, with --apply only").**
`python main.py mvmeta_upgrade [all|<id>|<prefix>] [device <alias|serial>] [--apply]` (`main.py:15720`).
- Lists the phones read-only (`device_inventory`, Step 8) and finds each uploaded entry's / extras item's
  sidecar by name (`classify_device_objects`). FLAC holders are in no sidecar and are left out.
- Adds per chunk only **proven** facts, pinned to that chunk's own SHA-256: `sha1` + `dedup_key` only from a
  capture, the trusted block's own content key, or an account record verified by `dedupkey` / `sha256` (never
  a `filename` or `manual` proof); `date_utc` (the file's DateUTC, else a confirmed record's taken second);
  `uploaded_name` (block, else capture; never derived); `gphotos_account` (the confirmed copy's account).
  Keys are appended after the chunk's own keys (`MVMETA_UPGRADE_KEYS`); a v1 sidecar that gains a key
  becomes version 2; nothing else changes.
- A sidecar is rewritten only when it is exactly its owner's (manual_id, short_id, current chunk hashes),
  in the writer's exact bytes (the new bytes minus the added keys must encode to the original bytes), in a
  known version, and no present value disagrees. Anything else is listed "left as it is".
- Dry run (default) pulls into a temporary folder that is removed again and writes nothing anywhere.
  `--apply`, one sidecar at a time: keep the original locally
  (`~/.mediavault/gphotos/mvmeta/<UTC ts>/originals/NNNN.mvmeta.json` + `backup.jsonl`, fsynced) → push
  `<sidecar>.tmp` carrying the original mtime → `adb shell mv` over the sidecar → pull back and compare
  byte for byte → `stat` that the mtime was kept. The first failure stops the run; a re-run continues.
- Phones: without `device`, every device-check phone is read but only the push phones (`DEVICE_ALIASES`)
  are rewritten; the check-only extra movies phone (`DEVICE_CHECK_EXTRA_SERIALS`, alias `movies#2`) is
  rewritten only when named with `device`. Its adb commands pass its OWN allowlist (`_mu_argv_ok`: `pull`;
  with `--apply` also `push` to `<sidecar>.tmp` and `shell mv`), always with `-s <serial>`; the device
  check's read-only `_dev_argv_readonly` is not widened. The library is only read.
- Kill switch `MVMETA_UPGRADE_APPLY_ENABLED = True`; `False` makes `--apply` print one line containing
  `device rewrites disabled (OD-4 clarification)`.

**Identity capture `<short_id>.gpcapture.json` (IMP-C25, `gpcapture.py`, capture v2).** After a
**successful** `prep` / `push` (post-commit — after `journal.commit()`, never journalled, never raises) and
after an extras scan / extras push, MediaVault writes `<folder>/<short_id>.gpcapture.json` beside the `uid`
and `.sha256` sidecars (extras: `<title>/<group_rel>/<dir of sub_rel>/<item short_id>.gpcapture.json`).
Shape: `{capture_version: 2, manual_id, short_id, prep: {uploaded_name, source: {sha256, sha1, dedup_key,
date_utc, …}, tech}, pushes: [{captured_at, device_id, device_tz, device_tz_offset_min, remote_dir,
chunk_range, complete, objects: [{role whole|chunk|holder, index, uploaded_name, size_bytes, sha256, sha1,
dedup_key, date_utc, width, height, duration_s, approach_hint}]}]}`.
- `sha1` comes from the SAME read pass as the SHA-256 (`mvcommon.calculate_file_hash` → `cached_sha1`);
  `dedup_key` = unpadded urlsafe base64 of it = the dedupKey Google Photos will give the upload.
- `date_utc` and `duration_s` are read from the Matroska header in pure Python (no subprocess).
- The device timezone comes from ONE post-commit call `adb_base + ["shell", "getprop persist.sys.timezone;
  date +%z"]` (`main._device_facts`, `main.py:5861`); any problem → `{}` and the push stays successful.
- `approach_hint` = `hash` (SHA-1 known) > `taken_second` (DateUTC known) > `exact_name`.
- `mvcommon.read_capture` reads v1 and v2 alike. The capture is the push-time record only: its objects stay
  `unverified` (listed by `check_photos pending --list`) until confirmed in Google Photos; it never writes
  the library (P-11).

---

## 7. main.py Deep Dive

File: `C:\Users\harin\PycharmProjects\MediaVault\main.py` (18294 lines as of 2026-10-20; 3081 on 2026-06-12).

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

### 7.3a FLAC carry-out (unsplittable audio, feature/flac-carryout)

`mkvmerge` cannot `--split` a file carrying an `A_FLAC` audio track (a
packetizer-level refusal, all six `--split` modes, no override). Rather than
refuse-and-hand-off (the pre-carry-out behaviour), `cmd_push` now **carries the
FLAC out** when a split is requested — losslessly, with no conversion and no
dropping:

1. **Carry out** — extract the FLAC with `ffmpeg -map 0:N -c copy` (byte-exact),
   then wrap it in a valid-video **holder**: a ~10 s H.264/AAC stub with the FLAC
   attached as a Matroska ATTACHMENT (`wrap_payload_in_container`), and verify the
   wrap round-trips byte-exact (extract-and-md5). The holder is named
   `<base> [<short_id>].holder.mkv` in `_parts/` beside the chunks.
2. **Split the remainder** — `split_video_file(..., drop_track=N)` emits
   `--audio-tracks !N`, dropping only the FLAC so the rest splits normally.
3. **Record** — `split_info.carried_out_tracks[]` stores the FLAC's `position`,
   `language`/`default`/`forced`/`enabled`/`name`, the holder filename+hash, and a
   full `original_tracks` manifest (every track's type/codec/language/flags/name/uid).
4. **Restore** — fetch the holder beside the chunks; `extract_payload_from_container`
   recovers the byte-exact FLAC; `merge_video_files(..., carried=…)` re-adds it at its
   ORIGINAL position with its ORIGINAL flags via `--track-order` + per-file
   `--language`/`--default-track`/`--forced-track`/`--track-name` (all regenerated
   from the manifest, matched by track UID — generic, not sample-specific). The re-add
   is inside the single `--deterministic` run, so the blessed canonical hash already
   includes the re-added FLAC (the h1→split→h2→verify loop is unchanged).

The only field NOT preserved is the FLAC's **internal track UID** (mkvmerge has no
`--track-uid` and re-generates it; the regenerated value is itself deterministic). This
is cosmetic — playback, language/default/order, content bytes and the deterministic
hash are all unaffected. A FLAC file pushed **without** a split uploads whole, unchanged
(the carry-out engages only when FLAC AND split coincide). The same
`wrap_payload_in_container` primitive is the planned route for blank-disc `.iso`
archival (a future IMP) — it is payload-agnostic bytes-in→bytes-out.

**Split-sync (`--append-mode track`, D-10):** `merge_video_files` emits
`mkvmerge --append-mode track` so each appended chunk is offset by that TRACK's own
end timestamp, not the whole file's (mkvmerge's default `--append-mode file` uses the
highest-end timestamp across ALL tracks and overlaps/stretches the merged timeline when
tracks end at different times). Without it, the re-muxed video/AC3/subs drifted ~4.6 s on
a 2h41m movie while the carried-out FLAC (true length) stayed put → audible desync.
`--append-mode track` reproduces the original timeline exactly (0.0 s delta on the real
file). Determinism is unchanged (it is a fixed global flag).

`CARRY_OUT_CODEC_IDS = {"A_FLAC"}` (subset of `UNSPLITTABLE_CODEC_IDS`) gates which
codecs are carried out vs refused; `refuse_if_unsplittable(..., carry_out_flac=False)`
keeps the non-carry-out paths (e.g. `push_one_extra`, which splits without a carry-out)
refusing FLAC exactly as before. See also
[`docs/feature-flac-carryout/`](docs/feature-flac-carryout/).

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
   - If `<folder>/_parts/` exists and is not empty, **resume** (no
     re-split). The resume is strict (IMP-C32): it uploads only the files
     this entry's `split_info` records (its chunks and carried-out holders),
     and only after each one's bytes match the recorded SHA-256. Anything
     else in the folder is listed with its likely owner and left in place.
     A season's episodes share one `_parts/`, so the folder alone does not
     say whose chunks they are. With nothing recorded left to upload, the
     push refuses and changes nothing (IMP-C31 is that empty case).
     `push_one_extra` applies the same rule to its per-item chunk dir.
   - Else if `split_method` and `split_val` were passed AND the file is
     bigger than the target chunk size, call `split_video_file` and then
     immediately SHA256-hash every chunk into `checksums/<chunk>.sha256`
     and into `entry["split_info"]["chunks"]`. The library is saved
     **before** any upload begins, so an interrupted push can resume
     against verified hashes.
   - Else upload the single file as-is. If that completes a first archive
     (not a `chunks N-M` push) while the entry still records an unfinished
     split, the record and its `re_hashed` flag are dropped before the remote
     sidecar is written (IMP-C33), so the entry describes the whole file that
     was uploaded. An entry that was already uploaded keeps its record.
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
     the local chunk is deleted *only if* it sits directly in this push's
     chunk dir (`mvcommon.in_parts_dir`, IMP-C28; never a `_parts`
     substring of the path). The chunk counts as "done" only once it sits
     at its final name. This protects against accidentally deleting
     non-chunk source files if logic is ever rearranged.
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
   - **Identity capture (IMP-C25 Step 5).** After the push's last journal operation (post-commit, in both
     capture branches, `main.py:6972` / `:6988`) the push records its objects in the entry's
     `<short_id>.gpcapture.json` (`gpcapture.capture_after_push`) plus the device timezone (one
     `_device_facts` call). Wrapped: a capture problem can never fail or roll back a committed push.
     `push_one_extra` does the same per extras item; `push_title_extras` asks the device at most once per
     title, after the first extra uploaded. The OD-3 journal-op instrumentation test proves every
     `RollbackJournal` record is identical with and without the capture layer.
   - **Auto-confirm** of older unverified pushes runs only after the whole command returned, at the CLI
     dispatcher (`after_push_command`, §5) — never inside `cmd_push`.

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

#### `cmd_verify_restore(manual_id, temp_dir=None)` (`main.py:8233`) — DRY RUN

Re-reads each file in `<folder>/restore/` (with `temp_dir`: `<temp_dir>/<safe-id>/restore`, through the
same `_parts_base` as `cmd_restore` — IMP-C25 Step 11 h) and compares SHA256 against
either:
- For split: each chunk's hash in `entry["split_info"]["chunks"]`.
- For single: the entry's `hash`.

Prints pass/fail per file. Does not write or move anything.

#### `cmd_restore(manual_id, temp_dir=None)` (lines 895-984) — DESTRUCTIVE

`temp_dir` is optional. When given, the chunk + holder staging folder becomes
`temp_dir/<filesystem-safe manual_id>/restore` (via `_parts_base`, the SAME
`<safe-id>` the push `tempdir` uses, so fetch and restore agree on the off-volume
location); the MERGED OUTPUT and final target always stay in the entry's media
folder. This splits a big re-merge across two volumes — chunks read off `temp_dir`,
the ~orig-size merged file written on the media volume — so a 50 GB restore no
longer needs chunks + merged output simultaneously on one volume. With
`temp_dir=None` the restore folder is `<folder>/restore` (unchanged).

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
5. Delete each chunk file. Remove the now-empty `restore/` folder. (A leftover `restore\.mv_incoming\`
   from an interrupted fetch only makes this empty-folder cleanup skip; `fetch_cleanup` or the next fetch
   removes it. `cmd_restore` / `restore_one_extra` were deliberately not changed by IMP-C25 — change-gate.)
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
- `cmd_dispatch_fetch(manual_id, episode_range=None, fetch_extras=False, temp_dir=None, manual=False)`
  (`main.py:10397`) — spawns `["python", "mainfetch.py", "fetch", id, ("episodes", range), ("--fetchExtras"),
  ("tempdir", path), ("--manual")]` through `subprocess.Popen` with `_mainfetch_child_env()` (UTF-8 +
  `PYTHONUNBUFFERED=1`, IMP-C36), re-printing every line as it arrives, then `merge_learned_gphotos()`
  folds the learned sidecar into the library (one `   > Google Photos identity: N object(s) updated in the
  library[, M newly confirmed in Google Photos][, K conflict(s) not applied].` line when something changed;
  silent otherwise; skipped after a Ctrl+C — the records wait in the sidecar).
- `cmd_fetch_restore(manual_id, episode_range=None, fetch_extras=False, temp_dir=None, manual=False)` (`main.py:10461`) —
  dispatch fetch, then call `cmd_restore_group`/`cmd_restore` depending
  on entry type. **0-match guard (IMP-C18):** for a season_map run with
  an `episode_range`, if `cmd_restore_group` returns 0 it suppresses the
  green `✅✅✅ FETCH & RESTORE COMPLETE.` banner and prints a
  `⚠️ … 0 items (range … selected nothing)` summary instead (`main.py:10503`).
  Single-item / no-range runs keep the original banner; exit code is
  unchanged (the function returns `None` throughout).

---

## 8. mainfetch.py Deep Dive

File: `C:\Users\harin\PycharmProjects\MediaVault\mainfetch.py` (6313 lines as of 2026-10-20 — IMP-C25
rewrote the fetch selection, added download hygiene, the read-only `locate` diagnostic and the `check`
engine). Section map (grep the banner comments): configuration + `init_driver` (`:41-180`), session and tab
handling (`:184-515`), `trigger_download` = today's legacy attempt, S4 (`:516`), download hygiene P-12
(`:668-2257`), `FetchContext` + `build_fetch_queue` (`:2259-2548`), the exact-item locator (`:2551-4154`),
the harvester + lost-download retry + failed-list booking (`:4183-4643`), `--manual` + identification block
(`:4644-4883`), `_fetch_items` / `fetch_single_entry` (`:4886-5006`), target resolution + account routing
(`:5008-5393`), `cmd_fetch_route` (`:5435`), CLI parsers, `locate` (`:5617-5838`), `check` (`:5840-6609`),
`cli` (`:6610`).

### 8.1 Configuration, accounts and per-object routing

```python
CHROME_PROFILES = {                       # mainfetch.py:41
    "movies": r"C:\Media\Utils\ChromeProfile",
    "tv":     r"C:\Media\Utils\ChromeProfile_TV",
    "anime":  r"C:\Media\Utils\ChromeProfile_Anime",
    "others": r"C:\Media\Utils\ChromeProfile_Others",
}
ID_PREFIX_PROFILE = [("ani", "anime"), ("tv", "tv"), ("mov", "movies"), ("oth", "others")]   # :49
DEFAULT_PROFILE = "movies"
CHROME_PROFILE_NAME = "Default"
SYSTEM_DOWNLOADS_FOLDER = os.path.join(os.path.expanduser("~"), "Downloads")   # fallback mode only
EXACT_LOCATOR_ENABLED = True              # :59 — the P-14 kill switch (see §8.4)
```

Four Chrome user-data directories exist because the user has four Google accounts (movies, TV series,
anime, others/sports). `ChromeProfile_Others` is a user prerequisite (IMP-D18 / OD-10): until it exists,
`profile_preflight("others")` (`:299`) returns a one-line remediation and fetch / check / crawl refuse to
launch Chrome on it (Chrome would silently create an empty, signed-out profile).

**Routing — per OBJECT (IMP-C26 Step H + IMP-C25 P-4).** `_object_account(owner_id, block)` (`:5290`)
answers, for every uploaded object, `(account, "override" | "learned" | "prefix")`:

1. the `mvconfig.json` `fetch_account_overrides` match for the owning id — the user's escape hatch, the
   longest matching key wins, so an exact id beats a prefix (`_account_overrides` `:5232` validates it once
   per config object; a bad entry gives one stderr warning and is ignored);
2. the account its **trusted** `gphotos` block learned (`mvcommon.home_account`) when that differs from the
   prefix default;
3. the id prefix (`_prefix_profile` — today's routing).

`profile_for_id(manual_id)` (`:5283`) = override, else prefix (the entry-level answer used for the first
`[Account]` line). `_account_groups` (`:5345`) splits a batch into one group per account: the selector's own
account first, then `CHROME_PROFILES` order. One Chrome session per account runs sequentially under the one
`fetch_session_lock`. An entry whose objects span accounts is recorded in `ctx.plans` and fetched in each
of those sessions — only its own objects in each: the first session prints today's `🔹 PROCESSING:` header
and `> [Account] k of its N file(s) live in another account — fetched in the '<x>' session.`, a later one
`🔸 CONTINUING: …` (deliberately not a web-progress marker), a non-last one ends `⏸️ n file(s) left for the
'<x>' session.`, and the last gives the entry's verdict. An override that contradicts an object's verified
account prints `⚠️ override '<o>' for <id> differs from the verified account '<v>' (n file(s)) — the
override wins.`. With no valid override and nothing learned that differs from the prefix, the result is
today's single session, byte for byte (pinned).

The motivating case: 31 archived X-Files episodes (`tv-` ids) live in the MOVIES account, so
id-prefix routing could never fetch them (IMP-C26); the user's `fetch_account_overrides` entries route them
there today, and once mapped, their learned `home_account` does the same without an override.

### 8.2 Selenium + Chrome attach-mode setup (`init_driver`, `mainfetch.py:87`)

Rather than letting Selenium spawn Chrome with `--user-data-dir`
(Chrome refuses to load some Google sessions when launched by Selenium
in headful mode), `init_driver`:

1. `subprocess.Popen([chrome.exe, "--user-data-dir=<profile>",
   "--profile-directory=Default", "--remote-debugging-port=9222",
   "--disable-gpu", "--window-size=1920,1080", "--no-first-run",
   "--no-default-browser-check", "--disable-session-crashed-bubble",
   "--hide-crash-restore-bubble", "--disable-quic", "about:blank"])`
   - `--disable-quic` (IMP-C29) keeps downloads on TCP. Google serves a
     Photos download over HTTP/3 (QUIC) when Chrome allows it, and a QUIC
     download fails for good after a stall of about 8 s; over TCP it
     survives a 60 s stall. The switch only applies when this call
     starts the browser: a Chrome that is already running keeps the
     options it was started with.
   - No "Restore pages?" bubble (IMP-C25, 2026-10-08): Chrome 154 ignores
     `--disable-session-crashed-bubble` and leaves `profile.exit_type
     "Crashed"` in the profile's `Default\Preferences` even after a
     graceful close. So `--hide-crash-restore-bubble` is passed too, and
     `mark_profile_clean_exit(<profile>)` runs just before the launch and
     again once a Chrome a run closed or terminated is gone: it sets
     `exit_type "Normal"` + `exited_cleanly true` (only while no Chrome
     runs on that profile; atomic; every other key kept; never raises).
     The crawler (`tools/gp_inventory.py launch_chrome`) does the same.
2. `time.sleep(3)` — give Chrome time to bind port 9222.
3. Build a Selenium `Options` with
   `add_experimental_option("debuggerAddress", "127.0.0.1:9222")`.
4. `ChromeDriverManager().install()` to fetch/cache a matching driver.
5. `webdriver.Chrome(service=service, options=options)` — Selenium
   attaches to the already-running Chrome over the DevTools protocol.
6. `use_photos_tab(driver)` (IMP-C29) — move the driver into a normal
   `photos.google.com` tab (the open one, or a new one) and give it
   focus. chromedriver attaches to whichever target it finds first; on
   Chrome 154 that is the Gemini side panel. If no Photos tab can be
   had, `init_driver` returns `None`.
7. **Bounded page loads (IMP-C25 Step 14 c).** `bound_page_loads(driver)` (`:73`) sets a 60 s page-load
   timeout (`FETCH_PAGE_LOAD_S`) before the first navigation — Selenium's 300 s default once hung a live
   probe for ~5 min. The direct download lowers it to 15 s for its own navigation; check sessions use 60 s.
8. **A failed attach ends the Chrome it launched (Step 14 c).** If chromedriver-manager, the attach, or the
   Photos tab fails — or Ctrl+C arrives meanwhile — `_end_launched_chrome` ends the Chrome this call
   started, so it never stays behind on port 9222 and the profile. A `chrome.exe` that handed over to a
   Chrome already running exits at once; that Chrome is never touched (attach mode).
9. The `Popen` handle and the profile folder ride on the driver (`driver._mv_chrome_proc`,
   `_mv_user_data_dir`) for the fetch journal and the close (§8.5).

This means **all cookies, saved logins, and Google Photos session state
live in `C:\Media\Utils\ChromeProfile*`** and persist across runs. The
user must log in once manually in each profile; thereafter the script
inherits that login.

### 8.3 Today's legacy attempt = stage S4 (`trigger_download`, `mainfetch.py:516`)

`trigger_download(driver, query, index)` is the pre-IMP-C25 search-and-click attempt, kept as the
**last resort** (stage S4) and pinned by a source digest (`tests/test_fetch_search_wait.py`; a change is a
reviewed re-pin). It runs only for objects the exact-item stages (§8.4) did not settle, and for every
object when `EXACT_LOCATOR_ENABLED = False`. Fire-and-forget:

1. `use_photos_tab(driver)` (IMP-C29 — a focused `photos.google.com` page, never the Gemini side panel),
   `driver.get("https://photos.google.com")`, wait for `<body>`, `check_session_alive`.
2. Keystrokes via ActionChains: `"/"` (open search), `<query>`, `ENTER`. What is typed comes from the queue
   item built by `build_fetch_queue` (`:2447`):
   - **Attempt 1** (`specific_query`, index 0): for a whole file the plain library `filename` — WITHOUT
     the ` [<short_id>]` tag the uploaded name carries (RESEARCH F25: this query found zero results for 4
     of 11 sampled objects); for a chunk / FLAC holder its exact chunk / holder name (which IS the uploaded
     name — exactly one result in every sample).
   - **Attempt 2** (`fallback_query`): the entry's `search_term` (else its `filename`), clicking tile index
     = the chunk index (0 for a whole file) — the blind positional click.
3. `_await_search_results(driver)` (`:449`, IMP-C35) replaces the old fixed 3 s sleep: a bounded poll
   (`SEARCH_RESULTS_WAIT_S = 10`, every 0.5 s) until the `/search/` page shows settled results, says
   "No results", or has shown only the **"Most relevant to your search"** strip for 4 looks in a row (a
   miss). If the page never reached `/search/` or still shows the home timeline, it returns a problem line
   (`❌ … — this is not a 'Not found'.`) and nothing is clicked.
4. Click the Nth thumbnail. The primary selector `a[href*='./photo/']` matches nothing on search pages
   (RESEARCH F14), so in practice the XPath fallback `//div[contains(@style, 'background-image')]` does the
   clicking. Both lists pass `_drop_timeline` (`:500`), which removes home-timeline thumbnails AND the
   tiles of the "Most relevant to your search" strip (live finding 2026-10-05: its first tile — another
   show — was the wrong item the legacy index-0 click had picked). Click via
   `driver.execute_script("arguments[0].click();", el)`.
5. Sleep 2 s, send `Shift+D` (download original), sleep 1 s, `Escape`.

The function returns `True` if the trigger was sent (it does NOT wait
for the download to complete).

**Retry (IMP-C2)**: the whole attempt body is retried once after a 5 s wait
when the first pass returns `False` (0 thumbnails / index out of range) **or**
raises a Selenium fault, printing one `⏳ Retry 2/2 after 5s` line. The second
pass's result is final, so the `True`/`False` return contract is unchanged.

A backstop of 3 consecutive zero-result attempts on one driver (IMP-C6) raises `PhotosThrottledError` (a
`SessionExpiredError`, so every handler still stops the batch): each attempt passed `check_session_alive` on a
Google Photos page, so the profile IS signed in — the search answers nothing (IMP-C25 thfix, 2026-10-28; until then
it was reported as "logged out"). The fetch then prints `⏸️ Google Photos is throttled in '<account>' (3 searches in
a row found nothing although the profile is signed in) — the fetch stops here; …` (§8.8). The source digest was
re-pinned for this change.

### 8.4 The exact-item locator — stages S0a–S3u (candidate C + 10 cherry-picks)

`fetch_single_entry` (`:4952`) → `_fetch_items` (`:4886`) runs, per entry and per account session:
`_prepare_downloads` (§8.5) → **`_run_locator_phase`** (`:3819`, only when `EXACT_LOCATOR_ENABLED`) →
today's two attempts (§8.3) for whatever is still pending → IMP-C34's one retry of a lost download →
failed-list booking (`_record_unfetched`, then `_record_wrong_items`) → the identification block /
`--manual` round (§8.6) → `_finish_downloads`.

The engine is **Step 12 candidate C, "hybrid arbitration"**, chosen by the user at 🚦 G12 (2026-10-12,
DECISIONS §1f; judge `.candidates/c25-step-12/DECISION.md`: the only candidate with zero wrong downloads
under both an RPC outage and an item-page format change), plus the judge's 10 cherry-picks
(`.candidates/c25-step-12/C/PICKS_STATUS.md`).

**`locate_object(driver, locator, ctx, approaches="best", exclude=(), stages=None)`** (`:3479`) is
READ-ONLY: it navigates the session's Photos tab, types searches and asks allowlisted RPCs; it never
downloads, clicks a tile, presses Shift+D or writes anything. The waterfall (P-8, `_LOCATE_STAGES`
`:2602`), with the object's verified `approach.best` moved first:

| Stage | approach | How candidates are found |
|---|---|---|
| S0a | `photo_id` | the learned `photo_id` of this account, then its `alternates` — RPC item info (`VrseUb` + `fDcn4b`), else the item page |
| S0b | `hash` | content-hash lookup `swbisb` with the object's SHA-1 (one batch of ≤ 50 per entry). RPC only |
| S1 | `taken_second` | the items taken in the object's second (learned `taken_ms`, else its DateUTC): `lcxiM` at that timestamp, else the web day search (the whole day is read before filtering to the second) |
| S2 | `exact_name` | the exact uploaded name (whole file: `<name> [<short_id>]<ext>`; chunk / holder: its exact name) — `EzkLib`, else the search box |
| S3 | `title_sid` | title + short_id; then the **bare short id** when that pinned nothing (live 2026-10-07: an anime episode was found only that way) |
| S3u | `user_query` | the user's `search_override` from the failed list (OD-5) |
| (S4 preview) | `legacy` | for a content-keyed object nothing pinned: the two queries today's attempts would type, listed and verified (OD-5) |

- **Verification, never position.** A candidate counts only when its record PROVES it (P-5): its dedupKey
  equals the object's content key (proof `dedupkey`), or — for an object without one — its file name is
  the exact uploaded name (proof `filename`; P-6 normalized counts, flagged `name_normalized`). Never a tile
  index; a chunk set is resolved per chunk by dedupKey or exact `.chunk.NNN` name. The "Most relevant to
  your search" strip is never treated as a result (`gpweb.SEARCH_STATE_JS` / `TIMELINE_LINKS_JS`). More than
  `LOCATE_MAX_CANDIDATES = 20` names-only candidates = `ambiguous`, never a guess (no cap for a content-keyed
  RPC listing: every record carries its dedupKey — cherry-pick 9).
- **Per-stage fallback.** Each stage asks RPC first and, on `gprpc.RpcUnavailable` (incl. `RpcShapeError` /
  `RpcServerError`), runs the SAME stage on the web pages (`_stage_listing` `:3074`). One record cache per
  run (`ctx.locator`) feeds both paths: a season or a chunk set lists and opens each item at most once.
- **Drift detector** (`_arbitrate` `:3295`). Once per run the first RPC-located object is cross-checked
  against its item page's record (dedupKey, taken / upload time, tz offset). A disagreement prints
  `⚠️ Google Photos' internal answers disagree with the item page (…) — RPC is off for the rest of this
  run; the web pages decide.` and that object is located again from the pages.
- **Stale photo_ids** are recorded only when RPC AND the item page both call the item gone.
- **OD-5 skip.** When every candidate today's attempts could click is proven another item, the object
  ends `failed` with reason `s4-skipped` (today's blind attempts could only download wrong multi-GB files).
- **The throttle guard** (`_zero_check` `mainfetch.py:3438`, IMP-C25 thfix 2026-10-28 — the R3 dry runs of
  2026-10-27: Google Photos' search answered NOTHING for tens of minutes per account, in waves, while the items
  were there, and every object read "not found"). When nothing pinned an object and a LISTING stage (S1, S2, S3,
  S3u, the S4 preview) answered nothing, that zero stands only while the search demonstrably works on the
  transport it came by (`ui` when any zero came by the web pages or RPC is not usable now, else `rpc` = `EzkLib`):
  - a fresh search on that transport listed an item, or a canary passed, within `CANARY_REUSE_S = 10` s →
    `zero_check: "recent"` (no extra search);
  - else a **canary**: one uncached search for an item known to be in that account (`_pick_canary`): an item a
    TEXT query pinned earlier in the run (its query), else the account's crawl inventory (the latest complete
    enumeration; not archived / partial / errored; MediaVault-tagged chunk names first — an exact chunk name finds
    exactly one item), else — only if the inventory gives none — a library object with a CONFIRMED record there
    (its uploaded name; `ctx.library`, which the guard never reads on its own). Never the object itself (its photo
    ids, the candidates it saw, its name, its content key). The canary lists its item → `"canary"`. It does not →
    that item is never used again in the run and ONE more canary (another item) decides (a single deleted or
    unsearchable item never decides alone while the account has another); both fail, or the only one fails →
    **`PhotosThrottledError`** (`page="search"`, why "a search for an item known to be there found nothing"):
    nothing is recorded for the object and the caller stops;
  - no item to test with at all → `"unverified"`: the zero is recorded as before, marked; check_photos and the
    fetch never book it on the failed list.
  S0a (photo_id) and S0b (hash) look-ups and found objects never ask for a canary (in R3 the photo-id look-ups
  kept working throughout). Pacing is unchanged: after a recovery the throttle returned after ~30 searches even at the
  web pages' ~6 searches/min, so no evidence supports a minimum spacing.
- Result contract (Step 13 reuses it): `{found, photo_id, record, proof, name_normalized, stage, approach,
  via, outcome, n, rule, alternates, query, per_approach, per_approach_via, per_approach_pick, stale,
  s4_skip, zero_check, candidates_seen, candidates, pages_opened, searches, rpc_calls, elapsed}`, where
  `per_approach[<approach>]` is exactly `{outcome, n}` (cherry-pick 1) — each entry is a valid failed-list
  `tried` row; `zero_check` is None, `recent`, `canary` or `unverified` (the guard above).

**Triggering a located object** (`_fetch_window`, at most `DIRECT_DOWNLOAD_CONCURRENCY = 3` at once):
1. **Direct download (P-20)** — `_direct_trigger` (`:3665`): mint a FRESH original-download link
   (`gprpc.download_url`: item info `[7]`, else `pLFTfd` with that one key), `_rearm_downloads`, open it in
   the automation Chrome (15 s page-load bound). Confirmed when a download shows in the watched folder within
   `TRIGGER_CONFIRM_S = 20` s — never one of the window's other downloads (cherry-pick 4). A link that
   answers with a web page is left at once (cherry-pick 5). Lines: `⬇️ <file>: direct download`,
   `↪️ <file>: no download link was minted — Shift+D next.`, `… the direct download did not start — its
   link answered with a web page — Shift+D next.`. The link is never printed, stored, logged or journaled.
2. **Shift+D** — `_shift_d_trigger` (`:3787`): the item's own page (reused when already open — cherry-pick
   7), and `_shows_item` checks before every keystroke that the page's ADDRESS names the item and its record
   is there (an item page also embeds its neighbours' records); else the click path opens the item from its
   search results by the tile whose link names the photo_id.
3. A located object none of whose requests fetched it is located ONCE more without that item
   (`exclude`, `_another_copy` — cherry-pick 6) before today's attempts.
4. A content-verified link that served other bytes switches direct downloads off for the run
   (`⚠️ The direct download link served other bytes than a content-verified item (a transcode?) — direct
   downloads are off for the rest of this run; Shift+D fetches the originals.`). If Shift+D also brings other
   bytes, the object ends `inconsistent` (cherry-pick 3; IMP-C27).

SHA-256 after the download stays the final authority (the harvester). Each account session ends with
`> 🔎 Exact item ('<account>'): L of N object(s) located · D direct download(s) · S Shift+D · RPC calls: N
(errors E)` (`_locator_session_line` `:4148`).

**Kill switch (P-14).** `EXACT_LOCATOR_ENABLED = False` reproduces today's fetch selection exactly (no
stage runs); learning, the lost-download retry, the search wait and the download hygiene stay on.

**`python mainfetch.py locate <id> [episodes <r>] [--fetchExtras] [--probe-direct]`** (`cmd_locate`
`:5695`) — the same engine as a READ-ONLY diagnostic: per object `FOUND · <stage> <approach> · proof … · via
rpc|ui · <s> · pages · searches · RPC`, its photo id and item address (on the terminal only), `tried: …`.
It takes the fetch lock without waiting (a running fetch → refused), closes the Chrome it opened, and ends
`✅ Located F of N object(s). Nothing was downloaded or written.`. `--probe-direct` also mints each found
item's link and reports only whether a cookie-less HEAD request answers with media
(`direct link without the browser's cookies: yes (<type>)` / no) — the link is never shown.

### 8.5 Where downloads land — download hygiene (P-12, Step 11)

**Destination-direct.** `_prepare_downloads` (`:1097`) runs before the entry's first request:

- `<dest>` = `_fetch_restore_folder(entry, temp_dir, entry_id)` (`:655`) — `<folder_path>\restore`, or
  `<tempdir>\<safe id>\restore` with `tempdir` (the same `<safe-id>` `cmd_push` / `cmd_restore` use).
- Chrome is pointed at `<dest>\.mv_incoming\` (a marker file `.mediavault_incoming` inside) through CDP
  `Browser.setDownloadBehavior {behavior: allow, downloadPath, eventsEnabled: true}` (fallback
  `Page.setDownloadBehavior`), re-applied on every Photos tab (`use_photos_tab` → `_rearm_downloads`).
- A path too long for Chrome (folder + longest name + `.crdownload` > 240) → `<drive>:\MV_incoming\<first 8 hex
  of SHA-1(dest)>\`, same volume.
- **Disk pre-flight**: the destination volume must hold the entry's remaining bytes + 2 GB, else
  `❌ Not enough free space on <vol> for this entry: … Nothing was downloaded — free some space, then
  re-run.` and the entry is refused before any request; otherwise one `> 💽 Disk space on <vol>: …` line.
- Modes (`ctx.target["mode"]`): `incoming` (normal — the harvester watches only the incoming folder;
  `~/Downloads` is neither read nor written), `fallback` (Chrome refused the redirect → `~/Downloads` for the
  rest of the run, one warning line; only files that appeared during this run, belong to the entry's
  expected-name family and failed verification are deleted there), `legacy` (a driver without CDP — test
  stand-ins only, never a real Chrome — keeps the pre-IMP-C25 flow byte for byte).

**The harvester** `harvest_downloads(queue, ctx, timeout=300, stop=None)` (`:4284`): every completed file
in the watched folder is SHA-256-verified (hashed once per run — IMP-B10; its SHA-1 from the same pass); a
match is `os.replace`d into `<dest>\<expected filename>` (`✅ MOVED: <file>` — the P-13 web marker), a copy
of an item already fetched is deleted (`Duplicate. Deleting.`), anything else is deleted at once and listed
(name + size). The 300 s base timeout self-extends while a `.crdownload` is active. A match teaches the
learned sidecar (`_learn_from_download`: proof `sha256`, source `fetch`, the bytes' dedupKey; the photo_id
only when the requested item's record holds exactly those bytes). IMP-C34: a lost download (requested, the
round timed out with nothing downloading, never arrived) is requested once more (`=== RETRY … ===`); a
second loss → `❌ Download lost twice: <file> — …` and a failed row with reason `download-lost`.

**End of an entry.** `_finish_downloads` (`:1157`): whatever is still in the incoming folder (a broken
transfer's partial, a surplus download) is deleted and listed, the folder is removed; a file Chrome still
holds goes to `pending_delete` and is deleted after Chrome closes, else at the next start-up. Nothing
outside a marked incoming folder is ever deleted (except the fallback rule above).

**The fetch journal** `~/.mediavault/fetch_journal/<run_id>.json` (`FetchJournal` `:747`; atomic temp +
fsync + `os.replace`; a separate file — NOT the rollback journal `.mediavault_txn.json`): pid, state
(`running` / `clean` / `pending` / `recovered`), sessions `{account, chrome_pid, user_data_dir, launched,
closed}`, entries `{manual_id, label, account, dest, mode, incoming, names, cleaned, objects}`, the
`~/Downloads` snapshot (fallback only), `pending_delete`. Object fields are whitelisted (label, sha256,
state, method, photo_id) — never a URL.

**After an interruption.** `_end_run(ctx, interrupted)` (`:2104`, in a `finally` around the session loop):
Ctrl+C or an error terminates the Chrome this run LAUNCHED (`🛑 Chrome terminated (pid N) — its downloads
stopped.`), deletes this run's leftovers and leaves the journal `clean` or `pending`. The next run's
`_startup_cleanup(ctx)` (`:1546`, inside the fetch lock, before its first Chrome) finishes every unfinished
journal that is not a live fetch: it terminates a recorded Chrome only if that pid still runs AND its
command line names the session's profile folder (`_chrome_still_ours`; PowerShell `Get-CimInstance
Win32_Process` with `-ErrorAction Stop`, `taskkill /PID n /T /F`), never a Chrome the run only attached to;
deletes the leftovers of the incoming folders it names; marks it `recovered`; and prints `> 🧹 Cleaned N
leftover file(s) from an interrupted fetch (X.X GB)`.

**`fetch_cleanup [--apply]`** (`fetch_cleanup_plan` `:1640`, `fetch_cleanup_apply` `:1728`) — for leftovers
from before this feature: files directly in `~/Downloads` whose name belongs to a library object's
expected-name family (its exact uploaded name, Chrome's ` (n)` variants, their `.crdownload` partials), every
file in a marked incoming folder, and unfinished journals. The dry run lists name / path, size and reason and
writes nothing (no lock, no journal); `--apply` takes the fetch lock without waiting, re-checks each file
(still there, same size, still where the rule allows) right before deleting it, removes emptied marked
folders and marks journals recovered. Folders without the marker are listed "Not touched".

**Chrome lifecycle (user requirements 2026-10-04 / 2026-10-08).** `_close_session(driver, ctx, last)`
(`:2035`) ends every account session except an interrupted one: it first waits until no download of the
session is still growing (`DOWNLOAD_STALL_S = 300`; a transfer is never cancelled), then
- a Chrome the run **launched**: CDP `Browser.close`; still running 10 s later → terminated, only when its
  command line names the profile (under heavy load the open `Popen` handle is the identity proof when the
  process table cannot answer — Step 14 c). Line `🧹 Chrome ('<account>') closed — the next fetch starts a
  fresh one.`;
- a Chrome the run only **attached** to: after the last account only the tabs this run opened are closed
  (`🧹 Closed N tab(s) this fetch opened — the Chrome it attached to ('<account>', already running before)
  stays open.`); between two accounts its windows are closed (the next account needs port 9222).
- **No "Restore pages?" bubble.** Every launch passes `--hide-crash-restore-bubble` (Chrome 154 ignores
  `--disable-session-crashed-bubble`), and `mark_profile_clean_exit(user_data_dir)` (`:1400`) sets
  `profile.exit_type "Normal"` + `exited_cleanly true` in `Default\Preferences` before EVERY launch and
  after EVERY close once that Chrome is gone — only while no Chrome runs on that profile, atomically, every
  other key kept, never raising. The crawler and `warm_profiles` follow the same rules.

### 8.6 What nothing fetched — identification block, `--manual`, resume

- **Identification block** (`identification_lines` `:4653`; `_help_unfetched` `:4864`, incoming / fallback
  modes only): `=== NOT FETCHED (N file(s)) — what to look for in Google Photos ===`, then per object
  `🧭 <file> · <id> (<label>)` and its account, exact uploaded name, local taken time ("the time Google
  Photos shows"), content key, size, what was tried, its failed-list status and the next commands
  (`python main.py fetch <id> … --manual`; for a failed-list row `python main.py resolve_photos --id <id>`).
  No photo id, URL or web marker.
- **`--manual`** (CLI only; the web UI never passes it): `=== MANUAL (N file(s)) — find each one in this
  Chrome window and download it ===`; per object the Photos tab is opened at its best start
  (`open_best_start` `:4748`: its located page, its manual match, the first candidate no record disproves,
  the day search, the exact-name search) and the incoming folder is watched for up to `MANUAL_WAIT_S` =
  30 min. Only a SHA-256 match is kept (`> ✋ … fetched by hand …`); a wrong item is deleted and the wait
  goes on. Learned with proof `sha256`, source `manual` — never `confirmed` until a check.
- **Resume.** A re-run skips files already in `restore\`; `_resume_hints` (`:3991`) adds as S0a candidates
  the photo_ids the learned sidecar and earlier runs' journals hold (still verified before any request):
  `> ↩️ <file>: N item(s) an earlier run found or learned are tried first.`

### 8.7 Entry resolution, batch mode, removed stubs

- **Removed stubs (IMP-A8).** `wait_for_download`, `automation_download_file` and `build_download_queue`
  were deleted in IMP-C25 Step 10 (pinned gone by a test); the queue builder is `build_fetch_queue`
  (`:2447`), shared by fetch, `locate` and `check`.
- `resolve_targets(manual_id, ep_range)` (`:5027`): if the ID is a
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
- `cmd_fetch_route(manual_id, ep_range=None, fetch_extras=False, temp_dir=None, manual=False)` (`:5435`):
  prints the entry-level `[Account]` line, resolves targets (+ extras with `--fetchExtras`), builds the
  account groups (§8.1), then under `fetch_session_lock(blocking=True)` per group: `profile_preflight` (a
  missing profile folder stops the batch with `❌ The Chrome profile for '<a>' does not exist: …`), the
  port-9222 guard before a second session, `_startup_cleanup` + a new `FetchJournal` before the FIRST
  session, `init_driver`, then `fetch_single_entry` per target / extra. A `SessionExpiredError` stops the
  batch with the sign-in remediation — its `PhotosThrottledError` with the throttle line instead (IMP-C25 thfix,
  §8.8); Ctrl+C prints `🛑 Stopped by user.`. Each session's `finally` prints
  the locator line and runs `_close_session` (not after an interruption); the outer `finally` runs
  `_end_run`. Ends with the lost-download line (if any), the "Left in Downloads" list (fallback mode only)
  and `✅ Batch Processing Complete.`.

### 8.8 Session-alive detector + keep-alive tooling (IMP-C17 / IMP-C6)

#### `SessionExpiredError` and `check_session_alive`

`mainfetch.py` defines:

```python
class SessionExpiredError(Exception): ...
class PhotosThrottledError(SessionExpiredError): ...   # IMP-C25 thfix — .why (the line's phrase), .page

def check_session_alive(driver, profile_key=None) -> bool:
    """Side-effect-free: inspect driver.current_url (the CALLER navigated already).
    True on photos.google.com (and note on the driver that this session has shown
    signed-in Google Photos) or when current_url cannot be read (a Selenium fault);
    SessionExpiredError on a sign-in page (accounts.google.com) or on Google Photos'
    page for signed-out visitors before the session showed signed-in Photos;
    PhotosThrottledError on every other page."""
```

The detector is **shared** by these callers:

- **Live fetch path (`cmd_fetch_route` / `trigger_download`)** — called before
  each trigger to fast-fail with a clear `SessionExpiredError` rather than
  silently burning minutes waiting for downloads that will never arrive (IMP-C6).
  A backstop of **3 consecutive zero-result trigger rounds** raises
  `PhotosThrottledError` (IMP-C25 thfix; until then a `SessionExpiredError` read as "logged out" — §8.3).
- **The exact-item stages and RPC** — the locator's navigations, and `gprpc._photos_url` (`gprpc.py:233`) before
  EVERY RPC request (IMP-C25 thfix: on Google Photos it notes the session as signed in even when an RPC-only
  session navigates nowhere).
- **Warm-up runner (`tools/warm_profiles.py`)** — calls `check_session_alive`
  after navigating to Google Photos to determine the `OK` / `LOGGED_OUT` /
  `THROTTLED` / `LAUNCH_FAIL` status for each profile. `THROTTLED` (IMP-C25 thfix) prints
  `⏸️ Profile '<key>': Google showed another page than Google Photos (throttled?) — not a sign-out; the next
  warm-up looks again.`, sends no toast and does not fail the run (exit 0, `✅ No profile is signed out.`).

`check_session_alive` (`mainfetch.py:250`) classifies the tab's address (`_page_kind` `:224`: its host and path
only) and raises `SessionExpiredError` only when the session is confidently signed out: the host is
`accounts.google.com` (a sign-in or verify page), or the page is Google Photos' landing page for signed-out visitors
(`google.com/…/photos/about` — a signed-out profile lands there at once, RESEARCH F26) while this browser session
has not shown signed-in Google Photos yet (`driver._mv_photos_seen`, set on every photos.google.com check).
**IMP-C25 thfix (2026-10-28):** every other page raises `PhotosThrottledError` — a `SessionExpiredError` on
purpose, so every handler still stops the session or the batch, but none of them prints "logged out":

- that same signed-out page once the session WAS signed in — in the R3 dry runs of 2026-10-27 Google
  server-redirected a signed-in session there in the middle of a search throttle (the movies profile's history,
  13:31:59Z, its only non-Photos page among 5,537 visits; the profile worked again afterwards). That was the false
  "logged out";
- Google's "unusual traffic" page (`google.com/sorry/…`) and any other page (an empty tab included).

The canary (`page="search"`, §8.4) and the fetch's zero streak (`page="streak"`, §8.3) raise it too. Each handler
prints ONE throttle line built from `throttled_head` (`:284` — `Google Photos is throttled in '<account>'
(<why>)`) and `throttled_hint` (`:290` — only for the signed-out page: `If Chrome on this profile still shows
Google Photos' signed-out page after the pause, sign in (--user-data-dir=…).`), advises a re-run in
`THROTTLE_RETRY_MIN = 30` minutes (advice only — nothing waits or retries by itself; R3's zero windows ended after
4–55 min) and records nothing for the object at hand (`<head>` = the `throttled_head` text):

| Caller | The line | Then |
|---|---|---|
| fetch (`cmd_fetch_route`) | `⏸️ <head> — the fetch stops here; files already fetched are kept and skipped. Re-run the same fetch in about 30 minutes.` | the batch stops (as a sign-out does) |
| `mainfetch.py locate` | `⏸️ <head> — the locate stops here; re-run it in about 30 minutes.` | the batch stops |
| check_photos child (`_check_session`) | `⏸️ <head> — nothing was recorded for the object it was checking; re-run the same command in about 30 minutes: it continues where it stopped.` | stop reason `throttled` (resumable, §8.10) |
| resolve_photos (`_rp_walk`) | `⏸️ <head> — its objects stop here (no verdict for the one at hand: it comes back next time; the answers given so far are kept). Re-run resolve_photos in about 30 minutes.` | the next account goes on (as after a sign-out); a search term just typed stays the object's search term |
| crawler (`tools/gp_inventory.py`) | `STOPPED: <head> — not a sign-out; progress is kept, re-run in about 30 minutes.` | the run ends `throttled`, exit 4 (§9.3) |

> **IMP-X5 reuse seam**: the planned account-health canary command will import
> `check_session_alive` directly. The ban-sentinel logic is out of scope for
> IMP-C17.

#### Single-flight session lock (`mvcommon.fetch_session_lock`)

`mvcommon.py` exposes a `fetch_session_lock` context manager backed by a lock
file at `~/.mediavault/locks/fetch_session.lock`.

- **`cmd_fetch_route`** acquires it in blocking mode for the entire batch (every account session) so
  only one session owns Chrome's CDP port 9222 and the profiles at a time.
- **The warm-up runner** tries it in **non-blocking** mode before launching
  Chrome; if the lock is held (a fetch is running) it skips that profile's
  warm-up and logs accordingly.
- **IMP-C25:** `mainfetch.py locate`, the `check_photos` child, `resolve_photos`, `fetch_cleanup --apply` and
  the crawler (`tools/gp_inventory.py`) take it **without waiting** and refuse while it is held. The check
  child and the crawler keep it alive with a heartbeat (lock mtime refreshed at most every 60 s after
  checking the lock still holds their line; a takeover → they yield). The auto-confirm probes it the same way
  before spawning its child.
- `LockHeldError` is raised by `mvcommon` when a non-blocking acquire fails;
  callers catch it and decide whether to skip or abort.
- Known limit (unchanged, Step 7 note): a plain fetch does not heartbeat, so a fetch running > 1 h can be
  reclaimed as stale (`stale_after=3600`) by another lock taker.

### 8.9 Read-only Google Photos RPC (`gprpc.py`, IMP-C25 Step 2b; OD-9 ruling)

MediaVault reads Google Photos' own internal web calls (the "batchexecute" RPCs the web app itself uses)
to find items quickly and prove identity without a download. **It never changes anything in a Google
account, and it never stores or prints a download link (P-20).**

- **One client, one allowlist.** Every RPC goes through `gprpc.call(driver, rpcid, request_data)`
  (`gprpc.py:341`); no other module builds a request. `ALLOWED_RPCIDS` (`gprpc.py:61`) is the read-only
  allowlist, checked before the driver is touched AND again at the one send site; anything else raises
  `RpcRefused` (deliberately NOT an `RpcUnavailable`, so no fallback handler can swallow it). The literal is
  pinned (`tests/test_c25_regression_pins.py` registry → `test_gprpc`); a new rpcid is a reviewed change, and
  `tests/test_locator.py` checks the fake's request log after every test.

  | rpcid | Read-only use | Helper |
  |---|---|---|
  | `lcxiM` | timeline page by taken time (S1; the crawler's full enumeration) | `timeline_at` |
  | `EzkLib` | the web UI's search box (S2/S3/S3u); the recently-uploaded listing | `search`, `recent_uploads` |
  | `swbisb` | content-hash lookup: which items hold exactly these bytes (S0b, ≤ 50 keys) | `hash_lookup` |
  | `VrseUb` | item info (record; original-download link at `[7]` when present) | `item_info`, `download_url` |
  | `fDcn4b` | item info ext: file name, size (own library only) | `item_info` |
  | `EWgK9e` | batch info: names, size, times, quality (≤ 50 ids) | `batch_info` |
  | `pLFTfd` | a fresh download link for ONE media key (P-20 fallback) | `download_url` |

  **Never called** (PLAN P13, named so the tests can refuse them): trash/restore, locked folder,
  albums/sharing, geo edits, set timestamps, set description, zip download jobs. Nothing in IMP-C25 needs a
  mutating call.
- **Page context — cookies never leave the browser.** Each request is a `fetch()` run inside the signed-in
  `photos.google.com` tab of the automation Chrome (`execute_async_script`, `credentials: 'include'`). The
  page tokens (`WIZ_global_data`) are read per request into local variables and sent to `photos.google.com`
  only; the endpoint path must be a plain path, so the XSRF token can go nowhere else. No credential is read
  out of the browser, stored or logged; gprpc prints nothing and writes no file. The signed-in email
  (`signed_in_account`) is for a terminal-only identity check, never logged.
- **Pacing and failure handling.** ≥ 0.5 s between two requests + up to 0.25 s jitter; 3 retries with
  1 / 2 / 4 s backoff (+ jitter) on HTTP 429 / 5xx / no answer envelope / a failed page request; a circuit
  breaker turns RPC off for the rest of that browser session after 5 consecutive failed attempts (state lives
  on the driver, so another account's session keeps RPC). Each session prints `RPC calls: N (errors E)`.
- **Evidence, not authority.** Typed parsers (`gpweb.PARSERS`) fail closed (`RpcShapeError`); answers are
  still verified (dedupKey / name), the drift detector cross-checks them against the item page once per run,
  and SHA-256 after a download stays the final gate. `gprpc.STATUS_NOT_FOUND = 5` ("no such item") is
  UNVERIFIED; a stored photo_id is called stale only when the item page agrees.
- **Download links (P-20).** `download_url(driver, photo_id)` returns a `DownloadUrl` (`gprpc.py:122`) whose
  `str` / `repr` / `format` are redacted and which `json.dumps` refuses; `reveal()` is used only for
  `driver.get`. Links expire within about an hour and act as a bearer credential while valid, so only the
  permanent `photo_id` is stored; a fresh link is minted at every fetch.
- **Quality flags (Step 14 b, one read-only live probe).** The compact `swbisb` record does not carry
  partial-upload / archived, so `check_photos` reads them from the item's full timeline record (≤ 1 extra
  `lcxiM` call per pinned object); original-quality stays unknown (no position could be established) and is
  never claimed.

### 8.10 Google Photos identity commands (`main.py`) — map, check, confirm, resolve

**Inventory crawler** (`tools/gp_inventory.py`, §9.3) → **`map_photos`** (backfill the archived library) →
user resolutions → `--apply`; then **`check_photos`** (verify any scope, download-free), the
**auto-confirm** after the next push (§5), and **`resolve_photos`** (manual confirm / match).

**`map_photos`** (`cmd_map_photos` `main.py:12830`) — offline: reads only the crawler's files through
`tools.gp_inventory.inventory_state` (no Chrome). Per uploaded object (`mvcommon.iter_uploaded_objects`:
whole, chunk, holder, extras; four categories):
1. freshness gate — an account's inventory backs a verdict only with a complete enumeration started ≤ 24 h
   ago (`MAP_INVENTORY_MAX_AGE_H`); otherwise `stale-inventory` and the re-crawl command;
2. match — a known content key (trusted block or capture) decides first (proof `dedupkey`); else the exact
   uploaded name in the default account, then the P-6 normalized name, then another account
   (`wrong-account`), then the `[short_id]`-tag `probable`; same-named items holding other bytes → `conflict`;
3. duplicates and re-split sets (newest complete upload, else `ambiguous`), IMP-C27 detectors
   (split_info lost, untagged old name, partial / not-original upload);
4. **not in any account — Step 9b (user ruling 2026-10-03, "why do you need the phone for this?")**: no
   phone needed; by its capture push time against the routed account's crawl start: pushed after the crawl
   began or within `MAP_RECENT_DAYS = 7` days before it → `not-backed-up-yet` (no failed row); earlier, or no
   push time → `possible-loss` (a LOUD block). Phones are read only with `--devices` (opt-in), which refines
   an old object to awaiting-backup / missing-after-backup / missing-everywhere.
   **Except `not-listed`** (IMP-C25 thfix, 2026-10-28 — in R3 a chunk confirmed by its dedupKey on 2026-10-16
   read `possible-loss` because the next crawl did not list its item): after the freshness gates, an object whose
   library block holds a CONFIRMED record (a photo_id with a verified proof) in an account whose inventory is fresh
   and whose latest complete enumeration does not list that photo_id (`_mp_unlisted` `main.py:11684`) is NOT a
   loss — archived or in the trash, perhaps. No failed-list reason, no `resolutions_todo.csv` row, out of the LOUD
   block, nothing recorded by `--apply`; its own short block in summary.txt (`Not listed by the crawl — NOT a loss:
   N object(s) whose confirmed record names an item its account's latest crawl does not list (archived, in the
   trash?) — nothing is recorded for them; check one with: python main.py check_photos <id>`, then one
   `    not-listed           <id> · <label> — in '<account>' per its confirmed record (proof <proof>)` line each)
   and in mapping.csv its status `not-listed` with the note `in '<account>' per its confirmed record, but the crawl
   does not list it — maybe archived or in the trash; check it with: python main.py check_photos <id>`.
   **And `uploaded-after-crawl`** (IMP-C25 mpfix, 2026-10-28 — Aavesham chunk 1/2, pushed in the evening, reached
   Google Photos after the next morning's crawl began; a check confirmed it minutes later and the next dry run called
   it `not-listed`): when that confirmed record's item reached Google Photos at or after its account's crawl began
   (`_mp_after_crawl` `main.py:11704`, to the second), the crawl could not list it yet. The item's own `upload_ms`
   decides whenever the record has one (every confirmed record does: `mvcommon.apply_learned` confirms none without
   it); without it, the capture's push time; never `verified_at` (it proves the item was there when verified, not
   when it arrived). Handled like `not-listed` — no failed-list reason, no `resolutions_todo.csv` row, out of the LOUD
   block, nothing recorded by `--apply`, `_mp_recency` leaves it — with its own block in summary.txt (`Uploaded after
   the crawl began — NOT a loss: N object(s) whose confirmed record names an item that reached Google Photos after its
   account's latest crawl began — the next crawl lists them; nothing is recorded for them`, then one
   `    uploaded-after-crawl <id> · <label> — in '<account>' per its confirmed record (uploaded <t>, the crawl began
   <t>)` line each — `pushed <t>` when the push time decided) and in mapping.csv the note `in '<account>' per its
   confirmed record — uploaded after this crawl began (uploaded <t>, the crawl began <t>); the next crawl lists it`.
   Its texts never say archived or trash.

Dry run by default: only its run directory (`~/.mediavault/gphotos/map/<UTC ts>/` or `--out`). `--apply`
appends learned records (source `map`, no approach verdict) for mapped / mapped-normalized / wrong-account
rows and runs the apply engine; `--resolutions <csv>` imports the user's filled `resolutions_todo.csv` /
`failed_objects.csv` (url → manual record + hint, datetime → hint, search → `search_override`); `--repair
split-info|names` and `--accept-probable` are explicit opt-ins.

**The dry run's apply preview** (IMP-C25 mpfix, 2026-10-28 — after the R3 apply, a `--repair split-info` dry run
previewed `2,207 object(s) would change (0 newly confirmed)`; the apply then recorded 6 new facts — the 6 chunks of
the 3 entries it repaired, newly confirmed) gives the apply's answer (`_mp_preview` `main.py:12772`): the engine's
dry run over the learned sidecar plus ONLY the run's records not on record yet — `_mp_new_records`, the rule
`--apply` (and check_photos' apply) appends by: same facts, whatever their time, so a re-dated duplicate changes
nothing — and the `--repair names` facts, taken as the apply takes them (`_mp_run_records`), also when the run itself
records nothing (the apply folds the whole sidecar). `--repair split-info`: the apply writes the PROVEN split_info
first and maps the repaired entries' chunks in the same run; the preview patches a copy of those entries the same
way (`_mp_patch_split_info`) and maps them again with the run's inventory index (`_mp_repair_preview`), printing
them apart — `  + N chunk(s) of M repaired entr(y/ies) map after the repair — --apply writes their split_info first,
then records them (not in the count above)` (N = the chunks whose verdict records something; the result's
`preview.repair` = {entries, chunks}) — and leaves every record about the repaired objects (their whole files, which
the repair removes, and their chunks) out of the engine's count: neither number exceeds what the apply changes.

**`check_photos <scope>`** (`cmd_check_photos` `main.py:14263`; engine `mainfetch.cmd_check` `mainfetch.py:6548`) —
verifies uploaded objects in Google Photos **without downloading**. main resolves and prints the scope
(item / alias, season, `series:<season id|episode id|show folder>`, id prefix, `account:<a>`, `pending`,
`hinted`, `all`), writes `run.json` in `~/.mediavault/gphotos/check/<UTC ts>/`, and spawns
`python mainfetch.py check --run <dir>` (streamed; Ctrl+C waits for the child to close Chrome). The child,
under the fetch lock (no wait) + heartbeat, runs one Chrome session per account, `locate_object` per object
(swbisb / EWgK9e batches of ≤ 50 warm its cache; a mapped cloud dedupKey is never a content proof — legacy
objects stay proof `filename`), looks in the other accounts only for an object its own account did not find
that is not a push of the last `MAP_RECENT_DAYS`, paces (3 s after a web-page object, 0.5 s after an RPC
one, `--pace`), stops after 5 uncheckable objects in a row, honours `--limit` / `--max-minutes`, and appends
`results.jsonl` (fsync per line). main turns results into verdicts (`confirmed · inconsistent · ambiguous ·
not-backed-up-yet · possible-loss`, with `--devices` also `awaiting-backup / missing-after-backup /
missing-everywhere`, `error`, `unchecked`), `report.txt` (per account × category × approach), `outliers.csv`
(`approaches-disagree, single-approach, legacy-only, ambiguous-everywhere, account-mismatch, duplicates,
partial-upload, not-original-quality, taken-time-differs, stale-photo-id`) and one summary line
`🔎 check_photos <scope>: N object(s) — …`. Dry run writes only its run dir; `--apply` = check, then record
(learned records source `check`, or `confirm` for `pending`; stale records; failed rows; the apply engine;
`failed_objects.csv`; `applied.json`); `--apply-run <dir|latest>` applies a reviewed run without Chrome
(`latest` = the newest complete run of the same scope). Re-running the same scope resumes its newest
unfinished run; `--recheck` starts afresh. `check_photos pending --list` is offline (no Chrome, no child).
`--min-age-h H` (IMP-C25 follow-up, 2026-10-27) leaves out of the run — after the scope and `--account` — every
object whose capture push time is less than H hours old (no push time counts as old): never looked up, no verdict
row, no failed row, no learned record, one line `N object(s) pushed less than H h ago skipped — checked at a later
push`; all of them young → `Nothing to check.`, no run directory. `run.json` records it and it is part of the resume
key (a plain run and a `--min-age-h` run never continue each other); `--apply-run` / `--list` refuse it.

**A throttled check (IMP-C25 thfix, 2026-10-28).** Every look-up runs through `locate_object`'s throttle guard
(§8.4) and the session detector (§8.8). A `PhotosThrottledError` ends the child's session (`_check_session`
`mainfetch.py:6444`) with `   ⏸️ Google Photos is throttled in '<account>' (<why>) — nothing was recorded for the
object it was checking; re-run the same command in about 30 minutes: it continues where it stopped.`: stop reason
`throttled`, no further account, status `stopped` (exit 1, like a sign-out). The object at hand gets NO
`results.jsonl` line — it stays unchecked, so a re-run (which resumes the run) looks it up again — and report.txt
keeps `State: stopped (throttled) — …` plus `  Google Photos was throttled: its search answered nothing even for an
item known to be there, or Google showed another page than Google Photos (never a sign-out, never a 'not found') —
nothing was recorded for the object it stopped at; wait about 30 minutes, then re-run the same command: it continues
where it stopped.` A `--apply` run still applies the objects checked before the stop (as after any stop but Ctrl+C).
Each results line carries `zero_check` (§8.4). A not-in-Google-Photos verdict (`possible-loss`,
`not-backed-up-yet`) resting on a look whose zero was `unverified` — no item could show the search was answering —
is marked `unverified` (`_cp_rows` `main.py:13656`): its note says `unverified: its search answered nothing and no
item known to be in the account could show that the search was answering — not booked`, `_cp_settle` gives it no
failed-list reason, and report.txt adds `Unverified: N object(s) of the above — …`. Runs started before this change
carry no `zero_check`: their zero answers may be throttle zeros — start such a scope afresh with `--recheck`, and do
not `--apply-run` them.

**Auto-confirm** — see §5 (`AUTO_CONFIRM_MIN_AGE_H = 24`, measured: 41 % of uploads were in Google Photos
12 h after their push, 84 % after 24 h, 99 % after 48 h; config `auto_confirm_after_push`). Its check is
`check_photos pending --apply --min-age-h 24 --max-minutes 15`: only the pushes at least 24 h old are looked up.

**`resolve_photos [failed|<csv>] [--account a] [--id <manual_id>]`** (`cmd_resolve_photos` `main.py:14834`)
— interactive, terminal only (no TTY → `❌ resolve_photos is interactive: run it in a terminal — …`, exit
1, nothing launched). Walks the newest failed row per object (answered / confirmed ones left out), one Chrome
per account under the fetch lock, shows the identification block, opens the best start, and asks
`[p]aste photo URL | [s]earch term | [n]ot uploaded | s[k]ip | [q]uit`. A pasted item verified download-free
(dedupkey / filename) → learned record source `check`; a proven content mismatch is refused; otherwise
"accept as a manual match (a later fetch verifies it by SHA-256)? [y/N]" → proof / source `manual` (never
confirms alone; the fetch's stages never download it — `fetch <id> --manual` does). A search term →
`search_override` + an S3u try at once. Every answer is logged at once to `resolved.jsonl`. At the end:
the engine's dry-run summary and `Apply these N changes to the library? [y/N]`; lost downloads get
`Re-fetch the N lost download(s) now? …`. Also reachable from the check report's ambiguous line.
IMP-C25 thfix: a throttle (§8.8) stops that account's objects with its `⏸️` line and the next account goes on —
the object at hand gets no answer in `resolved.jsonl` (it comes back next time; a search term just typed stays its
search term); a search term whose zero answer could not be verified says so: `🔍 your term found no verified item
(user_query not-found(0); unverified: no item known to be in '<account>' could show that its search was answering) —
kept as its search term: every fetch tries it (S3u)`.

**Device cross-check (opt-in, Step 8).** `device_inventory()` (`main.py:10800`) lists each configured
phone's `/sdcard/Media/` read-only (`adb devices`, `shell find`, `shell stat` — every argv passes the exact
allowlist `_dev_argv_readonly`); `classify_device_objects()` (`:10959`) gives each object `on_device / freed /
absent / unchecked` (`unchecked` when a phone was not listed — never "missing"). A check-only extra movies
phone (`DEVICE_CHECK_EXTRA_SERIALS`, listed as `movies#2`) is scanned too; push routing (`DEVICE_ALIASES`)
is unchanged.

**Live results (R2 / R3): results pending R2/R3** — redacted counts per command (locate, direct download,
chunk set + interrupt, wrong-account fetch, fetch_restore + web, `--manual`, `fetch_cleanup`, the real-library
map / check apply) will be added here after the gates.

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
| `tools/warm_profiles.py` | Daily keep-alive runner. Launches Chrome for each profile (or a single `--profile <key>`), calls `check_session_alive`, logs `OK` / `LOGGED_OUT` / `THROTTLED` / `LAUNCH_FAIL` to `~/.mediavault/logs/warm_profiles.log`, fires a Windows desktop toast on failure, and exits non-zero if any profile is logged out or failed to launch (`THROTTLED` — IMP-C25 thfix: Google showed another page than Google Photos or its sign-in page — prints one `⏸️` line, no toast, exit 0; §8.8). Since IMP-C25 Step 14 c it closes the Chrome it launched (`mainfetch._close_session(..., last=True)`; an attached Chrome only loses the warm-up's own tabs) and refuses to warm the next profile while port 9222 is still held (`❌ Profile '<key>' not warmed: …`), so it can no longer attach to the previous profile's Chrome and check the wrong account. It runs no `profile_preflight` (known gap: it could create an empty `ChromeProfile_Others`). |
| `tools/notify_toast.py` | Dependency-free Windows toast helper. Calls the WinRT `ToastNotification` API via `ctypes` / `winrt` if available; falls back to a `print` so non-Windows or headless environments don't crash. `send_toast(title, body)` is the only public function. |
| `tools/mediavault_warm_profiles.xml` | Windows Task Scheduler definition. Runs `warm_profiles.py` daily at ~03:00 as the current interactive user, only when the machine has been idle for 5 minutes, without requiring elevated privileges. Register with `schtasks /create /xml "tools\mediavault_warm_profiles.xml" /tn "MediaVault Warm Profiles"`; remove with `schtasks /delete /tn "MediaVault Warm Profiles" /f`. |

### 9.3 `tools/gp_inventory.py` — Google Photos inventory crawler (IMP-C25 Steps 2 / 7)

Read-only list of everything one Google account holds, the input of `map_photos`.

```
python tools/gp_inventory.py <movies|tv|anime|others> --port <port>   # launch that profile (not 9222)
python tools/gp_inventory.py <account> --attach <port>                # or attach to an open Chrome
python tools/gp_inventory.py all --status                              # offline counts: no Chrome, writes nothing
  [--mode rpc|dom] [--phase enumerate|details|all] [--max N] [--out <dir>]
```

- **`--mode rpc` (default, minutes):** incremental runs first page the recently-uploaded listing (`EzkLib`),
  then EVERY run does the full `lcxiM` timeline pass (library + archive — the deletion-detection pass), then
  `EWgK9e` batches (≤ 50 ids) for new or errored ids. Any `RpcUnavailable` → one WARNING line → the dom crawl
  does the rest. **`--mode dom`** = the original browser crawl (hours; opens every item page).
- Files in `D:\MediaVault_gp_inventory\` (`mvcommon.GP_INVENTORY_DIR`): `<account>.tiles.jsonl`,
  `<account>.items.jsonl` (one line per item, fsync'd), `<account>.runs.jsonl` (start / one seen line per id /
  an `enumerated` line once the whole account was listed — the completeness proof / end), `<account>.log`.
  Resumable by construction; append helpers repair a torn last line.
- `inventory_state(account, out_dir=None)` is the offline reader (`--status`, `map_photos`, `check_photos`):
  latest good line per id wins; `possibly_deleted` = ids absent from the latest complete enumeration.
- Lock: `crawl_lock()` = the fetch session lock without waiting + a progress-driven heartbeat (ONE crawl at a
  time, across accounts — run them one after another). Profiles: `--port` runs `profile_preflight` first; every
  session does a landing check. Exit codes 0 done / interrupted · 1 usage or missing profile · 2 signed out ·
  3 lock held or taken over · 4 throttled (IMP-C25 thfix: Google showed another page than Google Photos —
  `STOPPED: Google Photos is throttled in '<account>' (<why>) — not a sign-out; progress is kept, re-run in about
  30 minutes.`; the runs file ends `throttled`; §8.8). Output stays cp1252-encodable.

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

1. `main.py:18425` -> `cmd_dispatch_fetch(mid, None)` -> spawns
   `python mainfetch.py fetch mov-en-2024-inception` (UTF-8, unbuffered; output streamed).
2. `mainfetch.cmd_fetch_route` routes every object (§8.1): `fetch_account_overrides` > learned
   `home_account` > id prefix → `movies` here; one account group.
3. Under the fetch lock: `_startup_cleanup` finishes any interrupted earlier fetch (`🧹 Cleaned …`), a new
   fetch journal is opened, `profile_preflight("movies")`, then `init_driver("movies")` launches Chrome on
   `C:\Media\Utils\ChromeProfile` (port 9222, `--disable-quic`, `--hide-crash-restore-bubble`; the profile
   is marked cleanly exited first) and attaches Selenium to a `photos.google.com` tab.
4. `resolve_targets` returns `[entry]`; `fetch_single_entry` prints `🔹 PROCESSING: …` and
   `> Detected Split File (2 chunks)` and builds the queue of the 2 chunks not yet in `restore\`.
5. `_prepare_downloads`: disk pre-flight, `<folder>\restore\.mv_incoming\` created and set as Chrome's
   download folder (CDP).
6. `=== EXACT ITEM (2 files) ===` — `locate_object` per chunk: a chunk with a learned `photo_id` is
   verified by one item-info call (S0a, zero searches); otherwise S0b hash lookup (content key known) /
   S1 the shared taken second / S2 the exact chunk name — each candidate proven by dedupKey or exact name.
7. Each located chunk: a freshly minted original-download link opened in Chrome (`⬇️ …: direct download`),
   else Shift+D on its verified item page. The file lands in `restore\.mv_incoming\`.
8. Harvester: SHA-256 each completed file in the incoming folder; a match is renamed to
   `<folder>\restore\<chunk name>` (`✅ MOVED: …`), anything else deleted; a match teaches the learned
   sidecar.
9. Anything still pending gets today's attempts (S4, §8.3) unless OD-5 skips it; what nothing fetched gets
   its identification block (or the `--manual` round) and a failed-list row.
10. `_finish_downloads` empties and removes `.mv_incoming`; `✅ ENTRY COMPLETE.`; the session line
    `🔎 Exact item ('movies'): …`; `_close_session` waits for running downloads, then closes the Chrome it
    launched; `_end_run` marks the journal clean.
11. Back in `main.py`: `merge_learned_gphotos()` folds the learned records into the library
    (`> Google Photos identity: …`). Nothing was written to `~/Downloads`.

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
| **Chrome (DevTools)** | `subprocess.Popen([chrome.exe, "--remote-debugging-port=9222", "--disable-quic", "--hide-crash-restore-bubble", ...])` then Selenium `add_experimental_option("debuggerAddress", "127.0.0.1:9222")`; CDP `Browser.setDownloadBehavior` / `Page.setDownloadBehavior` (download folder), `Browser.close`, `Target.*` (tab selection) | `mainfetch.py:init_driver` (87), `_point_downloads` (1022), `_close_session` (2035) |
| **Chrome profile files** | `Default\Preferences` `exit_type` / `exited_cleanly` rewritten atomically (no "Restore pages?") | `mainfetch.py:mark_profile_clean_exit` (1400) |
| **Google Photos (web UI)** | `driver.get(...)` + `ActionChains` (`/`, query, Enter, Shift+D, Esc); item pages read through `gpweb.page_record` | `mainfetch.py:trigger_download` (516), the locator's page path |
| **Google Photos (internal RPC, read-only)** | `fetch()` POSTs to `batchexecute` from the signed-in page (`execute_async_script`), allowlisted rpcids only | `gprpc.call` (341) — §8.9 |
| **Download folder watcher** | `os.listdir` of the entry's `restore\.mv_incoming\` (fallback mode: `~/Downloads`), polling | `mainfetch.py:harvest_downloads` (4284) |
| **Windows process table** | PowerShell `Get-CimInstance Win32_Process` (`-ErrorAction Stop`), `Get-NetTCPConnection -LocalPort 9222`, `taskkill /PID n /T /F` — only for a Chrome this tool launched | `mainfetch.py:_ProcessTable` (1184) |
| **ADB (read-only listing / mvmeta)** | `adb devices`, `shell find`, `shell stat` (allowlisted); `mvmeta_upgrade`: `pull`, and with `--apply` `push <sidecar>.tmp` + `shell mv` | `main.py:device_inventory` (10800), `_mu_argv_ok` |
| **HTTP** (poster/fanart) | `requests.get(url, headers={"User-Agent": ...}, stream=True)` | `main.py:cmd_set_poster`, `cmd_set_fanart` |
| **HTTP HEAD** (`locate --probe-direct` only) | a cookie-less HEAD to a minted link; prints yes/no + content type | `mainfetch.py:_head_without_cookies` (5784) |
| **Subprocess to fetcher** | `subprocess.Popen(["python", MAINFETCH_SCRIPT, "fetch", id, ...], env=_mainfetch_child_env())` | `main.py:cmd_dispatch_fetch` (10397) |
| **Subprocess to the check engine** | `[sys.executable, <abs mainfetch.py>, "check", "--run", <dir>]` via `_cp_spawn`; the auto-confirm child `[sys.executable, <abs main.py>, "check_photos", "pending", "--apply", "--min-age-h", "24", "--max-minutes", "15"]` | `main.py:_cp_spawn` (13568), `auto_confirm_argv` (14939) |

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
  chunks are re-uploaded without re-splitting. Since IMP-C32 only the
  chunks that entry's `split_info` records are resumed, each checked
  against its recorded hash first. A re-prep of an unchanged file keeps
  that record (IMP-C33), so re-running an autopilot resumes as well. This
  is the primary fault tolerance mechanism — there is no transaction log.
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
- **Filename collisions**: a `.crdownload` arriving for a name already on disk gets ` (1)`, ` (2)`, ...
  suffixes by Chrome. Since routing is hash-based, this doesn't matter; the harvester picks up whatever new
  file appears in the watched folder (`.mv_incoming`, or `~/Downloads` in the fallback mode).
- **Wrong or partial downloads (IMP-C25 P-12)**: deleted at once inside `.mv_incoming` and listed; an
  interrupted fetch is cleaned up by the next fetch's start-up (`🧹 Cleaned N leftover file(s) from an
  interrupted fetch (X.X GB)`); files Chrome still locks are retried after Chrome closes and at the next
  start; `fetch_cleanup` handles leftovers from before this feature. A lost download is requested once more
  (IMP-C34); lost twice → the failed list (`download-lost`).
- **Lost session state**: if Selenium's debug-port attach fails (e.g.,
  9222 already in use by another Chrome), `init_driver` ends the Chrome it just launched and returns None,
  and the fetch route exits cleanly. A signed-out profile raises `SessionExpiredError` → one remediation line
  naming the profile folder; a missing profile folder is refused before any launch (`profile_preflight`).
- **A throttled Google Photos (IMP-C25 thfix)**: a search that answers nothing even for an item known to be in the
  account, or a page that is neither Google Photos nor its sign-in page, raises `PhotosThrottledError` → one `⏸️`
  line per command advising a re-run in about 30 minutes; never a "logged out", never a "not found", nothing
  recorded for the object at hand (§8.4, §8.8).
- **RPC trouble**: every locator stage falls back to the web pages on `RpcUnavailable`; 5 consecutive
  failures turn RPC off for the session; a silent RPC/page disagreement turns it off for the run (drift
  detector). Never a reason to download an unverified item.
- **Library written by another process during an identity apply**: the apply engine's fingerprint re-check
  reloads and re-patches (3 tries), then aborts with one line; the learned sidecar keeps every record, so a
  re-run is idempotent.

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
  **(IMP-R7, 2026-06-27)** `RollbackJournal.__init__` now calls `_handle_leftover()` before `_flush()`: a pre-PONR leftover triggers an auto-call to `recover_journal()` first (idempotent; restores clean state before the new command runs); a post-PONR leftover is renamed to a timestamped sibling (`.mediavault_txn.<ts>.json`) instead of silently overwritten. The no-leftover path (the common happy path) is byte-for-byte unchanged.

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
| `cmd_restore` (`main.py:2032`) | **split-path merged-chunk delete**, marked at `main.py:2185` | pre-PONR: **(IMP-R6)** merge staged to `<target>.merge_tmp<ext>`; `os.replace()` onto dummy only after verify/bless passes; the `create_reproducible` journal record carries the temp path; a failure removes the temp and leaves the dummy intact. At/after PONR: `RollbackHardFail` naming `fetch_restore <id>`. Standard path is a single `shutil.move` — no torn window |

*(Line numbers re-verified 2026-06-12.)*

### O-1 resume-message vs O-2 hard-fail split

- **O-1 (push = resume-message).** A failed multi-chunk push is reversible/resumable
  because the master survives; `cmd_push` already auto-resumes from a surviving
  `_parts/` (strictly: only the chunks the entry's own `split_info` records —
  IMP-C32). So a push failure is NOT a PONR — it leaves the partial upload and
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

> **Extras lifecycle (IMP-D19, §6.3):** the extras push/replace/restore phases
> **reuse** this journal additively — one journal per extra, in that extra's own
> folder — and change **nothing** in the main-content contract above (journal
> format/durability, the two PONR locations, created-this-run scoping, the `cmd_*`
> wrapping, `recover_journal` semantics, the season resume-range messaging, and the
> `RollbackHardFail` contract are all byte-for-byte unchanged — decision E1, user-
> cleared). Extras push is O-1 resumable (no PONR — a failed item stays
> `uploaded=False` and is retried next run); extras replace carries the same per-file
> commit-rename PONR and, at/after it, raises `RollbackHardFail` naming the existing
> `fetch_restore <id> --fetchExtras`, which `replace_title_extras` catches and prints
> (so a one-line extras wire inside `cmd_replace` can never trip that command's own
> handlers); extras restore merges to a `.merge_tmp` sibling and `os.replace`s only
> after verify/bless (IMP-R6), so the dummy is never zeroed. Because the journal lives
> in the extras folder, `recover --scan` lists it and
> `recover "<title folder>\<group_rel>"` finishes it.

> **Strict resume (IMP-C32, user ruling 2026-10-02):** the one user-ruled change to
> resume behaviour. A resume (`cmd_push`, and `push_one_extra` for extras) uploads
> only the files the entry's own `split_info` records, each checked against its
> recorded SHA-256; everything else in the chunk dir is listed and left in place, and
> with nothing recorded left to upload the push refuses. It changes resume
> SELECTION only. Every refusal comes before any upload and before anything is
> journalled, never touches the pre-existing `_parts/` (D-6) and saves nothing. The
> journal format/durability, the PONR locations, created-this-run scoping,
> `recover_journal`, the O-1 resume message, the season resume-range messaging and
> the `RollbackHardFail` contract are unchanged. See
> `docs/feature-auto-rollback/ROLLBACK_MECHANISM.md` §6/§8/§10.

> **IMP-C25 is rollback-neutral (OD-3 ruling "yes correct").** Nothing in the exact-item fetch,
> map / check / resolve, `mvmeta_upgrade` or the auto-confirm changes the journal format/durability, the
> PONR locations or `mark_point_of_no_return()` placement, D-6/D-7 scoping, the `cmd_*` wrapping,
> `recover_journal()` semantics, the season resume-range messaging or the `RollbackHardFail` contract.
> - The identity capture runs **post-commit** (after `journal.commit()`), is never journalled and never
>   raises (wrapped, so a capture problem can never roll back a committed prep / push); the OD-3
>   journal-op instrumentation test (`tests/test_gp_capture.py`) proves every journal record is identical
>   with the capture layer on and off.
> - The auto-confirm hook runs at the CLI-dispatcher level **after** a push-family function returned
>   successfully — outside every journal scope; an exception never reaches it (`tests/test_auto_confirm.py`
>   instruments a real `prep_push_rep` with a PONR: journal ops identical, no `.mediavault_txn*` file on disk
>   when the hook starts).
> - The apply engine (`gphotos_apply`) and `--repair split-info` write the library through their own
>   fingerprint-guarded load → patch → save, with **no** rollback journal; `cmd_restore` /
>   `restore_one_extra` / `bless_or_verify_merged_hash` are not modified (`verify_restore` only gained
>   `tempdir`).
> - The fetch journal `~/.mediavault/fetch_journal/<run_id>.json` is a separate file for download cleanup —
>   it is NOT `.mediavault_txn.json` and not part of this mechanism.

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
| `test_entry_schema_guard.py` | IMP-H3 — round-trips one entry of every `ENTRY_TYPE_KEYS` type through `save`/`load`, and asserts the whole-library read commands tolerate non-physical entries (the registry-driven guard); IMP-C25 section (d): the literal pinned, the canonical `_gphotos_block`, a four-prefix library with a block on every uploaded object round-trips byte for byte, and every whole-library reader gives identical output with and without blocks |

**IMP-C25 test files** (as of 2026-10-20: full suite **3278 passed**, smoke **102 passed in ~12 s**; disarm
62/62 in `.candidates/c25-step15/DISARM.md`). All run against the fake Google Photos world
(`tests/gphotos_fake.py`, conftest fixture `fake_photos`: four accounts — movies in UK English, the others US
— a Selenium-compatible driver that real `ActionChains` / `WebDriverWait` work against, item pages with
embedded records, downloads, signed-out modes, a search throttle — `throttle(account, after=N, rpc="zero"|"error")`:
after N searches every search answers nothing, as in the R3 dry runs — and Google's "unusual traffic" page (session
`"sorry"`; IMP-C25 thfix), and a `batchexecute` emulator with failure injection and a request log) — never live
Google, adb or network:

| File | Covers |
|---|---|
| `test_gpweb.py` / `test_gprpc.py` / `test_rpc_quality_flags.py` | parsers; the RPC client (allowlist, pacing, retries, breaker, redacted links, no token in any traceback); the live quality-flag layouts |
| `test_gphotos_primitives.py` / `test_gphotos_apply.py` | mvcommon identity primitives (pin rule, iterators, learned / failed / hints stores); the apply engine (fingerprint guard, conflicts, orphans, CSV safety) |
| `test_gp_capture.py` / `test_gp_inventory.py` / `test_gp_inventory_own_tab.py` | capture v2 + mvmeta v2 + the OD-3 journal-op instrumentation; the crawler (rpc / dom, lock + heartbeat, run log, golden crawl) |
| `test_device_inventory.py` / `test_map_photos.py` | the read-only device check (exact argv allowlist); the mapper (freshness gate, Step 9b recency, re-split sets, repairs, resolutions, the `not-listed` and `uploaded-after-crawl` verdicts, the dry-run preview = what `--apply` changes) |
| `test_fetch_routing.py` / `test_fetch_account_routing.py` / `test_anime_fetch_routing.py` | per-object routing, mixed batches, frozen pre-change transcripts |
| `test_fetch_harvester.py` / `test_fetch_hygiene.py` / `test_fetch_search_wait.py` / `test_fetch_photos_tab.py` / `test_fetch_child_stream.py` | the harvester (frozen oracles with the kill switch on and off), `.mv_incoming`, journal + cleanup + `fetch_cleanup`, the search wait + `trigger_download` digest, the Photos tab, live child output |
| `test_locator.py` | the exact-item stages S0a–S4 and both triggers (every PLAN Step 12 scenario + the 10 cherry-picks) and the throttle guard (canary, reuse window, `unverified`); the read-only allowlist checked after every test |
| `test_check_photos.py` / `test_auto_confirm.py` / `test_mvmeta_upgrade.py` | check engine, verdicts, outliers, apply, resume, a throttled stop and `unverified` verdicts; the push hook (six dispatcher branches, journal-op instrumentation); sidecar upgrade with a directory-backed fake adb |
| `test_fetch_manual.py` / `test_resolve_photos.py` / `test_chrome_robustness.py` / `test_chrome_restore_bubble.py` / `test_warm_profiles.py` | `--manual`, resume hints, resolve walker; Chrome close under load, failed attach, page-load bound, warm-up close, "Restore pages?" |
| `test_c25_regression_pins.py` | a registry of every existing C25 pin (a deleted / renamed pin fails) + the missing ones (spawned fetch argv, P-13 marker literals, `calculate_file_hash` contract, mvmeta v1 byte form) |

Hermetic autouse guards in `tests/conftest.py`: `_hermetic_gphotos_state` (redirects `~/.mediavault/gphotos`,
the inventory dir, the fetch journal dir, `~/Downloads`, the short incoming root, and an inert process
table), `_hermetic_chrome_profiles`, `_hermetic_check_child` (no test can spawn the real check child),
`_hermetic_auto_confirm` (the hook is an inert recorder; opt-in `real_auto_confirm`). Smoke Group 5
(`TestC25*` in `tests/smoke/test_smoke_all_commands.py`) drives capture, `pending --list`, the post-fetch
merge, `check_photos` / `map_photos` in-process on the fake world, an incoming-mode fetch, `mvmeta_upgrade`
on the mock device, the `resolve_photos` TTY refusal, `fetch_cleanup`, the six push-family dispatcher runs,
an alias sweep and all four prefixes. Details: `docs/testing-strategy.md`.

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
Code paths outside the listed areas (Selenium against real Google Photos — the download triggers, the CDP
download redirect and the Chrome close are verified live at 🚦 R2 (results pending R2/R3) — and MediaInfo
parsing) remain "tested by use"; the legacy snapshots under `archive/`
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

Most configuration is **hardcoded Python constants at the top of each
file**; paths are not CLI flags. To re-target the system, you edit the source. The exceptions are the
gitignored `mvconfig.json` (web host/port, API keys, and the two IMP-C25/C26 keys below) and the
environment variables MediaVault sets for its own children (`PYTHONIOENCODING=utf-8`,
`PYTHONUNBUFFERED=1`; `MEDIAVAULT_WEB_JOB` marks a web job so the auto-confirm only prints its reminder).

> **As of IMP-A1**, the shared library/path constants (`LIBRARY_MOVIES`,
> `LIBRARY_SERIES`, `LIBRARY_ANIME`, `LIBRARY_OTHERS`, `LOCAL_ROOT`,
> `MKVMERGE_PATH`,
> `SPLIT_DIR_NAME`, `CHECKSUM_DIR_NAME`, `RESTORE_DIR_NAME`,
> `VIDEO_EXTENSIONS`) are now defined once in `mvcommon.py` and imported by both
> entry points. The "Defined in" column below reflects the historical
> per-file locations; edit `mvcommon.py` to re-target any shared constant.

> **`mvconfig.json` keys read by IMP-C25 / IMP-C26** (gitignored; `mvconfig.example.json` documents them;
> read at call time through `mvcommon._load_config()`; absent keys keep today's behavior):
> - `"fetch_account_overrides": {"<exact id or id prefix>": "<movies|tv|anime|others>"}` — the top routing
>   layer (§8.1); the longest matching key wins; an invalid entry gives one stderr warning and is ignored.
> - `"auto_confirm_after_push": true|false` — absent / `null` / `true` = on; `false` = off (silent); any
>   other value = off + one stderr warning.

### Active configuration constants

| Constant | Defined in | Value |
|---|---|---|
| `LIBRARY_MOVIES` | `main.py:19`, `mainfetch.py:29` | `C:\Media\library_movies.json` |
| `LIBRARY_SERIES` | `main.py:20`, `mainfetch.py:30` | `C:\Media\library_series.json` |
| `LIBRARY_ANIME` | `main.py:21`, `mainfetch.py:31` | `C:\Media\library_anime.json` |
| `LIBRARY_OTHERS` | `mvcommon.py:24` | `C:\Media\library_others.json` (IMP-D18 "Others"/sports category) |
| `LOCAL_ROOT` | `main.py:23`, `mainfetch.py:33` | `C:\Media` |
| `REMOTE_ROOT` | `main.py:24` | `/sdcard/Media` |
| `MKVMERGE_PATH` | `main.py:25`, `mainfetch.py:34` | `C:\Program Files\MKVToolNix\mkvmerge.exe` |
| `MAINFETCH_SCRIPT` | `main.py:26` | `mainfetch.py` |
| `DEVICE_ALIASES` | `main.py:82` | `{"movies": "FA69H0300200", "series": "FA75V0303405", "others": "<NEW_PIXEL_SERIAL>"}` — hardcoded user-edited mapping from CLI alias to ADB serial; consumed by `resolve_device()`. The `others` serial is a `<NEW_PIXEL_SERIAL>` placeholder (IMP-D18 user prerequisite — `device others` fails at adb until the real Others Pixel serial is filled in) |
| `CATEGORY_ROOTS` | `main.py:107` | `{"movies":["Movies"],"series":["Series"],"anime":["Anime"],"other":["Sports"]}` — IMP-D18 single source of truth mapping each content category to a LIST of top-level subdirs under `LOCAL_ROOT`. Every disk walker (`cmd_recover --scan`, `cmd_scan_unprepped`, `collect_reclaimable`) derives its roots from this one table; list-capable, so appending `"Documentary"` to `"other"` is a one-line data edit with NO walker code change |
| `SPLIT_DIR_NAME` | both | `_parts` |
| `CHECKSUM_DIR_NAME` | both | `checksums` |
| `RESTORE_DIR_NAME` | both | `restore` |
| `VIDEO_EXTENSIONS` | both | `('.mkv', '.mp4', '.avi', '.mov')` |
| `PARTIAL_SUFFIX` | `main.py` | `.partial` — chunks upload to `<final>.partial` then `adb shell mv` to final (atomic remote rename); auto-rollback seam |
| `MVMETA_SUFFIX` | `main.py` | `.mvmeta.json` — remote disaster-recovery sidecar name (`<base> [<short_id>].mvmeta.json`) written on full push success |
| `CHROME_PROFILES` | `mainfetch.py:41` | `movies` → `C:\Media\Utils\ChromeProfile`, `tv` → `…\ChromeProfile_TV`, `anime` → `…\ChromeProfile_Anime`, `others` → `…\ChromeProfile_Others` (IMP-D18; a user prerequisite) |
| `ID_PREFIX_PROFILE` / `DEFAULT_PROFILE` | `mainfetch.py:49-50` | `[("ani","anime"),("tv","tv"),("mov","movies"),("oth","others")]` / `"movies"` — ordered id-prefix→Chrome-profile map; the last routing layer (§8.1) |
| `CHROME_PROFILE_NAME` | `mainfetch.py:51` | `Default` (sub-profile inside the user-data-dir) |
| `SYSTEM_DOWNLOADS_FOLDER` | `mainfetch.py:52` | `~/Downloads` — read only in the fallback mode and by `fetch_cleanup` |
| `EXACT_LOCATOR_ENABLED` | `mainfetch.py:59` | `True` — P-14 kill switch for the exact-item stages |
| `FETCH_PAGE_LOAD_S` / `DIRECT_PAGE_LOAD_S` / `CHECK_PAGE_LOAD_S` | `mainfetch.py:70` / `:2588` / `:5844` | 60 s / 15 s / 60 s page-load bounds |
| `CHROME_PATH` | `mainfetch.py:100` | `C:\Program Files\Google\Chrome\Application\chrome.exe` (falls back to `(x86)`) |
| Debug port | `mainfetch.py:108` | `9222` (the crawler's `--port` must be another one) |
| `THROTTLE_RETRY_MIN` | `mainfetch.py:210` | 30 — the minutes every throttle line advises waiting before a re-run (advice only: nothing waits or retries by itself; IMP-C25 thfix, §8.8) |
| `SEARCH_RESULTS_WAIT_S` / `SEARCH_POLL_S` / `SEARCH_SUGGESTIONS_ONLY_LOOKS` | `mainfetch.py:436-443` | 10 s / 0.5 s / 4 (IMP-C35 + the "Most relevant" strip) |
| `INCOMING_DIR_NAME` / `INCOMING_MARKER` / `SHORT_INCOMING_ROOT` / `INCOMING_PATH_LIMIT` / `DISK_HEADROOM_BYTES` | `mainfetch.py:681-685` | `.mv_incoming` / `.mediavault_incoming` / `MV_incoming` / 240 / 2 GB |
| `FETCH_JOURNAL_DIR` | `mainfetch.py:689` | `~/.mediavault/fetch_journal` |
| `DOWNLOAD_STALL_S` | `mainfetch.py:1264` | 300 s — a partial not grown for this long is no longer running (the close waits for the rest) |
| `TRIGGER_CONFIRM_S` / `DIRECT_DOWNLOAD_CONCURRENCY` / `LOCATE_MAX_CANDIDATES` | `mainfetch.py:2574-2577` | 20 s / 3 / 20 |
| `CANARY_REUSE_S` / `CANARY_TRIES` | `mainfetch.py:2595-2597` | 10 s / 2 — the throttle guard (IMP-C25 thfix, §8.4): a search on the transport that listed an item, or a passed canary, this recently needs no canary; a canary that finds nothing is confirmed by one more (another item) before the run stops |
| `MANUAL_WAIT_S` | `mainfetch.py:4644` | 1800 s (30 min per object with `--manual`) |
| `CHECK_UI_PACE_S` / `CHECK_RPC_PACE_S` / `CHECK_ERROR_BREAKER` | `mainfetch.py:5840-5843` | 3 s / 0.5 s / 5 |
| `ALLOWED_RPCIDS` | `gprpc.py:61` | `lcxiM, EzkLib, swbisb, VrseUb, fDcn4b, EWgK9e, pLFTfd` (read-only allowlist, pinned) |
| `RPC_MIN_INTERVAL_S` / `RPC_JITTER_S` / `RPC_RETRIES` / `RPC_BACKOFF_S` / `CIRCUIT_BREAKER_FAILURES` / `MAX_BATCH_KEYS` | `gprpc.py:63-68` | 0.5 s / 0.25 s / 3 / (1, 2, 4) s / 5 / 50 |
| `GPHOTOS_STATE_DIR` / `GP_INVENTORY_DIR` | `mvcommon.py:57-58` | `~/.mediavault/gphotos` / `D:\MediaVault_gp_inventory` |
| `GPHOTOS_APPLY_ATTEMPTS` | `main.py:9977` | 3 — fingerprint changes before the apply engine aborts |
| `MAP_INVENTORY_MAX_AGE_H` / `MAP_RECENT_DAYS` | `main.py:11068` / `:11073` | 24 h / 7 days (not-backed-up-yet vs possible-loss) |
| `AUTO_CONFIRM_MIN_AGE_H` / `AUTO_CONFIRM_MAX_MINUTES` | `main.py:14931-14932` | 24 h (the auto-confirm's `--min-age-h`: only pushes this old are looked up) / 15 min |
| `DEVICE_CHECK_EXTRA_SERIALS` | `main.py:98` | the extra movies phone, scanned by the device check only (`movies#2`); push routing is unchanged |
| `MVMETA_SCHEMA_VERSION` / `MVMETA_UPGRADE_APPLY_ENABLED` | `main.py:5767` / `:15144` | 2 / `True` |
| Language priority (sort) | `main.py:1035` | `en=1, ta=2, hi=3, default=99` |
| Hash block size | `main.py:101`, `mainfetch.py:85`, `archive/legacy/index_file.py:22` | 65536 (64 KB) |
| Balanced-split buffer | `main.py:231, 245` | `+10 MB` |
| Fetch base timeout | `mainfetch.py:4284` (`harvest_downloads(timeout=300)`) | 300 s (self-extends while a `.crdownload` is active) |
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
- All four Chrome profiles exist and are signed in (`ChromeProfile_Others` is created by the user — until
  then `oth-` live steps are blocked by the profile pre-flight).
- `~/Downloads` is no longer part of a normal fetch (downloads land in `restore\.mv_incoming\`); it matters
  only in the fallback mode (Chrome refused the CDP redirect) and for `fetch_cleanup`.
- An inventory crawl of an account must be fresh (≤ 24 h) for `map_photos` to give verdicts for it.

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
- **Identity before bytes (IMP-C25)**: the fetch proves WHICH Google Photos item is the object (photo_id,
  content key = dedupKey, taken second, exact name) before requesting a download; today's precision /
  positional search is only the last resort (S4).
- **Dry run by default, `--apply` to write**: `map_photos`, `check_photos`, `mvmeta_upgrade`,
  `fetch_cleanup` (and the existing `enrich_metadata` / migrations) write nothing but their own run report
  unless told to.
- **Append-only sidecars + one writer**: learned facts go to append-only JSONL files first; one engine
  folds them into the library idempotently.
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
- ~~**`build_download_queue` in `mainfetch.py:413` is unreachable**~~: **FIXED** (IMP-A8, absorbed by
  IMP-C25 Step 10) — deleted together with `wait_for_download` / `automation_download_file`; the queue is
  `build_fetch_queue`.
- ~~**`fallback_query` is the same as `specific_query` for non-chunked files**~~: **SUPERSEDED** (IMP-C5,
  absorbed by IMP-C25) — the legacy attempts are unchanged on purpose (they are stage S4, digest-pinned), but
  they now run only after the exact-item stages S0a–S3u, which search by the exact uploaded name, title +
  short id, the bare short id and the user's own term, each candidate verified.
- ~~**The harvester re-hashes the same file every attempt**~~: **FIXED** (IMP-B10, absorbed by IMP-C25 Step
  10) — a file is hashed once per run (path + size + `mtime_ns`).
- **Known IMP-C25 limits (not bugs, tracked):** `gprpc.STATUS_NOT_FOUND = 5` is unverified (a photo_id is
  called stale only when the item page agrees); original-quality cannot be read from the RPC records yet
  (never claimed); fetch journals under `~/.mediavault/fetch_journal/` are never pruned; `warm_profiles` runs
  no profile pre-flight; the shared close line says "this fetch" also inside `resolve_photos`; a fetch holds
  the lock without a heartbeat, so one running > 1 h can be reclaimed as stale; the live download triggers
  and the CDP redirect are verified only at 🚦 R2 (results pending R2/R3).
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
  (`main.py:10399`) instead of `sys.executable` (still true; the IMP-C25 `check_photos` and auto-confirm
  children do use `sys.executable`). On a machine where
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
- ~~`build_download_queue` / `wait_for_download` / `automation_download_file` dead stubs~~ **DONE**
  (IMP-A8, deleted in IMP-C25 Step 10).
- `main.py` (18,294 lines) and `mainfetch.py` (6,313 lines) keep growing as single files; the IMP-C25
  sections are banner-delimited (grep "GOOGLE PHOTOS IDENTITY" / "IMP-C25 STEP").

### Security / privacy concerns

- The `archive/unrelated/a.json` file contains API keys / bot tokens
  for an unrelated tool. It is gitignored, but **rotate those keys if
  the repo was ever pushed to a remote before the gitignore landed.**
- Selenium attaches to a debug port without any auth on
  `127.0.0.1:9222` — anything local on the machine can issue commands
  to the browser session while it's running. Acceptable for a
  single-user PC; risky on a shared machine.
- **IMP-C25 private data.** Photo ids, dedupKeys, inventory and mapping reports identify the user's cloud
  items: they live only in `~/.mediavault/gphotos/` and `D:\MediaVault_gp_inventory\`, never in the repo
  (P-15; the tests use synthetic `AF1QipFAKE…` ids). Minted download links are bearer credentials while valid
  (~1 h): memory only, redacted wrapper, never printed / logged / journaled (P-20). RPC page tokens never
  leave the browser's own request. Every Google Photos call is read-only (§8.9).

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
  ceiling on throughput. Since IMP-C25 a batch whose objects live in several accounts runs one Chrome
  session per account, one after another; direct downloads run at most 3 at once per entry.
- A full-library `check_photos` sweep is paced (≥ 0.5 s per RPC call, 3 s after a web-page object) and
  resumable (`--max-minutes`, re-run to continue) — hours, spread over sessions, by design (account safety).
- Once the learned sidecar is non-empty, every `main.py fetch` re-reduces it and loads the library for the
  post-fetch merge (seconds at full-library scale).

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
7. ~~**Better fallback search**~~ — **DONE** (IMP-C5, absorbed by IMP-C25): the exact-item waterfall
   (§8.4) runs before today's two attempts.
7a. **Resumable direct transfer** (suggested after IMP-C25): if `locate --probe-direct` shows the minted
   original-download link answers without browser cookies, the byte transfer could move from Chrome to
   Python with HTTP Range resume (links stay memory-only, P-20).
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
    the latest documentation. (IMP-C25 instead uses the web app's own read-only internal calls from the
    signed-in page — §8.9 — and still downloads through the automation Chrome.)
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
| Which Chrome profile / account an object is fetched in | `mainfetch.py:_object_account` (5290), `_account_groups` (5345); overrides `_account_overrides` (5232) |
| How the exact item is found | `mainfetch.py:locate_object` (3479), the stages (3107-3238), `_run_locator_phase` (3819) |
| How a located item is downloaded | `mainfetch.py:_direct_trigger` (3665), `_shift_d_trigger` (3787) |
| Today's legacy search (S4) | `mainfetch.py:trigger_download` (516) — digest-pinned |
| Where downloads land / cleanup | `mainfetch.py:_prepare_downloads` (1097), `_finish_downloads` (1157), `_startup_cleanup` (1546), `fetch_cleanup_plan` (1640) |
| How downloaded files are routed | `mainfetch.py:harvest_downloads` (4284) |
| How Chrome is closed | `mainfetch.py:_close_session` (2035), `_end_run` (2104), `mark_profile_clean_exit` (1400) |
| Which Google Photos RPCs may be called | `gprpc.py:ALLOWED_RPCIDS` (61), `call` (341) |
| Who writes the `gphotos` block | `main.py:gphotos_apply` (10240) — the only writer; post-fetch `merge_learned_gphotos` (10347) |
| Identity primitives (pin, iterators, stores) | `mvcommon.py` "GOOGLE PHOTOS IDENTITY" section (from ~950) |
| Capture at prep / push | `gpcapture.py`; hooks `main.py:1609`, `:5613`, `:6333`, `:6972`, `:6988` |
| map / check / resolve / mvmeta_upgrade / auto-confirm | `main.py:cmd_map_photos` (12830), `cmd_check_photos` (14263), `cmd_resolve_photos` (14834), `cmd_mvmeta_upgrade` (15720), `after_push_command` (15091) |
| What ID prefix goes to which library file | `main.py:save_library` (75-84), `tools/migrate_lib.py:38-45` |
| Auto-parent / season-map creation | `main.py:cmd_prep` (333-364) |
| Episode-number auto-detection from filenames | `main.py:cmd_prep_season` (489-507) |
| Episode-range filter (group ops) | `main.py:cmd_push_group` (731-732), `cmd_restore_group` (997-998), `cmd_prep_push_rep_season` (1303); `mainfetch.py:resolve_targets` (391-392). Note: `cmd_push_group` lacks the `x\d+$` pattern — see §16. |
| Chrome profile per category | `mainfetch.py:ID_PREFIX_PROFILE` (49): `mov` → movies, `tv` → tv, `ani` → anime, `oth` → others |
| Half-episode (`.5`) regex support | `main.py:336, 491, 738, 1004, 1310` (point-in-time refs); `mvcommon.episode_num_from_id` |
| Adding a new subcommand | Append to the `if/elif` chain of the dispatcher (`main.py:17646` onward) |

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

*Last updated 2026-11-03 (IMP-C25 follow-up "mpfix", from the R3 map runs of 2026-10-28): map_photos' dry-run apply
preview counts what `--apply` changes (records already on record are not new; a run that records nothing previews
the sidecar too; the `--repair split-info` chunks on their own line) and the `uploaded-after-crawl` verdict (§8.10
step 4 and the preview paragraph; §13). The `main.py` refs this change shifted (+1 at the map constants … +118 after
the map section) were re-pointed and re-verified across the document: §5, §6.5, §8.10, §10 Stage 4, §11, §14, §18.*

*Earlier — 2026-10-28 (IMP-C25 follow-up "thfix", from the R3 dry runs of 2026-10-27): the throttle guard — a
Google Photos search that answers nothing is a "not found" only while a canary shows the search works (§8.4,
`zero_check`), `PhotosThrottledError` and the page classifier that no longer reads Google's throttle bounce as
"logged out" (§8.3, §8.7, §8.8 with the per-command lines), check_photos' stop reason `throttled` and `unverified`
verdicts, resolve_photos' throttle line (§8.10), the warm-up's `THROTTLED` (§9.1b), the crawler's exit 4 (§9.3),
`THROTTLE_RETRY_MIN` / `CANARY_REUSE_S` / `CANARY_TRIES` (§14); map_photos' `not-listed` verdict (§8.10 step 4). The
refs this change shifted (`mainfetch.py` +83 … +321 after `SessionExpiredError`, `gprpc.py` +1 / +4, `main.py`
+2 … +82 from the map section on) were re-verified across the document, including the §11 and §18 tables.*

*Earlier — 2026-10-27 (IMP-C25 follow-up "auto-confirm checks only objects old enough", the user's finding —
`docs/feature-fetch-datetime/DECISIONS.md` §1g): `check_photos --min-age-h H` (§5 table, §8.10) and the auto-confirm
child `check_photos pending --apply --min-age-h 24 --max-minutes 15` with its due-only header line (§5 note, §8.10,
§11, §14). The `main.py` refs this change shifted (+24 … +40 after the check section) were re-verified in §5, §6.5,
§8.10, §10 Stage 4, §11, §14 and §18.*

*Earlier — 2026-10-20 (IMP-C25 Step 16, branch `feature/imp_c25_fetch_exact_gp_item`): exact Google
Photos item fetch — §1 overview, §3 layout (gpweb / gprpc / gpcapture / gp_inventory, the `~/.mediavault`
state tree), §5 new commands (`map_photos`, `check_photos`, `resolve_photos`, `mvmeta_upgrade`,
`fetch_cleanup`, `verify_restore tempdir`, `--manual`, the auto-confirm hook, `mainfetch.py locate/check`),
§6.3 the `gphotos` block, §6.5 capture v2 + mvmeta v2 + `mvmeta_upgrade`, §7.5 capture, §8 rewritten (four
profiles + per-object routing, the S4 drift fix, the locator, download hygiene + Chrome lifecycle, manual /
resume, the read-only RPC section §8.9, the identity commands §8.10), §9.1b / §9.3, §10 Stage 4, §11, §12,
§12a rollback-neutral addendum, §13 C25 tests, §14 constants + mvconfig keys, §16 / §17 C5 / A8 / B10 struck,
§18. Line refs in the touched sections verified against the code at de5620b; `main.py:NNN` refs elsewhere
(e.g. the §12a PONR table, the 2026-05-25 audit list, the §14 historical "Defined in" column) are
point-in-time. Live R2 / R3 results: pending.*

*Earlier — 2026-06-12 (fable-review session, branch `feature_fable_review`):
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
