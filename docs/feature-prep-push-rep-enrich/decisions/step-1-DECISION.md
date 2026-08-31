# Decision: IMP-D22 Step 1 — Core enrich-composition mechanism + `cmd_prep_push_rep_enrich`

## Outcome

**Winner: Candidate A** ("fully isolated" — never calls `cmd_enrich_metadata`'s apply path).
Branch: `feature/imp_d22_prep_push_rep_enrich__cand_a`, commit `143f58e`.

**This is a recommendation, not a merge.** Per the plan, Step 1 is a user candidate
checkpoint (🚦) — nothing is merged until the user picks. Everything below is evidence to let
the user override this pick with full information, including the strongest case for B.

**One-line rationale:** both candidates are equally faithful, equally rollback-safe, and pass
every acceptance scenario — the tie is broken by the plan's own criterion 3 (blast radius),
where I personally verified A touches **zero lines of existing `cmd_enrich_metadata` logic**
(only the shared, non-judged NFO `api_key` thread), while B's diff **deletes 8 lines and rewrites
them** inside that function's existing, load-bearing body (signature + the `if will_stamp:`
block) — a real, if small and behavior-preserving, edit to shared code that A structurally cannot
regress.

## Step requirements (from PLAN.md lines 316–554)

Build `cmd_prep_push_rep_enrich(manual_id, filepath, split_method=None, split_val=None,
device_id=None, eager_rehash=False, temp_dir=None, extras=None, extras_size=None, tmdb_id=None,
tvdb_id=None, write_nfo=False, no_web=False, rename_choice="ask")` — archive via the untouched
`cmd_prep_push_rep`, then enrich the archived title, with the folder-rename token stamp gated
behind a `rename_choice`-driven confirmation (`"ask"`/`"yes"`/`"no"`). `-tvdbid` is refused
outright before anything runs. An enrich-leg `RollbackHardFail` is caught, printed, and the
command still reports success (the archive already happened). Two candidate architectures were
explicitly pre-specified: **A** — fully isolated, duplicates the small resolve/apply loop in a
private helper, zero edits to `cmd_enrich_metadata`; **B** — adds one keyword-only
`confirm_rename=None` parameter to `cmd_enrich_metadata` and delegates to it directly. Both also
implement an identical, non-forked NFO element-set extension (excluded from judging).

## Judge criteria applied (ranked, from the plan)

1. Fidelity — `test_enrich_metadata.py` green, `cmd_prep_push_rep`/`cmd_prep_push_rep_season`
   provably zero-diff.
2. Rollback change-gate compliance — no new journal/PONR/`RollbackHardFail`-class touch.
3. Blast radius / surgical-changes — lines of EXISTING code touched.
4. Correctness on every Acceptance scenario.
5. Maintainability / DRY (duplication-drift risk for A vs. shared-logic risk for B) — **lowest
   weight, tiebreaker only.**

## What I personally verified (not trusted from CRITIQUE.md)

For both worktrees I ran, from a clean state: `git -C <worktree> diff main -- main.py` (full
hunk-by-hunk read), `git diff main -- tests/test_enrich_metadata.py`, and
`python -m pytest tests/test_enrich_metadata.py -q`, `tests/test_prep_push_rep_enrich.py -q`,
`tests/smoke -q`, and `tests -q` (the full suite) — no `pytest -q` bare invocation, per the
documented collection hazard. Base commit for both candidates is `404e294` (three Step-0
docs-only commits ahead of `main`@`3be95a5`); confirmed via `git log 3be95a5..404e294`.

