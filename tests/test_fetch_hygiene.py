"""IMP-C25 Step 11 — download hygiene (docs/feature-fetch-datetime/PLAN.md P-12 + Step 11).

Chrome downloads straight into each entry's own destination, <dest>\\.mv_incoming\\ (<dest> = the
unchanged _fetch_restore_folder): a SHA-256-verified file is renamed into <dest>, anything else is
deleted, the folder is removed when the entry ends, nothing lands in ~/Downloads. A fetch journal
(~/.mediavault/fetch_journal/<run_id>.json) lets the next run clean up after an interrupted one;
`fetch_cleanup` lists (and with --apply deletes) leftovers from before this feature; verify_restore
accepts `tempdir`.

  11.a  the per-entry download folder: CDP redirect (Browser, then Page), long-path rule, marker,
        disk pre-flight, the expected-name family, re-arming on every Photos tab

Hermetic: tmp folders only (the autouse fixtures in tests/conftest.py redirect the state dirs);
CDP is a recording stand-in or the fake Google Photos world (tests/gphotos_fake.py); no browser, no
real ~/Downloads, no real C:\\Media.
"""
import hashlib
import os

import pytest

import mainfetch
import mvcommon


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


def test_a_path_too_long_for_chrome_moves_to_a_short_folder_on_the_same_volume(tmp_path):
    dest = os.path.join(r"D:\MV_fetch", "x" * 120, "restore")
    incoming = os.path.join(dest, ".mv_incoming")
    room = mainfetch.INCOMING_PATH_LIMIT - len(incoming) - 1 - len(".crdownload")   # longest name that still fits

    assert mainfetch._incoming_location(dest, ["n" * room]) == incoming
    short = mainfetch._incoming_location(dest, ["n" * (room + 1)])
    digest = hashlib.sha1(os.path.normcase(dest).encode("utf-8")).hexdigest()[:8]
    assert short == os.path.join("D:" + os.sep, "MV_incoming", digest)
    assert mainfetch._incoming_location(dest, ["n" * (room + 1)]) == short       # the same entry, the same folder


def test_a_destination_without_a_drive_has_no_short_folder(monkeypatch):
    monkeypatch.setattr(mainfetch.os.path, "splitdrive", lambda p: ("", p))
    assert mainfetch._incoming_location("restore", ["n" * 300]) is None


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
        assert out.startswith("   > 💽 Space on ")
    else:
        assert out.startswith("   ❌ Not enough space on ") and "nothing was downloaded for this entry" in out


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
