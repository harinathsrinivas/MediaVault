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


# Glued anime sSSEE shape (the IMP-C18 repro): child ids stick season+episode with no
# e/x separator (…-s0202 = season 02, episode 02). The base/season id is …-s02, so
# stripping it leaves "02" = ep 2 (NOT 0202=202, the old unanchored-fallback bug).
ANIME_SEASON_ID = "ani-ja-2013-kurokosbasketball-s02"
ANIME_EP_IDS = [f"{ANIME_SEASON_ID}{n:02d}" for n in (1, 2, 3)]  # …-s0201/02/03


def _seed_anime_ssee_season(sandbox, make_video, *, uploaded=False):
    """Seed a season_map with glued anime children (…-s0201/02/03), real files on disk.

    Mirrors _seed_season_two but for the sSSEE id shape that exposed IMP-C18 (the range
    filter must strip the season base before reading the episode number). All three lib
    files are written; ani-* ids route to library_anime.json via save_library.
    """
    media_dir = sandbox["media_dir"]
    out = {}
    for ep_id in ANIME_EP_IDS:
        fname = f"Kuroko {ep_id[-4:]}.mkv"  # e.g. "Kuroko s0202.mkv"
        path, fhash = make_video(media_dir / fname, marker=ep_id.encode())
        out[ep_id] = {
            "short_id": _short(ep_id), "filename": fname, "folder_path": str(media_dir),
            "status": "onboarded" if uploaded else "local_ready", "uploaded": uploaded,
            "hash": fhash, "metadata": {"title": "Kuroko", "year": 2013},
            "tech_spec": {"resolution": "1080p", "size_bytes": os.path.getsize(path)},
            "parent_id": ANIME_SEASON_ID,
        }
    library = {
        ANIME_SEASON_ID: {"type": "season_map", "folder_path": str(media_dir),
                          "total_episodes": len(ANIME_EP_IDS),
                          "children": list(ANIME_EP_IDS)},
        **out,
    }
    _write_all_libs(sandbox, library)
    return {"season_id": ANIME_SEASON_ID, "ep_ids": list(ANIME_EP_IDS), "media_dir": media_dir}


# ===========================================================================
# IMP-D18 Others/Sports category helpers (oth- prefix)
# ===========================================================================

OTH_SEASON_ID = "oth-football-2026-fifaworldcup-s01"
OTH_EP_IDS = [f"{OTH_SEASON_ID}e0{i}" for i in (1, 2, 3)]


def _seed_others_season(sandbox, make_video, *, uploaded=False):
    """Seed a season_map with THREE oth- episodes and real >200 KB files on disk.

    Mirrors _seed_season_two but for the IMP-D18 Others/Sports category:
    - Season id: oth-football-2026-fifaworldcup-s01
    - Episodes: …e01, …e02, …e03 (3 real files)
    - Files placed under LOCAL_ROOT/Sports/Football/FIFA World Cup/FIFA World Cup 2026/
      so the Step-4 disk walkers find them.
    - All four lib files written (oth- ids route to library_others.json via save_library).

    Returns dict(season_id, ep_ids, oth_dir).
    """
    oth_dir = (sandbox["local_root"] / "Sports" / "Football"
               / "FIFA World Cup" / "FIFA World Cup 2026")
    oth_dir.mkdir(parents=True, exist_ok=True)
    out = {}
    for i, ep_id in enumerate(OTH_EP_IDS, 1):
        fname = f"FifaWC2026.e{i:02d}.mkv"
        path, fhash = make_video(oth_dir / fname, marker=ep_id.encode())
        out[ep_id] = {
            "short_id": _short(ep_id), "filename": fname,
            "folder_path": str(oth_dir),
            "status": "onboarded" if uploaded else "local_ready",
            "uploaded": uploaded,
            "hash": fhash,
            "metadata": {"title": "FIFA World Cup 2026", "year": 2026},
            "tech_spec": {"resolution": "2160p", "size_bytes": os.path.getsize(path)},
            "parent_id": OTH_SEASON_ID,
        }
    library = {
        OTH_SEASON_ID: {
            "type": "season_map", "folder_path": str(oth_dir),
            "total_episodes": len(OTH_EP_IDS), "children": list(OTH_EP_IDS),
        },
        **out,
    }
    _write_all_libs(sandbox, library)
    # save_library routes oth- ids to LIBRARY_OTHERS; guarantee the file exists
    # (mirrors the movies/series/anime guarantee in _write_all_libs).
    if not sandbox["lib_others"].exists():
        sandbox["lib_others"].write_text("{}", encoding="utf-8")
    return {"season_id": OTH_SEASON_ID, "ep_ids": list(OTH_EP_IDS), "oth_dir": oth_dir}


# ===========================================================================
# IMP-D19 extras helpers (bonus-content folders: Specials/ , Extra/)
# ===========================================================================

EXTRAS_SEASON_ID = "tv-en-2021-smkx-s01"
EXTRAS_EP_ID = f"{EXTRAS_SEASON_ID}e01"
EXTRAS_GROUP_REL = "Specials"


def _stored_extras(library, title_id, group_rel=EXTRAS_GROUP_REL):
    """The stored item list of one extras group, in library order (mirrors
    tests/test_extras.py's `_items` accessor)."""
    return library[title_id]["extras"]["groups"][group_rel]["items"]


def _extras_block_on_disk(title_dir, title_id, make_video):
    """Create a real 2-file `Specials/` group on disk; return (extras_block, items).

    ONE place owns the extras item field set for this suite (used by both
    _seed_title_with_extras and the alias sweep). Field provenance =
    scan_extras_folders (main.py:3754-3763), i.e. the same shape as conftest's
    `sandbox_extras` (testing-strategy §4.9) and the canonical `_extras_block` in
    tests/test_entry_schema_guard.py: sub_rel is the bare filename (flat group),
    short_id = generate_short_id(f"{title_id}::{group_rel}/{sub_rel}"),
    search_term = f"{name} [{short_id}]{ext}", items sorted by sub_rel, items in
    the PRE-PUSH state (status="local_ready", uploaded=False) a fresh scan
    produces. Every stored `hash` is the file's REAL sha256, so the
    push/replace/restore hash verification the lifecycle performs is genuine, not
    stubbed — the smoke's phases move the items forward from here.

    Returns (extras_block, items):
        extras_block - the Card A2 nested dict to assign to entry["extras"]
        items        - [{"sub_rel","filename","path","hash","short_id"}, ...]
    """
    extras_dir = title_dir / EXTRAS_GROUP_REL
    # Hard guard (mirrors conftest's sandbox_extras): every extras file this helper
    # creates lives in the caller's temp sandbox — never real C:\Media.
    assert "C:\\Media" not in str(extras_dir), \
        f"extras seed must never touch real C:\\Media: {extras_dir}"
    extras_dir.mkdir(parents=True, exist_ok=True)
    items = []
    for fn, marker in (("BTS.mkv", b"EXTRA-BTS\n"), ("Trailer.mkv", b"EXTRA-TRAILER\n")):
        path = extras_dir / fn
        _, file_hash = make_video(path, marker=marker)
        items.append({"sub_rel": fn, "filename": fn, "path": path, "hash": file_hash,
                      "short_id": _short(f"{title_id}::{EXTRAS_GROUP_REL}/{fn}")})
    items.sort(key=lambda it: it["sub_rel"])   # canonical within-group order

    def _item(it):
        name, ext = os.path.splitext(it["filename"])
        return {
            "filename": it["filename"],
            "sub_rel": it["sub_rel"],
            "short_id": it["short_id"],
            "hash": it["hash"],
            "status": "local_ready",
            "uploaded": False,
            "search_term": f"{name} [{it['short_id']}]{ext}",
            "tech_spec": {"resolution": "1080p", "size_bytes": os.path.getsize(it["path"])},
        }

    # added_date is merge_extras_into_title's new-group stamp; no code reads its
    # value, so a fixed date keeps the seed deterministic.
    block = {"groups": {EXTRAS_GROUP_REL: {"added_date": "2026-01-01",
                                           "items": [_item(it) for it in items]}}}
    return block, items


