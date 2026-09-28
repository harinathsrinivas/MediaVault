"""gpweb.py — the shared Google Photos web + RPC parser module (IMP-C25 Step 2).

Table tests for every parser (US / UK / year-less / U+202F / en-dash / 24 h / collapsed spaces / both
thumbnail shapes / escaped slashes / invalid shapes / every typed RPC parser) plus the fake Google Photos
self-tests (tests/gphotos_fake.py) at the bottom. SYNTHETIC data only: every photo id contains "FAKE";
the only real identity value is the published S02E03 example of RESEARCH F21 (SHA-1 → dedupKey).
RPC payloads are re-created from PLAN P3/P12/P14 — no third-party fixture is copied.
"""
import doctest
import hashlib
import json
import os
import subprocess
import sys
import time
import urllib.parse
from datetime import date, datetime
from pathlib import Path

import pytest
from selenium.common.exceptions import (JavascriptException, NoSuchWindowException, StaleElementReferenceException,
                                        WebDriverException)
from selenium.webdriver import ActionChains
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys

import gpcapture
import gphotos_fake
import gpweb
import mainfetch

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NNBSP, NBSP = "\N{NARROW NO-BREAK SPACE}", "\N{NO-BREAK SPACE}"  # what Google puts before AM / PM
EN, MINUS = "\N{EN DASH}", "\N{MINUS SIGN}"

PID = "AF1QipFAKEgpwebAAAA0123456789abcdefFAKE"
PID_B = "AF1QipFAKEgpwebBBBB0123456789abcdefFAKE"
PID_C = "AF1QipFAKEgpwebCCCC0123456789abcdefFAKE"
SHA1 = "85df66012c86f692618ac3d7152dd9afb32aaf6f"  # S02E03 (RESEARCH F21) — published example
DEDUP = "hd9mASyG9pJhisPXFS3Zr7Mqr28"
TAKEN = 1644061786000                               # 2022-02-05T11:49:46Z (RESEARCH F20)
TZ = 28800000                                       # GMT+08:00
UPLOAD = 1790104139274


def _key(tag):
    """A synthetic dedupKey: the real key of synthetic bytes."""
    return gpcapture.dedup_key(hashlib.sha1(tag.encode()).hexdigest())


DEDUP_B, DEDUP_C = _key("FAKE-B"), _key("FAKE-C")


# ============================================================================ page record

def _record(pid=PID, dedup=DEDUP, thumb_tail="", url="https://example.invalid/t", w=3840, h=2160,
            taken=TAKEN, tz=TZ, upload=UPLOAD, rest=',["{pid}"],[[1],[2]],2,null'):
    return (f'["{pid}",["{url}",{w},{h}{thumb_tail}],{taken},"{dedup}",{tz},{upload}'
            + rest.format(pid=pid) + "]")


def _page(*records):
    """An item page as Selenium's page_source shows it: records inside AF_initDataCallback data."""
    data = ",".join(records)
    return ("<html><head><script nonce=\"x\">AF_initDataCallback({key: 'ds:0', hash: '2', data:["
            + data + "], sideChannel: {}});</script></head><body><div>Photo</div></body></html>")


RECORD = {"photo_id": PID, "width": 3840, "height": 2160, "taken_ms": TAKEN, "dedup_key": DEDUP,
          "tz_offset_ms": TZ, "upload_ms": UPLOAD}
LONG_TAIL = ",null,null,null,null,null,null,[1053204]"


@pytest.mark.parametrize("src", [
    _page(_record()),                                                   # short thumbnail shape
    _page(_record(thumb_tail=LONG_TAIL)),                               # long shape (commit 70635cb)
    _page(_record(url="https:\\/\\/example.invalid\\/t")),              # JS-escaped slashes
    _page(_record(thumb_tail=',"x",[[1,2]]')),                          # a tail the old regex cannot read
    _page(_record(rest=',["{pid}"],undefined,\\x3d')),                  # non-JSON after the six fields
    _page('["' + PID + '",["only a mention"]]', _record()),             # the id is mentioned first
    _page(_record(pid=PID_B, dedup=DEDUP_B), _record()),                # a neighbour comes first
], ids=["short-thumb", "long-thumb", "escaped-slashes", "nested-tail", "non-json-tail",
        "mention-first", "neighbour-first"])
def test_page_record_reads_every_known_shape(src):
    assert gpweb.page_record(src, PID) == RECORD


@pytest.mark.parametrize("src", [
    "<html>nothing here</html>",
    _page(_record(dedup=DEDUP[:-1])),                  # dedupKey not 27 chars
    _page(_record(taken=164406178)),                   # taken_ms too short to be a time
    _page(_record(upload="null")),                     # upload time missing
    _page(_record(w=-5)),                              # negative width
    _page(_record(pid=PID_B)),                         # only another item's record
], ids=["absent", "short-dedup", "short-taken", "no-upload", "negative-width", "other-item"])
def test_page_record_rejects_invalid_shapes(src):
    assert gpweb.page_record(src, PID) == {}


@pytest.mark.parametrize("bad", [None, 42, b"bytes"])
def test_page_record_never_raises(bad):
    assert gpweb.page_record(bad, PID) == {}
    assert gpweb.page_record("<html/>", bad) == {}
    assert gpweb.page_records(bad) == []


def test_page_records_lists_the_item_and_its_neighbours_once_each_in_page_order():
    src = _page(_record(pid=PID_B, dedup=DEDUP_B), _record(thumb_tail=LONG_TAIL),
                _record(pid=PID_C, dedup=DEDUP_C, rest=",undefined"), _record())
    recs = gpweb.page_records(src)
    assert [r["photo_id"] for r in recs] == [PID_B, PID, PID_C]
    assert recs[1] == RECORD and recs[0]["dedup_key"] == DEDUP_B and recs[2]["dedup_key"] == DEDUP_C


@pytest.mark.parametrize("url,expected", [
    (f"https://photos.google.com/photo/{PID}", PID),
    (f"https://photos.google.com/u/1/photo/{PID}?authuser=1", PID),
    (f"./search/_tra_FAKEtoken/photo/{PID}", PID),
    (f"https://photos.google.com/lr/photo/{PID}#info", PID),
    ("https://photos.google.com/search/2022-02-05", None),
    ("https://photos.google.com/photo/", None),
    (None, None),
])
def test_photo_id_from_url(url, expected):
    assert gpweb.photo_id_from_url(url) == expected


# ============================================================================ RPC envelope

def _frame(rpcid, payload, code=None):
    text = None if payload is None else json.dumps(payload, separators=(",", ":"))
    return ["wrb.fr", rpcid, text, None, None, [code] if code is not None else None, "generic"]


def _body(*frames, same_line_length=False):
    """A batchexecute answer as fetch() returns it with rt=c: XSSI guard, then length-prefixed chunks."""
    chunk = json.dumps(list(frames) + [["di", 42], ["af.httprm", 41, "-7000000000000000000", 7]],
                       separators=(",", ":"))
    tail = f'[["e",4,null,null,{len(chunk)}]]'
    if same_line_length:
        return f")]}}'\n\n{len(chunk)}{chunk}\n{len(tail)}{tail}\n"
    return f")]}}'\n\n{len(chunk)}\n{chunk}\n{len(tail)}\n{tail}\n"


def _item(pid=PID, dedup=DEDUP, taken=TAKEN, tz=TZ, upload=UPLOAD, w=1920, h=1080, partial=None,
          archived=None, duration=1234567, length=16):
    """A P3 library item as RPC answers carry it (PLAN P3/P12): [12] partial-upload marker, [13] archived,
    last element = the extension object with key 76647426 (duration)."""
    arr = [pid, [f"https://example.invalid/thumb/{pid}", w, h, None, None, None, None, None, None, [1]],
           taken, dedup, tz, upload, None, None, None, None, None, None, partial, archived, None,
           {"76647426": [duration]}]
    return arr[:length]


def test_rpc_payload_unwraps_the_json_in_json_envelope():
    body = _body(_frame("VrseUb", [_item(), "https://example.invalid/display"]))
    payload = gpweb.rpc_payload(body)
    assert payload[0][0] == PID and payload[1] == "https://example.invalid/display"
    assert gpweb.rpc_payload(body.encode("utf-8"), "VrseUb") == payload       # bytes + rpcid filter
    assert gpweb.rpc_payload(_body(_frame("VrseUb", [1]), same_line_length=True)) == [1]


