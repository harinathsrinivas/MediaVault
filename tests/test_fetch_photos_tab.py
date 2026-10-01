"""IMP-C29 — fetch works in a photos.google.com tab, never Chrome's Gemini side panel.

Chrome 154 lists its Gemini side panel among Selenium's window handles (a `webview` on
gemini.google.com/glic and an `other` on chrome://glic/), and chromedriver attached to the
webview, so every fetch search was typed into Gemini (2026-10-01, IMP-C25 gate RH). Under test:

  - mainfetch.use_photos_tab(driver): keeps / switches to / opens a normal tab (CDP type
    "page", https, photos.google.com host) — never a webview, chrome://, devtools:// or glic
    target — then focus_page() sends Page.bringToFront + Emulation.setFocusEmulationEnabled.
  - mainfetch.init_driver: runs it right after the unchanged debuggerAddress attach, or gives
    up loudly (returns None) when no Photos tab can be had.
  - mainfetch.trigger_download: re-asserts the tab + focus before EVERY attempt, and reports a
    search that never ran in Google Photos as its own error — never as "Not found".
  - No-regression pins for today's flow: the keystroke chain, the CSS-then-XPath thumbnail
    lookup, the clicked index, the IMP-C2 single retry, the IMP-C6 logged-out propagation
    and zero-streak backstop.

Hermetic and Selenium-free, in the style of test_trigger_download_retry.py: _FakeChrome models
the attached browser as chromedriver 154 reported it live (window handles ARE the CDP target
ids, the Gemini targets are among them, the attach starts in the webview); WebDriverWait and
ActionChains are stand-ins and every sleep is instant. No real browser, no real C:\\Media,
no library I/O.
"""
import types

import pytest

import mainfetch

PHOTOS_HOME = "https://photos.google.com/"
SEARCH_URL = "https://photos.google.com/search/ClJUOKEN"   # where a keystroke search lands (a token, not the text)
QUERY = "Show.S01E01 [abc123].mkv"

GLIC_WEBVIEW = {"id": "GLIC-WEBVIEW", "type": "webview", "url": "https://gemini.google.com/glic?hl=en-US"}
GLIC_OTHER = {"id": "GLIC-OTHER", "type": "other", "url": "chrome://glic/"}
PHOTOS_TAB = {"id": "PHOTOS-TAB", "type": "page", "url": PHOTOS_HOME}
BLANK_TAB = {"id": "BLANK-TAB", "type": "page", "url": "about:blank"}


# ---------------------------------------------------------------------------
# The fake browser
# ---------------------------------------------------------------------------
class _Thumb:
    """A displayed thumbnail wide enough to pass trigger_download's > 50 px filter."""

    def __init__(self, name):
        self.name = name

    def is_displayed(self):
        return True

    @property
    def size(self):
        return {"width": 100, "height": 100}

    def __repr__(self):
        return f"<thumb {self.name}>"


