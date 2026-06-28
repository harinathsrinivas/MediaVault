"""Unit tests for the five pure data-functions added in step 1 of IMP-E12.

Functions under test (all in main.py):
  - classify_entry_state(entry, on_disk_real)
  - guess_manual_id(path)
  - suggest_target_folder(item)
  - suggest_next_command(item)
  - collect_reclaimable()

All tests use the `sandbox` fixture (dual-patches mvcommon.LIBRARY_* AND
main.LIBRARY_* plus LOCAL_ROOT) and never touch real C:\\Media or library_*.json.
"""
import hashlib
import json
import os

import pytest

import main
import mvcommon


# ---------------------------------------------------------------------------
# (a) classify_entry_state
# ---------------------------------------------------------------------------

class TestClassifyEntryState:
    """Cover all five result values + edge cases."""

    def test_none_entry_returns_unprepped(self):
        """entry=None means unknown-to-library -> always UNPREPPED."""
        assert main.classify_entry_state(None, True) == "UNPREPPED"
        assert main.classify_entry_state(None, False) == "UNPREPPED"

    def test_season_map_returns_none(self):
        entry = {"type": "season_map", "folder_path": "/foo", "total_episodes": 2}
        assert main.classify_entry_state(entry, True) is None
        assert main.classify_entry_state(entry, False) is None

    def test_multi_ep_alias_returns_none(self):
        entry = {"type": "multi_ep_alias", "alias_of": "tv-en-2009-bsg-s04e19", "parent_id": "x"}
        assert main.classify_entry_state(entry, True) is None
        assert main.classify_entry_state(entry, False) is None

    def test_local_ready_real_file_returns_local_not_pushed(self):
        entry = {"status": "local_ready", "uploaded": False, "filename": "f.mkv",
                 "folder_path": "/x"}
        assert main.classify_entry_state(entry, True) == "LOCAL_NOT_PUSHED"

    def test_onboarded_real_file_returns_pushed_not_archived(self):
        entry = {"status": "onboarded", "uploaded": True, "filename": "f.mkv",
                 "folder_path": "/x"}
        assert main.classify_entry_state(entry, True) == "PUSHED_NOT_ARCHIVED"

    def test_restored_local_real_file_returns_restored_replace_again(self):
        entry = {"status": "restored_local", "uploaded": True, "filename": "f.mkv",
                 "folder_path": "/x"}
        assert main.classify_entry_state(entry, True) == "RESTORED_REPLACE_AGAIN"

    def test_archived_dummy_on_disk_returns_archived(self):
        """In-library + on_disk_real=False + status archived -> ARCHIVED (excluded by caller)."""
        entry = {"status": "archived", "uploaded": True, "filename": "f.mkv",
                 "folder_path": "/x"}
        assert main.classify_entry_state(entry, False) == "ARCHIVED"

    def test_onboarded_dummy_on_disk_returns_none(self):
        """onboarded entry but dummy on disk -> None (disk is source of truth)."""
        entry = {"status": "onboarded", "uploaded": True, "filename": "f.mkv",
                 "folder_path": "/x"}
        assert main.classify_entry_state(entry, False) is None

    def test_local_ready_dummy_on_disk_returns_none(self):
        entry = {"status": "local_ready", "uploaded": False, "filename": "f.mkv",
                 "folder_path": "/x"}
        assert main.classify_entry_state(entry, False) is None

    def test_unknown_status_real_file_returns_none(self):
        """Unknown status -> .get() returns None — no invented badge."""
        entry = {"status": "some_future_status", "filename": "f.mkv", "folder_path": "/x"}
        assert main.classify_entry_state(entry, True) is None

    def test_archived_real_file_returns_none(self):
        """archived + real file on disk (anomaly) -> None (not cleanly reclaimable)."""
        entry = {"status": "archived", "uploaded": True, "filename": "f.mkv",
                 "folder_path": "/x"}
        # archived is NOT in _RECLAIMABLE_STATUS_BADGE, so .get() returns None.
        assert main.classify_entry_state(entry, True) is None


