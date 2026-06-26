"""Permanent tests for tmdb_detail() / GET /api/detail/{id} (IMP-E16).

The detail endpoint backs the SPA's hover-preview DOSSIER: rich TMDB facts
(rating, genres, runtime, tagline, cast, director/creators, IMDb link, …) for
ONE library entry. tmdb_detail(library, mid) resolves the entry's stored
metadata.tmdb_id, determines the kind from the id (movie / tv / episode),
fetches the canonical TMDB details (all via the cached, None-on-failure
_tmdb_get), and returns the contract dict — or None when there is no tmdb_id.

What these tests guarantee:
  (a) a movie entry -> kind=movie + rating/genres/runtime/cast/directors/imdb_url
  (b) an episode id  -> kind=episode + episode_title/air_date/season/episode
  (c) a tv (show) id -> kind=tv + number_of_seasons/networks/created-by directors
  (d) an entry with NO tmdb_id -> 404 {"detail":"no tmdb_id"}
  (e) a TMDB SUB-call failure (credits/external_ids None) -> PARTIAL dict, no 500
  (f) NO tmdb_id (full failure) is the ONLY None; read-only; alias-safe
  (g) the HTTP surface mirrors tmdb_detail end-to-end via TestClient

TMDB is mocked by monkeypatching main._tmdb_get (the single cached seam EVERY
detail call funnels through) + main's api-key so tmdb_detail never short-circuits
and NO real network/cache is touched. All over the LOCAL_ROOT-hermetic `sandbox`
fixture (never real C:\\Media / library_*.json).
"""
import json

import pytest

import main
import mvcommon

# TestClient (httpx) is only needed for the endpoint tests; guard so the pure
# tmdb_detail() tests still run without fastapi installed.
pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient  # noqa: E402 (after importorskip guard)


# ---------------------------------------------------------------------------
# Canned TMDB payloads keyed by URL suffix (the exact endpoints tmdb_detail hits).
# ---------------------------------------------------------------------------

_MOVIE_DETAIL = {
    "id": 27205,
    "title": "Inception",
    "tagline": "Your mind is the scene of the crime.",
    "overview": "A thief who steals corporate secrets through dream-sharing tech.",
    "vote_average": 8.4,
    "vote_count": 35000,
    "runtime": 148,
    "genres": [{"id": 28, "name": "Action"}, {"id": 878, "name": "Science Fiction"}],
    "release_date": "2010-07-15",
    "imdb_id": "tt1375666",
    "status": "Released",
    "homepage": "https://www.warnerbros.com/inception",
}

_MOVIE_CREDITS = {
    "cast": [
        {"name": "Leonardo DiCaprio", "character": "Cobb"},
        {"name": "Joseph Gordon-Levitt", "character": "Arthur"},
        {"name": "Elliot Page", "character": "Ariadne"},
    ],
    "crew": [
        {"name": "Christopher Nolan", "job": "Director"},
        {"name": "Hans Zimmer", "job": "Original Music Composer"},
        {"name": "Christopher Nolan", "job": "Writer"},  # dup name, not a Director-dup
    ],
}

_TV_DETAIL = {
    "id": 2316,
    "name": "The Office",
    "tagline": "",
    "overview": "A mockumentary on a group of typical office workers.",
    "vote_average": 8.6,
    "vote_count": 12000,
    "episode_run_time": [22],
    "genres": [{"id": 35, "name": "Comedy"}],
    "first_air_date": "2005-03-24",
    "status": "Ended",
    "homepage": "https://www.nbc.com/the-office",
    "number_of_seasons": 9,
    "number_of_episodes": 201,
    "networks": [{"id": 6, "name": "NBC"}],
    "created_by": [{"name": "Greg Daniels"}, {"name": "Ricky Gervais"}],
}

_TV_EXTERNAL_IDS = {"imdb_id": "tt0386676"}

_TV_CREDITS = {
    "cast": [
        {"name": "Steve Carell", "character": "Michael Scott"},
        {"name": "Rainn Wilson", "character": "Dwight Schrute"},
    ],
    "crew": [],
}

_EP_DETAIL = {
    "name": "Pilot",
    "overview": "The documentary crew arrives at Dunder Mifflin.",
    "vote_average": 7.6,
    "vote_count": 120,
    "runtime": 23,
    "air_date": "2005-03-24",
    "season_number": 1,
    "episode_number": 1,
}


def _make_fake_tmdb_get(*, fail_suffixes=()):
    """Return a stand-in for main._tmdb_get(url, params, api_key, _cache=True) that
    URL-dispatches to the canned payloads above — NO network, NO cache.

    Any URL whose tail matches an entry in `fail_suffixes` returns None (modelling
    a network/non-200 sub-call failure, exactly as the real _tmdb_get degrades).
    Records every requested URL on `.urls` for assertions.
    """
    calls = {"urls": []}

    def fake(url, params, api_key, _cache=True):
        calls["urls"].append(url)
        for suf in fail_suffixes:
            if url.endswith(suf):
                return None
        if url.endswith("/movie/27205"):
            return dict(_MOVIE_DETAIL)
        if url.endswith("/movie/27205/credits"):
            return dict(_MOVIE_CREDITS)
        if url.endswith("/tv/2316/season/1/episode/1"):
            return dict(_EP_DETAIL)
        if url.endswith("/tv/2316/external_ids"):
            return dict(_TV_EXTERNAL_IDS)
        if url.endswith("/tv/2316/credits"):
            return dict(_TV_CREDITS)
        if url.endswith("/tv/2316"):
            return dict(_TV_DETAIL)
        return None  # any unmodelled endpoint -> graceful miss

    fake.calls = calls
    return fake


