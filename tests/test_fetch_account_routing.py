"""IMP-C26 — fetch routes objects to their real Google account (IMP-C25 Step H).

Some archived objects live in a DIFFERENT Google account than their id prefix says
(the 2026-09-25 inventory mapping found 31 X-Files episodes in the MOVIES account), so
prefix routing alone can never fetch them. The fix under test:

  - mainfetch._account_overrides(): the validated `fetch_account_overrides` map from
    mvconfig.json (read module-qualified through mvcommon._load_config()); an invalid
    entry prints ONE warning and is ignored.
  - mainfetch.profile_for_id(manual_id): longest matching override key (an exact id
    beats any prefix of it) -> its account; otherwise today's ID_PREFIX_PROFILE loop.
  - mainfetch.cmd_fetch_route: a batch whose items span accounts runs one init_driver
    per account group — the selector's own account first, then CHROME_PROFILES order —
    under ONE fetch_session_lock; extras follow their TITLE id; a later group prints the
    `Switching to profile` line; a SessionExpiredError names the failing group's profile.
  - With no overrides the whole transcript is byte-identical to a frozen oracle that
    was captured from the pre-IMP-C26 code (and still is with unrelated overrides).

Hermetic: config via mvcommon._CONFIG_CACHE (never the real mvconfig.json), library
via the `sandbox` fixture (never real C:\\Media / library_*.json), no browser —
init_driver, fetch_single_entry, the fetch lock and the debug-port probe are stubbed
and every call is recorded in order. Synthetic ids only.
"""
import contextlib
import socket
import types

import pytest

import mainfetch
import mvcommon

SEASON = "tv-en-2001-demo-s01"
EP1, EP2, EP3 = (f"{SEASON}e{n:02d}" for n in (1, 2, 3))


# ---------------------------------------------------------------------------
# helpers / fixtures
# ---------------------------------------------------------------------------
def _config(monkeypatch, overrides=None):
    """Pin the process config to a FRESH dict (never the machine's real
    mvconfig.json). `overrides` is stored as-is, so each test passes a new dict
    literal — _account_overrides caches its validation per config object."""
    cfg = {} if overrides is None else {"fetch_account_overrides": overrides}
    monkeypatch.setattr(mvcommon, "_CONFIG_CACHE", cfg)


def _seed_season(sandbox, extras=False):
    """A 3-episode tv- season (library_series.json in the sandbox); optionally the
    season (= the title) carries one cloud-resident extra."""
    folder = sandbox["local_root"] / "Series" / "Demo" / "Season 01"
    library = {SEASON: {"type": "season_map", "folder_path": str(folder),
                        "total_episodes": 3, "children": [EP1, EP2, EP3]}}
    for n, eid in enumerate((EP1, EP2, EP3), start=1):
        library[eid] = {"filename": f"S01E{n:02d}.mkv", "folder_path": str(folder),
                        "hash": str(n) * 64, "parent_id": SEASON, "status": "archived",
                        "uploaded": True, "short_id": mvcommon.generate_short_id(eid),
                        "search_term": f"S01E{n:02d}.mkv"}
    if extras:
        library[SEASON]["extras"] = {"groups": {"Specials": {"items": [{
            "filename": "Trailer.mkv", "sub_rel": "Trailer.mkv", "hash": "e" * 64,
            "uploaded": True, "status": "archived", "short_id": "abc123",
            "search_term": "Trailer [abc123].mkv"}]}}}
    mvcommon.save_library(library)


class _FakeDriver:
    """Truthy stand-in for the attached Chrome of ONE account. Records window
    enumeration / close / quit in the shared ordered event list."""

    def __init__(self, profile, events):
        self.profile = profile
        self._events = events
        self.switch_to = types.SimpleNamespace(window=self._switch)

    @property
    def window_handles(self):
        self._events.append(("handles", self.profile))
        return [f"{self.profile}-tab"]

    def _switch(self, handle):
        self._events.append(("switch", self.profile, handle))

    def close(self):
        self._events.append(("close", self.profile))

    def quit(self):
        self._events.append(("quit", self.profile))
        print(f"[quit {self.profile}]")


