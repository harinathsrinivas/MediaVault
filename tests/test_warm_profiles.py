"""IMP-C17 — Unit tests for tools/warm_profiles.py (the keep-alive runner).

Never touch real C:\\Media files or real library_*.json.
Run `python -m pytest` and fix failures before marking the step done.

Exercises warm_all() with all I/O boundaries fully mocked:
  - mainfetch.init_driver        → returns a _FakeDriver or None
  - mainfetch.check_session_alive → real implementation against _FakeDriver.current_url
  - tools.warm_profiles.send_toast → no-op recorder
  - tools.warm_profiles.LOG_PATH   → redirected to tmp_path
  - tools.warm_profiles.fetch_session_lock → no-op nullcontext (overridden for (d))
  - tools.warm_profiles.time.sleep → no-op

No real browser is launched; nothing is written under ~/.mediavault.
"""
import contextlib

import pytest
import mainfetch
import tools.warm_profiles as warm_profiles


# ---------------------------------------------------------------------------
# Minimal fake driver
# ---------------------------------------------------------------------------
class _FakeDriver:
    """Minimal driver with a settable current_url, no-op get() and quit()."""
    def __init__(self, current_url="https://photos.google.com/"):
        self.current_url = current_url

    def get(self, *a, **k):
        return None

    def quit(self):
        return None


# ---------------------------------------------------------------------------
# Autouse fixture: redirect LOG_PATH, stub sleep, stub fetch_session_lock
# ---------------------------------------------------------------------------
@pytest.fixture(autouse=True)
def _common_stubs(tmp_path, monkeypatch):
    """Redirect LOG_PATH → tmp_path; stub sleep + fetch_session_lock for every test."""
    log_file = tmp_path / "warm.log"
    monkeypatch.setattr(warm_profiles, "LOG_PATH", str(log_file))
    monkeypatch.setattr(warm_profiles.time, "sleep", lambda *a, **k: None)
    monkeypatch.setattr(
        warm_profiles,
        "fetch_session_lock",
        lambda *a, **k: contextlib.nullcontext(),
    )
    # Also stub mvcommon.MV_LOG_DIR so _append_log's makedirs stays in tmp_path.
    import mvcommon
    monkeypatch.setattr(mvcommon, "MV_LOG_DIR", str(tmp_path))
    yield log_file


# ---------------------------------------------------------------------------
# (a) ALL HEALTHY → returns 0, no toast, log line contains OK for each profile
# ---------------------------------------------------------------------------
def test_all_healthy(monkeypatch, _common_stubs):
    log_file = _common_stubs

    # All three profiles get a healthy driver
    drivers = {key: _FakeDriver("https://photos.google.com/") for key in mainfetch.CHROME_PROFILES}
    monkeypatch.setattr(mainfetch, "init_driver", lambda key: drivers.get(key))

    toast_calls = []
    monkeypatch.setattr(warm_profiles, "send_toast", lambda *a, **k: toast_calls.append((a, k)))

    result = warm_profiles.warm_all()

    assert result == 0
    assert toast_calls == [], "send_toast must NOT be called when all profiles are healthy"

    # Log file should have been written with OK for each profile
    log_content = log_file.read_text(encoding="utf-8")
    for key in mainfetch.CHROME_PROFILES:
        assert f"{key}=OK" in log_content, f"Expected '{key}=OK' in log line"


