---
title: "C1 — Season auto-resume"
type: prerequisite-task
improvement: IMP-C1
tier: C
role: complementary
order: 4
status: not-started
branch: feature/season_auto_resume
feature: auto-rollback
tags: [claude, mediavault, complementary, tier/C, status/not-started]
created: 2026-05-28
---

# C1 — Season auto-resume

> **At a glance:** Persist per-season progress so re-running
> `prep_push_rep_season` auto-resumes from the last completed episode instead of
> stopping. Complementary: auto-rollback *prints* the resume command; C1
> *automates* it.
> Related: [[RELATED_IMPROVEMENTS]] · [[_TRACKER]]

## Claude Code prompt (paste into a fresh session)

```
Use the planner agent.

Task: Plan the implementation of improvement IMP-C1 ("Auto-resume from last completed episode in cmd_prep_push_rep_season") from improvements_tierC.md.

Read these FIRST, in this order, before planning:
1. improvements_tierC.md -> the IMP-C1 section. The spec for WHAT and WHY.
2. docs/feature-auto-rollback/README.md, then docs/feature-auto-rollback/RELATED_IMPROVEMENTS.md ("C1" subsection). C1 is COMPLEMENTARY to the upcoming auto-rollback feature: rollback PRINTS the resume command on a season failure; C1 AUTOMATES the resume from a progress file. RELATED_IMPROVEMENTS explains how they fit together.
3. ARCHITECTURE.md and the code: cmd_prep_push_rep_season in main.py (~1425-1486) — the episode loop, the already-uploaded skip ~1465-1469, and the bare break ~1483 that currently stops the run with no automation.

What to build: persist per-season progress (e.g., a .mediavault_progress.json) so re-running prep_push_rep_season auto-resumes from the last completed episode. Completed episodes are skipped; processing continues from the first incomplete one.

Constraints (full list in docs/feature-auto-rollback/README.md):
- Branch from origin/main. Suggested branch: feature/season_auto_resume.
- Happy path identical for a clean full run; preserve the existing already-uploaded skip behavior.
- Tests in tests/, COPIES only — never touch real C:\Media files or real library_*.json; sandbox a multi-episode season folder; mock adb.
- Surgical: cmd_prep_push_rep_season + progress-file helpers + tests; don't touch archive/.
- Leave the seam: choose a progress-file location and schema the later auto-rollback season handling can READ, so its resume messaging and single-item rollback stay consistent.

DOCUMENTATION: save your PLAN.md into docs/feature-auto-rollback/C1-season-auto-resume/PLAN.md; keep task artifacts there; fill the "Completion report" in docs/feature-auto-rollback/C1-season-auto-resume/C1-season-auto-resume.md when done. (Keep /PLAN.md at root in sync if needed.)

Pause and ask me about open decisions, at minimum: progress-file location and schema; when it is created/updated/deleted (e.g., removed on full success); how it interacts with the episode-range filter and the already-uploaded skip.

Deliverables: PLAN.md only (no code, no branches) with steps + tests + verification + "Open Decisions". Note IMP-C1 is marked done in improvements_tierC.md on implementation, the architect updates docs, and I want branch name, PR to main, and manual test commands at the end.
```

## What & why
A failed season run currently just stops. A progress file lets a re-run pick up
from the last completed episode.

## Relationship to auto-rollback / seam to leave
Auto-rollback delivers the resume *message*; C1 the *automation*. The progress
file format should be one rollback can read. Details: [[RELATED_IMPROVEMENTS]] → C1.

## Definition of Done
- [ ] Planner run; `PLAN.md` in this subfolder; open decisions confirmed
- [ ] Branched `feature/season_auto_resume` off `origin/main`
- [ ] Progress persistence + auto-resume implemented; clean-run path unchanged
- [ ] Tests in `tests/` (copies only, mocked adb); passing
- [ ] Seam left: progress-file schema readable by rollback
- [ ] `IMP-C1` marked done in `improvements_tierC.md`
- [ ] `ARCHITECTURE.md` / `README` updated
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
