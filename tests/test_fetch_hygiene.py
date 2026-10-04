"""IMP-C25 Step 11 — download hygiene (docs/feature-fetch-datetime/PLAN.md P-12 + Step 11).

Chrome downloads straight into each entry's own destination, <dest>\\.mv_incoming\\ (<dest> = the
unchanged _fetch_restore_folder): a SHA-256-verified file is renamed into <dest>, anything else is
deleted, the folder is removed when the entry ends, nothing lands in ~/Downloads. A fetch journal
(~/.mediavault/fetch_journal/<run_id>.json) lets the next run clean up after an interrupted one;
`fetch_cleanup` lists (and with --apply deletes) leftovers from before this feature; verify_restore
accepts `tempdir`.

  11.a  the per-entry download folder: CDP redirect (Browser, then Page), long-path rule, marker,
        disk pre-flight, the expected-name family, re-arming on every Photos tab
  11.b  the harvester's modes: incoming (only the entry's folder; renamed into restore; everything
        else deleted and listed; ~/Downloads never read), the ~/Downloads fallback (P-12's delete
        rules), legacy (a stand-in driver without DevTools: today's flow, frozen in
        tests/test_fetch_harvester.py)

Hermetic: tmp folders only (the autouse fixtures in tests/conftest.py redirect the state dirs,
~/Downloads and the long-path root); CDP is a recording stand-in or the fake Google Photos world
(tests/gphotos_fake.py); mainfetch's clock is the C29 fake clock; no browser, no real ~/Downloads,
no real C:\\Media.
"""
import contextlib
import hashlib
import json
import os
import pathlib
import shutil
import types

import pytest

import mainfetch
import mvcommon
from test_fetch_harvester import _Clock   # sleep() advances a fake clock and fires the scripted Chrome events


def _sha(data):
    return hashlib.sha256(data if isinstance(data, bytes) else data.encode()).hexdigest()


class _CdpDriver:
    """A stand-in for the attached Chrome that speaks CDP: records every execute_cdp_cmd call and
    raises for the commands listed in `refuse` (a Chrome that refuses them)."""

    def __init__(self, refuse=()):
        self.cdp = []
        self.refuse = set(refuse)

    def execute_cdp_cmd(self, cmd, params):
        self.cdp.append((cmd, dict(params)))
        if cmd in self.refuse:
            raise RuntimeError(f"{cmd} is not allowed")
        return {}


# ===========================================================================
# 11.a — the per-entry download folder
# ===========================================================================
def test_the_redirect_is_browser_set_download_behavior_with_events(tmp_path):
    driver = _CdpDriver()
    path = str(tmp_path / "restore" / ".mv_incoming")

    assert mainfetch._set_download_dir(driver, path) is True
    assert driver.cdp == [("Browser.setDownloadBehavior",
                           {"behavior": "allow", "downloadPath": path, "eventsEnabled": True})]


def test_a_refused_browser_command_falls_back_to_the_page_command(tmp_path):
    driver = _CdpDriver(refuse={"Browser.setDownloadBehavior"})
    path = str(tmp_path / "restore" / ".mv_incoming")

    assert mainfetch._set_download_dir(driver, path) is True
    assert [c for c, _ in driver.cdp] == ["Browser.setDownloadBehavior", "Page.setDownloadBehavior"]
    assert driver.cdp[1][1] == {"behavior": "allow", "downloadPath": path}


def test_chrome_refusing_both_commands_is_reported_not_raised(tmp_path):
    driver = _CdpDriver(refuse={"Browser.setDownloadBehavior", "Page.setDownloadBehavior"})
    assert mainfetch._set_download_dir(driver, str(tmp_path)) is False
    assert len(driver.cdp) == 2


def test_every_photos_tab_re_applies_the_armed_download_folder(fake_photos, tmp_path):
    # Chromium drops the redirect when the DevTools session of the tab that set it ends; use_photos_tab
    # runs before every search and click, so re-applying there keeps the entry's downloads in place.
    d = fake_photos.driver("tv")
    incoming = str(tmp_path / "Show" / "restore" / ".mv_incoming")

    mainfetch.use_photos_tab(d)                              # nothing armed: no redirect is sent
    assert not [c for c, _ in d.cdp_log if c.endswith("setDownloadBehavior")]

    d._mv_download_dir = incoming
    mainfetch.use_photos_tab(d)
    assert d.cdp_log[-1] == ("Browser.setDownloadBehavior",
                             {"behavior": "allow", "downloadPath": incoming, "eventsEnabled": True})
    assert d.download_dir == incoming


def test_the_incoming_folder_sits_in_the_destination(tmp_path):
    dest = str(tmp_path / "Show" / "restore")
    assert mainfetch._incoming_location(dest, ["Show.S01E01 [abc123].mkv"]) == os.path.join(dest, ".mv_incoming")


def test_a_path_too_long_for_chrome_moves_to_a_short_folder_on_the_same_volume(monkeypatch, real_short_incoming_root):
    monkeypatch.setattr(mainfetch, "_short_incoming_root", real_short_incoming_root)   # arithmetic only: creates nothing
    dest = os.path.join(r"D:\MV_fetch", "x" * 120, "restore")
    incoming = os.path.join(dest, ".mv_incoming")
    room = mainfetch.INCOMING_PATH_LIMIT - len(incoming) - 1 - len(".crdownload")   # longest name that still fits

    assert mainfetch._incoming_location(dest, ["n" * room]) == incoming
    short = mainfetch._incoming_location(dest, ["n" * (room + 1)])
    digest = hashlib.sha1(os.path.normcase(dest).encode("utf-8")).hexdigest()[:8]
    assert short == os.path.join("D:" + os.sep, "MV_incoming", digest)
    assert mainfetch._incoming_location(dest, ["n" * (room + 1)]) == short       # the same entry, the same folder


def test_a_destination_without_a_drive_has_no_short_folder(monkeypatch, real_short_incoming_root):
    monkeypatch.setattr(mainfetch, "_short_incoming_root", real_short_incoming_root)
    monkeypatch.setattr(mainfetch.os.path, "splitdrive", lambda p: ("", p))
    assert mainfetch._incoming_location("restore", ["n" * 300]) is None


def test_no_test_can_reach_a_real_drive_root_or_the_real_downloads(tmp_path):
    # tests/conftest.py (autouse): the long-path folder and ~/Downloads are redirected under tmp_path.
    assert mainfetch._short_incoming_root(r"D:\anything").startswith(str(tmp_path))
    assert mainfetch.SYSTEM_DOWNLOADS_FOLDER.startswith(str(tmp_path))


def test_the_incoming_folder_is_created_with_its_marker(tmp_path):
    path = tmp_path / "restore" / ".mv_incoming"

    assert mainfetch._make_incoming(str(path)) is True
    assert sorted(p.name for p in path.iterdir()) == [mainfetch.INCOMING_MARKER]
    assert mainfetch._make_incoming(str(path)) is True                           # reused, not an error
    blocked = tmp_path / "file-in-the-way"
    blocked.write_bytes(b"x")
    assert mainfetch._make_incoming(str(blocked / ".mv_incoming")) is False


@pytest.mark.parametrize("layout, allowed", [
    ("restore/.mv_incoming", True),           # <dest>\.mv_incoming with the marker
    ("MV_incoming/0a1b2c3d", True),           # the short long-path folder with the marker
    ("restore/.mv_incoming#nomarker", False),  # right name, no marker
    ("restore/incoming", False),              # marker, wrong name
    ("Downloads", False),                     # never the Downloads folder, marker or not
])
def test_only_a_marked_incoming_folder_may_be_emptied(tmp_path, layout, allowed):
    rel, _, flag = layout.partition("#")
    path = tmp_path.joinpath(*rel.split("/"))
    path.mkdir(parents=True)
    if flag != "nomarker":
        (path / mainfetch.INCOMING_MARKER).write_text("x", encoding="utf-8")
    assert mainfetch._is_incoming_dir(str(path)) is allowed


FAMILY = ["Show.S01E01 [abc123].mkv", "Movie [f1f1f1].chunk.002.mkv"]


@pytest.mark.parametrize("name, member", [
    ("Show.S01E01 [abc123].mkv", True),
    ("show.s01e01 [ABC123].MKV", True),                       # Windows names are case-insensitive
    ("Show.S01E01 [abc123] (1).mkv", True),                   # Chrome's uniquifier
    ("Show.S01E01 [abc123] (12).mkv.crdownload", True),       # ... and its partial
    ("Show.S01E01 [abc123].mkv.crdownload", True),
    ("Movie [f1f1f1].chunk.002 (3).mkv", True),               # numbered before the LAST extension
    ("Show.S01E01 [abc123].mkv.part", False),
    ("Show.S01E01 [abc123] (x).mkv", False),
    ("Show.S01E01 [abc123] ().mkv", False),
    ("Show.S01E01 [abc123] (1).mp4", False),
    ("Show.S01E01.mkv", False),                               # the plain library name was never uploaded
    ("Movie [f1f1f1].chunk.001.mkv", False),                  # another object of the same title
    ("Unconfirmed 734123.crdownload", False),                 # Chrome has not named it: not provably ours
    ("Holiday.mkv", False),
])
def test_the_expected_name_family(name, member):
    assert mainfetch._in_family(name, FAMILY) is member


def test_the_family_is_every_queued_objects_uploaded_name():
    queue = [{"filename": "Show.S01E01.mkv", "locator": {"uploaded_name": "Show.S01E01 [abc123].mkv"}},
             {"filename": "x.mkv", "locator": {"uploaded_name": None}},
             {"filename": "y.mkv", "locator": {"uploaded_name": "Show.S01E01 [abc123].mkv"}}]
    assert mainfetch._expected_names(queue) == ["Show.S01E01 [abc123].mkv"]


class _Usage:
    def __init__(self, free):
        self.free, self.total, self.used = free, free * 2, free


@pytest.mark.parametrize("free_extra, ok", [(0, True), (-1, False)])
def test_the_disk_pre_flight_needs_the_bytes_plus_two_gb(tmp_path, monkeypatch, capsys, free_extra, ok):
    need = 5 * 1024 ** 3
    monkeypatch.setattr(mainfetch.shutil, "disk_usage",
                        lambda p: _Usage(need + mainfetch.DISK_HEADROOM_BYTES + free_extra))

    assert mainfetch._disk_preflight(str(tmp_path), need) is ok

    out = capsys.readouterr().out
    assert "7.00 GB needed (5.00 GB to download + 2.00 GB headroom)" in out
    if ok:
        assert out.startswith("   > 💽 Disk space on ")
    else:
        assert out.startswith("   ❌ Not enough free space on ") and "Nothing was downloaded" in out


def test_an_unreadable_volume_does_not_block_the_fetch(tmp_path, monkeypatch, capsys):
    def boom(path):
        raise OSError("device not ready")

    monkeypatch.setattr(mainfetch.shutil, "disk_usage", boom)
    assert mainfetch._disk_preflight(str(tmp_path), 10) is True
    assert "Could not read the free space" in capsys.readouterr().out


