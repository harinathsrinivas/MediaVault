"""FLAC carry-out — generic track reconstruction + detector unit tests.

The carry-out feature (docs/feature-flac-carryout/) archives a FLAC-bearing file by
extracting the FLAC into a valid-video "holder", splitting the remainder with the
FLAC dropped, and — on restore — re-adding the FLAC at its ORIGINAL position with its
ORIGINAL flags, GENERICALLY (driven by a recorded per-track manifest, matched by UID —
never by sample-specific indices).

These tests are CI-safe: no real mkvmerge/ffmpeg/media. They lock the PURE seam —
`_carryout_merge_argv` (the manifest -> --track-order + flag-options builder) — plus
the detector/disposition and the split drop-track argv shape.
"""
import json
import os

import main


# --------------------------------------------------------------------------
# _carryout_merge_argv — the generic reconstruction builder (D-9)
# --------------------------------------------------------------------------

def _manifest():
    """A non-Black-Panther manifest: video, a FLAC DEFAULT track (position 2),
    a forced subtitle, a named subtitle, a cover-ish second video track.
    Chosen to prove genericity: the FLAC is mid-list AND default=True, and a
    forced + named sub must survive unchanged."""
    return [
        {"id": 0, "type": "video", "codec_id": "V_MPEGH/ISO/HEVC", "language": "eng",
         "default": True, "forced": False, "enabled": True, "name": None, "uid": "Uvideo"},
        {"id": 1, "type": "audio", "codec_id": "A_AC3", "language": "eng",
         "default": False, "forced": False, "enabled": True, "name": "Commentary", "uid": "Uac3"},
        {"id": 2, "type": "audio", "codec_id": "A_FLAC", "language": "jpn",
         "default": True, "forced": False, "enabled": True, "name": None, "uid": "Uflac"},
        {"id": 3, "type": "subtitles", "codec_id": "S_TEXT/UTF8", "language": "eng",
         "default": False, "forced": True, "enabled": True, "name": "Forced", "uid": "Usubforced"},
        {"id": 4, "type": "subtitles", "codec_id": "S_TEXT/UTF8", "language": "eng",
         "default": False, "forced": False, "enabled": True, "name": "SDH", "uid": "Usubsdh"},
    ]


def _carried():
    return {
        "uid": "Uflac",
        "language": "jpn",
        "default": True,
        "forced": False,
        "enabled": True,
        "name": None,
        "track_id": 2,
        "position": 2,
        "original_tracks": _manifest(),
    }


def test_carryout_merge_argv_reinserts_flac_at_original_position():
    # chunk (FLAC dropped) keeps 4 tracks in the same relative order minus the FLAC:
    # Uvideo, Uac3, Usubforced, Usubsdh  -> chunk ids 0,1,2,3
    chunk_uids = ["Uvideo", "Uac3", "Usubforced", "Usubsdh"]
    track_order, opts = main._carryout_merge_argv(chunk_uids, _carried(), flac_fid=4)

    pairs = track_order.split(",")
    # FLAC (fid 4) must land at the manifest position of Uflac == index 2
    assert pairs[2] == "4:0", f"FLAC must reinsert at position 2, got {pairs}"
    # everything else maps to its chunk id
    assert pairs == ["0:0", "0:1", "4:0", "0:2", "0:3"], pairs


def test_carryout_merge_argv_reapplies_default_and_language(  ):
    chunk_uids = ["Uvideo", "Uac3", "Usubforced", "Usubsdh"]
    _, opts = main._carryout_merge_argv(chunk_uids, _carried(), flac_fid=4)
    # language -> jpn
    assert "--language" in opts and opts[opts.index("--language") + 1] == "0:jpn"
    # default=True -> --default-track 0:1
    assert "--default-track" in opts and opts[opts.index("--default-track") + 1] == "0:1"
    # forced=False -> --forced-track 0:0
    assert "--forced-track" in opts and opts[opts.index("--forced-track") + 1] == "0:0"
    # no name -> no --track-name
    assert "--track-name" not in opts


def test_carryout_merge_argv_applies_track_name_when_present(  ):
    carried = _carried()
    carried["name"] = "Main Audio"
    chunk_uids = ["Uvideo", "Uac3", "Usubforced", "Usubsdh"]
    _, opts = main._carryout_merge_argv(chunk_uids, carried, flac_fid=4)
    assert "--track-name" in opts and opts[opts.index("--track-name") + 1] == "0:Main Audio"


def test_carryout_merge_argv_last_position_flac(  ):
    # FLAC at the END of the manifest (position 4) still reconstructs correctly
    manifest = _manifest()
    flac = manifest.pop(2)          # remove FLAC from middle
    manifest.append(flac)           # put it last
    carried = {
        "uid": "Uflac", "language": "jpn", "default": False, "forced": False,
        "enabled": True, "name": None, "track_id": 2, "position": 4,
        "original_tracks": manifest,
    }
    chunk_uids = ["Uvideo", "Uac3", "Usubforced", "Usubsdh"]
    track_order, opts = main._carryout_merge_argv(chunk_uids, carried, flac_fid=4)
    pairs = track_order.split(",")
    assert pairs[-1] == "4:0", f"FLAC at end must be last, got {pairs}"
    assert "--default-track" in opts and opts[opts.index("--default-track") + 1] == "0:0"


