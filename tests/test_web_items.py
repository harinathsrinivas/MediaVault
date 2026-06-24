"""Permanent tests for items_payload() / GET /api/items (IMP-E14 Phase 1).

items_payload() is the LIBRARY-anchored inventory that backs the SPA's
media-type tabs. It differs from collect_reclaimable() (the disk-anchored
reclaim scan) in one load-bearing way: it INCLUDES archived rows. Both share the
single-source-of-truth _classify_item() core, so this module also pins that the
reclaim contract is byte-for-byte unchanged by that refactor (step 1.1).

What these tests guarantee:
  (a) per-state classification + category bucketing across mov-/tv-/ani-
  (b) ARCHIVED is INCLUDED in items_payload (the divergence from /api/reclaim)
  (c) alias / season_map safety — no virtual rows, no crash (PR #21 class)
  (d) /api/reclaim unchanged (back-compat) + /api/items shape via TestClient
  (e) items_payload() is strictly read-only (library file + load_library() stable)
  (f) every emitted row has exactly the contract keys + is JSON-serializable

All tests operate exclusively on the `sandbox` / `sandbox_alias` fixtures, which
dual-patch mvcommon.LIBRARY_* AND main.LIBRARY_* plus LOCAL_ROOT to a tmp tree
and hard-guard against real C:\\Media. No real C:\\Media file or real
library_*.json is ever touched.
"""
import json
import os

import pytest

import main
import mvcommon


# The exact public key set every items_payload() row must carry (parent_id is
# OPTIONAL — present only when the entry has one). Mirrors items_payload's
# docstring + row builder at main.py:3658-3671.
_ITEM_BASE_KEYS = {
    "id", "category", "state", "size_bytes", "path",
    "title", "year", "tmdb_id", "poster_available", "chunk_count",
}


# ---------------------------------------------------------------------------
# Shared seeding helpers
# ---------------------------------------------------------------------------

def _write_libs(sandbox, movies=None, series=None, anime=None):
    """Write all three library JSON files into the sandbox (None -> empty {})."""
    sandbox["lib_movies"].write_text(json.dumps(movies or {}), encoding="utf-8")
    sandbox["lib_series"].write_text(json.dumps(series or {}), encoding="utf-8")
    sandbox["lib_anime"].write_text(json.dumps(anime or {}), encoding="utf-8")


