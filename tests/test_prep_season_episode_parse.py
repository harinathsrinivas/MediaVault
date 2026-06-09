"""
Unit tests for cmd_prep_season episode-ID derivation.

Constraints:
Never touch real C:\\Media files or real library_*.json.
Run `pytest -q` and fix failures before marking this step done.
"""

import main
import mvcommon


# ---------------------------------------------------------------------------
# Test A — dotted-title SxxExx (the bug fix)
# Filename: Fringe.S03E20.6.02.AM.EST...
# Before fix: regex r"[sS]\d+[eE](\d+(?:\.\d+)?)" captured "20.6" -> wrong key
# After fix:  regex r"[sS]\d+[eE](\d+)"           captures "20"   -> correct key
# ---------------------------------------------------------------------------

def test_dotted_title_sxxexx_key(sandbox, stub_tech_specs, tmp_path):
    """Test A: Fringe.S03E20.6.02... yields e20 (not e20.6 from old regex)."""
    fringe_folder = tmp_path / "series" / "fringe_s03"
    fringe_folder.mkdir(parents=True)

    mkv = fringe_folder / "Fringe.S03E20.6.02.AM.EST.2011.1080p.BluRay.REMUX.AVC.DTS-HD.MA.5.1-NOGRP.mkv"
    mkv.write_bytes(b"x" * 210_000)  # must exceed DUMMY_MAX_BYTES (200_000)

    # Initialise the library files that sandbox provides
    sandbox["lib_movies"].write_text("{}", encoding="utf-8")
    sandbox["lib_series"].write_text("{}", encoding="utf-8")
    sandbox["lib_anime"].write_text("{}", encoding="utf-8")

    main.cmd_prep_season("tv-en-2010-fringe-s03", str(fringe_folder))

    lib = mvcommon.load_library()

    assert "tv-en-2010-fringe-s03e20" in lib, \
        "Expected key tv-en-2010-fringe-s03e20 to exist after prep"
    assert "tv-en-2010-fringe-s03e20.6" not in lib, \
        "Old wrong key tv-en-2010-fringe-s03e20.6 must NOT exist after fix"


# ---------------------------------------------------------------------------
# Test B — canonical SxxExx
# ---------------------------------------------------------------------------

def test_canonical_sxxexx_key(sandbox, stub_tech_specs, tmp_path):
    """Test B: Fringe.S03E19.1080p.BluRay.mkv yields e19."""
    fringe_folder = tmp_path / "series" / "fringe_s03_b"
    fringe_folder.mkdir(parents=True)

    mkv = fringe_folder / "Fringe.S03E19.1080p.BluRay.mkv"
    mkv.write_bytes(b"x" * 210_000)  # must exceed DUMMY_MAX_BYTES (200_000)

    sandbox["lib_movies"].write_text("{}", encoding="utf-8")
    sandbox["lib_series"].write_text("{}", encoding="utf-8")
    sandbox["lib_anime"].write_text("{}", encoding="utf-8")

    main.cmd_prep_season("tv-en-2010-fringe-s03", str(fringe_folder))

    lib = mvcommon.load_library()

    assert "tv-en-2010-fringe-s03e19" in lib, \
        "Expected key tv-en-2010-fringe-s03e19 to exist after prep"


# ---------------------------------------------------------------------------
# Test C — anime NxYY half-episode (NxYY line 891 preserves decimal)
# ---------------------------------------------------------------------------

def test_anime_nxyy_half_episode_key(sandbox, stub_tech_specs, tmp_path):
    """Test C: [Grp] Show 16x05.5 [hash].mkv yields ani-test-show05.5."""
    anime_folder = tmp_path / "anime" / "show"
    anime_folder.mkdir(parents=True)

    mkv = anime_folder / "[Grp] Show 16x05.5 [hash].mkv"
    mkv.write_bytes(b"x" * 210_000)  # must exceed DUMMY_MAX_BYTES (200_000)

    sandbox["lib_movies"].write_text("{}", encoding="utf-8")
    sandbox["lib_series"].write_text("{}", encoding="utf-8")
    sandbox["lib_anime"].write_text("{}", encoding="utf-8")

    main.cmd_prep_season("ani-test-show", str(anime_folder))

    lib = mvcommon.load_library()

    assert "ani-test-show05.5" in lib, \
        "Expected key ani-test-show05.5 to exist — NxYY decimal must be preserved"


