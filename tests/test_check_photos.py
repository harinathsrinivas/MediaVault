"""IMP-C25 Step 13 — `check_photos`: download-free verification, sweep and confirm (docs/feature-fetch-datetime/PLAN.md
Step 13; P-5, P-8, P-9, P-10, P-11, P-15, P-18; OD-5, OD-7, OD-9; the Step 9b rule; DECISIONS §1e/§1f).

  13.0  the locator's per_approach_pick; `python mainfetch.py …` is ONE module (gprpc's lazy import)
  13.a  the command line, main.py's dispatcher branch, `check_photos pending --list` (offline)
  13.b  the scope: item (alias) · season (anime glued ids) · series (nested, flat, category / language folders, list-capable
        Others roots) · id prefix · account:<a> · pending · hinted · all · --account
  13.c  the run directory (new · resumed · --recheck · complete -> afresh), the child's command line, the streaming
        spawn (Ctrl+C waits for the child), the conftest guard (no test spawns the real child)
  13.d  the engine (`python mainfetch.py check`): one session per account, results.jsonl + progress.json only, pacing,
        --limit, --max-minutes, the error breaker, sign-out, Ctrl+C, the lock (held · taken over), a missing profile, the
        Chrome guarantee (killed through its Popen handle when the shared close left it running), the child's CLI
  13.e  RPC acceleration: hash_lookup batches of 50 warm the locator's own cache (content keys + mapped cloud keys —
        a mapped key never becomes a content proof), batch_info names, the UI fallback, the quality post-pass
  13.f  the other accounts (an old object not found / wrong-item in its own account — never a recent push, never an
        ambiguous one), the session order, the hinted locators (url -> photo_id, datetime -> the taken second)

Hermetic: the fake Google Photos world + its batchexecute emulator (tests/gphotos_fake.py); mainfetch's and gprpc's clocks
are fake (nothing waits); the autouse fixtures of tests/conftest.py redirect every state dir, ~/Downloads, the process
table and the Chrome profiles. No network, no browser, no adb, no real C:\\Media. Synthetic ids only (every photo id
contains "FAKE").
"""
import contextlib
import functools
import json
import os
import subprocess
import sys
import textwrap
import types
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
REAL_SPAWN = main._cp_spawn          # main's real child spawner — captured before tests/conftest.py's guard replaces it


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


@functools.lru_cache(maxsize=1)
def _guard():
    """main.py's `if __name__ == "__main__":` body, compiled once for this module (parsing main.py is not cheap)."""
    return _main_guard()


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
    exec(_guard(), namespace)
    return calls


def test_the_dispatcher_hands_the_parsed_options_to_cmd_check_photos():
    calls = _dispatch(["account:anime", "--approaches", "all", "--limit", "20", "--apply"])
    assert calls == [dict(_DEFAULTS, scope="account:anime", approaches="all", limit=20, apply=True)]


@pytest.mark.parametrize("status", ["error", "refused", "stopped", "interrupted"])
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
                   "route": "prefix", "home": "tv", "pushed_at": None, "elsewhere": True}


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
    assert pending["objects"][0]["elsewhere"] is False                     # 14 h old: not looked for elsewhere (9b)
    assert all(o["elsewhere"] for o in everything["objects"] if o["manual_id"] != "mov-en-2026-pushed")
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


# ===========================================================================
# 13.c — the run directory and the child
# ===========================================================================
def _fake_child(monkeypatch, state="partial", reason="limit", code=0, interrupted=False, results=()):
    """main._cp_spawn replaced by a recorder that plays the child: it writes progress.json (state, reason) and the given
    results.jsonl lines into the run directory it was handed -> the recorded argv lists."""
    calls = []

    def spawn(argv):
        calls.append(list(argv))
        run_dir = argv[argv.index("--run") + 1]
        if state is not None:
            main._cp_write_json(os.path.join(run_dir, "progress.json"), {"v": 1, "state": state, "reason": reason})
        for line in results:
            mvcommon._append_jsonl(os.path.join(run_dir, "results.jsonl"), line)
        return code, interrupted

    monkeypatch.setattr(main, "_cp_spawn", spawn)
    return calls


def _routed(obj, outcome="exactly-one"):
    return {"v": 1, "kind": "check", "pass": "routed", "account": obj["account"], "manual_id": obj["manual_id"],
            "label": obj["label"], "sha256": obj["sha256"], "outcome": outcome}


def test_a_check_creates_its_run_directory_and_hands_it_to_the_child(sandbox, monkeypatch, capsys):
    _scope_library(sandbox).save()
    monkeypatch.setattr(mvcommon, "_now_utc", lambda: NOW)
    calls = _fake_child(monkeypatch)

    res = main.cmd_check_photos("tv-en-1994-files-s02", limit=20, max_minutes=1.5, pace=0.0)

    out = capsys.readouterr().out
    run_dir = res["run_dir"]
    assert res["status"] == "partial" and os.path.dirname(run_dir) == os.path.join(mvcommon.GPHOTOS_STATE_DIR, "check")
    assert os.path.basename(run_dir) == "20261001T000000Z"
    assert calls == [[sys.executable, os.path.join(REPO, "mainfetch.py"), "check", "--run", run_dir, "--limit", "20",
                      "--max-minutes", "1.5", "--pace", "0"]]
    doc = main._cp_read_json(os.path.join(run_dir, "run.json"))
    assert {k: doc[k] for k in ("v", "run", "created_at", "scope", "scope_text", "kind", "account", "approaches",
                                "source")} == {"v": 1, "run": "check-20261001T000000Z", "created_at": "2026-10-01T00:00:00Z",
                                               "scope": "tv-en-1994-files-s02", "scope_text": "tv-en-1994-files-s02",
                                               "kind": "season", "account": None, "approaches": "best",
                                               "source": "check"}
    assert [o["manual_id"] for o in doc["objects"]] == ["tv-en-1994-files-s02e05", "tv-en-1994-files-s02e06"]
    assert out.startswith("=== CHECK PHOTOS (dry-run) — scope: tv-en-1994-files-s02 · approaches best — download-free ===")
    assert "  2 object(s) in 2 entr(y/ies) — checked first in: tv 2" in out and f"  Run: {run_dir}" in out