def _seed_lifecycle(sandbox, make_video):
    """Seed one sandbox library spanning the lifecycle states across all three
    category prefixes. Every media file lives UNDER the patched LOCAL_ROOT so the
    disk-anchored collect_reclaimable() walk and the library-anchored
    items_payload() agree on the same physical leaves.

    Layout (state in parens is the classify_entry_state result):
      mov-en-2022-localready   local_ready    REAL  -> LOCAL_NOT_PUSHED   (movies)
      mov-en-2020-archived     archived       DUMMY -> ARCHIVED           (movies)
      tv-en-2021-onboardshow…  onboarded      REAL  -> PUSHED_NOT_ARCHIVED(series)
      ani-en-2006-restored…    restored_local REAL  -> RESTORED_REPLACE…  (anime)

    Returns a dict mapping a logical name -> {"id", "path", "state", "category"}.
    """
    root = sandbox["local_root"]

    # --- movies: local_ready REAL -> LOCAL_NOT_PUSHED ---
    mov_local_dir = root / "Movies" / "LocalReadyFilm"
    mov_local_dir.mkdir(parents=True, exist_ok=True)
    mov_local_path, _ = make_video(mov_local_dir / "LocalReady.2022.mkv", marker=b"A")
    mov_local_id = "mov-en-2022-localready"

    # --- movies: archived DUMMY (< DUMMY_MAX_BYTES) -> ARCHIVED ---
    mov_arch_dir = root / "Movies" / "ArchivedFilm"
    mov_arch_dir.mkdir(parents=True, exist_ok=True)
    mov_arch_path = mov_arch_dir / "Archived.2020.mkv"
    mov_arch_path.write_bytes(b"tiny-dummy" * 100)  # 1000 bytes — a dummy
    assert os.path.getsize(mov_arch_path) < main.DUMMY_MAX_BYTES
    mov_arch_id = "mov-en-2020-archived"

    # --- series: onboarded REAL -> PUSHED_NOT_ARCHIVED ---
    tv_dir = root / "Series" / "OnboardShow" / "Season 01"
    tv_dir.mkdir(parents=True, exist_ok=True)
    tv_path, _ = make_video(tv_dir / "OnboardShow.S01E01.2021.mkv", marker=b"B")
    tv_id = "tv-en-2021-onboardshow-s01e01"

    # --- anime: restored_local REAL -> RESTORED_REPLACE_AGAIN ---
    ani_dir = root / "Anime" / "RestoredAnime"
    ani_dir.mkdir(parents=True, exist_ok=True)
    ani_path, _ = make_video(ani_dir / "RestoredAnime.2006.07.mkv", marker=b"C")
    ani_id = "ani-en-2006-restoredanime07"

    movies = {
        mov_local_id: {
            "status": "local_ready",
            "uploaded": False,
            "folder_path": str(mov_local_dir),
            "filename": "LocalReady.2022.mkv",
            "type": "movie",
        },
        mov_arch_id: {
            "status": "archived",
            "uploaded": True,
            "folder_path": str(mov_arch_dir),
            "filename": "Archived.2020.mkv",
            "type": "movie",
        },
    }
    series = {
        tv_id: {
            "status": "onboarded",
            "uploaded": True,
            "folder_path": str(tv_dir),
            "filename": "OnboardShow.S01E01.2021.mkv",
        },
    }
    anime = {
        ani_id: {
            "status": "restored_local",
            "uploaded": True,
            "folder_path": str(ani_dir),
            "filename": "RestoredAnime.2006.07.mkv",
        },
    }
    _write_libs(sandbox, movies=movies, series=series, anime=anime)

    return {
        "mov_local": {"id": mov_local_id, "path": str(mov_local_path),
                      "state": "LOCAL_NOT_PUSHED", "category": "movies"},
        "mov_archived": {"id": mov_arch_id, "path": str(mov_arch_path),
                         "state": "ARCHIVED", "category": "movies"},
        "tv_onboarded": {"id": tv_id, "path": str(tv_path),
                         "state": "PUSHED_NOT_ARCHIVED", "category": "series"},
        "ani_restored": {"id": ani_id, "path": str(ani_path),
                         "state": "RESTORED_REPLACE_AGAIN", "category": "anime"},
    }


# ---------------------------------------------------------------------------
# (a) per-state classification + category bucketing
# ---------------------------------------------------------------------------

def test_per_state_classification_and_category(sandbox, make_video):
    """Each physical leaf gets the right (category, state); by_category counts
    every leaf (including the archived one)."""
    seeded = _seed_lifecycle(sandbox, make_video)

    payload = main.items_payload()
    rows = {it["id"]: it for it in payload["items"]}

    # Every seeded id appears exactly once with the expected category + state.
    for spec in seeded.values():
        assert spec["id"] in rows, (
            f"Expected {spec['id']!r} in items; got ids={list(rows)}"
        )
        row = rows[spec["id"]]
        assert row["category"] == spec["category"], (
            f"{spec['id']}: category {row['category']!r} != {spec['category']!r}"
        )
        assert row["state"] == spec["state"], (
            f"{spec['id']}: state {row['state']!r} != {spec['state']!r}"
        )

    # by_category counts ALL physical leaves per bucket (archived included).
    assert payload["by_category"] == {
        "movies": 2,  # local_ready + archived
        "series": 1,  # onboarded
        "anime": 1,   # restored_local
        "other": 0,
    }, f"Unexpected by_category: {payload['by_category']}"

    # And the counts agree with the emitted item rows.
    counts = {"movies": 0, "series": 0, "anime": 0, "other": 0}
    for it in payload["items"]:
        counts[it["category"]] += 1
    assert counts == payload["by_category"]


# ---------------------------------------------------------------------------
# (b) ARCHIVED is INCLUDED (the key divergence from /api/reclaim)
# ---------------------------------------------------------------------------

