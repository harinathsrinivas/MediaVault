"""Permanent tests for the folder-tree web routes (IMP-E14 polish).

Covers the three new BACKEND endpoints and their main.py helpers:

  * GET  /api/tree           — folder hierarchy mirroring on-disk structure,
                               with REAL recursive folder sizes + has_image,
                               leaves = items_payload() rows + {"type":"leaf"}.
  * GET  /api/folder-image   — streams poster.jpg/fanart.jpg from a folder or
                               its first image-bearing descendant; security:
                               under LOCAL_ROOT + only those two filenames.
  * POST /api/open-folder    — localhost-only Explorer opener; demo-simulated;
                               path must be an existing dir under LOCAL_ROOT.

All tests run exclusively on the `sandbox` / `sandbox_alias` fixtures, which
dual-patch mvcommon.LOCAL_ROOT + main.LOCAL_ROOT + the LIBRARY_* constants to a
tmp tree and hard-guard against real C:\\Media. No real C:\\Media file or real
library_*.json is ever touched.
"""
import json
import os

import pytest

import main
import mvcommon

# TestClient (httpx) + fastapi are required for the endpoint tests; guard so the
# pure-helper tests still collect without them.
pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient  # noqa: E402 (after importorskip guard)

from webui.server import create_app  # noqa: E402 (after importorskip guard)


# ---------------------------------------------------------------------------
# Shared seeding helpers (mirror tests/test_web_items.py's _seed_lifecycle, but
# arranged so the on-disk folder hierarchy is interesting: a movie collection
# folder with a movie subfolder, and a show -> season -> episode TV chain).
# ---------------------------------------------------------------------------

def _write_libs(sandbox, movies=None, series=None, anime=None):
    sandbox["lib_movies"].write_text(json.dumps(movies or {}), encoding="utf-8")
    sandbox["lib_series"].write_text(json.dumps(series or {}), encoding="utf-8")
    sandbox["lib_anime"].write_text(json.dumps(anime or {}), encoding="utf-8")


def _seed_tree(sandbox, make_video):
    """Seed a hierarchy:

      Movies/
        James Bond/Dr No/DrNo.1962.mkv        (mov, nested collection -> movie)
        Standalone/Standalone.2020.mkv        (mov, flat folder)
      Series/
        Fringe/Season 01/Fringe.S01E01.mkv    (tv, show -> season -> episode)

    Returns a dict of seeded facts for assertions.
    """
    root = sandbox["local_root"]

    # --- movie inside a collection folder (James Bond -> Dr No) ---
    bond_dir = root / "Movies" / "James Bond" / "Dr No"
    bond_dir.mkdir(parents=True, exist_ok=True)
    bond_path, _ = make_video(bond_dir / "DrNo.1962.mkv", marker=b"B")
    bond_id = "mov-en-1962-drno"

    # --- standalone movie in a flat folder ---
    standalone_dir = root / "Movies" / "Standalone"
    standalone_dir.mkdir(parents=True, exist_ok=True)
    standalone_path, _ = make_video(standalone_dir / "Standalone.2020.mkv", marker=b"S")
    standalone_id = "mov-en-2020-standalone"

    # --- TV: show -> season -> episode ---
    tv_dir = root / "Series" / "Fringe" / "Season 01"
    tv_dir.mkdir(parents=True, exist_ok=True)
    tv_path, _ = make_video(tv_dir / "Fringe.S01E01.2008.mkv", marker=b"F")
    tv_id = "tv-en-2008-fringe-s01e01"

    movies = {
        bond_id: {
            "status": "local_ready",
            "uploaded": False,
            "folder_path": str(bond_dir),
            "filename": "DrNo.1962.mkv",
            "type": "movie",
        },
        standalone_id: {
            "status": "local_ready",
            "uploaded": False,
            "folder_path": str(standalone_dir),
            "filename": "Standalone.2020.mkv",
            "type": "movie",
        },
    }
    series = {
        tv_id: {
            "status": "onboarded",
            "uploaded": True,
            "folder_path": str(tv_dir),
            "filename": "Fringe.S01E01.2008.mkv",
        },
    }
    _write_libs(sandbox, movies=movies, series=series)

    return {
        "root": root,
        "bond": {"id": bond_id, "dir": bond_dir, "path": str(bond_path)},
        "standalone": {"id": standalone_id, "dir": standalone_dir, "path": str(standalone_path)},
        "tv": {"id": tv_id, "dir": tv_dir, "path": str(tv_path)},
    }


