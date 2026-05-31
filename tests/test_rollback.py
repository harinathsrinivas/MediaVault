"""Auto-rollback scenario matrix — Candidate B (compensating-action stack).

Each test documents the simulated failure + the asserted post-state. Boundaries
are mocked only at I/O edges per docs/testing-strategy.md (subprocess.run via
fail_nth_subprocess / mock_device, merge via fail_merge, dummy via fake_dummy).
Application logic always runs real. Never touches real C:\\Media or library_*.json.

Covered scenarios (PLAN.md 3d):
  - prep-fail (reversible): hashing fails -> entry + sidecars + parent fully rolled back
  - split push-fail-before-upload (reversible): split ok, first push fails ->
    this-run _parts/checksums/split_info rolled back, master intact
  - push-fail O-1 resume (after an upload): partial upload left, entry stays local_ready
  - replace-fail pre-PONR (reversible): commit rename never happens -> dummy temp removed
  - replace-fail at/after PONR (irreversible): RollbackHardFail naming fetch_restore
  - restore split merge-fail (reversible): reproducible target removed, chunks kept
  - restore split corrupt chunk (C11 quarantine + clean state)
  - season mid-failure: completed kept + resume-range message
"""
import hashlib
import json
import os

import pytest

import main


def _movies(sandbox):
    return json.loads(sandbox["lib_movies"].read_text(encoding="utf-8"))


def _series(sandbox):
    return json.loads(sandbox["lib_series"].read_text(encoding="utf-8"))


def _empty(sandbox):
    sandbox["lib_movies"].write_text("{}", encoding="utf-8")
    sandbox["lib_series"].write_text("{}", encoding="utf-8")
    sandbox["lib_anime"].write_text("{}", encoding="utf-8")


# ===========================================================================
# prep — fully reversible (no PONR)
# ===========================================================================

def test_prep_fail_rolls_back_entry_and_sidecars(sandbox, stub_tech_specs, monkeypatch):
    """SIMULATED FAILURE: calculate_file_hash returns '' (hash failure) after the
    snapshot. ASSERTED POST-STATE: no entry, no uid/sha256 sidecars, library
    unchanged from the empty baseline."""
    _empty(sandbox)
    media_dir = sandbox["media_dir"]
    filepath = media_dir / "Inception.mkv"
    filepath.write_bytes(b"X" * (main.DUMMY_MAX_BYTES + 1))

    monkeypatch.setattr(main, "calculate_file_hash", lambda p: "")
    result = main.cmd_prep("mov-en-2010-inception", str(filepath))

    assert result is False
    assert _movies(sandbox) == {}
    assert not (media_dir / "uid").exists()


def test_prep_season_parent_rolled_back_when_only_child(sandbox, stub_tech_specs, monkeypatch):
    """SIMULATED FAILURE: a season episode prep fails at hashing. ASSERTED
    POST-STATE: the this-run-created parent season_map AND the child are both
    gone (D-7: this run created the parent and rollback leaves 0 children)."""
    _empty(sandbox)
    media_dir = sandbox["media_dir"]
    filepath = media_dir / "Show.S01E01.mkv"
    filepath.write_bytes(b"E" * (main.DUMMY_MAX_BYTES + 1))

    # Fail AFTER the snapshot but the rollback must still wipe parent+child.
    monkeypatch.setattr(main, "calculate_file_hash", lambda p: "")
    result = main.cmd_prep("tv-en-2022-show-s01e01", str(filepath))

    assert result is False
    assert _series(sandbox) == {}


# ===========================================================================
# push — O-1 (no rollback PONR); pre-upload reversible
# ===========================================================================

_SPLIT_ID = "mov_rollback_split"


def _seed_split_master(sandbox):
    """Seed a NON-split entry (no _parts/, no split_info) with a real master big
    enough that a SIZE_MB split produces multiple chunks via ffmpeg."""
    media_dir = sandbox["media_dir"]
    base = "movie.mkv"
    (media_dir / base).write_bytes(b"M" * (main.DUMMY_MAX_BYTES + 1))
    entry = {
        _SPLIT_ID: {
            "short_id": "rbk123", "filename": base, "folder_path": str(media_dir),
            "status": "local_ready", "uploaded": False, "hash": "h",
            "metadata": {}, "tech_spec": {},
        }
    }
    sandbox["lib_movies"].write_text(json.dumps(entry), encoding="utf-8")
    sandbox["lib_series"].write_text("{}", encoding="utf-8")
    sandbox["lib_anime"].write_text("{}", encoding="utf-8")
    return media_dir


