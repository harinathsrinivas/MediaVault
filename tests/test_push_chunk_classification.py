"""IMP-C28 — a push decides "chunk vs whole file" by the file's PARENT DIR, never by
a "_parts" substring of its path.

Before the fix cmd_push treated ANY path containing the substring "_parts" as a
chunk. A whole-file push from a title folder such as `Spare_parts (2015)` was
uploaded WITHOUT its " [<short_id>]" tag (so fetch-by-id could not find it) and
the local MASTER was then deleted as if it were an uploaded chunk. A file now
counts as a chunk only if it lives directly in THIS push's parts dir
(`_parts_base(...)` + SPLIT_DIR_NAME, tempdir redirect included). That single rule,
`mvcommon.in_parts_dir`, is shared with the identity capture
(`gpcapture.snapshot_push_objects`), so the recorded upload name cannot drift from
the name the device actually received.

Fixtures (docs/testing-strategy.md §4): `sandbox` (dual LIBRARY_*/LOCAL_ROOT
patch + the C:\\Media hard-guard), `mock_device` (files really land on a fake
device), `make_video` (real-media-sized masters, > DUMMY_MAX_BYTES). Every title
folder is built under `sandbox["local_root"]`, so the remote dir mirrors it.
Device lookups index by `.name`, because "[short_id]" is a glob character class
(§8.1). The split tests stub the three I/O boundaries a real split needs
(mkvmerge `--split`, the `mkvmerge -J` probe, `shutil.disk_usage`), the same way
test_extras.py and test_rehash.py do.

Test names are long and keep "_parts" out of their first 30 characters. pytest
builds tmp_path from the first 30 characters of the test name, so no path here
carries the substring by accident: every "_parts" below is put there on purpose.
"""
import hashlib
import json
import os
import types

import pytest

import gpcapture
import main
import mvcommon

ENTRY_ID = "mov-en-2015-spareparts"


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _device_names(device_dir):
    """{filename: Path} for every .mkv on the fake device (index by name, §8.1)."""
    return {f.name: f for f in device_dir.rglob("*.mkv")}


def _title_dir(sandbox, folder_name):
    folder = sandbox["local_root"] / "Movies" / folder_name
    folder.mkdir(parents=True)
    return folder


def _seed_movie(folder, filename, file_hash, **extra):
    """A cmd_prep-shaped movie leaf for ENTRY_ID; `extra` adds split_info / extras."""
    short_id = mvcommon.generate_short_id(ENTRY_ID)
    stem, ext = os.path.splitext(filename)
    entry = {
        "short_id": short_id,
        "filename": filename,
        "folder_path": str(folder),
        "status": "local_ready",
        "uploaded": False,
        "search_term": f"{stem} [{short_id}]{ext}",
        "hash": file_hash,
        "metadata": main.parse_metadata_from_id(ENTRY_ID),
        "tech_spec": {"resolution": "1080p", "video_codec": "HEVC"},
    }
    entry.update(extra)
    mvcommon.save_library({ENTRY_ID: entry})
    return entry


def _last_push_objects(folder, short_id):
    """The objects of the newest push recorded in <folder>/<short_id>.gpcapture.json."""
    with open(gpcapture.capture_path(str(folder), short_id), encoding="utf-8") as f:
        return json.load(f)["pushes"][-1]["objects"]


def _install_fake_split(monkeypatch, n_chunks=2):
    """Replace mkvmerge `--split` with a byte-slicer that keeps split_video_file's
    contract: chunks named "<stem> [<file_id>].chunk.NNN.mkv", written into
    `output_dir`, returned as os.path.join(output_dir, name) in order. Also stubs
    the `mkvmerge -J` unsplittable-track probe (-> no such tracks) and gives the
    free-space pre-flight a huge disk (its 2 GB buffer floor would otherwise make
    the test depend on the host). Returns the recorded split calls."""
    calls = []

    def fake_split(input_path, output_dir, method, value_str, file_id=""):
        calls.append({"output_dir": output_dir, "method": method, "val": value_str})
        with open(input_path, "rb") as f:
            data = f.read()
        step = len(data) // n_chunks + 1
        stem = os.path.splitext(os.path.basename(input_path))[0]
        tag = f" [{file_id}]" if file_id else ""
        paths = []
        for i in range(n_chunks):
            p = os.path.join(output_dir, f"{stem}{tag}.chunk.{i + 1:03d}.mkv")
            with open(p, "wb") as f:
                f.write(data[i * step:(i + 1) * step])
            paths.append(p)
        return paths

    huge = 500 * 1024 ** 3
    monkeypatch.setattr(main, "split_video_file", fake_split)
    monkeypatch.setattr(main, "find_unsplittable_tracks", lambda path: [])
    monkeypatch.setattr(main.shutil, "disk_usage",
                        lambda path: types.SimpleNamespace(total=huge, used=0, free=huge))
    return calls


