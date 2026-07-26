# IMP-D19 — Extras: Execution Journal (resumable, cross-session / cross-account)

> **This is the single machine-readable "where we are" for IMP-D19.** Update + commit this file at the END of every
> step (and whenever a step is paused mid-way), in the SAME commit as the step. A fresh session — even on a different
> Claude account/machine — resumes from here. See PLAN.md §"Execution resumability" for the full protocol.

- **Task:** IMP-D19 — add an `--extras` option (Specials / Trailers / Behind-the-Scenes) end-to-end.
- **Framework:** v1 for Steps 0–5; **v2 for Steps 6–14 (user-directed 2026-07-27)** — orchestrate per
  `.claude/agents/orchestrator-v2.md` (8-block dispatch packaging, no-limits), route 6/7/8/9 → executor-fable,
  10/11/12 → executor-opus, 13/14 → executor-sonnet, judge-v2 if any judging arises, git-agent unchanged
  (pathspec commits ONLY — user files staged). A resuming session continues under v2 for these steps.
- **Branch:** `feature/imp_d19_extras` (NOT YET CREATED — see NEXT ACTION).
- **Plan:** `docs/feature-extras/PLAN.md` · **Decisions (locked):** `docs/feature-extras/DECISIONS.md`.
- **Locked decisions:** A2 (extras nested, grouped per source folder; group key = path relative to title) · B1
  (`add_extras` cmd + `--extras`/`--extras-size` on prep/push family) · C = FLAG-ONLY (`--fetchExtras`, aliases
  `--fetch-extras`/`--extras`/`--extra`; no prompt; absent = no extras) · extras-size default = inherit main split ·
  D1 (full push→dummy→fetch→restore lifecycle) · E1 (additive rollback; main contract byte-for-byte unchanged).
- **Last updated:** 2026-07-27 (Steps 0–7 done under v2 pace; 6+7 on executor-fable). Step 8 next (fable).

