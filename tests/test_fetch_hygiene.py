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


def test_a_driver_without_devtools_keeps_todays_downloads_flow(world, tmp_path):
    # Only a test stand-in lacks execute_cdp_cmd (a real Chrome driver always has it): today's flow, frozen in
    # tests/test_fetch_harvester.py — no redirect, no folder, no pre-flight.
    entry = _whole_entry(world.tmp)
    queue = mainfetch.build_fetch_queue(entry, str(_restore(entry)), entry_id=EP_ID)
    ctx = mainfetch.FetchContext()

    assert mainfetch._prepare_downloads(None, queue, ctx, entry, EP_ID) is True
    assert (ctx.target["mode"], ctx.target["dir"]) == ("legacy", str(world.downloads))
    assert not _restore(entry).exists()