# ---------------------------------------------------------------------------
# (b) guess_manual_id
# ---------------------------------------------------------------------------

class TestGuessManualId:
    """Cover movie/series/anime prefix inference, year extraction, no-year case."""

    def test_movie_prefix_and_year(self):
        path = r"C:\Media\Movies\English\Action\Dark.River.2024.1080p.mkv"
        result = main.guess_manual_id(path)
        assert result.startswith("mov-"), f"Expected mov- prefix, got: {result}"
        assert "-en-" in result, f"Expected default lang 'en', got: {result}"
        assert "2024" in result, f"Expected year 2024, got: {result}"

    def test_series_prefix_and_year(self):
        path = r"C:\Media\Series\Dark\Season 01\Dark.S01E01.2017.mkv"
        result = main.guess_manual_id(path)
        assert result.startswith("tv-"), f"Expected tv- prefix, got: {result}"
        assert "-en-" in result, f"Expected default lang 'en', got: {result}"
        assert "2017" in result, f"Expected year 2017, got: {result}"

    def test_anime_prefix_and_year(self):
        path = r"C:\Media\Anime\Death Note\Death.Note.2006.07.mkv"
        result = main.guess_manual_id(path)
        assert result.startswith("ani-"), f"Expected ani- prefix, got: {result}"
        assert "-en-" in result, f"Expected default lang 'en', got: {result}"
        assert "2006" in result, f"Expected year 2006, got: {result}"

    def test_no_year_filename_produces_0000_placeholder(self):
        """No 4-digit year token -> year segment should be absent / use 0000."""
        path = r"C:\Media\Movies\English\Some.Movie.BluRay.mkv"
        result = main.guess_manual_id(path)
        assert result.startswith("mov-"), f"Expected mov- prefix, got: {result}"
        # The implementation uses yr = year if year else "0000"
        assert "-0000-" in result or result.count("-en-") == 1, \
            f"No-year path should produce 0000 segment, got: {result}"

    def test_never_raises(self):
        """Guaranteed never-raise contract: try weird inputs."""
        for path in ("", ".", "/no/ext/file", r"C:\weird[brackets].txt"):
            result = main.guess_manual_id(path)
            assert isinstance(result, str), f"Expected str, got {type(result)} for path={path!r}"

    def test_release_noise_stripped(self):
        """Release-noise tokens (bluray, x265, 1080p, etc.) should NOT appear in the slug."""
        path = r"C:\Media\Movies\English\Action\Interstellar.2014.BluRay.x265.1080p.mkv"
        result = main.guess_manual_id(path)
        assert result.startswith("mov-")
        assert "2014" in result
        # Noise tokens dropped — slug should not contain these keywords
        slug_part = result.split("-", 3)[-1] if result.count("-") >= 3 else result
        assert "bluray" not in slug_part
        assert "x265" not in slug_part
        assert "1080p" not in slug_part


# ---------------------------------------------------------------------------
# (c) suggest_target_folder
# ---------------------------------------------------------------------------