# Title folders that CONTAIN "_parts" without being a chunk dir. "Body_parts"
# ENDS in it, so its master's path also contains "_parts" + a separator. That
# defeats a "fix" which merely anchors the substring test on a separator.
LOOKALIKE_FOLDERS = [
    pytest.param("Spare_parts (2015)", "Spare_parts (2015).mkv", id="in-folder-and-name"),
    pytest.param("Body_parts", "Body.Parts.2015.mkv", id="folder-ends-with-it"),
]

# Every route by which cmd_push uploads the master WHOLE: a plain push, a split
# that is skipped because the file is under the target, and a push with a temp
# dir (the chunk dir is redirected elsewhere, but nothing is split).
WHOLE_FILE_ROUTES = [
    pytest.param({}, id="plain-push"),
    pytest.param({"split_method": "SIZE_MB", "split_val": "8000"}, id="split-skipped-under-target"),
    pytest.param({"temp_dir": "SCRATCH"}, id="tempdir-given"),
]


# ---------------------------------------------------------------------------
# (1) the bug: a whole-file push from a lookalike folder
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("folder_name, filename", LOOKALIKE_FOLDERS)
@pytest.mark.parametrize("route", WHOLE_FILE_ROUTES)
def test_whole_file_push_keeps_its_tag_and_master_beside_a_chunk_dir_lookalike(
        sandbox, mock_device, make_video, tmp_path, folder_name, filename, route):
    """The reproduction. Before the fix, a master whose path merely CONTAINS "_parts"
    was uploaded untagged and then deleted locally."""
    title = _title_dir(sandbox, folder_name)
    master, sha256 = make_video(title / filename)
    with open(master, "rb") as f:
        master_bytes = f.read()
    entry = _seed_movie(title, filename, sha256)
    kwargs = dict(route)
    if kwargs.get("temp_dir") == "SCRATCH":
        scratch = tmp_path / "scratch"
        scratch.mkdir()
        kwargs["temp_dir"] = str(scratch)

    assert main.cmd_push(ENTRY_ID, **kwargs) is True

    tagged = entry["search_term"]  # "<stem> [<short_id>].mkv" (what fetch-by-id looks for)
    on_device = _device_names(mock_device)
    assert tagged in on_device, f"uploaded without its [short_id] tag: {sorted(on_device)}"
    assert filename not in on_device, "the bare local name must never reach the device"
    assert on_device[tagged].read_bytes() == master_bytes
    # The master is the source of truth: a push never deletes it (O-1).
    assert os.path.exists(master), "cmd_push deleted the local master"
    with open(master, "rb") as f:
        assert f.read() == master_bytes
    stored = mvcommon.load_library()[ENTRY_ID]
    assert (stored["uploaded"], stored["status"]) == (True, "onboarded")
    # The identity capture records the SAME name the device received.
    (obj,) = _last_push_objects(title, entry["short_id"])
    assert (obj["role"], obj["uploaded_name"], obj["sha256"]) == ("whole", tagged, sha256)


# ---------------------------------------------------------------------------
# (2) split pushes from a lookalike folder: only this push's chunks are chunks
# ---------------------------------------------------------------------------

def test_resumed_split_push_deletes_only_chunks_never_the_master(sandbox, mock_device, make_video):
    """Resume branch (a populated _parts/ is already on disk). The chunks keep
    their own names, which already carry the tag, so they are never tagged twice.
    Each chunk is deleted once uploaded, and the master next to them survives."""
    title = _title_dir(sandbox, "Spare_parts (2015)")
    master, sha256 = make_video(title / "Spare_parts (2015).mkv")
    short_id = mvcommon.generate_short_id(ENTRY_ID)
    parts = title / main.SPLIT_DIR_NAME
    parts.mkdir()
    names = [f"Spare_parts (2015) [{short_id}].chunk.00{i}.mkv" for i in (1, 2)]
    chunk_bytes = {}
    for i, name in enumerate(names, start=1):
        chunk_bytes[name] = f"chunk-{i}-bytes".encode()
        (parts / name).write_bytes(chunk_bytes[name])
    # Real chunk hashes, as a split records them: a resume checks the bytes against them (IMP-C32).
    _seed_movie(title, "Spare_parts (2015).mkv", sha256, split_info={
        "is_split": True, "method": "COUNT", "val": "2", "total_chunks": 2,
        "chunks": [{"filename": n, "hash": hashlib.sha256(chunk_bytes[n]).hexdigest()} for n in names],
    })

    assert main.cmd_push(ENTRY_ID) is True

    on_device = _device_names(mock_device)
    for name in names:
        assert on_device[name].read_bytes() == chunk_bytes[name]
    assert [n for n in on_device if n.count(f"[{short_id}]") != 1] == [], \
        f"a chunk was tagged twice (treated as a whole file): {sorted(on_device)}"
    assert not parts.exists(), "uploaded chunks must be deleted and the emptied chunk dir removed"
    assert os.path.exists(master), "cmd_push deleted the local master"
    assert [(o["role"], o["index"], o["uploaded_name"]) for o in _last_push_objects(title, short_id)] == \
        [("chunk", 1, names[0]), ("chunk", 2, names[1])]


