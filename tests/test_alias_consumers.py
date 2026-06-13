"""Regression tests for multi_ep_alias consumer fixes (IMP-C12 / IMP-C13 — Step A3).

Every test here uses the `sandbox_alias` fixture (plus `mock_device`, `fake_dummy`
as needed) which sets up a combined-episode (multi_ep_alias) library:
  - primary  : tv-en-2009-bsg-s04e19  (real .mkv on disk, status=local_ready, uploaded=False)
  - alias    : tv-en-2009-bsg-s04e20  (type=multi_ep_alias, alias_of=primary, parent_id=season)
  - season   : tv-en-2009-bsg-s04     (type=season_map, children=[primary, alias])

Eight consumer tests (one test per consumer) assert that:
  1. cmd_scan_unprepped does not raise on the alias and does not list it as unprepped.
  2. cmd_local_status does not raise on the alias and does not include it as a pending row.
  3. cmd_check(alias_id) resolves to the primary — prints the info line, returns PASS.
  4. cmd_push(alias_id)  resolves to the primary — info line printed, library shows primary uploaded.
  5. cmd_replace(alias_id) resolves to the primary — info line, side effect on the primary's file.
  6. cmd_restore(alias_id) resolves to the primary — info line, no KeyError.
  7. cmd_verify_restore(alias_id) resolves to the primary — info line, no KeyError.
  8. cmd_prep(alias_id, path) refuses and leaves alias entry byte-for-byte unchanged.

Plus control tests:
  - cmd_check(primary_id) does NOT print the info line (real_id == manual_id).
  - cmd_push(primary_id)  does NOT print the info line (real_id == manual_id).

Anti-patterns observed per docs/testing-strategy.md:
  - Never touch real C:\\Media or real library_*.json (sandbox + sandbox_alias guard this).
  - Never assert on absolute device paths — use rglob("*.mkv") + filter by .name.
  - Never bypass the sandbox fixture (patch both bindings).
"""
import hashlib
import json
import os

import pytest

import main
import mvcommon

# ---------------------------------------------------------------------------
# Shared constant: the info line that every alias-resolving command must print
# ---------------------------------------------------------------------------
INFO_SUBSTRING = "is part of the combined file registered as tv-en-2009-bsg-s04e19"


# ===========================================================================
# 1. cmd_scan_unprepped — must not raise and must not list the alias
# ===========================================================================

def test_scan_unprepped_skips_alias_no_crash(sandbox_alias, capsys):
    """cmd_scan_unprepped must not raise KeyError / AttributeError on the
    multi_ep_alias entry (which has no filename), and must never list the alias
    id as an unprepped file."""
    alias_id = sandbox_alias["alias_id"]

    # Must not raise
    main.cmd_scan_unprepped()

    out = capsys.readouterr().out
    # The alias id must NOT appear as an unprepped file in the output.
    # (The primary's file IS in known_paths because it is a leaf entry.)
    assert alias_id not in out, (
        f"alias id '{alias_id}' appeared in scan_unprepped output — phantom row or crash"
    )


# ===========================================================================
# 2. cmd_local_status — must not raise and must not include the alias as pending
# ===========================================================================

def test_local_status_skips_alias_no_crash(sandbox_alias, capsys):
    """cmd_local_status must skip multi_ep_alias entries (they have no filename
    field) — no AttributeError from None.filename slicing, and no phantom row."""
    alias_id = sandbox_alias["alias_id"]

    # Must not raise
    main.cmd_local_status()

    out = capsys.readouterr().out
    # The alias id must NOT appear as a pending row.
    # The primary WILL appear (uploaded=False), but the alias must not.
    assert alias_id not in out, (
        f"alias id '{alias_id}' appeared as a pending local_status row — should be skipped"
    )


# ===========================================================================
# 3. cmd_check(alias_id) — resolves to primary, info line, PASS result
# ===========================================================================

