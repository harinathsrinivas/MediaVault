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

-----------------------------------------------------------------------------
IMP-D22 Step 4 EXTENSION — the movie command's full test matrix. Two groups
are appended below the seven scenarios above (which are left byte-for-byte
untouched and un-renumbered):

  GROUP A — EXISTING-BEHAVIOUR REGRESSION MATRIX for `cmd_prep_push_rep`.
    Fourteen tests that call the PRE-EXISTING `main.cmd_prep_push_rep` DIRECTLY
    (never the new sibling) across every flag the CLI exposes — no-split,
    SIZE_MB below/above the limit, SIZE_GB, COUNT, `device`, `rehash`,
    `tempdir`, `--extras`, `--extras-size`, plus two multi-flag combinations —
    and assert the SAME post-state oracle `test_baseline_happy_path.py` and the
    smoke suite already use (status `archived`, uploaded bytes on the fake
    device, dummy on disk, no `.dummy_tmp` / `.tobedeleted` / `_parts` /
    journal leftovers). `cmd_prep_push_rep` was NEVER touched by IMP-D22, and
    that is precisely the point: Group A is a REGRESSION PIN, not new coverage.
    If any of it goes red, something in this task leaked into the wrong
    function. The last test in the group
    (`…console_contract_is_byte_for_byte_unchanged`) is the explicit
    byte-for-byte pin, in the spirit of
    `test_extras.py::test_season_resume_command_unchanged_without_extras`.

  GROUP B — the remaining NEW-COMMAND scenarios (auto-resolve fallback, the
    non-TTY confirmation default, `--nfo` on/off + the `<tvdbid>`-never-emitted
    guarantee of Decision 4, `--no-web` vs the EXA fallback, and the
    extras/enrich interplay), joining the four gate/refusal/warn scenarios
    already covered above.