def test_archived_is_included_unlike_reclaim(sandbox, make_video):
    """An archived+dummy entry appears in items_payload() with state ARCHIVED,
    while collect_reclaimable() excludes it. This is THE divergence between the
    library-anchored inventory and the disk-anchored reclaim scan."""
    seeded = _seed_lifecycle(sandbox, make_video)
    archived_id = seeded["mov_archived"]["id"]

    payload = main.items_payload()
    item_ids = {it["id"]: it for it in payload["items"]}

    # INCLUDED in items_payload, with state ARCHIVED.
    assert archived_id in item_ids, (
        f"ARCHIVED entry {archived_id!r} must be INCLUDED in items_payload; "
        f"got ids={list(item_ids)}"
    )
    assert item_ids[archived_id]["state"] == "ARCHIVED"

    # EXCLUDED from collect_reclaimable (the divergence).
    reclaim = main.collect_reclaimable()
    reclaim_ids = {it["id"] for it in reclaim["items"]}
    assert archived_id not in reclaim_ids, (
        f"ARCHIVED entry {archived_id!r} must be EXCLUDED from collect_reclaimable; "
        f"got ids={reclaim_ids}"
    )


# ---------------------------------------------------------------------------
# (c) alias / season_map safety — no virtual rows, no crash (PR #21 class)
# ---------------------------------------------------------------------------

def test_alias_and_season_map_emit_no_virtual_rows(sandbox_alias):
    """Over a library containing a season_map parent + a multi_ep_alias,
    items_payload() must NOT raise and must emit NO virtual rows: only the
    physical primary leaf appears; neither the season_map nor the alias id does.
    This is the PR #21 crash-class guard (virtual rows skipped BEFORE
    folder_path/filename are dereferenced)."""
    season_id = sandbox_alias["season_id"]
    primary_id = sandbox_alias["primary_id"]
    alias_id = sandbox_alias["alias_id"]

    payload = main.items_payload()  # must not raise on the alias/season_map rows
    ids = {it["id"] for it in payload["items"]}

    # Virtual rows are NEVER emitted.
    assert season_id not in ids, "season_map must NOT appear in items"
    assert alias_id not in ids, "multi_ep_alias must NOT appear in items"

    # The physical primary leaf (local_ready + real file) IS emitted, in series.
    assert primary_id in ids, (
        f"primary physical leaf {primary_id!r} should appear; got ids={ids}"
    )
    primary_row = next(it for it in payload["items"] if it["id"] == primary_id)
    assert primary_row["category"] == "series"
    assert primary_row["state"] == "LOCAL_NOT_PUSHED"
    # parent_id is surfaced because the leaf carries one.
    assert primary_row.get("parent_id") == season_id

    # Exactly one row total (the single physical leaf) and the count agrees.
    assert len(payload["items"]) == 1, (
        f"Expected exactly the one physical leaf, got {len(payload['items'])} rows"
    )
    assert payload["by_category"] == {"movies": 0, "series": 1, "anime": 0, "other": 0}


# ---------------------------------------------------------------------------
# (d) /api/reclaim unchanged (back-compat guard) + /api/items shape — TestClient
# ---------------------------------------------------------------------------

# TestClient (and its httpx dep) are only needed for the endpoint tests below;
# guard them so the module's pure-function tests still run without fastapi.
pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient  # noqa: E402 (after importorskip guard)


@pytest.mark.usefixtures("web_as_local_admin")
def test_api_items_and_reclaim_endpoints(sandbox, make_video):
    """GET /api/items returns 200 with exactly {by_category, items}; GET
    /api/reclaim still returns 200 with its existing three keys AND content that
    EQUALS calling collect_reclaimable() directly on the same library — proving
    the shared _classify_item refactor (step 1.1) did not change reclaim output."""
    from webui.server import create_app

    _seed_lifecycle(sandbox, make_video)

    client = TestClient(create_app())

    # --- /api/items: 200 + exact top-level key set ---
    r_items = client.get("/api/items")
    assert r_items.status_code == 200, f"/api/items -> {r_items.status_code}: {r_items.text}"
    items_data = r_items.json()
    assert set(items_data.keys()) == {"by_category", "items"}, (
        f"/api/items top-level keys {set(items_data.keys())} != {{'by_category','items'}}"
    )
    # It mirrors the direct call (endpoint returns the payload verbatim).
    assert items_data == main.items_payload()

    # --- /api/reclaim: 200 + exact three keys, UNCHANGED content ---
    r_reclaim = client.get("/api/reclaim")
    assert r_reclaim.status_code == 200, (
        f"/api/reclaim -> {r_reclaim.status_code}: {r_reclaim.text}"
    )
    reclaim_data = r_reclaim.json()
    assert set(reclaim_data.keys()) == {
        "items", "total_reclaimable_bytes", "total_reclaimable_human",
    }, f"/api/reclaim keys {set(reclaim_data.keys())} changed"

    # Back-compat guard: the endpoint's body must equal a direct
    # collect_reclaimable() call on the same seeded library (JSON round-trip
    # normalises tuples->lists, so normalise the direct call the same way).
    direct = json.loads(json.dumps(main.collect_reclaimable(), default=str))
    assert reclaim_data == direct, (
        "GET /api/reclaim content diverged from collect_reclaimable() — the "
        "shared _classify_item refactor must not change reclaim output"
    )


