"""Focused tests for the IMP-D18 Others/Sports category (oth- prefix).

Three test areas:
  1. routing  — oth- save/load routes ONLY to library_others.json; category_of_id and
                profile_for_id return the expected values.
  2. prep numbering — cmd_prep_season assigns position-number episode ids (e01..e06)
                      in sorted-filename order for the six FIFA World Cup 2026 halves.
  3. enrich-skip  — _gather_enrich_units returns ZERO units whose key/ids contain an
                    oth- id while mov-/tv- units ARE present.

All tests use the `sandbox` fixture (dual-patches BOTH mvcommon.LIBRARY_* AND
main.LIBRARY_* plus LOCAL_ROOT) and never touch real C:\\Media or library_*.json.
"""
import json
import os

import pytest

import main
import mainfetch
import mvcommon

# ---------------------------------------------------------------------------
# Constants shared across tests
# ---------------------------------------------------------------------------

OTH_SEASON_BASE = "oth-football-2026-fifaworldcup-s01"
OTH_EP1 = "oth-football-2026-fifaworldcup-s01e01"
OTH_EP6 = "oth-football-2026-fifaworldcup-s01e06"

# Exact 6 filenames in the sort order the filesystem returns them (alphabetical).
# Sorted they resolve to: Spain·First, Spain·Second, Uruguay·First, Uruguay·Second,
# Norway·First (comes before Spain/Uruguay by date "2026-06-26" > "2026-06-22", but
# note Norway match is 2026-06-26, while Spain/Uruguay are 2026-06-22 — but
# "2026-06-22 - N..." sorts BEFORE "2026-06-26 - N..." alphabetically so:
#  Position 1: Spain First  (2026-06-22 - Spain vs Saudi Arabia - First Half ...)
#  Position 2: Spain Second (2026-06-22 - Spain vs Saudi Arabia - Second Half ...)
#  Position 3: Uruguay First  (2026-06-22 - Uruguay  vs Cabo Verde - First Half ...)
#  Position 4: Uruguay Second (2026-06-22 - Uruguay  vs Cabo Verde - Second Half ...)
#  Position 5: Norway First   (2026-06-26 - Norway  vs France - First Half ...)
#  Position 6: Norway Second  (2026-06-26 - Norway  vs France - Second Half ...)
MATCH_FILES = [
    "2026-06-22 - Spain vs Saudi Arabia - First Half - Group Stage - Group H [2160p UHD].mkv",
    "2026-06-22 - Spain vs Saudi Arabia - Second Half - Group Stage - Group H [2160p UHD].mkv",
    "2026-06-22 - Uruguay  vs Cabo Verde - First Half - Group Stage - Group H [2160p UHD].mkv",
    "2026-06-22 - Uruguay  vs Cabo Verde - Second Half - Group Stage - Group H [2160p UHD].mkv",
    "2026-06-26 - Norway  vs France - First Half - Group Stage - Group H [2160p UHD].mkv",
    "2026-06-26 - Norway  vs France - Second Half - Group Stage - Group H [2160p UHD].mkv",
]

# Expected episode id -> filename mapping (1-based position in sorted order).
EXPECTED_EP_FILES = {
    "oth-football-2026-fifaworldcup-s01e01": MATCH_FILES[0],
    "oth-football-2026-fifaworldcup-s01e02": MATCH_FILES[1],
    "oth-football-2026-fifaworldcup-s01e03": MATCH_FILES[2],
    "oth-football-2026-fifaworldcup-s01e04": MATCH_FILES[3],
    "oth-football-2026-fifaworldcup-s01e05": MATCH_FILES[4],
    "oth-football-2026-fifaworldcup-s01e06": MATCH_FILES[5],
}


# ---------------------------------------------------------------------------
# 1. Routing: save/load and category helpers
# ---------------------------------------------------------------------------

