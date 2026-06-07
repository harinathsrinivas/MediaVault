"""Tests for IMP-C11 hash-mismatch quarantine in cmd_restore.

All tests operate on COPIES in a tmp_path sandbox and monkeypatch the three
LIBRARY_* constants (via the C9 `sandbox` fixture in conftest.py). They NEVER
touch the real C:\\Media files or the real library_*.json.

Covers:
  - standard (single-file) path: mismatch -> quarantine, greppable diagnostic,
    returns False, success path unchanged, same-second re-quarantine collision
    counter, fetch self-heal contract.
  - split path: offending-chunk-only quarantine, stale partial-merge output
    deleted, all-clean path proceeds to merge.
"""

import hashlib
import json
import os

import pytest

import main


# ---------------------------------------------------------------------------
# Local helpers — build a sandbox restore scenario on top of the C9 `sandbox`
# fixture (which already monkeypatches LIBRARY_MOVIES/SERIES/ANIME to tmp JSONs
# and creates sandbox["media_dir"]).
# ---------------------------------------------------------------------------

ENTRY_ID = "mov_test_c11_001"  # "mov" prefix -> LIBRARY_MOVIES


def _seed_library(sandbox, entry):
    """Write a single-entry library to the sandbox movies JSON; empty others."""
    sandbox["lib_movies"].write_text(json.dumps({ENTRY_ID: entry}), encoding="utf-8")
    sandbox["lib_series"].write_text("{}", encoding="utf-8")
    sandbox["lib_anime"].write_text("{}", encoding="utf-8")


def _make_single_file_scenario(sandbox, restore_bytes, stored_hash):
    """Create a standard (non-split) entry with a file in restore/ whose bytes
    are `restore_bytes` and a library hash of `stored_hash`. A dummy sits at the
    target path. Returns (filename, restore_folder, target_path)."""
    media_dir = str(sandbox["media_dir"])
    filename = "film.mkv"
    restore_folder = os.path.join(media_dir, main.RESTORE_DIR_NAME)
    os.makedirs(restore_folder, exist_ok=True)

    with open(os.path.join(restore_folder, filename), "wb") as f:
        f.write(restore_bytes)
    # Dummy placeholder at the target path (what restore would overwrite).
    with open(os.path.join(media_dir, filename), "wb") as f:
        f.write(b"DUMMY")

    entry = {
        "status": "archived",
        "uploaded": True,
        "folder_path": media_dir,
        "filename": filename,
        "hash": stored_hash,
    }
    _seed_library(sandbox, entry)
    return filename, restore_folder, os.path.join(media_dir, filename)


def _make_split_scenario(sandbox, chunks):
    """Create a split entry. `chunks` is a list of dicts:
        {"filename": str, "disk_bytes": bytes, "stored_hash": str}
    Each chunk file is written into restore/ with `disk_bytes`; the library
    stores `stored_hash`. Returns (filename, restore_folder, target_path)."""
    media_dir = str(sandbox["media_dir"])
    filename = "film.mkv"
    restore_folder = os.path.join(media_dir, main.RESTORE_DIR_NAME)
    os.makedirs(restore_folder, exist_ok=True)

    chunks_meta = []
    for c in chunks:
        with open(os.path.join(restore_folder, c["filename"]), "wb") as f:
            f.write(c["disk_bytes"])
        chunks_meta.append({"filename": c["filename"], "hash": c["stored_hash"]})

    entry = {
        "status": "archived",
        "uploaded": True,
        "folder_path": media_dir,
        "filename": filename,
        "hash": "merged-hash-placeholder",
        "split_info": {
            "is_split": True,
            "total_chunks": len(chunks),
            "chunks": chunks_meta,
        },
    }
    _seed_library(sandbox, entry)
    return filename, restore_folder, os.path.join(media_dir, filename)


def _sha256_bytes(b):
    return hashlib.sha256(b).hexdigest()


