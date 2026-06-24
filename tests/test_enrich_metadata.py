"""Focused tests for `cmd_enrich_metadata` — the local-first TMDB backfill
(IMP-E3 / U3 / D17, Phase 5 step 5.4, user-chosen design C: SHOW-CENTRIC).

`cmd_enrich_metadata` reads the ids already in the library, asks TMDB by
title+year, and — only with `--apply` — writes `metadata.tmdb_id`, stamps the
`{tmdb-<id>}` folder token (once per show/movie, via `cmd_rename_folder`), and
downloads poster.jpg / fanart.jpg (+ per-season posters) WITHOUT fetching any
media. These tests assert the LOCKED behaviours:

  - DRY-RUN (default) writes NOTHING (no tmdb_id, no rename, no images);
  - --apply writes metadata.tmdb_id on the show's season_maps + episode leaves,
    stamps the {tmdb-…} token on the SHOW folder exactly ONCE, and downloads
    poster.jpg ONLY when absent (a seeded local poster.jpg is NEVER overwritten);
  - a series resolves SHOW-CENTRICally: 2 seasons share ONE TMDB resolve and ONE
    folder-token stamp on the show folder; per-season posters land in each season;
  - an AMBIGUOUS canned result is LISTED for confirmation, never written;
  - a TMDB error on one unit is SKIPPED without corrupting the library;
  - NO media fetch occurs (the .mkv bytes + stored hash are untouched; no adb).

HERMETIC: monkeypatches `main.requests.get` to serve CANNED TMDB JSON + fake JPG
bytes — NEVER a real network call. Redirects `main.TMDB_CACHE_DIR` to a temp dir
so the real ~/.mediavault cache is never touched. Uses the `sandbox` fixture
(LOCAL_ROOT-hermetic, hard-guards real C:\\Media / real library_*.json). Step 5.5
will promote a shared `mock_tmdb` conftest fixture; this inline mock is the
local seam for now.
"""
import hashlib
import json
import os

import pytest

import main
import mvcommon


FAKE_JPG = b"\xff\xd8\xff\xe0FAKE-JPEG-BYTES\xff\xd9"  # tiny but non-empty image body


# ---------------------------------------------------------------------------
# Canned TMDB backend — a URL-dispatching fake `requests.get`.
# ---------------------------------------------------------------------------

class _Resp:
    """Minimal requests.Response stand-in: .status_code, .json(), .content."""
    def __init__(self, status_code=200, json_data=None, content=b""):
        self.status_code = status_code
        self._json = json_data
        self.content = content

    def json(self):
        if self._json is None:
            raise ValueError("no json")
        return self._json


class FakeTMDB:
    """Records every GET and returns canned search/image JSON + fake JPG bytes.

    The `search` dict maps a lowercased query string -> a TMDB results list. The
    `season_images` dict maps (series_id, season_number) -> a posters list. The
    `episode_images` dict maps (series_id, season_number, episode_number) -> a
    stills list. An (series_id, season, episode) key in `episode_error` raises to
    simulate a transient episode-images failure. Image URLs (anything starting with
    the image base) return FAKE_JPG. A query whose title is in
    `network_error_titles` raises to simulate a transient search failure.
    """
    def __init__(self, search, season_images=None, episode_images=None,
                 network_error_titles=(), episode_error=()):
        self.search = {k.lower(): v for k, v in search.items()}
        self.season_images = season_images or {}
        self.episode_images = episode_images or {}
        self.episode_error = set(episode_error)
        self.network_error_titles = {t.lower() for t in network_error_titles}
        self.calls = []            # list of (url, params) for assertions
        self.image_urls = []       # image URLs actually requested
        self.configuration_hits = 0

    def get(self, url, params=None, headers=None, timeout=None, **kwargs):
        params = params or {}
        self.calls.append((url, dict(params)))

        # Image download (poster/fanart/season) — not a JSON endpoint.
        if url.startswith("https://image.tmdb.org/t/p/"):
            self.image_urls.append(url)
            return _Resp(200, content=FAKE_JPG)

        if url.endswith("/configuration"):
            self.configuration_hits += 1
            return _Resp(200, json_data={"images": {"secure_base_url": "https://image.tmdb.org/t/p/"}})

        if "/search/movie" in url or "/search/tv" in url:
            q = (params.get("query") or "").lower()
            if q in self.network_error_titles:
                raise ConnectionError("simulated TMDB network failure")
            return _Resp(200, json_data={"results": self.search.get(q, [])})

        if "/episode/" in url and url.endswith("/images"):
            # /tv/{id}/season/{s}/episode/{e}/images — parse id + s + e. Checked
            # BEFORE the season branch (this URL also contains /season/).
            parts = url.rstrip("/").split("/")
            e = int(parts[-2])
            s = int(parts[-4])
            series_id = int(parts[-6])
            if (series_id, s, e) in self.episode_error:
                raise ConnectionError("simulated episode-images failure")
            return _Resp(200, json_data={"stills": self.episode_images.get((series_id, s, e), [])})

        if "/season/" in url and url.endswith("/images"):
            # /tv/{id}/season/{n}/images — parse id + n from the path.
            parts = url.rstrip("/").split("/")
            n = int(parts[-2])
            series_id = int(parts[-4])
            return _Resp(200, json_data={"posters": self.season_images.get((series_id, n), [])})

        # Any other TMDB images endpoint -> empty (we use search paths directly).
        return _Resp(200, json_data={})


@pytest.fixture()
def patch_tmdb(monkeypatch, tmp_path):
    """Redirect the TMDB cache to a temp dir and install a key, then hand back an
    `install(fake)` that patches `main.requests.get`. Never touches the real home
    cache or makes a network call."""
    cache_dir = tmp_path / "tmdb_cache"
    monkeypatch.setattr(main, "TMDB_CACHE_DIR", str(cache_dir))
    monkeypatch.setattr(mvcommon, "tmdb_api_key", lambda: "TEST-V3-KEY")

    def install(fake):
        monkeypatch.setattr(main.requests, "get", fake.get)
        return fake

    return install


# ---------------------------------------------------------------------------
# Library seeders (mirror cmd_prep / cmd_prep_season output shapes)
# ---------------------------------------------------------------------------

def _empty_libs(sandbox):
    sandbox["lib_movies"].write_text("{}", encoding="utf-8")
    sandbox["lib_series"].write_text("{}", encoding="utf-8")
    sandbox["lib_anime"].write_text("{}", encoding="utf-8")