@pytest.fixture()
def stubs(monkeypatch):
    """Stub cmd_fetch_route's side effects (browser, per-entry fetch, lock) and
    record every call, in order, in `stubs.events`. Knobs: `raise_on`
    {(profile, filename): exception} and `init_fail` {profile, ...}."""
    st = types.SimpleNamespace(events=[], lock_acquired=0, lock_held=False,
                               raise_on={}, init_fail=set())

    def _init(profile_key="movies"):
        st.events.append(("init", profile_key, st.lock_held))
        print(f"[init {profile_key}]")
        return None if profile_key in st.init_fail else _FakeDriver(profile_key, st.events)

    def _fetch(driver, entry, temp_dir=None, entry_id=None):
        st.events.append(("fetch", driver.profile, entry["filename"], entry_id, temp_dir))
        print(f"[fetch {driver.profile} {entry['filename']} id={entry_id} temp={temp_dir}]")
        exc = st.raise_on.get((driver.profile, entry["filename"]))
        if exc is not None:
            raise exc

    @contextlib.contextmanager
    def _lock(blocking=True, **_k):
        # Never the real ~/.mediavault/locks/fetch_session.lock in a unit test.
        st.lock_acquired += 1
        st.lock_held = True
        try:
            yield
        finally:
            st.lock_held = False

    monkeypatch.setattr(mainfetch, "init_driver", _init)
    monkeypatch.setattr(mainfetch, "fetch_single_entry", _fetch)
    monkeypatch.setattr(mainfetch, "fetch_session_lock", _lock)
    return st


@pytest.fixture()
def port(monkeypatch, stubs):
    """Stub the debug-port probe used between account groups (no real socket)."""
    st = types.SimpleNamespace(free=True)

    def _probe(*_a, **_k):
        stubs.events.append(("port",))
        return st.free

    monkeypatch.setattr(mainfetch, "_debug_port_free", _probe)
    return st


def _kinds(events, kind):
    return [e for e in events if e[0] == kind]


# ---------------------------------------------------------------------------
# 1. precedence: exact id > longest prefix > today's ID_PREFIX_PROFILE routing
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("mid, expected", [
    ("tv-en-2001-demo-s01e02", "tv"),
    ("ani-ja-2006-demo01", "anime"),
    ("mov-en-2025-demo", "movies"),
    ("oth-en-2024-demo-s01e01", "others"),
    ("legacy-id", "movies"),
    ("", "movies"),
])
def test_no_overrides_is_todays_prefix_routing(monkeypatch, mid, expected):
    _config(monkeypatch)  # no fetch_account_overrides key at all
    assert mainfetch.profile_for_id(mid) == expected


def test_prefix_override_routes_its_whole_subtree(monkeypatch):
    _config(monkeypatch, {SEASON: "movies"})
    assert mainfetch.profile_for_id(SEASON) == "movies"      # the key itself (exact)
    assert mainfetch.profile_for_id(EP2) == "movies"         # everything under it
    assert mainfetch.profile_for_id("tv-en-2001-demo-s02e01") == "tv"   # a sibling season: untouched
    assert mainfetch.profile_for_id("ani-ja-2006-demo01") == "anime"


@pytest.mark.parametrize("overrides", [
    {"tv-en-2001-demo": "anime", SEASON: "movies", EP2: "tv"},
    {EP2: "tv", SEASON: "movies", "tv-en-2001-demo": "anime"},   # insertion order irrelevant
])
def test_exact_id_beats_longest_prefix_beats_shorter_prefix(monkeypatch, overrides):
    _config(monkeypatch, overrides)
    assert mainfetch.profile_for_id(EP2) == "tv"                      # exact id
    assert mainfetch.profile_for_id(EP1) == "movies"                  # longest matching prefix
    assert mainfetch.profile_for_id("tv-en-2001-demo-s02e01") == "anime"   # shorter prefix
    assert mainfetch.profile_for_id("tv-en-2002-other-s01e01") == "tv"     # no key -> prefix default


def test_keys_are_plain_string_prefixes(monkeypatch):
    # Documented semantics (same as ID_PREFIX_PROFILE): a key is a plain string
    # prefix, so "...s01e1" also covers ...s01e10-19 — list exact ids to be precise.
    _config(monkeypatch, {f"{SEASON}e1": "movies"})
    assert mainfetch.profile_for_id(f"{SEASON}e12") == "movies"
    assert mainfetch.profile_for_id(EP2) == "tv"


