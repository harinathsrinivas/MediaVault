"""Tests for IMP-R2 — the `recover` CLI subcommand (cmd_recover).

Covers:
  - id resolution -> folder lookup
  - direct folder-path resolution
  - unknown id / missing folder error path
  - crossed-PONR journal left in place (no destructive action)
  - --scan mode: read-only walk, counts both pre- and post-PONR journals
"""

import json
import pytest
import main


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------

def _write_journal(folder, crossed, records):
    """Write a minimal .mediavault_txn.json to folder."""
    data = {
        "manual_id": "test-id",
        "crossed_ponr": crossed,
        "records": records,
    }
    jpath = folder / main.TXN_JOURNAL_NAME
    jpath.write_text(json.dumps(data), encoding="utf-8")
    return jpath


def _seed_library(sandbox, entry_id, folder_path):
    """Seed lib_movies with one entry; write empty dicts to series + anime."""
    entry = {
        entry_id: {
            "folder_path": str(folder_path),
            "type": "movie",
            "status": "onboarded",
        }
    }
    sandbox["lib_movies"].write_text(json.dumps(entry), encoding="utf-8")
    sandbox["lib_series"].write_text("{}", encoding="utf-8")
    sandbox["lib_anime"].write_text("{}", encoding="utf-8")


# ---------------------------------------------------------------------------
# Test 1: id resolution
# ---------------------------------------------------------------------------

def test_id_resolution(sandbox, capsys, monkeypatch):
    entry_id = "mov-test-r2-001"
    _seed_library(sandbox, entry_id, sandbox["media_dir"])
    jpath = _write_journal(sandbox["media_dir"], crossed=False, records=[])

    main.cmd_recover(entry_id)

    out = capsys.readouterr().out
    assert "Resolved id 'mov-test-r2-001'" in out
    assert not jpath.exists(), "Journal should be removed after successful pre-PONR recovery"


# ---------------------------------------------------------------------------
# Test 2: direct path resolution
# ---------------------------------------------------------------------------

def test_direct_path_resolution(sandbox, capsys):
    _seed_library(sandbox, "mov-test-r2-002", sandbox["media_dir"])
    jpath = _write_journal(sandbox["media_dir"], crossed=False, records=[])

    main.cmd_recover(str(sandbox["media_dir"]))

    assert not jpath.exists(), "Journal should be removed after direct-path recovery"
    out = capsys.readouterr().out
    assert "recover finished" in out


# ---------------------------------------------------------------------------
# Test 3: unknown id / missing folder
# ---------------------------------------------------------------------------

def test_unknown_id_missing_folder(sandbox, capsys):
    _seed_library(sandbox, "mov-test-r2-real", sandbox["media_dir"])

    # This id is NOT in the library; "mov-does-not-exist-xyz" also fails the
    # isdir() check since it is not a real path.
    result = main.cmd_recover("mov-does-not-exist-xyz")

    assert result is None or not result
    out = capsys.readouterr().out
    assert "No such media folder / unknown id" in out


# ---------------------------------------------------------------------------
# Test 4: crossed-PONR journal — declined (no destructive action)
# ---------------------------------------------------------------------------

def test_crossed_ponr_journal_declined(sandbox, capsys):
    _seed_library(sandbox, "mov-test-r2-003", sandbox["media_dir"])
    jpath = _write_journal(sandbox["media_dir"], crossed=True, records=[])

    result = main.cmd_recover(str(sandbox["media_dir"]))

    assert jpath.exists(), "Crossed-PONR journal must remain untouched"
    assert not result, "recover_journal should return False for crossed journals"


# ---------------------------------------------------------------------------
# Test 5: --scan mode (read-only walk)
# ---------------------------------------------------------------------------

def test_scan_read_only(sandbox, capsys, monkeypatch):
    # sandbox["media_dir"] = tmp_path / "Media" / "Movies" / "TestMovie"
    # We want LOCAL_ROOT = tmp_path / "Media" so os.path.join(LOCAL_ROOT, "Movies")
    # resolves to the real sandbox Movies directory.
    local_root = str(sandbox["media_dir"].parent.parent)  # tmp_path / "Media"
    assert "C:\\Media" not in local_root, "Safety guard: must not reference real media root"
    monkeypatch.setattr(main, "LOCAL_ROOT", local_root)

    _seed_library(sandbox, "mov-test-r2-004", sandbox["media_dir"])

    # Pre-PONR journal in the existing TestMovie folder
    jpath_pre = _write_journal(sandbox["media_dir"], crossed=False, records=[])

    # Crossed journal in a second subfolder
    another = sandbox["media_dir"].parent / "AnotherMovie"
    another.mkdir()
    jpath_crossed = _write_journal(another, crossed=True, records=[])

    found = main.cmd_recover(scan=True)

    assert found == 2, f"Expected 2 journals found, got {found}"

    # Scan is read-only — neither journal should be removed
    assert jpath_pre.exists(), "Scan must not remove pre-PONR journals"
    assert jpath_crossed.exists(), "Scan must not remove crossed journals"

    out = capsys.readouterr().out
    assert "crossed_ponr" in out
    assert str(sandbox["media_dir"]) in out or "TestMovie" in out
    assert str(another) in out or "AnotherMovie" in out
