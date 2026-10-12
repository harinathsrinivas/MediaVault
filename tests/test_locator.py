"""IMP-C25 Step 12 — the exact-item locator, candidate C: hybrid arbitration (docs/feature-fetch-datetime/PLAN.md
Step 12, P-5, P-7, P-8, P-9, P-20; OD-5).

  12C.a  locate_object — S0a photo_id · S0b swbisb · S1 taken second · S2 exact name · S3 title + short_id · S3u the
         user's search_override, RPC first with a per-stage fallback to the web pages, one record cache fed by both,
         the drift detector, the OD-5 preview of today's queries; read-only
  12C.b  the fetch's exact-item phase (_run_locator_phase): the direct download first, then Shift+D on the item page
         (and the click path), ≤3 at once, SHA-256 deciding; OD-5 skips; the failed list; the journal
  12C.c  `python mainfetch.py locate` — read-only, no download, no write

Hermetic: the fake Google Photos world + its batchexecute emulator (tests/gphotos_fake.py); mainfetch's and gprpc's
clocks are fake (nothing waits); the autouse fixtures of tests/conftest.py redirect every state dir, ~/Downloads and
the process table. No network, no browser, no real C:\\Media. Synthetic ids only (every photo id contains "FAKE").
"""
import contextlib
import hashlib
import json
import os
import types

import pytest

import gpcapture
import gphotos_fake
import gprpc
import gpweb
import mainfetch
import mvcommon
from test_fetch_harvester import _Clock

READ_ONLY = frozenset(gphotos_fake.READ_ONLY_RPCIDS)
MUTATING = gphotos_fake.MUTATING_RPCIDS
TAKEN = "2022-02-05T11:49:46Z"          # one release batch's second (RESEARCH F13)
TAKEN_MS = 1644061786000
EP = "tv-en-2015-exampleshow-s02e03"
SHOW = "Example.Show.S02E03.Pilot.2015.1080p.BluRay.mkv"
SID = "a1b2c3"
TRIGGER_DOWNLOAD = mainfetch.trigger_download   # today's legacy trigger (S4) — captured before any fixture stubs it


@pytest.fixture(autouse=True)
def clock(monkeypatch):
    """mainfetch and gprpc share one fake clock: every sleep, poll and RPC pacing gap is instant."""
    c = _Clock()
    monkeypatch.setattr(mainfetch, "time", c)
    monkeypatch.setattr(gprpc, "_monotonic", c.time)
    monkeypatch.setattr(gprpc, "_sleep", c.sleep)
    monkeypatch.setattr(gprpc, "_uniform", lambda low, high: low)
    return c


# ================================================================================ harness
def _sha256(data):
    return hashlib.sha256(data).hexdigest()


def _sha1(data):
    return hashlib.sha1(data).hexdigest()


def _bytes(tag):
    return f"FAKE-BYTES:{tag}:".encode() * 40


def _up(name, sid):
    """The exact uploaded name of a whole file (cmd_push's rule)."""
    stem, ext = os.path.splitext(name)
    return f"{stem} [{sid}]{ext}"


def _folder(tmp_path, name="Show"):
    folder = tmp_path / name
    folder.mkdir(exist_ok=True)
    return folder


def _whole(folder, name, data, sid, block=None, search_term=None):
    """A whole-file library leaf (cmd_prep's keys) whose bytes are `data`."""
    entry = {"filename": name, "short_id": sid, "folder_path": str(folder), "hash": _sha256(data),
             "search_term": search_term or _up(name, sid), "status": "archived", "uploaded": True,
             "tech_spec": {"size_bytes": len(data)}}
    if block is not None:
        entry["gphotos"] = block
    return entry


def _split(folder, name, sid, datas, blocks=None):
    """A split leaf whose chunk n holds datas[n-1] (uploaded under '<stem> [sid].chunk.00n.mkv')."""
    stem = os.path.splitext(name)[0]
    chunks = [{"filename": f"{stem} [{sid}].chunk.{n:03d}.mkv", "hash": _sha256(d)} for n, d in enumerate(datas, 1)]
    for chunk, block in zip(chunks, blocks or []):
        if block is not None:
            chunk["gphotos"] = block
    return {"filename": name, "short_id": sid, "folder_path": str(folder), "hash": _sha256(b"".join(datas)),
            "search_term": _up(name, sid), "status": "archived", "uploaded": True,
            "tech_spec": {"size_bytes": sum(len(d) for d in datas)},
            "split_info": {"is_split": True, "total_chunks": len(chunks), "chunks": chunks}}


def _capture(folder, sid, objects, offset_min=480):
    """The push-time capture of a new archival (gpcapture v2 shape): SHA-1 + DateUTC per object."""
    doc = {"capture_version": 2, "manual_id": "x", "short_id": sid, "prep": {},
           "pushes": [{"captured_at": "2026-09-30T10:00:00Z", "device_id": "FAKEPIXEL", "device_tz": "Asia/Singapore",
                       "device_tz_offset_min": offset_min, "complete": True, "objects": objects}]}
    (folder / f"{sid}.gpcapture.json").write_text(json.dumps(doc), encoding="utf-8")


def _obj(data, uploaded, role="whole", index=None, date_utc=TAKEN, sha1=True):
    """One pushed object of a capture, with the approach_hint a real push gives it (gpcapture.approach_hint)."""
    obj = {"role": role, "uploaded_name": uploaded, "sha256": _sha256(data), "size_bytes": len(data)}
    if sha1:
        obj["sha1"] = _sha1(data)
    if date_utc:
        obj["date_utc"] = date_utc
    if index is not None:
        obj["index"] = index
    obj["approach_hint"] = gpcapture.approach_hint(obj)
    return obj


def _learned(item, data=None, account="tv", proof="dedupkey", content_key=True, **block):
    """A TRUSTED gphotos block (P-1/P-2) for the object holding `data` (default: the fake item's bytes) whose
    `account` record names `item` (a mapped / confirmed object)."""
    data = item.data if data is None else data
    record = {"photo_id": item.photo_id, "taken_ms": item.taken_ms, "tz_offset_ms": item.tz_offset_ms,
              "upload_ms": item.upload_ms, "dedup_key": item.dedup_key, "verified_by": proof,
              "verified_at": "2026-10-01T00:00:00Z", "source": "map"}
    return mvcommon.new_gphotos_block(_sha256(data), sha1=_sha1(data) if content_key else None,
                                      accounts={account: record}, **block)


def _ctx(account="tv"):
    ctx = mainfetch.FetchContext()
    ctx.start_session(account)
    return ctx


def _locators(entry, entry_id, ctx, tmp_path):
    """Every object's locator, as the fetch builds them (a restore folder holding none of the files)."""
    return [i["locator"] for i in mainfetch.build_fetch_queue(entry, str(tmp_path / "nowhere"), ctx, entry_id)]


def _assert_read_only_rpc(world):
    """P-9 / P13: only allowlisted read-only rpcids ever reached the emulator."""
    sent = set(world.rpc_ids_sent())
    assert sent <= READ_ONLY and not sent & MUTATING, sent


def _tree(root):
    return sorted(str(p.relative_to(root)) for p in root.rglob("*"))


def _drift(world, rpcid):
    """Silent RPC drift: the answers of `rpcid` move the item's timezone offset and upload time (both still
    integers, so the typed parsers accept them) — only the drift detector can tell."""
    real = getattr(world, f"_h_{rpcid}")

    def swap(arr):
        if isinstance(arr, list) and len(arr) > 5 and isinstance(arr[0], str) and arr[0].startswith("AF1QipFAKE"):
            arr = list(arr)
            arr[4], arr[5] = arr[5], arr[4]
        return arr

    def handler(acc, req):
        payload, code = real(acc, req)
        if rpcid == "VrseUb" and payload:
            payload = [swap(payload[0])] + payload[1:]
        elif rpcid == "swbisb" and payload:
            payload = [[[h, swap(a)] for h, a in payload[0]]]
        elif rpcid in ("EzkLib", "lcxiM") and payload:
            payload = [[swap(a) for a in payload[0]]] + payload[1:]
        return payload, code

    setattr(world, f"_h_{rpcid}", handler)


# ===========================================================================
# 12C.a — locate_object
# ===========================================================================
def test_s0a_a_learned_photo_id_is_pinned_by_item_info_with_zero_searches(fake_photos, tmp_path):
    data = _bytes("s02e03")
    item = fake_photos.add_item("tv", _up(SHOW, SID), data=data, taken=TAKEN)
    fake_photos.add_item("tv", "Noise [zzz999].mkv", taken=TAKEN)
    entry = _whole(_folder(tmp_path), SHOW, data, SID, block=_learned(item))
    ctx = _ctx()
    [loc] = _locators(entry, EP, ctx, tmp_path)

    res = mainfetch.locate_object(fake_photos.driver("tv"), loc, ctx)

    assert res["found"] and res["photo_id"] == item.photo_id
    assert (res["stage"], res["approach"], res["proof"], res["via"]) == ("s0_photo_id", "photo_id", "dedupkey", "rpc")
    assert res["per_approach"] == {"photo_id": {"outcome": "exactly-one", "n": 1}}
    assert res["per_approach_via"] == {"photo_id": "rpc"}
    assert res["record"] == {"photo_id": item.photo_id, "dedup_key": item.dedup_key, "taken_ms": item.taken_ms,
                             "tz_offset_ms": item.tz_offset_ms, "upload_ms": item.upload_ms, "width": item.width,
                             "height": item.height, "filename": item.filename}
    assert res["searches"] == 0 and res["candidates_seen"] == 1
    assert fake_photos.rpc_ids_sent() == ["VrseUb", "fDcn4b"]
    assert res["rpc_calls"] == 2
    assert res["pages_opened"] == 1                  # candidate C: the run's one drift cross-check
    assert mainfetch._locator_run(ctx).drift == "ok"
    for key in ("found", "photo_id", "record", "proof", "stage", "approach", "via", "candidates_seen",
                "pages_opened", "searches", "rpc_calls", "elapsed", "per_approach"):
        assert key in res                            # PLAN Step 12's contract


def test_the_drift_cross_check_is_paid_once_per_run(fake_photos, tmp_path):
    folder = _folder(tmp_path)
    ctx, d = _ctx(), fake_photos.driver("tv")
    pages = []
    for n in (2, 3, 4):
        data = _bytes(f"ep{n}")
        item = fake_photos.add_item("tv", _up(f"Example.Show.S02E0{n}.mkv", f"e{n}e{n}e{n}"), data=data, taken=TAKEN)
        entry = _whole(folder, f"Example.Show.S02E0{n}.mkv", data, f"e{n}e{n}e{n}", block=_learned(item))
        [loc] = _locators(entry, f"tv-en-2015-exampleshow-s02e0{n}", ctx, tmp_path)
        res = mainfetch.locate_object(d, loc, ctx)
        pages.append(res["pages_opened"])
        assert res["found"] and res["photo_id"] == item.photo_id and res["searches"] == 0
    assert pages == [1, 0, 0]                        # only the run's first RPC-located object is cross-checked


def test_s0b_the_hash_lookup_pins_a_content_keyed_object_directly(fake_photos, tmp_path):
    data = _bytes("new-archival")
    item = fake_photos.add_item("tv", _up(SHOW, SID), data=data, taken=TAKEN)
    fake_photos.add_item("tv", _up(SHOW, SID), data=_bytes("other"), taken=TAKEN)   # same name, other bytes
    folder = _folder(tmp_path)
    _capture(folder, SID, [_obj(data, _up(SHOW, SID))])
    entry = _whole(folder, SHOW, data, SID)
    ctx = _ctx()
    [loc] = _locators(entry, EP, ctx, tmp_path)
    assert loc["approach_best"] == "hash" and loc["sha1"] == _sha1(data)

    res = mainfetch.locate_object(fake_photos.driver("tv"), loc, ctx)

    assert res["found"] and res["photo_id"] == item.photo_id
    assert (res["stage"], res["proof"], res["via"]) == ("s0_hash", "dedupkey", "rpc")
    assert res["per_approach"] == {"hash": {"outcome": "exactly-one", "n": 1}}
    assert res["per_approach_via"] == {"hash": "rpc"}
    assert fake_photos.rpc_ids_sent() == ["swbisb"] and res["searches"] == 0
    _assert_read_only_rpc(fake_photos)


def test_s1_a_same_second_batch_of_ten_is_resolved_by_dedupkey_by_rpc(fake_photos, tmp_path):
    datas = [_bytes(f"batch{n}") for n in range(10)]
    items = [fake_photos.add_item("tv", f"Example.Show.S02E{n + 1:02d} [b{n:02d}b{n:02d}].mkv", data=d, taken=TAKEN)
             for n, d in enumerate(datas)]
    fake_photos.add_item("tv", "Earlier [eee111].mkv", taken="2022-02-05T11:49:29Z")   # the previous batch's second
    target = items[6]
    block = mvcommon.new_gphotos_block(_sha256(datas[6]), sha1=_sha1(datas[6]), date_utc=TAKEN,
                                       accounts={"tv": {"approach": {"best": "taken_second", "ok": ["taken_second"]}}})
    entry = _whole(_folder(tmp_path), "Example.Show.S02E07.mkv", datas[6], "b06b06", block=block)
    ctx = _ctx()
    [loc] = _locators(entry, "tv-en-2015-exampleshow-s02e07", ctx, tmp_path)
    assert (loc["approach_best"], loc["taken_ms"]) == ("taken_second", TAKEN_MS)

    d = fake_photos.driver("tv")
    res = mainfetch.locate_object(d, loc, ctx)

    assert res["found"] and res["photo_id"] == target.photo_id
    assert (res["stage"], res["via"], res["outcome"], res["n"]) == ("s1_taken_second", "rpc", "exactly-one", 10)
    assert fake_photos.rpc_ids_sent() == ["lcxiM"]                     # one timeline page, no item opened by RPC
    opened = [a for a in d.actions if a["kind"] == "open"]
    assert opened == [] and res["pages_opened"] == 1                   # only the drift cross-check, of the pick
    assert res["candidates_seen"] == 10


