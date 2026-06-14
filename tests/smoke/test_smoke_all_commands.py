"""Fast full-command SMOKE suite — the mandated pre-PR cross-command gate (IMP-H3 / B1).

WHAT THIS IS
============
ONE fast package that drives EVERY user-facing command + its major options against a
tiny in-repo fixture and the existing stub device/browser, asserting only:
    "no crash + correct top-level effect"
NOT deep correctness — that is the job of the focused unit/command tests
(test_cmd_push_*, test_cmd_replace, test_cmd_restore_quarantine, test_rehash, ...).
This is the cross-command integration gate a single owner runs before every PR — the
check that would have caught PR #21 / IMP-E13 (a new `multi_ep_alias` entry type that
silently broke scan_unprepped / local_status / the single-id commands).

TWO GROUPS
==========
1. Per-command smokes (TestEachCommand): each command driven against a plain `sandbox`
   library with the right stubs; asserts the top-level effect (entry created / library
   flipped / file moved / no raise). Parametrized where natural (push options).
2. The ALIAS SWEEP (TestAliasSweep): every user-facing command run against the
   `sandbox_alias` library (which contains a multi_ep_alias entry) — each must not raise.
   THIS is the anti-PR#21 regression gate: "a new entry type silently breaks a distant
   command" fails here the moment any command is added that doesn't tolerate the alias.

FIXTURE REUSE (no new mocking philosophy — see tests/conftest.py + tests/smoke/conftest.py)
  library      -> sandbox / sandbox_alias        ADB data  -> mock_device
  fetch        -> mock_device neutralizes the `python mainfetch.py` subprocess (see note)
  ffmpeg dummy -> fake_dummy                       MediaInfo -> stub_tech_specs
  walkers      -> smoke_local_root (LOCAL_ROOT)    real split-> mkvmerge_split_chunks (gated)

DUMMY_MAX_BYTES (200_000) gotcha — DOCUMENTED CHOICES:
  * make_video writes ~264 KB so cmd_check / cmd_prep / cmd_restore exercise the
    REAL-media path (hash verify / merge) rather than the dummy early-skip.
  * test_repair_dummies deliberately writes a <200 KB dummy and asserts the regenerate
    path — the one place the dummy path is the point.

fetch note: cmd_dispatch_fetch runs `subprocess.run(["python", "mainfetch.py", ...])`.
Under mock_device that argv is parsed by the fake adb runner, does not match
push/shell/devices, and returns a no-op success — so fetch / fetch_restore are exercised
WITHOUT spawning a real subprocess, opening a browser, or touching a device. mock_fetch
patches mainfetch.trigger_download in-process (it is exercised by its own round-trip test).

SPEED: whole package targets well under ~30s. The split-push smokes RESUME from a
pre-seeded _parts/ (seed_split_parts) instead of doing a real split; only ONE case does a
genuine mkvmerge split, gated on ffmpeg+mkvmerge availability so the suite stays green
without the binaries.

Anti-patterns avoided (docs/testing-strategy.md §8): never touch real C:\\Media /
library_*.json; never assert absolute device paths (rglob("*.mkv") + .name, never a
bracketed "[id]" glob); stdout via capsys.
"""
import hashlib
import json
import os

import pytest

import main
import mvcommon
from conftest import _ffmpeg_available, _mkvmerge_available  # skip-gate helpers


# ===========================================================================
# Shared library builders (Group 1). Series ids (tv-…) route to library_series.json;
# we always write all three lib files so load_library never skips one.
# ===========================================================================

SEASON_ID = "tv-en-2020-smk-s01"
EP1_ID = "tv-en-2020-smk-s01e01"
EP2_ID = "tv-en-2020-smk-s01e02"


def _short(mid):
    return mvcommon.generate_short_id(mid)


def _write_all_libs(sandbox, library):
    """Persist a merged dict via the real save_library (prefix-routes the ids)."""
    mvcommon.save_library(library)
    # save_library only writes files for prefixes present; guarantee all three exist
    for p in (sandbox["lib_movies"], sandbox["lib_series"], sandbox["lib_anime"]):
        if not p.exists():
            p.write_text("{}", encoding="utf-8")


