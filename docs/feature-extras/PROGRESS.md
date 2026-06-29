# IMP-D19 — Extras: Execution Journal (resumable, cross-session / cross-account)

> **This is the single machine-readable "where we are" for IMP-D19.** Update + commit this file at the END of every
> step (and whenever a step is paused mid-way), in the SAME commit as the step. A fresh session — even on a different
> Claude account/machine — resumes from here. See PLAN.md §"Execution resumability" for the full protocol.

- **Task:** IMP-D19 — add an `--extras` option (Specials / Trailers / Behind-the-Scenes) end-to-end.
- **Branch:** `feature/imp_d19_extras` (NOT YET CREATED — see NEXT ACTION).
- **Plan:** `docs/feature-extras/PLAN.md` · **Decisions (locked):** `docs/feature-extras/DECISIONS.md`.
- **Locked decisions:** A2 (extras nested, grouped per source folder; group key = path relative to title) · B1
  (`add_extras` cmd + `--extras`/`--extras-size` on prep/push family) · C = FLAG-ONLY (`--fetchExtras`, aliases
  `--fetch-extras`/`--extras`/`--extra`; no prompt; absent = no extras) · extras-size default = inherit main split ·
  D1 (full push→dummy→fetch→restore lifecycle) · E1 (additive rollback; main contract byte-for-byte unchanged).
- **Last updated:** 2026-06-29 (Step 0 `415ae4b`, Step 1 `dccd993`, Step 2 `3d959a4`; Step 3 IN PROGRESS — candidate A
  committed @ d892991, candidate B re-running; this is a mid-step resumability checkpoint).

## ▶ NEXT ACTION
**Finish Step 3 multi-candidate, then STOP at the 🚦 user checkpoint.** Candidate A done (`__cand_a` @ d892991).
Awaiting Candidate B (`__cand_b`) to finish + be committed; then run the **judge** → `.candidates/d19-step-3/DECISION.md`;
then remove the worktrees (footgun-safe); then **STOP and present the judge's analysis to the USER to pick the winner**
(do NOT auto-merge). Only after the user's pick: MERGE_CANDIDATE_WINNER → smoke gate → commit → archive/tag.
Do NOT re-run the planner; do NOT re-open Cards A–E.

## Resume protocol (first thing a new session does)
1. `git fetch && git checkout feature/imp_d19_extras` (or create it from `main` if it does not exist — first run).
2. Read `PLAN.md` + `DECISIONS.md` + this file (all in `docs/feature-extras/`).
3. Reconcile: `git log --oneline` must match the per-step SHAs below; `git status` clean. On disagreement, trust git.
4. Resume at the first non-`done` step (continue from its sub-state notes if `in_progress`); never re-run a `done` step.
5. Finish the step → update + commit this file (status + SHA + tests) and tick the PLAN.md checkbox in the same commit.

## Step status
| Step | Description | Status | Completing SHA | Tests | Notes |
|------|-------------|--------|----------------|-------|-------|
| 0  | Scaffold + commit this execution journal | done | 415ae4b | n/a | plan + DECISIONS + journal committed onto the branch |
| 1  | Extras data model + scan/merge/dedup core (A2 grouped) | done | dccd993 | smoke 72✓, schema-guard 2✓, self-check 8/8✓ | [model: opus] `_extras_title_id`/`scan_extras_folders`/`merge_extras_into_title` + ENTRY_TYPE_KEYS comment; `re_hashed` dropped (not True) on byte-change |
| 2  | CLI parsing `--extras`/`--extras-size`/`--fetchExtras` + `add_extras` | done | (this commit) | cli-parsers 29✓, smoke 72✓ | [model: sonnet] `parse_extras_tokens`; 6 cmds + argv walkers; `cmd_add_extras` routed; `--fetchExtras`(+aliases) on fetch/fetch_restore; prep/prep_season/add_extras scan+merge; markers left for Steps 3/4/5. `--extras-size none`→`('NONE',None)` |
| 3  | Extras upload phase (independent chunk size; resumable) | in_progress | — | A: suite 603✓ | [model: opus] **multi-candidate (2)** — A committed (`__cand_a` @ d892991); B re-running; judge + 🚦 checkpoint pending |
| 4  | Extras replace (dummy) phase for reclaim | pending | — | — | [model: opus] rollback-adjacent (E1) |
| 5  | Extras fetch (flag-only `--fetchExtras`, no prompt) | pending | — | — | [model: opus] |
| 6  | Extras restore (merge+verify into recreated subfolder) | pending | — | — | [model: opus] |
| 7  | Cross-command integrity (scan_unprepped/reclaim/items/tree) | pending | — | — | [model: opus] PR#21 class |
| 8  | `ENTRY_TYPE_KEYS` doc + schema-guard round-trip | pending | — | — | [model: opus] no new entry type |
| 9  | conftest `sandbox_extras` fixture | pending | — | — | [model: opus] binding hazard |
| 10 | Unit/command tests `tests/test_extras.py` | pending | — | — | [model: sonnet] |
| 11 | Smoke coverage (round-trip + alias sweep + not-flagged) | pending | — | — | [model: sonnet] |
| 12 | Architect docs (ARCHITECTURE/README/...) | pending | — | — | [model: opus] |
| 13 | Register IMP-D19 (tier file + PRIORITY.md + graph) | pending | — | — | [model: sonnet] |
| 14 | Final verification + smoke gate (last) | pending | — | — | [model: sonnet] |

