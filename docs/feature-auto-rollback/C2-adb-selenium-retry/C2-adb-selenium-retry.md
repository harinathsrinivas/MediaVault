---
title: "C2 — ADB / Selenium retry with backoff"
type: prerequisite-task
improvement: IMP-C2
tier: C
role: complementary
order: 5
status: not-started
branch: feature/adb_selenium_retry
feature: auto-rollback
tags: [claude, mediavault, complementary, tier/C, status/not-started]
created: 2026-05-28
---

# C2 — ADB / Selenium retry with backoff

> **At a glance:** Add a shared `retry()` helper and wrap `adb push` (exp backoff
> 1/4/16s) and `trigger_download` (one retry after ~5s). Complementary: fewer
> transient failures → rollback/hard-fail fires less often. **Tip:** do [[A1-extract-mvcommon|A1]]
> first if you want the helper in `mvcommon`.
> Related: [[RELATED_IMPROVEMENTS]] · [[_TRACKER]]

## Claude Code prompt (paste into a fresh session)

```
Use the planner agent.

Task: Plan the implementation of improvement IMP-C2 ("Exponential-backoff retry logic for ADB and Selenium ops") from improvements_tierC.md.

Read these FIRST, in this order, before planning:
1. improvements_tierC.md -> the IMP-C2 section. The spec for WHAT and WHY.
2. docs/feature-auto-rollback/README.md, then docs/feature-auto-rollback/RELATED_IMPROVEMENTS.md ("C2" subsection). C2 is COMPLEMENTARY to the upcoming auto-rollback feature: fewer transient failures means rollback/hard-fail fires less often. The one hard requirement: do NOT change the failure contract callers rely on.
3. ARCHITECTURE.md and the code: the adb push call in cmd_push in main.py; trigger_download in mainfetch.py; and the existing 3-retry PermissionError loop in cmd_replace (the only existing retry — do NOT disturb it).

Confirmed state — do not re-derive these, treat as facts:
- IMP-A1 (mvcommon) is DONE and merged into origin/main. Put the retry() helper in mvcommon.py — not main.py. Branch C2 from origin/main AFTER confirming A1 is merged (check git log origin/main for the A1 merge commit).
- IMP-G1 (push partial + atomic rename) is DONE and merged into origin/main (PR #7). A retried adb push MUST delete any `.partial` remnant left by the failed attempt before re-uploading — add a pre-retry `adb shell rm <remote>.partial` step within the retry wrapper for adb push specifically. This is not conditional.

What to build: a shared retry() helper in mvcommon.py — retry(callable, attempts=3, backoff=(1,4,16), retry_on=(SubprocessError, TimeoutError)). Wrap adb push with it (exponential backoff on transient CalledProcessError, with pre-retry .partial cleanup). Wrap the inner body of trigger_download to retry once after ~5s if the search returns 0 thumbnails or the click fails, before returning False.

Constraints (full list in docs/feature-auto-rollback/README.md):
- Branch from origin/main (after A1 merge). Suggested branch: feature/adb_selenium_retry.
- Happy path identical (first-attempt success behaves exactly as today). A retried-then-exhausted op must return the SAME failure signal callers rely on — do not change the failure contract auto-rollback depends on.
- Tests in tests/, COPIES only — never touch real C:\Media files or real library_*.json; mock subprocess/Selenium to fail N times then succeed, and to fail permanently.
- Surgical: the retry helper in mvcommon + its two call sites + tests; don't touch the cmd_replace PermissionError loop; don't touch archive/. Retry counts hardcoded for now (configurability is IMP-A5, out of scope).

DOCUMENTATION: save your PLAN.md into docs/feature-auto-rollback/C2-adb-selenium-retry/PLAN.md; keep artifacts there; fill the "Completion report" in docs/feature-auto-rollback/C2-adb-selenium-retry/C2-adb-selenium-retry.md when done. (Keep /PLAN.md at root in sync if needed.)

Pause and ask me about open decisions, at minimum: exact exception set to retry on for adb push vs trigger_download; whether to add per-attempt logging; whether to add jitter to the backoff.

Deliverables: PLAN.md only (no code, no branches) with steps + tests + verification + "Open Decisions". Note IMP-C2 is marked done in improvements_tierC.md on implementation, the architect updates docs if needed, and I want branch name, PR to main, and manual test commands at the end.
```

## What & why
ADB push and Selenium ops have zero retry today, so transient blips become hard
failures. A shared backoff retry absorbs them.

## Relationship to auto-rollback / seam to leave
Reduces how often every failure scenario fires. Critical: a retried-then-failed
op must keep the same failure signal so rollback behaves unchanged. Details:
[[RELATED_IMPROVEMENTS]] → C2.

## Definition of Done
- [ ] Planner run; `PLAN.md` in this subfolder; open decisions confirmed
- [ ] Branched `feature/adb_selenium_retry` off `origin/main`
- [ ] `retry()` helper + wrapped `adb push` + wrapped `trigger_download`
- [ ] First-attempt success unchanged; failure contract unchanged
- [ ] Tests in `tests/` (mocked, copies only): fail-then-succeed + permanent-fail
- [ ] `cmd_replace` PermissionError loop untouched
- [ ] `IMP-C2` marked done in `improvements_tierC.md`
- [ ] `ARCHITECTURE.md` / `README` updated if needed
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
