"""Behavior-baseline characterization + happy-path smoke harness (auto-rollback Step 1).

This file is the REGRESSION ORACLE for the auto-rollback feature. It captures the
exact happy-path post-state (library entry fields + on-disk artifacts) of the five
target functions against UNMODIFIED main.py, so every Step 3 candidate can prove
"no happy-path change" (Decision D-4: the happy path stays byte-for-byte identical;
prefer wrapping over rewriting).

Boundaries are mocked per docs/testing-strategy.md §1 only at I/O edges:
  - library JSON  -> `sandbox` fixture (hard-guards real C:\\Media + real library_*.json)
  - ADB / device  -> `mock_device` (stateful data round-trip) for cmd_push/restore
  - ffmpeg dummy  -> `fake_dummy` for cmd_replace
  - MediaInfo     -> `stub_tech_specs` for cmd_prep
Application logic (path math, hashing, library updates) always runs real.

Covered happy paths:
  - cmd_prep   : no-split single movie -> entry + sidecars + (no parent)
  - cmd_prep   : season episode -> parent season_map created + child linked
  - cmd_push   : split entry -> chunks land on device, entry flips onboarded
  - cmd_replace: uploaded entry -> dummy goes live, status archived (fake_dummy)
  - cmd_restore: split path -> chunks merge to target, status restored_local
  - cmd_restore: standard path -> file moved from restore/, status restored_local

These all assert against UNMODIFIED main.py and MUST stay green after any Step 3
candidate wraps the commands with rollback handling.
"""
import hashlib
import json
import os

import pytest

import main
import mvcommon


def _read_movies(sandbox):
    return json.loads(sandbox["lib_movies"].read_text(encoding="utf-8"))


def _read_series(sandbox):
    return json.loads(sandbox["lib_series"].read_text(encoding="utf-8"))


def _sha256_file(path):
    return hashlib.sha256(open(path, "rb").read()).hexdigest()


def _empty_libs(sandbox):
    sandbox["lib_movies"].write_text("{}", encoding="utf-8")
    sandbox["lib_series"].write_text("{}", encoding="utf-8")
    sandbox["lib_anime"].write_text("{}", encoding="utf-8")


# ===========================================================================
# cmd_prep — fully reversible; the oracle for "what a clean prep creates"
# ===========================================================================

def test_prep_no_split_movie_oracle(sandbox, stub_tech_specs):
    """A clean prep of a standalone movie creates: a library entry (status
    local_ready / uploaded False), the `uid` sidecar, the `<short_id>.sha256`
    sidecar, and NO parent. This is the reversible-prep snapshot a rollback must
    be able to fully undo."""
    _empty_libs(sandbox)
    media_dir = sandbox["media_dir"]
    filepath = media_dir / "Inception.mkv"
    # Must exceed DUMMY_MAX_BYTES (200_000) or cmd_prep's dummy-detection
    # safety net (@ main.py 316-318) skips it as a dummy.
    filepath.write_bytes(b"X" * (main.DUMMY_MAX_BYTES + 1))

    manual_id = "mov-en-2010-inception"
    result = main.cmd_prep(manual_id, str(filepath))
    assert result is True

    lib = _read_movies(sandbox)
    assert manual_id in lib
    entry = lib[manual_id]
    short_id = entry["short_id"]

    # Library field oracle
    assert entry["status"] == "local_ready"
    assert entry["uploaded"] is False
    assert entry["filename"] == "Inception.mkv"
    assert entry["folder_path"] == str(media_dir)
    assert entry["hash"] == _sha256_file(filepath)
    assert "parent_id" not in entry
    assert entry["tech_spec"]["resolution"] == "1080p"

    # On-disk artifact oracle: both sidecars present with expected contents
    uid_path = media_dir / "uid"
    sha_path = media_dir / f"{short_id}.sha256"
    assert uid_path.read_text() == short_id
    assert sha_path.exists()
    assert short_id == mvcommon.generate_short_id(manual_id)


def test_prep_season_episode_oracle(sandbox, stub_tech_specs):
    """Prepping a season episode (id ending eNN) creates the parent season_map
    AND links the child. The per-id snapshot must record that THIS run created
    the parent (D-7) so a rollback of the only episode also removes the parent,
    but a rollback of one episode among many only unlinks the child."""
    _empty_libs(sandbox)
    media_dir = sandbox["media_dir"]
    filepath = media_dir / "Show.S01E01.mkv"
    filepath.write_bytes(b"E" * (main.DUMMY_MAX_BYTES + 1))

    child_id = "tv-en-2022-show-s01e01"
    result = main.cmd_prep(child_id, str(filepath))
    assert result is True

    # The parent id is everything before the trailing e01.
    lib = _read_series(sandbox)
    assert child_id in lib
    parent_id = lib[child_id]["parent_id"]
    assert parent_id in lib
    parent = lib[parent_id]
    assert parent["type"] == "season_map"
    assert parent["children"] == [child_id]
    assert parent["total_episodes"] == 1


