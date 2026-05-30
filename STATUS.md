# Execution Log

Task: IMP-C2 — Exponential-backoff retry for ADB push and Selenium fetch
Branch: feature/adb_selenium_retry (from origin/main @ 1aac738)
Baseline (pre-change): pytest -q -> 31 passed.
Note: Task subagent tool is unavailable in this run, so the orchestrator executes steps directly (same situation as the A1 run). STATUS.md is committed per-step as a scratchpad artifact.

## Step 1 — [status: done]
- Executor: orchestrator (direct; Task subagent tool unavailable)
- Model: opus
- Files changed: mvcommon.py
- Outcome: Added `import time`, `import random`, `from subprocess import SubprocessError`. Implemented `retry(fn, attempts=3, backoff=(1,4,16), jitter=1.0, retry_on=(SubprocessError, TimeoutError), on_retry=None)`. Loops `for attempt in range(attempts)`, returns `fn()` on success; on a `retry_on` exception re-raises on the last attempt, else computes `base=backoff[min(attempt,len-1)]`, `delay=base+random.uniform(0,jitter)`, calls `on_retry(attempt+1,e)` (wrapped in try/except so a callback failure never masks the retry), then `time.sleep(delay)`. Non-`retry_on` exceptions propagate (no clause catches them). ~28 lines incl. docstring; stdlib-only; no import of main/mainfetch.
- Key decision: jitter offset added to the backoff base (not multiplied); `jitter=0` yields exact base for deterministic tests.
- Acceptance: `python -c "import mvcommon; mvcommon.retry"` resolves; signature matches spec incl. `jitter` param; no circular import. PASS.

## Step 2 — [status: done]
- Executor: orchestrator (direct)
- Model: sonnet
- Files changed: tests/test_mvcommon.py
- Outcome: Added 7 retry() unit tests (no fixtures). A helper `_patch_sleep` monkeypatches `mvcommon.time.sleep` to record delays. Tests cover: (a) first-call success, no sleep; (b) fail-twice-then-succeed with jitter=0 -> sleeps [1,4]; (c) exhaustion re-raises last exception, fn called exactly `attempts` times; (d) non-retryable ValueError propagates immediately, no sleep; (e) backoff clamp attempts=4 backoff=(1,4) -> sleeps [1,4,4]; (f) on_retry callback invoked once per retry with (attempt_number, exception); (g) jitter offset — patch `mvcommon.random.uniform`->0.5 -> sleeps [1.5,4.5].
- Acceptance: `pytest tests/test_mvcommon.py -q` -> 12 passed (5 prior A1 + 7 new). All seven behaviours incl. jitter-offset asserted. PASS.

## Step 3 — [status: done]
- Executor: orchestrator (direct)
- Model: opus
- Files changed: main.py, tests/test_cmd_push_partial.py (necessary follow-on, see Key decision)
- Outcome: Added `retry` to the `from mvcommon import (...)` block. Refactored the upload-loop body: the two `subprocess.run` calls (push to `.partial`, atomic `shell mv`) are now a local closure `_push_and_rename()` capturing `f`, `remote_partial_path`, `safe_partial`, `safe_final`, `adb_base`. An `on_retry` callback `_cleanup_and_log(attempt, exc)` (1) prints `⏳ Retry {attempt}/3 after {1/4/16}s (ADB push failed)…` using the backoff base for the displayed seconds, and (2) runs `subprocess.run(adb_base + ["shell","rm", f"'{safe_partial}'"], check=False)` to delete the stale `.partial` before re-push (unconditional). Call: `retry(_push_and_rename, attempts=3, backoff=(1,4,16), retry_on=(subprocess.CalledProcessError,), on_retry=_cleanup_and_log)` — jitter left at default 1.0 for real pushes. The surrounding `try/except subprocess.CalledProcessError: all_success=False; break` and `except Exception` are untouched; retry re-raises the last CalledProcessError after exhaustion so the failure contract (break, leave `_parts/`, return False) is unchanged. `os.remove(f)` + `print("✅")` stay in the post-`retry()` success path. cmd_replace PermissionError loop NOT touched.
- Key decision (FLAGGED): the three G1 failure tests in `tests/test_cmd_push_partial.py` previously injected a SINGLE transient push/mv failure and asserted return False — which C2 is explicitly designed to self-heal. To keep them testing the *failure contract* (not accidentally testing self-heal), I changed `FakeAdb` to fail a targeted CHUNK POSITION on EVERY attempt (keyed off the `.partial` remote path, not a global call counter), i.e. a *permanent* failure, and updated the push/mv count assertions to reflect 3 retry attempts on the failing chunk. Added an autouse `_no_retry_sleep` fixture patching `mvcommon.time.sleep` so the failure-path tests stay instant. This edit is outside step 3's listed files but is a direct, necessary consequence of introducing the retry layer; transient-self-heal is covered separately by the new step-4 tests.
- Acceptance: happy path unchanged (1 push + 1 mv/chunk, no rm, no retry line); transient failure retried with preceding `.partial` rm + printed `⏳ Retry N/3`; exhausted retries break + return False. `pytest tests/test_cmd_push_partial.py -q` -> 5 passed; full `pytest -q` -> 38 passed.

