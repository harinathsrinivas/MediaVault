"""IMP-C21: the manual remux tool.

`tools/remux_unsplittable.py` is the fix for a file mkvmerge cannot split. It is kept
deliberately separate from the pipeline: MediaVault detects and refuses, the
operator decides and runs this. The tests below pin both the mechanics and that
separation — the latter is a standing user decision, not an implementation
detail (see docs/edge-case-unsplittable-tracks/CODE-GAPS.md Gap 3).

CI-safe: no ffmpeg or mkvmerge needed.
"""
import io
import os

import pytest

import main as mv
from tools import remux_unsplittable as ru

VIDEO = {"index": 0, "codec_type": "video", "codec_name": "hevc"}
DTS = {"index": 1, "codec_type": "audio", "codec_name": "dts", "tags": {"language": "kor"}}
AC3_A = {"index": 2, "codec_type": "audio", "codec_name": "ac3", "tags": {"language": "kor"}}
AC3_B = {"index": 3, "codec_type": "audio", "codec_name": "ac3", "tags": {"language": "kor"}}
FLAC = {"index": 4, "codec_type": "audio", "codec_name": "flac", "tags": {"language": "ita"}}
SUB = {"index": 5, "codec_type": "subtitle", "codec_name": "hdmv_pgs_subtitle"}
INCIDENT = [VIDEO, DTS, AC3_A, AC3_B, FLAC, SUB]


# --------------------------------------------------------------------------
# the index trap
# --------------------------------------------------------------------------

def test_audio_index_is_position_among_audio_streams_only():
    """The incident file's FLAC track is overall stream 4 but the FOURTH audio
    stream is index 3. Using the overall index would have transcoded a subtitle;
    using 1 would have destroyed the DTS-HD MA main track."""
    assert ru.audio_index_for(INCIDENT, 4) == 3
    assert ru.audio_index_for(INCIDENT, 1) == 0
    assert ru.audio_index_for(INCIDENT, 0) is None, "video is not an audio stream"
    assert ru.audio_index_for(INCIDENT, 99) is None


def test_build_command_converts_only_the_named_track():
    cmd = ru.build_command("ffmpeg", "in.mkv", "out.mkv", (4, 3), "wavpack")
    assert "-map" in cmd and "0" in cmd
    assert cmd[cmd.index("-c:a:3") + 1] == "wavpack"
    assert "-c" in cmd and cmd[cmd.index("-c") + 1] == "copy", "everything else copied"
    assert cmd[-1] == "out.mkv"


def test_build_command_drop_removes_the_stream():
    cmd = ru.build_command("ffmpeg", "in.mkv", "out.mkv", (4, 3), "drop")
    assert "-0:4" in cmd, "the dropped stream is negatively mapped"
    assert not any(a.startswith("-c:a:") for a in cmd), "nothing is re-encoded"


# --------------------------------------------------------------------------
# the standing user decision
# --------------------------------------------------------------------------

def test_no_lossy_conversion_targets_are_offered():
    """The tool converts losslessly or drops. It must never quietly degrade audio;
    a lossy choice is the operator's to make with ffmpeg directly."""
    assert set(ru.CODECS) == {"wavpack", "pcm", "drop"}
    assert ru.CODECS["drop"][0] is None
    for key in ("wavpack", "pcm"):
        assert ru.CODECS[key][0] in ("wavpack", "pcm_s16le")


def test_mediavault_never_invokes_this_tool():
    """MediaVault detects and refuses; it must not reach for the fix itself. If
    this ever fails, the automatic-conversion line has been crossed."""
    for module in ("main.py", "mainfetch.py", "mvcommon.py"):
        src = io.open(module, encoding="utf-8").read()
        assert "remux_unsplittable" not in src, (
            f"{module} references the remux tool — MediaVault must never call it"
        )


# --------------------------------------------------------------------------
# CLI behaviour
# --------------------------------------------------------------------------

@pytest.fixture()
def stubbed(tmp_path, monkeypatch):
    src = tmp_path / "movie.mkv"
    src.write_bytes(b"m" * 4096)
    monkeypatch.setattr(ru, "MKVMERGE_PATH", str(src))       # "exists"
    monkeypatch.setattr(ru.mv, "resolve_ffmpeg", lambda: "ffmpeg")
    monkeypatch.setattr(ru.mv, "find_unsplittable_tracks", lambda p: [(4, "A_FLAC", "ita")])
    monkeypatch.setattr(ru, "probe_streams", lambda p: INCIDENT)
    ran = []
    monkeypatch.setattr(ru.subprocess, "run", lambda *a, **k: ran.append(a) or pytest.fail(
        "subprocess.run must not be reached in a dry run"))
    return {"src": src, "ran": ran}


def test_dry_run_is_the_default_and_changes_nothing(stubbed, capsys):
    rc = ru.main_cli([str(stubbed["src"])])

    assert rc == 0
    out = capsys.readouterr().out
    assert "DRY RUN" in out
    assert "-c:a:3" in out, "the exact command must be shown for inspection"
    assert not list(stubbed["src"].parent.glob("*.remux.mkv")), "no output written"


def test_refuses_to_overwrite_an_existing_output(stubbed, capsys):
    (stubbed["src"].parent / "movie.remux.mkv").write_bytes(b"pre-existing")

    rc = ru.main_cli([str(stubbed["src"]), "--run"])

    assert rc == 2
    assert "refusing to overwrite" in capsys.readouterr().out
    assert (stubbed["src"].parent / "movie.remux.mkv").read_bytes() == b"pre-existing"


def test_reports_nothing_to_do_for_a_splittable_file(stubbed, monkeypatch, capsys):
    monkeypatch.setattr(ru.mv, "find_unsplittable_tracks", lambda p: [])

    rc = ru.main_cli([str(stubbed["src"])])

    assert rc == 0
    assert "Nothing to do" in capsys.readouterr().out


def test_refuses_when_several_tracks_are_unsplittable(stubbed, monkeypatch, capsys):
    """Two unsplittable tracks is two separate decisions; the tool will not make
    them in a batch."""
    monkeypatch.setattr(ru.mv, "find_unsplittable_tracks",
                        lambda p: [(4, "A_FLAC", "ita"), (5, "A_FLAC", "fre")])

    rc = ru.main_cli([str(stubbed["src"]), "--run"])

    assert rc == 3
    out = capsys.readouterr().out
    assert "2 unsplittable tracks" in out and "one track at a time" in out


def test_refuses_when_mkvmerge_and_ffprobe_disagree(stubbed, monkeypatch, capsys):
    """mkvmerge flags a track index ffprobe does not see as audio — refuse rather
    than convert a guessed stream."""
    monkeypatch.setattr(ru, "probe_streams", lambda p: [VIDEO, DTS])

    rc = ru.main_cli([str(stubbed["src"]), "--run"])

    assert rc == 3
    assert "Refusing to guess" in capsys.readouterr().out


def test_missing_file_is_reported(tmp_path, capsys):
    rc = ru.main_cli([str(tmp_path / "nope.mkv")])
    assert rc == 2
    assert "No such file" in capsys.readouterr().out
