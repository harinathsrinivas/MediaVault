"""Tests for cmd_repair_dummies — Bug 1 atomic-swap regression guard + alias skip.

Two test functions:
  1. test_repair_dummies_atomic_swap — seeds an archived entry with a tiny dummy,
     runs cmd_repair_dummies, asserts the regenerated file landed (via atomic
     os.replace), asserts no .repair_tmp orphan, and guards that os.remove was NOT
     called on the current_path (regression: remove+rename would re-introduce a
     no-file window).
  2. test_repair_dummies_skips_alias — seeds a multi_ep_alias entry alongside the
     above; asserts cmd_repair_dummies runs clean (no KeyError) and never touches
     the alias entry (the explicit `if entry.get("type") == "multi_ep_alias": continue`
     added in Step 1).
"""
import json
import os

import pytest

import main
import mvcommon

# Mirror conftest's constant so the bytes assertion is unambiguous.
from conftest import FAKE_DUMMY_BYTES

# Entry id with "mov" prefix -> routed to LIBRARY_MOVIES by load_library/save_library.
_ENTRY_ID = "mov-repair-test-001"
_FILENAME = "test_repair.mkv"

# A multi_ep_alias entry that carries only the mandatory 3 keys (no status/folder_path).
_ALIAS_ID = "mov-repair-alias-001"
_ALIAS_ENTRY = {
    "type": "multi_ep_alias",
    "alias_of": _ENTRY_ID,
    "parent_id": "mov-repair-season-001",
}


def _seed_archived_entry(sandbox):
    """Write a tiny file (<DUMMY_MAX_BYTES) and a matching archived library entry.

    Returns current_path (Path) and media_dir (Path) for assertion use.
    """
    media_dir = sandbox["media_dir"]
    current_path = media_dir / _FILENAME
    current_path.write_bytes(b"tiny")  # < DUMMY_MAX_BYTES -> qualifies for regeneration

    entry = {
        _ENTRY_ID: {
            "status": "archived",
            "uploaded": True,
            "folder_path": str(media_dir),
            "filename": _FILENAME,
            "type": "movie",
        }
    }
    sandbox["lib_movies"].write_text(json.dumps(entry), encoding="utf-8")
    sandbox["lib_series"].write_text("{}", encoding="utf-8")
    sandbox["lib_anime"].write_text("{}", encoding="utf-8")

    return current_path, media_dir


def test_repair_dummies_atomic_swap(sandbox, fake_dummy, monkeypatch):
    """cmd_repair_dummies must:
      - regenerate the tiny archived file via the fake_dummy stub,
      - land the result at current_path (atomic os.replace),
      - leave no .repair_tmp orphan in the media dir,
      - NOT call os.remove(current_path) (regression guard: remove+rename would
        re-introduce a no-file window between deletion and rename).
    """
    current_path, media_dir = _seed_archived_entry(sandbox)

    # Spy on os.remove: record which paths were removed, but still delegate to
    # the real os.remove so unrelated cleanup (e.g. temp files) stays intact.
    _real_remove = os.remove
    removed_paths = []

    def _spy_remove(path):
        removed_paths.append(os.path.abspath(path))
        return _real_remove(path)

    monkeypatch.setattr(main.os, "remove", _spy_remove)

    main.cmd_repair_dummies()

    # 1. The regenerated file is in place and contains the expected bytes.
    assert current_path.exists(), f"regenerated file missing: {current_path}"
    assert current_path.read_bytes() == FAKE_DUMMY_BYTES, (
        "regenerated file does not contain FAKE_DUMMY_BYTES"
    )

    # 2. No .repair_tmp orphan left behind.
    orphans = list(media_dir.glob("*.repair_tmp*"))
    assert orphans == [], f"unexpected .repair_tmp orphan(s): {orphans}"

    # 3. Regression guard: current_path must NOT appear in the removed-paths log.
    #    A future revert to os.remove(current_path) + os.rename(...) re-introduces
    #    the no-file window and will trip this assertion.
    abs_current = os.path.abspath(str(current_path))
    assert abs_current not in removed_paths, (
        f"cmd_repair_dummies called os.remove({current_path!r}) — "
        "this re-introduces the no-file window; use os.replace instead."
    )


def test_repair_dummies_skips_alias(sandbox, fake_dummy):
    """cmd_repair_dummies must skip multi_ep_alias entries cleanly (no KeyError).

    Seeds both an archived movie entry AND a multi_ep_alias entry into the same
    library, then asserts:
      - cmd_repair_dummies completes without raising,
      - the alias entry is untouched in the library (still type=multi_ep_alias),
      - the alias has no unexpected keys added (it still carries only the 3 mandatory
        keys the Step 1 skip guards against accidentally dereferencing).
    """
    current_path, media_dir = _seed_archived_entry(sandbox)

    # Add the alias entry alongside the regular archived entry in movies lib.
    existing = json.loads(sandbox["lib_movies"].read_text(encoding="utf-8"))
    existing[_ALIAS_ID] = dict(_ALIAS_ENTRY)
    sandbox["lib_movies"].write_text(json.dumps(existing), encoding="utf-8")

    # Must not raise (no KeyError on the alias's missing status/folder_path/filename).
    main.cmd_repair_dummies()

    # Alias entry in library is untouched.
    lib = mvcommon.load_library()
    assert _ALIAS_ID in lib, "alias entry disappeared from library after repair_dummies"
    alias_entry = lib[_ALIAS_ID]
    assert alias_entry["type"] == "multi_ep_alias"
    assert alias_entry["alias_of"] == _ENTRY_ID
    assert "status" not in alias_entry, (
        "repair_dummies added a 'status' key to the alias entry — it should be skipped entirely"
    )
    assert "folder_path" not in alias_entry, (
        "repair_dummies added a 'folder_path' key to the alias entry — it should be skipped"
    )
