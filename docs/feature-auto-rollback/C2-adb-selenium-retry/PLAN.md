# Task: IMP-C2 — Exponential-backoff retry for ADB push and Selenium fetch

Suggested branch: feature/adb_selenium_retry

## Context
ADB push (`cmd_push` in `main.py`) and the Selenium trigger (`trigger_download` in
`mainfetch.py`) have zero retry logic today. Transient blips — phone screen locks,
USB reseat, a Google Photos search that returns 0 thumbnails on the first try —
become hard failures that the user must manually re-run. IMP-A1 (mvcommon) and
IMP-G1 (`.partial` upload + atomic remote rename) are both merged into
`origin/main` (verified: commits `1aac738` for A1, `8c12680` for G1). C2 is
*complementary* to the upcoming auto-rollback feature: fewer transient failures
means rollback/hard-fail fires less often. The single hard requirement from
`RELATED_IMPROVEMENTS.md` → C2 is: **do not change the failure contract callers
rely on** — a retried-then-exhausted op must return the exact same signal as today
(`cmd_push` returns `False` and leaves state untouched; `trigger_download` returns
`False`).

## Goal
A reusable `retry()` helper lives in `mvcommon.py`. `adb push` is wrapped so a
transient `CalledProcessError` triggers up to 3 attempts with backoff (1s, 4s, 16s)
plus randomized jitter, deleting the `.partial` remnant on the device before each
re-attempt. The inner body of `trigger_download` retries once after ~5s when the
search yields 0 thumbnails OR the attempt raises an exception, before returning
`False`. Each retry prints a single user-visible line (`⏳ Retry N/M after Ss…`).
First-attempt success is byte-for-byte identical to today. Exhausted-retry behaviour
produces the identical failure signal to today. Tests (mocked, copies only) prove:
(a) fail-N-then-succeed self-heals, and (b) permanent failure returns the unchanged
failure signal. `pytest -q` is green.

## Files affected
- `mvcommon.py` — add the shared `retry()` helper (stdlib only, including `random` for jitter; no import of main/mainfetch).
- `main.py` — wrap the `adb push` (and its atomic `mv`) in the `cmd_push` upload loop with `retry()`, including the pre-retry `adb shell rm <remote>.partial` cleanup; import `retry` from mvcommon.
- `mainfetch.py` — wrap the inner search/click body of `trigger_download` with the retry-once-after-5s behaviour; import `retry` from mvcommon.
- `tests/conftest.py` — add the `mock_fetch` fixture (per testing-strategy §4.6 / §11) for the fetch round-trip and trigger retry tests.
- `tests/test_mvcommon.py` — unit tests for `retry()` itself (no fixtures).
- `tests/test_cmd_push_retry.py` — new: adb push retry protocol tests (FakeAdb recorder).
- `tests/test_trigger_download_retry.py` — new: trigger_download retry tests (monkeypatched Selenium-free).
- `improvements_tierC.md` — flip IMP-C2 Status to done on implementation.
- `ARCHITECTURE.md` — short note documenting the retry wrapper at both call sites (§7.5 push, §8.3 trigger_download, §12 error handling).
- `docs/feature-auto-rollback/C2-adb-selenium-retry/C2-adb-selenium-retry.md` — fill the Completion report.
- `/PLAN.md` (repo root) — keep in sync with this plan (root copy is the live working copy per README).

## Approach
`retry(fn, attempts=3, backoff=(1,4,16), jitter=1.0, retry_on=(...), on_retry=None)`
calls `fn()`; on an exception whose type is in `retry_on` it sleeps
`backoff[attempt_index] + random.uniform(0, jitter)` (the base value clamped to the
last entry if attempts exceed the tuple length) and retries, up to `attempts` total
calls. `jitter=0` disables the random offset (used by tests that want exact sleep
values). If a non-`retry_on` exception is raised it propagates immediately
(unchanged). After the final attempt the *last* `retry_on` exception is re-raised. An
optional `on_retry(attempt, exception)` callback runs before each sleep — this is the
seam used by `cmd_push` to issue the pre-retry `.partial` cleanup and to print the
per-attempt line, without baking ADB knowledge into mvcommon.

