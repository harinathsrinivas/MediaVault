"""IMP-C8 — post-push remote sha256sum verification tests (Candidate B).

cmd_push, when PUSH_VERIFY_REMOTE=True, runs `adb shell sha256sum '<remote>'`
after each push+atomic-mv and compares the device hash to the stored chunk
hash. A mismatch raises CalledProcessError, which feeds C2's retry() wrapper
(re-push -> re-verify). A sha256sum that is unavailable (the command itself
exits non-zero) is warn-and-skipped, NOT retried.

Candidate B — real-bytes round trip through the shared stateful `mock_device`
fixture (conftest gained an `elif sub[0] == "sha256sum":` branch computing
hashlib.sha256 over the actual file copied into device_dir). The hash is
computed over the bytes that were really "pushed", so this exercises a genuine
push-vs-hash round trip. Fault injection corrupts the on-device bytes (or seeds
a deliberately wrong stored hash) so the real sha256 genuinely differs.
"""
import hashlib
import json
import subprocess

import pytest

import main
import mvcommon


ENTRY_ID = "mov_test_c8_verify"
CHUNK_NAME = "test_movie [abc123].chunk.001.mkv"
CHUNK_BYTES = b"real-chunk-payload-bytes-for-c8"
GOOD_HASH = hashlib.sha256(CHUNK_BYTES).hexdigest()
WRONG_HASH = hashlib.sha256(b"different-bytes").hexdigest()


@pytest.fixture(autouse=True)
def _no_retry_sleep(monkeypatch):
    """Make C2 retry backoff instant (sleep lives in mvcommon.time.sleep)."""
    monkeypatch.setattr(mvcommon.time, "sleep", lambda *_a, **_k: None)


def _seed_split_resume_entry(sandbox, stored_hash):
    """Seed a split entry in the resume state: a populated _parts/ folder with a
    real chunk file, and split_info.chunks[0].hash = stored_hash. cmd_push then
    takes the resume branch and pushes the real chunk bytes into the mock device.
    """
    media_dir = sandbox["media_dir"]
    (media_dir / "test_movie.mkv").write_bytes(b"master-bytes")

    parts_dir = media_dir / "_parts"
    parts_dir.mkdir()
    (parts_dir / CHUNK_NAME).write_bytes(CHUNK_BYTES)

    entry = {
        ENTRY_ID: {
            "short_id": "abc123",
            "filename": "test_movie.mkv",
            "folder_path": str(media_dir),
            "status": "local_ready",
            "uploaded": False,
            "hash": "originalhash",
            "metadata": {"title": ENTRY_ID, "year": 2024},
            "tech_spec": {"resolution": "1080p"},
            "split_info": {
                "is_split": True,
                "method": "SIZE_MB",
                "val": "9900",
                "total_chunks": 1,
                "chunks": [{"filename": CHUNK_NAME, "hash": stored_hash}],
            },
        }
    }
    sandbox["lib_movies"].write_text(json.dumps(entry), encoding="utf-8")
    sandbox["lib_series"].write_text("{}", encoding="utf-8")
    sandbox["lib_anime"].write_text("{}", encoding="utf-8")
    return {"entry_id": ENTRY_ID, "media_dir": media_dir, "parts_dir": parts_dir,
            "lib_movies": sandbox["lib_movies"]}


def _load_entry(fix):
    data = json.loads(fix["lib_movies"].read_text(encoding="utf-8"))
    return data[fix["entry_id"]]


def _count_sha256(monkeypatch):
    """Wrap main.subprocess.run (already the mock_device fake) so we can count
    sha256sum calls without disturbing the real-bytes round trip. Returns a list
    that accumulates each sha256sum argv."""
    inner = main.subprocess.run
    recorded = {"sha256": [], "rm": []}

    def counting(argv, *a, **k):
        if "shell" in argv and "sha256sum" in argv:
            recorded["sha256"].append(list(argv))
        if "shell" in argv and "rm" in argv:
            recorded["rm"].append(list(argv))
        return inner(argv, *a, **k)

    monkeypatch.setattr(main.subprocess, "run", counting)
    return recorded


# (a) PUSH_VERIFY_REMOTE=False -> zero sha256sum calls (regression guard)
def test_verify_off_no_sha256sum_calls(sandbox, mock_device, monkeypatch):
    fix = _seed_split_resume_entry(sandbox, GOOD_HASH)
    monkeypatch.setattr(main, "PUSH_VERIFY_REMOTE", False)
    rec = _count_sha256(monkeypatch)

    result = main.cmd_push(fix["entry_id"])
    assert result is True
    assert rec["sha256"] == [], "no sha256sum call may run when verify is off"

    entry = _load_entry(fix)
    assert entry["uploaded"] is True
    assert entry["status"] == "onboarded"