"""
import builtins
import hashlib
import json
import os
import types
import xml.etree.ElementTree as ET

import pytest

import main
import mvcommon
from conftest import FAKE_DUMMY_BYTES


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
    stamped = old_folder.parent / f"{old_folder.name} [tmdbid-{TMDB_ID}]"
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
    assert not (original_folder.parent / f"{original_folder.name} [tmdbid-{TMDB_ID}]").exists()

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


# ---------------------------------------------------------------------------
# Extra: a resolver that RAISES is swallowed by `_enrich_after_archive`'s
# defensive try/except — the same belt-and-braces wrapper `cmd_enrich_metadata`
# puts around this identical waterfall (main.py:~2487-2507). The resolvers all
# document "NEVER raises", so this guards against a future regression in them;
# `cmd_prep_push_rep_enrich` catches ONLY `RollbackHardFail`, so without the
# wrapper a plain `RuntimeError` would escape the ENTIRE command *after* the
# archive leg had already succeeded — exactly what Decision 7 forbids.
# ---------------------------------------------------------------------------

def test_resolver_exception_is_caught_warns_and_does_not_propagate(
        sandbox, make_video, stub_tech_specs, mock_device, fake_dummy,
        patch_tmdb_by_id, monkeypatch, capsys):
    path, _ = _archive(sandbox, make_video)
    # requests.get is patched too, so a stray call can never reach the network.
    patch_tmdb_by_id(_FakeTMDBById(TMDB_ID, _movie_details(TMDB_ID)))

    def _boom(unit, api_key):
        raise RuntimeError("simulated resolver blow-up")

    # No tmdb_id preset is passed, so the waterfall takes the search branch and
    # `_resolve_unit` is the resolver that runs (and raises).
    monkeypatch.setattr(main, "_resolve_unit", _boom)

    result = main.cmd_prep_push_rep_enrich(MOVIE_ID, str(path), rename_choice="yes")

    assert result is True, "the archive completed — a resolver crash must not flip this to False"
    out = capsys.readouterr().out
    assert "TMDB error — enrich skipped" in out
    assert "simulated resolver blow-up" in out, "the exception detail is surfaced to the user"
    assert "AUTO-PILOT COMPLETE (archive + enrich)" in out

    # The archive stands; nothing enrich-side was written or renamed.
    lib = mvcommon.load_library()
    entry = lib[MOVIE_ID]
    assert entry["status"] == "archived"
    assert "tmdb_id" not in entry.get("metadata", {})
    folder = sandbox["media_dir"]
    assert entry["folder_path"] == str(folder), "a failed resolve must never rename the folder"
    assert folder.is_dir()
    assert not (folder / "poster.jpg").exists()


# ===========================================================================
#   GROUP A — EXISTING-BEHAVIOUR REGRESSION MATRIX for `cmd_prep_push_rep`
#
# Every test in this group calls the PRE-EXISTING `main.cmd_prep_push_rep`
# directly. IMP-D22 added a SIBLING command and touched zero lines of this one,
# so all of Group A must pass unchanged — that is the whole point. The oracle is
# the same one `tests/test_baseline_happy_path.py` and
# `tests/smoke/test_smoke_all_commands.py::test_prep_push_rep` already use:
# library entry post-state + bytes on the fake device + dummy on disk + no
# working-file leftovers.
#
# The helpers below are per-file plain functions (NOT fixtures) mirroring
# `tests/test_extras.py`'s own `_install_fake_split` / `_install_concat_merge` /
# `plenty_of_disk` convention. This step deliberately adds NO conftest fixture
# (docs/testing-strategy.md binding-hazard rule).
# ===========================================================================

# Golden key sets of an ARCHIVED movie entry, captured against the UNMODIFIED
# `cmd_prep_push_rep`. An IMP-D22 leak that added/renamed/dropped a field on the
# OLD command's path would break these immediately.
_ARCHIVED_KEYS_WHOLE_FILE = sorted([
    "filename", "folder_path", "hash", "metadata", "search_term",
    "short_id", "status", "tech_spec", "uploaded",
])
# A NEW split additionally writes `split_info` and pins `re_hashed` (False when
# deferred, True once an eager canonical hash is promoted at replace).
_ARCHIVED_KEYS_SPLIT = sorted(_ARCHIVED_KEYS_WHOLE_FILE + ["re_hashed", "split_info"])


def _device_files(device_dir):
    """{filename: Path} index of the fake device.

    ALWAYS index by `.name`: MediaVault filenames carry a `[short_id]` tag and
    `rglob("… [id].mkv")` would read the brackets as a glob character class and
    silently match nothing (docs/testing-strategy.md §8.1 / §9).
    """
    return {p.name: p for p in device_dir.rglob("*") if p.is_file()}


def _whole_file_remote_name(entry):
    """The UID-tagged name a WHOLE-file push gives the master on the device."""
    base, ext = os.path.splitext(entry["filename"])
    return f"{base} [{entry['short_id']}]{ext}"


def _mvmeta_remote_name(entry):
    """The best-effort remote disaster-recovery sidecar written next to it."""
    base, _ext = os.path.splitext(entry["filename"])
    return f"{base} [{entry['short_id']}].mvmeta.json"


def _plenty_of_disk(monkeypatch):
    """Neutralize the split free-space pre-flight at its I/O boundary.

    `_free_space_ok` -> `_disk_shortfall` -> `shutil.disk_usage(dir).free`, and
    `_disk_buffer` imposes a 2 GB floor, so splitting even a 264 KB fixture
    "requires" ~2 GB. Patching `disk_usage` (the boundary, not the decision
    helper) keeps the split path reachable on any host without changing the
    logic under test. Same technique as tests/test_extras.py's `plenty_of_disk`.
    """
    huge = 500 * 1024 ** 3
    monkeypatch.setattr(
        main.shutil, "disk_usage",
        lambda path: types.SimpleNamespace(total=huge, used=0, free=huge),
    )


def _install_fake_split(monkeypatch, n_chunks=2):
    """Replace `main.split_video_file` with a deterministic byte-slicer.

    Reproduces the real function's contract — chunk names
    `"<base> [<file_id>].chunk.NNN.mkv"` inside `output_dir`, returned in order
    — but slices the input's REAL bytes instead of invoking mkvmerge, so the
    chunks concatenate back to the master exactly. A genuine mkvmerge split is
    impossible at fixture scale (the real splitter adds a +10 MB per-chunk
    buffer, and `mock_device` has already replaced `subprocess.run`), which is
    why tests/test_extras.py carries the identical helper.

    Returns the recorded call list so a test can assert WHICH method/val/file_id
    each split actually received (the `--extras-size` independence proof).
    """
    calls = []

    def fake_split(input_path, output_dir, method, value_str, file_id=""):
        calls.append({"input": input_path, "output_dir": output_dir,
                      "method": method, "val": value_str, "file_id": file_id})
        os.makedirs(output_dir, exist_ok=True)
        with open(input_path, "rb") as fh:
            data = fh.read()
        step = len(data) // n_chunks + 1  # +1 => no empty trailing slice
        base = os.path.splitext(os.path.basename(input_path))[0]
        tag = f" [{file_id}]" if file_id else ""
        paths = []
        for i in range(n_chunks):
            p = os.path.join(output_dir, f"{base}{tag}.chunk.{i + 1:03d}.mkv")
            with open(p, "wb") as fh:
                fh.write(data[i * step:(i + 1) * step])
            paths.append(p)
        return paths

    monkeypatch.setattr(main, "split_video_file", fake_split)
    return calls


def _install_concat_merge(monkeypatch):
    """Replace `main.merge_video_files` (mkvmerge) with an in-order byte
    concatenation — the exact inverse of `_install_fake_split`'s slicer — so the
    eager canonical re-hash reproduces the master's own sha256 and the real
    promote-at-replace path is exercised without mkvmerge. Returns the recorded
    call list (chunks / out / seed)."""
    calls = []

    def fake_merge(chunk_paths, output_path, seed=None):
        calls.append({"chunks": list(chunk_paths), "out": output_path, "seed": seed})
        with open(output_path, "wb") as out:
            for p in chunk_paths:
                with open(p, "rb") as fh:
                    out.write(fh.read())
        return True

    monkeypatch.setattr(main, "merge_video_files", fake_merge)
    return calls


def _record_subprocess(monkeypatch):
    """Record every argv handed to `main.subprocess.run`, delegating to whatever
    implementation is currently installed.

    MUST be called from the test BODY, never at fixture time: it wraps the fake
    `mock_device` has already installed, so the recorded calls are the real
    command stream (adb + the mkvmerge probes) with the fake still doing the
    work. Returns the (live) list of argv lists.
    """
    recorded = []
    inner = main.subprocess.run

    def _wrapper(cmd, *args, **kwargs):
        recorded.append(list(cmd))
        return inner(cmd, *args, **kwargs)

    monkeypatch.setattr(main.subprocess, "run", _wrapper)
    return recorded


def _assert_no_working_leftovers(folder):
    """The autopilot must leave NO working files behind: no `.dummy_tmp` temp,
    no `.tobedeleted` master leftover, no `_parts/` chunk dir, and no rollback
    journal (a clean run always commits, which deletes it)."""
    names = [p.name for p in folder.rglob("*")]
    assert not [n for n in names if ".dummy_tmp" in n], f"dummy temp left behind: {names}"
    assert not [n for n in names if n.endswith(".tobedeleted")], f"master leftover: {names}"
    assert not (folder / main.SPLIT_DIR_NAME).exists(), "the _parts chunk dir must be cleaned up"
    assert main.TXN_JOURNAL_NAME not in names, "a clean run must commit (and delete) its journal"


def _assert_whole_file_archive_oracle(sandbox, mock_device, title_id, filename,
                                      orig_hash, extra_keys=()):
    """The shared post-state oracle for a WHOLE-FILE `cmd_prep_push_rep` run.

    Identical in shape to test_baseline_happy_path.py's cmd_push/cmd_replace
    oracles and the smoke suite's `test_prep_push_rep`: archived + uploaded,
    the REAL pre-dummy bytes on the device under the UID-tagged name (and
    nothing else), a tiny dummy in the master's place, the hash untouched, and
    no working-file leftovers. Returns the entry for further assertions.
    """
    entry = mvcommon.load_library()[title_id]
    assert sorted(entry) == sorted(_ARCHIVED_KEYS_WHOLE_FILE + list(extra_keys))
    assert entry["status"] == "archived"
    assert entry["uploaded"] is True
    assert entry["filename"] == filename
    assert entry["hash"] == orig_hash, "archiving must never rewrite the master's hash"
    assert entry["folder_path"] == str(sandbox["media_dir"])
    assert "split_info" not in entry, "a whole-file push must not write split_info"

    on_device = _device_files(mock_device)
    assert set(on_device) == {_whole_file_remote_name(entry), _mvmeta_remote_name(entry)}
    assert hashlib.sha256(on_device[_whole_file_remote_name(entry)].read_bytes()).hexdigest() \
        == orig_hash, "the REAL pre-dummy bytes must be what reached the device"

    master = sandbox["media_dir"] / filename
    assert master.read_bytes() == FAKE_DUMMY_BYTES, "the master must be reclaimed to a dummy"
    _assert_no_working_leftovers(sandbox["media_dir"])
    return entry


def _assert_split_archive_oracle(sandbox, mock_device, title_id, filename,
                                 orig_hash, method, val, n_chunks, extra_keys=()):
    """The same oracle for a run that ACTUALLY split: every chunk on the device
    under its own name, split_info recording the method/val/per-chunk hashes,
    the chunks concatenating back to the master, and the local `checksums/`
    sidecars written. Returns the entry."""
    entry = mvcommon.load_library()[title_id]
    assert sorted(entry) == sorted(_ARCHIVED_KEYS_SPLIT + list(extra_keys))
    assert entry["status"] == "archived"
    assert entry["uploaded"] is True

    info = entry["split_info"]
    assert info["is_split"] is True
    assert (info["method"], info["val"]) == (method, val), \
        "the split method/value must reach split_info verbatim"
    assert info["total_chunks"] == n_chunks
    chunk_names = [c["filename"] for c in info["chunks"]]
    assert chunk_names == [f"{os.path.splitext(filename)[0]} [{entry['short_id']}]"
                           f".chunk.{i:03d}.mkv" for i in range(1, n_chunks + 1)]

    on_device = _device_files(mock_device)
    assert set(on_device) == set(chunk_names) | {_mvmeta_remote_name(entry)}
    for chunk in info["chunks"]:
        assert hashlib.sha256(on_device[chunk["filename"]].read_bytes()).hexdigest() \
            == chunk["hash"], f"{chunk['filename']} arrived with the wrong bytes"
    rebuilt = b"".join(on_device[name].read_bytes() for name in chunk_names)
    assert hashlib.sha256(rebuilt).hexdigest() == orig_hash, \
        "the uploaded chunks must reconstruct the master byte-for-byte"

    checksums = sandbox["media_dir"] / mvcommon.CHECKSUM_DIR_NAME
    assert sorted(p.name for p in checksums.iterdir()) == \
        sorted(f"{n}.sha256" for n in chunk_names), \
        "the per-chunk parity sidecars stay in the MEDIA folder"

    master = sandbox["media_dir"] / filename
    assert master.read_bytes() == FAKE_DUMMY_BYTES
    _assert_no_working_leftovers(sandbox["media_dir"])
    return entry


def _seed_extras(media_dir, make_video, group="Specials",
                 files=(("BTS.mkv", b"A-BTS\n"), ("Teaser.mkv", b"A-TEASER\n"))):
    """Seed a bonus-content folder next to the master. Returns
    (folder, {filename: original bytes}, {filename: sha256})."""
    folder = media_dir / group
    folder.mkdir()
    originals, hashes = {}, {}
    for name, marker in files:
        _p, hashes[name] = make_video(folder / name, marker=marker)
        originals[name] = (folder / name).read_bytes()
    return folder, originals, hashes


# --- A1: no split flags at all — the plainest archive ----------------------

def test_regression_no_split_whole_file_push_is_unchanged(
        sandbox, make_video, stub_tech_specs, mock_device, fake_dummy, capsys):
    """Group A / no-split. The baseline every other permutation is measured
    against: prep -> whole-file push -> replace, entry archived, real bytes on
    the device, dummy on disk, no split_info, no leftovers."""
    title_id = "mov-en-2024-plain"
    path, orig_hash = make_video(sandbox["media_dir"] / "Plain.mkv")

    main.cmd_prep_push_rep(title_id, str(path))

    entry = _assert_whole_file_archive_oracle(
        sandbox, mock_device, title_id, "Plain.mkv", orig_hash)
    assert entry["search_term"] == _whole_file_remote_name(entry)
    assert entry["tech_spec"]["resolution"] == "1080p"
    assert "AUTO-PILOT COMPLETE: Movie is safely archived." in capsys.readouterr().out


# --- A2/A3: SIZE_MB / SIZE_GB below the limit -> documented skip-the-split --

def test_regression_size_mb_below_the_limit_skips_the_split(
        sandbox, make_video, stub_tech_specs, mock_device, fake_dummy, capsys):
    """Group A / `SIZE_MB 8`. A file SMALLER than the target is pushed whole —
    cmd_push's `should_split` guard — and the end state is byte-identical to the
    no-split run. Pins the guard's own message too."""
    title_id = "mov-en-2024-mbsmall"
    path, orig_hash = make_video(sandbox["media_dir"] / "MbSmall.mkv")

    main.cmd_prep_push_rep(title_id, str(path), "SIZE_MB", "8")

    out = capsys.readouterr().out
    assert "is smaller than split limit" in out and "Skipping split." in out
    _assert_whole_file_archive_oracle(
        sandbox, mock_device, title_id, "MbSmall.mkv", orig_hash)