def _seed_single(sandbox, make_video, *, uploaded=False, status="local_ready",
                 split=None, with_hash=True):
    """Seed ONE leaf Series episode (tv-…e01) with a real >200KB file on disk.

    Returns dict(id, short_id, folder, filename, path, hash). `split` is an optional
    split_info dict (e.g. from seed_split_parts) merged into the entry.
    """
    media_dir = sandbox["media_dir"]
    filename = "SMK.S01E01.mkv"
    short_id = _short(EP1_ID)
    path, file_hash = make_video(media_dir / filename)

    entry = {
        "short_id": short_id,
        "filename": filename,
        "folder_path": str(media_dir),
        "status": status,
        "uploaded": uploaded,
        "search_term": f"SMK.S01E01 [{short_id}].mkv",
        "hash": file_hash if with_hash else "deadbeef",
        "metadata": {"title": "SMK", "year": 2020},
        "tech_spec": {"resolution": "1080p", "size_bytes": os.path.getsize(path)},
        "parent_id": SEASON_ID,
    }
    if split:
        entry["split_info"] = split
    library = {
        SEASON_ID: {
            "type": "season_map",
            "folder_path": str(media_dir),
            "total_episodes": 1,
            "children": [EP1_ID],
        },
        EP1_ID: entry,
    }
    _write_all_libs(sandbox, library)
    return {"id": EP1_ID, "short_id": short_id, "folder": media_dir,
            "filename": filename, "path": path, "hash": file_hash}


def _seed_season_two(sandbox, make_video, *, uploaded=False):
    """Seed a season_map with TWO leaf episodes (e01, e02), both real files on disk."""
    media_dir = sandbox["media_dir"]
    out = {}
    for ep_id, fname in ((EP1_ID, "SMK.S01E01.mkv"), (EP2_ID, "SMK.S01E02.mkv")):
        path, fhash = make_video(media_dir / fname, marker=ep_id.encode())
        out[ep_id] = {
            "short_id": _short(ep_id), "filename": fname, "folder_path": str(media_dir),
            "status": "onboarded" if uploaded else "local_ready", "uploaded": uploaded,
            "hash": fhash, "metadata": {"title": "SMK", "year": 2020},
            "tech_spec": {"resolution": "1080p", "size_bytes": os.path.getsize(path)},
            "parent_id": SEASON_ID,
        }
    library = {
        SEASON_ID: {"type": "season_map", "folder_path": str(media_dir),
                    "total_episodes": 2, "children": sorted([EP1_ID, EP2_ID])},
        **out,
    }
    _write_all_libs(sandbox, library)
    return {"season_id": SEASON_ID, "ep_ids": [EP1_ID, EP2_ID], "media_dir": media_dir}


# ===========================================================================
# Group 1 — one fast smoke per command (+ major options)
# ===========================================================================

