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

import pytest

import main
import mvcommon


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