def _seed_title_with_extras(sandbox, make_video):
    """Seed a season_map TITLE carrying a 2-item extras group, plus 1 leaf episode.

    The SEASON-shaped counterpart of conftest's movie-leaf `sandbox_extras`
    (§4.9) — series is the common extras case, and the block nests on the
    season_map (the TITLE), never on the episode leaf (Card A2).

    Files go under LOCAL_ROOT/Series/ (NOT sandbox["media_dir"], which sits under
    Movies/) so the disk WALKERS read them against the library file that actually
    holds these entries — library_series.json for tv- ids. Same reasoning as
    _seed_others_season's Sports placement; it is what makes the "extras are not
    UNPREPPED" assertion meaningful instead of vacuous.

        <LOCAL_ROOT>/Series/SMKX/SMKX.S01E01.mkv        <- the leaf episode
        <LOCAL_ROOT>/Series/SMKX/Specials/BTS.mkv       <- extras item 1
        <LOCAL_ROOT>/Series/SMKX/Specials/Trailer.mkv   <- extras item 2

    Every file is real media (make_video > DUMMY_MAX_BYTES) with its real sha256
    stored. The leaf is pre-push (local_ready / uploaded=False) so a test can
    assert the extras lifecycle never touched the MAIN content (Card E1).

    Returns dict(title_id, ep_id, title_dir, group_rel, extras_dir, ep_filename,
                 ep_path, items=[{sub_rel, filename, path, hash, short_id}, ...]).
    """
    title_dir = sandbox["local_root"] / "Series" / "SMKX"
    title_dir.mkdir(parents=True, exist_ok=True)

    ep_filename = "SMKX.S01E01.mkv"
    ep_path = title_dir / ep_filename
    _, ep_hash = make_video(ep_path, marker=EXTRAS_EP_ID.encode())
    ep_short = _short(EXTRAS_EP_ID)

    extras_block, items = _extras_block_on_disk(title_dir, EXTRAS_SEASON_ID, make_video)
    library = {
        EXTRAS_SEASON_ID: {
            "type": "season_map",
            "folder_path": str(title_dir),
            "total_episodes": 1,
            "children": [EXTRAS_EP_ID],
            "extras": extras_block,     # Card A2: extras nest on the TITLE entry
        },
        EXTRAS_EP_ID: {
            "short_id": ep_short, "filename": ep_filename, "folder_path": str(title_dir),
            "status": "local_ready", "uploaded": False,
            "search_term": f"SMKX.S01E01 [{ep_short}].mkv",
            "hash": ep_hash, "metadata": {"title": "SMKX", "year": 2021},
            "tech_spec": {"resolution": "1080p", "size_bytes": os.path.getsize(ep_path)},
            "parent_id": EXTRAS_SEASON_ID,
        },
    }
    _write_all_libs(sandbox, library)
    return {"title_id": EXTRAS_SEASON_ID, "ep_id": EXTRAS_EP_ID, "title_dir": title_dir,
            "group_rel": EXTRAS_GROUP_REL, "extras_dir": title_dir / EXTRAS_GROUP_REL,
            "ep_filename": ep_filename, "ep_path": ep_path, "items": items}


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

    # ---- web / collect_reclaimable (the web command's testable core) ---------
    def test_web_collect_reclaimable(self, sandbox, make_video, smoke_local_root, capsys):
        """Exercise collect_reclaimable (the `web` data layer) — NOT uvicorn.

        Starting the server would bind a port / hang the suite, so the smoke
        drives the pure data-function that powers GET /api/reclaim. Mirrors
        test_scan_unprepped's on-disk seeding: a known leaf (LOCAL_NOT_PUSHED)
        plus an EXTRA file unknown to the library. The extra file MUST be written
        via make_video (>DUMMY_MAX_BYTES) — collect_reclaimable gates UNPREPPED
        emission on real on-disk size, so a sub-threshold file would NOT appear.
        """
        _seed_single(sandbox, make_video)  # one local_ready leaf, real file on disk
        # An UNPREPPED extra file in the Series tree: real (>DUMMY_MAX_BYTES) so it
        # qualifies as reclaimable, and not in the library so it classifies UNPREPPED.
        extra_path, _ = make_video(sandbox["media_dir"] / "Unprepped.S01E99.mkv")

        result = main.collect_reclaimable()   # must not raise

        # Contract keys present and well-typed.
        assert set(result) >= {"items", "total_reclaimable_bytes", "total_reclaimable_human"}
        assert isinstance(result["items"], list)
        assert isinstance(result["total_reclaimable_bytes"], int)
        assert isinstance(result["total_reclaimable_human"], str)

        # The extra on-disk file appears as an UNPREPPED row (match by basename,
        # never a bracketed glob; path is a walk-join so compare on the name).
        extra_name = os.path.basename(str(extra_path))
        unprepped = [it for it in result["items"]
                     if it["badge"] == "UNPREPPED"
                     and os.path.basename(it["path"]) == extra_name]
        assert len(unprepped) == 1, (
            f"expected one UNPREPPED row for {extra_name}, got: "
            f"{[(it['badge'], it['path']) for it in result['items']]!r}"
        )

        # Optional, lightweight: the FastAPI app factory imports (no port bound,
        # no server started). Skipped cleanly where fastapi is absent.
        pytest.importorskip("fastapi")
        from webui.server import create_app
        assert create_app() is not None

    # ---- web / fetch_restore via the HTTP action path ------------------------
    # Secure-by-default auth (IMP-E15) is always enforced on /api/*; drive the API
    # as the LOCAL OWNER (TestClient host "testclient" is non-loopback => would 401)
    # via the web_as_local_admin fixture so the smoke exercises the endpoint, not auth.
    @pytest.mark.usefixtures("web_as_local_admin")
    def test_web_fetch_restore_action(self, sandbox, make_video, mock_device, capsys):
        """Drive fetch_restore through the real HTTP action path (POST /api/action/
        fetch_restore -> poll GET /api/job/{id}) without spawning a real subprocess.

        mock_device neutralizes both subprocess.run (ADB) and subprocess.Popen
        (the mainfetch worker subprocess that cmd_dispatch_fetch spawns), so no
        browser or real process is started. The seeded entry is onboarded+uploaded
        so cmd_fetch_restore runs the single-item branch, which prints
        "FETCH & RESTORE COMPLETE" and returns None — a _NONE_IS_SUCCESS action
        the server marks "done". The test polls GET /api/job/{id} with a short
        deadline (well within the smoke budget) and asserts the terminal status is
        "done" (not "error" / not wedged).

        Constraints honoured: never touches real C:\\Media / real library_*.json;
        all I/O is in sandbox tmp_path. Skipped cleanly where fastapi is absent.
        """
        pytest.importorskip("fastapi")
        import time as _time
        from starlette.testclient import TestClient
        from webui.server import create_app

        _seed_single(sandbox, make_video, uploaded=True, status="onboarded")

        client = TestClient(create_app())
        r = client.post("/api/action/fetch_restore", json={"id": EP1_ID})
        assert r.status_code == 202, f"expected 202, got {r.status_code}: {r.text}"
        job_id = r.json()["job_id"]

        # Poll until the job leaves "running" state. The fake subprocess completes
        # immediately; deadline is generous (10s) but still well inside smoke budget.
        deadline = _time.monotonic() + 10
        job = None
        while _time.monotonic() < deadline:
            jr = client.get(f"/api/job/{job_id}")
            assert jr.status_code == 200, f"job poll {jr.status_code}: {jr.text}"
            job = jr.json()
            if job["status"] in ("done", "error"):
                break
            _time.sleep(0.05)
        else:
            raise TimeoutError(
                f"fetch_restore job {job_id!r} did not finish within 10s; last: {job!r}"
            )

        assert job["status"] == "done", (
            f"fetch_restore via web action should finish as 'done', got: {job!r}"
        )
        # progress dict is always present (IMP-E14 Phase 2 contract).
        assert "progress" in job, "job record must carry a 'progress' key"
        assert "done" in job["progress"] and "total" in job["progress"], (
            f"progress must have {{done, total}}, got: {job['progress']!r}"
        )

    # ---- web / GET /api/items import-safety + contract -----------------------
    def test_web_items_plain(self, sandbox, make_video, capsys):
        """GET /api/items returns 200 with {items, by_category} over a plain
        sandbox library — import-safety and contract shape check.

        Two levels of coverage:
        1. Direct: main.items_payload() must not raise and must return the
           contract dict with the expected top-level keys.
        2. HTTP: GET /api/items via TestClient must return 200 and the same
           contract keys in the JSON body.

        Both levels are light (no deep content assertion) — Group 1 unit tests
        own correctness; this smoke asserts "the endpoint is wired and the
        payload builder does not crash on a normal library". Skipped cleanly
        where fastapi is absent.
        """
        _seed_single(sandbox, make_video, uploaded=False)

        # Level 1: in-process call.
        result = main.items_payload()   # must not raise
        assert set(result) >= {"items", "by_category"}, (
            f"items_payload contract keys missing; got: {set(result)!r}"
        )
        assert isinstance(result["items"], list)
        assert isinstance(result["by_category"], dict)

        # At least one item for our seeded leaf (the season_map is skipped).
        assert any(it["id"] == EP1_ID for it in result["items"]), (
            f"seeded leaf {EP1_ID!r} not found in items_payload; "
            f"items: {[it['id'] for it in result['items']]!r}"
        )

        # Level 2: HTTP via TestClient. Skipped cleanly where fastapi is absent.
        pytest.importorskip("fastapi")
        from unittest.mock import patch as _patch
        from starlette.testclient import TestClient
        import webui.server as _web_server
        from webui.server import create_app

        # Secure-by-default auth (IMP-E15) is always enforced on /api/*. TestClient's
        # host is "testclient" (non-loopback => non-admin) and would 401, so behave
        # as the genuine-local admin for this endpoint check. Patched inline (not via
        # the web_as_local_admin fixture) so Level 1 above still runs without fastapi.
        with _patch.object(_web_server, "_is_genuine_local_admin", lambda request: True):
            client = TestClient(create_app())
            r = client.get("/api/items")
        assert r.status_code == 200, f"GET /api/items returned {r.status_code}: {r.text}"
        body = r.json()
        assert set(body) >= {"items", "by_category"}, (
            f"/api/items response missing contract keys; got: {set(body)!r}"
        )

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

    # ---- IMP-C18: 0-via-range guard suppresses the auto-pilot ✅✅✅ banner -----
    def _seed_anime_restore_copies(self, info, ep_ids):
        """Seed restore/ copies for the given anime episodes (so cmd_restore moves
        them back) and overwrite the live files with placeholders. Mirrors the
        test_restore_group seeding so a real selection actually restores > 0 files."""
        restore_folder = info["media_dir"] / main.RESTORE_DIR_NAME
        restore_folder.mkdir(exist_ok=True)
        lib = mvcommon.load_library()
        for ep_id in ep_ids:
            fname = lib[ep_id]["filename"]
            src = info["media_dir"] / fname
            (restore_folder / fname).write_bytes(open(src, "rb").read())
            with open(src, "wb") as f:
                f.write(b"PLACEHOLDER")

    def test_fetch_restore_empty_range_suppresses_banner(
            self, sandbox, make_video, mock_device, capsys):
        """0-via-range run: the ⚠️ warning fires, the green ✅✅✅ banner is SUPPRESSED,
        and nothing raises / no sys.exit (acceptance (a) + (c) of step 4)."""
        info = _seed_anime_ssee_season(sandbox, make_video, uploaded=True)
        # 98-99 matches no episode in a non-empty (3-child) season -> 0-via-range.
        main.cmd_fetch_restore(info["season_id"], "98-99")  # must not raise / exit
        out = capsys.readouterr().out
        assert "⚠️" in out, f"expected the 0-match warning, got: {out!r}"
        assert "0 items" in out, f"expected the suppressed-banner summary, got: {out!r}"
        assert "✅✅✅ FETCH & RESTORE COMPLETE." not in out, (
            f"the green banner must be suppressed over 0 items, got: {out!r}"
        )

    def test_fetch_restore_real_range_keeps_banner(
            self, sandbox, make_video, mock_device, capsys):
        """Real selection (episodes 2-3 over a glued sSSEE season) restores 2 files,
        so the ✅✅✅ banner STILL prints (acceptance (b) of step 4)."""
        info = _seed_anime_ssee_season(sandbox, make_video, uploaded=True)
        # Episodes 2 and 3 are …-s0202 / …-s0203 (the original IMP-C18 repro shape).
        self._seed_anime_restore_copies(info, info["ep_ids"][1:])  # s0202, s0203
        main.cmd_fetch_restore(info["season_id"], "2-3")
        out = capsys.readouterr().out
        assert "✅✅✅ FETCH & RESTORE COMPLETE." in out, (
            f"a real 2-item selection must keep the success banner, got: {out!r}"
        )
        lib = mvcommon.load_library()
        # The two selected episodes restored; the unselected ep01 did not.
        assert lib[info["ep_ids"][1]]["status"] == "restored_local"
        assert lib[info["ep_ids"][2]]["status"] == "restored_local"
        assert lib[info["ep_ids"][0]]["status"] != "restored_local"

    # ---- IMP-C18: push_group / restore_group select correctly on sSSEE ids ----
    def test_push_group_episode_range_anime_ssee(
            self, sandbox, make_video, mock_device, capsys):
        """push_group with episode_range="2-3" selects …-s0202 and …-s0203 only.

        This is the cross-command push coverage for the IMP-C18 fix: previously the
        unanchored fallback would parse '0202' as 202, skipping all episodes in a
        sSSEE season. Range "2-3" must match exactly ep02 and ep03 (not ep01).
        """
        info = _seed_anime_ssee_season(sandbox, make_video, uploaded=False)
        main.cmd_push_group(info["season_id"], episode_range="2-3")
        lib = mvcommon.load_library()
        # Episodes 2 and 3 were selected; episode 1 was NOT.
        assert lib[info["ep_ids"][1]]["uploaded"] is True   # …-s0202 pushed
        assert lib[info["ep_ids"][2]]["uploaded"] is True   # …-s0203 pushed
        assert lib[info["ep_ids"][0]]["uploaded"] is False  # …-s0201 skipped

    def test_restore_group_episode_range_anime_ssee(
            self, sandbox, make_video, capsys):
        """restore_group with episode_range="2-3" restores …-s0202 and …-s0203 only.

        Mirrors test_restore_group seeding (restore/ copies + placeholder live files)
        but for the sSSEE id shape. Asserts exactly 2 episodes reach restored_local
        and the unselected …-s0201 is not touched.
        """
        info = _seed_anime_ssee_season(sandbox, make_video, uploaded=True)
        # Seed restore/ copies only for ep02 and ep03 (s0202, s0203).
        self._seed_anime_restore_copies(info, info["ep_ids"][1:])  # s0202, s0203
        count = main.cmd_restore_group(info["season_id"], "2-3")
        lib = mvcommon.load_library()
        assert lib[info["ep_ids"][1]]["status"] == "restored_local"  # …-s0202 restored
        assert lib[info["ep_ids"][2]]["status"] == "restored_local"  # …-s0203 restored
        assert lib[info["ep_ids"][0]]["status"] != "restored_local"  # …-s0201 untouched
        # cmd_restore_group returns the count of successfully restored entries.
        assert count == 2

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

    # ---- set_tmdb -----------------------------------------------------------
    def test_set_tmdb(self, sandbox, make_video, capsys):
        """set_tmdb <id> <tmdb_id> writes metadata.tmdb_id on the leaf; no crash."""
        info = _seed_single(sandbox, make_video)
        main.cmd_set_tmdb(info["id"], "12345")
        lib = mvcommon.load_library()
        assert lib[info["id"]]["metadata"]["tmdb_id"] == 12345

    # ---- rename_folder ------------------------------------------------------
    def test_rename_folder(self, sandbox, make_video, capsys):
        """rename_folder <id> <new-name> renames the on-disk folder + rewrites
        folder_path for every library entry that lives under it. No rehash."""
        info = _seed_single(sandbox, make_video)
        old_folder = info["folder"]
        new_name = old_folder.name + " [tmdbid-99901]"  # stamp a token

        result = main.cmd_rename_folder(info["id"], new_name)
        assert result is True

        # Folder physically moved.
        new_folder = old_folder.parent / new_name
        assert new_folder.is_dir()
        assert not old_folder.exists()

        # Library pointer rewritten.
        lib = mvcommon.load_library()
        assert new_name in lib[EP1_ID]["folder_path"]

        # Hash is UNCHANGED (rename is path-only, not a rehash).
        assert lib[EP1_ID]["hash"] == info["hash"]

    def test_rename_folder_via_alias(self, sandbox_alias, capsys):
        """rename_folder via an alias-bearing library: season_map folder renamed,
        all children (primary + alias) have their folder_path updated correctly.
        No multi_ep_alias corruption; alias still has type == multi_ep_alias."""
        old_media = sandbox_alias["media_dir"]
        new_name = old_media.name + " [tmdbid-1984]"

        result = main.cmd_rename_folder(sandbox_alias["season_id"], new_name)
        assert result is True

        new_folder = old_media.parent / new_name
        assert new_folder.is_dir()
        assert not old_media.exists()

        lib = mvcommon.load_library()
        # Primary leaf's folder_path updated.
        assert new_name in lib[sandbox_alias["primary_id"]]["folder_path"]
        # Alias entry UNCHANGED (it has no folder_path — it's a 3-key virtual entry).
        alias = lib[sandbox_alias["alias_id"]]
        assert alias["type"] == "multi_ep_alias"
        assert "alias_of" in alias and "parent_id" in alias

    # ---- enrich_metadata (with mock_tmdb) -----------------------------------
    def test_enrich_metadata_dry_run(self, sandbox, make_video, mock_tmdb, capsys):
        """DRY-RUN (no --apply): a confident match is REPORTED but NOTHING is
        written. No network call escapes (mock_tmdb intercepts main.requests.get)."""
        info = _seed_single(sandbox, make_video)
        # Adjust the entry's metadata title so mock_tmdb's canned "smk" result matches.
        lib = mvcommon.load_library()
        lib[info["id"]].setdefault("metadata", {})["title"] = "SMK"
        mvcommon.save_library(lib)
        before = mvcommon.load_library()

        main.cmd_enrich_metadata(info["id"])  # no --apply -> dry-run

        out = capsys.readouterr().out
        assert "DRY-RUN" in out
        # Library not changed.
        assert mvcommon.load_library() == before
        # No real subprocess was called (no ADB / no media fetch).
        # The mock_tmdb fixture guarantees no real network hit.

    def test_enrich_metadata_apply(self, sandbox, make_video, mock_tmdb, capsys):
        """--apply: writes metadata.tmdb_id on a confident match. No network call
        escapes, no subprocess.run (no ADB), no Popen (no media fetch). The
        stored entry hash is untouched (no rehash)."""
        # Ensure subprocess.run / Popen would raise if somehow called.
        import main as _main
        _orig_run = _main.subprocess.run
        _orig_popen = _main.subprocess.Popen

        def _no_run(*a, **k):
            raise AssertionError("enrich_metadata must NOT call subprocess.run")

        def _no_popen(*a, **k):
            raise AssertionError("enrich_metadata must NOT call subprocess.Popen")

        _main.subprocess.run = _no_run
        _main.subprocess.Popen = _no_popen

        try:
            info = _seed_single(sandbox, make_video)
            lib = mvcommon.load_library()
            lib[info["id"]].setdefault("metadata", {})["title"] = "SMK"
            mvcommon.save_library(lib)
            original_hash = mvcommon.load_library()[info["id"]]["hash"]

            main.cmd_enrich_metadata(info["id"], "--apply")
        finally:
            _main.subprocess.run = _orig_run
            _main.subprocess.Popen = _orig_popen

        out = capsys.readouterr().out
        assert "APPLIED" in out or "tmdb_id=99901" in out

        lib = mvcommon.load_library()
        assert lib[info["id"]]["metadata"]["tmdb_id"] == 99901
        # Hash must not change (no rehash).
        assert lib[info["id"]]["hash"] == original_hash


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

    def test_web_reclaim_alias(self, sandbox_alias, smoke_local_root):
        """Anti-PR#21 guard: the NEW collect_reclaimable whole-library walker must
        tolerate season_map + multi_ep_alias rows (skip them before dereferencing
        folder_path/filename). Asserts it does not raise and honours the contract."""
        result = main.collect_reclaimable()
        assert set(result) >= {"items", "total_reclaimable_bytes", "total_reclaimable_human"}

    def test_web_items_alias(self, sandbox_alias, smoke_local_root):
        """Anti-PR#21 guard for items_payload: the whole-library walker in
        items_payload() must skip season_map + multi_ep_alias rows without
        crashing and without emitting virtual rows in the output.

        The sandbox_alias library has three entries: one season_map, one real
        leaf (primary_id), and one multi_ep_alias. items_payload must emit
        exactly ONE item (the primary leaf) — not the season_map or the alias.
        This mirrors test_web_reclaim_alias's PR#21 regression guard, extended
        to the items_payload code path introduced in IMP-E14.
        """
        result = main.items_payload()   # must not raise
        assert set(result) >= {"items", "by_category"}, (
            f"items_payload contract keys missing on alias library; got: {set(result)!r}"
        )
        # Only the primary leaf should appear; virtual rows (season_map /
        # multi_ep_alias) must NOT be emitted.
        emitted_ids = [it["id"] for it in result["items"]]
        assert sandbox_alias["primary_id"] in emitted_ids, (
            f"primary leaf {sandbox_alias['primary_id']!r} missing from items; "
            f"got: {emitted_ids!r}"
        )
        assert sandbox_alias["alias_id"] not in emitted_ids, (
            f"multi_ep_alias {sandbox_alias['alias_id']!r} leaked into items output — "
            f"PR#21 regression (virtual rows must be skipped). emitted: {emitted_ids!r}"
        )
        assert sandbox_alias["season_id"] not in emitted_ids, (
            f"season_map {sandbox_alias['season_id']!r} leaked into items output — "
            f"virtual rows must be skipped. emitted: {emitted_ids!r}"
        )

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

    # -- set_tmdb / rename_folder / enrich_metadata over the alias library -----

    def test_set_tmdb_alias(self, sandbox_alias):
        """set_tmdb on the primary id in an alias-bearing library: no crash, no
        corruption of the alias entry."""
        main.cmd_set_tmdb(sandbox_alias["primary_id"], "1984")
        lib = mvcommon.load_library()
        assert lib[sandbox_alias["primary_id"]]["metadata"]["tmdb_id"] == 1984
        # Alias must survive the set_tmdb call unchanged.
        assert lib[sandbox_alias["alias_id"]]["type"] == "multi_ep_alias"

    def test_rename_folder_alias(self, sandbox_alias, capsys):
        """rename_folder over an alias-bearing season: folder renamed, primary's
        folder_path updated, alias 3-key shape preserved (no folder_path on alias)."""
        old_media = sandbox_alias["media_dir"]
        new_name = old_media.name + " [tmdbid-1984]"
        result = main.cmd_rename_folder(sandbox_alias["season_id"], new_name)
        assert result is True
        # The renamed folder must exist and the old one must not.
        new_folder = old_media.parent / new_name
        assert new_folder.is_dir()
        assert not old_media.exists()
        lib = mvcommon.load_library()
        assert new_name in lib[sandbox_alias["primary_id"]]["folder_path"]
        alias = lib[sandbox_alias["alias_id"]]
        assert alias["type"] == "multi_ep_alias"  # alias still valid

    def test_enrich_metadata_alias(self, sandbox_alias, mock_tmdb, capsys):
        """enrich_metadata (dry-run) over an alias-bearing library: must not crash,
        must not corrupt the multi_ep_alias entry. No network call escapes
        (mock_tmdb intercepts main.requests.get)."""
        # The alias library has a BSG show — mock_tmdb has a canned "bsg" result.
        main.cmd_enrich_metadata()  # whole library, dry-run (no --apply)
        out = capsys.readouterr().out
        # No crash; the output should mention the enrich header.
        assert "ENRICH METADATA" in out
        # The alias entry must not have been mutated (dry-run + alias has no metadata).
        lib = mvcommon.load_library()
        assert lib[sandbox_alias["alias_id"]]["type"] == "multi_ep_alias"

    # -- IMP-D19: the sweep must ALSO survive an extras-bearing library --------
    # Extras nest on the TITLE entry (here the season_map), and every
    # whole-library iterator calls _extras_item_paths on EVERY entry — including
    # the multi_ep_alias, which owns no folder_path — BEFORE the physical-leaf
    # type skip. A regression there breaks every walker at once: precisely the
    # "new nested shape silently breaks a distant command" class this sweep exists
    # to catch. Both new tests keep the sweep contract: must not raise.

    def _with_extras(self, sandbox_alias, make_video):
        """Attach a real 2-item Specials/ group to the alias library's season_map.

        The files land in <season folder>/Specials/ (under the sandbox Series
        tree), so the category walkers see them against library_series.json —
        the file that actually holds the BSG entries."""
        library = mvcommon.load_library()
        block, items = _extras_block_on_disk(
            sandbox_alias["media_dir"], sandbox_alias["season_id"], make_video)
        library[sandbox_alias["season_id"]]["extras"] = block
        mvcommon.save_library(library)
        return items

    def test_iterators_with_extras_alias(self, sandbox_alias, make_video,
                                         smoke_local_root, fake_dummy, capsys):
        """Every whole-library iterator this sweep drives, re-driven over a library
        holding BOTH a multi_ep_alias AND an extras block: none may raise, the
        extras are never mis-flagged UNPREPPED (they ARE library content — IMP-D19
        Step 7), and both nested shapes survive cmd_sort's library rewrite."""
        items = self._with_extras(sandbox_alias, make_video)

        main.cmd_scan_unprepped()
        out = capsys.readouterr().out
        for it in items:
            assert it["filename"] not in out, (
                f"extras item {it['filename']} mis-flagged UNPREPPED: {out!r}"
            )

        result = main.collect_reclaimable()
        assert set(result) >= {"items", "total_reclaimable_bytes", "total_reclaimable_human"}
        badges = {os.path.basename(r["path"]): r["badge"] for r in result["items"]}
        for it in items:
            assert badges.get(it["filename"]) == "LOCAL_NOT_PUSHED", (
                f"extras must be badged by their ITEM status, not UNPREPPED; got {badges!r}"
            )

        assert set(main.items_payload()) >= {"items", "by_category"}
        main.cmd_local_status()
        main.cmd_local_status("40gb")
        main.cmd_repair_dummies()
        main.cmd_sort()
        capsys.readouterr()

        # Both nested structures survive the sort round-trip.
        lib = mvcommon.load_library()
        assert lib[sandbox_alias["alias_id"]]["type"] == "multi_ep_alias"
        assert [it["sub_rel"] for it in _stored_extras(lib, sandbox_alias["season_id"])] == \
               [it["sub_rel"] for it in items]

    def test_single_id_commands_with_extras_alias(self, sandbox_alias, make_video,
                                                  mock_device, fake_dummy, capsys):
        """push / replace / fetch_restore given the ALIAS id over an extras-bearing
        library. Each call does TWO resolutions off the same alias id: de-alias to
        the primary leaf (main content) and _extras_title_id -> the season_map that
        owns the extras. Asserts the extras' own top-level effects (on the device,
        then dummied) and that the alias entry is never corrupted."""
        season_id = sandbox_alias["season_id"]
        alias_id = sandbox_alias["alias_id"]
        items = self._with_extras(sandbox_alias, make_video)

        # push <alias> --extras: the primary uploads AND the title's extras push.
        assert main.cmd_push(
            alias_id, extras=[str(sandbox_alias["media_dir"] / EXTRAS_GROUP_REL)]) is True
        on_device = {f.name for f in mock_device.rglob("*.mkv")}  # never a "[id]" glob
        for it in items:
            base, ext = os.path.splitext(it["filename"])
            assert f"{base} [{it['short_id']}]{ext}" in on_device, (
                f"extras not pushed via the alias id; on device: {sorted(on_device)!r}"
            )
        lib = mvcommon.load_library()
        assert all(it["uploaded"] is True for it in _stored_extras(lib, season_id))

        # replace <alias>: the primary archives and so do the title's extras.
        from conftest import FAKE_DUMMY_BYTES
        assert main.cmd_replace(alias_id) is True
        lib = mvcommon.load_library()
        assert all(it["status"] == "archived" for it in _stored_extras(lib, season_id))
        for it in items:
            assert it["path"].read_bytes() == FAKE_DUMMY_BYTES

        # fetch_restore <alias> --fetchExtras: nothing staged -> silent no-op.
        main.cmd_fetch_restore(alias_id, fetch_extras=True)
        capsys.readouterr()
        lib = mvcommon.load_library()
        assert lib[alias_id]["type"] == "multi_ep_alias"
        assert all(it["status"] == "archived" for it in _stored_extras(lib, season_id))


