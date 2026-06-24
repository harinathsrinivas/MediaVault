import shutil
import hashlib
import subprocess
import sys, os, json
import pytest

# Ensure repo root is importable BEFORE importing the app modules, so a bare
# `pytest` (which, unlike `python -m pytest`, does NOT add the CWD to sys.path)
# can import them from any CWD.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import main      # repo root ensured on sys.path above
import mvcommon  # authoritative home of LIBRARY_* + load_library/save_library

FAKE_ORIGINAL_BYTES = b"ORIGINAL-MASTER-BYTES"
FAKE_DUMMY_BYTES    = b"DUMMY"

# A tiny payload, repeated, that comfortably clears DUMMY_MAX_BYTES (200_000)
# so the "real media" code paths (hash check, prep, restore) run instead of
# the dummy early-skip. ~264 KB keeps hashing fast while staying well over.
# Shared here (parent conftest) so both top-level tests/ and tests/smoke/ can
# resolve the `make_video` fixture (child dirs inherit parent conftest fixtures).
_REAL_MEDIA_BYTES = b"SMOKE-REAL-MEDIA-MASTER\n" * 11000  # ~264 KB > 200_000

TEST_ENTRY_ID = "mov_test_c9_001"  # "mov" prefix -> goes to LIBRARY_MOVIES


@pytest.fixture()
def sandbox(tmp_path, monkeypatch):
    """
    Redirects all three LIBRARY_* constants AND LOCAL_ROOT to a sandbox tree,
    creates the required directories, and hard-guards against real C:\\Media.

    The LOCAL_ROOT redirect makes the "no writes to real C:\\Media" guarantee
    STRUCTURAL: the whole-tree walkers (cmd_scan_unprepped @main.py:2459-2461,
    cmd_recover(scan=True) @main.py:738) and any LOCAL_ROOT-derived path build
    their roots from LOCAL_ROOT/{Movies,Series,Anime}; without this patch they
    would walk (and, for write paths, touch) the real C:\\Media. LOCAL_ROOT is
    pointed at tmp_path/"Media" — the same dir under which this fixture (and the
    sandbox_alias fixture, which seeds Series/.../*.mkv) create their media — so
    scan tests stay MEANINGFUL (they walk the real fixture files, not an empty
    dir) while never escaping tmp_path. Same value test_recover_cli.py and the
    old smoke_local_root used; smoke_local_root is now a thin confirm of this.

    Yields: dict with keys:
        media_dir    - Path: sandbox folder holding media files
        lib_movies   - Path: sandbox LIBRARY_MOVIES json
        lib_series   - Path: sandbox LIBRARY_SERIES json
        lib_anime    - Path: sandbox LIBRARY_ANIME json
        local_root   - Path: tmp_path/"Media" (the patched LOCAL_ROOT, == media_dir.parent.parent)
    """
    media_dir = tmp_path / "Media" / "Movies" / "TestMovie"
    media_dir.mkdir(parents=True)

    media_root = tmp_path / "Media"  # the LOCAL_ROOT redirect target (holds Movies/Series/Anime)

    lib_dir = tmp_path / "library"
    lib_dir.mkdir()

    lib_movies = lib_dir / "library_movies.json"
    lib_series = lib_dir / "library_series.json"
    lib_anime  = lib_dir / "library_anime.json"

    # Hard guard: fail immediately if any constant still points under C:\Media.
    # After the mvcommon extraction, load_library/save_library read mvcommon's
    # OWN module-level LIBRARY_* bindings, so mvcommon is the authoritative patch
    # target. main imported the names by value (a separate binding), so we patch
    # both mvcommon and main to keep every reader pointed at the sandbox. The
    # SAME import-by-value hazard applies to LOCAL_ROOT, so it is dual-patched too
    # (a future regression that forgets either patch trips the C:\Media guard).
    for attr, path in [
        ("LIBRARY_MOVIES", str(lib_movies)),
        ("LIBRARY_SERIES", str(lib_series)),
        ("LIBRARY_ANIME",  str(lib_anime)),
        ("LOCAL_ROOT",     str(media_root)),
    ]:
        assert "C:\\Media" not in path, f"Safety check failed: {attr} still points to real media!"
        monkeypatch.setattr(mvcommon, attr, path)
        monkeypatch.setattr(main, attr, path)

    yield {
        "media_dir":  media_dir,
        "lib_movies": lib_movies,
        "lib_series": lib_series,
        "lib_anime":  lib_anime,
        "local_root": media_root,
    }


