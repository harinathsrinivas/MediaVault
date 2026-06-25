# Execution Log

Task: `web` command — local FastAPI operations/console UI (Disk Reclaim view + suggested next-commands + integrated sort/replace) — IMP-E12

(Note: this file replaces a stale prior-run log from IMP-C18, which is already merged; its content is preserved in git history.)

## Step 1 — [status: done] (multi-candidate, 3 candidates, opus-max)
- Winner: Candidate A (disk-first + targeted library second-pass) — branch `feature/web_console__cand_a`, tag `candidates/step-1/A-chosen`. Losers B/C preserved as `candidates/step-1/B-rejected`/`C-rejected`.
- DECISION.md: `.candidates/step-1/DECISION.md` (committed on the feature branch).
- Merge commit: `35b5798` (squash-merge of cand_a; root `CRITIQUE.md` excluded; `DECISION.md` force-added). +494 lines.
- Files changed: `main.py` — added five module-level read-only data-functions: `classify_entry_state`, `guess_manual_id`, `suggest_target_folder`, `suggest_next_command`, `collect_reclaimable` (placed near `cmd_scan_unprepped`).
- Outcome: `collect_reclaimable()` walks the 3 category roots + the library, classifies each physical file into `UNPREPPED`/`LOCAL_NOT_PUSHED`/`PUSHED_NOT_ARCHIVED`/`RESTORED_REPLACE_AGAIN` (ARCHIVED + `season_map`/`multi_ep_alias` excluded), de-dupes by normpath-lower, returns `{items, total_reclaimable_bytes, total_reclaimable_human}`.

### Key decisions (carry into downstream steps — esp. step 2 tests)
- **UNPREPPED is gated on `on_disk_real` (`size >= DUMMY_MAX_BYTES`):** a NOT-in-library video under the dummy threshold is EXCLUDED from `items`. The gate lives inside `collect_reclaimable` (NOT in `classify_entry_state`, which still honors `entry=None ⇒ "UNPREPPED"` literally). → **Step 2 MUST assert a sub-threshold unknown file does NOT appear in `items`.**
- `classify_entry_state` maps status→badge via a `.get()` lookup → an UNKNOWN status yields `None` (no invented badge). Returns `None` for `season_map`/`multi_ep_alias`; `entry=None ⇒ "UNPREPPED"`; in-library + dummy/absent ⇒ `"ARCHIVED"` (if archived) or `None`.
- `suggest_target_folder` for an in-library item returns `provider_tag=None`, `editable_provider_field=None`, `applies=False` (informational only). NEW items get curly `{tmdb-…}` (movies) / `{tvdb-…}` (TV/anime).
- item dict shape (exact 7 keys): `{id, badge, path, size_bytes, suggested_command, suggested_folder, guessed}` (`guessed` bool: True for UNPREPPED guessed id, False for in-library).
- A reused `cmd_scan_unprepped`'s exclusion set. Alias-safety: `season_map`/`multi_ep_alias` skipped in BOTH the `known_paths` build and the library second pass.
- (Step-2 executor: READ the merged `main.py` functions for exact names/signatures — the above are hints, the code is ground truth.)

### Verification
- Acceptance: `importable+callable ok`. Live read-only run: 38 items / 160.84 GB. Read-only proven (library mtimes unchanged).
- Smoke gate on the MERGED feature branch: `python -m pytest tests/smoke -q` → **56 passed in 17.21s**.

## Step 2 — [status: done]
- Executor: executor-sonnet
- Model: sonnet
- Mode: single-executor
- Files changed:
  - `tests/conftest.py` — promoted `make_video` fixture here (added `_REAL_MEDIA_BYTES` constant + `make_video` fixture after `fake_dummy`; `hashlib` import added inline inside the fixture)
  - `tests/smoke/conftest.py` — removed `_REAL_MEDIA_BYTES` constant, removed `make_video` fixture, removed now-orphaned `import hashlib`
  - `tests/test_web_datafns.py` — NEW: 36 unit tests for the five pure data-functions
- Outcome: Moved `make_video` from `tests/smoke/conftest.py` up to `tests/conftest.py` (parent conftest, visible to both `tests/` and `tests/smoke/`). The fixture's dependency `_REAL_MEDIA_BYTES` was also relocated to `tests/conftest.py`; `hashlib` used inside the fixture inline (no new top-level import needed since `tests/conftest.py` already imports `hashlib`). Created `tests/test_web_datafns.py` with 36 tests covering all six acceptance criteria: (a) `classify_entry_state` for all 5 result values + edge cases; (b) `guess_manual_id` for movie/series/anime prefixes + year extraction + no-year case; (c) `suggest_target_folder` for tmdb/tvdb curly-brace shape + in-library `applies=False`; (d) `suggest_next_command` exact strings from the State table; (e) `collect_reclaimable` over a seeded sandbox asserting badges, ARCHIVED/alias/season_map exclusions, total_bytes sum, and no-duplicate guarantee; (f) sub-DUMMY_MAX_BYTES unknown file does NOT appear in items (pins step 1's UNPREPPED size gate).
- Key decisions: `make_video` in smoke conftest referenced only `_REAL_MEDIA_BYTES` (local constant) and `main.DUMMY_MAX_BYTES` (already imported via `import main`). Both were cleanly relocatable to `tests/conftest.py` with no other dependency drag. The `hashlib` import that `make_video` uses was added inline inside the fixture body (to avoid a top-level duplicate since `tests/conftest.py` already imports `hashlib` at the top). The now-orphaned `import hashlib` was removed from `tests/smoke/conftest.py`.
- Verification:
  - `python -m pytest tests/test_web_datafns.py -q` → **36 passed in 0.85s**
  - `python -m pytest tests/smoke -q` → **56 passed in 14.66s**

### Follow-up (from DECISION.md "what we keep", NOT auto-applied)
- Candidate C's unified-normpath-index architecture is the ideal target for a future perf refactor of `collect_reclaimable` (keep A's semantics + the UNPREPPED size-gate + the informational in-library `suggest_target_folder`). C also sorted items largest-first — a cheap nicety A could adopt.

## Step 3 — [status: done] (multi-candidate, 3 candidates, opus-max)
- Winner: Candidate C (serialized single-worker FIFO queue) — branch `feature/web_console__s3cand_c`, tag `candidates/step-3/C-chosen`. Losers A (thread+stdout-proxy) / B (subprocess) preserved as `candidates/step-3/A-rejected`/`B-rejected`.
- DECISION.md: `.candidates/step-3/DECISION.md` (committed on the feature branch).
- Merge commit: `0d77a68` (squash-merge of s3cand_c; root `CRITIQUE.md` excluded; `DECISION.md` force-added). +454 lines, 4 files.
- Files added: `webui/__init__.py`, `webui/server.py` (~316 lines — `create_app()` + the queue/worker action model), `webui/static/index.html` (placeholder — step 6 overwrites the real SPA).
- NOTE: a fresh executor session FINISHED candidate C after the original died on an auth drop (it had written the code but not validated/critiqued); the finisher confirmed ZERO code changes were needed.

### Key decisions (carry into downstream steps — esp. step 4 endpoint tests + step 6 frontend)
- **FIXED HTTP contract (all candidates honored it; step 4 tests + step 6 frontend bind to THIS):** `GET /api/reclaim` → `collect_reclaimable()` dict; `GET /api/library` → status-counts-by-category; `POST /api/action/{name}` body `{id?,filepath?,confirm?,options?}` — allow-list EXACTLY `{prep,push,replace,sort,prep_push_rep}` (404 else), `replace` needs `confirm is True` else **409**, else **202** `{job_id}`; `GET /api/job/{job_id}` → job record (404 unknown); StaticFiles(html=True) mounted LAST at `/`. NO uvicorn import (step 5 owns that).
- Job record shape: `{id, name, status: running|done|error, output, started_at}` in a module dict under a Lock. Progress = POLLING `/api/job/{id}`.
- C's model = ONE daemon worker draining a `queue.Queue`, in-process, one action at a time → device-safety + stdout-isolation by construction. Worker catches `SystemExit` FIRST (load_library does sys.exit(1) on corrupt lib) so it can't wedge. The Tier-S/IMP-S2 daemon seed.
- **C's arg-mapping gap (recoverable follow-up, NOT a contract miss):** C threads only `id`/`filepath`/`split_method`/`split_val` into `cmd_*`; it omits `device_id`/`eager_rehash`/`temp_dir`/`parent_id` (all default cleanly in main.py) and skips A's `422` pre-validation. Per the judge's "what we keep", FOLD A's richer arg mapping + 422 validation into STEP 6 (or a follow-up). Also add the documented `redirect_stdout` "no unrelated concurrent stdout writer" caveat + `_JOBS` eviction before IMP-S2.

