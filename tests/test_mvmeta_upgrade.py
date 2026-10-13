"""IMP-C25 Step 13c — `mvmeta_upgrade`: v2 phone sidecars for the existing library (OD-4 ruling; G0 question 2 answered
2026-09-28 "Yes, with --apply only").

Every push leaves `<base> [<short_id>].mvmeta.json` next to what it uploaded (main.write_remote_mvmeta). Since Step 5 a new
push writes it as v2 (per chunk sha1 / dedup_key / date_utc / uploaded_name); the existing library's sidecars are v1.
Pinned here:
  (a) the PURE core — which facts are PROVEN for an object's own bytes (the content key never from a filename or manual
      proof, never from an untrusted block; date_utc from the file, else a confirmed record; the account of the
      confirmed copy), the sidecar's exact bytes, and the upgrade itself: missing keys appended, every v1 key
      byte-identical (the new bytes minus the added keys ARE the original bytes), a disagreeing value / other bytes /
      another entry / an unknown version or form -> left as it is;
  (b) the device path — mvmeta_upgrade's OWN adb allowlist (pull; with --apply push `<sidecar>.tmp` + `shell mv` of it
      over the sidecar; every command names its phone with -s) while the device check's read-only allowlist is NOT
      widened; the --apply order keep-the-original -> push .tmp (the original's mtime) -> mv -> pull back -> stat, a
      failure at every stage, and that a dry run cannot write;
The fake adb runs every command against a real folder standing for the phone's Media root and checks it against THIS
module's own allowlist (independent of main's), splitting `adb shell` commands the way the device shell does.
Synthetic data only: no real adb, phone, Google Photos, C:\\Media or library file. Every sidecar here is produced by the
REAL write_remote_mvmeta (at version 1 for the existing library's sidecars, at today's version 2 for a new push's).
"""
import copy
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import types
from datetime import datetime, timezone

import pytest

import gpcapture
import gpweb
import main
import mvcommon
from test_device_inventory import device_words   # the device shell's word splitting (Step 8's test helper)

SERIES, MOVIES, EXTRA = "FAKESER0002", "FAKEMOV0001", "FAKEXTRA0003"
ROOT = "/sdcard/Media/"


@pytest.fixture(autouse=True)
def _no_real_adb(monkeypatch, tmp_path):
    """Every test of this module: nothing reaches a real adb (a test installs its fake phones), and the system temp
    folder is tmp_path/systmp, so a test can prove a run leaves nothing behind in it."""
    def refuse(argv, *args, **kwargs):
        raise AssertionError(f"a test reached the real subprocess.run: {argv!r}")

    monkeypatch.setattr(main.subprocess, "run", refuse)
    systmp = tmp_path / "systmp"
    systmp.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(systmp))


# ============================================================================ synthetic library objects

def _sha(tag):
    return hashlib.sha256(tag.encode("utf-8")).hexdigest()


def _sha1(tag):
    return hashlib.sha1(tag.encode("utf-8")).hexdigest()


def _key(tag):
    return gpcapture.dedup_key(_sha1(tag))


def _pid(tag):
    return f"AF1QipFAKE{tag}".ljust(24, "x")


def leaf(mid, folder, filename, chunks=0, holder=False, status="archived"):
    """A library leaf as cmd_prep / cmd_push leave it (synthetic hashes); `chunks` > 0 -> split, + a FLAC holder."""
    sid = mvcommon.generate_short_id(mid)
    stem, ext = os.path.splitext(filename)
    entry = {"short_id": sid, "filename": filename, "folder_path": str(folder), "status": status, "uploaded": True,
             "search_term": f"{stem} [{sid}]{ext}", "hash": _sha(mid),
             "metadata": {"title": "Amélie Ünïcødé ' \" \\ test", "year": 2001, "genre": ["Drama"]},
             "tech_spec": {"resolution": "1080p", "width_height": "1920x1080", "size_bytes": 4_000_000,
                           "frame_rate": 23.976, "subtitles": ["eng", "tam"]}}
    if chunks:
        entry["split_info"] = {"is_split": True, "method": "SIZE_GB", "val": "4", "total_chunks": chunks,
                               "chunks": [{"filename": f"{stem} [{sid}].chunk.{i:03d}.mkv", "hash": _sha(f"{mid}#{i}")}
                                          for i in range(1, chunks + 1)]}
        if holder:
            entry["split_info"]["carried_out_tracks"] = [{
                "track_id": 2, "codec": "A_FLAC", "language": "eng", "holder_filename": f"{stem} [{sid}].holder.mkv",
                "holder_hash": _sha(f"{mid}#holder")}]
    return entry


def extra(title_id, group_rel, sub_rel, chunks=0):
    """An extras item as scan_extras_folders / push_one_extra leave it."""
    filename = sub_rel.split("/")[-1]
    sid = mvcommon.generate_short_id(f"{title_id}::{group_rel}/{sub_rel}")
    stem, ext = os.path.splitext(filename)
    item = {"filename": filename, "sub_rel": sub_rel, "short_id": sid, "hash": _sha(f"{title_id}::{sub_rel}"),
            "status": "archived", "uploaded": True, "search_term": f"{stem} [{sid}]{ext}",
            "tech_spec": {"size_bytes": 5000}}
    if chunks:
        item["split_info"] = {"is_split": True, "method": "COUNT", "val": str(chunks), "total_chunks": chunks,
                              "chunks": [{"filename": f"{stem} [{sid}].chunk.{i:03d}.mkv", "hash": _sha(f"{sid}#{i}")}
                                         for i in range(1, chunks + 1)]}
    return item


def write_capture(folder, manual_id, short_id, prep=None, pushes=()):
    """A gpcapture file (capture v2 shape) at <folder>/<short_id>.gpcapture.json. prep: the master's `source` facts +
    uploaded_name; pushes: [[object facts, ...], ...]."""
    os.makedirs(folder, exist_ok=True)
    doc = {"capture_version": 2, "manual_id": manual_id, "short_id": short_id}
    if prep:
        doc["prep"] = {"captured_at": "2026-09-29T10:00:00Z", "source": {k: v for k, v in prep.items()
                                                                       if k != "uploaded_name"},
                       "uploaded_name": prep.get("uploaded_name"), "tech": {"width_height": "1920x1080"}}
    doc["pushes"] = [{"captured_at": f"2026-09-29T1{n}:00:00Z", "device_id": SERIES, "device_tz": "Asia/Singapore",
                      "device_tz_offset_min": 480, "remote_dir": "/sdcard/Media/X", "chunk_range": None,
                      "complete": True, "objects": list(objects)} for n, objects in enumerate(pushes, 1)]
    with open(gpcapture.capture_path(str(folder), short_id), "w", encoding="utf-8") as fh:
        json.dump(doc, fh)


def cap_facts(sha256, tag, uploaded_name, role="whole", index=None, date="2022-02-05T11:49:46Z"):
    """One capture object's facts for the bytes `sha256` (SHA-1 = _sha1(tag))."""
    return {"role": role, "index": index, "sha256": sha256, "sha1": _sha1(tag), "dedup_key": _key(tag),
            "date_utc": date, "uploaded_name": uploaded_name, "local_name": uploaded_name, "size_bytes": 1000}


def record(account, proof, tag, confirmed=True, taken_ms=1_644_061_786_000, upload_ms=1_700_000_000_000,
           dedup=True):
    """An account record as the apply engine folds it (P-1): `tag` names the cloud item (its photo id and dedupKey)."""
    rec = {"photo_id": _pid(tag), "taken_ms": taken_ms, "tz_offset_ms": 28_800_000, "upload_ms": upload_ms,
           "gp_filename": f"{tag}.mkv", "verified_at": "2026-10-10T10:00:00Z", "verified_by": proof,
           "source": "manual" if proof == "manual" else "check"}
    if dedup:
        rec["dedup_key"] = _key(tag)
    if confirmed:
        rec["confirmed"] = True
    if upload_ms is None:
        rec.pop("upload_ms")
    return {account: rec}


