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

**Step 2** — `[model: fable]` single-executor. `cmd_prep_push_rep_season_enrich`, built on
candidate A's now-merged isolated pattern (`_enrich_after_archive`). Step 2 MUST also carry the
two items flagged below: the **`<director>`-for-shows plan defect** (shows-only, so it lands
naturally here) and D6's season scoping rules — scope by `base_id`, preset the id on an episode
leaf (since `cmd_set_tmdb` refuses a `season_map`), and suppress the "parent of the season" note
for flat/root-level layouts.

**Step 1 outcome:** candidate **A** won (judge verdict + explicit user confirmation), merged as
`d1660a8` after the user-required pre-merge fix. Post-merge gate green: **smoke 76/76, full
710/710**. The judge decision and BOTH candidates' critiques are preserved under
`docs/feature-prep-push-rep-enrich/decisions/` — `.candidates/` is gitignored, so they were copied
into tracked docs; the record of WHY A won must outlive the worktrees.

## Step 1 candidate progress

| Cand | Branch | Worktree | Commit | Status | Tests |
|---|---|---|---|---|---|
| A (isolated) | `…__cand_a` | `.candidates/imp-d22-step-1/A` | `143f58e` | **done** | enrich 48/48 · smoke 76/76 · full 709/709 · schema-guard 4/4 |
| B (hooked) | `…__cand_b` | `.candidates/imp-d22-step-1/B` | `7ca55a8` | **done** | enrich 49/49 · new-cmd 9/9 · smoke 76/76 · full 713/713 |

Both branch from the parent at `404e294` — **B must NOT see A's work**; independence is what
makes the judge's comparison meaningful.

### Orchestrator's independent verification of candidate A (not taken on trust)

- Autopilot bodies: **purely additive**, 269-line insertion with zero deletions in that region.
- `raise RollbackHardFail(` sites: **still exactly 3** — no new PONR. Change-gate honoured.
- `<tvdbid>`: **never emitted** — all 7 occurrences are docstrings, comments, or the refusal text.
- `test_enrich_metadata.py`: **zero existing tests modified** (3 appended).

**Design win the plan did not anticipate.** The plan expected existing NFO assertions would need
updating for the richer element set. A avoided that by gating the new fields behind an
`api_key=None` default — a caller that passes no key gets byte-identical old behaviour, while
`cmd_enrich_metadata --nfo` (which does pass one) gets the richer output. Exactly the deliberate
change D4 wanted, with zero test churn.

**⚠️ Reporting discrepancy — MUST be given to the judge verbatim.** A's summary claims *"Zero
lines of `cmd_enrich_metadata` touched"*, but the diff adds ONE line inside it: `api_key=api_key,`
at the `_write_nfo` call site (hunk `@@ -2629,6 +2696,7 @@`). A disclosed this in its file list
and then contradicted it in its summary. **Correct reading: that line is a consequence of the
SHARED, non-judged `_write_nfo` extension — candidate B will need the identical line — so it does
NOT erode A's "isolated" differentiator.** The judge must be told this precisely, or it will
either penalise A for a false claim or credit it with a literal "zero" that is not true.

### Judge verdict (2026-08-28) — `judge-v2`, written to `.candidates/imp-d22-step-1/DECISION.md`

**Winner: Candidate A**, on criterion 3 (blast radius). Criteria 1/2/4 are ties; criterion 5
(DRY, where B wins clearly) is a tiebreaker only and is never reached. A touches ZERO lines of
existing `cmd_enrich_metadata` logic; B deletes and rewrites 8 lines inside that load-bearing
function (behaviour-preserving, but shared code A structurally cannot regress).

The judge re-ran every suite in both worktrees and read both diffs hunk by hunk; all reported
numbers matched. It confirmed the A self-report discrepancy independently, and correctly treated
the `api_key=api_key,` line as excluded shared NFO work (B has the identical line) — not crediting
A with a literal "zero", not penalising it as a code defect, but noting the inaccurate self-report.

**🔻 REAL DEFECT IN THE WINNER — must be fixed before or at merge.** A's `_enrich_after_archive`
does NOT reproduce the `try: … except Exception: n_skipped += 1; continue` defensive wrapper that
surrounds the resolve waterfall in the original (base `main.py:2487-2507`). Latent robustness gap,
not a live bug — every callee (`_resolve_unit`, `_resolve_unit_by_id`, `_exa_resolve_tmdb_id`,
`_tmdb_get`) is explicitly documented "NEVER raises". But it is a first, already-realised instance
of exactly the duplication-drift risk criterion 5 warns about, on day one of the duplicate's
existence. **Restore the wrapper.**

### 🔁 STANDING MAINTENANCE OBLIGATION created by choosing candidate A

Candidate A's isolation strategy means `_enrich_after_archive` **duplicates** logic that also
lives in `cmd_enrich_metadata`. The judge found one drift instance (the missing defensive
wrapper) and it was fixed pre-merge — but the duplication itself remains, by design:

| Duplicated in `_enrich_after_archive` | Mirrors in `cmd_enrich_metadata` |
|---|---|
| resolve waterfall (preset → search → EXA fallback) + its defensive `try/except` | base `main.py:~2487-2507` |
| apply block (tmdb_id / title / year / overview write loop) | base `main.py:~2572-2591` |
| the "no TMDB API key" guard | its own early bail |

**Whenever `cmd_enrich_metadata`'s resolve or apply logic changes, `_enrich_after_archive` must
be updated in lockstep.** This was the accepted cost of the user's Step 1 pick (blast radius
over DRY); candidate B's branch — which has zero duplication — is preserved as a tag if the
trade is ever revisited. **Step 7 must document this obligation in `ARCHITECTURE.md`** so it is
discoverable outside this journal.

### 🔻 Carried-forward defect found by candidate A (plan bug, not an implementation bug)

For a **SHOW**, `<director>` will usually be EMPTY: the plan specified `_tmdb_directors_from_crew`,
but TMDB carries show creators in `created_by` (i.e. `_tmdb_created_by_names`), not as a crew
"Director" at show level. Candidate A followed the plan as written rather than silently improving
it, and surfaced the issue — the right call. Only manifests for shows, so **fix it in Step 2 (the
season command)**. Do not let it be lost.

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
| 1 | fable | **2 candidates** | **done** | `d1660a8` (squash of `…__cand_a` @ `5178e8f`) | **post-merge: smoke 76/76 · full 710/710** | Core enrich-composition + `cmd_prep_push_rep_enrich`; also owns the `_write_nfo` element-set extension (excluded from judging — identical in both candidates). **Sub-state:** worktrees `.candidates/imp-d22-step-1/{A,B}`, branches `…__cand_a` / `…__cand_b`. Candidates run SEQUENTIALLY (A, then B), then `judge-v2`. See "Step 1 candidate progress" below. |
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