class _FakeChrome:
    """Selenium stand-in for the attached Chrome.

    Targets are {"id", "type", "url"}; window handles are the ids of the page / webview /
    other targets, as chromedriver 154 lists them. While the current window is closed,
    CDP commands and new_window raise, as chromedriver's NoSuchWindowException does
    (verified live 2026-10-01). get() navigates the current target
    (`redirect` maps a URL to where Google sends it). The search keystrokes (see
    _KeysActions) move a Photos tab to SEARCH_URL when `search_runs`; that page shows
    `results_css` (a[href*='./photo/']) and `results_xpath` (background-image divs),
    while the home timeline shows `home_css`. `after_keys_url` instead sends the current
    target there when the search keys arrive (whatever took them was not Photos).
    Every call is logged in order, tagged with the handle it ran in."""

    def __init__(self, targets, current, *, search_runs=True, results_css=(), results_xpath=(),
                 home_css=(), redirect=None, after_keys_url=None, cdp_fails=(), new_window_fails=False):
        self.targets = {t["id"]: dict(t) for t in targets}
        self.order = [t["id"] for t in targets]
        self.current = current
        self.search_runs = search_runs
        self.results_css = list(results_css)
        self.results_xpath = list(results_xpath)
        self.home_css = list(home_css)
        self.redirect = dict(redirect or {})
        self.after_keys_url = after_keys_url
        self.cdp_fails = set(cdp_fails)
        self.new_window_fails = new_window_fails
        self.log = []
        self.clicked = []
        self._tabs_opened = 0
        self.switch_to = types.SimpleNamespace(window=self._switch, new_window=self._new_window)

    # --- window surface -----------------------------------------------------
    @property
    def window_handles(self):
        return [h for h in self.order
                if h in self.targets and self.targets[h]["type"] in ("page", "webview", "other")]

    @property
    def current_window_handle(self):
        if self.current not in self.targets:
            raise RuntimeError("no such window: target window already closed")
        return self.current

    @property
    def current_url(self):
        return self.targets[self.current_window_handle]["url"]

    def _switch(self, handle):
        if handle not in self.targets:
            raise RuntimeError(f"no such window: {handle}")
        self.log.append(("switch", handle))
        self.current = handle

    def _new_window(self, kind):
        self.current_window_handle  # like chromedriver: no such window while ours is closed
        if self.new_window_fails:
            raise RuntimeError("cannot open a new tab")
        self._tabs_opened += 1
        handle = f"NEW-TAB-{self._tabs_opened}"
        self.targets[handle] = {"id": handle, "type": "page", "url": "about:blank"}
        self.order.append(handle)
        self.log.append(("new_window", kind, handle))
        self.current = handle

    def get(self, url):
        handle = self.current_window_handle
        self.log.append(("get", handle, url))
        self.targets[handle]["url"] = self.redirect.get(url, url)

    def quit(self):
        self.log.append(("quit",))

    def close_tab(self, handle):
        """Test helper: the tab disappears (closed by the user, a crash, ...)."""
        del self.targets[handle]

    # --- Chrome DevTools protocol --------------------------------------------
    def execute_cdp_cmd(self, cmd, params):
        self.log.append(("cdp", self.current, cmd, params))
        self.current_window_handle  # like chromedriver: no such window while ours is closed
        if cmd in self.cdp_fails:
            raise RuntimeError(f"CDP {cmd} failed")
        if cmd == "Target.getTargets":
            return {"targetInfos": [{"targetId": t["id"], "type": t["type"], "url": t["url"],
                                     "attached": False} for t in self.targets.values()]}
        if cmd == "Target.getTargetInfo":
            t = self.targets[self.current_window_handle]
            return {"targetInfo": {"targetId": t["id"], "type": t["type"], "url": t["url"]}}
        return {}

    # --- page surface used by trigger_download -------------------------------
    def find_elements(self, by, selector):
        url = self.current_url
        if "background-image" in str(selector):
            return list(self.results_xpath) if url == SEARCH_URL else []
        if url == SEARCH_URL:
            return list(self.results_css)
        return list(self.home_css) if url.rstrip("/") == PHOTOS_HOME.rstrip("/") else []

    def execute_script(self, script, *args):
        if "click" in script:
            self.clicked.append((self.current_window_handle, args[0]))

    def _keys(self, chain):
        handle = self.current_window_handle
        self.log.append(("keys", handle, tuple(chain)))
        target = self.targets[handle]
        if chain[:1] == ["/"] and chain[-1:] == [mainfetch.Keys.ENTER]:
            if self.after_keys_url is not None:
                target["url"] = self.after_keys_url
            elif (self.search_runs and target["type"] == "page"
                  and target["url"].startswith("https://photos.google.com")):
                target["url"] = SEARCH_URL


class _NoCdpChrome(_FakeChrome):
    """The same browser behind a driver WITHOUT Chrome's DevTools protocol — what
    trigger_download saw before IMP-C29 (it never selected a tab)."""

    def __getattribute__(self, name):
        if name == "execute_cdp_cmd":
            raise AttributeError(name)
        return super().__getattribute__(name)


class _KeysActions:
    """ActionChains stand-in: collects the chain and hands it to the fake browser on perform()."""

    def __init__(self, driver):
        self._driver = driver
        self._chain = []

    def send_keys(self, *keys):
        self._chain.extend(keys)
        return self

    def key_down(self, key):
        self._chain.append(("down", key))
        return self

    def key_up(self, key):
        self._chain.append(("up", key))
        return self

    def pause(self, seconds):
        self._chain.append(("pause", seconds))
        return self

    def perform(self):
        chain, self._chain = self._chain, []
        self._driver._keys(chain)


