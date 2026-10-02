"""IMP-C25 identity capture (gpcapture.py + the mvcommon SHA-1 side-channel) — v1 interim + v2 (PLAN Step 5).

The capture is best-effort, post-commit and never journalled, so these tests pin the three
things that matter: the facts are right (DateUTC, SHA-1/dedupKey, uploaded names), the
existing contracts are untouched (calculate_file_hash's SHA-256, prep/push results), and a
capture problem can never turn a successful command into a failure.

Step 5 (capture v2) adds: the Matroska duration; per push object width/height + approach_hint;
the device's timezone from ONE post-commit adb query through the push's own adb_base
(main._device_facts); completion of unknown SHA-1/DateUTC from the file's earlier records for
the same bytes; the extras captures (scan + push); the remote .mvmeta.json v2 per-chunk keys;
and — the OD-3 acceptance — a journal-op instrumentation proof that every rollback journal
record is identical with and without the capture layer, and that no capture or device query
runs while a journal is open. Synthetic data only; no real adb, device, Google or C:\\Media.
"""
import hashlib
import json
import os
import struct
import subprocess
import time
import types
from collections import Counter
from datetime import datetime, timezone

import pytest

import gpcapture
import main
import mvcommon
from test_cmd_push_partial import FakeAdb  # THE protocol recorder — its results carry NO stdout (PLAN Step 5)

MOVIE_ID = "mov-en-2020-capturetest"
DEVICE_QUERY = "getprop persist.sys.timezone; date +%z"   # PLAN Step 5b, verbatim
SERIAL = "FAKESERIAL05"
SPLIT_START = datetime(2026, 9, 24, 6, 3, 2, tzinfo=timezone.utc)   # F4: all chunks of a split share it
MASTER_DATE = datetime(2022, 2, 5, 11, 49, 46, tzinfo=timezone.utc)  # S02E03's real DateUTC (RESEARCH F1)
MVMETA_V1_KEYS = {"version", "manual_id", "short_id", "base_filename", "original_hash", "is_split", "method",
                  "val", "total_chunks", "chunks", "folder_path", "remote_target_dir", "tech_spec", "metadata"}


# --------------------------------------------------------------------------- helpers

def _vint_size(n):
    """EBML size vint for n < 2**56 using the 8-byte form (always valid)."""
    return bytes([0x01]) + n.to_bytes(7, "big")


def _mkv_bytes(date_utc=None, with_cluster=True, duration=None, scale=1_000_000, float32=False):
    """Minimal but structurally valid Matroska: EBML header, unknown-size Segment, a Void,
    an Info (TimestampScale [+ Duration] [+ DateUTC]) and optionally a Cluster. `duration` is
    Matroska's Duration float, in TimestampScale units (default scale: milliseconds)."""
    ebml_header = bytes.fromhex("1A45DFA3") + bytes([0x84]) + bytes.fromhex("42868101")  # EBMLVersion=1
    info_children = bytes.fromhex("2AD7B1") + bytes([0x83]) + scale.to_bytes(3, "big")
    if duration is not None:
        info_children += bytes.fromhex("4489") + (bytes([0x84]) + struct.pack(">f", duration) if float32
                                                  else bytes([0x88]) + struct.pack(">d", duration))
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


def _mkv_master(path, date_utc, seconds, marker=b""):
    """A Matroska header (DateUTC + Duration) padded past DUMMY_MAX_BYTES so prep / push treat it as
    real media. The padding follows the Cluster, which no header reader passes."""
    data = _mkv_bytes(date_utc, duration=seconds * 1000) + marker + b"\0" * (main.DUMMY_MAX_BYTES + 10_000)
    return _write(path, data)


def _sha1(path_or_bytes):
    data = path_or_bytes if isinstance(path_or_bytes, bytes) else open(path_or_bytes, "rb").read()
    return hashlib.sha1(data).hexdigest()


@pytest.fixture()
def tech_1080p(monkeypatch):
    """main.get_tech_specs stub WITH a frame size (stub_tech_specs has none), so the width/height facts
    of the v2 capture can be asserted. Covers cmd_prep and scan_extras_folders alike."""
    def _specs(filepath):
        return {"resolution": "1080p", "width_height": "1920x1080", "duration_mins": 45,
                "size_bytes": os.path.getsize(filepath)}
    monkeypatch.setattr(main, "get_tech_specs", _specs)


def _answering_device(monkeypatch, answer="Asia/Singapore\n+0800\n"):
    """Wrap the installed main.subprocess.run (mock_device's fake_run): record every argv and answer the
    device-facts query like a Pixel does (PLAN P9). Install AFTER mock_device. Returns the argv list."""
    inner = main.subprocess.run
    calls = []

    def run(argv, *a, **kw):
        argv = list(argv)
        calls.append(argv)
        if argv[-1:] == [DEVICE_QUERY]:
            return types.SimpleNamespace(returncode=0, stdout=answer)
        return inner(argv, *a, **kw)

    monkeypatch.setattr(main.subprocess, "run", run)
    return calls


def _device_mvmeta(device_dir, name=None):
    """The (one, or the named) .mvmeta.json on the fake device, parsed. Indexed by name — '[short_id]'
    would be a glob class (testing-strategy §8.1)."""
    metas = {p.name: p for p in device_dir.rglob("*.mvmeta.json")}
    assert (name in metas) if name else (len(metas) == 1), sorted(metas)
    path = metas[name] if name else next(iter(metas.values()))
    return json.loads(path.read_text(encoding="utf-8"))


def _install_mkv_split(monkeypatch, seconds=(1500.0, 1234.5)):
    """Replace main.split_video_file with a splitter that writes one small Matroska chunk per duration,
    named like the real one and stamped with ONE DateUTC (F4); the free-space pre-flight is neutralized.
    Returns {chunk name: bytes} (uploaded chunks are deleted locally, so tests keep the bytes)."""
    made = {}

    def fake_split(input_path, output_dir, method, value_str, file_id="", drop_track=None):
        os.makedirs(output_dir, exist_ok=True)
        base = os.path.splitext(os.path.basename(input_path))[0]
        paths = []
        for i, secs in enumerate(seconds, 1):
            name = f"{base} [{file_id}].chunk.{i:03d}.mkv"
            data = _mkv_bytes(SPLIT_START, duration=secs * 1000) + f"{base}-chunk-{i}".encode()
            paths.append(_write(os.path.join(output_dir, name), data))
            made[name] = data
        return paths

    monkeypatch.setattr(main, "split_video_file", fake_split)
    monkeypatch.setattr(main, "_free_space_ok", lambda *a, **k: True)
    return made


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
        [master, c1, c2, holder], str(parts), "abc123",
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


# =========================================================================== capture v2 (PLAN Step 5)

S02E03_SHA1 = "85df66012c86f692618ac3d7152dd9afb32aaf6f"   # RESEARCH F21 — the published example


def _prep_mkv(sandbox, seconds=2640.0):
    """cmd_prep MOVIE_ID over a Matroska master (DateUTC + Duration). Needs tech_1080p."""
    path = _mkv_master(sandbox["media_dir"] / "Cap.2020.mkv", MASTER_DATE, seconds)
    assert main.cmd_prep(MOVIE_ID, path) is True
    return path, main.load_library()[MOVIE_ID]


def _raising(exc):
    def run(*a, **kw):
        raise exc
    return run


# --------------------------------------------------------------------------- v2: Matroska header facts

