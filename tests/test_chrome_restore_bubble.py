"""IMP-C25 — no "Restore pages?" bubble in the automation Chrome (user request 2026-10-08).

User, verbatim: "also, make sure to close the restore small box which opens in chrome. in right top." Chrome opens with
"Restore pages? Chrome didn't shut down correctly." when its profile's Preferences record an exit that was not clean
(profile.exit_type "Crashed"). Chrome 154 ignores --disable-session-crashed-bubble and — seen live 2026-10-08 — leaves
"Crashed" even after a graceful CDP Browser.close. So:
  * every launch also passes --hide-crash-restore-bubble (init_driver: the fetch and the profile warm-up; the
    crawler's launch_chrome);
  * mainfetch.mark_profile_clean_exit sets exit_type "Normal" + exited_cleanly true — only while no Chrome browser runs
    on that profile, atomically, every other key kept, never raising — before every launch, and once a Chrome a run
    closed (gracefully or not) is gone.

Hermetic: every profile is a folder under tmp_path, the process table and the clock are fakes; the real helper runs only
through the `real_mark_profile_clean_exit` fixture (tmp_path only, asserted). No Chrome, no chromedriver, no PowerShell.
"""
import json
import os
import pathlib
import types

import pytest

import mainfetch
from tools import gp_inventory
from tools import warm_profiles

CHROME_EXE = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
BUBBLE_FLAGS = ("--disable-session-crashed-bubble", "--hide-crash-restore-bubble")
WARN = "   > WARNING: Chrome profile not marked as closed cleanly — "
GONE = mainfetch.CHROME_GONE_WAIT_S


def _browser_cmd(user_data_dir):
    """The command line of a Chrome BROWSER process on `user_data_dir` (no --type=)."""
    return (f'"{CHROME_EXE}" --user-data-dir={user_data_dir} --profile-directory=Default '
            f'--remote-debugging-port=9222 --disable-quic about:blank')


def _helper_cmd(user_data_dir, kind="crashpad-handler"):
    """A helper process of that Chrome: it names the profile too, but carries --type= (it may outlive the browser)."""
    return f'"{CHROME_EXE}" --type={kind} "--user-data-dir={user_data_dir}" /prefetch:7'


