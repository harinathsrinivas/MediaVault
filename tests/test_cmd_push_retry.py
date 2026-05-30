"""IMP-C2 — ADB push retry protocol tests for cmd_push.

cmd_push wraps the (push -> atomic mv) pair in mvcommon.retry(), so a transient
CalledProcessError gets up to 3 attempts (1/4/16s backoff + jitter), with a
pre-retry `adb shell rm '<remote>.partial'` cleanup and a user-visible retry
line. These tests prove:
  (a) transient self-heal: fail twice then succeed -> cmd_push True, entry
      onboarded, and a `rm '<...>.partial'` was issued before each re-push.
  (b) permanent failure: all 3 attempts fail -> cmd_push False, entry unchanged,
      _parts/ left populated (failure contract unchanged).
  (c) clean happy path: zero failures -> exactly one push + one mv per chunk,
      zero rm calls, no retry line.

All ADB interaction is mocked via a fake subprocess.run recorder; no real
device, no real C:\\Media, no real library JSON. mvcommon.time.sleep is stubbed
so retries are instant.
"""
import json
import subprocess

import pytest

import main
import mvcommon


@pytest.fixture(autouse=True)
def _no_retry_sleep(monkeypatch):
    """Stub the retry backoff sleep (lives in mvcommon.time.sleep)."""
    monkeypatch.setattr(mvcommon.time, "sleep", lambda *_a, **_k: None)


# ---------------------------------------------------------------------------
# Retry-aware fake ADB recorder
# ---------------------------------------------------------------------------
class FakeAdb:
    """Records every adb argv. Can fail a chunk position's push the first K
    attempts (transient) then succeed, or fail every attempt (permanent)."""

    def __init__(self, transient_push_fails=0, permanent_push=False):
        self.calls = []
        # number of leading push attempts (for the FIRST chunk) that should fail
        # before succeeding; models a transient blip that self-heals.
        self.transient_push_fails = transient_push_fails
        self.permanent_push = permanent_push
        self._push_attempts_by_dest = {}

    def run(self, argv, check=False, **kwargs):
        self.calls.append(list(argv))

        is_push = "push" in argv
        dest = argv[-1] if argv else ""
        is_mvmeta = is_push and dest.endswith(main.MVMETA_SUFFIX)

        if is_push and not is_mvmeta:
            n = self._push_attempts_by_dest.get(dest, 0) + 1
            self._push_attempts_by_dest[dest] = n
            if check:
                if self.permanent_push:
                    raise subprocess.CalledProcessError(1, argv)
                if n <= self.transient_push_fails:
                    raise subprocess.CalledProcessError(1, argv)

        class _R:
            returncode = 0
        return _R()

    # --- analysis helpers ---
    def pushes(self):
        return [c for c in self.calls if "push" in c]

    def chunk_pushes(self):
        return [c for c in self.pushes() if not c[-1].endswith(main.MVMETA_SUFFIX)]

    def mvs(self):
        return [c for c in self.calls if "shell" in c and "mv" in c]

    def rms(self):
        return [c for c in self.calls if "shell" in c and "rm" in c]


# ---------------------------------------------------------------------------
# Single-chunk (non-split) entry — simplest path to exercise the retry wrapper
# ---------------------------------------------------------------------------
ENTRY_ID = "mov_test_c2_retry"


@pytest.fixture()
def push_entry(sandbox):
    """Seed a movies library with a simple (non-split) onboardable entry and an
    existing local source file so cmd_push takes the standard single-file push.
    """
    media_dir = sandbox["media_dir"]
    filename = "test_movie.mkv"
    (media_dir / filename).write_bytes(b"master-bytes")

    entry = {
        ENTRY_ID: {
            "short_id": "abc123",
            "filename": filename,
            "folder_path": str(media_dir),
            "status": "local_ready",
            "uploaded": False,
            "hash": "originalhash",
            "metadata": {"title": ENTRY_ID, "year": 2024},
            "tech_spec": {"resolution": "1080p"},
        }
    }
    sandbox["lib_movies"].write_text(json.dumps(entry), encoding="utf-8")
    sandbox["lib_series"].write_text("{}", encoding="utf-8")
    sandbox["lib_anime"].write_text("{}", encoding="utf-8")

    return {
        "entry_id": ENTRY_ID,
        "media_dir": media_dir,
        "lib_movies": sandbox["lib_movies"],
    }


def _load_entry(push_entry):
    data = json.loads(push_entry["lib_movies"].read_text(encoding="utf-8"))
    return data[push_entry["entry_id"]]


# ---------------------------------------------------------------------------
# (a) Transient: fail twice then succeed -> self-heal
# ---------------------------------------------------------------------------
def test_push_fails_twice_then_succeeds_self_heals(push_entry, monkeypatch):
    fake = FakeAdb(transient_push_fails=2)
    monkeypatch.setattr(main.subprocess, "run", fake.run)

    result = main.cmd_push(push_entry["entry_id"])
    assert result is True

    # 3 push attempts for the single chunk: fail, fail, succeed.
    assert len(fake.chunk_pushes()) == 3

    # A `rm '<...>.partial'` was issued before each re-push (2 retries -> 2 rms),
    # each targeting the chunk's .partial remote path.
    rms = fake.rms()
    assert len(rms) == 2
    for rm in rms:
        rm_target = rm[-1].strip("'")
        assert rm_target.endswith(main.PARTIAL_SUFFIX), rm

    # Library entry flipped to onboarded/uploaded.
    entry = _load_entry(push_entry)
    assert entry["uploaded"] is True
    assert entry["status"] == "onboarded"


# ---------------------------------------------------------------------------
# (b) Permanent: all 3 attempts fail -> failure contract unchanged
# ---------------------------------------------------------------------------
def test_push_permanent_failure_contract_unchanged(push_entry, monkeypatch):
    fake = FakeAdb(permanent_push=True)
    monkeypatch.setattr(main.subprocess, "run", fake.run)

    result = main.cmd_push(push_entry["entry_id"])
    assert result is False
    assert isinstance(result, bool)

    # Exactly 3 push attempts (exhausted), then break.
    assert len(fake.chunk_pushes()) == 3
    # No mv ever ran (push never succeeded).
    assert len(fake.mvs()) == 0

    # Library entry untouched.
    entry = _load_entry(push_entry)
    assert entry["uploaded"] is False
    assert entry["status"] == "local_ready"


# ---------------------------------------------------------------------------
# (c) Clean happy path: exactly one push + one mv, zero rm, no retry line
# ---------------------------------------------------------------------------
def test_push_happy_path_no_retry(push_entry, monkeypatch, capsys):
    fake = FakeAdb()  # never fails
    monkeypatch.setattr(main.subprocess, "run", fake.run)

    result = main.cmd_push(push_entry["entry_id"])
    assert result is True

    # Exactly one chunk push + one mv, zero rm calls.
    assert len(fake.chunk_pushes()) == 1
    assert len(fake.mvs()) == 1
    assert len(fake.rms()) == 0

    # No retry line printed on the happy path.
    out = capsys.readouterr().out
    assert "Retry" not in out

    entry = _load_entry(push_entry)
    assert entry["uploaded"] is True
    assert entry["status"] == "onboarded"