## ▶ NEXT ACTION
**Step 8 — `ENTRY_TYPE_KEYS` doc + schema-guard round-trip coverage for an extras block. [model: fable] (v2).**
Document the optional `extras` nested block (leaf + season_map) in the `ENTRY_TYPE_KEYS` comment exactly as
`split_info`/`metadata` are described — NO new entry type, NO change to `required`/`physical` sets (guard's
`NON_PHYSICAL_TYPES` assertion stays). Extend `tests/test_entry_schema_guard.py`: leaf AND season_map carrying a
representative A2 extras block round-trip byte-for-byte through save/load; whole-library read commands
(`scan_unprepped`, `local_status`, `sort`) complete without raising on an extras-bearing library. Note: Step 1 already
added an extras paragraph to the comment — Step 8 verifies/completes it rather than duplicating. ⚠️ Pathspec commits
ONLY (user's archiver files staged). Do NOT re-run the planner; do NOT re-open Cards A–E.

## Resume protocol (first thing a new session does)
1. `git fetch && git checkout feature/imp_d19_extras` (or create it from `main` if it does not exist — first run).
2. Read `PLAN.md` + `DECISIONS.md` + this file (all in `docs/feature-extras/`).
3. Reconcile: `git log --oneline` must match the per-step SHAs below; `git status` clean. On disagreement, trust git.
4. Resume at the first non-`done` step (continue from its sub-state notes if `in_progress`); never re-run a `done` step.
5. Finish the step → update + commit this file (status + SHA + tests) and tick the PLAN.md checkbox in the same commit.

## Step status
| Step | Description | Status | Completing SHA | Tests | Notes |
|------|-------------|--------|----------------|-------|-------|
| 0  | Scaffold + commit this execution journal | done | 415ae4b | n/a | plan + DECISIONS + journal committed onto the branch |
| 1  | Extras data model + scan/merge/dedup core (A2 grouped) | done | dccd993 | smoke 72✓, schema-guard 2✓, self-check 8/8✓ | [model: opus] `_extras_title_id`/`scan_extras_folders`/`merge_extras_into_title` + ENTRY_TYPE_KEYS comment; `re_hashed` dropped (not True) on byte-change |
| 2  | CLI parsing `--extras`/`--extras-size`/`--fetchExtras` + `add_extras` | done | (this commit) | cli-parsers 29✓, smoke 72✓ | [model: sonnet] `parse_extras_tokens`; 6 cmds + argv walkers; `cmd_add_extras` routed; `--fetchExtras`(+aliases) on fetch/fetch_restore; prep/prep_season/add_extras scan+merge; markers left for Steps 3/4/5. `--extras-size none`→`('NONE',None)` |
| 3  | Extras upload phase (independent chunk size; resumable) | done | fcd9e66 | full suite 603✓ (post-merge) | [model: opus] **multi-candidate** — judge ⇒ B, **USER picked B**; `__cand_b` squash-merged (`push_one_extra`/`push_title_extras`, cmd_push untouched). DECISION.md @ aae6db6. To archive: tags `candidates/imp_d19_extras/step-3/B-chosen`+`A-rejected` |
| 4  | Extras replace (dummy) phase for reclaim | done | (this commit) | replace+rollback+smoke 95✓ | [model: opus] `replace_one_extra`/`replace_title_extras`; mirrors cmd_replace PONR; driver catches per-file RollbackHardFail to isolate main except-handlers; cmd_replace byte-for-byte unchanged (E1 cleared) |
| 5  | Extras fetch (flag-only `--fetchExtras`, no prompt) | done | (this commit) | smoke+parsers 103✓, fetch-k 83✓ | [model: opus] synthetic leaf-shaped entries → `fetch_single_entry` verbatim; `build_extras_entries`/`resolve_title_extras` in mainfetch; `parse_fetch_args` → 3-tuple; staging `<title>/<group_rel>/restore/` (convention logged in STATUS.md for Step 6) |
| 6  | Extras restore (merge+verify into recreated subfolder) | done | (this commit) | full 605✓, smoke 72✓, restore+smoke gate 81✓ | [model: fable] (v2) `restore_one_extra`/`restore_title_extras`; archived→restored_local; split blesses re_hashed; chunk-only quarantine (dummy never deleted on bad-chunk — R6 invariant); driver has NO status filter so Step-4 post-PONR resume heals; cmd_restore contract byte-for-byte (E1) |
| 7  | Cross-command integrity (scan_unprepped/reclaim/items/tree) | done | (this commit) | full 605✓, smoke 72✓, gate 115✓, sanity 22/22✓ | [model: fable] (v2) `_extras_item_paths` helper; scan_unprepped known_paths + collect_reclaimable PASS 2b extras-aware (classify via shared classify_entry_state; suggest `add_extras`); items_payload/build_tree deliberately NOT touched (season_map rows never emitted — count would mislead); recover --scan + rename_folder confirmed no-change; extras-less output byte-identical |
| 8  | `ENTRY_TYPE_KEYS` doc + schema-guard round-trip | pending | — | — | [model: opus] no new entry type |
| 9  | conftest `sandbox_extras` fixture | pending | — | — | [model: opus] binding hazard |
| 10 | Unit/command tests `tests/test_extras.py` | pending | — | — | [model: sonnet] |
| 11 | Smoke coverage (round-trip + alias sweep + not-flagged) | pending | — | — | [model: sonnet] |
| 12 | Architect docs (ARCHITECTURE/README/...) | pending | — | — | [model: opus] |
| 13 | Register IMP-D19 (tier file + PRIORITY.md + graph) | pending | — | — | [model: sonnet] |
| 14 | Final verification + smoke gate (last) | pending | — | — | [model: sonnet] |

## Multi-candidate tracking (Step 3)
- **Namespace (task-unique — `step-3`/`step-03` are taken by prior tasks):** worktrees `.candidates/d19-step-3/A|B`;
  candidate branches `feature/imp_d19_extras__cand_a|b`; judge `DECISION.md` → `.candidates/d19-step-3/DECISION.md`;
  archive tags → `candidates/imp_d19_extras/step-3/<letter>-chosen|rejected`.
- **Candidate A** (refactor `cmd_push` core into shared `_upload_file`): DONE + committed → branch
  `feature/imp_d19_extras__cand_a` @ **d892991** (+427/−180). Self-report: full suite **603 passed**, smoke 72,
  push tests 11, rollback matrix 63; confidence **high**. Honest caveat: journal lifecycle physically relocated into
  `_upload_file` (observable contract preserved, larger blast radius than B).
- **Candidate B** (isolated `push_one_extra`, `cmd_push` byte-for-byte untouched bar a 2-line wire): DONE + committed →
  branch `feature/imp_d19_extras__cand_b` @ **083d34a** (+398/−7). Self-report: push 11, smoke 72, retry/verify 13,
  sweep 55; confidence **high**.
- **Judge:** DONE → recommends **Candidate B**. Full analysis at `.candidates/d19-step-3/DECISION.md` (committed to the
  feature branch for durability). Rationale: correctness ~tie; B wins criteria 2 (blast radius — `cmd_push` untouched)
  and 3 (E1 rollback — zero new journal/PONR surface) decisively; A wins only criterion 4 (single-source-of-truth, the
  lowest-weighted axis) by relocating the change-gated journal lifecycle into a shared `_upload_file`.
- **Worktrees:** REMOVED after the judge (footgun-safe — they carry `.claude/agents/`); both candidate **branches**
  persist (the real source), and `DECISION.md` is committed.
- **Chosen candidate: — AWAITING USER'S PICK at the 🚦 checkpoint** (judge recommends B). On pick → MERGE_CANDIDATE_WINNER
  the chosen `__cand_*` → run `pytest -q` + smoke on the merged feature branch → commit → archive/tag both candidates.
- **🚦 CANDIDATE CHECKPOINT (task-specific, user-required):** after the judge writes `DECISION.md`, the orchestrator
  does NOT auto-merge. It STOPS, relays the judge's full analysis + recommendation to the user, and the user picks which
  candidate to merge. Only after the user's explicit choice is the winner merged + committed. (Overrides the default
  orchestrator auto-merge flow; see PLAN.md Step 3 🚦 bullet + "Checkpoint 0".)

## In-progress sub-state
_(Step 3 complete — candidate B merged `fcd9e66`, full suite 603✓; candidate branches pending archive-tag. Next: Step 4.)_

**Note for a resuming session:** run `python -m pytest tests -q` (NOT bare `pytest -q` — there is no `testpaths`, so a
bare invocation collects nothing). The full suite lives under `tests/`.

## Blockers / human gates
- **Gate (now):** awaiting user "go" to begin execution.
- **Checkpoint 1 (later):** STOP after opening the PR — do not merge to `main` without the user's OK.
- **Checkpoint 2 (later):** after merge, ask before archiving the branch (annotated `archive/...` tag, then delete).
