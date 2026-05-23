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
  the result to `/sdcard/Media/...` on a Pixel phone.
- (out of band) — the phone's Google Photos app auto-uploads the new files at
  original quality. MediaVault is not involved in this step.
- `replace` — once `uploaded` is true, swap the original for a tiny dummy
  placeholder of the same name and free the local disk space.
- `fetch` — Selenium attaches to a logged-in Chrome session, searches Google
  Photos for the embedded short-id, and triggers the "download original"
  shortcut for every chunk into `~/Downloads`.
- `restore` — re-merge the chunks with `mkvmerge`, SHA256-verify against the
  library, and move the resulting file back into the original folder.

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
pip install requests   # missing from requirements.txt — required by main.py
```

## Usage / CLI reference

All operations are invoked as `python main.py <subcommand> ...`. There is no
help text and no `--help` flag; this table is the reference.

| Subcommand | Signature | Description |
|---|---|---|
| `prep` | `prep [id] [filepath]` | Index a new local file, compute SHA256, write sidecars |
| `prep_season` | `prep_season [base_id] [folder]` | Batch-prep an entire season folder |
| `prep_push_rep` | `prep_push_rep [id] [filepath] [SIZE_MB/SIZE_GB/COUNT val]` | Full pipeline (prep -> push -> replace) for one file |
| `prep_push_rep_season` | `prep_push_rep_season [id] [folder] [SIZE_MB/SIZE_GB/COUNT val] [episodes <range>]` | Sequential full pipeline for a whole season |
| `push` | `push [id] [SIZE_MB/SIZE_GB/COUNT val] [chunks 1-4]` | Split and ADB-push to phone |
| `push_group` | `push_group [id] [SIZE_..] [episodes 1-3]` | Push a season group |
| `replace` | `replace [id]` | Swap original with tiny dummy placeholder |
| `replace_group` | `replace_group [id]` | Replace a season group |
| `fetch` | `fetch [id] [episodes <range>]` | Selenium-download from Google Photos |
| `fetch_restore` | `fetch_restore [id] [episodes <range>]` | Fetch then restore in one command |
| `restore` | `restore [id]` | Re-merge chunks, verify SHA256, place file back |
| `restore_group` | `restore_group [id]` | Restore a season group |
| `verify_restore` | `verify_restore [id]` | Dry-run hash check of restore/ folder contents |
| `check` | `check [id]` | Re-hash file in place and compare to library |
| `scan_unprepped` | `scan_unprepped` | Find video files on disk not yet in any library |
| `local_status` | `local_status [limit_size]` | Show pending uploads with optional bin-packing |
| `set_search` | `set_search [id] [term]` | Override the Google Photos search term |
| `set_poster` | `set_poster [id] [url]` | Download and save poster.jpg into the media folder |
| `set_fanart` | `set_fanart [id] [url]` | Download and save fanart.jpg into the media folder |
| `set_uploaded` | `set_uploaded [id]` | Force-mark as uploaded (emergency rescue) |
| `sort` | `sort` | Re-sort all library JSONs by language -> year -> size |

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
├── main.py              # Active — main CLI (prep/push/replace/restore/fetch/sort/...)
├── mainfetch.py         # Active — Selenium fetch from Google Photos
├── requirements.txt     # pymediainfo, selenium, undetected-chromedriver
├── ARCHITECTURE.md      # Full engineering reference
├── README.md            # This file
├── tools/
│   └── migrate_lib.py   # One-shot helper: library.json → three category files
└── archive/             # Historical snapshots — NOT used at runtime
    ├── main/            # Older versions of main.py
    ├── mainfetch/       # Older versions of mainfetch.py
    ├── legacy/          # index_file.py and media_library.json (pre-dates current design)
    └── transcripts/     # Session transcript artifacts
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

All paths are hardcoded constants at the top of each source file. There is no
config file, no environment variable lookup, and no command-line override for
any of them. To re-target the system, edit the constants directly.

## Architecture deep-dive

For the full engineering reference including data model, state machine,
balanced-split algorithm, and Selenium harvester design, see
[`ARCHITECTURE.md`](ARCHITECTURE.md).

## Status / disclaimers

- Solo-developer project, actively used in production by the author.
- Windows 10/11 only — paths, ADB, and Chrome integration are hardcoded for
  Windows. There is no plan to port to macOS or Linux.
- No automated tests. The project is "tested by use"; the legacy snapshots
  under `archive/` serve as informal regression baselines that can be diffed
  against the active files when something breaks.
- The `undetected-chromedriver` package is listed in `requirements.txt` but
  not imported anywhere; `requests` is imported by `main.py` but not listed
  in `requirements.txt`. Both are known issues documented in
  `ARCHITECTURE.md` section 16.
- No support is offered. Issues and pull requests are unlikely to be acted on.

## License

No license file is included. Treat as all-rights-reserved unless the maintainer
states otherwise.