# ===========================================================================
# Group 3 — IMP-D18 Others/Sports category smoke (oth- prefix)
# Three smokes: round-trip, season sweep, enrich-skip.
# ===========================================================================

class TestOthersCategory:
    """Smoke coverage for the IMP-D18 oth- content category.

    Three orthogonal smokes (no crash + correct top-level effect):
      1. round-trip  — scan_unprepped / local_status / sort / verify_library see the
                       oth- season and its Sports files without crashing.
      2. season sweep — push_group pushes all 3 episodes; restore_group with range
                        "1-2" restores exactly 2 and leaves ep3 untouched; a
                        fetch_restore call over the season is also neutralized by
                        mock_device without crash.
      3. enrich-skip  — cmd_enrich_metadata dry-run produces 0 oth- units in
                        _gather_enrich_units and does not rename the Sports folder.
    """

    # ---- round-trip -------------------------------------------------------
    def test_oth_round_trip(self, sandbox, make_video, smoke_local_root, capsys):
        """scan_unprepped / local_status / sort / verify_library run without crashing
        over an oth- season library and report the oth- entries / Sports files."""
        info = _seed_others_season(sandbox, make_video)

        main.cmd_scan_unprepped()  # must not crash; oth- files are prepped (in library)
        capsys.readouterr()

        main.cmd_local_status()
        out = capsys.readouterr().out
        # At least one oth- leaf should appear in the local_status output.
        assert any(ep_id in out for ep_id in OTH_EP_IDS), (
            f"cmd_local_status must list oth- entries; output: {out!r}"
        )

        main.cmd_sort()
        out = capsys.readouterr().out
        assert "Library sorted" in out

        # verify_library must not crash and must return True (no violations):
        # all three entries are local_ready + real files on disk -> clean.
        result = main.cmd_verify_library()
        capsys.readouterr()
        assert result is True, "cmd_verify_library must return True for clean oth- season"

    # ---- season sweep (push_group + restore_group) -----------------------
    def test_oth_season_sweep(self, sandbox, make_video, mock_device, capsys):
        """push_group uploads all 3 oth- episodes; restore_group with range '1-2'
        restores exactly 2 and leaves ep3 untouched; fetch_restore must not crash."""
        info = _seed_others_season(sandbox, make_video, uploaded=False)

        # Phase 1: push all 3 episodes.
        main.cmd_push_group(info["season_id"])
        lib = mvcommon.load_library()
        for ep_id in OTH_EP_IDS:
            assert lib[ep_id]["uploaded"] is True, (
                f"push_group must upload {ep_id}; got: {lib[ep_id]!r}"
            )
        # Files should be on the (fake) device — search by name, never a bracketed glob.
        on_device = {f.name for f in mock_device.rglob("*.mkv")}
        assert any("FifaWC2026" in n for n in on_device), (
            f"oth- files must appear on device after push_group; found: {on_device!r}"
        )
        capsys.readouterr()

        # Phase 2: seed restore/ copies for episodes 1-2 only, then restore_group "1-2".
        restore_folder = info["oth_dir"] / main.RESTORE_DIR_NAME
        restore_folder.mkdir(exist_ok=True)
        lib = mvcommon.load_library()
        for ep_id in OTH_EP_IDS[:2]:
            fname = lib[ep_id]["filename"]
            data = open(info["oth_dir"] / fname, "rb").read()
            (restore_folder / fname).write_bytes(data)
            with open(info["oth_dir"] / fname, "wb") as f:
                f.write(b"PLACEHOLDER")

        count = main.cmd_restore_group(info["season_id"], "1-2")
        capsys.readouterr()
        lib = mvcommon.load_library()
        assert lib[OTH_EP_IDS[0]]["status"] == "restored_local", (
            f"ep01 must be restored_local after restore_group '1-2'; got: {lib[OTH_EP_IDS[0]]!r}"
        )
        assert lib[OTH_EP_IDS[1]]["status"] == "restored_local", (
            f"ep02 must be restored_local after restore_group '1-2'; got: {lib[OTH_EP_IDS[1]]!r}"
        )
        assert lib[OTH_EP_IDS[2]]["status"] != "restored_local", (
            f"ep03 is outside range '1-2' and must NOT be restored; got: {lib[OTH_EP_IDS[2]]!r}"
        )

        # Phase 3: fetch_restore over the season — mock_device neutralizes subprocess.
        # After phase 2, ep1/ep2 are restored_local; ep3 is still onboarded/uploaded.
        # fetch_restore on the whole season must not crash regardless of restore/ state.
        main.cmd_fetch_restore(info["season_id"])
        capsys.readouterr()

    # ---- enrich-skip ------------------------------------------------------
    def test_oth_enrich_skip(self, sandbox, make_video, mock_tmdb, capsys):
        """Dry-run cmd_enrich_metadata: 0 oth- units processed and Sports folder
        is untouched (no rename_folder on the Sports tree)."""
        info = _seed_others_season(sandbox, make_video)
        sports_dir = info["oth_dir"]

        main.cmd_enrich_metadata()  # whole-library dry-run (no --apply)
        capsys.readouterr()

        # Sports folder must not have been renamed or removed.
        assert sports_dir.is_dir(), (
            f"Sports dir must not be renamed by enrich_metadata: {sports_dir}"
        )

        # _gather_enrich_units must return zero oth- units.
        lib = mvcommon.load_library()
        units = main._gather_enrich_units(lib)
        oth_units = [u for u in units if u["key"].startswith("oth-")]
        assert len(oth_units) == 0, (
            f"_gather_enrich_units must exclude oth- entries; found: {oth_units!r}"
        )


