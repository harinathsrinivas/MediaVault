"""IMP-C25 Step 13 — `check_photos`: download-free verification, sweep and confirm (docs/feature-fetch-datetime/PLAN.md
Step 13; P-5, P-8, P-9, P-10, P-11, P-15, P-18; OD-5, OD-7, OD-9; the Step 9b rule; DECISIONS §1e/§1f).

  13.0  the locator's per_approach_pick; `python mainfetch.py …` is ONE module (gprpc's lazy import)
  13.a  the command line, main.py's dispatcher branch, `check_photos pending --list` (offline)
  13.b  the scope: item (alias) · season (anime glued ids) · series (nested, flat, category / language folders, list-capable
        Others roots) · id prefix · account:<a> · pending · hinted · all · --account

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


# ===========================================================================
# 13.b — the scope
# ===========================================================================
class _Lib:
    """A library under construction in the sandbox: leaves (one whole uploaded file each) and season_maps."""

    def __init__(self, sandbox):
        self.root, self.lib = sandbox["local_root"], {}

    def folder(self, *parts):
        path = self.root.joinpath(*parts)
        path.mkdir(parents=True, exist_ok=True)
        return path

    def leaf(self, mid, folder, parent=None, **extra):
        sid = mvcommon.generate_short_id(mid)
        entry = _whole(folder, f"{mid}.mkv", _bytes(mid), sid)
        if parent:
            entry["parent_id"] = parent
        entry.update(extra)
        self.lib[mid] = entry
        return entry

    def season(self, sid, folder, episodes, **extra):
        self.lib[sid] = dict({"type": "season_map", "folder_path": str(folder), "total_episodes": len(episodes),
                              "children": list(episodes)}, **extra)
        for mid in episodes:
            self.leaf(mid, folder, parent=sid)

    def save(self):
        mvcommon.save_library(self.lib)
        return mvcommon.load_library()


def _scope_library(sandbox):
    b = _Lib(sandbox)
    files = ("Series", "English", "Files (1993) {tmdb-4087}")
    b.season("tv-en-1994-files-s02", b.folder(*files, "Season 02"), ["tv-en-1994-files-s02e05", "tv-en-1994-files-s02e06"])
    b.season("tv-en-1995-files-s03", b.folder(*files, "Season 03"), ["tv-en-1995-files-s03e01"])
    b.season("tv-en-1996-files-s04", b.folder(*files, "Season 04"), ["tv-en-1996-files-s04e02"])
    b.season("tv-en-2001-othershow-s01", b.folder("Series", "English", "Other Show", "Season 01"),
             ["tv-en-2001-othershow-s01e01"])
    classic = ("Series", "English", "Classic")
    b.season("tv-en-2019-chernobyl", b.folder(*classic, "Chernobyl (Miniseries) 2019"), ["tv-en-2019-chernobyle01"])
    b.season("tv-en-2001-bandofbrothers", b.folder(*classic, "Band Of Brothers 2001"), ["tv-en-2001-bandofbrotherse01"])
    b.season("ani-ja-2006-deathnote", b.folder("Anime", "Death Note (2006)", "Season 1"), ["ani-ja-2006-deathnote01"])
    b.season("ani-ja-2007-deathnote-s02", b.folder("Anime", "Death Note (2006)", "Season 2"),
             ["ani-ja-2007-deathnote-s0201"])
    b.season("oth-en-2023-f1", b.folder("Sports", "F1", "2023"), ["oth-en-2023-f1e01"])
    b.season("oth-en-2024-f1", b.folder("Sports", "F1", "2024"), ["oth-en-2024-f1e01"])
    b.leaf("mov-en-2010-movie", b.folder("Movies", "English", "Movie (2010)"))
    b.lib["tv-en-1994-files-s02e07"] = {"type": "multi_ep_alias", "alias_of": "tv-en-1994-files-s02e06",
                                        "parent_id": "tv-en-1994-files-s02"}
    b.lib["tv-en-1994-files-s02"]["children"].append("tv-en-1994-files-s02e07")
    return b


def _ids(resolved):
    return [o["manual_id"] for o in resolved["objects"]]


def test_an_item_scope_checks_its_objects_and_an_alias_resolves_to_its_primary(sandbox):
    library = _scope_library(sandbox).save()

    item = main._cp_resolve(library, "tv-en-1994-files-s02e05", None, NOW)
    alias = main._cp_resolve(library, "tv-en-1994-files-s02e07", None, NOW)

    assert (item["kind"], item["canonical"], _ids(item)) == ("item", "tv-en-1994-files-s02e05", ["tv-en-1994-files-s02e05"])
    assert (alias["kind"], _ids(alias)) == ("item", ["tv-en-1994-files-s02e06"])          # its primary, once
    [obj] = item["objects"]
    assert obj == {"manual_id": "tv-en-1994-files-s02e05", "label": "whole", "sha256": _sha256(_bytes(obj["manual_id"])),
                   "kind": "whole", "category": "tv", "uploaded_name": obj["uploaded_name"], "account": "tv",
                   "route": "prefix", "home": "tv", "pushed_at": None}


def test_a_season_scope_checks_its_children_and_anime_glued_ids_are_its_children(sandbox):
    library = _scope_library(sandbox).save()

    season = main._cp_resolve(library, "tv-en-1994-files-s02", None, NOW)
    anime = main._cp_resolve(library, "ani-ja-2006-deathnote", None, NOW)

    assert season["kind"] == "season"
    assert _ids(season) == ["tv-en-1994-files-s02e05", "tv-en-1994-files-s02e06"]       # the alias: its primary, once
    assert (anime["kind"], _ids(anime)) == ("season", ["ani-ja-2006-deathnote01"])        # glued, not a prefix match
    assert {o["account"] for o in anime["objects"]} == {"anime"}


@pytest.mark.parametrize("target", ["tv-en-1995-files-s03", "tv-en-1994-files-s02e05", "tv-en-1994-files-s02e07"])
def test_a_series_is_every_season_under_the_show_folder_whatever_the_years_in_the_ids(sandbox, target):
    b = _scope_library(sandbox)
    library = b.save()

    res = main._cp_resolve(library, f"series:{target}", None, NOW)

    show = os.path.join(str(b.root), "Series", "English", "Files (1993) {tmdb-4087}")
    assert res["kind"] == "series" and res["show"] == show and res["canonical"] == "series:" + main._cp_norm(show)
    assert res["seasons"] == ["tv-en-1994-files-s02", "tv-en-1995-files-s03", "tv-en-1996-files-s04"]
    assert _ids(res) == ["tv-en-1994-files-s02e05", "tv-en-1994-files-s02e06", "tv-en-1995-files-s03e01",
                         "tv-en-1996-files-s04e02"]                   # a prefix (tv-en-1994-files) would get one season
    lines = main._cp_scope_lines(res, library)
    assert lines[0] == (f"  Series: {show} — 3 season(s): tv-en-1994-files-s02 (Season 02), "
                        f"tv-en-1995-files-s03 (Season 03), tv-en-1996-files-s04 (Season 04)")
    assert lines[1] == "  4 object(s) in 4 entr(y/ies) — checked first in: tv 4"


def test_a_series_given_as_its_show_folder_is_the_same_scope(sandbox):
    b = _scope_library(sandbox)
    library = b.save()
    show = os.path.join(str(b.root), "Series", "English", "Files (1993) {tmdb-4087}")

    by_folder = main._cp_resolve(library, f'series:"{show}"', None, NOW)
    by_id = main._cp_resolve(library, "series:tv-en-1996-files-s04", None, NOW)

    assert by_folder["canonical"] == by_id["canonical"] and by_folder["seasons"] == by_id["seasons"]


def test_a_flat_show_in_a_folder_of_other_shows_is_only_its_own_folder(sandbox):
    library = _scope_library(sandbox).save()

    res = main._cp_resolve(library, "series:tv-en-2019-chernobyl", None, NOW)

    assert res["seasons"] == ["tv-en-2019-chernobyl"] and _ids(res) == ["tv-en-2019-chernobyle01"]
    assert res["show"].endswith("Chernobyl (Miniseries) 2019")
    assert any("holds the seasons of more than one show" in n for n in res["notes"])


def test_a_season_shaped_folder_in_a_language_folder_widens_only_while_that_holds_one_show(sandbox):
    b = _scope_library(sandbox)
    b.season("tv-ta-2024-aindhamvedham-s01", b.folder("Series", "Tamil", "Aindham Vedham (2024) S01 EP (01-08)"),
             ["tv-ta-2024-aindhamvedham-s01e01"])
    alone = main._cp_resolve(b.save(), "series:tv-ta-2024-aindhamvedham-s01", None, NOW)
    b.season("tv-ta-2023-othertamil-s01", b.folder("Series", "Tamil", "Other Tamil S01"),
             ["tv-ta-2023-othertamil-s01e01"])
    crowded = main._cp_resolve(b.save(), "series:tv-ta-2024-aindhamvedham-s01", None, NOW)

    assert alone["seasons"] == crowded["seasons"] == ["tv-ta-2024-aindhamvedham-s01"]
    assert crowded["show"].endswith("Aindham Vedham (2024) S01 EP (01-08)") and crowded["notes"]


def test_anime_seasons_with_glued_episode_ids_form_one_series(sandbox):
    library = _scope_library(sandbox).save()

    res = main._cp_resolve(library, "series:ani-ja-2006-deathnote01", None, NOW)

    assert res["seasons"] == ["ani-ja-2006-deathnote", "ani-ja-2007-deathnote-s02"]
    assert _ids(res) == ["ani-ja-2006-deathnote01", "ani-ja-2007-deathnote-s0201"]


def test_others_series_and_every_list_capable_root_is_a_category_folder(sandbox, monkeypatch):
    b = _scope_library(sandbox)
    monkeypatch.setattr(main, "CATEGORY_ROOTS", dict(main.CATEGORY_ROOTS, other=["Sports", "Documentary"]))
    b.season("oth-en-2024-planet-s01", b.folder("Documentary", "Planet S01"), ["oth-en-2024-planet-s01e01"])
    b.season("oth-en-2020-ocean-s01", b.folder("Documentary", "Ocean S01"), ["oth-en-2020-ocean-s01e01"])
    library = b.save()

    f1 = main._cp_resolve(library, "series:oth-en-2023-f1", None, NOW)
    planet = main._cp_resolve(library, "series:oth-en-2024-planet-s01", None, NOW)

    assert f1["seasons"] == ["oth-en-2023-f1", "oth-en-2024-f1"] and f1["show"].endswith(os.path.join("Sports", "F1"))
    assert {o["account"] for o in f1["objects"]} == {"others"}
    assert planet["seasons"] == ["oth-en-2024-planet-s01"]                  # its parent IS the second Others root
    assert any("category folder" in n for n in planet["notes"])


def test_a_prefix_scope_and_the_named_scopes(sandbox, monkeypatch):
    b = _scope_library(sandbox)
    folder = b.folder("Movies", "English", "Pushed (2026)")
    data = _bytes("pushed")
    b.lib["mov-en-2026-pushed"] = _whole(folder, "Pushed.mkv", data, "pu5hed")
    _capture(folder, "pu5hed", [_obj(data, _up("Pushed.mkv", "pu5hed"))])
    b.lib["mov-en-2011-nohash"] = dict(_whole(folder, "NoHash.mkv", b"x", "n0hash"), hash=None)
    library = b.save()
    monkeypatch.setattr(mvcommon, "_CONFIG_CACHE", {"fetch_account_overrides": {"tv-en-1994-files": "movies"}})

    prefix = main._cp_resolve(library, "tv-en-1994-files", None, NOW)
    everything = main._cp_resolve(library, "all", None, NOW)
    movies = main._cp_resolve(library, "account:movies", None, NOW)
    pending = main._cp_resolve(library, "pending", None, NOW)

    assert (prefix["kind"], _ids(prefix)) == ("prefix", ["tv-en-1994-files-s02e05", "tv-en-1994-files-s02e06"])
    assert {o["account"] for o in prefix["objects"]} == {"movies"} and {o["route"] for o in prefix["objects"]} == {
        "override"}
    assert len(everything["objects"]) == 13 and "mov-en-2011-nohash" not in _ids(everything)
    assert any("1 object(s) left out: the library holds no SHA-256" in n for n in everything["notes"])
    assert _ids(movies) == ["mov-en-2010-movie", "mov-en-2026-pushed", "tv-en-1994-files-s02e05",
                            "tv-en-1994-files-s02e06"]
    assert (pending["kind"], _ids(pending)) == ("pending", ["mov-en-2026-pushed"])
    assert pending["objects"][0]["pushed_at"] == "2026-09-30T10:00:00Z"
    assert _ids(main._cp_resolve(library, "all", "anime", NOW)) == ["ani-ja-2006-deathnote01",
                                                                    "ani-ja-2007-deathnote-s0201"]
    with pytest.raises(ValueError) as err:
        main._cp_resolve(library, "tv-xx-nothing", None, NOW)
    assert "no library id is or starts with 'tv-xx-nothing'" in str(err.value)
    with pytest.raises(ValueError):
        main._cp_resolve(library, "series:mov-en-2010-movie", None, NOW)       # a movie is no season


def test_the_hinted_scope_checks_each_hinted_object_in_the_hints_account(sandbox):
    library = _scope_library(sandbox).save()
    target = next(o for o in mvcommon.iter_entry_objects("tv-en-1995-files-s03e01", library["tv-en-1995-files-s03e01"]))
    base = {"manual_id": "tv-en-1995-files-s03e01", "label": "whole", "sha256": target["sha256"]}
    mvcommon.append_hint(dict(base, account="tv", kind="url", photo_id="AF1QipFAKEhintedfirst001"))
    mvcommon.append_hint(dict(base, account="movies", kind="datetime", taken_ms=1_700_000_000_000, gmt_offset_min=480,
                              taken_local="2023-11-15T06:13:20"))                           # the newer hint wins
    mvcommon.append_hint(dict(base, manual_id="tv-en-1996-files-s04e02", sha256="0" * 64, account="tv", kind="url",
                              photo_id="AF1QipFAKEhintedgone0001"))                         # its object is gone

    res = main._cp_resolve(library, "hinted", None, NOW)

    assert res["kind"] == "hinted" and _ids(res) == ["tv-en-1995-files-s03e01"]
    [obj] = res["objects"]
    assert (obj["account"], obj["route"]) == ("movies", "hint")
    assert obj["hint"] == {"kind": "datetime", "account": "movies", "taken_ms": 1_700_000_000_000, "gmt_offset_min": 480}
    assert any("1 hint(s) name an object no longer in the library" in n for n in res["notes"])