@pytest.fixture()
def patch_tmdb(monkeypatch):
    """Install the canned _tmdb_get + a fake api key so tmdb_detail fetches.

    Returns an `install(fail_suffixes=())` callable so a test can request a
    specific sub-call to fail. Patches main._tmdb_get (the cached seam) and
    main's api-key source — NO real network/cache is reachable.
    """
    def install(fail_suffixes=()):
        fake = _make_fake_tmdb_get(fail_suffixes=fail_suffixes)
        monkeypatch.setattr(main, "_tmdb_get", fake)
        # tmdb_detail reads the key via mvcommon.tmdb_api_key(); main imported the
        # name, so patch where main looks it up. A non-empty key prevents the
        # no-key short-circuit.
        monkeypatch.setattr(main.mvcommon, "tmdb_api_key", lambda: "TEST-KEY")
        return fake
    return install


# ---------------------------------------------------------------------------
# Seeding helpers (all under the patched LOCAL_ROOT via the sandbox fixture).
# ---------------------------------------------------------------------------

def _write_libs(sandbox, movies=None, series=None, anime=None):
    sandbox["lib_movies"].write_text(json.dumps(movies or {}), encoding="utf-8")
    sandbox["lib_series"].write_text(json.dumps(series or {}), encoding="utf-8")
    sandbox["lib_anime"].write_text(json.dumps(anime or {}), encoding="utf-8")


def _seed_movie(sandbox, make_video, *, with_tmdb=True):
    """Seed one movie leaf. with_tmdb=True -> metadata.tmdb_id=27205 (Inception);
    with_tmdb=False -> a movie with NO tmdb_id. Returns the id."""
    root = sandbox["local_root"]
    mid = "mov-en-2010-inception"
    folder = root / "Movies" / ("Inception {tmdb-27205}" if with_tmdb else "Inception")
    folder.mkdir(parents=True, exist_ok=True)
    make_video(folder / "Inception.2010.mkv", marker=b"M")
    meta = {"title": "Inception", "year": 2010}
    if with_tmdb:
        meta["tmdb_id"] = 27205
    movies = {
        mid: {
            "status": "local_ready", "uploaded": False,
            "folder_path": str(folder), "filename": "Inception.2010.mkv",
            "type": "movie", "metadata": meta,
        }
    }
    _write_libs(sandbox, movies=movies)
    return mid


def _seed_episode(sandbox, make_video):
    """Seed a season_map + one episode leaf for The Office S01E01 (show tmdb 2316).
    The show tmdb_id is stamped on the EPISODE leaf (as enrich does). Returns
    (episode_id, season_id)."""
    root = sandbox["local_root"]
    show_dir = root / "Series" / "The Office {tmdb-2316}"
    season_dir = show_dir / "Season 01"
    season_dir.mkdir(parents=True, exist_ok=True)
    make_video(season_dir / "The.Office.S01E01.mkv", marker=b"E")
    season_id = "tv-en-2005-the-office-s01"
    ep_id = "tv-en-2005-the-office-s01e01"
    series = {
        season_id: {"type": "season_map", "folder_path": str(season_dir),
                    "total_episodes": 1, "children": [ep_id]},
        ep_id: {
            "status": "local_ready", "uploaded": False,
            "folder_path": str(season_dir), "filename": "The.Office.S01E01.mkv",
            "parent_id": season_id,
            "metadata": {"title": "The Office", "year": 2005, "tmdb_id": 2316,
                         "episode_title": "Pilot"},
        },
    }
    _write_libs(sandbox, series=series)
    return ep_id, season_id


def _seed_show(sandbox, make_video):
    """Seed a SHOW-level leaf (no -sNNeMM in the id) carrying tmdb_id 2316, so the
    kind resolves to 'tv'. A flat series entry (no season suffix) suffices for the
    kind logic. Returns the id."""
    root = sandbox["local_root"]
    show_dir = root / "Series" / "The Office {tmdb-2316}"
    show_dir.mkdir(parents=True, exist_ok=True)
    make_video(show_dir / "The.Office.mkv", marker=b"S")
    show_id = "tv-en-2005-the-office"  # NO -sNNeMM -> kind tv
    series = {
        show_id: {
            "status": "local_ready", "uploaded": False,
            "folder_path": str(show_dir), "filename": "The.Office.mkv",
            "metadata": {"title": "The Office", "year": 2005, "tmdb_id": 2316},
        }
    }
    _write_libs(sandbox, series=series)
    return show_id