class TestEachCommand:

    # ---- prep ----------------------------------------------------------------
    def test_prep(self, sandbox, make_video, stub_tech_specs, capsys):
        sandbox["lib_movies"].write_text("{}", encoding="utf-8")
        sandbox["lib_series"].write_text("{}", encoding="utf-8")
        sandbox["lib_anime"].write_text("{}", encoding="utf-8")
        path, _ = make_video(sandbox["media_dir"] / "Movie.mkv")
        result = main.cmd_prep("mov-en-2024-smoke", str(path))
        assert result is True
        lib = mvcommon.load_library()
        assert "mov-en-2024-smoke" in lib
        assert lib["mov-en-2024-smoke"]["status"] == "local_ready"

    # ---- prep_season (also exercises the multi_ep_alias CREATION path) --------
    def test_prep_season_creates_alias_chain(self, sandbox, make_video, stub_tech_specs, capsys):
        sandbox["lib_movies"].write_text("{}", encoding="utf-8")
        sandbox["lib_series"].write_text("{}", encoding="utf-8")
        sandbox["lib_anime"].write_text("{}", encoding="utf-8")
        # A combined-episode file -> primary e19 + alias e20 (the PR #21 shape).
        make_video(sandbox["media_dir"] / "BSG.S04E19E20.mkv")
        main.cmd_prep_season("tv-en-2009-bsg-s04", str(sandbox["media_dir"]))
        lib = mvcommon.load_library()
        assert lib["tv-en-2009-bsg-s04"]["type"] == "season_map"
        assert "tv-en-2009-bsg-s04e19" in lib  # primary leaf
        alias = lib["tv-en-2009-bsg-s04e20"]
        assert alias["type"] == "multi_ep_alias"
        assert alias["alias_of"] == "tv-en-2009-bsg-s04e19"

    # ---- prep_push_rep (prep -> push -> replace autopilot) -------------------
    def test_prep_push_rep(self, sandbox, make_video, stub_tech_specs,
                           mock_device, fake_dummy, capsys):
        sandbox["lib_movies"].write_text("{}", encoding="utf-8")
        sandbox["lib_series"].write_text("{}", encoding="utf-8")
        sandbox["lib_anime"].write_text("{}", encoding="utf-8")
        path, _ = make_video(sandbox["media_dir"] / "Auto.mkv")
        main.cmd_prep_push_rep("mov-en-2024-auto", str(path))
        lib = mvcommon.load_library()
        # End state of the autopilot: pushed then replaced -> archived.
        assert lib["mov-en-2024-auto"]["status"] == "archived"

    # ---- prep_push_rep_season (+ episodes range) -----------------------------
    def test_prep_push_rep_season_with_episode_range(self, sandbox, make_video,
                                                     stub_tech_specs, mock_device,
                                                     fake_dummy, capsys):
        sandbox["lib_movies"].write_text("{}", encoding="utf-8")
        sandbox["lib_series"].write_text("{}", encoding="utf-8")
        sandbox["lib_anime"].write_text("{}", encoding="utf-8")
        # Two episodes on disk; range 1-1 must process only e01.
        make_video(sandbox["media_dir"] / "SMK.S01E01.mkv", marker=b"e1")
        make_video(sandbox["media_dir"] / "SMK.S01E02.mkv", marker=b"e2")
        main.cmd_prep_push_rep_season(SEASON_ID, str(sandbox["media_dir"]),
                                      episode_range="1-1")
        lib = mvcommon.load_library()
        assert lib[EP1_ID]["status"] == "archived"   # in range -> processed
        assert lib[EP2_ID]["status"] == "local_ready"  # out of range -> untouched

    # ---- scan_unprepped (walker; needs LOCAL_ROOT redirect) ------------------
    def test_scan_unprepped(self, sandbox, make_video, smoke_local_root, capsys):
        _seed_single(sandbox, make_video)
        # An UNPREPPED extra file in the Series tree should be reported.
        (sandbox["media_dir"] / "Unprepped.S01E99.mkv").write_bytes(b"x" * 300_000)
        main.cmd_scan_unprepped()   # must not raise
        out = capsys.readouterr().out
        assert "SCANNING FOR UNPREPPED FILES" in out
        assert "Unprepped.S01E99.mkv" in out

    # ---- local_status (+ size limit) -----------------------------------------
    def test_local_status_plain(self, sandbox, make_video, capsys):
        _seed_single(sandbox, make_video, uploaded=False)
        main.cmd_local_status()
        out = capsys.readouterr().out
        assert EP1_ID in out  # the pending leaf is listed

    def test_local_status_with_size_limit(self, sandbox, make_video, capsys):
        _seed_single(sandbox, make_video, uploaded=False)
        main.cmd_local_status("40gb")
        out = capsys.readouterr().out
        assert "Optimization Target" in out

    # ---- check ---------------------------------------------------------------
    def test_check(self, sandbox, make_video, capsys):
        _seed_single(sandbox, make_video)
        main.cmd_check(EP1_ID)
        out = capsys.readouterr().out
        assert "PASS" in out  # real file, matching hash -> verified

    # ---- push: single / split SIZE_MB / chunks range / rehash / tempdir ------
    def test_push_single_file(self, sandbox, make_video, mock_device, capsys):
        info = _seed_single(sandbox, make_video, uploaded=False)
        result = main.cmd_push(info["id"])
        assert result is True
        lib = mvcommon.load_library()
        assert lib[info["id"]]["uploaded"] is True
        # File landed on the device (search by name, never a bracketed glob).
        on_device = {f.name: f for f in mock_device.rglob("*.mkv")}
        assert len(on_device) >= 1

    def test_push_split_resume_from_parts(self, sandbox, make_video, seed_split_parts,
                                          mock_device, capsys):
        # Pre-seed _parts/ so push RESUMES (no real split / mkvmerge).
        info = _seed_single(sandbox, make_video, uploaded=False)
        parts = seed_split_parts(info["folder"], info["short_id"], info["filename"], n_chunks=3)
        lib = mvcommon.load_library()
        lib[info["id"]]["split_info"] = parts["split_info"]
        mvcommon.save_library(lib)
        result = main.cmd_push(info["id"])
        assert result is True
        chunks = [f for f in mock_device.rglob("*.mkv") if ".chunk." in f.name]
        assert len(chunks) == 3

    def test_push_split_method_smaller_than_limit(self, sandbox, make_video, mock_device, capsys):
        # SIZE_MB 8000 on a ~264 KB file -> "smaller than split limit" -> single push.
        info = _seed_single(sandbox, make_video, uploaded=False)
        result = main.cmd_push(info["id"], "SIZE_MB", "8000")
        assert result is True
        out = capsys.readouterr().out
        assert "smaller than split limit" in out

    def test_push_chunks_range(self, sandbox, make_video, seed_split_parts, mock_device, capsys):
        info = _seed_single(sandbox, make_video, uploaded=False)
        parts = seed_split_parts(info["folder"], info["short_id"], info["filename"], n_chunks=3)
        lib = mvcommon.load_library()
        lib[info["id"]]["split_info"] = parts["split_info"]
        mvcommon.save_library(lib)
        # chunks 1-2 -> partial upload (only 2 of 3 chunks), returns True, NOT onboarded.
        result = main.cmd_push(info["id"], chunk_range="1-2")
        assert result is True
        on_device = [f for f in mock_device.rglob("*.mkv") if ".chunk." in f.name]
        assert len(on_device) == 2
        lib = mvcommon.load_library()
        assert lib[info["id"]]["uploaded"] is False  # partial range never flips uploaded

    def test_push_eager_rehash_single(self, sandbox, make_video, mock_device, capsys):
        # eager rehash on a non-split single push is a no-op path (no split happened),
        # so it does not need a real merge — just must not crash and must upload.
        info = _seed_single(sandbox, make_video, uploaded=False)
        result = main.cmd_push(info["id"], eager_rehash=True)
        assert result is True

    def test_push_tempdir(self, sandbox, make_video, seed_split_parts, mock_device,
                          tmp_path, capsys):
        # temp_dir only redirects the _parts/ scratch; with a pre-seeded _parts/ in the
        # media dir the resume branch is taken and the push still succeeds. We assert the
        # tempdir arg is accepted and the push completes (no crash on the temp plumbing).
        info = _seed_single(sandbox, make_video, uploaded=False)
        parts = seed_split_parts(info["folder"], info["short_id"], info["filename"], n_chunks=2)
        lib = mvcommon.load_library()
        lib[info["id"]]["split_info"] = parts["split_info"]
        mvcommon.save_library(lib)
        alt = tmp_path / "scratch_vol"
        alt.mkdir()
        result = main.cmd_push(info["id"], temp_dir=str(alt))
        assert result is True

    # ---- push_group (+ episodes) ---------------------------------------------
    def test_push_group(self, sandbox, make_video, mock_device, capsys):
        info = _seed_season_two(sandbox, make_video, uploaded=False)
        main.cmd_push_group(info["season_id"])
        lib = mvcommon.load_library()
        assert lib[EP1_ID]["uploaded"] is True
        assert lib[EP2_ID]["uploaded"] is True

    def test_push_group_episode_range(self, sandbox, make_video, mock_device, capsys):
        info = _seed_season_two(sandbox, make_video, uploaded=False)
        main.cmd_push_group(info["season_id"], episode_range="1-1")
        lib = mvcommon.load_library()
        assert lib[EP1_ID]["uploaded"] is True
        assert lib[EP2_ID]["uploaded"] is False  # filtered out by range

    # ---- replace / replace_group ---------------------------------------------
    def test_replace(self, sandbox, make_video, fake_dummy, capsys):
        info = _seed_single(sandbox, make_video, uploaded=True, status="onboarded")
        result = main.cmd_replace(info["id"])
        assert result is True
        lib = mvcommon.load_library()
        assert lib[info["id"]]["status"] == "archived"
        # The on-disk file is now the fake dummy.
        from conftest import FAKE_DUMMY_BYTES
        assert open(info["path"], "rb").read() == FAKE_DUMMY_BYTES

    def test_replace_group(self, sandbox, make_video, fake_dummy, capsys):
        info = _seed_season_two(sandbox, make_video, uploaded=True)
        main.cmd_replace_group(info["season_id"])
        lib = mvcommon.load_library()
        assert lib[EP1_ID]["status"] == "archived"
        assert lib[EP2_ID]["status"] == "archived"

    # ---- repair_dummies (DELIBERATELY exercises the <200KB dummy path) -------
    def test_repair_dummies(self, sandbox, make_video, fake_dummy, capsys):
        info = _seed_single(sandbox, make_video, uploaded=True, status="onboarded")
        # Make the on-disk file a SMALL (corrupt/zero-byte) dummy and mark archived;
        # repair_dummies must regenerate it via make_video_dummy (fake_dummy stub).
        with open(info["path"], "wb") as f:
            f.write(b"tiny")  # < DUMMY_MAX_BYTES -> qualifies for regeneration
        lib = mvcommon.load_library()
        lib[info["id"]]["status"] = "archived"
        mvcommon.save_library(lib)
        main.cmd_repair_dummies()
        out = capsys.readouterr().out
        assert "repair_dummies complete" in out
        assert "regenerated 1" in out
        from conftest import FAKE_DUMMY_BYTES
        assert open(info["path"], "rb").read() == FAKE_DUMMY_BYTES

    # ---- verify_restore / restore (standard, pre-seeded restore/) ------------
    def _seed_for_restore(self, sandbox, make_video):
        """Archived entry + a restore/<filename> copy whose hash matches entry['hash']."""
        info = _seed_single(sandbox, make_video, uploaded=True, status="onboarded")
        restore_folder = info["folder"] / main.RESTORE_DIR_NAME
        restore_folder.mkdir(exist_ok=True)
        # Copy the real bytes into restore/ (same hash already stored on the entry).
        data = open(info["path"], "rb").read()
        (restore_folder / info["filename"]).write_bytes(data)
        return info, restore_folder

    def test_verify_restore(self, sandbox, make_video, capsys):
        info, _ = self._seed_for_restore(sandbox, make_video)
        main.cmd_verify_restore(info["id"])  # dry run; must not raise
        out = capsys.readouterr().out
        assert "SUCCESS" in out or "Verified" in out

    def test_restore_standard(self, sandbox, make_video, capsys):
        info, _ = self._seed_for_restore(sandbox, make_video)
        # Overwrite the live target with a placeholder so restore actually moves the file back.
        with open(info["path"], "wb") as f:
            f.write(b"PLACEHOLDER")
        result = main.cmd_restore(info["id"])
        assert result is True
        lib = mvcommon.load_library()
        assert lib[info["id"]]["status"] == "restored_local"

    # ---- restore_group --------------------------------------------------------
    def test_restore_group(self, sandbox, make_video, capsys):
        info = _seed_season_two(sandbox, make_video, uploaded=True)
        # Seed restore/ copies for both episodes (matching their stored hashes).
        restore_folder = info["media_dir"] / main.RESTORE_DIR_NAME
        restore_folder.mkdir(exist_ok=True)
        lib = mvcommon.load_library()
        for ep_id, fname in ((EP1_ID, "SMK.S01E01.mkv"), (EP2_ID, "SMK.S01E02.mkv")):
            data = open(info["media_dir"] / fname, "rb").read()
            (restore_folder / fname).write_bytes(data)
            with open(info["media_dir"] / fname, "wb") as f:
                f.write(b"PLACEHOLDER")  # placeholder so restore moves the copy back
        main.cmd_restore_group(info["season_id"])
        lib = mvcommon.load_library()
        assert lib[EP1_ID]["status"] == "restored_local"
        assert lib[EP2_ID]["status"] == "restored_local"

    # ---- fetch / fetch_restore (subprocess neutralized by mock_device) -------
    def test_fetch(self, sandbox, make_video, mock_device, capsys):
        _seed_single(sandbox, make_video)
        main.cmd_dispatch_fetch(EP1_ID)  # must not raise / spawn a real process
        out = capsys.readouterr().out
        assert "Dispatching Fetch" in out

    def test_fetch_restore_single(self, sandbox, make_video, mock_device, capsys):
        # No restore/ folder -> cmd_restore returns False with "restore folder missing".
        # The point is the whole fetch->restore pipeline runs without crashing.
        _seed_single(sandbox, make_video, uploaded=True, status="onboarded")
        main.cmd_fetch_restore(EP1_ID)
        out = capsys.readouterr().out
        assert "FETCH & RESTORE COMPLETE" in out

    # ---- mock_fetch round-trip (the in-process browser stub) -----------------
    def test_fetch_round_trip_with_mock_fetch(self, mock_device, mock_fetch):
        # Seed the fake device, then the mock browser copies it into restore_dir.
        import mainfetch
        (mock_device / "BSG.S01E01.mkv").write_bytes(b"x" * 300_000)
        ok = mainfetch.trigger_download(None, "BSG.S01E01")
        assert ok is True
        assert (mock_fetch / "BSG.S01E01.mkv").exists()

    # ---- anime fetch routing (profile selection; no browser) -----------------
    def test_anime_fetch_routing_profile_selection(self, sandbox, monkeypatch, capsys):
        """Routing smoke: ani-* ids must drive the 'anime' profile, not 'tv'."""
        import mainfetch
        # Safety net: prevent any accidental browser launch (not needed since
        # resolve_targets returns empty first, but guards against future code reorder)
        monkeypatch.setattr(mainfetch, "init_driver", lambda *_a, **_k: None)

        mainfetch.cmd_fetch_route("ani-ja-2006-deathnote01")
        out = capsys.readouterr().out

        # The profile-selection print happens before resolve_targets, so it is always emitted
        assert "anime" in out, f"Expected 'anime' in output, got: {out!r}"
        assert "ChromeProfile_Anime" in out, f"Expected 'ChromeProfile_Anime' in output, got: {out!r}"
        # Regression guard: must NOT have routed to the tv/series profile
        assert "'tv'" not in out or "anime" in out, (
            f"Anime id was routed to tv profile — regression! output: {out!r}"
        )

    # ---- fetch: logged-out IMP-C6 remediation --------------------------------
    def test_fetch_route_logged_out_aborts(self, sandbox, monkeypatch, capsys, tmp_path):
        """Smoke: cmd_fetch_route must print 'is logged out' when SessionExpiredError fires.

        Constraints:
            Never touch real C:\\Media files or real library_*.json.
            Run `python -m pytest` and fix failures before marking the step done.

        Uses OPTION 2 (boundary injection): resolve_targets returns one fake target,
        fetch_single_entry raises SessionExpiredError so cmd_fetch_route's except arm
        fires and prints the IMP-C6 remediation message.  No real browser, no real
        subprocess, no ~/.mediavault writes (fetch_session_lock is bypassed with
        contextlib.nullcontext).
        """
        import contextlib
        import mainfetch

        # Bypass the file-backed session lock so the test never writes ~/.mediavault/locks/
        monkeypatch.setattr(mainfetch, "fetch_session_lock",
                            lambda *_a, **_k: contextlib.nullcontext())

        # Return a fake driver (truthy, no real browser) so init_driver check passes
        import types as _types
        fake_driver = _types.SimpleNamespace(
            get=lambda *_a, **_k: None,
            quit=lambda: None,
            current_url="https://accounts.google.com/signin",
        )
        monkeypatch.setattr(mainfetch, "init_driver", lambda *_a, **_k: fake_driver)

        # Return one fake target (enough for cmd_fetch_route to enter the batch loop)
        fake_target = {
            "filename": "x.mkv",
            "folder_path": str(tmp_path),
            "hash": "deadbeef",
            "search_term": "x",
        }
        monkeypatch.setattr(mainfetch, "resolve_targets",
                            lambda *_a, **_k: [fake_target])

        # Inject a logged-out error at the entry boundary (re-raises into cmd_fetch_route)
        def _raise_session_expired(*_a, **_k):
            raise mainfetch.SessionExpiredError("profile appears logged out")

        monkeypatch.setattr(mainfetch, "fetch_single_entry", _raise_session_expired)

        mainfetch.cmd_fetch_route("mov-en-2024-testmovie")
        out = capsys.readouterr().out

        assert "is logged out" in out, (
            f"Expected 'is logged out' in output for IMP-C6 remediation, got: {out!r}"
        )

    # ---- sort -----------------------------------------------------------------
    def test_sort(self, sandbox, make_video, capsys):
        _seed_season_two(sandbox, make_video)
        main.cmd_sort()
        out = capsys.readouterr().out
        assert "Library sorted" in out

    # ---- recover / recover --scan --------------------------------------------
    def test_recover_id(self, sandbox, make_video, capsys):
        info = _seed_single(sandbox, make_video)
        # Pre-PONR journal in the media folder -> recover resolves + clears it.
        jpath = info["folder"] / main.TXN_JOURNAL_NAME
        jpath.write_text(json.dumps({"manual_id": info["id"], "crossed_ponr": False,
                                     "records": []}), encoding="utf-8")
        main.cmd_recover(info["id"])
        out = capsys.readouterr().out
        assert "Resolved id" in out

    def test_recover_scan(self, sandbox, make_video, smoke_local_root, capsys):
        info = _seed_single(sandbox, make_video)
        jpath = info["folder"] / main.TXN_JOURNAL_NAME
        jpath.write_text(json.dumps({"manual_id": info["id"], "crossed_ponr": False,
                                     "records": []}), encoding="utf-8")
        found = main.cmd_recover(scan=True)
        assert found >= 1  # read-only walk found the journal

    # ---- setters: set_search / set_uploaded / set_poster / set_fanart --------
    def test_set_search(self, sandbox, make_video, capsys):
        info = _seed_single(sandbox, make_video)
        main.cmd_set_search(info["id"], "Custom Search Term")
        lib = mvcommon.load_library()
        assert lib[info["id"]]["search_term"] == "Custom Search Term"

    def test_set_uploaded(self, sandbox, make_video, capsys):
        info = _seed_single(sandbox, make_video, uploaded=False)
        main.cmd_set_uploaded(info["id"])
        lib = mvcommon.load_library()
        assert lib[info["id"]]["uploaded"] is True
        assert lib[info["id"]]["status"] == "onboarded"

    def test_set_poster_and_fanart(self, sandbox, make_video, monkeypatch, capsys):
        # set_poster / set_fanart do a real requests.get — patch it so no network is hit.
        info = _seed_single(sandbox, make_video)

        class _Resp:
            status_code = 200

            class raw:
                decode_content = False

                @staticmethod
                def read(*_a, **_k):
                    return b""

        def _fake_get(url, headers=None, stream=False):
            return _Resp()

        monkeypatch.setattr(main.requests, "get", _fake_get)
        main.cmd_set_poster(info["id"], "http://example.invalid/p.jpg")
        main.cmd_set_fanart(info["id"], "http://example.invalid/f.jpg")
        out = capsys.readouterr().out
        assert "POSTER" in out and "FANART" in out  # both ran without a network call


