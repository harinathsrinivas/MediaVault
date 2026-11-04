"""IMP-C25 Step 6 — the apply engine, the ONLY writer of `gphotos` blocks (PLAN Step 6; P-10, OD-2 ruling).

Pinned here:
  * main.gphotos_apply(records, capture=True, dry_run=True) folds learned records (+ each named object's
    capture facts) into the library: every object kind over all four prefixes; untrusted blocks replaced,
    blocks from newer code never touched; conflicts reported and never applied; dead photo ids dropped;
    P-4 home accounts (wrong-account objects, multi-account copies); search_override folding; aliases and
    season_maps; orphans; the report shape Steps 9 / 10 / 13 / 14 consume;
  * the library I/O guardrails: no valid record -> no load and no save (files byte-identical); a dry run
    writes nothing; --apply saves once; only the `gphotos` key changes; the OD-2 fingerprint guard reloads +
    re-patches after a concurrent write and aborts after three; never raises (one warning line); no
    rollback journal;
  * failed_objects.csv regenerated after an apply (confirmed rows drop off) — never over the user's edits;
  * main.merge_learned_gphotos() and its cmd_dispatch_fetch hook: argv, streaming and transcript
    unchanged, the merge runs after the child exits, no P-13 web-progress marker, Ctrl+C skips it.
SYNTHETIC data only: every photo id contains FAKE, device serials are FAKESERIAL…. Library I/O goes through
the `sandbox` fixture; the Google Photos state dir is redirected by conftest's autouse fixture.
"""
import copy
import csv
import hashlib
import io
import json
import os
import subprocess
from datetime import datetime, timezone

import pytest

import gpcapture
import gpweb
import main
import mainfetch
import mvcommon

PID = [f"AF1QipFAKEapply{i:02d}AAAA0123456789FAKE" for i in range(16)]
UPLOAD_MS = 1_790_104_139_274                      # backed up 2026-09 (synthetic)
TAKEN_MS = int(datetime(2024, 3, 9, 6, 3, 2, tzinfo=timezone.utc).timestamp()) * 1000
DATE_UTC = "2024-03-09T06:03:02Z"                   # the files' DateUTC = the taken time Google Photos shows

MOVIE = "mov-en-2020-gpamovie"         # unsplit movie leaf + extras: BTS (whole), Making Of (split, 2 chunks)
ANIME = "ani-ja-2019-gpaanime01"       # split leaf: 2 chunks + a FLAC holder (P-3: never the whole file)
SEASON = "tv-en-2015-gpashow-s02"      # season_map with one extras item (Recap)
EP = "tv-en-2015-gpashow-s02e02"       # unsplit episode leaf (a combined S02E02E03 file)
ALIAS = "tv-en-2015-gpashow-s02e03"    # multi_ep_alias -> EP
XFILE = "tv-en-1995-gpaxfiles-s02e05"  # a tv episode whose item lives in the MOVIES account (IMP-C26)
OTHER = "oth-en-2024-gpafinal"         # unsplit sports leaf
_LIBRARY_FILES = ("lib_movies", "lib_series", "lib_anime", "lib_others")
_MARKERS = ("🔹 PROCESSING:", "Detected Split File", "✅ MOVED:", "ENTRY COMPLETE")  # P-13


def _h(tag):
    """A synthetic SHA-256 hex."""
    return hashlib.sha256(tag.encode()).hexdigest()


def _sha1(tag):
    return hashlib.sha1(tag.encode()).hexdigest()


def T(minute, day=20):
    """A '…Z' timestamp on 2026-09-<day> at 10:<minute>."""
    return f"2026-09-{day:02d}T10:{minute:02d}:00Z"


# ================================================================== the synthetic library (all four prefixes)

def _leaf(mid, folder, filename, status="archived", chunks=0, holder=False):
    sid = mvcommon.generate_short_id(mid)
    stem, ext = os.path.splitext(filename)
    entry = {"short_id": sid, "filename": filename, "folder_path": str(folder), "status": status,
             "uploaded": True, "search_term": f"{stem} [{sid}]{ext}", "hash": _h(mid)}
    if chunks:
        entry["split_info"] = {
            "is_split": True, "method": "SIZE_GB", "val": "8", "total_chunks": chunks,
            "chunks": [{"filename": f"{stem} [{sid}].chunk.{i:03d}.mkv", "hash": _h(f"{mid}#c{i}")}
                       for i in range(1, chunks + 1)]}
        if holder:
            entry["split_info"]["carried_out_tracks"] = [
                {"track_id": 1, "codec": "A_FLAC", "language": "jpn",
                 "holder_filename": f"{stem} [{sid}].holder.mkv", "holder_hash": _h(f"{mid}#holder")}]
    return entry


def _extra(title_id, sub_rel, status="onboarded", chunks=0):
    sid = mvcommon.generate_short_id(f"{title_id}::Specials/{sub_rel}")
    stem, ext = os.path.splitext(sub_rel)
    item = {"filename": sub_rel, "sub_rel": sub_rel, "short_id": sid, "hash": _h(f"{title_id}::{sub_rel}"),
            "status": status, "uploaded": True, "search_term": f"{stem} [{sid}]{ext}"}
    if chunks:
        item["split_info"] = {
            "is_split": True, "method": "COUNT", "val": str(chunks), "total_chunks": chunks,
            "chunks": [{"filename": f"{stem} [{sid}].chunk.{i:03d}.mkv", "hash": _h(f"{sid}#c{i}")}
                       for i in range(1, chunks + 1)]}
    return item


@pytest.fixture()
def world(sandbox):
    """The synthetic library over all four prefixes, saved into the sandbox (the real save_library routes each
    prefix into its own file). Every folder exists under the sandbox LOCAL_ROOT so captures can be written."""
    root = sandbox["local_root"]
    dirs = {MOVIE: root / "Movies" / "GPA Movie (2020)", ANIME: root / "Anime" / "GPA Anime",
            EP: root / "Series" / "GPA Show" / "Season 02", XFILE: root / "Series" / "GPA X" / "Season 02",
            OTHER: root / "Sports" / "GPA Final"}
    for folder in dirs.values():
        folder.mkdir(parents=True, exist_ok=True)
        assert "C:\\Media" not in str(folder.resolve()), f"fixture folder must never be real media: {folder}"
    library = {
        MOVIE: {**_leaf(MOVIE, dirs[MOVIE], "GPA.Movie.2020.mkv"), "extras": {"groups": {"Specials": {
            "added_date": "2026-09-01",
            "items": [_extra(MOVIE, "BTS.mkv"), _extra(MOVIE, "Making Of.mkv", status="archived", chunks=2)]}}}},
        ANIME: _leaf(ANIME, dirs[ANIME], "GPA Anime - 01.mkv", chunks=2, holder=True),
        SEASON: {"type": "season_map", "folder_path": str(dirs[EP]), "total_episodes": 2, "children": [EP, ALIAS],
                 "extras": {"groups": {"Specials": {"added_date": "2026-09-01",
                                                    "items": [_extra(SEASON, "Recap.mkv")]}}}},
        EP: {**_leaf(EP, dirs[EP], "GPA.Show.S02E02E03.mkv"), "parent_id": SEASON},
        ALIAS: {"type": "multi_ep_alias", "alias_of": EP, "parent_id": SEASON},
        XFILE: _leaf(XFILE, dirs[XFILE], "GPA.X.S02E05.mkv"),
        OTHER: _leaf(OTHER, dirs[OTHER], "GPA.Final.2024.mkv"),
    }
    mvcommon.save_library(library)
    return {"library": library, "dirs": dirs, "sandbox": sandbox}


def _objects(mid, library=None):
    """{label: object} of one entry, from the saved library (mvcommon's own inventory)."""
    library = mvcommon.load_library() if library is None else library
    return {o["label"]: o for o in mvcommon.iter_entry_objects(mid, library[mid])}


def _sha(mid, label="whole"):
    return _objects(mid)[label]["sha256"]


