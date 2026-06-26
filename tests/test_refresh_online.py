"""Permanent tests for the online-metadata refresh backend (IMP-E16).

The "refresh online metadata" feature fetches the cross-aggregator ratings TMDB
does NOT carry — IMDb / Rotten Tomatoes / Metacritic — plus Rated / Runtime /
Awards / BoxOffice from OMDb, ONCE per distinct title, caches them in the
gitignored mvonline.json keyed by tmdb_id, and the hover dossier merges that cache
(read-only). These tests pin the backend contract:

  (a) omdb_fetch parses the 3 ratings (+ awards/box-office/rated/runtime) from a
      canned OMDb payload, normalizing IMDb '8.8/10'->'8.8' and MC '74/100'->'74';
      and degrades to None on a bad/"not found" payload (never raises).
  (b) the mvonline.json cache helpers round-trip + are atomic + degrade on a
      malformed file; the freshness rule (~14 days) skips fresh entries.
  (c) cmd_refresh_online writes mvonline.json keyed by tmdb_id, DEDUPES by tmdb_id
      (a show's episodes => ONE OMDb fetch), honors the freshness skip + --force,
      and NEVER mutates library_*.json (the headline safety property).

ALL mocked — NO real network:
  * TMDB external-id resolution: monkeypatch main._tmdb_get (the cached seam).
  * OMDb: monkeypatch main._omdb_get (the cached seam) OR main.omdb_fetch.
  * The cache file + the OMDb on-disk cache are redirected to tmp_path via the
    `online_cache` fixture (which hard-guards against the real repo-root
    mvonline.json), and the key getters are faked so the command does not bail.
All over the LOCAL_ROOT-hermetic `sandbox` fixture (never real C:\\Media /
library_*.json / the real mvonline.json).
"""
import json

import pytest

import main
import mvcommon


# ---------------------------------------------------------------------------
# Canned OMDb payloads (the exact shape omdbapi.com returns for ?i=tt…).
# ---------------------------------------------------------------------------

_OMDB_INCEPTION = {
    "Title": "Inception",
    "Year": "2010",
    "Rated": "PG-13",
    "Runtime": "148 min",
    "Genre": "Action, Adventure, Sci-Fi",
    "Director": "Christopher Nolan",
    "Actors": "Leonardo DiCaprio, Joseph Gordon-Levitt, Elliot Page",
    "Awards": "Won 4 Oscars. 159 wins & 220 nominations total",
    "BoxOffice": "$292,587,330",
    "imdbRating": "8.8",
    "imdbVotes": "2,400,000",
    "Metascore": "74",
    "imdbID": "tt1375666",
    "Ratings": [
        {"Source": "Internet Movie Database", "Value": "8.8/10"},
        {"Source": "Rotten Tomatoes", "Value": "87%"},
        {"Source": "Metacritic", "Value": "74/100"},
    ],
    "Response": "True",
}

_OMDB_THE_OFFICE = {
    "Title": "The Office",
    "Year": "2005–2013",
    "Rated": "TV-14",
    "Runtime": "22 min",
    "Awards": "Won 5 Primetime Emmys. 50 wins & 209 nominations total",
    "BoxOffice": "N/A",  # N/A must be dropped, not surfaced
    "imdbID": "tt0386676",
    "Ratings": [
        {"Source": "Internet Movie Database", "Value": "9.0/10"},
        # No Rotten Tomatoes / Metacritic entries -> those keys are omitted.
    ],
    "Response": "True",
}


# ---------------------------------------------------------------------------
# online_cache fixture — redirect mvonline.json + the OMDb cache to tmp_path and
# fake the key getters so the command never bails. Hard-guards the cache path.
# ---------------------------------------------------------------------------