# ---------------------------------------------------------------------------
# (e) items_payload() is strictly read-only
# ---------------------------------------------------------------------------

def test_items_payload_is_read_only(sandbox, make_video):
    """Calling items_payload() must not mutate any library file or the loaded
    library structure (it only os.stat()s files)."""
    _seed_lifecycle(sandbox, make_video)

    # Snapshot the on-disk library bytes + mtimes BEFORE.
    lib_paths = [sandbox["lib_movies"], sandbox["lib_series"], sandbox["lib_anime"]]
    before_bytes = {p: p.read_bytes() for p in lib_paths}
    before_mtime = {p: p.stat().st_mtime_ns for p in lib_paths}
    before_lib = mvcommon.load_library()

    main.items_payload()
    main.items_payload()  # call twice — still no writes

    # Files are byte-for-byte identical and untouched (mtime unchanged).
    for p in lib_paths:
        assert p.read_bytes() == before_bytes[p], f"{p.name} bytes changed — not read-only"
        assert p.stat().st_mtime_ns == before_mtime[p], f"{p.name} mtime changed — a write occurred"

    # And the parsed library is unchanged.
    assert mvcommon.load_library() == before_lib


# ---------------------------------------------------------------------------
# (f) shape contract — exact keys, no internal leakage, JSON-serializable
# ---------------------------------------------------------------------------

def test_item_shape_contract_and_json_serializable(sandbox, make_video):
    """Every emitted row carries exactly the contract keys (no `entry`/internal
    leakage); parent_id is the only optional key; the whole payload is
    JSON-serializable."""
    _seed_lifecycle(sandbox, make_video)

    payload = main.items_payload()
    assert payload["items"], "Need at least one item for the shape check"

    for item in payload["items"]:
        keys = set(item.keys())
        extra = keys - (_ITEM_BASE_KEYS | {"parent_id"})
        assert not extra, f"Unexpected/leaked keys in item {item.get('id')!r}: {extra}"
        missing = _ITEM_BASE_KEYS - keys
        assert not missing, f"Missing required keys in item {item.get('id')!r}: {missing}"
        # No internal passthrough must leak out (the _classify_item 'entry').
        assert "entry" not in item, "internal 'entry' leaked into an item row"

    # by_category is exactly the four canonical buckets.
    assert set(payload["by_category"].keys()) == {"movies", "series", "anime", "other"}

    # The entire payload must serialise cleanly (default=str catches any stray
    # non-JSON value without masking a structural problem).
    dumped = json.dumps(payload, default=str)
    assert json.loads(dumped) == json.loads(json.dumps(payload, default=str))


def test_alias_library_payload_json_serializable(sandbox_alias):
    """The alias/season_map library also yields a JSON-serializable payload
    (defence-in-depth for the crash class — serialisation must not choke)."""
    payload = main.items_payload()
    json.dumps(payload, default=str)  # must not raise


# ---------------------------------------------------------------------------
# (g) poster_available / tmdb_id / title truthful reporting (IMP-E3/U3/D17 5.7)
#
# items_payload() reports, per row: poster_available (a real on-disk check via
# resolve_artwork_path — the SAME resolver /api/media-image uses), tmdb_id (from
# metadata.tmdb_id), and the real metadata.title when set. The SPA gates the
# poster <img> on poster_available and shows metadata.title, so these three fields
# must be truthful. All over the LOCAL_ROOT-hermetic sandbox (never real C:\Media).
# ---------------------------------------------------------------------------

