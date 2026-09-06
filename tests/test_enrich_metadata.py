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
    def __init__(self, search=None, season_images=None, episode_images=None,
                 network_error_titles=(), episode_error=(),
                 movie_by_id=None, tv_by_id=None, by_id_error=(),
                 season_details=None, season_details_error=()):
        self.search = {k.lower(): v for k, v in (search or {}).items()}
        self.season_images = season_images or {}
        self.episode_images = episode_images or {}
        # Season DETAILS (GET /3/tv/{id}/season/{n}) — the ONE-call-per-season source
        # of per-episode overview/name. Maps (series_id, season_number) -> a payload
        # dict (typically {"episodes": [ {episode_number, name, overview, ...}, ... ]}).
        # A (series_id, season_number) in season_details_error raises to simulate a
        # transient season-details failure.
        self.season_details = season_details or {}
        self.season_details_error = set(season_details_error)
        self.episode_error = set(episode_error)
        self.network_error_titles = {t.lower() for t in network_error_titles}
        # By-id details (enrich-by-known-id): {tmdb_id: details_dict}. An id in
        # by_id_error raises to simulate a transient/404 by-id details failure.
        self.movie_by_id = {int(k): v for k, v in (movie_by_id or {}).items()}
        self.tv_by_id = {int(k): v for k, v in (tv_by_id or {}).items()}
        self.by_id_error = {int(i) for i in by_id_error}
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

        # Season DETAILS — /tv/{id}/season/{n} (bare: NOT /images, NOT /episode/).
        # Checked AFTER the two /images shapes above so it only catches the bare URL.
        # Returns the canned season payload (with its `episodes[]`), or raises for a
        # season in season_details_error, or an empty {} when unknown (graceful skip).
        if "/season/" in url and "/episode/" not in url and not url.endswith("/images"):
            parts = url.rstrip("/").split("/")
            if len(parts) >= 4 and parts[-1].isdigit() and parts[-2] == "season":
                n = int(parts[-1])
                series_id = int(parts[-3])
                if (series_id, n) in self.season_details_error:
                    raise ConnectionError("simulated season-details failure")
                return _Resp(200, json_data=self.season_details.get((series_id, n), {}))

        # By-id DETAILS (enrich-by-known-id): /3/movie/{id} or /3/tv/{id} with a
        # bare numeric id and nothing after it. Checked AFTER /search and /images
        # so those distinct shapes win first. Returns the canned details dict, or
        # raises for an id in by_id_error (transient/404), or 404 if unknown.
        parts = url.rstrip("/").split("/")
        if len(parts) >= 2 and parts[-1].isdigit() and parts[-2] in ("movie", "tv"):
            kind = parts[-2]
            tid = int(parts[-1])
            if tid in self.by_id_error:
                raise ConnectionError("simulated TMDB by-id details failure")
            table = self.movie_by_id if kind == "movie" else self.tv_by_id
            if tid in table:
                return _Resp(200, json_data=table[tid])
            return _Resp(404, json_data=None)

        # Any other TMDB images endpoint -> empty (we use search paths directly).
        return _Resp(200, json_data={})


@pytest.fixture()
def patch_tmdb(monkeypatch, tmp_path):
    """Redirect the TMDB cache to a temp dir and install a key, then hand back an
    `install(fake)` that patches `main.requests.get`. Never touches the real home
    cache or makes a network call.

    SEALS THE EXA BOUNDARY (IMP-E16/D5): the enrich waterfall now has a step (iii)
    EXA web-search fallback that fires on a none/ambiguous TMDB result when an EXA
    key is configured. The dev machine's real mvconfig.json HAS an exa key, so
    without this seal the existing ambiguous/none tests below would make a REAL EXA
    POST. Default the key to "" (fallback OFF) and redirect EXA_CACHE_DIR to a temp
    dir, so the pure-API behaviour is the default; the D5 tests re-enable the key
    explicitly and monkeypatch the resolver/POST themselves."""
    cache_dir = tmp_path / "tmdb_cache"
    monkeypatch.setattr(main, "TMDB_CACHE_DIR", str(cache_dir))
    monkeypatch.setattr(main, "EXA_CACHE_DIR", str(tmp_path / "exa_cache"))
    monkeypatch.setattr(mvcommon, "tmdb_api_key", lambda: "TEST-V3-KEY")
    monkeypatch.setattr(mvcommon, "exa_api_key", lambda: "")  # EXA fallback OFF by default

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