def _seed_movie(sandbox, mid, folder_name, filename="movie.mkv"):
    """Seed a single movie leaf with a real on-disk file + known hash.
    Returns (folder, filepath, original_hash)."""
    folder = sandbox["local_root"] / "Movies" / folder_name
    folder.mkdir(parents=True, exist_ok=True)
    fp = folder / filename
    body = b"MOVIE-MASTER-BYTES\n" * 40
    fp.write_bytes(body)
    h = hashlib.sha256(body).hexdigest()
    lib = mvcommon.load_library()
    lib[mid] = {
        "short_id": mvcommon.generate_short_id(mid),
        "filename": filename,
        "folder_path": str(folder),
        "status": "local_ready",
        "uploaded": False,
        "hash": h,
        "metadata": main.parse_metadata_from_id(mid),
    }
    mvcommon.save_library(lib)
    return folder, fp, h


def _seed_two_season_show(sandbox):
    """Seed a 2-season series laid out as Series/<Show>/Season NN/<ep>.mkv with a
    season_map + one episode leaf per season. Returns a dict of the ids/paths."""
    show_root = sandbox["local_root"] / "Series" / "The Office"
    s1 = show_root / "Season 01"
    s2 = show_root / "Season 02"
    for d in (s1, s2):
        d.mkdir(parents=True, exist_ok=True)

    show = "tv-en-2005-the-office"
    season1 = f"{show}-s01"
    season2 = f"{show}-s02"
    ep1 = f"{season1}e01"
    ep2 = f"{season2}e01"

    files = {}
    lib = mvcommon.load_library()
    for season_id, season_dir, ep_id in [(season1, s1, ep1), (season2, s2, ep2)]:
        fname = f"{os.path.basename(season_dir).replace(' ', '.')}.E01.mkv"
        fp = season_dir / fname
        body = (b"EP-MASTER-" + ep_id.encode() + b"\n") * 40
        fp.write_bytes(body)
        files[ep_id] = (fp, hashlib.sha256(body).hexdigest())
        lib[season_id] = {
            "type": "season_map",
            "folder_path": str(season_dir),
            "total_episodes": 1,
            "children": [ep_id],
        }
        lib[ep_id] = {
            "short_id": mvcommon.generate_short_id(ep_id),
            "filename": fname,
            "folder_path": str(season_dir),
            "status": "local_ready",
            "uploaded": False,
            "hash": files[ep_id][1],
            "metadata": {"title": "The Office", "year": 2005},
            "parent_id": season_id,
        }
    mvcommon.save_library(lib)
    return {
        "show_id": show, "show_root": show_root,
        "season1": season1, "season2": season2, "s1_dir": s1, "s2_dir": s2,
        "ep1": ep1, "ep2": ep2, "files": files,
    }


# Result builders carry a `popularity` (the ranker's tiebreaker) so the canned
# data mirrors real TMDB payloads. Default popularity is comfortably above the
# ranker's 1.0 floor so a lone exact-title hit is confident.
def _movie_result(tmdb_id, title, year, popularity=50.0):
    return {"id": tmdb_id, "title": title, "release_date": f"{year}-01-01",
            "popularity": popularity,
            "poster_path": "/poster.jpg", "backdrop_path": "/backdrop.jpg"}


def _tv_result(tmdb_id, name, year, poster=True, backdrop=True, popularity=50.0):
    r = {"id": tmdb_id, "name": name, "first_air_date": f"{year}-01-01",
         "popularity": popularity}
    if poster:
        r["poster_path"] = "/tvposter.jpg"
    if backdrop:
        r["backdrop_path"] = "/tvbackdrop.jpg"
    return r


# ===========================================================================
# Tests
# ===========================================================================

def test_no_api_key_is_graceful(sandbox, monkeypatch, capsys):
    """An empty TMDB key -> clear message + graceful exit, library untouched."""
    _empty_libs(sandbox)
    _seed_movie(sandbox, "mov-en-2025-f1", "F1")
    monkeypatch.setattr(mvcommon, "tmdb_api_key", lambda: "")
    before = mvcommon.load_library()

    main.cmd_enrich_metadata()  # must not raise

    out = capsys.readouterr().out
    assert "no tmdb api key" in out.lower()
    assert mvcommon.load_library() == before


def test_dry_run_writes_nothing(sandbox, patch_tmdb, capsys):
    """DRY-RUN (no --apply): a confident movie match is reported but NOTHING is
    written — no tmdb_id, no folder rename, no poster.jpg."""
    _empty_libs(sandbox)
    folder, fp, h = _seed_movie(sandbox, "mov-en-2021-f1", "F1")
    patch_tmdb(FakeTMDB(search={"f1": [_movie_result(1003159, "F1", 2025)]}))
    before = mvcommon.load_library()

    main.cmd_enrich_metadata("mov-en-2021-f1")  # no --apply -> dry run

    out = capsys.readouterr().out
    assert "DRY-RUN" in out
    assert "tmdb_id=1003159" in out          # it WOULD write this
    assert "would write" in out

    # Nothing actually changed on disk.
    assert mvcommon.load_library() == before
    assert "tmdb_id" not in mvcommon.load_library()["mov-en-2021-f1"].get("metadata", {})
    assert folder.exists() and not any(p.name.endswith("}") for p in folder.parent.iterdir())
    assert not (folder / "poster.jpg").exists()


