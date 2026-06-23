"""Web /api/* auth contract tests — MINTED-token model (IMP-E15).

MIGRATION NOTE: IMP-E15 REPLACED the old static ``web.token`` (a single shared
secret in mvconfig.json, with a refuse-to-start guard for non-localhost binds)
with admin-minted, expiring, revocable tokens stored in ``mvtokens.json``. The
old static-token + startup-guard tests that lived here no longer apply (there is
no ``mvcommon.web_token`` and no startup guard to test). The full token-system
coverage — store unit tests, genuine-local-admin detection, the token-management
endpoints, and /api/whoami — lives in ``tests/test_web_tokens.py``.

This module keeps the focused HTTP-layer AUTH-CONTRACT checks that were this
file's historical job:

  * The "no tokens minted -> auth OFF" guarantee the rest of the web suite relies
    on (a request with no credentials still reaches /api/* when the store is
    empty — the unconfigured/local-only mode).
  * Once a token exists, a NON-admin request must present that token via the
    FIXED carrier set — the ``mv_token`` cookie OR the ``X-MediaVault-Token``
    header OR a ``?token=`` query param — else 401, and a WRONG token is 401.
  * The static SPA shell (index, *.js, *.css) is served WITHOUT auth so the page
    can load to prompt for a token.
  * /api/open-folder keeps its genuine-local-admin rule LAYERED ON TOP of auth: a
    valid token does NOT let a remote peer open a folder.

The token store is monkeypatched to a temp file (never the real mvtokens.json),
and a request's client host is set via ``TestClient(app, client=(host, port))``
to simulate the genuine-local admin vs. a remote peer. No real C:\\Media files or
real library_*.json / mvconfig.json are touched.
"""

import pytest

# Skip the whole module if fastapi (or httpx, its TestClient dep) is absent.
pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient  # noqa: E402 (after importorskip guard)

import mvcommon  # noqa: E402


@pytest.fixture()
def token_store(tmp_path, monkeypatch):
    """Redirect mvcommon's token store to a temp file (never the real one)."""
    store = tmp_path / "mvtokens.json"
    assert str(store) != mvcommon.MVTOKENS_PATH
    monkeypatch.setattr(mvcommon, "MVTOKENS_PATH", str(store))
    yield store


def _remote_client(app):
    """TestClient whose requests look like a remote (non-loopback) peer."""
    return TestClient(app, client=("100.64.0.9", 5))


# ---------------------------------------------------------------------------
# (1) No tokens minted -> auth is OFF (the frictionless default the suite relies
#     on). Even a remote peer reaches /api/* when the store is empty.
# ---------------------------------------------------------------------------

def test_empty_store_means_auth_off(token_store, sandbox_entry):
    """With an empty token store, GET /api/items is 200 with no credentials —
    today's behaviour, which the rest of the web suite depends on."""
    from webui.server import create_app

    client = _remote_client(create_app())
    r = client.get("/api/items")
    assert r.status_code == 200, f"empty-store read must be 200, got {r.status_code}: {r.text}"


# ---------------------------------------------------------------------------
# (2) Token minted -> a non-admin request is gated; accepted via header, cookie,
#     OR ?token=; 401 on no/wrong token (the FIXED carrier contract).
# ---------------------------------------------------------------------------

def test_minted_token_gates_nonadmin_reads(token_store, sandbox_entry):
    """GET /api/items from a remote peer: 401 with no/wrong token; 200 via header,
    cookie, query."""
    from webui.server import create_app

    _, raw, _ = mvcommon.mint_token("device", 3600)
    client = _remote_client(create_app())

    # No credentials -> 401 with the exact contract body.
    r = client.get("/api/items")
    assert r.status_code == 401, f"expected 401 unauthenticated, got {r.status_code}"
    assert r.json() == {"detail": "Access token required or expired"}, (
        f"401 body must be the fixed contract, got {r.text!r}"
    )

    # Header carrier.
    r = client.get("/api/items", headers={"X-MediaVault-Token": raw})
    assert r.status_code == 200, f"header token -> {r.status_code}: {r.text}"

    # Cookie carrier (raw Cookie header — exercises request.cookies.get path).
    r = client.get("/api/items", headers={"Cookie": f"mv_token={raw}"})
    assert r.status_code == 200, f"cookie token -> {r.status_code}: {r.text}"

    # Query carrier.
    r = client.get(f"/api/items?token={raw}")
    assert r.status_code == 200, f"query token -> {r.status_code}: {r.text}"

    # Wrong token -> 401.
    r = client.get("/api/items", headers={"X-MediaVault-Token": "wrong"})
    assert r.status_code == 401, f"wrong token must be 401, got {r.status_code}"
    assert r.json() == {"detail": "Access token required or expired"}


# ---------------------------------------------------------------------------
# (3) The static SPA shell is served WITHOUT auth even when a token is set, so
#     the page can load to prompt the user for the token.
# ---------------------------------------------------------------------------

def test_static_shell_unauthenticated_when_token_set(token_store):
    """With a token minted, GET / (index) and GET /app.js return 200 with no
    credentials from a remote peer — only /api/* is gated, never the SPA shell."""
    from webui.server import create_app

    mvcommon.mint_token("device", 3600)
    client = _remote_client(create_app())

    r = client.get("/")
    assert r.status_code == 200, f"index must load unauthenticated, got {r.status_code}"

    r = client.get("/app.js")
    assert r.status_code == 200, f"/app.js must load unauthenticated, got {r.status_code}"


# ---------------------------------------------------------------------------
# (4) /api/open-folder keeps its genuine-local-admin rule LAYERED ON TOP of auth:
#     a valid token does NOT let a non-admin client open a folder.
# ---------------------------------------------------------------------------

def test_open_folder_admin_rule_survives_auth(token_store, sandbox_entry):
    """With a token minted AND correctly presented, POST /api/open-folder from a
    remote peer returns 403 — the genuine-local-admin restriction fires after the
    token check, so the token never widens access. (A 200 here would mean the
    token bypassed the admin gate.)"""
    from webui.server import create_app

    _, raw, _ = mvcommon.mint_token("device", 3600)
    client = _remote_client(create_app())

    # A path inside the sandbox local root, so the ONLY reason to reject is the
    # non-admin client (proving the admin rule, not a path/body error).
    r = client.post(
        "/api/open-folder",
        json={"path": str(sandbox_entry["media_dir"])},
        headers={"X-MediaVault-Token": raw},
    )
    assert r.status_code == 403, (
        f"open-folder from a non-admin client must 403 even WITH a valid token, "
        f"got {r.status_code}: {r.text}"
    )
