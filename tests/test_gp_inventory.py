"""tools/gp_inventory.py parsing (IMP-C25). Synthetic fixtures only — no real ids/URLs."""
import importlib.util
import os

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