def test_regression_size_gb_below_the_limit_skips_the_split(
        sandbox, make_video, stub_tech_specs, mock_device, fake_dummy, capsys):
    """Group A / `SIZE_GB 8` — the same guard on the GB branch (the value is
    multiplied by 1024 before the comparison), same end state."""
    title_id = "mov-en-2024-gbsmall"
    path, orig_hash = make_video(sandbox["media_dir"] / "GbSmall.mkv")

    main.cmd_prep_push_rep(title_id, str(path), "SIZE_GB", "8")

    out = capsys.readouterr().out
    assert "Skipping split." in out
    _assert_whole_file_archive_oracle(
        sandbox, mock_device, title_id, "GbSmall.mkv", orig_hash)


# --- A4/A5: the two branches that ACTUALLY split ---------------------------

def test_regression_size_mb_above_the_limit_really_splits(
        sandbox, make_video, stub_tech_specs, mock_device, fake_dummy,
        monkeypatch):
    """Group A / `SIZE_MB 0.1` on a ~264 KB fixture — over the target, so the
    real split path runs end to end: chunks produced, hashed, sidecar'd,
    uploaded, deleted locally, and recorded in split_info."""
    title_id = "mov-en-2024-mbsplit"
    path, orig_hash = make_video(sandbox["media_dir"] / "MbSplit.mkv")
    _plenty_of_disk(monkeypatch)
    split_calls = _install_fake_split(monkeypatch, n_chunks=2)

    main.cmd_prep_push_rep(title_id, str(path), "SIZE_MB", "0.1")

    entry = _assert_split_archive_oracle(
        sandbox, mock_device, title_id, "MbSplit.mkv", orig_hash,
        "SIZE_MB", "0.1", 2)
    assert entry["re_hashed"] is False, "a deferred split must end unblessed"
    assert len(split_calls) == 1
    assert (split_calls[0]["method"], split_calls[0]["val"]) == ("SIZE_MB", "0.1")
    assert split_calls[0]["file_id"] == entry["short_id"]
    assert split_calls[0]["output_dir"] == str(
        sandbox["media_dir"] / main.SPLIT_DIR_NAME), \
        "without tempdir the chunks stage next to the master"


def test_regression_count_split_uploads_every_chunk(
        sandbox, make_video, stub_tech_specs, mock_device, fake_dummy,
        monkeypatch):
    """Group A / `COUNT 3`. COUNT splits regardless of file size, so all three
    chunks must reach the device and reconstruct the master."""
    title_id = "mov-en-2024-count"
    path, orig_hash = make_video(sandbox["media_dir"] / "Count.mkv")
    _plenty_of_disk(monkeypatch)
    split_calls = _install_fake_split(monkeypatch, n_chunks=3)

    main.cmd_prep_push_rep(title_id, str(path), "COUNT", "3")

    _assert_split_archive_oracle(
        sandbox, mock_device, title_id, "Count.mkv", orig_hash, "COUNT", "3", 3)
    assert (split_calls[0]["method"], split_calls[0]["val"]) == ("COUNT", "3")