# ---------------------------------------------------------------------------
# (a) movie -> rating / genres / runtime / cast / directors / imdb_url
# ---------------------------------------------------------------------------

def test_movie_detail_full(sandbox, make_video, patch_tmdb):
    patch_tmdb()
    mid = _seed_movie(sandbox, make_video)

    d = main.tmdb_detail(main.load_library(), mid)
    assert d is not None

    assert d["kind"] == "movie"
    assert d["tmdb_id"] == 27205
    assert d["title"] == "Inception"
    assert d["year"] == 2010
    assert d["tagline"] == "Your mind is the scene of the crime."
    assert d["rating"] == 8.4
    assert d["vote_count"] == 35000
    assert d["runtime"] == 148
    assert d["genres"] == ["Action", "Science Fiction"]
    assert d["release_date"] == "2010-07-15"
    assert d["status"] == "Released"
    assert d["homepage"] == "https://www.warnerbros.com/inception"

    # IMDb + TMDB links (imdb from imdb_id; tmdb from movie/id).
    assert d["imdb_id"] == "tt1375666"
    assert d["imdb_url"] == "https://www.imdb.com/title/tt1375666/"
    assert d["tmdb_url"] == "https://www.themoviedb.org/movie/27205"

    # Cast -> [{name,character}]; directors -> crew job==Director (de-duped).
    assert d["cast"][0] == {"name": "Leonardo DiCaprio", "character": "Cobb"}
    assert len(d["cast"]) == 3
    assert d["directors"] == ["Christopher Nolan"]  # the Writer dup is not re-added


def test_movie_cast_truncated_to_8(sandbox, make_video, monkeypatch):
    """Cast is capped at 8 even when TMDB returns more (contract truncation)."""
    # 10-member cast.
    big_credits = {"cast": [{"name": f"Actor {i}", "character": f"Role {i}"}
                            for i in range(10)], "crew": []}
    fake = _make_fake_tmdb_get()
    base_movie = dict(_MOVIE_DETAIL)

    def patched(url, params, api_key, _cache=True):
        if url.endswith("/movie/27205/credits"):
            return dict(big_credits)
        if url.endswith("/movie/27205"):
            return base_movie
        return None

    monkeypatch.setattr(main, "_tmdb_get", patched)
    monkeypatch.setattr(main.mvcommon, "tmdb_api_key", lambda: "TEST-KEY")
    mid = _seed_movie(sandbox, make_video)

    d = main.tmdb_detail(main.load_library(), mid)
    assert len(d["cast"]) == 8, "cast must be truncated to 8"
    assert d["cast"][0]["name"] == "Actor 0"


# ---------------------------------------------------------------------------
# (b) episode -> episode_title / air_date / season_number / episode_number
# ---------------------------------------------------------------------------

def test_episode_detail_full(sandbox, make_video, patch_tmdb):
    patch_tmdb()
    ep_id, _ = _seed_episode(sandbox, make_video)

    d = main.tmdb_detail(main.load_library(), ep_id)
    assert d is not None

    assert d["kind"] == "episode"
    assert d["tmdb_id"] == 2316  # the SHOW id (stamped on the episode leaf)
    # Episode-specific extras.
    assert d["episode_title"] == "Pilot"
    assert d["season_number"] == 1
    assert d["episode_number"] == 1
    assert d["air_date"] == "2005-03-24"
    assert d["overview"] == "The documentary crew arrives at Dunder Mifflin."
    assert d["rating"] == 7.6
    assert d["runtime"] == 23

    # IMDb comes from the SHOW external_ids; tmdb_url links to the show (tv) page.
    assert d["imdb_id"] == "tt0386676"
    assert d["imdb_url"] == "https://www.imdb.com/title/tt0386676/"
    assert d["tmdb_url"] == "https://www.themoviedb.org/tv/2316"
    # (The exact per-episode endpoint URL is asserted in the next test.)


def test_episode_uses_episode_endpoint(sandbox, make_video, patch_tmdb):
    """The episode path calls /tv/{show}/season/{s}/episode/{e} with the season +
    episode parsed from the leaf id (s01e01 -> 1/1), NOT the movie/tv endpoints."""
    fake = patch_tmdb()
    ep_id, _ = _seed_episode(sandbox, make_video)

    main.tmdb_detail(main.load_library(), ep_id)
    urls = fake.calls["urls"]
    assert any(u.endswith("/tv/2316/season/1/episode/1") for u in urls), urls
    assert not any(u.endswith("/movie/2316") for u in urls)


# ---------------------------------------------------------------------------
# (c) tv (show/season) -> number_of_seasons / networks / created-by directors
# ---------------------------------------------------------------------------