@pytest.fixture()
def sandbox_entry(sandbox, tmp_path):
    """
    Creates a fake media file with known bytes at a sandbox path,
    seeds LIBRARY_MOVIES with a minimal entry cmd_replace can operate on.

    Yields: dict with keys:
        entry_id   - str: TEST_ENTRY_ID
        media_dir  - Path: folder holding the file
        filename   - str: just the filename
        orig_path  - Path: full path to the fake original file
    """
    filename = "test_movie.mkv"
    orig_path = sandbox["media_dir"] / filename
    orig_path.write_bytes(FAKE_ORIGINAL_BYTES)

    entry = {
        TEST_ENTRY_ID: {
            "status": "onboarded",
            "uploaded": True,
            "folder_path": str(sandbox["media_dir"]),
            "filename": filename,
            "type": "movie",
        }
    }
    sandbox["lib_movies"].write_text(json.dumps(entry), encoding="utf-8")
    # Other two libs must exist (empty) so load_library doesn't skip them
    sandbox["lib_series"].write_text("{}", encoding="utf-8")
    sandbox["lib_anime"].write_text("{}", encoding="utf-8")

    yield {
        "entry_id":  TEST_ENTRY_ID,
        "media_dir": sandbox["media_dir"],
        "filename":  filename,
        "orig_path": orig_path,
    }


@pytest.fixture()
def sandbox_alias(sandbox, tmp_path):
    """Sandbox library seeded with a combined-episode (multi_ep_alias) chain.

    Built ON TOP OF the `sandbox` fixture — it inherits sandbox's dual LIBRARY_*
    patch (BOTH mvcommon.LIBRARY_* AND main.LIBRARY_*, the IMP-A1 binding hazard)
    and its hard-guard against real C:\\Media. This fixture does NOT re-implement
    that redirection; it only seeds an alias-bearing Series library into it via
    the real `mvcommon.save_library` helper (which routes the three `tv-…` ids
    into library_series.json and leaves movies/anime empty).

    Seeds three Series entries that mirror what `cmd_prep_season` produces for a
    combined-episode file (e.g. `…S04E19E20.mkv`):
      1. season_map  `tv-en-2009-bsg-s04`        — type/folder_path/total_episodes/children
                       (children = [primary, alias], sorted; total_episodes = 2)
      2. leaf primary `tv-en-2009-bsg-s04e19`     — the SAME key set cmd_prep writes
                       (short_id/filename/folder_path/status="local_ready"/uploaded=False/
                        search_term/hash/metadata/tech_spec/parent_id), pointing at a REAL
                        .mkv on disk under the sandbox media dir.
      3. multi_ep_alias `tv-en-2009-bsg-s04e20`   — the exact 3-key schema and NOTHING else:
                        {type:"multi_ep_alias", alias_of:<primary>, parent_id:<season>}.

    The primary's .mkv is written LARGER than DUMMY_MAX_BYTES (200_000) with
    deterministic bytes, and the leaf `hash` is that file's real sha256, so
    cmd_check/cmd_restore treat it as real media (not an already-archived dummy)
    and the hash verifies. (Files < DUMMY_MAX_BYTES are early-skipped as dummies
    by cmd_check at main.py:1108 — A3 exercises check/push/restore on the primary,
    so the file must clear that threshold.)

    Note: if a test ever drives `mainfetch`, that module's `mainfetch.LIBRARY_*`
    bindings would ALSO need patching (same import-by-value hazard). A2 does not
    exercise mainfetch, so this fixture does not patch it — add it if needed.

    Yields a dict:
        primary_id - str: "tv-en-2009-bsg-s04e19" (the real leaf, holds the file)
        alias_id   - str: "tv-en-2009-bsg-s04e20" (the multi_ep_alias)
        season_id  - str: "tv-en-2009-bsg-s04"    (the season_map parent)
        media_dir  - Path: the season folder holding the .mkv (under tmp_path)
        orig_path  - Path: full path to the primary's on-disk .mkv
        sandbox    - dict: the underlying sandbox fixture's paths (lib_*/media_dir)
    """
    season_id = "tv-en-2009-bsg-s04"
    primary_id = "tv-en-2009-bsg-s04e19"
    alias_id = "tv-en-2009-bsg-s04e20"

    # Season media dir under the SANDBOX temp tree (Series path) — never C:\Media.
    media_dir = tmp_path / "Media" / "Series" / "BSG" / "Season 04"
    media_dir.mkdir(parents=True)

    # Real primary file, LARGER than DUMMY_MAX_BYTES so check/restore don't treat
    # it as an archived dummy. Deterministic bytes -> stable sha256 for `hash`.
    filename = "BSG.S04E19E20.mkv"
    orig_path = media_dir / filename
    orig_path.write_bytes(b"BSG-COMBINED-EP-MASTER\n" * 9000)  # ~207 KB > 200_000

    # Hard guard: the file we just created must live under tmp_path and must NEVER
    # be a real-media path. (sandbox already hard-guards the LIBRARY_* constants.)
    tmp_resolved = tmp_path.resolve()
    op_resolved = orig_path.resolve()
    assert tmp_resolved in op_resolved.parents, f"primary .mkv escaped tmp_path: {orig_path}"
    assert "C:\\Media" not in str(op_resolved), f"primary .mkv must never touch real C:\\Media: {orig_path}"
    assert os.path.getsize(orig_path) > main.DUMMY_MAX_BYTES, "primary .mkv must exceed DUMMY_MAX_BYTES"

    short_id = mvcommon.generate_short_id(primary_id)
    file_hash = hashlib.sha256(orig_path.read_bytes()).hexdigest()
    name_no_ext, ext = os.path.splitext(filename)

    # Leaf primary: byte-for-byte the key set cmd_prep writes (main.py:906-919),
    # plus parent_id since this episode belongs to a season_map.
    primary_entry = {
        "short_id": short_id,
        "filename": filename,
        "folder_path": str(media_dir),
        "status": "local_ready",
        "uploaded": False,
        "search_term": f"{name_no_ext} [{short_id}]{ext}",
        "hash": file_hash,
        "metadata": main.parse_metadata_from_id(primary_id),
        "tech_spec": {"resolution": "1080p", "video_codec": "HEVC", "size_bytes": os.path.getsize(orig_path)},
        "parent_id": season_id,
    }

    library = {
        # season_map parent — mirrors cmd_prep's season_map shape (main.py:881-886);
        # children includes the alias (appended at main.py:1085-1087), sorted.
        season_id: {
            "type": "season_map",
            "folder_path": str(media_dir),
            "total_episodes": 2,
            "children": sorted([primary_id, alias_id]),
        },
        primary_id: primary_entry,
        # multi_ep_alias — EXACT schema, nothing else (main.py:1080-1084).
        alias_id: {
            "type": "multi_ep_alias",
            "alias_of": primary_id,
            "parent_id": season_id,
        },
    }

    # Seed via the real helper (routes all three tv- ids into library_series.json,
    # leaves movies/anime as {}). The sandbox hard-guard governs the write paths.
    mvcommon.save_library(library)

    yield {
        "primary_id": primary_id,
        "alias_id": alias_id,
        "season_id": season_id,
        "media_dir": media_dir,
        "orig_path": orig_path,
        "sandbox": sandbox,
    }