# ---------------------------------------------------------------------------
# 2. invalid entries: ONE warning each (stderr), entry ignored, never a crash
# ---------------------------------------------------------------------------
def test_invalid_account_is_ignored_with_one_warning(monkeypatch, capsys):
    _config(monkeypatch, {EP1: "series", EP2: "movies", EP3: ["movies"], "": "movies"})
    for _ in range(3):  # a whole batch calls profile_for_id many times
        assert mainfetch.profile_for_id(EP1) == "tv"        # "series" is not an account key
        assert mainfetch.profile_for_id(EP2) == "movies"    # the valid entry still applies
        assert mainfetch.profile_for_id(EP3) == "tv"        # a non-string value is ignored
        assert mainfetch.profile_for_id("mov-en-2025-demo") == "movies"  # blank key refused
        assert mainfetch.profile_for_id("ani-ja-2006-demo01") == "anime"  # (it would match every id)
    captured = capsys.readouterr()
    warnings = [ln for ln in captured.err.splitlines() if "fetch_account_overrides" in ln]
    assert len(warnings) == 3, captured.err
    assert any(repr(EP1) in ln and "'series'" in ln for ln in warnings)
    assert any(repr(EP3) in ln for ln in warnings)
    assert any("blank key" in ln for ln in warnings)
    assert all(ln.startswith("⚠️  mvconfig.json:") and "entry ignored" in ln for ln in warnings)
    assert captured.out == ""


def test_non_object_value_disables_overrides_with_one_warning(monkeypatch, capsys):
    _config(monkeypatch, [EP1, "movies"])
    assert mainfetch.profile_for_id(EP1) == "tv"
    assert mainfetch.profile_for_id(EP1) == "tv"
    err = capsys.readouterr().err
    assert err.count("fetch_account_overrides") == 1, err
    assert "must be an object" in err and "list" in err


def test_validation_reruns_for_a_new_config(monkeypatch, capsys):
    # Cached per config object, not forever: a new config is validated afresh.
    _config(monkeypatch, {EP1: "series"})
    assert mainfetch.profile_for_id(EP1) == "tv"
    _config(monkeypatch, {EP1: "anime"})
    assert mainfetch.profile_for_id(EP1) == "anime"
    assert capsys.readouterr().err.count("fetch_account_overrides") == 1


# ---------------------------------------------------------------------------
# 3. no overrides -> transcript byte-identical to the frozen pre-IMP-C26 oracle
# ---------------------------------------------------------------------------
# Captured from mainfetch.cmd_fetch_route BEFORE IMP-C26 (same fakes) — do not edit
# to make a failing test pass: a diff here means today's fetch output changed.
ORACLE_SEASON_WITH_EXTRAS = "\n".join([
    "--- FETCH ROUTER: tv-en-2001-demo-s01 ---",
    r"   > [Account] Profile for tv-en-2001-demo-s01: 'tv' (C:\Media\Utils\ChromeProfile_TV)",
    "   > 📂 Season Map detected. Resolving children...",
    "   > 📋 Processing 3 items...",
    "   > 📎 + 1 extra(s) (--fetchExtras)",
    "[init tv]",
    r"[fetch tv S01E01.mkv id=tv-en-2001-demo-s01e01 temp=T:\mvtmp]",
    r"[fetch tv S01E02.mkv id=tv-en-2001-demo-s01e02 temp=T:\mvtmp]",
    r"[fetch tv S01E03.mkv id=tv-en-2001-demo-s01e03 temp=T:\mvtmp]",
    "",
    "=== 📎 FETCHING 1 EXTRA(S) for tv-en-2001-demo-s01 ===",
    "[fetch tv Trailer.mkv id=None temp=None]",
    "[quit tv]",
    "",
    "✅ Batch Processing Complete.",
    "",
])

ORACLE_EPISODE_RANGE = "\n".join([
    "--- FETCH ROUTER: tv-en-2001-demo-s01 ---",
    r"   > [Account] Profile for tv-en-2001-demo-s01: 'tv' (C:\Media\Utils\ChromeProfile_TV)",
    "   > 📂 Season Map detected. Resolving children...",
    "   > 🎯 Filtered to 2 episodes (2-3)",
    "   > 📋 Processing 2 items...",
    "[init tv]",
    "[fetch tv S01E02.mkv id=tv-en-2001-demo-s01e02 temp=None]",
    "[fetch tv S01E03.mkv id=tv-en-2001-demo-s01e03 temp=None]",
    "[quit tv]",
    "",
    "✅ Batch Processing Complete.",
    "",
])