class _NoOpWait:
    def __init__(self, *a, **k):
        pass

    def until(self, *a, **k):
        return True


@pytest.fixture(autouse=True)
def sleeps(monkeypatch):
    """Instant sleeps (recorded), stand-in WebDriverWait + ActionChains; mainfetch stays real."""
    durations = []
    monkeypatch.setattr(mainfetch.time, "sleep", lambda d=0: durations.append(d))
    monkeypatch.setattr(mainfetch, "WebDriverWait", _NoOpWait)
    monkeypatch.setattr(mainfetch.webdriver, "ActionChains", _KeysActions)
    return durations


# ---------------------------------------------------------------------------
# log helpers
# ---------------------------------------------------------------------------
def _searches(log):
    """Log indexes of the search keystroke chains ("/" … ENTER)."""
    return [i for i, e in enumerate(log) if e[0] == "keys" and e[2][:1] == ("/",)]


def _touched(log, handle):
    """Everything that ran IN `handle` (navigation, keys, CDP) — switching into it excluded."""
    return [e for e in log if e[0] in ("get", "keys", "cdp") and e[1] == handle]


def _assert_focused_before_each_search(log):
    """Before every search, after the last tab move and the previous search, BOTH focus
    commands were sent in the very tab the search keys then went to."""
    previous = -1
    for s in _searches(log):
        handle = log[s][1]
        moves = [i for i, e in enumerate(log[:s]) if e[0] in ("switch", "new_window")]
        since = max([previous] + moves)
        sent = [(e[2], e[3]) for e in log[since + 1:s] if e[0] == "cdp" and e[1] == handle]
        assert ("Page.bringToFront", {}) in sent, (s, log)
        assert ("Emulation.setFocusEmulationEnabled", {"enabled": True}) in sent, (s, log)
        previous = s


# ---------------------------------------------------------------------------
# 1. use_photos_tab — which tab fetch works in
# ---------------------------------------------------------------------------
def test_attach_order_glic_webview_then_photos_selects_the_photos_tab():
    # The live 2026-10-01 layout: chromedriver attached in the Gemini webview.
    driver = _FakeChrome([GLIC_WEBVIEW, PHOTOS_TAB, GLIC_OTHER], current="GLIC-WEBVIEW")

    assert mainfetch.use_photos_tab(driver) == "existing"

    assert driver.current == "PHOTOS-TAB"
    assert ("switch", "PHOTOS-TAB") in driver.log
    assert not [e for e in driver.log if e[0] in ("new_window", "get", "keys")]
    # Classified without ever switching into a Gemini target.
    assert ("switch", "GLIC-WEBVIEW") not in driver.log and ("switch", "GLIC-OTHER") not in driver.log


def test_a_photos_tab_that_is_already_current_is_kept():
    driver = _FakeChrome([GLIC_WEBVIEW, PHOTOS_TAB], current="PHOTOS-TAB")

    assert mainfetch.use_photos_tab(driver) == "current"

    assert driver.current == "PHOTOS-TAB"
    assert not [e for e in driver.log if e[0] in ("switch", "new_window", "get")]


def test_only_non_photos_targets_opens_a_new_tab_and_navigates_it():
    # A freshly launched Chrome 154: the about:blank launch tab plus the Gemini targets.
    driver = _FakeChrome([GLIC_WEBVIEW, GLIC_OTHER, BLANK_TAB], current="GLIC-WEBVIEW")

    assert mainfetch.use_photos_tab(driver) == "new"

    assert driver.current == "NEW-TAB-1"
    assert ("new_window", "tab", "NEW-TAB-1") in driver.log
    assert ("get", "NEW-TAB-1", mainfetch.PHOTOS_URL) in driver.log
    assert [e for e in driver.log if e[0] == "get"] == [("get", "NEW-TAB-1", mainfetch.PHOTOS_URL)]
    assert driver.targets["BLANK-TAB"]["url"] == "about:blank"            # the launch tab is left alone
    assert driver.targets["GLIC-WEBVIEW"]["url"] == GLIC_WEBVIEW["url"]   # Gemini is never driven


