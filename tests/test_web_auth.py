"""Web shared-token auth + non-localhost startup-guard tests (IMP-E15).

Covers the two halves of IMP-E15:

  * webui.server's `/api/*` token middleware — when `mvcommon.web_token()` is
    empty, auth is OFF (today's behaviour, the whole suite stays frictionless);
    when it returns a token, EVERY `/api/*` request must present that exact token
    via the `mv_token` cookie OR the `X-MediaVault-Token` header OR a `?token=`
    query param, else 401 `{"detail":"Token required"}`. The SPA shell (index,
    *.js, *.css) is served WITHOUT auth so the page can load to prompt for the
    token, and `/api/open-folder`'s localhost-only rule still fires on top of a
    valid token (it only ever narrows access).

  * main.cmd_web's startup guard — binding to a NON-localhost host with NO token
    is REFUSED (uvicorn.run is never called); a localhost bind needs no token;
    and a non-localhost bind WITH a token proceeds and auto-opens the local
    browser at a `?token=` URL.

Auth is driven purely by monkeypatching `mvcommon.web_token` (the request-time
getter the middleware and cmd_web both read) — NO real mvconfig.json is written.
uvicorn.run + webbrowser.open are stubbed so the startup-guard tests launch
nothing real. Sandbox fixtures back `/api/items` with data. No real C:\\Media
files or real library_*.json / mvconfig.json are ever touched.
"""

import json
import time as _time

import pytest

# Skip the whole module if fastapi (or httpx, its TestClient dep) is absent —
# mirrors tests/test_web_endpoints.py / tests/test_web_demo.py.
pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient  # noqa: E402 (after importorskip guard)

import main  # noqa: E402
import mvcommon  # noqa: E402

_TOKEN = "secret123"


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _poll(client, job_id, headers=None, timeout=20):
    """Poll GET /api/job/{job_id} until status is done or error, or timeout.

    `headers` carries the auth token when a token is configured (the job-poll
    route is itself under /api/ and therefore gated)."""
    deadline = _time.monotonic() + timeout
    data = None
    while _time.monotonic() < deadline:
        r = client.get(f"/api/job/{job_id}", headers=headers or {})
        assert r.status_code == 200, f"job poll returned {r.status_code}: {r.text}"
        data = r.json()
        if data["status"] in ("done", "error"):
            return data
        _time.sleep(0.05)
    raise TimeoutError(
        f"job {job_id} did not finish within {timeout}s; last record: {data}"
    )


def _sentinel_sort(monkeypatch):
    """Replace main.cmd_sort with a recording sentinel (returns None; "sort" is
    in the server's _NONE_IS_SUCCESS set so None -> job 'done'). Returns the
    call-count dict so a test can assert the real runner did/did not execute.
    Touches no library or disk because the sentinel stands in for cmd_sort."""
    called = {"n": 0}

    def _stub():
        called["n"] += 1
        return None

    monkeypatch.setattr(main, "cmd_sort", _stub)
    return called


# ---------------------------------------------------------------------------
# (1) No token configured -> auth is OFF (today's behaviour preserved).
# ---------------------------------------------------------------------------

def test_no_token_means_auth_off(sandbox_entry, monkeypatch):
    """With web_token() == "" the /api/* middleware is a no-op: GET /api/items
    returns 200 with no credentials, and a (stubbed) action still runs to done.
    This is the frictionless default the rest of the suite relies on."""
    from webui.server import create_app

    monkeypatch.setattr(mvcommon, "web_token", lambda: "")
    called = _sentinel_sort(monkeypatch)

    client = TestClient(create_app())

    # Read route: no token, still 200 (and carries the seeded sandbox entry).
    r = client.get("/api/items")
    assert r.status_code == 200, f"/api/items -> {r.status_code}: {r.text}"

    # Action route: enqueues + runs the real (sentinel) runner with no token.
    r = client.post("/api/action/sort", json={})
    assert r.status_code == 202, f"/api/action/sort -> {r.status_code}: {r.text}"
    job = _poll(client, r.json()["job_id"])
    assert job["status"] == "done", f"sort ended status={job['status']!r}"
    assert called["n"] == 1, (
        f"real cmd_sort ran {called['n']}x; expected 1 (auth off must not block it)"
    )


