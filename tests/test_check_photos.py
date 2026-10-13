"""IMP-C25 Step 13 — `check_photos`: download-free verification, sweep and confirm (docs/feature-fetch-datetime/PLAN.md
Step 13; P-5, P-8, P-9, P-10, P-11, P-15, P-18; OD-5, OD-7, OD-9; the Step 9b rule; DECISIONS §1e/§1f).

  13.0  the locator's per_approach_pick; `python mainfetch.py …` is ONE module (gprpc's lazy import)
  13.a  the command line, main.py's dispatcher branch, `check_photos pending --list` (offline)

Hermetic: the fake Google Photos world + its batchexecute emulator (tests/gphotos_fake.py); mainfetch's and gprpc's clocks
are fake (nothing waits); the autouse fixtures of tests/conftest.py redirect every state dir, ~/Downloads, the process
table and the Chrome profiles. No network, no browser, no adb, no real C:\\Media. Synthetic ids only (every photo id
contains "FAKE").
"""
import os
import subprocess
import sys
import textwrap
from datetime import datetime, timezone

import pytest

import gprpc
import main
import mainfetch
import mvcommon
from test_fetch_harvester import _Clock
from test_locator import (EP, SHOW, SID, TAKEN, _assert_read_only_rpc, _bytes, _capture, _ctx, _folder, _learned,
                          _locators, _obj, _sha256, _tree, _up, _whole)
from test_map_photos import _main_guard

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NOW = datetime(2026, 10, 1, 0, 0, 0, tzinfo=timezone.utc)   # 14 h after test_locator._capture's push (09-30 10:00 Z)


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


# ===========================================================================
# 13.a — the command line, the dispatcher, `pending --list`
# ===========================================================================
_DEFAULTS = {"scope": None, "approaches": None, "account": None, "recheck": False, "apply": False, "apply_run": None,
             "limit": None, "pace": None, "max_minutes": None, "devices": False, "list_only": False}


@pytest.mark.parametrize("tokens, expected", [
    (["tv-en-2014-show-s02"], {"scope": "tv-en-2014-show-s02"}),
    (["all", "--approaches", "all", "--apply"], {"scope": "all", "approaches": "all", "apply": True}),
    (["account:tv", "--apply-run", "latest"], {"scope": "account:tv", "apply_run": "latest"}),
    (["series:tv-en-1994-files-s02", "--account", "movies", "--recheck", "--limit", "20", "--pace", "0",
      "--max-minutes", "15", "--devices"],
     {"scope": "series:tv-en-1994-files-s02", "account": "movies", "recheck": True, "limit": 20, "pace": 0.0,
      "max_minutes": 15.0, "devices": True}),
    ([r"series:C:\Media\Series\Show (2020)", "--max-minutes", "0.5"],
     {"scope": r"series:C:\Media\Series\Show (2020)", "max_minutes": 0.5}),
    (["pending", "--list", "--account", "tv"], {"scope": "pending", "list_only": True, "account": "tv"}),
    (["--apply", "hinted"], {"scope": "hinted", "apply": True}),                   # options in any order
])
def test_the_command_line(tokens, expected):
    assert main.parse_check_photos_args(tokens) == dict(_DEFAULTS, **expected)


@pytest.mark.parametrize("tokens, problem", [
    ([], "a scope is required"),
    (["--apply"], "a scope is required"),
    (["a", "b"], "one scope only"),
    (["all", "--frobnicate"], "unknown option --frobnicate"),
    (["all", "--approaches"], "--approaches needs a value"),
    (["all", "--approaches", "some"], "--approaches must be one of best, all"),
    (["all", "--account", "music"], "--account must be one of movies, tv, anime, others"),
    (["all", "--limit", "0"], "--limit must be at least 1"),
    (["all", "--limit", "two"], "--limit needs a number"),
    (["all", "--pace", "-1"], "--pace must be at least 0"),
    (["all", "--max-minutes", "0"], "--max-minutes must be above 0"),
    (["all", "--max-minutes", "inf"], "--max-minutes must be above 0"),
    (["series:"], "series: needs a season id or a show folder"),
    (["account:music"], "account: must name one of"),
    (["all", "--apply", "--apply-run", "latest"], "--apply and --apply-run exclude each other"),
    (["all", "--apply-run", "latest", "--limit", "5"], "--apply-run applies a finished run without searching"),
    (["all", "--apply-run", "latest", "--recheck"], "--apply-run applies a finished run without searching"),
    (["all", "--list"], "--list works with the pending scope only"),
    (["pending", "--list", "--apply"], "pending --list is offline: it takes only --account"),
    (["pending", "--list", "--approaches", "all"], "pending --list is offline: it takes only --account"),
])
def test_a_bad_command_line_names_its_problem(tokens, problem):
    with pytest.raises(ValueError) as err:
        main.parse_check_photos_args(tokens)
    assert problem in str(err.value)


