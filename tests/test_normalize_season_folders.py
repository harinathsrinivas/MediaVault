"""IMP-U6 Steps 15-16 — cmd_normalize_season_folders, the structural season-folder
rename, and its TMDB-id-based CATEGORY-FOLDER GUARD (_season_parent_is_exclusive_to_show
/ _folder_tmdb_owner_ids).

Two real-library defects motivate this file:

  DEFECT 1 — the guard used to compare `_show_id_of` LIBRARY KEYS, not TMDB ids.
  MediaVault's library ids embed the SEASON's own air year (Peaky Blinders
  S01..S06 -> 6 DISTINCT `tv-en-<year>-peakyblinders-s0N` keys, all sharing ONE
  TMDB id, 60574), so a show's own sibling seasons looked like foreign titles
  and the guard refused almost everything on the real library (scanned=64,
  seasons_would_rename=12, skipped=52). Fixed by `_folder_tmdb_owner_ids`
  (TMDB-id-based ownership, not a library-key set) — cases 1, 4, 5 pin this
  directly against the guard functions.

  DEFECT 2 — (already fixed before this file was written) a parent that is a
  DIRECT CHILD of a CATEGORY_ROOT is a language folder and can never be any
  one show's own folder, no matter how few occupants it currently has (the
  real `C:\\Media\\Series\\Tamil` shape) — case 2 pins this.

Fixtures only — `sandbox` (redirects both `mvcommon.LIBRARY_*` and
`main.LIBRARY_*`, the IMP-A1 binding hazard, plus LOCAL_ROOT, which
CATEGORY_ROOTS' structural gate is derived from) and `mock_tmdb` (extended in
conftest.py with a `/tv/{id}` details branch for Phase B's show-name/air-year
lookup). NEVER touches real C:\\Media files or real library_*.json: every path
this file creates or asserts on is run through `_guard()` below.
"""
import json
import os

import main
import mvcommon


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

REPORT_LINE_PREFIX = "   > report: "
REPORT_KEYS = {"scanned", "show_folders_tokened", "seasons_renamed",
               "already_normalized", "skipped", "errors"}


def _guard(sandbox, *paths):
    """Hard guard (docs/testing-strategy.md §8.5): every path this file creates
    or asserts on must live under the sandbox LOCAL_ROOT, never under real
    C:\\Media. Mirrors tests/test_migrate_provider_tokens.py::_guard."""
    root_norm = main._norm_path(str(sandbox["local_root"]))
    assert "C:\\Media" not in root_norm, "sandbox LOCAL_ROOT escaped to real media!"
    for p in paths:
        p_norm = main._norm_path(str(p))
        assert "C:\\Media" not in p_norm, f"path must never touch real C:\\Media: {p}"
        assert main._is_under(p_norm, root_norm), f"path escaped the sandbox LOCAL_ROOT: {p}"


def _seed(sandbox, library):
    """Persist `library` through the REAL `mvcommon.save_library` (routes each
    id into the right sandbox library_*.json by prefix)."""
    _guard(sandbox, *[e["folder_path"] for e in library.values() if e.get("folder_path")])
    mvcommon.save_library(library)


def _lib():
    """Re-read the merged library from the sandbox JSON files."""
    return mvcommon.load_library()


def _tree(root):
    """Sorted relative listing of everything under `root` — the filesystem
    fingerprint a dry-run/skip must leave byte-identical."""
    return sorted(str(p.relative_to(root)) for p in root.rglob("*"))


def _season_map(folder, children=None, **overrides):
    """A minimal season_map entry pointed at `folder` (a Path). `folder` need
    not contain any file — cmd_rename_folder only requires the DIRECTORY to
    exist on disk and at least one library entry (this one) to reference it;
    it never re-hashes or touches file contents."""
    entry = {"type": "season_map", "folder_path": str(folder),
              "total_episodes": len(children or []), "children": children or []}
    entry.update(overrides)
    return entry


def _tv_detail(name, season_air_dates):
    """A minimal `/tv/{id}` details payload for MockTMDB.tv_details:
    {season_number: 'YYYY-MM-DD', ...} -> the documented details shape."""
    return {"name": name,
            "seasons": [{"season_number": n, "air_date": d} for n, d in season_air_dates.items()]}