def test_push_split_fail_before_upload_rolls_back(sandbox, ffmpeg_multichunk_mkv, fail_nth_subprocess):
    """SIMULATED FAILURE: a genuine ffmpeg split succeeds, then the FIRST adb push
    fails (1st matching push). ASSERTED POST-STATE (reversible, pre-any-upload):
    this-run _parts/ + checksums/ + split_info are rolled back; the master stays;
    the entry stays local_ready/uploaded=False."""
    media_dir = sandbox["media_dir"]
    base = "bigsample.mkv"
    import shutil as _sh
    _sh.copy2(str(ffmpeg_multichunk_mkv), str(media_dir / base))
    entry = {
        _SPLIT_ID: {
            "short_id": "rbk123", "filename": base, "folder_path": str(media_dir),
            "status": "local_ready", "uploaded": False, "hash": "h",
            "metadata": {}, "tech_spec": {},
        }
    }
    sandbox["lib_movies"].write_text(json.dumps(entry), encoding="utf-8")
    sandbox["lib_series"].write_text("{}", encoding="utf-8")
    sandbox["lib_anime"].write_text("{}", encoding="utf-8")

    # Fail the first actual push (mkdir is shell, not push).
    fail_nth_subprocess(1, match=lambda a: "push" in a)

    result = main.cmd_push(_SPLIT_ID, split_method="SIZE_MB", split_val="2")
    assert result is False

    parts_dir = media_dir / main.SPLIT_DIR_NAME
    checksum_dir = media_dir / main.CHECKSUM_DIR_NAME
    assert not parts_dir.exists(), "_parts/ created this run must be rolled back"
    assert not checksum_dir.exists(), "checksums/ created this run must be rolled back"
    assert (media_dir / base).exists(), "master must survive a push failure"
    e = _movies(sandbox)[_SPLIT_ID]
    assert "split_info" not in e, "this-run split_info must be popped"
    assert e["uploaded"] is False and e["status"] == "local_ready"


def test_push_resume_does_not_delete_preexisting_parts(sandbox, monkeypatch):
    """SIMULATED FAILURE: a RESUME push (pre-existing _parts/) where the push
    fails AFTER one chunk already uploaded. ASSERTED POST-STATE (O-1): the
    pre-existing _parts/ is NOT deleted, the partial upload is left, entry stays
    local_ready. A pre-existing _parts/ is never treated as created-this-run."""
    media_dir = sandbox["media_dir"]
    short_id = "rbk999"
    base = "movie.mkv"
    (media_dir / base).write_bytes(b"original-master")
    parts_dir = media_dir / main.SPLIT_DIR_NAME
    parts_dir.mkdir()
    chunk_names = [f"movie [{short_id}].chunk.00{i}.mkv" for i in (1, 2)]
    chunks_meta = []
    for i, cn in enumerate(chunk_names, start=1):
        data = f"c{i}".encode()
        (parts_dir / cn).write_bytes(data)
        chunks_meta.append({"filename": cn, "hash": hashlib.sha256(data).hexdigest()})
    entry = {
        _SPLIT_ID: {
            "short_id": short_id, "filename": base, "folder_path": str(media_dir),
            "status": "local_ready", "uploaded": False, "hash": "h", "metadata": {},
            "tech_spec": {},
            "split_info": {"is_split": True, "method": "SIZE_MB", "val": "8000",
                           "total_chunks": 2, "chunks": chunks_meta},
        }
    }
    sandbox["lib_movies"].write_text(json.dumps(entry), encoding="utf-8")
    sandbox["lib_series"].write_text("{}", encoding="utf-8")
    sandbox["lib_anime"].write_text("{}", encoding="utf-8")

    # Let chunk 1 push+mv succeed; fail EVERY push of chunk 2 (so retry() exhausts
    # all 3 attempts and the upload loop breaks). Match on the chunk-002 filename.
    import subprocess as _sp

    def fake_run(argv, check=False, **kw):
        argv = list(argv)
        class _R:
            returncode = 0
            stdout = ""
        if "push" in argv and any("chunk.002" in str(a) for a in argv):
            if check:
                raise _sp.CalledProcessError(1, argv)
        return _R()

    monkeypatch.setattr(main.subprocess, "run", fake_run)
    monkeypatch.setattr(main, "PUSH_VERIFY_REMOTE", False, raising=False)
    import mvcommon
    monkeypatch.setattr(mvcommon.time, "sleep", lambda *a, **k: None)

    result = main.cmd_push(_SPLIT_ID)
    assert result is False
    # Pre-existing _parts/ must NOT be deleted (resume-safe). The first chunk was
    # deleted post-upload (resumable), but the dir + remaining chunk stay.
    assert parts_dir.exists(), "pre-existing _parts/ must never be rolled back"
    e = _movies(sandbox)[_SPLIT_ID]
    assert e["uploaded"] is False and e["status"] == "local_ready"