def test_a_re_run_continues_the_unfinished_run_and_recheck_starts_afresh(sandbox, monkeypatch, capsys):
    _scope_library(sandbox).save()
    monkeypatch.setattr(mvcommon, "_now_utc", lambda: NOW)
    calls = _fake_child(monkeypatch)
    first = main.cmd_check_photos("tv-en-1994-files-s02")["run_dir"]
    doc = main._cp_read_json(os.path.join(first, "run.json"))
    mvcommon._append_jsonl(os.path.join(first, "results.jsonl"), _routed(doc["objects"][0]))
    capsys.readouterr()

    again = main.cmd_check_photos("tv-en-1994-files-s02")["run_dir"]
    resumed = capsys.readouterr().out
    fresh = main.cmd_check_photos("tv-en-1994-files-s02", recheck=True)["run_dir"]
    other = main.cmd_check_photos("tv-en-1994-files-s02", approaches="all")["run_dir"]

    assert again == first and len(calls) == 4
    assert "↻ Continuing the unfinished run 20261001T000000Z (begun 2026-10-01T00:00:00Z): 1 of 2 object(s) checked " \
           "already — --recheck starts afresh" in resumed
    assert fresh != first and os.path.basename(fresh) == "20261001T000000Z-2"                # same second: -2
    assert other not in (first, fresh)                                                        # another key: its own run


def test_a_complete_run_is_never_continued(sandbox, monkeypatch):
    _scope_library(sandbox).save()
    monkeypatch.setattr(mvcommon, "_now_utc", lambda: NOW)
    _fake_child(monkeypatch, state="complete", reason=None)
    first = main.cmd_check_photos("tv-en-1994-files-s02")
    second = main.cmd_check_photos("tv-en-1994-files-s02")
    assert first["status"] == second["status"] == "ok" and first["run_dir"] != second["run_dir"]


@pytest.mark.parametrize("code, interrupted, progress, status", [
    (3, False, None, "refused"),
    (1, False, None, "error"),
    (0, True, {"state": "partial", "reason": "interrupted"}, "interrupted"),
    (None, True, None, "interrupted"),
    (0, False, {"state": "complete"}, "ok"),
    (0, False, {"state": "partial", "reason": "limit"}, "partial"),
    (0, False, {"state": "partial", "reason": "max-minutes"}, "partial"),
    (0, False, {"state": "partial", "reason": "signed-out"}, "stopped"),
    (0, False, {"state": "running"}, "stopped"),
])
def test_the_status_of_a_child_run(code, interrupted, progress, status):
    assert main._cp_status(code, interrupted, progress) == status


def test_a_refused_child_is_reported(sandbox, monkeypatch, capsys):
    _scope_library(sandbox).save()
    _fake_child(monkeypatch, state=None, code=3)
    assert main.cmd_check_photos("tv-en-1994-files-s02")["status"] == "refused"
    assert "holds the fetch session lock — nothing was checked" in capsys.readouterr().out


def test_an_empty_scope_starts_nothing(sandbox, monkeypatch, capsys):
    _scope_library(sandbox).save()
    calls = _fake_child(monkeypatch)
    res = main.cmd_check_photos("pending")
    assert res == {"status": "ok", "run_dir": None} and calls == []
    assert "  Nothing to check." in capsys.readouterr().out
    assert not os.path.exists(os.path.join(mvcommon.GPHOTOS_STATE_DIR, "check"))


def test_no_test_can_spawn_the_real_check_child():
    with pytest.raises(AssertionError) as err:
        main._cp_spawn([sys.executable, "mainfetch.py", "check", "--run", "x"])
    assert "a test tried to spawn the real check_photos child" in str(err.value)


class _FakeProc:
    """subprocess.Popen's stand-in for the streaming idiom: its stdout yields the lines; Ctrl+C arrives once, after the
    `stop_after`-th line (the child keeps writing its closing lines)."""

    def __init__(self, lines, stop_after=None):
        self.lines, self.stop_after, self.waited = list(lines), stop_after, 0
        self.stdout = self

    def __iter__(self):
        return self

    def __next__(self):
        if self.stop_after is not None and self.stop_after == 0:
            self.stop_after = None
            raise KeyboardInterrupt
        if not self.lines:
            raise StopIteration
        if self.stop_after is not None:
            self.stop_after -= 1
        return self.lines.pop(0)

    def wait(self):
        self.waited += 1
        return 0


def test_the_spawn_streams_the_child_and_waits_for_it_after_a_ctrl_c(monkeypatch, capsys):
    proc, seen = _FakeProc(["one\n", "two\n", "closing Chrome\n"], stop_after=1), {}

    def popen(argv, **kwargs):
        seen.update(argv=argv, **kwargs)
        return proc

    monkeypatch.setattr(main.subprocess, "Popen", popen)
    code, interrupted = REAL_SPAWN(["python", "mainfetch.py", "check", "--run", "x"])

    out = capsys.readouterr().out
    assert (code, interrupted, proc.waited) == (0, True, 1)
    assert out.index("one") < out.index("🛑 Stopping — waiting for the check to close its Chrome") < out.index(
        "closing Chrome")                                          # every line after the Ctrl+C still printed
    assert seen["env"]["PYTHONUNBUFFERED"] == "1" and seen["env"]["PYTHONIOENCODING"] == "utf-8"
    assert seen["stdout"] is main.subprocess.PIPE and seen["stderr"] is main.subprocess.STDOUT