def _read_report(capsys, sandbox):
    """Drain stdout, pull the `--apply` report path out of it, and load the
    JSON. Mirrors tests/test_migrate_provider_tokens.py::_read_report."""
    out = capsys.readouterr().out
    lines = [ln for ln in out.splitlines() if ln.startswith(REPORT_LINE_PREFIX)]
    assert len(lines) == 1, f"expected exactly one report line, got {lines!r}"
    path = lines[0][len(REPORT_LINE_PREFIX):].strip()
    _guard(sandbox, path)
    assert os.path.basename(path).startswith("season_folders_")
    assert os.path.basename(os.path.dirname(path)) == "migration_reports"
    with open(path, "r", encoding="utf-8") as f:
        return out, json.load(f), path


# ---------------------------------------------------------------------------
# (1) DEFECT 1 — the year-drift case (Peaky Blinders shape), pinned directly
# against the guard functions.
# ---------------------------------------------------------------------------

def test_year_drift_seasons_share_one_tmdb_id_guard_passes(sandbox):
    """Peaky Blinders' real shape: seasons whose library ids embed the
    SEASON's own air year (2013, 2014, 2016, 2022 — not the show's), so
    `_show_id_of` returns DISTINCT keys per season, but all of them share ONE
    TMDB id (60574). The OLD guard compared `_show_id_of` keys and refused
    this parent almost every time; the FIX compares TMDB ids, so this must
    pass cleanly."""
    show = sandbox["local_root"] / "Series" / "English" / "Peaky Blinders"
    seasons = {
        "tv-en-2013-peaky-s01": "Peaky.Blinders.S01.2013 [tmdbid-60574]",
        "tv-en-2014-peaky-s02": "Peaky.Blinders.S02.2014 [tmdbid-60574]",
        "tv-en-2016-peaky-s03": "Peaky.Blinders.S03.2016 [tmdbid-60574]",
        "tv-en-2022-peaky-s06": "Peaky.Blinders.S06.2022 [tmdbid-60574]",
    }
    library = {}
    for mid, name in seasons.items():
        folder = show / name
        folder.mkdir(parents=True)
        library[mid] = _season_map(folder)
    _seed(sandbox, library)

    # Prove the year-drift is real: _show_id_of gives 4 DISTINCT keys here —
    # without this, the rest of the test would not actually exercise defect 1.
    show_keys = {main._show_id_of(mid, library[mid], library) for mid in seasons}
    assert len(show_keys) == 4

    exclusive, reason = main._season_parent_is_exclusive_to_show(library, str(show), "60574")
    assert (exclusive, reason) == (True, None)


# ---------------------------------------------------------------------------
# (2) DEFECT 2 — language-folder refusal (kept, pinned here).
# ---------------------------------------------------------------------------

def test_language_folder_refused_via_structural_gate(sandbox):
    """A parent that is a DIRECT CHILD of a CATEGORY_ROOT is a language folder
    and can never be any one show's own folder — even when it currently holds
    exactly one occupant whose name carries a season marker (the real
    `Series/Tamil` shape: its only occupant, `Aindham Vedham (2024) S01 EP
    (01-08) ...`, genuinely contains 'S01', so the sibling rule ALONE would
    not catch this)."""
    tamil = sandbox["local_root"] / "Series" / "Tamil"
    folder = tamil / "Aindham Vedham (2024) S01 EP (01-08) [tmdbid-274276]"
    folder.mkdir(parents=True)
    library = {"tv-ta-2024-aindhamvedham-s01": _season_map(folder)}
    _seed(sandbox, library)

    exclusive, reason = main._season_parent_is_exclusive_to_show(library, str(tamil), "274276")
    assert exclusive is False
    assert "language folder" in reason


# ---------------------------------------------------------------------------
# (3) Genre-folder refusal — two unrelated shows sharing one parent.
# ---------------------------------------------------------------------------

def test_genre_folder_refused_two_unrelated_shows(sandbox):
    """A Classic-shaped parent holding two DIFFERENT shows' season folders
    directly (the ONE-hop level the guard actually checks): a sibling with a
    DIFFERENT TMDB id refuses the whole parent."""
    classic = sandbox["local_root"] / "Series" / "English" / "Classic"
    folder_a = classic / "Show A Season 01 (2020)"
    folder_b = classic / "Show B Season 01 (2021)"
    folder_a.mkdir(parents=True)
    folder_b.mkdir(parents=True)
    library = {
        "tv-en-2020-showa-s01": _season_map(folder_a, metadata={"tmdb_id": 111}),
        "tv-en-2021-showb-s01": _season_map(folder_b, metadata={"tmdb_id": 222}),
    }
    _seed(sandbox, library)

    exclusive, reason = main._season_parent_is_exclusive_to_show(library, str(classic), "111")
    assert exclusive is False
    assert "another title's folder" in reason


