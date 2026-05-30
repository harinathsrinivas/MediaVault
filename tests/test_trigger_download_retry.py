"""IMP-C2 — trigger_download retry tests (Selenium-free).

trigger_download retries its whole attempt ONCE after ~5s when the first pass
returns False (0 thumbnails) OR raises a Selenium fault, then returns the second
pass's result. These tests exercise the public function with a fake driver and
the Selenium surface (WebDriverWait / ActionChains / EC / By / Keys) stubbed to
no-ops, and mainfetch.time.sleep patched so the 5s wait is instant. No real
browser, no real C:\\Media.
"""
import pytest

import mainfetch


# ---------------------------------------------------------------------------
# Selenium-surface stubs
# ---------------------------------------------------------------------------
class _FakeThumb:
    """A clickable thumbnail: displayed and wide enough to pass the > 50 filter."""
    def is_displayed(self):
        return True

    @property
    def size(self):
        return {"width": 100, "height": 100}


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
    """Drives trigger_download's find_elements via a per-call script.

    `thumb_script` is a list consumed one entry per CSS-selector find_elements
    call (the primary 'a[href]' lookup). Each entry is either:
      - a list of thumbnails to return, or
      - an Exception instance/class to raise (simulates a Selenium fault).
    The XPATH fallback always returns [] so the primary path drives behaviour.
    """
    def __init__(self, thumb_script, get_raises_first=False):
        self._script = list(thumb_script)
        self._css_calls = 0
        self._get_raises_first = get_raises_first
        self._get_calls = 0

    def get(self, *a, **k):
        self._get_calls += 1
        if self._get_raises_first and self._get_calls == 1:
            raise RuntimeError("driver blip on navigate")
        return None

    def execute_script(self, *a, **k):
        return None

    def find_elements(self, by, selector):
        # The fallback XPATH lookup returns nothing; only the CSS lookup is
        # scripted, and it is the first find_elements call of each attempt.
        if "background-image" in str(selector):
            return []
        step = self._script[self._css_calls]
        self._css_calls += 1
        if isinstance(step, Exception):
            raise step
        if isinstance(step, type) and issubclass(step, Exception):
            raise step("simulated selenium fault")
        return step


@pytest.fixture(autouse=True)
def _stub_selenium(monkeypatch):
    """No-op the Selenium surface + instant sleeps; leave trigger_download real."""
    monkeypatch.setattr(mainfetch.time, "sleep", lambda *_a, **_k: None)
    monkeypatch.setattr(mainfetch, "WebDriverWait", _NoOpWait)
    monkeypatch.setattr(mainfetch.webdriver, "ActionChains", _NoOpActions)
    # By / Keys / EC are only referenced as attribute lookups; the real ones are
    # harmless constants/classes, so they can stay. find_elements is fully faked.


def _record_sleeps(monkeypatch):
    """Record every sleep duration so tests can assert the 5s retry wait fired
    exactly once (the internal per-attempt waits use other magnitudes)."""
    durations = []
    monkeypatch.setattr(mainfetch.time, "sleep", lambda d=0: durations.append(d))
    return durations


# ---------------------------------------------------------------------------
# (a) 0 thumbnails first, clickable second -> retry self-heals
# ---------------------------------------------------------------------------
def test_zero_then_success_retries_once(monkeypatch):
    durations = _record_sleeps(monkeypatch)
    driver = _FakeDriver(thumb_script=[[], [_FakeThumb()]])

    result = mainfetch.trigger_download(driver, "chunk.001")
    assert result is True
    # Two attempts (one retry).
    assert driver._css_calls == 2
    # The 5s retry wait fired exactly once.
    assert durations.count(5) == 1


# ---------------------------------------------------------------------------
# (b) 0 thumbnails on both attempts -> False after exactly one retry
# ---------------------------------------------------------------------------
def test_zero_both_attempts_returns_false(monkeypatch):
    driver = _FakeDriver(thumb_script=[[], []])

    result = mainfetch.trigger_download(driver, "chunk.001")
    assert result is False
    # Two attempts total (one retry).
    assert driver._css_calls == 2


# ---------------------------------------------------------------------------
# (c) First attempt succeeds -> True, no retry
# ---------------------------------------------------------------------------
def test_first_attempt_success_no_retry(monkeypatch):
    durations = _record_sleeps(monkeypatch)
    driver = _FakeDriver(thumb_script=[[_FakeThumb()]])

    result = mainfetch.trigger_download(driver, "chunk.001")
    assert result is True
    # Only one attempt; the retry-specific 5s sleep never ran.
    assert driver._css_calls == 1
    assert durations.count(5) == 0


def test_first_attempt_success_prints_no_retry_line(monkeypatch, capsys):
    driver = _FakeDriver(thumb_script=[[_FakeThumb()]])
    mainfetch.trigger_download(driver, "chunk.001")
    out = capsys.readouterr().out
    assert "Retry 2/2" not in out


# ---------------------------------------------------------------------------
# (d) First attempt RAISES, second succeeds -> True (exceptions are retried)
# ---------------------------------------------------------------------------
def test_exception_then_success_retries_once(monkeypatch):
    durations = _record_sleeps(monkeypatch)
    # First attempt raises at driver.get() (NOT inside the swallowing CSS
    # try/except), so the exception propagates to the retry wrapper. Second
    # attempt navigates fine and finds a clickable thumb.
    driver = _FakeDriver(thumb_script=[[_FakeThumb()]], get_raises_first=True)

    result = mainfetch.trigger_download(driver, "chunk.001")
    assert result is True
    # Two navigate attempts; only the second reaches the CSS lookup.
    assert driver._get_calls == 2
    assert driver._css_calls == 1
    # The 5s retry wait fired exactly once.
    assert durations.count(5) == 1