def objects_of(library, mid):
    """mvcommon.iter_entry_objects of one entry (its captures read, its block trusted or not — the REAL rules)."""
    return list(mvcommon.iter_entry_objects(mid, library[mid]))


def written_sidecar(monkeypatch, entry, manual_id, version=1, identity=None, remote_dir="/sdcard/Media/X"):
    """(phone path, bytes) the REAL write_remote_mvmeta pushes for `entry` when its schema version is `version` (1 =
    every sidecar of the existing library, written before Step 5)."""
    pushed = {}

    def run(argv, check=False, **kwargs):
        with open(argv[-2], "rb") as fh:          # the temp sidecar, read before the writer removes it
            pushed["data"] = fh.read()
        pushed["path"] = argv[-1]
        return types.SimpleNamespace(returncode=0)

    with monkeypatch.context() as m:
        m.setattr(main, "MVMETA_SCHEMA_VERSION", version)
        m.setattr(main.subprocess, "run", run)
        assert main.write_remote_mvmeta(["adb", "-s", SERIES], remote_dir, manual_id, entry, identity=identity) is True
    return pushed["path"], pushed["data"]


# ============================================================================ (a) the pure core

def test_the_upgrade_writes_today_s_v2_keys_plus_the_account():
    assert gpcapture.MVMETA_KEYS == ("sha1", "dedup_key", "date_utc", "uploaded_name")    # Step 5's v2 keys
    assert main.MVMETA_UPGRADE_KEYS == gpcapture.MVMETA_KEYS + ("gphotos_account",)
    # drift pin: an upgraded sidecar carries the push writer's version — a v3 writer must decide what the upgrade does
    assert main.MVMETA_UPGRADE_VERSION == main.MVMETA_SCHEMA_VERSION == 2
    assert main.MVMETA_UPGRADE_APPLY_ENABLED is True                     # G0 question 2: "Yes, with --apply only"
    assert main.MVMETA_UPGRADE_DISABLED == "device rewrites disabled (OD-4 clarification)"


@pytest.fixture()
def lib(tmp_path):
    """A small library over all four prefixes (folders under tmp_path), no captures and no blocks yet."""
    root = tmp_path / "Media"
    library = {
        "mov-en-2001-amelie": leaf("mov-en-2001-amelie", root / "Movies" / "Amelie (2001)", "Amelie.2001.mkv"),
        "mov-en-2019-split": leaf("mov-en-2019-split", root / "Movies" / "Split (2019)", "Split.2019.mkv", chunks=3,
                                  holder=True),
        "tv-en-2015-mushow-s02e03": leaf("tv-en-2015-mushow-s02e03", root / "Series" / "Mu Show" / "Season 02",
                                         "Mu.Show.S02E03.mkv"),
        "ani-ja-2006-muanime07": leaf("ani-ja-2006-muanime07", root / "Anime" / "Mu Anime", "Mu Anime - 07.mkv"),
        "oth-en-2024-mufinal-e01": leaf("oth-en-2024-mufinal-e01", root / "Sports" / "Mu Final", "Mu.Final.mkv",
                                        chunks=2),
    }
    library["mov-en-2001-amelie"]["extras"] = {"groups": {"Specials": {"added_date": "2026-09-01", "items": [
        extra("mov-en-2001-amelie", "Specials", "BTS.mkv"),
        extra("mov-en-2001-amelie", "Specials", "Bonus/Making Of.mkv", chunks=2)]}}}
    return library


def _whole(library, mid="tv-en-2015-mushow-s02e03"):
    (obj,) = [o for o in objects_of(library, mid) if o["kind"] == "whole"]
    return obj


def test_capture_facts_are_proven_for_the_bytes_they_were_read_from(lib):
    mid = "tv-en-2015-mushow-s02e03"
    entry = lib[mid]
    write_capture(entry["folder_path"], mid, entry["short_id"],
                  prep=cap_facts(entry["hash"], "ep3", entry["search_term"]))
    facts, why = main._mu_facts(_whole(lib))
    assert why is None
    assert facts == {"sha1": _sha1("ep3"), "dedup_key": _key("ep3"), "date_utc": "2022-02-05T11:49:46Z",
                     "uploaded_name": entry["search_term"]}               # no confirmed copy -> no account


def test_a_confirmed_content_proven_copy_adds_its_account(lib):
    mid = "tv-en-2015-mushow-s02e03"
    entry = lib[mid]
    write_capture(entry["folder_path"], mid, entry["short_id"],
                  prep=cap_facts(entry["hash"], "ep3", entry["search_term"]))
    entry["gphotos"] = mvcommon.new_gphotos_block(entry["hash"], sha1=_sha1("ep3"), date_utc="2022-02-05T11:49:46Z",
                                                  accounts=record("tv", "dedupkey", "ep3"), home_account="tv")
    facts, why = main._mu_facts(_whole(lib))
    assert why is None and facts["gphotos_account"] == "tv"
    assert (facts["sha1"], facts["dedup_key"], facts["date_utc"]) == (_sha1("ep3"), _key("ep3"), "2022-02-05T11:49:46Z")


@pytest.mark.parametrize("proof", ["filename", "manual"])
def test_a_filename_or_manual_proof_never_gives_a_content_key(lib, proof):
    """THE rule of Step 13c (PLAN P-5): a legacy object (its local copy is a dummy, no capture) is confirmed by its NAME
    only — the cloud item's dedupKey belongs to the cloud copy and is never written as these bytes' content key. Its
    date (the confirmed taken time) and account are still facts; a manual match is not even confirmed."""
    mid = "tv-en-2015-mushow-s02e03"
    lib[mid]["gphotos"] = mvcommon.new_gphotos_block(
        lib[mid]["hash"], accounts=record("tv", proof, "legacy-ep3", confirmed=proof != "manual"), home_account="tv")
    facts, why = main._mu_facts(_whole(lib))
    assert why is None
    assert "sha1" not in facts and "dedup_key" not in facts
    if proof == "filename":
        assert facts == {"date_utc": "2022-02-05T11:49:46Z", "gphotos_account": "tv"}    # 1644061786000 ms (F20)
    else:
        assert facts == {}


def test_a_downloaded_copy_proves_the_content_key_through_its_record(lib):
    """A fetch that downloaded the bytes and matched their SHA-256 records proof sha256 with the bytes' own dedupKey."""
    mid = "tv-en-2015-mushow-s02e03"
    lib[mid]["gphotos"] = mvcommon.new_gphotos_block(lib[mid]["hash"], accounts=record("tv", "sha256", "dl-ep3"),
                                                     home_account="tv")
    facts, _why = main._mu_facts(_whole(lib))
    assert (facts["sha1"], facts["dedup_key"], facts["gphotos_account"]) == (_sha1("dl-ep3"), _key("dl-ep3"), "tv")


def test_an_untrusted_block_is_ignored_entirely(lib):
    """P-2: a block pinned to OTHER bytes (re-mastered / re-split since) describes nothing about these bytes."""
    mid = "tv-en-2015-mushow-s02e03"
    lib[mid]["gphotos"] = mvcommon.new_gphotos_block(
        _sha("older bytes"), sha1=_sha1("older"), date_utc="2020-01-01T00:00:00Z", uploaded_name="Old [x].mkv",
        accounts=record("tv", "dedupkey", "older"), home_account="tv")
    assert _whole(lib)["block"] is None                                  # the real reader's rule
    assert main._mu_facts(_whole(lib)) == ({}, None)