def _sha_from(world, mid):
    """A leaf's hash from the fixture's in-memory library (for tests that make the saved one unreadable)."""
    return world["library"][mid]["hash"]


def _node(mid, label="whole", library=None):
    library = mvcommon.load_library() if library is None else library
    return mvcommon.slot_owner(library, _objects(mid, library)[label]["slot"])


def _rec(mid, sha256, account, at=None, source="check", **kw):
    """A complete learned record — the P-10 line as read_learned returns it."""
    return {"v": 1, "at": at or T(1), "account": account, "manual_id": mid, "sha256": sha256, "source": source, **kw}


def _confirm(mid, sha256, account, n, at=None, **kw):
    """A verified check record that confirms: identity proven by dedupKey, the item seen uploaded (P-5, OD-12)."""
    kw.setdefault("proof", "dedupkey")
    kw.setdefault("approach", {"tried": "s0_hash", "outcome": "exactly-one", "n": 1, "via": "rpc"})
    return _rec(mid, sha256, account, at=at, photo_id=PID[n], upload_ms=UPLOAD_MS + n, taken_ms=TAKEN_MS, **kw)


def _files(sandbox):
    return {name: sandbox[name].read_bytes() for name in _LIBRARY_FILES}


def _mtimes(sandbox):
    return {name: os.stat(sandbox[name]).st_mtime_ns for name in _LIBRARY_FILES}


def _without_gphotos(node):
    if isinstance(node, dict):
        return {k: _without_gphotos(v) for k, v in node.items() if k != "gphotos"}
    if isinstance(node, list):
        return [_without_gphotos(v) for v in node]
    return node


def _blocks(node, path=()):
    """{path: block} of every `gphotos` value nested in `node`."""
    out = {}
    if isinstance(node, dict):
        for k, v in node.items():
            if k == "gphotos":
                out[path] = v
            else:
                out.update(_blocks(v, path + (k,)))
    elif isinstance(node, list):
        for i, v in enumerate(node):
            out.update(_blocks(v, path + (i,)))
    return out


def _state_snapshot():
    """{relative path: bytes} of the Google Photos state dir (the learned sidecars, failed list, CSV)."""
    root = mvcommon.GPHOTOS_STATE_DIR
    if not os.path.isdir(root):
        return {}
    out = {}
    for folder, _dirs, names in os.walk(root):
        for name in names:
            path = os.path.join(folder, name)
            with open(path, "rb") as fh:
                out[os.path.relpath(path, root)] = fh.read()
    return out


def _write_capture(folder, short_id, doc):
    with open(os.path.join(str(folder), f"{short_id}.gpcapture.json"), "w", encoding="utf-8") as fh:
        json.dump(doc, fh)


def _capture_ep(world, sha1=None, device="FAKESERIAL06"):
    """Record EP's push with the REAL gpcapture writer (capture v2) -> the SHA-1 it recorded."""
    ep = world["library"][EP]
    sha1 = sha1 or _sha1("EP master bytes")
    path = gpcapture.capture_after_push(
        ep["folder_path"], EP, ep["short_id"],
        [{"role": "whole", "index": None, "sha256": ep["hash"], "local_name": ep["filename"],
          "uploaded_name": ep["search_term"], "size_bytes": 4_294_967_296, "sha1": sha1,
          "dedup_key": gpweb.dedup_key(sha1), "date_utc": DATE_UTC, "duration_s": 2712.48,
          "width": 1920, "height": 1080}],
        device_id=device, remote_dir="/sdcard/Media/Series/GPA Show",
        device_facts={"device_tz": "Asia/Singapore", "device_tz_offset_min": 480})
    assert path is not None
    return sha1


@pytest.fixture()
def io_calls(monkeypatch):
    """Counts the engine's library loads / saves (spies around the REAL functions — main's bindings, which
    the engine calls)."""
    calls = {"load": 0, "save": 0}
    real_load, real_save = main.load_library, main.save_library

    def load():
        calls["load"] += 1
        return real_load()

    def save(data):
        calls["save"] += 1
        return real_save(data)

    monkeypatch.setattr(main, "load_library", load)
    monkeypatch.setattr(main, "save_library", save)
    return calls


# ================================================================== no records -> no load, no save

@pytest.mark.parametrize("records,invalid", [
    ([], 0), (None, 0), ([{"v": 1}], 1), (["junk", 7, None], 3),
    ([_rec(EP, "not-a-sha256", "tv", proof="dedupkey", photo_id=PID[0])], 1),
    ([_rec(EP, _h(EP), "films", proof="dedupkey", photo_id=PID[0])], 1),
])
def test_no_valid_record_means_no_load_no_save_and_untouched_files(world, io_calls, capsys, records, invalid):
    before, mtimes = _files(world["sandbox"]), _mtimes(world["sandbox"])
    for dry_run in (True, False):
        report = main.gphotos_apply(records, dry_run=dry_run)
        assert (report["status"], report["records"], report["invalid"]) == ("nothing-to-apply", 0, invalid)
        assert (report["attempts"], report["saved"], report["changed"]) == (0, False, [])
    assert io_calls == {"load": 0, "save": 0}
    assert _files(world["sandbox"]) == before and _mtimes(world["sandbox"]) == mtimes
    assert capsys.readouterr().out == ""
    assert not os.path.exists(mvcommon.GPHOTOS_STATE_DIR)


def test_merge_with_no_learned_record_reads_and_writes_nothing(world, io_calls, capsys):
    before, mtimes = _files(world["sandbox"]), _mtimes(world["sandbox"])
    assert main.merge_learned_gphotos() is None                        # no sidecar at all
    os.makedirs(mvcommon.GPHOTOS_STATE_DIR)
    with open(mvcommon.learned_path("tv"), "wb") as fh:                # only a torn line (a crash mid-append)
        fh.write(b'{"v": 1, "account": "tv", "manu')
    assert main.merge_learned_gphotos() is None
    assert io_calls == {"load": 0, "save": 0}
    assert _files(world["sandbox"]) == before and _mtimes(world["sandbox"]) == mtimes
    assert capsys.readouterr().out == ""
    assert sorted(os.listdir(mvcommon.GPHOTOS_STATE_DIR)) == ["learned_tv.jsonl"]   # no CSV, nothing else


# ================================================================== what gets written, where

TARGETS = [  # (manual_id, object label, account) — one per object kind, over all four prefixes
    (MOVIE, "whole", "movies"),
    (MOVIE, "extra Specials/BTS.mkv", "movies"),
    (MOVIE, "extra Specials/Making Of.mkv chunk 1/2", "movies"),
    (ANIME, "chunk 1/2", "anime"),
    (ANIME, "holder", "anime"),
    (SEASON, "extra Specials/Recap.mkv", "tv"),
    (EP, "whole", "tv"),
    (OTHER, "whole", "others"),
]