# ---------------------------------------------------------------------------
# (b) ONE PROFILE LOGGED OUT → returns 1, toast called once naming that profile
# ---------------------------------------------------------------------------
def test_one_profile_logged_out(monkeypatch, _common_stubs):
    log_file = _common_stubs

    profile_keys = list(mainfetch.CHROME_PROFILES.keys())
    logged_out_key = profile_keys[1]  # e.g. "tv"

    def _make_driver(key):
        if key == logged_out_key:
            return _FakeDriver("https://accounts.google.com/ServiceLogin?continue=...")
        return _FakeDriver("https://photos.google.com/")

    monkeypatch.setattr(mainfetch, "init_driver", _make_driver)

    toast_calls = []
    monkeypatch.setattr(warm_profiles, "send_toast", lambda *a, **k: toast_calls.append((a, k)))

    result = warm_profiles.warm_all()

    assert result == 1
    assert len(toast_calls) == 1, "send_toast must be called exactly once"

    # Toast message must name the logged-out profile
    toast_args = toast_calls[0][0]  # positional args to send_toast
    # second arg is the body message
    assert logged_out_key in toast_args[1], (
        f"Toast message should name the logged-out profile '{logged_out_key}'"
    )

    # Log line should show LOGGED_OUT for that profile
    log_content = log_file.read_text(encoding="utf-8")
    assert f"{logged_out_key}=LOGGED_OUT" in log_content


# ---------------------------------------------------------------------------
# (c) init_driver returns None → returns 1, LAUNCH_FAIL recorded, toast called
# ---------------------------------------------------------------------------
def test_launch_fail(monkeypatch, _common_stubs):
    log_file = _common_stubs

    profile_keys = list(mainfetch.CHROME_PROFILES.keys())
    fail_key = profile_keys[0]  # e.g. "movies"

    def _make_driver(key):
        if key == fail_key:
            return None
        return _FakeDriver("https://photos.google.com/")

    monkeypatch.setattr(mainfetch, "init_driver", _make_driver)

    toast_calls = []
    monkeypatch.setattr(warm_profiles, "send_toast", lambda *a, **k: toast_calls.append((a, k)))

    result = warm_profiles.warm_all()

    assert result == 1
    assert len(toast_calls) == 1, "send_toast must be called when a profile fails to launch"

    # Log line should record LAUNCH_FAIL for that profile
    log_content = log_file.read_text(encoding="utf-8")
    assert f"{fail_key}=LAUNCH_FAIL" in log_content


# ---------------------------------------------------------------------------
# (d) SINGLE-FLIGHT: fetch_session_lock raises LockHeldError →
#     returns 0, prints skip line, no driver launched, no toast
# ---------------------------------------------------------------------------
def test_lock_held_skips_warmup(monkeypatch, _common_stubs, capsys):
    log_file = _common_stubs

    def _raise_lock(*a, **k):
        raise warm_profiles.LockHeldError("held by live fetch")

    monkeypatch.setattr(warm_profiles, "fetch_session_lock", _raise_lock)

    init_driver_calls = []
    monkeypatch.setattr(
        mainfetch,
        "init_driver",
        lambda key: init_driver_calls.append(key) or _FakeDriver(),
    )

    toast_calls = []
    monkeypatch.setattr(warm_profiles, "send_toast", lambda *a, **k: toast_calls.append((a, k)))

    result = warm_profiles.warm_all()

    assert result == 0
    assert init_driver_calls == [], "init_driver must NOT be called when lock is held"
    assert toast_calls == [], "send_toast must NOT be called when skipping due to lock"

    out = capsys.readouterr().out
    assert "skip" in out.lower() or "live fetch" in out.lower(), (
        "Expected skip message in stdout when lock is held"
    )


# ---------------------------------------------------------------------------
# (e) --profile <key> subset: warm_all(["anime"]) → only one init_driver call
# ---------------------------------------------------------------------------
def test_single_profile_subset(monkeypatch, _common_stubs):
    init_calls = []

    def _make_driver(key):
        init_calls.append(key)
        return _FakeDriver("https://photos.google.com/")

    monkeypatch.setattr(mainfetch, "init_driver", _make_driver)

    toast_calls = []
    monkeypatch.setattr(warm_profiles, "send_toast", lambda *a, **k: toast_calls.append((a, k)))

    result = warm_profiles.warm_all(["anime"])

    assert result == 0
    assert init_calls == ["anime"], (
        f"Expected exactly one init_driver call for 'anime', got: {init_calls}"
    )
    assert toast_calls == [], "No toast expected when the single profile is healthy"