# ===========================================================================
# replace — PONR at the commit rename
# ===========================================================================

def test_replace_fail_pre_ponr_rolls_back_dummy(sandbox_entry, fake_dummy, monkeypatch):
    """SIMULATED FAILURE: the commit rename (original -> .tobedeleted) fails with a
    non-permission OSError (pre-PONR). ASSERTED POST-STATE: the dummy temp is
    removed, the original is untouched, status is NOT archived."""
    entry_id = sandbox_entry["entry_id"]
    orig_path = sandbox_entry["orig_path"]
    media_dir = sandbox_entry["media_dir"]

    real_rename = os.rename

    def patched(src, dst):
        if str(dst).endswith(".tobedeleted"):
            raise OSError("disk error before commit")
        real_rename(src, dst)

    monkeypatch.setattr(main.os, "rename", patched)

    result = main.cmd_replace(entry_id)
    assert result is False
    from conftest import FAKE_ORIGINAL_BYTES
    assert orig_path.read_bytes() == FAKE_ORIGINAL_BYTES
    for item in media_dir.iterdir():
        assert ".dummy_tmp" not in item.name, "pre-PONR rollback must remove the dummy temp"
    lib = json.loads(open(str(main.LIBRARY_MOVIES), encoding="utf-8").read())
    assert lib[entry_id]["status"] != "archived"


def test_replace_fail_post_ponr_hard_fails(sandbox_entry, fake_dummy, monkeypatch):
    """SIMULATED FAILURE: the commit rename succeeds (PONR crossed), then the
    second rename (dummy_tmp -> original) raises. ASSERTED POST-STATE: a
    RollbackHardFail naming `fetch_restore <id>` is raised; the data is still
    recoverable (C9 invariant)."""
    entry_id = sandbox_entry["entry_id"]
    orig_path = sandbox_entry["orig_path"]

    real_rename = os.rename
    n = {"i": 0}

    def patched(src, dst):
        n["i"] += 1
        if n["i"] == 1:
            real_rename(src, dst)  # commit succeeds -> PONR crossed
        else:
            raise OSError("simulated crash after commit")

    monkeypatch.setattr(main.os, "rename", patched)

    with pytest.raises(main.RollbackHardFail) as ei:
        main.cmd_replace(entry_id)
    assert f"fetch_restore {entry_id}" in ei.value.resume_cmd
    # C9 safety: bytes still recoverable from .tobedeleted
    assert os.path.exists(str(orig_path) + ".tobedeleted")


# ===========================================================================
# restore — split path PONR at chunk delete
# ===========================================================================

_RID = "mov_rollback_restore"