### Environment / deps
- Installed into `.venv` ahead of the plan's step 8 (needed now to validate create_app/TestClient): `fastapi 0.138.0`, `uvicorn[standard] 0.49.0`, `httpx 0.28.1`. **`httpx` is a TestClient dependency NOT in the plan's requirements list — step 8 should add it (as a test/dev dep at minimum).**

### Verification
- `create_app()` OK on the merged tree (no uvicorn import). Read-only contract verified on the merged branch.
- Smoke gate on the MERGED feature branch: initially 1 failed / 55 passed due to **flaky `test_push_real_split` (transient OSError in a REAL file-split — Windows file-handle/temp contention under full-suite load; pre-existing, unrelated to step 3 which added no code to the tested modules)**; it PASSED in isolation and the full smoke suite PASSED 56/56 on clean retry. Flaky-test candidate worth a future stabilization (own follow-up).

## Step 4 — [status: done]
- Executor: executor-sonnet
- Model: sonnet
- Mode: single-executor
- Files changed: `tests/test_web_endpoints.py` (NEW — 5 tests)
- Outcome: Created `tests/test_web_endpoints.py` with 5 endpoint tests using FastAPI `TestClient`. Module skips cleanly via `pytest.importorskip("fastapi")` + `pytest.importorskip("httpx")` when those packages are absent. Tests cover: (a) `GET /api/reclaim` 200 with correct shape + at least one `PUSHED_NOT_ARCHIVED` item for a seeded onboarded+real-file entry; (b) `POST /api/action/replace` without `confirm` → 409 and file byte-content unchanged; (c) `POST /api/action/replace` with `confirm=True` → 202 + job_id, poll until `done`, assert dummy bytes on disk and `status=="archived"` in library; (d) `POST /api/action/bogus` → 404; (e) `POST /api/action/sort` → 202 + terminal state (accepts `done` or `error` since `cmd_sort` returns `None`, which the worker maps to `error`). All tests use the sandbox fixture dual-patch (LIBRARY_* + LOCAL_ROOT); `fake_dummy` neutralizes ffmpeg for replace; no ADB calls needed (replace only does local file renames). A shared `_seed_onboarded` helper writes a real (>DUMMY_MAX_BYTES) file via `make_video` and populates lib_movies.
- Key decisions: `cmd_sort` returns `None` (implicit return on the success path), which the worker maps to `status="error"`. Test (e) therefore accepts either terminal state and documents the behavior, rather than asserting `done`. No `mock_device` needed for replace tests — `cmd_replace` does local file renames only, no ADB calls. The `fake_dummy` fixture is sufficient to neutralize the `make_video_dummy` ffmpeg call.
- Verification:
  - `python -m pytest tests/test_web_endpoints.py -q` → **5 passed, 1 warning in 2.14s**
  - `python -m pytest tests/smoke -q` → **56 passed in 13.00s**

## Step 5 — [status: done]
- Executor: executor-sonnet
- Model: sonnet
- Mode: single-executor
- Files changed: `main.py`
- Outcome: Added three things to `main.py`: (1) `import webbrowser` at the top (line 11, after `import tempfile`); (2) `def cmd_web(host="127.0.0.1", port=8765, open_browser=True)` function with lazy internal imports of `uvicorn` and `webui.server.create_app` (wrapped in `try/except ImportError` that prints a clear remediation and calls `sys.exit(1)`), then builds the app, prints the URL, best-effort opens the browser, and calls `uvicorn.run`; (3) usage line `web [--port N] [--host H] [--no-browser]` in the help block; (4) `elif cmd == "web":` dispatch arm that manually parses `--host`, `--port` (validates int, exits non-zero on non-int), and `--no-browser` flag, then calls `cmd_web`.
- Key decisions: `import webbrowser` at module top is conventional (standard library, no dep issue). `uvicorn` and `from webui.server import create_app` are INSIDE the function body only — this is the load-bearing lazy import constraint: `import main` never pulls in fastapi/uvicorn. The dispatch arm follows the existing manual `sys.argv` parsing style (no argparse). The `--port` validation prints `❌ --port must be an integer` and calls `sys.exit(1)` matching the project's error style.
- Verification:
  - `python -c "import sys, main; assert 'uvicorn' not in sys.modules and 'webui.server' not in sys.modules, 'lazy import leaked'; print('lazy import OK')"` → **lazy import OK**
  - `python main.py web --port notanint` → exits with returncode 1, stdout contains `--port must be an integer` (tested via subprocess, no traceback)
  - Happy path (monkeypatched): `cmd_web` called with fake `uvicorn.run` and `webbrowser.open` → printed `🌐 MediaVault web UI: http://127.0.0.1:8765`, called `uvicorn.run` with correct host/port, called `webbrowser.open` with correct URL. **PASS**
  - `python -m pytest tests/smoke -q` → **56 passed in 14.37s**
  - `python -m pytest tests/test_web_endpoints.py tests/test_web_datafns.py -q` → **41 passed, 1 warning in 2.66s**
  - Did NOT run `python main.py web` to completion (would block; uvicorn.run is blocking). Tested via monkeypatching only.

## Step 6 — [status: done] (multi-candidate, 3 candidates, opus-max)
- Winner: **Candidate B (card grid)** — branch `feature/web_console__s6cand_b`, tag `candidates/step-6/B-chosen`. **USER-SELECTED at the C3 human gate, overriding the judge's pick of A** (per W-13 the choice is the user's). Losers A (mission-control table) / C (master-detail) preserved as `candidates/step-6/A-rejected` / `C-rejected`.
- DECISION.md: `.candidates/step-6/DECISION.md` (judge advisory; committed on the feature branch).
- Merge commit: `e2d76c4` (squash-merge of s6cand_b; root `CRITIQUE.md` excluded; `DECISION.md` force-added). +1395/-11.
- Files: `webui/static/index.html` (overwrote placeholder), `webui/static/app.js` (new, ~17.7 KB), `webui/static/styles.css` (new). Vanilla HTML/CSS/JS, no framework/build/CDN.
- All three candidates were verified correct + safe by the judge (exact action→endpoint→body mappings; replace POSTed ONLY from the confirm modal with `confirm:true`; exact modal copy; XSS-safe `<pre>.textContent` stdout). Judge ranked A first on at-a-glance density; user chose B.
- **Why B (user rationale, recorded):** B's card grid is the intended SUBSTRATE for a future "Apple-like" local media UI that grows beyond disk-reclaim — posters, movie titles, fetch-in-UI. Card tiles naturally hold artwork/titles; a dense table does not. B's poster-placeholder is the slot real posters drop into later. Aligns with W-2 (this FastAPI app = the Tier-S/IMP-S2 daemon-UI seed) and the project's future-media-UI direction.
- **Scope caveat (kept explicit):** this PR (E12) remains the OPERATIONS console; viewing/playback stays Jellyfin (W-1, locked 2026-06-12). Posters/titles/fetch-in-UI are NOT in this PR — follow-ons (poster/title enrichment → IMP-D10/E3; fetch-in-UI + always-on service → IMP-S2). Logged on the board in step 9.
- Live preview: all 3 candidates were launched (ports 8771/8772/8773) against the real read-only reclaim list (38 items / 160.84 GB) for the user's visual comparison, then stopped before archival.

### Verification
- Merged tree: `GET /`→200 serving B's SPA, `/app.js` + `/styles.css` 200, `/api/reclaim` 200 (TestClient).
- Smoke gate on merged feature branch: **56 passed**.

### Pending pre-PR touch-up (user-approved — fold the judge's "what we keep")
- **Server fix (`webui/server.py`):** the worker maps a falsy `cmd_*` return to `status="error"`, so a SUCCESSFUL `cmd_sort()` (returns `None`) shows as "error" in the UI. Fix the worker to treat "no exception (and not an explicit `False`)" as `done` (None/True/truthy → done; explicit False → error; exception → error). Then tighten step-4 endpoint test (e) to assert sort → `done`.
- **Frontend (`webui/static/app.js`, B):** add C's auto-refresh of `/api/reclaim` after a terminal job (B shares A's no-auto-refresh gap) so badges update without a manual reload; also port C's explicit 409/404/non-202 runner handling.