def test_s1_on_the_web_pages_when_rpc_is_unavailable(fake_photos, tmp_path, capsys):
    datas = [_bytes(f"batch{n}") for n in range(10)]
    items = [fake_photos.add_item("tv", f"Example.Show.S02E{n + 1:02d} [b{n:02d}b{n:02d}].mkv", data=d, taken=TAKEN)
             for n, d in enumerate(datas)]
    fake_photos.wiz_token_missing["tv"] = True                         # every RPC: RpcUnavailable
    block = mvcommon.new_gphotos_block(_sha256(datas[6]), sha1=_sha1(datas[6]), date_utc=TAKEN,
                                       accounts={"tv": {"approach": {"best": "taken_second", "ok": ["taken_second"]}}})
    entry = _whole(_folder(tmp_path), "Example.Show.S02E07.mkv", datas[6], "b06b06", block=block)
    ctx = _ctx()
    [loc] = _locators(entry, "tv-en-2015-exampleshow-s02e07", ctx, tmp_path)

    res = mainfetch.locate_object(fake_photos.driver("tv"), loc, ctx)

    assert res["found"] and res["photo_id"] == items[6].photo_id
    assert (res["stage"], res["via"], res["proof"]) == ("s1_taken_second", "ui", "dedupkey")
    assert res["per_approach"]["taken_second"] == {"outcome": "exactly-one", "n": 10}
    assert res["per_approach_via"]["taken_second"] == "ui"
    assert res["searches"] == 1 and res["pages_opened"] == 7           # the day search; items opened up to the match
    out = capsys.readouterr().out
    assert out.count("A Google Photos internal call failed (RpcUnavailable)") == 1
    assert fake_photos.rpc_log == []                                   # no page tokens: gprpc never even sent one


def test_s1_on_the_web_pages_reads_the_whole_release_day_before_filtering_to_the_second(fake_photos, tmp_path):
    # RESEARCH F13, the real day: February 5, 2022 = 38 videos = four seasons, each at ONE second (8 + 10 + 10 + 10).
    # The day search lists them newest first, so the oldest batch sits at tiles 31–38 — beyond the first viewport and
    # beyond LOCATE_MAX_CANDIDATES. The candidate cap applies AFTER the same-second filter, never to the day listing.
    batches = (("2022-02-05T11:52:28Z", 10), ("2022-02-05T11:52:19Z", 10), ("2022-02-05T11:49:46Z", 10),
               ("2022-02-05T11:49:29Z", 8))
    season = {}
    for s, (taken, count) in enumerate(reversed(batches), 1):
        season[s] = [fake_photos.add_item("tv", f"Example.Show.S0{s}E{e:02d} [s{s}e{e:03d}].mkv",
                                          data=_bytes(f"s{s}e{e}"), taken=taken) for e in range(1, count + 1)]
    target = season[1][4]                                               # the oldest batch: tiles 31–38
    assert len(fake_photos.search("tv", "2022-02-05")) == 38 > fake_photos.viewport_tiles > mainfetch.LOCATE_MAX_CANDIDATES
    fake_photos.wiz_token_missing["tv"] = True
    block = mvcommon.new_gphotos_block(_sha256(target.data), sha1=_sha1(target.data), date_utc="2022-02-05T11:49:29Z",
                                       accounts={"tv": {"approach": {"best": "taken_second", "ok": ["taken_second"]},
                                                        "tz_offset_ms": 28800000}})
    entry = _whole(_folder(tmp_path), "Example.Show.S01E05.mkv", target.data, "s1e005", block=block)
    ctx = _ctx()
    [loc] = _locators(entry, "tv-en-2015-exampleshow-s01e05", ctx, tmp_path)

    res = mainfetch.locate_object(fake_photos.driver("tv"), loc, ctx)

    assert res["found"] and res["photo_id"] == target.photo_id
    assert (res["stage"], res["via"], res["proof"]) == ("s1_taken_second", "ui", "dedupkey")
    assert res["per_approach"]["taken_second"] == {"outcome": "exactly-one", "n": 8}   # its batch only
    assert res["per_approach_via"]["taken_second"] == "ui"
    assert res["searches"] == 1 and res["pages_opened"] == 5           # one day search; S01E01…E05 opened


LIVE_NAME = "Valley.Show.S02E02.Runaway.2015.1080p.BluRay.REMUX.mkv"


def test_s2_a_legacy_object_is_pinned_by_its_exact_name_never_by_tile_position(fake_photos, tmp_path):
    # The live 2026-10-05 failure (STATUS / brief): the exact-name search lists unrelated items first ("most
    # relevant") and the right one twice; the legacy click took index 0 — a different episode. S2 verifies each.
    right = fake_photos.add_item("tv", _up(LIVE_NAME, "0003a8"), taken=TAKEN)
    space = fake_photos.add_item("tv", "Space.Show.S01E02.The.Big.Empty [bd42e9].mkv", taken="2020-04-30T08:15:20Z")
    robot = fake_photos.add_item("tv", "Hacker.Show.S02E12 [a08d87].chunk.002.mkv", taken="2026-02-04T10:44:51Z")
    fake_photos.script_search("tv", _up(LIVE_NAME, "0003a8"),
                              [space.photo_id, robot.photo_id, right.photo_id, right.photo_id])
    entry = _whole(_folder(tmp_path), LIVE_NAME, _bytes("legacy-dummy"), "0003a8")   # no block, no capture: legacy
    ctx = _ctx()
    [loc] = _locators(entry, "tv-en-2015-valleyshow-s02e02", ctx, tmp_path)
    assert loc["sha1"] is None and loc["photo_id"] is None and loc["taken_ms"] is None

    res = mainfetch.locate_object(fake_photos.driver("tv"), loc, ctx)

    assert res["found"] and res["photo_id"] == right.photo_id
    assert (res["stage"], res["proof"], res["name_normalized"], res["via"]) == ("s2_exact_name", "filename", False, "rpc")
    assert res["per_approach"] == {"exact_name": {"outcome": "exactly-one", "n": 3}}
    assert res["per_approach_via"] == {"exact_name": "rpc"}
    assert res["candidates_seen"] == 3 and res["query"] == _up(LIVE_NAME, "0003a8")
    assert fake_photos.rpc_ids_sent() == ["EzkLib", "EWgK9e"]           # one search + one batch of names


def test_s2_on_the_uk_web_page_a_legacy_name_matches_after_normalizing(fake_photos, tmp_path):
    # Movies account: UK English, a current-year item (the panel omits the year), a name with double spaces that
    # innerText collapses (P-6: a normalized match is still a filename proof).
    name = "Some  Film  Night.2026.2160p.mkv"
    item = fake_photos.add_item("movies", _up(name, "f0f0f0"), taken="2026-03-14T09:00:20Z", tz_offset_min=60)
    fake_photos.add_item("movies", "Some Film Night Extended [f9f9f9].mkv", taken="2026-03-14T09:00:20Z")
    fake_photos.wiz_token_missing["movies"] = True
    entry = _whole(_folder(tmp_path, "Film"), name, _bytes("film-dummy"), "f0f0f0")
    ctx = _ctx("movies")
    [loc] = _locators(entry, "mov-en-2026-somefilmnight", ctx, tmp_path)

    d = fake_photos.driver("movies")
    res = mainfetch.locate_object(d, loc, ctx)

    assert res["found"] and res["photo_id"] == item.photo_id
    assert (res["stage"], res["proof"], res["name_normalized"], res["via"]) == ("s2_exact_name", "filename", True, "ui")
    assert res["record"]["filename"] == "Some Film Night.2026.2160p [f0f0f0].mkv"   # the panel's collapsed name
    assert [a["open"] for a in d.actions if a["kind"] == "info_toggle"] == [True]   # opened once, never shut


def test_s0a_a_stale_photo_id_is_reported_and_the_next_stage_finds_the_reupload(fake_photos, tmp_path):
    data = _bytes("reuploaded")
    dead = fake_photos.add_item("tv", _up(SHOW, SID), data=data, taken=TAKEN, deleted=True)
    alive = fake_photos.add_item("tv", _up(SHOW, SID), data=data, taken=TAKEN)
    entry = _whole(_folder(tmp_path), SHOW, data, SID, block=_learned(dead))
    ctx = _ctx()
    [loc] = _locators(entry, EP, ctx, tmp_path)

    res = mainfetch.locate_object(fake_photos.driver("tv"), loc, ctx)

    assert res["found"] and res["photo_id"] == alive.photo_id and res["stage"] == "s0_hash"
    assert res["stale"] == [dead.photo_id]                             # RPC said gone AND its page shows nothing
    assert res["per_approach"]["photo_id"] == {"outcome": "not-found", "n": 0}
    assert res["per_approach_via"]["photo_id"] == "ui"
    assert ("tv", dead.photo_id) in mainfetch._locator_run(ctx).gone


def test_s0a_rpc_calling_an_item_gone_is_overruled_by_its_page(fake_photos, tmp_path, monkeypatch):
    # Arbitration: the "not found" status mapping is UNVERIFIED (gprpc.STATUS_NOT_FOUND); the page has the last word.
    data = _bytes("still-there")
    item = fake_photos.add_item("tv", _up(SHOW, SID), data=data, taken=TAKEN)
    entry = _whole(_folder(tmp_path), SHOW, data, SID, block=_learned(item))
    monkeypatch.setattr(gprpc, "item_info", lambda driver, photo_id: None)
    ctx = _ctx()
    [loc] = _locators(entry, EP, ctx, tmp_path)

    res = mainfetch.locate_object(fake_photos.driver("tv"), loc, ctx)

    assert res["found"] and res["photo_id"] == item.photo_id
    assert (res["stage"], res["via"], res["stale"]) == ("s0_photo_id", "ui", [])


def _resplit(world, folder, legacy):
    """A 2-chunk film uploaded twice under the same chunk names (a re-split): the OLD set first, then the NEW one
    the library's split_info describes. legacy: no content keys anywhere (names only)."""
    name, sid = "Some.Film.2019.mkv", "c3c3c3"
    stem = os.path.splitext(name)[0]
    old = [world.add_item("movies", f"{stem} [{sid}].chunk.00{n}.mkv", data=_bytes(f"old{n}"),
                          taken="2025-01-10T08:00:00Z") for n in (1, 2)]
    new = [world.add_item("movies", f"{stem} [{sid}].chunk.00{n}.mkv", data=_bytes(f"new{n}"),
                          taken="2026-02-20T09:30:00Z") for n in (1, 2)]
    datas = [_bytes("new1"), _bytes("new2")]
    if not legacy:
        _capture(folder, sid, [_obj(d, f"{stem} [{sid}].chunk.00{n}.mkv", role="chunk", index=n,
                                    date_utc="2026-02-20T09:30:00Z") for n, d in enumerate(datas, 1)])
    return _split(folder, name, sid, datas), old, new


@pytest.mark.parametrize("legacy", [False, True], ids=["content-key", "names-only"])
def test_a_re_split_duplicate_set_resolves_to_the_set_the_library_holds(fake_photos, tmp_path, legacy):
    entry, old, new = _resplit(fake_photos, _folder(tmp_path, "Film"), legacy)
    ctx, d = _ctx("movies"), fake_photos.driver("movies")
    locs = _locators(entry, "mov-en-2019-somefilm", ctx, tmp_path)

    results = [mainfetch.locate_object(d, loc, ctx) for loc in locs]

    assert [r["photo_id"] for r in results] == [new[0].photo_id, new[1].photo_id]
    if legacy:   # names alone: both sets match — the newest set is picked (Step 9's rule), SHA-256 decides later
        assert [(r["stage"], r["outcome"], r["rule"], r["proof"]) for r in results] == [
            ("s2_exact_name", "ambiguous", "newest-set", "filename")] * 2
        assert [r["alternates"] for r in results] == [[old[0].photo_id], [old[1].photo_id]]
    else:        # the content key never even looks at the old set
        assert [(r["stage"], r["outcome"], r["proof"]) for r in results] == [("s0_hash", "exactly-one", "dedupkey")] * 2


def test_a_no_date_utc_object_skips_s1_and_is_pinned_at_s2(fake_photos, tmp_path):
    # RESEARCH §3.10: an object without a DateUTC (an MP4) has no taken second to look at — its content key still
    # proves it once a text search lists it. S0b answering nothing usable (here: its answer changed shape) is not
    # the end: the waterfall goes on.
    name = "Example.Show.S01E01.mp4"
    data = _bytes("mp4-without-date")
    item = fake_photos.add_item("tv", _up(name, "d0d0d0"), data=data, taken="2019-06-01T10:00:00Z")
    folder = _folder(tmp_path)
    _capture(folder, "d0d0d0", [_obj(data, _up(name, "d0d0d0"), date_utc=None)])
    fake_photos.fail_next_rpc("changed_shape", rpcid="swbisb")
    ctx = _ctx()
    [loc] = _locators(_whole(folder, name, data, "d0d0d0"), "tv-en-2019-exampleshow-s01e01", ctx, tmp_path)
    assert loc["taken_ms"] is None and loc["sha1"] == _sha1(data)

    res = mainfetch.locate_object(fake_photos.driver("tv"), loc, ctx)

    assert res["found"] and res["photo_id"] == item.photo_id
    assert (res["stage"], res["proof"], res["via"]) == ("s2_exact_name", "dedupkey", "rpc")
    assert res["per_approach"] == {"hash": {"outcome": "error", "n": 0},
                                   "exact_name": {"outcome": "exactly-one", "n": 1}}   # no S1 at all
    assert res["per_approach_via"] == {"hash": "rpc", "exact_name": "rpc"}


ANIME_NAME = "Death.Saga.01.Rebirth.1080p.mkv"   # synthetic stand-in for the live case


