# MediaVault Testing Strategy

## 1. Philosophy

Mock at the I/O boundary — never inside application logic. A test failure means
the application broke, not that a mock was subtly wrong.

Two rules that override everything else:
- **Never touch `C:\Media`** or real `library_*.json` in any test, ever.
- **Never issue real `adb` commands** or open a real browser in any test, ever.

The `sandbox` fixture's hard-guard and the `mock_device` fixture enforce this
automatically. If you bypass either fixture, you are doing it wrong.

---

## 2. Test pyramid

```
                     ┌─────────────────────┐
                     │       MANUAL        │  Real device, Google Photos,
                     │   (pre-release)     │  full GB-scale push/restore
                     └─────────────────────┘
                   ┌───────────────────────────┐
                   │       SMOKE SUITE         │  tests/smoke/ — every command +
                   │   (pre-PR gate)           │  major option; tiny fixtures;
                   │   pytest tests/smoke -q   │  sandbox_alias; ~<30 s
                   └───────────────────────────┘
                   ┌───────────────────────────┐
                   │       INTEGRATION         │  mock_device (push round-trip)
                   │   tests/test_cmd_push_    │  mock_fetch  (fetch round-trip)
                   │   mock_device.py          │  sandbox + FakeAdb combined
                   └───────────────────────────┘
                 ┌─────────────────────────────────┐
                 │         COMMAND TESTS            │  FakeAdb recorder (protocol)
                 │  test_cmd_push_partial.py        │  sandbox (library I/O)
                 │  test_cmd_replace.py             │  fake_dummy (ffmpeg stub)
                 │  test_cmd_restore_quarantine.py  │
                 └─────────────────────────────────┘
               ┌───────────────────────────────────────┐
               │            UNIT / MODULE               │  Pure functions, no I/O
               │   test_mvcommon.py                     │  No fixtures needed
               └───────────────────────────────────────┘
```

Go as low in the pyramid as possible. Only move up when you genuinely need
a boundary (ADB, browser, library file) to be exercised.

---

## 3. The three mock boundaries

```
  ┌─────────────────────────────────────────────────────────────────┐
  │                        TEST PROCESS                             │
  │                                                                 │
  │  ┌─────────────┐    ┌──────────────────┐    ┌───────────────┐  │
  │  │   Library   │    │    ADB / device   │    │ Browser/fetch │  │
  │  │  boundary   │    │    boundary       │    │   boundary    │  │
  │  │             │    │                  │    │               │  │
  │  │  sandbox    │    │  mock_device  OR │    │  mock_fetch   │  │
  │  │  fixture    │    │  FakeAdb         │    │  fixture      │  │
  │  │  redirects  │    │  recorder        │    │  (future C2)  │  │
  │  │  LIBRARY_*  │    │  intercepts      │    │  monkeypatches│  │
  │  │  → tmp_path │    │  subprocess.run  │    │  trigger_     │  │
  │  └─────────────┘    └──────────────────┘    │  download     │  │
  │                                             └───────────────┘  │
  │  Application code (main.py, mainfetch.py, mvcommon.py)         │
  │  runs REAL — only I/O is redirected                            │
  └─────────────────────────────────────────────────────────────────┘
```

| Boundary | Mock tool | What stays real |
|---|---|---|
| Library JSON files | `sandbox` fixture | `load_library`, `save_library`, all JSON parsing |
| Android device (ADB) | `mock_device` (stateful) or `FakeAdb` (recorder) | File I/O, path math, library updates |
| Browser / Selenium | `mock_fetch` fixture (future) | Restore logic, hash verification |

---

## 4. Fixture catalogue

### 4.1 `sandbox` — library file redirect

**File:** `tests/conftest.py`
**Use for:** Any test that calls `load_library`, `save_library`, or any `cmd_*`
that touches the library.

Redirects all three `LIBRARY_*` constants to temp-dir JSON files and hard-guards
against `C:\Media`. After IMP-A1, patches **both** `mvcommon.LIBRARY_*` (where
`load_library`/`save_library` read) and `main.LIBRARY_*` (where `cmd_*` imported
by value). Both must be patched — patching only one silently hits the real files.

```python
def test_something(sandbox):
    # sandbox["lib_movies"]  -> Path to tmp library_movies.json
    # sandbox["lib_series"]  -> Path to tmp library_series.json
    # sandbox["lib_anime"]   -> Path to tmp library_anime.json
    # sandbox["media_dir"]   -> Path: tmp_path/Media/Movies/TestMovie
    sandbox["lib_movies"].write_text('{"mov-test": {...}}', encoding="utf-8")
    library = mvcommon.load_library()  # reads from sandbox, not C:\Media
```

