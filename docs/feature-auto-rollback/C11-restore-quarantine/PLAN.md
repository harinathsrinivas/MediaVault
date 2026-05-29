# Task: IMP-C11 — Hash-mismatch quarantine in cmd_restore

Suggested branch: feature/restore_quarantine

> **STATUS: READY FOR IMPLEMENTATION.** All open decisions resolved (2026-05-29).

---

## Context

When `cmd_restore` (main.py) finds a SHA256 mismatch on a downloaded file in
`<folder>/restore/`, it returns `False` and **leaves the bad file in place**.
The next `fetch` (mainfetch.py `fetch_single_entry`) sees the file already
exists via an `os.path.exists` skip-check and does **not** re-download it,
trapping the user until they manually delete the bad file. IMP-C11 makes restore
self-healing: on mismatch, move the bad file to
`<folder>/restore/quarantine/<filename>.<ISO-timestamp>` and print a clear,
greppable diagnostic so a fresh fetch re-downloads automatically. This is also a
prerequisite for the paused auto-rollback feature — "leave `restore/` in a clean,
self-healing state on failure" IS the restore-side expression of rollback
(`RELATED_IMPROVEMENTS.md` → C11), so the quarantine path must be centralized
into one reusable helper (the "seam").

## Goal

Concrete definition of done:

1. On a SHA256 mismatch during `cmd_restore` (standard single-file path, line
   ~1124), the bad file is **moved** out of `restore/` into
   `<folder>/restore/quarantine/<filename>.<ISO-timestamp>`, NOT left in place.
   In the split path, any chunk whose SHA256 doesn't match its stored hash is
   quarantined the same way BEFORE the merge runs; clean chunks are left in
   `restore/`; any stale partial merged output at `target_path` is deleted.
2. A machine-greppable diagnostic is printed (exact substring, see Approach):
   `Hash mismatch. Bad file quarantined at <path>. A fresh fetch will re-download.`
3. After quarantine, re-running `fetch` re-downloads the file (the original
   filename is now absent from `restore/`, so mainfetch's `os.path.exists` skip
   no longer triggers), and a subsequent `restore` succeeds — proven by a test.
4. The **happy path is byte-for-byte identical**: a successful restore produces
   the exact same file moves, library mutations, stdout, and return value as
   today. No new behavior on success.
5. The quarantine location/naming logic lives in **one helper** (e.g.
   `quarantine_restore_file(restore_folder, filename) -> quarantine_path`) so
   auto-rollback can reuse it. No duplicated path-building.