def test_routing_save_load_and_category_helpers(sandbox):
    """oth- leaf saved via save_library lands ONLY in library_others.json.

    - The entry does NOT appear in movies/series/anime.
    - load_library() round-trips it with the same content.
    - category_of_id returns "other"; mainfetch.profile_for_id returns "others".
    """
    eid = "oth-football-2026-fifaworldcup-s01e01"
    entry = {
        "short_id": mvcommon.generate_short_id(eid),
        "filename": "Spain vs Saudi Arabia First Half.mkv",
        "folder_path": str(sandbox["local_root"] / "Sports" / "Football"),
        "status": "local_ready",
        "uploaded": False,
        "hash": "deadbeefdeadbeef",
        "metadata": {"title": "FIFA World Cup 2026", "year": 2026},
        "tech_spec": {"resolution": "2160p", "size_bytes": 300_000},
        "parent_id": "oth-football-2026-fifaworldcup-s01",
    }
    library = {eid: entry}

    # Write via the real helper (routing-by-prefix to the right lib file).
    mvcommon.save_library(library)

    # -- ONLY library_others.json should contain the oth- entry ----------------
    others_data = json.loads(sandbox["lib_others"].read_text(encoding="utf-8"))
    assert eid in others_data, (
        f"{eid!r} must be routed to library_others.json; got keys: {list(others_data)!r}"
    )

    # The other three lib files must NOT contain it.
    for lib_key, lib_path in [
        ("lib_movies", sandbox["lib_movies"]),
        ("lib_series", sandbox["lib_series"]),
        ("lib_anime",  sandbox["lib_anime"]),
    ]:
        if not lib_path.exists():
            continue
        data = json.loads(lib_path.read_text(encoding="utf-8"))
        assert eid not in data, (
            f"{eid!r} must NOT appear in {lib_key}; got keys: {list(data)!r}"
        )

    # -- load_library round-trip -----------------------------------------------
    loaded = mvcommon.load_library()
    assert eid in loaded, f"load_library() must return the oth- entry; got keys: {list(loaded)!r}"
    assert loaded[eid]["filename"] == entry["filename"]
    assert loaded[eid]["status"] == "local_ready"

    # -- category helpers -------------------------------------------------------
    assert main.category_of_id(eid) == "other", (
        f"category_of_id({eid!r}) must return 'other', got {main.category_of_id(eid)!r}"
    )
    assert mainfetch.profile_for_id(eid) == "others", (
        f"profile_for_id({eid!r}) must return 'others', got {mainfetch.profile_for_id(eid)!r}"
    )


# ---------------------------------------------------------------------------
# 2. Prep numbering: cmd_prep_season assigns e01..e06 in sorted order
# ---------------------------------------------------------------------------

def test_prep_numbering_six_halves(sandbox, stub_tech_specs, capsys):
    """cmd_prep_season assigns position-based episode numbers to 6 sports halves.

    Creates 6 real (>200 KB) files in sorted filename order, runs cmd_prep_season,
    then asserts the season_map has e01..e06 as children mapped to the right filenames.
    """
    # Create the folder under the sandbox Sports tree (mirrors real disk layout).
    sports_dir = sandbox["local_root"] / "Sports" / "Football" / "FIFA World Cup" / "FIFA World Cup 2026"
    sports_dir.mkdir(parents=True, exist_ok=True)

    # Write each match file as a real >200 KB file so cmd_prep treats it as real media.
    real_bytes = b"SPORTS-MEDIA-CONTENT\n" * 11000  # ~230 KB > 200_000
    for fname in MATCH_FILES:
        (sports_dir / fname).write_bytes(real_bytes)
        assert os.path.getsize(sports_dir / fname) > main.DUMMY_MAX_BYTES, (
            f"{fname} must exceed DUMMY_MAX_BYTES for real-media path"
        )

    # All four lib files must exist so load_library doesn't skip any.
    for lib_path in (sandbox["lib_movies"], sandbox["lib_series"],
                     sandbox["lib_anime"], sandbox["lib_others"]):
        if not lib_path.exists():
            lib_path.write_text("{}", encoding="utf-8")

    # Run the real cmd_prep_season.
    result_ok = main.cmd_prep_season(OTH_SEASON_BASE, str(sports_dir))
    capsys.readouterr()  # consume stdout

    lib = mvcommon.load_library()

    # -- season_map parent exists and has correct shape -------------------------
    assert OTH_SEASON_BASE in lib, (
        f"season_map parent {OTH_SEASON_BASE!r} must be created in library"
    )
    parent = lib[OTH_SEASON_BASE]
    assert parent.get("type") == "season_map", (
        f"parent entry must be type='season_map', got: {parent.get('type')!r}"
    )
    children = parent.get("children", [])
    assert len(children) == 6, (
        f"Expected 6 children (e01..e06), got {len(children)}: {children!r}"
    )

    # Children must be sorted and match e01..e06.
    assert children == sorted(children), f"children must be sorted: {children!r}"
    expected_child_ids = sorted(EXPECTED_EP_FILES.keys())
    assert children == expected_child_ids, (
        f"children must be e01..e06; got: {children!r}"
    )

    # -- each child leaf maps to the right file --------------------------------
    for ep_id, expected_filename in EXPECTED_EP_FILES.items():
        assert ep_id in lib, f"Episode {ep_id!r} must exist in library"
        leaf = lib[ep_id]
        assert leaf.get("filename") == expected_filename, (
            f"{ep_id!r}: expected filename {expected_filename!r}, "
            f"got {leaf.get('filename')!r}"
        )
        assert leaf.get("parent_id") == OTH_SEASON_BASE, (
            f"{ep_id!r}: expected parent_id={OTH_SEASON_BASE!r}, "
            f"got {leaf.get('parent_id')!r}"
        )