def test_mkv_info_reads_dateutc_and_duration_in_one_header_pass(tmp_path):
    p = _write(tmp_path / "a.mkv", _mkv_bytes(MASTER_DATE, duration=2640123.0))     # ms: the default scale
    assert gpcapture.mkv_info(p) == {"date_utc": "2022-02-05T11:49:46Z", "duration_s": 2640.123}
    assert gpcapture.mkv_date_utc(p) == "2022-02-05T11:49:46Z"                       # the v1 API still answers
    # another TimestampScale (0.5 ms units) and a 4-byte float Duration
    p = _write(tmp_path / "b.mkv", _mkv_bytes(None, duration=3000.0, scale=500_000, float32=True))
    assert gpcapture.mkv_info(p) == {"date_utc": None, "duration_s": 1.5}


@pytest.mark.parametrize("duration", [float("nan"), float("inf"), -5.0])
def test_mkv_info_ignores_an_impossible_duration(tmp_path, duration):
    p = _write(tmp_path / "x.mkv", _mkv_bytes(MASTER_DATE, duration=duration))
    assert gpcapture.mkv_info(p) == {"date_utc": "2022-02-05T11:49:46Z", "duration_s": None}


def test_mkv_info_never_raises_and_knows_nothing_without_a_readable_header(tmp_path):
    torn = _mkv_bytes(MASTER_DATE, duration=10.0)[:-20]     # the Cluster, DateUTC and part of Duration cut off
    for i, data in enumerate((b"", b"SMOKE-REAL-MEDIA-MASTER\n" * 50, torn, bytes.fromhex("1A45DFA3"))):
        assert gpcapture.mkv_info(_write(tmp_path / f"{i}.mkv", data)) == {"date_utc": None, "duration_s": None}
    assert gpcapture.mkv_info(str(tmp_path / "missing.mkv")) == {"date_utc": None, "duration_s": None}


def test_file_facts_carry_the_header_duration(tmp_path):
    facts = gpcapture.file_facts(_mkv_master(tmp_path / "m.mkv", MASTER_DATE, 1742.5), "s256")
    assert (facts["date_utc"], facts["duration_s"], facts["sha1"]) == ("2022-02-05T11:49:46Z", 1742.5, None)


# --------------------------------------------------------------------------- v2: approach hint + device facts

@pytest.mark.parametrize("facts,hint", [
    ({"sha1": S02E03_SHA1, "date_utc": "2022-02-05T11:49:46Z"}, "hash"),
    ({"sha1": S02E03_SHA1, "date_utc": None}, "hash"),
    ({"sha1": None, "date_utc": "2022-02-05T11:49:46Z"}, "taken_second"),
    ({"sha1": None, "date_utc": None}, "exact_name"),
    ({}, "exact_name"), (None, "exact_name")])
def test_approach_hint_is_the_first_p8_stage_the_facts_make_usable(facts, hint):
    assert gpcapture.approach_hint(facts) == hint
    assert hint in mvcommon.GP_APPROACHES      # read_capture keeps only this vocabulary


@pytest.mark.parametrize("text,expected", [
    ("Asia/Singapore\n+0800\n", {"device_tz": "Asia/Singapore", "device_tz_offset_min": 480}),   # PLAN P9
    ("America/Argentina/Buenos_Aires\r\n-0300\r\n",
     {"device_tz": "America/Argentina/Buenos_Aires", "device_tz_offset_min": -180}),              # CRLF shells
    ("Asia/Kolkata\n+05:30\n", {"device_tz": "Asia/Kolkata", "device_tz_offset_min": 330}),
    ("Etc/GMT+8\n-0800\n", {"device_tz": "Etc/GMT+8", "device_tz_offset_min": -480}),
    ("UTC\n+0000\n", {"device_tz": "UTC", "device_tz_offset_min": 0}),
    ("\n+0800\n", {"device_tz_offset_min": 480}),                     # persist.sys.timezone unset
    ("+0800\nAsia/Singapore\n", {"device_tz": "Asia/Singapore", "device_tz_offset_min": 480}),
    ("Asia/Singapore\n+2500\n", {"device_tz": "Asia/Singapore"}),     # impossible offsets are dropped
    ("Asia/Singapore\n+0875\n", {"device_tz": "Asia/Singapore"}),
    ("/system/bin/sh: date: inaccessible or not found\n", {}),
    ("error: device offline\n", {}), ("x" * 65 + "\n", {}),
    ("", {}), (None, {}), (b"Asia/Singapore\n+0800\n", {})])
def test_device_facts_from_output(text, expected):
    assert gpcapture.device_facts_from_output(text) == expected


def test_device_facts_is_one_readonly_query_through_the_push_adb_base(monkeypatch):
    seen = []

    def run(argv, **kw):
        seen.append((list(argv), kw))
        return types.SimpleNamespace(returncode=0, stdout="Asia/Singapore\n+0800\n")

    monkeypatch.setattr(main.subprocess, "run", run)
    assert main._device_facts(["adb", "-s", SERIAL]) == {"device_tz": "Asia/Singapore", "device_tz_offset_min": 480}
    ((argv, kw),) = seen
    assert argv == ["adb", "-s", SERIAL, "shell", DEVICE_QUERY]
    assert kw["timeout"] == 15 and kw["capture_output"] is True and not kw.get("check")


@pytest.mark.parametrize("runner", [
    FakeAdb().run,                                                                          # results without stdout
    lambda argv, **kw: types.SimpleNamespace(returncode=1, stdout="Asia/Singapore\n+0800\n"),   # adb failed
    lambda argv, **kw: types.SimpleNamespace(returncode=0, stdout=None),
    lambda argv, **kw: types.SimpleNamespace(stdout="Asia/Singapore\n+0800\n"),            # no returncode
    _raising(subprocess.TimeoutExpired(["adb"], 15)),
    _raising(FileNotFoundError("adb")),
    _raising(RuntimeError("anything"))],
    ids=["fakeadb-no-stdout", "exit-1", "stdout-none", "no-returncode", "timeout", "no-adb", "any-error"])
def test_device_facts_gives_no_facts_on_any_problem(monkeypatch, runner):
    monkeypatch.setattr(main.subprocess, "run", runner)
    assert main._device_facts(["adb", "-s", SERIAL]) == {}


# --------------------------------------------------------------------------- v2: prep

def test_prep_v2_records_the_header_duration_and_the_frame_size(sandbox, tech_1080p):
    path, entry = _prep_mkv(sandbox)
    doc = _capture(sandbox["media_dir"], entry["short_id"])
    src = doc["prep"]["source"]
    assert doc["capture_version"] == 2
    assert (src["date_utc"], src["duration_s"], src["sha1"]) == ("2022-02-05T11:49:46Z", 2640.0, _sha1(path))
    assert doc["prep"]["tech"] == {"duration_mins": 45, "width_height": "1920x1080", "resolution": "1080p",
                                   "size_bytes": os.path.getsize(path)}
    facts = mvcommon.read_capture(str(sandbox["media_dir"]), entry["short_id"])[entry["hash"]]
    assert (facts["width"], facts["height"], facts["duration_s"]) == (1920, 1080, 2640.0)
    assert "pushed" not in facts                 # prep alone is not a push (P-11)


