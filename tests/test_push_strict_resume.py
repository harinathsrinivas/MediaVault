"""IMP-C32 — strict resume: a push resumes only what its OWN split_info records.

Before the fix a resume uploaded every `.mkv` it found in the chunk dir and then
marked the entry onboarded. Two shapes made that wrong:

  (a) The chunk dir belongs to a FOLDER, not an entry. Every episode of a season
      shares `Season NN/_parts/`, so pushing episode 2 while episode 1's chunks
      waited there uploaded episode 1's chunks and marked episode 2 onboarded.
  (b) Chunks no split_info records: a failed split's partial chunk, a lone FLAC
      holder, or the leftovers of an interrupted push whose entry was re-prepped
      (cmd_prep rebuilds the entry, which drops its split_info). The entry ended
      onboarded with no split record.

Either way the next `replace` swapped the master for a dummy.

The rule (user ruling 2026-10-02, the rollback change-gate decision): a resume
uploads only the files this entry's split_info records, its chunks and its
carried-out holders, and only when their bytes still match the recorded sha256.
Everything else in the dir is a stranger: never uploaded, never deleted, always
listed with its likely owner. With nothing recorded to upload the push refuses
(IMP-C31's refusal is this empty case). The same rule guards the extras resume.

Fixtures (docs/testing-strategy.md §4): `sandbox`, `mock_device`, `make_video`,
`sandbox_extras`, plus `stub_tech_specs` + `fake_dummy` for the season autopilot.
Device lookups index by `.name` (§8.1). Chunk hashes are REAL sha256 values, as
split_info always records them.
"""
import hashlib
import json
import os
import subprocess
import types

import pytest

import main
import mvcommon

SEASON = "tv-en-2020-smk-s01"
EP1, EP2 = SEASON + "e01", SEASON + "e02"
ALIAS = SEASON + "e03"  # a multi_ep_alias of EP2: the owner lookup walks the whole library
MOVIE = "mov-en-2015-strictresume"
STEM = "Strict.Resume.2015"


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _sha256(data):
    return hashlib.sha256(data).hexdigest()


def _leaf(entry_id, folder, filename, file_hash, **extra):
    """A cmd_prep-shaped leaf."""
    short_id = mvcommon.generate_short_id(entry_id)
    stem, ext = os.path.splitext(filename)
    entry = {
        "short_id": short_id, "filename": filename, "folder_path": str(folder),
        "status": "local_ready", "uploaded": False, "search_term": f"{stem} [{short_id}]{ext}",
        "hash": file_hash, "metadata": main.parse_metadata_from_id(entry_id),
        "tech_spec": {"resolution": "1080p", "video_codec": "HEVC"},
    }
    entry.update(extra)
    return entry


def _chunk_names(stem, short_id, n):
    return [f"{stem} [{short_id}].chunk.{i:03d}.mkv" for i in range(1, n + 1)]


def _split_info(chunk_bytes, **extra):
    """split_info as a real split records it: every chunk with the sha256 of its bytes."""
    info = {"is_split": True, "method": "COUNT", "val": str(len(chunk_bytes)), "total_chunks": len(chunk_bytes),
            "chunks": [{"filename": n, "hash": _sha256(b)} for n, b in chunk_bytes.items()]}
    info.update(extra)
    return info


def _movie(sandbox, make_video, **extra):
    folder = sandbox["local_root"] / "Movies" / "Strict Resume (2015)"
    folder.mkdir(parents=True)
    master, sha256 = make_video(folder / f"{STEM}.mkv")
    mvcommon.save_library({MOVIE: _leaf(MOVIE, folder, f"{STEM}.mkv", sha256, **extra)})
    return folder, master, mvcommon.generate_short_id(MOVIE)


def _season(sandbox, make_video, ep1_extra=None):
    """Two episodes in ONE season folder, so they share one `_parts/`. The season also
    carries a multi_ep_alias, so every refusal here walks the three entry types
    (season_map, leaf, alias) when it looks up a stranger's owner."""
    folder = sandbox["local_root"] / "Series" / "SMK" / "Season 01"
    folder.mkdir(parents=True)
    library = {SEASON: {"type": "season_map", "folder_path": str(folder), "total_episodes": 3,
                        "children": [EP1, EP2, ALIAS]},
               ALIAS: {"type": "multi_ep_alias", "alias_of": EP2, "parent_id": SEASON}}
    masters = {}
    for ep_id, filename in ((EP1, "SMK.S01E01.mkv"), (EP2, "SMK.S01E02.mkv")):
        masters[ep_id], sha256 = make_video(folder / filename, marker=ep_id.encode())
        library[ep_id] = _leaf(ep_id, folder, filename, sha256, parent_id=SEASON)
    library[EP1].update(ep1_extra or {})
    mvcommon.save_library(library)
    return folder, masters


