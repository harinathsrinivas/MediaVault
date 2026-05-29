# Task: IMP-A1 — Extract shared library I/O + hashing into `mvcommon.py`

Suggested branch: refactor/extract_mvcommon

## Context
`main.py` and `mainfetch.py` each carry verbatim-duplicated copies of the library path/folder constants and a set of helper functions (`load_library`, `calculate_file_hash`, etc.). The two copies have already drifted: `main.load_library` aborts with `sys.exit(1)` on a corrupt library, while `mainfetch.load_library` swallows the error with `except: pass` and treats a corrupt file as zero entries. IMP-A1 (per `improvements_tierA.md` and `docs/feature-auto-rollback/RELATED_IMPROVEMENTS.md`) extracts the shared surface into a new root module `mvcommon.py` imported by both entry points, eliminating the drift category permanently. This module is the foundation seam where the upcoming auto-rollback feature, C2's retry helper, and A7's import surface will live, so the public surface must stay clean and stable.

## Goal
A single `mvcommon.py` at the repo root owns the shared constants and helpers. Both `main.py` and `mainfetch.py` do `from mvcommon import ...` and define none of these symbols locally. Runtime behavior is byte-for-byte identical on the happy path, with exactly ONE deliberate change: `mainfetch`'s `load_library` now fails loudly (`sys.exit(1)`) on a corrupt library instead of silently returning zero entries. New tests cover a load/save round-trip and corrupt-library handling through `mvcommon`, and the existing C9/C11 tests still pass.

## Files affected
- `mvcommon.py` (NEW) — holds the shared constants + helpers; the single source of truth for library I/O and hashing.
- `main.py` — remove the moved definitions; add `from mvcommon import ...`; keep `main`-only constants/helpers in place.
- `mainfetch.py` — remove its `load_library` + `calculate_file_hash` definitions; add `from mvcommon import ...`; adopts the loud `load_library` contract.
- `tests/conftest.py` — fixture currently monkeypatches `main.LIBRARY_*`; must be updated to patch the new authoritative location (`mvcommon.LIBRARY_*`) so the moved `load_library`/`save_library` see the sandbox paths.
- `tests/test_mvcommon.py` (NEW) — round-trip + corrupt-library tests through `mvcommon`.
- `docs/feature-auto-rollback/A1-extract-mvcommon/A1-extract-mvcommon.md` — completion report (fill at the end).
- `improvements_tierA.md` — flip IMP-A1 Status to done on completion.
- `ARCHITECTURE.md` / `README.md` — note the new module in the layout/config sections.

## Exact symbols to move (verified line ranges as of this plan)

From `main.py` (these are the canonical/strict implementations — they win on every conflict):
- Constants: `LIBRARY_MOVIES`, `LIBRARY_SERIES`, `LIBRARY_ANIME` (lines 26-28), `LOCAL_ROOT` (line 30), `MKVMERGE_PATH` (line 32), `SPLIT_DIR_NAME`, `CHECKSUM_DIR_NAME`, `RESTORE_DIR_NAME`, `VIDEO_EXTENSIONS` (lines 74-77).
- `load_library()` — lines 83-98 (the loud `sys.exit(1)` version).
- `save_library(data)` — lines 101-129 (atomic tempfile + `os.replace`).
- `generate_short_id(long_id)` — lines 140-143.
- `calculate_file_hash(filepath, block_size=65536)` — lines 146-160.
- `human_readable_size(size_bytes)` — lines 163-169.
- `parse_size_str(size_str)` — lines 172-181.