def test_tv_show_detail_full(sandbox, make_video, patch_tmdb):
    patch_tmdb()
    show_id = _seed_show(sandbox, make_video)

    d = main.tmdb_detail(main.load_library(), show_id)
    assert d is not None

    assert d["kind"] == "tv"
    assert d["tmdb_id"] == 2316
    assert d["title"] == "The Office"
    assert d["rating"] == 8.6
    assert d["runtime"] == 22  # episode_run_time[0]
    assert d["genres"] == ["Comedy"]
    assert d["status"] == "Ended"
    # TV-only extras.
    assert d["number_of_seasons"] == 9
    assert d["number_of_episodes"] == 201
    assert d["networks"] == ["NBC"]
    # directors == created_by names for a show (truncated to 3 — only 2 here).
    assert d["directors"] == ["Greg Daniels", "Ricky Gervais"]
    # IMDb from the show external_ids; cast from /tv/{id}/credits.
    assert d["imdb_id"] == "tt0386676"
    assert d["cast"][0] == {"name": "Steve Carell", "character": "Michael Scott"}
    assert d["tmdb_url"] == "https://www.themoviedb.org/tv/2316"
    # An empty tagline ("") must be OMITTED (not stored as a blank string).
    assert "tagline" not in d


# ---------------------------------------------------------------------------
# (d) no tmdb_id -> None (the route maps this to 404 "no tmdb_id")
# ---------------------------------------------------------------------------

def test_no_tmdb_id_returns_none(sandbox, make_video, patch_tmdb):
    patch_tmdb()
    mid = _seed_movie(sandbox, make_video, with_tmdb=False)
    assert main.tmdb_detail(main.load_library(), mid) is None


def test_unknown_id_returns_none(sandbox, make_video, patch_tmdb):
    """A crafted/unknown id is just a missing dict key -> None (never raises)."""
    patch_tmdb()
    _seed_movie(sandbox, make_video)
    assert main.tmdb_detail(main.load_library(), "mov-does-not-exist") is None
    # Path-traversal-looking ids are likewise just missing keys.
    assert main.tmdb_detail(main.load_library(), "../../etc/passwd") is None


# ---------------------------------------------------------------------------
# (e) a TMDB SUB-call failure -> PARTIAL dict (never a 500/raise)
# ---------------------------------------------------------------------------

def test_movie_partial_when_credits_fails(sandbox, make_video, patch_tmdb):
    """Movie base succeeds but /credits returns None -> a PARTIAL dict: the base
    fields are present, cast/directors are simply OMITTED. No raise, no 500."""
    patch_tmdb(fail_suffixes=("/credits",))
    mid = _seed_movie(sandbox, make_video)

    d = main.tmdb_detail(main.load_library(), mid)
    assert d is not None
    # Base movie fields survived.
    assert d["title"] == "Inception"
    assert d["rating"] == 8.4
    assert d["imdb_url"] == "https://www.imdb.com/title/tt1375666/"
    # The failed credits sub-call contributed nothing — omitted, not null.
    assert "cast" not in d
    assert "directors" not in d


def test_movie_partial_when_base_fails(sandbox, make_video, patch_tmdb):
    """Even the BASE movie call failing yields a still-useful offline core
    (tmdb_id/kind/tmdb_url + stored title) rather than None — so the route returns
    200 with what it has, never a misleading 'no tmdb_id' 404."""
    patch_tmdb(fail_suffixes=("/movie/27205", "/movie/27205/credits"))
    mid = _seed_movie(sandbox, make_video)

    d = main.tmdb_detail(main.load_library(), mid)
    assert d is not None
    assert d["tmdb_id"] == 27205
    assert d["kind"] == "movie"
    assert d["tmdb_url"] == "https://www.themoviedb.org/movie/27205"
    # The stored metadata.title seeds the dict so the entry is still named.
    assert d["title"] == "Inception"
    # No TMDB-only fields leaked in from the failed fetch.
    assert "rating" not in d
    assert "genres" not in d


def test_episode_partial_when_external_ids_fails(sandbox, make_video, patch_tmdb):
    """Episode details succeed but the show external_ids fails -> episode fields
    present, imdb omitted. Still a 200-able partial dict."""
    patch_tmdb(fail_suffixes=("/external_ids",))
    ep_id, _ = _seed_episode(sandbox, make_video)

    d = main.tmdb_detail(main.load_library(), ep_id)
    assert d is not None
    assert d["episode_title"] == "Pilot"
    assert d["season_number"] == 1
    assert "imdb_id" not in d
    assert "imdb_url" not in d


# ---------------------------------------------------------------------------
# (f) read-only + alias-safe
# ---------------------------------------------------------------------------

def test_tmdb_detail_is_read_only(sandbox, make_video, patch_tmdb):
    """tmdb_detail must not mutate any library file or the loaded library."""
    patch_tmdb()
    _seed_movie(sandbox, make_video)

    lib_paths = [sandbox["lib_movies"], sandbox["lib_series"], sandbox["lib_anime"]]
    before_bytes = {p: p.read_bytes() for p in lib_paths}
    before_mtime = {p: p.stat().st_mtime_ns for p in lib_paths}

    lib = main.load_library()
    main.tmdb_detail(lib, "mov-en-2010-inception")
    main.tmdb_detail(lib, "mov-en-2010-inception")  # twice — still no writes

    for p in lib_paths:
        assert p.read_bytes() == before_bytes[p], f"{p.name} bytes changed"
        assert p.stat().st_mtime_ns == before_mtime[p], f"{p.name} mtime changed"