# ---------------------------------------------------------------------------
# (4) Untracked sibling tolerated — the Mr.Robot S04 case.
# ---------------------------------------------------------------------------

def test_untracked_sibling_tolerated(sandbox):
    """Mr.Robot's real shape: library-tracked seasons + one un-prepped
    directory the library knows nothing about. The untracked sibling is
    harmless (it rides along for free when the parent is renamed) and must
    NOT cause a refusal."""
    show = sandbox["local_root"] / "Series" / "English" / "Thriller" / "Mr.Robot"
    s01 = show / "Mr.Robot.S01.2015 [tmdbid-62560]"
    s01.mkdir(parents=True)
    untracked = show / "Mr.Robot.S04.BluRay.2019.1080p.DTS"
    untracked.mkdir(parents=True)  # no library entry at all
    library = {"tv-en-2015-mrrobot-s01": _season_map(s01)}
    _seed(sandbox, library)

    exclusive, reason = main._season_parent_is_exclusive_to_show(library, str(show), "62560")
    assert (exclusive, reason) == (True, None)


# ---------------------------------------------------------------------------
# (5) Ambiguous sibling refused — tracked content, no discoverable TMDB id.
# ---------------------------------------------------------------------------

def test_ambiguous_sibling_refused(sandbox):
    """A sibling WITH library-tracked content but no discoverable TMDB id
    anywhere (no folder-name token, no metadata.tmdb_id) is refused —
    conservatively — rather than treated as 'no conflict'."""
    show = sandbox["local_root"] / "Series" / "English" / "Show"
    s01 = show / "Show.S01.2020 [tmdbid-999]"
    s01.mkdir(parents=True)
    ambiguous = show / "Extras"
    ambiguous.mkdir(parents=True)
    library = {
        "tv-en-2020-show-s01": _season_map(s01),
        "tv-en-2020-show-extras": _season_map(ambiguous),  # no token, no metadata.tmdb_id
    }
    _seed(sandbox, library)

    exclusive, reason = main._season_parent_is_exclusive_to_show(library, str(show), "999")
    assert exclusive is False
    assert "ambiguous" in reason


# ---------------------------------------------------------------------------
# (6) Phase A + Phase B end to end — the Peaky-Blinders shape.
# ---------------------------------------------------------------------------

def test_phase_a_and_b_end_to_end_peaky_blinders_shape(sandbox, mock_tmdb, capsys):
    """The id lives ONLY on the season folders (show folder untokened): Phase
    A moves it up onto the show folder, Phase B strips it from every season
    name."""
    show = sandbox["local_root"] / "Series" / "English" / "Peaky Blinders"
    s01 = show / "Peaky.Blinders.S01.2013 [tmdbid-60574]"
    s02 = show / "Peaky.Blinders.S02.2014 [tmdbid-60574]"
    s01.mkdir(parents=True)
    s02.mkdir(parents=True)
    library = {
        "tv-en-2013-peaky-s01": _season_map(s01),
        "tv-en-2014-peaky-s02": _season_map(s02),
    }
    _seed(sandbox, library)
    mock_tmdb.tv_details[60574] = _tv_detail("Peaky Blinders", {1: "2013-09-12", 2: "2014-10-02"})

    main.cmd_normalize_season_folders("--apply")
    _out, report, _path = _read_report(capsys, sandbox)

    tok = mvcommon.CANONICAL_TMDB_TOKEN_FMT.format(id=60574)
    new_show = show.parent / f"Peaky Blinders {tok}"
    new_s01 = new_show / "Peaky Blinders Season 01 (2013)"
    new_s02 = new_show / "Peaky Blinders Season 02 (2014)"
    assert not show.exists()
    assert new_show.is_dir()
    assert new_s01.is_dir()
    assert new_s02.is_dir()
    assert mvcommon.has_tmdb_token(new_s01.name) is False
    assert mvcommon.has_tmdb_token(new_show.name) is True

    lib = _lib()
    assert main._norm_path(lib["tv-en-2013-peaky-s01"]["folder_path"]) == main._norm_path(str(new_s01))
    assert main._norm_path(lib["tv-en-2014-peaky-s02"]["folder_path"]) == main._norm_path(str(new_s02))

    assert len(report["show_folders_tokened"]) == 1
    assert report["show_folders_tokened"][0]["id_source"] == "season folder's own token"
    assert len(report["seasons_renamed"]) == 2
    assert report["errors"] == []


