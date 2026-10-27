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
  13.g  the verdicts (confirmed · inconsistent · ambiguous · Step 9b not-backed-up-yet / possible-loss, --devices,
        partial sets · error · unchecked — a look elsewhere that errored / is unfinished / was ambiguous), report.txt
        (per account × category × approach), outliers.csv — every OD-7 class from a synthetic case — and the one
        summary line
  13.h  the apply: --apply (check, then record: learned records, stale records, the failed list — only an apply writes
        it —, the apply engine, failed_objects.csv, applied.json) and --apply-run <dir | latest> (a reviewed run, no
        Chrome; refused for another scope; latest = the newest complete run); idempotent; Ctrl+C applies nothing
  13.i  resumability end to end (errors retried, settled objects never re-checked, --recheck, a complete run followed
        by a fresh one, a killed child's torn line, objects that joined later, the Chrome a killed check left running);
        never a download (every request / arm / harvest path forbidden, RPC and web pages, best and all)
  13.j  --min-age-h (IMP-C25 acfix — the user's finding of 2026-10-27: the auto-confirm looked up the chunks pushed
        minutes earlier): only objects pushed at least H hours ago are looked up (the boundary, no push time = old, a
        push time in the future = young, every scope, after --account); a skipped object gets one line and nothing else
        (no lookup, no verdict row, no failed row, no learned record; all young -> no run directory); part of the resume
        key; without it nothing changes

Hermetic: the fake Google Photos world + its batchexecute emulator (tests/gphotos_fake.py); mainfetch's and gprpc's clocks
are fake (nothing waits); the autouse fixtures of tests/conftest.py redirect every state dir, ~/Downloads, the process
table and the Chrome profiles. No network, no browser, no adb, no real C:\\Media. Synthetic ids only (every photo id
contains "FAKE").
"""
import contextlib
import csv
import functools
import json
import os
import subprocess
import sys
import textwrap
import types
from datetime import datetime, timedelta, timezone

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
             "limit": None, "pace": None, "max_minutes": None, "min_age_h": None, "devices": False, "list_only": False}


@pytest.mark.parametrize("tokens, expected", [
    (["tv-en-2014-show-s02"], {"scope": "tv-en-2014-show-s02"}),
    (["all", "--approaches", "all", "--apply"], {"scope": "all", "approaches": "all", "apply": True}),
    (["account:tv", "--apply-run", "latest"], {"scope": "account:tv", "apply_run": "latest"}),
    (["account: anime "], {"scope": "account:anime"}),                             # one spelling: the run's key
    (["series:tv-en-1994-files-s02", "--account", "movies", "--recheck", "--limit", "20", "--pace", "0",
      "--max-minutes", "15", "--devices"],
     {"scope": "series:tv-en-1994-files-s02", "account": "movies", "recheck": True, "limit": 20, "pace": 0.0,
      "max_minutes": 15.0, "devices": True}),
    ([r"series:C:\Media\Series\Show (2020)", "--max-minutes", "0.5"],
     {"scope": r"series:C:\Media\Series\Show (2020)", "max_minutes": 0.5}),
    (["pending", "--list", "--account", "tv"], {"scope": "pending", "list_only": True, "account": "tv"}),
    (["--apply", "hinted"], {"scope": "hinted", "apply": True}),                   # options in any order
    (["pending", "--apply", "--min-age-h", "24", "--max-minutes", "15"],           # the auto-confirm's own line (acfix)
     {"scope": "pending", "apply": True, "min_age_h": 24.0, "max_minutes": 15.0}),
    (["all", "--min-age-h", " 0.5 "], {"scope": "all", "min_age_h": 0.5}),
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
    (["pending", "--min-age-h"], "--min-age-h needs a value"),                     # --min-age-h: a positive number
    (["pending", "--min-age-h", "--apply"], "--min-age-h needs a value"),
    (["pending", "--min-age-h", "0"], "--min-age-h must be above 0 (got 0)"),
    (["pending", "--min-age-h", "-3"], "--min-age-h must be above 0 (got -3)"),
    (["pending", "--min-age-h", "a day"], "--min-age-h needs a number (got 'a day')"),
    (["pending", "--min-age-h", "nan"], "--min-age-h must be above 0"),
    (["pending", "--min-age-h", "inf"], "--min-age-h must be above 0"),
    (["pending", "--apply-run", "latest", "--min-age-h", "24"],
     "--apply-run applies a finished run without searching: it takes no --limit, --pace, --max-minutes, --min-age-h"),
    (["pending", "--list", "--min-age-h", "24"], "pending --list is offline: it takes only --account"),
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


@pytest.mark.parametrize("value", ["0", "-1", "soon", "inf"])
def test_a_bad_min_age_exits_1_with_the_usage_line_and_checks_nothing(capsys, value):
    # IMP-C25 acfix: `--min-age-h <hours>` takes a positive number; anything else is a usage error (exit 1, nothing run)
    assert "[--min-age-h H]" in main.CHECK_USAGE
    with pytest.raises(SystemExit) as stop:
        _dispatch(["pending", "--apply", "--min-age-h", value])
    assert stop.value.code == 1
    out = capsys.readouterr().out.splitlines()
    assert out[0].startswith("❌ check_photos: --min-age-h ") and out[1] == f"   Usage: {main.CHECK_USAGE}"
    assert _dispatch(["pending", "--apply", "--min-age-h", "24"]) == [dict(_DEFAULTS, scope="pending", apply=True,
                                                                           min_age_h=24.0)]


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
    assert pending["objects"][0]["pushed_at"] == "2026-09-30T10:00:00Z"    # Step 9b's evidence (the child's rule)
    assert all(o["pushed_at"] is None for o in everything["objects"] if o["manual_id"] != "mov-en-2026-pushed")
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
                                "source", "recent_days")} == {
        "v": 1, "run": "check-20261001T000000Z", "created_at": "2026-10-01T00:00:00Z", "scope": "tv-en-1994-files-s02",
        "scope_text": "tv-en-1994-files-s02", "kind": "season", "account": None, "approaches": "best", "source": "check",
        "recent_days": main.MAP_RECENT_DAYS}
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


LATER = datetime(2026, 10, 14, 0, 0, 0, tzinfo=timezone.utc)   # two weeks after test_locator._capture's push


def _set_now(monkeypatch, when):
    """One clock for both halves: main's now (scope, run name, Step 9b) and the child's line times."""
    monkeypatch.setattr(mvcommon, "_now_utc", lambda: when)
    monkeypatch.setattr(mainfetch, "_utc_now", lambda: when.strftime("%Y-%m-%dT%H:%M:%SZ"))


@pytest.fixture()
def cw(fake_photos, monkeypatch):
    """A check on the fake world: init_driver hands out one fake driver per account session (no Chrome), the fetch lock
    is a stand-in with a counting heartbeat, no socket, no pacing jitter, both halves on one clock (LATER: a capture of
    test_locator._capture is two weeks old) — and main's child spawner runs the REAL `mainfetch.py check` command line
    in-process (parse_check_args + cmd_check)."""
    _set_now(monkeypatch, LATER)
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
    assert sorted(os.listdir(res["run_dir"])) == ["outliers.csv", "progress.json", "report.txt", "results.jsonl",
                                                  "run.json"]
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


def test_ctrl_c_while_chrome_launches_stops_right_after_and_closes_that_chrome(cw, sandbox, monkeypatch, capsys):
    # The new Chrome's Popen handle lives only inside init_driver: a Ctrl+C there would leave it running unrecorded.
    # The check holds Ctrl+C for the launch (and its record), then stops through the session's own close.
    import signal
    _demo_season(sandbox, cw.world)
    before, closed, launched = signal.getsignal(signal.SIGINT), [], []

    def init(profile_key="movies"):
        signal.raise_signal(signal.SIGINT)                          # Ctrl+C while Chrome settles
        launched.append(cw.world.driver(profile_key))
        return launched[-1]

    monkeypatch.setattr(mainfetch, "init_driver", init)
    monkeypatch.setattr(mainfetch, "_close_check_session", lambda driver, ctx, last: closed.append(
        (ctx.account, (ctx.chrome or {}).get("account"), last)))
    res = main.cmd_check_photos(SEASON)

    assert (res["status"], _progress(res["run_dir"])["reason"]) == ("interrupted", "interrupted")
    assert closed == [("tv", "tv", True)] and launched[0].quit_called and _results(res["run_dir"]) == []
    assert signal.getsignal(signal.SIGINT) is before                # Ctrl+C is the user's again
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
    _set_now(monkeypatch, NOW)
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


# ===========================================================================
# 13.g — the verdicts, the report, the outlier list
# ===========================================================================
ONE = "tv-en-2020-one-s01e01"


def _single(sandbox, mid=ONE, name="One.mkv", sid="on1on1", data=None, block=None, capture=None, folder="One"):
    """A library holding ONE whole-file object (its bytes `data`, default _bytes(mid)), optionally with a trusted block
    and a capture object (test_locator._capture: pushed 2026-09-30 10:00 Z). -> (mid, data)."""
    path = sandbox["local_root"] / "Series" / folder
    path.mkdir(parents=True, exist_ok=True)
    data = data if data is not None else _bytes(mid)
    if capture is not None:
        _capture(path, sid, [capture])
    mvcommon.save_library({mid: _whole(path, name, data, sid, block=block)})
    return mid, data


def _kinds(res):
    return [(row["manual_id"], kind) for row, kind, _detail in res["outliers"]]


def _outlier_csv(run_dir):
    with open(os.path.join(run_dir, "outliers.csv"), encoding="utf-8-sig", newline="") as fh:
        return list(csv.DictReader(fh))


def test_outlier_approaches_that_disagree(cw, sandbox):
    old = cw.world.add_item("tv", _up(SHOW, SID), data=_bytes("first upload"), taken=TAKEN)
    new = cw.world.add_item("tv", _up(SHOW, SID), data=_bytes("re-upload"), taken="2023-03-04T05:06:07Z")
    held = _bytes("library")
    _single(sandbox, EP, SHOW, SID, data=held, block=_learned(old, data=held, proof="filename", content_key=False))

    res = main.cmd_check_photos(EP, approaches="all")

    [row] = res["rows"]
    assert (row["status"], row["found"]["photo_id"]) == ("confirmed", old.photo_id)
    assert (EP, "approaches-disagree") in _kinds(res)
    [csv_row] = [r for r in _outlier_csv(res["run_dir"]) if r["outlier"] == "approaches-disagree"]
    assert new.photo_id in csv_row["detail"] and old.photo_id in csv_row["detail"]
    assert csv_row["photo_url"] == f"https://photos.google.com/photo/{old.photo_id}"


def test_outlier_only_one_approach_works(cw, sandbox):
    data = _bytes("renamed")
    cw.world.add_item("tv", "Renamed.Thing.2020.mkv", data=data, taken=TAKEN)        # uploaded under another name
    _single(sandbox, data=data, capture=_obj(data, _up("One.mkv", "on1on1"), date_utc=None))

    res = main.cmd_check_photos(ONE, approaches="all")

    [row] = res["rows"]
    assert row["status"] == "confirmed" and row["found"]["approach"] == "hash"
    assert (ONE, "single-approach") in _kinds(res)
    assert [d for _r, k, d in res["outliers"] if k == "single-approach"] == [
        "only hash pins it — exact_name, title_sid, legacy do not"]


def test_outlier_found_only_by_todays_legacy_queries(cw, sandbox, monkeypatch):
    data = _bytes("plain name")
    item = cw.world.add_item("tv", "One.mkv", data=data, taken=TAKEN)              # its old untagged name
    _single(sandbox, data=data, capture=_obj(data, _up("One.mkv", "on1on1"), date_utc=None))
    monkeypatch.setattr(gprpc, "hash_lookup", lambda driver, digests: {d: [] for d in digests})   # swbisb lags

    res = main.cmd_check_photos(ONE)

    [row] = res["rows"]
    assert (row["status"], row["found"]["approach"], row["found"]["photo_id"]) == ("confirmed", "legacy", item.photo_id)
    assert (ONE, "legacy-only") in _kinds(res)


def test_outlier_ambiguous_everywhere_and_its_duplicates(cw, sandbox):
    _single(sandbox)
    cw.world.add_item("tv", _up("One.mkv", "on1on1"), data=_bytes("cut a"), taken=TAKEN)
    cw.world.add_item("tv", _up("One.mkv", "on1on1"), data=_bytes("cut b"), taken="2024-01-01T00:00:00Z")

    res = main.cmd_check_photos(ONE)

    [row] = res["rows"]
    assert (row["status"], row["reason"]) == ("ambiguous", "ambiguous")
    assert _kinds(res) == [(ONE, "ambiguous-everywhere"), (ONE, "duplicates")]
    assert "several uploads under its name hold different bytes" in res["outliers"][1][2]


def test_outlier_account_mismatch_found_elsewhere_or_routed_by_an_override(cw, sandbox, monkeypatch):
    _season_of(sandbox, cw.world, {1: "legacy"}, in_account="movies")
    plain = main.cmd_check_photos(SEASON)
    monkeypatch.setattr(mvcommon, "_CONFIG_CACHE", {"fetch_account_overrides": {SEASON: "movies"}})
    overridden = main.cmd_check_photos(SEASON, recheck=True)

    for res in (plain, overridden):
        [row] = res["rows"]
        assert (row["status"], row["account"], row["home"], row["mismatch"]) == ("confirmed", "movies", "tv", True)
        assert (f"{SEASON}e01", "account-mismatch") in _kinds(res)
    assert [d for _r, k, d in overridden["outliers"] if k == "account-mismatch"] == [
        "found in 'movies', its home account is 'tv' (an mvconfig override routes it there)"]
    assert list(cw.drivers) == ["tv", "movies"]                      # the override's run went straight to movies


def test_outlier_duplicates_the_same_bytes_uploaded_twice(cw, sandbox):
    data = _bytes("twice")
    first = cw.world.add_item("tv", _up("One.mkv", "on1on1"), data=data, taken=TAKEN, upload_ms=1)
    second = cw.world.add_item("tv", _up("One.mkv", "on1on1"), data=data, taken=TAKEN, upload_ms=2)
    _single(sandbox, data=data, capture=_obj(data, _up("One.mkv", "on1on1")))

    res = main.cmd_check_photos(ONE)

    [row] = res["rows"]
    assert (row["status"], row["found"]["photo_id"], row["found"]["alternates"]) == (
        "confirmed", first.photo_id, [second.photo_id])          # the oldest upload, the other an alternate
    assert _kinds(res) == [(ONE, "duplicates")]


def test_outliers_partial_and_storage_saver_copies_are_inconsistent_never_confirmed(cw, sandbox):
    items = _season_of(sandbox, cw.world, {1: "learned", 2: "pending"})
    items[f"{SEASON}e01"].partial_upload = True
    items[f"{SEASON}e02"].original_quality = False

    res = main.cmd_check_photos(SEASON)

    assert [(r["status"], r["reason"]) for r in res["rows"]] == [("inconsistent", "inconsistent")] * 2
    assert _kinds(res) == [(f"{SEASON}e01", "partial-upload"), (f"{SEASON}e02", "not-original-quality")]


def test_outlier_cloud_taken_time_differs_from_the_files_date_utc(cw, sandbox):
    data = _bytes("shifted")
    cw.world.add_item("tv", _up("One.mkv", "on1on1"), data=data, taken="2022-02-05T11:49:50Z")
    _single(sandbox, data=data, capture=_obj(data, _up("One.mkv", "on1on1"), date_utc=TAKEN))

    res = main.cmd_check_photos(ONE)

    assert [d for _r, k, d in res["outliers"] if k == "taken-time-differs"] == [
        "Google Photos dates it 2022-02-05 11:49:50 UTC, the file's DateUTC is 2022-02-05 11:49:46 UTC"]


def test_outlier_a_stored_photo_id_that_is_gone(cw, sandbox):
    items = _season_of(sandbox, cw.world, {1: "mapped"})
    items[f"{SEASON}e01"].deleted = True

    res = main.cmd_check_photos(SEASON)

    assert (f"{SEASON}e01", "stale-photo-id") in _kinds(res)
    assert items[f"{SEASON}e01"].photo_id in [d for _r, k, d in res["outliers"] if k == "stale-photo-id"][0]


def test_the_report_states_verdicts_per_account_category_and_approach(cw, sandbox, monkeypatch, capsys):
    _set_now(monkeypatch, NOW)                                        # the pending push below is 14 h old
    items = _season_of(sandbox, cw.world, {1: "learned", 2: "legacy", 3: "missing", 4: "pending"})
    cw.world.items("tv")[-1].deleted = True                           # e04's push has not reached Google Photos yet

    res = main.cmd_check_photos(SEASON)

    out = capsys.readouterr().out
    run_dir = res["run_dir"]
    assert [r["status"] for r in res["rows"]] == ["confirmed", "confirmed", "possible-loss", "not-backed-up-yet"]
    assert [r["reason"] for r in res["rows"]] == [None, None, "not-found", None]
    with open(os.path.join(run_dir, "report.txt"), encoding="utf-8") as fh:
        report = fh.read()
    assert report in out                                              # the report printed is the report saved
    lines = report.splitlines()
    assert lines[0] == (f"=== CHECK PHOTOS (dry-run) — scope: {SEASON} · approaches best — 4 object(s) — run "
                        f"{os.path.basename(run_dir)} ===")
    assert lines[1] == "State: complete — 4 of 4 object(s) checked"
    assert lines[2] == "Verdicts: confirmed 2 · not-backed-up-yet 1 · possible-loss 1"
    assert "  tv/tv: 4 — confirmed 2 · not-backed-up-yet 1 · possible-loss 1" in lines
    assert lines[5:12] == [
        "Per account/category/approach (the look that decided each object):",
        "  tv/tv · photo_id: exactly-one 1",                              # e01 (S0a)
        "  tv/tv · hash: not-found 1",                                    # e04 (its push: not backed up yet)
        "  tv/tv · taken_second: wrong-item 1",                           # e04: its second holds the other episodes
        "  tv/tv · exact_name: exactly-one 1 · not-found 2",              # e02 found · e03, e04 not
        "  tv/tv · title_sid: not-found 2",
        "  tv/tv · legacy: not-found 1"]                                  # e04's preview of today's queries
    assert "  possible-loss: 1 — in no account, pushed over 7 days before it was checked or with no push time (no " \
           "capture) — possible LOSS" in lines
    assert f"    possible-loss        {SEASON}e03 · whole — push time unknown (no capture) · entry 4/5 in " \
           f"library_series.json" in lines
    assert "Not backed up yet (pushed in the last 7 days — not a problem yet): 1 object(s)" in lines
    assert "Failed list (P-18): 1 object(s) would go to the failed list with --apply / --apply-run — not-found 1" in lines
    assert lines[-2] == (f'Next: review this report and outliers.csv, then: python main.py check_photos {SEASON} '
                         f'--apply-run "{run_dir}"')
    assert lines[-1] == (f"Outputs: {run_dir} — report.txt, results.jsonl, outliers.csv, run.json, progress.json")
    assert out.rstrip().splitlines()[-1] == (f"🔎 check_photos {SEASON}: 4 object(s) — confirmed 2 · not-backed-up-yet 1 "
                                             f"· possible-loss 1")
    assert not any(marker in out for marker in WEB_MARKERS)
    with open(os.path.join(run_dir, "outliers.csv"), "rb") as fh:
        raw = fh.read()
    assert raw.startswith(b"\xef\xbb\xbf") and b"\r\n" in raw                  # Excel-friendly (BOM, CRLF)
    assert raw[3:].split(b"\r\n")[0].decode() == ",".join(main._CP_OUTLIER_COLUMNS)


def test_a_name_held_by_other_bytes_is_not_backed_up_yet_while_recent_and_a_loss_later(cw, sandbox, monkeypatch):
    # Step 9b (map's rule too): a recent push whose name is held by OTHER bytes — an older upload — is just not backed up
    # yet; two weeks later its own bytes are in no account: a possible loss, its failed-list reason wrong-item.
    data = _bytes("new cut")
    cw.world.add_item("tv", _up("One.mkv", "on1on1"), data=_bytes("old cut"), taken=TAKEN)
    _single(sandbox, data=data, capture=_obj(data, _up("One.mkv", "on1on1")))
    _set_now(monkeypatch, NOW)
    recent = main.cmd_check_photos(ONE)
    _set_now(monkeypatch, LATER)
    later = main.cmd_check_photos(ONE, recheck=True)

    assert [(r["status"], r["reason"]) for r in recent["rows"]] == [("not-backed-up-yet", None)]
    assert [(r["status"], r["reason"]) for r in later["rows"]] == [("possible-loss", "wrong-item")]
    assert "the search for its exact name finds only other bytes" in later["rows"][0]["note"]


def test_devices_refine_what_google_photos_does_not_show(cw, sandbox, monkeypatch, capsys):
    _season_of(sandbox, cw.world, {1: "missing", 2: "missing", 3: "missing"})
    phone = {"alias": "series", "serial": "FAKESER0002", "status": "ok", "detail": None, "root": "/sdcard/Media/",
             "stat_errors": 0, "files": [
                 {"path": "/sdcard/Media/X/Demo.S01E01 [de1de1].mkv", "name": "Demo.S01E01 [de1de1].mkv",
                  "size": None, "mtime": 1},
                 {"path": "/sdcard/Media/X/Demo.S01E02 [de2de2].mvmeta.json", "name": "Demo.S01E02 [de2de2].mvmeta.json",
                  "size": 300, "mtime": 1}]}
    monkeypatch.setattr(main, "device_inventory",
                        lambda aliases=None: {"complete": True, "unconfigured": [], "devices": {"series": phone}})

    res = main.cmd_check_photos(SEASON, devices=True)

    assert [(r["status"], r["reason"]) for r in res["rows"]] == [
        ("awaiting-backup", None), ("missing-after-backup", "missing-after-backup"),
        ("missing-everywhere", "missing-everywhere")]
    out = capsys.readouterr().out
    assert "Device check (read-only; DEVICE_ALIASES + DEVICE_CHECK_EXTRA_SERIALS): complete" in out
    assert "add --devices to see" not in out


def test_a_split_whose_last_chunk_is_in_no_account_is_a_partial_set(cw, sandbox):
    folder = sandbox["local_root"] / "Movies" / "Split"
    folder.mkdir(parents=True)
    datas = [_bytes(f"chunk {n}") for n in (1, 2, 3)]
    sid = "sp1it1"
    chunks = [{"filename": f"Split [{sid}].chunk.{n:03d}.mkv", "hash": _sha256(d)} for n, d in enumerate(datas, 1)]
    entry = {"filename": "Split.mkv", "short_id": sid, "folder_path": str(folder), "hash": _sha256(b"".join(datas)),
             "search_term": f"Split [{sid}].mkv", "status": "archived", "uploaded": True,
             "split_info": {"is_split": True, "total_chunks": 3, "chunks": chunks}}
    mvcommon.save_library({"mov-en-2020-split": entry})
    for chunk, data in list(zip(chunks, datas))[:2]:
        cw.world.add_item("movies", chunk["filename"], data=data, taken=TAKEN)

    res = main.cmd_check_photos("mov-en-2020-split")

    assert [(r["label"], r["status"], r["partial_set"], r["reason"]) for r in res["rows"]] == [
        ("chunk 1/3", "confirmed", False, None), ("chunk 2/3", "confirmed", False, None),
        ("chunk 3/3", "possible-loss", True, "partial-set")]


def test_an_unfinished_run_reports_what_is_unchecked_and_how_to_continue(cw, sandbox, capsys):
    _season_of(sandbox, cw.world, {1: "learned", 2: "learned", 3: "learned"})

    res = main.cmd_check_photos(SEASON, limit=1)

    out = capsys.readouterr().out
    assert res["status"] == "partial" and [r["status"] for r in res["rows"]] == ["confirmed", "unchecked", "unchecked"]
    assert "State: partial (limit) — 1 of 3 object(s) checked; re-run the same command to continue (--recheck starts " \
           "afresh)" in out
    assert "--apply-run" not in out                                   # nothing to review yet
    assert out.rstrip().splitlines()[-1].endswith("— partial: re-run the same command to continue")


def test_objects_that_could_not_be_checked_are_reported_never_failed(cw, sandbox, monkeypatch, capsys):
    _season_of(sandbox, cw.world, {1: "learned", 2: "learned"})
    real = mainfetch.locate_object

    def flaky(driver, loc, ctx, *args, **kwargs):
        if loc["manual_id"].endswith("e02"):
            raise RuntimeError("the page did not render")
        return real(driver, loc, ctx, *args, **kwargs)

    monkeypatch.setattr(mainfetch, "locate_object", flaky)
    res = main.cmd_check_photos(SEASON)

    assert [(r["status"], r["reason"]) for r in res["rows"]] == [("confirmed", None), ("error", None)]
    out = capsys.readouterr().out
    assert "Not checked: 1 object(s) — RuntimeError 1 (check them again by id: python main.py check_photos <id>)" in out
    assert f"    {SEASON}e02 · whole — RuntimeError" in out          # the run is complete: the ids to re-check


def test_a_failed_look_elsewhere_is_no_loss_and_never_failed(cw, sandbox, monkeypatch, capsys):
    # "In no account" needs every account's look: one that errored says nothing — the object is not called lost.
    _season_of(sandbox, cw.world, {1: "missing"})
    real = mainfetch.locate_object

    def flaky(driver, loc, ctx, *args, **kwargs):
        if ctx.account == "anime":
            raise RuntimeError("the page did not render")
        return real(driver, loc, ctx, *args, **kwargs)

    monkeypatch.setattr(mainfetch, "locate_object", flaky)
    res = main.cmd_check_photos(SEASON)

    assert [(l["pass"], l["account"], l["outcome"]) for l in _results(res["run_dir"])] == [
        ("routed", "tv", "not-found"), ("cross", "movies", "not-found"), ("cross", "anime", "error"),
        ("cross", "others", "not-found")]
    [row] = res["rows"]
    assert (row["status"], row["error"], row["reason"]) == ("error", "RuntimeError in 'anime'", None)
    assert "Not checked: 1 object(s) — RuntimeError in 'anime' 1" in capsys.readouterr().out


def test_an_ambiguous_look_elsewhere_makes_it_ambiguous_in_that_account(cw, sandbox):
    # Not in its own account; in movies its exact name holds two uploads of different bytes — the object IS there, its
    # copy just cannot be told apart: ambiguous in movies (its failed row names movies and the candidates seen there).
    _season_of(sandbox, cw.world, {1: "missing"})
    name = _up("Demo.S01E01.mkv", "de1de1")
    first = cw.world.add_item("movies", name, data=_bytes("cut a"), taken=TAKEN)
    second = cw.world.add_item("movies", name, data=_bytes("cut b"), taken="2024-01-01T00:00:00Z")

    res = main.cmd_check_photos(SEASON)

    [row] = res["rows"]
    assert (row["status"], row["account"], row["reason"]) == ("ambiguous", "movies", "ambiguous")
    assert row["decisive"]["pass"] == "cross" and set(row["decisive"]["candidates"]) == {first.photo_id,
                                                                                        second.photo_id}


# ===========================================================================
# 13.h — the apply: --apply (check, then apply) and --apply-run (a reviewed run, no browser)
# ===========================================================================
def _entry_block(mid):
    return mvcommon.load_library()[mid].get("gphotos")


def _found_record(account, mid):
    """The learned record of `mid` in `account` that names a photo_id (the identity), or None."""
    return next((r for r in mvcommon.read_learned(account) if r["manual_id"] == mid and "photo_id" in r
                 and not r.get("stale")), None)


def test_apply_records_what_the_check_proved_and_the_engine_confirms_it(cw, sandbox, capsys):
    items = _season_of(sandbox, cw.world, {1: "learned", 2: "mapped", 3: "legacy", 4: "missing"})

    res = main.cmd_check_photos(SEASON, apply=True)

    out = capsys.readouterr().out
    run_dir, run = res["run_dir"], f"check-{os.path.basename(res['run_dir'])}"
    assert res["status"] == "ok" and [r["status"] for r in res["rows"]] == ["confirmed"] * 3 + ["possible-loss"]
    # the learned records (P-10): the found item, its proof (P-5), the stage that pinned it, source check
    expected = {f"{SEASON}e01": ("dedupkey", "s0_photo_id"), f"{SEASON}e02": ("filename", "s0_photo_id"),
                f"{SEASON}e03": ("filename", "s2_exact_name")}
    for mid, (proof, stage) in expected.items():
        rec = _found_record("tv", mid)
        item = items[mid]
        assert (rec["photo_id"], rec["proof"], rec["source"], rec["approach"]["tried"], rec["approach"]["outcome"]) == (
            item.photo_id, proof, "check", stage, "exactly-one")
        assert (rec["at"], rec["upload_ms"], rec["dedup_key"], rec["gp_filename"]) == (
            "2026-10-14T00:00:00Z", item.upload_ms, item.dedup_key, item.filename)
        assert rec["original_quality"] is True and rec["partial_upload"] is False     # the quality post-pass
        # the apply engine — the only writer of gphotos blocks — confirms each one
        acc = _entry_block(mid)["accounts"]["tv"]
        assert (acc["photo_id"], acc["verified_by"], acc["source"], acc["confirmed"]) == (item.photo_id, proof, "check",
                                                                                          True)
    assert mvcommon.read_learned("movies") == mvcommon.read_learned("anime") == mvcommon.read_learned("others") == []
    # P-5: a mapped legacy object's cloud dedupKey stays an item fact — never the block's content key (IMP-E5)
    for mid in (f"{SEASON}e02", f"{SEASON}e03"):
        block = _entry_block(mid)
        assert "sha1" not in block and "dedup_key" not in block and block["accounts"]["tv"]["verified_by"] == "filename"
    assert _entry_block(f"{SEASON}e04") is None                     # an unconfirmed object gets no block
    # the failed list (P-18): only the object in no account
    [failed] = mvcommon.read_failed()
    assert {k: failed.get(k) for k in ("run", "account", "category", "manual_id", "label", "reason")} == {
        "run": run, "account": "tv", "category": "tv", "manual_id": f"{SEASON}e04", "label": "whole",
        "reason": res["rows"][3]["reason"]}
    assert failed["tried"] == [{"approach": a, "outcome": v["outcome"], "n": v["n"]}
                               for a, v in res["rows"][3]["routed_line"]["per_approach"].items()]
    with open(mvcommon.failed_csv_path(), encoding="utf-8-sig", newline="") as fh:
        assert [(r["manual_id"], r["status"]) for r in csv.DictReader(fh)] == [(f"{SEASON}e04", failed["reason"])]
    assert main._cp_read_json(os.path.join(run_dir, "applied.json")) == {
        "v": 1, "at": "2026-10-14T00:00:00Z", "learned": {"new": 3, "known": 0}, "failed": {"new": 1, "known": 0},
        "engine": {"status": "saved", "changed": 3, "confirmed": 3, "conflicts": 0, "orphans": 0}}
    with open(os.path.join(run_dir, "report.txt"), encoding="utf-8") as fh:
        report = fh.read().splitlines()
    assert report[0].startswith(f"=== CHECK PHOTOS (APPLY) — scope: {SEASON} · approaches best — 4 object(s)")
    assert "Recorded: learned records 3 new (0 already on record) · failed list 1 new (0 already listed)" in report
    assert "  library (saved): 3 object(s) updated (3 newly confirmed) · conflict(s) not applied 0 · orphan(s) 0" in report
    assert "  failed_objects.csv: regenerated — 1 row(s)" in report
    assert f"Failed list (P-18): 1 object(s) on the failed list — {failed['reason']} 1" in report
    assert report[-1] == (f"Outputs: {run_dir} — report.txt, results.jsonl, outliers.csv, run.json, progress.json, "
                          f"applied.json")
    assert out.rstrip().splitlines()[-1] == (f"🔎 check_photos {SEASON}: 4 object(s) — confirmed 3 · possible-loss 1 · "
                                             f"applied: 3 object(s) updated, 3 newly confirmed")
    assert cw.world.downloads == [] and "pLFTfd" not in cw.world.rpc_ids_sent()      # still download-free
    assert not any(item.photo_id in out for item in items.values() if item)       # P-15


def test_a_reviewed_dry_run_is_applied_by_apply_run_without_a_browser_and_only_once(cw, sandbox, capsys):
    _season_of(sandbox, cw.world, {1: "learned", 2: "missing"})
    dry = main.cmd_check_photos(SEASON)
    assert mvcommon.read_learned() == [] and mvcommon.read_failed() == []          # a dry run records nothing
    sessions, spawned = _sessions(cw), list(cw.spawned)
    next_line = [l for l in capsys.readouterr().out.splitlines() if l.startswith("Next: ")]
    assert next_line == [f'Next: review this report and outliers.csv, then: python main.py check_photos {SEASON} '
                         f'--apply-run "{dry["run_dir"]}"']

    latest = main.cmd_check_photos(SEASON, apply_run="latest")
    again = main.cmd_check_photos(SEASON, apply_run=dry["run_dir"])               # the same run, by its directory

    out = capsys.readouterr().out
    assert _sessions(cw) == sessions and cw.spawned == spawned                     # no search, no Chrome, no child
    assert latest["status"] == again["status"] == "ok" and latest["run_dir"] == again["run_dir"] == dry["run_dir"]
    assert [(r["manual_id"], r["source"]) for r in mvcommon.read_learned()] == [(f"{SEASON}e01", "check")]
    assert [r["manual_id"] for r in mvcommon.read_failed()] == [f"{SEASON}e02"]     # appended once
    assert _entry_block(f"{SEASON}e01")["accounts"]["tv"]["confirmed"] is True
    assert (latest["applied"]["learned"], latest["applied"]["failed"]) == ((1, 0), (1, 0))
    assert (again["applied"]["learned"], again["applied"]["failed"]) == ((0, 1), (0, 1))   # nothing twice
    assert again["applied"]["engine"]["status"] == "up-to-date"
    assert f"=== CHECK PHOTOS (APPLY-RUN latest) — scope: {SEASON} — no search, no Chrome ===" in out
    assert out.rstrip().splitlines()[-1].endswith("· applied: 0 object(s) updated, 0 newly confirmed")
    with open(os.path.join(dry["run_dir"], "report.txt"), encoding="utf-8") as fh:
        assert fh.readline().startswith(f"=== CHECK PHOTOS (APPLY) — scope: {SEASON}")    # rewritten with the apply


def test_apply_run_refuses_another_scope_account_or_approaches_and_a_non_run_directory(cw, sandbox, capsys, tmp_path):
    _season_of(sandbox, cw.world, {1: "learned", 2: "learned"})
    dry = main.cmd_check_photos(SEASON)
    capsys.readouterr()

    item = main.cmd_check_photos(f"{SEASON}e01", apply_run=dry["run_dir"])
    account = main.cmd_check_photos(SEASON, account="movies", apply_run=dry["run_dir"])
    approaches = main.cmd_check_photos(SEASON, approaches="all", apply_run=dry["run_dir"])
    stranger = main.cmd_check_photos(SEASON, apply_run=str(tmp_path))

    out = capsys.readouterr().out
    assert [r["status"] for r in (item, account, approaches, stranger)] == ["error"] * 4
    assert f"❌ check_photos: refused — that run checked scope '{SEASON}' · approaches best, not '{SEASON}e01'. Apply " \
           f"it with its own scope." in out
    assert f"not '{SEASON}' · account movies." in out and f"not '{SEASON}' · approaches all." in out
    assert f"❌ check_photos: {tmp_path} is not a check_photos run directory (no readable run.json)." in out
    assert mvcommon.read_learned() == [] and mvcommon.read_failed() == []
    assert all(_entry_block(m)["accounts"]["tv"].get("confirmed") is None for m in (f"{SEASON}e01", f"{SEASON}e02"))


def test_apply_run_latest_needs_a_completed_run_and_an_unfinished_one_is_applied_by_its_directory(cw, sandbox, capsys):
    _season_of(sandbox, cw.world, {1: "learned", 2: "missing", 3: "learned"})
    none = main.cmd_check_photos(SEASON, apply_run="latest")
    unfinished = main.cmd_check_photos(SEASON, limit=2)          # e01 and e02 checked in tv, e02's look elsewhere too
    latest = main.cmd_check_photos(SEASON, apply_run="latest")
    capsys.readouterr()
    cw.world.session["movies"] = "expired"
    stopped = main.cmd_check_photos(SEASON, recheck=True)         # e02 not in tv; movies signed out: its look stops
    capsys.readouterr()

    applied = main.cmd_check_photos(SEASON, apply_run=stopped["run_dir"])

    out = capsys.readouterr().out
    assert none["status"] == latest["status"] == "error" and unfinished["status"] == "partial"
    assert stopped["status"] == "stopped" and _progress(stopped["run_dir"])["reason"] == "signed-out"
    assert applied["status"] == "ok"
    assert "⚠️  That run is unfinished (signed-out) — what it settled is applied; the objects it has not finished stay " \
           "unchecked." in out
    assert [(r["manual_id"], r["status"]) for r in applied["rows"]] == [
        (f"{SEASON}e01", "confirmed"), (f"{SEASON}e02", "unchecked"), (f"{SEASON}e03", "confirmed")]
    assert "the run stopped before it was looked for in: movies, anime, others" in applied["rows"][1]["note"]
    assert sorted(r["manual_id"] for r in mvcommon.read_learned()) == [f"{SEASON}e01", f"{SEASON}e03"]
    assert mvcommon.read_failed() == []                          # e02's verdict is not final: no failed row
    assert f"continue the check with: python main.py check_photos {SEASON}" in out
    assert out.rstrip().splitlines()[-1].startswith(f"🔎 check_photos {SEASON}: 3 object(s) — confirmed 2 · unchecked 1 "
                                                    f"— the run is unfinished: continue it with python main.py "
                                                    f"check_photos {SEASON} · applied:")


def test_apply_run_latest_names_the_unfinished_run_to_continue(cw, sandbox, capsys):
    _season_of(sandbox, cw.world, {1: "learned", 2: "learned"})
    first = main.cmd_check_photos(SEASON, limit=1)
    capsys.readouterr()

    res = main.cmd_check_photos(SEASON, apply_run="latest")

    assert res == {"status": "error", "run_dir": None}
    assert (f"❌ check_photos: no completed run of scope '{SEASON}' — its newest run {first['run_dir']} is unfinished: "
            f"continue it with python main.py check_photos {SEASON}, or apply it by naming that directory.") in \
        capsys.readouterr().out


def test_no_run_at_all_tells_how_to_make_one(cw, sandbox, capsys):
    _season_of(sandbox, cw.world, {1: "learned"})
    assert main.cmd_check_photos(SEASON, account="tv", apply_run="latest")["status"] == "error"
    assert (f"❌ check_photos: no completed run of scope '{SEASON}' · account tv — run `python main.py check_photos "
            f"{SEASON} --account tv` first.") in capsys.readouterr().out


def test_pending_apply_confirms_the_new_pushes_and_empties_the_pending_list(cw, sandbox, capsys):
    # PLAN P-11 / Step 13b: `check_photos pending --apply` is the post-backup confirmation (source confirm).
    items = _season_of(sandbox, cw.world, {1: "pending", 2: "pending"})
    assert len(main._cp_pending_list()["objects"]) == 2

    res = main.cmd_check_photos("pending", apply=True)

    assert res["status"] == "ok" and [r["status"] for r in res["rows"]] == ["confirmed", "confirmed"]
    for mid, item in items.items():
        rec = _found_record("tv", mid)
        assert (rec["source"], rec["proof"], rec["approach"]["tried"], rec["photo_id"]) == ("confirm", "dedupkey",
                                                                                          "s0_hash", item.photo_id)
        block = _entry_block(mid)
        assert block["accounts"]["tv"]["confirmed"] is True and block["accounts"]["tv"]["source"] == "confirm"
        assert block["dedup_key"] == item.dedup_key              # the capture's content key, proven by the push
    capsys.readouterr()
    assert main._cp_pending_list()["objects"] == []
    assert "  Nothing pending." in capsys.readouterr().out


def test_an_object_found_in_another_account_is_recorded_there_and_routed_there_next(cw, sandbox):
    items = _season_of(sandbox, cw.world, {1: "legacy"}, in_account="movies")

    res = main.cmd_check_photos(SEASON, apply=True)

    [row] = res["rows"]
    assert (row["status"], row["account"], row["mismatch"]) == ("confirmed", "movies", True)
    assert mvcommon.read_learned("tv") == [] and _found_record("movies", f"{SEASON}e01")["photo_id"] == items[
        f"{SEASON}e01"].photo_id
    block = _entry_block(f"{SEASON}e01")
    assert list(block["accounts"]) == ["movies"] and block["accounts"]["movies"]["confirmed"] is True
    assert block["home_account"] == "movies"                     # P-4: where its verified copy is — fetch goes there
    [obj] = main._cp_resolve(mvcommon.load_library(), SEASON, None, LATER)["objects"]
    assert (obj["account"], obj["route"]) == ("movies", "learned")


def test_a_gone_photo_id_is_recorded_stale_and_the_reupload_becomes_the_identity(cw, sandbox):
    items = _season_of(sandbox, cw.world, {1: "mapped"})
    old = items[f"{SEASON}e01"]
    old.deleted = True
    new = cw.world.add_item("tv", old.filename, data=_bytes("re-upload"), taken="2026-09-01T00:00:00Z")

    main.cmd_check_photos(SEASON, apply=True)

    stale = [r for r in mvcommon.read_learned("tv") if r.get("stale")]
    assert [(r["photo_id"], r["approach"]) for r in stale] == [
        (old.photo_id, {"tried": "s0_photo_id", "outcome": "not-found", "n": 0, "via": "rpc"})]
    acc = _entry_block(f"{SEASON}e01")["accounts"]["tv"]
    assert (acc["photo_id"], acc["verified_by"], acc["confirmed"]) == (new.photo_id, "filename", True)
    assert old.photo_id not in (acc.get("alternates") or [])     # a dead id is never an alternate


def test_an_inconsistent_copy_is_never_confirmed_and_goes_to_the_failed_list(cw, sandbox, capsys):
    items = _season_of(sandbox, cw.world, {1: "learned", 2: "pending"})
    items[f"{SEASON}e01"].partial_upload = True
    items[f"{SEASON}e02"].original_quality = False
    before = _entry_block(f"{SEASON}e01")

    res = main.cmd_check_photos(SEASON, apply=True)

    assert [r["status"] for r in res["rows"]] == ["inconsistent"] * 2
    assert res["status"] == "ok" and res["applied"]["engine"]["status"] == "nothing-to-apply"
    assert capsys.readouterr().out.rstrip().splitlines()[-1].endswith("· applied: 0 object(s) updated, 0 newly confirmed")
    assert mvcommon.read_learned() == []                         # no identity is recorded for a bad copy
    assert _entry_block(f"{SEASON}e01") == before and _entry_block(f"{SEASON}e02") is None   # never confirmed
    failed = {r["manual_id"]: r for r in mvcommon.read_failed()}
    assert {m: (r["reason"], r["candidates"][0]) for m, r in failed.items()} == {
        m: ("inconsistent", items[m].photo_id) for m in items}  # the pinned item first: the user sees which copy


def test_an_interrupted_apply_records_nothing_and_its_rerun_applies_the_whole_run(cw, sandbox, monkeypatch, capsys):
    _demo_season(sandbox, cw.world)
    real, calls = mainfetch.locate_object, []

    def locate(*args, **kwargs):
        calls.append(1)
        if len(calls) == 2:
            raise KeyboardInterrupt
        return real(*args, **kwargs)

    monkeypatch.setattr(mainfetch, "locate_object", locate)
    stopped = main.cmd_check_photos(SEASON, apply=True)
    out = capsys.readouterr().out
    monkeypatch.setattr(mainfetch, "locate_object", real)
    resumed = main.cmd_check_photos(SEASON, apply=True)

    assert stopped["status"] == "interrupted" and stopped["applied"] is None
    assert "⏸️ Interrupted — nothing was applied; re-run the same command to continue (it applies at its end)." in out
    assert "Recorded:" not in out and "applied:" not in out.rstrip().splitlines()[-1]
    assert resumed["run_dir"] == stopped["run_dir"] and resumed["status"] == "ok"
    assert sorted(r["manual_id"] for r in mvcommon.read_learned()) == [f"{SEASON}e01", f"{SEASON}e02", f"{SEASON}e03"]
    assert resumed["applied"]["learned"] == (3, 0)               # the first invocation's object included


def test_an_apply_bounded_by_max_minutes_records_what_it_settled_and_the_rerun_the_rest(cw, sandbox, clock):
    # Step 13b's call (`check_photos pending --apply --min-age-h 24 --max-minutes 15` since the acfix) is bounded — a
    # bounded run still applies what it settled.
    _demo_season(sandbox, cw.world)
    first = main.cmd_check_photos(SEASON, apply=True, max_minutes=0.02, pace=0.4)
    assert (first["status"], first["applied"]["learned"]) == ("partial", (2, 0))
    assert sorted(r["manual_id"] for r in mvcommon.read_learned()) == [f"{SEASON}e01", f"{SEASON}e02"]

    second = main.cmd_check_photos(SEASON, apply=True)

    assert second["run_dir"] == first["run_dir"] and second["status"] == "ok"
    assert second["applied"]["learned"] == (1, 2)                # e03 new; e01 and e02 already on record
    assert all(_entry_block(m)["accounts"]["tv"]["confirmed"] for m in (f"{SEASON}e01", f"{SEASON}e02", f"{SEASON}e03"))


def test_approaches_all_records_every_stage_verdict(cw, sandbox):
    # PLAN Step 13: `--approaches all` evaluates every approach and records approach.ok / approach.failed.
    data = _bytes("renamed")
    item = cw.world.add_item("tv", "Renamed.Thing.2020.mkv", data=data, taken=TAKEN)        # uploaded under another name
    _single(sandbox, data=data, capture=_obj(data, _up("One.mkv", "on1on1"), date_utc=None))

    res = main.cmd_check_photos(ONE, approaches="all", apply=True)

    [row] = res["rows"]
    assert row["status"] == "confirmed" and list(row["found"]["per_approach"]) == ["hash", "exact_name", "title_sid",
                                                                                   "legacy"]
    records = mvcommon.read_learned("tv")
    assert [(r["approach"]["tried"], r["approach"]["outcome"], "photo_id" in r) for r in records] == [
        ("s0_hash", "exactly-one", True), ("s2_exact_name", "not-found", False), ("s3_title_sid", "not-found", False),
        ("s4_legacy", "not-found", False)]
    acc = _entry_block(ONE)["accounts"]["tv"]
    assert (acc["photo_id"], acc["confirmed"]) == (item.photo_id, True)
    assert {k: acc["approach"][k] for k in ("best", "ok", "failed")} == {
        "best": "hash", "ok": ["hash"], "failed": ["exact_name", "title_sid", "legacy"]}


def test_stages_that_cannot_tell_the_uploads_apart_are_recorded_failed(cw, sandbox):
    # The approaches-disagree case: the stored photo_id and the taken second pin the first upload; a name search sees a
    # re-upload of other bytes too and cannot tell them apart (ambiguous — its tentative pick, the newest, is the other
    # item). True verdicts all: photo_id / taken_second ok, exact_name / title_sid failed.
    old = cw.world.add_item("tv", _up(SHOW, SID), data=_bytes("first upload"), taken=TAKEN)
    new = cw.world.add_item("tv", _up(SHOW, SID), data=_bytes("re-upload"), taken="2023-03-04T05:06:07Z")
    held = _bytes("library")
    _single(sandbox, EP, SHOW, SID, data=held, block=_learned(old, data=held, proof="filename", content_key=False))

    res = main.cmd_check_photos(EP, approaches="all", apply=True)

    [row] = res["rows"]
    assert row["found"]["per_approach_pick"] == {"photo_id": old.photo_id, "taken_second": old.photo_id,
                                                 "exact_name": new.photo_id, "title_sid": new.photo_id}
    assert [(r["approach"]["tried"], r["approach"]["outcome"], r.get("photo_id")) for r in mvcommon.read_learned()] == [
        ("s0_photo_id", "exactly-one", old.photo_id), ("s1_taken_second", "exactly-one", None),
        ("s2_exact_name", "ambiguous", None), ("s3_title_sid", "ambiguous", None)]
    acc = _entry_block(EP)["accounts"]["tv"]
    assert (acc["photo_id"], acc["approach"]["ok"], acc["approach"]["failed"]) == (
        old.photo_id, ["photo_id", "taken_second"], ["exact_name", "title_sid"])


def test_a_stage_that_pinned_another_item_makes_no_ok_claim():
    # An exactly-one verdict naming ANOTHER item than the one recorded (approaches disagree) would be a contradicting
    # `approach.ok`: it is left out — the user reviews the outlier. A same-bytes copy (an alternate) agrees.
    old, new, copy_ = ("AF1QipFAKEold" + "0" * 25), ("AF1QipFAKEnew" + "0" * 25), ("AF1QipFAKEcpy" + "0" * 25)
    found = {"account": "tv", "at": "2026-10-14T00:00:00Z", "photo_id": old, "proof": "filename",
             "stage": "s0_photo_id", "approach": "photo_id", "n": 1, "via": "rpc", "alternates": [copy_],
             "record": {"photo_id": old, "upload_ms": 1, "filename": "x.mkv"},
             "per_approach": {"photo_id": {"outcome": "exactly-one", "n": 1},
                              "taken_second": {"outcome": "exactly-one", "n": 1},
                              "exact_name": {"outcome": "exactly-one", "n": 1},
                              "title_sid": {"outcome": "error", "n": 0}},
             "per_approach_via": {"photo_id": "rpc", "taken_second": "rpc", "exact_name": "ui", "title_sid": "ui"},
             "per_approach_pick": {"photo_id": old, "taken_second": new, "exact_name": copy_, "title_sid": None}}
    row = {"manual_id": EP, "label": "whole", "sha256": "a" * 64, "status": "confirmed", "found": found,
           "quality": {}, "routed_line": found, "cross": {}}

    records = main._cp_records({"source": "check"}, [row])

    assert [(r["approach"]["tried"], r["approach"]["outcome"], r["approach"].get("via")) for r in records] == [
        ("s0_photo_id", "exactly-one", "rpc"), ("s2_exact_name", "exactly-one", "ui")]


def test_the_look_elsewhere_is_decided_when_its_account_is_checked_not_when_the_run_began(cw, sandbox, monkeypatch):
    # Step 9b measured at the check (A2): e02 was a 14 h old push when the run began, but the run reached it two weeks
    # later — then it is no longer "not backed up yet": it is looked for in the other accounts before it is called lost.
    _set_now(monkeypatch, NOW)
    items = _season_of(sandbox, cw.world, {1: "learned", 2: "pending"})
    items[f"{SEASON}e02"].deleted = True
    first = main.cmd_check_photos(SEASON, limit=1)
    _set_now(monkeypatch, LATER)

    second = main.cmd_check_photos(SEASON)

    assert second["run_dir"] == first["run_dir"] and second["status"] == "ok"
    lines = [(l["manual_id"][-3:], l["pass"], l["account"]) for l in _results(second["run_dir"])]
    assert lines == [("e01", "routed", "tv"), ("e02", "routed", "tv"), ("e02", "cross", "movies"),
                     ("e02", "cross", "anime"), ("e02", "cross", "others")]
    assert [r["status"] for r in second["rows"]] == ["confirmed", "possible-loss"]


@pytest.mark.parametrize("trouble", ["sidecar", "engine"])
def test_an_apply_that_cannot_record_fails_and_says_so(cw, sandbox, monkeypatch, capsys, trouble):
    _season_of(sandbox, cw.world, {1: "learned"})
    before = sandbox["lib_series"].read_bytes()
    if trouble == "sidecar":
        def refuse(account, record):
            raise OSError(28, "No space left on device")
        monkeypatch.setattr(mvcommon, "append_learned", refuse)
    else:
        monkeypatch.setattr(main, "gphotos_apply", lambda *a, **k: {
            "status": "aborted", "warning": "⚠️  Google Photos identity not applied: library changed by another process "
                                            "— re-run when idle.", "changed": [], "confirmed": 0, "conflicts": [],
            "orphans": []})

    res = main.cmd_check_photos(SEASON, apply=True)

    out = capsys.readouterr().out
    assert res["status"] == "error" and sandbox["lib_series"].read_bytes() == before
    if trouble == "sidecar":
        assert "❌ check_photos: the apply stopped: OSError: [Errno 28] No space left on device — the library was not " \
               "changed; re-run the apply (records already appended are not appended twice)" in out
    else:
        assert "  ❌ library: ⚠️  Google Photos identity not applied: library changed by another process" in out
        assert out.rstrip().splitlines()[-1].endswith("· NOT applied (aborted)")


# ===========================================================================
# 13.i — resumability, end to end; never a download
# ===========================================================================
def _routed_lines(run_dir):
    return [(l["manual_id"][-3:], l["outcome"]) for l in _results(run_dir) if l["pass"] == "routed"]


def test_a_resumed_run_retries_its_errors_and_never_rechecks_a_settled_object(cw, sandbox, monkeypatch, capsys):
    _demo_season(sandbox, cw.world)
    real, seen = mainfetch.locate_object, []

    def flaky(driver, loc, ctx, *args, **kwargs):
        seen.append(loc["manual_id"][-3:])
        if seen.count("e02") == 1 and seen[-1] == "e02":
            raise RuntimeError("the page did not render")
        if seen.count("e03") == 1 and seen[-1] == "e03":
            raise KeyboardInterrupt
        return real(driver, loc, ctx, *args, **kwargs)

    monkeypatch.setattr(mainfetch, "locate_object", flaky)
    first = main.cmd_check_photos(SEASON)
    capsys.readouterr()
    second = main.cmd_check_photos(SEASON)

    out = capsys.readouterr().out
    assert (first["status"], second["status"]) == ("interrupted", "ok") and first["run_dir"] == second["run_dir"]
    assert f"  ↻ Continuing the unfinished run {os.path.basename(first['run_dir'])} (begun 2026-10-14T00:00:00Z): 1 of " \
           f"3 object(s) checked already — --recheck starts afresh" in out          # an error is not "checked"
    assert seen == ["e01", "e02", "e03", "e02", "e03"]                             # e01 never again; e02 retried
    assert _routed_lines(second["run_dir"]) == [("e01", "exactly-one"), ("e02", "error"), ("e02", "exactly-one"),
                                                ("e03", "exactly-one")]
    assert [r["status"] for r in second["rows"]] == ["confirmed"] * 3                # the newest look decides
    assert [(i["n"], i["reason"]) for i in _progress(second["run_dir"])["invocations"]] == [(1, "interrupted"),
                                                                                           (2, None)]


def test_recheck_starts_a_new_run_and_a_complete_run_is_followed_by_a_fresh_one(cw, sandbox):
    # A1: a complete run is never continued — the next run checks every object again, confirmed ones included (R3's
    # check after map --apply and R4's sweep verify what the library already believes).
    _demo_season(sandbox, cw.world)
    partial = main.cmd_check_photos(SEASON, limit=1)
    fresh = main.cmd_check_photos(SEASON, recheck=True)
    following = main.cmd_check_photos(SEASON)

    assert len({partial["run_dir"], fresh["run_dir"], following["run_dir"]}) == 3
    assert (_progress(partial["run_dir"])["state"], len(_results(partial["run_dir"]))) == ("partial", 1)   # left as is
    for res in (fresh, following):
        assert res["status"] == "ok" and _routed_lines(res["run_dir"]) == [
            ("e01", "exactly-one"), ("e02", "exactly-one"), ("e03", "exactly-one")]


def test_a_killed_check_leaves_a_run_the_next_one_continues_past_its_torn_line(cw, sandbox):
    # A child killed hard (closed terminal, power) leaves progress.json "running" and maybe half a results line: the
    # next run continues that run; the torn line is skipped and terminated before the next line is appended.
    _demo_season(sandbox, cw.world)
    first = main.cmd_check_photos(SEASON, limit=1)
    run_dir = first["run_dir"]
    progress = _progress(run_dir)
    main._cp_write_json(os.path.join(run_dir, "progress.json"), dict(progress, state="running", reason=None))
    with open(os.path.join(run_dir, "results.jsonl"), "ab") as fh:
        fh.write(b'{"v":1,"kind":"check","pass":"routed","manual_id":"' + f"{SEASON}e02".encode())   # no newline

    second = main.cmd_check_photos(SEASON)

    assert second["run_dir"] == run_dir and second["status"] == "ok"
    assert _routed_lines(run_dir) == [("e01", "exactly-one"), ("e02", "exactly-one"), ("e03", "exactly-one")]
    with open(os.path.join(run_dir, "results.jsonl"), "rb") as fh:
        raw = fh.read().split(b"\n")
    assert sum(1 for line in raw if line.startswith(b'{"v":1,"kind":"check","pass":"routed","manual_id":"') and
               not line.endswith(b"}")) == 1                                       # the torn line stays alone


def test_objects_that_joined_the_scope_wait_for_the_next_run(cw, sandbox, capsys):
    _demo_season(sandbox, cw.world, episodes=(1, 2))
    first = main.cmd_check_photos(SEASON, limit=1)
    lib = mvcommon.load_library()
    folder = sandbox["local_root"] / "Series" / "Demo" / "Season 01"
    mid, name, sid = f"{SEASON}e03", "Demo.S01E03.mkv", "de3de3"
    item = cw.world.add_item("tv", _up(name, sid), data=_bytes(mid), taken=TAKEN)
    lib[mid] = dict(_whole(folder, name, _bytes(mid), sid, block=_learned(item)), parent_id=SEASON)
    lib[SEASON]["children"].append(mid)
    mvcommon.save_library(lib)
    capsys.readouterr()

    resumed = main.cmd_check_photos(SEASON)
    out = capsys.readouterr().out
    following = main.cmd_check_photos(SEASON)

    assert resumed["run_dir"] == first["run_dir"] and len(resumed["rows"]) == 2
    assert "  1 object(s) of the scope joined since that run began — the next run checks them (or now: --recheck)" in out
    assert following["run_dir"] != first["run_dir"] and [r["manual_id"][-3:] for r in following["rows"]] == [
        "e01", "e02", "e03"]


class _ProcessTable:
    """mainfetch.PROCESS_TABLE's stand-in: `alive` {pid: command line}; terminate() ends a pid."""

    def __init__(self, alive):
        self.alive, self.terminated = dict(alive), []

    def query(self, pid):
        return (True, self.alive[pid]) if pid in self.alive else (False, None)

    def listener(self, port):
        return None

    def chrome_command_lines(self):
        return list(self.alive.values())

    def terminate(self, pid):
        self.terminated.append(pid)
        self.alive.pop(pid, None)
        return True


def test_the_next_check_ends_the_chrome_a_killed_check_left_running(cw, sandbox, monkeypatch, capsys):
    # A check killed hard leaves its fetch journal "running" with the Chrome it launched: the next check's start-up
    # cleanup (Step 11's, P-12 rule 3) ends exactly that Chrome before opening its own.
    _demo_season(sandbox, cw.world, episodes=(1,))
    profile = mainfetch.CHROME_PROFILES["tv"]
    table = _ProcessTable({7002: f'"C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe" '
                                 f'--user-data-dir={profile} --remote-debugging-port=9222 about:blank'})
    monkeypatch.setattr(mainfetch, "PROCESS_TABLE", table)
    journal = os.path.join(mainfetch.FETCH_JOURNAL_DIR, "check-20261013T090000Z-7001.json")
    os.makedirs(mainfetch.FETCH_JOURNAL_DIR, exist_ok=True)
    with open(journal, "w", encoding="utf-8") as fh:
        json.dump({"v": 1, "run_id": "check-20261013T090000Z-7001", "started_at": "2026-10-13T09:00:00Z",
                   "updated_at": "2026-10-13T09:05:00Z", "pid": 7001, "state": "running", "interrupted": False,
                   "mode": None, "sessions": [{"account": "tv", "chrome_pid": 7002, "user_data_dir": profile,
                                               "launched": True, "started_at": "2026-10-13T09:00:01Z"}],
                   "entries": [], "pending_delete": []}, fh)

    res = main.cmd_check_photos(SEASON)

    assert res["status"] == "ok" and table.terminated == [7002]
    with open(journal, encoding="utf-8") as fh:
        assert json.load(fh)["state"] == "recovered"
    assert "Closed the Chrome an interrupted fetch left running (pid 7002)." in capsys.readouterr().out


_DOWNLOAD_PATHS = ("_direct_trigger", "_shift_d_trigger", "trigger_download", "_fire", "harvest_downloads",
                   "_fetch_window", "_another_copy", "_learn_from_download", "_run_locator_phase", "_record_unfetched",
                   "_set_download_dir")   # every way the fetch requests, arms, harvests or books a download


@pytest.mark.parametrize("approaches", ["best", "all"])
@pytest.mark.parametrize("rpc", [True, False], ids=["rpc", "web-pages"])
def test_a_check_never_requests_a_download(cw, sandbox, monkeypatch, approaches, rpc):
    # PLAN Step 13 "never triggers a download": while a check takes every kind of object through every stage — in its
    # own account and in the others, by RPC or on the web pages — and applies, each fetch path that could request, arm
    # or harvest a download is forbidden; no link is minted, no Shift+D pressed, no tile opened, no file written.
    def forbidden(name):
        def refuse(*args, **kwargs):
            raise AssertionError(f"check_photos reached {name}")
        return refuse

    for name in _DOWNLOAD_PATHS:
        monkeypatch.setattr(mainfetch, name, forbidden(name))
    monkeypatch.setattr(gprpc, "download_url", forbidden("gprpc.download_url"))
    items = _season_of(sandbox, cw.world, {1: "learned", 2: "mapped", 3: "pending", 4: "legacy", 5: "missing",
                                           6: "pending"})
    items[f"{SEASON}e06"].deleted = True                       # content-keyed and gone: S4's preview runs for it too
    for account in mvcommon.GP_ACCOUNTS:
        cw.world.wiz_token_missing[account] = not rpc

    res = main.cmd_check_photos(SEASON, approaches=approaches, apply=True)

    assert res["status"] == "ok" and [r["status"] for r in res["rows"]] == ["confirmed"] * 4 + ["possible-loss"] * 2
    assert {l["account"] for l in _results(res["run_dir"]) if l["pass"] == "cross"} == {"movies", "anime", "others"}
    assert any("legacy" in l["per_approach"] for l in _results(res["run_dir"]))   # the S4 preview did run
    assert cw.world.downloads == [] and "pLFTfd" not in cw.world.rpc_ids_sent()
    assert (cw.world.rpc_log == []) is (not rpc)
    assert not [a for drivers in cw.drivers.values() for d in drivers for a in d.actions
                if a["kind"] in ("shift_d", "open")]
    assert not os.path.isdir(cw.world.default_download_dir) or os.listdir(cw.world.default_download_dir) == []
    assert not os.path.exists(mainfetch.SYSTEM_DOWNLOADS_FOLDER)


# ===========================================================================
# 13.j — --min-age-h (IMP-C25 acfix): only objects pushed at least H hours ago are looked up
# ===========================================================================
def _aged_capture(folder, sid, data, name, hours, now):
    """test_locator._capture of ONE whole-file push (SHA-1 + DateUTC: a new archival) made `hours` before `now` — None:
    the capture records the push without its time."""
    _capture(folder, sid, [_obj(data, _up(name, sid))])
    path = folder / f"{sid}.gpcapture.json"
    doc = json.loads(path.read_text(encoding="utf-8"))
    if hours is None:
        del doc["pushes"][0]["captured_at"]
    else:
        doc["pushes"][0]["captured_at"] = (now - timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%SZ")
    path.write_text(json.dumps(doc), encoding="utf-8")


def _aged_season(sandbox, world, ages, now=LATER):
    """A season SEASON with one pending whole-file episode per entry of `ages` {n: hours before `now` it was pushed, or
    None: no push time}; every episode's item IS in Google Photos (tv), so any lookup confirms it -> {episode id: item}."""
    folder = sandbox["local_root"] / "Series" / "Demo" / "Season 01"
    folder.mkdir(parents=True, exist_ok=True)
    lib, items = {SEASON: {"type": "season_map", "folder_path": str(folder), "total_episodes": len(ages),
                           "children": [f"{SEASON}e0{n}" for n in ages]}}, {}
    for n, hours in ages.items():
        mid, name, sid = f"{SEASON}e0{n}", f"Demo.S01E0{n}.mkv", f"ag{n}ag{n}"
        data = _bytes(mid)
        items[mid] = world.add_item("tv", _up(name, sid), data=data, taken=TAKEN)
        _aged_capture(folder, sid, data, name, hours, now)
        lib[mid] = dict(_whole(folder, name, data, sid), parent_id=SEASON)
    mvcommon.save_library(lib)
    return items


def test_min_age_h_looks_up_only_the_old_objects_and_records_nothing_for_the_young_ones(cw, sandbox, capsys):
    # The auto-confirm's call `check_photos pending --apply --min-age-h 24` (the user's finding of 2026-10-27: the chunks
    # pushed minutes earlier were looked up). e01 was pushed 30 h ago, e02's capture has no push time (it counts as
    # old), e03 and e04 were pushed 2 h and 23.9 h ago. Every item IS in Google Photos: any lookup would confirm it.
    items = _aged_season(sandbox, cw.world, {1: 30, 2: None, 3: 2, 4: 23.9})
    old, young = [f"{SEASON}e01", f"{SEASON}e02"], [f"{SEASON}e03", f"{SEASON}e04"]

    res = main.cmd_check_photos("pending", apply=True, min_age_h=24)

    out = capsys.readouterr().out
    assert res["status"] == "ok"
    assert [(r["manual_id"], r["status"]) for r in res["rows"]] == [(old[0], "confirmed"), (old[1], "confirmed")]
    assert ("  2 object(s) in 2 entr(y/ies) — checked first in: tv 2\n"
            "  2 object(s) pushed less than 24 h ago skipped — checked at a later push\n") in out
    doc = main._cp_read_json(os.path.join(res["run_dir"], "run.json"))
    assert doc["min_age_h"] == 24 and [o["manual_id"] for o in doc["objects"]] == old
    assert sorted({line["manual_id"] for line in main._cp_read_results(res["run_dir"])}) == old
    looked = json.dumps([e.get("request") for e in cw.world.rpc_log]) + json.dumps(
        [d.actions for drivers in cw.drivers.values() for d in drivers])
    assert not [m for m in young for trace in (items[m].hash_b64, items[m].dedup_key, items[m].filename,
                                               items[m].photo_id) if trace in looked]   # never looked up: RPC or page
    assert sorted({r["manual_id"] for r in mvcommon.read_learned()}) == old      # no learned record for a young one
    assert mvcommon.read_failed() == []                                          # no failed row
    assert [_entry_block(m) is not None for m in old + young] == [True, True, False, False]
    assert {o["manual_id"] for o in main._cp_pending_list()["objects"]} == set(young)   # still pending: a later push


def test_without_min_age_h_every_pending_object_is_looked_up_as_before(cw, sandbox, capsys):
    _aged_season(sandbox, cw.world, {1: 30, 2: None, 3: 2, 4: 23.9})

    res = main.cmd_check_photos("pending", apply=True)

    assert [r["status"] for r in res["rows"]] == ["confirmed"] * 4
    assert "min_age_h" not in main._cp_read_json(os.path.join(res["run_dir"], "run.json"))   # today's run.json
    assert "skipped — checked at a later push" not in capsys.readouterr().out


def test_min_age_h_with_only_young_pushes_starts_nothing_and_writes_nothing(cw, sandbox, tmp_path, capsys):
    _aged_season(sandbox, cw.world, {1: 0.1, 2: 23.99})
    before = _tree(tmp_path)

    res = main.cmd_check_photos("pending", apply=True, min_age_h=24)

    assert res == {"status": "ok", "run_dir": None}
    assert cw.spawned == [] and cw.drivers == {} and cw.world.rpc_log == []     # no child, no Chrome, no lookup
    assert capsys.readouterr().out.splitlines()[1:] == [
        "  0 object(s) in 0 entr(y/ies)",
        "  2 object(s) pushed less than 24 h ago skipped — checked at a later push",
        "  Nothing to check."]
    assert _tree(tmp_path) == before                   # no run directory, no record, no failed row: nothing written


def test_min_age_h_is_part_of_the_resume_key(cw, sandbox, capsys):
    # An unfinished plain `pending` run holds the young e02: the auto-confirm's `pending --min-age-h 24` must not continue
    # it (it would look e02 up) — it starts its own run; each kind of run then continues its own.
    _aged_season(sandbox, cw.world, {1: 30, 2: 2, 3: 40})
    plain = main.cmd_check_photos("pending", limit=1)
    aged = main.cmd_check_photos("pending", min_age_h=24, limit=1)
    capsys.readouterr()
    aged_again = main.cmd_check_photos("pending", min_age_h=24)
    resumed = capsys.readouterr().out
    plain_again = main.cmd_check_photos("pending")

    assert [r["status"] for r in (plain, aged, aged_again, plain_again)] == ["partial", "partial", "ok", "ok"]
    assert aged["run_dir"] != plain["run_dir"]
    assert (aged_again["run_dir"], plain_again["run_dir"]) == (aged["run_dir"], plain["run_dir"])
    assert (f"↻ Continuing the unfinished run {os.path.basename(aged['run_dir'])} (begun 2026-10-14T00:00:00Z): 1 of 2 "
            f"object(s) checked already") in resumed
    doc = main._cp_read_json(os.path.join(aged["run_dir"], "run.json"))
    assert [o["manual_id"] for o in doc["objects"]] == [f"{SEASON}e01", f"{SEASON}e03"]
    assert f"{SEASON}e02" not in {line["manual_id"] for line in main._cp_read_results(aged["run_dir"])}
    assert main._cp_check_command(doc) == "python main.py check_photos pending --min-age-h 24"   # "continue it with"
    assert main._cp_check_command(main._cp_read_json(os.path.join(plain["run_dir"], "run.json"))) == (
        "python main.py check_photos pending")


@pytest.mark.parametrize("hours, looked_up", [(24.0, True), (24.01, True), (23.99, False), (None, True), (-1.0, False)],
                         ids=["exactly-24h", "older", "younger", "no-push-time", "pushed-in-the-future"])
def test_min_age_h_an_object_exactly_that_old_is_looked_up(sandbox, hours, looked_up):
    # maybe_auto_confirm's own boundary (age_hours >= AUTO_CONFIRM_MIN_AGE_H is due); no push time counts as old (Step
    # 13's verdict rule); a push time after `now` (a clock change) is young, as maybe_auto_confirm reads it too.
    folder = sandbox["local_root"] / "Movies" / "Aged (2026)"
    folder.mkdir(parents=True)
    data = _bytes("aged")
    _aged_capture(folder, "ag0ag0", data, "Aged.mkv", hours, NOW)
    mvcommon.save_library({"mov-en-2026-aged": _whole(folder, "Aged.mkv", data, "ag0ag0")})

    res = main._cp_resolve(mvcommon.load_library(), "pending", None, NOW, min_age_h=24)

    assert (_ids(res), res["young"]) == ((["mov-en-2026-aged"], 0) if looked_up else ([], 1))


def test_min_age_h_works_with_every_scope_after_the_account_filter(sandbox):
    folder = sandbox["local_root"] / "Series" / "Aged"
    folder.mkdir(parents=True)
    lib = {}
    for mid, sid, hours in (("tv-en-2026-aged-s01e01", "ag1ag1", 2), ("tv-en-2026-aged-s01e02", "ag2ag2", 48)):
        _aged_capture(folder, sid, _bytes(mid), f"{mid}.mkv", hours, NOW)
        lib[mid] = _whole(folder, f"{mid}.mkv", _bytes(mid), sid)
    lib["mov-en-2010-legacy"] = _whole(folder, "Legacy.mkv", _bytes("legacy"), "le9acy")   # no capture: no push time
    mvcommon.save_library(lib)
    library = mvcommon.load_library()

    everything = main._cp_resolve(library, "all", None, NOW, min_age_h=24)
    movies = main._cp_resolve(library, "all", "movies", NOW, min_age_h=24)
    prefix = main._cp_resolve(library, "tv-en-2026-aged", None, NOW, min_age_h=2.5)
    plain = main._cp_resolve(library, "all", None, NOW)

    assert (_ids(everything), everything["young"]) == (["mov-en-2010-legacy", "tv-en-2026-aged-s01e02"], 1)
    assert everything["notes"] == ["1 object(s) pushed less than 24 h ago skipped — checked at a later push"]
    assert main._cp_scope_lines(everything, library) == [
        "  2 object(s) in 2 entr(y/ies) — checked first in: movies 1 · tv 1",
        "  1 object(s) pushed less than 24 h ago skipped — checked at a later push"]
    assert (_ids(movies), movies["young"], movies["notes"]) == (["mov-en-2010-legacy"], 0, [])   # --account first
    assert _ids(prefix) == ["tv-en-2026-aged-s01e02"]
    assert prefix["notes"] == ["1 object(s) pushed less than 2.5 h ago skipped — checked at a later push"]
    assert (len(plain["objects"]), plain["young"], plain["notes"]) == (3, 0, [])   # without it: unchanged