@pytest.fixture()
def mock_device(tmp_path, monkeypatch):
    """
    Stateful fake Android device backed by tmp_path/device/.
    Intercepts main.subprocess.run for all adb calls and executes them against
    the local filesystem instead of a real device. See docs/testing-strategy.md.

    adb push [-p] <local> <remote>   -> shutil.copy2 into device_dir
    adb shell mv '<src>' '<dst>'     -> os.rename within device_dir
    adb shell rm '<path>'            -> os.unlink from device_dir
    adb shell mkdir -p '<path>'      -> os.makedirs inside device_dir
    adb shell md5sum '<path>'        -> md5 of file in device_dir on stdout
    adb devices                      -> fake device list

    Yields device_dir (pathlib.Path). Tests inspect it with rglob("*.mkv") etc.
    Does NOT conflict with FakeAdb in test_cmd_push_partial.py — both patch
    subprocess.run but they are used in separate test functions.
    """
    device_dir = tmp_path / "device"
    device_dir.mkdir()

    def _parse_adb(argv):
        """Strip 'adb' and optional '-s <id>' prefix; return the subcommand list."""
        argv = list(argv)
        i = 0
        while i < len(argv):
            if argv[i] == "adb":
                i += 1
            elif argv[i] == "-s":
                i += 2
            else:
                break
        return argv[i:]

    class _R:
        returncode = 0
        stdout = ""

    def fake_run(argv, check=False, capture_output=False, **kwargs):
        cmd = _parse_adb(argv)
        res = _R()
        if not cmd:
            return res

        if cmd[0] == "push":
            # adb push [-p] <local> <remote> — skip flags
            positional = [a for a in cmd[1:] if not a.startswith("-")]
            local, remote = positional[0], positional[1]
            dest = device_dir / remote.lstrip("/")
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(local, dest)

        elif cmd[0] == "shell":
            sub = cmd[1:]
            if not sub:
                return res
            if sub[0] == "mv":
                src = sub[1].strip("'")
                dst = sub[2].strip("'")
                src_p = device_dir / src.lstrip("/")
                dst_p = device_dir / dst.lstrip("/")
                dst_p.parent.mkdir(parents=True, exist_ok=True)
                if src_p.exists():
                    src_p.rename(dst_p)
                elif check:
                    raise subprocess.CalledProcessError(1, argv)
            elif sub[0] == "rm":
                path = sub[-1].strip("'")
                p = device_dir / path.lstrip("/")
                if p.exists():
                    p.unlink()
            elif sub[0] == "mkdir":
                path = sub[-1].strip("'")
                (device_dir / path.lstrip("/")).mkdir(parents=True, exist_ok=True)
            elif sub[0] == "md5sum":
                path = sub[-1].strip("'")
                p = device_dir / path.lstrip("/")
                if p.exists():
                    h = hashlib.md5(p.read_bytes()).hexdigest()
                    res.stdout = f"{h}  {path}\n"
                elif check:
                    raise subprocess.CalledProcessError(1, argv)

        elif cmd[0] == "devices":
            res.stdout = "List of devices attached\nfake123\tdevice\n"

        return res

    class _FakeProc:
        """Minimal stand-in for subprocess.Popen's return value, sufficient for
        the cmd_dispatch_fetch streaming loop. That loop touches ONLY two members:
        `for line in proc.stdout:` (iteration) and `proc.wait()` in a finally.
        `.stdout` is a list of already-newline-terminated mainfetch-style lines,
        so the loop reads a few lines, hits EOF, and exits without blocking; no
        real child / Selenium is spawned. `.wait()` returns 0."""

        def __init__(self):
            self.stdout = [
                "🔹 PROCESSING: <id>\n",
                "   > Detected Split File (1 chunks)\n",
                "     ✅ MOVED: dummy\n",
                "✅ ENTRY COMPLETE.\n",
            ]

        def wait(self):
            return 0

    def fake_popen(cmd, **kwargs):
        # Neutralizes `python mainfetch.py fetch ...` (the only subprocess.Popen
        # call in main.py — see cmd_dispatch_fetch). Accepts the real call's
        # kwargs (stdout/stderr/text/bufsize/encoding/errors/env) via **kwargs and
        # ignores them. Returns a fake process that yields a few lines then EOFs.
        return _FakeProc()

    monkeypatch.setattr(main.subprocess, "run", fake_run)
    monkeypatch.setattr(main.subprocess, "Popen", fake_popen)
    yield device_dir