def test_an_unconfirmed_record_gives_neither_date_nor_account(lib):
    mid = "tv-en-2015-mushow-s02e03"
    lib[mid]["gphotos"] = mvcommon.new_gphotos_block(
        lib[mid]["hash"], accounts=record("tv", "filename", "ep3", confirmed=False, upload_ms=None))
    assert main._mu_facts(_whole(lib)) == ({}, None)


@pytest.mark.parametrize("home,expected", [("movies", "movies"), ("tv", "tv"), (None, None), ("anime", None)])
def test_the_account_is_the_confirmed_home_else_the_only_confirmed_one(lib, home, expected):
    mid = "tv-en-2015-mushow-s02e03"
    accounts = {**record("movies", "filename", "m-copy"), **record("tv", "filename", "t-copy", taken_ms=1_700_000_123_456)}
    lib[mid]["gphotos"] = mvcommon.new_gphotos_block(lib[mid]["hash"], accounts=accounts, home_account=home)
    facts, _why = main._mu_facts(_whole(lib))
    assert facts.get("gphotos_account") == expected
    assert facts.get("date_utc") == {"movies": "2022-02-05T11:49:46Z", "tv": "2023-11-14T22:15:23Z", None: None}[expected]
    lib[mid]["gphotos"] = mvcommon.new_gphotos_block(lib[mid]["hash"], accounts=record("anime", "filename", "a"),
                                                     home_account=home)
    assert main._mu_facts(_whole(lib))[0]["gphotos_account"] == "anime"   # the only confirmed one wins anyway


def test_the_file_s_own_dateutc_wins_over_the_cloud_taken_time(lib):
    mid = "tv-en-2015-mushow-s02e03"
    entry = lib[mid]
    write_capture(entry["folder_path"], mid, entry["short_id"],
                  prep=cap_facts(entry["hash"], "ep3", entry["search_term"], date="2021-01-01T00:00:01Z"))
    entry["gphotos"] = mvcommon.new_gphotos_block(entry["hash"], accounts=record("tv", "dedupkey", "ep3"),
                                                  home_account="tv")
    assert main._mu_facts(_whole(lib))[0]["date_utc"] == "2021-01-01T00:00:01Z"
    os.remove(gpcapture.capture_path(entry["folder_path"], entry["short_id"]))
    entry["gphotos"]["date_utc"] = "2021-01-01T00:00:02Z"                  # a block's DateUTC (only a capture writes it)
    assert main._mu_facts(_whole(lib))[0]["date_utc"] == "2021-01-01T00:00:02Z"


def test_the_block_s_uploaded_name_wins_over_the_capture_s(lib):
    mid = "tv-en-2015-mushow-s02e03"
    entry = lib[mid]
    write_capture(entry["folder_path"], mid, entry["short_id"],
                  prep=cap_facts(entry["hash"], "ep3", entry["search_term"]))
    entry["gphotos"] = mvcommon.new_gphotos_block(entry["hash"], uploaded_name="Mu.Show.S02E03.mkv")   # --repair names
    assert main._mu_facts(_whole(lib))[0]["uploaded_name"] == "Mu.Show.S02E03.mkv"


@pytest.mark.parametrize("clash", ["key", "date"])
def test_disagreeing_facts_leave_the_object_out(lib, clash):
    mid = "tv-en-2015-mushow-s02e03"
    entry = lib[mid]
    write_capture(entry["folder_path"], mid, entry["short_id"],
                  prep=cap_facts(entry["hash"], "ep3", entry["search_term"]))
    if clash == "key":       # a content-proven record of OTHER bytes than the capture's (the engine reports it)
        entry["gphotos"] = mvcommon.new_gphotos_block(entry["hash"], accounts=record("tv", "sha256", "not-ep3"))
    else:
        entry["gphotos"] = mvcommon.new_gphotos_block(entry["hash"], date_utc="2000-01-01T00:00:00Z")
    facts, why = main._mu_facts(_whole(lib))
    assert facts == {} and why in ("its proven content keys disagree", "its recorded DateUTCs disagree")


def test_taken_time_to_dateutc():
    assert main._mu_taken_utc(1_644_061_786_000) == "2022-02-05T11:49:46Z"     # RESEARCH F20 (S02E03)
    assert main._mu_taken_utc(1_644_061_786_999) == "2022-02-05T11:49:46Z"     # floored to the second
    for bad in (None, "1644061786000", 1.5e12, True, 10 ** 30):
        assert main._mu_taken_utc(bad) is None


def test_owners_are_entries_and_extras_items_without_their_flac_holders(lib):
    owners = main._mu_owners(mvcommon.iter_uploaded_objects(lib))
    by = {o["label"]: o for o in owners}
    split = lib["mov-en-2019-split"]
    assert by["mov-en-2019-split"]["hashes"] == [c["hash"] for c in split["split_info"]["chunks"]]   # no holder
    assert [o["kind"] for o in by["mov-en-2019-split"]["objects"]] == ["chunk"] * 3
    making = lib["mov-en-2001-amelie"]["extras"]["groups"]["Specials"]["items"][1]
    label = "mov-en-2001-amelie::Specials/Bonus/Making Of.mkv"
    assert (by[label]["manual_id"], by[label]["extra"], by[label]["short_id"]) == (
        "mov-en-2001-amelie", True, making["short_id"])
    assert by[label]["hashes"] == [c["hash"] for c in making["split_info"]["chunks"]]
    assert by["mov-en-2001-amelie"]["hashes"] == [lib["mov-en-2001-amelie"]["hash"]]
    assert {o["category"] for o in owners} == {"movies", "tv", "anime", "others"}
    assert len(owners) == 7                                                       # 5 entries + 2 extras items


# --------------------------------------------------------------------------- the sidecar's bytes

def test_the_real_writer_s_bytes_decode_and_re_encode_exactly(lib, monkeypatch):
    for mid in ("mov-en-2001-amelie", "mov-en-2019-split"):
        _path, raw = written_sidecar(monkeypatch, lib[mid], mid)
        doc, newline, why = main._mu_decode(raw)
        assert why is None and doc["version"] == 1 and newline == ("\r\n" if b"\r\n" in raw else "\n")
        assert main._mu_encode(doc, newline) == raw
        assert raw.decode("ascii")                                         # json.dump's ASCII: unicode is \u-escaped
    lf = raw.replace(b"\r\n", b"\n")                                       # a sidecar written on a non-Windows PC
    doc, newline, why = main._mu_decode(lf)
    assert (why, newline) == (None, "\n") and main._mu_encode(doc, newline) == lf


@pytest.mark.parametrize("edit", ["reindented", "trailing newline", "bom", "mixed line ends", "unicode written raw",
                                  "compact"])
def test_a_sidecar_not_in_the_writer_s_exact_form_is_refused(lib, monkeypatch, edit):
    """Only the writer's exact form can be rewritten keeping every byte (any other form would be re-serialized).
    A different key ORDER is still that form (json.dump of its own content), so it is not refused."""
    mid = "tv-en-2015-mushow-s02e03"
    _path, raw = written_sidecar(monkeypatch, lib[mid], mid)
    doc = json.loads(raw.decode("utf-8"))
    newline = "\r\n" if b"\r\n" in raw else "\n"
    edited = {
        "reindented": json.dumps(doc, indent=4).replace("\n", newline).encode("utf-8"),
        "trailing newline": raw + newline.encode("ascii"),
        "bom": b"\xef\xbb\xbf" + raw,
        "mixed line ends": raw.replace(b"\r\n", b"\n", 1) if b"\r\n" in raw else raw.replace(b"\n", b"\r\n", 1),
        "unicode written raw": json.dumps(doc, indent=2, ensure_ascii=False).replace("\n", newline).encode("utf-8"),
        "compact": json.dumps(doc).encode("utf-8"),
    }[edit]
    assert edited != raw
    assert main._mu_decode(edited)[0] is None
    reordered = main._mu_encode(dict(reversed(list(doc.items()))), newline)
    assert main._mu_decode(reordered)[0] == doc


