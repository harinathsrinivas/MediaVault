"""Permanent tests for the media-image artwork route + resolver (Phase 5.2,
IMP-E3/U3/D17 — approach A, resolve-on-request).

Covers the new READ-ONLY resolver and its HTTP route:

  * main.resolve_artwork_path(library, mid, kind) — path-only artwork resolver
    with season-inheritance (own folder -> season_map folder -> nearest
    {tmdb-…} show folder), LOCAL ALWAYS WINS at each level (locked decision #8,
    the "Dark" requirement). Alias/season_map-safe; security-contained.
  * GET /api/media-image/{id}?kind=poster|fanart — streams the resolved jpg
    (200, image/jpeg) or 404 so the SPA falls back to its gradient placeholder.

Security guarantees pinned here:
  - a crafted id (``..`` / an absolute path) cannot read a file outside the
    library — it is just a missing dict key -> 404, never a file leak;
  - ONLY poster.jpg / fanart.jpg are ever served (a sibling secret.jpg in the
    same folder is never returned).

All tests run exclusively on the `sandbox` / `sandbox_alias` fixtures, which
dual-patch mvcommon.LOCAL_ROOT + main.LOCAL_ROOT + the LIBRARY_* constants to a
tmp tree and hard-guard against real C:\\Media. No real C:\\Media file or real
library_*.json is ever touched. Artwork is tiny seeded jpg bytes.
"""
import json
import os

import pytest

import main
import mvcommon

# TestClient (httpx) + fastapi are required for the endpoint tests; guard so the
# pure-resolver tests still collect without them.
pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient  # noqa: E402 (after importorskip guard)

from webui.server import create_app  # noqa: E402 (after importorskip guard)

# Drive the API as the genuine-local admin so the always-on /api/* auth guard
# (IMP-E15) never interferes — these tests exercise the resolver+route, not auth.
pytestmark = pytest.mark.usefixtures("web_as_local_admin")


# Tiny but valid-enough JPEG byte sequence (SOI ... EOI). Distinct content per
# call so we can assert WHICH file was served (own vs show poster).
def _jpeg(tag=b""):
    return b"\xff\xd8\xff\xe0\x00\x10JFIF" + tag + b"\x00" * 16 + b"\xff\xd9"


def _write_libs(sandbox, movies=None, series=None, anime=None):
    sandbox["lib_movies"].write_text(json.dumps(movies or {}), encoding="utf-8")
    sandbox["lib_series"].write_text(json.dumps(series or {}), encoding="utf-8")
    sandbox["lib_anime"].write_text(json.dumps(anime or {}), encoding="utf-8")


# ---------------------------------------------------------------------------
# Seeding: a TV show chain whose SHOW folder carries a {tmdb-…} token, mirroring
# what rename_folder stamps. Layout:
#
#   Series/Dark {tmdb-70523}/Season 01/Dark.S01E01.mkv   (leaf, parent_id=season)
#
#   - leaf folder_path      = .../Dark {tmdb-70523}/Season 01   (the season folder)
#   - season_map folder_path= .../Dark {tmdb-70523}/Season 01   (same, per cmd_prep)
#   - show folder           = .../Dark {tmdb-70523}             (the {tmdb-…} folder)
# ---------------------------------------------------------------------------

def _seed_show(sandbox, make_video):
    root = sandbox["local_root"]
    show_dir = root / "Series" / "Dark {tmdb-70523}"
    season_dir = show_dir / "Season 01"
    season_dir.mkdir(parents=True, exist_ok=True)
    ep_path, _ = make_video(season_dir / "Dark.S01E01.mkv", marker=b"D")

    season_id = "tv-de-2017-dark-s01"
    ep_id = "tv-de-2017-dark-s01e01"

    series = {
        season_id: {
            "type": "season_map",
            "folder_path": str(season_dir),
            "total_episodes": 1,
            "children": [ep_id],
        },
        ep_id: {
            "status": "local_ready",
            "uploaded": False,
            "folder_path": str(season_dir),
            "filename": "Dark.S01E01.mkv",
            "parent_id": season_id,
        },
    }
    _write_libs(sandbox, series=series)
    return {
        "root": root,
        "show_dir": show_dir,
        "season_dir": season_dir,
        "season_id": season_id,
        "ep_id": ep_id,
        "ep_path": str(ep_path),
    }