@pytest.mark.parametrize("redirect", [False, True], ids=["chunk-dir-in-title", "chunk-dir-redirected"])
def test_fresh_split_push_uploads_and_deletes_only_this_push_chunks(
        sandbox, mock_device, make_video, tmp_path, monkeypatch, redirect):
    """New-split branch, with and without the temp-dir redirect. When the chunk dir
    is redirected (temp_dir/<safe id>/_parts), the chunks still count as chunks:
    the rule compares against the chunk dir this push computed, not against
    <title>/_parts. The master in the lookalike folder is never touched."""
    split_calls = _install_fake_split(monkeypatch)
    title = _title_dir(sandbox, "Spare_parts (2015)")
    master, sha256 = make_video(title / "Spare_parts (2015).mkv")
    with open(master, "rb") as f:
        master_bytes = f.read()
    entry = _seed_movie(title, "Spare_parts (2015).mkv", sha256)
    short_id = entry["short_id"]
    temp_dir = None
    expected_parts = title / main.SPLIT_DIR_NAME
    if redirect:
        temp_dir = tmp_path / "scratch"
        temp_dir.mkdir()
        expected_parts = temp_dir / ENTRY_ID / main.SPLIT_DIR_NAME

    assert main.cmd_push(ENTRY_ID, "COUNT", "2", temp_dir=str(temp_dir) if temp_dir else None) is True

    assert [c["output_dir"] for c in split_calls] == [str(expected_parts)]
    names = [f"Spare_parts (2015) [{short_id}].chunk.00{i}.mkv" for i in (1, 2)]
    on_device = _device_names(mock_device)
    assert set(on_device) == set(names), f"expected exactly the two chunks on the device: {sorted(on_device)}"
    assert b"".join(on_device[n].read_bytes() for n in names) == master_bytes
    assert not expected_parts.exists(), "uploaded chunks must be deleted and the emptied chunk dir removed"
    if redirect:
        assert not (temp_dir / ENTRY_ID).exists(), "the per-entry scratch dir this run created must be removed"
        assert not (title / main.SPLIT_DIR_NAME).exists(), "a redirected push must not create <title>/_parts"
    assert os.path.exists(master), "cmd_push deleted the local master"
    with open(master, "rb") as f:
        assert f.read() == master_bytes
    stored = mvcommon.load_library()[ENTRY_ID]
    assert (stored["uploaded"], stored["status"]) == (True, "onboarded")
    assert [c["filename"] for c in stored["split_info"]["chunks"]] == names
    assert [(o["role"], o["index"], o["uploaded_name"]) for o in _last_push_objects(title, short_id)] == \
        [("chunk", 1, names[0]), ("chunk", 2, names[1])]


# ---------------------------------------------------------------------------
# (3) extras pushed from a lookalike folder (push_one_extra)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("extras_size", [("NONE", None), ("COUNT", "2")], ids=["whole-extra", "split-extra"])
def test_extras_push_from_a_lookalike_folder_keeps_the_extra_master(
        sandbox, mock_device, make_video, monkeypatch, extras_size):
    """push_one_extra decides by `is_split`, so a whole extra in a folder that
    contains "_parts" is uploaded tagged and kept on disk. A split extra's chunks
    are deleted from ITS item chunk dir (<extra folder>/_parts/<short_id>), and
    the extra's master survives."""
    split = extras_size[0] != "NONE"
    if split:
        _install_fake_split(monkeypatch)
    title = _title_dir(sandbox, "Spare_parts (2015)")
    _, main_sha = make_video(title / "Spare_parts (2015).mkv")
    specials = title / "Specials"
    specials.mkdir()
    bts, bts_sha = make_video(specials / "BTS.mkv", marker=b"EXTRA-BTS\n")
    with open(bts, "rb") as f:
        bts_bytes = f.read()
    item_sid = mvcommon.generate_short_id(f"{ENTRY_ID}::Specials/BTS.mkv")
    item = {
        "filename": "BTS.mkv", "sub_rel": "BTS.mkv", "short_id": item_sid, "hash": bts_sha,
        "status": "local_ready", "uploaded": False, "search_term": f"BTS [{item_sid}].mkv",
        "tech_spec": {"resolution": "1080p", "video_codec": "HEVC", "size_bytes": len(bts_bytes)},
    }
    _seed_movie(title, "Spare_parts (2015).mkv", main_sha,
                extras={"groups": {"Specials": {"added_date": "2026-10-01", "items": [item]}}})

    assert main.push_title_extras(mvcommon.load_library(), ENTRY_ID, extras_size) is True

    on_device = _device_names(mock_device)
    if split:
        names = [f"BTS [{item_sid}].chunk.00{i}.mkv" for i in (1, 2)]
        assert set(on_device) == set(names), sorted(on_device)
        assert b"".join(on_device[n].read_bytes() for n in names) == bts_bytes
        assert not (specials / main.SPLIT_DIR_NAME / item_sid).exists(), "uploaded extra chunks must be deleted"
    else:
        assert set(on_device) == {f"BTS [{item_sid}].mkv"}, f"whole extra must be uploaded tagged: {sorted(on_device)}"
    assert os.path.exists(bts), "push_one_extra deleted the extra's master"
    with open(bts, "rb") as f:
        assert f.read() == bts_bytes
    (stored,) = mvcommon.load_library()[ENTRY_ID]["extras"]["groups"]["Specials"]["items"]
    assert (stored["uploaded"], stored["status"]) == (True, "onboarded")


