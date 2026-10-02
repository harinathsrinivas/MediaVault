"""IMP-C30 — a file is a chunk because its name ENDS like one, never because it
contains ".chunk.".

split_video_file writes every chunk as "<stem>[ [<short_id>]].chunk.NNN.mkv". Two
read-only walkers, cmd_scan_unprepped and the reclaim scan collect_reclaimable,
skipped any video whose name merely CONTAINED ".chunk.". Push's `chunks N-M`
filter numbered a chunk by the FIRST ".chunk.<digits>." in its name. So a real,
unprepped video such as `the.chunk.2019.1080p.web.h264.mkv` vanished from both
reports, and every chunk of that title was numbered 2019, so `push <id> chunks 1-1`
refused with "No chunks found in range". The old test was case-sensitive:
"The.Chunk.…" was never affected, but lower-case release names were.

The single rule is now mvcommon.chunk_index(name): the number in a name that ENDS
in ".chunk.<digits>.mkv" (case-sensitive, exactly what split_video_file's own
listing accepts), else None.

Fixtures (docs/testing-strategy.md §4): `sandbox` (dual LIBRARY_*/LOCAL_ROOT patch +
the C:\\Media hard-guard), `make_video` (> DUMMY_MAX_BYTES, so a file reads as real
media), `mock_device` for the push. Device lookups index by `.name` (§8.1).
"""
import hashlib
import os

import pytest

import main
import mvcommon

# Real videos whose names CONTAIN ".chunk." without being chunks.
LOOKALIKE_VIDEOS = [
    pytest.param("the.chunk.2019.1080p.web.h264.mkv", id="scene-name-with-a-year"),
    pytest.param("a.big.chunk.of.time.2004.mp4", id="title-word-in-an-mp4"),
]


def _movies_folder(sandbox, name):
    folder = sandbox["local_root"] / "Movies" / name
    folder.mkdir(parents=True)
    return folder


# ---------------------------------------------------------------------------
# (1) the reproduction: both walkers hid a real, unprepped video
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("filename", LOOKALIKE_VIDEOS)
def test_scan_unprepped_reports_a_real_video_named_like_a_chunk(sandbox, make_video, capsys, filename):
    make_video(_movies_folder(sandbox, "Lookalike") / filename)
    mvcommon.save_library({})

    main.cmd_scan_unprepped()

    out = capsys.readouterr().out
    assert filename in out, f"an unprepped real video was silently skipped:\n{out}"


@pytest.mark.parametrize("filename", LOOKALIKE_VIDEOS)
def test_reclaim_scan_reports_a_real_video_named_like_a_chunk(sandbox, make_video, filename):
    make_video(_movies_folder(sandbox, "Lookalike") / filename)
    mvcommon.save_library({})

    rows = [it for it in main.collect_reclaimable()["items"]
            if os.path.basename(it["path"]) == filename]

    assert [r["badge"] for r in rows] == ["UNPREPPED"], \
        f"the reclaim scan must list the real video exactly once, as UNPREPPED: {rows}"


def test_chunks_range_push_numbers_each_chunk_by_its_own_suffix(sandbox, mock_device, make_video):
    """A resumed `chunks N-M` push of a title whose name contains ".chunk.2019."
    must number its chunks 1 and 2, not 2019 and 2019."""
    entry_id = "mov-en-2019-thechunk"
    filename = "the.chunk.2019.1080p.web.h264.mkv"
    folder = _movies_folder(sandbox, "The Chunk (2019)")
    _, sha256 = make_video(folder / filename)
    short_id = mvcommon.generate_short_id(entry_id)
    stem, ext = os.path.splitext(filename)
    parts = folder / main.SPLIT_DIR_NAME
    parts.mkdir()
    names = [f"{stem} [{short_id}].chunk.00{i}.mkv" for i in (1, 2)]
    chunk_bytes = {name: f"chunk-{i}-bytes".encode() for i, name in enumerate(names, start=1)}
    for name, data in chunk_bytes.items():
        (parts / name).write_bytes(data)
    mvcommon.save_library({entry_id: {
        "short_id": short_id, "filename": filename, "folder_path": str(folder),
        "status": "local_ready", "uploaded": False, "search_term": f"{stem} [{short_id}]{ext}",
        "hash": sha256, "metadata": main.parse_metadata_from_id(entry_id),
        "tech_spec": {"resolution": "1080p", "video_codec": "HEVC"},
        # real chunk hashes, as a split records them: a resume checks the bytes against them (IMP-C32)
        "split_info": {"is_split": True, "method": "COUNT", "val": "2", "total_chunks": 2,
                       "chunks": [{"filename": n, "hash": hashlib.sha256(b).hexdigest()}
                                  for n, b in chunk_bytes.items()]},
    }})

    assert main.cmd_push(entry_id, chunk_range="1-1") is True
    assert {f.name for f in mock_device.rglob("*.mkv")} == {names[0]}, "chunk 1, and only chunk 1"
    assert sorted(p.name for p in parts.iterdir()) == [names[1]], "chunk 2 waits for its own range"

    assert main.cmd_push(entry_id, chunk_range="2-2") is True
    assert {f.name for f in mock_device.rglob("*.mkv")} == set(names)
    assert not parts.exists(), "the emptied chunk dir is removed"
    stored = mvcommon.load_library()[entry_id]
    assert (stored["uploaded"], stored["status"]) == (False, "local_ready"), \
        "a range push never marks the entry onboarded"