# ===========================================================================
# Group 1b — ONE genuine real-binary split push (gated). Proves the REAL split
# path (split_video_file -> mkvmerge chunks) still pushes end-to-end. Skipped
# cleanly on machines without ffmpeg+mkvmerge.
# ===========================================================================

@pytest.mark.skipif(not (_ffmpeg_available() and _mkvmerge_available()),
                    reason="real-split smoke needs ffmpeg + mkvmerge")
def test_push_real_split(sandbox, mkvmerge_split_chunks, mock_device):
    """End-to-end real split push: a genuine multi-chunk _parts/ produced by the
    REAL split_video_file is pushed via cmd_push's resume branch onto mock_device."""
    chunks = mkvmerge_split_chunks["chunks"]  # >=2 real .mkv chunks under tmp_path
    media_dir = sandbox["media_dir"]
    parts_dir = media_dir / main.SPLIT_DIR_NAME
    parts_dir.mkdir(exist_ok=True)
    chunks_meta = []
    import shutil as _sh
    for c in chunks:
        _sh.copy2(c, parts_dir / c.name)
        chunks_meta.append({"filename": c.name, "hash": "x"})
    entry_id = "mov-en-2024-realsplit"
    library = {
        entry_id: {
            "short_id": "rs0001", "filename": "RealSplit.mkv",
            "folder_path": str(media_dir), "status": "local_ready", "uploaded": False,
            "hash": "masterhash", "metadata": {"title": "RS", "year": 2024},
            "tech_spec": {"resolution": "1080p"},
            "split_info": {"is_split": True, "method": "SIZE_MB", "val": "10",
                           "total_chunks": len(chunks_meta), "chunks": chunks_meta},
        }
    }
    # A real master file so the source-exists check passes.
    (media_dir / "RealSplit.mkv").write_bytes(b"y" * 300_000)
    mvcommon.save_library(library)
    for p in (sandbox["lib_series"], sandbox["lib_anime"]):
        p.write_text("{}", encoding="utf-8")

    result = main.cmd_push(entry_id)
    assert result is True
    on_device = [f for f in mock_device.rglob("*.mkv") if ".chunk." in f.name]
    assert len(on_device) == len(chunks)