@pytest.mark.parametrize("rpc", [True, False], ids=["rpc", "web-pages"])
def test_s3_ends_with_the_bare_short_id_when_name_and_title_find_nothing(fake_photos, tmp_path, rpc):
    # Live 2026-10-07 (anime, "Death Note 01"): "No results" for the exact uploaded name, the title and the plain name;
    # the bare short id listed exactly the item. S3's last query is the bare short id — its candidates are still
    # verified on their records, here against another item that carries the id in its name.
    sid = "d0a1e2"
    item = fake_photos.add_item("anime", _up(ANIME_NAME, sid), taken="2006-10-03T15:00:00Z")
    lookalike = fake_photos.add_item("anime", f"Other.Saga.05 [{sid}9].mkv", taken="2007-01-01T00:00:00Z")
    entry = _whole(_folder(tmp_path, "Anime"), ANIME_NAME, _bytes("dummy"), sid)
    ctx = _ctx("anime")
    [loc] = _locators(entry, "ani-ja-2006-deathsaga01", ctx, tmp_path)
    for query in (_up(ANIME_NAME, sid), loc["queries"]["s3"][0], ANIME_NAME):
        fake_photos.script_search("anime", query, [])
    fake_photos.script_search("anime", sid, [lookalike.photo_id, item.photo_id])
    fake_photos.wiz_token_missing["anime"] = not rpc

    res = mainfetch.locate_object(fake_photos.driver("anime"), loc, ctx)

    assert res["found"] and res["photo_id"] == item.photo_id
    assert (res["stage"], res["proof"], res["query"], res["via"]) == ("s3_title_sid", "filename", sid,
                                                                      "rpc" if rpc else "ui")
    assert res["per_approach"]["exact_name"]["outcome"] == "not-found"
    assert res["per_approach"]["title_sid"] == {"outcome": "exactly-one", "n": 2}
    assert res["per_approach_via"]["title_sid"] == ("rpc" if rpc else "ui")


def test_a_short_id_under_six_characters_is_never_searched_alone(fake_photos, tmp_path):
    entry = _whole(_folder(tmp_path, "Anime"), ANIME_NAME, _bytes("dummy"), "d0a1e")
    ctx = _ctx("anime")
    [loc] = _locators(entry, "ani-ja-2006-deathsaga01", ctx, tmp_path)

    res = mainfetch.locate_object(fake_photos.driver("anime"), loc, ctx)

    assert not res["found"]
    queries = [e["request"][0] for e in fake_photos.rpc_log if e["rpcid"] == "EzkLib"]
    assert "d0a1e" not in queries and len(queries) == 2                # the exact name and the title only


def _override_entry(tmp_path, query):
    """A legacy episode whose exact name and title query find nothing (RESEARCH F25: 4 of 11 exact names gave zero
    results) and whose failed-list row the user answered with `query` (OD-5 → gphotos.search_override)."""
    name = "Example.Show.S03E01.mkv"
    data = _bytes("s03e01-dummy")
    block = mvcommon.new_gphotos_block(_sha256(data), search_override={"query": query, "at": "2026-10-02T00:00:00Z"})
    return _whole(_folder(tmp_path), name, data, "u1u1u1", block=block), _up(name, "u1u1u1")


@pytest.mark.parametrize("listed_right", [True, False], ids=["verified", "only-other-items"])
def test_s3u_the_users_search_term_is_tried_and_still_verified(fake_photos, tmp_path, listed_right):
    entry, uploaded = _override_entry(tmp_path, "example show third season opener")
    right = fake_photos.add_item("tv", uploaded, taken=TAKEN)
    other = fake_photos.add_item("tv", "Example.Show.S03E01.Extended [u9u9u9].mkv", taken=TAKEN)
    for query in (uploaded, "Example Show u1u1u1", "u1u1u1"):          # name, title and bare short id: nothing
        fake_photos.script_search("tv", query, [])
    fake_photos.script_search("tv", "example show third season opener",
                              [other.photo_id] + ([right.photo_id] if listed_right else []))
    ctx = _ctx()
    [loc] = _locators(entry, "tv-en-2015-exampleshow-s03e01", ctx, tmp_path)
    assert loc["queries"]["s3u"] == ["example show third season opener"]

    res = mainfetch.locate_object(fake_photos.driver("tv"), loc, ctx)

    expected = {"exact_name": {"outcome": "not-found", "n": 0},
                "title_sid": {"outcome": "not-found", "n": 0}}
    if listed_right:
        assert res["found"] and res["photo_id"] == right.photo_id
        assert (res["stage"], res["approach"], res["proof"]) == ("s3u_user_query", "user_query", "filename")
        expected["user_query"] = {"outcome": "exactly-one", "n": 2}
    else:            # the user's term lists only another item: verified away, never taken
        assert not res["found"] and not res["s4_skip"]          # no content key: today's attempts stay (OD-5)
        expected["user_query"] = {"outcome": "wrong-item", "n": 1}
    assert res["per_approach"] == expected
    assert res["per_approach_via"] == {"exact_name": "rpc", "title_sid": "rpc", "user_query": "rpc"}


def test_nothing_verifiable_previews_todays_queries_and_marks_the_skip(fake_photos, tmp_path):
    # OD-5: a content-keyed object whose every candidate — including what today's two attempts would click — holds
    # other bytes. Nothing is pinned; the skip is marked (the fetch then writes the s4-skipped row).
    name = "Example.Show.S04E01.mkv"
    data = _bytes("the-real-bytes")
    fake_photos.add_item("tv", _up(name, "l0l0l0"), data=_bytes("an-older-upload"), taken=TAKEN)
    fake_photos.add_item("tv", name, data=_bytes("someone-else"), taken=TAKEN)   # under the plain library name
    folder = _folder(tmp_path)
    _capture(folder, "l0l0l0", [_obj(data, _up(name, "l0l0l0"))])
    ctx, d = _ctx(), fake_photos.driver("tv")
    [loc] = _locators(_whole(folder, name, data, "l0l0l0"), "tv-en-2015-exampleshow-s04e01", ctx, tmp_path)

    res = mainfetch.locate_object(d, loc, ctx)

    assert not res["found"] and res["s4_skip"] is True
    assert res["per_approach"] == {
        "hash": {"outcome": "not-found", "n": 0},
        "taken_second": {"outcome": "wrong-item", "n": 2},
        "exact_name": {"outcome": "wrong-item", "n": 1},
        "title_sid": {"outcome": "wrong-item", "n": 1},       # its bare short id lists the old upload
        "legacy": {"outcome": "wrong-item", "n": 2}}
    assert res["per_approach_via"] == dict.fromkeys(res["per_approach"], "rpc")
    assert res["candidates_seen"] == 2 and fake_photos.downloads == []
    assert not [a for a in d.actions if a["kind"] in ("shift_d", "open")]


def test_rpc_unavailable_every_stage_runs_on_the_web_pages(fake_photos, tmp_path, capsys):
    data = _bytes("learned")
    learned = fake_photos.add_item("tv", _up(SHOW, SID), data=data, taken=TAKEN)
    legacy = fake_photos.add_item("tv", _up(LIVE_NAME, "0003a8"), taken=TAKEN)
    fake_photos.wiz_token_missing["tv"] = True
    folder = _folder(tmp_path)
    ctx, d = _ctx(), fake_photos.driver("tv")
    [loc1] = _locators(_whole(folder, SHOW, data, SID, block=_learned(learned)), EP, ctx, tmp_path)
    [loc2] = _locators(_whole(folder, LIVE_NAME, _bytes("dummy"), "0003a8"), "tv-en-2015-valleyshow-s02e02", ctx,
                       tmp_path)

    first, second = mainfetch.locate_object(d, loc1, ctx), mainfetch.locate_object(d, loc2, ctx)

    assert (first["photo_id"], first["stage"], first["via"]) == (learned.photo_id, "s0_photo_id", "ui")
    assert (second["photo_id"], second["stage"], second["via"]) == (legacy.photo_id, "s2_exact_name", "ui")
    assert second["per_approach"]["exact_name"] == {"outcome": "exactly-one", "n": 1}
    assert second["per_approach_via"]["exact_name"] == "ui"
    assert capsys.readouterr().out.count("A Google Photos internal call failed") == 1     # one note per run
    assert fake_photos.rpc_log == [] and mainfetch._locator_run(ctx).stats["tv"]["fallbacks"] >= 2


def test_one_failing_rpc_stage_falls_back_alone_and_rpc_stays_on(fake_photos, tmp_path):
    # Candidate C: per STAGE, not per run — the search's changed answer moves S2 to the web page for that object
    # only; the next object's S2 asks RPC again.
    first_item = fake_photos.add_item("tv", _up(LIVE_NAME, "0003a8"), taken=TAKEN)
    other_name = "Valley.Show.S02E03.Signaling.Risk.2015.1080p.BluRay.REMUX.mkv"
    second_item = fake_photos.add_item("tv", _up(other_name, "0003a9"), taken=TAKEN)
    fake_photos.fail_next_rpc("changed_shape", rpcid="EzkLib")
    folder = _folder(tmp_path)
    ctx, d = _ctx(), fake_photos.driver("tv")
    [loc1] = _locators(_whole(folder, LIVE_NAME, _bytes("d1"), "0003a8"), "tv-en-2015-valleyshow-s02e02", ctx, tmp_path)
    [loc2] = _locators(_whole(folder, other_name, _bytes("d2"), "0003a9"), "tv-en-2015-valleyshow-s02e03", ctx, tmp_path)

    first, second = mainfetch.locate_object(d, loc1, ctx), mainfetch.locate_object(d, loc2, ctx)

    assert (first["photo_id"], first["via"]) == (first_item.photo_id, "ui")
    assert (second["photo_id"], second["via"]) == (second_item.photo_id, "rpc")
    assert mainfetch._locator_run(ctx).rpc_off is None
    assert fake_photos.rpc_ids_sent().count("EzkLib") == 2             # the failed search, then the next object's


def test_the_drift_detector_turns_rpc_off_for_the_run_on_a_mismatch(fake_photos, tmp_path, capsys):
    datas = [_bytes("d-one"), _bytes("d-two")]
    items = [fake_photos.add_item("tv", _up(f"Example.Show.S05E0{n}.mkv", f"g{n}g{n}g{n}"), data=d, taken=TAKEN)
             for n, d in enumerate(datas, 1)]
    _drift(fake_photos, "VrseUb")                    # RPC now reports another timezone offset and upload time
    folder = _folder(tmp_path)
    ctx, d = _ctx(), fake_photos.driver("tv")
    locs = [_locators(_whole(folder, f"Example.Show.S05E0{n}.mkv", data, f"g{n}g{n}g{n}", block=_learned(item)),
                      f"tv-en-2015-exampleshow-s05e0{n}", ctx, tmp_path)[0]
            for n, (data, item) in enumerate(zip(datas, items), 1)]

    first = mainfetch.locate_object(d, locs[0], ctx)
    sent = len(fake_photos.rpc_log)
    second = mainfetch.locate_object(d, locs[1], ctx)

    run = mainfetch._locator_run(ctx)
    assert (run.drift, run.rpc_off) == ("drift", "drift")
    assert (first["photo_id"], first["via"]) == (items[0].photo_id, "ui")          # located again from the page
    assert first["record"]["tz_offset_ms"] == items[0].tz_offset_ms                 # the page's truth, not RPC's
    assert first["record"]["upload_ms"] == items[0].upload_ms
    assert (second["photo_id"], second["via"], second["rpc_calls"]) == (items[1].photo_id, "ui", 0)
    assert len(fake_photos.rpc_log) == sent                                         # RPC stays off for the run
    out = capsys.readouterr().out
    assert out.count("Google Photos' internal answers disagree with the item page") == 1
    assert "tz_offset_ms" in out and "upload_ms" in out


@pytest.mark.parametrize("path", ["rpc", "ui"])
def test_a_chunk_set_shares_one_taken_second_lookup(fake_photos, tmp_path, path):
    # All chunks of one split share their DateUTC second (RESEARCH F4): one lookup serves the set, and each item is
    # opened at most once (the record cache) — every chunk still picked by its own dedupKey, never by position.
    stem, sid, taken = "Some.Film.2021", "k1k1k1", "2025-11-02T21:04:13Z"
    datas = [_bytes(f"chunk{n}") for n in (1, 2, 3)]
    items = [fake_photos.add_item("movies", f"{stem} [{sid}].chunk.00{n}.mkv", data=d, taken=taken)
             for n, d in enumerate(datas, 1)]
    fake_photos.add_item("movies", "Another.Film [x0x0x0].chunk.001.mkv", taken=taken)   # same second, other film
    if path == "ui":
        fake_photos.wiz_token_missing["movies"] = True
    blocks = [mvcommon.new_gphotos_block(_sha256(d), sha1=_sha1(d), date_utc=taken, pushed={"device_tz_offset_min": 480},
                                         accounts={"movies": {"approach": {"best": "taken_second",
                                                                           "ok": ["taken_second"]}}}) for d in datas]
    entry = _split(_folder(tmp_path, "Film"), f"{stem}.mkv", sid, datas, blocks)
    ctx, d = _ctx("movies"), fake_photos.driver("movies")

    results = [mainfetch.locate_object(d, loc, ctx) for loc in _locators(entry, "mov-en-2021-somefilm", ctx, tmp_path)]

    assert [r["photo_id"] for r in results] == [i.photo_id for i in items]
    assert {(r["stage"], r["via"]) for r in results} == {("s1_taken_second", path)}
    if path == "rpc":
        assert fake_photos.rpc_ids_sent() == ["lcxiM"]                   # one timeline page for the whole set
        assert [r["pages_opened"] for r in results] == [1, 0, 0]         # the run's drift check only
    else:
        assert [r["searches"] for r in results] == [1, 0, 0]             # one day search for the whole set
        assert [r["pages_opened"] for r in results] == [1, 1, 1]         # each chunk's own page, once


