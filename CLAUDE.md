# MediaVault — Project Instructions

Project-specific guidance for Claude Code. Loaded into every session and subagent. Merges with the global `~/.claude/CLAUDE.md`.

## Git & pull requests
Follow [`docs/git-pr-conventions.md`](docs/git-pr-conventions.md) for all branches, commits, and PRs. The two rules that are easy to forget:

1. **PR title must include the IMP code** when the task maps to a tracked improvement (e.g. `… — IMP-C2`, `… — IMP-H1`). Check `improvements/improvements_tier*.md` to find the code.
2. **PR body order:** the auto-generated Claude Code summary FIRST, then a `## Original task prompt` section containing the **complete verbatim** initial task prompt, then the `🤖 Generated with Claude Code` trailer.
3. **Checkpoint 1 — merging into `main` is human-gated.** Never `gh pr merge` / merge / push to `main` without the user's explicit confirmation. Create the PR, then STOP and ask.
4. **Checkpoint 2 — archiving a merged branch is human-gated.** After a branch is merged, ask the user; on approval, create an annotated `archive/<branch>` tag (merge info + revive steps in the message), push it, then delete the branch (local + remote). Tagging is the standard archive method — it keeps branches clean without losing the squashed per-step history.

## Agentic workflow
Non-trivial changes go through the multi-agent pipeline in `.claude/agents/` (planner → orchestrator → executors, with git-agent and judge). It runs on Opus 4.8 effort tiers — see `ARCHITECTURE.md` §19 and `.claude/AGENT_WORKFLOW_NOTES.md`.

**Execution model — top-level orchestration.** A Claude Code sub-agent cannot spawn sub-agents (nesting depth = 1), and `orchestrator` is otherwise a sub-agent. So the pipeline runs in the **main (top-level) session**: it reads `PLAN.md`, follows `.claude/agents/orchestrator.md` as a *playbook*, and spawns the executor / candidate / judge / git sub-agents **itself** (depth-1 from the main session works), committing between steps and pausing at the human gates. **Do NOT launch the `orchestrator` agent via `Task` to execute a plan** — it would hit the depth limit and (as happened on the A1/C2/C8/auto-rollback runs) silently fall back to running everything inline. See the 2026-06-03 decision in `.claude/AGENT_WORKFLOW_NOTES.md`.

## Editing agents — snapshot first, two silent footguns
**Before changing ANY file in `.claude/agents/`, snapshot the whole directory first.** Copy `.claude/agents/` to `.claude/agent-backups/<YYYY-MM-DD>/` (append a short `_<label>` if a meaningful state, e.g. `2026-06-13_pre-yaml-fix`). Skip if today's snapshot already exists. These backups are **local-only** (gitignored under `.claude/agent-backups/`) — git history is the real version store; the dated folders just make "what changed / quick rollback" easy without git gymnastics. **Never put backup or duplicate copies UNDER `.claude/agents/`** (see footgun 2).

Two ways an agent gets **silently dropped with no error** — both bit us and both are invisible until you notice the agent is missing from the spawnable list:
1. **Invalid YAML frontmatter.** Claude Code parses the `---` frontmatter as strict YAML. A `: ` (colon-space) inside an *unquoted* `description` (e.g. `marked [model: opus]`) throws `mapping values are not allowed here` and the whole agent is discarded. Quote any description containing `:`. Validate after editing — parse every agent's frontmatter (e.g. `python -c "import yaml,glob,re; ...safe_load..."`).
2. **Duplicate `name:` in the scanned tree.** Claude Code scans `.claude/agents/` and `~/.claude/agents/` **recursively**; if two files declare the same `name`, it keeps one and discards the rest without warning. So stale candidate **worktrees** and any backup copies left under `.claude/agents/` will silently disable the real agents (this is what commit `4ce9e4a` fixed by pruning worktrees). Keep `name` unique across the whole tree; keep backups OUT of `.claude/agents/`.

After any agent edit, confirm the agents still register by starting a fresh session (registration is fixed at session start) and checking the spawnable-agent list.

## Surface fundamental contradictions — no silent handling
If any agent — or the main session — hits a **fundamental capability gap or contradiction** with the task/plan (a required tool is unavailable, e.g. nested `Task`; a planned approach is impossible; an instruction conflicts with a hard runtime limit), **STOP and surface it to the user as an explicit decision** — state what was expected, what actually differs, and the options — rather than silently working around it and continuing. This applies to every agent (this file loads into every session and sub-agent).

## Improvement tasks
**Everything about what we're building and why lives in the [`improvements/`](improvements/) folder** — start at [`improvements/README.md`](improvements/README.md). Work is tracked as `IMP-<XN>` tasks across `improvements/improvements_tier*.md` (tiers A–H, R, S, U, X); the operating manual is `improvements/improvement_details.md`. Mark status (`pending`/`in_progress`/`done`) as work progresses. The master doc index is `docs/README.md`; the forward roadmap is `improvements/ROADMAP_END_GOAL.md`.

