# Task: MediaVault web-UI upgrade — media-type tabs, fetch-in-UI, aesthetic polish, mobile/Tailscale remote access, TMDB posters + crash-safe folder rename

> **Delivery model:** ONE plan, executed as a SEQUENCE of focused, separately-shippable, human-gated PRs (Phase 0 → Phase 5). Each phase = its own branch + its own PR to `main` + its own manual test commands. Do NOT collapse into a mega-PR.
>
> **PLAN.md location note (this run):** Per the repo convention the *tracked* copy of this plan belongs at `docs/feature-web-media-ui/PLAN.md` (plus `DECISIONS.md`). The originating task for THIS planning run constrained the deliverable to the root `/PLAN.md` only ("PLAN.md ONLY … do NOT edit any other file"), so only the root copy is written now. **At execution time, the first action of Phase 1 is to copy this file to `docs/feature-web-media-ui/PLAN.md` and record the locked decisions in `docs/feature-web-media-ui/DECISIONS.md`** so the tracked artifacts ship with the branches (git-agent commits the `docs/` copy; root stays gitignored).

Suggested branch (per-phase branches are listed in each phase; the umbrella feature is): feature/web_media_ui

---

## Context & verified findings

MediaVault's web ops console (IMP-E12, PR #30, **done**) is a no-build vanilla SPA over a FastAPI app. This task grows it from a *disk-reclaim* console into a *media-type-first* console with fetch-in-UI, posters, polish, and secure mobile access — explicitly the "Apple-like local media UI" follow-up the user locked at the IMP-E12 C3 gate (`improvements/improvements_tierE.md` IMP-E12 "Follow-ups / forward vision, user-decided 2026-06-22").

Verified anchors executors are grounded on (function names are stable; prefer grep-by-name over line numbers):

- **Server:** `webui/server.py` `create_app()` — routes `GET /api/reclaim` → `main.collect_reclaimable()` (server.py:315-318), `GET /api/library` → `_library_summary()` (server.py:279-322), `POST /api/action/{name}` over `ACTION_TABLE = {prep,push,replace(confirm),sort,prep_push_rep}` (server.py:81-87, 324-339), `GET /api/job/{id}` (server.py:341-349). Static mount LAST at `/` (server.py:354-355).
- **Job worker:** ONE serialized daemon thread `_worker_loop` drains a FIFO `WORK_QUEUE`, runs each `main.cmd_*` in-process, captures stdout via `contextlib.redirect_stdout(io.StringIO())`, and writes `JOBS[id]["output"]` **only at the terminal state** (server.py:152-227). Job record = `{id,name,status(running|done|error),output,started_at}`. **No progress signal today.** This serialization is load-bearing (it is the device lock + race-free stdout).
- **Category mapping:** `_category_of(mid)` (server.py:268-276): `mov→movies`, `tv→series`, `ani→anime`, else `other`. Mirrors `save_library`'s prefix split.
- **Reclaim data layer (read-only, in `main.py`):** `collect_reclaimable()` (main.py:3169-3298, whole-library iterator, **already alias/season_map-safe** — skips `type in (season_map,multi_ep_alias)` before dereferencing `folder_path`/`filename`), `classify_entry_state(entry,on_disk_real)` (main.py:2960-2993), `guess_manual_id` (main.py:2996), `suggest_target_folder` (main.py:3093), `suggest_next_command` (main.py:3153). Badge map `_RECLAIMABLE_STATUS_BADGE` (main.py:2935-2939): `local_ready→LOCAL_NOT_PUSHED`, `onboarded→PUSHED_NOT_ARCHIVED`, `restored_local→RESTORED_REPLACE_AGAIN`. **`archived` + dummy is EXCLUDED** from `/api/reclaim` (main.py:3264, 3290).
- **Frontend:** `webui/static/{index.html,app.js,styles.css}` — vanilla, no framework, **no build step, no CDNs** (explicit in styles.css:1-2). The `node --check webui/static/app.js` gate covers JS. Card grid clones `<template id="card-tpl">`; output rendered via `textContent` only (XSS-safe). `BADGE_META`/`BADGE_ORDER` drive the 4 state chips (app.js:23-60). `--port`/`--host`/`--no-browser` parsed in main.py:3603-3625; `cmd_web` binds localhost only (main.py:3301-3317).
- **Library schema:** 3 JSON manifests (`mvcommon.LIBRARY_MOVIES/SERIES/ANIME` = `C:\Media\library_*.json`), merged by `load_library()`, split-by-prefix by `save_library()` (mvcommon.py:191-219). `ENTRY_TYPE_KEYS` (main.py:115-119): `leaf` (physical: folder_path/filename/status, NO `type`), `season_map` (virtual: type/folder_path/children/total_episodes), `multi_ep_alias` (virtual: alias_of/parent_id). Whole-library loops MUST skip virtual types or `_resolve_alias` (main.py:1668-1685). Guard: `tests/test_entry_schema_guard.py` + the smoke alias sweep.
- **Status lifecycle:** `local_ready` (prep) → `onboarded` (push, all chunks) → `archived` (replace; tiny dummy on disk) → `restored_local` (restore). "Fetched but not archived" == `restored_local` == `RESTORED_REPLACE_AGAIN`. Real-vs-dummy is decided by on-disk size vs `DUMMY_MAX_BYTES = 200_000` (main.py:36).
- **Fetch:** no `cmd_fetch` in main.py. `cmd_dispatch_fetch` (main.py:2863-2877) subprocesses `python mainfetch.py fetch <id> [episodes <range>]`. `cmd_fetch_restore` (main.py:2880-2915) = dispatch_fetch THEN `cmd_restore_group`(season_map) / `cmd_restore`(single). `mainfetch.fetch_single_entry` per-chunk queue (status `pending|done`) from `split_info.chunks`; **progress unit = chunks-done/total_chunks (NO byte %)**; single-flight `mvcommon.fetch_session_lock`; Selenium drives Chrome ON the Alienware; plain `print`s (worker captures stdout).
- **Hashing (verified):** `entry["hash"]` = sha256 of FILE bytes (`mvcommon.calculate_file_hash`); chunk hash = sha256 of chunk bytes. **NOT** folder/name/metadata. Adding tmdb id / poster / NFO or renaming a folder = zero byte change ⇒ **no rehash, no re-upload**. `cmd_set_poster`/`cmd_set_fanart` (main.py:984-1037) write `<folder_path>/poster.jpg|fanart.jpg` sidecars via `requests`. The web server does NOT currently serve media-folder images.

---

## Original task prompt

> Use the planner agent,
>
> Check out my latest PR and understand in detail what we did there for the web ui with all information.
>
> Currently it works great with all functionalities.. Now I want to improve this Ui and make some important changes to it.
>
> 1. Split the Ui page into 3 different sections, one for movies, one for Tv series, one for Anime and one for Others (Will come back to it later)
> The Ui should show this as a clickable tab on top - use your own creativity it needs to look elegant and smooth. If I click each tab it should filter only for files related to that.
> For example if I click movies tab ,
> Under it it should show the current tabs like unprepped ; prepped but not pushed , etc
> But it should only show movies filtered.  Similarly other 2 also should work tv series and anime.
>
> 2. Make the Ui even more interesting,  it needs to be super smooth, buttery and users should feel like coming back to the Ui page.
> Add some nice things like if I hover over a particular section or box or item (movie box or something) there should be a flickering effect highlighting the box - like keep moving constantly but in an aesthetic way. Add these kinds of stuff and other awesome features from online. Use any best skills for web also if needed.
> Install that skill globally so that claude can use in the future.
>
> 3. Add the fetch functionality , in each box or item now , currently it only shows prep command or next suggested commands etc.
> Add one more tab under movies similar to unprepped, that should show all the properly prepped,pushed and archived ones. If possible show the movie poster or image in the respective box in this tab. For functionality I should be able to click and do a fetch or fetch restore. Then while fetching it should show a proper working percentage bar or some interesting way of bar filling while it downloads or a highlighter around the box which keeps growing and finishes completely when the fetch and restore is completed meaning I can watch those now.  Once that fetch restore command completes this particular item should go to the Fetched but not archived section. So that i can action from that later once I finish watching.
>
> 4. Make this Ui also work on mobile phones- it should work both in same network - using the Alienware's ip address and tailscale ip - if I'm outside but connected to the same tailscale network, I should still be able to access this web ui and all functionalities should work. I can connect to the tailscale ip or whatever you suggest , even Alienware ip if it works. It should also look good and same smoothness in mobile screen also. Mostly I'll use from ipad or iPhone from safari browser- if you want you can code specifically for this.
>
> Give me different options for this improvement if you have multiple wats. let me check that and decide how to proceed. Once I confirm, I want you to create an elaborate plan to implement this in the best and optimal way. If any decision pending, give me live example in real world usecase complete step by step and ask me about the different options before you finalize the plan.
>
> Go through the improvements directory completely, any other related improvements, how this approach will affect that can you elaborate. Also any prerequisite small task you want me to complete before we start this implementation? If any thing is in line with it we can add it to this task and complete them also.
>
> Do not worry about the limits or usage for this task. I need the best output so decide accordingly. So no worries in assigning to candidates also and no-cap on candidates. If needed and genuinely multiple canddidates can help you can assign to the task. Also during orchestration if you are doing executors for candidate steps - you can do all of the executors in parallel. No need to do them sequentially.
>
> Deliverables: PLAN.md only (no code, no branches) with steps + tests + verification + "Open Decisions". Note if we are solving any improvement tasks with this task say C18 - IMP-C18 needs to be marked done in improvements_tierC.md on implementation, the architect updates ARCHITECTURE.md/README (documented behavior change), and I want branch name, PR to main, and manual test commands at the end. Also you also need to update the priority graph and suggest me the next starts we can start.

---

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

---

## IMP codes assigned (research-confirmed; this is the IMP-E12 family, NOT C18)

> C18 is DONE and unrelated — the user cited it only as an example of the done-marking convention. E12 (web console) and D16 (scan_reclaimable) are DONE. Next-free codes verified by grep: tier E → **E14, E15** free; tier D → **D17** free.

