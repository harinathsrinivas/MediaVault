"""IMP-C25 Step 14, follow-up c — Chrome robustness (docs/feature-fetch-datetime/PLAN.md Step 14; DECISIONS.md §1e).

The user's rule (2026-10-04): "make sure the fetch also closes the chrome and tabs properly before finishing the task.
which makes it easier for us to re open in next fetch" — and what Steps 11 / 12 / 13 and the live probes found:
  * the per-pid process query fails CLOSED: a WMI / CIM error is "cannot tell" (-ErrorAction Stop), never "gone";
  * the shared close is reliable under heavy load: when the process table cannot answer, the session's open Popen
    handle proves the launched Chrome is this run's — a table naming another profile, or an unreadable command line,
    still means never (Step 11's pins);
  * a failed attach in init_driver (chromedriver-manager's online check, the attach, the Photos tab, a Ctrl+C) ends the
    Chrome it just launched — a hand-over to a Chrome that was already running is never touched;
  * every driver.get is bounded (Selenium's 300 s default hung a live probe ~5 min): fetch sessions and the crawler;
  * warm_profiles closes the Chrome it launched (it left each one on port 9222, so the next profile attached to it and
    checked the wrong account) and never attaches to the previous profile's Chrome.

Hermetic: the process table, the Popen handles, Selenium, chromedriver-manager, the port probe and the clock are fakes —
no Chrome, no PowerShell, no taskkill, no socket; the profile-mark helper is a recorder (no Preferences file is read).
"""
import contextlib
import subprocess
import types

import pytest

import mainfetch

CHROME_EXE = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
TV = mainfetch.CHROME_PROFILES["tv"]        # a path string only: nothing under it is ever read or written here
GONE = mainfetch.CHROME_GONE_WAIT_S


def _cmd(profile):
    """The command line of a Chrome browser process on `profile`."""
    return f'"{CHROME_EXE}" --user-data-dir={profile} --profile-directory=Default --remote-debugging-port=9222 about:blank'


class _Clock:
    """mainfetch's `time`: sleep() only advances the clock."""

    def __init__(self):
        self.now = 1000.0

    def time(self):
        return self.now

    def sleep(self, seconds=0):
        self.now += seconds


class _Proc:
    """The Popen handle of a launched chrome.exe: poll() is None while it runs; kill() ends it unless `unkillable`;
    wait() raises subprocess.TimeoutExpired while it still runs."""

    def __init__(self, pid=4242, running=True, unkillable=False):
        self.pid, self.running, self.unkillable, self.kills = pid, running, unkillable, 0

    def poll(self):
        return None if self.running else 0

    def kill(self):
        self.kills += 1
        if not self.unkillable:
            self.running = False

    def wait(self, timeout=None):
        if self.running:
            raise subprocess.TimeoutExpired("chrome.exe", timeout)
        return 0


class _Table:
    """mainfetch.PROCESS_TABLE: query(pid) -> answers[pid] (default: (None, None) — it cannot tell, as a PowerShell query
    that timed out under load); terminate(pid) -> `taskkill` (when True it ends the handle with that pid); listener(9222)
    -> on_port (None: nothing / cannot tell)."""

    def __init__(self, answers=None, taskkill=True, on_port=None, procs=()):
        self.answers, self.taskkill, self.on_port, self.procs = dict(answers or {}), taskkill, on_port, list(procs)
        self.queried, self.terminated = [], []

    def query(self, pid):
        self.queried.append(pid)
        return self.answers.get(pid, (None, None))

    def terminate(self, pid):
        self.terminated.append(pid)
        if self.taskkill:
            for proc in self.procs:
                if proc.pid == pid:
                    proc.running = False
        return self.taskkill

    def listener(self, port):
        return self.on_port if port == 9222 else None

    def chrome_command_lines(self):
        return None