### 4.2 `sandbox_entry` — pre-seeded library entry

**File:** `tests/conftest.py`
**Use for:** Tests that need a real-looking library entry without building it manually.
Extends `sandbox` with a fake media file and a populated `LIBRARY_MOVIES` entry.

```python
def test_replace(sandbox_entry):
    # sandbox_entry["entry_id"]  -> "mov_test_c9_001"
    # sandbox_entry["orig_path"] -> Path to the fake .mkv file
    # sandbox_entry["media_dir"] -> Path to the media folder
    result = main.cmd_replace(sandbox_entry["entry_id"])
    assert result is True
```

### 4.3 `fake_dummy` — ffmpeg stub

**File:** `tests/conftest.py`
**Use for:** Tests involving `cmd_replace` that would otherwise need ffmpeg.
Replaces `main.make_video_dummy` with a stub that writes `FAKE_DUMMY_BYTES`.

### 4.4 `mock_device` — stateful fake Android device

**File:** `tests/conftest.py`
**Use for:** Integration tests that verify files actually reach the "device" —
data integrity, content correctness, partial/final rename state.

Intercepts `main.subprocess.run` for every `adb` argv and executes it against
`tmp_path/device/` on the local filesystem:

| ADB command | Real action | Mock action |
|---|---|---|
| `adb push [-p] <local> <remote>` | Upload via USB | `shutil.copy2(local, device_dir / remote.lstrip("/"))` |
| `adb shell mv '<src>' '<dst>'` | Atomic rename on device | `os.rename` within `device_dir` |
| `adb shell rm '<path>'` | Delete on device | `os.unlink` from `device_dir` |
| `adb shell mkdir -p '<path>'` | Create dir on device | `os.makedirs` inside `device_dir` |
| `adb shell md5sum '<path>'` | Hash on device | Compute md5 of file in `device_dir`, stdout `"HASH  path\n"` |
| `adb devices` | List attached devices | `"List of devices attached\nfake123\tdevice\n"` |

Yields `device_dir` (a `pathlib.Path`). Inspect with `device_dir.rglob("*.mkv")`.

```python
def test_push_lands_on_device(sandbox, mock_device):
    # ... seed sandbox with a split entry ...
    result = main.cmd_push(entry_id)
    assert result is True
    # Files actually copied into device_dir
    chunks = [f for f in mock_device.rglob("*.mkv") if ".chunk." in f.name]
    assert len(chunks) == 3
```

**Do not use for:** Protocol/sequencing tests (use `FakeAdb` instead — lighter).

### 4.5 `FakeAdb` recorder — call sequence verifier

**File:** `tests/test_cmd_push_partial.py` (defined inline, not in conftest)
**Use for:** Verifying the exact sequence of ADB commands — `.partial` naming,
`adb shell mv` ordering, mvmeta sidecar, failure-at-Nth-chunk.
Does NOT copy any files. Use when you care about WHAT commands were issued,
not WHAT data landed on the device.

```python
def test_partial_then_mv_protocol(split_entry, monkeypatch):
    fake = FakeAdb()
    monkeypatch.setattr(main.subprocess, "run", fake.run)
    main.cmd_push(entry_id)
    assert fake.chunk_pushes()[0][-1].endswith(main.PARTIAL_SUFFIX)
    assert len(fake.mvs()) == len(fake.chunk_pushes())
```

### 4.6 `mock_fetch` — browser download stub *(future — implement in C2)*

**File:** `tests/conftest.py` (to be added during C2)
**Use for:** End-to-end fetch → restore tests without Selenium or a real browser.
Monkeypatches `mainfetch.trigger_download` to copy a pre-seeded file from
`mock_device` (or any temp dir) to the local restore directory.

```python
@pytest.fixture()
def mock_fetch(mock_device, tmp_path, monkeypatch):
    import shutil, mainfetch
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
```

### 4.7 `sandbox_alias` — alias + season_map entry seed

**File:** `tests/conftest.py`
**Use for:** Tests that exercise alias resolution, multi_ep_alias handling, or any
whole-library iteration that must stay alias/season_map-safe.

Seeds the sandbox with a three-node structure: a `season_map` parent entry, a leaf
`primary` entry, and a `multi_ep_alias` pointing at the leaf. Used by alias
regression tests and by the smoke suite's alias-sweep checks.

