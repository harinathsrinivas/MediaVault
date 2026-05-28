# Task: IMP-C9 — Atomic cmd_replace via two-rename pattern

Suggested branch: fix/atomic_replace
(Branch from `origin/main`, NOT the current `docs/auto-rollback-planning` branch. Per README.md, `origin/main` is code-identical to the working tree as of the pause; branching off it loses nothing.)

## Context
`cmd_replace` (`main.py:857-904`) currently writes a tiny dummy temp file, then `os.remove(original)`, then `os.rename(tmp -> original)`. Between the delete (line 884) and the rename (line 899) the disk has NEITHER the original NOR the dummy at the expected path — a power-loss/process-kill in that window destroys the only local copy (bytes survive only in Google Photos). This is the single true point-of-no-return for the whole archive pipeline (see `FAILURE_ANALYSIS.md` Example B). IMP-C9 closes that window with a two-rename pattern so a crash always leaves either the original or the dummy. C9 is the FIRST prerequisite for the paused auto-rollback feature, which will pin the replace point-of-no-return at the first rename — so that step must stay clearly identifiable (the "seam").

## Goal
A power-loss-safe `cmd_replace`: at every instant of an in-progress replace, the expected path holds either the original file or the dummy — never nothing. Concretely:
- Success path is behaviorally unchanged: same final on-disk state (dummy at `original`'s name), same `status="archived"`, same `save_library` call, same `return True`, same printed success line.
- The `os.remove(original)` is replaced by an atomic `os.rename(original -> original + ".tobedeleted")`, guarded by the SAME 3-retry / `chmod` / PermissionError logic that today guards the remove.
- A new automated test in `tests/` simulates a crash AFTER the first rename but BEFORE the second, and asserts the original bytes are still recoverable on disk (nothing is lost).
- The first rename (`original -> .tobedeleted`) carries an inline `# ROLLBACK SEAM:` comment marking it as the commit / point-of-no-return.

## Files affected
- `main.py` — rewrite the swap section inside `cmd_replace` (lines ~876-899) to the two-rename pattern + add a leading stale-`.tobedeleted` sweep. No other function touched.
- `tests/conftest.py` — NEW. Pytest fixtures: a temp sandbox media folder + monkeypatched library JSON path constants so tests never touch real `C:\Media`.
- `tests/test_cmd_replace.py` — NEW. The C9 tests (happy path, crash-between-renames, stale-file re-entry, locked-file retry, dummy-creation-failure abort).
- `requirements.txt` or a new `requirements-dev.txt` — add `pytest` (it is currently installed in NEITHER the system interpreter nor `.venv`). See Risks.
- `improvements_tierC.md` — flip IMP-C9 `Status: pending` -> `Status: done` (completion step).
- `docs/feature-auto-rollback/C9-atomic-replace/C9-atomic-replace.md` — fill in the "Completion report" section (completion step).
- `ARCHITECTURE.md` — update the two prose spots that describe the old replace sequence (§7.6 "cmd_replace flow" step 4-5, and §10 Stage 3 steps 4-5). Only if behavior description changed; it has.
- `docs/feature-auto-rollback/FAILURE_ANALYSIS.md` — OPTIONAL note that Example B's window is now closed by C9 (line refs will shift). Defer unless trivial.

## Approach
The change is localized to the file-swap block of `cmd_replace`. The new order is:

1. (unchanged) `make_video_dummy(tmp_path, ext)` writes `<original>.dummy_tmp<ext>`. If it returns `False`, abort with the same message and `return False`.
2. (NEW) Compute `tobedeleted = original + ".tobedeleted"`. If a stale `tobedeleted` already exists from a prior crashed run, handle it per Open Decision 2 before proceeding (idempotent re-entry).
3. **If `original` exists**: rename `original -> tobedeleted` using the EXISTING 3-retry + `chmod(S_IWRITE)` + PermissionError loop (moved from the old `os.remove`). This is the atomic commit point. Mark it `# ROLLBACK SEAM:`.
   - If `original` does NOT exist (already a partial/prior run with no original), there is nothing to move aside; skip straight to step 4. (Mirrors today's `if os.path.exists(original)` guard.)
4. `os.rename(tmp_path -> original)` — atomic; the dummy becomes live. (Simple single attempt, as today.)
5. Delete `tobedeleted` per Open Decision 1. (No retry loop needed — failure here is non-fatal.)
6. (unchanged) set `status="archived"`, `save_library`, print success, `return True`.

Why this is crash-safe on NTFS: `os.rename` over an existing target is `MoveFileEx`-backed and atomic at the filesystem level for same-volume renames (always true here — `tmp_path`, `original`, `tobedeleted` are all siblings in `local_folder`). At no instant between step 3 and step 4 is the original name absent AND the data gone: after step 3 the data lives at `tobedeleted`; step 4 makes the dummy live at `original`; step 5 only removes the now-redundant `tobedeleted`. A crash after step 3 / before step 4 leaves the original recoverable at `tobedeleted`; a crash after step 4 leaves the dummy live and `tobedeleted` as a harmless leftover the next run sweeps.

## Steps

- [ ] 1. [model: haiku] Create the failing-test scaffold directory + pytest dependency.
  - Files: `requirements-dev.txt` (new; single line `pytest`), `tests/conftest.py` (new, minimal placeholder OK — full fixture lands in step 3).
  - Details: Add `pytest` to a new `requirements-dev.txt` (do NOT add it to runtime `requirements.txt` — it is a test-only dep; keep runtime deps clean). Create an empty-but-importable `tests/conftest.py`. Do not delete `tests/.gitkeep`. Do NOT install anything yet (step 5 / Verification installs into `.venv`).
  - Acceptance: `requirements-dev.txt` exists with `pytest`; `tests/conftest.py` exists and is valid empty Python.

- [ ] 2. [model: opus] Implement the two-rename swap inside `cmd_replace`.
  - Files: `main.py` (only the body of `cmd_replace`, ~lines 876-899; add the stale sweep just before the swap).
  - Details:
    - Keep lines 857-874 (load, uploaded guard, path building, `make_video_dummy`) BYTE-IDENTICAL.
    - Introduce `tobedeleted = original + ".tobedeleted"` (sibling of `original`, same volume — required for atomicity).
    - STALE SWEEP (per Open Decision 2, default = option (a)): before the swap, if `os.path.exists(tobedeleted)`, remove it (best-effort; wrap in try/except so a lock here can't abort the replace). Print a one-line warning that a stale leftover from a prior interrupted run was cleaned.
    - Replace the `os.remove(original)` retry loop with a `os.rename(original, tobedeleted)` retry loop: SAME structure — `for attempt in range(3)`, `os.chmod(original, stat.S_IWRITE)` then `os.rename(original, tobedeleted)`, `except PermissionError: print retry msg; time.sleep(1)`, `except Exception: print error; return False`, and the post-loop `if not removed: print PERMISSION DENIED + close-players hint; return False`. Keep the same emoji/message wording. Rename the local flag `removed` -> `moved` (or keep `removed` to minimize diff — executor's choice, note in commit).
    - On the rename line add the seam comment EXACTLY: `# ROLLBACK SEAM: original removed from its path here (atomic commit / point-of-no-return)`.
    - Keep the `if os.path.exists(original):` guard around the retry loop (so a missing original — e.g. partial prior run — skips straight to the dummy-rename, matching today's behavior).
    - After the guard: `os.rename(tmp_path, original)` (unchanged single-attempt line).
    - Then (per Open Decision 1, default = option (b)): attempt `os.remove(tobedeleted)` inside try/except; on failure print a single WARNING line (do NOT `return False` — the replace already succeeded). Only attempt this if the file exists.
    - Keep lines 901-904 (`status="archived"`, `save_library`, success print, `return True`) BYTE-IDENTICAL.
    - Do NOT extract a helper; the rollback feature needs each step visibly in-line (per RELATED_IMPROVEMENTS "Don't bury the rename sequence behind a helper").
  - Acceptance: On the success path the function returns `True`, the dummy lives at `original`'s path, `status="archived"`, and no `.tobedeleted` or `.dummy_tmp` file remains. Reading the diff, a reviewer can point to exactly one line as the commit/seam. The 3-retry PermissionError behavior is visibly preserved.

- [ ] 3. [model: sonnet] Build the pytest sandbox fixtures (no real `C:\Media`, no ffmpeg).
  - Files: `tests/conftest.py`.
  - Details: Provide fixtures that let `cmd_replace` run fully against a temp dir:
    - `monkeypatch` `main.LIBRARY_MOVIES`, `main.LIBRARY_SERIES`, `main.LIBRARY_ANIME` to paths under a `tmp_path` sandbox so `load_library`/`save_library` only touch the sandbox. Assert in the fixture that none of these point under `C:\Media` (hard guard against accidents).
    - A `sandbox_entry` fixture that: creates a fake media folder under `tmp_path`, writes a fake "original" file with known bytes (e.g. `b"ORIGINAL-MASTER-BYTES"`), and seeds the in-sandbox library JSON with a leaf entry (`status="onboarded"`, `uploaded=True`, `folder_path`, `filename`, minimal fields `cmd_replace` reads).
    - A `fake_dummy` fixture/helper that monkeypatches `main.make_video_dummy` to write a tiny valid-enough placeholder file (e.g. `b"DUMMY"`) to the given `tmp_path` arg and return `True` — so tests never invoke ffmpeg. Make the fake honor the same contract (writes to the temp path it is given, returns bool).
    - Ensure `sys.path` includes the repo root so `import main` works when pytest runs from repo root (add a `conftest.py` at repo root OR insert repo root into `sys.path` inside `tests/conftest.py`).
  - Acceptance: A throwaway test that calls `cmd_replace(entry_id)` with these fixtures returns `True` and leaves the dummy bytes at the original path — all within `tmp_path`, with zero access to `C:\Media`.

- [ ] 4. [model: sonnet] Write the C9 test cases.
  - Files: `tests/test_cmd_replace.py`.
  - Details: Using the fixtures from step 3, implement:
    - `test_happy_path`: original exists -> `cmd_replace` returns `True`; the file at `original`'s path now contains the DUMMY bytes; no `.tobedeleted` and no `.dummy_tmp` leftover; entry `status == "archived"`.
    - `test_crash_between_renames` (THE key test): monkeypatch `main.os.rename` (or `os.rename` as imported by `main`) so the FIRST call (`original -> .tobedeleted`) succeeds but the SECOND call (`tmp -> original`) raises (e.g. `OSError` / simulated kill). Assert that after the exception the ORIGINAL bytes are still on disk — recoverable at `<original>.tobedeleted` (the dummy did not overwrite anything and the master is not lost). This proves the no-data-loss invariant. (Use a side-effect counter or match on the call arguments to fail only the second rename.)
    - `test_stale_tobedeleted_swept`: pre-create a stale `<original>.tobedeleted` before calling `cmd_replace`; assert the run completes successfully and the stale file is gone afterward (validates Open Decision 2 default behavior).
    - `test_locked_file_retry`: monkeypatch the first rename to raise `PermissionError` twice then succeed; assert the retry loop recovers and the final result is `True` (validates the preserved 3-retry logic). Patch `time.sleep` to a no-op so the test is fast.
    - `test_dummy_creation_failure_aborts`: make the `make_video_dummy` fake return `False`; assert `cmd_replace` returns `False`, the original is UNTOUCHED, and no `.tobedeleted` exists (abort before any rename).
  - Acceptance: All five tests pass under `pytest tests/ -v` using the `.venv` interpreter; none reads or writes anything under `C:\Media`.

- [ ] 5. [model: haiku] Update docs to reflect the new replace sequence.
  - Files: `ARCHITECTURE.md` (§7.6 cmd_replace flow steps 4-5; §10 Stage 3 steps 4-5), and the Quick-Reference dummy-format line if it cites the old `main.py:778-780` range (verify; only fix if now wrong).
  - Details: Change the "delete original then rename dummy" prose to "rename original -> `.tobedeleted` (atomic commit), rename dummy -> original (atomic), delete `.tobedeleted`". Mention that a crash now leaves either the original or the dummy. Keep edits surgical — do not rewrite surrounding paragraphs.
  - Acceptance: ARCHITECTURE.md no longer describes a delete-before-rename gap for `cmd_replace`; line refs are not knowingly left wrong.

## Risks and edge cases
- **pytest not installed anywhere.** Verified: neither system Python nor `.venv` has pytest. The Verification step must install it into `.venv` (`.venv\Scripts\python.exe -m pip install pytest`). The orchestrator must run tests with the `.venv` interpreter, not bare `python`. This is the first real test in the repo.
- **ffmpeg dependency in `make_video_dummy`.** Real `make_video_dummy` shells out to ffmpeg (Emby path). Tests MUST monkeypatch it (step 3) so CI/sandbox has no ffmpeg dependency. The C9 change does not touch `make_video_dummy` itself.
- **Atomicity assumption holds only for same-volume renames.** `tmp_path`, `original`, and `tobedeleted` are all siblings in `local_folder`, so this is always a same-volume rename (atomic on NTFS). If a future change ever placed the temp on a different drive, atomicity would break — note this invariant in a comment.
- **Cross-volume note.** `os.rename` on Windows raises if the target is on a different volume; not a concern here (all siblings), but worth the inline note for the rollback author.
- **`os.rename` over an existing target.** The stale-sweep removes any pre-existing `.tobedeleted` first, so step 3's rename targets a free name. Step 4's `os.rename(tmp, original)` targets a name that is now FREE (original was just moved away), so it does not need overwrite semantics — matches today's behavior where the original was deleted first.
- **Seam discoverability for auto-rollback.** The inline `# ROLLBACK SEAM:` comment plus keeping the sequence un-helpered is the contract RELATED_IMPROVEMENTS asks for. Do not refactor into a helper.
- **Root `/PLAN.md` conflict (IMPORTANT).** The repo-root `PLAN.md` currently contains the AUTO-ROLLBACK draft plan, not C9. The task says "keep the root copy in sync if orchestration reads it" — but blindly overwriting root `PLAN.md` with THIS C9 plan would clobber the auto-rollback draft. RECOMMENDATION: do NOT overwrite root `PLAN.md`. The canonical C9 plan lives only in this subfolder. If the orchestrator strictly requires a root `PLAN.md` for C9, copy this file there ONLY after the user confirms it is safe to displace the auto-rollback draft (which is itself gitignored per ARCHITECTURE.md §3). Flagged as a process question, not a code change.
- **Windows file locks during the stale sweep.** If a stale `.tobedeleted` is itself locked (rare), the sweep's try/except must not abort the replace; worst case the run proceeds and the lock surfaces at the rename. Acceptable.

## Verification
Run from repo root `C:\Users\harin\PycharmProjects\MediaVault`:
- `.venv\Scripts\python.exe -m pip install pytest` (one-time bootstrap).
- `.venv\Scripts\python.exe -m pytest tests\ -v` — all five tests pass.
- `.venv\Scripts\python.exe -c "import ast; ast.parse(open('main.py').read())"` — main.py still parses.
- Manual smoke (SAFE — uses a throwaway copy, never real media):
  1. Copy any small video to a temp folder, `prep` it under a throwaway id, `set_uploaded <id>` (forces `uploaded=True`), then `python main.py replace <id>` and confirm `✅ Replaced/Archived` + the dummy is in place + no `.tobedeleted`/`.dummy_tmp` leftover. Use a temp library by editing constants locally OR just inspect the on-disk result; DO NOT run against a real `C:\Media` entry.
- Diff review: confirm `cmd_replace` lines 857-874 and 901-904 are byte-identical to `origin/main`; the only changes are the swap block.

## Out of scope
- The auto-rollback feature itself (this only leaves the seam).
- `cmd_replace_group`, `cmd_repair_dummies`, `cmd_restore`, `cmd_push` — untouched.
- A `prune_dummies` / lazy `.tobedeleted` sweep command (IMP-D6) — referenced by the spec as a future home for cleanup, but NOT built here.
- Any change to `make_video_dummy`, the dummy recipes, or ffmpeg handling.
- Anything under `archive/`.
- A full pytest harness / library fixtures formalization (IMP-A7) — this only seeds the minimal first tests; A7 later extends them.
- Refactoring the duplicated retry/print style or adding a `logging` module.

## Open Decisions — RESOLVED (2026-05-29)

### Decision 1 — What to do if deleting `.tobedeleted` (step 5) fails?
**RESOLVED: (b) Log a one-line WARNING and leave it.**
The replace has already succeeded. Print a warning; do not `return False`. The next run's stale-sweep (Decision 2) will clean it up.

### Decision 2 — What to do if a stale `.tobedeleted` is found at the START of the next run?
**RESOLVED: (a1) Safe sweep — restore if no live file exists at original path, else delete.**
If `original` already has a real (non-dummy) file, the `.tobedeleted` leftover is redundant → delete it.
If `original` has no file (crash happened between step 3 and step 4 — master is sitting at `.tobedeleted`), restore it by renaming `.tobedeleted` → `original` before proceeding.
The test `test_stale_tobedeleted_swept` must validate both sub-cases.

## Completion checklist (run at the very end)
- [ ] Mark IMP-C9 `Status: pending` -> `Status: done` in `improvements_tierC.md` (the IMP-C9 section).
- [ ] Fill in the "Completion report" in `docs/feature-auto-rollback/C9-atomic-replace/C9-atomic-replace.md` (branch, PR, commit, files changed, tests added, manual test commands, open-decisions-resolved, notes).
- [ ] Update `ARCHITECTURE.md` §7.6 and §10 Stage 3 (done in step 5) — verify before closing.
- [ ] Optionally note in `FAILURE_ANALYSIS.md` that Example B's window is closed by C9.
- [ ] Confirm branch is `fix/atomic_replace` off `origin/main`.
- [ ] Open a PR to `main`. In the PR body, state this was done as an auto-rollback prerequisite (per README.md onboarding) and call out the `# ROLLBACK SEAM:` line for the rollback author.
- [ ] Manual test commands documented in the PR / completion report (the SAFE throwaway-copy smoke from Verification).
- [ ] Decide the root `/PLAN.md` sync question (Risks) with the user — do NOT clobber the auto-rollback draft without confirmation.
- [ ] Tests green: `.venv\Scripts\python.exe -m pytest tests\ -v`.
