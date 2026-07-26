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

**Status:** DECIDED 2026-06-03 — adopt top-level orchestration (the restructuring this note flagged). See below.

## Decision (2026-06-03): top-level orchestration + no-silent-handling

The depth-1 limit was confirmed in practice across the A1 / C2 / C8 / auto-rollback runs: the spawned `orchestrator` found `Task` unavailable and silently fell back to running every step inline (noting it only in `STATUS.md`). Two changes:

1. **Execution model = top-level orchestration.** The pipeline runs in the MAIN session, not as a spawned `orchestrator` sub-agent. The main session reads `PLAN.md` and follows `.claude/agents/orchestrator.md` as a *playbook*, spawning the executor / candidate / judge / git sub-agents itself (depth-1 from the main session works), committing between steps, and pausing at the human gates. Do NOT launch `orchestrator` via `Task` to execute a plan — that reproduces the depth-1 problem. `orchestrator.md` is retained as the canonical playbook + spawn-context-packaging spec.

2. **No silent handling of fundamental contradictions.** If any agent (or the main session) hits a fundamental capability gap or contradiction vs. the plan — a needed tool is unavailable, a planned approach is impossible, an instruction conflicts with a hard runtime limit — it must STOP and surface an explicit DECISION REQUEST to the user (what was expected, what differs, the options) instead of quietly degrading. The earlier inline fallback is exactly what this forbids. Mirrored in `CLAUDE.md`.

Not changed: sub-agent nesting depth is a Claude Code runtime cap with **no project setting** — we don't attempt to configure one (the `orchestrator` already lists `Task` in its tools; the repo was never the blocker).

## v2 agent set (2026-07-27): Fable tier + no-limits + richer context packaging

With Fable 5 available (Max plan; the user has explicitly waived token/limit concerns for v2), a **v2 agent set** was added ALONGSIDE v1 — nothing in v1 was modified. Pre-change snapshot: `.claude/agent-backups/2026-07-27_pre-v2/`.

**Invocation convention:** the user says which set to use when invoking the planner or orchestrator ("plan … v2", "use orchestrator v2"). A v2 plan carries `Framework: v2` directly under its `Suggested branch:` line; `Framework: v1` or no tag = v1. If the user's instruction and the plan tag conflict, ask. Never mix sets silently in one run.

| Agent (v2)       | Model  | Effort | Role in v2                                                        |
| :--------------- | :----- | :----- | :---------------------------------------------------------------- |
| planner-v2       | fable  | max    | plans; may tag steps `[model: fable]`; no-limits candidate policy |
| orchestrator-v2  | fable  | xhigh  | PLAYBOOK for the main session (depth-1 still applies — never spawn via Task); 8-block context packaging |
| executor-fable   | fable  | xhigh  | complex/critical code + logic changes (NEW executor)              |
| judge-v2         | fable  | xhigh  | candidate judging; diff-corroborated, user-facing DECISION.md     |
| executor-opus    | opus   | max    | (reused from v1) normal code changes                              |
| executor-sonnet  | sonnet | medium | (reused) very simple, mistake-proof jobs only                     |
| executor-haiku   | haiku  | low    | (reused) trivial mechanics only                                   |
| git-agent        | haiku  | low    | (reused, unchanged)                                               |
| architect        | opus   | high   | (reused, unchanged)                                               |

Headline v2 behaviors (full detail in each `*-v2.md` / `executor-fable.md`, which are THIN DELTA files — each reads its v1 counterpart as the base contract, so the base protocol has one source of truth):
1. **Routing:** fable = complex/cross-cutting/contract- or rollback-adjacent work; opus = normal implementation; sonnet/haiku = only mistake-proof mechanics.
2. **No-limits:** never downsize a model or trim verification to save tokens; multi-candidate wherever genuinely different approaches exist (quality guardrails and max-5 stay; the 0–2-per-plan cost budget does not); optional per-candidate models (`[candidate-model: fable|opus]`) for solution diversity.
3. **8-block context packaging:** every dispatch carries WHOLE-TASK BRIEF / LOCKED DECISIONS / PRIOR-STEP DIGEST / THE STEP / DOWNSTREAM CONSUMERS / GUARDRAILS / VERIFICATION DUTIES / REPORTING+RESUMABILITY DUTIES — codifying (and tightening) the packaging proven on the IMP-D19 run.
4. **Resumability journal is standard:** every v2 plan has a Step 0 scaffolding `docs/<feature>/PROGRESS.md`, updated + committed in the same commit as each step (pattern: `docs/feature-extras/PROGRESS.md`).
5. **Registration reminder:** agent registration is fixed at session start — after adding/editing v2 files, a FRESH session is needed before the new names are spawnable. (Same-session workaround: dispatch `general-purpose` with a `model` override and paste the v2 contract into the prompt.)