# ---------------------------------------------------------------------------
# Test D — range filter includes correct ID e20 for range "20-20"
# Inline the filter logic from cmd_prep_push_rep_season (main.py:2121-2137)
# ---------------------------------------------------------------------------

def test_range_filter_includes_correct_id():
    """Test D: e20 ID is included by episode range 20-20 (ep_num == 20.0)."""
    import re

    base_id = "tv-en-2010-fringe-s03"
    mid = "tv-en-2010-fringe-s03e20"
    episode_range = "20-20"

    start, end = map(float, episode_range.split('-'))
    ep_str = mid.replace(base_id, "")
    match = re.search(r'^[eExX]?(\d+(?:\.\d+)?)$', ep_str)
    assert match is not None, "ep_str 'e20' should match the filter regex"
    ep_num = float(match.group(1))
    assert ep_num == 20.0
    assert start <= ep_num <= end, "e20 must be included in range 20-20"


# ---------------------------------------------------------------------------
# Test E — range filter excludes old wrong ID e20.6 for range "20-20"
# Documents why the old regex was broken: e20.6 yields ep_num == 20.6,
# which falls outside the 20-20 range and would silently skip the episode.
# ---------------------------------------------------------------------------

def test_range_filter_excludes_old_wrong_id():
    """Test E: e20.6 ID (old bad key) is excluded by episode range 20-20."""
    import re

    base_id = "tv-en-2010-fringe-s03"
    mid = "tv-en-2010-fringe-s03e20.6"
    episode_range = "20-20"

    start, end = map(float, episode_range.split('-'))
    ep_str = mid.replace(base_id, "")
    match = re.search(r'^[eExX]?(\d+(?:\.\d+)?)$', ep_str)
    assert match is not None, "ep_str 'e20.6' should match the filter regex"
    ep_num = float(match.group(1))
    assert ep_num == 20.6
    assert not (start <= ep_num <= end), "e20.6 must NOT be included in range 20-20"


# ---------------------------------------------------------------------------
# Test F — SxxExxExx combined file: both episode keys created in library
# ---------------------------------------------------------------------------

def test_combined_episode_both_keys_created(sandbox, stub_tech_specs, tmp_path):
    """Test F: S04E19E20 file creates both e19 and e20 keys in the library."""
    bsg_folder = tmp_path / "series" / "bsg_s04"
    bsg_folder.mkdir(parents=True)

    mkv = bsg_folder / "Battlestar.Galactica.S04E19E20.Daybreak.2009.1080p.BluRay.REMUX.VC-1.DTS-HD.MA.5.1-NOGRP.mkv"
    mkv.write_bytes(b"x" * 210_000)

    sandbox["lib_movies"].write_text("{}", encoding="utf-8")
    sandbox["lib_series"].write_text("{}", encoding="utf-8")
    sandbox["lib_anime"].write_text("{}", encoding="utf-8")

    main.cmd_prep_season("tv-en-2009-bsg-s04", str(bsg_folder))

    lib = mvcommon.load_library()

    assert "tv-en-2009-bsg-s04e19" in lib, \
        "Expected key tv-en-2009-bsg-s04e19 to exist after prep"
    assert "tv-en-2009-bsg-s04e20" in lib, \
        "Expected key tv-en-2009-bsg-s04e20 to exist after prep"
    children = lib["tv-en-2009-bsg-s04"]["children"]
    assert "tv-en-2009-bsg-s04e19" in children, \
        "e19 must be in season_map children"
    assert "tv-en-2009-bsg-s04e20" in children, \
        "e20 must be in season_map children"


# ---------------------------------------------------------------------------
# Test G — e20 is a thin alias; e19 is the real primary leaf
# ---------------------------------------------------------------------------

