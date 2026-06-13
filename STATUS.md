# Execution Log

Task: Fix the multi_ep_alias production crash (IMP-C12/C13) AND harden the agent workflow + testing strategy so a feature change can never silently break another command

Branch: fix/alias_crash_and_smoke_gate (from main)
PLAN: root /PLAN.md (live, gitignored); canonical copy to be restored under docs/feature-alias-crash-and-smoke-gate/ at finalize.
Baseline (pre-change, unmodified tree): `pytest -q` -> 99 passed, 2 skipped (the 2 skips are ffmpeg/mkvmerge-gated). This is the regression oracle.

Run model: USER-GATED, ONE STEP AT A TIME. The user reviews after each step and says "continue". This log + PLAN.md checkboxes + per-step git commits are the resume record — a fresh session can resume by reading this file, `git log --oneline`, and the PLAN.md `[x]` marks.

Planned step order: A1 -> A2 -> A3 -> B1 -> B2 -> B3 -> B4 -> B5 -> B6 -> B7 -> (bookkeeping: tierC/tierH/PRIORITY/priority-graph) -> (architect: ARCHITECTURE.md/README) -> Phase 3 finalize (restore docs/<feature>/PLAN.md, push, open PR). Merge to main is HUMAN-GATED.

Note on the in-scope pre-existing change: `.claude/agents/planner.md` was already modified (frontmatter) before this run; it is carried onto the feature branch and will be committed as part of step B3 (which edits planner.md), NOT with earlier steps.

---

## RUN BLOCKER — paused before Step A1 (RESUME POINT)

Date: 2026-06-13. State on disk is safe; nothing committed yet on this branch beyond the carried working-tree changes.

**Blocker:** the named executor sub-agents `executor-opus` / `executor-sonnet` / `executor-haiku` are NOT dispatchable in this session. `Task(subagent_type="executor-opus")` returns: `Agent type 'executor-opus' not found. Available agents: architect, claude, claude-code-guide, Explore, general-purpose, git-agent, judge, orchestrator, Plan, planner, statusline-setup`. So only 5 of the 8 project agents in `.claude/agents/` register (architect, git-agent, judge, orchestrator, planner); the 3 executors do not.

**Diagnosis (done this session):** no global `~/.claude/agents/` dir; no agent allowlist in `.claude/settings*.json`; executor frontmatter is structurally identical to the registered architect/judge (same `name/model/effort/tools` fields, same CRLF). No file-level defect found. Cause is environmental (session-fixed agent registry — likely a harness quirk or a custom-agent count cap). This is the same class of failure noted on prior runs (A1/C2/C8/auto-rollback: "Task subagent tool UNAVAILABLE → orchestrator executed inline").

**User decision (2026-06-13):** FIX AGENT REGISTRATION FIRST (do not fall back to inline or generic-agent execution).

**RESUME PROCEDURE after a Claude Code restart:**
1. Re-set session: `/model` → Opus 4.8, `/effort` → xhigh (saved as defaults, but confirm).
2. Verify the fix: attempt a trivial `Task(subagent_type="executor-opus")` or check the available-agents list — confirm all of executor-opus/sonnet/haiku now appear.
3. If registered → resume execution at **Step A1** (the branch `fix/alias_crash_and_smoke_gate` is already created and checked out; baseline is 99 passed/2 skipped). Follow `.claude/agents/orchestrator.md` as the playbook from Phase 2, step A1. Dispatch A1 → executor-opus; commit via git-agent; report; await user "continue".
4. If STILL not registered → it's a harness cap/limit, not transient. Surface to user; options then are (a) consolidate the 3 executors into fewer agent files, (b) generic `claude` agent + model override, or (c) explicit inline execution.

**Steps completed this run:** none of A1–B7 (the code steps). Setup + registration-fix done: branch `fix/alias_crash_and_smoke_gate` created (27cd911 → 4ce9e4a), baseline captured (99 passed/2 skipped), STATUS.md reset, **2 stale locked worktrees pruned (commit `4ce9e4a`)**. ⏸️ **NEXT ACTION = USER RESTARTS Claude Code** to re-register the executor agents, then `/agents` to verify, then resume at Step A1.

**Diagnosis update (clutter found):** `.claude/` contains duplicate agent definitions:
- backup dirs `.claude/agents_copy2/`, `.claude/agents_old/`, `.claude/agents_pre_opus48/` (all git-TRACKED) — each a full copy of the 8 agents with DIFFERING content (older pre-Opus-4.8 versions; e.g. their `executor-opus.md` lacks the `effort: max` line).
- two STALE LOCKED git worktrees `.claude/worktrees/agent-a7378bcf38009429a` (@00e4216) and `.claude/worktrees/agent-a79b36f292a96e1d9` (@d246430), leftover from May-26 candidate runs — each carries its own `.claude/agents/` (+ agents_copy2/old) copy.
Claude Code docs: agents load at session start (no hot-reload); duplicate `name:` across the scanned tree is "kept once, others discarded without warning"; `/agents` shows the loaded registry. NOTE: `architect.md` also differs across the backup copies yet still registers — so the backup dirs are likely NOT scanned; the 2 git worktrees (real `.claude/agents/` dirs) are the leading scannable-duplicate suspect. Mechanism not 100% proven from inside the session.

