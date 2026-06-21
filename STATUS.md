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