def _interrupted_episode_one(sandbox, make_video, present=(2,)):
    """Episode 1's split push was interrupted: its split_info records two chunks and
    the chunks in `present` still wait in the shared season chunk dir."""
    sid1 = mvcommon.generate_short_id(EP1)
    names = _chunk_names("SMK.S01E01", sid1, 2)
    chunk_bytes = {n: f"ep1-chunk-{i}-bytes".encode() for i, n in enumerate(names, start=1)}
    folder, masters = _season(sandbox, make_video, ep1_extra={"split_info": _split_info(chunk_bytes)})
    parts = folder / main.SPLIT_DIR_NAME
    parts.mkdir()
    for i in present:
        (parts / names[i - 1]).write_bytes(chunk_bytes[names[i - 1]])
    return folder, masters, parts, names, chunk_bytes


def _tree(root):
    return sorted((str(p.relative_to(root)), p.is_dir(), None if p.is_dir() else p.stat().st_size)
                  for p in root.rglob("*"))


def _device_files(device_dir):
    return sorted(p.name for p in device_dir.rglob("*") if p.is_file())


def _device_videos(device_dir):
    return {f.name: f for f in device_dir.rglob("*.mkv")}


def _read(path):
    with open(path, "rb") as f:
        return f.read()


def _assert_journal_recorded_nothing(folder):
    """A refusal comes before every journalled action. cmd_push has already opened
    its journal, so the empty journal every pre-flight refusal leaves may be there."""
    jpath = folder / main.TXN_JOURNAL_NAME
    if jpath.exists():
        data = json.loads(jpath.read_text(encoding="utf-8"))
        assert data["records"] == [] and data["crossed_ponr"] is False


def _install_fake_split(monkeypatch, n_chunks=2):
    """mkvmerge `--split` -> a byte slicer honouring split_video_file's contract; the
    unsplittable-track probe finds nothing; the disk is huge."""
    def fake_split(input_path, output_dir, method, value_str, file_id=""):
        data = _read(input_path)
        step = len(data) // n_chunks + 1
        stem = os.path.splitext(os.path.basename(input_path))[0]
        paths = []
        for i in range(n_chunks):
            p = os.path.join(output_dir, f"{stem} [{file_id}].chunk.{i + 1:03d}.mkv")
            with open(p, "wb") as f:
                f.write(data[i * step:(i + 1) * step])
            paths.append(p)
        return paths

    huge = 500 * 1024 ** 3
    monkeypatch.setattr(main, "split_video_file", fake_split)
    monkeypatch.setattr(main, "find_unsplittable_tracks", lambda path: [])
    monkeypatch.setattr(main.shutil, "disk_usage",
                        lambda path: types.SimpleNamespace(total=huge, used=0, free=huge))


def _fail_first_pushes_of(monkeypatch, needle, times=3):
    """Fail the first `times` `adb push` calls whose argv mentions `needle` (one
    chunk's three retry attempts), then let the device behave again. Wraps whatever
    mock_device installed, so every other call still reaches the fake device."""
    inner = main.subprocess.run
    seen = {"n": 0}

    def run(argv, check=False, **kwargs):
        argv = list(argv)
        if "push" in argv and any(needle in str(a) for a in argv):
            seen["n"] += 1
            if seen["n"] <= times:
                if check:
                    raise subprocess.CalledProcessError(1, argv)
        return inner(argv, check=check, **kwargs)

    monkeypatch.setattr(main.subprocess, "run", run)
    monkeypatch.setattr(mvcommon.time, "sleep", lambda *a, **k: None)


PUSH_CALLS = [
    pytest.param({}, id="plain-push"),
    pytest.param({"split_method": "SIZE_GB", "split_val": "1"}, id="with-split-args"),
    pytest.param({"chunk_range": "1-2"}, id="chunks-range"),
]