# ---------------------------------------------------------------------------
# (2) Token configured -> every /api/* request is gated.
#
# 401 without a token; accepted via header, cookie, OR ?token=; 401 on a wrong
# token. Covers a read route (GET /api/items) AND an action route (POST
# /api/action/sort).
# ---------------------------------------------------------------------------

def test_token_gates_api_reads(sandbox_entry, monkeypatch):
    """GET /api/items: 401 with no/ wrong token; 200 via header, cookie, query."""
    from webui.server import create_app

    monkeypatch.setattr(mvcommon, "web_token", lambda: _TOKEN)
    client = TestClient(create_app())

    # No credentials -> 401 with the exact contract body.
    r = client.get("/api/items")
    assert r.status_code == 401, f"expected 401 unauthenticated, got {r.status_code}"
    assert r.json() == {"detail": "Token required"}, (
        f"401 body must be the fixed contract, got {r.text!r}"
    )

    # Header carrier.
    r = client.get("/api/items", headers={"X-MediaVault-Token": _TOKEN})
    assert r.status_code == 200, f"header token -> {r.status_code}: {r.text}"

    # Cookie carrier (sent as a raw Cookie header — exercises the same
    # request.cookies.get("mv_token") path without the deprecated per-request
    # cookies= kwarg that ambiguously persists on the client).
    r = client.get("/api/items", headers={"Cookie": f"mv_token={_TOKEN}"})
    assert r.status_code == 200, f"cookie token -> {r.status_code}: {r.text}"

    # Query carrier.
    r = client.get(f"/api/items?token={_TOKEN}")
    assert r.status_code == 200, f"query token -> {r.status_code}: {r.text}"

    # Wrong token (header) -> 401, not accepted.
    r = client.get("/api/items", headers={"X-MediaVault-Token": "wrong"})
    assert r.status_code == 401, f"wrong header token must be 401, got {r.status_code}"
    assert r.json() == {"detail": "Token required"}


def test_token_gates_api_actions(monkeypatch):
    """POST /api/action/sort: 401 without a token (the action never enqueues);
    202 -> done when the token is presented via header."""
    from webui.server import create_app

    monkeypatch.setattr(mvcommon, "web_token", lambda: _TOKEN)
    called = _sentinel_sort(monkeypatch)

    client = TestClient(create_app())

    # No token: rejected at the middleware, before the route -> sentinel unused.
    r = client.post("/api/action/sort", json={})
    assert r.status_code == 401, f"unauthenticated action must 401, got {r.status_code}"
    assert r.json() == {"detail": "Token required"}
    assert called["n"] == 0, "a 401'd action must never reach the real runner"

    # With the token: 202, and the job runs to done (the sentinel executes once).
    headers = {"X-MediaVault-Token": _TOKEN}
    r = client.post("/api/action/sort", json={}, headers=headers)
    assert r.status_code == 202, f"authenticated action -> {r.status_code}: {r.text}"
    job = _poll(client, r.json()["job_id"], headers=headers)
    assert job["status"] == "done", f"sort ended status={job['status']!r}"
    assert called["n"] == 1, (
        f"real cmd_sort ran {called['n']}x; expected exactly 1 once authenticated"
    )


# ---------------------------------------------------------------------------
# (3) The static SPA shell is served WITHOUT auth even when a token is set, so
#     the page can load to prompt the user for the token.
# ---------------------------------------------------------------------------