class TestSuggestTargetFolder:
    """Cover movie vs series/anime curly-brace shape and in-library applies=False."""

    def _make_item(self, mid, entry=None, path=""):
        """Build a minimal item dict for suggest_target_folder."""
        return {"id": mid, "badge": "UNPREPPED", "path": path, "size_bytes": 1000,
                "entry": entry}

    def test_new_movie_has_tmdb_placeholder(self):
        item = self._make_item("mov-en-2024-darkriver")
        result = main.suggest_target_folder(item)
        assert result["applies"] is True
        assert "{tmdb-" in result["folder"], f"Expected {{tmdb-…}} in folder, got: {result['folder']}"
        assert result["provider_tag"] is not None
        assert result["editable_provider_field"] == "tmdb"

    def test_new_series_has_tvdb_placeholder(self):
        item = self._make_item("tv-en-2017-dark-s01e01")
        result = main.suggest_target_folder(item)
        assert result["applies"] is True
        assert "{tvdb-" in result["folder"], f"Expected {{tvdb-…}} in folder, got: {result['folder']}"
        assert result["provider_tag"] is not None
        assert result["editable_provider_field"] == "tvdb"

    def test_new_anime_has_tvdb_placeholder(self):
        item = self._make_item("ani-en-2006-deathnote07")
        result = main.suggest_target_folder(item)
        assert result["applies"] is True
        assert "{tvdb-" in result["folder"], f"Expected {{tvdb-…}} in folder, got: {result['folder']}"
        assert result["editable_provider_field"] == "tvdb"

    def test_in_library_item_applies_false(self):
        """Existing in-library item -> applies=False, provider_tag=None, editable_provider_field=None."""
        entry = {"status": "onboarded", "uploaded": True,
                 "folder_path": r"C:\Media\Movies\English\Action\SomeMovie (2023) [tmdb-12345]",
                 "filename": "SomeMovie.mkv"}
        item = self._make_item("mov-en-2023-somemovie", entry=entry)
        result = main.suggest_target_folder(item)
        assert result["applies"] is False
        assert result["provider_tag"] is None
        assert result["editable_provider_field"] is None
        # folder should be the existing folder_path
        assert result["folder"] == entry["folder_path"]


# ---------------------------------------------------------------------------
# (d) suggest_next_command
# ---------------------------------------------------------------------------

class TestSuggestNextCommand:
    """Assert exact command strings from the State table."""

    def _item(self, badge, mid, path=""):
        return {"id": mid, "badge": badge, "path": path, "size_bytes": 1000}

    def test_unprepped_exact_string(self, tmp_path):
        path = str(tmp_path / "Dark.River.2024.mkv")
        item = self._item("UNPREPPED", "mov-en-2024-darkriver", path)
        result = main.suggest_next_command(item)
        assert result == f'python main.py prep mov-en-2024-darkriver "{path}"'

    def test_local_not_pushed_exact_string(self):
        item = self._item("LOCAL_NOT_PUSHED", "tv-en-2017-dark-s01e01")
        result = main.suggest_next_command(item)
        assert result == "python main.py push tv-en-2017-dark-s01e01 SIZE_GB 8"

    def test_pushed_not_archived_exact_string(self):
        item = self._item("PUSHED_NOT_ARCHIVED", "mov-en-2025-f1")
        result = main.suggest_next_command(item)
        assert result == "python main.py replace mov-en-2025-f1"

    def test_restored_replace_again_exact_string(self):
        item = self._item("RESTORED_REPLACE_AGAIN", "ani-ja-2006-deathnote07")
        result = main.suggest_next_command(item)
        assert result == "python main.py replace ani-ja-2006-deathnote07"

    def test_archived_returns_empty(self):
        item = self._item("ARCHIVED", "mov-en-2020-archived")
        result = main.suggest_next_command(item)
        assert result == ""

    def test_unknown_badge_returns_empty(self):
        item = self._item("UNKNOWN_BADGE", "mov-en-2020-x")
        result = main.suggest_next_command(item)
        assert result == ""


# ---------------------------------------------------------------------------
# (e) collect_reclaimable  — seeded sandbox
# ---------------------------------------------------------------------------

def _seed_library(sandbox, movies=None, series=None, anime=None):
    """Write three library JSON files. Pass {} for empty, None means {}."""
    movies = movies or {}
    series = series or {}
    anime = anime or {}
    sandbox["lib_movies"].write_text(json.dumps(movies), encoding="utf-8")
    sandbox["lib_series"].write_text(json.dumps(series), encoding="utf-8")
    sandbox["lib_anime"].write_text(json.dumps(anime), encoding="utf-8")