class _Browser:
    """The browser Selenium drives: CDP (Target.getTargets lists its tabs; Browser.close ends `proc` when `closes`),
    its tabs (RUN = the tab the run opened), get / current_url, quit."""

    def __init__(self, proc=None, closes=True, tabs=("RUN",), url="https://photos.google.com/"):
        self.proc, self.closes, self.tabs, self.current, self.url = proc, closes, list(tabs), None, url
        self.cdp, self.quits, self.gets = [], 0, []
        self._mv_opened_tabs = ["RUN"]
        self.switch_to = types.SimpleNamespace(window=lambda handle: setattr(self, "current", handle))

    def execute_cdp_cmd(self, cmd, params):
        self.cdp.append(cmd)
        if cmd == "Target.getTargets":
            return {"targetInfos": [{"targetId": h, "type": "page", "url": "https://photos.google.com/"}
                                    for h in self.tabs]}
        if cmd == "Browser.close" and self.closes and self.proc is not None:
            self.proc.running = False
        return {}

    @property
    def window_handles(self):
        return list(self.tabs)

    @property
    def current_url(self):
        return self.url

    def get(self, url):
        self.gets.append(url)

    def close(self):
        self.tabs.remove(self.current)

    def quit(self):
        self.quits += 1


@pytest.fixture()
def clock(monkeypatch):
    c = _Clock()
    monkeypatch.setattr(mainfetch, "time", c)
    return c


@pytest.fixture()
def marks(monkeypatch):
    """mark_profile_clean_exit as a recorder: (profile folder, wait) per call."""
    seen = []
    monkeypatch.setattr(mainfetch, "mark_profile_clean_exit",
                        lambda user_data_dir, profile="Default", wait=0: seen.append((user_data_dir, wait)))
    return seen


def _install(monkeypatch, table):
    monkeypatch.setattr(mainfetch, "PROCESS_TABLE", table)
    return table


def _ctx(proc, launched=True, journal=False, profile=TV):
    """A session's context as _note_session leaves it (+ its fetch journal when asked)."""
    ctx = mainfetch.FetchContext()
    ctx.start_session("tv")
    ctx.chrome = {"account": "tv", "proc": proc, "pid": proc.pid, "user_data_dir": profile, "launched": launched}
    if journal:
        ctx.journal = mainfetch.FetchJournal(ctx.run_id)
        ctx.journal.add_session("tv", proc.pid, profile, launched)
    return ctx


# ===========================================================================
# 1. the per-pid process query fails closed
# ===========================================================================
class _Run:
    def __init__(self, returncode=0, stdout=""):
        self.returncode, self.stdout = returncode, stdout


@pytest.mark.parametrize("answer, expected", [
    (_Run(1, "Get-CimInstance : Invalid class\r\n"), (None, None)),     # WMI broken: the script stops -> cannot tell
    (_Run(1, "GONE\r\n"), (None, None)),                                 # a failed run never reads as "gone"
    (_Run(0, "GONE\r\n"), (False, None)),                                # a clean "no such process": gone
    (_Run(0, f"ALIVE\r\n{_cmd(TV)}\r\n"), (True, _cmd(TV))),
    (subprocess.TimeoutExpired("powershell", 60), (None, None)),        # the query timed out under load
])
def test_the_process_query_stops_on_a_wmi_error_and_never_reads_it_as_gone(monkeypatch, answer, expected):
    seen = []

    def run(argv, **kwargs):
        seen.append(argv)
        if isinstance(answer, Exception):
            raise answer
        return answer

    monkeypatch.setattr(mainfetch.subprocess, "run", run)

    assert mainfetch._ProcessTable().query(4321) == expected
    script = seen[0][4]
    assert "Get-CimInstance Win32_Process -Filter 'ProcessId=4321' -ErrorAction Stop" in script


# ===========================================================================
# 2. the shared close under heavy load: the open Popen handle proves the launched Chrome
# ===========================================================================
@pytest.mark.parametrize("answer, ours", [
    ((None, None), True),                                     # the table cannot tell (a timed-out query): the handle decides
    ((False, None), True),                                    # "gone" while the handle says it runs: the handle decides
    ((True, _cmd(TV)), True),                                 # the table names its profile
    ((True, _cmd(r"C:\Users\x\Other Profile")), False),       # the table names ANOTHER profile: never (Step 11)
    ((True, None), False),                                    # alive, its command line unreadable: never (Step 11)
], ids=["cannot-tell", "gone-but-running", "its-profile", "another-profile", "unreadable-command-line"])
def test_a_launched_chrome_is_terminated_when_the_table_or_its_handle_proves_it_ours(monkeypatch, answer, ours):
    proc = _Proc(7070)
    table = _install(monkeypatch, _Table({7070: answer}, procs=[proc]))

    got = mainfetch._terminate_session_chrome(_ctx(proc))

    assert table.queried == [7070]                            # one query, as before
    assert (got, table.terminated) == ((7070, [7070]) if ours else (None, []))
    assert proc.running is not ours