def test_remaining_bytes_use_recorded_sizes_then_a_share_of_the_whole_file(tmp_path):
    chunks = [{"filename": f"Film [f1f1f1].chunk.00{n}.mkv", "hash": _sha(f"c{n}")} for n in (1, 2, 3)]
    chunks[0]["gphotos"] = mvcommon.new_gphotos_block(chunks[0]["hash"], size_bytes=1000)   # a recorded size
    entry = {"filename": "Film.mkv", "short_id": "f1f1f1", "folder_path": str(tmp_path), "hash": _sha("film"),
             "tech_spec": {"size_bytes": 9000}, "uploaded": True, "status": "archived",
             "split_info": {"is_split": True, "total_chunks": 3, "chunks": chunks,
                            "carried_out_tracks": [{"holder_filename": "Film [f1f1f1].holder.mkv",
                                                    "holder_hash": _sha("holder")}]}}
    queue = mainfetch.build_fetch_queue(entry, str(tmp_path / "restore"), entry_id="mov-en-2020-film")

    assert mainfetch._remaining_bytes(entry, "mov-en-2020-film", queue) == 1000 + 3000 + 3000 + 0
    whole = {"filename": "Show.mkv", "short_id": "abc123", "folder_path": str(tmp_path), "hash": _sha("show"),
             "tech_spec": {"size_bytes": 4096}}
    wq = mainfetch.build_fetch_queue(whole, str(tmp_path / "r2"), entry_id="tv-en-2001-show-s01e01")
    assert mainfetch._remaining_bytes(whole, "tv-en-2001-show-s01e01", wq) == 4096
    assert mainfetch._remaining_bytes({"filename": "x.mkv", "hash": "x"}, None, []) == 0


def test_extras_entries_carry_their_size_for_the_pre_flight(tmp_path):
    title = {"folder_path": str(tmp_path), "extras": {"groups": {"Specials": {"items": [
        {"filename": "BTS.mkv", "sub_rel": "BTS.mkv", "hash": _sha("bts"), "uploaded": True, "short_id": "b1b1b1",
         "tech_spec": {"size_bytes": 777}}]}}}}
    [extra] = mainfetch.build_extras_entries(title, "mov-en-2024-demo")
    assert extra["tech_spec"] == {"size_bytes": 777}
    queue = mainfetch.build_fetch_queue(extra, str(tmp_path / "Specials" / "restore"))
    assert mainfetch._remaining_bytes(extra, "mov-en-2024-demo", queue) == 777


# ===========================================================================
# 11.b — the harvester's modes
# ===========================================================================
PAYLOAD_A, PAYLOAD_B, PAYLOAD_C, PAYLOAD_H, PAYLOAD_X = (bytes([c]) * 4096 for c in b"ABCHX")
EP_ID = "tv-en-2001-show-s01e01"
SHOW_UP = "Show.S01E01 [abc123].mkv"      # the episode's uploaded name = the name Google Photos downloads it under
SEASON = "tv-en-2001-demo-s01"
WEB_MARKERS = ("🔹 PROCESSING:", "Detected Split File", "✅ MOVED:", "✅ ENTRY COMPLETE.")   # PLAN P-13


class _Browser(_CdpDriver):
    """The attached Chrome, scripted: CDP Browser/Page.setDownloadBehavior moves its download folder (its own
    Downloads before that; `refuse` makes Chrome refuse). start(name, payload, seconds) begins a download in the
    CURRENT folder — '<name>.crdownload' now (Chrome's ' (n)' name when the name is taken), the finished file
    `seconds` later on the fake clock, or nothing at all with payload None (a broken transfer that cannot
    resume). `saved` lists (folder, final name) per download."""

    def __init__(self, clock, downloads, refuse=(), profile="tv"):
        super().__init__(refuse)
        self.clock, self.dir, self.profile = clock, str(downloads), profile
        self.saved, self.quits = [], 0

    def execute_cdp_cmd(self, cmd, params):
        super().execute_cdp_cmd(cmd, params)
        if cmd.endswith("setDownloadBehavior"):
            self.dir = params["downloadPath"]
        return {}

    @property
    def window_handles(self):
        return []

    def quit(self):
        self.quits += 1

    def start(self, name, payload, seconds):
        folder = pathlib.Path(self.dir)
        stem, ext = os.path.splitext(name)
        final, n = name, 0
        while (folder / final).exists() or (folder / (final + ".crdownload")).exists():
            n += 1
            final = f"{stem} ({n}){ext}"
        partial = folder / (final + ".crdownload")
        partial.write_bytes(b"partial")
        self.saved.append((str(folder), final))

        def finish():
            if not partial.exists():
                return                   # deleted while still downloading: that transfer is over
            partial.unlink()
            if payload is not None:
                (folder / final).write_bytes(payload)

        self.clock.after(seconds, finish)
        return True


@pytest.fixture()
def world(tmp_path, monkeypatch):
    downloads = tmp_path / "Downloads"
    downloads.mkdir()
    clock = _Clock()
    monkeypatch.setattr(mainfetch, "SYSTEM_DOWNLOADS_FOLDER", str(downloads))
    monkeypatch.setattr(mainfetch, "time", clock)
    monkeypatch.setattr(mainfetch.shutil, "disk_usage", lambda path: _Usage(10 ** 15))   # plenty, unless a test says
    return types.SimpleNamespace(clock=clock, browser=_Browser(clock, downloads), downloads=downloads, tmp=tmp_path)


def _script(monkeypatch, script):
    """trigger_download -> script(query, index): truthy when a download was requested."""
    calls = []

    def _trigger(driver, query, index=0):
        calls.append((query, index))
        return bool(script(query, index))

    monkeypatch.setattr(mainfetch, "trigger_download", _trigger)
    return calls


def _session(account):
    ctx = mainfetch.FetchContext()
    ctx.start_session(account)
    return ctx


def _whole_entry(tmp_path, payload=PAYLOAD_A):
    folder = tmp_path / "title"
    folder.mkdir(exist_ok=True)
    return {"filename": "Show.S01E01.mkv", "short_id": "abc123", "folder_path": str(folder), "hash": _sha(payload),
            "search_term": SHOW_UP, "tech_spec": {"size_bytes": len(payload)}}


def _restore(entry):
    return pathlib.Path(entry["folder_path"]) / "restore"


def _forbid(monkeypatch, folder):
    """Any read or write of `folder` (a listing, a stat, a delete, a rename, a move) fails the test."""
    root = os.path.normcase(os.path.abspath(str(folder)))

    def inside(path):
        try:
            return os.path.normcase(os.path.abspath(os.fspath(path))).startswith(root)
        except TypeError:
            return False

    for module, name in ((os, "listdir"), (os, "scandir"), (os, "stat"), (os, "remove"), (os, "replace"),
                         (os, "rename"), (os, "makedirs"), (os.path, "exists"), (os.path, "isfile"),
                         (os.path, "isdir"), (os.path, "getsize"), (shutil, "move")):
        real = getattr(module, name)

        def guard(*args, _real=real, _name=name, **kwargs):
            if args and inside(args[0]):
                raise AssertionError(f"~/Downloads was touched: {_name}({args[0]!r})")
            return _real(*args, **kwargs)

        monkeypatch.setattr(module, name, guard)


def test_a_download_lands_in_the_entrys_incoming_folder_and_is_renamed_into_restore(world, monkeypatch, capsys):
    entry = _whole_entry(world.tmp)
    incoming = _restore(entry) / ".mv_incoming"
    calls = _script(monkeypatch, lambda q, i: world.browser.start(SHOW_UP, PAYLOAD_A, 100) if q == SHOW_UP else False)
    ctx = _session("tv")

    with monkeypatch.context() as m:
        _forbid(m, world.downloads)                       # incoming mode never reads nor writes ~/Downloads
        mainfetch.fetch_single_entry(world.browser, entry, entry_id=EP_ID, ctx=ctx)

    out = capsys.readouterr().out
    assert world.browser.cdp[0] == ("Browser.setDownloadBehavior",
                                    {"behavior": "allow", "downloadPath": str(incoming), "eventsEnabled": True})
    assert world.browser.saved == [(str(incoming), SHOW_UP)]
    assert calls == [("Show.S01E01.mkv", 0), (SHOW_UP, 0)]   # today's two attempts, unchanged
    assert (_restore(entry) / "Show.S01E01.mkv").read_bytes() == PAYLOAD_A
    assert sorted(p.name for p in _restore(entry).iterdir()) == ["Show.S01E01.mkv"]   # the incoming folder is gone
    assert out.count(f"   > Watching {incoming} (Infinite wait if active)...") == 2
    assert "Watching Downloads" not in out
    assert "✅ MOVED: Show.S01E01.mkv" in out and "✅ ENTRY COMPLETE." in out
    assert list(world.downloads.iterdir()) == [] and ctx.downloads is None
    [record] = mvcommon.read_learned("tv")                 # it arrived in this session: learned
    assert (record["manual_id"], record["sha256"], record["proof"]) == (EP_ID, _sha(PAYLOAD_A), "sha256")
    assert getattr(world.browser, "_mv_download_dir", None) is None          # disarmed when the entry ended


def test_a_wrong_item_is_deleted_listed_and_still_counts_as_unmatched(world, monkeypatch, capsys):
    # P-12 rule 1 + the IMP-C34 contract: a deleted mismatch must still count in "unmatched", or the round would book
    # a lost download and request the object again.
    entry = _whole_entry(world.tmp)
    calls = _script(monkeypatch, lambda q, i: world.browser.start("Other [zzz999].mkv", PAYLOAD_X, 60)
                    if q == SHOW_UP else False)
    ctx = _session("tv")

    mainfetch.fetch_single_entry(world.browser, entry, entry_id=EP_ID, ctx=ctx)

    out = capsys.readouterr().out
    assert "     🗑️ Not an expected file — deleted: Other [zzz999].mkv (4.00 KB)" in out
    assert calls == [("Show.S01E01.mkv", 0), (SHOW_UP, 0)] and "RETRY" not in out   # not booked as a lost download
    assert ctx.stats["retried"] == 0 and mvcommon.read_failed() == []
    assert "❌ ENTRY INCOMPLETE." in out
    assert not (_restore(entry) / ".mv_incoming").exists() and list(world.downloads.iterdir()) == []


def test_harvest_reports_a_deleted_mismatch_in_unmatched(world, monkeypatch, capsys):
    entry = _whole_entry(world.tmp)
    queue = mainfetch.build_fetch_queue(entry, str(_restore(entry)), entry_id=EP_ID)
    ctx = _session("tv")
    assert mainfetch._prepare_downloads(world.browser, queue, ctx, entry, EP_ID) is True
    world.browser.start("Other [zzz999].mkv", PAYLOAD_X, 1)
    world.clock.sleep(2)

    assert mainfetch.harvest_downloads(queue, ctx) == {"timed_out": True, "saw_download": False, "unmatched": 1}
    assert os.listdir(ctx.target["incoming"]) == [mainfetch.INCOMING_MARKER]


def test_a_copy_of_a_fetched_chunk_is_deleted_from_the_incoming_folder(world, monkeypatch, capsys):
    folder = world.tmp / "movie"
    folder.mkdir()
    c1, c2 = "Movie [abc123].chunk.001.mkv", "Movie [abc123].chunk.002.mkv"
    entry = {"filename": "Movie.mkv", "short_id": "abc123", "folder_path": str(folder), "hash": "unused",
             "search_term": "Movie [abc123].mkv",
             "split_info": {"is_split": True, "total_chunks": 2, "chunks": [{"filename": c1, "hash": _sha(PAYLOAD_A)},
                                                                            {"filename": c2, "hash": _sha(PAYLOAD_B)}]}}

    def script(q, i):
        if q == c1:
            return world.browser.start(c1, PAYLOAD_A, 20)
        if q == c2:
            return world.browser.start(c1, PAYLOAD_A, 40)          # the click fetched chunk 1 again
        return world.browser.start(c2, PAYLOAD_B, 30)              # ATTEMPT 2 gets chunk 2

    _script(monkeypatch, script)

    mainfetch.fetch_single_entry(world.browser, entry, entry_id="mov-en-2020-movie", ctx=_session("movies"))

    out = capsys.readouterr().out
    assert "     ⚠️ Duplicate. Deleting." in out and "✅ ENTRY COMPLETE." in out and "RETRY" not in out
    assert sorted(p.name for p in (folder / "restore").iterdir()) == [c1, c2]


