"""Focused tests for `cmd_prep_push_rep_season_enrich` — the season prep->push->
replace autopilot folded together with enrich (IMP-D22 Step 2). Built on Step 1's
winning mechanism: `_enrich_after_archive` is reused UNCHANGED (zero new copies of
the enrich waterfall), called with `base_id` in the same `id_or_prefix=<this
unit's own id>` slot the movie command passes its own `real_id`.

Covers the plan's Step 2 Acceptance scenarios (see
docs/feature-prep-push-rep-enrich/DECISIONS.md, Decision 6):
  - BOTH folder layouts: nested `<Show>/Season NN/` (token stamped on the
    PARENT show folder, WITH the "this is the SHOW folder" confirmation note)
    and flat/root-level (token stamped on that SAME folder, note SUPPRESSED
    because the season folder and the show folder are the identical path).
  - `episode_range` sub-selection: when the RANGE ITSELF fully archives, enrich
    proceeds even though sibling episodes outside the range stay untouched
    (local_ready); when the range does NOT fully archive, enrich is skipped.
  - a preset id set via `-tmdbid` lands on an episode LEAF, never the
    season_map (`cmd_set_tmdb` would refuse a season_map container).
  - `_season_run_target_ids` returns `[]` (not a crash) on an invalid range,
    and on a `base_id` missing from the library.
  - scoping by `base_id` directly (NOT a derived show id, per Decision 6's
    "Why harder" reason 2) means a SIBLING season's own preset is correctly
    NOT reached — a positive proof of the revised, simpler design.
  - the IMP-D22 Step 2 `<director>`-for-shows fix: a show NFO sources
    `<director>` from the TV details payload's `created_by`, not from the
    (always-empty-for-a-show) movie-style crew Director job.

`-tvdbid` refusal is also re-verified here (cheap, and specific to this new
command's own arg-forwarding plumbing — a wiring bug could slip past Step 1's
suite, which only exercises the movie command).

HERMETIC: reuses the SAME `sandbox` / `make_video` / `stub_tech_specs` /
`mock_device` / `fake_dummy` fixtures the movie enrich suite
(test_prep_push_rep_enrich.py) uses for a real (mocked-I/O) archive, plus a
tiny local by-id-only TV TMDB fake (a preset id must NEVER search).
"""
import os
import re
import xml.etree.ElementTree as ET

import pytest

import main
import mvcommon
from conftest import FAKE_DUMMY_BYTES


FAKE_JPG = b"\xff\xd8\xff\xe0FAKE-JPEG-BYTES\xff\xd9"


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


class _FakeTMDBByIdTV:
    """By-id-ONLY TV TMDB fake for the preset-tmdb_id path (`_resolve_unit_by_id`).

    Serves ONE show's bare `/tv/{id}` details, `/configuration`, and image
    bytes. Every OTHER TMDB shape this run's enrich leg may touch (per-season
    images, per-episode stills, season DETAILS for `_apply_episode_overviews`,
    credits, external_ids) degrades to an empty `{}` — never a 404 — so those
    graceful-omit paths are exercised without needing bespoke canned data for
    each. Deliberately has NO `/search/*` handling — a preset id must never
    search.
    """
    def __init__(self, tv_id, details):
        self.tv_id = tv_id
        self.details = details
        self.calls = []
        self.image_urls = []

    def get(self, url, params=None, headers=None, timeout=None, **kwargs):
        params = params or {}
        self.calls.append((url, dict(params)))
        if url.startswith("https://image.tmdb.org/t/p/"):
            self.image_urls.append(url)
            return _Resp(200, content=FAKE_JPG)
        if url.endswith("/configuration"):
            return _Resp(200, json_data={"images": {"secure_base_url": "https://image.tmdb.org/t/p/"}})
        if url.endswith(f"/tv/{self.tv_id}"):
            return _Resp(200, json_data=self.details)
        return _Resp(200, json_data={})


@pytest.fixture()
def patch_tmdb_by_id(monkeypatch, tmp_path):
    """Redirect the TMDB/EXA caches to temp dirs, install a v3 key, force the
    EXA fallback OFF, and hand back an `install(fake)` that patches
    `main.requests.get`. Identical rationale to test_prep_push_rep_enrich.py's
    fixture of the same name."""
    monkeypatch.setattr(main, "TMDB_CACHE_DIR", str(tmp_path / "tmdb_cache"))
    monkeypatch.setattr(main, "EXA_CACHE_DIR", str(tmp_path / "exa_cache"))
    monkeypatch.setattr(mvcommon, "tmdb_api_key", lambda: "TEST-V3-KEY")
    monkeypatch.setattr(mvcommon, "exa_api_key", lambda: "")

    def install(fake):
        monkeypatch.setattr(main.requests, "get", fake.get)
        return fake

    return install


def _tv_details(tmdb_id, name="Dark", year=2017, created_by=None):
    """A /3/tv/{id} DETAILS object — the same shape `_resolve_unit_by_id` and
    `_write_nfo`'s richer element set read (name/first_air_date/poster_path/
    backdrop_path/overview/vote_average, plus optional `created_by`)."""
    d = {
        "id": tmdb_id, "name": name, "first_air_date": f"{year}-01-01",
        "poster_path": "/poster.jpg", "backdrop_path": "/backdrop.jpg",
        "overview": "A show about testing season enrich autopilots.",
        "vote_average": 8.2,
    }
    if created_by is not None:
        d["created_by"] = created_by
    return d


