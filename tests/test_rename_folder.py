"""IMP-D17 — crash-safe cascading `rename_folder` (cmd_rename_folder).

Renames an on-disk show/season folder and atomically rewrites `folder_path` for
EVERY descendant library entry (the season_map container + all episode leaves),
under the EXISTING auto-rollback journal. It is hash-safe (paths only — never
re-hashes/re-splits/re-uploads), works on archived dummy files, and skips
`multi_ep_alias` entries (which carry no folder_path).

Boundaries are mocked only at the I/O edge (save_library failure injection),
mirroring docs/testing-strategy.md and the test_rollback.py crash-recovery
pattern. NEVER touches real C:\\Media files or real library_*.json (the `sandbox`
fixture's hard-guard governs every library write). Run `pytest -q` and fix
failures before marking the step done.
"""
import hashlib
import json
import os

import pytest

import main
import mvcommon


# ---------------------------------------------------------------------------
# Helpers (mirror test_rollback.py's _movies/_series readers)
# ---------------------------------------------------------------------------

def _series(sandbox):
    return json.loads(sandbox["lib_series"].read_text(encoding="utf-8"))


def _movies(sandbox):
    return json.loads(sandbox["lib_movies"].read_text(encoding="utf-8"))


def _empty(sandbox):
    sandbox["lib_movies"].write_text("{}", encoding="utf-8")
    sandbox["lib_series"].write_text("{}", encoding="utf-8")
    sandbox["lib_anime"].write_text("{}", encoding="utf-8")


def _seed_archived_movie(sandbox):
    """Seed an ARCHIVED movie whose on-disk file is a tiny DUMMY (< DUMMY_MAX_BYTES),
    with a recorded `hash`. Returns (entry_id, folder, dummy_path, recorded_hash).

    The hash is the sha256 of the (unrelated) original master — NOT of the dummy —
    exactly as a real archived entry stores it. rename_folder must leave this byte
    string untouched (no rehash)."""
    _empty(sandbox)
    entry_id = "mov-en-2017-dark-movie"
    folder = sandbox["local_root"] / "Movies" / "DarkMovie"
    folder.mkdir(parents=True)
    dummy = folder / "DarkMovie.mkv"
    dummy.write_bytes(b"DUMMY")  # well under DUMMY_MAX_BYTES (200_000)
    recorded_hash = hashlib.sha256(b"PRETEND-ORIGINAL-MASTER-BYTES").hexdigest()

    # An on-disk sidecar that must MOVE with the folder (no content change).
    (folder / "uid").write_text("dark-uid-123", encoding="utf-8")

    entry = {
        entry_id: {
            "short_id": mvcommon.generate_short_id(entry_id),
            "filename": "DarkMovie.mkv",
            "folder_path": str(folder),
            "status": "archived",
            "uploaded": True,
            "hash": recorded_hash,
            "metadata": {"title": "Dark", "year": "2017"},
        }
    }
    sandbox["lib_movies"].write_text(json.dumps(entry), encoding="utf-8")
    return entry_id, folder, dummy, recorded_hash


# ---------------------------------------------------------------------------
# (a) renames the on-disk folder + rewrites folder_path for season_map + leaves
# ---------------------------------------------------------------------------

def test_renames_folder_and_rewrites_season_map_and_leaves(sandbox_alias):
    """The season folder is renamed on disk and BOTH the season_map container and
    the episode leaf get their folder_path re-pointed to the new folder."""
    sandbox = sandbox_alias["sandbox"]
    old_folder = sandbox_alias["media_dir"]  # …\Series\BSG\Season 04
    new_name = "Season 04 {tmdb-12345}"
    new_folder = old_folder.parent / new_name

    result = main.cmd_rename_folder(sandbox_alias["season_id"], new_name)
    assert result is True

    # On-disk: old gone, new present, the primary .mkv moved with the folder.
    assert not old_folder.exists()
    assert new_folder.is_dir()
    assert (new_folder / sandbox_alias["orig_path"].name).exists()

    lib = _series(sandbox)
    # season_map container re-pointed
    assert main._norm_path(lib[sandbox_alias["season_id"]]["folder_path"]) == main._norm_path(str(new_folder))
    # episode leaf re-pointed
    assert main._norm_path(lib[sandbox_alias["primary_id"]]["folder_path"]) == main._norm_path(str(new_folder))
    # the leaf's filename is unchanged (only the folder moved)
    assert lib[sandbox_alias["primary_id"]]["filename"] == sandbox_alias["orig_path"].name