@pytest.mark.parametrize("target", [
    {"id": "T", "type": "webview", "url": "https://photos.google.com/"},     # a webview, even on Photos
    {"id": "T", "type": "other", "url": "chrome://glic/"},
    {"id": "T", "type": "page", "url": "devtools://devtools/bundled/inspector.html"},
    {"id": "T", "type": "page", "url": "chrome://newtab/"},
    {"id": "T", "type": "page", "url": "https://gemini.google.com/app"},
    {"id": "T", "type": "page", "url": "https://accounts.google.com/ServiceLogin"},
    {"id": "T", "type": "page", "url": "http://photos.google.com/"},          # not https
    {"id": "T", "type": "page", "url": "https://photos.google.com.evil.example/"},
    {"id": "T", "type": "page", "url": "about:blank"},
], ids=lambda t: f"{t['type']}:{t['url'].split('//')[-1][:28]}")
def test_a_non_photos_target_is_never_worked_in(target):
    driver = _FakeChrome([target], current="T")

    assert mainfetch.use_photos_tab(driver) == "new"

    assert driver.current == "NEW-TAB-1"
    assert driver.targets["T"]["url"] == target["url"]
    assert not [e for e in driver.log if e[0] in ("get", "keys") and e[1] == "T"]


@pytest.mark.parametrize("url", [
    "https://photos.google.com/",
    "https://photos.google.com/u/1/album/AF1QipFAKE",
    "https://photos.google.com/search/ClJUOKEN/photo/AF1QipFAKE",
])
def test_an_open_photos_tab_on_any_photos_path_is_reused(url):
    driver = _FakeChrome([GLIC_WEBVIEW, {**PHOTOS_TAB, "url": url}], current="GLIC-WEBVIEW")

    assert mainfetch.use_photos_tab(driver) == "existing"
    assert driver.current == "PHOTOS-TAB"
    assert not [e for e in driver.log if e[0] in ("new_window", "get")]


def test_when_cdp_cannot_classify_targets_no_open_tab_is_trusted():
    driver = _FakeChrome([GLIC_WEBVIEW, PHOTOS_TAB], current="GLIC-WEBVIEW",
                         cdp_fails={"Target.getTargetInfo", "Target.getTargets"})

    assert mainfetch.use_photos_tab(driver) == "new"
    assert driver.current == "NEW-TAB-1"
    assert not [e for e in driver.log if e[0] in ("get", "keys") and e[1] == "GLIC-WEBVIEW"]


def test_a_closed_tab_is_replaced_by_the_open_photos_tab():
    # Selenium's handle points at a tab that no longer exists (closed under us).
    driver = _FakeChrome([GLIC_WEBVIEW, PHOTOS_TAB], current="GONE-TAB")

    assert mainfetch.use_photos_tab(driver) == "existing"

    assert driver.current == "PHOTOS-TAB"
    # Re-entering via the first live handle only gives CDP a target; nothing is driven there.
    assert not [e for e in _touched(driver.log, "GLIC-WEBVIEW") if e[0] in ("get", "keys")]


def test_a_browser_without_any_tab_is_a_fault():
    driver = _FakeChrome([], current="GONE-TAB")

    with pytest.raises(RuntimeError, match="no open tab"):
        mainfetch.use_photos_tab(driver)


def test_focus_emulation_is_sent_in_the_selected_tab():
    driver = _FakeChrome([GLIC_WEBVIEW, PHOTOS_TAB], current="GLIC-WEBVIEW")

    mainfetch.use_photos_tab(driver)

    assert driver.log[-2:] == [
        ("cdp", "PHOTOS-TAB", "Page.bringToFront", {}),
        ("cdp", "PHOTOS-TAB", "Emulation.setFocusEmulationEnabled", {"enabled": True}),
    ]


