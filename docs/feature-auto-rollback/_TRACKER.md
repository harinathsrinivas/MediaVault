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

**C9 → C11 → G1** (direct prerequisites), then **C8 / C1 / C2 / A1 / A7** as desired.
Dependency hints:
- Do **G1 before C8** — C8 must verify at the correct remote path (final name after G1's `adb shell mv`).
- Do **C2 before C8** (or together) — C8's mismatch is the natural trigger for C2's retry wrapper.
- Do **A1 before C2 and A7** if you want the retry helper and test imports to live in the shared `mvcommon` module.

## Board

| # | Task | Tier | Role | Suggested branch | Status |
|---|---|---|---|---|---|
| 1 | [[C9-atomic-replace]] — atomic `cmd_replace` | C | Prereq (do first) | `fix/atomic_replace` | done |
| 2 | [[C11-restore-quarantine]] — restore quarantine | C | Prereq | `feature/restore_quarantine` | done |
| 3 | [[G1-push-partial-atomic-rename]] — `.partial` + atomic remote rename + mvmeta | G | Prereq (bigger) | `feature/push_partial_atomic_rename` | done |
| 4 | [[C1-season-auto-resume]] — season auto-resume | C | Complementary | `feature/season_auto_resume` | not-started |
| 5 | [[C2-adb-selenium-retry]] — ADB/Selenium retry | C | Complementary | `feature/adb_selenium_retry` | done |
| 6 | [[C8-post-push-verify]] — post-push remote verify | C | Complementary (after G1) | `feature/post_push_verify` | done |
| 7 | [[A1-extract-mvcommon]] — extract `mvcommon.py` | A | Foundation | `refactor/extract_mvcommon` | done |
| 8 | [[A7-pytest-harness]] — pytest harness | A | Complementary | `test/pytest_harness` | not-started |

## Checklist

- [x] C9 — atomic replace *(done — fix/atomic_replace, merged 2026-05-29)*
- [x] C11 — restore quarantine *(done — feature/restore_quarantine, PR #6, merged 2026-05-29)*
- [x] G1 — push `.partial` + atomic remote rename + mvmeta *(done — PR #7, merged)*
- [ ] C1 — season auto-resume
- [x] C2 — ADB/Selenium retry *(done — feature/adb_selenium_retry, 2026-05-30)*
- [x] C8 — post-push remote verify *(done — feature/post_push_verify, PR to main 2026-05-30)*
- [x] A1 — extract `mvcommon.py` *(done — refactor/extract_mvcommon, merged)*
- [ ] A7 — pytest harness
- [x] **All prerequisites done → return to finalize [[PLAN|auto-rollback PLAN]]**

## How each task subfolder is used

When you start a task, paste its note's **Claude Code prompt** into a fresh
Claude Code session. The session should:
1. write its `PLAN.md` into that task's subfolder,
2. implement on the suggested branch (off `origin/main`),
3. fill in the **Completion report** at the bottom of the task note,
4. mark the improvement done in its `improvements_tier*.md` file.

That way each subfolder ends up as the full, self-contained record of the task.