# --- A6: device <id_or_name> ------------------------------------------------

def test_regression_device_id_reaches_every_adb_invocation(
        sandbox, make_video, stub_tech_specs, mock_device, fake_dummy,
        monkeypatch):
    """Group A / `device <id>`. Every adb argv the run issues must carry
    `-s <device>`; the SAME run without the flag must carry none. Running both
    in one test makes the assertion causal rather than incidental."""
    _plenty_of_disk(monkeypatch)
    recorded = _record_subprocess(monkeypatch)

    targeted, targeted_hash = make_video(sandbox["media_dir"] / "Targeted.mkv",
                                         marker=b"TARGETED\n")
    main.cmd_prep_push_rep("mov-en-2024-targeted", str(targeted),
                           device_id="fakeserial")

    adb_calls = [c for c in recorded if c and c[0] == "adb"]
    assert adb_calls, "the push leg must issue adb calls"
    assert all(c[1:3] == ["-s", "fakeserial"] for c in adb_calls), \
        f"an adb call lost the device selector: {adb_calls}"

    recorded.clear()
    plain, plain_hash = make_video(sandbox["media_dir"] / "Plain.mkv", marker=b"PLAIN\n")
    main.cmd_prep_push_rep("mov-en-2024-plain", str(plain))

    plain_adb = [c for c in recorded if c and c[0] == "adb"]
    assert plain_adb
    assert not any("-s" in c for c in plain_adb), \
        "without `device` the adb argv must be unchanged from the default"

    library = mvcommon.load_library()
    assert library["mov-en-2024-targeted"]["status"] == "archived"
    assert library["mov-en-2024-plain"]["status"] == "archived"
    on_device = _device_files(mock_device)
    for tid, digest in (("mov-en-2024-targeted", targeted_hash),
                        ("mov-en-2024-plain", plain_hash)):
        name = _whole_file_remote_name(library[tid])
        assert hashlib.sha256(on_device[name].read_bytes()).hexdigest() == digest


# --- A7/A8: rehash ----------------------------------------------------------

def test_regression_rehash_promotes_the_canonical_hash_at_replace(
        sandbox, make_video, stub_tech_specs, mock_device, fake_dummy,
        monkeypatch, capsys):
    """Group A / `rehash` + a real split. The eager bless-at-push merges the
    just-created chunks, stages `split_info.canonical_hash`, and cmd_replace
    promotes it to the entry's truth (`re_hashed=True`, `rehashed_at` stamped,
    the transient field dropped)."""
    title_id = "mov-en-2024-rehash"
    path, orig_hash = make_video(sandbox["media_dir"] / "Rehash.mkv")
    _plenty_of_disk(monkeypatch)
    _install_fake_split(monkeypatch, n_chunks=2)
    merges = _install_concat_merge(monkeypatch)

    main.cmd_prep_push_rep(title_id, str(path), "COUNT", "2", eager_rehash=True)

    entry = _assert_split_archive_oracle(
        sandbox, mock_device, title_id, "Rehash.mkv", orig_hash, "COUNT", "2", 2)
    assert entry["re_hashed"] is True
    info = entry["split_info"]
    assert "canonical_hash" not in info, "the transient staging field is dropped on promotion"
    assert info["rehashed_at"], "promotion stamps rehashed_at"
    assert info["merge_seed"] == entry["short_id"]
    assert info["merge_tool"].startswith("mkvmerge")
    # The concat merge is the exact inverse of the slicer, so the canonical hash
    # equals the master's own — promotion must not change the recorded truth.
    assert entry["hash"] == orig_hash
    assert len(merges) == 1 and merges[0]["seed"] == entry["short_id"]

    out = capsys.readouterr().out
    assert "Eager canonical re-hash" in out
    assert "Promoted eager canonical hash to entry truth (re_hashed=True)." in out


def test_regression_rehash_without_a_split_is_a_no_op(
        sandbox, make_video, stub_tech_specs, mock_device, fake_dummy,
        monkeypatch):
    """Group A / `rehash` with NO split. The eager bless only fires when a NEW
    split happened this run, so the flag alone must leave the entry
    key-for-key identical to the plain no-split archive — no merge, no
    `re_hashed`, no `split_info`."""
    title_id = "mov-en-2024-rehashonly"
    path, orig_hash = make_video(sandbox["media_dir"] / "RehashOnly.mkv")
    merges = _install_concat_merge(monkeypatch)

    main.cmd_prep_push_rep(title_id, str(path), eager_rehash=True)

    entry = _assert_whole_file_archive_oracle(
        sandbox, mock_device, title_id, "RehashOnly.mkv", orig_hash)
    assert merges == [], "no split => nothing to merge"
    assert "re_hashed" not in entry


# --- A9: tempdir ------------------------------------------------------------

def test_regression_tempdir_redirects_the_chunks_and_cleans_up(
        sandbox, make_video, stub_tech_specs, mock_device, fake_dummy,
        monkeypatch, tmp_path):
    """Group A / `tempdir <path>`. The big chunk artifacts move to
    `<tempdir>/<id>/_parts` (never the media volume) while the parity
    `checksums/` sidecars STAY next to the master; a clean run removes the
    per-entry scratch dir it created."""
    title_id = "mov-en-2024-temp"
    path, orig_hash = make_video(sandbox["media_dir"] / "Temp.mkv")
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    _plenty_of_disk(monkeypatch)
    split_calls = _install_fake_split(monkeypatch, n_chunks=2)

    main.cmd_prep_push_rep(title_id, str(path), "COUNT", "2", temp_dir=str(scratch))

    _assert_split_archive_oracle(
        sandbox, mock_device, title_id, "Temp.mkv", orig_hash, "COUNT", "2", 2)
    assert split_calls[0]["output_dir"] == str(scratch / title_id / main.SPLIT_DIR_NAME)
    assert not (sandbox["media_dir"] / main.SPLIT_DIR_NAME).exists(), \
        "with tempdir the media folder must never see a _parts dir"
    assert list(scratch.iterdir()) == [], \
        "a clean run removes the per-entry scratch dir it created"


# --- A10/A11: --extras and --extras-size ------------------------------------

