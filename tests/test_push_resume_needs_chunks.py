"""IMP-C31 — a push resumes only when its chunk dir actually holds a chunk.

cmd_push treated ANY non-empty `_parts/` as a resume. If that dir held no .mkv at
all, the resume list was empty, so the upload loop ran zero times and
`all_success` stayed True. Examples: the FLAC extract an interrupted carry-out
leaves behind, a stray note, a sub-folder. The entry was then marked
uploaded/onboarded with NOTHING sent; only the .mvmeta.json sidecar reached the
device. The next `replace` (the autopilot's very next leg) swapped the master for
a dummy, so the title existed nowhere.

The push now refuses such a resume and explains why. It refuses before anything
is journalled, the same clean early return as the free-space and unsplittable
pre-flights: the pre-existing `_parts/` is never touched (D-6), the library is
not saved, and the journal records nothing. A `_parts/` that does hold a chunk
(or the FLAC holder) resumes exactly as before.

Fixtures (docs/testing-strategy.md §4): `sandbox`, `mock_device` (uploads really land
on a fake device, or here must not), `make_video` (> DUMMY_MAX_BYTES), plus
`stub_tech_specs` + `fake_dummy` for the prep_push_rep autopilot. Device lookups
index by `.name` (§8.1).
"""
import json
import os
import types

import pytest

import main
import mvcommon

ENTRY_ID = "mov-en-2015-resumecheck"
FILENAME = "Resume.Check.2015.mkv"
STEM = "Resume.Check.2015"


def _title_folder(sandbox):
    folder = sandbox["local_root"] / "Movies" / "Resume Check (2015)"
    folder.mkdir(parents=True)
    return folder


def _seed(sandbox, make_video, **extra):
    """A cmd_prep-shaped movie leaf with a real master; `extra` adds split_info."""
    folder = _title_folder(sandbox)
    master, sha256 = make_video(folder / FILENAME)
    short_id = mvcommon.generate_short_id(ENTRY_ID)
    entry = {
        "short_id": short_id, "filename": FILENAME, "folder_path": str(folder),
        "status": "local_ready", "uploaded": False, "search_term": f"{STEM} [{short_id}].mkv",
        "hash": sha256, "metadata": main.parse_metadata_from_id(ENTRY_ID),
        "tech_spec": {"resolution": "1080p", "video_codec": "HEVC"},
    }
    entry.update(extra)
    mvcommon.save_library({ENTRY_ID: entry})
    return folder, master, short_id


def _split_info(names):
    return {"is_split": True, "method": "COUNT", "val": str(len(names)), "total_chunks": len(names),
            "chunks": [{"filename": n, "hash": f"h{i}"} for i, n in enumerate(names, start=1)]}


def _tree(root):
    """Everything under `root`: (relative path, is_dir, size)."""
    return sorted((str(p.relative_to(root)), p.is_dir(), None if p.is_dir() else p.stat().st_size)
                  for p in root.rglob("*"))


def _device_files(device_dir):
    return sorted(p.name for p in device_dir.rglob("*") if p.is_file())


def _assert_journal_recorded_nothing(folder):
    """cmd_push opens its journal before the resume check, so a refusal may leave the
    empty journal every pre-flight refusal leaves. It must record nothing to undo."""
    jpath = folder / main.TXN_JOURNAL_NAME
    if jpath.exists():
        data = json.loads(jpath.read_text(encoding="utf-8"))
        assert data["records"] == [], "the refusal must precede every journalled action"
        assert data["crossed_ponr"] is False


def _install_fake_split(monkeypatch):
    """mkvmerge `--split` -> a byte slicer honouring split_video_file's contract
    ("<stem> [<file_id>].chunk.NNN.mkv" written into output_dir, returned in
    order); the unsplittable-track probe finds nothing; the disk is huge."""
    calls = []

    def fake_split(input_path, output_dir, method, value_str, file_id=""):
        calls.append(output_dir)
        with open(input_path, "rb") as f:
            data = f.read()
        half = len(data) // 2 + 1
        stem = os.path.splitext(os.path.basename(input_path))[0]
        paths = []
        for i in range(2):
            p = os.path.join(output_dir, f"{stem} [{file_id}].chunk.{i + 1:03d}.mkv")
            with open(p, "wb") as f:
                f.write(data[i * half:(i + 1) * half])
            paths.append(p)
        return paths

    huge = 500 * 1024 ** 3
    monkeypatch.setattr(main, "split_video_file", fake_split)
    monkeypatch.setattr(main, "find_unsplittable_tracks", lambda path: [])
    monkeypatch.setattr(main.shutil, "disk_usage",
                        lambda path: types.SimpleNamespace(total=huge, used=0, free=huge))
    return calls


