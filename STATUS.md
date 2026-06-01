# Execution Log

Task: Auto-rollback for multi-step commands (feature/auto_rollback)
Branch: feature/auto_rollback (from origin/main @ a70d118; carried the three uncommitted plan docs over). Initial plan commit: 8b8896a.
Scope for THIS run: Steps 1 -> 2 -> 3 (flagship uncapped bake-off), then PAUSE at the user-decides gate. Steps 4 + merge + push are OUT OF SCOPE this run (D-2 user-selects the Step 3 winner first).
Note: the Task subagent tool is UNAVAILABLE in this run (same as the A1/C2/C8 runs), so the orchestrator executes every step directly with Read/Write/Edit/Bash and performs git operations inline (the git-agent's exact step sequences are followed by hand). STATUS.md is a per-step scratchpad artifact committed with the branch.
Baseline (pre-change, unmodified main.py): `pytest -q` -> 58 passed.
Model/effort policy (DECISIONS.md N-5): every logic-bearing step is opus/max; no downgrade; 3-candidate cap lifted for Step 3 (uncapped). No effort mismatches — the orchestrator runs at opus effort directly.

## Step 1 — [status: done] Behavior-baseline characterization + happy-path smoke harness
- Executor: orchestrator (direct; Task subagent tool unavailable). Model: opus, effort max (matches the step tag — no mismatch).
- Files changed: tests/conftest.py (extended, additive only), tests/test_baseline_happy_path.py (new).
- Outcome:
  - conftest.py additions (all additive; existing fixtures untouched):
    - `stub_tech_specs` — deterministic get_tech_specs stub so cmd_prep does not need pymediainfo on the fake fixture file.
    - `FailNthSubprocess` class + `fail_nth_subprocess` factory fixture — fail the Nth *matching* subprocess.run (e.g. Nth `push`) with CalledProcessError, composing over an optional inner run (e.g. mock_device) so path math / library writes still execute; records `.calls`. Also stubs mvcommon.time.sleep so retry() backoff is instant. (Extends the FakeAdb(fail_push_n=...) pattern rather than duplicating it.)
    - `fail_merge` factory fixture — patch merge_video_files to return False or raise, for the cmd_restore pre-PONR (reversible) merge-failure scenarios; records attempt count.
    - `ffmpeg_multichunk_mkv` fixture — ffmpeg-generated ~6 MB testsrc MKV for GENUINE-split tests; `pytest.skip`s cleanly when ffmpeg is absent or the invocation fails (testing-strategy §4/§11).
  - test_baseline_happy_path.py (the regression ORACLE for D-4): 7 tests snapshotting the happy-path post-state of all five target paths — cmd_prep (no-split movie: entry + uid + <short_id>.sha256, no parent), cmd_prep (season episode: parent season_map created + child linked + total_episodes), cmd_prep early-skip (uploaded -> True, ZERO artifacts), cmd_push (split via mock_device: chunk bytes land at final names, _parts cleaned, entry onboarded/uploaded), cmd_replace (fake_dummy: dummy live, status archived, no .dummy_tmp/.tobedeleted), cmd_restore split (stubbed merge: chunks merge to target + status restored_local + chunks deleted), cmd_restore standard (verified move + status restored_local + restore/ cleaned).
- Key decisions:
  - cmd_prep fixture files must exceed DUMMY_MAX_BYTES (200_000) or the @316-318 dummy-detection safety net early-skips them (hit during first run; fixed by writing DUMMY_MAX_BYTES+1 bytes). This is itself a captured fact for the Step 3 candidates: the early-skips create no artifacts and must never roll back.
  - cmd_push happy path uses `mock_device` (data round-trip) not FakeAdb — O-1 means push FAILURE is resume-message, so only the SUCCESS path is the oracle here; failure scenarios live in Step 3 candidate matrices.
  - cmd_restore split uses a stubbed merge_video_files (no real mkvmerge) — mirrors test_cmd_restore_quarantine's split-success test.
- Acceptance: `pytest tests/test_baseline_happy_path.py -q` -> 7 passed against UNMODIFIED main.py. Full `pytest -q` -> 58 passed (was 58 before; conftest additions caused zero regressions). New helpers patch via the existing `sandbox` fixture (both mvcommon.LIBRARY_* and main.LIBRARY_*) — no DIY redirect; no fixture references real C:\Media or real library_*.json. PASS.

## Step 2 — [status: done] In-code PONR + artifact-map spec (design-only comments)
- Executor: orchestrator (direct; Task subagent tool unavailable). Model: opus, effort max (matches the step tag — no mismatch).
- Commit: `01b18de` (`docs(main): in-code PONR + artifact-map spec for rollback — Step 2`).
- Files changed: main.py ONLY (+123 lines, all comments — NO behavior change; `python -m py_compile main.py` clean; `git diff --stat` = main.py only).
- Outcome — transcribed the re-derived PONR table + snapshot/restore contract into main.py as the authoritative implementation spec every Step 3 candidate builds against:
  - Module-level `AUTO-ROLLBACK SPEC` block inserted before CORE COMMANDS header (location-agnostic per N-4 — does NOT presume the primitive's final home): O-2 invariant (master = source of truth; exactly two PONRs); D-6 snapshot shape (entry_existed, prior_status/uploaded, parent_id/parent_existed, child_already_linked, split_info_existed, preexisting_paths set); inverse action per reversible artifact (entry / split_info / status+uploaded / uid / <short_id>.sha256 / _parts / checksums / restore merged target / parent child-link / parent season_map per D-7); the partial-rollback-on-Windows-lock honesty rule; the PONR toggle -> structured hard-fail naming `fetch_restore <id>` (N-2); D-9 leave-remote-dir; and a per-command PONR summary.
  - Per-site `[ROLLBACK SPEC]` markers with CURRENT verified line refs: cmd_prep fully reversible + both early-skips @311-318 flagged as zero-artifact / never-roll-back; cmd_push NO rollback PONR (O-1) at the resume `_parts` branch @700 + the split_info save @736 + the chunk delete @858 (all noted resumable, never delete pre-existing _parts); cmd_replace dummy-temp @957 as the only pre-PONR artifact + the PONR seam @990 (augmented the existing C9 seam comment with pre/at-PONR behavior + the don't-double-handle-C9-stale-sweep note); cmd_restore pre-PONR C11 quarantine reuse @1202 + the chunk-delete PONR @1232 + standard-path-no-torn-window note.
- Acceptance: map names every in-scope artifact (entry, parent season_map + child link, uid, <short_id>.sha256, _parts, checksums, split_info, status, uploaded, restore merge output) with current line refs; matches D-1/D-4/D-6/D-7/D-9 + O-1/O-2. Step 1 baseline remains green: full `pytest -q` -> 58 passed (no runtime change). PASS.

## Step 3 — [status: in_progress] FLAGSHIP UNCAPPED BAKE-OFF (JUDGE-REVIEWS-ONLY / USER-DECIDES)
- Mode: multi-candidate, executed inline (Task subagent unavailable). Worktrees under .candidates/step-03/{A,B,C} (gitignored), each branched off feature/auto_rollback HEAD 80f7711 (Steps 1+2). Three genuinely-distinct schools per plan: A snapshot/transaction context-manager, B compensating-action stack, C on-disk journal. No D/E (no further genuinely-distinct strategy that isn't a cosmetic variant of these three).
- Per-candidate deliverable: primitive (3a) + wrapping integration of cmd_prep/push/replace/restore (3b) + orchestrator unification of both ad-hoc paths with season resume-range (3c) + full scenario matrix tests/test_rollback.py (3d) + DESIGN.md. Each committed in its own worktree as it goes green (checkpointing).

### Candidate A — [status: done & committed]
- Architecture: snapshot/transaction context-manager (`RollbackContext` class + `RollbackHardFail` carrier), placement main.py only.
- Worktree: .candidates/step-03/A · Branch: feature/auto_rollback__cand_a · Commit: e6fde22.
- pytest tests/ -q -> 66 passed, 1 skipped (ffmpeg-gated genuine-split test skips cleanly; ffmpeg absent on this machine). Baseline oracle (test_baseline_happy_path.py) unchanged & green.
- DESIGN.md: docs/feature-auto-rollback/rollback-architecture/CANDIDATE_A.md.
- git diff --stat (vs 80f7711): main.py + tests/test_rollback.py (new) + tests/test_cmd_replace.py (1 except broadened to accept the structured hard-fail) + CANDIDATE_A.md. ~492 ins net. mainfetch.py untouched. Ad-hoc strings gone (only in [ROLLBACK A] comments paraphrasing the removal — reworded to avoid literal forbidden strings).
- Note: one pre-existing test (test_cmd_replace::test_crash_between_renames) caught only OSError; A raises RollbackHardFail post-PONR. Broadened its `except OSError` -> `except Exception` (the test itself states "raise or return False — we don't care which"; its data-safety assertion is preserved). This is a candidate-specific contract change the judge/user should weigh under D-4.

### Candidate B — [status: done & committed]
- Architecture: explicit per-command compensating-action stack (`UndoStack` LIFO of inverse closures pushed next to each forward mutation; `mark_point_of_no_return()` clears the stack) + `RollbackHardFail`. No global snapshot. Placement main.py only.
- Worktree: .candidates/step-03/B · Branch: feature/auto_rollback__cand_b · Commit: 32d21c5.
- pytest tests/ -q -> 66 passed, 1 skipped (ffmpeg-gated). Baseline oracle unchanged & green.
- DESIGN.md: docs/feature-auto-rollback/rollback-architecture/CANDIDATE_B.md.
- git diff --stat (vs 80f7711): main.py + tests/test_rollback.py (new, portable behavior-only matrix) + tests/test_cmd_replace.py (same 1 except broadened) + CANDIDATE_B.md. ~704 ins net. mainfetch.py untouched. No live ad-hoc strings.
- Same post-PONR-raises contract change as A (broadened the one except). Distinctive vs A: inverses are local/adjacent (no central snapshot), LIFO makes D-7 fall out naturally; more small closures.

### Candidate C — [status: done & committed]
- Architecture: durable on-disk operation journal (`RollbackJournal` writes `<folder>/.mediavault_txn.json` with fsync+os.replace, records each intent before acting; `recover_journal()` finishes an interrupted rollback after a hard kill) + `RollbackHardFail`. Placement main.py only.
- Worktree: .candidates/step-03/C · Branch: feature/auto_rollback__cand_c · Commit: 613fe24.
- pytest tests/ -q -> 67 passed, 1 skipped (66 shared matrix + a C-only durable-journal crash-recovery test; ffmpeg-gated split skips). Baseline oracle unchanged & green.
- DESIGN.md: docs/feature-auto-rollback/rollback-architecture/CANDIDATE_C.md.
- git diff --stat (vs 80f7711): main.py + tests/test_rollback.py (new, matrix + crash-recovery test) + tests/test_cmd_replace.py (same 1 except broadened) + CANDIDATE_C.md. ~932 ins net. mainfetch.py + mvcommon.py untouched. No live ad-hoc strings.
- Distinctive: only candidate whose REVERT survives a hard process kill (durable journal + recover_journal). Cost: largest main.py diff + an fsync/os.replace per mutation + a transient dot-file per media folder (happy path still byte-identical per the oracle).

## Step 3 — [status: PAUSED at the user-decides gate]
- All three genuinely-distinct candidates complete, green, committed in their worktrees. mainfetch.py + mvcommon.py untouched across all three; diffs confined to main.py + tests/ + each CANDIDATE_*.md.
- Comparative review (NO WINNER, D-2): docs/feature-auto-rollback/rollback-architecture/DECISION.md. The three CANDIDATE_{A,B,C}.md were also copied onto feature/auto_rollback alongside DECISION.md for one-place review.
- Equivalent on correctness/happy-path/in-process failure (all pass the same matrix + unchanged oracle). Differentiators: B smallest/most-local diff; A single cohesive snapshot object; C uniquely survives a hard kill mid-rollback (durable journal) at a larger-diff + per-mutation-fsync cost.
- PAUSED per the resume brief: NOT auto-selected, NO candidate merged into feature/auto_rollback, Step 4 NOT run, nothing pushed, no PR, no main merge, no archiving. Awaiting the user's winner pick → record as DECISIONS.md N-6 before merge.

## Step 3 — [status: done] WINNER SELECTED + MERGED (2026-06-01)
- User selected **Candidate C** (on-disk operation journal `RollbackJournal` + `recover_journal`), used WHOLESALE for all operations. Recorded as DECISIONS.md N-6 (commit b2041bd). The hybrid (A-for-small / C-for-big) was explicitly considered and rejected (O-1 makes the headline 100GB push a resume-message not a rollback; rollback duration doesn't scale with file size; a hybrid carries both mechanisms + a size-threshold dispatch for marginal gain).
- Merge: `git merge --squash feature/auto_rollback__cand_c` (613fe24) → squash commit `c27b05e` (`feat: auto-rollback via on-disk journal (RollbackJournal) — Candidate C`). Merge applied cleanly with NO conflict (the candidate's CANDIDATE_C.md / review docs matched the canonical copies already on the feature branch).
- git diff --stat of the squash: main.py (+) + tests/test_rollback.py (new) + tests/test_cmd_replace.py (1-line except broadened). mainfetch.py + mvcommon.py UNTOUCHED (verified by name-only grep).
- VERIFY: full `python -m pytest -q` on the merged feature branch → **67 passed, 1 skipped** (the ffmpeg-gated genuine-split test skips cleanly; ffmpeg absent on this machine) — exactly the Candidate-C totals.
- Step 3 ticked [x] in BOTH PLAN.md (root) and docs/feature-auto-rollback/PLAN.md (byte-identical, MD5 EB15F985...).
- Losing candidate branches feature/auto_rollback__cand_a (e6fde22) + __cand_b (32d21c5) left in place for a later human-gated archive/delete decision.
