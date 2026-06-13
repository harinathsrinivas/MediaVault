"""Entry-type schema registry round-trip + whole-library consumer-safety guard
(IMP-H3 / Part B-2 — the enforced data-shape guardrail).

This is the lightest ENFORCED mechanism that catches the PR #21 / IMP-E13 class of
bug: a new top-level library entry type (`multi_ep_alias`) was added without
updating a whole-library iterator (`cmd_scan_unprepped`), which then blindly
dereferenced a physical-only key (`entry['folder_path']`) on the alias and crashed
with KeyError. A memory NOTE predicted that class but a note is not enforcement —
this test is.

`main.ENTRY_TYPE_KEYS` is the single source of truth for "what keys does each
top-level entry type have" and whether it owns a physical file. The two tests:

  (a) round_trip  — every type registered in ENTRY_TYPE_KEYS survives
                    save_library -> load_library with no key loss / type drift.
  (b) GUARD       — a library containing one entry of EACH non-physical type
                    (season_map, multi_ep_alias) plus a leaf is fed to every
                    whole-library READ command (cmd_scan_unprepped,
                    cmd_local_status, cmd_sort); each must COMPLETE WITHOUT
                    RAISING. The moment a new (or changed) iterator blindly
                    dereferences a physical-only key on a non-physical entry,
                    this test fails. Demonstrated: reverting cmd_scan_unprepped's
                    multi_ep_alias skip makes (b) fail with KeyError: 'folder_path'.

When you add or change an entry type, update `ENTRY_TYPE_KEYS` in main.py AND add
it to this guard's non-physical set; every whole-library iterator must tolerate
every non-physical type (skip it, or _resolve_alias it — never blindly deref a
physical-only key).

Constraints (docs/testing-strategy.md): uses the `sandbox` fixture, which is
LOCAL_ROOT-hermetic (redirects BOTH mvcommon.* and main.* LIBRARY_*/LOCAL_ROOT
into a temp dir). Never touches real C:\\Media or real library_*.json.
"""
import os

import main
import mvcommon


# ---------------------------------------------------------------------------
# Helpers: build a minimal, schema-faithful entry for a given registered type.
# ---------------------------------------------------------------------------
# Every id here uses the "tv-" prefix so mvcommon.save_library routes them all
# into library_series.json (one file, so the merged load returns them together).
SEASON_ID  = "tv-en-2009-bsg-s04"
PRIMARY_ID = "tv-en-2009-bsg-s04e19"
ALIAS_ID   = "tv-en-2009-bsg-s04e20"


def _leaf_entry(folder_path, filename):
    """A leaf with EXACTLY the registry's `required` keys for "leaf"
    (folder_path / filename / status). Leaves carry NO `type` key — a missing
    `type` IS a leaf (documented on ENTRY_TYPE_KEYS). uploaded=False so
    cmd_local_status renders it as a pending row (exercising filename[:40] —
    the line that TypeErrors on an alias's None filename if it isn't skipped)."""
    return {
        "folder_path": folder_path,
        "filename": filename,
        "status": "local_ready",
        "uploaded": False,
    }


def _entry_for_type(entry_type, folder_path):
    """Build a minimal entry carrying exactly the `required` keys ENTRY_TYPE_KEYS
    declares for `entry_type`, plus the `type` discriminator for non-leaf types.

    Driven off the registry so a newly-registered type forces this builder (and
    therefore the round-trip test) to account for its required keys."""
    required = main.ENTRY_TYPE_KEYS[entry_type]["required"]
    if entry_type == "leaf":
        # Leaf is the implicit no-`type` entry; its required keys are physical.
        return _leaf_entry(folder_path, "leaf_episode.mkv")
    if entry_type == "season_map":
        return {"type": "season_map", "folder_path": folder_path,
                "children": sorted([PRIMARY_ID, ALIAS_ID])}
    if entry_type == "multi_ep_alias":
        return {"type": "multi_ep_alias", "alias_of": PRIMARY_ID,
                "parent_id": SEASON_ID}
    # A type was added to ENTRY_TYPE_KEYS but not to this builder. Fail loudly
    # rather than silently — the round-trip test must cover EVERY registered type.
    raise AssertionError(
        f"ENTRY_TYPE_KEYS has type '{entry_type}' (required={required}) with no "
        f"builder in test_entry_schema_guard.py — add one so the guard covers it."
    )


# ===========================================================================
# (a) ROUND-TRIP — every registered type survives save_library/load_library.
# ===========================================================================