# What an interrupted or tampered-with chunk dir can hold without holding a chunk.
LEFTOVERS = {
    "flac-extract-of-an-interrupted-carry-out": lambda parts, sid: (parts / f"{STEM} [{sid}].flac").write_bytes(b"flac"),
    "stray-text-file": lambda parts, sid: (parts / "notes.txt").write_text("left behind"),
    "partial-remnant": lambda parts, sid: (parts / f"{STEM} [{sid}].chunk.002.mkv.partial").write_bytes(b"p"),
    "sub-folder": lambda parts, sid: (parts / "checksums").mkdir(),
}

PUSH_CALLS = [
    pytest.param({}, id="plain-push"),
    pytest.param({"split_method": "SIZE_GB", "split_val": "1"}, id="with-split-args"),
    pytest.param({"chunk_range": "1-2"}, id="chunks-range"),
]


# ---------------------------------------------------------------------------
# (1) the reproduction
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("call", PUSH_CALLS)
@pytest.mark.parametrize("leftover", sorted(LEFTOVERS))
def test_push_refuses_a_resume_whose_chunk_dir_holds_no_chunk(
        sandbox, mock_device, make_video, capsys, leftover, call):
    folder, master, short_id = _seed(sandbox, make_video)
    parts = folder / main.SPLIT_DIR_NAME
    parts.mkdir()
    LEFTOVERS[leftover](parts, short_id)
    parts_before = _tree(parts)
    library_before = mvcommon.load_library()
    with open(master, "rb") as f:
        master_bytes = f.read()

    assert main.cmd_push(ENTRY_ID, **call) is False, "nothing was uploaded, so the push did not succeed"

    assert mvcommon.load_library() == library_before, "the entry must not be marked uploaded/onboarded"
    assert _device_files(mock_device) == [], "nothing may reach the device, not even the .mvmeta.json sidecar"
    assert _tree(parts) == parts_before, "a pre-existing chunk dir is never touched (D-6)"
    with open(master, "rb") as f:
        assert f.read() == master_bytes
    _assert_journal_recorded_nothing(folder)
    out = capsys.readouterr().out
    assert str(parts) in out and "holds no chunk" in out, f"the refusal must name the dir and why:\n{out}"
    assert os.listdir(parts)[0] in out, "the refusal must show what it found there"
    assert "SUCCESS" not in out and "Upload Complete" not in out


def test_prep_push_rep_keeps_the_master_when_the_chunk_dir_holds_no_chunk(
        sandbox, mock_device, make_video, stub_tech_specs, fake_dummy, capsys):
    """The whole loss, end to end: before the fix the push "succeeded" with nothing
    uploaded and the autopilot's replace leg then swapped the master for a dummy."""
    folder = _title_folder(sandbox)
    master, _ = make_video(folder / FILENAME)
    with open(master, "rb") as f:
        master_bytes = f.read()
    parts = folder / main.SPLIT_DIR_NAME
    parts.mkdir()
    (parts / "notes.txt").write_text("left behind")
    mvcommon.save_library({})

    main.cmd_prep_push_rep(ENTRY_ID, master)

    stored = mvcommon.load_library()[ENTRY_ID]
    assert (stored["status"], stored["uploaded"]) == ("local_ready", False)
    with open(master, "rb") as f:
        assert f.read() == master_bytes, "the master was replaced by a dummy although nothing was uploaded"
    assert _device_files(mock_device) == []
    assert "Auto-Pilot Paused" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# (2) regression pins: every genuine resume still resumes
# ---------------------------------------------------------------------------