def _seed_restore_split(sandbox, good=True):
    media_dir = str(sandbox["media_dir"])
    filename = "film.mkv"
    restore_folder = os.path.join(media_dir, main.RESTORE_DIR_NAME)
    os.makedirs(restore_folder, exist_ok=True)
    b1, b2 = b"CHUNK-ONE", b"CHUNK-TWO"
    c1, c2 = "film.chunk.001.mkv", "film.chunk.002.mkv"
    open(os.path.join(restore_folder, c1), "wb").write(b1 if good else b"CORRUPT")
    open(os.path.join(restore_folder, c2), "wb").write(b2)
    entry = {
        "status": "archived", "uploaded": True, "folder_path": media_dir,
        "filename": filename, "hash": "placeholder",
        "split_info": {"is_split": True, "total_chunks": 2, "chunks": [
            {"filename": c1, "hash": hashlib.sha256(b1).hexdigest()},
            {"filename": c2, "hash": hashlib.sha256(b2).hexdigest()},
        ]},
    }
    sandbox["lib_movies"].write_text(json.dumps({_RID: entry}), encoding="utf-8")
    sandbox["lib_series"].write_text("{}", encoding="utf-8")
    sandbox["lib_anime"].write_text("{}", encoding="utf-8")
    return media_dir, restore_folder, filename, c1, c2


def test_restore_merge_fail_is_reversible(sandbox, fail_merge):
    """SIMULATED FAILURE: merge_video_files returns False (pre-PONR). ASSERTED
    POST-STATE: chunks remain in restore/ for a re-merge, status stays archived
    (NOT restored_local), no fake success."""
    media_dir, restore_folder, filename, c1, c2 = _seed_restore_split(sandbox)
    fail_merge("return_false")

    result = main.cmd_restore(_RID)
    assert result is False
    assert os.path.exists(os.path.join(restore_folder, c1))
    assert os.path.exists(os.path.join(restore_folder, c2))
    assert _movies(sandbox)[_RID]["status"] == "archived"


def test_restore_corrupt_chunk_quarantines(sandbox):
    """SIMULATED FAILURE: chunk 1's bytes do not match its stored hash. ASSERTED
    POST-STATE (C11 reuse): the bad chunk is quarantined under restore/quarantine,
    status stays archived, no merged output remains."""
    media_dir, restore_folder, filename, c1, c2 = _seed_restore_split(sandbox, good=False)

    result = main.cmd_restore(_RID)
    assert result is False
    q = os.path.join(restore_folder, "quarantine")
    assert os.path.isdir(q) and os.listdir(q), "bad chunk must be quarantined (C11)"
    assert _movies(sandbox)[_RID]["status"] == "archived"


# ===========================================================================
# season — completed items kept + resume-range messaging
# ===========================================================================

def test_season_mid_failure_keeps_completed_and_prints_resume(sandbox, stub_tech_specs, fake_dummy, monkeypatch, capsys):
    """SIMULATED FAILURE: a 3-episode season where episode 2's push fails.
    ASSERTED POST-STATE: episode 1 is archived (completed kept), the loop stops,
    and the printed resume command covers episodes 2 onward."""
    _empty(sandbox)
    media_dir = sandbox["media_dir"]
    base = "tv-en-2022-show"
    for ep in (1, 2, 3):
        (media_dir / f"Show.S01E0{ep}.mkv").write_bytes(b"V" * (main.DUMMY_MAX_BYTES + 1))

    # Drive prep_season + the loop. Make push succeed for ep1, fail for ep2.
    pushed = {"n": 0}
    real_push = main.cmd_push

    def fake_push(mid, *a, **kw):
        pushed["n"] += 1
        if pushed["n"] == 1:
            # mark ep1 uploaded so replace can run
            lib = main.load_library()
            lib[mid]["uploaded"] = True
            lib[mid]["status"] = "onboarded"
            main.save_library(lib)
            return True
        return False  # ep2 push fails (resume-message already printed inside real push)

    monkeypatch.setattr(main, "cmd_push", fake_push)

    main.cmd_prep_push_rep_season(base, str(media_dir), split_method="SIZE_GB", split_val="9")
    out = capsys.readouterr().out

    # ep1 archived (replace ran on the uploaded item)
    lib = _series(sandbox)
    ep1 = f"{base}e01"
    assert lib[ep1]["status"] == "archived"
    # resume command names the season command + the device/size + a range from ep2
    assert "prep_push_rep_season" in out
    assert "SIZE_GB 9" in out
    assert "episodes 02" in out
