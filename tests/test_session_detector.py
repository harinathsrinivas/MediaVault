"""IMP-C17 / IMP-C6 — Google-Photos logged-out detector + early-abort tests.

Exercises the shared session detector that keeps an interactive fetch from
silently downloading nothing once the Chrome profile's Photos login expires:

  - check_session_alive(driver): inspects driver.current_url. True while on a
    photos.google.com host (www./locale subpaths tolerated); raises
    SessionExpiredError when redirected to accounts.google.com or any other
    non-photos host; returns True (never invents a failure) if reading
    current_url itself raises a Selenium fault.
  - trigger_download(driver, query): runs check_session_alive right after
    navigation; a SessionExpiredError propagates past the broad retry arms
    (it is NOT swallowed into a False and is NOT retried).
  - cmd_fetch_route(manual_id): catches SessionExpiredError once and prints a
    remediation pointing at the active profile's --user-data-dir.

Selenium-free, mirroring tests/test_trigger_download_retry.py: a _FakeDriver
with a settable current_url, _NoOpWait/_NoOpActions stubs, and
mainfetch.time.sleep patched to a no-op so nothing ever actually waits.

Never touch real C:\\Media files or real library_*.json.
Run `python -m pytest` and fix failures before marking the step done.
"""
import contextlib

import pytest

import mainfetch


# ---------------------------------------------------------------------------
# Selenium-surface stubs (same shape as test_trigger_download_retry.py)
# ---------------------------------------------------------------------------
class _NoOpActions:
    """ActionChains stub: every builder method returns self; perform() no-ops."""
    def __init__(self, driver=None):
        pass

    def send_keys(self, *a, **k):
        return self

    def key_down(self, *a, **k):
        return self

    def key_up(self, *a, **k):
        return self

    def pause(self, *a, **k):
        return self

    def perform(self, *a, **k):
        return self


class _NoOpWait:
    def __init__(self, *a, **k):
        pass

    def until(self, *a, **k):
        return True


class _FakeDriver:
    """Minimal driver exposing a settable current_url and a get() call counter.

    `current_url` is whatever the test sets it to; trigger_download navigates
    via get() (counted) and then check_session_alive reads current_url.
    find_elements is scripted only for completeness — the detector raises
    before any thumbnail lookup in these tests, so it returns [].
    """
    def __init__(self, current_url="https://photos.google.com/"):
        self.current_url = current_url
        self._get_calls = 0

    def get(self, *a, **k):
        self._get_calls += 1
        return None

    def execute_script(self, *a, **k):
        return None

    def find_elements(self, by, selector):
        return []


class _RaisingUrlDriver:
    """Driver whose current_url property raises a Selenium-style fault on read."""
    @property
    def current_url(self):
        raise RuntimeError("simulated WebDriverException reading current_url")


class _FakeRouteDriver:
    """Truthy driver for cmd_fetch_route: only needs a tolerated no-op quit()."""
    def quit(self):
        return None


@pytest.fixture(autouse=True)
def _stub_selenium(monkeypatch):
    """No-op the Selenium surface + instant sleeps; leave the detector real."""
    monkeypatch.setattr(mainfetch.time, "sleep", lambda *_a, **_k: None)
    monkeypatch.setattr(mainfetch, "WebDriverWait", _NoOpWait)
    monkeypatch.setattr(mainfetch.webdriver, "ActionChains", _NoOpActions)


# ---------------------------------------------------------------------------
# (a) photos.google.com host -> alive (True), incl. www./locale subpath variant
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("url", [
    "https://photos.google.com/",
    "https://photos.google.com/search/anything",
    "https://www.photos.google.com/u/0/photo/abc123",
])
def test_check_session_alive_true_on_photos(url):
    driver = _FakeDriver(current_url=url)
    assert mainfetch.check_session_alive(driver) is True


# ---------------------------------------------------------------------------
# (b) redirected to accounts.google.com -> SessionExpiredError
# ---------------------------------------------------------------------------
def test_check_session_alive_raises_on_accounts_host():
    driver = _FakeDriver(
        current_url="https://accounts.google.com/ServiceLogin?continue=...",
    )
    with pytest.raises(mainfetch.SessionExpiredError):
        mainfetch.check_session_alive(driver)