def test_a_recapture_of_the_same_bytes_never_loses_a_known_fact(tmp_path, monkeypatch):
    folder = str(tmp_path)
    master = _mkv_master(tmp_path / "Movie.mkv", MASTER_DATE, 60.0)
    sha256 = mvcommon.calculate_file_hash(master)                     # primes the SHA-1 side-channel
    gpcapture.capture_after_prep(folder, MOVIE_ID, "abc123", master, "Movie [abc123].mkv", sha256)
    monkeypatch.setattr(mvcommon, "_SHA1_CACHE", {})                  # a later run: nothing cached
    gpcapture.capture_after_prep(folder, MOVIE_ID, "abc123", master, "Movie [abc123].mkv", sha256)
    src = _capture(folder, "abc123")["prep"]["source"]
    assert (src["sha1"], src["date_utc"], src["duration_s"]) == (_sha1(master), "2022-02-05T11:49:46Z", 60.0)
    # other bytes are never completed from these bytes' facts
    other = _mkv_master(tmp_path / "Movie.mkv", MASTER_DATE, 61.0, marker=b"remastered")
    gpcapture.capture_after_prep(folder, MOVIE_ID, "abc123", other, "Movie [abc123].mkv", "f" * 64)
    src = _capture(folder, "abc123")["prep"]["source"]
    assert (src["sha256"], src["sha1"], src["dedup_key"], src["duration_s"]) == ("f" * 64, None, None, 61.0)


# --------------------------------------------------------------------------- v2: cmd_push

def test_split_push_captures_v2_facts_and_the_device_timezone(sandbox, tech_1080p, mock_device, monkeypatch):
    _, entry = _prep_mkv(sandbox)
    made = _install_mkv_split(monkeypatch)
    calls = _answering_device(monkeypatch)
    assert main.cmd_push(MOVIE_ID, "COUNT", "2", device_id=SERIAL) is True
    doc = _capture(sandbox["media_dir"], entry["short_id"])
    push = doc["pushes"][-1]
    assert doc["capture_version"] == 2 and push["complete"] is True and push["chunk_range"] is None
    assert (push["device_id"], push["device_tz"], push["device_tz_offset_min"]) == (SERIAL, "Asia/Singapore", 480)
    assert [o["uploaded_name"] for o in push["objects"]] == sorted(made)
    for i, (o, seconds) in enumerate(zip(push["objects"], (1500.0, 1234.5)), 1):
        data = made[o["uploaded_name"]]
        assert (o["role"], o["index"], o["sha256"]) == ("chunk", i, hashlib.sha256(data).hexdigest())
        assert o["sha1"] == _sha1(data) and o["dedup_key"] == gpcapture.dedup_key(o["sha1"])   # hashed this run
        assert (o["date_utc"], o["duration_s"]) == ("2026-09-24T06:03:02Z", seconds)          # F4: one second
        assert (o["width"], o["height"], o["approach_hint"]) == (1920, 1080, "hash")
    # the ONE device query ran after every upload (after the sidecar), through the pinned serial
    queries = [i for i, a in enumerate(calls) if a[-1:] == [DEVICE_QUERY]]
    sidecar = max(i for i, a in enumerate(calls) if "push" in a and a[-1].endswith(main.MVMETA_SUFFIX))
    assert len(queries) == 1 and queries[0] > sidecar
    assert all(a[1:3] == ["-s", SERIAL] for a in calls if a and a[0] == "adb")
    # the Step 3 reader hands the v2 facts on
    facts = mvcommon.read_capture(str(sandbox["media_dir"]), entry["short_id"])
    for name, data in made.items():
        f = facts[hashlib.sha256(data).hexdigest()]
        assert f["pushed"] == {"at": push["captured_at"], "device_id": SERIAL, "device_tz": "Asia/Singapore",
                               "device_tz_offset_min": 480}
        assert (f["approach_hint"], f["width"], f["height"]) == ("hash", 1920, 1080)


def test_split_push_writes_mvmeta_v2_with_every_v1_key_unchanged(sandbox, tech_1080p, mock_device, monkeypatch):
    _prep_mkv(sandbox)
    made = _install_mkv_split(monkeypatch)
    _answering_device(monkeypatch)
    assert main.cmd_push(MOVIE_ID, "COUNT", "2", device_id=SERIAL) is True
    entry = main.load_library()[MOVIE_ID]
    meta = _device_mvmeta(mock_device)
    assert set(meta) == MVMETA_V1_KEYS and meta["version"] == main.MVMETA_SCHEMA_VERSION == 2
    assert {k: meta[k] for k in MVMETA_V1_KEYS - {"version", "chunks"}} == {
        "manual_id": MOVIE_ID, "short_id": entry["short_id"], "base_filename": "Cap.2020",
        "original_hash": entry["hash"], "is_split": True, "method": "COUNT", "val": "2", "total_chunks": 2,
        "folder_path": entry["folder_path"], "remote_target_dir": "/sdcard/Media/Movies/TestMovie",
        "tech_spec": entry["tech_spec"], "metadata": entry["metadata"]}
    assert [{"filename": c["filename"], "hash": c["hash"]} for c in meta["chunks"]] == \
        [{"filename": c["filename"], "hash": c["hash"]} for c in entry["split_info"]["chunks"]]   # v1 part
    for c in meta["chunks"]:
        data = made[c["filename"]]
        assert c == {"filename": c["filename"], "hash": hashlib.sha256(data).hexdigest(), "sha1": _sha1(data),
                     "dedup_key": gpcapture.dedup_key(_sha1(data)), "date_utc": "2026-09-24T06:03:02Z",
                     "uploaded_name": c["filename"]}


def test_a_whole_file_push_in_another_process_takes_the_sha1_from_the_prep_record(
        sandbox, tech_1080p, mock_device, monkeypatch):
    path, entry = _prep_mkv(sandbox)                    # prep hashed the master: its capture holds the SHA-1
    monkeypatch.setattr(mvcommon, "_SHA1_CACHE", {})    # `push` later, in a NEW process: nothing cached
    _answering_device(monkeypatch)
    assert main.cmd_push(MOVIE_ID, device_id=SERIAL) is True
    (obj,) = _capture(sandbox["media_dir"], entry["short_id"])["pushes"][-1]["objects"]
    assert (obj["role"], obj["uploaded_name"], obj["sha256"]) == ("whole", entry["search_term"], entry["hash"])
    assert (obj["sha1"], obj["dedup_key"]) == (_sha1(path), gpcapture.dedup_key(_sha1(path)))
    assert (obj["date_utc"], obj["duration_s"], obj["approach_hint"]) == ("2022-02-05T11:49:46Z", 2640.0, "hash")
    assert (obj["width"], obj["height"]) == (1920, 1080)
    (chunk,) = _device_mvmeta(mock_device)["chunks"]
    assert chunk == {"filename": entry["search_term"], "hash": entry["hash"], "sha1": _sha1(path),
                     "dedup_key": gpcapture.dedup_key(_sha1(path)), "date_utc": "2022-02-05T11:49:46Z",
                     "uploaded_name": entry["search_term"]}