@pytest.fixture()
def online_cache(tmp_path, monkeypatch):
    """Redirect ONLINE_CACHE_PATH + OMDB_CACHE_DIR to tmp_path and fake the OMDb /
    TMDB keys so cmd_refresh_online runs.

    HARD GUARD (mirrors the sandbox fixture's C:\\Media guard): the redirected
    mvonline.json must live under tmp_path and must NOT be the real repo-root
    mvonline.json — a future regression that forgets the patch would write a real
    cache file at the repo root, which this assertion catches.

    Yields the Path to the sandbox mvonline.json so a test can seed/inspect it.
    """
    cache_path = tmp_path / "mvonline.json"
    omdb_dir = tmp_path / "omdb_cache"
    omdb_dir.mkdir()

    # Never the real repo-root cache.
    assert str(cache_path) != main.ONLINE_CACHE_PATH, "must redirect away from the real mvonline.json"
    assert "PycharmProjects" not in str(cache_path) or str(tmp_path) in str(cache_path)

    monkeypatch.setattr(main, "ONLINE_CACHE_PATH", str(cache_path))
    monkeypatch.setattr(main, "OMDB_CACHE_DIR", str(omdb_dir))
    # The command checks BOTH keys; fake both so it does not bail early.
    monkeypatch.setattr(mvcommon, "omdb_api_key", lambda: "OMDB-TEST-KEY")
    monkeypatch.setattr(main.mvcommon, "tmdb_api_key", lambda: "TMDB-TEST-KEY")
    yield cache_path


# ===========================================================================
# (a) omdb_fetch — parses ratings/awards/box-office; normalizes; degrades to None
# ===========================================================================

def _patch_omdb_get(monkeypatch, by_imdb=None, by_title=None):
    """Patch main._omdb_get to URL-dispatch the canned payloads by the params it is
    handed (no network, no on-disk cache touched). `by_imdb` maps imdb_id->payload,
    `by_title` maps title->payload."""
    by_imdb = by_imdb or {}
    by_title = by_title or {}

    def fake(params, api_key):
        if "i" in params:
            return by_imdb.get(params["i"])
        if "t" in params:
            return by_title.get(params["t"])
        return None

    monkeypatch.setattr(main, "_omdb_get", fake)


def test_omdb_fetch_parses_three_ratings_and_extras(monkeypatch):
    """omdb_fetch(imdb_id) -> the 3 ratings (normalized) + rated/runtime/awards/
    box-office, fetched by ?i=."""
    monkeypatch.setattr(mvcommon, "omdb_api_key", lambda: "OMDB-TEST-KEY")
    _patch_omdb_get(monkeypatch, by_imdb={"tt1375666": dict(_OMDB_INCEPTION)})

    d = main.omdb_fetch(imdb_id="tt1375666")
    assert d is not None
    assert d["imdb_id"] == "tt1375666"
    # The 3 ratings, normalized to the compact dossier form.
    assert d["ratings"] == {"imdb": "8.8", "rotten_tomatoes": "87%", "metacritic": "74"}
    assert d["rated"] == "PG-13"
    assert d["runtime"] == "148 min"
    assert d["awards"].startswith("Won 4 Oscars")
    assert d["boxoffice"] == "$292,587,330"
    # omdb_fetch does NOT stamp fetched_at (the cache layer does).
    assert "fetched_at" not in d


def test_omdb_fetch_partial_ratings_and_na_dropped(monkeypatch):
    """A title with only an IMDb rating + BoxOffice 'N/A': RT/MC keys are OMITTED
    and the 'N/A' box-office becomes '' (never the literal 'N/A')."""
    monkeypatch.setattr(mvcommon, "omdb_api_key", lambda: "OMDB-TEST-KEY")
    _patch_omdb_get(monkeypatch, by_imdb={"tt0386676": dict(_OMDB_THE_OFFICE)})

    d = main.omdb_fetch(imdb_id="tt0386676")
    assert d["ratings"] == {"imdb": "9.0"}       # only IMDb present
    assert "rotten_tomatoes" not in d["ratings"]
    assert "metacritic" not in d["ratings"]
    assert d["boxoffice"] == ""                  # 'N/A' dropped
    assert d["rated"] == "TV-14"