def _seed_unprepped(sandbox, make_video, seeded):
    """On top of an already-seeded library tree, drop UNPREPPED disk files — real
    videos NOT present in any library — so collect_reclaimable() surfaces them
    with badge=="UNPREPPED" and build_tree() must add them as leaves:

      * a Sample.mkv INSIDE the existing Dr No movie folder (so it nests beside
        the library leaf under James Bond -> Dr No);
      * an Extras clip in a NEW Series/Documentaries folder (its own subtree).

    Returns {"sample": {path}, "extra": {path, dir}}.
    """
    # An unprepped sample sitting next to the library movie leaf.
    sample_path, _ = make_video(seeded["bond"]["dir"] / "Sample.mkv", marker=b"X")

    # An unprepped clip in a brand-new Series subfolder (not in the library).
    extras_dir = sandbox["local_root"] / "Series" / "Documentaries"
    extras_dir.mkdir(parents=True, exist_ok=True)
    extra_path, _ = make_video(extras_dir / "BehindTheScenes.mkv", marker=b"E")

    return {
        "sample": {"path": str(sample_path)},
        "extra": {"path": str(extra_path), "dir": extras_dir},
    }


def _find_folder(nodes, name):
    """Return the first folder child named ``name`` in ``nodes``, else None."""
    for n in nodes:
        if n["type"] == "folder" and n["name"] == name:
            return n
    return None


def _all_leaves(nodes):
    """Collect every leaf NODE (full dict) reachable from ``nodes`` (recursive)."""
    out = []
    for n in nodes:
        if n["type"] == "leaf":
            out.append(n)
        elif n["type"] == "folder":
            out.extend(_all_leaves(n["children"]))
    return out


def _leaf_by_path(nodes, path):
    """Return the leaf whose normpath-lower matches ``path``, else None."""
    want = os.path.normpath(path).lower()
    for leaf in _all_leaves(nodes):
        if os.path.normpath(leaf["path"]).lower() == want:
            return leaf
    return None


def _all_leaf_ids(nodes):
    """Collect every leaf id reachable from ``nodes`` (recursive)."""
    out = []
    for n in nodes:
        if n["type"] == "leaf":
            out.append(n["id"])
        elif n["type"] == "folder":
            out.extend(_all_leaf_ids(n["children"]))
    return out


# ---------------------------------------------------------------------------
# GET /api/tree — shape, nesting, real folder size
# ---------------------------------------------------------------------------

_LEAF_BASE_KEYS = {
    "type", "id", "category", "state", "size_bytes", "path",
    "title", "year", "tmdb_id", "poster_available", "backdrop_available",
    "overview", "episode_title", "chunk_count",
}


@pytest.mark.usefixtures("web_as_local_admin")
def test_tree_shape_and_roots(sandbox, make_video):
    """/api/tree returns {"roots": {movies, series, anime, other}} with each
    value a list."""
    _seed_tree(sandbox, make_video)
    client = TestClient(create_app())

    r = client.get("/api/tree")
    assert r.status_code == 200, f"/api/tree -> {r.status_code}: {r.text}"
    data = r.json()
    assert set(data.keys()) == {"roots"}, f"top-level keys: {set(data.keys())}"
    roots = data["roots"]
    assert set(roots.keys()) == {"movies", "series", "anime", "other"}
    for cat, nodes in roots.items():
        assert isinstance(nodes, list), f"roots[{cat}] is not a list"