# ---------------------------------------------------------------------------
# 3. Enrich-skip: _gather_enrich_units excludes all oth- entries
# ---------------------------------------------------------------------------

def test_enrich_skip_excludes_oth_entries(sandbox):
    """_gather_enrich_units must return ZERO units with oth- key/ids.

    Seeds a mixed library with mov-/tv-/oth- entries; asserts that the enrich
    unit list contains mov-/tv- units but no oth- unit at all.
    """
    # Mixed library: one movie leaf, one tv season_map + leaf, one oth- season_map + leaf.
    library = {
        # Movie leaf
        "mov-en-2024-smoke": {
            "short_id": "abc12345",
            "filename": "Smoke.2024.mkv",
            "folder_path": str(sandbox["local_root"] / "Movies" / "Smoke"),
            "status": "local_ready",
            "uploaded": False,
            "hash": "moviesthash",
            "metadata": {"title": "Smoke", "year": 2024},
            "tech_spec": {"resolution": "1080p", "size_bytes": 300_000},
        },
        # TV season_map + leaf
        "tv-en-2020-demo-s01": {
            "type": "season_map",
            "folder_path": str(sandbox["local_root"] / "Series" / "Demo" / "Season 01"),
            "total_episodes": 1,
            "children": ["tv-en-2020-demo-s01e01"],
        },
        "tv-en-2020-demo-s01e01": {
            "short_id": "def56789",
            "filename": "Demo.S01E01.mkv",
            "folder_path": str(sandbox["local_root"] / "Series" / "Demo" / "Season 01"),
            "status": "local_ready",
            "uploaded": False,
            "hash": "tvhash",
            "metadata": {"title": "Demo", "year": 2020},
            "tech_spec": {"resolution": "1080p", "size_bytes": 300_000},
            "parent_id": "tv-en-2020-demo-s01",
        },
        # Others season_map + leaf (must NOT be enriched)
        "oth-football-2026-fifaworldcup-s01": {
            "type": "season_map",
            "folder_path": str(sandbox["local_root"] / "Sports" / "Football"),
            "total_episodes": 1,
            "children": ["oth-football-2026-fifaworldcup-s01e01"],
        },
        "oth-football-2026-fifaworldcup-s01e01": {
            "short_id": "oth00001",
            "filename": "Spain First Half.mkv",
            "folder_path": str(sandbox["local_root"] / "Sports" / "Football"),
            "status": "local_ready",
            "uploaded": False,
            "hash": "othhash",
            "metadata": {"title": "FIFA World Cup 2026", "year": 2026},
            "tech_spec": {"resolution": "2160p", "size_bytes": 300_000},
            "parent_id": "oth-football-2026-fifaworldcup-s01",
        },
    }

    mvcommon.save_library(library)

    lib = mvcommon.load_library()
    units = main._gather_enrich_units(lib)

    # -- No oth- unit must appear in the result --------------------------------
    oth_units = [u for u in units if "oth" in u["key"] or
                 any("oth" in uid for uid in u.get("ids", []))]
    assert len(oth_units) == 0, (
        f"_gather_enrich_units must skip ALL oth- entries; "
        f"found unexpected oth- units: {oth_units!r}"
    )

    # -- The mov- and tv- units ARE present ------------------------------------
    unit_keys = {u["key"] for u in units}
    assert "mov-en-2024-smoke" in unit_keys, (
        f"mov- unit must be present; got keys: {unit_keys!r}"
    )
    # TV show units use the show id (strip trailing -sNN from the season id).
    # "tv-en-2020-demo-s01" -> show key "tv-en-2020-demo".
    tv_units = [u for u in units if u["key"].startswith("tv-")]
    assert len(tv_units) >= 1, (
        f"tv- unit must be present; got keys: {unit_keys!r}"
    )