def test_every_entry_type_round_trips(sandbox):
    """Build one entry of EACH type in ENTRY_TYPE_KEYS, save, reload, and assert
    each entry survives intact: no required key is lost and the `type`
    discriminator is preserved (leaf stays type-less, the others keep their
    `type`). Iterating the registry binds this test to it — a new entry type
    cannot be added without round-tripping it here."""
    folder_path = str(sandbox["media_dir"])

    # One id per registered type, all "tv-" so they land in library_series.json.
    ids_by_type = {
        "leaf":           PRIMARY_ID,
        "season_map":     SEASON_ID,
        "multi_ep_alias": ALIAS_ID,
    }
    # Guard: the id map must cover exactly the registered types (catches a new
    # type being registered without a representative id here).
    assert set(ids_by_type) == set(main.ENTRY_TYPE_KEYS), (
        "ids_by_type is out of sync with main.ENTRY_TYPE_KEYS — add the new type "
        "(see the file's top-of-module guidance)."
    )

    library = {
        ids_by_type[t]: _entry_for_type(t, folder_path)
        for t in main.ENTRY_TYPE_KEYS
    }
    mvcommon.save_library(library)

    reloaded = mvcommon.load_library()

    for entry_type, mid in ids_by_type.items():
        assert mid in reloaded, f"{entry_type} entry '{mid}' lost on round-trip"
        before = library[mid]
        after = reloaded[mid]

        # No key loss / mutation: the entry must survive byte-for-byte.
        assert after == before, (
            f"{entry_type} entry '{mid}' changed on round-trip: "
            f"{before!r} -> {after!r}"
        )

        # Type discriminator preserved (and leaf stays implicit / type-less).
        spec = main.ENTRY_TYPE_KEYS[entry_type]
        if entry_type == "leaf":
            assert "type" not in after, "leaf must remain the implicit no-`type` entry"
        else:
            assert after.get("type") == entry_type, (
                f"type discriminator lost for '{mid}': {after.get('type')!r}"
            )

        # Every declared `required` key survived.
        missing = spec["required"] - set(after)
        assert not missing, (
            f"{entry_type} entry '{mid}' lost required keys {missing} on round-trip"
        )


# ===========================================================================
# (b) GUARD — whole-library READ commands tolerate every non-physical type.
# ===========================================================================
# Derived FROM the registry so it auto-extends: any type registered as
# physical=False must be tolerated by every whole-library iterator. Adding a new
# non-physical type to ENTRY_TYPE_KEYS automatically widens this set (and the
# fixture below seeds one of each), so the new type is exercised by the guard
# without editing the assertions.
NON_PHYSICAL_TYPES = sorted(
    t for t, spec in main.ENTRY_TYPE_KEYS.items() if not spec["physical"]
)


def _seed_guard_library(sandbox):
    """Seed a Series library with ONE real leaf + one entry of EACH non-physical
    type (season_map, multi_ep_alias, plus any future physical=False type). The
    leaf's file is created on disk under the sandbox media dir so the iterators
    have a real physical entry to process alongside the virtual ones."""
    # Real leaf file under the sandbox (LOCAL_ROOT-hermetic) media dir.
    media_dir = sandbox["media_dir"]
    leaf_filename = "leaf_episode.mkv"
    leaf_path = media_dir / leaf_filename
    leaf_path.write_bytes(b"LEAF-EPISODE-BYTES\n" * 100)

    library = {PRIMARY_ID: _leaf_entry(str(media_dir), leaf_filename)}

    # One entry of each non-physical type. (Today: season_map + multi_ep_alias.)
    for entry_type in NON_PHYSICAL_TYPES:
        if entry_type == "season_map":
            library[SEASON_ID] = _entry_for_type("season_map", str(media_dir))
        elif entry_type == "multi_ep_alias":
            library[ALIAS_ID] = _entry_for_type("multi_ep_alias", str(media_dir))
        else:
            # A future non-physical type was registered without seeding it here.
            # Fail loudly so the guard genuinely exercises EVERY non-physical type.
            raise AssertionError(
                f"non-physical type '{entry_type}' is in ENTRY_TYPE_KEYS but not "
                f"seeded by _seed_guard_library — add it (top-of-module guidance)."
            )

    mvcommon.save_library(library)
    return library


def test_guard_read_commands_tolerate_non_physical_entries(sandbox, capsys):
    """THE load-bearing guard. A library holding one entry of every non-physical
    type (season_map, multi_ep_alias) + a leaf is fed to every whole-library READ
    command; each must COMPLETE WITHOUT RAISING.

    This is the test that fails the moment any whole-library iterator blindly
    dereferences a physical-only key (folder_path / filename) on a non-physical
    entry — i.e. it catches the PR #21 / IMP-E13 KeyError class. Demonstrated to
    FAIL (KeyError: 'folder_path') if cmd_scan_unprepped's multi_ep_alias skip is
    reverted; see tests/test_entry_schema_guard.py / STATUS.md Step B2."""
    # Sanity: there IS at least one non-physical type to guard against, and the
    # registry stays in sync with the documented set the guard exercises.
    assert NON_PHYSICAL_TYPES, "ENTRY_TYPE_KEYS must declare at least one non-physical type"
    assert NON_PHYSICAL_TYPES == ["multi_ep_alias", "season_map"], (
        "the guard's non-physical set drifted from ENTRY_TYPE_KEYS — update both "
        "(top-of-module guidance). Got: " + repr(NON_PHYSICAL_TYPES)
    )

    _seed_guard_library(sandbox)

    # Each of these iterates the WHOLE merged library. None may raise on a
    # season_map or multi_ep_alias entry. capsys swallows their stdout.
    main.cmd_scan_unprepped()
    main.cmd_local_status()
    main.cmd_local_status("40gb")  # also exercise the size-limit branch
    main.cmd_sort()

    # Drain captured output (so a failure trace isn't polluted by command noise).
    capsys.readouterr()

    # Belt-and-suspenders: the alias entry must still be intact in the library
    # after the read commands run (cmd_sort rewrites the library — it must not
    # drop or corrupt the non-physical entries).
    reloaded = mvcommon.load_library()
    assert ALIAS_ID in reloaded and reloaded[ALIAS_ID].get("type") == "multi_ep_alias"
    assert SEASON_ID in reloaded and reloaded[SEASON_ID].get("type") == "season_map"
    assert PRIMARY_ID in reloaded and "type" not in reloaded[PRIMARY_ID]
