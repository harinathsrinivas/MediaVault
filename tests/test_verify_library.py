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


# ===========================================================================
# IMP-E14 — close the cmd_prep "clobber a cloud-bearing entry to local_ready"
# regression hole, and detect already-dangling entries in verify_library.
# ===========================================================================

# Ids for the guard-regression test (mov- -> library_movies.json).
_DANGER_ONBOARDED = "mov-en-2024-onbd-noupload"     # onboarded + uploaded=False -> must be REFUSED
_DANGER_RESTORED = "mov-en-2024-restored-noupload"  # restored_local + uploaded missing -> must be REFUSED
_GENUINE_LOCAL = "mov-en-2024-genuine-local"        # local_ready + uploaded=False + real file -> MUST still prep


# ---------------------------------------------------------------------------
# FIX 1 — guard regression: cmd_prep refuses to clobber a cloud-bearing entry
# (status onboarded / restored_local) back to local_ready, but STILL preps a
# genuinely-local entry. Reproduces the battlestar/dark dangling-bug class.
# ---------------------------------------------------------------------------
def test_cmd_prep_refuses_to_clobber_cloud_bearing_status(sandbox, make_video, capsys):
    media = sandbox["media_dir"]

    # An onboarded entry (pushed; master still local) whose `uploaded` was somehow
    # left False — exactly the shape cmd_prep_push_rep_season can re-prep. A REAL
    # file is on disk (so the dummy secondary-skip is NOT what saves it — the new
    # status guard is). short_id/hash are present so we can prove no rebuild.
    onbd_path, _ = make_video(media / "onbd.mkv")
    onbd_entry = {
        "short_id": "aaaa1111",
        "filename": "onbd.mkv",
        "folder_path": str(media),
        "status": "onboarded",
        "uploaded": False,                  # the dangerous shape: cloud-bearing yet uploaded falsy
        "search_term": "onbd [aaaa1111].mkv",
        "hash": "DEADBEEF_ORIGINAL_HASH",
        "metadata": {},
        "tech_spec": {"sentinel": "UNTOUCHED"},
        "type": "movie",
    }

    # A restored_local entry with `uploaded` entirely MISSING (older schema) — also
    # cloud-bearing, also must be refused.
    rest_path, _ = make_video(media / "rest.mkv")
    rest_entry = {
        "short_id": "bbbb2222",
        "filename": "rest.mkv",
        "folder_path": str(media),
        "status": "restored_local",         # uploaded key omitted on purpose
        "search_term": "rest [bbbb2222].mkv",
        "hash": "FEEDFACE_ORIGINAL_HASH",
        "metadata": {},
        "tech_spec": {"sentinel": "UNTOUCHED"},
        "type": "movie",
    }

    library = {_DANGER_ONBOARDED: onbd_entry, _DANGER_RESTORED: rest_entry}
    _write_movies_lib(sandbox, library)

    # Re-prepping either must RETURN True (early-skip success) and create ZERO
    # changes to the entry — the clobber to local_ready/uploaded=False is refused.
    assert main.cmd_prep(_DANGER_ONBOARDED, str(onbd_path)) is True
    assert main.cmd_prep(_DANGER_RESTORED, str(rest_path)) is True

    out = capsys.readouterr().out
    assert "refusing to clobber cloud-bearing status" in out

    lib = mvcommon.load_library()

    # The onboarded entry is byte-for-byte unchanged: status NOT flipped to
    # local_ready, uploaded NOT toggled, hash/tech_spec NOT rebuilt.
    e1 = lib[_DANGER_ONBOARDED]
    assert e1["status"] == "onboarded"
    assert e1["uploaded"] is False
    assert e1["hash"] == "DEADBEEF_ORIGINAL_HASH"
    assert e1["tech_spec"] == {"sentinel": "UNTOUCHED"}

    # The restored_local entry is likewise untouched — and `uploaded` was NOT
    # silently introduced as False by a rebuild (the rebuild never ran).
    e2 = lib[_DANGER_RESTORED]
    assert e2["status"] == "restored_local"
    assert "uploaded" not in e2
    assert e2["hash"] == "FEEDFACE_ORIGINAL_HASH"
    assert e2["tech_spec"] == {"sentinel": "UNTOUCHED"}