def test_locate_object_never_downloads_clicks_or_writes(fake_photos, tmp_path):
    data = _bytes("ro")
    item = fake_photos.add_item("tv", _up(SHOW, SID), data=data, taken=TAKEN)
    legacy = fake_photos.add_item("tv", _up(LIVE_NAME, "0003a8"), taken=TAKEN)
    folder = _folder(tmp_path)
    ctx, d = _ctx(), fake_photos.driver("tv")
    locs = _locators(_whole(folder, SHOW, data, SID, block=_learned(item)), EP, ctx, tmp_path) + \
        _locators(_whole(folder, LIVE_NAME, _bytes("dummy"), "0003a8"), "tv-en-2015-valleyshow-s02e02", ctx, tmp_path)
    before = _tree(tmp_path)

    results = [mainfetch.locate_object(d, loc, ctx) for loc in locs]

    assert [r["photo_id"] for r in results] == [item.photo_id, legacy.photo_id]
    assert fake_photos.downloads == []                                   # nothing downloaded
    assert "pLFTfd" not in fake_photos.rpc_ids_sent()                    # no download link asked for (item info's
    #                                                                      own link is dropped by gprpc at once)
    assert not [a for a in d.actions if a["kind"] in ("shift_d", "open")]   # no Shift+D, no tile clicked
    assert _tree(tmp_path) == before                                     # no file written anywhere
    assert mvcommon.read_learned() == [] and mvcommon.read_failed() == []
    _assert_read_only_rpc(fake_photos)


@pytest.mark.parametrize("rpc", [True, False], ids=["rpc", "web-pages"])
def test_a_signed_out_profile_raises_session_expired(fake_photos, tmp_path, rpc):
    item = fake_photos.add_item("tv", _up(SHOW, SID), data=_bytes("x"), taken=TAKEN)
    fake_photos.session["tv"] = "expired"
    fake_photos.wiz_token_missing["tv"] = not rpc
    ctx = _ctx()
    [loc] = _locators(_whole(_folder(tmp_path), SHOW, item.data, SID, block=_learned(item)), EP, ctx, tmp_path)

    with pytest.raises(mainfetch.SessionExpiredError):
        mainfetch.locate_object(fake_photos.driver("tv"), loc, ctx)


def test_a_stand_in_driver_locates_nothing_and_today_flow_is_untouched(tmp_path):
    ctx = _ctx()
    [loc] = _locators(_whole(_folder(tmp_path), SHOW, _bytes("x"), SID), EP, ctx, tmp_path)
    res = mainfetch.locate_object(None, loc, ctx)
    assert (res["found"], res["per_approach"], res["rpc_calls"], res["pages_opened"]) == (False, {}, 0, 0)
    queue = [{"filename": "x.mkv", "hash": "h", "status": "pending", "locator": loc}]
    assert mainfetch._run_locator_phase(None, queue, ctx) is None and queue[0]["status"] == "pending"


def test_approaches_all_evaluates_every_stage_for_check_photos(fake_photos, tmp_path):
    # PLAN Step 13 reuses locate_object: `--approaches all` needs every stage's verdict, the pick unchanged.
    data = _bytes("all")
    item = fake_photos.add_item("tv", _up(SHOW, SID), data=data, taken=TAKEN)
    block = _learned(item, date_utc=TAKEN)
    ctx = _ctx()
    [loc] = _locators(_whole(_folder(tmp_path), SHOW, data, SID, block=block), EP, ctx, tmp_path)

    res = mainfetch.locate_object(fake_photos.driver("tv"), loc, ctx, approaches="all")

    assert (res["photo_id"], res["stage"]) == (item.photo_id, "s0_photo_id")
    assert res["per_approach"] == {
        "photo_id": {"outcome": "exactly-one", "n": 1},
        "hash": {"outcome": "exactly-one", "n": 1},
        "taken_second": {"outcome": "exactly-one", "n": 1},
        "exact_name": {"outcome": "exactly-one", "n": 1},
        "title_sid": {"outcome": "exactly-one", "n": 1},      # the title: nothing; the bare short id: it
        "legacy": {"outcome": "exactly-one", "n": 1}}
    assert res["per_approach_via"] == dict.fromkeys(res["per_approach"], "rpc")
    _assert_read_only_rpc(fake_photos)


# ===========================================================================
# 12C.b1 — the triggers: (ii) the direct download, then (i) Shift+D (+ the click path)
# ===========================================================================
def _armed(fake_photos, tmp_path, account="tv", **world):
    """One located whole episode in a session whose download folder is armed (Step 11's _prepare_downloads):
    -> (ctx, driver, item, fake item, incoming folder)."""
    for knob, value in world.items():
        setattr(fake_photos, knob, value)
    data = _bytes("trigger")
    fake = fake_photos.add_item(account, _up(SHOW, SID), data=data, taken=TAKEN)
    entry = _whole(_folder(tmp_path), SHOW, data, SID, block=_learned(fake, account=account))
    ctx, d = _ctx(account), fake_photos.driver(account)
    [item] = mainfetch.build_fetch_queue(entry, os.path.join(entry["folder_path"], "restore"), ctx, EP)
    assert mainfetch._prepare_downloads(d, [item], ctx) is True and ctx.target["mode"] == "incoming"
    item["_located"] = mainfetch.locate_object(d, item["locator"], ctx)
    assert item["_located"]["found"]
    return ctx, d, item, fake, ctx.target["dir"]


def _files(folder):
    return sorted(n for n in os.listdir(folder) if n != mainfetch.INCOMING_MARKER)


def test_a_download_counts_as_started_only_when_it_was_requested_after_the_look(tmp_path):
    folder = tmp_path / "incoming"
    folder.mkdir()
    ctx = mainfetch.FetchContext()
    ctx.target = {"dir": str(folder)}
    assert mainfetch._download_started(ctx, set()) is False                # nothing came within TRIGGER_CONFIRM_S
    (folder / "a.mkv.crdownload").write_bytes(b"x")
    assert mainfetch._download_started(ctx, set()) is True                 # a new partial
    before = mainfetch._watched_now(ctx)
    (folder / "a.mkv.crdownload").rename(folder / "a.mkv")
    assert mainfetch._download_started(ctx, before) is False               # an EARLIER download finishing
    (folder / "b.mkv").write_bytes(b"y")
    assert mainfetch._download_started(ctx, before) is True                # a small new file, finished between looks


def test_the_direct_download_lands_in_the_entry_folder_without_showing_its_link(fake_photos, tmp_path, capsys):
    ctx, d, item, fake, incoming = _armed(fake_photos, tmp_path)

    assert mainfetch._direct_trigger(d, item, ctx) is True

    assert _files(incoming) == [fake.filename]                             # P-12: straight into .mv_incoming
    assert [r["via"] for r in fake_photos.downloads] == ["direct"]
    out = capsys.readouterr().out
    assert f"     ⬇️ {item['filename']}: direct download" in out
    assert gphotos_fake.DOWNLOAD_HOST not in out and not any(t in out for t in fake_photos.minted)
    assert mainfetch._is_photos_host(gpweb_host(d.current_url))            # the tab never stays on a link


def gpweb_host(url):
    import urllib.parse
    return urllib.parse.urlparse(url).hostname


def test_a_link_answering_with_html_starts_nothing_and_the_tab_goes_back_to_photos(fake_photos, tmp_path, capsys):
    ctx, d, item, _fake, incoming = _armed(fake_photos, tmp_path, direct_mode="html")

    assert mainfetch._direct_trigger(d, item, ctx) is False

    assert _files(incoming) == [] and fake_photos.downloads == []
    assert mainfetch._is_photos_host(gpweb_host(d.current_url))            # never left on the link's page (P-20)
    assert ("the direct download did not start — its link answered with a web page — Shift+D next."
            in capsys.readouterr().out)


def test_a_refused_mint_is_no_direct_download(fake_photos, tmp_path, capsys):
    ctx, d, item, _fake, incoming = _armed(fake_photos, tmp_path, vrseub_original_url=False, mint_refused=True)

    assert mainfetch._direct_trigger(d, item, ctx) is False

    assert "pLFTfd" in fake_photos.rpc_ids_sent() and _files(incoming) == []
    assert "no download link was minted — Shift+D next." in capsys.readouterr().out


def test_no_direct_download_is_tried_while_rpc_is_off(fake_photos, tmp_path):
    ctx, d, item, _fake, _incoming = _armed(fake_photos, tmp_path)
    mainfetch._locator_run(ctx).rpc_off = "drift"
    sent = len(fake_photos.rpc_log)

    assert mainfetch._direct_trigger(d, item, ctx) is False
    assert len(fake_photos.rpc_log) == sent


def test_opening_the_link_bounds_the_page_load_and_restores_it(fake_photos, tmp_path):
    # A navigation that becomes a download may never "load": Selenium's 300 s default hung a live probe ~5 min.
    ctx, d, item, _fake, _incoming = _armed(fake_photos, tmp_path)
    timeouts = []
    d.timeouts = type("T", (), {"page_load": 120.0})()
    d.set_page_load_timeout = timeouts.append

    assert mainfetch._direct_trigger(d, item, ctx) is True
    assert timeouts == [mainfetch.DIRECT_PAGE_LOAD_S, 120.0]


def test_shift_d_on_the_located_items_page(fake_photos, tmp_path, capsys):
    ctx, d, item, fake, incoming = _armed(fake_photos, tmp_path)

    assert mainfetch._shift_d_trigger(d, item, ctx) is True

    assert _files(incoming) == [fake.filename]
    assert [(a["photo_id"], a["downloaded"]) for a in d.actions if a["kind"] == "shift_d"] == [(fake.photo_id, True)]
    assert f"     ⬇️ {item['filename']}: Shift+D on its item page" in capsys.readouterr().out


def test_shift_d_ignored_on_a_direct_page_load_opens_the_item_from_its_results(fake_photos, tmp_path, capsys):
    ctx, d, item, fake, incoming = _armed(fake_photos, tmp_path, shift_d_requires_click=True)

    assert mainfetch._shift_d_trigger(d, item, ctx) is True

    assert _files(incoming) == [fake.filename]
    assert [a["photo_id"] for a in d.actions if a["kind"] == "open"] == [fake.photo_id]   # the tile naming it
    assert [a["downloaded"] for a in d.actions if a["kind"] == "shift_d"] == [False, True]
    assert "Shift+D after opening it from its search results" in capsys.readouterr().out


def test_shift_d_is_never_pressed_on_a_neighbours_page(fake_photos, tmp_path):
    # An item page embeds its NEIGHBOURS' records too: the located item's record in the source proves nothing about
    # which item is open — the page's own address must name it.
    ctx, d, item, fake, _incoming = _armed(fake_photos, tmp_path)
    neighbour = fake_photos.add_item("tv", "Other.Show.S09E09 [o9o9o9].mkv", taken=TAKEN)
    d.get(f"{gphotos_fake.PHOTOS_URL}/photo/{neighbour.photo_id}")
    assert gpweb.page_record(d.page_source, fake.photo_id)            # the located item's record IS in this page …

    assert mainfetch._shows_item(d, fake.photo_id, fake.dedup_key) is False   # … yet this page shows another item
    assert mainfetch._shows_item(d, neighbour.photo_id, None) is True


def test_the_click_path_opens_the_located_item_not_the_first_result(fake_photos, tmp_path):
    # The results list another item FIRST (live: unrelated items lead) — the click path clicks the tile naming the
    # located photo_id, never a position.
    ctx, d, item, fake, incoming = _armed(fake_photos, tmp_path, shift_d_requires_click=True)
    first = fake_photos.add_item("tv", "Other.Show.S09E09 [o9o9o9].mkv", taken=TAKEN)
    fake_photos.script_search("tv", fake.filename, [first.photo_id, fake.photo_id])

    assert mainfetch._shift_d_trigger(d, item, ctx) is True

    assert [a["photo_id"] for a in d.actions if a["kind"] == "open"] == [fake.photo_id]
    assert _files(incoming) == [fake.filename]


def _with_strip(d, strip_tiles):
    """Google Photos' "Most relevant to your search" strip on the fake's search pages (live 2026-10-05): the first
    `strip_tiles` result tiles of a search sit in the strip — gpweb's shared page scripts (merged 8dacbad) count
    them as `relevant`, not as results, and mark them never clickable (TIMELINE_LINKS_JS)."""
    real = d.execute_script

    def execute_script(script, *args):
        if script == gpweb.TIMELINE_LINKS_JS:
            return [True if n < strip_tiles else verdict for n, verdict in enumerate(real(script, *args))]
        if script == gpweb.SEARCH_STATE_JS:
            state = real(script)
            moved = min(strip_tiles, state["results"])
            return dict(state, results=state["results"] - moved, relevant=state.get("relevant", 0) + moved)
        return real(script, *args)

    d.execute_script = execute_script


def _strip_world(fake_photos, tmp_path):
    """The live page: the exact name's search shows the strip [another episode, a chunk, the right item] above the
    date group holding the right item again (TV account, 2026-10-05)."""
    right = fake_photos.add_item("tv", _up(LIVE_NAME, "0003a8"), taken=TAKEN)
    space = fake_photos.add_item("tv", "Space.Show.S01E02.The.Big.Empty [bd42e9].mkv", taken="2020-04-30T08:15:20Z")
    robot = fake_photos.add_item("tv", "Hacker.Show.S02E12 [a08d87].chunk.002.mkv", taken="2026-02-04T10:44:51Z")
    fake_photos.script_search("tv", _up(LIVE_NAME, "0003a8"),
                              [space.photo_id, robot.photo_id, right.photo_id, right.photo_id])
    ctx, d = _ctx(), fake_photos.driver("tv")
    _with_strip(d, 3)
    [loc] = _locators(_whole(_folder(tmp_path), LIVE_NAME, _bytes("dummy"), "0003a8"),
                      "tv-en-2015-valleyshow-s02e02", ctx, tmp_path)
    return ctx, d, loc, right, (space, robot)