def test_alias_resolves_to_primary(sandbox_alias, patch_tmdb):
    """A multi_ep_alias id resolves (one hop) to its PRIMARY leaf's metadata. The
    BSG alias library's primary carries no tmdb_id, so this returns None — but it
    must NOT raise on the virtual alias row (PR #21 crash class)."""
    patch_tmdb()
    alias_id = sandbox_alias["alias_id"]
    # No tmdb_id on the primary -> None, and crucially no KeyError/crash.
    assert main.tmdb_detail(main.load_library(), alias_id) is None

    # Now stamp a tmdb_id on the PRIMARY and confirm the alias dereferences to it.
    lib = main.load_library()
    primary_id = sandbox_alias["primary_id"]
    lib[primary_id].setdefault("metadata", {})["tmdb_id"] = 2316
    mvcommon.save_library(lib)

    d = main.tmdb_detail(main.load_library(), alias_id)
    assert d is not None
    assert d["tmdb_id"] == 2316
    # The alias is an episode leaf (s04e20) -> kind episode.
    assert d["kind"] == "episode"


# ---------------------------------------------------------------------------
# (f2) ONLINE-METADATA MERGE (IMP-E16) — tmdb_detail merges the mvonline.json cache
# (OMDb ratings/awards/box-office) when present, omits it when absent, NEVER does a
# live OMDb call, and an EPISODE inherits the SHOW's cached ratings.
# ---------------------------------------------------------------------------

@pytest.fixture()
def online_cache(tmp_path, monkeypatch):
    """Redirect main.ONLINE_CACHE_PATH to a tmp mvonline.json (never the real repo-root
    one) so the detail-merge reads a sandbox cache. Yields a seed(tmdb_id, data)
    callable that writes one cache entry. The OMDb network/cache is never touched —
    these tests exercise the CACHE-READ merge only, so no _omdb_get/omdb_fetch patch
    is needed (a live OMDb call here would be a bug the tests would catch via the
    monkeypatched guard)."""
    cache_path = tmp_path / "mvonline.json"
    assert str(cache_path) != main.ONLINE_CACHE_PATH, "must redirect away from the real mvonline.json"
    monkeypatch.setattr(main, "ONLINE_CACHE_PATH", str(cache_path))
    # Guard: a live OMDb fetch must never happen inside the dossier path.
    monkeypatch.setattr(
        main, "omdb_fetch",
        lambda **k: (_ for _ in ()).throw(AssertionError("tmdb_detail must NOT call omdb_fetch (cache-only)")),
    )

    def seed(tmdb_id, data):
        main.online_cache_set(tmdb_id, data)

    return seed


@pytest.fixture()
def extra_cache(tmp_path, monkeypatch):
    """Redirect main.EXTRA_CACHE_PATH to a tmp mvextra.json (never the real repo-root
    one) so the detail-merge reads a sandbox trivia cache. Yields a seed(tmdb_id,
    trivia_list) callable that writes one cache entry. EXA/GROQ are never touched —
    these tests exercise the CACHE-READ merge only, so the fixture installs a guard
    that fails if tmdb_detail ever makes a live EXA/GROQ call (the cache-not-live
    contract)."""
    cache_path = tmp_path / "mvextra.json"
    assert str(cache_path) != main.EXTRA_CACHE_PATH, "must redirect away from the real mvextra.json"
    monkeypatch.setattr(main, "EXTRA_CACHE_PATH", str(cache_path))
    # Guard: a live EXA/GROQ fetch must never happen inside the dossier path.
    monkeypatch.setattr(
        main, "exa_search_trivia",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("tmdb_detail must NOT call exa_search_trivia (cache-only)")),
    )
    monkeypatch.setattr(
        main, "groq_distill_trivia",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("tmdb_detail must NOT call groq_distill_trivia (cache-only)")),
    )

    def seed(tmdb_id, trivia):
        main.extra_cache_set(tmdb_id, {"trivia": trivia, "fetched_at": "2026-06-01T00:00:00+00:00"})

    return seed


_TRIVIA_INCEPTION = [
    {"text": "The rotating-hallway fight used a giant practical set.", "source": "ScreenRant"},
    {"text": "The spinning-top ending was deliberately left ambiguous.", "source": "IMDb"},
]


_CACHE_INCEPTION = {
    "imdb_id": "tt1375666",
    "ratings": {"imdb": "8.8", "rotten_tomatoes": "87%", "metacritic": "74"},
    "rated": "PG-13",
    "runtime": "148 min",
    "awards": "Won 4 Oscars. 159 wins & 220 nominations total",
    "boxoffice": "$292,587,330",
    "fetched_at": "2026-06-01T00:00:00+00:00",
}


def test_detail_merges_cached_ratings_when_present(sandbox, make_video, patch_tmdb, online_cache):
    """A movie with a cached online entry: tmdb_detail adds ratings/rated/awards/
    boxoffice from mvonline.json (NO live OMDb call — the fixture guard enforces it)."""
    patch_tmdb()
    online_cache(27205, dict(_CACHE_INCEPTION))
    mid = _seed_movie(sandbox, make_video)

    d = main.tmdb_detail(main.load_library(), mid)
    assert d["ratings"] == {"imdb": "8.8", "rotten_tomatoes": "87%", "metacritic": "74"}
    assert d["rated"] == "PG-13"
    assert d["awards"].startswith("Won 4 Oscars")
    assert d["boxoffice"] == "$292,587,330"
    # The TMDB-sourced fields are still present (merge is additive).
    assert d["title"] == "Inception"
    assert d["tmdb_id"] == 27205