**Priority list is load-bearing — keep it current.** `improvements/PRIORITY.md` is the single source of truth for "what to do next" (critical bugs first, a `👉 SUGGESTED NEXT TASK` pointer, five priority bands), and `docs/priority-graph/priority-graph.html` is its interactive visual twin. **Whenever you add, complete, or re-prioritize a task, update BOTH** (and the task's tier file) in the same change — the maintenance protocol is at the bottom of `improvements/PRIORITY.md`. A new bug that breaks something goes into PRIORITY.md Band 0 and is a candidate for the NEXT pointer.

## Operations Q&A — keep it current

[`docs/OPERATIONS_QA.md`](docs/OPERATIONS_QA.md) is the **living reference of practical "how do I
actually do this" answers**, built from real questions asked during real archiving sessions. It sits
alongside `README.md` (what commands exist) and `ARCHITECTURE.md` (how it's built) and answers the
third question: **how to operate it, and what has actually bitten.**

**Whenever the user asks a practical operational question — "how do I…", "why did this happen",
"which command should I use" — and you give a verified answer, add it to that file** under the right
section, and bump its `Last updated`. Verify every claim against the code or the live library before
writing it (cite `main.py:NNNN` where useful); never write from memory. If the answer turns out to be
"that's a bug", name the IMP code, or register one if it doesn't exist. The maintenance protocol is
at the bottom of the file.

This matters because these answers are otherwise lost in chat scrollback and re-derived (often
differently) in the next session.

## Cross-command integrity + smoke gate

A change to one feature must never silently break another command. Two mechanisms enforce this:

- **Smoke gate — run before any PR (and before committing any code-touching step):** `pytest tests/smoke -q` drives every command and major option against tiny fixtures and the existing stubs. It should complete in under 30 seconds. If it is red, nothing ships.
- **`ENTRY_TYPE_KEYS` registry + guard test:** `main.py` declares `ENTRY_TYPE_KEYS` — the canonical set of library entry types and shared data-field names. `tests/test_entry_schema_guard.py` asserts this registry stays consistent. Any change that adds, renames, or removes a library entry type or shared field **must** update `ENTRY_TYPE_KEYS` and keep every whole-library iterator alias/season_map-safe (call `_resolve_alias` or skip `type == "multi_ep_alias"` entries).

## Out-of-band data requests
Web tools (`WebSearch`/`WebFetch`) live ONLY on **planner, orchestrator, and architect** — executors never browse (they stay deterministic and side-effect-bounded). When a web-less sub-agent genuinely needs external/library/doc data mid-step, it must NOT guess or fabricate: it pauses (step stays in-progress) and emits a fenced `DATA_REQUEST` block (`step`/`purpose`/`query_or_url`/`fields_needed`/`return_format`/`blocking`). The orchestrator performs the fetch itself, then re-dispatches the same executor with a fenced `DATA_RESPONSE` block carrying the answer in the requested shape, and the executor resumes. The planner, having web tools, should PRE-RESOLVE such facts during planning and bake them into the step text so executors rarely need to pause.

## Auto-rollback is load-bearing — change-gate
The unified auto-rollback mechanism (`RollbackJournal` / `recover_journal` / `RollbackHardFail` in `main.py`, the per-`cmd_*` point-of-no-return markers, the `.mediavault_txn.json` journal format, the O-1 resume-message vs O-2 hard-fail split, and the `cmd_prep_push_rep_season` resume-range messaging) was chosen via a user-decided bake-off (`docs/feature-auto-rollback/DECISIONS.md` N-6, PR #14). Many commands depend on it for safe failure handling.

**Before implementing ANY change that would alter rollback behavior, STOP, state EXACTLY what differs from the documented behavior, and ask the user as an explicit decision.** Do not silently modify it. "Affecting rollback" = the journal format/durability (`fsync` + `os.replace`), the PONR locations or `mark_point_of_no_return()` placement, what gets recorded (created-this-run / D-6 / D-7 scoping), the wrapping of `cmd_prep`/`cmd_push`/`cmd_replace`/`cmd_restore`, `recover_journal()` semantics (incl. that it is NOT on the happy path), the season resume-range messaging, or the `RollbackHardFail` contract (`resume_cmd` must name an existing command). Full spec + scenario matrix: [`docs/feature-auto-rollback/ROLLBACK_MECHANISM.md`](docs/feature-auto-rollback/ROLLBACK_MECHANISM.md) (§10 Change-gate) and `ARCHITECTURE.md` §12a. Forward-looking rollback/storage work is tracked in `improvements/improvements_tierR.md`.