def test_a_most_relevant_strip_tile_is_never_a_candidate_on_the_web_pages(fake_photos, tmp_path):
    ctx, d, loc, right, strip = _strip_world(fake_photos, tmp_path)
    fake_photos.wiz_token_missing["tv"] = True       # the web-page path decides

    res = mainfetch.locate_object(d, loc, ctx)

    assert res["found"] and res["photo_id"] == right.photo_id and res["via"] == "ui"
    assert res["per_approach"]["exact_name"] == {"outcome": "exactly-one", "n": 1}   # the date group's
    assert res["per_approach_via"]["exact_name"] == "ui"
    assert not set(res["candidates"]) & {s.photo_id for s in strip}                     # strip tiles never listed


def test_a_search_showing_only_the_strip_is_a_miss(fake_photos, tmp_path):
    # Live: the untagged name's search showed ONLY the strip (another episode first) — nothing to verify or click.
    space = fake_photos.add_item("tv", "Space.Show.S01E02.The.Big.Empty [bd42e9].mkv", taken="2020-04-30T08:15:20Z")
    fake_photos.script_search("tv", _up(LIVE_NAME, "0003a8"), [space.photo_id])
    fake_photos.wiz_token_missing["tv"] = True
    ctx, d = _ctx(), fake_photos.driver("tv")
    _with_strip(d, 1)
    [loc] = _locators(_whole(_folder(tmp_path), LIVE_NAME, _bytes("dummy"), "0003a8"),
                      "tv-en-2015-valleyshow-s02e02", ctx, tmp_path)

    res = mainfetch.locate_object(d, loc, ctx)

    assert not res["found"] and res["per_approach"]["exact_name"] == {"outcome": "not-found", "n": 0}
    assert res["per_approach_via"]["exact_name"] == "ui"
    assert res["candidates_seen"] == 0 and res["pages_opened"] == 0


def test_the_click_path_never_clicks_a_strip_tile(fake_photos, tmp_path, monkeypatch):
    ctx, d, loc, right, strip = _strip_world(fake_photos, tmp_path)
    fake_photos.shift_d_requires_click = True
    [item] = mainfetch.build_fetch_queue(_whole(_folder(tmp_path), LIVE_NAME, _bytes("dummy"), "0003a8"),
                                         os.path.join(str(tmp_path / "Show"), "restore"), ctx,
                                         "tv-en-2015-valleyshow-s02e02")
    assert mainfetch._prepare_downloads(d, [item], ctx) is True
    item["_located"] = mainfetch.locate_object(d, item["locator"], ctx)
    clicked = []
    real_click = d._click
    monkeypatch.setattr(d, "_click", lambda el: clicked.append(el.attrs.get("href")) or real_click(el))

    assert mainfetch._shift_d_trigger(d, item, ctx) is True

    assert len(clicked) == 1 and clicked[0].endswith(f"/photo/{right.photo_id}")   # the date group's tile
    assert [a["photo_id"] for a in d.actions if a["kind"] == "open"] == [right.photo_id]


def test_shift_d_is_never_pressed_on_a_page_that_does_not_show_the_located_item(fake_photos, tmp_path, capsys):
    ctx, d, item, fake, incoming = _armed(fake_photos, tmp_path)
    fake.deleted = True                                 # gone between locating and triggering

    assert mainfetch._shift_d_trigger(d, item, ctx) is False

    assert [a for a in d.actions if a["kind"] in ("shift_d", "open")] == []
    assert _files(incoming) == [] and "Shift+D started no download." in capsys.readouterr().out


# ===========================================================================
# 12C.b2 — the fetch's exact-item phase (_run_locator_phase) through cmd_fetch_route / fetch_single_entry
# ===========================================================================
WEB_MARKERS = ("🔹 PROCESSING:", "Detected Split File", "✅ MOVED:", "✅ ENTRY COMPLETE.")   # PLAN P-13
SEASON = "tv-en-2015-demo-s01"


class _Disk:
    free, total, used = 10 ** 15, 2 * 10 ** 15, 10 ** 15


@pytest.fixture()
def flow(fake_photos, monkeypatch):
    """A fetch run on the fake world: init_driver hands out one fake driver per account session (no Chrome); no lock
    file, no socket, plenty of disk (P-12's pre-flight); today's trigger_download is a recorder that requests nothing
    (S4 — what reaches it, a test reads)."""
    drivers, legacy = {}, []

    def init(profile_key="movies"):
        drivers[profile_key] = fake_photos.driver(profile_key)
        return drivers[profile_key]

    monkeypatch.setattr(mainfetch, "init_driver", init)
    monkeypatch.setattr(mainfetch, "fetch_session_lock", lambda *a, **k: contextlib.nullcontext())
    monkeypatch.setattr(mainfetch, "_debug_port_free", lambda *a, **k: True)
    monkeypatch.setattr(mainfetch.shutil, "disk_usage", lambda path: _Disk())
    monkeypatch.setattr(mainfetch, "trigger_download",
                        lambda driver, query, index=0: legacy.append((query, index)) or False)
    return types.SimpleNamespace(world=fake_photos, drivers=drivers, legacy=legacy)


def _season(sandbox, world, episodes, block=lambda item, data: _learned(item)):
    """A season_map SEASON whose episode n holds _bytes(f'ep{n}') and lives in the TV account under its uploaded name;
    -> (folder, {episode id: fake item})."""
    folder = sandbox["local_root"] / "Series" / "Demo" / "Season 01"
    folder.mkdir(parents=True, exist_ok=True)
    lib, items = {SEASON: {"type": "season_map", "folder_path": str(folder), "total_episodes": len(episodes),
                           "children": [f"{SEASON}e0{n}" for n in episodes]}}, {}
    for n in episodes:
        data, name, sid = _bytes(f"ep{n}"), f"Demo.S01E0{n}.mkv", f"de{n}de{n}"
        item = world.add_item("tv", _up(name, sid), data=data, taken=TAKEN)
        entry = _whole(folder, name, data, sid, block=block(item, data) if block else None)
        lib[f"{SEASON}e0{n}"] = dict(entry, parent_id=SEASON)
        items[f"{SEASON}e0{n}"] = item
    mvcommon.save_library(lib)
    return folder, items


def _journal_objects():
    [path] = [os.path.join(mainfetch.FETCH_JOURNAL_DIR, n) for n in os.listdir(mainfetch.FETCH_JOURNAL_DIR)]
    with open(path, encoding="utf-8") as fh:
        return [o for e in json.load(fh)["entries"] for o in e["objects"]]


def test_fast_path_a_season_with_learned_photo_ids_downloads_directly_with_zero_searches(flow, sandbox, capsys):
    folder, items = _season(sandbox, flow.world, (1, 2))

    mainfetch.cmd_fetch_route(SEASON)

    out = capsys.readouterr().out
    assert sorted(p.name for p in (folder / "restore").iterdir()) == ["Demo.S01E01.mkv", "Demo.S01E02.mkv"]
    assert flow.legacy == [] and "ATTEMPT 1" not in out                     # today's attempts never ran
    assert "EzkLib" not in flow.world.rpc_ids_sent()                        # zero searches: no RPC search …
    assert not [a for a in flow.drivers["tv"].actions if a["kind"] == "search"]   # … and no search typed
    assert [r["via"] for r in flow.world.downloads] == ["direct", "direct"]
    assert out.count("✅ ENTRY COMPLETE.") == 2 and out.count("     ⬇️ ") == 2
    learned = mvcommon.read_learned("tv")
    assert sorted((r["photo_id"], r["proof"], r["approach"]["tried"]) for r in learned) == sorted(
        (i.photo_id, "sha256", "s0_photo_id") for i in items.values())
    assert [o["method"] for o in _journal_objects()] == ["direct", "direct"]
    web = pytest.importorskip("webui.server")
    assert web._parse_progress(out) == {"done": 2, "total": 2}             # P-13: the web progress is unchanged
    for line in out.splitlines():
        if "EXACT ITEM" in line or "🎯" in line or "⬇️" in line:
            assert not any(marker in line for marker in WEB_MARKERS)
    _assert_read_only_rpc(flow.world)


def test_a_same_second_batch_of_ten_downloads_only_the_right_episode(flow, sandbox):
    # RESEARCH F13: a release batch shares one second. The episode is picked by its dedupKey among the ten; the
    # other nine are never requested.
    def by_second(item, data):
        return mvcommon.new_gphotos_block(_sha256(data), sha1=_sha1(data), date_utc=TAKEN, accounts={
            "tv": {"approach": {"best": "taken_second", "ok": ["taken_second"]}}})

    folder, items = _season(sandbox, flow.world, range(1, 10), block=None)   # nine siblings at the second, no keys
    target_data = _bytes("ep-target")
    target = flow.world.add_item("tv", _up("Demo.S01E10.mkv", "de0de0"), data=target_data, taken=TAKEN)
    lib = mvcommon.load_library()
    lib[f"{SEASON}e10"] = dict(_whole(folder, "Demo.S01E10.mkv", target_data, "de0de0",
                                      block=by_second(target, target_data)), parent_id=SEASON)
    mvcommon.save_library(lib)

    mainfetch.cmd_fetch_route(f"{SEASON}e10")

    assert [(r["photo_id"], r["via"]) for r in flow.world.downloads] == [(target.photo_id, "direct")]
    assert (folder / "restore" / "Demo.S01E10.mkv").read_bytes() == target_data
    assert flow.world.rpc_ids_sent().count("lcxiM") == 1 and flow.legacy == []


def test_a_wrong_account_object_is_fetched_in_its_home_account(flow, sandbox, capsys):
    # The 31 X-Files episodes live in the MOVIES account: a block that verified the copy there routes the episode to
    # the movies session (P-4) and the locator looks for it there.
    folder = sandbox["local_root"] / "Series" / "Files" / "Season 02"
    folder.mkdir(parents=True)
    data = _bytes("xfile")
    item = flow.world.add_item("movies", _up("Files.S02E05.mkv", "x2x5x2"), data=data, taken="1994-10-28T02:00:00Z")
    episode = "tv-en-1994-files-s02e05"
    mvcommon.save_library({episode: _whole(folder, "Files.S02E05.mkv", data, "x2x5x2",
                                           block=_learned(item, account="movies", proof="filename"))})

    mainfetch.cmd_fetch_route(episode)

    out = capsys.readouterr().out
    assert list(flow.drivers) == ["movies"] and "Switching to profile 'movies'" in out
    assert [(r["account"], r["photo_id"]) for r in flow.world.downloads] == [("movies", item.photo_id)]
    assert (folder / "restore" / "Files.S02E05.mkv").read_bytes() == data and flow.legacy == []


def test_a_partly_staged_chunk_set_fetches_only_its_missing_chunks_in_one_hash_batch(flow, sandbox):
    folder = sandbox["local_root"] / "Movies" / "Film (2020)"
    folder.mkdir(parents=True)
    stem, sid = "Film.2020", "f2f2f2"
    datas = [_bytes(f"film-chunk{n}") for n in (1, 2, 3)]
    items = [flow.world.add_item("movies", f"{stem} [{sid}].chunk.00{n}.mkv", data=d, taken="2026-01-31T00:29:27Z")
             for n, d in enumerate(datas, 1)]
    _capture(folder, sid, [_obj(d, f"{stem} [{sid}].chunk.00{n}.mkv", role="chunk", index=n,
                                date_utc="2026-01-31T00:29:27Z") for n, d in enumerate(datas, 1)])
    (folder / "restore").mkdir()
    (folder / "restore" / f"{stem} [{sid}].chunk.001.mkv").write_bytes(datas[0])   # chunk 1 is already here
    mvcommon.save_library({"mov-en-2020-film": _split(folder, f"{stem}.mkv", sid, datas)})

    mainfetch.cmd_fetch_route("mov-en-2020-film")

    assert sorted(r["photo_id"] for r in flow.world.downloads) == sorted([items[1].photo_id, items[2].photo_id])
    assert flow.world.rpc_ids_sent().count("swbisb") == 1            # both missing chunks' keys in ONE lookup
    for n, data in enumerate(datas, 1):
        assert (folder / "restore" / f"{stem} [{sid}].chunk.00{n}.mkv").read_bytes() == data
    assert flow.legacy == []


def test_a_chunk_set_downloads_with_at_most_three_direct_downloads_at_once(flow, sandbox, monkeypatch):
    # P-20: DIRECT_DOWNLOAD_CONCURRENCY = 3. With transfers that run until the harvester looks ("manual"), the
    # incoming folder never holds more than three partials: a 5-chunk set goes 3 + 2.
    flow.world.download_mode = "manual"
    folder = sandbox["local_root"] / "Movies" / "Long (2019)"
    folder.mkdir(parents=True)
    stem, sid, taken = "Long.2019", "l5l5l5", "2025-05-05T05:05:05Z"
    datas = [_bytes(f"long{n}") for n in range(1, 6)]
    for n, d in enumerate(datas, 1):
        flow.world.add_item("movies", f"{stem} [{sid}].chunk.00{n}.mkv", data=d, taken=taken)
    _capture(folder, sid, [_obj(d, f"{stem} [{sid}].chunk.00{n}.mkv", role="chunk", index=n, date_utc=taken)
                           for n, d in enumerate(datas, 1)])
    mvcommon.save_library({"mov-en-2019-long": _split(folder, f"{stem}.mkv", sid, datas)})
    running, real = [], mainfetch.harvest_downloads

    def harvest(queue, ctx):
        running.append(sum(1 for n in os.listdir(ctx.target["dir"]) if n.endswith(".crdownload")))
        flow.world.finish_downloads()
        return real(queue, ctx)

    monkeypatch.setattr(mainfetch, "harvest_downloads", harvest)

    mainfetch.cmd_fetch_route("mov-en-2019-long")

    assert running == [3, 2]
    assert [r["via"] for r in flow.world.downloads] == ["direct"] * 5
    for n, data in enumerate(datas, 1):
        assert (folder / "restore" / f"{stem} [{sid}].chunk.00{n}.mkv").read_bytes() == data


