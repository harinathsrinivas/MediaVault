"""Entry-type schema registry round-trip + whole-library consumer-safety guard
(IMP-H3 / Part B-2 — the enforced data-shape guardrail).

This is the lightest ENFORCED mechanism that catches the PR #21 / IMP-E13 class of
bug: a new top-level library entry type (`multi_ep_alias`) was added without
updating a whole-library iterator (`cmd_scan_unprepped`), which then blindly
dereferenced a physical-only key (`entry['folder_path']`) on the alias and crashed
with KeyError. A memory NOTE predicted that class but a note is not enforcement —
this test is.

`main.ENTRY_TYPE_KEYS` is the single source of truth for "what keys does each
top-level entry type have" and whether it owns a physical file. The tests:

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
  (c) EXTRAS      — (IMP-D19) the optional nested `extras` block (Card A2,
                    documented on ENTRY_TYPE_KEYS: no new type, in NO
                    `required` set) attached to BOTH title shapes — a movie
                    leaf and a season_map — round-trips byte-for-byte through
                    save_library/load_library, and every whole-library READ
                    command (plus collect_reclaimable, the Step-7 extras-aware
                    consumer) completes without raising AND without corrupting
                    the nested block on an extras-bearing library.
                    `_extras_block()` below is THE canonical representative
                    shape — faithful to what the lifecycle code actually
                    writes — for other fixtures/tests to mirror.
  (d) GPHOTOS     — (IMP-C25) the optional nested `gphotos` identity block
                    (PLAN P-1, documented on ENTRY_TYPE_KEYS: no new type, in
                    NO `required` set) on EVERY uploaded object — an unsplit
                    leaf, split chunks + a FLAC holder, extras whole items and
                    split items' chunks — across all four prefixes: the
                    registry literal stays untouched; THE canonical
                    `_gphotos_block()` carries every P-1 key; blocks round-trip
                    byte-for-byte and sit exactly where mvcommon's object
                    inventory looks for them; and every whole-library READ
                    command (scan_unprepped, local_status, sort,
                    collect_reclaimable, items_payload, verify_library,
                    repair_dummies, build_tree) completes, mutates nothing and
                    behaves EXACTLY as on the same library without blocks.

When you add or change an entry type, update `ENTRY_TYPE_KEYS` in main.py AND add
it to this guard's non-physical set; every whole-library iterator must tolerate
every non-physical type (skip it, or _resolve_alias it — never blindly deref a
physical-only key).

Constraints (docs/testing-strategy.md): uses the `sandbox` fixture, which is
LOCAL_ROOT-hermetic (redirects BOTH mvcommon.* and main.* LIBRARY_*/LOCAL_ROOT
into a temp dir). Never touches real C:\\Media or real library_*.json.
"""
import hashlib
import json
import os
from datetime import datetime, timezone

import pytest

import gpweb
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


# ===========================================================================
# (c) EXTRAS — the optional nested `extras` block (IMP-D19, Card A2).
# ===========================================================================
# `extras` is NOT an entry type: it is an optional nested field on a TITLE
# entry (the movie leaf, or the season_map for series/anime/others), exactly
# like `split_info`/`metadata` — documented on ENTRY_TYPE_KEYS. These tests are
# the schema tripwire for that contract: any future change that breaks the A2
# grouped shape's round-trip, or makes a whole-library iterator choke on an
# extras-bearing entry, fails HERE instead of shipping.

MOVIE_TITLE_ID = "mov-en-2024-guardmovie"  # "mov-" prefix -> library_movies.json


