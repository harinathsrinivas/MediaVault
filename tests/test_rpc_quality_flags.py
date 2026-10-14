"""IMP-C25 Step 14, follow-up d — the RPC quality flags as real item records carry them (docs/feature-fetch-datetime/
PLAN.md Step 14 "Follow-ups carried into Step 14" d; PLAN P12; Step 13's quality pass, its `inconsistent` verdict and
its partial-upload / not-original-quality outliers).

ONE read-only live probe (2026-10-14: the TV account, one known original-quality video, downloads denied, Chrome closed
afterwards; only the STRUCTURE was printed — no URL, key or name) showed, recreated below with SYNTHETIC values:
  * lcxiM / EzkLib / VrseUb carry the FULL dense item record — 16 entries, [12] null (P12: not a partial upload),
    [13] false (not archived), the extension object at [15];
  * swbisb carries a COMPACT, field-masked record — dense only up to [8] ([6] / [7] null although the full record sets
    [7]), every later field in a trailing object keyed by FIELD NUMBER (its "15" holds what the full record holds at
    [14]). It does not carry partial upload / archived: the hash lookup cannot tell them (they parsed None for every
    item, so Step 13's quality pass never knew them);
  * EWgK9e's info array has 26 entries and NO [takesUpSpace, spaceTaken, quality] triple: original_quality cannot be
    read. Several fields of several answers hold 2 for this original-quality item, but with no storage-saver item to
    compare, which one is the quality code cannot be told — the parsers keep it unknown (never a guess).
Pinned here: gpweb._jspb_field (dense · sparse by field number · absent = unknown); the flags each live layout yields;
check_photos' quality pass reading partial upload / archived from the item's timeline record when the hash record lacks
them — one lcxiM call per distinct taken second — so a partial upload is `inconsistent` with the live layouts; a failing
timeline call or the live batch-info layout leaves a fact unknown, never a reason to doubt an item.

Hermetic: the fake Google Photos world (tests/gphotos_fake.py; its swbisb_compact / ewgk9e_live knobs answer in the
live layouts) and test_check_photos' check harness. Synthetic ids only (every photo id contains "FAKE").
"""
import hashlib

import pytest

import gpcapture
import gpweb
import main
from test_check_photos import (SEASON, _calls, _kinds, _only_read_only_rpcids_ever, _results, _season_of,  # noqa: F401
                               clock, cw)

PID = "AF1QipFAKEqualityAAAA0123456789abcdefFAKE"
PID_B = "AF1QipFAKEqualityBBBB0123456789abcdefFAKE"
TAKEN = 1644061786000                   # 2022-02-05T11:49:46Z
TZ = 28800000                           # GMT+08:00
UPLOAD = 1790104139274
DURATION = 1320000
CODES = [[1], [2], [3], [8], [21], [14], [15], [19], [22], [28, False, True], [29, False, True], [35, False, True]]


def _key(tag):
    """A synthetic dedupKey: the real key of synthetic bytes."""
    return gpcapture.dedup_key(hashlib.sha1(tag.encode()).hexdigest())


KEY, KEY_B = _key("FAKE-quality-A"), _key("FAKE-quality-B")


def _thumb(pid):
    return [f"https://example.invalid/thumb/{pid}", 1920, 1080, None, None, None, None, None, [None, None, 1], [0]]


def _full(pid=PID, key=KEY, partial=None, archived=False):
    """The full dense item record (lcxiM / EzkLib items, VrseUb payload[0]) in the live layout."""
    return [pid, _thumb(pid), TAKEN, key, TZ, UPLOAD, None, CODES, 2, None, None, None, partial, archived, 4242,
            {"76647426": [DURATION, None, 1920, 1080, None, 4, 134, None, [[0, 0, 0, 0]], False, None, None, None,
                          ["https://example.invalid/stream"]],
             "129168200": [[], [None, None, None, False]], "163238866": [False]}]


def _compact(pid=PID, key=KEY, **fields):
    """The swbisb record in the live layout: dense only up to [8], later fields by field number (`fields`: more of
    them, e.g. {"13": [20]})."""
    return [pid, _thumb(pid), TAKEN, key, TZ, UPLOAD, None, None, 2,
            dict({"15": 4242, "76647426": [DURATION, None, None, None, None, 4, 134, None, None, False]}, **fields)]


def _info_row(pid=PID, name="Sample.Show.S02E03 [a1b2c3].mkv", size=734003200):
    """An EWgK9e row in the live layout: 6 entries; a 26-entry info array without a quality triple."""
    info = ([None, None, "", name, None, None, TAKEN, TZ, UPLOAD, size, 100] + [None] * 7 + [2] + [None] * 4
            + [1, None, 4242])
    return [pid, info, None, None, None, [None, None]]


