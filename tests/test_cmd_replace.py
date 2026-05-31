import json
import os
import pytest
import main
from conftest import FAKE_ORIGINAL_BYTES, FAKE_DUMMY_BYTES, TEST_ENTRY_ID


# ---------------------------------------------------------------------------
# test 1: happy path — dummy goes live, original is gone, library updated
# ---------------------------------------------------------------------------

def test_happy_path(sandbox_entry, fake_dummy):
    entry_id = sandbox_entry["entry_id"]
    orig_path = sandbox_entry["orig_path"]
    media_dir = sandbox_entry["media_dir"]

    result = main.cmd_replace(entry_id)

    assert result is True
    assert orig_path.read_bytes() == FAKE_DUMMY_BYTES

    # No stale markers left
    tobedeleted = str(orig_path) + ".tobedeleted"
    assert not os.path.exists(tobedeleted), ".tobedeleted should be gone after clean run"

    # No orphaned .dummy_tmp files
    for item in media_dir.iterdir():
        assert ".dummy_tmp" not in item.name, f"Unexpected dummy_tmp leftover: {item.name}"

    # Library reflects archived status
    lib_path = str(main.LIBRARY_MOVIES)
    with open(lib_path, "r", encoding="utf-8") as f:
        library = json.load(f)
    assert library[entry_id]["status"] == "archived"


# ---------------------------------------------------------------------------
# test 2: crash between renames — original bytes are never lost
# ---------------------------------------------------------------------------

def test_crash_between_renames(sandbox_entry, fake_dummy, monkeypatch):
    entry_id = sandbox_entry["entry_id"]
    orig_path = sandbox_entry["orig_path"]

    real_rename = os.rename
    call_count = {"n": 0}

    def patched_rename(src, dst):
        call_count["n"] += 1
        if call_count["n"] == 1:
            # First call (original -> .tobedeleted): let it through
            real_rename(src, dst)
        else:
            # Second call (dummy_tmp -> original): simulate crash
            raise OSError("simulated crash")

    monkeypatch.setattr(main.os, "rename", patched_rename)

    # cmd_replace will raise (or return False) — we don't care which.
    # Auto-rollback (this candidate) converts a post-PONR failure into a
    # structured hard-fail exception; the data-safety assertion below is what
    # this test actually guards, so accept any exception type here.
    try:
        main.cmd_replace(entry_id)
    except Exception:
        pass

    tobedeleted = str(orig_path) + ".tobedeleted"

    # The original bytes must be recoverable from one of the two paths
    original_at_orig = os.path.exists(str(orig_path))
    original_at_tbd = os.path.exists(tobedeleted)

    assert original_at_orig or original_at_tbd, (
        "Original file is not at orig_path OR .tobedeleted — data was lost!"
    )

    if original_at_orig:
        content = orig_path.read_bytes()
    else:
        with open(tobedeleted, "rb") as f:
            content = f.read()

    assert content == FAKE_ORIGINAL_BYTES, (
        f"Recoverable bytes do not match original. Got: {content!r}"
    )


# ---------------------------------------------------------------------------
# test 3A: stale .tobedeleted present alongside real original — swept cleanly
# ---------------------------------------------------------------------------

def test_stale_tobedeleted_swept_redundant_leftover(sandbox_entry, fake_dummy):
    entry_id = sandbox_entry["entry_id"]
    orig_path = sandbox_entry["orig_path"]

    tobedeleted = str(orig_path) + ".tobedeleted"
    # Pre-create a stale .tobedeleted (redundant leftover from a prior interrupted run)
    with open(tobedeleted, "wb") as f:
        f.write(b"LEFTOVER")

    # Original is still at orig_path (the real file is intact)
    assert orig_path.read_bytes() == FAKE_ORIGINAL_BYTES

    result = main.cmd_replace(entry_id)

    assert result is True
    assert not os.path.exists(tobedeleted), ".tobedeleted should be swept"
    assert orig_path.read_bytes() == FAKE_DUMMY_BYTES


# ---------------------------------------------------------------------------
# test 3B: only .tobedeleted exists (original missing) — crash recovery restore
# ---------------------------------------------------------------------------

def test_stale_tobedeleted_swept_crash_recovery(sandbox_entry, fake_dummy):
    entry_id = sandbox_entry["entry_id"]
    orig_path = sandbox_entry["orig_path"]

    tobedeleted = str(orig_path) + ".tobedeleted"
    # Simulate mid-crash state: original has been renamed to .tobedeleted,
    # but nothing is at orig_path yet
    os.rename(str(orig_path), tobedeleted)
    assert not os.path.exists(str(orig_path))
    assert os.path.exists(tobedeleted)

    result = main.cmd_replace(entry_id)

    # Should detect stale state, restore original, and abort
    assert result is False
    assert orig_path.read_bytes() == FAKE_ORIGINAL_BYTES, (
        "Original should be restored from .tobedeleted"
    )
    assert not os.path.exists(tobedeleted), ".tobedeleted should be gone after restore"


# ---------------------------------------------------------------------------
# test 4: PermissionError on first two rename attempts — retries and succeeds
# ---------------------------------------------------------------------------

def test_locked_file_retry(sandbox_entry, fake_dummy, monkeypatch):
    entry_id = sandbox_entry["entry_id"]
    orig_path = sandbox_entry["orig_path"]

    real_rename = os.rename
    tobedeleted_suffix = ".tobedeleted"
    fail_count = {"n": 0}

    def patched_rename(src, dst):
        if str(dst).endswith(tobedeleted_suffix):
            fail_count["n"] += 1
            if fail_count["n"] <= 2:
                raise PermissionError("file is locked")
        real_rename(src, dst)

    monkeypatch.setattr(main.os, "rename", patched_rename)
    monkeypatch.setattr(main.time, "sleep", lambda *a: None)

    result = main.cmd_replace(entry_id)

    assert result is True
    assert orig_path.read_bytes() == FAKE_DUMMY_BYTES
    assert fail_count["n"] == 3, (
        f"Expected 3 attempts at the commit rename, got {fail_count['n']}"
    )


# ---------------------------------------------------------------------------
# test 5: dummy creation failure — aborts before touching anything
# ---------------------------------------------------------------------------

def test_dummy_creation_failure_aborts(sandbox_entry, monkeypatch):
    entry_id = sandbox_entry["entry_id"]
    orig_path = sandbox_entry["orig_path"]
    media_dir = sandbox_entry["media_dir"]

    monkeypatch.setattr(main, "make_video_dummy", lambda *a: False)

    result = main.cmd_replace(entry_id)

    assert result is False
    assert orig_path.read_bytes() == FAKE_ORIGINAL_BYTES, (
        "Original file must be untouched when dummy creation fails"
    )

    tobedeleted = str(orig_path) + ".tobedeleted"
    assert not os.path.exists(tobedeleted), ".tobedeleted must not exist when aborted before rename"