| Claim | A reported | A verified | B reported | B verified |
|---|---|---|---|---|
| `test_enrich_metadata.py` | 48/48 | **48 passed** (matches) | 49/49 | **49 passed** (matches) |
| new-command suite | 6/6 | **6 passed** (matches) | 9/9 | **9 passed** (matches) |
| `tests/smoke` | 76/76 | **76 passed** (matches) | 76/76 | **76 passed** (matches) |
| full `tests` | 709/709 | **709 passed** (matches) | 713/713 | **713 passed** (matches) |
| `raise RollbackHardFail(` count | 3 (unchanged) | **3** (grep, matches `main`) | 3 (unchanged) | **3** (grep, matches `main`) |
| new `RollbackJournal(`/PONR/journal tokens in diff | 0 | **0** (grepped the diff text) | 0 | **0** (grepped the diff text) |
| `cmd_prep_push_rep`/`_season` bodies zero-diff | yes | **yes** — 0 hunks fall inside lines 7204–7456 (base `def` boundaries confirmed by grep); the one hunk touching that region is a pure 269-line (A) / 125-line (B) insertion starting exactly at old-line 7456 with **0 `-` lines** | yes | **yes** — same check, 0 deletions in the insertion hunk |

**The (b) reporting discrepancy, independently confirmed:** A's diff has hunk
`@@ -2629,6 +2696,7 @@ def cmd_enrich_metadata(...)` adding exactly one line,
`api_key=api_key,`, at the existing `_write_nfo(...)` call site. A's summary said "Zero lines of
`cmd_enrich_metadata` touched," which is literally false; its own file list disclosed the line.
B has the **identical** one-line addition at the identical call site (its own hunk
`@@ -2629,6 +2707,7 @@`). This line exists **only** because both candidates implement the
same, non-forked NFO element-set extension (which needs `api_key` threaded through for the new
`_resolve_imdb_id`/genre/credits calls) — it is not a consequence of either candidate's
enrich-composition architecture. I have treated it as excluded from the A-vs-B comparison per
the task's explicit instruction, and I do **not** credit A with a literal "zero," nor penalize it
for the inaccurate self-report (I flag the self-report inaccuracy itself, below, as a minor
critique-quality ding — not a code defect).

## Candidate summaries

### Candidate A — fully isolated
- Approach: archives via the untouched `cmd_prep_push_rep`, then a new private
  `_enrich_after_archive(real_id, write_nfo, no_web, gate)` re-executes the resolve waterfall
  (`_unit_preset_tmdb_id` → `_resolve_unit_by_id`/`_resolve_unit` → EXA fallback) and the
  additive tmdb_id/title/year/overview write loop, then gates the stamp with `gate(folder,
  new_folder)` before calling the unmodified `cmd_rename_folder`. `cmd_enrich_metadata` itself
  is never called.
- Files: `main.py` (+352/−4, all 4 deletions inside `_write_nfo`'s shared docstring/signature —
  none in `cmd_enrich_metadata`'s body), `tests/test_prep_push_rep_enrich.py` (new, 336 lines),
  `tests/test_enrich_metadata.py` (+114/−0 — pure addition, zero existing lines touched).
- Tests: 48/48 (`test_enrich_metadata.py`), 6/6 (new-command), 76/76 (smoke), 709/709 (full) — all
  independently re-run and matched above.
- Self-critique highlights: claims zero `cmd_enrich_metadata` edits (see discrepancy above,
  correctly self-disclosed in the file list even though the prose overclaimed); flags its own
  `_enrich_after_archive` helper as not literally named in the plan's Files bullet; flags a
  plausible NFO `<director>`-for-shows under-population risk (NFO, excluded from judging).
- Independent assessment:
  - Strengths: `main.py:7524` insertion (hunk `@@ -7456,6 +7524,269 @@`) is 100% additive —
    confirmed 0 `-` lines. `_enrich_after_archive`'s apply loop (title-is-id-shaped guard, year/
    overview write, `will_stamp` formula) is a faithful line-for-line mirror of the base
    `cmd_enrich_metadata` loop body I read at `main.py:2412` (base commit) — same guard, same
    field list. `-tvdbid` wording is byte-identical to the plan's required text (verified via
    grep against both diffs). Test file is a pure 114-line addition to
    `tests/test_enrich_metadata.py` — not even the plan's own sanctioned NFO-assertion exception
    was needed.
  - Weaknesses: **verified, real (low-severity) drift** — `_enrich_after_archive` does NOT
    reproduce the `try: ... except Exception as e: ... n_skipped += 1; continue` defensive wrapper
    that surrounds the resolve waterfall in the original `cmd_enrich_metadata` (base
    `main.py:2487`–`2507`, which the plan's own mirroring instruction, "main.py:~2486-2506",
    actually spans). I traced this through: `_resolve_unit`, `_resolve_unit_by_id`,
    `_exa_resolve_tmdb_id`, and the underlying `_tmdb_get` all carry an explicit "NEVER raises"
    docstring contract, so in practice this wrapper is genuinely belt-and-suspenders and I found
    no live bug — but it is a real, already-realized instance of exactly the "duplication drift"
    risk criterion 5 warns about, on day one of the duplicate's existence. Also: the CRITIQUE.md
    overclaimed "zero lines of `cmd_enrich_metadata` touched" (addressed above).