def test_when_taskkill_fails_the_handle_ends_the_chrome_it_launched(monkeypatch):
    proc = _Proc(7171)
    table = _install(monkeypatch, _Table(taskkill=False, procs=[proc]))

    assert mainfetch._terminate_session_chrome(_ctx(proc)) == 7171

    assert table.terminated == [7171] and proc.kills == 1 and not proc.running


def test_a_chrome_neither_taskkill_nor_its_handle_can_end_is_reported_not_terminated(monkeypatch):
    proc = _Proc(7272, unkillable=True)
    _install(monkeypatch, _Table(taskkill=False, procs=[proc]))

    assert mainfetch._terminate_session_chrome(_ctx(proc)) is None
    assert proc.running


@pytest.mark.parametrize("launched", [False, None], ids=["attached", "unknown"])
def test_a_chrome_the_run_only_attached_to_is_never_terminated_even_when_the_table_cannot_tell(monkeypatch, launched):
    proc = _Proc(8080)
    table = _install(monkeypatch, _Table(procs=[proc]))

    assert mainfetch._terminate_session_chrome(_ctx(proc, launched=launched)) is None

    assert table.queried == [] and table.terminated == [] and proc.running


def test_a_launched_chrome_that_has_exited_is_left_alone_without_a_query(monkeypatch):
    proc = _Proc(9090, running=False)
    table = _install(monkeypatch, _Table(procs=[proc]))

    assert mainfetch._terminate_session_chrome(_ctx(proc)) is None
    assert table.queried == [] and table.terminated == []


def test_under_heavy_load_the_shared_close_still_ends_the_chrome_it_launched(monkeypatch, capsys, clock, marks):
    # Step 12/C's live failure, replayed: the machine is so busy that PowerShell answers nothing — the debug-port
    # listener cannot be told (so only the run's tabs are closed), the Chrome does not exit within the wait, and the
    # per-pid query cannot tell either. Before Step 14 the close ended with "did not close" and the next account's
    # Chrome could not bind port 9222.
    proc = _Proc(7070)
    table = _install(monkeypatch, _Table(procs=[proc]))
    browser = _Browser(proc, closes=False)
    ctx = _ctx(proc, journal=True)

    mainfetch._close_session(browser, ctx, last=True)

    out = capsys.readouterr().out
    assert "Browser.close" not in browser.cdp                 # the listener could not be told: never sent blind
    assert browser.tabs == []                                 # only the run's own tab was closed first
    assert table.queried == [7070] and table.terminated == [7070] and not proc.running
    assert "   > 🧹 Chrome ('tv') terminated (pid 7070) — the next fetch starts a fresh one." in out
    assert "did not close" not in out
    assert ctx.journal.doc["sessions"][-1]["closed"] == "terminated"
    assert marks == [(TV, GONE)]                              # once it is gone: no "Restore pages?"


# ===========================================================================
# 3. init_driver: a failed attach ends the Chrome it just launched
# ===========================================================================
@pytest.fixture()
def launch(monkeypatch, clock, marks):
    """The REAL init_driver with every outside piece faked: Popen hands out `state.proc` (pid 4242, running), the
    driver manager / Service / webdriver.Chrome / use_photos_tab are stand-ins a test can make fail, the process table
    is a _Table whose taskkill ends the handle."""
    state = types.SimpleNamespace(proc=_Proc(4242), driver=None, install=None, chrome=None, tab=None, launched=[],
                                  marks=marks)
    state.table = _install(monkeypatch, _Table(procs=[state.proc]))

    def popen(cmd, *args, **kwargs):
        state.launched.append(list(cmd))
        return state.proc

    def install():
        if state.install is not None:
            raise state.install
        return "chromedriver.exe"

    def chrome(service=None, options=None):
        if state.chrome is not None:
            raise state.chrome
        return state.driver

    def tab(driver):
        if state.tab is not None:
            raise state.tab
        return "current"

    monkeypatch.setattr(mainfetch.subprocess, "Popen", popen)
    monkeypatch.setattr(mainfetch, "ChromeDriverManager", lambda *a, **k: types.SimpleNamespace(install=install))
    monkeypatch.setattr(mainfetch, "Service", lambda *a, **k: "service")
    monkeypatch.setattr(mainfetch.webdriver, "Chrome", chrome)
    monkeypatch.setattr(mainfetch, "use_photos_tab", tab)
    state.driver = _Browser()
    return state