# ===========================================================================
# Group 4 — IMP-D19 EXTRAS smoke (bonus-content folders: Specials/ , Extra/)
# Two smokes: the whole lifecycle round-trip, and the "extras are library
# content, not unprepped files" integrity assertion. The extras × alias
# interaction lives in TestAliasSweep (the anti-PR#21 gate).
# ===========================================================================

class TestExtras:
    """Smoke coverage for the IMP-D19 extras lifecycle (season-shaped title).

    Two orthogonal smokes (no crash + correct top-level effect):
      1. round-trip     — add_extras (scan+merge -> push -> replace) under
                          mock_device + fake_dummy, then fetch_restore
                          --fetchExtras: the extras land on the fake device, get
                          dummied locally, and come back hash-verified as
                          restored_local, all WITHOUT touching the main content.
      2. not-unprepped  — cmd_scan_unprepped and collect_reclaimable treat the
                          nested extras as library content (IMP-D19 Step 7) while
                          still reporting a genuinely unknown video that sits in
                          the very same Specials/ folder.

    FETCH LEG — how the external service is faked (and why): cmd_fetch_restore
    spawns `python mainfetch.py fetch … --fetchExtras`, which mock_device
    neutralizes (no browser, no real child). mainfetch's harvester loop is NEVER
    driven here — a hash miss busy-waits for minutes, which would wreck the smoke
    budget. Instead the download is simulated at the FILE-SYSTEM seam: the cloud
    copies are read back off the fake device and staged into
    <title>/Specials/restore/<item filename> — exactly where fetch_single_entry
    puts them for an extras entry (mainfetch.build_extras_entries gives each
    synthetic entry folder_path = the extra's own folder). The flag's own
    contribution (flag-only forwarding, Card C) is asserted directly on the
    spawned argv, so the smoke cannot pass if the forwarding is dropped.
    """

    # ---- round-trip -------------------------------------------------------
    def test_extras_round_trip(self, sandbox, make_video, stub_tech_specs,
                               mock_device, fake_dummy, monkeypatch, capsys):
        """add_extras -> (push + replace) -> fetch_restore --fetchExtras over a
        season_map title: every phase's top-level effect is asserted, and the
        title's MAIN content is untouched throughout (Card E1)."""
        from conftest import FAKE_DUMMY_BYTES
        info = _seed_title_with_extras(sandbox, make_video)
        originals = {it["filename"]: it["path"].read_bytes() for it in info["items"]}

        # --- Phase 1: add_extras = scan+merge -> push -> replace (Card D1) -----
        # The scan re-derives the SAME items (same sub_rel + same real hash), so
        # the merge is the documented idempotent no-op and the command proceeds to
        # push the extras whole-file (no --extras-size, no main split to inherit)
        # and then dummy them.
        main.cmd_add_extras(info["title_id"], [str(info["extras_dir"])])
        capsys.readouterr()

        # Effect 1: both extras reached the fake device under their UID-tagged
        # names, byte-identical, in a remote folder mirroring Specials/.
        on_device = {f.name: f for f in mock_device.rglob("*.mkv")}  # never a "[id]" glob
        for it in info["items"]:
            base, ext = os.path.splitext(it["filename"])
            remote = f"{base} [{it['short_id']}]{ext}"
            assert remote in on_device, f"{remote} not on device: {sorted(on_device)!r}"
            assert on_device[remote].read_bytes() == originals[it["filename"]]
            assert on_device[remote].parent.name == EXTRAS_GROUP_REL

        # Effect 2: every item is uploaded+archived and its local file is a dummy.
        lib = mvcommon.load_library()
        stored = _stored_extras(lib, info["title_id"])
        assert [it["status"] for it in stored] == ["archived", "archived"]
        assert all(it["uploaded"] is True for it in stored)
        for it in info["items"]:
            assert it["path"].read_bytes() == FAKE_DUMMY_BYTES

        # Effect 3: the MAIN content was never pushed, replaced, or re-statused.
        assert lib[info["ep_id"]]["status"] == "local_ready"
        assert lib[info["ep_id"]]["uploaded"] is False
        assert info["ep_path"].read_bytes() != FAKE_DUMMY_BYTES
        assert [n for n in on_device if n.startswith("SMKX.S01E01")] == []

        # --- Phase 2: the FETCH leg, simulated at the file-system seam ---------
        restore_dir = info["extras_dir"] / main.RESTORE_DIR_NAME
        restore_dir.mkdir()
        for it in info["items"]:
            base, ext = os.path.splitext(it["filename"])
            (restore_dir / it["filename"]).write_bytes(
                on_device[f"{base} [{it['short_id']}]{ext}"].read_bytes())

        # Record the argv of the (mock_device-neutralized) mainfetch subprocess:
        # with the download faked above, the forwarded flag is the fetch leg's
        # only observable effect, so assert it rather than assume it.
        fetch_argv = []
        neutralized_popen = main.subprocess.Popen

        def _recording_popen(cmd, **kwargs):
            fetch_argv.append(list(cmd))
            return neutralized_popen(cmd, **kwargs)

        monkeypatch.setattr(main.subprocess, "Popen", _recording_popen)

        main.cmd_fetch_restore(info["title_id"], fetch_extras=True)
        out = capsys.readouterr().out

        # Effect 4: --fetchExtras was forwarded VERBATIM to mainfetch (Card C) and
        # the pipeline completed (the main-content leg finds nothing staged and is
        # skipped without crashing).
        assert fetch_argv, "fetch_restore must dispatch the mainfetch subprocess"
        assert "--fetchExtras" in fetch_argv[0], (
            f"--fetchExtras must reach the mainfetch argv; got {fetch_argv[0]!r}"
        )
        assert "FETCH & RESTORE COMPLETE" in out

        # Effect 5: the staged bytes are back at <title>/Specials/<sub_rel> and
        # every item is restored_local (uploaded stays True — still in the cloud).
        # restore_one_extra REFUSES a hash mismatch, so byte equality here means
        # the push -> archive -> fetch -> restore round trip preserved the master.
        lib = mvcommon.load_library()
        restored = list(main._extras_item_paths(lib[info["title_id"]]))
        assert len(restored) == 2, f"expected 2 extras items, got {restored!r}"
        for _group, path, item in restored:
            assert item["status"] == "restored_local" and item["uploaded"] is True
            with open(path, "rb") as f:
                assert f.read() == originals[item["filename"]]
        assert not restore_dir.exists(), "the emptied restore/ staging folder is removed"

    # ---- extras are library content, not unprepped files ------------------
    def test_extras_are_not_flagged_unprepped(self, sandbox, make_video,
                                              smoke_local_root, capsys):
        """cmd_scan_unprepped must NOT list the extras (IMP-D19 Step 7 indexes them
        as known paths) while a genuinely unknown video inside the SAME Specials/
        folder still IS reported — proving the walker really looks into the group
        folder, so the exclusion comes from library knowledge, not from skipping
        the subfolder. collect_reclaimable (the web data layer's walker) gets the
        same check: extras are badged by their ITEM status, never UNPREPPED."""
        info = _seed_title_with_extras(sandbox, make_video)
        stray = "Stray.Bonus.mkv"
        make_video(info["extras_dir"] / stray)   # real bytes, unknown to the library

        main.cmd_scan_unprepped()   # must not raise
        out = capsys.readouterr().out
        assert "SCANNING FOR UNPREPPED FILES" in out
        assert stray in out, (
            f"an unknown video inside Specials/ must still be reported: {out!r}"
        )
        for it in info["items"]:
            assert it["filename"] not in out, (
                f"extras item {it['filename']} must NOT be listed as unprepped: {out!r}"
            )
        assert info["ep_filename"] not in out  # the known leaf is not unprepped either

        # Same integrity through the web data layer's walker.
        result = main.collect_reclaimable()
        capsys.readouterr()
        badges = {os.path.basename(r["path"]): r["badge"] for r in result["items"]}
        for it in info["items"]:
            assert badges.get(it["filename"]) == "LOCAL_NOT_PUSHED", (
                f"extras must be badged by their ITEM status; got {badges!r}"
            )
        assert badges.get(stray) == "UNPREPPED", (
            f"the unknown video must classify UNPREPPED; got {badges!r}"
        )