def test_combined_episode_alias_structure(sandbox, stub_tech_specs, tmp_path):
    """Test G: e20 entry is a thin alias; e19 is the real primary with a hash."""
    bsg_folder = tmp_path / "series" / "bsg_s04_g"
    bsg_folder.mkdir(parents=True)

    mkv = bsg_folder / "Battlestar.Galactica.S04E19E20.Daybreak.2009.1080p.BluRay.REMUX.VC-1.DTS-HD.MA.5.1-NOGRP.mkv"
    mkv.write_bytes(b"x" * 210_000)

    sandbox["lib_movies"].write_text("{}", encoding="utf-8")
    sandbox["lib_series"].write_text("{}", encoding="utf-8")
    sandbox["lib_anime"].write_text("{}", encoding="utf-8")

    main.cmd_prep_season("tv-en-2009-bsg-s04", str(bsg_folder))

    lib = mvcommon.load_library()

    assert lib["tv-en-2009-bsg-s04e20"]["type"] == "multi_ep_alias", \
        "e20 must be a multi_ep_alias"
    assert lib["tv-en-2009-bsg-s04e20"]["alias_of"] == "tv-en-2009-bsg-s04e19", \
        "e20 alias_of must point to e19"
    assert lib["tv-en-2009-bsg-s04e19"].get("type") != "multi_ep_alias", \
        "e19 (primary) must not have type multi_ep_alias"
    assert "hash" in lib["tv-en-2009-bsg-s04e19"], \
        "e19 (primary) must have a real hash"


# ---------------------------------------------------------------------------
# Test H — de-alias transform: range 18-20 over seeded library collapses e20
#           alias to e19 primary, deduplicating → [e18, e19]
# Pure unit test of the de-alias logic — no cmd_prep_season call.
# ---------------------------------------------------------------------------

def test_dealias_range_18_20_collapses_alias():
    """Test H: range 18-20 with e20 as alias → dealiased list is [e18, e19]."""
    lib = {
        "tv-en-2009-bsg-s04": {
            "type": "season_map",
            "children": [
                "tv-en-2009-bsg-s04e18",
                "tv-en-2009-bsg-s04e19",
                "tv-en-2009-bsg-s04e20",
            ],
        },
        "tv-en-2009-bsg-s04e18": {
            "filename": "Battlestar.Galactica.S04E18.1080p.BluRay.mkv",
            "hash": "aaa",
            "status": "local_ready",
            "uploaded": False,
        },
        "tv-en-2009-bsg-s04e19": {
            "filename": "Battlestar.Galactica.S04E19E20.Daybreak.2009.1080p.BluRay.mkv",
            "hash": "bbb",
            "status": "local_ready",
            "uploaded": False,
        },
        "tv-en-2009-bsg-s04e20": {
            "type": "multi_ep_alias",
            "alias_of": "tv-en-2009-bsg-s04e19",
            "parent_id": "tv-en-2009-bsg-s04",
        },
    }

    # Simulate range filter for "18-20" — all three IDs pass
    target_ids = [
        "tv-en-2009-bsg-s04e18",
        "tv-en-2009-bsg-s04e19",
        "tv-en-2009-bsg-s04e20",
    ]

    # Apply de-alias transform (same logic as cmd_prep_push_rep_season)
    seen = set()
    dealiased = []
    for mid in target_ids:
        real_id, _ = main._resolve_alias(lib, mid)
        if real_id not in seen:
            seen.add(real_id)
            dealiased.append(real_id)

    assert dealiased == ["tv-en-2009-bsg-s04e18", "tv-en-2009-bsg-s04e19"], \
        "e20 alias must collapse to e19 and be deduplicated out"


# ---------------------------------------------------------------------------
# Test I — de-alias transform: range 20-20 (alias-only) still yields the primary
# Pure unit test of the de-alias logic — no cmd_prep_season call.
# ---------------------------------------------------------------------------

def test_dealias_range_20_20_alias_only_yields_primary():
    """Test I: range 20-20 matches only the alias → dealiased list is [e19]."""
    lib = {
        "tv-en-2009-bsg-s04": {
            "type": "season_map",
            "children": [
                "tv-en-2009-bsg-s04e18",
                "tv-en-2009-bsg-s04e19",
                "tv-en-2009-bsg-s04e20",
            ],
        },
        "tv-en-2009-bsg-s04e18": {
            "filename": "Battlestar.Galactica.S04E18.1080p.BluRay.mkv",
            "hash": "aaa",
            "status": "local_ready",
            "uploaded": False,
        },
        "tv-en-2009-bsg-s04e19": {
            "filename": "Battlestar.Galactica.S04E19E20.Daybreak.2009.1080p.BluRay.mkv",
            "hash": "bbb",
            "status": "local_ready",
            "uploaded": False,
        },
        "tv-en-2009-bsg-s04e20": {
            "type": "multi_ep_alias",
            "alias_of": "tv-en-2009-bsg-s04e19",
            "parent_id": "tv-en-2009-bsg-s04",
        },
    }

    # Range filter "20-20" matches only the alias
    target_ids = ["tv-en-2009-bsg-s04e20"]

    # Apply de-alias transform
    seen = set()
    dealiased = []
    for mid in target_ids:
        real_id, _ = main._resolve_alias(lib, mid)
        if real_id not in seen:
            seen.add(real_id)
            dealiased.append(real_id)

    assert dealiased == ["tv-en-2009-bsg-s04e19"], \
        "alias-only range must resolve to the primary"


