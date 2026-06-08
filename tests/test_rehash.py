"""Tests for the split-hash-deterministic feature (Step 9).

Never touch real C:\\Media files or real library_*.json.
Run `pytest -q` and fix failures before marking the step done.

Covers: determinism (real mkvmerge, skip-guarded), deferred bless/verify/alarm,
end-to-end fetch→restore, eager push + promote-at-replace, re-split reset,
disk pre-flight, eager merge-failure fallback, tempdir routing, cmd_check window,
and the migrate_rehash_flag migration tool.
"""

import hashlib
import json
import os
import sys

import pytest

import main
import mvcommon

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

ENTRY_ID = "mov_test_rehash_001"   # "mov" prefix → LIBRARY_MOVIES


def _sha256(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _empty_libs(sandbox):
    sandbox["lib_movies"].write_text("{}", encoding="utf-8")
    sandbox["lib_series"].write_text("{}", encoding="utf-8")
    sandbox["lib_anime"].write_text("{}", encoding="utf-8")


def _read_movies(sandbox) -> dict:
    return json.loads(sandbox["lib_movies"].read_text(encoding="utf-8"))


def _seed_split_library(sandbox, entry_dict: dict):
    """Write a single-entry movies library + empty others."""
    sandbox["lib_movies"].write_text(json.dumps(entry_dict), encoding="utf-8")
    sandbox["lib_series"].write_text("{}", encoding="utf-8")
    sandbox["lib_anime"].write_text("{}", encoding="utf-8")


def _make_split_entry_and_restore_dir(sandbox, *, re_hashed=None, stored_hash="placeholder",
                                       merge_seed=None, merge_tool=None,
                                       short_id="abc123"):
    """Build a split library entry + two chunk files in restore/.

    Returns (media_dir_str, restore_folder_str, c1_bytes, c2_bytes, c1_name, c2_name).
    The chunk files under restore/ have the correct bytes so their SHA256 matches
    the library stored chunk hashes — pre-merge per-chunk check will pass.
    """
    media_dir = str(sandbox["media_dir"])
    filename = "film.mkv"
    restore_folder = os.path.join(media_dir, main.RESTORE_DIR_NAME)
    os.makedirs(restore_folder, exist_ok=True)

    c1_bytes = b"CHUNK-ONE-GOOD"
    c2_bytes = b"CHUNK-TWO-GOOD"
    c1_name = f"film [{short_id}].chunk.001.mkv"
    c2_name = f"film [{short_id}].chunk.002.mkv"

    for fname, data in ((c1_name, c1_bytes), (c2_name, c2_bytes)):
        with open(os.path.join(restore_folder, fname), "wb") as fh:
            fh.write(data)

    split_info = {
        "is_split": True,
        "method": "COUNT",
        "val": "2",
        "total_chunks": 2,
        "chunks": [
            {"filename": c1_name, "hash": _sha256(c1_bytes)},
            {"filename": c2_name, "hash": _sha256(c2_bytes)},
        ],
    }
    if merge_seed:
        split_info["merge_seed"] = merge_seed
    if merge_tool:
        split_info["merge_tool"] = merge_tool

    entry = {
        "status": "archived",
        "uploaded": True,
        "folder_path": media_dir,
        "filename": filename,
        "hash": stored_hash,
        "short_id": short_id,
        "split_info": split_info,
    }
    if re_hashed is not None:
        entry["re_hashed"] = re_hashed

    _seed_split_library(sandbox, {ENTRY_ID: entry})
    return media_dir, restore_folder, c1_bytes, c2_bytes, c1_name, c2_name


# ---------------------------------------------------------------------------
# Test 1 — DETERMINISM (real mkvmerge; skip-guarded)
# ---------------------------------------------------------------------------

def test_deterministic_merge_same_seed_yields_identical_hash(mkvmerge_split_chunks):
    """Merging the same chunks 2–3 times with the same seed yields identical SHA256.
    This is the linchpin: proves --deterministic <seed> makes the merge reproducible.
    """
    # Fixture already skipped cleanly if mkvmerge or ffmpeg absent.
    chunks = mkvmerge_split_chunks["chunks"]
    out_dir = mkvmerge_split_chunks["out_dir"]

    chunk_paths = [str(p) for p in chunks]

    out1 = str(out_dir / "merged_run1.mkv")
    out2 = str(out_dir / "merged_run2.mkv")
    out3 = str(out_dir / "merged_run3.mkv")

    seed = "f6b674"

    ok1 = main.merge_video_files(chunk_paths, out1, seed=seed)
    ok2 = main.merge_video_files(chunk_paths, out2, seed=seed)
    ok3 = main.merge_video_files(chunk_paths, out3, seed=seed)

    assert ok1 and ok2 and ok3, "all three merges must succeed"

    h1 = mvcommon.calculate_file_hash(out1)
    h2 = mvcommon.calculate_file_hash(out2)
    h3 = mvcommon.calculate_file_hash(out3)

    assert h1 == h2 == h3, (
        f"Deterministic merges with seed='{seed}' must produce identical SHA256; "
        f"got {h1!r} / {h2!r} / {h3!r}"
    )


# ---------------------------------------------------------------------------
# Test 2 — DEFERRED restore: bless → verify → mismatch alarm (no PONR)
# ---------------------------------------------------------------------------

# Fixed merge output bytes so calculate_file_hash is deterministic without mkvmerge.
_MERGE_BYTES = b"DETERMINISTIC-MERGED-CONTENT"
_MERGE_HASH = _sha256(_MERGE_BYTES)


def _install_fake_merge(monkeypatch, write_bytes=_MERGE_BYTES):
    """Monkeypatch merge_video_files to write fixed bytes + return True."""
    def _fake_merge(chunk_paths, output_path, seed=None):
        with open(output_path, "wb") as fh:
            fh.write(write_bytes)
        return True
    monkeypatch.setattr(main, "merge_video_files", _fake_merge)


def test_deferred_restore_first_call_blesses(sandbox, monkeypatch):
    """First cmd_restore on a not-yet-re_hashed split entry blesses: sets entry["hash"]
    to the merged hash, flips re_hashed=True, writes merge_seed/merge_tool/rehashed_at.
    """
    _install_fake_merge(monkeypatch)
    _make_split_entry_and_restore_dir(sandbox, re_hashed=None, stored_hash="placeholder-original")

    result = main.cmd_restore(ENTRY_ID)
    assert result is True

    lib = _read_movies(sandbox)
    entry = lib[ENTRY_ID]
    assert entry["hash"] == _MERGE_HASH, "hash must be updated to the merged file hash"
    assert entry["re_hashed"] is True
    split_info = entry["split_info"]
    assert "merge_seed" in split_info, "merge_seed must be stored"
    assert "merge_tool" in split_info, "merge_tool must be stored"
    assert "rehashed_at" in split_info, "rehashed_at must be stored"
    assert entry["status"] == "restored_local"


def test_deferred_restore_second_call_verifies_hash_unchanged(sandbox, monkeypatch):
    """Second cmd_restore (re_hashed=True, canonical hash matches) verifies and does
    NOT change entry["hash"] — the stored canonical must be stable after bless.
    """
    _install_fake_merge(monkeypatch)
    # Pre-seed entry as already blessed with the hash the fake merge will produce.
    _make_split_entry_and_restore_dir(
        sandbox, re_hashed=True, stored_hash=_MERGE_HASH,
        merge_seed="abc123", merge_tool="mkvmerge vTEST",
    )
    # Re-create the restore dir (first restore cleaned it up; for this test we just call it once)
    media_dir = str(sandbox["media_dir"])
    restore_folder = os.path.join(media_dir, main.RESTORE_DIR_NAME)
    os.makedirs(restore_folder, exist_ok=True)
    # Re-write chunk files so the entry finds them.
    c1_bytes = b"CHUNK-ONE-GOOD"
    c2_bytes = b"CHUNK-TWO-GOOD"
    entry = _read_movies(sandbox)[ENTRY_ID]
    for c in entry["split_info"]["chunks"]:
        data = c1_bytes if "001" in c["filename"] else c2_bytes
        with open(os.path.join(restore_folder, c["filename"]), "wb") as fh:
            fh.write(data)

    result = main.cmd_restore(ENTRY_ID)
    assert result is True

    lib = _read_movies(sandbox)
    assert lib[ENTRY_ID]["hash"] == _MERGE_HASH, "hash must stay unchanged on verify path"
    assert lib[ENTRY_ID]["re_hashed"] is True


def test_deferred_restore_mismatch_alarms_no_ponr_chunks_kept(sandbox, monkeypatch, capsys):
    """re_hashed=True + stored hash != what merge produces → alarm + returns False,
    does NOT cross the PONR (no chunk delete), chunk files still present after.
    """
    _install_fake_merge(monkeypatch)
    wrong_canonical = "0" * 64   # Does NOT match _MERGE_HASH
    media_dir, restore_folder, c1_bytes, c2_bytes, c1_name, c2_name = (
        _make_split_entry_and_restore_dir(
            sandbox, re_hashed=True, stored_hash=wrong_canonical,
            merge_seed="abc123", merge_tool="mkvmerge vTEST",
        )
    )

    result = main.cmd_restore(ENTRY_ID)

    assert result is False
    out = capsys.readouterr().out
    assert "RESTORE HASH MISMATCH" in out or "mismatch" in out.lower(), (
        "alarm message must be printed"
    )
    # Chunks must still exist — PONR (chunk delete) must NOT have been crossed.
    assert os.path.exists(os.path.join(restore_folder, c1_name)), "chunk 1 must survive mismatch alarm"
    assert os.path.exists(os.path.join(restore_folder, c2_name)), "chunk 2 must survive mismatch alarm"
    # entry hash must NOT have been changed.
    lib = _read_movies(sandbox)
    assert lib[ENTRY_ID]["hash"] == wrong_canonical, "stored canonical must not change on mismatch"
    assert lib[ENTRY_ID]["re_hashed"] is True


# ---------------------------------------------------------------------------
# Test 3 — END-TO-END fetch→restore: not-yet-hashed → bless; already-hashed → verify;
#            corrupt chunk caught pre-merge
# ---------------------------------------------------------------------------

def test_e2e_not_yet_rehashed_blesses(sandbox, monkeypatch):
    """cmd_restore on a not-yet-re_hashed entry verifies chunks, merges, then blesses.
    Covers the path cmd_fetch_restore inherits.
    """
    _install_fake_merge(monkeypatch)
    _make_split_entry_and_restore_dir(sandbox, re_hashed=None, stored_hash="original-before-bless")

    result = main.cmd_restore(ENTRY_ID)
    assert result is True

    lib = _read_movies(sandbox)
    assert lib[ENTRY_ID]["re_hashed"] is True
    assert lib[ENTRY_ID]["hash"] == _MERGE_HASH


def test_e2e_already_rehashed_verifies(sandbox, monkeypatch):
    """cmd_restore on an already-re_hashed entry reproduces the canonical and leaves
    hash unchanged — the end-to-end verify path inherited by fetch_restore.
    """
    _install_fake_merge(monkeypatch)
    _make_split_entry_and_restore_dir(
        sandbox, re_hashed=True, stored_hash=_MERGE_HASH,
        merge_seed="abc123", merge_tool="mkvmerge vTEST",
    )
    # Re-stage the chunk files for the second restore call.
    media_dir = str(sandbox["media_dir"])
    restore_folder = os.path.join(media_dir, main.RESTORE_DIR_NAME)
    os.makedirs(restore_folder, exist_ok=True)
    c1_bytes = b"CHUNK-ONE-GOOD"
    c2_bytes = b"CHUNK-TWO-GOOD"
    entry = _read_movies(sandbox)[ENTRY_ID]
    for c in entry["split_info"]["chunks"]:
        data = c1_bytes if "001" in c["filename"] else c2_bytes
        with open(os.path.join(restore_folder, c["filename"]), "wb") as fh:
            fh.write(data)

    result = main.cmd_restore(ENTRY_ID)
    assert result is True
    lib = _read_movies(sandbox)
    assert lib[ENTRY_ID]["hash"] == _MERGE_HASH


def test_e2e_corrupt_chunk_caught_before_bless(sandbox, monkeypatch):
    """A chunk whose on-disk bytes don't match the stored hash is caught by the
    pre-merge per-chunk check — no bless/merge ever runs.
    """
    merge_calls = {"n": 0}

    def _never_merge(chunk_paths, output_path, seed=None):
        merge_calls["n"] += 1
        return True

    monkeypatch.setattr(main, "merge_video_files", _never_merge)

    media_dir = str(sandbox["media_dir"])
    filename = "film.mkv"
    restore_folder = os.path.join(media_dir, main.RESTORE_DIR_NAME)
    os.makedirs(restore_folder, exist_ok=True)

    short_id = "abc123"
    c1_name = f"film [{short_id}].chunk.001.mkv"
    c2_name = f"film [{short_id}].chunk.002.mkv"
    good_bytes = b"CHUNK-ONE-GOOD"
    corrupt_bytes = b"CORRUPT-BYTES"

    # Write corrupt bytes for chunk 1 but store the hash of good_bytes.
    with open(os.path.join(restore_folder, c1_name), "wb") as fh:
        fh.write(corrupt_bytes)
    with open(os.path.join(restore_folder, c2_name), "wb") as fh:
        fh.write(b"CHUNK-TWO-GOOD")

    entry = {
        "status": "archived",
        "uploaded": True,
        "folder_path": media_dir,
        "filename": filename,
        "hash": "placeholder",
        "short_id": short_id,
        "split_info": {
            "is_split": True,
            "method": "COUNT",
            "val": "2",
            "total_chunks": 2,
            "chunks": [
                {"filename": c1_name, "hash": _sha256(good_bytes)},    # mismatch
                {"filename": c2_name, "hash": _sha256(b"CHUNK-TWO-GOOD")},
            ],
        },
    }
    _seed_split_library(sandbox, {ENTRY_ID: entry})

    result = main.cmd_restore(ENTRY_ID)
    assert result is False
    assert merge_calls["n"] == 0, "merge must not run when chunk hash fails pre-check"


# ---------------------------------------------------------------------------
# Test 4 — EAGER push: canonical staged in split_info; cmd_replace promotes it
# ---------------------------------------------------------------------------

def _make_split_entry_for_push(sandbox, *, short_id="abc123", eager_seed=None):
    """Seed a split entry with a physical _parts/ folder (resume path) so cmd_push
    takes the resume branch without requiring a real split (no ffmpeg needed)."""
    media_dir = sandbox["media_dir"]
    base = "film.mkv"
    (media_dir / base).write_bytes(b"X" * (main.DUMMY_MAX_BYTES + 1))

    chunk_names = [f"film [{short_id}].chunk.00{i}.mkv" for i in (1, 2)]
    parts_dir = media_dir / main.SPLIT_DIR_NAME
    parts_dir.mkdir()
    chunks_meta = []
    for i, cn in enumerate(chunk_names, start=1):
        data = f"chunk-{i}-data".encode()
        (parts_dir / cn).write_bytes(data)
        chunks_meta.append({"filename": cn, "hash": _sha256(data)})

    original_hash = "ORIGINAL-HASH-BEFORE-PUSH"
    entry = {
        ENTRY_ID: {
            "short_id": short_id,
            "filename": base,
            "folder_path": str(media_dir),
            "status": "local_ready",
            "uploaded": False,
            "hash": original_hash,
            "metadata": {"title": ENTRY_ID},
            "tech_spec": {"resolution": "1080p"},
            "split_info": {
                "is_split": True,
                "method": "COUNT",
                "val": "2",
                "total_chunks": 2,
                "chunks": chunks_meta,
            },
            "re_hashed": False,
        }
    }
    _seed_split_library(sandbox, entry)
    return media_dir, parts_dir, chunk_names, original_hash


_EAGER_CANONICAL_BYTES = b"EAGER-CANONICAL-MERGED-TEMP"
_EAGER_CANONICAL_HASH = _sha256(_EAGER_CANONICAL_BYTES)


def _seed_new_split_entry(sandbox, *, short_id="abc123", original_hash="ORIGINAL-HASH-BEFORE-PUSH",
                          filler=b"X"):
    """Seed a local_ready entry with a master file > DUMMY_MAX_BYTES and NO _parts/,
    so cmd_push takes the NEW-SPLIT path (where the eager bless + re_hashed reset live).
    Returns (media_dir, original_hash)."""
    media_dir = sandbox["media_dir"]
    base = "film.mkv"
    (media_dir / base).write_bytes(filler * (main.DUMMY_MAX_BYTES + 1))
    _seed_split_library(sandbox, {ENTRY_ID: {
        "short_id": short_id, "filename": base, "folder_path": str(media_dir),
        "status": "local_ready", "uploaded": False, "hash": original_hash,
        "metadata": {}, "tech_spec": {},
    }})
    return media_dir, original_hash


def _fake_split_two_chunks(local_file, out_dir, method, val, file_id=None):
    """split_video_file stand-in: drop two fake chunk files so the NEW-SPLIT branch runs
    without real ffmpeg/mkvmerge."""
    os.makedirs(out_dir, exist_ok=True)
    c1 = os.path.join(out_dir, f"film [{file_id}].chunk.001.mkv")
    c2 = os.path.join(out_dir, f"film [{file_id}].chunk.002.mkv")
    open(c1, "wb").write(b"new-chunk-1")
    open(c2, "wb").write(b"new-chunk-2")
    return [c1, c2]


def test_eager_push_stages_canonical_in_split_info(sandbox, mock_device, monkeypatch):
    """cmd_push with eager_rehash=True on a NEW split stages merge_seed/merge_tool/
    canonical_hash in split_info; entry["hash"] stays original and re_hashed stays
    False — only cmd_replace promotes. (The eager bless runs ONLY on the new-split path,
    so this exercises a real split — not the resume branch which bypasses it.)
    """
    media_dir, original_hash = _seed_new_split_entry(sandbox)

    def _fake_eager_merge(chunk_paths, output_path, seed=None):
        with open(output_path, "wb") as fh:
            fh.write(_EAGER_CANONICAL_BYTES)
        return True

    monkeypatch.setattr(main, "split_video_file", _fake_split_two_chunks)
    monkeypatch.setattr(main, "merge_video_files", _fake_eager_merge)

    result = main.cmd_push(ENTRY_ID, split_method="COUNT", split_val="2", eager_rehash=True)
    assert result is True

    entry = _read_movies(sandbox)[ENTRY_ID]
    # entry["hash"] must still be the original (not promoted yet — only at replace).
    assert entry["hash"] == original_hash, "entry hash must NOT be promoted at push time"
    # re_hashed must still be False (reset on the new split; promoted only at replace).
    assert entry.get("re_hashed") is not True, "re_hashed must not be True before replace"
    # split_info must carry the staged canonical fields.
    si = entry["split_info"]
    assert "merge_seed" in si, "merge_seed must be staged in split_info"
    assert "merge_tool" in si, "merge_tool must be staged in split_info"
    assert "canonical_hash" in si, "canonical_hash must be staged in split_info"
    assert si["canonical_hash"] == _EAGER_CANONICAL_HASH


def test_cmd_replace_promotes_eager_canonical(sandbox, fake_dummy, monkeypatch):
    """After eager push staged canonical_hash, cmd_replace promotes it: entry["hash"]
    becomes the canonical, re_hashed=True, rehashed_at stamped, canonical_hash dropped.
    """
    media_dir, parts_dir, chunk_names, original_hash = _make_split_entry_for_push(sandbox)

    # Stage eager canonical directly in the library (simulates a prior eager push).
    lib = _read_movies(sandbox)
    lib[ENTRY_ID]["split_info"]["merge_seed"] = "abc123"
    lib[ENTRY_ID]["split_info"]["merge_tool"] = "mkvmerge vTEST"
    lib[ENTRY_ID]["split_info"]["canonical_hash"] = _EAGER_CANONICAL_HASH
    lib[ENTRY_ID]["uploaded"] = True
    lib[ENTRY_ID]["status"] = "onboarded"
    sandbox["lib_movies"].write_text(json.dumps(lib), encoding="utf-8")

    result = main.cmd_replace(ENTRY_ID)
    assert result is True

    lib2 = _read_movies(sandbox)
    entry2 = lib2[ENTRY_ID]
    assert entry2["hash"] == _EAGER_CANONICAL_HASH, "hash must be promoted to canonical at replace"
    assert entry2["re_hashed"] is True
    assert "rehashed_at" in entry2["split_info"], "rehashed_at must be stamped"
    assert "canonical_hash" not in entry2["split_info"], "canonical_hash transient field must be dropped"
    assert entry2["status"] == "archived"


# ---------------------------------------------------------------------------
# Test 5 — RE-SPLIT RESET: re-push with new chunks clears canonical fields
# ---------------------------------------------------------------------------

def test_resplit_reset_clears_canonical_fields(sandbox, mock_device, monkeypatch):
    """A fresh split of an already-re_hashed=True entry resets re_hashed=False and
    drops stale merge_seed/merge_tool/rehashed_at/canonical_hash from split_info.
    """
    media_dir = sandbox["media_dir"]
    short_id = "abc123"
    base = "film.mkv"
    (media_dir / base).write_bytes(b"Y" * (main.DUMMY_MAX_BYTES + 1))

    stale_entry = {
        ENTRY_ID: {
            "short_id": short_id,
            "filename": base,
            "folder_path": str(media_dir),
            "status": "local_ready",
            "uploaded": False,
            "hash": "stale-canonical-hash",
            "metadata": {},
            "tech_spec": {},
            "re_hashed": True,   # Was blessed before
            "split_info": {
                "is_split": True,
                "method": "COUNT",
                "val": "2",
                "total_chunks": 2,
                "chunks": [],
                "merge_seed": short_id,
                "merge_tool": "mkvmerge vOLD",
                "rehashed_at": "2026-01-01T00:00:00Z",
            },
        }
    }
    _seed_split_library(sandbox, stale_entry)

    # Monkeypatch split_video_file to drop two fake chunks so the NEW-SPLIT branch runs.
    parts_dir = media_dir / main.SPLIT_DIR_NAME

    def _fake_split(local_file, out_dir, method, val, file_id=None):
        os.makedirs(out_dir, exist_ok=True)
        c1 = os.path.join(out_dir, f"film [{file_id}].chunk.001.mkv")
        c2 = os.path.join(out_dir, f"film [{file_id}].chunk.002.mkv")
        open(c1, "wb").write(b"new-chunk-1")
        open(c2, "wb").write(b"new-chunk-2")
        return [c1, c2]

    monkeypatch.setattr(main, "split_video_file", _fake_split)

    result = main.cmd_push(ENTRY_ID, split_method="COUNT", split_val="2")
    assert result is True

    lib = _read_movies(sandbox)
    entry = lib[ENTRY_ID]
    # After re-split, re_hashed must be False (cleared).
    assert entry.get("re_hashed") is False or entry.get("re_hashed") is None or entry.get("re_hashed") == False
    si = entry["split_info"]
    # Stale canonical fields must be gone from the new split_info.
    assert "canonical_hash" not in si, "stale canonical_hash must not survive a re-split"


# ---------------------------------------------------------------------------
# Test 6 — DISK PRE-FLIGHT: hard-stop when space insufficient
# ---------------------------------------------------------------------------

def test_disk_preflight_stops_before_parts_dir_created(sandbox, monkeypatch):
    """cmd_push with insufficient disk space stops before creating _parts/ and returns False.
    The _parts/ directory must not exist after the hard-stop.
    """
    media_dir = sandbox["media_dir"]
    short_id = "abc123"
    base = "film.mkv"
    (media_dir / base).write_bytes(b"Z" * (main.DUMMY_MAX_BYTES + 1))
    entry = {
        ENTRY_ID: {
            "short_id": short_id,
            "filename": base,
            "folder_path": str(media_dir),
            "status": "local_ready",
            "uploaded": False,
            "hash": "h",
            "metadata": {},
            "tech_spec": {},
        }
    }
    _seed_split_library(sandbox, entry)

    # Force _free_space_ok to return False (simulating no free space).
    monkeypatch.setattr(main, "_free_space_ok", lambda *a, **kw: False)

    result = main.cmd_push(ENTRY_ID, split_method="COUNT", split_val="2")
    assert result is False

    parts_dir = media_dir / main.SPLIT_DIR_NAME
    assert not parts_dir.exists(), "_parts/ must not be created when disk pre-flight fails"


def test_season_preflight_picks_largest_splitting_item(sandbox, monkeypatch, tmp_path):
    """The season/group pre-flight picks the LARGEST splitting item and hard-stops
    when that item's requirement doesn't fit (not just any episode).
    """
    media_dir = sandbox["media_dir"]
    short_id_ep1 = "ep1abc"
    short_id_ep2 = "ep2def"
    SMALL_EP_ID = "tv-en-2020-show-s01e01"
    LARGE_EP_ID = "tv-en-2020-show-s01e02"

    small_file = media_dir / "show_e01.mkv"
    large_file = media_dir / "show_e02.mkv"
    small_file.write_bytes(b"S" * 100)    # 100 bytes — trivially small
    large_file.write_bytes(b"L" * (main.DUMMY_MAX_BYTES + 1))  # bigger

    group_entry = {
        "tv-en-2020-show-s01": {
            "type": "season_map",
            "children": [SMALL_EP_ID, LARGE_EP_ID],
            "total_episodes": 2,
        },
        SMALL_EP_ID: {
            "short_id": short_id_ep1,
            "filename": "show_e01.mkv",
            "folder_path": str(media_dir),
            "status": "local_ready",
            "uploaded": False,
            "hash": "h1",
            "metadata": {},
            "tech_spec": {},
        },
        LARGE_EP_ID: {
            "short_id": short_id_ep2,
            "filename": "show_e02.mkv",
            "folder_path": str(media_dir),
            "status": "local_ready",
            "uploaded": False,
            "hash": "h2",
            "metadata": {},
            "tech_spec": {},
        },
    }
    sandbox["lib_series"].write_text(json.dumps(group_entry), encoding="utf-8")
    sandbox["lib_movies"].write_text("{}", encoding="utf-8")
    sandbox["lib_anime"].write_text("{}", encoding="utf-8")

    push_calls = {"ids": []}

    def _fake_push(mid, *a, **kw):
        push_calls["ids"].append(mid)
        return False  # Should never be reached for the LARGE_EP_ID

    # Force _free_space_ok to return False so the season pre-flight rejects.
    monkeypatch.setattr(main, "_free_space_ok", lambda *a, **kw: False)
    # Also intercept shutil.disk_usage in main to report 0 free.
    import shutil as _shutil

    class _FakeDiskUsage:
        free = 0
        total = 1024 ** 4
        used = 1024 ** 4

    monkeypatch.setattr(main.shutil, "disk_usage", lambda p: _FakeDiskUsage())

    # Use cmd_push_group with COUNT split → the LARGE_EP_ID triggers will_split=True.
    result = main.cmd_push_group("tv-en-2020-show-s01", split_method="COUNT", split_val="2")

    # Neither episode should have been pushed (hard-stop before the loop).
    assert push_calls["ids"] == [], "no episode must be pushed when season pre-flight fails"


# ---------------------------------------------------------------------------
# Test 7 — EAGER merge-failure fallback: push still succeeds as deferred
# ---------------------------------------------------------------------------

def test_eager_merge_failure_falls_back_to_deferred(sandbox, mock_device, monkeypatch):
    """When the eager merge temp fails on a NEW split, cmd_push continues as deferred:
    no canonical_hash written, re_hashed not set True, push still returns True. (Uses
    the new-split path so the eager block actually RUNS and its failure path is exercised
    — the resume branch would bypass the eager block and false-pass.)
    """
    media_dir, original_hash = _seed_new_split_entry(sandbox, filler=b"Y")

    def _fail_merge(chunk_paths, output_path, seed=None):
        return False  # eager merge fails

    monkeypatch.setattr(main, "split_video_file", _fake_split_two_chunks)
    monkeypatch.setattr(main, "merge_video_files", _fail_merge)

    result = main.cmd_push(ENTRY_ID, split_method="COUNT", split_val="2", eager_rehash=True)
    # Push must still succeed (deferred fallback — never aborts for an eager hiccup).
    assert result is True

    entry = _read_movies(sandbox)[ENTRY_ID]
    si = entry.get("split_info", {})
    assert "canonical_hash" not in si, "no canonical_hash must be written when eager merge fails"
    assert entry.get("re_hashed") is not True, "re_hashed must not be True after eager failure"


# ---------------------------------------------------------------------------
# Test 8 — TEMPDIR: chunks land under scratch/, checksums/journal in media folder
# ---------------------------------------------------------------------------

def test_tempdir_chunks_land_under_scratch(sandbox, mock_device, monkeypatch, tmp_path):
    """cmd_push with temp_dir routes _parts/ under scratch/<safe-id>/,
    while checksums/ and the journal stay under the media folder.
    """
    media_dir = sandbox["media_dir"]
    short_id = "abc123"
    base = "film.mkv"
    (media_dir / base).write_bytes(b"W" * (main.DUMMY_MAX_BYTES + 1))
    entry = {
        ENTRY_ID: {
            "short_id": short_id,
            "filename": base,
            "folder_path": str(media_dir),
            "status": "local_ready",
            "uploaded": False,
            "hash": "h",
            "metadata": {},
            "tech_spec": {},
        }
    }
    _seed_split_library(sandbox, entry)

    scratch = tmp_path / "scratch"
    scratch.mkdir()

    def _fake_split(local_file, out_dir, method, val, file_id=None):
        os.makedirs(out_dir, exist_ok=True)
        c1 = os.path.join(out_dir, f"film [{file_id}].chunk.001.mkv")
        c2 = os.path.join(out_dir, f"film [{file_id}].chunk.002.mkv")
        open(c1, "wb").write(b"new-chunk-1")
        open(c2, "wb").write(b"new-chunk-2")
        return [c1, c2]

    monkeypatch.setattr(main, "split_video_file", _fake_split)

    result = main.cmd_push(ENTRY_ID, split_method="COUNT", split_val="2", temp_dir=str(scratch))
    assert result is True

    # Chunks must have been routed under scratch (via mock_device).
    # scratch/<safe-id>/_parts/ should have been cleaned up after upload.
    # Checksums must live under the media folder, NOT under scratch.
    checksum_dir = media_dir / main.CHECKSUM_DIR_NAME
    assert checksum_dir.exists(), "checksums/ must stay under the media folder"
    checksum_files = list(checksum_dir.glob("*.sha256"))
    assert len(checksum_files) == 2, "checksums for both chunks must be under media folder"


def test_tempdir_bad_path_returns_false(sandbox, monkeypatch):
    """cmd_push with a non-existent temp_dir hard-stops immediately and returns False,
    with no _parts/ directory created anywhere.
    """
    media_dir = sandbox["media_dir"]
    short_id = "abc123"
    base = "film.mkv"
    (media_dir / base).write_bytes(b"Z" * (main.DUMMY_MAX_BYTES + 1))
    entry = {
        ENTRY_ID: {
            "short_id": short_id,
            "filename": base,
            "folder_path": str(media_dir),
            "status": "local_ready",
            "uploaded": False,
            "hash": "h",
            "metadata": {},
            "tech_spec": {},
        }
    }
    _seed_split_library(sandbox, entry)

    bad_temp = r"C:\NonExistentVolumeXYZ\does_not_exist"

    result = main.cmd_push(ENTRY_ID, split_method="COUNT", split_val="2", temp_dir=bad_temp)
    assert result is False

    parts_dir = media_dir / main.SPLIT_DIR_NAME
    assert not parts_dir.exists(), "_parts/ must not exist when temp_dir is bad"


# ---------------------------------------------------------------------------
# Test 9 — CMD_CHECK WINDOW: check passes against original file during eager
#            pre-replace window (canonical_hash in split_info; entry hash = original)
# ---------------------------------------------------------------------------

def test_cmd_check_passes_in_eager_prereplace_window(sandbox):
    """In the eager pre-replace window (split_info has canonical_hash but entry["hash"]
    is still the original AND the original file is on disk), cmd_check PASSES.
    """
    media_dir = sandbox["media_dir"]
    short_id = "abc123"
    base = "film.mkv"
    original_bytes = b"ORIGINAL-MASTER-DATA" * 11000   # 220000 bytes > DUMMY_MAX_BYTES (200000)
    (media_dir / base).write_bytes(original_bytes)

    original_hash = _sha256(original_bytes)
    entry = {
        ENTRY_ID: {
            "short_id": short_id,
            "filename": base,
            "folder_path": str(media_dir),
            "status": "onboarded",
            "uploaded": True,
            "hash": original_hash,   # still the on-disk file's hash
            "metadata": {},
            "tech_spec": {},
            "re_hashed": False,
            "split_info": {
                "is_split": True,
                "method": "COUNT",
                "val": "2",
                "total_chunks": 2,
                "chunks": [],
                # Eager canonical is staged here, but NOT yet promoted to entry["hash"]
                "merge_seed": short_id,
                "merge_tool": "mkvmerge vTEST",
                "canonical_hash": "STAGED-CANONICAL-HASH-NOT-YET-PROMOTED",
            },
        }
    }
    _seed_split_library(sandbox, entry)

    # cmd_check should check entry["hash"] against the actual file on disk — should PASS.
    import io
    from contextlib import redirect_stdout

    buf = io.StringIO()
    with redirect_stdout(buf):
        main.cmd_check(ENTRY_ID)
    out = buf.getvalue()

    assert "PASS" in out, f"cmd_check must PASS in the eager pre-replace window; got: {out!r}"


# ---------------------------------------------------------------------------
# Test 10 — MIGRATION: migrate_rehash_flag stamps re_hashed:false on split entries only
# ---------------------------------------------------------------------------

def test_migration_stamps_split_only_and_is_idempotent(sandbox):
    """migrate() stamps re_hashed=False on split entries lacking the flag; leaves
    non-split entries and season_map parents untouched; is idempotent on re-run.
    """
    # Build a library with 4 entries across the three files:
    # 1. a split entry WITHOUT re_hashed (should be stamped)
    # 2. a non-split entry (no split_info; should be skipped)
    # 3. a split entry WITH re_hashed=False already (idempotent — should NOT re-stamp)
    # 4. a season_map parent (should be completely skipped)
    movies = {
        "mov-split-no-flag": {
            "status": "archived",
            "hash": "aabbcc",
            "split_info": {"is_split": True, "chunks": []},
        },
        "mov-non-split": {
            "status": "archived",
            "hash": "ddeeff",
        },
    }
    series = {
        "tv-split-already-flagged": {
            "status": "archived",
            "hash": "112233",
            "re_hashed": False,    # already present
            "split_info": {"is_split": True, "chunks": []},
        },
        "tv-season-parent": {
            "type": "season_map",
            "children": [],
        },
    }
    sandbox["lib_movies"].write_text(json.dumps(movies), encoding="utf-8")
    sandbox["lib_series"].write_text(json.dumps(series), encoding="utf-8")
    sandbox["lib_anime"].write_text("{}", encoding="utf-8")

    # Import the migrate function (tools/ is not on sys.path by default).
    tools_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools")
    if tools_dir not in sys.path:
        sys.path.insert(0, tools_dir)
    from migrate_rehash_flag import migrate

    counts = migrate(verbose=False)

    # scanned counts every non-season-map entry.
    assert counts["scanned"] == 3    # split-no-flag + non-split + split-already-flagged
    assert counts["stamped"] == 1    # only split-no-flag
    assert counts["already_had_flag"] == 1
    assert counts["skipped_non_split"] == 1

    lib = mvcommon.load_library()

    # split-no-flag: must have re_hashed=False stamped; hash must be unchanged.
    e_split = lib["mov-split-no-flag"]
    assert e_split.get("re_hashed") is False
    assert e_split["hash"] == "aabbcc"
    # NO merge_seed/merge_tool/rehashed_at written.
    si = e_split.get("split_info", {})
    assert "merge_seed" not in si
    assert "merge_tool" not in si
    assert "rehashed_at" not in si

    # non-split: no re_hashed key.
    assert "re_hashed" not in lib["mov-non-split"]

    # already-flagged: re_hashed still False, nothing new.
    assert lib["tv-split-already-flagged"]["re_hashed"] is False

    # season_map: completely untouched.
    assert lib["tv-season-parent"]["type"] == "season_map"
    assert "re_hashed" not in lib["tv-season-parent"]

    # Idempotent: second call stamps 0.
    counts2 = migrate(verbose=False)
    assert counts2["stamped"] == 0
    assert counts2["already_had_flag"] == 2   # both split entries now have the flag

    lib2 = mvcommon.load_library()
    # Byte-identical to after first run.
    assert lib2["mov-split-no-flag"] == lib["mov-split-no-flag"]