# ---------------------------------------------------------------------------
# 2. init_driver — the attach itself
# ---------------------------------------------------------------------------
@pytest.fixture()
def attach(monkeypatch):
    """Run the REAL init_driver with no Chrome launch and no chromedriver: Popen, the
    driver manager, Service and webdriver.Chrome are stand-ins. `attach.driver` is the
    browser it attaches to; `attach.options` records the Options it attached with."""
    st = types.SimpleNamespace(driver=None, options=None, launched=[])

    def _chrome(service=None, options=None):
        st.options = options
        return st.driver

    monkeypatch.setattr(mainfetch.subprocess, "Popen", lambda cmd, *a, **k: st.launched.append(cmd))
    monkeypatch.setattr(mainfetch, "ChromeDriverManager",
                        lambda *a, **k: types.SimpleNamespace(install=lambda: "chromedriver.exe"))
    monkeypatch.setattr(mainfetch, "Service", lambda *a, **k: "service")
    monkeypatch.setattr(mainfetch.webdriver, "Chrome", _chrome)
    return st


def test_init_driver_attaches_then_moves_off_the_gemini_webview(attach, capsys):
    attach.driver = _FakeChrome([GLIC_WEBVIEW, PHOTOS_TAB, GLIC_OTHER], current="GLIC-WEBVIEW")

    driver = mainfetch.init_driver("movies")

    assert driver is attach.driver
    assert attach.options.experimental_options["debuggerAddress"] == "127.0.0.1:9222"  # attach unchanged
    assert len(attach.launched) == 1 and "--remote-debugging-port=9222" in attach.launched[0]
    assert driver.current == "PHOTOS-TAB"
    assert not [e for e in _touched(driver.log, "GLIC-WEBVIEW") if e[0] in ("get", "keys")]
    assert ("cdp", "PHOTOS-TAB", "Emulation.setFocusEmulationEnabled", {"enabled": True}) in driver.log
    assert "   > 🗂️ Switched to the open photos.google.com tab." in capsys.readouterr().out


def test_init_driver_opens_a_photos_tab_when_none_is_open(attach, capsys):
    attach.driver = _FakeChrome([GLIC_WEBVIEW, GLIC_OTHER, BLANK_TAB], current="GLIC-WEBVIEW")

    driver = mainfetch.init_driver("tv")

    assert driver.current == "NEW-TAB-1"
    assert driver.targets["NEW-TAB-1"]["url"] == mainfetch.PHOTOS_URL
    assert "   > 🗂️ Opened a new photos.google.com tab." in capsys.readouterr().out


def test_init_driver_gives_up_loudly_without_a_photos_tab(attach, capsys):
    attach.driver = _FakeChrome([GLIC_WEBVIEW, GLIC_OTHER], current="GLIC-WEBVIEW", new_window_fails=True)

    assert mainfetch.init_driver("movies") is None

    out = capsys.readouterr().out
    assert "❌ Could not open a photos.google.com tab in the attached Chrome: cannot open a new tab" in out
    assert ("quit",) in attach.driver.log          # the Selenium session is released
    assert not [e for e in attach.driver.log if e[0] in ("get", "keys")]


# ---------------------------------------------------------------------------
# 3. trigger_download — every attempt runs in the focused Photos tab
# ---------------------------------------------------------------------------
def test_search_is_typed_into_the_photos_tab_not_gemini(capsys):
    # A driver still sitting in the Gemini webview (the RH situation) is moved first.
    driver = _FakeChrome([GLIC_WEBVIEW, PHOTOS_TAB, GLIC_OTHER], current="GLIC-WEBVIEW",
                         results_xpath=[_Thumb("x0"), _Thumb("x1")])

    assert mainfetch.trigger_download(driver, QUERY) is True

    assert {driver.log[s][1] for s in _searches(driver.log)} == {"PHOTOS-TAB"}
    assert [c[0] for c in driver.clicked] == ["PHOTOS-TAB"]
    assert not [e for e in _touched(driver.log, "GLIC-WEBVIEW") if e[0] in ("get", "keys")]
    _assert_focused_before_each_search(driver.log)
    assert "Switched to the open photos.google.com tab." in capsys.readouterr().out