# ===========================================================================
# 13.d — the engine: `python mainfetch.py check --run <dir>` (mainfetch.cmd_check)
# ===========================================================================
WEB_MARKERS = ("🔹 PROCESSING:", "Detected Split File", "✅ MOVED:", "✅ ENTRY COMPLETE.")   # PLAN P-13
SEASON = "tv-en-2015-demo-s01"


class _Beat:
    """The lock heartbeat's stand-in (the crawler's Heartbeat refreshes the real lock file): counts beats; after
    `lose_after` beats another command has taken the lock over."""

    def __init__(self):
        self.beats, self.lose_after = 0, None

    def beat(self):
        self.beats += 1
        if self.lose_after is not None and self.beats > self.lose_after:
            from tools import gp_inventory
            raise gp_inventory.LockLost("the fetch session lock was taken over by another MediaVault command")


@pytest.fixture()
def cw(fake_photos, monkeypatch):
    """A check on the fake world: init_driver hands out one fake driver per account session (no Chrome), the fetch lock
    is a stand-in with a counting heartbeat, no socket, no pacing jitter — and main's child spawner runs the REAL
    `mainfetch.py check` command line in-process (parse_check_args + cmd_check)."""
    drivers, beat = {}, _Beat()

    def init(profile_key="movies"):
        drivers.setdefault(profile_key, []).append(fake_photos.driver(profile_key))
        return drivers[profile_key][-1]

    @contextlib.contextmanager
    def lock():
        yield beat

    spawned = []

    def spawn(argv):
        spawned.append(list(argv))
        return mainfetch.cmd_check(**mainfetch.parse_check_args(argv[1:])), False

    monkeypatch.setattr(mainfetch, "init_driver", init)
    monkeypatch.setattr(mainfetch, "_check_lock", lock)
    monkeypatch.setattr(mainfetch, "_debug_port_free", lambda *a, **k: True)
    monkeypatch.setattr(mainfetch, "_check_jitter", lambda low, high: 0)
    monkeypatch.setattr(main, "_cp_spawn", spawn)
    return types.SimpleNamespace(world=fake_photos, drivers=drivers, beat=beat, spawned=spawned)


def _season_of(sandbox, world, modes, account="tv", in_account=None):
    """A season SEASON with one whole-file episode per entry of `modes` {n: mode}; episode n's item lives in
    `in_account` (default `account`) under its uploaded name, holding _bytes(<episode id>). Modes:
      learned  a trusted block with the content key and the item's photo_id (S0a, proof dedupkey)
      mapped   a LEGACY object mapped by name: the library holds dummy bytes, the block's record names the item and its
               cloud dedupKey but the block has NO content key (S0a, proof filename)
      pending  a new push: a capture with the SHA-1 + DateUTC, no block (S0b, proof dedupkey)
      legacy   nothing but the library entry (S2 exact name, proof filename)
      missing  like legacy, but no item anywhere
    -> {episode id: fake item or None}."""
    folder = sandbox["local_root"] / "Series" / "Demo" / "Season 01"
    folder.mkdir(parents=True, exist_ok=True)
    lib, items = {SEASON: {"type": "season_map", "folder_path": str(folder), "total_episodes": len(modes),
                           "children": [f"{SEASON}e0{n}" for n in modes]}}, {}
    for n, mode in modes.items():
        mid, name, sid = f"{SEASON}e0{n}", f"Demo.S01E0{n}.mkv", f"de{n}de{n}"
        data = _bytes(mid)
        item = None if mode == "missing" else world.add_item(in_account or account, _up(name, sid), data=data,
                                                             taken=TAKEN)
        block, held = None, data
        if mode == "learned":
            block = _learned(item, account=account)
        elif mode == "mapped":
            held = _bytes(mid + " dummy")
            block = _learned(item, data=held, account=account, proof="filename", content_key=False)
        elif mode == "pending":
            _capture(folder, sid, [_obj(data, _up(name, sid))])
        lib[mid] = dict(_whole(folder, name, held, sid, block=block), parent_id=SEASON)
        items[mid] = item
    mvcommon.save_library(lib)
    return items


def _demo_season(sandbox, world, episodes=(1, 2, 3), learned=(1, 2, 3), account="tv"):
    """_season_of with episodes in `learned` learned and the others legacy."""
    return _season_of(sandbox, world, {n: "learned" if n in learned else "legacy" for n in episodes}, account)


def _results(run_dir, kind="check"):
    return [line for line in main._cp_read_results(run_dir) if line.get("kind") == kind]


def _progress(run_dir):
    return main._cp_read_json(os.path.join(run_dir, "progress.json"))