At the `cmd_push` call site, the existing `try/except subprocess.CalledProcessError`
that sets `all_success=False; break` is preserved exactly. We move only the
`push` + `mv` pair into a small local closure passed to `retry(...)`. Because `retry`
re-raises the last `CalledProcessError` after exhaustion, the surrounding `except`
catches it identically to today — same `break`, same `_parts/` left populated for
resume, same `return False`. The `on_retry` callback runs `adb shell rm '<final>.partial'`
(best-effort, `check=False`) so a re-push to `.partial` never collides with a stale
remnant, and prints the per-attempt retry line. The happy path (first attempt
succeeds) never sleeps, never calls `rm`, and never prints a retry line.

At the `trigger_download` call site, the entire existing body (navigate → search →
click → Shift+D → Esc) already returns `True` on success and `False`/raises on
trouble. We keep that body, but treat both "0 thumbnails found / click failed" AND
"a caught exception inside the attempt" as retryable: on the first `False`/exception,
print the retry line, sleep ~5s, and run the body once more; if the second attempt
also fails, return `False` exactly as today. The function still returns `True`/`False`
with the same meaning — no contract change.

## Steps

- [x] 1. [model: opus] Add the `retry()` helper to `mvcommon.py`.
  - Files: `mvcommon.py`
  - Details: Add `import time` and `import random` and `from subprocess import SubprocessError` (or reference `subprocess.SubprocessError`) at the top with the existing stdlib imports. Implement `retry(fn, attempts=3, backoff=(1, 4, 16), jitter=1.0, retry_on=(SubprocessError, TimeoutError), on_retry=None)`: loop `for attempt in range(attempts)`; `try: return fn()`; `except retry_on as e:` — if this was the last attempt, `raise`; else compute `base = backoff[min(attempt, len(backoff) - 1)]` and `delay = base + random.uniform(0, jitter)` (so `jitter=0` yields the deterministic base), call `on_retry(attempt + 1, e)` if provided (wrap the callback in its own try/except so a callback failure never masks the retry), `time.sleep(delay)`, continue. Exceptions NOT in `retry_on` propagate immediately (no except clause catches them). `attempts=1` means a single call with no retry. Keep it tiny (~22 lines), stdlib-only, no import of main/mainfetch. Add a concise docstring stating the contract: returns `fn()`'s value on success; sleeps `backoff[i] + random.uniform(0, jitter)` between attempts; re-raises the last `retry_on` exception after exhaustion; passes through non-retryable exceptions unchanged.
  - Acceptance: `python -c "import mvcommon; mvcommon.retry"` resolves; helper signature matches the spec (including `jitter` param); no circular import.

- [x] 2. [model: sonnet] Unit-test `retry()` in `tests/test_mvcommon.py`.
  - Files: `tests/test_mvcommon.py`
  - Details: Add tests (no fixtures, pure functions). To keep sleep assertions deterministic despite jitter, call `retry(...)` with `jitter=0` in the timing tests (or monkeypatch `mvcommon.random.uniform` to return a fixed value, e.g. `0.0`); always monkeypatch `mvcommon.time.sleep` to record the delays passed to it. Tests: (a) `fn` succeeding on first call → returns value, `sleep` never called; (b) `fn` raising a `retry_on` exception twice then succeeding (counter closure) → returns value, with `jitter=0` `sleep` called twice with `[1, 4]`; (c) `fn` raising `retry_on` every time → after `attempts` calls the last exception is re-raised and `fn` was called exactly `attempts` times; (d) `fn` raising a non-`retry_on` exception (e.g. `ValueError`) → propagates immediately, called once, `sleep` not called; (e) backoff tuple shorter than attempts (`attempts=4, backoff=(1,4), jitter=0`) → last backoff value reused, sleeps `[1,4,4]`; (f) `on_retry` callback invoked once per retry with `(attempt_number, exception)`; (g) jitter applied — monkeypatch `mvcommon.random.uniform` to return `0.5` and assert the recorded sleeps are the base values plus `0.5` (e.g. `[1.5, 4.5]`), proving the jitter offset is added to the backoff base. Monkeypatch `mvcommon.time.sleep` so the suite stays fast. Never touch real `C:\Media` files or real `library_*.json`. Run `pytest -q` and fix failures before marking the step done.
  - Acceptance: `pytest tests/test_mvcommon.py -q` green; the seven behaviours above are asserted, including the jitter-offset test.