## Step 4 — [status: done]
- Executor: orchestrator (direct)
- Model: sonnet
- Files changed: tests/test_cmd_push_retry.py (new)
- Outcome: New file with a retry-aware `FakeAdb` recorder (per-dest attempt counter; supports `transient_push_fails=K` to fail the first K push attempts then succeed, and `permanent_push=True` to fail every attempt) and a simple non-split `push_entry` fixture (built on `sandbox`). Autouse `_no_retry_sleep` stubs `mvcommon.time.sleep`. Tests: (a) fail-twice-then-succeed -> cmd_push True, 3 chunk pushes, 2 `rm '<...>.partial'` issued before each re-push (asserted the rm targets end with PARTIAL_SUFFIX), entry flips uploaded=True/status=onboarded; (b) permanent fail -> cmd_push False (bool), exactly 3 push attempts, 0 mv, entry unchanged (uploaded False / local_ready); (c) happy path -> exactly 1 push + 1 mv, 0 rm, no "Retry" in stdout, entry onboarded.
- Key decision: used a non-split single-file entry (not the G1 `split_entry`, which lives in test_cmd_push_partial.py rather than conftest) to keep the new file self-contained and avoid cross-file fixture imports; this still exercises the same retry-wrapped upload-loop body.
- Acceptance: `pytest tests/test_cmd_push_retry.py -q` -> 3 passed; full `pytest -q` -> 41 passed.

## Step 5 — [status: done]
- Executor: orchestrator (direct)
- Model: opus
- Files changed: tests/conftest.py
- Outcome: Added the `mock_fetch` fixture per testing-strategy §4.6 — composes `mock_device` + `tmp_path`, patches `mainfetch.trigger_download` with `_fake_trigger(driver, query, index=0)` that copies the first `mock_device.rglob(f"*{query}*")` match into `tmp_path/restore` and returns True (False on no match). Signature matches the real `trigger_download(driver, query, index=0)`.
- Key decision (binding hazard resolved): `mock_fetch` does NOT redirect LIBRARY_*, so the dual-patch rule (mvcommon.LIBRARY_* AND main.LIBRARY_*) does not apply to it — that concern is owned by the `sandbox` fixture, which post-A1 already patches both. mock_fetch only patches `mainfetch.trigger_download` and reuses `mock_device` (which patches main.subprocess.run). Documented this reasoning in the fixture docstring. No testing-strategy.md edit needed — the final shape equals the documented §4.6 stub.
- Acceptance: `mock_fetch` registered (`pytest --fixtures` shows it at conftest.py:193) and usable; full `pytest -q` -> 41 passed (no regressions); fixture references only tmp_path / mock_device, never a real C:\Media path.

