---
title: "A1 — Extract mvcommon.py"
type: prerequisite-task
improvement: IMP-A1
tier: A
role: foundation
order: 6
status: done
branch: refactor/extract_mvcommon
feature: auto-rollback
tags: [claude, mediavault, foundation, tier/A, status/done]
created: 2026-05-28
---

# A1 — Extract `mvcommon.py`

> **At a glance:** Move shared constants + `load_library`/`save_library`/hash/id
> helpers into one `mvcommon.py` imported by both scripts, and fix the
> load_library error-handling drift. Foundation for auto-rollback's snapshot/
> restore, [[C2-adb-selenium-retry|C2]]'s helper, and [[A7-pytest-harness|A7]]'s imports.
> Related: [[RELATED_IMPROVEMENTS]] · [[_TRACKER]]

## Claude Code prompt (paste into a fresh session)

```
Use the planner agent.

Task: Plan the implementation of improvement IMP-A1 ("Extract shared module mvcommon.py") from improvements_tierA.md.

Read these FIRST, in this order, before planning:
1. improvements_tierA.md -> the IMP-A1 section. The spec for WHAT and WHY.
2. docs/feature-auto-rollback/README.md, then docs/feature-auto-rollback/RELATED_IMPROVEMENTS.md ("A1" subsection). A1 is a FOUNDATION for the upcoming auto-rollback feature (its snapshot/restore uses load_library/save_library) and for C2/A7. RELATED_IMPROVEMENTS explains the relationship.
3. ARCHITECTURE.md and the code: shared constants + helpers near the top of main.py (load_library, save_library, calculate_file_hash, generate_short_id, human_readable_size, parse_size_str) and their counterparts in mainfetch.py — note the asymmetric error handling: main.py's load_library fails LOUDLY on a corrupt library, mainfetch.py's fails SILENTLY (zero entries).

What to build: create mvcommon.py at the project root; move the shared constants and the listed helpers into it; make both main.py and mainfetch.py import from it (from mvcommon import ...). Unify the two load_library implementations onto one behavior (recommend loud/explicit) and eliminate the drift.

Constraints (full list in docs/feature-auto-rollback/README.md):
- Branch from origin/main. Suggested branch: refactor/extract_mvcommon.
- Pure refactor: runtime behavior IDENTICAL afterward, EXCEPT the deliberate unification of load_library error handling — call that out explicitly and confirm it with me (it changes mainfetch's silent-zero-entries behavior).
- Touch only main.py, mainfetch.py, the new mvcommon.py (+ tests); don't touch archive/. No new dependencies. Watch for import cycles.
- Tests in tests/, COPIES only — never touch real C:\Media files or real library_*.json; cover a load/save round-trip and corrupt-library handling through mvcommon.
- Leave the seam: this is where shared library I/O lives for auto-rollback, C2's retry helper, and A7's imports — keep the public surface clean and stable.

DOCUMENTATION: save your PLAN.md into docs/feature-auto-rollback/A1-extract-mvcommon/PLAN.md; keep artifacts there; fill the "Completion report" in docs/feature-auto-rollback/A1-extract-mvcommon/A1-extract-mvcommon.md when done. (Keep /PLAN.md at root in sync if needed.)

Pause and ask me about open decisions, at minimum: the exact set of symbols to move; how to resolve the load_library error-handling divergence (unify loud vs preserve each); whether any thin re-export shims are needed (recommend none — import directly).

Use Opus if the cross-file move looks risky; otherwise single-executor (it is a well-understood extraction).

Deliverables: PLAN.md only (no code, no branches) with steps + tests + verification + "Open Decisions". Note IMP-A1 is marked done in improvements_tierA.md on implementation, the architect updates ARCHITECTURE.md/README (module layout change), and I want branch name, PR to main, and manual test commands at the end.
```

## What & why
Shared constants/helpers are duplicated across `main.py` and `mainfetch.py`
(already one drift case in prod). A shared module removes the duplication and the
loud-vs-silent `load_library` divergence.

## Relationship to auto-rollback / seam to leave
Auto-rollback's snapshot/restore uses `load_library`/`save_library`; centralizing
them here is the foundation. Keep the public surface clean. Details:
[[RELATED_IMPROVEMENTS]] → A1.