# (b) Hash matches (real round trip) -> push succeeds, exactly one sha256sum/chunk
def test_verify_on_hash_matches(sandbox, mock_device, monkeypatch):
    fix = _seed_split_resume_entry(sandbox, GOOD_HASH)
    monkeypatch.setattr(main, "PUSH_VERIFY_REMOTE", True)
    rec = _count_sha256(monkeypatch)

    result = main.cmd_push(fix["entry_id"])
    assert result is True
    assert len(rec["sha256"]) == 1

    # Real round trip: the chunk landed on the mock device and the sha256 the
    # fixture computed over those bytes equals the stored hash (so verification
    # passed against genuinely-pushed bytes, not a canned response).
    pushed = [p for p in mock_device.rglob("*" + CHUNK_NAME[-10:]) if p.is_file()]
    assert pushed, "chunk should have landed on the mock device"
    assert hashlib.sha256(pushed[0].read_bytes()).hexdigest() == GOOD_HASH

    entry = _load_entry(fix)
    assert entry["uploaded"] is True
    assert entry["status"] == "onboarded"


# (c) Mismatch once then matches -> C2 retry self-heals.
# Corrupt the device bytes after the FIRST push so the real sha256 differs; on the
# retry, push the clean bytes again (mock_device copies the real source -> matches).
def test_verify_mismatch_then_match_retries(sandbox, mock_device, monkeypatch):
    fix = _seed_split_resume_entry(sandbox, GOOD_HASH)
    monkeypatch.setattr(main, "PUSH_VERIFY_REMOTE", True)

    inner = main.subprocess.run
    state = {"push_n": 0, "sha256": [], "rm": []}

    def corrupting(argv, *a, **k):
        is_push = "push" in argv and not str(argv[-1]).endswith(main.MVMETA_SUFFIX)
        r = inner(argv, *a, **k)
        if is_push:
            state["push_n"] += 1
            if state["push_n"] == 1:
                # Corrupt the freshly-pushed .partial so the post-mv sha256 mismatches.
                remote = argv[-1].lstrip("/")
                dev = mock_device / remote
                if dev.exists():
                    dev.write_bytes(b"corrupted-on-wire")
        if "shell" in argv and "sha256sum" in argv:
            state["sha256"].append(list(argv))
        if "shell" in argv and "rm" in argv:
            state["rm"].append(list(argv))
        return r

    monkeypatch.setattr(main.subprocess, "run", corrupting)

    result = main.cmd_push(fix["entry_id"])
    assert result is True

    # sha256sum ran twice: corrupt mismatch -> retry -> clean match.
    assert len(state["sha256"]) == 2
    # on_retry issued exactly one rm of the .partial.
    assert len(state["rm"]) == 1
    assert state["rm"][0][-1].strip("'").endswith(main.PARTIAL_SUFFIX)

    entry = _load_entry(fix)
    assert entry["uploaded"] is True
    assert entry["status"] == "onboarded"


# (d) Mismatch on all attempts -> failure contract unchanged.
# Stored hash is deliberately WRONG, so the real device sha256 never matches it.
def test_verify_mismatch_all_retries_fails(sandbox, mock_device, monkeypatch):
    fix = _seed_split_resume_entry(sandbox, WRONG_HASH)
    monkeypatch.setattr(main, "PUSH_VERIFY_REMOTE", True)
    rec = _count_sha256(monkeypatch)

    result = main.cmd_push(fix["entry_id"])
    assert result is False
    assert isinstance(result, bool)
    assert len(rec["sha256"]) == 3  # one per retry attempt

    entry = _load_entry(fix)
    assert entry["uploaded"] is False
    assert entry["status"] == "local_ready"
    assert fix["parts_dir"].exists()
    assert any(fix["parts_dir"].iterdir())


# (e) sha256sum unavailable -> warn and skip, push succeeds, no retry.
# Disable the conftest sha256sum branch by making it raise CalledProcessError.
def test_verify_unavailable_warns_and_skips(sandbox, mock_device, monkeypatch, capsys):
    fix = _seed_split_resume_entry(sandbox, GOOD_HASH)
    monkeypatch.setattr(main, "PUSH_VERIFY_REMOTE", True)

    inner = main.subprocess.run
    state = {"sha256": []}

    def unavailable_sha256(argv, *a, **k):
        if "shell" in argv and "sha256sum" in argv:
            state["sha256"].append(list(argv))
            if k.get("check"):
                raise subprocess.CalledProcessError(127, argv)
        return inner(argv, *a, **k)

    monkeypatch.setattr(main.subprocess, "run", unavailable_sha256)

    result = main.cmd_push(fix["entry_id"])
    assert result is True
    assert len(state["sha256"]) == 1  # attempted once, not retried

    out = capsys.readouterr().out
    assert "sha256sum unavailable" in out
    assert "Retry" not in out

    entry = _load_entry(fix)
    assert entry["uploaded"] is True
    assert entry["status"] == "onboarded"
