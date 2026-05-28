---
title: "A7 — Pytest harness with library fixtures"
type: prerequisite-task
improvement: IMP-A7
tier: A
role: complementary
order: 7
status: not-started
branch: test/pytest_harness
feature: auto-rollback
tags: [claude, mediavault, complementary, tier/A, status/not-started]
created: 2026-05-28
---

# A7 — Pytest harness with library fixtures

> **At a glance:** Stand up a real pytest harness in `tests/` (conftest +
> sandbox/library fixtures using the gitignored `resources/library_*.json`
> snapshots). Auto-rollback creates the first tests; A7 formalizes the harness
> they plug into. Tests-only — don't fix bugs found.
> Related: [[RELATED_IMPROVEMENTS]] · [[_TRACKER]]

## Claude Code prompt (paste into a fresh session)

```
Use the planner agent.

Task: Plan the implementation of improvement IMP-A7 ("Pytest harness with library fixtures") from improvements_tierA.md.

Read these FIRST, in this order, before planning:
1. improvements_tierA.md -> the IMP-A7 section. The spec for WHAT and WHY.
2. docs/feature-auto-rollback/README.md, then docs/feature-auto-rollback/RELATED_IMPROVEMENTS.md ("A7" subsection). A7 is COMPLEMENTARY to the upcoming auto-rollback feature, which will create the FIRST real tests in tests/ and bootstrap a minimal harness; A7 formalizes it. RELATED_IMPROVEMENTS explains how the two should share conftest/fixtures and coordinate naming.
3. ARCHITECTURE.md and the code; note the gitignored resources/library_*.json snapshots, which are the intended READ-ONLY library fixtures.

What to build: a pytest harness in tests/ — conftest.py with fixtures that load the resources/library_*.json snapshots read-only and provide a sandboxed temp library + media folder (via monkeypatched path/library constants). Add an initial set of characterization tests for representative existing behavior (e.g., library load/save round-trip, manual-id parsing). Set up pytest config (pytest.ini or pyproject) for discovery.

Constraints (full list in docs/feature-auto-rollback/README.md):
- Branch from origin/main. Suggested branch: test/pytest_harness.
- Tests-only: do NOT modify main.py/mainfetch.py behavior. Read-only characterization tests are fine; if a test reveals a bug, REPORT it, don't fix it here.
- COPIES only — fixtures use the gitignored resources/library_*.json snapshots and temp sandboxes; NEVER touch real C:\Media files or real C:\Media\library_*.json.
- CI integration (GitHub Actions) is OUT of scope (that's the A7 follow-up).
- Leave the seam: design conftest/fixtures and naming so the auto-rollback rollback tests plug straight in (sandbox fixture + failure-injection monkeypatch helpers). If IMP-A1 (mvcommon) is already done, import helpers from mvcommon.

DOCUMENTATION: save your PLAN.md into docs/feature-auto-rollback/A7-pytest-harness/PLAN.md; keep artifacts there; fill the "Completion report" in docs/feature-auto-rollback/A7-pytest-harness/A7-pytest-harness.md when done. (Keep /PLAN.md at root in sync if needed.)

Pause and ask me about open decisions, at minimum: pytest config location; which behaviors to cover first; fixture/sandbox API shape (auto-rollback will build on it).

Deliverables: PLAN.md only (no code, no branches) with steps + tests + verification + "Open Decisions". Note IMP-A7 is marked done in improvements_tierA.md (minus the CI follow-up) on implementation, the architect updates README (how to run tests), and I want branch name, PR to main, and manual test commands at the end.
```

## What & why
The project has no automated tests. A7 builds the pytest harness + library
fixtures that everything else (including auto-rollback) tests against.

## Relationship to auto-rollback / seam to leave
Auto-rollback bootstraps the first tests; A7 formalizes the harness. Share
conftest/fixtures + naming so rollback tests plug in. Details:
[[RELATED_IMPROVEMENTS]] → A7.

## Definition of Done
- [ ] Planner run; `PLAN.md` in this subfolder; open decisions confirmed
- [ ] Branched `test/pytest_harness` off `origin/main`
- [ ] `conftest.py` + sandbox/library fixtures + pytest config
- [ ] Initial characterization tests passing; no source behavior changed
- [ ] Any bug found is REPORTED, not fixed here
- [ ] Seam left: fixtures/naming ready for rollback tests to extend
- [ ] `IMP-A7` marked done (minus CI follow-up) in `improvements_tierA.md`
- [ ] `README` updated (how to run tests)
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