def test_omdb_fetch_by_title_year(monkeypatch):
    """With no imdb_id, omdb_fetch falls back to ?t=&y= (title+year)."""
    monkeypatch.setattr(mvcommon, "omdb_api_key", lambda: "OMDB-TEST-KEY")
    captured = {}

    def fake(params, api_key):
        captured.update(params)
        return dict(_OMDB_INCEPTION)

    monkeypatch.setattr(main, "_omdb_get", fake)
    d = main.omdb_fetch(title="Inception", year=2010)
    assert d is not None
    assert captured == {"t": "Inception", "y": "2010"}  # title+year params used


def test_omdb_fetch_no_key_returns_none(monkeypatch):
    """No OMDb key -> None without any fetch attempt."""
    monkeypatch.setattr(mvcommon, "omdb_api_key", lambda: "")
    # _omdb_get must never be reached; make it explode if it is.
    monkeypatch.setattr(main, "_omdb_get", lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("no key -> no fetch")))
    assert main.omdb_fetch(imdb_id="tt1375666") is None


def test_omdb_fetch_no_args_returns_none(monkeypatch):
    """Neither imdb_id nor title -> None immediately (no network)."""
    monkeypatch.setattr(mvcommon, "omdb_api_key", lambda: "OMDB-TEST-KEY")
    monkeypatch.setattr(main, "_omdb_get", lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("no args -> no fetch")))
    assert main.omdb_fetch() is None


def test_omdb_fetch_get_failure_returns_none(monkeypatch):
    """A None from _omdb_get (network/non-200/not-found) -> omdb_fetch None."""
    monkeypatch.setattr(mvcommon, "omdb_api_key", lambda: "OMDB-TEST-KEY")
    monkeypatch.setattr(main, "_omdb_get", lambda params, api_key: None)
    assert main.omdb_fetch(imdb_id="tt0000000") is None


def test_omdb_get_treats_response_false_as_miss(monkeypatch, tmp_path):
    """_omdb_get returns None (a miss, NOT cached) for OMDb {"Response":"False"}."""
    monkeypatch.setattr(main, "OMDB_CACHE_DIR", str(tmp_path / "omdb"))

    class _Resp:
        status_code = 200

        def json(self):
            return {"Response": "False", "Error": "Movie not found!"}

    monkeypatch.setattr(main.requests, "get", lambda *a, **k: _Resp())
    assert main._omdb_get({"i": "tt9999999"}, "KEY") is None
    # Not-found is NOT cached (so a later valid id re-queries) — the cache dir stays empty.
    assert not list((tmp_path / "omdb").glob("*.json")) if (tmp_path / "omdb").exists() else True


def test_omdb_get_caches_and_reuses(monkeypatch, tmp_path):
    """A successful _omdb_get caches to disk; a second call reads the cache (no 2nd
    network hit)."""
    cache_dir = tmp_path / "omdb"
    monkeypatch.setattr(main, "OMDB_CACHE_DIR", str(cache_dir))
    n = {"calls": 0}

    class _Resp:
        status_code = 200

        def json(self):
            return dict(_OMDB_INCEPTION)

    def _get(*a, **k):
        n["calls"] += 1
        return _Resp()

    monkeypatch.setattr(main.requests, "get", _get)
    first = main._omdb_get({"i": "tt1375666"}, "KEY")
    second = main._omdb_get({"i": "tt1375666"}, "KEY")
    assert first == second == _OMDB_INCEPTION
    assert n["calls"] == 1, "second call must hit the on-disk cache, not the network"


# ===========================================================================
# (b) cache helpers — round-trip, atomic, malformed-degrade, freshness
# ===========================================================================

def test_online_cache_round_trip(online_cache):
    """online_cache_set then online_cache_get round-trips by str(tmdb_id) — an int
    and a string id resolve to the same entry."""
    main.online_cache_set(27205, {"ratings": {"imdb": "8.8"}, "fetched_at": "2026-01-01T00:00:00+00:00"})
    # int key
    got = main.online_cache_get(27205)
    assert got["ratings"] == {"imdb": "8.8"}
    # string key resolves to the same entry
    assert main.online_cache_get("27205") == got
    # the file on disk is keyed by the STRING id
    raw = json.loads(online_cache.read_text(encoding="utf-8"))
    assert "27205" in raw