def _extras_block(title_id):
    """THE canonical representative `extras` block (Card A2 — grouped per
    source folder), faithful to what the lifecycle code ACTUALLY writes.
    Step 9's `sandbox_extras` fixture and future tests should mirror this.

    Field provenance (verified against main.py):
      - scan_extras_folders: filename / sub_rel (posix, may be nested) /
        short_id (generate_short_id(f"{title_id}::{group_rel}/{sub_rel}")) /
        hash (sha256 hex) / status="local_ready" / uploaded=False /
        search_term (f"{name} [{short_id}]{ext}") / tech_spec; items sorted by
        sub_rel within each group; merge_extras_into_title stamps the group's
        added_date.
      - push_one_extra: split item gains split_info {is_split, method, val,
        total_chunks, chunks: [{filename, hash}]} (chunk names carry the
        [short_id] tag, split_video_file's ".chunk.NNN.mkv" pattern); then
        uploaded=True, status="onboarded".
      - replace_one_extra: status="archived".
      - restore_one_extra: status="restored_local"; a split item's FIRST
        restore blesses the merged hash — item hash updated, re_hashed=True,
        split_info gains merge_seed / merge_tool / rehashed_at.

    The four items cover the full status vocabulary (local_ready / onboarded /
    archived / restored_local), one nested sub_rel, one split item with chunks
    + blessed-merge fields, and three whole-file items."""

    def _item(group_rel, sub_rel, status, uploaded):
        filename = sub_rel.split("/")[-1]
        name_no_ext, ext = os.path.splitext(filename)
        sid = mvcommon.generate_short_id(f"{title_id}::{group_rel}/{sub_rel}")
        return {
            "filename": filename,
            "sub_rel": sub_rel,
            "short_id": sid,
            "hash": hashlib.sha256(f"{title_id}:{group_rel}/{sub_rel}:master".encode()).hexdigest(),
            "status": status,
            "uploaded": uploaded,
            "search_term": f"{name_no_ext} [{sid}]{ext}",
            "tech_spec": {"resolution": "1080p", "video_codec": "HEVC",
                          "size_bytes": 123_456_789},
        }

    # SPLIT item, fully through the lifecycle's restore leg: chunks + the
    # blessed-merge fields. Nested sub_rel exercises the "<group>/<sub>/" path
    # composition idiom every consumer shares (_extras_item_paths).
    split = _item("Specials", "Bonus/Making Of.mkv", "restored_local", True)
    sid = split["short_id"]
    split["split_info"] = {
        "is_split": True, "method": "SIZE_GB", "val": "2", "total_chunks": 2,
        "chunks": [
            {"filename": f"Making Of [{sid}].chunk.001.mkv",
             "hash": hashlib.sha256(f"{title_id}:chunk1".encode()).hexdigest()},
            {"filename": f"Making Of [{sid}].chunk.002.mkv",
             "hash": hashlib.sha256(f"{title_id}:chunk2".encode()).hexdigest()},
        ],
        "merge_seed": sid,
        "merge_tool": "mkvmerge v97.0",
        "rehashed_at": "2026-07-27T00:00:00Z",
    }
    split["re_hashed"] = True

    # Item order within each group mirrors the canonical sorted-by-sub_rel
    # invariant scan_extras_folders/merge_extras_into_title maintain.
    return {
        "groups": {
            "Specials": {
                "added_date": "2026-07-27",
                "items": [_item("Specials", "BTS.mkv", "onboarded", True), split],
            },
            "Extra": {
                "added_date": "2026-07-27",
                "items": [_item("Extra", "Recap.mkv", "archived", True),
                          _item("Extra", "Trailer.mkv", "local_ready", False)],
            },
        }
    }


def test_extras_block_round_trips_on_title_entries(sandbox):
    """A movie LEAF and a SEASON_MAP each carrying the representative `extras`
    block survive save_library -> load_library byte-for-byte (dict-equal: no
    key loss, no value drift, nested chunks/blessed fields intact). The two
    title ids use different prefixes (mov-/tv-), so the block also round-trips
    through BOTH prefix-routed library files — proving `extras` needs no
    mvcommon routing change (Consumer Impact #1)."""
    folder_path = str(sandbox["media_dir"])

    # Contract precondition: `extras` is OPTIONAL — it must never enter any
    # type's `required` set (that would break every extras-less entry).
    for entry_type, spec in main.ENTRY_TYPE_KEYS.items():
        assert "extras" not in spec["required"], (
            f"`extras` must stay an OPTIONAL nested block, but it appeared in "
            f"ENTRY_TYPE_KEYS['{entry_type}']['required'] — see the registry comment."
        )

    movie = _leaf_entry(folder_path, "guard_movie.mkv")
    movie["extras"] = _extras_block(MOVIE_TITLE_ID)
    season = _entry_for_type("season_map", folder_path)
    season["extras"] = _extras_block(SEASON_ID)

    library = {MOVIE_TITLE_ID: movie, SEASON_ID: season}
    mvcommon.save_library(library)

    # Prefix routing put each title (with its nested block) in its own file.
    assert MOVIE_TITLE_ID in sandbox["lib_movies"].read_text(encoding="utf-8")
    assert SEASON_ID in sandbox["lib_series"].read_text(encoding="utf-8")

    reloaded = mvcommon.load_library()
    for mid, before in library.items():
        assert mid in reloaded, f"extras-bearing title '{mid}' lost on round-trip"
        after = reloaded[mid]
        # Focused first: the nested block itself (better failure diff) …
        assert after.get("extras") == before["extras"], (
            f"`extras` block changed on round-trip for '{mid}'"
        )
        # … then the whole entry (no sibling key lost/mutated either).
        assert after == before, f"entry '{mid}' changed on round-trip"


