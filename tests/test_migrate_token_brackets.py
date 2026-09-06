"""IMP-U6 — `tools/migrate_token_brackets.py` (the one-shot token migration).

Every test runs against the `sandbox` fixture (LIBRARY_* + LOCAL_ROOT redirected
into tmp_path; hard-guarded against real C:\\Media), and drives the REAL
`cmd_rename_folder` + `_write_nfo` code paths — the same machinery the live
Step-8 migration uses.
"""
import os
import re

import mvcommon
import main
from tools import migrate_token_brackets as mig


def _leaf(path):
    return os.path.basename(os.path.normpath(str(path)))


def _seed_folder(sandbox, rel, ids, leaf_name=None):
    """Create a physical folder under the sandbox local root and register every
    id in `ids` against it. Returns the folder Path."""
    folder = sandbox["local_root"] / rel
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "media.mkv").write_bytes(b"MEDIA\n" * 40)
    lib = mvcommon.load_library()
    for mid in ids:
        entry = {
            "short_id": mvcommon.generate_short_id(mid),
            "status": "local_ready",
            "uploaded": False,
            "hash": "0" * 64,
            "metadata": main.parse_metadata_from_id(mid),
        }
        if re.search(r"-s\d+$", mid):
            # a season folder id -> season_map shape (virtual, no filename)
            entry["type"] = "season_map"
            entry["children"] = []
            entry["total_episodes"] = 0
        else:
            entry["filename"] = "media.mkv"
        entry["folder_path"] = str(folder)
        lib[mid] = entry
    mvcommon.save_library(lib)
    if leaf_name:
        renamed = folder.parent / leaf_name
        folder.rename(renamed)
        lib = mvcommon.load_library()
        for mid in ids:
            lib[mid]["folder_path"] = str(renamed)
        mvcommon.save_library(lib)
        return renamed
    return folder


def test_dry_run_reports_and_touches_nothing(sandbox, capsys):
    curly = _seed_folder(sandbox, "Movies/Curly", ["mov-en-2020-curly"],
                         leaf_name="Curly (2020) {tmdb-59436}")
    lib_before = mvcommon.load_library()

    summary = mig.migrate(apply=False)

    out = capsys.readouterr().out
    assert summary["found"] >= 1 and summary["needs_rename"] >= 1
    assert "DRY-RUN — nothing was touched" in out
    assert "[tmdbid-59436]" in out, "the plan must show the target name"
    # Nothing mutated: folder name + library folder_path unchanged.
    assert curly.exists() and curly.name == "Curly (2020) {tmdb-59436}"
    assert mvcommon.load_library() == lib_before
    assert not list(curly.glob("*.nfo"))


def test_apply_renames_curly_and_writes_nfo(sandbox, capsys):
    folder = _seed_folder(sandbox, "Movies/Curly", ["mov-en-2020-curly"],
                          leaf_name="Curly (2020) {tmdb-59436}")

    summary = mig.migrate(apply=True)

    out = capsys.readouterr().out
    stamped = folder.parent / "Curly (2020) [tmdbid-59436]"
    assert stamped.is_dir(), out
    assert not folder.exists()
    assert summary["renamed"] == 1 and summary["rename_failed"] == 0
    lib = mvcommon.load_library()
    assert lib["mov-en-2020-curly"]["folder_path"] == str(stamped)
    # The D6 NFO backfill: offline, id-first, correct root tag.
    nfo = stamped / "movie.nfo"
    assert nfo.is_file()
    body = nfo.read_text(encoding="utf-8")
    assert "<movie>" in body and "<tmdbid>59436</tmdbid>" in body
    assert summary["nfos_written"] == 1


def test_apply_show_folder_writes_tvshow_nfo(sandbox):
    folder = _seed_folder(sandbox, "Series/DarkCurly", ["tv-en-2017-darkcurly-s01"],
                          leaf_name="Dark (2017) {tmdb-70523}")

    mig.migrate(apply=True)

    stamped = folder.parent / "Dark (2017) [tmdbid-70523]"
    assert stamped.is_dir()
    nfo = stamped / "tvshow.nfo"
    assert nfo.is_file(), "a tv/ani folder gets a tvshow.nfo"
    assert "<tvshow>" in nfo.read_text(encoding="utf-8")


def test_uppercase_curly_and_dedup_and_preserved_tags(sandbox):
    # Uppercase legacy token (the real IMP-C23 folder shape).
    run = _seed_folder(sandbox, "Movies/Run", ["mov-en-2002-run"],
                       leaf_name="Run (2002) {TMDB-69590}")
    # Mixed shape: other-provider square tag preserved, curly converted.
    dark = _seed_folder(sandbox, "Series/DarkMixed", ["tv-en-2017-darkmixed-s01"],
                        leaf_name="Dark (2017) [tvdbid-334824] {tmdb-70523}")
    # Duplicate identity: the curly token is DROPPED, not doubled.
    dup = _seed_folder(sandbox, "Movies/Dup", ["mov-en-2012-dup"],
                       leaf_name="3 (2012) {tmdb-79660} [tmdbid-79660]")

    mig.migrate(apply=True)

    assert (run.parent / "Run (2002) [tmdbid-69590]").is_dir()
    assert (dark.parent / "Dark (2017) [tvdbid-334824] [tmdbid-70523]").is_dir()
    assert (dup.parent / "3 (2012) [tmdbid-79660]").is_dir()
    lib = mvcommon.load_library()
    assert lib["mov-en-2012-dup"]["folder_path"].endswith("3 (2012) [tmdbid-79660]")