def test_a_stray_file_beside_real_chunks_does_not_block_their_resume(sandbox, mock_device, make_video):
    short_id = mvcommon.generate_short_id(ENTRY_ID)
    names = [f"{STEM} [{short_id}].chunk.00{i}.mkv" for i in (1, 2)]
    folder, master, _ = _seed(sandbox, make_video, split_info=_split_info(names))
    parts = folder / main.SPLIT_DIR_NAME
    parts.mkdir()
    for i, name in enumerate(names, start=1):
        (parts / name).write_bytes(f"chunk-{i}-bytes".encode())
    (parts / "notes.txt").write_text("left behind")

    assert main.cmd_push(ENTRY_ID) is True

    assert {f.name for f in mock_device.rglob("*.mkv")} == set(names)
    assert sorted(p.name for p in parts.iterdir()) == ["notes.txt"], "uploaded chunks are deleted, the stray file is not"
    assert os.path.exists(master)
    stored = mvcommon.load_library()[ENTRY_ID]
    assert (stored["uploaded"], stored["status"]) == (True, "onboarded")


def test_a_chunk_dir_holding_only_the_flac_holder_still_resumes(sandbox, mock_device, make_video):
    """Every chunk went up on an earlier run; only the carried-out FLAC holder is left."""
    short_id = mvcommon.generate_short_id(ENTRY_ID)
    holder = f"{STEM} [{short_id}].holder.mkv"
    info = _split_info([f"{STEM} [{short_id}].chunk.00{i}.mkv" for i in (1, 2)])
    info["carried_out_tracks"] = [{"holder_filename": holder, "holder_hash": "hh"}]
    folder, master, _ = _seed(sandbox, make_video, split_info=info)
    parts = folder / main.SPLIT_DIR_NAME
    parts.mkdir()
    (parts / holder).write_bytes(b"holder-bytes")

    assert main.cmd_push(ENTRY_ID) is True

    assert {f.name for f in mock_device.rglob("*.mkv")} == {holder}
    assert not parts.exists()
    assert os.path.exists(master)
    stored = mvcommon.load_library()[ENTRY_ID]
    assert (stored["uploaded"], stored["status"]) == (True, "onboarded")


def test_an_interrupted_split_is_recovered_before_the_resume_check(
        sandbox, mock_device, make_video, monkeypatch, capsys):
    """IMP-R7 recovery runs when the journal opens, BEFORE the resume check. A carry-out
    killed mid-way leaves a chunk dir holding only the FLAC extract, plus a journal
    that recorded creating that dir. The next push must recover that dir, not refuse
    it, and then split afresh."""
    split_calls = _install_fake_split(monkeypatch)
    folder, master, short_id = _seed(sandbox, make_video)
    parts = folder / main.SPLIT_DIR_NAME
    parts.mkdir()
    (parts / f"{STEM} [{short_id}].flac").write_bytes(b"flac")
    (folder / main.TXN_JOURNAL_NAME).write_text(json.dumps({
        "manual_id": ENTRY_ID, "crossed_ponr": False,
        "records": [{"op": "create_dir", "path": str(parts)}]}), encoding="utf-8")

    assert main.cmd_push(ENTRY_ID, "COUNT", "2") is True

    assert split_calls == [str(parts)], "a fresh split, into the recovered chunk dir"
    assert {f.name for f in mock_device.rglob("*.mkv")} == {f"{STEM} [{short_id}].chunk.00{i}.mkv" for i in (1, 2)}
    assert not parts.exists()
    assert os.path.exists(master)
    stored = mvcommon.load_library()[ENTRY_ID]
    assert (stored["uploaded"], stored["status"]) == (True, "onboarded")
    assert "Recovery complete" in capsys.readouterr().out


def test_an_empty_chunk_dir_is_not_a_resume(sandbox, mock_device, make_video):
    """Nothing to resume and nothing at risk: the push carries on as a whole-file push."""
    folder, master, short_id = _seed(sandbox, make_video)
    (folder / main.SPLIT_DIR_NAME).mkdir()

    assert main.cmd_push(ENTRY_ID) is True

    assert {f.name for f in mock_device.rglob("*.mkv")} == {f"{STEM} [{short_id}].mkv"}
    assert os.path.exists(master)
    stored = mvcommon.load_library()[ENTRY_ID]
    assert (stored["uploaded"], stored["status"]) == (True, "onboarded")
