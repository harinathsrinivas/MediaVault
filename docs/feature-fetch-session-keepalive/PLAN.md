# Task: Fetch-session keep-alive + shared Google-Photos logged-out detector (IMP-C17, satisfies IMP-C6)

Suggested branch: feature/fetch_session_keepalive

## Context
Unattended/couch fetches silently die when a Chrome profile's Google session has
expired: `trigger_download` finds 0 thumbnails, returns `False`, and a 10-chunk movie
cascades into "0 of 10 succeeded" with no cause — often a 90-minute wait on a doomed run
(IMP-C6). MediaVault drives three separate Google accounts (movies / series / anime) via
three persistent Chrome profiles in `C:\Media\Utils\` (IMP-C16). This feature implements
the user-chosen **Hybrid** approach: (1) a one-time profile-hardening checklist that
reduces forced re-auth, (2) a daily, idle-gated Selenium **keep-alive warm-up**
(`tools/warm_profiles.py`) that opens each profile, confirms the session is alive, and
keeps the cookies fresh, and (3) a **shared logged-out detector** in `mainfetch.py`
(`SessionExpiredError` + `check_session_alive`) reused by both the live fetch path
(satisfying IMP-C6) and the warm-up runner. A logged-out profile fails loudly: console
message + appended log line + non-zero exit code + a Windows desktop toast. A single-flight
lock keeps the warm-up from colliding with a live fetch on Chrome's fixed CDP port 9222.

## Goal
Concrete definition of done:
- `mainfetch.py` exposes `SessionExpiredError` and a pure-ish `check_session_alive(driver)`
  helper; `trigger_download`/`cmd_fetch_route` use it to abort a logged-out fetch fast with
  a remediation message (IMP-C6), instead of timing out.
- `python tools/warm_profiles.py` opens each of the three profiles (reusing
  `init_driver` / `profile_for_id` / `CHROME_PROFILES`), confirms each session is alive,
  appends a log line per run, and on any logged-out profile prints a message, fires a
  desktop toast, and exits non-zero. `--profile <key>` warms just one. Healthy run exits 0.
- A committed `tools/mediavault_warm_profiles.xml` Task Scheduler definition (daily ~03:00,
  run-only-if-idle, current user, no admin) plus a one-line `schtasks /create /xml ...`
  README snippet.
- A single-flight lock under `~/.mediavault/locks/fetch_session.lock` is acquired by BOTH
  `cmd_fetch_route` and the warm-up; the warm-up skips (exit 0) if a live fetch holds it.
- New unit tests cover the detector and the warm-up runner with Selenium/driver fully
  mocked; `python -m pytest -q` and `python -m pytest tests/smoke -q` stay green.
- Docs (README + ARCHITECTURE), `improvements_tierC.md` (C17 + C6 → done), `PRIORITY.md`,
  and `docs/priority-graph/priority-graph.html` are updated in the same change.

## Files affected
- `mainfetch.py` — add `SessionExpiredError`, `PHOTOS_URL` constant, `check_session_alive(driver)`; wire it into `trigger_download` and `cmd_fetch_route` (IMP-C6 early-abort + remediation message + 3-consecutive-zero backstop); acquire the single-flight lock in `cmd_fetch_route`.
- `mvcommon.py` — add a tiny shared single-flight lock helper (`fetch_session_lock()` context manager) + `MV_STATE_DIR`/lock-path constants, so both `mainfetch` and `tools/warm_profiles.py` import one implementation (mvcommon imports only stdlib — no cycle).
- `tools/warm_profiles.py` — NEW. The keep-alive runner: imports `mainfetch` (`init_driver`, `CHROME_PROFILES`, `profile_for_id`, `check_session_alive`, `SessionExpiredError`) and `mvcommon` (lock + log path); per-profile warm + health check; log line; toast; exit code; single-flight lock; `--profile` arg.
- `tools/notify_toast.py` — NEW (small). `send_toast(title, message)` → dependency-free Windows toast via PowerShell+WinRT; no-op-safe (returns False, never raises) off-Windows or if PowerShell is unavailable. Kept separate so it is unit-testable and reusable (IMP-X5/daemon).
- `tools/mediavault_warm_profiles.xml` — NEW. Committed Task Scheduler task definition (daily 03:00, run-only-if-idle, current user).
- `tests/test_session_detector.py` — NEW. Unit tests for `check_session_alive` / `SessionExpiredError` / `trigger_download` early-abort (fake driver, Selenium stubbed).
- `tests/test_warm_profiles.py` — NEW. Unit tests for `tools/warm_profiles.py` (mock `init_driver`/`check_session_alive`/toast/lock; assert per-profile loop, exit code, log line, toast-on-logout, single-flight skip).
- `tests/test_notify_toast.py` — NEW. Unit tests for `send_toast` (monkeypatch `subprocess.run`; assert PowerShell argv shape, non-Windows no-op, never raises).
- `tests/smoke/test_smoke_all_commands.py` — extend the existing anime-routing smoke with a logged-out-detector assertion on `cmd_fetch_route` (no browser).
- `README.md` — add a "Fetch session keep-alive" subsection (warm-up command, the `schtasks` registration one-liner, the profile-hardening checklist, logged-out remediation).
- `ARCHITECTURE.md` — document the shared detector, the warm-up runner, the lock, the toast, and the scheduler under the mainfetch / tooling sections.
- `improvements/improvements_tierC.md` — IMP-C6 → done; add IMP-C17 → done.
- `improvements/PRIORITY.md` — move C6 to DONE, add C17 to DONE, bump count + Last-updated, advance the NEXT pointer.
- `docs/priority-graph/priority-graph.html` — add the C17 node, mark C6+C17 done, wire edges.
- `docs/feature-fetch-session-keepalive/PLAN.md` + `DECISIONS.md` — this plan + decisions (tracked, shipped with the branch).

## Approach
The detector is the keystone and ships first so both consumers share one behavior. After
`driver.get(PHOTOS_URL)` and a short wait, `check_session_alive(driver)` inspects
`driver.current_url`: if the host is `accounts.google.com` (or anything other than a
`photos.google.com` URL), the session is logged out → raise `SessionExpiredError(profile)`.
`trigger_download` runs the check at the top of each `_attempt()` and lets
`SessionExpiredError` propagate (it is NOT swallowed by the broad `except`, and is NOT a
retryable type), so `cmd_fetch_route` catches it once, prints the remediation message, and
aborts the batch — no more 90-minute doomed wait (IMP-C6). A heuristic backstop in
`cmd_fetch_route` (3 consecutive 0-thumbnail results on the same profile) raises the same
error for a logged-in-but-search-dead session.

The single-flight lock lives in `mvcommon` (stdlib-only) as a context manager over
`~/.mediavault/locks/fetch_session.lock`; `cmd_fetch_route` holds it for the whole batch.
`tools/warm_profiles.py` tries the same lock non-blocking: if a live fetch holds it, the
warm-up logs "fetch in progress, skipping" and exits 0 (success — nothing to do). The
warm-up otherwise loops the three `CHROME_PROFILES`, calls `init_driver(profile_key)`,
navigates to Photos, runs `check_session_alive`, quits the driver, and records the result.
A logged-out profile → console line + appended `~/.mediavault/logs/warm_profiles.log` line
+ a desktop toast (via `tools/notify_toast.send_toast`) + a non-zero process exit. The
committed `.xml` registers it daily at 03:00, run-only-if-idle, as the current user.

## Steps

- [x] 1. [model: opus] [effort: high] Add the shared logged-out detector to `mainfetch.py`.
  - Files: `mainfetch.py`
  - Details: Add module constant `PHOTOS_URL = "https://photos.google.com"` (reuse it in `trigger_download._attempt` instead of the inline literal). Add `class SessionExpiredError(Exception): pass`. Add `def check_session_alive(driver, profile_key=None):` that reads `driver.current_url`, parses the host with `urllib.parse.urlparse`, and: returns `True` if the host endswith `photos.google.com`; raises `SessionExpiredError(f"profile {profile_key!r} appears logged out (redirected to {host})")` if the host is `accounts.google.com` or does not contain `photos.google.com`. Be defensive: if `driver.current_url` raises (Selenium fault) just return `True` (let the existing retry/handle paths deal with a genuine browser fault — the detector must not invent failures). Keep it side-effect-free (no `driver.get` inside — the caller has already navigated). Pre-resolved fact: `accounts.google.com` is the host Google redirects an expired session to; `photos.google.com` is the only signed-in host. Do NOT add any selenium import beyond what exists; use stdlib `urllib.parse` only.
  - Acceptance: `python -c "import mainfetch; e=mainfetch.SessionExpiredError; print(callable(mainfetch.check_session_alive))"` prints `True`; the file still imports without selenium installed (the existing `try/except ImportError` guard must still cover all new code — `check_session_alive` references no selenium symbol). `python -m pytest tests/test_anime_fetch_routing.py -q` still green.

- [x] 2. [model: opus] [effort: high] Wire the detector into `trigger_download` + `cmd_fetch_route` (IMP-C6 early-abort) and add the single-flight lock to `cmd_fetch_route`.
  - Files: `mainfetch.py`
  - Details: (a) In `trigger_download._attempt`, immediately after `driver.get(PHOTOS_URL)` + the `wait.until(... body ...)`, call `check_session_alive(driver)`. Let `SessionExpiredError` propagate — it must NOT be caught by the broad `except Exception` retry arms in `trigger_download` (re-raise it explicitly if needed so the one-retry wrapper does not turn it into a `False`). Rationale: a logged-out session will not self-heal on a 5s retry; failing fast is the whole point of IMP-C6. (b) In `cmd_fetch_route`, wrap the per-entry loop so `SessionExpiredError` is caught ONCE at the top: print the remediation message exactly — `❌ Profile '{active_profile}' is logged out. Open Chrome with --user-data-dir={CHROME_PROFILES[active_profile]}, sign in to photos.google.com, then re-run.` — then `return` (abort the batch; do not process further entries). (c) Heuristic backstop: track consecutive 0-thumbnail `trigger_download` results for the active profile; on the 3rd consecutive zero, raise `SessionExpiredError` so the same remediation fires (covers logged-in-but-search-dead). (d) Acquire the single-flight lock (from step 3) around the whole batch via `with mvcommon.fetch_session_lock(blocking=True):` so a live fetch is mutually exclusive with the warm-up; an interactive fetch should still proceed (blocking acquire with a generous timeout, then proceed anyway if the lock is stale — never hard-block the user). Keep healthy-session behavior byte-identical when the session is alive.
  - Acceptance: `python -m pytest tests/test_trigger_download_retry.py -q` still green (healthy path unchanged). New tests in step 9 assert the early-abort + remediation message. `python -m pytest tests/smoke -q` green.

- [x] 3. [model: opus] [effort: high] Add the single-flight lock helper + state-dir constants to `mvcommon.py`.
  - Files: `mvcommon.py`
  - Details: Add `MV_STATE_DIR = os.path.join(os.path.expanduser("~"), ".mediavault")`, `MV_LOCK_DIR = os.path.join(MV_STATE_DIR, "locks")`, `MV_LOG_DIR = os.path.join(MV_STATE_DIR, "logs")`, and `FETCH_SESSION_LOCK = os.path.join(MV_LOCK_DIR, "fetch_session.lock")`. Implement `@contextlib.contextmanager def fetch_session_lock(blocking=True, timeout=30, stale_after=3600):` that: `os.makedirs(MV_LOCK_DIR, exist_ok=True)`; attempts an atomic create-exclusive of the lock file via `os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)` writing the current pid + timestamp; on collision, if the existing lock is older than `stale_after` seconds (read its mtime) treat it as stale and reclaim it; if `blocking`, poll up to `timeout`s then (interactive case) proceed by reclaiming rather than raising; if non-blocking and held, raise `LockHeldError` (define it). Always remove the lock file on `__exit__` (best-effort, guarded). Keep it stdlib-only (`os`, `time`, `contextlib`, `errno`) — mvcommon must NEVER import main/mainfetch. This is a generic OS-file lock; do NOT add fcntl/msvcrt platform branches unless the create-exclusive approach proves insufficient (it works cross-platform for advisory single-flight). Pre-resolved: `os.O_CREAT|os.O_EXCL` is the standard atomic "create only if absent" idiom and is the simplest correct single-flight primitive here.
  - Acceptance: `python -c "import mvcommon; ctx=mvcommon.fetch_session_lock; print(callable(ctx))"` → `True`; a quick local check that two nested non-blocking acquisitions of the same lock raise `LockHeldError`. `python -m pytest tests/test_mvcommon.py -q` still green.

- [x] 4. [model: sonnet] [effort: medium] Create `tools/notify_toast.py` — dependency-free Windows toast.
  - Files: `tools/notify_toast.py` (new)
  - Details: Implement `def send_toast(title, message):` that builds the toast XML and runs a PowerShell command via `subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", PS_SCRIPT], capture_output=True, timeout=20)`. Use the EXACT pre-resolved WinRT pattern (no BurntToast, no pip dep): load `[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime]`, `loadXml` a `<toast><visual><binding template="ToastGeneric"><text>$title</text><text>$message</text></binding></visual></toast>` document, then `[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime]::CreateToastNotifier($AppId).Show($doc)` with `$AppId = '{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\WindowsPowerShell\v1.0\powershell.exe'` (the built-in PowerShell shortcut AppId, so the toast actually surfaces). Pass `title`/`message` safely (escape single quotes; reject/normalize newlines). MUST be no-op-safe: if `sys.platform != "win32"`, or PowerShell is missing, or the subprocess errors/times out → return `False`, never raise. Return `True` on a clean exit. Keep it ~40 lines, no classes. Sources for the WinRT snippet are recorded in `docs/feature-fetch-session-keepalive/DECISIONS.md`.
  - Acceptance: `python -c "import tools.notify_toast as t; print(t.send_toast('x','y'))"` returns a bool and does not raise on this Windows box (a toast may briefly appear). Unit tests in step 10 assert the argv shape via a mocked `subprocess.run` and the non-Windows no-op.