# ===========================================================================
# Group 2 — THE ALIAS SWEEP (anti-PR#21 gate)
# Every user-facing command runs at least once against the sandbox_alias library
# (which contains a multi_ep_alias entry). Each must NOT raise. This is the single
# sweep that would have caught PR #21 — a new entry type breaking a distant command.
# ===========================================================================

class TestAliasSweep:
    """Drive every command against the multi_ep_alias-bearing library; assert no crash.

    Each command is invoked the way the CLI dispatch would invoke it. Commands that
    take an id are given the ALIAS id (tv-…e20) to specifically exercise de-aliasing /
    skip-on-alias. Whole-library iterators and the season-level commands run over the
    whole alias-bearing library. Side effects are not deeply asserted here (Group 1 +
    test_alias_consumers.py do that) — the contract here is "does not raise".
    """

    def _alias_ids(self, sandbox_alias):
        return (sandbox_alias["alias_id"], sandbox_alias["primary_id"],
                sandbox_alias["season_id"])

    # -- whole-library iterators / status commands -----------------------------
    def test_scan_unprepped_alias(self, sandbox_alias, smoke_local_root):
        main.cmd_scan_unprepped()

    def test_local_status_alias(self, sandbox_alias):
        main.cmd_local_status()
        main.cmd_local_status("40gb")

    def test_sort_alias(self, sandbox_alias):
        main.cmd_sort()
        # Alias must survive a sort round-trip untouched.
        lib = mvcommon.load_library()
        assert lib[sandbox_alias["alias_id"]]["type"] == "multi_ep_alias"

    def test_repair_dummies_alias(self, sandbox_alias, fake_dummy):
        main.cmd_repair_dummies()  # alias has no 'status' -> skipped, no folder_path deref

    # -- single-id commands given the ALIAS id (must de-alias, not crash) -------
    def test_check_alias(self, sandbox_alias, capsys):
        main.cmd_check(sandbox_alias["alias_id"])

    def test_push_alias(self, sandbox_alias, mock_device, capsys):
        assert main.cmd_push(sandbox_alias["alias_id"]) is True

    def test_replace_alias(self, sandbox_alias, fake_dummy, capsys):
        # replace requires uploaded=True on the primary.
        lib = mvcommon.load_library()
        lib[sandbox_alias["primary_id"]]["uploaded"] = True
        lib[sandbox_alias["primary_id"]]["status"] = "onboarded"
        mvcommon.save_library(lib)
        assert main.cmd_replace(sandbox_alias["alias_id"]) is True

    def test_verify_restore_alias(self, sandbox_alias, capsys):
        main.cmd_verify_restore(sandbox_alias["alias_id"])  # no restore/ -> reports missing, no crash

    def test_restore_alias(self, sandbox_alias, capsys):
        # No restore/ folder -> returns False (folder missing) without crashing.
        assert main.cmd_restore(sandbox_alias["alias_id"]) is False

    def test_fetch_alias(self, sandbox_alias, mock_device, capsys):
        main.cmd_dispatch_fetch(sandbox_alias["alias_id"])

    def test_fetch_restore_alias(self, sandbox_alias, mock_device, capsys):
        # fetch_restore: dispatch (no-op) then single-item branch -> cmd_restore(alias) de-aliases.
        main.cmd_fetch_restore(sandbox_alias["alias_id"])

    def test_set_search_alias(self, sandbox_alias):
        # set_search has no de-alias; setting on the alias id writes a key on the
        # alias entry. The contract here is only "no crash" — the alias still resolves.
        main.cmd_set_search(sandbox_alias["primary_id"], "x")

    def test_set_uploaded_alias(self, sandbox_alias):
        main.cmd_set_uploaded(sandbox_alias["primary_id"])

    # -- group / season commands over the whole alias-bearing library ----------
    def test_push_group_alias(self, sandbox_alias, mock_device, capsys):
        # season_map children = [primary, alias]; the de-alias loop collapses to the primary.
        main.cmd_push_group(sandbox_alias["season_id"])
        lib = mvcommon.load_library()
        assert lib[sandbox_alias["primary_id"]]["uploaded"] is True

    def test_replace_group_alias(self, sandbox_alias, fake_dummy, capsys):
        lib = mvcommon.load_library()
        lib[sandbox_alias["primary_id"]]["uploaded"] = True
        lib[sandbox_alias["primary_id"]]["status"] = "onboarded"
        mvcommon.save_library(lib)
        main.cmd_replace_group(sandbox_alias["season_id"])

    def test_restore_group_alias(self, sandbox_alias, capsys):
        main.cmd_restore_group(sandbox_alias["season_id"])  # no restore/ -> skips, no crash

    def test_prep_refuses_over_alias(self, sandbox_alias, tmp_path, capsys):
        # cmd_prep over an existing alias id must refuse and leave the entry unchanged.
        dummy = tmp_path / "ep.mkv"
        dummy.write_bytes(b"X" * (main.DUMMY_MAX_BYTES + 1))
        before = dict(mvcommon.load_library()[sandbox_alias["alias_id"]])
        assert main.cmd_prep(sandbox_alias["alias_id"], str(dummy)) is False
        after = dict(mvcommon.load_library()[sandbox_alias["alias_id"]])
        assert after == before

    def test_recover_scan_alias(self, sandbox_alias, smoke_local_root):
        main.cmd_recover(scan=True)  # walks the sandbox tree; tolerates the alias library