def test_a_resumed_push_keeps_what_the_earlier_partial_push_knew(sandbox, tech_1080p, mock_device, monkeypatch):
    _, entry = _prep_mkv(sandbox)
    made = _install_mkv_split(monkeypatch)
    calls = _answering_device(monkeypatch)
    assert main.cmd_push(MOVIE_ID, "COUNT", "2", chunk_range="1-1", device_id=SERIAL) is True
    monkeypatch.setattr(mvcommon, "_SHA1_CACHE", {})    # the resume runs in a new process
    assert main.cmd_push(MOVIE_ID, device_id=SERIAL) is True
    first, second = sorted(made)
    partial, resume = _capture(sandbox["media_dir"], entry["short_id"])["pushes"]
    assert (partial["chunk_range"], partial["complete"], resume["chunk_range"], resume["complete"]) == \
        ("1-1", False, None, True)
    assert partial["device_tz"] == resume["device_tz"] == "Asia/Singapore"   # both capture branches ask
    ((p1,), (p2,)) = partial["objects"], resume["objects"]
    assert (p1["uploaded_name"], p1["sha1"], p1["approach_hint"]) == (first, _sha1(made[first]), "hash")
    # chunk 2 was hashed only by the run that did not upload it: unknown now -> the DateUTC hint
    assert (p2["uploaded_name"], p2["sha1"], p2["approach_hint"]) == (second, None, "taken_second")
    meta = {c["filename"]: c for c in _device_mvmeta(mock_device)["chunks"]}
    assert meta[first]["sha1"] == _sha1(made[first])            # from the partial push's record
    assert "sha1" not in meta[second] and "dedup_key" not in meta[second]   # unknown -> not written
    assert (meta[second]["date_utc"], meta[second]["uploaded_name"]) == ("2026-09-24T06:03:02Z", second)
    assert sum(a[-1:] == [DEVICE_QUERY] for a in calls) == 2
    assert all(a[1:3] == ["-s", SERIAL] for a in calls if a and a[0] == "adb")


def test_a_runner_without_stdout_gives_no_device_facts_and_the_push_still_succeeds(sandbox, tech_1080p, monkeypatch):
    _, entry = _prep_mkv(sandbox)
    fake = FakeAdb()                                    # the protocol recorder: no result carries stdout
    monkeypatch.setattr(main.subprocess, "run", fake.run)
    assert main.cmd_push(MOVIE_ID, device_id=SERIAL) is True
    assert main.load_library()[MOVIE_ID]["status"] == "onboarded"
    assert not (sandbox["media_dir"] / main.TXN_JOURNAL_NAME).exists()
    assert [c for c in fake.calls if c[-1:] == [DEVICE_QUERY]] == [["adb", "-s", SERIAL, "shell", DEVICE_QUERY]]
    push = _capture(sandbox["media_dir"], entry["short_id"])["pushes"][-1]
    assert (push["device_id"], push["device_tz"], push["device_tz_offset_min"]) == (SERIAL, None, None)
    assert push["objects"][0]["sha256"] == entry["hash"]           # the capture itself is complete


@pytest.mark.parametrize("fail_push_n", [1, 2], ids=["before-any-upload", "after-an-upload"])
def test_a_failed_push_neither_captures_nor_asks_the_device(sandbox, tech_1080p, monkeypatch, fail_push_n):
    _, entry = _prep_mkv(sandbox)
    _install_mkv_split(monkeypatch)
    monkeypatch.setattr(mvcommon.time, "sleep", lambda *a, **k: None)   # retry backoff
    fake = FakeAdb(fail_push_n=fail_push_n)
    monkeypatch.setattr(main.subprocess, "run", fake.run)
    assert main.cmd_push(MOVIE_ID, "COUNT", "2", device_id=SERIAL) is False
    assert "pushes" not in _capture(sandbox["media_dir"], entry["short_id"])      # only the prep record
    assert not [c for c in fake.calls if c[-1:] == [DEVICE_QUERY]]


@pytest.mark.parametrize("broken", ["snapshot_push_objects", "mvmeta_identity", "capture_after_push", "_device_facts"])
def test_a_failing_capture_layer_never_breaks_a_push(sandbox, tech_1080p, mock_device, monkeypatch, capsys, broken):
    _prep_mkv(sandbox)
    monkeypatch.setattr(main if broken == "_device_facts" else gpcapture, broken, _raising(RuntimeError("boom")))
    assert main.cmd_push(MOVIE_ID, device_id=SERIAL) is True
    entry = main.load_library()[MOVIE_ID]
    assert (entry["status"], entry["uploaded"]) == ("onboarded", True)
    assert not (sandbox["media_dir"] / main.TXN_JOURNAL_NAME).exists()     # committed: nothing to recover
    out = capsys.readouterr().out
    assert "✅ SUCCESS." in out and "Rollback" not in out and "FAILED" not in out
    assert len(list(mock_device.rglob("*.mvmeta.json"))) == 1             # the sidecar still written


def test_write_remote_mvmeta_adds_only_the_known_v2_keys(monkeypatch):
    pushed = {}

    def run(argv, check=False, **kw):
        with open(argv[-2], encoding="utf-8") as fh:           # the temp sidecar, before it is removed
            pushed[argv[-1]] = json.load(fh)
        return types.SimpleNamespace(returncode=0)

    monkeypatch.setattr(main.subprocess, "run", run)
    h1, h2 = "1" * 64, "2" * 64
    entry = {"short_id": "abc123", "filename": "Movie.mkv", "hash": "3" * 64, "folder_path": "X", "tech_spec": {},
             "metadata": {}, "split_info": {"is_split": True, "method": "COUNT", "val": "2", "total_chunks": 2,
                                           "chunks": [{"filename": "Movie [abc123].chunk.001.mkv", "hash": h1},
                                                      {"filename": "Movie [abc123].chunk.002.mkv", "hash": h2}]}}
    hostile = {h1: {"sha1": S02E03_SHA1, "dedup_key": gpcapture.dedup_key(S02E03_SHA1), "date_utc": None,
                    "uploaded_name": "", "hash": "EVIL", "filename": "EVIL", "photo_id": "AF1QipFAKEmeta"},
               h2: "not a dict"}
    remote = "/sdcard/Media/Movies/M/Movie [abc123].mvmeta.json"
    assert main.write_remote_mvmeta(["adb"], "/sdcard/Media/Movies/M", MOVIE_ID, entry, identity=hostile) is True
    assert pushed[remote]["chunks"] == [
        {"filename": "Movie [abc123].chunk.001.mkv", "hash": h1, "sha1": S02E03_SHA1,
         "dedup_key": gpcapture.dedup_key(S02E03_SHA1)},
        {"filename": "Movie [abc123].chunk.002.mkv", "hash": h2}]
    for identity in (None, {}, ["not", "a", "dict"]):       # no usable identity: the v1 content, as version 2
        assert main.write_remote_mvmeta(["adb"], "/sdcard/Media/Movies/M", MOVIE_ID, entry, identity=identity)
        assert pushed[remote]["version"] == 2
        assert pushed[remote]["chunks"] == entry["split_info"]["chunks"]


def test_the_pre_commit_helpers_are_read_only(tmp_path):
    folder = str(tmp_path)
    master = _mkv_master(tmp_path / "Movie.mkv", MASTER_DATE, 60.0)
    gpcapture.capture_after_prep(folder, MOVIE_ID, "abc123", master, "Movie [abc123].mkv", "a" * 64)
    before = {p.name: p.read_bytes() for p in tmp_path.iterdir()}
    objects = gpcapture.snapshot_push_objects(
        [master], os.path.join(folder, main.SPLIT_DIR_NAME), "abc123", {}, "a" * 64, [])
    assert gpcapture.mvmeta_identity(folder, "abc123", objects)["a" * 64]["date_utc"] == "2022-02-05T11:49:46Z"
    assert {p.name: p.read_bytes() for p in tmp_path.iterdir()} == before


# --------------------------------------------------------------------------- v2: extras