@pytest.mark.parametrize("overrides", [
    None,                                   # no fetch_account_overrides key (today)
    {},                                     # the example file's empty object
    {"mov-en-2099-unrelated": "tv", "ani-ja-2099-x": "movies"},   # overrides that match nothing here
])
@pytest.mark.parametrize("call, oracle", [
    (dict(manual_id=SEASON, fetch_extras=True, temp_dir=r"T:\mvtmp"), ORACLE_SEASON_WITH_EXTRAS),
    (dict(manual_id=SEASON, ep_range="2-3"), ORACLE_EPISODE_RANGE),
])
def test_no_override_transcript_is_frozen(sandbox, stubs, monkeypatch, capsys,
                                          overrides, call, oracle):
    _seed_season(sandbox, extras=True)
    _config(monkeypatch, overrides)
    # Tripwire: a single-account batch must never probe the debug port.
    monkeypatch.setattr(mainfetch, "_debug_port_free",
                        lambda *a, **k: stubs.events.append(("port",)) or True,
                        raising=False)

    mainfetch.cmd_fetch_route(**call)

    captured = capsys.readouterr()
    assert captured.out == oracle
    assert captured.err == ""
    assert stubs.lock_acquired == 1
    assert [e[1] for e in _kinds(stubs.events, "init")] == ["tv"]
    # ...and never closes the browser's windows or checks the port (today: quit only).
    assert not [e for e in stubs.events if e[0] in ("handles", "switch", "close", "port")]


# ---------------------------------------------------------------------------
# 4. mixed batch -> one init_driver per account, documented order, right partition
# ---------------------------------------------------------------------------
def test_mixed_batch_runs_one_session_per_account(sandbox, stubs, port, monkeypatch, capsys):
    _seed_season(sandbox)
    _config(monkeypatch, {EP2: "movies"})

    mainfetch.cmd_fetch_route(SEASON, temp_dir=r"T:\mvtmp")

    out = capsys.readouterr().out
    ev = stubs.events
    # The selector's own account first (announced by today's unchanged line), then movies.
    assert [e[1] for e in _kinds(ev, "init")] == ["tv", "movies"]
    assert r"   > [Account] Profile for tv-en-2001-demo-s01: 'tv' (C:\Media\Utils\ChromeProfile_TV)" in out
    # Partition, original order kept inside each group, temp_dir forwarded everywhere.
    assert _kinds(ev, "fetch") == [
        ("fetch", "tv", "S01E01.mkv", EP1, r"T:\mvtmp"),
        ("fetch", "tv", "S01E03.mkv", EP3, r"T:\mvtmp"),
        ("fetch", "movies", "S01E02.mkv", EP2, r"T:\mvtmp"),
    ]
    # One lock for the whole batch; every browser launched while holding it.
    assert stubs.lock_acquired == 1
    assert all(held for (_, _, held) in _kinds(ev, "init"))
    # Switch sequence: close the tv browser's windows, quit it, check the port, launch movies.
    i_quit_tv, i_port, i_init_movies = ev.index(("quit", "tv")), ev.index(("port",)), ev.index(("init", "movies", True))
    assert ev.index(("close", "tv")) < i_quit_tv < i_port < i_init_movies
    assert _kinds(ev, "quit") == [("quit", "tv"), ("quit", "movies")]
    assert _kinds(ev, "close") == [("close", "tv")]      # the LAST group is only quit (as today)
    assert len(_kinds(ev, "port")) == 1
    # Exactly one Switching line, in the documented format, printed before movies launches.
    lines = out.splitlines()
    switching = [ln for ln in lines if "Switching to profile" in ln]
    assert switching == ["   > [Account] Switching to profile 'movies' for 1 item(s) (fetch_account_overrides)"]
    assert lines.index(switching[0]) < lines.index("[init movies]")
    assert out.rstrip().endswith("✅ Batch Processing Complete.")


