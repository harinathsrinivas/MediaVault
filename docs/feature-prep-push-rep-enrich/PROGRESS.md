# IMP-D22 `prep_push_rep_enrich` — Execution Journal (resumable, cross-session / cross-account)

> **Single machine-readable "where we are" for the IMP-D22 build.** The orchestrator updates +
> commits this at the START of every dispatch (intent) and at the END (outcome), so a limit-crash
> mid-agent still leaves a durable trace. A fresh session — even on another Claude account —
> resumes from this file + `PLAN.md` + `DECISIONS.md` + `git log` without re-deriving anything.
> Precedent: `docs/feature-extras/FIXES_PROGRESS.md`.

- **Branch:** `feature/imp_d22_prep_push_rep_enrich` (base `main` @ `3be95a5`, PR #46 / IMP-D21).
- **Framework:** v2 (`.claude/agents/orchestrator-v2.md` playbook, followed by the MAIN session).
- **Orchestrator model:** Opus 5 @ max effort (user-chosen 2026-08-28; the v2 playbook prefers
  Fable for the driving session — the user was told and elected to proceed on Opus 5. Executors
  still route per the plan, so Steps 1/2 land on `executor-fable` regardless).
- **Last updated:** 2026-08-28.

## 🔴 CRITICAL — the user's personal files are PARKED IN A STASH

**`stash@{0}` — "IMP-D22 run: user archiver files parked — RESTORE WITH: `git stash pop --index`"**

Ten of the user's personal files (`Master_Stream_Archiver*.py`, `MatchArchiver*.py`) were
deliberately stashed at the start of this run, at the user's explicit instruction, so that
`git-agent` could operate on a clean tree without its `git add -A` sweeping them into a feature
commit (that exact accident happened once before — see `docs/feature-extras/FIXES_PROGRESS.md`).

**RESTORE OBLIGATION — do not end this task without it.** When execution completes (at the PR,
Checkpoint 1), run `git stash pop --index`. The `--index` flag is REQUIRED: a plain `pop` returns
every file as unstaged and loses the original staged/untracked split.

Expected post-restore state (verify against this exact baseline):

```
AM Master_Stream_Archiver.py
A  Master_Stream_ArchiverV1.py
AM Master_Stream_Archiver_MultiModel.py
AM Master_Stream_Archiver_MultiModel_v3.py
AM Master_Stream_Archiver_MultiModel_v5.py
AM Master_Stream_Archiver_MultiModel_v7.py
AM Master_Stream_Archiver_MultiModel_v8.py
AM MatchArchiver.py
A  MatchArchiver_bkp1.py
?? Master_Stream_Archiver_MultiModel_v6.py
```

**Belt-and-braces backup** (working-tree bytes of all 10 files + the baseline status) lives at
`…/scratchpad/archiver_backup_pre_stash/` for this session. That path is session-scoped and will
NOT survive into a new session — **the stash is the durable copy**. If the stash is ever lost,
tell the user immediately rather than reconstructing silently.

## ⚠️ Standing hazard for EVERY commit on this branch

While the stash holds, the tree is clean and `git-agent` is safe to use normally. **If the stash
is ever restored mid-run, revert to explicit-pathspec commits** (`git commit -m "…" -- <paths>`),
never `git add -A`, never a bare `git commit`.

## ▶ NEXT ACTION

**Step 1** — `[model: fable]` `[candidates: 2]`. Core enrich-composition mechanism +
`cmd_prep_push_rep_enrich`, and the `_write_nfo` element-set extension (D4). Two candidate
worktrees under `.candidates/imp-d22-step-1/{A,B}`, judged by `judge-v2`.

## Blockers / human gates

| Gate | What | Status |
|---|---|---|
| **Checkpoint 1** | Merging the PR into `main`. **Human-gated — never `gh pr merge` without the user's explicit approval.** The run STOPS at PR creation. | not reached |
| **Checkpoint 2** | Archiving the merged branch (annotated `archive/<branch>` tag, then delete local+remote). Human-gated, separate from Checkpoint 1. | not reached |
| **Stash restore** | `git stash pop --index` returning the user's 10 archiver files to their exact prior state. Owed at end of run — see the CRITICAL section above. | **OUTSTANDING** |
| Step 1 candidate pick | PLAN.md marks Step 1 multi-candidate; if the user wants the final say over `judge-v2`, surface both candidates rather than auto-merging. | not reached |

## Fable reachability (Step 0 standing rule)

Per PLAN.md's "Fable reachability protocol", probe before the FIRST `[model: fable]` dispatch of
any session. On failure, route those steps to `executor-opus` at max effort and record the
substitution in the Step table below.

| Session / date | Probe result | Routing in effect |
|---|---|---|
| 2026-08-28 (initial) | `FABLE_PROBE_OK` | fable available |
| 2026-08-28 (after rate-limit reset) | `FABLE_PROBE_OK` | fable available |

## Step status

| Step | Model | Mode | Status | Commit | Tests | Notes |
|---|---|---|---|---|---|---|
| 0 | *orchestrator* | single | **done** | `5ba35fe` + `c33f4d2` | n/a | Performed by the orchestrator directly, as Step 0's own text permits (the fable-probe sub-step *must* be — executors cannot spawn Tasks). Fable probed OK ×2; `PLAN.md`, `DECISIONS.md`, `PROGRESS.md` scaffolded under `docs/feature-prep-push-rep-enrich/`. |
| 1 | fable | **2 candidates** | pending | — | — | Core enrich-composition + `cmd_prep_push_rep_enrich`; also owns the `_write_nfo` element-set extension |
| 2 | fable | single | pending | — | — | `cmd_prep_push_rep_season_enrich` on Step 1's winning mechanism |
| 3 | opus | single | pending | — | — | CLI dispatcher wiring |
| 4 | opus | single | pending | — | — | Movie tests |
| 5 | opus | single | pending | — | — | Season tests (both folder layouts + `tvshow.nfo`) |
| 6 | opus | single | pending | — | — | Smoke-suite coverage |
| 7 | opus | single | pending | — | — | Architect docs |
| 8 | sonnet | single | pending | — | — | Register IMP-D22 + PRIORITY.md + priority-graph |
| 9 | opus | single | pending | — | — | Final verification + smoke gate |

## Run history (append-only — every interruption recorded)

- **2026-08-28** — Branch created off `main` @ `3be95a5` (0 commits). User's 10 archiver files
  backed up then stashed as `stash@{0}`.
- **2026-08-28** — **RATE-LIMIT CRASH.** `git-agent` (haiku) and `planner-v2` (sonnet) both died on
  HTTP 429 "session limit, resets 2:30am Asia/Singapore". Branch creation had already succeeded;
  the planner's Decision-4/NFO edit landed ~90% and left 5 residuals (3 stale "provisional"
  labels at PLAN.md ~223/~294/~1075; Step 5 missing `tvshow.nfo` assertions; Step 7 missing the
  richer-NFO docs). No code was written, no step ran, PLAN.md was NOT corrupted.