def test_every_object_kind_over_all_four_prefixes_gets_a_trusted_confirmed_block(world, io_calls, capsys):
    records = [_confirm(mid, _sha(mid, label), account, n, label=label)
               for n, (mid, label, account) in enumerate(TARGETS)]
    report = main.gphotos_apply(records, dry_run=False)
    assert (report["status"], report["saved"], report["attempts"]) == ("saved", True, 1)
    assert (report["records"], report["objects"], report["unchanged"], report["confirmed"]) == (8, 8, 0, 8)
    assert report["conflicts"] == [] and report["orphans"] == [] and report["warning"] is None
    # in library order: sorted ids, then the entry's own object order
    assert [(c["manual_id"], c["label"], c["kind"], c["action"]) for c in report["changed"]] == [
        (ANIME, "chunk 1/2", "chunk", "create"), (ANIME, "holder", "holder", "create"),
        (MOVIE, "whole", "whole", "create"), (MOVIE, "extra Specials/BTS.mkv", "extra_whole", "create"),
        (MOVIE, "extra Specials/Making Of.mkv chunk 1/2", "extra_chunk", "create"),
        (OTHER, "whole", "whole", "create"),
        (SEASON, "extra Specials/Recap.mkv", "extra_whole", "create"),
        (EP, "whole", "whole", "create")]
    assert io_calls == {"load": 1, "save": 1}
    assert capsys.readouterr().out == ""                                # the engine prints only warnings
    library = mvcommon.load_library()
    for n, (mid, label, account) in enumerate(TARGETS):
        obj = _objects(mid, library)[label]
        assert obj["cloud_state"] == "confirmed" and obj["block"] is not None       # TRUSTED (P-2)
        block = obj["block"]
        record = block["accounts"][account]
        assert (record["photo_id"], record["verified_by"], record["source"], record["confirmed"]) == (
            PID[n], "dedupkey", "check", True)
        assert block["home_account"] == account                        # the prefix-default account (P-4)
        assert list(block) == [k for k in mvcommon._BLOCK_KEYS if k in block]       # P-1 key order
    # objects no record names keep no block; a split leaf never gets an entry-level one (P-3)
    assert set(_blocks(library)) == {
        (ANIME, "split_info", "chunks", 0), (ANIME, "split_info", "carried_out_tracks", 0), (MOVIE,),
        (MOVIE, "extras", "groups", "Specials", "items", 0),
        (MOVIE, "extras", "groups", "Specials", "items", 1, "split_info", "chunks", 0), (OTHER,),
        (SEASON, "extras", "groups", "Specials", "items", 0), (EP,)}
    for name in _LIBRARY_FILES:                                        # every prefix-routed file holds blocks
        assert b'"gphotos"' in world["sandbox"][name].read_bytes(), name
    assert not os.path.exists(mvcommon.failed_csv_path())             # no failed list -> no CSV is created


def test_only_the_gphotos_key_of_the_named_objects_changes(world):
    before = mvcommon.load_library()
    records = [_confirm(MOVIE, _sha(MOVIE), "movies", 0), _confirm(ANIME, _sha(ANIME, "chunk 2/2"), "anime", 1),
               _confirm(ALIAS, _sha(EP), "tv", 2)]
    assert main.gphotos_apply(records, dry_run=False)["status"] == "saved"
    after = mvcommon.load_library()
    # every other key, value, type AND key order is exactly as before
    assert json.dumps(_without_gphotos(after)) == json.dumps(_without_gphotos(before))
    assert set(_blocks(after)) == {(MOVIE,), (ANIME, "split_info", "chunks", 1), (EP,)}
    assert after[ALIAS] == {"type": "multi_ep_alias", "alias_of": EP, "parent_id": SEASON}


# ================================================================== capture facts (P-11)

def _anime_capture(world):
    """ANIME's push in the documented capture-v2 shape: two chunks + the FLAC holder."""
    entry = world["library"][ANIME]
    chunks = entry["split_info"]["chunks"]
    holder = entry["split_info"]["carried_out_tracks"][0]
    objects = [{"role": "chunk", "index": i, "sha256": c["hash"], "uploaded_name": c["filename"],
                "local_name": c["filename"], "size_bytes": 1000 + i, "sha1": _sha1(f"anime chunk {i}"),
                "dedup_key": gpweb.dedup_key(_sha1(f"anime chunk {i}")), "date_utc": DATE_UTC,
                "duration_s": 600.5 * i, "width": 1920, "height": 1080, "approach_hint": "hash"}
               for i, c in enumerate(chunks, 1)]
    objects.append({"role": "holder", "index": None, "sha256": holder["holder_hash"],
                    "uploaded_name": holder["holder_filename"], "size_bytes": 77, "sha1": _sha1("anime holder"),
                    "date_utc": DATE_UTC, "approach_hint": "hash"})
    doc = {"capture_version": 2, "manual_id": ANIME, "short_id": entry["short_id"], "pushes": [{
        "captured_at": T(0, day=1), "device_id": "FAKESERIAL07", "device_tz": "Asia/Singapore",
        "device_tz_offset_min": 480, "remote_dir": "/sdcard/Media/Anime/GPA Anime", "chunk_range": None,
        "complete": True, "objects": objects}]}
    _write_capture(entry["folder_path"], entry["short_id"], doc)
    return objects


def test_capture_facts_are_folded_into_the_blocks_of_the_named_objects(world):
    ep_sha1 = _capture_ep(world)
    anime = _anime_capture(world)
    ep_key = gpweb.dedup_key(ep_sha1)
    records = [_confirm(EP, _sha(EP), "tv", 0, dedup_key=ep_key),
               _confirm(ANIME, anime[0]["sha256"], "anime", 1, dedup_key=anime[0]["dedup_key"])]
    report = main.gphotos_apply(records, dry_run=False)
    assert report["status"] == "saved" and report["conflicts"] == []
    ep = _objects(EP)["whole"]["block"]
    assert (ep["sha1"], ep["dedup_key"], ep["size_bytes"], ep["width"], ep["height"]) == (
        ep_sha1, ep_key, 4_294_967_296, 1920, 1080)
    assert (ep["duration_s"], ep["date_utc"], ep["uploaded_name"]) == (
        2712.48, DATE_UTC, world["library"][EP]["search_term"])
    assert ep["pushed"]["device_id"] == "FAKESERIAL06" and ep["pushed"]["at"].endswith("Z")
    assert (ep["pushed"]["device_tz"], ep["pushed"]["device_tz_offset_min"]) == ("Asia/Singapore", 480)
    assert ep["accounts"]["tv"]["confirmed"] is True and ep["home_account"] == "tv"
    assert list(ep) == [k for k in mvcommon._BLOCK_KEYS if k in ep]
    chunk = _objects(ANIME)["chunk 1/2"]["block"]
    assert (chunk["sha1"], chunk["size_bytes"], chunk["duration_s"]) == (anime[0]["sha1"], 1001, 600.5)
    assert chunk["pushed"] == {"at": T(0, day=1), "device_id": "FAKESERIAL07", "device_tz": "Asia/Singapore",
                               "device_tz_offset_min": 480}
    assert "approach_hint" not in chunk and "role" not in chunk      # capture-only facts are not block keys
    # the capture of an object no record names is not folded anywhere (chunk 2, the holder)
    assert _objects(ANIME)["chunk 2/2"]["block"] is None and _objects(ANIME)["holder"]["block"] is None


def test_capture_false_folds_no_capture_fact(world):
    _capture_ep(world)
    report = main.gphotos_apply([_confirm(EP, _sha(EP), "tv", 0)], capture=False, dry_run=False)
    assert report["status"] == "saved"
    block = _objects(EP)["whole"]["block"]
    assert set(block) == {"v", "sha256", "accounts", "home_account"}


def test_capture_alone_never_creates_a_block_or_confirms(world):
    """P-11 / PLAN Step 6: a capture proves the push, never that Google Photos holds the object. Capture-only
    objects get no block; records that do not prove the item was seen uploaded never confirm, however much
    the capture knows."""
    ep_sha1 = _capture_ep(world)
    anime = _anime_capture(world)
    main.gphotos_apply([_confirm(MOVIE, _sha(MOVIE), "movies", 0)], dry_run=False)     # records for others only
    assert _objects(EP)["whole"]["block"] is None and _objects(EP)["whole"]["cloud_state"] == "unverified"
    assert _objects(ANIME)["chunk 1/2"]["block"] is None
    records = [
        # identity proven, but the item was never seen uploaded (no upload_ms) ...
        _rec(EP, _sha(EP), "tv", proof="dedupkey", photo_id=PID[1], dedup_key=gpweb.dedup_key(ep_sha1)),
        # ... a manual match alone ...
        _rec(EP, _sha(EP), "movies", source="manual", proof="manual", photo_id=PID[2], upload_ms=UPLOAD_MS),
        # ... and an approach verdict that found nothing
        _rec(ANIME, anime[0]["sha256"], "anime", approach={"tried": "s0_hash", "outcome": "not-found", "n": 0}),
    ]
    report = main.gphotos_apply(records, dry_run=False)
    assert report["status"] == "saved" and report["confirmed"] == 0
    for mid, label in ((EP, "whole"), (ANIME, "chunk 1/2")):
        obj = _objects(mid)[label]
        assert obj["cloud_state"] == "unverified"                     # still pending (check_photos pending)
        assert obj["block"]["sha1"] and obj["block"]["pushed"]         # the capture WAS folded ...
        assert not any(r.get("confirmed") for r in obj["block"]["accounts"].values())   # ... never confirming
        assert all("upload_ms" not in r for a, r in obj["block"]["accounts"].items() if a != "movies")


