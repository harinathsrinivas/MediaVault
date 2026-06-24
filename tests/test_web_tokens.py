"""Minted web access token tests (IMP-E15).

Covers the BACKEND half of IMP-E15 — the admin-minted, expiring, revocable token
system that REPLACED the old static ``web.token``:

  * mvcommon token store unit tests — mint -> validate(raw)=True; validate(wrong)
    =False; an expired (past-ttl) token validate=False; revoke -> validate=False;
    list() hides the hash/raw and computes ``expired``. The store path is
    monkeypatched to a temp file so a real mvtokens.json is NEVER written.

  * Genuine-local-admin detection (the security hinge) — loopback + no headers =>
    admin; loopback + a forwarded/identity header => NOT admin; non-loopback =>
    NOT admin.

  * webui.server auth ENFORCEMENT rule — SECURE-BY-DEFAULT, always enforced on
    /api/* (NO "empty store -> auth off" escape; that escape would expose a
    0.0.0.0 bind to the whole LAN/tailnet). A non-admin request always needs a
    valid minted token (cookie / X-MediaVault-Token / ?token=) or gets 401 — even
    with an EMPTY store, where no valid token can exist, so a remote peer is LOCKED
    until the owner mints + shares one. The genuine-local admin is ALWAYS allowed
    token-free (frictionless local/dev), empty store or not.

  * /api/whoami (no auth) reports is_admin / authed correctly.

  * Token-management endpoints (POST/GET/DELETE /api/token) require the
    genuine-local admin -> 403 from a non-admin even WITH a valid token.

Test harness notes:
  * The token store is redirected via monkeypatch of ``mvcommon.MVTOKENS_PATH``
    to a tmp file — NEVER the real repo-root mvtokens.json.
  * A request's "client host" is set with ``TestClient(app, client=(host, port))``
    so a request can be made to look like the genuine-local admin (127.0.0.1, no
    forwarded headers) or a remote peer (non-loopback / forwarded header present).
  * No real C:\\Media files or real library_*.json / mvconfig.json are touched.
"""

import json

import pytest

# Skip the whole module if fastapi (or httpx, its TestClient dep) is absent —
# mirrors tests/test_web_endpoints.py.
pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient  # noqa: E402 (after importorskip guard)

import mvcommon  # noqa: E402


# ---------------------------------------------------------------------------
# Fixtures + helpers
# ---------------------------------------------------------------------------

@pytest.fixture()
def token_store(tmp_path, monkeypatch):
    """Redirect mvcommon's token store to a temp file (never the real one).

    Yields the Path to the (initially absent) store. mvcommon._load_tokens /
    _save_tokens read mvcommon.MVTOKENS_PATH at call time, so patching the module
    attribute fully redirects the store. Hard-guards that the path is under
    tmp_path and never the real repo-root mvtokens.json."""
    store = tmp_path / "mvtokens.json"
    assert "C:\\Media" not in str(store)
    # Belt-and-suspenders: must NOT be the real store next to mvcommon.py.
    assert str(store) != mvcommon.MVTOKENS_PATH
    monkeypatch.setattr(mvcommon, "MVTOKENS_PATH", str(store))
    yield store


def _admin_client(app):
    """TestClient whose requests look like the genuine-local admin: a loopback
    client host and (by default) no forwarding headers."""
    return TestClient(app, client=("127.0.0.1", 54321))


def _remote_client(app):
    """TestClient whose requests look like a remote (non-loopback) peer."""
    return TestClient(app, client=("100.64.0.9", 5))


def _fake_request(host, headers=None):
    """Build a minimal Starlette Request with a given client host + headers, for
    unit-testing _is_genuine_local_admin without spinning up the HTTP stack.

    headers is a dict of {name: value}; encoded as ASGI raw header pairs."""
    from starlette.requests import Request

    raw_headers = []
    for k, v in (headers or {}).items():
        raw_headers.append((k.lower().encode("latin-1"), v.encode("latin-1")))
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/api/whoami",
        "headers": raw_headers,
        "query_string": b"",
        "client": (host, 12345) if host is not None else None,
    }
    return Request(scope)


# ===========================================================================
# (1) mvcommon token store — unit tests
# ===========================================================================

def test_mint_then_validate_true(token_store):
    """mint() returns (id, raw, expires) and the raw validates True."""
    token_id, raw, expires_at = mvcommon.mint_token("iPhone", 3600)
    assert isinstance(token_id, str) and len(token_id) == 8
    assert isinstance(raw, str) and len(raw) >= 20
    assert expires_at is not None  # a 1h ttl -> a concrete expiry
    assert mvcommon.validate_token(raw) is True