def test_group_order_is_selector_first_then_chrome_profiles_order(sandbox, stubs, port, monkeypatch, capsys):
    _seed_season(sandbox)
    _config(monkeypatch, {EP1: "anime", EP3: "movies"})

    mainfetch.cmd_fetch_route(SEASON)

    # CHROME_PROFILES order is movies, tv, anime, others; tv (the selector) jumps to the front.
    assert [e[1] for e in _kinds(stubs.events, "init")] == ["tv", "movies", "anime"]
    assert [(e[1], e[3]) for e in _kinds(stubs.events, "fetch")] == [
        ("tv", EP2), ("movies", EP3), ("anime", EP1)]
    out = capsys.readouterr().out
    assert [ln.strip() for ln in out.splitlines() if "Switching" in ln] == [
        "> [Account] Switching to profile 'movies' for 1 item(s) (fetch_account_overrides)",
        "> [Account] Switching to profile 'anime' for 1 item(s) (fetch_account_overrides)",
    ]
    assert _kinds(stubs.events, "close") == [("close", "tv"), ("close", "movies")]
    assert len(_kinds(stubs.events, "port")) == 2


def test_selector_account_without_items_is_never_launched(sandbox, stubs, port, monkeypatch, capsys):
    # Every episode is overridden by exact id; the season id itself is not.
    _seed_season(sandbox)
    _config(monkeypatch, {EP1: "movies", EP2: "movies", EP3: "movies"})

    mainfetch.cmd_fetch_route(SEASON)

    out = capsys.readouterr().out
    assert "'tv' (C:\\Media\\Utils\\ChromeProfile_TV)" in out   # announced as today...
    assert [e[1] for e in _kinds(stubs.events, "init")] == ["movies"]   # ...but no empty tv session
    assert "   > [Account] Switching to profile 'movies' for 3 item(s) (fetch_account_overrides)" in out
    assert not _kinds(stubs.events, "port")     # the first launch is never port-gated
    assert not _kinds(stubs.events, "close")


# ---------------------------------------------------------------------------
# 5. extras follow their TITLE id
# ---------------------------------------------------------------------------
def test_extras_follow_the_title_not_the_selected_episode(sandbox, stubs, port, monkeypatch, capsys):
    _seed_season(sandbox, extras=True)
    _config(monkeypatch, {EP2: "movies"})       # the episode moves; its title (the season) does not

    mainfetch.cmd_fetch_route(EP2, fetch_extras=True)

    out = capsys.readouterr().out
    assert "   > [Account] Profile for tv-en-2001-demo-s01e02: 'movies' (C:\\Media\\Utils\\ChromeProfile)" in out
    assert [e[1] for e in _kinds(stubs.events, "init")] == ["movies", "tv"]
    assert [(e[1], e[2]) for e in _kinds(stubs.events, "fetch")] == [
        ("movies", "S01E02.mkv"), ("tv", "Trailer.mkv")]
    assert "   > [Account] Switching to profile 'tv' for 1 item(s) (fetch_account_overrides)" in out
    # The extras header prints inside the group that fetches them, after the switch.
    lines = out.splitlines()
    assert lines.index("=== 📎 FETCHING 1 EXTRA(S) for tv-en-2001-demo-s01e02 ===") > lines.index("[init tv]")


def test_extras_of_an_overridden_title_stay_with_it(sandbox, stubs, port, monkeypatch, capsys):
    _seed_season(sandbox, extras=True)
    _config(monkeypatch, {SEASON: "movies"})    # a prefix override covers the season AND its extras

    mainfetch.cmd_fetch_route(SEASON, fetch_extras=True)

    assert [e[1] for e in _kinds(stubs.events, "init")] == ["movies"]
    assert [e[2] for e in _kinds(stubs.events, "fetch")] == [
        "S01E01.mkv", "S01E02.mkv", "S01E03.mkv", "Trailer.mkv"]
    assert "Switching" not in capsys.readouterr().out
    assert not _kinds(stubs.events, "port")