```python
def test_alias_does_not_crash(sandbox_alias):
    # sandbox_alias["season_id"]   -> "tv-en-2023-show"  (season_map entry)
    # sandbox_alias["primary_id"]  -> "tv-en-2023-show-s01e01"
    # sandbox_alias["alias_id"]    -> "tv-en-2023-show-s01e01e02"  (multi_ep_alias)
    lib = mvcommon.load_library()
    # Iterating must not KeyError on the alias entry
    for eid, entry in lib.items():
        resolved = main._resolve_alias(lib, eid, entry)
        assert resolved is not None
```

### 4.8 `ENTRY_TYPE_KEYS` registry + `test_entry_schema_guard.py`

`main.py` declares `ENTRY_TYPE_KEYS` — the canonical set of library entry-type
strings and shared data-field names. `tests/test_entry_schema_guard.py` asserts
that this registry matches reality (all known types are listed; no stale names).

**Rule:** any change that adds, renames, or removes a library entry type or a
shared field key **must** update `ENTRY_TYPE_KEYS`. Every whole-library iterator
must either call `_resolve_alias(lib, eid, entry)` or explicitly skip entries
where `entry.get("type") == "multi_ep_alias"` — otherwise it will crash or
silently miscount on alias entries.

Run the guard test in isolation:

```powershell
pytest tests/test_entry_schema_guard.py -q
```

### 4.9 `sandbox_extras` — extras-bearing movie title seed (IMP-D19)

**File:** `tests/conftest.py`
**Use for:** Tests that exercise the extras lifecycle (scan/merge,
`push_title_extras`, `replace_title_extras`, `restore_title_extras`,
`add_extras`, `--fetchExtras`) or any command that must tolerate an
extras-bearing library.

Extends `sandbox` (inheriting the dual LIBRARY_*/LOCAL_ROOT patch and the
`C:\Media` hard-guard — it patches nothing itself). Seeds ONE movie-leaf title
(`mov-en-2024-extrasmovie`, cmd_prep-faithful key set) into
`library_movies.json` whose folder is `sandbox["media_dir"]`, carrying a nested
`extras` block with one group `"Specials"` of two items in the pre-push state
(`status="local_ready"`, `uploaded=False`), plus three REAL files on disk
(`make_video`, each > `DUMMY_MAX_BYTES`): the movie's own main `.mkv` and the
two extras under the `Specials/` subfolder — every stored `hash` matching the
real bytes (the canonical `calculate_file_hash` sha256). Item fields mirror
`scan_extras_folders` (same provenance as the canonical `_extras_block` in
`tests/test_entry_schema_guard.py`); the disk layout matches
`main._extras_item_paths`. The exact yield shape is documented in the fixture
docstring. For a season_map-titled extras seed, see the smoke suite's
`_seed_title_with_extras` helper (Step 11).

```python
def test_extras_push(sandbox_extras, mock_device):
    # sandbox_extras["title_id"]   -> "mov-en-2024-extrasmovie" (movie leaf)
    # sandbox_extras["media_dir"]  -> Path: title folder
    # sandbox_extras["orig_path"]  -> Path: the movie's own main .mkv
    # sandbox_extras["group_rel"]  -> "Specials"
    # sandbox_extras["extras_dir"] -> Path: media_dir / "Specials"
    # sandbox_extras["items"]      -> 2 dicts (sorted by sub_rel):
    #                                 {"sub_rel","filename","path","hash","short_id"}
    # sandbox_extras["sandbox"]    -> the underlying sandbox dict
    library = mvcommon.load_library()
    main.push_title_extras(library, sandbox_extras["title_id"], None)
    # chunk/pushed names carry "[<short_id>]" — rglob("*.mkv") + filter by
    # .name (never a bracketed pattern; see §8.1)
```

---

## 5. Fixture selection decision tree

```
Does the test touch library JSON (load_library / save_library / any cmd_*)?
  YES ──► use sandbox
  NO  ──► skip sandbox

Does the test issue adb commands?
  NO  ──► no ADB mock needed (pure unit test)
  YES ──► Do you care about WHAT data landed on the device?
            YES ──► use mock_device (stateful)
            NO  ──► use FakeAdb recorder (protocol only)

Does the test need Selenium / browser / trigger_download?
  YES ──► use mock_fetch (once implemented in C2)
  NO  ──► no browser mock needed

Does the test touch conftest.py itself (new fixture, binding patch)?
  YES ──► assign to [model: opus] in the plan (binding hazard risk)
  NO  ──► [model: sonnet] is fine
```