# ---------------------------------------------------------------------------
# Standard-path tests (6)
# ---------------------------------------------------------------------------

def test_mismatch_moves_file_to_quarantine(sandbox):
    filename, restore_folder, _ = _make_single_file_scenario(
        sandbox, restore_bytes=b"CORRUPT-BYTES", stored_hash="0" * 64
    )

    main.cmd_restore(ENTRY_ID)

    # Original gone from restore/
    assert not os.path.exists(os.path.join(restore_folder, filename))
    # Exactly one file under restore/quarantine/, name starts with "<filename>."
    quarantine_dir = os.path.join(restore_folder, "quarantine")
    assert os.path.isdir(quarantine_dir)
    q_files = os.listdir(quarantine_dir)
    assert len(q_files) == 1
    assert q_files[0].startswith(filename + ".")


def test_mismatch_prints_greppable_diagnostic(sandbox, capsys):
    _make_single_file_scenario(
        sandbox, restore_bytes=b"CORRUPT-BYTES", stored_hash="0" * 64
    )

    main.cmd_restore(ENTRY_ID)

    out = capsys.readouterr().out
    assert "Hash mismatch. Bad file quarantined at" in out


def test_mismatch_returns_false(sandbox):
    _make_single_file_scenario(
        sandbox, restore_bytes=b"CORRUPT-BYTES", stored_hash="0" * 64
    )

    result = main.cmd_restore(ENTRY_ID)

    assert result is False


def test_success_path_unchanged(sandbox):
    good = b"GOOD-MASTER-BYTES"
    filename, restore_folder, target_path = _make_single_file_scenario(
        sandbox, restore_bytes=good, stored_hash=_sha256_bytes(good)
    )

    result = main.cmd_restore(ENTRY_ID)

    assert result is True
    # File moved into folder_path (overwriting the dummy)
    assert os.path.exists(target_path)
    with open(target_path, "rb") as f:
        assert f.read() == good
    # NO quarantine folder created
    assert not os.path.isdir(os.path.join(restore_folder, "quarantine"))
    # restore/ cleaned up (it was empty after the move)
    assert not os.path.isdir(restore_folder)
    # status updated
    library = json.loads(sandbox["lib_movies"].read_text(encoding="utf-8"))
    assert library[ENTRY_ID]["status"] == "restored_local"


def test_requarantine_no_collision(sandbox):
    filename, restore_folder, _ = _make_single_file_scenario(
        sandbox, restore_bytes=b"CORRUPT-A", stored_hash="0" * 64
    )

    # First mismatch restore -> quarantines the file
    main.cmd_restore(ENTRY_ID)

    # Re-create a (still-corrupt) file in restore/ and quarantine again
    with open(os.path.join(restore_folder, filename), "wb") as f:
        f.write(b"CORRUPT-B")
    main.cmd_restore(ENTRY_ID)

    quarantine_dir = os.path.join(restore_folder, "quarantine")
    q_files = os.listdir(quarantine_dir)
    # Two distinct files exist; neither was overwritten (collision counter works)
    assert len(q_files) == 2
    assert len(set(q_files)) == 2


def test_self_heal_contract(sandbox):
    filename, restore_folder, _ = _make_single_file_scenario(
        sandbox, restore_bytes=b"CORRUPT-BYTES", stored_hash="0" * 64
    )

    main.cmd_restore(ENTRY_ID)

    # The exact path mainfetch's os.path.exists skip-check looks for must be gone,
    # so a re-fetch will re-download.
    assert not os.path.exists(os.path.join(restore_folder, filename))


# ---------------------------------------------------------------------------
# Split-path tests (3)
# ---------------------------------------------------------------------------