**FIX SEQUENCE (requires a Claude Code restart — only the user can do it):**
1. (optional, zero-risk) run `/agents` in the interactive session to view the loaded registry / any conflict on the executors.
2. ✅ DONE (2026-06-13, commit `4ce9e4a`): pruned the 2 stale locked worktrees via `git worktree remove --force` + `git worktree prune`. Backup dirs `agents_copy2`/`agents_old`/`agents_pre_opus48` were INTENTIONALLY KEPT (user decision; not named `agents` so almost certainly not scanned — `architect.md` is duplicated there too yet registers fine).
3. FULLY quit + restart Claude Code; run `/agents` (or attempt `Task(subagent_type="executor-opus")`) to confirm all of executor-opus/sonnet/haiku now register.
4. Re-set `/model` Opus 4.8 + `/effort` xhigh, then resume execution at Step A1 per orchestrator.md.
5. If executors STILL missing after a clean restart with no duplicates → it's a harness bug; report via `/feedback`; fall back (with user consent) to generic `claude` agent + model override or explicit inline execution.

---

## Step A1 — [status: done]
- Executor: executor-opus
- Model: opus
- Mode: single-executor
- Files changed: `main.py` (only)
- Outcome: Fixed the two whole-library iterators (IMP-C12) and de-aliased the five direct single-id commands plus added the `cmd_prep` refuse-over-alias guard (IMP-C13), in one coherent edit. `cmd_local_status` and `cmd_scan_unprepped` now skip `multi_ep_alias` entries alongside `season_map`, so neither dereferences the alias's absent `folder_path`/`filename` (the reported `KeyError`) nor counts it as a phantom pending row / slices `None[:40]`. `cmd_check`, `cmd_push`, `cmd_replace`, `cmd_restore`, `cmd_verify_restore` now resolve a secondary-episode id to the primary at the lookup head via `_resolve_alias`, print exactly one info line, and operate on the primary thereafter. `cmd_prep` refuses to prep over an existing alias id before writing anything. `cmd_fetch_restore` (#8) needed no change — it delegates to `cmd_restore`, which now resolves. Verified by careful code reading, a throwaway sanity snippet (since deleted), and the full suite staying at baseline.
- Key decisions:
  - **Iterator-skip form used (both sites):** `if entry.get("type") in ("season_map", "multi_ep_alias"): continue` — the explicit tuple form, matching the existing `season_map` skip style (chose this over the belt-and-suspenders `or "folder_path" not in entry`). Applied at `cmd_local_status` (loop head, now `main.py:2369`) and `cmd_scan_unprepped` (loop head, now `main.py:2475`).
  - **De-alias + rekey strategy:** at each command's lookup head I call `real_id, entry = _resolve_alias(library, manual_id)`, and when `real_id != manual_id` I print the info line then REBIND `manual_id = real_id`. Rebinding the variable (not just `entry`) is what makes every downstream `library[manual_id]` assignment, the `RollbackJournal(local_folder, manual_id)` construction, and the resume-message `push {manual_id}` all land consistently on the PRIMARY id — the rekeying concern in PLAN Risks. The info line prints the ORIGINAL typed id and the resolved id (captured before the rebind), so the user sees both. For a non-alias id `real_id == manual_id`, so `manual_id` is unchanged and the whole path is byte-identical (the info `print` is guarded by `real_id != manual_id`).
  - **`cmd_prep` guard placement:** added as the FIRST check inside the existing `if manual_id in library:` block (`main.py:808-812`), before the uploaded/archived early-skip. It `return False`s before `folder_path`/`short_id`/journal are computed (those are all after `main.py:824`), so zero artifacts are created and the alias entry is left untouched. Message: `❌ {manual_id} is a combined-episode alias of {alias_of}; prep the primary instead.` (uses `entry.get('alias_of')`).
- Verification:
  - `python -c "ast.parse(main.py)"` → parses OK.
  - Throwaway sanity snippet (created, run, then deleted — no test file added): confirmed `_resolve_alias` one-hops the secondary id to the primary and leaves non-alias ids unchanged; confirmed both iterator-skip loop bodies no longer KeyError on `folder_path`/`filename` and no longer slice a `None` filename. Output: `ALIAS SANITY OK`.
  - `python -m pytest -q` → **99 passed, 2 skipped in 15.15s** — identical to the recorded baseline (the 2 skips are the ffmpeg/mkvmerge-gated cases). No regressions.
  - `git status --short` → only `main.py` modified by this step (the pre-existing `M STATUS.md` was carried in before the run; this append updates it).
  - **Rollback change-gate: RESPECTED — nothing touched.** All de-alias edits sit at the command lookup heads, strictly BEFORE any journal is opened (push journal `main.py:1293`, replace `1775`, restore `2143`) and before any `mark_point_of_no_return()` (replace `1820`, restore `2205`). The edits are read-only alias resolution. No PONR moved, no `mark_point_of_no_return()` placement changed, no `.mediavault_txn.json` journal format/durability changed, no change to what `RollbackJournal` records, and `recover_journal()` is untouched. When an alias id is passed, the journal is now constructed with the resolved PRIMARY id — which is the only coherent behavior (the alias has no physical artifacts/PONR of its own); for non-alias ids the journal id is unchanged.
