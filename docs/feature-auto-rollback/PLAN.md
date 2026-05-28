# Task: Auto-rollback for multi-step commands (reversible -> rollback, irreversible -> actionable hard-fail)

Suggested branch: feature/auto_rollback
> BRANCH FROM `origin/main`, NOT the current `feature/video_dummy` branch. origin/main is verified up-to-date and code-identical to the working tree, so branching off it loses nothing. The orchestrator / git-agent MUST create `feature/auto_rollback` off `origin/main`.

> STATUS: DRAFT — pending user confirmation of the two decision sections below. Do NOT begin orchestration until the user has answered the Open Decisions and chosen which Related Improvements (if any) to fold in.

---

## Open Decisions — REQUIRES USER CONFIRMATION

Each decision lists the question, realistic options, and my recommended default (with a one-line rationale). Numbers 1 and 6 are the load-bearing ones.

### 1. The complete list of IRREVERSIBLE (hard-fail) scenarios and the exact "point of no return" per command
This is the list the user explicitly asked to confirm. Two state changes are genuinely irreversible because they destroy the only local copy of bytes:

- **(A) Push, after a chunk is uploaded-then-locally-deleted.** In `cmd_push` (main.py:773-779), after each successful `adb push` the local chunk is `os.remove`'d if its path contains `_parts`. The moment chunk N is deleted, the local artifact for chunk N is gone. If push then fails on chunk N+1, we CANNOT rebuild the deleted chunks without re-splitting the original — and a partial `_parts` folder no longer contains them. **Point of no return: the first `os.remove(f)` of a successfully-pushed chunk.** Before that first delete (e.g., split done, hashing done, mkdir done, even chunk 1 mid-upload), everything is reversible.
   - Note: the LAST chunk is the same — if all-but-one uploaded+deleted and the last fails, the deleted chunks are gone.
- **(B) Replace, after the original file is deleted.** In `cmd_replace` (main.py:877-899) the original is `os.remove`'d, then the dummy is renamed into place. **Point of no return: the `os.remove(original)`.** Failure of the subsequent `os.rename` leaves only the dummy temp + a missing original (a window IMP-C9 addresses; see Related Improvements). For our purposes: once the original is deleted, prep/push/replace pipeline cannot "undo" back to having the original file.

Additional hard-fail / non-rollback scenarios I found that the user should confirm are treated as hard-fail (clear message, no fake rollback):