def test_cmd_prep_still_preps_a_genuine_local_entry(sandbox, make_video, stub_tech_specs, capsys):
    # The fix must NOT over-block: a genuinely-local entry (local_ready, uploaded
    # falsy, a REAL file, never pushed) must STILL prep normally — not be skipped.
    media = sandbox["media_dir"]
    local_path, real_hash = make_video(media / "genuine.mkv")

    library = {
        _GENUINE_LOCAL: {
            "short_id": "cccc3333",
            "filename": "genuine.mkv",
            "folder_path": str(media),
            "status": "local_ready",
            "uploaded": False,
            "type": "movie",
        }
    }
    _write_movies_lib(sandbox, library)

    result = main.cmd_prep(_GENUINE_LOCAL, str(local_path))
    out = capsys.readouterr().out

    assert result is True
    # It went down the PREP path (not the skip path).
    assert "PREPPING" in out
    assert "refusing to clobber" not in out
    assert "Library Entry Created" in out

    # The entry was (re)built normally: stays local_ready, gains the freshly
    # computed real hash (proves the rebuild ran, unlike the refused cases above).
    lib = mvcommon.load_library()
    e = lib[_GENUINE_LOCAL]
    assert e["status"] == "local_ready"
    assert e["uploaded"] is False
    assert e["hash"] == real_hash


# ---------------------------------------------------------------------------
# FIX 2 — verify_library detects possibly-dangling leaves (in-cloud but marked
# local/not-uploaded) as a SEPARATE advisory that does NOT change the True/False
# return (that stays driven solely by the status↔disk invariant).
# ---------------------------------------------------------------------------
_DANGLING_SPLIT = "mov-en-2024-dangling-split"      # local_ready + split_info -> HIGH
_DANGLING_SIDECAR = "mov-en-2024-dangling-sidecar"  # local_ready + checksums sidecar -> HIGH
_DANGLING_LOW = "mov-en-2024-dangling-searchonly"   # local_ready + search_term only -> LOW
_CLEAN_LOCAL = "mov-en-2024-clean-local"            # local_ready + no cloud evidence -> NOT flagged


def test_verify_library_flags_possibly_dangling_without_failing(sandbox, make_video, capsys):
    media = sandbox["media_dir"]

    # All four leaves have a REAL on-disk file so the status↔disk invariant is
    # SATISFIED for every one — the library is "clean" by the hard check, which
    # lets us prove the dangling advisory is independent of the True/False return.
    make_video(media / "dsplit.mkv")
    make_video(media / "dsidecar.mkv")
    make_video(media / "dlow.mkv")
    make_video(media / "dclean.mkv")

    # HIGH via split_info on the entry.
    split_entry = _leaf(media, "dsplit.mkv", "local_ready")
    split_entry["short_id"] = "5151aaaa"
    split_entry["split_info"] = {
        "is_split": True,
        "total_chunks": 2,
        "chunks": [{"filename": "x.chunk.001.mkv", "hash": "h1"}],
    }

    # HIGH via a checksums/ sidecar embedding THIS entry's short_id (the chunk
    # parity sidecars cmd_push writes). Matched by short_id so a shared season
    # checksums dir is attributed to the right episode.
    sidecar_sid = "7272bbbb"
    sidecar_entry = _leaf(media, "dsidecar.mkv", "local_ready")
    sidecar_entry["short_id"] = sidecar_sid
    sidecar_entry["search_term"] = f"dsidecar [{sidecar_sid}].mkv"  # also present, but HIGH wins
    checksum_dir = media / "checksums"
    checksum_dir.mkdir(exist_ok=True)
    # A sidecar whose name embeds the short_id (mirrors cmd_push's "<name> [sid].chunk.NNN.mkv.sha256").
    (checksum_dir / f"dsidecar [{sidecar_sid}].chunk.001.mkv.sha256").write_text(
        "deadbeef *dsidecar [{}].chunk.001.mkv".format(sidecar_sid), encoding="utf-8"
    )

    # LOW via search_term only (cmd_prep sets it on every entry -> weak evidence).
    low_entry = _leaf(media, "dlow.mkv", "local_ready")
    low_entry["short_id"] = "9393cccc"
    low_entry["search_term"] = "dlow [9393cccc].mkv"

    # CLEAN: a genuine local entry with NO cloud evidence at all — must NOT flag.
    # (No split_info, no checksums sidecar for its short_id, no search_term.)
    clean_entry = _leaf(media, "dclean.mkv", "local_ready")
    clean_entry["short_id"] = "0404dddd"

    library = {
        _DANGLING_SPLIT: split_entry,
        _DANGLING_SIDECAR: sidecar_entry,
        _DANGLING_LOW: low_entry,
        _CLEAN_LOCAL: clean_entry,
    }
    _write_movies_lib(sandbox, library)

    result = main.cmd_verify_library()
    out = capsys.readouterr().out

    # The hard status↔disk invariant is satisfied for all 4 (real files) -> True,
    # PROVING possibly_dangling does NOT, by itself, fail the audit.
    assert result is True
    assert "scanned 4, OK 4, MISMATCH 0" in out

    # The advisory section is printed and names the three flagged ids with tiers.
    assert "POSSIBLY DANGLING (in-cloud but marked local/not-uploaded)" in out
    assert f"{_DANGLING_SPLIT}  [high]" in out
    assert f"{_DANGLING_SIDECAR}  [high]" in out
    assert f"{_DANGLING_LOW}  [low]" in out

    # The clean local entry is NOT flagged as dangling.
    assert _CLEAN_LOCAL not in out.split("POSSIBLY DANGLING", 1)[1]

    # Summary tallies: 2 high + 1 low = 3.
    assert "possibly_dangling: 3 (high=2, low=1)" in out