- [x] 5. [model: opus] [effort: high] Create `tools/warm_profiles.py` — the keep-alive runner.
  - Files: `tools/warm_profiles.py` (new)
  - Details: A `__main__`-style script with a testable `def warm_all(profile_keys=None) -> int:` returning the intended process exit code. Behavior: (1) Acquire `mvcommon.fetch_session_lock(blocking=False)`; if `LockHeldError`, print `⏭️ Live fetch in progress — skipping warm-up.`, append a log line, and `return 0`. (2) For each key in `profile_keys` (default `list(mainfetch.CHROME_PROFILES.keys())`): `driver = mainfetch.init_driver(key)`; if `driver is None`, record a launch failure for that profile; else `driver.get(mainfetch.PHOTOS_URL)`, brief `time.sleep`, then `mainfetch.check_session_alive(driver, key)` inside try/except catching `SessionExpiredError`; always `driver.quit()` in a `finally`. (3) Aggregate results; append ONE structured line per run to `~/.mediavault/logs/warm_profiles.log` (timestamp + per-profile `OK`/`LOGGED_OUT`/`LAUNCH_FAIL`) — create `mvcommon.MV_LOG_DIR` first. (4) If any profile is `LOGGED_OUT` or `LAUNCH_FAIL`: print a console summary, call `tools.notify_toast.send_toast("MediaVault: account needs attention", "<profile(s)> logged out — re-login required")`, and `return 1`; else print `✅ All profiles healthy.` and `return 0`. `__main__` parses `--profile <key>` (warm just one; validate against `CHROME_PROFILES`) and calls `sys.exit(warm_all(...))`. Factor the side-effecting bits (init_driver, check, toast, log-append, lock) so tests can monkeypatch them. NO selenium import in this file — go through `mainfetch.init_driver` only. Make the log path and toast function module-level names so tests can patch them.
  - Acceptance: `python tools/warm_profiles.py --help` (or a `--profile bogus`) exits with usage/validation, not a traceback. (Live multi-profile run is the manual Windows test in Verification, not an automated test.) Unit tests in step 8 drive `warm_all` with everything mocked.