def test_tree_nests_leaves_under_real_folders(sandbox, make_video):
    """Leaves nest under their REAL on-disk folder segments relative to the
    category root: James Bond -> Dr No -> leaf; Standalone -> leaf; for TV,
    Fringe -> Season 01 -> leaf."""
    seeded = _seed_tree(sandbox, make_video)
    tree = main.build_tree()["roots"]

    # --- movies ---
    bond = _find_folder(tree["movies"], "James Bond")
    assert bond is not None, f"James Bond folder missing; movies={[n['name'] for n in tree['movies'] if n['type']=='folder']}"
    drno = _find_folder(bond["children"], "Dr No")
    assert drno is not None, "Dr No sub-folder missing under James Bond"
    drno_leaves = [c for c in drno["children"] if c["type"] == "leaf"]
    assert [l["id"] for l in drno_leaves] == [seeded["bond"]["id"]]

    standalone = _find_folder(tree["movies"], "Standalone")
    assert standalone is not None, "Standalone folder missing"
    assert [c["id"] for c in standalone["children"] if c["type"] == "leaf"] == [
        seeded["standalone"]["id"]
    ]

    # --- series: show -> season -> episode ---
    fringe = _find_folder(tree["series"], "Fringe")
    assert fringe is not None, "Fringe show folder missing"
    season = _find_folder(fringe["children"], "Season 01")
    assert season is not None, "Season 01 folder missing under Fringe"
    assert [c["id"] for c in season["children"] if c["type"] == "leaf"] == [
        seeded["tv"]["id"]
    ]

    # Every seeded leaf appears exactly once across the whole tree.
    all_ids = _all_leaf_ids(tree["movies"]) + _all_leaf_ids(tree["series"])
    assert sorted(all_ids) == sorted(
        [seeded["bond"]["id"], seeded["standalone"]["id"], seeded["tv"]["id"]]
    )


def test_tree_folder_size_is_real_and_positive(sandbox, make_video):
    """A folder node's size_bytes is the REAL recursive sum of file sizes under
    it (matches an independent os.walk), and is > 0 for a folder holding media."""
    seeded = _seed_tree(sandbox, make_video)
    tree = main.build_tree()["roots"]

    bond = _find_folder(tree["movies"], "James Bond")
    # Independent ground-truth: sum every file size under the James Bond folder.
    expected = 0
    for dirpath, _dirs, files in os.walk(seeded["bond"]["dir"].parent):
        for f in files:
            expected += os.path.getsize(os.path.join(dirpath, f))
    assert bond["size_bytes"] == expected, (
        f"James Bond folder size {bond['size_bytes']} != real {expected}"
    )
    assert bond["size_bytes"] > 0

    # The nested Dr No folder is the single movie file's size.
    drno = _find_folder(bond["children"], "Dr No")
    assert drno["size_bytes"] == os.path.getsize(seeded["bond"]["path"])


def test_tree_leaf_node_keys_and_type(sandbox, make_video):
    """Each leaf node carries type:"leaf" plus the items_payload() row keys; each
    folder node carries the folder contract keys."""
    _seed_tree(sandbox, make_video)
    tree = main.build_tree()["roots"]

    def _check(nodes):
        for n in nodes:
            if n["type"] == "leaf":
                keys = set(n.keys())
                missing = _LEAF_BASE_KEYS - keys
                assert not missing, f"leaf {n.get('id')} missing keys: {missing}"
                extra = keys - (_LEAF_BASE_KEYS | {"parent_id"})
                assert not extra, f"leaf {n.get('id')} unexpected keys: {extra}"
            else:
                assert set(n.keys()) == {
                    "type", "name", "path", "size_bytes", "has_image", "children"
                }, f"folder {n.get('name')} keys: {set(n.keys())}"
                assert isinstance(n["has_image"], bool)
                assert isinstance(n["size_bytes"], int)
                _check(n["children"])

    for cat in ("movies", "series", "anime", "other"):
        _check(tree[cat])


def test_tree_has_image_detects_descendant_poster(sandbox, make_video):
    """has_image is True for a folder (and its ancestors) when a poster.jpg lives
    in a descendant, and False when none exists in the subtree."""
    seeded = _seed_tree(sandbox, make_video)
    # Drop a poster.jpg INSIDE the deep Dr No folder.
    (seeded["bond"]["dir"] / "poster.jpg").write_bytes(b"\xff\xd8\xff\xe0jpegdata")

    tree = main.build_tree()["roots"]
    bond = _find_folder(tree["movies"], "James Bond")
    drno = _find_folder(bond["children"], "Dr No")
    assert drno["has_image"] is True, "Dr No should see its own poster.jpg"
    assert bond["has_image"] is True, "James Bond should see the descendant poster.jpg"

    # Standalone has no image anywhere in its subtree.
    standalone = _find_folder(tree["movies"], "Standalone")
    assert standalone["has_image"] is False


