# Agent Workflow Notes

Notes on the MediaVault multi-agent workflow (`.claude/agents/`). Not an agent definition — kept out of `agents/` so it isn't scanned as one.

## Opus 4.8 + effort migration (2026-05-30)

Migrated all 8 agents to the Opus 4.8 / effort-tier model. Pre-migration configs backed up to `.claude/agents_pre_opus48/`.

Each agent now declares an `effort:` frontmatter field (`low | medium | high | xhigh | max`; omitting it inherits session effort). Assignments:

| Agent            | Model  | Effort |
| :--------------- | :----- | :----- |
| planner          | opus   | high   |
| orchestrator     | opus   | high   |
| architect        | opus   | high   |
| judge            | opus   | high   |
| executor-opus    | opus   | max    |
| executor-sonnet  | sonnet | medium |
| executor-haiku   | haiku  | low    |
| git-agent        | haiku  | low    |

`model: opus` is kept as an alias (auto-resolves to the latest Opus, currently 4.8) rather than pinned to `claude-opus-4-8`, so configs track future upgrades.

### Per-step effort = "Hybrid advisory" design
The Task/Agent tool has **no per-invocation effort parameter** (open upstream: claude-code issues #25669, #43083, #31536). Effort can only be set via static frontmatter or session `/effort`. So:
- The planner tags each step with both `[model: ...]` and an **advisory** `[effort: ...]`.
- The orchestrator routes by `[model: ...]` (which determines the real runtime effort, since each executor's effort is fixed in frontmatter), notes any effort mismatch, and reports under-powered steps in its final summary.
- Reliable way to deliver high/xhigh/max thinking today: assign `[model: opus]` (runs at `max`).
- **Revisit when upstream adds a per-call effort param** — at that point the orchestrator can honor `[effort: ...]` directly and we can drop the model-as-effort-proxy coupling.

## Deferred: Opus 4.8 "dynamic workflows"

Opus 4.8 added **dynamic workflows** — a session plans a task then spins up *hundreds* of parallel subagents in one session, with outputs verified before being reported back. Potentially a much stronger backbone than the current sequential orchestrator + multi-candidate worktrees.

**Blocker for our current design:** subagents cannot spawn subagents, and our `orchestrator` *is* a subagent. Dynamic workflows is a main-session capability. Exploiting it would mean restructuring so the orchestrator drives from the main session (e.g. via `--agent orchestrator`) rather than being spawned as a subagent.

**Status:** noted only, no restructuring. Decide later whether MediaVault's scale justifies the rework.
