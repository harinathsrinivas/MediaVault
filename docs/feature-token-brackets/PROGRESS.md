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
- **Test baselines.** Pre-change on this branch: **887 passed**. After merging `main` (commit `a2085c9`,
  which brought 8 upstream commits and 33 new tests): **41 failed, 879 passed = 920 total**. The failure
  count is IDENTICAL across the merge, so the merge introduced zero regressions; all 41 are the
  format-literal assertions Steps 9 and 11 own. **Step 12's gate is 920 + later steps' additions (928 after Step 7), 0 failing** —
  do NOT compare against the stale 887.
- **Last updated:** 2026-09-21 (Steps 9+10 done — suite down to the 2 smoke reds Step 11 owns).

## ▶ NEXT ACTION
**Steps 9 + 10 running in parallel (disjoint files), then Step 11.** Step 9 [sonnet]:
update the 39 existing assertions that hardcode the OLD emit format across `test_enrich_metadata.py`,
`test_prep_push_rep_enrich.py`, `test_prep_push_rep_season_enrich.py`, `test_web_datafns.py`. Step 10
[sonnet]: add ONE square-bracket regression pin to `test_split_brace_escape.py` (no code change).
Step 11 [sonnet] then updates the smoke suite (2 known reds) — run AFTER, since 9/10 execute smoke.

> **Steps 0-8 are done and committed, and `main` is merged in (`a2085c9`).** Step 4's executor died mid-step on a session rate limit; its
> work was already on disk and was verified + finished by the orchestrator (one remaining site,
> `main.py:373`) rather than re-run from scratch — see the Step 4 row.

## Real-library state re-audited 2026-09-21 (supersedes the 2026-09-07 figures in PLAN.md)

The user kept archiving between 2026-09-07 and 2026-09-21 (all four `library_*.json` written
2026-09-20 23:51), so the migration worklist moved. Re-measured read-only:

| Measure | 2026-09-07 (PLAN.md) | 2026-09-21 (now) |
|---|---|---|
| `library_movies.json` `[tmdbid-]` | 135 | **168** (+33 new titles) |
| `library_series.json` entries whose `folder_path` holds a brace token | 202 | **244** |
| Folders on disk with an OLD-style tmdb token | 1 | **1** (`Series/English/Classic/Friends (1994) {tmdb-1668}`) |
| Double-stamped folders (brace AND square in one name) | 0 | **0** |

Two consequences for Step 6 and the post-merge run:

1. **The ancestor case IS the entire remaining migration.** Exactly one folder needs renaming, and
   renaming it re-points **244** library entries via `cmd_rename_folder`'s cascade (up from 202 —
   Friends gained episodes). A leaf-only migration would find nothing to do and report success while
   leaving all 244 entries stale. Candidate selection for Step 6 must turn on ancestor handling.
2. **The double-stamp risk has NOT fired yet, and is still live on `main`.** No folder carries both
   token styles. `Friends` is skipped correctly because it still has a brace, and the ~1400
   `[tmdbid-]` folders have not been through `enrich_metadata` since the foreign migration. The
   guard on `main` is still brace-only, so the exposure persists until this branch merges — this is
   an argument for merging sooner rather than sitting on the branch.

## Upstream `main` — MERGED 2026-09-21 as `a2085c9` (was: scheduled after Step 6)

While this branch was in flight, `main` gained 8 commits (branch point `562fb4a`): the FLAC carry-out
feature, a restore tempdir option, and the mkvmerge `-J` UTF-8 probe fix — **+614 lines in `main.py`**,
plus `mvcommon.py`, `README.md`, `ARCHITECTURE.md`, `improvements/PRIORITY.md` and
`improvements/improvements_tierD.md`.

Assessed 2026-09-21 (read-only, `git merge-tree --write-tree main HEAD`):
- **The merge is CLEAN — zero conflicts.** `mvcommon.py`'s change is a new `MKVEXTRACT_PATH` constant
  near line 28, nowhere near the provider-token helper at the end of the file.
- **`main`'s new code adds NO provider-token handling** (grepped every added line for
  tmdb/tvdb/imdb/_has_tmdb_token/PROVIDER_TOKEN — no hits), so Steps 2/3's "no third detection site /
  no third stamping site" audits remain valid against the merged tree.
- `main` DID change `split_video_file` (new `drop_track` parameter) adjacent to the mkvmerge
  brace-escape comment Step 4 reworded. Merges cleanly; **re-read that comment after merging** to
  confirm it still describes the surrounding code truthfully.

**Plan: merge `main` into this branch immediately AFTER Step 6 lands**, so Step 7's migration tests and
Step 12's full-suite gate run against the real combined code and Step 13 documents merged reality.
Step 6's candidate worktrees were cut from `4f31606` and are mid-flight, so merging now would strand
them. The 887-passing baseline predates these 8 commits — **re-baseline the expected suite count right
after the merge**, before treating any Step 12 number as a regression signal.