def _seed_flat_movie(sandbox, make_video):
    """A standalone movie in a flat folder (no season, no {tmdb-…} ancestor)."""
    root = sandbox["local_root"]
    mov_dir = root / "Movies" / "Standalone"
    mov_dir.mkdir(parents=True, exist_ok=True)
    mov_path, _ = make_video(mov_dir / "Standalone.2020.mkv", marker=b"S")
    mov_id = "mov-en-2020-standalone"
    movies = {
        mov_id: {
            "status": "local_ready",
            "uploaded": False,
            "folder_path": str(mov_dir),
            "filename": "Standalone.2020.mkv",
            "type": "movie",
        },
    }
    _write_libs(sandbox, movies=movies)
    return {"dir": mov_dir, "id": mov_id, "path": str(mov_path)}


# ===========================================================================
# (a) entry WITH a local poster.jpg -> 200 + image bytes
# ===========================================================================

def test_media_image_serves_own_poster(sandbox, make_video):
    seeded = _seed_flat_movie(sandbox, make_video)
    payload = _jpeg(b"OWN")
    (seeded["dir"] / "poster.jpg").write_bytes(payload)

    client = TestClient(create_app())
    r = client.get(f"/api/media-image/{seeded['id']}")
    assert r.status_code == 200, f"-> {r.status_code}: {r.text}"
    assert r.headers["content-type"].startswith("image/")
    assert r.content == payload


def test_media_image_fanart_kind(sandbox, make_video):
    """kind=fanart serves fanart.jpg (not poster.jpg)."""
    seeded = _seed_flat_movie(sandbox, make_video)
    (seeded["dir"] / "poster.jpg").write_bytes(_jpeg(b"POS"))
    fan = _jpeg(b"FAN")
    (seeded["dir"] / "fanart.jpg").write_bytes(fan)

    client = TestClient(create_app())
    r = client.get(f"/api/media-image/{seeded['id']}", params={"kind": "fanart"})
    assert r.status_code == 200, f"-> {r.status_code}: {r.text}"
    assert r.content == fan


def test_media_image_unknown_kind_falls_back_to_poster(sandbox, make_video):
    """A bogus ``kind`` is treated as poster (never a 400 file-leak vector)."""
    seeded = _seed_flat_movie(sandbox, make_video)
    pos = _jpeg(b"POS")
    (seeded["dir"] / "poster.jpg").write_bytes(pos)

    client = TestClient(create_app())
    r = client.get(f"/api/media-image/{seeded['id']}", params={"kind": "../etc"})
    assert r.status_code == 200, f"-> {r.status_code}: {r.text}"
    assert r.content == pos


# ===========================================================================
# (b) entry WITHOUT any poster -> 404 (SPA shows the gradient)
# ===========================================================================

def test_media_image_404_when_absent(sandbox, make_video):
    seeded = _seed_flat_movie(sandbox, make_video)  # no poster anywhere
    client = TestClient(create_app())
    r = client.get(f"/api/media-image/{seeded['id']}")
    assert r.status_code == 404, f"-> {r.status_code}: {r.text}"


def test_media_image_404_for_unknown_id(sandbox, make_video):
    """An id not in the library -> 404, no crash."""
    _seed_flat_movie(sandbox, make_video)
    client = TestClient(create_app())
    r = client.get("/api/media-image/mov-en-1900-doesnotexist")
    assert r.status_code == 404, f"-> {r.status_code}: {r.text}"