def test_read_commands_tolerate_extras_bearing_library(sandbox, capsys):
    """Every whole-library READ command (cmd_scan_unprepped, cmd_local_status
    incl. the size-limit branch, cmd_sort) plus collect_reclaimable (the other
    Step-7 extras-aware iterator) must COMPLETE WITHOUT RAISING on a library
    where BOTH title shapes carry extras — alongside a real leaf and a
    multi_ep_alias, so the non-physical tolerance of test (b) still holds with
    extras present. The extras files exist on disk at their canonical composed
    paths, so the disk-walkers genuinely encounter them (the Step-7
    known-paths seam). Afterwards the nested blocks must be byte-for-byte
    intact (cmd_sort rewrites every library file — it must not corrupt the
    optional nested block)."""
    movie_dir = sandbox["media_dir"]
    (movie_dir / "guard_movie.mkv").write_bytes(b"GUARD-MOVIE-BYTES\n" * 100)
    season_dir = sandbox["local_root"] / "Series" / "BSG" / "Season 04"
    season_dir.mkdir(parents=True)
    (season_dir / "leaf_episode.mkv").write_bytes(b"LEAF-EPISODE-BYTES\n" * 100)

    movie = _leaf_entry(str(movie_dir), "guard_movie.mkv")
    movie["extras"] = _extras_block(MOVIE_TITLE_ID)
    season = _entry_for_type("season_map", str(season_dir))
    season["extras"] = _extras_block(SEASON_ID)
    leaf = _leaf_entry(str(season_dir), "leaf_episode.mkv")
    alias = _entry_for_type("multi_ep_alias", str(season_dir))

    library = {MOVIE_TITLE_ID: movie, SEASON_ID: season,
               PRIMARY_ID: leaf, ALIAS_ID: alias}

    # Materialize every extras item on disk at its canonical path — composed
    # via the SAME seam the iterators read (_extras_item_paths), so the walk
    # meets real extras files it must recognize rather than choke on.
    for entry in (movie, season):
        for _grp, ex_path, _item in main._extras_item_paths(entry):
            os.makedirs(os.path.dirname(ex_path), exist_ok=True)
            with open(ex_path, "wb") as f:
                f.write(b"EXTRA-BYTES")

    mvcommon.save_library(library)

    # None of these may raise on an extras-bearing library.
    main.cmd_scan_unprepped()
    main.cmd_local_status()
    main.cmd_local_status("40gb")  # size-limit branch too
    main.cmd_sort()
    main.collect_reclaimable()

    # Drain captured output (so a failure trace isn't polluted by command noise).
    capsys.readouterr()

    # The read commands (cmd_sort rewrote all four files) must leave the
    # nested blocks — and the non-physical entries — byte-for-byte intact.
    reloaded = mvcommon.load_library()
    assert reloaded[MOVIE_TITLE_ID].get("extras") == movie["extras"]
    assert reloaded[SEASON_ID].get("extras") == season["extras"]
    assert reloaded[ALIAS_ID].get("type") == "multi_ep_alias"
    assert "type" not in reloaded[PRIMARY_ID]