def test_prep_early_skip_uploaded_creates_nothing(sandbox, stub_tech_specs):
    """A prep early-skip (entry already uploaded) returns True and creates NO
    artifacts (@ main.py 311-313). The rollback wrapper MUST treat this as a
    no-op success, never roll anything back."""
    media_dir = sandbox["media_dir"]
    filepath = media_dir / "Done.mkv"
    filepath.write_bytes(b"ALREADY-UPLOADED")
    manual_id = "mov-en-2020-done"
    sandbox["lib_movies"].write_text(
        json.dumps({manual_id: {"status": "archived", "uploaded": True,
                                "folder_path": str(media_dir), "filename": "Done.mkv"}}),
        encoding="utf-8",
    )
    sandbox["lib_series"].write_text("{}", encoding="utf-8")
    sandbox["lib_anime"].write_text("{}", encoding="utf-8")

    before = sorted(p.name for p in media_dir.iterdir())
    result = main.cmd_prep(manual_id, str(filepath))
    after = sorted(p.name for p in media_dir.iterdir())

    assert result is True
    assert before == after  # no sidecars created
    assert not (media_dir / "uid").exists()


# ===========================================================================
# cmd_push — split happy path lands chunks on device, flips onboarded
# ===========================================================================

_SPLIT_ID = "mov_baseline_split"


def _seed_split_entry(sandbox):
    """Seed a split entry with a populated _parts/ folder so cmd_push takes the
    resume path (no real ffmpeg split). Mirrors test_cmd_push_partial.split_entry
    but lives here so the baseline file is self-contained."""
    media_dir = sandbox["media_dir"]
    short_id = "bsl123"
    base = "movie.mkv"
    (media_dir / base).write_bytes(b"original-master-bytes")

    chunk_names = [f"movie [{short_id}].chunk.00{i}.mkv" for i in (1, 2, 3)]
    parts_dir = media_dir / main.SPLIT_DIR_NAME
    parts_dir.mkdir()
    chunks_meta = []
    for i, cn in enumerate(chunk_names, start=1):
        data = f"chunk-{i}-bytes".encode()
        (parts_dir / cn).write_bytes(data)
        chunks_meta.append({"filename": cn, "hash": hashlib.sha256(data).hexdigest()})

    entry = {
        _SPLIT_ID: {
            "short_id": short_id,
            "filename": base,
            "folder_path": str(media_dir),
            "status": "local_ready",
            "uploaded": False,
            "hash": "originalhash",
            "metadata": {"title": _SPLIT_ID, "year": 2024},
            "tech_spec": {"resolution": "1080p"},
            "split_info": {
                "is_split": True, "method": "SIZE_MB", "val": "8000",
                "total_chunks": 3, "chunks": chunks_meta,
            },
        }
    }
    sandbox["lib_movies"].write_text(json.dumps(entry), encoding="utf-8")
    sandbox["lib_series"].write_text("{}", encoding="utf-8")
    sandbox["lib_anime"].write_text("{}", encoding="utf-8")
    return {"media_dir": media_dir, "parts_dir": parts_dir, "chunk_names": chunk_names}


def test_push_split_happy_path_oracle(sandbox, mock_device):
    """A clean split push uploads all 3 chunks to the device, removes the local
    _parts/ chunks, and flips the entry to onboarded/uploaded. This is the push
    success oracle; O-1 means a push FAILURE is resume-message (never rollback),
    so the candidates must leave THIS path identical."""
    fx = _seed_split_entry(sandbox)
    expected = {cn: (fx["parts_dir"] / cn).read_bytes() for cn in fx["chunk_names"]}

    result = main.cmd_push(_SPLIT_ID)
    assert result is True

    # All chunk bytes landed on the device at their final names.
    on_device = {f.name: f for f in mock_device.rglob("*.mkv")}
    for name, data in expected.items():
        assert name in on_device, f"{name} missing on device"
        assert on_device[name].read_bytes() == data

    # Local chunks removed + empty _parts cleaned up.
    assert not fx["parts_dir"].exists() or not list(fx["parts_dir"].iterdir())

    # Library oracle
    entry = _read_movies(sandbox)[_SPLIT_ID]
    assert entry["uploaded"] is True
    assert entry["status"] == "onboarded"


# ===========================================================================
# cmd_replace — dummy goes live, status archived (fake_dummy stub)
# ===========================================================================