# ===========================================================================
# Group 4 — IMP-D22 archive+enrich autopilots (cmd_prep_push_rep_enrich /
# cmd_prep_push_rep_season_enrich).
#
# SMOKE SCOPE ONLY: "the command runs end-to-end, produces its top-level
# effect, and does not break its neighbours". The exhaustive behaviour matrix
# (every flag, every warn-and-continue boundary, the NFO element set, the EXA
# fallback, the archive-regression pins) lives in
# tests/test_prep_push_rep_enrich.py + tests/test_prep_push_rep_season_enrich.py.
#
# THE HAZARD THIS GROUP EXISTS TO GUARD: `_make_rename_confirm("ask")`
# (main.py, grep "^def _make_rename_confirm") holds the ONLY input() call in
# the entire codebase. It is reached solely when `sys.stdin.isatty()` is True —
# False under pytest — so a smoke run can never block on it. `_forbid_input`
# below turns that implicit guarantee into an EXPLICIT, fast-failing one: if a
# future change ever reaches the prompt, this gate fails in milliseconds
# instead of hanging every future PR forever.
#
# TMDB: the shared `mock_tmdb` fixture (no network can escape, caches
# redirected, EXA sealed off) is used as-is; `_serve_tmdb_details` layers the
# two by-id DETAILS endpoints on top of it, because a preset `-tmdbid` resolves
# via `_resolve_unit_by_id` (GET /3/{movie,tv}/{id}) — an endpoint MockTMDB
# answers with its generic `{}` fallback.
# ===========================================================================

