"""Focused tests for `cmd_prep_push_rep_enrich` — the movie prep->push->replace
autopilot folded together with enrich (IMP-D22, Candidate A: fully isolated
from `cmd_enrich_metadata` — zero lines of it touched).

Covers the plan's Step 1 Acceptance item (3) scenarios:
  - id-supplied happy path: archive -> preset (`set_tmdb`) -> resolve-by-id
    (NO title search) -> confirm(yes, via rename_choice="yes") -> rename ->
    poster downloaded.
  - id-supplied + rename_choice="no" (--no-rename): folder name unchanged,
    metadata.tmdb_id still written.
  - `-tvdbid` refuses BEFORE anything touches disk (archive is never even
    attempted; folder + library byte-identical to before the call).
  - a `RollbackHardFail` raised from a monkeypatched `cmd_rename_folder` is
    caught and printed, and the function still returns True (Decision 7).

Plus two extra scenarios for the two other warn-and-continue boundaries the
plan calls out: the archive not completing (distinct from "enrich failed"),
and the independently-replicated "no TMDB API key" guard.

HERMETIC: reuses the SAME `sandbox` / `make_video` / `stub_tech_specs` /
`mock_device` / `fake_dummy` fixtures the smoke suite's `test_prep_push_rep`
case uses for a real (mocked-I/O) archive, plus a tiny local by-id-only TMDB
fake (a preset id must NEVER search, so there is no /search endpoint here at
all — a `/search/*` hit would be a test bug, not a feature to support).
"""
import hashlib
import json
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


class _FakeTMDBById:
    """By-id-ONLY TMDB fake for the preset-tmdb_id path (`_resolve_unit_by_id`).

    Serves ONE movie's bare `/movie/{id}` details, `/configuration`, and image
    bytes. Deliberately has NO `/search/*` handling — a preset id must never
    search; if the code under test ever did, the resulting confident match
    would come back malformed (no matching id) and the test would fail loudly
    rather than silently passing on the wrong path.
    """
    def __init__(self, movie_id, details):
        self.movie_id = movie_id
        self.details = details
        self.calls = []
        self.image_urls = []

    def get(self, url, params=None, headers=None, timeout=None, **kwargs):
        params = params or {}
        self.calls.append((url, dict(params)))
        if url.startswith("https://image.tmdb.org/t/p/"):
            self.image_urls.append(url)
            return _Resp(200, content=FAKE_JPG)
        if url.endswith("/configuration"):
            return _Resp(200, json_data={"images": {"secure_base_url": "https://image.tmdb.org/t/p/"}})
        if url.endswith(f"/movie/{self.movie_id}"):
            return _Resp(200, json_data=self.details)
        # credits / external_ids / anything else (e.g. --nfo's extra calls, not
        # exercised by these tests) -> empty, never a 404 (keeps _write_nfo's
        # optional-field omission path exercised gracefully if ever hit).
        return _Resp(200, json_data={})


@pytest.fixture()
def patch_tmdb_by_id(monkeypatch, tmp_path):
    """Redirect the TMDB/EXA caches to temp dirs, install a v3 key, force the
    EXA fallback OFF (this repo's real mvconfig.json may carry a live EXA key
    — see test_enrich_metadata.py's identical rationale), and hand back an
    `install(fake)` that patches `main.requests.get`."""
    monkeypatch.setattr(main, "TMDB_CACHE_DIR", str(tmp_path / "tmdb_cache"))
    monkeypatch.setattr(main, "EXA_CACHE_DIR", str(tmp_path / "exa_cache"))
    monkeypatch.setattr(mvcommon, "tmdb_api_key", lambda: "TEST-V3-KEY")
    monkeypatch.setattr(mvcommon, "exa_api_key", lambda: "")

    def install(fake):
        monkeypatch.setattr(main.requests, "get", fake.get)
        return fake

    return install


MOVIE_ID = "mov-en-2024-enrich"
TMDB_ID = 424242


def _movie_details(tmdb_id, title="Enrich Movie", year=2024):
    """A /3/movie/{id} DETAILS object — the same shape `_resolve_unit_by_id`
    reads (title/release_date/poster_path/backdrop_path/overview/vote_average)."""
    return {
        "id": tmdb_id, "title": title, "release_date": f"{year}-03-15",
        "poster_path": "/poster.jpg", "backdrop_path": "/backdrop.jpg",
        "overview": "A movie about testing enrich autopilots.",
        "vote_average": 7.7,
    }


def _archive(sandbox, make_video, filename="AutoEnrich.mkv"):
    """Seed a fresh (unprepped) movie file under the sandbox's media dir."""
    path, orig_hash = make_video(sandbox["media_dir"] / filename)
    return path, orig_hash


# ---------------------------------------------------------------------------
# (1) id-supplied happy path: archive -> preset -> resolve-by-id -> confirm
#     (yes, via rename_choice="yes") -> rename -> poster downloaded.
# ---------------------------------------------------------------------------