def test_detail_omits_online_fields_when_absent(sandbox, make_video, patch_tmdb, online_cache):
    """With NO cache entry for the title, the online-only keys are simply absent
    (the dossier degrades to the TMDB-only dict)."""
    patch_tmdb()
    # online_cache fixture active but nothing seeded for 27205.
    mid = _seed_movie(sandbox, make_video)

    d = main.tmdb_detail(main.load_library(), mid)
    assert "ratings" not in d
    assert "rated" not in d
    assert "awards" not in d
    assert "boxoffice" not in d
    # TMDB fields still present.
    assert d["rating"] == 8.4  # the TMDB vote_average (distinct from OMDb ratings)


def test_detail_episode_inherits_show_cached_ratings(sandbox, make_video, patch_tmdb, online_cache):
    """An EPISODE leaf carries the SHOW's tmdb_id (2316). A cache entry keyed by the
    show id is merged into the episode dossier — episodes inherit show ratings."""
    patch_tmdb()
    online_cache(2316, {
        "imdb_id": "tt0386676",
        "ratings": {"imdb": "9.0"},
        "rated": "TV-14",
        "awards": "Won 5 Primetime Emmys",
        "boxoffice": "",
        "fetched_at": "2026-06-01T00:00:00+00:00",
    })
    ep_id, _ = _seed_episode(sandbox, make_video)

    d = main.tmdb_detail(main.load_library(), ep_id)
    assert d["kind"] == "episode"
    assert d["ratings"] == {"imdb": "9.0"}   # inherited from the show's cache entry
    assert d["rated"] == "TV-14"
    assert d["awards"] == "Won 5 Primetime Emmys"
    # An empty boxoffice in the cache is OMITTED (not a blank string).
    assert "boxoffice" not in d


def test_detail_merge_partial_cache(sandbox, make_video, patch_tmdb, online_cache):
    """A cache entry with only some fields: ratings present, awards/rated/boxoffice
    empty -> only the non-empty fields are merged."""
    patch_tmdb()
    online_cache(27205, {
        "imdb_id": "tt1375666",
        "ratings": {"imdb": "8.8"},  # RT/MC missing
        "rated": "",                 # empty -> omitted
        "awards": "",
        "boxoffice": "",
        "fetched_at": "2026-06-01T00:00:00+00:00",
    })
    mid = _seed_movie(sandbox, make_video)

    d = main.tmdb_detail(main.load_library(), mid)
    assert d["ratings"] == {"imdb": "8.8"}
    assert "rated" not in d
    assert "awards" not in d
    assert "boxoffice" not in d


def test_detail_merge_with_no_tmdb_key(sandbox, make_video, monkeypatch, online_cache):
    """The online merge is INDEPENDENT of the TMDB key: with NO TMDB key (the offline
    core path), a populated mvonline.json still enriches the dossier with ratings."""
    # No TMDB key -> tmdb_detail returns the offline core, but still merges the cache.
    monkeypatch.setattr(main.mvcommon, "tmdb_api_key", lambda: "")
    online_cache(27205, dict(_CACHE_INCEPTION))
    mid = _seed_movie(sandbox, make_video)

    d = main.tmdb_detail(main.load_library(), mid)
    # Offline core present.
    assert d["tmdb_id"] == 27205
    assert d["kind"] == "movie"
    # The online ratings were merged even though no TMDB fetch happened.
    assert d["ratings"] == {"imdb": "8.8", "rotten_tomatoes": "87%", "metacritic": "74"}
    assert d["awards"].startswith("Won 4 Oscars")
    # No TMDB-only fields (the fetch was skipped).
    assert "genres" not in d


def test_detail_merge_is_read_only(sandbox, make_video, patch_tmdb, online_cache):
    """Merging the cache must not write the library OR the cache file."""
    patch_tmdb()
    online_cache(27205, dict(_CACHE_INCEPTION))
    mid = _seed_movie(sandbox, make_video)

    lib_paths = [sandbox["lib_movies"], sandbox["lib_series"], sandbox["lib_anime"]]
    before = {p: p.read_bytes() for p in lib_paths}
    with open(main.ONLINE_CACHE_PATH, "rb") as fh:
        cache_before = fh.read()

    lib = main.load_library()
    main.tmdb_detail(lib, mid)
    main.tmdb_detail(lib, mid)

    for p in lib_paths:
        assert p.read_bytes() == before[p], f"{p.name} changed during a read-only detail"
    with open(main.ONLINE_CACHE_PATH, "rb") as fh:
        assert fh.read() == cache_before, "mvonline.json changed during a read-only detail"