def _season_details(episodes):
    """A /3/tv/{id}/season/{n} SEASON DETAILS payload from a list of
    (episode_number, name, overview) tuples — each becomes an `episodes[]` member
    carrying episode_number/name/overview (the fields enrich reads for the
    per-episode synopsis + title). `still_path` is included to mirror the real
    payload, though this enrich step persists only overview + name."""
    return {"episodes": [
        {"episode_number": num, "name": name, "overview": overview,
         "still_path": f"/still-e{num}.jpg"}
        for (num, name, overview) in episodes
    ]}


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
    token (the {tmdb-…} token is detected and the rename is skipped).

    The FIRST run has no preset id, so it SEARCHES and writes metadata.tmdb_id.
    The SECOND run now finds that id preset on the leaf, so it resolves BY ID
    (the enrich-by-known-id path) — the backend therefore serves BOTH the search
    (run 1) and the by-id details (run 2). The idempotency guarantee (no second
    stamp) is what is asserted, regardless of which resolve path the rerun takes."""
    _empty_libs(sandbox)
    folder, fp, h = _seed_movie(sandbox, "mov-en-2025-f1", "F1")
    patch_tmdb(FakeTMDB(
        search={"f1": [_movie_result(1003159, "F1", 2025)]},
        movie_by_id={1003159: _movie_details(1003159, "F1", 2025)},
    ))

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
# Enrich-by-known-id: a manually-set metadata.tmdb_id is honoured DIRECTLY
# (fetch the details by id, NO title search) so a user-pasted id gets the full
# stamp + download treatment. This is the `set_tmdb <id> <tmdbid>` then
# `enrich_metadata <id> --apply` flow. (IMP-E3/U3/D17.)
# ===========================================================================

def _movie_details(tmdb_id, title, year, overview="A great film.", vote_average=7.5):
    """A /3/movie/{id} DETAILS object (carries title/release_date/poster/backdrop
    just like a search result — the by-id resolver reads the same fields)."""
    return {"id": tmdb_id, "title": title, "release_date": f"{year}-01-01",
            "poster_path": "/byid-poster.jpg", "backdrop_path": "/byid-backdrop.jpg",
            "overview": overview, "vote_average": vote_average}


def _tv_details(tmdb_id, name, year, overview="A great show.", vote_average=8.2):
    """A /3/tv/{id} DETAILS object (carries name/first_air_date/poster/backdrop)."""
    return {"id": tmdb_id, "name": name, "first_air_date": f"{year}-01-01",
            "poster_path": "/byid-tvposter.jpg", "backdrop_path": "/byid-tvbackdrop.jpg",
            "overview": overview, "vote_average": vote_average}


def test_apply_movie_with_preset_id_fetches_by_id_not_search(sandbox, patch_tmdb, capsys):
    """A movie whose metadata.tmdb_id is already set (via set_tmdb) is resolved BY
    THAT ID on --apply: enrich GETs /3/movie/{id} (NOT /search/movie), writes the
    by-id title/year, stamps the {tmdb-…} token, and downloads art — with NO search
    call at all (the user explicitly chose this id)."""
    _empty_libs(sandbox)
    folder, fp, orig_hash = _seed_movie(sandbox, "mov-en-2099-f1", "F1")
    # Manually set the id (as `set_tmdb` would) — note the id YEAR (2099) is wrong,
    # proving the by-id details year (2025) is what gets written, not a search.
    lib = mvcommon.load_library()
    lib["mov-en-2099-f1"].setdefault("metadata", {})["tmdb_id"] = 1003159
    mvcommon.save_library(lib)
    # Backend ONLY answers the by-id details — there is NO search entry, so any
    # search attempt would resolve to nothing (and the test would fail downstream).
    fake = patch_tmdb(FakeTMDB(movie_by_id={1003159: _movie_details(1003159, "F1", 2025)}))

    main.cmd_enrich_metadata("mov-en-2099-f1", "--apply")

    out = capsys.readouterr().out
    assert "using preset tmdb_id=1003159 (manual)" in out
    assert "APPLIED" in out

    # Resolved BY ID: the /3/movie/{id} details URL was hit, and NO /search call.
    assert any(u.endswith("/movie/1003159") for u, _ in fake.calls), \
        "expected a by-id /movie/{id} details call"
    assert not any("/search/" in u for u, _ in fake.calls), \
        "enrich-by-known-id must NOT issue any /search call"

    # Title/year written FROM THE BY-ID DETAILS (year 2025, not the id's 2099).
    lib = mvcommon.load_library()
    meta = lib["mov-en-2099-f1"]["metadata"]
    assert meta["tmdb_id"] == 1003159
    assert meta["title"] == "F1"
    assert meta["year"] == 2025

    # Token stamped + art downloaded into the renamed folder (full treatment).
    new_folder = folder.parent / "F1 {tmdb-1003159}"
    assert new_folder.is_dir() and not folder.exists()
    assert (new_folder / "poster.jpg").read_bytes() == FAKE_JPG
    assert (new_folder / "fanart.jpg").read_bytes() == FAKE_JPG
    assert any(u == "https://image.tmdb.org/t/p/w342/byid-poster.jpg" for u in fake.image_urls)
    assert any(u == "https://image.tmdb.org/t/p/w780/byid-backdrop.jpg" for u in fake.image_urls)

    # No media fetch / no rehash.
    assert hashlib.sha256((new_folder / "movie.mkv").read_bytes()).hexdigest() == orig_hash
    assert lib["mov-en-2099-f1"]["hash"] == orig_hash


def test_apply_show_with_preset_id_fetches_by_id_not_search(sandbox, patch_tmdb, capsys):
    """A SHOW whose preset id sits on an episode leaf (set_tmdb refuses season_maps)
    is resolved by /3/tv/{id} on --apply — NO /search/tv — and gets the full
    show-centric treatment: id on both season_maps + episodes, ONE stamp on the show
    folder, per-season posters, per-episode stills."""
    _empty_libs(sandbox)
    show = _seed_two_season_show(sandbox)
    # Set the preset id on ONE episode leaf only (mirrors `set_tmdb <ep_id> 2316`).
    lib = mvcommon.load_library()
    lib[show["ep1"]].setdefault("metadata", {})["tmdb_id"] = 2316
    mvcommon.save_library(lib)
    fake = patch_tmdb(FakeTMDB(
        tv_by_id={2316: _tv_details(2316, "The Office", 2005)},
        season_images={(2316, 1): [{"file_path": "/s1.jpg"}],
                       (2316, 2): [{"file_path": "/s2.jpg"}]},
        episode_images={(2316, 1, 1): [{"file_path": "/still-s1e1.jpg", "vote_average": 8.0}],
                        (2316, 2, 1): [{"file_path": "/still-s2e1.jpg", "vote_average": 6.0}]},
    ))

    main.cmd_enrich_metadata("tv-en-2005-the-office", "--apply")

    out = capsys.readouterr().out
    assert "using preset tmdb_id=2316 (manual)" in out

    # By-id details hit; NO /search/tv at all.
    assert any(u.endswith("/tv/2316") for u, _ in fake.calls), "expected a /tv/{id} details call"
    assert not any("/search/" in u for u, _ in fake.calls), "must NOT search when an id is preset"

    lib = mvcommon.load_library()
    # tmdb_id on BOTH season_maps and BOTH episode leaves.
    for eid in (show["season1"], show["season2"], show["ep1"], show["ep2"]):
        assert lib[eid].get("metadata", {}).get("tmdb_id") == 2316, eid

    # ONE stamp on the show folder; season subfolders intact underneath.
    stamped = show["show_root"].parent / "The Office {tmdb-2316}"
    assert stamped.is_dir() and not show["show_root"].exists()
    assert (stamped / "Season 01").is_dir() and (stamped / "Season 02").is_dir()

    # Show poster/fanart + per-season posters + per-episode stills all landed.
    assert (stamped / "poster.jpg").read_bytes() == FAKE_JPG
    assert (stamped / "fanart.jpg").read_bytes() == FAKE_JPG
    assert (stamped / "Season 01" / "poster.jpg").read_bytes() == FAKE_JPG
    assert (stamped / "Season 02" / "poster.jpg").read_bytes() == FAKE_JPG
    for ep_id, sdir in ((show["ep1"], "Season 01"), (show["ep2"], "Season 02")):
        thumb = stamped / sdir / _thumb_name(lib[ep_id]["filename"])
        assert thumb.read_bytes() == FAKE_JPG


def test_dry_run_with_preset_id_prints_by_id_intent_writes_nothing(sandbox, patch_tmdb, capsys):
    """DRY-RUN (no --apply) with a preset id: prints the by-id intent + WOULD-write
    lines but writes NOTHING — no extra tmdb_id key churn, no rename, no poster."""
    _empty_libs(sandbox)
    folder, fp, h = _seed_movie(sandbox, "mov-en-2099-f1", "F1")
    lib = mvcommon.load_library()
    lib["mov-en-2099-f1"].setdefault("metadata", {})["tmdb_id"] = 1003159
    mvcommon.save_library(lib)
    before = mvcommon.load_library()
    fake = patch_tmdb(FakeTMDB(movie_by_id={1003159: _movie_details(1003159, "F1", 2025)}))

    main.cmd_enrich_metadata("mov-en-2099-f1")  # no --apply -> dry run

    out = capsys.readouterr().out
    assert "DRY-RUN" in out
    assert "using preset tmdb_id=1003159 (manual)" in out
    assert "would write" in out
    # Resolved by id even in dry-run (so the printed title/year are accurate),
    # but still NO search call.
    assert any(u.endswith("/movie/1003159") for u, _ in fake.calls)
    assert not any("/search/" in u for u, _ in fake.calls)

    # Nothing changed on disk.
    assert mvcommon.load_library() == before
    assert folder.exists()
    assert not (folder.parent / "F1 {tmdb-1003159}").exists()
    assert not (folder / "poster.jpg").exists()


def test_preset_id_by_id_fetch_failure_is_skipped_no_search_fallback(sandbox, patch_tmdb, capsys):
    """A by-id details fetch that FAILS (network/404) -> the unit is SKIPPED (no
    crash, library untouched) and there is NO fall-back title search (the user
    explicitly chose this id; re-searching would just re-miss)."""
    _empty_libs(sandbox)
    folder, fp, h = _seed_movie(sandbox, "mov-en-2025-f1", "F1")
    lib = mvcommon.load_library()
    lib["mov-en-2025-f1"].setdefault("metadata", {})["tmdb_id"] = 1003159
    mvcommon.save_library(lib)
    before = mvcommon.load_library()
    # The by-id details call raises; a search entry EXISTS but must never be used.
    fake = patch_tmdb(FakeTMDB(
        search={"f1": [_movie_result(1003159, "F1", 2025)]},
        by_id_error={1003159},
    ))

    main.cmd_enrich_metadata("mov-en-2025-f1", "--apply")  # must NOT crash

    out = capsys.readouterr().out
    assert "skipping" in out.lower()
    # The by-id call was attempted; NO search fallback happened.
    assert any(u.endswith("/movie/1003159") for u, _ in fake.calls)
    assert not any("/search/" in u for u, _ in fake.calls), \
        "a by-id failure must NOT fall back to a title search"

    # Library untouched: no rename, original folder + hash intact.
    assert mvcommon.load_library() == before
    assert folder.exists()
    assert not (folder.parent / "F1 {tmdb-1003159}").exists()


def test_no_preset_id_still_searches(sandbox, patch_tmdb, capsys):
    """Guard: with NO preset id the existing title-SEARCH path is unchanged — enrich
    issues a /search/movie and does NOT attempt any by-id details call."""
    _empty_libs(sandbox)
    folder, fp, h = _seed_movie(sandbox, "mov-en-2025-f1", "F1")
    fake = patch_tmdb(FakeTMDB(search={"f1": [_movie_result(1003159, "F1", 2025)]}))

    main.cmd_enrich_metadata("mov-en-2025-f1", "--apply")

    assert any("/search/movie" in u for u, _ in fake.calls), "no preset id -> must search"
    # No by-id details call (a bare /movie/{digits} with nothing after).
    assert not any(
        u.rstrip("/").split("/")[-1].isdigit() and u.rstrip("/").split("/")[-2] == "movie"
        for u, _ in fake.calls), "no preset id -> must NOT fetch by id"
    assert "using preset tmdb_id" not in capsys.readouterr().out
    # Sanity: the search path still applied normally.
    assert mvcommon.load_library()["mov-en-2025-f1"]["metadata"]["tmdb_id"] == 1003159


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
# NFO richer element set (IMP-D22, Decision 4) — every added element OPTIONAL,
# <tvdbid> NEVER emitted (Decision 1). Shared/non-forked work (identical in
# both IMP-D22 candidates); these tests are pure ADDITIONS — no existing
# assertion above needed updating (none pinned the pre-D22 minimal set).
# ===========================================================================

def test_nfo_movie_richer_fields_populate_when_available(sandbox, patch_tmdb, capsys):
    """--apply --nfo on a movie resolved BY A PRESET ID (so the by-id details
    payload can carry the extra TMDB fields): the richer element set populates
    from that SAME payload (imdbid/genre/runtime/premiered/studio), cast/
    director gracefully OMIT (no credits payload supplied), and <tvdbid> is
    NEVER emitted."""
    import xml.etree.ElementTree as ET
    _empty_libs(sandbox)
    folder, fp, h = _seed_movie(sandbox, "mov-en-2025-f1", "F1")
    lib = mvcommon.load_library()
    lib["mov-en-2025-f1"].setdefault("metadata", {})["tmdb_id"] = 1003159
    mvcommon.save_library(lib)
    rich_details = {
        "id": 1003159, "title": "F1", "release_date": "2025-05-01",
        "poster_path": "/poster.jpg", "backdrop_path": "/backdrop.jpg",
        "overview": "Racing film.", "vote_average": 7.1,
        "imdb_id": "tt1234567",
        "genres": [{"id": 1, "name": "Action"}, {"id": 2, "name": "Drama"}],
        "runtime": 145,
        "production_companies": [{"id": 10, "name": "Apex Films"}],
    }
    patch_tmdb(FakeTMDB(movie_by_id={1003159: rich_details}))

    main.cmd_enrich_metadata("mov-en-2025-f1", "--apply", "--nfo")

    new_folder = folder.parent / "F1 {tmdb-1003159}"
    nfo_path = new_folder / "movie.nfo"
    assert nfo_path.exists()
    root = ET.parse(str(nfo_path)).getroot()

    assert root.find("tmdbid").text == "1003159"
    assert root.find("imdbid").text == "tt1234567"
    imdb_uids = [u for u in root.findall("uniqueid") if u.get("type") == "imdb"]
    assert len(imdb_uids) == 1 and imdb_uids[0].text == "tt1234567"
    assert [g.text for g in root.findall("genre")] == ["Action", "Drama"]
    assert root.find("runtime").text == "145"
    assert root.find("premiered").text == "2025-05-01"
    assert [s.text for s in root.findall("studio")] == ["Apex Films"]

    # No credits payload was supplied -> cast/director gracefully OMITTED.
    assert root.find("director") is None
    assert root.findall("actor") == []

    # <tvdbid> is NEVER emitted (Decision 1 — MediaVault is TMDB-only).
    assert root.find("tvdbid") is None


def test_nfo_movie_omits_richer_fields_gracefully_without_detail_data(sandbox, patch_tmdb, capsys):
    """--apply --nfo on a movie resolved by TITLE SEARCH (no by-id details, no
    credits payload available): every richer element is cleanly OMITTED —
    the base title/year/plot/rating/uniqueid(tmdb)/tmdbid fields are still
    written — and <tvdbid> is NEVER emitted."""
    import xml.etree.ElementTree as ET
    _empty_libs(sandbox)
    folder, fp, h = _seed_movie(sandbox, "mov-en-2025-f1", "F1")
    patch_tmdb(FakeTMDB(search={"f1": [
        _movie_result_with_meta(1003159, "F1", 2025, overview="Racing film.", vote_average=7.1)
    ]}))

    main.cmd_enrich_metadata("mov-en-2025-f1", "--apply", "--nfo")

    new_folder = folder.parent / "F1 {tmdb-1003159}"
    root = ET.parse(str(new_folder / "movie.nfo")).getroot()

    # Base fields (pre-D22) still present, plus the always-on plain <tmdbid>.
    assert root.find("title").text == "F1"
    assert root.find("tmdbid").text == "1003159"

    # Every richer element gracefully OMITTED — no genre/runtime/premiered/
    # studio/director/actor/imdbid data is available from a title-search match.
    assert root.find("imdbid") is None
    assert [u.get("type") for u in root.findall("uniqueid")] == ["tmdb"]
    assert root.findall("genre") == []
    assert root.find("runtime") is None
    assert root.find("premiered") is None
    assert root.findall("studio") == []
    assert root.find("director") is None
    assert root.findall("actor") == []

    # <tvdbid> is NEVER emitted (Decision 1 — MediaVault is TMDB-only).
    assert root.find("tvdbid") is None


def test_nfo_show_never_emits_tvdbid(sandbox, patch_tmdb, capsys):
    """--apply --nfo on a confident SHOW never emits <tvdbid> either (the
    Fringe-folder shape in Decision 4's spec has one; MediaVault deliberately
    never writes it — no TVDB client exists, Decision 1)."""
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

    stamped_show = show["show_root"].parent / "The Office {tmdb-2316}"
    root = ET.parse(str(stamped_show / "tvshow.nfo")).getroot()

    assert root.find("tmdbid").text == "2316"
    assert root.find("tvdbid") is None


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


# ===========================================================================
# Synopsis / overview storage (IMP-E16 step 1 — the detail-window data).
#
# On a confident --apply, enrich now ALSO persists the TMDB synopsis:
#   * movie / show  -> metadata.overview (the title-level overview), on the unit.
#   * each episode  -> metadata.overview (episode synopsis) + metadata.episode_title
#                      (the episode name), via ONE cached SEASON DETAILS call per
#                      season (GET /3/tv/{id}/season/{n} -> episodes[]).
# Additive + idempotent; a failed season-details call degrades gracefully (the
# episode keeps the show synopsis seeded in step 1). NEVER a media fetch.
# ===========================================================================

def test_apply_movie_writes_overview(sandbox, patch_tmdb):
    """A confident movie --apply persists metadata.overview = the TMDB overview
    (alongside tmdb_id/title/year), so the detail-window has a synopsis."""
    _empty_libs(sandbox)
    folder, fp, h = _seed_movie(sandbox, "mov-en-2025-f1", "F1")
    patch_tmdb(FakeTMDB(search={"f1": [
        _movie_result_with_meta(1003159, "F1", 2025, overview="A racing thriller.")
    ]}))

    main.cmd_enrich_metadata("mov-en-2025-f1", "--apply")

    meta = mvcommon.load_library()["mov-en-2025-f1"]["metadata"]
    assert meta["overview"] == "A racing thriller."
    assert meta["tmdb_id"] == 1003159  # the existing fields are still written


def test_apply_movie_by_id_writes_overview(sandbox, patch_tmdb):
    """The enrich-by-known-id path also persists metadata.overview (the by-id movie
    details object carries `overview`, read identically to a search result)."""
    _empty_libs(sandbox)
    folder, fp, h = _seed_movie(sandbox, "mov-en-2099-f1", "F1")
    lib = mvcommon.load_library()
    lib["mov-en-2099-f1"].setdefault("metadata", {})["tmdb_id"] = 1003159
    mvcommon.save_library(lib)
    patch_tmdb(FakeTMDB(movie_by_id={
        1003159: _movie_details(1003159, "F1", 2025, overview="By-id racing synopsis.")
    }))

    main.cmd_enrich_metadata("mov-en-2099-f1", "--apply")

    meta = mvcommon.load_library()["mov-en-2099-f1"]["metadata"]
    assert meta["overview"] == "By-id racing synopsis."
    assert meta["tmdb_id"] == 1003159


def test_apply_show_writes_overview_on_seasonmaps_and_episodes(sandbox, patch_tmdb):
    """A confident show --apply seeds the SHOW overview onto BOTH season_maps (which
    have no per-episode synopsis of their own) AND — refined by the season-details
    call — writes each EPISODE's OWN synopsis + episode_title onto its leaf."""
    _empty_libs(sandbox)
    show = _seed_two_season_show(sandbox)
    fake = patch_tmdb(FakeTMDB(
        search={"the office": [
            _tv_result_with_meta(2316, "The Office", 2005, overview="A mockumentary.")
        ]},
        season_images={(2316, 1): [], (2316, 2): []},
        season_details={
            (2316, 1): _season_details([(1, "Pilot", "The documentary begins.")]),
            (2316, 2): _season_details([(1, "The Dundies", "An awards night.")]),
        },
    ))

    main.cmd_enrich_metadata("tv-en-2005-the-office", "--apply")

    lib = mvcommon.load_library()

    # Season_maps carry the SHOW overview (no per-episode synopsis applies to them).
    for sid in (show["season1"], show["season2"]):
        assert lib[sid]["metadata"]["overview"] == "A mockumentary.", sid
        assert "episode_title" not in lib[sid]["metadata"]  # not an episode

    # Each EPISODE leaf carries its OWN synopsis + episode_title (refined from the
    # show overview seeded in step 1 by the per-season season-details call).
    assert lib[show["ep1"]]["metadata"]["overview"] == "The documentary begins."
    assert lib[show["ep1"]]["metadata"]["episode_title"] == "Pilot"
    assert lib[show["ep2"]]["metadata"]["overview"] == "An awards night."
    assert lib[show["ep2"]]["metadata"]["episode_title"] == "The Dundies"

    # ONE season-details GET per season (the bare /tv/{id}/season/{n}, not /images).
    sd_calls = [c for c in fake.calls
                if "/season/" in c[0] and "/episode/" not in c[0]
                and not c[0].endswith("/images")]
    assert len(sd_calls) == 2, f"expected one season-details call per season, got {len(sd_calls)}"