# ---------------------------------------------------------------------------
# (1) shape (a): another episode's chunks in the shared season chunk dir
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("call", PUSH_CALLS)
def test_push_refuses_to_resume_another_episodes_chunks(sandbox, mock_device, make_video, capsys, call):
    folder, masters, parts, names, _ = _interrupted_episode_one(sandbox, make_video)
    library_before = mvcommon.load_library()
    parts_before = _tree(parts)

    assert main.cmd_push(EP2, **call) is False, "episode 2 has nothing of its own to resume here"

    assert mvcommon.load_library() == library_before, "episode 2 must not be marked uploaded/onboarded"
    assert _device_files(mock_device) == [], "episode 1's chunk must not be uploaded by episode 2's push"
    assert _tree(parts) == parts_before, "episode 1's waiting chunk is never touched"
    _assert_journal_recorded_nothing(folder)
    out = capsys.readouterr().out
    assert "holds no chunk recorded in its split" in out and str(parts) in out
    assert names[1] in out and f"a recorded chunk of {EP1}" in out, f"the stranger and its owner must be named:\n{out}"
    assert f"push {EP1}" in out, "the safe next step is to resume the owner first"
    assert "SUCCESS" not in out and "Upload Complete" not in out


def test_push_group_never_uploads_one_episodes_chunks_as_another(sandbox, mock_device, make_video, monkeypatch):
    """The cascade push_group made likely: episode 1's chunk 2 fails for the whole of
    its retries, push_group moves on, and the device recovers just in time for
    episode 2. Episode 2 must be refused, not marked onboarded on episode 1's chunk.
    A second run then resumes episode 1 from the shared dir and pushes episode 2."""
    folder, masters, parts, names, chunk_bytes = _interrupted_episode_one(sandbox, make_video, present=(1, 2))
    _fail_first_pushes_of(monkeypatch, "chunk.002", times=3)

    main.cmd_push_group(SEASON)

    library = mvcommon.load_library()
    assert (library[EP1]["status"], library[EP1]["uploaded"]) == ("local_ready", False)
    assert (library[EP2]["status"], library[EP2]["uploaded"]) == ("local_ready", False), \
        "episode 2 was marked onboarded on episode 1's chunk"
    assert set(_device_videos(mock_device)) == {names[0]}
    assert sorted(p.name for p in parts.iterdir()) == [names[1]]

    main.cmd_push_group(SEASON)

    library = mvcommon.load_library()
    for ep_id in (EP1, EP2):
        assert (library[ep_id]["status"], library[ep_id]["uploaded"]) == ("onboarded", True)
    on_device = _device_videos(mock_device)
    assert set(on_device) == set(names) | {library[EP2]["search_term"]}
    assert on_device[names[1]].read_bytes() == chunk_bytes[names[1]]
    assert on_device[library[EP2]["search_term"]].read_bytes() == _read(masters[EP2])
    assert not parts.exists()


# ---------------------------------------------------------------------------
# (2) shape (b): chunks that no split_info records
# ---------------------------------------------------------------------------

UNRECORDED = {
    "lone-flac-holder": lambda sid: {f"{STEM} [{sid}].holder.mkv": b"holder-bytes"},
    "partial-chunk-of-a-failed-split": lambda sid: {f"{STEM} [{sid}].chunk.001.mkv": b"truncated"},
    "complete-looking-chunks": lambda sid: {n: f"chunk-{i}".encode() for i, n in
                                            enumerate(_chunk_names(STEM, sid, 2), start=1)},
}


@pytest.mark.parametrize("call", PUSH_CALLS)
@pytest.mark.parametrize("leftover", sorted(UNRECORDED))
def test_push_refuses_chunks_that_no_split_info_records(sandbox, mock_device, make_video, capsys, leftover, call):
    folder, master, short_id = _movie(sandbox, make_video)  # no split_info at all
    parts = folder / main.SPLIT_DIR_NAME
    parts.mkdir()
    files = UNRECORDED[leftover](short_id)
    for name, data in files.items():
        (parts / name).write_bytes(data)
    library_before = mvcommon.load_library()
    parts_before = _tree(parts)
    master_bytes = _read(master)

    assert main.cmd_push(MOVIE, **call) is False, "an unrecorded chunk is not a resume"

    assert mvcommon.load_library() == library_before
    assert _device_files(mock_device) == []
    assert _tree(parts) == parts_before
    assert _read(master) == master_bytes
    _assert_journal_recorded_nothing(folder)
    out = capsys.readouterr().out
    assert "holds no chunk recorded in its split" in out
    for name in files:
        assert name in out
    assert "tagged for this entry, which has no recorded split" in out, \
        f"the refusal must say why these are not a resume:\n{out}"
    resplit = f"{call['split_method']} {call['split_val']}" if "split_method" in call else "SIZE_GB <n>"
    assert f"push {MOVIE} {resplit}" in out, "the safe next step is a fresh split of the intact master"


