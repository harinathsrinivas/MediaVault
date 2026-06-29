# IMP-D19 — Extras: Execution Journal (resumable, cross-session / cross-account)

> **This is the single machine-readable "where we are" for IMP-D19.** Update + commit this file at the END of every
> step (and whenever a step is paused mid-way), in the SAME commit as the step. A fresh session — even on a different
> Claude account/machine — resumes from here. See PLAN.md §"Execution resumability" for the full protocol.

- **Task:** IMP-D19 — add an `--extras` option (Specials / Trailers / Behind-the-Scenes) end-to-end.
- **Branch:** `feature/imp_d19_extras` (NOT YET CREATED — see NEXT ACTION).
- **Plan:** `docs/feature-extras/PLAN.md` · **Decisions (locked):** `docs/feature-extras/DECISIONS.md`.
- **Locked decisions:** A2 (extras nested, grouped per source folder; group key = path relative to title) · B1
  (`add_extras` cmd + `--extras`/`--extras-size` on prep/push family) · C = FLAG-ONLY (`--fetchExtras`, aliases
  `--fetch-extras`/`--extras`/`--extra`; no prompt; absent = no extras) · extras-size default = inherit main split ·
  D1 (full push→dummy→fetch→restore lifecycle) · E1 (additive rollback; main contract byte-for-byte unchanged).
- **Last updated:** 2026-06-29 (Step 0 = `415ae4b`; Step 1 done + committing; Step 2 next).

## ▶ NEXT ACTION
**Step 2 — CLI parsing for `--extras` / `--extras-size` / `--fetchExtras` + new `add_extras` command.** [model: sonnet].
Dispatch the sonnet executor against `main.py` dispatch (argv walkers ~7603–7895) + a new pure `parse_extras_tokens`
helper near `parse_push_group_args`. The Step 1 helpers (`_extras_title_id`/`scan_extras_folders`/
`merge_extras_into_title`) are in place for Step 2 to wire into. Do NOT re-run the planner; do NOT re-open Cards A–E.
At Step 3, honor the 🚦 candidate checkpoint (stop for the user's pick before merging).

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
| 1  | Extras data model + scan/merge/dedup core (A2 grouped) | done | (this commit) | smoke 72✓, schema-guard 2✓, self-check 8/8✓ | [model: opus] `_extras_title_id`/`scan_extras_folders`/`merge_extras_into_title` + ENTRY_TYPE_KEYS comment; `re_hashed` dropped (not True) on byte-change |
| 2  | CLI parsing `--extras`/`--extras-size`/`--fetchExtras` + `add_extras` | pending | — | — | [model: sonnet] |
| 3  | Extras upload phase (independent chunk size; resumable) | pending | — | — | [model: opus] **multi-candidate (2)** — judge required |
| 4  | Extras replace (dummy) phase for reclaim | pending | — | — | [model: opus] rollback-adjacent (E1) |
| 5  | Extras fetch (flag-only `--fetchExtras`, no prompt) | pending | — | — | [model: opus] |
| 6  | Extras restore (merge+verify into recreated subfolder) | pending | — | — | [model: opus] |
| 7  | Cross-command integrity (scan_unprepped/reclaim/items/tree) | pending | — | — | [model: opus] PR#21 class |
| 8  | `ENTRY_TYPE_KEYS` doc + schema-guard round-trip | pending | — | — | [model: opus] no new entry type |
| 9  | conftest `sandbox_extras` fixture | pending | — | — | [model: opus] binding hazard |
| 10 | Unit/command tests `tests/test_extras.py` | pending | — | — | [model: sonnet] |
| 11 | Smoke coverage (round-trip + alias sweep + not-flagged) | pending | — | — | [model: sonnet] |
| 12 | Architect docs (ARCHITECTURE/README/...) | pending | — | — | [model: opus] |
| 13 | Register IMP-D19 (tier file + PRIORITY.md + graph) | pending | — | — | [model: sonnet] |
| 14 | Final verification + smoke gate (last) | pending | — | — | [model: sonnet] |

## Multi-candidate tracking (Step 3)
- Status: not started. Candidate A = refactor `cmd_push` core into shared `_upload_file`; Candidate B = isolated
  `push_one_extra` duplication (cmd_push untouched). Judge `DECISION.md` path: `.candidates/step-3/DECISION.md`.
  Chosen candidate: —.
- **🚦 CANDIDATE CHECKPOINT (task-specific, user-required):** after the judge writes `DECISION.md`, the orchestrator
  does NOT auto-merge. It STOPS, relays the judge's full analysis + recommendation to the user, and the user picks which
  candidate to merge. Only after the user's explicit choice is the winner merged + committed. (Overrides the default
  orchestrator auto-merge flow; see PLAN.md Step 3 🚦 bullet + "Checkpoint 0".)

## In-progress sub-state
_(none — execution not started)_

## Blockers / human gates
- **Gate (now):** awaiting user "go" to begin execution.
- **Checkpoint 1 (later):** STOP after opening the PR — do not merge to `main` without the user's OK.
- **Checkpoint 2 (later):** after merge, ask before archiving the branch (annotated `archive/...` tag, then delete).