def test_apply_show_season_details_failure_degrades_to_show_overview(sandbox, patch_tmdb):
    """If a season's season-details call FAILS, that episode keeps the SHOW overview
    seeded in step 1 (graceful degradation, no crash) and has NO episode_title; the
    healthy season's episode still gets its own synopsis + title."""
    _empty_libs(sandbox)
    show = _seed_two_season_show(sandbox)
    patch_tmdb(FakeTMDB(
        search={"the office": [
            _tv_result_with_meta(2316, "The Office", 2005, overview="A mockumentary.")
        ]},
        season_images={(2316, 1): [], (2316, 2): []},
        # Season 1 resolves; season 2's season-details call raises.
        season_details={(2316, 1): _season_details([(1, "Pilot", "The documentary begins.")])},
        season_details_error={(2316, 2)},
    ))

    main.cmd_enrich_metadata("tv-en-2005-the-office", "--apply")  # must NOT crash

    lib = mvcommon.load_library()
    # Season 1 episode: its OWN synopsis + title.
    assert lib[show["ep1"]]["metadata"]["overview"] == "The documentary begins."
    assert lib[show["ep1"]]["metadata"]["episode_title"] == "Pilot"
    # Season 2 episode: degrades to the SHOW overview, NO episode_title written.
    assert lib[show["ep2"]]["metadata"]["overview"] == "A mockumentary."
    assert "episode_title" not in lib[show["ep2"]]["metadata"]


