"""Tests for `cmd_prep_push_rep_enrich` — the movie autopilot + enrich command
(IMP-D22, Step 1, Candidate B: minimal additive `confirm_rename` hook on
`cmd_enrich_metadata`, composed via `cmd_set_tmdb` + `cmd_enrich_metadata`).

Exercises the FULL composition: `cmd_prep_push_rep` (untouched archive: prep ->
push -> replace) followed by the enrich leg (a preset `cmd_set_tmdb` + a
`cmd_enrich_metadata --apply` call whose folder-token rename is gated behind a
confirmation). Reuses the same archive-pipeline fixtures as
tests/test_extras.py's autopilot section (sandbox, make_video,
stub_tech_specs, mock_device, fake_dummy) plus a small LOCAL TMDB HTTP stub for
the enrich leg. `test_enrich_metadata.py`'s `FakeTMDB`/`patch_tmdb` are
file-local (its own docstring notes a shared `mock_tmdb` conftest fixture is a
future promotion — Step 5.5), so this file defines its own minimal stub scoped
to exactly what `cmd_prep_push_rep_enrich`'s by-known-id enrich path needs: a
bare `/movie/{id}` detail lookup, `/configuration`, and image bytes. Content of
the extended NFO element set is exhaustively covered in
tests/test_enrich_metadata.py; this file only proves the composition.

HERMETIC: monkeypatches `main.requests.get`; NEVER a real network call. NEVER
touches C:\\Media / real library_*.json (the `sandbox` fixture's hard-guard).
"""
import os

import pytest

import main
import mvcommon


FAKE_JPG = b"\xff\xd8\xff\xe0FAKE-JPEG-BYTES\xff\xd9"


class _Resp:
    """Minimal requests.Response stand-in: .status_code, .json(), .content."""
    def __init__(self, status_code=200, json_data=None, content=b""):
        self.status_code = status_code
        self._json = json_data
        self.content = content

    def json(self):
        if self._json is None:
            raise ValueError("no json")
        return self._json


def _make_fake_tmdb_get(movie_detail):
    """Minimal TMDB GET stub for the by-known-id enrich path (a `-tmdbid` was
    preset via cmd_set_tmdb, so `cmd_enrich_metadata` resolves BY ID — no title
    search). Serves /configuration, a bare /movie/{id} detail lookup for
    `movie_detail["id"]`, and any image URL. Anything else (credits/
    external_ids/…) degrades to an empty 200 payload — the NFO extended-
    element fetches all tolerate that gracefully (see test_enrich_metadata.py
    for full NFO-content coverage). Records every call for assertions."""
    calls = []

    def fake_get(url, params=None, headers=None, timeout=None, **kwargs):
        calls.append((url, dict(params or {})))
        if url.startswith("https://image.tmdb.org/t/p/"):
            return _Resp(200, content=FAKE_JPG)
        if url.endswith("/configuration"):
            return _Resp(200, json_data={"images": {"secure_base_url": "https://image.tmdb.org/t/p/"}})
        parts = url.rstrip("/").split("/")
        if len(parts) >= 2 and parts[-1].isdigit() and parts[-2] == "movie":
            tid = int(parts[-1])
            if tid == movie_detail["id"]:
                return _Resp(200, json_data=movie_detail)
            return _Resp(404, json_data=None)
        return _Resp(200, json_data={})

    fake_get.calls = calls
    return fake_get


@pytest.fixture()
def patch_tmdb_enrich(monkeypatch, tmp_path):
    """Redirect the TMDB cache to a temp dir, install a test API key, disable
    the EXA web-search fallback (mirrors test_enrich_metadata.py's patch_tmdb —
    never a real network/EXA call), and hand back install(fake_get) that
    patches main.requests.get."""
    monkeypatch.setattr(main, "TMDB_CACHE_DIR", str(tmp_path / "tmdb_cache"))
    monkeypatch.setattr(main, "EXA_CACHE_DIR", str(tmp_path / "exa_cache"))
    monkeypatch.setattr(mvcommon, "tmdb_api_key", lambda: "TEST-V3-KEY")
    monkeypatch.setattr(mvcommon, "exa_api_key", lambda: "")

    def install(fake_get):
        monkeypatch.setattr(main.requests, "get", fake_get)
        return fake_get

    return install