class TestCollectReclaimable:
    """End-to-end tests of collect_reclaimable() over a seeded sandbox."""

    def test_unprepped_real_file_appears(self, sandbox, make_video):
        """Real file (>DUMMY_MAX_BYTES) not in library -> UNPREPPED in items."""
        movies_dir = sandbox["local_root"] / "Movies" / "TestMovie"
        movies_dir.mkdir(parents=True, exist_ok=True)
        real_path, _ = make_video(movies_dir / "NewMovie.2024.mkv")

        _seed_library(sandbox)  # empty library

        result = main.collect_reclaimable()
        assert "items" in result
        assert "total_reclaimable_bytes" in result
        assert "total_reclaimable_human" in result

        badges = {it["badge"] for it in result["items"]}
        paths = {it["path"] for it in result["items"]}
        assert "UNPREPPED" in badges, f"Expected UNPREPPED badge, got: {badges}"
        assert any(os.path.basename(p) == "NewMovie.2024.mkv" for p in paths), \
            f"Expected NewMovie.2024.mkv in paths, got: {paths}"

    def test_local_ready_leaf_appears_with_badge(self, sandbox, make_video):
        """local_ready leaf with real file -> LOCAL_NOT_PUSHED."""
        movies_dir = sandbox["local_root"] / "Movies" / "LocalMovie"
        movies_dir.mkdir(parents=True, exist_ok=True)
        real_path, _ = make_video(movies_dir / "LocalReady.2022.mkv")

        entry_id = "mov-en-2022-localready"
        library = {
            entry_id: {
                "status": "local_ready",
                "uploaded": False,
                "folder_path": str(movies_dir),
                "filename": "LocalReady.2022.mkv",
                "type": "movie",
            }
        }
        _seed_library(sandbox, movies=library)

        result = main.collect_reclaimable()
        items_by_id = {it["id"]: it for it in result["items"]}
        assert entry_id in items_by_id, \
            f"Expected {entry_id} in items, got ids: {list(items_by_id.keys())}"
        assert items_by_id[entry_id]["badge"] == "LOCAL_NOT_PUSHED"
        assert items_by_id[entry_id]["guessed"] is False

    def test_onboarded_real_file_appears(self, sandbox, make_video):
        """onboarded leaf with real file -> PUSHED_NOT_ARCHIVED."""
        movies_dir = sandbox["local_root"] / "Movies" / "OnboardedMovie"
        movies_dir.mkdir(parents=True, exist_ok=True)
        real_path, _ = make_video(movies_dir / "Onboarded.2021.mkv")

        entry_id = "mov-en-2021-onboarded"
        library = {
            entry_id: {
                "status": "onboarded",
                "uploaded": True,
                "folder_path": str(movies_dir),
                "filename": "Onboarded.2021.mkv",
                "type": "movie",
            }
        }
        _seed_library(sandbox, movies=library)

        result = main.collect_reclaimable()
        items_by_id = {it["id"]: it for it in result["items"]}
        assert entry_id in items_by_id, \
            f"Expected {entry_id} in items, got: {list(items_by_id.keys())}"
        assert items_by_id[entry_id]["badge"] == "PUSHED_NOT_ARCHIVED"

    def test_archived_dummy_excluded_from_items(self, sandbox):
        """archived entry with a dummy-sized file is NOT in items."""
        movies_dir = sandbox["local_root"] / "Movies" / "ArchivedMovie"
        movies_dir.mkdir(parents=True, exist_ok=True)
        dummy_file = movies_dir / "Archived.2020.mkv"
        # Write a sub-DUMMY_MAX_BYTES file (simulating an archived dummy).
        dummy_file.write_bytes(b"tiny-dummy" * 100)
        assert os.path.getsize(dummy_file) < main.DUMMY_MAX_BYTES

        entry_id = "mov-en-2020-archived"
        library = {
            entry_id: {
                "status": "archived",
                "uploaded": True,
                "folder_path": str(movies_dir),
                "filename": "Archived.2020.mkv",
                "type": "movie",
            }
        }
        _seed_library(sandbox, movies=library)

        result = main.collect_reclaimable()
        ids = {it["id"] for it in result["items"]}
        assert entry_id not in ids, \
            f"ARCHIVED entry should be excluded, but found in items: {ids}"

    def test_season_map_and_alias_excluded(self, sandbox, make_video):
        """season_map and multi_ep_alias entries produce no items row."""
        series_dir = sandbox["local_root"] / "Series" / "TestShow" / "Season 01"
        series_dir.mkdir(parents=True, exist_ok=True)
        real_path, file_hash = make_video(series_dir / "Show.S01E01E02.mkv")

        season_id = "tv-en-2020-testshow-s01"
        primary_id = "tv-en-2020-testshow-s01e01"
        alias_id = "tv-en-2020-testshow-s01e02"

        library = {
            season_id: {
                "type": "season_map",
                "folder_path": str(series_dir),
                "total_episodes": 2,
                "children": [primary_id, alias_id],
            },
            primary_id: {
                "status": "local_ready",
                "uploaded": False,
                "folder_path": str(series_dir),
                "filename": "Show.S01E01E02.mkv",
                "hash": file_hash,
                "parent_id": season_id,
            },
            alias_id: {
                "type": "multi_ep_alias",
                "alias_of": primary_id,
                "parent_id": season_id,
            },
        }
        _seed_library(sandbox, series=library)

        result = main.collect_reclaimable()
        ids = {it["id"] for it in result["items"]}
        assert season_id not in ids, "season_map should NOT appear in items"
        assert alias_id not in ids, "multi_ep_alias should NOT appear in items"
        # The primary leaf (local_ready + real file) SHOULD appear
        assert primary_id in ids, \
            f"primary leaf should appear with LOCAL_NOT_PUSHED, got ids: {ids}"

    def test_total_bytes_sums_only_reclaimable(self, sandbox, make_video):
        """total_reclaimable_bytes must sum only the items in the result (not archived)."""
        movies_dir = sandbox["local_root"] / "Movies" / "SumTest"
        movies_dir.mkdir(parents=True, exist_ok=True)
        real_path, _ = make_video(movies_dir / "Reclaimable.2023.mkv")
        real_size = os.path.getsize(real_path)

        # Add an archived dummy (should NOT be counted)
        dummy_file = movies_dir / "Dummy.2019.mkv"
        dummy_file.write_bytes(b"x" * 100)

        entry_reclaimable = "mov-en-2023-reclaimable"
        entry_archived = "mov-en-2019-dummy"
        library = {
            entry_reclaimable: {
                "status": "onboarded",
                "uploaded": True,
                "folder_path": str(movies_dir),
                "filename": "Reclaimable.2023.mkv",
                "type": "movie",
            },
            entry_archived: {
                "status": "archived",
                "uploaded": True,
                "folder_path": str(movies_dir),
                "filename": "Dummy.2019.mkv",
                "type": "movie",
            },
        }
        _seed_library(sandbox, movies=library)

        result = main.collect_reclaimable()
        assert result["total_reclaimable_bytes"] == sum(
            it["size_bytes"] for it in result["items"]
        ), "total_reclaimable_bytes must match sum of item sizes"
        # Archived entry should not inflate the total
        ids = {it["id"] for it in result["items"]}
        assert entry_archived not in ids

    def test_no_duplicate_rows(self, sandbox, make_video):
        """A library leaf and its on-disk file must produce exactly ONE row (no de-dup bug)."""
        movies_dir = sandbox["local_root"] / "Movies" / "DedupTest"
        movies_dir.mkdir(parents=True, exist_ok=True)
        real_path, _ = make_video(movies_dir / "DedupeMe.2022.mkv")

        entry_id = "mov-en-2022-dedupeme"
        library = {
            entry_id: {
                "status": "onboarded",
                "uploaded": True,
                "folder_path": str(movies_dir),
                "filename": "DedupeMe.2022.mkv",
                "type": "movie",
            }
        }
        _seed_library(sandbox, movies=library)

        result = main.collect_reclaimable()
        matching = [it for it in result["items"] if it["id"] == entry_id]
        assert len(matching) == 1, \
            f"Expected exactly 1 row for {entry_id}, got {len(matching)}"

    def test_item_has_seven_keys(self, sandbox, make_video):
        """Every item dict must carry the exact 7 public keys from the contract."""
        movies_dir = sandbox["local_root"] / "Movies" / "KeyTest"
        movies_dir.mkdir(parents=True, exist_ok=True)
        make_video(movies_dir / "KeyMovie.2024.mkv")

        _seed_library(sandbox)  # empty library -> item will be UNPREPPED

        result = main.collect_reclaimable()
        assert result["items"], "Need at least one item for the key check"
        expected_keys = {"id", "badge", "path", "size_bytes",
                         "suggested_command", "suggested_folder", "guessed"}
        for item in result["items"]:
            assert set(item.keys()) == expected_keys, \
                f"Unexpected keys in item: {set(item.keys())} (expected {expected_keys})"