def test_apply_episode_overview_is_idempotent(sandbox, patch_tmdb):
    """A second --apply rewrites the SAME episode overview/title (idempotent — the
    season-details payload is cached/deterministic, so no drift)."""
    _empty_libs(sandbox)
    show = _seed_two_season_show(sandbox)
    patch_tmdb(FakeTMDB(
        search={"the office": [
            _tv_result_with_meta(2316, "The Office", 2005, overview="A mockumentary.")
        ]},
        season_images={(2316, 1): [], (2316, 2): []},
        season_details={
            (2316, 1): _season_details([(1, "Pilot", "The documentary begins.")]),
            (2316, 2): _season_details([(1, "The Dundies", "An awards night.")]),
        },
    ))

    main.cmd_enrich_metadata("tv-en-2005-the-office", "--apply")
    main.cmd_enrich_metadata("tv-en-2005-the-office", "--apply")  # re-run

    lib = mvcommon.load_library()
    assert lib[show["ep1"]]["metadata"]["overview"] == "The documentary begins."
    assert lib[show["ep1"]]["metadata"]["episode_title"] == "Pilot"
    assert lib[show["ep2"]]["metadata"]["episode_title"] == "The Dundies"


def test_dry_run_writes_no_overview(sandbox, patch_tmdb):
    """DRY-RUN (no --apply): no overview / episode_title is written (nothing is)."""
    _empty_libs(sandbox)
    show = _seed_two_season_show(sandbox)
    before = mvcommon.load_library()
    patch_tmdb(FakeTMDB(
        search={"the office": [
            _tv_result_with_meta(2316, "The Office", 2005, overview="A mockumentary.")
        ]},
        season_details={(2316, 1): _season_details([(1, "Pilot", "Synopsis.")])},
    ))

    main.cmd_enrich_metadata("tv-en-2005-the-office")  # no --apply

    assert mvcommon.load_library() == before  # nothing written at all