## Definition of Done
- [x] Planner run; `PLAN.md` in this subfolder; open decisions confirmed
- [x] Branched `refactor/extract_mvcommon` off `origin/main`
- [x] `mvcommon.py` created; both scripts import from it; no import cycles
- [x] Behavior identical except the agreed `load_library` unification
- [x] Tests in `tests/` (copies only): round-trip + corrupt-library handling
- [x] Seam left: clean, stable public surface
- [x] `IMP-A1` marked done in `improvements_tierA.md`
- [x] `ARCHITECTURE.md` / `README` updated (module layout)
- [ ] PR to `main` opened (branch pushed; open the PR from the compare URL)
- [x] Completion report below filled in

## Completion report

- **Branch:** `refactor/extract_mvcommon` (branched from `origin/main` @ `8c12680`).
- **PR:** to `main` (open after push; see end-of-run summary for URL).
- **Merged commit:** pending merge (PR target `main`).
- **Files changed:**
  - `mvcommon.py` (NEW) — 9 shared constants + 6 helpers; stdlib-only imports.
  - `main.py` — removed the 9 moved constants and 6 moved helper defs; added
    `from mvcommon import (...)`; dropped orphaned `import hashlib`.
  - `mainfetch.py` — deleted its silent `load_library` + duplicate
    `calculate_file_hash` + shared constants; added
    `from mvcommon import RESTORE_DIR_NAME, load_library, calculate_file_hash`;
    dropped orphaned `import hashlib` and `import json`. Now uses the loud
    `load_library` contract.
  - `tests/conftest.py` — sandbox fixture now monkeypatches BOTH
    `mvcommon.LIBRARY_*` (authoritative) AND `main.LIBRARY_*`.
  - `tests/test_mvcommon.py` (NEW) — 3 tests.
  - `improvements_tierA.md`, `ARCHITECTURE.md`, `README.md` — docs.
- **Tests added:** `tests/test_mvcommon.py` — (A) save/load round-trip with
  prefix split, (B) atomic `save_library` failure (`os.replace` raises ->
  re-raise, no `.tmp` orphan, target unchanged), (C) corrupt-library loud
  failure (`SystemExit`). Full suite: 23 passed.
- **Manual test commands (all run, all green):**
  - `python -c "import mvcommon, main, mainfetch"` — all three import cleanly.
  - `python main.py local_status` — same pending list as a pre-change run.
  - `python mainfetch.py` — prints usage, no NameError (rc 0).
  - `pytest -q` — 23 passed.
  - `pytest tests/test_cmd_replace.py tests/test_cmd_restore_quarantine.py -q` —
    15 passed (matches pre-change baseline).
- **Open decisions resolved:**
  1. Symbol set — moved exactly the 9 constants + 6 helpers; nothing else.
     `PARTIAL_SUFFIX`/`MVMETA_SUFFIX` (G1 push-side) deliberately stayed in `main.py`.
  2. `load_library` error handling — unified LOUD (`sys.exit(1)`) everywhere;
     mainfetch's silent-zero-entries behavior intentionally removed.
  3. Re-export shims — none; both files use `from mvcommon import ...`.
  4. Hashing print cosmetic change — accepted; both entry points now use the
     live progress bar from `mvcommon.calculate_file_hash`.
  5. mainfetch import line — kept honest: imported only the referenced symbols
     (`RESTORE_DIR_NAME`, `load_library`, `calculate_file_hash`). The other 8
     constants were dead in mainfetch even before the refactor, so they were not
     re-imported. `save_library` not imported (unused; available for future
     fetch-side writes per the spec).
- **Notes / surprises:**
  - The binding-location hazard was real and demonstrated: with the old conftest
    (patching only `main.LIBRARY_*`), 12 C9/C11 tests failed ("ID not found")
    because `mvcommon`'s own bindings still pointed at real `C:\Media`. Patching
    `mvcommon.LIBRARY_*` fixed it; the `"C:\\Media" not in path` hard guard is retained.
  - `origin/main` had already absorbed the G1 push-partial work (`PARTIAL_SUFFIX`,
    `MVMETA_SUFFIX`, shifted line numbers); the plan's symbol set was still exact.
  - Root `PLAN.md` was left as-is (it is the in-flight G1 working copy) — not
    overwritten with this A1 plan, per step 7.
  - An unrelated in-progress edit to `improvements_tierG.md` was parked in
    `git stash` (`A1-orchestrator: park G1 improvements_tierG.md edit`) before
    branching from `origin/main`, so it can be restored on the G1 branch later.
- **Follow-ups created:** none. (Foundation seam ready for auto-rollback,
  IMP-C2 retry helper, IMP-A6 type hints, IMP-A7 pytest harness.)
