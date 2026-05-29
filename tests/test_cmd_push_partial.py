"""Tests for cmd_push's .partial + atomic-rename + .mvmeta.json protocol (IMP-G1).

All ADB interaction is mocked via a fake `subprocess.run` recorder; no real
device, no real C:\\Media, no real library JSON. Reuses the sandbox fixtures
from conftest.py (which hard-guard against the real media root).
"""
import json
import os
import subprocess

import pytest

import main


# ---------------------------------------------------------------------------
# Fake ADB recorder
# ---------------------------------------------------------------------------
class FakeAdb:
    """Records every adb argv and classifies it. Configurable to fail the Nth
    `push` (of a real chunk, not the mvmeta sidecar) or the Nth `shell mv`."""

    def __init__(self, fail_push_n=None, fail_mv_n=None, fail_mvmeta=False):
        self.calls = []          # list of argv lists, in order
        self.fail_push_n = fail_push_n     # 1-based index of chunk push to fail
        self.fail_mv_n = fail_mv_n         # 1-based index of mv to fail
        self.fail_mvmeta = fail_mvmeta     # fail the .mvmeta.json push
        self._chunk_push_count = 0
        self._mv_count = 0

    def run(self, argv, check=False, **kwargs):
        self.calls.append(list(argv))

        is_push = "push" in argv
        is_mv = "shell" in argv and "mv" in argv
        dest = argv[-1] if argv else ""
        is_mvmeta = is_push and dest.endswith(main.MVMETA_SUFFIX)

        if is_push and not is_mvmeta:
            self._chunk_push_count += 1
            if self.fail_push_n is not None and self._chunk_push_count == self.fail_push_n:
                if check:
                    raise subprocess.CalledProcessError(1, argv)
        elif is_mvmeta:
            if self.fail_mvmeta and check:
                raise subprocess.CalledProcessError(1, argv)
        elif is_mv:
            self._mv_count += 1
            if self.fail_mv_n is not None and self._mv_count == self.fail_mv_n:
                if check:
                    raise subprocess.CalledProcessError(1, argv)

        class _R:
            returncode = 0
        return _R()

    # --- analysis helpers ---
    def pushes(self):
        """All `push` argvs (chunks + mvmeta)."""
        return [c for c in self.calls if "push" in c]

    def chunk_pushes(self):
        return [c for c in self.pushes() if not c[-1].endswith(main.MVMETA_SUFFIX)]

    def mvmeta_pushes(self):
        return [c for c in self.pushes() if c[-1].endswith(main.MVMETA_SUFFIX)]

    def mvs(self):
        return [c for c in self.calls if "shell" in c and "mv" in c]


# ---------------------------------------------------------------------------
# Split-entry fixture: split_info with 3 chunks + populated _parts/
# ---------------------------------------------------------------------------
SPLIT_ENTRY_ID = "mov_test_g1_split"


@pytest.fixture()
def split_entry(sandbox):
    """Seed a movies library with a split entry and create a _parts/ folder with
    3 fake chunk files so cmd_push takes the resume path (no real mkvmerge)."""
    media_dir = sandbox["media_dir"]
    short_id = "abc123"
    base = "test_movie.mkv"

    # cmd_push requires the original source file to exist (existence check)
    # before taking the resume path off the populated _parts/ folder.
    (media_dir / base).write_bytes(b"original-master-bytes")

    chunk_names = [f"test_movie [{short_id}].chunk.00{i}.mkv" for i in (1, 2, 3)]
    parts_dir = media_dir / main.SPLIT_DIR_NAME
    parts_dir.mkdir()
    chunks_meta = []
    for i, cn in enumerate(chunk_names, start=1):
        (parts_dir / cn).write_bytes(f"chunk-{i}-bytes".encode())
        chunks_meta.append({"filename": cn, "hash": f"hash{i}"})

    entry = {
        SPLIT_ENTRY_ID: {
            "short_id": short_id,
            "filename": base,
            "folder_path": str(media_dir),
            "status": "local_ready",
            "uploaded": False,
            "hash": "originalhash",
            "metadata": {"title": SPLIT_ENTRY_ID, "year": 2024},
            "tech_spec": {"resolution": "1080p"},
            "split_info": {
                "is_split": True,
                "method": "SIZE_MB",
                "val": "8000",
                "total_chunks": 3,
                "chunks": chunks_meta,
            },
        }
    }
    sandbox["lib_movies"].write_text(json.dumps(entry), encoding="utf-8")
    sandbox["lib_series"].write_text("{}", encoding="utf-8")
    sandbox["lib_anime"].write_text("{}", encoding="utf-8")

    return {
        "entry_id": SPLIT_ENTRY_ID,
        "media_dir": media_dir,
        "parts_dir": parts_dir,
        "chunk_names": chunk_names,
        "lib_movies": sandbox["lib_movies"],
    }


def _load_entry(split_entry):
    data = json.loads(split_entry["lib_movies"].read_text(encoding="utf-8"))
    return data[split_entry["entry_id"]]


