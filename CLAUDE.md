# MediaVault — Project Instructions

Project-specific guidance for Claude Code. Loaded into every session and subagent. Merges with the global `~/.claude/CLAUDE.md`.

## Git & pull requests
Follow [`docs/git-pr-conventions.md`](docs/git-pr-conventions.md) for all branches, commits, and PRs. The two rules that are easy to forget:

1. **PR title must include the IMP code** when the task maps to a tracked improvement (e.g. `… — IMP-C2`, `… — IMP-H1`). Check `improvements_tier*.md` to find the code.
2. **PR body order:** the auto-generated Claude Code summary FIRST, then a `## Original task prompt` section containing the **complete verbatim** initial task prompt, then the `🤖 Generated with Claude Code` trailer.
3. **Checkpoint 1 — merging into `main` is human-gated.** Never `gh pr merge` / merge / push to `main` without the user's explicit confirmation. Create the PR, then STOP and ask.
4. **Checkpoint 2 — archiving a merged branch is human-gated.** After a branch is merged, ask the user; on approval, create an annotated `archive/<branch>` tag (merge info + revive steps in the message), push it, then delete the branch (local + remote). Tagging is the standard archive method — it keeps branches clean without losing the squashed per-step history.

## Agentic workflow
Non-trivial changes go through the multi-agent pipeline in `.claude/agents/` (planner → orchestrator → executors, with git-agent and judge). It runs on Opus 4.8 effort tiers — see `ARCHITECTURE.md` §19 and `.claude/AGENT_WORKFLOW_NOTES.md`.

**Execution model — top-level orchestration.** A Claude Code sub-agent cannot spawn sub-agents (nesting depth = 1), and `orchestrator` is otherwise a sub-agent. So the pipeline runs in the **main (top-level) session**: it reads `PLAN.md`, follows `.claude/agents/orchestrator.md` as a *playbook*, and spawns the executor / candidate / judge / git sub-agents **itself** (depth-1 from the main session works), committing between steps and pausing at the human gates. **Do NOT launch the `orchestrator` agent via `Task` to execute a plan** — it would hit the depth limit and (as happened on the A1/C2/C8/auto-rollback runs) silently fall back to running everything inline. See the 2026-06-03 decision in `.claude/AGENT_WORKFLOW_NOTES.md`.

## Surface fundamental contradictions — no silent handling
If any agent — or the main session — hits a **fundamental capability gap or contradiction** with the task/plan (a required tool is unavailable, e.g. nested `Task`; a planned approach is impossible; an instruction conflicts with a hard runtime limit), **STOP and surface it to the user as an explicit decision** — state what was expected, what actually differs, and the options — rather than silently working around it and continuing. This applies to every agent (this file loads into every session and sub-agent).

## Improvement tasks
Work is tracked as `IMP-<XN>` tasks across `improvements_tier*.md` (tiers A–H, R, S, U, X); start from `improvement_details.md`. Mark status (`pending`/`in_progress`/`done`) as work progresses. The master index of all documentation is `docs/README.md`; the forward roadmap is `docs/feature-fable-review/ROADMAP_END_GOAL.md`.

**Priority list is load-bearing — keep it current.** `PRIORITY.md` (root) is the single source of truth for "what to do next" (critical bugs first, a `👉 SUGGESTED NEXT TASK` pointer, five priority bands), and `docs/priority-graph/priority-graph.html` is its interactive visual twin. **Whenever you add, complete, or re-prioritize a task, update BOTH** (and the task's tier file) in the same change — the maintenance protocol is at the bottom of `PRIORITY.md`. A new bug that breaks something goes into PRIORITY.md Band 0 and is a candidate for the NEXT pointer.

## Auto-rollback is load-bearing — change-gate
The unified auto-rollback mechanism (`RollbackJournal` / `recover_journal` / `RollbackHardFail` in `main.py`, the per-`cmd_*` point-of-no-return markers, the `.mediavault_txn.json` journal format, the O-1 resume-message vs O-2 hard-fail split, and the `cmd_prep_push_rep_season` resume-range messaging) was chosen via a user-decided bake-off (`docs/feature-auto-rollback/DECISIONS.md` N-6, PR #14). Many commands depend on it for safe failure handling.

**Before implementing ANY change that would alter rollback behavior, STOP, state EXACTLY what differs from the documented behavior, and ask the user as an explicit decision.** Do not silently modify it. "Affecting rollback" = the journal format/durability (`fsync` + `os.replace`), the PONR locations or `mark_point_of_no_return()` placement, what gets recorded (created-this-run / D-6 / D-7 scoping), the wrapping of `cmd_prep`/`cmd_push`/`cmd_replace`/`cmd_restore`, `recover_journal()` semantics (incl. that it is NOT on the happy path), the season resume-range messaging, or the `RollbackHardFail` contract (`resume_cmd` must name an existing command). Full spec + scenario matrix: [`docs/feature-auto-rollback/ROLLBACK_MECHANISM.md`](docs/feature-auto-rollback/ROLLBACK_MECHANISM.md) (§10 Change-gate) and `ARCHITECTURE.md` §12a. Forward-looking rollback/storage work is tracked in `improvements_tierR.md`.
