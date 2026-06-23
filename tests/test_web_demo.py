"""Server-side DEMO / SAFE-mode tests for webui.server (IMP-E14).

`python main.py web --demo` must serve a build where EVERY action is SIMULATED:
no real main.cmd_* / Selenium / library mutation can ever run. These tests prove
that contract via FastAPI TestClient + monkeypatch only — they never touch real
C:\\Media files or real library_*.json (no sandbox writes are even needed,
because demo mode never reaches the library).

The default (demo=False) path must be byte-unchanged: actions still route to the
real ACTION_TABLE runner. The last test asserts that with a sentinel runner.
"""

import time as _time

import pytest

# Skip the whole module if fastapi (or httpx, its TestClient dep) is absent.
pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient  # noqa: E402 (after importorskip guard)

import main  # noqa: E402

# Secure-by-default auth (IMP-E15) is always enforced on /api/*; these demo-mode
# endpoint tests drive the API as the LOCAL OWNER (TestClient host "testclient"
# is non-loopback => would 401), so run the module as the genuine-local admin.
# Demo mode itself is unaffected by auth. See the web_as_local_admin fixture.
pytestmark = pytest.mark.usefixtures("web_as_local_admin")


# ---------------------------------------------------------------------------
# Shared poll helper (mirrors tests/test_web_endpoints.py)
# ---------------------------------------------------------------------------

def _poll(client, job_id, timeout=20):
    """Poll GET /api/job/{job_id} until status is done or error, or timeout."""
    deadline = _time.monotonic() + timeout
    data = None
    while _time.monotonic() < deadline:
        r = client.get(f"/api/job/{job_id}")
        assert r.status_code == 200, f"job poll returned {r.status_code}: {r.text}"
        data = r.json()
        if data["status"] in ("done", "error"):
            return data
        _time.sleep(0.05)
    raise TimeoutError(
        f"job {job_id} did not finish within {timeout}s; last record: {data}"
    )


# ---------------------------------------------------------------------------
# /api/mode reflects the flag in both builds.
# ---------------------------------------------------------------------------

def test_mode_endpoint_reports_demo_true():
    from webui.server import create_app

    client = TestClient(create_app(demo=True))
    r = client.get("/api/mode")
    assert r.status_code == 200, r.text
    assert r.json() == {"demo": True}


def test_mode_endpoint_reports_demo_false_by_default():
    from webui.server import create_app

    # Default (no flag) must report demo:false.
    client = TestClient(create_app())
    r = client.get("/api/mode")
    assert r.status_code == 200, r.text
    assert r.json() == {"demo": False}


# ---------------------------------------------------------------------------
# Demo mode SIMULATES fetch_restore: 202 -> done, real cmd_fetch_restore is
# NEVER called, the DEMO banner line is in the output, and progress advances to
# done == total.
# ---------------------------------------------------------------------------

def test_demo_fetch_restore_simulated_not_real(monkeypatch):
    from webui.server import create_app

    # Trip-wire: if the real fetch path is EVER invoked in demo mode, fail loudly.
    calls = []

    def _boom(*args, **kwargs):
        calls.append((args, kwargs))
        raise AssertionError(
            "real main.cmd_fetch_restore was called in demo mode — SAFETY BREACH"
        )

    monkeypatch.setattr(main, "cmd_fetch_restore", _boom)

    client = TestClient(create_app(demo=True))

    r = client.post("/api/action/fetch_restore", json={"id": "mov-en-2020-demofilm"})
    assert r.status_code == 202, f"Expected 202, got {r.status_code}: {r.text}"
    job_id = r.json()["job_id"]

    job = _poll(client, job_id)

    # The simulator returns truthy -> the job must reach "done", never "error".
    assert job["status"] == "done", (
        f"demo fetch_restore ended status={job['status']!r}. "
        f"Output:\n{job.get('output', '')}"
    )

    # The real fetch was NEVER invoked.
    assert calls == [], f"real cmd_fetch_restore was invoked {len(calls)} time(s)"

    # The DEMO banner line is present in the captured output.
    assert "DEMO MODE — no real command executed (simulated)." in job["output"], (
        f"DEMO banner line missing from output:\n{job['output']}"
    )

    # Progress advanced to completion (simulator emitted MOVED markers).
    prog = job["progress"]
    assert prog["total"] > 0, f"expected a non-zero total, got {prog}"
    assert prog["done"] == prog["total"], (
        f"expected done==total at completion, got {prog}"
    )