def _seed_episodes(dest_dir, make_video, filenames):
    """Write real (>DUMMY_MAX_BYTES) .mkv files for each filename directly
    into dest_dir (already created by the caller). Returns the sorted list of
    (path, hash) tuples in filename order (matches cmd_prep_season's own
    sorted-listdir iteration, so id assignment order is predictable)."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    out = []
    for fn in filenames:
        path, h = make_video(dest_dir / fn)
        out.append((path, h))
    return out


# ---------------------------------------------------------------------------
# (1) NESTED layout (`<Show>/Season NN/`) full-season happy path: token
#     stamped on the PARENT show folder, WITH the "SHOW folder" note. Also
#     covers "preset lands on an episode LEAF, never the season_map".
# ---------------------------------------------------------------------------

def test_nested_layout_happy_path_stamps_parent_folder_with_note(
        sandbox, make_video, stub_tech_specs, mock_device, fake_dummy,
        patch_tmdb_by_id, monkeypatch, capsys):
    base_id = "tv-en-2017-dark-s01"
    show_dir = sandbox["local_root"] / "Series" / "Dark"
    season_dir = show_dir / "Season 01"
    _seed_episodes(season_dir, make_video, ["Dark.S01E01.mkv", "Dark.S01E02.mkv"])

    TMDB_ID = 70523
    fake = patch_tmdb_by_id(_FakeTMDBByIdTV(TMDB_ID, _tv_details(TMDB_ID)))

    set_tmdb_calls = []
    real_set_tmdb = main.cmd_set_tmdb
    def _spy_set_tmdb(mid, tid):
        set_tmdb_calls.append(mid)
        return real_set_tmdb(mid, tid)
    monkeypatch.setattr(main, "cmd_set_tmdb", _spy_set_tmdb)

    result = main.cmd_prep_push_rep_season_enrich(
        base_id, str(season_dir), tmdb_id=TMDB_ID, rename_choice="yes")

    assert result is True
    out = capsys.readouterr().out
    assert "using preset tmdb_id=70523 (manual)" in out
    assert "AUTO-PILOT COMPLETE (archive + enrich)" in out
    assert "this is the SHOW folder" in out, \
        "nested layout: the show folder DIFFERS from the season folder -> note expected"

    # No /search call was ever made — resolved strictly by id.
    assert not any("/search/" in u for u, _ in fake.calls), \
        "a preset tmdb_id must resolve by id, never search"

    # --- preset landed on an episode LEAF, never the season_map ---
    assert len(set_tmdb_calls) == 1
    preset_target = set_tmdb_calls[0]
    assert preset_target != base_id, "the preset must never target the season_map itself"
    lib = mvcommon.load_library()
    assert "filename" in lib[preset_target], "the preset target must be a real leaf (has a filename)"
    assert preset_target == f"{base_id}e01", "children are processed in sorted order -> first child == e01"

    # --- token stamped on the PARENT show folder (nested layout) ---
    stamped_show = show_dir.parent / "Dark {tmdb-70523}"
    assert stamped_show.is_dir() and not show_dir.exists()
    assert (stamped_show / "Season 01").is_dir(), "the season subfolder must move WITH the show folder"
    assert (stamped_show / "poster.jpg").read_bytes() == FAKE_JPG
    assert (stamped_show / "fanart.jpg").read_bytes() == FAKE_JPG

    # --- library reflects both the archive AND the rename cascade ---
    season_entry = lib[base_id]
    assert season_entry["folder_path"] == str(stamped_show / "Season 01")
    ep1, ep2 = lib[f"{base_id}e01"], lib[f"{base_id}e02"]
    assert ep1["status"] == "archived" and ep2["status"] == "archived"
    assert ep1["folder_path"] == str(stamped_show / "Season 01")
    assert ep1["metadata"]["tmdb_id"] == TMDB_ID
    assert ep2["metadata"]["tmdb_id"] == TMDB_ID


# ---------------------------------------------------------------------------
# (2) FLAT/root-level layout (season IS the show folder — Decision 6 "Why
#     harder" reason 1, the DOMINANT real-library shape): token stamped on
#     that SAME folder, note SUPPRESSED (it would be factually wrong).
# ---------------------------------------------------------------------------

def test_flat_layout_happy_path_stamps_same_folder_no_note(
        sandbox, make_video, stub_tech_specs, mock_device, fake_dummy,
        patch_tmdb_by_id, capsys):
    base_id = "tv-en-2022-peakyblinders-s06"
    season_dir = sandbox["local_root"] / "Series" / "Peaky.Blinders.S06.2022"
    _seed_episodes(season_dir, make_video,
                   ["Peaky.Blinders.S06E01.mkv", "Peaky.Blinders.S06E02.mkv"])

    TMDB_ID = 60574
    patch_tmdb_by_id(_FakeTMDBByIdTV(TMDB_ID, _tv_details(TMDB_ID, name="Peaky Blinders", year=2022)))

    result = main.cmd_prep_push_rep_season_enrich(
        base_id, str(season_dir), tmdb_id=TMDB_ID, rename_choice="yes")

    assert result is True
    out = capsys.readouterr().out
    assert "will be changed to" in out  # the gate DID run (rename offered)
    assert "this is the SHOW folder" not in out, \
        "flat layout: season folder == show folder -> the parent-folder note would be WRONG"

    # --- token stamped on the SAME folder (no separate parent) ---
    stamped = season_dir.parent / "Peaky.Blinders.S06.2022 {tmdb-60574}"
    assert stamped.is_dir() and not season_dir.exists()
    assert (stamped / "poster.jpg").read_bytes() == FAKE_JPG

    lib = mvcommon.load_library()
    assert lib[base_id]["folder_path"] == str(stamped)
    assert lib[f"{base_id}e01"]["folder_path"] == str(stamped)
    assert lib[f"{base_id}e01"]["status"] == "archived"
    assert lib[f"{base_id}e02"]["status"] == "archived"


# ---------------------------------------------------------------------------
# (3) episode_range sub-selection: when the RANGE ITSELF fully archives,
#     enrich proceeds even though the sibling episode OUTSIDE the range is
#     left untouched (target_ids is range-scoped, not whole-season).
# ---------------------------------------------------------------------------

def test_episode_range_that_fully_archives_still_enriches(
        sandbox, make_video, stub_tech_specs, mock_device, fake_dummy,
        patch_tmdb_by_id, capsys):
    base_id = "tv-en-2019-theexpanse-s05"
    season_dir = sandbox["local_root"] / "Series" / "TheExpanse.S05.2020"
    _seed_episodes(season_dir, make_video, [
        "TheExpanse.S05E01.mkv", "TheExpanse.S05E02.mkv", "TheExpanse.S05E03.mkv",
    ])

    TMDB_ID = 63639
    patch_tmdb_by_id(_FakeTMDBByIdTV(TMDB_ID, _tv_details(TMDB_ID, name="The Expanse", year=2020)))

    result = main.cmd_prep_push_rep_season_enrich(
        base_id, str(season_dir), episode_range="1-2", tmdb_id=TMDB_ID, rename_choice="no")

    assert result is True
    out = capsys.readouterr().out
    assert "nothing was archived this run" not in out
    assert "not archived yet" not in out
    assert "AUTO-PILOT COMPLETE (archive + enrich)" in out

    lib = mvcommon.load_library()
    ep1, ep2, ep3 = lib[f"{base_id}e01"], lib[f"{base_id}e02"], lib[f"{base_id}e03"]
    assert ep1["status"] == "archived" and ep2["status"] == "archived"
    assert ep3["status"] == "local_ready", \
        "episode 3 is OUTSIDE the range and must be left untouched by the archive"

    # Enrich proceeded (whole-season unit) even though ep3 never archived —
    # its metadata still gets the tmdb_id write (the unit spans the whole
    # season_map regardless of which episodes THIS RUN happened to archive).
    assert ep1["metadata"]["tmdb_id"] == TMDB_ID
    assert ep2["metadata"]["tmdb_id"] == TMDB_ID
    assert ep3["metadata"]["tmdb_id"] == TMDB_ID
    assert (season_dir / "poster.jpg").read_bytes() == FAKE_JPG


def test_episode_range_that_does_not_fully_archive_skips_enrich(
        sandbox, make_video, stub_tech_specs, mock_device, fake_dummy,
        patch_tmdb_by_id, monkeypatch, capsys):
    base_id = "tv-en-2019-theexpanse-s05"
    season_dir = sandbox["local_root"] / "Series" / "TheExpanse.S05.2020"
    _seed_episodes(season_dir, make_video, [
        "TheExpanse.S05E01.mkv", "TheExpanse.S05E02.mkv", "TheExpanse.S05E03.mkv",
    ])

    TMDB_ID = 63639
    patch_tmdb_by_id(_FakeTMDBByIdTV(TMDB_ID, _tv_details(TMDB_ID, name="The Expanse", year=2020)))

    # Force episode 2 (still inside the requested range) to fail its push, so
    # the range "1-2" does NOT fully archive this run.
    real_push = main.cmd_push
    def _fail_e02(mid, *a, **kw):
        if mid == f"{base_id}e02":
            return False
        return real_push(mid, *a, **kw)
    monkeypatch.setattr(main, "cmd_push", _fail_e02)

    def _tmdb_key_must_not_be_read():
        raise AssertionError("enrich must never even check for a TMDB key when the range didn't finish")
    monkeypatch.setattr(mvcommon, "tmdb_api_key", _tmdb_key_must_not_be_read)

    result = main.cmd_prep_push_rep_season_enrich(
        base_id, str(season_dir), episode_range="1-2", tmdb_id=TMDB_ID, rename_choice="no")

    assert result is False
    out = capsys.readouterr().out
    assert f"{base_id}e02" in out
    assert "not archived yet" in out
    assert "status='local_ready'" in out

    lib = mvcommon.load_library()
    assert lib[f"{base_id}e01"]["status"] == "archived"
    assert lib[f"{base_id}e02"]["status"] == "local_ready"
    assert "tmdb_id" not in lib[f"{base_id}e01"].get("metadata", {}), \
        "enrich must never run when this run's range did not fully archive"


# ---------------------------------------------------------------------------
# (4) `_season_run_target_ids` — direct unit tests (module-level, independently
#     callable per the plan's binding convention for Step 5).
# ---------------------------------------------------------------------------

def test_season_run_target_ids_empty_on_missing_base_id():
    assert main._season_run_target_ids({}, "tv-en-2020-nope-s01", None) == []


def test_season_run_target_ids_empty_on_invalid_range():
    library = {
        "tv-en-2020-show-s01": {"type": "season_map", "children": [
            "tv-en-2020-show-s01e01", "tv-en-2020-show-s01e02"]},
        "tv-en-2020-show-s01e01": {"filename": "e01.mkv"},
        "tv-en-2020-show-s01e02": {"filename": "e02.mkv"},
    }
    assert main._season_run_target_ids(library, "tv-en-2020-show-s01", "abc-def") == []


def test_season_run_target_ids_filters_and_dealiases():
    base_id = "tv-en-2020-show-s01"
    library = {
        base_id: {"type": "season_map", "children": [
            f"{base_id}e01", f"{base_id}e02", f"{base_id}e03"]},
        f"{base_id}e01": {"filename": "e01.mkv"},
        f"{base_id}e02": {"filename": "e02.mkv"},
        f"{base_id}e03": {"filename": "e03.mkv"},
    }
    assert main._season_run_target_ids(library, base_id, "1-2") == [f"{base_id}e01", f"{base_id}e02"]
    assert main._season_run_target_ids(library, base_id, None) == [
        f"{base_id}e01", f"{base_id}e02", f"{base_id}e03"]


# ---------------------------------------------------------------------------
# (5) Sibling-season isolation: scoping by `base_id` (not a derived show id)
#     means a SIBLING season's own preset is correctly NOT reached — a
#     positive proof of the revised, simpler design (Decision 6 reason 2).
# ---------------------------------------------------------------------------

def test_sibling_season_preset_not_reached_by_base_id_scoping(
        sandbox, make_video, stub_tech_specs, mock_device, fake_dummy,
        patch_tmdb_by_id, capsys):
    # Real per-season-year id convention (Decision 6): different seasons of
    # the SAME show embed DIFFERENT years, so they share no common id prefix.
    base_id_a = "tv-en-2022-peakyblinders-s06"
    sibling_id = "tv-en-2019-peakyblinders-s05"
    sibling_ep = f"{sibling_id}e01"

    season_dir_a = sandbox["local_root"] / "Series" / "Peaky.Blinders.S06.2022"
    _seed_episodes(season_dir_a, make_video, ["Peaky.Blinders.S06E01.mkv"])

    # Seed the sibling season directly (no real archive needed — it must
    # never even be looked at by this run). Its leaf already carries a
    # PRIOR preset, simulating an earlier independent enrich run.
    sibling_dir = sandbox["local_root"] / "Series" / "Peaky.Blinders.S05.2019"
    sibling_dir.mkdir(parents=True, exist_ok=True)
    lib = mvcommon.load_library()
    lib[sibling_id] = {"type": "season_map", "folder_path": str(sibling_dir), "total_episodes": 1,
                        "children": [sibling_ep]}
    lib[sibling_ep] = {
        "filename": "Peaky.Blinders.S05E01.mkv", "folder_path": str(sibling_dir),
        "status": "archived", "uploaded": True,
        "metadata": {"tmdb_id": 999999, "title": "Peaky Blinders (S05)"},
        "parent_id": sibling_id,
    }
    mvcommon.save_library(lib)

    TMDB_ID_A = 60574
    patch_tmdb_by_id(_FakeTMDBByIdTV(TMDB_ID_A, _tv_details(TMDB_ID_A, name="Peaky Blinders", year=2022)))

    result = main.cmd_prep_push_rep_season_enrich(
        base_id_a, str(season_dir_a), tmdb_id=TMDB_ID_A, rename_choice="no")

    assert result is True

    lib = mvcommon.load_library()
    # Season A got its own preset's tmdb_id.
    assert lib[f"{base_id_a}e01"]["metadata"]["tmdb_id"] == TMDB_ID_A
    # The sibling season is COMPLETELY untouched — its preset survives
    # unchanged, and this run never renamed/relocated it.
    assert lib[sibling_ep]["metadata"]["tmdb_id"] == 999999
    assert lib[sibling_ep]["folder_path"] == str(sibling_dir)
    assert lib[sibling_id]["folder_path"] == str(sibling_dir)
    assert not (sibling_dir.parent / f"Peaky.Blinders.S05.2019 {{tmdb-{TMDB_ID_A}}}").exists()


# ---------------------------------------------------------------------------
# (6) `-tvdbid` refuses before anything touches disk (same D1 refusal Step 1
#     verified for the movie command — re-verified here since it is THIS
#     command's own arg-forwarding plumbing, not shared code).
# ---------------------------------------------------------------------------

def test_tvdbid_refuses_before_anything_touches_disk(
        sandbox, make_video, stub_tech_specs, monkeypatch, capsys):
    base_id = "tv-en-2017-dark-s01"
    season_dir = sandbox["local_root"] / "Series" / "Dark" / "Season 01"
    _seed_episodes(season_dir, make_video, ["Dark.S01E01.mkv"])

    def _must_not_be_called(*a, **kw):
        raise AssertionError("cmd_prep_push_rep_season must NOT be called when -tvdbid is supplied")
    monkeypatch.setattr(main, "cmd_prep_push_rep_season", _must_not_be_called)

    assert not sandbox["lib_series"].exists()

    result = main.cmd_prep_push_rep_season_enrich(base_id, str(season_dir), tvdb_id=82066)

    assert result is False
    out = capsys.readouterr().out
    assert "MediaVault is TMDB-only for movies, series, and anime" in out
    assert not sandbox["lib_series"].exists()


# ---------------------------------------------------------------------------
# (7) IMP-D22 Step 2 fix: a SHOW NFO sources <director> from the TV details
#     payload's `created_by`, never from the (always-empty-for-a-show)
#     movie-style crew Director job.
# ---------------------------------------------------------------------------

def test_show_nfo_carries_creator_names_from_created_by(
        sandbox, make_video, stub_tech_specs, mock_device, fake_dummy,
        patch_tmdb_by_id, capsys):
    import xml.etree.ElementTree as ET

    base_id = "tv-en-2016-stranger-s01"
    season_dir = sandbox["local_root"] / "Series" / "Stranger.Things.S01.2016"
    _seed_episodes(season_dir, make_video, ["Stranger.Things.S01E01.mkv"])

    TMDB_ID = 66732
    details = _tv_details(TMDB_ID, name="Stranger Things", year=2016,
                           created_by=[{"name": "The Duffer Brothers"}, {"name": "Shawn Levy"}])
    patch_tmdb_by_id(_FakeTMDBByIdTV(TMDB_ID, details))

    result = main.cmd_prep_push_rep_season_enrich(
        base_id, str(season_dir), tmdb_id=TMDB_ID, write_nfo=True, rename_choice="yes")

    assert result is True
    stamped = season_dir.parent / f"Stranger.Things.S01.2016 {{tmdb-{TMDB_ID}}}"
    nfo_path = stamped / "tvshow.nfo"
    assert nfo_path.exists(), "tvshow.nfo must be written when --nfo (write_nfo=True) is on"

    root = ET.parse(str(nfo_path)).getroot()
    assert root.tag == "tvshow"
    directors = [d.text for d in root.findall("director")]
    assert directors == ["The Duffer Brothers", "Shawn Levy"], \
        "a show's <director> must come from created_by, not the (empty) movie-style crew Director job"
    assert root.find("tvdbid") is None, "Decision 1: <tvdbid> is NEVER emitted"


# ###########################################################################
#
#   IMP-D22 STEP 5 — FULL SEASON TEST MATRIX (added 2026-08-29)
#
#   GROUP A (below, `test_regression_*`) — proves the EXISTING
#   `cmd_prep_push_rep_season` still behaves EXACTLY as it does today, across
#   every flag it accepts: no-split, SIZE_MB, SIZE_GB, COUNT, `episodes`
#   (single + multi), `device`, `rehash`, `tempdir`, `--extras`,
#   `--extras-size`, and combinations. These call the OLD function DIRECTLY
#   (IMP-D22 does not touch a line of it) and assert the same post-state the
#   smoke suite's `test_prep_push_rep_season_with_episode_range` asserts. One
#   further test pins that the NEW command forwards every archive argument to
#   it VERBATIM, so the wrapper cannot silently drop or reorder a flag.
#
#   GROUP B (below, `test_inventory_*` / `test_nfo_*` / …) — the new command's
#   FULL artifact inventory (PLAN.md "Full artifact inventory", Decision 6's
#   added scope) in BOTH real-world folder layouts:
#     (i)  NESTED  `<Show>/Season NN/`   — `_show_folder_of` climbs to the parent
#     (ii) FLAT    `<Release.Folder>/`   — `_show_folder_of` returns the SAME
#          folder as both show and season folder (46 of the user's real shows;
#          the DOMINANT shape, not an edge case)
#
# ---------------------------------------------------------------------------
#   TWO DOCUMENTED DEVIATIONS FROM PLAN.md's Step 5 PROSE — the tests below
#   pin the IMPLEMENTATION's ACTUAL (pre-existing, unmodified) behaviour and
#   say so at each site, rather than asserting prose that `main.py` does not
#   satisfy. Neither is a bug introduced by IMP-D22:
#
#   D-A. FLAT layout, inventory row 3. PLAN.md says the season-images endpoint
#        "IS still called … but its result is DISCARDED". It is NOT called at
#        all: `_download_unit_images` (main.py:~3416) checks
#        `os.path.exists(dest)` and `continue`s BEFORE issuing the
#        `/tv/{id}/season/{n}/images` GET. Skipping the call is strictly better
#        (one fewer API round trip) and the user-visible outcome PLAN.md cares
#        about is identical: a "kept" skip message, no error, no overwrite.
#        `test_inventory_flat_layout_full_artifact_checklist` asserts the real
#        thing — the endpoint is never hit AND the "kept" line is printed.
#
#   D-B. LOCAL-ALWAYS-WINS, inventory row 7 (`tvshow.nfo`). PLAN.md lists row 7
#        among the rows that must be local-always-wins. `_write_nfo`
#        (main.py:2373) documents the opposite and always has: "Overwrites an
#        existing file (NFOs are regenerable metadata)." That is pre-existing,
#        deliberate, and shared with `enrich_metadata --nfo`. Rows 1/3/4 (the
#        artwork) ARE local-always-wins and are pinned as such by
#        `test_local_always_wins_across_the_full_inventory`; row 7's real
#        contract is pinned separately by `test_nfo_is_regenerated_not_kept`.
# ###########################################################################


# ---------------------------------------------------------------------------
#   Shared helpers (per-file and self-contained, matching this project's
#   convention — patterns COPIED from tests/test_extras.py rather than
#   imported across test files).
# ---------------------------------------------------------------------------

def _device_names(device_dir, pattern="*.mkv"):
    """{filename: Path} index of the fake device.

    Always index by `.name` — MediaVault filenames carry "[short_id]", and
    `rglob("name [id].mkv")` would read the brackets as a glob character class
    and silently match NOTHING (docs/testing-strategy.md §8.1/§9)."""
    return {f.name: f for f in device_dir.rglob(pattern)}


def _install_fake_split(monkeypatch, n_chunks=2):
    """Replace `main.split_video_file` with a deterministic byte-slicer.

    Reproduces the real function's contract — chunk names
    `"<base> [<file_id>].chunk.NNN.mkv"` inside `output_dir`, returned sorted —
    but slices the input's REAL bytes instead of invoking mkvmerge (the fixture
    episodes are ~264 KB and the real splitter adds a +10 MB per-chunk buffer,
    so a genuine split is impossible at fixture scale). Returns the recorded
    call list so a test can assert WHICH method/val reached the splitter."""
    calls = []

    def fake_split(input_path, output_dir, method, value_str, file_id=""):
        calls.append({"input": input_path, "output_dir": output_dir,
                      "method": method, "val": value_str, "file_id": file_id})
        os.makedirs(output_dir, exist_ok=True)
        with open(input_path, "rb") as f:
            data = f.read()
        step = len(data) // n_chunks + 1  # +1 => no empty trailing slice
        base = os.path.splitext(os.path.basename(input_path))[0]
        tag = f" [{file_id}]" if file_id else ""
        paths = []
        for i in range(n_chunks):
            p = os.path.join(output_dir, f"{base}{tag}.chunk.{i + 1:03d}.mkv")
            with open(p, "wb") as f:
                f.write(data[i * step:(i + 1) * step])
            paths.append(p)
        return paths

    monkeypatch.setattr(main, "split_video_file", fake_split)
    return calls


def _install_concat_merge(monkeypatch):
    """Replace `main.merge_video_files` (mkvmerge) with an in-order byte
    concatenation — the inverse of `_install_fake_split`'s slicer, so the eager
    re-hash merge produces a real, hashable file. Returns the recorded calls."""
    calls = []

    def fake_merge(chunk_paths, output_path, seed=None):
        calls.append({"chunks": list(chunk_paths), "out": output_path, "seed": seed})
        with open(output_path, "wb") as out:
            for c in chunk_paths:
                with open(c, "rb") as fh:
                    out.write(fh.read())
        return True

    monkeypatch.setattr(main, "merge_video_files", fake_merge)
    return calls


def _record_subprocess(monkeypatch):
    """Record every argv `main.subprocess.run` receives, DELEGATING to whatever
    implementation is already installed (i.e. `mock_device`'s fake_run).

    MUST be called from inside the test body, AFTER the `mock_device` fixture
    has installed its own patch, or it would record nothing and break the
    device emulation."""
    calls = []
    inner = main.subprocess.run

    def _run(argv, *a, **kw):
        calls.append(list(argv))
        return inner(argv, *a, **kw)

    monkeypatch.setattr(main.subprocess, "run", _run)
    return calls


# ---------------------------------------------------------------------------
#   GROUP A — regression matrix for the EXISTING `cmd_prep_push_rep_season`.
#   IMP-D22 changes NOTHING in this function; these tests exist because the
#   user explicitly asked for proof that every pre-existing flag combination
#   still behaves identically after the enrich work lands.
# ---------------------------------------------------------------------------

def test_regression_season_no_split_archives_every_episode(
        sandbox, make_video, stub_tech_specs, mock_device, fake_dummy):
    """(A1) Baseline: no split, no range, no device — every episode ends
    `archived`, uploaded, dummied on disk, and NO split_info is recorded."""
    base_id = "tv-en-2020-regr-s01"
    season_dir = sandbox["local_root"] / "Series" / "Regr S01"
    _seed_episodes(season_dir, make_video, ["REG.S01E01.mkv", "REG.S01E02.mkv"])

    main.cmd_prep_push_rep_season(base_id, str(season_dir))

    lib = mvcommon.load_library()
    assert lib[base_id]["type"] == "season_map"
    assert lib[base_id]["children"] == [f"{base_id}e01", f"{base_id}e02"]
    for eid in lib[base_id]["children"]:
        entry = lib[eid]
        assert entry["status"] == "archived"
        assert entry["uploaded"] is True
        assert "split_info" not in entry, "a whole-file push must not record split_info"
        assert (season_dir / entry["filename"]).read_bytes() == FAKE_DUMMY_BYTES
    on_device = _device_names(mock_device)
    assert len(on_device) == 2, sorted(on_device)
    assert all("REG.S01E0" in n for n in on_device), sorted(on_device)


def test_regression_season_episode_range_single(
        sandbox, make_video, stub_tech_specs, mock_device, fake_dummy):
    """(A2) `episodes 1-1` — byte-for-byte the smoke suite's own oracle
    (`test_prep_push_rep_season_with_episode_range`): only the in-range episode
    reaches `archived`; the out-of-range one stays `local_ready` and its real
    media bytes are untouched on disk."""
    base_id = "tv-en-2020-range1-s01"
    season_dir = sandbox["local_root"] / "Series" / "Range1 S01"
    seeded = _seed_episodes(season_dir, make_video, ["RNG.S01E01.mkv", "RNG.S01E02.mkv"])
    e02_bytes = open(seeded[1][0], "rb").read()

    main.cmd_prep_push_rep_season(base_id, str(season_dir), episode_range="1-1")

    lib = mvcommon.load_library()
    assert lib[f"{base_id}e01"]["status"] == "archived"
    assert lib[f"{base_id}e02"]["status"] == "local_ready"
    assert (season_dir / "RNG.S01E02.mkv").read_bytes() == e02_bytes
    assert len(_device_names(mock_device)) == 1


def test_regression_season_episode_range_multi(
        sandbox, make_video, stub_tech_specs, mock_device, fake_dummy, capsys):
    """(A3) A MULTI-episode range over a 4-episode season: exactly episodes
    2 and 3 archive; 1 and 4 are untouched, and the filter line reports 2."""
    base_id = "tv-en-2020-range2-s01"
    season_dir = sandbox["local_root"] / "Series" / "Range2 S01"
    _seed_episodes(season_dir, make_video,
                   [f"RN2.S01E0{n}.mkv" for n in (1, 2, 3, 4)])

    main.cmd_prep_push_rep_season(base_id, str(season_dir), episode_range="2-3")

    out = capsys.readouterr().out
    assert "Filtered to 2 episodes (2-3)" in out
    lib = mvcommon.load_library()
    assert [lib[f"{base_id}e0{n}"]["status"] for n in (1, 2, 3, 4)] == [
        "local_ready", "archived", "archived", "local_ready"]


@pytest.mark.parametrize("method,val", [("SIZE_MB", "700"), ("SIZE_GB", "9")])
def test_regression_season_size_method_below_threshold_pushes_whole(
        sandbox, make_video, stub_tech_specs, mock_device, fake_dummy, capsys,
        method, val):
    """(A4/A5) SIZE_MB and SIZE_GB: the fixture episodes are far below the
    target, so cmd_push takes the documented "Skipping split" branch — whole
    files are pushed, no split_info, no `_parts/` directory left behind."""
    base_id = f"tv-en-2020-{method.lower().replace('_', '')}-s01"
    season_dir = sandbox["local_root"] / "Series" / f"Sized {method}"
    _seed_episodes(season_dir, make_video, ["SZ.S01E01.mkv", "SZ.S01E02.mkv"])

    main.cmd_prep_push_rep_season(base_id, str(season_dir), method, val)

    out = capsys.readouterr().out
    assert "Skipping split" in out
    lib = mvcommon.load_library()
    for eid in (f"{base_id}e01", f"{base_id}e02"):
        assert lib[eid]["status"] == "archived"
        assert "split_info" not in lib[eid]
    assert not (season_dir / "_parts").exists()


def test_regression_season_count_split_records_split_info_and_chunks(
        sandbox, make_video, stub_tech_specs, mock_device, fake_dummy, monkeypatch):
    """(A6) COUNT always splits. With the byte-slicer standing in for mkvmerge,
    each episode records `split_info` (method/val/total_chunks/chunks) and BOTH
    chunks reach the device; the `checksums/` sidecars land next to the media.

    NOTE: the season pre-flight requires ~2 GB free on the media volume (the
    `_disk_buffer` floor) — the same environmental requirement every existing
    split test in this repo already has."""
    base_id = "tv-en-2020-count-s01"
    season_dir = sandbox["local_root"] / "Series" / "Counted S01"
    _seed_episodes(season_dir, make_video, ["CNT.S01E01.mkv", "CNT.S01E02.mkv"])
    split_calls = _install_fake_split(monkeypatch, n_chunks=2)

    main.cmd_prep_push_rep_season(base_id, str(season_dir), "COUNT", "2")

    assert [c["method"] for c in split_calls] == ["COUNT", "COUNT"]
    assert [c["val"] for c in split_calls] == ["2", "2"]
    lib = mvcommon.load_library()
    for eid in (f"{base_id}e01", f"{base_id}e02"):
        si = lib[eid]["split_info"]
        assert si["is_split"] is True
        assert (si["method"], si["val"]) == ("COUNT", "2")
        assert si["total_chunks"] == 2 and len(si["chunks"]) == 2
        assert lib[eid]["status"] == "archived"
    on_device = _device_names(mock_device)
    assert len(on_device) == 4, sorted(on_device)          # 2 episodes x 2 chunks
    assert all(".chunk.00" in n for n in on_device), sorted(on_device)
    assert (season_dir / "checksums").is_dir()


def test_regression_season_device_id_reaches_every_adb_call(
        sandbox, make_video, stub_tech_specs, mock_device, fake_dummy, monkeypatch):
    """(A7) `device <serial>` must be threaded onto EVERY adb invocation the
    season run makes (`adb -s <serial> …`), not just the first."""
    base_id = "tv-en-2020-dev-s01"
    season_dir = sandbox["local_root"] / "Series" / "Deviced S01"
    _seed_episodes(season_dir, make_video, ["DEV.S01E01.mkv", "DEV.S01E02.mkv"])
    argvs = _record_subprocess(monkeypatch)

    main.cmd_prep_push_rep_season(base_id, str(season_dir), device_id="fakeserial")

    adb_calls = [a for a in argvs if a and a[0] == "adb"]
    assert adb_calls, "the run made no adb calls at all"
    for argv in adb_calls:
        assert argv[1:3] == ["-s", "fakeserial"], argv
    lib = mvcommon.load_library()
    assert all(lib[f"{base_id}e0{n}"]["status"] == "archived" for n in (1, 2))


def test_regression_season_eager_rehash_stages_then_promotes_canonical(
        sandbox, make_video, stub_tech_specs, mock_device, fake_dummy, monkeypatch):
    """(A8) `rehash` (eager_rehash=True) on a real split: cmd_push stages
    merge_seed/merge_tool/canonical_hash into split_info, and the cmd_replace
    the SEASON LOOP runs immediately afterwards promotes it — entry["hash"]
    becomes the canonical merge hash and re_hashed flips True."""
    import hashlib

    base_id = "tv-en-2020-rehash-s01"
    season_dir = sandbox["local_root"] / "Series" / "Rehashed S01"
    seeded = _seed_episodes(season_dir, make_video, ["RH.S01E01.mkv"])
    original_hash = seeded[0][1]
    original_bytes = open(seeded[0][0], "rb").read()
    _install_fake_split(monkeypatch, n_chunks=2)
    merges = _install_concat_merge(monkeypatch)

    main.cmd_prep_push_rep_season(base_id, str(season_dir), "COUNT", "2",
                                  eager_rehash=True)

    assert len(merges) == 1, "the eager re-hash must merge exactly once"
    assert merges[0]["seed"], "the eager merge must be seeded (deterministic)"
    lib = mvcommon.load_library()
    entry = lib[f"{base_id}e01"]
    assert entry["status"] == "archived"
    assert entry["re_hashed"] is True, "replace must promote the eager canonical"
    # The slicer+concat round trip is lossless, so the canonical equals the
    # original hash here — what matters is that promotion HAPPENED.
    assert entry["hash"] == hashlib.sha256(original_bytes).hexdigest() == original_hash
    assert "rehashed_at" in entry["split_info"]
    assert "canonical_hash" not in entry["split_info"], "canonical is consumed at replace"


def test_regression_season_tempdir_redirects_chunks_only(
        sandbox, make_video, stub_tech_specs, mock_device, fake_dummy, monkeypatch,
        tmp_path):
    """(A9) `tempdir <dir>`: the `_parts/` chunk directory moves under
    `<tempdir>/<safe-id>/`, while the `checksums/` sidecars STAY in the media
    folder (the documented split of responsibilities in `_parts_base`).

    The chunk directory is CLEANED UP after a successful push, so the proof has
    to be captured while it exists — the recorded `output_dir` the splitter was
    handed is exactly that, and it is deterministic."""
    base_id = "tv-en-2020-tmpd-s01"
    season_dir = sandbox["local_root"] / "Series" / "Temped S01"
    _seed_episodes(season_dir, make_video, ["TMP.S01E01.mkv"])
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    split_calls = _install_fake_split(monkeypatch, n_chunks=2)

    main.cmd_prep_push_rep_season(base_id, str(season_dir), "COUNT", "2",
                                  temp_dir=str(scratch))

    lib = mvcommon.load_library()
    assert lib[f"{base_id}e01"]["status"] == "archived"
    assert len(split_calls) == 1
    assert split_calls[0]["output_dir"] == str(scratch / f"{base_id}e01" / "_parts"), \
        "chunks must be written under <temp_dir>/<safe-id>/_parts"
    assert (season_dir / "checksums").is_dir(), "checksums must NOT follow temp_dir"
    assert not (season_dir / "_parts").exists(), "_parts must NOT be left in the media folder"


def test_regression_season_extras_are_registered_pushed_and_dummied(
        sandbox, make_video, stub_tech_specs, mock_device, fake_dummy):
    """(A10) `--extras <dir> --extras-size none`: the extras register on the
    SEASON_MAP (never an episode leaf), upload after the episode loop, and are
    dummied locally — while every episode still ends `archived`."""
    base_id = "tv-en-2020-xtras-s01"
    # NOTE: keep apostrophes OUT of fixture folder names. main.py shell-escapes
    # `'` as `'\''` for the adb `mv`, but pushes the RAW path; the `mock_device`
    # fake only does `.strip("'")`, so the two disagree and the push "fails".
    # That is a fixture limitation, not a product bug.
    season_dir = sandbox["local_root"] / "Series" / "Extras S01"
    _seed_episodes(season_dir, make_video, ["XT.S01E01.mkv", "XT.S01E02.mkv"])
    extras_dir = season_dir / "Specials"
    extras_dir.mkdir()
    bts, bts_hash = make_video(extras_dir / "BTS.mkv", marker=b"SEASON-BTS\n")
    bts_bytes = open(bts, "rb").read()

    main.cmd_prep_push_rep_season(base_id, str(season_dir),
                                  extras=[str(extras_dir)],
                                  extras_size=("NONE", None))

    lib = mvcommon.load_library()
    groups = lib[base_id].get("extras", {}).get("groups", {})
    assert sorted(groups) == ["Specials"], lib[base_id].get("extras")
    item, = groups["Specials"]["items"]
    assert item["sub_rel"] == "BTS.mkv" and item["hash"] == bts_hash
    for eid in (f"{base_id}e01", f"{base_id}e02"):
        assert lib[eid]["status"] == "archived"
        assert "extras" not in lib[eid], "extras belong on the season_map, not a leaf"
    on_device = _device_names(mock_device)
    remote = f"BTS [{item['short_id']}].mkv"
    assert remote in on_device, sorted(on_device)
    assert on_device[remote].read_bytes() == bts_bytes
    assert (extras_dir / "BTS.mkv").read_bytes() == FAKE_DUMMY_BYTES


def test_regression_season_combined_flags_all_take_effect_together(
        sandbox, make_video, stub_tech_specs, mock_device, fake_dummy, monkeypatch):
    """(A11) The combination case: `episodes 1-2` + `SIZE_GB 9` + `device` +
    `--extras`/`--extras-size` on a 3-episode season. Every flag's individual
    effect must survive being combined — range filtering, the below-threshold
    size skip, the device serial on every adb call, and the extras round trip."""
    base_id = "tv-en-2020-combo-s01"
    season_dir = sandbox["local_root"] / "Series" / "Combo S01"
    _seed_episodes(season_dir, make_video,
                   [f"CMB.S01E0{n}.mkv" for n in (1, 2, 3)])
    extras_dir = season_dir / "Specials"
    extras_dir.mkdir()
    make_video(extras_dir / "BTS.mkv", marker=b"COMBO-BTS\n")
    argvs = _record_subprocess(monkeypatch)

    main.cmd_prep_push_rep_season(base_id, str(season_dir), "SIZE_GB", "9",
                                  episode_range="1-2", device_id="fakeserial",
                                  extras=[str(extras_dir)],
                                  extras_size=("NONE", None))

    lib = mvcommon.load_library()
    assert [lib[f"{base_id}e0{n}"]["status"] for n in (1, 2, 3)] == [
        "archived", "archived", "local_ready"]
    assert all("split_info" not in lib[f"{base_id}e0{n}"] for n in (1, 2, 3))
    assert sorted(lib[base_id]["extras"]["groups"]) == ["Specials"]
    assert (extras_dir / "BTS.mkv").read_bytes() == FAKE_DUMMY_BYTES
    for argv in [a for a in argvs if a and a[0] == "adb"]:
        assert argv[1:3] == ["-s", "fakeserial"], argv


def test_regression_enrich_wrapper_forwards_every_archive_argument_verbatim(
        sandbox, monkeypatch, capsys):
    """(A12) The load-bearing "existing functionality is unaffected" proof for
    the WRAPPER: `cmd_prep_push_rep_season_enrich` must hand
    `cmd_prep_push_rep_season` EXACTLY the arguments it was given — same values,
    same slots — and must add nothing of its own to the archive leg. The spy's
    signature mirrors the real function's, so a reordered positional would
    surface as a wrong-value assertion rather than a silent behaviour change."""
    seen = {}

    def _spy(base_id, folder_path, split_method=None, split_val=None,
             episode_range=None, device_id=None, eager_rehash=False,
             temp_dir=None, extras=None, extras_size=None):
        seen.update({
            "base_id": base_id, "folder_path": folder_path,
            "split_method": split_method, "split_val": split_val,
            "episode_range": episode_range, "device_id": device_id,
            "eager_rehash": eager_rehash, "temp_dir": temp_dir,
            "extras": extras, "extras_size": extras_size,
        })

    monkeypatch.setattr(main, "cmd_prep_push_rep_season", _spy)

    result = main.cmd_prep_push_rep_season_enrich(
        "tv-en-2020-fwd-s01", r"C:\sandbox\Series\Fwd S01",
        split_method="SIZE_MB", split_val="700", episode_range="2-4",
        device_id="fakeserial", eager_rehash=True, temp_dir=r"C:\scratch",
        extras=[r"C:\sandbox\Series\Fwd S01\Specials"],
        extras_size=("SIZE_MB", "500"),
        tmdb_id=70523, write_nfo=True, no_web=True, rename_choice="no")

    assert seen == {
        "base_id": "tv-en-2020-fwd-s01",
        "folder_path": r"C:\sandbox\Series\Fwd S01",
        "split_method": "SIZE_MB", "split_val": "700",
        "episode_range": "2-4", "device_id": "fakeserial",
        "eager_rehash": True, "temp_dir": r"C:\scratch",
        "extras": [r"C:\sandbox\Series\Fwd S01\Specials"],
        "extras_size": ("SIZE_MB", "500"),
    }
    # The archive never ran (the spy is a no-op), so the completion check below
    # it correctly reports nothing archived and returns False WITHOUT enriching.
    assert result is False
    assert "nothing was archived this run" in capsys.readouterr().out


# ---------------------------------------------------------------------------
#   GROUP B — the new command's FULL artifact inventory, BOTH layouts.
#
#   Seed helpers (the two layouts) + a richer TV TMDB fake whose image bytes
#   ENCODE THEIR OWN SOURCE URL, so every artwork assertion proves PROVENANCE
#   (show endpoint vs season endpoint vs episode endpoint) instead of merely
#   proving "some jpeg landed here".
# ---------------------------------------------------------------------------

def _seed_nested_season_show(sandbox, make_video, show="Dark", season="Season 01",
                             filenames=("Dark.S01E01.mkv", "Dark.S01E02.mkv")):
    """LAYOUT (i) — the classic Plex/Emby/Jellyfin `<Show>/Season NN/<episodes>`
    tree. `_show_folder_of` sees ONE season folder whose basename matches its
    season-like regex (`^season[\\s_]*\\d+$|^s\\d+$`) and climbs to the PARENT,
    so the show folder and the season folder are DIFFERENT paths.

    Returns (show_dir, season_dir)."""
    show_dir = sandbox["local_root"] / "Series" / show
    season_dir = show_dir / season
    _seed_episodes(season_dir, make_video, list(filenames))
    return show_dir, season_dir


def _seed_flat_layout_season(sandbox, make_video, folder="Peaky.Blinders.S06.2022",
                             filenames=("Peaky.Blinders.S06E01.mkv",
                                        "Peaky.Blinders.S06E02.mkv")):
    """LAYOUT (ii) — ONE release-named folder holding the episodes DIRECTLY,
    with NO `Season NN` subdirectory. Its basename deliberately does NOT match
    `_show_folder_of`'s season-like regex, so that function's third branch
    returns this SAME folder as both the show folder and the season folder.

    This is the DOMINANT real-world shape for this user (46 of their shows —
    2026-08-28 library audit, Decision 6 "Why harder" reason 1), which is why
    it is a first-class scenario here rather than an edge case.

    Returns season_dir (which IS also the show folder)."""
    season_dir = sandbox["local_root"] / "Series" / folder
    _seed_episodes(season_dir, make_video, list(filenames))
    return season_dir


_IMAGE_BASE = "https://image.tmdb.org/t/p/"


def _image_bytes(url):
    """Deterministic fake-JPEG bytes that ENCODE the `<size>/<file_path>` they
    were fetched from, e.g. `w342/showposter.jpg`.

    A single shared constant (like the conftest `mock_tmdb`'s `_FAKE_JPG`)
    cannot distinguish a SHOW poster from a SEASON poster from an EPISODE
    still — and telling those apart is the whole point of the artifact
    inventory. Encoding the source URL makes every artwork assertion a
    provenance assertion."""
    return b"\xff\xd8\xff\xe0IMG:" + url.split("/t/p/", 1)[-1].encode() + b"\xff\xd9"


def _expect_img(size, file_path):
    """The bytes `_image_bytes` will produce for a TMDB `<size>` + `file_path`."""
    return _image_bytes(f"{_IMAGE_BASE}{size}{file_path}")


class _FakeTMDBSeriesFull:
    """Full-artifact TV TMDB fake — serves EVERY endpoint a series enrich can
    touch, so all 7 inventory rows are exercised against canned data:

      * `/configuration`                              -> the image base
      * `/tv/{id}`                                    -> details (rows 1/6/7)
      * `/tv/{id}/season/{n}/images`                  -> season posters (row 3)
      * `/tv/{id}/season/{n}/episode/{e}/images`      -> episode stills (row 4)
      * `/tv/{id}/season/{n}`                         -> season details (row 5)
      * `/tv/{id}/external_ids`                       -> imdb_id  (row 7)
      * `/tv/{id}/credits`                            -> cast     (row 7)
      * `/search/tv`                                  -> only when `search` is
        seeded; a preset-id run must NEVER reach it.

    Any other URL degrades to `{}` (never a 404) so untested graceful-omit
    paths stay graceful. Image URLs return `_image_bytes(url)`."""

    def __init__(self, tv_id, details, season_posters=None, episode_stills=None,
                 season_episodes=None, external_ids=None, credits=None, search=None):
        self.tv_id = tv_id
        self.details = details
        self.season_posters = season_posters or {}      # {season_no: [posters]}
        self.episode_stills = episode_stills or {}      # {(season_no, ep_no): [stills]}
        self.season_episodes = season_episodes or {}    # {season_no: [episode dicts]}
        self.external_ids = external_ids if external_ids is not None else {}
        self.credits = credits if credits is not None else {}
        self.search = {k.lower(): v for k, v in (search or {}).items()}
        self.calls = []
        self.image_urls = []

    @property
    def urls(self):
        return [u for u, _ in self.calls]

    def get(self, url, params=None, headers=None, timeout=None, **kwargs):
        params = params or {}
        self.calls.append((url, dict(params)))

        if url.startswith(_IMAGE_BASE):
            self.image_urls.append(url)
            return _Resp(200, content=_image_bytes(url))
        if url.endswith("/configuration"):
            return _Resp(200, json_data={"images": {"secure_base_url": _IMAGE_BASE}})
        if "/search/tv" in url:
            q = (params.get("query") or "").lower()
            return _Resp(200, json_data={"results": self.search.get(q, [])})

        # The EPISODE-images URL also contains "/season/" and ends in "/images",
        # so it MUST be matched before the season branch (same ordering hazard
        # the conftest MockTMDB documents).
        m = re.search(r"/tv/(\d+)/season/(\d+)/episode/(\d+)/images$", url)
        if m:
            key = (int(m.group(2)), int(m.group(3)))
            return _Resp(200, json_data={"stills": self.episode_stills.get(key, [])})
        m = re.search(r"/tv/(\d+)/season/(\d+)/images$", url)
        if m:
            return _Resp(200, json_data={"posters": self.season_posters.get(int(m.group(2)), [])})
        m = re.search(r"/tv/(\d+)/season/(\d+)$", url)
        if m:
            return _Resp(200, json_data={"episodes": self.season_episodes.get(int(m.group(2)), [])})

        if url.endswith(f"/tv/{self.tv_id}/external_ids"):
            return _Resp(200, json_data=self.external_ids)
        if url.endswith(f"/tv/{self.tv_id}/credits"):
            return _Resp(200, json_data=self.credits)
        if url.endswith(f"/tv/{self.tv_id}"):
            return _Resp(200, json_data=self.details)
        return _Resp(200, json_data={})


def _tv_details_full(tmdb_id, name, year, **extra):
    """A `/3/tv/{id}` DETAILS payload carrying BOTH the fields the resolver
    reads (name/first_air_date/poster_path/backdrop_path/overview/vote_average)
    AND the ones Decision 4's richer NFO reads (genres/episode_run_time/
    networks/created_by)."""
    detail = {
        "id": tmdb_id, "name": name, "first_air_date": f"{year}-03-04",
        "poster_path": "/showposter.jpg", "backdrop_path": "/showbackdrop.jpg",
        "overview": f"The show synopsis for {name}.",
        "vote_average": 8.4,
        "genres": [{"id": 18, "name": "Drama"}, {"id": 80, "name": "Crime"}],
        "episode_run_time": [58],
        "networks": [{"id": 1, "name": "BBC One"}],
        "created_by": [{"name": "A Creator"}],
    }
    detail.update(extra)
    return detail


_SEASON1_EPISODES = [
    {"episode_number": 1, "name": "Secrets", "overview": "Episode one synopsis."},
    {"episode_number": 2, "name": "Lies", "overview": "Episode two synopsis."},
]
_SEASON6_EPISODES = [
    {"episode_number": 1, "name": "Black Day", "overview": "S06E01 synopsis."},
    {"episode_number": 2, "name": "Black Shirt", "overview": "S06E02 synopsis."},
]
_CREDITS = {"cast": [{"name": "Lead Actor", "character": "Lead"},
                     {"name": "Second Actor", "character": "Second"}], "crew": []}


# --- B1: NESTED layout, every row of the artifact-inventory table -----------

def test_inventory_nested_layout_full_artifact_checklist(
        sandbox, make_video, stub_tech_specs, mock_device, fake_dummy,
        patch_tmdb_by_id, capsys):
    """Inventory rows 1-6 for LAYOUT (i) (`<Show>/Season NN/`), id-supplied
    happy path over a whole 2-episode season:

      row 1  show `poster.jpg` + `fanart.jpg` at the folder `_show_folder_of`
             resolves — here the PARENT of `Season 01`
      row 2  the `{tmdb-…}` token stamped on that SAME show folder, with
             `Season 01` intact underneath it
      row 3  a per-season `poster.jpg` inside `Season 01` that is a DIFFERENT
             file from the show poster — its bytes must come from the
             `/season/{n}/images` endpoint, not the show details payload
      row 4  a `<episode-basename>-thumb.jpg` next to EACH episode video
      row 5  per-episode `metadata.overview` + `metadata.episode_title`
      row 6  `metadata.tmdb_id` + real title/year/overview on EVERY leaf AND
             the season_map
    """
    base_id = "tv-en-2017-dark-s01"
    show_dir, season_dir = _seed_nested_season_show(sandbox, make_video)

    TMDB_ID = 70523
    fake = patch_tmdb_by_id(_FakeTMDBSeriesFull(
        TMDB_ID, _tv_details_full(TMDB_ID, "Dark", 2017),
        season_posters={1: [{"file_path": "/season01poster.jpg"}]},
        episode_stills={(1, 1): [{"file_path": "/s01e01still.jpg", "vote_average": 6.0}],
                        (1, 2): [{"file_path": "/s01e02still.jpg", "vote_average": 6.0}]},
        season_episodes={1: _SEASON1_EPISODES},
    ))

    assert main.cmd_prep_push_rep_season_enrich(
        base_id, str(season_dir), tmdb_id=TMDB_ID, rename_choice="yes") is True

    out = capsys.readouterr().out
    assert not any("/search/" in u for u in fake.urls), "a preset id must never search"

    # --- row 2: the token landed on the SHOW folder (the PARENT) ------------
    stamped_show = show_dir.parent / "Dark {tmdb-70523}"
    stamped_season = stamped_show / "Season 01"
    assert stamped_show.is_dir() and not show_dir.exists()
    assert stamped_season.is_dir(), "the season subfolder must move WITH the show folder"

    # --- row 1: show poster + fanart, at the SHOW folder --------------------
    assert (stamped_show / "poster.jpg").read_bytes() == _expect_img("w342", "/showposter.jpg")
    assert (stamped_show / "fanart.jpg").read_bytes() == _expect_img("w780", "/showbackdrop.jpg")

    # --- row 3: per-season poster, DISTINCT from the show poster ------------
    assert any(u.endswith(f"/tv/{TMDB_ID}/season/1/images") for u in fake.urls), \
        "the per-season images endpoint must be called for the nested layout"
    season_poster = (stamped_season / "poster.jpg").read_bytes()
    assert season_poster == _expect_img("w342", "/season01poster.jpg")
    assert season_poster != (stamped_show / "poster.jpg").read_bytes(), \
        "nested layout: show art and season art are DIFFERENT files"

    # --- row 4: a per-episode still next to each episode video --------------
    assert (stamped_season / "Dark.S01E01-thumb.jpg").read_bytes() == \
        _expect_img("w300", "/s01e01still.jpg")
    assert (stamped_season / "Dark.S01E02-thumb.jpg").read_bytes() == \
        _expect_img("w300", "/s01e02still.jpg")

    lib = mvcommon.load_library()
    ep1, ep2, season_map = lib[f"{base_id}e01"], lib[f"{base_id}e02"], lib[base_id]

    # --- row 5: per-episode overview + episode_title ------------------------
    assert ep1["metadata"]["episode_title"] == "Secrets"
    assert ep1["metadata"]["overview"] == "Episode one synopsis."
    assert ep2["metadata"]["episode_title"] == "Lies"
    assert ep2["metadata"]["overview"] == "Episode two synopsis."

    # --- row 6: tmdb_id + real title/year on EVERY leaf AND the season_map --
    for entry in (ep1, ep2, season_map):
        assert entry["metadata"]["tmdb_id"] == TMDB_ID
        assert entry["metadata"]["title"] == "Dark"
        assert entry["metadata"]["year"] == 2017
    # The SHOW synopsis stays on the season_map; the episode leaves are
    # deliberately REFINED to their own synopsis by `_apply_episode_overviews`.
    assert season_map["metadata"]["overview"] == "The show synopsis for Dark."

    # The archive itself is unaffected by the enrich leg.
    assert ep1["status"] == "archived" and ep2["status"] == "archived"
    assert ep1["folder_path"] == str(stamped_season)
    assert "this is the SHOW folder" in out, "nested layout keeps the parent-folder note"


# --- B2: FLAT layout, every row + the show/season folder collision ----------

def test_inventory_flat_layout_full_artifact_checklist(
        sandbox, make_video, stub_tech_specs, mock_device, fake_dummy,
        patch_tmdb_by_id, monkeypatch, capsys):
    """Inventory rows 1-6 for LAYOUT (ii) (one release folder, episodes
    directly inside — the DOMINANT real shape). Everything matches B1 EXCEPT
    row 3: the show folder and the season folder are the LITERAL SAME PATH, so
    the show `poster.jpg` is written first and the per-season poster collides
    with it.

    DEVIATION D-A (see the header block): PLAN.md's prose says the season-images
    endpoint "IS still called … but its result is DISCARDED". The actual,
    unmodified `_download_unit_images` checks `os.path.exists(dest)` and
    `continue`s BEFORE issuing the GET, so the endpoint is NOT called at all —
    strictly better (one fewer API round trip), same user-visible outcome (a
    "kept" skip, no error, no overwrite). This test pins the REAL behaviour."""
    base_id = "tv-en-2022-peakyblinders-s06"
    season_dir = _seed_flat_layout_season(sandbox, make_video)

    TMDB_ID = 60574
    fake = patch_tmdb_by_id(_FakeTMDBSeriesFull(
        TMDB_ID, _tv_details_full(TMDB_ID, "Peaky Blinders", 2022),
        season_posters={6: [{"file_path": "/season06poster.jpg"}]},
        episode_stills={(6, 1): [{"file_path": "/s06e01still.jpg", "vote_average": 7.0}],
                        (6, 2): [{"file_path": "/s06e02still.jpg", "vote_average": 7.0}]},
        season_episodes={6: _SEASON6_EPISODES},
    ))

    rename_calls = []
    real_rename = main.cmd_rename_folder

    def _spy_rename(folder, new_name):
        rename_calls.append((folder, new_name))
        return real_rename(folder, new_name)

    monkeypatch.setattr(main, "cmd_rename_folder", _spy_rename)

    assert main.cmd_prep_push_rep_season_enrich(
        base_id, str(season_dir), tmdb_id=TMDB_ID, rename_choice="yes") is True

    out = capsys.readouterr().out
    stamped = season_dir.parent / "Peaky.Blinders.S06.2022 {tmdb-60574}"

    # --- row 2: EXACTLY ONE rename, onto the one and only folder ------------
    assert len(rename_calls) == 1, rename_calls
    assert rename_calls[0][1] == "Peaky.Blinders.S06.2022 {tmdb-60574}"
    assert stamped.is_dir() and not season_dir.exists()
    assert "this is the SHOW folder" not in out, \
        "flat layout: show folder == season folder -> the parent note would be WRONG"

    # --- row 1: show poster + fanart land in that same folder ---------------
    assert (stamped / "poster.jpg").read_bytes() == _expect_img("w342", "/showposter.jpg")
    assert (stamped / "fanart.jpg").read_bytes() == _expect_img("w780", "/showbackdrop.jpg")

    # --- row 3: the season poster is SKIPPED as "kept", never overwritten ---
    assert "local season poster present — kept" in out
    assert "image download failed" not in out and "could not write image" not in out, \
        "the show/season folder collision must be a graceful skip, never an error"
    assert (stamped / "poster.jpg").read_bytes() != _expect_img("w342", "/season06poster.jpg"), \
        "the SHOW-level image must win — the season poster must not overwrite it"
    assert not any(u.endswith(f"/tv/{TMDB_ID}/season/6/images") for u in fake.urls), \
        ("DEVIATION D-A: _download_unit_images short-circuits on os.path.exists "
         "BEFORE the season-images GET, so the endpoint is never reached here")
    assert sorted(p.name for p in stamped.glob("*.jpg")) == [
        "Peaky.Blinders.S06E01-thumb.jpg", "Peaky.Blinders.S06E02-thumb.jpg",
        "fanart.jpg", "poster.jpg",
    ], "exactly ONE poster.jpg and ONE fanart.jpg, plus the per-episode stills"

    # --- row 4: per-episode stills are keyed off the EPISODE filename, so the
    #            folder collision cannot affect them -------------------------
    assert (stamped / "Peaky.Blinders.S06E01-thumb.jpg").read_bytes() == \
        _expect_img("w300", "/s06e01still.jpg")
    assert (stamped / "Peaky.Blinders.S06E02-thumb.jpg").read_bytes() == \
        _expect_img("w300", "/s06e02still.jpg")

    lib = mvcommon.load_library()
    ep1, ep2, season_map = lib[f"{base_id}e01"], lib[f"{base_id}e02"], lib[base_id]

    # --- row 5 -------------------------------------------------------------
    assert ep1["metadata"]["episode_title"] == "Black Day"
    assert ep1["metadata"]["overview"] == "S06E01 synopsis."
    assert ep2["metadata"]["episode_title"] == "Black Shirt"

    # --- row 6 -------------------------------------------------------------
    for entry in (ep1, ep2, season_map):
        assert entry["metadata"]["tmdb_id"] == TMDB_ID
        assert entry["metadata"]["title"] == "Peaky Blinders"
        assert entry["metadata"]["year"] == 2022
    assert season_map["metadata"]["overview"] == "The show synopsis for Peaky Blinders."
    assert ep1["status"] == "archived" and ep2["status"] == "archived"
    assert ep1["folder_path"] == str(stamped) == season_map["folder_path"]


# --- B3: episode_range sub-selection still stamps the token ----------------

def test_episode_range_that_fully_archives_still_stamps_the_token(
        sandbox, make_video, stub_tech_specs, mock_device, fake_dummy,
        patch_tmdb_by_id):
    """An `episode_range` run whose RANGE fully archives enriches normally —
    including the `{tmdb-…}` folder stamp — even though episodes OUTSIDE the
    range are still `local_ready`. (The existing range test asserts the
    metadata side with `rename_choice="no"`; this pins the STAMP side.)"""
    base_id = "tv-en-2019-theexpanse-s05"
    season_dir = _seed_flat_layout_season(
        sandbox, make_video, folder="TheExpanse.S05.2020",
        filenames=[f"TheExpanse.S05E0{n}.mkv" for n in (1, 2, 3)])

    TMDB_ID = 63639
    patch_tmdb_by_id(_FakeTMDBSeriesFull(
        TMDB_ID, _tv_details_full(TMDB_ID, "The Expanse", 2020),
        season_episodes={5: [{"episode_number": 1, "name": "Aberration",
                              "overview": "S05E01 synopsis."}]},
    ))

    assert main.cmd_prep_push_rep_season_enrich(
        base_id, str(season_dir), episode_range="1-2", tmdb_id=TMDB_ID,
        rename_choice="yes") is True

    stamped = season_dir.parent / f"TheExpanse.S05.2020 {{tmdb-{TMDB_ID}}}"
    assert stamped.is_dir() and not season_dir.exists()
    assert (stamped / "poster.jpg").exists()
    lib = mvcommon.load_library()
    assert [lib[f"{base_id}e0{n}"]["status"] for n in (1, 2, 3)] == [
        "archived", "archived", "local_ready"]
    # The out-of-range episode moved with the folder and still got the id.
    assert lib[f"{base_id}e03"]["folder_path"] == str(stamped)
    assert lib[f"{base_id}e03"]["metadata"]["tmdb_id"] == TMDB_ID


# --- B4: scope isolation WITHOUT an explicit -tmdbid -----------------------

def test_sibling_season_preset_is_never_reused_without_an_explicit_id(
        sandbox, make_video, stub_tech_specs, mock_device, fake_dummy,
        patch_tmdb_by_id, capsys):
    """POSITIVE proof of Decision 6's `base_id`-scoped design (replacing the
    original "sibling preset reuse" scenario).

    The user's real ids embed a DIFFERENT year per season
    (`tv-en-2022-peakyblinders-s06` vs `tv-en-2019-peakyblinders-s05`), so the
    two seasons share no id prefix and land in SEPARATE `_gather_enrich_units`
    buckets. Running season 06 with NO `-tmdbid` while season 05 already carries
    a preset must therefore: never fetch season 05's id, run season 06's OWN
    search waterfall, and (with nothing seeded for it) report a clean
    "NO TMDB match" without writing anything. This is expected, documented
    behaviour — NOT a bug to fix."""
    base_id = "tv-en-2022-peakyblinders-s06"
    sibling_id = "tv-en-2019-peakyblinders-s05"
    sibling_ep = f"{sibling_id}e01"
    SIBLING_PRESET = 999999

    season_dir = _seed_flat_layout_season(
        sandbox, make_video, filenames=["Peaky.Blinders.S06E01.mkv"])
    sibling_dir = sandbox["local_root"] / "Series" / "Peaky.Blinders.S05.2019"
    sibling_dir.mkdir(parents=True, exist_ok=True)

    # The sibling season is seeded directly — it must never be looked at.
    lib = mvcommon.load_library()
    lib[sibling_id] = {"type": "season_map", "folder_path": str(sibling_dir),
                       "total_episodes": 1, "children": [sibling_ep]}
    lib[sibling_ep] = {
        "filename": "Peaky.Blinders.S05E01.mkv", "folder_path": str(sibling_dir),
        "status": "archived", "uploaded": True, "parent_id": sibling_id,
        "metadata": {"tmdb_id": SIBLING_PRESET, "title": "Peaky Blinders"},
    }
    mvcommon.save_library(lib)

    # No `search` seeded -> season 06's own waterfall finds nothing.
    fake = patch_tmdb_by_id(_FakeTMDBSeriesFull(60574, _tv_details_full(60574, "Peaky Blinders", 2022)))

    assert main.cmd_prep_push_rep_season_enrich(
        base_id, str(season_dir), rename_choice="no") is True

    out = capsys.readouterr().out
    assert "NO TMDB match" in out, out
    # Season 06 ran its OWN search; season 05's preset id was NEVER fetched.
    assert any("/search/tv" in u for u in fake.urls), "season 06 must run its own waterfall"
    assert not any(f"/tv/{SIBLING_PRESET}" in u for u in fake.urls), \
        "the sibling season's preset id must be unreachable from this run"

    lib = mvcommon.load_library()
    assert "tmdb_id" not in lib[f"{base_id}e01"].get("metadata", {}), \
        "an unresolved season must never inherit a sibling's id"
    assert lib[f"{base_id}e01"]["status"] == "archived", "the ARCHIVE still succeeded"
    # The sibling is byte-for-byte untouched.
    assert lib[sibling_ep]["metadata"]["tmdb_id"] == SIBLING_PRESET
    assert lib[sibling_id]["folder_path"] == str(sibling_dir)
    assert sibling_dir.is_dir()


# --- B5: the preset lands on an episode LEAF, never the season_map ---------

def test_preset_tmdb_id_lands_on_a_leaf_never_on_the_season_map(
        sandbox, make_video, stub_tech_specs, mock_device, fake_dummy,
        monkeypatch):
    """`cmd_set_tmdb` REFUSES a season_map container, so a CLI `-tmdbid` for a
    season must be written to an episode LEAF instead (`_unit_preset_tmdb_id`
    scans every id of the unit, so a leaf preset is found just the same).

    The library is snapshotted at the exact moment the enrich leg is entered —
    i.e. AFTER the preset, BEFORE the apply loop (which legitimately DOES add
    `metadata` to the season_map for inventory row 6). Asserting later would
    conflate the two writes."""
    base_id = "tv-en-2017-dark-s01"
    _show_dir, season_dir = _seed_nested_season_show(sandbox, make_video)
    TMDB_ID = 70523

    captured = {}

    def _spy_enrich(real_id, write_nfo, no_web, gate):
        captured["real_id"] = real_id
        captured["library"] = mvcommon.load_library()

    monkeypatch.setattr(main, "_enrich_after_archive", _spy_enrich)

    assert main.cmd_prep_push_rep_season_enrich(
        base_id, str(season_dir), tmdb_id=TMDB_ID, rename_choice="no") is True

    # Decision 6: the enrich leg is scoped by base_id DIRECTLY, not a derived
    # show id.
    assert captured["real_id"] == base_id
    at_preset = captured["library"]
    assert at_preset[base_id]["type"] == "season_map"
    assert "metadata" not in at_preset[base_id], \
        "cmd_set_tmdb refuses a season_map — the preset must not land there"
    assert at_preset[f"{base_id}e01"]["metadata"]["tmdb_id"] == TMDB_ID
    assert "tmdb_id" not in at_preset[f"{base_id}e02"].get("metadata", {}), \
        "the preset goes on the FIRST leaf only; the unit scan finds it from there"


# --- B6: -tvdbid refusal wins even when a -tmdbid is also supplied ---------

def test_tvdbid_refusal_wins_even_when_tmdbid_is_also_supplied(
        sandbox, make_video, stub_tech_specs, monkeypatch, capsys):
    """Decision 1 is a hard refusal, not a preference: supplying BOTH ids must
    still refuse and run NOTHING (no archive, no enrich) rather than quietly
    honouring the tmdb one."""
    base_id = "tv-en-2017-dark-s01"
    _show_dir, season_dir = _seed_nested_season_show(sandbox, make_video)

    def _must_not_be_called(*a, **kw):
        raise AssertionError("nothing may run when -tvdbid is supplied")

    monkeypatch.setattr(main, "cmd_prep_push_rep_season", _must_not_be_called)
    monkeypatch.setattr(main, "_enrich_after_archive", _must_not_be_called)

    assert main.cmd_prep_push_rep_season_enrich(
        base_id, str(season_dir), tmdb_id=70523, tvdb_id=82066) is False
    assert "MediaVault is TMDB-only for movies, series, and anime" in capsys.readouterr().out
    assert not sandbox["lib_series"].exists()


# --- B7: mid-season archive failure — enrich skipped, resume text intact ----

def test_mid_season_failure_skips_enrich_and_leaves_resume_message_untouched(
        sandbox, make_video, stub_tech_specs, mock_device, fake_dummy,
        monkeypatch, capsys):
    """When the season loop stops early, the enrich leg must not run AND the
    EXISTING `_season_resume_cmd` message must be printed EXACTLY as it is
    today — no new command wired in front of it, none trailing after it.

    This is the rollback-adjacent guarantee `cmd_prep_push_rep_season_enrich`'s
    docstring makes (CLAUDE.md's auto-rollback change-gate): the new command
    neither touches nor reimplements that closure."""
    base_id = "tv-en-2020-halted-s01"
    season_dir = _seed_flat_layout_season(
        sandbox, make_video, folder="Halted.S01.2020",
        filenames=[f"HLT.S01E0{n}.mkv" for n in (1, 2, 3)])

    real_push = main.cmd_push

    def _fail_e02(mid, *a, **kw):
        return False if mid == f"{base_id}e02" else real_push(mid, *a, **kw)

    monkeypatch.setattr(main, "cmd_push", _fail_e02)

    def _enrich_must_not_run(*a, **kw):
        raise AssertionError("enrich must not run when the archive stopped early")

    monkeypatch.setattr(main, "_enrich_after_archive", _enrich_must_not_run)

    assert main.cmd_prep_push_rep_season_enrich(
        base_id, str(season_dir), tmdb_id=60574, rename_choice="yes") is False

    out = capsys.readouterr().out
    resume_lines = [l for l in out.splitlines() if "Resume the rest of the season:" in l]
    assert len(resume_lines) == 1, out
    assert resume_lines[0] == (
        f'   > Resume the rest of the season: prep_push_rep_season {base_id} '
        f'"{season_dir}" episodes 02-03')
    assert "prep_push_rep_season_enrich" not in out, \
        "the resume messaging must not have been rewritten to name the new command"
    assert f"{base_id}e02 is not archived yet" in out
    lib = mvcommon.load_library()
    assert lib[f"{base_id}e01"]["status"] == "archived"
    assert lib[f"{base_id}e02"]["status"] == "local_ready"
    assert not (season_dir.parent / f"Halted.S01.2020 {{tmdb-60574}}").exists()


# --- B8: `_season_run_target_ids` de-aliases a combined-episode season ------

def test_season_run_target_ids_dealiases_a_combined_episode_season(sandbox_alias):
    """A `multi_ep_alias` child (e.g. `…S04E19E20.mkv` producing an e20 alias
    row) must collapse to its PRIMARY leaf exactly once — both for a whole
    season and for a range that spans the alias."""
    season_id = sandbox_alias["season_id"]
    primary_id = sandbox_alias["primary_id"]
    library = mvcommon.load_library()

    assert library[sandbox_alias["alias_id"]]["type"] == "multi_ep_alias"
    assert main._season_run_target_ids(library, season_id, None) == [primary_id]
    assert main._season_run_target_ids(library, season_id, "19-20") == [primary_id]
    assert main._season_run_target_ids(library, season_id, "20-20") == [primary_id]
    assert main._season_run_target_ids(library, season_id, "1-2") == []


# --- B9: a post-PONR rename failure warns and continues (Decision 7) --------

def test_rollback_hard_fail_during_stamp_warns_and_still_completes(
        sandbox, make_video, stub_tech_specs, mock_device, fake_dummy,
        patch_tmdb_by_id, monkeypatch, capsys):
    """Decision 7: the ONE genuine exception the enrich leg can propagate is a
    `RollbackHardFail` from a post-PONR `cmd_rename_folder`. It must become a
    printed warning naming the EXISTING resume command — never an aborted
    return — because the archive has already succeeded."""
    base_id = "tv-en-2022-peakyblinders-s06"
    season_dir = _seed_flat_layout_season(
        sandbox, make_video, filenames=["Peaky.Blinders.S06E01.mkv"])
    TMDB_ID = 60574
    patch_tmdb_by_id(_FakeTMDBSeriesFull(TMDB_ID, _tv_details_full(TMDB_ID, "Peaky Blinders", 2022)))

    def _boom(folder, new_name):
        raise main.RollbackHardFail(
            "PARTIALLY RENAMED", "the folder moved but the library rewrite failed",
            f'rename_folder "{folder}" "{new_name}"')

    monkeypatch.setattr(main, "cmd_rename_folder", _boom)

    assert main.cmd_prep_push_rep_season_enrich(
        base_id, str(season_dir), tmdb_id=TMDB_ID, rename_choice="yes") is True

    out = capsys.readouterr().out
    assert "Enrich folder rename left incomplete: PARTIALLY RENAMED" in out
    assert "the folder moved but the library rewrite failed" in out
    assert "> To finish it: rename_folder " in out
    assert "AUTO-PILOT COMPLETE (archive + enrich)" in out

    lib = mvcommon.load_library()
    assert lib[f"{base_id}e01"]["status"] == "archived", "the archive is untouched"
    assert lib[f"{base_id}e01"]["metadata"]["tmdb_id"] == TMDB_ID, \
        "the metadata write happens BEFORE the stamp, so it survives"
    # Documented consequence: the hard fail aborts the rest of the enrich leg,
    # so no artwork is downloaded on this run.
    assert not (season_dir / "poster.jpg").exists()


# --- B10: LOCAL ALWAYS WINS across every artwork row (1, 3, 4) --------------

def test_local_always_wins_across_the_full_inventory(
        sandbox, make_video, stub_tech_specs, mock_device, fake_dummy,
        patch_tmdb_by_id, capsys):
    """Every artwork row that writes a file is LOCAL-ALWAYS-WINS. Pre-seed a
    show `poster.jpg` (row 1), a season `poster.jpg` (row 3 — nested layout, the
    only layout where a season poster is a distinct file) and ONE episode's
    `-thumb.jpg` (row 4) with known non-TMDB bytes; all three must be
    byte-identical afterwards, each reported as "kept".

    The control assertions matter as much as the kept ones: `fanart.jpg` and the
    OTHER episode's thumb must still be downloaded, proving the run really did
    reach the image step rather than skipping it wholesale.

    (Row 7, `tvshow.nfo`, is deliberately NOT local-always-wins — see
    DEVIATION D-B in the header and `test_nfo_is_regenerated_not_kept`.)"""
    base_id = "tv-en-2017-dark-s01"
    show_dir, season_dir = _seed_nested_season_show(sandbox, make_video)

    LOCAL_SHOW_POSTER = b"LOCAL-SHOW-POSTER-DO-NOT-TOUCH"
    LOCAL_SEASON_POSTER = b"LOCAL-SEASON-POSTER-DO-NOT-TOUCH"
    LOCAL_THUMB = b"LOCAL-EPISODE-THUMB-DO-NOT-TOUCH"
    (show_dir / "poster.jpg").write_bytes(LOCAL_SHOW_POSTER)
    (season_dir / "poster.jpg").write_bytes(LOCAL_SEASON_POSTER)
    (season_dir / "Dark.S01E01-thumb.jpg").write_bytes(LOCAL_THUMB)

    TMDB_ID = 70523
    patch_tmdb_by_id(_FakeTMDBSeriesFull(
        TMDB_ID, _tv_details_full(TMDB_ID, "Dark", 2017),
        season_posters={1: [{"file_path": "/season01poster.jpg"}]},
        episode_stills={(1, 1): [{"file_path": "/s01e01still.jpg", "vote_average": 6.0}],
                        (1, 2): [{"file_path": "/s01e02still.jpg", "vote_average": 6.0}]},
    ))

    assert main.cmd_prep_push_rep_season_enrich(
        base_id, str(season_dir), tmdb_id=TMDB_ID, rename_choice="yes") is True

    out = capsys.readouterr().out
    stamped_show = show_dir.parent / "Dark {tmdb-70523}"
    stamped_season = stamped_show / "Season 01"

    assert (stamped_show / "poster.jpg").read_bytes() == LOCAL_SHOW_POSTER
    assert (stamped_season / "poster.jpg").read_bytes() == LOCAL_SEASON_POSTER
    assert (stamped_season / "Dark.S01E01-thumb.jpg").read_bytes() == LOCAL_THUMB
    assert "local poster.jpg present — kept" in out
    assert "local season poster present — kept" in out
    assert "local episode still present — kept" in out

    # Controls: the artifacts that did NOT pre-exist WERE fetched.
    assert (stamped_show / "fanart.jpg").read_bytes() == _expect_img("w780", "/showbackdrop.jpg")
    assert (stamped_season / "Dark.S01E02-thumb.jpg").read_bytes() == \
        _expect_img("w300", "/s01e02still.jpg")


# --- B11/B12/B13/B14: `--nfo` (Decision 4) ---------------------------------

def _nfo_texts(root, tag):
    return [el.text for el in root.findall(tag)]


def test_nfo_nested_layout_full_element_set_with_imdb(
        sandbox, make_video, stub_tech_specs, mock_device, fake_dummy,
        patch_tmdb_by_id):
    """Decision 4 (LOCKED), NESTED layout: `tvshow.nfo` lands in the SHOW folder
    (the parent of `Season 01`, NOT the season folder) and carries the full
    richer element set, including BOTH `<imdbid>` and `<uniqueid type="imdb">`
    when `_resolve_imdb_id` succeeds. `<tvdbid>` is NEVER present (Decision 1)."""
    base_id = "tv-en-2017-dark-s01"
    show_dir, season_dir = _seed_nested_season_show(sandbox, make_video)

    TMDB_ID = 70523
    patch_tmdb_by_id(_FakeTMDBSeriesFull(
        TMDB_ID, _tv_details_full(TMDB_ID, "Dark", 2017,
                                  created_by=[{"name": "Baran bo Odar"},
                                              {"name": "Jantje Friese"}]),
        external_ids={"imdb_id": "tt5753856"},
        credits=_CREDITS,
    ))

    assert main.cmd_prep_push_rep_season_enrich(
        base_id, str(season_dir), tmdb_id=TMDB_ID, write_nfo=True,
        rename_choice="yes") is True

    stamped_show = show_dir.parent / "Dark {tmdb-70523}"
    nfo_path = stamped_show / "tvshow.nfo"
    assert nfo_path.exists(), "the NFO belongs in the SHOW folder"
    assert not (stamped_show / "Season 01" / "tvshow.nfo").exists(), \
        "the NFO must NOT be duplicated into the season folder"

    root = ET.parse(str(nfo_path)).getroot()
    assert root.tag == "tvshow"
    assert root.findtext("title") == "Dark"
    assert root.findtext("year") == "2017"
    assert root.findtext("plot") == "The show synopsis for Dark."
    assert root.findtext("rating") == "8.4"
    assert root.findtext("tmdbid") == str(TMDB_ID)
    assert root.findtext("imdbid") == "tt5753856"
    uids = {el.get("type"): (el.text, el.get("default")) for el in root.findall("uniqueid")}
    assert uids["tmdb"] == (str(TMDB_ID), "true")
    assert uids["imdb"] == ("tt5753856", None)
    assert _nfo_texts(root, "genre") == ["Drama", "Crime"]
    assert root.findtext("runtime") == "58"          # episode_run_time[0]
    assert root.findtext("premiered") == "2017-03-04"
    assert _nfo_texts(root, "studio") == ["BBC One"]  # _tmdb_network_names
    assert _nfo_texts(root, "director") == ["Baran bo Odar", "Jantje Friese"]
    assert [a.findtext("name") for a in root.findall("actor")] == \
        ["Lead Actor", "Second Actor"]
    assert root.find("tvdbid") is None, "Decision 1: <tvdbid> is NEVER emitted"


def test_nfo_flat_layout_omits_imdb_elements_when_the_lookup_fails(
        sandbox, make_video, stub_tech_specs, mock_device, fake_dummy,
        patch_tmdb_by_id):
    """Decision 4, FLAT layout + a FAILED imdb lookup: the NFO lands in the one
    folder (which is both show and season folder), and `<imdbid>` /
    `<uniqueid type="imdb">` are OMITTED ENTIRELY rather than written empty.
    `<tvdbid>` is still never present."""
    base_id = "tv-en-2022-peakyblinders-s06"
    season_dir = _seed_flat_layout_season(
        sandbox, make_video, filenames=["Peaky.Blinders.S06E01.mkv"])

    TMDB_ID = 60574
    patch_tmdb_by_id(_FakeTMDBSeriesFull(
        TMDB_ID, _tv_details_full(TMDB_ID, "Peaky Blinders", 2022),
        external_ids={},          # _resolve_imdb_id -> None
        credits=_CREDITS,
    ))

    assert main.cmd_prep_push_rep_season_enrich(
        base_id, str(season_dir), tmdb_id=TMDB_ID, write_nfo=True,
        rename_choice="yes") is True

    stamped = season_dir.parent / f"Peaky.Blinders.S06.2022 {{tmdb-{TMDB_ID}}}"
    root = ET.parse(str(stamped / "tvshow.nfo")).getroot()

    assert root.find("imdbid") is None, "a failed lookup must OMIT the element"
    uids = {el.get("type") for el in root.findall("uniqueid")}
    assert uids == {"tmdb"}, "no empty <uniqueid type='imdb'> may be written"
    assert root.findtext("tmdbid") == str(TMDB_ID)
    assert root.findtext("title") == "Peaky Blinders"
    assert _nfo_texts(root, "studio") == ["BBC One"]
    assert _nfo_texts(root, "director") == ["A Creator"]
    assert root.find("tvdbid") is None, "Decision 1: <tvdbid> is NEVER emitted"


def test_nfo_is_off_by_default(
        sandbox, make_video, stub_tech_specs, mock_device, fake_dummy,
        patch_tmdb_by_id):
    """Decision 4: `--nfo` is OPT-IN. The same happy path WITHOUT `write_nfo`
    must leave NO `.nfo` file of any name anywhere under the show folder."""
    base_id = "tv-en-2017-dark-s01"
    show_dir, season_dir = _seed_nested_season_show(sandbox, make_video)
    TMDB_ID = 70523
    patch_tmdb_by_id(_FakeTMDBSeriesFull(
        TMDB_ID, _tv_details_full(TMDB_ID, "Dark", 2017),
        external_ids={"imdb_id": "tt5753856"}, credits=_CREDITS))

    assert main.cmd_prep_push_rep_season_enrich(
        base_id, str(season_dir), tmdb_id=TMDB_ID, rename_choice="yes") is True

    stamped_show = show_dir.parent / "Dark {tmdb-70523}"
    assert (stamped_show / "poster.jpg").exists(), "the rest of the enrich still ran"
    assert list(stamped_show.rglob("*.nfo")) == []


def test_nfo_is_regenerated_not_kept(
        sandbox, make_video, stub_tech_specs, mock_device, fake_dummy,
        patch_tmdb_by_id):
    """DEVIATION D-B, pinned explicitly: unlike the artwork rows, a pre-existing
    `tvshow.nfo` is OVERWRITTEN. That is `_write_nfo`'s own documented,
    pre-existing contract ("NFOs are regenerable metadata"), shared verbatim
    with `enrich_metadata --nfo`; IMP-D22 did not introduce or change it. This
    test exists so the difference from LOCAL-ALWAYS-WINS is deliberate and
    visible rather than an untested assumption."""
    base_id = "tv-en-2017-dark-s01"
    show_dir, season_dir = _seed_nested_season_show(sandbox, make_video)
    (show_dir / "tvshow.nfo").write_bytes(b"<tvshow><title>STALE</title></tvshow>")

    TMDB_ID = 70523
    patch_tmdb_by_id(_FakeTMDBSeriesFull(
        TMDB_ID, _tv_details_full(TMDB_ID, "Dark", 2017),
        external_ids={"imdb_id": "tt5753856"}, credits=_CREDITS))

    assert main.cmd_prep_push_rep_season_enrich(
        base_id, str(season_dir), tmdb_id=TMDB_ID, write_nfo=True,
        rename_choice="yes") is True

    nfo = (show_dir.parent / "Dark {tmdb-70523}" / "tvshow.nfo").read_text(encoding="utf-8")
    assert "STALE" not in nfo
    assert "<title>Dark</title>" in nfo and f"<tmdbid>{TMDB_ID}</tmdbid>" in nfo


# --- B15: the D3 non-interactive default can never reach input() -----------

def test_default_rename_choice_is_non_interactive_and_does_not_rename(
        sandbox, make_video, stub_tech_specs, mock_device, fake_dummy,
        patch_tmdb_by_id, monkeypatch, capsys):
    """Decision 3, the smoke-hang guard: with NO `--yes`/`--no-rename` and no
    TTY (every pytest/cron/script run), the gate must default to NOT renaming
    and must never reach `input()`. The metadata + artwork writes still happen —
    only the folder stamp is skipped."""
    base_id = "tv-en-2022-peakyblinders-s06"
    season_dir = _seed_flat_layout_season(
        sandbox, make_video, filenames=["Peaky.Blinders.S06E01.mkv"])

    def _no_input(*a, **kw):
        raise AssertionError("input() must be unreachable without an interactive TTY")

    monkeypatch.setattr("builtins.input", _no_input)

    TMDB_ID = 60574
    patch_tmdb_by_id(_FakeTMDBSeriesFull(TMDB_ID, _tv_details_full(TMDB_ID, "Peaky Blinders", 2022)))

    # rename_choice deliberately left at its "ask" default.
    assert main.cmd_prep_push_rep_season_enrich(
        base_id, str(season_dir), tmdb_id=TMDB_ID) is True

    out = capsys.readouterr().out
    assert "non-interactive session — defaulting to NOT renaming" in out
    assert season_dir.is_dir(), "the folder must NOT have been renamed"
    assert not (season_dir.parent / f"Peaky.Blinders.S06.2022 {{tmdb-{TMDB_ID}}}").exists()
    assert (season_dir / "poster.jpg").exists(), "artwork still lands in the un-stamped folder"
    lib = mvcommon.load_library()
    assert lib[f"{base_id}e01"]["metadata"]["tmdb_id"] == TMDB_ID


# ===========================================================================
#   GROUP C — the two library categories the IMP-D22 suite never exercised:
#   ANIME (`ani-`) and OTHERS/sports (`oth-`).
#
#   Both are driven END-TO-END through `cmd_prep_push_rep_season_enrich` with
#   the SAME hermetic fixtures Groups A/B use (sandbox / make_video /
#   stub_tech_specs / mock_device / fake_dummy + a canned TMDB), so no real
#   C:\Media path and no real library_*.json is ever reachable.
#
#   ANIME uses the two REAL id shapes from this user's library, which behave
#   DIFFERENTLY and are therefore both covered:
#     (i)  season-suffixed base -> the episode is GLUED onto the season tag
#          (`ani-ja-2015-kurokosbasketball-s03` + "24" -> `…-s0324`)
#     (ii) suffix-less base     -> the episode is glued onto the slug
#          (`ani-ja-2013-attackontitan` + "01" -> `…attackontitan01`)
#   `cmd_prep_season`'s anime branch builds BOTH as `f"{base_id}{ep_num}"`
#   (main.py:4193) — there is no `e` separator, unlike TV/Others.
#
#   OTHERS is the deliberate IMP-D18 EXCLUSION: `_gather_enrich_units` skips
#   every `oth-` id (main.py:~2013 — sports is not on TMDB), so the archive
#   succeeds and the enrich leg warns-and-skips.
# ===========================================================================

def _tv_search_hit(tmdb_id, name, year, popularity=90.0):
    """A `/search/tv` RESULT row (not a details payload).

    `_resolve_unit` reads the matched row directly — id / name /
    first_air_date / poster_path / backdrop_path / overview — and
    `_download_unit_images` takes poster_path + backdrop_path from that same
    row, so a search-resolved run never needs `/tv/{id}` at all."""
    return {
        "id": tmdb_id, "name": name, "first_air_date": f"{year}-04-06",
        "popularity": popularity,
        "poster_path": "/showposter.jpg", "backdrop_path": "/showbackdrop.jpg",
        "overview": f"The show synopsis for {name}.",
    }


class _NoTMDBTraffic:
    """A `requests.get` stand-in that FAILS the test if it is ever called.

    Used by the Others case: an `oth-` run must make ZERO TMDB requests — the
    unit gather returns nothing, so the enrich leg bails before any resolve.
    Recording-and-asserting would also work, but hard-failing pinpoints the
    exact call site in the traceback."""

    def __init__(self):
        self.calls = []

    def get(self, url, params=None, headers=None, timeout=None, **kwargs):
        self.calls.append(url)
        raise AssertionError(
            f"an oth- (Others/sports) run must never reach TMDB — got GET {url}")


# --- C1: ANIME, season-suffixed base id (`…-s03` + "24" -> `…-s0324`) -------

def test_anime_glued_season_episode_ids_archive_and_enrich_via_tv_endpoints(
        sandbox, make_video, stub_tech_specs, mock_device, fake_dummy,
        patch_tmdb_by_id, capsys):
    """An `ani-` season archives and enriches through the TMDB **TV** endpoints
    exactly like a series: `_gather_enrich_units` buckets it as a `show`, the
    preset id resolves via `/tv/{id}` (never `/movie/…`, never `/search/…`),
    the `{tmdb-…}` token stamps the PARENT show folder, and show + per-season
    artwork lands.

    ALSO PINS A REAL LIMITATION (pre-existing, shared with `enrich_metadata` —
    NOT introduced by IMP-D22). `_episode_se_of` (main.py:1725) recovers an
    anime episode number from the id's BARE TRAILING DIGITS, which for a
    season-suffixed base swallows the season digits too:
        `ani-ja-2015-kurokosbasketball-s0324` -> (season 3, episode **324**)
    (`mvcommon.episode_num_from_id`, which strips the base_id prefix first,
    correctly reports 24). Consequence, asserted below: the per-episode still
    is requested for episode 324, the canned still seeded at the CORRECT
    episode 24 is never fetched, no `-thumb.jpg` is written, and
    `_apply_episode_overviews` finds no episode 324 so no per-episode overview
    / episode_title is written. The run still completes and the show-level
    enrichment is intact — this test pins the ACTUAL behaviour so a future fix
    to `_ANIME_EP_TAIL_RE` is a deliberate, visible change."""
    base_id = "ani-ja-2015-kurokosbasketball-s03"
    show_dir = sandbox["local_root"] / "Anime" / "Kurokos Basketball"
    season_dir = show_dir / "Season 03"
    filenames = ["[SubGrp] Kurokos Basketball - 24 [1080p].mkv",
                 "[SubGrp] Kurokos Basketball - 25 [1080p].mkv"]
    _seed_episodes(season_dir, make_video, filenames)

    TMDB_ID = 45999
    fake = patch_tmdb_by_id(_FakeTMDBSeriesFull(
        TMDB_ID,
        _tv_details_full(TMDB_ID, "Kurokos Basketball", 2015),
        season_posters={3: [{"file_path": "/aniseason3.jpg"}]},
        # Seeded at the CORRECT episode numbers on purpose — the assertions
        # below prove the code asks for 324/325 instead and never reaches these.
        episode_stills={(3, 24): [{"file_path": "/anistill24.jpg"}],
                        (3, 25): [{"file_path": "/anistill25.jpg"}]},
        season_episodes={3: [{"episode_number": 24, "name": "Ep 24", "overview": "Ep 24 synopsis."},
                             {"episode_number": 25, "name": "Ep 25", "overview": "Ep 25 synopsis."}]},
    ))

    assert main.cmd_prep_push_rep_season_enrich(
        base_id, str(season_dir), tmdb_id=TMDB_ID, rename_choice="yes") is True

    out = capsys.readouterr().out
    assert "AUTO-PILOT COMPLETE (archive + enrich)" in out
    assert "this is the SHOW folder" in out, "nested anime layout -> the note applies"

    lib = mvcommon.load_library()
    ep24, ep25 = f"{base_id}24", f"{base_id}25"

    # --- the anime id shape cmd_prep_season actually produced (glued, no "e") --
    assert sorted(lib[base_id]["children"]) == [ep24, ep25]
    assert lib[base_id]["type"] == "season_map"

    # --- archive leg: both episodes fully archived + dummied + on the device ---
    stamped_show = show_dir.parent / f"Kurokos Basketball {{tmdb-{TMDB_ID}}}"
    stamped_season = stamped_show / "Season 03"
    assert stamped_show.is_dir() and not show_dir.exists()
    assert stamped_season.is_dir(), "the season subfolder moves WITH the show folder"
    for eid, fn in zip((ep24, ep25), filenames):
        entry = lib[eid]
        assert entry["status"] == "archived" and entry["uploaded"] is True
        assert entry["folder_path"] == str(stamped_season)
        assert (stamped_season / fn).read_bytes() == FAKE_DUMMY_BYTES
    assert len(_device_names(mock_device)) == 2, sorted(_device_names(mock_device))

    # --- enrich leg: TV endpoints only, resolved strictly by id ----------------
    assert main._gather_enrich_units(lib, id_or_prefix=base_id)[0]["kind"] == "show", \
        "an ani- season must bucket as a SHOW so the TV endpoints are used"
    assert any(u.endswith(f"/tv/{TMDB_ID}") for u in fake.urls), fake.urls
    assert not any("/movie/" in u for u in fake.urls), fake.urls
    assert not any("/search/" in u for u in fake.urls), "a preset id must never search"

    # tmdb_id on BOTH leaves AND the season_map (every id in the unit).
    assert lib[base_id]["metadata"]["tmdb_id"] == TMDB_ID
    assert lib[ep24]["metadata"]["tmdb_id"] == TMDB_ID
    assert lib[ep25]["metadata"]["tmdb_id"] == TMDB_ID
    assert lib[ep24]["metadata"]["title"] == "Kurokos Basketball"
    assert lib[ep24]["metadata"]["year"] == 2015

    # --- artwork: show poster/fanart on the stamped show folder, season poster
    #     on the season folder (proves /tv/{id}/season/3/images was used) -------
    assert (stamped_show / "poster.jpg").read_bytes() == _expect_img("w342", "/showposter.jpg")
    assert (stamped_show / "fanart.jpg").read_bytes() == _expect_img("w780", "/showbackdrop.jpg")
    assert (stamped_season / "poster.jpg").read_bytes() == _expect_img("w342", "/aniseason3.jpg")

    # --- THE PINNED LIMITATION: the trailing-digit episode parse -------------
    assert main._episode_se_of(ep24, lib[ep24]) == (3, 324), \
        "pre-existing: the season digits are absorbed into the episode number"
    assert mvcommon.episode_num_from_id(ep24, base_id) == 24.0, \
        "…while the base-relative parser gets it right — the two disagree"
    assert any(u.endswith(f"/tv/{TMDB_ID}/season/3/episode/324/images") for u in fake.urls), \
        f"the still is requested for the WRONG episode number: {fake.urls}"
    assert not any(u.endswith(f"/tv/{TMDB_ID}/season/3/episode/24/images") for u in fake.urls), \
        "the correct episode number is never requested"
    assert "no episode still for S03E324" in out
    for fn in filenames:
        thumb = os.path.splitext(fn)[0] + "-thumb.jpg"
        assert not (stamped_season / thumb).exists(), \
            "no per-episode still lands for a season-suffixed anime id"
    for eid in (ep24, ep25):
        assert "episode_title" not in lib[eid]["metadata"], \
            "no per-episode overview/title is backfilled for this id shape"
        assert lib[eid]["metadata"]["overview"] == \
            _tv_details_full(TMDB_ID, "Kurokos Basketball", 2015)["overview"], \
            "the leaves keep the SHOW overview (the episode one is never found)"


# --- C2: ANIME, suffix-less base id (`…attackontitan` + "01") --------------

def test_anime_without_a_season_suffix_resolves_through_search_tv(
        sandbox, make_video, stub_tech_specs, mock_device, fake_dummy,
        patch_tmdb_by_id, capsys):
    """The OTHER real anime id shape — a base id with NO `-sNN` tag, episodes
    glued straight onto the slug (`ani-ja-2013-attackontitan01`) — archives and
    enriches with NO preset id, proving the SEARCH waterfall routes anime to
    `/search/tv` (a series), never `/search/movie`.

    Also pins the second half of the `_episode_se_of` story: with no `-sNN` on
    the parent, `_season_number_of` yields None, so the helper returns None and
    BOTH per-season and per-episode artwork are skipped entirely (no `/season/`
    and no `/episode/` request is ever made). Show-level poster/fanart still
    land, and the flat layout means the token stamps THIS folder with the
    parent-show note suppressed."""
    base_id = "ani-ja-2013-attackontitan"
    season_dir = sandbox["local_root"] / "Anime" / "Attack on Titan"
    filenames = ["[SubGrp] Attack on Titan - 01 [1080p].mkv",
                 "[SubGrp] Attack on Titan - 02 [1080p].mkv"]
    _seed_episodes(season_dir, make_video, filenames)

    TMDB_ID = 1429
    # The humanized query for this id is the bare slug "attackontitan"
    # (_humanize_id_title strips ani/ja/2013); TMDB normalisation drops spaces,
    # so "Attack on Titan" scores a 1.0 title match, and the id year (2013)
    # corroborates -> CONFIDENT.
    fake = patch_tmdb_by_id(_FakeTMDBSeriesFull(
        TMDB_ID,
        _tv_details_full(TMDB_ID, "Attack on Titan", 2013),
        search={"attackontitan": [_tv_search_hit(TMDB_ID, "Attack on Titan", 2013)]},
    ))

    assert main.cmd_prep_push_rep_season_enrich(
        base_id, str(season_dir), rename_choice="yes") is True

    out = capsys.readouterr().out
    assert "AUTO-PILOT COMPLETE (archive + enrich)" in out
    assert "this is the SHOW folder" not in out, \
        "flat layout: the show folder IS the season folder -> note suppressed"

    lib = mvcommon.load_library()
    ep1, ep2 = f"{base_id}01", f"{base_id}02"
    assert sorted(lib[base_id]["children"]) == [ep1, ep2]

    # --- resolved via the TV SEARCH endpoint, never the movie one -------------
    assert any("/search/tv" in u for u in fake.urls), fake.urls
    assert not any("/search/movie" in u for u in fake.urls), fake.urls
    assert not any("/season/" in u for u in fake.urls), \
        "a suffix-less anime base has no season number -> no season/episode calls"
    assert not any("/episode/" in u for u in fake.urls), fake.urls

    # --- archive + stamp on THIS folder (flat layout) -------------------------
    stamped = season_dir.parent / f"Attack on Titan {{tmdb-{TMDB_ID}}}"
    assert stamped.is_dir() and not season_dir.exists()
    for eid, fn in zip((ep1, ep2), filenames):
        assert lib[eid]["status"] == "archived" and lib[eid]["uploaded"] is True
        assert lib[eid]["folder_path"] == str(stamped)
        assert (stamped / fn).read_bytes() == FAKE_DUMMY_BYTES
        assert lib[eid]["metadata"]["tmdb_id"] == TMDB_ID
    assert lib[base_id]["metadata"]["tmdb_id"] == TMDB_ID
    assert len(_device_names(mock_device)) == 2

    # --- show artwork only; no episode thumbs (the pinned limitation) ---------
    assert (stamped / "poster.jpg").read_bytes() == _expect_img("w342", "/showposter.jpg")
    assert (stamped / "fanart.jpg").read_bytes() == _expect_img("w780", "/showbackdrop.jpg")
    assert main._episode_se_of(ep1, lib[ep1]) is None, \
        "no -sNN on the parent -> no (season, episode) pair at all"
    for fn in filenames:
        assert not (stamped / (os.path.splitext(fn)[0] + "-thumb.jpg")).exists()


# --- C3: OTHERS (`oth-`) — archives, but enrich is DELIBERATELY skipped -----

def test_others_season_archives_but_enrich_is_skipped(
        sandbox, make_video, stub_tech_specs, mock_device, fake_dummy,
        patch_tmdb_by_id, capsys):
    """IMP-D18's deliberate exclusion, pinned end-to-end on the new command.

    Sports/Others is not on TMDB, so `_gather_enrich_units` skips every `oth-`
    id (main.py:~2013). Through `cmd_prep_push_rep_season_enrich` that means:
    the ARCHIVE leg succeeds normally (every half prepped, pushed, replaced by
    a dummy, and reported complete), and the ENRICH leg warns
    "no enrich unit found for … — skipped" and does nothing — no TMDB request
    at all, no `metadata.tmdb_id`, no `{tmdb-…}` folder rename, no artwork.
    The command still returns True and prints the completion banner, because
    Decision 7 says a skipped enrich never fails a completed archive."""
    import json  # local (this file is append-only; matches the in-test
                 # `import hashlib` precedent in GROUP A above)

    base_id = "oth-football-2026-fifaworldcup-s01"
    season_dir = sandbox["local_root"] / "Sports" / "Football" / "FIFA World Cup 2026"
    filenames = [
        "2026-06-22 - Spain vs Saudi Arabia - First Half - Group Stage [2160p UHD].mkv",
        "2026-06-22 - Spain vs Saudi Arabia - Second Half - Group Stage [2160p UHD].mkv",
    ]
    _seed_episodes(season_dir, make_video, filenames)

    # A TMDB key IS configured (so the enrich leg gets past its api-key guard and
    # actually reaches the unit gather), but ANY request hard-fails the test.
    fake = patch_tmdb_by_id(_NoTMDBTraffic())

    assert main.cmd_prep_push_rep_season_enrich(
        base_id, str(season_dir), rename_choice="yes") is True

    out = capsys.readouterr().out
    ep1, ep2 = f"{base_id}e01", f"{base_id}e02"

    # --- the enrich leg was SKIPPED, and said so ------------------------------
    assert f"no enrich unit found for {base_id}" in out
    assert "AUTO-PILOT COMPLETE (archive + enrich)" in out, \
        "a skipped enrich must NOT stop the command reporting archive success"
    assert fake.calls == [], "no TMDB request may be made for an oth- run"

    lib = mvcommon.load_library()
    assert main._gather_enrich_units(lib, id_or_prefix=base_id) == [], \
        "IMP-D18: oth- ids are excluded from enrich units at the source"

    # --- the ARCHIVE leg nonetheless completed in full ------------------------
    assert lib[base_id]["type"] == "season_map"
    assert sorted(lib[base_id]["children"]) == [ep1, ep2], \
        "Others episodes are numbered by sorted-filename position (e01, e02)"
    for eid, fn in zip((ep1, ep2), filenames):
        entry = lib[eid]
        assert entry["status"] == "archived" and entry["uploaded"] is True
        assert entry["folder_path"] == str(season_dir)
        assert (season_dir / fn).read_bytes() == FAKE_DUMMY_BYTES
    assert len(_device_names(mock_device)) == 2, sorted(_device_names(mock_device))

    # --- NOTHING was enriched: no tmdb_id, no rename, no artwork --------------
    for eid in (base_id, ep1, ep2):
        assert "tmdb_id" not in (lib[eid].get("metadata") or {}), \
            f"{eid} must never receive a TMDB id"
    assert season_dir.is_dir(), "the real Sports folder must NOT be renamed"
    assert [p.name for p in season_dir.parent.iterdir()] == ["FIFA World Cup 2026"], \
        "no {tmdb-…}-stamped sibling folder may appear"
    assert not (season_dir / "poster.jpg").exists()
    assert not (season_dir / "fanart.jpg").exists()

    # --- and the entries routed to library_others.json only ------------------
    others = json.loads(sandbox["lib_others"].read_text(encoding="utf-8"))
    assert set(others) == {base_id, ep1, ep2}
    for lib_path in ("lib_movies", "lib_series", "lib_anime"):
        assert json.loads(sandbox[lib_path].read_text(encoding="utf-8")) == {}
