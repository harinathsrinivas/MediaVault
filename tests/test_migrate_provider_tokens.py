"""IMP-U6 — the provider-token format migration command (cmd_migrate_provider_tokens).

Migrates every on-disk folder still carrying an OLD tmdb spelling (a square
`[tmdb-…]`/`[tmdbid-…]`/`[tmdbid=…]`, or a wrong-cased curly `{TMDB-…}`) to the
canonical `{tmdb-…}`. Discovery is ANCESTOR-AWARE (it climbs from every physical entry's
`folder_path` up to — not past — LOCAL_ROOT) and renames DEEPEST-FIRST, so a
show folder no entry's `folder_path` names directly is caught and every folder
is renamed while its own path is still the one on disk. Every rename goes
through the existing, crash-safe `cmd_rename_folder` (IMP-D17) — this command
adds no journal, no PONR and no rollback-contract change of its own.

This is the ONE command in IMP-U6 that will be run against the user's REAL
media folders, so these tests pin the behaviour that protects them: the exact
resulting folder STRINGS, the hash/status/uploaded no-touch guarantee, that a
dry-run writes absolutely nothing, and that the deepest-first sort key cannot be
refactored away silently.

Fixtures only — `sandbox` (which redirects BOTH `mvcommon.LIBRARY_*` and
`main.LIBRARY_*`, the IMP-A1 binding hazard, plus LOCAL_ROOT) and `tmp_path`.
NEVER touches real C:\\Media files or real library_*.json: on top of the
fixture's own hard-guard, every path this file creates or asserts on is run
through `_guard()` below. Run `pytest -q` and fix failures before marking the
step done.
"""
import hashlib
import json
import os

import pytest

import main
import mvcommon


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# The exact line cmd_migrate_provider_tokens prints for its --apply report.
REPORT_LINE_PREFIX = "   > report: "

# The documented report shapes (locked by Step 6; asserted in case 7).
REPORT_KEYS = {"scanned", "renamed", "already_canonical", "errors"}
RENAMED_KEYS = {"id_or_note", "old_folder", "new_folder", "remote_bearing"}


def _guard(sandbox, *paths):
    """Hard guard (standing rule, docs/testing-strategy.md §8.5): every path this
    file creates or asserts on MUST live under the sandbox LOCAL_ROOT and can
    never resolve under the real C:\\Media. The `sandbox` fixture already guards
    the LIBRARY_*/LOCAL_ROOT constants; this guards the folders the tests build
    on top of them, so a future fixture mistake fails loudly instead of renaming
    somebody's real media."""
    root_norm = main._norm_path(str(sandbox["local_root"]))
    assert "C:\\Media" not in root_norm, "sandbox LOCAL_ROOT escaped to real media!"
    for p in paths:
        p_norm = main._norm_path(str(p))
        assert "C:\\Media" not in p_norm, f"path must never touch real C:\\Media: {p}"
        assert main._is_under(p_norm, root_norm), f"path escaped the sandbox LOCAL_ROOT: {p}"


def _seed(sandbox, library):
    """Persist `library` through the REAL `mvcommon.save_library`, which routes
    each id into one of the four sandbox `library_*.json` files by its prefix.
    Uses the fixture's redirection rather than a DIY `load_library` mock
    (docs/testing-strategy.md §8.4)."""
    _guard(sandbox, *[e["folder_path"] for e in library.values() if e.get("folder_path")])
    mvcommon.save_library(library)


def _lib():
    """Re-read the merged library from the sandbox JSON files. `load_library`
    reads mvcommon's OWN LIBRARY_* bindings (IMP-A1), which `sandbox` patches."""
    return mvcommon.load_library()


def _tree(root):
    """Sorted relative listing of everything under `root` — the filesystem
    fingerprint a dry-run must leave byte-identical.

    The `"*"` pattern is deliberate: only the PATTERN is glob-expanded, so
    folder names containing `[...]` are matched fine (the §8.1 anti-pattern is
    about putting brackets INTO the pattern, which this never does)."""
    return sorted(str(p.relative_to(root)) for p in root.rglob("*"))