# ---------------------------------------------------------------------------
# (2) regression pins: real chunks are still skipped, wherever they lie
# ---------------------------------------------------------------------------

def test_both_walkers_still_skip_real_chunks_inside_and_outside_the_chunk_dir(sandbox, make_video, capsys):
    """A tagged chunk and an untagged (no short_id) chunk lying loose in a title
    folder, plus one inside the pruned chunk dir: none of them is a video to report."""
    folder = _movies_folder(sandbox, "Stray")
    for name in ("Movie [ab12cd].chunk.001.mkv", "movie.chunk.012.mkv"):
        make_video(folder / name)
    parts = folder / main.SPLIT_DIR_NAME
    parts.mkdir()
    make_video(parts / "Movie [ab12cd].chunk.002.mkv")
    mvcommon.save_library({})

    main.cmd_scan_unprepped()
    out = capsys.readouterr().out
    assert ".chunk." not in out, f"a real chunk was reported as an unprepped video:\n{out}"
    assert "No unprepped video files found for Movies" in out

    assert main.collect_reclaimable()["items"] == [], "a real chunk is not reclaimable media"


# ---------------------------------------------------------------------------
# (3) the shared rule itself
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("name, expected", [
    ("Movie [ab12cd].chunk.001.mkv", 1),
    ("movie.chunk.012.mkv", 12),                                # untagged (no short_id)
    ("movie.chunk.1000.mkv", 1000),                             # %03d widens past 999
    ("the.chunk.2019.1080p [ab12cd].chunk.002.mkv", 2),         # the END decides, not the first match
    ("the.chunk.2019.1080p.web.h264.mkv", None),
    ("Movie [ab12cd].holder.mkv", None),                        # FLAC holder: uploaded beside chunks
    ("Movie [ab12cd].flac", None),
    ("Movie [ab12cd].chunk.001.mkv.partial", None),
    ("Movie.chunk.001.mp4", None),                              # chunks are always .mkv
    ("Movie.CHUNK.001.MKV", None),                              # case-sensitive, like the producer
])
def test_chunk_index_reads_only_a_trailing_chunk_suffix(name, expected):
    assert mvcommon.chunk_index(name) == expected


def test_chunk_rule_agrees_with_what_split_video_file_returns(tmp_path, monkeypatch):
    """Drift pin: the walkers' rule and the producer's own listing must accept the
    SAME files. Drive the real split_video_file with mkvmerge stubbed to write the
    chunks its `-o` pattern names, plus decoys, into the output dir."""
    src = tmp_path / "the.chunk.2019.1080p.mkv"
    src.write_bytes(b"x" * 4096)
    out = tmp_path / "chunks"
    out.mkdir()
    decoys = ["the.chunk.2019.1080p [ab12cd].holder.mkv", "notes.txt", "the.chunk.2019.1080p.mkv",
              "X [ab12cd].chunk.009.mkv.partial", "Y.CHUNK.004.MKV", "Z.chunk.005.mp4"]

    def fake_mkvmerge(cmd, **kwargs):
        pattern = cmd[cmd.index("-o") + 1].replace("{{", "{").replace("}}", "}")
        for i in (1, 2):
            open(pattern.replace("%03d", f"{i:03d}"), "wb").close()
        for name in decoys:
            (out / name).write_bytes(b"d")

        class _R:
            returncode, stdout, stderr = 0, "", ""
        return _R()

    monkeypatch.setattr(main.subprocess, "run", fake_mkvmerge)

    produced = {os.path.basename(p) for p in
                main.split_video_file(str(src), str(out), "COUNT", "2", file_id="ab12cd")}

    assert produced == {f"the.chunk.2019.1080p [ab12cd].chunk.00{i}.mkv" for i in (1, 2)}
    assert {n for n in os.listdir(out) if mvcommon.chunk_index(n) is not None} == produced
