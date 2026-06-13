# MediaVault

## Overview

MediaVault is a single-user, Windows-only command-line system for archiving and
restoring large ripped video files (movies, TV series, anime). It is built and
maintained by one developer for one developer. The codebase is intentionally
small: two active Python scripts (`main.py` and `mainfetch.py`) plus a one-shot
migration helper.

The trick MediaVault exploits is mundane but effective. A Google Pixel phone
configured to auto-back-up a chosen folder to Google Photos at "original
quality" is, in practice, free cold storage for arbitrary `.mkv` and `.mp4`
files. The `prep` -> `push` workflow indexes a file, splits it into ~10 GB
chunks with `mkvmerge`, and pushes the chunks to `/sdcard/Media/...` on a Pixel
over USB ADB. The phone's Photos app then uploads them to the cloud
unattended. Once the upload finishes the `replace` command swaps the local
original for a tiny dummy placeholder file of the same name, recovering all of
its disk space while keeping Plex/Kodi library entries intact. To watch the
file again, `fetch` drives Chrome via Selenium to download the chunks back
from `photos.google.com`, and `restore` merges them, verifies the SHA256s, and
puts the file back where it was.

## What it does

- `prep` — index a local file, compute its SHA256, extract MediaInfo specs,
  write sidecar files (`uid`, `<short_id>.sha256`) and record everything in
  the appropriate library JSON.
- `push` — optionally split into balanced chunks via `mkvmerge`, then `adb push`
  the result to `/sdcard/Media/...` on a Pixel phone. Each chunk is uploaded to
  a temporary `<name>.partial` and then atomically renamed with `adb shell mv`
  only after the transfer succeeds, so a mid-push failure never leaves a
  complete-named chunk for Google Photos to ingest. On full success a
  `<base> [<short_id>].mvmeta.json` sidecar is written next to the chunks on the
  phone (mirrors `split_info`) for disaster-recovery library rebuild. A push that
  would exceed the target volume's free space hard-stops before splitting (a
  **disk pre-flight**: deferred needs 1X, eager 2X, plus a `max(1%, 2 GB)`
  buffer; a season sizes to its largest splitting episode). The optional
  `tempdir <path>` token redirects the `_parts/` chunks (and the eager merge
  temp) to another volume; `checksums/` and the rollback journal stay put.
- (out of band) — the phone's Google Photos app auto-uploads the new files at
  original quality. MediaVault is not involved in this step.
- `replace` — once `uploaded` is true, swap the original for a tiny dummy
  placeholder of the same name and free the local disk space.
- `fetch` — Selenium attaches to a logged-in Chrome session, searches Google
  Photos for the embedded short-id, and triggers the "download original"
  shortcut for every chunk into `~/Downloads`.
- `restore` — re-merge the chunks with `mkvmerge --deterministic` (a byte-identical
  merge), SHA256-verify against the library, and move the resulting file back into
  the original folder. For split entries the first restore **blesses** that
  deterministic merged hash as the canonical whole-file hash (`re_hashed`); later
  restores **verify** against it and alarm on mismatch (without crossing the
  restore point-of-no-return). Use the `rehash` token at `push` to bless eagerly
  instead (canonical promoted into the entry at `replace`).

### Failure handling (auto-rollback)

Every multi-step command is wrapped by a single **auto-rollback** mechanism. A
failure *before* a command's point-of-no-return rolls back to the exact
pre-command state (removing only what that run created); a failure *at/after* it
hard-fails with a message naming the existing command that resumes/repairs it
(`fetch_restore <id>`); a `push` failure is treated as resumable (it leaves the
partial upload and prints `push <id>`); and a season batch keeps completed
episodes and prints how to resume the rest. It is backed by a durable on-disk
journal (`.mediavault_txn.json`) that survives a hard process kill, so an
interrupted rollback can be finished afterward via `recover_journal`. See
[`ARCHITECTURE.md` §12a](ARCHITECTURE.md) and
[`docs/feature-auto-rollback/ROLLBACK_MECHANISM.md`](docs/feature-auto-rollback/ROLLBACK_MECHANISM.md).