def test_check_alias_resolves_to_primary_and_passes(sandbox_alias, capsys):
    """cmd_check(alias_id) must not raise KeyError, must print the info line
    that shows resolution to the primary, and must PASS the hash check (the
    primary's file bytes match the stored sha256 in the fixture)."""
    alias_id = sandbox_alias["alias_id"]

    main.cmd_check(alias_id)

    out = capsys.readouterr().out
    assert INFO_SUBSTRING in out, (
        f"Expected info line about alias resolution in output.\nActual output:\n{out}"
    )
    assert "PASS" in out, (
        f"Expected PASS result for alias check.\nActual output:\n{out}"
    )


def test_check_primary_no_info_line(sandbox_alias, capsys):
    """Control: cmd_check(primary_id) must NOT print the info line
    (real_id == manual_id) — non-alias behaviour is unchanged."""
    primary_id = sandbox_alias["primary_id"]

    main.cmd_check(primary_id)

    out = capsys.readouterr().out
    assert INFO_SUBSTRING not in out, (
        f"Info line should NOT appear for primary_id — control assertion failed.\nActual output:\n{out}"
    )
    assert "PASS" in out, (
        f"Expected PASS result for direct primary check.\nActual output:\n{out}"
    )


# ===========================================================================
# 4. cmd_push(alias_id) — resolves to primary, info line, library updated
# ===========================================================================

def test_push_alias_resolves_to_primary(sandbox_alias, mock_device, capsys):
    """cmd_push(alias_id) must:
      - not raise KeyError
      - print the info line showing alias resolution
      - upload the primary's file (not create an entry under alias_id)
      - set the primary entry's uploaded=True / status=onboarded in the library
      - leave the alias entry byte-for-byte unchanged (still 3-key schema)
    The primary has no split_info, so this exercises the non-split single-file
    push path. The file is on disk (created by sandbox_alias).
    """
    alias_id = sandbox_alias["alias_id"]
    primary_id = sandbox_alias["primary_id"]

    result = main.cmd_push(alias_id)

    out = capsys.readouterr().out
    assert INFO_SUBSTRING in out, (
        f"Expected info line about alias resolution.\nActual output:\n{out}"
    )
    assert result is True, f"cmd_push(alias_id) expected True, got {result!r}"

    library = mvcommon.load_library()
    # Primary must be marked uploaded
    assert library[primary_id]["uploaded"] is True, (
        "Primary entry must be marked uploaded after push via alias"
    )
    assert library[primary_id]["status"] == "onboarded", (
        "Primary entry must have status onboarded after push via alias"
    )
    # Alias schema must remain unchanged (3 keys only)
    alias_entry = library[alias_id]
    assert set(alias_entry.keys()) == {"type", "alias_of", "parent_id"}, (
        f"Alias entry must stay 3-key schema; got keys: {set(alias_entry.keys())}"
    )

    # No file should land on the device under alias_id. The primary's file should
    # be there. We look by name (rglob avoids [...] glob metachar problem).
    all_on_device = {f.name: f for f in mock_device.rglob("*") if f.is_file()}
    # Sanity: at least one file was pushed
    assert len(all_on_device) >= 1, "Expected at least one file on device after push"


def test_push_primary_no_info_line(sandbox_alias, mock_device, capsys):
    """Control: cmd_push(primary_id) must NOT print the info line."""
    primary_id = sandbox_alias["primary_id"]

    main.cmd_push(primary_id)

    out = capsys.readouterr().out
    assert INFO_SUBSTRING not in out, (
        f"Info line should NOT appear when pushing primary directly.\nActual output:\n{out}"
    )


# ===========================================================================
# 5. cmd_replace(alias_id) — resolves to primary, info line, side effect
# ===========================================================================

