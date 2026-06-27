# Improvements — Tier E · Integration & Workflow Features

> Features that connect MediaVault to the surrounding ecosystem (the phone, the cloud, media servers, external metadata, the user's actual viewing habits). Higher payoff than Tier D but generally bigger effort. Several feed directly into the couch/Jellyfin end-goal — the daemon/server wiring itself now lives in **Tier S** (`improvements_tierS.md`), and client/UX polish in **Tier U**.

> **Cross-cutting context:**
> - User's phones are Pixel 1 XLs with Google Photos auto-upload of `/sdcard/Media/` (unlimited original-quality — the load-bearing free-storage trick). After upload, the phone's local copy is NOT automatically cleaned by MediaVault — it accumulates until manually deleted.
> - TWO Google accounts in play (movies vs series/anime), each with its own `ChromeProfile*`. The brief says FOUR Pixels run in parallel; `DEVICE_ALIASES` maps two serials — topology question open (REVIEW_NOTES §E1).
> - Folders contain `poster.jpg` / `fanart.jpg` IF the user has manually run `set_poster` / `set_fanart`. Most entries do not have these.
> - The user runs Plex today and is standing up **Jellyfin as the primary couch platform** (2026-06-12 decision; Emby lifetime owned as fallback). Media-server integration specifics live in Tier S.
> - `parse_metadata_from_id` is intentionally naive — extracts only `year`. All other metadata (title, genre, cast, synopsis) is empty.
> - **Attribute key (added 2026-06-12):** `Risk` = blast radius of MAKING the change. `If skipped` = the failure/limitation that persists, with a scenario.

---

## IMP-E1: Subtitle pre-extraction before archive

- Category: other
- Priority: medium
- Files: new helper in `main.py` invoked by `cmd_replace`; uses `mkvextract` binary from MKVToolNix
- Current behavior: `cmd_replace` deletes the original file. All embedded subtitle tracks go with it. If the user later restores and wants subs, they have to wait for the full restore.
- Proposed change:
  - Before `cmd_replace` destroys the original, run `mkvextract tracks <file> N:<folder>/subs/<lang>.srt` for each subtitle track listed in `entry.tech_spec.subtitles`.
  - Store under `<folder>/subs/`.
  - Subtitles are tiny (typically <500 KB per file, multiple languages).
  - Subtitle files survive the archive — they're committed-to-disk forever.
  - Optional config to disable for certain languages.
  - On restore, do NOT extract again — the existing `subs/` files are authoritative.
- Rationale: Subtitles are valuable independently of the video. Useful for: quick dialogue search (IMP-F7), translating-while-archived, accessibility, and Jellyfin serving external subs alongside restored files.
- Goal: Subtitles always available locally, even for archived content.
- Effort estimate: small
- Risk: medium — inserts a new step BEFORE `cmd_replace`'s journaled flow; must run strictly pre-journal (a sub-extraction failure must not abort or alter the replace contract — warn-and-continue), and the new `subs/` dir must be excluded from scan/walk loops like `checksums/` is. No PONR/journal semantics change → stays outside the change-gate, but state that explicitly in the PR.
- If skipped: every archived file's embedded subs remain locked in the cloud; a "watch with subs tonight" decision requires the full multi-GB fetch even when the user only needed the dialogue layer.
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
    - Time cost: ~5-10× realtime with NVENC on the Alienware RTX; config-gated.
  - Storage cost: ~500 MB per archived item × 100 items = ~50 GB. Significant but acceptable for many users.
  - Config-gated (off by default). Per-entry override via `--no-preview` / `--preview`.
  - Open design question for the Jellyfin era: the preview must NOT replace the dummy at the original filename (the dummy-as-fetch-trigger flow in Tier S depends on the dummy), so previews surface either as a Jellyfin "extra" (`<name>-trailer.mp4` convention) or behind the daemon's action stubs.
- Rationale: Eliminates the "I just want to quickly check what this movie is" friction while the real bytes are cold. Complements (does not replace) the T2 watch-while-fetching experiments in `RESEARCH_STORAGE_STREAMING.md` §2.
- Goal: Every archived item has a low-bitrate previewable copy on disk. Full quality is on-demand.
- Effort estimate: medium
- Risk: medium — adds a long transcode step into the archive pipeline (changes `prep_push_rep` wall-clock significantly when enabled) and doubles down on disk-layout conventions Jellyfin will scan; keep strictly opt-in.
- If skipped: "what was this one again?" keeps costing a full fetch; trickplay thumbnails (Tier U) cover scrubbing but not actual preview playback.
- Status: pending

---

## IMP-E3: External metadata enrichment (TMDB / TheTVDB / AniDB)

- Category: other
- Priority: high
- Files: `main.py` — `parse_metadata_from_id` extended; new helpers; new dependencies (`requests` already used, plus API keys)
- Current behavior: `parse_metadata_from_id` only extracts year. `metadata.title` is just the manual_id itself. `metadata.genre` is always `[]`. There's no synopsis, no rating, no cast, no real title.
- Proposed change:
  - At prep time (and via a backfill command `enrich_metadata`), look up the title against:
    - TMDB (free API, key required) — movies and TV.
    - TheTVDB — TV alternative.
    - AniDB or AniList GraphQL — anime.
  - Populate `entry.metadata`: `title`, `original_title`, `genre`, `synopsis`, `rating`, `runtime_mins`, `cast` (top 5), `tmdb_id`/`tvdb_id`/`anidb_id`.
  - Auto-download `poster.jpg` and `fanart.jpg` from the lookup (replaces the manual `set_poster`/`set_fanart` for the 95% case).
  - **NFO emission (new sub-goal, Jellyfin era):** optionally write Kodi/Jellyfin-compatible `movie.nfo` / `tvshow.nfo` / `<episode>.nfo` files next to the media so Jellyfin's local-metadata reader gets authoritative titles/ratings even for dummy-state items (RESEARCH_MEDIA_SERVERS §1.5; local images are already picked up automatically).
  - Cache lookups under `~/.mediavault/cache/metadata/<id>.json`; config keys for API keys; `enrich_metadata --library all` for backfill.
- Rationale: The **hard prerequisite for the couch UI**: a grid of tiles needs real titles, posters, fanart, ratings — both in any custom UI and in Jellyfin (where NFO + local images make even archived dummies render beautifully).
- Goal: Every entry has rich metadata sourced automatically from authoritative APIs; Jellyfin renders archived items indistinguishably from local ones (minus the runtime probe).
- Effort estimate: large (multiple APIs, caching, error handling)
- Risk: medium — touches `cmd_prep`'s flow and writes new files into media folders (NFOs/posters must be excluded from scan_unprepped video-extension matching — they are, by extension); API failures must degrade to today's naive metadata, never block a prep.
- If skipped: the Jellyfin library renders slugs-as-titles wherever filename-matching fails (especially anime with absolute numbering), and the Apple-TV-style experience never looks like Netflix no matter what the daemon does. TMDB matching by release-style filenames is mediocre — the curated manual-id → TMDB lookup is the fix.
- Status: in_progress — **local-first TMDB slice delivered** on `feature/imp_e3_u3_d17_tmdb_posters_rename` (2026-06-24): `enrich_metadata` command (show-centric TMDB lookup, cached, dry-run default); sets `metadata.tmdb_id` + real `metadata.title`/`year`; stamps `{tmdb-…}` folder token via `rename_folder`; downloads show + per-season `poster.jpg`/`fanart.jpg` (never overwrites locals); `--nfo` writes `movie.nfo`/`tvshow.nfo` (title/year/plot/rating/`<uniqueid type="tmdb">`). `set_tmdb` command added for manual id pinning. `/api/media-image/{id}` endpoint + `resolve_artwork_path` (season-inheritance resolver). SPA shows real titles + real poster artwork. **Remaining:** synopsis/cast/ratings fields; AniDB/AniList breadth (anime); TheTVDB fallback; per-episode NFOs; `prep`-time auto-enrich hook; watch-state and rating backfill from external sources.

---

## IMP-E4: Watch-state tracking

- Category: other
- Priority: medium
- Files: `main.py` — entry schema additions; ingestion helpers
- Current behavior: No watch-state. MediaVault knows what's in the library; it does not know what's been watched.
- Proposed change (REORIENTED 2026-06-12 — Jellyfin becomes the primary source):
  - Add `entry.watch_state = { "watched_at": "...", "progress_pct": 73, "watch_count": 2, "source": "jellyfin|manual|mpv" }`.
  - **Primary ingestion: the Tier S daemon's webhook listener** — Jellyfin `PlaybackStop`/progress events carry user, item path, played-to-completion; the daemon maps path→manual_id and updates `watch_state`. (This is also what drives the archive-prompt flow — IMP-S4.)
  - Secondary paths kept from the original design: `python main.py watched <id> [--progress 73]` manual marking; optional mpv watch-later ingestion for non-Jellyfin plays.
- Rationale: Foundation for "Continue Watching" semantics in MediaVault's own data (survives a Jellyfin rebuild), the smart-prefetch policy (IMP-S5 fetches episode N+1 because N was just watched), and grace-period auto-archive (IMP-S4).
- Goal: MediaVault knows what's been watched, from any client, without the user doing anything.
- Effort estimate: medium (small once IMP-S2's webhook listener exists)
- Risk: low — additive schema field + ingestion that only ever annotates entries.
- If skipped: the daemon can still react to live webhook events, but has no durable memory — after a daemon restart it can't answer "was S01E05 watched last week?" and smart-prefetch/auto-archive policies become stateless guesses.
- Status: pending

---

## IMP-E5: Auto-cleanup of /sdcard/Media after Google Photos confirms upload

- Category: other
- Priority: high
- Files: new `cmd_cleanup_phone` in `main.py`; reuses Selenium machinery from mainfetch
- Current behavior: After `cmd_push` finishes, chunks sit on `/sdcard/Media/...` on the Pixel indefinitely. The Pixel's Google Photos app uploads them, but the local file on the phone is NOT automatically deleted by MediaVault or by Photos. Over time, the phone fills up with already-backed-up chunks and the user has to manually delete them via the file manager.
- Proposed change:
  - New `python main.py cleanup_phone [--id <id>] [--all-confirmed] [device <alias>]` command:
    1. For each entry with `uploaded=True`, search Google Photos via Selenium for each expected chunk's filename.
    2. If FOUND in Photos: delete the corresponding file on the phone via `adb shell rm /sdcard/Media/.../<chunk>` (keep the `.mvmeta.json` sidecar — it is the phone-side disaster-recovery record).
    3. If NOT FOUND: leave it (Photos hasn't uploaded yet OR it's lost — keep the phone copy as insurance).
    4. Update `entry.cloud_confirmed_at` timestamp + `cloud_confirmed` boolean.
  - Gate behind `--apply`; default is dry-run reporting what would be deleted.
- Rationale: Pixel 1 XL storage is small (32/128 GB). With ~9.6 GB chunks, a dozen chunks fill a phone — this is THE bottleneck on batch pushes, and the Tier S daemon's continuous pipeline (push → confirm → clean → next batch) needs it automated. `cloud_confirmed` also strengthens the integrity story: it's the first explicit record that the bytes were SEEN in the cloud, not just handed to the Photos app.
- Goal: Automated, verified cleanup. Phone storage stays available for the next push batch with zero manual file-manager sessions.
- Effort estimate: large (Selenium integration, robust matching, careful deletion)
- Risk: high — this command DELETES the phone copy, which between upload and any future Google mishap is one of only two copies of the bytes. The Photos-presence check must match by exact UID-tagged filename (not fuzzy search), require per-chunk confirmation, and never run implicitly. Dry-run default + `--apply` + per-chunk logging are mandatory; consider requiring `cloud_confirmed` to be N days old before the daemon may invoke it.
- If skipped: phones keep filling; batch throughput stays capped at ~1 phone-load per manual cleanup session; the daemon's autonomous upload pipeline (Tier S) hard-blocks on a human with a file manager.
- Status: pending

---

## IMP-E6: Per-account quota / bandwidth tracker

- Category: other
- Priority: medium
- Files: new `~/.mediavault/quota.csv` log; new `cmd_quota` reporting
- Current behavior: There's no in-app visibility into cumulative pushed bytes per account, daily push rate, or month-over-month growth. (Note: Pixel-1 uploads are unlimited-original-quality, so this is about *operational* visibility and upload-rate sanity, not a hard storage cap — though Google's tolerance of the grandfather clause is itself a monitored risk, see RESEARCH_STORAGE_STREAMING §1.3.)
- Proposed change:
  - On every successful `cmd_push`, append a row to `~/.mediavault/quota.csv`: `{timestamp, account_profile, manual_id, bytes_pushed, chunk_count}`.
  - Account profile is inferred from ID prefix: `mov-*` → movies-account, `tv-*/ani-*` → tv-account.
  - New `python main.py quota` reports: cumulative GB per account, last 7/30/90 days, top 10 items by size, push-rate trend.
- Rationale: Operational visibility; an unusual upload-rate spike is also the earliest signal if Google ever throttles or flags the accounts.
- Goal: Always know how much each Google account holds and how fast it's growing.
- Effort estimate: small
- Risk: low — append-only logging on the push success path.
- If skipped: account-level exposure stays unknown; if the unlimited-upload grandfather ever ends, there's no record of what would need re-homing per account (relevant to IMP-F9 planning).
- Status: pending

---

## IMP-E7: Multi-device parallel push

- Category: performance
- Priority: low → **medium once the 4-phone topology is confirmed**
- Files: `main.py` — `cmd_push`, `cmd_push_group`
- Current behavior: One Pixel at a time. Push runs serially. Device pinning (IMP-C4 / PR #2) is done, so the plumbing exists; the brief says FOUR Pixel 1 XLs upload in parallel today — meaning the parallelism currently happens at the *Google Photos app* layer across phones the user loads by hand, not in MediaVault.
- Proposed change:
  - With multiple phones connected (different ADB serials), partition the chunks (or whole items in a group push) across them.
  - `concurrent.futures.ThreadPoolExecutor`, one worker per pinned device; each worker `adb -s <serial> push ...`.
  - The library entry merges results from all workers at the end; per-device progress lines.
  - Honor the per-account mapping (topology ANSWERED 2026-06-12: **3 accounts — movies, series, anime**): `mov-*`→movies-account device(s), `tv-*`→series-account device(s), `ani-*`→anime-account device(s). Chunks of one entry must land in ONE account or fetch-side search breaks. (Fetch-side mirror = IMP-C16's data-driven profile map; backup replicas = IMP-X1.)
- Rationale: The user already runs 4 phones; making MediaVault aware of them turns manual load-balancing into scheduled parallel lanes — push wall-clock ÷ phones-per-account.
- Goal: Saturate all available phones from one command; the daemon (Tier S) schedules lanes automatically.
- Effort estimate: medium
- Risk: medium-high — touches `cmd_push`'s upload loop (O-1 resume semantics must hold PER DEVICE: a one-lane failure must leave other lanes' progress resumable and the journal/`_parts` bookkeeping coherent). Plan against the change-gate checklist even though intent is no-contract-change.
- If skipped: multi-phone parallelism stays a manual ritual (user hand-splits batches across phones); fine interactively, but the daemon can only drive one lane per account.
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
- Rationale: Frees up USB ports and makes overnight pushes ergonomic (phones on a charging shelf, MediaVault running on PC, no cable management). With 4 phones, cable juggling is real friction; wifi ADB plus E7 = a hands-off upload rack.
- Goal: Push wirelessly when convenient; fall back to USB for speed when not.
- Effort estimate: small
- Risk: low — additive helper; wifi ADB is slower and flakier than USB (C2 retry already cushions transient drops); never default.
- If skipped: 4-phone operation keeps needing 4 cables and physical shuffling.
- Status: pending

---

## IMP-E9: Plex / Jellyfin / Kodi library-refresh integration

- Category: other
- Priority: medium
- Files: new `mvmedia_server.py` module; integration hooks in `cmd_restore`, `cmd_replace`
- Current behavior: After `cmd_replace`, Plex/Jellyfin's view of the file is unchanged in name but the content is a dummy; after `cmd_restore` the real file returns — either way the server doesn't notice until its next scan. The user manually triggers scans.
- Proposed change:
  - After `cmd_replace` and `cmd_restore`, POST a targeted refresh:
    - Jellyfin: `/Library/Media/Updated` (targeted) — the primary platform.
    - Plex: `POST /library/sections/<id>/refresh?path=<folder>` with `X-Plex-Token` (kept for as long as Plex is around).
    - Kodi (CoreELEC/Ugoos): JSON-RPC `VideoLibrary.Scan` — usually unnecessary in add-on playback mode (Jellyfin for Kodi syncs from the server), so implement only if native mode is ever used.
  - Config keys for media-server URLs and tokens (IMP-A5). `--no-refresh` flag to suppress.
  - **Relationship to Tier S:** IMP-S2's daemon performs refreshes for daemon-initiated operations; THIS task is the complementary hook for *manually-run* CLI commands. Same small client module (`mvmedia_server.py`) serves both — build once.
- Rationale: Removes the manual "now go refresh the server" step after every restore; required for the tile to flip to playable within seconds of a fetch completing.
- Goal: Library changes appear in Jellyfin (and Plex while it remains) within seconds, automatically, whether the operation was manual or daemon-driven.
- Effort estimate: medium (small for Jellyfin-only)
- Risk: low — post-success HTTP call, best-effort (a refresh failure must never fail the command; print a warning).
- If skipped: manual CLI restores keep looking "broken" on the TV until the next scheduled scan (up to 12 h with the recommended settings) — confusing exactly during the Phase-0/1 validation period.
- Status: pending

---

## IMP-E10: Telegram bot dispatch

- Category: other
- Priority: low (REPRIORITIZED 2026-06-12 — was medium)
- Files: new `mvbot.py` — a small `python-telegram-bot` daemon; new commands accessible remotely
- Current behavior: All MediaVault commands require a shell on the PC. User can't trigger a fetch from a phone or initiate an unarchive while away from the PC.
- Proposed change:
  - A long-running bot daemon (`python mvbot.py`) that listens for whitelisted-user messages (`/fetch tv-en-2019-chernobyl episodes 1-3`, `/find inception`, `/stats`), dispatches to `main.py` subprocesses, streams progress back, notifies on completion/failure.
  - Config keys for bot token and authorized user IDs (IMP-A5).
- Rationale: Remote control + push notifications from anywhere.
- **2026-06-12 session decision:** the user chose **in-client (Jellyfin) interaction ONLY** for the core couch flow — fetch-done notifications and archive prompts must live inside the media clients (IMP-S3/S4), NOT Telegram. This task is therefore demoted to an *optional remote-control fallback* for away-from-home scenarios (trigger a fetch from work so it's ready by evening). Build, if ever, only after Tier S lands; the bot would then talk to the daemon's API rather than spawning subprocesses.
- Goal: Optional remote control channel for away-from-home use; never the primary notification path.
- Effort estimate: large
- Risk: low-medium — separate daemon, no core-path changes; main risk is security surface (bot token, command whitelist).
- If skipped: no impact on the end-goal couch flow (by design); away-from-home triggering waits until/unless wanted.
- Status: pending

---

## IMP-E11: Pre-prep ID lookup by folder (`uid` sidecar awareness)

- Category: other
- Priority: low
- Files: `cmd_prep`; new `cmd_find_id_by_folder`
- Current behavior: When the user wants to re-prep or work with an existing folder, they have to remember its manual ID. The `uid` sidecar file contains the short_id but NOT the manual ID; reverse-lookup requires scanning the library JSONs.
- Proposed change:
  - Two improvements:
    1. Change `uid` sidecar to ALSO store the manual_id (or rename it to `mvid` with content `{"manual_id": "...", "short_id": "..."}`).
    2. New `python main.py find_id_by_folder <folder>` command reads the `uid` sidecar and returns the manual_id, OR scans library JSONs for `folder_path == <folder>` if no sidecar.
  - Backwards compat: read both old and new sidecar formats.
- Rationale: Speeds up re-prep / inspection / debugging workflows. The user opens a folder in Explorer, drags it to a terminal, gets the ID instantly. The daemon needs the same mapping in reverse (webhook events carry file PATHS, not manual ids) — a shared `path→id` resolver helper serves both (see IMP-S2).
- Goal: Zero memorization of long IDs for existing entries.
- Effort estimate: small
- Risk: low — additive sidecar field + read-only lookup; keep writing the old `uid` format alongside for belt-and-suspenders compat.
- If skipped: minor interactive friction; the daemon will build its own path→id index anyway (the library already has folder_path+filename).
- Status: pending

---

## IMP-E12: `web` command — local web UI

- Category: other
- Priority: high
- Files: new `webui/` package (`server.py` FastAPI `create_app()` + a serialized single-worker job queue; `static/` no-build card-grid SPA); `main.py` `cmd_web` + `web` dispatch arm + the read-only data-functions (`collect_reclaimable`, `classify_entry_state`, `guess_manual_id`, `suggest_target_folder`, `suggest_next_command`)
- Current behavior: MediaVault is CLI-only. There's no graphical view of the library.
- Proposed change:
  - New `python main.py web` starts a local web server on `localhost:8765`.
  - Backend (as shipped): FastAPI `create_app()` exposing `GET /api/reclaim` (the four-state reclaim scan), `GET /api/library` (status counts by category), `POST /api/action/{name}` (allow-list `prep`/`push`/`replace`/`sort`/`prep_push_rep`; `replace` requires `confirm:true` else **409**; returns **202** + `job_id`), `GET /api/job/{id}` (polling). Actions call the existing `cmd_*` UNCHANGED via a serialized single-worker queue. Progress = polling for v1 (SSE/WebSocket = IMP-F10). (A4's `--json` will later reuse the same data-functions.)
  - Frontend (as shipped): a no-build dark responsive **card-grid SPA** (`webui/static/`) — the merged Disk Reclaim view: per-item state badge, total-reclaimable-GB header, filter chips, suggested next command + suggested target folder (editable curly provider tag, NEW items only), one-click actions with an unmissable confirm modal on `replace`. A poster grid with real posters/titles is future polish (see Follow-ups).
  - Auth: optional basic-auth via config; default localhost-only.
  - **Relationship to Tier S (2026-06-12):** the daemon (IMP-S2) IS this backend grown up — one FastAPI process serving webhook ingestion + job queue + these APIs + this status UI. Build E12 as the daemon's UI layer, not a separate server. The "complete modern Web UI to track all operations" ambition from the original brief = this + IMP-F10 + D1's stats feed.
- Rationale: The ops dashboard for the whole system (queue, progress, errors, library state) and the validation surface for the data model the couch UI consumes. Jellyfin remains the *viewing* UI; this is the *operations* UI.
- Goal: A clean web UI for browsing and managing the archive + watching daemon activity live.
- Effort estimate: large
- Risk: low-medium — additive new module; the only core-code coupling is via `--json`/function calls. Keep it localhost-bound by default (it can trigger destructive commands).
- If skipped: operations stay CLI-only; daemon behavior is observable only through logs (IMP-A3) — workable but the "never come to the computer" goal then has no at-a-glance health surface when something DOES go wrong.
- **Follow-ups / forward vision (user-decided 2026-06-22):** the shipped frontend is the **card-grid** (the bake-off's candidate B), chosen at the human C3 gate over the denser table specifically as the **substrate for a future "Apple-like" local media UI** that grows beyond disk-reclaim — adding real movie **titles**, **posters/artwork**, and **fetch-in-the-UI**. Those are explicitly OUT of scope for E12 (which stays the *operations* console; *viewing/playback* remains Jellyfin, locked 2026-06-12) and are tracked as: poster/title enrichment → **IMP-D10 / IMP-E3**; fetch-in-UI + the always-on service → **IMP-S2** (this FastAPI app is that daemon's seed). The card's poster-placeholder is the slot real posters drop into.
- Status: **done** (`feature/web_console` — implemented via the multi-agent pipeline with 3-candidate bake-offs on the reclaim data model, the action-execution model, and the UI; PR to `main` pending). Bundled IMP-A10 (requirements truth-up) and introduced IMP-D16 (`scan_reclaimable`).

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
- Status: done (follow-up bugs found in the 2026-06-12 review — whole-library iterators and single-id commands missed the de-alias memo — are tracked as IMP-C12 / IMP-C13)

---

## IMP-E14: Web media-type UI (tabs + fetch-in-UI + posters)

- Category: other
- Priority: high
- Files: `webui/server.py` (new `GET /api/items` route, `items_payload()` helper, `_classify_item` shared with `collect_reclaimable`); `webui/static/` (media-type tab rail + sub-view rail SPA additions); `main.py` (`items_payload`, `_classify_item`, `_category_of`)
- Parent / prerequisite: IMP-E12 (the FastAPI ops console and card-grid SPA substrate)
- Tracked plan: `docs/feature-web-media-ui/PLAN.md`
- Phased scope:
  - **Phase 1 ✓ done** (branch `feature/imp_e14_web_media_tabs`): new read-only `GET /api/items` endpoint returning `main.items_payload()` = `{"items":[...], "by_category":{movies,series,anime,other}}`; every PHYSICAL leaf (skips `season_map`/`multi_ep_alias`) with `id, category, state, size_bytes, path, title, year, tmdb_id, poster_available, chunk_count` (+ `parent_id`); includes ALL states incl. `archived` (divergence from `/api/reclaim` which excludes archived). `items_payload` and `collect_reclaimable` share a `_classify_item` helper so the two endpoints can't drift on state semantics. Frontend: media-type tab rail (Movies / TV series / Anime / Others, keyed by id-prefix via `_category_of`) + sub-view rail (Unprepped / Local·not-pushed / Pushed·not-archived / Fetched·not-archived / Archived); UNPREPPED rows still sourced from `/api/reclaim`. No build step — vanilla ES modules. Tests added.
  - **Phase 2 ✓ done** (branch `feature/imp_e14_fetch_in_ui`): `fetch_restore` added to the web action allow-list (`ACTION_TABLE` + `_NONE_IS_SUCCESS`), wrapping `main.cmd_fetch_restore`. Worker publishes incremental `output` + `progress {done, total}` (chunk units) via a stdout tee; job record gained `progress` and `progress_unit` fields. Transport is **polling `GET /api/job/{id}`**, NOT SSE/WebSocket (future streaming upgrade tracked as IMP-F10; this is the down-payment on IMP-S2 — the serialized web worker is the daemon's seed and now performs `fetch_restore` with live progress). `cmd_dispatch_fetch` now streams `python mainfetch.py` subprocess stdout via `subprocess.Popen` line-by-line with `PYTHONIOENCODING=utf-8` in the child env (was `subprocess.run`, which bypassed capture). SPA additions: Fetch & Restore button on Archived cards; SVG `stroke-dashoffset` growing chunk-% progress border that snaps to a glowing loop on done; auto-flip of the card from Archived → Fetched·not-archived via an `/api/items` refresh on job completion; default size-descending sort + a Size/Title/Year sort bar; readable titles (humanized id now; real `metadata.title` when Phase-5 TMDB lands) with the raw id at the card foot; expandable full-screen terminal (⤢) showing the equivalent CLI command + live progress + full output; cursor-following card glow (disabled on touch + reduced-motion). Demo/safe mode: `python main.py web --demo` simulates EVERY action with no real `cmd_*`, no library mutation, no Selenium — exposed via `GET /api/mode`; default `python main.py web` unchanged.
  - **Phase 3 ✓ done** (branch `feature/imp_e14_polish_pwa`): **continuous hover border** — a
    rotating conic-gradient accent arc (`.card::after`, `@property --ring-angle`) on each card;
    `@supports`-gated mask clip with a box-shadow fallback for iOS Safari; `prefers-reduced-motion`
    static variant; touch-gated. **PWA / Add-to-Home-Screen**: `webui/static/manifest.webmanifest`
    (`display:standalone`, `theme_color:#0b0f17`) + self-generated branded PNG icons (192/512/apple-
    touch-icon 180) + iOS meta tags (`apple-mobile-web-app-*`, `theme-color`, `viewport-fit=cover`).
    **Global `web-ui-polish` Claude skill** installed at `~/.claude/skills/web-ui-polish/SKILL.md`
    (outside repo) — reusable buttery-motion recipes + iOS-Safari mask-render safety rules +
    `prefers-reduced-motion` discipline.
    Additional enhancements shipped on this branch (scope grew beyond original Phase 3):
    - **Grouped (hierarchical) folder view + Grouped/Decluttered toggle**: each media-type tab can
      render the on-disk folder hierarchy (show→season→episode, collection→movie). The state rail
      gains an **"All" segment** (default) alongside the 5 lifecycle states; in grouped mode a
      state selection prunes the folder tree to folders with at least one matching descendant leaf
      (folder size = aggregate of visible leaves; "All" = real Windows folder size). Recursive sort
      at every tree level.
    - **`GET /api/tree`**: new backend endpoint returning the per-category folder hierarchy across
      all lifecycle states including un-prepped on-disk files; real Windows folder sizes via
      `os.scandir`; `has_image` flag per folder. Read-only; alias-safe.
    - **`GET /api/folder-image?path=`**: serves a folder's `poster.jpg` / `fanart.jpg` (or first
      found in a descendant); realpath-contained to `C:\Media`; only those two filenames served.
    - **`POST /api/open-folder`**: opens a folder in Windows Explorer; **localhost-only** (403
      over Tailscale); realpath-contained to `C:\Media`; simulated in demo mode.
    - **Open-in-Explorer button** on every folder and item card (fires `POST /api/open-folder`).
    - **Procedural animated space/galaxy background** (`background.js`): Canvas starfield + nebula,
      perf-capped (30 fps cap, pauses when tab hidden), `prefers-reduced-motion` aware, no external
      assets.
    - **Static no-cache policy** (`_NoCacheStaticFiles` subclass): `Cache-Control: no-cache` on
      all static responses (ETag kept) — **fixes the iOS Safari stale-ES-module blank-page bug**.
      Plus a `/favicon.ico` handler and a global JS error banner (`window.onerror` +
      `unhandledrejection`) so the SPA can never silently blank.
  - **Phase 4** (mobile + Tailscale + auth — tracked as **IMP-E15**): responsive layout,
    Tailscale-safe binding, optional basic-auth (IMP-A5).
  - **Phase 5** (TMDB posters + rename — tracked as **IMP-E3 / IMP-U3 / IMP-D17**): real poster
    artwork, NFO emission, proper title display.
- Rationale: The ops console was the disk-reclaim foundation; the media-type UI is the step toward a usable library browser that surfaces what you have by type, not just by disk pressure. The phased approach ships each phase as a PR while the fuller phases follow.
- Effort estimate: large overall (phased — Phase 1 was medium; Phase 2 was medium; Phase 3 was small; Phases 4-5 each small-to-medium)
- Risk: low (Phases 1-3 call existing `cmd_*` unchanged via the existing job queue — no rollback-contract change; Phase 3 is CSS/manifest only, no backend change)
- If skipped: the console stays disk-reclaim-only; there's no browsable media-type view, and fetch-in-UI/mobile access stay future-only.
- Status: **done** — all five phases shipped: Phases 1-3 (media-type tabs + fetch-in-UI + motion/PWA + grouped folder view + /api/tree + /api/folder-image + /api/open-folder + space background + iOS no-cache fix; `feature/imp_e14_polish_pwa`); Phase 4 = IMP-E15 (mobile/Tailscale/auth; `feature/imp_e15_mobile_tailscale_auth`); Phase 5 = IMP-E3/U3/D17 (TMDB posters + real titles + rename_folder + /api/media-image; `feature/imp_e3_u3_d17_tmdb_posters_rename`)

---

## IMP-E15: Mobile + Tailscale remote access + shared-token auth

- Category: integration / security
- Priority: high
- Parent / prerequisite: IMP-E14 (built on its FastAPI console + PWA substrate)
- Files: `mvcommon.py` (mvconfig.json loader + `mvtokens.json` store: `mint_token`/`list_tokens`/`revoke_token`/`validate_token`), `main.py` (`cmd_token_create`/`cmd_token_list`/`cmd_token_revoke`, updated `cmd_web`), `webui/server.py` (`_is_genuine_local_admin`, `_request_token_is_valid`, `_is_authed`, `/api/whoami`, `/api/token` CRUD, `/api/*` auth middleware), `webui/static/admin.js` (owner Access panel: mint + list + revoke), `webui/static/auth.js` (device-side token capture + cookie + header injection), `tools/tailscale_serve_setup.ps1` (one-time Tailscale HTTPS setup), `docs/feature-web-media-ui/REMOTE_ACCESS.md` (full remote-access guide), `mvconfig.example.json` (checked in; no `web.token`)
- What shipped (branch `feature/imp_e15_mobile_tailscale_auth`):
  - **mvconfig.json minimal config (IMP-A5 first slice):** `mvcommon.py` loads `mvconfig.json` at startup. Schema: `web.host`, `web.port`, `tmdb.api_key`. **No `web.token`** — auth is no longer a static config key (superseded by minted tokens). Absent / malformed → defaults (`127.0.0.1:8765`). `mvconfig.json` gitignored; `mvconfig.example.json` checked in.
  - **Admin-minted, expiring, revocable token auth — SUPERSEDES the initial static-token slice:** tokens are NOT a static config value. The gitignored `mvtokens.json` stores only sha256 hashes (raw token shown once at mint, never persisted). Each record: `{id, label, hash, created_at, expires_at}`. The store is read fresh per request. Minting/listing/revoking: CLI (`python main.py token create [--label X] [--ttl 1h|8h|12h|1d|3d|7d|30d|never]`, `token list`, `token revoke <id>`) and the localhost-only **Access panel** in the web UI.
  - **Genuine-local-admin detection (the security hinge):** `_is_genuine_local_admin` = loopback host AND none of the proxy/identity headers (`x-forwarded-*`, `tailscale-user-*`, `forwarded`). `tailscale serve` proxies remote tailnet peers to `127.0.0.1` but injects those headers, so a proxied peer is never mistaken for the owner. `GET /api/whoami` (unauthenticated) returns `{is_admin, authed}` so the SPA shows the Access panel only to the owner.
  - **Always-enforce auth — secure-by-default:** auth is enforced on every `/api/*` request (except `/api/whoami`). With no tokens minted, the genuine-local admin still has full, token-free access; every remote request gets 401 — remote is locked until the owner mints and shares a token. There is no startup guard: the always-enforce rule makes it unnecessary.
  - **Remote access model:** app binds `0.0.0.0` → reachable on LAN IP + Tailscale IP over HTTP; `tailscale serve` provides an HTTPS tailnet URL (one-time: enable MagicDNS + HTTPS in Tailscale admin). `tools/tailscale_serve_setup.ps1` automates the `tailscale serve` setup. Full guide: `docs/feature-web-media-ui/REMOTE_ACCESS.md`.
  - **Client token UX (`auth.js` + `admin.js`):** owner mints a token (Access panel or CLI) and shares the `?token=` link; device captures it → stores in `mv_token` cookie + `sessionStorage` → strips from URL → sends as `X-MediaVault-Token` header on every fetch. On 401 → re-prompts. The local browser is auto-opened at `127.0.0.1` (no `?token=` needed — genuine-local admin). iPhone/iPad flow: open share link once, "Add to Home Screen" to install PWA.
- Rationale: The PWA (IMP-E14 Phase 3) installs beautifully on iPhone/iPad; without a safe remote-access model and auth layer, it is only usable on localhost. Tailscale gives a production-grade HTTPS tunnel with zero infra; the token keeps the API private without an identity provider.
- Effort estimate: medium
- Risk: low-medium — auth middleware is additive (no cmd_* change, no rollback-contract change, no ENTRY_TYPE_KEYS change); the non-localhost guard is additive; the mvconfig.json loader is additive (fallback to defaults if absent).
- If skipped: the console stays localhost-only; phone access requires either an SSH tunnel or an insecure LAN URL; the PWA can't be used from the couch.
- Cross-references: IMP-E14 (Phase 4 of the web media-UI plan); IMP-A5 (full config migration — E15 delivers the minimal slice only); IMP-E3/U3/D17 (Phase 5, next).
- Status: **done** — shipped on `feature/imp_e15_mobile_tailscale_auth`

---

## IMP-E16: UI wow + online enrichment (dossier, ratings, trivia, grid view, palette, glow, view-transitions, hero, perf, auto-resolve)

- Category: integration / UX
- Priority: high
- Parent / prerequisites: IMP-E3 (extends the local-first TMDB slice with online ratings, trivia, and a web-search auto-resolve waterfall), IMP-E14 (built on the card-grid SPA substrate), IMP-E15 (auth layer)
- Files: `webui/server.py` (new `GET /api/detail/{id}` route; `refresh_online`/`fetch_trivia` JSON dispatch); `main.py` (`tmdb_detail`, `cmd_refresh_online`, `cmd_fetch_trivia`, EXA auto-resolve waterfall in `cmd_enrich_metadata`); `mvcommon.py` (mvconfig EXA/OMDb/GROQ key loaders); `webui/static/preview.js` (cinematic dossier), `webui/static/swatch.js` (poster-driven ambient glow), `webui/static/palette.js` (⌘K command palette), `webui/static/hero.js` (parallax hero band), plus CSS/JS changes throughout `webui/static/`
- What shipped (branch `feature/imp_e16_ui_wow`):
  - **Cinematic hover/long-press dossier (`preview.js`):** resting a pointer (desktop) or long-pressing (touch) on any card opens a large translucent glass "dossier" — backdrop hero image, real title, year, episode line, synopsis, and meta (rating, runtime, genres, tagline, cast, directors/creators, status, IMDb/TMDb links, merged OMDb + Metacritic/RT scores). Persistent + interactive: `pointer-events` gated on `.is-open` — a closed panel NEVER traps hover events (invariant: dossier is inert unless explicitly `.is-open`). Lazy-fetches `GET /api/detail/{id}`.
  - **`GET /api/detail/{id}`** (webui/server.py + `main.py:tmdb_detail`): cached, `/api/*`-auth-gated, read-only — full TMDB detail (rating/votes, runtime, genres, tagline, full overview, top cast, directors/creators, status, IMDb+TMDb links; TV seasons/episodes/networks; episode title/air-date/S·E number) PLUS merged OMDb ratings (IMDb/RT/Metacritic), MPAA `rated`, `awards`, `boxoffice` (read from gitignored `mvonline.json`) PLUS `trivia` (`[{text, source}]` read from gitignored `mvextra.json`). Cache-read only: the request path makes NO live API calls.
  - **`metadata.overview`** stored by `enrich_metadata` (also `metadata.episode_title` per episode); `items_payload` exposes `overview`, `episode_title`, `backdrop_available`, `actual_size_bytes`, `tech`, and `release_name` as new item fields.
  - **New commands:**
    - `refresh_online [id_or_prefix] [--force] [--library X]` — bulk OMDb ratings fetch → writes gitignored `mvonline.json` keyed by `tmdb_id`.
    - `fetch_trivia [id_or_prefix] [--force] [--library X]` — EXA web-search → GROQ-distilled `[source]`-tagged trivia facts → gitignored `mvextra.json`.
  - **EXA auto-resolve waterfall in `enrich_metadata`:** on TMDB API miss → EXA searches themoviedb.org → extracts a candidate TMDB id → validates by-id fetch; `--no-web` flag disables the web leg. Resolves hard/regional/concatenated titles without manual link-pasting. Cross-ref IMP-E3 (extends that slice).
  - **Archived tile chips:** show real fetched size (`tech_spec.size_bytes`) + print/tech chips (resolution, Dolby-Vision+profile, REMUX, IMAX, codec, audio format).
  - **Grouped GRID / drill-down view (List|Grid toggle, persisted):** language/folder boxes → drill in (Show→Seasons→Episodes) with breadcrumb + Back; state-prune + sort + leaf-card reuse.
  - **Poster-driven ambient glow (`swatch.js`):** each card's glow, border, and scrim tint to its poster's dominant color (mint fallback when no poster).
  - **⌘K / Ctrl-K command palette (`palette.js`):** fuzzy-jump to any title + run global actions.
  - **View-Transitions morphs:** `document.startViewTransition` scoped to `#panel` on tab/filter/view switches; graceful fallback for unsupporting browsers.
  - **Cinematic parallax hero (`hero.js`):** per-tab backdrop band of archived/featured titles (Ken-Burns animation + scroll parallax + auto-rotate + click-jump; `prefers-reduced-motion` static fallback).
  - **Lazy-load perf:** admin, palette, and terminal modules are lazy-loaded; the critical path is `<link rel="modulepreload">`-preloaded.
  - **Config:** `mvconfig.json` extended with `omdb.api_key`, `exa.api_key`, `groq.api_key`, `rapidapi.*` keys (all gitignored). New gitignored caches: `mvonline.json`, `mvextra.json`, `~/.mediavault/cache/exa/`.
- Rationale: Turns the ops console into a cinematic media browser: the dossier gives richer context than any external app page, online ratings and trivia make "what should I watch?" decisions inside the vault, and the EXA auto-resolve waterfall finally makes `enrich_metadata` robust on difficult titles.
- Goal: Any card hover/press opens a rich detail panel; `refresh_online`/`fetch_trivia` populate ratings and trivia; hard-to-match titles auto-resolve without manual TMDB id pasting.
- Effort estimate: large
- Risk: low — `/api/detail` is read-only and cache-gated; no `cmd_*` rollback contract changes; `mvonline.json`/`mvextra.json` are gitignored caches (never in the library JSONs); `ENTRY_TYPE_KEYS` unchanged; new config keys are additive.
- Cross-references: IMP-E3 (extends the local-first TMDB slice); IMP-E14 (the card-grid SPA substrate); IMP-E15 (auth layer for `/api/*`).
- Status: in_progress — on `feature/imp_e16_ui_wow` (done on merge of this branch)