@pytest.mark.usefixtures("web_as_local_admin")
def test_tree_json_serializable_via_endpoint(sandbox, make_video):
    """The whole /api/tree payload is JSON-serializable (no stray non-JSON)."""
    _seed_tree(sandbox, make_video)
    client = TestClient(create_app())
    r = client.get("/api/tree")
    assert r.status_code == 200
    json.dumps(r.json())  # must not raise


# ---------------------------------------------------------------------------
# UNPREPPED disk files are added to the tree (the fifth state) — IMP-E14 polish
# ---------------------------------------------------------------------------

def test_tree_includes_unprepped_disk_file_nested(sandbox, make_video):
    """An UNPREPPED disk file (a real video NOT in the library) appears in
    /api/tree as a leaf with state=="UNPREPPED", nested under its own on-disk
    folder — the Sample.mkv shows beside the library leaf under James Bond ->
    Dr No, and the Extras clip under Series -> Documentaries."""
    seeded = _seed_tree(sandbox, make_video)
    extra = _seed_unprepped(sandbox, make_video, seeded)

    tree = main.build_tree()["roots"]

    # Sample.mkv nests beside the library leaf inside the Dr No folder.
    bond = _find_folder(tree["movies"], "James Bond")
    assert bond is not None
    drno = _find_folder(bond["children"], "Dr No")
    assert drno is not None
    drno_leaves = {os.path.normpath(c["path"]).lower(): c
                   for c in drno["children"] if c["type"] == "leaf"}
    sample_key = os.path.normpath(extra["sample"]["path"]).lower()
    bond_key = os.path.normpath(seeded["bond"]["path"]).lower()
    assert sample_key in drno_leaves, (
        f"Sample.mkv missing under Dr No; leaves={list(drno_leaves)}"
    )
    assert bond_key in drno_leaves, "library movie leaf no longer under Dr No"
    sample_leaf = drno_leaves[sample_key]
    assert sample_leaf["state"] == "UNPREPPED"
    assert sample_leaf["category"] == "movies"
    assert sample_leaf["guessed"] is True
    assert sample_leaf["poster_available"] is False
    assert sample_leaf["chunk_count"] == 1
    assert sample_leaf["size_bytes"] == os.path.getsize(extra["sample"]["path"])

    # The Extras clip lives under its own new Series subtree.
    docs = _find_folder(tree["series"], "Documentaries")
    assert docs is not None, (
        f"Documentaries folder missing; series={[n['name'] for n in tree['series'] if n['type']=='folder']}"
    )
    extra_leaf = _leaf_by_path(docs["children"], extra["extra"]["path"])
    assert extra_leaf is not None, "Extras clip missing under Documentaries"
    assert extra_leaf["state"] == "UNPREPPED"
    assert extra_leaf["category"] == "series"


def test_tree_spans_all_states_with_unprepped(sandbox, make_video):
    """With both library leaves and an unprepped file seeded, the tree carries a
    non-null `state` on EVERY leaf, library leaves keep their correct state, and
    the unprepped file's UNPREPPED state is present — so the grouped view can be
    filtered by all five states."""
    seeded = _seed_tree(sandbox, make_video)
    extra = _seed_unprepped(sandbox, make_video, seeded)

    tree = main.build_tree()["roots"]
    all_leaves = []
    for cat in ("movies", "series", "anime", "other"):
        all_leaves.extend(_all_leaves(tree[cat]))

    # Every leaf has a non-null, non-empty state string.
    for leaf in all_leaves:
        assert leaf.get("state"), f"leaf {leaf.get('id')} has no state: {leaf}"

    state_by_path = {os.path.normpath(l["path"]).lower(): l["state"] for l in all_leaves}
    # Library leaves keep their items_payload state (local_ready -> LOCAL_NOT_PUSHED,
    # onboarded -> PUSHED_NOT_ARCHIVED).
    assert state_by_path[os.path.normpath(seeded["bond"]["path"]).lower()] == "LOCAL_NOT_PUSHED"
    assert state_by_path[os.path.normpath(seeded["standalone"]["path"]).lower()] == "LOCAL_NOT_PUSHED"
    assert state_by_path[os.path.normpath(seeded["tv"]["path"]).lower()] == "PUSHED_NOT_ARCHIVED"
    # The unprepped disk file is UNPREPPED.
    assert state_by_path[os.path.normpath(extra["sample"]["path"]).lower()] == "UNPREPPED"

    # All five-state coverage requires UNPREPPED to be present alongside the
    # library states this fixture produces.
    assert "UNPREPPED" in set(state_by_path.values())