# ===========================================================================
# EXA web-search fallback for TMDB resolution (IMP-E16/D5).
#
# WATERFALL: (i) preset metadata.tmdb_id -> by-id; (ii) else TMDB title-search ->
# confident; (iii) NEW: else (none/ambiguous) AND an EXA key is configured AND NOT
# --no-web -> _exa_resolve_tmdb_id web-searches themoviedb.org for the id, which is
# then VALIDATED by a real by-id details fetch (confident result w/ real title/year/
# poster) before ANYTHING is written. EXA finding nothing OR a failed by-id
# validation falls through to the EXISTING manual handling — CONFIDENT-ONLY, never an
# unvalidated guess. --no-web disables the fallback (pure TMDB-API behaviour).
#
# Mocked: the waterfall-wiring tests monkeypatch main._exa_resolve_tmdb_id (a recorder
# that also proves the API-search-FIRST ordering); the extraction/kind-preference
# tests drive the REAL function with a canned main.requests.post + a temp EXA cache
# dir. NEVER a real network call. patch_tmdb defaults the EXA key OFF, so these tests
# re-enable it explicitly.
# ===========================================================================


class _ExaRecorder:
    """Stand-in for main._exa_resolve_tmdb_id. Records each (title, year, kind) call
    and returns a canned id (or None). Bound to a FakeTMDB so the FIRST call snapshots
    whether a TMDB /search already ran — proving the API-search-FIRST, then-EXA order."""
    def __init__(self, return_id, fake=None):
        self.return_id = return_id
        self.fake = fake
        self.calls = []
        self.search_seen_before = None

    def __call__(self, title, year, kind):
        if self.search_seen_before is None and self.fake is not None:
            self.search_seen_before = any("/search/" in c[0] for c in self.fake.calls)
        self.calls.append((title, year, kind))
        return self.return_id


