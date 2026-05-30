# MediaVault — Project Instructions

Project-specific guidance for Claude Code. Loaded into every session and subagent. Merges with the global `~/.claude/CLAUDE.md`.

## Git & pull requests
Follow [`docs/git-pr-conventions.md`](docs/git-pr-conventions.md) for all branches, commits, and PRs. The two rules that are easy to forget:

1. **PR title must include the IMP code** when the task maps to a tracked improvement (e.g. `… — IMP-C2`, `… — IMP-H1`). Check `improvements_tier*.md` to find the code.
2. **PR body order:** the auto-generated Claude Code summary FIRST, then a `## Original task prompt` section containing the **complete verbatim** initial task prompt, then the `🤖 Generated with Claude Code` trailer.

## Agentic workflow
Non-trivial changes go through the multi-agent pipeline in `.claude/agents/` (planner → orchestrator → executors, with git-agent and judge). It runs on Opus 4.8 effort tiers — see `ARCHITECTURE.md` §19 and `.claude/AGENT_WORKFLOW_NOTES.md`.

## Improvement tasks
Work is tracked as `IMP-<XN>` tasks across `improvements_tier*.md`; start from `improvement_details.md`. Mark status (`pending`/`in_progress`/`done`) as work progresses.
