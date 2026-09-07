# IMP-U6 — Provider-token bracket format: Execution Journal (resumable, cross-session / cross-account)

> **This is the single machine-readable "where we are" for IMP-U6.** Update + commit this file at the END of every
> step (and whenever a step is paused mid-way), in the SAME commit as the step. A fresh session — even on a different
> Claude account/machine — resumes from here. See PLAN.md §"Real-library migration procedure" / "Branch, PR, and
> manual test commands" for the full protocol.

- **Task:** IMP-U6 — canonical `[tmdbid-<id>]` square-bracket provider-token format (replacing `{tmdb-…}`) +
  a crash-safe, ancestor-aware `migrate_provider_tokens` command for the real library.
- **Framework:** v2 — orchestrate per `.claude/agents/orchestrator-v2.md`. Steps 1 and 6 route to
  `executor-fable` (fallback `executor-opus` if Fable is unavailable — probed AVAILABLE at run start,
  `claude-fable-5-1`); Steps 2/3/7/8/12 → `executor-opus`; Steps 0/4/5/9/10/11/13/14 → `executor-sonnet`;
  `judge-v2` for Steps 1 and 6's multi-candidate comparisons; git-agent unchanged.
- **Branch:** `feature/imp_u6_provider_tokens` (deliberately distinct from a different tool's
  `feature/imp_u6_token_brackets`, not read/reasoned from). 2 prior commits before this step: `71871a9`
  (agent-infrastructure waterfall) + a commit adding `docs/feature-token-brackets/PLAN.md`.
- **Plan:** `docs/feature-token-brackets/PLAN.md` · **Decisions:** `docs/feature-token-brackets/DECISIONS.md`.
- **Locked decisions (see DECISIONS.md for full cards):** canonical emit format = single-token
  `[tmdbid-<id>]` (Plex tradeoff accepted; dual-token is a one-line escape hatch via
  `mvcommon.CANONICAL_TMDB_TOKEN_FMT`) · finish the ~202 stragglers forward, not restore-and-redo ·
  remote-path remedy = report-only `remote_bearing` field, no schema/adb change · do not touch the real
  library in this PR · **Steps 1 and 6's 🚦 candidate checkpoints are WAIVED for this run** — see D10.
- **Pre-change test baseline (recorded by the orchestrator on this branch, before any code step):**
  `python -m pytest tests -q` → **887 passed** in 241s. Step 12's full-suite run must be >= this and green.
- **Last updated:** 2026-09-07 (Step 0 — journal scaffolded).

## ▶ NEXT ACTION
**Step 1 — [model: fable, fallback: opus] Design + implement the shared provider-token detect/parse helper
in `mvcommon.py` (`[candidates: 2]`, judge-v2 decides — see D10, no user pause).**

## Resume protocol (first thing a new session does)
1. `git fetch && git checkout feature/imp_u6_provider_tokens` (or create it from `main` if it does not
   exist — first run).
2. Read `PLAN.md` + `DECISIONS.md` + this file (all in `docs/feature-token-brackets/`).
3. Reconcile: `git log --oneline` must match the per-step SHAs in the Step status table below; `git status`
   clean. On disagreement, trust git.
4. Resume at the first non-`done` step (continue from its sub-state notes if `in_progress`); never re-run a
   `done` step.
5. Finish the step → update + commit this file (status + SHA + tests + model actually used) and tick the
   PLAN.md checkbox in the same commit.
6. **Note:** Steps 1 and 6 are multi-candidate (`[candidates: 2]`), but per D10 the 🚦 user checkpoint after
   the judge's verdict is WAIVED for this run — the orchestrator merges the judge's chosen candidate and
   continues without stopping. Record the judge's recommendation and the merged candidate's letter in that
   step's row regardless.
7. Run `python -m pytest tests -q` (NOT bare `pytest -q` — there is no `testpaths`, so a bare invocation
   collects nothing) for the full suite; `pytest tests/smoke -q` is the mandatory gate before any step that
   touches `main.py`/`mainfetch.py`/`mvcommon.py`.