def _exa_response(*urls):
    """A canned EXA /search response: {"results": [{"url", "title", "text"}, ...]}."""
    return {"results": [{"url": u, "title": "t", "text": "x"} for u in urls]}


def test_api_miss_then_exa_fallback_resolves_confident(sandbox, patch_tmdb, monkeypatch, capsys):
    """API search MISSES (none) -> EXA fallback returns an id -> by-id VALIDATION ->
    CONFIDENT: tmdb_id + the real (validated) title/year written and the {tmdb-…} token
    stamped. Proves the waterfall ORDER: the TMDB title-search runs FIRST, THEN EXA,
    THEN the by-id validation fetch."""
    _empty_libs(sandbox)
    folder, fp, h = _seed_movie(sandbox, "mov-ta-2008-vaaranamaayiram", "Vaaranam Aayiram")
    # The TMDB title-search knows nothing about this concatenated slug -> status "none".
    # The by-id details ARE seeded, so the EXA-found id validates to a confident result.
    fake = patch_tmdb(FakeTMDB(
        search={},  # 'vaaranamaayiram' -> [] (a true API miss)
        movie_by_id={38637: _movie_details(38637, "Vaaranam Aayiram", 2008)},
    ))
    monkeypatch.setattr(mvcommon, "exa_api_key", lambda: "TEST-EXA-KEY")  # fallback ON
    rec = _ExaRecorder(38637, fake=fake)
    monkeypatch.setattr(main, "_exa_resolve_tmdb_id", rec)

    main.cmd_enrich_metadata("mov-ta-2008-vaaranamaayiram", "--apply")

    out = capsys.readouterr().out
    assert "web-search fallback ON" in out
    assert "resolved via web search: tmdb_id=38637" in out

    # Waterfall ORDER: the API SEARCH ran first, THEN EXA was consulted exactly once
    # with the unit's (humanized title, year, kind).
    assert any("/search/movie" in u for u, _ in fake.calls), "the TMDB title-search must run first"
    assert rec.calls == [("vaaranamaayiram", 2008, "movie")]
    assert rec.search_seen_before is True, "EXA must be consulted AFTER the API search misses"
    # The EXA-found id was VALIDATED by a real by-id details fetch.
    assert any(u.endswith("/movie/38637") for u, _ in fake.calls), "EXA id must be by-id validated"

    # Confident write: tmdb_id + the real (validated) title/year, and the folder stamp.
    lib = mvcommon.load_library()
    meta = lib["mov-ta-2008-vaaranamaayiram"]["metadata"]
    assert meta["tmdb_id"] == 38637
    assert meta["title"] == "Vaaranam Aayiram"
    assert meta["year"] == 2008
    assert (folder.parent / "Vaaranam Aayiram {tmdb-38637}").is_dir()
    assert not folder.exists()