def _read_report(capsys, sandbox):
    """Drain stdout, pull the `--apply` report path out of it, and load the JSON.

    Reading the path from the command's OWN printed line (rather than globbing
    the folder) is both precise and a de-facto assertion that the documented
    `   > report: <path>` line is still printed — and it stays correct when the
    de-collision suffix kicks in (`…-1.json` sorts BEFORE `….json`, so a
    name-sorted glob would silently pick the wrong file).

    Returns (stdout, report_dict, report_path)."""
    out = capsys.readouterr().out
    lines = [ln for ln in out.splitlines() if ln.startswith(REPORT_LINE_PREFIX)]
    assert len(lines) == 1, f"expected exactly one report line, got {lines!r}"
    path = lines[0][len(REPORT_LINE_PREFIX):].strip()
    _guard(sandbox, path)  # the report must be written INSIDE the sandbox
    assert os.path.basename(path).startswith("token_format_")
    assert os.path.basename(os.path.dirname(path)) == "migration_reports"
    with open(path, "r", encoding="utf-8") as f:
        return out, json.load(f), path


def _seed_archived_movie(sandbox, folder_name):
    """Seed ONE archived movie (tiny DUMMY on disk + a recorded `hash`) in
    `<LOCAL_ROOT>/Movies/<folder_name>`.

    Mirrors tests/test_rename_folder.py::_seed_archived_movie — the hash-safety
    pattern this file deliberately REUSES rather than reinventing: the stored
    hash is the sha256 of the (unrelated) original master, NOT of the dummy,
    exactly as a real archived entry stores it, so "no rehash" is provable.

    Returns (entry_id, folder, recorded_hash)."""
    entry_id = "mov-en-2017-dark-movie"
    folder = sandbox["local_root"] / "Movies" / folder_name
    folder.mkdir(parents=True)
    (folder / "DarkMovie.mkv").write_bytes(b"DUMMY")  # well under DUMMY_MAX_BYTES
    (folder / "uid").write_text("dark-uid-123", encoding="utf-8")  # sidecar must move along
    recorded_hash = hashlib.sha256(b"PRETEND-ORIGINAL-MASTER-BYTES").hexdigest()

    _seed(sandbox, {entry_id: {
        "short_id": mvcommon.generate_short_id(entry_id),
        "filename": "DarkMovie.mkv",
        "folder_path": str(folder),
        "status": "archived",
        "uploaded": True,
        "hash": recorded_hash,
        "metadata": {"title": "Dark", "year": "2017"},
    }})
    return entry_id, folder, recorded_hash


def _movie(entry_id, folder, **overrides):
    """A minimal movie-leaf entry dict pointed at `folder` (a Path)."""
    entry = {
        "short_id": mvcommon.generate_short_id(entry_id),
        "filename": "m.mkv",
        "folder_path": str(folder),
        "status": "local_ready",
        "uploaded": False,
        "hash": hashlib.sha256(entry_id.encode()).hexdigest(),
    }
    entry.update(overrides)
    return entry


# ---------------------------------------------------------------------------
# (1) leaf-only rename — the entry's OWN folder carries the old token
# ---------------------------------------------------------------------------

def test_leaf_only_rename_is_hash_and_status_safe(sandbox, capsys):
    """The entry's own folder carries `[tmdbid-…]` — the exact shape the user's
    live library is in today — with no ancestor involved: it is renamed to the
    canonical `{tmdb-…}`, `folder_path` is re-pointed, and the
    entry's `hash`/`status`/`uploaded`/`filename` are byte-identical afterwards
    (same hash-safety assertions as test_rename_folder.py — this command only
    ever moves a directory and rewrites path strings)."""
    entry_id, folder, recorded_hash = _seed_archived_movie(sandbox, "DarkMovie [tmdbid-70523]")
    new_folder = folder.parent / f"DarkMovie {mvcommon.CANONICAL_TMDB_TOKEN_FMT.format(id=70523)}"

    main.cmd_migrate_provider_tokens("--apply")
    _out, report, _path = _read_report(capsys, sandbox)

    # On disk: old gone, new present, contents (incl. the sidecar) carried along.
    assert not folder.exists()
    assert new_folder.is_dir()
    assert (new_folder / "DarkMovie.mkv").read_bytes() == b"DUMMY"
    assert (new_folder / "uid").read_text(encoding="utf-8") == "dark-uid-123"

    entry = _lib()[entry_id]
    assert main._norm_path(entry["folder_path"]) == main._norm_path(str(new_folder))
    # HASH-SAFE: the recorded hash string is untouched (no rehash of the dummy).
    assert entry["hash"] == recorded_hash
    assert entry["status"] == "archived"
    assert entry["uploaded"] is True
    assert entry["filename"] == "DarkMovie.mkv"

    # `scanned` = own folder + every ancestor up to (not incl.) LOCAL_ROOT,
    # deduplicated -> here exactly {DarkMovie …, Movies}.
    assert report["scanned"] == 2
    assert report["already_canonical"] == 0
    assert report["errors"] == []
    assert len(report["renamed"]) == 1
    assert report["renamed"][0]["id_or_note"] == entry_id
    assert report["renamed"][0]["remote_bearing"] is True  # archived + uploaded