def _one_episode(flow, sandbox, **world):
    for knob, value in world.items():
        setattr(flow.world, knob, value)
    folder, items = _season(sandbox, flow.world, (1,))
    return folder, items[f"{SEASON}e01"], f"{SEASON}e01"


def test_a_minted_link_answering_html_falls_to_shift_d(flow, sandbox, capsys):
    folder, item, episode = _one_episode(flow, sandbox, direct_mode="html")

    mainfetch.cmd_fetch_route(episode)

    out = capsys.readouterr().out
    assert out.index("the direct download did not start") < out.index("Shift+D on its item page")
    assert (folder / "restore" / "Demo.S01E01.mkv").read_bytes() == item.data
    assert [r["via"] for r in flow.world.downloads] == ["shift_d"]
    assert [o["method"] for o in _journal_objects()] == ["shift_d"]
    assert "✅ ENTRY COMPLETE." in out and flow.legacy == []


def test_a_link_serving_other_bytes_is_deleted_and_shift_d_fetches_the_original(flow, sandbox, capsys):
    # P14: a minted link might serve a transcode. SHA-256 rejects it, Shift+D fetches the original — no wrong-item
    # row (the item was verified) — and the run stops minting links: the next episode goes straight to Shift+D.
    folder, items = _season(sandbox, flow.world, (1, 2))
    flow.world.direct_mode = "wrong_bytes"

    mainfetch.cmd_fetch_route(SEASON)

    out = capsys.readouterr().out
    assert [(r["photo_id"], r["via"]) for r in flow.world.downloads] == [
        (items[f"{SEASON}e01"].photo_id, "direct"), (items[f"{SEASON}e01"].photo_id, "shift_d"),
        (items[f"{SEASON}e02"].photo_id, "shift_d")]
    for n in (1, 2):
        assert (folder / "restore" / f"Demo.S01E0{n}.mkv").read_bytes() == items[f"{SEASON}e0{n}"].data
    assert "Wrong item downloaded (deleted)" in out and "its direct link served other bytes" in out
    assert out.count("direct downloads are off for the rest of this run") == 1
    assert mvcommon.read_failed() == [] and out.count("✅ ENTRY COMPLETE.") == 2


def test_a_verified_items_other_bytes_are_never_booked_as_a_wrong_pick(flow, sandbox, capsys):
    # The direct link served other bytes (P14) and Shift+D then started nothing: the object ends unfetched — but it
    # WAS verified, so no "the search picked another item" row (the round's `_wrong` mark is cleared); a re-run
    # fetches it.
    folder, item, episode = _one_episode(flow, sandbox, direct_mode="wrong_bytes", shift_d_requires_click=True)
    for query in (item.filename, "2022-02-05"):                        # no search lists it: no click path either
        flow.world.script_search("tv", query, [])

    mainfetch.cmd_fetch_route(episode)

    out = capsys.readouterr().out
    assert "its direct link served other bytes" in out and "❌ ENTRY INCOMPLETE." in out
    assert "the search picked another item" not in out and mvcommon.read_failed() == []


def test_a_refused_mint_falls_to_shift_d(flow, sandbox, capsys):
    folder, item, episode = _one_episode(flow, sandbox, vrseub_original_url=False, mint_refused=True)

    mainfetch.cmd_fetch_route(episode)

    out = capsys.readouterr().out
    assert "no download link was minted — Shift+D next." in out and "Shift+D on its item page" in out
    assert [r["via"] for r in flow.world.downloads] == ["shift_d"]
    assert (folder / "restore" / "Demo.S01E01.mkv").read_bytes() == item.data


def test_shift_d_ignored_on_a_direct_page_load_falls_to_the_click_path(flow, sandbox, capsys):
    folder, item, episode = _one_episode(flow, sandbox, vrseub_original_url=False, mint_refused=True,
                                         shift_d_requires_click=True)

    mainfetch.cmd_fetch_route(episode)

    out = capsys.readouterr().out
    assert "Shift+D after opening it from its search results" in out
    assert [a["photo_id"] for a in flow.drivers["tv"].actions if a["kind"] == "open"] == [item.photo_id]
    assert (folder / "restore" / "Demo.S01E01.mkv").read_bytes() == item.data and flow.legacy == []


def _episode_folder(sandbox):
    folder = sandbox["local_root"] / "Series" / "Demo" / "Season 04"
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def test_nothing_verifiable_triggers_nothing_skips_todays_search_and_lands_on_the_failed_list(flow, sandbox, capsys):
    # OD-5: a content-keyed object whose every candidate is proven another item — no request in S0–S3u, and today's
    # attempts (which could only fetch those wrong items) are skipped: failed, one s4-skipped row.
    folder, name, sid = _episode_folder(sandbox), "Demo.S04E01.mkv", "l4l4l4"
    data = _bytes("the-real-bytes")
    flow.world.add_item("tv", _up(name, sid), data=_bytes("an-older-upload"), taken=TAKEN)
    flow.world.add_item("tv", name, data=_bytes("someone-else"), taken=TAKEN)
    _capture(folder, sid, [_obj(data, _up(name, sid))])
    mvcommon.save_library({"tv-en-2015-demo-s04e01": _whole(folder, name, data, sid)})

    mainfetch.cmd_fetch_route("tv-en-2015-demo-s04e01")

    out = capsys.readouterr().out
    assert flow.world.downloads == [] and flow.legacy == []                  # nothing requested, no S4
    assert "ATTEMPT 1" not in out and "❌ ENTRY INCOMPLETE." in out
    assert "today's search is skipped (OD-5); added to the failed list." in out
    [row] = mvcommon.read_failed()
    assert (row["reason"], row["manual_id"], row["account"]) == ("s4-skipped", "tv-en-2015-demo-s04e01", "tv")
    assert row["expected_taken_local"] == "2022-02-05 19:49:46 GMT+08:00"
    assert [t["approach"] for t in row["tried"]] == ["hash", "taken_second", "exact_name", "title_sid", "legacy"]
    assert len(row["candidates"]) == 2 and row["sha256"] == _sha256(data)


def test_a_stale_photo_id_is_recorded_stale_and_the_reupload_is_fetched(flow, sandbox):
    folder, name, sid = _episode_folder(sandbox), "Demo.S04E02.mkv", "l4l4l5"
    data = _bytes("reuploaded-episode")
    dead = flow.world.add_item("tv", _up(name, sid), data=data, taken=TAKEN, deleted=True)
    alive = flow.world.add_item("tv", _up(name, sid), data=data, taken=TAKEN)
    mvcommon.save_library({"tv-en-2015-demo-s04e02": _whole(folder, name, data, sid, block=_learned(dead))})

    mainfetch.cmd_fetch_route("tv-en-2015-demo-s04e02")

    assert (folder / "restore" / name).read_bytes() == data
    learned = mvcommon.read_learned("tv")
    assert [(r["photo_id"], r["approach"]["outcome"]) for r in learned if r.get("stale")] == [(dead.photo_id, "not-found")]
    [fetched] = [r for r in learned if not r.get("stale")]
    assert (fetched["photo_id"], fetched["proof"], fetched["approach"]["tried"]) == (alive.photo_id, "sha256", "s0_hash")
    reduced = mvcommon.reduce_learned(learned)[("tv", _sha256(data))]
    assert reduced["photo_id"] == alive.photo_id and dead.photo_id in reduced["dead"]   # the apply engine's view


def test_a_signed_out_profile_stops_the_batch_with_its_remediation(flow, sandbox, capsys):
    folder, items = _season(sandbox, flow.world, (1, 2))
    flow.world.session["tv"] = "expired"

    mainfetch.cmd_fetch_route(SEASON)

    out = capsys.readouterr().out
    assert "❌ Profile 'tv' is logged out." in out and f"--user-data-dir={mainfetch.CHROME_PROFILES['tv']}" in out
    assert flow.world.downloads == [] and flow.legacy == [] and "Batch Processing Complete" not in out


def test_the_live_wrong_click_case_is_fetched_by_its_exact_name_and_todays_search_never_runs(flow, sandbox, capsys):
    # 2026-10-05: `fetch tv-en-2014-siliconvalley-s02e02` clicked The Expanse (index 0 of the exact name's results)
    # twice; the hash check deleted it and the entry ended INCOMPLETE. A legacy object (no key, no photo_id) is now
    # pinned by its record's exact name among the listed candidates, then downloaded directly.
    folder, sid = _episode_folder(sandbox), "0003a8"
    data = _bytes("valley-s02e02-original")
    right = flow.world.add_item("tv", _up(LIVE_NAME, sid), data=data, taken=TAKEN)
    space = flow.world.add_item("tv", "Space.Show.S01E02.The.Big.Empty [bd42e9].mkv", data=_bytes("5.39 GB of space"),
                                taken="2020-04-30T08:15:20Z")
    flow.world.script_search("tv", _up(LIVE_NAME, sid), [space.photo_id, right.photo_id])
    mvcommon.save_library({"tv-en-2015-valleyshow-s02e02": _whole(folder, LIVE_NAME, data, sid)})

    mainfetch.cmd_fetch_route("tv-en-2015-valleyshow-s02e02")

    out = capsys.readouterr().out
    assert [(r["photo_id"], r["via"]) for r in flow.world.downloads] == [(right.photo_id, "direct")]   # never Space
    assert (folder / "restore" / LIVE_NAME).read_bytes() == data and flow.legacy == []
    assert "S2 exact_name · proof filename · via rpc" in out and "✅ ENTRY COMPLETE." in out
    [learned] = mvcommon.read_learned("tv")
    assert (learned["photo_id"], learned["proof"], learned["approach"]["tried"]) == (right.photo_id, "sha256",
                                                                                     "s2_exact_name")


# ===========================================================================
# 12C.b3 — the failed list after today's attempts, the session line, the kill switch
# ===========================================================================
def test_a_legacy_object_nothing_verifies_gets_todays_attempts_then_a_failed_row(flow, sandbox, capsys):
    # OD-5: an object without a content key keeps today's attempts (S4) — after them, still unfetched, it lands on
    # the failed list with what every stage saw (P-18).
    folder, name, sid = _episode_folder(sandbox), "Demo.S04E07.mkv", "l7l7l7"
    other = flow.world.add_item("tv", "Demo.S04E07.Extended [l9l9l9].mkv", taken=TAKEN)
    flow.world.script_search("tv", _up(name, sid), [other.photo_id])
    mvcommon.save_library({"tv-en-2015-demo-s04e07": _whole(folder, name, _bytes("legacy"), sid)})

    mainfetch.cmd_fetch_route("tv-en-2015-demo-s04e07")

    out = capsys.readouterr().out
    assert flow.world.downloads == []                                       # nothing requested in S0–S3u
    assert flow.legacy == [(name, 0), (_up(name, sid), 0)]                  # today's two attempts, unchanged (S4)
    assert out.index("not located (exact_name wrong-item(1) · title_sid not-found(0))") < out.index("ATTEMPT 1")
    assert "Not fetched: Demo.S04E07.mkv — no stage found its exact item (wrong-item); added to the failed list." in out
    [row] = mvcommon.read_failed()
    assert (row["reason"], row["candidates"]) == ("wrong-item", [other.photo_id])
    assert row["tried"] == [{"approach": "exact_name", "outcome": "wrong-item", "n": 1},
                            {"approach": "title_sid", "outcome": "not-found", "n": 0}]


def test_each_account_session_ends_with_one_exact_item_line_naming_its_rpc_calls(flow, sandbox, capsys):
    folder, items = _season(sandbox, flow.world, (1, 2))

    mainfetch.cmd_fetch_route(SEASON)

    out = capsys.readouterr().out
    calls = gprpc.stats(flow.drivers["tv"])["calls"]
    assert out.count("   > 🔎 Exact item ('tv'): 2 of 2 object(s) located · 2 direct download(s) · 0 Shift+D · "
                     f"RPC calls: {calls} (errors 0)") == 1
    line = next(ln for ln in out.splitlines() if "🔎 Exact item" in ln)
    assert out.index(line) < out.index("Batch Processing Complete") and not any(m in line for m in WEB_MARKERS)


def test_the_kill_switch_restores_todays_sequence_exactly(flow, sandbox, capsys, monkeypatch):
    # P-14: EXACT_LOCATOR_ENABLED = False — no stage, no RPC, no exact-item line: today's two attempts as before (the
    # learning and hygiene layers stay on).
    monkeypatch.setattr(mainfetch, "EXACT_LOCATOR_ENABLED", False)
    folder, items = _season(sandbox, flow.world, (1,))

    mainfetch.cmd_fetch_route(f"{SEASON}e01")

    out = capsys.readouterr().out
    assert flow.legacy == [("Demo.S01E01.mkv", 0), ("Demo.S01E01 [de1de1].mkv", 0)]
    assert flow.world.rpc_log == [] and flow.world.downloads == []
    assert "EXACT ITEM" not in out and "🔎 Exact item" not in out and mvcommon.read_failed() == []
    assert "=== ATTEMPT 1 (1 files) ===" in out and "=== ATTEMPT 2 (1 files) ===" in out