@pytest.mark.parametrize("drift, note", [
    ("closed", "Opened a new photos.google.com tab."),        # our tab was closed after the download
    ("moved", "Switched to the open photos.google.com tab."),  # the driver was left in another tab
])
def test_every_attempt_reasserts_the_tab_and_focus(drift, note, capsys):
    driver = _FakeChrome([GLIC_WEBVIEW, PHOTOS_TAB], current="PHOTOS-TAB",
                         results_xpath=[_Thumb("x0")])
    assert mainfetch.trigger_download(driver, QUERY) is True     # download + Esc sent

    if drift == "closed":
        driver.close_tab("PHOTOS-TAB")
    else:
        driver.targets["DL-TAB"] = {"id": "DL-TAB", "type": "page",
                                    "url": "https://video-downloads.googleusercontent.com/x"}
        driver.order.append("DL-TAB")
        driver.current = "DL-TAB"
    capsys.readouterr()

    assert mainfetch.trigger_download(driver, QUERY) is True

    second = _searches(driver.log)[-1]
    assert driver.log[second][1] == ("NEW-TAB-1" if drift == "closed" else "PHOTOS-TAB")
    assert driver.targets[driver.log[second][1]]["type"] == "page"
    _assert_focused_before_each_search(driver.log)
    assert note in capsys.readouterr().out
    assert not [e for e in _touched(driver.log, "GLIC-WEBVIEW") if e[0] in ("get", "keys")]
    assert not [e for e in _touched(driver.log, "DL-TAB") if e[0] in ("get", "keys")]


def test_the_retry_attempt_refocuses_too(sleeps):
    driver = _FakeChrome([PHOTOS_TAB], current="PHOTOS-TAB")      # searched, nothing found, twice

    assert mainfetch.trigger_download(driver, QUERY) is False

    assert len(_searches(driver.log)) == 2
    _assert_focused_before_each_search(driver.log)
    assert sleeps.count(5) == 1


def test_a_search_that_never_ran_is_not_reported_as_not_found(capsys):
    # The keystrokes never reached the search box: the page stays on '/', whose timeline
    # thumbnails the selectors WOULD match — they must never be clicked.
    driver = _FakeChrome([PHOTOS_TAB], current="PHOTOS-TAB", search_runs=False,
                         home_css=[_Thumb("timeline0"), _Thumb("timeline1")])

    assert mainfetch.trigger_download(driver, QUERY) is False

    out = capsys.readouterr().out
    line = ("     ❌ Search did not run (or had not started yet): Google Photos is still on /, "
            "not on a /search/ results page — this is not a 'Not found'.")
    assert out.count(line) == 2                    # both attempts
    assert "Not found (Found" not in out
    assert out.count("Retry 2/2") == 1
    assert driver.clicked == []


def test_the_rh_gemini_miss_is_named_not_reported_as_not_found(capsys):
    # Pre-IMP-C29 RH reproduction: a driver without CDP sitting in the Gemini webview. Its
    # navigation reads as Photos, so the session check passes; the side panel then takes
    # the keystrokes back to Gemini. The miss must name where the search went.
    driver = _NoCdpChrome([GLIC_WEBVIEW], current="GLIC-WEBVIEW",
                          after_keys_url="https://gemini.google.com/glic?hl=en-US")

    assert mainfetch.trigger_download(driver, QUERY) is False

    out = capsys.readouterr().out
    line = ("     ❌ Not a Google Photos page: the search went to https://gemini.google.com/glic "
            "— this is not a 'Not found'.")
    assert out.count(line) == 2
    assert "Not found (Found" not in out
    assert driver.clicked == []


@pytest.mark.parametrize("target, problem", [
    (GLIC_WEBVIEW, "Not a Google Photos page: the search went to webview https://gemini.google.com/glic"),
    (GLIC_OTHER, "Not a Google Photos page: the search went to other chrome://glic/"),
    ({**PHOTOS_TAB, "url": "https://accounts.google.com/v3/signin/identifier?continue=x"},
     "Not a Google Photos page: the search went to page https://accounts.google.com/v3/signin/identifier"),
    (PHOTOS_TAB, "Search did not run (or had not started yet): Google Photos is still on /, "
                 "not on a /search/ results page"),
    ({**PHOTOS_TAB, "url": "https://photos.google.com/photo/AF1QipFAKE"},
     "Search did not run (or had not started yet): Google Photos is still on /photo/AF1QipFAKE, "
     "not on a /search/ results page"),
    ({**PHOTOS_TAB, "url": SEARCH_URL}, None),
    ({**PHOTOS_TAB, "url": "https://photos.google.com/u/1/search/ClJUOKEN"}, None),
], ids=["gemini-webview", "glic-other", "accounts", "home", "photo-page", "search", "search-u1"])
def test_search_page_problem_names_the_actual_target(target, problem):
    driver = _FakeChrome([target], current=target["id"])
    assert mainfetch._search_page_problem(driver) == problem