def test_chunks_holders_and_extras_of_any_extension_are_collected(world, monkeypatch, capsys):
    folder = world.tmp / "movie"
    folder.mkdir()
    c1, h = "Movie [abc123].chunk.001.mkv", "Movie [abc123].holder.mkv"
    movie = {"filename": "Movie.mkv", "short_id": "abc123", "folder_path": str(folder), "hash": "unused",
             "search_term": "Movie [abc123].mkv",
             "split_info": {"is_split": True, "total_chunks": 1, "chunks": [{"filename": c1, "hash": _sha(PAYLOAD_A)}],
                            "carried_out_tracks": [{"holder_filename": h, "holder_hash": _sha(PAYLOAD_H)}]}}
    title = {"folder_path": str(world.tmp / "Film"), "extras": {"groups": {"Specials": {"items": [
        {"filename": "Trailer.mov", "sub_rel": "Trailer.mov", "hash": _sha(PAYLOAD_C), "uploaded": True,
         "short_id": "e1e1e1", "search_term": "Trailer [e1e1e1].mov"}]}}}}
    [extra] = mainfetch.build_extras_entries(title, "mov-en-2020-movie")
    names = {c1: PAYLOAD_A, h: PAYLOAD_H, "Trailer [e1e1e1].mov": PAYLOAD_C}
    _script(monkeypatch, lambda q, i: world.browser.start(q, names[q], 10) if q in names else False)
    ctx = _session("movies")

    mainfetch.fetch_single_entry(world.browser, movie, entry_id="mov-en-2020-movie", ctx=ctx)
    mainfetch.fetch_single_entry(world.browser, extra, ctx=ctx)

    out = capsys.readouterr().out
    assert sorted(p.name for p in (folder / "restore").iterdir()) == [c1, h]
    specials = world.tmp / "Film" / "Specials" / "restore"
    assert sorted(p.name for p in specials.iterdir()) == ["Trailer.mov"]    # a .mov: today's filter only saw .mkv/.mp4
    assert out.count("✅ ENTRY COMPLETE.") == 2
    assert {r["label"] for r in mvcommon.read_learned("movies")} == {"chunk 1/1", "holder", "extra Specials/Trailer.mov"}


def _route(monkeypatch, browsers):
    """cmd_fetch_route without Chrome: init_driver hands out browsers(profile); no lock file; no socket."""
    launched = []

    def _init(profile_key="movies"):
        launched.append(profile_key)
        return browsers(profile_key)

    monkeypatch.setattr(mainfetch, "init_driver", _init)
    monkeypatch.setattr(mainfetch, "fetch_session_lock", lambda *a, **k: contextlib.nullcontext())
    monkeypatch.setattr(mainfetch, "_debug_port_free", lambda *a, **k: True)
    return launched


def _seed_season(sandbox):
    folder = sandbox["local_root"] / "Series" / "Demo" / "Season 01"
    folder.mkdir(parents=True)
    ep1, ep2 = SEASON + "e01", SEASON + "e02"
    mvcommon.save_library({
        SEASON: {"type": "season_map", "folder_path": str(folder), "total_episodes": 2, "children": [ep1, ep2]},
        ep1: {"filename": "S01E01.mkv", "folder_path": str(folder), "hash": _sha(PAYLOAD_A), "parent_id": SEASON,
              "status": "archived", "uploaded": True, "short_id": "aaa111", "search_term": "S01E01 [aaa111].mkv"},
        ep2: {"filename": "S01E02.mkv", "folder_path": str(folder), "hash": _sha(PAYLOAD_B), "parent_id": SEASON,
              "status": "archived", "uploaded": True, "short_id": "bbb222", "search_term": "S01E02 [bbb222].mkv"}})
    return folder


SEASON_DOWNLOADS = {"S01E01.mkv": ("S01E01 [aaa111].mkv", PAYLOAD_A), "S01E02.mkv": ("S01E02 [bbb222].mkv", PAYLOAD_B)}


def test_a_season_downloads_entry_by_entry_through_one_incoming_folder(world, monkeypatch, capsys, sandbox):
    folder = _seed_season(sandbox)
    incoming = folder / "restore" / ".mv_incoming"
    _script(monkeypatch, lambda q, i: world.browser.start(*SEASON_DOWNLOADS[q], 30) if q in SEASON_DOWNLOADS
            else False)
    _route(monkeypatch, lambda profile: world.browser)

    mainfetch.cmd_fetch_route(SEASON)

    out = capsys.readouterr().out
    redirects = [p["downloadPath"] for c, p in world.browser.cdp if c == "Browser.setDownloadBehavior"]
    assert redirects == [str(incoming), str(incoming)]                    # set before each entry's first request
    assert world.browser.saved == [(str(incoming), "S01E01 [aaa111].mkv"), (str(incoming), "S01E02 [bbb222].mkv")]
    assert sorted(p.name for p in (folder / "restore").iterdir()) == ["S01E01.mkv", "S01E02.mkv"]
    assert list(world.downloads.iterdir()) == []
    web = pytest.importorskip("webui.server")
    assert web._parse_progress(out) == {"done": 2, "total": 2}            # P-13: the web progress is unchanged
    assert out.count("✅ MOVED:") == 2 and out.count("✅ ENTRY COMPLETE.") == 2 and out.count("🔹 PROCESSING:") == 2
    for line in out.splitlines():
        if "Watching" in line or "💽" in line:
            assert not any(marker in line for marker in WEB_MARKERS)


def test_an_entry_split_across_accounts_empties_its_incoming_folder_in_each_session(
        world, monkeypatch, capsys, sandbox):
    from test_fetch_routing import CHUNK_BYTES, MOVIE, _seed_split_movie
    folder, names = _seed_split_movie(sandbox)                 # chunk 3 lives in the TV account
    incoming = folder / "restore" / ".mv_incoming"
    payloads = dict(zip(names, CHUNK_BYTES))
    browsers, seen_at_switch = {}, []

    def browser(profile):
        seen_at_switch.append((profile, incoming.exists()))
        browsers[profile] = _Browser(world.clock, world.downloads, profile=profile)
        return browsers[profile]

    _script(monkeypatch, lambda q, i: _active(browsers).start(q, payloads[q], 30) if q in payloads else False)
    _route(monkeypatch, browser)

    mainfetch.cmd_fetch_route(MOVIE)

    out = capsys.readouterr().out
    assert seen_at_switch == [("movies", False), ("tv", False)]      # emptied and removed when each part ended
    assert [s for s in browsers["movies"].saved] == [(str(incoming), names[0]), (str(incoming), names[1])]
    assert browsers["tv"].saved == [(str(incoming), names[2])]
    assert sorted(p.name for p in (folder / "restore").iterdir()) == sorted(names)
    assert out.count("✅ ENTRY COMPLETE.") == 1


def _active(browsers):
    """The browser of the session now running (the last one handed out)."""
    return list(browsers.values())[-1]


def test_chrome_refusing_the_redirect_falls_back_to_downloads_for_the_rest_of_the_run(
        world, monkeypatch, capsys, sandbox):
    folder = _seed_season(sandbox)
    world.browser.refuse = {"Browser.setDownloadBehavior", "Page.setDownloadBehavior"}
    _script(monkeypatch, lambda q, i: world.browser.start(*SEASON_DOWNLOADS[q], 30) if q in SEASON_DOWNLOADS
            else False)
    _route(monkeypatch, lambda profile: world.browser)

    mainfetch.cmd_fetch_route(SEASON)

    out = capsys.readouterr().out
    assert out.count("Chrome refused the download folder (CDP)") == 1            # one warning for the whole run
    assert [c for c, _ in world.browser.cdp] == ["Browser.setDownloadBehavior", "Page.setDownloadBehavior"]
    assert out.count("   > Watching Downloads (Infinite wait if active)...") == 2
    assert sorted(p.name for p in (folder / "restore").iterdir()) == ["S01E01.mkv", "S01E02.mkv"]
    assert out.count("✅ ENTRY COMPLETE.") == 2 and list(world.downloads.iterdir()) == []


def test_the_fallback_deletes_only_run_new_files_of_the_entrys_family(world, monkeypatch, capsys):
    # P-12: in the ~/Downloads fallback ONLY files that appeared during this run, belong to the entry's expected-name
    # family and failed verification are deleted — everything else stays (run-new ones are listed at the end).
    (world.downloads / "Holiday.mkv").write_bytes(b"F" * 4096)                              # the user's own file
    (world.downloads / "Show.S01E01 [abc123] (1).mkv").write_bytes(PAYLOAD_X)               # an old leftover: family
    (world.downloads / "notes.txt").write_text("mine", encoding="utf-8")
    world.browser.refuse = {"Browser.setDownloadBehavior", "Page.setDownloadBehavior"}
    entry = _whole_entry(world.tmp)

    def script(q, i):
        if q == "Show.S01E01.mkv":                         # ATTEMPT 1's click fetches a wrong item under our name,
            world.browser.start(SHOW_UP, PAYLOAD_X, 10)    # plus another item named like nothing of ours
            return world.browser.start("Random [zzz999].mkv", PAYLOAD_B, 10)
        return world.browser.start(SHOW_UP, PAYLOAD_A, 10)

    _script(monkeypatch, script)
    ctx = _session("tv")

    mainfetch.fetch_single_entry(world.browser, entry, entry_id=EP_ID, ctx=ctx)

    out = capsys.readouterr().out
    assert "     🗑️ Not an expected file — deleted: Show.S01E01 [abc123].mkv (4.00 KB)" in out
    assert "✅ MOVED: Show.S01E01.mkv" in out and "✅ ENTRY COMPLETE." in out
    assert sorted(p.name for p in world.downloads.iterdir()) == [
        "Holiday.mkv", "Random [zzz999].mkv", "Show.S01E01 [abc123] (1).mkv", "notes.txt"]
    assert (world.downloads / "Show.S01E01 [abc123] (1).mkv").read_bytes() == PAYLOAD_X       # never deleted
    assert mainfetch._left_in_downloads_line(ctx) == (
        "   > 📋 Left in Downloads (appeared during this fetch, not provably its own): Random [zzz999].mkv (4.00 KB)")


def test_a_pre_existing_copy_of_a_fetched_object_stays_in_downloads(world, capsys):
    # The fallback never deletes a file that was there before the run — even a byte-identical copy of an object.
    path = world.downloads / "Show.S01E01 [abc123] (2).mkv"
    path.write_bytes(PAYLOAD_A)
    ctx = mainfetch.FetchContext()
    ctx.run_downloads = {mainfetch._family_key(path.name): (4096, 1)}
    target = {"mode": "fallback", "dir": str(world.downloads), "family": [SHOW_UP], "handled": set()}

    mainfetch._drop_unmatched(path.name, str(path), True, False, target, ctx)

    assert path.read_bytes() == PAYLOAD_A
    assert "Duplicate — left in Downloads (not provably this fetch's download)" in capsys.readouterr().out
    assert ctx.left == {}                                   # it was there before: not even listed


def test_the_disk_pre_flight_refuses_an_entry_before_any_request(world, monkeypatch, capsys):
    monkeypatch.setattr(mainfetch.shutil, "disk_usage", lambda path: _Usage(1024))
    entry = _whole_entry(world.tmp)
    calls = _script(monkeypatch, lambda q, i: True)

    mainfetch.fetch_single_entry(world.browser, entry, entry_id=EP_ID, ctx=_session("tv"))

    out = capsys.readouterr().out
    assert calls == [] and world.browser.cdp == []                          # nothing requested, Chrome not even told
    assert "❌ Not enough free space on" in out and "❌ ENTRY INCOMPLETE." in out
    assert sorted(p.name for p in _restore(entry).iterdir()) == []         # no incoming folder was made