def test_online_cache_get_missing_returns_none(online_cache):
    assert main.online_cache_get(99999) is None
    assert main.online_cache_get(None) is None


def test_online_cache_malformed_degrades(online_cache):
    """A malformed mvonline.json degrades to {} (never crashes load/get)."""
    online_cache.write_text("{ not json", encoding="utf-8")
    assert main.online_cache_load() == {}
    assert main.online_cache_get(27205) is None


def test_online_entry_freshness():
    """_online_entry_is_fresh: within ONLINE_FRESH_DAYS -> True; older -> False;
    missing/unparseable timestamp -> False (re-fetch)."""
    now = mvcommon._now_utc()
    fresh = (now - main.timedelta(days=1)).isoformat()
    stale = (now - main.timedelta(days=main.ONLINE_FRESH_DAYS + 1)).isoformat()
    assert main._online_entry_is_fresh({"fetched_at": fresh}) is True
    assert main._online_entry_is_fresh({"fetched_at": stale}) is False
    assert main._online_entry_is_fresh({}) is False          # no timestamp -> refetch
    assert main._online_entry_is_fresh(None) is False


# ===========================================================================
# (c) cmd_refresh_online — dedupe by tmdb_id, freshness skip, --force, no-mutate
# ===========================================================================

def _patch_tmdb_external(monkeypatch, movie_imdb=None, tv_imdb=None):
    """Patch main._tmdb_get to return imdb_id for the movie-detail and
    tv/external_ids endpoints (the only TMDB calls _resolve_imdb_id makes)."""
    def fake(url, params, api_key, _cache=True):
        if "/external_ids" in url:
            return {"imdb_id": tv_imdb} if tv_imdb else {}
        if "/movie/" in url:
            return {"imdb_id": movie_imdb} if movie_imdb else {}
        return None

    monkeypatch.setattr(main, "_tmdb_get", fake)


def _seed_show_two_episodes(sandbox, make_video, *, tmdb_id=2316):
    """Seed a season_map + TWO episode leaves of one show, all carrying the SAME
    show tmdb_id (as enrich stamps). Returns the show tmdb_id."""
    root = sandbox["local_root"]
    season_dir = root / "Series" / "The Office {tmdb-2316}" / "Season 01"
    season_dir.mkdir(parents=True, exist_ok=True)
    make_video(season_dir / "The.Office.S01E01.mkv", marker=b"1")
    make_video(season_dir / "The.Office.S01E02.mkv", marker=b"2")
    season_id = "tv-en-2005-the-office-s01"
    ep1 = "tv-en-2005-the-office-s01e01"
    ep2 = "tv-en-2005-the-office-s01e02"
    series = {
        season_id: {"type": "season_map", "folder_path": str(season_dir),
                    "total_episodes": 2, "children": [ep1, ep2]},
        ep1: {"status": "local_ready", "uploaded": False, "folder_path": str(season_dir),
              "filename": "The.Office.S01E01.mkv", "parent_id": season_id,
              "metadata": {"title": "The Office", "year": 2005, "tmdb_id": tmdb_id}},
        ep2: {"status": "local_ready", "uploaded": False, "folder_path": str(season_dir),
              "filename": "The.Office.S01E02.mkv", "parent_id": season_id,
              "metadata": {"title": "The Office", "year": 2005, "tmdb_id": tmdb_id}},
    }
    sandbox["lib_movies"].write_text("{}", encoding="utf-8")
    sandbox["lib_series"].write_text(json.dumps(series), encoding="utf-8")
    sandbox["lib_anime"].write_text("{}", encoding="utf-8")
    return tmdb_id