# ===========================================================================
# 12C.c — `python mainfetch.py locate <id> [episodes <r>] [--fetchExtras] [--probe-direct]` (read-only)
# ===========================================================================
def test_locate_lists_every_object_and_never_downloads_clicks_or_writes(flow, sandbox, tmp_path, capsys):
    folder, items = _season(sandbox, flow.world, (1, 2))
    lost_data = _bytes("lost")
    flow.world.add_item("tv", _up("Demo.S01E03.mkv", "de3de3"), data=_bytes("other bytes"), taken=TAKEN)
    _capture(folder, "de3de3", [_obj(lost_data, _up("Demo.S01E03.mkv", "de3de3"))])
    lib = mvcommon.load_library()
    lib[SEASON]["children"].append(f"{SEASON}e03")
    lib[f"{SEASON}e03"] = dict(_whole(folder, "Demo.S01E03.mkv", lost_data, "de3de3"), parent_id=SEASON)
    mvcommon.save_library(lib)
    before = _tree(tmp_path)

    mainfetch.cmd_locate(SEASON)

    out = capsys.readouterr().out
    assert out.startswith(f"--- LOCATE: {SEASON} (read-only: nothing is downloaded or written) ---")
    assert out.count("whole · FOUND · S0a photo_id · proof dedupkey · via rpc") == 2
    for item in items.values():
        assert f"photo_id {item.photo_id} · https://photos.google.com/photo/{item.photo_id}" in out
    assert "whole · NOT FOUND" in out and "— a fetch would skip today's search (OD-5)" in out
    assert "✅ Located 2 of 3 object(s). Nothing was downloaded or written." in out and "RPC calls: " in out
    assert flow.world.downloads == [] and flow.legacy == []                     # no download, no legacy search
    assert not [a for a in flow.drivers["tv"].actions if a["kind"] in ("shift_d", "open")]
    assert _tree(tmp_path) == before                                            # nothing written anywhere …
    assert mvcommon.read_learned() == [] and mvcommon.read_failed() == []       # … not even the sidecars
    assert not any(marker in out for marker in WEB_MARKERS)                    # P-13: no web progress marker
    _assert_read_only_rpc(flow.world)


def test_locate_refuses_while_a_fetch_holds_the_lock(flow, sandbox, monkeypatch, capsys):
    _season(sandbox, flow.world, (1,))

    @contextlib.contextmanager
    def held(blocking=True, **kwargs):
        assert blocking is False                                                # never waits for a running fetch
        raise mvcommon.LockHeldError("held")
        yield  # pragma: no cover

    monkeypatch.setattr(mainfetch, "fetch_session_lock", held)

    mainfetch.cmd_locate(SEASON)

    assert "A fetch is running (the fetch session lock is held)" in capsys.readouterr().out
    assert flow.drivers == {}                                                   # Chrome never started


def test_locate_follows_the_objects_account_and_closes_its_chrome(flow, sandbox, capsys):
    folder = sandbox["local_root"] / "Series" / "Files" / "Season 02"
    folder.mkdir(parents=True)
    data = _bytes("xfile")
    item = flow.world.add_item("movies", _up("Files.S02E05.mkv", "x2x5x2"), data=data, taken="1994-10-28T02:00:00Z")
    mvcommon.save_library({"tv-en-1994-files-s02e05": _whole(folder, "Files.S02E05.mkv", data, "x2x5x2",
                                                             block=_learned(item, account="movies"))})

    mainfetch.cmd_locate("tv-en-1994-files-s02e05")

    out = capsys.readouterr().out
    assert list(flow.drivers) == ["movies"] and "Switching to profile 'movies'" in out
    assert "1 object(s) in 'movies'" in out and f"photo_id {item.photo_id}" in out
    assert "🧹 Closed" in out                                                   # the session's tabs closed (Step 11 i)


def test_locate_on_a_signed_out_profile_prints_the_remediation(flow, sandbox, capsys):
    _season(sandbox, flow.world, (1,))
    flow.world.session["tv"] = "expired"

    mainfetch.cmd_locate(SEASON)

    out = capsys.readouterr().out
    assert "❌ Profile 'tv' is logged out." in out and "Located" not in out


@pytest.mark.parametrize("argv, expected", [
    (["mainfetch.py", "locate", "tv-x"], {"manual_id": "tv-x", "ep_range": None, "fetch_extras": False,
                                         "probe_direct": False}),
    (["mainfetch.py", "locate", "tv-x", "--probe-direct", "episodes", "1-2", "--fetchExtras"],
     {"manual_id": "tv-x", "ep_range": "1-2", "fetch_extras": True, "probe_direct": True}),
])
def test_the_locate_command_line(monkeypatch, argv, expected):
    seen = []
    monkeypatch.setattr(mainfetch, "cmd_locate", lambda **kwargs: seen.append(kwargs))
    monkeypatch.setattr(mainfetch, "cmd_fetch_route", lambda *a, **k: pytest.fail("a locate must never fetch"))
    mainfetch.cli(argv)
    assert seen == [expected]


def test_locate_without_an_id_prints_its_usage(capsys):
    with pytest.raises(SystemExit):
        mainfetch.parse_locate_args(["mainfetch.py", "locate"])
    assert "Usage: locate [id]" in capsys.readouterr().out


@pytest.mark.parametrize("answer, said", [
    ((200, "video/x-matroska", None), "yes (video/x-matroska)"),
    ((403, None, None), "no (HTTP 403)"),
    ((200, "text/html", None), "no (HTTP 200, text/html)"),            # a sign-in page, not the file
    ((None, None, "URLError"), "no (URLError)"),
])
def test_probe_direct_asks_without_cookies_and_says_only_yes_or_no(flow, sandbox, monkeypatch, capsys, answer, said):
    # §M6 / Suggested next task #10: does the minted link answer WITHOUT the browser's cookies? HEAD only — nothing
    # is downloaded, and the link is never shown.
    _season(sandbox, flow.world, (1,))
    asked = []
    monkeypatch.setattr(mainfetch, "_head_without_cookies", lambda url: asked.append(url) or answer)

    mainfetch.cmd_locate(SEASON, probe_direct=True)

    out = capsys.readouterr().out
    assert f"     direct link without the browser's cookies: {said}" in out
    assert len(asked) == 1 and gpweb_host(asked[0]) == gphotos_fake.DOWNLOAD_HOST   # a freshly minted link was asked
    assert asked[0] not in out and gphotos_fake.DOWNLOAD_HOST not in out
    assert not any(token in out for token in flow.world.minted)
    assert flow.world.downloads == []                                  # the browser never opened the link


def test_probe_direct_without_a_minted_link(flow, sandbox, monkeypatch, capsys):
    flow.world.vrseub_original_url, flow.world.mint_refused = False, True
    _season(sandbox, flow.world, (1,))
    monkeypatch.setattr(mainfetch, "_head_without_cookies", lambda url: pytest.fail("no link: nothing to ask"))

    mainfetch.cmd_locate(SEASON, probe_direct=True)

    assert "direct link without the browser's cookies: no (no link was minted)" in capsys.readouterr().out


def test_the_cookie_less_head_request_never_returns_or_raises_its_url(monkeypatch):
    import urllib.error
    import urllib.request
    secret = "https://" + gphotos_fake.DOWNLOAD_HOST + "/dl/FAKESECRETTOKEN0001"
    sent = []

    class Answer:
        status, headers = 200, {"Content-Type": "video/mp4; codecs=avc1"}

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    def urlopen(request, timeout):
        sent.append((request.get_method(), dict(request.header_items()), timeout))
        return Answer()

    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    assert mainfetch._head_without_cookies(secret) == (200, "video/mp4", None)
    method, headers, timeout = sent[0]
    assert method == "HEAD" and "Cookie" not in headers and timeout == mainfetch.PROBE_HEAD_TIMEOUT_S

    def refused(request, timeout):
        raise urllib.error.HTTPError(secret, 403, "Forbidden", {}, None)

    monkeypatch.setattr(urllib.request, "urlopen", refused)
    assert mainfetch._head_without_cookies(secret) == (403, None, None)

    def broken(request, timeout):
        raise urllib.error.URLError(f"cannot reach {secret}")

    monkeypatch.setattr(urllib.request, "urlopen", broken)
    assert mainfetch._head_without_cookies(secret) == (None, None, "URLError")   # the reason (with the URL) dropped


# ===========================================================================
# 12C.d — cross-cutting: no secret anywhere, read-only RPC only
# ===========================================================================
def _state_files():
    """Every file the run's state lives in: the learned sidecars + failed list (GPHOTOS_STATE_DIR) and the fetch
    journal (FETCH_JOURNAL_DIR) — both redirected under tmp_path by tests/conftest.py."""
    out = []
    for root in (mvcommon.GPHOTOS_STATE_DIR, mainfetch.FETCH_JOURNAL_DIR):
        for folder, _dirs, names in os.walk(root):
            out += [os.path.join(folder, n) for n in names]
    return out


def test_no_link_token_or_account_secret_reaches_any_output_journal_or_sidecar(flow, sandbox, capfd):
    # P-20 + CLAUDE.md §5: a minted link is a bearer credential; the page tokens and the account email are secrets.
    # A run that uses every path — direct downloads, links answering with other bytes, Shift+D, a stale photo_id, an
    # OD-5 skip, a failed row, locate --probe-direct — leaves none of them in stdout / stderr or any state file.
    folder, items = _season(sandbox, flow.world, (1, 2))
    lost = _bytes("lost-everywhere")
    flow.world.add_item("tv", _up("Demo.S01E03.mkv", "de3de3"), data=_bytes("other bytes"), taken=TAKEN)
    _capture(folder, "de3de3", [_obj(lost, _up("Demo.S01E03.mkv", "de3de3"))])
    dead = flow.world.add_item("tv", _up("Demo.S01E04.mkv", "de4de4"), data=_bytes("ep4"), taken=TAKEN, deleted=True)
    flow.world.add_item("tv", _up("Demo.S01E04.mkv", "de4de4"), data=_bytes("ep4"), taken=TAKEN)
    lib = mvcommon.load_library()
    lib[SEASON]["children"] += [f"{SEASON}e03", f"{SEASON}e04"]
    lib[f"{SEASON}e03"] = dict(_whole(folder, "Demo.S01E03.mkv", lost, "de3de3"), parent_id=SEASON)
    lib[f"{SEASON}e04"] = dict(_whole(folder, "Demo.S01E04.mkv", _bytes("ep4"), "de4de4", block=_learned(dead)),
                               parent_id=SEASON)
    mvcommon.save_library(lib)
    flow.world.direct_mode = "wrong_bytes"           # links serve other bytes: every fallback path runs

    mainfetch.cmd_fetch_route(SEASON)
    mainfetch.cmd_locate(SEASON, probe_direct=True)

    captured = capfd.readouterr()
    texts = [captured.out, captured.err] + [open(p, encoding="utf-8", errors="replace").read() for p in _state_files()]
    secrets = [f"https://{gphotos_fake.DOWNLOAD_HOST}/{m['kind']}/{t}" for t, m in flow.world.minted.items()]
    secrets += list(flow.world.minted) + [gphotos_fake.DOWNLOAD_HOST]
    secrets += [flow.world.accounts["tv"].wiz["SNlM0e"], flow.world.accounts["tv"].email]
    assert len(flow.world.minted) >= 3 and any("journal" in p for p in _state_files())
    for text in texts:
        for secret in secrets:
            assert secret not in text, f"a secret reached the output or a state file: {secret[:24]}…"
    assert [r["reason"] for r in mvcommon.read_failed()] == ["s4-skipped"]
    assert [r["photo_id"] for r in mvcommon.read_learned("tv") if r.get("stale")] == [dead.photo_id]
    _assert_read_only_rpc(flow.world)                 # and never a mutating rpcid (P13)


# ===========================================================================
# 12C.e — 🚦 G12 follow-up: the judge's ten cherry-picks (.candidates/c25-step-12/DECISION.md; DECISIONS §1f)
# ===========================================================================
CONTRACT_KEYS = {"found", "photo_id", "record", "proof", "stage", "approach", "via", "candidates_seen",
                 "pages_opened", "searches", "rpc_calls", "elapsed", "per_approach"}


@pytest.mark.parametrize("run", ["fast-path", "approaches-all", "web-pages"])
def test_the_result_carries_the_whole_contract(fake_photos, tmp_path, run):
    # Pick 1 (from B): per_approach[<approach>] is EXACTLY {outcome, n} — PLAN Step 12's contract, which Step 13 reuses
    # unchanged; the path each verdict came by lives in the parallel per_approach_via. The judge's probe: one extra key
    # made mvcommon.append_failed refuse the entries as failed-list `tried` rows ("tried entries are {approach,
    # outcome, n}"), so a Step 13 that passed them through would have lost its rows.
    data = _bytes("contract")
    item = fake_photos.add_item("tv", _up(SHOW, SID), data=data, taken=TAKEN)
    fake_photos.wiz_token_missing["tv"] = run == "web-pages"
    ctx = _ctx()
    block = _learned(item, date_utc=TAKEN)
    [loc] = _locators(_whole(_folder(tmp_path), SHOW, data, SID, block=block), EP, ctx, tmp_path)

    res = mainfetch.locate_object(fake_photos.driver("tv"), loc, ctx,
                                  approaches="all" if run == "approaches-all" else "best")

    assert CONTRACT_KEYS <= set(res)
    assert res["found"] and res["photo_id"] == item.photo_id and isinstance(res["elapsed"], float)
    assert len(res["per_approach"]) == (6 if run == "approaches-all" else 1)
    assert all(set(verdict) == {"outcome", "n"} for verdict in res["per_approach"].values())
    assert list(res["per_approach_via"]) == list(res["per_approach"])
    assert set(res["per_approach_via"].values()) == {"ui" if run == "web-pages" else "rpc"}
    tried = [dict(approach=approach, **verdict) for approach, verdict in res["per_approach"].items()]
    mvcommon.append_failed({"account": "tv", "manual_id": EP, "reason": "ambiguous", "tried": tried})
    assert mvcommon.read_failed()[0]["tried"] == tried                 # Step 13 passes them through as they are


def _todays_attempts_click(flow, monkeypatch):
    """Today's two attempts (S4) really search and click in the fake world — recorded in flow.legacy as before."""
    monkeypatch.setattr(mainfetch, "trigger_download", lambda driver, query, index=0: flow.legacy.append(
        (query, index)) or TRIGGER_DOWNLOAD(driver, query, index))