- [x] 3. [model: opus] Wrap `adb push` + atomic `mv` in `cmd_push` with `retry()`, including pre-retry `.partial` cleanup and a per-attempt print.
  - Files: `main.py`
  - Details: Add `retry` to the existing `from mvcommon import (...)` block. Inside the upload loop (currently `main.py` ~745-794), refactor the two `subprocess.run(... push ...)` and `subprocess.run(... shell mv ...)` calls into a local closure (e.g. `def _push_and_rename(): ...`) capturing `f`, `remote_partial_path`, `safe_partial`, `safe_final`, `adb_base`. Define an `on_retry(attempt, exc)` callback that (1) prints a single user-visible retry line matching the codebase's print style — e.g. `print(f"⏳ Retry {attempt}/3 after {…}s (ADB push failed)…")` (use the backoff base for the displayed seconds; this is a human-facing line, not the exact jittered value), and (2) runs `subprocess.run(adb_base + ["shell", "rm", "'<remote_full_path>.partial'"], check=False)` — i.e. `adb shell rm '<remote>.partial'`, single-quote-escaped exactly like the existing `mv` paths — to delete the failed attempt's `.partial` remnant before re-upload. This cleanup is unconditional for adb push (not gated on G1 detection). Call `retry(_push_and_rename, attempts=3, backoff=(1, 4, 16), retry_on=(subprocess.CalledProcessError,), on_retry=_cleanup_and_log)` (jitter stays at its default so real pushes get randomized backoff). CRITICAL: keep the surrounding `try/except subprocess.CalledProcessError: ... all_success=False; break` and `except Exception` blocks exactly as they are — `retry` re-raises the last `CalledProcessError` after exhaustion, so the existing except catches it and the failure contract (break, leave `_parts/`, `return False`) is unchanged. The local-chunk `os.remove(f)` and `print("✅")` stay inside the success path after the `retry(...)` call returns. Do NOT touch the `cmd_replace` PermissionError loop.
  - Acceptance: First-attempt success path is unchanged (one push + one mv, no rm, no sleep, no retry line); on transient failure the closure is retried with a preceding `rm` of the `.partial` and a printed `⏳ Retry N/3` line; exhausted retries break the loop and `return False` exactly as before.

- [ ] 4. [model: sonnet] ADB push retry protocol tests in `tests/test_cmd_push_retry.py`.
  - Files: `tests/test_cmd_push_retry.py`
  - Details: Use the `FakeAdb` recorder pattern (defined inline in `tests/test_cmd_push_partial.py`) and the `sandbox` + `split_entry` fixtures. Extend a local FakeAdb subclass (or add a flag) so a chunk push can fail the first K attempts then succeed (transient), and separately fail permanently. Monkeypatch `main.subprocess.run` to the recorder and monkeypatch `mvcommon.time.sleep` so the suite is fast (retry's sleep lives in `mvcommon.time.sleep`); jitter does not need patching because `time.sleep` is stubbed, but if any test asserts on the slept value, also monkeypatch `mvcommon.random.uniform` to a fixed return. Tests: (a) push fails twice then succeeds → `cmd_push` returns `True`, library entry flips to `uploaded=True`/`status="onboarded"`, and an `adb shell rm '<...>.partial'` was issued before each re-push attempt (assert the rm appears in `fake.calls` for that chunk's remote path); (b) push fails permanently (all 3 attempts) → `cmd_push` returns `False`, library entry `uploaded` stays `False` (failure contract unchanged), `_parts/` left populated; (c) happy path (no failures) issues exactly one push + one mv per chunk and zero `rm` calls (regression guard for the unchanged happy path, no retry line printed). Never touch real `C:\Media` files or real `library_*.json`. Run `pytest -q` and fix failures before marking the step done.
  - Acceptance: `pytest tests/test_cmd_push_retry.py -q` green; transient-then-success, permanent-fail-contract, and clean-happy-path all asserted.

