"""IMP-C20 regression: refuse to start a split mkvmerge cannot finish.

mkvmerge cannot `--split` a file containing a FLAC track — every split mode
refuses it (see `docs/edge-case-unsplittable-tracks/CODEC-SPLIT-MATRIX.md`).
Before this preflight the failure landed at split time, i.e. AFTER prep had
deep-scanned and whole-file hashed the master (62 GB on the incident file).

Both autopilots (`prep_push_rep`, `prep_push_rep_season`) now probe with
`mkvmerge -J` BEFORE their prep leg, and `cmd_push` probes again before the
split itself. Gating only at push time would be too late — prep is the leg that
spends the deep scan and the whole-file hash.

It deliberately does NOT convert or drop the track — that is an irreversible
quality decision reserved for the operator.

CI-safe: no real mkvmerge needed.
"""
import json
import subprocess

import main

FLAC_TRACK = {
    "id": 4,
    "type": "audio",
    "properties": {"codec_id": "A_FLAC", "language": "ita", "audio_channels": 6},
}
DTS_TRACK = {
    "id": 1,
    "type": "audio",
    "properties": {"codec_id": "A_DTS", "language": "kor", "audio_channels": 6},
}
VIDEO_TRACK = {"id": 0, "type": "video", "properties": {"codec_id": "V_MPEGH/ISO/HEVC"}}

THREE_MB = b"m" * (3 * 1024 * 1024)


def _stub_identify(monkeypatch, payload, exc=None):
    def _run(cmd, **kwargs):
        if exc is not None:
            raise exc

        class _R:
            returncode = 0
            stdout = payload

        return _R()

    monkeypatch.setattr(main.subprocess, "run", _run)


# --------------------------------------------------------------------------
# find_unsplittable_tracks — the probe
# --------------------------------------------------------------------------

def test_probe_flags_flac_track_with_id_and_language(monkeypatch, tmp_path):
    _stub_identify(monkeypatch, json.dumps({"tracks": [VIDEO_TRACK, DTS_TRACK, FLAC_TRACK]}))

    assert main.find_unsplittable_tracks(str(tmp_path / "m.mkv")) == [(4, "A_FLAC", "ita")]


def test_probe_passes_a_file_with_no_unsplittable_track(monkeypatch, tmp_path):
    _stub_identify(monkeypatch, json.dumps({"tracks": [VIDEO_TRACK, DTS_TRACK]}))

    assert main.find_unsplittable_tracks(str(tmp_path / "m.mkv")) == []


def test_probe_failure_never_blocks_an_archive(monkeypatch, tmp_path):
    """A probe that cannot run must return [] — the split itself still reports
    (legibly, post-IMP-C19). Blocking on a probe error would be worse than the
    bug it guards against."""
    _stub_identify(monkeypatch, "", exc=subprocess.CalledProcessError(2, "mkvmerge"))
    assert main.find_unsplittable_tracks(str(tmp_path / "m.mkv")) == []

    _stub_identify(monkeypatch, "", exc=FileNotFoundError("mkvmerge missing"))
    assert main.find_unsplittable_tracks(str(tmp_path / "m.mkv")) == []

    _stub_identify(monkeypatch, "not json at all")
    assert main.find_unsplittable_tracks(str(tmp_path / "m.mkv")) == []


def test_registry_stays_conservative():
    """A false positive blocks a legitimate archive. Only measured codecs belong
    here — TrueHD is suspected but untested, so it must stay out until measured."""
    assert main.UNSPLITTABLE_CODEC_IDS == {"A_FLAC"}


# --------------------------------------------------------------------------
# cmd_push — the early return
# --------------------------------------------------------------------------

def _ready_to_split(sandbox, sandbox_entry):
    """sandbox_entry is shaped for cmd_replace; the push path also needs a
    short_id (chunk tagging) and a file big enough to actually split."""
    sandbox_entry["orig_path"].write_bytes(THREE_MB)
    lib = json.loads(sandbox["lib_movies"].read_text(encoding="utf-8"))
    lib[sandbox_entry["entry_id"]]["short_id"] = "abcd12"
    sandbox["lib_movies"].write_text(json.dumps(lib), encoding="utf-8")


def test_push_aborts_before_creating_anything(sandbox, sandbox_entry, mock_device,
                                              monkeypatch, capsys):
    """The preflight must fire before makedirs/journal, so the abort leaves the
    folder exactly as it was — a clean early return with nothing to roll back."""
    _ready_to_split(sandbox, sandbox_entry)
    media_dir = sandbox_entry["media_dir"]
    before = sorted(p.name for p in media_dir.iterdir())

    monkeypatch.setattr(main, "find_unsplittable_tracks", lambda path: [(4, "A_FLAC", "ita")])

    result = main.cmd_push(sandbox_entry["entry_id"], "SIZE_MB", "1")

    assert result is False, "push must refuse a file it cannot split"

    out = capsys.readouterr().out
    assert "track 4" in out and "A_FLAC" in out, f"the offending track must be named:\n{out}"
    assert "ita" in out, "the track's language helps identify which dub it is"
    assert "RUNBOOK-remux-before-split" in out, "the operator needs the fix procedure"

    assert not (media_dir / main.SPLIT_DIR_NAME).exists(), "_parts must not be created"
    assert not (media_dir / main.CHECKSUM_DIR_NAME).exists(), "checksums/ must not be created"
    assert sorted(p.name for p in media_dir.iterdir()) == sorted(before + [main.TXN_JOURNAL_NAME]), (
        "nothing may be created except the empty journal every command opens"
    )
    # RollbackJournal.__init__ always flushes a fresh journal (main.py:735), so its
    # presence is expected at ANY early return — including the pre-existing
    # free-space one. What matters is that it recorded nothing to undo.
    journal = json.loads((media_dir / main.TXN_JOURNAL_NAME).read_text(encoding="utf-8"))
    assert journal["records"] == [], "the abort must precede every journalled action"
    assert journal["crossed_ponr"] is False