# ---------------------------------------------------------------------------
# 6. failures inside a group
# ---------------------------------------------------------------------------
def test_logged_out_later_group_names_its_own_profile_and_aborts(sandbox, stubs, port, monkeypatch, capsys):
    _seed_season(sandbox)
    _config(monkeypatch, {EP2: "movies", EP3: "anime"})
    stubs.raise_on[("movies", "S01E02.mkv")] = mainfetch.SessionExpiredError("logged out")

    mainfetch.cmd_fetch_route(SEASON)

    out = capsys.readouterr().out
    assert ("❌ Profile 'movies' is logged out. Open Chrome with "
            r"--user-data-dir=C:\Media\Utils\ChromeProfile, sign in to "
            "photos.google.com, then re-run.") in out
    assert "Profile 'tv' is logged out" not in out
    assert [e[1] for e in _kinds(stubs.events, "init")] == ["tv", "movies"]   # anime never starts
    assert _kinds(stubs.events, "quit") == [("quit", "tv"), ("quit", "movies")]
    assert "Batch Processing Complete" not in out                              # today's abort contract


def test_logged_out_first_group_keeps_todays_message(sandbox, stubs, port, monkeypatch, capsys):
    _seed_season(sandbox)
    _config(monkeypatch, {EP2: "movies"})
    stubs.raise_on[("tv", "S01E01.mkv")] = mainfetch.SessionExpiredError("logged out")

    mainfetch.cmd_fetch_route(SEASON)

    out = capsys.readouterr().out
    assert ("❌ Profile 'tv' is logged out. Open Chrome with "
            r"--user-data-dir=C:\Media\Utils\ChromeProfile_TV, sign in to "
            "photos.google.com, then re-run.") in out
    assert [e[1] for e in _kinds(stubs.events, "init")] == ["tv"]
    assert not _kinds(stubs.events, "close") and not _kinds(stubs.events, "port")


@pytest.mark.parametrize("exc, message", [
    (KeyboardInterrupt(), "🛑 Stopped by user."),
    (RuntimeError("boom"), "❌ Critical Error: boom"),
])
def test_stop_or_crash_in_a_group_ends_the_whole_batch(sandbox, stubs, port, monkeypatch, capsys, exc, message):
    _seed_season(sandbox)
    _config(monkeypatch, {EP2: "movies"})
    stubs.raise_on[("tv", "S01E01.mkv")] = exc

    mainfetch.cmd_fetch_route(SEASON)

    out = capsys.readouterr().out
    assert message in out
    assert [e[1] for e in _kinds(stubs.events, "init")] == ["tv"]   # movies is never started
    assert _kinds(stubs.events, "quit") == [("quit", "tv")]
    assert out.rstrip().endswith("✅ Batch Processing Complete.")   # today's post-stop line


def test_later_group_launch_failure_aborts_like_today(sandbox, stubs, port, monkeypatch, capsys):
    _seed_season(sandbox)
    _config(monkeypatch, {EP2: "movies"})
    stubs.init_fail.add("movies")

    mainfetch.cmd_fetch_route(SEASON)

    out = capsys.readouterr().out
    assert [e[1] for e in _kinds(stubs.events, "init")] == ["tv", "movies"]
    assert [e[2] for e in _kinds(stubs.events, "fetch")] == ["S01E01.mkv", "S01E03.mkv"]
    assert "Batch Processing Complete" not in out


def test_busy_debug_port_blocks_the_switch_loudly(sandbox, stubs, port, monkeypatch, capsys):
    # The previous account's Chrome still holds port 9222: attaching now would drive
    # the WRONG account, so the batch stops instead of launching the next profile.
    _seed_season(sandbox)
    _config(monkeypatch, {EP2: "movies"})
    port.free = False

    mainfetch.cmd_fetch_route(SEASON)

    out = capsys.readouterr().out
    assert [e[1] for e in _kinds(stubs.events, "init")] == ["tv"]
    assert "❌ Cannot switch to profile 'movies'" in out and "9222" in out
    assert "Batch Processing Complete" not in out


# ---------------------------------------------------------------------------
# 7. the debug-port probe itself (loopback only, ephemeral port)
# ---------------------------------------------------------------------------
def test_debug_port_probe_sees_a_listener_and_its_absence():
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    busy_port = srv.getsockname()[1]
    try:
        assert mainfetch._debug_port_free(port=busy_port, timeout=0) is False
    finally:
        srv.close()
    assert mainfetch._debug_port_free(port=busy_port, timeout=0) is True