def test_season_resume_command_no_longer_archives_an_episode_whose_split_record_is_gone(
        sandbox, mock_device, make_video, stub_tech_specs, fake_dummy, capsys):
    """The real route to shape (b). The season autopilot prints `prep_push_rep_season …
    episodes N-M` after an interrupted split push. That command re-preps the episode,
    and cmd_prep rebuilds the entry without its split_info. Before the fix the push
    then "resumed" the leftover chunk and `replace` dummied the master: an archived
    entry with no split record."""
    folder, masters, parts, names, _ = _interrupted_episode_one(sandbox, make_video)
    master_bytes = _read(masters[EP1])

    main.cmd_prep_push_rep_season(SEASON, str(folder), episode_range="1-1")

    stored = mvcommon.load_library()[EP1]
    assert (stored["status"], stored["uploaded"]) == ("local_ready", False)
    assert _read(masters[EP1]) == master_bytes, "the master was dummied although its split record was gone"
    assert _device_files(mock_device) == []
    assert sorted(p.name for p in parts.iterdir()) == [names[1]]
    out = capsys.readouterr().out
    assert "which has no recorded split" in out
    assert "Resume the rest of the season:" in out, "the autopilot stops with its usual resume line"


# ---------------------------------------------------------------------------
# (3) a recorded chunk whose bytes changed
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("damaged", ["chunk", "holder"])
def test_push_refuses_a_recorded_file_whose_bytes_no_longer_match(sandbox, mock_device, make_video, capsys, damaged):
    """Names are not enough: a partial or altered chunk under a recorded name must
    never be uploaded. Verification runs before the first upload, so nothing is sent."""
    short_id = mvcommon.generate_short_id(MOVIE)
    names = _chunk_names(STEM, short_id, 2)
    chunk_bytes = {n: f"chunk-{i}-bytes".encode() for i, n in enumerate(names, start=1)}
    holder = f"{STEM} [{short_id}].holder.mkv"
    info = _split_info(chunk_bytes, carried_out_tracks=[{"holder_filename": holder,
                                                          "holder_hash": _sha256(b"holder-bytes")}])
    folder, master, _ = _movie(sandbox, make_video, split_info=info)
    parts = folder / main.SPLIT_DIR_NAME
    parts.mkdir()
    for name, data in chunk_bytes.items():
        (parts / name).write_bytes(data)
    (parts / holder).write_bytes(b"holder-bytes")
    victim = names[1] if damaged == "chunk" else holder
    (parts / victim).write_bytes(b"truncated")
    library_before = mvcommon.load_library()
    parts_before = _tree(parts)

    assert main.cmd_push(MOVIE) is False

    assert mvcommon.load_library() == library_before
    assert _device_files(mock_device) == [], "not even the intact chunks may go up before the set is verified"
    assert _tree(parts) == parts_before
    _assert_journal_recorded_nothing(folder)
    out = capsys.readouterr().out
    assert "no longer match the hash its split recorded" in out and victim in out


# ---------------------------------------------------------------------------
# (4) recorded chunks beside strangers: upload ours, leave and list the rest
# ---------------------------------------------------------------------------

