# Task: `web` command — local FastAPI operations/console UI (Disk Reclaim view + suggested next-commands + integrated sort/replace) — IMP-E12

Suggested branch: `feature/web_console`
(`<type>/<short_name>` per `docs/git-pr-conventions.md`: lowercase, under 50 chars.)

## Context
MediaVault is CLI-only today. `python main.py scan_unprepped` (`main.py:2579`) walks `C:\Media\{Movies,Series,Anime}` and lists video files not in any library; `sort` and `replace` are separate commands. The user wants a single futuristic-but-functional local web UI that (1) merges "unprepped" with "prepped/pushed-but-still-local-and-not-archived" into one disk-reclaim view, (2) suggests the exact next command + a Plex/Jellyfin/Emby-correct target folder per item, (3) integrates `sort`, and (4) offers a one-click `replace` for already-watched, already-uploaded local files. This implements **IMP-E12** (`web` command — `improvements/improvements_tierE.md:243`), introduces a NEW **IMP-D16** scan (`scan_reclaimable`), and advances a thin slice of **IMP-D1** (total reclaimable GB). The FastAPI backend is deliberately the seed of the Tier-S daemon (IMP-S2, `improvements/improvements_tierS.md:40`), not throwaway.

## Goal
Concrete, testable definition of done:
1. `python main.py web` starts a FastAPI+uvicorn server bound to `127.0.0.1:8765` (port overridable via `web --port N`, host via `web --host H`), prints the URL, and opens the default browser. `Ctrl-C` shuts it down cleanly.
2. The page shows ONE merged "Disk Reclaim" list, each item carrying a **state badge** (`UNPREPPED` / `LOCAL·NOT-PUSHED` / `PUSHED·NOT-ARCHIVED` / `RESTORED·REPLACE-AGAIN`), filter chips per state, and a header total of reclaimable GB.
3. Each row shows a deterministic **suggested next command** string and a **suggested target folder** (current MediaVault layout + an editable provider-id placeholder tag). The UI never moves/renames files; move is copy-a-command only.
4. Action buttons run the EXISTING `cmd_*` functions unchanged: `prep`, `push`, `replace`, `sort`, `prep_push_rep`. Every destructive action (`replace`) requires an in-UI confirm modal. Long actions report progress via a polled job mechanism.
5. New pure data-functions (`collect_reclaimable`, `classify_entry_state`, `suggest_next_command`, `suggest_target_folder`, `guess_manual_id`) return plain dicts, are unit-tested, and are `multi_ep_alias`/`season_map`-safe.
6. New `web` command + `collect_reclaimable` are covered in `tests/smoke` (per-command + alias sweep). `pytest -q` and `pytest tests/smoke -q` are green; the smoke suite stays < 30 s.
7. No new library entry type and no shared-field change → `ENTRY_TYPE_KEYS` and the guard test are untouched; the auto-rollback change-gate is NOT tripped (replace reuses `cmd_replace` verbatim).

## Files affected
- `main.py` — add `cmd_web(host, port, open_browser)`; add the `elif cmd == "web":` dispatch arm + usage line; add the pure data-functions (`collect_reclaimable`, `classify_entry_state`, `suggest_next_command`, `suggest_target_folder`, `guess_manual_id`, `_provider_tag_for`). Reason: data layer + entry point live with the other `cmd_*` and the library helpers.
- `mvcommon.py` — only if a helper is genuinely shared with `mainfetch` (not expected here); keep `VIDEO_EXTENSIONS`/`LOCAL_ROOT` reuse via import. Reason: avoid needless surface; the data-functions read library state already loaded by `main.load_library`.
- `webui/__init__.py` (new) — package marker. Reason: keeps the web app out of `main.py`'s 3000+ lines and gives the Tier-S daemon a home to grow into.
- `webui/server.py` (new) — FastAPI app factory `create_app()`, all routes, the in-process job registry, the SSE-or-poll progress endpoint. Reason: the daemon (IMP-S2) becomes this module grown up.
- `webui/static/index.html`, `webui/static/app.js`, `webui/static/styles.css` (new) — single-page frontend (no build step). Reason: "static HTML/CSS/JS frontend" decision; servable by FastAPI `StaticFiles`.
- `requirements.txt` — add `fastapi` and `uvicorn[standard]` (coordinate with IMP-A10). Reason: new runtime deps.
- `tests/test_web_datafns.py` (new) — unit tests for the five pure data-functions. Reason: deterministic, browser-free.
- `tests/test_web_endpoints.py` (new) — FastAPI `TestClient` endpoint tests. Reason: exercise routes without a live browser/uvicorn.
- `tests/smoke/test_smoke_all_commands.py` — add a `web`/`collect_reclaimable` smoke + an alias-sweep entry. Reason: cross-command gate (the check that would have caught PR #21).
- `README.md` and `ARCHITECTURE.md` — architect documents the new `web` command + data-functions (documented behavior change). Reason: required by the task.
- `improvements/improvements_tierE.md`, `improvements/improvements_tierD.md`, `improvements/improvements_tierA.md`, `improvements/PRIORITY.md`, `docs/priority-graph/priority-graph.html` — IMP bookkeeping (E12 done, D16 added, A10 done, D1 advanced). Reason: maintenance protocol.
- `docs/feature-web-console/` — `PLAN.md` (tracked copy of this), `DECISIONS.md`, completion report. Reason: PLAN.md location convention.

## Approach
The change is layered so each layer is independently testable and the web tier is a thin caller of pure functions.

1. **Data layer (pure, in `main.py`).** `collect_reclaimable()` loads the merged library once, walks the same three category roots `scan_unprepped` uses, and produces a unified list of item dicts. For each on-disk video file it determines whether the path is known in the library; for each known leaf it reads `(status, uploaded, on-disk dummy/real)`. `classify_entry_state()` maps that tuple to one of the four badges. UNPREPPED items (on disk, not in library) additionally get a `guess_manual_id()` proposal. Every item gets `suggest_next_command()` (a single exact command string) and `suggest_target_folder()` (current-layout path + editable provider-id tag). All of these skip/de-alias `season_map` and `multi_ep_alias` entries.

2. **Web tier (`webui/server.py`).** `create_app()` returns a FastAPI app. `GET /api/reclaim` returns `collect_reclaimable()` as JSON (with the GB total). `GET /api/library` returns a slim library summary. `POST /api/action/{name}` validates the action name against a fixed allow-list (`prep`, `push`, `replace`, `sort`, `prep_push_rep`), spawns the corresponding `cmd_*` in a background thread, registers a job id, and returns it immediately. `GET /api/job/{id}` returns the job's status/captured-output for polling. `replace` requires a JSON body flag `confirm=true` or the endpoint returns 409 (the frontend's confirm modal sets it). The app is created with `StaticFiles` mounted at `/` serving the SPA.