def test_push_does_not_convert_or_drop_the_track(sandbox, sandbox_entry, mock_device,
                                                 monkeypatch, capsys):
    """Guard on the user's standing decision: MediaVault never fixes this itself.
    The abort must tell the operator what to do, not report doing it."""
    _ready_to_split(sandbox, sandbox_entry)
    monkeypatch.setattr(main, "find_unsplittable_tracks", lambda path: [(4, "A_FLAC", "ita")])

    main.cmd_push(sandbox_entry["entry_id"], "SIZE_MB", "1")

    out = capsys.readouterr().out.lower()
    assert "converting" not in out and "converted" not in out
    assert "remux the track" in out, "it should tell the operator what to do, not do it"


def test_clean_file_still_splits(sandbox, sandbox_entry, mock_device, monkeypatch):
    """Control: a file with nothing unsplittable is unaffected by the preflight."""
    _ready_to_split(sandbox, sandbox_entry)
    monkeypatch.setattr(main, "find_unsplittable_tracks", lambda path: [])
    calls = []
    monkeypatch.setattr(main, "split_video_file", lambda *a, **k: calls.append(a) or [])

    main.cmd_push(sandbox_entry["entry_id"], "SIZE_MB", "1")

    assert calls, "the split must still be attempted when the probe finds nothing"


# --------------------------------------------------------------------------
# the autopilots — the gate must fire BEFORE the expensive prep leg
# --------------------------------------------------------------------------

def test_autopilot_refuses_before_prep(tmp_path, monkeypatch, capsys):
    """cmd_push alone is too late: prep_push_rep deep-scans and whole-file hashes
    the master at STEP 1, so refusing at push time still burns it (62 GB on the
    incident file). The gate must precede prep entirely."""
    src = tmp_path / "movie.mkv"
    src.write_bytes(THREE_MB)
    monkeypatch.setattr(main, "find_unsplittable_tracks", lambda p: [(4, "A_FLAC", "ita")])
    called = []
    monkeypatch.setattr(main, "cmd_prep", lambda *a, **k: called.append("prep") or True)
    monkeypatch.setattr(main, "cmd_push", lambda *a, **k: called.append("push") or True)

    main.cmd_prep_push_rep("mov-x", str(src), "SIZE_MB", "1")

    assert called == [], "neither prep nor push may run"
    out = capsys.readouterr().out
    assert "track 4" in out and "A_FLAC" in out
    assert "Nothing was prepped" in out


def test_autopilot_not_gated_when_no_split_requested(tmp_path, monkeypatch):
    """An unsplittable track is only a problem if we intend to split. A plain
    unsplit archive of the same file is perfectly fine and must not be blocked."""
    src = tmp_path / "movie.mkv"
    src.write_bytes(THREE_MB)
    probed = []
    monkeypatch.setattr(main, "find_unsplittable_tracks",
                        lambda p: probed.append(p) or [(4, "A_FLAC", "ita")])
    called = []
    monkeypatch.setattr(main, "cmd_prep", lambda *a, **k: called.append("prep") or False)

    main.cmd_prep_push_rep("mov-x", str(src))

    assert called == ["prep"], "no split requested -> the gate must not fire"
    assert probed == [], "and it should not even probe"


def test_season_autopilot_refuses_before_prep_season(tmp_path, monkeypatch, capsys):
    """cmd_prep_season hashes EVERY episode before the first push, so one bad
    episode would waste the whole season's prep."""
    folder = tmp_path / "Season 01"
    folder.mkdir()
    (folder / "ep01.mkv").write_bytes(THREE_MB)
    (folder / "ep02.mkv").write_bytes(THREE_MB)
    (folder / "notes.txt").write_text("ignored")

    seen = []

    def _probe(path):
        seen.append(path)
        return [(4, "A_FLAC", "ita")] if path.endswith("ep02.mkv") else []

    monkeypatch.setattr(main, "find_unsplittable_tracks", _probe)
    called = []
    monkeypatch.setattr(main, "cmd_prep_season", lambda *a, **k: called.append("prep_season"))

    main.cmd_prep_push_rep_season("tv-x-s01", str(folder), "SIZE_MB", "1")

    assert called == [], "prep_season must not run"
    assert len(seen) == 2, f"both episodes probed, non-video skipped: {seen}"
    out = capsys.readouterr().out
    assert "ep02.mkv" in out, "the offending episode must be named"
    assert "Nothing was prepped" in out