# ---------------------------------------------------------------------------
# Test J — regression guard: single-E file creates only e19, not e20
# ---------------------------------------------------------------------------

def test_single_episode_no_alias_created(sandbox, stub_tech_specs, tmp_path):
    """Test J: S04E19 single-E file creates e19 only — no e20 alias."""
    bsg_folder = tmp_path / "series" / "bsg_s04_j"
    bsg_folder.mkdir(parents=True)

    mkv = bsg_folder / "Battlestar.Galactica.S04E19.1080p.BluRay.mkv"
    mkv.write_bytes(b"x" * 210_000)

    sandbox["lib_movies"].write_text("{}", encoding="utf-8")
    sandbox["lib_series"].write_text("{}", encoding="utf-8")
    sandbox["lib_anime"].write_text("{}", encoding="utf-8")

    main.cmd_prep_season("tv-en-2009-bsg-s04", str(bsg_folder))

    lib = mvcommon.load_library()

    assert "tv-en-2009-bsg-s04e19" in lib, \
        "Expected key tv-en-2009-bsg-s04e19 to exist after prep"
    assert "tv-en-2009-bsg-s04e20" not in lib, \
        "e20 must NOT exist for a single-episode file"
    assert lib["tv-en-2009-bsg-s04e19"].get("type") != "multi_ep_alias", \
        "e19 must not be an alias for a single-episode file"


# ---------------------------------------------------------------------------
# Test K — 3-episode generalization: S04E17E18E19 → e17 primary, e18+e19 aliases
# ---------------------------------------------------------------------------

def test_three_episode_combined_file(sandbox, stub_tech_specs, tmp_path):
    """Test K: S04E17E18E19 creates e17 as primary; e18 and e19 as aliases of e17."""
    bsg_folder = tmp_path / "series" / "bsg_s04_k"
    bsg_folder.mkdir(parents=True)

    mkv = bsg_folder / "Battlestar.Galactica.S04E17E18E19.Crossroads.2007.1080p.BluRay.mkv"
    mkv.write_bytes(b"x" * 210_000)

    sandbox["lib_movies"].write_text("{}", encoding="utf-8")
    sandbox["lib_series"].write_text("{}", encoding="utf-8")
    sandbox["lib_anime"].write_text("{}", encoding="utf-8")

    main.cmd_prep_season("tv-en-2009-bsg-s04", str(bsg_folder))

    lib = mvcommon.load_library()

    assert "tv-en-2009-bsg-s04e17" in lib, \
        "Expected key tv-en-2009-bsg-s04e17 (primary) to exist"
    assert "hash" in lib["tv-en-2009-bsg-s04e17"], \
        "e17 (primary) must have a real hash"
    assert lib["tv-en-2009-bsg-s04e17"].get("type") != "multi_ep_alias", \
        "e17 must not be an alias"

    assert "tv-en-2009-bsg-s04e18" in lib, \
        "Expected key tv-en-2009-bsg-s04e18 (alias) to exist"
    assert lib["tv-en-2009-bsg-s04e18"]["type"] == "multi_ep_alias", \
        "e18 must be a multi_ep_alias"
    assert lib["tv-en-2009-bsg-s04e18"]["alias_of"] == "tv-en-2009-bsg-s04e17", \
        "e18 alias_of must point to e17"

    assert "tv-en-2009-bsg-s04e19" in lib, \
        "Expected key tv-en-2009-bsg-s04e19 (alias) to exist"
    assert lib["tv-en-2009-bsg-s04e19"]["type"] == "multi_ep_alias", \
        "e19 must be a multi_ep_alias"
    assert lib["tv-en-2009-bsg-s04e19"]["alias_of"] == "tv-en-2009-bsg-s04e17", \
        "e19 alias_of must point to e17"

    children = lib["tv-en-2009-bsg-s04"]["children"]
    assert "tv-en-2009-bsg-s04e17" in children, "e17 must be in season_map children"
    assert "tv-en-2009-bsg-s04e18" in children, "e18 must be in season_map children"
    assert "tv-en-2009-bsg-s04e19" in children, "e19 must be in season_map children"