def _movie_detail(tmdb_id, title, year, poster_path="/p.jpg", backdrop_path="/b.jpg"):
    return {
        "id": tmdb_id,
        "title": title,
        "release_date": f"{year}-01-01",
        "poster_path": poster_path,
        "backdrop_path": backdrop_path,
        "overview": "A test film.",
        "vote_average": 7.0,
    }


def _seed_source(sandbox, make_video, title_id, filename="Film.mkv"):
    """Write the source .mkv the archive leg will prep -> push -> replace."""
    path = sandbox["media_dir"] / filename
    make_video(path, marker=title_id.encode())
    return path


def _refuse_input(monkeypatch, reason):
    """Guard against an accidental interactive prompt: any input() call fails
    the test loudly instead of hanging it."""
    def _boom(*a, **k):
        raise AssertionError(reason)
    monkeypatch.setattr("builtins.input", _boom)


# ---------------------------------------------------------------------------
# (1) id-supplied happy path.
# ---------------------------------------------------------------------------

def test_happy_path_archives_presets_confirms_renames_and_downloads_poster(
        sandbox, make_video, stub_tech_specs, mock_device, fake_dummy,
        patch_tmdb_enrich, monkeypatch):
    """--yes (rename_choice="yes"): the archive completes, metadata.tmdb_id is
    preset (cmd_set_tmdb) and confirmed by cmd_enrich_metadata, the folder is
    renamed to carry the {tmdb-…} token, and the poster is downloaded — all in
    ONE call, with NO interactive input() (a monkeypatched input() that raises
    proves "yes" never prompts)."""
    title_id = "mov-en-2025-testfilm"
    path = _seed_source(sandbox, make_video, title_id)
    patch_tmdb_enrich(_make_fake_tmdb_get(_movie_detail(555, "Test Film", 2025)))
    _refuse_input(monkeypatch, "input() must never be called when rename_choice='yes'")

    result = main.cmd_prep_push_rep_enrich(title_id, str(path), tmdb_id=555, rename_choice="yes")

    assert result is True
    library = mvcommon.load_library()
    assert title_id in library, "the rename must NOT change the library KEY"
    entry = library[title_id]
    assert entry["status"] == "archived"
    assert entry["metadata"]["tmdb_id"] == 555
    stamped_folder = entry["folder_path"]
    assert "{tmdb-555}" in os.path.basename(stamped_folder)
    assert os.path.exists(os.path.join(stamped_folder, "poster.jpg"))


# ---------------------------------------------------------------------------
# (2) --no-rename leaves the folder name unchanged but still writes the id.
# ---------------------------------------------------------------------------

def test_no_rename_leaves_folder_unchanged_but_still_writes_tmdb_id(
        sandbox, make_video, stub_tech_specs, mock_device, fake_dummy,
        patch_tmdb_enrich, monkeypatch, capsys):
    """rename_choice="no" (CLI --no-rename): metadata.tmdb_id is still written
    (additive, independent of the rename decision) and the poster still
    downloads into the folder, but the folder KEEPS its original name — no
    {tmdb-…} token — and no input() prompt ever fires."""
    title_id = "mov-en-2025-testfilm2"
    path = _seed_source(sandbox, make_video, title_id, filename="Film2.mkv")
    patch_tmdb_enrich(_make_fake_tmdb_get(_movie_detail(777, "Test Film 2", 2025)))
    _refuse_input(monkeypatch, "input() must never be called when rename_choice='no'")
    original_folder = str(sandbox["media_dir"])

    result = main.cmd_prep_push_rep_enrich(title_id, str(path), tmdb_id=777, rename_choice="no")

    assert result is True
    library = mvcommon.load_library()
    entry = library[title_id]
    assert entry["status"] == "archived"
    assert entry["metadata"]["tmdb_id"] == 777
    assert entry["folder_path"] == original_folder, "declined rename must leave folder_path untouched"
    assert "{tmdb-" not in os.path.basename(entry["folder_path"])
    # The poster still downloads into the (unrenamed) folder — the download
    # step is independent of the stamp decision.
    assert os.path.exists(os.path.join(original_folder, "poster.jpg"))
    out = capsys.readouterr().out
    assert "auto-declined (--no-rename)" in out