@pytest.mark.parametrize("raw", [b"", b"{", b"\xff\xfe\x00", b"[1, 2]".replace(b"[", b"(")])
def test_a_sidecar_that_is_not_json_is_refused(raw):
    assert main._mu_decode(raw) == (None, None, "it is not a JSON file")


# --------------------------------------------------------------------------- the upgrade

def _facts_of(library, mid):
    return {o["sha256"]: main._mu_facts(o)[0] for o in objects_of(library, mid) if o["kind"] != "holder"}


def _owner(library, mid):
    (owner,) = [o for o in main._mu_owners(mvcommon.iter_uploaded_objects(library, [mid])) if not o["extra"]]
    return owner


def test_a_v1_whole_file_sidecar_gains_the_proven_keys_and_keeps_every_byte(lib, monkeypatch):
    mid = "tv-en-2015-mushow-s02e03"
    entry = lib[mid]
    write_capture(entry["folder_path"], mid, entry["short_id"],
                  prep=cap_facts(entry["hash"], "ep3", entry["search_term"]))
    entry["gphotos"] = mvcommon.new_gphotos_block(entry["hash"], sha1=_sha1("ep3"),
                                                  accounts=record("tv", "dedupkey", "ep3"), home_account="tv")
    _path, raw = written_sidecar(monkeypatch, entry, mid)
    old, newline, _why = main._mu_decode(raw)
    new, adds, why = main._mu_upgrade(old, _owner(lib, mid), _facts_of(lib, mid))
    assert why is None and adds == {k: 1 for k in main.MVMETA_UPGRADE_KEYS}
    assert new["version"] == 2 and old["version"] == 1                     # the input is never modified
    (chunk,) = new["chunks"]
    assert list(chunk) == ["filename", "hash", *main.MVMETA_UPGRADE_KEYS]   # appended after the v1 keys
    assert chunk == {"filename": entry["search_term"], "hash": entry["hash"], "sha1": _sha1("ep3"),
                     "dedup_key": _key("ep3"), "date_utc": "2022-02-05T11:49:46Z",
                     "uploaded_name": entry["search_term"], "gphotos_account": "tv"}
    assert {k: v for k, v in new.items() if k not in ("version", "chunks")} == \
        {k: v for k, v in old.items() if k not in ("version", "chunks")}   # every other v1 key: the same value ...
    assert main._mu_only_added(old, new, raw, newline) is True            # ... and the same bytes
    stripped = dict(new, version=1, chunks=[{"filename": chunk["filename"], "hash": chunk["hash"]}])
    assert main._mu_encode(stripped, newline) == raw


def test_a_split_sidecar_gains_keys_only_where_they_are_proven(lib, monkeypatch):
    mid = "mov-en-2019-split"
    entry = lib[mid]
    c1, c2, c3 = entry["split_info"]["chunks"]
    # chunk 1: a capture of its push · chunk 2: nothing known · chunk 3: a legacy confirmation by name only
    write_capture(entry["folder_path"], mid, entry["short_id"],
                  pushes=[[cap_facts(c1["hash"], "c1", c1["filename"], role="chunk", index=1)]])
    c3["gphotos"] = mvcommon.new_gphotos_block(c3["hash"], accounts=record("movies", "filename", "c3-cloud"),
                                               home_account="movies")
    _path, raw = written_sidecar(monkeypatch, entry, mid)
    old, newline, _why = main._mu_decode(raw)
    new, adds, why = main._mu_upgrade(old, _owner(lib, mid), _facts_of(lib, mid))
    assert why is None
    assert adds == {"sha1": 1, "dedup_key": 1, "date_utc": 2, "uploaded_name": 1, "gphotos_account": 1}
    n1, n2, n3 = new["chunks"]
    assert set(n1) == {"filename", "hash", "sha1", "dedup_key", "date_utc", "uploaded_name"}
    assert n2 == {"filename": c2["filename"], "hash": c2["hash"]}
    assert n3 == {"filename": c3["filename"], "hash": c3["hash"], "date_utc": "2022-02-05T11:49:46Z",
                  "gphotos_account": "movies"}                              # NO sha1 / dedup_key from a name match
    assert main._mu_only_added(old, new, raw, newline) is True


def test_a_v2_sidecar_of_a_new_push_only_gains_what_it_lacks(lib, monkeypatch):
    mid = "tv-en-2015-mushow-s02e03"
    entry = lib[mid]
    identity = {entry["hash"]: {"sha1": _sha1("ep3"), "dedup_key": _key("ep3"), "date_utc": "2022-02-05T11:49:46Z",
                                "uploaded_name": entry["search_term"]}}
    _path, raw = written_sidecar(monkeypatch, entry, mid, version=2, identity=identity)
    write_capture(entry["folder_path"], mid, entry["short_id"],
                  prep=cap_facts(entry["hash"], "ep3", entry["search_term"]))
    old, newline, _why = main._mu_decode(raw)
    assert main._mu_upgrade(old, _owner(lib, mid), _facts_of(lib, mid))[1] == {}    # up to date: nothing to add
    entry["gphotos"] = mvcommon.new_gphotos_block(entry["hash"], accounts=record("tv", "dedupkey", "ep3"),
                                                  home_account="tv")         # confirmed later (check_photos)
    new, adds, why = main._mu_upgrade(old, _owner(lib, mid), _facts_of(lib, mid))
    assert (why, adds, new["version"]) == (None, {"gphotos_account": 1}, 2)
    assert main._mu_only_added(old, new, raw, newline) is True


def test_a_present_value_is_compared_in_its_own_spelling(lib, monkeypatch):
    mid = "tv-en-2015-mushow-s02e03"
    entry = lib[mid]
    write_capture(entry["folder_path"], mid, entry["short_id"],
                  prep=cap_facts(entry["hash"], "ep3", entry["search_term"]))
    spelled = {entry["hash"]: {"sha1": _sha1("ep3").upper(),
                               "dedup_key": gpweb.sha1_hex_to_std_b64(_sha1("ep3"))}}   # standard base64, padded
    _path, raw = written_sidecar(monkeypatch, entry, mid, version=2, identity=spelled)
    old, _newline, _why = main._mu_decode(raw)
    new, adds, why = main._mu_upgrade(old, _owner(lib, mid), _facts_of(lib, mid))
    assert why is None and adds == {"date_utc": 1, "uploaded_name": 1}          # the key is the same bytes'
    assert new["chunks"][0]["sha1"] == _sha1("ep3").upper()                     # and is never re-spelled


@pytest.mark.parametrize("key", ["sha1", "dedup_key", "date_utc", "uploaded_name"])
def test_a_present_value_that_disagrees_leaves_the_sidecar_alone(lib, monkeypatch, key):
    mid = "tv-en-2015-mushow-s02e03"
    entry = lib[mid]
    write_capture(entry["folder_path"], mid, entry["short_id"],
                  prep=cap_facts(entry["hash"], "ep3", entry["search_term"]))
    other = {"sha1": _sha1("other"), "dedup_key": _key("other"), "date_utc": "2001-01-01T00:00:00Z",
             "uploaded_name": "Other [abcdef].mkv"}
    _path, raw = written_sidecar(monkeypatch, entry, mid, version=2, identity={entry["hash"]: {key: other[key]}})
    old, _newline, _why = main._mu_decode(raw)
    assert main._mu_upgrade(old, _owner(lib, mid), _facts_of(lib, mid)) == (
        None, None, f"its {key} of chunk 1/1 differs from the proven one")


