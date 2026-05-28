---
title: Auto-Rollback Prerequisites — Tracker
type: dashboard
feature: auto-rollback
tags: [claude, mediavault, tracker]
created: 2026-05-28
status-legend: not-started | planning | in-progress | in-review | done
---

# Auto-Rollback Prerequisites — Tracker

Live dashboard for the improvements to do **before** finalizing auto-rollback.
Update the `status:` field in each task note and tick its box here as it moves.
See [[RELATED_IMPROVEMENTS]] for how each connects, [[FAILURE_ANALYSIS]] for the
code boundaries, and [[_VAULT-GUIDE]] for sync setup.

## Suggested order

**C9 → C11 → G1** (direct prerequisites), then **C1 / C2 / A1 / A7** as desired.
Dependency hint: do **A1 before C2 and A7** if you want the retry helper and test
imports to live in the shared `mvcommon` module.

## Board

| # | Task | Tier | Role | Suggested branch | Status |
|---|---|---|---|---|---|
| 1 | [[C9-atomic-replace]] — atomic `cmd_replace` | C | Prereq (do first) | `fix/atomic_replace` | not-started |
| 2 | [[C11-restore-quarantine]] — restore quarantine | C | Prereq | `feature/restore_quarantine` | not-started |
| 3 | [[G1-push-partial-atomic-rename]] — `.partial` + atomic remote rename + mvmeta | G | Prereq (bigger) | `feature/push_partial_atomic_rename` | not-started |
| 4 | [[C1-season-auto-resume]] — season auto-resume | C | Complementary | `feature/season_auto_resume` | not-started |
| 5 | [[C2-adb-selenium-retry]] — ADB/Selenium retry | C | Complementary | `feature/adb_selenium_retry` | not-started |
| 6 | [[A1-extract-mvcommon]] — extract `mvcommon.py` | A | Foundation | `refactor/extract_mvcommon` | not-started |
| 7 | [[A7-pytest-harness]] — pytest harness | A | Complementary | `test/pytest_harness` | not-started |

## Checklist

- [ ] C9 — atomic replace
- [ ] C11 — restore quarantine
- [ ] G1 — push `.partial` + atomic remote rename + mvmeta
- [ ] C1 — season auto-resume
- [ ] C2 — ADB/Selenium retry
- [ ] A1 — extract `mvcommon.py`
- [ ] A7 — pytest harness
- [ ] **All prerequisites done → return to finalize [[PLAN|auto-rollback PLAN]]**

## How each task subfolder is used

When you start a task, paste its note's **Claude Code prompt** into a fresh
Claude Code session. The session should:
1. write its `PLAN.md` into that task's subfolder,
2. implement on the suggested branch (off `origin/main`),
3. fill in the **Completion report** at the bottom of the task note,
4. mark the improvement done in its `improvements_tier*.md` file.

That way each subfolder ends up as the full, self-contained record of the task.
