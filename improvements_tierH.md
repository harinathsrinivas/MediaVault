# Improvements — Tier H · Agentic Workflow & Tooling

> Tasks about the multi-agent Claude Code pipeline that *builds* MediaVault (`.claude/agents/`), not about the `main.py` / `mainfetch.py` runtime. These change how work gets done, not what the product does.

> **Cross-cutting context:**
> - The pipeline is: `architect` → `planner` (writes `PLAN.md`) → `orchestrator` (drives execution, delegates to `git-agent`) → `executor-haiku|sonnet|opus` → `judge` (multi-candidate only). Full description in `ARCHITECTURE.md` §19.
> - Agents run on Opus 4.8 / Sonnet 4.6 with an `effort:` frontmatter tier. `model: opus` is an alias (auto-tracks latest Opus).
> - Effort **cannot** be set per Task invocation today (upstream issues #25669/#43083/#31536), so per-step effort is "advisory" — see IMP-H1.

---

## IMP-H1: Migrate agent pipeline to Opus 4.8 effort tiers

- Category: other (dev tooling / agent infrastructure)
- Priority: high
- Files: all of `.claude/agents/*.md`; `.claude/AGENT_WORKFLOW_NOTES.md`; backup at `.claude/agents_pre_opus48/`; `ARCHITECTURE.md` §19; `README.md` (Development / agentic workflow)
- Current behavior: every agent declared only `model:` (opus/sonnet/haiku). No effort control existed; the planner assigned a model per step but had no concept of reasoning effort, and the orchestrator could not reason about it.
- Proposed change:
  - Add an `effort:` frontmatter field to all 8 agents: planner/orchestrator/architect/judge → `high`, executor-opus → `max`, executor-sonnet → `medium`, executor-haiku/git-agent → `low`.
  - Keep `model: opus` as an alias so agents auto-track the latest Opus (4.8 today).
  - Teach the **planner** to tag each step `[model: …] [effort: …]` and add a MODEL + EFFORT rubric (Opus 4.8 tier semantics, per-step heuristic, model/effort coherence rule).
  - Teach the **orchestrator** to parse `[effort: …]`, reconcile it against each executor's fixed frontmatter effort, hint the executor on mismatch, flag under-powered steps as re-plan candidates, and report all mismatches in its final summary.
  - Document the design ("hybrid advisory") because effort can't be set per Task call — record it in `AGENT_WORKFLOW_NOTES.md` and `ARCHITECTURE.md` §19.4.
- Rationale: Opus 4.8 makes effort a first-class, high-impact lever on speed and token cost (its `low` ≈ 4.7 `max`). Baking sensible per-agent defaults and making the planner/orchestrator effort-aware gets correct effort estimation per task and per step without manual `/effort` fiddling.
- Goal: spawning any agent from now uses the updated configs; the planner emits `[model][effort]` tags and the orchestrator surfaces effort mismatches. Pre-migration configs preserved for rollback.
- Effort estimate: small
- Status: done (2026-05-30, branch `chore/agent-opus48-effort`)

---

## IMP-H2: Evaluate Opus 4.8 "dynamic workflows" for the pipeline

- Category: other (dev tooling / agent infrastructure)
- Priority: low
- Files: would restructure `.claude/agents/orchestrator.md` and how it is launched
- Current behavior: the orchestrator runs sequentially and handles parallelism only via multi-candidate git worktrees, one step at a time. It is spawned as a subagent.
- Proposed change: spike whether Opus 4.8 "dynamic workflows" (plan a task, then spin up hundreds of verified parallel subagents in one session) can replace or augment the sequential orchestrator. Note the blocker: subagents cannot spawn subagents, and our `orchestrator` is itself a subagent — exploiting dynamic workflows requires running the orchestrator as the main session (`--agent orchestrator`) rather than a spawned subagent.
- Rationale: could dramatically speed up large multi-step plans and improve verification, but it is a structural change with unclear payoff at MediaVault's scale.
- Goal: a decision (with rationale) on whether to restructure the pipeline around dynamic workflows, or keep the current orchestrator.
- Effort estimate: medium (spike) → large (restructure if chosen)
- Status: pending