def _dispatch(argv, status="ok"):
    """Run main.py's dispatcher with `argv` against a recorder for cmd_check_photos -> its recorded kwargs."""
    import sys as real_sys

    class Sys:
        def __init__(self):
            self.argv = ["main.py", "check_photos", *argv]

        def __getattr__(self, name):
            return getattr(real_sys, name)

    calls = []
    namespace = dict(main.__dict__, sys=Sys(),
                     cmd_check_photos=lambda **kwargs: calls.append(kwargs) or {"status": status})
    exec(_main_guard(), namespace)
    return calls


def test_the_dispatcher_hands_the_parsed_options_to_cmd_check_photos():
    calls = _dispatch(["account:anime", "--approaches", "all", "--limit", "20", "--apply"])
    assert calls == [dict(_DEFAULTS, scope="account:anime", approaches="all", limit=20, apply=True)]


@pytest.mark.parametrize("status", ["error", "refused"])
def test_the_dispatcher_exits_1_on_a_usage_error_a_failed_or_a_refused_run(capsys, status):
    with pytest.raises(SystemExit) as stop:
        _dispatch(["all", "--frobnicate"])
    assert stop.value.code == 1
    out = capsys.readouterr().out
    assert "❌ check_photos: unknown option --frobnicate" in out and f"   Usage: {main.CHECK_USAGE}" in out
    with pytest.raises(SystemExit) as stop:
        _dispatch(["all"], status=status)
    assert stop.value.code == 1
    assert _dispatch(["pending", "--list"], status="ok") == [dict(_DEFAULTS, scope="pending", list_only=True)]


def _no_chrome(monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError("Chrome must not be started")
    monkeypatch.setattr(mainfetch, "init_driver", refuse)


def _pending_library(sandbox):
    """E1 pushed (captured), not confirmed · E2 pushed and confirmed · E3 legacy (no capture) · E4 pushed, an
    X-Files-like episode an mvconfig override routes to movies -> {id: data}."""
    folder = sandbox["local_root"] / "Series" / "Demo" / "Season 01"
    folder.mkdir(parents=True)
    datas = {mid: _bytes(mid) for mid in ("tv-en-2015-demo-s01e01", "tv-en-2015-demo-s01e02", "mov-en-2010-legacy",
                                          "tv-en-1994-files-s02e05")}
    lib = {}
    for n, mid in enumerate(datas, 1):
        sid = f"pe{n}pe{n}"
        name = f"Item{n}.mkv"
        sub = folder / mid
        sub.mkdir()
        block = None
        if mid != "mov-en-2010-legacy":
            _capture(sub, sid, [_obj(datas[mid], _up(name, sid))])
        if mid == "tv-en-2015-demo-s01e02":
            block = mvcommon.new_gphotos_block(_sha256(datas[mid]), accounts={"tv": {
                "photo_id": "AF1QipFAKEconfirmed000001", "upload_ms": 1, "verified_by": "dedupkey", "source": "check",
                "confirmed": True}})
        lib[mid] = _whole(sub, name, datas[mid], sid, block=block)
    mvcommon.save_library(lib)
    return datas


def test_pending_list_is_offline_and_lists_only_unconfirmed_pushes(sandbox, monkeypatch, capsys, tmp_path):
    _pending_library(sandbox)
    monkeypatch.setattr(mvcommon, "_now_utc", lambda: NOW)
    monkeypatch.setattr(mvcommon, "_CONFIG_CACHE", {"fetch_account_overrides": {"tv-en-1994-files": "movies"}})
    _no_chrome(monkeypatch)
    before = _tree(tmp_path)

    res = main.cmd_check_photos("pending", list_only=True)

    out = capsys.readouterr().out
    assert res["status"] == "ok"
    assert [(o["manual_id"], o["label"]) for o in res["objects"]] == [("tv-en-1994-files-s02e05", "whole"),
                                                                   ("tv-en-2015-demo-s01e01", "whole")]
    assert "2 object(s) in 2 entr(y/ies) await confirmation:" in out
    assert ("    tv-en-2015-demo-s01e01 · whole — Item1 [pe1pe1].mkv · pushed 2026-09-30 10:00 UTC (14 h ago) · "
            "account tv") in out
    assert "tv-en-1994-files-s02e05 · whole — Item4 [pe4pe4].mkv" in out and "account movies" in out
    assert "demo-s01e02" not in out and "mov-en-2010-legacy" not in out          # confirmed · legacy (no capture)
    assert "python main.py check_photos pending --apply" in out
    assert _tree(tmp_path) == before                                             # nothing written anywhere


def test_pending_list_filters_by_account_and_says_when_nothing_waits(sandbox, monkeypatch, capsys):
    _pending_library(sandbox)
    monkeypatch.setattr(mvcommon, "_now_utc", lambda: NOW)
    _no_chrome(monkeypatch)

    res = main.cmd_check_photos("pending", account="anime", list_only=True)

    assert res["objects"] == [] and "  Nothing pending in 'anime'." in capsys.readouterr().out
    res = main.cmd_check_photos("pending", account="tv", list_only=True)
    assert {o["manual_id"] for o in res["objects"]} == {"tv-en-2015-demo-s01e01", "tv-en-1994-files-s02e05"}