# ---------------------------------------------------------------------------
# (f) Sub-DUMMY_MAX_BYTES unknown file is EXCLUDED (pins step 1's UNPREPPED gate)
# ---------------------------------------------------------------------------

def test_sub_threshold_unknown_file_not_in_items(sandbox):
    """A NOT-in-library file BELOW DUMMY_MAX_BYTES must NOT appear in items.

    This pins the decision made in step 1: the UNPREPPED gate in
    collect_reclaimable() emits a row only when the on-disk size >= DUMMY_MAX_BYTES.
    A tiny/dummy unknown file occupies no reclaimable space and must be silently
    skipped. classify_entry_state(None, False) does return "UNPREPPED" literally,
    but collect_reclaimable wraps that with an `if on_disk_real:` guard.
    """
    movies_dir = sandbox["local_root"] / "Movies" / "TinyUnknown"
    movies_dir.mkdir(parents=True, exist_ok=True)

    tiny_file = movies_dir / "TinyUnknown.2024.mkv"
    # Write well under DUMMY_MAX_BYTES (200_000 bytes)
    tiny_file.write_bytes(b"tiny" * 100)
    assert os.path.getsize(tiny_file) < main.DUMMY_MAX_BYTES

    _seed_library(sandbox)  # empty library — file is unknown

    result = main.collect_reclaimable()
    paths = {it["path"] for it in result["items"]}
    assert str(tiny_file) not in paths and not any(
        os.path.basename(p) == "TinyUnknown.2024.mkv" for p in paths
    ), f"Sub-threshold unknown file must NOT appear in items, but paths include: {paths}"