---

## 6. Mock flow diagrams

### 6.1 Push round-trip with `mock_device`

```
Test: test_chunks_land_on_device_at_final_names(sandbox, mock_device)
│
├─ sandbox fixture patches:
│    mvcommon.LIBRARY_MOVIES → tmp/library/library_movies.json
│    mvcommon.LIBRARY_SERIES → tmp/library/library_series.json
│    mvcommon.LIBRARY_ANIME  → tmp/library/library_anime.json
│    main.LIBRARY_MOVIES     → (same) ← both must be patched
│
├─ mock_device fixture patches:
│    main.subprocess.run → fake_run()
│    device_dir = tmp/device/
│
└─ main.cmd_push(entry_id)
     │
     ├─ load_library()          reads tmp/library/*.json   ✓ sandbox
     ├─ subprocess.run(["adb","shell","mkdir",...])
     │    └─ fake_run() → device_dir/sdcard/.../mkdir     ✓ mock_device
     ├─ subprocess.run(["adb","push","-p", local, remote+".partial"])
     │    └─ fake_run() → shutil.copy2(local, device_dir/remote+".partial")
     ├─ subprocess.run(["adb","shell","mv", partial, final])
     │    └─ fake_run() → os.rename(device_dir/partial, device_dir/final)
     ├─ os.remove(local_chunk)  deletes local _parts/ file  ✓ real filesystem
     └─ save_library()          writes tmp/library/*.json   ✓ sandbox

Test assertions:
  device_dir.rglob("*.mkv")   → 3 files, none ending in .partial  ✓
  mvcommon.load_library()      → entry["uploaded"] is True         ✓
  parts_dir.exists()           → False (cleaned up)                ✓
```

### 6.2 Fetch round-trip with `mock_fetch` *(future)*

```
Test: test_fetch_then_restore_round_trip(sandbox, mock_device, mock_fetch)
│
├─ Setup: seed mock_device with chunk files (simulating a prior push)
│
├─ mock_fetch patches mainfetch.trigger_download:
│    query "chunk.001" → copies device_dir/**/*chunk.001* → restore_dir/
│
├─ (call fetch logic — driver=None, mock bypasses Selenium)
│
├─ restore_dir now has chunk files
│
├─ main.cmd_restore(entry_id)
│    ├─ finds chunks in restore_dir
│    ├─ verifies hashes against split_info
│    └─ mkvmerge merges → output file  (or fake_merge stub)
│
└─ assert output file exists and hash matches original
```

### 6.3 Library binding after IMP-A1 (THE binding hazard)

```
BEFORE IMP-A1 (wrong mental model — DO NOT use):
  monkeypatch.setattr(main, "LIBRARY_MOVIES", tmp_path)
  main.load_library()  ← read main.LIBRARY_MOVIES  ✓ (was correct before)

AFTER IMP-A1 (correct):
  monkeypatch.setattr(mvcommon, "LIBRARY_MOVIES", tmp_path)  ← REQUIRED
  monkeypatch.setattr(main, "LIBRARY_MOVIES", tmp_path)      ← ALSO required
  mvcommon.load_library()  ← reads mvcommon.LIBRARY_MOVIES   ✓

WHY both? `from mvcommon import LIBRARY_MOVIES` in main.py creates a NEW binding
in the main module namespace. After import, main.LIBRARY_MOVIES and
mvcommon.LIBRARY_MOVIES are TWO SEPARATE NAMES pointing at the same string.
Patching one does not patch the other. load_library/save_library live in mvcommon
and read mvcommon's names, so mvcommon must be the primary patch target.
The sandbox fixture already handles this correctly — never bypass it.
```

---

## 7. Complete examples by layer

### 7.1 Unit test (no fixtures)

```python
# tests/test_mvcommon.py
def test_human_readable_size_gb():
    assert mvcommon.human_readable_size(2 * 1024**3) == "2.00 GB"

def test_parse_size_str_mb():
    assert mvcommon.parse_size_str("500MB") == 500 * 1024 * 1024

def test_generate_short_id_length():
    sid = mvcommon.generate_short_id("mov-en-2024-inception-4k-bluray")
    assert len(sid) == 8  # first 8 chars of sha256
```

### 7.2 Library round-trip test (sandbox only)

