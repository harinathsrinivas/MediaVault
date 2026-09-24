# Model-availability waterfall (v2 agent set)

**Added 2026-09-07** (user directive). Fable is not always reachable — weekly/usage caps can
disable it for a session with no warning. A v2 run must therefore never *assume* fable; it probes
first and degrades deliberately, loudly, and on the record.

This file is the single source of truth for that policy. It is referenced by
`orchestrator-v2.md`, `planner-v2.md`, `executor-fable.md`, and `judge-v2.md`.

> Location note: this file lives in `.claude/`, **not** `.claude/agents/`. Claude Code scans
> `.claude/agents/` recursively for agent definitions; a non-agent `.md` there is a footgun
> (see CLAUDE.md "Editing agents — snapshot first, two silent footguns").

---

## 1. The key mechanism: override the model, keep the agent

The `Agent` tool's `model` parameter **takes precedence over the agent definition's `model:`
frontmatter**, while everything else in the definition — the V2 deltas, the base contract, and the
baked `effort:` tier — still applies.

So the fallback is **not** "swap to a different agent". It is:

```
Agent(subagent_type: "executor-fable", model: "opus", prompt: <same dispatch + banner>)
```

`executor-fable` and `judge-v2` declare `effort: max` (raised from `xhigh` on 2026-09-24, user
decision, so a fallback really is Opus at *max*; `orchestrator-v2`'s frontmatter is irrelevant — it is a
playbook, never spawned). Overriding only the model therefore yields exactly what the user asked for — **"Opus with max/ultra"** — with
every v2 quality rule (no-limits depth, mandatory self-review, evidence-over-self-report) intact.

**Exception — `subagent_type: "fork"` ignores a `model` override** (a fork always runs the parent's
model). Never rely on a fork for a fable step.

---

## 2. Preflight probe (once per session, before the first fable dispatch)

```
Agent(subagent_type: "general-purpose", model: "fable",
      prompt: "Reply with exactly one line and nothing else:
               FABLE_PROBE_OK <the exact model ID you are running as>
               Do not use any tools. Do not think at length. Just answer.")
```

| Probe outcome | Reading | Action |
|---|---|---|
| Returns `FABLE_PROBE_OK claude-fable-5-1` (or any `claude-fable-*`) | **AVAILABLE** | Run v2 at full strength. |
| Spawn error, quota/limit/capacity error, or a non-fable model ID comes back | **UNAVAILABLE** | Engage the waterfall (§3) and tell the user before any step runs. |

The probe is cheap and non-mutating (no tools, one line). **Run it once per session**, and re-run
it after any mid-run failure that smells like a model/limit error — caps can trip *during* a long
run, so availability is not a one-time fact.

**Report the probe result to the user before starting execution.** They chose v2 for maximum
capability; a silent downgrade is exactly the kind of hidden substitution CLAUDE.md's
"Surface fundamental contradictions" rule forbids.

---

## 3. The waterfall

Applied per role. Tier 1 is the default; drop a tier only on a failed probe or a hard failure.

| Role | Tier 1 (primary) | Tier 2 (fallback) | Tier 3 (last resort) |
|---|---|---|---|
| Planning | `planner-v2` (fable/max) | `planner-v2` + `model: "opus"` | `planner` (v1) + `model: "opus"` |
| Orchestration (main session) | main session on Fable, xhigh–max | main session on **Opus 5 at `/effort max`** | — |
| Complex step | `executor-fable` (fable/max) | `executor-fable` + `model: "opus"` | `executor-opus` (max) |
| Judging | `judge-v2` (fable/max) | `judge-v2` + `model: "opus"` | `judge` (v1) + `model: "opus"` |
| Normal / simple steps | unchanged (`executor-opus` / `-sonnet` / `-haiku`) | unchanged | unchanged |

Tier 3 exists only for the case where a v2 definition itself fails to load. **If Opus is also
unavailable, STOP and ask the user** — do not silently run a fable-tagged step on sonnet. A
fable-tagged step is fable-tagged because a mistake there is expensive.

The orchestrator is the main session, so its "fallback" is a user action, not a spawn: if fable is
unavailable, tell the user to run `/model opus` and `/effort max` before driving a v2 plan.

---

## 4. Mandatory banner on a degraded dispatch

Any dispatch made under Tier 2/3 MUST open with this block, so the sub-agent knows its own tier and
so the transcript records it:

```
⚠️ MODEL-FALLBACK ACTIVE — fable unavailable this session.
You are `<agent-name>` running on **opus** (not fable) at this definition's baked effort tier.
Every V2 rule in your definition still binds: no-limits depth, full reads (never summaries),
mandatory self-review, assumption surfacing. Do not narrow scope because of the model change.
If you hit something you believe genuinely needed the fable tier, SAY SO in your report rather
than guessing — the orchestrator will re-run that step when fable returns.
```

---

## 5. Recording it (resumability)

A degraded run must be legible to a *different* session or account picking the work up later:

- **`docs/<feature>/PROGRESS.md`** — the step's row records the model actually used
  (`fable` / `opus (fallback)`), not the planned one.
- **`STATUS.md`** — the executor's entry writes `Model: opus (fable-fallback)`.
- **`docs/<feature>/DECISIONS.md`** — one line when a run is degraded end-to-end, with the date.
- Steps the plan marks `fallback: none` (§6) are **not executed** while degraded — they are parked
  with a blocker note in PROGRESS.md and surfaced to the user as a decision.

Re-running a step purely because fable came back is optional and is the **user's** call: a green,
reviewed, committed step is not re-run automatically.

---

## 6. Planner duty

Every `[model: fable]` step in a v2 PLAN.md carries an explicit fallback tag:

- `[fallback: opus]` — default; the step is safe to run degraded.
- `[fallback: none]` — the step is genuinely fable-or-nothing (rare; reserve it for
  change-gated surfaces such as the rollback contract). A degraded run **parks** it and asks
  the user rather than proceeding.