def test_rpc_payload_picks_the_first_frame_or_the_one_for_the_rpcid():
    body = _body(_frame("EzkLib", [[]]), _frame("VrseUb", [2]))
    assert gpweb.rpc_payload(body) == [[]]
    assert gpweb.rpc_payload(body, "VrseUb") == [2]
    with pytest.raises(gpweb.NoEnvelopeError):
        gpweb.rpc_payload(body, "lcxiM")


@pytest.mark.parametrize("body", [
    "<html><body>Error 500 (Server Error)</body></html>",
    ")]}'\n\n25\n[[\"e\",4,null,null,25]]\n",                                 # no wrb.fr frame
    ")]}'\n\n80\n[[\"wrb.fr\",\"VrseUb\",\"[1]\",null,null,null,\"gene",         # truncated line
    "", None, 12,
], ids=["html", "no-frame", "truncated", "empty", "none", "int"])
def test_rpc_payload_without_an_envelope(body):
    with pytest.raises(gpweb.NoEnvelopeError):
        gpweb.rpc_payload(body)
    assert gpweb.rpc_envelopes(body) == [] and gpweb.rpc_records(body) == []


def test_rpc_payload_error_frame_carries_rpcid_and_status_code():
    with pytest.raises(gpweb.EmptyPayloadError) as e:
        gpweb.rpc_payload(_body(_frame("VrseUb", None, code=5)))
    assert (e.value.rpcid, e.value.code) == ("VrseUb", 5)
    with pytest.raises(gpweb.EmptyPayloadError):
        gpweb.rpc_payload(_body(["wrb.fr", "VrseUb", "null", None, None, None, "generic"]))
    assert issubclass(gpweb.EmptyPayloadError, gpweb.RpcEnvelopeError)


def test_malformed_payload_error_never_echoes_the_answer():
    secret = "https://example.invalid/dl/FAKE-BEARER-TOKEN"
    body = _body(["wrb.fr", "pLFTfd", '["' + secret + '"', None, None, None, "generic"])  # not JSON
    with pytest.raises(gpweb.MalformedPayloadError) as e:
        gpweb.rpc_payload(body)
    assert secret not in str(e.value) and "FAKE-BEARER" not in repr(e.value)


def test_rpc_records_harvests_items_and_ext_records_from_every_frame():
    ext = [PID_C, None, "Sample.Show.S01E03 [c3c3c3].mkv", TAKEN, TZ, 734003200, 1920, 1080, None, None,
           None, DEDUP_C]
    body = (_body(_frame("lcxiM", [[_item(), _item(pid=PID_B, dedup=DEDUP_B)], "FAKEnext", "1644061786000"]))
            + _body(_frame("fDcn4b", [ext]), _frame("swbisb", [[["hash=", _item()]]]))
            + ")]}'\n\n9\n[[\"wrb.fr\",\"x\",\"[tru\n")  # a torn frame: ignored
    recs = gpweb.rpc_records(body)
    assert [(r["kind"], r["photo_id"], r["rpcid"]) for r in recs] == [
        ("item", PID, "lcxiM"), ("item", PID_B, "lcxiM"), ("ext", PID_C, "fDcn4b")]
    assert recs[0]["duration_ms"] == 1234567 and recs[0]["partial_upload"] is False
    assert recs[2] == {"kind": "ext", "rpcid": "fDcn4b", "photo_id": PID_C, "filename":
                       "Sample.Show.S01E03 [c3c3c3].mkv", "size_bytes": 734003200, "dedup_key": DEDUP_C,
                       "taken_ms": TAKEN, "tz_offset_ms": TZ}


def test_rpc_records_finds_records_nested_in_extension_objects():
    body = _body(_frame("EzkLib", [{"123": [[_item(pid=PID_B, dedup=DEDUP_B)]]}, None]))
    assert [r["photo_id"] for r in gpweb.rpc_records(body)] == [PID_B]


# ============================================================================ typed RPC parsers

def test_parse_lcxiM_items_flags_paging():
    payload = [[_item(partial=[20], duration=5400000), _item(pid=PID_B, dedup=DEDUP_B, archived=True),
                _item(pid=PID_C, dedup=DEDUP_C, length=6), ["not", "an", "item", "at", "all", 1]],
               "FAKEnextPage", "1644061786000"]
    page = gpweb.parse_lcxiM(payload)
    assert page["next_page_id"] == "FAKEnextPage" and page["last_timestamp_ms"] == TAKEN
    assert page["rejected"] == 1 and [r["photo_id"] for r in page["items"]] == [PID, PID_B, PID_C]
    a, b, c = page["items"]
    assert a == {"photo_id": PID, "width": 1920, "height": 1080, "taken_ms": TAKEN, "dedup_key": DEDUP,
                 "tz_offset_ms": TZ, "upload_ms": UPLOAD, "partial_upload": True, "archived": False,
                 "duration_ms": 5400000}
    assert (b["partial_upload"], b["archived"]) == (False, True)
    assert (c["partial_upload"], c["archived"], c["duration_ms"]) == (None, None, None)  # indexes missing


@pytest.mark.parametrize("payload,expected", [
    ([], {"items": [], "next_page_id": None, "last_timestamp_ms": None, "rejected": 0}),
    ([None, None, None], {"items": [], "next_page_id": None, "last_timestamp_ms": None, "rejected": 0}),
    ([[], "", 1644061786000], {"items": [], "next_page_id": None, "last_timestamp_ms": TAKEN, "rejected": 0}),
])
def test_parse_lcxiM_empty_pages(payload, expected):
    assert gpweb.parse_lcxiM(payload) == expected


@pytest.mark.parametrize("name", ["parse_lcxiM", "parse_EzkLib", "parse_swbisb", "parse_VrseUb",
                                  "parse_fDcn4b", "parse_EWgK9e", "parse_pLFTfd"])
@pytest.mark.parametrize("payload", [None, {"shape": "changed"}, "text", 7, [{"0": 1}, 2]])
def test_every_typed_parser_flags_a_changed_top_level_shape(name, payload):
    parsed = getattr(gpweb, name)(payload)
    assert parsed is None or not (parsed.get("items") or parsed.get("matches"))
    if not isinstance(payload, list):
        assert parsed is None


def test_parse_EzkLib_keeps_items_and_next_page():
    page = gpweb.parse_EzkLib([[_item()], "FAKEnext"])
    assert page == {"items": [gpweb.parse_lcxiM([[_item()]])["items"][0]], "next_page_id": "FAKEnext",
                    "rejected": 0}


def test_parse_swbisb_maps_hashes_to_records():
    std = gpweb.sha1_hex_to_std_b64(SHA1)
    payload = [[[std, _item()], [std, _item(pid=PID_B, dedup=DEDUP_B)], ["bad-pair"], [std, ["nope"]]]]
    res = gpweb.parse_swbisb(payload)
    assert res["rejected"] == 2 and [m["photo_id"] for m in res["matches"]] == [PID, PID_B]
    assert res["matches"][0]["hash_b64"] == std and res["matches"][0]["dedup_consistent"] is True
    assert res["matches"][1]["dedup_consistent"] is False  # a record whose key is not the queried hash
    assert gpweb.parse_swbisb([]) == {"matches": [], "rejected": 0}


def test_parse_VrseUb_urls_flags_and_dedup_position():
    payload = [_item(partial=[20]), "https://example.invalid/display", None, None, None, None, None,
               "https://example.invalid/original"]
    rec = gpweb.parse_VrseUb(payload)
    assert rec["photo_id"] == PID and rec["dedup_key"] == DEDUP and rec["partial_upload"] is True
    assert rec["display_url"] == "https://example.invalid/display"
    assert rec["download_original_url"] == "https://example.invalid/original"
    assert rec["original_quality"] is None  # not pre-resolved for VrseUb — read it from EWgK9e
    short = gpweb.parse_VrseUb([_item()])
    assert short["display_url"] is None and short["download_original_url"] is None
    moved = _item()
    moved[3], moved[11] = None, DEDUP  # key only at the item-info-ext position
    assert gpweb.parse_VrseUb([moved])["dedup_key"] == DEDUP
    assert gpweb.parse_VrseUb([["not an item"]]) is None and gpweb.parse_VrseUb([]) is None


def test_parse_fDcn4b_reads_the_ext_positions():
    ext = [PID, "description", "Sample Movie (2019) [abc123].mkv", TAKEN, TZ, 1073741824, 3840, 2160,
           None, None, None, DEDUP]
    assert gpweb.parse_fDcn4b([ext]) == {"photo_id": PID, "filename": "Sample Movie (2019) [abc123].mkv",
                                         "size_bytes": 1073741824, "dedup_key": DEDUP, "taken_ms": TAKEN,
                                         "tz_offset_ms": TZ}
    sparse = [PID, None, "a.mkv", None, None, None, None, None, None, None, None, DEDUP]
    assert gpweb.parse_fDcn4b([sparse])["size_bytes"] is None
    assert gpweb.parse_fDcn4b([ext[:11]]) is None       # no dedupKey slot
    assert gpweb.parse_fDcn4b([["x"] + ext[1:]]) is None  # not a photo id