6. New tests in `tests/` (sandbox copies only) cover: mismatch → quarantine,
   diagnostic emitted, success path unchanged, idempotent re-quarantine
   (two mismatches of the same filename don't collide), and the fetch-self-heal
   contract (the original filename is absent after quarantine).
7. `IMP-C11` marked `done` in `improvements_tierC.md`; completion report filled
   in `docs/feature-auto-rollback/C11-restore-quarantine/C11-restore-quarantine.md`.

## Files affected

- `main.py` — add `quarantine_restore_file(...)` helper near the restore
  utilities; call it from `cmd_restore`'s standard-path hash-mismatch branch
  (and, pending Open Decision #2, the split-path merge-verification branch).
- `tests/test_cmd_restore_quarantine.py` — NEW test module (mirrors the
  structure of the existing `tests/test_cmd_replace.py` from C9).
- `tests/conftest.py` — extend ONLY if a new shared fixture is needed (prefer
  reusing the existing C9 sandbox/library fixtures; do not rewrite them).
- `improvements_tierC.md` — flip IMP-C11 status to `done`.
- `docs/feature-auto-rollback/C11-restore-quarantine/C11-restore-quarantine.md`
  — fill the "Completion report" section.
- `ARCHITECTURE.md` — update §7.7 (`cmd_restore` flow) and §12 (Error Handling:
  "Hash mismatch on restore" bullet) to describe quarantine.
- `docs/feature-auto-rollback/RELATED_IMPROVEMENTS.md` — (optional, small) note
  the helper name auto-rollback should reuse, so the seam is discoverable.

> **mainfetch.py is intentionally NOT modified.** The spec confirms
> `fetch_single_entry` already `os.makedirs(restore_folder, exist_ok=True)` and
> the `os.path.exists` skip (mainfetch.py:253-254 / 268) self-heals once the bad
> file is gone from `restore/`. Touching mainfetch is out of scope.

## Approach

The change is small, surgical, and additive. The current standard-path branch
in `cmd_restore` (main.py ~1122-1126) is:

```python
print("   > Verifying Hash before restore...")
if calculate_file_hash(source_path) != entry['hash']:
    print("❌ Error: Restore file hash mismatch! Corrupt?")
    return False
```

We replace ONLY the failure branch body: instead of `print(...) ; return False`,
call the new helper to move `source_path` into a `quarantine/` subfolder of
`restore_folder`, print the greppable diagnostic, and `return False`. The success
path (hash matches → `shutil.move` into place → cleanup → status update) is left
completely untouched.

The helper centralizes the seam:

```python
def quarantine_restore_file(restore_folder, filename):
    """Move a bad restore file into restore/quarantine/<filename>.<ISO-ts>.
    Returns the destination path. Single source of truth for 'where a bad
    restore file goes' — reused by auto-rollback's restore handling."""
    quarantine_dir = os.path.join(restore_folder, "quarantine")
    os.makedirs(quarantine_dir, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%dT%H%M%S")   # filesystem-safe ISO-ish
    dest = os.path.join(quarantine_dir, f"{filename}.{ts}")
    # collision guard: if same filename quarantined twice within one second,
    # append a counter so we never overwrite a prior quarantined copy.
    n = 1
    final = dest
    while os.path.exists(final):
        final = f"{dest}.{n}"
        n += 1
    shutil.move(src=os.path.join(restore_folder, filename), dst=final)
    return final
```

Notes baked into the recommendation:
- **Timestamp format**: `%Y%m%dT%H%M%S` (e.g. `20260529T142233`). A literal
  `:`-containing ISO-8601 string is NOT filesystem-safe on Windows NTFS, so we
  use the colon-free compact ISO basic form. (Open Decision #3 can override.)
- **Diagnostic** goes to **stdout** (consistent with every other `print` in this
  codebase — there is no `logging` and no stderr usage anywhere; see
  ARCHITECTURE §15). Exact greppable substring, emoji-prefixed to match house
  style: `❌ Hash mismatch. Bad file quarantined at <path>. A fresh fetch will
  re-download.` The plain ASCII core (`Hash mismatch. Bad file quarantined at`)
  is what tests assert on, so the emoji never breaks the grep contract.
  (Open Decision #4: whether to also print expected vs actual hash.)
- `datetime` is already imported in `main.py` (ARCHITECTURE §2 lists it among
  stdlib imports), so no new import is needed; confirm before use.

## Steps

- [x] 1. [model: sonnet] Add the `quarantine_restore_file` helper to `main.py`.
  - Files: `main.py`
  - Details: Place the helper next to the other restore utilities (near
    `merge_video_files` / `cmd_verify_restore`, before `cmd_restore` ~line 1062).
    Implement exactly the signature and body sketched in "Approach": create
    `<restore_folder>/quarantine/` with `exist_ok=True`, build a
    colon-free timestamped destination `<filename>.<YYYYmmddTHHMMSS>`, apply the
    collision-counter guard, `shutil.move` the file, and return the final dest
    path. Confirm `datetime` and `shutil` are already imported at the top of
    `main.py`; if `datetime` is imported as `from datetime import datetime`, use
    `datetime.now()`; if as `import datetime`, use `datetime.datetime.now()` —
    match the existing import form, do not add a redundant import.
  - Acceptance: `python -c "import main"` still imports cleanly; the helper is
    callable and, given a temp `restore/` containing a file, moves it into
    `restore/quarantine/<name>.<timestamp>` and returns that path. No call site
    yet — this step only adds the function.

- [x] 2. [model: sonnet] Wire the helper into `cmd_restore`'s standard-path
  hash-mismatch branch.
  - Files: `main.py`
  - Details: In `cmd_restore`, the standard (non-split) branch at ~1122-1126,
    replace ONLY the failure body. Current:
    `print("❌ Error: Restore file hash mismatch! Corrupt?"); return False`.
    New: call `q = quarantine_restore_file(restore_folder, filename)`, then
    `print(f"❌ Hash mismatch. Bad file quarantined at {q}. A fresh fetch will re-download.")`,
    then `return False`. Do NOT alter the success path, the `shutil.move`
    into place, the empty-`restore/` cleanup, or the `status="restored_local"`
    update. Wrap the `quarantine_restore_file` call defensively only if a move
    failure (e.g. Windows lock) must not crash restore — if the move raises,
    fall back to today's behavior (print the old error, leave file, return
    False) so we never make restore *worse* than before. Keep that fallback
    minimal.
  - Acceptance: With a sandbox `restore/` holding a file whose bytes do NOT match
    `entry["hash"]`, `cmd_restore` returns `False`, the original filename is gone
    from `restore/`, a copy exists under `restore/quarantine/`, and the
    greppable diagnostic substring `Hash mismatch. Bad file quarantined at`
    appears in stdout. With a matching file, behavior is byte-for-byte identical
    to before (file moved into place, status updated, returns truthy).

- [x] 3. [model: sonnet] Add pre-merge per-chunk verification to the split path
  of `cmd_restore`, quarantining offending chunks on mismatch.
  - Files: `main.py`
  - Details: In the split path of `cmd_restore` (~line 1083, after the existence
    check at line 1084 and BEFORE the `merge_video_files` call at line 1089),
    insert a per-chunk SHA256 verification loop. For each chunk, compare
    `calculate_file_hash(chunk_path)` against `chunk_meta["hash"]` from
    `chunks_meta`. Collect all offending chunk filenames. If any chunk fails:
      1. Call `quarantine_restore_file(restore_folder, chunk_filename)` for each
         offending chunk ONLY (clean chunks stay in `restore/` so a targeted
         re-fetch can refill just the bad ones).
      2. If a partial merged output already exists at `target_path` (from a
         prior failed merge attempt), delete it with `os.remove` — do NOT
         quarantine it; it is reproducible from chunks and re-fetch + re-merge
         regenerates it.
      3. Print one greppable diagnostic per offending chunk:
         `❌ Hash mismatch. Bad file quarantined at <path>. A fresh fetch will re-download.`
      4. `return False`.
    If ALL chunks pass, proceed to `merge_video_files` exactly as today. The
    success path (merge → re-index → cleanup → return True) is untouched.
  - Acceptance: A sandbox split entry with one corrupt chunk results in: that
    chunk moved to `restore/quarantine/`, the clean chunk left in `restore/`,
    no merged output at `target_path`, the greppable diagnostic in stdout, and
    `return False`. A split entry with all clean chunks merges and restores
    byte-for-byte as before.

- [x] 4. [model: sonnet] Write the test module for the quarantine behavior.
  - Files: `tests/test_cmd_restore_quarantine.py` (new), `tests/conftest.py`
    (extend only if necessary).
  - Details: FIRST read `tests/conftest.py` and `tests/test_cmd_replace.py`
    (added by C9) to reuse the existing sandbox + library-path monkeypatch
    fixtures and the established naming/style; do not reinvent the harness or
    rewrite C9 fixtures. All tests operate on COPIES in a `tmp_path` sandbox and
    monkeypatch the three `LIBRARY_*` constants — NEVER touch real `C:\Media` or
    real `library_*.json`. Build a minimal sandbox: a fake media folder with a
    `restore/` subfolder, a single-file entry in an in-memory/temp library whose
    `entry["hash"]` is a known SHA256, and a "downloaded" file in `restore/`
    whose bytes do NOT match (to simulate corruption). Tests to write:
      1. `test_mismatch_moves_file_to_quarantine` — after `cmd_restore`, the
         original filename is absent from `restore/`, exactly one file exists
         under `restore/quarantine/` and its name starts with `<filename>.`.
      2. `test_mismatch_prints_greppable_diagnostic` — capture stdout
         (`capsys`); assert the substring
         `Hash mismatch. Bad file quarantined at` is present.
      3. `test_mismatch_returns_false` — `cmd_restore` returns False on mismatch.
      4. `test_success_path_unchanged` — with a file whose bytes DO match
         `entry["hash"]`, the file is moved into `folder_path` (overwriting the
         dummy), `restore/` is cleaned, `status == "restored_local"`, and NO
         `quarantine/` folder is created. This is the byte-for-byte-identical
         guard.
      5. `test_requarantine_no_collision` — quarantine the same filename twice
         (two mismatch restores in succession); assert two distinct files exist
         under `quarantine/` (collision-counter guard works), neither
         overwritten.
      6. `test_self_heal_contract` — after a mismatch quarantine, assert the
         exact filename mainfetch's skip-check looks for
         (`os.path.join(restore_folder, filename)`) does NOT exist, so a re-fetch
         would re-download. (This encodes the cross-file contract without running
         Selenium.)
      7. `test_split_mismatch_quarantines_offending_chunk_only` — build a
         sandbox split entry with two chunks where only one has bad bytes;
         after `cmd_restore`, assert: the corrupt chunk is absent from
         `restore/` and exists under `restore/quarantine/`; the clean chunk
         is still in `restore/`; `return False`; greppable diagnostic in
         stdout.
      8. `test_split_mismatch_deletes_partial_merge_output` — pre-place a
         stale file at `target_path` to simulate a prior partial merge; run
         `cmd_restore` with a corrupt chunk; assert `target_path` does NOT
         exist afterward (partial output was deleted, not left or quarantined).
      9. `test_split_success_path_unchanged` — all chunks clean; assert merge
         succeeds, `target_path` exists, library `status == "restored_local"`,
         no `quarantine/` folder created, chunk files cleaned up.
  - Acceptance: `pytest tests/test_cmd_restore_quarantine.py -v` passes; the full
    `pytest tests/` suite (including the C9 tests) still passes; no real
    `C:\Media` path or real library file is read or written by any test.

- [x] 5. [model: haiku] Update `ARCHITECTURE.md` to document the quarantine
  behavior.
  - Files: `ARCHITECTURE.md`
  - Details: In §7.7 (`cmd_restore` flow, standard-file list), change the
    "refuse to proceed if it doesn't match" bullet to state that on mismatch the
    file is moved to `restore/quarantine/<filename>.<timestamp>` and a fresh
    fetch self-heals. In §12 (Error Handling), update the bullet "**Hash mismatch
    on restore**: blocks the move into place; the bad file remains in `restore/`
    for inspection." to describe quarantine instead. Mention the
    `quarantine_restore_file` helper as the centralized seam. Keep edits
    surgical — only those two locations.
  - Acceptance: Both sections accurately describe the new behavior; no unrelated
    edits; the helper name appears so future readers (and auto-rollback) can find
    the seam.

- [ ] 6. [model: haiku] Flip IMP-C11 status to done and fill the completion
  report.
  - Files: `improvements_tierC.md`,
    `docs/feature-auto-rollback/C11-restore-quarantine/C11-restore-quarantine.md`
  - Details: In `improvements_tierC.md`, change the IMP-C11 `Status: pending`
    line to `Status: done (feature/restore_quarantine, PR to main <date>)`
    matching the format C9 used (`done (fix/atomic_replace, PR to main
    2026-05-29)`). In the C11 tracking doc, fill the "Completion report" fields
    (Branch, PR, Merged commit, Files changed, Tests added, Manual test commands,
    Open decisions resolved, Notes) and check the "Definition of Done" boxes that
    are satisfied. Do this LAST, after code + tests are green and the PR exists.
  - Acceptance: IMP-C11 reads `done` with branch/PR; the completion report is
    fully filled with real values (not placeholders).

## Risks and edge cases

- **Windows ISO-8601 colons**: a raw `datetime.now().isoformat()` contains `:`,
  which is illegal in NTFS filenames. The plan mandates the colon-free
  `%Y%m%dT%H%M%S` form. The executor must NOT use bare `.isoformat()`.
- **Same-second re-quarantine collision**: two mismatches of the same filename
  within one second would produce the same timestamp; the collision-counter
  guard prevents overwriting a prior quarantined copy. Tested explicitly.
- **Move failure under file lock** (Plex/Windows Search holding the bad file):
  `shutil.move` could raise. The plan's Step 2 keeps a minimal fallback to
  today's behavior (print error, leave file, return False) so C11 never makes
  restore *worse* than the status quo. Don't over-engineer retries here (that's
  C2's domain).
- **`datetime` import form**: `main.py` imports `datetime` — executor must match
  the existing import style (`from datetime import datetime` vs
  `import datetime`) rather than adding a duplicate import.
- **Happy-path regression**: the single biggest risk is accidentally changing
  the success branch. Step 2 edits ONLY the failure branch; Test #4 is the
  byte-for-byte guard. Keep the diff to the failure branch + the new helper.
- **Split path silent corrupt restore**: before this fix, a corrupt chunk could
  silently produce a bad merged file (mkvmerge is lenient). Step 3's pre-merge
  verification catches this before the merge runs. The clean chunk(s) are left
  in `restore/` so only the bad chunk needs re-fetching — do not quarantine
  clean chunks or delete them.
- **Root `/PLAN.md` is NOT a C11 file**: see the "Root PLAN.md" note below — do
  not overwrite it.
- **Test isolation**: tests must monkeypatch `LIBRARY_MOVIES/SERIES/ANIME` to
  sandbox paths and use `tmp_path`. A test that calls the real `load_library` /
  `save_library` without monkeypatching would read/write the real `C:\Media`
  JSONs — strictly forbidden.

## Verification

Run after all steps (from repo root, using the venv interpreter):

```
.venv\Scripts\python.exe -c "import main"            # imports cleanly
.venv\Scripts\python.exe -m pytest tests/ -v          # full suite incl. C9 tests
.venv\Scripts\python.exe -m pytest tests/test_cmd_restore_quarantine.py -v
git diff --stat                                        # confirm only intended files
```

Manual smoke (SANDBOX ONLY — never against real `C:\Media`):
1. Build a throwaway folder with a `restore/` subdir and a copy of a small
   file; craft a temp library JSON entry pointing at it with a deliberately
   wrong `hash`; monkeypatch/point the library constants at the temp JSON.
2. `python main.py restore <test-id>` → expect the greppable diagnostic and the
   file relocated under `restore/quarantine/`.
3. Confirm the original filename is absent from `restore/` (proves the fetch
   skip-check will no longer trap).

Grep contract check (the auto-rollback / user-facing guarantee):
```
python main.py restore <test-id> 2>&1 | findstr /C:"Bad file quarantined at"
```

## Out of scope

- Any change to `mainfetch.py` (the existing `os.path.exists` skip + `makedirs`
  already self-heal once the bad file is gone).
- A `cleanup_quarantine` command / retention/purge policy (explicitly deferred —
  IMP-D extension).
- Touching anything under `archive/`.
- Auto-rollback itself (this is only the prerequisite seam).
- Printing expected-vs-actual hash values in the diagnostic (stdout, concise, no hashes).
- Overwriting the root `/PLAN.md` (it holds the paused auto-rollback plan).

---

## Resolved Decisions (2026-05-29)

| # | Question | Answer |
|---|----------|--------|
| 1 | Which split-path files get quarantined? | Offending chunk(s) only; partial merged output at `target_path` is deleted |
| 2 | Is the split path in scope? | Yes — pre-merge per-chunk verification added in Step 3 |
| 3 | Quarantine path & naming | `<folder>/restore/quarantine/<filename>.<YYYYmmddTHHMMSS>` (NTFS-safe) |
| 4 | Diagnostic stream & verbosity | stdout, concise, no hash values |
| 5 | Root `/PLAN.md` handling | Do NOT overwrite; C11 plan stays in subfolder only |

---

## Wrap-up reference (for the implementer)

- **Branch:** `feature/restore_quarantine`, branched from `origin/main`
  (NOT from the current `fix/atomic_replace` branch).
- **PR target:** `main`.
- **Manual test commands** (sandbox only — never real `C:\Media`):
  - `.venv\Scripts\python.exe -m pytest tests/ -v`
  - `python main.py restore <sandbox-test-id>` (with library constants pointed
    at a temp JSON) → expect quarantine + greppable diagnostic.
  - `python main.py restore <sandbox-test-id> 2>&1 | findstr /C:"Bad file quarantined at"`
- On completion: mark IMP-C11 `done` in `improvements_tierC.md` and fill the
  completion report in `C11-restore-quarantine.md`; note in the PR that this was
  done as an auto-rollback prerequisite.