# ===========================================================================
# (d) GPHOTOS — the optional nested Google Photos identity block (IMP-C25).
# ===========================================================================
# `gphotos` is NOT an entry type either: it nests on each UPLOADED OBJECT
# (PLAN P-1) — the unsplit leaf itself, each split_info.chunks[i], each FLAC
# holder split_info.carried_out_tracks[i], an unsplit extras item, each chunk
# of a split extras item — and never on a split entry itself (P-3). Documented
# on ENTRY_TYPE_KEYS. These tests are its schema tripwire:
#   (d1) the registry literal stays untouched;
#   (d2) THE canonical `_gphotos_block()` carries every P-1 key, in the
#        writer's order — so (d3)/(d4) exercise the FULL block;
#   (d3) blocks round-trip byte-for-byte at every placement through all four
#        prefix-routed library files, and sit exactly where mvcommon's object
#        inventory looks for them (TRUSTED — the P-2 pin);
#   (d4) every whole-library READ command tolerates them: completes, mutates
#        nothing, and behaves EXACTLY as on the same library without blocks
#        (PLAN Consumer Impact row 14: "nested block invisible").

ANIME_ID = "ani-ja-2019-guardanime01"  # "ani-" -> library_anime.json (split leaf + FLAC holder)
OTHERS_ID = "oth-en-2024-guardfinal"   # "oth-" -> library_others.json (unsplit leaf)

# Synthetic, mutually consistent facts (PLAN P-15: nothing real — photo ids
# carry FAKE, the device serial is fake).
GP_DATE_UTC = "2024-03-09T06:03:02Z"   # the file's DateUTC = the taken time Google Photos shows
GP_TAKEN_MS = int(datetime(2024, 3, 9, 6, 3, 2, tzinfo=timezone.utc).timestamp()) * 1000
GP_TZ_OFFSET_MS = 8 * 3600 * 1000      # the Pixel's +08:00 (Asia/Singapore)
GP_PUSHED_AT = "2026-09-20T10:00:00Z"
GP_UPLOAD_MS = int(datetime(2026, 9, 21, 3, 15, tzinfo=timezone.utc).timestamp()) * 1000  # backed up later
GP_OVERRIDE_AT = "2026-09-27T09:00:00Z"
GP_VERIFIED_AT = "2026-09-28T10:00:00Z"


def _gphotos_block(owner_id, sha256, uploaded_name):
    """THE canonical representative `gphotos` block (IMP-C25, PLAN P-1) of ONE
    uploaded object: EVERY optional key present, synthetic values only. Built
    with mvcommon.new_gphotos_block — the constructor behind the apply engine,
    the block's only writer (P-10) — so it is exactly a stored block's shape.
    Later IMP-C25 fixtures/tests should mirror it, as _extras_block is mirrored.

      - `sha256` is the PIN: the owning object's CURRENT hash (P-2 — leaf
        `hash`, chunk `hash`, holder `holder_hash`, extras item `hash`), so the
        block is TRUSTED where it is placed;
      - content key: a synthetic SHA-1 (one per object) and its dedupKey;
      - `accounts` holds one record, for owner_id's prefix-default account,
        which is also `home_account` (P-4): a confirmed, dedupkey-verified item
        carrying every P-1 account key — approach verdicts, an alternate (a
        re-split duplicate's photo id) and both quality flags;
      - `search_override` is the user's failed-list term (OD-5) as Excel's
        autocorrect leaves it (en dash, curly apostrophe), so the guards also
        prove non-ASCII text survives save_library/load_library exactly."""
    account = mvcommon.category_for_id(owner_id)  # a category is named after its default account
    sha1 = hashlib.sha1(f"gphotos-guard:{sha256}".encode()).hexdigest()
    dedup_key = gpweb.dedup_key(sha1)
    stem = os.path.splitext(uploaded_name)[0]
    return mvcommon.new_gphotos_block(
        sha256,
        sha1=sha1, dedup_key=dedup_key, size_bytes=4_294_967_296, width=1920, height=1080,
        duration_s=2712.48, date_utc=GP_DATE_UTC, uploaded_name=uploaded_name,
        pushed={"at": GP_PUSHED_AT, "device_id": "FAKESERIAL04", "device_tz": "Asia/Singapore",
                "device_tz_offset_min": 480},
        accounts={account: {
            "photo_id": f"AF1QipFAKEguard{sha256[:24]}", "taken_ms": GP_TAKEN_MS,
            "tz_offset_ms": GP_TZ_OFFSET_MS, "upload_ms": GP_UPLOAD_MS, "dedup_key": dedup_key,
            "gp_filename": uploaded_name, "width": 1920, "height": 1080,
            "verified_at": GP_VERIFIED_AT, "verified_by": "dedupkey", "source": "check",
            "approach": {"best": "photo_id", "ok": ["photo_id", "hash", "taken_second"],
                         "failed": ["title_sid", "legacy"], "at": GP_VERIFIED_AT, "via": "rpc"},
            "alternates": [f"AF1QipFAKEalt{sha256[24:48]}"],
            "original_quality": True, "partial_upload": False, "confirmed": True,
        }},
        home_account=account,
        search_override={"query": f"{stem} – director’s cut", "at": GP_OVERRIDE_AT},
    )