def test_a_trusted_block_takes_the_latest_push_capture(world):
    _capture_ep(world)
    records = [_confirm(EP, _sha(EP), "tv", 0)]
    main.gphotos_apply(records, dry_run=False)
    first = _objects(EP)["whole"]["block"]
    _capture_ep(world, device="FAKESERIAL08")                         # the same bytes pushed again elsewhere
    report = main.gphotos_apply(records, dry_run=False)
    (change,) = report["changed"]
    assert change["action"] == "update" and change["fields"]["pushed.device_id"] == ["FAKESERIAL06", "FAKESERIAL08"]
    assert set(change["fields"]) <= {"pushed.at", "pushed.device_id"}
    block = _objects(EP)["whole"]["block"]
    assert block["accounts"] == first["accounts"] and block["pushed"]["device_id"] == "FAKESERIAL08"


# ================================================================== the pin (P-2) and newer code

def test_an_untrusted_block_is_replaced_never_trusted(world):
    """P-2: a block pinned to OTHER bytes (the object was re-mastered after its push) describes nothing about
    the current bytes: its confirmation, photo id and learned home must not survive — the engine replaces it."""
    library = mvcommon.load_library()
    stale_pin = _h("EP bytes before the re-master")
    library[EP]["gphotos"] = stale = mvcommon.new_gphotos_block(
        stale_pin, sha1=_sha1("old bytes"), uploaded_name="Old Name [x].mkv", home_account="movies",
        accounts={"movies": {"photo_id": PID[9], "upload_ms": 1, "verified_by": "dedupkey", "source": "check",
                             "verified_at": T(0), "confirmed": True}})
    mvcommon.save_library(library)
    report = main.gphotos_apply([_rec(EP, _sha(EP), "tv", approach={"tried": "s2_exact_name",
                                                                   "outcome": "not-found", "n": 0})],
                                dry_run=False)
    (change,) = report["changed"]
    assert change["action"] == "replace" and change["fields"]["sha256"] == [stale_pin, _sha(EP)]
    assert change["fields"]["accounts.movies.confirmed"] == [True, None]
    block = mvcommon.load_library()[EP]["gphotos"]
    assert block == {"v": 1, "sha256": _sha(EP), "accounts": {"tv": {"approach": {"failed": ["exact_name"],
                                                                                  "at": T(1)}}}}
    assert _objects(EP)["whole"]["cloud_state"] == "uploaded" and report["confirmed"] == 0
    assert stale["accounts"]["movies"]["photo_id"] == PID[9]           # (the fixture itself is untouched)


def test_a_block_written_by_newer_code_is_never_overwritten(world, io_calls):
    library = mvcommon.load_library()
    future = dict(mvcommon.new_gphotos_block(library[EP]["hash"]), v=2, from_the_future={"kept": True})
    library[EP]["gphotos"] = copy.deepcopy(future)
    mvcommon.save_library(library)
    before = _files(world["sandbox"])
    report = main.gphotos_apply([_confirm(EP, _sha(EP), "tv", 0)], dry_run=False)
    assert report["status"] == "up-to-date" and report["changed"] == [] and report["unchanged"] == 1
    assert report["conflicts"] == [{"manual_id": EP, "label": "whole", "kind": "whole", "sha256": _sha(EP),
                                    "account": None, "code": "block-version"}]
    assert io_calls["save"] == 0 and _files(world["sandbox"]) == before
    assert mvcommon.load_library()[EP]["gphotos"] == future


def test_a_second_apply_is_up_to_date_and_writes_nothing(world, io_calls):
    _capture_ep(world)
    records = [_confirm(mid, _sha(mid, label), account, n) for n, (mid, label, account) in enumerate(TARGETS)]
    assert main.gphotos_apply(records, dry_run=False)["status"] == "saved"
    before, mtimes = _files(world["sandbox"]), _mtimes(world["sandbox"])
    report = main.gphotos_apply(records, dry_run=False)
    assert (report["status"], report["saved"], report["changed"], report["unchanged"]) == (
        "up-to-date", False, [], len(TARGETS))
    assert io_calls == {"load": 2, "save": 1}
    assert _files(world["sandbox"]) == before and _mtimes(world["sandbox"]) == mtimes


# ================================================================== conflicts, dead photo ids

def test_conflicts_are_reported_and_never_applied(world):
    sha1 = _capture_ep(world)
    ours = gpweb.dedup_key(sha1)
    records = [
        _confirm(EP, _sha(EP), "movies", 0, dedup_key=ours),                                  # consistent
        _rec(EP, _sha(EP), "tv", source="map", proof="filename", photo_id=PID[1], upload_ms=5,
             dedup_key=gpweb.dedup_key(_sha1("a re-split twin"))),                            # other bytes
        _rec(EP, _sha(EP), "anime", source="fetch", proof="sha256",
             dedup_key=gpweb.dedup_key(_sha1("downloaded other bytes"))),                     # vs the capture
    ]
    report = main.gphotos_apply(records, dry_run=False)
    where = {"manual_id": EP, "label": "whole", "kind": "whole", "sha256": _sha(EP)}
    assert report["conflicts"] == [dict(where, account="tv", code="dedup-key-mismatch"),
                                   dict(where, account="anime", code="content-key-mismatch")]
    block = _objects(EP)["whole"]["block"]
    assert set(block["accounts"]) == {"movies"} and block["accounts"]["movies"]["photo_id"] == PID[0]
    assert block["dedup_key"] == ours and block["home_account"] == "movies"


def test_a_capture_contradicting_the_blocks_content_key_is_not_folded(world):
    held_sha1 = _sha1("the bytes a download proved")
    main.gphotos_apply([_rec(EP, _sha(EP), "tv", source="fetch", proof="sha256",
                             dedup_key=gpweb.dedup_key(held_sha1))], dry_run=False)
    assert _objects(EP)["whole"]["block"]["sha1"] == held_sha1
    _capture_ep(world, sha1=_sha1("a capture of other bytes"))
    report = main.gphotos_apply([_confirm(EP, _sha(EP), "tv", 3)], dry_run=False)
    assert [(c["account"], c["code"]) for c in report["conflicts"]] == [(None, "capture-content-mismatch")]
    block = _objects(EP)["whole"]["block"]
    assert block["sha1"] == held_sha1 and "pushed" not in block and "size_bytes" not in block
    assert block["accounts"]["tv"]["photo_id"] == PID[3]              # the records still apply


def test_invalid_capture_facts_are_a_conflict_not_a_crash(world, monkeypatch):
    real = mvcommon.read_capture

    def broken(folder, short_id):
        facts = real(folder, short_id)
        return {**facts, _h(EP): {"sha256": _h(EP), "size_bytes": -5}} if short_id == world["library"][EP][
            "short_id"] else facts

    monkeypatch.setattr(mvcommon, "read_capture", broken)
    report = main.gphotos_apply([_confirm(EP, _sha(EP), "tv", 0)], dry_run=False)
    assert report["status"] == "saved"
    assert [(c["account"], c["code"]) for c in report["conflicts"]] == [(None, "capture-invalid")]
    assert "size_bytes" not in _objects(EP)["whole"]["block"]