## Multi-candidate tracking (Step 3)
- **Namespace (task-unique — `step-3`/`step-03` are taken by prior tasks):** worktrees `.candidates/d19-step-3/A|B`;
  candidate branches `feature/imp_d19_extras__cand_a|b`; judge `DECISION.md` → `.candidates/d19-step-3/DECISION.md`;
  archive tags → `candidates/imp_d19_extras/step-3/<letter>-chosen|rejected`.
- **Candidate A** (refactor `cmd_push` core into shared `_upload_file`): DONE + committed → branch
  `feature/imp_d19_extras__cand_a` @ **d892991** (+427/−180). Self-report: full suite **603 passed**, smoke 72,
  push tests 11, rollback matrix 63; confidence **high**. Honest caveat: journal lifecycle physically relocated into
  `_upload_file` (observable contract preserved, larger blast radius than B).
- **Candidate B** (isolated `push_one_extra`, `cmd_push` untouched): RE-RUNNING (first attempt cut off by session
  limit with no saved work; worktree was clean at base, re-dispatched). Not yet committed.
- **Judge:** pending (runs after B commits). Chosen candidate: — (decided by the USER at the 🚦 checkpoint).
- **🚦 CANDIDATE CHECKPOINT (task-specific, user-required):** after the judge writes `DECISION.md`, the orchestrator
  does NOT auto-merge. It STOPS, relays the judge's full analysis + recommendation to the user, and the user picks which
  candidate to merge. Only after the user's explicit choice is the winner merged + committed. (Overrides the default
  orchestrator auto-merge flow; see PLAN.md Step 3 🚦 bullet + "Checkpoint 0".)

## In-progress sub-state
**Step 3 (multi-candidate) — in progress.** Candidate A is committed and durable on `feature/imp_d19_extras__cand_a`
(@ d892991). Candidate B is re-running in worktree `.candidates/d19-step-3/B` (re-dispatched after a session-limit
interruption that saved no work). The feature branch HEAD is still `3d959a4` (Step 2) — no candidate is merged yet.
**Resume sequence if interrupted:** (1) if B's worktree has uncommitted work, finish/commit it via git-agent
COMMIT_CANDIDATE (letter B, worktree `.candidates/d19-step-3/B`); if its worktree is clean at base, re-dispatch the
Candidate-B executor; (2) run the judge → `.candidates/d19-step-3/DECISION.md`; (3) remove the worktrees (footgun-safe)
keeping the candidate branches; (4) **STOP at the 🚦 user checkpoint** — present the judge's analysis, let the USER pick
the winner; (5) only then MERGE_CANDIDATE_WINNER (the chosen `__cand_*` branch) → smoke gate → commit → archive/tag.

## Blockers / human gates
- **Gate (now):** awaiting user "go" to begin execution.
- **Checkpoint 1 (later):** STOP after opening the PR — do not merge to `main` without the user's OK.
- **Checkpoint 2 (later):** after merge, ask before archiving the branch (annotated `archive/...` tag, then delete).