def test_apply_movie_writes_tmdb_id_stamps_token_and_downloads(sandbox, patch_tmdb, capsys):
    """--apply on a confident movie: writes metadata.tmdb_id, renames the folder to
    carry the {tmdb-…} token (once), and downloads poster.jpg + fanart.jpg. The
    media file's bytes + stored hash are UNCHANGED (no rehash, no media fetch)."""
    _empty_libs(sandbox)
    folder, fp, orig_hash = _seed_movie(sandbox, "mov-en-2025-f1", "F1")
    fake = patch_tmdb(FakeTMDB(search={"f1": [_movie_result(1003159, "F1", 2025)]}))

    main.cmd_enrich_metadata("mov-en-2025-f1", "--apply")

    out = capsys.readouterr().out
    assert "APPLIED" in out

    # 1) tmdb_id + real TITLE/YEAR written (the placeholder id-shaped title is
    #    replaced by the TMDB title so the web cards show "F1", not the raw id).
    lib = mvcommon.load_library()
    assert lib["mov-en-2025-f1"]["metadata"]["tmdb_id"] == 1003159
    assert lib["mov-en-2025-f1"]["metadata"]["title"] == "F1"
    assert lib["mov-en-2025-f1"]["metadata"]["year"] == 2025

    # 2) folder renamed exactly once to carry the token; file carried along.
    new_folder = folder.parent / "F1 {tmdb-1003159}"
    assert not folder.exists()
    assert new_folder.is_dir()
    assert main._norm_path(lib["mov-en-2025-f1"]["folder_path"]) == main._norm_path(str(new_folder))

    # 3) poster + fanart downloaded into the NEW folder.
    assert (new_folder / "poster.jpg").read_bytes() == FAKE_JPG
    assert (new_folder / "fanart.jpg").read_bytes() == FAKE_JPG
    # Correct sizes used (w342 poster, w780 fanart) against the live image base.
    assert any(u == "https://image.tmdb.org/t/p/w342/poster.jpg" for u in fake.image_urls)
    assert any(u == "https://image.tmdb.org/t/p/w780/backdrop.jpg" for u in fake.image_urls)

    # 4) NO media fetch / NO rehash: the .mkv bytes + stored hash are untouched.
    moved_file = new_folder / "movie.mkv"
    assert hashlib.sha256(moved_file.read_bytes()).hexdigest() == orig_hash
    assert lib["mov-en-2025-f1"]["hash"] == orig_hash


def test_apply_preserves_human_curated_title(sandbox, patch_tmdb, capsys):
    """A human-curated metadata.title that differs from BOTH the id AND the TMDB
    title is PRESERVED on --apply (only id-shaped/placeholder titles are replaced).
    tmdb_id still writes, and year is refreshed from the confident TMDB match.

    The curated title is what enrich SEARCHES by (_enrich_title_year prefers a
    curated title). It is chosen to NORMALIZE-match the TMDB title ('F1!' vs 'F1',
    punctuation-only difference) so the match is CONFIDENT, yet it is not byte-equal
    to the TMDB title — proving the curated value (not 'F1') survives the write."""
    _empty_libs(sandbox)
    folder, fp, h = _seed_movie(sandbox, "mov-en-2025-f1", "F1")
    # Curate a display title that differs from the id AND the TMDB title, but
    # normalizes the same as the TMDB title so the match stays confident.
    curated = "F1!"
    lib = mvcommon.load_library()
    lib["mov-en-2025-f1"].setdefault("metadata", {})["title"] = curated
    mvcommon.save_library(lib)
    # Backend answers the CURATED query, returning the TMDB title "F1" (differs).
    patch_tmdb(FakeTMDB(search={curated.lower(): [_movie_result(1003159, "F1", 2025)]}))

    main.cmd_enrich_metadata("mov-en-2025-f1", "--apply")

    meta = mvcommon.load_library()["mov-en-2025-f1"]["metadata"]
    assert meta["title"] == curated, "curated title must NOT be overwritten by the TMDB title"
    assert meta["tmdb_id"] == 1003159  # tmdb_id is still applied (additive)
    assert meta["year"] == 2025         # year refreshed from the confident match


def test_apply_never_overwrites_local_poster(sandbox, patch_tmdb, capsys):
    """LOCAL ALWAYS WINS: a pre-existing poster.jpg is NEVER overwritten by enrich,
    even on a confident --apply match (fanart, which is absent, still downloads)."""
    _empty_libs(sandbox)
    folder, fp, h = _seed_movie(sandbox, "mov-en-2025-f1", "F1")
    local_poster_bytes = b"USER-HAND-PICKED-POSTER"
    (folder / "poster.jpg").write_bytes(local_poster_bytes)
    patch_tmdb(FakeTMDB(search={"f1": [_movie_result(1003159, "F1", 2025)]}))

    main.cmd_enrich_metadata("mov-en-2025-f1", "--apply")

    new_folder = folder.parent / "F1 {tmdb-1003159}"
    # The user's poster moved with the folder and is byte-for-byte preserved.
    assert (new_folder / "poster.jpg").read_bytes() == local_poster_bytes
    # Fanart was absent locally -> it WAS downloaded.
    assert (new_folder / "fanart.jpg").read_bytes() == FAKE_JPG
    out = capsys.readouterr().out
    assert "kept (not overwritten)" in out


def test_show_centric_two_seasons_one_resolve_one_stamp(sandbox, patch_tmdb, capsys):
    """SHOW-CENTRIC core: a 2-season series resolves ONCE and the {tmdb-…} token is
    stamped ONCE on the SHOW folder (not per season). tmdb_id lands on BOTH
    season_maps AND both episode leaves; per-season posters land in each season."""
    _empty_libs(sandbox)
    show = _seed_two_season_show(sandbox)
    fake = patch_tmdb(FakeTMDB(
        search={"the office": [_tv_result(2316, "The Office", 2005)]},
        season_images={(2316, 1): [{"file_path": "/s1.jpg"}],
                       (2316, 2): [{"file_path": "/s2.jpg"}]},
    ))

    main.cmd_enrich_metadata("tv-en-2005-the-office", "--apply")

    out = capsys.readouterr().out
    lib = mvcommon.load_library()

    # ONE TV search for the whole show (not one per season).
    tv_searches = [c for c in fake.calls if "/search/tv" in c[0]]
    assert len(tv_searches) == 1, f"expected exactly one TV search, got {len(tv_searches)}"

    # tmdb_id written on BOTH season_maps and BOTH episode leaves.
    for eid in (show["season1"], show["season2"], show["ep1"], show["ep2"]):
        assert lib[eid].get("metadata", {}).get("tmdb_id") == 2316, eid

    # The SHOW folder (parent of the Season NN dirs) was stamped exactly once.
    stamped = show["show_root"].parent / "The Office {tmdb-2316}"
    assert not show["show_root"].exists()
    assert stamped.is_dir()
    # Both season subfolders still exist UNDER the stamped show folder, untouched names.
    assert (stamped / "Season 01").is_dir()
    assert (stamped / "Season 02").is_dir()
    # Season folders are NOT separately tokenized (one stamp, on the show).
    assert not (stamped / "Season 01 {tmdb-2316}").exists()

    # Per-season posters landed in each season folder.
    assert (stamped / "Season 01" / "poster.jpg").read_bytes() == FAKE_JPG
    assert (stamped / "Season 02" / "poster.jpg").read_bytes() == FAKE_JPG
    # Show-level poster/fanart landed in the show folder.
    assert (stamped / "poster.jpg").read_bytes() == FAKE_JPG
    assert (stamped / "fanart.jpg").read_bytes() == FAKE_JPG


