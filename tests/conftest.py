import shutil
import hashlib
import subprocess
import sys, os, json
import pytest
import main      # repo root must be on sys.path
import mvcommon  # authoritative home of LIBRARY_* + load_library/save_library

# Ensure repo root is importable (for pytest invoked from any CWD)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

FAKE_ORIGINAL_BYTES = b"ORIGINAL-MASTER-BYTES"
FAKE_DUMMY_BYTES    = b"DUMMY"

TEST_ENTRY_ID = "mov_test_c9_001"  # "mov" prefix -> goes to LIBRARY_MOVIES


@pytest.fixture()
def sandbox(tmp_path, monkeypatch):
    """
    Redirects all three LIBRARY_* constants to sandbox JSON files,
    creates the required directories, and hard-guards against real C:\\Media.

    Yields: dict with keys:
        media_dir    - Path: sandbox folder holding media files
        lib_movies   - Path: sandbox LIBRARY_MOVIES json
        lib_series   - Path: sandbox LIBRARY_SERIES json
        lib_anime    - Path: sandbox LIBRARY_ANIME json
    """
    media_dir = tmp_path / "Media" / "Movies" / "TestMovie"
    media_dir.mkdir(parents=True)

    lib_dir = tmp_path / "library"
    lib_dir.mkdir()

    lib_movies = lib_dir / "library_movies.json"
    lib_series = lib_dir / "library_series.json"
    lib_anime  = lib_dir / "library_anime.json"

    # Hard guard: fail immediately if any constant still points under C:\Media.
    # After the mvcommon extraction, load_library/save_library read mvcommon's
    # OWN module-level LIBRARY_* bindings, so mvcommon is the authoritative patch
    # target. main imported the names by value (a separate binding), so we patch
    # both mvcommon and main to keep every reader pointed at the sandbox.
    for attr, path in [
        ("LIBRARY_MOVIES", str(lib_movies)),
        ("LIBRARY_SERIES", str(lib_series)),
        ("LIBRARY_ANIME",  str(lib_anime)),
    ]:
        assert "C:\\Media" not in path, f"Safety check failed: {attr} still points to real media!"
        monkeypatch.setattr(mvcommon, attr, path)
        monkeypatch.setattr(main, attr, path)

    yield {
        "media_dir":  media_dir,
        "lib_movies": lib_movies,
        "lib_series": lib_series,
        "lib_anime":  lib_anime,
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

    monkeypatch.setattr(main.subprocess, "run", fake_run)
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
        def _merge(chunk_paths, output_path):
            state["n"] += 1
            if mode == "raise":
                raise RuntimeError("simulated mkvmerge crash")
            return False

        monkeypatch.setattr(main, "merge_video_files", _merge)
        return state

    return install


def _ffmpeg_available():
    """True if an ffmpeg binary is callable. Used to gate the real-split
    fixture so machines without ffmpeg skip those tests cleanly."""
    import shutil as _sh
    return _sh.which("ffmpeg") is not None


@pytest.fixture()
def ffmpeg_multichunk_mkv(tmp_path):
    """ffmpeg-generated multi-MB MKV for tests that need a GENUINE split (i.e.
    they exercise the real split_video_file path rather than a pre-seeded
    _parts/ folder). Skips cleanly when ffmpeg is absent so the suite stays
    green on machines without it (docs/testing-strategy.md §4 / §11).

    Generates a ~6 MB testsrc MKV at tmp_path. Yields its Path. The caller pairs
    it with a small split target (e.g. SIZE_MB 2) to force multiple chunks.
    Never writes under real C:\\Media."""
    if not _ffmpeg_available():
        pytest.skip("ffmpeg not available — skipping real-split fixture")

    out = tmp_path / "bigsample.mkv"
    # testsrc at a modest resolution/duration produces a few MB of real MKV.
    cmd = [
        "ffmpeg", "-y", "-f", "lavfi",
        "-i", "testsrc=duration=8:size=640x480:rate=25",
        "-c:v", "libx264", "-pix_fmt", "yuv420p",
        str(out),
    ]
    proc = subprocess.run(cmd, capture_output=True)
    if proc.returncode != 0 or not out.exists():
        pytest.skip("ffmpeg invocation failed — skipping real-split fixture")
    yield out