@pytest.mark.parametrize("change,why", [
    (lambda d: d.update(manual_id="tv-en-2015-mushow-s02e04"), "not this entry"),
    (lambda d: d.update(short_id="ffffff"), "not this entry"),
    (lambda d: d["chunks"][0].update(hash=_sha("re-mastered")), "other bytes"),
    (lambda d: d["chunks"].append({"filename": "x", "hash": _sha("x")}), "other bytes"),
    (lambda d: d.update(chunks=[]), "other bytes"),
    (lambda d: d.update(chunks="nope"), "malformed"),
    (lambda d: d.update(chunks=[["filename", "hash"]]), "malformed"),
    (lambda d: d.update(version=3), "version 3"),
    (lambda d: d.update(version="1"), "version '1'"),
    (lambda d: d.update(version=True), "version True"),
    (lambda d: d.pop("version"), "version None"),
])
def test_a_sidecar_that_is_not_exactly_the_owner_s_is_left_alone(lib, monkeypatch, change, why):
    mid = "tv-en-2015-mushow-s02e03"
    entry = lib[mid]
    write_capture(entry["folder_path"], mid, entry["short_id"],
                  prep=cap_facts(entry["hash"], "ep3", entry["search_term"]))
    _path, raw = written_sidecar(monkeypatch, entry, mid)
    doc = json.loads(raw.decode("utf-8"))
    change(doc)
    new, adds, reason = main._mu_upgrade(doc, _owner(lib, mid), _facts_of(lib, mid))
    assert (new, adds) == (None, None) and why in reason


def test_the_upgrade_is_pure_and_adds_nothing_without_proven_facts(lib, monkeypatch):
    mid = "mov-en-2019-split"
    _path, raw = written_sidecar(monkeypatch, lib[mid], mid)
    old, _newline, _why = main._mu_decode(raw)
    before = copy.deepcopy(old)
    new, adds, why = main._mu_upgrade(old, _owner(lib, mid), _facts_of(lib, mid))
    assert (new, adds, why) == (old, {}, None) and new["version"] == 1    # no key -> no version change
    assert old == before


@pytest.mark.parametrize("damage", ["value changed", "key reordered", "key removed", "top-level key added",
                                    "foreign chunk key", "chunk dropped", "top-level value changed", "not a dict"])
def test_the_byte_identity_check_refuses_any_other_change(lib, monkeypatch, damage):
    """The guard right before a write: a builder bug that touched anything but appended v2 keys is caught."""
    mid = "mov-en-2019-split"
    entry = lib[mid]
    c1 = entry["split_info"]["chunks"][0]
    write_capture(entry["folder_path"], mid, entry["short_id"],
                  pushes=[[cap_facts(c1["hash"], "c1", c1["filename"], role="chunk", index=1)]])
    _path, raw = written_sidecar(monkeypatch, entry, mid)
    old, newline, _why = main._mu_decode(raw)
    new, _adds, _why = main._mu_upgrade(old, _owner(lib, mid), _facts_of(lib, mid))
    assert main._mu_only_added(old, new, raw, newline) is True
    bad = copy.deepcopy(new)
    if damage == "value changed":
        bad["chunks"][1]["filename"] = bad["chunks"][1]["filename"].upper()
    elif damage == "key reordered":
        bad["chunks"][0] = dict(reversed(list(bad["chunks"][0].items())))
    elif damage == "key removed":
        bad["chunks"][2].pop("hash")
    elif damage == "top-level key added":
        bad["gphotos_account"] = "movies"
    elif damage == "foreign chunk key":
        bad["chunks"][0]["photo_id"] = _pid("leak")
    elif damage == "chunk dropped":
        bad["chunks"].pop()
    elif damage == "top-level value changed":
        bad["folder_path"] = "elsewhere"
    else:
        bad = ["not", "a", "dict"]
    assert main._mu_only_added(old, bad, raw, newline) is False


# ============================================================================ the fake phones

ROOTS_ALL = ("/sdcard/Media/", "/storage/emulated/0/Media/")   # Step 8's listing roots (both address the same folder)
STAT_FORMAT = "%s %Y %n"
PUSH_NOW = 1_800_000_000      # the mtime a phone that does NOT keep a pushed file's mtime stamps it with
NOW = datetime(2026, 10, 14, 12, 0, 0, tzinfo=timezone.utc)


def result(code, stdout=b"", stderr=b""):
    return types.SimpleNamespace(returncode=code, stdout=stdout, stderr=stderr)


def crlf(lines):
    """Device output: real Pixels end every line with CRLF."""
    return "".join(line + "\r\n" for line in lines).encode("utf-8")


class Phone:
    """One fake phone whose Media root is the real folder `root` (both listing roots address it), read and written at
    call time. state: what `adb devices` lists (None = not attached). fail: {verb: mode} for every call of a verb
    (find / stat / pull / push / mv), or {(verb, n): mode} for its n-th call only (1-based); mode error | timeout |
    corrupt (push: the bytes that land are not the bytes sent). keeps_mtime=False: a push stamps PUSH_NOW instead of
    keeping the pushed file's mtime (RESEARCH F3: real phones keep it)."""

    def __init__(self, root, state="device", fail=None, keeps_mtime=True):
        self.root, self.state, self.fail, self.keeps_mtime = root, state, dict(fail or {}), keeps_mtime
        self.counts = {}
        root.mkdir(parents=True, exist_ok=True)

    def file(self, remote):
        """The folder path standing for a phone path."""
        for prefix in ROOTS_ALL:
            if remote.startswith(prefix):
                return self.root / remote[len(prefix):]
        raise AssertionError(f"not a Media path: {remote!r}")

    def put(self, rel, data, mtime):
        """Store a file on the phone -> its phone path."""
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        os.utime(path, (mtime, mtime))
        return ROOT + rel

    def files(self):
        """{phone path: (bytes, mtime)} of every file on the phone."""
        return {ROOT + p.relative_to(self.root).as_posix(): (p.read_bytes(), int(p.stat().st_mtime))
                for p in sorted(self.root.rglob("*")) if p.is_file()}

    def mode(self, verb):
        self.counts[verb] = self.counts.get(verb, 0) + 1
        return self.fail.get((verb, self.counts[verb]), self.fail.get(verb))


