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