- [x] 6. [model: sonnet] [effort: medium] Create the committed Task Scheduler XML.
  - Files: `tools/mediavault_warm_profiles.xml` (new)
  - Details: Write a Task Scheduler v1.2 task definition (exact pre-resolved schema): root `<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">`; `<RegistrationInfo>` with `<Author>MediaVault</Author>` and a `<Description>`; `<Triggers><CalendarTrigger><StartBoundary>2026-01-01T03:00:00</StartBoundary><Enabled>true</Enabled><ScheduleByDay><DaysInterval>1</DaysInterval></ScheduleByDay></CalendarTrigger></Triggers>`; `<Principals><Principal id="Author"><LogonType>InteractiveToken</LogonType><RunLevel>LeastPrivilege</RunLevel></Principal></Principals>` (current user, NO admin); `<Settings>` with `<RunOnlyIfIdle>true</RunOnlyIfIdle>`, `<IdleSettings><Duration>PT10M</Duration><WaitTimeout>PT1H</WaitTimeout><StopOnIdleEnd>true</StopOnIdleEnd><RestartOnIdle>false</RestartOnIdle></IdleSettings>`, `<StartWhenAvailable>true</StartWhenAvailable>` (catch-up if the PC was off at 03:00), `<MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>`, `<DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>`, `<Enabled>true</Enabled>`, `<ExecutionTimeLimit>PT30M</ExecutionTimeLimit>`; `<Actions Context="Author"><Exec><Command>C:\Users\harin\PycharmProjects\MediaVault\.venv\Scripts\python.exe</Command><Arguments>C:\Users\harin\PycharmProjects\MediaVault\tools\warm_profiles.py</Arguments><WorkingDirectory>C:\Users\harin\PycharmProjects\MediaVault</WorkingDirectory></Exec></Actions>`. Add a short header comment noting the user must adjust the python.exe path / repo path if it differs, and that the toast requires the InteractiveToken (interactive desktop) principal. Encode as UTF-16 is NOT required for schtasks /xml (UTF-8 is accepted); write plain UTF-8.
  - Acceptance: The XML is well-formed: `python -c "import xml.dom.minidom,sys; xml.dom.minidom.parse(r'tools/mediavault_warm_profiles.xml'); print('ok')"` prints `ok`. (Actual `schtasks /create` is the manual Windows test in Verification.)