def test_regression_extras_are_registered_pushed_and_dummied(
        sandbox, make_video, stub_tech_specs, mock_device, fake_dummy):
    """Group A / `--extras`. The one-shot promise on a brand-new title: the prep
    leg REGISTERS the folder, the extras phase UPLOADS the real bytes, and the
    replace phase DUMMIES them — while the main content's own outcome is
    unchanged."""
    title_id = "mov-en-2024-extras"
    media_dir = sandbox["media_dir"]
    path, orig_hash = make_video(media_dir / "Extras.mkv", marker=b"MAIN\n")
    extras_dir, originals, hashes = _seed_extras(media_dir, make_video)

    main.cmd_prep_push_rep(title_id, str(path), extras=[str(extras_dir)],
                           extras_size=("NONE", None))

    entry = mvcommon.load_library()[title_id]
    assert sorted(entry) == sorted(_ARCHIVED_KEYS_WHOLE_FILE + ["extras"])
    assert entry["status"] == "archived" and entry["uploaded"] is True
    assert entry["hash"] == orig_hash

    items = entry["extras"]["groups"]["Specials"]["items"]
    assert [it["sub_rel"] for it in items] == ["BTS.mkv", "Teaser.mkv"]
    for it in items:
        assert it["hash"] == hashes[it["filename"]], \
            "the registered hash must be the REAL pre-dummy sha256"
        assert it["uploaded"] is True and it["status"] == "archived"

    on_device = _device_files(mock_device)
    for it in items:
        remote = _whole_file_remote_name(it)
        assert remote in on_device, f"{remote} not on device: {sorted(on_device)}"
        assert on_device[remote].read_bytes() == originals[it["filename"]]
        assert on_device[remote].parent.name == "Specials"
    for _group, item_path, _it in main._extras_item_paths(entry):
        with open(item_path, "rb") as fh:
            assert fh.read() == FAKE_DUMMY_BYTES

    assert (media_dir / "Extras.mkv").read_bytes() == FAKE_DUMMY_BYTES
    _assert_no_working_leftovers(media_dir)


def test_regression_extras_size_is_independent_of_the_main_split(
        sandbox, make_video, stub_tech_specs, mock_device, fake_dummy,
        monkeypatch):
    """Group A / `--extras-size`. The extras' chunk size is INDEPENDENT of the
    command's own split: the master splits COUNT/3 while each extra splits
    SIZE_MB/0.1, proven from the recorded `split_video_file` calls (the exact
    values the two legs passed down)."""
    title_id = "mov-en-2024-extrasize"
    media_dir = sandbox["media_dir"]
    path, _orig_hash = make_video(media_dir / "ExtraSize.mkv", marker=b"MAIN\n")
    extras_dir, _originals, _hashes = _seed_extras(
        media_dir, make_video, files=(("BTS.mkv", b"SIZED-BTS\n"),))
    _plenty_of_disk(monkeypatch)
    split_calls = _install_fake_split(monkeypatch, n_chunks=2)

    main.cmd_prep_push_rep(title_id, str(path), "COUNT", "3",
                           extras=[str(extras_dir)],
                           extras_size=("SIZE_MB", "0.1"))

    entry = mvcommon.load_library()[title_id]
    item, = entry["extras"]["groups"]["Specials"]["items"]
    by_file_id = {c["file_id"]: c for c in split_calls}
    assert by_file_id[entry["short_id"]]["method"] == "COUNT"
    assert by_file_id[entry["short_id"]]["val"] == "3"
    assert by_file_id[item["short_id"]]["method"] == "SIZE_MB"
    assert by_file_id[item["short_id"]]["val"] == "0.1"
    assert entry["split_info"]["method"] == "COUNT"
    assert item["split_info"]["method"] == "SIZE_MB"
    assert item["uploaded"] is True and item["status"] == "archived"


# --- A12/A13: combinations --------------------------------------------------

def test_regression_combo_count_device_tempdir_rehash(
        sandbox, make_video, stub_tech_specs, mock_device, fake_dummy,
        monkeypatch, tmp_path):
    """Group A / COMBINATION 1 — `COUNT 2 device fakeserial rehash tempdir <p>`
    all at once. Each flag keeps its own behaviour with no interference:
    chunks staged in the scratch dir, pushed to the selected device, the eager
    canonical hash promoted, the scratch dir cleaned."""
    title_id = "mov-en-2024-combo1"
    path, orig_hash = make_video(sandbox["media_dir"] / "Combo1.mkv")
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    _plenty_of_disk(monkeypatch)
    split_calls = _install_fake_split(monkeypatch, n_chunks=2)
    merges = _install_concat_merge(monkeypatch)
    recorded = _record_subprocess(monkeypatch)

    main.cmd_prep_push_rep(title_id, str(path), "COUNT", "2",
                           device_id="fakeserial", eager_rehash=True,
                           temp_dir=str(scratch))

    entry = _assert_split_archive_oracle(
        sandbox, mock_device, title_id, "Combo1.mkv", orig_hash, "COUNT", "2", 2)
    assert entry["re_hashed"] is True and entry["hash"] == orig_hash
    assert split_calls[0]["output_dir"] == str(scratch / title_id / main.SPLIT_DIR_NAME)
    assert merges[0]["out"].startswith(str(scratch / title_id)), \
        "the eager merge temp lives next to the chunks, not on the media volume"
    assert list(scratch.iterdir()) == []
    assert all(c[1:3] == ["-s", "fakeserial"] for c in recorded if c and c[0] == "adb")


def test_regression_combo_size_split_with_extras_and_extras_size_and_device(
        sandbox, make_video, stub_tech_specs, mock_device, fake_dummy,
        monkeypatch):
    """Group A / COMBINATION 2 — `SIZE_MB 0.1 device fakeserial --extras <dir>
    --extras-size 0.1mb`. A split main file, a split extra, and a device
    selector together: both legs split, both land on the selected device, both
    end archived."""
    title_id = "mov-en-2024-combo2"
    media_dir = sandbox["media_dir"]
    path, orig_hash = make_video(media_dir / "Combo2.mkv", marker=b"MAIN\n")
    extras_dir, _originals, _hashes = _seed_extras(
        media_dir, make_video, files=(("BTS.mkv", b"COMBO-BTS\n"),))
    _plenty_of_disk(monkeypatch)
    split_calls = _install_fake_split(monkeypatch, n_chunks=2)
    recorded = _record_subprocess(monkeypatch)

    main.cmd_prep_push_rep(title_id, str(path), "SIZE_MB", "0.1",
                           device_id="fakeserial",
                           extras=[str(extras_dir)],
                           extras_size=("SIZE_MB", "0.1"))

    entry = mvcommon.load_library()[title_id]
    assert sorted(entry) == sorted(_ARCHIVED_KEYS_SPLIT + ["extras"])
    assert entry["status"] == "archived" and entry["hash"] == orig_hash
    assert entry["split_info"]["total_chunks"] == 2

    item, = entry["extras"]["groups"]["Specials"]["items"]
    assert item["uploaded"] is True and item["status"] == "archived"
    assert {c["file_id"] for c in split_calls} == {entry["short_id"], item["short_id"]}
    assert all(c["method"] == "SIZE_MB" for c in split_calls)

    on_device = _device_files(mock_device)
    chunk_names = [c["filename"] for c in entry["split_info"]["chunks"]]
    assert set(chunk_names) <= set(on_device)
    rebuilt = b"".join(on_device[n].read_bytes() for n in chunk_names)
    assert hashlib.sha256(rebuilt).hexdigest() == orig_hash
    assert all(c[1:3] == ["-s", "fakeserial"] for c in recorded if c and c[0] == "adb")
    assert (media_dir / "Combo2.mkv").read_bytes() == FAKE_DUMMY_BYTES
    _assert_no_working_leftovers(media_dir)


