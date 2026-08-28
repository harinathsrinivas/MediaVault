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
- **Last updated:** 2026-08-29.

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

**Step 6** — `[model: opus]`. Smoke-suite coverage in `tests/smoke/test_smoke_all_commands.py`.

**Steps 4 + 5 outcome:** both done, dispatched in parallel (different files, no conflict).
Full suite now **768 passed** (720 → +21 movie, +27 season). Both files pure-append — the 7 and 10
pre-existing tests are byte-untouched and still collect first in their original order.

### 📋 Two PLAN-vs-IMPLEMENTATION deviations found by Step 5 — plan inaccuracies, NOT code bugs
Both are pre-existing, unmodified `main.py` behaviour. The tests pin the REAL contract rather than
working around it, and the test file documents them as D-A / D-B.

- **D-A — flat layout, artifact row 3.** PLAN.md says the season-images endpoint "IS still called …
  but its result is DISCARDED." It is in fact **never called**: `_download_unit_images`
  short-circuits on `if os.path.exists(dest): … continue` BEFORE the
  `/tv/{id}/season/{n}/images` GET. Strictly better (one fewer round trip), identical user-visible
  outcome. Test asserts the endpoint is never hit AND the "kept" line prints.
- **D-B — LOCAL-ALWAYS-WINS, artifact row 7.** PLAN.md lists `tvshow.nfo` among the
  local-always-wins rows. `_write_nfo` documents the OPPOSITE and always has — *"Overwrites an
  existing file (NFOs are regenerable metadata)."* Artwork rows 1/3/4 are pinned as
  local-always-wins; row 7's real contract is pinned separately by `test_nfo_is_regenerated_not_kept`.

**Fixture-hygiene finding (not a product bug):** apostrophes must stay out of fixture folder names
— `main.py` escapes `'` as `'''` for the adb `mv` but pushes the raw path, while `mock_device`
only does `.strip("'")`, so they disagree and the push "fails".

### ⚠️ Step 3 parser caveat — Step 4/5 test authors MUST know this
A bare `-tmdbid` / `-tvdbid` with **no following token** falls through to the path parts rather
than setting the value. This deliberately mirrors the guard shape (`if i + 1 < len(rest)`) that
the pre-existing `device` / `tempdir` / `SIZE_*` arms already use — Step 3 was told to mirror
token-for-token, and the executor rejected deviating. **Consequence: a test asserting that a bare
`-tvdbid` (no id) triggers `_refuse_tvdbid()` would FAIL** — the token just joins the filepath and
prep then fails file-not-found, writing nothing. Harmless, but do not write that test.

**Step 2 outcome:** done. `cmd_prep_push_rep_season_enrich` + `_season_run_target_ids` landed;
the `<director>`-for-shows defect is CLOSED (real code + docstring together this time —
`_tmdb_created_by_names(detail)` for shows, `_tmdb_directors_from_crew` kept for movies).
Verified by the orchestrator: full 720/720 (+10 new), smoke 76/76, `raise RollbackHardFail(`
still exactly 3, both autopilots zero-diff. Took THREE dispatches — the first two executors died
on rate limits (the first mid-docstring, reverted; the second before touching main.py); the third
completed the work and died only while appending to STATUS.md, so all code and tests survived.

**Step 1 outcome:** candidate **A** won (judge verdict + explicit user confirmation), merged as
`d1660a8` after the user-required pre-merge fix. Post-merge gate green: **smoke 76/76, full
710/710**. The judge decision and BOTH candidates' critiques are preserved under
`docs/feature-prep-push-rep-enrich/decisions/` — `.candidates/` is gitignored, so they were copied
into tracked docs; the record of WHY A won must outlive the worktrees.

## Step 2 — crash record + resume guidance (2026-08-28 14:13)

**Step 2's executor died mid-work on a session rate limit** (HTTP 429, "resets 2pm
Asia/Singapore"), while starting sub-task 4b (the `<director>`-for-shows fix). It had NOT yet
touched `cmd_prep_push_rep_season_enrich` or `_season_run_target_ids` at all.

**Partial work found and DELIBERATELY REVERTED.** `git diff` showed 6 insertions / 3 deletions,
entirely inside `_write_nfo`'s DOCSTRING — describing the `<director>` fix without the code change
having been made. Leaving it would have left the source documenting behaviour that does not exist,
so `git checkout -- main.py` was run. Nothing of substance was lost; the tree is clean at
`b53b906`. **Re-dispatch Step 2 from scratch.**

**Worth preserving from the dead run — its `<director>` design choice was correct** and the
re-dispatch should reuse it: for `kind != "movie"`, source `<director>` from the DETAILS payload's
`created_by` array via `_tmdb_created_by_names`, because a SHOW has no series-level crew
"Director" job. The MOVIE path stays on `_tmdb_directors_from_crew` off the credits payload.

**Non-issue, recorded so it is not re-investigated:** a stale planner agent (dispatched ~5h
earlier, resumed late) reported a "documentation regression" claiming Step 1's plan text had lost
its `_write_nfo` assignment. **This was FALSE** — verified directly: Step 1 spans PLAN.md lines
316-555, contains 8 `_write_nfo` mentions, and its `Files` line correctly assigns `_write_nfo` +
`_tmdb_company_names` with the excluded-from-judging note; root and tracked copies are identical.
That agent's edits never landed and nothing needed undoing. It appears to have made a
too-small-window reading error from stale context.

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
| 2 | fable | single | **done** | (this commit) | **full 720/720 (+10) · smoke 76/76** | `cmd_prep_push_rep_season_enrich` + `_season_run_target_ids` on candidate A's merged `_enrich_after_archive` pattern. Single-executor **by design** (the plan reasons re-forking the same A-vs-B decision for the season case is not genuine differentiation). **Also carries the `<director>`-for-shows fix** (see below). Works in the MAIN checkout — no worktree. |
| 3 | opus | single | **done** | (this commit) | full 720/720 · smoke 76/76 · cli_parsers 31/31 | Two new `elif` blocks, 205 lines, PURE INSERTION. Zero-diff proved by AST/byte comparison: both dispatcher blocks + both autopilot functions + `ENTRY_TYPE_KEYS` all identical to `main`. |
| 4 | opus | single | **done** | (this commit) | **28/28** (was 7, +21) · smoke 76/76 | Pure append (0 deletions verified). 14 regression tests over every existing `prep_push_rep` permutation + a byte-for-byte console pin. **Negative control run:** mutated the implementation → 12 tests failed → reverted byte-identically, proving the oracles are not vacuous. |
| 5 | opus | single | **done** | (this commit) | **37/37** (was 10, +27) · smoke 76/76 | Pure append (0 deletions verified). 12 regression ids + the 15-test artifact inventory across BOTH folder layouts. Fake TMDB image bytes encode their own source URL, so every artwork assertion is a **provenance** assertion. |
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