## Step status
| Step | Description | Status | Completing SHA | Tests | Notes |
|------|-------------|--------|----------------|-------|-------|
| 0  | [model: sonnet] Scaffold `PROGRESS.md` + `DECISIONS.md` | done | 063f19f | n/a | journal + decisions committed onto the branch |
| 1  | [model: fable, fallback: opus] `[candidates: 2]` 🚦(waived, D10) Shared provider-token detect/parse helper in `mvcommon.py` | pending | | | `has_tmdb_token`/`find_provider_tokens`/`CANONICAL_TMDB_TOKEN_FMT`/`CANONICAL_TVDB_TOKEN_FMT` |
| 2  | [model: opus] Wire the shared helper into every detection/read call site in `main.py` | pending | | | `_has_tmdb_token` wrapper + delete `_PROVIDER_TOKEN_RE` |
| 3  | [model: opus] Update every EMIT site to canonical `[tmdbid-…]`/`[tvdbid-…]` format | pending | | | `cmd_enrich_metadata`, `_enrich_after_archive`, `suggest_target_folder` (STANDING SYNC pair) |
| 4  | [model: sonnet] Mechanical doc-string/help-text/comment updates in `main.py` | pending | | | 14+ comment/docstring sites, no logic change |
| 5  | [model: sonnet] New unit tests for the shared detection helper | pending | | | `tests/test_provider_tokens.py` (NEW) |
| 6  | [model: fable, fallback: opus] `[candidates: 2]` 🚦(waived, D10) `cmd_migrate_provider_tokens` command | pending | | | ancestor-aware, deepest-first, idempotent, dry-run-by-default |
| 7  | [model: opus] Tests for the migration command | pending | | | `tests/test_migrate_provider_tokens.py` (NEW), 7 cases |
| 8  | [model: opus] Artwork-inheritance regression coverage across all three formats | pending | | | `tests/test_web_media_image.py`, additive parallel cases |
| 9  | [model: sonnet] Update existing test assertions that hardcode the OLD emitted format | pending | | | enrich/prep_push_rep_enrich/web_datafns test files |
| 10 | [model: sonnet] mkvmerge brace-escape regression pin (no code change) | pending | | | `tests/test_split_brace_escape.py`, 1 new test |
| 11 | [model: sonnet] Smoke-suite coverage | pending | | | `tests/smoke/test_smoke_all_commands.py`, migrate case + assertion update |
| 12 | [model: opus] Full verification pass — run the complete suite and fix any fallout | pending | | | full suite + smoke gate, record exact counts |
| 13 | [model: sonnet] Documentation updates | pending | | | `ARCHITECTURE.md`, `README.md`, `docs/OPERATIONS_QA.md` |
| 14 | [model: sonnet] Register IMP-U6 | pending | | | `improvements_tierU.md`, `PRIORITY.md`, `priority-graph.html` |

## Multi-candidate tracking (Steps 1 and 6)
Both steps use the standard orchestrator multi-candidate flow (isolated worktrees, `judge-v2`), but **per
the user's override for this run (see D10 in DECISIONS.md), the 🚦 checkpoint after the judge's verdict is
WAIVED** — the orchestrator merges the judge's recommended candidate immediately and continues to the next
step without stopping for a user pick. Each step's row above, once completed, must record: both candidates'
branch/worktree namespace, the judge's recommendation + one-line rationale, which candidate was actually
merged, and the model that actually executed each candidate (per `.claude/MODEL_WATERFALL.md`, since Fable
availability can change between steps).

- Step 1 candidates: A = single unified compiled regex (`[candidate-model: fable]`), B = two-stage
  pipeline — find brackets, then validate vocabulary (`[candidate-model: opus]`).
- Step 6 candidates: A = library-entry-driven per-entry ancestor walk-up (`[candidate-model: fable]`),
  B = category-root disk walk with library cross-reference (`[candidate-model: opus]`).

## In-progress sub-state
_(Step 0 complete this commit. Next: Step 1 — dispatch two candidates in isolated worktrees per the
multi-candidate flow, using the "Shared detection/parsing contract" section of PLAN.md as the locked
external behavior both candidates must satisfy.)_

**Note for a resuming session:** run `python -m pytest tests -q` (NOT bare `pytest -q` — there is no
`testpaths`, so a bare invocation collects nothing). The full suite lives under `tests/`.

## Blockers / human gates
- **Checkpoint 🚦 after Step 1's judge verdict (user picks the detection-helper candidate)** — per PLAN.md,
  but **WAIVED for this run** by explicit user override (D10): judge-v2 decides, orchestrator merges and
  continues, no pause.
- **Checkpoint 🚦 after Step 6's judge verdict (user picks the migration-command candidate)** — per
  PLAN.md, but **WAIVED for this run** by the same override (D10): judge-v2 decides, orchestrator merges
  and continues, no pause.
- **Checkpoint 1 (later, real):** STOP after opening the PR — do not merge to `main` without the user's
  explicit confirmation.
- **Checkpoint 2 (later, real):** after merge, ask before archiving the branch (annotated `archive/...`
  tag, then delete).
- Real `--apply` against `C:\Media` is explicitly OUT OF SCOPE for this PR (Decision #6/D6) — it is a
  separate, user-run, post-merge procedure documented at the end of PLAN.md.
</content>
</invoke>
