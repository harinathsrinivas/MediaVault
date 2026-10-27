"""tools/gp_inventory.py (IMP-C25): the parsers (Step 2) and the productized crawler (Step 7). Synthetic fixtures
only — no real ids/URLs; the fake Google Photos, the lock, the inventory and the Chrome profiles live in tmp_path."""
import importlib.util
import itertools
import os
import contextlib
import json
import re
import shutil
import threading
import time
import types
from datetime import datetime, timedelta, timezone
from unittest import mock

import pytest

import gphotos_fake
import gprpc
import gpweb
import mainfetch
import mvcommon

_spec = importlib.util.spec_from_file_location(
    "gp_inventory", os.path.join(os.path.dirname(os.path.dirname(__file__)), "tools", "gp_inventory.py"))
gp_inventory = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gp_inventory)

PID = "AF1QipFAKEfakeFAKEfake0123456789abcdefFAKE"
DEDUP = "hd9mASyG9pJhisPXFS3Zr7Mqr28"  # the published S02E03 example (RESEARCH F21)


def _page(thumb_tail):
    return ('x hash: \'2\', data:[["' + PID + '",["https://example.invalid/t",3840,2160' + thumb_tail + '],'
            '1644061786000,"' + DEDUP + '",28800000,1790104139274,["' + PID + '"],[[1],[2]],2,null]')


def test_item_record_long_thumbnail_form():
    rec = gp_inventory.item_record(_page(",null,null,null,null,null,null,[1053204]"), PID)
    assert rec == {"width": 3840, "height": 2160, "taken_ms": 1644061786000, "dedup_key": DEDUP,
                   "tz_offset_ms": 28800000, "upload_ms": 1790104139274}


def test_item_record_short_thumbnail_form():  # the shape that broke the first crawl
    rec = gp_inventory.item_record(_page(""), PID)
    assert rec["taken_ms"] == 1644061786000 and rec["dedup_key"] == DEDUP and rec["width"] == 3840


def test_item_record_absent():
    assert gp_inventory.item_record("<html>nothing here</html>", PID) == {}


def test_parse_details_us_locale():
    body = ("Info\nAdd a description\nDetails\nFeb 5, 2022\nSat, 7:49 PM\nGMT+08:00\n"
            "Silicon.Valley.S02E03.Bad.Money [92396f].mkv\n2.1MP\n1920 × 1080\nUploaded from Android device\n")
    d = gp_inventory.parse_details(body)
    assert d["filename"] == "Silicon.Valley.S02E03.Bad.Money [92396f].mkv"
    assert (d["date_text"], d["tz_text"]) == ("Feb 5, 2022", "GMT+08:00")


def test_parse_details_uk_locale_current_year_and_mp_line_not_a_filename():
    body = "Details\n21 Sept\nSun, 09:00\nGMT+08:00\n2.1MP\n1920 × 1080\n"
    d = gp_inventory.parse_details(body)
    assert d["date_text"] == "21 Sept" and d["time_text"] == "Sun, 09:00"
    assert "filename" not in d  # "2.1MP" must never be taken for a filename


# ================================================================ IMP-C25 Step 2: the crawler uses gpweb
# The crawler's parsers now live in gpweb (one shared module, no private copies). These tests pin that
# the move changed nothing: a frozen oracle — the crawler's own parse_details / item_record, verbatim as
# of commit 70635cb — must agree with the delegating versions, and a full enumerate + details crawl
# against the fake Google Photos must write byte-identical JSONL (golden records captured from the
# pre-refactor crawler).

_O_EXT_LINE_RE = re.compile(r"^[^\n/\\]{1,300}\.[A-Za-z0-9]{2,5}$")
_O_TZ_RE = re.compile(r"^GMT(?:[+\-]\d{1,2}:\d{2})?$")
_O_MP_RE = re.compile(r"^\d+(?:\.\d+)?\s?MP$")
_O_RECORD_TAIL = r'(?:,(?:null|-?\d+(?:\.\d+)?|\[[^\[\]]*\]))*'


def _oracle_parse_details(body):  # frozen copy of tools/gp_inventory.parse_details @ 70635cb
    i = body.find("\nDetails\n")
    if i < 0:
        i = body.find("Details")
        if i < 0:
            return None
    lines = [l.strip() for l in body[i:].split("\n")[1:40] if l.strip()]
    out = {"details_text": "\n".join(lines[:14])[:700]}
    for idx, l in enumerate(lines[:12]):
        if _O_TZ_RE.match(l):
            out["tz_text"] = l
            if idx >= 1:
                out["time_text"] = lines[idx - 1]
            if idx >= 2:
                out["date_text"] = lines[idx - 2]
            break
    for l in lines[:14]:
        if _O_EXT_LINE_RE.match(l) and not _O_MP_RE.match(l) and not l.lower().startswith(("learn more", "original quality")):
            out["filename"] = l
            break
    return out


def _oracle_item_record(page_source, pid):  # frozen copy of tools/gp_inventory.item_record @ 70635cb
    m = re.search(re.escape(pid) + r'",\["[^"]*",(\d+),(\d+)' + _O_RECORD_TAIL
                  + r'\],(-?\d{10,14}),"([A-Za-z0-9_-]{27})",(-?\d+),(\d{12,14})', page_source)
    if not m:
        return {}
    return {"width": int(m.group(1)), "height": int(m.group(2)), "taken_ms": int(m.group(3)),
            "dedup_key": m.group(4), "tz_offset_ms": int(m.group(5)), "upload_ms": int(m.group(6))}


US_PANEL = ("Info\nAdd a description\nDetails\nFeb 5, 2022\nSat, 7:49\N{NARROW NO-BREAK SPACE}PM\nGMT+08:00\n"
            "Sample.Show.S02E03 [a1b2c3].mkv\n2.1MP\n1920 × 1080\nUploaded from Android device\nBacked up\n"
            "Original quality. Learn more\n")
PANEL_BODIES = [
    US_PANEL,
    "Photos\nShare\nInfo\nDetails\n23 Sept\nWed, 15:48\nGMT+08:00\nSample  Movie [b2c3d4].chunk.009.mkv\n"
    "8.3 MP\n3840 × 2160\nUploaded from an Android device\n",
    "Details\n21 Sept\nSun, 09:00\nGMT+08:00\n2.1MP\n1920 × 1080\n",                 # starts with Details
    "More Details here\nFeb 5, 2022\nSat, 7:49 PM\nGMT\nname.mkv\n",                 # "Details" mid-line
    "x\nDetails\nGMT+08:00\nname.mkv\n",                                              # TZ right after Details
    "x\nDetails\nFeb 5, 2022\nSat, 7:49 PM\nname.mkv\nOriginal quality. Learn more\n",  # no TZ line
    "x\nDetails\r\nJan 2, 2025\r\nThu, 12:20 AM\r\nGMT-5:30\r\nclip.mp4\r\n",         # CRLF
    "x\nDetails\n" + "\n".join(f"line {i}" for i in range(12)) + "\nGMT+08:00\nlate.mkv\n",  # TZ past line 12
    "x\nDetails\n" + "\n".join(f"row{i}" for i in range(16)) + "\nlate.mkv\n",      # name past line 14
    "x\nDetails\nFeb 5\nSat, 1:05 PM\nGMT+08:00\nLearn more.html\n12 MP\nreal.name.mkv\n",
    "x\nDetails\n" + "\n".join("y" * 90 for _ in range(20)),                       # details_text cap 700
    "no panel on this page",
    "",
]


@pytest.mark.parametrize("body", PANEL_BODIES)
def test_parse_details_matches_the_frozen_legacy_parser(body):
    got, want = gp_inventory.parse_details(body), _oracle_parse_details(body)
    assert got == want
    assert json.dumps(got, ensure_ascii=False) == json.dumps(want, ensure_ascii=False)  # key order too


PID2 = "AF1QipFAKEinventoryBBBB0123456789FAKEbb"


def _src(pid=PID, thumb_tail="", dedup=DEDUP, taken=1644061786000, upload=1790104139274, lead=""):
    return (f'x hash: \'2\', data:[{lead}["{pid}",["https://example.invalid/t",3840,2160{thumb_tail}],'
            f'{taken},"{dedup}",28800000,{upload},["{pid}"],[[1],[2]],2,null]')


PAGE_SOURCES = [
    _src(),
    _src(thumb_tail=",null,null,null,null,null,null,[1053204]"),
    _src(pid="XX" + PID),                                   # the id as the tail of a longer token
    _src(pid=PID2) + "," + _src(),                          # a neighbour record first
    '["' + PID + '",["only a mention"]],' + _src(),         # an earlier non-record mention
    _src(dedup=DEDUP[:-1]),
    _src(taken=164406178),
    _src(upload=17901041),
    "<html>nothing here</html>",
]


@pytest.mark.parametrize("src", PAGE_SOURCES)
def test_item_record_matches_the_frozen_legacy_parser(src):
    got, want = gp_inventory.item_record(src, PID), _oracle_item_record(src, PID)
    assert list(got.items()) == list(want.items())


def test_item_record_generalizes_where_the_legacy_regex_gave_up():
    """The one intended difference (PLAN Step 2a): a record whose thumbnail tail the old regex could not
    read is now found by the structural (raw_decode) check — same record format, no error record."""
    src = _src(thumb_tail=',"extra",[[1,2]]')
    assert _oracle_item_record(src, PID) == {}
    assert gp_inventory.item_record(src, PID) == {"width": 3840, "height": 2160, "taken_ms": 1644061786000,
                                                  "dedup_key": DEDUP, "tz_offset_ms": 28800000,
                                                  "upload_ms": 1790104139274}