# ---------------------------------------------------------------------------
# (3) -tvdbid refuses before anything touches disk.
# ---------------------------------------------------------------------------

def test_tvdbid_refuses_before_any_prep_and_leaves_disk_and_library_untouched(
        sandbox, make_video, monkeypatch, capsys):
    """-tvdbid is refused BEFORE cmd_prep_push_rep is even called (a
    monkeypatch that raises if it IS called proves this) — the source file and
    the (never-created) library file are byte-identical to before the call."""
    title_id = "mov-en-2025-tvdbtest"
    path = _seed_source(sandbox, make_video, title_id, filename="TvdbTest.mkv")
    original_bytes = path.read_bytes()

    def _boom(*a, **k):
        raise AssertionError("cmd_prep_push_rep must NOT be called when -tvdbid is refused")
    monkeypatch.setattr(main, "cmd_prep_push_rep", _boom)

    result = main.cmd_prep_push_rep_enrich(title_id, str(path), tvdb_id=82066)

    assert result is False
    out = capsys.readouterr().out
    assert "-tvdbid is not supported" in out
    assert "MediaVault is TMDB-only" in out
    assert "-tmdbid <id>" in out
    # Nothing touched disk: the source file is untouched, and no library file
    # was ever created (cmd_prep never ran).
    assert path.read_bytes() == original_bytes
    assert not sandbox["lib_movies"].exists()


def test_tvdbid_and_tmdbid_together_still_refuses(sandbox, make_video, monkeypatch, capsys):
    """Decision 1 is unconditional: even with a (valid-looking) -tmdbid ALSO
    supplied, a -tvdbid still refuses before anything runs."""
    title_id = "mov-en-2025-tvdbtest2"
    path = _seed_source(sandbox, make_video, title_id, filename="TvdbTest2.mkv")

    def _boom(*a, **k):
        raise AssertionError("cmd_prep_push_rep must NOT be called when -tvdbid is refused")
    monkeypatch.setattr(main, "cmd_prep_push_rep", _boom)

    result = main.cmd_prep_push_rep_enrich(title_id, str(path), tmdb_id=1234, tvdb_id=82066)

    assert result is False
    assert "-tvdbid is not supported" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# (4) a RollbackHardFail from the rename is caught, printed, and the function
#     still returns True (Decision 7 — an enrich-leg failure warns and
#     continues; the return value reflects only the archive's completion).
# ---------------------------------------------------------------------------

def test_rollback_hard_fail_from_rename_is_caught_warned_and_still_returns_true(
        sandbox, make_video, stub_tech_specs, mock_device, fake_dummy,
        patch_tmdb_enrich, monkeypatch, capsys):
    title_id = "mov-en-2025-testfilm3"
    path = _seed_source(sandbox, make_video, title_id, filename="Film3.mkv")
    patch_tmdb_enrich(_make_fake_tmdb_get(_movie_detail(888, "Test Film 3", 2025)))

    def _boom_rename(old_folder, new_name):
        raise main.RollbackHardFail(
            state=f"{title_id}: folder moved to X",
            reason="folder_path rewrite failed past the rename point-of-no-return: simulated",
            resume_cmd='rename_folder "X" "TestMovie3 {tmdb-888}"',
        )
    monkeypatch.setattr(main, "cmd_rename_folder", _boom_rename)

    result = main.cmd_prep_push_rep_enrich(title_id, str(path), tmdb_id=888, rename_choice="yes")

    assert result is True, "the archive already succeeded — a post-PONR rename failure must not flip the return value"
    out = capsys.readouterr().out
    assert "Enrich folder rename left incomplete" in out
    assert "folder moved to X" in out
    assert 'rename_folder "X"' in out
    assert "AUTO-PILOT COMPLETE (archive + enrich)" in out

    # The archive itself is intact regardless of the enrich-leg hard-fail.
    library = mvcommon.load_library()
    assert library[title_id]["status"] == "archived"


# ---------------------------------------------------------------------------
# (bonus) the archive not completing (push/replace paused) skips enrich
# entirely and returns False — distinct from "the archive finished but
# enrich failed" above.
# ---------------------------------------------------------------------------