# ---------------------------------------------------------------------------
# (2) ancestor-only rename — the EXACT real "Friends" shape
# ---------------------------------------------------------------------------

def test_ancestor_only_rename_friends_shape(sandbox, capsys):
    """The real gap the prior external migration left behind: the SEASON folder
    (which the entries' `folder_path` names) carries a square `[tmdbid-…]`
    token, and its PARENT show folder — named by no entry's folder_path at all —
    carries one too.

    What this pins is ANCESTOR DISCOVERY: the show folder is found and renamed
    even though no `folder_path` names it, and both the season_map and the
    episode leaf end up pointing THROUGH the moved ancestor
    (cmd_rename_folder's cascade). The multi_ep_alias under it is never given a
    folder_path.

    `[tmdbid-…]` is an OLD format, NOT the canonical one: Plex rejects the `id`
    suffix, so the canonical emit is the curly `{tmdb-…}`. BOTH levels are
    deliberately seeded in the `[tmdbid-…]` shape the user's real library is in
    TODAY, which makes this test a true rehearsal of the real run — both migrate,
    deepest-first, leaf before ancestor. Do not "modernise" these fixtures: they
    are the migration's actual input."""
    season_id = "tv-en-1994-friends-s01"
    ep_id = "tv-en-1994-friends-s01e01"
    alias_id = "tv-en-1994-friends-s01e02"

    classic = sandbox["local_root"] / "Series" / "English" / "Classic"
    # Seeded in the EXACT shape the user's real library is in right now. Still
    # recognized tokens (detection is unchanged), but no longer the canonical
    # emit format -> both levels migrate. Do not "modernise" these fixtures.
    show_old_name = "Friends (1994) [tmdbid-1668]"
    show_old = classic / show_old_name
    season_old_name = "Friends Season 01 (1994) [tmdbid-1668]"
    season_old = show_old / season_old_name
    season_old.mkdir(parents=True)
    (season_old / "S01E01.mkv").write_bytes(b"DUMMY")

    _seed(sandbox, {
        season_id: {"type": "season_map", "folder_path": str(season_old),
                    "total_episodes": 2, "children": sorted([ep_id, alias_id])},
        ep_id: _movie(ep_id, season_old, filename="S01E01.mkv", parent_id=season_id),
        # multi_ep_alias — the exact 3-key schema, NO folder_path (PR #21 class).
        alias_id: {"type": "multi_ep_alias", "alias_of": ep_id, "parent_id": season_id},
    })

    canonical_token = mvcommon.CANONICAL_TMDB_TOKEN_FMT.format(id=1668)
    show_new = classic / f"Friends (1994) {canonical_token}"
    season_new_name = f"Friends Season 01 (1994) {canonical_token}"
    season_new = show_new / season_new_name

    main.cmd_migrate_provider_tokens("--apply")
    _out, report, _path = _read_report(capsys, sandbox)

    # BOTH levels moved: the season leaf to the canonical token, and the
    # ancestor around it — with the leaf's file riding along inside.
    assert not show_old.exists()
    assert show_new.is_dir()
    assert season_new.is_dir()
    assert season_new.name == season_new_name
    assert (season_new / "S01E01.mkv").read_bytes() == b"DUMMY"

    lib = _lib()
    assert main._norm_path(lib[season_id]["folder_path"]) == main._norm_path(str(season_new))
    assert main._norm_path(lib[ep_id]["folder_path"]) == main._norm_path(str(season_new))
    alias = lib[alias_id]
    assert set(alias.keys()) == {"type", "alias_of", "parent_id"}

    # Two renames — the season leaf AND the ancestor above it.
    assert len(report["renamed"]) == 2
    by_old = {os.path.basename(r["old_folder"]): r for r in report["renamed"]}
    assert set(by_old) == {season_old_name, show_old_name}

    # THE POINT OF THIS TEST: the show folder is renamed even though no entry's
    # folder_path names it, and it is reported as an ancestor.
    ancestor = by_old[show_old_name]
    assert main._norm_path(ancestor["old_folder"]) == main._norm_path(str(show_old))
    assert main._norm_path(ancestor["new_folder"]) == main._norm_path(str(show_new))
    assert ancestor["id_or_note"].startswith("ancestor of")

    # The season leaf IS named by a folder_path, so it is reported by id rather
    # than as an ancestor. Its recorded new_folder is still UNDER the old show
    # name (deepest-first: the leaf moves before the ancestor around it does),
    # so only the basename is pinned here.
    leaf = by_old[season_old_name]
    assert not leaf["id_or_note"].startswith("ancestor of")
    assert os.path.basename(leaf["new_folder"]) == season_new_name

    assert report["already_canonical"] == 0  # `[tmdbid-…]` is no longer canonical
    assert report["errors"] == []