From `mainfetch.py` (DELETE — superseded by the mvcommon import):
- `load_library()` — lines 52-80 (the silent `except: pass` version; behavior is being unified to loud).
- `calculate_file_hash(filepath, block_size=65536)` — lines 83-97 (near-identical; mvcommon version wins; note mainfetch's progress-print prefix differs cosmetically — see Risks).
- Constants `LIBRARY_MOVIES/SERIES/ANIME` (27-29), `LOCAL_ROOT` (31), `MKVMERGE_PATH` (32), `SPLIT_DIR_NAME/CHECKSUM_DIR_NAME/RESTORE_DIR_NAME/VIDEO_EXTENSIONS` (43-46).

Symbols that STAY put (not shared / out of scope): `main.py` keeps `REMOTE_ROOT`, `FFMPEG_PATH`, `DUMMY_MAX_BYTES`, `DUMMY_RECIPE_BY_EXT`, `MAINFETCH_SCRIPT`, `DEVICE_ALIASES`, `resolve_device`, `get_tech_specs`, `parse_metadata_from_id`, all `cmd_*`. `mainfetch.py` keeps `CHROME_PROFILES`, `CHROME_PROFILE_NAME`, `SYSTEM_DOWNLOADS_FOLDER`, and all Selenium logic.

`mainfetch.py` only actually references `load_library` (line 375) and `calculate_file_hash` (line 342); it does not use `generate_short_id`, `human_readable_size`, `parse_size_str`, or `save_library`. Its import line should bring in only what it uses (plus constants it references), but `save_library` is intentionally made available per the spec for future fetch-side state writes — import it lazily/only when a future change needs it, not preemptively (keeps the import line honest). See Open Decisions.

## Approach
Create `mvcommon.py` containing the eight constants and six helpers copied verbatim from `main.py` (the strict versions). It imports only stdlib (`os`, `json`, `sys`, `hashlib`, `re`, `tempfile`) — no project modules — so there is zero import-cycle risk (verified: `main.py` and `mainfetch.py` do not import each other today). Then delete the moved definitions from `main.py` and add `from mvcommon import (...)`; do the same in `mainfetch.py`, which simultaneously unifies its `load_library` onto the loud contract. Finally, repoint the test fixture's monkeypatch target from `main.LIBRARY_*` to the authoritative `mvcommon.LIBRARY_*` and add a dedicated `tests/test_mvcommon.py`. The happy path is unchanged because the moved code is identical to what `main.py` ran before; only mainfetch's corrupt-load path changes (deliberately).

## Steps

- [ ] 1. [model: sonnet] Create `mvcommon.py` with the shared constants and helpers, and replace the flat hash-progress print with a live progress bar.
  - Files: `mvcommon.py` (new)
  - Details: Create the module at repo root. No encoding-reconfigure guard (stays in main/mainfetch). Define exactly: the constants `LIBRARY_MOVIES`, `LIBRARY_SERIES`, `LIBRARY_ANIME`, `LOCAL_ROOT`, `MKVMERGE_PATH`, `SPLIT_DIR_NAME`, `CHECKSUM_DIR_NAME`, `RESTORE_DIR_NAME`, `VIDEO_EXTENSIONS`; then the helpers `load_library` (loud/strict from main.py), `save_library` (atomic from main.py), `generate_short_id`, `human_readable_size`, `parse_size_str` — copied verbatim. `calculate_file_hash` is NOT copied verbatim — replace the flat one-liner print with a live in-place progress bar (no new deps, stdlib only):
    ```python
    def calculate_file_hash(filepath, block_size=65536):
        try:
            total = os.path.getsize(filepath)
        except OSError:
            print(f"  ❌ File not found: {os.path.basename(filepath)}")
            return None
        sha256 = hashlib.sha256()
        done = 0
        bar_width = 24
        fname = os.path.basename(filepath)
        try:
            with open(filepath, 'rb') as f:
                for block in iter(lambda: f.read(block_size), b''):
                    sha256.update(block)
                    done += len(block)
                    pct = done / total if total else 1.0
                    filled = int(bar_width * pct)
                    bar = '█' * filled + '░' * (bar_width - filled)
                    size_str = f"{human_readable_size(done)} / {human_readable_size(total)}"
                    print(f"\r  🔍 {fname}  [{bar}] {size_str} ", end='', flush=True)
            print()
            return sha256.hexdigest()
        except Exception as e:
            print(f"\n  ❌ Error hashing {fname}: {e}")
            return None
    ```
    Output example: `  🔍 movie.mkv  [████████████░░░░░░░░░░░░] 1.2 GB / 2.1 GB`
    Import only stdlib (`os`, `json`, `sys`, `hashlib`, `re`, `tempfile`). No type hints (IMP-A6 scope).
  - Acceptance: `python -c "import mvcommon; print(mvcommon.LIBRARY_MOVIES, mvcommon.load_library, mvcommon.save_library, mvcommon.calculate_file_hash, mvcommon.generate_short_id, mvcommon.human_readable_size, mvcommon.parse_size_str)"` runs without error and prints all seven symbols.

- [ ] 2. [model: sonnet] Rewire `main.py` to import from `mvcommon` and delete the moved definitions.
  - Files: `main.py`
  - Details: Remove the eight shared constant definitions (lines 26-28, 30, 32, 74-77 region — leave `REMOTE_ROOT`, `FFMPEG_PATH`, `DUMMY_MAX_BYTES`, `DUMMY_RECIPE_BY_EXT`, `MAINFETCH_SCRIPT`, `DEVICE_ALIASES` exactly where they are) and the six moved helper definitions (83-98, 101-129, 140-143, 146-160, 163-169, 172-181). Add a single `from mvcommon import (LIBRARY_MOVIES, LIBRARY_SERIES, LIBRARY_ANIME, LOCAL_ROOT, MKVMERGE_PATH, SPLIT_DIR_NAME, CHECKSUM_DIR_NAME, RESTORE_DIR_NAME, VIDEO_EXTENSIONS, load_library, save_library, generate_short_id, calculate_file_hash, human_readable_size, parse_size_str)` near the other imports. Keep the `sys.stdout.reconfigure` guard. Do not touch any `cmd_*` body or any other constant/helper. Remove now-unused stdlib imports ONLY if your deletions made them unused (e.g. confirm `tempfile` is still used elsewhere in main.py before removing — likely it is not used outside save_library, so it can go; verify with a grep before removing).
  - Acceptance: `python -c "import main"` imports cleanly (note: importing main triggers no command). `python main.py local_status` runs and prints the same pending list it did before (manual eyeball against a pre-change run). Grep confirms zero remaining `def load_library`/`def save_library`/`def calculate_file_hash`/`def generate_short_id`/`def human_readable_size`/`def parse_size_str` in `main.py`.

- [ ] 3. [model: sonnet] Rewire `mainfetch.py` to import from `mvcommon`; delete its `load_library` + `calculate_file_hash` and shared constants. This unifies its load behavior to loud.
  - Files: `mainfetch.py`
  - Details: Delete `load_library` (52-80) and `calculate_file_hash` (83-97) and the shared constants (`LIBRARY_*` 27-29, `LOCAL_ROOT` 31, `MKVMERGE_PATH` 32, `SPLIT_DIR_NAME/CHECKSUM_DIR_NAME/RESTORE_DIR_NAME/VIDEO_EXTENSIONS` 43-46). Add `from mvcommon import (LIBRARY_MOVIES, LIBRARY_SERIES, LIBRARY_ANIME, LOCAL_ROOT, MKVMERGE_PATH, SPLIT_DIR_NAME, CHECKSUM_DIR_NAME, RESTORE_DIR_NAME, VIDEO_EXTENSIONS, load_library, calculate_file_hash)`. Keep `CHROME_PROFILES`, `CHROME_PROFILE_NAME`, `SYSTEM_DOWNLOADS_FOLDER`, the Selenium import block, and all fetch logic untouched. Confirm which of those constants mainfetch actually still references (grep) and import only the referenced ones plus the two functions — do NOT import `save_library`/`generate_short_id`/`human_readable_size`/`parse_size_str` since mainfetch doesn't use them (see Open Decisions item 3). Remove now-unused stdlib imports in mainfetch ONLY if your deletions orphaned them (likely none — `hashlib` was used only by the deleted `calculate_file_hash`; verify and remove `import hashlib` if so).
  - Acceptance: `python -c "import mainfetch"` imports cleanly. Grep confirms zero remaining `def load_library`/`def calculate_file_hash` in `mainfetch.py`. `python mainfetch.py` with no args prints its usage/exit path without raising NameError (sanity that no symbol is missing).

- [ ] 4. [model: opus] Repoint the test fixture monkeypatch to the authoritative `mvcommon` location and verify the existing C9/C11 tests still pass.
  - Files: `tests/conftest.py`
  - Details: The `sandbox` fixture currently does `monkeypatch.setattr(main, attr, path)` for `LIBRARY_MOVIES/SERIES/ANIME`. After extraction, `mvcommon.load_library`/`save_library` read the module-level names in `mvcommon`, so patching `main.LIBRARY_*` no longer redirects them — the moved functions would read the real `C:\Media` paths, tripping the hard safety guard or worse. This is the one risky entanglement in the refactor: the binding location of the constants moves out from under the test. Update the fixture to `import mvcommon` and `monkeypatch.setattr(mvcommon, attr, path)` (keep the `"C:\\Media" not in path` assertion). Decide whether `main` also needs the attr patched: since `main` imported the constants by value (`from mvcommon import LIBRARY_MOVIES`), `main.LIBRARY_MOVIES` is a separate binding; patch BOTH `mvcommon` and `main` (and `mainfetch` if a test ever touches fetch) so any code reading either binding sees the sandbox. Confirm `conftest.py` still imports `main` (it does, for `make_video_dummy`/`merge_video_files` patches) and add `import mvcommon`.
  - Acceptance: `pytest tests/test_cmd_replace.py tests/test_cmd_restore_quarantine.py -q` passes with the same result as before the refactor (run it on the pre-change tree first to capture the baseline). No test reads or writes anything under real `C:\Media`.

- [ ] 5. [model: sonnet] Add `tests/test_mvcommon.py` covering the round-trip and corrupt-library handling.
  - Files: `tests/test_mvcommon.py` (new)
  - Details: Reuse the `sandbox` fixture (now patching `mvcommon`). Test A — round-trip: build a dict with one `mov-*`, one `tv-*`, one `ani-*` entry; `mvcommon.save_library(data)`; assert the three sandbox JSON files exist and split by prefix correctly; `mvcommon.load_library()` returns a dict equal to the input. Test B — atomic save: monkeypatch `mvcommon.os.replace` to raise; call `save_library` and assert it re-raises and leaves no `.tmp` orphan in the lib dir (and the original target file, if pre-existing, is unchanged). Test C — corrupt-library loud failure: write invalid JSON into `lib_movies`; assert `mvcommon.load_library()` raises `SystemExit` (this codifies the unified loud contract and is the regression guard for the deliberate behavior change). Never touch real `C:\Media` — all paths come from the sandbox fixture.
  - Acceptance: `pytest tests/test_mvcommon.py -q` passes (3 tests). Running the full `pytest -q` suite is green.

- [ ] 6. [model: haiku] Update docs: mark IMP-A1 done and note the new module layout.
  - Files: `improvements_tierA.md`, `ARCHITECTURE.md`, `README.md`
  - Details: In `improvements_tierA.md` flip the IMP-A1 `Status: pending` line to `Status: done`. In `ARCHITECTURE.md` add `mvcommon.py` to the Section 3 repository-layout block (one line: "shared library I/O + hashing constants/helpers imported by both entry points") and add a short note in Section 7.2 / Section 14 that the shared constants and the six helpers now live in `mvcommon.py` and that BOTH entry points' `load_library` is now the loud/strict version (drift eliminated). In `README.md`, if it lists the active files, add `mvcommon.py`. Do not restructure either doc — surgical additions only.
  - Acceptance: Grep `improvements_tierA.md` for `IMP-A1` shows `Status: done`. `ARCHITECTURE.md` mentions `mvcommon.py` in the layout section. No unrelated doc lines changed (git diff is small and additive).

- [ ] 7. [model: haiku] Fill the completion report and sync the root PLAN.md note.
  - Files: `docs/feature-auto-rollback/A1-extract-mvcommon/A1-extract-mvcommon.md`; check `PLAN.md` at repo root.
  - Details: Fill the completion-report stub with: what moved, the unified-load decision outcome, the conftest monkeypatch-location change, and the verification commands that passed. The repo-root `PLAN.md` is currently the G1 working copy (unrelated feature) — do NOT overwrite it with this A1 plan; only add a one-line note in the A1 completion report that root `PLAN.md` was left as-is (it belongs to the in-flight G1 branch). If a reviewer expects the root PLAN to track the current task, flag it rather than clobbering.
  - Acceptance: completion report is filled with concrete commands/results; root `PLAN.md` untouched unless the user later asks.

## Confirmed Decisions (2026-05-30)

1. **Symbol set** — confirmed as listed. `REMOTE_ROOT`, `DEVICE_ALIASES`, `parse_metadata_from_id`, `get_tech_specs`, all `cmd_*` stay in `main.py`.
2. **`load_library` error handling** — unified loud (`sys.exit(1)`) everywhere. `mainfetch`'s silent-zero-entries behavior is intentionally removed.
3. **Re-export shims** — none. Both files import directly via `from mvcommon import ...`.
4. **Hashing print cosmetic change** — accepted. mainfetch adopts main's prefix AND gets the new progress bar (see step 1 note).
5. **Progress UI enhancement** — `calculate_file_hash` gets a simple in-place progress bar (no new dependencies). Replaces the flat `"Hashing: file... Done."` in both entry points. See step 1 for the exact implementation spec.

---

## Open Decisions (RESOLVED — kept for record)

1. Exact set of symbols to move. Recommendation: move the 9 constants and 6 helpers listed in "Exact symbols to move" above. `main.py` is the source for all of them (its strict `load_library` and atomic `save_library` win). Confirm nothing else should move (e.g. `REMOTE_ROOT`, `DEVICE_ALIASES`, `parse_metadata_from_id`, `get_tech_specs` are intentionally NOT shared and stay in `main.py`).
   - main.py: `LIBRARY_MOVIES/SERIES/ANIME` (26-28), `LOCAL_ROOT` (30), `MKVMERGE_PATH` (32), `SPLIT_DIR_NAME/CHECKSUM_DIR_NAME/RESTORE_DIR_NAME/VIDEO_EXTENSIONS` (74-77), `load_library` (83-98), `save_library` (101-129), `generate_short_id` (140-143), `calculate_file_hash` (146-160), `human_readable_size` (163-169), `parse_size_str` (172-181).
   - mainfetch.py (delete, superseded): `load_library` (52-80), `calculate_file_hash` (83-97), constants (27-29, 31, 32, 43-46).

2. `load_library` error-handling divergence. RECOMMENDED: unify on the LOUD `main.py` behavior (`sys.exit(1)` on corrupt JSON). This CHANGES `mainfetch.py`'s current behavior: today a corrupt library makes a fetch silently see zero entries (it would do nothing and exit "clean"); after this change a corrupt library aborts the fetch loudly. This is the only intentional runtime change in the whole task and is exactly what the auto-rollback work wants (a corrupt library should never be silently treated as empty). Alternative (NOT recommended): keep a `silent=True` parameter so mainfetch preserves the swallow. Confirm the loud unification is acceptable.

3. Thin re-export shims in `main.py`/`mainfetch.py`. RECOMMENDED: none — import the names directly with `from mvcommon import ...`. No `main.load_library = mvcommon.load_library` aliasing. The existing tests import `main` and (after step 4) patch `mvcommon`; no external caller depends on `main.load_library` existing as an attribute except the test fixture, which step 4 updates. Confirm we do NOT need a back-compat shim (e.g. if any usage_commands.txt or external script does `from main import load_library`).

## Risks and edge cases
- Conftest binding location (the main risk): `from mvcommon import X` binds a copy; monkeypatching `main.X` no longer affects `mvcommon`'s own reads. Step 4 (opus) repoints the patch to `mvcommon` and patches both bindings. If missed, tests would either hit the real `C:\Media` (caught by the safety assertion) or silently pass against stale state.
- `calculate_file_hash` cosmetic divergence: main's progress prefix is `"     🔍 Verifying: "`, mainfetch's is `"   > 🔍 Hashing: "`. Unifying on main's version changes the exact console string mainfetch prints during hashing. This is cosmetic (stdout only, no behavior/state change) but should be called out — confirm it is acceptable that mainfetch's hashing line now reads "Verifying" instead of "Hashing".
- Orphaned stdlib imports after deletion: removing `save_library` may orphan `tempfile` in `main.py`; removing `calculate_file_hash` orphans `hashlib` in `mainfetch.py`. Remove ONLY imports that the deletions orphaned (verify with grep first); do not touch other imports.
- Import cycle: none today (`main.py` and `mainfetch.py` do not import each other; `mvcommon` imports only stdlib). Keep it that way — `mvcommon` must never import `main` or `mainfetch`.
- Happy-path identity: because the moved code is copied verbatim from `main.py`, `main.py`'s runtime is unchanged. Only `mainfetch`'s corrupt-load path and its hashing print string change.

## Verification
Run from repo root (`C:\Users\harin\PycharmProjects\MediaVault`):
- `python -c "import mvcommon, main, mainfetch"` — all three import without error.
- `python main.py local_status` — prints the same pending list as a pre-change run (no behavior change).
- `python mainfetch.py` — prints usage and exits without NameError.
- `pytest -q` — full suite green (existing C9/C11 tests + new `test_mvcommon.py`).
- `git diff --stat` — only the seven listed files changed; `archive/` untouched.
- Grep both entry points for `def load_library`/`def save_library`/`def calculate_file_hash` — zero matches (definitions live only in `mvcommon.py`).

## Out of scope
- Type hints / mypy on `mvcommon` (that is IMP-A6).
- `mvconfig.json` externalization of constants (IMP-A5).
- The `argparse` migration (IMP-A2) and any new flags.
- Moving `REMOTE_ROOT`, `DEVICE_ALIASES`, `parse_metadata_from_id`, `get_tech_specs`, or any `cmd_*` into `mvcommon`.
- Touching anything under `archive/`, real `C:\Media` files, or real `library_*.json`.
- Adding `save_library` calls into `mainfetch` (the spec only asks that it be *available*; actually wiring fetch-side writes is future C1/E5 work).
- Rewriting the root `PLAN.md` (it is the in-flight G1 working copy).

## End-of-plan deliverables
- Branch name: `refactor/extract_mvcommon` (branch from `origin/main`).
- PR target: `main`.
- Manual test commands to verify nothing broke (run before merge):
  - `python -c "import mvcommon, main, mainfetch"`
  - `python main.py local_status` (compare against a captured pre-change run)
  - `python main.py scan_unprepped` (exercises load_library + human_readable_size + the constants)
  - `python mainfetch.py` (usage path, no NameError)
  - `pytest -q`
- On implementation, mark IMP-A1 `Status: done` in `improvements_tierA.md` (step 6).
- `ARCHITECTURE.md` (Sections 3, 7.2, 14) and `README.md` need the new `mvcommon.py` module noted in the layout/config (step 6).
- In the PR/commit note, record that A1 was done as an auto-rollback foundation (per `docs/feature-auto-rollback/RELATED_IMPROVEMENTS.md`), and that it intentionally unified `mainfetch.load_library` to the loud contract.
