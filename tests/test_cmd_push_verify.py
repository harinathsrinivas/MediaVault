"""IMP-C8 — post-push remote sha256sum verification tests (Candidate A).

cmd_push, when PUSH_VERIFY_REMOTE=True, runs `adb shell sha256sum '<remote>'`
after each push+atomic-mv and compares the device hash to the stored chunk
hash. A mismatch raises CalledProcessError, which feeds C2's retry() wrapper
(re-push -> re-verify). A sha256sum that is unavailable (the command itself
exits non-zero) is warn-and-skipped, NOT retried.

Candidate A — inline FakeAdbVerify recorder. No real bytes are copied; the
"device" sha256sum response is whatever the recorder is told to emit, and the
stored chunk hash is seeded to match (or mismatch). No conftest sha256sum
branch is used. Library isolation comes from the shared `sandbox` fixture only.
"""
import json
import subprocess

import pytest

import main
import mvcommon


# Stored/expected hash for the seeded chunk. The recorder emits this verbatim
# when configured "correct"; tests flip to a different string to force a mismatch.
GOOD_HASH = "a" * 64
BAD_HASH = "b" * 64

ENTRY_ID = "mov_test_c8_verify"
CHUNK_NAME = "test_movie [abc123].chunk.001.mkv"


@pytest.fixture(autouse=True)
def _no_retry_sleep(monkeypatch):
    """Make C2 retry backoff instant (sleep lives in mvcommon.time.sleep)."""
    monkeypatch.setattr(mvcommon.time, "sleep", lambda *_a, **_k: None)


class FakeAdbVerify:
    """Records every adb argv and answers `adb shell sha256sum` with a
    configurable result, mirroring the FakeAdb recorder in
    test_cmd_push_retry.py.

    sha256_mode:
      "correct"            -> always emit GOOD_HASH (verification passes)
      "wrong"              -> always emit BAD_HASH (mismatch every attempt)
      "wrong_then_correct" -> emit BAD_HASH on the 1st sha256sum, GOOD_HASH after
      "unavailable"        -> raise CalledProcessError on sha256sum (command missing)

    push/mv/rm/mkdir all succeed (return a 0 result); no real files are touched.
    """

    def __init__(self, sha256_mode="correct"):
        self.calls = []
        self.sha256_mode = sha256_mode
        self._sha_calls = 0

    def run(self, argv, check=False, capture_output=False, text=False, **kwargs):
        self.calls.append(list(argv))

        # adb shell sha256sum '<path>'
        if "shell" in argv and "sha256sum" in argv:
            self._sha_calls += 1
            if self.sha256_mode == "unavailable":
                if check:
                    raise subprocess.CalledProcessError(127, argv)
            path = argv[-1].strip("'")
            if self.sha256_mode == "correct":
                h = GOOD_HASH
            elif self.sha256_mode == "wrong":
                h = BAD_HASH
            elif self.sha256_mode == "wrong_then_correct":
                h = BAD_HASH if self._sha_calls == 1 else GOOD_HASH
            else:
                h = GOOD_HASH

            class _R:
                returncode = 0
                stdout = f"{h}  {path}\n"
            return _R()

        class _R:
            returncode = 0
            stdout = ""
        return _R()

    # --- analysis helpers ---
    def sha256_calls(self):
        return [c for c in self.calls if "shell" in c and "sha256sum" in c]

    def rms(self):
        return [c for c in self.calls if "shell" in c and "rm" in c]


@pytest.fixture()
def split_resume_entry(sandbox):
    """Seed a split entry already in the resume state: split_info with one chunk
    (hash seeded to GOOD_HASH) and a populated _parts/ folder so cmd_push takes
    the resume branch and _chunk_hashes is sourced from split_info.chunks.
    """
    media_dir = sandbox["media_dir"]
    # cmd_push requires the original source file to exist (existence check).
    (media_dir / "test_movie.mkv").write_bytes(b"master-bytes")

    parts_dir = media_dir / "_parts"
    parts_dir.mkdir()
    (parts_dir / CHUNK_NAME).write_bytes(b"chunk-bytes")

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
                "chunks": [{"filename": CHUNK_NAME, "hash": GOOD_HASH}],
            },
        }
    }
    sandbox["lib_movies"].write_text(json.dumps(entry), encoding="utf-8")
    sandbox["lib_series"].write_text("{}", encoding="utf-8")
    sandbox["lib_anime"].write_text("{}", encoding="utf-8")

    return {
        "entry_id": ENTRY_ID,
        "media_dir": media_dir,
        "parts_dir": parts_dir,
        "lib_movies": sandbox["lib_movies"],
    }