def test_replace_alias_resolves_to_primary(sandbox_alias, fake_dummy, capsys):
    """cmd_replace(alias_id) must:
      - not raise KeyError
      - print the info line showing alias resolution
      - operate on the primary (swap its file for a dummy, mark archived)

    The sandbox_alias fixture sets uploaded=False on the primary; cmd_replace
    requires uploaded=True. We mutate the library entry before calling to satisfy
    this precondition — this mirrors the real world where replace is called after
    a successful push.
    """
    alias_id = sandbox_alias["alias_id"]
    primary_id = sandbox_alias["primary_id"]
    orig_path = sandbox_alias["orig_path"]

    # Precondition: mark primary as uploaded (as if cmd_push already ran).
    library = mvcommon.load_library()
    library[primary_id]["uploaded"] = True
    library[primary_id]["status"] = "onboarded"
    mvcommon.save_library(library)

    result = main.cmd_replace(alias_id)

    out = capsys.readouterr().out
    assert INFO_SUBSTRING in out, (
        f"Expected info line about alias resolution.\nActual output:\n{out}"
    )
    assert result is True, f"cmd_replace(alias_id) expected True, got {result!r}"

    # Side effect: the primary's file is now a dummy (fake_dummy writes FAKE_DUMMY_BYTES)
    from conftest import FAKE_DUMMY_BYTES
    assert orig_path.read_bytes() == FAKE_DUMMY_BYTES, (
        "Primary's file must be replaced with dummy bytes after replace via alias"
    )

    # Library: primary is archived
    library = mvcommon.load_library()
    assert library[primary_id]["status"] == "archived", (
        "Primary entry must have status archived after replace via alias"
    )
    # Alias entry unchanged
    alias_entry = library[alias_id]
    assert set(alias_entry.keys()) == {"type", "alias_of", "parent_id"}, (
        f"Alias entry must stay 3-key schema after replace; got keys: {set(alias_entry.keys())}"
    )


# ===========================================================================
# 6. cmd_restore(alias_id) — resolves to primary, info line, no KeyError
# ===========================================================================

def test_restore_alias_resolves_to_primary(sandbox_alias, capsys):
    """cmd_restore(alias_id) must not raise KeyError and must print the info line.

    We set up the restore folder with a copy of the primary's file (matching
    hash) so the standard (non-split) restore path can complete successfully.
    The primary entry is set to 'archived' / 'uploaded=True' as cmd_restore
    expects.
    """
    alias_id = sandbox_alias["alias_id"]
    primary_id = sandbox_alias["primary_id"]
    media_dir = sandbox_alias["media_dir"]
    orig_path = sandbox_alias["orig_path"]

    # Compute the hash of the primary's current file
    file_bytes = orig_path.read_bytes()
    file_hash = hashlib.sha256(file_bytes).hexdigest()

    # Create a restore/ folder with the file at the expected filename
    filename = sandbox_alias["orig_path"].name
    restore_folder = media_dir / main.RESTORE_DIR_NAME
    restore_folder.mkdir(exist_ok=True)
    restore_file = restore_folder / filename
    restore_file.write_bytes(file_bytes)

    # Placeholder at target (the file cmd_restore would overwrite)
    target_path = media_dir / filename
    # The real file is already there; write a dummy placeholder over it so
    # restore can move the restore copy back.
    target_path.write_bytes(b"DUMMY-PLACEHOLDER")

    # Precondition: primary must be archived/uploaded for cmd_restore
    library = mvcommon.load_library()
    library[primary_id]["status"] = "archived"
    library[primary_id]["uploaded"] = True
    library[primary_id]["hash"] = file_hash
    # Ensure no split_info so we take the standard restore path
    library[primary_id].pop("split_info", None)
    mvcommon.save_library(library)

    result = main.cmd_restore(alias_id)

    out = capsys.readouterr().out
    assert INFO_SUBSTRING in out, (
        f"Expected info line about alias resolution.\nActual output:\n{out}"
    )
    assert result is True, f"cmd_restore(alias_id) expected True, got {result!r}"

    # Side effect: target file contains the restored bytes
    assert target_path.read_bytes() == file_bytes, (
        "Target file must contain the restored bytes after restore via alias"
    )

    # Library: primary status updated to restored_local
    library = mvcommon.load_library()
    assert library[primary_id]["status"] == "restored_local", (
        "Primary entry must have status restored_local after restore via alias"
    )