def test_a_stale_record_drops_the_dead_photo_id_its_confirmation_and_the_learned_home(world):
    confirmed = _confirm(EP, _sha(EP), "tv", 0, at=T(1))
    main.gphotos_apply([confirmed], dry_run=False)
    assert _objects(EP)["whole"]["cloud_state"] == "confirmed"
    dead = _rec(EP, _sha(EP), "tv", at=T(5), source="fetch", stale=True, photo_id=PID[0])
    report = main.gphotos_apply([confirmed, dead], dry_run=False)
    (change,) = report["changed"]
    assert change["fields"]["accounts.tv.photo_id"] == [PID[0], None]
    assert change["fields"]["accounts.tv.confirmed"] == [True, None]
    assert change["fields"]["home_account"] == ["tv", None]
    block = _objects(EP)["whole"]["block"]
    assert block["accounts"]["tv"] == {"approach": {"best": "hash", "ok": ["hash"], "failed": ["photo_id"],
                                                    "at": T(5), "via": "rpc"}}
    # no verified copy anywhere: the learned layer is empty and routing is the id prefix again (P-4)
    assert "home_account" not in block
    assert mvcommon.home_account(block, EP, mainfetch.ID_PREFIX_PROFILE, mainfetch.DEFAULT_PROFILE) \
        == mainfetch.profile_for_id(EP) == "tv"


# ================================================================== accounts (P-4)

def test_a_wrong_account_object_learns_its_home_account(world):
    """The X-Files case (IMP-C26): a tv episode whose only verified copy is in the MOVIES account."""
    sha = _sha(XFILE)
    records = [
        _rec(XFILE, sha, "tv", approach={"tried": "s2_exact_name", "outcome": "not-found", "n": 0}),
        _rec(XFILE, sha, "movies", source="map", proof="filename", photo_id=PID[4], upload_ms=UPLOAD_MS,
             gp_filename=world["library"][XFILE]["search_term"],
             approach={"tried": "s2_exact_name", "outcome": "exactly-one", "n": 1, "via": "ui"}),
    ]
    report = main.gphotos_apply(records, dry_run=False)
    assert report["changed"][0]["fields"]["home_account"] == [None, "movies"]
    block = _objects(XFILE)["whole"]["block"]
    assert block["home_account"] == "movies" and block["accounts"]["movies"]["confirmed"] is True
    assert block["accounts"]["tv"] == {"approach": {"failed": ["exact_name"], "at": T(1)}}
    assert mvcommon.home_account(block, XFILE, mainfetch.ID_PREFIX_PROFILE, mainfetch.DEFAULT_PROFILE) == "movies"
    assert mainfetch.profile_for_id(XFILE) == "tv"                    # what prefix routing alone answers


def test_multi_account_copies_are_all_recorded_and_the_prefix_default_wins_home(world):
    chunk = _sha(ANIME, "chunk 1/2")
    records = [  # Kuroko-like: a copy in TV besides the anime account; the TV proof is even stronger
        _confirm(ANIME, chunk, "anime", 0, proof="filename", source="map"),
        _confirm(ANIME, chunk, "tv", 1),
        # an oth- object verified only in two NON-default accounts: the strongest proof wins
        _confirm(OTHER, _sha(OTHER), "tv", 2, proof="filename", source="map"),
        _confirm(OTHER, _sha(OTHER), "movies", 3),
    ]
    main.gphotos_apply(records, dry_run=False)
    anime = _objects(ANIME)["chunk 1/2"]["block"]
    assert list(anime["accounts"]) == ["tv", "anime"]                 # GP_ACCOUNTS order; both copies kept
    assert anime["home_account"] == "anime"
    other = _objects(OTHER)["whole"]["block"]
    assert list(other["accounts"]) == ["movies", "tv"] and other["home_account"] == "movies"


def test_search_override_folds_and_the_newest_wins(world):
    term = "GPA Show S02E02 – director’s cut"                        # Excel-autocorrected, non-ASCII
    records = [_rec(EP, _sha(EP), "tv", at=T(2), source="manual", search_override=term),
               _rec(EP, _sha(EP), "tv", at=T(1), source="manual", search_override="an older term")]
    main.gphotos_apply(records, dry_run=False)
    block = _objects(EP)["whole"]["block"]
    assert block == {"v": 1, "sha256": _sha(EP), "search_override": {"query": term, "at": T(2)}}
    older = [_rec(EP, _sha(EP), "tv", at=T(0), source="manual", search_override="oldest")]
    assert main.gphotos_apply(records + older, dry_run=False)["status"] == "up-to-date"
    newer = [_rec(EP, _sha(EP), "tv", at=T(9), source="manual", search_override="the newest term")]
    main.gphotos_apply(records + newer, dry_run=False)
    assert _objects(EP)["whole"]["block"]["search_override"] == {"query": "the newest term", "at": T(9)}


# ================================================================== aliases, season_maps, orphans

def test_records_naming_an_alias_apply_to_its_primary(world):
    report = main.gphotos_apply([_confirm(ALIAS, _sha(EP), "tv", 0)], dry_run=False)
    (change,) = report["changed"]
    assert (change["manual_id"], change["label"]) == (EP, "whole")
    library = mvcommon.load_library()
    assert library[EP]["gphotos"]["accounts"]["tv"]["photo_id"] == PID[0]
    assert library[ALIAS] == {"type": "multi_ep_alias", "alias_of": EP, "parent_id": SEASON}
    # the same object named through the alias AND its primary is one object and one change
    both = [_confirm(ALIAS, _sha(EP), "tv", 1, at=T(3)), _confirm(EP, _sha(EP), "tv", 1, at=T(3))]
    again = main.gphotos_apply(both, dry_run=True)
    assert again["objects"] == 1 and len(again["changed"]) == 1 and again["orphans"] == []


def test_the_canonical_alias_chain_is_alias_safe(sandbox_alias):
    """On the project's canonical alias library (season_map + leaf + multi_ep_alias — the smoke alias sweep's
    fixture): a record naming the alias lands on the primary; the alias and the season_map stay
    byte-for-byte what they were."""
    alias, primary, season = sandbox_alias["alias_id"], sandbox_alias["primary_id"], sandbox_alias["season_id"]
    library = mvcommon.load_library()
    frozen = {mid: json.dumps(library[mid]) for mid in (alias, season)}
    report = main.gphotos_apply([_confirm(alias, library[primary]["hash"], "tv", 0)], dry_run=False)
    assert [(c["manual_id"], c["kind"], c["action"]) for c in report["changed"]] == [(primary, "whole", "create")]
    after = mvcommon.load_library()
    assert {mid: json.dumps(after[mid]) for mid in (alias, season)} == frozen
    assert after[primary]["gphotos"]["accounts"]["tv"]["confirmed"] is True
    assert json.dumps(_without_gphotos(after)) == json.dumps(_without_gphotos(library))


def test_every_entry_the_records_name_is_folded_even_when_entries_share_bytes(world):
    """reduce_learned groups records by (account, sha256) and keeps only the LAST record's manual_id; the
    engine still folds the group into EVERY entry the records named — two entries holding the same bytes
    are one Google Photos item (it dedups by content), so both learn it."""
    library = mvcommon.load_library()
    twin = "tv-en-2015-gpashow-s02e02x"
    library[twin] = dict(copy.deepcopy(library[EP]), filename="GPA.Show.S02E02E03.copy.mkv")
    mvcommon.save_library(library)
    records = [_confirm(EP, _sha(EP), "tv", 0, at=T(1)), _confirm(twin, _sha(EP), "tv", 0, at=T(2))]
    report = main.gphotos_apply(records, dry_run=False)
    assert [c["manual_id"] for c in report["changed"]] == [EP, twin] and report["orphans"] == []
    after = mvcommon.load_library()
    assert after[EP]["gphotos"]["accounts"]["tv"]["photo_id"] == after[twin]["gphotos"]["accounts"]["tv"][
        "photo_id"] == PID[0]


