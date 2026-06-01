# Auto-Rollback Architecture Bake-Off — Comparative Review (NO WINNER)

> **Status: JUDGE-REVIEWS-ONLY / USER-DECIDES (Decision D-2 / N-3).**
> This document compares the three complete, integrated, tested implementations of
> the auto-rollback feature. It deliberately **declares no winner**. The user picks
> the winning candidate; that choice is recorded as `DECISIONS.md` N-6 **before** the
> winning candidate's branch is merged into `feature/auto_rollback`.

## What was built

Each candidate is a *complete* implementation in its own worktree/branch off the
post-Step-2 base (`feature/auto_rollback` @ `80f7711`): a rollback primitive, the
wrapping integration of `cmd_prep` / `cmd_push` / `cmd_replace` / `cmd_restore`, the
unification of both ad-hoc orchestrator paths (`cmd_prep_push_rep` +
`cmd_prep_push_rep_season` with season resume-range messaging), a full scenario test
matrix in `tests/test_rollback.py`, and a per-candidate DESIGN.md.

| Candidate | Architecture | Branch | Commit | `pytest tests/ -q` | DESIGN.md |
|---|---|---|---|---|---|
| **A** | Snapshot / transaction context-manager (`RollbackContext`) | `feature/auto_rollback__cand_a` | `e6fde22` | 66 passed, 1 skipped | `CANDIDATE_A.md` |
| **B** | Explicit compensating-action stack (`UndoStack`) | `feature/auto_rollback__cand_b` | `32d21c5` | 66 passed, 1 skipped | `CANDIDATE_B.md` |
| **C** | On-disk operation journal (`RollbackJournal`) | `feature/auto_rollback__cand_c` | `613fe24` | 67 passed, 1 skipped | `CANDIDATE_C.md` |

(The 1 skip is the ffmpeg-gated genuine-split test, which skips cleanly when ffmpeg is
absent — as it is on the current machine. The extra passing test in C is its
durable-journal crash-recovery test, which has no A/B analogue.)

Diff scope (vs the `80f7711` base), all confined to `main.py` + `tests/` + the
candidate's DESIGN.md; **`mainfetch.py` and `mvcommon.py` are untouched in all three**:

| Candidate | main.py change | tests added | net insertions |
|---|---|---|---|
| A | 632 lines touched | test_rollback.py (356) + 1 line in test_cmd_replace.py | ~848 ins |
| B | 569 lines touched | test_rollback.py (356) + 1 line | ~779 ins |
| C | 675 lines touched | test_rollback.py (403) + 1 line | ~932 ins |

## Shared design (identical across all three)

To keep the comparison about *architecture*, every candidate makes the same
load-bearing choices, so the user is choosing the mechanism, not the policy:

- **O-1 push = resume-message, no PONR.** A push failure after any chunk uploaded
  leaves the partial upload, keeps the entry `local_ready`/`uploaded=False`, and
  prints `push <id>`. A pre-any-upload failure rolls back this-run
  `_parts`/`checksums`/`split_info` only. A **resume `_parts/` is never deleted**.
- **O-2 PONRs = exactly two.** `cmd_replace` commit rename and `cmd_restore` split
  chunk-delete. After either, a failure is a structured `RollbackHardFail` naming the
  existing `fetch_restore <id>` (N-2) — no new command.
- **D-7** parent season_map handling, **D-9** leave the remote dir, **C11** restore
  quarantine reuse, **C9** stale-sweep left intact, created-this-run artifact scoping.
- **Orchestrator unification:** both ad-hoc paths replaced; the season path keeps
  completed episodes, lets the in-flight item self-handle, and prints a reconstructed
  resume-range command (handles `.5` episodes). The three forbidden ad-hoc strings
  ("Reverting temporary files", "run 'push' manually", "to prevent mess") are gone.
- **One shared contract change** (worth the user's attention under D-4): a post-PONR
  replace failure now *raises* `RollbackHardFail` instead of returning False. The one
  pre-existing test that asserted "raise or return False — we don't care which"
  (`test_cmd_replace::test_crash_between_renames`) had its `except OSError` broadened
  to `except Exception`; its data-safety assertion is unchanged. This is identical in
  all three candidates.

## Comparison across the ranked Judge criteria

### 1. Correctness & safety (most important)
All three pass the same scenario matrix and the unchanged Step-1/2 oracle, and all
three enforce created-this-run scoping (proved by
`test_push_resume_does_not_delete_preexisting_parts`) and D-7. They are **equivalent
on the happy and the in-process failure paths.** The differentiator is *durability of
the revert itself*: only **C** records inverses on disk before acting, so the revert
survives a hard kill; **A** and **B** hold the plan in memory and rely on the existing
re-run/resume semantics (C9 stale-sweep, push resume) if the process dies mid-command.

### 2. Minimal happy-path intrusion (D-4)
- **B** is the smallest `main.py` change (569 lines touched) and arguably the least
  conceptually intrusive — each `undo.push(...)` is one line adjacent to its forward
  action.
- **A** is in the middle (632); the registration calls are spread through each body.
- **C** is the largest (675) and adds real (if negligible-for-this-tool) runtime cost:
  an fsync + `os.replace` per mutation and a transient dot-file per media folder.
  The happy path is behaviorally identical in all three (oracle green).

### 3. Crash-survival & debuggability
- **C wins decisively** — `recover_journal()` finishes an interrupted rollback from a
  durable, human-readable journal (proved by `test_journal_survives_hard_kill_and_recovers`),
  and the journal is a forensic record of exactly what a run did. This is the
  `save_library`-mid-rollback edge PLAN.md flags.
- **A** and **B** are equivalent here and weaker: an in-memory plan is lost on a kill;
  both report partial rollback honestly but cannot resume themselves.

### 4. Readability / maintainability (solo maintainer, procedural, no type hints)
- **B**: "what gets undone" is literally the list of `undo.push` lines, read top to
  bottom — no central state to keep in sync. Cost: several small closures per command.
- **A**: one well-documented class, but the `created_*()` registration calls are
  scattered, so you must read the whole body to know what will roll back.
- **C**: central `_replay_inverses()` is easy to audit, but it adds the most concepts
  (durable file format, recovery entry point, dot-file lifecycle).

## Summary of the trade-off (for the user's decision)

- Pick **A** if you prefer a single cohesive transaction object with an explicit
  snapshot and don't need on-disk crash recovery.
- Pick **B** if you value the smallest, most locally-auditable diff and like
  create/undo pairs sitting side by side — at the cost of in-memory-only recovery.
- Pick **C** if crash-survivability of the rollback itself (durable journal +
  `recover_journal`) is worth a larger diff and a small per-mutation disk cost.

All three are correct and ship-ready against the test matrix. The decision is a
genuine engineering-values trade-off (diff size / readability vs. durable crash
recovery), which is exactly why D-2 reserves it for the user.

## How to inspect each candidate

```
# See a candidate's full diff vs the base:
git diff 80f7711 feature/auto_rollback__cand_a -- main.py tests/   # or __cand_b / __cand_c

# Run a candidate's tests from its worktree:
cd .candidates/step-03/A && python -m pytest tests/ -q             # or B / C

# Read each design write-up:
docs/feature-auto-rollback/rollback-architecture/CANDIDATE_{A,B,C}.md  (in each worktree)
```

## Next step (user-gated)

The user selects A, B, or C. Record the choice as `DECISIONS.md` **N-6**, then merge
that candidate's branch into `feature/auto_rollback`, run Step 4 (architect docs),
and push. Until then the orchestrator PAUSES — no candidate is merged, nothing is
pushed, no PR is opened (per the resume brief's Step-3 gate).
