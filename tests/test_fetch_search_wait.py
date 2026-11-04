"""IMP-C35 (IMP-C25 Step 10 h) — a keystroke search waits for its results and never clicks the home timeline.

IMP-C29 measured it live: for 1-2 s after Enter the page already reads /search/ while it still SHOWS the home
timeline, whose thumbnails trigger_download's selectors match too, and results took 2-7 s. With the fixed 3 s sleep
a slow search could click a timeline item and download the wrong file. Under test:

  - mainfetch._await_search_results: a bounded poll (SEARCH_RESULTS_WAIT_S, every SEARCH_POLL_S) until the /search/
    page shows result thumbnails — settled over two looks — and no timeline, or says "No results"; a page that
    never reached /search/ or still shows only the timeline gives a problem line and nothing is clicked.
  - mainfetch._drop_timeline: a thumbnail that is (or sits inside) a home-timeline link is never clicked.
  - trigger_download uses both — its digest is re-pinned on purpose (PLAN Step 10 h); the IMP-C2 retry, the IMP-C6
    propagation and the IMP-C29 tab rules are pinned by their own, unchanged tests.
  - [live check 2026-10-05] Google Photos' "Most relevant to your search" strip of loose matches ABOVE the results
    (the legacy click took its first tile: The Expanse for a Silicon Valley filename): its tiles are never clicked,
    and a search showing only the strip is a miss after SEARCH_SUGGESTIONS_ONLY_LOOKS looks. The shared fake has no
    strip; _Page models it (gpweb pins the DOM marker the page scripts use).

Hermetic and Selenium-free: _Page models the Photos tab during a search (each look at the page advances it; the
page SHOWS what the last look saw), ActionChains / WebDriverWait are stand-ins and every sleep is recorded, never
slept. No browser, no network, no library.
"""
import hashlib
import inspect

import pytest

import gpweb
import mainfetch

QUERY = "Show.S01E01 [abc123].mkv"
TIMELINE = {"results": 0, "timeline": 6, "empty": False}       # the home timeline, still shown after Enter


def results(n):
    return {"results": n, "timeline": 0, "empty": False}


def strip(relevant, n=0):
    """A search page showing the "Most relevant to your search" strip (`relevant` tiles) above `n` results."""
    return {"results": n, "timeline": 0, "relevant": relevant, "empty": False}


class _El:
    """A displayed thumbnail element; `timeline` = it is (or sits inside) a link that is not a result to click — a
    home-timeline link or a "Most relevant" strip suggestion (what gpweb.TIMELINE_LINKS_JS answers for it)."""

    def __init__(self, name, timeline):
        self.name, self.timeline = name, timeline

    def is_displayed(self):
        return True

    @property
    def size(self):
        return {"width": 100, "height": 100}


class _Page:
    """The Photos tab during a keystroke search. `states` = what each look at the page (gpweb.SEARCH_STATE_JS) sees,
    one per look, the last repeating; the page SHOWS what the latest look saw (before any look: the first state).
    Shown timeline: ./photo/<id> links (the CSS selector) and their background-image divs; shown results:
    background-image divs inside ./search/<token>/photo/<id> links (the XPath selector only), after the "Most
    relevant" strip's divs (search links too, above the results in DOM order)."""
    HOME = "https://photos.google.com/"
    SEARCH = "https://photos.google.com/search/ClJUOKEN"

    def __init__(self, states, search_runs=True, busy_looks=0):
        self.states, self.search_runs, self.busy_looks = list(states), search_runs, busy_looks
        self.url, self.looks, self.clicked, self.scripts = "about:blank", 0, [], []

    @property
    def current_url(self):
        return self.url

    def get(self, url):
        self.url = url

    def _shown(self):
        return self.states[min(max(self.looks - 1, 0), len(self.states) - 1)]

    def execute_script(self, script, *args):
        if script == gpweb.SEARCH_STATE_JS:
            self.scripts.append("look")
            if self.busy_looks:
                self.busy_looks -= 1
                raise RuntimeError("javascript error: Cannot read properties of null")
            state = self.states[min(self.looks, len(self.states) - 1)]
            self.looks += 1
            return dict(state)
        if script == gpweb.TIMELINE_LINKS_JS:
            self.scripts.append("classify")
            return [el.timeline for el in args[0]]
        if "click" in script:
            self.clicked.append(args[0].name)
            return None
        raise AssertionError(f"unexpected page script: {script[:60]!r}")

    def find_elements(self, by, selector):
        shown = self._shown()
        timeline = shown.get("timeline", 0)
        if "background-image" in str(selector):
            return ([_El(f"timeline-div-{n}", True) for n in range(timeline)]
                    + [_El(f"relevant-{n}", True) for n in range(shown.get("relevant", 0))]
                    + [_El(f"result-{n}", False) for n in range(shown.get("results", 0))])
        return [_El(f"timeline-link-{n}", True) for n in range(timeline)]

    def _keys(self, chain):
        if chain[:1] == ["/"] and chain[-1:] == [mainfetch.Keys.ENTER] and self.search_runs:
            self.url = self.SEARCH