# ---------------------------------------------------------------------------
# (7) Friends shape — season loses its id, show folder keeps it.
# ---------------------------------------------------------------------------

def test_friends_shape_season_loses_id_show_keeps_it(sandbox, mock_tmdb, capsys):
    """The show folder is ALREADY tokened (Phase A is a no-op); the season
    folder still carries the old-style id and loses it in Phase B."""
    tok = mvcommon.CANONICAL_TMDB_TOKEN_FMT.format(id=1668)
    show = sandbox["local_root"] / "Series" / "English" / "Classic" / f"Friends (1994) {tok}"
    s01 = show / "Friends Season 01 (1994) [tmdbid-1668]"
    s01.mkdir(parents=True)
    library = {"tv-en-1994-friends-s01": _season_map(s01)}
    _seed(sandbox, library)
    mock_tmdb.tv_details[1668] = _tv_detail("Friends", {1: "1994-09-22"})

    main.cmd_normalize_season_folders("--apply")
    _out, report, _path = _read_report(capsys, sandbox)

    new_s01 = show / "Friends Season 01 (1994)"
    assert show.is_dir()  # show folder's OWN name untouched -- Phase A skipped
    assert show.name == f"Friends (1994) {tok}"
    assert not s01.exists()
    assert new_s01.is_dir()
    assert mvcommon.has_tmdb_token(new_s01.name) is False

    assert report["show_folders_tokened"] == []  # already tokened -> Phase A no-op
    assert len(report["seasons_renamed"]) == 1
    assert report["errors"] == []


# ---------------------------------------------------------------------------
# (8) Flat show left structurally untouched.
# ---------------------------------------------------------------------------

def test_flat_show_left_structurally_untouched(sandbox, mock_tmdb, capsys):
    """Chernobyl's real shape: a single season_map id with NO `-sNN` suffix
    (one folder is both show and season). `_season_number_of` cannot parse a
    season number from it, so it is skipped and left byte-identical —
    migrate_provider_tokens (a different command) already fixes its id."""
    classic = sandbox["local_root"] / "Series" / "English" / "Classic"
    tok = mvcommon.CANONICAL_TMDB_TOKEN_FMT.format(id=87108)
    flat = classic / f"Chernobyl (Miniseries) 2019 {tok}"
    flat.mkdir(parents=True)
    library = {"tv-en-2019-chernobyl": _season_map(flat, metadata={"tmdb_id": 87108})}
    _seed(sandbox, library)

    lib_before = json.dumps(_lib(), sort_keys=True)
    # `--apply` always writes its own JSON report (even when nothing else
    # changed), so the tree fingerprint is taken excluding `migration_reports/`
    # — everything ELSE must stay byte-identical.
    tree_before = [p for p in _tree(sandbox["local_root"]) if not p.startswith("migration_reports")]

    main.cmd_normalize_season_folders("--apply")
    _out, report, _path = _read_report(capsys, sandbox)

    tree_after = [p for p in _tree(sandbox["local_root"]) if not p.startswith("migration_reports")]
    assert tree_after == tree_before
    assert json.dumps(_lib(), sort_keys=True) == lib_before
    assert report["show_folders_tokened"] == []
    assert report["seasons_renamed"] == []
    assert report["scanned"] == 1
    assert len(report["skipped"]) == 1
    assert "season number" in report["skipped"][0]["reason"]


# ---------------------------------------------------------------------------
# BEYOND THE 12 CASES — regression pin for a real-data-confirmed gap this fix
# introduced (found while writing case 9, then verified against the real
# library: Friends and The X-Files carry NO metadata.tmdb_id on ANY season —
# only Peaky Blinders/Dark/Stranger Things partially do). Once Phase B strips
# a season's OWN folder token, a season with no metadata.tmdb_id becomes
# unidentifiable to _folder_tmdb_owner_ids, and the category-folder guard
# would wrongly refuse the WHOLE show on a later run. Fixed by persisting
# metadata.tmdb_id during --apply (see cmd_normalize_season_folders' docstring,
# "IDEMPOTENT BY RE-DETECTION").
# ---------------------------------------------------------------------------