## Pre-PR touch-up — [status: done]
- Executor: executor-opus
- Model: opus
- Mode: single-executor
- Files changed: `webui/server.py`, `webui/static/app.js`, `tests/test_web_endpoints.py` (main.py NOT touched — cmd_* left unchanged by design).

### Change 1 — worker success/error mapping (`webui/server.py`) — SAFETY-CRITICAL
Verified the return convention of all five actions by reading `main.py` BEFORE choosing a rule:

| Command | Success return | Handled failure (no raise) | Raises? |
|---|---|---|---|
| `cmd_prep` (832) | `True` (incl. early-skips) | `False` — every path (file-not-found, alias clobber, hash fail, `except`) | internal exceptions caught → `False` |
| `cmd_push` (1270) | `True` (full + chunk-range) | `False` — every path (incl. both resume-message and rollback branches) | internal exceptions caught → `False` |
| `cmd_replace` (1865) | `True` | `False` — every pre-PONR path | `RollbackHardFail` post-PONR (PROPAGATES → worker BaseException → error) |
| `cmd_sort` (2443) | `None` (falls off end) | `None` — empty/unloadable library (line 2448) | — |
| `cmd_prep_push_rep` (2662) | `None` (falls off end) | `None` — prep/push/replace failed (2669/2682/2691); **swallows post-PONR `RollbackHardFail` into a bare `return` (2692-2695)** | — |