class FakeAdb:
    """A fake `adb` over fake phones, installed as main.subprocess.run (= the global subprocess.run, so nothing can
    reach a real adb). Every call must pass check() — THIS module's own allowlist of what mvmeta_upgrade may run,
    independent of main's — and is recorded in .calls as (argv, kwargs); a call that breaks a rule raises
    AssertionError AND is recorded in .violations (the fake_adb fixture fails the test on any, even a caught one)."""

    def __init__(self, phones):
        self.phones, self.calls, self.violations = dict(phones), [], []

    @staticmethod
    def check(argv, args, kwargs):
        """The rules for ONE adb call -> (verb, operands); AssertionError when one is broken. Allowed: `adb devices`;
        Step 8's read-only `shell find` / `shell stat` over the Media roots; `pull` of a `.mvmeta.json` into an
        absolute local path; `push` of an absolute local file to a `.mvmeta.json.tmp`; `shell mv` of a sidecar's own
        `.tmp` over it. Every call but `adb devices` names its phone with -s."""
        assert not args, f"positional subprocess.run arguments: {args!r}"
        assert kwargs.get("stdin") is subprocess.DEVNULL, f"adb must never read the caller's stdin: {argv!r}"
        assert kwargs.get("capture_output") is True, f"adb output must be captured: {argv!r}"
        assert isinstance(kwargs.get("timeout"), (int, float)) and kwargs["timeout"] > 0, f"no timeout: {argv!r}"
        assert not kwargs.get("check") and not kwargs.get("shell"), f"check / shell set: {argv!r}"
        assert argv[:1] == ["adb"], f"not an adb call: {argv!r}"
        if argv == ["adb", "devices"]:
            return "devices", ()
        assert argv[1:2] == ["-s"] and len(argv) > 4 and re.fullmatch(r"[A-Za-z0-9._:-]+", argv[2]), \
            f"a phone command must name its phone with -s: {argv!r}"
        verb, rest = argv[3], argv[4:]

        def sidecar(path):
            return path.startswith(ROOTS_ALL) and path.endswith(".mvmeta.json") and "/../" not in path

        if verb == "pull":
            assert len(rest) == 2 and sidecar(rest[0]) and os.path.isabs(rest[1]), f"not a sidecar pull: {argv!r}"
            return "pull", tuple(rest)
        if verb == "push":
            assert len(rest) == 2 and os.path.isabs(rest[0]) and rest[1].endswith(".mvmeta.json.tmp") \
                and sidecar(rest[1][:-len(".tmp")]), f"not a push to a sidecar's .tmp: {argv!r}"
            return "push", tuple(rest)
        assert verb == "shell" and rest, f"adb command off the allowlist: {argv!r}"
        words = device_words(" ".join(rest))
        if words[0] == "find":
            assert words[1:] in [[root, "-type", "f"] for root in ROOTS_ALL], f"not a read-only find: {words!r}"
        elif words[0] == "stat":
            assert words[1:3] == ["-c", STAT_FORMAT] and len(words) > 3 \
                and all(p.startswith(ROOTS_ALL) for p in words[3:]), f"not a read-only stat: {words!r}"
        elif words[0] == "mv":
            assert len(words) == 3 and sidecar(words[2]) and words[1] == words[2] + ".tmp", \
                f"not a sidecar's own rename: {words!r}"
        else:
            raise AssertionError(f"device command off the allowlist: {words!r}")
        return words[0], tuple(words[1:])

    def run(self, argv, *args, **kwargs):
        argv = list(argv)
        self.calls.append((argv, kwargs))
        try:
            verb, ops = self.check(argv, args, kwargs)
        except AssertionError as e:
            self.violations.append(str(e))
            raise
        if verb == "devices":
            return result(0, crlf(["List of devices attached"] + [f"{serial}\t{phone.state}" for serial, phone
                                                                  in self.phones.items() if phone.state] + [""]))
        serial = argv[2]
        phone = self.phones.get(serial)
        if phone is None or phone.state is None:
            return result(1, b"", crlf([f"adb: device '{serial}' not found"]))
        if phone.state != "device":
            return result(1, b"", crlf([f"adb: device {phone.state}"]))
        mode = phone.mode(verb)
        if mode == "timeout":
            raise subprocess.TimeoutExpired(argv, kwargs["timeout"])
        if mode == "error":
            return result(1, b"", crlf([f"adb: error: {verb} failed on the fake phone"]))
        return getattr(self, "_" + verb)(phone, ops, mode)

    @staticmethod
    def _find(phone, ops, mode):
        return result(0, crlf([ops[0] + rel for rel in
                               sorted(p.relative_to(phone.root).as_posix() for p in phone.root.rglob("*") if p.is_file())]))

    @staticmethod
    def _stat(phone, ops, mode):
        out, err = [], []
        for path in ops[2:]:
            f = phone.file(path)
            if f.is_file():
                out.append(f"{f.stat().st_size} {int(f.stat().st_mtime)} {path}")
            else:
                err.append(f"stat: '{path}': No such file or directory")
        return result(1 if err else 0, crlf(out), crlf(err))

    @staticmethod
    def _pull(phone, ops, mode):
        remote, local = ops
        f = phone.file(remote)
        if not f.is_file():
            return result(1, b"", crlf([f"adb: error: failed to stat remote object '{remote}': No such file or "
                                        f"directory"]))
        shutil.copyfile(f, local)
        return result(0, crlf([f"{remote}: 1 file pulled, 0 skipped."]))

    @staticmethod
    def _push(phone, ops, mode):
        local, remote = ops
        with open(local, "rb") as fh:
            data = fh.read() + (b" " if mode == "corrupt" else b"")
        f = phone.file(remote)
        f.write_bytes(data)
        mtime = int(os.stat(local).st_mtime) if phone.keeps_mtime else PUSH_NOW
        os.utime(f, (mtime, mtime))
        return result(0, crlf([f"{local}: 1 file pushed, 0 skipped."]))

    @staticmethod
    def _mv(phone, ops, mode):
        source, target = (phone.file(path) for path in ops)
        if not source.is_file():
            return result(1, b"", crlf([f"mv: {ops[0]}: No such file or directory"]))
        os.replace(source, target)
        return result(0)

    def argvs(self):
        return [argv for argv, _kwargs in self.calls]

    def verbs(self, serial=None):
        """The verb of every call (of one phone): devices / find / stat / pull / push / mv."""
        out = []
        for argv in self.argvs():
            if argv == ["adb", "devices"]:
                out.append("devices")
            elif serial is None or argv[2] == serial:
                out.append(argv[4] if argv[3] == "shell" else argv[3])
        return out


@pytest.fixture()
def fake_adb(monkeypatch):
    """install(phones {serial: Phone}) -> the FakeAdb now answering every subprocess.run. At teardown the test fails if
    ANY call of any fake it installed broke a rule — even when the code under test caught the AssertionError."""
    installed = []

    def install(phones):
        fake = FakeAdb(phones)
        installed.append(fake)
        monkeypatch.setattr(main.subprocess, "run", fake.run)
        return fake

    yield install
    violations = [v for fake in installed for v in fake.violations]
    assert violations == [], f"adb calls broke the rules: {violations}"


# ============================================================================ (b) the device path

HOSTILE_REL = "Series/Mu Show/Season 02/It's $5 `id` ; rm x — Ünïcødé [abc123].mvmeta.json"
SIDECAR = ROOT + "Series/Mu Show/Season 02/Mu.Show.S02E03 [abc123].mvmeta.json"


@pytest.mark.parametrize("argv,write", [
    (["adb", "-s", SERIES, "pull", SIDECAR, r"C:\t\1.json"], False),
    (["adb", "-s", SERIES, "pull", SIDECAR, r"C:\t\1.json"], True),
    (["adb", "-s", SERIES, "pull", "/storage/emulated/0/Media/a [abc123].mvmeta.json", r"C:\t\1.json"], False),
    (["adb", "-s", "192.168.1.5:5555", "pull", SIDECAR, r"C:\t\1.json"], False),
    (["adb", "-s", SERIES, "push", r"C:\t\1.json", SIDECAR + ".tmp"], True),
    (["adb", "-s", SERIES, "shell", "mv", main._dev_quote(SIDECAR + ".tmp"), main._dev_quote(SIDECAR)], True),
    (["adb", "-s", SERIES, "shell", "mv", main._dev_quote(ROOT + HOSTILE_REL + ".tmp"),
      main._dev_quote(ROOT + HOSTILE_REL)], True),
])
def test_the_own_allowlist_takes_exactly_the_sidecar_shapes(argv, write):
    assert main._mu_argv_ok(argv, write) is True
    FakeAdb.check(argv, (), {"stdin": subprocess.DEVNULL, "capture_output": True, "timeout": 5})  # and this module's


