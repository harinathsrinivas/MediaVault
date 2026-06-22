"""Tests for the IMP-D4 library status ↔ disk integrity guard.

Covers:
  (a) a clean sandbox library (statuses matching on-disk shapes) -> cmd_verify_library() is True.
  (b) seeded mismatches (archived+TEXT_DUMMY, local_ready+tiny dummy) -> cmd_verify_library()
      is False AND the report names both ids.
  (c) _warn_if_entry_inconsistent prints a warning + returns None for a mismatch, and prints
      nothing for a consistent entry; it must NEVER raise.
  (d) alias-safety: a season_map + multi_ep_alias bearing library does not raise (PR #21 class).

Constraints honored (docs/testing-strategy.md §1):
  * Never touch real C:\\Media files or real library_*.json — everything goes through the
    `sandbox` / `sandbox_alias` fixtures (which redirect LIBRARY_* + LOCAL_ROOT to tmp_path
    and hard-guard against real media).
  * Run `pytest -q` and fix failures before marking the step done.

The on-disk shapes are produced WITHOUT ffmpeg: REAL files via the `make_video` fixture
(>DUMMY_MAX_BYTES of deterministic bytes), small binary VIDEO_DUMMY files via raw bytes,
and the legacy TEXT_DUMMY stub via a 126-byte payload starting with b"Original Hash:".
"""
import json
import os

import main
import mvcommon


# Movie ids (mov- prefix) route to library_movies.json via load_library/save_library.
_OK_REAL_LOCAL = "mov-en-2024-okreal-local"      # local_ready + REAL  -> OK
_OK_REAL_ONBOARDED = "mov-en-2024-okreal-onbd"   # onboarded   + REAL  -> OK
_OK_ARCHIVED_DUMMY = "mov-en-2024-okarchived"    # archived    + VIDEO_DUMMY -> OK
_BAD_ARCHIVED_TEXT = "mov-en-2024-badtextstub"   # archived    + TEXT_DUMMY  -> violation
_BAD_LOCAL_DUMMY = "mov-en-2024-badlocaldummy"   # local_ready + VIDEO_DUMMY -> violation

# The exact legacy stub the IMP-D4 bug left behind: 126 bytes, leading b"Original Hash".
_LEGACY_TEXT_STUB = b"Original Hash: deadbeefdeadbeefdeadbeefdeadbeef\nStatus: ARCHIVED\n" + b"x" * 61
_SMALL_VIDEO_DUMMY = b"\x00\x01\x02BINARY-DUMMY-NOT-TEXT" * 4  # small, not a text stub


def _write_movies_lib(sandbox, library):
    """Persist a movies-only library to the sandbox; series/anime stay empty.

    Writes the JSON directly into the sandbox movie lib (all ids are mov-*), and
    guarantees the other two lib files exist as {} so load_library never skips one.
    """
    sandbox["lib_movies"].write_text(json.dumps(library), encoding="utf-8")
    sandbox["lib_series"].write_text("{}", encoding="utf-8")
    sandbox["lib_anime"].write_text("{}", encoding="utf-8")


def _leaf(folder, filename, status, *, uploaded=False):
    return {
        "status": status,
        "uploaded": uploaded,
        "folder_path": str(folder),
        "filename": filename,
        "type": "movie",
    }


# ---------------------------------------------------------------------------
# (a) clean library -> True
# ---------------------------------------------------------------------------
def test_verify_library_clean_returns_true(sandbox, make_video, capsys):
    media = sandbox["media_dir"]

    # local_ready + REAL on disk
    make_video(media / "okreal_local.mkv")
    # onboarded + REAL on disk (pushed, master still local)
    make_video(media / "okreal_onbd.mkv")
    # archived + VIDEO_DUMMY (small binary, NOT a text stub)
    (media / "okarchived.mkv").write_bytes(_SMALL_VIDEO_DUMMY)

    library = {
        _OK_REAL_LOCAL: _leaf(media, "okreal_local.mkv", "local_ready"),
        _OK_REAL_ONBOARDED: _leaf(media, "okreal_onbd.mkv", "onboarded", uploaded=True),
        _OK_ARCHIVED_DUMMY: _leaf(media, "okarchived.mkv", "archived", uploaded=True),
    }
    _write_movies_lib(sandbox, library)

    result = main.cmd_verify_library()
    out = capsys.readouterr().out

    assert result is True
    assert "No integrity mismatches found" in out
    # Summary line: 3 scanned, all OK, none mismatched.
    assert "scanned 3, OK 3, MISMATCH 0" in out


# ---------------------------------------------------------------------------
# (b) seeded mismatches -> False + both ids in the report
# ---------------------------------------------------------------------------
def test_verify_library_flags_mismatches(sandbox, make_video, capsys):
    media = sandbox["media_dir"]

    # One genuinely-OK entry so the scan count is meaningful (and OK > 0).
    make_video(media / "okreal_local.mkv")

    # VIOLATION 1: archived but on-disk is a 126-byte legacy TEXT stub.
    stub_path = media / "badtextstub.mkv"
    stub_path.write_bytes(_LEGACY_TEXT_STUB)
    assert len(_LEGACY_TEXT_STUB) == 126, "the legacy stub fixture should be 126 bytes"

    # VIOLATION 2: local_ready but on-disk is a tiny (sub-threshold) dummy.
    (media / "badlocaldummy.mkv").write_bytes(_SMALL_VIDEO_DUMMY)

    library = {
        _OK_REAL_LOCAL: _leaf(media, "okreal_local.mkv", "local_ready"),
        _BAD_ARCHIVED_TEXT: _leaf(media, "badtextstub.mkv", "archived", uploaded=True),
        _BAD_LOCAL_DUMMY: _leaf(media, "badlocaldummy.mkv", "local_ready"),
    }
    _write_movies_lib(sandbox, library)

    result = main.cmd_verify_library()
    out = capsys.readouterr().out

    assert result is False
    # Both violating ids are named in the per-violation report.
    assert _BAD_ARCHIVED_TEXT in out
    assert _BAD_LOCAL_DUMMY in out
    # The OK entry is NOT reported as a violation (it only appears in counts/summary).
    assert "scanned 3, OK 1, MISMATCH 2" in out
    # Category labels surfaced for the human + summary.
    assert "archived_textdummy" in out
    assert "local_ready_dummy" in out