def test_static_shell_unauthenticated_when_token_set(monkeypatch):
    """With a token configured, GET / (index) and GET /app.js return 200 with no
    credentials — only /api/* is gated, never the SPA shell."""
    from webui.server import create_app

    monkeypatch.setattr(mvcommon, "web_token", lambda: _TOKEN)
    client = TestClient(create_app())

    r = client.get("/")
    assert r.status_code == 200, f"index must load unauthenticated, got {r.status_code}"

    r = client.get("/app.js")
    assert r.status_code == 200, f"/app.js must load unauthenticated, got {r.status_code}"


# ---------------------------------------------------------------------------
# (4) /api/open-folder keeps its localhost-only rule LAYERED ON TOP of auth: a
#     valid token does NOT let a non-local client open a folder.
# ---------------------------------------------------------------------------

def test_open_folder_localhost_rule_survives_auth(sandbox_entry, monkeypatch):
    """With a token set AND correctly presented, POST /api/open-folder from the
    TestClient (whose client host is "testclient", i.e. non-local) returns 403 —
    the localhost restriction fires after the token check, so the token never
    widens access. (A 200 here would mean the token bypassed the localhost gate.)
    """
    from webui.server import create_app

    monkeypatch.setattr(mvcommon, "web_token", lambda: _TOKEN)
    client = TestClient(create_app())

    # A path inside the sandbox local root, so the ONLY reason to reject is the
    # non-local client host (proving the localhost rule, not a path/body error).
    r = client.post(
        "/api/open-folder",
        json={"path": str(sandbox_entry["media_dir"])},
        headers={"X-MediaVault-Token": _TOKEN},
    )
    assert r.status_code == 403, (
        f"open-folder from a non-local client must 403 even WITH a valid token, "
        f"got {r.status_code}: {r.text}"
    )


# ---------------------------------------------------------------------------
# (5) cmd_web startup guard: refuse a non-localhost bind with no token; allow a
#     localhost bind without a token; allow a non-localhost bind WITH a token
#     (and auto-open the local browser at a ?token= URL).
#
# uvicorn.run is replaced with a record-only sentinel and webbrowser.open is
# stubbed, so cmd_web returns immediately and launches nothing real. host/port
# are passed explicitly so the resolver never reads a real mvconfig.json.
# ---------------------------------------------------------------------------

def test_cmd_web_startup_guard(monkeypatch, capsys):
    import uvicorn

    run_calls = []
    monkeypatch.setattr(uvicorn, "run", lambda *a, **k: run_calls.append((a, k)))

    browser_calls = []
    monkeypatch.setattr(main.webbrowser, "open", lambda url: browser_calls.append(url))

    # (a) Non-local bind + NO token -> REFUSED: uvicorn.run is never reached and
    #     a refusal is printed. (open_browser=False so the auto-open path, which
    #     would only run on the proceed branch, is irrelevant here.)
    monkeypatch.setattr(mvcommon, "web_token", lambda: "")
    main.cmd_web(host="0.0.0.0", port=8765, open_browser=False)
    assert run_calls == [], "uvicorn.run must NOT run for a non-local bind with no token"
    out = capsys.readouterr().out
    assert "Refusing to start" in out, f"expected a refusal message, got:\n{out}"

    # (b) Localhost bind + NO token -> PROCEEDS (a local bind needs no token).
    main.cmd_web(host="127.0.0.1", port=8765, open_browser=False)
    assert len(run_calls) == 1, (
        f"localhost bind must proceed to uvicorn.run; calls={len(run_calls)}"
    )

    # (c) Non-local bind + a token -> PROCEEDS, and auto-opens the LOCAL browser
    #     at a ?token= URL (open_browser left True).
    monkeypatch.setattr(mvcommon, "web_token", lambda: _TOKEN)
    main.cmd_web(host="0.0.0.0", port=8765)
    assert len(run_calls) == 2, (
        f"non-local bind WITH a token must proceed; calls={len(run_calls)}"
    )
    assert browser_calls, "a token bind should auto-open the local browser"
    assert f"?token={_TOKEN}" in browser_calls[-1], (
        f"auto-open URL must carry the token, got {browser_calls[-1]!r}"
    )