# ===========================================================================
# (c) season inheritance — episode + season folders have NO poster, the ancestor
#     {tmdb-…} show folder HAS poster.jpg -> resolves to the SHOW poster (200)
# ===========================================================================

def test_season_inherits_show_poster_via_endpoint(sandbox, make_video):
    seeded = _seed_show(sandbox, make_video)
    show_poster = _jpeg(b"SHOW")
    (seeded["show_dir"] / "poster.jpg").write_bytes(show_poster)
    # Deliberately NO poster in the season or episode folder.

    client = TestClient(create_app())
    r = client.get(f"/api/media-image/{seeded['ep_id']}")
    assert r.status_code == 200, f"-> {r.status_code}: {r.text}"
    assert r.content == show_poster


def test_season_inherits_show_poster_resolver(sandbox, make_video):
    """Same, asserting the resolver returns the SHOW folder's poster path."""
    seeded = _seed_show(sandbox, make_video)
    (seeded["show_dir"] / "poster.jpg").write_bytes(_jpeg(b"SHOW"))

    library = mvcommon.load_library()
    got = main.resolve_artwork_path(library, seeded["ep_id"], kind="poster")
    assert got is not None
    assert os.path.realpath(got) == os.path.realpath(
        str(seeded["show_dir"] / "poster.jpg")
    ), f"expected show poster, got {got}"


def test_season_map_folder_poster_wins_over_show(sandbox, make_video):
    """Level (ii): a poster in the SEASON folder beats the show folder poster
    (LOCAL-wins climbs the inheritance order one rung at a time)."""
    seeded = _seed_show(sandbox, make_video)
    (seeded["show_dir"] / "poster.jpg").write_bytes(_jpeg(b"SHOW"))
    season_poster = _jpeg(b"SEASON")
    (seeded["season_dir"] / "poster.jpg").write_bytes(season_poster)

    client = TestClient(create_app())
    r = client.get(f"/api/media-image/{seeded['ep_id']}")
    assert r.status_code == 200, f"-> {r.status_code}: {r.text}"
    assert r.content == season_poster


# ===========================================================================
# (d) local-wins — the episode's OWN folder poster wins over the show poster
#
# In this seed the leaf folder_path IS the season folder, so its own poster and
# the season_map poster are the same file. To exercise a DISTINCT own-folder that
# is deeper than the season_map's folder, seed an episode whose own folder is a
# sub-folder of the season folder.
# ===========================================================================

def test_own_folder_poster_wins_over_show(sandbox, make_video):
    seeded = _seed_show(sandbox, make_video)
    # Show poster present...
    (seeded["show_dir"] / "poster.jpg").write_bytes(_jpeg(b"SHOW"))
    # ...and an episode-own poster present in the SAME (season=own) folder.
    own_poster = _jpeg(b"OWN")
    (seeded["season_dir"] / "poster.jpg").write_bytes(own_poster)

    library = mvcommon.load_library()
    got = main.resolve_artwork_path(library, seeded["ep_id"], kind="poster")
    assert os.path.realpath(got) == os.path.realpath(
        str(seeded["season_dir"] / "poster.jpg")
    ), f"own/season poster must win over the show poster, got {got}"

    client = TestClient(create_app())
    r = client.get(f"/api/media-image/{seeded['ep_id']}")
    assert r.status_code == 200
    assert r.content == own_poster


