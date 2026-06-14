# IMP-C17 (folds IMP-C6) — Decision Log

Fetch-session keep-alive + shared logged-out detector. Decisions confirmed by the
user from the decision card (2026-06-14) plus the three planner-proposed open-question
defaults.

| # | Question | Options considered | Choice | Rationale |
|---|----------|--------------------|--------|-----------|
| D-1 | Overall approach | 1: HYBRID (one-time profile hardening + daily Selenium keep-alive warm-up + shared logged-out detector folding IMP-C6) · 2: detector-only (IMP-C6 alone) · 3: cookie-refresh hack | **Option 1 — HYBRID** | User-chosen. Warm-up keeps the three Google sessions alive so an unattended/couch fetch rarely hits a login wall; the shared detector (IMP-C6) gives fast, loud failure on the residual case; profile hardening reduces forced re-auth frequency. |
| D-2 | Warm-up cadence | daily 03:00 run-only-if-idle · hourly · on-demand only | **Daily ~03:00, run-only-if-idle** (Task Scheduler) | User-chosen. One quiet touch per account per day is enough to keep Google's session cookies fresh; run-only-if-idle avoids stealing the CDP port / CPU while the user is active. |
| D-3 | Logged-out / failed-warm-up signal | console only · +log · +exit code · +desktop toast | **All four**: console message + appended log line + non-zero exit code + Windows desktop TOAST when a profile is logged out | User-chosen. Console+exit serve interactive/CI use; the log line is the unattended audit trail; the toast is the "react within hours not weeks" couch-vault alert (the IMP-C6 goal). |
| D-4 | Scheduler mechanism | committed `.xml` + `schtasks /create /xml` README snippet · PowerShell `Register-ScheduledTask` script · manual GUI steps | **Committed Task Scheduler `.xml` under `tools/` + one-line `schtasks /create /xml` snippet in README** | User-chosen. Runs as the current user (no admin), is reviewable/diffable in git, and is reproducible on a fresh machine with one command. |
| D-5 (default) | Where does the detector live so BOTH the warm-up runner and the live fetch use it? | helper in `mainfetch.py` · helper in a new module · duplicate in `tools/` | **Single helper in `mainfetch.py`** (`SessionExpiredError` + `check_session_alive(driver)` or `is_logged_out(driver)`) reused by `trigger_download` / `cmd_fetch_route` AND imported by `tools/warm_profiles.py` | Planner default. One detector = one behavior; IMP-C6 (live fetch) and IMP-C17 (warm-up) cannot drift. IMP-X5's canary will later import the SAME helper (its ban-sentinel stays out of scope here). |
| D-6 (default) | How does the detector decide "logged out"? | URL/host redirect to `accounts.google.com` · DOM "Sign in" probe · 3-consecutive-zero-thumbnail heuristic | **Primary: redirected host is `accounts.google.com` (or not `photos.google.com`) after `driver.get(PHOTOS_URL)` + wait; backstop already in C6 spec: 3 consecutive 0-thumbnail results on the same profile in `cmd_fetch_route`** | Planner default, straight from the IMP-C6 spec. URL/host check is cheap and deterministic; the zero-thumbnail backstop catches a logged-in-but-search-broken session. DOM-text probing is locale-fragile, so it is only a tertiary fallback. |
| D-7 (default) | Single-flight: keep the warm-up from colliding with a live fetch on CDP port 9222 | OS file lock under `~/.mediavault/` · port-probe of 9222 · no lock | **A single-flight lock file under `~/.mediavault/locks/fetch_session.lock` acquired by BOTH `cmd_fetch_route` (live fetch) and `tools/warm_profiles.py` (warm-up)**; warm-up that cannot acquire it logs "fetch in progress, skipping" and exits 0 | Planner default + explicit user instruction ("keep the single-flight lock so the warm-up never collides with a live fetch on the CDP port"). Chrome's `--remote-debugging-port=9222` is a single fixed port; two concurrent `init_driver` launches would fight over it. The lock is advisory and best-effort (stale-lock age-out), never a hard blocker for an interactive fetch. |

## Scope boundaries (explicit)

- **IMP-C17** = the keep-alive runner + scheduler + toast + single-flight lock + the shared detector wiring.
- **IMP-C6** is *satisfied* by the shared detector (`SessionExpiredError` + early-exit in `trigger_download`/`cmd_fetch_route` with a remediation message). Both IMP-C17 and IMP-C6 are marked `done` on implementation.
- **IMP-X5** (account-health canary / ban early-warning) **shares the session-check helper** (`check_session_alive`) but its per-account **ban sentinel item** is **OUT OF SCOPE** here. Note the reuse seam; do not build the sentinel.
- **No change to the rollback mechanism** — this feature does not touch `cmd_push`/`cmd_replace`/`cmd_restore`, the journal, or any PONR. The change-gate does not apply.
- **No change to `ENTRY_TYPE_KEYS`** — no library entry type or shared data field is added/renamed/removed. `tests/test_entry_schema_guard.py` is therefore NOT touched.
- **No external config dependency** — the prefix→profile map and `CHROME_PROFILES` already exist in `mainfetch.py` (IMP-C16); `tools/warm_profiles.py` reuses them. Sourcing them from `mvconfig.json` stays deferred to IMP-A5.

## Pre-resolved external facts (baked into PLAN.md steps so executors never browse)

- **Dependency-free Windows toast** via PowerShell + WinRT `Windows.UI.Notifications.ToastNotificationManager`, invoked from Python with `subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", <script>])`. Use the built-in PowerShell AppId `{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\WindowsPowerShell\v1.0\powershell.exe` so the toast actually surfaces. No BurntToast module, no pip package. (Sources: 4sysops BurntToast overview; GitHub30/toast-notification-examples WinRT snippet — both confirm the WinRT path needs no module.)
- **Task Scheduler XML** uses root `<Task xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task" version="1.2">` with a `CalendarTrigger` → `StartBoundary` (a 03:00 local datetime) → `ScheduleByDay/DaysInterval=1`; `Settings` carries `<RunOnlyIfIdle>true</RunOnlyIfIdle>` + an `<IdleSettings>` (`<Duration>PT10M</Duration><WaitTimeout>PT1H</WaitTimeout>`) and `<StartWhenAvailable>true</StartWhenAvailable>` (catch-up if the box was off at 03:00); `Principal` uses `<LogonType>InteractiveToken</LogonType>` (current user, no admin, needed so a desktop toast can render). Register with `schtasks /create /xml "<path>" /tn "MediaVault Warm Profiles"` — runs as the current interactive user, no elevation. (Source: Microsoft Learn — Daily Trigger Example (XML), Task Scheduler Schema.)

## Sources

- 4sysops — Generate Windows toast notifications with the PowerShell module BurntToast: https://4sysops.com/archives/generate-windows-toast-notifications-with-the-powershell-module-burnttoast/
- GitHub — GitHub30/toast-notification-examples (WinRT, no-module toast): https://github.com/GitHub30/toast-notification-examples
- Microsoft Learn — Daily Trigger Example (XML): https://learn.microsoft.com/en-us/windows/win32/taskschd/daily-trigger-example--xml-
- Microsoft Learn — Task Scheduler Schema: https://learn.microsoft.com/en-us/windows/win32/taskschd/task-scheduler-schema
- Google Photos / CSAM-AI ban-wave context (motivates the early-warning toast) — see `improvements/improvements_tierX.md` Sources.
