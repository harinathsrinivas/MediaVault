"""IMP-U6 — the `[tmdbid-…]` folder-token convention + NFO-at-stamp (D1/D6).

Covers the four NEW behaviors this task introduces, end-to-end through the real
enrich path (conftest's `mock_tmdb` seals the network):

  1. A confident enrich --apply stamps the CANONICAL square token
     `[tmdbid-<id>]` (D1) — and writes `movie.nfo` into the NEW folder by
     DEFAULT (D6), carrying `<uniqueid type="tmdb">` + `<tmdbid>` so Plex's
     NFO agent (and Jellyfin/Emby) pin the same id.
  2. `--no-nfo` opts out of the stamp-time NFO write.
  3. A pre-existing (hand-tuned) NFO is NEVER overwritten — same rule as
     enrich's poster/fanart rule.
  4. A LEGACY curly `{tmdb-…}` folder is recognized and never re-stamped
     (D3 — the generalized IMP-C23 idempotency guard).
  5. The artwork season-inheritance walk accepts the new square shape AND a
     `[tvdbid-…]`-only show folder (the detector is any-provider/any-shape).
"""
import os

import mvcommon
import main


def _seed_movie(sandbox, mid, folder_name, filename="movie.mkv"):
    """Seed a single movie leaf with a real on-disk file (mirrors the
    test_enrich_metadata helper shape). Returns (folder, filepath)."""
    folder = sandbox["local_root"] / "Movies" / folder_name
    folder.mkdir(parents=True, exist_ok=True)
    fp = folder / filename
    fp.write_bytes(b"MOVIE-MASTER-BYTES\n" * 40)
    lib = mvcommon.load_library()
    lib[mid] = {
        "short_id": mvcommon.generate_short_id(mid),
        "filename": filename,
        "folder_path": str(folder),
        "status": "local_ready",
        "uploaded": False,
        "hash": "0" * 64,
        "metadata": main.parse_metadata_from_id(mid),
    }
    mvcommon.save_library(lib)
    return folder, fp


def _movie_result(tmdb_id, title, year):
    return {"id": tmdb_id, "title": title, "release_date": f"{year}-01-01",
            "popularity": 50.0, "poster_path": "/poster.jpg",
            "backdrop_path": "/backdrop.jpg"}


def test_enrich_stamp_writes_nfo_by_default(sandbox, mock_tmdb, capsys):
    """D1+D6: confident enrich --apply stamps `[tmdbid-<id>]` AND writes the NFO
    into the NEW folder (Plex's NFO agent pins the id from it)."""
    folder, _fp = _seed_movie(sandbox, "mov-en-2025-f1", "F1 The Movie")
    mock_tmdb.search["f1"] = [_movie_result(1003159, "F1", 2025)]

    main.cmd_enrich_metadata("mov-en-2025-f1", "--apply")

    out = capsys.readouterr().out
    stamped = folder.parent / "F1 The Movie [tmdbid-1003159]"
    assert stamped.is_dir(), out
    assert not folder.exists()
    nfo = stamped / "movie.nfo"
    assert nfo.exists(), "the stamp-time NFO (D6 default) must exist"
    body = nfo.read_text(encoding="utf-8")
    assert '<uniqueid type="tmdb" default="true">1003159</uniqueid>' in body
    assert "<tmdbid>1003159</tmdbid>" in body
    assert "wrote movie.nfo" in out
    # The library's folder_path points at the stamped folder.
    lib = mvcommon.load_library()
    assert main._norm_path(lib["mov-en-2025-f1"]["folder_path"]) == main._norm_path(str(stamped))


def test_enrich_stamp_no_nfo_opt_out(sandbox, mock_tmdb, capsys):
    """D6: `--no-nfo` skips the stamp-time NFO write; the token still stamps."""
    folder, _fp = _seed_movie(sandbox, "mov-en-2025-f1", "F1 The Movie")
    mock_tmdb.search["f1"] = [_movie_result(1003159, "F1", 2025)]

    main.cmd_enrich_metadata("mov-en-2025-f1", "--apply", "--no-nfo")

    out = capsys.readouterr().out
    stamped = folder.parent / "F1 The Movie [tmdbid-1003159]"
    assert stamped.is_dir(), out
    assert not (stamped / "movie.nfo").exists(), "--no-nfo must suppress the NFO"