def test_validate_wrong_token_false(token_store):
    """A token that was never minted does not validate."""
    mvcommon.mint_token("iPhone", 3600)
    assert mvcommon.validate_token("not-a-real-token") is False
    assert mvcommon.validate_token("") is False
    assert mvcommon.validate_token(None) is False


def test_expired_token_does_not_validate(token_store):
    """A token minted with a negative ttl (already past) validates False."""
    _, raw, expires_at = mvcommon.mint_token("stale", -10)
    assert expires_at is not None
    assert mvcommon.validate_token(raw) is False


def test_never_expiring_token_validates(token_store):
    """ttl_seconds=None -> expires_at null -> validates True indefinitely."""
    _, raw, expires_at = mvcommon.mint_token("forever", None)
    assert expires_at is None
    assert mvcommon.validate_token(raw) is True


def test_revoke_makes_validate_false(token_store):
    """After revoke(id) the token no longer validates; revoke is idempotent."""
    token_id, raw, _ = mvcommon.mint_token("temp", 3600)
    assert mvcommon.validate_token(raw) is True
    assert mvcommon.revoke_token(token_id) is True
    assert mvcommon.validate_token(raw) is False
    # Idempotent: revoking again returns False (nothing removed) and does not raise.
    assert mvcommon.revoke_token(token_id) is False


def test_list_tokens_hides_hash_and_raw_and_computes_expired(token_store):
    """list() exposes id/label/created/expires/expired ONLY — never hash/raw —
    and flags an expired token."""
    mvcommon.mint_token("live", 3600)
    mvcommon.mint_token("dead", -10)

    listed = mvcommon.list_tokens()
    assert len(listed) == 2

    allowed_keys = {"id", "label", "created_at", "expires_at", "expired"}
    for item in listed:
        assert set(item.keys()) == allowed_keys, (
            f"list_tokens must expose exactly {allowed_keys}, got {set(item.keys())}"
        )
        assert "hash" not in item and "token" not in item

    by_label = {t["label"]: t for t in listed}
    assert by_label["live"]["expired"] is False
    assert by_label["dead"]["expired"] is True


def test_store_is_atomic_and_only_stores_hash(token_store):
    """The on-disk store holds the sha256 hash, NEVER the raw token."""
    _, raw, _ = mvcommon.mint_token("iPhone", 3600)
    data = json.loads(token_store.read_text(encoding="utf-8"))
    assert "tokens" in data and len(data["tokens"]) == 1
    rec = data["tokens"][0]
    assert "hash" in rec and rec["hash"] != raw  # the raw is never persisted
    assert raw not in json.dumps(data), "raw token must never be written to disk"


def test_malformed_store_is_ignored(token_store):
    """A corrupt mvtokens.json -> treated as empty (no crash, owner not locked)."""
    token_store.write_text("{ this is not json", encoding="utf-8")
    assert mvcommon.list_tokens() == []
    assert mvcommon.validate_token("anything") is False


# ===========================================================================
# (2) Genuine-local-admin detection — the security hinge
# ===========================================================================

def test_genuine_local_admin_loopback_no_headers():
    """Loopback host + NO forwarding/identity headers => admin."""
    from webui.server import _is_genuine_local_admin

    for host in ("127.0.0.1", "::1", "localhost"):
        assert _is_genuine_local_admin(_fake_request(host)) is True, host


def test_genuine_local_admin_rejected_with_forwarded_header():
    """Loopback host BUT a proxy/identity header present => NOT admin (this is the
    tailscale-serve case: remote peer proxied to 127.0.0.1 but with headers)."""
    from webui.server import _is_genuine_local_admin

    for hdr in (
        "X-Forwarded-For",
        "X-Forwarded-Host",
        "X-Forwarded-Proto",
        "Forwarded",
        "Tailscale-User-Login",
        "Tailscale-User-Name",
    ):
        req = _fake_request("127.0.0.1", {hdr: "something"})
        assert _is_genuine_local_admin(req) is False, hdr


def test_genuine_local_admin_rejected_non_loopback():
    """A non-loopback client host => NOT admin, regardless of headers."""
    from webui.server import _is_genuine_local_admin

    assert _is_genuine_local_admin(_fake_request("100.64.0.9")) is False
    assert _is_genuine_local_admin(_fake_request("192.168.1.50")) is False
    assert _is_genuine_local_admin(_fake_request(None)) is False  # no client at all


# ===========================================================================
# (3) Auth enforcement rule — SECURE-BY-DEFAULT: ALWAYS enforced. A non-admin
#     remote request needs a valid token even with an EMPTY store (-> 401); the
#     genuine-local admin is always allowed token-free.
# ===========================================================================