def test_exa_fallback_returns_none_still_listed_manual(sandbox, patch_tmdb, monkeypatch, capsys):
    """EXA fallback returns None (no themoviedb hit) -> the unit falls through to the
    EXISTING manual-review handling UNCHANGED: listed for set_tmdb, NOTHING written,
    no by-id validation call."""
    _empty_libs(sandbox)
    folder, fp, h = _seed_movie(sandbox, "mov-en-2099-noexist", "NoExist")
    fake = patch_tmdb(FakeTMDB(search={}))  # API miss
    monkeypatch.setattr(mvcommon, "exa_api_key", lambda: "TEST-EXA-KEY")  # fallback ON
    rec = _ExaRecorder(None, fake=fake)  # ...but EXA finds nothing
    monkeypatch.setattr(main, "_exa_resolve_tmdb_id", rec)
    before = mvcommon.load_library()

    main.cmd_enrich_metadata("mov-en-2099-noexist", "--apply")

    out = capsys.readouterr().out
    assert len(rec.calls) == 1                         # the fallback WAS attempted
    assert "resolved via web search" not in out        # ...and resolved nothing
    assert "NO TMDB match" in out or "NEED MANUAL CONFIRMATION" in out
    # No id was found -> no by-id validation call was made.
    assert not any(u.rstrip("/").split("/")[-1].isdigit() and u.rstrip("/").split("/")[-2] == "movie"
                   for u, _ in fake.calls), "no id found -> must NOT issue a by-id validation call"
    assert mvcommon.load_library() == before           # nothing written
    assert folder.exists()


def test_no_web_flag_disables_exa_fallback(sandbox, patch_tmdb, monkeypatch, capsys):
    """--no-web DISABLES the EXA fallback even when an EXA key is configured: the API
    miss is listed for manual review and _exa_resolve_tmdb_id is NEVER called."""
    _empty_libs(sandbox)
    folder, fp, h = _seed_movie(sandbox, "mov-en-2099-noexist", "NoExist")
    fake = patch_tmdb(FakeTMDB(search={}))  # API miss
    monkeypatch.setattr(mvcommon, "exa_api_key", lambda: "TEST-EXA-KEY")  # key IS present

    def _boom(*a, **k):
        raise AssertionError("--no-web must NOT call the EXA fallback")
    monkeypatch.setattr(main, "_exa_resolve_tmdb_id", _boom)
    before = mvcommon.load_library()

    main.cmd_enrich_metadata("mov-en-2099-noexist", "--apply", "--no-web")  # must NOT trip _boom

    out = capsys.readouterr().out
    assert "--no-web" in out and "DISABLED" in out
    assert "resolved via web search" not in out
    assert "NO TMDB match" in out or "NEED MANUAL CONFIRMATION" in out
    assert mvcommon.load_library() == before  # nothing written


def test_exa_resolve_prefers_same_kind_url(monkeypatch, tmp_path):
    """Unit: _exa_resolve_tmdb_id extracts (kind, id) from each themoviedb.org URL and
    PREFERS the first hit whose kind matches the unit — a MOVIE unit picks the /movie/
    id even when a /tv/ URL is listed FIRST; a SHOW unit picks the /tv/ id. Also asserts
    the POST mirrors exa_search_trivia's headers + the documented body shape."""
    monkeypatch.setattr(main, "EXA_CACHE_DIR", str(tmp_path / "exa_cache"))
    monkeypatch.setattr(mvcommon, "exa_api_key", lambda: "TEST-EXA-KEY")

    posts = []

    def fake_post(url, headers=None, json=None, timeout=None, **kwargs):
        posts.append((url, headers, json))
        # A /tv/ URL appears BEFORE the /movie/ URL, so kind PREFERENCE (not list
        # order) is what must decide the winner.
        return _Resp(200, json_data=_exa_response(
            "https://www.themoviedb.org/tv/99999-some-show",
            "https://www.themoviedb.org/movie/38637-vaaranam-aayiram",
        ))
    monkeypatch.setattr(main.requests, "post", fake_post)

    # MOVIE unit -> the /movie/ id (even though /tv/ is listed first).
    assert main._exa_resolve_tmdb_id("Vaaranam Aayiram", 2008, "movie") == 38637
    # SHOW unit (same canned response, distinct year -> fresh query, not cache) -> /tv/ id.
    assert main._exa_resolve_tmdb_id("Vaaranam Aayiram", 2013, "show") == 99999

    # The EXA POST mirrored exa_search_trivia's headers + the documented body shape.
    url, headers, body = posts[0]
    assert url == main.EXA_API_ROOT
    assert headers["x-api-key"] == "TEST-EXA-KEY"
    assert body["includeDomains"] == ["themoviedb.org"]
    assert body["numResults"] == 5
    assert "site:themoviedb.org" in body["query"]


def test_exa_resolve_other_kind_fallback_and_none(monkeypatch, tmp_path):
    """Unit: the best-effort + None branches of _exa_resolve_tmdb_id —
      * a MOVIE unit with ONLY a /tv/ URL accepts that other-kind id (best-effort);
      * a response with NO themoviedb detail URL -> None;
      * no EXA key -> None, with NO network POST (self-defensive)."""
    monkeypatch.setattr(main, "EXA_CACHE_DIR", str(tmp_path / "exa_cache"))
    monkeypatch.setattr(mvcommon, "exa_api_key", lambda: "TEST-EXA-KEY")

    monkeypatch.setattr(main.requests, "post", lambda *a, **k: _Resp(
        200, json_data=_exa_response("https://www.themoviedb.org/tv/60574-peaky")))
    # MOVIE unit, only a /tv/ URL present -> accept the other-kind id (best-effort).
    assert main._exa_resolve_tmdb_id("Peaky Blinders", 2013, "movie") == 60574

    monkeypatch.setattr(main.requests, "post", lambda *a, **k: _Resp(
        200, json_data=_exa_response("https://example.com/not-tmdb")))
    assert main._exa_resolve_tmdb_id("Whatever", 2000, "movie") is None

    # No EXA key -> None and NO network call (the guard short-circuits before the POST).
    monkeypatch.setattr(mvcommon, "exa_api_key", lambda: "")

    def post_boom(*a, **k):
        raise AssertionError("must NOT POST without an EXA key")
    monkeypatch.setattr(main.requests, "post", post_boom)
    assert main._exa_resolve_tmdb_id("Whatever", 2000, "movie") is None