## Tech stack

- **Python 3.7+** — `main.py` relies on guaranteed dict insertion order
  (`cmd_sort`). No type hints, no `argparse`, no class hierarchies; just
  procedural `cmd_*` functions plus manual `sys.argv` walking.
- **pip packages**: `pymediainfo`, `selenium`, `webdriver-manager`, and
  `requests`. Note that `requests` is used by `main.py` (`cmd_set_poster`,
  `cmd_set_fanart`) but is missing from `requirements.txt`; install it
  explicitly. `undetected-chromedriver` is listed in `requirements.txt` but
  never imported in the active codebase.
- **External binaries**: `mkvmerge` from MKVToolNix; `adb` from the Android
  Platform Tools; `chrome.exe` launched in remote-debugging mode; and
  `chromedriver`, fetched and cached automatically by `webdriver-manager`.
- **No daemon, no API, no database** — three JSON files on disk plus per-media
  sidecar files are the entire persistent state.

## Requirements / prerequisites

- Windows 10 or 11. All paths in source are hardcoded as Windows absolute
  paths (`C:\Media\...`, `C:\Program Files\...`). No POSIX support.
- Python 3.7 or newer, with `pip` available.
- MKVToolNix installed at `C:\Program Files\MKVToolNix\mkvmerge.exe` (or
  symlinked to that path).
- Google Chrome installed at
  `C:\Program Files\Google\Chrome\Application\chrome.exe`. The `(x86)`
  fallback is also accepted.
- A Google Pixel phone connected over USB with ADB debugging authorised and
  the Google Photos app configured to back up the `/sdcard/Media` folder at
  original quality.
- Two persistent Chrome user-data directories under `C:\Media\Utils\`:
  - `ChromeProfile` — signed into the Google account that holds your movies.
  - `ChromeProfile_TV` — signed into the Google account that holds your TV
    series and anime.
  Each profile must be signed in manually at least once before MediaVault can
  attach to it.
- The three library JSON files at `C:\Media\library_movies.json`,
  `C:\Media\library_series.json`, and `C:\Media\library_anime.json`. These
  are created on first `prep` if missing.

## Installation

```
git clone https://github.com/harinathsrinivas/MediaVault.git
cd MediaVault
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
pip install requests webdriver-manager   # both missing from requirements.txt —
                                         # requests is required by main.py,
                                         # webdriver-manager by mainfetch.py