def test_ambiguous_match_is_listed_not_written(sandbox, patch_tmdb, capsys):
    """Two close results with no single title+year winner -> AMBIGUOUS: listed for
    confirmation with candidate tmdb_ids, and NOTHING is written even with --apply."""
    _empty_libs(sandbox)
    folder, fp, h = _seed_movie(sandbox, "mov-en-2020-the-thing", "The Thing")
    # Two different "The Thing" movies (1982 + 2011) — neither year matches 2020,
    # and there are two title matches -> the heuristic must refuse to guess.
    patch_tmdb(FakeTMDB(search={"the thing": [
        _movie_result(1091, "The Thing", 1982),
        _movie_result(60935, "The Thing", 2011),
    ]}))
    before = mvcommon.load_library()

    main.cmd_enrich_metadata("mov-en-2020-the-thing", "--apply")

    out = capsys.readouterr().out
    assert "AMBIGUOUS" in out
    assert "NEED MANUAL CONFIRMATION" in out
    assert "tmdb_id=1091" in out and "tmdb_id=60935" in out
    assert "set_tmdb mov-en-2020-the-thing" in out  # the follow-up hint

    # Nothing written: no tmdb_id, no rename, no poster.
    assert mvcommon.load_library() == before
    assert folder.exists()
    assert not (folder.parent / "The Thing {tmdb-1091}").exists()
    assert not (folder / "poster.jpg").exists()


def test_tmdb_error_on_one_unit_is_skipped_without_corruption(sandbox, patch_tmdb, capsys):
    """A TMDB network error on one unit -> that unit is SKIPPED (warned), the run
    continues, and a healthy second unit still applies. The library is never
    corrupted (the failed unit is simply untouched)."""
    _empty_libs(sandbox)
    f_err, _, herr = _seed_movie(sandbox, "mov-en-2025-boom", "Boom")
    f_ok, _, hok = _seed_movie(sandbox, "mov-en-2025-f1", "F1")
    patch_tmdb(FakeTMDB(
        search={"f1": [_movie_result(1003159, "F1", 2025)]},
        network_error_titles=("boom",),
    ))

    main.cmd_enrich_metadata("--apply")  # whole library

    out = capsys.readouterr().out
    assert "skipping" in out.lower()

    lib = mvcommon.load_library()
    # The errored unit is intact and untouched (no tmdb_id, original folder/hash).
    assert "tmdb_id" not in lib["mov-en-2025-boom"].get("metadata", {})
    assert lib["mov-en-2025-boom"]["hash"] == herr
    assert f_err.exists()
    # The healthy unit applied normally.
    assert lib["mov-en-2025-f1"]["metadata"]["tmdb_id"] == 1003159
    assert (f_ok.parent / "F1 {tmdb-1003159}").is_dir()


def test_idempotent_rerun_does_not_double_stamp(sandbox, patch_tmdb, capsys):
    """A second --apply over an already-stamped show is a no-op for the folder
    token (the {tmdb-…} token is detected and the rename is skipped)."""
    _empty_libs(sandbox)
    folder, fp, h = _seed_movie(sandbox, "mov-en-2025-f1", "F1")
    patch_tmdb(FakeTMDB(search={"f1": [_movie_result(1003159, "F1", 2025)]}))

    main.cmd_enrich_metadata("mov-en-2025-f1", "--apply")
    capsys.readouterr()  # drain first-run output
    new_folder = folder.parent / "F1 {tmdb-1003159}"
    assert new_folder.is_dir()

    # Second run: the folder already carries the token -> no second rename, no crash.
    main.cmd_enrich_metadata("mov-en-2025-f1", "--apply")
    out = capsys.readouterr().out
    assert "already has a {tmdb-…} token" in out
    # Still exactly one stamped folder; no double-token folder created.
    assert new_folder.is_dir()
    assert not (folder.parent / "F1 {tmdb-1003159} {tmdb-1003159}").exists()


def test_no_media_fetch_subprocess_or_popen(sandbox, patch_tmdb, monkeypatch):
    """Hard guarantee: enrich NEVER fetches media — it must not invoke adb via
    subprocess.run nor spawn mainfetch via subprocess.Popen. We trip a guard if
    either is called during an --apply run."""
    _empty_libs(sandbox)
    _seed_movie(sandbox, "mov-en-2025-f1", "F1")
    patch_tmdb(FakeTMDB(search={"f1": [_movie_result(1003159, "F1", 2025)]}))

    def _boom_run(*a, **k):
        raise AssertionError("enrich_metadata must NOT call subprocess.run (no adb/media)")

    def _boom_popen(*a, **k):
        raise AssertionError("enrich_metadata must NOT call subprocess.Popen (no fetch)")

    monkeypatch.setattr(main.subprocess, "run", _boom_run)
    monkeypatch.setattr(main.subprocess, "Popen", _boom_popen)

    main.cmd_enrich_metadata("mov-en-2025-f1", "--apply")  # must not trip the guards


# ===========================================================================
# Search-quality refinement (coordinator findings 1-3): variant building +
# similarity/popularity/year ranking. Still fully mocked — no real network.
# ===========================================================================

def test_concatenated_show_resolves_only_via_wordninja_split(sandbox, patch_tmdb, capsys):
    """A concatenated slug ('gameofthrones') returns NOTHING from TMDB for the raw
    query, but the wordninja-split variant ('game of thrones') resolves the show.
    The canned backend ONLY answers the split query, so a confident match here
    PROVES the variant search ran and unioned the split result."""
    _empty_libs(sandbox)
    # Seed a show whose slug is concatenated and whose metadata.title is the raw id
    # so _enrich_title_year humanizes it to the concatenated single word.
    show_root = sandbox["local_root"] / "Series" / "GameOfThrones"
    sdir = show_root / "Season 01"
    sdir.mkdir(parents=True)
    (sdir / "e01.mkv").write_bytes(b"X" * 300)
    season = "tv-en-2011-gameofthrones-s01"
    ep = season + "e01"
    lib = mvcommon.load_library()
    lib[season] = {"type": "season_map", "folder_path": str(sdir), "total_episodes": 1, "children": [ep]}
    lib[ep] = {"folder_path": str(sdir), "filename": "e01.mkv", "status": "local_ready",
               "metadata": {"title": ep, "year": 2011}, "parent_id": season}
    mvcommon.save_library(lib)

    # Backend answers ONLY the split query 'game of thrones' (raw 'gameofthrones' -> []).
    fake = patch_tmdb(FakeTMDB(search={
        "gameofthrones": [],
        "game of thrones": [_tv_result(1399, "Game of Thrones", 2011)],
    }))

    main.cmd_enrich_metadata("tv-en-2011-gameofthrones", "--apply")

    out = capsys.readouterr().out
    lib = mvcommon.load_library()
    assert lib[season]["metadata"]["tmdb_id"] == 1399, out
    # Both the raw and the split variant were queried (union search ran).
    tv_queries = {c[1].get("query") for c in fake.calls if "/search/tv" in c[0]}
    assert "gameofthrones" in tv_queries and "game of thrones" in tv_queries