- [ ] 5. [model: opus] Add the `mock_fetch` fixture to `tests/conftest.py`.
  - Files: `tests/conftest.py`
  - Details: Implement `mock_fetch` per `docs/testing-strategy.md` §4.6 — a fixture that monkeypatches `mainfetch.trigger_download` to copy a pre-seeded file (from a temp dir / `mock_device`) into a local restore directory and return `True`, so fetch logic can be exercised without Selenium. Follow the conftest binding rules: any fixture that redirects `LIBRARY_*` must patch BOTH `mvcommon.LIBRARY_*` AND `main.LIBRARY_*` — reuse/compose with the existing `sandbox` fixture rather than DIY-redirecting. Document the new fixture in testing-strategy.md §4.6 only if its final shape differs from the documented stub. This step is assigned to opus specifically because of the conftest binding hazard. Never touch real `C:\Media` files or real `library_*.json`. Run `pytest -q` and fix failures before marking the step done.
  - Acceptance: `mock_fetch` importable and usable; existing tests still green (`pytest -q`); fixture does not reference any real `C:\Media` path.

- [ ] 6. [model: sonnet] Wrap the inner body of `trigger_download` with retry-once-after-5s on both 0-thumbnails and exceptions, with a per-attempt print.
  - Files: `mainfetch.py`
  - Details: Add `retry` to the `from mvcommon import (...)` line. Refactor the existing body of `trigger_download` (the navigate→search→click→Shift+D→Esc sequence, `mainfetch.py` ~96-153) into an inner helper (e.g. `def _attempt(): ...`) that returns `True` on success and `False` when 0 thumbnails are found / index out of range (the existing `return False` at the "Not found" branch). Implement the retry so BOTH a `False` return AND a raised exception inside `_attempt()` are retried once: run `_attempt()` inside a `try`; if it returns `False` OR raises, print a single user-visible retry line matching the codebase style (e.g. `print("⏳ Retry 2/2 after 5s (no results / error)…")`), `time.sleep(5)`, then run `_attempt()` once more; return that second result. On the second attempt, if it returns `False` or raises, return `False` (matching today's failure signal — let the existing outer `except Exception` semantics of returning `False` hold; do not let a second-attempt exception escape `trigger_download`). The simpler explicit one-retry form is preferred here for readability over routing through `retry()` (this is a single targeted retry, not the exponential ADB case; `retry()` does not natively treat a `False` return as retryable). Keep the function's return contract identical: `True` if the trigger was sent, `False` otherwise. Do NOT change `init_driver`, the harvester loop, or the two-attempt structure in `fetch_single_entry` (that is a separate, existing precision-vs-fallback retry).
  - Acceptance: A first-attempt success returns `True` with no sleep and no retry line; a first-attempt "0 thumbnails" leads to one printed retry line, exactly one 5s wait, and one re-attempt before returning `False`; a first-attempt exception also triggers exactly one 5s wait + re-attempt (not an immediate `False`); the function signature and return semantics are unchanged.

- [ ] 7. [model: sonnet] trigger_download retry tests in `tests/test_trigger_download_retry.py`.
  - Files: `tests/test_trigger_download_retry.py`
  - Details: Test `trigger_download` without a real browser by passing a fake `driver` object whose `find_elements` / `get` / `execute_script` are stubs, and monkeypatching `mainfetch.time.sleep` (so the 5s wait is instant) and `mainfetch.webdriver.ActionChains` / `WebDriverWait` as needed to no-ops. Tests: (a) fake driver returns 0 thumbnails on the first call then a clickable thumbnail on the second → `trigger_download` returns `True` and `sleep(5)` was called exactly once; (b) fake driver returns 0 thumbnails on both calls → returns `False` after exactly one 5s wait (two attempts total), matching today's failure signal; (c) first attempt succeeds → returns `True`, `sleep(5)` never called (happy path unchanged); (d) NEW — the first attempt raises an exception (e.g. driver stub raises on `find_elements`) but the second attempt succeeds → `trigger_download` returns `True` and `sleep(5)` was called exactly once (proves exceptions are retried, not just the 0-thumbnail case). If stubbing the Selenium surface proves heavy, instead refactor the attempt body to a small injectable seam in step 6 and test that seam directly — but prefer driver-stubbing to keep the public function under test. Never touch real `C:\Media` files or open a real browser. Run `pytest -q` and fix failures before marking the step done.
  - Acceptance: `pytest tests/test_trigger_download_retry.py -q` green; retry-then-success, permanent-fail-contract, clean-happy-path, and exception-then-success all asserted.

- [ ] 8. [model: haiku] Mark IMP-C2 done and document the retry wrapper.
  - Files: `improvements_tierC.md`, `ARCHITECTURE.md`
  - Details: In `improvements_tierC.md`, change the IMP-C2 `Status: pending` line to `Status: done (feature/adb_selenium_retry, PR to main <DATE>)` — mirror the wording style used by IMP-C9 (`Status: done (fix/atomic_replace, PR to main 2026-05-29)`). In `ARCHITECTURE.md`, add a one-to-two sentence note in §7.5 (ADB push flow) that the push+mv pair is now wrapped in `mvcommon.retry()` with 1/4/16s backoff plus randomized jitter, a pre-retry `.partial` rm, and a per-attempt `⏳ Retry N/3` print; add a sentence in §8.3 (trigger_download) that the body retries once after 5s on a 0-thumbnail miss or a caught exception, printing one retry line; add a bullet under §12 (Error Handling) noting the new retry layer and that it preserves the existing failure contract. Do not restructure or reword unrelated content. This is a doc-only step.
  - Acceptance: IMP-C2 shows `done` with branch + PR note; ARCHITECTURE.md mentions the retry wrapper (with jitter and the retry print) at both call sites and in §12; no unrelated edits.

- [ ] 9. [model: haiku] Fill the Completion report and sync the root PLAN.md.
  - Files: `docs/feature-auto-rollback/C2-adb-selenium-retry/C2-adb-selenium-retry.md`, `/PLAN.md`
  - Details: Fill the "Completion report" section in `C2-adb-selenium-retry.md` (Branch, PR, Merged commit, Files changed, Tests added, Manual test commands, Open decisions resolved, Notes, Follow-ups) with the actual outcomes once steps 1-8 are done and the PR is opened. Copy/sync this plan to the repo-root `/PLAN.md` so the live working copy matches (per `docs/feature-auto-rollback/README.md`). Doc-only step.
  - Acceptance: Completion report populated; root `/PLAN.md` matches the folder copy.

## Risks and edge cases
- **Failure-contract drift (highest risk).** The whole point of C2 is invisibility on failure. `retry` MUST re-raise the last `CalledProcessError` so `cmd_push`'s existing `except` still fires `all_success=False; break; return False`. If instead `retry` swallowed the exception and returned a sentinel, the contract would break. Step 3 acceptance + test 4(b) guard this.
- **Pre-retry `rm` escaping.** The `.partial` path must be single-quote-escaped exactly like the `mv` paths (titles like `Sorcerer's Apprentice`). Reuse the existing `safe_final` escaping and append `.partial` to the *unescaped* path before escaping, matching how `remote_partial_path` is built today.
- **`rm` of a non-existent `.partial`.** First retry after a push that failed before writing anything may have no `.partial` to remove. Use `check=False` so a missing-file `rm` is a no-op, never a new failure source.
- **Backoff tuple vs attempts length mismatch.** `attempts=3` with `backoff=(1,4,16)` aligns, but the helper must clamp to the last backoff value if `attempts > len(backoff)` (tested in step 2e).
- **Jitter vs deterministic tests.** Real pushes use `jitter=1.0` so concurrent retries don't thunder. Tests must neutralize the randomness: either pass `jitter=0` or monkeypatch `mvcommon.random.uniform`. The retry-print displays the backoff base (not the jittered value) so the user-facing line stays clean. Step 2g asserts the jitter offset is actually added.
- **`mv` failure mid-sequence.** Today a `mv` failure is treated identically to a push failure (same `except`). Wrapping push+mv together means a transient `mv` failure also retries — but the pre-retry `rm` removes the final `.partial` and the next attempt re-pushes cleanly. The user confirmed this combined wrap is acceptable (matches the spirit of "transient blip").
- **trigger_download double side-effects.** A retried attempt re-navigates and re-sends Shift+D. If the first attempt actually succeeded but returned `False` spuriously, a second Shift+D could trigger a duplicate download — harmless because the harvester routes by hash and dedupes duplicates (ARCHITECTURE §8.4), but worth noting. Retrying on a caught exception (not just 0-thumbnails) slightly widens this window, but the harvester dedupe still absorbs it.
- **Sleep in tests.** All retry sleeps must be monkeypatched (`mvcommon.time.sleep`, `mainfetch.time.sleep`) or the suite becomes slow. Each test step calls this out.
- **conftest binding hazard.** Step 5 touches `conftest.py`; assigned to opus per testing-strategy §10. Patch BOTH `mvcommon.LIBRARY_*` and `main.LIBRARY_*` — reuse `sandbox`.

## Verification
Run from the repo root after all steps:
```powershell
pytest -q                                   # full suite must be green
pytest tests/test_mvcommon.py -q            # retry() unit tests (incl. jitter)
pytest tests/test_cmd_push_retry.py -q      # adb push retry protocol
pytest tests/test_trigger_download_retry.py -q   # trigger_download retry
```
Manual smoke (real device, optional, pre-merge):
```powershell
# Happy path unchanged — should behave exactly as before (no retry line printed):
python main.py push <mov-id> SIZE_GB 10
# Transient-failure self-heal: start a push, lock the phone screen for a few
# seconds mid-chunk, unlock — the push should print a ⏳ Retry line, wait
# (backoff + jitter), and complete instead of failing.
# Fetch retry: run a fetch whose first search momentarily returns no results
python main.py fetch <id>
```
Confirm: first-attempt runs show no retry/backoff output; exhausted retries print the
same final failure message as today and leave `_parts/` populated for resume.

## Out of scope
- Configurability of retry counts/backoff (that is IMP-A5 — counts are hardcoded here; only the `jitter` magnitude has a default param).
- The `cmd_replace` 3-retry `PermissionError` loop — explicitly untouched.
- Anything under `archive/`.
- `init_driver` retry / session-expiry detection (that is IMP-C6).
- Post-push remote `md5sum` verification (IMP-C8) — C2 only provides the retry seam C8 will later trigger.
- The `fetch_single_entry` two-attempt precision/fallback structure — that is a separate existing mechanism, not modified here.

## Resolved Decisions
The three previously-open decisions are confirmed by the user (2026-05-30):

1. **`retry_on` exception set per call site.**
   - adb push: `retry_on=(subprocess.CalledProcessError,)` only. ADB surfaces transient USB/device errors as a non-zero exit (CalledProcessError with `check=True`). The generic `except Exception` in `cmd_push` stays as the non-retryable catch-all that breaks immediately. (The helper's library-wide default stays `(SubprocessError, TimeoutError)` per spec; the call site narrows it.)
   - trigger_download: retry on **BOTH** a `False` return (0 thumbnails / index out of range) **AND** a caught exception inside the attempt body. A Selenium exception (e.g. `WebDriverException`) on the first attempt triggers the one 5s-wait re-attempt rather than an immediate `False`. Implemented in step 6; asserted by test 7(d).
2. **Per-attempt logging — user-visible.** One `print` per retry, matching the codebase's print style (the codebase uses `print`, not `logging`). adb push: `⏳ Retry N/3 after Ss (ADB push failed)…` (seconds shown = backoff base). trigger_download: `⏳ Retry 2/2 after 5s (no results / error)…`. The happy path prints nothing new.
3. **Jitter — YES.** Randomized jitter is added to each backoff sleep. The `retry()` helper takes a `jitter` parameter (default `1.0`) and sleeps `backoff[i] + random.uniform(0, jitter)`. Tests stay deterministic by passing `jitter=0` or by patching `mvcommon.random.uniform` / `mvcommon.time.sleep` (see step 2g). The user-facing retry line shows the integer backoff base, not the jittered float.

Design note (confirmed, kept as-is): the `push` + atomic `mv` pair is wrapped together in a single `retry()` closure, so a transient `mv` failure retries identically to a push failure, with the pre-retry `.partial` rm guaranteeing a clean re-push.

## Branch / PR / manual test (end matter)
- Branch: `feature/adb_selenium_retry`, cut from `origin/main` (A1 = `1aac738`, G1 = `8c12680` confirmed merged).
- PR: open against `main`, title `Feature/adb selenium retry (IMP-C2)`, body noting it was done as an auto-rollback complementary improvement and that the failure contract is unchanged.
- Manual test commands: see Verification → Manual smoke above.