class _Keys:
    def __init__(self, driver):
        self._driver, self._chain = driver, []

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
        self._driver._keys([k for k in chain if not (isinstance(k, tuple) and k[0] == "pause")])


class _NoOpWait:
    def __init__(self, *a, **k):
        pass

    def until(self, *a, **k):
        return True


@pytest.fixture(autouse=True)
def sleeps(monkeypatch):
    durations = []
    monkeypatch.setattr(mainfetch.time, "sleep", lambda d=0: durations.append(d))
    monkeypatch.setattr(mainfetch, "WebDriverWait", _NoOpWait)
    monkeypatch.setattr(mainfetch.webdriver, "ActionChains", _Keys)
    return durations


# ---------------------------------------------------------------------------
# the wait
# ---------------------------------------------------------------------------
def test_it_waits_out_the_home_timeline_and_clicks_a_search_result(sleeps):
    page = _Page([TIMELINE] * 3 + [results(4)])

    assert mainfetch.trigger_download(page, QUERY) is True

    assert page.clicked == ["result-0"]                 # never a timeline thumbnail
    assert sleeps.count(mainfetch.SEARCH_POLL_S) == 4   # 3 looks at the timeline, then two at settled results
    assert 3 not in sleeps                              # the fixed 3 s wait is gone


def test_a_search_still_showing_the_timeline_is_never_clicked(sleeps, capsys):
    page = _Page([TIMELINE])

    assert mainfetch.trigger_download(page, QUERY) is False

    out = capsys.readouterr().out
    line = ("     ❌ Search results did not replace the home timeline within 10 s; nothing was clicked "
            "— this is not a 'Not found'.")
    assert out.count(line) == 2                          # both passes (the IMP-C2 retry ran once)
    assert out.count("Retry 2/2") == 1 and "Not found (Found" not in out
    assert page.clicked == []
    polls = int(mainfetch.SEARCH_RESULTS_WAIT_S / mainfetch.SEARCH_POLL_S)
    assert sleeps.count(mainfetch.SEARCH_POLL_S) == 2 * polls     # bounded: the wait never exceeds its limit


def test_a_timeline_thumbnail_shown_beside_the_results_is_never_clicked():
    # e.g. the timeline still in the DOM, displayed under the results: both selectors would match its thumbnails.
    page = _Page([{"results": 2, "timeline": 3, "empty": False}])

    assert mainfetch.trigger_download(page, QUERY, index=1) is True

    assert page.clicked == ["result-1"]                  # the second RESULT — not the second thumbnail on the page


def test_no_results_ends_the_wait_early_and_reads_as_not_found(sleeps, capsys):
    page = _Page([TIMELINE, {"results": 0, "timeline": 0, "empty": True}])

    assert mainfetch.trigger_download(page, QUERY) is False

    out = capsys.readouterr().out
    assert out.count("     ⚠️ Not found (Found 0).") == 2 and "❌" not in out
    # One short poll in the first pass; the retry pass sees "No results" at its first look — never the full wait.
    assert sleeps.count(mainfetch.SEARCH_POLL_S) == 1


def test_it_reads_the_results_only_once_they_stop_growing(sleeps, capsys):
    page = _Page([results(2), results(5)])

    assert mainfetch.trigger_download(page, QUERY, index=4) is True

    assert page.clicked == ["result-4"]                  # read too early, index 4 would have been "Not found"
    out = capsys.readouterr().out                        # ...and found on the FIRST pass — not rescued by the
    assert "Retry 2/2" not in out and "Not found" not in out and 5 not in sleeps   # IMP-C2 retry


def test_it_never_reads_while_the_timeline_is_shown(sleeps):
    # Results already visible, then the timeline flashes back (the page re-renders): ready only after two
    # looks at results with no timeline in either — never on the look that still shows the timeline.
    page = _Page([results(3), {"results": 3, "timeline": 2, "empty": False}, results(3)])

    assert mainfetch.trigger_download(page, QUERY) is True

    assert page.clicked == ["result-0"]
    assert sleeps.count(mainfetch.SEARCH_POLL_S) == 3 and page.looks == 4


def test_the_most_relevant_strip_above_the_results_is_never_clicked():
    # The live case (tagged name): strip [The Expanse, Mr. Robot, SV] above the date group [SV]. Index 0 used to be the
    # strip's first tile; now it is the first RESULT.
    page = _Page([TIMELINE, strip(3, 1)])

    assert mainfetch.trigger_download(page, QUERY) is True

    assert page.clicked == ["result-0"]