def test_resume_uploads_only_its_recorded_chunks_and_names_each_stranger(sandbox, mock_device, make_video, capsys):
    """Episode 2 resumes its own two recorded chunks. Episode 1's waiting chunk, an
    unrecorded chunk tagged for episode 2, a chunk tagged for nobody in the library
    and a stray note stay where they are, and each is named with its likely owner."""
    folder, masters, parts, ep1_names, _ = _interrupted_episode_one(sandbox, make_video)
    sid2 = mvcommon.generate_short_id(EP2)
    names = _chunk_names("SMK.S01E02", sid2, 2)
    chunk_bytes = {n: f"ep2-chunk-{i}-bytes".encode() for i, n in enumerate(names, start=1)}
    library = mvcommon.load_library()
    library[EP2]["split_info"] = _split_info(chunk_bytes)
    mvcommon.save_library(library)
    for name, data in chunk_bytes.items():
        (parts / name).write_bytes(data)
    strangers = {
        ep1_names[1]: f"a recorded chunk of {EP1}",
        f"SMK.S01E02 [{sid2}].chunk.003.mkv": "tagged for this entry, but not in its recorded split",
        "Other [ffffff].chunk.001.mkv": "tagged [ffffff], but no library entry has that id",
        "notes.txt": "no entry tag",
    }
    for name in list(strangers)[1:]:
        (parts / name).write_bytes(b"stranger")

    assert main.cmd_push(EP2) is True

    on_device = _device_videos(mock_device)
    assert set(on_device) == set(names), f"only episode 2's recorded chunks may be uploaded: {sorted(on_device)}"
    for name in names:
        assert on_device[name].read_bytes() == chunk_bytes[name]
    assert sorted(p.name for p in parts.iterdir()) == sorted(strangers), "strangers are left in place"
    library = mvcommon.load_library()
    assert (library[EP2]["status"], library[EP2]["uploaded"]) == ("onboarded", True)
    assert (library[EP1]["status"], library[EP1]["uploaded"]) == ("local_ready", False)
    out = capsys.readouterr().out
    for name, note in strangers.items():
        assert f"{name}  ({note})" in out, f"missing stranger line for {name}:\n{out}"


# ---------------------------------------------------------------------------
# (5) every genuine resume still resumes
# ---------------------------------------------------------------------------

def test_an_interrupted_first_push_resumes_from_the_split_it_recorded(sandbox, mock_device, make_video, monkeypatch):
    """split_info is saved BEFORE the first upload, so a push interrupted after one
    chunk leaves a recorded split and the next `push <id>` resumes the rest."""
    _install_fake_split(monkeypatch)
    folder, master, short_id = _movie(sandbox, make_video)
    names = _chunk_names(STEM, short_id, 2)
    _fail_first_pushes_of(monkeypatch, "chunk.002", times=3)

    assert main.cmd_push(MOVIE, "COUNT", "2") is False, "chunk 2 failed all three attempts"

    stored = mvcommon.load_library()[MOVIE]
    assert [c["filename"] for c in stored["split_info"]["chunks"]] == names, "the split is recorded before any upload"
    assert (stored["status"], stored["uploaded"]) == ("local_ready", False)
    parts = folder / main.SPLIT_DIR_NAME
    assert set(_device_videos(mock_device)) == {names[0]}
    assert sorted(p.name for p in parts.iterdir()) == [names[1]]

    assert main.cmd_push(MOVIE) is True

    on_device = _device_videos(mock_device)
    assert set(on_device) == set(names)
    stored = mvcommon.load_library()[MOVIE]
    assert [c["hash"] for c in stored["split_info"]["chunks"]] == [_sha256(on_device[n].read_bytes()) for n in names]
    assert b"".join(on_device[n].read_bytes() for n in names) == _read(master)
    assert (stored["status"], stored["uploaded"]) == ("onboarded", True)
    assert not parts.exists()


def test_push_first_then_the_season_command_finishes_an_interrupted_episode(
        sandbox, mock_device, make_video, stub_tech_specs, fake_dummy):
    """The safe way back after an interrupted split push in a season: `push <id>`
    first, which resumes from the recorded split, then the season command for the
    rest. The resumed episode keeps its split record and is archived; the season
    command then leaves it alone (already pushed) and carries on."""
    folder, masters, parts, names, chunk_bytes = _interrupted_episode_one(sandbox, make_video)

    assert main.cmd_push(EP1) is True
    main.cmd_prep_push_rep_season(SEASON, str(folder), episode_range="1-2")

    library = mvcommon.load_library()
    assert [(library[e]["status"], library[e]["uploaded"]) for e in (EP1, EP2)] == [("archived", True)] * 2
    assert [c["filename"] for c in library[EP1]["split_info"]["chunks"]] == names, "the split record must survive"
    on_device = _device_videos(mock_device)
    # chunk 1 went up in the interrupted run, before this test's device existed
    assert set(on_device) == {names[1], library[EP2]["search_term"]}
    assert on_device[names[1]].read_bytes() == chunk_bytes[names[1]]
    assert not parts.exists()