# ---------------------------------------------------------------------------
# (4) the shared rule itself
# ---------------------------------------------------------------------------

def test_chunk_rule_matches_only_direct_children_of_the_chunk_dir(tmp_path):
    """mvcommon.in_parts_dir compares the normalised PARENT DIR with the push's chunk
    dir. No substring, prefix or folder-name match is involved."""
    title = tmp_path / "Spare_parts (2015)"
    parts = str(title / main.SPLIT_DIR_NAME)
    assert mvcommon.in_parts_dir(os.path.join(parts, "X [ab12cd].chunk.001.mkv"), parts)
    assert mvcommon.in_parts_dir(os.path.join(parts, "X [ab12cd].holder.mkv"), parts)
    # the master, next to the chunk dir, in a folder whose name contains "_parts"
    assert not mvcommon.in_parts_dir(str(title / "Spare_parts (2015).mkv"), parts)
    # a file whose name starts with the chunk dir's name, and a sibling dir that does too
    assert not mvcommon.in_parts_dir(str(title / "_parts.mkv"), parts)
    assert not mvcommon.in_parts_dir(str(title / "_parts_old" / "X.chunk.001.mkv"), parts)
    # only DIRECT children: every chunk producer writes straight into the chunk dir
    assert not mvcommon.in_parts_dir(os.path.join(parts, "nested", "X.chunk.001.mkv"), parts)
    # a different push's chunk dir is not this push's chunk dir
    assert not mvcommon.in_parts_dir(os.path.join(parts, "X.chunk.001.mkv"), str(tmp_path / main.SPLIT_DIR_NAME))
    # spelling differences are normalised away: separators, a trailing separator,
    # a relative spelling of the same place, and (on Windows) letter case
    assert mvcommon.in_parts_dir(parts.replace(os.sep, "/") + "/X.chunk.001.mkv", parts + os.sep)
    assert mvcommon.in_parts_dir(os.path.join("scratch", "_parts", "X.chunk.001.mkv"),
                                 os.path.abspath(os.path.join("scratch", "_parts")))
    if os.name == "nt":
        assert mvcommon.in_parts_dir(os.path.join(parts.upper(), "X.chunk.001.mkv"), parts)


def test_capture_snapshot_uses_the_same_chunk_rule_as_cmd_push(tmp_path):
    """gpcapture mirrors cmd_push's remote naming, so it must use the same rule.
    Files directly in the push's chunk dir keep their names. Any other file is a
    WHOLE upload renamed to "<stem> [<short_id>]<ext>". That includes a master in
    a "_parts" lookalike folder, and even one whose name starts with the chunk
    dir's name."""
    title = tmp_path / "Spare_parts (2015)"
    parts = title / main.SPLIT_DIR_NAME
    parts.mkdir(parents=True)
    master = title / "Spare_parts (2015).mkv"
    odd = title / "_parts.mkv"
    c1 = parts / "Spare_parts (2015) [abc123].chunk.001.mkv"
    holder = parts / "Spare_parts (2015) [abc123].holder.mkv"
    for p in (master, odd, c1, holder):
        p.write_bytes(b"x")
    objs = gpcapture.snapshot_push_objects(
        [str(master), str(odd), str(c1), str(holder)], str(parts), "abc123",
        {c1.name: "h1"}, "whole-hash",
        [{"holder_filename": holder.name, "holder_hash": "hh"}])
    assert [(o["role"], o["index"], o["uploaded_name"], o["sha256"]) for o in objs] == [
        ("whole", None, "Spare_parts (2015) [abc123].mkv", "whole-hash"),
        ("whole", None, "_parts [abc123].mkv", "whole-hash"),
        ("chunk", 1, c1.name, "h1"),
        ("holder", None, holder.name, "hh"),
    ]
