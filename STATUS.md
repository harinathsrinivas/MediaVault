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
