"""IMP-C19 regression: mkvmerge's own error text must reach the operator.

mkvmerge writes `Error:`/`Warning:` lines to STDOUT, not stderr. `split_video_file`
used to run it with `stdout=subprocess.DEVNULL` and, although it captured stderr,
never printed it — so on 2026-08-24 a FLAC track that mkvmerge simply cannot split
surfaced as a bare `returned non-zero exit status 2`, after a full 62 GB prep had
already run. `merge_video_files` had the same defect and is worse: a merge failure
happens during restore, when the chunks are the only copy.

Both must now echo mkvmerge's diagnosis. See `docs/edge-case-unsplittable-tracks/`.

CI-safe: subprocess.run is stubbed; no real mkvmerge needed.
"""
import subprocess

import main

# The real message mkvmerge v97.0 emitted for the incident file (track 4 = A_FLAC).
FLAC_ERROR = (
    "Error: The track ID 4 from the file 'A.Tale.of.Two.Sisters.mkv' cannot be "
    "split. Splitting tracks of this type is not supported."
)


def _raise_mkvmerge(stdout="", stderr=""):
    """Stub for subprocess.run that fails the way mkvmerge fails."""

    def _run(cmd, **kwargs):
        raise subprocess.CalledProcessError(2, cmd, output=stdout, stderr=stderr)

    return _run


def test_split_prints_mkvmerge_error_from_stdout(tmp_path, monkeypatch, capsys):
    parts = tmp_path / "_parts"
    parts.mkdir()
    src = tmp_path / "movie.mkv"
    src.write_bytes(b"\x00" * (3 * 1024 * 1024))

    monkeypatch.setattr(
        main.subprocess, "run",
        _raise_mkvmerge(stdout="mkvmerge v97.0\n" + FLAC_ERROR + "\n"),
    )

    chunks = main.split_video_file(str(src), str(parts), "SIZE_MB", "2", file_id="abcd")

    assert chunks == []
    out = capsys.readouterr().out
    assert "cannot be split" in out, "mkvmerge's reason must be shown, not swallowed"
    assert "track ID 4" in out, "the offending track must be named"


def test_merge_prints_mkvmerge_error_from_stdout(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(
        main.subprocess, "run",
        _raise_mkvmerge(stdout="Error: The file 'chunk.002.mkv' could not be opened.\n"),
    )

    ok = main.merge_video_files(
        [str(tmp_path / "chunk.001.mkv"), str(tmp_path / "chunk.002.mkv")],
        str(tmp_path / "merged.mkv"),
    )

    assert ok is False
    assert "could not be opened" in capsys.readouterr().out


def test_falls_back_to_output_tail_when_no_error_line(tmp_path, monkeypatch, capsys):
    """The libfmt crash (exit 3) writes no `Error:` line — it dies on stderr with
    `terminate called ... fmt::v11::format_error`. That tail must still surface."""
    parts = tmp_path / "_parts"
    parts.mkdir()
    src = tmp_path / "movie.mkv"
    src.write_bytes(b"\x00" * (3 * 1024 * 1024))

    monkeypatch.setattr(
        main.subprocess, "run",
        _raise_mkvmerge(
            stdout="mkvmerge v97.0\n",
            stderr="terminate called after throwing an instance of 'fmt::v11::format_error'\n"
                   "  what():  argument not found\n",
        ),
    )

    assert main.split_video_file(str(src), str(parts), "SIZE_MB", "2") == []
    assert "argument not found" in capsys.readouterr().out


def test_bytes_streams_do_not_crash_the_reporter(tmp_path, monkeypatch, capsys):
    """Defensive: if a caller ever runs mkvmerge without text=True the streams are
    bytes. Reporting must degrade to a decode, never raise on top of the failure."""
    monkeypatch.setattr(
        main.subprocess, "run",
        _raise_mkvmerge(stdout=b"Error: something broke\n", stderr=b""),
    )

    assert main.merge_video_files([str(tmp_path / "a.mkv")], str(tmp_path / "m.mkv")) is False
    assert "something broke" in capsys.readouterr().out
