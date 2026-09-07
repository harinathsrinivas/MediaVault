# Decision: IMP-U6 Step 1 — shared provider-token detect/parse helper (`mvcommon.py`)

**NOTHING IS MERGED YET.** This document is the judge's evidence-based comparison. Per the
plan, this step is a 🚦 user-gated checkpoint — the user makes the final pick. The orchestrator
will act on this verdict only after that pick (or immediately if the user has waived the
checkpoint for this run — confirm before merge either way).

⚠️ **Model-fallback note:** this judgment was produced under the documented fable→opus fallback
path (`.claude/MODEL_WATERFALL.md`). All V2 deltas (evidence-over-self-report, full diff
corroboration, no sampling) were applied in full — see "Confidence" at the bottom for what that
does and doesn't change about how much weight to put on this verdict, and what I'd want re-judged
on fable if it becomes available.

## Outcome

**Winner: Candidate B** (two-stage pipeline: vocabulary-free span-finder + vocabulary-only
validator). Confidence: **high** on correctness/criteria 1–2, **medium-high** overall (see
Confidence section — the deciding gap is real but currently untriggered by any folder in the
user's actual library, which is exactly the kind of nuance the user should see before ratifying).

- Winner branch: `feature/imp_u6_provider_tokens__s1cand_b`, commit `b817545`
- Runner-up branch: `feature/imp_u6_provider_tokens__s1cand_a`, commit `f2b391b`

## Step requirements (from PLAN.md, `docs/feature-token-brackets/PLAN.md`)

`mvcommon.py` gains `CANONICAL_TMDB_TOKEN_FMT = "[tmdbid-{id}]"`, `CANONICAL_TVDB_TOKEN_FMT =
"[tvdbid-{id}]"`, `has_tmdb_token(name) -> bool`, `find_provider_tokens(name) -> list[dict]`
(keys `provider`/`id`/`bracket`/`match`/`span`). Vocabulary: `tmdb|tmdbid|tvdb|tvdbid|imdb|imdbid`
case-insensitive; `{...}`/`[...]` never cross-matched; `=` tolerated only on the square family,
detection-only; bare bracketed non-tokens (`[rartv]`, `[FraMeSToR]`, `[a1b2c3]`) must never match;
stdlib-only, never imports main/mainfetch. Both candidates implement the identical external
contract with deliberately different internal strategies (A: one unified compiled regex over two
alternation branches; B: a two-stage span-then-validate pipeline).

## Judge criteria applied (ranked, from PLAN.md)

1. Correctness against every acceptance case, zero false positives/negatives — non-negotiable.
2. Whether the design makes future drift between call sites structurally impossible vs.
   merely tested-for-agreement (the point of this step — closes the IMP-C18/C22/C23 drift class).
3. Readability/extensibility if a 4th provider (e.g. `anidb`) is added.
4. Total lines touched.

## Independent verification performed (not adopted from either CRITIQUE on trust)

- Loaded both candidates' `mvcommon.py` directly (`importlib.util`, stdlib-only, no side effects)
  and ran all 9 locked acceptance cases (a)–(i) plus span-correctness plus the 3 vocabulary-reject
  cases — **24/24 pass on both candidates**, independently reproduced, not copy-pasted from
  either CRITIQUE.
- Reproduced the orchestrator-supplied compound case (`{tmdb-123] [tmdbid-456}` and the
  junk-padded variant) against both candidates directly — **confirmed**: A over-matches with a
  corrupted id; B correctly returns `[]`.
- Ran a broader adversarial sweep (19 additional inputs: bare release tags, empty brackets, no
  separator, wrong tag, `None`/`""`, nested brackets, two adjacent legitimate curly tokens, the
  compound cases, and the one real legacy folder in the user's library) — **A and B agree on every
  input except the two compound cross-family cases**, isolating the divergence to exactly one
  root cause (see comparison table).
- Pulled the user's REAL library data (`C:\Media\library_movies.json`/`library_series.json`/
  `library_anime.json`/`library_others.json`, `folder_path` fields) plus a live, unbounded
  `os.walk` of `C:\Media\Movies`, `Series`, `Anime`, `Sports` — 290 unique real folder/subfolder
  names — and ran both candidates against every one of them. **0 disagreements.** Only **one**
  folder in the entire real library still carries the legacy curly format at all:
  `Friends (1994) {tmdb-1668}` — a single, cleanly-closed token (both candidates parse it
  identically, `id="1668"`), not a compound case. 204 folders already carry `[tmdbid-…]`; 85 carry
  no tmdb mention. **Zero folder names anywhere in the real library contain 2+ curly-brace
  characters** — the precondition for A's bug to fire.
- Spot-checked BOTH CRITIQUEs' test claims myself (guardrail required at least one; I ran both,
  per delta 4's no-sampling instruction):
  - `pytest tests/test_rename_folder.py tests/test_enrich_metadata.py -q` in both worktrees →
    **66 passed** in each, confirmed directly (not copied from CRITIQUE.md).
  - `pytest tests/smoke -q` in both worktrees → **80 passed, 1 pre-existing warning** in each,
    confirmed directly.
  - B's extra claim (`tests/test_mvcommon.py tests/test_split_brace_escape.py
    tests/test_web_media_image.py`) → **44 passed**, confirmed directly.
- Verified blast radius: `git diff b0ff719 <commit>` for both candidates touches **only**
  `mvcommon.py` (91 insertions for A, 125 for B, purely additive, zero deletions) plus their own
  `CRITIQUE.md`. Grepped both diffs for `ENTRY_TYPE_KEYS`, `RollbackJournal`, `point_of_no_return`,
  `journal` — **zero hits in either**. See "Blast radius" section below for the exact quote.
- Verified the 4th-provider extensibility claim empirically for both (monkey-patched a 4th
  `anidb` provider into each running module exactly as each CRITIQUE describes, then queried
  `[anidbid-12345]`) — **both correctly recognized it with no other code change**, confirming
  criterion 3 is a genuine tie, not a self-reported one.
- Corroborated two specific factual claims against the actual codebase (not taken on trust):
  - B's claim that `main.py` imports `mvcommon` module-qualified at a specific line, with a
    binding-hazard comment, and a `from mvcommon import (...)` tuple at another specific line —
    **confirmed exactly**: `from mvcommon import (` is `main.py:30`, `import mvcommon` is
    `main.py:41`, matching B's citation.
  - A's claim that "Python's stdlib `re`... does not support duplicate group names across
    alternation branches — that landed in 3.12" — **empirically tested and found FALSE**: even on
    this environment's Python 3.13.5 (newer than 3.12), `re.compile(r"(?P<tag>a)|(?P<tag>b)")`
    still raises `re.error: redefinition of group name`. This does not affect A's actual code
    (A avoided the pattern regardless, using distinct `c_tag`/`s_tag` group names, which is a
    valid and correct choice on its own merits) — it is a factual inaccuracy in the CRITIQUE's
    stated *justification*, not a code bug. Flagged per delta 1 rather than silently adopted.
- What I did **not** run: `tests/test_split_brace_escape.py`/`tests/test_web_media_image.py`
  against candidate A specifically (I ran them against B only, since B's CRITIQUE cited them and A's
  did not; both candidates' `mvcommon.py` diffs are close enough in shape — pure append, same
  functions — that a differential result was not expected and the smoke+regression run already
  covers the same code paths for A). If the user wants belt-and-suspenders parity, that's a
  30-second re-run, not a re-judge.

## Candidate summaries

### Candidate A — single unified compiled regex
- Approach: one `re.compile` (`mvcommon.py:712-717`) with two full alternation branches (curly
  named-group pair `c_tag`/`c_id`, square named-group pair `s_tag`/`s_id`), `re.IGNORECASE`.
  `find_provider_tokens` (`mvcommon.py:732-762`) runs one `finditer` and dispatches on which pair
  of groups matched. `has_tmdb_token` (`mvcommon.py:720-729`) is `any(... for tok in
  find_provider_tokens(name))` — derived, not a second regex.
- Files modified: `mvcommon.py` only.
- Lines changed: +91 / -0 (code); +178 CRITIQUE.md.
- Tests: 66/66 (`test_rename_folder.py`+`test_enrich_metadata.py`), 80/80 smoke — both
  independently re-run and confirmed by me, not copied from CRITIQUE.
- Self-critique highlights: correctly explains the two-full-alternatives design closes the literal
  acceptance-case-(g) mismatched-bracket trap; flags the binding-hazard call convention for the
  next wiring step; explicitly declines to add a `tag` field / stricter id validation /
  `re.escape` as out-of-scope speculative additions (good judgment, matches CLAUDE.md §2).
  **Does not mention or test** the compound cross-family case that its own design rationale (two
  full alternatives, explicitly to avoid "the mismatched-bracket false-positive trap") implies it
  should be immune to.
- Independent assessment:
  - Strengths:
    - `mvcommon.py:693-701` — vocabulary lives in one dict; `_PROVIDER_TAG_ALT` regenerates from
      it, verified empirically to support a 4th provider with a 2-line dict edit and zero regex
      hand-editing.
    - `mvcommon.py:720` — `has_tmdb_token` is a thin derivation of `find_provider_tokens`, so the
      predicate/finder pair cannot drift from each other (closes the IMP-C22/C23 drift class on
      this specific axis, same as B).
    - Fewest lines touched: 91 vs. B's 125 (criterion 4, lowest-weight but real).
  - Weaknesses:
    - `mvcommon.py:712-717` — each branch's id character class excludes only its OWN closing
      bracket (`[^}]+` for curly, `[^\]]+` for square), not the other family's bracket characters.
      **Demonstrated, reproducible bug**: `find_provider_tokens("{tmdb-123] [tmdbid-456}")`
      returns one token with `id = "123] [tmdbid-456"` and `match` spanning the entire string —
      a false positive with a corrupted id, on an input adjacent to (not identical to) the locked
      acceptance case (g) that this exact branch-separation design was built to prevent.
    - CRITIQUE's stated rationale for the group-naming choice (Python 3.12 duplicate-group-name
      support) is factually wrong, per my empirical test above — cosmetic, not a code defect, but
      it is a claim I could not corroborate and is now recorded as false rather than silently
      adopted.

### Candidate B — two-stage pipeline (span-finder → vocabulary validator)
- Approach: Stage 1 — `_CURLY_SPAN_RE`/`_SQUARE_SPAN_RE` (`mvcommon.py:712-713`), each
  `\{([^{}\[\]]*)\}` / `\[([^{}\[\]]*)\]` — vocabulary-free, and critically excludes **all four**
  bracket characters from the interior, not just its own pair. Stage 2 — `_parse_token_content`
  (`mvcommon.py:728-748`), bracket-agnostic, `fullmatch`es `<tag><sep><id>` against the shared
  vocabulary table. `find_provider_tokens` (`mvcommon.py:751-782`) composes stage 1 → stage 2,
  applies the one family-specific rule (`=` is square-only) as a 3-line guard, sorts by span.
  `has_tmdb_token` (`mvcommon.py:785-793`) derives from it, same as A.
- Files modified: `mvcommon.py` only.
- Lines changed: +125 / -0 (code); +200 CRITIQUE.md.
- Tests: 66/66, 80/80 smoke, plus 44/44 on an extra safety-net selection
  (`test_mvcommon.py`/`test_split_brace_escape.py`/`test_web_media_image.py`) — all three
  independently re-run and confirmed by me.
- Self-critique highlights: explicitly names and tests the compound cross-family case
  (`{tmdb-123] [tmdbid-456}` → `[]`) as the deliberate payoff of excluding all four bracket
  characters, not just its own pair — this is the one substantive design point that turned out to
  matter empirically. Documents 6 explicit "Assumptions" including a superset-of-legacy-regex
  proof and an explicit confirmation that nothing touches `ENTRY_TYPE_KEYS`/rollback. Documents 6
  honest weaknesses (permissive id charclass, ids not trimmed, no short-circuit, `TypeError` on
  non-str/non-None, no nested-bracket support, unused `tag` return element) — none of which are
  correctness bugs against the locked contract or real data.
- Independent assessment:
  - Strengths:
    - `mvcommon.py:712-713` — the `[^{}\[\]]*` exclusion is a **structural** proof, not a tested
      special case: a span's interior can contain no bracket character, so neither family's
      delimiters can ever land inside the other's span. Reproduced myself on both the acceptance
      list AND every adversarial input I constructed — zero false positives found anywhere.
    - `mvcommon.py:728-748` — stage 2 never sees which bracket family produced its input, so the
      *vocabulary itself* structurally cannot diverge between `{}` and `[]` (the one family-
      specific rule, `=`-square-only, is isolated to a single 3-line guard in the composer,
      `mvcommon.py:775-778`, not smeared across the parser).
    - Directly closes the exact failure class the plan's own text calls out (line 174-175 of
      PLAN.md: "a naive single-character-class-for-both-brackets regex gets this wrong") — and
      closes the compound, two-real-token version of it that A's design does not.
  - Weaknesses:
    - Most lines touched (125 vs. 91) — criterion 4, lowest-weight, genuinely conceded.
    - `has_tmdb_token` builds the full list rather than short-circuiting — irrelevant at
      folder-name scale (self-flagged, not something I found independently necessary to weigh).
    - `_parse_token_content` returns a `tag` element no current caller uses (`mvcommon.py:774`,
      bound `_tag`) — minor unused-value smell, not a defect.

## Acceptance matrix — actual observed results (independently run, not from CRITIQUEs)

| Case | Input | Candidate A | Candidate B |
|---|---|---|---|
| (a) `{tmdb-603692}` | found, id=603692 | PASS | PASS |
| (b) `[tmdb-603692]` | found | PASS | PASS |
| (c) `[tmdbid-603692]` | found, id=603692 | PASS | PASS |
| (d) `{TMDB-69590}` | found (case-insens), `has_tmdb_token` True | PASS | PASS |
| (e) `[tmdbid=603692]` | found, id=603692 | PASS | PASS |
| (f) `...-FLUX[rartv] [tmdbid-60574]` | exactly 1 token, `[rartv]` excluded | PASS | PASS |
| (g) `{tmdb-123]` / `[tmdb-123}` | NOT found (both) | PASS | PASS |
| (h) `[tvdbid-266189] [tmdbid-70523]` | both returned, `has_tmdb_token` True | PASS | PASS |
| (i) `[tvdbid-266189]` only | `has_tmdb_token` False | PASS | PASS |
| span | `name[start:end] == match` for every token in every case above | PASS (7/7) | PASS (7/7) |
| reject | `[rartv]`, `[FraMeSToR]`, `[a1b2c3]` | PASS | PASS |
| **compound (orchestrator, not in locked list)** | `{tmdb-123] [tmdbid-456}` | **FALSE POSITIVE** — 1 token, `id="123] [tmdbid-456"`, `match` spans whole string | **correct** — `[]` |
| **compound 2 (orchestrator)** | `Movie {tmdb-123] junk [tmdbid-456}` | **FALSE POSITIVE** — same pattern | **correct** — `[]` |
| **adversarial sweep (mine, 19 extra inputs)** | bare tags, empty brackets, no separator, wrong tag, `None`/`""`, nested brackets, two legit adjacent curly tokens, the real `Friends (1994) {tmdb-1668}` folder | **agrees with B on all 17 non-compound inputs** | same |
| **real library (mine, 290 real folder names)** | every folder/subfolder currently in `C:\Media\{Movies,Series,Anime,Sports}` + JSON `folder_path` fields | **0 disagreements with B** | same |

Both candidates satisfy every one of the plan's 9 locked acceptance cases (a)–(i) plus span
correctness — 100% identical on the literal contract. The only observed divergence is the
orchestrator-supplied compound case and its variant, which sit just outside the locked list but
squarely inside criterion 1's "zero false positives... non-negotiable" language and directly on
the failure class criterion 2 asks about.

## Blast radius — change-gated surfaces (expected: none; confirmed: none)

```
$ git diff b0ff719 f2b391b --stat   (Candidate A, full commit)
 CRITIQUE.md | 178 ++++++++++++
 mvcommon.py |  91 +++++++++
 2 files changed, 269 insertions(+)