ENRICH_MOVIE_ID = "mov-en-2024-autoenrich"
ENRICH_TMDB_ID = 424242

# /3/movie/{id} DETAILS — the exact shape `_resolve_unit_by_id` reads for a
# movie unit (title / release_date / poster_path / backdrop_path).
_ENRICH_MOVIE_DETAILS = {
    "id": ENRICH_TMDB_ID, "title": "Auto Enrich", "release_date": "2024-03-15",
    "poster_path": "/smoke_poster.jpg", "backdrop_path": "/smoke_backdrop.jpg",
    "overview": "A movie about testing the enrich autopilot.", "vote_average": 7.7,
}
# /3/tv/{id} DETAILS — same, for a show unit (name / first_air_date).
_ENRICH_SHOW_DETAILS = {
    "id": ENRICH_TMDB_ID, "name": "SMK", "first_air_date": "2020-01-01",
    "poster_path": "/smoke_poster.jpg", "backdrop_path": "/smoke_backdrop.jpg",
    "overview": "A show about testing the enrich autopilot.", "vote_average": 8.2,
}


def _empty_libs(sandbox):
    """Start from three empty library files (the prep-from-scratch precondition
    the per-command prep smokes above set up inline)."""
    for p in (sandbox["lib_movies"], sandbox["lib_series"], sandbox["lib_anime"]):
        p.write_text("{}", encoding="utf-8")