ENDED = "   > 🧹 Closed the Chrome this launch started (pid 4242) — attaching to it failed."


@pytest.mark.parametrize("where", ["driver-manager", "attach"])
def test_a_failed_attach_ends_the_chrome_this_launch_started(launch, capsys, where):
    error = RuntimeError("Could not reach host. Are you offline?")
    if where == "driver-manager":
        launch.install = error                                # chromedriver-manager's online check (live 2026-10-07)
    else:
        launch.chrome = error

    assert mainfetch.init_driver("tv") is None

    out = capsys.readouterr().out
    assert "❌ Selenium Connection Error: Could not reach host. Are you offline?" in out
    assert ENDED in out
    assert launch.table.terminated == [4242] and not launch.proc.running
    assert launch.marks == [(TV, 0), (TV, GONE)]              # marked before the launch, and once it is gone


def test_a_photos_tab_that_cannot_be_opened_ends_the_chrome_after_the_selenium_session(launch, capsys):
    launch.tab = RuntimeError("cannot open a new tab")

    assert mainfetch.init_driver("tv") is None

    out = capsys.readouterr().out
    assert "❌ Could not open a photos.google.com tab in the attached Chrome: cannot open a new tab" in out
    assert launch.driver.quits == 1 and ENDED in out and not launch.proc.running
    assert out.index("Could not open a photos.google.com tab") < out.index(ENDED)


def test_when_taskkill_fails_the_handle_ends_it(launch, capsys):
    launch.chrome = RuntimeError("session not created")
    launch.table.taskkill = False

    assert mainfetch.init_driver("tv") is None
    assert launch.proc.kills == 1 and not launch.proc.running and ENDED in capsys.readouterr().out


def test_a_chrome_that_cannot_be_ended_is_one_warning_never_an_exception(launch, capsys):
    launch.chrome = RuntimeError("session not created")
    launch.table.taskkill = False
    launch.proc.unkillable = True

    assert mainfetch.init_driver("tv") is None

    out = capsys.readouterr().out
    assert ("   > ⚠️ The Chrome this launch started (pid 4242) could not be ended (RuntimeError) — close it before "
            "the next fetch.") in out
    assert ENDED not in out and launch.marks == [(TV, 0)]     # still running: its profile is never marked


def test_a_hand_over_to_a_chrome_already_running_is_never_touched(launch, capsys):
    # chrome.exe exits at once when its profile's Chrome already runs: that Chrome may hold the user's own windows.
    launch.proc.running = False
    launch.chrome = RuntimeError("cannot connect to chrome at 127.0.0.1:9222")

    assert mainfetch.init_driver("tv") is None

    assert launch.table.terminated == [] and launch.proc.kills == 0
    assert "Closed the Chrome" not in capsys.readouterr().out and launch.marks == [(TV, 0)]


def test_a_launch_that_failed_has_nothing_to_end(launch, monkeypatch, capsys):
    def popen(cmd, *args, **kwargs):
        raise OSError("chrome.exe not found")

    monkeypatch.setattr(mainfetch.subprocess, "Popen", popen)

    assert mainfetch.init_driver("tv") is None
    assert "❌ Failed to launch Chrome binary: chrome.exe not found" in capsys.readouterr().out
    assert launch.table.terminated == []


@pytest.mark.parametrize("where", ["settling", "attach", "photos-tab"])
def test_ctrl_c_during_the_launch_ends_the_chrome_and_still_stops(launch, monkeypatch, capsys, where):
    if where == "settling":
        def sleep(seconds=0):
            raise KeyboardInterrupt
        monkeypatch.setattr(mainfetch.time, "sleep", sleep)
    elif where == "attach":
        launch.chrome = KeyboardInterrupt()
    else:
        launch.tab = KeyboardInterrupt()

    with pytest.raises(KeyboardInterrupt):
        mainfetch.init_driver("tv")

    assert launch.table.terminated == [4242] and not launch.proc.running and ENDED in capsys.readouterr().out
    assert launch.driver.quits == (1 if where == "photos-tab" else 0)