# ---------------------------------------------------------------------------
# (3) idempotent re-run — a second --apply is a no-op
# ---------------------------------------------------------------------------

def test_apply_is_idempotent_on_rerun(sandbox, capsys):
    """Resumability comes from re-detection alone (no migration state file): a
    second `--apply` finds the folder already canonical, renames 0 and reports 0
    errors, and leaves the entry byte-identical. Also pins the report filename
    de-collision — two runs inside the same wall-clock second must never clobber
    the first run's audit trail."""
    entry_id, folder, _hash = _seed_archived_movie(sandbox, "DarkMovie [tmdbid-70523]")
    new_folder = folder.parent / f"DarkMovie {mvcommon.CANONICAL_TMDB_TOKEN_FMT.format(id=70523)}"

    main.cmd_migrate_provider_tokens("--apply")
    _out1, report1, path1 = _read_report(capsys, sandbox)
    entry_after_first = _lib()[entry_id]

    main.cmd_migrate_provider_tokens("--apply")
    out2, report2, path2 = _read_report(capsys, sandbox)

    assert len(report1["renamed"]) == 1
    assert report2["renamed"] == []
    assert report2["errors"] == []
    assert report2["already_canonical"] == 1  # the folder renamed by the first run
    assert "renamed=0" in out2 and "errors=0" in out2

    # The second run changed nothing at all.
    assert new_folder.is_dir()
    assert _lib()[entry_id] == entry_after_first

    # Both reports survive — the second run de-collided its filename.
    assert path2 != path1
    assert os.path.exists(path1) and os.path.exists(path2)


# ---------------------------------------------------------------------------
# (4) dry-run (the default) writes NOTHING
# ---------------------------------------------------------------------------

def test_dry_run_changes_nothing_and_never_calls_rename_folder(sandbox, monkeypatch, capsys):
    """Without `--apply` the command is a pure reporter: `cmd_rename_folder` is
    never called (monkeypatch spy), the on-disk tree and the library JSON are
    byte-identical afterwards, and NO report file is written (nothing changed,
    so there is nothing to persist)."""
    entry_id, folder, _hash = _seed_archived_movie(sandbox, "DarkMovie [tmdbid-70523]")

    calls = []

    def _spy(*args, **kwargs):
        calls.append((args, kwargs))
        return True

    monkeypatch.setattr(main, "cmd_rename_folder", _spy)

    tree_before = _tree(sandbox["local_root"])
    lib_before = json.dumps(_lib(), sort_keys=True)

    main.cmd_migrate_provider_tokens()  # no --apply -> DRY-RUN
    out = capsys.readouterr().out

    assert calls == [], f"dry-run must never call cmd_rename_folder, got {calls!r}"
    assert _tree(sandbox["local_root"]) == tree_before
    assert json.dumps(_lib(), sort_keys=True) == lib_before
    assert folder.is_dir(), "the old-token folder must still be on disk after a dry-run"
    assert not (sandbox["local_root"] / "migration_reports").exists()

    # It still REPORTS what it would do, in the documented shape.
    assert "DRY-RUN" in out
    assert "would-rename=1" in out
    assert f"{folder} -> " in out
    assert f"DarkMovie {mvcommon.CANONICAL_TMDB_TOKEN_FMT.format(id=70523)}" in out


# ---------------------------------------------------------------------------
# (5) mixed-format library — only the non-canonical tmdb folders move
# ---------------------------------------------------------------------------