# ---------------------------------------------------------------------------
# (c) any other non-photos host -> SessionExpiredError
# ---------------------------------------------------------------------------
def test_check_session_alive_raises_on_other_host():
    driver = _FakeDriver(current_url="https://example.com/foo")
    with pytest.raises(mainfetch.SessionExpiredError):
        mainfetch.check_session_alive(driver)


# ---------------------------------------------------------------------------
# (d) reading current_url itself raises -> True (never invent a logged-out fail)
# ---------------------------------------------------------------------------
def test_check_session_alive_true_when_url_read_raises():
    driver = _RaisingUrlDriver()
    assert mainfetch.check_session_alive(driver) is True


# ---------------------------------------------------------------------------
# (e) trigger_download PROPAGATES SessionExpiredError (no swallow, no retry)
# ---------------------------------------------------------------------------
def test_trigger_download_propagates_session_expired_without_retry():
    # After get(), current_url is the accounts redirect, so check_session_alive
    # (called right after navigation, before any thumbnail lookup) raises on the
    # FIRST attempt. The error must propagate past the broad retry arms.
    driver = _FakeDriver(current_url="https://accounts.google.com/ServiceLogin")

    with pytest.raises(mainfetch.SessionExpiredError):
        mainfetch.trigger_download(driver, "chunk.001")

    # Raised on the first attempt -> no retry navigation happened.
    assert driver._get_calls == 1


# ---------------------------------------------------------------------------
# (f) cmd_fetch_route catches SessionExpiredError and prints the remediation
# ---------------------------------------------------------------------------
def test_cmd_fetch_route_prints_remediation_on_logout(monkeypatch, tmp_path, capsys):
    # A mov-… id routes to the "movies" profile.
    manual_id = "mov-en-2099-testfilm"

    # Inject the logged-out condition at the fetch_single_entry boundary so this
    # test stays focused on cmd_fetch_route's except arm. The REAL propagation
    # through trigger_download is covered by test (e) above.
    def _raise_logged_out(*_a, **_k):
        raise mainfetch.SessionExpiredError("logged out")

    # Keep the real load_library / C:\Media completely out of the picture.
    monkeypatch.setattr(
        mainfetch, "resolve_targets",
        lambda *a, **k: [{"filename": "x.mkv",
                          "folder_path": str(tmp_path),
                          "hash": "deadbeef"}],
    )
    monkeypatch.setattr(mainfetch, "fetch_single_entry", _raise_logged_out)
    monkeypatch.setattr(mainfetch, "init_driver", lambda *a, **k: _FakeRouteDriver())
    # CRITICAL: never create ~/.mediavault/locks/fetch_session.lock in a unit test.
    monkeypatch.setattr(
        mainfetch, "fetch_session_lock",
        lambda *a, **k: contextlib.nullcontext(),
    )

    mainfetch.cmd_fetch_route(manual_id)

    out = capsys.readouterr().out
    assert "is logged out" in out
    assert f"--user-data-dir={mainfetch.CHROME_PROFILES['movies']}" in out


# ---------------------------------------------------------------------------
# (g) IMP-C25 thfix — a throttle is never a sign-out (the R3 dry runs of 2026-10-27: deep in a signed-in session Google
#     server-redirected photos.google.com/ to its page for signed-out visitors, and the check said "logged out")
# ---------------------------------------------------------------------------
ABOUT = "https://www.google.com/photos/about/"


@pytest.mark.parametrize("url, kind", [
    ("https://photos.google.com/", "photos"), ("https://photos.google.com/u/1/search/x", "photos"),
    ("https://www.photos.google.com/photo/abc", "photos"),
    ("https://accounts.google.com/v3/signin/identifier", "signin"), ("https://accounts.google.com/ServiceLogin", "signin"),
    ("https://www.google.com/photos/about", "about"), (ABOUT, "about"),
    ("https://www.google.com/intl/en/photos/about/", "about"),
    ("https://www.google.com/sorry/index", "unusual-traffic"), ("https://google.com/sorry/", "unusual-traffic"),
    ("https://www.google.com/", "other"), ("https://www.google.com/photos/aboutx", "other"),
    ("https://example.com/sorry/x", "other"), ("about:blank", "other"), (None, "other"),
])
def test_page_kind_reads_only_the_host_and_path(url, kind):
    assert mainfetch._page_kind(url) == kind