def test_the_engine_checks_every_object_in_its_account_and_writes_only_its_run_files(cw, sandbox, tmp_path, capsys):
    items = _demo_season(sandbox, cw.world, learned=(1, 2))           # e01, e02 learned · e03 legacy (by name)
    library_before = {p: p.read_bytes() for p in (sandbox["lib_series"], sandbox["lib_movies"])}
    outside = [p for p in _tree(tmp_path) if p.split(os.sep)[0] != "mv_state"]

    res = main.cmd_check_photos(SEASON)

    out = capsys.readouterr().out
    assert res["status"] == "ok" and _progress(res["run_dir"])["state"] == "complete"
    lines = _results(res["run_dir"])
    assert [(l["manual_id"], l["pass"], l["account"], l["outcome"], l["stage"], l["proof"]) for l in lines] == [
        (f"{SEASON}e01", "routed", "tv", "exactly-one", "s0_photo_id", "dedupkey"),
        (f"{SEASON}e02", "routed", "tv", "exactly-one", "s0_photo_id", "dedupkey"),
        (f"{SEASON}e03", "routed", "tv", "exactly-one", "s2_exact_name", "filename")]
    assert [l["photo_id"] for l in lines] == [items[m].photo_id for m in sorted(items)]
    assert all(set(v) == {"outcome", "n"} for l in lines for v in l["per_approach"].values())
    assert list(cw.drivers) == ["tv"] and len(cw.drivers["tv"]) == 1               # one session, one account
    assert cw.world.downloads == [] and "pLFTfd" not in cw.world.rpc_ids_sent()      # download-free
    assert not [a for a in cw.drivers["tv"][0].actions if a["kind"] in ("shift_d", "open")]
    assert mvcommon.read_learned() == [] and mvcommon.read_failed() == []            # a dry run records nothing
    assert {p: p.read_bytes() for p in library_before} == library_before             # the library untouched
    assert [p for p in _tree(tmp_path) if p.split(os.sep)[0] != "mv_state"] == outside   # only the state dirs grew
    assert sorted(os.listdir(res["run_dir"])) == ["progress.json", "results.jsonl", "run.json"]
    assert not any(marker in out for marker in WEB_MARKERS)                          # P-13
    assert not any(item.photo_id in out for item in items.values())                  # P-15: no photo id printed
    assert "   === CHECK 'tv' — 3 object(s) routed here · 0 looked for here from other accounts ===" in out
    assert f"   🔎 {SEASON}e03 · whole — exactly-one · S2 exact_name · proof filename · via rpc" in out
    journals = os.listdir(mainfetch.FETCH_JOURNAL_DIR)
    assert len(journals) == 1 and json.load(open(os.path.join(mainfetch.FETCH_JOURNAL_DIR, journals[0]),
                                                 encoding="utf-8"))["state"] == "clean"


def test_progress_records_every_invocation(cw, sandbox):
    _demo_season(sandbox, cw.world)
    first = main.cmd_check_photos(SEASON, limit=2)
    second = main.cmd_check_photos(SEASON)

    progress = _progress(first["run_dir"])
    assert first["run_dir"] == second["run_dir"] and (first["status"], second["status"]) == ("partial", "ok")
    assert [(i["n"], i["state"], i["reason"], i["checked"], i["limit"]) for i in progress["invocations"]] == [
        (1, "partial", "limit", 2, 2), (2, "complete", None, 1, None)]
    assert progress["state"] == "complete" and len(_results(first["run_dir"])) == 3


def test_max_minutes_stops_between_two_objects_and_a_rerun_continues(cw, sandbox, clock):
    _demo_season(sandbox, cw.world)
    # 1.2 s: object 1 starts at 0 s, object 2 at 0.5 s (gprpc's 0.5 s between the two item-info calls), object 3 at 1.5 s
    res = main.cmd_check_photos(SEASON, max_minutes=0.02, pace=0.4)

    assert (res["status"], _progress(res["run_dir"])["reason"]) == ("partial", "max-minutes")
    assert len(_results(res["run_dir"])) == 2
    assert main.cmd_check_photos(SEASON)["status"] == "ok" and len(_results(res["run_dir"])) == 3


@pytest.mark.parametrize("ui, pace, low, high", [(False, None, 0.5, 3.0), (True, None, 3.0, None),
                                                 (False, 2.0, 2.0, 3.0)], ids=["rpc", "web-pages", "--pace"])
def test_objects_are_paced_by_the_path_they_took(cw, sandbox, clock, monkeypatch, ui, pace, low, high):
    # PLAN Step 13: UI 3 s / RPC 0.5 s between two objects (+ jitter, 0 here), or --pace S. The gap after the first
    # object is the drift check's page (one per run) — the second gap shows the path of an ordinary object.
    _demo_season(sandbox, cw.world)
    cw.world.wiz_token_missing["tv"] = ui
    starts, real = [], mainfetch.locate_object
    monkeypatch.setattr(mainfetch, "locate_object", lambda *a, **k: (starts.append(clock.now), real(*a, **k))[1])

    main.cmd_check_photos(SEASON, pace=pace)

    gaps = [b - a for a, b in zip(starts, starts[1:])]
    assert len(gaps) == 2 and all(g >= low for g in gaps)
    assert high is None or gaps[1] < high


def test_five_objects_in_a_row_that_cannot_be_checked_stop_the_run(cw, sandbox, monkeypatch, capsys):
    _demo_season(sandbox, cw.world, episodes=(1, 2, 3, 4, 5, 6, 7), learned=())

    def broken(*args, **kwargs):
        raise RuntimeError("the page did not render")

    monkeypatch.setattr(mainfetch, "locate_object", broken)
    res = main.cmd_check_photos(SEASON)

    assert (res["status"], _progress(res["run_dir"])["reason"]) == ("stopped", "breaker")
    assert [(l["outcome"], l["error"]) for l in _results(res["run_dir"])] == [("error", "RuntimeError")] * 5
    assert "5 objects in a row could not be checked — the check stops here" in capsys.readouterr().out


def test_a_signed_out_profile_stops_the_run_with_its_remediation(cw, sandbox, capsys):
    _demo_season(sandbox, cw.world)
    cw.world.session["tv"] = "expired"

    res = main.cmd_check_photos(SEASON)

    out = capsys.readouterr().out
    assert (res["status"], _progress(res["run_dir"])["reason"]) == ("stopped", "signed-out")
    assert "❌ Profile 'tv' is logged out." in out and "the check continues where it stopped" in out
    assert _results(res["run_dir"]) == [] and cw.drivers["tv"][0].quit_called


def test_ctrl_c_stops_after_the_current_object_and_still_closes_chrome(cw, sandbox, monkeypatch, capsys):
    _demo_season(sandbox, cw.world)
    real, calls, closed = mainfetch.locate_object, [], []

    def locate(*args, **kwargs):
        calls.append(1)
        if len(calls) == 2:
            raise KeyboardInterrupt
        return real(*args, **kwargs)

    monkeypatch.setattr(mainfetch, "locate_object", locate)
    monkeypatch.setattr(mainfetch, "_close_check_session", lambda driver, ctx, last: closed.append((ctx.account, last)))
    res = main.cmd_check_photos(SEASON)

    assert (res["status"], _progress(res["run_dir"])["reason"]) == ("interrupted", "interrupted")
    assert len(_results(res["run_dir"])) == 1 and closed == [("tv", True)]
    assert "🛑 Stopped by user — closing Chrome; re-run the same command to continue." in capsys.readouterr().out