def test_mixed_format_library_moves_only_non_canonical_tmdb(sandbox, capsys):
    """Five folders, one per real-world format. Only the three carrying a
    NON-canonical tmdb token move; an already-canonical folder and a
    tvdb-only folder are left exactly as they are. A coexisting `[tvdbid-…]`
    survives byte-identically in the renamed folder's new name, because only the
    tmdb token's own span is rewritten.

    The seeded names below are all REAL formats found in the wild. WHICH of them
    counts as already-canonical is exactly the thing that moves when the emit
    format is flipped: it is now the curly `{tmdb-…}` (row B), and every SQUARE
    spelling migrates. Each row is therefore identified by its FORMAT and never
    by its role, so a future flip cannot leave behind a row called "canonical"
    that is no longer canonical. The totals (3 renamed / 1 already-canonical /
    6 scanned) are unchanged by the flip; only which row sits in which bucket
    moved."""
    movies = sandbox["local_root"] / "Movies"
    cases = {
        "mov-en-2019-tmdbid":   "A Tmdbid (2019) [tmdbid-111]",              # `tmdbid` keyword -> migrate
        "mov-en-2018-curly":    "B Curly (2018) {tmdb-222}",                 # CANONICAL -> untouched
        "mov-en-2017-square":   "C Square (2017) [tmdb-333]",                # bare square -> migrate
        "mov-en-2016-coexist":  "D Coexist (2016) [tvdbid-444] [tmdbid=555]",  # Emby `=` + coexisting tvdb -> migrate
        "mov-en-2015-tvdbonly": "E TvdbOnly (2015) [tvdbid-666]",            # no tmdb -> untouched
    }
    library = {}
    for entry_id, name in cases.items():
        folder = movies / name
        folder.mkdir(parents=True)
        (folder / "m.mkv").write_bytes(b"DUMMY")
        library[entry_id] = _movie(entry_id, folder)
    _seed(sandbox, library)

    main.cmd_migrate_provider_tokens("--apply")
    _out, report, _path = _read_report(capsys, sandbox)

    tok = mvcommon.CANONICAL_TMDB_TOKEN_FMT.format
    expected = {
        "mov-en-2019-tmdbid":   f"A Tmdbid (2019) {tok(id=111)}",
        # Already canonical — seeded and expected byte-identical, never renamed.
        "mov-en-2018-curly":    "B Curly (2018) {tmdb-222}",
        "mov-en-2017-square":   f"C Square (2017) {tok(id=333)}",
        # The `[tvdbid-444]` half is byte-identical — only `[tmdbid=555]` was
        # replaced. This migration NEVER touches a tvdb token's span.
        "mov-en-2016-coexist":  f"D Coexist (2016) [tvdbid-444] {tok(id=555)}",
        "mov-en-2015-tvdbonly": "E TvdbOnly (2015) [tvdbid-666]",
    }
    # "TestMovie" is the `sandbox` fixture's own folder: no library entry points
    # at it, so discovery never walks it and it must survive untouched — proof
    # that this command cannot reach a folder the library does not reference.
    assert sorted(p.name for p in movies.iterdir()) == sorted([*expected.values(), "TestMovie"])

    lib = _lib()
    for entry_id, name in expected.items():
        want = movies / name
        assert want.is_dir()
        assert main._norm_path(lib[entry_id]["folder_path"]) == main._norm_path(str(want))
        assert (want / "m.mkv").read_bytes() == b"DUMMY"

    assert len(report["renamed"]) == 3
    assert report["already_canonical"] == 1  # only "B" — "E" carries no tmdb token at all
    assert report["errors"] == []
    assert report["scanned"] == 6           # the five title folders + "Movies"


# ---------------------------------------------------------------------------
# (6) a `[rartv]` release-group tag is never touched
# ---------------------------------------------------------------------------