def test_id_supplied_happy_path_archives_presets_resolves_renames_downloads(
        sandbox, make_video, stub_tech_specs, mock_device, fake_dummy,
        patch_tmdb_by_id, capsys):
    path, orig_hash = _archive(sandbox, make_video)
    fake = patch_tmdb_by_id(_FakeTMDBById(TMDB_ID, _movie_details(TMDB_ID)))

    result = main.cmd_prep_push_rep_enrich(
        MOVIE_ID, str(path), tmdb_id=TMDB_ID, rename_choice="yes")

    assert result is True

    out = capsys.readouterr().out
    assert "using preset tmdb_id=424242 (manual)" in out
    assert "AUTO-PILOT COMPLETE (archive + enrich)" in out
    assert "will be changed to" in out  # _make_rename_confirm's required phrase
    assert "auto-confirmed (--yes)" in out

    # No /search call was ever made — resolved strictly by id.
    assert not any("/search/" in u for u, _ in fake.calls), \
        "a preset tmdb_id must resolve by id, never search"

    lib = mvcommon.load_library()
    entry = lib[MOVIE_ID]
    assert entry["status"] == "archived", "the archive leg must still complete"
    assert entry["metadata"]["tmdb_id"] == TMDB_ID
    assert entry["metadata"]["title"] == "Enrich Movie"
    assert entry["metadata"]["year"] == 2024

    old_folder = sandbox["media_dir"]
    # The renamed folder keeps its ORIGINAL basename (the media folder's own
    # leaf name, not the video filename) with the {tmdb-…} token appended.
    stamped = old_folder.parent / f"{old_folder.name} {{tmdb-{TMDB_ID}}}"
    assert stamped.is_dir() and not old_folder.exists(), \
        f"expected the folder renamed to {stamped}"
    assert entry["folder_path"] == str(stamped)

    assert (stamped / "poster.jpg").read_bytes() == FAKE_JPG
    assert (stamped / "fanart.jpg").read_bytes() == FAKE_JPG
    assert any(u.endswith("/w342/poster.jpg") for u in fake.image_urls)
    assert any(u.endswith("/w780/backdrop.jpg") for u in fake.image_urls)


# ---------------------------------------------------------------------------
# (2) id-supplied + rename_choice="no" (--no-rename): folder name UNCHANGED,
#     metadata.tmdb_id still written.
# ---------------------------------------------------------------------------

def test_no_rename_leaves_folder_unchanged_but_still_writes_tmdb_id(
        sandbox, make_video, stub_tech_specs, mock_device, fake_dummy,
        patch_tmdb_by_id, capsys):
    path, _ = _archive(sandbox, make_video)
    patch_tmdb_by_id(_FakeTMDBById(TMDB_ID, _movie_details(TMDB_ID)))
    original_folder = sandbox["media_dir"]

    result = main.cmd_prep_push_rep_enrich(
        MOVIE_ID, str(path), tmdb_id=TMDB_ID, rename_choice="no")

    assert result is True
    out = capsys.readouterr().out
    assert "auto-declined (--no-rename)" in out
    assert "rename declined" in out

    lib = mvcommon.load_library()
    entry = lib[MOVIE_ID]
    assert entry["status"] == "archived"
    assert entry["metadata"]["tmdb_id"] == TMDB_ID, \
        "tmdb_id must be written regardless of the rename decision"
    assert entry["folder_path"] == str(original_folder), "folder must NOT be renamed"
    assert original_folder.is_dir()
    assert not (original_folder.parent / f"{original_folder.name} {{tmdb-{TMDB_ID}}}").exists()

    # Images still download into the (unrenamed) folder — enrich beyond the
    # rename decision is unaffected.
    assert (original_folder / "poster.jpg").read_bytes() == FAKE_JPG


# ---------------------------------------------------------------------------
# (3) `-tvdbid` refuses BEFORE anything touches disk.
# ---------------------------------------------------------------------------

def test_tvdbid_refuses_before_anything_touches_disk(
        sandbox, make_video, stub_tech_specs, monkeypatch, capsys):
    path, _ = _archive(sandbox, make_video)

    def _must_not_be_called(*a, **kw):
        raise AssertionError("cmd_prep_push_rep must NOT be called when -tvdbid is supplied")

    monkeypatch.setattr(main, "cmd_prep_push_rep", _must_not_be_called)

    # Snapshot state before the call: the library file does not exist yet (no
    # entry created), and the media folder holds exactly the one seeded file.
    assert not sandbox["lib_movies"].exists()
    before_listing = sorted(os.listdir(sandbox["media_dir"]))

    result = main.cmd_prep_push_rep_enrich(MOVIE_ID, str(path), tvdb_id=82066)

    assert result is False
    out = capsys.readouterr().out
    assert "MediaVault is TMDB-only for movies, series, and anime" in out
    assert "-tmdbid <id>" in out

    # Byte-identical to before: no library file created, folder listing unchanged.
    assert not sandbox["lib_movies"].exists()
    assert sorted(os.listdir(sandbox["media_dir"])) == before_listing