@pytest.fixture()
def mock_fetch(mock_device, tmp_path, monkeypatch):
    """IMP-C2 — browser download stub (testing-strategy §4.6).

    Monkeypatches mainfetch.trigger_download to copy a pre-seeded file from the
    mock_device device_dir into a local restore directory and return True, so
    fetch/restore logic can be exercised without Selenium or a real browser.

    Composition / binding notes:
      - Composes `mock_device` (which intercepts main.subprocess.run) so the
        fake device filesystem is the search source; uses `tmp_path` for the
        restore dir. Never references a real C:\\Media path.
      - This fixture does NOT redirect LIBRARY_*; tests that need the library
        boundary still pull in the `sandbox` fixture, which (post-A1) patches
        BOTH mvcommon.LIBRARY_* and main.LIBRARY_*. mock_fetch only patches
        mainfetch.trigger_download, so there is no LIBRARY_* binding hazard here.

    Yields the restore_dir (pathlib.Path).
    """
    import shutil
    import mainfetch

    restore_dir = tmp_path / "restore"
    restore_dir.mkdir(exist_ok=True)

    def _fake_trigger(driver, query, index=0):
        matches = list(mock_device.rglob(f"*{query}*"))
        if not matches:
            return False
        shutil.copy2(matches[0], restore_dir / matches[0].name)
        return True

    monkeypatch.setattr(mainfetch, "trigger_download", _fake_trigger)
    yield restore_dir


@pytest.fixture()
def fake_dummy(monkeypatch):
    """
    Replaces main.make_video_dummy with a stub that writes FAKE_DUMMY_BYTES
    to the given path and returns True. No ffmpeg needed.
    """
    def _fake_make_video_dummy(tmp_path_arg, ext):
        with open(tmp_path_arg, "wb") as f:
            f.write(FAKE_DUMMY_BYTES)
        return True

    monkeypatch.setattr(main, "make_video_dummy", _fake_make_video_dummy)


