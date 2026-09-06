"""Regression: mkvmerge --split braces-in-path escaping (the legacy {tmdb-…} folder bug).

mkvmerge v97 renders the `--split` output filename through libfmt, so a literal
`{`/`}` in the path is parsed as a fmt replacement field and mkvmerge aborts with
`fmt::format_error: argument not found` (exit 3), rolling back the whole
prep→push→replace. `split_video_file` must pass the `-o` value with braces doubled
(`{{`/`}}`); mkvmerge renders them back to single braces and writes to the real
folder. (A plain merge `-o` is taken LITERALLY by mkvmerge and must NOT be escaped —
verified against mkvmerge v97 — so only the split path escapes.)

IMP-U6: the canonical provider token is now the BRACKET form `[tmdbid-…]`, which
needs no escaping (mkvmerge only treats `{`/`}` as special — the chunk-name pattern
already carries literal `[`/`]`). The curly-brace defense test stays for legacy
folder names still on disk and any other literal brace.

CI-safe: subprocess.run is stubbed (no real mkvmerge needed); we assert the exact
`-o` argument the code would hand mkvmerge.
"""
import main


def test_split_output_escapes_curly_braces_for_mkvmerge(tmp_path, monkeypatch):
    """DEFENSE (legacy): a folder name carrying a literal `{` — e.g. a pre-IMP-U6
    `{tmdb-79660}` curly token — must still be escaped for mkvmerge's libfmt."""
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


def test_split_bracket_token_path_is_passed_through_unescaped(tmp_path, monkeypatch):
    """IMP-U6: the canonical `[tmdbid-…]` folder token needs NO escaping — mkvmerge
    treats only `{`/`}` as libfmt specials; the chunk-name pattern already contains
    literal `[`/`]`. The -o arg must carry the bracket folder byte-identically, and
    the returned chunk paths must point at the real bracket folder."""
    parts = tmp_path / "Movie (2012) [tmdbid-79660]" / "_parts"
    parts.mkdir(parents=True)
    src = tmp_path / "movie.mkv"
    src.write_bytes(b"\x00" * (3 * 1024 * 1024))

    captured = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        (parts / "movie [abcd].chunk.001.mkv").write_bytes(b"x")

        class _R:
            returncode = 0

        return _R()

    monkeypatch.setattr(main.subprocess, "run", fake_run)

    chunks = main.split_video_file(str(src), str(parts), "SIZE_MB", "2", file_id="abcd")

    o_arg = captured["cmd"][captured["cmd"].index("-o") + 1]
    assert "Movie (2012) [tmdbid-79660]" in o_arg, o_arg
    assert "{{" not in o_arg and "}}" not in o_arg, o_arg
    assert chunks, "no chunks returned"
    assert all("[tmdbid-79660]" in c and "{{" not in c for c in chunks), chunks
