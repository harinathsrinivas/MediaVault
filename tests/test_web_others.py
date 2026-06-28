"""Step 8 endpoint guard for IMP-D18 Others category.

Confirms that an oth- leaf seeded in library_others.json is counted under
by_category["other"] in GET /api/items, proving the wiring from load_library
-> items_payload -> the HTTP endpoint is complete end-to-end.

Keeps exactly ONE focused test — wiring confirmation, not a feature build.
All I/O is confined to the sandbox fixture (never real C:\\Media).
"""
import json

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient  # noqa: E402

import main  # noqa: E402


@pytest.mark.usefixtures("web_as_local_admin")
def test_oth_leaf_counted_under_other_in_api_items(sandbox, make_video):
    """An oth- season_map + real leaf written to library_others.json must appear
    in GET /api/items with category 'other' and be counted in by_category['other'].

    Mirrors the seeding pattern in test_web_items.py: write JSON files directly
    into the sandbox paths; the sandbox fixture has already patched
    mvcommon.LIBRARY_OTHERS to lib_others so load_library() picks them up.
    """
    from webui.server import create_app

    root = sandbox["local_root"]

    # Create the physical file under LOCAL_ROOT/Sports/... (never C:\Media)
    ep_dir = root / "Sports" / "FIFA World Cup 2026" / "Season 01"
    ep_dir.mkdir(parents=True, exist_ok=True)
    ep_filename = "FIFAWorldCup2026.S01E01.mkv"
    ep_path, _ = make_video(ep_dir / ep_filename, marker=b"W")

    season_id = "oth-football-2026-fifaworldcup-s01"
    leaf_id = "oth-football-2026-fifaworldcup-s01e01"

    others = {
        season_id: {
            "type": "season_map",
            "folder_path": str(ep_dir),
            "total_episodes": 1,
            "children": [leaf_id],
        },
        leaf_id: {
            "status": "local_ready",
            "uploaded": False,
            "folder_path": str(ep_dir),
            "filename": ep_filename,
            "parent_id": season_id,
        },
    }

    # Write all four library files so load_library() sees a clean, consistent state.
    sandbox["lib_movies"].write_text("{}", encoding="utf-8")
    sandbox["lib_series"].write_text("{}", encoding="utf-8")
    sandbox["lib_anime"].write_text("{}", encoding="utf-8")
    sandbox["lib_others"].write_text(json.dumps(others), encoding="utf-8")

    client = TestClient(create_app())
    r = client.get("/api/items")
    assert r.status_code == 200, f"/api/items -> {r.status_code}: {r.text}"

    data = r.json()

    # The season_map must NOT appear as an item row (virtual, skipped).
    ids = {it["id"] for it in data["items"]}
    assert season_id not in ids, "season_map must NOT appear as a row in /api/items"

    # The real leaf must appear with category 'other'.
    assert leaf_id in ids, (
        f"oth- leaf {leaf_id!r} must appear in /api/items; got ids={ids}"
    )
    leaf_row = next(it for it in data["items"] if it["id"] == leaf_id)
    assert leaf_row["category"] == "other", (
        f"oth- leaf must have category 'other', got {leaf_row['category']!r}"
    )

    # by_category must count the leaf under 'other'.
    assert data["by_category"]["other"] == 1, (
        f"by_category['other'] expected 1, got {data['by_category']['other']}"
    )
    # The other buckets must be zero (nothing else was seeded).
    assert data["by_category"]["movies"] == 0
    assert data["by_category"]["series"] == 0
    assert data["by_category"]["anime"] == 0