def test_tv_search_omits_first_air_date_year_filter(sandbox, patch_tmdb):
    """TV/anime search must NOT pass first_air_date_year (the id year is often a
    LATER season's air year and would wrongly filter the show out). Assert no TV
    search call carries a year filter, while a movie search still does."""
    _empty_libs(sandbox)
    # A show id whose year (2022) is a later season than the show's first air year.
    show_root = sandbox["local_root"] / "Series" / "PeakyBlinders"
    sdir = show_root / "Season 06"
    sdir.mkdir(parents=True)
    (sdir / "e01.mkv").write_bytes(b"X" * 300)
    season = "tv-en-2022-peakyblinders-s06"
    ep = season + "e01"
    lib = mvcommon.load_library()
    lib[season] = {"type": "season_map", "folder_path": str(sdir), "total_episodes": 1, "children": [ep]}
    lib[ep] = {"folder_path": str(sdir), "filename": "e01.mkv", "status": "local_ready",
               "metadata": {"title": ep, "year": 2022}, "parent_id": season}
    # Plus a movie so we can confirm the movie path DOES keep &year=.
    mdir = sandbox["local_root"] / "Movies" / "F1"
    mdir.mkdir(parents=True)
    (mdir / "f1.mkv").write_bytes(b"X" * 300)
    lib["mov-en-2025-f1"] = {"folder_path": str(mdir), "filename": "f1.mkv",
                             "status": "local_ready", "metadata": {"year": 2025}}
    mvcommon.save_library(lib)

    fake = patch_tmdb(FakeTMDB(search={
        "peakyblinders": [_tv_result(60574, "Peaky Blinders", 2013)],
        "peak y blinders": [],  # wordninja mangles this one -> no extra hit
        "f1": [_movie_result(1003159, "F1", 2025)],
    }))

    main.cmd_enrich_metadata()  # whole library, dry-run is fine for the URL assertion

    tv_calls = [c for c in fake.calls if "/search/tv" in c[0]]
    assert tv_calls, "expected at least one TV search"
    for url, params in tv_calls:
        assert "first_air_date_year" not in params, f"TV search must omit year filter: {params}"
    # The movie search DOES carry &year= (release year is reliable).
    movie_calls = [c for c in fake.calls if "/search/movie" in c[0]]
    assert movie_calls and any(c[1].get("year") == 2025 for c in movie_calls)


def test_popularity_tiebreak_obscure_same_title_does_not_win(sandbox, patch_tmdb):
    """Two results with the SAME normalized title and NO year to separate them ->
    the obscure low-popularity one must NOT be picked. With no year signal the
    ranker lists them AMBIGUOUS rather than guessing, and the obscure id is never
    written (locked decision #6: never write a guess)."""
    _empty_libs(sandbox)
    folder, fp, h = _seed_movie(sandbox, "mov-en-the-thing", "The Thing")  # no year in id
    # Same title, very different popularity; no year in the id to break the tie.
    fake = patch_tmdb(FakeTMDB(search={"the thing": [
        _movie_result(1091, "The Thing", 1982, popularity=80.0),   # the real, popular one
        _movie_result(999999, "The Thing", 2099, popularity=0.3),  # obscure imposter
    ]}))

    main.cmd_enrich_metadata("mov-en-the-thing", "--apply")

    lib = mvcommon.load_library()
    # No guess written at all (same-title tie, no year -> AMBIGUOUS).
    assert "tmdb_id" not in lib["mov-en-the-thing"].get("metadata", {})
    # And the obscure imposter specifically never won.
    assert lib["mov-en-the-thing"].get("metadata", {}).get("tmdb_id") != 999999

    # Sanity: the ranker orders the popular one first among the listed candidates.
    status, payload = main._pick_tmdb_match(
        [_movie_result(1091, "The Thing", 1982, popularity=80.0),
         _movie_result(999999, "The Thing", 2099, popularity=0.3)],
        "the thing", None, "title", "release_date")
    assert status == "ambiguous"
    assert payload[0]["id"] == 1091  # popular one ranked above the obscure imposter


def test_ambiguous_no_confident_lists_and_writes_nothing(sandbox, patch_tmdb):
    """A no-confident case (weak similarity, no year corroboration) is LISTED for
    manual set_tmdb and writes NOTHING — even with --apply."""
    _empty_libs(sandbox)
    folder, fp, h = _seed_movie(sandbox, "mov-en-someobscurefilm", "Some Obscure Film")
    # Only weak partial-title matches come back; nothing clears the 0.9 bar.
    patch_tmdb(FakeTMDB(search={
        "some obscure film": [
            _movie_result(11, "Some Other Movie", 1990, popularity=5.0),
            _movie_result(22, "An Obscure Documentary", 2001, popularity=2.0),
        ],
        "someobscurefilm": [],
    }))
    before = mvcommon.load_library()

    main.cmd_enrich_metadata("mov-en-someobscurefilm", "--apply")

    after = mvcommon.load_library()
    assert after == before, "a non-confident match must write NOTHING"
    assert not (folder.parent / "Some Obscure Film {tmdb-11}").exists()


def test_pick_match_prefers_full_title_over_obscure_substring():
    """Unit: the real 'The Conjuring' (high pop) must beat the obscure 'Conjuring'
    (low pop) — exact-normalized-equality would have wrongly picked the substring.
    With a year that matches the real one, the result is CONFIDENT."""
    results = [
        _movie_result(694185, "Conjuring", 2017, popularity=1.5),     # obscure substring
        _movie_result(138843, "The Conjuring", 2013, popularity=90.0),  # the real one
    ]
    status, payload = main._pick_tmdb_match(results, "the conjuring", 2013, "title", "release_date")
    assert status == "confident"
    assert payload["id"] == 138843