## Known follow-up (recorded, deliberately NOT done in this PR)

**Test-file prose still describes brace stamping.** Step 4 realigned `main.py`'s comments/docstrings to the
canonical `[tmdbid-...]` form, but the same stale prose survives in the TEST files' own docstrings and
comments — `tests/test_enrich_metadata.py` (:6, :12, :342, :428, :527, :747, :953, :1626),
`tests/test_prep_push_rep_enrich.py` (:188), `tests/test_prep_push_rep_season_enrich.py`
(:1089, :1265, :1811, :1999, :2050) and `tests/test_web_media_image.py` (:6-9, :59, :66, :106, :188, :460).

No assertion depends on any of it, and Steps 8/9 deliberately left it alone: editing prose would have
produced deletion lines and broken the "existing tests pass byte-unchanged" evidence that is itself part of
each step's proof. Step 13's scope is ARCHITECTURE/README/OPERATIONS_QA, not test prose.

Left as a cheap, low-risk follow-up rather than silently widening this PR's scope. Worth doing before the
next person reads a test docstring and believes MediaVault still stamps braces.

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
| 0  | [model: sonnet] Scaffold `PROGRESS.md` + `DECISIONS.md` | done | b0ff719 | n/a | journal + decisions committed onto the branch |
| 1  | [model: fable, fallback: opus]  🚦(waived, D10) Shared provider-token detect/parse helper in  | done | 68d30df | acceptance 11/11 incl. compound mismatched-bracket case; targeted 66; smoke 80 | **Candidate B merged** (two-stage: vocabulary-free span finder -> separate validator). Ran A=fable, B=opus. Judge verified both against the REAL library (290 folder names, 0 disagreements) and found A had a cross-family bracket-bleed false positive (`{tmdb-123] [tmdbid-456}` matched as one token) that B rejects structurally — decisive under criterion 1. Records: `.candidates/imp-u6-step-1/DECISION.md`, `CRITIQUE-A.md`, `CRITIQUE-B.md`. |
| 2  | [model: opus] Wire the shared helper into every detection/read call site in `main.py` | done | b2a537e | rename_folder+set_tmdb 16; enrich+web_media_image 88; smoke 80; FULL SUITE 887 (= baseline, zero regressions) | `_has_tmdb_token` is now a thin module-qualified wrapper; `_PROVIDER_TOKEN_RE` deleted and `_ancestor_show_folder_image` repointed at the shared helper — this is the fix for the live artwork-inheritance regression. **Authorized deviation:** also repaired `tests/test_enrich_metadata.py:1824`, which dereferenced the deleted constant (reproduced as a real failure first); the drift-pin test now pins `main._has_tmdb_token` against `mvcommon.has_tmdb_token`. Audited for a third detection site: none exists (`_show_folder_of` is geometric; `suggest_target_folder` tokenizes titles; `mainfetch.py` has no provider-token code). |
| 3  | [model: opus] Update every EMIT site to canonical `[tmdbid-…]`/`[tvdbid-…]` | done | 1edef0b | full 41F/846P = 887 (all reds owned by Steps 9/11); smoke 2F/78P; targeted 48 | Standing-sync pair `cmd_enrich_metadata` + `_enrich_after_archive` changed together; `suggest_target_folder` placeholders now `[tmdbid-0000000]`/`[tvdbid-000000]`. All emission built from `mvcommon.CANONICAL_*_TOKEN_FMT` (never a literal) so dual-token stays a one-line change. Confirmed NO third stamping site. **Authorized deviation:** the 'already has a token' print is format-AGNOSTIC, not naming the new format — otherwise a legacy `{tmdb-1668}` folder prints a self-contradictory message. Step 9 must assert `'already has a TMDB token'`. |
| 4  | [model: sonnet] Mechanical doc-string/help-text/comment updates in `main.py` | done | 4f31606 | acceptance grep clean; smoke/full re-run at Step 12 | 15 comment/docstring/help-text sites, zero logic change. Executor died mid-step on a session rate limit with its edits already on disk; the orchestrator verified them against the acceptance criteria and finished the one remaining site (`main.py:373`, the mkvmerge brace-escape comment) rather than re-running the step. `{tmdb-...}` is deliberately RETAINED at main.py:1689/1698/9670 — those docstrings describe what detection ACCEPTS (braces are still valid input) and at :1698 the IMP-C23 history; rewriting them would make the code lie about its own contract. |
| 5  | [model: sonnet] New unit tests for the shared detection helper | done | eff88de | `tests/test_provider_tokens.py` 16 passed | Acceptance (a)-(i) one named test each, plus pins that must not be lost: the compound cross-family case `{tmdb-123] [tmdbid-456}` (the exact input that decided the Step 1 bake-off), `span` integrity + non-overlap + left-to-right ordering (load-bearing for Step 6's in-place rewrite), the canonical constants with str+int ids, None/empty tolerance, and the IMP-C23-style drift-pin asserting `main._has_tmdb_token` == `mvcommon.has_tmdb_token` across every input. |
| 6  | [model: fable, fallback: opus] `[candidates: 2]` (waived, D10) `cmd_migrate_provider_tokens` command | done | 3dd57ef | targeted 28; smoke 2F/78P (known Step-11 reds); judge ran a shared fixture harness against both | **Candidate A merged** (library-entry-driven ancestor walk-up, +308 purely additive). Ran A=fable, B=opus. Decisive finding: the judge injected a mid-batch `RollbackHardFail` and found B **re-raised it uncaught** (no enclosing try/except in the CLI dispatch) — a raw traceback on a real `--apply`, plus it skipped an unrelated folder that would have succeeded; A warns and continues per the existing 'Decision 7' precedent and persists `resume_cmd` in the report. The judge also PROVED the multi-level nested-ancestor case that A had flagged as unproven — it passes. Both share a `remote_bearing` blind spot to pushed `extras` sub-items (a wash, logged as future scope). B's orphan-audit is retained as a future `--audit-disk` follow-up. Records: `.candidates/imp-u6-step-6/DECISION.md`, `CRITIQUE-A.md`, `CRITIQUE-B.md`; tags `candidates/imp-u6/step-6/cand_{a,b}`. |
| 7  | [model: opus] Tests for the migration command | done | cfd020e | `tests/test_migrate_provider_tokens.py` 8 passed; full suite 41F/887P = **928** (+8, +0 failures) | 7 plan cases + an 8th pinning the multi-level nested-ancestor case the Step 6 judge proved (that proof otherwise lived only in DECISION.md and would evaporate on a refactor). **Mutation-verified:** flipping Step 6's `reverse=True` -> `False` makes the command report `renamed=1 errors=2` and leave a half-migrated tree, so deepest-first is genuinely load-bearing and the test is not vacuous (mutation reverted; `git diff main.py` empty). Report path is parsed from stdout, never globbed — the `-1` de-collision suffix sorts BEFORE `.json`, so a name-sorted glob silently returns the wrong run's file (Step 11 should reuse `_read_report`). No bugs found in Step 6. |
| 8  | [model: opus] Artwork-inheritance regression coverage across all three formats | done | b4bddbd | `test_web_media_image.py` 30 -> 38 passed; full suite 41F/895P = **936** | +189 lines, **zero deletions, zero existing tests modified** — the originals passing byte-unchanged is itself half the proof. **Mutation-verified:** restoring the pre-IMP-U6 brace-only predicate makes 6 of the 8 new cases fail while all 30 originals still pass, so the new cases are genuine regression detectors and the originals were always format-agnostic. Carry to Step 13: this file's OWN module docstring + comments (:6-9, :59, :66, :106, :188, :460) still say "nearest `{tmdb-...}` show folder" — left deliberately to preserve the additions-only guarantee. |
| 9  | [model: sonnet] Update existing test assertions that hardcode the OLD emit format | done | 75ceda2 | four files 39F/128P -> **167 passed**; full suite **2F/935P** (only Step 11's smoke pair left) | 56 emit-assertion literals updated across 4 files. **Legacy brace FIXTURES deliberately preserved** — verified by the orchestrator: the IMP-C23 drift-pin tests at `test_enrich_metadata.py:1802-1825` still carry `{TMDB-69590}` / `{TmDb-1}` / the `{tvdb-9}` negative, and :549 now asserts the format-agnostic `'already has a TMDB token'` per Step 3. Converting those would have deleted legacy coverage while turning the suite green — the worse of the two possible mistakes. One self-inflicted regex slip (an f-string mangled to `f"...{[tmdbid-60574]}"`) was caught by the test run and hand-fixed, not papered over. |
| 10 | [model: sonnet] mkvmerge brace-escape regression pin (no code change) | done | 17a21c0 | `test_split_brace_escape.py` 2 -> 3 passed | +29 lines, zero deletions, **zero production code touched** (`git diff main.py` empty). Pins D4: square brackets need NO libfmt escape and the brace escape stays (still required for the real `Friends (1994) {tmdb-1668}` folder). Non-vacuous — the captured `-o` is `... (2012) [tmdbid-79660]\_parts\movie.chunk.%03d.mkv`: token byte-identical, no `[[`/`]]` doubling. |
| 11 | [model: sonnet] Smoke-suite coverage | done | (backfilled next step) | smoke 2F/78P -> **81 passed in 15.06s** (gate is 30s); **FULL SUITE 938 passed, 0 failed** | Fixed the last 2 reds (3 emit literals now built from `mvcommon.CANONICAL_TMDB_TOKEN_FMT`, 3 docstrings). Left `:1019`/`:1041`/`:1365` untouched — caller-supplied `cmd_rename_folder` names, a genuinely different property from 'what does enrich stamp'. New case `test_migrate_provider_tokens_dry_run_then_apply_then_idempotent` exercises dry-run -> apply -> re-apply on a seeded old-format folder. |
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