def test_a_path_too_long_for_chrome_downloads_through_the_short_folder(world, monkeypatch, capsys):
    monkeypatch.setattr(mainfetch, "INCOMING_PATH_LIMIT", 60)               # any tmp path is "too long" now
    entry = _whole_entry(world.tmp)
    short = pathlib.Path(mainfetch._incoming_location(str(_restore(entry)), [SHOW_UP]))
    _script(monkeypatch, lambda q, i: world.browser.start(SHOW_UP, PAYLOAD_A, 10) if q == SHOW_UP else False)

    mainfetch.fetch_single_entry(world.browser, entry, entry_id=EP_ID, ctx=_session("tv"))

    assert short.parent.name == "MV_incoming" and world.tmp in short.parents   # the autouse tmp root
    assert world.browser.saved == [(str(short), SHOW_UP)]
    assert (_restore(entry) / "Show.S01E01.mkv").read_bytes() == PAYLOAD_A
    assert not short.exists() and not short.parent.exists()                 # its emptied root goes too
    assert "✅ ENTRY COMPLETE." in capsys.readouterr().out


def test_without_any_incoming_folder_the_entry_points_chrome_at_downloads(world, monkeypatch, capsys):
    entry = _whole_entry(world.tmp)
    _restore(entry).mkdir()
    (_restore(entry) / ".mv_incoming").write_bytes(b"a file is in the way")
    _script(monkeypatch, lambda q, i: world.browser.start(SHOW_UP, PAYLOAD_A, 10) if q == SHOW_UP else False)

    mainfetch.fetch_single_entry(world.browser, entry, entry_id=EP_ID, ctx=_session("tv"))

    out = capsys.readouterr().out
    assert "No download folder could be made next to" in out and "refused" not in out
    assert world.browser.cdp[-1][1]["downloadPath"] == str(world.downloads)   # never into an earlier entry's folder
    assert world.browser.saved == [(str(world.downloads), SHOW_UP)]
    assert "✅ MOVED: Show.S01E01.mkv" in out
    assert (_restore(entry) / ".mv_incoming").read_bytes() == b"a file is in the way"   # not ours: untouched


def _held(monkeypatch, *names):
    """Chrome still holds these files (a download still running): deleting them fails."""
    real = mainfetch._delete_download

    def delete(path):
        return False if os.path.basename(path) in names else real(path)

    monkeypatch.setattr(mainfetch, "_delete_download", delete)


# ===========================================================================
# 11.c — the end of an entry: the incoming folder is emptied and removed; what Chrome holds is pending
# ===========================================================================
def test_a_surplus_download_still_running_at_the_end_is_deleted_and_listed(world, monkeypatch, capsys):
    entry = _whole_entry(world.tmp)

    def script(q, i):
        if q != SHOW_UP:
            return False
        world.browser.start("Extra [qqq999].mkv", PAYLOAD_X, 1000)   # a surplus download, still running at the end
        return world.browser.start(SHOW_UP, PAYLOAD_A, 10)

    _script(monkeypatch, script)
    ctx = _session("tv")

    mainfetch.fetch_single_entry(world.browser, entry, entry_id=EP_ID, ctx=ctx)

    out = capsys.readouterr().out
    assert "   > 🧹 Deleted 1 unfinished or surplus download(s): Extra [qqq999].mkv.crdownload (7.00 B)" in out
    assert out.index("✅ MOVED: Show.S01E01.mkv") < out.index("🧹 Deleted") < out.index("✅ ENTRY COMPLETE.")
    assert sorted(p.name for p in _restore(entry).iterdir()) == ["Show.S01E01.mkv"]   # folder empty, so removed
    assert ctx.pending_delete == []
    world.clock.sleep(2000)                               # the surplus transfer ends; nothing reappears
    assert sorted(p.name for p in _restore(entry).iterdir()) == ["Show.S01E01.mkv"]


def test_a_file_chrome_still_holds_is_pending_and_its_folder_stays(world, monkeypatch, capsys):
    entry = _whole_entry(world.tmp)
    _held(monkeypatch, "Extra [qqq999].mkv.crdownload")

    def script(q, i):
        if q != SHOW_UP:
            return False
        world.browser.start("Extra [qqq999].mkv", PAYLOAD_X, 1000)
        return world.browser.start(SHOW_UP, PAYLOAD_A, 10)

    _script(monkeypatch, script)
    ctx = _session("tv")

    mainfetch.fetch_single_entry(world.browser, entry, entry_id=EP_ID, ctx=ctx)

    out = capsys.readouterr().out
    incoming = _restore(entry) / ".mv_incoming"
    held = incoming / "Extra [qqq999].mkv.crdownload"
    assert ctx.pending_delete == [str(held)]
    assert f"   > ⏳ 1 file(s) in {incoming} are still in use by Chrome — deleted once Chrome has closed." in out
    assert sorted(p.name for p in incoming.iterdir()) == [mainfetch.INCOMING_MARKER, held.name]   # kept, marked
    assert "✅ ENTRY COMPLETE." in out


def test_an_earlier_entrys_held_leftover_is_never_taken_for_this_entrys_download(world, monkeypatch, capsys):
    # The episodes of a season share one restore folder, so one incoming folder: E01's surplus download is still
    # running when E02 begins. It is not E02's — never hashed, never counted as a wrong item for IMP-C34.
    folder = world.tmp / "season"
    folder.mkdir()
    incoming = folder / "restore" / ".mv_incoming"
    assert mainfetch._make_incoming(str(incoming))
    leftover = incoming / "S01E01 [aaa111] (1).mkv.crdownload"
    leftover.write_bytes(b"partial")
    stale = incoming / "S01E01 [aaa111].mkv.crdownload"                 # a dead partial: deleted at once
    stale.write_bytes(b"")
    _held(monkeypatch, leftover.name)
    e02 = {"filename": "S01E02.mkv", "short_id": "bbb222", "folder_path": str(folder), "hash": _sha(PAYLOAD_B),
           "search_term": "S01E02 [bbb222].mkv"}
    queue = mainfetch.build_fetch_queue(e02, str(folder / "restore"), entry_id=SEASON + "e02")
    ctx = _session("tv")

    assert mainfetch._prepare_downloads(world.browser, queue, ctx, e02, SEASON + "e02") is True
    out = capsys.readouterr().out
    assert f"   > 🧹 Deleted 1 leftover file(s) from {incoming}: {stale.name} (0 B)" in out
    assert ctx.pending_delete == [str(leftover)]
    leftover.rename(incoming / "S01E01 [aaa111] (1).mkv")               # E01's surplus transfer finishes now
    (incoming / "S01E01 [aaa111] (1).mkv").write_bytes(PAYLOAD_A)
    queue[0]["triggered"] = True                                        # E02's own request never delivers

    result = mainfetch.harvest_downloads(queue, ctx)

    assert result == {"timed_out": True, "saw_download": False, "unmatched": 0}   # so C34 may book E02's loss
    assert "Checking: S01E01" not in capsys.readouterr().out
    mainfetch._finish_downloads(world.browser, ctx)                     # now unheld: deleted, folder removed
    assert not incoming.exists()


def test_a_held_file_in_the_downloads_fallback_is_pending_not_reported_deleted(world, monkeypatch, capsys):
    path = world.downloads / SHOW_UP
    path.write_bytes(PAYLOAD_X)
    _held(monkeypatch, SHOW_UP)
    ctx = mainfetch.FetchContext()
    ctx.run_downloads = {}                                              # it appeared during the run
    target = {"mode": "fallback", "dir": str(world.downloads), "family": [SHOW_UP], "handled": set()}

    mainfetch._drop_unmatched(SHOW_UP, str(path), False, False, target, ctx)

    assert path.exists() and ctx.pending_delete == [str(path)]
    assert "still in use, deleted once Chrome has closed: Show.S01E01 [abc123].mkv (4.00 KB)" in \
        capsys.readouterr().out


# ===========================================================================
# 11.d — the fetch journal
# ===========================================================================
def _journals():
    folder = pathlib.Path(mainfetch.FETCH_JOURNAL_DIR)
    return [json.loads(p.read_text(encoding="utf-8")) for p in sorted(folder.glob("*.json"))] if folder.is_dir() else []


def test_the_run_journal_exists_before_chrome_starts_and_names_the_launched_chrome(world, monkeypatch, sandbox):
    _seed_season(sandbox)
    _script(monkeypatch, lambda q, i: world.browser.start(*SEASON_DOWNLOADS[q], 30) if q in SEASON_DOWNLOADS
            else False)
    world.browser._mv_chrome_proc = types.SimpleNamespace(pid=4242)        # what init_driver keeps (additive)
    world.browser._mv_user_data_dir = mainfetch.CHROME_PROFILES["tv"]
    at_launch = []

    def browser(profile):
        at_launch.append(_journals())
        return world.browser

    _route(monkeypatch, browser)

    mainfetch.cmd_fetch_route(SEASON)

    [[before]] = at_launch
    assert (before["state"], before["sessions"], before["pid"]) == ("running", [], os.getpid())
    [journal] = _journals()
    assert journal["run_id"].startswith("fetch-") and journal["state"] == "clean" and journal["interrupted"] is False
    assert [(s["account"], s["chrome_pid"], s["user_data_dir"]) for s in journal["sessions"]] == [
        ("tv", 4242, mainfetch.CHROME_PROFILES["tv"])]


def test_the_journal_records_each_entrys_folder_names_and_object_states(world, monkeypatch, sandbox):
    folder = _seed_season(sandbox)
    _script(monkeypatch, lambda q, i: world.browser.start(*SEASON_DOWNLOADS[q], 30) if q in SEASON_DOWNLOADS
            else False)
    _route(monkeypatch, lambda profile: world.browser)

    mainfetch.cmd_fetch_route(SEASON)

    [journal] = _journals()
    incoming = str(folder / "restore" / ".mv_incoming")
    assert [(e["manual_id"], e["account"], e["mode"], e["incoming"], e["names"], e["cleaned"])
            for e in journal["entries"]] == [
        (SEASON + "e01", "tv", "incoming", incoming, ["S01E01 [aaa111].mkv"], True),
        (SEASON + "e02", "tv", "incoming", incoming, ["S01E02 [bbb222].mkv"], True)]
    assert journal["entries"][0]["objects"] == [
        {"label": "whole", "sha256": _sha(PAYLOAD_A), "state": "moved", "method": "legacy"}]
    assert journal["mode"] == "incoming" and journal["pending_delete"] == []
    assert "downloads_snapshot" not in journal                          # the incoming mode never read ~/Downloads


def test_a_crash_mid_download_leaves_a_journal_that_names_what_to_clean(world, monkeypatch, sandbox):
    folder = _seed_season(sandbox)
    world.browser._mv_chrome_proc = types.SimpleNamespace(pid=5151)
    crash_time = []

    def script(q, i):
        world.browser.start("S01E01 [aaa111].mkv", PAYLOAD_A, 500)
        crash_time.append(_journals())                                  # what a killed process leaves on disk
        raise KeyboardInterrupt

    _script(monkeypatch, script)
    _route(monkeypatch, lambda profile: world.browser)

    mainfetch.cmd_fetch_route(SEASON)

    [[journal]] = crash_time
    assert journal["state"] == "running" and journal["sessions"][0]["chrome_pid"] == 5151
    [entry] = journal["entries"]
    assert (entry["incoming"], entry["cleaned"]) == (str(folder / "restore" / ".mv_incoming"), False)
    [after] = _journals()
    assert after["interrupted"] is True and after["state"] in ("pending", "clean")


