# Decision: Step 2 — Route `oth-` entries in `save_library` (and harden the `else→movies` trap)

## Outcome
Winner: **Candidate B**
Branch: `feature/imp_d18_others_category__cand_b`

This is a CLOSE call. Both candidates are correct and ship-able. B wins on the criteria
as ranked by the step; A is the stronger pick if you weight the project's surgical /
minimal-diff ethos above the trap-hardening goal. See "Caveats" and "If you prefer A"
below so the final human pick is informed.

## Step requirements
Route `oth-` entries in `save_library` to `library_others.json` (constant `LIBRARY_OTHERS`,
file #4). The 3 existing prefixes (`mov`/`tv`/`ani`) must keep routing EXACTLY as before —
byte-identical per-file output for the same input, pinned by
`tests/test_entry_schema_guard.py` + the smoke suite. The step title also calls to
**"harden the `else→movies` trap"** so an `oth-` (or future) entry is not silently absorbed
into `library_movies.json`.

## Judge criteria applied (priority order, as given)
1. **Correctness** — `oth-` lands ONLY in `library_others.json`; `mov`/`tv`/`ani` byte-identical to before.
2. **Safety of the unknown-prefix fallback** — is a typo'd/unknown prefix surfaced, or still silently lost to movies? (Explicitly: "so a future 5th category can't be lost.")
3. **Surgical-ness & house-style fit** — diff size, mirrors existing idiom, readability.
4. **Extensibility** — cost of adding the NEXT prefix.

Weighting note: this is a foundational, data-loss-adjacent routing change. Correctness and
fallback-safety are weighted heavily per the step.

## Per-criterion scorecard

| Criterion (weight) | Candidate A | Candidate B | Winner |
|---|---|---|---|
| 1. Correctness — oth isolated; mov/tv/ani byte-identical | Pass (3 branches untouched → obviously identical) | Pass (shared-by-identity Movies bucket + table-order writes) | Tie |
| 2. Fallback safety — unknown prefix surfaced | Silent (unchanged trap) | Warned on stderr, back-compat kept | **B** |
| 3. Surgical-ness / house-style | ~+5/−0, mirrors idiom one-for-one | ~+54/−14, full rewrite + local ALL_CAPS table | **A** |
| 4. Extensibility — next prefix | 3 edit sites | 1-line table edit | **B** |
| Tests | schema-guard + smoke: 71 passed | schema-guard + smoke: 71 passed; test_mvcommon: 12 passed | Tie |

## Candidate summaries

### Candidate A — `.candidates/step-02/A`
- Approach: Minimal additive `elif`. Adds `oth_data = {}`, an `elif key.startswith("oth"): oth_data[key] = val` branch immediately before the existing `else`, and appends `(LIBRARY_OTHERS, oth_data)` to the atomic-write tuple list. Docstring 3→4 files. The silent `else → mov_data` legacy fallback is kept verbatim.
- Files modified: `mvcommon.py` (`save_library`, lines 569–600).
- Lines changed: ~+5 / −0 (4 logic lines + 1 docstring word). The three existing branches are byte-for-byte untouched.
- Tests: schema-guard + `tests/smoke` → 71 passed. Standalone routing proof green (oth isolated; mov+legacy→movies; tv→series; ani→anime; round-trip equal).
- Self-critique highlights: Honest and accurate. Explicitly flags that it KEEPS the silent `else→movies` fallback as a deliberate scope choice, names Candidate B's louder fallback as strictly better on observability, and lists "not table-driven / 3 edit sites for a 5th category" as a weakness.
- Independent assessment:
  - Strengths:
    - Smallest possible, maximally reviewable diff; mirrors the existing `if/elif startswith` idiom one-for-one and the existing `(LIBRARY_*, *_data)` write-list idiom (`mvcommon.py:583`, `:591`). Textbook CLAUDE.md §2 (Simplicity) / §3 (Surgical).
    - The 3 existing branches are literally unchanged, so byte-identical output for `mov`/`tv`/`ani` is self-evident, not merely tested — the lowest possible regression risk for a foundational routing function.
    - Reads `LIBRARY_*` globals at call time inside the per-call write list (`mvcommon.py:591`), so it is monkeypatch-safe with no special handling. No analogous hazard to the one B had to mitigate.
  - Weaknesses:
    - **Does not harden the `else→movies` trap at all** — it only narrows it (oth no longer falls in). A future 5th-category prefix or a typo (e.g. `oht-`) is STILL silently written into `library_movies.json` with zero signal — precisely the failure mode judge criterion 2 names ("a future 5th category can't be lost"). Left unaddressed by design.
    - 3 edit sites to add the next category (bucket decl, `elif`, write tuple).

### Candidate B — `.candidates/step-02/B`
- Approach: Table-driven rewrite. Introduces a function-local ordered table `_PREFIX_TO_LIB = [("mov", LIBRARY_MOVIES), ("tv", LIBRARY_SERIES), ("ani", LIBRARY_ANIME), ("oth", LIBRARY_OTHERS)]`; buckets (`{path: {} for _, path in _PREFIX_TO_LIB}`) and the atomic-write loop both derive from it. Routing is first-match via `for…else`; the `else` keeps legacy→Movies back-compat BUT emits one `print(…, file=sys.stderr)` warning naming the unrouted key.
- Files modified: `mvcommon.py` (`save_library`, lines 569–637).
- Lines changed: ~+54 / −14 (logic ≈15 lines; remainder is an expanded docstring + rationale comments).
- Tests: schema-guard + `tests/smoke` → 71 passed; `tests/test_mvcommon.py` → 12 passed (incl. `test_round_trip` and `test_atomic_save_failure_leaves_no_tmp_orphan`). Standalone proof green incl. exactly one stderr warning for the legacy key.
- Self-critique highlights: Thorough. Correctly identifies the load-bearing reason the table is FUNCTION-LOCAL (monkeypatch-after-import safety), explains the fallback shares the Movies bucket by identity (byte-identical), and honestly lists the larger diff, per-key warning chattiness, and ALL_CAPS-local-name oddity as weaknesses.
- Independent assessment:
  - Strengths:
    - **Hardens the trap exactly as the step asks**: an unknown/typo'd prefix can no longer vanish silently — it is warned on stderr (`mvcommon.py:617–623`) while still preserving back-compat routing to Movies. The only candidate that protects against "a future 5th category can't be lost."
    - Single source of truth: buckets, write-set, and the warning's known-prefix list all derive from `_PREFIX_TO_LIB`, so a 5th category is a genuine one-line edit and the diagnostic can't drift.
    - Byte-identical for the 3 existing files is preserved: the fallback shares the `mov` bucket by identity (`fallback_path = LIBRARY_MOVIES`; `buckets[fallback_path] is buckets[LIBRARY_MOVIES]`), and write order (movies, series, anime, others) matches insertion order. Atomic-failure test still green (movies written first).
    - The function-local table correctly reads current module globals at call time — its monkeypatch claim is accurate: a module-level table would freeze import-time `C:\Media` strings into the tuples and defeat the `sandbox` fixture's post-import `monkeypatch.setattr`.
  - Weaknesses:
    - Far larger, less surgical diff on a load-bearing routing function; rewrites control flow from a trivial if/elif to a table + `for…else` + identity-shared bucket. More cognitive surface in a data-loss-adjacent path.
    - **Cry-wolf risk**: the warning fires once PER unrouted key on EVERY save. The legacy/no-prefix `else` branch was an intentionally-supported back-compat path in the original (it has its own pinning test, `legacy_nopfx`). If real libraries contain legitimate no-prefix legacy keys, B warns for each on every save — noise that can desensitize against the real signal (an actual typo'd prefix). B cannot distinguish "supported legacy key" from "dangerous typo."
    - `_PREFIX_TO_LIB` is ALL_CAPS yet function-local (mildly unconventional; mitigated by a comment).
    - Rebuilds the table + buckets dict per call (negligible).

## Head-to-head comparison

**Correctness (A vs B):** Tie. Both isolate `oth-` to `library_others.json` and produce
byte-identical `mov`/`tv`/`ani` output for the same input, proven by standalone proof +
green schema-guard/smoke. A's correctness is more *obvious* (3 branches untouched verbatim),
lowering review/regression risk; B's rests on a subtler invariant (fallback bucket shared by
path identity, insertion order). Neither is wrong. Edge to A on *demonstrability*, but
functionally equal.

**Fallback safety (A vs B):** B wins decisively, and this is the criterion the step's own
title ("harden the `else→movies` trap") and judge criterion 2 ("surfaced so a future 5th
category can't be lost") single out. A leaves the unknown-prefix path exactly as silent as
before — it fixes `oth` but does nothing to harden the trap, so a future category typo is
still silently swallowed. B makes the fallback loud while preserving routing. The only
blemish on B here is that it warns on *legitimate* legacy keys too (cry-wolf), not just typos.

**Surgical-ness & house-style (A vs B):** A wins decisively. A is a ~4-line additive change
that mirrors the existing idiom one-for-one — the embodiment of CLAUDE.md §2/§3. B is a
+54/−14 rewrite of a foundational function with an ALL_CAPS function-local and heavy comment
prose. For a reviewer optimizing for minimal blast radius, A is the cleaner change.

**Extensibility (A vs B):** B wins (one-line table edit vs three edit sites in A). Lowest-
weight criterion; only one category is being added now.

## Rationale for chosen winner (B)

Applying the criteria in the priority order the step specifies: criterion 1 (correctness)
is a genuine tie — both are proven byte-identical for the existing three files and both
isolate `oth-` correctly. The first criterion that *distinguishes* the candidates is
criterion 2, fallback safety, and the step foregrounds it twice: the step title literally
says "harden the `else→movies` trap," and judge criterion 2 spells out the intent — "so a
future 5th category can't be lost." Candidate B is the only one that satisfies this: an
unrouted prefix is surfaced on stderr (`mvcommon.py:617–623`) instead of silently absorbed
into `library_movies.json`. Candidate A explicitly and knowingly declines to harden the trap
("I deliberately did not"), so a future `doc-`/typo prefix would still be lost exactly as
today. Because correctness ties and the highest-priority *distinguishing* criterion
(fallback safety, ranked above surgical-ness) goes to B, B wins under the stated rubric. B
also takes criterion 4 (extensibility).

B achieves this without sacrificing correctness: the legacy fallback shares the Movies
bucket by identity (`fallback_path = LIBRARY_MOVIES`), so legacy + `mov-` keys accumulate
into one dict in `data.items()` order — byte-identical to the old `mov_data` — and the write
order (movies, series, anime, others) is preserved, keeping the schema-guard, round-trip, and
atomic-failure tests green (71 + 12 passed). B's headline design decision — keeping
`_PREFIX_TO_LIB` function-local so it reads the monkeypatched `LIBRARY_*` globals at call
time — is independently verified correct and necessary FOR THE TABLE APPROACH; a module-level
table would freeze import-time `C:\Media` paths and route sandbox tests at real media.
Important framing for the reader: this is a hazard B *introduced* by choosing a table and
then correctly mitigated; A never had it, because A references the globals directly in its
per-call write list. So the monkeypatch story is not a point in B's favor *over* A — it is B
cleaning up after its own design choice. It does, however, show B understood the test harness.

What B does WORSE than A, honestly: it is markedly less surgical (+54/−14 vs ~+5/−0) and
rewrites a foundational data-routing function's control flow, which carries more inherent risk
in a data-loss-adjacent path than A's untouchable-three-branches approach; and its warning is
indiscriminate — it cries wolf on legitimate legacy no-prefix keys, not just the dangerous
typo case. These are real costs. They are acceptable here because (a) B's correctness is fully
pinned by the existing round-trip + atomic-failure + schema-guard tests, which all pass, so
the rewrite risk is contained; and (b) the cry-wolf noise is a diagnostic-tuning concern, not
a correctness or data-integrity defect — the *data* still lands in the same files, only an
extra stderr line is emitted. Given the step ranks fallback-safety above surgical-ness, the
trap-hardening B delivers outweighs A's smaller diff.

## Why not the other (A)?
Candidate A is excellent, ruthlessly minimal, and the lowest-regression-risk way to make
`oth-` route correctly — under a pure "smallest correct diff" rubric it would win. It is not
selected because it satisfies only the first half of the step: it routes `oth-` correctly but
does NOT harden the `else→movies` trap, which the step title and judge criterion 2 explicitly
request. In A, a future fifth-category prefix or a typo'd id is still silently written into
`library_movies.json` with no signal — the exact "a future 5th category can't be lost" failure
mode the criterion names. A's author acknowledges this as a deliberate scope reading. Because
the unmet item is the second-highest-priority criterion (above surgical-ness, where A wins),
A places second.

## What we keep from losing candidate A (follow-up suggestions)
- **A's surgical discipline is the better house-style baseline.** If the user prefers A's
  approach, the trap-hardening can be retrofitted onto A with a single line: keep A's verbatim
  three branches and its `else`, but emit B's one-line stderr warning inside the `else` before
  `mov_data[key] = val`. That yields A's minimal diff + B's trap-hardening — the likely
  best-of-both for a future refinement step (NOT synthesized here; V1 picks one candidate).
- **A's "leave the three branches byte-for-byte untouched" instinct** is the safest pattern
  for this load-bearing function and worth preserving in any future edit to `save_library`.

## Caveats / what the orchestrator & user should watch for (winner = B)
1. **Cry-wolf on legacy keys (primary caveat).** B warns once per unrouted key on EVERY save.
   If real libraries legitimately contain no-prefix legacy keys, this prints a warning line per
   such key on every save — noise that can mask the genuine signal (a typo'd prefix). Confirm
   whether legacy no-prefix keys are an expected, supported reality; if so, consider (in a
   follow-up) de-duplicating/summarizing the warning, or scoping it to "looks like a prefix but
   is unknown" rather than "no prefix at all." This is the single most important thing to
   confirm before merge.
2. **stderr surface.** B newly writes to stderr from `save_library`. Verified safe (`sys`
   imported at `mvcommon.py:3`; `main.py:20–23` reconfigures stderr to utf-8/`errors='replace'`)
   and no test asserts clean stderr here (the only `capsys` usages are unrelated file-hash /
   trivia tests). But any downstream tooling that treats `save_library` stderr as fatal should
   be re-checked.
3. **Subtler correctness invariant.** B's byte-identical guarantee depends on the fallback
   bucket being the *same object* as the `mov` bucket and on dict insertion order. It is correct
   and tested, but a future maintainer must preserve that identity if the table is edited (e.g.,
   don't point the fallback at a separate dict).
4. **Function-local ALL_CAPS table.** Intentional and commented (monkeypatch-safety); leave it
   function-local. Do not "tidy" it up to module scope — that would route sandbox tests at real
   `C:\Media`.

## If you (the user) prefer A instead
A is a fully valid winner if you weight the project's minimal-surgical-diff ethos (CLAUDE.md
§2/§3) above the trap-hardening goal, or if legacy no-prefix keys are common enough that B's
warning would be pure noise. A is correct, byte-identical, and the lowest-risk change. The
trade you accept with A: the `else→movies` trap stays silent, so a future fifth category or
typo'd prefix can still be lost without warning (the precise risk the step asked to harden).
The recommended path if choosing A is to apply the one-line follow-up in "What we keep from A"
to add the warning surgically.

## Verification status
Winner (B) passes all acceptance criteria:
- `oth-` routes ONLY to `library_others.json` — proven (standalone proof + isolation assert).
- `mov`/`tv`/`ani` byte-identical to before — preserved via shared-by-identity Movies bucket + table-order writes; `test_round_trip` + schema-guard green.
- Pinned suites green: `tests/test_entry_schema_guard.py` + `tests/smoke` → 71 passed; `tests/test_mvcommon.py` → 12 passed (incl. atomic-failure).
- Additionally satisfies the step-title hardening goal (unknown prefix surfaced, not silent).
Confirmed: B meets the acceptance criteria and is a valid winner. (Close call — A is a valid
fallback if the human prefers minimal diff over trap-hardening.)