MEDIA = ROOT + "Series/Mu Show/Season 02/Mu.Show.S02E03 [abc123].mkv"


@pytest.mark.parametrize("argv", [
    ["adb", "-s", SERIES, "push", r"C:\t\1.json", SIDECAR + ".tmp"],                         # no --apply
    ["adb", "-s", SERIES, "shell", "mv", main._dev_quote(SIDECAR + ".tmp"), main._dev_quote(SIDECAR)],
])
def test_a_dry_run_cannot_write(argv):
    assert main._mu_argv_ok(argv, False) is False


@pytest.mark.parametrize("argv", [
    ["adb", "-s", SERIES, "push", r"C:\t\1.json", SIDECAR],                                  # straight onto it
    ["adb", "-s", SERIES, "push", r"C:\t\1.mkv", MEDIA + ".tmp"],                            # a media file
    ["adb", "-s", SERIES, "push", r"C:\t\1.mkv", MEDIA],
    ["adb", "-s", SERIES, "push", r"C:\t\1.json", SIDECAR + ".tmp.tmp"],
    ["adb", "-s", SERIES, "push", "-p", r"C:\t\1.json", SIDECAR + ".tmp"],                   # no other shape
    ["adb", "-s", SERIES, "push", r"t\1.json", SIDECAR + ".tmp"],                            # a relative local path
    ["adb", "-s", SERIES, "push", r"C:\t\1.json", "/sdcard/DCIM/a [abc123].mvmeta.json.tmp"],  # off the Media roots
    ["adb", "-s", SERIES, "push", r"C:\t\1.json", ROOT + "../../data/a.mvmeta.json.tmp"],
    ["adb", "-s", SERIES, "pull", MEDIA, r"C:\t\1.mkv"],                                      # a media file
    ["adb", "-s", SERIES, "pull", "-a", SIDECAR, r"C:\t\1.json"],
    ["adb", "-s", SERIES, "pull", SIDECAR, "-x"],
    ["adb", "-s", SERIES, "pull", SIDECAR, r"C:\t\1.json", r"C:\t\2.json"],
    ["adb", "-s", SERIES, "pull", SIDECAR + "\nx", r"C:\t\1.json"],
    ["adb", "-s", SERIES, "shell", "mv", main._dev_quote(SIDECAR), main._dev_quote(SIDECAR + ".tmp")],  # reversed
    ["adb", "-s", SERIES, "shell", "mv", main._dev_quote(SIDECAR + ".tmp"),
     main._dev_quote(ROOT + "Other [ffffff].mvmeta.json")],                                  # onto another file
    ["adb", "-s", SERIES, "shell", "mv", main._dev_quote(MEDIA + ".tmp"), main._dev_quote(MEDIA)],
    ["adb", "-s", SERIES, "shell", "mv", SIDECAR + ".tmp", SIDECAR],                          # unquoted
    ["adb", "-s", SERIES, "shell", "mv", "'" + SIDECAR + ".tmp'; rm -rf /'", main._dev_quote(SIDECAR)],
    ["adb", "-s", SERIES, "shell", "mv", "-f", main._dev_quote(SIDECAR + ".tmp"), main._dev_quote(SIDECAR)],
    ["adb", "-s", SERIES, "shell", "rm", main._dev_quote(SIDECAR)],
    ["adb", "-s", SERIES, "shell", "rm", main._dev_quote(SIDECAR + ".tmp")],
    ["adb", "-s", SERIES, "shell", f"mv {main._dev_quote(SIDECAR + '.tmp')} {main._dev_quote(SIDECAR)}"],
    ["adb", "push", r"C:\t\1.json", SIDECAR + ".tmp"],                                       # no -s: any phone
    ["adb", "pull", SIDECAR, r"C:\t\1.json"],
    ["adb", "-s", "FA75 ; rm", "pull", SIDECAR, r"C:\t\1.json"],
    ["adb", "-s", SERIES, "devices"], ["adb", "devices"],
    ["adb.exe", "-s", SERIES, "pull", SIDECAR, r"C:\t\1.json"], "adb pull", ("adb", 7), [],
])
def test_the_own_allowlist_refuses_every_other_shape(argv):
    assert main._mu_argv_ok(argv, True) is False


@pytest.mark.parametrize("argv", [
    ["adb", "-s", SERIES, "pull", SIDECAR, r"C:\t\1.json"],
    ["adb", "-s", SERIES, "push", r"C:\t\1.json", SIDECAR + ".tmp"],
    ["adb", "-s", SERIES, "shell", "mv", main._dev_quote(SIDECAR + ".tmp"), main._dev_quote(SIDECAR)],
])
def test_the_device_check_s_read_only_allowlist_is_not_widened(argv):
    """PROGRESS row 8 (binding): Step 13c must NOT widen _dev_argv_readonly — its pull / push / mv go through its own
    allowlist; the device check's stays exactly read-only."""
    assert main._dev_argv_readonly(argv) is False


def test_a_refused_argv_never_runs(monkeypatch):
    ran = []
    monkeypatch.setattr(main.subprocess, "run", lambda *a, **k: ran.append(a))
    for argv, write in ((["adb", "-s", SERIES, "push", r"C:\t\1.json", SIDECAR + ".tmp"], False),
                        (["adb", "-s", SERIES, "shell", "rm", main._dev_quote(SIDECAR)], True)):
        with pytest.raises(main._MvmetaCommandRefused):
            main._mu_run(argv, write)
    assert ran == []


def _planned(lib, monkeypatch, mid="tv-en-2015-mushow-s02e03"):
    """(v1 bytes, v2 bytes) of one entry's sidecar the pure core upgrades — a capture proves every key."""
    entry = lib[mid]
    write_capture(entry["folder_path"], mid, entry["short_id"],
                  prep=cap_facts(entry["hash"], "ep3", entry["search_term"]))
    _path, raw = written_sidecar(monkeypatch, entry, mid)
    old, newline, _why = main._mu_decode(raw)
    new, adds, why = main._mu_upgrade(old, _owner(lib, mid), _facts_of(lib, mid))
    assert why is None and adds
    return raw, main._mu_encode(new, newline)


def _ctx(tmp_path, apply=True):
    work = tmp_path / "work"
    work.mkdir(exist_ok=True)
    return {"apply": apply, "work": str(work), "run_dir": None, "now": NOW, "kept": 0, "n": 0}


def _row(path, raw, new, mtime, serial=SERIES):
    return {"alias": "series", "serial": serial, "path": path, "mtime": mtime, "raw": raw, "new": new,
            "label": "tv-en-2015-mushow-s02e03", "status": "upgrade", "why": None, "warning": None}


MTIME = 1_632_000_000


