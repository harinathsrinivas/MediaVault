# Auto-Rollback — Planning Session Log

**Date:** 2026-05-28
**Participants:** user `harinathsrinivas` (via remote-control / mobile), Claude
Code (Opus 4.7) as orchestrating agent, plus the `planner` sub-agent.
**Outcome:** draft `PLAN.md` produced; feature **paused** at planning stage so the
user can implement prerequisite improvements first.

This log records the whole session so a future session/agent can reconstruct the
reasoning without guessing.

---

## 1. The task as given

The user asked to implement an **auto-rollback** feature for MediaVault's
multi-step commands, with these key points (paraphrased; intent preserved):

- Multi-step commands (e.g. `prep_push_rep`, `prep_push_rep_season`) can fail on
  an intermediate step and leave a confusing, undocumented half-state with no
  indication of what to run next.
- **Example given (season):** `prep_push_rep_season` for a 10-episode season,
  `adb push` fails on episode 5 → today it just hangs/breaks, half archived half
  local. Want: completed episodes kept; clear message on how to resume the rest.
- **Example given (movie):** `prep_push_rep` for an 80 GB movie; main-file hashing
  done, then split into chunks fails on the 5th chunk (disk full) → want a
  rollback that removes the split artifacts + the newly-added library entry/uid/
  chunks folder, back to the exact pre-command state.
- **Non-rollback example:** pushing a split file that fails halfway after some
  chunks are uploaded and their local copies deleted → DON'T invent a new
  "fetch-to-fix" command; **hard-fail with a clear message** so the user looks
  manually. The user explicitly asked to be told what other hard-failures can
  happen and to confirm them.
- Constraints: start from `main` (not a feature branch); work across ALL current
  commands; don't change happy-path behavior or add new failure cases for normal
  scenarios; use Opus + multi-candidate for important logic steps (don't worry
  about limits for those); add tests in proper folders; test using a **copy** of
  a real video from `C:\Media` and **copies** of the library JSONs (never touch
  the real media files or library JSONs — hash-mismatch risk); document which
  scenarios are testable; cross-reference the `improvements_tier*.md` files and
  flag related improvements (and if the user opts in, implement + mark them done);
  at the end have the architect update ARCHITECTURE.md/README and provide the
  branch name, PR, and manual test commands.
- The user said to use the **planner agent** and to pause/ask before finalizing
  whenever a decision was needed.

## 2. Investigation findings (orchestrator, before planning)

- **Git base resolved.** Working branch was `feature/video_dummy`. `origin/main`
  had advanced to `47c7382 Feature/video dummy (#3)` (the branch's work was
  squash-merged). `git diff origin/main HEAD` for code = empty → **branching off
  `origin/main` loses nothing.** So the "start from main" constraint is safe.
- **Project shape.** Active source: `main.py`, `mainfetch.py`, `tools/migrate_lib.py`.
  `archive/` = historical (do not touch). `tests/` exists but is empty
  (`.gitkeep`) — no tests yet. Improvement tiers A–G exist.
- **Command map** (see `FAILURE_ANALYSIS.md` for line refs): orchestrators
  `cmd_prep_push_rep`, `cmd_prep_push_rep_season`, `cmd_fetch_restore`; batch
  `cmd_prep_season`, `cmd_push_group`, `cmd_replace_group`, `cmd_restore_group`;
  atomic-but-multi-step `cmd_prep`, `cmd_push`, `cmd_replace`, `cmd_restore`.
- **Existing ad-hoc rollback found:** `cmd_prep_push_rep` deletes `_parts/` on push
  failure; `cmd_prep_push_rep_season` just `break`s. These disagree and are what
  the feature unifies.

## 3. Planner dispatch

The planner sub-agent was given a self-contained brief (the task, the constraints,
the command map, the existing ad-hoc rollback, the improvement cross-reference
requirement) and asked to produce a DRAFT `PLAN.md` plus an explicit "Open
Decisions" list — because the planner can't pause to ask the user mid-run.

It produced `PLAN.md` (archived in this folder): branch `feature/auto_rollback`
off `origin/main`; production changes confined to `main.py` + new `tests/`; a
6-step plan; the core rollback-mechanism step marked multi-candidate (originally
3 candidates: A snapshot/transaction wrapper, B compensating-action stack, C
on-disk journal); test sandbox approach; and a Related-Improvements section
(default: defer all).

## 4. The Q&A round (verbatim user answers)

The orchestrator surfaced 4 questions. The user's answers:

1. **Hard-fail scenario list (A: chunk uploaded-then-deleted during push;
   B: original deleted during replace):**
   > "give me more clear examples of this happening. before I can decide"
   → Led to the corrected analysis in `FAILURE_ANALYSIS.md`: push failures are
   actually *resumable* (original survives); the true PONR is replace's delete of
   the original. Confirmation deferred (open item O-1/O-2).

2. **How to choose the rollback-mechanism architecture:**
   > "for this - lets remove the max 3 agent candidate constraint. Let the planner
   > put as many options as possible even more than 3 agents if needed based on
   > different approaches. let the agents run as intended, let the judge review on
   > its own, provide us the results. Let me be the final point who chooses the
   > selected approach with all the data points and judge results."
   → Decision D-2: uncapped bake-off, judge reviews, **user picks**.

3. **Restore/fetch_restore in scope?**
   > "Include restore rollback too"
   → Decision D-1.

4. **Fold in related improvements?**
   > "give me each of these tasks and how it is related to ours. I will do the
   > respective ones separately first and come back to this task."
   → The user will do prerequisite improvements separately first. Each is
   described in `RELATED_IMPROVEMENTS.md`. Nothing folded in yet.

## 5. Analysis correction delivered to the user

The orchestrator delivered concrete failure walk-throughs (Examples A/B/C, now in
`FAILURE_ANALYSIS.md`) and the key correction: **the original master file being
deleted (in `replace`) is the real point of no return**, not the first chunk
delete in `push`; push failures are resumable. Plus the per-improvement breakdown
(now in `RELATED_IMPROVEMENTS.md`) with suggested order C9 → C11 → G1.

## 6. Current status & next steps

- Feature is **PAUSED** at planning. Draft `PLAN.md` exists (root + archived copy
  here). No code written. No tier item marked done.
- The user is implementing prerequisite improvement(s) first (their choice;
  recommended C9, then C11, then optionally G1).
- **On resume:** re-run the planner against the updated code; refresh `PLAN.md`
  with restore-in-scope (D-1), the uncapped bake-off with user-as-final-chooser
  (D-2), and the user's push-failure boundary choice (O-1). Then orchestrate:
  git-agent branches `feature/auto_rollback` off `origin/main`; executors
  (Opus for logic steps, with the candidate bake-off for the mechanism step);
  judge writes up candidates for the user to pick; architect updates
  ARCHITECTURE.md + README; finally provide branch name, PR to `main`, and manual
  test commands.

See also: `../../improvements/improvements_tierC.md`, `../../improvements/improvements_tierG.md`,
`../../improvements/improvements_tierA.md`, and the auto-memory note
`project_auto_rollback_task.md`.
