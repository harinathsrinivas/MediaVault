# Execution Log

Task: IMP-A1 — Extract shared library I/O + hashing into mvcommon.py
Branch: refactor/extract_mvcommon (from origin/main @ 8c12680)
Baseline (pre-change): pytest tests/test_cmd_replace.py tests/test_cmd_restore_quarantine.py -q -> 15 passed.
Note: this STATUS.md is an orchestration scratchpad and is intentionally NOT committed (not in the A1 plan file list).

## Step 1 — [status: done]
- Executor: orchestrator (direct; subagent Task tool unavailable in this run)
- Model: sonnet
- Files changed: mvcommon.py (new)
- Outcome: Created mvcommon.py at repo root with the 9 shared constants (LIBRARY_MOVIES/SERIES/ANIME, LOCAL_ROOT, MKVMERGE_PATH, SPLIT_DIR_NAME, CHECKSUM_DIR_NAME, RESTORE_DIR_NAME, VIDEO_EXTENSIONS) and 6 helpers. Stdlib-only imports (os, json, sys, hashlib, re, tempfile). load_library (loud sys.exit(1)), save_library (atomic), generate_short_id, human_readable_size, parse_size_str copied verbatim from main.py. calculate_file_hash rewritten with the live █/░ progress bar per the exact spec.
- Key decision: PARTIAL_SUFFIX/MVMETA_SUFFIX (added on main by G1 work) are push-side and left in main.py — not in the move set.
- Acceptance: python -c "import mvcommon; print(...)" printed all seven symbols. PASS.

## Step 2 — [status: done]
- Executor: orchestrator (direct)
- Model: sonnet
- Files changed: main.py
- Outcome: Removed the 9 shared constant defs and the 6 moved helper defs; added a single `from mvcommon import (...)` near the config block. Kept REMOTE_ROOT, FFMPEG_PATH, DUMMY_*, MAINFETCH_SCRIPT, DEVICE_ALIASES, PARTIAL_SUFFIX, MVMETA_SUFFIX, resolve_device, get_tech_specs, all cmd_*. Removed orphaned `import hashlib` (only the deleted helpers used it). Kept `import tempfile` (still used at line 608 by G1 mvmeta sidecar) and `import re`/`json` (still used).
- Acceptance: `import main` clean; `main.load_library.__module__ == mvcommon`; grep shows zero moved defs in main.py; `python main.py local_status` printed the same pending list as before (read-only, exercises load_library + human_readable_size). PASS.

## Step 3 — [status: done]
- Executor: orchestrator (direct)
- Model: sonnet
- Files changed: mainfetch.py
- Outcome: Deleted mainfetch's silent load_library and its calculate_file_hash + the 9 shared constants; added `from mvcommon import RESTORE_DIR_NAME, load_library, calculate_file_hash`. mainfetch now uses the loud load_library contract (intentional behavior change). Removed orphaned `import hashlib` AND `import json` (both were used only by the deleted helpers; verified zero other uses). Kept CHROME_PROFILES, CHROME_PROFILE_NAME, SYSTEM_DOWNLOADS_FOLDER, Selenium block, all fetch logic.
- Key decision: imported only the symbols mainfetch actually references (RESTORE_DIR_NAME at lines ~172/~349 + the 2 functions). The other 8 constants were dead in mainfetch even before this refactor, so re-importing them would just re-create dead bindings — kept the import line honest per plan's "import only the referenced ones" guidance and Open Decision 3. save_library/generate_short_id/human_readable_size/parse_size_str NOT imported (unused).
- Acceptance: `import mainfetch` clean; load_library/calculate_file_hash resolve to mvcommon; grep shows zero defs; `python mainfetch.py` prints usage with no NameError (rc 0). PASS.

## Step 4 — [status: done]
- Executor: orchestrator (direct)
- Model: opus
- Files changed: tests/conftest.py
- Outcome: Added `import mvcommon`; the sandbox fixture now monkeypatches BOTH `mvcommon.LIBRARY_*` (authoritative — load_library/save_library read mvcommon's own bindings) AND `main.LIBRARY_*` (the by-value copy). Kept the `"C:\\Media" not in path` hard guard.
- Key decision: DEMONSTRATED the binding hazard first — running C9/C11 with the old conftest (patching only main.LIBRARY_*) produced 12 failures ("ID not found") because mvcommon's bindings still pointed at real C:\Media and cmd_* couldn't see the sandbox entry. Patching mvcommon fixed it. mainfetch.LIBRARY_* not patched (no test touches fetch I/O).
- Acceptance: `pytest tests/test_cmd_replace.py tests/test_cmd_restore_quarantine.py -q` -> 15 passed, identical to the pre-change baseline. No real C:\Media path touched (hard guard active).

## Step 5 — [status: done]
- Executor: orchestrator (direct)
- Model: sonnet
- Files changed: tests/test_mvcommon.py (new)
- Outcome: 3 tests on the `sandbox` fixture (now patching mvcommon). Test A round-trip: save_library splits one mov-/tv-/ani- entry into the three sandbox JSONs by prefix, load_library merges back to an equal dict. Test B atomic save: monkeypatch mvcommon.os.replace to raise -> save_library re-raises OSError, leaves no .tmp orphan in lib dir, pre-existing target unchanged. Test C corrupt library: invalid JSON in lib_movies -> load_library raises SystemExit (codifies the unified loud contract).
- Acceptance: `pytest tests/test_mvcommon.py -q` -> 3 passed. Full `pytest -q` -> 23 passed, 0 failures.

## Step 6 — [status: done]
- Executor: orchestrator (direct)
- Model: haiku
- Files changed: improvements_tierA.md, ARCHITECTURE.md, README.md
- Outcome: improvements_tierA.md IMP-A1 Status flipped pending -> done (refactor/extract_mvcommon, PR to main 2026-05-30). ARCHITECTURE.md: added mvcommon.py to the §3 layout block, a note after §7.2 (six helpers + constants now in mvcommon, both load_library loud, progress bar added), and a note in §14 (shared constants now defined once in mvcommon). README.md: added mvcommon.py to the file-tree.
- Key decision: surgical additive edits only — historical line numbers in the §7.2/§14 tables left intact with a note that they predate the extraction (avoids churning the whole tables).
- Acceptance: grep IMP-A1 -> Status: done; ARCHITECTURE mvcommon mentions = 7; README = 1; git diff is small/additive.

## Step 7 — [status: done]
- Executor: orchestrator (direct)
- Model: haiku
- Files changed: docs/feature-auto-rollback/A1-extract-mvcommon/A1-extract-mvcommon.md; docs/.../A1-extract-mvcommon/PLAN.md (committed as artifact)
- Outcome: Filled the completion report (files changed, tests, manual commands all green, 5 resolved decisions, the conftest binding-location finding, the parked G1 stash note). Front-matter status -> done; DoD boxes ticked except "PR opened" (left for after push). Root PLAN.md confirmed gitignored + untouched (in-flight G1 copy).
- Acceptance: report filled with concrete commands/results; root PLAN.md untouched (git check-ignore = PLAN.md, no diff).