def _info(name, quality, size=2147483648):
    info = [None, None, "", name, None, None, TAKEN, TZ, UPLOAD, size] + [None] * 20
    return info + ([[1, size, quality]] if quality is not None else [])


def test_parse_EWgK9e_filename_size_quality():
    payload = [[None, [[PID, _info("a [x].chunk.001.mkv", 2)], [PID_B, _info("b.mkv", 1)],
                       [PID_C, _info("c.mkv", None)], ["not-a-key!", _info("d.mkv", 2)]]]]
    res = gpweb.parse_EWgK9e(payload)
    assert res["rejected"] == 1
    a, b, c = res["items"]
    assert a == {"photo_id": PID, "filename": "a [x].chunk.001.mkv", "size_bytes": 2147483648,
                 "taken_ms": TAKEN, "tz_offset_ms": TZ, "upload_ms": UPLOAD, "original_quality": True}
    assert b["original_quality"] is False and c["original_quality"] is None
    assert gpweb.parse_EWgK9e([]) == {"items": [], "rejected": 0}
    assert gpweb.parse_EWgK9e([[None]]) == {"items": [], "rejected": 0}


def test_parse_pLFTfd():
    assert gpweb.parse_pLFTfd(["https://example.invalid/dl/1"]) == {"download_url": "https://example.invalid/dl/1"}
    for payload in ([], [None], ["not a url"], [["https://example.invalid/nested"]]):
        assert gpweb.parse_pLFTfd(payload) is None


def test_parsers_cover_exactly_the_read_only_rpcids_of_plan_p12():
    assert set(gpweb.PARSERS) == {"lcxiM", "EzkLib", "swbisb", "VrseUb", "fDcn4b", "EWgK9e", "pLFTfd"}


# ============================================================================ hash formats

FB = "+/8" + "A" * 24 + "="  # standard base64 of 0xFB 0xFF 0x00*18 — exercises '+' and '/'


def test_dedup_key_is_the_gpcapture_implementation(monkeypatch):
    assert gpweb.dedup_key(SHA1) == DEDUP == gpcapture.dedup_key(SHA1)
    assert gpweb.dedup_key(None) is None and gpweb.dedup_key("not-hex") is None
    monkeypatch.setattr(gpcapture, "dedup_key", lambda h: f"delegated:{h}")
    assert gpweb.dedup_key("abc") == "delegated:abc"  # a re-export, not a private copy


def test_hash_format_conversions():
    assert gpweb.sha1_hex_to_std_b64(SHA1) == DEDUP + "="
    assert gpweb.sha1_hex_to_std_b64("fbff" + "00" * 18) == FB
    assert gpweb.std_b64_to_dedup_key(FB) == "-_8" + "A" * 24
    assert gpweb.std_b64_to_dedup_key(DEDUP + "=") == DEDUP
    assert gpweb.dedup_key_to_sha1_hex(DEDUP) == SHA1
    assert gpweb.dedup_key_to_sha1_hex("-_8" + "A" * 24) == "fbff" + "00" * 18
    for bad in (None, "", "zz", SHA1[:-2], SHA1 + "00", 12):
        assert gpweb.sha1_hex_to_std_b64(bad) is None


@pytest.mark.parametrize("key,expected", [
    (DEDUP, DEDUP),
    (DEDUP + "=", DEDUP),
    (f"  {DEDUP}\n", DEDUP),
    (FB, "-_8" + "A" * 24),
    ("-_8" + "A" * 24, "-_8" + "A" * 24),
    (DEDUP[:-1] + "9", None),   # non-canonical: trailing bits set
    (DEDUP[:-1], None),
    ("hd9mASyG9pJhisPXFS3Zr7Mq!28", None),
    (None, None), (27, None),
])
def test_normalize_dedup_key(key, expected):
    assert gpweb.normalize_dedup_key(key) == expected


def test_dedup_keys_equal_across_spellings():
    assert gpweb.dedup_keys_equal(FB, "-_8" + "A" * 24)
    assert gpweb.dedup_keys_equal(DEDUP + "=", DEDUP)
    assert not gpweb.dedup_keys_equal(DEDUP, DEDUP_B)
    assert not gpweb.dedup_keys_equal(None, None) and not gpweb.dedup_keys_equal("x", "x")