- [x] 7. [model: opus] [effort: high] Unit tests for the session detector + early-abort.
  - Files: `tests/test_session_detector.py` (new)
  - Details: Read `docs/testing-strategy.md` first. Pure unit tests, no fixtures needed (mirror `tests/test_trigger_download_retry.py`'s Selenium-stub style — a `_FakeDriver` with a settable `current_url`). Cover: (a) `check_session_alive` returns `True` when `current_url` is a `photos.google.com` URL; (b) raises `SessionExpiredError` when `current_url` host is `accounts.google.com`; (c) raises when host is some other non-photos host; (d) returns `True` (does not raise) when `driver.current_url` itself raises a Selenium fault; (e) `trigger_download` propagates `SessionExpiredError` (does NOT swallow it into a `False` and does NOT retry it) — drive a fake driver whose `current_url` post-`get` is `accounts.google.com` and assert `pytest.raises(mainfetch.SessionExpiredError)`; (f) `cmd_fetch_route` catches it and prints the remediation string containing `is logged out` and the profile's `--user-data-dir=` path (monkeypatch `init_driver` to return a fake logged-out driver, `resolve_targets` to return one entry; assert via `capsys`). Stub `mainfetch.time.sleep` to no-op and the Selenium surface (`WebDriverWait`, `webdriver.ActionChains`) exactly like the existing retry test. Constraints (MUST appear in test file header): "Never touch real C:\\Media files or real library_*.json." and "Run `python -m pytest` and fix failures before marking the step done."
  - Acceptance: `python -m pytest tests/test_session_detector.py -q` all green; no real browser, no network.

- [x] 8. [model: sonnet] [effort: medium] Unit tests for `tools/warm_profiles.py`.
  - Files: `tests/test_warm_profiles.py` (new)
  - Details: Read `docs/testing-strategy.md` first. Import the module as `tools.warm_profiles` (ensure `tools/__init__.py` exists or the repo root is on `sys.path` — pytest runs from root, and `tools/` currently has no `__init__.py`; add an empty `tools/__init__.py` in THIS step if import fails, noting it in the step output). Monkeypatch: `warm_profiles` module's reference to `mainfetch.init_driver` (return a fake driver with a settable `current_url`), `mainfetch.check_session_alive` (or let it run against the fake driver's url), the module-level toast function (record calls), the log path (point at `tmp_path`), and the single-flight lock. Cover: (a) all-healthy → `warm_all()` returns 0, toast NOT called, log line written with `OK` for each profile; (b) one profile logged out → returns 1, toast called once with a message naming the logged-out profile, log line shows `LOGGED_OUT`; (c) `init_driver` returns None for a profile → returns 1, recorded `LAUNCH_FAIL`, toast called; (d) single-flight: lock already held (`fetch_session_lock` raises `LockHeldError`) → returns 0, prints the skip line, no driver launched, no toast; (e) `--profile <key>` warms only that key (drive via `warm_all(["anime"])` and assert only one init_driver call). Never launch a real browser; never write to `~/.mediavault` (redirect the log path to `tmp_path`). Constraints (MUST appear in header): "Never touch real C:\\Media files or real library_*.json." and "Run `python -m pytest` and fix failures before marking the step done."
  - Acceptance: `python -m pytest tests/test_warm_profiles.py -q` all green; no browser, no `~/.mediavault` writes.