@pytest.fixture()
def web_as_local_admin(monkeypatch):
    """Make every /api/* request behave as the GENUINE-LOCAL ADMIN (IMP-E15).

    WHY THIS EXISTS — the secure-by-default auth rule:
      webui.server now ALWAYS enforces auth on /api/* (the old "empty token store
      -> auth off" escape was removed because it left a 0.0.0.0 bind with an empty
      store unauthenticated to the whole LAN/tailnet). Under the new rule a request
      is allowed IFF it is the genuine-local admin OR carries a valid minted token.

      The ENDPOINT/BEHAVIOR web tests drive the API as the LOCAL OWNER — they
      exercise the endpoints (reclaim/items/tree/actions/jobs/demo/progress), NOT
      the auth gate — and FastAPI ``TestClient`` reports ``request.client.host`` as
      ``"testclient"`` (NON-loopback), so without this they would be treated as a
      remote non-admin and 401 on every /api/* call. Patching the admin predicate
      to True makes those requests the owner, so auth never interferes with what
      they actually test. ``_is_authed`` short-circuits on the admin branch, so the
      token store is never consulted for these tests.

    SCOPING (deliberate — NOT a global blanket): this fixture is OPT-IN. It is
    applied per-module via ``pytestmark = pytest.mark.usefixtures("web_as_local_admin")``
    in exactly the endpoint/behavior modules (test_web_endpoints, test_web_items,
    test_web_tree, test_web_demo, test_web_progress) and the web smoke cases. The
    AUTH-SPECIFIC modules (test_web_tokens, test_web_auth) MUST NOT pull this in —
    they test admin-vs-remote gating directly and simulate the client host
    themselves via ``TestClient(app, client=(host, port))``.

    Patches the module attribute ``webui.server._is_genuine_local_admin`` (read at
    request time by the auth middleware, /api/whoami, the token endpoints, and
    /api/open-folder). The patch is undone automatically at fixture teardown.

    NOTE on /api/open-folder: this DOES make a TestClient request look like the
    local admin, so the admin-gated open-folder/token endpoints would be reachable
    here too. That is fine for the endpoint modules — the open-folder tests in
    test_web_tree.py that assert the 403/localhost rule set their OWN client host
    explicitly (127.0.0.1 / non-localhost) and do not rely on this predicate, and
    the dedicated admin-vs-remote gating is owned by the auth modules that exclude
    this fixture.
    """
    pytest.importorskip("fastapi")  # webui.server imports fastapi at module load
    import webui.server as _web_server

    monkeypatch.setattr(_web_server, "_is_genuine_local_admin", lambda request: True)


@pytest.fixture()
def make_video():
    """Factory: write a tiny (~264 KB) .mkv that clears DUMMY_MAX_BYTES.

    Returns write(path, marker=b"") -> (Path, sha256_hex). Deterministic bytes
    so the caller can store the returned hash in a library entry and have
    cmd_check / cmd_restore verify it. `marker` (optional) is prepended so two
    files can differ.

    Promoted to tests/conftest.py from tests/smoke/conftest.py so that both
    top-level tests/ files and tests/smoke/ (which inherits the parent conftest)
    can resolve this fixture.
    """
    import hashlib as _hashlib

    def write(path, marker=b""):
        path = str(path)
        data = marker + _REAL_MEDIA_BYTES
        with open(path, "wb") as f:
            f.write(data)
        assert os.path.getsize(path) > main.DUMMY_MAX_BYTES, \
            "make_video must write > DUMMY_MAX_BYTES so the real-media path is taken"
        return path, _hashlib.sha256(data).hexdigest()

    return write


# ===========================================================================
# Auto-rollback test infrastructure (added for the auto-rollback feature).
#
# These helpers are the shared failure-injection + fixture surface the Step 1
# baseline oracle AND the Step 3 candidate scenario matrices build on. They
# mock ONLY at the I/O boundary (subprocess.run, merge_video_files,
# get_tech_specs) per docs/testing-strategy.md §1 — application logic always
# runs real. Nothing here references a real C:\Media path or real library_*.json
# (the `sandbox` fixture's hard-guard still governs every library write).
# ===========================================================================


@pytest.fixture()
def stub_tech_specs(monkeypatch):
    """Replace main.get_tech_specs with a deterministic stub so cmd_prep does
    not depend on pymediainfo / a real MediaInfo parse of the fake fixture file.

    cmd_prep calls get_tech_specs(filepath) once and stores the result verbatim
    under entry["tech_spec"]; the value is opaque to rollback logic, so a fixed
    dict keeps the prep happy-path deterministic across machines. Yields the
    dict that will be stored so a test can assert the entry mirrors it."""
    specs = {"resolution": "1080p", "video_codec": "HEVC", "size_bytes": 0}

    def _fake_get_tech_specs(filepath):
        out = dict(specs)
        out["size_bytes"] = os.path.getsize(filepath)
        return out

    monkeypatch.setattr(main, "get_tech_specs", _fake_get_tech_specs)
    yield specs


