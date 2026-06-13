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
- Risk: low for the spike (no production code, no agent files changed); large restructures of `.claude/agents/` would change how every future task is built — gate behind a written decision doc, keep `agents_pre_*` backups like the H1 migration did.
- If skipped: the pipeline stays sequential — perfectly adequate at MediaVault's current task sizes; revisit only when a plan regularly exceeds ~10 steps or wall-clock pain appears. (2026-06-12 note: the main-session-as-orchestrator pattern mandated by CLAUDE.md after the nested-Task failures is a partial, manual version of this idea.)
- Status: pending

---

## IMP-H3: Cross-command smoke gate + consumer-impact guardrail + agent enforcement + out-of-band data-request protocol

- Category: other (dev tooling / agent infrastructure)
- Priority: high
- Files: `tests/smoke/__init__.py`, `tests/smoke/conftest.py`, `tests/smoke/test_smoke_all_commands.py` (NEW — 50-test fast gate); `tests/test_entry_schema_guard.py` (NEW — ENTRY_TYPE_KEYS registry guard); `main.py` (`ENTRY_TYPE_KEYS` constant); `.claude/agents/planner.md` (Consumer Impact Analysis mandate + smoke-gate rule + DATA_REQUEST pre-resolve rule); `.claude/agents/orchestrator.md` (per-step + pre-PR smoke gate enforcement + DATA_REQUEST handler); `.claude/agents/executor-opus.md`, `executor-sonnet.md`, `executor-haiku.md` (smoke-gate + ENTRY_TYPE_KEYS instructions + DATA_REQUEST protocol); `.claude/agents/architect.md` (web-capable note); `CLAUDE.md` (cross-command integrity + smoke-gate + out-of-band data-request subsections); `docs/testing-strategy.md` (smoke-suite pyramid tier, `sandbox_alias` §4.7, `ENTRY_TYPE_KEYS` §4.8)
- Current behavior (pre-fix): PR #21 shipped `multi_ep_alias` without auditing whole-library consumers — `cmd_scan_unprepped` and `cmd_local_status` silently broke (production KeyError / TypeError). No cross-command gate existed; no planning mandate to audit consumers; agents had no web-access discipline.
- Proposed change (delivered):
  1. `tests/smoke/` — 50-test cross-command gate; runs every user-facing command + aliases against `sandbox` and `sandbox_alias` libraries in <10 s. Demonstrated to catch the PR #21 regression (`TestAliasSweep::test_scan_unprepped_alias` → `KeyError: 'folder_path'`) while the plain-library case still passes.
  2. `ENTRY_TYPE_KEYS` registry in `main.py` + `tests/test_entry_schema_guard.py` — authoritative record of the three entry shapes (leaf/season_map/multi_ep_alias); guard test auto-extends when a new type is registered.
  3. Planner Consumer Impact Analysis mandate — any step that changes a shared data contract must audit every consumer in a PLAN.md table (file:line, safe/needs-fix verdict) before coding begins; cites PR #21 / IMP-E13 as the cautionary example.
  4. Orchestrator smoke-gate enforcement — per-step (before commit), post-merge (multi-candidate), and pre-PR; red smoke blocks commit.
  5. Executor smoke-gate + ENTRY_TYPE_KEYS instructions — wired into all three executor agent files.
  6. Out-of-band DATA_REQUEST protocol — web tools (`WebSearch`/`WebFetch`) granted only to planner/orchestrator/architect; executors raise a fenced `DATA_REQUEST` block (never browse); orchestrator services and re-dispatches with a `DATA_RESPONSE`. Protocol documented in all 8 agent files + CLAUDE.md.
- Rationale: The PR #21 bug class (a new shared data shape silently breaks a distant consumer, shipped because no cross-command test existed and no planning mandate required a consumer audit) was the root cause of IMP-C12/C13. This task makes the class structurally unshippable.
- Goal: Any future feature that touches a shared data contract must pass a consumer audit in the plan AND a cross-command smoke run before commit — or the pipeline refuses to proceed.
- Effort estimate: medium
- Risk: low — purely additive tests/docs/agent-files; no production code paths changed.
- Status: done (fix/alias_crash_and_smoke_gate — all six deliverables shipped in steps A1–B7; full suite 163 passed / 0 skipped; smoke suite 50 passed in <10 s)
