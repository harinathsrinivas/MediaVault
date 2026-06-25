"""Focused tests for `cmd_set_tmdb` — the manual metadata.tmdb_id override
(IMP-E3 / U3 / D17, Phase 5 step 5.1).

`metadata.tmdb_id` is an OPTIONAL leaf sub-field: every existing entry lacks it,
and the override is a pure ZERO-BYTE JSON edit (mirrors cmd_set_search) — it must
NOT touch the media file or rehash it. These tests assert:

  - on a seeded leaf, cmd_set_tmdb sets metadata.tmdb_id as an int and it PERSISTS
    through save_library/load_library;
  - an all-digits string is stored as an int (TMDB ids are integers), a non-digit
    string is stored as-is (leniency);
  - a missing id is handled gracefully (no crash, no library mutation);
  - the on-disk media bytes and the entry's stored `hash` are UNCHANGED (no rehash);
  - alias/season_map shape-safety: a set_tmdb on a multi_ep_alias lands on its
    PRIMARY leaf (the alias's 3-key shape is preserved), and a season_map container
    is refused (its shape is preserved).

Constraints (docs/testing-strategy.md): uses the `sandbox` / `sandbox_alias`
fixtures, which are LOCAL_ROOT-hermetic and hard-guard against real C:\\Media.
Never touches real C:\\Media or real library_*.json.
"""
import hashlib
import json

import main
import mvcommon


LEAF_ID = "mov-en-2010-inception"


def _seed_leaf(sandbox, *, with_metadata=True):
    """Seed LIBRARY_MOVIES with a single leaf carrying a real on-disk file and a
    known hash. Returns (filepath, original_bytes, original_hash)."""
    media_dir = sandbox["media_dir"]
    filename = "inception.mkv"
    filepath = media_dir / filename
    original_bytes = b"INCEPTION-MASTER-BYTES\n" * 50
    filepath.write_bytes(original_bytes)
    original_hash = hashlib.sha256(original_bytes).hexdigest()

    entry = {
        "folder_path": str(media_dir),
        "filename": filename,
        "status": "local_ready",
        "uploaded": False,
        "hash": original_hash,
    }
    if with_metadata:
        # A leaf typically already carries a metadata dict (parse_metadata_from_id).
        entry["metadata"] = {"title": "Inception", "year": 2010}

    mvcommon.save_library({LEAF_ID: entry})
    return filepath, original_bytes, original_hash


def test_set_tmdb_sets_int_on_leaf_and_persists(sandbox):
    """A numeric tmdb id is stored as an int under metadata.tmdb_id and survives a
    reload from disk (the edit is persisted via save_library)."""
    _seed_leaf(sandbox)

    main.cmd_set_tmdb(LEAF_ID, "27205")  # Inception's real TMDB id, as a CLI string

    reloaded = mvcommon.load_library()
    tmdb = reloaded[LEAF_ID]["metadata"]["tmdb_id"]
    assert tmdb == 27205
    assert isinstance(tmdb, int), f"expected int, got {type(tmdb).__name__}"


def test_set_tmdb_digits_string_stored_as_int(sandbox):
    """An all-digits string argument is coerced to int (TMDB ids are integers)."""
    _seed_leaf(sandbox)

    main.cmd_set_tmdb(LEAF_ID, "603")

    reloaded = mvcommon.load_library()
    assert reloaded[LEAF_ID]["metadata"]["tmdb_id"] == 603
    assert isinstance(reloaded[LEAF_ID]["metadata"]["tmdb_id"], int)


def test_set_tmdb_creates_metadata_when_absent(sandbox):
    """setdefault must create the metadata dict if the leaf has none, without
    disturbing the rest of the entry."""
    _seed_leaf(sandbox, with_metadata=False)

    main.cmd_set_tmdb(LEAF_ID, "1234")

    reloaded = mvcommon.load_library()
    assert reloaded[LEAF_ID]["metadata"] == {"tmdb_id": 1234}
    # The leaf's other keys are untouched.
    assert reloaded[LEAF_ID]["status"] == "local_ready"
    assert reloaded[LEAF_ID]["filename"] == "inception.mkv"


def test_set_tmdb_non_digit_string_stored_as_is(sandbox):
    """Leniency: a non-digit value is stored verbatim as a string rather than
    crashing on int()."""
    _seed_leaf(sandbox)

    main.cmd_set_tmdb(LEAF_ID, "tt1375666")  # an IMDB-style id (not all digits)

    reloaded = mvcommon.load_library()
    assert reloaded[LEAF_ID]["metadata"]["tmdb_id"] == "tt1375666"


def test_set_tmdb_missing_id_is_graceful(sandbox, capsys):
    """A non-existent id must NOT crash and must NOT mutate the library."""
    _seed_leaf(sandbox)
    before = mvcommon.load_library()

    main.cmd_set_tmdb("mov-does-not-exist", "999")  # must not raise

    out = capsys.readouterr().out
    assert "not found" in out.lower()
    after = mvcommon.load_library()
    assert after == before, "library must be unchanged after a missing-id set_tmdb"


def test_set_tmdb_does_not_rehash_or_touch_media(sandbox):
    """The override is a zero-byte JSON edit: the media file's bytes and the
    entry's stored hash are both UNCHANGED (no rehash, no file touch)."""
    filepath, original_bytes, original_hash = _seed_leaf(sandbox)

    main.cmd_set_tmdb(LEAF_ID, "27205")

    # File bytes on disk are byte-for-byte identical (no media touch).
    assert filepath.read_bytes() == original_bytes
    # The stored hash field is unchanged (no rehash was performed).
    reloaded = mvcommon.load_library()
    assert reloaded[LEAF_ID]["hash"] == original_hash
    # And it still matches the file (belt-and-suspenders: nothing recomputed/wrote).
    assert hashlib.sha256(filepath.read_bytes()).hexdigest() == original_hash


def test_set_tmdb_on_alias_targets_primary_leaf(sandbox_alias):
    """set_tmdb on a multi_ep_alias id resolves to the PRIMARY leaf: the tmdb_id
    lands on the primary's metadata and the alias's exact 3-key shape is preserved
    (no metadata added to the alias)."""
    alias_id = sandbox_alias["alias_id"]
    primary_id = sandbox_alias["primary_id"]

    main.cmd_set_tmdb(alias_id, "94605")

    reloaded = mvcommon.load_library()
    # The id landed on the resolved primary leaf.
    assert reloaded[primary_id]["metadata"]["tmdb_id"] == 94605
    # The alias keeps its exact schema — NOTHING added, no metadata, no tmdb_id.
    assert set(reloaded[alias_id]) == {"type", "alias_of", "parent_id"}
    assert reloaded[alias_id]["type"] == "multi_ep_alias"


def test_set_tmdb_refuses_season_map(sandbox_alias, capsys):
    """A season_map is a virtual container, not a leaf: set_tmdb refuses it and
    leaves its shape untouched (no metadata key added)."""
    season_id = sandbox_alias["season_id"]
    before = json.dumps(mvcommon.load_library()[season_id], sort_keys=True)

    main.cmd_set_tmdb(season_id, "1234")  # must not raise

    out = capsys.readouterr().out
    assert "leaf" in out.lower()  # the refusal message mentions it targets a leaf
    after = json.dumps(mvcommon.load_library()[season_id], sort_keys=True)
    assert after == before, "season_map shape must be unchanged after a refused set_tmdb"
    assert "metadata" not in mvcommon.load_library()[season_id]