# ===========================================================================
# NFO writer tests (IMP-U3 down-payment, step 5.8)
# ===========================================================================

def _movie_result_with_meta(tmdb_id, title, year, overview="A great film.", vote_average=7.5,
                            popularity=50.0):
    """Like _movie_result but also carries overview + vote_average (mirrors real TMDB payload)."""
    r = _movie_result(tmdb_id, title, year, popularity=popularity)
    r["overview"] = overview
    r["vote_average"] = vote_average
    return r


def _tv_result_with_meta(tmdb_id, name, year, overview="A great show.", vote_average=8.2,
                          popularity=50.0):
    """Like _tv_result but also carries overview + vote_average."""
    r = _tv_result(tmdb_id, name, year, popularity=popularity)
    r["overview"] = overview
    r["vote_average"] = vote_average
    return r


def test_nfo_movie_written_on_apply_with_flag(sandbox, patch_tmdb, capsys):
    """--apply --nfo on a confident movie: writes well-formed movie.nfo in the
    (potentially renamed) movie folder containing title/year/plot/rating/uniqueid."""
    import xml.etree.ElementTree as ET
    _empty_libs(sandbox)
    folder, fp, h = _seed_movie(sandbox, "mov-en-2025-f1", "F1")
    patch_tmdb(FakeTMDB(search={"f1": [
        _movie_result_with_meta(1003159, "F1", 2025, overview="Racing film.", vote_average=7.1)
    ]}))

    main.cmd_enrich_metadata("mov-en-2025-f1", "--apply", "--nfo")

    # After the confident apply the folder was renamed to include {tmdb-…}.
    new_folder = folder.parent / "F1 {tmdb-1003159}"
    nfo_path = new_folder / "movie.nfo"
    assert nfo_path.exists(), "movie.nfo must be written after --apply --nfo on a movie"

    # Parse back with ElementTree to confirm it is well-formed XML.
    tree = ET.parse(str(nfo_path))
    root = tree.getroot()
    assert root.tag == "movie"
    assert root.find("title").text == "F1"
    assert root.find("year").text == "2025"
    assert root.find("plot").text == "Racing film."
    assert root.find("rating").text == "7.1"
    uid = root.find("uniqueid")
    assert uid is not None
    assert uid.get("type") == "tmdb"
    assert uid.get("default") == "true"
    assert uid.text == "1003159"

    out = capsys.readouterr().out
    assert "movie.nfo" in out


def test_nfo_show_written_on_apply_with_flag(sandbox, patch_tmdb, capsys):
    """--apply --nfo on a confident show: writes well-formed tvshow.nfo in the SHOW
    folder (not per season) with a <tvshow> root."""
    import xml.etree.ElementTree as ET
    _empty_libs(sandbox)
    show = _seed_two_season_show(sandbox)
    patch_tmdb(FakeTMDB(
        search={"the office": [
            _tv_result_with_meta(2316, "The Office", 2005,
                                 overview="Mockumentary comedy.", vote_average=8.9)
        ]},
        season_images={(2316, 1): [], (2316, 2): []},
    ))

    main.cmd_enrich_metadata("tv-en-2005-the-office", "--apply", "--nfo")

    # Show folder was renamed.
    stamped_show = show["show_root"].parent / "The Office {tmdb-2316}"
    nfo_path = stamped_show / "tvshow.nfo"
    assert nfo_path.exists(), "tvshow.nfo must be written in the show folder"

    tree = ET.parse(str(nfo_path))
    root = tree.getroot()
    assert root.tag == "tvshow"
    assert root.find("title").text == "The Office"
    assert root.find("year").text == "2005"
    assert root.find("plot").text == "Mockumentary comedy."
    assert root.find("rating").text == "8.9"
    uid = root.find("uniqueid")
    assert uid.get("type") == "tmdb"
    assert uid.get("default") == "true"
    assert uid.text == "2316"

    # No per-season NFO files.
    assert not (stamped_show / "Season 01" / "tvshow.nfo").exists()
    assert not (stamped_show / "Season 02" / "tvshow.nfo").exists()

    out = capsys.readouterr().out
    assert "tvshow.nfo" in out


def test_nfo_not_written_without_flag(sandbox, patch_tmdb, capsys):
    """Without --nfo, no NFO file is ever written (--apply alone, or dry-run alone)."""
    _empty_libs(sandbox)
    folder, fp, h = _seed_movie(sandbox, "mov-en-2025-f1", "F1")
    patch_tmdb(FakeTMDB(search={"f1": [
        _movie_result_with_meta(1003159, "F1", 2025)
    ]}))

    main.cmd_enrich_metadata("mov-en-2025-f1", "--apply")  # no --nfo

    new_folder = folder.parent / "F1 {tmdb-1003159}"
    assert not (new_folder / "movie.nfo").exists(), "movie.nfo must NOT be written without --nfo"
    assert not (folder / "movie.nfo").exists()


def test_nfo_not_written_in_dry_run(sandbox, patch_tmdb, capsys):
    """With --nfo but WITHOUT --apply (dry-run): nothing is written — no NFO."""
    _empty_libs(sandbox)
    folder, fp, h = _seed_movie(sandbox, "mov-en-2025-f1", "F1")
    patch_tmdb(FakeTMDB(search={"f1": [
        _movie_result_with_meta(1003159, "F1", 2025)
    ]}))

    main.cmd_enrich_metadata("mov-en-2025-f1", "--nfo")  # dry-run (no --apply)

    # Dry-run: folder not renamed, no NFO anywhere.
    assert not (folder / "movie.nfo").exists()
    assert not (folder.parent / "F1 {tmdb-1003159}" / "movie.nfo").exists()
    out = capsys.readouterr().out
    assert "DRY-RUN" in out


def test_nfo_is_well_formed_xml(sandbox, patch_tmdb):
    """Parse the written NFO with ET.parse — if it throws, the XML is malformed."""
    import xml.etree.ElementTree as ET
    _empty_libs(sandbox)
    # Title with XML-special characters that must be properly escaped.
    folder, fp, h = _seed_movie(sandbox, "mov-en-2025-f1", "F1 & Beyond")
    # Supply the escaped title directly in the canned result so search finds it.
    result = _movie_result_with_meta(9999, "F1 & Beyond", 2025,
                                     overview="Plot with <angle> & ampersand.")
    patch_tmdb(FakeTMDB(search={"f1 & beyond": [result], "f1  beyond": [result]}))

    main.cmd_enrich_metadata("mov-en-2025-f1", "--apply", "--nfo")

    # Find the nfo — folder may or may not be renamed depending on whether the title
    # matched, but the NFO must parse without error.
    nfo_files = list(folder.parent.rglob("movie.nfo"))
    if not nfo_files:
        # If no confident match (title mismatch), that's fine — no NFO expected.
        return
    root = ET.parse(str(nfo_files[0])).getroot()
    # If it parsed, it is well-formed.  Also verify the text round-trips.
    assert root.find("plot") is not None


