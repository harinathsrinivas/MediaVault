# Improvements — Tier E · Integration & Workflow Features

> Features that connect MediaVault to the surrounding ecosystem (the phone, the cloud, media servers, external metadata, the user's actual viewing habits). Higher payoff than Tier D but generally bigger effort. Several feed directly into the Apple TV UI roadmap ([[project_future_apple_tv_ui]]).

> **Cross-cutting context:**
> - User's phone is a Pixel with Google Photos auto-upload of `/sdcard/Media/`. After upload, the phone's local copy is NOT automatically cleaned by MediaVault — it accumulates until manually deleted.
> - User has TWO Google accounts in play (movies vs series/anime), each with its own `ChromeProfile*` and presumably its own paid Google One storage tier.
> - Folders contain `poster.jpg` / `fanart.jpg` IF the user has manually run `set_poster` / `set_fanart`. Most entries do not have these.
> - The user runs Plex (mentioned in the architecture's `cmd_replace` retry rationale). Other media servers (Jellyfin, Kodi) are also plausible deployments.
> - `parse_metadata_from_id` (lines 176-183) is intentionally naive — extracts only `year`. All other metadata (title, genre, cast, synopsis) is empty.

---

## IMP-E1: Subtitle pre-extraction before archive

- Category: other
- Priority: medium
- Files: new helper in `main.py` invoked by `cmd_replace` (755-806); uses `mkvextract` binary from MKVToolNix
- Current behavior: `cmd_replace` deletes the original file. All embedded subtitle tracks go with it. If the user later restores and wants subs, they have to wait for the full restore.
- Proposed change:
  - Before `cmd_replace` destroys the original, run `mkvextract tracks <file> N:<folder>/subs/<lang>.srt` for each subtitle track listed in `entry.tech_spec.subtitles`.
  - Store under `<folder>/subs/`.
  - Subtitles are tiny (typically <500 KB per file, multiple languages).
  - Subtitle files survive the archive — they're committed-to-disk forever.
  - Optional config to disable for certain languages.
  - On restore, do NOT extract again — the existing `subs/` files are authoritative.
- Rationale: Subtitles are valuable independently of the video. Useful for: quick dialogue search (IMP-F7), Plex reading-mode-style features, translating-while-the-file-is-archived, hard-of-hearing accessibility while waiting for a restore.
- Goal: Subtitles always available locally, even for archived content.
- Effort estimate: small
- Status: pending

---

## IMP-E2: Pre-archive low-resolution preview variant

- Category: other
- Priority: low
- Files: new helper invoked by `cmd_replace`; uses `ffmpeg`
- Current behavior: Archived content is fully gone from local disk except for the dummy. To "preview" a movie the user must do a full restore (potentially 70 GB).
- Proposed change:
  - Before `cmd_replace`, optionally transcode a 480p ~500 MB H.264 preview:
    - `ffmpeg -i <original> -vf scale=-2:480 -c:v libx264 -crf 28 -c:a aac -b:a 96k <folder>/preview.mp4`
    - Time cost: ~5-10× realtime on a modest GPU with NVENC; ~20× realtime on CPU.
  - Storage cost: ~500 MB per archived item × 100 items = ~50 GB. Significant but acceptable for many users.
  - Config-gated (off by default). Per-entry override via `--no-preview` / `--preview`.
  - Preview becomes the file Plex sees while content is archived — instant playback for low-stakes viewing.
- Rationale: Eliminates the "I just want to quickly check what this movie is" friction. Hand-in-hand with the future Apple TV UI where instant playback matters.
- Goal: Every archived item has a low-bitrate previewable copy on disk. Full quality is on-demand.
- Effort estimate: medium
- Status: pending

---

## IMP-E3: External metadata enrichment (TMDB / TheTVDB / AniDB)

- Category: other
- Priority: high
- Files: `main.py` — `parse_metadata_from_id` (176-183) extended; new helpers; new dependencies (`requests` already used, plus API keys)
- Current behavior: `parse_metadata_from_id` only extracts year. `metadata.title` is just the manual_id itself. `metadata.genre` is always `[]`. There's no synopsis, no rating, no cast, no real title.
- Proposed change:
  - At prep time (and via a backfill command `enrich_metadata`), look up the title against:
    - TMDB (free API, key required) — movies and TV.
    - TheTVDB — TV alternative.
    - AniDB or AniList GraphQL — anime.
  - Populate `entry.metadata`:
    - `title` (canonical from TMDB)
    - `original_title` (for non-English content)
    - `genre` (list)
    - `synopsis`
    - `rating`
    - `runtime_mins`
    - `cast` (top 5)
    - `tmdb_id`, `tvdb_id`, `anidb_id` for future cross-reference
  - Auto-download `poster.jpg` and `fanart.jpg` from the lookup (replaces the manual `set_poster`/`set_fanart` for the 95% case).
  - Cache lookups under `~/.mediavault/cache/metadata/<id>.json` to avoid hitting APIs on every prep.
  - Config keys for API keys; `enrich_metadata --library all` for backfill.
- Rationale: This is the **HARD prerequisite for the future Apple TV UI**. A grid of tiles needs real titles, posters, fanart, ratings. Without metadata enrichment, the UI is a wall of slugs.
- Goal: Every entry has rich metadata sourced automatically from authoritative APIs.
- Effort estimate: large (multiple APIs, caching, error handling)
- Status: pending

---

## IMP-E4: Watch-state tracking

- Category: other
- Priority: medium
- Files: `main.py` — entry schema additions; new helper to ingest mpv state
- Current behavior: No watch-state. MediaVault knows what's in the library; it does not know what's been watched.
- Proposed change:
  - Add `entry.watch_state = { "watched_at": "...", "progress_pct": 73, "watch_count": 2 }`.
  - Two ingestion paths:
    1. **mpv integration**: use mpv's `--script-opts=osc-` and `--write-filename-in-watch-later-config` to dump progress files. A `mediavault sync_watch_state` command reads `~/.config/mpv/watch_later/` (or Windows equivalent) and updates entries.
    2. **Manual marking**: `python main.py watched <id> [--progress 73]` for non-mpv plays.
  - Plex/Jellyfin integration optional (IMP-E9): consume their per-user watch state via API.
- Rationale: Foundation for "Continue Watching" rails in the Apple TV UI, "watched this month" stats, and the smart-pruning feature (IMP-F6).
- Goal: Track which items have been watched. Surface continue-watching candidates in stats and UI.
- Effort estimate: medium
- Status: pending

---

## IMP-E5: Auto-cleanup of /sdcard/Media after Google Photos confirms upload

- Category: other
- Priority: high
- Files: new `cmd_cleanup_phone` in `main.py`; reuses Selenium machinery from mainfetch
- Current behavior: After `cmd_push` finishes, chunks sit on `/sdcard/Media/...` on the Pixel indefinitely. The Pixel's Google Photos app uploads them, but the local file on the phone is NOT automatically deleted by MediaVault or by Photos. Over time, the phone fills up with already-backed-up chunks and the user has to manually delete them via the file manager.
- Proposed change:
  - New `python main.py cleanup_phone [--id <id>] [--all-confirmed]` command:
    1. For each entry with `uploaded=True`, search Google Photos via Selenium for each expected chunk's filename.
    2. If FOUND in Photos: delete the corresponding file on the phone via `adb shell rm /sdcard/Media/.../<chunk>`.
    3. If NOT FOUND: leave it (Photos hasn't uploaded yet OR it's lost — keep the phone copy as insurance).
    4. Update `entry.cloud_confirmed_at` timestamp.
  - Add a `cloud_confirmed` boolean to the entry schema. Once true, the phone copy is safe to delete; future pushes can skip re-pushing this entry even if the library is partially lost.
  - Gate behind `--apply`; default is dry-run reporting what would be deleted.
- Rationale: Phone storage is finite (Pixel typically 128 GB). With 9.6 GB chunks, ~13 chunks fill the phone. This is the bottleneck preventing more aggressive batch pushes.
- Goal: Automated, verified cleanup. Phone storage stays available for the next push batch.
- Effort estimate: large (Selenium integration, robust matching, careful deletion)
- Status: pending

---

## IMP-E6: Per-account quota / bandwidth tracker

- Category: other
- Priority: medium
- Files: new `~/.mediavault/quota.csv` log; new `cmd_quota` reporting
- Current behavior: User has paid Google One storage on two accounts. There's no in-app visibility into cumulative pushed bytes per account, daily push rate, distance to a storage cap, or month-over-month growth.
- Proposed change:
  - On every successful `cmd_push`, append a row to `~/.mediavault/quota.csv`: `{timestamp, account_profile, manual_id, bytes_pushed, chunk_count}`.
  - Account profile is inferred from ID prefix: `mov-*` → movies-account, `tv-*/ani-*` → tv-account.
  - New `python main.py quota` reports:
    - Cumulative GB pushed per account
    - Last 7/30/90 days
    - Top 10 items by size
    - Optional `--google-storage <2tb>` to compute fill % against a known cap
- Rationale: Operational visibility. The "free" archive is bounded by paid Google One tiers; visibility prevents surprise overages.
- Goal: Always know how much storage each Google account is consuming.
- Effort estimate: small
- Status: pending

---

## IMP-E7: Multi-device parallel push

- Category: performance
- Priority: low
- Files: `main.py` — `cmd_push` (535-704), `cmd_push_group` (707-752)
- Current behavior: One Pixel at a time. Push runs serially. Depends on IMP-C4 for device serial pinning.
- Proposed change:
  - With two phones plugged in (different ADB serials), partition the chunks across them.
  - Use a `concurrent.futures.ThreadPoolExecutor` with one worker per pinned device.
  - Each worker handles its own subset of chunks, calling `adb -s <serial> push ...`.
  - The library entry merges results from both workers at the end.
- Rationale: Halves wall-clock push time if the user has a spare phone. Also creates implicit redundancy (chunks on two devices, both uploading to Photos).
- Goal: 2× push speed with two phones. Bonus: cross-device upload redundancy.
- Effort estimate: medium
- Status: pending

---

## IMP-E8: Wifi-mode ADB

- Category: other
- Priority: low
- Files: `main.py` — config + doctor checks
- Current behavior: ADB push requires a USB cable. User must be physically near the phone with a cable plugged in.
- Proposed change:
  - Document the `adb tcpip 5555` + `adb connect <phone-ip>:5555` flow.
  - Add a `python main.py adb_wifi setup|teardown` helper that automates the dance.
  - `doctor` (IMP-C3) detects wifi-connected ADB and lists it alongside USB devices.
- Rationale: Frees up USB ports and makes overnight pushes ergonomic (phone on dock, MediaVault running on PC, no cable management).
- Goal: Push wirelessly when convenient; fall back to USB for speed when not.
- Effort estimate: small
- Status: pending

---

## IMP-E9: Plex / Jellyfin / Kodi library-refresh integration

- Category: other
- Priority: medium
- Files: new `mvmedia_server.py` module; integration hooks in `cmd_restore`, `cmd_replace`
- Current behavior: After `cmd_replace`, Plex's view of the file is unchanged in name but the content is a dummy. Plex doesn't realize until next scan. Same after `cmd_restore` — Plex doesn't immediately see the real file return. The user has to manually trigger a library scan.
- Proposed change:
  - After `cmd_replace` and `cmd_restore`, POST to the configured media server's REST API to trigger a targeted scan of that folder:
    - Plex: `POST /library/sections/<id>/refresh?path=<folder>` with `X-Plex-Token`.
    - Jellyfin: `POST /Library/Refresh` (full) or `/Library/Media/Updated` (targeted).
    - Kodi: JSON-RPC `VideoLibrary.Scan` with path.
  - Config keys for media-server URLs and tokens (IMP-A5).
  - `--no-refresh` flag to suppress.
- Rationale: Removes the manual "now go refresh Plex" step that today follows every restore.
- Goal: Library changes appear in Plex/Jellyfin/Kodi within seconds, automatically.
- Effort estimate: medium
- Status: pending

---

## IMP-E10: Telegram bot dispatch

- Category: other
- Priority: medium
- Files: new `mvbot.py` — a small `python-telegram-bot` daemon; new commands accessible remotely
- Current behavior: All MediaVault commands require a shell on the PC. User can't trigger a fetch from a phone or initiate an unarchive while away from the PC.
- Proposed change:
  - A long-running bot daemon (`python mvbot.py`) that:
    - Listens for messages from the user's Telegram chat (whitelist by user ID).
    - Parses commands like `/fetch tv-en-2019-chernobyl episodes 1-3`, `/find inception`, `/stats`.
    - Dispatches to `main.py` subprocesses and streams progress back as edited Telegram messages.
    - Notifies on completion / failure with summary.
  - Config keys for bot token and authorized user IDs (IMP-A5).
- Rationale: Triggering an unarchive while leaving work means "by the time I'm home, the file is ready to watch". Massive QoL.
- Goal: Drive MediaVault from your phone via Telegram. Push notifications when long ops complete.
- Effort estimate: large
- Status: pending

---

## IMP-E11: Pre-prep ID lookup by folder (`uid` sidecar awareness)

- Category: other
- Priority: low
- Files: `cmd_prep` (289-381); new `cmd_find_id_by_folder`
- Current behavior: When the user wants to re-prep or work with an existing folder, they have to remember its manual ID. The `uid` sidecar file (written by `cmd_prep` at line 319-320) contains the short_id but NOT the manual ID; reverse-lookup requires scanning the library JSONs.
- Proposed change:
  - Two improvements:
    1. Change `uid` sidecar to ALSO store the manual_id (or rename it to `mvid` with content `{"manual_id": "...", "short_id": "..."}`).
    2. New `python main.py find_id_by_folder <folder>` command reads the `uid` sidecar and returns the manual_id, OR scans library JSONs for `folder_path == <folder>` if no sidecar.
  - Backwards compat: read both old and new sidecar formats.
- Rationale: Speeds up re-prep / inspection / debugging workflows. The user opens a folder in Explorer, drags it to a terminal, gets the ID instantly.
- Goal: Zero memorization of long IDs for existing entries.
- Effort estimate: small
- Status: pending

---

## IMP-E12: `web` command — local web UI

- Category: other
- Priority: high
- Files: new `mvweb.py` — Flask or FastAPI; HTML/CSS/JS frontend; new `cmd_web` to launch
- Current behavior: MediaVault is CLI-only. There's no graphical view of the library.
- Proposed change:
  - New `python main.py web` starts a local web server on `localhost:8765`.
  - Backend: Flask or FastAPI exposing JSON APIs (built on IMP-A4 `--json` foundations):
    - `GET /library` — full library data
    - `GET /entry/<id>` — full entry
    - `POST /command/<name>` — run any `cmd_*` and stream progress via SSE
  - Frontend: a clean responsive HTML page with:
    - Poster grid (uses `poster.jpg` from each folder)
    - Status filters (archived / local_ready / etc.)
    - Click → entry detail → action buttons (fetch/restore/replace)
    - Live progress streams during long ops
  - Auth: optional basic-auth via config; default localhost-only.
- Rationale: PRECURSOR TO THE APPLE TV UI. A web UI is faster to build, runs everywhere, and validates the data model and APIs. Once the web UI works, the Apple TV UI is a styling exercise.
- Goal: A clean web UI for browsing and managing the archive. Foundation for the Apple TV UI roadmap.
- Effort estimate: large
- Status: pending

---

## IMP-E13: Multi-episode combined-file support (SxxExxExx)

- Category: parsing / library
- Priority: high
- Files: `main.py` (`cmd_prep_season`, `_resolve_alias`, group command range filters, `_season_resume_cmd`); `mainfetch.py` (`_resolve_alias` mirror, `resolve_targets`); `ARCHITECTURE.md` §6.3 + §7.8
- Current behavior: `cmd_prep_season` only parsed the FIRST episode number from `S04E19E20`, silently skipping `e20`. Fetching ep20 failed; range filter `episodes 18-20` missed the combined file.
- Change delivered (PR #21, 2026-06-10):
  - `cmd_prep_season` detects `[sS]\d+(?:[eE]\d+){2,}` (TV SxxExx branch only, never anime), registers the lowest episode as the full primary leaf, and creates a thin `multi_ep_alias` entry for each additional episode: `{"type":"multi_ep_alias","alias_of":"...e19","parent_id":"...s04"}`. Both primary and aliases appear in `season_map.children`.
  - New `_resolve_alias(lib, mid)` helper (main.py + local mirror in mainfetch.py) collapses an alias to its primary in one hop.
  - All group push/replace/restore loops and `mainfetch.resolve_targets` de-alias `target_ids` before processing — the physical file is pushed/fetched exactly once.
  - `fetch episodes 20-20` and `fetch episodes 19-19` both correctly queue the same file.
  - Generalises to 3+ episodes per file (`E17E18E19`) with no extra code.
  - 6 new tests (F–K); 11 total pass.
- Status: done