def test_the_journal_never_holds_a_url(world, monkeypatch):
    # P-20: a minted download URL is a short-lived bearer credential — memory only. A stage that keeps one on the item
    # (Step 12's direct download) must not leak it into the journal.
    url = "https://video-downloads.fake.invalid/orig/FAKEORIG0001abc"

    def seam(driver, queue, ctx):
        queue[0]["_trigger"] = {"method": "direct", "url": url, "approach": {"tried": "s0_photo_id", "via": url},
                                "record": {"photo_id": "AF1QipFAKEstep11journal01", "dedup_key": "x", "url": url,
                                           "filename": url}}

    monkeypatch.setattr(mainfetch, "_run_locator_phase", seam)
    _script(monkeypatch, lambda q, i: world.browser.start(SHOW_UP, PAYLOAD_A, 10) if q == SHOW_UP else False)
    ctx = _session("tv")
    ctx.journal = mainfetch.FetchJournal(ctx.run_id)

    mainfetch.fetch_single_entry(world.browser, _whole_entry(world.tmp), entry_id=EP_ID, ctx=ctx)

    text = pathlib.Path(ctx.journal.path).read_text(encoding="utf-8")
    assert "://" not in text and "video-downloads" not in text
    [entry] = json.loads(text)["entries"]
    assert entry["objects"] == [{"label": "whole", "sha256": _sha(PAYLOAD_A), "state": "moved", "method": "direct",
                                 "photo_id": "AF1QipFAKEstep11journal01"}]


@pytest.mark.parametrize("item, state", [
    ({"status": "done"}, "moved"),
    ({"status": "failed", "triggered": True}, "failed"),
    ({"status": "pending", "triggered": True}, "triggered"),
    ({"status": "pending", "triggered": False, "_retrigger": print}, "triggered"),   # requested in an earlier round
    ({"status": "pending", "_trigger": {"record": {"photo_id": "AF1QipFAKEstep11state001"}}}, "located"),
    ({"status": "pending", "_trigger": {"record": {"photo_id": "https://x/y"}}}, "pending"),   # never a URL
    ({"status": "pending"}, "pending"),
])
def test_object_states(item, state):
    assert mainfetch._object_state(item) == state


def test_a_journal_that_cannot_be_written_warns_once_and_the_fetch_goes_on(world, monkeypatch, capsys):
    blocker = world.tmp / "not-a-folder"
    blocker.write_text("x", encoding="utf-8")
    monkeypatch.setattr(mainfetch, "FETCH_JOURNAL_DIR", str(blocker / "fetch_journal"))
    _script(monkeypatch, lambda q, i: world.browser.start(SHOW_UP, PAYLOAD_A, 10) if q == SHOW_UP else False)
    ctx = _session("tv")
    ctx.journal = mainfetch.FetchJournal(ctx.run_id)

    mainfetch.fetch_single_entry(world.browser, _whole_entry(world.tmp), entry_id=EP_ID, ctx=ctx)
    mainfetch._end_run(ctx)

    out = capsys.readouterr().out
    assert out.count("The fetch journal could not be written") == 1
    assert "✅ ENTRY COMPLETE." in out and ctx.journal.broken


def test_a_fallback_run_journals_its_downloads_snapshot(world, monkeypatch):
    (world.downloads / "Holiday.mkv").write_bytes(b"F" * 10)
    world.browser.refuse = {"Browser.setDownloadBehavior", "Page.setDownloadBehavior"}
    _script(monkeypatch, lambda q, i: world.browser.start(SHOW_UP, PAYLOAD_A, 10) if q == SHOW_UP else False)
    ctx = _session("tv")
    ctx.journal = mainfetch.FetchJournal(ctx.run_id)

    mainfetch.fetch_single_entry(world.browser, _whole_entry(world.tmp), entry_id=EP_ID, ctx=ctx)

    journal = json.loads(pathlib.Path(ctx.journal.path).read_text(encoding="utf-8"))
    assert list(journal["downloads_snapshot"]) == ["Holiday.mkv"]
    [entry] = journal["entries"]
    assert (entry["mode"], entry["incoming"], entry["names"]) == ("fallback", None, [SHOW_UP])


# ===========================================================================
# 11.e — the start-up cleanup after an interrupted fetch
# ===========================================================================
TV_PROFILE = mainfetch.CHROME_PROFILES["tv"]
CHROME_TV = (f'"C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe" --user-data-dir={TV_PROFILE} '
             f'--profile-directory=Default --remote-debugging-port=9222 --disable-quic about:blank')


class _Procs:
    """A fake process table: `alive` {pid: command line or None}; every other pid has ended. terminate() ends a
    pid (and lets go of the files it held: on_terminate)."""

    def __init__(self, alive=None):
        self.alive = dict(alive or {})
        self.queried, self.terminated = [], []
        self.on_terminate = lambda pid: None
        self.listener = lambda port: None       # nothing listens on a debug port unless a test says so

    def query(self, pid):
        self.queried.append(pid)
        return (True, self.alive[pid]) if pid in self.alive else (False, None)

    def terminate(self, pid):
        self.terminated.append(pid)
        self.alive.pop(pid, None)
        self.on_terminate(pid)
        return True


def _procs(monkeypatch, alive=None):
    table = _Procs(alive)
    monkeypatch.setattr(mainfetch, "PROCESS_TABLE", table)
    return table


def _held_until_killed(monkeypatch, procs, pid, *names):
    """These files stay held (undeletable) until the Chrome `pid` is terminated."""
    locked = set(names)
    real = mainfetch._delete_download
    monkeypatch.setattr(mainfetch, "_delete_download",
                        lambda path: False if os.path.basename(path) in locked else real(path))
    procs.on_terminate = lambda p: locked.clear() if p == pid else None


def _crashed_journal(run_id, **fields):
    """A journal as a killed fetch leaves it (state running, its own pid 7001)."""
    doc = {"v": 1, "run_id": run_id, "started_at": "2026-10-04T10:00:00Z", "updated_at": "2026-10-04T10:05:00Z",
           "pid": 7001, "state": "running", "interrupted": False, "mode": "incoming", "sessions": [], "entries": [],
           "pending_delete": []}
    doc.update(fields)
    path = pathlib.Path(mainfetch.FETCH_JOURNAL_DIR) / f"{run_id}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc), encoding="utf-8")
    return path


def _incoming_with(folder, *files):
    incoming = folder / "restore" / ".mv_incoming"
    assert mainfetch._make_incoming(str(incoming))
    for name, data in files:
        (incoming / name).write_bytes(data)
    return incoming


def test_the_next_start_terminates_only_the_recorded_chrome_and_cleans_only_named_marked_folders(
        world, monkeypatch, capsys):
    a = _incoming_with(world.tmp / "A", ("S01E01 [aaa111].mkv.crdownload", b"p" * 100), ("Wrong [x].mkv", b"w" * 50))
    unmarked = world.tmp / "B" / "restore" / ".mv_incoming"                  # named, but without the marker
    unmarked.mkdir(parents=True)
    (unmarked / "x.mkv").write_bytes(b"x")
    (a.parent / "keep.mkv").write_bytes(b"k")                                # beside the incoming folder: not ours
    procs = _procs(monkeypatch, {7002: CHROME_TV,                            # the crashed run's Chrome, still running
                                 7003: CHROME_TV.replace(TV_PROFILE, r"C:\Users\x\ChromeProfile_TV2")})
    _held_until_killed(monkeypatch, procs, 7002, "S01E01 [aaa111].mkv.crdownload")
    path = _crashed_journal("fetch-20261004T100000Z-7001", sessions=[
        {"account": "tv", "chrome_pid": 7002, "user_data_dir": TV_PROFILE},
        {"account": "movies", "chrome_pid": 7003, "user_data_dir": TV_PROFILE + "2"}],   # pid 7003 runs another dir
        entries=[{"incoming": str(a), "cleaned": False, "mode": "incoming", "names": []},
                 {"incoming": str(unmarked), "cleaned": False, "mode": "incoming", "names": []}])
    ctx = mainfetch.FetchContext(run_id="fetch-20261004T120000Z-9999")

    with monkeypatch.context() as m:
        _forbid(m, world.downloads)                                           # an incoming run: ~/Downloads untouched
        mainfetch._startup_cleanup(ctx)

    out = capsys.readouterr().out
    assert procs.terminated == [7002]                                         # never 7003: its command line differs
    assert not a.exists() and (a.parent / "keep.mkv").exists()
    assert (unmarked / "x.mkv").exists()                                      # no marker: never emptied
    journal = json.loads(path.read_text(encoding="utf-8"))
    assert (journal["state"], journal["recovered_by"], journal["pending_delete"]) == (
        "recovered", "fetch-20261004T120000Z-9999", [])
    assert "   > 🛑 Closed the Chrome an interrupted fetch left running (pid 7002)." in out
    assert "   > 🧹 Cleaned 2 leftover file(s) from an interrupted fetch (0.0 GB)" in out


@pytest.mark.parametrize("owner_cmdline, live", [
    ("python mainfetch.py fetch tv-en-2001-demo-s01", True),     # another fetch is still running: hands off
    (None, True),                                                 # alive, command line unreadable: cannot tell
    ("C:\\Windows\\notepad.exe", False),                          # the pid was reused: that fetch is gone
])
def test_a_fetch_that_may_still_run_is_left_alone(world, monkeypatch, capsys, owner_cmdline, live):
    a = _incoming_with(world.tmp / "A", ("x.mkv.crdownload", b"p"))
    procs = _procs(monkeypatch, {7001: owner_cmdline, 7002: CHROME_TV})
    path = _crashed_journal("fetch-20261004T100000Z-7001",
                            sessions=[{"account": "tv", "chrome_pid": 7002, "user_data_dir": TV_PROFILE}],
                            entries=[{"incoming": str(a), "cleaned": False, "mode": "incoming", "names": []}])

    mainfetch._startup_cleanup(mainfetch.FetchContext())

    assert (procs.terminated, (a / "x.mkv.crdownload").exists()) == (([], True) if live else ([7002], False))
    assert json.loads(path.read_text(encoding="utf-8"))["state"] == ("running" if live else "recovered")


def test_a_fallback_runs_leftovers_are_only_its_run_new_family_files(world, monkeypatch, capsys):
    pre = {"Holiday.mkv": b"h", "Show.S01E01 [abc123] (1).mkv": b"old"}                   # there before that run
    new = {"Show.S01E01 [abc123].mkv.crdownload": b"p", "Show.S01E01 [abc123] (2).mkv": b"x",   # that run's family
           "Other [zzz999].mkv": b"o", "Unconfirmed 12345.crdownload": b"u", "notes.txt": b"n"}  # never provably ours
    for name, data in {**pre, **new}.items():
        (world.downloads / name).write_bytes(data)
    _procs(monkeypatch)
    path = _crashed_journal("fetch-20261004T100000Z-7001", mode="fallback",
                            downloads_snapshot={name: [len(data), 1] for name, data in pre.items()},
                            entries=[{"incoming": None, "cleaned": True, "mode": "fallback", "names": [SHOW_UP]}])

    mainfetch._startup_cleanup(mainfetch.FetchContext())

    assert sorted(p.name for p in world.downloads.iterdir()) == sorted(
        ["Holiday.mkv", "Show.S01E01 [abc123] (1).mkv", "Other [zzz999].mkv", "Unconfirmed 12345.crdownload",
         "notes.txt"])
    assert json.loads(path.read_text(encoding="utf-8"))["state"] == "recovered"
    assert "Cleaned 2 leftover file(s)" in capsys.readouterr().out


def test_files_still_held_keep_the_journal_pending_for_the_next_start(world, monkeypatch, capsys):
    a = _incoming_with(world.tmp / "A", ("x.mkv.crdownload", b"p"))
    _procs(monkeypatch)                                   # its Chrome is gone, yet something still holds the file
    _held(monkeypatch, "x.mkv.crdownload")
    path = _crashed_journal("fetch-20261004T100000Z-7001", state="pending",
                            entries=[{"incoming": str(a), "cleaned": False, "mode": "incoming", "names": []}])

    mainfetch._startup_cleanup(mainfetch.FetchContext(run_id="fetch-20261004T110000Z-1"))

    journal = json.loads(path.read_text(encoding="utf-8"))
    assert (journal["state"], journal["pending_delete"]) == ("pending", [str(a / "x.mkv.crdownload")])
    assert "1 leftover file(s) of an interrupted fetch are still in use — the next fetch tries again." in \
        capsys.readouterr().out
    monkeypatch.setattr(mainfetch, "_delete_download", lambda p: (os.remove(p), True)[1])   # let go at last

    mainfetch._startup_cleanup(mainfetch.FetchContext(run_id="fetch-20261004T120000Z-2"))

    assert json.loads(path.read_text(encoding="utf-8"))["state"] == "recovered" and not a.exists()
    assert "Cleaned 1 leftover file(s)" in capsys.readouterr().out