def _prep_extras_title(sandbox):
    """cmd_prep MOVIE_ID with --extras over a Specials folder holding two Matroska extras, one nested.
    Needs tech_1080p. Returns ({sub_rel: path}, {sub_rel: stored item})."""
    media = sandbox["media_dir"]
    specials = media / "Specials"
    (specials / "Bonus").mkdir(parents=True)
    files = {"BTS.mkv": _mkv_master(specials / "BTS.mkv", MASTER_DATE, 300.0, marker=b"bts"),
             "Bonus/Making Of.mkv": _mkv_master(specials / "Bonus" / "Making Of.mkv", SPLIT_START, 420.5,
                                                marker=b"making")}
    master = _mkv_master(media / "Cap.2020.mkv", MASTER_DATE, 2640.0)
    assert main.cmd_prep(MOVIE_ID, master, extras=[str(specials)]) is True
    items = {it["sub_rel"]: it for it in main.load_library()[MOVIE_ID]["extras"]["groups"]["Specials"]["items"]}
    assert set(items) == set(files)
    return files, items


_EXTRA_HEADERS = {"BTS.mkv": ("2022-02-05T11:49:46Z", 300.0), "Bonus/Making Of.mkv": ("2026-09-24T06:03:02Z", 420.5)}


def test_the_extras_scan_captures_every_registered_item_beside_its_sha256_sidecar(sandbox, tech_1080p):
    files, items = _prep_extras_title(sandbox)
    for sub_rel, path in files.items():
        item, item_dir = items[sub_rel], os.path.dirname(path)
        assert os.path.isfile(os.path.join(item_dir, f"{item['short_id']}.sha256"))       # IMP-D20's sidecar
        doc = _capture(item_dir, item["short_id"])
        assert (doc["capture_version"], doc["manual_id"]) == (2, f"{MOVIE_ID}::Specials/{sub_rel}")
        src = doc["prep"]["source"]
        assert (src["sha256"], src["sha1"]) == (item["hash"], _sha1(path))     # the scan's own hash pass
        assert (src["date_utc"], src["duration_s"]) == _EXTRA_HEADERS[sub_rel]
        assert doc["prep"]["uploaded_name"] == item["search_term"] == \
            mvcommon.remote_upload_name(os.path.basename(path), item["short_id"], "extra_whole")
        assert doc["prep"]["tech"]["width_height"] == "1920x1080" and "pushes" not in doc
    # the Step 3 reader finds each capture where the library says the item lives (not pushed yet: local)
    objects = [o for o in mvcommon.iter_entry_objects(MOVIE_ID, main.load_library()[MOVIE_ID])
               if o["kind"] == "extra_whole"]
    assert {o["sub_rel"]: (o["capture"]["sha1"], o["cloud_state"]) for o in objects} == \
        {sub_rel: (_sha1(path), "local") for sub_rel, path in files.items()}


def test_an_extras_rescan_never_captures_bytes_the_library_does_not_record(sandbox, tech_1080p):
    files, items = _prep_extras_title(sandbox)
    specials = sandbox["media_dir"] / "Specials"
    bts, making = items["BTS.mkv"], items["Bonus/Making Of.mkv"]
    # (1) an unchanged re-scan refreshes the same facts
    main._extras_scan_merge_and_save(main.load_library(), MOVIE_ID, [str(specials)])
    assert _capture(specials, bts["short_id"])["prep"]["source"]["sha1"] == _sha1(files["BTS.mkv"])
    # (2) BTS was pushed + archived: a dummy stands in its place. The re-scan keeps the library item as
    #     stored (D19-B1) — and must leave its capture alone: the dummy's SHA-1 is not the item's
    library = main.load_library()
    stored = {it["sub_rel"]: it for it in library[MOVIE_ID]["extras"]["groups"]["Specials"]["items"]}
    stored["BTS.mkv"].update(status="archived", uploaded=True)
    main.save_library(library)
    _write(files["BTS.mkv"], b"DUMMY")
    capture_file = gpcapture.capture_path(str(specials), bts["short_id"])
    with open(capture_file, "rb") as fh:
        before = fh.read()
    main._extras_scan_merge_and_save(main.load_library(), MOVIE_ID, [str(specials)])
    with open(capture_file, "rb") as fh:
        assert fh.read() == before
    # (3) a genuine re-master (new real-sized bytes on a local item) is captured afresh
    remaster = _mkv_master(specials / "Bonus" / "Making Of.mkv", SPLIT_START, 421.0, marker=b"v2")
    main._extras_scan_merge_and_save(main.load_library(), MOVIE_ID, [str(specials)])
    src = _capture(specials / "Bonus", making["short_id"])["prep"]["source"]
    with open(remaster, "rb") as fh:
        assert (src["sha256"], src["sha1"], src["duration_s"]) == \
            (hashlib.sha256(fh.read()).hexdigest(), _sha1(remaster), 421.0)


def test_extras_push_captures_each_item_and_asks_the_device_once_per_title(
        sandbox, tech_1080p, mock_device, monkeypatch):
    files, items = _prep_extras_title(sandbox)
    monkeypatch.setattr(mvcommon, "_SHA1_CACHE", {})    # `push --extras` later, in a new process
    calls = _answering_device(monkeypatch)
    assert main.push_title_extras(main.load_library(), MOVIE_ID, ("NONE", None), device_id=SERIAL) is True
    queries = [i for i, a in enumerate(calls) if a[-1:] == [DEVICE_QUERY]]
    first_mv = min(i for i, a in enumerate(calls) if a[3:5] == ["shell", "mv"])
    assert len(queries) == 1 and queries[0] > first_mv          # once per title, after the first upload
    assert all(a[1:3] == ["-s", SERIAL] for a in calls if a and a[0] == "adb")
    for sub_rel, path in files.items():
        item = items[sub_rel]
        push = _capture(os.path.dirname(path), item["short_id"])["pushes"][-1]
        assert (push["device_id"], push["device_tz"], push["device_tz_offset_min"]) == (SERIAL, "Asia/Singapore", 480)
        assert push["remote_dir"] == "/sdcard/Media/Movies/TestMovie/" + os.path.dirname("Specials/" + sub_rel)
        (obj,) = push["objects"]
        assert (obj["role"], obj["uploaded_name"], obj["sha256"]) == ("whole", item["search_term"], item["hash"])
        assert (obj["sha1"], obj["approach_hint"]) == (_sha1(path), "hash")    # completed from the scan's record
        assert (obj["date_utc"], obj["duration_s"]) == _EXTRA_HEADERS[sub_rel]
        assert (obj["width"], obj["height"]) == (1920, 1080)
        stem = os.path.splitext(os.path.basename(path))[0]
        meta = _device_mvmeta(mock_device, f"{stem} [{item['short_id']}].mvmeta.json")
        assert (meta["version"], meta["manual_id"]) == (2, f"{MOVIE_ID}::Specials/{sub_rel}")
        (chunk,) = meta["chunks"]
        assert (chunk["sha1"], chunk["uploaded_name"]) == (_sha1(path), item["search_term"])
    objects = [o for o in mvcommon.iter_entry_objects(MOVIE_ID, main.load_library()[MOVIE_ID])
               if o["kind"] == "extra_whole"]
    assert {o["cloud_state"] for o in objects} == {"unverified"}      # pushed + captured, not confirmed (P-11)