def _seed_two_movies_one_with_poster(sandbox, make_video):
    """Seed two movie leaves under LOCAL_ROOT:
      * mov-en-2010-withposter : has poster.jpg in its folder + metadata
        {title:"Inception", year:2010, tmdb_id:27205}
      * mov-en-2011-noposter   : NO poster.jpg, NO metadata (placeholder title)
    Returns the two ids."""
    root = sandbox["local_root"]

    with_dir = root / "Movies" / "Inception {tmdb-27205}"
    with_dir.mkdir(parents=True, exist_ok=True)
    make_video(with_dir / "Inception.2010.mkv", marker=b"P")
    # A real poster.jpg directly in the folder (resolve_artwork_path level i wins).
    (with_dir / "poster.jpg").write_bytes(b"\xff\xd8\xff\xe0FAKE-JPEG\xff\xd9")
    with_id = "mov-en-2010-withposter"

    no_dir = root / "Movies" / "NoPoster"
    no_dir.mkdir(parents=True, exist_ok=True)
    make_video(no_dir / "NoPoster.2011.mkv", marker=b"Q")
    no_id = "mov-en-2011-noposter"

    movies = {
        with_id: {
            "status": "local_ready",
            "uploaded": False,
            "folder_path": str(with_dir),
            "filename": "Inception.2010.mkv",
            "type": "movie",
            "metadata": {"title": "Inception", "year": 2010, "tmdb_id": 27205},
        },
        no_id: {
            "status": "local_ready",
            "uploaded": False,
            "folder_path": str(no_dir),
            "filename": "NoPoster.2011.mkv",
            "type": "movie",
            # No metadata at all -> placeholder title (the id) + null tmdb_id.
        },
    }
    _write_libs(sandbox, movies=movies)
    return with_id, no_id


def test_poster_available_tmdb_id_and_title_reported(sandbox, make_video):
    """items_payload() reports poster_available truthfully (True only when a
    poster.jpg exists on disk), tmdb_id == metadata.tmdb_id (else None), and the
    real metadata.title when set (else the id placeholder)."""
    with_id, no_id = _seed_two_movies_one_with_poster(sandbox, make_video)

    rows = {it["id"]: it for it in main.items_payload()["items"]}
    assert with_id in rows and no_id in rows, f"got ids={list(rows)}"

    with_row = rows[with_id]
    no_row = rows[no_id]

    # poster_available reflects a REAL on-disk poster.jpg.
    assert with_row["poster_available"] is True, (
        "the entry with a poster.jpg on disk must report poster_available=True"
    )
    assert no_row["poster_available"] is False, (
        "the entry with no poster.jpg must report poster_available=False"
    )

    # tmdb_id reflects metadata.tmdb_id (an int) / None when absent.
    assert with_row["tmdb_id"] == 27205
    assert no_row["tmdb_id"] is None

    # The real metadata.title is carried; the bare entry falls back to its id so
    # title.js humanizes it (it never equals a real, non-id title).
    assert with_row["title"] == "Inception"
    assert with_row["year"] == 2010
    assert no_row["title"] == no_id  # placeholder -> title.js humanizes the id

    # poster_available is a real bool (JSON-friendly), never a truthy path/object.
    for r in (with_row, no_row):
        assert isinstance(r["poster_available"], bool)


def test_poster_available_false_when_poster_removed(sandbox, make_video):
    """poster_available is a LIVE disk check, not a cached flag: deleting the
    poster.jpg flips a previously-True row to False on the next call."""
    with_id, _ = _seed_two_movies_one_with_poster(sandbox, make_video)

    first = {it["id"]: it for it in main.items_payload()["items"]}
    assert first[with_id]["poster_available"] is True

    # Remove the poster on disk; the next payload must report False (no caching).
    poster = sandbox["local_root"] / "Movies" / "Inception {tmdb-27205}" / "poster.jpg"
    poster.unlink()

    second = {it["id"]: it for it in main.items_payload()["items"]}
    assert second[with_id]["poster_available"] is False


@pytest.mark.usefixtures("web_as_local_admin")
def test_api_items_reports_poster_available_and_tmdb_id(sandbox, make_video):
    """The HTTP surface carries the truthful fields end-to-end: GET /api/items
    mirrors items_payload(), so a seeded poster/tmdb_id is visible to the SPA."""
    from webui.server import create_app

    with_id, no_id = _seed_two_movies_one_with_poster(sandbox, make_video)

    client = TestClient(create_app())
    r = client.get("/api/items")
    assert r.status_code == 200, f"/api/items -> {r.status_code}: {r.text}"
    rows = {it["id"]: it for it in r.json()["items"]}

    assert rows[with_id]["poster_available"] is True
    assert rows[with_id]["tmdb_id"] == 27205
    assert rows[with_id]["title"] == "Inception"
    assert rows[no_id]["poster_available"] is False
    assert rows[no_id]["tmdb_id"] is None