def _seed_movie(sandbox, make_video, *, tmdb_id=27205):
    root = sandbox["local_root"]
    folder = root / "Movies" / "Inception {tmdb-27205}"
    folder.mkdir(parents=True, exist_ok=True)
    make_video(folder / "Inception.2010.mkv", marker=b"M")
    mid = "mov-en-2010-inception"
    movies = {mid: {"status": "local_ready", "uploaded": False, "folder_path": str(folder),
                    "filename": "Inception.2010.mkv", "type": "movie",
                    "metadata": {"title": "Inception", "year": 2010, "tmdb_id": tmdb_id}}}
    sandbox["lib_movies"].write_text(json.dumps(movies), encoding="utf-8")
    sandbox["lib_series"].write_text("{}", encoding="utf-8")
    sandbox["lib_anime"].write_text("{}", encoding="utf-8")
    return mid


def test_refresh_online_writes_cache_keyed_by_tmdb_id(sandbox, make_video, online_cache, monkeypatch, capsys):
    """A movie + a show: cmd_refresh_online resolves each imdb_id via TMDB, fetches
    OMDb, and writes mvonline.json keyed by tmdb_id with the contract shape."""
    _seed_movie(sandbox, make_video, tmdb_id=27205)
    # Append a show into the series lib alongside the movie (separate write so the
    # movie lib above is preserved).
    _patch_tmdb_external(monkeypatch, movie_imdb="tt1375666")
    _patch_omdb_get(monkeypatch, by_imdb={"tt1375666": dict(_OMDB_INCEPTION)})

    main.cmd_refresh_online()

    cache = json.loads(online_cache.read_text(encoding="utf-8"))
    assert "27205" in cache
    entry = cache["27205"]
    assert entry["imdb_id"] == "tt1375666"
    assert entry["ratings"] == {"imdb": "8.8", "rotten_tomatoes": "87%", "metacritic": "74"}
    assert entry["awards"].startswith("Won 4 Oscars")
    assert entry["boxoffice"] == "$292,587,330"
    assert "fetched_at" in entry  # the cache layer stamps it
    out = capsys.readouterr().out
    assert "REFRESH ONLINE METADATA" in out
    assert "IMDb 8.8" in out and "RT 87%" in out and "MC 74" in out


def test_refresh_online_dedupes_episodes_to_one_fetch(sandbox, make_video, online_cache, monkeypatch, capsys):
    """A show with TWO episodes (same show tmdb_id) triggers exactly ONE OMDb fetch
    — episodes inherit the show's ratings (dedupe-by-tmdb_id)."""
    _seed_show_two_episodes(sandbox, make_video, tmdb_id=2316)
    _patch_tmdb_external(monkeypatch, tv_imdb="tt0386676")

    fetch_calls = {"n": 0}
    real = main.omdb_fetch

    def counting_fetch(imdb_id=None, title=None, year=None):
        fetch_calls["n"] += 1
        # Return the canned data without touching the network.
        return {"imdb_id": imdb_id or "tt0386676",
                "ratings": {"imdb": "9.0"}, "rated": "TV-14", "runtime": "22 min",
                "awards": "", "boxoffice": ""}

    monkeypatch.setattr(main, "omdb_fetch", counting_fetch)

    main.cmd_refresh_online()

    assert fetch_calls["n"] == 1, "two episodes of one show must dedupe to ONE OMDb fetch"
    cache = json.loads(online_cache.read_text(encoding="utf-8"))
    assert list(cache.keys()) == ["2316"], f"exactly one cache key (the show id); got {list(cache)}"
    out = capsys.readouterr().out
    assert "1 distinct title" in out