def test_rartv_release_group_tag_is_preserved_byte_identical(sandbox, capsys):
    """Mirrors the real `Peaky.Blinders…-FLUX[rartv] …` folder. The bracketed
    release-group tag carries no `<tag><sep><id>` content, so it is not a
    provider token and must survive the rename byte-identically — only the tmdb
    token's span is rewritten. Asserted on the EXACT resulting string."""
    entry_id = "tv-en-2022-peaky-s06e01"
    base = ("Peaky.Blinders.S06.2022.2160p.iP.WEB-DL.x265.10bit.HDR.HLG.DDP5.1-FLUX"
            "[rartv]")
    canonical_token = mvcommon.CANONICAL_TMDB_TOKEN_FMT.format(id=60574)
    # The EXACT real folder — `[rartv]` immediately followed by the `[tmdbid-…]`
    # the prior external migration left behind. Two adjacent square brackets, one
    # a provider token and one not: leave this fixture in its live shape.
    old_name = f"{base} [tmdbid-60574]"
    new_name = f"{base} {canonical_token}"

    series = sandbox["local_root"] / "Series"
    old_folder = series / old_name
    old_folder.mkdir(parents=True)
    (old_folder / "m.mkv").write_bytes(b"DUMMY")
    _seed(sandbox, {entry_id: _movie(entry_id, old_folder)})

    main.cmd_migrate_provider_tokens("--apply")
    _out, report, _path = _read_report(capsys, sandbox)

    new_folder = series / new_name
    assert not old_folder.exists()
    assert new_folder.is_dir()
    # EXACT string — the `[rartv]` substring is untouched and nothing else moved.
    assert new_folder.name == new_name
    assert "[rartv]" in new_folder.name
    assert new_folder.name.replace(f" {canonical_token}", "") == base

    assert main._norm_path(_lib()[entry_id]["folder_path"]) == main._norm_path(str(new_folder))
    assert len(report["renamed"]) == 1
    assert report["renamed"][0]["id_or_note"] == entry_id
    assert report["errors"] == []


# ---------------------------------------------------------------------------
# (7) the --apply JSON report: documented shape + remote_bearing
# ---------------------------------------------------------------------------

def test_apply_report_shape_and_remote_bearing_flag(sandbox, capsys):
    """The report is written under `<LOCAL_ROOT>/migration_reports/` with the
    documented key set, and `remote_bearing` is True both when the renamed
    folder's OWN entry is already pushed (status=archived / uploaded=True) and
    when merely a DESCENDANT of it is (the ancestor case — this is the
    exposure-quantification deliverable: it records which renames touched
    already-pushed content, it does not try to fix the phone side)."""
    movies = sandbox["local_root"] / "Movies"
    # Seeded in the live `[tmdbid-…]` shape, so all three are real candidates.
    pushed = movies / "Pushed (2020) [tmdbid-777]"
    local = movies / "Local (2021) [tmdbid-888]"
    show = sandbox["local_root"] / "Series" / "Show (2019) [tmdbid-999]"
    season = show / "Season 01"
    for d in (pushed, local, season):
        d.mkdir(parents=True)

    season_id, ep_id = "tv-en-2019-show-s01", "tv-en-2019-show-s01e01"
    _seed(sandbox, {
        "mov-en-2020-pushed": _movie("mov-en-2020-pushed", pushed,
                                     status="archived", uploaded=True),
        "mov-en-2021-local": _movie("mov-en-2021-local", local),
        # The show folder itself is named by NO entry — its remote-bearing-ness
        # can only come from the episode leaf two levels below it.
        season_id: {"type": "season_map", "folder_path": str(season),
                    "total_episodes": 1, "children": [ep_id]},
        ep_id: _movie(ep_id, season, status="onboarded", uploaded=True,
                      parent_id=season_id),
    })

    main.cmd_migrate_provider_tokens("--apply")
    _out, report, path = _read_report(capsys, sandbox)

    # --- documented shape ---
    assert set(report.keys()) == REPORT_KEYS
    assert isinstance(report["scanned"], int) and isinstance(report["already_canonical"], int)
    assert report["errors"] == []
    # scanned = Movies, Pushed…, Local…, Series, Show…, Season 01
    assert report["scanned"] == 6
    assert report["already_canonical"] == 0
    assert len(report["renamed"]) == 3
    for rec in report["renamed"]:
        assert set(rec.keys()) == RENAMED_KEYS
        assert isinstance(rec["remote_bearing"], bool)
        _guard(sandbox, rec["old_folder"], rec["new_folder"])

    # --- remote_bearing, per renamed folder ---
    bearing = {os.path.basename(r["old_folder"]): r["remote_bearing"]
               for r in report["renamed"]}
    assert bearing == {
        "Pushed (2020) [tmdbid-777]": True,    # own entry: archived + uploaded
        "Local (2021) [tmdbid-888]": False,    # own entry: local_ready, never pushed
        "Show (2019) [tmdbid-999]": True,      # DESCENDANT episode is onboarded + uploaded
    }
    note = {os.path.basename(r["old_folder"]): r["id_or_note"] for r in report["renamed"]}
    assert note["Pushed (2020) [tmdbid-777]"] == "mov-en-2020-pushed"
    assert note["Show (2019) [tmdbid-999]"].startswith("ancestor of")

    # --- the file really is on disk, in the documented location ---
    report_dir = sandbox["local_root"] / "migration_reports"
    assert report_dir.is_dir()
    assert os.path.dirname(path) == str(report_dir)
    written = sorted(p.name for p in report_dir.iterdir())
    assert len(written) == 1 and written[0].startswith("token_format_") and written[0].endswith(".json")

    # The renames themselves landed.
    tok = mvcommon.CANONICAL_TMDB_TOKEN_FMT.format
    assert (movies / f"Pushed (2020) {tok(id=777)}").is_dir()
    assert (movies / f"Local (2021) {tok(id=888)}").is_dir()
    assert (sandbox["local_root"] / "Series" / f"Show (2019) {tok(id=999)}" / "Season 01").is_dir()