class FailNthSubprocess:
    """subprocess.run replacement that succeeds for the first (N-1) *matching*
    calls then raises CalledProcessError on the Nth, modelling a permanent ADB
    failure at a chosen point. Composes ON TOP of an underlying run impl
    (default: a no-op success) so the surrounding command logic — path math,
    library writes, .partial naming — still executes for real.

    Args:
      fail_on_nth: 1-based index of the matching call that should raise.
      match: predicate(argv) -> bool selecting which calls are counted/failed.
             Default counts every call. Use e.g. lambda a: "push" in a to fail
             the Nth push specifically.
      inner: optional underlying run(argv, **kw) used for the calls that are
             allowed through (e.g. a mock_device fake_run to actually move
             bytes). When None, allowed calls return a returncode==0 stub.

    Records every argv in `.calls` for post-hoc assertions.
    """

    def __init__(self, fail_on_nth, match=None, inner=None):
        self.fail_on_nth = fail_on_nth
        self.match = match or (lambda argv: True)
        self.inner = inner
        self.calls = []
        self._matched = 0

    def run(self, argv, check=False, **kwargs):
        argv = list(argv)
        self.calls.append(argv)
        if self.match(argv):
            self._matched += 1
            if self._matched == self.fail_on_nth:
                if check:
                    raise subprocess.CalledProcessError(1, argv)

                class _R:
                    returncode = 1
                    stdout = ""

                return _R()
        if self.inner is not None:
            return self.inner(argv, check=check, **kwargs)

        class _R:
            returncode = 0
            stdout = ""

        return _R()


@pytest.fixture()
def fail_nth_subprocess(monkeypatch):
    """Factory fixture: returns a callable
        install(fail_on_nth, match=None, inner=None) -> FailNthSubprocess
    that patches main.subprocess.run with a FailNthSubprocess and hands the
    recorder back so a test can inspect `.calls`. Use this to fail the Nth adb
    push / mv / mkdir during cmd_push without duplicating the FakeAdb recorder.
    Also stubs mvcommon.time.sleep so retry() backoff is instant."""
    monkeypatch.setattr(mvcommon.time, "sleep", lambda *_a, **_k: None)

    def install(fail_on_nth, match=None, inner=None):
        rec = FailNthSubprocess(fail_on_nth, match=match, inner=inner)
        monkeypatch.setattr(main.subprocess, "run", rec.run)
        return rec

    return install


@pytest.fixture()
def fail_merge(monkeypatch):
    """Factory fixture for failing merge_video_files / mkvmerge in cmd_restore.

    Returns install(mode="return_false"|"raise") which patches
    main.merge_video_files to either return False (the in-band failure signal
    the code already handles) or raise RuntimeError (an unexpected mkvmerge
    crash). Records the number of merge attempts in the returned dict's "n".
    Use the split-restore PONR comes AFTER a successful merge, so a merge
    failure is a *pre-PONR* (reversible) event the rollback mechanism must
    handle without faking a restore."""
    state = {"n": 0}

    def install(mode="return_false"):
        def _merge(chunk_paths, output_path, seed=None):
            state["n"] += 1
            if mode == "raise":
                raise RuntimeError("simulated mkvmerge crash")
            return False

        monkeypatch.setattr(main, "merge_video_files", _merge)
        return state

    return install


def _ffmpeg_available():
    """True if an ffmpeg binary is reachable, via the SAME resolver PRODUCTION
    uses (`main.resolve_ffmpeg()` → configured `FFMPEG_PATH` if it exists on disk,
    else `shutil.which("ffmpeg")`). Used to gate the real-split fixtures so
    machines without any ffmpeg skip those tests cleanly.

    B1b: this deliberately reuses the production resolver instead of a divergent
    PATH-only `shutil.which("ffmpeg")` check. On a box where ffmpeg lives only at
    the configured path (e.g. Emby's bundled binary, not on PATH), the PATH-only
    check wrongly reported "no ffmpeg" and the real-binary tests skipped even
    though the ffmpeg the app uses is present. Mirrors `_mkvmerge_available`'s
    configured-path-or-PATH logic. Genuine absence (resolver returns None) still
    skips cleanly — it does not hard-fail collection on a machine with no ffmpeg."""
    return main.resolve_ffmpeg() is not None