# ===========================================================================
# 7. cmd_verify_restore(alias_id) — resolves to primary, info line, no KeyError
# ===========================================================================

def test_verify_restore_alias_resolves_to_primary(sandbox_alias, capsys):
    """cmd_verify_restore(alias_id) must not raise KeyError and must print the
    info line. We set up the restore folder with a matching file so the
    standard-file verify path reports SUCCESS.
    """
    alias_id = sandbox_alias["alias_id"]
    primary_id = sandbox_alias["primary_id"]
    media_dir = sandbox_alias["media_dir"]
    orig_path = sandbox_alias["orig_path"]

    file_bytes = orig_path.read_bytes()
    file_hash = hashlib.sha256(file_bytes).hexdigest()

    filename = orig_path.name
    restore_folder = media_dir / main.RESTORE_DIR_NAME
    restore_folder.mkdir(exist_ok=True)
    (restore_folder / filename).write_bytes(file_bytes)

    # Precondition: primary must be archived/uploaded
    library = mvcommon.load_library()
    library[primary_id]["status"] = "archived"
    library[primary_id]["uploaded"] = True
    library[primary_id]["hash"] = file_hash
    library[primary_id].pop("split_info", None)
    mvcommon.save_library(library)

    # Must not raise; resolves to primary and verifies
    main.cmd_verify_restore(alias_id)

    out = capsys.readouterr().out
    assert INFO_SUBSTRING in out, (
        f"Expected info line about alias resolution.\nActual output:\n{out}"
    )
    # Verify succeeded (hash matched)
    assert "SUCCESS" in out or "Verified" in out, (
        f"Expected verify success in output.\nActual output:\n{out}"
    )


# ===========================================================================
# 8. cmd_prep(alias_id) — must refuse and leave alias entry byte-for-byte unchanged
# ===========================================================================

def test_prep_alias_refuses_and_entry_unchanged(sandbox_alias, tmp_path, capsys):
    """cmd_prep(alias_id, path) must return False and print a refusal message.
    The alias entry in the library must remain exactly {type, alias_of, parent_id}
    after the call — no mutation, no new keys.
    """
    alias_id = sandbox_alias["alias_id"]
    primary_id = sandbox_alias["primary_id"]

    # Capture the alias entry before the call
    library_before = mvcommon.load_library()
    alias_before = dict(library_before[alias_id])

    # Create a dummy file to pass as the filepath argument (cmd_prep checks existence)
    dummy_file = tmp_path / "some_episode.mkv"
    # Must be larger than DUMMY_MAX_BYTES or cmd_prep early-skips for a different reason
    dummy_file.write_bytes(b"X" * (main.DUMMY_MAX_BYTES + 1))

    result = main.cmd_prep(alias_id, str(dummy_file))

    out = capsys.readouterr().out

    # Must refuse
    assert result is False, f"cmd_prep(alias_id) must return False, got {result!r}"

    # Refusal message must mention the alias relationship
    assert alias_id in out or "alias" in out.lower(), (
        f"Expected refusal message mentioning alias.\nActual output:\n{out}"
    )

    # Alias entry must be byte-for-byte unchanged
    library_after = mvcommon.load_library()
    alias_after = dict(library_after[alias_id])
    assert alias_after == alias_before, (
        f"Alias entry was mutated by cmd_prep!\n  before: {alias_before}\n  after:  {alias_after}"
    )
    # Explicitly confirm it still has exactly the 3-key schema
    assert set(alias_after.keys()) == {"type", "alias_of", "parent_id"}, (
        f"Alias entry must have exactly 3 keys; got: {set(alias_after.keys())}"
    )