def test_finished_journals_need_nothing_and_an_unreadable_one_is_left_alone(world, monkeypatch, capsys):
    procs = _procs(monkeypatch, {7002: CHROME_TV})
    for state in ("clean", "recovered"):
        _crashed_journal(f"fetch-2026100{4 if state == 'clean' else 3}T100000Z-1", state=state,
                         sessions=[{"account": "tv", "chrome_pid": 7002, "user_data_dir": TV_PROFILE}])
    bad = pathlib.Path(mainfetch.FETCH_JOURNAL_DIR) / "fetch-20261004T090000Z-2.json"
    bad.write_text("{not json", encoding="utf-8")

    mainfetch._startup_cleanup(mainfetch.FetchContext())

    assert procs.queried == [] and procs.terminated == []
    assert bad.read_text(encoding="utf-8") == "{not json"
    assert capsys.readouterr().out == "   > ⚠️ An unreadable fetch journal was left alone: fetch-20261004T090000Z-2.json\n"


def test_a_journal_naming_foreign_places_deletes_nothing(world, monkeypatch, capsys):
    # Every delete asks the folder's name AND marker, or the run's own ~/Downloads snapshot: a damaged journal that
    # names arbitrary places cannot delete there.
    documents = world.tmp / "Documents"
    documents.mkdir()
    (documents / "thesis.docx").write_bytes(b"d")
    (documents / mainfetch.INCOMING_MARKER).write_text("x", encoding="utf-8")        # a marker alone is not enough
    (world.downloads / "Holiday.mkv").write_bytes(b"h")
    _procs(monkeypatch)
    _crashed_journal("fetch-20261004T100000Z-7001", downloads_snapshot={"Holiday.mkv": [1, 1]},
                     entries=[{"incoming": str(documents), "cleaned": False, "mode": "fallback", "names": ["Holiday.mkv"]}],
                     pending_delete=[str(documents / "thesis.docx"), str(world.downloads / "Holiday.mkv"),
                                     str(world.tmp / "elsewhere.mkv")])
    (world.tmp / "elsewhere.mkv").write_bytes(b"e")

    mainfetch._startup_cleanup(mainfetch.FetchContext())

    assert (documents / "thesis.docx").exists() and (world.downloads / "Holiday.mkv").exists()
    assert (world.tmp / "elsewhere.mkv").exists()
    assert "Cleaned" not in capsys.readouterr().out


@pytest.mark.parametrize("cmdline, expected", [
    (CHROME_TV, TV_PROFILE),
    (f'chrome.exe "--user-data-dir={TV_PROFILE}" --x', TV_PROFILE),                 # quoted whole argument
    (f'chrome.exe --user-data-dir="C:\\Media\\Utils\\Chrome Profile" --x', r"C:\Media\Utils\Chrome Profile"),
    ("chrome.exe --profile-directory=Default", None),
])
def test_the_profile_a_command_line_names(cmdline, expected):
    got = mainfetch._cmdline_user_data_dir(cmdline)
    assert got == (os.path.normcase(os.path.abspath(expected)) if expected else None)


def test_only_a_chrome_on_exactly_that_profile_is_ours(monkeypatch):
    _procs(monkeypatch, {1: CHROME_TV, 2: CHROME_TV.replace(TV_PROFILE, TV_PROFILE + "2"), 3: None,
                         4: CHROME_TV.upper()})
    assert mainfetch._chrome_still_ours(1, TV_PROFILE) is True
    assert mainfetch._chrome_still_ours(2, TV_PROFILE) is False          # ChromeProfile_TV2 shares a prefix only
    assert mainfetch._chrome_still_ours(3, TV_PROFILE) is False          # command line unknown: never killed
    assert mainfetch._chrome_still_ours(4, TV_PROFILE) is True           # Windows paths: case-insensitive
    assert mainfetch._chrome_still_ours(5, TV_PROFILE) is False          # gone
    assert mainfetch._chrome_still_ours(None, TV_PROFILE) is False


class _Run:
    def __init__(self, returncode=0, stdout=""):
        self.returncode, self.stdout = returncode, stdout


@pytest.mark.parametrize("answer, expected", [
    (_Run(0, "ALIVE\r\nchrome.exe --user-data-dir=C:\\x\r\n"), (True, "chrome.exe --user-data-dir=C:\\x")),
    (_Run(0, "\ufeffALIVE\n\n"), (True, None)),
    (_Run(0, "GONE\n"), (False, None)),
    (_Run(1, "GONE\n"), (None, None)),
    (_Run(0, "Get-CimInstance : Access denied\n"), (None, None)),
    (OSError("powershell not found"), (None, None)),
])
def test_the_windows_process_query_never_guesses(monkeypatch, answer, expected):
    seen = []

    def run(argv, **kwargs):
        seen.append(argv)
        if isinstance(answer, Exception):
            raise answer
        return answer

    monkeypatch.setattr(mainfetch.subprocess, "run", run)
    assert mainfetch._ProcessTable().query(4321) == expected
    assert seen[0][:4] == ["powershell", "-NoProfile", "-NonInteractive", "-Command"]
    assert "ProcessId=4321" in seen[0][4]
    assert mainfetch._ProcessTable().query("4321; rm -r") == (None, None)       # only an int ever reaches a shell


def test_the_windows_terminate_ends_the_process_tree(monkeypatch):
    seen = []
    monkeypatch.setattr(mainfetch.subprocess, "run", lambda argv, **k: seen.append(argv) or _Run(0))
    assert mainfetch._ProcessTable().terminate(4321) is True
    assert seen == [["taskkill", "/PID", "4321", "/T", "/F"]]
    assert mainfetch._ProcessTable().terminate(0) is False and len(seen) == 1


def test_the_start_up_cleanup_runs_inside_the_lock_after_the_pre_flight_before_chrome(world, monkeypatch, sandbox):
    _seed_season(sandbox)
    events = []

    @contextlib.contextmanager
    def lock(*a, **k):
        events.append("lock")
        yield
        events.append("unlock")

    real_preflight = mainfetch.profile_preflight
    monkeypatch.setattr(mainfetch, "fetch_session_lock", lock)
    monkeypatch.setattr(mainfetch, "profile_preflight", lambda a: events.append("preflight") or real_preflight(a))
    monkeypatch.setattr(mainfetch, "_startup_cleanup", lambda ctx: events.append("cleanup"))
    monkeypatch.setattr(mainfetch, "init_driver", lambda p="movies": events.append("init") or world.browser)
    _script(monkeypatch, lambda q, i: False)

    mainfetch.cmd_fetch_route(SEASON)

    assert events[:4] == ["lock", "preflight", "cleanup", "init"] and events[-1] == "unlock"


def test_no_start_up_cleanup_when_the_profile_is_missing(world, monkeypatch, sandbox, tmp_path):
    _seed_season(sandbox)
    ran = []
    monkeypatch.setattr(mainfetch, "CHROME_PROFILES", {**mainfetch.CHROME_PROFILES, "tv": str(tmp_path / "nope")})
    monkeypatch.setattr(mainfetch, "_startup_cleanup", lambda ctx: ran.append(ctx))
    _route(monkeypatch, lambda profile: world.browser)

    mainfetch.cmd_fetch_route(SEASON)

    assert ran == [] and _journals() == []


# ===========================================================================
# 11.f — an interrupted run cleans up after itself; a finished one releases what Chrome held
# ===========================================================================
class _Popen:
    """The Popen handle init_driver keeps: poll() is None while the launched chrome.exe runs."""

    def __init__(self, pid, running=True):
        self.pid, self.running = pid, running

    def poll(self):
        return None if self.running else 0


def _launched(browser, procs, pid, running=True):
    browser._mv_chrome_proc = _Popen(pid, running)
    browser._mv_user_data_dir = TV_PROFILE
    procs.alive[pid] = CHROME_TV


@pytest.mark.parametrize("stop, line", [(KeyboardInterrupt, "🛑 Stopped by user."),
                                        (RuntimeError("selenium went away"), "❌ Critical Error: selenium went away")])
def test_an_interrupted_run_terminates_its_chrome_and_cleans_its_incoming_leftovers(
        world, monkeypatch, capsys, sandbox, stop, line):
    folder = _seed_season(sandbox)
    incoming = folder / "restore" / ".mv_incoming"
    procs = _procs(monkeypatch)
    _launched(world.browser, procs, 6060)
    _held_until_killed(monkeypatch, procs, 6060, "S01E01 [aaa111].mkv.crdownload")

    def script(q, i):
        world.browser.start("S01E01 [aaa111].mkv", PAYLOAD_A, 900)     # still downloading when the run is stopped
        raise stop

    _script(monkeypatch, script)
    _route(monkeypatch, lambda profile: world.browser)

    mainfetch.cmd_fetch_route(SEASON)

    out = capsys.readouterr().out
    assert procs.terminated == [6060] and procs.queried == []           # the kept handle proves the pid is ours
    assert out.index(line) < out.index("   > 🛑 Chrome terminated (pid 6060) — its downloads stopped.") < out.index(
        "   > 🧹 Cleaned 1 leftover file(s) of this interrupted fetch (0.0 GB)")
    assert not incoming.exists()
    [journal] = _journals()
    assert (journal["state"], journal["interrupted"], journal["pending_delete"]) == ("clean", True, [])
    assert journal["entries"][0]["cleaned"] is True


def test_what_chrome_still_holds_after_an_interruption_waits_for_the_next_start(world, monkeypatch, capsys, sandbox):
    folder = _seed_season(sandbox)
    incoming = folder / "restore" / ".mv_incoming"
    partial = incoming / "S01E01 [aaa111].mkv.crdownload"
    procs = _procs(monkeypatch)
    _launched(world.browser, procs, 6060, running=False)    # handed over to an earlier Chrome: no pid to terminate
    _held(monkeypatch, partial.name)
    world.browser._mv_chrome_proc = _Popen(6060, running=False)

    def script(q, i):
        world.browser.start("S01E01 [aaa111].mkv", PAYLOAD_A, 900)
        raise KeyboardInterrupt

    _script(monkeypatch, script)
    _route(monkeypatch, lambda profile: world.browser)

    mainfetch.cmd_fetch_route(SEASON)

    out = capsys.readouterr().out
    assert procs.terminated == [] and "Chrome terminated" not in out
    assert "   > ⏳ 1 file(s) are still in use — the next fetch deletes them." in out
    [journal] = _journals()
    assert (journal["state"], journal["pending_delete"]) == ("pending", [str(partial)])
    monkeypatch.setattr(mainfetch, "_delete_download", lambda p: (os.remove(p), True)[1])   # Chrome let go

    mainfetch._startup_cleanup(mainfetch.FetchContext(run_id="fetch-29991231T000000Z-1"))   # the next run (one
                                                                                             # per process for real)
    assert not incoming.exists() and _journals()[0]["state"] == "recovered"