def test_the_signed_out_page_at_a_sessions_first_look_is_a_sign_out():
    # RESEARCH F26 (live): a signed-out profile lands on Google Photos' page for signed-out visitors at once.
    with pytest.raises(mainfetch.SessionExpiredError) as err:
        mainfetch.check_session_alive(_FakeDriver(current_url=ABOUT), "movies")
    assert type(err.value) is mainfetch.SessionExpiredError                  # NOT the throttle
    assert str(err.value) == "profile 'movies' appears logged out (redirected to www.google.com)"


def test_the_signed_out_page_after_signed_in_photos_is_a_throttle_never_a_sign_out():
    driver = _FakeDriver()
    assert mainfetch.check_session_alive(driver, "movies") is True          # the session shows signed-in Photos …
    driver.current_url = ABOUT                                               # … then Google bounces it (R3 movies)
    with pytest.raises(mainfetch.PhotosThrottledError) as err:
        mainfetch.check_session_alive(driver, "movies")
    assert (err.value.page, err.value.why) == ("about", "Google showed its signed-out page to a session that was "
                                                        "signed in")
    assert isinstance(err.value, mainfetch.SessionExpiredError)              # every sign-out handler still stops on it


def test_a_sign_in_page_is_a_sign_out_even_after_signed_in_photos():
    driver = _FakeDriver()
    mainfetch.check_session_alive(driver)
    driver.current_url = "https://accounts.google.com/ServiceLogin"
    with pytest.raises(mainfetch.SessionExpiredError) as err:
        mainfetch.check_session_alive(driver)
    assert type(err.value) is mainfetch.SessionExpiredError


@pytest.mark.parametrize("url, page, why", [
    ("https://www.google.com/sorry/index", "unusual-traffic", "Google showed its 'unusual traffic' page"),
    ("https://example.com/foo", "other", "the browser landed on example.com, not on Google Photos"),
    ("about:blank", "other", "the browser landed on an empty page, not on Google Photos")])
def test_unusual_traffic_and_other_pages_are_throttles_even_at_the_first_look(url, page, why):
    with pytest.raises(mainfetch.PhotosThrottledError) as err:
        mainfetch.check_session_alive(_FakeDriver(current_url=url))
    assert (err.value.page, err.value.why) == (page, why)


def test_the_legacy_zero_streak_is_a_throttle_never_a_sign_out():
    # Every attempt passed check_session_alive on a Google Photos page: three empty searches are a throttle.
    driver = _FakeDriver()
    assert mainfetch.trigger_download(driver, "x") is False
    assert mainfetch.trigger_download(driver, "x") is False
    with pytest.raises(mainfetch.PhotosThrottledError) as err:
        mainfetch.trigger_download(driver, "x")
    assert (err.value.page, err.value.why) == ("streak", "3 searches in a row found nothing although the profile is "
                                                         "signed in")


@pytest.mark.parametrize("page, hint", [("search", False), ("about", True)])
def test_cmd_fetch_route_prints_the_throttle_line_never_logged_out(monkeypatch, tmp_path, capsys, page, hint):
    def _raise_throttled(*_a, **_k):
        raise mainfetch.PhotosThrottledError("a search for an item known to be there found nothing", page=page)

    monkeypatch.setattr(mainfetch, "resolve_targets",
                        lambda *a, **k: [{"filename": "x.mkv", "folder_path": str(tmp_path), "hash": "deadbeef"}])
    monkeypatch.setattr(mainfetch, "fetch_single_entry", _raise_throttled)
    monkeypatch.setattr(mainfetch, "init_driver", lambda *a, **k: _FakeRouteDriver())
    monkeypatch.setattr(mainfetch, "fetch_session_lock", lambda *a, **k: contextlib.nullcontext())

    assert mainfetch.cmd_fetch_route("mov-en-2099-testfilm") is None       # the exit stays today's

    out = capsys.readouterr().out
    assert ("⏸️ Google Photos is throttled in 'movies' (a search for an item known to be there found nothing) — the "
            "fetch stops here; files already fetched are kept and skipped. Re-run the same fetch in about 30 "
            "minutes.") in out
    assert "logged out" not in out and "not found" not in out.lower() and "Batch Processing Complete" not in out
    assert ("If Chrome on this profile still shows Google Photos' signed-out page after the pause, sign in "
            f"(--user-data-dir={mainfetch.CHROME_PROFILES['movies']}).") in out if hint else "sign in" not in out