# --------------------------------------------------------------------------
# refuse_if_unsplittable — carry_out_flac disposition
# --------------------------------------------------------------------------

def test_refuse_flac_default(monkeypatch):
    """Default (carry_out_flac=False) still refuses FLAC — the extras split path."""
    monkeypatch.setattr(main, "find_unsplittable_tracks", lambda p: [(2, "A_FLAC", "jpn")])
    assert main.refuse_if_unsplittable("x", "lbl") is True


def test_refuse_flac_carry_out_true_lets_flac_through(monkeypatch):
    monkeypatch.setattr(main, "find_unsplittable_tracks", lambda p: [(2, "A_FLAC", "jpn")])
    assert main.refuse_if_unsplittable("x", "lbl", carry_out_flac=True) is False


def test_refuse_nonflac_always_refuses(monkeypatch):
    """An unknown unsplittable codec is refused even with carry_out_flac=True."""
    monkeypatch.setattr(main, "find_unsplittable_tracks", lambda p: [(3, "A_TRUEHD", "eng")])
    assert main.refuse_if_unsplittable("x", "lbl", carry_out_flac=True) is True


def test_carry_out_codecs_are_a_subset_of_unsplittable():
    """defensive: you cannot carry out a codec that is not measured-unsplittable."""
    assert main.CARRY_OUT_CODEC_IDS <= main.UNSPLITTABLE_CODEC_IDS


# --------------------------------------------------------------------------
# split_video_file drop_track argv shape
# --------------------------------------------------------------------------

def test_split_drop_track_adds_audio_tracks_bang(tmp_path, monkeypatch):
    parts = tmp_path / "_parts"
    parts.mkdir()
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
    main.split_video_file(str(src), str(parts), "SIZE_MB", "2", drop_track=2)
    cmd = captured["cmd"]
    assert "--audio-tracks" in cmd and cmd[cmd.index("--audio-tracks") + 1] == "!2"


def test_split_no_drop_track_has_no_audio_tracks(tmp_path, monkeypatch):
    parts = tmp_path / "_parts"
    parts.mkdir()
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
    assert "--audio-tracks" not in captured["cmd"]


def test_split_returns_only_chunks_not_holder(tmp_path, monkeypatch):
    """[bug fix] split_video_file globs ONLY the chunk pattern — a carried-out FLAC
    holder ".holder.mkv" living in the same dir must NOT be returned as a chunk
    (it is uploaded/hashed separately via carried_out_tracks). This is the exact bug
    that made the holder upload TWICE and fail the Black Panther push."""
    parts = tmp_path / "_parts"
    parts.mkdir()
    src = tmp_path / "movie.mkv"
    src.write_bytes(b"\x00" * (3 * 1024 * 1024))
    # a holder that would be present in a real carry-out
    (parts / "movie [abcd].holder.mkv").write_bytes(b"holder")

    def fake_run(cmd, **kwargs):
        # mkvmerge writes two real chunks
        (parts / "movie [abcd].chunk.001.mkv").write_bytes(b"c1")
        (parts / "movie [abcd].chunk.002.mkv").write_bytes(b"c2")

        class _R:
            returncode = 0
        return _R()

    monkeypatch.setattr(main.subprocess, "run", fake_run)
    chunks = main.split_video_file(str(src), str(parts), "SIZE_MB", "1", file_id="abcd")
    names = [os.path.basename(c) for c in chunks]
    assert ".holder.mkv" not in names, f"holder must not be returned as a chunk: {names}"
    assert len(chunks) == 2, names


# --------------------------------------------------------------------------
# probe_track_manifest — field shape (generic reconstruction source)
# --------------------------------------------------------------------------

def test_probe_track_manifest_shape(monkeypatch):
    ident = {"tracks": [
        {"id": 0, "type": "video", "properties": {"codec_id": "V_MPEGH/ISO/HEVC",
            "language": "eng", "default_track": True, "forced_track": False,
            "enabled_track": True, "track_name": None, "uid": 123456789}},
    ]}

    class _R:
        stdout = json.dumps(ident)

    monkeypatch.setattr(main.subprocess, "run", lambda *a, **k: _R())
    manifest = main.probe_track_manifest("x.mkv")
    assert manifest[0]["codec_id"] == "V_MPEGH/ISO/HEVC"
    assert manifest[0]["default"] is True
    assert manifest[0]["uid"] == "123456789"
    assert manifest[0]["id"] == 0
    assert manifest[0]["type"] == "video"


def test_probe_track_manifest_empty_on_failure(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("probe fail")
    monkeypatch.setattr(main.subprocess, "run", boom)
    assert main.probe_track_manifest("x.mkv") == []