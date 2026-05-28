---
title: "A1 — Extract mvcommon.py"
type: prerequisite-task
improvement: IMP-A1
tier: A
role: foundation
order: 6
status: not-started
branch: refactor/extract_mvcommon
feature: auto-rollback
tags: [claude, mediavault, foundation, tier/A, status/not-started]
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
- [ ] Planner run; `PLAN.md` in this subfolder; open decisions confirmed
- [ ] Branched `refactor/extract_mvcommon` off `origin/main`
- [ ] `mvcommon.py` created; both scripts import from it; no import cycles
- [ ] Behavior identical except the agreed `load_library` unification
- [ ] Tests in `tests/` (copies only): round-trip + corrupt-library handling
- [ ] Seam left: clean, stable public surface
- [ ] `IMP-A1` marked done in `improvements_tierA.md`
- [ ] `ARCHITECTURE.md` / `README` updated (module layout)
- [ ] PR to `main` opened
- [ ] Completion report below filled in

## Completion report (fill in when done)
- **Branch:**
- **PR:**
- **Merged commit:**
- **Files changed:**
- **Tests added:**
- **Manual test commands:**
- **Open decisions resolved:**
- **Notes / surprises:**
- **Follow-ups created:**