# ---------------------------------------------------------------------------
# (f3) TRIVIA MERGE (IMP-E16/A5) — tmdb_detail merges the mvextra.json cache (EXA+GROQ
# distilled facts) when present, omits it when absent, NEVER does a live EXA/GROQ call,
# and an EPISODE inherits the SHOW's cached trivia.
# ---------------------------------------------------------------------------

def test_detail_merges_trivia_when_present(sandbox, make_video, patch_tmdb, extra_cache):
    """A movie with a cached trivia entry: tmdb_detail adds `trivia` from mvextra.json
    (NO live EXA/GROQ call — the fixture guard enforces it)."""
    patch_tmdb()
    extra_cache(27205, _TRIVIA_INCEPTION)
    mid = _seed_movie(sandbox, make_video)

    d = main.tmdb_detail(main.load_library(), mid)
    assert d["trivia"] == _TRIVIA_INCEPTION
    # The TMDB-sourced fields are still present (merge is additive).
    assert d["title"] == "Inception"
    assert d["tmdb_id"] == 27205


def test_detail_omits_trivia_when_absent(sandbox, make_video, patch_tmdb, extra_cache):
    """With NO cache entry for the title, the `trivia` key is simply absent."""
    patch_tmdb()
    # extra_cache fixture active but nothing seeded for 27205.
    mid = _seed_movie(sandbox, make_video)

    d = main.tmdb_detail(main.load_library(), mid)
    assert "trivia" not in d
    assert d["rating"] == 8.4  # TMDB fields still present


def test_detail_empty_trivia_list_omitted(sandbox, make_video, patch_tmdb, extra_cache):
    """A cache entry whose trivia list is empty is OMITTED (not a blank list)."""
    patch_tmdb()
    extra_cache(27205, [])
    mid = _seed_movie(sandbox, make_video)

    d = main.tmdb_detail(main.load_library(), mid)
    assert "trivia" not in d


def test_detail_episode_inherits_show_trivia(sandbox, make_video, patch_tmdb, extra_cache):
    """An EPISODE leaf carries the SHOW's tmdb_id (2316). A trivia entry keyed by the
    show id is merged into the episode dossier — episodes inherit show trivia."""
    patch_tmdb()
    show_trivia = [{"text": "The cast improvised many of the talking-head scenes.", "source": "IMDb"}]
    extra_cache(2316, show_trivia)
    ep_id, _ = _seed_episode(sandbox, make_video)

    d = main.tmdb_detail(main.load_library(), ep_id)
    assert d["kind"] == "episode"
    assert d["trivia"] == show_trivia  # inherited from the show's cache entry


def test_detail_trivia_merge_is_read_only(sandbox, make_video, patch_tmdb, extra_cache):
    """Merging the trivia cache must not write the library OR the cache file."""
    patch_tmdb()
    extra_cache(27205, _TRIVIA_INCEPTION)
    mid = _seed_movie(sandbox, make_video)

    lib_paths = [sandbox["lib_movies"], sandbox["lib_series"], sandbox["lib_anime"]]
    before = {p: p.read_bytes() for p in lib_paths}
    with open(main.EXTRA_CACHE_PATH, "rb") as fh:
        cache_before = fh.read()

    lib = main.load_library()
    main.tmdb_detail(lib, mid)
    main.tmdb_detail(lib, mid)

    for p in lib_paths:
        assert p.read_bytes() == before[p], f"{p.name} changed during a read-only detail"
    with open(main.EXTRA_CACHE_PATH, "rb") as fh:
        assert fh.read() == cache_before, "mvextra.json changed during a read-only detail"


# ---------------------------------------------------------------------------
# (g) HTTP surface — GET /api/detail/{id} mirrors tmdb_detail end-to-end
# ---------------------------------------------------------------------------

@pytest.mark.usefixtures("web_as_local_admin")
def test_api_detail_movie_endpoint(sandbox, make_video, patch_tmdb):
    from webui.server import create_app
    patch_tmdb()
    mid = _seed_movie(sandbox, make_video)

    client = TestClient(create_app())
    r = client.get(f"/api/detail/{mid}")
    assert r.status_code == 200, f"/api/detail -> {r.status_code}: {r.text}"
    body = r.json()
    assert body["kind"] == "movie"
    assert body["tmdb_id"] == 27205
    assert body["rating"] == 8.4
    assert body["genres"] == ["Action", "Science Fiction"]
    assert body["cast"][0] == {"name": "Leonardo DiCaprio", "character": "Cobb"}
    assert body["directors"] == ["Christopher Nolan"]
    assert body["imdb_url"] == "https://www.imdb.com/title/tt1375666/"
    # The endpoint returns tmdb_detail verbatim.
    assert body == main.tmdb_detail(main.load_library(), mid)