pip install -r requirements-dev.txt      # pytest (only needed to run the test suite)
```

## Usage / CLI reference

All operations are invoked as `python main.py <subcommand> ...`. There is no
help text and no `--help` flag; this table is the reference.

| Subcommand             | Signature                                                                                                                           | Description                                                                                                                                            |
| ---------------------- | ----------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `prep`                 | `prep [id] [filepath]`                                                                                                              | Index a new local file, compute SHA256, write sidecars                                                                                                 |
| `prep_season`          | `prep_season [base_id] [folder]`                                                                                                    | Batch-prep an entire season folder                                                                                                                     |
| `prep_push_rep`        | `prep_push_rep [id] [filepath] [SIZE_MB/SIZE_GB/COUNT val] [device <id_or_name>] [rehash] [tempdir <path>]`                         | Full pipeline (prep -> push -> replace) for one file                                                                                                   |
| `prep_push_rep_season` | `prep_push_rep_season [id] [folder] [SIZE_MB/SIZE_GB/COUNT val] [episodes <range>] [device <id_or_name>] [rehash] [tempdir <path>]` | Sequential full pipeline for a whole season                                                                                                            |
| `push`                 | `push [id] [SIZE_MB/SIZE_GB/COUNT val] [chunks 1-4] [device <id_or_name>] [rehash] [tempdir <path>]`                                | Split and ADB-push to phone (`rehash` = eager canonical re-hash; `tempdir` = off-volume chunks)                                                        |
| `push_group`           | `push_group [id] [SIZE_..] [episodes 1-3] [device <id_or_name>] [rehash] [tempdir <path>]`                                          | Push a season group                                                                                                                                    |
| `replace`              | `replace [id]`                                                                                                                      | Swap original with a tiny valid video file placeholder (requires ffmpeg)                                                                               |
| `replace_group`        | `replace_group [id]`                                                                                                                | Replace a season group                                                                                                                                 |
| `repair_dummies`       | `repair_dummies [id_prefix]`                                                                                                        | Regenerate any archived-entry dummy on disk to the current 10 KB video spec (idempotent — re-runs are safe)                                            |
| `fetch`                | `fetch [id] [episodes <range>]`                                                                                                     | Selenium-download from Google Photos                                                                                                                   |
| `fetch_restore`        | `fetch_restore [id] [episodes <range>]`                                                                                             | Fetch then restore in one command                                                                                                                      |
| `restore`              | `restore [id]`                                                                                                                      | Re-merge chunks, verify SHA256, place file back                                                                                                        |
| `restore_group`        | `restore_group [id]`                                                                                                                | Restore a season group                                                                                                                                 |
| `verify_restore`       | `verify_restore [id]`                                                                                                               | Dry-run hash check of restore/ folder contents                                                                                                         |
| `check`                | `check [id]`                                                                                                                        | Re-hash file in place and compare to library                                                                                                           |
| `scan_unprepped`       | `scan_unprepped`                                                                                                                    | Find video files on disk not yet in any library                                                                                                        |
| `local_status`         | `local_status [limit_size]`                                                                                                         | Show pending uploads with optional bin-packing                                                                                                         |
| `set_search`           | `set_search [id] [term]`                                                                                                            | Override the Google Photos search term                                                                                                                 |
| `set_poster`           | `set_poster [id] [url]`                                                                                                             | Download and save poster.jpg into the media folder                                                                                                     |
| `set_fanart`           | `set_fanart [id] [url]`                                                                                                             | Download and save fanart.jpg into the media folder                                                                                                     |
| `set_uploaded`         | `set_uploaded [id]`                                                                                                                 | Force-mark as uploaded (emergency rescue)                                                                                                              |
| `sort`                 | `sort`                                                                                                                              | Re-sort all library JSONs by language -> year -> size                                                                                                  |
| `recover`              | `recover [id\|folder]` (or `recover --scan`)                                                                                        | Finish an interrupted auto-rollback for a media folder (resolves by id or path); `--scan` reports leftover `.mediavault_txn.json` journals (read-only) |

The Selenium fetcher can also be invoked directly:

```
python mainfetch.py fetch <id> [episodes <range>]
```

`main.py fetch` is a thin wrapper that spawns exactly that command.

> **CRITICAL — the `episodes` keyword is a literal trigger.**
> The word `episodes` must appear as its own argument immediately before the
> range value.
> Correct: `python main.py fetch tv-TheBoys episodes 1-3`
> Wrong:   `python main.py fetch tv-TheBoys 1-3` (range silently ignored)

### Pinning a push to a specific phone (multi-device)

All four push-flavoured subcommands (`push`, `push_group`, `prep_push_rep`,
`prep_push_rep_season`) accept an optional `device <id_or_name>` keyword that
selects a specific ADB device. The value is either a raw ADB serial or a
short alias defined in the hardcoded `DEVICE_ALIASES` dict near the top of
`main.py` (currently `movies` -> `FA69H0300200`, `series` -> `FA75V0303405`).
When omitted, ADB picks the connected device on its own (and errors if more
than one is connected without `-s`).

```
python main.py push mov-en-2024-inception SIZE_MB 9900 device movies
python main.py push_group tv-en-2016-strangerthings-s01 SIZE_MB 9900 episodes 1-3 device series
python main.py prep_push_rep mov-en-2025-f1 "C:\Media\Movies\English\F1\F1.mkv" SIZE_MB 9900 device movies
python main.py prep_push_rep_season tv-en-2024-show-s01 "C:\Media\Series\Show S01" SIZE_MB 9900 episodes 1-5 device series
```

Aliases are edited in source (matching the hardcoded-config convention noted
in [`ARCHITECTURE.md` §14](ARCHITECTURE.md)). An unknown alias falls through
to ADB as a raw serial, so any serial works without registration.

## Manual ID conventions

Manual IDs are free-form strings that the user assigns. The first three
characters select which library JSON the entry lands in. Conventional shapes:

- **Movies**: `mov-<lang2>-<year>-<slug>` e.g. `mov-en-2024-inception`
- **TV series**: `tv-<lang2>-<year>-<slug>-s<NN>e<MM>` e.g.
  `tv-en-2016-strangerthings-s01e03`
- **Anime**: `ani-<lang2>-<year>-<slug><NN>` (no `e` separator before the
  episode number) e.g. `ani-ja-2006-deathnote07`

Season-map parent entries are auto-created during `prep` by stripping the
trailing episode segment.

## File layout

```
MediaVault/
├── main.py                  # Active — main CLI (prep/push/replace/restore/fetch/sort/recover/...)
├── mainfetch.py             # Active — Selenium fetch from Google Photos
├── mvcommon.py              # Active — shared library I/O + hashing constants/helpers (imported by both)
├── requirements.txt         # pymediainfo, selenium, undetected-chromedriver (see install note above)
├── requirements-dev.txt     # pytest
├── ARCHITECTURE.md          # Full engineering reference
├── ARCHITECTURE_GRAPH.md    # Graph views (Mermaid) of the architecture
├── README.md                # This file
├── apple_tv_ui_roadmap.md   # 2026-05 Jellyfin UI design (superseded — see improvements/ROADMAP_END_GOAL.md)
├── improvements/            # The backlog + direction "brain" — start at improvements/README.md
│   ├── README.md            #   index of this folder
│   ├── PRIORITY.md          #   always-current task ordering ("what to do next"; critical bugs first)
│   ├── improvement_details.md  #   IMP-XN operating manual
│   ├── improvements_tier*.md   #   tracked improvement tasks (tiers A–H, R, S, U, X)
│   ├── ROADMAP_END_GOAL.md  #   the phased couch-vault roadmap
│   └── RESEARCH_*.md, JELLYFIN_SETUP_GUIDE.md, BLOCKERS_AND_MOONSHOTS.md  # durable research/direction
├── docs/                    # Per-feature plans/decisions, conventions, testing strategy, graphs
│   └── README.md            # Master index of all documentation
├── tests/                   # pytest suite (rollback, push, replace, restore, rehash, parsing, recover, ...)
├── tools/
│   ├── migrate_lib.py       # One-shot: library.json → three category files
│   └── migrate_rehash_flag.py  # One-shot (PR #20): stamp re_hashed=false on split entries
└── archive/                 # Historical snapshots — NOT used at runtime
    ├── main/                # Older versions of main.py
    ├── mainfetch/           # Older versions of mainfetch.py
    ├── legacy/              # index_file.py and media_library.json (pre-dates current design)
    └── transcripts/         # Session transcript artifacts