def test_already_tokened_show_with_no_metadata_stays_idempotent(sandbox, mock_tmdb, capsys):
    """The EXACT real Friends/X-Files shape: the show folder is ALREADY
    tokened (Phase A is a no-op on every run), every season's ONLY id source
    is its own folder name, and NO season carries metadata.tmdb_id. A second
    `--apply` (after the first stripped every season's own token) must still
    recognize the show and report it as fully normalized — not refuse it as
    'ambiguous'."""
    tok = mvcommon.CANONICAL_TMDB_TOKEN_FMT.format(id=4087)
    show = sandbox["local_root"] / "Series" / "English" / "Sci-Fi" / f"The X-Files (1993) {tok}"
    s01 = show / "The X-Files Season 01 (1993) [tmdbid-4087]"
    s02 = show / "The X-Files Season 02 (1994) [tmdbid-4087]"
    s01.mkdir(parents=True)
    s02.mkdir(parents=True)
    library = {
        # No `metadata` key at all — mirrors the real, unenriched season_map shape.
        "tv-en-1993-xfiles-s01": _season_map(s01),
        "tv-en-1994-xfiles-s02": _season_map(s02),
    }
    _seed(sandbox, library)
    mock_tmdb.tv_details[4087] = _tv_detail("The X-Files", {1: "1993-09-10", 2: "1994-09-16"})

    main.cmd_normalize_season_folders("--apply")
    _out1, report1, _path1 = _read_report(capsys, sandbox)
    assert len(report1["seasons_renamed"]) == 2
    assert report1["errors"] == []

    # Both seasons' folder-name tokens are now gone, AND neither had
    # metadata.tmdb_id before this run — without the fix, THIS run would
    # refuse the whole show as ambiguous.
    lib_mid = _lib()
    assert lib_mid["tv-en-1993-xfiles-s01"]["metadata"]["tmdb_id"] == 4087
    assert lib_mid["tv-en-1994-xfiles-s02"]["metadata"]["tmdb_id"] == 4087

    main.cmd_normalize_season_folders("--apply")
    _out2, report2, _path2 = _read_report(capsys, sandbox)

    assert report2["errors"] == []
    assert report2["skipped"] == []
    assert report2["seasons_renamed"] == []
    assert report2["already_normalized"] == 2
    assert report2["show_folders_tokened"] == []


# ---------------------------------------------------------------------------
# (9) Idempotent re-run — a second --apply is a no-op.
# ---------------------------------------------------------------------------

def test_apply_is_idempotent_on_rerun(sandbox, mock_tmdb, capsys):
    """Resumability comes from re-detection alone (no state file, mirrors
    migrate_provider_tokens): a second `--apply` tokens 0 show folders,
    renames 0 seasons, and leaves the library byte-identical."""
    show = sandbox["local_root"] / "Series" / "English" / "Devs"
    s01 = show / "Devs.S01.2020 [tmdbid-81349]"
    s01.mkdir(parents=True)
    library = {"tv-en-2020-devs-s01": _season_map(s01)}
    _seed(sandbox, library)
    mock_tmdb.tv_details[81349] = _tv_detail("Devs", {1: "2020-03-05"})

    main.cmd_normalize_season_folders("--apply")
    _out1, report1, path1 = _read_report(capsys, sandbox)
    lib_after_first = _lib()

    main.cmd_normalize_season_folders("--apply")
    _out2, report2, path2 = _read_report(capsys, sandbox)

    assert len(report1["show_folders_tokened"]) == 1
    assert len(report1["seasons_renamed"]) == 1
    assert report2["show_folders_tokened"] == []
    assert report2["seasons_renamed"] == []
    assert report2["already_normalized"] == 1
    assert report2["errors"] == []
    assert _lib() == lib_after_first
    assert path1 != path2  # both reports survive -- de-collision


# ---------------------------------------------------------------------------
# (10) Dry-run purity — the default is a pure reporter.
# ---------------------------------------------------------------------------