@pytest.mark.usefixtures("web_as_local_admin")
def test_api_detail_endpoint_includes_cached_online_metadata(sandbox, make_video, patch_tmdb, online_cache):
    """GET /api/detail surfaces the merged online ratings/awards/box-office when the
    mvonline.json cache has them — the dossier's headline IMP-E16 payload, over HTTP."""
    from webui.server import create_app
    patch_tmdb()
    online_cache(27205, dict(_CACHE_INCEPTION))
    mid = _seed_movie(sandbox, make_video)

    client = TestClient(create_app())
    r = client.get(f"/api/detail/{mid}")
    assert r.status_code == 200, f"/api/detail -> {r.status_code}: {r.text}"
    body = r.json()
    assert body["ratings"] == {"imdb": "8.8", "rotten_tomatoes": "87%", "metacritic": "74"}
    assert body["rated"] == "PG-13"
    assert body["awards"].startswith("Won 4 Oscars")
    assert body["boxoffice"] == "$292,587,330"


@pytest.mark.usefixtures("web_as_local_admin")
def test_api_detail_endpoint_omits_online_metadata_when_absent(sandbox, make_video, patch_tmdb, online_cache):
    """With no cache entry, GET /api/detail omits the online-only keys (200, partial)."""
    from webui.server import create_app
    patch_tmdb()
    mid = _seed_movie(sandbox, make_video)  # no cache seeded

    client = TestClient(create_app())
    r = client.get(f"/api/detail/{mid}")
    assert r.status_code == 200
    body = r.json()
    assert "ratings" not in body
    assert "awards" not in body
    assert "boxoffice" not in body


@pytest.mark.usefixtures("web_as_local_admin")
def test_api_detail_endpoint_includes_trivia(sandbox, make_video, patch_tmdb, extra_cache):
    """GET /api/detail surfaces the merged EXA+GROQ trivia when the mvextra.json cache
    has it (IMP-E16/A5), over HTTP — and never makes a live EXA/GROQ call (the fixture
    guard enforces the cache-only contract)."""
    from webui.server import create_app
    patch_tmdb()
    extra_cache(27205, _TRIVIA_INCEPTION)
    mid = _seed_movie(sandbox, make_video)

    client = TestClient(create_app())
    r = client.get(f"/api/detail/{mid}")
    assert r.status_code == 200, f"/api/detail -> {r.status_code}: {r.text}"
    body = r.json()
    assert body["trivia"] == _TRIVIA_INCEPTION


@pytest.mark.usefixtures("web_as_local_admin")
def test_api_detail_endpoint_omits_trivia_when_absent(sandbox, make_video, patch_tmdb, extra_cache):
    """With no trivia cache entry, GET /api/detail omits the `trivia` key (200, partial)."""
    from webui.server import create_app
    patch_tmdb()
    mid = _seed_movie(sandbox, make_video)  # no trivia seeded

    client = TestClient(create_app())
    r = client.get(f"/api/detail/{mid}")
    assert r.status_code == 200
    assert "trivia" not in r.json()


@pytest.mark.usefixtures("web_as_local_admin")
def test_api_detail_episode_endpoint(sandbox, make_video, patch_tmdb):
    from webui.server import create_app
    patch_tmdb()
    ep_id, _ = _seed_episode(sandbox, make_video)

    client = TestClient(create_app())
    r = client.get(f"/api/detail/{ep_id}")
    assert r.status_code == 200, f"/api/detail -> {r.status_code}: {r.text}"
    body = r.json()
    assert body["kind"] == "episode"
    assert body["episode_title"] == "Pilot"
    assert body["season_number"] == 1
    assert body["episode_number"] == 1
    assert body["air_date"] == "2005-03-24"


@pytest.mark.usefixtures("web_as_local_admin")
def test_api_detail_404_when_no_tmdb_id(sandbox, make_video, patch_tmdb):
    from webui.server import create_app
    patch_tmdb()
    mid = _seed_movie(sandbox, make_video, with_tmdb=False)

    client = TestClient(create_app())
    r = client.get(f"/api/detail/{mid}")
    assert r.status_code == 404
    assert r.json() == {"detail": "no tmdb_id"}


@pytest.mark.usefixtures("web_as_local_admin")
def test_api_detail_partial_no_500_on_subcall_failure(sandbox, make_video, patch_tmdb):
    """A TMDB sub-call failure must surface as a 200 PARTIAL dict, never a 500."""
    from webui.server import create_app
    patch_tmdb(fail_suffixes=("/credits",))
    mid = _seed_movie(sandbox, make_video)

    client = TestClient(create_app())
    r = client.get(f"/api/detail/{mid}")
    assert r.status_code == 200, f"expected 200 partial, got {r.status_code}: {r.text}"
    body = r.json()
    assert body["title"] == "Inception"
    assert "cast" not in body  # the failed credits call contributed nothing


def test_api_detail_requires_auth(sandbox, make_video, patch_tmdb):
    """With NO auth fixture and a non-loopback TestClient host, /api/detail is
    gated by the standard /api/* middleware -> 401 (honors the auth model). The
    token store is never consulted because the request is not the local admin."""
    from webui.server import create_app
    patch_tmdb()
    mid = _seed_movie(sandbox, make_video)

    # Default TestClient host is "testclient" (non-loopback) -> not the local admin
    # and no token -> 401, exactly like every other /api/* route.
    client = TestClient(create_app())
    r = client.get(f"/api/detail/{mid}")
    assert r.status_code == 401, f"expected 401 without auth, got {r.status_code}"
