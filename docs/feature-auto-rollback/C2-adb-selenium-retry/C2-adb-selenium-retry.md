---
title: "C2 — ADB / Selenium retry with backoff"
type: prerequisite-task
improvement: IMP-C2
tier: C
role: complementary
order: 5
status: done
branch: feature/adb_selenium_retry
feature: auto-rollback
tags: [claude, mediavault, complementary, tier/C, status/done]
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
- [x] Planner run; `PLAN.md` in this subfolder; open decisions confirmed
- [x] Branched `feature/adb_selenium_retry` off `origin/main`
- [x] `retry()` helper + wrapped `adb push` + wrapped `trigger_download`
- [x] First-attempt success unchanged; failure contract unchanged
- [x] Tests in `tests/` (mocked, copies only): fail-then-succeed + permanent-fail
- [x] `cmd_replace` PermissionError loop untouched
- [x] `IMP-C2` marked done in `improvements_tierC.md`
- [x] `ARCHITECTURE.md` / `README` updated if needed
- [ ] PR to `main` opened — branch pushed; open manually (gh not authenticated)
- [x] Completion report below filled in

## Completion report
- **Branch:** `feature/adb_selenium_retry` (cut from `origin/main` @ `1aac738` — A1 merged; G1 = `8c12680`).
- **PR:** _Open manually_ — `gh` is not authenticated in this environment. Branch `feature/adb_selenium_retry` is pushed; create the PR at https://github.com/harinathsrinivas/MediaVault/pull/new/feature/adb_selenium_retry (base `main`, title `Feature/adb selenium retry (IMP-C2)`).
- **Merged commit:** _(pending merge of the PR to `main`)_
- **Files changed:**
  - `mvcommon.py` — added `retry(fn, attempts=3, backoff=(1,4,16), jitter=1.0, retry_on=(SubprocessError, TimeoutError), on_retry=None)` (stdlib-only).
  - `main.py` — wrapped `cmd_push`'s push + atomic `mv` pair in `retry()` (1/4/16s + jitter, pre-retry `.partial` rm, `⏳ Retry N/3` print); failure contract preserved.
  - `mainfetch.py` — `trigger_download` body refactored to `_attempt()` with an explicit one-retry-after-5s on a `False` return OR a caught exception.
  - `tests/conftest.py` — added the `mock_fetch` fixture (testing-strategy §4.6).
  - `tests/test_mvcommon.py` — 7 `retry()` unit tests (incl. jitter offset).
  - `tests/test_cmd_push_retry.py` (new) — adb push transient/permanent/happy-path tests.
  - `tests/test_trigger_download_retry.py` (new) — trigger_download retry tests.
  - `tests/test_cmd_push_partial.py` — G1 failure tests adapted to inject *permanent* (all-attempt) failures so they keep testing the failure contract under the new retry layer.
  - `improvements_tierC.md`, `ARCHITECTURE.md` — docs.
- **Tests added:** 7 (`test_mvcommon` retry) + 3 (`test_cmd_push_retry`) + 5 (`test_trigger_download_retry`) = 15 new; full suite 46 passed.
- **Manual test commands:** see PLAN.md → Verification → Manual smoke (`python main.py push <id> SIZE_GB 10`; lock-screen mid-chunk to see a `⏳ Retry` line; `python main.py fetch <id>`).
- **Open decisions resolved:** (1) `retry_on` per call site — adb push `(CalledProcessError,)`, trigger_download retries on BOTH `False` and exception. (2) Per-attempt logging is user-visible `print`. (3) Jitter enabled (`jitter=1.0` default; tests use `jitter=0` or patch `random.uniform`).
- **Notes / surprises:**
  - The three G1 push tests in `test_cmd_push_partial.py` previously injected a *single* transient failure and asserted return `False`; the new retry layer self-heals that, so `FakeAdb` was reworked to fail a targeted chunk position on *every* attempt (permanent) — preserving their failure-contract intent. Self-heal is covered by the new `test_cmd_push_retry.py`.
  - `retry` was deliberately NOT imported into `mainfetch.py`: the trigger_download retry is an explicit one-retry block (Resolved Decision 5), since `retry()` does not treat a `False` return as retryable; adding the import would be dead code.
  - The Task subagent tool was unavailable this run, so the orchestrator executed all steps directly (same as the A1 run); STATUS.md is committed per-step as a scratchpad.
- **Follow-ups created:** none. (IMP-C8 remote `md5sum` verification will later reuse this retry seam; IMP-A5 will make counts/backoff configurable — both already out of scope and tracked separately.)