# ---------------------------------------------------------------------------
# The confirm-gate is unchanged in demo: replace without confirm still 409s
# (and obviously deletes nothing — no job is even created).
# ---------------------------------------------------------------------------

def test_demo_replace_without_confirm_still_409(monkeypatch):
    from webui.server import create_app

    # Belt-and-suspenders: the real replace must never run in demo either.
    def _boom(*args, **kwargs):
        raise AssertionError("real main.cmd_replace was called in demo mode")

    monkeypatch.setattr(main, "cmd_replace", _boom)

    client = TestClient(create_app(demo=True))

    r = client.post("/api/action/replace", json={"id": "mov-en-2020-demofilm"})
    assert r.status_code == 409, f"Expected 409, got {r.status_code}: {r.text}"

    r2 = client.post(
        "/api/action/replace", json={"id": "mov-en-2020-demofilm", "confirm": False}
    )
    assert r2.status_code == 409


# ---------------------------------------------------------------------------
# An unknown action is still 404 in demo (no name reaches a real runner).
# ---------------------------------------------------------------------------

def test_demo_unknown_action_still_404():
    from webui.server import create_app

    client = TestClient(create_app(demo=True))
    r = client.post("/api/action/does_not_exist", json={})
    assert r.status_code == 404, f"Expected 404, got {r.status_code}: {r.text}"


# ---------------------------------------------------------------------------
# Demo replace WITH confirm simulates (does not run the real cmd_replace) and
# reaches "done" — proving even the destructive action is safely simulated once
# confirmed, and that "done" is reached for an action NOT in _NONE_IS_SUCCESS.
# ---------------------------------------------------------------------------

def test_demo_replace_with_confirm_simulates_to_done(monkeypatch):
    from webui.server import create_app

    def _boom(*args, **kwargs):
        raise AssertionError("real main.cmd_replace was called in demo mode")

    monkeypatch.setattr(main, "cmd_replace", _boom)

    client = TestClient(create_app(demo=True))

    r = client.post(
        "/api/action/replace", json={"id": "mov-en-2020-demofilm", "confirm": True}
    )
    assert r.status_code == 202, f"Expected 202, got {r.status_code}: {r.text}"
    job = _poll(client, r.json()["job_id"])
    assert job["status"] == "done", (
        f"demo replace (confirmed) ended status={job['status']!r}. "
        f"Output:\n{job.get('output', '')}"
    )
    assert "DEMO MODE — no real command executed (simulated)." in job["output"]


# ---------------------------------------------------------------------------
# DEFAULT (demo=False) routes to the REAL ACTION_TABLE runner — sentinel proof.
#
# We monkeypatch main.cmd_sort (the callable behind the "sort" runner) with a
# recording sentinel that returns None (sort's success convention -> done). If
# default mode still dispatches through ACTION_TABLE -> _run_sort -> cmd_sort,
# the sentinel is invoked exactly once. This is the "default behavior unchanged"
# guard. No library or disk is touched because the sentinel replaces cmd_sort.
# ---------------------------------------------------------------------------

def test_default_routes_to_real_runner(monkeypatch):
    from webui.server import create_app

    called = {"n": 0}

    def _sentinel_sort():
        called["n"] += 1
        # cmd_sort returns None on success; "sort" is in _NONE_IS_SUCCESS so the
        # worker maps None -> done.
        return None

    monkeypatch.setattr(main, "cmd_sort", _sentinel_sort)

    client = TestClient(create_app())  # default: demo=False

    r = client.post("/api/action/sort", json={})
    assert r.status_code == 202, f"Expected 202, got {r.status_code}: {r.text}"
    job = _poll(client, r.json()["job_id"])

    assert job["status"] == "done", (
        f"default sort ended status={job['status']!r}. Output:\n{job.get('output','')}"
    )
    assert called["n"] == 1, (
        f"real cmd_sort was called {called['n']} time(s); expected exactly 1 "
        "(default mode must route to the real ACTION_TABLE runner)"
    )