def test_a_held_lock_refuses_before_any_chrome_starts(cw, sandbox, monkeypatch, capsys):
    _demo_season(sandbox, cw.world)

    @contextlib.contextmanager
    def held():
        raise mvcommon.LockHeldError("held")
        yield  # pragma: no cover

    monkeypatch.setattr(mainfetch, "_check_lock", held)
    res = main.cmd_check_photos(SEASON)

    assert res["status"] == "refused" and cw.drivers == {} and _progress(res["run_dir"]) is None
    assert "holds the fetch session lock — nothing was checked" in capsys.readouterr().out


def test_the_real_check_lock_is_taken_without_waiting(monkeypatch, tmp_path):
    seen = []

    @contextlib.contextmanager
    def lock(blocking=True, **kwargs):
        seen.append(blocking)
        yield

    monkeypatch.setattr(mainfetch, "fetch_session_lock", lock)
    monkeypatch.setattr(mvcommon, "FETCH_SESSION_LOCK", str(tmp_path / "fetch_session.lock"))
    (tmp_path / "fetch_session.lock").write_text("1 2\n", encoding="utf-8")
    with mainfetch._check_lock() as heartbeat:
        from tools import gp_inventory
        assert isinstance(heartbeat, gp_inventory.Heartbeat) and heartbeat.path == str(tmp_path / "fetch_session.lock")
    assert seen == [False]                                                          # never waits for a running fetch


def test_a_lock_taken_over_makes_the_check_yield(cw, sandbox, capsys):
    _demo_season(sandbox, cw.world)
    cw.beat.lose_after = 1

    res = main.cmd_check_photos(SEASON)

    assert (res["status"], _progress(res["run_dir"])["reason"]) == ("stopped", "lock-lost")
    assert len(_results(res["run_dir"])) == 2 and "this check yields" in capsys.readouterr().out


def test_a_missing_profile_marks_its_objects_unchecked_and_the_run_still_completes(cw, sandbox, monkeypatch, capsys):
    _demo_season(sandbox, cw.world)
    monkeypatch.setitem(mainfetch.CHROME_PROFILES, "tv", str(sandbox["local_root"] / "NoSuchProfile"))

    res = main.cmd_check_photos(SEASON)

    assert res["status"] == "ok" and cw.drivers == {}
    assert {(l["outcome"], l["error"]) for l in _results(res["run_dir"])} == {("error", "no-profile")}
    assert "❌ The Chrome profile for 'tv' does not exist" in capsys.readouterr().out


class _Proc:
    """A launched Chrome's Popen handle: it runs until kill()."""

    def __init__(self, pid=4242):
        self.pid, self.killed, self.waited = pid, 0, []

    def poll(self):
        return 1 if self.killed else None

    def kill(self):
        self.killed += 1

    def wait(self, timeout=None):
        self.waited.append(timeout)
        return 1


def _launched_ctx(proc, launched=True):
    ctx = mainfetch.FetchContext()
    ctx.chrome = {"account": "tv", "proc": proc, "pid": proc.pid, "user_data_dir": r"Z:\profiles\tv",
                  "launched": launched}
    ctx.journal = types.SimpleNamespace(ends=[], end_session=lambda closed: ctx.journal.ends.append(closed))
    return ctx


@pytest.mark.parametrize("shared", ["left-it-running", "ctrl-c", "raised"])
def test_a_chrome_the_shared_close_left_running_is_ended_through_its_popen_handle(monkeypatch, capsys, shared):
    proc, marked = _Proc(), []
    ctx = _launched_ctx(proc)

    def close(driver, ctx, last):
        if shared == "ctrl-c":
            raise KeyboardInterrupt
        if shared == "raised":
            raise RuntimeError("the browser went away")

    monkeypatch.setattr(mainfetch, "_close_session", close)
    monkeypatch.setattr(mainfetch, "mark_profile_clean_exit", lambda path, **kw: marked.append((path, kw)))
    mainfetch._close_check_session(object(), ctx, last=True)

    assert proc.killed == 1 and proc.waited == [mainfetch.CHROME_GONE_WAIT_S]
    assert marked == [(r"Z:\profiles\tv", {"wait": mainfetch.CHROME_GONE_WAIT_S})] and ctx.journal.ends == ["terminated"]
    assert "Chrome ('tv') ended (pid 4242) — the usual close left it running." in capsys.readouterr().out


@pytest.mark.parametrize("launched", [False, None])
def test_a_chrome_the_check_only_attached_to_is_never_ended(monkeypatch, launched):
    proc = _Proc()
    monkeypatch.setattr(mainfetch, "_close_session", lambda driver, ctx, last: None)
    mainfetch._close_check_session(object(), _launched_ctx(proc, launched=launched), last=True)
    assert proc.killed == 0


def test_a_chrome_that_closed_is_left_alone(monkeypatch):
    proc = _Proc()
    proc.killed = 1                                                       # already gone: poll() answers
    monkeypatch.setattr(mainfetch, "_close_session", lambda driver, ctx, last: None)
    ctx = _launched_ctx(proc)
    mainfetch._close_check_session(object(), ctx, last=True)
    assert proc.killed == 1 and ctx.journal.ends == []


def test_every_session_is_closed_with_the_guarantee(cw, sandbox, monkeypatch):
    _demo_season(sandbox, cw.world)
    closed = []
    monkeypatch.setattr(mainfetch, "_close_check_session", lambda driver, ctx, last: closed.append(ctx.account))
    main.cmd_check_photos(SEASON)
    assert closed == ["tv"] and cw.drivers["tv"][0].quit_called