## Step 6 — [status: done]
- Executor: orchestrator (direct)
- Model: sonnet
- Files changed: mainfetch.py
- Outcome: Refactored the navigate→search→click→Shift+D→Esc body of `trigger_download` into an inner `_attempt()` that returns True on success / False on the "Not found" branch and may raise on a Selenium fault. New explicit one-retry wrapper: run `_attempt()` in try/except; if it returns True, return True; if it returns False OR raises, print `⏳ Retry 2/2 after 5s (no results / error)…`, `time.sleep(5)`, run `_attempt()` once more, and return its result (a second-attempt exception is caught and yields False). Return contract unchanged (True if trigger sent, False otherwise). init_driver / harvester loop / fetch_single_entry two-attempt structure untouched.
- Key decision (FLAGGED deviation from step-6 text): the step text said "Add `retry` to the `from mvcommon import (...)` line", but Resolved Decision 5 + the step's own body mandate an EXPLICIT one-retry block, NOT routing through `retry()` (which does not treat a False return as retryable). Importing `retry` here would be a dead import, violating the surgical/no-unused-imports guideline. I therefore did NOT add the `retry` import to mainfetch. Flagged here per "do not deviate without flagging".
- Acceptance: first-attempt success returns True with no sleep / no retry line; first-attempt False -> one printed retry line + exactly one 5s wait + one re-attempt; first-attempt exception -> same one 5s wait + re-attempt (not immediate False); signature `(driver, query, index=0)` and True/False semantics unchanged. `import mainfetch` clean; full `pytest -q` -> 41 passed. (Behavioural tests added in step 7.)

## Step 7 — [status: done]
- Executor: orchestrator (direct)
- Model: sonnet
- Files changed: tests/test_trigger_download_retry.py (new)
- Outcome: Selenium-free tests using a `_FakeDriver` (scripted `find_elements` per attempt + optional `get_raises_first`), `_FakeThumb` (displayed, width 100), and no-op `_NoOpWait`/`_NoOpActions`. Autouse `_stub_selenium` patches `mainfetch.time.sleep`, `mainfetch.WebDriverWait`, `mainfetch.webdriver.ActionChains`. A `_record_sleeps` helper records sleep durations so tests assert the 5s retry wait fired exactly once via `durations.count(5)`. Tests: (a) 0 thumbnails then clickable -> True, 2 attempts, exactly one 5s wait; (b) 0 thumbnails both -> False after exactly two attempts; (c) first success -> True, 1 attempt, zero 5s waits + no "Retry 2/2" line; (d) first attempt raises at `driver.get()` then second succeeds -> True, two navigate attempts, exactly one 5s wait.
- Key decision: the exception test raises at `driver.get()` (outside trigger_download's swallowing CSS `try/except: pass`) so the fault genuinely propagates to the retry wrapper; raising inside `find_elements` would be swallowed and would only look like a 0-thumbnail miss, not exercise the exception-retry path.
- Acceptance: `pytest tests/test_trigger_download_retry.py -q` -> 5 passed (covers retry-then-success, permanent-fail-contract, clean-happy-path, exception-then-success). Full `pytest -q` -> 46 passed.

## Step 8 — [status: done]
- Executor: orchestrator (direct)
- Model: haiku
- Files changed: improvements_tierC.md, ARCHITECTURE.md
- Outcome: improvements_tierC.md IMP-C2 Status flipped `pending` -> `done (feature/adb_selenium_retry, PR to main 2026-05-30)` (mirrors the IMP-C9 wording). ARCHITECTURE.md: §7.5 push flow gained a "Retry (IMP-C2)" bullet under the upload loop (push+mv wrapped in mvcommon.retry, 1/4/16s + jitter, pre-retry `.partial` rm, `⏳ Retry N/3` print, contract unchanged); §8.3 trigger_download gained a "Retry (IMP-C2)" note (one 5s retry on False or Selenium fault, contract unchanged); §12 Error Handling gained a "Transient-failure retry (IMP-C2)" bullet covering both call sites + the preserved failure contract.
- Acceptance: IMP-C2 shows done with branch + PR note; ARCHITECTURE mentions the retry wrapper (with jitter + retry print) at both call sites and in §12; additive edits only, no unrelated content reworded. Doc-only step (no test impact).

## Step 9 — [status: done]
- Executor: orchestrator (direct)
- Model: haiku
- Files changed: docs/feature-auto-rollback/C2-adb-selenium-retry/C2-adb-selenium-retry.md; root PLAN.md (gitignored, local sync only)
- Outcome: Filled the Completion report (branch, files changed, 15 tests added, manual commands, the 3 resolved decisions, the G1-test + dead-import notes, the no-follow-ups note). Front-matter status -> done; all DoD boxes ticked. PR URL left as PR_URL_PLACEHOLDER to be filled after the PR is opened in finalize. Synced root /PLAN.md from the folder copy (confirmed gitignored via `git check-ignore PLAN.md`, so it is NOT committed — matches the README's live-working-copy convention).
- Acceptance: Completion report populated; root /PLAN.md matches the folder copy (re-synced after the final step-9 checkbox edit).