3. **Entry point.** `cmd_web(host, port, open_browser)` builds the app, prints `http://{host}:{port}`, opens the browser (best-effort `webbrowser.open`, skippable), and runs `uvicorn.run(app, host=host, port=port)`. The dispatch parses `web [--port N] [--host H] [--no-browser]`.

4. **Frontend.** `index.html`+`app.js`+`styles.css`: fetch `/api/reclaim`, render rows with badge chips and per-state filters, show the suggested command (copy button) and suggested folder (copy button, editable provider-id field), and wire action buttons to `POST /api/action/...` then poll `/api/job/...`. A confirm modal gates `replace`.

5. **Tests + smoke wiring + docs**, then IMP bookkeeping.

The state classifier is the heart of the merge: it is what distinguishes "unprepped" (scenario 1) from "prepped-but-still-local-occupying-space" (scenario 2) and surfaces the watched-then-replace case (scenario "RESTORED·REPLACE-AGAIN").

## State-classification table (badge ↔ state tuple ↔ suggested next command)

Tuple = `(in_library?, status, uploaded, on-disk file is real(>=DUMMY_MAX_BYTES) / dummy / absent)`. `id` below = the library id when in-library, else the `guess_manual_id()` proposal.

| Badge | in_library? | status | uploaded | on-disk | Meaning (reclaim story) | Suggested next command |
|-------|-------------|--------|----------|---------|-------------------------|------------------------|
| `UNPREPPED` | no | — | — | real file present | New file on disk, not tracked. Scenario 1. | `python main.py prep <guess_id> "<path>"` (or `prep_season` for a multi-file folder) |
| `LOCAL·NOT-PUSHED` | yes | `local_ready` | `False` | real | Prepped but not uploaded; full size on disk. Scenario 2a. | `python main.py push <id> SIZE_GB 8` then `replace <id>` (or `prep_push_rep <id> "<path>"`) |
| `PUSHED·NOT-ARCHIVED` | yes | `onboarded` | `True` | real | Uploaded to cloud, original STILL on disk = reclaimable. Scenario 2b (the headline case). | `python main.py replace <id>` (verifies upload, then dummy-swaps) |
| `RESTORED·REPLACE-AGAIN` | yes | `restored_local` | `True` | real | Unarchived, watched, now reclaimable again. The "already pushed, just watched, replace again" button. | `python main.py replace <id>` |
| (not shown) `ARCHIVED` | yes | `archived` | `True` | dummy | Already reclaimed — excluded from the list (not reclaimable). | — |

