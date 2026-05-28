import sys, os, json
import pytest
import main  # repo root must be on sys.path

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

    # Hard guard: fail immediately if any constant still points under C:\Media
    for attr, path in [
        ("LIBRARY_MOVIES", str(lib_movies)),
        ("LIBRARY_SERIES", str(lib_series)),
        ("LIBRARY_ANIME",  str(lib_anime)),
    ]:
        assert "C:\\Media" not in path, f"Safety check failed: {attr} still points to real media!"
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
