"""tools/gp_inventory.py parsing (IMP-C25). Synthetic fixtures only — no real ids/URLs."""
import importlib.util
import os
import contextlib
import json
import re
import types
from unittest import mock

import pytest

import gphotos_fake
import gpweb

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