### Candidate B — additive hook
- Approach: adds `confirm_rename=None` (keyword-only) to `cmd_enrich_metadata`'s signature;
  replaces the existing unconditional `if will_stamp: ok = cmd_rename_folder(...)` block with a
  `do_stamp = True if confirm_rename is None else confirm_rename(folder, new_folder_full)` gate.
  `cmd_prep_push_rep_enrich` composes `cmd_set_tmdb` + `cmd_enrich_metadata(real_id, "--apply",
  [--nfo], [--no-web], confirm_rename=gate)` — the entire enrich leg is one delegated call.
- Files: `main.py` (+235/−12: 4 deletions shared/`_write_nfo`, 1 signature-line deletion + 7
  body-line deletions inside `cmd_enrich_metadata` itself), `tests/test_prep_push_rep_enrich.py`
  (new, 357 lines), `tests/test_enrich_metadata.py` (+147/−0, but 2 of those additions are new
  assertion *lines* inserted into 2 pre-existing NFO tests — no existing assertion's value
  changed).
- Tests: 49/49, 9/9, 76/76, 713/713 — all independently re-run and matched above.
- Self-critique highlights: explicitly names the `cmd_enrich_metadata` edit as "a real, if small
  and short-circuit-proof, blast-radius tradeoff" and asks the judge to weigh it under criterion
  3 — an honest, accurate self-assessment (no discrepancy found).
- Independent assessment:
  - Strengths: genuinely zero duplicated resolve/apply logic — any future change to the resolve
    waterfall (new fallback provider, new field) benefits both call sites automatically. The
    short-circuit is provable by construction (`confirm_rename is None` first-checked) and is
    backed by a dedicated, passing test,
    `test_confirm_rename_omitted_still_stamps_unconditionally` (verified present and green —
    calls `cmd_enrich_metadata(id, "--apply")` with no kwarg and asserts the folder is stamped
    unconditionally). It also gets the "no TMDB key" guard, the preset waterfall, and the EXA
    fallback boundary "for free" — A must (and does) reimplement the API-key guard independently.
  - Weaknesses: **verified, real (small) blast radius** — 8 lines of an existing, widely-depended
    -on function's body are deleted and rewritten (`main.py` base lines ~2412 for the signature,
    ~2596–2602 for the stamp block). `cmd_enrich_metadata` is the single call target for every
    other enrich path in the codebase (CLI dispatcher + all 45 pre-existing tests) — it is exactly
    the kind of shared surface CLAUDE.md's "Surgical Changes" principle (§3, "touch only what you
    must") counsels against touching when an isolated alternative exists and is not materially
    worse. B also duplicates a ~15-line minimal TMDB stub instead of reusing `test_enrich_metadata
    .py`'s file-local `FakeTMDB` (self-disclosed, not independently a defect — just DRY-in-tests,
    same tiebreaker tier).

## Head-to-head comparison