- **2026-08-28** — Resumed after the limit reset. Fable re-probed OK. Planner re-dispatched to
  close the 5 residuals. This journal created as the first durable artifact of the run
  (commit `5ba35fe`).
- **2026-08-28** — Planner closed all 5 residuals and *flagged a 6th it deliberately did not
  touch* (PLAN.md line 509: Step 2's `write_nfo=False` comment still read `PROVISIONAL`, which
  contradicted Step 1's `LOCKED` at line 327). The orchestrator fixed that one line. No stale
  `PROVISIONAL` labels remain — the two surviving hits are the prose "no longer provisional".
- **2026-08-28** — **Step 0 done** (`c33f4d2`). Plan copied to
  `docs/feature-prep-push-rep-enrich/PLAN.md` (1393 lines); `DECISIONS.md` written with all 7
  rulings LOCKED plus the standing guardrails. Three files, tree clean, stash untouched.
- **2026-08-28** — **PLANNING GAP caught during Step 1 dispatch prep, before any executor ran.**
  Decision 4's block claimed *"Step 1 ... now owns the `_write_nfo` extension"*, but Step 1's
  `Files` line and `Details` never specced it — both `_write_nfo` mentions inside Step 1 merely
  *call* it. The richer-NFO element set (plain `<tmdbid>`, `<imdbid>` + `<uniqueid type="imdb">`
  via `_resolve_imdb_id`, genre, runtime, premiered, studio, director/actors; `<tvdbid>` NEVER)
  was therefore assigned to **no step**, while Steps 4/5/7 already test and document it. Left
  unfixed this would have burned both Fable candidate runs on an incomplete spec. Planner
  re-dispatched to fold the extension into Step 1's Files/Details/Acceptance and to mark it
  **excluded from judging** (identical work in both candidates — not part of the A-vs-B fork).

## Resume protocol (what a fresh session — or a different Claude account — does FIRST)

1. `git fetch && git checkout feature/imp_d22_prep_push_rep_enrich`. Read this file, then
   `PLAN.md` (root, gitignored working copy) and `docs/feature-prep-push-rep-enrich/DECISIONS.md`.
2. **Check `git stash list` for the parked archiver stash** (see the CRITICAL section above). If it
   is present, the restore obligation is still outstanding. If it is absent and the branch is not
   yet merged, STOP and ask the user — do not guess.
3. `git log --oneline main..HEAD` and reconcile against the Step table. **On disagreement, trust
   git**, not the table.
4. If a step is `in_progress`, inspect the working tree (`git diff --stat`) — a crashed agent's
   EDITS SURVIVE even when its transcript is gone. Review the diff before re-dispatching so
   completed work is never redone.
5. Re-probe Fable before the first `[model: fable]` dispatch of the session; append a row to the
   Fable table above.
6. Human gates are NOT resumable by an agent: **Checkpoint 1** (merge to `main`) and
   **Checkpoint 2** (archive the merged branch) both require the user's explicit approval.