def test_distinct_own_subfolder_poster_wins(sandbox, make_video):
    """An episode whose OWN folder is a sub-folder UNDER the season folder: its
    own poster (level i) wins over the season_map folder poster (level ii)."""
    root = sandbox["local_root"]
    show_dir = root / "Series" / "Dark {tmdb-70523}"
    season_dir = show_dir / "Season 01"
    ep_dir = season_dir / "E01"  # the episode lives in its OWN deeper folder
    ep_dir.mkdir(parents=True, exist_ok=True)
    make_video(ep_dir / "Dark.S01E01.mkv", marker=b"D")

    season_id = "tv-de-2017-dark-s01"
    ep_id = "tv-de-2017-dark-s01e01"
    series = {
        season_id: {
            "type": "season_map",
            "folder_path": str(season_dir),
            "total_episodes": 1,
            "children": [ep_id],
        },
        ep_id: {
            "status": "local_ready",
            "uploaded": False,
            "folder_path": str(ep_dir),
            "filename": "Dark.S01E01.mkv",
            "parent_id": season_id,
        },
    }
    _write_libs(sandbox, series=series)

    (season_dir / "poster.jpg").write_bytes(_jpeg(b"SEASON"))
    own = _jpeg(b"OWN")
    (ep_dir / "poster.jpg").write_bytes(own)

    library = mvcommon.load_library()
    got = main.resolve_artwork_path(library, ep_id, kind="poster")
    assert os.path.realpath(got) == os.path.realpath(str(ep_dir / "poster.jpg")), (
        f"own-folder poster must win over season folder, got {got}"
    )


# ===========================================================================
# (e) no path traversal — a crafted id cannot read a file outside the library
# ===========================================================================

@pytest.mark.parametrize(
    "evil_id",
    [
        "..",
        "../../../../etc/passwd",
        "..%2f..%2fsecret",
        "mov-en-2020-standalone/../../../secret",
    ],
)
def test_no_path_traversal_via_id(sandbox, make_video, tmp_path, evil_id):
    """A crafted id (``..`` / absolute / encoded) returns 404 and NEVER leaks a
    file outside LOCAL_ROOT. We even plant a poster.jpg OUTSIDE the root to prove
    it can't be reached."""
    _seed_flat_movie(sandbox, make_video)
    outside = tmp_path / "outside"
    outside.mkdir()
    secret = _jpeg(b"SECRET")
    (outside / "poster.jpg").write_bytes(secret)

    client = TestClient(create_app())
    r = client.get(f"/api/media-image/{evil_id}")
    assert r.status_code == 404, f"traversal id {evil_id!r} -> {r.status_code}: {r.text[:120]}"
    # Hard proof: the out-of-root secret was never streamed.
    assert r.content != secret


def test_absolute_path_id_does_not_leak(sandbox, make_video, tmp_path):
    """An id that IS an absolute path to a real poster.jpg cannot be read — it is
    only ever a dict-key lookup, so it 404s and never streams the file."""
    _seed_flat_movie(sandbox, make_video)
    outside = tmp_path / "outside2"
    outside.mkdir()
    secret = _jpeg(b"ABS-SECRET")
    secret_file = outside / "poster.jpg"
    secret_file.write_bytes(secret)

    library = mvcommon.load_library()
    # The resolver: an absolute-path "id" is just a missing key -> None.
    assert main.resolve_artwork_path(library, str(secret_file), kind="poster") is None
    assert main.resolve_artwork_path(library, str(outside), kind="poster") is None


# ===========================================================================
# (f) only poster.jpg / fanart.jpg are ever served — a sibling secret.jpg in the
#     same folder is never returned
# ===========================================================================

def test_only_named_images_served(sandbox, make_video):
    seeded = _seed_flat_movie(sandbox, make_video)
    # A non-allowed image name sitting right next to the (absent) poster.
    (seeded["dir"] / "secret.jpg").write_bytes(_jpeg(b"SECRET"))

    # Resolver returns None (no poster.jpg/fanart.jpg present).
    library = mvcommon.load_library()
    assert main.resolve_artwork_path(library, seeded["id"], kind="poster") is None

    # And the route 404s rather than serving secret.jpg.
    client = TestClient(create_app())
    r = client.get(f"/api/media-image/{seeded['id']}")
    assert r.status_code == 404, f"-> {r.status_code}: {r.text}"