def _chrome_bytes(doc):
    """JSON as Chrome writes Preferences: compact UTF-8."""
    return json.dumps(doc, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


# A Preferences document with the shapes Chrome writes (synthetic values): nested objects and lists, non-ASCII and
# escaped text, integers of any size, floats, booleans, null, empty containers — and the unclean exit Chrome 154 leaves
# even after a graceful close: exit_type "Crashed", no exited_cleanly key.
PREFS = {
    "account_info": [{"email": "someone@example.invalid", "full_name": "Zoë Ünïcode 名前 😀", "given_name": "Zoë"}],
    "browser": {"window_placement": {"bottom": 1080, "left": 0, "maximized": False, "right": 1920, "top": 0}},
    "download": {"default_directory": "D:\\MV_fetch\\x", "directory_upgrade": True, "prompt_for_download": False},
    "profile": {"avatar_index": 26, "content_settings": {"exceptions": {}}, "created_by_version": "154.0.7420.0",
                "exit_type": "Crashed", "name": "Person 1", "password_manager_enabled": False},
    "sessions": {"event_log": [{"crashed": False, "time": "13403456789012345", "type": 0}]},
    "site_engagement": {"last_engagement_time": 1.3403456789012345e+16, "raw_score": 3.5, "zero": 0.0},
    "big_int": 123456789012345678901234567890,
    "text": "<b>tag</b> \u2028 \"quoted\" \\ back / slash \t tab",
    "empty": {}, "nothing": [], "null": None,
}


class _Table:
    """mainfetch.PROCESS_TABLE for the helper: chrome_command_lines() gives `answers` in turn, the last one repeating
    (a list of command lines, None in it = one that cannot be read; None = the process table cannot be read).
    on_ask(n) runs before the n-th answer — a Chrome that still writes on its way out."""

    def __init__(self, *answers):
        self.answers, self.asked = list(answers) or [[]], 0
        self.on_ask = lambda n: None

    def chrome_command_lines(self):
        self.asked += 1
        self.on_ask(self.asked)
        return self.answers[min(self.asked, len(self.answers)) - 1]


class _Clock:
    """mainfetch's `time`: sleep() only advances the clock."""

    def __init__(self):
        self.now, self.slept = 1000.0, 0.0

    def time(self):
        return self.now

    def sleep(self, seconds=0):
        self.now += seconds
        self.slept += seconds


@pytest.fixture()
def table(monkeypatch):
    """table(*answers) installs a fresh _Table as mainfetch.PROCESS_TABLE (and a fake clock) and returns it."""
    monkeypatch.setattr(mainfetch, "time", _Clock())

    def install(*answers):
        t = _Table(*answers)
        monkeypatch.setattr(mainfetch, "PROCESS_TABLE", t)
        return t

    return install


def _profile(tmp_path, prefs=PREFS, raw=None, name="ChromeProfile_TV"):
    """A Chrome user-data-dir under tmp_path; Default/Preferences holds `raw` bytes, else `prefs` as Chrome writes it
    (both None: no Preferences file). -> (user_data_dir, the Preferences path)."""
    root = tmp_path / name
    (root / "Default").mkdir(parents=True)
    path = root / "Default" / "Preferences"
    if raw is not None:
        path.write_bytes(raw)
    elif prefs is not None:
        path.write_bytes(_chrome_bytes(prefs))
    return str(root), path


def _snapshot(root):
    """{path relative to the profile folder: (bytes, mtime_ns)} of every file in it."""
    files = {}
    for folder, _, names in os.walk(root):
        for name in names:
            path = os.path.join(folder, name)
            with open(path, "rb") as fh:
                files[os.path.relpath(path, root)] = (fh.read(), os.stat(path).st_mtime_ns)
    return files


def _with_exit(**profile_keys):
    """PREFS whose profile records exactly these exit keys."""
    prefs = json.loads(json.dumps(PREFS))
    for key in ("exit_type", "exited_cleanly"):
        prefs["profile"].pop(key, None)
    prefs["profile"].update(profile_keys)
    return prefs


# ===========================================================================
# the helper — mark_profile_clean_exit
# ===========================================================================
@pytest.mark.parametrize("exit_keys", [
    {"exit_type": "Crashed"},                             # Chrome 154 after any close — no exited_cleanly key (live)
    {"exit_type": "Crashed", "exited_cleanly": False},    # an older Chrome after a crash
    {"exit_type": "Normal", "exited_cleanly": False},     # the older key alone
])
def test_an_unclean_exit_is_marked_clean_and_nothing_else_changes(tmp_path, table, capsys, exit_keys,
                                                                  real_mark_profile_clean_exit):
    prefs = _with_exit(**exit_keys)
    user_data_dir, path = _profile(tmp_path, prefs)
    t = table([])                                           # no Chrome runs on it

    assert real_mark_profile_clean_exit(user_data_dir) == "marked"

    expected = json.loads(json.dumps(prefs))
    expected["profile"].update(exit_type="Normal", exited_cleanly=True)
    assert path.read_bytes() == _chrome_bytes(expected)     # byte for byte: the two values, nothing else, same order
    after = json.loads(path.read_bytes())
    assert {k: json.dumps(v) for k, v in after.items() if k != "profile"} == \
        {k: json.dumps(v) for k, v in prefs.items() if k != "profile"}
    assert {k: v for k, v in after["profile"].items() if k not in ("exit_type", "exited_cleanly")} == \
        {k: v for k, v in prefs["profile"].items() if k not in ("exit_type", "exited_cleanly")}
    assert t.asked == 1
    out = capsys.readouterr().out
    assert out == f'   > Chrome profile marked as closed cleanly (no "Restore pages?"): {user_data_dir}\n'
    out.encode("cp1252")        # the crawler's output is often a cp1252 pipe: an emoji here would stop it


def test_chromes_own_number_and_escape_forms_keep_their_values(tmp_path, table, real_mark_profile_clean_exit):
    # Chrome writes "<" as \u003C and a double as 13403456789012345.0; Python writes the same values as "<" and
    # 1.3403456789012345e+16 — the JSON values stay identical, which is all Chrome reads back.
    raw = (b'{"a":"\\u003Cb>\\u2028\\/","n":13403456789012345.0,"i":-7,"f":0.1,"t":true,'
           b'"profile":{"exit_type":"Crashed","name":"x"}}')
    user_data_dir, path = _profile(tmp_path, raw=raw)
    table([])

    assert real_mark_profile_clean_exit(user_data_dir) == "marked"

    before, after = json.loads(raw), json.loads(path.read_bytes())
    before["profile"].update(exit_type="Normal", exited_cleanly=True)
    assert after == before and list(after) == list(before) and list(after["profile"]) == list(before["profile"])
    assert type(after["n"]) is float and type(after["i"]) is int


@pytest.mark.parametrize("exit_keys", [
    {"exit_type": "Normal"},                              # Chrome 154 after a clean exit
    {"exit_type": "Normal", "exited_cleanly": True},
    {"exit_type": "SessionEnded"},                        # Windows shut down with it open: no bubble either
    {},                                                   # no exit recorded at all
])
def test_a_profile_without_an_unclean_exit_is_only_read(tmp_path, table, capsys, exit_keys,
                                                        real_mark_profile_clean_exit):
    user_data_dir, _ = _profile(tmp_path, _with_exit(**exit_keys))
    before = _snapshot(user_data_dir)
    t = table([_browser_cmd(user_data_dir)])

    assert real_mark_profile_clean_exit(user_data_dir) == "clean"

    assert _snapshot(user_data_dir) == before and t.asked == 0     # not even the process table is asked
    assert capsys.readouterr().out == ""


@pytest.mark.parametrize("form", ["plain", "quoted", "other case"])
def test_a_profile_a_chrome_still_runs_on_is_never_written(tmp_path, table, capsys, form,
                                                           real_mark_profile_clean_exit):
    user_data_dir, _ = _profile(tmp_path)
    cmdline = {"plain": _browser_cmd(user_data_dir),
               "quoted": f'"{CHROME_EXE}" "--user-data-dir={user_data_dir}" --profile-directory=Default',
               "other case": _browser_cmd(user_data_dir.upper())}[form]          # Windows paths: case-insensitive
    before = _snapshot(user_data_dir)
    t = table([_browser_cmd(r"C:\Users\x\Other Profile"), cmdline])

    assert real_mark_profile_clean_exit(user_data_dir) == "running"

    assert _snapshot(user_data_dir) == before                      # a running Chrome writes Preferences itself
    assert t.asked == 1 and mainfetch.time.slept == 0              # before a launch: asked once, never waited for
    assert capsys.readouterr().out == ""


def test_only_a_browser_on_exactly_that_profile_counts(tmp_path, table, real_mark_profile_clean_exit):
    user_data_dir, path = _profile(tmp_path)
    table([_helper_cmd(user_data_dir),                             # its crashpad handler, still exiting
           _helper_cmd(user_data_dir, "renderer"),
           _browser_cmd(user_data_dir + "2"),                      # ...ChromeProfile_TV2 shares a prefix only
           f'"{CHROME_EXE}" --no-startup-window /prefetch:5'])     # the user's own Chrome (its default profile)

    assert real_mark_profile_clean_exit(user_data_dir) == "marked"

    assert json.loads(path.read_bytes())["profile"]["exit_type"] == "Normal"


@pytest.mark.parametrize("answer", [None, [None], [_helper_cmd(r"C:\x"), None]],
                         ids=["process table unreadable", "a command line unreadable", "one of two unreadable"])
def test_when_it_cannot_be_told_whether_chrome_runs_nothing_is_written(tmp_path, table, capsys, answer,
                                                                       real_mark_profile_clean_exit):
    user_data_dir, _ = _profile(tmp_path)
    before = _snapshot(user_data_dir)
    table(answer)

    assert real_mark_profile_clean_exit(user_data_dir, wait=GONE) == "skipped"

    assert _snapshot(user_data_dir) == before and mainfetch.time.slept == 0     # "cannot tell" is never waited out
    assert capsys.readouterr().out == f"{WARN}it cannot be told whether a Chrome still runs on {user_data_dir}.\n"


def test_a_just_closed_chrome_is_waited_for_then_its_profile_is_marked(tmp_path, table, real_mark_profile_clean_exit):
    user_data_dir, path = _profile(tmp_path)
    t = table([_browser_cmd(user_data_dir)], [_browser_cmd(user_data_dir)], [_helper_cmd(user_data_dir)])

    assert real_mark_profile_clean_exit(user_data_dir, wait=GONE) == "marked"

    assert t.asked == 3 and mainfetch.time.slept == 2 * mainfetch._UNLOCK_POLL_S
    assert json.loads(path.read_bytes())["profile"]["exit_type"] == "Normal"


def test_a_chrome_that_outlives_the_wait_is_left_alone(tmp_path, table, capsys, real_mark_profile_clean_exit):
    user_data_dir, _ = _profile(tmp_path)
    before = _snapshot(user_data_dir)
    table([_browser_cmd(user_data_dir)])

    assert real_mark_profile_clean_exit(user_data_dir, wait=3) == "running"

    assert 3 <= mainfetch.time.slept <= 3 + mainfetch._UNLOCK_POLL_S
    assert _snapshot(user_data_dir) == before and capsys.readouterr().out == ""


def test_what_a_closing_chrome_writes_last_is_kept(tmp_path, table, real_mark_profile_clean_exit):
    # Read while Chrome is still on its way out (closing its windows, it rewrites Preferences): the mark goes onto
    # what it wrote last — the file read afresh once it is gone — never onto the earlier read.
    user_data_dir, path = _profile(tmp_path)
    t = table([_browser_cmd(user_data_dir)], [])
    last_word = dict(PREFS, browser={"window_placement": {"bottom": 900}})
    t.on_ask = lambda n: path.write_bytes(_chrome_bytes(last_word)) if n == 1 else None

    assert real_mark_profile_clean_exit(user_data_dir, wait=GONE) == "marked"

    after = json.loads(path.read_bytes())
    assert after["browser"] == {"window_placement": {"bottom": 900}} and after["profile"]["exit_type"] == "Normal"


@pytest.mark.parametrize("raw, why", [
    (None, "does not exist"),                             # the profile folder exists, Chrome never wrote one
    (b"{not json", "is not valid JSON"),
    (b'{"profile":{"exit_type":"Crash', "is not valid JSON"),     # cut short
    (b"\xff\xfe\x00\x81garbage", "is not valid JSON"),
    (b"", "is not valid JSON"),
    (b"[1, 2]", "is not a JSON object"),
    (b'"Preferences"', "is not a JSON object"),
    (b'{"profile":"Crashed"}', "is not a JSON object"),
    ("a folder", "could not be read"),
], ids=["missing", "not json", "cut short", "binary", "empty", "array", "string", "profile not an object", "folder"])
def test_missing_or_corrupt_preferences_are_left_as_they_are_with_one_line(tmp_path, table, capsys, raw, why,
                                                                           real_mark_profile_clean_exit):
    user_data_dir, path = _profile(tmp_path, prefs=None, raw=raw if isinstance(raw, bytes) else None)
    if raw == "a folder":
        path.mkdir()
    before, listing = _snapshot(user_data_dir), sorted(os.listdir(path.parent))
    t = table([])

    assert real_mark_profile_clean_exit(user_data_dir) == "skipped"           # never raises

    assert _snapshot(user_data_dir) == before and sorted(os.listdir(path.parent)) == listing
    assert t.asked == 0
    out = capsys.readouterr().out
    assert out.startswith(f"{WARN}{path} {why}") and out.count("\n") == 1
    out.encode("cp1252")        # the crawler's output is often a cp1252 pipe: an emoji here would stop it


def test_a_profile_folder_that_does_not_exist_is_not_created(tmp_path, table, capsys, real_mark_profile_clean_exit):
    user_data_dir = str(tmp_path / "ChromeProfile_Others")
    table([])

    assert real_mark_profile_clean_exit(user_data_dir) == "skipped"

    assert not os.path.exists(user_data_dir) and "does not exist" in capsys.readouterr().out


def test_a_value_json_cannot_carry_is_never_written_back(tmp_path, table, capsys, real_mark_profile_clean_exit):
    # Python reads NaN (Chrome never writes it); written back, Chrome would reject the whole file — so it is refused.
    raw = b'{"x":NaN,"profile":{"exit_type":"Crashed"}}'
    user_data_dir, _ = _profile(tmp_path, raw=raw)
    before = _snapshot(user_data_dir)
    table([])

    assert real_mark_profile_clean_exit(user_data_dir) == "skipped"

    assert _snapshot(user_data_dir) == before
    assert "holds a value that cannot be written back" in capsys.readouterr().out


def test_an_unexpected_error_is_one_line_never_an_exception(tmp_path, monkeypatch, capsys,
                                                            real_mark_profile_clean_exit):
    user_data_dir, _ = _profile(tmp_path)

    def boom(*args):
        raise RuntimeError("unexpected")

    monkeypatch.setattr(mainfetch, "_mark_clean_exit", boom)

    assert real_mark_profile_clean_exit(user_data_dir) == "skipped"
    assert capsys.readouterr().out == f"{WARN}RuntimeError: unexpected.\n"


def test_the_write_is_atomic_through_a_sibling_temp_file(tmp_path, table, monkeypatch, real_mark_profile_clean_exit):
    user_data_dir, path = _profile(tmp_path)
    old = path.read_bytes()
    table([])
    swaps, real_replace = [], os.replace

    def replace(src, dst):
        # at the swap: the new file is complete beside the old one, which is still untouched
        with open(src, "rb") as fh:
            new = json.loads(fh.read())
        swaps.append((os.path.dirname(src) == os.path.dirname(dst), os.path.normcase(dst) == os.path.normcase(str(path)),
                      new["profile"]["exit_type"], path.read_bytes() == old))
        return real_replace(src, dst)

    monkeypatch.setattr(mainfetch.os, "replace", replace)

    assert real_mark_profile_clean_exit(user_data_dir) == "marked"

    assert swaps == [(True, True, "Normal", True)]
    assert os.listdir(path.parent) == ["Preferences"]                         # no temp file left behind


@pytest.mark.parametrize("fails", ["replace", "fsync"])
def test_a_failed_write_leaves_the_old_file_and_no_temp_file(tmp_path, table, monkeypatch, capsys, fails,
                                                             real_mark_profile_clean_exit):
    # e.g. Chrome or an antivirus holds Preferences at the swap, or the disk fails while the temp file is written
    user_data_dir, path = _profile(tmp_path)
    before = _snapshot(user_data_dir)
    table([])

    def refuse(*args, **kwargs):
        raise PermissionError(13, "The process cannot access the file")

    monkeypatch.setattr(mainfetch.os, fails, refuse)

    assert real_mark_profile_clean_exit(user_data_dir) == "skipped"

    assert _snapshot(user_data_dir) == before                                 # the old file — and nothing else
    out = capsys.readouterr().out
    assert out.startswith(f"{WARN}{path} could not be written") and out.count("\n") == 1


def test_no_other_file_of_the_profile_is_touched(tmp_path, table, real_mark_profile_clean_exit):
    user_data_dir, _ = _profile(tmp_path)
    root = pathlib.Path(user_data_dir)
    (root / "Local State").write_bytes(b'{"profile":{"info_cache":{}},"exited_cleanly":false}')
    (root / "Default" / "Secure Preferences").write_bytes(b'{"protection":{"macs":{}}}')
    (root / "Default" / "Cookies").write_bytes(b"SQLite format 3\x00")
    before = _snapshot(user_data_dir)
    table([])

    assert real_mark_profile_clean_exit(user_data_dir) == "marked"

    after = _snapshot(user_data_dir)
    assert {name for name in before.keys() | after.keys() if before.get(name) != after.get(name)} == {
        os.path.join("Default", "Preferences")}


class _Run:
    def __init__(self, returncode=0, stdout=""):
        self.returncode, self.stdout = returncode, stdout


@pytest.mark.parametrize("answer, expected", [
    (_Run(0, 'OK\r\nCMD "C:\\chrome.exe" --user-data-dir=C:\\x\r\nCMD?\r\n'),
     ['"C:\\chrome.exe" --user-data-dir=C:\\x', None]),          # one command line that cannot be read: None
    (_Run(0, "\ufeffOK\r\n"), []),                                 # no chrome.exe runs at all
    (_Run(1, "OK\n"), None),
    (_Run(0, "Get-CimInstance : Access denied\n"), None),          # -ErrorAction Stop: never a false "none runs"
    (_Run(0, ""), None),
    (OSError("powershell not found"), None),
])
def test_the_windows_chrome_query_never_guesses(monkeypatch, answer, expected):
    seen = []

    def run(argv, **kwargs):
        seen.append(argv)
        if isinstance(answer, Exception):
            raise answer
        return answer

    monkeypatch.setattr(mainfetch.subprocess, "run", run)

    assert mainfetch._ProcessTable().chrome_command_lines() == expected
    assert seen[0][:4] == ["powershell", "-NoProfile", "-NonInteractive", "-Command"]
    assert "Get-CimInstance Win32_Process -Filter 'Name=''chrome.exe''' -ErrorAction Stop" in seen[0][4]


def test_every_test_sees_an_inert_mark_profile_clean_exit(tmp_path, table, capsys):
    # tests/conftest.py: the code under test often holds the REAL profile folders (C:\Media\Utils\ChromeProfile*), so
    # the module attribute every caller uses reads and writes nothing in any test.
    user_data_dir, path = _profile(tmp_path)
    before = path.read_bytes()
    t = table([])

    assert mainfetch.mark_profile_clean_exit(user_data_dir) is None

    assert path.read_bytes() == before and t.asked == 0 and capsys.readouterr().out == ""


# ===========================================================================
# every launch: the profile is marked first, and the bubble switches are passed
# ===========================================================================
@pytest.fixture()
def launches(monkeypatch):
    """Every Chrome launch path, run for real without Chrome or chromedriver: Popen records the command, the attach
    returns a stand-in already on photos.google.com, and mark_profile_clean_exit records its calls in the same list
    (their order shows)."""
    events = []
    browser = types.SimpleNamespace(current_url="https://photos.google.com/", get=lambda url: None,
                                    quit=lambda: events.append(("quit",)))

    def popen(cmd, *args, **kwargs):
        events.append(("launch", list(cmd)))
        return types.SimpleNamespace(pid=4242, poll=lambda: None)

    monkeypatch.setattr(mainfetch.subprocess, "Popen", popen)            # gp_inventory's subprocess is the same module
    monkeypatch.setattr(mainfetch, "ChromeDriverManager",
                        lambda *a, **k: types.SimpleNamespace(install=lambda: "chromedriver.exe"))
    monkeypatch.setattr(mainfetch, "Service", lambda *a, **k: "service")
    monkeypatch.setattr(mainfetch.webdriver, "Chrome", lambda service=None, options=None: browser)
    monkeypatch.setattr(mainfetch, "use_photos_tab", lambda driver: "current")
    monkeypatch.setattr(mainfetch, "time", _Clock())                     # init_driver's settle wait
    monkeypatch.setattr(warm_profiles, "time", types.SimpleNamespace(sleep=lambda seconds: None))
    monkeypatch.setattr(mainfetch, "mark_profile_clean_exit",
                        lambda user_data_dir, profile="Default", wait=0: events.append(("mark", user_data_dir, wait)))
    return events


LAUNCHERS = {
    "fetch (init_driver)": lambda: mainfetch.init_driver("tv"),
    "profile warm-up": lambda: warm_profiles._warm_one("tv"),
    "inventory crawler": lambda: gp_inventory.launch_chrome(mainfetch.CHROME_PROFILES["tv"], 9225),
}


@pytest.mark.parametrize("launcher", list(LAUNCHERS))
def test_every_launch_marks_its_profile_first_and_hides_the_bubble(launches, launcher):
    LAUNCHERS[launcher]()

    profile = mainfetch.CHROME_PROFILES["tv"]
    [launch] = [event for event in launches if event[0] == "launch"]
    assert [event for event in launches if event[0] == "mark"] == [("mark", profile, 0)]   # once, never waiting
    assert launches.index(("mark", profile, 0)) < launches.index(launch)                  # BEFORE Chrome starts
    assert f"--user-data-dir={profile}" in launch[1]
    assert all(flag in launch[1] for flag in BUBBLE_FLAGS)


# ===========================================================================
# every close: once the Chrome a run closed is gone, its profile is marked
# ===========================================================================
class _Proc:
    """The Popen of the Chrome a run launched: poll() is None while it runs."""

    def __init__(self, pid, running=True):
        self.pid, self.running = pid, running

    def poll(self):
        return None if self.running else 0


class _Procs:
    """mainfetch.PROCESS_TABLE for the close paths: `alive` {pid: command line}; terminate() ends the pid (and its
    Popen). Whether a Chrome still runs on the profile is the helper's question — a recorder stands in for it here."""

    def __init__(self, events, alive=None, on_port=None, popens=()):
        self.events, self.alive, self.on_port, self.popens = events, dict(alive or {}), on_port, list(popens)

    def query(self, pid):
        return (True, self.alive[pid]) if pid in self.alive else (False, None)

    def listener(self, port):
        return self.on_port if port == 9222 else None

    def terminate(self, pid):
        self.events.append(("terminate", pid))
        self.alive.pop(pid, None)
        for popen in self.popens:
            if popen.pid == pid:
                popen.running = False
        return True


class _Driver:
    """The browser Selenium drives, as _close_session uses it: CDP (Browser.close ends the launched Chrome unless it
    `exits` is False) and its windows — one, the tab the run opened."""

    def __init__(self, events, popen, exits=True):
        self.events, self.popen, self.exits = events, popen, exits
        self.tabs, self.current, self._mv_opened_tabs = ["RUN"], None, ["RUN"]
        self.switch_to = types.SimpleNamespace(window=lambda handle: setattr(self, "current", handle))

    def execute_cdp_cmd(self, cmd, params):
        if cmd == "Target.getTargets":
            return {"targetInfos": [{"targetId": h, "type": "page", "url": "https://photos.google.com/"}
                                    for h in self.tabs]}
        self.events.append(("cdp", cmd))
        if cmd == "Browser.close" and self.exits and self.popen is not None:
            self.popen.running = False
        return {}

    @property
    def window_handles(self):
        return list(self.tabs)

    def close(self):
        self.events.append(("close tab", self.current))
        self.tabs.remove(self.current)


@pytest.fixture()
def closing(tmp_path, monkeypatch):
    """A session at its end: the profile folder (under tmp_path), the fake clock, and a recorder standing in for
    mark_profile_clean_exit that notes whether the launched Chrome (st.popen) still ran at that moment."""
    st = types.SimpleNamespace(events=[], profile=str(tmp_path / "ChromeProfile_TV"), popen=None)
    monkeypatch.setattr(mainfetch, "time", _Clock())

    def mark(user_data_dir, profile="Default", wait=0):
        st.events.append(("mark", user_data_dir, wait, st.popen.running if st.popen else None))
        return "marked"

    monkeypatch.setattr(mainfetch, "mark_profile_clean_exit", mark)
    return st


def _ctx(user_data_dir, pid, popen, launched=True, journal=False):
    """A run's context as _note_session leaves it (a journal when asked)."""
    ctx = mainfetch.FetchContext()
    ctx.chrome = {"account": "tv", "proc": popen, "pid": pid, "user_data_dir": user_data_dir, "launched": launched}
    if journal:
        ctx.journal = mainfetch.FetchJournal(ctx.run_id)
    return ctx


@pytest.mark.parametrize("last", [True, False], ids=["end of the run", "account switch"])
def test_a_graceful_close_still_marks_the_profile_once_chrome_is_gone(closing, monkeypatch, capsys, last):
    # Live 2026-10-08: a graceful CDP Browser.close left Default\Preferences at exit_type "Crashed".
    popen = closing.popen = _Proc(7070)
    monkeypatch.setattr(mainfetch, "PROCESS_TABLE",
                        _Procs(closing.events, {7070: _browser_cmd(closing.profile)}, on_port=7070, popens=[popen]))

    mainfetch._close_session(_Driver(closing.events, popen), _ctx(closing.profile, 7070, popen), last=last)

    assert closing.events == [("cdp", "Browser.close"), ("mark", closing.profile, GONE, False)]   # False: it had exited
    assert "   > 🧹 Chrome ('tv') closed — the next fetch starts a fresh one." in capsys.readouterr().out


def test_a_terminate_fallback_marks_the_profile_after_the_terminate(closing, monkeypatch, capsys):
    popen = closing.popen = _Proc(7171)
    monkeypatch.setattr(mainfetch, "PROCESS_TABLE",
                        _Procs(closing.events, {7171: _browser_cmd(closing.profile)}, on_port=7171, popens=[popen]))

    mainfetch._close_session(_Driver(closing.events, popen, exits=False), _ctx(closing.profile, 7171, popen), last=True)

    assert closing.events == [("cdp", "Browser.close"), ("terminate", 7171), ("mark", closing.profile, GONE, False)]
    assert "   > 🧹 Chrome ('tv') terminated (pid 7171) — the next fetch starts a fresh one." in capsys.readouterr().out


def test_a_chrome_left_open_is_never_marked(closing, monkeypatch, capsys):
    # it does not exit, and its command line names another profile: never terminated, so it still runs
    popen = closing.popen = _Proc(7272)
    monkeypatch.setattr(mainfetch, "PROCESS_TABLE", _Procs(closing.events, {7272: _browser_cmd(r"C:\Users\x\Other")},
                                                           on_port=7272, popens=[popen]))

    mainfetch._close_session(_Driver(closing.events, popen, exits=False), _ctx(closing.profile, 7272, popen), last=True)

    assert closing.events == [("cdp", "Browser.close")]
    assert "did not close — close it before the next fetch." in capsys.readouterr().out


@pytest.mark.parametrize("last, marked", [(True, False), (False, True)],
                         ids=["end of the run: it stays open", "account switch: its windows are closed"])
def test_an_attached_chrome_is_marked_only_when_the_run_closed_it(closing, monkeypatch, last, marked):
    monkeypatch.setattr(mainfetch, "PROCESS_TABLE",
                        _Procs(closing.events, {8080: _browser_cmd(closing.profile)}, on_port=8080))

    mainfetch._close_session(_Driver(closing.events, None), _ctx(closing.profile, 8080, None, launched=False),
                             last=last)

    assert ("cdp", "Browser.close") not in closing.events                  # never the user's browser
    assert [e for e in closing.events if e[0] == "mark"] == ([("mark", closing.profile, GONE, None)] if marked else [])


@pytest.mark.parametrize("launched", [True, False], ids=["launched: terminated", "attached: never touched"])
def test_an_interrupted_run_marks_the_profile_of_the_chrome_it_terminated(closing, monkeypatch, capsys, launched):
    popen = closing.popen = _Proc(6060) if launched else None
    monkeypatch.setattr(mainfetch, "PROCESS_TABLE", _Procs(closing.events, {6060: _browser_cmd(closing.profile)},
                                                           popens=[popen] if popen else []))

    mainfetch._end_run(_ctx(closing.profile, 6060, popen, launched=launched, journal=True), interrupted=True)

    if launched:
        assert closing.events == [("terminate", 6060), ("mark", closing.profile, GONE, False)]
        assert "   > 🛑 Chrome terminated (pid 6060) — its downloads stopped." in capsys.readouterr().out
    else:
        assert closing.events == []


def test_the_start_up_cleanup_marks_the_profile_of_each_chrome_it_terminated(closing, monkeypatch, capsys, tmp_path):
    attached = str(tmp_path / "ChromeProfile_Anime")
    monkeypatch.setattr(mainfetch, "PROCESS_TABLE", _Procs(closing.events, {7002: _browser_cmd(closing.profile),
                                                                            7003: _browser_cmd(attached)}))
    journal = pathlib.Path(mainfetch.FETCH_JOURNAL_DIR) / "fetch-20261008T100000Z-7001.json"
    journal.parent.mkdir(parents=True)
    journal.write_text(json.dumps({
        "v": 1, "run_id": "fetch-20261008T100000Z-7001", "pid": 7001, "state": "running", "entries": [],
        "pending_delete": [], "sessions": [
            {"account": "tv", "chrome_pid": 7002, "user_data_dir": closing.profile, "launched": True},
            {"account": "anime", "chrome_pid": 7003, "user_data_dir": attached, "launched": False}]}), encoding="utf-8")

    mainfetch._startup_cleanup(mainfetch.FetchContext())

    assert closing.events == [("terminate", 7002), ("mark", closing.profile, GONE, None)]   # never the attached one
    assert "   > 🛑 Closed the Chrome an interrupted fetch left running (pid 7002)." in capsys.readouterr().out


@pytest.mark.parametrize("attach", [False, True], ids=["launched: terminated", "attached: never touched"])
def test_the_crawler_marks_its_profile_after_it_terminated_its_chrome(closing, monkeypatch, tmp_path, attach):
    profiles = {account: str(tmp_path / f"profile_{account}") for account in mainfetch.CHROME_PROFILES}
    monkeypatch.setattr(mainfetch, "CHROME_PROFILES", profiles)
    monkeypatch.setattr(gp_inventory, "CHROME_START_WAIT_S", 0)
    proc = types.SimpleNamespace(terminate=lambda: closing.events.append(("terminate",)))
    monkeypatch.setattr(gp_inventory, "launch_chrome",
                        lambda profile_dir, port: closing.events.append(("launch", profile_dir, port)) or proc)

    def connect(port):
        raise gp_inventory.LoggedOut("signed out")          # every way out of a session ends in its finally

    monkeypatch.setattr(gp_inventory, "connect", connect)
    a = types.SimpleNamespace(attach=9222 if attach else None, port=None if attach else 9225, mode="rpc",
                              phase="all", max=None)

    assert gp_inventory._session(a, "tv", str(tmp_path), lambda msg: None, None) == gp_inventory.EXIT_SIGNED_OUT

    assert closing.events == ([] if attach else [("launch", profiles["tv"], 9225), ("terminate",),
                                                 ("mark", profiles["tv"], GONE, None)])