def test_split_extras_capture_each_chunk_and_write_mvmeta_v2(sandbox, tech_1080p, mock_device, monkeypatch):
    files, items = _prep_extras_title(sandbox)
    made = _install_mkv_split(monkeypatch)
    _answering_device(monkeypatch)
    assert main.push_title_extras(main.load_library(), MOVIE_ID, ("COUNT", "2"), device_id=SERIAL) is True
    for sub_rel, path in files.items():
        item = items[sub_rel]
        stem = os.path.splitext(os.path.basename(path))[0]
        names = [f"{stem} [{item['short_id']}].chunk.{i:03d}.mkv" for i in (1, 2)]
        push = _capture(os.path.dirname(path), item["short_id"])["pushes"][-1]
        assert [(o["role"], o["index"], o["uploaded_name"]) for o in push["objects"]] == \
            [("chunk", 1, names[0]), ("chunk", 2, names[1])]
        for o in push["objects"]:
            data = made[o["uploaded_name"]]
            assert (o["sha256"], o["sha1"], o["approach_hint"]) == \
                (hashlib.sha256(data).hexdigest(), _sha1(data), "hash")       # hashed by push_one_extra's split
            assert (o["date_utc"], o["width"], o["height"]) == ("2026-09-24T06:03:02Z", 1920, 1080)
        meta = _device_mvmeta(mock_device, f"{stem} [{item['short_id']}].mvmeta.json")
        assert [(c["filename"], c["sha1"], c["uploaded_name"]) for c in meta["chunks"]] == \
            [(n, _sha1(made[n]), n) for n in names]
    objects = [o for o in mvcommon.iter_entry_objects(MOVIE_ID, main.load_library()[MOVIE_ID])
               if o["kind"] == "extra_chunk"]
    assert len(objects) == 4 and {o["cloud_state"] for o in objects} == {"unverified"}


def test_an_extras_title_folder_named_like_the_chunk_dir_records_the_real_upload_name(
        sandbox, tech_1080p, mock_device, monkeypatch):
    """push_one_extra names by is_split, so its snapshot marks chunks by the item's OWN chunk dir — not by
    the bare `_parts` substring, which a title folder may legitimately contain."""
    title = sandbox["local_root"] / "Movies" / "Spare_parts (2015)"
    specials = title / "Specials"
    specials.mkdir(parents=True)
    _mkv_master(specials / "BTS.mkv", MASTER_DATE, 300.0)
    assert main.cmd_prep(MOVIE_ID, _mkv_master(title / "Spare.Parts.2015.mkv", MASTER_DATE, 5400.0),
                         extras=[str(specials)]) is True
    _answering_device(monkeypatch)
    assert main.push_title_extras(main.load_library(), MOVIE_ID, ("NONE", None), device_id=SERIAL) is True
    (item,) = main.load_library()[MOVIE_ID]["extras"]["groups"]["Specials"]["items"]
    renamed = f"BTS [{item['short_id']}].mkv"
    assert renamed in {p.name for p in mock_device.rglob("*.mkv")}          # what push_one_extra uploaded
    (obj,) = _capture(specials, item["short_id"])["pushes"][-1]["objects"]
    assert (obj["role"], obj["uploaded_name"]) == ("whole", renamed)


def test_no_extra_uploaded_means_no_device_query_and_no_push_capture(sandbox, tech_1080p, monkeypatch):
    files, items = _prep_extras_title(sandbox)
    monkeypatch.setattr(mvcommon.time, "sleep", lambda *a, **k: None)
    calls = []

    def run(argv, check=False, **kw):
        argv = list(argv)
        calls.append(argv)
        if "push" in argv and check:
            raise subprocess.CalledProcessError(1, argv)          # every upload fails
        return types.SimpleNamespace(returncode=0, stdout="")

    monkeypatch.setattr(main.subprocess, "run", run)
    assert main.push_title_extras(main.load_library(), MOVIE_ID, ("NONE", None), device_id=SERIAL) is False
    assert not [a for a in calls if a[-1:] == [DEVICE_QUERY]]
    for sub_rel, path in files.items():
        assert "pushes" not in _capture(os.path.dirname(path), items[sub_rel]["short_id"])


def test_a_failing_extras_capture_never_breaks_prep(sandbox, tech_1080p, monkeypatch, capsys):
    """cmd_prep scans its --extras AFTER its journal committed, inside its own try: an exception escaping
    the capture would reach cmd_prep's rollback and undo a committed prep. It must never escape."""
    monkeypatch.setattr(gpcapture, "capture_after_extras_scan", _raising(RuntimeError("boom")))
    _prep_extras_title(sandbox)                           # asserts cmd_prep -> True and both items registered
    entry = main.load_library()[MOVIE_ID]
    assert entry["status"] == "local_ready" and (sandbox["media_dir"] / "uid").exists()     # NOT rolled back
    assert not (sandbox["media_dir"] / main.TXN_JOURNAL_NAME).exists()
    out = capsys.readouterr().out
    assert "Prep failed" not in out and "Rollback" not in out


@pytest.mark.parametrize("broken", ["snapshot_push_objects", "mvmeta_identity", "capture_after_push", "_device_facts"])
def test_a_failing_capture_layer_never_breaks_an_extras_push(sandbox, tech_1080p, mock_device, monkeypatch, broken):
    _prep_extras_title(sandbox)
    monkeypatch.setattr(main if broken == "_device_facts" else gpcapture, broken, _raising(RuntimeError("boom")))
    assert main.push_title_extras(main.load_library(), MOVIE_ID, ("NONE", None), device_id=SERIAL) is True
    items = main.load_library()[MOVIE_ID]["extras"]["groups"]["Specials"]["items"]
    assert {(it["status"], it["uploaded"]) for it in items} == {("onboarded", True)}
    assert len(list(mock_device.rglob("*.mvmeta.json"))) == 2


# --------------------------------------------------------------------------- v1 files stay valid