def test_a_finished_run_closes_chrome_to_delete_what_it_still_held(world, monkeypatch, capsys):
    entry = _whole_entry(world.tmp)
    procs = _procs(monkeypatch)
    _launched(world.browser, procs, 6161)
    _held_until_killed(monkeypatch, procs, 6161, "Extra [qqq999].mkv.crdownload")

    def script(q, i):
        if q != SHOW_UP:
            return False
        world.browser.start("Extra [qqq999].mkv", PAYLOAD_X, 5000)    # a surplus download, still running at the end
        return world.browser.start(SHOW_UP, PAYLOAD_A, 10)

    _script(monkeypatch, script)
    ctx = _session("tv")
    ctx.journal = mainfetch.FetchJournal(ctx.run_id)
    mainfetch._note_session(ctx, world.browser, "tv")
    mainfetch.fetch_single_entry(world.browser, entry, entry_id=EP_ID, ctx=ctx)
    assert ctx.pending_delete

    mainfetch._end_run(ctx)

    out = capsys.readouterr().out
    assert procs.terminated == [6161]
    assert "   > 🛑 Chrome terminated (pid 6161) — it still held files of this fetch." in out
    assert "   > 🧹 Cleaned 1 leftover file(s) Chrome had held (0.0 GB)" in out
    assert not (_restore(entry) / ".mv_incoming").exists()
    journal = json.loads(pathlib.Path(ctx.journal.path).read_text(encoding="utf-8"))
    assert (journal["state"], journal["interrupted"], journal["pending_delete"]) == ("clean", False, [])


def test_a_run_that_left_nothing_never_touches_chrome(world, monkeypatch, sandbox):
    _seed_season(sandbox)
    procs = _procs(monkeypatch)
    _launched(world.browser, procs, 6262)
    _script(monkeypatch, lambda q, i: world.browser.start(*SEASON_DOWNLOADS[q], 30) if q in SEASON_DOWNLOADS
            else False)
    _route(monkeypatch, lambda profile: world.browser)

    mainfetch.cmd_fetch_route(SEASON)

    assert procs.terminated == [] and procs.queried == []
    assert _journals()[0]["state"] == "clean" and _journals()[0]["sessions"][0]["chrome_pid"] == 6262


def test_an_interrupted_fallback_run_deletes_only_its_own_family_partials(world, monkeypatch, capsys):
    (world.downloads / "Holiday.mkv").write_bytes(b"h")
    (world.downloads / "Show.S01E01 [abc123] (1).mkv").write_bytes(b"old")        # an older leftover: not this run's
    world.browser.refuse = {"Browser.setDownloadBehavior", "Page.setDownloadBehavior"}
    procs = _procs(monkeypatch)
    _launched(world.browser, procs, 6363)

    def script(q, i):
        world.browser.start(SHOW_UP, PAYLOAD_A, 900)
        world.browser.start("Other [zzz999].mkv", PAYLOAD_B, 900)
        raise KeyboardInterrupt

    _script(monkeypatch, script)
    ctx = _session("tv")
    ctx.journal = mainfetch.FetchJournal(ctx.run_id)
    mainfetch._note_session(ctx, world.browser, "tv")
    with pytest.raises(KeyboardInterrupt):
        mainfetch.fetch_single_entry(world.browser, _whole_entry(world.tmp), entry_id=EP_ID, ctx=ctx)

    mainfetch._end_run(ctx, interrupted=True)

    assert sorted(p.name for p in world.downloads.iterdir()) == [
        "Holiday.mkv", "Other [zzz999].mkv.crdownload", "Show.S01E01 [abc123] (1).mkv"]
    assert "Cleaned 1 leftover file(s) of this interrupted fetch" in capsys.readouterr().out


def test_a_chrome_that_handed_over_is_tracked_by_its_debug_port_listener(world, monkeypatch):
    procs = _procs(monkeypatch, {8080: CHROME_TV, 9090: CHROME_TV.replace(TV_PROFILE, r"C:\Other\Profile")})
    world.browser._mv_chrome_proc = _Popen(1111, running=False)       # chrome.exe exited at once: a hand-over
    world.browser._mv_user_data_dir = TV_PROFILE
    for listener, recorded in ((8080, 8080), (9090, None), (None, None)):
        procs.listener = lambda port, _pid=listener: _pid if port == 9222 else None
        ctx = mainfetch.FetchContext()
        mainfetch._note_session(ctx, world.browser, "tv")
        assert (ctx.chrome["pid"], ctx.chrome["proc"]) == (recorded, None)


@pytest.mark.parametrize("stdout, code, expected", [("4242\r\n", 0, 4242), ("", 0, None), ("abc\n", 0, None),
                                                    ("4242\n", 1, None), ("0\n", 0, None)])
def test_the_windows_debug_port_query_never_guesses(monkeypatch, stdout, code, expected):
    seen = []
    monkeypatch.setattr(mainfetch.subprocess, "run", lambda argv, **k: seen.append(argv) or _Run(code, stdout))
    assert mainfetch._ProcessTable().listener(9222) == expected
    assert "Get-NetTCPConnection -LocalPort 9222 -State Listen" in seen[0][4]


# ===========================================================================
# 11.g — fetch_cleanup [--apply]
# ===========================================================================
MOVIE_ID, LOCAL_ID, ALIAS_ID = "mov-en-2020-film", "mov-en-2021-local", SEASON + "e03"


@pytest.fixture()
def lock_in_tmp(tmp_path, monkeypatch):
    """The fetch lock file under tmp_path (never ~/.mediavault/locks)."""
    lock_dir = tmp_path / "mv_locks"
    monkeypatch.setattr(mvcommon, "MV_LOCK_DIR", str(lock_dir))
    monkeypatch.setattr(mvcommon, "FETCH_SESSION_LOCK", str(lock_dir / "fetch_session.lock"))
    return lock_dir / "fetch_session.lock"


def _tree(root):
    """{path: "dir" | (size, mtime_ns)} of everything under root."""
    out = {}
    for dirpath, dirnames, filenames in os.walk(root):
        for d in dirnames:
            out[os.path.join(dirpath, d)] = "dir"
        for f in filenames:
            st = os.stat(os.path.join(dirpath, f))
            out[os.path.join(dirpath, f)] = (st.st_size, st.st_mtime_ns)
    return out


def _seed_cleanup_world(sandbox, world):
    """A library (a season with an alias and an extra, a split movie, a local-only movie) plus everything a cleanup
    may meet; returns (expected plan paths, things that must survive)."""
    root = sandbox["local_root"]
    season, movie, local = (root / "Series" / "Demo" / "Season 01", root / "Movies" / "Film (2020)",
                            root / "Movies" / "Local (2021)")
    for folder in (season, movie, local):
        folder.mkdir(parents=True)
    ep1, ep2 = SEASON + "e01", SEASON + "e02"
    archived = {"status": "archived", "uploaded": True}
    mvcommon.save_library({
        SEASON: {"type": "season_map", "folder_path": str(season), "total_episodes": 3, "children": [ep1, ep2, ALIAS_ID],
                 "extras": {"groups": {"Specials": {"items": [dict(
                     archived, filename="Trailer.mkv", sub_rel="Trailer.mkv", hash=_sha("trailer"), short_id="e1e1e1",
                     search_term="Trailer [e1e1e1].mkv")]}}}},
        ep1: dict(archived, filename="S01E01.mkv", folder_path=str(season), hash=_sha("e1"), parent_id=SEASON,
                  short_id="aaa111"),
        ep2: dict(archived, filename="S01E02.mkv", folder_path=str(season), hash=_sha("e2"), parent_id=SEASON,
                  short_id="bbb222"),
        ALIAS_ID: {"type": "multi_ep_alias", "alias_of": ep2, "parent_id": SEASON},
        MOVIE_ID: dict(archived, filename="Film.mkv", folder_path=str(movie), hash=_sha("film"), short_id="f1f1f1",
                       split_info={"is_split": True, "total_chunks": 2, "chunks": [
                           {"filename": "Film [f1f1f1].chunk.001.mkv", "hash": _sha("c1")},
                           {"filename": "Film [f1f1f1].chunk.002.mkv", "hash": _sha("c2")}]}),
        LOCAL_ID: {"filename": "Local.mkv", "folder_path": str(local), "hash": _sha("local"), "short_id": "l0l0l0",
                   "status": "local_ready", "uploaded": False},
    })
    d = world.downloads
    expected = [d / "S01E01 [aaa111].mkv", d / "S01E02 [bbb222] (1).mkv", d / "Film [f1f1f1].chunk.002.mkv.crdownload",
                d / "Trailer [e1e1e1].mkv"]
    survive = [d / "Local [l0l0l0].mkv",          # never uploaded: no download is named like it
               d / "S01E01.mkv", d / "Holiday.mkv", d / "Unconfirmed 123.crdownload", d / "notes.txt"]
    for path in expected + survive:
        path.write_bytes(b"leftover")
    (d / "S01E01 [aaa111] (2).mkv").mkdir()       # a folder, even with such a name, is never a leftover file
    season_in = _incoming_with(season, ("S01E02 [bbb222].mkv.crdownload", b"partial"))
    (season / "restore" / "S01E01.mkv").write_bytes(b"staged")                     # beside it: a restore file
    extras_in = _incoming_with(season / "Specials")                                # marked, empty
    unmarked = movie / "restore" / ".mv_incoming"                                  # no marker: never touched
    unmarked.mkdir(parents=True)
    (unmarked / "x.mkv").write_bytes(b"x")
    temp_in = _incoming_with(world.tmp / "T" / "tv-en-2001-demo-s01e01", ("S01E01 [aaa111].mkv.crdownload", b"p"))
    short_root = pathlib.Path(mainfetch._short_incoming_root(str(season)))
    short_in = short_root / "0a1b2c3d"
    assert mainfetch._make_incoming(str(short_in))
    (short_in / "y.mkv").write_bytes(b"y")
    (short_root / "mine").mkdir()
    (short_root / "mine" / "z.txt").write_bytes(b"z")
    _crashed_journal("fetch-20261004T100000Z-7001", entries=[
        {"incoming": str(temp_in), "cleaned": False, "mode": "incoming", "names": []}])    # only a journal knows it
    _crashed_journal("fetch-20261003T100000Z-7000", state="clean",
                     entries=[{"incoming": str(world.tmp / "gone"), "cleaned": True, "mode": "incoming"}])
    expected += [season_in / "S01E02 [bbb222].mkv.crdownload", temp_in / "S01E01 [aaa111].mkv.crdownload",
                 short_in / "y.mkv"]
    survive += [season / "restore" / "S01E01.mkv", unmarked / "x.mkv", short_root / "mine" / "z.txt"]
    return {"expected": sorted(str(p) for p in expected), "survive": survive, "folders": [season_in, extras_in, temp_in,
            short_in], "unmarked": [unmarked, short_root / "mine"]}


def test_the_fetch_cleanup_dry_run_lists_exactly_the_leftovers_and_writes_nothing(
        world, sandbox, lock_in_tmp, monkeypatch, capsys):
    seeded = _seed_cleanup_world(sandbox, world)
    _procs(monkeypatch)
    import main
    before = _tree(world.tmp)

    plan = main.cmd_fetch_cleanup()

    assert _tree(world.tmp) == before and not lock_in_tmp.exists()      # writes nothing: no file, folder or lock
    assert sorted(f["path"] for f in plan["files"]) == seeded["expected"]
    assert sorted(plan["folders"]) == sorted(str(p) for p in seeded["folders"])
    assert sorted(plan["skipped"]) == sorted(str(p) for p in seeded["unmarked"])
    assert [j["doc"]["run_id"] for j in plan["journals"]] == ["fetch-20261004T100000Z-7001"]
    out = capsys.readouterr().out
    assert out.startswith("=== FETCH CLEANUP — dry run: nothing is deleted ===")
    assert "     S01E01 [aaa111].mkv · 8.00 B · named like tv-en-2001-demo-s01e01 (whole)" in out
    assert "     Trailer [e1e1e1].mkv · 8.00 B · named like tv-en-2001-demo-s01 (extra Specials/Trailer.mkv)" in out
    assert "   Total: 7 file(s), " in out and "python main.py fetch_cleanup --apply" in out


