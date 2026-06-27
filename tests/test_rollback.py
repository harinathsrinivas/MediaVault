"""Auto-rollback scenario matrix — Candidate C (on-disk operation journal).

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
from conftest import _ffmpeg_available, _mkvmerge_available


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


@pytest.mark.skipif(not (_ffmpeg_available() and _mkvmerge_available()),
                    reason="real split-during-push needs ffmpeg + mkvmerge")
def test_push_split_fail_before_upload_rolls_back(sandbox, ffmpeg_splittable_master_mkv, monkeypatch):
    """SIMULATED FAILURE: a GENUINE multi-chunk split runs inside cmd_push (real
    split_video_file → mkvmerge on a high-entropy ~60 MB master), then the FIRST
    chunk's adb push fails permanently (every push attempt fails, so retry()
    exhausts and the first chunk never reaches the device). ASSERTED POST-STATE
    (reversible, pre-any-upload): this-run _parts/ + checksums/ + split_info are
    rolled back; the master stays; the entry stays local_ready/uploaded=False.

    This is the FIRST time the live split-push-fail→rollback path is exercised:
    the test was always skipped before (ffmpeg off PATH), and the old
    `ffmpeg_multichunk_mkv` source (~50 KB) could never split (split_video_file's
    +10 MB buffer → 1 chunk → cmd_push skipped the split → single-file push that
    retry() absorbed → push SUCCEEDED). A SIZE_MB "10" push of the ~60 MB master
    splits into ~3 real chunks (main.py:184-194), so the split branch actually runs.

    Why fail EVERY push (not fail_nth_subprocess(1)): cmd_push wraps each chunk
    push in retry(attempts=3) (main.py:1537). Failing only the 1st matching push
    lets the SAME first chunk succeed on retry attempt 2 → any_upload_done=True →
    the O-1 resume-message branch (main.py:1605), NOT the pre-upload rollback the
    assertions below require. To genuinely hit "first push fails → roll back this
    run" we fail every push so the first chunk never lands and any_upload_done
    stays False (main.py:1614 branch). Mirrors the established inline fail-every-
    matching-push pattern in test_push_resume_does_not_delete_preexisting_parts."""
    media_dir = sandbox["media_dir"]
    base = "bigsample.mkv"
    import shutil as _sh
    _sh.copy2(str(ffmpeg_splittable_master_mkv), str(media_dir / base))
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

    # Fail EVERY adb push permanently; let the mkdir (shell) and any other shell
    # calls through. The first chunk's push exhausts all 3 retry attempts and
    # raises, so the upload loop breaks before any chunk reaches the device
    # (any_upload_done stays False) → the pre-any-upload rollback branch.
    import subprocess as _sp

    def fake_run(argv, check=False, **kw):
        argv = list(argv)

        class _R:
            returncode = 0
            stdout = ""

        if "push" in argv:
            if check:
                raise _sp.CalledProcessError(1, argv)
            _R.returncode = 1
        return _R()

    monkeypatch.setattr(main.subprocess, "run", fake_run)
    import mvcommon
    monkeypatch.setattr(mvcommon.time, "sleep", lambda *a, **k: None)

    result = main.cmd_push(_SPLIT_ID, split_method="SIZE_MB", split_val="10")
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


# ===========================================================================
# Candidate C ONLY — durable journal crash-recovery (the distinguishing feature)
# ===========================================================================

def test_journal_survives_hard_kill_and_recovers(sandbox, stub_tech_specs, monkeypatch):
    """SIMULATED FAILURE: a hard process kill (SystemExit) during cmd_prep AFTER
    the sidecars + entry were written but BEFORE any in-process rollback could run
    — so the on-disk journal is the only record. ASSERTED POST-STATE: the journal
    file persists with the created-artifact records; recover_journal() then replays
    them, removing the orphaned sidecars + entry and deleting the journal. This is
    the crash-survival edge the in-memory candidates (A/B) cannot cover."""
    _empty(sandbox)
    media_dir = sandbox["media_dir"]
    filepath = media_dir / "Inception.mkv"
    filepath.write_bytes(b"X" * (main.DUMMY_MAX_BYTES + 1))

    # Kill the process right after save_library, before journal.commit().
    real_save = main.save_library
    state = {"saved": False}

    def killing_save(lib):
        real_save(lib)
        if not state["saved"]:
            state["saved"] = True
            raise SystemExit("simulated hard kill after save, before commit")

    monkeypatch.setattr(main, "save_library", killing_save)

    with pytest.raises(SystemExit):
        main.cmd_prep("mov-en-2010-inception", str(filepath))

    # The journal survived on disk and the entry/sidecars are still present
    # (the in-process rollback never got to run — SystemExit skipped the except).
    journal_path = media_dir / main.TXN_JOURNAL_NAME
    assert journal_path.exists(), "journal must persist a hard kill"
    assert (media_dir / "uid").exists()
    assert "mov-en-2010-inception" in _movies(sandbox)

    # Recovery replays the journalled inverses and cleans up.
    monkeypatch.setattr(main, "save_library", real_save)
    recovered = main.recover_journal(str(media_dir))
    assert recovered is True
    assert not journal_path.exists(), "journal removed after a clean recovery"
    assert not (media_dir / "uid").exists(), "orphan sidecar removed by recovery"
    assert _movies(sandbox) == {}, "orphan entry removed by recovery"


# ===========================================================================
# IMP-R6 — split restore merges to a TEMP + os.replace, so a merge/verify
# failure can never delete the archived dummy at target_path.
# ===========================================================================

def test_restore_merge_fail_preserves_dummy(sandbox, fail_merge, monkeypatch):
    """IMP-R6 SIMULATED FAILURE: merge_video_files returns False (pre-PONR) for a
    split restore whose target_path holds the archived dummy. ASSERTED POST-STATE:
    the dummy is STILL PRESENT and byte-for-byte intact (entry stays archived, a
    file still exists → no media-server drop), chunks are kept for a re-merge, and
    there is NO rollback-orphan (journal removed, no .merge_tmp left).

    This is the regression the old code missed: it merged directly onto target_path
    and recorded target_path as the reproducible output, so the merge-fail rollback
    `os.remove`d the dummy too. The merge now writes to a TEMP, recorded as the
    reproducible output, so rollback removes the temp and never the dummy.

    The restore-side disk pre-check (`_free_space_ok`, a 2 GB-buffer guard) is
    orthogonal to the merge-failure path under test, so it is stubbed True to keep
    this test hermetic (independent of the host's free space)."""
    monkeypatch.setattr(main, "_free_space_ok", lambda *a, **k: True)
    media_dir, restore_folder, filename, c1, c2 = _seed_restore_split(sandbox)
    target_path = os.path.join(media_dir, filename)
    dummy_bytes = b"DUMMY-PLACEHOLDER"
    with open(target_path, "wb") as f:
        f.write(dummy_bytes)

    fail_merge("return_false")

    result = main.cmd_restore(_RID)
    assert result is False
    # The dummy survives untouched (the core IMP-R6 guarantee).
    assert os.path.exists(target_path), "merge failure must NOT delete the archived dummy"
    with open(target_path, "rb") as f:
        assert f.read() == dummy_bytes, "dummy bytes must be byte-for-byte intact"
    # Chunks kept for a re-merge; status stays archived (no fake success).
    assert os.path.exists(os.path.join(restore_folder, c1))
    assert os.path.exists(os.path.join(restore_folder, c2))
    assert _movies(sandbox)[_RID]["status"] == "archived"
    # No rollback-orphan: clean rollback deleted the journal and no merge temp lingers.
    assert not os.path.exists(os.path.join(media_dir, main.TXN_JOURNAL_NAME))
    leftovers = [n for n in os.listdir(media_dir) if ".merge_tmp" in n]
    assert leftovers == [], f"merge temp must be cleaned up, found {leftovers}"


def test_restore_merge_crash_preserves_dummy(sandbox, fail_merge, monkeypatch):
    """IMP-R6: same guarantee when merge_video_files RAISES (unexpected mkvmerge
    crash) rather than returning False — the dummy still survives intact."""
    monkeypatch.setattr(main, "_free_space_ok", lambda *a, **k: True)
    media_dir, restore_folder, filename, c1, c2 = _seed_restore_split(sandbox)
    target_path = os.path.join(media_dir, filename)
    dummy_bytes = b"DUMMY-PLACEHOLDER-2"
    with open(target_path, "wb") as f:
        f.write(dummy_bytes)

    fail_merge("raise")

    result = main.cmd_restore(_RID)
    assert result is False
    assert os.path.exists(target_path)
    with open(target_path, "rb") as f:
        assert f.read() == dummy_bytes
    assert not os.path.exists(os.path.join(media_dir, main.TXN_JOURNAL_NAME))


def test_restore_split_success_swaps_temp_into_place(sandbox, monkeypatch):
    """IMP-R6 SUCCESS: a split restore merges into a TEMP sibling, then atomically
    os.replace()s it onto target_path ONLY after the hash gate passes. ASSERTED:
    the merge output never lands directly on target_path (proves merge-to-temp),
    the swapped file holds the merged bytes, the temp is consumed, the chunks are
    deleted (PONR unchanged), the journal is committed, and the first restore
    blesses the merged hash as canonical truth (no rehash regression).

    The restore-side disk pre-check (`_free_space_ok`) is stubbed True to keep this
    test hermetic (independent of the host's free space)."""
    monkeypatch.setattr(main, "_free_space_ok", lambda *a, **k: True)
    media_dir, restore_folder, filename, c1, c2 = _seed_restore_split(sandbox)
    target_path = os.path.join(media_dir, filename)
    with open(target_path, "wb") as f:
        f.write(b"DUMMY-PLACEHOLDER")

    merged = b"MERGED-REAL-OUTPUT-BYTES"
    seen = {"output_paths": []}

    def fake_merge(chunk_paths, output_path, seed=None):
        seen["output_paths"].append(output_path)
        with open(output_path, "wb") as f:
            f.write(merged)
        return True

    monkeypatch.setattr(main, "merge_video_files", fake_merge)

    result = main.cmd_restore(_RID)
    assert result is True
    # The merge wrote to a TEMP sibling, NEVER directly onto target_path.
    assert len(seen["output_paths"]) == 1
    out_path = seen["output_paths"][0]
    assert out_path != target_path
    assert os.path.basename(out_path) == "film.merge_tmp.mkv"
    # os.replace landed the merged bytes onto target_path (dummy replaced).
    assert os.path.exists(target_path)
    with open(target_path, "rb") as f:
        assert f.read() == merged
    # The temp was consumed by the swap — no orphan temp.
    assert not os.path.exists(out_path)
    # PONR reached: chunks deleted, journal committed.
    assert not os.path.exists(os.path.join(restore_folder, c1))
    assert not os.path.exists(os.path.join(restore_folder, c2))
    assert not os.path.exists(os.path.join(media_dir, main.TXN_JOURNAL_NAME))
    # No rehash regression: the first restore blesses the merged hash as truth.
    e = _movies(sandbox)[_RID]
    assert e["status"] == "restored_local"
    assert e["re_hashed"] is True
    assert e["hash"] == hashlib.sha256(merged).hexdigest()


# ===========================================================================
# IMP-R7 — opening a journal auto-recovers a leftover PRE-PONR journal before
# flushing a fresh one (crash → re-run no longer destroys recovery info), and
# preserves a POST-PONR / partial leftover under a timestamped name.
# ===========================================================================

def _write_leftover_journal(media_dir, manual_id, crossed_ponr, records):
    """Seed a leftover .mediavault_txn.json (as a crashed run would leave behind)."""
    jpath = os.path.join(str(media_dir), main.TXN_JOURNAL_NAME)
    with open(jpath, "w", encoding="utf-8") as f:
        json.dump({"manual_id": manual_id, "crossed_ponr": crossed_ponr,
                   "records": records}, f)
    return jpath


def _preserved_journals(media_dir):
    """Timestamped sibling journals (.mediavault_txn.<ts>.json), excluding the
    live .mediavault_txn.json and any transient .tmp."""
    return [n for n in os.listdir(str(media_dir))
            if n.startswith(".mediavault_txn.") and n.endswith(".json")
            and n != main.TXN_JOURNAL_NAME]


def test_journal_open_auto_recovers_pre_ponr_leftover(sandbox, capsys):
    """IMP-R7 (option b): opening a new journal over a leftover PRE-PONR journal
    AUTO-RUNS recover_journal() FIRST (its inverse removes the crashed run's orphan
    artifact), THEN flushes the fresh journal. Proves a crash→re-run recovers the
    crashed run instead of silently clobbering its inverses."""
    _empty(sandbox)
    media_dir = sandbox["media_dir"]
    orphan = media_dir / "rbk777.sha256"
    orphan.write_text("deadbeef *film.mkv", encoding="utf-8")

    _write_leftover_journal(media_dir, "mov-crashed", False,
                            [{"op": "create_file", "path": str(orphan)}])

    journal = main.RollbackJournal(str(media_dir), "mov-newrun")

    out = capsys.readouterr().out
    assert "Found an interrupted run's journal" in out
    # Recovery ran: the crashed run's orphan sidecar was removed.
    assert not orphan.exists(), "leftover artifact must be recovered before the new run"
    # A FRESH journal for the new run now sits at the path (empty records, new id).
    data = json.loads((media_dir / main.TXN_JOURNAL_NAME).read_text(encoding="utf-8"))
    assert data == {"manual_id": "mov-newrun", "crossed_ponr": False, "records": []}
    # No timestamped preserve for a clean pre-PONR recovery.
    assert _preserved_journals(media_dir) == []


def test_journal_open_preserves_post_ponr_leftover(sandbox, capsys):
    """IMP-R7: a leftover POST-PONR journal must NOT be auto-recovered (that run
    committed irreversibly) and must NOT be silently overwritten — it is preserved
    under a timestamped name, and a fresh journal is then created for the new run."""
    _empty(sandbox)
    media_dir = sandbox["media_dir"]
    # A file the post-PONR record points at: recovery must NOT touch it.
    keep = media_dir / "keep.sha256"
    keep.write_text("x", encoding="utf-8")

    _write_leftover_journal(media_dir, "mov-crashed", True,
                            [{"op": "create_file", "path": str(keep)}])

    journal = main.RollbackJournal(str(media_dir), "mov-newrun")

    out = capsys.readouterr().out
    assert "crossed its point-of-no-return" in out
    assert "preserving it for inspection" in out
    # Post-PONR recovery did NOT run — the referenced artifact survives.
    assert keep.exists(), "post-PONR leftover must NOT be auto-recovered"
    # The leftover was preserved (not destroyed) under a timestamped name.
    preserved = _preserved_journals(media_dir)
    assert len(preserved) == 1, f"expected one preserved journal, got {preserved}"
    pdata = json.loads((media_dir / preserved[0]).read_text(encoding="utf-8"))
    assert pdata["crossed_ponr"] is True
    assert pdata["records"] == [{"op": "create_file", "path": str(keep)}]
    # A fresh journal exists for the new run.
    data = json.loads((media_dir / main.TXN_JOURNAL_NAME).read_text(encoding="utf-8"))
    assert data == {"manual_id": "mov-newrun", "crossed_ponr": False, "records": []}


def test_journal_open_no_leftover_is_byte_unchanged(sandbox, capsys):
    """IMP-R7: the overwhelmingly common no-leftover path is byte-for-byte the old
    behavior — a fresh empty journal, no recovery call, no preserve, no extra files."""
    media_dir = sandbox["media_dir"]
    jpath = media_dir / main.TXN_JOURNAL_NAME
    assert not jpath.exists()  # precondition: no leftover

    journal = main.RollbackJournal(str(media_dir), "mov-fresh")

    out = capsys.readouterr().out
    assert "interrupted" not in out.lower()
    assert "recover" not in out.lower()
    assert "preserv" not in out.lower()
    # Canonical fresh-journal content.
    data = json.loads(jpath.read_text(encoding="utf-8"))
    assert data == {"manual_id": "mov-fresh", "crossed_ponr": False, "records": []}
    # No timestamped preserve was created.
    assert _preserved_journals(media_dir) == []


def test_journal_open_partial_recovery_preserves_leftover(sandbox, capsys, monkeypatch):
    """IMP-R7: if the auto-recovery is only PARTIAL (an inverse fails, e.g. a file
    lock), recover_journal leaves the journal in place — the fresh _flush() must NOT
    destroy those surviving inverses. They are preserved under a timestamped name so
    `recover` can be retried, and a fresh journal is still created for the new run."""
    _empty(sandbox)
    media_dir = sandbox["media_dir"]
    orphan = media_dir / "partial.sha256"
    orphan.write_text("x", encoding="utf-8")
    record = {"op": "create_file", "path": str(orphan)}
    _write_leftover_journal(media_dir, "mov-crashed", False, [record])

    # Simulate a partial recovery: the inverse replay reports failure, so
    # recover_journal keeps the journal on disk.
    monkeypatch.setattr(main, "_replay_inverses", lambda records, library: False)

    journal = main.RollbackJournal(str(media_dir), "mov-newrun")

    out = capsys.readouterr().out
    assert "Recovery was partial; preserving" in out
    # The surviving inverses were preserved, not destroyed.
    preserved = _preserved_journals(media_dir)
    assert len(preserved) == 1, f"expected one preserved journal, got {preserved}"
    pdata = json.loads((media_dir / preserved[0]).read_text(encoding="utf-8"))
    assert pdata["records"] == [record]
    # A fresh journal still exists for the new run.
    data = json.loads((media_dir / main.TXN_JOURNAL_NAME).read_text(encoding="utf-8"))
    assert data == {"manual_id": "mov-newrun", "crossed_ponr": False, "records": []}