def test_gpweb_imports_no_mediavault_module_and_no_selenium():
    """Import rule + 'stdlib only': a fresh interpreter importing gpweb loads none of these."""
    code = ("import sys, gpweb; print(sorted(m for m in ('gpcapture','mvcommon','mainfetch','main',"
            "'selenium') if m in sys.modules))")
    out = subprocess.run([sys.executable, "-c", code], cwd=REPO, capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == "[]"


# ============================================================================ display text

US_BODY = (f"Info\nAdd a description\nDetails\nFeb 5, 2022\nSat, 7:49{NNBSP}PM\nGMT+08:00\n"
           "Sample.Show.S02E03.Title [a1b2c3].mkv\n2.1MP\n1920 × 1080\nUploaded from Android device\n"
           "Backed up\nOriginal quality. Learn more\n")


@pytest.mark.parametrize("body,date_text,time_text,tz_text,gmt,filename", [
    (US_BODY, "Feb 5, 2022", f"Sat, 7:49{NNBSP}PM", "GMT+08:00", 480, "Sample.Show.S02E03.Title [a1b2c3].mkv"),
    ("Info\nDetails\nSep 10\nThu, 9:16 AM\nGMT+08:00\nX [b2c3d4].chunk.002.mkv\n8.3MP\n3840 × 2160\n",
     "Sep 10", "Thu, 9:16 AM", "GMT+08:00", 480, "X [b2c3d4].chunk.002.mkv"),                 # US, this year
    ("Info\nDetails\n31 Dec 2025\nWed, 20:50\nGMT+08:00\nSample Movie (2019) [c3d4e5].mkv\n8.3 MP\n",
     "31 Dec 2025", "Wed, 20:50", "GMT+08:00", 480, "Sample Movie (2019) [c3d4e5].mkv"),        # UK, 24 h
    ("Details\n21 Sept\nSun, 09:00\nGMT+08:00\n2.1 MP\n1920 × 1080\nUploaded from an Android device\n",
     "21 Sept", "Sun, 09:00", "GMT+08:00", 480, None),                                        # UK, no name
    ("Details\nJan 2, 2025\nThu, 12:20 AM\nGMT\nSample Anime - 01 [d4e5f6].mkv\n",
     "Jan 2, 2025", "Thu, 12:20 AM", "GMT", 0, "Sample Anime - 01 [d4e5f6].mkv"),               # UTC item
    ("Details\nMar 1\nSun, 1:05 PM\nGMT-5:30\nTwo Spaces Collapsed [e5f6a7].mkv\n",
     "Mar 1", "Sun, 1:05 PM", "GMT-5:30", -330, "Two Spaces Collapsed [e5f6a7].mkv"),
], ids=["us-other-year", "us-current-year", "uk-other-year", "uk-current-year-mp-space", "gmt-only",
        "negative-offset"])
def test_info_panel_locales(body, date_text, time_text, tz_text, gmt, filename):
    p = gpweb.info_panel(body)
    assert (p["date_text"], p["time_text"], p["tz_text"], p["gmt_offset_min"], p["filename"]) == \
        (date_text, time_text, tz_text, gmt, filename)


def test_info_panel_details_text_and_edge_layouts():
    p = gpweb.info_panel(US_BODY)
    assert p["details_text"].startswith("Details\nFeb 5, 2022\n") and len(p["details_text"]) <= 700
    assert gpweb.info_panel("Share\nZoom in\nNo panel here") is None
    assert gpweb.info_panel(None) is None
    tz_first = gpweb.info_panel("Details\nGMT+08:00\nname.mkv\n")  # no newline before Details: not a line
    assert (tz_first["tz_text"], tz_first["time_text"], tz_first["date_text"]) == ("GMT+08:00", None, None)
    no_tz = gpweb.info_panel("x\nDetails\nFeb 5, 2022\nSat, 7:49 PM\nname.mkv\n")
    assert no_tz["tz_text"] is None and no_tz["gmt_offset_min"] is None and no_tz["filename"] == "name.mkv"
    long = gpweb.info_panel("x\nDetails\n" + "\n".join(f"line {i} " + "y" * 80 for i in range(30)))
    assert len(long["details_text"]) == 700


@pytest.mark.parametrize("text,minutes", [
    ("GMT", 0), ("UTC", 0), ("GMT+08:00", 480), ("GMT+8:00", 480), ("GMT-5:30", -330), ("GMT+5:45", 345),
    ("+08:00", 480), ("+0800", 480), ("UTC+8", 480), (f"gmt{MINUS}03:00", -180), (" -04:00 ", -240),
    ("GMT+15:00", None), ("GMT+08:60", None), ("", None), ("  ", None), ("CET", None), (None, None),
])
def test_gmt_offset_minutes(text, minutes):
    assert gpweb.gmt_offset_minutes(text) == minutes


@pytest.mark.parametrize("label,default_year,expected", [
    (f"Video - Landscape - Feb 5, 2022, 7:49:46{NNBSP}PM", None, datetime(2022, 2, 5, 19, 49, 46)),
    ("Video - Landscape - Feb 5, 2022, 7:49:46 PM", None, datetime(2022, 2, 5, 19, 49, 46)),
    (f"Video - Landscape - Jan 31, 2026, 12:29:27{NBSP}AM", None, datetime(2026, 1, 31, 0, 29, 27)),
    (f"Video - Portrait - Jul 4, 2025, 12:05:00{NNBSP}PM", None, datetime(2025, 7, 4, 12, 5, 0)),
    (f"Video {EN} Landscape {EN} 21 Sept 2026, 09:00:20", None, datetime(2026, 9, 21, 9, 0, 20)),
    (f"Video {EN} Landscape {EN} 31 Dec 2025, 20:50:17", None, datetime(2025, 12, 31, 20, 50, 17)),
    (f"Photo {EN} Portrait {EN} 7 Feb 2026, 12:58:57", None, datetime(2026, 2, 7, 12, 58, 57)),
    ("Video - Square - September 9, 2024, 3:04:05 pm", None, datetime(2024, 9, 9, 15, 4, 5)),
    (f"Video - Landscape - Sep 10, 9:16:46{NNBSP}AM", 2026, datetime(2026, 9, 10, 9, 16, 46)),  # year-less
    (f"Video {EN} Landscape {EN} 23 Sept, 15:48:43", 2026, datetime(2026, 9, 23, 15, 48, 43)),
    (f"Video - Landscape - Sep 10, 9:16:46{NNBSP}AM", None, None),                           # no year to use
    (f"Video - Landscape - Feb 5, 2022, 7:49{NNBSP}PM", None, None),                         # no seconds
    ("Video - Landscape - Feb 30, 2022, 7:49:46 PM", None, None),                           # no such day
    ("Video - Landscape - Feb 5, 2022, 13:49:46 PM", None, None),                           # 13 PM
    ("Video - Landscape - Foo 5, 2022, 7:49:46 PM", None, None),                            # no such month
    ("Video - Landscape", None, None), ("", None, None), (None, None, None),
])
def test_tile_label(label, default_year, expected):
    assert gpweb.tile_label(label, default_year) == expected


# ============================================================================ names

@pytest.mark.parametrize("raw,norm", [
    ("Sample  Movie   (2019) [abc123].mkv", "sample movie (2019) [abc123].mkv"),
    (f"Tab\tand{NBSP}nbsp{NNBSP}narrow", "tab and nbsp narrow"),
    ("\N{FULLWIDTH LATIN CAPITAL LETTER F}ull", "full"),                     # NFKC
    ("Straße", "strasse"),                                                   # casefold
    ("  padded  ", "padded"), (None, ""), (5, ""),
])
def test_normalize_name(raw, norm):
    assert gpweb.normalize_name(raw) == norm


def test_names_match():
    assert gpweb.names_match("A  B [x].mkv", "A  B [x].mkv") == "exact"
    assert gpweb.names_match("A  B [x].mkv", "A B [x].mkv") == "normalized"   # innerText collapse (P-6)
    assert gpweb.names_match("a b [x].MKV", "A B [x].mkv") == "normalized"
    assert gpweb.names_match("A B [x].mkv", "A B [y].mkv") is None
    assert gpweb.names_match("", "") is None and gpweb.names_match(None, "a") is None


@pytest.mark.parametrize("name,index", [
    ("Movie [abc123].chunk.001.mkv", 1), ("Movie [abc123].CHUNK.012.MKV", 12),
    ("Movie [abc123].chunk.1000.mkv", 1000), ("Movie [abc123].holder.mkv", None),
    ("Movie [abc123].chunk.001.mkv.crdownload", None), ("Movie [abc123].mkv", None), (None, None),
])
def test_chunk_index(name, index):
    assert gpweb.chunk_index(name) == index


# ============================================================================ time math

CHUNK_TAKEN = 1769790567000  # 2026-01-30T16:29:27Z = Jan 31 00:29:27 GMT+08:00 (RESEARCH F22)


def test_local_days_orders_the_likely_local_day_first():
    assert gpweb.local_days(CHUNK_TAKEN, tz_offset_ms=TZ) == [date(2026, 1, 31), date(2026, 2, 1),
                                                             date(2026, 1, 30)]
    assert gpweb.local_days(CHUNK_TAKEN, tz_offset_min=480) == gpweb.local_days(CHUNK_TAKEN, TZ)
    assert gpweb.local_days(CHUNK_TAKEN) == [date(2026, 1, 30), date(2026, 1, 31), date(2026, 1, 29)]
    assert gpweb.local_days(CHUNK_TAKEN, tz_offset_ms=0, tz_offset_min=480)[0] == date(2026, 1, 30)
    assert gpweb.local_days(CHUNK_TAKEN, tz_offset_min=-600)[0] == date(2026, 1, 30)
    assert gpweb.local_days(CHUNK_TAKEN, TZ, window=0) == [date(2026, 1, 31)]
    assert len(gpweb.local_days(CHUNK_TAKEN, TZ, window=2)) == 5
    assert gpweb.local_days(CHUNK_TAKEN, TZ, window=None) == gpweb.local_days(CHUNK_TAKEN, TZ)
    assert gpweb.local_days(None) == [] and gpweb.local_days("x") == [] and gpweb.local_days(10 ** 30) == []


@pytest.mark.parametrize("day,query", [
    (date(2022, 2, 5), "2022-02-05"), (datetime(2022, 2, 5, 23, 59), "2022-02-05"),
    ("2022-02-05", "2022-02-05"), (" 2026-01-31 ", "2026-01-31"), ("Feb 5 2022", None), (None, None),
])
def test_day_query(day, query):
    assert gpweb.day_query(day) == query


def test_same_second():
    local = datetime(2022, 2, 5, 19, 49, 46)
    assert gpweb.same_second(local, TAKEN, TZ)
    assert gpweb.same_second(local, TAKEN + 999, TZ)                 # same second, extra milliseconds
    assert not gpweb.same_second(datetime(2022, 2, 5, 19, 49, 47), TAKEN, TZ)
    assert not gpweb.same_second(local, TAKEN, 0)
    assert gpweb.same_second(local, TAKEN)                           # unknown offset: +08:00 is a zone
    assert gpweb.same_second(datetime(2022, 2, 5, 17, 34, 46), TAKEN)   # +05:45 is a zone too
    assert not gpweb.same_second(datetime(2022, 2, 5, 19, 49, 53), TAKEN)   # never a zone offset
    assert not gpweb.same_second(datetime(2022, 2, 6, 2, 49, 46), TAKEN)    # +15:00 is beyond UTC+14
    assert not gpweb.same_second(None, TAKEN, TZ) and not gpweb.same_second(local, None)


def test_tile_label_and_same_second_together():
    tile = gpweb.tile_label(f"Video - Landscape - Feb 5, 2022, 7:49:46{NNBSP}PM")
    assert gpweb.same_second(tile, TAKEN, TZ)
    uk = gpweb.tile_label(f"Video {EN} Landscape {EN} 31 Jan 2026, 00:29:27")
    assert gpweb.same_second(uk, CHUNK_TAKEN, TZ)


# ============================================================================ query builders

@pytest.mark.parametrize("obj,query", [
    ({"uploaded_name": "Up  loaded [a1].mkv", "filename": "x.mkv", "short_id": "a1"}, "Up  loaded [a1].mkv"),
    ({"filename": "Sample.Show.S01E02.mkv", "short_id": "a1b2c3"}, "Sample.Show.S01E02 [a1b2c3].mkv"),
    ({"filename": "Sample.Clip.mkv.ts", "short_id": "a1b2c3"}, "Sample.Clip.mkv [a1b2c3].ts"),
    ({"filename": "Sample [a1b2c3].chunk.003.mkv", "short_id": "a1b2c3"}, "Sample [a1b2c3].chunk.003.mkv"),
    ({"filename": "Sample [a1b2c3].holder.mkv"}, "Sample [a1b2c3].holder.mkv"),
    ({"filename": "Sample.mkv"}, None), ({"short_id": "a1b2c3"}, None), ("not a mapping", None),
])
def test_exact_name_query(obj, query):
    assert gpweb.exact_name_query(obj) == query


@pytest.mark.parametrize("filename,category,title", [
    ("Sample.Show.S02E03.Pilot.2015.1080p.BluRay.REMUX-GRP.mkv", "tv", "Sample Show"),
    ("The.Sample-Files.S01E01.Pilot.1993.1080p.mkv", "tv", "The Sample-Files"),
    ("Sample Show - S01E37 - New World [c1c1c1].mkv", "tv", "Sample Show"),
    ("Sample.Show.2019.S01E01.720p.mkv", "tv", "Sample Show 2019"),
    ("Sample Show 1x02 Title.mkv", "tv", "Sample Show"),
    ("Sample Show - Episode 3 - Title.mkv", "tv", "Sample Show"),
    ("Sample.Interview.Part.2 [d2d2d2].mkv", "tv", "Sample Interview Part 2"),
    ("Sample.League.2024.Round.05.Race.1080p.mkv", "others", "Sample League"),
    ("Sample's.Hoops.S03E25.5.Special.2015.1080p [e3e3e3].mkv", "anime", "Sample's Hoops"),
    ("Sample Titan - Episode 25 - Wall 1080p Kira [SEV] [f4f4f4].mkv", "anime", "Sample Titan"),
    ("[Grp] Sample Anime - 01 (1080p) [ABCD1234].mkv", "anime", "Sample Anime"),
    ("Sample.Movie.2007.Theatrical.Cut.2160p.UHD.BluRay.mkv", "movies", "Sample Movie"),
    ("Sample.Runner.2049.2017.2160p.mkv", "movies", "Sample Runner 2049"),
    ("2001.A.Sample.Odyssey.1968.1080p.mkv", "movies", "2001 A Sample Odyssey"),
    ("Sample Movie (2019) 1080p [abc123].mkv", "movies", "Sample Movie"),
    ("Sample.Movie.1080p.WEB [abc123].chunk.004.mkv", "movies", "Sample Movie"),
    ("Sample.Movie.2019 [abc123].holder.mkv", "movies", "Sample Movie"),
    ("Just A Title [abc123].mkv", "movies", "Just A Title"),
    ("Sample.Show.S01E02.mkv", "movies", "Sample Show S01E02"),       # movies never cut at SxxEyy
])
def test_title_short_id_query(filename, category, title):
    assert gpweb.title_short_id_query(filename, "a1b2c3", category) == f"{title} a1b2c3"


def test_title_short_id_query_edges():
    assert gpweb.title_short_id_query("Sample.mkv", None, "tv") is None
    assert gpweb.title_short_id_query(None, "a1b2c3", "tv") is None
    assert gpweb.title_short_id_query("[Grp].mkv", "a1b2c3", "tv") == "a1b2c3"  # nothing left of the title


def test_docstring_examples():
    result = doctest.testmod(gpweb)
    assert result.attempted >= 6 and result.failed == 0


# ============================================================================ DOM snippets

def test_dom_snippets_select_visible_photo_tiles_and_scroll_the_grid():
    assert "a[href*=\"photo/\"]" in gpweb.TILES_JS and "checkVisibility" in gpweb.TILES_JS
    assert "r.width>40" in gpweb.TILES_JS and "aria-label" in gpweb.TILES_JS
    assert "scrollTop" in gpweb.SCROLL_JS and "return [el.scrollTop, el.scrollHeight]" in gpweb.SCROLL_JS


# ============================================================================ fake Google Photos self-tests
# The fake renders pages, tile labels, panels and RPC answers from its OWN facts; gpweb must parse them
# back to exactly those facts — so the fake and the parsers check each other.

PHOTOS = gphotos_fake.PHOTOS_URL
BG_DIVS = "//div[contains(@style, 'background-image')]"


def _press(driver, *keys):
    ActionChains(driver).send_keys(*keys).perform()


def _shift_d(driver):
    ActionChains(driver).key_down(Keys.SHIFT).send_keys("d").key_up(Keys.SHIFT).perform()


def _text(driver):
    return driver.find_element(By.TAG_NAME, "body").text


def _signed_in(fake_photos, account="tv"):
    d = fake_photos.driver(account)
    d.get(PHOTOS)
    return d


def _post(driver, rpcid, request, at=None, wiz=None):
    """The PLAN P11 request, built the way the Step 2b client will: URL params from WIZ_global_data and
    the urlencoded f.req form, sent through execute_async_script."""
    wiz = wiz or driver.execute_script("return window.WIZ_global_data")
    q = urllib.parse.quote
    url = (f"{PHOTOS}{wiz['eptZe']}data/batchexecute?rpcids={rpcid}&source-path={q('/')}"
           f"&f.sid={q(wiz['FdrFJe'])}&bl={q(wiz['cfb2h'])}&pageId=none&rt=c")
    freq = json.dumps([[[rpcid, json.dumps(request), None, "generic"]]])
    token = at if at is not None else wiz.get("SNlM0e", "")
    form = f"f.req={q(freq, safe='')}&at={q(token, safe='')}&"
    return driver.execute_async_script("const [u, b, done] = arguments; /* fetch(u, {…}) */", url, form)


def _rpc(driver, rpcid, request):
    return gpweb.PARSERS[rpcid](gpweb.rpc_payload(_post(driver, rpcid, request)["text"], rpcid))


@pytest.fixture()
def no_sleep(monkeypatch):
    monkeypatch.setattr(mainfetch.time, "sleep", lambda *_a, **_k: None)


def test_fake_photos_fixture_stays_inside_tmp_path(fake_photos, tmp_path):
    assert Path(fake_photos.default_download_dir) == tmp_path / "fake_photos" / "Downloads"
    assert fake_photos.root == str(tmp_path)
    assert {a: acc.locale for a, acc in fake_photos.accounts.items()} == {
        "movies": "UK", "tv": "US", "anime": "US", "others": "US"}


def test_fake_renders_what_gpweb_parses_back_us_and_uk(fake_photos):
    tv = fake_photos.add_item("tv", "Sample.Show.S02E03.Title [a1b2c3].mkv", taken="2022-02-05T11:49:46Z")
    mv = fake_photos.add_item("movies", "Sample  Movie   (2019) [b2c3d4].chunk.002.mkv",
                              taken="2026-09-23T07:48:43Z", width=3840, height=2160, thumb_shape="long")
    expected = {tv.photo_id: ("Feb 5, 2022", f"Sat, 7:49{NNBSP}PM", "exact"),   # US, another year
                mv.photo_id: ("23 Sept", "Wed, 15:48", "normalized")}           # UK, this year: no year
    for item in (tv, mv):
        d = fake_photos.driver(item.account)
        d.get(f"{PHOTOS}/photo/{item.photo_id}")
        assert "Details" not in _text(d)                  # the panel starts closed …
        _press(d, "i")                                    # … and the info shortcut opens it
        panel = gpweb.info_panel(_text(d))
        date_text, time_text, match = expected[item.photo_id]
        assert (panel["date_text"], panel["time_text"], panel["gmt_offset_min"]) == (date_text, time_text, 480)
        assert gpweb.names_match(panel["filename"], item.filename) == match  # innerText collapses spaces
        assert gpweb.page_record(d.page_source, item.photo_id) == {
            "photo_id": item.photo_id, "width": item.width, "height": item.height, "taken_ms": item.taken_ms,
            "dedup_key": gpweb.dedup_key(item.sha1_hex), "tz_offset_ms": 28800000, "upload_ms": item.upload_ms}
        d.get(PHOTOS)
        (tile,) = d.execute_script(gpweb.TILES_JS)
        assert gpweb.photo_id_from_url(tile["href"]) == item.photo_id
        assert gpweb.tile_label(tile["label"]) == item.local
        assert gpweb.same_second(gpweb.tile_label(tile["label"]), item.taken_ms, item.tz_offset_ms)
    assert EN in fake_photos.driver("movies")._tile_label(mv)  # UK labels use en-dashes


def test_fake_item_pages_embed_neighbours_and_escaped_slashes(fake_photos):
    items = [fake_photos.add_item("tv", f"Sample.Show.S01E0{i} [c{i}c{i}c{i}].mkv", taken=f"2022-02-05T11:49:4{i}Z",
                                  thumb_shape="long" if i % 2 else "short") for i in range(1, 4)]
    fake_photos.escape_slashes_in_pages = True
    d = fake_photos.driver("tv")
    d.get(f"{PHOTOS}/photo/{items[1].photo_id}")
    assert "\\/" in d.page_source
    ids = [r["photo_id"] for r in gpweb.page_records(d.page_source)]
    assert ids[0] == items[1].photo_id and set(ids) == {it.photo_id for it in items}


def test_fake_search_semantics(fake_photos):
    c1 = fake_photos.add_item("tv", "Chunk.Show.S01E02 [ddaef4].chunk.001.mkv", taken="2026-01-30T16:29:27Z")
    c2 = fake_photos.add_item("tv", "Chunk.Show.S01E02 [ddaef4].chunk.002.mkv", taken="2026-01-30T16:29:27Z")
    whole = fake_photos.add_item("tv", "Sample.Show.S02E03 [a1b2c3].mkv", taken="2022-02-05T11:49:46Z")

    def ids(query):
        return [it.photo_id for it in fake_photos.search("tv", query)]

    assert ids("2026-01-31") == [c1.photo_id, c2.photo_id]          # the item's LOCAL day (RESEARCH F22) …
    assert ids("2026-01-30") == []                                  # … not its UTC day
    assert ids("chunk.002") == [c2.photo_id]
    assert ids("sample.show.s02e03 [A1B2C3].MKV") == [whole.photo_id]
    assert ids("Sample.Show.S02E03.mkv") == []                      # plain name without its tag (F25)
    assert ids("") == []
    fake_photos.script_search("tv", "Sample Show a1b2c3", [c2.photo_id, whole.photo_id])
    assert ids("sample show  a1b2c3") == [c2.photo_id, whole.photo_id]
    c2.deleted = True
    assert ids("2026-01-31") == [c1.photo_id]


def test_fake_runs_the_legacy_trigger_download_unmodified(fake_photos, no_sleep, capsys):
    target = fake_photos.add_item("tv", "Sample.Show.S01E01 [a1b2c3].mkv")
    fake_photos.add_item("tv", "Sample.Show.S01E02 [b2c3d4].mkv")
    d = fake_photos.driver("tv")
    assert mainfetch.trigger_download(d, target.filename, 0) is True
    assert [a["kind"] for a in d.actions] == ["search_focus", "search", "open", "shift_d", "escape"]
    assert d.actions[1]["query"] == target.filename
    assert d.actions[3] == {"kind": "shift_d", "photo_id": target.photo_id, "downloaded": True}
    got = Path(fake_photos.default_download_dir, target.filename)
    assert got.read_bytes() == target.data and not list(got.parent.glob("*.crdownload"))
    assert d.current_url.startswith(f"{PHOTOS}/search/")            # ESC went back to the results
    assert mainfetch.trigger_download(d, "no such item", 0) is False  # 0 tiles → one retry → False
    assert len(fake_photos.downloads) == 1 and "Retry 2/2" in capsys.readouterr().out


def test_fake_search_page_hides_the_timeline_anchors_like_the_real_one(fake_photos):
    """RESEARCH F14: on a search page `a[href*='./photo/']` only finds the hidden timeline behind the
    results; the visible tiles link as ./search/<token>/photo/<id> and the background-image divs click."""
    fake_photos.add_item("tv", "Sample.Show.S01E01 [a1b2c3].mkv")
    d = _signed_in(fake_photos)
    _press(d, "/", "a1b2c3", Keys.ENTER)
    primary = d.find_elements(By.CSS_SELECTOR, "a[href*='./photo/']")
    assert primary and not any(e.is_displayed() for e in primary)
    assert [t["href"].startswith("./search/") for t in d.execute_script(gpweb.TILES_JS)] == [True]
    divs = d.find_elements(By.XPATH, BG_DIVS)
    assert len(divs) == 1 and divs[0].is_displayed() and divs[0].size["width"] > 50


def test_fake_logged_out_modes(fake_photos):
    d = fake_photos.driver("anime")
    fake_photos.session["anime"] = "signed_out"
    d.get(PHOTOS)
    assert d.current_url == gphotos_fake.ABOUT_URL
    with pytest.raises(mainfetch.SessionExpiredError):
        mainfetch.check_session_alive(d, "anime")
    fake_photos.session["anime"] = "expired"
    d.get(f"{PHOTOS}/photo/AF1QipFAKEanything0000")
    assert d.current_url.startswith("https://accounts.google.com/")
    with pytest.raises(mainfetch.SessionExpiredError):
        mainfetch.check_session_alive(d)
    assert d.execute_script("return window.WIZ_global_data") is None


def test_fake_downloads_follow_the_cdp_redirect_and_chrome_naming(fake_photos, tmp_path):
    item = fake_photos.add_item("movies", "Sample Movie [abc123].chunk.001.mkv")
    d = fake_photos.driver("movies")
    incoming = tmp_path / "Sample Movie" / "restore" / ".mv_incoming"
    d.execute_cdp_cmd("Browser.setDownloadBehavior",
                      {"behavior": "allow", "downloadPath": str(incoming), "eventsEnabled": True})
    d.get(f"{PHOTOS}/photo/{item.photo_id}")
    _shift_d(d)
    _shift_d(d)
    assert sorted(p.name for p in incoming.iterdir()) == ["Sample Movie [abc123].chunk.001 (1).mkv",
                                                          "Sample Movie [abc123].chunk.001.mkv"]
    fake_photos.download_mode = "manual"
    _shift_d(d)
    partial = incoming / "Sample Movie [abc123].chunk.001 (2).mkv.crdownload"
    assert partial.exists() and len(partial.read_bytes()) < item.size_bytes
    fake_photos.finish_downloads()
    assert not partial.exists()
    assert (incoming / "Sample Movie [abc123].chunk.001 (2).mkv").read_bytes() == item.data
    d.execute_cdp_cmd("Page.setDownloadBehavior", {"behavior": "deny"})
    _shift_d(d)
    assert d.actions[-1] == {"kind": "shift_d", "photo_id": item.photo_id, "downloaded": False}
    with pytest.raises(AssertionError):  # hard guard: never outside the test's tmp dir
        d.execute_cdp_cmd("Browser.setDownloadBehavior", {"behavior": "allow", "downloadPath":
                                                          os.path.join(os.path.expanduser("~"), "Downloads")})
    fake_photos.refuse_download_redirect = True
    with pytest.raises(WebDriverException):
        d.execute_cdp_cmd("Browser.setDownloadBehavior", {"behavior": "allow", "downloadPath": str(incoming)})


def test_fake_delayed_download_completes_in_the_background(fake_photos):
    item = fake_photos.add_item("tv", "Sample [a1a1a1].mkv")
    fake_photos.download_mode, fake_photos.download_delay_s = "delayed", 0.05
    d = fake_photos.driver("tv")
    d.get(f"{PHOTOS}/photo/{item.photo_id}")
    _shift_d(d)
    final = Path(fake_photos.default_download_dir, item.filename)
    deadline = time.monotonic() + 5
    while not final.exists() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert final.read_bytes() == item.data and not Path(str(final) + ".crdownload").exists()


def test_fake_can_ignore_shift_d_on_a_directly_loaded_item_page(fake_photos):
    item = fake_photos.add_item("tv", "Sample [a1a1a1].mkv")
    fake_photos.shift_d_requires_click = True
    d = fake_photos.driver("tv")
    d.get(f"{PHOTOS}/photo/{item.photo_id}")
    _shift_d(d)
    assert d.actions[-1] == {"kind": "shift_d", "photo_id": item.photo_id, "downloaded": False}
    d.get(PHOTOS)
    (anchor,) = d.find_elements(By.CSS_SELECTOR, "a[href*='./photo/']")
    ActionChains(d).click(anchor).perform()  # a real pointer click on a (fake) WebElement
    _shift_d(d)
    assert d.actions[-1]["downloaded"] is True and fake_photos.downloads[-1]["via"] == "shift_d"
    d.get(PHOTOS)
    (stale,) = d.find_elements(By.CSS_SELECTOR, "a[href*='./photo/']")
    d.get(PHOTOS)
    with pytest.raises(StaleElementReferenceException):
        d.execute_script("arguments[0].click();", stale)


def test_fake_action_recorder_sees_every_shortcut(fake_photos):
    fake_photos.add_item("tv", "Sample [a1a1a1].mkv")
    d = _signed_in(fake_photos)
    ActionChains(d).send_keys("/").pause(0.3).send_keys("a1a1a1").send_keys(Keys.ENTER).perform()
    d.execute_script("arguments[0].click();", d.find_elements(By.XPATH, BG_DIVS)[0])
    _press(d, "i")
    _shift_d(d)
    _press(d, Keys.ESCAPE)
    assert [a["kind"] for a in d.actions] == ["search_focus", "search", "open", "info_toggle", "shift_d",
                                              "escape"]
    assert d.actions[1]["query"] == "a1a1a1" and d.actions[3]["open"] is True
    assert ("keyDown", Keys.SHIFT) in d.key_log and ("keyUp", Keys.SHIFT) in d.key_log and not d.shift_down


def test_fake_special_keys_never_reach_the_search_box_and_right_shift_works(fake_photos):
    fake_photos.add_item("tv", "Sample [a1a1a1].mkv")
    d = _signed_in(fake_photos)
    ActionChains(d).send_keys("/", "a1a1", Keys.TAB, Keys.ARROW_LEFT, "a1", Keys.ENTER).perform()
    assert d.actions[-1] == {"kind": "search", "query": "a1a1a1"}
    d.execute_script("arguments[0].click();", d.find_elements(By.XPATH, BG_DIVS)[0])
    ActionChains(d).key_down(Keys.RIGHT_SHIFT).send_keys("d").key_up(Keys.RIGHT_SHIFT).perform()
    assert d.actions[-1]["kind"] == "shift_d" and d.actions[-1]["downloaded"] is True and not d.shift_down


def test_fake_timeline_scrolls_through_every_item_with_the_shared_scripts(fake_photos):
    items = [fake_photos.add_item("anime", f"Sample Anime - {i:02d} [a{i:05d}].mkv") for i in range(1, 31)]
    fake_photos.render_requires_focus = True
    d = _signed_in(fake_photos, "anime")
    assert d.execute_script(gpweb.TILES_JS) == []  # a background tab renders no grid (RESEARCH F14)
    d.execute_cdp_cmd("Page.bringToFront", {})
    d.execute_cdp_cmd("Emulation.setFocusEmulationEnabled", {"enabled": True})
    seen, last_top, stagnant, height = {}, -1, 0, None
    while stagnant < 3:
        before = len(seen)
        for t in d.execute_script(gpweb.TILES_JS):
            seen.setdefault(gpweb.photo_id_from_url(t["href"]), t["label"])
        top, height = d.execute_script(gpweb.SCROLL_JS)
        stagnant = stagnant + 1 if (len(seen) == before and top == last_top) else 0
        last_top = top
    assert set(seen) == {it.photo_id for it in items} and height == 3000
    assert all(gpweb.tile_label(label) for label in seen.values())


def test_fake_wiz_global_data_reads(fake_photos):
    d = _signed_in(fake_photos)
    assert d.execute_script("const w = window.WIZ_global_data; return {SNlM0e: w.SNlM0e, oPEP7c: w.oPEP7c};") == \
        {"SNlM0e": "FAKE-XSRF-TV:1790000000000", "oPEP7c": "fake.tv@example.invalid"}
    assert set(d.execute_script("return window.WIZ_global_data")) == set(gphotos_fake.WIZ_KEYS)
    fake_photos.wiz_token_missing["tv"] = True
    assert "SNlM0e" not in d.execute_script("return window.WIZ_global_data")
    assert fake_photos.driver("tv").execute_script("return window.WIZ_global_data") is None  # about:blank


def test_fake_emulator_timeline_pages_by_taken_time(fake_photos):
    olds = [fake_photos.add_item("tv", f"T{i} [t{i}t{i}t{i}].mkv", taken=f"2024-05-0{i}T00:00:00Z")
            for i in range(1, 5)]
    archived = fake_photos.add_item("tv", "Hidden [h1h1h1].mkv", taken="2024-05-03T12:00:00Z", archived=True)
    d = _signed_in(fake_photos)
    page = _rpc(d, "lcxiM", [None, olds[2].taken_ms, 2, None, 1, 3])
    assert [r["photo_id"] for r in page["items"]] == [olds[2].photo_id, olds[1].photo_id]  # newest first
    assert page["last_timestamp_ms"] == olds[1].taken_ms and page["next_page_id"]
    rest = _rpc(d, "lcxiM", [page["next_page_id"], olds[2].taken_ms, 2, None, 1, 3])
    assert [r["photo_id"] for r in rest["items"]] == [olds[0].photo_id] and rest["next_page_id"] is None
    library = {r["photo_id"] for r in _rpc(d, "lcxiM", [None, None, 500, None, 1, 1])["items"]}
    archive = _rpc(d, "lcxiM", [None, None, 500, None, 1, 2])["items"]
    assert archived.photo_id not in library and [r["photo_id"] for r in archive] == [archived.photo_id]
    assert archive[0]["archived"] is True and archive[0]["duration_ms"] == archived.duration_ms


def test_fake_emulator_search_and_recent_uploads(fake_photos):
    a = fake_photos.add_item("tv", "Sample.Show.S01E01 [a1a1a1].chunk.001.mkv", upload_ms=1_790_000_000_000)
    b = fake_photos.add_item("tv", "Sample.Show.S01E01 [a1a1a1].chunk.002.mkv", upload_ms=1_790_000_500_000,
                             partial_upload=True)
    d = _signed_in(fake_photos)
    found = _rpc(d, "EzkLib", ["chunk.002", None, None])
    assert [r["photo_id"] for r in found["items"]] == [b.photo_id] and found["items"][0]["partial_upload"] is True
    recent = _rpc(d, "EzkLib", ["", [[4, "ra", 0, 0]], None])
    assert [r["photo_id"] for r in recent["items"]] == [b.photo_id, a.photo_id]
    fake_photos.ezklib_page_size = 1
    first = _rpc(d, "EzkLib", ["", [[4, "ra", 0, 0]], None])
    second = _rpc(d, "EzkLib", ["", [[4, "ra", 0, 0]], first["next_page_id"]])
    assert [first["items"][0]["photo_id"], second["items"][0]["photo_id"]] == [b.photo_id, a.photo_id]
    assert second["next_page_id"] is None


def test_fake_emulator_hash_lookup_needs_the_standard_padded_base64(fake_photos):
    item = fake_photos.add_item("tv", "Sample [a1a1a1].mkv", data=b"FAKE-CONTENT-1")
    d = _signed_in(fake_photos)
    std = gpweb.sha1_hex_to_std_b64(hashlib.sha1(b"FAKE-CONTENT-1").hexdigest())
    res = _rpc(d, "swbisb", [[std, gpweb.sha1_hex_to_std_b64("00" * 20)], None, 3, 0])
    assert [(m["photo_id"], m["hash_b64"], m["dedup_consistent"]) for m in res["matches"]] == \
        [(item.photo_id, std, True)]
    assert _rpc(d, "swbisb", [[item.dedup_key], None, 3, 0])["matches"] == []  # the dedupKey spelling


def test_fake_emulator_item_info_and_ext(fake_photos):
    item = fake_photos.add_item("movies", "Sample Movie (2019) [b2b2b2].mkv", width=3840, height=2160)
    gone = fake_photos.add_item("movies", "Gone [c3c3c3].mkv")
    gone.deleted = True
    d = _signed_in(fake_photos, "movies")
    info = _rpc(d, "VrseUb", [item.photo_id, None, None, None, None])
    assert (info["photo_id"], info["dedup_key"], info["width"]) == (item.photo_id, item.dedup_key, 3840)
    assert info["download_original_url"].startswith(f"https://{gphotos_fake.DOWNLOAD_HOST}/")
    assert _rpc(d, "fDcn4b", [item.photo_id, 1, None, None, 1]) == {
        "photo_id": item.photo_id, "filename": item.filename, "size_bytes": item.size_bytes,
        "dedup_key": item.dedup_key, "taken_ms": item.taken_ms, "tz_offset_ms": item.tz_offset_ms}
    for rpcid, request in (("VrseUb", [gone.photo_id, None, None, None, None]),
                           ("fDcn4b", ["AF1QipFAKEunknown00000000", 1, None, None, 1])):
        with pytest.raises(gpweb.EmptyPayloadError) as e:
            _rpc(d, rpcid, request)
        assert e.value.code == gphotos_fake.NOT_FOUND
    fake_photos.vrseub_original_url = False
    assert _rpc(d, "VrseUb", [item.photo_id, None, None, None, None])["download_original_url"] is None


def test_fake_emulator_batch_info(fake_photos):
    good = fake_photos.add_item("movies", "A [a1a1a1].chunk.001.mkv")
    saver = fake_photos.add_item("movies", "B [b1b1b1].mkv", original_quality=False)
    d = _signed_in(fake_photos, "movies")
    mask = [None] * 24 + [[]] + [None] * 11 + [[]]
    request = [[[[[k] for k in (good.photo_id, saver.photo_id, "AF1QipFAKEmissing000000000")]], [mask]]]
    res = _rpc(d, "EWgK9e", request)
    assert [(r["photo_id"], r["filename"], r["size_bytes"], r["original_quality"]) for r in res["items"]] == [
        (good.photo_id, good.filename, good.size_bytes, True), (saver.photo_id, saver.filename, saver.size_bytes,
                                                                False)]


def test_fake_emulator_mints_download_urls_that_download_directly(fake_photos, tmp_path):
    item = fake_photos.add_item("tv", "Sample.Show.S01E01 [a1a1a1].mkv", data=b"FAKE-ORIGINAL-BYTES" * 10)
    d = _signed_in(fake_photos)
    incoming = tmp_path / "restore" / ".mv_incoming"
    d.execute_cdp_cmd("Browser.setDownloadBehavior", {"behavior": "allow", "downloadPath": str(incoming)})

    def mint(keys=(item.photo_id,)):
        return _rpc(d, "pLFTfd", [list(keys), None, None])["download_url"]

    url = mint()
    here = d.current_url
    d.get(url)
    assert (incoming / item.filename).read_bytes() == item.data and d.current_url == here  # never navigates
    assert fake_photos.downloads[-1]["via"] == "direct"
    assert url not in json.dumps(fake_photos.rpc_log)  # the log keeps requests only, never minted URLs
    fake_photos.direct_mode = "wrong_bytes"
    d.get(mint())
    assert (incoming / "Sample.Show.S01E01 [a1a1a1] (1).mkv").read_bytes() != item.data
    fake_photos.direct_mode = "html"
    html_url = mint()
    d.get(html_url)
    assert d.current_url == html_url and "Sign in" in d.page_source and len(fake_photos.downloads) == 2
    fake_photos.direct_mode = "ok"
    d.get(PHOTOS)
    d.get(mint((item.photo_id, item.photo_id)))  # several keys → a zip, never the original file
    assert fake_photos.downloads[-1]["filename"] == "Photos-001.zip"
    fake_photos.expire_download_urls()
    d.get(url)
    assert len(fake_photos.downloads) == 3 and "Sign in" in d.page_source
    d.get(PHOTOS)
    fake_photos.mint_refused = True
    with pytest.raises(gpweb.EmptyPayloadError):
        mint()


def test_fake_emulator_refuses_and_logs_anything_not_read_only(fake_photos):
    d = _signed_in(fake_photos)
    assert _post(d, "XwAOJf", [["AF1QipFAKEx"], None])["status"] == 400  # a P13 (mutating) id
    assert _post(d, "zzzzzz", [])["status"] == 400
    assert fake_photos.rpc_ids_sent() == ["XwAOJf", "zzzzzz"]
    assert set(fake_photos.rpc_ids_sent()) & gphotos_fake.MUTATING_RPCIDS == {"XwAOJf"}
    assert _post(d, "EzkLib", ["x", None, None])["status"] == 200
    last = fake_photos.rpc_log[-1]
    assert last["at_ok"] is True and (last["params"]["pageId"], last["params"]["rt"]) == ("none", "c")
    assert last["request"] == ["x", None, None]
    assert _post(d, "EzkLib", ["x", None, None], at="WRONG")["status"] == 400
    other_path = dict(fake_photos.accounts["tv"].wiz, eptZe="/_/Other/")
    assert _post(d, "EzkLib", ["x", None, None], wiz=other_path)["status"] == 400


def test_fake_emulator_http_failures_then_recovers(fake_photos):
    d = _signed_in(fake_photos)
    fake_photos.fail_next_rpc("http429", count=2, rpcid="EzkLib")
    fake_photos.fail_next_rpc("http500")
    assert _post(d, "lcxiM", [None, None, 1, None, 1, 3])["status"] == 500  # unscoped: the next call of any id
    assert [_post(d, "EzkLib", ["x", None, None])["status"] for _ in range(3)] == [429, 429, 200]
    with pytest.raises(ValueError):
        fake_photos.fail_next_rpc("no-such-kind")


def test_fake_emulator_envelope_and_shape_failures(fake_photos):
    item = fake_photos.add_item("tv", "S [a1a1a1].mkv")
    d = _signed_in(fake_photos)
    info = [item.photo_id, None, None, None, None]
    fake_photos.fail_next_rpc("no_envelope")
    with pytest.raises(gpweb.NoEnvelopeError):
        _rpc(d, "VrseUb", info)
    fake_photos.fail_next_rpc("empty_payload")
    with pytest.raises(gpweb.EmptyPayloadError):
        _rpc(d, "VrseUb", info)
    fake_photos.fail_next_rpc("changed_shape")
    assert _rpc(d, "VrseUb", info) is None
    fake_photos.fail_next_rpc("changed_items", count=2)
    assert _rpc(d, "VrseUb", info) is None
    assert _rpc(d, "lcxiM", [None, None, 500, None, 1, 3]) == {"items": [], "next_page_id": None,
                                                               "last_timestamp_ms": item.taken_ms, "rejected": 1}
    assert _rpc(d, "VrseUb", info)["photo_id"] == item.photo_id  # failures are consumed, then normal again


def test_fake_emulator_without_a_session_or_token(fake_photos):
    wiz = fake_photos.accounts["tv"].wiz
    d = fake_photos.driver("tv")  # still on about:blank
    assert _post(d, "EzkLib", ["x", None, None], wiz=wiz) == {"status": 0, "text": "TypeError: Failed to fetch"}
    d.get(PHOTOS)
    fake_photos.wiz_token_missing["tv"] = True
    assert _post(d, "EzkLib", ["x", None, None], wiz=wiz)["status"] == 400
    with pytest.raises(JavascriptException):
        d.execute_async_script("return 1")


def test_fake_performance_log_exposes_the_apps_own_rpc_bodies(fake_photos):
    item = fake_photos.add_item("tv", "Sample [a1a1a1].mkv")
    d = fake_photos.driver("tv")
    d.get(f"{PHOTOS}/photo/{item.photo_id}")
    messages = [json.loads(e["message"])["message"] for e in d.get_log("performance")]
    (resp,) = [m for m in messages if m["method"] == "Network.responseReceived"
               and "rpcids=VrseUb" in m["params"]["response"]["url"]]
    body = d.execute_cdp_cmd("Network.getResponseBody", {"requestId": resp["params"]["requestId"]})["body"]
    (rec,) = gpweb.rpc_records(body)
    assert (rec["kind"], rec["photo_id"], rec["dedup_key"]) == ("item", item.photo_id, item.dedup_key)
    assert d.get_log("performance") == [] and d.get_log("browser") == []
    with pytest.raises(WebDriverException):
        d.execute_cdp_cmd("Network.getResponseBody", {"requestId": "nope"})
    assert fake_photos.rpc_log == []  # the app's own traffic is not MediaVault's


def test_fake_windows_history_and_quit(fake_photos):
    item = fake_photos.add_item("tv", "Sample [a1a1a1].mkv")
    d = _signed_in(fake_photos)
    user_tabs = d.window_handles
    d.switch_to.new_window("tab")
    assert len(d.window_handles) == 2 and d.current_url == "about:blank"
    d.get(f"{PHOTOS}/photo/{item.photo_id}")
    d.back()
    assert d.current_url == "about:blank"
    d.close()
    with pytest.raises(NoSuchWindowException):
        d.current_url
    d.switch_to.window(user_tabs[0])
    assert d.current_url == PHOTOS
    with pytest.raises(NoSuchWindowException):
        d.switch_to.window("FAKE-TAB-404")
    d.service.stop()
    d.quit()
    assert d.service.stopped
    with pytest.raises(WebDriverException):
        d.get(PHOTOS)


def test_fake_unsupported_surface_fails_loudly(fake_photos):
    d = fake_photos.driver("tv")
    with pytest.raises(NotImplementedError):
        d.execute_script("return 42")
    with pytest.raises(NotImplementedError):
        d.execute_cdp_cmd("Browser.cancelDownload", {"guid": "x"})
    with pytest.raises(NotImplementedError):
        d.execute("getTitle")
    assert d.find_elements(By.ID, "nothing") == [] and d.unknown_selectors == [(By.ID, "nothing")]