def test_dry_run_purity_no_rename_calls_and_tree_untouched(sandbox, mock_tmdb, monkeypatch, capsys):
    """Without `--apply`, cmd_rename_folder is never called (monkeypatch
    spy), and the on-disk tree + library JSON are byte-identical afterwards —
    even though the preview still resolves the real TMDB name/year to make
    the printed preview meaningful."""
    show = sandbox["local_root"] / "Series" / "English" / "Devs"
    s01 = show / "Devs.S01.2020 [tmdbid-81349]"
    s01.mkdir(parents=True)
    library = {"tv-en-2020-devs-s01": _season_map(s01)}
    _seed(sandbox, library)
    mock_tmdb.tv_details[81349] = _tv_detail("Devs", {1: "2020-03-05"})

    calls = []

    def _spy(*args, **kwargs):
        calls.append((args, kwargs))
        return True

    monkeypatch.setattr(main, "cmd_rename_folder", _spy)

    tree_before = _tree(sandbox["local_root"])
    lib_before = json.dumps(_lib(), sort_keys=True)

    main.cmd_normalize_season_folders()  # no --apply -> DRY-RUN
    out = capsys.readouterr().out

    assert calls == [], f"dry-run must never call cmd_rename_folder, got {calls!r}"
    assert _tree(sandbox["local_root"]) == tree_before
    assert json.dumps(_lib(), sort_keys=True) == lib_before
    assert not (sandbox["local_root"] / "migration_reports").exists()

    assert "DRY-RUN" in out
    assert "show_folders_would_token=1" in out
    assert "seasons_would_rename=1" in out


# ---------------------------------------------------------------------------
# (11) Season 00 handled.
# ---------------------------------------------------------------------------

def test_season_00_specials_handled(sandbox, mock_tmdb, capsys):
    """A `-s00` (specials) season is a valid season number (0), not a falsy
    'missing' value — it must be renamed exactly like any other season."""
    show = sandbox["local_root"] / "Series" / "English" / "ShowWithSpecials"
    s00 = show / "Show.S00.Specials.2020 [tmdbid-999]"
    s01 = show / "Show.S01.2020 [tmdbid-999]"
    s00.mkdir(parents=True)
    s01.mkdir(parents=True)
    library = {
        "tv-en-2020-showspecials-s00": _season_map(s00),
        "tv-en-2020-showspecials-s01": _season_map(s01),
    }
    _seed(sandbox, library)
    mock_tmdb.tv_details[999] = _tv_detail("ShowWithSpecials", {0: "2020-01-01", 1: "2020-03-01"})

    main.cmd_normalize_season_folders("--apply")
    _out, report, _path = _read_report(capsys, sandbox)

    tok = mvcommon.CANONICAL_TMDB_TOKEN_FMT.format(id=999)
    new_show = show.parent / f"ShowWithSpecials {tok}"
    new_s00 = new_show / "ShowWithSpecials Season 00 (2020)"
    new_s01 = new_show / "ShowWithSpecials Season 01 (2020)"
    assert new_s00.is_dir()
    assert new_s01.is_dir()
    assert len(report["seasons_renamed"]) == 2
    assert report["errors"] == []


# ---------------------------------------------------------------------------
# (12) No season folder ends up with an id in its name.
# ---------------------------------------------------------------------------

def test_no_season_folder_ends_up_with_id_in_name(sandbox, mock_tmdb, capsys):
    """Broad sweep across a two-show fixture (one needing Phase A, one
    already tokened) — after --apply, no SEASON folder anywhere carries a
    TMDB token, on disk or in the report."""
    show1 = sandbox["local_root"] / "Series" / "English" / "Show One"
    s1 = show1 / "Show.One.S01.2018 [tmdbid-100]"
    s1.mkdir(parents=True)
    show2_tok = mvcommon.CANONICAL_TMDB_TOKEN_FMT.format(id=200)
    show2 = sandbox["local_root"] / "Series" / "English" / f"Show Two {show2_tok}"
    s2 = show2 / "Show Two Season 01 (2019) [tmdbid-200]"
    s2.mkdir(parents=True)
    library = {
        "tv-en-2018-showone-s01": _season_map(s1),
        "tv-en-2019-showtwo-s01": _season_map(s2),
    }
    _seed(sandbox, library)
    mock_tmdb.tv_details[100] = _tv_detail("Show One", {1: "2018-05-01"})
    mock_tmdb.tv_details[200] = _tv_detail("Show Two", {1: "2019-06-01"})

    main.cmd_normalize_season_folders("--apply")
    _out, report, _path = _read_report(capsys, sandbox)

    assert len(report["seasons_renamed"]) == 2
    for rec in report["seasons_renamed"]:
        season_name = os.path.basename(rec["new_folder"])
        assert mvcommon.has_tmdb_token(season_name) is False

    english = sandbox["local_root"] / "Series" / "English"
    season_dirs = [p for p in english.rglob("*") if p.is_dir() and "Season" in p.name]
    assert len(season_dirs) == 2
    for d in season_dirs:
        assert mvcommon.has_tmdb_token(d.name) is False