def test_verify_library_no_dangling_section_when_clean(sandbox, make_video, capsys):
    # A purely-clean local library (real files, no cloud evidence anywhere) prints
    # NEITHER the advisory header NOR a possibly_dangling summary suffix.
    media = sandbox["media_dir"]
    make_video(media / "okreal_local.mkv")

    library = {_CLEAN_LOCAL: _leaf(media, "okreal_local.mkv", "local_ready")}
    _write_movies_lib(sandbox, library)

    result = main.cmd_verify_library()
    out = capsys.readouterr().out

    assert result is True
    assert "scanned 1, OK 1, MISMATCH 0" in out
    assert "POSSIBLY DANGLING" not in out
    assert "possibly_dangling" not in out


def test_verify_library_dangling_skips_uploaded_and_virtual(sandbox_alias, capsys):
    # Two guarantees in one alias-bearing library:
    #   (1) an archived/uploaded=True entry is the CORRECT in-cloud end state — it
    #       must NOT be reported as dangling (it's not local_ready + not-uploaded).
    #   (2) the alias/season_map VIRTUAL entries must be skipped by the dangling
    #       pass (no crash, never flagged — they own no file).
    media = sandbox_alias["media_dir"]

    # Add an archived+uploaded movie alongside the alias chain. archived+VIDEO_DUMMY
    # keeps the status↔disk invariant happy (so the return stays True) while proving
    # the dangling pass ignores a correctly cloud-bearing entry — even though it has
    # split_info (which WOULD be HIGH if it were a local/not-uploaded candidate).
    (media / "arch.mkv").write_bytes(b"\x00\x01\x02BINARY-DUMMY" * 8)  # small video dummy
    lib = mvcommon.load_library()
    lib["mov-en-2024-archived-correct"] = {
        "short_id": "eeee5555",
        "filename": "arch.mkv",
        "folder_path": str(media),
        "status": "archived",
        "uploaded": True,
        "split_info": {"is_split": True, "total_chunks": 1, "chunks": []},
        "type": "movie",
    }
    mvcommon.save_library(lib)

    result = main.cmd_verify_library()
    out = capsys.readouterr().out

    # All on-disk shapes are consistent -> the hard invariant passes -> True.
    assert result is True

    # The archived/uploaded entry is the correct end state -> NOT a dangler, even
    # though it carries split_info. The two virtual entries never appear either.
    # (Match the exact dangler-line shape `<id>  [tier]` so the season_id check
    # does not collide with the primary id, of which it is a prefix.)
    danger_section = out.split("POSSIBLY DANGLING", 1)
    if len(danger_section) > 1:  # a dangling section exists (see below) — scope to it
        assert "mov-en-2024-archived-correct  [" not in danger_section[1]
        assert f"{sandbox_alias['alias_id']}  [" not in danger_section[1]
        assert f"{sandbox_alias['season_id']}  [" not in danger_section[1]

    # The sandbox_alias PRIMARY is local_ready + uploaded=False and carries a
    # search_term (cmd_prep sets one on every entry) but no stronger evidence, so it
    # IS flagged — as LOW. This is the expected weak-signal behavior, and it proves
    # uploaded/virtual exclusion above is what filters the others (not a blanket
    # "no danglers"). Exactly one LOW dangler: the primary.
    assert f"{sandbox_alias['primary_id']}  [low]" in out
    assert "possibly_dangling: 1 (high=0, low=1)" in out