def _forbid_input(monkeypatch):
    """Make the codebase's ONE input() call a FAILURE rather than a HANG.

    Deliberately does NOT patch `sys.stdin.isatty` — that is the very guard
    being verified (patching it would defeat it). Under pytest isatty() is
    already False, so `_make_rename_confirm("ask")` auto-declines and this
    stub is never reached; it only fires if that guard ever regresses.
    """
    def _boom(*a, **k):
        raise AssertionError(
            "the smoke gate must never reach input(): _make_rename_confirm's "
            "prompt is guarded by sys.stdin.isatty(); reaching it would block "
            "the pre-PR gate for every future change")
    monkeypatch.setattr("builtins.input", _boom)


def _serve_tmdb_details(monkeypatch, mock_tmdb, details):
    """Layer by-id DETAILS endpoints on top of the shared `mock_tmdb` backend.

    `details` maps a URL suffix ("/movie/424242") to its payload. Everything
    else — /search/*, /configuration, image bytes, /season/…/images,
    /episode/…/images — DELEGATES to the fixture, so the fixture's "no real
    network call can escape" guarantee is preserved unchanged (main.requests.get
    is still fully replaced, and the delegate is the only other handler).
    """
    from conftest import _MockTMDBResp   # the fixture's own response stand-in
    base_get = mock_tmdb.get

    def get(url, params=None, headers=None, timeout=None, **kwargs):
        for suffix, payload in details.items():
            if url.endswith(suffix):
                mock_tmdb.calls.append((url, dict(params or {})))
                return _MockTMDBResp(200, json_data=payload)
        return base_get(url, params=params, headers=headers, timeout=timeout, **kwargs)

    monkeypatch.setattr(main.requests, "get", get)
    return mock_tmdb


