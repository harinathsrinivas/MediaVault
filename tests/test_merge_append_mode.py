import main


def test_merge_argv_includes_append_mode_track(tmp_path, monkeypatch):
    """[SPLIT-SYNC regression] The deterministic re-merge MUST use
    `--append-mode track` so appended chunks are offset by each TRACK's own end
    timestamp, not the whole file's (mkvmerge's default 'file' mode stretches the
    merged video timeline and desyncs a carried-out FLAC)."""
    c1 = tmp_path / "a.chunk.001.mkv"; c1.write_bytes(b"c1")
    c2 = tmp_path / "a.chunk.002.mkv"; c2.write_bytes(b"c2")
    out = tmp_path / "merged.mkv"

    captured = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        out.write_bytes(b"merged")

        class _R:
            returncode = 0
        return _R()

    monkeypatch.setattr(main.subprocess, "run", fake_run)
    main.merge_video_files([str(c1), str(c2)], str(out), seed="abc123")

    cmd = captured["cmd"]
    assert "--append-mode" in cmd and cmd[cmd.index("--append-mode") + 1] == "track", cmd
    # still deterministic
    assert "--deterministic" in cmd and cmd[cmd.index("--deterministic") + 1] == "abc123", cmd


def test_merge_no_seed_still_append_mode_track(tmp_path, monkeypatch):
    """append-mode track applies regardless of determinism (defensive)."""
    c1 = tmp_path / "a.chunk.001.mkv"; c1.write_bytes(b"c1")
    c2 = tmp_path / "a.chunk.002.mkv"; c2.write_bytes(b"c2")
    out = tmp_path / "merged.mkv"
    captured = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        out.write_bytes(b"x")

        class _R:
            returncode = 0
        return _R()

    monkeypatch.setattr(main.subprocess, "run", fake_run)
    main.merge_video_files([str(c1), str(c2)], str(out))
    cmd = captured["cmd"]
    assert "--append-mode" in cmd and cmd[cmd.index("--append-mode") + 1] == "track", cmd