def test_tree_folder_size_counts_unprepped_file(sandbox, make_video):
    """Folder size logic is unchanged: the Dr No folder's REAL size already counts
    the unprepped Sample.mkv sitting in it (size walk is independent of which
    files are library leaves)."""
    seeded = _seed_tree(sandbox, make_video)
    extra = _seed_unprepped(sandbox, make_video, seeded)

    tree = main.build_tree()["roots"]
    bond = _find_folder(tree["movies"], "James Bond")
    drno = _find_folder(bond["children"], "Dr No")

    # Independent ground truth: every file under Dr No (library movie + sample).
    expected = 0
    for dirpath, _dirs, files in os.walk(seeded["bond"]["dir"]):
        for f in files:
            expected += os.path.getsize(os.path.join(dirpath, f))
    assert drno["size_bytes"] == expected
    # Sanity: the sample's bytes are part of that sum.
    assert drno["size_bytes"] >= os.path.getsize(extra["sample"]["path"])


def test_tree_unprepped_is_read_only(sandbox, make_video):
    """Adding unprepped leaves to the tree must not mutate any library file (the
    unprepped scan is the same read-only collect_reclaimable disk walk)."""
    seeded = _seed_tree(sandbox, make_video)
    _seed_unprepped(sandbox, make_video, seeded)

    lib_paths = [sandbox["lib_movies"], sandbox["lib_series"], sandbox["lib_anime"]]
    before_bytes = {p: p.read_bytes() for p in lib_paths}
    before_lib = mvcommon.load_library()

    main.build_tree()
    main.build_tree()

    for p in lib_paths:
        assert p.read_bytes() == before_bytes[p], f"{p.name} changed — not read-only"
    assert mvcommon.load_library() == before_lib


def test_tree_alias_safe_with_unprepped(sandbox_alias, make_video):
    """With a season_map/multi_ep_alias library AND an unprepped disk file under
    Anime, build_tree() must NOT raise, must NOT leak the virtual ids, and MUST
    still surface the unprepped file as a leaf with state=="UNPREPPED"."""
    primary_id = sandbox_alias["primary_id"]
    season_id = sandbox_alias["season_id"]
    alias_id = sandbox_alias["alias_id"]

    # An unprepped clip in a fresh Anime folder (unknown to the library).
    ani_dir = sandbox_alias["sandbox"]["local_root"] / "Anime" / "RandomClip"
    ani_dir.mkdir(parents=True, exist_ok=True)
    clip_path, _ = make_video(ani_dir / "Clip.mkv", marker=b"A")

    tree = main.build_tree()["roots"]  # must not raise
    all_leaves = []
    for cat in tree.values():
        all_leaves.extend(_all_leaves(cat))
    all_ids = [l["id"] for l in all_leaves]

    assert season_id not in all_ids, "season_map leaked into the tree"
    assert alias_id not in all_ids, "multi_ep_alias leaked into the tree"
    assert primary_id in all_ids, f"primary physical leaf missing; got {all_ids}"

    clip_leaf = _leaf_by_path(tree["anime"], str(clip_path))
    assert clip_leaf is not None, "unprepped Anime clip missing from the tree"
    assert clip_leaf["state"] == "UNPREPPED"


# ---------------------------------------------------------------------------
# Alias / season_map safety — no virtual rows in the tree (PR #21 class)
# ---------------------------------------------------------------------------

def test_tree_alias_emits_no_virtual_rows(sandbox_alias):
    """Over a library with a season_map parent + a multi_ep_alias, build_tree()
    must NOT raise and the season_map/alias ids must NEVER appear as leaves —
    only the physical primary leaf does."""
    season_id = sandbox_alias["season_id"]
    primary_id = sandbox_alias["primary_id"]
    alias_id = sandbox_alias["alias_id"]

    tree = main.build_tree()["roots"]  # must not raise
    all_ids = []
    for cat in tree.values():
        all_ids.extend(_all_leaf_ids(cat))

    assert season_id not in all_ids, "season_map leaked into the tree"
    assert alias_id not in all_ids, "multi_ep_alias leaked into the tree"
    assert primary_id in all_ids, f"primary physical leaf missing; got {all_ids}"


