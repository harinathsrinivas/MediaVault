"""IMP-C25 interim identity capture (gpcapture.py + the mvcommon SHA-1 side-channel).

The capture is best-effort, post-commit and never journalled, so these tests pin the three
things that matter: the facts are right (DateUTC, SHA-1/dedupKey, uploaded names), the
existing contracts are untouched (calculate_file_hash's SHA-256, prep/push results), and a
capture problem can never turn a successful command into a failure.
"""
import hashlib
import json
import os
import time
from datetime import datetime, timezone

import gpcapture
import main
import mvcommon

MOVIE_ID = "mov-en-2020-capturetest"


# --------------------------------------------------------------------------- helpers

def _vint_size(n):
    """EBML size vint for n < 2**56 using the 8-byte form (always valid)."""
    return bytes([0x01]) + n.to_bytes(7, "big")


def _mkv_bytes(date_utc=None, with_cluster=True):
    """Minimal but structurally valid Matroska: EBML header, unknown-size Segment, a Void,
    an Info (TimestampScale [+ DateUTC]) and optionally a Cluster."""
    ebml_header = bytes.fromhex("1A45DFA3") + bytes([0x84]) + bytes.fromhex("42868101")  # EBMLVersion=1
    info_children = bytes.fromhex("2AD7B1") + bytes([0x83]) + (1_000_000).to_bytes(3, "big")
    if date_utc is not None:
        ns = int((date_utc - datetime(2001, 1, 1, tzinfo=timezone.utc)).total_seconds() * 1_000_000_000)
        info_children += bytes.fromhex("4461") + bytes([0x88]) + ns.to_bytes(8, "big", signed=True)
    info = bytes.fromhex("1549A966") + _vint_size(len(info_children)) + info_children
    void = bytes([0xEC, 0x82, 0x00, 0x00])
    cluster = bytes.fromhex("1F43B675") + bytes([0x81, 0x00]) if with_cluster else b""
    segment = bytes.fromhex("18538067") + bytes.fromhex("01FFFFFFFFFFFFFF") + void + info + cluster
    return ebml_header + segment


def _write(path, data):
    with open(path, "wb") as f:
        f.write(data)
    return str(path)


def _capture(folder, short_id):
    with open(gpcapture.capture_path(str(folder), short_id), encoding="utf-8") as f:
        return json.load(f)


# --------------------------------------------------------------------------- DateUTC

def test_mkv_date_utc_reads_segment_info(tmp_path):
    when = datetime(2022, 2, 5, 11, 49, 46, tzinfo=timezone.utc)  # S02E03's real DateUTC (RESEARCH F1)
    p = _write(tmp_path / "a.mkv", _mkv_bytes(when))
    assert gpcapture.mkv_date_utc(p) == "2022-02-05T11:49:46Z"


def test_mkv_date_utc_none_when_absent_or_not_matroska(tmp_path):
    assert gpcapture.mkv_date_utc(_write(tmp_path / "nodate.mkv", _mkv_bytes(None))) is None
    assert gpcapture.mkv_date_utc(_write(tmp_path / "text.mkv", b"SMOKE-REAL-MEDIA-MASTER\n" * 50)) is None
    assert gpcapture.mkv_date_utc(_write(tmp_path / "empty.mkv", b"")) is None
    full = _mkv_bytes(datetime(2024, 5, 3, 23, 18, 2, tzinfo=timezone.utc))
    assert gpcapture.mkv_date_utc(_write(tmp_path / "torn.mkv", full[:-12])) is None  # DateUTC cut off
    assert gpcapture.mkv_date_utc(str(tmp_path / "missing.mkv")) is None


# --------------------------------------------------------------------------- SHA-1 side-channel

def test_calculate_file_hash_sha256_unchanged_and_sha1_cached(tmp_path, capsys):
    for size in (0, 10, 65536 * 3 + 7):
        data = os.urandom(size)
        p = _write(tmp_path / f"f{size}.bin", data)
        assert mvcommon.calculate_file_hash(p) == hashlib.sha256(data).hexdigest()
        assert mvcommon.cached_sha1(p) == hashlib.sha1(data).hexdigest()
    assert "🔍" in capsys.readouterr().out  # progress output still printed


def test_cached_sha1_is_invalidated_when_the_file_changes(tmp_path):
    p = _write(tmp_path / "x.bin", b"first version")
    mvcommon.calculate_file_hash(p)
    assert mvcommon.cached_sha1(p) == hashlib.sha1(b"first version").hexdigest()
    _write(p, b"second version, different size")
    os.utime(p, (time.time() + 5, time.time() + 5))
    assert mvcommon.cached_sha1(p) is None


def test_dedup_key_matches_the_verified_google_photos_example():
    # RESEARCH F21: S02E03's SHA-1 on the Pixel == its Google Photos dedupKey
    assert gpcapture.dedup_key("85df66012c86f692618ac3d7152dd9afb32aaf6f") == "hd9mASyG9pJhisPXFS3Zr7Mqr28"
    assert gpcapture.dedup_key(None) is None
    assert gpcapture.dedup_key("not-hex") is None


# --------------------------------------------------------------------------- cmd_prep hook

def _prep(sandbox, make_video):
    path, sha256 = make_video(sandbox["media_dir"] / "Capture.Test.2020.mkv")
    assert main.cmd_prep(MOVIE_ID, path) is True
    entry = main.load_library()[MOVIE_ID]
    return path, sha256, entry