def test_the_session_bounds_its_page_loads_before_the_first_navigation(launch, monkeypatch):
    events = []
    launch.driver.set_page_load_timeout = lambda seconds: events.append(("bound", seconds))
    monkeypatch.setattr(mainfetch, "use_photos_tab", lambda driver: events.append(("tab",)) or "current")

    assert mainfetch.init_driver("tv") is launch.driver

    assert events == [("bound", mainfetch.FETCH_PAGE_LOAD_S), ("tab",)]
    assert launch.table.terminated == [] and launch.proc.running        # a good attach keeps its Chrome
    assert launch.driver._mv_chrome_proc is launch.proc


# ===========================================================================
# 4. every driver.get is bounded — the fetch's sessions and the crawler's
# ===========================================================================
def test_bound_page_loads_sets_the_bound_and_never_raises():
    seen = []
    assert mainfetch.bound_page_loads(types.SimpleNamespace(set_page_load_timeout=seen.append)) is True
    assert seen == [mainfetch.FETCH_PAGE_LOAD_S]
    assert mainfetch.FETCH_PAGE_LOAD_S < 300                              # below Selenium's own default

    def refuse(seconds):
        raise RuntimeError("session deleted")

    assert mainfetch.bound_page_loads(types.SimpleNamespace(set_page_load_timeout=refuse)) is False
    assert mainfetch.bound_page_loads(object()) is False                 # a stand-in without the setter


def test_the_crawler_bounds_its_page_loads_too(monkeypatch):
    from tools import gp_inventory
    seen = []
    driver = types.SimpleNamespace(set_page_load_timeout=seen.append)
    monkeypatch.setattr(gp_inventory.webdriver, "Chrome", lambda options=None: driver)

    assert gp_inventory.connect(9225) is driver
    assert seen == [mainfetch.FETCH_PAGE_LOAD_S]


# ===========================================================================
# 5. warm_profiles closes the Chrome it launched — and never attaches to the previous profile's
# ===========================================================================
@pytest.fixture()
def warm(monkeypatch, tmp_path, clock, marks):
    """warm_profiles over fake sessions: init_driver hands out a _Browser per profile whose chrome.exe (pid 5000 + n) was
    LAUNCHED and listens on the debug port (CDP Browser.close ends it — unless state.closes is False), or — for the
    profiles in state.handed_over — a hand-over to a Chrome already running (the browser on the port is pid 8000, which
    the table names with that profile). The port probe and the toast are recorders; the log goes under tmp_path."""
    from tools import warm_profiles
    import mvcommon
    state = types.SimpleNamespace(launched=[], browsers={}, port_probes=[], port_free=True, closes=True,
                                  handed_over=set(), logged_out=set(), toasts=[], marks=marks, wp=warm_profiles)
    state.table = _install(monkeypatch, _Table())

    def init(key):
        profile = mainfetch.CHROME_PROFILES[key]
        proc = _Proc(5000 + len(state.launched), running=key not in state.handed_over)
        state.launched.append(key)
        browser_pid = 8000 if key in state.handed_over else proc.pid
        state.table.procs.append(proc)
        state.table.answers[browser_pid] = (True, _cmd(profile))
        state.table.on_port = browser_pid
        url = "https://accounts.google.com/signin" if key in state.logged_out else "https://photos.google.com/"
        browser = _Browser(proc, closes=state.closes, url=url)
        browser._mv_chrome_proc, browser._mv_user_data_dir = proc, profile
        state.browsers[key] = browser
        return browser

    def port_free(*args, **kwargs):
        state.port_probes.append(1)
        return state.port_free

    monkeypatch.setattr(mainfetch, "init_driver", init)
    monkeypatch.setattr(mainfetch, "_debug_port_free", port_free)
    monkeypatch.setattr(warm_profiles, "fetch_session_lock", lambda *a, **k: contextlib.nullcontext())
    monkeypatch.setattr(warm_profiles, "time", types.SimpleNamespace(sleep=lambda seconds: None))
    monkeypatch.setattr(warm_profiles, "send_toast", lambda *a, **k: state.toasts.append(a))
    monkeypatch.setattr(warm_profiles, "LOG_PATH", str(tmp_path / "warm.log"))
    monkeypatch.setattr(mvcommon, "MV_LOG_DIR", str(tmp_path))
    return state