def test_a_search_showing_only_most_relevant_suggestions_is_a_miss(sleeps, capsys):
    # The live case (untagged name): no result at all, only the strip with The Expanse. Nothing is clicked, and the wait
    # ends after SEARCH_SUGGESTIONS_ONLY_LOOKS looks at the strip — not the full SEARCH_RESULTS_WAIT_S.
    page = _Page([TIMELINE, strip(1)])

    assert mainfetch.trigger_download(page, QUERY) is False

    out = capsys.readouterr().out
    assert out.count("     ⚠️ Not found (Found 0).") == 2 and "❌" not in out     # both passes (the IMP-C2 retry)
    assert page.clicked == []
    looks = mainfetch.SEARCH_SUGGESTIONS_ONLY_LOOKS
    assert sleeps.count(mainfetch.SEARCH_POLL_S) == looks + (looks - 1)    # 1 timeline look + 4 strip looks; then 4
    assert looks * mainfetch.SEARCH_POLL_S <= 3.0                          # no slower than the old fixed 3 s read


def test_results_that_render_after_the_strip_are_waited_for(sleeps):
    # A strip shown alone for fewer than SEARCH_SUGGESTIONS_ONLY_LOOKS looks is not yet a miss: results may follow.
    page = _Page([TIMELINE, strip(3), strip(3), strip(3, 1)])

    assert mainfetch.trigger_download(page, QUERY) is True

    assert page.clicked == ["result-0"] and sleeps.count(mainfetch.SEARCH_POLL_S) == 4


def test_a_busy_page_is_simply_looked_at_again():
    page = _Page([results(3)], busy_looks=2)

    assert mainfetch.trigger_download(page, QUERY) is True

    assert page.clicked == ["result-0"] and page.scripts.count("look") == 4


def test_a_search_that_never_reached_photos_search_still_says_so(sleeps, capsys):
    page = _Page([TIMELINE], search_runs=False)

    assert mainfetch.trigger_download(page, QUERY) is False

    out = capsys.readouterr().out
    line = ("     ❌ Search did not run (or had not started yet): Google Photos is still on /, "
            "not on a /search/ results page — this is not a 'Not found'.")
    assert out.count(line) == 2 and page.clicked == []
    assert "look" not in page.scripts                    # no page script runs before the URL is a search page


def test_a_stand_in_without_page_scripts_is_read_at_once(sleeps):
    # The IMP-C2 / IMP-C29 test doubles run no page scripts (execute_script returns nothing): the wait reads such a
    # page as before it existed, so their pins hold unchanged.
    class _StandIn(_Page):
        def execute_script(self, script, *args):
            if "click" in script:
                self.clicked.append(args[0].name)

    page = _StandIn([results(2)])

    assert mainfetch.trigger_download(page, QUERY) is True

    assert page.clicked == ["result-0"]
    assert mainfetch.SEARCH_POLL_S not in sleeps


def test_the_page_scripts_click_nothing_and_the_wait_is_bounded():
    for script in (gpweb.SEARCH_STATE_JS, gpweb.TIMELINE_LINKS_JS):
        assert "click" not in script and "submit" not in script
    assert mainfetch.SEARCH_RESULTS_WAIT_S >= 7                         # IMP-C29 saw results take up to ~7 s
    assert int(mainfetch.SEARCH_RESULTS_WAIT_S / mainfetch.SEARCH_POLL_S) == 20


# ---------------------------------------------------------------------------
# digest pins — re-pinned on purpose by IMP-C25 Step 10 h (IMP-C35); a change to the legacy S4 path is a decision.
# The search-wait pin was re-pinned on purpose once more for the "Most relevant to your search" strip (live check
# 2026-10-05). trigger_download was re-pinned on purpose by IMP-C25 thfix (2026-10-28, the user's "Fix inside C25"):
# its IMP-C6 zero streak raises PhotosThrottledError — every attempt passed check_session_alive on a Google Photos
# page, so three empty searches are a throttle, never the sign-out it used to report; nothing else changed.
# ---------------------------------------------------------------------------
def _digest(*parts):
    text = "\n".join(p if isinstance(p, str) else inspect.getsource(p) for p in parts).replace("\r\n", "\n")
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


TRIGGER_DOWNLOAD_DIGEST = "43702178cb8f7f50ea7f55489067b52f53374848ebf2dd2aa0914f0674d124e1"
SEARCH_WAIT_DIGEST = "3894100f93b2fb574f2eb91ea85fe75a5f1b5d6d7e4884130fd36dfd60f91936"


def test_trigger_download_source_is_pinned():
    assert _digest(mainfetch.trigger_download) == TRIGGER_DOWNLOAD_DIGEST, (
        "trigger_download (the legacy S4 stage) changed — re-pin only as a reviewed decision")


def test_the_search_wait_it_relies_on_is_pinned():
    assert _digest(mainfetch._await_search_results, mainfetch._drop_timeline, mainfetch._search_page_problem,
                   gpweb.SEARCH_STATE_JS, gpweb.TIMELINE_LINKS_JS) == SEARCH_WAIT_DIGEST, (
        "the search wait / timeline guard changed — re-pin only as a reviewed decision")