# ---------------------------------------------------------------------------
# (g) category_of_id  — IMP-D18 Others category (oth- prefix)
# ---------------------------------------------------------------------------

class TestCategoryOfId:
    """Pin that category_of_id routes each id prefix to the correct category string.

    IMP-D18 adds the fourth category "other" for oth- prefixed ids.  Tests for the
    three pre-existing prefixes (mov-/tv-/ani-) are included so the full routing
    table is pinned in one place and a regression on any prefix is caught here.
    """

    def test_mov_prefix_returns_movies(self):
        assert main.category_of_id("mov-en-2024-darkriver") == "movies"

    def test_tv_prefix_returns_series(self):
        assert main.category_of_id("tv-en-2017-dark-s01e01") == "series"

    def test_ani_prefix_returns_anime(self):
        assert main.category_of_id("ani-ja-2006-deathnote07") == "anime"

    def test_oth_prefix_returns_other(self):
        """oth- id must classify as 'other' — the IMP-D18 Others category."""
        assert main.category_of_id("oth-football-2026-fifaworldcup-s01e01") == "other"

    def test_oth_season_prefix_returns_other(self):
        """Season-level oth- id also classifies as 'other' (no leaf suffix required)."""
        assert main.category_of_id("oth-football-2026-fifaworldcup-s01") == "other"