**Decisive finding: SOME actions return `None`-on-failure** — `cmd_sort` AND `cmd_prep_push_rep`. So the blanket rule `ok = result is not False` is UNSAFE (the instructions' branch 2): it would mark a FAILED `prep_push_rep` (file pushed but NOT archived, or original lost past the PONR that prep_push_rep swallowed) as `done` — a real safety regression on a destructive autopilot.

**Rule implemented (per-action convention):** introduced module-level `_NONE_IS_SUCCESS = {"sort"}` and changed the worker `else` (no-exception) branch to: `result is False → error`; `result is None → ok only if action in _NONE_IS_SUCCESS`; else (truthy) → `done`. Threaded the action `name` through the work-queue tuple (`_enqueue` puts `(job_id, name, runner, body)`; `_worker_loop` unpacks it). `None`-as-success is granted ONLY to `sort` (non-destructive; its single None-on-failure path is a read-only empty-library check that creates nothing → benign). `prep_push_rep` is deliberately EXCLUDED so its ambiguous `None` never auto-marks "done" — the safe direction for a destructive autopilot (a successful run still prints "AUTO-PILOT COMPLETE" in captured output and the reclaim refresh shows the archived state). The `SystemExit` and `BaseException`/`RollbackHardFail` branches were left untouched. Updated the stale comment at the former lines 166-168.

### Change 2 — fold candidate C robustness into candidate B frontend (`webui/static/app.js`)
- (a) **Auto-refresh after a terminal job:** added `scheduleReclaimRefresh()` (debounced via `_refreshTimer`, fires `load()` after `REFRESH_AFTER_JOB_MS = 2500`); called from `pollJob`'s terminal branch. The delay keeps the just-shown job result visible briefly before the grid rebuild re-renders badges (e.g. a replaced item drops PUSHED·NOT-ARCHIVED). Debounce coalesces back-to-back terminal jobs into one refresh. The global `sort` panel lives outside `#grid` so its result persists across the refresh.
- (b) **Explicit response-status handling:** added `actionHttpError(status, detail)` mapping 409→"needs explicit confirmation", 404→"unknown action", else a generic "HTTP {status}". `runAction` now reads `job_id` ONLY on 202; every other status parses the FastAPI `{"detail":…}` body (falls back to raw text) and throws a readable error rendered inline via the existing `.catch`. The modal-gated `confirm:true` replace flow is unchanged (replace still POSTed only from the modal).
- Kept vanilla JS, XSS-safe (textContent/`<pre>.textContent`, no innerHTML for data).

### Change 3 — tighten step-4 sort test (`tests/test_web_endpoints.py`)
Updated `test_sort_enqueues_and_finishes` (test "(e)"): now asserts terminal status is exactly `done` (was: accepts `done` or `error`). Docstring/comment updated to describe the fixed `_NONE_IS_SUCCESS` behavior instead of the old bug.

### Validation (all green)
- `node --check webui/static/app.js` → `NODE_CHECK_OK`.
- `python -m pytest tests/test_web_endpoints.py -q` → **5 passed, 1 warning in 2.93s** (incl. sort→`done` and replace-with-confirm→`done`, confirming `cmd_replace` `True`→`done` path intact).
- `python -m pytest tests/test_web_datafns.py -q` → **36 passed in 0.96s**.
- `python -m pytest tests/smoke -q` → **56 passed in 29.86s**.
- Focused re-run `test_sort_enqueues_and_finishes` + `test_replace_with_confirm_runs_and_archives` → **2 passed** (sort polls to `done`, replace polls to `done` against the sandbox — the optional sanity check, covered by the endpoint test).

## Step 8 — [status: done]
- Executor: executor-sonnet
- Model: sonnet
- Mode: single-executor
- Files changed: `requirements.txt`
- Outcome: Updated `requirements.txt` to add the full IMP-A10 truth-up plus the web-app runtime and test deps. Added `requests`, `webdriver-manager`, `fastapi`, `uvicorn[standard]`, and `httpx`. Attached the exact reserved comment to `undetected-chromedriver` per `improvements/improvements_tierA.md` line 200. `httpx` is noted as a test dep (one addition beyond the A10 list) required by `fastapi.testclient.TestClient` used in `tests/test_web_endpoints.py`. No version pins added (file was unpinned; style preserved). No other files touched.
- Key decisions: Used exact comment wording from improvements_tierA.md line 200 for `undetected-chromedriver`: `# reserved: anti-bot fallback for Google Photos (see RESEARCH_STORAGE_STREAMING.md §1.3)`. Added `httpx` with explicit comment explaining it is a test dep for TestClient — this is one dep beyond the A10 list, justified because the endpoint tests use `importorskip` and silently skip without it.
- Verification: `C:/Users/harin/PycharmProjects/MediaVault/.venv/Scripts/python.exe -c "import fastapi, uvicorn, httpx, requests; print('imports ok')"` → `imports ok`. All four packages imported cleanly.

Final `requirements.txt` content:
```
pymediainfo
selenium
undetected-chromedriver  # reserved: anti-bot fallback for Google Photos (see RESEARCH_STORAGE_STREAMING.md §1.3)
requests
webdriver-manager
fastapi
uvicorn[standard]
httpx  # test: required by fastapi.testclient.TestClient (tests/test_web_endpoints.py)
```

## Step 7 — [status: done]
- Executor: executor-opus
- Model: opus
- Mode: single-executor
- Files changed: `tests/smoke/test_smoke_all_commands.py`
- Outcome: Added the two mandated smoke tests wiring the new `web`/`collect_reclaimable` data layer into the cross-command gate. (1) `TestEachCommand::test_web_collect_reclaimable` seeds via `_seed_single` plus an EXTRA on-disk file written with `make_video` (>`DUMMY_MAX_BYTES`, so it qualifies for UNPREPPED emission), calls `main.collect_reclaimable()`, and asserts it does not raise, returns the contract keys (`items`/`total_reclaimable_bytes`/`total_reclaimable_human` with correct types), and that the extra file appears as exactly one `badge=="UNPREPPED"` row (matched by `os.path.basename`). It also optionally asserts `webui.server.create_app()` imports, guarded by `pytest.importorskip("fastapi")` — no uvicorn, no port bind. (2) `TestAliasSweep::test_web_reclaim_alias(self, sandbox_alias, smoke_local_root)` runs `collect_reclaimable()` against the alias-bearing library and asserts the contract keys — the anti-PR#21 guard proving the new whole-library iterator tolerates `season_map`/`multi_ep_alias` rows. No `main.py`/`webui/` change; reused `sandbox`/`sandbox_alias`/`smoke_local_root`/`make_video` (no DIY library patching).
- Key decisions: (a) **Matched the UNPREPPED row by `os.path.basename`, not the full path.** The shipped `collect_reclaimable` stores PASS-1 rows' `path` as `os.path.join(root, f)` (a non-normpathed walk join), so an exact-string or normcase compare would be brittle across separators; basename match mirrors the testing-strategy §8.1 "filter by `.name`, never a bracketed `[id]` glob" rule and is the robust choice. (b) **Used `make_video` for the extra unprepped file rather than the raw `b"x"*300_000` that `test_scan_unprepped` uses** — the step mandates it and it self-asserts `> DUMMY_MAX_BYTES`, so the file is guaranteed to clear the real-vs-dummy gate that `collect_reclaimable` applies before emitting an UNPREPPED row (a sub-threshold file would be silently excluded). (c) **Placed `test_web_reclaim_alias` immediately after `test_scan_unprepped_alias`** (both are whole-library disk walkers requesting `smoke_local_root`) so the alias sweep keeps its iterator-grouping. (d) Kept the optional `create_app()` assertion behind `importorskip` so the suite stays green where fastapi is absent (it IS present in this venv — `fastapi 0.138.0` — so the import path is genuinely exercised here).
- Verification: `C:/Users/harin/PycharmProjects/MediaVault/.venv/Scripts/python.exe -m pytest tests/smoke -q` → **58 passed in 20.75s** (was 56; +2 new tests). Well under the 30 s budget. Focused: `... -m pytest "...::TestEachCommand::test_web_collect_reclaimable" "...::TestAliasSweep::test_web_reclaim_alias" -v` → **2 passed in 1.86s**. `fastapi 0.138.0` confirmed installed (optional `create_app()` assertion exercised, not skipped). Only `tests/smoke/test_smoke_all_commands.py` modified — no core-file change, and the full smoke suite (the smoke gate itself) is green.

## Step 8 — [status: done]
- Executor: executor-sonnet · single-executor
- Files: `requirements.txt`
- Outcome: Truth-up `requirements.txt` (bundled IMP-A10): added `fastapi`, `uvicorn[standard]`, `requests`, `webdriver-manager`; kept `undetected-chromedriver` with the exact reserved-comment from `improvements_tierA.md:200`; added `httpx  # test: required by fastapi.testclient.TestClient` (one dep beyond A10's list — the endpoint tests `importorskip("httpx")`). Import check `import fastapi, uvicorn, httpx, requests` → ok. Commit `4fa2d2f`.

## Step 9 — [status: done] (executed INLINE by the orchestrator/architect after 3 spend-limit interruptions on sub-agents)
- Files: `README.md`, `ARCHITECTURE.md`, `improvements/improvements_tierE.md`, `improvements/improvements_tierA.md`, `improvements/improvements_tierD.md`, `improvements/PRIORITY.md`, `docs/priority-graph/priority-graph.html`.
- Docs: README — added the `web` row to the CLI table + a "Web operations console" subsection (Disk Reclaim view, badges, deps). ARCHITECTURE — added `web` to the §5 subcommand table, a new "`python main.py web`" subsection (webui/ package + single-worker queue + the 5 read-only data-functions + 4 badges + "calls cmd_* unchanged → rollback gate not tripped, ENTRY_TYPE_KEYS unchanged"), and added `collect_reclaimable` to the §6.3 alias-skip list.
- Bookkeeping (all three surfaces AGREE): IMP-E12 → done (+ reconciled the stale `mvweb.py`/poster-grid text to the shipped `webui/` package + `/api/*` API; added the card-grid-as-future-media-UI-substrate follow-up note → posters/titles=D10/E3, fetch-in-UI+daemon=S2). IMP-A10 → done. IMP-D16 (`scan_reclaimable`) → ADDED (done). IMP-D1 → advanced (reclaimable-GB slice delivered; full dashboard still pending; NOT marked done). PRIORITY.md: Last-updated→2026-06-22, E12/A10/D16 in DONE list (count 20→23), A10 row removed from Band 1, E12 marked ✅ in Band 4, 👉 NEXT repointed A10→**A12 (CI)** (S1 noted as the zero-code parallel win). priority-graph.html: E12 + A10 nodes → `priority="done"`/`status="done"`; added `["D16",...,"done","done",...]` node; added `["E12","D16"]` edge (kept `["A4","E12"]`); `⚡ Next` banner repointed A10→A12. Did NOT touch IMP-C18.

## FINAL VERIFICATION (Phase 3)
- `python -m pytest -q` (full suite) → **286 passed, 1 warning** (benign Starlette/httpx TestClient deprecation).
- `python -m pytest tests/smoke -q` (LAST gate — main.py touched) → **58 passed in ~11s** (< 30 s).
- All 9 steps done (3 multi-candidate bake-offs: step1=A disk-first, step3=C single-worker-queue, step6=B card-grid [user pick over judge's A]). ENTRY_TYPE_KEYS untouched; auto-rollback change-gate NOT tripped. Ready for PR to main (Checkpoint C1 — human-gated).

## IMP-D4 — Complete integrity guard for `cmd_restore` — [status: done]
- Executor: executor-haiku
- Model: haiku
- Files changed: `main.py` (split-restore success path only)
- Outcome: Added the warn-only post-condition `_warn_if_entry_inconsistent()` call to `cmd_restore`'s split-restore success path (after `journal.commit()` at line 2552, before cleanup/return). The standard-restore path was NOT modified because it does not use a journal (it calls `save_library()` directly without a rollback journal, and the task spec is to add the warning AFTER `journal.commit()`). The split-restore path is where status mutation via a journal happens, matching the pattern from `cmd_push` (2 success paths) and `cmd_replace` (1 success path), both of which already have the guard. This completes IMP-D4 (post-commit observability of library↔disk status consistency via `_warn_if_entry_inconsistent`, warn-only, no control-flow impact).
- Key decisions: Only the split-restore path (journal-based) gets the warning because the task explicitly states "AFTER `journal.commit()`". The standard-restore path does not have a journal and therefore does not qualify per the strict instruction. This aligns with the pattern in cmd_push and cmd_replace where every journal.commit() success path has the warning.
- Verification:
  - Exact inserted lines (2 lines after `journal.commit()` at line 2552):
    ```
    2553	            # Warn-only post-condition (IMP-D4). Post-commit; does NOT affect rollback/PONR.
    2554	            _warn_if_entry_inconsistent(library[manual_id], manual_id)
    ```
  - Context (lines 2551-2555):
    ```
    2551	            journal.mark_point_of_no_return()
    2552	            journal.commit()
    2553	            # Warn-only post-condition (IMP-D4). Post-commit; does NOT affect rollback/PONR.
    2554	            _warn_if_entry_inconsistent(library[manual_id], manual_id)
    2555	            print("   > 🧹 Cleaning up chunks...")
    ```
  - Test results:
    - `python -m pytest tests/test_verify_library.py tests/test_rehash.py tests/test_baseline_happy_path.py -q` → **31 passed in 19.19s**
    - `python -m pytest tests/smoke -q` → **58 passed in 21.17s**

## IMP-D4 follow-up — Add integrity guard to non-split restore path — [status: done]
- Executor: executor-haiku
- Model: haiku
- Files changed: `main.py` (non-split-restore success path only)
- Outcome: Added the warn-only post-condition `_warn_if_entry_inconsistent()` call to `cmd_restore`'s non-split (standard) success path. The hook was inserted after `save_library(library)` at line 2611 and before `return True` at line 2615, matching the indentation (8 spaces) of the surrounding code. The split-restore path already had this guard (from the earlier IMP-D4 work at line 2554). Now both success paths in `cmd_restore` include the warn hook, ensuring consistent post-commit observability of library↔disk status consistency across all restore scenarios.
- Key decisions: Non-split path uses `save_library()` directly (no journal), so the hook is placed immediately after the save, before return. The two-line insertion matches the existing guard semantics and indentation. Split path was NOT re-touched (it already has the guard).
- Verification:
  - Exact inserted lines (lines 2612-2613, after `save_library(library)` at 2611):
    ```
    2612	        # Warn-only post-condition (IMP-D4). Post-commit; does NOT affect rollback/PONR.
    2613	        _warn_if_entry_inconsistent(library[manual_id], manual_id)
    ```
  - Context (lines 2610-2615):
    ```
    2610	        library[manual_id]["status"] = "restored_local"
    2611	        save_library(library)
    2612	        # Warn-only post-condition (IMP-D4). Post-commit; does NOT affect rollback/PONR.
    2613	        _warn_if_entry_inconsistent(library[manual_id], manual_id)
    2614	        print(f"✅ SUCCESS: {filename} restored.")
    2615	        return True
    ```
  - Test results:
    - `python -m pytest tests/test_verify_library.py tests/test_rehash.py tests/test_baseline_happy_path.py -q` → **31 passed in 37.66s**
    - `python -m pytest tests/smoke -q` → **58 passed in 27.95s**

## IMP-E14 (data-integrity hole) — close the cmd_prep clobber hole + add dangling detection — [status: done]
- Executor: executor-opus
- Model: opus
- Mode: single-executor (standalone data-integrity directive on branch `feature/imp_e14_fetch_in_ui`; NOT an enumerated PLAN.md step, so no PLAN checkbox was ticked)
- Files changed: `main.py`, `tests/test_verify_library.py`
- Outcome: Enforced the invariant "an entry that asserts a cloud copy must never auto-revert to local_ready/uploaded=False." (1) Tightened the `cmd_prep` early-skip guard (main.py:850) so it ALSO refuses to re-prep an entry whose status is `onboarded`/`archived`/`restored_local` (any cloud-bearing state), not just `uploaded==True`/`archived`. This closes the regression where `cmd_prep_push_rep_season` preps every episode before checking `uploaded` and could rebuild a cloud-bearing leaf to `local_ready/uploaded=False`, stranding the cloud copy (the battlestar/dark dangling-bug class). A genuinely-local entry (`local_ready` + falsy `uploaded` + real file) still preps normally. (2) Added an additive, read-only possibly-dangling DETECTION pass to `cmd_verify_library` via a new `_dangling_evidence()` helper: flags a leaf that looks local (`status=="local_ready"` or missing, `uploaded` falsy) yet shows cloud evidence — HIGH (`split_info`, OR a `checksums/` `.sha256` sidecar embedding the entry's `short_id`, OR an mvmeta sidecar referencing the id), LOW (`search_term`-only). Printed as a separate advisory + summary suffix; does NOT affect the True/False return (still driven solely by the status↔disk invariant).
- Key decisions:
  - Guard semantics unchanged (still early-skip → returns True, ZERO artifacts, never rolls back); only the skip SET widened. The rollback journal never records `uploaded`/`status`, so this is strictly more conservative and is NOT a change-gate change (confirmed against the auto-rollback change-gate).
  - Dangling detection folded into the EXISTING physical-leaf walk (single iteration) after the virtual-skip guards, so it is inherently alias/season_map-safe and adds no second pass.
  - HIGH `checksums/` matching keys on the entry's OWN `short_id` in the sidecar name, NOT mere presence of a `checksums/` dir. This is load-bearing: a shared season `checksums/` folder holds sidecars for many episodes; matching by short_id attributes evidence to the RIGHT episode (verified on real data — battlestar s01e11's sidecar embeds its short_id `044cc3` → HIGH, while e12/e13 whose short_ids are absent fall to LOW).
  - LOW (`search_term`-only) is intentionally noisy and low-confidence (cmd_prep sets `search_term` on every entry) — separated from HIGH per the task. A `# TODO future --reconcile-dangling` was added noting a future mutating command could `set_uploaded` the HIGH ones after Google-Photos confirmation.
  - Summary suffix ` | possibly_dangling: N (high=a, low=b)` is appended ONLY when N>0, preserving every existing exact-substring assertion (`"scanned X, OK Y, MISMATCH Z"`).
- Verification:
  - Before/after of the cmd_prep guard condition (main.py:850):
    ```
    -        if entry.get("uploaded") == True or entry.get("status") == "archived":
    +        if entry.get("uploaded") or entry.get("status") in ("onboarded", "archived", "restored_local"):
    ```
    Skip message changed to: `⏭️  Skipping Prep: {manual_id} (already pushed/archived — refusing to clobber cloud-bearing status to local_ready).`
  - New tests in `tests/test_verify_library.py`: `test_cmd_prep_refuses_to_clobber_cloud_bearing_status`, `test_cmd_prep_still_preps_a_genuine_local_entry`, `test_verify_library_flags_possibly_dangling_without_failing`, `test_verify_library_no_dangling_section_when_clean`, `test_verify_library_dangling_skips_uploaded_and_virtual`.
  - `python -m pytest tests/test_verify_library.py -q` → **12 passed**.
  - `python -m pytest tests/test_rehash.py tests/test_baseline_happy_path.py tests/test_rollback.py -q` → **34 passed** (prep/push/rollback regression intact).
  - `python -m pytest tests/smoke -q` → **58 passed** (main.py touched — smoke gate green).
  - `python -m pytest tests/test_entry_schema_guard.py -q` → **2 passed**; full suite `pytest -q` → **305 passed** (1 pre-existing unrelated FastAPI/httpx deprecation warning).
  - Real-library read-only `PYTHONUTF8=1 python main.py verify_library` → `scanned 657, OK 657, MISMATCH 0 | possibly_dangling: 6 (high=2, low=4)`. The 6 advisories are exactly the known danglers: `tv-en-2004-battlestargalactica-s01e11` [high], `tv-en-2017-dark-s01e10` [high], `mov-ta-2012-3` [low], `mov-ta-2013-soodhukavvum` [low], `tv-en-2004-battlestargalactica-s01e12` [low], `tv-en-2004-battlestargalactica-s01e13` [low]. (NOTE: the danglers are the 2004 SEASON 01 battlestar episodes — `local_ready/uploaded=False` with surviving cloud evidence — not the 2008 S04 ones, which are correctly `archived/uploaded=True`. The audit's "battlestar e11/e12/e13" referred to S01.) No mutation occurred (MISMATCH 0 → returns True; advisory is independent).

## Cursor-following card glow (P3 step 3.1 / Candidate-D "Spotlight follow") — [status: done]
- Executor: executor-opus
- Model: opus
- Mode: single-executor (user's direct polish request "where I hover the mouse inside a selectable box, behind the pointer it should glow", dispatched on branch `feature/imp_e14_fetch_in_ui`). This is a faithful slice of PLAN step 3.1 → **Candidate D (Spotlight follow: radial highlight following the cursor via `--mx/--my` on `pointermove`)**. Because it was run as a SINGLE-executor task and not the full `[candidates: 4]` 3.1 bake-off (3.2 skill + 3.3 PWA + 3.4 docs still pending), the `[ ] 3.1` PLAN checkbox was deliberately NOT ticked — that box is governed by the multi-candidate protocol and remains the orchestrator's to mark after judging.
- Files changed:
  - `webui/static/glow.js` — NEW (~110 lines): exports `wireCardGlow(container)`. Delegated `pointermove` listener on a stable container, rAF-coalesced one CSS-var write per frame, writes `--mx`/`--my` (px relative to the card) on the `.closest('.card')`. Skips entirely under `prefers-reduced-motion` and on non-hover/coarse-pointer (touch) devices; ignores `touch` pointer events; drops the queued frame on `pointerleave`/`pointercancel`.
  - `webui/static/app.js` — added `import { wireCardGlow } from "./glow.js";` and one call `wireCardGlow($("#panel"));` inside `init()`. No render-path changes.
  - `webui/static/styles.css` — added `isolation: isolate` to `.card` (forms a stacking context so the glow layer is contained) and a `.card::before` glow layer: `position:absolute; inset:0; z-index:-1; pointer-events:none; opacity:0; transition:opacity 0.2s` with `background: radial-gradient(circle at var(--mx,50%) var(--my,50%), rgba(56,224,200,0.16), rgba(56,224,200,0.06) 90px, transparent 160px)`. Reveal gated to `@media (hover:hover) and (pointer:fine)` (`.card:hover::before{opacity:1}`); a separate `@media (prefers-reduced-motion: reduce)` block replaces it with a STATIC centered highlight (no transition, fixed 50%/50% center).
- Outcome: A soft mint radial glow appears centered under the pointer and follows it within each card on desktop (hovering, fine pointer), fading in/out over 0.2s. It paints ABOVE the card's own gradient background but BELOW the in-flow poster/body and every positive-z overlay (`.fetch-ring` z:3, `.demo-tag` z:4, `.card-actions` z:5), so clicks/taps on buttons/inputs and the ⤢ expand arrow are never blocked (the layer is also `pointer-events:none`). Delegation on the persistent `#panel` (only its children are cleared on re-render) means no per-card listener and zero leak across the sort/tab/sub-view/post-job re-renders. The P2 fetch-ring is unaffected and never fights the glow (different layers).
- Key decisions:
  - **`::before` at `z-index:-1` + `isolation:isolate` on `.card`, NOT a `<div>` or a positive-z layer.** A glow must sit behind in-flow content (poster/body), which in CSS paint order sits between negative-z and zero-z positioned layers — so the layer MUST be negative-z, which requires the card to form a stacking context (else the negative child escapes behind the card and is invisible). `isolation:isolate` creates that context with zero layout/visual cost and preserves the existing children's relative z-order (3<4<5 still on top). Considered an appended `<div class="card-glow">` (the brief's other option) but rejected it: it would need either negative z (same stacking-context requirement) or it covers content at z:0+, AND it adds DOM churn + teardown wiring per re-render — the pseudo-element needs none.
  - **Delegated listener on `#panel` (the truly stable node), not on `.grid`.** `app.js` RE-CREATES the `.grid` div on every paint (`panel.textContent=""` then a fresh `createElement`), so `.grid` is NOT stable; `#panel` is created once in index.html and only child-cleared. Attaching once at `init()` to `#panel` is leak-proof and needs no teardown alongside the ring's `destroyRingsIn`.
  - **Touch graceful-degradation via the CSS `@media (hover:hover) and (pointer:fine)` opacity gate (primary) + ignoring `pointerType==='touch'` in JS (belt-and-suspenders).** On the user's primary devices (iPhone/iPad Safari, `hover:none`) the hover rule never matches, so a tap can't light the glow and nothing can stick on after a finger lifts — no layout shift, no blocked taps, no scroll interference. Pen + mouse (fine pointers) get the glow. This is cleaner than a JS-driven `pointerdown→pointerup` reset and avoids the mobile "sticky :hover" trap.
  - **Reduced-motion = static centered highlight (not nothing).** Honors "no motion" (no following, no fade — `transition:none`, fixed center) while keeping a gentle hover affordance; JS also skips registering the move listener entirely in this mode (no wasted work).
  - **rAF coalescing + `--mx/--my` fallback to `50%`** so it stays 60fps and never flashes at the 0,0 corner before the first move / on an un-tracked card.
- Verification:
  - `node --check` on every `webui/static/*.js` (app.js, card.js, data.js, glow.js, modal.js, ring.js, sort.js, terminal.js, title.js) → all OK.
  - TestClient static+endpoints: `['/','/app.js','/card.js','/styles.css','/api/items','/api/mode']` → all **200**. Also confirmed `/glow.js` → **200** (`text/javascript`), `./glow.js` import present in served `/app.js`, and `isolation: isolate` + `.card::before` present in served `/styles.css`.
  - `.venv\Scripts\python.exe -m pytest tests/test_web_endpoints.py -q` → **5 passed, 1 warning** (pre-existing Starlette/httpx TestClient deprecation, unrelated).
  - Smoke gate NOT required: frontend-only change under `webui/static/`; `main.py`/`mainfetch.py`/`mvcommon.py` untouched.

## Step 4.5 — Tests for the web token auth + non-localhost startup guard — [status: done]
- Executor: executor-opus
- Model: opus
- Mode: single-executor
- Files changed: `tests/test_web_auth.py` (NEW — only file touched). `PLAN.md` step 4.5 marked `[x]`.
- Outcome: Added 6 tests proving the IMP-E15 auth contract end-to-end against the SHIPPED code (`webui/server.py` `_api_token_guard` middleware + `_request_has_valid_token`, and `main.cmd_web`'s startup guard / token auto-open). All auth is driven by monkeypatching `mvcommon.web_token` (the request-time getter both the middleware and `cmd_web` read) — NO real `mvconfig.json` is written. `uvicorn.run` and `main.webbrowser.open` are stubbed in the startup-guard test so nothing real launches. Sandbox fixtures (`sandbox_entry`) back `/api/items`. Tests + functions + key assertions:
  - `test_no_token_means_auth_off` — `web_token()→""`: `GET /api/items`→200 with NO creds; `POST /api/action/sort`→202→`done`; the sentinel `cmd_sort` ran exactly once (auth-off must not block actions). Proves the frictionless default the whole suite relies on.
  - `test_token_gates_api_reads` — `web_token()→"secret123"`: `GET /api/items` no-creds→401 + body `{"detail":"Token required"}`; accepted via `X-MediaVault-Token` header (200), via raw `Cookie: mv_token=…` header (200), via `?token=…` query (200); WRONG header token→401.
  - `test_token_gates_api_actions` — `POST /api/action/sort` no-token→401 AND the sentinel `cmd_sort` never ran (rejected at middleware before the route); with the header→202→`done`, sentinel ran once.
  - `test_static_shell_unauthenticated_when_token_set` — token set: `GET /`→200 and `GET /app.js`→200 with NO creds (only `/api/*` is gated; the SPA shell must load to prompt for the token).
  - `test_open_folder_localhost_rule_survives_auth` — token set AND presented (header), `POST /api/open-folder` from the TestClient (client host `"testclient"`, non-local)→403. Proves the localhost rule fires AFTER the token check (the token does not widen access).
  - `test_cmd_web_startup_guard` — (a) `cmd_web(host="0.0.0.0")` + no token → `uvicorn.run` NEVER called + "Refusing to start" printed (capsys); (b) `cmd_web(host="127.0.0.1")` + no token → proceeds (sentinel `uvicorn.run` called); (c) `cmd_web(host="0.0.0.0")` + token → proceeds AND auto-opens local browser at a URL containing `?token=secret123`.
- Key decisions:
  - **Monkeypatch point = `mvcommon.web_token` (the getter), per the constraint "do NOT write a real mvconfig.json".** Binding-safe: the middleware calls `mvcommon.web_token()` (attribute lookup at request time, server.py:670) and `cmd_web` calls `mvcommon.web_token()` (main.py:4189) — both honour a `setattr(mvcommon, "web_token", …)` patch. NOT a `from mvcommon import web_token` by-value binding, so a single patch covers both readers.
  - **`uvicorn.run` patched on the real `uvicorn` module** (`import uvicorn; monkeypatch.setattr(uvicorn,"run",…)`). `cmd_web` does `import uvicorn` INSIDE the function (main.py:4204); uvicorn 0.49.0 is installed, so the in-function import returns the same module singleton and sees the patch. The real `create_app()` is left to run (it is import-safe, no network — same as the endpoint tests build it repeatedly); only `uvicorn.run` is short-circuited.
  - **host AND port passed explicitly** in the startup-guard test so `cmd_web`'s resolver never falls back to reading a real `mvconfig.json` (it only calls `web_host()/web_port()` when the arg is None).
  - **Test 1/2/4 explicitly set `web_token()→""` or `→"secret123"`** rather than relying on the ambient default — deterministic regardless of any real `mvconfig.json` on the box, and honours the "monkeypatch the getter" constraint.
  - **Cookie carrier sent as a raw `Cookie:` header**, not the per-request `cookies=` kwarg (which httpx 0.28.1 deprecates with an ambiguous-persistence warning). Exercises the identical `request.cookies.get("mv_token")` server path, warning-free.
  - **TestClient client host is `"testclient"`** (verified by probe), which is NOT in server.py's `_LOCALHOST_HOSTS`, so `/api/open-folder` 403s from the TestClient — exactly the condition test 4 needs.
- ⚠️ DIVERGENCE TO RECONCILE IN STEP 4.6 (PLAN.md prose is STALE vs the shipped code): PLAN.md step 4.5 *Details* (line 400) and its one-line summary ("localhost-exempt; remote-required; read-flag behavior") describe an OLDER, REJECTED auth design — a localhost-EXEMPT `/api/*` middleware (case c: "localhost client without header → still works (exemption)") and a `require_token_for_reads=True/False` knob (case d). **The actually-shipped code does the OPPOSITE on both points, by deliberate design:** (1) there is INTENTIONALLY NO localhost exemption on the `/api/*` token middleware — server.py:660-666 documents that `tailscale serve` proxies remote peers to 127.0.0.1, so a localhost exemption would let every tailnet visitor bypass the token; a localhost client without the token gets 401. (2) There is NO `require_token_for_reads` knob — when a token is set, ALL `/api/*` (reads AND actions) are gated unconditionally; the localhost rule survives ONLY on `/api/open-folder`, where it merely narrows access. I implemented the tests against the ORCHESTRATOR'S PROMPT and the SHIPPED code (the authoritative, current contract — verified by reading server.py/main.py), NOT the stale PLAN prose; writing tests asserting a localhost exemption or a read-flag would codify a rejected design and contradict the running code. ACTION for 4.6 (the "Architect + docs" step): update PLAN.md line 398/400 prose and any IMP-E15 doc to the no-exemption, token-gates-everything contract.
- Verification:
  - `.venv\Scripts\python.exe -m pytest tests/test_web_auth.py -q` → **6 passed, 1 warning** (the pre-existing module-wide Starlette/httpx TestClient deprecation, unrelated to this change).
  - `.venv\Scripts\python.exe -m pytest tests/test_web_endpoints.py tests/test_web_demo.py tests/smoke -q` → **74 passed, 1 warning** (no regression; the no-token default keeps endpoints/demo/smoke frictionless). Smoke suite green — though the smoke-gate is not strictly triggered (this step adds a test file only; `main.py`/`mainfetch.py`/`mvcommon.py` untouched).
  - Full `.venv\Scripts\python.exe -m pytest tests/ -q` → **352 passed, 1 warning** (broader regression-safe; registering the new module changed nothing else).
  - `git status --porcelain` confirms the ONLY file I created is `tests/test_web_auth.py` (other modified/untracked files are prior IMP-E15 steps — not touched here).

## Step 5.1 — [status: done]
- Executor: executor-opus
- Model: opus
- Mode: single-executor
- Files changed: `main.py` (new `cmd_set_tmdb` + argv arm + usage line), `tests/test_set_tmdb.py` (new), `PLAN.md` (5.1 → [x])
- Outcome: Added `cmd_set_tmdb(manual_id, tmdb_id)` — a pure ZERO-BYTE JSON edit mirroring `cmd_set_search` (load_library → missing-id guard → mutate → save_library; no media touch, NO rehash, no file touch). It sets the OPTIONAL `metadata.tmdb_id` leaf sub-field via `entry.setdefault("metadata", {})["tmdb_id"] = value`, coercing an all-digits string to int and storing any other string verbatim (leniency). Routed `set_tmdb <id> <tmdb_id>` through the argv chain (right after `set_fanart`, same `len(sys.argv) >= 4` validation + usage style) and added the `set_tmdb [id] [tmdb_id]` line to the help block. Verified end-to-end (focused tests + smoke + a TestClient-free `python -c` before/after demo on an isolated temp library): on a seeded leaf the id lands as an int under `metadata.tmdb_id` and persists through a reload, the media bytes and stored `hash` are unchanged, and a missing id prints `❌ ID not found.` without crashing or mutating the library.
- Key decisions:
  - **ENTRY_TYPE_KEYS / guard — NO CHANGE (decision + why).** I read `tests/test_entry_schema_guard.py`: both tests key ONLY on each type's TOP-LEVEL `required` key set and the `type` discriminator (round-trip asserts `spec["required"] - set(after)` at line 142; the guard exercises whole-library iterator safety on non-physical types). Neither asserts on `metadata` SUB-fields. `metadata.tmdb_id` is an additive, optional sub-field of the optional `metadata` dict — it is NOT a top-level key and NOT in any `required` set. Adding it to `ENTRY_TYPE_KEYS` would be wrong (the registry tracks top-level entry shapes, not metadata internals). PLAN.md line 525 anticipated exactly this ("currently keys on TOP-LEVEL required keys only … likely needs no change — CONFIRM by reading the test"); confirmed. `pytest tests/test_entry_schema_guard.py -q` stays green untouched.
  - **Alias/season_map shape-safety via `_resolve_alias` (chose the FIRST sanctioned option, not the sibling's behavior).** The siblings (`set_search`/`set_poster`/`set_fanart`) target `library[manual_id]` directly and do NOT resolve aliases — `set_poster`/`set_fanart` would in fact KeyError on a `multi_ep_alias` (they deref `entry["folder_path"]`). The task offered two sanctioned options; I chose the more robust one: resolve a `multi_ep_alias` one hop to its primary leaf via the existing `_resolve_alias`, so `set_tmdb <alias_id>` sets `tmdb_id` on the primary leaf (where metadata belongs) and the alias's exact 3-key shape `{type, alias_of, parent_id}` is NEVER mutated. A `season_map` is a virtual container (`_resolve_alias` returns it unchanged), so I detect `type == "season_map"` and REFUSE with a clear "targets a leaf entry, not a season_map container" message rather than adding a `metadata` key to the container — its shape is preserved. This strictly improves on the siblings (no crash) while honoring "Do NOT alter season_map/multi_ep_alias SHAPES." Tests assert both: alias → primary leaf with alias shape intact; season_map refused with shape intact.
  - **Value coercion:** `int(tmdb_id) if str(tmdb_id).isdigit() else tmdb_id`. TMDB ids are integers (stored as int), but a non-digit value (e.g. an IMDB `tt…` id) is stored as-is rather than crashing — matches the task's "be lenient" directive. `str(...).isdigit()` tolerates an already-int argument too.
  - **No docs touched** (architect step 5.9 owns ARCHITECTURE §5 / README). Left only in-code comments on `cmd_set_tmdb` and the new argv arm/help line.
- Verification:
  - `.venv\Scripts\python.exe -m pytest tests/test_set_tmdb.py tests/test_entry_schema_guard.py -q` → **10 passed in 0.75s** (8 set_tmdb + 2 guard).
  - `.venv\Scripts\python.exe -m pytest tests/smoke -q` → **61 passed, 1 warning** (smoke-gate MANDATORY — main.py touched; the lone warning is the pre-existing Starlette/httpx TestClient deprecation, unrelated).
  - Functional `python -c` (isolated temp library, dual-patched `mvcommon`/`main` LIBRARY_* — never real C:\Media): BEFORE `metadata = {"title":"Inception","year":2010}`, hash matches file; AFTER `cmd_set_tmdb(..., "27205")` → `metadata = {"title":"Inception","year":2010,"tmdb_id":27205}` (tmdb_id type = int), `hash` UNCHANGED and still matches the file bytes (no rehash); `cmd_set_tmdb("mov-DOES-NOT-EXIST", "999")` → `❌ ID not found.` (no crash).

## Step 5.3 — [status: done] — crash-safe cascading `rename_folder` (IMP-D17)
- Executor: executor-opus
- Model: opus
- Mode: single-executor
- Files changed: `main.py` (new `cmd_rename_folder` + helpers `_norm_path`/`_is_under`/`_collect_folder_descendants`/`_rewrite_folder_path`, the `rename_folder` argv arm, one help line); `tests/test_rename_folder.py` (new, 8 tests). NO docs/ARCHITECTURE/README touched (step 5.9 owns those).
- Outcome: Added a crash-safe cascading folder rename. It resolves the target by id (→ its `folder_path`) or by a plain on-disk path, computes new = same parent + the new leaf name, scans the WHOLE library for descendants (entries whose `folder_path` IS the old folder OR nested under it), atomically renames the directory on disk, and rewrites `folder_path` for the season_map container + every episode leaf. Hash-safe (paths only — never re-hashes/splits/uploads; the entry `hash` byte string is provably unchanged in test (b) and the manual run). Works on ARCHIVED dummy folders (no special-casing; the dummy + `uid`/`.sha256` sidecars move with the directory). Alias-safe: `multi_ep_alias` entries (no `folder_path`) are skipped (verified the key stays absent). Verified end-to-end via a manual temp-sandbox CLI run.
- Key decisions:
  - **AUTO-ROLLBACK CHANGE-GATE — NOT tripped (additive only).** Reused ONLY the existing journal API: `RollbackJournal(...).record_set_field(...)`, `.mark_point_of_no_return()`, `.commit()`, `.rollback(library)`, `.crossed_ponr`, and `RollbackHardFail`. Invented ZERO new record `op` types (each descendant's folder_path rewrite is a standard `set_field` record with `existed=True, prior=<old>` — the same vocabulary `cmd_prep` uses for `split_info`; its inverse restores the prior path). Did NOT modify the journal format, `fsync`+`os.replace` durability, `_replay_inverses`, `recover_journal()`, `TXN_JOURNAL_NAME`, the PONR/`mark_point_of_no_return` semantics, the `RollbackHardFail` contract, or the wrapping of `cmd_prep`/`push`/`replace`/`restore`. `git diff main.py` = 221 insertions, 0 deletions, all in the new function + argv arm + help line (the journal classes at ~593-832 and cmd_replace/restore/prep are byte-untouched).
  - **Journal lives in the PARENT directory of the target folder (the key design choice).** Because the command renames the directory ITSELF, a journal placed inside the old folder would be carried to the new path by `os.rename`, leaving `commit()`/`_delete()` (which target the old path) unable to clean it up AND making any post-rename `_flush` (e.g. `mark_point_of_no_return`) write into a now-missing directory. The parent dir does not move, so every existing journal method and a later `recover_journal(<parent>)` stay valid across the rename. This changes only WHICH folder holds this command's journal — a per-command choice `cmd_replace`/`cmd_restore` already make independently — NOT the journal contract. Consequence (documented in the docstring): `recover <id>` (which resolves the id → its folder_path) won't auto-find a parent-dir journal; use `recover --scan` (which walks all dirs and lists it) + `recover "<parent dir>"`, or re-run `rename_folder` to self-heal.
  - **PONR = the `os.rename(old → new)`, mirroring cmd_replace's commit rename.** Records are written BEFORE acting; `mark_point_of_no_return()` fires immediately after a successful rename. A failure BEFORE the rename → `journal.rollback(library)` (set_field inverses restore old paths; folder never moved; journal deleted — fully reversible, exercised by `test_pre_ponr_failure_rolls_back`). A failure AT/AFTER the rename → the crossed journal is left on disk and a `RollbackHardFail` is raised naming an EXISTING resume form (`rename_folder "<new folder>" "<new name>"`), since `recover_journal` deliberately will NOT auto-undo a crossed journal.
  - **Forward self-heal for the irreducible torn window (rename committed, save not yet done) — mirrors cmd_replace's C9 stale-sweep.** There is exactly one unavoidable torn window between the two independent durable ops (the directory rename and the library JSON save); they cannot be made atomic together. A stale-sweep at the top of the command detects "OLD folder gone, NEW exists, descendants still point at OLD" and finishes the JSON rewrite + drops the leftover journal. So a post-PONR crash is genuinely recoverable: `recover_journal(parent)` correctly declines (crossed) and leaves the folder put; a re-run of `rename_folder` self-completes. Exercised end-to-end by `test_post_ponr_failure_is_recoverable` (injects `save_library` raising post-rename, like test_rollback.py's hard-kill test).
  - **Path matching is case/separator-insensitive (Windows) with a sibling guard.** `_is_under` requires `child == parent` OR `child.startswith(parent + os.sep)` so a sibling `…\Dark2` never matches the prefix `…\Dark`. `_rewrite_folder_path` uses `os.path.relpath` for nested subfolders, which matches the common prefix case-insensitively but RETURNS the tail with its ORIGINAL casing (verified: `Season 01`, not `season 01`), so a subfolder's real name is preserved. New name must be a bare leaf (rejects a path separator) — a name with a separator would relocate the folder elsewhere (out of scope, footgun).
- Verification:
  - `.venv\Scripts\python.exe -m pytest tests/test_rename_folder.py -q` → **8 passed in 1.17s** (covers a: season_map+leaves rewrite & id/path resolution; b: archived-dummy hash-safe + sidecar moves; c: post-PONR failure recoverable via recover + self-heal; pre-PONR rollback; d: multi_ep_alias untouched (3-key shape preserved); e: refuses existing target + unknown id).
  - `.venv\Scripts\python.exe -m pytest -k "rollback or recover" -q` → **20 passed, 374 deselected** (existing journal/recover machinery unaffected).
  - `.venv\Scripts\python.exe -m pytest tests/smoke -q` → **61 passed, 1 warning** (MANDATORY smoke-gate — main.py touched; the warning is the pre-existing Starlette/httpx TestClient deprecation, unrelated).
  - `.venv\Scripts\python.exe -m pytest -q` → **394 passed, 1 warning** (full suite green).
  - Manual end-to-end CLI run against an isolated temp library (dual-patched `mvcommon`/`main` LIBRARY_* + LOCAL_ROOT — never real C:\Media): `rename_folder tv-en-2017-dark-s01 "Season 01 {tmdb-70523}"` → folder renamed on disk, season_map + leaf re-pointed, alias has no folder_path, leaf `hash` UNCHANGED, dummy `.mkv` + `uid` sidecar both moved.

## Step 5.7 — [status: done]
- Executor: executor-opus
- Model: opus
- Mode: single-executor
- Files changed: `main.py` (cmd_enrich_metadata APPLY branch + new `_title_is_id_shaped` helper; `items_payload` poster_available), `webui/static/card.js` (gate `addPosterImage` + `.has-poster` scrim toggle), `webui/static/styles.css` (poster-img polish + scrim + fade), `tests/test_web_items.py` (+3 tests), `tests/test_enrich_metadata.py` (+1 test, +3 asserts).
- Outcome: Real TMDB titles + posters now surface in the web UI end-to-end. (a) On a CONFIDENT `--apply` match, enrich now writes `metadata.title` = the TMDB title and `metadata.year` = the TMDB year onto each resolved entry (alongside the existing `tmdb_id`), but ONLY when the stored title is still id-shaped/placeholder (blank, == the entry id, or `mov-/tv-/ani-`-prefixed) or already equals the TMDB title — a genuinely human-curated title is preserved. (b) `items_payload` flipped its P1 placeholder `poster_available=False` to a real per-row existence check via `resolve_artwork_path(library, mid, "poster")` (the SAME resolver `/api/media-image` uses), short-circuiting to False when an entry has neither `folder_path` nor `parent_id`; `tmdb_id`/`title`/`year` were already sourced from `metadata`. (c) The SPA `addPosterImage` now only creates/requests the poster `<img>` when `item.poster_available` is true (no speculative 404 per card), keeps the on-error gradient fallback, and adds a bottom scrim (via a `.has-poster` class, no `:has()`) plus a shift-free fade-in. `webui/server.py` and `app.js` needed NO change — `/api/items` already returns `items_payload()` verbatim and data.js already carries `title`/`poster_available` into the card item, so `displayTitle(item)` shows the real title automatically. Both decluttered and grouped/tree cards share `buildCard`, so gating + title apply in both views.
- Key decisions:
  - **Title-replace rule** lives in a small pure helper `_title_is_id_shaped(title, entry_id)` (next to `_enrich_title_year`) so the "only fill placeholder titles" policy is one testable predicate. Considered overwriting unconditionally (rejected — clobbers user curation) and matching against `parse_metadata_from_id` (rejected — `parse_metadata_from_id` just sets `title=id`, so the id-equality + category-prefix check captures every placeholder the codebase produces, including a leaf id stored on a season_map). A title already equal to the incoming TMDB title is treated as an idempotent refresh (re-runs are no-ops).
  - **`year` is refreshed unconditionally** from the confident match (not just filled-when-absent): the id year is often a later season's air year, so the matched show/movie year is authoritative. This only runs on a confident match (never a guess).
  - **`poster_available` is a LIVE disk check, not a cached flag** — deleting a poster flips it False next call (test asserts this). Cost is a few `os.path.isfile`/`realpath` checks per row (acceptable per the step note for a ~570-row grid); short-circuited to False for folderless leaves so the resolver isn't even entered.
  - **Scrim uses a `.has-poster` class** card.js toggles on img load/error, NOT the CSS `:has()` selector — `:has()` isn't used anywhere else in this codebase, so a class keeps the scrim deterministic and browser-portable. The badge already has its own dark background, so the scrim is cosmetic-only.
  - Did NOT edit `server.py`/`app.js` despite the step listing them as touchable — both already pass the data through unchanged, so editing them would violate surgical-change discipline.
- Verification:
  - `.venv\Scripts\python.exe -m pytest tests/test_web_items.py tests/test_enrich_metadata.py -q` → **25 passed, 1 warning** (the warning is the pre-existing Starlette/httpx TestClient deprecation, unrelated).
  - `node --check webui/static/card.js` → OK; `node --check webui/static/app.js` → OK (only card.js changed of the two; app.js re-checked as it's in the touch list).
  - `.venv\Scripts\python.exe -m pytest tests/smoke -q` → **61 passed, 1 warning** (MANDATORY smoke-gate — main.py + server.py touched).
  - `.venv\Scripts\python.exe -m pytest tests/test_web_media_image.py tests/test_web_tree.py -q` → **44 passed** (regression check: the resolver reuse + tree/leaf card path are unaffected).