# ---------------------------------------------------------------------------
# build_tree() is strictly read-only
# ---------------------------------------------------------------------------

def test_build_tree_is_read_only(sandbox, make_video):
    """build_tree() must not mutate any library file (only os.stat/os.scandir)."""
    _seed_tree(sandbox, make_video)
    lib_paths = [sandbox["lib_movies"], sandbox["lib_series"], sandbox["lib_anime"]]
    before_bytes = {p: p.read_bytes() for p in lib_paths}
    before_lib = mvcommon.load_library()

    main.build_tree()
    main.build_tree()

    for p in lib_paths:
        assert p.read_bytes() == before_bytes[p], f"{p.name} changed — not read-only"
    assert mvcommon.load_library() == before_lib


# ---------------------------------------------------------------------------
# GET /api/folder-image
# ---------------------------------------------------------------------------

_JPEG_BYTES = b"\xff\xd8\xff\xe0\x00\x10JFIF" + b"\x00" * 32 + b"\xff\xd9"


@pytest.mark.usefixtures("web_as_local_admin")
def test_folder_image_serves_poster_in_folder(sandbox, make_video):
    """A poster.jpg in the requested folder is streamed (200, image/jpeg)."""
    seeded = _seed_tree(sandbox, make_video)
    poster = seeded["standalone"]["dir"] / "poster.jpg"
    poster.write_bytes(_JPEG_BYTES)

    client = TestClient(create_app())
    r = client.get("/api/folder-image", params={"path": str(seeded["standalone"]["dir"])})
    assert r.status_code == 200, f"-> {r.status_code}: {r.text}"
    assert r.headers["content-type"].startswith("image/")
    assert r.content == _JPEG_BYTES


@pytest.mark.usefixtures("web_as_local_admin")
def test_folder_image_serves_descendant_when_folder_has_none(sandbox, make_video):
    """When the requested folder has no image but a descendant does, the
    descendant's poster.jpg is served."""
    seeded = _seed_tree(sandbox, make_video)
    # poster only deep inside Dr No; request the parent James Bond folder.
    (seeded["bond"]["dir"] / "fanart.jpg").write_bytes(_JPEG_BYTES)

    client = TestClient(create_app())
    bond_parent = seeded["bond"]["dir"].parent  # .../Movies/James Bond
    r = client.get("/api/folder-image", params={"path": str(bond_parent)})
    assert r.status_code == 200, f"-> {r.status_code}: {r.text}"
    assert r.content == _JPEG_BYTES


@pytest.mark.usefixtures("web_as_local_admin")
def test_folder_image_404_when_absent(sandbox, make_video):
    """No poster/fanart anywhere in the subtree -> 404."""
    seeded = _seed_tree(sandbox, make_video)
    client = TestClient(create_app())
    r = client.get("/api/folder-image", params={"path": str(seeded["standalone"]["dir"])})
    assert r.status_code == 404, f"-> {r.status_code}: {r.text}"


@pytest.mark.usefixtures("web_as_local_admin")
def test_folder_image_rejects_path_outside_root(sandbox, make_video, tmp_path):
    """A path OUTSIDE LOCAL_ROOT (traversal) is rejected with 403."""
    _seed_tree(sandbox, make_video)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "poster.jpg").write_bytes(_JPEG_BYTES)

    client = TestClient(create_app())
    r = client.get("/api/folder-image", params={"path": str(outside)})
    assert r.status_code == 403, f"expected 403 for out-of-root, got {r.status_code}"


@pytest.mark.usefixtures("web_as_local_admin")
def test_folder_image_traversal_via_dotdot_rejected(sandbox, make_video, tmp_path):
    """A ..-based traversal that escapes LOCAL_ROOT is rejected (403), even
    though the literal prefix starts under the root."""
    seeded = _seed_tree(sandbox, make_video)
    sibling = tmp_path / "secret"
    sibling.mkdir()
    (sibling / "poster.jpg").write_bytes(_JPEG_BYTES)

    # Path that textually starts under LOCAL_ROOT but ..-escapes to the sibling.
    evil = os.path.join(str(seeded["root"]), "..", "secret")
    client = TestClient(create_app())
    r = client.get("/api/folder-image", params={"path": evil})
    assert r.status_code in (403, 404), f"traversal must be rejected, got {r.status_code}"