def test_resolves_a_plain_folder_path_arg(sandbox_alias):
    """The target can be given as an on-disk folder PATH (not only an id)."""
    sandbox = sandbox_alias["sandbox"]
    old_folder = sandbox_alias["media_dir"]
    new_name = "Season 04 renamed"
    new_folder = old_folder.parent / new_name

    result = main.cmd_rename_folder(str(old_folder), new_name)
    assert result is True
    assert new_folder.is_dir()
    lib = _series(sandbox)
    assert main._norm_path(lib[sandbox_alias["primary_id"]]["folder_path"]) == main._norm_path(str(new_folder))


# ---------------------------------------------------------------------------
# (b) works on archived dummies + performs NO rehash
# ---------------------------------------------------------------------------

def test_archived_dummy_rename_is_hash_safe(sandbox):
    """An archived entry (status=archived, tiny dummy on disk) is renamed; the
    stored `hash` is byte-identical afterwards (no rehash) and the dummy + its
    sidecar move with the folder."""
    entry_id, folder, dummy, recorded_hash = _seed_archived_movie(sandbox)
    new_name = "DarkMovie {tmdb-70523}"
    new_folder = folder.parent / new_name

    result = main.cmd_rename_folder(entry_id, new_name)
    assert result is True

    # Folder moved; dummy + sidecar carried along untouched.
    assert not folder.exists()
    assert new_folder.is_dir()
    assert (new_folder / "DarkMovie.mkv").read_bytes() == b"DUMMY"
    assert (new_folder / "uid").read_text(encoding="utf-8") == "dark-uid-123"

    lib = _movies(sandbox)
    assert main._norm_path(lib[entry_id]["folder_path"]) == main._norm_path(str(new_folder))
    # HASH-SAFE: the recorded hash string is untouched (no rehash of the dummy).
    assert lib[entry_id]["hash"] == recorded_hash
    assert lib[entry_id]["status"] == "archived"


# ---------------------------------------------------------------------------
# (c) a failure AFTER the PONR (mid-folder_path-rewrite) is recoverable
# ---------------------------------------------------------------------------

def test_post_ponr_failure_is_recoverable(sandbox_alias, monkeypatch):
    """SIMULATED FAILURE: save_library raises AFTER the on-disk rename (the PONR)
    while the folder_path rewrites are being persisted — modelling a crash in the
    post-PONR window. ASSERTED POST-STATE: the on-disk folder IS at the new path;
    the crossed journal survives in the STABLE parent dir; the existing
    recover_journal() correctly DECLINES to auto-undo a crossed journal (returns
    False, leaves the folder put); and a RE-RUN self-heals the torn window so the
    library finally points at the new folder. This mirrors test_rollback.py's
    hard-kill-then-recover pattern and cmd_replace's C9 forward self-heal."""
    sandbox = sandbox_alias["sandbox"]
    old_folder = sandbox_alias["media_dir"]
    parent_dir = old_folder.parent
    new_name = "Season 04 {tmdb-12345}"
    new_folder = parent_dir / new_name

    # Kill save_library on its FIRST call (which happens post-PONR, after os.rename).
    real_save = main.save_library
    state = {"killed": False}

    def killing_save(lib):
        if not state["killed"]:
            state["killed"] = True
            raise SystemExit("simulated crash mid folder_path rewrite (post-PONR)")
        real_save(lib)

    monkeypatch.setattr(main, "save_library", killing_save)

    with pytest.raises(SystemExit):
        main.cmd_rename_folder(sandbox_alias["season_id"], new_name)

    # The on-disk rename committed (PONR crossed before the kill).
    assert not old_folder.exists()
    assert new_folder.is_dir()

    # The journal survived in the PARENT dir and recorded that it crossed the PONR.
    journal_path = parent_dir / main.TXN_JOURNAL_NAME
    assert journal_path.exists(), "journal must persist a post-PONR crash"
    jdata = json.loads(journal_path.read_text(encoding="utf-8"))
    assert jdata["crossed_ponr"] is True

    # The library was NOT saved before the kill — it still points at the OLD folder.
    lib_before = _series(sandbox)
    assert main._norm_path(lib_before[sandbox_alias["primary_id"]]["folder_path"]) == main._norm_path(str(old_folder))

    # recover_journal correctly DECLINES a crossed journal (no auto-undo) and leaves
    # the folder where it is — the existing recover path's documented behavior.
    monkeypatch.setattr(main, "save_library", real_save)
    recovered = main.recover_journal(str(parent_dir))
    assert recovered is False, "a crossed-PONR journal is left for inspection, not undone"
    assert journal_path.exists(), "recover must leave a crossed journal untouched"
    assert new_folder.is_dir(), "recover must not move the folder back"

    # RE-RUN self-heals the torn window: folder already moved, library still on OLD
    # -> finish the pointer rewrite + drop the leftover journal.
    result = main.cmd_rename_folder(sandbox_alias["season_id"], new_name)
    assert result is True
    lib_after = _series(sandbox)
    assert main._norm_path(lib_after[sandbox_alias["primary_id"]]["folder_path"]) == main._norm_path(str(new_folder))
    assert main._norm_path(lib_after[sandbox_alias["season_id"]]["folder_path"]) == main._norm_path(str(new_folder))
    assert not journal_path.exists(), "self-heal clears the leftover journal"