def _uploaded_leaf(mid, folder, filename, status):
    """A leaf with cmd_prep's key set (its `entry_data`) after the push:
    uploaded=True, `status` = where it stands in the lifecycle."""
    sid = mvcommon.generate_short_id(mid)
    stem, ext = os.path.splitext(filename)
    return {
        "short_id": sid, "filename": filename, "folder_path": str(folder),
        "status": status, "uploaded": True, "search_term": f"{stem} [{sid}]{ext}",
        "hash": hashlib.sha256(f"{mid}:master".encode()).hexdigest(),
        "metadata": main.parse_metadata_from_id(mid),
        "tech_spec": {"resolution": "1080p", "video_codec": "HEVC", "size_bytes": 4_294_967_296},
    }


def _split_leaf(mid, folder, filename):
    """A leaf pushed SPLIT with a FLAC carry-out, then restored and blessed:
    split_info as cmd_push writes it — chunks [{filename, hash}] and
    carried_out_tracks [_carry_out_flac_track's record] — plus the bless fields
    of its first restore (merge_seed / merge_tool / rehashed_at, re_hashed)."""
    leaf = _uploaded_leaf(mid, folder, filename, "restored_local")
    sid, stem = leaf["short_id"], os.path.splitext(filename)[0]
    manifest = [  # probe_track_manifest's shape: a video track + the carried-out FLAC
        {"id": 0, "type": "video", "codec_id": "V_MPEGH/ISO/HEVC", "language": "und",
         "default": True, "forced": False, "enabled": True, "name": None, "uid": "1001"},
        {"id": 1, "type": "audio", "codec_id": "A_FLAC", "language": "jpn",
         "default": True, "forced": False, "enabled": True, "name": "Stereo", "uid": "1002"},
    ]
    leaf["split_info"] = {
        "is_split": True, "method": "SIZE_GB", "val": "8", "total_chunks": 2,
        "chunks": [{"filename": f"{stem} [{sid}].chunk.{n:03d}.mkv",
                    "hash": hashlib.sha256(f"{mid}:chunk{n}".encode()).hexdigest()} for n in (1, 2)],
        "carried_out_tracks": [{
            "track_id": 1, "codec": "A_FLAC", "language": "jpn",
            "holder_filename": f"{stem} [{sid}].holder.mkv",
            "holder_hash": hashlib.sha256(f"{mid}:holder".encode()).hexdigest(),
            "original_tracks": manifest, "position": 1, "default": True, "forced": False,
            "enabled": True, "name": "Stereo", "uid": "1002"}],
        "merge_seed": sid, "merge_tool": "mkvmerge v97.0", "rehashed_at": "2026-09-25T00:00:00Z",
    }
    leaf["re_hashed"] = True
    return leaf