def test_archive_not_complete_skips_enrich_and_returns_false(sandbox, monkeypatch, capsys):
    """When the archive leg does not reach status='archived', enrich is
    SKIPPED entirely (cmd_set_tmdb/cmd_enrich_metadata are never called — a
    monkeypatch that raises if either IS called proves this) and the function
    returns False."""
    title_id = "mov-en-2025-notarchived"

    def _fake_prep_push_rep(*a, **k):
        # Simulate a paused push: cmd_prep succeeded (entry exists) but the
        # archive never reached 'archived'.
        mvcommon.save_library({title_id: {
            "status": "local_ready", "uploaded": False,
            "folder_path": str(sandbox["media_dir"]), "filename": "Film.mkv",
            "type": "movie",
        }})
        return None
    monkeypatch.setattr(main, "cmd_prep_push_rep", _fake_prep_push_rep)

    def _boom(*a, **k):
        raise AssertionError("enrich must not run when the archive did not complete")
    monkeypatch.setattr(main, "cmd_set_tmdb", _boom)
    monkeypatch.setattr(main, "cmd_enrich_metadata", _boom)

    result = main.cmd_prep_push_rep_enrich(title_id, "unused.mkv", tmdb_id=999)

    assert result is False
    out = capsys.readouterr().out
    assert "not archived yet" in out
    assert "local_ready" in out


def test_prep_did_not_create_entry_skips_enrich_and_returns_false(sandbox, monkeypatch, capsys):
    """When the prep leg registers NOTHING (manual_id never lands in the
    library — e.g. an unsplittable-track refusal), enrich is skipped and the
    function returns False."""
    title_id = "mov-en-2025-noentry"
    monkeypatch.setattr(main, "cmd_prep_push_rep", lambda *a, **k: None)  # registers nothing

    def _boom(*a, **k):
        raise AssertionError("enrich must not run when prep created no entry")
    monkeypatch.setattr(main, "cmd_set_tmdb", _boom)
    monkeypatch.setattr(main, "cmd_enrich_metadata", _boom)

    result = main.cmd_prep_push_rep_enrich(title_id, "unused.mkv", tmdb_id=999)

    assert result is False
    assert "prep did not create a library entry" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# (bonus) write_nfo=True is forwarded to the enrich leg as --nfo.
# ---------------------------------------------------------------------------

def test_write_nfo_flag_is_forwarded_to_the_enrich_leg(
        sandbox, make_video, stub_tech_specs, mock_device, fake_dummy,
        patch_tmdb_enrich, monkeypatch):
    """write_nfo=True on the composed command reaches cmd_enrich_metadata as
    --nfo. NFO CONTENT is exhaustively covered in test_enrich_metadata.py —
    this only proves the flag plumbing."""
    title_id = "mov-en-2025-testfilmnfo"
    path = _seed_source(sandbox, make_video, title_id, filename="FilmNFO.mkv")
    patch_tmdb_enrich(_make_fake_tmdb_get(_movie_detail(444, "Test Film NFO", 2025)))
    _refuse_input(monkeypatch, "input() must never be called when rename_choice='yes'")

    result = main.cmd_prep_push_rep_enrich(title_id, str(path), tmdb_id=444,
                                            rename_choice="yes", write_nfo=True)

    assert result is True
    library = mvcommon.load_library()
    folder = library[title_id]["folder_path"]
    assert os.path.exists(os.path.join(folder, "movie.nfo"))


def test_write_nfo_default_off_writes_no_nfo(
        sandbox, make_video, stub_tech_specs, mock_device, fake_dummy,
        patch_tmdb_enrich, monkeypatch):
    """Decision 4 (LOCKED): write_nfo defaults to False — a plain call writes
    no NFO."""
    title_id = "mov-en-2025-testfilmnonfo"
    path = _seed_source(sandbox, make_video, title_id, filename="FilmNoNFO.mkv")
    patch_tmdb_enrich(_make_fake_tmdb_get(_movie_detail(333, "Test Film No NFO", 2025)))
    _refuse_input(monkeypatch, "input() must never be called when rename_choice='yes'")

    result = main.cmd_prep_push_rep_enrich(title_id, str(path), tmdb_id=333, rename_choice="yes")

    assert result is True
    library = mvcommon.load_library()
    folder = library[title_id]["folder_path"]
    assert not os.path.exists(os.path.join(folder, "movie.nfo"))