def test_nfo_never_overwritten(sandbox, mock_tmdb, capsys):
    """D6: a hand-tuned NFO in the folder ALWAYS wins — the stamp-time write
    keeps it byte-for-byte (same rule as enrich's poster/fanart rule)."""
    folder, _fp = _seed_movie(sandbox, "mov-en-2025-f1", "F1 The Movie")
    hand_tuned = b'<!-- my curated NFO -->\n<movie><title>Hand tuned</title></movie>\n'
    (folder / "movie.nfo").write_bytes(hand_tuned)
    mock_tmdb.search["f1"] = [_movie_result(1003159, "F1", 2025)]

    main.cmd_enrich_metadata("mov-en-2025-f1", "--apply")

    out = capsys.readouterr().out
    stamped = folder.parent / "F1 The Movie [tmdbid-1003159]"
    assert stamped.is_dir(), out
    # The hand-tuned NFO moved with the folder, byte-for-byte.
    assert (stamped / "movie.nfo").read_bytes() == hand_tuned
    assert "already exists — kept (never overwritten)" in out


def test_legacy_curly_folder_never_restamped(sandbox, mock_tmdb, capsys):
    """D3: a legacy `{tmdb-…}` folder is recognized by the either-shape guard —
    enrich skips the stamp (no second token, no rename)."""
    folder, _fp = _seed_movie(sandbox, "mov-en-2025-f1", "F1 The Movie {tmdb-1003159}")
    mock_tmdb.search["f1"] = [_movie_result(1003159, "F1", 2025)]

    main.cmd_enrich_metadata("mov-en-2025-f1", "--apply")

    out = capsys.readouterr().out
    # Folder UNCHANGED — still the legacy curly name, exactly one token.
    assert folder.is_dir(), out
    assert not (folder.parent / "F1 The Movie [tmdbid-1003159]").exists()
    assert "already has a provider id token" in out
    children = [p.name for p in folder.parent.iterdir()]
    assert not any(c.count("tmdb") > 1 for c in children), children


def _seed_season_leaf(sandbox, show_folder_name, mid, season_dir_name="Season 01"):
    """Seed `Series/<show>/<season>/ep.mkv` + a leaf entry; returns (leaf_dir, show_dir)."""
    show_dir = sandbox["local_root"] / "Series" / show_folder_name
    leaf_dir = show_dir / season_dir_name
    leaf_dir.mkdir(parents=True, exist_ok=True)
    (leaf_dir / "ep01.mkv").write_bytes(b"EP\n" * 40)
    lib = mvcommon.load_library()
    lib[mid] = {
        "short_id": mvcommon.generate_short_id(mid),
        "filename": "ep01.mkv",
        "folder_path": str(leaf_dir),
        "status": "local_ready",
        "uploaded": False,
        "hash": "0" * 64,
        "metadata": main.parse_metadata_from_id(mid),
    }
    mvcommon.save_library(lib)
    return leaf_dir, show_dir


def test_artwork_walk_accepts_new_square_token(sandbox):
    """IMP-U6: rung (iii) of resolve_artwork_path climbs to a show folder carrying
    the CANONICAL square `[tmdbid-…]` token and serves its poster."""
    leaf_dir, show_dir = _seed_season_leaf(sandbox, "Dark [tmdbid-70523]",
                                           "tv-en-2017-dark-s01e01")
    poster = show_dir / "poster.jpg"
    poster.write_bytes(b"SHOW-POSTER")

    lib = mvcommon.load_library()
    hit = main.resolve_artwork_path(lib, "tv-en-2017-dark-s01e01", kind="poster")

    assert hit is not None
    assert main._norm_path(str(poster)) == main._norm_path(hit)


def test_artwork_walk_accepts_tvdbid_only_show_folder(sandbox):
    """IMP-U6: a `[tvdbid-…]`-ONLY show folder (the user's pre-Jellyfin naming)
    now satisfies the artwork walk too — the detector is any-provider/any-shape."""
    leaf_dir, show_dir = _seed_season_leaf(sandbox, "Dark (2017) [tvdbid-334824]",
                                           "tv-en-2017-dark-s01e01")
    poster = show_dir / "poster.jpg"
    poster.write_bytes(b"SHOW-POSTER")

    lib = mvcommon.load_library()
    hit = main.resolve_artwork_path(lib, "tv-en-2017-dark-s01e01", kind="poster")

    assert hit is not None
    assert main._norm_path(str(poster)) == main._norm_path(hit)