def test_chunks_range_resume_verifies_and_uploads_only_its_range(sandbox, mock_device, make_video, monkeypatch, capsys):
    """Range pushes resume from the recorded split too. Each one verifies exactly the
    chunks it uploads: a damaged chunk outside the range does not stop a range that
    leaves it alone, and it is refused when its own range comes."""
    _install_fake_split(monkeypatch, n_chunks=3)
    folder, master, short_id = _movie(sandbox, make_video)
    names = _chunk_names(STEM, short_id, 3)
    parts = folder / main.SPLIT_DIR_NAME

    assert main.cmd_push(MOVIE, "COUNT", "3", chunk_range="1-1") is True
    assert set(_device_videos(mock_device)) == {names[0]}
    (parts / names[2]).write_bytes(b"truncated")

    assert main.cmd_push(MOVIE, chunk_range="2-2") is True
    assert set(_device_videos(mock_device)) == {names[0], names[1]}

    capsys.readouterr()
    assert main.cmd_push(MOVIE, chunk_range="3-3") is False
    assert set(_device_videos(mock_device)) == {names[0], names[1]}
    assert "no longer match the hash its split recorded" in capsys.readouterr().out
    stored = mvcommon.load_library()[MOVIE]
    assert (stored["status"], stored["uploaded"]) == ("local_ready", False), "a range push never marks onboarded"


def test_resume_uploads_a_recorded_flac_holder_beside_its_chunk(sandbox, mock_device, make_video):
    short_id = mvcommon.generate_short_id(MOVIE)
    names = _chunk_names(STEM, short_id, 2)
    chunk_bytes = {n: f"chunk-{i}-bytes".encode() for i, n in enumerate(names, start=1)}
    holder = f"{STEM} [{short_id}].holder.mkv"
    info = _split_info(chunk_bytes, carried_out_tracks=[{"holder_filename": holder,
                                                          "holder_hash": _sha256(b"holder-bytes")}])
    folder, master, _ = _movie(sandbox, make_video, split_info=info)
    parts = folder / main.SPLIT_DIR_NAME
    parts.mkdir()
    (parts / names[1]).write_bytes(chunk_bytes[names[1]])
    (parts / holder).write_bytes(b"holder-bytes")

    assert main.cmd_push(MOVIE) is True

    on_device = _device_videos(mock_device)
    assert set(on_device) == {names[1], holder}
    assert on_device[holder].read_bytes() == b"holder-bytes"
    assert not parts.exists()
    stored = mvcommon.load_library()[MOVIE]
    assert (stored["status"], stored["uploaded"]) == ("onboarded", True)


def test_resume_from_a_redirected_chunk_dir(sandbox, mock_device, make_video, monkeypatch, tmp_path):
    """tempdir: the chunk dir is <tempdir>/<id>/_parts. A push interrupted there
    resumes from there when the same tempdir is given again."""
    _install_fake_split(monkeypatch)
    folder, master, short_id = _movie(sandbox, make_video)
    names = _chunk_names(STEM, short_id, 2)
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    parts = scratch / MOVIE / main.SPLIT_DIR_NAME
    _fail_first_pushes_of(monkeypatch, "chunk.002", times=3)

    assert main.cmd_push(MOVIE, "COUNT", "2", temp_dir=str(scratch)) is False
    assert sorted(p.name for p in parts.iterdir()) == [names[1]]

    assert main.cmd_push(MOVIE, temp_dir=str(scratch)) is True

    on_device = _device_videos(mock_device)
    assert set(on_device) == set(names)
    assert b"".join(on_device[n].read_bytes() for n in names) == _read(master)
    assert not parts.exists()
    assert not (folder / main.SPLIT_DIR_NAME).exists(), "a redirected push never creates <title>/_parts"
    stored = mvcommon.load_library()[MOVIE]
    assert (stored["status"], stored["uploaded"]) == ("onboarded", True)


def test_resume_accepts_a_recorded_chunk_that_has_no_recorded_hash(sandbox, mock_device, make_video):
    """Hashes are checked where recorded. A split_info without chunk hashes can only
    be matched by name."""
    short_id = mvcommon.generate_short_id(MOVIE)
    names = _chunk_names(STEM, short_id, 2)
    info = {"is_split": True, "method": "COUNT", "val": "2", "total_chunks": 2,
            "chunks": [{"filename": n} for n in names]}
    folder, master, _ = _movie(sandbox, make_video, split_info=info)
    parts = folder / main.SPLIT_DIR_NAME
    parts.mkdir()
    (parts / names[1]).write_bytes(b"chunk-2-bytes")

    assert main.cmd_push(MOVIE) is True

    assert set(_device_videos(mock_device)) == {names[1]}
    stored = mvcommon.load_library()[MOVIE]
    assert (stored["status"], stored["uploaded"]) == ("onboarded", True)