def _gphotos_library(sandbox):
    """A library over ALL FOUR prefixes in which EVERY uploaded object carries
    its canonical block at its P-1 placement:
      mov-  movie leaf (archived) ......... the entry itself
            + extras (_extras_block) ...... each whole item / each chunk of the split item
      tv-   season_map + extras ........... likewise (the season_map itself holds none)
            episode leaf (onboarded) ...... the entry itself (its multi_ep_alias holds none)
      ani-  split leaf + FLAC carry-out ... each chunk + the holder, never the entry (P-3)
      oth-  leaf (archived) ............... the entry itself
    Each title's Trailer.mkv (local_ready, uploaded=False) carries the one
    realistic UNTRUSTED block: it was re-mastered after its push, and
    merge_extras_into_title reset it IN PLACE with the new hash — so its block
    still pins the OLD bytes (P-2: ignored by readers until the apply engine
    replaces it).

    Returns (library, trusted, stale, files): the 14 trusted and 2 stale blocks
    as placed, and {on-disk path: bytes} for every physical object — a small
    dummy for `archived` (the movie's a legacy TEXT stub, the others valid
    video dummies) and real-sized bytes for every other status."""
    root = sandbox["local_root"]
    movie_dir, season_dir = sandbox["media_dir"], root / "Series" / "BSG" / "Season 04"
    movie = _uploaded_leaf(MOVIE_TITLE_ID, movie_dir, "guard_movie.mkv", "archived")
    movie["extras"] = _extras_block(MOVIE_TITLE_ID)
    season = _entry_for_type("season_map", str(season_dir))
    season["extras"] = _extras_block(SEASON_ID)
    library = {
        MOVIE_TITLE_ID: movie,
        SEASON_ID: season,
        PRIMARY_ID: dict(_uploaded_leaf(PRIMARY_ID, season_dir, "leaf_episode.mkv", "onboarded"),
                         parent_id=SEASON_ID),
        ALIAS_ID: _entry_for_type("multi_ep_alias", str(season_dir)),
        ANIME_ID: _split_leaf(ANIME_ID, root / "Anime" / "Guard Anime", "guard_anime_01.mkv"),
        OTHERS_ID: _uploaded_leaf(OTHERS_ID, root / "Sports" / "Guard Final", "guard_final.mkv", "archived"),
    }

    trusted, stale = [], []

    def place(owner_id, node, pin_key, uploaded_name):
        node["gphotos"] = _gphotos_block(owner_id, node[pin_key], uploaded_name)
        trusted.append(node["gphotos"])

    for mid in (MOVIE_TITLE_ID, PRIMARY_ID, OTHERS_ID):  # unsplit leaves: the entry itself
        leaf = library[mid]
        place(mid, leaf, "hash", mvcommon.remote_upload_name(leaf["filename"], leaf["short_id"]))
    split_info = library[ANIME_ID]["split_info"]         # a split leaf: its chunks + holder (P-3)
    for chunk in split_info["chunks"]:
        place(ANIME_ID, chunk, "hash", chunk["filename"])
    for track in split_info["carried_out_tracks"]:
        place(ANIME_ID, track, "holder_hash", track["holder_filename"])
    for title_id in (MOVIE_TITLE_ID, SEASON_ID):         # extras: whole items / split items' chunks
        for group in library[title_id]["extras"]["groups"].values():
            for item in group["items"]:
                name = mvcommon.remote_upload_name(item["filename"], item["short_id"])
                if "split_info" in item:
                    for chunk in item["split_info"]["chunks"]:
                        place(title_id, chunk, "hash", chunk["filename"])
                elif item["uploaded"]:
                    place(title_id, item, "hash", name)
                else:  # re-mastered after its push: the block still pins the OLD bytes
                    old = hashlib.sha256(f"{title_id}:{item['sub_rel']}:before re-master".encode()).hexdigest()
                    item["gphotos"] = _gphotos_block(title_id, old, name)
                    stale.append(item["gphotos"])

    real = b"GPHOTOS-GUARD-REAL-MEDIA\n" * 8400
    assert len(real) > main.DUMMY_MAX_BYTES
    statuses = {os.path.join(e["folder_path"], e["filename"]): e["status"]
                for e in library.values() if "type" not in e}  # physical leaves
    for entry in library.values():
        statuses.update({path: item["status"] for _grp, path, item in main._extras_item_paths(entry)})
    files = {path: b"MV-VIDEO-DUMMY" if status == "archived" else real for path, status in statuses.items()}
    files[os.path.join(str(movie_dir), "guard_movie.mkv")] = b"Original Hash: legacy text stub\n"
    return library, trusted, stale, files