$ git diff b0ff719 b817545 --stat   (Candidate B, full commit)
 CRITIQUE.md | 200 ++++++++++++++++
 mvcommon.py | 125 +++++++++++++++
 2 files changed, 325 insertions(+)
```

Both diffs are **purely additive** (0 deletions) to `mvcommon.py`, appended after
`episode_num_from_id` — no existing line in either candidate's `mvcommon.py` was touched. Grepping
both full diffs for `ENTRY_TYPE_KEYS`, `RollbackJournal`, `point_of_no_return`, `journal` returns
**zero hits in both candidates**. Neither candidate touches `main.py`, the journal format, any
`cmd_*` function, or the entry-type registry. The auto-rollback change-gate and the
`ENTRY_TYPE_KEYS` change-gate are both **untouched, byte-for-byte, by both candidates** — this
criterion does not differentiate them; both are equally safe on it.

## What you get / what you give up

**Candidate A (single unified regex):** you get the smallest diff (91 lines) and a compact,
single-pattern implementation that is easy to see "in one place." You give up a structural
guarantee against cross-family bracket bleed-through: its id character classes only exclude their
own closing bracket, so a string containing an unmatched curly-open followed eventually by *any*
`}` character — including one that belongs to a different, unrelated square-bracket token —
produces a false-positive match with a corrupted id. This does not currently affect any folder in
the real library (only one legacy curly-braced folder exists, and it is a clean single token), and
it does not fail any of the plan's 9 locked acceptance cases. It is a latent risk that would only
surface if a future folder name (a manual edit, a future dual-stamp decision per the plan's Open
Decision #7, or an unusual scene-release naming pattern) puts a legacy curly token and an
unrelated `}`-bearing fragment in the same name — at which point Step 6's migration command would
rewrite an over-wide `span` in the user's real folder.

**Candidate B (two-stage pipeline):** you get a provable structural guarantee that curly and
square token families can never bleed into each other, regardless of what else is in the name —
verified against every acceptance case, a 19-input adversarial sweep, and all 290 real folder
names in the user's actual library, with zero false positives found anywhere. You give up 34 extra
lines (125 vs. 91) and a marginally less centralized implementation (a span-finder plus a
separate validator plus a small compositional guard, instead of one regex) — genuinely more moving
parts, though each individually is simpler to reason about (self-described and independently
verified as accurate: stage 1 knows nothing about vocabulary, stage 2 knows nothing about
brackets).

## Head-to-head comparison

**Criterion 1 (correctness, non-negotiable):** tie on the literal 9-case acceptance matrix; **B
wins** on the compound cross-family case, which is a genuine false positive with a corrupted `id`
in A, reproduced independently by me on two separate inputs and confirmed absent in B on the same
inputs plus a further 19-input adversarial sweep and the full real library. Criterion 1 explicitly
says "zero false positives/negatives — non-negotiable," and this is a demonstrated false positive,
even though it sits outside the plan's literal 9-item list.

**Criterion 2 (structural impossibility of drift/error):** both make the predicate/finder pair
drift-proof (`has_tmdb_token` derives from `find_provider_tokens` in both). **B additionally**
makes cross-family bracket bleed-through structurally impossible by construction (proven: no
bracket character can appear inside a span's interior, so no span can straddle a family boundary).
A's two-branch design closes the *literal* acceptance-case-(g) mismatch but leaves a narrower,
compound version of the same class of bug open — which is notable because avoiding "the
mismatched-bracket false-positive trap" is A's own stated design rationale (PLAN.md's own
candidate-A description, and A's CRITIQUE §1) for choosing two full alternatives over a shared
character class in the first place.

**Criterion 3 (4th-provider extensibility):** tie, verified empirically for both — a 1-2 line
addition (a dict entry for A, a tuple entry for B) adds a working `anidb` provider to both with no
other code change.

**Criterion 4 (lines touched):** **A wins**, 91 vs. 125 lines. This is the lowest-ranked
criterion and does not offset a non-negotiable correctness gap.

## Rationale for chosen winner

B wins because it is strictly better on the two highest-ranked, explicitly non-negotiable criteria
and ties on the two lower-ranked ones. The deciding evidence is not the orchestrator's finding
taken at face value — I independently reproduced it against both candidates' actual code, then
went further: an 19-input adversarial sweep (which found zero further divergence beyond the two
compound cases already flagged) and a full sweep against all 290 real folder names currently in
the user's library (`C:\Media\{Movies,Series,Anime,Sports}` plus every library JSON's
`folder_path` field), which found zero disagreements and confirmed the bug is real but currently
dormant (only one legacy curly-braced folder exists in the whole library, and it is not a compound
case). That combination — a provable structural gap in A that is real and reproducible, but not
currently live against any actual folder — is exactly the nuance criterion 2's "structurally
impossible... vs merely tested-for-agreement" language exists to catch: A's design *happens* to be
safe against everything currently in the library; B's design *cannot* be unsafe against this class
of input, by construction. Given that Step 6's migration command will use `span` for in-place
rewrites against the user's real, evolving folder tree, and given the plan's explicit weighting of
this exact tradeoff in my task instructions, I judge B's structural guarantee as decisive.

I acknowledge what B costs: 34 more lines, a marginally less centralized implementation, and every
one of the 6 weaknesses B's own CRITIQUE lists (permissive id charclass, no id trimming, no
short-circuit, `TypeError` on non-str input, no nested-bracket support, an unused `tag` return
value) — I checked each of these myself and found none of them to be a correctness defect against
the locked contract, the acceptance matrix, or any real folder name; they are honest, low-stakes
disclosures, not omissions I had to catch.

## Why not Candidate A?

A is not disqualified — it satisfies every one of the plan's 9 locked acceptance cases and all
regression/smoke tests identically to B, and its code is genuinely well-organized (single
compiled pattern, dict-derived vocabulary, thin `has_tmdb_token` wrapper). It loses because its
own explicitly-stated design goal — using two full alternation branches specifically to avoid "the
mismatched-bracket false-positive trap" — is only partially achieved: each branch's id character
class excludes just its own closing bracket, not the other family's bracket characters, so a
string carrying fragments of both families can still make one branch's `[^}]+`/`[^\]]+` swallow
past its own intended closer into the other family's territory. This is a demonstrated false
positive with a corrupted `id`, on a class of input the plan's own text flags by name as the risk
this step exists to close (PLAN.md line 174-175). It happens not to be triggered by anything
currently in the real library, which is why A also legitimately passes the full smoke/regression
suite and the literal acceptance list — but "not currently triggered" is not the same bar as
"structurally impossible," and criterion 2 is explicit that the latter is what this step is for.

## What we keep from the losing candidate

A's vocabulary-generation pattern (`_PROVIDER_TAG_TO_NAME` dict → `_PROVIDER_TAG_ALT` derived
alternation string, `mvcommon.py:693-701` in A's worktree) is a slightly more compact single-file
version of the same "one source of truth, no hand-duplicated regex text" idea B also achieves via
`_PROVIDERS`/`_PROVIDER_BY_TAG`. Both are good; A's is marginally terser. Not worth re-opening the
decision over, but worth noting if a future editor is choosing a vocabulary-table shape for a
similar helper elsewhere.

## Fixable follow-up for the winner (B) — optional, not required

B has no correctness gap that needs fixing before merge. If the user wants to close B's own
self-flagged, non-blocking weaknesses opportunistically at merge time (none are required by the
locked contract or any acceptance case):
- `mvcommon.py:774` — `_tag` is parsed but unused in `find_provider_tokens`; could be dropped from
  `_parse_token_content`'s return tuple, or kept as-is (harmless, self-documenting for a future
  caller). Not a defect — no action needed unless the user wants tidiness.
- None of B's other self-flagged items (permissive id charclass, id whitespace not trimmed, no
  short-circuit in `has_tmdb_token`, `TypeError` on non-str/non-None input, no nested-bracket
  support) are required fixes — all are deliberate, documented, in-scope tradeoffs that match the
  locked contract and existing `main.py` precedent (the old `_PROVIDER_TOKEN_RE` was equally
  permissive on id content). I recommend **merging B as-is**.

## Verification status

Confirmed: Candidate B passes all 9 locked acceptance cases (a)–(i) plus span correctness,
independently re-run by me (not adopted from its CRITIQUE.md). Confirmed: 66/66 regression tests
and 80/80 smoke tests pass in B's worktree, independently re-run. Confirmed: B's diff touches only
`mvcommon.py`, purely additively, with zero hits on any auto-rollback or `ENTRY_TYPE_KEYS` keyword.
Confirmed: B has zero disagreements with A across 290 real folder names and 19 adversarial inputs,
except the two compound cases where B is the correct one per direct code tracing (both families'
bracket characters are excluded from each other's span interior — a property I verified by
construction, not just by test, by reading `mvcommon.py:712-713` in B's worktree).

## Confidence

**High on the correctness/criteria-1-and-2 verdict.** Every load-bearing claim in this document
was independently reproduced against the actual candidate code (not adopted from either
CRITIQUE.md on trust): the acceptance matrix, the compound-case bug and its absence in B, a
broader adversarial sweep, a full sweep of the real library, both pytest claims for both
candidates, the extensibility claim for both candidates, and two specific factual citations (one
of which — A's Python-3.12 claim — I found to be **incorrect** on independent testing and have
flagged rather than silently adopted, per delta 1).

**What I could not verify:** whether the *specific scene-release naming conventions* the user's
future acquisitions will use might ever produce the kind of stray/compound bracket input that
triggers A's bug — I can only report that it does not currently exist anywhere in this library
snapshot (290 folders, exactly one legacy curly token, cleanly formed) and that the risk is
therefore latent, not active. I also did not attempt to install or test against Python 3.11 itself
(this environment runs 3.13.5); the 3.12 duplicate-group-name claim was tested on the newer
version and found false there, which is sufficient to flag the claim as unverifiable/incorrect as
stated, but I note it in case Python 3.11-specific behavior somehow differs (unlikely — this
would be a *removal* of a hypothetical earlier feature, not a plausible reading).

**Model-fallback disclosure:** this judgment used the fable→opus fallback tier per
`.claude/MODEL_WATERFALL.md`. Given the closeness of this decision (100% identical on the literal
acceptance matrix; the deciding factor is a bug that is real but not yet live against real data),
if fable capacity becomes available before the user commits to this pick, a fable re-judge of
specifically the compound-case realism assessment (how likely is B's structural guarantee to
matter in practice vs. A's smaller diff) would be a reasonable, low-cost sanity check — I flag
this per the fallback instructions rather than silently presenting this as fable-tier confidence.
That said, every factual claim in this document was verified by direct code execution against
both candidates' real files and the user's real library data, not by model judgment alone, so the
fallback mainly affects the *weighting/narrative* judgment (how much a currently-dormant
structural gap should matter), not the *facts* on the table.