**Criterion 1 (fidelity) — TIE.** Both suites are green (48/48 vs 49/49 — different because B's
2 added assertion-lines land inside existing NFO tests, A's 3 new tests are pure additions; both
fall inside the plan's sanctioned NFO exception and are excluded from the A-vs-B judgment). Both
have a **provably** zero-diff `cmd_prep_push_rep`/`cmd_prep_push_rep_season` — I found 0 hunks
inside either body for either candidate, and the sole hunk touching that region in each diff is a
pure insertion (0 deletions) starting exactly at the base `def cmd_dispatch_fetch` boundary
(line 7456→7459).

**Criterion 2 (rollback compliance) — TIE.** Both diffs contain zero occurrences of
`RollbackJournal(`, `mark_point_of_no_return`, `TXN_JOURNAL_NAME`, or a new `raise
RollbackHardFail(` (grepped the diff text directly, not the candidates' claims). Both add exactly
one new `except RollbackHardFail as hf:` catch site, symmetric in structure and message wording.
`raise RollbackHardFail(` count is 3 in `main`, 3 in A, 3 in B.

**Criterion 3 (blast radius) — A WINS, decisively.** I isolated every `-` line in each diff:
A has 4 deletions total, **all four inside `_write_nfo`'s shared signature/docstring** (the
excluded NFO work) — **zero** deletions anywhere in `cmd_enrich_metadata`. B has 12 deletions:
the same 4 shared `_write_nfo` lines, **plus 8 more** — the `cmd_enrich_metadata` signature line
and 7 lines of its `if will_stamp:` body — that exist purely because of B's architecture, not the
shared NFO work. On the metric the plan itself names ("lines of EXISTING code touched"), A scores
0 and B scores 8, exactly matching the plan's own pre-computed framing ("(0 for A; the small,
provably short-circuit-safe diff for B)").

**Criterion 4 (correctness) — effectively TIE, A has one traceable ding.** Both pass all 4
mandated Acceptance scenarios plus extras (I read every new test in both files; both correctly
exercise the happy path, `--no-rename`, `-tvdbid` refusal pre-disk-touch, and the
`RollbackHardFail`-caught-and-still-returns-True case, with real monkeypatches on
`main.cmd_rename_folder`). A's one real gap — the missing defensive `try/except Exception` around
its duplicated resolve waterfall — is not exercised by any acceptance scenario and I confirmed
every callee it wraps (`_resolve_unit`, `_resolve_unit_by_id`, `_exa_resolve_tmdb_id`,
`_tmdb_get`) is explicitly documented "NEVER raises," so this is a latent robustness gap, not a
live bug today.

**Criterion 5 (DRY/maintainability, tiebreaker only) — B wins, but it's moot.** B has zero
duplicated logic; A duplicates roughly 90 lines (resolve waterfall + apply loop + API-key guard)
in `_enrich_after_archive`, and I found a first real (benign) instance of the drift this invites.
Per the plan, this criterion only matters when 1–4 are tied — here criterion 3 already breaks the
tie, so criterion 5 is not reached.

## Rationale for chosen winner

Candidate A wins because criteria 1, 2, and 4 are ties (both green, both rollback-safe, both
correct on every mandated scenario), and criterion 3 — blast radius — is not a tie: A touches
literally zero lines of `cmd_enrich_metadata`'s existing logic (the sole line it adds there is
the same shared NFO `api_key` thread B also adds), while B deletes and rewrites 8 lines of that
function's signature and stamp-block. The plan ranks blast radius above both correctness-as-a-
tiebreaker and DRY, and CLAUDE.md's "Surgical Changes" principle (touch only what you must) backs
the same ordering for a codebase where `cmd_enrich_metadata` is the single shared call target for
every other enrich path and all 45 pre-existing tests. Given the task's overriding requirement
("existing functionality is not affected"), a change that keeps that surface **byte-for-byte
identical** is a strictly stronger safety guarantee than a provably-short-circuit-safe edit to it
— even a very good one.

Being honest about what A gives up: it duplicates roughly 90 lines of resolve/apply logic that
will need to be kept in sync with `cmd_enrich_metadata` by hand as that function evolves (a new
fallback provider, a new field written on apply, a bugfix in the title-guard) — and I found a
first, already-real (if currently harmless) instance of that drift: the missing defensive
`try/except` around the resolve waterfall. That is the correct price to weigh against a 0-line
existing-code footprint, and per the plan's explicit ranking, it is not enough to change the
outcome, but the user should know it exists and will need occasional manual attention as Step 2
extends this pattern to the season case.

## Why not Candidate B?