def test_orphans_are_reported_and_never_applied(world, io_calls):
    library = mvcommon.load_library()
    dangling = "tv-en-2015-gpashow-s02e09"
    library[dangling] = {"type": "multi_ep_alias", "alias_of": "tv-en-2015-gpashow-s02e99", "parent_id": SEASON}
    mvcommon.save_library(library)
    before = _files(world["sandbox"])
    records = [
        _confirm(ANIME, _h("a chunk of the split before a re-split"), "anime", 0, label="chunk 1/3"),
        _confirm("tv-en-1999-gone-s01e01", _h("gone"), "tv", 1),
        _confirm(dangling, _h("dangling"), "tv", 2),
        _confirm(MOVIE, _sha(EP), "movies", 3),            # another entry's bytes: never applied library-wide
        _confirm(SEASON, _sha(EP), "tv", 4),               # a season_map owns only its extras
    ]
    report = main.gphotos_apply(records, dry_run=False)
    assert (report["status"], report["objects"], report["changed"]) == ("up-to-date", 0, [])
    assert [(o["manual_id"], o["account"], o["reason"]) for o in report["orphans"]] == [
        (ANIME, "anime", "no-object"), (MOVIE, "movies", "no-object"), ("tv-en-1999-gone-s01e01", "tv", "no-entry"),
        (SEASON, "tv", "no-object"), (dangling, "tv", "no-entry")]
    assert report["orphans"][0]["label"] == "chunk 1/3"
    assert io_calls["save"] == 0 and _files(world["sandbox"]) == before


# ================================================================== dry run

def test_a_dry_run_reports_the_would_be_diff_and_writes_nothing(world, io_calls, capsys):
    sha1 = _capture_ep(world)
    mvcommon.append_failed({"account": "tv", "manual_id": EP, "sha256": _sha(EP), "label": "whole",
                            "reason": "not-found"})
    records = [_confirm(EP, _sha(EP), "tv", 0, dedup_key=gpweb.dedup_key(sha1))]
    mvcommon.append_learned("tv", dict(records[0]))
    before, mtimes, state = _files(world["sandbox"]), _mtimes(world["sandbox"]), _state_snapshot()
    report = main.gphotos_apply(records)                             # dry_run defaults to True
    assert (report["status"], report["dry_run"], report["saved"], report["attempts"]) == ("dry-run", True, False, 1)
    (change,) = report["changed"]
    assert change["action"] == "create" and report["confirmed"] == 1 and report["failed_csv"] is None
    assert change["fields"]["sha1"] == [None, sha1]
    assert change["fields"]["accounts.tv.confirmed"] == [None, True]
    assert change["fields"]["home_account"] == [None, "tv"]
    assert io_calls == {"load": 1, "save": 0}
    assert _files(world["sandbox"]) == before and _mtimes(world["sandbox"]) == mtimes
    assert _state_snapshot() == state and not os.path.exists(mvcommon.failed_csv_path())
    assert capsys.readouterr().out == ""
    assert _objects(EP)["whole"]["block"] is None


# ================================================================== the OD-2 fingerprint guard

def _racing_load(monkeypatch, writes):
    """Patch the engine's load_library: after `writes` of the engine's loads, ANOTHER MediaVault process saves
    the library (a new entry each time) — exactly the window the OD-2 re-check must catch."""
    real = main.load_library
    loads = []

    def load():
        library = real()
        loads.append(1)
        if len(loads) <= writes:
            other = mvcommon.load_library()
            other[f"mov-en-2021-gpaother{len(loads)}"] = {"folder_path": "x", "filename": f"o{len(loads)}.mkv",
                                                          "status": "local_ready", "hash": _h(str(len(loads)))}
            other[OTHER]["status"] = "restored_local"
            mvcommon.save_library(other)
        return library

    monkeypatch.setattr(main, "load_library", load)
    return loads


def test_a_write_between_load_and_save_reloads_and_repatches(world, monkeypatch, capsys):
    loads = _racing_load(monkeypatch, writes=1)
    report = main.gphotos_apply([_confirm(EP, _sha(EP), "tv", 0)], dry_run=False)
    assert (report["status"], report["attempts"], len(loads)) == ("saved", 2, 2)
    library = mvcommon.load_library()
    assert "mov-en-2021-gpaother1" in library and library[OTHER]["status"] == "restored_local"  # theirs survived
    assert library[EP]["gphotos"]["accounts"]["tv"]["confirmed"] is True                       # ours landed
    assert capsys.readouterr().out == ""


def test_the_fingerprint_hash_catches_a_rewrite_that_keeps_size_and_mtime(world, monkeypatch):
    """OD-2: the fingerprint is size + mtime_ns + SHA-256 — a rewrite that keeps both the size and the mtime
    (a coarse-mtime file system, a tool that restores timestamps) is still caught, by the hash."""
    path = world["sandbox"]["lib_others"]
    real = main.load_library
    loads = []

    def load():
        library = real()
        loads.append(1)
        if len(loads) == 1:
            st = os.stat(path)
            data = path.read_bytes()
            assert data.count(b'"archived"') == 1
            path.write_bytes(data.replace(b'"archived"', b'"Archived"'))          # same size
            os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns))                      # same mtime
            assert (os.stat(path).st_size, os.stat(path).st_mtime_ns) == (st.st_size, st.st_mtime_ns)
        return library

    monkeypatch.setattr(main, "load_library", load)
    report = main.gphotos_apply([_confirm(EP, _sha(EP), "tv", 0)], dry_run=False)
    assert (report["status"], report["attempts"]) == ("saved", 2)
    library = mvcommon.load_library()
    assert library[OTHER]["status"] == "Archived"                                    # theirs survived
    assert library[EP]["gphotos"]["accounts"]["tv"]["confirmed"] is True


def test_a_library_that_keeps_changing_aborts_after_three_attempts(world, monkeypatch, capsys):
    loads = _racing_load(monkeypatch, writes=99)
    report = main.gphotos_apply([_confirm(EP, _sha(EP), "tv", 0)], dry_run=False)
    assert main.GPHOTOS_APPLY_ATTEMPTS == 3
    assert (report["status"], report["attempts"], report["saved"], len(loads)) == ("aborted", 3, False, 3)
    line = "⚠️  Google Photos identity not applied: library changed by another process — re-run when idle.\n"
    assert capsys.readouterr().out == line and report["warning"] == line.strip()
    library = mvcommon.load_library()
    assert {f"mov-en-2021-gpaother{i}" for i in (1, 2, 3)} <= set(library)   # every other write survived
    assert "gphotos" not in library[EP]                                       # ours was never written


# ================================================================== never raises

def test_a_corrupt_library_is_one_warning_line_never_an_exit(world, capsys):
    world["sandbox"]["lib_anime"].write_text("{not json", encoding="utf-8")
    before = _files(world["sandbox"])
    for dry_run in (True, False):
        report = main.gphotos_apply([_confirm(EP, _sha_from(world, EP), "tv", 0)], dry_run=dry_run)
        assert report["status"] == "error" and not report["saved"]
    out = capsys.readouterr().out
    warning = ("⚠️  Google Photos identity not applied (the library could not be loaded) — "
               "the library was not changed.")
    assert out.count(warning) == 2 and "CRITICAL" in out               # load_library's own message stays
    assert _files(world["sandbox"]) == before


def test_a_failing_save_is_one_warning_line(world, monkeypatch, capsys):
    def full_disk(_data):
        raise OSError(28, "No space left on device\nsecond line")

    monkeypatch.setattr(main, "save_library", full_disk)
    report = main.gphotos_apply([_confirm(EP, _sha(EP), "tv", 0)], dry_run=False)
    assert report["status"] == "error" and not report["saved"]
    out = capsys.readouterr().out
    assert out.count("\n") == 1 and out.startswith("⚠️  Google Photos identity not applied (OSError: ")
    assert out.rstrip().endswith("the library save was interrupted — re-run to finish it.")