@pytest.mark.parametrize("argv, expected", [
    (["mainfetch.py", "check", "--run", r"Z:\run"], {"run_dir": r"Z:\run", "limit": None, "max_minutes": None,
                                                     "pace": None}),
    (["mainfetch.py", "check", "--run", "d", "--limit", "5", "--max-minutes", "15", "--pace", "0"],
     {"run_dir": "d", "limit": 5, "max_minutes": 15.0, "pace": 0.0}),
])
def test_the_check_command_line(argv, expected):
    assert mainfetch.parse_check_args(argv) == expected


@pytest.mark.parametrize("argv", [["mainfetch.py", "check"], ["mainfetch.py", "check", "--limit", "5"],
                                  ["mainfetch.py", "check", "--run", "d", "--limit", "0"],
                                  ["mainfetch.py", "check", "--run", "d", "--max-minutes", "0"],
                                  ["mainfetch.py", "check", "--run", "d", "--pace", "nan"],
                                  ["mainfetch.py", "check", "--run", "d", "--frob", "1"]])
def test_a_bad_check_command_line_prints_the_usage_and_exits_1(argv, capsys):
    with pytest.raises(SystemExit) as stop:
        mainfetch.parse_check_args(argv)
    assert stop.value.code == 1 and "Usage: check --run <run dir>" in capsys.readouterr().out


def test_the_check_child_exits_with_cmd_checks_code_and_its_output_can_never_stop_it(monkeypatch):
    seen = {}

    def check(**kwargs):
        seen.update(kwargs, stdout=sys.stdout)
        print("a line the parent can no longer read")
        return 3

    class Broken:
        def write(self, text):
            raise OSError(22, "Invalid argument")

        def flush(self):
            raise OSError(22, "Invalid argument")

    monkeypatch.setattr(mainfetch, "cmd_check", check)
    monkeypatch.setattr(sys, "stdout", Broken())
    monkeypatch.setattr(sys, "stderr", Broken())
    with pytest.raises(SystemExit) as stop:
        mainfetch.cli(["mainfetch.py", "check", "--run", "d"])
    assert stop.value.code == 3 and seen["run_dir"] == "d" and isinstance(seen["stdout"], mainfetch._SafeStream)


# ===========================================================================
# 13.e — RPC acceleration and the quality post-pass
# ===========================================================================
def _calls(world, rpcid):
    return sum(1 for r in world.rpc_ids_sent() if r == rpcid)


def test_new_pushes_are_confirmed_by_one_hash_lookup_batch(cw, sandbox):
    items = _season_of(sandbox, cw.world, {1: "pending", 2: "pending", 3: "pending"})

    res = main.cmd_check_photos("pending")

    lines = _results(res["run_dir"])
    assert [(l["outcome"], l["stage"], l["proof"], l["via"]) for l in lines] == [
        ("exactly-one", "s0_hash", "dedupkey", "rpc")] * 3
    assert [l["photo_id"] for l in lines] == [items[m].photo_id for m in sorted(items)]
    assert cw.world.rpc_ids_sent() == ["swbisb", "EWgK9e"]       # one hash batch + one name/quality batch for all three
    assert sorted(cw.world.rpc_log[0]["request"][0]) == sorted(items[m].hash_b64 for m in items)


def test_mapped_legacy_objects_are_confirmed_in_batches_and_keep_their_filename_proof(cw, sandbox):
    # A legacy object's local copy is a dummy: its block knows the cloud item's dedupKey only from the MAPPING. The hash
    # lookup under that key says the item still holds those bytes; the proof stays `filename` (P-5) — never dedupkey.
    items = _season_of(sandbox, cw.world, {1: "mapped", 2: "mapped", 3: "mapped"})

    res = main.cmd_check_photos(SEASON)

    lines = _results(res["run_dir"])
    assert [(l["outcome"], l["stage"], l["proof"], l["content_key"]) for l in lines] == [
        ("exactly-one", "s0_photo_id", "filename", False)] * 3
    assert [l["photo_id"] for l in lines] == [items[m].photo_id for m in sorted(items)]
    assert cw.world.rpc_ids_sent() == ["swbisb", "EWgK9e"]       # no per-object item info (VrseUb / fDcn4b)


def test_hash_lookups_go_in_batches_of_fifty(cw, sandbox):
    _season_of(sandbox, cw.world, {n: "learned" for n in range(1, 121)})

    res = main.cmd_check_photos(SEASON)

    assert res["status"] == "ok" and len(_results(res["run_dir"])) == 120
    assert _calls(cw.world, "swbisb") == 3                        # 50 + 50 + 20
    assert [len(r["request"][0]) for r in cw.world.rpc_log if r["rpcid"] == "swbisb"] == [50, 50, 20]
    assert _calls(cw.world, "VrseUb") == _calls(cw.world, "fDcn4b") == 0
    assert _calls(cw.world, "EzkLib") == 0                        # no search at all


def test_without_rpc_every_object_is_checked_on_the_web_pages(cw, sandbox):
    items = _season_of(sandbox, cw.world, {1: "learned", 2: "pending", 3: "legacy"})
    cw.world.wiz_token_missing["tv"] = True

    res = main.cmd_check_photos(SEASON)

    lines = _results(res["run_dir"])
    assert cw.world.rpc_log == []                                 # no page tokens: not one RPC was sent
    assert [(l["outcome"], l["via"]) for l in lines] == [("exactly-one", "ui"), ("exactly-one", "ui"),
                                                         ("exactly-one", "ui")]
    assert [l["proof"] for l in lines] == ["dedupkey", "dedupkey", "filename"]
    assert [l["photo_id"] for l in lines] == [items[m].photo_id for m in sorted(items)]
    assert _results(res["run_dir"], "quality") == []              # RPC off: quality unknown, nothing claimed