# ---------------------------------------------------------------------------
# (8) BEYOND THE PLAN — two nested ancestors migrated in ONE run
# ---------------------------------------------------------------------------

def test_multi_level_nested_ancestors_migrate_deepest_first(sandbox, monkeypatch, capsys):
    """Regression pin for the DEEPEST-FIRST sort key.

    Step 6's winner argued the multi-level case (two nested ancestors BOTH
    carrying an old token, plus the entry's own folder) was correct by
    construction; the judge proved it empirically during selection. That proof
    lived only in a DECISION.md, so it is pinned here permanently — this is the
    one shape where the ordering is load-bearing rather than incidental.

    Renaming a shallower ancestor FIRST would move a deeper, not-yet-processed
    candidate out from under the path computed for it. So this asserts BOTH the
    call ORDER (deepest -> shallowest) and the composed end state, where
    cmd_rename_folder's own cascade re-points the already-moved descendants
    under each newly-renamed ancestor's prefix."""
    entry_id = "mov-en-2008-tdk"
    movies = sandbox["local_root"] / "Movies"
    coll_old = movies / "Nolan Coll [tmdbid-263]"
    show_old = coll_old / "Batman [tmdbid-120801]"
    title_old = show_old / "The Dark Knight (2008) [tmdbid-155]"
    title_old.mkdir(parents=True)
    (title_old / "m.mkv").write_bytes(b"DUMMY")
    _seed(sandbox, {entry_id: _movie(entry_id, title_old)})

    # Record the ORDER cmd_rename_folder is driven in, then delegate to the real
    # (crash-safe) implementation — the command's behaviour is not stubbed out.
    real_rename = main.cmd_rename_folder
    order = []

    def _recording_rename(old_folder_or_id, new_name):
        order.append(os.path.basename(str(old_folder_or_id)))
        return real_rename(old_folder_or_id, new_name)

    monkeypatch.setattr(main, "cmd_rename_folder", _recording_rename)

    main.cmd_migrate_provider_tokens("--apply")
    _out, report, _path = _read_report(capsys, sandbox)

    # DEEPEST-FIRST: own folder, then its parent, then its grandparent. Derived
    # from the seeded Paths so the old-format literals live in exactly one place.
    assert order == [title_old.name, show_old.name, coll_old.name]

    # End state: all three levels canonical, the file still inside, one entry
    # re-pointed through BOTH renamed ancestors.
    tok = mvcommon.CANONICAL_TMDB_TOKEN_FMT.format
    title_new = (movies / f"Nolan Coll {tok(id=263)}" / f"Batman {tok(id=120801)}"
                 / f"The Dark Knight (2008) {tok(id=155)}")
    assert not coll_old.exists()
    assert title_new.is_dir()
    assert (title_new / "m.mkv").read_bytes() == b"DUMMY"
    assert main._norm_path(_lib()[entry_id]["folder_path"]) == main._norm_path(str(title_new))

    assert len(report["renamed"]) == 3
    assert report["errors"] == []
    assert report["already_canonical"] == 0

    # A re-run over the now-fully-canonical tree is a no-op (resumability holds
    # across the nested case too, not just the flat one).
    main.cmd_migrate_provider_tokens("--apply")
    _out2, report2, _path2 = _read_report(capsys, sandbox)
    assert report2["renamed"] == [] and report2["errors"] == []
    assert report2["already_canonical"] == 3