def test_a_v1_capture_stays_readable_and_a_v2_push_builds_on_it(tmp_path):
    """A capture written by the v1 code (cb4dd91: no duration, hint or device timezone) reads as before;
    a v2 push in a later process completes its unknown SHA-1 from the v1 prep record and leaves the v1
    records untouched."""
    folder = str(tmp_path)
    master = _mkv_master(tmp_path / "Show.S01E01.mkv", MASTER_DATE, 2640.0)
    with open(master, "rb") as fh:
        data = fh.read()
    sha256, sha1 = hashlib.sha256(data).hexdigest(), _sha1(data)
    v1 = {"capture_version": 1, "manual_id": "tv-x-s01e01", "short_id": "def456",
          "note": "IMP-C25 interim capture: identity facts recorded at prep/push for the later Google Photos sweep",
          "prep": {"captured_at": "2026-09-20T10:00:00Z",
                   "source": {"local_name": "Show.S01E01.mkv", "size_bytes": len(data),
                              "mtime_utc": "2026-09-20T09:59:00Z", "sha256": sha256, "sha1": sha1,
                              "dedup_key": gpcapture.dedup_key(sha1), "date_utc": "2022-02-05T11:49:46Z"},
                   "uploaded_name": "Show.S01E01 [def456].mkv",
                   "tech": {"duration_mins": 44, "width_height": "1920x1080"}},
          "pushes": [{"captured_at": "2026-09-20T11:00:00Z", "device_id": "FAKESERIAL01",
                      "remote_dir": "/sdcard/Media/Series/Show", "chunk_range": None, "complete": True,
                      "objects": [{"local_name": "Show.S01E01.mkv", "size_bytes": len(data),
                                   "mtime_utc": "2026-09-20T09:59:00Z", "sha256": sha256, "sha1": None,
                                   "dedup_key": None, "date_utc": "2022-02-05T11:49:46Z", "role": "whole",
                                   "uploaded_name": "Show.S01E01 [def456].mkv", "index": None}]}],
          "updated_at": "2026-09-20T11:00:00Z"}
    (tmp_path / "def456.gpcapture.json").write_text(json.dumps(v1, indent=1), encoding="utf-8")
    facts = mvcommon.read_capture(folder, "def456")[sha256]
    assert (facts["sha1"], facts["width"], facts["pushed"]) == \
        (sha1, 1920, {"at": "2026-09-20T11:00:00Z", "device_id": "FAKESERIAL01"})
    assert "approach_hint" not in facts and "duration_s" not in facts        # v1 knew neither
    objects = gpcapture.snapshot_push_objects(
        [master], os.path.join(folder, main.SPLIT_DIR_NAME), "def456", {}, sha256, [],
        {"width_height": "1920x1080"})
    assert objects[0]["sha1"] is None                                      # a later process: nothing cached
    assert gpcapture.capture_after_push(folder, "tv-x-s01e01", "def456", objects, device_id="FAKESERIAL01",
                                        remote_dir="/sdcard/Media/Series/Show",
                                        device_facts={"device_tz": "Asia/Singapore", "device_tz_offset_min": 480})
    doc = _capture(folder, "def456")
    assert doc["capture_version"] == 2 and doc["prep"] == v1["prep"] and doc["pushes"][0] == v1["pushes"][0]
    new = doc["pushes"][1]["objects"][0]
    assert (new["sha1"], new["duration_s"], new["approach_hint"]) == (sha1, 2640.0, "hash")
    facts = mvcommon.read_capture(folder, "def456")[sha256]
    assert facts["pushed"] == {"at": doc["pushes"][1]["captured_at"], "device_id": "FAKESERIAL01",
                               "device_tz": "Asia/Singapore", "device_tz_offset_min": 480}
    assert (facts["approach_hint"], facts["duration_s"], facts["sha1"]) == ("hash", 2640.0, sha1)


# --------------------------------------------------------------------------- OD-3: the rollback journal is untouched

_LAYER = ("capture_after_prep", "capture_after_extras_scan", "capture_after_push", "device_facts")
_JOURNAL_OPS = {"create_file", "create_dir", "create_entry", "set_field", "link_child", "create_reproducible"}


def _norm(obj, root):
    """`obj` with the world's root path replaced by <ROOT> in every string."""
    if isinstance(obj, str):
        return obj.replace(root, "<ROOT>")
    if isinstance(obj, (list, tuple)):
        return type(obj)(_norm(x, root) for x in obj)
    if isinstance(obj, dict):
        return {k: _norm(v, root) for k, v in obj.items()}
    return obj


def _world(tmp_path, monkeypatch, name):
    """A fresh library + LOCAL_ROOT under tmp_path/<name>: the `sandbox` redirect re-done per world — BOTH the
    mvcommon and the main bindings (the IMP-A1 binding hazard), hard-guarded against the real C:\\Media."""
    root = tmp_path / name
    lib = root / "lib"
    lib.mkdir(parents=True)
    for attr, path in (("LIBRARY_MOVIES", lib / "m.json"), ("LIBRARY_SERIES", lib / "s.json"),
                       ("LIBRARY_ANIME", lib / "a.json"), ("LOCAL_ROOT", root / "Media")):
        assert tmp_path.resolve() in path.resolve().parents and "C:\\Media" not in str(path)
        monkeypatch.setattr(mvcommon, attr, str(path))
        monkeypatch.setattr(main, attr, str(path))
    monkeypatch.setattr(mvcommon, "LIBRARY_OTHERS", str(lib / "o.json"))
    return root


def _cap_movie(root):
    folder = root / "Media" / "Movies" / "Cap"
    folder.mkdir(parents=True, exist_ok=True)
    return _mkv_master(folder / "Cap.2020.mkv", MASTER_DATE, 2640.0)


def _cap_specials(root):
    folder = root / "Media" / "Movies" / "Cap" / "Specials"
    (folder / "Bonus").mkdir(parents=True)
    _mkv_master(folder / "BTS.mkv", MASTER_DATE, 300.0, marker=b"bts")
    _mkv_master(folder / "Bonus" / "Making Of.mkv", SPLIT_START, 420.5, marker=b"making")
    return str(folder)


def _sc_prep(root):
    season = root / "Media" / "Series" / "Cap" / "Season 01"
    season.mkdir(parents=True)
    return (main.cmd_prep(MOVIE_ID, _cap_movie(root)),
            main.cmd_prep("tv-en-2015-cap-s01e01", _mkv_master(season / "Cap.S01E01.mkv", MASTER_DATE, 1320.0)))


def _sc_prep_extras(root):
    return (main.cmd_prep(MOVIE_ID, _cap_movie(root), extras=[_cap_specials(root)]),)


def _sc_push_split(root):
    return main.cmd_prep(MOVIE_ID, _cap_movie(root)), main.cmd_push(MOVIE_ID, "COUNT", "2", device_id=SERIAL)


def _sc_push_range_then_resume(root):
    return (main.cmd_prep(MOVIE_ID, _cap_movie(root)),
            main.cmd_push(MOVIE_ID, "COUNT", "2", chunk_range="1-1", device_id=SERIAL),
            main.cmd_push(MOVIE_ID, device_id=SERIAL))


def _sc_push_whole(root):
    return main.cmd_prep(MOVIE_ID, _cap_movie(root)), main.cmd_push(MOVIE_ID, device_id=SERIAL)


def _sc_autopilot(root):
    return (main.cmd_prep_push_rep(MOVIE_ID, _cap_movie(root), device_id=SERIAL),
            main.load_library()[MOVIE_ID]["status"])


def _sc_extras_lifecycle(root):
    results = (main.cmd_prep(MOVIE_ID, _cap_movie(root)),
               main.cmd_add_extras(MOVIE_ID, [_cap_specials(root)], device_id=SERIAL))
    items = main.load_library()[MOVIE_ID]["extras"]["groups"]["Specials"]["items"]
    return results + (sorted(it["status"] for it in items),)


# name -> (the commands, a failing-adb predicate, their results, the capture-layer calls expected when ON)
_JOURNAL_SCENARIOS = {
    "prep": (_sc_prep, None, (True, True), {"capture_after_prep": 2}),
    "prep_extras": (_sc_prep_extras, None, (True,), {"capture_after_prep": 1, "capture_after_extras_scan": 2}),
    "push_split": (_sc_push_split, None, (True, True),
                   {"capture_after_prep": 1, "capture_after_push": 1, "device_facts": 1}),
    "push_range_then_resume": (_sc_push_range_then_resume, None, (True, True, True),
                               {"capture_after_prep": 1, "capture_after_push": 2, "device_facts": 2}),
    "push_whole": (_sc_push_whole, None, (True, True),
                   {"capture_after_prep": 1, "capture_after_push": 1, "device_facts": 1}),
    "push_fails_after_an_upload": (_sc_push_split, lambda argv: "push" in argv and ".chunk.002" in argv[-1],
                                   (True, False), {"capture_after_prep": 1}),
    "push_fails_before_any_upload": (_sc_push_split, lambda argv: "push" in argv, (True, False),
                                     {"capture_after_prep": 1}),
    "autopilot_with_replace": (_sc_autopilot, None, (None, "archived"),
                               {"capture_after_prep": 1, "capture_after_push": 1, "device_facts": 1}),
    "extras_lifecycle": (_sc_extras_lifecycle, None, (True, None, ["archived", "archived"]),
                         {"capture_after_prep": 1, "capture_after_extras_scan": 2, "capture_after_push": 2,
                          "device_facts": 1}),
}

