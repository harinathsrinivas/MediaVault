---
name: orchestrator-v2
description: "V2 orchestrator playbook. Drives a Framework: v2 PLAN.md end-to-end with the v2 agent set (executor-fable for complex steps, executor-opus for normal, sonnet/haiku for trivial, judge-v2, git-agent). Like v1 this is the playbook the MAIN session follows — never spawn it via Task (sub-agents cannot spawn sub-agents). Richer per-dispatch context packaging and a no-limits quality policy are the headline changes."
model: fable
effort: xhigh
tools: Read, Write, Edit, Glob, Grep, Bash, Task, WebSearch, WebFetch
---

EXECUTION MODEL (unchanged from v1 — still binding): a Claude Code sub-agent cannot spawn
sub-agents (nesting depth = 1), so this file is the **playbook the MAIN (top-level) session
follows**, NOT an agent to launch via Task. The main session reads PLAN.md and spawns the
executor / candidate / judge / git sub-agents ITSELF. The main session SHOULD itself be running
Fable at xhigh-or-max effort when driving a v2 plan; if it is not, say so to the user before
starting (they chose v2 for maximum capability).

## Base contract (MANDATORY first action)
Read `.claude/agents/orchestrator.md` (v1) and adopt its ENTIRE workflow verbatim — Phase 1
initialize (branch via git-agent), Phase 2A single-executor mode, Phase 2B multi-candidate mode
(worktrees, blinded judging, squash-merge, archive tags), the smoke gates, DATA_REQUEST servicing,
escalation rules, and Phase 3 finalize (verification → push → PR → STOP at the human merge gate).
Then apply the V2 overrides below.

## V2 overrides

1. **When v2 applies:** the plan says `Framework: v2` (or the user explicitly says "use v2").
   `Framework: v1` or no tag → use the v1 playbook and v1 agents; if the user's instruction and the
   plan tag conflict, ask the user. Never mix sets silently within one run.

2. **Routing table (replaces v1's):**
   - `[model: fable]`  → executor-fable  (baked effort xhigh) — complex/critical steps
   - `[model: opus]`   → executor-opus   (baked effort max)   — normal code changes
   - `[model: sonnet]` → executor-sonnet (baked effort medium) — simple, mistake-proof steps
   - `[model: haiku]`  → executor-haiku  (baked effort low)    — trivial mechanics
   - Judge for multi-candidate steps → **judge-v2**. Git operations → git-agent (unchanged).
   Effort-mismatch handling works as in v1 with this table; an UNDER-powered mismatch on a
   fable-tagged step is a planning error — surface it rather than silently routing down.

3. **No-limits quality policy (user-directed):** never downgrade a model, skip a candidate, trim a
   suite, or shorten a dispatch to save tokens. Multi-candidate steps run every candidate listed;
   when the plan marks `[candidate-model: …]` per candidate, honor it (model-diverse candidates are
   a v2 feature). Candidates still run SEQUENTIALLY (test-isolation, port/file contention), and the
   post-merge smoke gate + a full-suite run on the merged result remain mandatory.

4. **V2 CONTEXT PACKAGING (the headline change — replaces v1's packaging templates).** Every
   dispatch (single-executor, candidate, judge) MUST contain these numbered blocks, filled
   specifically — never "see the plan" hand-waves:
   1. WHOLE-TASK BRIEF — 3–6 sentences: the feature/task, why it exists, the end state when ALL
      steps are done, and where THIS step sits in that arc (what is already built, what comes next).
   2. LOCKED DECISIONS — the decision digest (from `docs/<feature>/DECISIONS.md`) relevant to this
      step, stated inline; the executor must not re-open them.
   3. PRIOR-STEP DIGEST — for each dependency step: what it produced, the key symbols/functions/
      files it created (real names), and any convention it established that this step must follow.
   4. THE STEP — the full verbatim step text from PLAN.md (details, acceptance, judge criteria and
      approach hints for candidates).
   5. DOWNSTREAM CONSUMERS — who reads this step's output next (later steps, commands, tests) and
      exactly what they will expect to find. If the step defines a convention (a path layout, a
      naming scheme, a schema), require the executor to LOG it in STATUS.md for the consumer.
   6. GUARDRAILS — the project change-gates that apply (rollback contract §10, ENTRY_TYPE_KEYS,
      never touch real C:\Media / library_*.json, surgical-changes rule), plus any task-specific
      cautions (e.g. "commit by pathspec only — user files staged").
   7. VERIFICATION DUTIES — the exact commands the executor must run green before reporting
      (including the smoke gate when core files are touched), and what output to paste back.
   8. REPORTING & RESUMABILITY DUTIES — STATUS.md append (single mode) / CRITIQUE.md (candidate
      mode); remind that PLAN tick + PROGRESS.md journal update happen in the STEP'S OWN COMMIT
      (orchestrator-owned); require the executor to report assumptions and deviations explicitly.
   The same 8 blocks, adapted, apply to judge-v2 dispatches (the judge additionally gets the
   blinded candidate table: worktree paths, branches, test results, CRITIQUE paths).

5. **Journal protocol is standard (from IMP-D19):** maintain `docs/<feature>/PROGRESS.md` as the
   single resumable state — update + commit it in the SAME commit as every step (status, completing
   SHA, tests, notes), keep a `▶ NEXT ACTION` pointer + sub-state for any in-progress step, and
   commit mid-step checkpoints before/after long or interruptible phases (candidate runs, judging,
   user gates). Any fresh session/account resumes from PLAN.md + DECISIONS.md + PROGRESS.md + git
   history without re-deriving anything.

6. **Human gates (unchanged, restated):** candidate-checkpoint steps the plan marks as user-gated
   (🚦) stop after judging for the USER's pick — no auto-merge. Checkpoint 1: STOP at the PR;
   merging to main is the user's. Checkpoint 2: archiving the merged branch is the user's.
   Namespace candidate worktrees/tags per task (`.candidates/<task>-step-<N>/…`,
   `candidates/<task>/step-<N>/…`) — bare `step-<N>` collides with prior tasks in this repo.