# ---------------------------------------------------------------------------
# (6) extras: the same rule on the per-item chunk dir (<extra folder>/_parts/<short_id>)
# ---------------------------------------------------------------------------

@pytest.fixture()
def plenty_of_disk(monkeypatch):
    huge = 500 * 1024 ** 3
    monkeypatch.setattr(main.shutil, "disk_usage",
                        lambda path: types.SimpleNamespace(total=huge, used=0, free=huge))


def _extras_items(title_id):
    return {it["filename"]: it for it in
            mvcommon.load_library()[title_id]["extras"]["groups"]["Specials"]["items"]}


def test_an_interrupted_extras_push_resumes_its_recorded_chunks(
        sandbox_extras, mock_device, plenty_of_disk, monkeypatch):
    _install_fake_split(monkeypatch)
    title_id = sandbox_extras["title_id"]
    bts = next(it for it in sandbox_extras["items"] if it["filename"] == "BTS.mkv")
    bts_names = _chunk_names("BTS", bts["short_id"], 2)
    _fail_first_pushes_of(monkeypatch, f"BTS [{bts['short_id']}].chunk.002", times=3)

    assert main.push_title_extras(mvcommon.load_library(), title_id, ("COUNT", "2")) is False

    items = _extras_items(title_id)
    assert (items["BTS.mkv"]["uploaded"], items["Trailer.mkv"]["uploaded"]) == (False, True)
    assert [c["filename"] for c in items["BTS.mkv"]["split_info"]["chunks"]] == bts_names
    item_parts = sandbox_extras["extras_dir"] / main.SPLIT_DIR_NAME / bts["short_id"]
    assert sorted(p.name for p in item_parts.iterdir()) == [bts_names[1]]

    assert main.push_title_extras(mvcommon.load_library(), title_id, ("COUNT", "2")) is True

    on_device = _device_videos(mock_device)
    assert set(bts_names) <= set(on_device) and len(on_device) == 4
    assert b"".join(on_device[n].read_bytes() for n in bts_names) == _read(bts["path"])
    items = _extras_items(title_id)
    assert (items["BTS.mkv"]["uploaded"], items["BTS.mkv"]["status"]) == (True, "onboarded")
    assert not item_parts.exists()


@pytest.mark.parametrize("shape", ["unrecorded-chunk", "recorded-chunk-with-changed-bytes"])
def test_extras_resume_refuses_what_its_split_info_does_not_vouch_for(
        sandbox_extras, mock_device, plenty_of_disk, capsys, shape):
    """The per-item chunk dir can hold a failed split's chunk that no split_info
    records, or a recorded chunk whose bytes changed. Neither is uploaded, and the
    item is not marked onboarded."""
    title_id = sandbox_extras["title_id"]
    bts = next(it for it in sandbox_extras["items"] if it["filename"] == "BTS.mkv")
    name = _chunk_names("BTS", bts["short_id"], 1)[0]
    item_parts = sandbox_extras["extras_dir"] / main.SPLIT_DIR_NAME / bts["short_id"]
    item_parts.mkdir(parents=True)
    (item_parts / name).write_bytes(b"truncated")
    library = mvcommon.load_library()
    if shape == "recorded-chunk-with-changed-bytes":
        stored = next(it for it in library[title_id]["extras"]["groups"]["Specials"]["items"]
                      if it["filename"] == "BTS.mkv")
        stored["split_info"] = _split_info({name: b"the-bytes-the-split-recorded"})
        mvcommon.save_library(library)
    parts_before = _tree(item_parts)

    assert main.push_title_extras(mvcommon.load_library(), title_id, ("NONE", None)) is False

    on_device = _device_videos(mock_device)
    assert name not in on_device, "the unvouched chunk reached the device"
    items = _extras_items(title_id)
    assert (items["BTS.mkv"]["uploaded"], items["BTS.mkv"]["status"]) == (False, "local_ready")
    assert (items["Trailer.mkv"]["uploaded"], items["Trailer.mkv"]["status"]) == (True, "onboarded"), \
        "one refused extra must not block the others"
    assert _tree(item_parts) == parts_before
    out = capsys.readouterr().out
    assert name in out and str(item_parts) in out
    expected = ("tagged for this extra, which has no recorded split" if shape == "unrecorded-chunk"
                else "no longer match the hash its split recorded")
    assert expected in out, out