```python
# tests/test_mvcommon.py
def test_round_trip(sandbox):
    data = {
        "mov-en-2024-dune2": {"status": "local_ready", "uploaded": False},
        "tv-en-2022-houseofdragon": {"status": "archived", "uploaded": True},
    }
    mvcommon.save_library(data)
    assert mvcommon.load_library() == data
```

### 7.3 ADB protocol test (FakeAdb recorder)

```python
# tests/test_cmd_push_partial.py
def test_each_chunk_uses_partial_then_mv(split_entry, monkeypatch):
    fake = FakeAdb()
    monkeypatch.setattr(main.subprocess, "run", fake.run)
    main.cmd_push(split_entry["entry_id"])
    for push, mv in zip(fake.chunk_pushes(), fake.mvs()):
        assert push[-1].endswith(main.PARTIAL_SUFFIX)
        assert mv[-2].strip("'") == push[-1]   # mv src == push dest
        assert mv[-1].strip("'") == push[-1].removesuffix(main.PARTIAL_SUFFIX)
```

### 7.4 ADB data-integrity test (mock_device)

```python
# tests/test_cmd_push_mock_device.py
def test_chunk_bytes_match_local_source(sandbox, mock_device):
    entry = _make_split_entry(sandbox)
    # capture before push (cmd_push deletes local chunks on success)
    expected = {cn: (entry["parts_dir"] / cn).read_bytes()
                for cn in entry["chunk_names"]}
    main.cmd_push(entry["entry_id"])
    # index by name — avoids [...] glob metachar problem
    on_device = {f.name: f for f in mock_device.rglob("*.mkv")}
    for name, original in expected.items():
        assert name in on_device
        assert on_device[name].read_bytes() == original
```

### 7.5 Progress bar / stdout test

```python
# tests/test_mvcommon.py
def test_calculate_file_hash_progress_bar(tmp_path, capsys):
    f = tmp_path / "sample.mkv"
    f.write_bytes(b"x" * 300_000)   # large enough for multiple block reads
    h = mvcommon.calculate_file_hash(str(f))
    out = capsys.readouterr().out
    assert h is not None and len(h) == 64
    assert "🔍" in out
    assert "█" in out
```

### 7.6 Failure-contract test (FakeAdb + sandbox)

```python
def test_push_failure_does_not_flip_library(split_entry, monkeypatch):
    fake = FakeAdb(fail_push_n=1)
    monkeypatch.setattr(main.subprocess, "run", fake.run)
    result = main.cmd_push(split_entry["entry_id"])
    assert result is False
    lib = mvcommon.load_library()
    assert lib[split_entry["entry_id"]]["uploaded"] is False
```

---

## 8. Anti-patterns

### 8.1 rglob with `[...]` in the pattern — SILENT FAILURE

`pathlib.rglob(pattern)` treats `[`, `]` as glob character classes. MediaVault
filenames contain `[short_id]` (e.g. `movie [abc123].chunk.001.mkv`). Searching
for them directly silently returns no matches.

```python
# WRONG — [abc123] is treated as a glob class, matches no files
device_dir.rglob("movie [abc123].chunk.001.mkv")

# CORRECT — match by extension, filter by .name
files = {f.name: f for f in device_dir.rglob("*.mkv")}
assert "movie [abc123].chunk.001.mkv" in files
```

### 8.2 Patching only one binding after IMP-A1

```python
# WRONG — load_library reads mvcommon's binding, not main's
monkeypatch.setattr(main, "LIBRARY_MOVIES", str(tmp_path / "lib.json"))

# CORRECT — patch both
monkeypatch.setattr(mvcommon, "LIBRARY_MOVIES", str(tmp_path / "lib.json"))
monkeypatch.setattr(main,     "LIBRARY_MOVIES", str(tmp_path / "lib.json"))
# (The sandbox fixture already does this — use sandbox, don't DIY.)
```

### 8.3 Asserting on a specific device path

```python
# WRONG — the remote path depends on LOCAL_ROOT and the temp dir prefix,
# which changes per pytest run
assert (device_dir / "sdcard/Media/Movies/TestMovie/file.mkv").exists()

# CORRECT — search by name, don't assume path
files = list(device_dir.rglob("file.mkv"))
assert len(files) == 1
```

### 8.4 Creating a new `load_library` mock instead of using sandbox

```python
# WRONG — duplicates fixture logic, misses binding hazard
monkeypatch.setattr(mvcommon, "load_library", lambda: {"fake": {}})

# CORRECT — use sandbox and write your test data there
sandbox["lib_movies"].write_text(json.dumps({"mov-test": {...}}), encoding="utf-8")
library = mvcommon.load_library()
```

