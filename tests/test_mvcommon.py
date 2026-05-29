"""Tests for IMP-A1 — the extracted shared module mvcommon.py.

All tests operate in a tmp_path sandbox and monkeypatch the three LIBRARY_*
constants (via the `sandbox` fixture in conftest.py, which after IMP-A1 patches
mvcommon.LIBRARY_* — the authoritative bindings read by load_library/save_library).
They NEVER touch the real C:\\Media files or the real library_*.json.

Covers:
  - round-trip: save_library splits by ID prefix and load_library merges back
    to an equal dict.
  - atomic save: if os.replace fails, save_library re-raises and leaves no .tmp
    orphan (and a pre-existing target file is unchanged).
  - corrupt library: load_library fails LOUDLY (SystemExit) — the unified loud
    contract, and the regression guard for the deliberate mainfetch change.
"""

import json
import os

import pytest

import mvcommon


def test_round_trip(sandbox):
    """save_library splits by prefix; load_library merges back to an equal dict."""
    data = {
        "mov-en-2024-inception": {"status": "local_ready", "uploaded": False},
        "tv-en-2016-strangerthings-s01e01": {"status": "archived", "uploaded": True},
        "ani-ja-2006-deathnote07": {"status": "archived", "uploaded": True},
    }

    mvcommon.save_library(data)

    # Each prefix landed in the correct sandbox file.
    assert sandbox["lib_movies"].exists()
    assert sandbox["lib_series"].exists()
    assert sandbox["lib_anime"].exists()

    mov = json.loads(sandbox["lib_movies"].read_text(encoding="utf-8"))
    tv = json.loads(sandbox["lib_series"].read_text(encoding="utf-8"))
    ani = json.loads(sandbox["lib_anime"].read_text(encoding="utf-8"))
    assert list(mov.keys()) == ["mov-en-2024-inception"]
    assert list(tv.keys()) == ["tv-en-2016-strangerthings-s01e01"]
    assert list(ani.keys()) == ["ani-ja-2006-deathnote07"]

    # Round-trip equality.
    assert mvcommon.load_library() == data


def test_atomic_save_failure_leaves_no_tmp_orphan(sandbox, monkeypatch):
    """If os.replace raises, save_library re-raises and cleans up the temp file."""
    lib_dir = sandbox["lib_movies"].parent

    # Pre-existing target with known content that must stay unchanged.
    original = {"mov-en-2000-original": {"status": "archived"}}
    sandbox["lib_movies"].write_text(json.dumps(original), encoding="utf-8")

    def _boom(src, dst):
        raise OSError("simulated replace failure")

    monkeypatch.setattr(mvcommon.os, "replace", _boom)

    with pytest.raises(OSError):
        mvcommon.save_library({"mov-en-2024-new": {"status": "local_ready"}})

    # No .tmp orphan left behind in the library dir.
    leftovers = [p for p in os.listdir(lib_dir) if p.endswith(".tmp")]
    assert leftovers == [], f"orphaned temp files: {leftovers}"

    # The pre-existing target file is unchanged (os.replace never committed).
    assert json.loads(sandbox["lib_movies"].read_text(encoding="utf-8")) == original


def test_corrupt_library_fails_loud(sandbox):
    """A corrupt library makes load_library exit loudly (SystemExit) — the
    unified loud contract that replaces mainfetch's old silent-zero-entries."""
    sandbox["lib_movies"].write_text("{ this is not valid json", encoding="utf-8")
    sandbox["lib_series"].write_text("{}", encoding="utf-8")
    sandbox["lib_anime"].write_text("{}", encoding="utf-8")

    with pytest.raises(SystemExit):
        mvcommon.load_library()