def test_a_failing_batch_leaves_the_objects_to_their_own_lookups(cw, sandbox, capsys):
    items = _season_of(sandbox, cw.world, {1: "learned", 2: "learned"})
    cw.world.fail_next_rpc("http500", count=4, rpcid="swbisb")   # the batch gives up after its retries

    res = main.cmd_check_photos(SEASON)

    lines = _results(res["run_dir"])
    assert [(l["outcome"], l["photo_id"]) for l in lines] == [("exactly-one", items[m].photo_id) for m in sorted(items)]
    assert _calls(cw.world, "VrseUb") == 2                        # each object asked on its own
    assert "A batched Google Photos call failed (RpcUnavailable)" in capsys.readouterr().out


def test_the_quality_of_every_pinned_item_is_recorded(cw, sandbox):
    items = _season_of(sandbox, cw.world, {1: "learned", 2: "pending", 3: "legacy"})
    items[f"{SEASON}e01"].partial_upload = True
    items[f"{SEASON}e02"].original_quality = False

    res = main.cmd_check_photos(SEASON)

    quality = {q["photo_id"]: {k: q.get(k) for k in ("original_quality", "partial_upload", "archived")}
               for q in _results(res["run_dir"], "quality")}
    assert quality == {
        items[f"{SEASON}e01"].photo_id: {"original_quality": True, "partial_upload": True, "archived": False},
        items[f"{SEASON}e02"].photo_id: {"original_quality": False, "partial_upload": False, "archived": False},
        items[f"{SEASON}e03"].photo_id: {"original_quality": True, "partial_upload": False, "archived": False}}
    assert {q["manual_id"] for q in _results(res["run_dir"], "quality")} == set(items)


def test_a_mapped_item_that_is_gone_is_reported_stale_and_its_reupload_is_found_by_name(cw, sandbox):
    items = _season_of(sandbox, cw.world, {1: "mapped"})
    old = items[f"{SEASON}e01"]
    old.deleted = True                                            # removed from Google Photos …
    new = cw.world.add_item("tv", old.filename, data=_bytes("re-upload"), taken="2026-09-01T00:00:00Z")   # … uploaded again

    res = main.cmd_check_photos(SEASON)

    [line] = _results(res["run_dir"])
    assert line["stale"] == [old.photo_id]                        # RPC and its own page agree: gone
    assert (line["outcome"], line["photo_id"], line["stage"], line["proof"]) == ("exactly-one", new.photo_id,
                                                                                 "s2_exact_name", "filename")


# ===========================================================================
# 13.f — the other accounts, and the hinted locators
# ===========================================================================
def _sessions(cw):
    return [(account, n) for account, drivers in cw.drivers.items() for n in range(len(drivers))]


def test_an_object_not_in_its_own_account_is_found_in_another_one(cw, sandbox, capsys):
    # The X-Files case before any override or learned home: routed by its prefix to tv, its item lives in movies.
    items = _season_of(sandbox, cw.world, {1: "legacy", 2: "legacy"}, in_account="movies")

    res = main.cmd_check_photos(SEASON)

    lines = _results(res["run_dir"])
    assert [(l["manual_id"], l["pass"], l["account"], l["outcome"]) for l in lines] == [
        (f"{SEASON}e01", "routed", "tv", "not-found"), (f"{SEASON}e02", "routed", "tv", "not-found"),
        (f"{SEASON}e01", "cross", "movies", "exactly-one"), (f"{SEASON}e02", "cross", "movies", "exactly-one")]
    assert [l["photo_id"] for l in lines[2:]] == [items[m].photo_id for m in sorted(items)]
    assert list(cw.drivers) == ["tv", "movies"]                    # found in the first other account: no further session
    assert res["status"] == "ok" and _progress(res["run_dir"])["state"] == "complete"
    assert "   🔎 ↪ in 'movies': tv-en-2015-demo-s01e01 · whole — exactly-one · S2 exact_name" in capsys.readouterr().out


def test_an_object_found_nowhere_is_looked_for_in_every_account_that_has_a_profile(cw, sandbox, monkeypatch):
    _season_of(sandbox, cw.world, {1: "missing"})
    monkeypatch.setitem(mainfetch.CHROME_PROFILES, "others", str(sandbox["local_root"] / "NoOthersProfile"))

    res = main.cmd_check_photos(SEASON)

    lines = _results(res["run_dir"])
    assert [(l["pass"], l["account"], l["outcome"]) for l in lines] == [
        ("routed", "tv", "not-found"), ("cross", "movies", "not-found"), ("cross", "anime", "not-found")]
    assert list(cw.drivers) == ["tv", "movies", "anime"]          # others: no profile, nothing launched
    assert res["status"] == "ok" and _progress(res["run_dir"])["state"] == "complete"


def test_a_recent_push_that_is_not_backed_up_yet_is_not_looked_for_elsewhere(cw, sandbox, monkeypatch):
    # P-19: the auto-confirm opens Chrome only for the accounts it needs — Step 9b: a push of the last week that Google
    # Photos does not show yet is simply not backed up yet.
    monkeypatch.setattr(mvcommon, "_now_utc", lambda: NOW)
    folder = sandbox["local_root"] / "Series" / "Demo" / "Season 01"
    folder.mkdir(parents=True)
    data = _bytes("not backed up")
    _capture(folder, "nb0nb0", [_obj(data, _up("Fresh.mkv", "nb0nb0"))])
    mvcommon.save_library({"tv-en-2026-fresh-s01e01": _whole(folder, "Fresh.mkv", data, "nb0nb0")})

    res = main.cmd_check_photos("pending")

    assert [(l["pass"], l["outcome"]) for l in _results(res["run_dir"])] == [("routed", "not-found")]
    assert list(cw.drivers) == ["tv"] and res["status"] == "ok"