- [x] 9. [model: sonnet] [effort: medium] Unit tests for `tools/notify_toast.py` + smoke extension.
  - Files: `tests/test_notify_toast.py` (new), `tests/smoke/test_smoke_all_commands.py` (extend)
  - Details: Read `docs/testing-strategy.md` first. (a) `tests/test_notify_toast.py`: monkeypatch `tools.notify_toast.subprocess.run` to a recorder; assert `send_toast("T","M")` invokes `powershell` with `-NoProfile`/`-Command` and a script containing `ToastNotificationManager` and both `T` and `M`; assert it returns `True` on a fake exit-0 and `False` when the recorder raises (never propagates); monkeypatch `sys.platform` to `"linux"` and assert it returns `False` without calling subprocess. (b) Extend the smoke file: add `test_fetch_route_logged_out_aborts` that monkeypatches `mainfetch.init_driver` to return a fake driver whose `current_url` is `https://accounts.google.com/...`, monkeypatches `resolve_targets` to return one minimal entry, calls `mainfetch.cmd_fetch_route("mov-en-2025-f1")`, and asserts the captured output contains `is logged out` (the IMP-C6 remediation) AND that no real subprocess/browser ran. Keep it under the existing smoke style (no real browser; ≤ a second). Constraints (MUST appear in header / smoke docstring): "Never touch real C:\\Media files or real library_*.json." and "Run `python -m pytest` and fix failures before marking the step done."
  - Acceptance: `python -m pytest tests/test_notify_toast.py -q` green; `python -m pytest tests/smoke -q` green and includes the new logged-out smoke.