def test_an_unexpected_error_is_one_warning_line_and_changes_nothing(world, monkeypatch, capsys):
    def boom(*_a, **_k):
        raise RuntimeError("unexpected")

    monkeypatch.setattr(mvcommon, "apply_learned", boom)
    before = _files(world["sandbox"])
    report = main.gphotos_apply([_confirm(EP, _sha(EP), "tv", 0)], dry_run=False)
    assert report["status"] == "error"
    assert capsys.readouterr().out == ("⚠️  Google Photos identity not applied (RuntimeError: unexpected) — "
                                       "the library was not changed.\n")
    assert _files(world["sandbox"]) == before


def test_an_unreadable_sidecar_is_one_warning_line(world, monkeypatch, capsys):
    def unreadable(*_a, **_k):
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(mvcommon, "read_learned", unreadable)
    assert main.merge_learned_gphotos() is None
    out = capsys.readouterr().out
    assert out.count("\n") == 1 and out.startswith("⚠️  Google Photos identity not merged (learned records unreadable")


def test_ctrl_c_is_never_swallowed(world, monkeypatch):
    def interrupted():
        raise KeyboardInterrupt

    monkeypatch.setattr(main, "load_library", interrupted)
    with pytest.raises(KeyboardInterrupt):
        main.gphotos_apply([_confirm(EP, _sha_from(world, EP), "tv", 0)], dry_run=False)


def test_the_engine_never_opens_a_rollback_journal(world, monkeypatch, tmp_path):
    """P-10 / OD-3: no rollback-journal involvement — rollback behavior is untouched."""
    class Forbidden:
        def __init__(self, *_a, **_k):
            raise AssertionError("the apply engine must never open a RollbackJournal")

    monkeypatch.setattr(main, "RollbackJournal", Forbidden)
    mvcommon.append_learned("tv", _confirm(EP, _sha(EP), "tv", 0))
    assert main.gphotos_apply(mvcommon.read_learned())["status"] == "dry-run"
    assert main.merge_learned_gphotos()["status"] == "saved"
    assert not [p for p in tmp_path.rglob("*") if p.name == main.TXN_JOURNAL_NAME]


# ================================================================== failed_objects.csv (P-18)

def _failed_rows():
    with open(mvcommon.failed_csv_path(), "rb") as fh:
        text = fh.read().decode("utf-8-sig")
    return list(csv.DictReader(io.StringIO(text, newline="")))


def _seed_failed(world):
    for mid, account in ((EP, "tv"), (OTHER, "others")):
        mvcommon.append_failed({"account": account, "manual_id": mid, "sha256": _sha(mid), "label": "whole",
                                "reason": "not-found"})
    mvcommon.export_failed_csv(mvcommon.load_library())
    assert [r["manual_id"] for r in _failed_rows()] == [OTHER, EP]


def test_an_apply_refreshes_failed_csv_so_confirmed_objects_drop_off(world):
    _seed_failed(world)
    report = main.gphotos_apply([_confirm(EP, _sha(EP), "tv", 0)], dry_run=False)
    assert report["failed_csv"] == {"path": mvcommon.failed_csv_path(), "rows": 1, "dropped_confirmed": 1,
                                    "dropped_orphans": 0}
    assert [r["manual_id"] for r in _failed_rows()] == [OTHER]
    # an apply that changes nothing still re-syncs the CSV with the failed list (e.g. a fetch's new rows)
    mvcommon.append_failed({"account": "movies", "manual_id": MOVIE, "sha256": _sha(MOVIE), "label": "whole",
                            "reason": "ambiguous"})
    again = main.gphotos_apply([_confirm(EP, _sha(EP), "tv", 0)], dry_run=False)
    assert again["status"] == "up-to-date" and again["failed_csv"]["rows"] == 2
    assert [r["manual_id"] for r in _failed_rows()] == [MOVIE, OTHER]


def test_an_edited_failed_csv_is_never_overwritten(world, capsys):
    """The user works failed_objects.csv in Excel (P-18, OD-5): regenerating it would empty the editable
    columns, so an apply — e.g. the post-fetch merge — leaves an edited CSV exactly as the user saved it."""
    _seed_failed(world)
    rows = _failed_rows()
    rows[0].update(decision="search", new_search_term="GPA Final 2024 full match")      # the OTHER row
    mvcommon.write_failed_csv(mvcommon.failed_csv_path(), rows)
    with open(mvcommon.failed_csv_path(), "rb") as fh:
        edited = fh.read()
    report = main.gphotos_apply([_confirm(EP, _sha(EP), "tv", 0)], dry_run=False)
    assert report["status"] == "saved"
    assert report["failed_csv"] == {"path": mvcommon.failed_csv_path(), "kept": True, "edited_rows": 1}
    with open(mvcommon.failed_csv_path(), "rb") as fh:
        assert fh.read() == edited
    assert capsys.readouterr().out == ""                              # the engine itself prints no note
    # the post-fetch merge tells the user, after its summary line
    mvcommon.append_learned("movies", _confirm(MOVIE, _sha(MOVIE), "movies", 2))
    main.merge_learned_gphotos()
    assert capsys.readouterr().out == (
        "   > Google Photos identity: 1 object(s) updated in the library, 1 newly confirmed in Google Photos.\n"
        "   > failed_objects.csv holds 1 edited row(s) — left as is (it is regenerated once they are imported).\n")
    with open(mvcommon.failed_csv_path(), "rb") as fh:
        assert fh.read() == edited
    # a CSV that no longer reads as a failed-objects list is left alone too
    with open(mvcommon.failed_csv_path(), "w", encoding="utf-8") as fh:
        fh.write("name;size\nsomething;1\n")
    report = main.gphotos_apply([_confirm(OTHER, _sha(OTHER), "others", 1)], dry_run=False)
    assert report["failed_csv"] == {"path": mvcommon.failed_csv_path(), "kept": True, "edited_rows": None}


def test_a_locked_failed_csv_is_one_warning_line_and_the_apply_stands(world, monkeypatch, capsys):
    mvcommon.append_failed({"account": "tv", "manual_id": EP, "sha256": _sha(EP), "label": "whole",
                            "reason": "not-found"})

    def locked(_library):
        raise PermissionError(13, "The process cannot access the file because it is being used")

    monkeypatch.setattr(mvcommon, "export_failed_csv", locked)
    report = main.gphotos_apply([_confirm(EP, _sha(EP), "tv", 0)], dry_run=False)
    assert report["status"] == "saved" and report["saved"] and report["failed_csv"] is None
    out = capsys.readouterr().out
    assert out.count("\n") == 1 and out.startswith("⚠️  failed_objects.csv not refreshed (PermissionError")
    assert _objects(EP)["whole"]["cloud_state"] == "confirmed"


# ================================================================== the report contract (Steps 9 / 10 / 13 / 14)

def test_the_report_shape_is_the_documented_contract(world):
    report = main.gphotos_apply([_confirm(EP, _sha(EP), "tv", 0)])
    assert list(report) == ["status", "dry_run", "records", "invalid", "objects", "changed", "unchanged",
                            "confirmed", "conflicts", "orphans", "saved", "attempts", "failed_csv", "warning"]
    (change,) = report["changed"]
    assert list(change) == ["manual_id", "label", "kind", "sha256", "action", "fields"]
    assert change["fields"] == {
        "v": [None, 1], "sha256": [None, _sha(EP)],
        "accounts.tv.photo_id": [None, PID[0]], "accounts.tv.taken_ms": [None, TAKEN_MS],
        "accounts.tv.upload_ms": [None, UPLOAD_MS], "accounts.tv.verified_at": [None, T(1)],
        "accounts.tv.verified_by": [None, "dedupkey"], "accounts.tv.source": [None, "check"],
        "accounts.tv.approach.best": [None, "hash"], "accounts.tv.approach.ok": [None, ["hash"]],
        "accounts.tv.approach.at": [None, T(1)], "accounts.tv.approach.via": [None, "rpc"],
        "accounts.tv.confirmed": [None, True], "home_account": [None, "tv"]}