# ===========================================================================
# the parsers
# ===========================================================================
@pytest.mark.parametrize("arr, index, expected", [
    (["a", "b", None], 1, ("dense", "b")),
    (["a", "b", None], 2, ("dense", None)),        # a null in the dense part is a value: the field is unset
    (["a", {"3": 7}], 2, ("sparse", 7)),           # index 2 = field 3, in the trailing object
    (["a", {"3": 7}], 1, ("absent", None)),        # the trailing object is not a dense slot
    (["a", {}], 0, ("dense", "a")),
    (["a", "b"], 5, ("absent", None)),             # beyond a record without a trailing object
    ([], 0, ("absent", None)),
    (None, 0, ("absent", None)),
    ({"1": "x"}, 0, ("absent", None)),
])
def test_a_jspb_field_is_read_dense_then_by_field_number(arr, index, expected):
    assert gpweb._jspb_field(arr, index) == expected


def test_the_compact_record_keys_its_tail_by_field_number():
    # The live evidence the reading rests on: the compact record's "15" is the value the full record holds at [14].
    assert gpweb._jspb_field(_compact(), 14) == ("sparse", 4242)
    assert gpweb._jspb_field(_full(), 14) == ("dense", 4242)
    assert gpweb._jspb_field(_compact(), 7) == ("dense", None)      # field-masked: [7] is set in the full record …
    assert _full()[7] == CODES                                      # … so an absent field is unknown, never "unset"


def test_the_live_full_record_reads_complete_and_in_the_library():
    expected = {"photo_id": PID, "width": 1920, "height": 1080, "taken_ms": TAKEN, "dedup_key": KEY,
                "tz_offset_ms": TZ, "upload_ms": UPLOAD, "partial_upload": False, "archived": False,
                "duration_ms": DURATION}
    page = gpweb.parse_lcxiM([[_full()], "FAKEnext", str(TAKEN)])
    assert page["items"] == [expected] and page["rejected"] == 0
    assert gpweb.parse_EzkLib([[_full()], None])["items"] == [expected]
    # VrseUb answered 5 entries live: [0] the full record, [1] a long URL, [2] codes, [3] a list, [4] a trailing object
    vrseub = gpweb.parse_VrseUb([_full(), "https://example.invalid/long", CODES, [None] * 18,
                                 {"11": "", "13": "https://example.invalid/x"}])
    assert {k: vrseub[k] for k in expected} == expected
    assert vrseub["download_original_url"] is None                  # P14's payload[7] was absent in that answer


def test_a_full_record_with_the_p12_markers_reads_partial_and_archived():
    # The probe could show only the complete case; the markers stay exactly P12's ([12][0] == 20, [13] true).
    [rec] = gpweb.parse_lcxiM([[_full(partial=[20], archived=True)]])["items"]
    assert (rec["partial_upload"], rec["archived"]) == (True, True)


def test_the_live_compact_hash_record_leaves_partial_and_archived_unknown():
    std = gpweb.sha1_hex_to_std_b64(gpweb.dedup_key_to_sha1_hex(KEY))
    res = gpweb.parse_swbisb([[[std, _compact()]]])
    assert res["rejected"] == 0
    [rec] = res["matches"]
    assert (rec["partial_upload"], rec["archived"]) == (None, None)   # unknown — never False
    assert (rec["photo_id"], rec["dedup_key"], rec["taken_ms"], rec["upload_ms"], rec["duration_ms"]) == (
        PID, KEY, TAKEN, UPLOAD, DURATION)
    assert rec["dedup_consistent"] is True


def test_a_compact_record_that_carries_the_flags_is_read_by_field_number():
    std = gpweb.sha1_hex_to_std_b64(gpweb.dedup_key_to_sha1_hex(KEY))
    [rec] = gpweb.parse_swbisb([[[std, _compact(**{"13": [20], "14": True})]]])["matches"]
    assert (rec["partial_upload"], rec["archived"]) == (True, True)
    [rec] = gpweb.parse_swbisb([[[std, _compact(**{"14": False})]]])["matches"]
    assert (rec["partial_upload"], rec["archived"]) == (None, False)