def test_split_mismatch_quarantines_offending_chunk_only(sandbox, capsys):
    c1 = "film.chunk.001.mkv"
    c2 = "film.chunk.002.mkv"
    clean_bytes = b"CHUNK-ONE-GOOD"
    # chunk 2's stored hash is for some other content, so its disk bytes mismatch
    filename, restore_folder, _ = _make_split_scenario(
        sandbox,
        chunks=[
            {"filename": c1, "disk_bytes": clean_bytes, "stored_hash": _sha256_bytes(clean_bytes)},
            {"filename": c2, "disk_bytes": b"CORRUPT", "stored_hash": _sha256_bytes(b"CHUNK-TWO-GOOD")},
        ],
    )

    result = main.cmd_restore(ENTRY_ID)

    assert result is False
    # Corrupt chunk gone from restore/, present under quarantine/
    assert not os.path.exists(os.path.join(restore_folder, c2))
    quarantine_dir = os.path.join(restore_folder, "quarantine")
    q_files = os.listdir(quarantine_dir)
    assert len(q_files) == 1
    assert q_files[0].startswith(c2 + ".")
    # Clean chunk still in restore/
    assert os.path.exists(os.path.join(restore_folder, c1))
    # Diagnostic emitted
    assert "Hash mismatch. Bad file quarantined at" in capsys.readouterr().out


def test_split_mismatch_deletes_partial_merge_output(sandbox):
    c1 = "film.chunk.001.mkv"
    c2 = "film.chunk.002.mkv"
    clean_bytes = b"CHUNK-ONE-GOOD"
    filename, restore_folder, target_path = _make_split_scenario(
        sandbox,
        chunks=[
            {"filename": c1, "disk_bytes": clean_bytes, "stored_hash": _sha256_bytes(clean_bytes)},
            {"filename": c2, "disk_bytes": b"CORRUPT", "stored_hash": _sha256_bytes(b"CHUNK-TWO-GOOD")},
        ],
    )
    # Pre-place a stale partial merged output at the target path
    with open(target_path, "wb") as f:
        f.write(b"STALE-PARTIAL-MERGE")

    main.cmd_restore(ENTRY_ID)

    # Partial output deleted (not left, not quarantined)
    assert not os.path.exists(target_path)


def test_split_success_path_unchanged(sandbox, monkeypatch):
    """All chunks clean -> pre-merge verification passes and control reaches the
    merge. We stub merge_video_files for THIS test only to avoid depending on a
    real mkvmerge invocation; the corrupt-chunk tests above never reach merge."""
    c1 = "film.chunk.001.mkv"
    c2 = "film.chunk.002.mkv"
    b1 = b"CHUNK-ONE-GOOD"
    b2 = b"CHUNK-TWO-GOOD"
    filename, restore_folder, target_path = _make_split_scenario(
        sandbox,
        chunks=[
            {"filename": c1, "disk_bytes": b1, "stored_hash": _sha256_bytes(b1)},
            {"filename": c2, "disk_bytes": b2, "stored_hash": _sha256_bytes(b2)},
        ],
    )

    merge_calls = {"n": 0}

    def _fake_merge(chunk_paths, output_path, seed=None):
        merge_calls["n"] += 1
        # Produce a merged file so the real success path can finish.
        with open(output_path, "wb") as f:
            f.write(b1 + b2)
        return True

    monkeypatch.setattr(main, "merge_video_files", _fake_merge)

    result = main.cmd_restore(ENTRY_ID)

    # Pre-merge verification passed and the merge ran exactly once.
    assert merge_calls["n"] == 1
    assert result is True
    # Merged output exists at target; NO quarantine folder; chunks cleaned up.
    assert os.path.exists(target_path)
    assert not os.path.isdir(os.path.join(restore_folder, "quarantine"))
    assert not os.path.exists(os.path.join(restore_folder, c1))
    assert not os.path.exists(os.path.join(restore_folder, c2))
    # status updated
    library = json.loads(sandbox["lib_movies"].read_text(encoding="utf-8"))
    assert library[ENTRY_ID]["status"] == "restored_local"