def test_empty_store_locks_remote(token_store, sandbox_entry):
    """With NO tokens, /api/items from a NON-admin remote client is 401 — the
    secure-by-default rule. No token has been minted, so no valid token can exist
    and the remote is locked; an empty store must never expose the destructive
    console to the LAN/tailnet."""
    from webui.server import create_app

    client = _remote_client(create_app())
    r = client.get("/api/items")
    assert r.status_code == 401, (
        f"empty-store remote read must be 401 (locked), got {r.status_code}: {r.text}"
    )
    assert r.json() == {"detail": "Access token required or expired"}, (
        f"401 body must be the fixed contract, got {r.text!r}"
    )


def test_empty_store_admin_still_allowed(token_store, sandbox_entry):
    """With NO tokens, the GENUINE-LOCAL admin (loopback, no headers) still reaches
    /api/items token-free -> 200. This is the frictionless local/dev half of the
    secure-by-default rule: the owner's own browser is always allowed; only remote
    peers are locked when no token exists."""
    from webui.server import create_app

    client = _admin_client(create_app())
    r = client.get("/api/items")  # no token, empty store
    assert r.status_code == 200, (
        f"empty-store admin read must be 200 (frictionless local), got "
        f"{r.status_code}: {r.text}"
    )


def test_nonempty_store_blocks_remote_without_token(token_store, sandbox_entry):
    """Once a token is minted, a non-admin remote request with NO token -> 401
    with the exact contract body."""
    from webui.server import create_app

    mvcommon.mint_token("phone", 3600)
    client = _remote_client(create_app())

    r = client.get("/api/items")
    assert r.status_code == 401, f"expected 401, got {r.status_code}: {r.text}"
    assert r.json() == {"detail": "Access token required or expired"}, (
        f"401 body must be the fixed contract, got {r.text!r}"
    )


def test_nonempty_store_accepts_valid_token_each_carrier(token_store, sandbox_entry):
    """A valid minted token is accepted via header, cookie, AND query; a wrong or
    expired token is 401."""
    from webui.server import create_app

    _, raw, _ = mvcommon.mint_token("phone", 3600)
    _, expired_raw, _ = mvcommon.mint_token("stale", -10)
    client = _remote_client(create_app())

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
    assert r.status_code == 401, f"wrong token must 401, got {r.status_code}"

    # Expired token -> 401.
    r = client.get("/api/items", headers={"X-MediaVault-Token": expired_raw})
    assert r.status_code == 401, f"expired token must 401, got {r.status_code}"


def test_admin_always_allowed_even_with_tokens(token_store, sandbox_entry):
    """The genuine-local admin (loopback, no headers) reaches /api/items with NO
    token, even when the store is non-empty."""
    from webui.server import create_app

    mvcommon.mint_token("phone", 3600)
    client = _admin_client(create_app())

    r = client.get("/api/items")  # no token at all
    assert r.status_code == 200, f"admin must be allowed token-free, got {r.status_code}: {r.text}"


def test_proxied_loopback_is_not_admin(token_store, sandbox_entry):
    """A loopback client that ALSO sends a forwarded header (the tailscale-serve
    shape) is NOT treated as admin: with a non-empty store and no token -> 401."""
    from webui.server import create_app

    mvcommon.mint_token("phone", 3600)
    client = _admin_client(create_app())  # client host is 127.0.0.1...

    # ...but a forwarded header marks it as proxied -> not admin -> needs a token.
    r = client.get("/api/items", headers={"X-Forwarded-For": "8.8.8.8"})
    assert r.status_code == 401, (
        f"a proxied loopback request must not be admin; got {r.status_code}: {r.text}"
    )


# ===========================================================================
# (4) /api/whoami — no auth, reports is_admin / authed.
# ===========================================================================

def test_whoami_admin(token_store):
    """From the genuine-local admin: is_admin and authed are both True (even with
    tokens present, the admin is authed without one)."""
    from webui.server import create_app

    mvcommon.mint_token("phone", 3600)
    r = _admin_client(create_app()).get("/api/whoami")
    assert r.status_code == 200, r.text
    assert r.json() == {"is_admin": True, "authed": True}


def test_whoami_remote_no_token(token_store):
    """From a remote peer with no token (store non-empty): is_admin False,
    authed False. /api/whoami itself is reachable without auth."""
    from webui.server import create_app

    mvcommon.mint_token("phone", 3600)
    r = _remote_client(create_app()).get("/api/whoami")
    assert r.status_code == 200, r.text
    assert r.json() == {"is_admin": False, "authed": False}