@pytest.fixture()
def ffmpeg_multichunk_mkv(tmp_path):
    """ffmpeg-generated multi-MB MKV for tests that need a GENUINE split (i.e.
    they exercise the real split_video_file path rather than a pre-seeded
    _parts/ folder). Skips cleanly when ffmpeg is absent so the suite stays
    green on machines without it (docs/testing-strategy.md §4 / §11).

    Generates a ~6 MB testsrc MKV at tmp_path. Yields its Path. The caller pairs
    it with a small split target (e.g. SIZE_MB 2) to force multiple chunks.
    Never writes under real C:\\Media."""
    ffmpeg = main.resolve_ffmpeg()  # configured FFMPEG_PATH, else PATH; None if truly absent
    if ffmpeg is None:
        pytest.skip("ffmpeg not available — skipping real-split fixture")

    out = tmp_path / "bigsample.mkv"
    # testsrc at a modest resolution/duration produces a few MB of real MKV.
    # Invoke the RESOLVED binary (B1b) — not bare "ffmpeg" — so this runs against
    # the same ffmpeg production uses (e.g. Emby's bundled binary, off PATH).
    cmd = [
        ffmpeg, "-y", "-f", "lavfi",
        "-i", "testsrc=duration=8:size=640x480:rate=25",
        "-c:v", "libx264", "-pix_fmt", "yuv420p",
        str(out),
    ]
    proc = subprocess.run(cmd, capture_output=True)
    if proc.returncode != 0 or not out.exists():
        pytest.skip("ffmpeg invocation failed — skipping real-split fixture")
    yield out


@pytest.fixture()
def ffmpeg_splittable_master_mkv(tmp_path):
    """ffmpeg-generated HIGH-ENTROPY ~60 MB MKV master for tests that drive the
    LIVE split path inside cmd_push (split_video_file called during the push),
    not a pre-seeded _parts/ folder. Yields its Path. Never writes under real
    C:\\Media.

    Why this is separate from `ffmpeg_multichunk_mkv`: that fixture's `testsrc`
    pattern compresses to ~50 KB, and `split_video_file` adds a +10 MB per-chunk
    buffer (main.py:190), so a tiny source can NEVER split into ≥2 chunks —
    cmd_push's `should_split` size check (main.py:1308-1314) would skip the split
    entirely and fall through to a single-file push. This fixture mirrors the
    PROVEN recipe `mkvmerge_split_chunks` uses for its own internal source:
    `color=…:d=6` + `-vf geq=random(1)*255:128:128` + `-c:v libx264 -qp 0`,
    invoked via the RESOLVED ffmpeg binary (so it runs against the same ffmpeg
    production uses, e.g. Emby's bundled binary off PATH). The result is
    incompressible (~60 MB), so a SIZE_MB "10" push splits it into ~3 real chunks
    (num_chunks=ceil(60/10)=6 → ~20 MB/chunk → 3 chunks; main.py:184-194).

    Gating (testing-strategy §4 / §11): skips cleanly via `_ffmpeg_available()`
    (the production resolver) when ffmpeg is genuinely absent, so the suite stays
    green on a binary-less box. `ffmpeg_multichunk_mkv` is intentionally left
    unchanged (other tests rely on its ~50 KB size and `bigsample.mkv` name)."""
    ffmpeg = main.resolve_ffmpeg()  # configured FFMPEG_PATH, else PATH; None if truly absent
    if ffmpeg is None:
        pytest.skip("ffmpeg not available — skipping splittable-master fixture")

    out = tmp_path / "splittable_master.mkv"
    cmd = [
        ffmpeg, "-y", "-f", "lavfi",
        "-i", "color=c=black:s=640x480:d=6:r=25",
        "-vf", "geq=random(1)*255:128:128",
        "-c:v", "libx264", "-qp", "0", "-pix_fmt", "yuv420p",
        str(out),
    ]
    proc = subprocess.run(cmd, capture_output=True)
    if proc.returncode != 0 or not out.exists():
        pytest.skip("ffmpeg invocation failed — skipping splittable-master fixture")

    # Hard guard: the master must live under tmp_path and never touch real C:\Media.
    out_resolved = out.resolve()
    assert tmp_path.resolve() in out_resolved.parents, f"master escaped tmp_path: {out}"
    assert "C:\\Media" not in str(out_resolved), f"master must never touch real C:\\Media: {out}"
    yield out


def _mkvmerge_available():
    """True if a real mkvmerge binary is reachable — EITHER the configured
    main.MKVMERGE_PATH exists on disk OR `mkvmerge` is on PATH. Mirrors the
    real-binary gating of `_ffmpeg_available`; used to skip the deterministic
    real-merge fixture cleanly on machines without MKVToolNix."""
    return os.path.exists(main.MKVMERGE_PATH) or shutil.which("mkvmerge") is not None