# ---------------------------------------------------------------------------
# (4) a RollbackHardFail from a monkeypatched cmd_rename_folder is caught and
#     printed; the function still returns True (Decision 7).
# ---------------------------------------------------------------------------

def test_rollback_hard_fail_from_rename_is_caught_prints_warning_returns_true(
        sandbox, make_video, stub_tech_specs, mock_device, fake_dummy,
        patch_tmdb_by_id, monkeypatch, capsys):
    path, _ = _archive(sandbox, make_video)
    patch_tmdb_by_id(_FakeTMDBById(TMDB_ID, _movie_details(TMDB_ID)))

    def _boom(old_folder, new_name):
        raise main.RollbackHardFail(
            state=f"{old_folder}: folder moved to <new>",
            reason="simulated post-PONR failure",
            resume_cmd=f'rename_folder "<new>" "{new_name}"',
        )

    monkeypatch.setattr(main, "cmd_rename_folder", _boom)

    result = main.cmd_prep_push_rep_enrich(
        MOVIE_ID, str(path), tmdb_id=TMDB_ID, rename_choice="yes")

    assert result is True, "the archive succeeded — a rename hard-fail must NOT flip this to False"
    out = capsys.readouterr().out
    assert "Enrich folder rename left incomplete" in out
    assert "simulated post-PONR failure" in out
    assert "To finish it:" in out
    assert "AUTO-PILOT COMPLETE (archive + enrich)" in out

    # tmdb_id was written BEFORE the (failed) rename attempt.
    lib = mvcommon.load_library()
    assert lib[MOVIE_ID]["metadata"]["tmdb_id"] == TMDB_ID
    assert lib[MOVIE_ID]["status"] == "archived"


# ---------------------------------------------------------------------------
# Extra: the archive not completing is a DIFFERENT warn-and-return boundary
# than "enrich failed" — enrich must never even be attempted.
# ---------------------------------------------------------------------------

def test_archive_not_completed_skips_enrich_entirely_and_returns_false(
        sandbox, make_video, stub_tech_specs, mock_device, fake_dummy,
        monkeypatch, capsys):
    path, _ = _archive(sandbox, make_video)

    # Force the push leg to fail so cmd_prep_push_rep bails BEFORE replace —
    # the library entry stays "local_ready", never reaching "archived".
    monkeypatch.setattr(main, "cmd_push", lambda *a, **kw: False)

    def _tmdb_key_must_not_be_read():
        raise AssertionError("enrich must never even check for a TMDB key when the archive didn't complete")

    monkeypatch.setattr(mvcommon, "tmdb_api_key", _tmdb_key_must_not_be_read)

    result = main.cmd_prep_push_rep_enrich(
        MOVIE_ID, str(path), tmdb_id=TMDB_ID, rename_choice="yes")

    assert result is False
    out = capsys.readouterr().out
    assert "not archived yet" in out
    assert "status='local_ready'" in out
    assert f"enrich_metadata {MOVIE_ID} --apply" in out
    assert "AUTO-PILOT COMPLETE" not in out

    lib = mvcommon.load_library()
    assert lib[MOVIE_ID]["status"] == "local_ready"
    assert "tmdb_id" not in lib[MOVIE_ID].get("metadata", {})


# ---------------------------------------------------------------------------
# Extra: "no TMDB API key" is independently replicated (mirrors
# cmd_enrich_metadata's own guard) — the archive still completes, enrich just
# prints and skips, and the command still returns True.
# ---------------------------------------------------------------------------

def test_no_tmdb_api_key_warns_and_skips_enrich_but_archive_still_completes(
        sandbox, make_video, stub_tech_specs, mock_device, fake_dummy,
        monkeypatch, capsys):
    path, _ = _archive(sandbox, make_video)
    monkeypatch.setattr(mvcommon, "tmdb_api_key", lambda: "")

    def _must_not_be_called(*a, **kw):
        raise AssertionError("cmd_set_tmdb must not run useful work without inspecting the key first "
                              "is fine, but resolving against TMDB must never be attempted")

    monkeypatch.setattr(main, "_resolve_unit_by_id", _must_not_be_called)
    monkeypatch.setattr(main, "_resolve_unit", _must_not_be_called)

    result = main.cmd_prep_push_rep_enrich(
        MOVIE_ID, str(path), tmdb_id=TMDB_ID, rename_choice="yes")

    assert result is True
    out = capsys.readouterr().out
    assert "No TMDB API key configured" in out
    assert "AUTO-PILOT COMPLETE (archive + enrich)" in out

    lib = mvcommon.load_library()
    entry = lib[MOVIE_ID]
    assert entry["status"] == "archived"
    # cmd_set_tmdb still ran (it needs no API key), so tmdb_id IS written —
    # but nothing further (title/year/overview/rename/images) since the
    # resolve step never ran.
    assert entry["metadata"]["tmdb_id"] == TMDB_ID