def test_one_failed_row_keeps_the_stages_evidence_when_todays_search_then_picks_another_item(flow, sandbox, capsys,
                                                                                             monkeypatch):
    # Pick 2 (from A / B; the judge's probe — defect 6): a legacy object no stage pins, and today's attempt then clicks
    # ANOTHER item. Its ONE row keeps what every stage saw — booked BEFORE _record_wrong_items, the wrong pick folded in
    # as legacy wrong-item(1) — so resolve_photos (PLAN Step 14) still shows the candidates. Before: a bare
    # [legacy wrong-item] row without candidates.
    _todays_attempts_click(flow, monkeypatch)
    folder, name, sid = _episode_folder(sandbox), "Demo.S04E08.mkv", "l8l8l8"
    other = flow.world.add_item("tv", "Demo.S04E08.Extended [l8x8x8].mkv", data=_bytes("another file"), taken=TAKEN)
    flow.world.script_search("tv", _up(name, sid), [other.photo_id])             # S2 — and today's attempt 2 — see it
    mvcommon.save_library({"tv-en-2015-demo-s04e08": _whole(folder, name, _bytes("legacy real"), sid)})

    mainfetch.cmd_fetch_route("tv-en-2015-demo-s04e08")

    out = capsys.readouterr().out
    assert flow.legacy == [(name, 0), (_up(name, sid), 0)]                       # today's two attempts (S4) ran
    assert [(d["photo_id"], d["via"]) for d in flow.world.downloads] == [(other.photo_id, "shift_d")]  # the blind click
    assert "Wrong item downloaded (deleted)" in out and "❌ ENTRY INCOMPLETE." in out
    [row] = mvcommon.read_failed()                                               # ONE row: never booked twice
    assert (row["reason"], row["candidates"], row["sha256"]) == ("wrong-item", [other.photo_id],
                                                                 _sha256(_bytes("legacy real")))
    assert row["tried"] == [{"approach": "exact_name", "outcome": "wrong-item", "n": 1},
                            {"approach": "title_sid", "outcome": "not-found", "n": 0},
                            {"approach": "legacy", "outcome": "wrong-item", "n": 1}]
    assert ("Not fetched: Demo.S04E08.mkv — no stage found its exact item, and today's search picked another item "
            "(wrong-item); added to the failed list.") in out
    assert "the search picked another item; added to the failed list (wrong-item)" not in out   # nothing left for it


def test_the_stages_book_every_object_they_examined_once_and_leave_the_rest_to_the_wrong_item_booking(capsys):
    # Pick 2's booking rules, one object each: a located object with a wrong pick of today's search (its found item
    # first among the candidates) · a located object that simply did not arrive (no row: a re-run fetches it) · one no
    # stage pinned (the stages' verdict) · one whose OD-5 preview verdict gives way to the wrong pick · one already
    # 'failed' (it has its row) · one the stages never saw (left to _record_wrong_items).
    ctx = _ctx()
    pid, seen = "AF1QipFAKETVL0000001found", "AF1QipFAKETVL0000002other"

    def obj(name, res, **marks):
        loc = {"manual_id": "tv-en-2015-demo-s04e09", "label": name, "uploaded_name": name, "taken_ms": TAKEN_MS,
               "device_tz_offset_min": 480}
        item = {"filename": name, "hash": _sha256(name.encode()), "status": "pending", "locator": loc, **marks}
        if res is not None:
            item["_located"] = res
        return item

    found = {"found": True, "photo_id": pid, "candidates": [seen, pid],
             "per_approach": {"exact_name": {"outcome": "exactly-one", "n": 2}}}
    missed = {"found": False, "photo_id": None, "candidates": [seen],
              "per_approach": {"hash": {"outcome": "not-found", "n": 0}, "exact_name": {"outcome": "ambiguous", "n": 1}}}
    previewed = {"found": False, "photo_id": None, "candidates": [seen],
                 "per_approach": {"exact_name": {"outcome": "wrong-item", "n": 1},
                                  "legacy": {"outcome": "ambiguous", "n": 3}}}
    queue = [obj("a.mkv", found, _wrong=1), obj("b.mkv", found), obj("c.mkv", missed), obj("d.mkv", previewed, _wrong=2),
             obj("e.mkv", missed, _wrong=1, status="failed"), obj("f.mkv", None, _wrong=1)]

    mainfetch._record_unfetched(queue, ctx)
    mainfetch._record_wrong_items(queue, ctx)

    assert [i["status"] for i in queue] == ["failed", "pending", "failed", "failed", "failed", "failed"]
    rows = {r["label"]: r for r in mvcommon.read_failed()}
    assert sorted(rows) == ["a.mkv", "c.mkv", "d.mkv", "f.mkv"]                  # b: no row · e: had its own
    assert (rows["a.mkv"]["reason"], rows["a.mkv"]["candidates"]) == ("wrong-item", [pid, seen])
    assert rows["a.mkv"]["tried"] == [{"approach": "exact_name", "outcome": "exactly-one", "n": 2},
                                      {"approach": "legacy", "outcome": "wrong-item", "n": 1}]
    assert rows["a.mkv"]["expected_taken_local"] == "2022-02-05 19:49:46 GMT+08:00"
    assert (rows["c.mkv"]["reason"], rows["c.mkv"]["tried"]) == ("ambiguous", [
        {"approach": "hash", "outcome": "not-found", "n": 0}, {"approach": "exact_name", "outcome": "ambiguous", "n": 1}])
    assert rows["d.mkv"]["tried"] == [{"approach": "exact_name", "outcome": "wrong-item", "n": 1},
                                      {"approach": "legacy", "outcome": "wrong-item", "n": 2}]   # the preview gave way
    assert (rows["f.mkv"]["reason"], rows["f.mkv"]["tried"]) == ("wrong-item", [
        {"approach": "legacy", "outcome": "wrong-item", "n": 1}])                # _record_wrong_items, as before
    out = capsys.readouterr().out
    assert out.count("the search picked another item; added to the failed list (wrong-item)") == 1   # f only


@pytest.mark.parametrize("keyed", [False, True], ids=["names-only", "content-key"])
def test_a_located_item_whose_downloads_hold_other_bytes_ends_inconsistent_with_one_row(flow, sandbox, capsys,
                                                                                         monkeypatch, keyed):
    # Pick 3 (from B): the item that IS the object — its exact name proves it, or its content key (Google keeps the
    # dedupKey of the ORIGINAL upload, a storage-saver item serves re-encoded bytes) — serves other bytes than the
    # library holds, by its direct link AND by Shift+D, the web app's own original download: an IMP-C27 case, not
    # "the search picked another item". ONE row, reason inconsistent, its item first among the candidates.
    _todays_attempts_click(flow, monkeypatch)
    folder, name, sid = _episode_folder(sandbox), "Demo.S04E11.mkv", "l1l1l1"
    cloud = flow.world.add_item("tv", _up(name, sid), data=_bytes("cloud bytes"), taken=TAKEN)
    library = _bytes("library bytes")
    block = mvcommon.new_gphotos_block(_sha256(library), sha1=cloud.sha1_hex) if keyed else None
    mvcommon.save_library({"tv-en-2015-demo-s04e11": _whole(folder, name, library, sid, block=block)})

    mainfetch.cmd_fetch_route("tv-en-2015-demo-s04e11")

    out = capsys.readouterr().out
    stage = "S0b hash · proof dedupkey" if keyed else "S2 exact_name · proof filename"
    assert f"🎯 {name}: {stage} · via rpc" in out
    assert [(d["photo_id"], d["via"]) for d in flow.world.downloads] == [
        (cloud.photo_id, "direct"), (cloud.photo_id, "shift_d"), (cloud.photo_id, "shift_d")]   # its link, Shift+D, S4
    assert out.count("Wrong item downloaded (deleted)") == 3 and "RETRY" not in out
    [row] = mvcommon.read_failed()                                               # ONE row: never booked twice
    assert (row["reason"], row["candidates"][0]) == ("inconsistent", cloud.photo_id)
    assert row["tried"][0] == {"approach": "hash" if keyed else "exact_name", "outcome": "exactly-one", "n": 1}
    assert row["tried"][-1] == {"approach": "legacy", "outcome": "wrong-item", "n": 1}   # today's attempt, folded in
    assert ("Not fetched: Demo.S04E11.mkv — its item was found, but what it serves held other bytes than the "
            "library's (inconsistent); added to the failed list.") in out
    assert "the search picked another item" not in out and "❌ ENTRY INCOMPLETE." in out
    assert not (folder / "restore" / mainfetch.INCOMING_DIR_NAME).exists()      # every wrong copy deleted


def test_a_download_of_another_object_of_the_window_never_confirms_this_ones_start(tmp_path):
    # Pick 4 (from B): the window's other downloads are not this request's — not their partials, not their finished
    # files, not Chrome's ' (n)' variants. An 'Unconfirmed N.crdownload' names no object: it still counts.
    folder = tmp_path / "incoming"
    folder.mkdir()
    ctx = mainfetch.FetchContext()
    ctx.target = {"dir": str(folder)}
    before = mainfetch._watched_now(ctx)
    (folder / "Other [o1o1o1].chunk.001.mkv.crdownload").write_bytes(b"x")    # named while this request waits
    (folder / "Other [o1o1o1].chunk.002 (1).mkv").write_bytes(b"x")           # a ' (n)' copy of another one
    others = ["Other [o1o1o1].chunk.001.mkv", "Other [o1o1o1].chunk.002.mkv"]
    assert mainfetch._download_started(ctx, before, others) is False
    assert mainfetch._download_started(ctx, before) is True                     # without them: taken for its own
    (folder / "Unconfirmed 12.crdownload").write_bytes(b"x")
    assert mainfetch._download_started(ctx, before, others) is True


def test_a_window_never_takes_another_objects_download_for_its_own_start(flow, sandbox, capsys, monkeypatch, clock):
    # Pick 4 in a fetch: chunk 1's download is still unnamed ('Unconfirmed N.crdownload') when chunk 2's link is
    # opened, and Chrome names it while chunk 2's start is being confirmed. Before, that new '<chunk 1>.crdownload'
    # confirmed chunk 2's direct download: chunk 2 was booked 'direct' and its round waited the 300 s harvest timeout
    # for a file never requested. Now chunk 2's link started nothing — Shift+D fetches it at once.
    flow.world.download_mode = "manual"
    folder = sandbox["local_root"] / "Movies" / "Pair (2018)"
    folder.mkdir(parents=True)
    stem, sid, taken = "Pair.2018", "p2p2p2", "2025-06-06T06:06:06Z"
    datas = [_bytes("pair1"), _bytes("pair2")]
    names = [f"{stem} [{sid}].chunk.00{n}.mkv" for n in (1, 2)]
    items = [flow.world.add_item("movies", name, data=d, taken=taken) for name, d in zip(names, datas)]
    _capture(folder, sid, [_obj(d, name, role="chunk", index=n, date_utc=taken)
                           for n, (name, d) in enumerate(zip(names, datas), 1)])
    mvcommon.save_library({"mov-en-2018-pair": _split(folder, f"{stem}.mkv", sid, datas)})
    real_direct, real_harvest = gphotos_fake.FakeDriver._direct_download, mainfetch.harvest_downloads

    def direct(driver, url):
        if flow.world.minted[url.rsplit("/", 1)[-1]]["keys"] == [items[1].photo_id]:
            return None                                      # chunk 2's link: no download starts, the tab stays
        real_direct(driver, url)
        rec = flow.world.downloads[-1]                       # chunk 1's partial, not named yet …
        named, rec["partial"] = rec["partial"], os.path.join(os.path.dirname(rec["partial"]), "Unconfirmed 4711.crdownload")
        os.replace(named, rec["partial"])

        def name_it():                                       # … until Chrome names it, 5 s later
            os.replace(rec["partial"], named)
            rec["partial"] = named
        clock.after(5, name_it)

    def harvest(queue, ctx):
        flow.world.finish_downloads()
        return real_harvest(queue, ctx)

    monkeypatch.setattr(gphotos_fake.FakeDriver, "_direct_download", direct)
    monkeypatch.setattr(mainfetch, "harvest_downloads", harvest)
    started = clock.now

    mainfetch.cmd_fetch_route("mov-en-2018-pair")

    out = capsys.readouterr().out
    assert f"⬇️ {names[0]}: direct download" in out and f"⬇️ {names[1]}: direct download" not in out
    assert f"↪️ {names[1]}: the direct download did not start" in out
    assert f"⬇️ {names[1]}: Shift+D on its item page" in out
    assert [(r["photo_id"], r["via"]) for r in flow.world.downloads] == [(items[0].photo_id, "direct"),
                                                                         (items[1].photo_id, "shift_d")]
    assert "Timeout (No active downloads)" not in out and "never arrived" not in out
    assert clock.now - started < mainfetch.TRIGGER_CONFIRM_S * 3                 # no 300 s harvest timeout
    assert [o["method"] for o in _journal_objects()] == ["direct", "shift_d"]
    for name, data in zip(names, datas):
        assert (folder / "restore" / name).read_bytes() == data


def test_a_link_answering_with_a_page_is_left_at_once_and_never_waited_on(fake_photos, tmp_path, capsys, monkeypatch,
                                                                          clock):
    # Pick 5 (from B; A left after 3 s): a minted link that answers with a page of its own (a sign-in or error page)
    # puts its address — a bearer credential while valid (P-20) — in the tab. Before, the tab sat on it through the
    # whole 20 s start confirmation; now it is back on Google Photos at once: not one wait passes on that page.
    ctx, d, item, _fake, incoming = _armed(fake_photos, tmp_path, direct_mode="html")
    waits, real_sleep = [], clock.sleep
    monkeypatch.setattr(clock, "sleep", lambda seconds=0: waits.append((seconds, gpweb_host(d.current_url)))
                        or real_sleep(seconds))

    assert mainfetch._direct_trigger(d, item, ctx) is False

    assert [host for _seconds, host in waits if host == gphotos_fake.DOWNLOAD_HOST] == []
    assert mainfetch._is_photos_host(gpweb_host(d.current_url)) and _files(incoming) == []
    assert "its link answered with a web page" in capsys.readouterr().out