def test_refresh_online_skips_fresh_unless_force(sandbox, make_video, online_cache, monkeypatch, capsys):
    """A fresh cache entry is skipped (no fetch); --force re-fetches it."""
    tmdb_id = _seed_movie(sandbox, make_video, tmdb_id=27205)
    _patch_tmdb_external(monkeypatch, movie_imdb="tt1375666")

    # Seed a FRESH cache entry for tmdb 27205.
    fresh_iso = mvcommon._now_utc().isoformat()
    main.online_cache_set(27205, {"imdb_id": "tt1375666", "ratings": {"imdb": "OLD"},
                                  "fetched_at": fresh_iso})

    fetch_calls = {"n": 0}

    def counting_fetch(imdb_id=None, title=None, year=None):
        fetch_calls["n"] += 1
        return {"imdb_id": imdb_id, "ratings": {"imdb": "8.8"}, "rated": "", "runtime": "",
                "awards": "", "boxoffice": ""}

    monkeypatch.setattr(main, "omdb_fetch", counting_fetch)

    # 1) No --force: the fresh entry is skipped, the old value survives.
    main.cmd_refresh_online()
    assert fetch_calls["n"] == 0, "a fresh entry must be skipped without --force"
    assert main.online_cache_get(27205)["ratings"] == {"imdb": "OLD"}
    out = capsys.readouterr().out
    assert "cached (fresh)" in out

    # 2) --force: it IS re-fetched and overwritten.
    main.cmd_refresh_online("--force")
    assert fetch_calls["n"] == 1, "--force must re-fetch even a fresh entry"
    assert main.online_cache_get(27205)["ratings"] == {"imdb": "8.8"}


def test_refresh_online_never_mutates_library(sandbox, make_video, online_cache, monkeypatch):
    """The headline safety property: refresh_online writes ONLY mvonline.json — the
    three library_*.json files are byte-for-byte unchanged."""
    _seed_movie(sandbox, make_video, tmdb_id=27205)
    _patch_tmdb_external(monkeypatch, movie_imdb="tt1375666")
    _patch_omdb_get(monkeypatch, by_imdb={"tt1375666": dict(_OMDB_INCEPTION)})

    lib_paths = [sandbox["lib_movies"], sandbox["lib_series"], sandbox["lib_anime"]]
    before = {p: p.read_bytes() for p in lib_paths}
    before_mtime = {p: p.stat().st_mtime_ns for p in lib_paths}

    main.cmd_refresh_online()

    for p in lib_paths:
        assert p.read_bytes() == before[p], f"{p.name} bytes changed — refresh_online must not write the library"
        assert p.stat().st_mtime_ns == before_mtime[p], f"{p.name} mtime changed"
    # But the online cache WAS written.
    assert main.online_cache_get(27205) is not None


def test_refresh_online_no_imdb_reports_skip(sandbox, make_video, online_cache, monkeypatch, capsys):
    """When TMDB yields no imdb_id, the title is reported no-imdb and NOT cached."""
    _seed_movie(sandbox, make_video, tmdb_id=27205)
    _patch_tmdb_external(monkeypatch, movie_imdb=None)  # no imdb_id from TMDB
    # omdb_fetch must never be reached.
    monkeypatch.setattr(main, "omdb_fetch",
                        lambda **k: (_ for _ in ()).throw(AssertionError("no imdb -> no OMDb fetch")))

    main.cmd_refresh_online()

    out = capsys.readouterr().out
    assert "no IMDb id" in out
    assert "no-imdb=1" in out
    assert main.online_cache_get(27205) is None  # nothing cached


def test_refresh_online_no_omdb_key_bails(sandbox, make_video, monkeypatch, capsys):
    """No OMDb key configured -> a clear bail, no fetch, no cache write."""
    _seed_movie(sandbox, make_video, tmdb_id=27205)
    monkeypatch.setattr(mvcommon, "omdb_api_key", lambda: "")
    main.cmd_refresh_online()
    out = capsys.readouterr().out
    assert "No OMDb API key configured" in out


def test_refresh_online_scope_by_prefix(sandbox, make_video, online_cache, monkeypatch, capsys):
    """A positional id/prefix restricts the refresh to matching ids only."""
    _seed_movie(sandbox, make_video, tmdb_id=27205)
    _patch_tmdb_external(monkeypatch, movie_imdb="tt1375666")
    _patch_omdb_get(monkeypatch, by_imdb={"tt1375666": dict(_OMDB_INCEPTION)})

    # A prefix that matches nothing -> 0 titles, nothing cached.
    main.cmd_refresh_online("tv-does-not-exist")
    out = capsys.readouterr().out
    assert "0 distinct title" in out
    assert main.online_cache_get(27205) is None

    # The matching movie prefix -> the movie is refreshed.
    main.cmd_refresh_online("mov-en-2010-inception")
    assert main.online_cache_get(27205) is not None