@pytest.fixture()
def mkvmerge_split_chunks(ffmpeg_multichunk_mkv, tmp_path):
    """Real-mkvmerge chunk set for the determinism test (Step 9): merging the
    SAME chunks 2–3 times with the same seed must yield an identical SHA256.

    Yields a dict (keys are stable; documented here):
        {"chunks": [Path, ...],  # sorted real .mkv chunk paths, len >= 2, under tmp_path
         "out_dir": Path}        # empty temp dir under tmp_path for merged outputs

    Gating (mirrors the project's real-binary skip pattern, testing-strategy
    §4 / §11): DEPENDS on `ffmpeg_multichunk_mkv` purely to INHERIT its ffmpeg
    skip — that fixture `pytest.skip`s when ffmpeg is unavailable, so this one
    never runs without ffmpeg. On top of that it SKIPS when mkvmerge is absent
    (BOTH main.MKVMERGE_PATH missing AND `mkvmerge` not on PATH). So it runs ONLY
    when both real binaries are present.

    NOTE on the source: it does NOT reuse `ffmpeg_multichunk_mkv`'s *output* MKV.
    That fixture's `testsrc` pattern compresses to ~50 KB (despite its ~6 MB
    docstring claim), and `split_video_file` adds a +10 MB per-chunk buffer, so a
    tiny source can NEVER split into ≥2 chunks. We therefore generate our OWN
    high-entropy (geq random-noise, -qp 0) ~60 MB source here so a real split
    genuinely yields ≥2 chunks. We still depend on `ffmpeg_multichunk_mkv` so the
    ffmpeg skip + "ffmpeg works on this box" guarantee are inherited (it has
    already proven ffmpeg runs by the time this fixture body executes). The
    shared fixture is intentionally left unchanged (other tests rely on its size
    and `bigsample.mkv` name).

    Splits with the REAL main.split_video_file (SIZE_MB "10" on the ~60 MB source
    → 3 chunks, with comfortable keyframe-drift margin) into a fresh `_parts` dir
    under tmp_path. No library I/O — does NOT redirect LIBRARY_*.
    """
    if not _mkvmerge_available():
        pytest.skip("mkvmerge not available — skipping deterministic real-merge fixture")

    # Build a high-entropy (incompressible) multi-MB source so a real split with
    # split_video_file's +10 MB buffer still produces ≥2 chunks. testsrc would
    # compress to a few KB and collapse to a single chunk.
    # ffmpeg is guaranteed present here: this fixture depends on
    # ffmpeg_multichunk_mkv, which already skips if ffmpeg is absent. Use the
    # RESOLVED binary (B1b), not bare "ffmpeg", to match production.
    ffmpeg = main.resolve_ffmpeg()
    src = tmp_path / "det_source.mkv"
    cmd = [
        ffmpeg, "-y", "-f", "lavfi",
        "-i", "color=c=black:s=640x480:d=6:r=25",
        "-vf", "geq=random(1)*255:128:128",
        "-c:v", "libx264", "-qp", "0", "-pix_fmt", "yuv420p",
        str(src),
    ]
    proc = subprocess.run(cmd, capture_output=True)
    if proc.returncode != 0 or not src.exists():
        pytest.skip("ffmpeg invocation failed — skipping deterministic real-merge fixture")

    parts_dir = tmp_path / "_parts"
    parts_dir.mkdir()
    out_dir = tmp_path / "merge_out"
    out_dir.mkdir()

    chunk_strs = main.split_video_file(
        str(src), str(parts_dir), "SIZE_MB", "10", file_id="det01"
    )
    # List by extension + filter on .name — never a bracketed glob ("[id]" is a
    # glob char class on Windows; testing-strategy §8.1 / §9).
    chunks = sorted(p for p in parts_dir.glob("*.mkv") if p.name.endswith(".mkv"))

    assert len(chunks) >= 2, (
        f"determinism fixture needs >=2 real chunks, got {len(chunks)} "
        f"(split returned {len(chunk_strs)})"
    )

    # Hard guard: every yielded path must live under tmp_path — NEVER real C:\Media.
    tmp_resolved = tmp_path.resolve()
    for p in (*chunks, out_dir):
        rp = p.resolve()
        assert tmp_resolved in rp.parents or rp == tmp_resolved, f"path escaped tmp_path: {p}"
        assert "C:\\Media" not in str(rp), f"path must never touch real C:\\Media: {p}"

    yield {"chunks": chunks, "out_dir": out_dir}