def test_apply_keeps_the_original_then_pushes_a_tmp_renames_it_and_reads_it_back(lib, monkeypatch, tmp_path, fake_adb):
    raw, new = _planned(lib, monkeypatch)
    phone = Phone(tmp_path / "phone_series")
    path = phone.put(HOSTILE_REL, raw, MTIME)
    fake = fake_adb({SERIES: phone})
    row, ctx = _row(path, raw, new, MTIME), _ctx(tmp_path)
    assert main._mu_write(row, ctx) is True
    assert (row["status"], row["why"], row["warning"]) == ("upgraded", None, None)
    assert phone.files() == {path: (new, MTIME)}                      # the new bytes, the push time kept, no .tmp
    push, mv, pull, stat = fake.argvs()
    assert push[:4] == ["adb", "-s", SERIES, "push"] and push[5] == path + ".tmp"
    assert os.path.dirname(push[4]) == ctx["work"]                    # pushed from the run's temporary folder
    assert mv == ["adb", "-s", SERIES, "shell", "mv", main._dev_quote(path + ".tmp"), main._dev_quote(path)]
    assert pull[:5] == ["adb", "-s", SERIES, "pull", path] and os.path.dirname(pull[5]) == ctx["work"]
    assert stat == ["adb", "-s", SERIES, "shell", "stat", "-c", main._dev_quote(STAT_FORMAT), main._dev_quote(path)]
    run_dir = os.path.join(mvcommon.GPHOTOS_STATE_DIR, "mvmeta", "20261014T120000Z")
    assert ctx["run_dir"] == run_dir and row["kept"] == os.path.join(run_dir, "originals", "0001.mvmeta.json")
    with open(row["kept"], "rb") as fh:
        assert fh.read() == raw                                       # the original, byte for byte
    with open(os.path.join(run_dir, "backup.jsonl"), encoding="utf-8") as fh:
        assert [json.loads(line) for line in fh] == [{
            "file": "originals/0001.mvmeta.json", "alias": "series", "serial": SERIES, "path": path, "mtime": MTIME,
            "manual_id": "tv-en-2015-mushow-s02e03", "sha256": hashlib.sha256(raw).hexdigest()}]


@pytest.mark.parametrize("fail,keeps_mtime,verbs,ok,phone_holds,fragment", [
    ({"push": "error"}, True, ["push"], False, "raw", "the push of"),
    ({"push": "timeout"}, True, ["push"], False, "raw", "TimeoutExpired"),
    ({"mv": "error"}, True, ["push", "mv"], False, "raw + tmp", "the rename on the phone failed"),
    ({"mv": "timeout"}, True, ["push", "mv"], False, "raw + tmp", "TimeoutExpired"),
    ({"push": "corrupt"}, True, ["push", "mv", "pull"], False, "corrupt", "does not read back as written (other bytes)"),
    ({("pull", 1): "error"}, True, ["push", "mv", "pull"], False, "new", "does not read back as written (adb pull"),
    ({}, False, ["push", "mv", "pull", "stat"], True, "new @ PUSH_NOW", "is now 1800000000, was 1632000000"),
    ({"stat": "error"}, True, ["push", "mv", "pull", "stat"], True, "new", "its mtime could not be read back"),
], ids=["push-error", "push-timeout", "mv-error", "mv-timeout", "corrupt-write", "read-back-fails",
        "mtime-not-kept", "mtime-unreadable"])
def test_every_stage_of_a_write_can_fail_and_says_what_the_phone_holds(lib, monkeypatch, tmp_path, fake_adb, fail,
                                                                       keeps_mtime, verbs, ok, phone_holds, fragment):
    raw, new = _planned(lib, monkeypatch)
    phone = Phone(tmp_path / "phone_series", fail=fail, keeps_mtime=keeps_mtime)
    path = phone.put(HOSTILE_REL, raw, MTIME)
    fake = fake_adb({SERIES: phone})
    row = _row(path, raw, new, MTIME)
    assert main._mu_write(row, _ctx(tmp_path)) is ok
    assert fake.verbs() == verbs
    assert fragment in (row["warning"] if ok else row["why"])
    assert row["status"] == ("upgraded" if ok else "failed")
    expected = {"raw": {path: (raw, MTIME)},
                "raw + tmp": {path: (raw, MTIME), path + ".tmp": (new, MTIME)},
                "corrupt": {path: (new + b" ", MTIME)},
                "new": {path: (new, MTIME)},
                "new @ PUSH_NOW": {path: (new, PUSH_NOW)}}[phone_holds]
    assert phone.files() == expected
    if phone_holds in ("raw", "raw + tmp"):
        assert "the sidecar is unchanged" in row["why"]
    if phone_holds == "corrupt":
        assert f'restore the original: adb -s {SERIES} push "{row["kept"]}" "{path}"' in row["why"]
    with open(row["kept"], "rb") as fh:                               # kept before anything was written
        assert fh.read() == raw


def test_a_dry_run_ctx_writes_nothing_even_when_asked_to(lib, monkeypatch, tmp_path, fake_adb):
    raw, new = _planned(lib, monkeypatch)
    phone = Phone(tmp_path / "phone_series")
    path = phone.put(HOSTILE_REL, raw, MTIME)
    fake = fake_adb({SERIES: phone})
    row, ctx = _row(path, raw, new, MTIME), _ctx(tmp_path, apply=False)
    assert main._mu_write(row, ctx) is False and row["why"] == "a dry run writes nothing"
    assert fake.calls == [] and ctx["run_dir"] is None and not os.path.exists(mvcommon.GPHOTOS_STATE_DIR)
    assert phone.files() == {path: (raw, MTIME)}


def test_no_local_copy_of_the_original_means_no_phone_write(lib, monkeypatch, tmp_path, fake_adb):
    raw, new = _planned(lib, monkeypatch)
    blocker = tmp_path / "a file"
    blocker.write_text("not a folder")
    monkeypatch.setattr(mvcommon, "GPHOTOS_STATE_DIR", str(blocker / "gphotos"))
    phone = Phone(tmp_path / "phone_series")
    path = phone.put(HOSTILE_REL, raw, MTIME)
    fake = fake_adb({SERIES: phone})
    row = _row(path, raw, new, MTIME)
    assert main._mu_write(row, _ctx(tmp_path)) is False
    assert "its original could not be kept locally" in row["why"] and "nothing was written to the phone" in row["why"]
    assert fake.calls == [] and phone.files() == {path: (raw, MTIME)}


def test_the_run_directory_never_reuses_another_run_s_folder():
    base = os.path.join(mvcommon.GPHOTOS_STATE_DIR, "mvmeta", "20261014T120000Z")
    assert main._mu_new_run_dir(NOW) == base
    assert main._mu_new_run_dir(NOW) == base + "-2"
    assert main._mu_new_run_dir(NOW) == base + "-3"
    assert sorted(os.listdir(os.path.dirname(base))) == ["20261014T120000Z", "20261014T120000Z-2", "20261014T120000Z-3"]


@pytest.mark.parametrize("fail,fragment", [({"pull": "error"}, "adb: error: pull failed"),
                                           ({"pull": "timeout"}, "TimeoutExpired"),
                                           ({}, "No such file or directory")])
def test_a_pull_that_fails_is_reported_never_raised(tmp_path, fake_adb, fail, fragment):
    fake = fake_adb({SERIES: Phone(tmp_path / "phone_series", fail=fail)})
    data, why = main._mu_pull(SERIES, SIDECAR, str(tmp_path / "1.json"))
    assert data is None and why.startswith("adb pull failed (") and fragment in why
    assert fake.verbs() == ["pull"]
    data, why = main._mu_pull(SERIES, MEDIA, str(tmp_path / "2.json"))       # off the allowlist: never runs
    assert data is None and "_MvmetaCommandRefused" in why and fake.verbs() == ["pull"]


def test_the_mtime_is_read_with_the_device_check_s_read_only_stat(tmp_path, fake_adb):
    phone = Phone(tmp_path / "phone_series")
    path = phone.put(HOSTILE_REL, b"{}", MTIME)
    fake_adb({SERIES: phone})
    assert main._mu_mtime(SERIES, path) == MTIME
    assert main._mu_mtime(SERIES, path + ".gone") is None
    fake_adb({SERIES: Phone(tmp_path / "phone_series", fail={"stat": "timeout"})})
    assert main._mu_mtime(SERIES, path) is None