# ---------------------------------------------------------------------------
# POST /api/open-folder
#
# These tests intentionally do NOT use the `web_as_local_admin` fixture: they
# exercise the genuine-local-admin gate ON open-folder itself, so the REAL admin
# predicate must govern. The localhost cases below pass a loopback client host
# (127.0.0.1) so they ARE the genuine-local admin — they sail through the
# always-on /api/* auth middleware (IMP-E15) and reach the handler's own logic.
# ---------------------------------------------------------------------------

def test_open_folder_rejects_non_localhost(sandbox, make_video):
    """A non-localhost client (the default TestClient host 'testclient') with no
    credentials is rejected — never open Explorer for a remote/Tailscale peer.

    Under the secure-by-default auth rule (IMP-E15), a non-admin request with no
    valid token is caught by the always-on /api/* middleware (401) BEFORE it can
    reach the open-folder handler's own genuine-local-admin 403. Either status is
    a rejection; here the middleware's 401 fires first (no token presented). The
    LAYERED property "a remote peer WITH a valid token still gets the handler's
    403" is owned by tests/test_web_auth.py::test_open_folder_admin_rule_survives_auth.
    """
    seeded = _seed_tree(sandbox, make_video)
    client = TestClient(create_app())  # default client host 'testclient' (non-admin)
    r = client.post("/api/open-folder", json={"path": str(seeded["standalone"]["dir"])})
    assert r.status_code == 401, (
        f"a non-localhost, no-token peer must be rejected by the auth gate (401), "
        f"got {r.status_code}: {r.text}"
    )


def test_open_folder_demo_simulates(sandbox, make_video):
    """In demo mode a localhost open returns 200 {opened:false, demo:true} and
    NEVER calls os.startfile."""
    seeded = _seed_tree(sandbox, make_video)
    called = {"startfile": False}

    def _boom(_path):
        called["startfile"] = True
        raise AssertionError("os.startfile must NOT be called in demo mode")

    orig = getattr(os, "startfile", None)
    os.startfile = _boom
    try:
        client = TestClient(create_app(demo=True), client=("127.0.0.1", 50000))
        r = client.post("/api/open-folder", json={"path": str(seeded["standalone"]["dir"])})
    finally:
        if orig is not None:
            os.startfile = orig
    assert r.status_code == 200, f"-> {r.status_code}: {r.text}"
    assert r.json() == {"opened": False, "demo": True}
    assert called["startfile"] is False


def test_open_folder_rejects_out_of_root(sandbox, make_video, tmp_path):
    """Even from localhost, a path outside LOCAL_ROOT is rejected (403)."""
    _seed_tree(sandbox, make_video)
    outside = tmp_path / "outside"
    outside.mkdir()
    client = TestClient(create_app(), client=("127.0.0.1", 50000))
    r = client.post("/api/open-folder", json={"path": str(outside)})
    assert r.status_code == 403, f"out-of-root must be 403, got {r.status_code}"


def test_open_folder_rejects_nonexistent_dir(sandbox, make_video):
    """A path under LOCAL_ROOT that is not an existing directory -> 400."""
    seeded = _seed_tree(sandbox, make_video)
    missing = seeded["root"] / "Movies" / "DoesNotExist"
    client = TestClient(create_app(), client=("127.0.0.1", 50000))
    r = client.post("/api/open-folder", json={"path": str(missing)})
    assert r.status_code == 400, f"non-dir must be 400, got {r.status_code}"


def test_open_folder_real_open_localhost(sandbox, make_video, monkeypatch):
    """A real (non-demo) localhost open of a valid dir calls os.startfile and
    returns 200 {opened:true}. os.startfile is stubbed so no Explorer launches."""
    seeded = _seed_tree(sandbox, make_video)
    opened = {"path": None}

    def _fake_startfile(path):
        opened["path"] = path

    monkeypatch.setattr(os, "startfile", _fake_startfile, raising=False)
    client = TestClient(create_app(), client=("127.0.0.1", 50000))
    r = client.post("/api/open-folder", json={"path": str(seeded["standalone"]["dir"])})
    assert r.status_code == 200, f"-> {r.status_code}: {r.text}"
    assert r.json() == {"opened": True}
    assert opened["path"] is not None
    assert os.path.realpath(opened["path"]) == os.path.realpath(str(seeded["standalone"]["dir"]))