- [x] 10. [model: sonnet] [effort: medium] Update README + ARCHITECTURE.
  - Files: `README.md`, `ARCHITECTURE.md`
  - Details: README — add a "Fetch session keep-alive (IMP-C17)" subsection after the Chrome-profile prerequisites: (1) the one-time **profile-hardening checklist** (sign in to each of the three profiles once; on each, check "Keep me signed in" / "Stay signed in"; do NOT enable Chrome "sign out on close" or clear-cookies-on-exit; leave the profile's Chrome closed between runs so the warm-up owns port 9222; if the org/account enforces frequent re-auth, consider an app-specific session); (2) `python tools/warm_profiles.py` (warm all) and `--profile anime` (warm one); (3) the exact registration one-liner: `schtasks /create /xml "tools\mediavault_warm_profiles.xml" /tn "MediaVault Warm Profiles"` (runs daily ~03:00 as the current user, only when idle, no admin) and how to remove it (`schtasks /delete /tn "MediaVault Warm Profiles" /f`); (4) the logged-out remediation (console + log at `~/.mediavault/logs/warm_profiles.log` + toast + non-zero exit). ARCHITECTURE — under the mainfetch section document `SessionExpiredError` + `check_session_alive` (the shared detector used by both the live fetch (IMP-C6) and the warm-up), the single-flight lock (`mvcommon.fetch_session_lock`, `~/.mediavault/locks/fetch_session.lock`), and add a short "Tooling" note for `tools/warm_profiles.py` + `tools/notify_toast.py` + `tools/mediavault_warm_profiles.xml`. Note the IMP-X5 reuse seam (canary will import `check_session_alive`; sentinel out of scope). Surgical edits — match existing doc voice; do not restructure unrelated sections. (Note: the architect may also refine ARCHITECTURE.md post-merge; this step makes it accurate at merge time.)
  - Acceptance: Both files mention `warm_profiles.py`, the `schtasks /create /xml` line, `check_session_alive`, and `fetch_session_lock`. `git diff --stat` shows only README.md + ARCHITECTURE.md changed by this step.

- [x] 11. [model: haiku] [effort: low] Mark IMP-C17 + IMP-C6 done and update the priority surfaces.
  - Files: `improvements/improvements_tierC.md`, `improvements/PRIORITY.md`, `docs/priority-graph/priority-graph.html`
  - Details: In `improvements_tierC.md`: set IMP-C6 `Status: done` with a one-line note (`fold into IMP-C17 — shared SessionExpiredError + check_session_alive; trigger_download/cmd_fetch_route abort logged-out fetches with remediation; tests in tests/test_session_detector.py`); add a new `## IMP-C17` block (Category: other; Priority: high; Files: `mainfetch.py`/`mvcommon.py`/`tools/warm_profiles.py`/`tools/notify_toast.py`/`tools/mediavault_warm_profiles.xml`; Status: done) summarizing the hybrid keep-alive + scheduler + toast + lock, and noting "satisfies IMP-C6; shares the session-check helper with IMP-X5 (its ban-sentinel stays out of scope)." In `PRIORITY.md`: move C6 and add C17 to the DONE list, bump the done count (17→19), update the **Last updated** line to 2026-06-14 (IMP-C17 done), and advance the **👉 SUGGESTED NEXT TASK** to the next unblocked item — keep it **IMP-A10** (truth-up requirements.txt) unless A10 is already done; mention C6 is now resolved in any Band 2/3 reference to it. In `priority-graph.html`: in the `TASKS`/`EDGES` arrays add a `C17` node (`p` high, `s` done) and set `C6` to done; wire `C17 → C6` (satisfies) and `C16 → C17` if a sequencing edge fits the existing convention. The graph and PRIORITY.md must agree. This is a pure docs/registry edit — no code.
  - Acceptance: `grep -n "C17" improvements/PRIORITY.md improvements/improvements_tierC.md docs/priority-graph/priority-graph.html` shows the new entries; C6 shows `done` in all three; the DONE count and Last-updated line are bumped.

## Risks and edge cases
- **Detector false-positive**: a transient redirect (Google interstitial, consent page) could momentarily make the host non-`photos.google.com` and trip a false `SessionExpiredError`. Mitigation: the live-fetch backstop only escalates on the URL check at navigate time, and the 3-consecutive-zero heuristic guards the search-dead case; the warm-up's job is precisely to surface "needs attention" so a rare false alarm is low-cost (a spurious toast, re-run clears it). Keep the URL check tolerant (host endswith `photos.google.com`, accept `www.`/locale subpaths).
- **`SessionExpiredError` accidentally swallowed**: `trigger_download` has broad `except Exception` arms (the C2 one-retry). If `SessionExpiredError` is caught there it degrades back to a silent `False`. The step explicitly re-raises it past those arms — tests (step 7e) lock this in.
- **Single-flight lock starving an interactive fetch**: the lock must never hard-block a user-initiated fetch. `cmd_fetch_route` uses blocking-with-timeout then proceeds (stale reclaim); only the warm-up uses non-blocking. Stale-lock age-out (`stale_after=3600`) prevents a crashed run from wedging the lock forever.
- **Toast requires an interactive desktop session**: a toast cannot render under a non-interactive service principal — hence `LogonType=InteractiveToken` in the XML and `RunLevel=LeastPrivilege` (current user). If the machine is locked, the toast queues in Action Center; the log line + exit code remain the durable signals.
- **Port 9222 contention with a Chrome the user left open on that profile**: `init_driver` already attaches to `127.0.0.1:9222`; if the user has a normal Chrome open on the SAME profile the debug attach can fail. The hardening checklist tells the user to keep these profiles' Chrome closed; a launch failure is recorded as `LAUNCH_FAIL` (toast + exit 1), which is the correct loud signal.
- **`tools/` import path in tests**: `tools/` has no `__init__.py` today; step 8 adds one if needed so `import tools.warm_profiles` / `tools.notify_toast` resolve under pytest. The two one-shot migration scripts already in `tools/` are not imported by tests, so adding `__init__.py` is inert for them.
- **Powershell escaping**: titles/messages with quotes/newlines could break the `-Command` string or allow injection. `send_toast` normalizes newlines and escapes single quotes; the warm-up only ever passes a fixed-shape message, so the blast radius is small.
- **Hardcoded venv path in the `.xml`**: the committed task references `C:\Users\harin\PycharmProjects\MediaVault\.venv\Scripts\python.exe`. This is correct for the owner's box (the documented single-user environment) but is called out in the XML header comment as the one thing to edit on a different machine.

## Verification
Run from the repo root (use `python -m pytest`, never bare `pytest` — per project memory):
1. `python -m pytest tests/test_session_detector.py -q` — detector + early-abort unit tests green.
2. `python -m pytest tests/test_warm_profiles.py -q` — warm-up runner unit tests green.
3. `python -m pytest tests/test_notify_toast.py -q` — toast unit tests green.
4. `python -c "import xml.dom.minidom; xml.dom.minidom.parse(r'tools/mediavault_warm_profiles.xml'); print('xml ok')"` — task XML is well-formed.
5. `python -m pytest -q` — full suite green (no regressions).
6. `python -m pytest tests/smoke -q` — fast full-command cross-command gate green (LAST gate; mainfetch/mvcommon were touched).

### Manual Windows test commands (the user runs these at the end)
- **Warm one profile (smoke a real browser launch):** `python tools/warm_profiles.py --profile movies` — Chrome opens on the movies profile, attaches on 9222, the session is confirmed alive, the driver closes, a line is appended to `%USERPROFILE%\.mediavault\logs\warm_profiles.log`, exit code 0 (`echo %ERRORLEVEL%`).
- **Warm all three:** `python tools/warm_profiles.py` — all three profiles cycle; `✅ All profiles healthy.`; exit 0.
- **Deliberately-logged-out toast test:** sign OUT of one profile (open that profile's Chrome via `--user-data-dir=...`, sign out of Google, close it), then run `python tools/warm_profiles.py --profile <that_key>` — expect the console "logged out" line, a **desktop toast** "MediaVault: account needs attention", a `LOGGED_OUT` log line, and a non-zero exit code. Re-login and re-run to confirm it returns to healthy/exit 0.
- **Register the scheduled task (no admin):** `schtasks /create /xml "tools\mediavault_warm_profiles.xml" /tn "MediaVault Warm Profiles"` then run it immediately with `schtasks /run /tn "MediaVault Warm Profiles"` and check the log file updated. Inspect with `schtasks /query /tn "MediaVault Warm Profiles" /v /fo LIST`. Remove with `schtasks /delete /tn "MediaVault Warm Profiles" /f`.
- **Single-flight check:** start a real `python mainfetch.py fetch <id>` (holds the lock), and in a second shell run `python tools/warm_profiles.py` — expect `⏭️ Live fetch in progress — skipping warm-up.` and exit 0 (no second Chrome on 9222).

## Out of scope
- IMP-X5 account-health **canary / ban sentinel item** — only the shared `check_session_alive` seam is provided; the per-account sentinel and ban-vs-expiry discrimination are NOT built here.
- IMP-C5 broader fallback search-query rewrite (separate task; this plan does not change query construction).
- IMP-A5 external config (`mvconfig.json`) — `CHROME_PROFILES`/`ID_PREFIX_PROFILE` stay in-module; warm-up reuses them as-is.
- IMP-C3 `doctor` command integration (doctor will later call `check_session_alive`; not wired here).
- Any change to `cmd_push`/`cmd_replace`/`cmd_restore`, the rollback journal, PONRs, or `ENTRY_TYPE_KEYS` (none are touched — change-gate and schema-guard do not apply).
- Multi-account fetch fallback (IMP-X4) and replication (IMP-X1).
- Auto-relogin / headless credential injection — explicitly NOT attempted (security + ToS); the remediation is a human re-login prompted by the loud signal.

## IMP mapping
- This is **IMP-C17** (Tier C — fetch reliability/robustness) and it **satisfies/advances IMP-C6** (detect Google Photos session expiry early). On implementation, BOTH IMP-C17 and IMP-C6 are marked `done` in `improvements/improvements_tierC.md` (step 11).
- `improvements/PRIORITY.md` AND `docs/priority-graph/priority-graph.html` must be updated in the SAME change (CLAUDE.md maintenance protocol) — step 11.
- ARCHITECTURE.md + README are updated at merge time (step 10); the architect may further refine ARCHITECTURE.md post-merge.
- **IMP-X5** shares the session-check helper (`check_session_alive`) but its **ban-sentinel stays out of scope** — note the seam only.
- **No `ENTRY_TYPE_KEYS` change** (no entry type or shared field added/renamed/removed) → `tests/test_entry_schema_guard.py` is NOT touched. No Consumer Impact Analysis section is required (no shared data contract changes).

## Branch + PR plan
- **Branch:** `feature/fetch_session_keepalive` (from up-to-date `origin/main`).
- **PR title (MUST carry the IMP codes):** `feature: fetch-session keep-alive + Google Photos logged-out detector — IMP-C17, IMP-C6`
- **PR body order (git-pr-conventions):** (1) auto-generated Claude Code summary (Summary / Changes / Test plan incl. the smoke gate + manual Windows steps), then (2) a `## Original task prompt` section containing the COMPLETE verbatim user prompt that kicked off this work, then (3) the `🤖 Generated with [Claude Code](https://claude.com/claude-code)` trailer.
- **Commit trailer:** `Co-Authored-By: Claude <model that did the work> <noreply@anthropic.com>`.
- **Checkpoint 1 (human gate):** create the PR, then STOP and ask the user before merging to `main`. Do not `gh pr merge` autonomously.
- **Checkpoint 2 (human gate):** after merge, on the user's approval, archive the branch as an annotated `archive/feature/fetch_session_keepalive` tag (merge info + revive steps in the message), push the tag, then delete the branch local+remote.
- Ship the `docs/feature-fetch-session-keepalive/` artifacts (this PLAN.md + DECISIONS.md) with the branch; the root `/PLAN.md` stays gitignored and is NOT committed.

## Suggested next tasks (informed by PRIORITY.md)
- **IMP-A10** (Band 1, low risk) — truth-up `requirements.txt` (`requests` + `webdriver-manager` are missing; a clean install is half-broken). The current 👉 SUGGESTED NEXT; pairs naturally after this since this feature adds no new pip deps but relies on `webdriver-manager`/`selenium` being installed.
- **IMP-C3** `doctor` (Band 2) — a pre-flight health command; it should call the new `check_session_alive` to FAIL on a logged-out profile before a long batch (the natural next consumer of this work).
- **IMP-X5** account-health canary (Band 3) — builds directly on the `check_session_alive` seam this feature exposes; adds the per-account ban sentinel that was kept out of scope here.
- **IMP-C5** real fallback search query (Band 2) — the other half of fetch reliability; increases second-attempt hit rate so the detector fires only on genuine logouts, not fuzzy-search misses.
- 🚦 **IMP-R6 / IMP-R7** (Band 0, change-gated) — still await a user decision before any code; surface them when the user is choosing the next critical item.
