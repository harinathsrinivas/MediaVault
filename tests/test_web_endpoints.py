"""Endpoint tests for webui.server (IMP-E12).

Uses FastAPI TestClient — no live uvicorn, no browser. The entire module is
skipped cleanly when fastapi is not installed so the suite stays green on
machines without it.

All tests operate exclusively on sandbox fixtures. No real C:\\Media files or
real library_*.json are ever touched.
"""

import json
import time as _time

import pytest

# Skip the whole module if fastapi (or httpx, its TestClient dep) is absent.
pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient  # noqa: E402 (after importorskip guard)

import main  # noqa: E402
import mvcommon  # noqa: E402

# Secure-by-default auth (IMP-E15) is now ALWAYS enforced on /api/*. These tests
# drive the endpoints as the LOCAL OWNER (TestClient's host is "testclient", a
# NON-loopback => non-admin, which would otherwise 401), so run the whole module
# as the genuine-local admin. See the web_as_local_admin fixture docstring.
pytestmark = pytest.mark.usefixtures("web_as_local_admin")

# Bytes we write as the "original" file for the replace test.  Anything over
# DUMMY_MAX_BYTES (200_000) so collect_reclaimable counts it as real.
_REAL_FILE_BYTES = b"REAL-MEDIA-CONTENT\n" * 11000  # ~209 KB

# FAKE_DUMMY_BYTES matches what the `fake_dummy` fixture writes.
from conftest import FAKE_DUMMY_BYTES  # noqa: E402


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _poll(client, job_id, timeout=20):
    """Poll GET /api/job/{job_id} until status is done or error, or timeout."""
    deadline = _time.monotonic() + timeout
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


def _seed_onboarded(sandbox, make_video):
    """Seed lib_movies with one onboarded+uploaded entry backed by a real file.

    Returns (entry_id, file_path, file_hash).
    """
    entry_id = "mov-en-2020-testreplacefilm"
    media_dir = sandbox["media_dir"]
    filename = "test_replace_film.mkv"
    file_path = media_dir / filename

    _, file_hash = make_video(file_path)

    entry = {
        entry_id: {
            "status": "onboarded",
            "uploaded": True,
            "folder_path": str(media_dir),
            "filename": filename,
            "type": "movie",
        }
    }
    sandbox["lib_movies"].write_text(json.dumps(entry), encoding="utf-8")
    sandbox["lib_series"].write_text("{}", encoding="utf-8")
    sandbox["lib_anime"].write_text("{}", encoding="utf-8")

    return entry_id, file_path, file_hash


# ---------------------------------------------------------------------------
# (a) GET /api/reclaim — 200 with items + total_reclaimable_bytes
# ---------------------------------------------------------------------------

def test_reclaim_returns_expected_shape(sandbox, make_video, monkeypatch):
    """GET /api/reclaim must return 200 with the three collect_reclaimable keys."""
    from webui.server import create_app

    # Seed a real (>DUMMY_MAX_BYTES) onboarded file in the sandbox.
    entry_id, file_path, _ = _seed_onboarded(sandbox, make_video)

    client = TestClient(create_app())
    r = client.get("/api/reclaim")

    assert r.status_code == 200, f"Expected 200, got {r.status_code}: {r.text}"
    data = r.json()

    assert "items" in data, "Response missing 'items' key"
    assert "total_reclaimable_bytes" in data, "Response missing 'total_reclaimable_bytes'"
    assert "total_reclaimable_human" in data, "Response missing 'total_reclaimable_human'"

    # There should be at least one PUSHED_NOT_ARCHIVED item for our seeded entry.
    ids = [it["id"] for it in data["items"]]
    assert entry_id in ids, (
        f"Expected {entry_id!r} in reclaim items; got ids={ids}"
    )
    assert data["total_reclaimable_bytes"] > 0


# ---------------------------------------------------------------------------
# (b) POST /api/action/replace WITHOUT confirm → 409, file unchanged
# ---------------------------------------------------------------------------

def test_replace_without_confirm_returns_409(sandbox, make_video, fake_dummy):
    """POST /api/action/replace without confirm=True must return 409 and NOT
    touch the on-disk file."""
    from webui.server import create_app

    entry_id, file_path, _ = _seed_onboarded(sandbox, make_video)
    original_content = file_path.read_bytes()

    client = TestClient(create_app())

    # Body without confirm (or confirm=False) must be rejected.
    r = client.post("/api/action/replace", json={"id": entry_id})
    assert r.status_code == 409, f"Expected 409, got {r.status_code}: {r.text}"

    # No job created; file must be entirely unchanged.
    assert file_path.exists(), "File was unexpectedly deleted"
    assert file_path.read_bytes() == original_content, (
        "File content changed despite rejected (no-confirm) replace request"
    )

    # Confirm=False also rejected.
    r2 = client.post("/api/action/replace", json={"id": entry_id, "confirm": False})
    assert r2.status_code == 409


# ---------------------------------------------------------------------------
# (c) POST /api/action/replace WITH confirm:true — full replace round-trip
# ---------------------------------------------------------------------------

