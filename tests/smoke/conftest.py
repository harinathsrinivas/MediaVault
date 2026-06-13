r"""Smoke-suite local fixtures (the fast full-command pre-PR gate, IMP-H3 / B1).

These helpers are SMOKE-SPECIFIC ONLY. The cross-cutting mocking philosophy lives
in tests/conftest.py and is inherited automatically (tests/smoke sits below tests/):
  - library boundary  -> `sandbox` / `sandbox_alias`   (never real library_*.json / C:\Media)
  - ADB data round-trip -> `mock_device`               (stateful fake device)
  - ADB protocol/seq    -> `FakeAdb`                    (recorder, in test_cmd_push_partial.py)
  - browser/fetch       -> `mock_fetch`                 (no Selenium / real browser)
  - ffmpeg dummy        -> `fake_dummy`                 (cmd_replace / cmd_repair_dummies)
  - MediaInfo           -> `stub_tech_specs`            (cmd_prep deep scan)
  - real ffmpeg/mkvmerge -> `_ffmpeg_available()` / `_mkvmerge_available()` skip gates

What this file adds:
  - `smoke_local_root` : patches main.LOCAL_ROOT + mvcommon.LOCAL_ROOT to the sandbox
    Media tree so the two whole-tree WALKERS (cmd_scan_unprepped, cmd_recover --scan)
    walk the temp dir instead of the real C:\Media. The `sandbox` fixture redirects the
    three LIBRARY_* constants but NOT LOCAL_ROOT, so without this the walkers would
    read real C:\Media\{Movies,Series,Anime}. Hard-guards against C:\Media.
  - `make_video`     : factory writing a tiny but >DUMMY_MAX_BYTES .mkv (so cmd_check /
    cmd_prep / cmd_restore treat it as REAL media, not an early-skipped dummy).
  - `seed_split_parts`: pre-seeds a _parts/ chunk dir + writes split_info into a library
    entry so cmd_push takes the RESUME branch (main.py:1299) — NO real split / mkvmerge.
    This is the speed lever the plan mandates (prefer pre-seeded _parts/ over real splits).

DUMMY_MAX_BYTES note (the gotcha called out in the step): files < 200_000 bytes are
treated by cmd_check / cmd_restore / cmd_prep as already-archived dummies and EARLY-SKIP.
`make_video` deliberately writes ~250 KB so the real-media path is exercised; the one
place we WANT the dummy path (cmd_repair_dummies regenerating an archived dummy) writes
a <200 KB file on purpose and asserts the regenerate path — both are valid smokes.
"""
import hashlib
import os

import pytest

import main
import mvcommon

# A tiny payload, repeated, that comfortably clears DUMMY_MAX_BYTES (200_000) so
# the "real media" code paths (hash check, prep, restore) run instead of the
# dummy early-skip. ~250 KB keeps hashing fast while staying well over the limit.
_REAL_MEDIA_BYTES = b"SMOKE-REAL-MEDIA-MASTER\n" * 11000  # ~264 KB > 200_000


@pytest.fixture()
def smoke_local_root(sandbox, monkeypatch):
    """Redirect LOCAL_ROOT (BOTH bindings) to the sandbox Media tree.

    cmd_scan_unprepped (main.py:2459-2461) and cmd_recover(scan=True) (main.py:738)
    build their walk roots from LOCAL_ROOT/{Movies,Series,Anime}. The sandbox fixture
    only patches LIBRARY_*, so those walkers would otherwise read the real C:\\Media.
    Like the LIBRARY_* dual-patch (the IMP-A1 binding hazard), LOCAL_ROOT is imported
    by value into main, so patch BOTH mvcommon.LOCAL_ROOT and main.LOCAL_ROOT.

    sandbox["media_dir"] is tmp/Media/Movies/TestMovie, so its parent.parent is the
    tmp Media root. Yields that Path (already created by the sandbox fixture).
    """
    media_root = sandbox["media_dir"].parent.parent  # tmp_path / "Media"
    assert "C:\\Media" not in str(media_root), \
        f"Safety check failed: smoke LOCAL_ROOT still points at real media: {media_root}"
    monkeypatch.setattr(mvcommon, "LOCAL_ROOT", str(media_root))
    monkeypatch.setattr(main, "LOCAL_ROOT", str(media_root))
    yield media_root


@pytest.fixture()
def make_video():
    """Factory: write a tiny (~264 KB) .mkv that clears DUMMY_MAX_BYTES.

    Returns write(path, marker=b"") -> (Path, sha256_hex). Deterministic bytes so
    the caller can store the returned hash in a library entry and have cmd_check /
    cmd_restore verify it. `marker` (optional) is prepended so two files can differ.
    """
    def write(path, marker=b""):
        path = str(path)
        data = marker + _REAL_MEDIA_BYTES
        with open(path, "wb") as f:
            f.write(data)
        assert os.path.getsize(path) > main.DUMMY_MAX_BYTES, \
            "make_video must write > DUMMY_MAX_BYTES so the real-media path is taken"
        return path, hashlib.sha256(data).hexdigest()

    return write


@pytest.fixture()
def seed_split_parts():
    """Factory: pre-seed a _parts/ chunk dir AND return split_info chunk metadata.

    Lets the split-push smoke RESUME from existing chunks (main.py:1299) instead of
    invoking a real split (no ffmpeg/mkvmerge on the hot path — the plan's speed lever).
    The chunk hashes are placeholders; that is fine because PUSH_VERIFY_REMOTE is False
    by default, so cmd_push never hashes the chunks on the push path.

    Returns seed(media_dir, short_id, base_name, n_chunks=3) -> dict:
        {"parts_dir": Path, "chunk_names": [str, ...], "split_info": {...}}
    The chunk names follow the real ".chunk.NNN.mkv" convention so the `chunks 1-N`
    range filter (which parses that pattern at main.py:1448) works in the smoke.
    """
    def seed(media_dir, short_id, base_name, n_chunks=3):
        parts_dir = media_dir / main.SPLIT_DIR_NAME
        parts_dir.mkdir(exist_ok=True)
        stem = os.path.splitext(base_name)[0]
        chunk_names = [f"{stem} [{short_id}].chunk.{i:03d}.mkv" for i in range(1, n_chunks + 1)]
        chunks_meta = []
        for i, cn in enumerate(chunk_names, start=1):
            (parts_dir / cn).write_bytes(f"smoke-chunk-{i}-bytes".encode())
            chunks_meta.append({"filename": cn, "hash": f"smokehash{i}"})
        split_info = {
            "is_split": True,
            "method": "SIZE_MB",
            "val": "8000",
            "total_chunks": n_chunks,
            "chunks": chunks_meta,
        }
        return {"parts_dir": parts_dir, "chunk_names": chunk_names, "split_info": split_info}

    return seed