def test_prep_writes_capture_beside_the_uid_sidecar(sandbox, make_video, stub_tech_specs):
    path, sha256, entry = _prep(sandbox, make_video)
    short_id = entry["short_id"]
    assert (sandbox["media_dir"] / "uid").exists()
    doc = _capture(sandbox["media_dir"], short_id)
    src = doc["prep"]["source"]
    with open(path, "rb") as f:
        data = f.read()
    assert doc["manual_id"] == MOVIE_ID and doc["short_id"] == short_id
    assert src["sha256"] == entry["hash"] == sha256
    assert src["sha1"] == hashlib.sha1(data).hexdigest()
    assert src["dedup_key"] == gpcapture.dedup_key(src["sha1"])
    assert src["size_bytes"] == len(data)
    assert src["date_utc"] is None  # the fixture bytes are not Matroska
    assert doc["prep"]["uploaded_name"] == entry["search_term"]
    # the capture is a sidecar only: the library entry gains no new fields
    assert not any("sha1" in k or "capture" in k for k in entry)


def test_a_failing_capture_never_breaks_prep(sandbox, make_video, stub_tech_specs, monkeypatch, capsys):
    def boom(*a, **kw):
        raise RuntimeError("capture exploded")
    monkeypatch.setattr(main.gpcapture, "capture_after_prep", boom)
    _, _, entry = _prep(sandbox, make_video)
    assert entry["status"] == "local_ready"
    assert "Prep failed" not in capsys.readouterr().out


def test_failed_prep_writes_no_capture(sandbox, make_video, stub_tech_specs, monkeypatch):
    path, _ = make_video(sandbox["media_dir"] / "Capture.Test.2020.mkv")
    monkeypatch.setattr(main, "calculate_file_hash", lambda p: None)
    assert main.cmd_prep(MOVIE_ID, path) is False
    assert not [n for n in os.listdir(sandbox["media_dir"]) if n.endswith(gpcapture.CAPTURE_SUFFIX)]


# --------------------------------------------------------------------------- cmd_push hook

def test_push_appends_the_uploaded_whole_file(sandbox, make_video, stub_tech_specs, mock_device):
    path, sha256, entry = _prep(sandbox, make_video)
    assert main.cmd_push(MOVIE_ID) is True
    doc = _capture(sandbox["media_dir"], entry["short_id"])
    assert len(doc["pushes"]) == 1
    push = doc["pushes"][0]
    assert push["complete"] is True and push["chunk_range"] is None
    assert push["remote_dir"].endswith("/Movies/TestMovie")
    (obj,) = push["objects"]
    assert obj["role"] == "whole" and obj["index"] is None
    assert obj["uploaded_name"] == entry["search_term"]  # "<name> [<short_id>].mkv" on the device
    assert obj["sha256"] == sha256
    assert obj["sha1"] == doc["prep"]["source"]["sha1"]  # same process -> side-channel hit
    assert "prep" in doc  # the prep record survives the push merge
    # and the push itself really happened ("[...]" is a glob class, so match by name — see conftest)
    assert [p for p in mock_device.rglob("*.mkv") if p.name == entry["search_term"]]


def test_snapshot_names_chunks_and_holder_like_cmd_push(tmp_path):
    parts = tmp_path / main.SPLIT_DIR_NAME
    parts.mkdir()
    master = _write(tmp_path / "Movie.2020.mkv", b"m" * 10)
    c1 = _write(parts / "Movie.2020 [abc123].chunk.001.mkv", _mkv_bytes(datetime(2026, 9, 24, 6, 3, 2, tzinfo=timezone.utc)))
    c2 = _write(parts / "Movie.2020 [abc123].chunk.002.mkv", b"c2")
    holder = _write(parts / "Movie.2020 [abc123].holder.mkv", b"h")
    objs = gpcapture.snapshot_push_objects(
        [master, c1, c2, holder], main.SPLIT_DIR_NAME, "abc123",
        {"Movie.2020 [abc123].chunk.001.mkv": "h1", "Movie.2020 [abc123].chunk.002.mkv": "h2"},
        "whole-hash", [{"holder_filename": "Movie.2020 [abc123].holder.mkv", "holder_hash": "hh"}])
    got = [(o["role"], o["index"], o["uploaded_name"], o["sha256"]) for o in objs]
    assert got == [("whole", None, "Movie.2020 [abc123].mkv", "whole-hash"),
                   ("chunk", 1, "Movie.2020 [abc123].chunk.001.mkv", "h1"),
                   ("chunk", 2, "Movie.2020 [abc123].chunk.002.mkv", "h2"),
                   ("holder", None, "Movie.2020 [abc123].holder.mkv", "hh")]
    assert objs[1]["date_utc"] == "2026-09-24T06:03:02Z"


def test_capture_merges_records_atomically_and_quietly_skips_bad_folders(tmp_path):
    folder = str(tmp_path)
    src = _write(tmp_path / "Show.S01E01.mkv", b"x" * 100)
    assert gpcapture.capture_after_prep(folder, "tv-x-s01e01", "def456", src, "Show.S01E01 [def456].mkv", "s256")
    assert gpcapture.capture_after_push(folder, "tv-x-s01e01", "def456", [{"role": "chunk"}], chunk_range="1-1")
    assert gpcapture.capture_after_push(folder, "tv-x-s01e01", "def456", [{"role": "chunk"}], chunk_range="2-2")
    doc = _capture(folder, "def456")
    assert doc["prep"]["uploaded_name"] == "Show.S01E01 [def456].mkv"
    assert [p["chunk_range"] for p in doc["pushes"]] == ["1-1", "2-2"]
    assert not [n for n in os.listdir(folder) if n.endswith(".tmp")]  # temp files replaced, none left
    assert gpcapture.capture_after_prep(str(tmp_path / "does-not-exist"), "id", "sid", src, "n", "h") is None