def test_replace_with_confirm_runs_and_archives(sandbox, make_video, fake_dummy):
    """POST /api/action/replace with confirm=true on an onboarded+uploaded leaf:
    - returns 202 with job_id
    - job reaches 'done'
    - on-disk file is replaced with dummy (FAKE_DUMMY_BYTES)
    - library entry status becomes 'archived'
    """
    from webui.server import create_app

    entry_id, file_path, _ = _seed_onboarded(sandbox, make_video)

    client = TestClient(create_app())

    r = client.post("/api/action/replace", json={"id": entry_id, "confirm": True})
    assert r.status_code == 202, f"Expected 202, got {r.status_code}: {r.text}"

    body = r.json()
    assert "job_id" in body, f"202 body missing job_id: {body}"
    job_id = body["job_id"]

    job = _poll(client, job_id)
    assert job["status"] == "done", (
        f"Replace job ended with status={job['status']!r}. "
        f"Output:\n{job.get('output', '')}"
    )

    # The on-disk file must now contain the fake dummy bytes.
    assert file_path.exists(), "File is missing after replace"
    assert file_path.read_bytes() == FAKE_DUMMY_BYTES, (
        f"File was not replaced with dummy. Size={file_path.stat().st_size}"
    )

    # The library entry must show status == "archived".
    library = json.loads(sandbox["lib_movies"].read_text(encoding="utf-8"))
    assert library[entry_id]["status"] == "archived", (
        f"Library entry status is {library[entry_id]['status']!r}, expected 'archived'"
    )


# ---------------------------------------------------------------------------
# (d) POST /api/action/{bogus} → 404
# ---------------------------------------------------------------------------

def test_bogus_action_returns_404():
    """POST /api/action/<unknown> must return 404."""
    from webui.server import create_app

    client = TestClient(create_app())
    r = client.post("/api/action/does_not_exist", json={})
    assert r.status_code == 404, f"Expected 404, got {r.status_code}: {r.text}"


# ---------------------------------------------------------------------------
# (e) POST /api/action/sort → 202, poll to terminal state
# ---------------------------------------------------------------------------

def test_sort_enqueues_and_finishes(sandbox, make_video):
    """POST /api/action/sort must return 202 with a job_id and the job must
    poll to status='done'.

    cmd_sort() returns None on its success path (it falls off the end). The
    worker's per-action success convention lists "sort" in _NONE_IS_SUCCESS, so
    a None return from a successful sort maps to status='done' (not 'error').
    This test asserts that fixed behavior end-to-end.
    """
    from webui.server import create_app

    # Seed at least one real entry so sort has something to operate on.
    entry_id, _, _ = _seed_onboarded(sandbox, make_video)

    client = TestClient(create_app())

    r = client.post("/api/action/sort", json={})
    assert r.status_code == 202, f"Expected 202, got {r.status_code}: {r.text}"

    body = r.json()
    assert "job_id" in body, f"202 body missing job_id: {body}"
    job_id = body["job_id"]

    job = _poll(client, job_id)
    # cmd_sort returns None on success; the worker maps None->done for "sort".
    assert job["status"] == "done", (
        f"sort job ended with status={job['status']!r}. "
        f"Output:\n{job.get('output', '')}"
    )


# ---------------------------------------------------------------------------
# (f) Static files carry Cache-Control: no-cache (the stale-ES-module fix), the
#     etag/304 revalidation still works, /api/* is unaffected, and /favicon.ico
#     no longer 404s.
# ---------------------------------------------------------------------------

def test_static_no_cache_and_favicon():
    """Every static response must set ``Cache-Control: no-cache`` so a browser
    revalidates each load (preventing the stale-module blank-page bug), while the
    strong etag still yields a cheap 304 for unchanged files. /api/* responses set
    ``Cache-Control: no-store`` so live state (mode/library/job) is never cached
    (the stale /api/mode demo-banner fix), and /favicon.ico returns an icon (200)
    or 204 instead of 404.
    """
    from webui.server import create_app

    client = TestClient(create_app())

    # Each served static asset (html / js / css) gets the revalidate-always header.
    for path in ("/", "/index.html", "/app.js", "/styles.css"):
        r = client.get(path)
        assert r.status_code == 200, f"{path} -> {r.status_code}"
        cc = (r.headers.get("cache-control") or "").lower()
        assert "no-cache" in cc, f"{path} Cache-Control={cc!r}, expected no-cache"

    # Etag is still emitted, and a conditional request 304s AND still carries
    # no-cache (so unchanged files stay cheap but are always revalidated).
    r = client.get("/app.js")
    etag = r.headers.get("etag")
    assert etag, "static FileResponse must still emit an etag for 304 revalidation"
    r304 = client.get("/app.js", headers={"If-None-Match": etag})
    assert r304.status_code == 304, f"conditional GET -> {r304.status_code}, expected 304"
    assert "no-cache" in (r304.headers.get("cache-control") or "").lower()

    # /api/* must be UNAFFECTED — the header belongs only to the static mount.
    api = client.get("/api/mode")
    assert api.status_code == 200
    assert (api.headers.get("cache-control") or "").lower() == "no-store", (
        "API responses must send Cache-Control: no-store so live state (mode / "
        "library / job status) is never cached — this is what fixed the stale "
        "/api/mode demo-banner-on-a-real-server bug."
    )

    # /favicon.ico must no longer 404 (real icon -> 200, else 204).
    fav = client.get("/favicon.ico")
    assert fav.status_code in (200, 204), f"/favicon.ico -> {fav.status_code}"