def test_pre_ponr_failure_rolls_back(sandbox_alias, monkeypatch):
    """SIMULATED FAILURE: os.rename raises (the directory move itself fails) — a
    PRE-PONR failure. ASSERTED POST-STATE: the in-command rollback replays the
    set_field inverses, the folder is untouched at its old path, the library is
    unchanged, and the journal is removed (a clean, fully-reversible rollback)."""
    sandbox = sandbox_alias["sandbox"]
    old_folder = sandbox_alias["media_dir"]
    parent_dir = old_folder.parent
    new_name = "Season 04 {tmdb-12345}"

    def boom_rename(src, dst):
        raise OSError("simulated directory rename failure (pre-PONR)")

    monkeypatch.setattr(main.os, "rename", boom_rename)

    result = main.cmd_rename_folder(sandbox_alias["season_id"], new_name)
    assert result is False

    # Folder never moved; library folder_paths unchanged; journal cleaned up.
    assert old_folder.is_dir()
    lib = _series(sandbox)
    assert main._norm_path(lib[sandbox_alias["primary_id"]]["folder_path"]) == main._norm_path(str(old_folder))
    assert not (parent_dir / main.TXN_JOURNAL_NAME).exists()


# ---------------------------------------------------------------------------
# (d) a multi_ep_alias under the folder is left untouched
# ---------------------------------------------------------------------------

def test_multi_ep_alias_is_left_untouched(sandbox_alias):
    """The combined-episode alias under the renamed folder is never given a
    folder_path; its exact 3-key schema is preserved (PR#21 crash class avoided)."""
    sandbox = sandbox_alias["sandbox"]
    new_name = "Season 04 {tmdb-12345}"

    result = main.cmd_rename_folder(sandbox_alias["season_id"], new_name)
    assert result is True

    lib = _series(sandbox)
    alias = lib[sandbox_alias["alias_id"]]
    assert "folder_path" not in alias, "rename_folder must NOT stamp a folder_path onto an alias"
    assert set(alias.keys()) == {"type", "alias_of", "parent_id"}
    assert alias["type"] == "multi_ep_alias"
    assert alias["alias_of"] == sandbox_alias["primary_id"]


# ---------------------------------------------------------------------------
# (e) refuses when the new folder already exists
# ---------------------------------------------------------------------------

def test_refuses_when_new_folder_exists(sandbox_alias, capsys):
    """If the target folder already exists, the command refuses and changes
    nothing (no rename, no library rewrite, no journal left behind)."""
    sandbox = sandbox_alias["sandbox"]
    old_folder = sandbox_alias["media_dir"]
    parent_dir = old_folder.parent
    new_name = "Season 04 {tmdb-12345}"
    # Pre-create the collision target.
    (parent_dir / new_name).mkdir()

    result = main.cmd_rename_folder(sandbox_alias["season_id"], new_name)
    assert result is False

    out = capsys.readouterr().out
    assert "already exists" in out

    # Old folder untouched; library unchanged; no journal created.
    assert old_folder.is_dir()
    lib = _series(sandbox)
    assert main._norm_path(lib[sandbox_alias["primary_id"]]["folder_path"]) == main._norm_path(str(old_folder))
    assert not (parent_dir / main.TXN_JOURNAL_NAME).exists()


def test_refuses_unknown_id_and_missing_folder(sandbox, capsys):
    """An id not in the library and not a real path is refused with a clear message."""
    _empty(sandbox)
    result = main.cmd_rename_folder("mov-does-not-exist-xyz", "Whatever {tmdb-1}")
    assert result is False
    out = capsys.readouterr().out
    assert "No such folder" in out