def test_resolver_never_returns_non_allowed_basename(sandbox, make_video):
    """Even with poster.jpg present, the returned path's basename is EXACTLY
    poster.jpg / fanart.jpg (the security allow-list)."""
    seeded = _seed_flat_movie(sandbox, make_video)
    (seeded["dir"] / "poster.jpg").write_bytes(_jpeg(b"P"))
    (seeded["dir"] / "fanart.jpg").write_bytes(_jpeg(b"F"))

    library = mvcommon.load_library()
    p = main.resolve_artwork_path(library, seeded["id"], kind="poster")
    f = main.resolve_artwork_path(library, seeded["id"], kind="fanart")
    assert os.path.basename(p).lower() == "poster.jpg"
    assert os.path.basename(f).lower() == "fanart.jpg"


# ===========================================================================
# Alias / season_map safety (PR #21 crash class) — resolver must not raise on a
# virtual library and must resolve an alias to its primary leaf's artwork.
# ===========================================================================

def test_resolver_alias_safe(sandbox_alias, make_video):
    """Over a season_map + multi_ep_alias library, resolve_artwork_path must NOT
    raise for any id, and the alias must resolve to the PRIMARY leaf's folder
    artwork (the alias owns no folder of its own)."""
    primary_id = sandbox_alias["primary_id"]
    alias_id = sandbox_alias["alias_id"]
    season_id = sandbox_alias["season_id"]
    media_dir = sandbox_alias["media_dir"]  # the season folder = primary's folder

    # A poster in the (shared) season/primary folder.
    poster = _jpeg(b"BSG")
    (media_dir / "poster.jpg").write_bytes(poster)

    library = mvcommon.load_library()
    # None of these raise; all three resolve to the same season-folder poster.
    want = os.path.realpath(str(media_dir / "poster.jpg"))
    for mid in (primary_id, alias_id, season_id):
        got = main.resolve_artwork_path(library, mid, kind="poster")
        assert got is not None, f"{mid} resolved to None"
        assert os.path.realpath(got) == want, f"{mid} -> {got}, want {want}"


def test_resolver_alias_missing_primary_does_not_raise(sandbox_alias, make_video):
    """A multi_ep_alias whose PRIMARY is absent must not raise — it has no folder,
    so it falls through to its season (here no poster) -> None."""
    alias_id = sandbox_alias["alias_id"]
    primary_id = sandbox_alias["primary_id"]

    library = mvcommon.load_library()
    del library[primary_id]  # break the alias target
    # No season poster seeded -> None, but crucially NO KeyError / AttributeError.
    assert main.resolve_artwork_path(library, alias_id, kind="poster") is None


def test_endpoint_alias_resolves_to_primary(sandbox_alias, make_video):
    """End-to-end: GET /api/media-image/<alias_id> streams the primary's folder
    poster (alias dereferenced to primary)."""
    alias_id = sandbox_alias["alias_id"]
    media_dir = sandbox_alias["media_dir"]
    poster = _jpeg(b"BSG-ALIAS")
    (media_dir / "poster.jpg").write_bytes(poster)

    client = TestClient(create_app())
    r = client.get(f"/api/media-image/{alias_id}")
    assert r.status_code == 200, f"-> {r.status_code}: {r.text}"
    assert r.content == poster


# ===========================================================================
# Read-only guarantee — resolving artwork never mutates a library file.
# ===========================================================================

def test_resolver_is_read_only(sandbox, make_video):
    seeded = _seed_show(sandbox, make_video)
    (seeded["show_dir"] / "poster.jpg").write_bytes(_jpeg(b"SHOW"))

    lib_paths = [sandbox["lib_movies"], sandbox["lib_series"], sandbox["lib_anime"]]
    before = {p: p.read_bytes() for p in lib_paths}
    before_lib = mvcommon.load_library()

    library = mvcommon.load_library()
    main.resolve_artwork_path(library, seeded["ep_id"], kind="poster")
    main.resolve_artwork_path(library, seeded["season_id"], kind="fanart")

    for p in lib_paths:
        assert p.read_bytes() == before[p], f"{p.name} changed — not read-only"
    assert mvcommon.load_library() == before_lib