# --- A14: the byte-for-byte console pin -------------------------------------

def test_regression_prep_push_rep_console_contract_is_byte_for_byte_unchanged(
        sandbox, make_video, stub_tech_specs, mock_device, fake_dummy, capsys):
    """Group A / the explicit BYTE-FOR-BYTE pin (same spirit as
    `test_extras.py::test_season_resume_command_unchanged_without_extras`).

    `cmd_prep_push_rep`'s five structural console lines — the banner, the three
    STEP headers and the completion line — must be exactly what they were
    before IMP-D22, in exactly this order, and NONE of the new sibling
    command's strings may appear. A leak of the enrich leg into the old command
    would show up here first."""
    title_id = "mov-en-2024-contract"
    path, _orig_hash = make_video(sandbox["media_dir"] / "Contract.mkv")

    main.cmd_prep_push_rep(title_id, str(path))

    out = capsys.readouterr().out
    structural = [line for line in out.splitlines()
                  if line.startswith(("=== ", ">>> ")) or line.startswith("✅✅✅")]
    assert structural == [
        f"=== \U0001f680 AUTO-PILOT: PREP -> PUSH -> REPLACE for {title_id} ===",
        ">>> STEP 1: PREP",
        ">>> STEP 2: PUSH",
        ">>> STEP 3: REPLACE",
        "✅✅✅ AUTO-PILOT COMPLETE: Movie is safely archived.",
    ], out

    for leaked in ("AUTO-PILOT COMPLETE (archive + enrich)",
                   "will be changed to",
                   "using preset tmdb_id",
                   "-tvdbid is not supported",
                   "enrich skipped",
                   "non-interactive session",
                   "No TMDB API key configured"):
        assert leaked not in out, f"the enrich leg leaked into cmd_prep_push_rep: {leaked!r}"


# ===========================================================================
#   GROUP B (continued) — the remaining NEW-COMMAND scenarios
# ===========================================================================

class _FakeTMDBSearchable(_FakeTMDBById):
    """Search-AND-by-id TMDB fake, for the scenarios where the TITLE SEARCH is
    the point (auto-resolve, a deliberate miss, the EXA fallback).

    Deliberately a SUBCLASS rather than a replacement: `_FakeTMDBById`'s "there
    is no /search endpoint at all" property is load-bearing for the preset-id
    tests above and must not be softened. Seeded with a `search` dict
    ({lowercased query: [result, …]}) and a `details` dict ({tmdb_id: payload})
    served at `/movie/{id}` so an EXA-resolved id can also be fetched by id.
    """

    def __init__(self, search=None, details=None):
        super().__init__(movie_id=None, details=None)
        self.search = {k.lower(): v for k, v in (search or {}).items()}
        self.details = {int(k): v for k, v in (details or {}).items()}

    @property
    def search_queries(self):
        return [p.get("query") for u, p in self.calls if "/search/movie" in u]

    def get(self, url, params=None, headers=None, timeout=None, **kwargs):
        params = params or {}
        if "/search/movie" in url:
            self.calls.append((url, dict(params)))
            q = (params.get("query") or "").lower()
            return _Resp(200, json_data={"results": self.search.get(q, [])})
        for tmdb_id, payload in self.details.items():
            if url.endswith(f"/movie/{tmdb_id}"):
                self.calls.append((url, dict(params)))
                return _Resp(200, json_data=payload)
        return super().get(url, params, headers, timeout, **kwargs)


class _FakeTMDBByIdRich(_FakeTMDBById):
    """`_FakeTMDBById` plus the ONE extra endpoint `_write_nfo`'s richer element
    set needs (`/movie/{id}/credits`). The movie details payload doubles as the
    `_resolve_imdb_id` source (a MOVIE reads `imdb_id` straight off
    `/movie/{id}`), so no external_ids endpoint is required. Still has NO
    /search endpoint — every --nfo test here passes a preset id."""

    def __init__(self, movie_id, details, credits=None):
        super().__init__(movie_id, details)
        self.credits = credits or {}

    def get(self, url, params=None, headers=None, timeout=None, **kwargs):
        if url.endswith(f"/movie/{self.movie_id}/credits"):
            self.calls.append((url, dict(params or {})))
            return _Resp(200, json_data=self.credits)
        return super().get(url, params, headers, timeout, **kwargs)


AUTO_ID = "mov-en-2024-autoenrich"      # humanizes to the search title "autoenrich"
AUTO_TMDB_ID = 555001


def _search_hit(tmdb_id=AUTO_TMDB_ID, title="Autoenrich", year=2024):
    """A /search/movie result the ranker scores CONFIDENT for `AUTO_ID`:
    normalized title identical to the humanized slug (sim 1.0) AND the year
    agrees, which is the documented confident rule."""
    return {
        "id": tmdb_id, "title": title, "release_date": f"{year}-06-01",
        "popularity": 42.0, "poster_path": "/searched_poster.jpg",
        "backdrop_path": "/searched_backdrop.jpg",
        "overview": "Resolved by title search.", "vote_average": 6.5,
    }


# --- B: auto-resolve fallback when no id is given ---------------------------

def test_auto_resolve_without_a_preset_id_searches_and_applies_the_match(
        sandbox, make_video, stub_tech_specs, mock_device, fake_dummy,
        patch_tmdb_by_id, capsys):
    """No `-tmdbid` -> the enrich leg falls back to the SAME title-search
    waterfall `enrich_metadata` uses today: a /search/movie call is made, the
    confident match is applied, the folder is stamped and the art downloaded.
    (`patch_tmdb_by_id` is the generic installer — it patches
    `main.requests.get`, redirects both caches, installs a test key and seals
    the EXA fallback; only the fake handed to it differs.)"""
    path, _orig_hash = make_video(sandbox["media_dir"] / "AutoEnrich.mkv")
    fake = patch_tmdb_by_id(_FakeTMDBSearchable(search={"autoenrich": [_search_hit()]}))

    result = main.cmd_prep_push_rep_enrich(AUTO_ID, str(path), rename_choice="yes")

    assert result is True
    out = capsys.readouterr().out
    assert "using preset tmdb_id" not in out, "there is no preset — this must be the search path"
    assert "AUTO-PILOT COMPLETE (archive + enrich)" in out

    assert "autoenrich" in [q.lower() for q in fake.search_queries], \
        f"no /search/movie for the humanized title: {fake.search_queries}"

    entry = mvcommon.load_library()[AUTO_ID]
    assert entry["status"] == "archived"
    assert entry["metadata"]["tmdb_id"] == AUTO_TMDB_ID
    assert entry["metadata"]["title"] == "Autoenrich"
    assert entry["metadata"]["year"] == 2024
    assert entry["metadata"]["overview"] == "Resolved by title search."

    old_folder = sandbox["media_dir"]
    stamped = old_folder.parent / f"{old_folder.name} [tmdbid-{AUTO_TMDB_ID}]"
    assert stamped.is_dir() and not old_folder.exists()
    assert entry["folder_path"] == str(stamped)
    assert (stamped / "poster.jpg").read_bytes() == FAKE_JPG
    assert (stamped / "fanart.jpg").read_bytes() == FAKE_JPG


