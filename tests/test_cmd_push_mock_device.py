"""Push integration tests using the stateful MockDevice fixture.

Unlike test_cmd_push_partial.py (which uses FakeAdb to verify the *protocol* —
correct adb command ordering, .partial naming, mvmeta sequencing), these tests
verify that files actually land on the "device" with the right content and names.
They catch regressions in path construction, constant imports, and the
.partial -> final rename leaving a clean device state.

No real device. No real C:\\Media. No real library_*.json.
"""
import json
import main
import mvcommon


# ---------------------------------------------------------------------------
# Fixture: a pre-split library entry (3 chunks ready in _parts/)
# Reuses the same shape as test_cmd_push_partial.py's split_entry fixture but
# is defined here independently so this file can run standalone.
# ---------------------------------------------------------------------------
ENTRY_ID = "mov_test_device_001"


def _make_split_entry(sandbox):
    media_dir = sandbox["media_dir"]
    short_id = "dev001"
    base = "test_device.mkv"

    (media_dir / base).write_bytes(b"master-file-bytes")

    parts_dir = media_dir / main.SPLIT_DIR_NAME
    parts_dir.mkdir()
    chunk_names = [f"test_device [{short_id}].chunk.00{i}.mkv" for i in (1, 2, 3)]
    chunks_meta = []
    for i, cn in enumerate(chunk_names, start=1):
        data = f"chunk-{i}-content".encode()
        (parts_dir / cn).write_bytes(data)
        chunks_meta.append({"filename": cn, "hash": f"fakehash{i}"})

    entry = {
        ENTRY_ID: {
            "short_id": short_id,
            "filename": base,
            "folder_path": str(media_dir),
            "status": "local_ready",
            "uploaded": False,
            "hash": "masterhash",
            "metadata": {"title": "Test Device Movie", "year": 2024},
            "tech_spec": {"resolution": "1080p"},
            "split_info": {
                "is_split": True,
                "method": "SIZE_MB",
                "val": "8000",
                "total_chunks": 3,
                "chunks": chunks_meta,
            },
        }
    }
    sandbox["lib_movies"].write_text(json.dumps(entry), encoding="utf-8")
    sandbox["lib_series"].write_text("{}", encoding="utf-8")
    sandbox["lib_anime"].write_text("{}", encoding="utf-8")
    return {"entry_id": ENTRY_ID, "media_dir": media_dir, "parts_dir": parts_dir,
            "chunk_names": chunk_names}


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_chunks_land_on_device_at_final_names(sandbox, mock_device):
    """All 3 chunks appear on the mock device under their final (non-.partial) names."""
    entry = _make_split_entry(sandbox)
    result = main.cmd_push(entry["entry_id"])

    assert result is True

    on_device = list(mock_device.rglob("*.mkv"))
    chunks = [f for f in on_device if ".chunk." in f.name]
    assert len(chunks) == 3, f"expected 3 chunks on device, got: {[f.name for f in on_device]}"
    for f in chunks:
        assert not f.name.endswith(main.PARTIAL_SUFFIX), \
            f".partial file still on device after successful push: {f.name}"


def test_no_partial_files_remain_after_success(sandbox, mock_device):
    """.partial files are cleaned up — G1 atomic rename completed for every chunk."""
    entry = _make_split_entry(sandbox)
    main.cmd_push(entry["entry_id"])

    partials = list(mock_device.rglob(f"*{main.PARTIAL_SUFFIX}"))
    assert partials == [], f"orphaned .partial files on device: {[f.name for f in partials]}"


def test_chunk_content_matches_local_source(sandbox, mock_device):
    """Bytes on the device match what was in the local _parts/ folder (no corruption)."""
    entry = _make_split_entry(sandbox)
    # Record expected content before push (cmd_push deletes local chunks on success)
    expected = {
        cn: (entry["parts_dir"] / cn).read_bytes()
        for cn in entry["chunk_names"]
    }

    main.cmd_push(entry["entry_id"])

    # rglob("*.mkv") avoids treating [...] in chunk names as glob character classes.
    device_files = {f.name: f for f in mock_device.rglob("*.mkv")}
    for chunk_name, original_bytes in expected.items():
        assert chunk_name in device_files, f"chunk not found on device: {chunk_name}"
        assert device_files[chunk_name].read_bytes() == original_bytes, \
            f"byte mismatch on device for {chunk_name}"


def test_library_marked_uploaded_after_push(sandbox, mock_device):
    """Library entry is flipped to uploaded=True, status=onboarded after success."""
    entry = _make_split_entry(sandbox)
    main.cmd_push(entry["entry_id"])

    library = mvcommon.load_library()
    assert library[ENTRY_ID]["uploaded"] is True
    assert library[ENTRY_ID]["status"] == "onboarded"


def test_local_chunks_deleted_after_push(sandbox, mock_device):
    """Local _parts/ chunks are removed once successfully uploaded (saves disk)."""
    entry = _make_split_entry(sandbox)
    main.cmd_push(entry["entry_id"])

    remaining = list(entry["parts_dir"].glob("*.mkv")) if entry["parts_dir"].exists() else []
    assert remaining == [], f"local chunks not cleaned up: {[f.name for f in remaining]}"


def test_constants_from_mvcommon_are_used(sandbox, mock_device):
    """Smoke-test that SPLIT_DIR_NAME / PARTIAL_SUFFIX from mvcommon still route
    correctly after the IMP-A1 extraction — if the import broke silently the
    _parts/ folder would not be found and push would re-split (or fail)."""
    entry = _make_split_entry(sandbox)
    # _parts/ folder is pre-populated; cmd_push must take the RESUME path
    # (finds existing chunks) rather than trying to split again.
    result = main.cmd_push(entry["entry_id"])

    # Resume path succeeded -> constants resolved correctly from mvcommon.
    assert result is True
    chunks = [f for f in mock_device.rglob("*.mkv") if ".chunk." in f.name]
    assert len(chunks) == 3
