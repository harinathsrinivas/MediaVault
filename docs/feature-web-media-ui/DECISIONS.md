# Locked Decisions & IMP Assignments

Locked decisions + IMP assignments for the web-media-UI feature (mirrors PLAN.md). Source of truth: docs/feature-web-media-ui/PLAN.md.

## Locked decisions (the contract — executors design to these exactly)

| # | Area | Locked decision |
|---|------|-----------------|
| 1 | Data model | Add ONE new endpoint **`GET /api/items`** (library-centric: every physical entry with `id`, `category`, `state`, `size_bytes`, `path`, `metadata`, `tmdb_id`, `poster_available`, `chunk_count`; season-aware). **`GET /api/reclaim` stays UNCHANGED** for back-compat. The new media-type UI is driven by `/api/items`. |
| 2 | Media-type tabs | Top-level tabs **Movies / TV series / Anime / Others**. Each nests the existing disk-state sub-views (Unprepped / Local·not-pushed / Pushed·not-archived / Restored=Fetched·not-archived) PLUS a new **Archived (fetchable)** sub-view. Classify by id prefix via `_category_of`. |
| 3 | Remote access | App **stays localhost-bound by default.** Primary remote path = **`tailscale serve`** → `https://<machine>.<tailnet>.ts.net` (HTTPS, tailnet-only, iPad/iPhone home AND away). Add **app-level shared-token** auth (mandatory-in-spirit; console does destructive replace + now fetch_restore). Plus PWA / Add-to-Home-Screen manifest + iOS-Safari responsive polish. |
| 4 | Fetch progress | Real **chunk-based %**. Worker flushes stdout to the job record **incrementally** (not only at terminal) AND exposes parsed `progress {done,total}` derived from chunks-done/total_chunks. Card's "growing highlighter border" fills as chunks land, snaps to a full glowing loop on restore-complete, with smooth CSS animation between discrete ticks. Transport = **polling** `/api/job/{id}` (NOT IMP-F10 WebSocket/SSE — noted as a future smoothness upgrade). |
| 5 | Fetch action + lifecycle | Add **`fetch_restore`** ("Fetch & Restore" primary button) to `ACTION_TABLE` (wraps `main.cmd_fetch_restore`); consider an advanced plain `fetch`. Lifecycle: Archived item (status=archived, dummy on disk, surfaced via `/api/items`) → click → chunk-% progress → on done status flips `restored_local` → card moves to "Fetched·not-archived" (=RESTORED_REPLACE_AGAIN) → later Replace re-archives. Integration seams: fetch is a Selenium subprocess (`mainfetch.py`) on the Alienware, **single-flight** (`fetch_session_lock`), the web worker is **already serialized** — design accordingly. |
| 6 | Posters / TMDB | Do ALL THREE: (a) store `tmdb_id` in entry metadata; (b) serve local `poster.jpg`/`fanart.jpg` via a NEW media-image route (graceful gradient fallback when absent); (c) NEW crash-safe `rename_folder` command. Population = **auto-enrich via TMDB API, local-first** (query title+year → write tmdb_id + stamp folder token + download poster/fanart for the whole ~570-entry library WITHOUT fetching media; local images you placed always win; list ambiguous matches; plus manual `set_tmdb`). Needs a free TMDB API key (user-provided — Open Decisions / prereq). |
| 7 | TMDB id storage | BOTH a folder-name token `{tmdb-12345}` AND a metadata field. `rename_folder` stamps the token and MUST **cascade**: rename the on-disk folder + atomically rewrite `folder_path` for EVERY entry under that folder (all seasons/episodes), crash-safe (journaled), works on **archived** folders (dummy files), **hash-safe** (only moves a folder + rewrites JSON pointers). |
| 8 | Season inheritance ("Dark") | Smart tmdb/art resolution for any episode/season: (i) entry's own `tmdb_id`; else (ii) season_map's `tmdb_id`; else (iii) walk UP the on-disk folder path to the nearest ancestor folder carrying `{tmdb-…}` (the show folder) = the SERIES id. Season-specific art = series-id + season-number (parsed from id, e.g. `s02`) → that season's TMDB poster if available, else the show poster. A locally-present image always wins. |
| 9 | Delivery | One PLAN.md, executed as a SEQUENCE of focused, human-gated PRs (NOT one mega-PR). Each phase = own branch + PR to main + manual test commands. |

## IMP codes assigned (research-confirmed; this is the IMP-E12 family, NOT C18)

| Phase | New / existing IMP | Meaning |
|------|--------------------|---------|
| 0 | **IMP-A12** (existing, the current 👉 NEXT) | CI pipeline — lock the suite green before/parallel to this work. |
| 1 | **IMP-E14** (NEW) — "web media-type UI" | `GET /api/items` + Movies/TV/Anime/Others tabs nesting disk-state sub-views. Extends E12. |
| 2 | **IMP-E14** (continues) + down-payment on **IMP-S2** | fetch_restore action + worker incremental progress + Archived(fetchable) sub-view + chunk-% growing border + auto-flip. (The serialized worker IS the S2 daemon's seed; this is "E12 grown up".) |
| 3 | **IMP-E14** (continues) | aesthetic polish + author & install GLOBAL `web-ui-polish` skill + PWA manifest. |
| 4 | **IMP-E15** (NEW) — "mobile + Tailscale remote + token auth" + **IMP-A5** (existing, minimal slice) | localhost bind kept; `tailscale serve` doc+script; shared-token middleware; minimal config for host/port/token/TMDB-key; iOS-Safari responsive polish. |
| 5 | **IMP-E3** + **IMP-U3** (existing) + **IMP-D17** (NEW — `rename_folder` CLI) | TMDB auto-enrich (local-first) + `tmdb_id` field + media-image route + crash-safe cascading `rename_folder` + season inheritance + optional NFO. |
