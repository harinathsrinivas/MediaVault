"""IMP-C25 Step 13 — `check_photos`: download-free verification, sweep and confirm (docs/feature-fetch-datetime/PLAN.md
Step 13; P-5, P-8, P-9, P-10, P-11, P-15, P-18; OD-5, OD-7, OD-9; the Step 9b rule; DECISIONS §1e/§1f).

  13.0  the locator's per_approach_pick; `python mainfetch.py …` is ONE module (gprpc's lazy import)

Hermetic: the fake Google Photos world + its batchexecute emulator (tests/gphotos_fake.py); mainfetch's and gprpc's clocks
are fake (nothing waits); the autouse fixtures of tests/conftest.py redirect every state dir, ~/Downloads, the process
table and the Chrome profiles. No network, no browser, no adb, no real C:\\Media. Synthetic ids only (every photo id
contains "FAKE").
"""
import os
import subprocess
import sys
import textwrap

import pytest

import gprpc
import mainfetch
import mvcommon
from test_fetch_harvester import _Clock
from test_locator import (EP, SHOW, SID, TAKEN, _assert_read_only_rpc, _bytes, _ctx, _folder, _learned, _locators,
                          _up, _whole)

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.fixture(autouse=True)
def clock(monkeypatch):
    """mainfetch and gprpc share one fake clock: every sleep, poll and RPC pacing gap is instant."""
    c = _Clock()
    monkeypatch.setattr(mainfetch, "time", c)
    monkeypatch.setattr(gprpc, "_monotonic", c.time)
    monkeypatch.setattr(gprpc, "_sleep", c.sleep)
    monkeypatch.setattr(gprpc, "_uniform", lambda low, high: low)
    return c


@pytest.fixture(autouse=True)
def _only_read_only_rpcids_ever(request):
    """P-9 / P13, after EVERY test of this module that drives the fake world: only allowlisted read-only rpcids."""
    world = request.getfixturevalue("fake_photos") if "fake_photos" in request.fixturenames else None
    yield
    if world is not None:
        _assert_read_only_rpc(world)


# ===========================================================================
# 13.0 — the locator's per_approach_pick; the script is one module
# ===========================================================================
def test_per_approach_pick_names_the_item_every_stage_picked(fake_photos, tmp_path):
    # PLAN Step 13 (OD-7 "approaches that disagree"): locate_object's parallel map {approach: photo_id | None}.
    data = _bytes("pick")
    item = fake_photos.add_item("tv", _up(SHOW, SID), data=data, taken=TAKEN)
    ctx = _ctx()
    [loc] = _locators(_whole(_folder(tmp_path), SHOW, data, SID, block=_learned(item, date_utc=TAKEN)), EP, ctx,
                      tmp_path)

    res = mainfetch.locate_object(fake_photos.driver("tv"), loc, ctx, approaches="all")

    assert list(res["per_approach_pick"]) == list(res["per_approach"])     # the same keys, the same order
    assert res["per_approach_pick"] == dict.fromkeys(res["per_approach"], item.photo_id)
    assert all(set(v) == {"outcome", "n"} for v in res["per_approach"].values())   # the contract stays exact


def test_per_approach_pick_shows_two_stages_picking_different_items(fake_photos, tmp_path):
    # A legacy object (no content key) mapped by name to its FIRST upload; a newer upload holds the same name with other
    # bytes. Its stored photo_id and its taken second pin the first upload; a name search sees both and picks the newest
    # set (ambiguous) — the disagreement check_photos lists as an outlier.
    old = fake_photos.add_item("tv", _up(SHOW, SID), data=_bytes("first upload"), taken=TAKEN)
    new = fake_photos.add_item("tv", _up(SHOW, SID), data=_bytes("re-upload"), taken="2023-03-04T05:06:07Z")
    library_bytes = _bytes("library")
    block = _learned(old, data=library_bytes, proof="filename", content_key=False)
    ctx = _ctx()
    [loc] = _locators(_whole(_folder(tmp_path), SHOW, library_bytes, SID, block=block), EP, ctx, tmp_path)

    res = mainfetch.locate_object(fake_photos.driver("tv"), loc, ctx, approaches="all")

    assert (res["found"], res["photo_id"], res["stage"]) == (True, old.photo_id, "s0_photo_id")
    assert res["per_approach_pick"]["photo_id"] == old.photo_id
    assert res["per_approach_pick"]["taken_second"] == old.photo_id
    assert res["per_approach"]["exact_name"]["outcome"] == "ambiguous"
    assert res["per_approach_pick"]["exact_name"] == new.photo_id             # the newest set
    assert len({p for p in res["per_approach_pick"].values() if p}) == 2


def test_a_stage_that_picks_nothing_maps_to_none(fake_photos, tmp_path):
    ctx = _ctx()
    [loc] = _locators(_whole(_folder(tmp_path), SHOW, _bytes("nowhere"), SID), EP, ctx, tmp_path)

    res = mainfetch.locate_object(fake_photos.driver("tv"), loc, ctx, approaches="all")

    assert not res["found"] and res["per_approach"]
    assert res["per_approach_pick"] == dict.fromkeys(res["per_approach"])     # every stage: None


def test_a_stand_in_driver_has_an_empty_pick_map(tmp_path):
    ctx = _ctx()
    [loc] = _locators(_whole(_folder(tmp_path), SHOW, _bytes("x"), SID), EP, ctx, tmp_path)
    assert mainfetch.locate_object(None, loc, ctx)["per_approach_pick"] == {}


def test_run_as_a_script_mainfetch_is_one_module_for_gprpc():
    # `python mainfetch.py check|fetch|locate …` runs this file as __main__. gprpc imports `mainfetch` lazily (its
    # sign-out check): without the alias that import loaded a SECOND copy, whose SessionExpiredError the script never
    # caught. A real interpreter, the real file, a usage error (it exits before touching anything).
    script = textwrap.dedent(f"""
        import runpy, sys
        sys.argv = ["mainfetch.py", "locate"]
        try:
            runpy.run_path({os.path.join(REPO, "mainfetch.py")!r}, run_name="__main__")
        except SystemExit:
            pass
        import gprpc
        module = gprpc._mainfetch()
        print("ONE-MODULE" if module.__name__ == "__main__" and module is sys.modules["mainfetch"] else "TWO-COPIES")
    """)
    done = subprocess.run([sys.executable, "-c", script], cwd=REPO, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=120)
    assert done.stdout.strip().splitlines()[-1] == "ONE-MODULE", done.stdout + done.stderr
