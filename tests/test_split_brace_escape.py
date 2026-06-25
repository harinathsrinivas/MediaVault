"""Regression: mkvmerge --split braces-in-path escaping (the {tmdb-…} folder bug).

mkvmerge v97 renders the `--split` output filename through libfmt, so a literal
`{`/`}` in the path — e.g. a `Movie (2012) {tmdb-79660}` Plex/Emby/Jellyfin folder
token — is parsed as a fmt replacement field and mkvmerge aborts with
`fmt::format_error: argument not found` (exit 3), rolling back the whole
prep→push→replace. `split_video_file` must pass the `-o` value with braces doubled
(`{{`/`}}`); mkvmerge renders them back to single braces and writes to the real
folder. (A plain merge `-o` is taken LITERALLY by mkvmerge and must NOT be escaped —
verified against mkvmerge v97 — so only the split path escapes.)

CI-safe: subprocess.run is stubbed (no real mkvmerge needed); we assert the exact
`-o` argument the code would hand mkvmerge.
"""
import main


def test_split_output_escapes_curly_braces_for_mkvmerge(tmp_path, monkeypatch):
    parts = tmp_path / "3 (2012) {tmdb-79660}" / "_parts"
    parts.mkdir(parents=True)
    src = tmp_path / "movie.mkv"
    src.write_bytes(b"\x00" * (3 * 1024 * 1024))  # 3 MB so the SIZE_MB math runs

    captured = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        # simulate mkvmerge writing one chunk into the REAL (single-brace) dir
        (parts / "movie [abcd].chunk.001.mkv").write_bytes(b"x")

        class _R:
            returncode = 0

        return _R()

    monkeypatch.setattr(main.subprocess, "run", fake_run)

    chunks = main.split_video_file(str(src), str(parts), "SIZE_MB", "2", file_id="abcd")

    cmd = captured["cmd"]
    o_arg = cmd[cmd.index("-o") + 1]
    # The -o handed to mkvmerge MUST have doubled braces (fmt escape)...
    assert "{{tmdb-79660}}" in o_arg, f"-o braces not escaped: {o_arg!r}"
    # ...and no UNescaped single brace token should remain.
    assert "{tmdb-79660}" not in o_arg.replace("{{tmdb-79660}}", "<esc>"), o_arg
    # The chunk paths the function RETURNS point at the real single-brace folder.
    assert chunks, "no chunks returned"
    assert all("{tmdb-79660}" in c and "{{" not in c for c in chunks), chunks


def test_split_no_braces_path_is_unchanged(tmp_path, monkeypatch):
    """A normal (brace-free) folder must be passed through byte-identically."""
    parts = tmp_path / "Plain Movie (2012)" / "_parts"
    parts.mkdir(parents=True)
    src = tmp_path / "movie.mkv"
    src.write_bytes(b"\x00" * (3 * 1024 * 1024))

    captured = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        (parts / "movie.chunk.001.mkv").write_bytes(b"x")

        class _R:
            returncode = 0

        return _R()

    monkeypatch.setattr(main.subprocess, "run", fake_run)
    main.split_video_file(str(src), str(parts), "SIZE_MB", "2")
    o_arg = captured["cmd"][captured["cmd"].index("-o") + 1]
    assert "{{" not in o_arg and "}}" not in o_arg, o_arg
    assert "Plain Movie (2012)" in o_arg