def test_fetch_cleanup_apply_deletes_exactly_the_dry_run_list(world, sandbox, lock_in_tmp, monkeypatch, capsys):
    seeded = _seed_cleanup_world(sandbox, world)
    _procs(monkeypatch)                                        # the journal's fetch (pid 7001) is gone
    import main
    listed = sorted(f["path"] for f in main.cmd_fetch_cleanup()["files"])
    before = _tree(world.tmp)

    result = main.cmd_fetch_cleanup(apply=True)

    assert sorted(p for p, _ in result["deleted"]) == listed == seeded["expected"]
    after = _tree(world.tmp)
    gone = sorted(set(before) - set(after))
    marked = [str(p) for p in seeded["folders"]] + [str(p / mainfetch.INCOMING_MARKER) for p in seeded["folders"]]
    assert gone == sorted(listed + marked)                    # the files listed + the emptied marked folders, no more
    assert all(p.exists() for p in seeded["survive"]) and all(p.exists() for p in seeded["unmarked"])
    assert (result["folders_removed"], result["journals_recovered"]) == (4, 1)
    assert [json.loads(p.read_text(encoding="utf-8"))["state"]
            for p in sorted(pathlib.Path(mainfetch.FETCH_JOURNAL_DIR).glob("*.json"))] == ["clean", "recovered"]
    assert not lock_in_tmp.exists()                           # the lock is released again
    assert "✅ Deleted 7 file(s)" in capsys.readouterr().out


def test_fetch_cleanup_apply_re_checks_every_file_before_deleting(world, sandbox, lock_in_tmp, monkeypatch, capsys):
    seeded = _seed_cleanup_world(sandbox, world)
    plan = mainfetch.fetch_cleanup_plan(mvcommon.load_library())
    grown, vanished = world.downloads / "S01E01 [aaa111].mkv", world.downloads / "Trailer [e1e1e1].mkv"
    grown.write_bytes(b"grew since the listing")
    vanished.unlink()
    season_in = seeded["folders"][0]
    (season_in / mainfetch.INCOMING_MARKER).unlink()          # no longer a MediaVault download folder

    result = mainfetch.fetch_cleanup_apply(plan)

    kept = dict(result["kept"])
    assert kept == {str(grown): "changed since it was listed", str(vanished): "already gone",
                    str(season_in / "S01E02 [bbb222].mkv.crdownload"):
                        "its folder is no longer a MediaVault download folder"}
    assert grown.exists() and (season_in / "S01E02 [bbb222].mkv.crdownload").exists()
    assert len(result["deleted"]) == 4


def test_fetch_cleanup_apply_is_refused_while_a_fetch_holds_the_lock(world, sandbox, lock_in_tmp, monkeypatch, capsys):
    seeded = _seed_cleanup_world(sandbox, world)
    lock_in_tmp.parent.mkdir(parents=True)
    lock_in_tmp.write_text(f"{os.getpid()} 0\n", encoding="utf-8")          # held, and fresh
    import main
    before = _tree(world.tmp)

    assert main.cmd_fetch_cleanup(apply=True) is None

    assert _tree(world.tmp) == before
    assert "❌ A fetch is running (the fetch lock is held)" in capsys.readouterr().out
    assert main.cmd_fetch_cleanup()["lock"] is True                      # the dry run says so too
    assert "A fetch seems to be running" in capsys.readouterr().out
    assert all(p.exists() for p in map(pathlib.Path, seeded["expected"]))


def test_fetch_cleanup_apply_leaves_a_running_fetchs_folder_alone(world, sandbox, lock_in_tmp, monkeypatch, capsys):
    _seed_cleanup_world(sandbox, world)
    _procs(monkeypatch, {7001: "python mainfetch.py fetch tv-en-2001-demo-s01"})   # that journal's fetch still runs
    temp_partial = world.tmp / "T" / "tv-en-2001-demo-s01e01" / "restore" / ".mv_incoming" / \
        "S01E01 [aaa111].mkv.crdownload"
    import main

    result = main.cmd_fetch_cleanup(apply=True)

    assert temp_partial.exists() and str(temp_partial) not in [p for p, _ in result["deleted"]]
    assert "Left alone — a fetch still running: fetch-20261004T100000Z-7001" in capsys.readouterr().out
    assert result["journals_recovered"] == 0


def test_the_fetch_cleanup_command_line():
    from test_prep_push_rep_enrich_cli import parse_argv
    assert parse_argv(["main.py", "fetch_cleanup"]) == ("cmd_fetch_cleanup", {"apply": False})
    assert parse_argv(["main.py", "fetch_cleanup", "--apply"]) == ("cmd_fetch_cleanup", {"apply": True})
    with pytest.raises(SystemExit):
        parse_argv(["main.py", "fetch_cleanup", "--aply"])           # a typo never runs a cleanup


# ===========================================================================
# 11.h — verify_restore tempdir; restore / verify_restore and a .mv_incoming folder
# ===========================================================================
def _seed_restore_case(sandbox, make_video, base, staged_in=None):
    """A whole-file episode and a 2-chunk movie whose files sit in their restore folders (or in `staged_in`'s
    tempdir layout, temp_dir/<safe id>/restore). Returns {id: restore folder}."""
    import main
    whole_dir, split_dir = base / "Series" / "Show" / "Season 01", base / "Movies" / "Film (2020)"
    whole_id, split_id = "tv-en-2001-show-s01e01", "mov-en-2020-film"
    for folder in (whole_dir, split_dir):
        folder.mkdir(parents=True)
    restore = {}
    for mid, folder in ((whole_id, whole_dir), (split_id, split_dir)):
        restore[mid] = pathlib.Path(main._parts_base(str(folder), str(staged_in) if staged_in else None, mid)[0],
                                    "restore")
        restore[mid].mkdir(parents=True)
    _, whole_hash = make_video(restore[whole_id] / "Show.S01E01.mkv", marker=b"WHOLE\n")
    make_video(whole_dir / "Show.S01E01.mkv", marker=b"DUMMY-SLOT\n")         # what the restore replaces
    chunks = []
    for n in (1, 2):
        name = f"Film [f1f1f1].chunk.00{n}.mkv"
        _, h = make_video(restore[split_id] / name, marker=f"CHUNK{n}\n".encode())
        chunks.append({"filename": name, "hash": h})
    mvcommon.save_library({
        whole_id: {"filename": "Show.S01E01.mkv", "folder_path": str(whole_dir), "hash": whole_hash,
                   "short_id": "abc123", "status": "archived", "uploaded": True},
        split_id: {"filename": "Film.mkv", "folder_path": str(split_dir), "hash": _sha("film"), "short_id": "f1f1f1",
                   "status": "archived", "uploaded": True,
                   "split_info": {"is_split": True, "total_chunks": 2, "chunks": chunks}}})
    return restore


def test_verify_restore_reads_the_tempdir_location_like_restore(sandbox, make_video, tmp_path, capsys):
    import main
    temp = tmp_path / "T"
    temp.mkdir()
    _seed_restore_case(sandbox, make_video, sandbox["local_root"], staged_in=temp)

    main.cmd_verify_restore("mov-en-2020-film", temp_dir=str(temp))
    out = capsys.readouterr().out
    assert "✅ Verified: Film [f1f1f1].chunk.001.mkv" in out and "✅ SUCCESS: All chunks verified." in out
    main.cmd_verify_restore("tv-en-2001-show-s01e01", temp_dir=str(temp))
    assert "✅ SUCCESS: Verified against Master Hash." in capsys.readouterr().out
    main.cmd_verify_restore("mov-en-2020-film")                         # without tempdir: today's folder
    assert "❌ Error: Restore folder missing at:" in capsys.readouterr().out


def test_verify_restore_refuses_a_temp_dir_that_is_not_there(sandbox, make_video, tmp_path, capsys):
    import main
    _seed_restore_case(sandbox, make_video, sandbox["local_root"])
    main.cmd_verify_restore("mov-en-2020-film", temp_dir=str(tmp_path / "nope"))
    out = capsys.readouterr().out
    assert "❌ temp dir does not exist or is not a directory:" in out and "Detected" not in out


def test_the_verify_restore_command_line_takes_tempdir():
    from test_prep_push_rep_enrich_cli import parse_argv
    assert parse_argv(["main.py", "verify_restore", "tv-x"]) == ("cmd_verify_restore",
                                                                  {"manual_id": "tv-x", "temp_dir": None})
    assert parse_argv(["main.py", "verify_restore", "tv-x", "tempdir", "D:\\t"]) == (
        "cmd_verify_restore", {"manual_id": "tv-x", "temp_dir": "D:\\t"})


def test_restore_and_verify_restore_do_not_care_about_an_incoming_folder(sandbox, make_video, tmp_path, monkeypatch,
                                                                         capsys):
    # PLAN Step 11 tests: identical results with no, an empty, or a leftover .mv_incoming folder in restore/ — both
    # address their files by name. The one difference, documented: restore's empty-folder cleanup leaves a restore
    # folder that still holds .mv_incoming (the next fetch or fetch_cleanup removes it).
    import main

    def concat(chunk_paths, output_path, seed=None, **kwargs):               # mkvmerge stand-in
        with open(output_path, "wb") as out:
            for path in chunk_paths:
                with open(path, "rb") as fh:
                    out.write(fh.read())
        return True

    monkeypatch.setattr(main, "merge_video_files", concat)
    monkeypatch.setattr(main, "_current_merge_tool", lambda: "mkvmerge vTEST")
    results = {}
    for variant in ("none", "empty", "leftover"):
        base = tmp_path / variant
        restore = _seed_restore_case(sandbox, make_video, base)
        for folder in restore.values():
            if variant != "none":
                assert mainfetch._make_incoming(str(folder / ".mv_incoming"))
            if variant == "leftover":
                (folder / ".mv_incoming" / "x.mkv.crdownload").write_bytes(b"partial")
        capsys.readouterr()
        outputs = []
        for mid in restore:
            main.cmd_verify_restore(mid)
            outputs.append(capsys.readouterr().out)
            outputs.append(main.cmd_restore(mid))
            outputs.append(capsys.readouterr().out)
        library = mvcommon.load_library()
        results[variant] = (outputs, {mid: (e["status"], e.get("re_hashed")) for mid, e in library.items()},
                            {mid: sorted(p.name for p in folder.iterdir()) if folder.exists() else None
                             for mid, folder in restore.items()})

    assert results["none"][0] == results["empty"][0] == results["leftover"][0]          # every line, every return
    assert results["none"][1] == results["empty"][1] == results["leftover"][1] == {
        "tv-en-2001-show-s01e01": ("restored_local", None), "mov-en-2020-film": ("restored_local", True)}
    assert all(r is True for r in results["none"][0][1::3])
    assert results["none"][2] == {"tv-en-2001-show-s01e01": None, "mov-en-2020-film": None}   # removed when empty
    assert results["empty"][2] == results["leftover"][2] == {"tv-en-2001-show-s01e01": [".mv_incoming"],
                                                            "mov-en-2020-film": [".mv_incoming"]}


def test_a_driver_without_devtools_keeps_todays_downloads_flow(world, tmp_path):
    # Only a test stand-in lacks execute_cdp_cmd (a real Chrome driver always has it): today's flow, frozen in
    # tests/test_fetch_harvester.py — no redirect, no folder, no pre-flight.
    entry = _whole_entry(world.tmp)
    queue = mainfetch.build_fetch_queue(entry, str(_restore(entry)), entry_id=EP_ID)
    ctx = mainfetch.FetchContext()

    assert mainfetch._prepare_downloads(None, queue, ctx, entry, EP_ID) is True
    assert (ctx.target["mode"], ctx.target["dir"]) == ("legacy", str(world.downloads))
    assert not _restore(entry).exists()