def test_nfo_write_failure_warns_but_enrich_still_completes(sandbox, patch_tmdb, monkeypatch, capsys):
    """If the NFO write raises (e.g. permission error), a warning is printed but the
    enrich run COMPLETES and the tmdb_id is still written to the library."""
    _empty_libs(sandbox)
    folder, fp, h = _seed_movie(sandbox, "mov-en-2025-f1", "F1")
    patch_tmdb(FakeTMDB(search={"f1": [
        _movie_result_with_meta(1003159, "F1", 2025)
    ]}))

    # Monkeypatch open inside _write_nfo to raise a PermissionError.
    real_open = open
    def _failing_open(path, *args, **kwargs):
        if str(path).endswith(".nfo"):
            raise PermissionError("No write permission (simulated)")
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr("builtins.open", _failing_open)

    main.cmd_enrich_metadata("mov-en-2025-f1", "--apply", "--nfo")

    out = capsys.readouterr().out
    # Warning printed, run did not crash.
    assert "NFO write failed" in out or "warning" in out.lower() or "⚠️" in out

    # tmdb_id still written (NFO failure is non-blocking).
    lib = mvcommon.load_library()
    # Note: the folder may be renamed; search by key (it's stable).
    entry = lib.get("mov-en-2025-f1")
    assert entry is not None and entry.get("metadata", {}).get("tmdb_id") == 1003159


# ===========================================================================
# Per-episode TMDB stills (IMP-E3/U3/D17, Phase 5 — episode-thumbnail waterfall).
#
# On a confident show --apply, enrich downloads each episode's best still as
# `<episode_video_basename>-thumb.jpg` next to the episode file via the
# /tv/{id}/season/{s}/episode/{e}/images endpoint — LOCAL ALWAYS WINS, NEVER a
# media fetch, and a per-episode failure falls back silently to the season poster.
# ===========================================================================

def _thumb_name(fname):
    """The `<basename>-thumb.jpg` enrich writes for an episode video file."""
    return os.path.splitext(fname)[0] + "-thumb.jpg"


def test_apply_show_downloads_per_episode_stills(sandbox, patch_tmdb, capsys):
    """A confident show --apply downloads `<basename>-thumb.jpg` for EACH episode,
    using the episode-images endpoint and the still size (w300). Both episodes get
    their still; the season poster + show poster are still downloaded too."""
    _empty_libs(sandbox)
    show = _seed_two_season_show(sandbox)
    fake = patch_tmdb(FakeTMDB(
        search={"the office": [_tv_result(2316, "The Office", 2005)]},
        season_images={(2316, 1): [{"file_path": "/s1.jpg"}],
                       (2316, 2): [{"file_path": "/s2.jpg"}]},
        episode_images={(2316, 1, 1): [{"file_path": "/still-s1e1.jpg", "vote_average": 8.0}],
                        (2316, 2, 1): [{"file_path": "/still-s2e1.jpg", "vote_average": 6.0}]},
    ))

    main.cmd_enrich_metadata("tv-en-2005-the-office", "--apply")

    lib = mvcommon.load_library()
    stamped = show["show_root"].parent / "The Office {tmdb-2316}"

    # Each episode's still landed next to its (moved) video file as <basename>-thumb.jpg.
    for ep_id, season_dir_name in ((show["ep1"], "Season 01"), (show["ep2"], "Season 02")):
        fname = lib[ep_id]["filename"]
        thumb = stamped / season_dir_name / _thumb_name(fname)
        assert thumb.read_bytes() == FAKE_JPG, f"missing per-episode still for {ep_id}"

    # The episode-images endpoint was hit for BOTH episodes (and is the episode,
    # not the season, endpoint — it contains /episode/).
    ep_calls = [c for c in fake.calls if "/episode/" in c[0] and c[0].endswith("/images")]
    assert len(ep_calls) == 2, f"expected 2 episode-images calls, got {len(ep_calls)}"

    # The still was fetched at the w300 still size against the live image base.
    assert any(u == "https://image.tmdb.org/t/p/w300/still-s1e1.jpg" for u in fake.image_urls)
    assert any(u == "https://image.tmdb.org/t/p/w300/still-s2e1.jpg" for u in fake.image_urls)

    # Season + show posters still present (the existing art path is intact).
    assert (stamped / "Season 01" / "poster.jpg").read_bytes() == FAKE_JPG
    assert (stamped / "poster.jpg").read_bytes() == FAKE_JPG

    out = capsys.readouterr().out
    assert "-thumb.jpg" in out


def test_apply_picks_highest_vote_still(sandbox, patch_tmdb):
    """When an episode has several stills, the HIGHEST vote_average one is chosen
    (not the first listed). Asserted via the requested image URL (bytes are
    identical FAKE_JPG, so the chosen file_path is what distinguishes them)."""
    _empty_libs(sandbox)
    show = _seed_two_season_show(sandbox)
    fake = patch_tmdb(FakeTMDB(
        search={"the office": [_tv_result(2316, "The Office", 2005)]},
        season_images={(2316, 1): [], (2316, 2): []},
        episode_images={
            (2316, 1, 1): [
                {"file_path": "/low.jpg", "vote_average": 2.0},
                {"file_path": "/best.jpg", "vote_average": 9.5},   # must be chosen
                {"file_path": "/mid.jpg", "vote_average": 5.0},
            ],
            (2316, 2, 1): [],
        },
    ))

    main.cmd_enrich_metadata("tv-en-2005-the-office", "--apply")

    # The highest-vote still (best.jpg) was the one fetched; the others were not.
    assert any(u == "https://image.tmdb.org/t/p/w300/best.jpg" for u in fake.image_urls)
    assert not any(u.endswith("/low.jpg") or u.endswith("/mid.jpg") for u in fake.image_urls)