def test_replace_happy_path_oracle(sandbox_entry, fake_dummy):
    """A clean replace swaps the original for a dummy and marks the entry
    archived, leaving NO .dummy_tmp or .tobedeleted leftovers. The PONR is the
    commit rename @ main.py:990; pre-PONR the only rollback artifact is the
    dummy temp."""
    from conftest import FAKE_DUMMY_BYTES
    entry_id = sandbox_entry["entry_id"]
    orig_path = sandbox_entry["orig_path"]
    media_dir = sandbox_entry["media_dir"]

    result = main.cmd_replace(entry_id)
    assert result is True

    assert orig_path.read_bytes() == FAKE_DUMMY_BYTES
    assert not os.path.exists(str(orig_path) + ".tobedeleted")
    for item in media_dir.iterdir():
        assert ".dummy_tmp" not in item.name

    lib = json.loads(open(str(main.LIBRARY_MOVIES), encoding="utf-8").read())
    assert lib[entry_id]["status"] == "archived"


# ===========================================================================
# cmd_restore — split + standard happy paths
# ===========================================================================

_RESTORE_ID = "mov_baseline_restore"


def test_restore_split_happy_path_oracle(sandbox, monkeypatch):
    """A clean split restore merges the chunks to the target, flips status to
    restored_local, and deletes the chunks from restore/ (the PONR @ 1232-1234
    is AFTER the merge). merge_video_files is stubbed (no real mkvmerge) — the
    corrupt-chunk path is covered separately in test_cmd_restore_quarantine."""
    media_dir = str(sandbox["media_dir"])
    filename = "film.mkv"
    restore_folder = os.path.join(media_dir, main.RESTORE_DIR_NAME)
    os.makedirs(restore_folder, exist_ok=True)

    b1, b2 = b"CHUNK-ONE-GOOD", b"CHUNK-TWO-GOOD"
    c1, c2 = "film.chunk.001.mkv", "film.chunk.002.mkv"
    open(os.path.join(restore_folder, c1), "wb").write(b1)
    open(os.path.join(restore_folder, c2), "wb").write(b2)

    entry = {
        "status": "archived", "uploaded": True, "folder_path": media_dir,
        "filename": filename, "hash": "placeholder",
        "split_info": {
            "is_split": True, "total_chunks": 2,
            "chunks": [
                {"filename": c1, "hash": hashlib.sha256(b1).hexdigest()},
                {"filename": c2, "hash": hashlib.sha256(b2).hexdigest()},
            ],
        },
    }
    sandbox["lib_movies"].write_text(json.dumps({_RESTORE_ID: entry}), encoding="utf-8")
    sandbox["lib_series"].write_text("{}", encoding="utf-8")
    sandbox["lib_anime"].write_text("{}", encoding="utf-8")

    target_path = os.path.join(media_dir, filename)

    def _fake_merge(chunk_paths, output_path, seed=None):
        with open(output_path, "wb") as f:
            f.write(b1 + b2)
        return True

    monkeypatch.setattr(main, "merge_video_files", _fake_merge)

    result = main.cmd_restore(_RESTORE_ID)
    assert result is True

    assert os.path.exists(target_path)
    assert open(target_path, "rb").read() == b1 + b2
    # Chunks removed after the merge (post-PONR cleanup).
    assert not os.path.exists(os.path.join(restore_folder, c1))
    assert not os.path.exists(os.path.join(restore_folder, c2))

    lib = _read_movies(sandbox)
    assert lib[_RESTORE_ID]["status"] == "restored_local"
    assert lib[_RESTORE_ID]["hash"] == hashlib.sha256(b1 + b2).hexdigest()


def test_restore_standard_happy_path_oracle(sandbox):
    """A clean standard restore moves the verified file from restore/ to the
    target and flips status to restored_local (single shutil.move — no torn
    window, no PONR)."""
    media_dir = str(sandbox["media_dir"])
    filename = "film.mkv"
    restore_folder = os.path.join(media_dir, main.RESTORE_DIR_NAME)
    os.makedirs(restore_folder, exist_ok=True)

    good = b"GOOD-MASTER-BYTES"
    open(os.path.join(restore_folder, filename), "wb").write(good)
    open(os.path.join(media_dir, filename), "wb").write(b"DUMMY")  # placeholder at target

    entry = {
        "status": "archived", "uploaded": True, "folder_path": media_dir,
        "filename": filename, "hash": hashlib.sha256(good).hexdigest(),
    }
    sandbox["lib_movies"].write_text(json.dumps({_RESTORE_ID: entry}), encoding="utf-8")
    sandbox["lib_series"].write_text("{}", encoding="utf-8")
    sandbox["lib_anime"].write_text("{}", encoding="utf-8")

    target_path = os.path.join(media_dir, filename)
    result = main.cmd_restore(_RESTORE_ID)
    assert result is True

    assert os.path.exists(target_path)
    assert open(target_path, "rb").read() == good
    assert not os.path.isdir(restore_folder)  # cleaned up (was empty after move)

    lib = _read_movies(sandbox)
    assert lib[_RESTORE_ID]["status"] == "restored_local"