# ---------------------------------------------------------------------------
# (1) Happy path
# ---------------------------------------------------------------------------
def test_happy_path_partial_then_mv_then_mvmeta(split_entry, monkeypatch):
    fake = FakeAdb()
    monkeypatch.setattr(main.subprocess, "run", fake.run)

    result = main.cmd_push(split_entry["entry_id"])
    assert result is True

    # Each of the 3 chunks pushed to a *.partial remote.
    chunk_pushes = fake.chunk_pushes()
    assert len(chunk_pushes) == 3
    for cp in chunk_pushes:
        assert cp[-1].endswith(main.PARTIAL_SUFFIX), cp

    # Each chunk push is immediately followed by a `shell mv` of that .partial
    # to the matching final name (ordering: push, mv, push, mv, ...).
    mvs = fake.mvs()
    assert len(mvs) == 3
    for cp, mv in zip(chunk_pushes, mvs):
        partial_remote = cp[-1]
        # mv argv: [..., "shell", "mv", "'<partial>'", "'<final>'"]
        mv_src = mv[-2].strip("'")
        mv_dst = mv[-1].strip("'")
        assert mv_src == partial_remote
        assert mv_dst + main.PARTIAL_SUFFIX == partial_remote

    # Exactly one .mvmeta.json push occurred (after the chunks).
    mvmeta = fake.mvmeta_pushes()
    assert len(mvmeta) == 1
    assert mvmeta[0][-1].endswith(f"[abc123]{main.MVMETA_SUFFIX}")

    # Library flipped to onboarded/uploaded.
    entry = _load_entry(split_entry)
    assert entry["uploaded"] is True
    assert entry["status"] == "onboarded"

    # Local _parts chunks were removed (and the empty dir cleaned up).
    assert not split_entry["parts_dir"].exists() or not list(split_entry["parts_dir"].iterdir())


# ---------------------------------------------------------------------------
# (2) Mid-push failure
# ---------------------------------------------------------------------------
def test_midpush_failure_returns_false_and_preserves_resume(split_entry, monkeypatch):
    fake = FakeAdb(fail_push_n=2)  # 2nd chunk push fails
    monkeypatch.setattr(main.subprocess, "run", fake.run)

    result = main.cmd_push(split_entry["entry_id"])
    assert result is False

    # Only 1 mv ran (for the first, successful chunk); the failed chunk got no mv.
    assert len(fake.mvs()) == 1
    # No mvmeta push on failure.
    assert len(fake.mvmeta_pushes()) == 0

    # State NOT flipped.
    entry = _load_entry(split_entry)
    assert entry["uploaded"] is False
    assert entry["status"] == "local_ready"

    # Surviving local chunks remain for resume: chunk 1 deleted (uploaded+mv'd),
    # chunks 2 and 3 still present.
    remaining = sorted(p.name for p in split_entry["parts_dir"].iterdir())
    assert split_entry["chunk_names"][1] in remaining
    assert split_entry["chunk_names"][2] in remaining
    assert split_entry["chunk_names"][0] not in remaining


# ---------------------------------------------------------------------------
# (3) mv failure treated like push failure
# ---------------------------------------------------------------------------
def test_mv_failure_returns_false_breaks_loop(split_entry, monkeypatch):
    fake = FakeAdb(fail_mv_n=2)  # 2nd rename fails
    monkeypatch.setattr(main.subprocess, "run", fake.run)

    result = main.cmd_push(split_entry["entry_id"])
    assert result is False

    # chunk 1: push+mv ok. chunk 2: push ok, mv fails -> break. chunk 3 never pushed.
    assert len(fake.chunk_pushes()) == 2
    assert len(fake.mvs()) == 2  # 2nd mv was attempted and raised
    assert len(fake.mvmeta_pushes()) == 0

    entry = _load_entry(split_entry)
    assert entry["uploaded"] is False
    assert entry["status"] == "local_ready"

    # Chunk 2's local file must remain (its mv failed, so it is NOT "done").
    remaining = sorted(p.name for p in split_entry["parts_dir"].iterdir())
    assert split_entry["chunk_names"][1] in remaining


# ---------------------------------------------------------------------------
# (4) mvmeta-write failure does NOT fail the push
# ---------------------------------------------------------------------------
def test_mvmeta_failure_does_not_fail_push(split_entry, monkeypatch):
    fake = FakeAdb(fail_mvmeta=True)  # only the sidecar push fails
    monkeypatch.setattr(main.subprocess, "run", fake.run)

    result = main.cmd_push(split_entry["entry_id"])
    assert result is True  # push still succeeds

    # All chunks pushed+renamed.
    assert len(fake.chunk_pushes()) == 3
    assert len(fake.mvs()) == 3
    # mvmeta was attempted (and failed internally).
    assert len(fake.mvmeta_pushes()) == 1

    # Library still flipped to onboarded.
    entry = _load_entry(split_entry)
    assert entry["uploaded"] is True
    assert entry["status"] == "onboarded"


# ---------------------------------------------------------------------------
# (5) Failure-contract parity: no exception escapes; same signal as today
# ---------------------------------------------------------------------------
def test_failure_contract_no_exception_escapes(split_entry, monkeypatch):
    fake = FakeAdb(fail_push_n=1)  # first push fails immediately
    monkeypatch.setattr(main.subprocess, "run", fake.run)

    # cmd_push must return False, not raise.
    result = main.cmd_push(split_entry["entry_id"])
    assert result is False
    assert isinstance(result, bool)