# --- B: the confirmation gate's non-interactive default ---------------------

def test_non_tty_ask_defaults_to_no_rename_and_never_calls_input(
        sandbox, make_video, stub_tech_specs, mock_device, fake_dummy,
        patch_tmdb_by_id, monkeypatch, capsys):
    """Decision 3 / the "never hangs the smoke gate" pin. With NO explicit
    flag (`rename_choice="ask"`, the CLI default) and a non-interactive stdin —
    which is what EVERY pytest and cron run has — the gate prints the
    before/after summary, defaults to NOT renaming, and never reaches `input()`.

    `builtins.input` is replaced with a raiser rather than a canned answer, so
    if the isatty guard ever regressed this test would FAIL LOUDLY instead of
    hanging the suite."""
    path, _ = _archive(sandbox, make_video)
    patch_tmdb_by_id(_FakeTMDBById(TMDB_ID, _movie_details(TMDB_ID)))
    original_folder = sandbox["media_dir"]

    assert main.sys.stdin.isatty() is False, \
        "a pytest run must present a non-interactive stdin — this is what keeps the gate safe"

    def _no_input(*a, **kw):
        raise AssertionError("input() must never be reached in a non-interactive session")

    monkeypatch.setattr(builtins, "input", _no_input)

    # rename_choice deliberately omitted -> the "ask" default.
    result = main.cmd_prep_push_rep_enrich(MOVIE_ID, str(path), tmdb_id=TMDB_ID)

    assert result is True
    out = capsys.readouterr().out
    stamped = original_folder.parent / f"{original_folder.name} [tmdbid-{TMDB_ID}]"
    assert f'"{original_folder}" will be changed to "{stamped}"' in out, \
        "the gate must show the exact before/after folder pair"
    assert "non-interactive session — defaulting to NOT renaming" in out
    assert "folder rename declined" in out
    assert "auto-confirmed" not in out and "auto-declined" not in out

    entry = mvcommon.load_library()[MOVIE_ID]
    assert entry["status"] == "archived"
    assert entry["metadata"]["tmdb_id"] == TMDB_ID, \
        "declining the rename must not skip the metadata write"
    assert entry["folder_path"] == str(original_folder)
    assert original_folder.is_dir() and not stamped.exists()
    assert (original_folder / "poster.jpg").read_bytes() == FAKE_JPG


# --- B: --nfo on / off ------------------------------------------------------

def _nfo_details(tmdb_id=TMDB_ID):
    """A rich /movie/{id} payload: the base resolve fields PLUS everything the
    IMP-D22 richer NFO element set reads (imdb_id / genres / runtime /
    production_companies)."""
    details = _movie_details(tmdb_id)
    details.update({
        "imdb_id": "tt7654321",
        "genres": [{"id": 18, "name": "Drama"}, {"id": 53, "name": "Thriller"}],
        "runtime": 121,
        "production_companies": [{"id": 7, "name": "Test Studio"}],
    })
    return details


_NFO_CREDITS = {
    "crew": [{"job": "Director", "name": "Jane Director"},
             {"job": "Editor", "name": "Ed Itor"}],
    "cast": [{"name": "Alice Star", "character": "Alice"},
             {"name": "Bob Support", "character": "Bob"}],
}


def test_nfo_written_with_the_flag_and_never_carries_a_tvdbid(
        sandbox, make_video, stub_tech_specs, mock_device, fake_dummy,
        patch_tmdb_by_id, capsys):
    """`--nfo` (write_nfo=True) writes a Kodi/Jellyfin `movie.nfo` into the
    (renamed) folder carrying the full IMP-D22 element set — AND, per Decision
    4/Decision 1, it NEVER emits a `<tvdbid>` or a tvdb `uniqueid`: MediaVault
    has no TVDB source, so any such value would be fabricated."""
    path, _ = _archive(sandbox, make_video)
    patch_tmdb_by_id(_FakeTMDBByIdRich(TMDB_ID, _nfo_details(), credits=_NFO_CREDITS))

    result = main.cmd_prep_push_rep_enrich(
        MOVIE_ID, str(path), tmdb_id=TMDB_ID, write_nfo=True, rename_choice="yes")

    assert result is True
    assert "wrote movie.nfo" in capsys.readouterr().out

    old_folder = sandbox["media_dir"]
    stamped = old_folder.parent / f"{old_folder.name} [tmdbid-{TMDB_ID}]"
    nfo_path = stamped / "movie.nfo"
    assert nfo_path.is_file(), f"movie.nfo missing in {sorted(p.name for p in stamped.iterdir())}"

    raw = nfo_path.read_text(encoding="utf-8")
    root = ET.fromstring(raw)
    assert root.tag == "movie"
    assert root.findtext("title") == "Enrich Movie"
    assert root.findtext("year") == "2024"
    assert root.findtext("tmdbid") == str(TMDB_ID)
    tmdb_uid = root.find("uniqueid[@type='tmdb']")
    assert tmdb_uid is not None and tmdb_uid.text == str(TMDB_ID)
    assert tmdb_uid.get("default") == "true"
    assert root.findtext("imdbid") == "tt7654321"
    assert [g.text for g in root.findall("genre")] == ["Drama", "Thriller"]
    assert root.findtext("runtime") == "121"
    assert root.findtext("premiered") == "2024-03-15"
    assert [s.text for s in root.findall("studio")] == ["Test Studio"]
    assert [d.text for d in root.findall("director")] == ["Jane Director"]
    assert [a.findtext("name") for a in root.findall("actor")] == ["Alice Star", "Bob Support"]

    # Decision 4 (LOCKED): no TVDB identifier may appear, in ANY shape.
    assert "tvdbid" not in raw.lower(), "a <tvdbid> would be fabricated — Decision 1/4 forbids it"
    assert root.find("tvdbid") is None
    assert root.find("uniqueid[@type='tvdb']") is None


def test_nfo_is_written_by_default(
        sandbox, make_video, stub_tech_specs, mock_device, fake_dummy,
        patch_tmdb_by_id, capsys):
    """IMP-U6 (D6): the stamp-time NFO is ON by default — the same run WITHOUT
    `--nfo` writes movie.nfo into the stamped folder (Plex's NFO agent pins the
    id from it), while everything else about the enrich (metadata, rename, art)
    is unchanged. (--no-nfo opts out.)"""
    path, _ = _archive(sandbox, make_video)
    patch_tmdb_by_id(_FakeTMDBByIdRich(TMDB_ID, _nfo_details(), credits=_NFO_CREDITS))

    result = main.cmd_prep_push_rep_enrich(
        MOVIE_ID, str(path), tmdb_id=TMDB_ID, rename_choice="yes")

    assert result is True
    assert "wrote movie.nfo" in capsys.readouterr().out

    old_folder = sandbox["media_dir"]
    stamped = old_folder.parent / f"{old_folder.name} [tmdbid-{TMDB_ID}]"
    nfo = stamped / "movie.nfo"
    assert nfo.is_file(), "the default stamp-time NFO must exist"
    assert f"<tmdbid>{TMDB_ID}</tmdbid>" in nfo.read_text(encoding="utf-8")
    entry = mvcommon.load_library()[MOVIE_ID]
    assert entry["metadata"]["tmdb_id"] == TMDB_ID
    assert entry["folder_path"] == str(stamped)
    assert (stamped / "poster.jpg").is_file()