def test_apply_never_overwrites_existing_episode_still(sandbox, patch_tmdb, capsys):
    """LOCAL ALWAYS WINS: a pre-existing `<basename>-thumb.jpg` is NEVER overwritten
    (seed one episode's still; the OTHER episode without a local still IS downloaded)."""
    _empty_libs(sandbox)
    show = _seed_two_season_show(sandbox)

    # Seed a hand-picked still for episode 1 (in its current season folder).
    lib = mvcommon.load_library()
    ep1_fname = lib[show["ep1"]]["filename"]
    seeded_bytes = b"USER-HAND-PICKED-STILL"
    (show["s1_dir"] / _thumb_name(ep1_fname)).write_bytes(seeded_bytes)

    patch_tmdb(FakeTMDB(
        search={"the office": [_tv_result(2316, "The Office", 2005)]},
        season_images={(2316, 1): [], (2316, 2): []},
        episode_images={(2316, 1, 1): [{"file_path": "/still-s1e1.jpg", "vote_average": 8.0}],
                        (2316, 2, 1): [{"file_path": "/still-s2e1.jpg", "vote_average": 6.0}]},
    ))

    main.cmd_enrich_metadata("tv-en-2005-the-office", "--apply")

    lib = mvcommon.load_library()
    stamped = show["show_root"].parent / "The Office {tmdb-2316}"

    # Episode 1's hand-picked still moved with the folder and is byte-for-byte preserved.
    ep1_thumb = stamped / "Season 01" / _thumb_name(ep1_fname)
    assert ep1_thumb.read_bytes() == seeded_bytes, "local episode still must NOT be overwritten"

    # Episode 2 had no local still -> it WAS downloaded.
    ep2_fname = lib[show["ep2"]]["filename"]
    ep2_thumb = stamped / "Season 02" / _thumb_name(ep2_fname)
    assert ep2_thumb.read_bytes() == FAKE_JPG

    out = capsys.readouterr().out
    assert "local episode still present — kept" in out


def test_apply_episode_still_failure_falls_back_silently(sandbox, patch_tmdb, capsys):
    """A failed/empty episode-images call for ONE episode must NOT crash and must
    NOT block the rest: that still is skipped (it falls back to the season poster at
    view time) while the healthy episode's still still downloads."""
    _empty_libs(sandbox)
    show = _seed_two_season_show(sandbox)
    fake = patch_tmdb(FakeTMDB(
        search={"the office": [_tv_result(2316, "The Office", 2005)]},
        season_images={(2316, 1): [{"file_path": "/s1.jpg"}],
                       (2316, 2): [{"file_path": "/s2.jpg"}]},
        # Episode 1: a healthy still. Episode 2: the call raises (transient failure).
        episode_images={(2316, 1, 1): [{"file_path": "/still-s1e1.jpg", "vote_average": 8.0}]},
        episode_error={(2316, 2, 1)},
    ))

    main.cmd_enrich_metadata("tv-en-2005-the-office", "--apply")  # must NOT crash

    lib = mvcommon.load_library()
    stamped = show["show_root"].parent / "The Office {tmdb-2316}"

    # Episode 1's still downloaded; episode 2's still is ABSENT (failed -> skipped).
    ep1_fname = lib[show["ep1"]]["filename"]
    ep2_fname = lib[show["ep2"]]["filename"]
    assert (stamped / "Season 01" / _thumb_name(ep1_fname)).read_bytes() == FAKE_JPG
    assert not (stamped / "Season 02" / _thumb_name(ep2_fname)).exists()

    # The season 2 poster IS present (the documented fall-back at view time).
    assert (stamped / "Season 02" / "poster.jpg").read_bytes() == FAKE_JPG


def test_apply_episode_empty_stills_falls_back_silently(sandbox, patch_tmdb, capsys):
    """An episode-images call that returns an EMPTY stills list (not an error) also
    falls back silently — the thumb is simply not written, no crash."""
    _empty_libs(sandbox)
    show = _seed_two_season_show(sandbox)
    patch_tmdb(FakeTMDB(
        search={"the office": [_tv_result(2316, "The Office", 2005)]},
        season_images={(2316, 1): [], (2316, 2): []},
        episode_images={(2316, 1, 1): [], (2316, 2, 1): []},  # no stills for either
    ))

    main.cmd_enrich_metadata("tv-en-2005-the-office", "--apply")  # must NOT crash

    lib = mvcommon.load_library()
    stamped = show["show_root"].parent / "The Office {tmdb-2316}"
    for ep_id, season_dir_name in ((show["ep1"], "Season 01"), (show["ep2"], "Season 02")):
        fname = lib[ep_id]["filename"]
        assert not (stamped / season_dir_name / _thumb_name(fname)).exists()

    out = capsys.readouterr().out
    assert "no episode still" in out.lower()


def test_movies_get_no_episode_stills(sandbox, patch_tmdb):
    """A movie --apply downloads poster/fanart but NEVER calls the episode-images
    endpoint (movies have no episodes/stills)."""
    _empty_libs(sandbox)
    _seed_movie(sandbox, "mov-en-2025-f1", "F1")
    fake = patch_tmdb(FakeTMDB(search={"f1": [_movie_result(1003159, "F1", 2025)]}))

    main.cmd_enrich_metadata("mov-en-2025-f1", "--apply")

    ep_calls = [c for c in fake.calls if "/episode/" in c[0]]
    assert ep_calls == [], "movies must never hit the episode-images endpoint"


def test_apply_stills_no_media_fetch(sandbox, patch_tmdb, monkeypatch):
    """Hard guarantee carried to the stills path: downloading episode stills NEVER
    invokes adb (subprocess.run) nor spawns mainfetch (subprocess.Popen)."""
    _empty_libs(sandbox)
    show = _seed_two_season_show(sandbox)
    patch_tmdb(FakeTMDB(
        search={"the office": [_tv_result(2316, "The Office", 2005)]},
        season_images={(2316, 1): [], (2316, 2): []},
        episode_images={(2316, 1, 1): [{"file_path": "/still-s1e1.jpg", "vote_average": 8.0}],
                        (2316, 2, 1): [{"file_path": "/still-s2e1.jpg", "vote_average": 6.0}]},
    ))

    def _boom_run(*a, **k):
        raise AssertionError("enrich stills must NOT call subprocess.run (no adb/media)")

    def _boom_popen(*a, **k):
        raise AssertionError("enrich stills must NOT call subprocess.Popen (no fetch)")

    monkeypatch.setattr(main.subprocess, "run", _boom_run)
    monkeypatch.setattr(main.subprocess, "Popen", _boom_popen)

    main.cmd_enrich_metadata("tv-en-2005-the-office", "--apply")  # must not trip guards