### 8.5 Testing with real files

```python
# WRONG — ever
f = pathlib.Path(r"C:\Media\Movies\Inception\inception.mkv")

# CORRECT — always tmp_path
f = tmp_path / "inception.mkv"
f.write_bytes(b"fake-bytes")
```

---

## 9. Windows-specific gotchas

| Gotcha | Detail | Fix |
|---|---|---|
| `[...]` in glob patterns | `rglob("name [id].mkv")` silently matches nothing | `rglob("*.mkv")` + filter `.name` |
| Path separators in remote paths | `cmd_push` uses `"/"` for adb paths; Windows `os.path.relpath` uses `"\\"` | `.replace("\\", "/")` already done in cmd_push; mock_device strips leading `/` |
| `relpath` across drives | `os.path.relpath("D:\\x", "C:\\y")` raises ValueError on Python | `cmd_push` already has `except: rel_path = basename` fallback |
| Single-quoted shell args | `adb shell mv '<path>'` wraps paths in `'`; the mock must strip them | `path.strip("'")` in `mock_device.fake_run` |
| Temp path length | Windows MAX_PATH=260; pytest temp paths can be 200+ chars; chunk names add ~25 | Prefer short fixture names (`"t.mkv"`, short IDs like `"abc"`) |

---

## 10. Adding a new fixture

1. **Write it in `tests/conftest.py`** (not inline in a test file) so all tests can share it.
2. **Follow the yield pattern** — set up, `yield`, teardown happens automatically via `tmp_path`.
3. **Guard against real paths** — if your fixture involves file paths, assert they do not contain `C:\Media`.
4. **Patch both bindings** if your fixture redirects `LIBRARY_*` constants.
5. **Document it here** in section 4 with its API, use-case, and a minimal example.
6. **Assign conftest additions to `[model: opus]`** in the plan — the binding hazard makes them risky.

Template:

```python
@pytest.fixture()
def my_fixture(tmp_path, monkeypatch):
    """
    One-line summary.
    Detailed explanation of what it mocks and why.
    Yields: <type and shape of what tests receive>
    """
    # setup
    thing = tmp_path / "thing"
    thing.mkdir()
    monkeypatch.setattr(main.some_module, "some_attr", str(thing))
    yield thing
    # no explicit teardown needed — tmp_path cleaned up automatically
```

---

## 11. Future test areas

### Fetch round-trip (implement in C2)

C2 adds the `retry()` helper and needs mocked ADB for its retry tests. At the same
time, add `mock_fetch` to `conftest.py` (design in section 4.6). First fetch
round-trip test: seed `mock_device` with chunks → mock fetch copies to restore dir
→ `cmd_restore` merges → assert output hash. This tests the entire download +
restore pipeline without a browser or device.

### Auto-rollback (implement when auto-rollback lands)

Auto-rollback needs:
- Pre/post state snapshots (library before and after a command)
- Failure injection at specific steps (patch `subprocess.run` to raise after N calls)
- Verification that state reverts cleanly

The `mock_device` + `sandbox` combination already provides the infrastructure.
Failure injection: use `FakeAdb(fail_push_n=2)` or a custom `partial_fail_run`
that raises after N total subprocess calls.

### C8 post-push verification

C8's `adb shell md5sum` response is already handled by `mock_device` (returns the
real md5 of the file in `device_dir`). Tests for C8 verify:
- Hash match → push proceeds
- Hash mismatch → retryable exception raised (or direct failure if C2 not present)
- `md5sum` unavailable → graceful fallback

---

## 12. Running the suite

```powershell
# Full suite (should always be green)
pytest -q

# Smoke gate — run before every PR and every code-touching commit
pytest tests/smoke -q                              # cross-command integrity (<30 s)

# Per-layer
pytest tests/test_mvcommon.py -q                  # unit + library
pytest tests/test_cmd_push_partial.py -q           # ADB protocol (FakeAdb)
pytest tests/test_cmd_push_mock_device.py -q       # ADB data-integrity (mock_device)
pytest tests/test_cmd_replace.py -q                # cmd_replace (C9)
pytest tests/test_cmd_restore_quarantine.py -q     # cmd_restore (C11)

# Verbose (shows test names)
pytest -v

# Stop on first failure
pytest -x -q

# Run a single test
pytest tests/test_cmd_push_mock_device.py::test_chunk_bytes_match_local_source -v
```