_LIBRARY_FILES = ("lib_movies", "lib_series", "lib_anime", "lib_others")


def _library_bytes(sandbox):
    """The raw bytes of all four prefix-routed library files."""
    return {name: sandbox[name].read_bytes() for name in _LIBRARY_FILES}


def _entries_json(library):
    """{id: the entry as JSON}: equal dumps mean equal keys, values, types AND key order."""
    return {mid: json.dumps(entry) for mid, entry in library.items()}


def _nested_blocks(node):
    """Every `gphotos` value nested anywhere in `node`, in document order."""
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "gphotos":
                yield value
            else:
                yield from _nested_blocks(value)
    elif isinstance(node, list):
        for value in node:
            yield from _nested_blocks(value)


def _without_gphotos(node):
    """A deep copy of `node` with every `gphotos` key removed: the library as it was before IMP-C25."""
    if isinstance(node, dict):
        return {key: _without_gphotos(value) for key, value in node.items() if key != "gphotos"}
    if isinstance(node, list):
        return [_without_gphotos(value) for value in node]
    return node


def _write_files(sandbox, files):
    """(Re)write every {path: bytes} file, resetting any dummy a previous command
    regenerated. Hard guard: every path lies under the sandbox's LOCAL_ROOT
    (tmp_path), never the real C:\\Media."""
    root = os.path.normcase(str(sandbox["local_root"])) + os.sep
    for path, data in files.items():
        assert os.path.normcase(path).startswith(root), f"fixture file escaped the sandbox: {path}"
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as fh:
            fh.write(data)


def test_gphotos_adds_no_entry_type_and_no_required_key():
    """(d1) `gphotos` is an OPTIONAL nested block: the registry literal stays
    exactly as IMP-H3 / IMP-D19 left it — no new type, no `required` or
    `physical` change. A deliberate registry change updates this pin together
    with the (a)/(b) builders above."""
    assert main.ENTRY_TYPE_KEYS == {
        "leaf":           {"required": {"folder_path", "filename", "status"}, "physical": True},
        "season_map":     {"required": {"folder_path", "children"},           "physical": False},
        "multi_ep_alias": {"required": {"alias_of", "parent_id"},             "physical": False},
    }, "ENTRY_TYPE_KEYS changed — `gphotos` must stay an optional nested block (see the registry comment)"


def test_canonical_gphotos_block_carries_every_p1_key():
    """(d2) _gphotos_block() is complete: every key of the P-1 shape at every
    level, in the writer's (mvcommon's) key order, and trusted under its pin.
    Bound to mvcommon's own key lists, so a key added to the block cannot land
    in the writer without landing in THE canonical block — and so in (d3)/(d4)."""
    pin = hashlib.sha256(b"canonical").hexdigest()
    block = _gphotos_block(MOVIE_TITLE_ID, pin, "guard_movie [abc123].mkv")
    assert list(block) == list(mvcommon._BLOCK_KEYS)
    assert block["v"] == mvcommon.GPHOTOS_BLOCK_VERSION
    assert list(block["pushed"]) == list(mvcommon._PUSHED_KEYS)
    assert list(block["accounts"]) == [block["home_account"]] == ["movies"]
    record = block["accounts"]["movies"]
    assert list(record) == list(mvcommon._ACCOUNT_KEYS)
    assert list(record["approach"]) == list(mvcommon._APPROACH_KEYS)
    assert list(block["search_override"]) == ["query", "at"]
    assert mvcommon.block_trusted(block, pin)