Notes baked into the implementation:
- "real vs dummy" uses the existing `DUMMY_MAX_BYTES` threshold (main.py) the same way `cmd_check`/`cmd_repair_dummies` do (file size < threshold ⇒ dummy; real ⇒ size >= threshold, matching the existing code).
- An `onboarded`/`restored_local`/`local_ready` entry whose on-disk file is already a DUMMY (size < threshold) is NOT reclaimable (no space to free) and is excluded, even though status says local — disk is the source of truth for "occupies space".
- `season_map` and `multi_ep_alias` entries are skipped entirely (they own no reclaimable file).
- The suggested `push` split size is **`SIZE_GB 8`** — the locked standard from `BEST_PRACTICES.md` §A1 (8 GiB ceiling ≈ 8.59 GB decimal, ~14% margin under Google's 10 GB cap; `SIZE_MB 9900` ≈ 10.38 GB would EXCEED the cap → corrupt/blocked upload). Files under the ceiling push whole automatically (`cmd_push`/`_will_split` never split a file smaller than the target, `main.py:347`), so `push <id> SIZE_GB 8` is a safe universal suggestion for any LOCAL·NOT-PUSHED item.

## Worked-example items (concrete, from the project's live conventions)

| On disk / library id | Badge | Suggested folder (current layout + provider tag) | Suggested next command |
|----------------------|-------|--------------------------------------------------|------------------------|
| `C:\Media\Movies\English\…\Dark.River.2024.mkv` (not in library) | `UNPREPPED` | `C:\Media\Movies\English\<Genre>\Dark River (2024) {tmdb-_______}\` (provider-id editable) | `python main.py prep mov-en-2024-darkriver "C:\Media\Movies\English\…\Dark.River.2024.mkv"` |
| `tv-en-2017-dark-s01e01` (status `local_ready`, real file) | `LOCAL·NOT-PUSHED` | `C:\Media\Series\…\Dark (2017) [tvdbid-334824]\Season 01\` (existing folder kept as-is, `applies=False`; NEW items would use curly `{tvdb-…}`) | `python main.py push tv-en-2017-dark-s01e01 SIZE_GB 8` |
| `mov-en-2025-f1` (status `onboarded`, uploaded `True`, real 76 GB file) | `PUSHED·NOT-ARCHIVED` | (existing folder — suggestion is informational for NEW items only) | `python main.py replace mov-en-2025-f1` |
| `ani-ja-2006-deathnote07` (status `restored_local`, watched) | `RESTORED·REPLACE-AGAIN` | (existing folder) | `python main.py replace ani-ja-2006-deathnote07` |

## Provider-tag template (per media type — curly-brace form, the officially cross-compatible Plex/Jellyfin/Emby syntax)

`suggest_target_folder` appends an EDITABLE provider-id placeholder to the leaf folder name (NEW items only; existing folders are NEVER renamed). Per the user's decision, NEW suggestions use the **curly-brace** form — all three servers officially support `{provider-id}`, the guaranteed-compatible syntax:
- Movies: `<Title> (<Year>) {tmdb-0000000}` (TMDB id).
- TV series: `<Title> (<Year>) {tvdb-000000}` — episodes under `Season NN/`.
- Anime: treated as series for foldering — `<Title> (<Year>) {tvdb-000000}` (AniDB/TMDB enrichment deferred to IMP-E3/D10).
The id inside the braces is an EDITABLE placeholder the user fills/confirms in the UI; this task does NO TMDB/TVDB lookup (deferred to IMP-D10/E3). NOTE: the user's pre-existing folders use the older square-bracket form (e.g. `Dark (2017) [tvdbid-334824]`) and are intentionally left untouched — only NEW suggestions use the curly form.

> **⚠️ SUPERSEDED by IMP-U6 (2026-09-07, D2):** suggestions now use the square
> `[tmdbid-0000000]` placeholder for EVERY category (movies, series AND anime) — TMDB-only,
> matching what the enricher actually stamps. The curly `{tvdb-000000}` series suggestion above
> is history; the note about the user's pre-existing square-bracket folders proved prescient —
> IMP-U6 migrated the whole library back to that shape (with the `tmdbid-` keyword).

## Steps

> **Multi-candidate steps (revised on user request — explore competing approaches; usage de-prioritized).** Steps **1, 3, and 6** run as multi-candidate bake-offs (each: N isolated worktrees → judge picks the winner, which is merged before dependent steps run). These are the three genuinely fork-worthy decisions: the reclaim-scan **data model** (step 1 — greenfield, and everything downstream depends on its shape), the **action-execution / concurrency model** behind partly-destructive actions (step 3 — safety-critical), and the **"futuristic" UI's information architecture** (step 6 — the user's headline ask and an inherently subjective design space). Each multi-candidate step PINS its output / HTTP / behavior **contract** (identical across candidates) so the bake-off never ripples into dependent steps; candidates differ ONLY on internal strategy. All other steps (2, 4, 5, 7, 8, 9 — tests, dispatch arm, requirements, docs) follow fixed patterns and stay single-executor.
>
> **Checkpoint C3 — judge gate (HUMAN; this plan).** For EVERY multi-candidate step (1, 3, 6), after the candidates run and the judge produces its DECISION, the orchestrator must NOT auto-merge or auto-commit the winner. It STOPS and presents to the user: the judge's chosen candidate, the full rationale, the per-candidate analysis/scores against that step's Judge criteria, and the orchestrator's OWN recommendation. The user then either accepts the judge's pick OR selects a different candidate. ONLY after that explicit choice does git-agent merge the SELECTED candidate, commit the step, and the pipeline proceed to the next step. Single-executor steps (2/4/5/7/8/9) keep normal commit-between-steps.

- [x] 1. [model: opus] [effort: max] [candidates: 3] Add the pure data-functions to `main.py` (the heart of the merge).
  - Files: `main.py`
  - Details: Add five module-level pure functions near the other `cmd_*`/library helpers (NOT inside `__main__`):
    - `classify_entry_state(entry, on_disk_real)` → one of `"UNPREPPED"|"LOCAL_NOT_PUSHED"|"PUSHED_NOT_ARCHIVED"|"RESTORED_REPLACE_AGAIN"|"ARCHIVED"|None` per the State-classification table. `entry=None` ⇒ `UNPREPPED`. Skip (`return None`) when `entry.get("type") in ("season_map","multi_ep_alias")`. Treat `on_disk_real=False` (dummy/absent) as not-reclaimable for in-library entries (`ARCHIVED` or `None`).
    - `guess_manual_id(path)` → a best-effort editable id from the file/parent-folder name per the canonical shapes documented in ARCHITECTURE.md §6.2 (`mov-<lang2>-<year>-<slug>`, `tv-…-sNNeMM`, `ani-…<EE>`); lowercase ascii slug, strip release noise; default lang `en`; year via the same first-4-digit rule as `parse_metadata_from_id` (`main.py:183`). Category (mov/tv/ani) inferred from which root the path is under. Never raise — return a plausible string the user edits.
    - `suggest_target_folder(item)` → dict `{folder, provider_tag, editable_provider_field}` using the current layout (`Movies/<Language>/<Genre>/<Title>/…`, `Series/…`, `Anime/…`) + the provider-tag template above. New-items-only; for in-library items return the existing `folder_path` and mark `applies=False` (informational).
    - `suggest_next_command(item)` → the exact command string from the State-classification table.
    - `collect_reclaimable()` → load library once via `load_library()`; walk the same three category roots as `cmd_scan_unprepped` (reuse its exclusion set: `SPLIT_DIR_NAME`, `CHECKSUM_DIR_NAME`, `RESTORE_DIR_NAME`, `.git`, `.idea`, `__pycache__`, `Utils`; skip `.temp_dummy` and `.chunk.`); build `known_paths` (normpath-lower of `folder_path/filename`) ONLY from physical leaves (skip non-physical types — mirror `main.py:2606`); for each on-disk video decide in-library via `known_paths`, compute `on_disk_real = size >= DUMMY_MAX_BYTES` (matching `cmd_check`/`cmd_repair_dummies`), classify, and (for non-None badges) append `{id, badge, path, size_bytes, suggested_command, suggested_folder, guessed (bool)}`. Also iterate library physical leaves whose status is reclaimable to catch entries whose file is real (covers PUSHED·NOT-ARCHIVED / RESTORED items). Return `{"items": [...], "total_reclaimable_bytes": N, "total_reclaimable_human": "..."}`. MUST de-dupe by normpath so a library leaf and its on-disk file are one row. Use `human_readable_size` for the human total.
  - Constraint: every `.values()`/`.items()` iteration over the library MUST skip non-physical types (`entry.get("type") in ("season_map","multi_ep_alias")`) — or resolve them via `_resolve_alias(lib, mid)` (2-arg, returns `(real_id, entry)`) — before touching `folder_path`/`filename` (IMP-C12 lesson; ARCHITECTURE.md §6.3). No new entry type, so do NOT touch `ENTRY_TYPE_KEYS`.
  - Acceptance: `python -c "import main; import json; print(json.dumps(main.collect_reclaimable(), default=str)[:200])"` runs without error against a sandbox library; functions importable; no library mutation. All three candidates MUST produce identical `collect_reclaimable()` output on the step-2 fixture set (judged for output-equivalence, not merely non-crash).
  - Judge criteria (most important first): (1) correctness — identical, correct badges + de-dup + GB total on the seeded fixture, including the `season_map`/`multi_ep_alias`/ARCHIVED exclusions; (2) alias/`season_map` crash-safety of the whole-library walk (the PR#21 class); (3) performance on a large library (avoids needless double-walking / re-statting); (4) readability + how cleanly the fixed return contract is honored.
  - Candidate approaches (the Details above are the REQUIRED output contract; candidates differ ONLY in the scan/index/de-dup strategy that produces it):
    - A: **Disk-first** — the recipe spelled out in Details: `os.walk` the three roots, cross-reference each video against an in-memory `known_paths` index built from the library, classify; then a targeted library pass adds PUSHED·NOT-ARCHIVED / RESTORED leaves whose on-disk file is real.
    - B: **Library-first** — iterate library physical leaves and `os.stat` each path to classify real/dummy/absent; then a single `os.walk` collects only on-disk files NOT in the library as UNPREPPED. (Different I/O profile; stats known paths directly instead of walking-then-matching.)
    - C: **Unified normpath index** — build ONE `dict[normpath_lower → record]` seeded from BOTH the disk walk and the library iteration, merge per key, then classify each unified record exactly once (single de-dup source of truth; structurally hardest to double-count).
  - ⛔ Checkpoint C3 (human judge gate): after the judge decides, the orchestrator presents its decision + per-candidate analysis + recommendation and STOPS — no auto-merge/commit; proceed only with the user-selected candidate (see the Multi-candidate note under `## Steps`).

- [x] 2. [model: sonnet] [effort: medium] Unit tests for the pure data-functions.
  - Files: `tests/test_web_datafns.py`, `tests/conftest.py` (promote `make_video` here), `tests/smoke/conftest.py` (delete the moved fixture)
  - Details: Use the `sandbox` fixture (it dual-patches `mvcommon.LIBRARY_*` AND `main.LIBRARY_*` plus `LOCAL_ROOT`; do NOT DIY) and `make_video` (writes >`DUMMY_MAX_BYTES`). **Fixture prerequisite (do FIRST):** `make_video` currently lives in `tests/smoke/conftest.py`, which is NOT visible to top-level `tests/`; move its definition up to `tests/conftest.py` (beside `sandbox`/`mock_device`/`fake_dummy`) and delete the smoke copy — a parent-conftest fixture stays visible to `tests/smoke/`, so step 7 still resolves it. Step 4 depends on this same promotion. Cover: (a) `classify_entry_state` for each of the five tuples incl. `None` for `season_map`/`multi_ep_alias` and `ARCHIVED`/`None` for a dummy-on-disk onboarded entry; (b) `guess_manual_id` for a movie/series/anime filename (assert prefix + lang + 4-digit year extraction, incl. a no-year filename → no year segment); (c) `suggest_target_folder` returns the curly `{tvdb-…}` shape for a series/anime and `{tmdb-…}` for a movie and marks `applies=False` for an existing in-library item; (d) `suggest_next_command` returns the exact strings from the table; (e) `collect_reclaimable` over a seeded sandbox containing one UNPREPPED file, one `local_ready` leaf, one `onboarded` leaf with a real file, one `archived` leaf with a dummy, and a `season_map`+`multi_ep_alias` pair — assert badges, that ARCHIVED/alias/season_map are excluded, the GB total sums only reclaimable items, and no row is duplicated.
  - Constraints (verbatim in this step): "Never touch real C:\\Media files or real library_*.json." and "Run `python -m pytest tests/test_web_datafns.py -q` and fix failures before marking the step done."
  - Acceptance: `python -m pytest tests/test_web_datafns.py -q` green.

- [x] 3. [model: opus] [effort: max] [candidates: 3] Build the FastAPI app + action-execution model in `webui/server.py`.
  - Files: `webui/__init__.py`, `webui/server.py`
  - Details: `create_app()` returns a `FastAPI` instance. Routes:
    - `GET /api/reclaim` → `main.collect_reclaimable()`.
    - `GET /api/library` → slim summary (counts by status per category; reuse data-functions, do not re-walk disk).
    - `POST /api/action/{name}` with JSON body `{id?, filepath?, confirm?, options?}`. Validate `name` against the fixed allow-list `{"prep","push","replace","sort","prep_push_rep"}` (404 otherwise). For `replace` require `confirm is True` else `409`. Spawn the matching `main.cmd_*` in a `threading.Thread`, capture stdout into the job record (redirect via `contextlib.redirect_stdout` to a buffer), store `{id, name, status: running|done|error, output, started_at}` in a module-level dict guarded by a `Lock`, return `{job_id}` immediately (202).
    - `GET /api/job/{job_id}` → the job record (for polling).
    - Mount `StaticFiles(directory=webui/static, html=True)` at `/`.
    - Bind nothing here (no `uvicorn.run`); `create_app` is import-safe and TestClient-friendly.
  - Progress mechanism: POLLING (`GET /api/job/{id}`), chosen over SSE for v1 because it needs no async generators, survives a closed tab, and is trivially testable with TestClient (justification recorded in DECISIONS.md). SSE/WebSocket streaming is the Tier-S/IMP-F10 upgrade.
  - Safety: server is created here but only ever bound to localhost by `cmd_web` (step 5). Actions call existing `cmd_*` UNCHANGED — no copy of their logic.
  - Acceptance: `python -c "from webui.server import create_app; create_app()"` succeeds (with fastapi installed); no route imports `uvicorn` at module top. The step-4 `TestClient` suite must pass against the chosen candidate. All candidates expose the IDENTICAL HTTP contract above (routes, allow-list, 409-without-confirm, 202+job_id, polling shape) — they differ ONLY in the action-execution model behind `POST /api/action`.
  - Judge criteria (most important first): (1) safety of partly-destructive actions — two mutating actions (push/replace) must not corrupt each other's state or the shared single ADB device; (2) correctness + isolation of per-job captured output (NO cross-job / cross-thread stdout bleed); (3) testability with `TestClient` (deterministic job completion, no port binding); (4) simplicity + fit as the Tier-S daemon seed.
  - Candidate approaches (genuinely different concurrency models; SAME HTTP contract):
    - A: **Thread-per-action + captured stdout** — the recipe in Details: each action in a `threading.Thread`, stdout captured per job. This candidate MUST solve the process-global `redirect_stdout` race (per-thread stream shim or a lock) or rigorously argue why it is safe — that burden is the candidate's.
    - B: **Subprocess-per-action** — run `python main.py <cmd> …` via `subprocess.Popen`, stream stdout/stderr from the pipe into the job record. True isolation + real per-job output; cost is a process spawn + a library reload per action.
    - C: **Serialized single-worker queue** — a `queue.Queue` + ONE worker thread runs actions one at a time (matches the reality that push/replace must not run concurrently against one device); `POST` enqueues and returns the job id; per-job output captured. Sidesteps the stdout race by construction.
  - ⛔ Checkpoint C3 (human judge gate): after the judge decides, the orchestrator presents its decision + per-candidate analysis + recommendation and STOPS — no auto-merge/commit; proceed only with the user-selected candidate (see the Multi-candidate note under `## Steps`).

- [x] 4. [model: sonnet] [effort: medium] Endpoint tests with FastAPI `TestClient` (no live browser/uvicorn).
  - Files: `tests/test_web_endpoints.py`
  - Details: Use the `sandbox` + `make_video` (promoted to `tests/conftest.py` in step 2) + `mock_device` + `fake_dummy` fixtures. Build the app via `create_app()` and `from fastapi.testclient import TestClient`. Cover: (a) `GET /api/reclaim` returns 200 with `items` + `total_reclaimable_bytes` against a seeded sandbox; (b) `POST /api/action/replace` WITHOUT `confirm` → 409 and no file change; (c) `POST /api/action/replace` WITH `confirm:true` on a seeded `onboarded` leaf → 202 job id, then poll `GET /api/job/{id}` until `done` and assert the on-disk file became the fake dummy and the library flipped to `archived`; (d) `POST /api/action/{bogus}` → 404; (e) `POST /api/action/sort` → 202 then `done`. Patch nothing in `cmd_*` (they run against the sandbox via the dual-patched bindings); `mock_device`/`fake_dummy` neutralize ADB/ffmpeg. Skip the whole module with `pytest.importorskip("fastapi")` so the suite stays green where fastapi is absent.
  - Constraints (verbatim): "Never touch real C:\\Media files or real library_*.json." and "Run `python -m pytest tests/test_web_endpoints.py -q` and fix failures before marking the step done."
  - Acceptance: `python -m pytest tests/test_web_endpoints.py -q` green (or cleanly skipped without fastapi).

- [x] 5. [model: sonnet] [effort: medium] Add `cmd_web` + the `web` dispatch arm to `main.py`.
  - Files: `main.py`
  - Details: `cmd_web(host="127.0.0.1", port=8765, open_browser=True)`: import `uvicorn` and `from webui.server import create_app` INSIDE the function (so a missing dep degrades to a clear `❌ web requires fastapi+uvicorn — pip install -r requirements.txt` message, and importing `main` never hard-requires fastapi); build app; print `🌐 MediaVault web UI: http://{host}:{port}`; if `open_browser`, best-effort `webbrowser.open(...)`; `uvicorn.run(app, host=host, port=port, log_level="warning")`. Add `elif cmd == "web":` to the dispatch (`main.py:2948`+ chain): parse optional `--port <int>` (validate int, else `❌` + `sys.exit(1)`), `--host <str>`, `--no-browser` flag; call `cmd_web(...)`. Add a `web [--port N] [--host H] [--no-browser]` line to the top-level usage block (`main.py:2920`+). Keep the binding default localhost.
  - Acceptance: `python main.py web --help`-style misuse paths print usage; with fastapi absent, `python main.py web` prints the clear remediation and exits non-zero (does not traceback). Manual: with fastapi present, server starts and serves `/api/reclaim`.

- [x] 6. [model: opus] [effort: max] [candidates: 3] Frontend single-page app ("futuristic nice UI" — the user's headline ask).
  - Files: `webui/static/index.html`, `webui/static/app.js`, `webui/static/styles.css`
  - Details: `index.html` loads `app.js`+`styles.css` (no framework, no build). On load `fetch('/api/reclaim')`; render a header with the reclaimable-GB total and filter chips for the four badges (toggle visibility client-side); render rows with: badge pill, path/id, size, a read-only "next command" with a Copy button, and an editable provider-id field inside the "suggested folder" with a Copy button. Action buttons per row map to the badge's command (`prep`/`push`/`replace`/`prep_push_rep`); a global `Sort library` button calls `sort`. Clicking an action `POST`s `/api/action/{name}`, then polls `/api/job/{id}` every ~1 s and shows status/output inline. The `replace` button opens a confirm modal ("This deletes the original after verifying the cloud upload"); only on confirm does it POST with `confirm:true`. Plain, dark, responsive CSS — "futuristic nice UI" via clean cards/badges, not heavy assets.
  - Acceptance: Manual browser walkthrough (see Verification) renders the list, filters work, Copy buttons copy, an action shows a job result, replace requires the modal. All candidates satisfy this identical behavior contract; they differ ONLY in information architecture / interaction model / aesthetic.
  - Judge criteria (most important first): (1) clarity of the four-state distinction + the suggested next command at a glance (an operator can act without reading docs); (2) destructive-action safety in the UX (the `replace` confirm is unmissable); (3) "futuristic but functional" aesthetic + responsiveness; (4) accessibility + code simplicity (vanilla JS/CSS, no build step).
  - Candidate approaches (genuinely different IA/interaction models — NOT cosmetic restyles):
    - A: **Mission-control data table** — one dense, sortable table; badge column; inline per-row action buttons; keyboard-navigable. Operator/console feel, maximum information density.
    - B: **Card grid** — each item a card (badge, size, poster-placeholder, suggested command, action buttons); visual, touch-friendly, the most "futuristic" consumer feel.
    - C: **Master–detail** — a left filterable list + a right detail pane showing the selected item's badge, suggested command, suggested folder (editable provider id) and actions; workflow-focused for acting on one item at a time.
  - ⛔ Checkpoint C3 (human judge gate): after the judge decides, the orchestrator presents its decision + per-candidate analysis + recommendation and STOPS — no auto-merge/commit; proceed only with the user-selected candidate (see the Multi-candidate note under `## Steps`).

- [x] 7. [model: opus] [effort: medium] Wire `web` + `collect_reclaimable` into the smoke suite (per-command + alias sweep).
  - Files: `tests/smoke/test_smoke_all_commands.py`
  - Details: Read `docs/testing-strategy.md` first. In `TestEachCommand` add `test_web_collect_reclaimable` (the `web` command's testable core): seed via the existing `_seed_single` helper plus an extra UNPREPPED file in the sandbox Media tree (mirror `test_scan_unprepped`, request `smoke_local_root`), call `main.collect_reclaimable()`, assert it does not raise, returns the expected keys, and the UNPREPPED extra file appears with badge `UNPREPPED`. Do NOT start uvicorn in the smoke suite (it would bind a port / hang) — exercise the data-function (and, optionally, `create_app()` import via `pytest.importorskip("fastapi")`). In `TestAliasSweep` add `test_web_reclaim_alias(self, sandbox_alias, smoke_local_root)` calling `main.collect_reclaimable()` to prove the alias-bearing library does not crash the new whole-library walker — THIS is the anti-PR#21 guard for the new iterator. Keep the suite < 30 s (no real split, no browser).
  - Constraints (verbatim): "Never touch real C:\\Media files or real library_*.json." and "Run `python -m pytest tests/smoke -q` and fix failures before marking the step done."
  - Acceptance: `python -m pytest tests/smoke -q` green and < 30 s.

- [x] 8. [model: sonnet] [effort: low] Truth-up `requirements.txt` (bundled IMP-A10 slice for this feature).
  - Files: `requirements.txt`
  - Details: Add `fastapi` and `uvicorn[standard]` for the web app. CONFIRMED — bundle full **IMP-A10** in this PR: ALSO add `requests` and `webdriver-manager`, and keep `undetected-chromedriver` with the `# reserved: anti-bot fallback…` comment per `improvements/improvements_tierA.md:200`. On completion mark IMP-A10 done (tier file + PRIORITY.md + priority-graph) alongside E12/D16 in step 9.
  - Acceptance: `pip install -r requirements.txt` into a fresh venv succeeds and `python -c "import fastapi, uvicorn"` works.

- [x] 9. [model: haiku] [effort: low] Docs + IMP bookkeeping (documented behavior change). (Executed inline by the orchestrator + architect-style edits after repeated spend-limit interruptions on sub-agents; effort effectively raised.)
  - Files: `README.md`, `ARCHITECTURE.md`, `improvements/improvements_tierE.md`, `improvements/improvements_tierD.md`, `improvements/improvements_tierA.md`, `improvements/PRIORITY.md`, `docs/priority-graph/priority-graph.html`, `docs/feature-web-console/PLAN.md`, `docs/feature-web-console/DECISIONS.md`
  - Details: This step is the "architect updates ARCHITECTURE.md/README" + the IMP bookkeeping the task requires. (a) README: document `python main.py web [--port N] [--host H] [--no-browser]` and the Disk Reclaim view. (b) ARCHITECTURE.md: add the `web` command to the §5 subcommand table and a short subsection describing `collect_reclaimable`/the data-functions and the four badges. (c) Mark IMP-E12 `Status: done` in `improvements_tierE.md` with the PR ref, AND mark **IMP-A10** `Status: done` in `improvements_tierA.md` (requirements truth-up completed in step 8). When marking E12 done, ALSO reconcile its now-stale "Proposed change" text in `improvements_tierE.md` (it still describes an `mvweb.py` module + a poster-grid `GET /library`/`GET /entry/<id>`/`POST /command/<name>` API) to match what shipped — the `webui/` package + the Disk-Reclaim API (`/api/reclaim`, `/api/action/{name}`, `/api/job/{id}`). (d) Add a new `IMP-D16: scan_reclaimable` task to `improvements_tierD.md` (Category: other; Priority: high; Files: `main.py` data-functions; describe the four-state reclaim scan; note it is the data layer behind `web` and feeds IMP-D1/A4; Status: done on this PR). (e) Advance IMP-D1: add a one-line note that the total-reclaimable-GB slice is delivered by `web`/`collect_reclaimable` but D1's full dashboard remains pending. (f) PRIORITY.md: move E12 to DONE, move IMP-A10 to DONE, add D16 (DONE), bump "Last updated" + repoint the 👉 NEXT pointer (A10 is bundled/done here, so NEXT points to IMP-S1 or A12 per Band 1 — pick per the current board). (g) priority-graph.html: edit the SOURCE `TASKS` and `EDGES` arrays (NOT the derived `nodes`/`edges`). Each node is `[id, label, tier, priority, status, note]` and the visual ring is keyed off the **priority** field (`t[3]`), so every DONE node sets BOTH `priority` AND `status` to `"done"`. Set the `E12` node to `priority="done"` AND `status="done"` (currently `"high"`/`"todo"`), set the `A10` node to `priority="done"`/`status="done"`, add a `["D16","scan_reclaimable","D","done","done","four-state reclaim scan behind web"]` node, and add edges `["E12","D16"]` (or `["D16","E12"]`); keep `["A4","E12"]`. ALSO repoint the `⚡ Next: IMP-A10` banner near the top of the file off A10 to the new 👉 NEXT. The graph and PRIORITY.md must agree (maintenance protocol, PRIORITY.md bottom).
  - Acceptance: `improvements`/PRIORITY/graph cross-agree; `python -c "import re,html,glob; ..."`-style sanity (or just visual) shows E12/D16 done; ARCHITECTURE.md + README mention `web`.

## Risks and edge cases
- **New whole-library walker (`collect_reclaimable`) is exactly the PR #21 crash class.** It dereferences `folder_path`/`filename`. It MUST skip/`_resolve_alias` `season_map` + `multi_ep_alias` (step 1 constraint) and is guarded by the step-7 alias sweep. This is the single highest risk.
- **Disk vs status disagreement.** An `onboarded`/`restored_local` entry may have a dummy on disk (already replaced out-of-band) — must classify by ACTUAL on-disk size, not status alone, or it shows phantom reclaimable GB.
- **De-dup.** A library leaf and its on-disk file are the same physical thing; the walk + library iteration must not double-count (normpath-lower de-dupe).
- **Port already in use.** `uvicorn.run` will raise; `cmd_web` should let it surface a clear error (acceptable for v1 — user picks `--port`).
- **`guess_manual_id` will sometimes be wrong** (release-noise filenames, anime absolute numbering). It is explicitly an EDITABLE placeholder; never auto-preps. No TMDB lookup here (deferred to D10/E3).
- **Cross-drive media (`D:\`).** `collect_reclaimable` walks `LOCAL_ROOT` roots only (like `scan_unprepped`); files on other drives are out of scope for v1 (same limitation as `scan_unprepped` today).
- **`replace` past-PONR hard-fail.** If `cmd_replace` raises `RollbackHardFail`, the job thread records it as `error` with the `resume_cmd` text; the UI surfaces it. The replace contract is untouched.
- **One-click `replace` deletes the only full-quality original (BEST_PRACTICES §A5).** The UI reuses `cmd_replace`'s verify-uploaded-before-delete, but adds NO replication gate (A4/A5/IMP-X1 — most titles still live in a single Google account) and NO pre-delete subtitle-extraction / enrichment (B1/B2/IMP-E1/U1). Surfacing one-click replace makes the documented single-account deletion risk easier to trigger; for v1 this is consciously accepted and OUT OF SCOPE to fix here (gating belongs to X1/E1/U1). Keep the confirm-modal copy to "verifies the cloud upload" — do NOT imply replication. Revisit gating the UI replace on X1 once replication ships.
- **fastapi/uvicorn absent.** Importing `main` must NOT require fastapi (imports are inside `cmd_web`/lazy); tests `importorskip`; `cmd_web` prints a clear remediation.
- **Frontend served over localhost only.** The server can trigger destructive `replace`; never bind a non-loopback host by default; the `replace` confirm gate is server-enforced (409 without `confirm`), not just client-side.

## Consumer Impact Analysis
No step adds, changes, or removes a shared data contract: no new/renamed/removed library entry type, no new/renamed shared field/key, no `status` value change, no ID-shape change. `collect_reclaimable` and the other data-functions only READ existing keys (`status`, `uploaded`, `folder_path`, `filename`, `tech_spec.size_bytes`, `type`) and the on-disk size. Therefore `ENTRY_TYPE_KEYS` (`main.py:114`) and `tests/test_entry_schema_guard.py` are unchanged, and a full per-consumer audit table is not required. The one consumer-style risk — the NEW whole-library iterator added in step 1 — is handled by the §"Risks" alias/season_map-skip requirement and verified by the step-7 alias sweep (`TestAliasSweep`), which is the registry's enforcement surface for "a new iterator that doesn't tolerate the alias."

## Verification
Run after all steps:
1. `python -m pytest tests/test_web_datafns.py -q` — data-function unit tests pass.
2. `python -m pytest tests/test_web_endpoints.py -q` — endpoint tests pass (or skip cleanly without fastapi; install fastapi locally to actually run them).
3. `python -m pytest -q` — full suite green (no regressions).
4. Manual browser walkthrough (PowerShell): `python main.py web` → browser opens `http://127.0.0.1:8765` → confirm the Disk Reclaim list renders with badges + GB total → toggle each filter chip → Copy a suggested command and a suggested folder → run a non-destructive action (e.g. `Sort library`) and watch the job result → click `replace` on a `PUSHED·NOT-ARCHIVED`/`RESTORED·REPLACE-AGAIN` item and confirm the modal gates it. (Use a throwaway/sandbox library for the destructive check.)
5. `python -m pytest tests/smoke -q` — **the fast full-command cross-command gate (must be the LAST gate; `main.py` is touched). Must be green and < 30 s.**

## Out of scope
- A viewing/playback UI (couch experience). Jellyfin remains the viewing surface (locked 2026-06-12). This is the OPERATIONS console only.
- One-click file MOVE/relocate (the UI suggests a folder + a copyable command; it never moves or renames files). The real move is **IMP-D8** (`relocate`).
- Automated TMDB/TheTVDB/AniDB lookup for ids or provider-id tags (the provider id is an editable placeholder). Deferred to **IMP-D10/E3**.
- Renaming or migrating EXISTING folders to the provider-tag convention (would break hashing/sidecars/`.sha256`/uid/`_parts`/remote relpath). Suggestions apply to NEW items only; the user migrates existing ones manually as they re-fetch.
- The `--json` argparse refactor and config file (IMP-A2/A4/A5) — RELATED, not prerequisite; the data-functions are written so A4 can later reuse them.
- SSE/WebSocket live streaming and the full daemon (IMP-S2 / IMP-F10) — the backend is shaped to grow into them, but v1 uses polling and is foreground-only.
- The full IMP-D1 stats dashboard (only the total-reclaimable-GB slice ships here).
- Any change to rollback behavior (`cmd_replace` is reused verbatim — see Ripple effects).

## Ripple effects & related improvements
- **ENTRY_TYPE_KEYS guard:** unchanged — no new entry type and no shared-field change. The new iterator is kept alias/season_map-safe and swept.
- **Auto-rollback change-gate: NOT tripped.** The in-UI replace calls `cmd_replace` UNCHANGED — same verify-uploaded-before-delete, same `RollbackJournal`/PONR/`RollbackHardFail` contract. No journal format, PONR location, `mark_point_of_no_return()` placement, recorded-scope, or season resume-range messaging is touched. We add NO new destructive path; we only invoke the existing one. (Stated explicitly per `CLAUDE.md` change-gate.)
- **Smoke gate:** `main.py` is touched, so `pytest tests/smoke -q` is the final verification gate (above).
- **IMP-D1 (stats):** `collect_reclaimable` delivers the total-reclaimable-GB figure; D1's full per-library/per-language dashboard stays pending and can consume these functions.
- **IMP-D8 (`relocate`):** the suggested-folder + copyable-move is the v1 stand-in; D8 is the real one-click move that updates `folder_path` + sidecars atomically.
- **IMP-D10 / IMP-E3 (enrichment):** will replace `guess_manual_id`'s heuristics and the placeholder provider tag with authoritative TMDB/TVDB/AniDB lookups + NFO emission.
- **IMP-A2/A4/A5 (`--json`/argparse/config):** A4's `--json` will reuse these pure data-functions as its payload source (E12 ⇐ A4 edge in the graph); the port could later come from A5's config.
- **Tier S / IMP-S2 (daemon):** `webui/server.py` IS the daemon's seed — one FastAPI process that grows webhook ingestion + a job queue + a status page on top of these routes.
- **Jellyfin boundary:** unchanged — this never serves video; it manages files and points the user at the right next command.

## Open Decisions

DECIDED (settled in Phase 1 — built into this plan):
1. **Scope = operations/console UI**, not viewing. Jellyfin keeps viewing. (Viewing UI → Out of scope.)
2. **UI mechanism = local web app**: `python main.py web` → `http://127.0.0.1:8765` (port/host overridable), FastAPI+uvicorn backend, static HTML/CSS/JS frontend, shaped to grow into the Tier-S daemon. Adds `fastapi`+`uvicorn` deps (coordinate with IMP-A10).
3. **Scan view = ONE merged "Disk Reclaim" view** with a per-item state badge (`UNPREPPED`/`LOCAL·NOT-PUSHED`/`PUSHED·NOT-ARCHIVED`/`RESTORED·REPLACE-AGAIN`), filter chips, and a total-reclaimable-GB figure. The badge distinguishes unprepped vs prepped-but-still-local.
4. **Move-to-folder = SUGGEST-ONLY for v1**: compute + display the target folder and a copyable command; the UI does not move files. Real move = IMP-D8.
5. **Folder layout = current MediaVault layout + a media-server provider-id tag** in the **curly-brace** form (`{tvdb-…}` for TV/anime, `{tmdb-…}` for movies — the officially cross-compatible syntax), applied to NEW items only; NEVER move/rename existing folders (pre-existing `[tvdbid-…]` square folders are left untouched); provider id is an editable placeholder (no auto-lookup here).
6. **Next-command suggestions = deterministic, rule-based** from the state tuple → exact command string; UNPREPPED items get a guessed-but-editable id per naming conventions; no TMDB enrichment.
7. **Action safety = UI runs real `cmd_*` unchanged**; every destructive action (`replace`) requires an in-UI confirm modal (server-enforced via 409 without `confirm`); server is localhost-only; long actions use a job/progress mechanism.
8. **Foundations = a thin in-process pure data layer NOW** (`collect_reclaimable`, `classify_entry_state`, `suggest_next_command`, `suggest_target_folder`, `guess_manual_id`) returning dicts, called directly (no subprocess, no dependency on the pending `--json` refactor); alias/season_map-safe; covered by the smoke alias sweep.

RESIDUAL — all now RESOLVED (recorded for the record):
- **Provider-id source — DECIDED: editable placeholder now** (no API keys/caching; auto-lookup lands with IMP-D10/E3). NEW suggestions use the curly-brace form — `{tmdb-<id>}` for movies, `{tvdb-<id>}` for TV/anime (officially cross-compatible across Plex/Emby/Jellyfin).
- **Progress mechanism: SSE vs polling.** **Recommendation: polling** for v1 (`GET /api/job/{id}`) — no async generators, survives a closed tab, trivially TestClient-testable; SSE/WebSocket is the Tier-S/IMP-F10 upgrade. (Baked into step 3.)
- **requirements scope (step 8) — DECIDED: bundle full IMP-A10** in this PR (fastapi+uvicorn + requests + webdriver-manager + the undetected-chromedriver comment); A10 is marked done in step 9.
- **Multi-candidate scope (revised on user request):** steps **1, 3, 6** run as 3-candidate bake-offs + judge; steps 2/4/5/7/8/9 stay single-executor. Rationale + pinned contracts are under `## Steps`. "Don't worry about usage" was explicitly granted, so the extra worktree/judge cost is accepted for these three fork-worthy decisions.

## IMP bookkeeping reminder (on implementation)
- Mark **IMP-E12** `Status: done` in `improvements/improvements_tierE.md` (with PR ref), and **IMP-A10** `Status: done` in `improvements/improvements_tierA.md` (requirements truth-up bundled here).
- Add **IMP-D16** (`scan_reclaimable`, the four-state reclaim scan / data layer behind `web`) to `improvements/improvements_tierD.md`, `improvements/PRIORITY.md`, AND `docs/priority-graph/priority-graph.html` — **in the same change** (maintenance protocol).
- Advance **IMP-D1**: note the total-reclaimable-GB slice is delivered; D1's full dashboard remains pending (do not mark D1 done).
- Architect updates **ARCHITECTURE.md** (§5 subcommand table + a `web`/data-functions subsection) and **README.md** (the new `web` command) — documented behavior change.
- Update **PRIORITY.md** (E12→DONE, A10→DONE, D16 added, bump Last-updated + repoint 👉 NEXT) and **priority-graph.html** (`E12` & `A10` nodes' status → `done`, add `D16` node + edges) so the two agree.
- Do NOT touch IMP-C18 (already merged; it was only the example of the mark-done pattern).

## Branch / PR / Manual test (END)

**Branch:** `feature/web_console` (from up-to-date `origin/main`; `docs/git-pr-conventions.md`).

**Human checkpoints in this run:** **C3** — after EACH multi-candidate step (1, 3, 6) the orchestrator pauses on the judge's decision for the user to confirm or override the chosen candidate BEFORE any merge/commit (see `## Steps`); **C1** — PR→`main` merge is human-gated; **C2** — branch archival is human-gated.

**PR to `main` (human-gated — Checkpoint 1; create then STOP and ask):**
- Title (MUST include the IMP code): `feature: local web ops console — Disk Reclaim view + suggested next-commands + integrated sort/replace — IMP-E12`
- Body order (per conventions):
  1. Auto-generated Claude Code summary (Summary / Changes / Test plan).
  2. `## Original task prompt` — the complete verbatim prompt (reproduced below).
  3. Trailer `🤖 Generated with [Claude Code](https://claude.com/claude-code)`.
- Co-author trailer on commits names the model that did the work.
- After the user approves: squash-merge; then (Checkpoint 2, also human-gated) archive the branch as an annotated `archive/feature/web_console` tag and delete the branch.

**Manual test commands (PowerShell, copy-paste):**
```powershell
# from repo root, venv active
pip install -r requirements.txt          # picks up fastapi + uvicorn
python -m pytest tests/test_web_datafns.py tests/test_web_endpoints.py -q
python -m pytest -q                       # full suite
python main.py web                        # opens http://127.0.0.1:8765 in the browser
#   → confirm the Disk Reclaim list + badges + reclaimable-GB total
#   → toggle each filter chip (UNPREPPED / LOCAL·NOT-PUSHED / PUSHED·NOT-ARCHIVED / RESTORED·REPLACE-AGAIN)
#   → Copy a suggested command and a suggested folder
#   → run "Sort library" and watch the job result
#   → click "replace" on a reclaimable item and confirm the modal gates it (use a sandbox library)
python main.py web --port 9000 --no-browser   # overridable port; no auto-open
python -m pytest tests/smoke -q           # LAST gate — cross-command smoke, must be green < 30s
```

## Suggested next tasks (after this)
- ~~IMP-A10~~ — CLOSED in this PR (requirements truth-up bundled into step 8). New 👉 NEXT candidate: **IMP-S1** (Tier-S Phase-0 foundation) or **IMP-A12** per PRIORITY Band 1.
- **IMP-D8** — `relocate`: the real one-click move the UI currently only suggests.
- **IMP-A2 → A4** — argparse then `--json`; A4 reuses these data-functions as its payload source.
- **IMP-D1** — the full `library_stats` dashboard (this PR delivered only the reclaimable-GB slice).
- **IMP-E3 / IMP-D10** — TMDB/TheTVDB/AniDB enrichment to replace `guess_manual_id` heuristics + the placeholder provider tag (and NFO emission).
- **IMP-S2** — `mvdaemon`: grow `webui/server.py` into the always-on Tier-S service (webhooks + job queue + status page on top of these routes).

---

## Original task prompt
> $ python main.py scan_unprepped
> You know this command right? this checks all my local files which are unprepped and ready to be prepped.
> Modify this Fully with some futuristic nice UI application or webpage. It should open this nice UI have multiple buttons:
> 1. to scan prepped — have all existing commands which this covers properly
> 2. add new functionality to scan and get prepped and pushed also — but currently in Local — which is not archived which occupies space. You can merge the above 2 also and find a way to distinguish above 2 scenarios. Also suggest on the next commands to run for the items in scan if I select a particular item based on our naming conventions and folder structure also.
> Note that it should work well in plex and emby and jellyfin servers. Give option to move the new unprepped file to some proper suggested folders also in the same UI.
> 3. also add the option to sort also in same UI — this sort is already an existing command but separate one. lets integrate into the same UI which covers all operations.
> also for 2 — already pushed files when I just have in my local and already watched — add a button to just replace — it should do current replace step which checks if already uploaded properly before deleting the original file and replacing with dummy.
>
> Give me different options to fix this. let me check that and decide how to proceed. Once I confirm, I want you to create an elaborate plan to fix this in the best and optimal way.
> If any decision pending, give me live example in real world usecase complete step by step and ask me about the different options before you finalize the plan.
> Also, any other related improvements, how this approach will affect that can you elaborate. Also any prerequisite small task you want me to complete before we start this implementation?
>
> Deliverables: PLAN.md only (no code, no branches) with steps + tests + verification + "Open Decisions". Note if we are solving any improvement tasks with this task say C18 - IMP-C18 needs to be marked done in improvements_tierC.md on implementation, the architect updates ARCHITECTURE.md/README (documented behavior change), and I want branch name, PR to main, and manual test commands at the end. Also you also need to update the priority graph and suggest me the next starts we can start.