def test_old_keyword_square_token_converted(sandbox):
    """The user's own pre-IMP-U6 MANUAL renames used square brackets with the OLD
    keyword — 'John Wick Chapter 4 (2023) [tmdb-603692]' — which Jellyfin/Emby do
    NOT parse. The migration fixes the keyword to the canonical [tmdbid-…]."""
    jw = _seed_folder(sandbox, "Movies/JW4", ["mov-en-2023-johnwick4"],
                      leaf_name="John Wick Chapter 4 (2023) [tmdb-603692]")

    summary = mig.migrate(apply=True)

    stamped = jw.parent / "John Wick Chapter 4 (2023) [tmdbid-603692]"
    assert stamped.is_dir(), "the old-keyword square token must be converted"
    assert not jw.exists()
    assert summary["renamed"] == 1
    lib = mvcommon.load_library()
    assert lib["mov-en-2023-johnwick4"]["folder_path"] == str(stamped)


def test_already_square_folder_gets_only_a_missing_nfo(sandbox):
    folder = _seed_folder(sandbox, "Movies/Square", ["mov-en-2010-square"],
                          leaf_name="Square (2010) [tmdbid-27205]")
    (folder / "metadata").write_bytes(b"{}")  # unrelated file

    summary = mig.migrate(apply=True)

    assert folder.is_dir(), "an already-square folder is never renamed"
    assert summary["renamed"] == 0
    assert summary["nfos_written"] == 1
    assert (folder / "movie.nfo").is_file()


def test_never_overwrites_an_existing_nfo(sandbox):
    folder = _seed_folder(sandbox, "Movies/Kept", ["mov-en-2019-kept"],
                          leaf_name="Kept (2019) {tmdb-550}")
    hand_tuned = b"<movie><title>Hand tuned</title></movie>"
    (folder / "movie.nfo").write_bytes(hand_tuned)

    mig.migrate(apply=True)

    stamped = folder.parent / "Kept (2019) [tmdbid-550]"
    assert stamped.is_dir()
    assert (stamped / "movie.nfo").read_bytes() == hand_tuned


def test_idempotent_rerun_finds_nothing_pending(sandbox):
    _seed_folder(sandbox, "Movies/Idem", ["mov-en-2021-idem"],
                 leaf_name="Idem (2021) {tmdb-847742}")

    first = mig.migrate(apply=True, verbose=False)
    second = mig.migrate(apply=True, verbose=False)

    assert first["renamed"] == 1
    # The re-run still LISTS the folder (now square) but renames nothing and
    # writes no NFO — the definition of idempotent for this tool.
    assert second["needs_rename"] == 0 and second["renamed"] == 0
    assert second["nfos_written"] == 0 and second["nfos_kept"] == 1


def test_library_filter_and_limit_and_call_order(sandbox, monkeypatch):
    a = _seed_folder(sandbox, "Movies/A", ["mov-en-2001-aaa"], leaf_name="Aaa (2001) {tmdb-1}")
    b = _seed_folder(sandbox, "Movies/B", ["mov-en-2002-bbb"], leaf_name="Bbb (2002) {tmdb-2}")
    s = _seed_folder(sandbox, "Series/S", ["tv-en-2003-sss-s01"], leaf_name="Sss (2003) {tmdb-3}")

    real_rename = main.cmd_rename_folder
    calls = []

    def recorder(old, new):
        calls.append((_leaf(old), new))
        return real_rename(old, new)

    monkeypatch.setattr(main, "cmd_rename_folder", recorder)

    # --library series: only the show folder is in the plan.
    summary_series = mig.migrate(apply=True, library_filter="series", verbose=False)
    assert summary_series["renamed"] == 1 and calls == [("Sss (2003) {tmdb-3}", "Sss (2003) [tmdbid-3]")]

    # --limit 1 on movies: exactly one folder, sorted order (A before B).
    calls.clear()
    summary_limit = mig.migrate(apply=True, library_filter="movies", limit=1, verbose=False)
    assert summary_limit["renamed"] == 1
    assert calls[0][0] == "Aaa (2001) {tmdb-1}"

    # Remaining movie folder migrates on the next run; order stays sorted.
    mig.migrate(apply=True, library_filter="movies", verbose=False)
    assert (a.parent / "Aaa (2001) [tmdbid-1]").is_dir()
    assert (b.parent / "Bbb (2002) [tmdbid-2]").is_dir()
    assert (s.parent / "Sss (2003) [tmdbid-3]").is_dir()