def test_crawler_parsers_and_page_scripts_come_from_gpweb(monkeypatch):
    assert gp_inventory.TILES_JS is gpweb.TILES_JS and gp_inventory.SCROLL_JS is gpweb.SCROLL_JS
    assert gp_inventory.PID_RE is gpweb.PHOTO_ID_RE
    for private_copy in ("TZ_RE", "EXT_LINE_RE", "MP_RE", "_RECORD_TAIL"):
        assert not hasattr(gp_inventory, private_copy), f"{private_copy} must live in gpweb only"
    calls = []
    monkeypatch.setattr(gpweb, "info_panel", lambda text: calls.append(("panel", text)) or None)
    monkeypatch.setattr(gpweb, "page_record", lambda src, pid: calls.append(("record", pid)) or {})
    assert gp_inventory.parse_details("Details\nx") is None and gp_inventory.item_record("<html/>", PID) == {}
    assert calls == [("panel", "Details\nx"), ("record", PID)]


class _Clock:
    """Deterministic stand-in for the crawler's `time` module (it only uses time() and sleep())."""

    def __init__(self):
        self.now = 1_800_000_000.0

    def time(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


def _golden_world(root):
    """A fixed fake Google Photos: every layout the crawler meets (US + UK, other year / this year, both
    thumbnail shapes, collapsed double spaces, a UTC item, a photo in portrait) plus one item that is
    deleted between the enumerate and details phases (→ an error record)."""
    world = gphotos_fake.FakePhotos(root)
    world.add_item("tv", "Sample.Show.S02E03.Title [a1b2c3].mkv", taken="2022-02-05T11:49:46Z")
    world.add_item("tv", "Sample.Show.S03E01 [d4e5f6].chunk.002.mkv", taken="2026-09-10T01:16:46Z",
                   width=3840, height=2160, thumb_shape="long")
    world.add_item("tv", "Sample  Double   Space [0a0b0c].mkv", taken="2025-01-01T15:47:43Z")
    world.add_item("tv", "Sample.UTC.Item [0d0e0f].mkv", taken="2024-05-03T23:18:02Z", tz_offset_min=0)
    gone = world.add_item("tv", "Sample.Gone [9f9f9f].mkv", taken="2023-03-03T03:03:03Z")
    world.add_item("movies", "Sample.Movie.2019.2160p [b1b2b3].chunk.001.mkv", taken="2026-09-23T07:48:43Z",
                   width=3840, height=2160, thumb_shape="long")
    world.add_item("movies", "Sample.Movie.2007 [c1c2c3].mkv", taken="2025-12-31T12:50:17Z", width=1920, height=800)
    world.add_item("movies", "Sample.Photo [e1e2e3].jpg", taken="2026-02-07T04:58:57Z", width=1080, height=1920)
    return world, gone.photo_id


def _crawl(world, account, out_dir, delete_after_enumerate=()):
    """The crawler's enumerate + details phases against the fake, deterministically (clock, seen_at /
    crawled_at and jitter pinned) → (tiles.jsonl text, items.jsonl text)."""
    tiles = os.path.join(out_dir, f"{account}.tiles.jsonl")
    items = os.path.join(out_dir, f"{account}.items.jsonl")
    log = gp_inventory.Log(os.path.join(out_dir, f"{account}.log"))
    driver = world.driver(account)
    with contextlib.ExitStack() as stack:
        stack.enter_context(mock.patch.object(gp_inventory, "time", _Clock()))
        stack.enter_context(mock.patch.object(gp_inventory, "now_iso", lambda: "2026-09-28T10:00:00+00:00"))
        stack.enter_context(mock.patch.object(gp_inventory, "random", types.SimpleNamespace(uniform=lambda a, b: a)))
        gp_inventory.front(driver)
        gp_inventory.enumerate_tiles(driver, tiles, log)
        for pid in delete_after_enumerate:
            world.item(pid).deleted = True
        gp_inventory.crawl_details(driver, account, tiles, items, log)
    with open(tiles, encoding="utf-8") as t, open(items, encoding="utf-8") as i:
        return t.read(), i.read()


# Captured from the PRE-refactor crawler (tools/gp_inventory.py as of 8673ae5) via _golden_world + _crawl:
# the exact JSONL bytes the crawler must keep writing (record format unchanged — PLAN Step 2 acceptance).
GOLDEN = {
    "tv.tiles": (
        '{"photo_id": "AF1QipFAKETV00000211a13323c63a300bdcc5fe553", "label": "Video - Landscape - Sep 10, 2026, 9:16:46\N{NARROW NO-BREAK SPACE}AM", "seen_at": "2026-09-28T10:00:00+00:00"}\n'
        '{"photo_id": "AF1QipFAKETV0000033171e6ca6b8512b1312fa1e12", "label": "Video - Landscape - Jan 1, 2025, 11:47:43\N{NARROW NO-BREAK SPACE}PM", "seen_at": "2026-09-28T10:00:00+00:00"}\n'
        '{"photo_id": "AF1QipFAKETV000004bf367668dfc5e067b8ce4e36b", "label": "Video - Landscape - May 3, 2024, 11:18:02\N{NARROW NO-BREAK SPACE}PM", "seen_at": "2026-09-28T10:00:00+00:00"}\n'
        '{"photo_id": "AF1QipFAKETV000005da6a81a3bd3e5767ff41a96c5", "label": "Video - Landscape - Mar 3, 2023, 11:03:03\N{NARROW NO-BREAK SPACE}AM", "seen_at": "2026-09-28T10:00:00+00:00"}\n'
        '{"photo_id": "AF1QipFAKETV0000013dba230d8e72701f93d606df7", "label": "Video - Landscape - Feb 5, 2022, 7:49:46\N{NARROW NO-BREAK SPACE}PM", "seen_at": "2026-09-28T10:00:00+00:00"}\n'
    ),
    "tv.items": (
        '{"photo_id": "AF1QipFAKETV00000211a13323c63a300bdcc5fe553", "details_text": "Details\\nSep 10\\nThu, 9:16\N{NARROW NO-BREAK SPACE}AM\\nGMT+08:00\\nSample.Show.S03E01 [d4e5f6].chunk.002.mkv\\n8.3MP\\n3840 \xd7 2160\\nUploaded from Android device\\nBacked up\\nOriginal quality. Learn more\\nThis item doesn\'t take up space in your account storage. Learn more\\nAdd a location", "tz_text": "GMT+08:00", "time_text": "Thu, 9:16\N{NARROW NO-BREAK SPACE}AM", "date_text": "Sep 10", "filename": "Sample.Show.S03E01 [d4e5f6].chunk.002.mkv", "width": 3840, "height": 2160, "taken_ms": 1789003006000, "dedup_key": "XRAGKPuL3jmakw1j8tKAFQBHAf0", "tz_offset_ms": 28800000, "upload_ms": 1789089406000, "tile_label": "Video - Landscape - Sep 10, 2026, 9:16:46\N{NARROW NO-BREAK SPACE}AM", "account": "tv", "crawled_at": "2026-09-28T10:00:00+00:00"}\n'
        '{"photo_id": "AF1QipFAKETV0000033171e6ca6b8512b1312fa1e12", "details_text": "Details\\nJan 1, 2025\\nWed, 11:47\N{NARROW NO-BREAK SPACE}PM\\nGMT+08:00\\nSample Double Space [0a0b0c].mkv\\n2.1MP\\n1920 \xd7 1080\\nUploaded from Android device\\nBacked up\\nOriginal quality. Learn more\\nThis item doesn\'t take up space in your account storage. Learn more\\nAdd a location", "tz_text": "GMT+08:00", "time_text": "Wed, 11:47\N{NARROW NO-BREAK SPACE}PM", "date_text": "Jan 1, 2025", "filename": "Sample Double Space [0a0b0c].mkv", "width": 1920, "height": 1080, "taken_ms": 1735746463000, "dedup_key": "C6XRYDLYgNUTvNWm3yNG98MChA4", "tz_offset_ms": 28800000, "upload_ms": 1735832863000, "tile_label": "Video - Landscape - Jan 1, 2025, 11:47:43\N{NARROW NO-BREAK SPACE}PM", "account": "tv", "crawled_at": "2026-09-28T10:00:00+00:00"}\n'
        '{"photo_id": "AF1QipFAKETV000004bf367668dfc5e067b8ce4e36b", "details_text": "Details\\nMay 3, 2024\\nFri, 11:18\N{NARROW NO-BREAK SPACE}PM\\nGMT\\nSample.UTC.Item [0d0e0f].mkv\\n2.1MP\\n1920 \xd7 1080\\nUploaded from Android device\\nBacked up\\nOriginal quality. Learn more\\nThis item doesn\'t take up space in your account storage. Learn more\\nAdd a location", "tz_text": "GMT", "time_text": "Fri, 11:18\N{NARROW NO-BREAK SPACE}PM", "date_text": "May 3, 2024", "filename": "Sample.UTC.Item [0d0e0f].mkv", "width": 1920, "height": 1080, "taken_ms": 1714778282000, "dedup_key": "33c_5w5gHKf-i4jocD1QPNSR3t8", "tz_offset_ms": 0, "upload_ms": 1714864682000, "tile_label": "Video - Landscape - May 3, 2024, 11:18:02\N{NARROW NO-BREAK SPACE}PM", "account": "tv", "crawled_at": "2026-09-28T10:00:00+00:00"}\n'
        '{"photo_id": "AF1QipFAKETV000005da6a81a3bd3e5767ff41a96c5", "error": "incomplete: details, record", "tile_label": "Video - Landscape - Mar 3, 2023, 11:03:03\N{NARROW NO-BREAK SPACE}AM", "account": "tv", "crawled_at": "2026-09-28T10:00:00+00:00"}\n'
        '{"photo_id": "AF1QipFAKETV0000013dba230d8e72701f93d606df7", "details_text": "Details\\nFeb 5, 2022\\nSat, 7:49\N{NARROW NO-BREAK SPACE}PM\\nGMT+08:00\\nSample.Show.S02E03.Title [a1b2c3].mkv\\n2.1MP\\n1920 \xd7 1080\\nUploaded from Android device\\nBacked up\\nOriginal quality. Learn more\\nThis item doesn\'t take up space in your account storage. Learn more\\nAdd a location", "tz_text": "GMT+08:00", "time_text": "Sat, 7:49\N{NARROW NO-BREAK SPACE}PM", "date_text": "Feb 5, 2022", "filename": "Sample.Show.S02E03.Title [a1b2c3].mkv", "width": 1920, "height": 1080, "taken_ms": 1644061786000, "dedup_key": "3IZUT9CLDU5L7Jkw7yWt29e6Of8", "tz_offset_ms": 28800000, "upload_ms": 1644148186000, "tile_label": "Video - Landscape - Feb 5, 2022, 7:49:46\N{NARROW NO-BREAK SPACE}PM", "account": "tv", "crawled_at": "2026-09-28T10:00:00+00:00"}\n'
    ),
    "movies.tiles": (
        '{"photo_id": "AF1QipFAKEMOV000006b33410a5555bcaa09267bb82d", "label": "Video \N{EN DASH} Landscape \N{EN DASH} 23 Sept 2026, 15:48:43", "seen_at": "2026-09-28T10:00:00+00:00"}\n'
        '{"photo_id": "AF1QipFAKEMOV0000089ef5dfd4eb2dee180a4ff484d", "label": "Photo \N{EN DASH} Portrait \N{EN DASH} 7 Feb 2026, 12:58:57", "seen_at": "2026-09-28T10:00:00+00:00"}\n'
        '{"photo_id": "AF1QipFAKEMOV000007fdf91bd65ca50a3fd76f23ef3", "label": "Video \N{EN DASH} Landscape \N{EN DASH} 31 Dec 2025, 20:50:17", "seen_at": "2026-09-28T10:00:00+00:00"}\n'
    ),
    "movies.items": (
        '{"photo_id": "AF1QipFAKEMOV000006b33410a5555bcaa09267bb82d", "details_text": "Details\\n23 Sept\\nWed, 15:48\\nGMT+08:00\\nSample.Movie.2019.2160p [b1b2b3].chunk.001.mkv\\n8.3 MP\\n3840 \xd7 2160\\nUploaded from an Android device\\nBacked up\\nOriginal quality. Learn more\\nThis item doesn\'t take up space in your account storage. Learn more\\nAdd a location", "tz_text": "GMT+08:00", "time_text": "Wed, 15:48", "date_text": "23 Sept", "filename": "Sample.Movie.2019.2160p [b1b2b3].chunk.001.mkv", "width": 3840, "height": 2160, "taken_ms": 1790149723000, "dedup_key": "cpVuV64-hMQsTSb1UiiigmWpREI", "tz_offset_ms": 28800000, "upload_ms": 1790236123000, "tile_label": "Video \N{EN DASH} Landscape \N{EN DASH} 23 Sept 2026, 15:48:43", "account": "movies", "crawled_at": "2026-09-28T10:00:00+00:00"}\n'
        '{"photo_id": "AF1QipFAKEMOV0000089ef5dfd4eb2dee180a4ff484d", "details_text": "Details\\n7 Feb\\nSat, 12:58\\nGMT+08:00\\nSample.Photo [e1e2e3].jpg\\n2.1 MP\\n1080 \xd7 1920\\nUploaded from an Android device\\nBacked up\\nOriginal quality. Learn more\\nThis item doesn\'t take up space in your account storage. Learn more\\nAdd a location", "tz_text": "GMT+08:00", "time_text": "Sat, 12:58", "date_text": "7 Feb", "filename": "Sample.Photo [e1e2e3].jpg", "width": 1080, "height": 1920, "taken_ms": 1770440337000, "dedup_key": "gdvcq_chQoPjlWtJRVFNtArfkEM", "tz_offset_ms": 28800000, "upload_ms": 1770526737000, "tile_label": "Photo \N{EN DASH} Portrait \N{EN DASH} 7 Feb 2026, 12:58:57", "account": "movies", "crawled_at": "2026-09-28T10:00:00+00:00"}\n'
        '{"photo_id": "AF1QipFAKEMOV000007fdf91bd65ca50a3fd76f23ef3", "details_text": "Details\\n31 Dec 2025\\nWed, 20:50\\nGMT+08:00\\nSample.Movie.2007 [c1c2c3].mkv\\n1.5 MP\\n1920 \xd7 800\\nUploaded from an Android device\\nBacked up\\nOriginal quality. Learn more\\nThis item doesn\'t take up space in your account storage. Learn more\\nAdd a location", "tz_text": "GMT+08:00", "time_text": "Wed, 20:50", "date_text": "31 Dec 2025", "filename": "Sample.Movie.2007 [c1c2c3].mkv", "width": 1920, "height": 800, "taken_ms": 1767185417000, "dedup_key": "b8xpc2MuYjrL8s4I4MEBcqjoY_0", "tz_offset_ms": 28800000, "upload_ms": 1767271817000, "tile_label": "Video \N{EN DASH} Landscape \N{EN DASH} 31 Dec 2025, 20:50:17", "account": "movies", "crawled_at": "2026-09-28T10:00:00+00:00"}\n'
    ),
}


def test_golden_crawl_records_are_byte_identical(tmp_path):
    world, gone = _golden_world(tmp_path)
    for account in ("tv", "movies"):
        out = tmp_path / account
        out.mkdir()
        tiles, items = _crawl(world, account, str(out), delete_after_enumerate=(gone,) if account == "tv" else ())
        assert tiles == GOLDEN[f"{account}.tiles"]
        assert items == GOLDEN[f"{account}.items"]


# ================================================================ IMP-C25 Step 7: the productized crawler
# RPC-first (read-only, via gprpc) with the dom crawl as the automatic fallback, the fetch session lock with a
# progress-driven heartbeat, the profile pre-flight + the signed-out stop, the run log (every id seen per run and
# the "enumerated" proof of a complete enumeration), deletion detection, --status, crash-resume and the
# backward-compatible record format. Everything runs against the fake Google Photos on a fake clock: no browser,
# no network, no real sleeping; the lock, the inventory and the Chrome profiles live under tmp_path.

ACCOUNT_KEYS = ("movies", "tv", "anime", "others")
CRAWLER_RPCIDS = {"lcxiM", "EzkLib", "EWgK9e"}  # all the crawler ever sends — read-only P12 ids
IDENTITY = ("width", "height", "taken_ms", "dedup_key", "tz_offset_ms", "upload_ms")
RPC_KEYS = ["photo_id", "filename", "width", "height", "taken_ms", "dedup_key", "tz_offset_ms", "upload_ms",
            "size_bytes", "duration_ms", "original_quality", "partial_upload", "archived", "account", "crawled_at",
            "source"]
WEB_MARKERS = ("🔹 PROCESSING:", "> Detected Split File", "✅ MOVED:", "✅ ENTRY COMPLETE.")  # PLAN P-13


class FakeTime:
    """One deterministic clock for the crawler's `time` module, its timestamps and gprpc's pacing: sleeping
    advances it. It starts after the golden records' crawled_at, like a real re-crawl."""

    def __init__(self, now=1_790_600_000.0):
        self.now = now

    def time(self):
        return self.now

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.now += max(0.0, seconds)

    def iso(self):
        return datetime.fromtimestamp(self.now, timezone.utc).isoformat(timespec="seconds")


@pytest.fixture(autouse=True)
def fake_time(monkeypatch):
    """No test here ever sleeps for real: the crawler's time / random / timestamps / run ids and gprpc's pacing
    clock run on one FakeTime (the golden test swaps in its own _Clock inside _crawl)."""
    clock = FakeTime()
    run_ids = itertools.count(1)
    monkeypatch.setattr(gp_inventory, "time", clock)
    monkeypatch.setattr(gp_inventory, "random", types.SimpleNamespace(uniform=lambda a, b: a))
    monkeypatch.setattr(gp_inventory, "now_iso", clock.iso)
    monkeypatch.setattr(gp_inventory, "new_run_id", lambda: f"run{next(run_ids):03d}")
    monkeypatch.setattr(gprpc, "_monotonic", clock.monotonic)
    monkeypatch.setattr(gprpc, "_sleep", clock.sleep)
    monkeypatch.setattr(gprpc, "_uniform", lambda low, high: high)
    return clock


@pytest.fixture(autouse=True)
def lock_file(tmp_path, monkeypatch):
    """The fetch session lock of every test here lives under tmp_path: a test must never take, refresh or remove
    the lock of a real fetch (~/.mediavault/locks/fetch_session.lock)."""
    lock_dir = tmp_path / "mv_locks"
    real = os.path.normcase(os.path.join(os.path.expanduser("~"), ".mediavault"))
    monkeypatch.setattr(mvcommon, "MV_LOCK_DIR", str(lock_dir))
    monkeypatch.setattr(mvcommon, "FETCH_SESSION_LOCK", str(lock_dir / "fetch_session.lock"))
    assert tmp_path.resolve() in (lock_dir / "fetch_session.lock").resolve().parents
    assert not os.path.normcase(mvcommon.FETCH_SESSION_LOCK).startswith(real)
    return lock_dir / "fetch_session.lock"


class FakeProc:
    def __init__(self):
        self.terminated = False

    def terminate(self):
        self.terminated = True


class Crawler:
    """gp_inventory.main against the fake Google Photos. The Chrome launch and the Selenium attach are replaced by
    the fake's driver, the four Chrome profiles are folders under tmp_path, and a real Chrome or chromedriver can
    never start (both entry points raise)."""

    def __init__(self, world, tmp_path, monkeypatch):
        self.world, self.out, self.account = world, tmp_path / "inventory", None
        self.profiles = {account: str(tmp_path / "profiles" / account) for account in ACCOUNT_KEYS}
        for folder in self.profiles.values():
            os.makedirs(folder)
        monkeypatch.setattr(mainfetch, "CHROME_PROFILES", dict(self.profiles))
        self.launched, self.procs, self.drivers = [], [], []
        monkeypatch.setattr(gp_inventory, "launch_chrome", self._launch)
        monkeypatch.setattr(gp_inventory, "connect", self._connect)

        def forbidden(*args, **kwargs):
            raise AssertionError("a real Chrome / chromedriver must never start in a test")

        monkeypatch.setattr(gp_inventory.subprocess, "Popen", forbidden)
        monkeypatch.setattr(gp_inventory.webdriver, "Chrome", forbidden)

    def _launch(self, profile_dir, port):
        self.launched.append((profile_dir, port))
        self.procs.append(FakeProc())
        return self.procs[-1]

    def _connect(self, port):
        self.drivers.append(self.world.driver(self.account))
        return self.drivers[-1]

    def run(self, account, *args, port=9225):
        self.account = gp_inventory.ACCOUNTS[account]
        return gp_inventory.main([account, "--port", str(port), "--out", str(self.out), *args])

    def state(self, account="tv"):
        return gp_inventory.inventory_state(account, str(self.out))

    def lines(self, account, kind):
        return gp_inventory.read_jsonl(str(self.out / f"{account}.{kind}.jsonl"))

    def events(self, account, run_id):
        return [line for line in self.lines(account, "runs") if line.get("run") == run_id]

    def seen(self, account, run_id):
        return [line["photo_id"] for line in self.lines(account, "runs") if line.get("seen_run") == run_id]

    def status(self, account="tv", now=None):
        lines = []
        gp_inventory.print_status(account, str(self.out), now=now, out=lines.append)
        return lines


@pytest.fixture
def crawler(fake_photos, tmp_path, monkeypatch):
    return Crawler(fake_photos, tmp_path, monkeypatch)


class HardCrash(BaseException):
    """Stands in for a process kill mid-run: nothing in the crawler catches it."""


def _episodes(world, account, n, first=1, sid="a1b2c3", year=2022):
    """n synthetic episodes, one day apart, the later the newer (taken and uploaded)."""
    return [world.add_item(account, f"Sample.Show.S01E{i:02d} [{sid}].mkv", taken=f"{year}-02-{i:02d}T11:49:46Z")
            for i in range(first, first + n)]


def _ewgk9e_ids(entries):
    return [key[0] for e in entries if e["rpcid"] == "EWgK9e" for key in e["request"][0][0][0]]


def _expected_rpc_record(item, account):
    return {"photo_id": item.photo_id, "filename": item.filename, "width": item.width, "height": item.height,
            "taken_ms": item.taken_ms, "dedup_key": item.dedup_key, "tz_offset_ms": item.tz_offset_ms,
            "upload_ms": item.upload_ms, "size_bytes": item.size_bytes, "duration_ms": item.duration_ms,
            "original_quality": item.original_quality, "partial_upload": item.partial_upload,
            "archived": item.archived, "account": account}


# ---------------------------------------------------------------- storage primitives

def test_appended_lines_keep_the_platform_newline_of_the_files_crawled_so_far(tmp_path):
    legacy, new = tmp_path / "legacy.jsonl", tmp_path / "new.jsonl"
    records = [{"photo_id": "AF1QipFAKEnl0001", "filename": "Sample தமிழ் [a1a1a1].mkv"}, {"photo_id": "AF1QipFAKEnl0002"}]
    for rec in records:  # the pre-Step-7 writer, verbatim
        with open(legacy, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    gp_inventory.append_jsonl(str(new), records[0])
    gp_inventory.append_jsonl_many(str(new), records[1:])
    gp_inventory.append_jsonl_many(str(new), [])  # nothing to write: the file is not touched
    assert new.read_bytes() == legacy.read_bytes()


def test_a_torn_tail_is_terminated_before_the_next_append(tmp_path):
    path = tmp_path / "x.jsonl"
    gp_inventory.append_jsonl(str(path), {"photo_id": "AF1QipFAKEtorn0001"})
    with open(path, "ab") as fh:
        fh.write(b'{"photo_id": "AF1QipFAKEtorn0002", "filename": "Sample \xe0\xae')  # a kill mid-character
    gp_inventory.append_jsonl_many(str(path), [{"photo_id": "AF1QipFAKEtorn0003"}, {"photo_id": "AF1QipFAKEtorn0004"}])
    problems = []
    got = gp_inventory.read_jsonl(str(path), problems)
    assert [r["photo_id"] for r in got] == ["AF1QipFAKEtorn0001", "AF1QipFAKEtorn0003", "AF1QipFAKEtorn0004"]
    assert problems == [{"file": "x.jsonl", "line": 2, "error": "not a JSON object line (torn write?)"}]


def test_read_jsonl_skips_what_is_not_a_json_object_line(tmp_path):
    path = tmp_path / "y.jsonl"
    path.write_bytes(b'\xef\xbb\xbf{"photo_id": "AF1QipFAKEbom001"}\r\n[1, 2]\r\n\r\n"text"\n\xff\xfe\n{"photo_id": "AF1QipFAKEok0002"}')
    problems = []
    assert [r["photo_id"] for r in gp_inventory.read_jsonl(str(path), problems)] == ["AF1QipFAKEbom001",
                                                                                      "AF1QipFAKEok0002"]
    assert [p["line"] for p in problems] == [2, 4, 5]
    assert gp_inventory.read_jsonl(str(tmp_path / "missing.jsonl")) == []


def test_the_reader_keeps_the_latest_good_line_per_id(tmp_path):
    """The reader contract Steps 9/13 rely on: later lines supersede earlier ones, but an error line never hides
    an earlier good record (a failed re-read is not news about the item)."""
    gp_inventory.append_jsonl_many(str(tmp_path / "tv.items.jsonl"), [
        {"photo_id": "AF1QipFAKEread0001", "filename": "Old.mkv", "crawled_at": "2026-09-25T00:00:00+00:00"},
        {"photo_id": "AF1QipFAKEread0001", "filename": "New.mkv", "crawled_at": "2026-09-26T00:00:00+00:00"},
        {"photo_id": "AF1QipFAKEread0001", "error": "incomplete: batch info", "crawled_at": "2026-09-27T00:00:00+00:00"},
        {"photo_id": "AF1QipFAKEread0002", "error": "incomplete: details", "crawled_at": "2026-09-25T00:00:00+00:00"},
        {"photo_id": "AF1QipFAKEread0002", "error": "incomplete: record", "crawled_at": "2026-09-26T00:00:00+00:00"}])
    state = gp_inventory.inventory_state("tv", str(tmp_path))
    assert state["items"]["AF1QipFAKEread0001"]["filename"] == "New.mkv"
    assert state["items"]["AF1QipFAKEread0002"]["error"] == "incomplete: record"  # only errors: the latest one
    assert state["detailed"] == {"AF1QipFAKEread0001"} and state["errored"] == {"AF1QipFAKEread0002"}
    assert state["known_ids"] == ["AF1QipFAKEread0001", "AF1QipFAKEread0002"] and state["latest_complete"] is None


# ---------------------------------------------------------------- mainfetch.profile_preflight (OD-10)

@pytest.mark.parametrize("account", ACCOUNT_KEYS)
def test_profile_preflight_names_a_missing_profile_and_passes_an_existing_one(tmp_path, monkeypatch, account):
    folder = tmp_path / "profiles" / account
    monkeypatch.setattr(mainfetch, "CHROME_PROFILES", {**mainfetch.CHROME_PROFILES, account: str(folder)})
    problem = mainfetch.profile_preflight(account)
    assert f"the Chrome profile for '{account}' does not exist: {folder}" in problem
    assert "sign in to photos.google.com" in problem
    folder.parent.mkdir(parents=True)
    folder.write_text("a file, not a profile folder", encoding="utf-8")
    assert "does not exist" in mainfetch.profile_preflight(account)
    folder.unlink()
    folder.mkdir()
    assert mainfetch.profile_preflight(account) is None


def test_profile_preflight_refuses_an_unknown_account():
    problem = mainfetch.profile_preflight("sports")
    assert problem.startswith("unknown Google account 'sports'") and "movies, tv, anime, others" in problem


def test_a_missing_profile_stops_before_the_lock_or_chrome(crawler, lock_file, capsys):
    shutil.rmtree(crawler.profiles["others"])
    crawler.world.add_item("others", "Sample.Match.2024 [f1f1f1].mkv")
    assert crawler.run("others") == gp_inventory.EXIT_USAGE
    out = capsys.readouterr().out
    assert f"STOPPED: the Chrome profile for 'others' does not exist: {crawler.profiles['others']}" in out
    assert crawler.launched == [] and crawler.drivers == []
    assert not lock_file.parent.exists()  # the lock was never even looked at
    assert not (crawler.out / "others.runs.jsonl").exists() and not (crawler.out / "others.items.jsonl").exists()


def test_attach_mode_uses_the_open_chrome_and_needs_no_profile_folder(crawler):
    shutil.rmtree(crawler.profiles["tv"])
    item = crawler.world.add_item("tv", "Sample.Show.S01E01 [a1a1a1].mkv")
    crawler.account = "tv"
    assert gp_inventory.main(["tv", "--attach", "9222", "--out", str(crawler.out)]) == 0
    assert crawler.launched == [] and len(crawler.drivers) == 1
    assert crawler.drivers[0].window_handles == ["FAKE-TAB-1"]  # it worked in its own tab and closed only that
    assert crawler.state()["detailed"] == {item.photo_id}


@pytest.mark.parametrize("session", ["signed_out", "expired"])
def test_a_signed_out_profile_stops_at_the_landing_page(crawler, lock_file, capsys, session):
    crawler.world.add_item("anime", "Sample Anime - 01 [a1a1a1].mkv")
    crawler.world.session["anime"] = session
    assert crawler.run("anime") == gp_inventory.EXIT_SIGNED_OUT
    out = capsys.readouterr().out
    assert "STOPPED: profile 'anime' appears logged out" in out
    assert f"sign this profile in to photos.google.com (Chrome --user-data-dir={crawler.profiles['anime']})" in out
    assert not any((crawler.out / f"anime.{kind}.jsonl").exists() for kind in ("tiles", "items", "runs"))
    assert crawler.world.rpc_log == [] and crawler.procs[0].terminated and not lock_file.exists()


def test_googles_unusual_traffic_page_stops_the_crawl_as_throttled_never_signed_out(crawler, lock_file, capsys):
    # IMP-C25 thfix: mainfetch's page classifier — Google's "unusual traffic" page is no sign-out (exit 4, not 2).
    crawler.world.add_item("anime", "Sample Anime - 01 [a1a1a1].mkv")
    crawler.world.session["anime"] = "sorry"
    assert crawler.run("anime") == gp_inventory.EXIT_THROTTLED == 4
    out = capsys.readouterr().out
    assert ("STOPPED: Google Photos is throttled in 'anime' (Google showed its 'unusual traffic' page) — not a "
            "sign-out; progress is kept, re-run in about 30 minutes.") in out
    assert "logged out" not in out and "sign this profile in" not in out
    assert not any((crawler.out / f"anime.{kind}.jsonl").exists() for kind in ("tiles", "items", "runs"))
    assert crawler.world.rpc_log == [] and crawler.procs[0].terminated and not lock_file.exists()


def test_a_bounce_to_the_signed_out_page_mid_crawl_is_a_throttle(crawler, lock_file, monkeypatch, capsys):
    # IMP-C25 thfix: the landing check showed signed-in Google Photos; then Google bounces the session (the R3 dry
    # runs). The dom crawl's own page check (ensure_logged_in) asks mainfetch's classifier first: throttled, exit 4.
    crawler.world.add_item("tv", "Sample.Show.S01E01 [a1a1a1].mkv")
    real = gp_inventory.landing_check

    def landing(driver, account):
        real(driver, account)
        crawler.world.session["tv"] = "signed_out"

    monkeypatch.setattr(gp_inventory, "landing_check", landing)
    assert crawler.run("tv", "--mode", "dom") == gp_inventory.EXIT_THROTTLED
    out = capsys.readouterr().out
    assert "STOPPED: Google Photos is throttled in 'tv' (Google showed its signed-out page to a session that was signed in)" \
           in out and "logged out" not in out
    assert [r["result"] for r in crawler.state()["runs"]] == ["throttled"]


# ---------------------------------------------------------------- the fetch session lock + heartbeat

def test_a_held_lock_refuses_the_crawl_before_anything_starts(crawler, lock_file, capsys):
    crawler.world.add_item("tv", "Sample.Show.S01E01 [a1a1a1].mkv")
    with mvcommon.fetch_session_lock(blocking=False):  # e.g. a running fetch
        held = lock_file.read_bytes()
        assert crawler.run("tv") == gp_inventory.EXIT_LOCK
        assert lock_file.read_bytes() == held  # neither removed nor rewritten
        assert gp_inventory.main(["tv", "--status", "--out", str(crawler.out)]) == 0  # --status needs no lock
    assert crawler.launched == [] and crawler.drivers == [] and crawler.world.rpc_log == []
    assert not any((crawler.out / f"tv.{kind}.jsonl").exists() for kind in ("tiles", "items", "runs"))
    assert "REFUSED: the fetch session lock is held" in capsys.readouterr().out


def test_every_run_releases_the_lock(crawler, lock_file):
    crawler.world.add_item("tv", "Sample.Show.S01E01 [a1a1a1].mkv")
    assert crawler.run("tv") == 0 and not lock_file.exists()
    assert crawler.run("tv", "--mode", "dom") == 0 and not lock_file.exists()


def test_the_heartbeat_refreshes_the_lock_at_most_every_interval(lock_file, fake_time):
    assert gp_inventory.LOCK_HEARTBEAT_S <= 600  # PLAN Step 7: refreshed at least every 10 minutes
    with gp_inventory.crawl_lock() as heartbeat:
        old = time.time() - 7200
        os.utime(lock_file, (old, old))
        heartbeat.beat()  # too early: nothing to do
        assert os.path.getmtime(lock_file) == pytest.approx(old, abs=1) and heartbeat.beats == 0
        fake_time.now += gp_inventory.LOCK_HEARTBEAT_S
        heartbeat.beat()
        assert os.path.getmtime(lock_file) > time.time() - 60 and heartbeat.beats == 1
    assert not lock_file.exists()


def test_the_heartbeat_is_driven_by_progress_so_a_hung_crawl_never_holds_the_lock_forever(lock_file, fake_time):
    threads = set(threading.enumerate())
    with gp_inventory.crawl_lock() as heartbeat:
        assert set(threading.enumerate()) == threads  # no background refresher
        old = time.time() - 7200
        os.utime(lock_file, (old, old))
        fake_time.now += 100 * gp_inventory.LOCK_HEARTBEAT_S  # time passes but the crawl makes no progress …
        assert os.path.getmtime(lock_file) == pytest.approx(old, abs=1)  # … so nothing refreshed the lock
        with mvcommon.fetch_session_lock(blocking=False):  # a stale lock can be reclaimed by a fetch / the warm-up
            pass
        with pytest.raises(gp_inventory.LockLost, match="taken over"):
            heartbeat.beat()  # when the hung crawl wakes up, it yields


def test_the_heartbeat_keeps_a_long_crawl_s_lock_fresh(crawler, monkeypatch, lock_file, fake_time):
    """A warm-up / fetch-style acquirer that tries the lock mid-crawl is always refused — even though every batch
    takes 5 minutes and leaves the lock file looking 2 hours old (disarm probe: without the heartbeat's
    refresh the second attempt reclaims the 'stale' lock)."""
    _episodes(crawler.world, "tv", 4)
    monkeypatch.setattr(gp_inventory, "DETAIL_BATCH", 1)
    real, probes = gprpc.batch_info, []

    def slow_batch_info(driver, ids):
        try:
            with mvcommon.fetch_session_lock(blocking=False):
                probes.append("acquired")
        except mvcommon.LockHeldError:
            probes.append("refused")
        fake_time.now += 300
        old = time.time() - 7200
        os.utime(lock_file, (old, old))
        return real(driver, ids)

    monkeypatch.setattr(gprpc, "batch_info", slow_batch_info)
    assert crawler.run("tv") == 0
    assert probes == ["refused"] * 4
    assert len(crawler.state()["detailed"]) == 4


def test_a_crawl_yields_when_another_command_takes_its_lock_over(crawler, monkeypatch, lock_file, fake_time, capsys):
    items = _episodes(crawler.world, "tv", 4)
    monkeypatch.setattr(gp_inventory, "DETAIL_BATCH", 1)
    real, asked = gprpc.batch_info, []

    def batch_info(driver, ids):
        asked.append(list(ids))
        if len(asked) == 2:  # a fetch waited 30 s, then reclaimed the lock (mvcommon's blocking acquire)
            os.remove(lock_file)
            lock_file.write_bytes(b"99999 1790000000.0\n")
        fake_time.now += 120
        return real(driver, ids)

    monkeypatch.setattr(gprpc, "batch_info", batch_info)
    assert crawler.run("tv") == gp_inventory.EXIT_LOCK
    assert len(asked) == 2  # it stopped at its next heartbeat
    assert "STOPPED: the fetch session lock was taken over by another MediaVault command" in capsys.readouterr().out
    assert crawler.events("tv", "run001")[-1]["result"] == "lock lost"
    assert crawler.state()["detailed"] == {i.photo_id for i in items[-2:]}  # the work done so far is kept


# ---------------------------------------------------------------- rpc mode

def test_an_rpc_run_writes_the_record_format_with_the_rpc_facts(crawler, capsys):
    world = crawler.world
    a = world.add_item("tv", "Sample.Show.S01E01 [a1a1a1].mkv", taken="2022-02-05T11:49:46Z", duration_ms=1_500_000)
    b = world.add_item("tv", "Sample  Double   Space [b2b2b2].mkv", taken="2023-03-03T03:03:03Z", thumb_shape="long")
    assert crawler.run("tv") == 0
    lines = crawler.lines("tv", "items")
    assert [r["photo_id"] for r in lines] == [b.photo_id, a.photo_id]  # timeline order, newest first
    for rec, item in zip(lines, (b, a)):
        assert list(rec) == RPC_KEYS  # the file's key order, the rpc facts after upload_ms, source last
        assert {k: v for k, v in rec.items() if k not in ("crawled_at", "source")} == _expected_rpc_record(item, "tv")
        assert rec["source"] == "rpc" and rec["crawled_at"].endswith("+00:00")
    assert lines[0]["filename"] == "Sample  Double   Space [b2b2b2].mkv"  # exact — no innerText collapse (P-6)
    sent = world.rpc_ids_sent()
    assert set(sent) <= CRAWLER_RPCIDS and "EzkLib" not in sent  # a first run: the timeline pass alone
    out = capsys.readouterr().out
    assert "enumeration complete (rpc): 2 ids" in out and "RPC calls: 2 (errors 0)" in out
    assert not any(marker in out for marker in WEB_MARKERS)


@pytest.mark.parametrize("account", ACCOUNT_KEYS)
def test_rpc_and_dom_crawls_record_the_same_items_full_and_incremental(crawler, account):
    """PLAN Step 7: rpc full + incremental runs produce the same record set as the dom mode on the same fake
    account — all four accounts (movies renders UK English)."""
    world = crawler.world

    def add(tag, taken, **attrs):
        return world.add_item(account, f"Sample.Title.{tag} [{tag.lower()}0000].mkv", taken=taken, **attrs)

    add("A1", "2021-01-01T10:00:00Z")
    add("B2", "2024-05-03T23:18:02Z", tz_offset_min=0)
    add("C3", "2025-12-31T12:50:17Z", width=1920, height=800, thumb_shape="long")
    world.add_item(account, "Sample  Double   Space [d4d4d4].mkv", taken="2026-09-10T01:16:46Z")
    base = crawler.out.parent

    def crawl_both():
        crawler.out = base / "dom"
        assert crawler.run(account, "--mode", "dom") == 0
        crawler.out = base / "rpc"
        before = len(world.rpc_log)
        assert crawler.run(account) == 0
        return world.rpc_log[before:]

    def assert_same_records():
        dom = gp_inventory.inventory_state(account, str(base / "dom"))
        rpc = gp_inventory.inventory_state(account, str(base / "rpc"))
        assert set(dom["items"]) == set(rpc["items"]) == dom["detailed"] == rpc["detailed"]
        for pid, d in dom["items"].items():
            r, item = rpc["items"][pid], world.item(pid)
            assert {k: d[k] for k in IDENTITY} == {k: r[k] for k in IDENTITY} == {k: getattr(item, k) for k in IDENTITY}
            assert r["filename"] == item.filename and r["source"] == "rpc" and "source" not in d
            assert gpweb.names_match(d["filename"], item.filename) in ("exact", "normalized")  # innerText collapses
        assert dom["latest_complete"]["ids"] == rpc["latest_complete"]["ids"] == frozenset(dom["items"])

    sent = crawl_both()
    assert set(e["rpcid"] for e in sent) <= CRAWLER_RPCIDS
    assert_same_records()
    new = [add("E5", "2026-09-20T08:00:00Z"), add("F6", "2020-06-06T06:06:06Z", upload_ms=1_790_500_000_000)]
    sent = crawl_both()
    assert any(e["rpcid"] == "EzkLib" and e["request"][1] == [[4, "ra", 0, 0]] for e in sent)  # recently uploaded
    assert sorted(_ewgk9e_ids(sent)) == sorted(i.photo_id for i in new)  # details only for the new ids
    assert_same_records()


def test_rpc_records_the_partial_upload_quality_and_archived_flags(crawler):
    world = crawler.world
    plain = world.add_item("movies", "Sample.Movie.2019 [c1c1c1].chunk.001.mkv")
    partial = world.add_item("movies", "Sample.Movie.2019 [c1c1c1].chunk.002.mkv", partial_upload=True)
    saver = world.add_item("movies", "Sample.Saver.2007 [d1d1d1].mkv", original_quality=False)
    archived = world.add_item("movies", "Sample.Archived.2010 [e1e1e1].mkv", archived=True)
    assert crawler.run("movies") == 0
    items = crawler.state("movies")["items"]
    flags = {pid: (r["partial_upload"], r["original_quality"], r["archived"]) for pid, r in items.items()}
    assert flags == {plain.photo_id: (False, True, False), partial.photo_id: (True, True, False),
                     saver.photo_id: (False, False, False), archived.photo_id: (False, True, True)}
    assert "  rpc flags          partial upload 1, not original quality 1, archived 1" in crawler.status("movies")


@pytest.mark.parametrize("breakage", ["http500", "token_missing", "changed_shape"])
def test_rpc_unavailable_falls_back_to_the_dom_crawl_with_one_warning(crawler, capsys, breakage):
    world = crawler.world
    items = _episodes(world, "tv", 3)
    if breakage == "http500":
        world.fail_next_rpc("http500", count=4, rpcid="lcxiM")
    elif breakage == "token_missing":
        world.wiz_token_missing["tv"] = True
    else:
        world.fail_next_rpc("changed_shape", rpcid="lcxiM")
    assert crawler.run("tv") == 0
    out = capsys.readouterr().out
    assert out.count("WARNING: RPC unavailable") == 1 and "falling back to the dom crawl" in out
    out.encode("cp1252")  # every line the crawler prints survives a legacy Windows console
    state = crawler.state()
    assert state["detailed"] == {i.photo_id for i in items}
    assert all("source" not in r and "details_text" in r for r in state["items"].values())  # dom records
    enumerated, end = crawler.events("tv", "run001")[1:]
    assert (enumerated["event"], enumerated["mode"], enumerated["ids"]) == ("enumerated", "dom", 3)
    assert (end["result"], end["mode"], end["detailed"]) == ("ok", "dom", 3) and end["fallback"]
    assert set(world.rpc_ids_sent()) <= {"lcxiM"}  # nothing after the failure


def test_rpc_failure_during_the_details_falls_back_to_dom_details(crawler, capsys):
    world = crawler.world
    items = _episodes(world, "tv", 3)
    world.fail_next_rpc("http500", count=4, rpcid="EWgK9e")
    assert crawler.run("tv") == 0
    assert capsys.readouterr().out.count("WARNING: RPC unavailable") == 1
    enumerated = crawler.events("tv", "run001")[1]
    assert (enumerated["event"], enumerated["mode"], enumerated["ids"]) == ("enumerated", "rpc", 3)
    state = crawler.state()
    assert state["detailed"] == {i.photo_id for i in items}
    assert all("source" not in r for r in state["items"].values())
    assert crawler.events("tv", "run001")[-1]["mode"] == "dom"


def test_a_timeline_paging_loop_is_never_followed(crawler, monkeypatch, capsys):
    item = crawler.world.add_item("tv", "Sample.Show.S01E01 [a1a1a1].mkv")
    monkeypatch.setattr(crawler.world, "_h_lcxiM", lambda acc, req: (
        [[gphotos_fake.item_array(item)], "FAKEP:loop", item.taken_ms], None))
    assert crawler.run("tv") == 0
    assert crawler.world.rpc_ids_sent().count("lcxiM") == 2
    assert "paging loop not followed" in capsys.readouterr().out
    assert crawler.state()["detailed"] == {item.photo_id}  # the dom crawl took over


def test_unreadable_timeline_records_leave_the_enumeration_incomplete(crawler, monkeypatch, capsys):
    items = _episodes(crawler.world, "tv", 2)
    real = crawler.world._h_lcxiM

    def with_a_record_of_another_shape(acc, req):
        payload, code = real(acc, req)
        payload[0].append(["AF1QipFAKEbroken00000", "not a thumbnail"])  # fails the P3 signature
        return payload, code

    monkeypatch.setattr(crawler.world, "_h_lcxiM", with_a_record_of_another_shape)
    assert crawler.run("tv") == 0
    assert "1 timeline record(s) had an unexpected shape" in capsys.readouterr().out
    state = crawler.state()
    assert state["latest_complete"] is None and state["detailed"] == {i.photo_id for i in items}
    assert [e["event"] for e in crawler.events("tv", "run001")] == ["start", "end"]
    assert "  enumeration        none complete yet — run: python tools/gp_inventory.py tv --port <port>" \
        in crawler.status()


def test_max_limits_the_details_of_one_run(crawler):
    items = _episodes(crawler.world, "tv", 5)
    assert crawler.run("tv", "--max", "2") == 0
    assert crawler.state()["detailed"] == {i.photo_id for i in items[-2:]}  # the newest two
    assert crawler.run("tv") == 0
    assert crawler.state()["detailed"] == {i.photo_id for i in items}
    assert len(crawler.lines("tv", "items")) == 5


# ---------------------------------------------------------------- the run log + deletion detection

@pytest.mark.parametrize("mode", ["rpc", "dom"])
def test_every_run_logs_every_id_it_saw_and_details_only_new_ids(crawler, mode):
    world = crawler.world
    old = _episodes(world, "tv", 3)
    assert crawler.run("tv", "--mode", mode) == 0
    new = _episodes(world, "tv", 1, first=4)
    assert crawler.run("tv", "--mode", mode) == 0
    ids = {i.photo_id for i in old + new}
    for run_id, expected, fresh in (("run001", ids - {new[0].photo_id}, 3), ("run002", ids, 1)):
        start, enumerated, end = crawler.events("tv", run_id)
        assert (start["event"], start["account"], start["mode"], start["phase"]) == ("start", "tv", mode, "all")
        assert set(crawler.seen("tv", run_id)) == expected  # every id seen — not only the new ones
        assert len(crawler.seen("tv", run_id)) == len(expected)  # once per run
        assert (enumerated["event"], enumerated["mode"], enumerated["ids"]) == ("enumerated", mode, len(expected))
        assert (end["event"], end["result"], end["new"], end["detailed"], end["errors"]) == ("end", "ok", fresh, fresh, 0)
    assert len(crawler.lines("tv", "items")) == 4  # the second run detailed only the new id
    assert crawler.state()["latest_complete"]["run"] == "run002"


@pytest.mark.parametrize("mode", ["rpc", "dom"])
def test_known_ids_absent_from_the_latest_complete_enumeration_are_possibly_deleted(crawler, mode):
    a, b, c = _episodes(crawler.world, "tv", 3)
    assert crawler.run("tv", "--mode", mode) == 0
    assert crawler.state()["possibly_deleted"] == []
    b.deleted = True
    assert crawler.run("tv", "--mode", mode) == 0
    state = crawler.state()
    assert state["possibly_deleted"] == [b.photo_id] and state["latest_complete"]["ids"] == {a.photo_id, c.photo_id}
    assert "  possibly deleted   1  (known ids absent from that enumeration)" in crawler.status()
    b.deleted = False  # it was only hidden for a while
    assert crawler.run("tv", "--mode", mode) == 0
    assert crawler.state()["possibly_deleted"] == []


def test_an_interrupted_enumeration_never_counts_and_new_ids_are_never_called_deleted(crawler, monkeypatch):
    monkeypatch.setattr(gp_inventory, "TIMELINE_PAGE_SIZE", 1)
    a, b, c = _episodes(crawler.world, "tv", 3)
    assert crawler.run("tv") == 0
    b.deleted = True
    d = _episodes(crawler.world, "tv", 1, first=9)[0]  # uploaded after run001
    real, pages = gprpc.timeline_at, []

    def interrupted(driver, *args, **kwargs):
        pages.append(1)
        if len(pages) == 2:
            raise KeyboardInterrupt  # Ctrl+C in the middle of the timeline pass
        return real(driver, *args, **kwargs)

    monkeypatch.setattr(gprpc, "timeline_at", interrupted)
    assert crawler.run("tv") == 0  # interrupted: progress kept
    assert [e["event"] for e in crawler.events("tv", "run002")] == ["start", "end"]
    assert crawler.events("tv", "run002")[-1]["result"] == "interrupted"
    state = crawler.state()
    assert state["latest_complete"]["run"] == "run001" and d.photo_id in state["known_ids"]
    assert state["possibly_deleted"] == []  # b was listed by run001; d was first seen after run001 began
    monkeypatch.setattr(gprpc, "timeline_at", real)
    assert crawler.run("tv") == 0
    state = crawler.state()
    assert state["latest_complete"]["run"] == "run003" and state["possibly_deleted"] == [b.photo_id]
    assert d.photo_id in state["detailed"]


def test_deletion_detection_needs_no_clock_the_run_log_order_decides(crawler, monkeypatch):
    """Every line stamped the same second (a coarse or skewed clock): the run log's ORDER still tells an id that
    existed before the enumeration from one first seen after it."""
    monkeypatch.setattr(gp_inventory, "now_iso", lambda: "2026-09-28T10:00:00+00:00")
    a, b, c = _episodes(crawler.world, "tv", 3)
    assert crawler.run("tv") == 0
    b.deleted = True
    assert crawler.run("tv") == 0
    assert crawler.state()["possibly_deleted"] == [b.photo_id]  # listed by run001, absent from run002
    e = _episodes(crawler.world, "tv", 1, first=9)[0]
    real = gprpc.timeline_at

    def interrupted(driver, *args, **kwargs):
        raise KeyboardInterrupt  # run003 saw e in the recently-uploaded listing, then Ctrl+C

    monkeypatch.setattr(gprpc, "timeline_at", interrupted)
    assert crawler.run("tv") == 0
    monkeypatch.setattr(gprpc, "timeline_at", real)
    state = crawler.state()
    assert state["latest_complete"]["run"] == "run002" and e.photo_id in state["known_ids"]
    assert state["possibly_deleted"] == [b.photo_id]  # e was first seen after run002 began: new, not deleted


def test_an_enumeration_line_that_does_not_match_its_seen_lines_is_not_complete(crawler):
    _episodes(crawler.world, "tv", 2)
    assert crawler.run("tv") == 0
    runs = crawler.out / "tv.runs.jsonl"
    text = runs.read_text(encoding="utf-8")
    assert text.count('"ids": 2') == 1
    runs.write_text(text.replace('"ids": 2', '"ids": 3'), encoding="utf-8")  # a seen line went missing
    state = crawler.state()
    assert state["latest_complete"] is None and state["runs"][0]["complete"] is False


def test_an_empty_enumeration_never_makes_the_inventory_look_deleted(crawler, capsys):
    items = _episodes(crawler.world, "tv", 2)
    assert crawler.run("tv") == 0
    for item in items:
        item.deleted = True
    assert crawler.run("tv") == 0
    assert "the enumeration found no items although the inventory knows some" in capsys.readouterr().out
    state = crawler.state()
    assert state["latest_complete"]["run"] == "run001" and state["possibly_deleted"] == []


def test_a_dom_enumeration_does_not_flag_archived_items(crawler):
    live = crawler.world.add_item("tv", "Sample.Show.S01E01 [a1a1a1].mkv")
    archived = crawler.world.add_item("tv", "Sample.Show.S01E02 [a1a1a1].mkv", archived=True)
    gone = crawler.world.add_item("tv", "Sample.Show.S01E03 [a1a1a1].mkv")
    assert crawler.run("tv") == 0  # rpc lists the archive too
    assert crawler.state()["items"][archived.photo_id]["archived"] is True
    gone.deleted = True
    assert crawler.run("tv", "--mode", "dom") == 0  # the dom timeline hides the archive
    state = crawler.state()
    assert state["latest_complete"]["mode"] == "dom" and state["latest_complete"]["ids"] == {live.photo_id}
    assert state["possibly_deleted"] == [gone.photo_id]


def test_details_never_chase_possibly_deleted_ids(crawler):
    a, b = _episodes(crawler.world, "tv", 2)
    assert crawler.run("tv", "--mode", "dom", "--phase", "enumerate") == 0
    b.deleted = True
    assert crawler.run("tv", "--mode", "dom", "--phase", "enumerate") == 0
    assert crawler.run("tv", "--mode", "dom", "--phase", "details") == 0
    lines = crawler.lines("tv", "items")
    assert [r["photo_id"] for r in lines] == [a.photo_id] and "error" not in lines[0]  # no error record for b


# ---------------------------------------------------------------- resume after a crash

def test_an_rpc_crawl_resumes_after_a_crash_mid_details_without_redoing_finished_items(crawler, monkeypatch, lock_file):
    items = _episodes(crawler.world, "tv", 7)
    monkeypatch.setattr(gp_inventory, "DETAIL_BATCH", 3)
    real, asked = gprpc.batch_info, []

    def crashing(driver, ids):
        asked.append(list(ids))
        if len(asked) == 2:
            raise HardCrash()
        return real(driver, ids)

    monkeypatch.setattr(gprpc, "batch_info", crashing)
    with pytest.raises(HardCrash):
        crawler.run("tv")
    finished = set(asked[0])
    assert crawler.state()["detailed"] == finished and len(finished) == 3
    assert not lock_file.exists() and crawler.procs[-1].terminated  # nothing left behind
    asked.clear()
    monkeypatch.setattr(gprpc, "batch_info", lambda driver, ids: asked.append(list(ids)) or real(driver, ids))
    assert crawler.run("tv") == 0
    assert set(sum(asked, [])) == {i.photo_id for i in items} - finished  # the finished ones are never asked again
    assert len(crawler.lines("tv", "items")) == 7 and crawler.state()["errored"] == set()


def test_a_dom_crawl_resumes_after_a_crash_mid_details_without_redoing_finished_items(crawler, monkeypatch):
    items = _episodes(crawler.world, "tv", 4)
    real, opened = gp_inventory.crawl_item, []

    def crashing(driver, pid):
        opened.append(pid)
        if len(opened) == 3:
            raise HardCrash()
        return real(driver, pid)

    monkeypatch.setattr(gp_inventory, "crawl_item", crashing)
    with pytest.raises(HardCrash):
        crawler.run("tv", "--mode", "dom")
    finished = opened[:2]
    opened.clear()
    assert crawler.run("tv", "--mode", "dom") == 0
    assert sorted(opened) == sorted({i.photo_id for i in items} - set(finished))
    assert len(crawler.lines("tv", "items")) == 4 and crawler.state()["errored"] == set()


def test_torn_lines_from_a_hard_kill_are_skipped_and_never_swallow_a_record(crawler):
    _episodes(crawler.world, "tv", 2)
    assert crawler.run("tv") == 0
    with open(crawler.out / "tv.items.jsonl", "ab") as fh:
        fh.write(b'{"photo_id": "AF1QipFAKEtorn00001", "filename": "Sample \xe0\xae')
    with open(crawler.out / "tv.runs.jsonl", "ab") as fh:
        fh.write(b'{"photo_id": "AF1QipFAKEto')
    new = _episodes(crawler.world, "tv", 1, first=5)[0]
    assert crawler.run("tv") == 0
    state = crawler.state()
    assert new.photo_id in state["detailed"] and len(state["detailed"]) == 3
    assert "AF1QipFAKEtorn00001" not in state["known_ids"] and len(state["problems"]) == 2
    assert state["latest_complete"]["run"] == "run002"
    assert "  unreadable lines   2  (torn writes — skipped)" in crawler.status()


# ---------------------------------------------------------------- the inventories crawled before Step 7

def test_rpc_upgrades_a_dom_crawled_inventory_once_and_only_appends(crawler):
    world = crawler.world
    items = [world.add_item("tv", "Sample.Show.S02E03.Title [a1b2c3].mkv", taken="2022-02-05T11:49:46Z"),
             world.add_item("tv", "Sample  Double   Space [0a0b0c].mkv", taken="2025-01-01T15:47:43Z"),
             world.add_item("tv", "Sample.Show.S03E01 [d4e5f6].chunk.002.mkv", taken="2026-09-10T01:16:46Z",
                            thumb_shape="long")]
    crawler.out.mkdir()
    _crawl(world, "tv", str(crawler.out))  # the pre-Step-7 crawler, exactly as the golden test runs it
    legacy = {pid: rec for pid, rec in crawler.state()["items"].items()}
    before = {kind: (crawler.out / f"tv.{kind}.jsonl").read_bytes() for kind in ("tiles", "items")}
    sent_before = len(world.rpc_log)
    assert crawler.run("tv") == 0
    assert (crawler.out / "tv.items.jsonl").read_bytes().startswith(before["items"])  # append-only
    assert (crawler.out / "tv.tiles.jsonl").read_bytes() == before["tiles"]
    assert sorted(_ewgk9e_ids(world.rpc_log[sent_before:])) == sorted(i.photo_id for i in items)
    state = crawler.state()
    for item in items:
        rec, old = state["items"][item.photo_id], legacy[item.photo_id]
        assert rec["source"] == "rpc" and rec["filename"] == item.filename  # the exact name now
        assert {k: rec[k] for k in gp_inventory._HINT_KEYS + ("tile_label",)} == \
            {k: old[k] for k in gp_inventory._HINT_KEYS + ("tile_label",)}  # the dom hints are kept
        assert list(rec)[:5] == ["photo_id", "details_text", "tz_text", "time_text", "date_text"]
    end = crawler.events("tv", "run001")[-1]
    assert (end["upgraded"], end["detailed"], end["new"]) == (3, 0, 0)
    sent_before = len(world.rpc_log)
    assert crawler.run("tv") == 0
    assert _ewgk9e_ids(world.rpc_log[sent_before:]) == []  # upgraded once


def test_a_failed_upgrade_never_replaces_a_good_dom_record(crawler, monkeypatch):
    world = crawler.world
    keep, drop = _episodes(world, "tv", 2)
    crawler.out.mkdir()
    _crawl(world, "tv", str(crawler.out))
    real = world._h_EWgK9e

    def without_drop(acc, req):
        payload, code = real(acc, req)
        payload[0][1] = [row for row in payload[0][1] if row[0] != drop.photo_id]
        return payload, code

    monkeypatch.setattr(world, "_h_EWgK9e", without_drop)
    assert crawler.run("tv") == 0
    state = crawler.state()
    assert state["items"][keep.photo_id]["source"] == "rpc"
    assert "source" not in state["items"][drop.photo_id] and state["errored"] == set()
    assert not any(r.get("error") for r in crawler.lines("tv", "items"))


# ---------------------------------------------------------------- --status (offline)

def test_status_of_an_inventory_crawled_before_run_logging(tmp_path):
    world, gone = _golden_world(tmp_path)
    out = tmp_path / "legacy"
    out.mkdir()
    _crawl(world, "tv", str(out), delete_after_enumerate=(gone,))
    lines = []
    gp_inventory.print_status("tv", str(out), out=lines.append)
    assert lines == [f"tv: Google Photos inventory in {out}",
                     "  ids                5",
                     "  detailed           4  (rpc 0, dom 4)",
                     "  errored            1  (retried by the next run)",
                     "  not yet detailed   0",
                     "  enumeration        none complete yet — run: python tools/gp_inventory.py tv --port <port>",
                     "  rpc flags          partial upload 0, not original quality 0, archived 0"]


def test_status_after_crawls(crawler):
    _, gone, _ = _episodes(crawler.world, "tv", 3)
    assert crawler.run("tv") == 0
    gone.deleted = True
    assert crawler.run("tv") == 0
    started = crawler.events("tv", "run002")[0]["at"]
    lines = crawler.status(now=datetime.fromisoformat(started) + timedelta(hours=3))
    when = datetime.fromisoformat(started).strftime("%Y-%m-%d %H:%M:%S UTC")
    assert lines[1:] == ["  ids                3",
                         "  detailed           3  (rpc 3, dom 0)",
                         "  errored            0",
                         "  not yet detailed   0",
                         f"  enumeration        complete as of {when} — rpc, 2 ids, 3.0 h ago",
                         "  possibly deleted   1  (known ids absent from that enumeration)",
                         "  rpc flags          partial upload 0, not original quality 0, archived 0",
                         f"  latest run         {when} rpc/all — ok"]


def test_status_reads_only_and_takes_no_lock(crawler, lock_file, capsys):
    _episodes(crawler.world, "tv", 2)
    assert crawler.run("tv") == 0
    capsys.readouterr()
    snapshot = {p: (p.stat().st_size, p.stat().st_mtime_ns, p.read_bytes()) for p in crawler.out.rglob("*")}
    with mvcommon.fetch_session_lock(blocking=False):  # even while a fetch runs
        held = lock_file.read_bytes()
        assert gp_inventory.main(["all", "--status", "--out", str(crawler.out)]) == 0
        assert lock_file.read_bytes() == held
    assert {p: (p.stat().st_size, p.stat().st_mtime_ns, p.read_bytes()) for p in crawler.out.rglob("*")} == snapshot
    out = capsys.readouterr().out
    assert [line.split(":")[0] for line in out.splitlines() if not line.startswith(" ")] == list(ACCOUNT_KEYS)
    assert out.count("no inventory yet") == 3 and "  detailed           2  (rpc 2, dom 0)" in out


# ---------------------------------------------------------------- the command line

@pytest.mark.parametrize("argv", [["tv"], ["all", "--port", "9225"], ["tv", "--port", "9225", "--status"],
                                  ["tv", "--attach", "9222", "--port", "9225"], ["tv", "--port", "9225", "--mode", "x"]],
                         ids=repr)
def test_the_command_line_refuses_an_invalid_combination(crawler, argv):
    with pytest.raises(SystemExit) as raised:
        gp_inventory.main(argv)
    assert raised.value.code == 2 and crawler.launched == []


def test_ctrl_c_while_chrome_starts_still_closes_that_chrome(crawler, monkeypatch, fake_time, lock_file, capsys):
    real_sleep = fake_time.sleep

    def sleep(seconds):
        if seconds == gp_inventory.CHROME_START_WAIT_S:
            raise KeyboardInterrupt
        real_sleep(seconds)

    monkeypatch.setattr(fake_time, "sleep", sleep)
    assert crawler.run("tv") == 0
    assert crawler.procs[0].terminated and crawler.drivers == [] and not lock_file.exists()
    assert "interrupted — progress is saved" in capsys.readouterr().out


def test_port_9222_is_never_launched(crawler, capsys):
    assert crawler.run("tv", port=9222) == gp_inventory.EXIT_USAGE
    assert "use a port other than 9222" in capsys.readouterr().out and crawler.launched == []


def test_without_out_a_run_uses_the_inventory_dir_the_test_suite_redirects(crawler, tmp_path, monkeypatch):
    assert gp_inventory.DEFAULT_OUT == r"D:\MediaVault_gp_inventory"  # the documented default (help text)
    decoy = tmp_path / "decoy_default_out"
    monkeypatch.setattr(gp_inventory, "DEFAULT_OUT", str(decoy))  # a regression to DEFAULT_OUT lands HERE, never on D:
    crawler.world.add_item("tv", "Sample.Show.S01E01 [a1a1a1].mkv")
    crawler.account = "tv"
    assert gp_inventory.main(["tv", "--port", "9225"]) == 0
    assert os.path.isfile(os.path.join(mvcommon.GP_INVENTORY_DIR, "tv.items.jsonl")) and not decoy.exists()
    assert tmp_path.resolve() in type(tmp_path)(mvcommon.GP_INVENTORY_DIR).resolve().parents