def test_gphotos_blocks_round_trip_byte_for_byte_at_every_placement(sandbox):
    """(d3) Every block — at every P-1 placement, in all four prefix-routed
    files — survives save_library -> load_library byte-for-byte, and so does
    every entry around it; another load -> save cycle (what every
    library-writing command does around its own edits) rewrites the files
    byte-identically. And the placements are exactly where mvcommon's object
    inventory (PLAN Step 3) looks: every uploaded object reports its canonical
    block as TRUSTED, over all five object kinds and all four categories."""
    library, trusted, stale, _files = _gphotos_library(sandbox)
    assert (len(trusted), len(stale)) == (14, 2)
    mvcommon.save_library(library)
    saved = _library_bytes(sandbox)
    for name, data in saved.items():  # each prefix-routed file really carries blocks
        assert b'"gphotos"' in data, f"{name} holds no gphotos block"

    reloaded = mvcommon.load_library()
    # Focused first (a better failure diff): the blocks themselves, trusted and stale ...
    assert sorted(json.dumps(b) for b in _nested_blocks(reloaded)) == \
        sorted(json.dumps(b) for b in trusted + stale)
    # ... then every entry around them.
    assert _entries_json(reloaded) == _entries_json(library)

    mvcommon.save_library(reloaded)
    assert _library_bytes(sandbox) == saved, "a load -> save cycle changed the library files"

    objects = list(mvcommon.iter_uploaded_objects(reloaded))
    assert sorted(json.dumps(o["block"]) for o in objects) == sorted(json.dumps(b) for b in trusted)
    assert {o["kind"] for o in objects} == {"whole", "chunk", "holder", "extra_whole", "extra_chunk"}
    assert {o["category"] for o in objects} == {"movies", "tv", "anime", "others"}


# The whole-library READ commands PLAN Step 4 names: label -> (call, rewrites_library).
# Each call looks its command up on `main` when it runs (a patched command is what runs).
_READ_COMMANDS = {
    "scan_unprepped":             (lambda: main.cmd_scan_unprepped(), False),
    "local_status":               (lambda: main.cmd_local_status(), False),
    "local_status_40gb":          (lambda: main.cmd_local_status("40gb"), False),
    "sort":                       (lambda: main.cmd_sort(), True),  # re-saves every file, sorted
    "collect_reclaimable":        (lambda: main.collect_reclaimable(), False),
    "items_payload":              (lambda: main.items_payload(), False),
    "verify_library":             (lambda: main.cmd_verify_library(), False),
    "verify_library_fix_dummies": (lambda: main.cmd_verify_library(fix_dummies=True), False),
    "repair_dummies":             (lambda: main.cmd_repair_dummies(), False),  # rewrites dummy FILES only
    "build_tree":                 (lambda: main.build_tree(), False),
}


@pytest.mark.parametrize("command", list(_READ_COMMANDS))
def test_read_commands_tolerate_gphotos_blocks(command, sandbox, fake_dummy, capsys):
    """(d4) Each whole-library READ command runs twice on the same disk — over
    the gphotos-bearing library, then over the SAME library with every block
    stripped (the library as it was before IMP-C25) — and must:
      - complete without raising on the blocks, trusted and stale alike;
      - mutate nothing: the library files stay byte-identical (cmd_sort, which
        re-saves every file in sorted order, must keep every entry — blocks
        included — JSON-identical), and no Google Photos identity state is
        written (only fetch/check/map/apply produce it — P-10);
      - behave EXACTLY as without blocks: the same return value, stdout and
        stderr. The block is invisible to these readers (PLAN Consumer Impact
        row 14); a reader that starts to act on it must update this guard.
    The disk holds real-sized files and dummies (fake_dummy stands in for
    ffmpeg), so the per-state branches of these commands all meet block-bearing
    entries: pending (local_status), reclaimable (collect_reclaimable,
    items_payload, build_tree), archived dummies incl. a legacy TEXT stub
    (verify_library, repair_dummies)."""
    call, rewrites_library = _READ_COMMANDS[command]
    library, _trusted, _stale, files = _gphotos_library(sandbox)
    bare = _without_gphotos(library)
    assert bare != library  # the blocks are really there

    observed = []
    for variant in (library, bare):
        _write_files(sandbox, files)  # the same disk for both runs (repair rewrites dummies)
        mvcommon.save_library(variant)
        before = _library_bytes(sandbox)
        result = call()
        captured = capsys.readouterr()
        observed.append((result, captured.out, captured.err))
        assert _entries_json(mvcommon.load_library()) == _entries_json(variant), \
            f"{command} changed library entries"
        if not rewrites_library:
            assert _library_bytes(sandbox) == before, f"{command} rewrote the library"
    assert not os.path.exists(mvcommon.GPHOTOS_STATE_DIR), f"{command} wrote Google Photos identity state"
    assert observed[0] == observed[1], f"{command} behaves differently when gphotos blocks are present"