# --- B: --no-web vs the EXA fallback ---------------------------------------

def test_no_web_blocks_the_exa_fallback_after_a_tmdb_miss(
        sandbox, make_video, stub_tech_specs, mock_device, fake_dummy,
        patch_tmdb_by_id, monkeypatch, capsys):
    """`--no-web` (no_web=True): when the TMDB title search MISSES, the EXA web
    fallback must not be consulted at all — even with a live EXA key present.
    The archive still stands and the command still returns True (Decision 7)."""
    path, _orig_hash = make_video(sandbox["media_dir"] / "AutoEnrich.mkv")
    patch_tmdb_by_id(_FakeTMDBSearchable(search={}))   # every query -> no results
    monkeypatch.setattr(mvcommon, "exa_api_key", lambda: "EXA-TEST-KEY")

    def _must_not_be_called(*a, **kw):
        raise AssertionError("--no-web must prevent the EXA fallback from ever firing")

    monkeypatch.setattr(main, "_exa_resolve_tmdb_id", _must_not_be_called)

    result = main.cmd_prep_push_rep_enrich(AUTO_ID, str(path), no_web=True,
                                           rename_choice="yes")

    assert result is True, "a resolve miss is warn-and-continue, not a failure"
    out = capsys.readouterr().out
    assert "NO TMDB match" in out
    assert "AUTO-PILOT COMPLETE (archive + enrich)" in out

    entry = mvcommon.load_library()[AUTO_ID]
    assert entry["status"] == "archived", "the archive leg is untouched by an enrich miss"
    assert "tmdb_id" not in entry.get("metadata", {})
    assert entry["folder_path"] == str(sandbox["media_dir"]), "a miss must never rename"


def test_exa_fallback_runs_when_no_web_is_off(
        sandbox, make_video, stub_tech_specs, mock_device, fake_dummy,
        patch_tmdb_by_id, monkeypatch, capsys):
    """The CONVERSE of the test above — without it, `--no-web` proves nothing.
    Same TMDB miss, same EXA key, but `no_web=False`: the fallback IS consulted,
    its id is fetched BY ID, and the confident result is applied."""
    path, _orig_hash = make_video(sandbox["media_dir"] / "AutoEnrich.mkv")
    exa_details = _movie_details(AUTO_TMDB_ID, title="Web Resolved", year=2024)
    patch_tmdb_by_id(_FakeTMDBSearchable(search={}, details={AUTO_TMDB_ID: exa_details}))
    monkeypatch.setattr(mvcommon, "exa_api_key", lambda: "EXA-TEST-KEY")

    exa_calls = []

    def _fake_exa(title, year, kind):
        exa_calls.append((title, year, kind))
        return AUTO_TMDB_ID

    monkeypatch.setattr(main, "_exa_resolve_tmdb_id", _fake_exa)

    result = main.cmd_prep_push_rep_enrich(AUTO_ID, str(path), rename_choice="yes")

    assert result is True
    out = capsys.readouterr().out
    assert f"resolved via web search: tmdb_id={AUTO_TMDB_ID}" in out
    assert exa_calls == [("autoenrich", 2024, "movie")]

    entry = mvcommon.load_library()[AUTO_ID]
    assert entry["metadata"]["tmdb_id"] == AUTO_TMDB_ID
    assert entry["metadata"]["title"] == "Web Resolved"
    stamped = sandbox["media_dir"].parent / f"{sandbox['media_dir'].name} [tmdbid-{AUTO_TMDB_ID}]"
    assert entry["folder_path"] == str(stamped) and stamped.is_dir()


# --- B: extras and enrich in the same run -----------------------------------

def test_extras_and_enrich_both_complete_in_one_run(
        sandbox, make_video, stub_tech_specs, mock_device, fake_dummy,
        patch_tmdb_by_id, capsys):
    """`--extras` and the enrich leg do not interfere in either direction: the
    extras group is still registered, pushed and dummied by the archive phase,
    AND the enrich still applies + stamps the folder afterwards. Because the
    stamp happens AFTER the extras were archived, the extras' stored
    `group_rel`/`sub_rel` must still resolve under the RENAMED folder — the
    IMP-D17 relative-path guarantee, proven end to end here."""
    media_dir = sandbox["media_dir"]
    path, orig_hash = make_video(media_dir / "AutoEnrich.mkv", marker=b"MAIN\n")
    extras_dir, originals, hashes = _seed_extras(
        media_dir, make_video, files=(("BTS.mkv", b"ENRICH-BTS\n"),))
    patch_tmdb_by_id(_FakeTMDBById(TMDB_ID, _movie_details(TMDB_ID)))

    result = main.cmd_prep_push_rep_enrich(
        MOVIE_ID, str(path), tmdb_id=TMDB_ID, extras=[str(extras_dir)],
        extras_size=("NONE", None), rename_choice="yes")

    assert result is True
    out = capsys.readouterr().out
    assert "EXTRAS UPLOAD COMPLETE" in out
    assert "AUTO-PILOT COMPLETE (archive + enrich)" in out

    entry = mvcommon.load_library()[MOVIE_ID]
    assert entry["status"] == "archived" and entry["hash"] == orig_hash
    assert entry["metadata"]["tmdb_id"] == TMDB_ID

    stamped = media_dir.parent / f"{media_dir.name} [tmdbid-{TMDB_ID}]"
    assert entry["folder_path"] == str(stamped) and stamped.is_dir()

    item, = entry["extras"]["groups"]["Specials"]["items"]
    assert item["hash"] == hashes["BTS.mkv"]
    assert item["uploaded"] is True and item["status"] == "archived"

    on_device = _device_files(mock_device)
    remote = _whole_file_remote_name(item)
    assert on_device[remote].read_bytes() == originals["BTS.mkv"]
    assert on_device[remote].parent.name == "Specials"

    # The extras still resolve through the RENAMED folder, and are dummies.
    paths = list(main._extras_item_paths(entry))
    assert len(paths) == 1
    _group, item_path, _it = paths[0]
    assert item_path == str(stamped / "Specials" / "BTS.mkv")
    with open(item_path, "rb") as fh:
        assert fh.read() == FAKE_DUMMY_BYTES
    assert (stamped / "poster.jpg").read_bytes() == FAKE_JPG