| Phase | New / existing IMP | Meaning |
|------|--------------------|---------|
| 0 | **IMP-A12** (existing, the current 👉 NEXT) | CI pipeline — lock the suite green before/parallel to this work. |
| 1 | **IMP-E14** (NEW) — "web media-type UI" | `GET /api/items` + Movies/TV/Anime/Others tabs nesting disk-state sub-views. Extends E12. |
| 2 | **IMP-E14** (continues) + down-payment on **IMP-S2** | fetch_restore action + worker incremental progress + Archived(fetchable) sub-view + chunk-% growing border + auto-flip. (The serialized worker IS the S2 daemon's seed; this is "E12 grown up".) |
| 3 | **IMP-E14** (continues) | aesthetic polish + author & install GLOBAL `web-ui-polish` skill + PWA manifest. |
| 4 | **IMP-E15** (NEW) — "mobile + Tailscale remote + token auth" + **IMP-A5** (existing, minimal slice) | localhost bind kept; `tailscale serve` doc+script; shared-token middleware; minimal config for host/port/token/TMDB-key; iOS-Safari responsive polish. |
| 5 | **IMP-E3** + **IMP-U3** (existing) + **IMP-D17** (NEW — `rename_folder` CLI) | TMDB auto-enrich (local-first) + `tmdb_id` field + media-image route + crash-safe cascading `rename_folder` + season inheritance + optional NFO. |

---

## Goal (definition of done, per phase)

- **P0:** `.github/workflows/ci.yml` runs `pytest -q` (+ smoke) on every PR into `main` on `windows-latest`; a red suite blocks merge. Green on a clean checkout.
- **P1:** Opening the console shows four media-type tabs; selecting one shows only that category's items, grouped into the existing disk-state sub-views; `GET /api/items` returns a season-aware, alias-safe, documented payload. `/api/reclaim` byte-identical to today. All existing actions still work.
- **P2:** An Archived item appears under its category's **Archived (fetchable)** sub-view with a "Fetch & Restore" button; clicking it shows a chunk-% growing-border progress that advances as chunks land and completes on restore; the card then auto-moves to "Fetched·not-archived". The worker exposes live `output` + `progress {done,total}` while running.
- **P3:** Hovering a card shows an aesthetic animated glow/flicker border; transitions are buttery; `prefers-reduced-motion` disables motion. A global `~/.claude/skills/web-ui-polish/SKILL.md` skill exists and is registered. The app installs to the iOS home screen via a manifest + apple meta tags.
- **P4:** From an iPad/iPhone on the same Tailscale tailnet (home AND away), `https://<machine>.<tailnet>.ts.net` loads the console and all functionality works; every state-changing request requires a shared token; localhost default unchanged. A documented one-liner sets up `tailscale serve`; a minimal `mvconfig.json` supplies host/port/token/TMDB key.
- **P5:** Running the enrich command populates `tmdb_id` + downloads posters for the library without fetching media; the media-image route serves real posters in cards (gradient fallback otherwise); `rename_folder` renames a show folder (incl. archived) + rewrites every descendant entry's `folder_path` crash-safely with no rehash; a "Dark"-style season inherits the show's tmdb token and shows per-season art where TMDB has it.

---

## Files affected (cumulative across phases)

- `.github/workflows/ci.yml` — NEW (P0): CI workflow.
- `requirements-dev.txt` — maybe touched (P0): ensure pytest/httpx present for CI.
- `webui/server.py` — P1 (`/api/items` + `_items_payload`), P2 (worker incremental flush + `progress`, `fetch_restore` in `ACTION_TABLE`), P4 (token middleware), P5 (media-image route).
- `webui/static/app.js` — P1 (tabs + sub-view grouping + `/api/items` render), P2 (Archived sub-view + Fetch&Restore + progress border + auto-flip + poller reads `progress`), P3 (animations wiring + reduced-motion), P4 (token header on requests + mobile fetch helpers). May split into ES modules (no build step).
- `webui/static/styles.css` — P1 (tab bar + sub-view layout), P2 (growing-border progress), P3 (hover glow/flicker, transitions, reduced-motion), P4 (responsive/iOS-Safari).
- `webui/static/index.html` — P1 (tab bar markup + poster slot in card template), P3 (PWA `<link rel="manifest">` + apple meta tags), P4 (viewport-fit, theme-color).
- `webui/static/manifest.webmanifest` — NEW (P3): PWA manifest.
- `webui/static/icons/` — NEW (P3): apple-touch-icon + manifest icons (generated, inline-safe; no external assets).
- `main.py` — P2 (none required — reuses `cmd_fetch_restore`), P5 (`tmdb_id` in metadata write path, `cmd_enrich_metadata`, `cmd_set_tmdb`, `cmd_rename_folder`, `_resolve_tmdb_for_entry` season-walk helper, `ENTRY_TYPE_KEYS`/guard if a shared field is added), P4 (config read for host/port/token).
- `mvcommon.py` — P4 (load `mvconfig.json` once at startup; keep current values as defaults), P5 (TMDB client helper + image-config constants if shared).
- `mvconfig.example.json` — NEW (P4): checked-in example; real `mvconfig.json` gitignored.
- `mainfetch.py` — P2 ONLY IF a per-chunk progress print needs standardizing for the parser (preferred: parse existing prints — see P2 risk; any change here trips the smoke gate).
- `tools/tailscale_serve_setup.ps1` — NEW (P4): documented setup script.
- `~/.claude/skills/web-ui-polish/SKILL.md` — NEW (P3): global skill (outside the repo).
- `tests/test_web_items.py`, `tests/test_web_progress.py`, `tests/test_web_auth.py`, `tests/test_web_media_image.py`, `tests/test_rename_folder.py`, `tests/test_enrich_metadata.py`, `tests/test_tmdb_resolve.py` — NEW per phase.
- `tests/conftest.py` — P5 (a `mock_tmdb` fixture; binding-hazard-adjacent → opus), maybe P2 (helper to seed an archived split entry if not already covered).
- `tests/smoke/test_smoke_all_commands.py` — extend (P2/P5) to drive new commands + assert `/api/items` import-safe.
- Per phase: `improvements/PRIORITY.md`, `improvements/improvements_tier{A,D,E,U}.md`, `docs/priority-graph/priority-graph.html`, `ARCHITECTURE.md`, `README.md`, `docs/feature-web-media-ui/{PLAN.md,DECISIONS.md}`, and per multi-candidate step a `docs/feature-web-media-ui/decisions/<step-id>-DECISION.md` (the judge's Decision Card — see §Multi-candidate decision protocol).

---

## Approach (end-to-end narrative)

The console already has a clean separation: a read-only data layer in `main.py` (`collect_reclaimable` + `classify_*` + `suggest_*`), a thin FastAPI surface in `server.py`, and a vanilla SPA. We grow each layer **additively**, never breaking `/api/reclaim`.

1. **Data first (P1).** Add `GET /api/items` — a NEW library-centric payload that, unlike `/api/reclaim` (disk-walk, reclaimable-only), enumerates EVERY physical leaf with a `state` (reusing `classify_entry_state` semantics + a new `ARCHIVED` row that reclaim deliberately drops) and a `category`. It is a whole-library iterator, so it MUST skip/resolve virtual entry types (the IMP-C12/PR#21 crash class). The SPA gains a top tab bar (Movies/TV/Anime/Others) that filters client-side by `category`, and within a tab groups cards by `state` into the familiar sub-views plus a new Archived sub-view.
2. **Fetch-in-UI (P2).** The Archived sub-view exposes "Fetch & Restore" → `POST /api/action/fetch_restore` (added to the allow-list; wraps the existing `cmd_fetch_restore` UNCHANGED). The single serialized worker is perfect for this (fetch is single-flight already). We make the worker flush captured stdout to the job record *incrementally* and parse a `progress {done,total}` from the per-chunk "done" lines; the SPA polls `/api/job/{id}`, drives a growing-border progress, and on terminal `done` re-fetches `/api/items` so the card auto-moves to Fetched·not-archived.
3. **Polish (P3).** Pure CSS/JS aesthetic layer (hover glow/flicker borders, buttery transitions) gated behind `prefers-reduced-motion`, authored once and captured as a reusable GLOBAL skill; plus a PWA manifest + apple meta tags so iOS "Add to Home Screen" gives a standalone app.
4. **Mobile + remote (P4).** Keep the localhost bind; document+script `tailscale serve` for HTTPS tailnet access; add a shared-token middleware (defense-in-depth for destructive actions) and a minimal `mvconfig.json` (the clean home for host/port/token/TMDB key, the first slice of IMP-A5); finish iOS-Safari responsive polish.
5. **Posters + rename (P5).** Add `tmdb_id` to metadata, a TMDB local-first enrich command, a media-image route serving `poster.jpg`/`fanart.jpg` (gradient fallback), a crash-safe cascading `rename_folder` (stamps `{tmdb-…}` on the show folder; rewrites every descendant `folder_path`; works on archived dummies; no rehash), and the "Dark" season-inheritance resolver. Cards then render real posters with smart per-season art.

The serialized worker is the IMP-S2 daemon's seed; doing fetch-in-UI here is an intentional down-payment on the couch-vault end goal.

---

## Phase 0 — IMP-A12 CI pipeline (prereq, separate quick PR)

**Branch:** `chore/imp_a12_ci_pipeline`
**Why first:** This task adds many web test modules; CI locks the suite so nothing merges red (the protector of everything built here). Cheap, low-risk, already the 👉 SUGGESTED NEXT task. Can run in parallel with Phase 1 authoring.

### Steps

- [ ] 0.1. [model: sonnet] [effort: medium] Add a GitHub Actions CI workflow running the full + smoke suites on Windows.
  - Files: `.github/workflows/ci.yml` (new); `requirements-dev.txt` (verify pytest + httpx present — `httpx` is the FastAPI TestClient dep, already added with IMP-E12).
  - Details: Workflow on `pull_request` (into `main`) and `push`. Single job: `runs-on: windows-latest`, `actions/setup-python@v5` with Python 3.11, `pip install -r requirements.txt -r requirements-dev.txt`, then `pytest -q` and a second step `pytest tests/smoke -q`. Windows runner because the code is Windows-pathed; sandbox fixtures already avoid real `C:\Media`. Do NOT add the optional choco/MKVToolNix real-split job in this PR (note it as a stretch follow-up in the workflow comments) — keep P0 minimal and fast. Making the workflow a required status check is a repo-settings action (note in the PR body for the human to enable at the merge gate; pairs with the existing human merge gate).
  - Acceptance: The workflow file is valid YAML; on the PR, the CI check runs and both pytest steps pass green on a clean checkout. Locally `pytest -q` and `pytest tests/smoke -q` still pass.

- [ ] 0.2. [model: haiku] [effort: low] Mark IMP-A12 done across the three tracking surfaces + advance the NEXT pointer.
  - Files: `improvements/PRIORITY.md`, `improvements/improvements_tierA.md`, `docs/priority-graph/priority-graph.html`.
  - Details: In `improvements_tierA.md` set IMP-A12 `Status: done` (note the workflow file + that the required-check toggle is a repo setting). In `PRIORITY.md`: move A12 to DONE, bump the **Last updated** line (line 12) to today, and set the **👉 SUGGESTED NEXT TASK** (line 16) to the start of this feature (IMP-E14 Phase 1) with IMP-S1 still noted as the cheap parallel win. In `priority-graph.html` set the `A12` TASKS entry `status` to `"done"` and priority `"done"` (line ~132). Keep the three in agreement (PRIORITY.md maintenance protocol, lines 89-98).
  - Acceptance: All three files show A12 done and agree; the NEXT pointer names the Phase-1 IMP code.

### Verification (Phase 0)
```
pytest -q
pytest tests/smoke -q
# On the PR: confirm the CI check appears and is green.
```

### PR to main (HUMAN-GATED — stop and ask)
Create the PR titled `chore: CI pipeline for the test suite — IMP-A12` with the IMP-E12-family context noted, then STOP and ask the user before merging. In the PR body ask the user to enable the required-status-check on `main` (repo setting). Embed the verbatim Original task prompt (above) per `docs/git-pr-conventions.md`.

### Manual test commands (Phase 0)
```
# locally, sanity:
python -c "import yaml; yaml.safe_load(open('.github/workflows/ci.yml')); print('workflow yaml ok')"
pytest -q && pytest tests/smoke -q
```

---

## Phase 1 — `GET /api/items` + media-type tabs (IMP-E14)

**Branch:** `feature/imp_e14_web_media_tabs`
**Depends on:** Phase 0 merged (so CI guards it) — but P1 authoring can begin in parallel.

### Steps

- [x] 1.0. [model: haiku] [effort: low] Seed the tracked plan/decisions artifacts for this feature.
  - Files: `docs/feature-web-media-ui/PLAN.md` (copy of this file), `docs/feature-web-media-ui/DECISIONS.md` (the 9 locked decisions + the IMP-code assignments table, verbatim from this plan).
  - Details: Create the `docs/feature-web-media-ui/` folder; copy `/PLAN.md` content into `docs/feature-web-media-ui/PLAN.md` identically; write `DECISIONS.md` capturing the locked-decisions table and IMP assignments so the rationale ships with the branch. (Root `/PLAN.md` stays gitignored; git-agent commits only the `docs/` copy.)
  - Acceptance: `docs/feature-web-media-ui/PLAN.md` is byte-identical to `/PLAN.md`; `DECISIONS.md` lists all 9 locked decisions + IMP codes.

- [x] 1.1. [model: opus] [effort: high] [candidates: 3] [WINNER: C — user-selected] Add a read-only `items_payload()` data builder in `main.py` (library-centric, season-aware, alias/season_map-safe) returning every physical leaf with category + state + poster/tmdb facts.
  - Files: `main.py` (new `items_payload()` near `collect_reclaimable`; reuse `classify_entry_state`, `human_readable_size`, `_category_of` logic — `_category_of` lives in server.py, so either import it or add a tiny `category_of(mid)` mirror in main.py and have server reuse it; decide in candidates).
  - Details: Return `{"items":[...], "by_category":{movies:N,series:N,anime:N,other:N}}`. Each item row: `id`, `category` (movies|series|anime|other), `state` (UNPREPPED|LOCAL_NOT_PUSHED|PUSHED_NOT_ARCHIVED|RESTORED_REPLACE_AGAIN|ARCHIVED), `size_bytes`, `path` (folder_path joined with filename when physical), `title` (`entry.metadata.title` or the id), `year` (`entry.metadata.year`), `tmdb_id` (from `entry.metadata.tmdb_id` if present else null — field arrives in P5; tolerate absence now), `poster_available` (false until P5's media-image route exists — wire the field now, always false here), `chunk_count` (`split_info.total_chunks` or 1), and for season context include `parent_id` when present. **Season-aware:** iterate physical leaves; for the ARCHIVED / reclaimable states reuse the EXACT `classify_entry_state(entry, on_disk_real)` decision where `on_disk_real = os.path.getsize(folder_path/filename) >= DUMMY_MAX_BYTES` (guard `OSError` → skip). **This is a whole-library iterator: MUST `if entry.get("type") in ("season_map","multi_ep_alias"): continue` BEFORE touching folder_path/filename** (the PR#21 crash class — `collect_reclaimable` is the model to copy, main.py:3188-3196). Do NOT mutate the library or touch media (strictly read-only). Unlike `/api/reclaim`, `/api/items` INCLUDES the `ARCHIVED` rows (that is the new data path that powers the Archived sub-view, locked decision #2/#5). De-dupe by normpath-lower like `collect_reclaimable`.
  - Acceptance: `python -c "import main; import json; print(json.dumps(main.items_payload(), default=str)[:200])"` runs without error against a sandboxed library; ARCHIVED rows are present; virtual entries never appear and never cause a KeyError.
  - Judge criteria: (1) correctness + alias/season_map-safety (must not KeyError on the `sandbox_alias` shape; must include ARCHIVED; must match `classify_entry_state` for the four reclaim states); (2) read-only purity (zero library/media mutation; no save_library call); (3) reuse vs duplication of the existing classify/suggest/category logic (prefer reuse, minimal new surface); (4) payload shape ergonomics for the SPA (flat, JSON-serializable, season context present).
  - Candidate approaches:
    - A: **Disk-anchored** — walk the three category roots like `collect_reclaimable` (PASS 1) and join to the library; ARCHIVED rows come from the library leaves whose on-disk file is a dummy. Closest to the existing reclaim code; reuses the walk.
    - B: **Library-anchored** — iterate `load_library()` leaves directly (single pass, no disk walk), `os.path.getsize` each to decide real-vs-dummy; category from id prefix. Faster, simpler, no double pass; the natural "library-centric" reading of locked decision #1.
    - C: **Hybrid/extracted-core** — factor the per-entry classification into a shared `_classify_item(mid, entry)` helper that BOTH `collect_reclaimable` and `items_payload` call (refactor reclaim to use it too), guaranteeing the two endpoints never drift on state semantics. Highest reuse, slightly larger blast radius (touches reclaim — must keep `/api/reclaim` byte-identical, verified by `test_web_endpoints`/smoke).

- [x] 1.2. [model: sonnet] [effort: medium] Expose `GET /api/items` in `server.py`.
  - Files: `webui/server.py` (new route `@app.get("/api/items")` returning `main.items_payload()`; mount BEFORE the static catch-all, same as `/api/reclaim`).
  - Details: Mirror `api_reclaim` exactly (read-only, returns the dict verbatim). Do NOT touch `/api/reclaim`, `/api/library`, the worker, or `ACTION_TABLE` in this step. Confirm the static mount stays LAST (server.py:354-355) so `/api/*` is never shadowed.
  - Acceptance: `GET /api/items` returns 200 with the payload; `GET /api/reclaim` response is byte-identical to before (assert in tests). `node --check webui/static/app.js` unaffected.

- [x] 1.3. [model: opus] [effort: high] [candidates: 2] [WINNER: B — user-selected; graft moot post-reconciliation] Add the top-level media-type tab bar + per-tab disk-state sub-view grouping to the SPA, driven by `/api/items`.
  - Files: `webui/static/index.html` (tab bar markup + a poster slot in `<template id="card-tpl">`), `webui/static/app.js` (fetch `/api/items`, tab state, group by `state` into sub-views, render), `webui/static/styles.css` (tab bar + sub-view section styling). May begin splitting `app.js` into ES modules via `<script type="module">` (NO build step; `node --check` must still pass on each module — verify each file individually).
  - Details: Tabs: **Movies / TV series / Anime / Others**, elegant + smooth (the user's words), keyboard-accessible (`role="tablist"`, `aria-selected`, arrow-key nav), one active at a time, with a live count badge per tab from `by_category`. Selecting a tab filters items to that `category` and renders them grouped into labelled sub-views in this order: **Unprepped → Local·not-pushed → Pushed·not-archived → Fetched·not-archived (RESTORED_REPLACE_AGAIN) → Archived (fetchable)**. Reuse the existing `BADGE_META` styling per state. Keep the existing per-card affordances (suggested command/folder/actions) for the non-archived states; the Archived sub-view's actions arrive in P2 (for now render Archived cards read-only with a poster slot + a disabled/"coming next" affordance, OR simply omit the action button — pick the cleaner of the two in candidates). Preserve XSS-safety (textContent only; never innerHTML for any id/title/path). Keep the existing hero/reclaim header OR fold it into the tab bar — candidate choice. The existing `/api/reclaim`-driven view may be retired in favor of `/api/items` for rendering, but `/api/reclaim` the ENDPOINT stays.
  - Acceptance: With a seeded library, the four tabs render with correct counts; clicking each shows only that category; within a tab, cards are grouped under the five sub-view headers; Archived items show under "Archived (fetchable)"; `node --check` passes on every JS file; no console errors; non-archived actions still work end-to-end.
  - Judge criteria: (1) UX quality of the tab/sub-view structure (clarity, the user's "elegant and smooth" bar, accessibility/keyboard nav); (2) correctness of category + state grouping against `/api/items` (incl. empty-state per sub-view); (3) code quality within the no-build vanilla constraint (clean module split, no framework, `node --check` green, textContent XSS-safety preserved); (4) minimal disruption to existing working affordances (suggested command/folder/copy/actions still function).
  - Candidate approaches:
    - A: **Tabs + collapsible sub-view sections** — each disk-state sub-view is a labelled collapsible section (accordion) inside the active tab; one scrolling column. Denser, fewer moving parts.
    - B: **Tabs + sub-view "rail" (segmented sub-nav)** — a secondary segmented control under the main tabs selects ONE sub-view at a time (Netflix-row-like), showing a single grid per selection. Closer to the eventual couch UI; more clicks but cleaner per-screen.

- [ ] 1.4. [model: opus] [effort: high] Add tests for `items_payload()` / `GET /api/items` (alias-safe, season-aware, reclaim-unchanged).
  - Files: `tests/test_web_items.py` (new). If a new conftest fixture is needed it goes in `tests/conftest.py` (binding hazard → this sub-task stays opus); prefer reusing the existing `sandbox`, `sandbox_alias`, `sandbox_entry` fixtures.
  - Details: Read `docs/testing-strategy.md` first to pick fixtures. Tests: (a) `items_payload()` over a `sandbox` library with leaves in each of the five states returns the right `category`+`state` per item; (b) over `sandbox_alias` (season_map + multi_ep_alias) it does NOT raise and emits exactly the physical leaves (no virtual rows); (c) ARCHIVED rows are present (the divergence from `/api/reclaim`); (d) via `TestClient(create_app())`, `GET /api/items` returns 200 and `GET /api/reclaim` is byte-identical to a snapshot taken before the change (guards locked decision #1). Use `TestClient` like the existing `tests/test_web_endpoints.py`. Constraints (MUST appear): "Never touch real `C:\\Media` files or real `library_*.json`." and "Run `pytest -q` and fix failures before marking the step done."
  - Acceptance: `pytest tests/test_web_items.py -q` passes; `pytest -q` stays green; the reclaim-unchanged assertion passes.

- [ ] 1.5. [model: sonnet] [effort: medium] Architect + docs: document `/api/items` and the media-type UI; mark IMP-E14 (Phase 1) progress.
  - Files: `ARCHITECTURE.md` (§5 web console subsection — add `/api/items` to the route list + describe the media-type tabs/sub-views + that `/api/reclaim` is unchanged), `README.md` (web UI section — mention the new tabs), `improvements/improvements_tierE.md` (add IMP-E14 with `Status: in_progress`, referencing E12 as parent and noting phases), `improvements/PRIORITY.md` (+ row/band, bump Last-updated + NEXT), `docs/priority-graph/priority-graph.html` (add `E14` node `["E14","web media tabs+fetch+posters","E","high","todo","…"]`, edges `["E12","E14"]` and `["A12","E14"]`).
  - Details: Keep PRIORITY.md, the tier file, and the graph in agreement (maintenance protocol). E14 stays `in_progress` until Phase 3 ends (the web-UI half); E15/D17/E3/U3 get marked at their phases.
  - Acceptance: ARCHITECTURE.md lists `/api/items`; the three tracking surfaces agree; graph has the E14 node + edges.

### Verification (Phase 1)
```
pytest tests/test_web_items.py -q
node --check webui/static/app.js          # (and: node --check each new module file)
pytest -q
pytest tests/smoke -q                       # MANDATORY final gate (touches main.py + webui)
```

### PR to main (HUMAN-GATED — stop and ask)
Title: `feature: web media-type tabs + /api/items library view — IMP-E14`. Create the PR, embed the verbatim Original task prompt, then STOP and ask before merging.

### Manual test commands (Phase 1)
```
python main.py web
# Browser (desktop): click Movies / TV series / Anime / Others — each filters to its category.
#   Confirm sub-view headers Unprepped / Local·not-pushed / Pushed·not-archived /
#   Fetched·not-archived / Archived (fetchable) appear with the right cards + counts.
# curl http://127.0.0.1:8765/api/items        -> library-centric payload incl. ARCHIVED rows
# curl http://127.0.0.1:8765/api/reclaim       -> unchanged shape (back-compat)
```

---

## Phase 2 — fetch_restore action + incremental progress + Archived sub-view chunk-% (IMP-E14 cont. / down-payment on IMP-S2)

**Branch:** `feature/imp_e14_fetch_in_ui`
**Depends on:** Phase 1 merged.

### Steps

- [x] 2.1. [model: opus] [effort: high] Add `fetch_restore` (and optional advanced `fetch`) to `ACTION_TABLE`, wrapping the existing `cmd_fetch_restore`.
  - Files: `webui/server.py` (`_run_fetch_restore(body)` → `main.cmd_fetch_restore(body.get("id"), (body.get("options") or {}).get("episodes"))`; add `"fetch_restore": (_run_fetch_restore, False)` to `ACTION_TABLE`, server.py:81-87; optionally `_run_fetch` → `main.cmd_dispatch_fetch(...)` as `"fetch"` — see Open Decisions).
  - Details: `cmd_fetch_restore` returns `None` throughout (it prints banners; main.py:2880-2915), so it CANNOT be distinguished success-vs-failure by return value — exactly like `prep_push_rep`. Decide deliberately: it is NOT destructive of local-only data (fetch downloads; restore merges/verifies and only quarantines on hash-mismatch), so treat a no-exception completion as `done` (its captured output carries the ✅/⚠️ banner). Recommended: add `"fetch_restore"` to `_NONE_IS_SUCCESS` (server.py:116) with a one-line comment mirroring the existing analysis block (server.py:89-116). The single serialized worker already guarantees only one fetch runs (and `mainfetch` has its own `fetch_session_lock` single-flight, mvcommon.py:82) so no new locking is needed. Do NOT change `main.py`.
  - Acceptance: `POST /api/action/fetch_restore {id}` returns 202 + job_id; the worker runs `cmd_fetch_restore`; under the smoke `mock_device` neutralization (which makes the `python mainfetch.py` subprocess a no-op success — see `tests/smoke` docstring) the action completes `done` without spawning Selenium.

- [x] 2.2. [model: opus] [effort: max] [candidates: 3] [WINNER: A — user-selected; + subprocess-streaming + conftest Popen-fake] Make the job worker flush captured stdout INCREMENTALLY and expose a parsed `progress {done,total}` derived from chunks-done/total_chunks.
  - Files: `webui/server.py` (`_worker_loop`, server.py:152-227; the job record shape; `redirect_stdout` capture). Possibly a small parsing helper. Avoid touching `mainfetch.py` if at all possible (any change there trips the smoke gate and the fetch-engine fragility) — prefer parsing the EXISTING per-chunk prints.
  - Details: Today the worker captures stdout into a `StringIO` and writes `output` ONLY at terminal (server.py:175-215). Change it so that WHILE a job runs, the partial captured output is periodically published to `JOBS[id]["output"]` under `JOBS_LOCK`, and a parsed `progress` dict `{"done":N,"total":M}` is published alongside. The progress unit is **chunks-done / total_chunks** (verified: NO byte %); derive `total` from the entry's `split_info.total_chunks` (1 for non-split) and `done` by counting per-chunk completion signals in the captured output (`mainfetch.fetch_single_entry` marks each chunk `status="done"` and the harvester moves each completed file — parse those lines). For `fetch_restore` of a season_map, total = sum of children's chunk counts (or number of episodes — pick the unit that the SPA border can render smoothly; document it; see Open Decision #8). On terminal `done`, set `progress` to `{done:total,total:total}`. Keep the serialized-worker invariants intact (still one job at a time; still catch `SystemExit` then `BaseException`; still `task_done()` in `finally`). The job record gains `progress` (and optionally `progress_unit`); existing `id/name/status/output/started_at` unchanged.
  - Acceptance: A long-running stub job shows `output` growing across successive `GET /api/job/{id}` polls (not just at the end) and a `progress` that advances; on completion `progress.done == progress.total`; `tests/test_web_endpoints.py` still green; the worker never wedges.
  - Judge criteria: (1) correctness + thread-safety (all `JOBS` reads/writes under `JOBS_LOCK`; no torn records; serialized-worker contract preserved; no deadlock); (2) faithfulness of `progress` to real chunk completion (no fake/linear-timer fudging — it must track actual chunks-done; smooth-tween is the SPA's job, not the server's); (3) blast radius (prefer parsing existing prints over editing `mainfetch.py`; if `mainfetch` must change, the smoke gate + manual fetch test must both pass); (4) robustness to non-fetch actions (push/replace/sort still report fine; absence of chunk lines degrades to status-only progress, never crashes).
  - Candidate approaches:
    - A: **Stdout tee + regex parse (server-only)** — replace the plain `StringIO` with a small write-through buffer that, on each write/newline, updates `JOBS[id]["output"]` and re-runs a regex over the accumulated text to count completed chunks (e.g. matches on the harvester's "moved/done" lines). Zero `mainfetch` changes; entirely inside `server.py`.
    - B: **Background flusher thread** — keep `redirect_stdout` to a `StringIO`, but spawn a short-lived helper thread (or use the worker's own loop tick) that snapshots `buf.getvalue()` into the job record every ~0.5s and parses progress. Decouples capture from publish; slightly more concurrency to reason about.
    - C: **Structured progress hook** — define a tiny module-level callback in `mainfetch` (e.g. `PROGRESS_HOOK(done,total)`) that `fetch_single_entry` calls when a chunk completes; the worker sets the hook for the duration of the job to publish exact `{done,total}` without text parsing. Most accurate + future-proof for IMP-F10/S2, but DOES edit `mainfetch.py` (smoke gate + manual fetch verification mandatory; keep the hook a no-op by default so CLI behavior is byte-unchanged).

- [x] 2.3. [model: opus] [effort: high] [candidates: 2] [WINNER: B — SVG ring, user-selected on real iPhone; + user enhancements: size-desc default + sort bar, readable titles + id-at-foot, expandable full-screen terminal, cursor-follow glow; + demo/safe mode; + upload-monotonicity guard (cmd_prep clobber fix) discovered here] Wire the Archived sub-view: Fetch & Restore button → poll job → growing chunk-% border → auto-flip the card to Fetched·not-archived on done.
  - Files: `webui/static/app.js` (Archived-card action + `pollJob` reads `progress` + border driver + post-done `/api/items` refresh), `webui/static/styles.css` (the growing-border progress + a full glowing loop on complete), `webui/static/index.html` (poster slot already added in P1; ensure the card template has a border/progress element).
  - Details: In the Archived (fetchable) sub-view each card gets a primary **"Fetch & Restore"** button → `POST /api/action/fetch_restore {id, options:{episodes:…}}` (episodes only for season selections — Open Decision on per-season UX; default whole-entry). On 202, poll `GET /api/job/{id}` (reuse the existing `pollJob`, app.js:409-443) and drive a **growing highlighter border** around the card from `progress.done/progress.total`: the border fills proportionally and SNAPS to a full glowing loop when status flips to `done`. Smooth the discrete chunk ticks with a CSS transition between values (the user explicitly wants "buttery", not steppy). Show the live `output` tail in the existing job panel. On terminal `done`, after the existing `REFRESH_AFTER_JOB_MS` delay, re-fetch `/api/items` (not `/api/reclaim`) and re-render so the card moves out of Archived into **Fetched·not-archived** (its state is now `RESTORED_REPLACE_AGAIN`) — locked decision #5. On `error`, surface the captured output faithfully (it carries the resume hint / RollbackHardFail `resume_cmd`) and leave the card in Archived. Keep XSS-safety. The "advanced plain fetch" button (download-only, no restore) is gated on the Open Decision — if enabled, it's a secondary action that does NOT auto-flip.
  - Acceptance: Clicking Fetch & Restore on an Archived card shows a border that grows with chunk progress and completes; the card then auto-moves to Fetched·not-archived without a manual reload; `node --check` passes; errors show the captured output.
  - Judge criteria: (1) the progress affordance matches the user's intent (growing border that tracks real chunks + buttery smoothing + a satisfying complete state) and stays accessible (text % alongside for reduced-motion); (2) correctness of the lifecycle auto-flip (Archived → Fetched·not-archived via `/api/items` refresh, no stale card); (3) robust polling/error handling (re-enable button on done OR error; faithful error output; no double-submit); (4) vanilla/no-build cleanliness + XSS-safety.
  - Candidate approaches:
    - A: **CSS conic-gradient border** — drive a `::before` conic-gradient ring via a CSS custom property `--progress` set from JS each poll, transitioned for smoothness. Single element, GPU-cheap, easy full-loop "glow" finish.
    - B: **SVG stroke-dashoffset ring** — an inline `<svg>` rounded-rect outline whose `stroke-dashoffset` animates from `progress`. More control over corners / an exact perimeter and easier to add a precise numeric %; slightly more DOM.

- [x] 2.4. [model: opus] [effort: high] Tests for the progress flush + fetch_restore action (serialized-worker safe, alias-safe).
  - Files: `tests/test_web_progress.py` (new). Reuse `tests/test_web_endpoints.py` patterns (real worker via FIFO + `_poll` helper). A helper to seed an archived split entry may live in the test file or `tests/conftest.py` (if conftest, this sub-task stays opus for the binding hazard).
  - Details: Read `docs/testing-strategy.md` first. Tests: (a) enqueue a stub action whose runner prints incrementally and assert `GET /api/job/{id}` shows `output` growing AND `progress.done` increasing across polls before terminal; (b) on completion `progress.done == progress.total` and `status=="done"`; (c) `POST /api/action/fetch_restore` returns 202 and, with the `python mainfetch.py` subprocess neutralized (as the smoke suite does) + a seeded archived split entry, the job reaches `done` and the worker did not wedge; (d) a runner that raises still records `status=="error"` with output (worker survives). Use a fake runner injected via the existing `ACTION_TABLE`/enqueue seam OR monkeypatch `main.cmd_fetch_restore`. Constraints (MUST appear): "Never touch real `C:\\Media` files or real `library_*.json`." and "Run `pytest -q` and fix failures before marking the step done."
  - Acceptance: `pytest tests/test_web_progress.py -q` passes; `pytest -q` green.

- [x] 2.5. [model: sonnet] [effort: medium] Extend the smoke suite to drive `fetch_restore` via the web action path + assert `/api/items` import-safety.
  - Files: `tests/smoke/test_smoke_all_commands.py`.
  - Details: Add a smoke case that POSTs `fetch_restore` through `create_app()`'s TestClient against the `sandbox`/`sandbox_alias` libraries and asserts no raise + terminal status (mirroring the existing `test_web_collect_reclaimable`/`test_web_reclaim_alias` cases). Confirm `/api/items` is import-safe and alias-safe (drive it against `sandbox_alias`). Keep the suite under ~30s. Constraints (MUST appear): "Never touch real `C:\\Media` files or real `library_*.json`." and "Run `pytest tests/smoke -q` and fix failures before marking the step done."
  - Acceptance: `pytest tests/smoke -q` passes and now exercises `fetch_restore` + `/api/items` against the alias library.

- [x] 2.6. [model: sonnet] [effort: medium] Architect + docs for the fetch-in-UI behavior change.
  - Files: `ARCHITECTURE.md` (§5 web console: add `fetch_restore` to the action allow-list; document the worker's incremental `output` + `progress {done,total}` and that it is chunk-based polling, NOT SSE/WebSocket — cross-reference IMP-F10 as the future upgrade; note this is a down-payment on IMP-S2), `README.md` (web UI: fetch-in-UI + progress), `improvements/improvements_tierE.md` (update IMP-E14 phase notes; add a one-line cross-reference under IMP-S2 that the serialized web worker is its seed and now performs fetch_restore). Update `docs/priority-graph/priority-graph.html` E14 note + add an edge `["E14","S2"]` (E14 feeds S2). Keep PRIORITY.md Last-updated current.
  - Acceptance: ARCHITECTURE.md documents the new action + progress contract; tracking surfaces agree.

### Verification (Phase 2)
```
pytest tests/test_web_progress.py -q
node --check webui/static/app.js          # (+ each module)
pytest -q
pytest tests/smoke -q                       # MANDATORY final gate (touches webui + maybe main.py)
```

### PR to main (HUMAN-GATED — stop and ask)
Title: `feature: fetch & restore in the web UI + live chunk-% progress — IMP-E14`. Create the PR, embed the verbatim Original task prompt, then STOP and ask before merging. **Note in the PR body:** rollback CHANGE-GATE is NOT tripped — `fetch_restore` reuses `cmd_fetch_restore`/`cmd_restore` UNCHANGED; no journal/PONR/`RollbackHardFail` change.

### Manual test commands (Phase 2)
```
python main.py web
# Browser: open a category tab -> Archived (fetchable) -> pick a REAL archived item -> Fetch & Restore.
#   Watch the growing border track chunks; confirm it completes and the card moves to Fetched·not-archived.
# Real end-to-end fetch sanity (uses Selenium + the live Chrome profile on the Alienware):
python main.py fetch_restore <some-archived-id>           # CLI path the UI wraps — must still work
python main.py fetch_restore tv-en-2019-chernobyl episodes 1-3   # season range still works
```

---

## Phase 3 — aesthetic polish + GLOBAL web-ui-polish skill + PWA manifest (IMP-E14 cont.)

**Branch:** `feature/imp_e14_ui_polish_pwa`
**Depends on:** Phase 2 merged.

### Steps

- [ ] 3.1. [model: opus] [effort: high] [candidates: 4] Design + implement the aesthetic motion layer: animated hover glow/flicker borders, buttery transitions, tasteful micro-interactions — with a `prefers-reduced-motion` fallback.
  - Files: `webui/static/styles.css` (the motion/effects layer), `webui/static/app.js` (only if an effect genuinely needs JS — prefer pure CSS), `webui/static/index.html` (only if an effect needs a wrapper element).
  - Details: The user wants the UI "super smooth, buttery", with a hover effect that "keeps moving constantly but in an aesthetic way" — i.e. an animated highlight border that travels/shimmers around a card on hover (NOT a jarring blink). Implement within the existing dark theme + CSS-variable palette (styles.css:4-29), **no external assets/fonts/CDNs** (the existing hard constraint), GPU-friendly (`transform`/`opacity`/`background-position` — avoid layout-thrashing properties). **MUST** wrap all non-essential motion in `@media (prefers-reduced-motion: reduce)` that disables it / replaces it with a static highlight, and keep focus-visible outlines intact for keyboard users. Keep the growing-border progress (P2) visually coherent with the hover border (they share the card's edge — don't fight each other). This is genuinely subjective design latitude — hence multi-candidate; the judge (and ultimately the human at the gate) picks the look.
  - Acceptance: Hovering a card produces a smooth, continuously-animating aesthetic border/glow; transitions across tab switches and card state changes feel buttery (no jank, 60fps-friendly properties); with reduced-motion enabled the animations stop and the UI stays fully usable; `node --check` passes; no external requests in the Network tab.
  - Judge criteria: (1) aesthetic quality + "buttery/come-back-to-it" feel matching the user's brief (the most important dimension — this is a taste call); (2) performance (only compositor-friendly properties; no reflow on hover; smooth on an iPad-class GPU); (3) accessibility correctness (`prefers-reduced-motion` fully honored, focus-visible preserved, no motion-sickness traps); (4) self-contained vanilla compliance (no CDN/font/asset, coherent with the existing palette + the P2 progress border).
  - Candidate approaches:
    - A: **Conic-gradient rotating border** — an animated `conic-gradient` ring behind the card (rotating via `@property --angle` or a `background` animation) that brightens on hover. Modern, smooth, single pseudo-element.
    - B: **Animated gradient sheen sweep** — a `linear-gradient` highlight that travels across the card edge/surface on hover via `background-position` keyframes (a "shine" that keeps moving). Subtle, performant.
    - C: **Dual-layer glow + border-trace** — a soft outer `box-shadow` glow that pulses gently plus a thin border whose highlight point traces the perimeter (masked gradient). Richer, layered look.
    - D: **Spotlight follow (JS pointer-position)** — a radial highlight that follows the cursor within the card (CSS variables `--mx/--my` set on `pointermove`), giving a living "flicker" that tracks the user. The one candidate that uses minimal JS; most "alive" feel; must still degrade with reduced-motion and not jank.

- [ ] 3.2. [model: opus] [effort: high] Author the GLOBAL `web-ui-polish` skill and install it under `~/.claude/skills/`.
  - Files: `~/.claude/skills/web-ui-polish/SKILL.md` (NEW — outside the repo, in the user's global Claude config; this is the one step that writes outside the repo, as the task requires "Install that skill globally").
  - Details: Capture the reusable, framework-agnostic recipes proven in 3.1 as a global skill: a YAML frontmatter (`name: web-ui-polish`, a `description` covering "buttery web UI motion: hover glow/flicker borders, smooth transitions, progress borders, reduced-motion fallbacks, no-build vanilla CSS") plus the body — the CSS patterns (conic/gradient/glow/spotlight), the `prefers-reduced-motion` discipline, GPU-friendly-property guidance, and accessibility notes. Keep it generic (not MediaVault-specific) so future projects benefit. **Snapshot caution:** this skill lives in the GLOBAL skills tree, NOT under `.claude/agents/` — it does not touch the agent registry, so the agent-frontmatter / duplicate-name footguns do not apply; still, validate the SKILL.md YAML frontmatter parses. After writing, confirm the skill registers by checking the available-skills list in a fresh session.
  - Acceptance: `~/.claude/skills/web-ui-polish/SKILL.md` exists with valid frontmatter; the skill appears in the available-skills list in a new session; the body documents the 3.1 patterns + reduced-motion + no-build constraints.

- [ ] 3.3. [model: sonnet] [effort: medium] Add a PWA manifest + iOS-Safari "Add to Home Screen" meta tags so the console installs as a standalone app.
  - Files: `webui/static/manifest.webmanifest` (new), `webui/static/icons/` (new — self-generated PNG icons, no external fetch; 180×180 apple-touch-icon + 192/512 manifest icons), `webui/static/index.html` (add the manifest link + apple meta tags), `webui/server.py` (only if the manifest/icon MIME needs help — `StaticFiles` already serves them; verify `.webmanifest` is served with a sane content-type, else add a tiny explicit route).
  - Details: Pre-resolved facts (cite in code comments). Manifest: `{"name":"MediaVault Console","short_name":"MediaVault","start_url":"/","display":"standalone","background_color":"#0b0f17","theme_color":"#0b0f17","icons":[{192},{512}]}` — note **Safari on iOS/iPadOS only supports `display:standalone`** (Apple docs). iOS meta tags in `<head>`: `<link rel="manifest" href="./manifest.webmanifest">`, `<meta name="apple-mobile-web-app-capable" content="yes">`, `<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">`, `<meta name="apple-mobile-web-app-title" content="MediaVault">`, `<link rel="apple-touch-icon" href="./icons/apple-touch-icon.png">`, and a `theme-color` meta. (Apple still honors `apple-mobile-web-app-capable`; pair it with the manifest — web.dev notes a missing/broken manifest harms the install, so ship both.) Keep everything same-origin / no-CDN.
  - Acceptance: `GET /manifest.webmanifest` and the icons return 200 with correct content-types; desktop Chrome shows the app as installable; iOS Safari "Add to Home Screen" produces a standalone (no Safari chrome) launch on next open. `node --check` unaffected.

- [ ] 3.4. [model: haiku] [effort: low] Doc update: record the polish layer + PWA + the global skill; flip the IMP-E14 web-UI half to done.
  - Files: `ARCHITECTURE.md` (web console: note the no-build motion layer + reduced-motion + PWA installability), `README.md` (mention Add-to-Home-Screen), `improvements/improvements_tierE.md` (IMP-E14: web-UI portion done; remote/posters continue in E15/E3/U3/D17), `improvements/PRIORITY.md` (Last-updated bump), `docs/priority-graph/priority-graph.html` (E14 note).
  - Acceptance: Docs mention polish + PWA + the global skill; tracking surfaces agree.

### Verification (Phase 3)
```
node --check webui/static/app.js          # (+ each module)
python -c "import json; json.load(open('webui/static/manifest.webmanifest')); print('manifest ok')"
pytest -q
pytest tests/smoke -q                       # MANDATORY final gate (webui touched; server.py may be)
```

### PR to main (HUMAN-GATED — stop and ask)
Title: `feature: buttery UI motion + PWA add-to-home-screen — IMP-E14`. Create the PR, embed the verbatim Original task prompt, then STOP and ask before merging.

### Manual test commands (Phase 3)
```
python main.py web
# Desktop: hover cards (smooth animated border), switch tabs (buttery); toggle OS "reduce motion" -> animations stop.
# iPad/iPhone Safari (LAN; full remote enabled next phase): open http://<alienware-LAN-ip>:8765 ,
#   Share -> Add to Home Screen -> launch from the icon -> confirm standalone (no Safari bars).
```

---

## Phase 4 — mobile + Tailscale remote + shared-token auth + minimal config (IMP-E15 + IMP-A5 slice)

**Branch:** `feature/imp_e15_mobile_tailscale_auth`
**Depends on:** Phase 3 merged.

### Steps

- [ ] 4.1. [model: opus] [effort: high] Add minimal `mvconfig.json` support (host / port / web token / TMDB key) loaded once at startup, defaults = today's values (the first slice of IMP-A5).
  - Files: `mvcommon.py` (load `mvconfig.json` from the project root or `~/.mediavault/mvconfig.json` once at import/startup; expose getters with the current hardcoded values as fallbacks), `main.py` (`cmd_web` + the `web` argv arm read host/port from config when flags are absent; flags still override), `mvconfig.example.json` (new, checked in), `.gitignore` (ensure `mvconfig.json` is ignored).
  - Details: Keep it MINIMAL — only the keys this feature needs: `{"web":{"host":"127.0.0.1","port":8765,"token":null,"require_token_for_reads":true},"tmdb":{"api_key":null}}`. An absent file or absent key is a no-op (defaults preserve today's behavior — localhost, 8765, no token, no TMDB). Document that this is a deliberate minimal slice of the full IMP-A5 config (do not migrate all the other constants now — out of scope). **Binding-hazard note:** mvcommon is imported-by-value into main; config getters should be CALLED at runtime (not bound as module constants) to avoid the dual-binding hazard. Validate the JSON shape; a malformed config should warn and fall back to defaults, never crash.
  - Acceptance: With no `mvconfig.json`, `python main.py web` behaves exactly as today (localhost:8765, no token). With a config setting port 9000, `python main.py web` (no `--port`) binds 9000; `--port` still overrides. `pytest -q` green.

- [ ] 4.2. [model: opus] [effort: max] Add a shared-token auth check (FastAPI middleware/dependency) protecting at minimum `POST /api/action/*`; configurable to also cover read endpoints; localhost requests exempt by default.
  - Files: `webui/server.py` (token dependency/middleware applied to the action routes; read endpoints behind a config flag — see Open Decisions), `webui/static/app.js` (attach the token to every `fetch` — header `X-MediaVault-Token` or a cookie set from a one-time `?token=` on first load), `webui/static/index.html` (only if a token-entry affordance is needed).
  - Details: The console runs **destructive replace + now fetch_restore**, and Phase 4 exposes it beyond localhost — so the token is mandatory-in-spirit (locked decision #3). Behavior: read the token from `mvconfig.json` (`web.token`, 4.1). If a token is configured, **every state-changing request** (`POST /api/action/*`) MUST present it (`X-MediaVault-Token: <token>`), else **401**. **Localhost exemption:** a request whose client host is `127.0.0.1`/`::1` is exempt by default (so the local `python main.py web` + auto-opened browser keeps working with zero friction); remote (Tailscale) requests are NOT exempt. Make "also require the token on read endpoints (`/api/items`,`/api/reclaim`,`/api/library`,`/api/job`)" a config flag (`web.require_token_for_reads`, default TRUE — see Open Decision #4). The SPA, when loaded over a non-localhost origin, must obtain + send the token: support a one-time `https://…ts.net/?token=XYZ` that the SPA stores (sessionStorage) and thereafter sends as the header; show a minimal token-entry prompt if missing / on 401. Never log the token; never put it in the captured job output. If no token is configured AND the bind host is non-localhost, print a loud startup WARNING (see Open Decision #5; recommend warn-loudly, don't hard-refuse).
  - Acceptance: With a token configured: `POST /api/action/replace` from a non-localhost client without the header → 401; with the correct header → proceeds; localhost POST without header still works (exemption). With no token: today's behavior (note the startup warning when bound non-localhost). The SPA over Tailscale prompts for / carries the token and all actions work. `pytest -q` green.

- [ ] 4.3. [model: sonnet] [effort: medium] Finish responsive / iOS-Safari polish so the console looks + feels native on iPad/iPhone.
  - Files: `webui/static/styles.css` (responsive breakpoints, touch targets ≥44px, safe-area insets), `webui/static/index.html` (`<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">` — add `viewport-fit=cover` to the existing base viewport), `webui/static/app.js` (touch-friendly interactions; ensure hover effects degrade to tap/focus on touch devices).
  - Details: Target iPad/iPhone Safari specifically (the user's primary devices). Make the tab bar + sub-views + cards reflow to one column on narrow widths; respect `env(safe-area-inset-*)` so content clears the notch / home indicator in standalone PWA mode; ensure the growing-border progress + posters render crisply on retina; convert hover-only affordances to also trigger on tap/focus (no hover on touch). Keep the same "smoothness" on mobile (the user's explicit ask). No CDN; system font stack.
  - Acceptance: On an iPad/iPhone (or a device-emulated viewport), the console is fully usable single-handed: tabs reachable, cards full-width, actions tappable, no horizontal scroll, content clears the safe areas in the home-screen app; the look matches desktop quality. `node --check` passes.

- [ ] 4.4. [model: sonnet] [effort: medium] Author the `tailscale serve` setup script + document the one-time admin prerequisites.
  - Files: `tools/tailscale_serve_setup.ps1` (new), `README.md` (a "Remote access over Tailscale" section), `docs/feature-web-media-ui/REMOTE_ACCESS.md` (optional fuller guide).
  - Details: **Pre-resolved exact commands (cite in the script + docs):** One-time admin-console prerequisites — in the Tailscale admin console enable **MagicDNS** (if not already) and under **HTTPS Certificates** select **Enable HTTPS** (both required before `tailscale serve` can provision a cert for the tailnet DNS name). Then on the Alienware run, in the background and persistent across reboots:
    ```
    tailscale serve --bg --https=443 127.0.0.1:8765
    ```
    This publishes `https://<machine>.<tailnet>.ts.net` → `http://127.0.0.1:8765` to the tailnet (HTTPS, tailnet-only — NOT `tailscale funnel`, which would expose to the public internet; we deliberately use `serve`, not `funnel`). Status: `tailscale serve status` (or `--json`). Turn off: `tailscale serve --https=443 127.0.0.1:8765 off`; reset all: `tailscale serve reset`. The `.ps1` should: check `tailscale` is on PATH, print the machine's tailnet name (`tailscale status`), run the serve command, print the resulting `https://…ts.net` URL, and remind the user to set `web.token` in `mvconfig.json` first (the token is what protects the now-remote destructive actions). Document that the app STILL binds localhost (`127.0.0.1:8765`) — Tailscale does the TLS termination + tailnet routing; we never bind `0.0.0.0`.
  - Acceptance: The script runs the documented command and prints the `…ts.net` URL; README documents the MagicDNS + Enable-HTTPS prerequisites and the exact serve/status/off/reset commands; it is explicit that this is tailnet-only (serve, not funnel) and the app stays localhost-bound.

- [x] 4.5. [model: opus] [effort: high] Tests for the token auth (localhost-exempt; remote-required; read-flag behavior).
  - Files: `tests/test_web_auth.py` (new).
  - Details: Read `docs/testing-strategy.md` first. Use `TestClient(create_app())`. Tests: (a) no token configured → `POST /api/action/sort` works (today's behavior) — patch the config getter to no-token; (b) token configured → POST without the header from a simulated non-localhost client → 401; with the header → 202; (c) localhost client without header → still works (exemption) — document how the middleware determines the client host under TestClient and test that path; (d) `require_token_for_reads=True` → `GET /api/items` without token → 401; `=False` → 200. Inject config by monkeypatching the config getter (do NOT write a real `mvconfig.json`). Constraints (MUST appear): "Never touch real `C:\\Media` files or real `library_*.json`." and "Run `pytest -q` and fix failures before marking the step done."
  - Acceptance: `pytest tests/test_web_auth.py -q` passes; `pytest -q` green.

- [ ] 4.6. [model: sonnet] [effort: medium] Architect + docs + mark IMP-E15 done; create IMP-E15 in the tier file; wire graph.
  - Files: `ARCHITECTURE.md` (web console: localhost bind unchanged; new shared-token auth model + localhost exemption + `mvconfig.json` minimal slice; Tailscale-serve remote-access path; explicitly that this does NOT change the rollback contract), `README.md`, `improvements/improvements_tierE.md` (add **IMP-E15** "mobile + Tailscale remote + token auth", `Status: done` on merge; cross-ref E14 + the IMP-A5 minimal slice), `improvements/improvements_tierA.md` (note IMP-A5 received a minimal slice — host/port/token/TMDB-key — and remains `pending` for the full config migration), `improvements/PRIORITY.md` (rows + Last-updated + NEXT), `docs/priority-graph/priority-graph.html` (add `E15` node + edges `["E14","E15"]`, `["A5","E15"]`; note A5 partial).
  - Acceptance: ARCHITECTURE.md documents the security + remote model; E15 exists and is marked; A5 partial-slice noted; all three tracking surfaces agree.

### Verification (Phase 4)
```
pytest tests/test_web_auth.py -q
node --check webui/static/app.js          # (+ each module)
pytest -q
pytest tests/smoke -q                       # MANDATORY final gate (server.py + main.py + mvcommon.py touched)
```

### PR to main (HUMAN-GATED — stop and ask)
Title: `feature: mobile + Tailscale remote access + shared-token auth — IMP-E15`. Create the PR, embed the verbatim Original task prompt, then STOP and ask before merging. **Note in the PR body:** the token MUST be set before exposing over Tailscale (destructive actions become remotely reachable).

### Manual test commands (Phase 4)
```
# 1. set a token + (optional) port in mvconfig.json (copy mvconfig.example.json)
# 2. start the app (still localhost-bound):
python main.py web
# 3. expose over Tailscale (one-time admin: enable MagicDNS + Enable HTTPS in the admin console first):
powershell -ExecutionPolicy Bypass -File tools/tailscale_serve_setup.ps1
tailscale serve status                       # confirm https://<machine>.<tailnet>.ts.net -> 127.0.0.1:8765
# 4. iPad/iPhone Safari over Tailscale (home AND away from home, same tailnet):
#    open https://<machine>.<tailnet>.ts.net  -> enter token -> exercise EVERY action
#    (tab filter, prep/push/replace with confirm, Fetch & Restore with the growing border).
#    Confirm Add-to-Home-Screen still works over HTTPS and feels native.
# 5. negative: from the tailnet WITHOUT the token, a replace/fetch_restore POST must be refused (401).
tailscale serve --https=443 127.0.0.1:8765 off   # teardown when done
```

---

## Phase 5 — TMDB auto-enrich (local-first) + tmdb_id field + media-image route + crash-safe cascading rename_folder + season inheritance (IMP-E3 + IMP-U3 + IMP-D17)

**Branch:** `feature/imp_e3_u3_d17_tmdb_posters_rename`
**Depends on:** Phase 4 merged. (Largest phase — keep steps independently verifiable.)

> **CONSUMER IMPACT ANALYSIS is REQUIRED here** — this phase adds a shared data-field (`metadata.tmdb_id`) and the `rename_folder` command rewrites the shared `folder_path` field across many entries. See the dedicated section below the steps.

### Steps

- [x] 5.1. [model: opus] [effort: high] Add `tmdb_id` to the leaf entry metadata schema + a manual `set_tmdb` override command; update `ENTRY_TYPE_KEYS`/guard if a shared field set is touched.
  - Files: `main.py` (`cmd_set_tmdb(manual_id, tmdb_id)` mirroring `cmd_set_search`/`cmd_set_poster`; ensure the prep path can carry `metadata.tmdb_id` when known; `ENTRY_TYPE_KEYS` main.py:115-119 + `tests/test_entry_schema_guard.py` IF the guard keys on `metadata` sub-fields — check first), argv arm + ARCHITECTURE §5 command table.
  - Details: `metadata.tmdb_id` is an OPTIONAL leaf field (additive; absent on all existing entries — every consumer MUST use `.get()`). `cmd_set_tmdb` loads the library, sets `library[id]["metadata"]["tmdb_id"] = <int/str>`, saves. This is a zero-byte-change metadata edit (no rehash). Check whether `ENTRY_TYPE_KEYS` enumerates `metadata` sub-fields — per main.py:115-119 it currently lists only top-level `required` keys, so adding an OPTIONAL nested `metadata.tmdb_id` likely needs NO `ENTRY_TYPE_KEYS` change; confirm by reading the guard test and update both ONLY if it asserts on metadata sub-fields. **Do NOT alter season_map/multi_ep_alias shapes.**
  - Acceptance: `python main.py set_tmdb mov-en-2025-f1 12345` sets `metadata.tmdb_id`; `pytest tests/test_entry_schema_guard.py -q` passes (updated iff needed); `pytest -q` green.

- [ ] 5.2. [model: opus] [effort: max] [candidates: 2] Add a media-image route serving local `poster.jpg`/`fanart.jpg` with a graceful gradient fallback + smart season inheritance for which image to serve.
  - Files: `webui/server.py` (new `GET /api/media-image/{id}?kind=poster|fanart` route), `main.py` (a read-only resolver `resolve_artwork_path(library, mid)` implementing locked decision #8 walk), `webui/static/app.js` (cards point `<img>` / background at the route; on error → CSS gradient placeholder), `webui/static/styles.css` (gradient fallback).
  - Details: The route resolves the on-disk image for an entry and streams it (`FileResponse`), or returns 404 so the SPA shows the gradient. **Resolution order (locked decision #8 — "Dark" requirement):** for the requested entry, find the artwork by: (i) the entry's own folder `poster.jpg` if present (a locally-present image ALWAYS wins); else (ii) the season_map's folder image; else (iii) walk UP the on-disk `folder_path` ancestors to the nearest folder carrying a `{tmdb-…}` token (the show folder) and use its `poster.jpg`. Season-specific art: when the id encodes a season (e.g. `…-s02`), prefer a season-specific local image if present, else the show poster. **This is read-only + path-only** (no library mutation; guard all `entry.get` / `os.path` with existence checks; alias/season_map-safe — resolve via `_resolve_alias` / skip virtual where needed). Security: the route MUST serve only files named exactly `poster.jpg` / `fanart.jpg` (and season variants) UNDER a known library `folder_path` — never an arbitrary path from the client (prevent path traversal); derive the path from the library entry, not from client input beyond the `id` + `kind`. Honor the Phase-4 token rule for reads if `require_token_for_reads` is on.
  - Acceptance: For an entry with a local `poster.jpg`, `GET /api/media-image/{id}?kind=poster` returns the image; for one without, returns 404 and the card shows the gradient; a season episode with only a show-level token+poster inherits the show poster; no path-traversal is possible (a crafted id cannot escape the library folders). `pytest -q` green.
  - Judge criteria: (1) correctness of the season-inheritance resolution (own → season_map → folder-walk to `{tmdb-…}` show → season-specific-vs-show fallback; local-always-wins) — the "Dark" requirement must demonstrably work; (2) security (no path traversal; only whitelisted filenames under library folders; token honored); (3) read-only + alias-safety; (4) SPA integration cleanliness (graceful 404→gradient, no broken-image flashes, retina-crisp).
  - Candidate approaches:
    - A: **Resolve-on-request** — the route calls `resolve_artwork_path` per request (load library handle, walk ancestors), streams or 404s. Simple, always-correct, a little work per image.
    - B: **Resolve via `/api/items` precompute** — `items_payload` (P1) computes `poster_available` + a resolved relative artwork key once; the image route trusts that mapping. Fewer per-request walks (faster on a big grid), but couples the two endpoints and must invalidate when files change.

- [x] 5.3. [model: opus] [effort: max] Implement the crash-safe cascading `rename_folder` CLI command (IMP-D17): rename the on-disk folder + atomically rewrite `folder_path` for every descendant entry; works on archived dummies; no rehash.
  - Files: `main.py` (`cmd_rename_folder(old_folder_or_id, new_folder_name_or_token)`; reuse the existing `RollbackJournal` / atomic-save machinery — see CHANGE-GATE note), argv arm, ARCHITECTURE §5 command table + §6/§7 description.
  - Details: Purpose (locked decision #7): stamp a `{tmdb-12345}` token onto a SHOW folder and have all seasons/episodes follow. Behavior: given a folder path (or a show/season id that resolves to a folder), rename the on-disk directory, then **rewrite `folder_path` for EVERY library entry whose `folder_path` is that folder OR lives UNDER it** (all seasons/episodes of a show; season_map entries too — their `folder_path` = first episode's folder). It MUST: (a) be **crash-safe** — perform the on-disk rename + the JSON pointer rewrites under the existing journal so an interruption is recoverable (use `RollbackJournal` / atomic `save_library`; the rename is the point-of-no-return seam — mirror `cmd_replace`'s pattern); (b) work on **archived** folders (the files are dummies — a rename moves dummies + the `{tmdb-…}` token folder identically; no special-casing needed); (c) be **hash-safe** — it only moves a folder + rewrites JSON `folder_path` strings; it NEVER re-hashes, re-splits, or re-uploads (verified: hash is over file bytes, not path — ARCHITECTURE §7.4 / Verified facts). The on-disk `uid`/`.sha256` sidecars MOVE with the folder (no content change). **Alias/season_map-safe:** iterate the whole library to find descendants; DO rewrite a season_map's `folder_path`; a `multi_ep_alias` has no `folder_path` (verify and skip). **CHANGE-GATE (mandatory):** `rename_folder` must be crash-safe but MUST NOT alter EXISTING rollback behavior — same journal format, same `fsync`+`os.replace` durability, same PONR / `mark_point_of_no_return` semantics; it ADDS a new journaled operation, it does not modify `cmd_prep/push/replace/restore` rollback or the `.mediavault_txn.json` format. If any chosen design WOULD change existing rollback behavior, STOP and surface to the user as an explicit decision (per CLAUDE.md auto-rollback change-gate) rather than proceeding.
  - Acceptance: `python main.py rename_folder <show-id-or-folder> "<NewName {tmdb-12345}>"` renames the dir and rewrites `folder_path` for the show's season_map + all episode leaves (incl. archived dummies); a simulated crash mid-rename is recoverable via `recover`; `cmd_check`/restore on a moved-but-already-uploaded entry still pass (no rehash); existing rollback tests stay green.

- [ ] 5.4. [model: opus] [effort: max] [candidates: 3] Implement TMDB auto-enrich (local-first): `cmd_enrich_metadata` querying TMDB by title+year, writing `tmdb_id` + stamping the `{tmdb-…}` folder token (via `rename_folder`) + downloading poster/fanart for the whole library WITHOUT fetching media; local images always win; ambiguous matches listed for confirmation.
  - Files: `main.py` (`cmd_enrich_metadata([id_or_prefix] [--apply] [--library …] [--nfo])`, a TMDB client helper; reuse `requests` + `cmd_set_poster`/`cmd_set_fanart` download logic + `cmd_rename_folder` for the token stamp; read the API key from `mvconfig.json` tmdb.api_key — Phase 4), `mvcommon.py` (optional shared TMDB image-config constants), argv arm.
  - Details: **Pre-resolved TMDB facts (cite in code comments; executors cannot browse):**
    - Image config endpoint: `GET https://api.themoviedb.org/3/configuration` → `images.secure_base_url` (currently `https://image.tmdb.org/t/p/`) + `images.poster_sizes` (`["w92","w154","w185","w342","w500","w780","original"]`). **Recommended poster size for the card grid: `w342`** (retina: `w500`). Full image URL = `secure_base_url + size + file_path` (e.g. `https://image.tmdb.org/t/p/w342/<file_path>.jpg`).
    - Movie search: `GET https://api.themoviedb.org/3/search/movie?query=<title>&year=<year>` (api_key via `?api_key=` or `Authorization: Bearer` v4 token).
    - TV search: `GET https://api.themoviedb.org/3/search/tv?query=<title>&first_air_date_year=<year>`.
    - TV series images (show-level poster/fanart): `GET https://api.themoviedb.org/3/tv/{series_id}/images`.
    - **TV SEASON images** (per-season poster — the "Dark" per-season art): `GET https://api.themoviedb.org/3/tv/{series_id}/season/{season_number}/images`.
    - (Episode images, if ever wanted: `GET https://api.themoviedb.org/3/tv/{series_id}/season/{n}/episode/{m}/images`.)
    Logic: for each entry (skip virtual types), parse title+year (reuse `parse_metadata_from_id` for year; title from the id slug or `metadata.title`), search the right endpoint (movie for `mov-`, tv for `tv-`/`ani-` — note anime via TMDB tv is imperfect; AniDB/AniList is a future IMP-E3 extension, out of scope now), and: write `metadata.tmdb_id`; stamp the `{tmdb-…}` token on the SHOW folder via `cmd_rename_folder` (so seasons inherit — locked decision #7/#8); download `poster.jpg`/`fanart.jpg` into the folder (show-level, plus per-season where TMDB has it) — BUT **a locally-present image ALWAYS wins** (never overwrite an existing `poster.jpg` the user placed; only fill when absent). **Local-first + WITHOUT fetching media** (locked decision #6): this NEVER touches `cmd_fetch` / media bytes — it only reads ids, calls TMDB, writes small JPGs + JSON. **Ambiguous matches:** when the top search result is uncertain (multiple close hits / low confidence), do NOT guess — list the candidates for user confirmation (print a chooser; `--apply` writes only confident matches, ambiguous ones are reported for a follow-up `set_tmdb`). Cache responses under `~/.mediavault/cache/metadata/<id>.json` (idempotent re-runs). **Dry-run default** (`--apply` to write), per the destructive-ish nature of renaming folders.
  - Acceptance: `python main.py enrich_metadata mov-en-2025-f1 --apply` writes `metadata.tmdb_id`, stamps the folder token, and downloads a `poster.jpg` (without overwriting an existing one); a run over a small fixture library populates tmdb_ids + posters with zero media fetches; ambiguous titles are listed, not silently mis-set. `pytest -q` green.
  - Judge criteria: (1) correctness + safety of the local-first contract (never overwrite a user's local image; never fetch media; dry-run default; ambiguous → list-not-guess); (2) correct TMDB endpoint usage + the season-token cascade that makes inheritance work (uses `rename_folder` to stamp the show folder so seasons inherit); (3) robustness (API failure / no key / rate-limit degrade gracefully and NEVER corrupt the library or block — additive only; cache for idempotency); (4) reuse (leans on `cmd_set_poster`/`cmd_rename_folder`/`requests`, minimal new surface) + alias/season_map-safety.
  - Candidate approaches:
    - A: **Per-entry inline** — one function loops entries, doing search→write→token→download per entry synchronously. Simplest; slow over 570 entries but fine as a backfill.
    - B: **Two-phase (resolve then apply)** — phase 1 resolves every entry to a TMDB id (and flags ambiguous ones), writing a review file; phase 2 (`--apply`) consumes the reviewed mapping to stamp tokens + download art. Cleaner human-in-the-loop for the ambiguous-match requirement; matches the dry-run/apply split.
    - C: **Show-centric** — group entries by show folder first (series/anime), resolve the SHOW once, stamp the token once (one `rename_folder` per show, not per episode), fetch show + season images in a batch; movies handled singly. Fewest renames + API calls, best fit for the season-inheritance model; more upfront grouping logic.

- [ ] 5.5. [model: opus] [effort: max] Tests: conftest `mock_tmdb` fixture + `rename_folder` crash-safety + season-inheritance resolver + media-image route + enrich local-first.
  - Files: `tests/conftest.py` (NEW `mock_tmdb` fixture — monkeypatches the TMDB HTTP calls / `requests.get` to return canned search+image JSON and fake image bytes; **binding-hazard-adjacent → opus**), `tests/test_rename_folder.py`, `tests/test_tmdb_resolve.py`, `tests/test_web_media_image.py`, `tests/test_enrich_metadata.py` (all new).
  - Details: Read `docs/testing-strategy.md` first; use `sandbox`/`sandbox_alias`/`sandbox_entry` + a `fake_dummy`-style approach for images. Tests: (a) **rename_folder** — moves the folder + rewrites every descendant `folder_path` (season_map + leaves), works when the files are dummies (archived), and a failure injected mid-operation is recoverable via the journal (mirror the rollback test patterns), AND it performs NO rehash; (b) **season inheritance** — `resolve_artwork_path` returns the entry's own image when present, else the season_map's, else walks up to the `{tmdb-…}` show folder; local-always-wins; (c) **media-image route** via TestClient — 200 with image when present, 404→gradient when absent, NO path traversal from a crafted id; (d) **enrich local-first** with `mock_tmdb` — writes `tmdb_id`, downloads `poster.jpg` only when absent (never overwrites a seeded local one), never fetches media, ambiguous match is reported not written. Constraints (MUST appear in EACH test step's Details): "Never touch real `C:\\Media` files or real `library_*.json`." and "Run `pytest -q` and fix failures before marking the step done." NEVER make a real TMDB network call (the `mock_tmdb` fixture is mandatory).
  - Acceptance: `pytest tests/test_rename_folder.py tests/test_tmdb_resolve.py tests/test_web_media_image.py tests/test_enrich_metadata.py -q` passes; `pytest -q` green; no network call escapes the mock.

- [ ] 5.6. [model: sonnet] [effort: medium] Extend the smoke suite for the new commands + assert the rollback contract is untouched.
  - Files: `tests/smoke/test_smoke_all_commands.py`.
  - Details: Add per-command smokes for `set_tmdb`, `rename_folder` (against `sandbox` + `sandbox_alias` — must not raise; must keep the alias library coherent), and `enrich_metadata` (with `mock_tmdb`, dry-run, no network, no media fetch). Add the new commands to the ALIAS SWEEP so a future entry-type change that breaks them fails here. Keep under ~30s. Constraints (MUST appear): "Never touch real `C:\\Media` files or real `library_*.json`." and "Run `pytest tests/smoke -q` and fix failures before marking the step done."
  - Acceptance: `pytest tests/smoke -q` passes and exercises the three new commands incl. the alias sweep.

- [x] 5.7. [model: opus] [effort: high] Wire posters + tmdb into `/api/items` + the SPA cards (real poster grid in Archived + other sub-views).
  - Files: `webui/static/app.js` (card poster slot points at `/api/media-image/{id}?kind=poster`; show title/year from `/api/items`), `webui/static/styles.css` (poster aspect-ratio + gradient fallback coherent with the P3 motion border), `main.py`/`webui/server.py` (set `poster_available`/`tmdb_id` truthfully in `items_payload` now that the route + field exist — flip the P1 placeholder fields to real values).
  - Details: Now that `metadata.tmdb_id` (5.1) and the media-image route (5.2) exist, make `items_payload` populate `tmdb_id` and `poster_available` truthfully (a cheap existence check or the precompute from 5.2-candidate-B), and have the SPA render the poster in each card (the user specifically wants posters in the Archived/fetchable tab; apply to all states for consistency). Gradient fallback when no image. Keep retina-crisp + coherent with the hover/progress borders.
  - Acceptance: Cards show real posters where available (esp. the Archived sub-view), gradient otherwise; titles/years render; `node --check` passes; no broken-image flashes.

- [ ] 5.8. [model: sonnet] [effort: medium] Optional NFO emission hook (IMP-U3 half) — write `movie.nfo`/`tvshow.nfo` next to media during enrich (flag-gated, additive, inert to scanners).
  - Files: `main.py` (NFO writer invoked by `cmd_enrich_metadata` behind a `--nfo` flag), README/ARCHITECTURE note.
  - Details: After a confident TMDB match, optionally write a Kodi/Jellyfin-compatible `movie.nfo` / `tvshow.nfo` (+ per-episode where natural) into `folder_path` carrying title/year/tmdb_id/synopsis/rating. **Additive + inert:** NFOs are non-video extensions, already ignored by `scan_unprepped`/walkers (extension-filtered) — confirm they are excluded from the reclaim/items walks (they are, by extension). Off by default (`--nfo` to enable) — this is the IMP-U3 down-payment; full per-episode/anime-id NFO + backfill stays U3 scope. A write failure warns and never blocks enrich.
  - Acceptance: `enrich_metadata <id> --apply --nfo` writes a valid `movie.nfo`/`tvshow.nfo`; no scanner picks it up as media; `pytest tests/smoke -q` stays green.

- [ ] 5.9. [model: sonnet] [effort: medium] Architect + docs + mark IMP-E3/U3 progress + IMP-D17 done; wire graph; update PRIORITY.
  - Files: `ARCHITECTURE.md` (§5 add `set_tmdb`/`rename_folder`/`enrich_metadata` to the command table + `/api/media-image`; §6 add the OPTIONAL `metadata.tmdb_id` field + the `{tmdb-…}` folder token convention + the season-inheritance resolution order; §6.4a/§12a note that `rename_folder` is journaled but does NOT change the rollback contract; §7.4 note hash-safety of a folder rename), `README.md`, `improvements/improvements_tierD.md` (add **IMP-D17** `rename_folder`, `Status: done`), `improvements/improvements_tierE.md` (IMP-E3: mark the local-first poster/tmdb_id/auto-enrich slice delivered, note the remaining synopsis/cast/AniDB/AniList breadth as still pending; IMP-E14 fully done), `improvements/improvements_tierU.md` (IMP-U3: note the NFO/artwork down-payment delivered, full backfill/anime-id NFO still pending), `improvements/PRIORITY.md` (rows + Last-updated + NEXT → suggested next starts below), `docs/priority-graph/priority-graph.html` (add `D17` node + edge `["D17","E3"]` (rename_folder enables the token stamp); set E14 done; mark E3/U3 partial — the `["E3","U3"]` edge already exists).
  - Details: Keep PRIORITY.md, tier files, and the graph in agreement (maintenance protocol). E3 and U3 are PARTIALLY delivered (local-first poster/tmdb_id/auto-enrich + NFO down-payment) — mark partial, not done, and say what remains.
  - Acceptance: ARCHITECTURE.md + README reflect all new commands/routes/fields; D17 done; E3/U3 partial with remaining scope noted; E14 done; all three tracking surfaces agree.

### Consumer Impact Analysis (Phase 5 — REQUIRED: adds `metadata.tmdb_id` + `rename_folder` rewrites `folder_path`)

Two shared data contracts change in Phase 5:
1. **NEW optional field `metadata.tmdb_id`** (additive). Risk = a consumer that assumes a fixed `metadata` shape. Additive optional fields are safe for `.get()`-style readers; the risk is only code that iterates/serializes `metadata` exhaustively or a guard test asserting the exact key set.
2. **`folder_path` REWRITTEN in bulk by `rename_folder`** across a show's season_map + all episode leaves (and the on-disk folder moved). Risk = any consumer that caches `folder_path`, holds a stale path across the rename, or a sidecar/remote pointer keyed on the old path.

Greps to run during implementation (enumerate EVERY hit with a verdict): `metadata\[` / `metadata\.get` / `\.get\("metadata"` ; `folder_path` (every read/write) ; `\.values\(\)`/`\.items\(\)` over `library`/`load_library` ; every `_resolve_alias` caller ; `ENTRY_TYPE_KEYS` + the guard test.

| # | Site (consumer of the changed shape) | Access | Verdict | Why / which step fixes it |
|---|--------------------------------------|--------|---------|----------------------------|
| 1 | `items_payload` (P1, new) reads `metadata.title/year/tmdb_id` | `entry.get("metadata",{}).get("tmdb_id")` | safe | designed to `.get()` the optional field (step 1.1/5.7); absence → null |
| 2 | `_library_summary` (server.py:279-300) | reads `status`/`type` only, not `metadata` | safe | does not touch `metadata` or rely on its shape |
| 3 | `collect_reclaimable` / `classify_entry_state` (main.py) | reads `status`,`folder_path`,`filename` | safe re tmdb; **safe re folder_path** | never reads `metadata.tmdb_id`; reads `folder_path` fresh from the (rewritten) library each call — no cache, so a post-rename scan sees the new path |
| 4 | `cmd_set_poster`/`cmd_set_fanart` (main.py:984-1037) | `entry["folder_path"]` | safe | reads `folder_path` live at call time; after `rename_folder` the new path is in the library |
| 5 | `cmd_check`/`cmd_restore`/`cmd_push` (use `folder_path`) | `entry["folder_path"]` live | safe | all read `folder_path` from the freshly-loaded library; hash is over file bytes not path (no rehash needed after a move) — Verified facts |
| 6 | `season_map` entry's own `folder_path` | rewritten by `rename_folder` | needs-handling | step 5.3 MUST rewrite the season_map's `folder_path` too (it = first episode's folder) — explicitly in scope |
| 7 | `multi_ep_alias` entries | have NO `folder_path` | safe | step 5.3 skips them (nothing to rewrite); they inherit via their primary |
| 8 | On-disk `uid` / `<short_id>.sha256` sidecars (ARCHITECTURE §6.5) | live inside `folder_path` | safe | they MOVE with the folder during the rename (no content change); never read by main.py at runtime |
| 9 | Remote `.mvmeta.json` sidecar (on the phone) carries `folder_path`/`remote_target_dir` (ARCHITECTURE §6.5) | stale after a local rename | safe (documented) | best-effort disaster-recovery record only; never read on the happy path; document that a local folder rename does not (and need not) update the already-uploaded remote sidecar |
| 10 | `tests/test_entry_schema_guard.py` + `ENTRY_TYPE_KEYS` | asserts entry-type key sets | needs-check | step 5.1: if the guard asserts on `metadata` sub-fields, update it; per main.py:115-119 it currently keys on TOP-LEVEL required keys only, so an optional nested field likely needs no change — CONFIRM by reading the test |
| 11 | The reclaim/items disk WALKS (PASS 1) match files by `folder_path+filename` normpath | post-rename, the walk re-reads the library | safe | the walk loads the library fresh and joins to the current `folder_path`; a moved folder is found at its new path (both disk + library moved together atomically) |

Every grep hit must be verdicted; if a grep surfaces a `folder_path`/`metadata` consumer not listed here, add it with a verdict before coding. No `needs-fix` crash-class consumer was found (the design — rewrite season_map + leaves atomically, skip aliases, no rehash — covers them); the two `needs-handling`/`needs-check` rows (#6, #10) are handled inside steps 5.3 / 5.1.

### Verification (Phase 5)
```
pytest tests/test_rename_folder.py tests/test_tmdb_resolve.py tests/test_web_media_image.py tests/test_enrich_metadata.py -q
pytest tests/test_entry_schema_guard.py -q
node --check webui/static/app.js          # (+ each module)
pytest -q
pytest tests/smoke -q                       # MANDATORY final gate (main.py + mvcommon.py + webui touched)
```

### PR to main (HUMAN-GATED — stop and ask)
Title: `feature: TMDB posters + crash-safe rename_folder + season inheritance — IMP-E3/U3/D17`. Create the PR, embed the verbatim Original task prompt, then STOP and ask before merging. **Note in the PR body:** rollback CHANGE-GATE — `rename_folder` is a NEW journaled operation that does NOT modify the existing journal format / PONR / `RollbackHardFail` contract (state this explicitly); if any reviewer finds it would, STOP and surface to the user.

### Manual test commands (Phase 5)
```
# 1. set TMDB key in mvconfig.json (tmdb.api_key) — user-provided
# 2. enrich a single movie (dry-run then apply):
python main.py enrich_metadata mov-en-2025-f1
python main.py enrich_metadata mov-en-2025-f1 --apply
#    -> writes metadata.tmdb_id, stamps {tmdb-…} on the folder, downloads poster.jpg (no media fetch)
# 3. rename a show folder (the "Dark" cascade) — works on archived dummies:
python main.py rename_folder tv-en-2017-dark "Dark {tmdb-70523}"
#    -> on-disk folder renamed + folder_path rewritten for the season_map + every episode leaf; no rehash
python main.py check tv-en-2017-dark-s01e01     # still verifies (no rehash needed after the move)
# 4. web posters + season inheritance:
python main.py web
#    Browser/iPad: open a category tab -> Archived (fetchable): cards show real posters;
#    a Dark season episode inherits the show poster; per-season art appears where TMDB has it.
# 5. recover sanity (crash-safety): interrupt a rename_folder, then:
python main.py recover --scan      # lists the leftover journal
python main.py recover <id-or-folder>
```

---

## Risks and edge cases

- **`/api/reclaim` must stay byte-identical** — the new `/api/items` is additive; a test snapshots `/api/reclaim` before/after Phase 1 (step 1.4d). If candidate C (1.1) refactors a shared classifier, the reclaim-unchanged test is the guard.
- **Whole-library iterators = the PR#21 crash class.** `items_payload`, `resolve_artwork_path`, and `rename_folder`'s descendant scan all iterate the library — each MUST skip/resolve `season_map`/`multi_ep_alias` before touching `folder_path`/`filename`. The smoke alias sweep + `sandbox_alias` tests are the enforcement.
- **Progress fidelity vs honesty.** The chunk-% must track REAL chunks-done (no fake linear timer); the SPA may TWEEN between ticks for smoothness, but the server must not lie. (Judge criterion in 2.2.)
- **Editing `mainfetch.py` is high-risk** (fetch fragility + smoke gate). Prefer parsing existing prints for progress (2.2 candidates A/B) over a hook (C); if C is chosen, the hook is a no-op by default so CLI behavior is byte-unchanged, and the manual fetch test is mandatory.
- **Token foot-guns.** Exposing over Tailscale without a token leaves destructive replace + fetch_restore reachable on the tailnet. Phase 4 warns loudly at startup when bound non-localhost without a token (Open Decision: warn vs hard-refuse). The token must never appear in logs or captured job output.
- **Path traversal in the media-image route.** The route derives the file path from the library entry, never from raw client input; only `poster.jpg`/`fanart.jpg` (+ season variants) under a known `folder_path` are served (5.2 security criterion + test 5.5c).
- **rename_folder × rollback change-gate.** It must be crash-safe via the EXISTING journal without changing the existing rollback contract. If a design would alter the journal format / PONR / `RollbackHardFail`, STOP and surface to the user (CLAUDE.md auto-rollback gate). Archived dummies rename like any folder (no special-casing); the remote `.mvmeta.json` is intentionally NOT updated (documented).
- **TMDB matching is imperfect for anime** (TMDB tv ≠ AniDB absolute numbering). Scope here is local-first poster/tmdb_id for the easy cases; ambiguous → list-not-guess; AniDB/AniList breadth stays future IMP-E3. Never overwrite a user's local `poster.jpg`.
- **No-build constraint.** Splitting `app.js` into ES modules is allowed (`<script type="module">`) but there is NO bundler — every module must pass `node --check` individually and load over plain static serving.
- **iOS standalone quirks.** Only `display:standalone` is supported by iOS Safari; safe-area insets matter in home-screen mode; hover effects must degrade to tap/focus on touch.
- **mvconfig binding hazard.** Read config values at runtime via getters (not module-level constants bound at import) to avoid the dual-binding patch hazard; if any value is bound at import, the sandbox dual-patch pattern applies.

---

## Cross-cutting constraints

- **Stay vanilla / no-build.** No framework, no bundler, no CDNs/external fonts/assets (explicit in styles.css). Keep `node --check webui/static/app.js` (and each new module) green and the smoke suite green. ES-module splitting is OK; a build step is NOT.
- **Honor the `ENTRY_TYPE_KEYS` guard.** Every new whole-library iterator skips `season_map`/`multi_ep_alias` or resolves via `_resolve_alias` before touching physical-only keys. If a shared entry field changes (the only candidate is the OPTIONAL `metadata.tmdb_id`), update `ENTRY_TYPE_KEYS` + keep `tests/test_entry_schema_guard.py` green (likely no change — confirm).
- **Honor the auto-rollback CHANGE-GATE.** `rename_folder` must be crash-safe but MUST NOT alter existing rollback behavior (journal format/durability, PONR locations, `mark_point_of_no_return`, `recover_journal` semantics, `RollbackHardFail` contract). It ADDS a new journaled op. If any design would change existing behavior — STOP and surface to the user. `fetch_restore` in the UI reuses `cmd_fetch_restore` UNCHANGED (gate not tripped).
- **Security.** The shared token is mandatory-in-spirit when bound non-localhost; destructive `replace` + `fetch_restore` are exposed remotely in Phase 4 — protect them. No token/secret in logs or job output. Media-image route is path-traversal-safe.
- **Read-only data layers stay read-only.** `items_payload`, `resolve_artwork_path`, and all `/api/*` GETs never mutate the library or touch media. Mutations go only through the existing `cmd_*` (allow-list) and the new journaled `rename_folder`/`enrich_metadata`.
- **Tracking-surface coherence.** Every phase updates PRIORITY.md (+ Last-updated + NEXT) AND the tier file AND `priority-graph.html` together (maintenance protocol, PRIORITY.md lines 89-98), plus an architect doc step (ARCHITECTURE.md/README) for the documented behavior change.
- **Tests + constraints in every test step.** Pick fixtures per `docs/testing-strategy.md`; conftest changes → opus (binding hazard); each test step's Details carries "Never touch real `C:\\Media` files or real `library_*.json`." and "Run `pytest -q` (or `pytest tests/smoke -q`) and fix failures before marking the step done." Never make a real TMDB/network call (use `mock_tmdb`); never open Selenium / a real device in a test.

---

## Verification (whole feature, run at the end of each phase as applicable)
```
pytest -q                                   # full suite green
pytest tests/test_entry_schema_guard.py -q  # schema guard (esp. after Phase 5)
node --check webui/static/app.js            # + node --check each new ES module file
python -c "import main; main.items_payload()"   # data layer import-safe (after P1)
pytest tests/smoke -q                         # MANDATORY FINAL GATE — every code phase touches
                                              # main.py / mvcommon.py / webui; this is the last gate
```
> SMOKE-GATE: Phases 1, 2, 4, 5 touch `main.py`/`mvcommon.py`/`webui`, and Phase 3 touches `webui` (+ maybe `server.py`); therefore `pytest tests/smoke -q` is the FINAL verification line of every code-touching phase, in addition to `pytest -q`. Phase 0 (CI) and the doc-only sub-steps are the only ones whose own change doesn't touch those modules — but P0's whole point is to RUN these gates in CI.

---

## Open Decisions (genuinely-residual — recommended default in **bold**; resolve at the relevant phase, do NOT block planning)

1. **TMDB API key — source + storage.** The free TMDB key is **user-provided**. Store it in **`mvconfig.json` `tmdb.api_key` (Phase 4 minimal config)** rather than an env var (consistent with the other tunables, and `mvconfig.json` is gitignored). *Default: mvconfig.json; env-var override acceptable as a fallback.* (User action/prereq: obtain a free TMDB API key before Phase 5.)
2. **Web token storage + rotation.** Store the shared token in **`mvconfig.json` `web.token`**; rotate by editing the file + restarting the app (`tailscale serve` is unaffected). *Default: static token in config, manual rotation; a `config set web.token <new>` helper is a nice-to-have, not required.*
3. **Expose the advanced plain `fetch` (download-only) button, or only "Fetch & Restore"?** *Default: ship **Fetch & Restore only** as the primary, user-facing action (matches the user's described flow); add a secondary "fetch (download only)" affordance ONLY if requested — it does NOT auto-flip the card.* (Wiring the `fetch` action in `ACTION_TABLE` is cheap; surfacing it in the UI is the decision.)
4. **Require the token on READ endpoints (`/api/items`,`/api/reclaim`,`/api/library`,`/api/job`)?** *Default: **YES** (`web.require_token_for_reads: true`) — the whole point of Phase 4 is remote exposure, and a tailnet peer shouldn't read the library/poster/job data without the token. Localhost stays exempt so the local browser is frictionless.* (Flip to false if the user wants frictionless read-only sharing on the tailnet.)
5. **No-token + non-localhost bind: warn vs hard-refuse?** *Default: **warn loudly at startup** (don't hard-refuse) to avoid a foot-gun that blocks a legitimate localhost-only run that happened to set a non-localhost host; document the risk. Note: with `tailscale serve` the app still binds 127.0.0.1, so this mainly guards a manual `--host 0.0.0.0`.*
6. **Ambiguous-TMDB-match confirmation UX.** *Default: **CLI list-then-confirm** during `enrich_metadata` (print candidates; `--apply` writes only confident matches; ambiguous ones reported for a follow-up `set_tmdb`). A web confirmation UI is out of scope now — the matching happens in the CLI backfill, not the SPA.*
7. **Does `rename_folder` rewrite the `season_map`'s `folder_path` or only leaves?** *Default: **rewrite BOTH** the season_map's `folder_path` (it equals the first episode's folder) AND every leaf under the folder — anything else leaves the season_map pointing at a stale path. (`multi_ep_alias` has no `folder_path` → skipped.)* (Captured as needs-handling row #6 in the Consumer Impact Analysis.)
8. **Progress unit for a season_map `fetch_restore` (chunks vs episodes).** *Default: **total chunks across all selected children** (finest-grained, smoothest border); fall back to episode-count if chunk counts are unavailable. Document whichever ships so the SPA renders it consistently.*
9. **Retire the `/api/reclaim`-driven SPA view, or keep both?** *Default: drive the SPA rendering from **`/api/items`** (richer) and KEEP the `/api/reclaim` ENDPOINT for back-compat (locked decision #1) — but do not maintain a second UI surface for it.*

---

## Suggested next starts (what this upgrade unlocks)

- **IMP-S1 — Jellyfin stand-up** (zero-code, immediate couch value; can run anytime in parallel) — the measured baseline every Tier-S task builds on.
- **IMP-S2 — `mvdaemon`** — the serialized web worker built here (+ fetch_restore + progress + `/api/items`) IS the daemon's seed; promoting it to an always-on Windows service is the natural next big step (this feature added the edge `E14→S2`).
- **Full IMP-E3 / IMP-U3 enrichment + backfill** — extend the local-first TMDB slice with synopsis/cast/AniDB/AniList breadth + the full NFO/artwork backfill over all ~570 entries.
- **IMP-A2 → A4 → A5 (argparse → --json → full config)** — this feature delivered a minimal config slice; the full chain unblocks the daemon's machine-readable contract and finishes config portability.
- **IMP-F10 — WebSocket/SSE status** — the deliberate future smoothness upgrade over today's polling (becomes the daemon event bus).
- **IMP-X1 — multi-account replication** — the real backup against the CSAM-ban single-point-of-failure (Band 3); orthogonal to the UI but the highest-value resilience work.

---

## Execution model

Runs via the repo's multi-agent pipeline: the **main (top-level) session acts as orchestrator** following `.claude/agents/orchestrator.md` as a playbook (do NOT launch the `orchestrator` agent via `Task` — depth-limit footgun, CLAUDE.md). It reads this `PLAN.md`, spawns **depth-1** executor / candidate / judge / git sub-agents itself, commits between steps, and pauses at the two human gates. **Candidate-step executors may run in PARALLEL** (the user explicitly allowed it; no cap on candidates). Multi-candidate steps in this plan: **1.1** (api/items shape, 3), **1.3** (tab/sub-view UX, 2), **2.2** (progress-flush mechanism, 3), **2.3** (progress border + auto-flip, 2), **3.1** (visual design, 4), **5.2** (media-image + inheritance, 2), **5.4** (TMDB enrich, 3). Parallelizable across-phase authoring: Phase 0 (CI) can be authored alongside Phase 1; within a phase the candidate executors for a given multi-candidate step run in parallel, then the **judge writes a Decision Card (ranked results + recommendation — it does NOT auto-pick) and the USER chooses** which candidate to merge (the candidate-selection gate); for live-A/B UI steps all candidates are kept running on distinct URLs for hands-on comparison. See **§Multi-candidate decision protocol** below. **Human gates (STOP and ask):** (0) **candidate selection** — for every multi-candidate step the user picks the candidate from the judge's Decision Card before anything merges (§Multi-candidate decision protocol); (1) merge each phase's PR into `main`; (2) archive each merged branch (annotated `archive/<branch>` tag, then delete) — both per `docs/git-pr-conventions.md`.

## Multi-candidate decision protocol (decision cards + live A/B for UI) — USER CHOOSES

For EVERY multi-candidate step the judge does **NOT** auto-select the winner. After the candidate executors finish and the judge reviews each candidate's code + self-critique + test results, the judge emits a **Decision Card** and execution PAUSES at the **candidate-selection human gate (gate 0)** — the user reads the card and picks which candidate to merge. **This deliberately overrides the repo's default judge-auto-pick behavior for this plan;** the git-agent then squash-merges the USER-chosen candidate's worktree and archives the rest. The user may pick the judge's #1, a different candidate, or "merge X but graft Y's <aspect>".

### Decision Card format (one per multi-candidate step)
The judge writes `docs/feature-web-media-ui/decisions/<step-id>-DECISION.md` AND surfaces it to the user, containing:
1. **Step** — id, title, N candidates, and the step's judge criteria (verbatim from the plan).
2. **Per candidate (A/B/C/D)** — approach in 1–2 lines; files touched + rough diff size; **test results** (which tests ran, pass/fail, a summary of `pytest` / `node --check` / smoke output); self-critique highlights; pros/cons mapped to each judge criterion; risks.
3. **Comparison table** — judge criteria as rows × candidates as columns, scored (✓ / △ / ✗ or 1–5) so the trade-offs are visible at a glance.
4. **Judge recommendation** — a ranked order (1st / 2nd / …), the rationale, and any "graft the best of X into the winner" notes (e.g. "take B's reduced-motion handling into A").
5. **For UI steps — the live preview URLs** (see below) + a short "what to look for / how to compare" checklist (the exact card/interaction to exercise on each URL).
6. **👉 Your choice** — an explicit prompt. **Nothing merges until the user answers.**

### Live A/B for UI steps — keep all candidate URLs up
For UI multi-candidate steps whose result is visually testable, the orchestrator keeps EVERY candidate running **simultaneously** so the user can flip between them and choose:
- Each candidate already lives in its own git worktree (the pipeline creates these for candidate steps), so each holds a complete, runnable app.
- The orchestrator launches each candidate's web app on a **distinct port** from its worktree — candidate A `python main.py web --port 8765 --no-browser`, B `--port 8766`, C `8767`, D `8768`.
- The Decision Card lists every URL: locally `http://127.0.0.1:<port>`, on the same Wi-Fi `http://<alienware-LAN-ip>:<port>`, and for remote A/B the user can `tailscale serve --bg --https=<port> 127.0.0.1:<port>` per candidate (distinct HTTPS ports) → each reachable as `https://<machine>.<tailnet>.ts.net:<port>` on the iPad/iPhone (home or away).
- The user compares them live (e.g. hover the same card across all four design candidates, or run Fetch & Restore on `<archived-id>` to watch each progress-border style), then picks. After the choice, the orchestrator stops the candidate servers, the git-agent merges the chosen worktree, and the others are archived.

**Which multi-candidate steps are live-A/B vs decision-card-only:**
- **Live A/B (keep URLs up):** **1.3** (tab / sub-view UX, 2) · **2.3** (progress border + auto-flip, 2) · **3.1** (visual design, 4 — the most important live comparison) · **5.2** (media-image + season inheritance — poster rendering, 2).
- **Decision-card-only (backend/CLI — judged by tests + criteria, no meaningful URL):** **1.1** (`/api/items` shape, 3 — card includes sample API payloads) · **2.2** (progress-flush mechanism, 3 — card includes sample poll sequences) · **5.4** (TMDB enrich, 3 — card includes sample CLI output). The card still carries full per-candidate results + the judge's recommendation; the user chooses from the card.
- (2.3 and 5.2 need a seeded library / a triggered fetch to show the visual — the card spells out the exact manual step to trigger it on each candidate URL.)

This gate is in ADDITION to the merge-to-main and archive-branch gates. Per multi-candidate step the order is: candidates run in parallel → judge writes the Decision Card (+ launches the live URLs for UI steps) → **USER chooses** (gate 0) → git-agent merges the chosen candidate → step continues.

---

## Out of scope

- The full IMP-S2 daemon as an always-on service (only its UI-layer seed + fetch-in-UI down-payment ship here); the in-client Jellyfin notify/grace flows (S3–S5).
- WebSocket/SSE (IMP-F10) — polling is the transport; F10 is the future upgrade.
- The full IMP-A2/A4 (argparse / `--json`) and the full IMP-A5 config migration of ALL constants — only a minimal host/port/token/TMDB-key slice ships (Phase 4).
- The complete IMP-E3 metadata breadth (synopsis/cast/ratings, AniDB/AniList) and the full IMP-U3 NFO/artwork backfill over all ~570 entries — only the local-first poster/tmdb_id slice + an optional NFO down-payment ship.
- Changing the fetch engine (mainfetch Selenium → gphotosdl/CDP, IMP-G2/S7) — we only ADD a progress signal, ideally by parsing existing prints, without altering fetch mechanics.
- ANY change to the auto-rollback contract (journal format/PONR/`RollbackHardFail`) — `rename_folder` is additive-journaled only; a change would require an explicit user decision (change-gate).
- `tailscale funnel` / public-internet exposure — we use `tailscale serve` (tailnet-only) by deliberate choice.
- The "Others" tab's category-specific behaviors beyond basic filtering (the user said "will come back to it later").