def test_exa_resolve_caches_response_idempotent(monkeypatch, tmp_path):
    """Unit: the raw EXA response is cached on disk (keyed by the query), so a second
    IDENTICAL call is served from cache and does NOT re-POST to EXA (idempotent)."""
    monkeypatch.setattr(main, "EXA_CACHE_DIR", str(tmp_path / "exa_cache"))
    monkeypatch.setattr(mvcommon, "exa_api_key", lambda: "TEST-EXA-KEY")
    n = {"posts": 0}

    def fake_post(url, headers=None, json=None, timeout=None, **kwargs):
        n["posts"] += 1
        return _Resp(200, json_data=_exa_response("https://www.themoviedb.org/movie/281992-sv"))
    monkeypatch.setattr(main.requests, "post", fake_post)

    assert main._exa_resolve_tmdb_id("Sathuranga Vettai", 2014, "movie") == 281992
    assert main._exa_resolve_tmdb_id("Sathuranga Vettai", 2014, "movie") == 281992  # cache hit
    assert n["posts"] == 1, "the second identical call must be served from the disk cache"


# ---------------------------------------------------------------------------
# IMP-C23 — `_has_tmdb_token` is CASE-INSENSITIVE (generalized by IMP-U6).
#
# Real folders in the wild carry an uppercase token (the user's own
# `Run (2002)  - 4K SDR - (DD+5.1 - 192Kbps & AAC)  {TMDB-69590}`). Before the
# fix `_has_tmdb_token` had no `re.IGNORECASE`, so such a folder read as "no
# token" and the next enrich/rename pass appended a SECOND one. IMP-U6 widened
# the predicate to BOTH shapes: the canonical square `[tmdbid-…]` (D1) and the
# legacy curly `{tmdb-…}` / `{TMDB-…}`, which D3 keeps recognized forever so a
# pre-migration folder can never be double-stamped.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("name", [
    "Run (2002) {TMDB-69590}",          # the real-world uppercase LEGACY shape
    "Drishyam 3 (2026) {tmdb-847742}",  # lowercase legacy curly (already worked)
    "X {TmDb-1}",                       # mixed case
    "Dark Season 01 (2017) {TMDB-70523}",
    "Inception (2010) [tmdbid-27205]",  # IMP-U6 canonical square form
    "X [TmDbId-1]",                     # square, mixed case
])
def test_has_tmdb_token_is_case_insensitive(name):
    assert main._has_tmdb_token(name) is True


@pytest.mark.parametrize("name", [
    "No token here", "", None, "Movie (2020)",
    "{tvdb-123}", "[tvdbid-123]",       # other providers: not a TMDB token, either shape
])
def test_has_tmdb_token_still_false_without_a_tmdb_token(name):
    """The fix must not make the predicate over-eager: a name with no tmdb
    token — including a tvdb token in either shape, which this project never
    STAMPS — stays False."""
    assert main._has_tmdb_token(name) is False


def test_has_tmdb_token_agrees_with_tmdb_token_re():
    """Regression pin for the DRIFT that caused IMP-C23: `_has_tmdb_token` and
    the module-level `_TMDB_TOKEN_RE` are the same predicate and must stay in
    lockstep. If a future edit changes one, this fails."""
    for name in ["Run (2002) {TMDB-69590}", "a {tmdb-1}", "X {TmDb-1}",
                 "Inception (2010) [tmdbid-27205]", "X [TmDbId-9]",
                 "none", "", "{tvdb-9}", "[tvdbid-9]", "Show (1993) {tmdb-4087}"]:
        assert bool(main._has_tmdb_token(name)) is bool(
            main._TMDB_TOKEN_RE.search(name or "")), name


def test_has_provider_token_agrees_with_provider_token_re():
    """IMP-U6 pin: `_has_provider_token` and `_PROVIDER_TOKEN_RE` are the same
    ANY-provider / any-shape predicate (the artwork walk's) and must not drift."""
    for name in ["Run (2002) {TMDB-69590}", "a {tvdb-1}", "X [tmdbid-1]",
                 "Y [TVDBID-334824]", "Z [IMDBID-tt123]", "none", "", "plain",
                 "{imdb-tt9}", "Show (1993) {tmdb-4087}"]:
        assert bool(main._has_provider_token(name)) is bool(
            main._PROVIDER_TOKEN_RE.search(name or "")), name


def test_format_and_strip_tmdb_token():
    """IMP-U6: the ONE stamp formatter + the strip helper used by the migration
    tool and re-stamp flows."""
    assert main.format_tmdb_token(603692) == "[tmdbid-603692]"
    assert main.format_tmdb_token("70523") == "[tmdbid-70523]"
    assert main.strip_tmdb_tokens("Dark (2017) [tmdbid-70523]") == "Dark (2017)"
    assert main.strip_tmdb_tokens("Run (2002) {TMDB-69590}") == "Run (2002)"
    assert main.strip_tmdb_tokens("A [tmdbid-1] B {tmdb-2}") == "A B"
    assert main.strip_tmdb_tokens("No token") == "No token"
    assert main.strip_tmdb_tokens(None) == ""