def _load_entry(fix):
    data = json.loads(fix["lib_movies"].read_text(encoding="utf-8"))
    return data[fix["entry_id"]]


# (a) PUSH_VERIFY_REMOTE=False -> zero sha256sum calls (regression guard)
def test_verify_off_no_sha256sum_calls(split_resume_entry, monkeypatch):
    monkeypatch.setattr(main, "PUSH_VERIFY_REMOTE", False)
    fake = FakeAdbVerify(sha256_mode="correct")
    monkeypatch.setattr(main.subprocess, "run", fake.run)

    result = main.cmd_push(split_resume_entry["entry_id"])
    assert result is True
    assert fake.sha256_calls() == [], "no sha256sum call may run when verify is off"

    entry = _load_entry(split_resume_entry)
    assert entry["uploaded"] is True
    assert entry["status"] == "onboarded"


# (b) Hash matches -> push succeeds, entry onboarded, exactly one sha256sum/chunk
def test_verify_on_hash_matches(split_resume_entry, monkeypatch):
    monkeypatch.setattr(main, "PUSH_VERIFY_REMOTE", True)
    fake = FakeAdbVerify(sha256_mode="correct")
    monkeypatch.setattr(main.subprocess, "run", fake.run)

    result = main.cmd_push(split_resume_entry["entry_id"])
    assert result is True
    assert len(fake.sha256_calls()) == 1

    entry = _load_entry(split_resume_entry)
    assert entry["uploaded"] is True
    assert entry["status"] == "onboarded"


# (c) Mismatch once then matches -> C2 retry self-heals
def test_verify_mismatch_then_match_retries(split_resume_entry, monkeypatch):
    monkeypatch.setattr(main, "PUSH_VERIFY_REMOTE", True)
    fake = FakeAdbVerify(sha256_mode="wrong_then_correct")
    monkeypatch.setattr(main.subprocess, "run", fake.run)

    result = main.cmd_push(split_resume_entry["entry_id"])
    assert result is True

    # sha256sum ran twice (mismatch -> retry -> match).
    assert len(fake.sha256_calls()) == 2
    # The on_retry callback issued exactly one `rm '<...>.partial'`.
    rms = fake.rms()
    assert len(rms) == 1
    assert rms[0][-1].strip("'").endswith(main.PARTIAL_SUFFIX)

    entry = _load_entry(split_resume_entry)
    assert entry["uploaded"] is True
    assert entry["status"] == "onboarded"


# (d) Mismatch on all 3 attempts -> failure contract unchanged
def test_verify_mismatch_all_retries_fails(split_resume_entry, monkeypatch):
    monkeypatch.setattr(main, "PUSH_VERIFY_REMOTE", True)
    fake = FakeAdbVerify(sha256_mode="wrong")
    monkeypatch.setattr(main.subprocess, "run", fake.run)

    result = main.cmd_push(split_resume_entry["entry_id"])
    assert result is False
    assert isinstance(result, bool)

    # Exactly 3 sha256sum calls (one per retry attempt).
    assert len(fake.sha256_calls()) == 3

    entry = _load_entry(split_resume_entry)
    assert entry["uploaded"] is False
    assert entry["status"] == "local_ready"
    # _parts/ left populated for resume.
    assert split_resume_entry["parts_dir"].exists()
    assert any(split_resume_entry["parts_dir"].iterdir())


# (e) sha256sum unavailable -> warn and skip, push succeeds, no retry
def test_verify_unavailable_warns_and_skips(split_resume_entry, monkeypatch, capsys):
    monkeypatch.setattr(main, "PUSH_VERIFY_REMOTE", True)
    fake = FakeAdbVerify(sha256_mode="unavailable")
    monkeypatch.setattr(main.subprocess, "run", fake.run)

    result = main.cmd_push(split_resume_entry["entry_id"])
    assert result is True

    # The sha256sum command was attempted exactly once (no retry on unavailable).
    assert len(fake.sha256_calls()) == 1
    out = capsys.readouterr().out
    assert "sha256sum unavailable" in out
    assert "Retry" not in out

    entry = _load_entry(split_resume_entry)
    assert entry["uploaded"] is True
    assert entry["status"] == "onboarded"
