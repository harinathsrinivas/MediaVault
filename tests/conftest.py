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