def test_search_page_problem_does_not_diagnose_an_unreadable_url():
    driver = _FakeChrome([PHOTOS_TAB], current="GONE-TAB")   # reading current_url raises
    assert mainfetch._search_page_problem(driver) is None


# ---------------------------------------------------------------------------
# 4. no-regression pins — today's search / click / download flow is unchanged
# ---------------------------------------------------------------------------
def test_flow_keystrokes_clicked_index_and_download_chain_are_unchanged(sleeps, capsys):
    thumbs = [_Thumb("c0"), _Thumb("c1"), _Thumb("c2")]
    driver = _FakeChrome([PHOTOS_TAB], current="PHOTOS-TAB", results_css=thumbs)

    assert mainfetch.trigger_download(driver, QUERY, index=2) is True

    K = mainfetch.Keys
    assert [e[2] for e in driver.log if e[0] == "keys"] == [
        ("/", ("pause", 0.3), QUERY, K.ENTER),             # the keystroke search
        (("down", K.SHIFT), "d", ("up", K.SHIFT)),         # Shift+D
        (K.ESCAPE,),                                       # leave the player
    ]
    assert driver.clicked == [("PHOTOS-TAB", thumbs[2])]   # the index-th thumbnail, as before
    assert [e for e in driver.log if e[0] == "get"] == [("get", "PHOTOS-TAB", mainfetch.PHOTOS_URL)]
    assert sleeps.count(5) == 0                            # no retry
    out = capsys.readouterr().out
    assert "     🚀 Triggered." in out and "🗂️" not in out   # steady state: no tab note


def test_xpath_fallback_still_finds_results_the_css_selector_misses():
    # Today's Photos result links are ./search/<token>/photo/<id>: no './photo/' substring.
    xs = [_Thumb("x0"), _Thumb("x1")]
    driver = _FakeChrome([PHOTOS_TAB], current="PHOTOS-TAB", results_xpath=xs)

    assert mainfetch.trigger_download(driver, QUERY, index=1) is True
    assert driver.clicked == [("PHOTOS-TAB", xs[1])]


@pytest.mark.parametrize("results, index, found", [([], 0, 0), ([_Thumb("x0")], 3, 1)])
def test_a_genuine_miss_still_says_not_found_and_retries_once(sleeps, capsys, results, index, found):
    driver = _FakeChrome([PHOTOS_TAB], current="PHOTOS-TAB", results_xpath=results)

    assert mainfetch.trigger_download(driver, QUERY, index=index) is False

    out = capsys.readouterr().out
    assert out.count(f"     ⚠️ Not found (Found {found}).") == 2
    assert out.count("Retry 2/2") == 1 and sleeps.count(5) == 1
    assert "❌" not in out and driver.clicked == []


def test_a_logged_out_redirect_still_propagates_without_retry():
    driver = _FakeChrome([PHOTOS_TAB], current="PHOTOS-TAB",
                         redirect={mainfetch.PHOTOS_URL: "https://accounts.google.com/v3/signin/identifier"})

    with pytest.raises(mainfetch.SessionExpiredError):
        mainfetch.trigger_download(driver, QUERY)

    assert [e for e in driver.log if e[0] == "get"] == [("get", "PHOTOS-TAB", mainfetch.PHOTOS_URL)]
    assert not _searches(driver.log)


def test_searches_that_never_run_still_trip_the_c6_zero_streak():
    driver = _FakeChrome([PHOTOS_TAB], current="PHOTOS-TAB", search_runs=False)

    assert mainfetch.trigger_download(driver, QUERY) is False
    assert mainfetch.trigger_download(driver, QUERY) is False
    with pytest.raises(mainfetch.SessionExpiredError, match="3 consecutive"):
        mainfetch.trigger_download(driver, QUERY)


def test_the_real_chrome_driver_takes_the_cdp_path():
    # trigger_download skips the tab re-assert only for a stand-in without execute_cdp_cmd;
    # the production driver class must never be such a stand-in.
    assert hasattr(mainfetch.webdriver.Chrome, "execute_cdp_cmd")