def test_the_live_batch_info_row_has_no_quality_triple_so_original_quality_stays_unknown():
    res = gpweb.parse_EWgK9e([[None, [_info_row(), _info_row(PID_B, "Other.mkv", 5)]]])
    assert res["rejected"] == 0
    assert res["items"] == [
        {"photo_id": PID, "filename": "Sample.Show.S02E03 [a1b2c3].mkv", "size_bytes": 734003200, "taken_ms": TAKEN,
         "tz_offset_ms": TZ, "upload_ms": UPLOAD, "original_quality": None},
        {"photo_id": PID_B, "filename": "Other.mkv", "size_bytes": 5, "taken_ms": TAKEN, "tz_offset_ms": TZ,
         "upload_ms": UPLOAD, "original_quality": None}]


# ===========================================================================
# check_photos' quality pass with the live layouts
# ===========================================================================
def _live(world):
    world.swbisb_compact = world.ewgk9e_live = True


def _second_end(item):
    return item.taken_ms // 1000 * 1000 + 999


def _quality(run_dir):
    return {q["manual_id"]: {k: q[k] for k in ("original_quality", "partial_upload", "archived") if k in q}
            for q in _results(run_dir, "quality")}


def test_with_the_live_layouts_a_partial_upload_is_inconsistent(cw, sandbox):
    _live(cw.world)
    items = _season_of(sandbox, cw.world, {1: "learned", 2: "pending"})
    items[f"{SEASON}e01"].partial_upload = True

    res = main.cmd_check_photos(SEASON)

    assert [(r["status"], r["reason"]) for r in res["rows"]] == [("inconsistent", "inconsistent"),
                                                                ("confirmed", None)]
    assert _kinds(res) == [(f"{SEASON}e01", "partial-upload")]
    assert _quality(res["run_dir"]) == {f"{SEASON}e01": {"partial_upload": True, "archived": False},
                                        f"{SEASON}e02": {"partial_upload": False, "archived": False}}
    lcxim = [e for e in cw.world.rpc_log if e["rpcid"] == "lcxiM"]
    assert [e["request"][1] for e in lcxim] == [_second_end(items[f"{SEASON}e01"])]   # one second, one page


def test_every_pinned_item_gets_its_flags_from_the_timeline(cw, sandbox):
    _live(cw.world)
    items = _season_of(sandbox, cw.world, {1: "learned", 2: "pending", 3: "legacy"})
    items[f"{SEASON}e03"].archived = True

    res = main.cmd_check_photos(SEASON)

    assert _quality(res["run_dir"]) == {f"{SEASON}e01": {"partial_upload": False, "archived": False},
                                        f"{SEASON}e02": {"partial_upload": False, "archived": False},
                                        f"{SEASON}e03": {"partial_upload": False, "archived": True}}
    assert [r["status"] for r in res["rows"]] == ["confirmed"] * 3   # archived is a fact, not a doubt


def test_the_timeline_is_asked_once_per_distinct_taken_second(cw, sandbox):
    _live(cw.world)
    items = _season_of(sandbox, cw.world, {1: "legacy", 2: "legacy", 3: "legacy"})
    items[f"{SEASON}e03"].taken_ms += 60_000                        # another second than e01 / e02

    res = main.cmd_check_photos(SEASON)

    assert res["status"] == "ok" and len(_quality(res["run_dir"])) == 3
    assert sorted(e["request"][1] for e in cw.world.rpc_log if e["rpcid"] == "lcxiM") == sorted(
        {_second_end(items[f"{SEASON}e01"]), _second_end(items[f"{SEASON}e03"])})


def test_a_failing_timeline_call_leaves_the_flags_unknown(cw, sandbox, capsys):
    cw.world.swbisb_compact = True                                  # the Step 2b batch-info layout: quality known
    items = _season_of(sandbox, cw.world, {1: "learned", 2: "pending"})
    items[f"{SEASON}e01"].partial_upload = True
    cw.world.fail_next_rpc("http500", count=4, rpcid="lcxiM")      # gives up after its retries

    res = main.cmd_check_photos(SEASON)

    assert [r["status"] for r in res["rows"]] == ["confirmed", "confirmed"]   # unknown is never a reason to doubt
    assert _quality(res["run_dir"]) == {f"{SEASON}e01": {"original_quality": True},
                                        f"{SEASON}e02": {"original_quality": True}}
    assert "A batched Google Photos call failed (RpcUnavailable)" in capsys.readouterr().out


def test_when_the_hash_record_tells_the_flags_the_timeline_is_never_asked(cw, sandbox):
    items = _season_of(sandbox, cw.world, {1: "learned", 2: "pending", 3: "legacy"})   # the Step 2b layouts
    items[f"{SEASON}e01"].partial_upload = True

    res = main.cmd_check_photos(SEASON)

    assert _calls(cw.world, "lcxiM") == 0
    assert _kinds(res) == [(f"{SEASON}e01", "partial-upload")]