# ---------------------------------------------------------------------------
# (c) _warn_if_entry_inconsistent: warns + returns None for a mismatch,
#     silent for a consistent entry, never raises.
# ---------------------------------------------------------------------------
def test_warn_if_entry_inconsistent_warns_on_mismatch(sandbox, capsys):
    media = sandbox["media_dir"]
    # archived status but on-disk is a legacy TEXT stub -> mismatch.
    (media / "warn_bad.mkv").write_bytes(_LEGACY_TEXT_STUB)
    entry = _leaf(media, "warn_bad.mkv", "archived", uploaded=True)

    ret = main._warn_if_entry_inconsistent(entry, "mov-en-2024-warnbad")
    out = capsys.readouterr().out

    assert ret is None  # observability hook returns None, never a value
    assert "INTEGRITY" in out
    assert "mov-en-2024-warnbad" in out
    assert "verify_library" in out  # tells the human how to audit


def test_warn_if_entry_inconsistent_silent_when_consistent(sandbox, make_video, capsys):
    media = sandbox["media_dir"]
    # onboarded + REAL on disk -> consistent, no warning.
    make_video(media / "warn_ok.mkv")
    entry = _leaf(media, "warn_ok.mkv", "onboarded", uploaded=True)

    ret = main._warn_if_entry_inconsistent(entry, "mov-en-2024-warnok")
    out = capsys.readouterr().out

    assert ret is None
    assert out == ""  # absolutely nothing printed for a consistent entry


def test_warn_if_entry_inconsistent_never_raises_on_virtual_or_malformed(capsys):
    # Virtual types own no file -> skipped silently, no raise.
    assert main._warn_if_entry_inconsistent(
        {"type": "multi_ep_alias", "alias_of": "x", "parent_id": "y"}, "alias-id"
    ) is None
    assert main._warn_if_entry_inconsistent(
        {"type": "season_map", "folder_path": "/nope", "children": []}, "season-id"
    ) is None
    # Malformed leaf (missing file keys) -> skipped silently, no raise.
    assert main._warn_if_entry_inconsistent({"status": "archived"}, "no-file-id") is None
    # A non-dict entry must not blow up the hook.
    assert main._warn_if_entry_inconsistent(None, "none-id") is None
    assert capsys.readouterr().out == ""


# ---------------------------------------------------------------------------
# (d) alias-safety: season_map + multi_ep_alias must not crash cmd_verify_library.
# ---------------------------------------------------------------------------
def test_verify_library_alias_safe(sandbox_alias, capsys):
    # sandbox_alias seeds a season_map + a leaf primary (REAL file, local_ready) +
    # a multi_ep_alias. cmd_verify_library must skip the two virtual entries BEFORE
    # dereferencing folder_path/filename (PR #21 crash class) and not raise.
    result = main.cmd_verify_library()  # must not raise
    out = capsys.readouterr().out

    # The leaf primary is local_ready with a REAL (>DUMMY_MAX_BYTES) file on disk,
    # so the library is clean and the virtual entries were skipped (only 1 scanned).
    assert result is True
    assert "scanned 1, OK 1, MISMATCH 0" in out

    # The alias entry is untouched by the read-only audit.
    lib = mvcommon.load_library()
    assert lib[sandbox_alias["alias_id"]]["type"] == "multi_ep_alias"


# ---------------------------------------------------------------------------
# (b-extra) fix_dummies reuses cmd_repair_dummies for archived+TEXT_DUMMY only,
# regenerates the dummy, and does NOT change any status field (IMP-D5 slice).
# ---------------------------------------------------------------------------
def test_verify_library_fix_dummies_regenerates_text_stub(sandbox, fake_dummy, capsys):
    from conftest import FAKE_DUMMY_BYTES

    media = sandbox["media_dir"]
    stub_path = media / "badtextstub.mkv"
    stub_path.write_bytes(_LEGACY_TEXT_STUB)  # archived + TEXT_DUMMY violation

    library = {
        _BAD_ARCHIVED_TEXT: _leaf(media, "badtextstub.mkv", "archived", uploaded=True),
    }
    _write_movies_lib(sandbox, library)

    # fix_dummies=True -> reports the violation, then regenerates via repair_dummies
    # (fake_dummy stub writes FAKE_DUMMY_BYTES). Status fields stay untouched.
    result = main.cmd_verify_library(fix_dummies=True)
    out = capsys.readouterr().out

    # The audit still reports the mismatch (returns False — fix does not relabel).
    assert result is False
    assert "fix_dummies: regenerating" in out
    assert "repair_dummies complete" in out  # the reused command actually ran

    # The on-disk text stub was replaced by the regenerated (fake) video dummy.
    assert stub_path.read_bytes() == FAKE_DUMMY_BYTES

    # fix_dummies must NOT mutate status/uploaded — only the dummy bytes change.
    lib = mvcommon.load_library()
    assert lib[_BAD_ARCHIVED_TEXT]["status"] == "archived"
    assert lib[_BAD_ARCHIVED_TEXT]["uploaded"] is True