def test_an_ambiguous_object_is_not_looked_for_elsewhere(cw, sandbox):
    items = _season_of(sandbox, cw.world, {1: "legacy"})
    cw.world.add_item("tv", items[f"{SEASON}e01"].filename, data=_bytes("another upload"), taken="2025-01-01T00:00:00Z")

    res = main.cmd_check_photos(SEASON)

    [line] = _results(res["run_dir"])
    assert (line["outcome"], line["rule"], line["found"]) == ("ambiguous", "newest-set", True)
    assert list(cw.drivers) == ["tv"]                             # its name IS in its account: no other account asked


def test_a_wrong_item_object_is_looked_for_elsewhere(cw, sandbox):
    # A new push whose name in its own account holds OTHER bytes (an old upload of another cut): its own bytes are in
    # movies — found there by its content key.
    folder = sandbox["local_root"] / "Series" / "Demo" / "Season 01"
    folder.mkdir(parents=True)
    data = _bytes("the real cut")
    _capture(folder, "wr0wr0", [_obj(data, _up("Cut.mkv", "wr0wr0"))])
    entry = _whole(folder, "Cut.mkv", data, "wr0wr0")
    mvcommon.save_library({"tv-en-2020-cut-s01e01": entry})
    cw.world.add_item("tv", _up("Cut.mkv", "wr0wr0"), data=_bytes("another cut"), taken=TAKEN)
    item = cw.world.add_item("movies", _up("Cut.mkv", "wr0wr0"), data=data, taken=TAKEN)

    res = main.cmd_check_photos("tv-en-2020-cut-s01e01")          # its capture is from 09-30: by NOW it is two weeks old

    lines = _results(res["run_dir"])
    assert [(l["pass"], l["account"], l["outcome"]) for l in lines] == [("routed", "tv", "wrong-item"),
                                                                         ("cross", "movies", "exactly-one")]
    assert (lines[1]["photo_id"], lines[1]["proof"]) == (item.photo_id, "dedupkey")


def test_routed_checks_go_round_the_accounts_before_the_look_elsewhere_starts_again_from_the_first(cw, sandbox):
    # movies-routed and anime-routed objects found nowhere: movies (its own), anime (its own + movies' object), then
    # from the first account again: movies (anime's object), tv (both), others (both).
    folder = sandbox["local_root"] / "Mixed"
    folder.mkdir(parents=True)
    mvcommon.save_library({"mov-en-2001-lost": _whole(folder, "Lost.mkv", _bytes("lost movie"), "lo5t01"),
                           "ani-ja-2002-lost01": _whole(folder, "Lost01.mkv", _bytes("lost anime"), "lo5t02")})

    res = main.cmd_check_photos("all")

    order = [(l["account"], l["manual_id"][:3]) for l in _results(res["run_dir"])]
    assert order == [("movies", "mov"), ("anime", "ani"), ("anime", "mov"), ("movies", "ani"), ("tv", "ani"),
                     ("tv", "mov"), ("others", "ani"), ("others", "mov")]   # within a session: the run's object order
    assert [a for a, drivers in cw.drivers.items() for _ in drivers] == ["movies", "movies", "anime", "tv", "others"]


def _hint(sandbox, obj_id, **hint):
    lib = mvcommon.load_library()
    [obj] = [o for o in mvcommon.iter_entry_objects(obj_id, lib[obj_id])]
    mvcommon.append_hint(dict({"manual_id": obj_id, "label": obj["label"], "sha256": obj["sha256"]}, **hint))


def test_a_url_hint_is_tried_first_and_verified_by_the_items_name(cw, sandbox):
    items = _season_of(sandbox, cw.world, {1: "legacy", 2: "legacy"})
    other = cw.world.add_item("tv", "Something.Else [zz9zz9].mkv", data=_bytes("else"), taken=TAKEN)
    _hint(sandbox, f"{SEASON}e01", account="tv", kind="url", photo_id=items[f"{SEASON}e01"].photo_id)
    _hint(sandbox, f"{SEASON}e02", account="tv", kind="url", photo_id=other.photo_id)    # the user pasted another item

    res = main.cmd_check_photos("hinted")

    lines = {l["manual_id"]: l for l in _results(res["run_dir"])}
    one, two = lines[f"{SEASON}e01"], lines[f"{SEASON}e02"]
    assert (one["stage"], one["outcome"], one["proof"], one["hint"]) == ("s0_photo_id", "exactly-one", "filename", True)
    assert two["per_approach"]["photo_id"] == {"outcome": "wrong-item", "n": 1}       # its name is not the object's
    assert (two["stage"], two["photo_id"]) == ("s2_exact_name", items[f"{SEASON}e02"].photo_id)   # the right one


def test_a_datetime_hint_gives_the_taken_second(cw, sandbox):
    items = _season_of(sandbox, cw.world, {1: "legacy"})
    item = items[f"{SEASON}e01"]
    _hint(sandbox, f"{SEASON}e01", account="tv", kind="datetime", taken_ms=item.taken_ms, gmt_offset_min=480,
          taken_local="2022-02-05T19:49:46")

    res = main.cmd_check_photos("hinted")

    [line] = _results(res["run_dir"])
    assert (line["stage"], line["outcome"], line["photo_id"], line["proof"]) == ("s1_taken_second", "exactly-one",
                                                                                 item.photo_id, "filename")


def test_a_hint_for_another_account_leaves_this_accounts_locator_alone(tmp_path):
    loc = {"photo_id": "AF1QipFAKElearned00000001", "alternates": [], "approach_best": None, "taken_ms": None}
    obj = {"hint": {"kind": "url", "account": "movies", "photo_id": "AF1QipFAKEhinted000000001"}}
    assert mainfetch._check_hinted(loc, obj, "tv") is loc
    hinted = mainfetch._check_hinted(loc, obj, "movies")
    assert (hinted["photo_id"], hinted["alternates"], hinted["approach_best"]) == (
        "AF1QipFAKEhinted000000001", ["AF1QipFAKElearned00000001"], "photo_id")
    assert loc["photo_id"] == "AF1QipFAKElearned00000001"           # the fetch's locator itself is never changed
