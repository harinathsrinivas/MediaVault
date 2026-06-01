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

## Improvement tasks
Work is tracked as `IMP-<XN>` tasks across `improvements_tier*.md`; start from `improvement_details.md`. Mark status (`pending`/`in_progress`/`done`) as work progresses.

## Auto-rollback is load-bearing — change-gate
The unified auto-rollback mechanism (`RollbackJournal` / `recover_journal` / `RollbackHardFail` in `main.py`, the per-`cmd_*` point-of-no-return markers, the `.mediavault_txn.json` journal format, the O-1 resume-message vs O-2 hard-fail split, and the `cmd_prep_push_rep_season` resume-range messaging) was chosen via a user-decided bake-off (`docs/feature-auto-rollback/DECISIONS.md` N-6, PR #14). Many commands depend on it for safe failure handling.

**Before implementing ANY change that would alter rollback behavior, STOP, state EXACTLY what differs from the documented behavior, and ask the user as an explicit decision.** Do not silently modify it. "Affecting rollback" = the journal format/durability (`fsync` + `os.replace`), the PONR locations or `mark_point_of_no_return()` placement, what gets recorded (created-this-run / D-6 / D-7 scoping), the wrapping of `cmd_prep`/`cmd_push`/`cmd_replace`/`cmd_restore`, `recover_journal()` semantics (incl. that it is NOT on the happy path), the season resume-range messaging, or the `RollbackHardFail` contract (`resume_cmd` must name an existing command). Full spec + scenario matrix: [`docs/feature-auto-rollback/ROLLBACK_MECHANISM.md`](docs/feature-auto-rollback/ROLLBACK_MECHANISM.md) (§10 Change-gate) and `ARCHITECTURE.md` §12a. Forward-looking rollback/storage work is tracked in `improvements_tierR.md`.