- **(C) Restore, split path, after merge has overwritten the on-disk file and chunks are deleted.** `cmd_restore` (main.py:1061-1082) merges chunks to `target_path`, re-hashes, overwrites `entry["hash"]`, then deletes the restore-folder chunks. If the process dies AFTER chunks are deleted but the merged file is in place, re-running is fine (idempotent-ish), but the OLD `entry["hash"]` is gone (overwritten by the merged-container hash — intentional per mkvmerge divergence memo). This is not a "this-run-created-X" rollback case for the archive pipeline; restore is a separate lifecycle. Recommend: restore failures are NOT in scope for auto-rollback (see Decision 8 / Out of scope), but if we DO wrap restore, the irreversible point is "chunks deleted from restore/".
- **(D) Push to a remote that partially succeeded but state was already flipped.** Not reachable today: `cmd_push` only flips `uploaded/status` after ALL chunks succeed (main.py:790-800). No partial state flip. Confirm we keep that invariant.
- **(E) `cmd_set_uploaded`** is a pure metadata flip with no file ops — not multi-step, not in scope. Confirm exclude.
- **(F) Cross-command interleaving / external mutation.** If Google Photos already ingested an uploaded chunk, deleting the phone copy or the local copy is still "irreversible" from MediaVault's view (we never re-download in a rollback — per the user's "do NOT invent a fetch-to-fix" rule).

   - **Options for handling (A)/(B):** (i) hard-fail with actionable message [recommended]; (ii) attempt partial rollback of only the still-local artifacts (rejected — leaves a confusing mix and violates "don't fake a rollback"); (iii) auto-resume (rejected — out of scope, that's IMP-C1).
   - **My recommendation:** Treat (A) and (B) as the canonical irreversible points. On hitting them, print: what state the system is in (which chunks are uploaded, which remain; or "original deleted, dummy in place"), exactly why rollback is impossible ("local chunks already deleted after upload" / "original file already deleted"), and which EXISTING command resumes/repairs it (`push <id> chunks N-M` to finish remaining chunks; `replace <id>` already complete; `set_uploaded` then `replace` for the multi-part rescue). No new commands invented.
   - **Confirm:** Is this list (A-F) complete and correct, and is hard-fail (not partial rollback) the right behavior for (A)/(B)?

### 2. How to detect "this run created X" vs. "X pre-existed" (so rollback only undoes this run)
- **Options:** (a) **Snapshot-before**: at command start, capture which artifacts already exist (library entry present? parent present? did this run's child-link already exist? `_parts`/`checksums`/sidecars present? `split_info` present?) and on rollback only remove artifacts that were absent in the snapshot; (b) per-action journal that records each created artifact as it is created and replays deletions on failure; (c) heuristic "delete everything that looks like this id's artifacts" (rejected — would clobber pre-existing state on a re-prep/resume).
- **My recommendation:** **(a) snapshot-before**, augmented by recording actions actually taken (a hybrid leaning on the snapshot for the "pre-existed?" question). Snapshot captures: `entry_existed` (bool), `parent_existed` (bool), `child_was_already_in_parent` (bool), `split_info_existed` (bool), `prior status/uploaded values`, and a set of pre-existing on-disk paths (`uid`, `<short_id>.sha256`, `_parts/`, `checksums/`, the chunk sidecars). Rollback removes only the set-difference (created-this-run).
- **Confirm:** snapshot-before is the detection mechanism. (This choice is partly settled by Decision 6's architecture vote.)

### 3. Season parent `season_map`: delete parent if this run created it and we roll back its only child?
- **Options:** (a) if this run CREATED the parent season_map and rollback removes the only child this run added, also delete the now-childless parent it created; if rollback would leave the parent with 0 children but the parent PRE-EXISTED, leave the empty parent (don't delete pre-existing state); (b) always leave parents; (c) always delete empty parents (rejected — could delete a pre-existing parent).
- **My recommendation:** **(a).** Delete the parent only if BOTH (this run created it) AND (removing this run's child leaves it with 0 children). If the parent pre-existed, only remove the child-link this run added and recompute `total_episodes`; never delete the parent.
- **Confirm:** matches the user's stated intent ("if this run created the parent and we roll back its only child, delete the parent too; if parent pre-existed, only remove the child link this run added").

### 4. Auto-rollback vs. prompt-before-rollback
- **Options:** (a) automatic rollback + clear message [recommended]; (b) prompt y/N before rolling back.
- **My recommendation:** **(a) automatic**, with a clear, explicit post-rollback message ("Rollback complete. System is back to its exact pre-command state."). Single-user tool, non-interactive batch runs; prompting would stall overnight season runs.
- **Confirm:** automatic.

### 5. On rollback, also clean the (possibly empty) remote dir created by `adb mkdir`?
- **Options:** (a) leave it [recommended] — an empty `/sdcard/Media/...` dir is harmless and removing it adds an ADB round-trip + a new failure surface during rollback; (b) `adb shell rmdir` it on rollback.
- **My recommendation:** **(a) leave it.** It is harmless, Google Photos ignores empty dirs, and a re-run reuses it.
- **Confirm:** leave the remote dir.

### 6. Rollback-mechanism ARCHITECTURE (multi-candidate decision — see Step 3)
This is the one genuinely multi-approach core decision. Candidates (built and judged in Step 3):
- **A — Snapshot/transaction context-manager wrapper.** A `RollbackContext` (or `transactional_command(...)`) captures library-entry + parent + filesystem state at entry, exposes a "mark irreversible point reached" toggle, and on any exception before that toggle restores the snapshot (re-deletes created files/dirs, reverts the in-memory library dict to the snapshot and saves). Commands opt in by wrapping their body.
- **B — Explicit per-command compensating-action functions.** Each `cmd_*` registers compensations as it goes (`undo.append(lambda: shutil.rmtree(parts_dir))`), and a small runner replays them LIFO on failure. No global snapshot; each action knows its own inverse.
- **C — On-disk operation journal / savepoint.** Append intended/completed operations to a per-run journal file (e.g., `.mediavault_txn.json`) before each mutating step; a rollback routine reads the journal and reverses entries, and the journal also survives a hard process kill for crash-recovery on next run.
- **My recommendation:** Lean toward **A** for this codebase (smallest happy-path blast radius — wrap existing logic, do not rewrite; matches the user's "prefer wrapping" constraint) but the candidates are genuinely different in debuggability, crash-survival, and intrusiveness, so this warrants the multi-candidate bake-off in Step 3.
- **Confirm:** approve running the A/B/C bake-off, or pre-pick one to skip the cost.

### 7. Test sandbox approach and which media file to copy
- The hard constraint: tests must NEVER touch real files under `C:\Media\{Movies,Series,Anime}` or the real `C:\Media\library_*.json`. Tests use COPIES in a temp sandbox.
- **Media file to copy:** the existing ~10 KB video DUMMIES under `C:\Media` are valid, real, tiny video containers and are the safest thing to copy (copying them cannot affect a real original's hash). For the SPLIT tests we need a file large enough to split into >=5 chunks; recommend **generating a synthetic multi-MB MKV with ffmpeg in a fixture** (skip the test if ffmpeg absent) rather than copying a real 80 GB remux. For no-split / prep / replace / restore single-file tests, a copied dummy (or a small ffmpeg-generated MKV) suffices.
- **Library JSONs:** copy the gitignored `resources/library_*.json` snapshots (if present) OR synthesize minimal fixture JSONs into the temp sandbox; monkeypatch `LIBRARY_MOVIES/SERIES/ANIME` (and `LOCAL_ROOT`) to point at the sandbox so `load_library`/`save_library` operate only on copies.
- **Failure simulation:** monkeypatch `subprocess.run` to fail on the Nth `adb push` (push-fail), to fail on the mkvmerge split call (disk-full / split-fail), and to fail the fetch dispatch (fetch-fail); monkeypatch `os.remove`/`shutil` or `os.statvfs`-equivalent where disk-full needs simulating; monkeypatch `make_video_dummy`/`os.remove` to force replace failure.
- **Options:** (a) sandbox via monkeypatched constants + ffmpeg-generated fixtures [recommended]; (b) copy a real small dummy for everything and skip split tests; (c) use a full real remux (rejected — slow, risky, huge).
- **My recommendation:** **(a).**
- **Confirm:** sandbox approach + "generate synthetic MKV via ffmpeg for split tests, skip if ffmpeg missing; copy a dummy for single-file tests" is acceptable.

---

## Related Improvements (from improvement tiers)

For each: tier + item, why it relates, and implement-now vs defer. **Default recommendation for ALL of these: DEFER** — they are separately-scoped tasks with their own plans, and folding them in risks violating the "happy path byte-for-byte identical / surgical change" constraint. Surface them so the user can decide.

- **IMP-C1 (Tier C) — Auto-resume from last completed episode in `cmd_prep_push_rep_season`.** Directly adjacent: our season rollback prints "how to resume episodes N-M"; C1 would AUTO-resume from a `.mediavault_progress.json`. Our task delivers the message + single-item rollback; C1 delivers the auto-resume. **Recommend DEFER** (this task does the messaging; C1 is the bigger resumability feature). Folding in would expand scope significantly.
- **IMP-C9 (Tier C) — Atomic `cmd_replace` via two-rename pattern.** Directly relevant to the replace "point of no return." C9 closes the power-loss window between deleting the original and renaming the dummy (rename original->`.tobedeleted`, rename dummy->original, then delete). It does NOT make replace reversible (once committed, original is gone) but it shrinks the irreversible window and makes a killed replace recoverable. **Recommend: surface as a strong candidate to fold in** because it touches the exact code our hard-fail analysis depends on — but DEFAULT DEFER to honor the surgical-change rule unless the user wants the safety upgrade now.
- **IMP-C11 (Tier C) — Hash-mismatch quarantine in `cmd_restore`.** Relevant if we extend rollback to restore: a failed restore today leaves a bad file in `restore/`; C11 quarantines it so re-fetch self-heals. Relates to "leave the system in a clear state on failure." **Recommend DEFER** (restore is out of scope for rollback per Decision 8).
- **IMP-C2 (Tier C) — Exponential-backoff retry for ADB/Selenium.** Relates as a complement: retry reduces how often we hit a rollback at all (transient USB blips self-heal before triggering rollback). Orthogonal mechanism. **Recommend DEFER.**
- **IMP-G1 (Tier G) — rclone chunker patterns: upload to `.partial` then atomic remote rename + remote `.mvmeta.json`.** Relates to the push irreversibility story: uploading to a `.partial` name and only renaming on success means a partial upload is never observable as complete — which narrows the "irreversible" surface and improves crash-safety. **Recommend DEFER** (changes the remote upload protocol; bigger than this task).
- **IMP-A1 (Tier A) — Extract `mvcommon.py`.** Relates only as foundation (shared `load_library`/`save_library`). Our rollback code will live in `main.py`; do NOT pull A1 in. **Recommend DEFER.**
- **IMP-A7 (Tier A) — Pytest harness with library fixtures.** This task CREATES the first real tests in `tests/`. We are effectively bootstrapping part of A7's harness (conftest, sandbox fixtures). **Recommend: build the minimal harness here, and note in A7 that the rollback tests seed it** — but do not attempt full A7 coverage (regex/round-trip/etc.). Coordinate naming so A7 later extends it.

> If the user says "yes, fold in C9" (or any other), the orchestration phase will implement it and mark that item's status in the tier file. This plan does NOT mark anything done.

---

## Context
MediaVault's multi-step commands (`cmd_prep_push_rep`, `cmd_prep_push_rep_season`, and the underlying `cmd_prep`/`cmd_push`/`cmd_replace`) can fail mid-way and leave a confusing half-finished state. Two ad-hoc rollback behaviors already exist and disagree: `cmd_prep_push_rep` deletes `_parts/` and prints a "local_ready" message on push failure (main.py:1396-1411), while `cmd_prep_push_rep_season` just `break`s "to prevent mess" (main.py:1483). This task unifies failure handling into one predictable mechanism: reversible failures auto-roll-back to the exact pre-command state; irreversible failures hard-fail with an actionable message that names an existing resume command; batch failures keep completed items and tell the user how to resume the rest.

## Goal
A single rollback mechanism, used by all multi-step commands, such that:
1. Any failure BEFORE a command's documented point-of-no-return restores the exact pre-command state (only artifacts THIS run created are removed: new library entry, this-run's parent season_map / child link, sidecars `uid` + `<short_id>.sha256`, `_parts`/`checksums` dirs, and any `split_info`/`status`/`uploaded` fields this run set). Pre-existing state is never touched. A clear "rollback complete" message prints.
2. Any failure AT/AFTER the point-of-no-return hard-fails with a message stating current state, why rollback is impossible, and which EXISTING command to use. No new commands invented; no fake rollback.
3. Batch/season failures leave completed items intact, roll back only the in-flight item if it is reversible, and print the exact resume command (e.g., `prep_push_rep_season <id> <folder> ... episodes 5-10`).
4. The happy path of every existing command is behaviorally byte-for-byte identical. The two ad-hoc rollback paths are replaced by the new mechanism (no competing mechanisms left).
5. Tests in `tests/` cover the enumerated scenarios using copies only.

## Files affected
- `main.py` — add the rollback mechanism (chosen architecture); wire it into `cmd_prep`, `cmd_push`, `cmd_replace`, `cmd_prep_push_rep`, `cmd_prep_push_rep_season`; replace the two ad-hoc rollback paths. This is the only production file touched.
- `tests/conftest.py` (new) — sandbox fixtures (temp library JSONs, temp media folder, ffmpeg-fixture MKV, monkeypatch helpers for failure injection).
- `tests/test_rollback.py` (new) — scenario tests (reversible + irreversible + season + fetch).
- `tests/__init__.py` (new, if needed for import resolution) — empty.
- `mainfetch.py` — NOT modified (fetch failure is simulated at the dispatch boundary; fetch_restore rollback is out of scope per Decision 8). Confirm during planning that no change is needed.

## Approach
Introduce ONE rollback primitive (architecture chosen in Step 3). The primitive (a) takes a snapshot of the relevant pre-command state for an id (library entry presence + values, parent presence + whether this id is already a child, `split_info` presence, and the set of pre-existing on-disk artifact paths), (b) tracks whether the command has crossed its point-of-no-return, and (c) on failure-before-PONR restores exactly the snapshot (removing only created artifacts, reverting the in-memory library and saving), or on failure-at/after-PONR raises/returns a structured hard-fail that the orchestrators turn into the actionable message. Each `cmd_*` is WRAPPED, not rewritten: the existing happy-path code runs unchanged; we add a snapshot at the top and a failure handler that triggers rollback or hard-fail. The orchestrators (`cmd_prep_push_rep`, `cmd_prep_push_rep_season`) drop their ad-hoc cleanup and delegate to the new mechanism; the season orchestrator additionally computes and prints the resume range from the failed episode onward.

## Steps

- [ ] 1. [model: sonnet] Write a behavior-baseline characterization document AND a happy-path smoke harness for the five target functions, so we can prove "no happy-path change" before/after.
  - Files: `tests/conftest.py` (new), `tests/test_baseline_happy_path.py` (new)
  - Details: Build the sandbox fixture (temp dir; copy/generate a small valid MKV; write minimal `library_movies.json`/`library_series.json`/`library_anime.json` into the sandbox; monkeypatch `main.LIBRARY_MOVIES/SERIES/ANIME` and `main.LOCAL_ROOT` to the sandbox). Add a fixture that generates a multi-MB MKV via ffmpeg (skip-marker if ffmpeg unavailable). Add monkeypatch helpers: `fail_adb_on_nth_push(n)`, `fail_mkvmerge_split()`, `fail_fetch_dispatch()`. Write happy-path tests that run `cmd_prep` (no-split) and a mocked `cmd_push` (adb push monkeypatched to succeed, mkvmerge split monkeypatched to produce fake chunk files) end-to-end and assert the resulting library entry + on-disk artifacts match the documented success state. These are the regression guardrails for Steps 3-6.
  - Acceptance: `pytest tests/test_baseline_happy_path.py` passes against UNMODIFIED `main.py`; fixtures never reference any path under real `C:\Media\{Movies,Series,Anime}` or real `library_*.json`.

- [ ] 2. [model: opus] Produce the authoritative point-of-no-return + artifact map (design note in code comments / docstrings only, no behavior change) and the exact snapshot/restore contract.
  - Files: `main.py` (docstrings/comments near the rollback primitive location only; no logic yet)
  - Details: For each of `cmd_prep`, `cmd_push`, `cmd_replace`: enumerate, with line references, (a) every artifact created/mutated and in what order, (b) the precise point-of-no-return, (c) the exact inverse action for each reversible artifact (delete file, rmdir, pop dict key, restore prior field value, remove child from parent + recompute total_episodes, delete this-run-created parent). Define the snapshot data shape (fields from Decision 2). Lock down the parent-deletion rule (Decision 3) and the "leave remote dir" rule (Decision 5). This is the spec Step 3 implements against.
  - Acceptance: The map covers all artifacts named in the task (library entry, parent season_map + child link, `uid`, `<short_id>.sha256`, `_parts`, `checksums`, `split_info`, `status`, `uploaded`) and matches the confirmed answers to Decisions 1-5. No runtime behavior changed (Step 1 baseline still green).

- [ ] 3. [model: opus] [candidates: 3] Implement the core rollback mechanism (snapshot + restore + point-of-no-return + hard-fail signaling) as a self-contained primitive in `main.py`.
  - Files: `main.py` (new primitive only; integration is Step 4)
  - Details: Implement the snapshot/restore/PONR primitive per the Step 2 spec. Must expose: take-snapshot for an id, a way for command code to signal "point of no return reached" (after which rollback is disabled and failures become hard-fail), a `rollback()` that removes only created artifacts and reverts the in-memory library + saves, and a structured hard-fail carrier (state description + reason + suggested existing command). Must NOT alter happy-path behavior when no failure occurs.
  - Acceptance: Unit tests (added in this step) prove: snapshot of a pre-existing entry then rollback is a no-op on pre-existing artifacts; snapshot of a fresh entry then rollback removes exactly the created artifacts and reverts the library dict; PONR toggle converts a post-PONR failure into a hard-fail object instead of a rollback. Step 1 baseline remains green.
  - Judge criteria: (1) Correctness — only this-run artifacts removed, pre-existing untouched, library dict reverted exactly (verified by the step's unit tests and the Step 1 baseline); (2) Minimal happy-path intrusion — wrapping over rewriting, smallest diff to existing `cmd_*` bodies in the eventual integration; (3) Crash-survival / debuggability — behavior if the process is killed mid-rollback, and how inspectable the in-flight state is; (4) Readability for a solo maintainer with no type hints / procedural style matching the existing codebase.
  - Candidate approaches:
    - A: Snapshot/transaction context-manager wrapper — capture library-entry + parent + filesystem artifact set at `__enter__`, expose `mark_point_of_no_return()`, restore the snapshot on any pre-PONR exception in `__exit__`.
    - B: Explicit per-command compensating-action stack — command code pushes inverse closures (`undo.append(lambda: shutil.rmtree(parts_dir))`) as it creates each artifact; a runner replays them LIFO on failure; PONR clears the stack.
    - C: On-disk operation journal/savepoint — append each intended mutation to a per-run `.mediavault_txn.json` before doing it; a rollback routine replays inverses from the journal; the journal also enables crash-recovery on the next invocation.

- [ ] 4. [model: opus] Integrate the chosen mechanism into `cmd_prep`, `cmd_push`, `cmd_replace` by WRAPPING existing logic (no success-path rewrite).
  - Files: `main.py` (`cmd_prep` 388-480, `cmd_push` 634-806, `cmd_replace` 857-904)
  - Details: Wrap each function so it snapshots at entry and, on a reversible failure, rolls back and prints the "rollback complete, back to pre-command state" message; on an irreversible failure prints the hard-fail message naming the existing resume command. In `cmd_push`, set the point-of-no-return at the first successful-chunk `os.remove` (main.py:777). In `cmd_replace`, set PONR at the `os.remove(original)` (main.py:884). In `cmd_prep` there is no PONR (fully reversible) — its only failures (hash fail, file missing) become clean rollbacks. Preserve all existing return values and the existing `cmd_prep` early-skip / dummy-detection short-circuits (those return True without creating artifacts — must NOT trigger rollback). Do NOT change which fields are written on success.
  - Acceptance: Step 1 baseline happy-path tests still pass unchanged. New tests: prep-fail rolls back; push-fail-before-first-chunk-delete rolls back (split artifacts + `split_info` + entry removed if newly created); push-fail-after-partial-upload hard-fails with a message naming `push <id> chunks N-M`; replace-fail-before-original-delete rolls back the dummy temp; replace at/after original delete hard-fails. Re-run (resume) on a pre-existing `_parts` is NOT rolled back (pre-existing artifact).

- [ ] 5. [model: opus] Unify the orchestrators onto the new mechanism and add season resume-range messaging; remove the two ad-hoc rollback paths.
  - Files: `main.py` (`cmd_prep_push_rep` 1383-1422, `cmd_prep_push_rep_season` 1425-1486)
  - Details: In `cmd_prep_push_rep`, delete the ad-hoc `_parts` rmtree + "local_ready" message (1396-1411) and rely on the wrapped `cmd_push`'s rollback/hard-fail. In `cmd_prep_push_rep_season`, replace the bare `break` (1483) with: completed episodes stay; if the failing in-flight item is reversible, it has already rolled itself back (via wrapped `cmd_push`); compute the resume range from the failed episode's number through the end of the (filtered) target list and print the exact resume command including the original `SIZE_*`/`device`/`episodes` args. Keep the already-uploaded-skip behavior (1465-1469) intact. Ensure no second rollback mechanism remains.
  - Acceptance: New tests: a 10-item season failing on item 5 (push-fail) leaves items 1-4 intact, rolls back item 5 if reversible, and prints a resume command covering episodes 5-10 with the same split/device args; single-movie pipeline push-fail produces the unified rollback message (not the old "Reverting temporary files" text). Grep confirms the old ad-hoc cleanup code is gone.

- [ ] 6. [model: sonnet] Complete the scenario test matrix and document the test approach inline.
  - Files: `tests/test_rollback.py` (new), `tests/conftest.py` (extend)
  - Details: Implement/finish tests for each required scenario, each documented with a docstring stating what failure is simulated and what state is asserted: split push-fail-before-upload (reversible), no-split push (reversible up to PONR), push-fail-after-partial-upload (irreversible hard-fail), prep-fail (reversible), replace-fail before original delete (reversible) and after (irreversible), season mid-failure (completed kept + resume message + single-item rollback), and fetch-fail (simulate `cmd_dispatch_fetch` failure; assert fetch_restore behavior — per Decision 8, assert it does NOT fake a rollback and prints a clear message, since restore-side state is out of scope). Confirm disk-full-during-split is simulated by forcing the mkvmerge split call to fail (and asserting split artifacts + entry are rolled back).
  - Acceptance: `pytest tests/` passes; every scenario in the constraints list has at least one test; tests touch only sandbox copies; ffmpeg-dependent split tests skip cleanly when ffmpeg is absent.

## Risks and edge cases
- **`cmd_prep` early-skip paths** (already uploaded/archived; dummy < `DUMMY_MAX_BYTES`) return True without creating artifacts — the wrapper must treat these as success, never rollback.
- **Resume semantics collision:** `cmd_push` intentionally resumes from a pre-existing `_parts` folder (main.py:680-683). Rollback must NOT delete a `_parts` that pre-existed this run — the snapshot must record `_parts` presence at entry.
- **`split_info` may pre-exist** from a prior interrupted push; rollback must only remove `split_info` if THIS run wrote it.
- **Parent season_map shared across episodes:** in a season run, the parent is created during episode 1's prep; rolling back episode 5 must NOT delete the parent (it pre-existed relative to episode 5's snapshot) nor remove episodes 1-4's child links. Per-id snapshots handle this if each `cmd_prep`/item takes its own snapshot.
- **`save_library` writes all three JSONs atomically** (main.py:118-129); rollback reverting the in-memory dict then calling `save_library` is consistent with existing behavior — but a rollback that itself fails mid-`save_library` is a (rare) hard edge; document behavior (Decision 6 candidate C survives this best).
- **`os.remove` of a chunk is wrapped in `try/except: pass`** today (main.py:776-779) — a "successful upload" whose local delete silently fails still counts as past-PONR; ensure PONR is keyed on upload success, not on delete success.
- **Bare `except` in `cmd_push` mkdir** (main.py:672-674) returns False before any artifact is created — clean rollback (no-op) expected.
- **Windows file locks** (Plex/Windows Search holding a file) can make a rollback `os.remove`/`rmtree` fail; rollback should report partial-rollback honestly rather than claim full success it didn't achieve.
- **Ambiguity:** whether `cmd_fetch_restore`/`cmd_restore` get rollback at all — currently scoped OUT (Decision 8). If the user wants restore covered, that adds Step(s) and changes the irreversible list (item C).

## Verification
- `pytest tests/ -v` (all scenario + baseline tests green; ffmpeg-gated split tests skip if ffmpeg missing).
- Behavioral diff check: run `cmd_prep` (no-split) and the mocked-adb `cmd_push` happy paths before and after the change against the sandbox; assert identical library entries and on-disk artifacts (Step 1 harness).
- `Grep` for the removed ad-hoc strings ("Reverting temporary files", the season `break` comment "to prevent mess") to confirm the old mechanisms are gone.
- Manual read-through diff confirming every changed line traces to rollback wrapping (no incidental edits to success-path logic).
- Confirm `mainfetch.py` is unmodified (`git diff --stat` shows only `main.py` + `tests/`).

## Out of scope
- `cmd_restore` / `cmd_restore_group` / `cmd_fetch_restore` rollback (fetch-side state). Fetch failures are tested only to confirm no fake rollback + clear message; restore lifecycle rollback is a separate task.
- Auto-resume from a progress file (IMP-C1), retry/backoff (IMP-C2), atomic-replace two-rename (IMP-C9), quarantine (IMP-C11), `.partial` remote rename / `mvmeta` (IMP-G1), `mvcommon` extraction (IMP-A1) — all deferred unless the user opts in via Related Improvements.
- Any new CLI command, flag, or new "fetch-to-fix"/repair command (explicitly forbidden by the user).
- Changing `save_library`'s atomic-write behavior, the split algorithm, the dummy recipes, or any success-path field values.
- Touching anything under `archive/`.