```

The three live library JSON files (`library_movies.json`, `library_series.json`,
`library_anime.json`) live **outside** the repo at `C:\Media\` and are never
committed. Media files themselves live under `C:\Media\Movies\`,
`C:\Media\Series\`, and `C:\Media\Anime\`.

## External dependencies

| Tool | Default path | Purpose |
|---|---|---|
| `mkvmerge` | `C:\Program Files\MKVToolNix\mkvmerge.exe` | Split `.mkv` into chunks; re-merge during restore |
| `adb` | resolved from PATH (Android Platform Tools) | Push chunks to Pixel phone via USB |
| `chrome.exe` | `C:\Program Files\Google\Chrome\Application\chrome.exe` | Launched with `--remote-debugging-port=9222` for Selenium attach |
| `chromedriver` | auto-managed by webdriver-manager (`~/.wdm/`) | Selenium WebDriver protocol client |
| `MediaInfo.dll` | bundled with the `pymediainfo` wheel | Extract video/audio/subtitle metadata |
| `ffmpeg` | `C:\Users\harin\AppData\Roaming\Emby-Server\system\ffmpeg.exe` or system PATH | Generate valid tiny video placeholder; required by `replace` and `repair_dummies` |

All paths are hardcoded constants at the top of each source file. There is no
config file, no environment variable lookup, and no command-line override for
any of them. To re-target the system, edit the constants directly.

## Architecture deep-dive

For the full engineering reference including data model, state machine,
balanced-split algorithm, and Selenium harvester design, see
[`ARCHITECTURE.md`](ARCHITECTURE.md) — graph views in
[`ARCHITECTURE_GRAPH.md`](ARCHITECTURE_GRAPH.md) and an interactive version at
`docs/architecture-graph/graph.html`. **The master index of ALL documentation
is [`docs/README.md`](docs/README.md).** The backlog, priority list, and the
forward roadmap (couch-vault / Jellyfin end goal) live in
[`improvements/`](improvements/) — start at
[`improvements/README.md`](improvements/README.md), roadmap at
[`improvements/ROADMAP_END_GOAL.md`](improvements/ROADMAP_END_GOAL.md).

## Development / agentic workflow

Non-trivial changes to MediaVault are built with a multi-agent Claude Code
workflow (planner → orchestrator → executors, with a git-agent and judge),
defined in [`.claude/agents/`](.claude/agents/). As of 2026-05-30 these agents
run on the Opus 4.8 / effort-tier model: each agent declares an `effort:` level
(`low`…`max`) and the planner annotates an advisory `[effort: …]` per step. See
[`ARCHITECTURE.md` §19](ARCHITECTURE.md) for the roster, flow diagram, and the
effort design, [`.claude/AGENT_WORKFLOW_NOTES.md`](.claude/AGENT_WORKFLOW_NOTES.md)
for the migration record, and `improvements_tierH.md` for the tracked task.

## Status / disclaimers

- Solo-developer project, actively used in production by the author.
- Windows 10/11 only — paths, ADB, and Chrome integration are hardcoded for
  Windows. There is no plan to port to macOS or Linux.
- Automated tests (run with `pytest -q`) cover the auto-rollback matrix, push
  (`.partial`+mv protocol, retry, remote verify, mock-device round-trip),
  replace, restore quarantine, the deterministic re-hash feature, episode
  parsing (incl. combined episodes), the `recover` CLI, the `multi_ep_alias`
  consumers, and the shared helpers. There is also a fast (~8-10s) full-command
  **smoke suite** — `pytest tests/smoke -q` — that drives every command against
  tiny fixtures (including a `multi_ep_alias` library sweep); run it as the
  pre-PR cross-command gate alongside `pytest -q`. See
  `docs/testing-strategy.md`. Everything else — notably the live Selenium
  fetch — is "tested by use"; the legacy snapshots under `archive/` serve as
  informal regression baselines.
- The `undetected-chromedriver` package is listed in `requirements.txt` but
  not imported anywhere; `requests` is imported by `main.py` but not listed
  in `requirements.txt`. Both are known issues documented in
  `ARCHITECTURE.md` section 16.
- No support is offered. Issues and pull requests are unlikely to be acted on.

## License

No license file is included. Treat as all-rights-reserved unless the maintainer
states otherwise.