_PREP_JOURNAL = [("open", MOVIE_ID), ("flush", (), False), ("flush", ("create_file",), False),
                 ("flush", ("create_file", "create_file"), False),
                 ("flush", ("create_file", "create_file", "create_entry"), False), ("commit", MOVIE_ID)]


@pytest.mark.parametrize("name", list(_JOURNAL_SCENARIOS))
def test_od3_journal_records_are_identical_with_and_without_the_capture_layer(
        tmp_path, monkeypatch, tech_1080p, fake_dummy, name):
    """OD-3 acceptance (PLAN Step 5): the capture layer changes NO rollback behaviour. Every RollbackJournal
    event — open, each durable flush (its full on-disk content: records + the PONR flag), commit, rollback —
    is recorded while the same commands run in two fresh worlds: capture layer ON (the real Step 5 code) and
    OFF (neutralized: the journal behaviour of a MediaVault without it). The journals, the libraries and
    every other adb call must be identical; ON must show the layer really ran, and never while a rollback
    journal was open (post-commit only)."""
    commands, fail, expected, layer_calls = _JOURNAL_SCENARIOS[name]
    _install_mkv_split(monkeypatch)
    monkeypatch.setattr(mvcommon.time, "sleep", lambda *a, **k: None)          # retry backoff
    captures = ("capture_after_prep", "capture_after_extras_scan", "capture_after_push")
    real = {n: getattr(gpcapture, n) for n in captures + ("snapshot_push_objects", "mvmeta_identity")}
    real["device_facts"] = main._device_facts
    journal_cls = main.RollbackJournal
    j_init, j_flush, j_commit, j_rollback = (journal_cls.__init__, journal_cls._flush, journal_cls.commit,
                                             journal_cls.rollback)
    live = {}

    def init(self, folder_path, manual_id):
        live["events"].append(("open", manual_id))
        j_init(self, folder_path, manual_id)

    def flush(self):
        j_flush(self)
        with open(self.path, encoding="utf-8") as fh:                    # exactly what is durably on disk
            live["events"].append(("flush", json.load(fh)))

    def commit(self):
        live["events"].append(("commit", self.manual_id))
        j_commit(self)

    def rollback(self, library):
        live["events"].append(("rollback", self.manual_id))
        return j_rollback(self, library)

    for attr, fn in (("__init__", init), ("_flush", flush), ("commit", commit), ("rollback", rollback)):
        monkeypatch.setattr(journal_cls, attr, fn)

    def logged(kind, fn, events):
        def wrapper(*a, **kw):
            events.append((kind,))
            return fn(*a, **kw)
        return wrapper

    def run(world, capture_on):
        events = live["events"] = []
        root = _world(tmp_path, monkeypatch, world)
        if capture_on:
            for n in captures:
                monkeypatch.setattr(gpcapture, n, logged(n, real[n], events))
            monkeypatch.setattr(gpcapture, "snapshot_push_objects", real["snapshot_push_objects"])
            monkeypatch.setattr(gpcapture, "mvmeta_identity", real["mvmeta_identity"])
            monkeypatch.setattr(main, "_device_facts", logged("device_facts", real["device_facts"], events))
        else:
            for n in captures:
                monkeypatch.setattr(gpcapture, n, lambda *a, **kw: None)
            monkeypatch.setattr(gpcapture, "snapshot_push_objects", lambda *a, **kw: [])
            monkeypatch.setattr(gpcapture, "mvmeta_identity", lambda *a, **kw: {})
            monkeypatch.setattr(main, "_device_facts", lambda adb_base: {})
        calls = []

        def adb(argv, check=False, **kw):
            argv = list(argv)
            calls.append(argv)
            if argv[-1:] == [DEVICE_QUERY]:
                return types.SimpleNamespace(returncode=0, stdout="Asia/Singapore\n+0800\n")
            if fail is not None and fail(argv):
                if check:
                    raise subprocess.CalledProcessError(1, argv)
                return types.SimpleNamespace(returncode=1, stdout="")
            return types.SimpleNamespace(returncode=0, stdout="")

        monkeypatch.setattr(main.subprocess, "run", adb)
        results = commands(root)
        base = str(root)
        others = [a[:-2] + ["<temp sidecar>", a[-1]] if "push" in a and a[-1].endswith(main.MVMETA_SUFFIX) else a
                  for a in (_norm(a, base) for a in calls if a[-1:] != [DEVICE_QUERY])]
        return {"results": results, "events": events,
                "journal": [_norm(e, base) for e in events if e[0] in ("open", "flush", "commit", "rollback")],
                "library": _norm(main.load_library(), base), "adb": others,
                "queries": [a for a in calls if a[-1:] == [DEVICE_QUERY]],
                "captures": sorted(str(p.relative_to(root)) for p in root.rglob("*" + gpcapture.CAPTURE_SUFFIX))}

    on, off = run("on", True), run("off", False)

    assert on["results"] == off["results"] == expected
    assert on["journal"] and on["journal"] == off["journal"]       # THE proof: identical journals, in order
    assert on["library"] == off["library"]                          # capture never writes the library (P-11)
    assert on["adb"] == off["adb"]                                  # ... nor changes any other device call
    # the layer really ran when ON (else the comparison proves nothing) and was absent when OFF
    assert Counter(e[0] for e in on["events"] if e[0] in _LAYER) == Counter(layer_calls)
    assert len(on["queries"]) == layer_calls.get("device_facts", 0)
    assert all(q[:3] == ["adb", "-s", SERIAL] for q in on["queries"])
    assert on["captures"] and not off["captures"] and not off["queries"]
    # post-commit only: no capture write and no device query while any rollback journal is open
    depth = 0
    for event in on["events"]:
        if event[0] == "open":
            depth += 1
        elif event[0] in ("commit", "rollback"):
            depth -= 1
        elif event[0] in _LAYER:
            assert depth == 0, f"{event[0]} ran inside an open rollback journal"
    assert depth == 0
    # the journal vocabulary is exactly the pre-existing one; golden pins for two scenarios
    assert {r["op"] for e in on["journal"] if e[0] == "flush" for r in e[1]["records"]} <= _JOURNAL_OPS
    shape = [(e[0], tuple(r["op"] for r in e[1]["records"]), e[1]["crossed_ponr"]) if e[0] == "flush" else e
             for e in on["journal"]]
    if name == "push_split":                 # a prep + a new-split push, as before Step 5
        assert shape == _PREP_JOURNAL + [
            ("open", MOVIE_ID), ("flush", (), False), ("flush", ("create_dir",), False),
            ("flush", ("create_dir", "create_dir"), False),
            ("flush", ("create_dir", "create_dir", "set_field"), False), ("commit", MOVIE_ID)]
    if name == "autopilot_with_replace":     # ... and the replace's point-of-no-return marker, where it was
        assert shape == _PREP_JOURNAL + [
            ("open", MOVIE_ID), ("flush", (), False), ("commit", MOVIE_ID),
            ("open", MOVIE_ID), ("flush", (), False), ("flush", ("create_file",), False),
            ("flush", ("create_file",), True), ("commit", MOVIE_ID)]