B is a strong, honest piece of work — its CRITIQUE.md is the more accurate of the two (no
self-report discrepancy), its short-circuit claim is backed by a real, verified, passing test,
and architecturally it is the more maintainable long-term choice in isolation. It loses solely
because the plan's own criteria rank blast radius (criterion 3) above DRY (criterion 5), and B's
edit to `cmd_enrich_metadata` — while small, well-isolated to one block, and provably behavior-
preserving for every existing caller — is a real, nonzero touch to a function every other enrich
path in the codebase depends on, where A's alternative achieves the same acceptance criteria with
zero touch to that surface.

## What we keep from losing candidate B

- The dedicated short-circuit test pattern (`test_confirm_rename_omitted_still_stamps_
  unconditionally` calling the real function with the kwarg omitted, then asserting unconditional
  behavior) is a good template if a future step ever needs to prove a similarly-shaped additive
  hook is behavior-preserving.
- B's `_write_nfo` additive-fetch block is wrapped in its own `try/except Exception` separate from
  the file-write `try/except` (belt-and-suspenders around the new TMDB detail/credits calls) — A's
  NFO work does something similar per its own critique; whichever wins Step 1's shared NFO
  extension should double-check this defensive layering was carried through (it is excluded from
  this judgment but worth a spot-check before Step 4/5's exhaustive suite).
- **Recommend a follow-up, regardless of which candidate is picked:** if A's pattern is used for
  Step 2 (season case, which the plan says reuses "the SAME strategy"), add the missing
  `try/except Exception` around A's duplicated resolve waterfall in `_enrich_after_archive` (or
  its season-variant equivalent) to fully close the gap identified above — cheap, and removes the
  one place the duplicate is not yet byte-for-byte equivalent to its source.

## Consequence for Step 2 and beyond

Step 2 explicitly "reuses [Step 1's] winning candidate's internal pattern verbatim — do not
re-open that design fork." Picking A means the season variant (`cmd_prep_push_rep_season_enrich`)
will also duplicate a resolve/apply loop rather than delegating to `cmd_enrich_metadata`, and per
Decision 6 the season case is materially more complex (folder-layout duality, per-season-year id
convention, `note=` context lines) — so Step 2's duplicate will be larger and carry more
independent surface to keep in sync than Step 1's ~90 lines. This is the real, compounding cost
of the A pick: every future improvement to `cmd_enrich_metadata`'s resolve/apply logic (new
fallback provider, a bugfix, a new written field) will need to be manually ported to both the
movie and season enrich-duplicate helpers, or they will silently drift. If the user instead picks
B here, Step 2 gets that synchronization for free, at the cost of one more small, well-isolated
edit to `cmd_enrich_metadata` for the season-specific parts of its apply loop.

## Verification status

Both candidates pass all 5 Acceptance items from the plan; I independently confirmed items (1)
(zero-diff autopilots), (2) (test_enrich_metadata.py green, NFO-only exception respected), (4)
(zero rollback-surface touch), and spot-checked (3) (all 4 mandated scenario tests present, read,
and green in both) and (5) (NFO `<tvdbid>`-absence assertions present in both, excluded from the
fork judgment). Confidence: **high** on the evidence in this document — every load-bearing claim
was corroborated against real diff hunks and my own pytest runs, not the candidates' self-reports.

**What I could NOT verify / did not attempt:** I did not exercise a live network path in either
candidate (both test suites run entirely against fixtures/mocks, consistent with the project's
"never touch the real `C:\Media`" rule) — so I cannot personally confirm real-TMDB response shapes
match what the fixtures assume; this risk is identical for both candidates and orthogonal to the
A-vs-B fork. I did not run a targeted test selection beyond the 4 suites already named for each
candidate (`test_enrich_metadata.py`, the new command file, `tests/smoke`, and the full `tests`
directory) — I judged this sufficient since it is the complete Acceptance-mandated test surface
for this step and I ran it directly rather than trusting the reported numbers. I did not
independently re-verify the shared NFO element-set extension's TMDB field-mapping correctness in
depth (explicitly excluded from judging per the task).

**Nothing is merged.** This document is a recommendation for the user's Checkpoint decision on
Step 1; the user may pick A, pick B, or ask for further investigation before either branch
advances.