def test_the_warm_up_closes_each_chrome_it_launched_before_the_next_profile(warm, capsys):
    assert warm.wp.warm_all(["tv", "anime"]) == 0

    out = capsys.readouterr().out
    for key in ("tv", "anime"):
        browser = warm.browsers[key]
        assert browser.cdp.count("Browser.close") == 1 and not browser.proc.running and browser.quits == 1
        assert f"   > 🧹 Chrome ('{key}') closed — the next fetch starts a fresh one." in out
    assert warm.port_probes == []                             # the previous Chrome exited: nothing to probe
    assert warm.table.terminated == []                        # a graceful close, no kill
    assert warm.marks == [(mainfetch.CHROME_PROFILES["tv"], GONE), (mainfetch.CHROME_PROFILES["anime"], GONE)]
    assert out.index("Chrome ('tv') closed") < out.index("Warming profile 'anime'")


def test_a_launched_chrome_that_ignores_the_close_is_terminated_as_the_fetch_does(warm, capsys):
    warm.closes = False

    assert warm.wp.warm_all(["tv"]) == 0

    assert warm.table.terminated == [5000] and not warm.browsers["tv"].proc.running
    assert "   > 🧹 Chrome ('tv') terminated (pid 5000) — the next fetch starts a fresh one." in capsys.readouterr().out


def test_a_logged_out_profile_is_closed_too(warm, capsys):
    warm.logged_out.add("tv")

    assert warm.wp.warm_all(["tv"]) == 1

    assert not warm.browsers["tv"].proc.running and warm.browsers["tv"].quits == 1
    with open(warm.wp.LOG_PATH, encoding="utf-8") as fh:
        assert "tv=LOGGED_OUT" in fh.read()


def test_the_next_profile_never_attaches_to_a_chrome_still_on_the_port(warm, capsys):
    # The first profile's chrome.exe handed over to a Chrome that already ran (attached): that Chrome keeps its windows
    # (only the warm-up's own tab closes) — and may still hold port 9222, which is probed before the next launch.
    warm.handed_over.add("tv")
    warm.port_free = False

    assert warm.wp.warm_all(["tv", "anime", "movies"]) == 1

    out = capsys.readouterr().out
    assert warm.launched == ["tv"]                            # anime / movies were never launched onto it
    assert warm.port_probes == [1, 1]
    assert warm.table.terminated == [] and warm.browsers["tv"].tabs == []
    assert "Closed 1 tab(s) this fetch opened" in out and "Browser.close" not in warm.browsers["tv"].cdp
    for key in ("anime", "movies"):
        assert (f"   > ❌ Profile '{key}' not warmed: Chrome's debug port 9222 is still in use (the previous "
                f"profile's Chrome did not close) — close that Chrome, then re-run.") in out
    with open(warm.wp.LOG_PATH, encoding="utf-8") as fh:
        log = fh.read()
    assert "tv=OK" in log and "anime=LAUNCH_FAIL" in log and "movies=LAUNCH_FAIL" in log
    assert len(warm.toasts) == 1


def test_a_port_freed_in_time_lets_the_next_profile_warm(warm):
    warm.handed_over.add("tv")

    assert warm.wp.warm_all(["tv", "anime"]) == 0
    assert warm.launched == ["tv", "anime"] and warm.port_probes == [1]


def test_stand_in_drivers_never_probe_the_real_port(monkeypatch, tmp_path):
    # tests/test_warm_profiles.py drives warm_all with stand-ins that speak no DevTools protocol: no Chrome is behind
    # them, so the warm-up must not touch the machine's real port 9222 (hermetic) — and they end as before.
    from tools import warm_profiles
    import mvcommon
    quits = []
    stand_in = types.SimpleNamespace(current_url="https://photos.google.com/", get=lambda url: None,
                                     quit=lambda: quits.append(1))

    def no_probe(*args, **kwargs):
        raise AssertionError("the real debug port was probed")

    monkeypatch.setattr(mainfetch, "init_driver", lambda key: stand_in)
    monkeypatch.setattr(mainfetch, "_debug_port_free", no_probe)
    monkeypatch.setattr(warm_profiles, "fetch_session_lock", lambda *a, **k: contextlib.nullcontext())
    monkeypatch.setattr(warm_profiles, "time", types.SimpleNamespace(sleep=lambda seconds: None))
    monkeypatch.setattr(warm_profiles, "LOG_PATH", str(tmp_path / "warm.log"))
    monkeypatch.setattr(mvcommon, "MV_LOG_DIR", str(tmp_path))

    assert warm_profiles.warm_all(["tv", "anime"]) == 0
    assert quits == [1, 1]