# ================================================================== the post-fetch hook (cmd_dispatch_fetch)

CHILD_LINES = ["🔹 PROCESSING: GPA.Show.S02E02E03.mkv (abc123)\n", "     ✅ MOVED: GPA.Show.S02E02E03.mkv\n",
               "   ✅ ENTRY COMPLETE.\n"]


class _Child:
    """A fake mainfetch child: streams CHILD_LINES; `on_line(i)` runs after line i was read (the child's own
    side effects, e.g. appending learned records); wait() logs its exit."""

    def __init__(self, state):
        self._state = state

    @property
    def stdout(self):
        for i, line in enumerate(self._state["lines"]):
            yield line
            if self._state["on_line"]:
                self._state["on_line"](i)

    def wait(self):
        self._state["events"].append("child exited")
        return 0


@pytest.fixture()
def fake_fetch(monkeypatch):
    """Replaces main.subprocess.Popen (the fetch child) — records argv / kwargs; no process is spawned."""
    state = {"argv": None, "kwargs": None, "events": [], "lines": list(CHILD_LINES), "on_line": None,
             "raise": None}

    def popen(cmd, **kwargs):
        if state["raise"] is not None:
            raise state["raise"]
        state["argv"], state["kwargs"] = list(cmd), kwargs
        state["events"].append("spawned")
        return _Child(state)

    monkeypatch.setattr(main.subprocess, "Popen", popen)
    return state


def test_dispatch_fetch_argv_streaming_and_output_unchanged_when_nothing_is_learned(world, fake_fetch, io_calls,
                                                                                   capsys):
    before, mtimes = _files(world["sandbox"]), _mtimes(world["sandbox"])
    main.cmd_dispatch_fetch(EP, "1-3", fetch_extras=True, temp_dir=r"D:\MV_fetch")
    assert fake_fetch["argv"] == ["python", "mainfetch.py", "fetch", EP, "episodes", "1-3", "--fetchExtras",
                                  "tempdir", r"D:\MV_fetch"]
    kwargs = fake_fetch["kwargs"]
    assert (kwargs["stdout"], kwargs["stderr"], kwargs["text"], kwargs["bufsize"], kwargs["encoding"],
            kwargs["errors"]) == (subprocess.PIPE, subprocess.STDOUT, True, 1, "utf-8", "replace")
    assert kwargs["env"]["PYTHONIOENCODING"] == "utf-8"
    assert capsys.readouterr().out == "   > 🚀 Dispatching Batch Fetch for episodes 1-3...\n" + "".join(CHILD_LINES)
    main.cmd_dispatch_fetch(EP)
    assert fake_fetch["argv"] == ["python", "mainfetch.py", "fetch", EP]
    assert capsys.readouterr().out == "   > 🚀 Dispatching Fetch...\n" + "".join(CHILD_LINES)
    assert io_calls == {"load": 0, "save": 0}
    assert _files(world["sandbox"]) == before and _mtimes(world["sandbox"]) == mtimes


def test_dispatch_fetch_merges_what_the_child_learned_after_it_exits(world, fake_fetch, monkeypatch, capsys):
    events = fake_fetch["events"]
    key = gpweb.dedup_key(_sha1("the downloaded EP bytes"))

    def child_learns(i):
        if i == 1:  # the download verified by SHA-256; mainfetch appends (the library stays read-only)
            mvcommon.append_learned("tv", {"manual_id": EP, "sha256": _sha(EP), "label": "whole", "source": "fetch",
                                           "proof": "sha256", "dedup_key": key, "photo_id": PID[5],
                                           "upload_ms": UPLOAD_MS})

    fake_fetch["on_line"] = child_learns
    real_load = main.load_library

    def load():
        events.append("library loaded")
        return real_load()

    monkeypatch.setattr(main, "load_library", load)
    main.cmd_dispatch_fetch(EP)
    assert events == ["spawned", "child exited", "library loaded"]
    assert capsys.readouterr().out == ("   > 🚀 Dispatching Fetch...\n" + "".join(CHILD_LINES)
                                       + "   > Google Photos identity: 1 object(s) updated in the library, "
                                         "1 newly confirmed in Google Photos.\n")
    block = _objects(EP)["whole"]["block"]
    assert block["accounts"]["tv"]["photo_id"] == PID[5] and block["accounts"]["tv"]["verified_by"] == "sha256"
    assert block["dedup_key"] == key                                   # the download taught the content key
    fake_fetch["on_line"] = None
    main.cmd_dispatch_fetch(EP)                                        # idempotent: nothing new -> silent
    assert capsys.readouterr().out == "   > 🚀 Dispatching Fetch...\n" + "".join(CHILD_LINES)


def test_the_merge_output_carries_no_web_progress_marker(world, capsys):
    sha1 = _capture_ep(world)
    for record in (_confirm(EP, _sha(EP), "tv", 0, dedup_key=gpweb.dedup_key(sha1)),
                   _confirm(OTHER, _sha(OTHER), "others", 1),
                   _rec(EP, _sha(EP), "movies", source="map", proof="filename", photo_id=PID[2], upload_ms=5,
                        dedup_key=gpweb.dedup_key(_sha1("other bytes")))):
        mvcommon.append_learned(record["account"], record)
    report = main.merge_learned_gphotos()
    assert report["status"] == "saved" and len(report["conflicts"]) == 1
    out = capsys.readouterr().out
    assert out == ("   > Google Photos identity: 2 object(s) updated in the library, 2 newly confirmed in "
                   "Google Photos, 1 conflict(s) not applied.\n")
    fixed_lines = (out, main._GP_BUSY, "Google Photos identity not applied", "Google Photos identity not merged",
                   "failed_objects.csv holds 1 edited row(s) — left as is (it is regenerated once they are "
                   "imported).", "failed_objects.csv no longer reads as the failed list — left as is.",
                   "failed_objects.csv not refreshed")
    for text in fixed_lines:
        assert not any(marker in text for marker in _MARKERS), text
    web = pytest.importorskip("webui.server")
    assert web._parse_progress(out) == {"done": 0, "total": 0}


def test_ctrl_c_during_the_fetch_skips_the_merge(world, fake_fetch, io_calls):
    mvcommon.append_learned("tv", _confirm(EP, _sha(EP), "tv", 0))

    def interrupted(_i):
        raise KeyboardInterrupt

    fake_fetch["on_line"] = interrupted
    with pytest.raises(KeyboardInterrupt):
        main.cmd_dispatch_fetch(EP)
    assert fake_fetch["events"] == ["spawned", "child exited"]
    assert io_calls == {"load": 0, "save": 0}                           # no library write after the user stopped


def test_a_spawn_failure_still_merges_pending_records(world, fake_fetch, capsys):
    mvcommon.append_learned("tv", _confirm(EP, _sha(EP), "tv", 0))
    fake_fetch["raise"] = FileNotFoundError(2, "python not found")
    main.cmd_dispatch_fetch(EP)
    assert capsys.readouterr().out.splitlines() == [
        "   > 🚀 Dispatching Fetch...", "❌ Error running fetch script: [Errno 2] python not found",
        "   > Google Photos identity: 1 object(s) updated in the library, 1 newly confirmed in Google Photos."]


def test_fetch_restore_merges_before_its_restore_phase(world, mock_device, capsys):
    mvcommon.append_learned("others", _confirm(OTHER, _sha(OTHER), "others", 0))
    main.cmd_fetch_restore(OTHER)
    out = capsys.readouterr().out
    merge = out.index("   > Google Photos identity: 1 object(s) updated in the library")
    assert out.index("✅ ENTRY COMPLETE.") < merge < out.index(">>> STARTING RESTORE PHASE")
    assert "FETCH & RESTORE COMPLETE" in out
    assert _objects(OTHER)["whole"]["cloud_state"] == "confirmed"