def test_whoami_remote_with_valid_token(token_store):
    """From a remote peer WITH a valid token: is_admin False but authed True."""
    from webui.server import create_app

    _, raw, _ = mvcommon.mint_token("phone", 3600)
    r = _remote_client(create_app()).get(
        "/api/whoami", headers={"X-MediaVault-Token": raw}
    )
    assert r.status_code == 200, r.text
    assert r.json() == {"is_admin": False, "authed": True}


# ===========================================================================
# (5) Token-management endpoints — ADMIN-only (403 from a non-admin).
# ===========================================================================

def test_token_endpoints_require_admin(token_store):
    """POST/GET/DELETE /api/token from a non-admin remote peer -> 403 with the
    exact contract body, even if it presents a valid token (token-management is
    admin-only, never token-gated)."""
    from webui.server import create_app

    _, raw, _ = mvcommon.mint_token("seed", 3600)  # a valid token to prove it does NOT help
    client = _remote_client(create_app())
    auth = {"X-MediaVault-Token": raw}
    expected = {"detail": "Token management is only allowed from the local (Alienware) browser."}

    r = client.post("/api/token", json={"label": "x", "ttl_seconds": 3600}, headers=auth)
    assert r.status_code == 403, f"POST /api/token non-admin -> {r.status_code}: {r.text}"
    assert r.json() == expected

    r = client.get("/api/token", headers=auth)
    assert r.status_code == 403, f"GET /api/token non-admin -> {r.status_code}: {r.text}"
    assert r.json() == expected

    r = client.delete("/api/token/abcd1234", headers=auth)
    assert r.status_code == 403, f"DELETE /api/token non-admin -> {r.status_code}: {r.text}"
    assert r.json() == expected


def test_token_create_mints_and_returns_raw_once(token_store):
    """POST /api/token (admin) mints a token, returns the raw ONCE, and the raw
    then validates. The response carries id/label/token/expires_at, never a hash."""
    from webui.server import create_app

    client = _admin_client(create_app())
    r = client.post("/api/token", json={"label": "iPhone", "ttl_seconds": 3600})
    assert r.status_code == 200, f"admin mint -> {r.status_code}: {r.text}"
    body = r.json()
    assert set(body.keys()) == {"id", "label", "token", "expires_at"}
    assert body["label"] == "iPhone"
    assert body["expires_at"] is not None
    assert "hash" not in body
    # The returned raw must actually validate.
    assert mvcommon.validate_token(body["token"]) is True


def test_token_create_never_ttl(token_store):
    """ttl_seconds=null mints a never-expiring token (expires_at None)."""
    from webui.server import create_app

    client = _admin_client(create_app())
    r = client.post("/api/token", json={"label": "forever", "ttl_seconds": None})
    assert r.status_code == 200, r.text
    assert r.json()["expires_at"] is None


def test_token_list_endpoint_hides_hash(token_store):
    """GET /api/token (admin) returns {tokens:[{id,label,created_at,expires_at,
    expired}]} with NO hash/raw."""
    from webui.server import create_app

    mvcommon.mint_token("phone", 3600)
    client = _admin_client(create_app())
    r = client.get("/api/token")
    assert r.status_code == 200, r.text
    body = r.json()
    assert "tokens" in body and len(body["tokens"]) == 1
    item = body["tokens"][0]
    assert set(item.keys()) == {"id", "label", "created_at", "expires_at", "expired"}
    assert "hash" not in item and "token" not in item


def test_token_revoke_endpoint_is_idempotent(token_store):
    """DELETE /api/token/{id} (admin) removes the token (and is idempotent ->
    {"ok": true} even for an unknown id)."""
    from webui.server import create_app

    token_id, raw, _ = mvcommon.mint_token("phone", 3600)
    client = _admin_client(create_app())

    r = client.delete(f"/api/token/{token_id}")
    assert r.status_code == 200, r.text
    assert r.json() == {"ok": True}
    assert mvcommon.validate_token(raw) is False  # gone

    # Idempotent: deleting again still 200 / ok.
    r2 = client.delete(f"/api/token/{token_id}")
    assert r2.status_code == 200, r2.text
    assert r2.json() == {"ok": True}


def test_token_mint_list_revoke_roundtrip_via_api(token_store):
    """End-to-end admin flow: mint via API -> appears in list -> revoke -> gone."""
    from webui.server import create_app

    client = _admin_client(create_app())

    mint = client.post("/api/token", json={"label": "roundtrip", "ttl_seconds": 3600})
    token_id = mint.json()["id"]

    listed = client.get("/api/token").json()["tokens"]
    assert any(t["id"] == token_id for t in listed)

    client.delete(f"/api/token/{token_id}")
    listed_after = client.get("/api/token").json()["tokens"]
    assert all(t["id"] != token_id for t in listed_after)