class TestPrepPushRepEnrich:
    """Smoke coverage for the IMP-D22 archive+enrich autopilots.

    Four cases, all fast (the archive legs run against mock_device/fake_dummy,
    the TMDB leg against mock_tmdb — no real adb, no ffmpeg, no network):
      1. movie round trip   — archived + tmdb_id stamped on the entry + folder
                              token + poster on disk.
      2. movie, rename flags at their DEFAULT — the non-interactive path:
                              returns (does not hang), folder left unrenamed,
                              metadata.tmdb_id still written.
      3. season             — a 1-episode season through the season autopilot;
                              the SHOW folder gets the {tmdb-…} token.
      4. -tvdbid refusal    — both commands refuse before anything runs
                              (no TMDB call, nothing prepped).
    """

    # ---- 1. movie: full archive -> preset -> resolve-by-id -> stamp ---------
    def test_prep_push_rep_enrich_movie_round_trip(
            self, sandbox, make_video, stub_tech_specs, mock_device, fake_dummy,
            mock_tmdb, monkeypatch, capsys):
        """`-tmdbid` + `--yes`: the movie ends ARCHIVED, carries the tmdb_id,
        its folder gets the {tmdb-…} token and a poster lands inside it."""
        _empty_libs(sandbox)
        _forbid_input(monkeypatch)
        _serve_tmdb_details(monkeypatch, mock_tmdb,
                            {f"/movie/{ENRICH_TMDB_ID}": _ENRICH_MOVIE_DETAILS})
        folder = sandbox["media_dir"]
        path, _h = make_video(folder / "AutoEnrich.mkv")

        result = main.cmd_prep_push_rep_enrich(
            ENRICH_MOVIE_ID, str(path), tmdb_id=ENRICH_TMDB_ID, rename_choice="yes")

        assert result is True
        out = capsys.readouterr().out
        assert "AUTO-PILOT COMPLETE (archive + enrich)" in out

        lib = mvcommon.load_library()
        entry = lib[ENRICH_MOVIE_ID]
        assert entry["status"] == "archived"          # archive leg completed
        assert entry["metadata"]["tmdb_id"] == ENRICH_TMDB_ID   # enrich leg applied

        stamped = folder.parent / f"{folder.name} [tmdbid-{ENRICH_TMDB_ID}]"
        assert stamped.is_dir() and not folder.exists()
        assert entry["folder_path"] == str(stamped)
        assert (stamped / "poster.jpg").stat().st_size > 0

    # ---- 2. movie, DEFAULT rename flags: never interactive, never hangs -----
    def test_prep_push_rep_enrich_default_is_non_interactive_no_hang(
            self, sandbox, make_video, stub_tech_specs, mock_device, fake_dummy,
            mock_tmdb, monkeypatch, capsys):
        """No `--yes` / `--no-rename`: `rename_choice` stays "ask", which under a
        non-TTY session (every pytest / cron / script run) auto-declines instead
        of prompting. THE smoke-hang risk — `_forbid_input` makes a regression
        fail fast here rather than block the gate."""
        _empty_libs(sandbox)
        _forbid_input(monkeypatch)
        _serve_tmdb_details(monkeypatch, mock_tmdb,
                            {f"/movie/{ENRICH_TMDB_ID}": _ENRICH_MOVIE_DETAILS})
        folder = sandbox["media_dir"]
        path, _h = make_video(folder / "AutoEnrich.mkv")

        result = main.cmd_prep_push_rep_enrich(
            ENRICH_MOVIE_ID, str(path), tmdb_id=ENRICH_TMDB_ID)  # <- defaults

        assert result is True   # returning at all is half the assertion
        out = capsys.readouterr().out
        assert "non-interactive session" in out

        lib = mvcommon.load_library()
        entry = lib[ENRICH_MOVIE_ID]
        assert entry["status"] == "archived"
        # tmdb_id still written — the rename decision does not gate the metadata.
        assert entry["metadata"]["tmdb_id"] == ENRICH_TMDB_ID
        assert folder.is_dir(), "the non-interactive default must NOT rename"
        assert entry["folder_path"] == str(folder)
        assert not (folder.parent / f"{folder.name} [tmdbid-{ENRICH_TMDB_ID}]").exists()

    # ---- 3. season: the SHOW folder gets the token --------------------------
    def test_prep_push_rep_season_enrich_stamps_show_folder(
            self, sandbox, make_video, stub_tech_specs, mock_device, fake_dummy,
            mock_tmdb, monkeypatch, capsys):
        """A 1-episode season through the season autopilot: the episode ends
        ARCHIVED and the SHOW folder (== the season folder in this flat layout,
        the dominant real-library shape) carries the {tmdb-…} token."""
        _empty_libs(sandbox)
        _forbid_input(monkeypatch)
        _serve_tmdb_details(monkeypatch, mock_tmdb,
                            {f"/tv/{ENRICH_TMDB_ID}": _ENRICH_SHOW_DETAILS})
        series_root = sandbox["local_root"] / "Series"
        season_dir = series_root / "SMK.S01.2020"
        season_dir.mkdir(parents=True, exist_ok=True)
        make_video(season_dir / "SMK.S01E01.mkv", marker=b"e1")

        result = main.cmd_prep_push_rep_season_enrich(
            SEASON_ID, str(season_dir), tmdb_id=ENRICH_TMDB_ID, rename_choice="yes")

        assert result is True
        out = capsys.readouterr().out
        assert "AUTO-PILOT COMPLETE (archive + enrich)" in out

        lib = mvcommon.load_library()
        assert lib[EP1_ID]["status"] == "archived"
        assert lib[EP1_ID]["metadata"]["tmdb_id"] == ENRICH_TMDB_ID

        stamped = series_root / f"SMK.S01.2020 [tmdbid-{ENRICH_TMDB_ID}]"
        assert stamped.is_dir() and not season_dir.exists()
        assert lib[SEASON_ID]["folder_path"] == str(stamped)
        assert lib[EP1_ID]["folder_path"] == str(stamped)
        # The token must land on the show folder itself, never on the Series root.
        assert series_root.is_dir(), "the Series tree root must never be renamed"

    # ---- 4. -tvdbid: refused before anything runs (no TMDB call at all) -----
    def test_prep_push_rep_enrich_tvdbid_refused(self, sandbox, make_video, capsys):
        """Decision 1: MediaVault is TMDB-only. BOTH commands refuse `-tvdbid`
        up front — nothing is prepped, pushed or replaced (note this case takes
        no device/dummy/TMDB fixture at all: it cannot need one)."""
        _empty_libs(sandbox)
        folder = sandbox["media_dir"]
        path, _h = make_video(folder / "NoTvdb.mkv")

        assert main.cmd_prep_push_rep_enrich(
            ENRICH_MOVIE_ID, str(path), tvdb_id=123456) is False
        assert main.cmd_prep_push_rep_season_enrich(
            SEASON_ID, str(folder), tvdb_id=123456) is False

        out = capsys.readouterr().out
        assert out.count("-tvdbid is not supported") == 2

        assert mvcommon.load_library() == {}, "nothing may be prepped on a refusal"
        assert os.path.isfile(path) and os.path.getsize(path) > main.DUMMY_MAX_BYTES, \
            "the source file must be untouched (not dummied)"
