"""
Unit tests for cmd_prep_season episode-ID derivation.

Constraints:
Never touch real C:\\Media files or real library_*.json.
Run `pytest -q` and fix failures before marking this step done.
"""

import main
import mvcommon


# ---------------------------------------------------------------------------
# Test A — dotted-title SxxExx (the bug fix)
# Filename: Fringe.S03E20.6.02.AM.EST...
# Before fix: regex r"[sS]\d+[eE](\d+(?:\.\d+)?)" captured "20.6" -> wrong key
# After fix:  regex r"[sS]\d+[eE](\d+)"           captures "20"   -> correct key
# ---------------------------------------------------------------------------

def test_dotted_title_sxxexx_key(sandbox, stub_tech_specs, tmp_path):
    """Test A: Fringe.S03E20.6.02... yields e20 (not e20.6 from old regex)."""
    fringe_folder = tmp_path / "series" / "fringe_s03"
    fringe_folder.mkdir(parents=True)

    mkv = fringe_folder / "Fringe.S03E20.6.02.AM.EST.2011.1080p.BluRay.REMUX.AVC.DTS-HD.MA.5.1-NOGRP.mkv"
    mkv.write_bytes(b"x" * 210_000)  # must exceed DUMMY_MAX_BYTES (200_000)

    # Initialise the library files that sandbox provides
    sandbox["lib_movies"].write_text("{}", encoding="utf-8")
    sandbox["lib_series"].write_text("{}", encoding="utf-8")
    sandbox["lib_anime"].write_text("{}", encoding="utf-8")

    main.cmd_prep_season("tv-en-2010-fringe-s03", str(fringe_folder))

    lib = mvcommon.load_library()

    assert "tv-en-2010-fringe-s03e20" in lib, \
        "Expected key tv-en-2010-fringe-s03e20 to exist after prep"
    assert "tv-en-2010-fringe-s03e20.6" not in lib, \
        "Old wrong key tv-en-2010-fringe-s03e20.6 must NOT exist after fix"


# ---------------------------------------------------------------------------
# Test B — canonical SxxExx
# ---------------------------------------------------------------------------

def test_canonical_sxxexx_key(sandbox, stub_tech_specs, tmp_path):
    """Test B: Fringe.S03E19.1080p.BluRay.mkv yields e19."""
    fringe_folder = tmp_path / "series" / "fringe_s03_b"
    fringe_folder.mkdir(parents=True)

    mkv = fringe_folder / "Fringe.S03E19.1080p.BluRay.mkv"
    mkv.write_bytes(b"x" * 210_000)  # must exceed DUMMY_MAX_BYTES (200_000)

    sandbox["lib_movies"].write_text("{}", encoding="utf-8")
    sandbox["lib_series"].write_text("{}", encoding="utf-8")
    sandbox["lib_anime"].write_text("{}", encoding="utf-8")

    main.cmd_prep_season("tv-en-2010-fringe-s03", str(fringe_folder))

    lib = mvcommon.load_library()

    assert "tv-en-2010-fringe-s03e19" in lib, \
        "Expected key tv-en-2010-fringe-s03e19 to exist after prep"


# ---------------------------------------------------------------------------
# Test C — anime NxYY half-episode (NxYY line 891 preserves decimal)
# ---------------------------------------------------------------------------

def test_anime_nxyy_half_episode_key(sandbox, stub_tech_specs, tmp_path):
    """Test C: [Grp] Show 16x05.5 [hash].mkv yields ani-test-show05.5."""
    anime_folder = tmp_path / "anime" / "show"
    anime_folder.mkdir(parents=True)

    mkv = anime_folder / "[Grp] Show 16x05.5 [hash].mkv"
    mkv.write_bytes(b"x" * 210_000)  # must exceed DUMMY_MAX_BYTES (200_000)

    sandbox["lib_movies"].write_text("{}", encoding="utf-8")
    sandbox["lib_series"].write_text("{}", encoding="utf-8")
    sandbox["lib_anime"].write_text("{}", encoding="utf-8")

    main.cmd_prep_season("ani-test-show", str(anime_folder))

    lib = mvcommon.load_library()

    assert "ani-test-show05.5" in lib, \
        "Expected key ani-test-show05.5 to exist — NxYY decimal must be preserved"
