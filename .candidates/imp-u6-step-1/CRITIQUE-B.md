# Step 1 — Self-Critique (shared provider-token detection helper)

## Approach taken
`mvcommon.py` gains one new section (125 added lines, no existing line touched) implementing a
**two-stage pipeline**. Stage 1 is two deliberately dumb regexes — `_CURLY_SPAN_RE` and
`_SQUARE_SPAN_RE` — that find bracketed spans with **zero vocabulary knowledge** ("open bracket, some
bracket-free text, the matching close bracket"). Stage 2 is `_parse_token_content(content)`, a
bracket-agnostic, vocabulary-only function that decides whether the inner text is
`<tag><separator><id>` with a recognized tag, returning `(provider, tag, separator, id)` or `None`.
`find_provider_tokens` composes stage 1 → stage 2 and applies the single family-specific rule (`=` is
square-only); `has_tmdb_token` is a one-line derivation from `find_provider_tokens`.

## Design decisions and tradeoffs

1. **The inner character class excludes ALL FOUR bracket characters, not just its own pair.**
   `\{([^{}\[\]]*)\}` rather than `\{([^{}]*)\}`. This is the load-bearing line for acceptance case
   (g). With only its own pair excluded, `{tmdb-123] [tmdbid-456}` — two *mismatched* pairs — would
   have matched as one curly span with content `tmdb-123] [tmdbid-456`, yielding a false-positive tmdb
   token with a garbage id. Excluding the other family's brackets makes a curly scan structurally
   unable to reach across a `]`/`[` to find a closer. The same exclusion also *proves* the two
   families' spans can never overlap (a span's interior contains no bracket char, so neither span's
   delimiters can land inside the other), which is why merging the two result lists by a plain sort is
   sound rather than merely "probably fine". Verified: `{tmdb-123] [tmdbid-456}` → `[]`.

2. **Stage 2 stays bracket-agnostic; the `=`-is-square-only rule lives in the composer.**
   The alternative was to pass the bracket family into `_parse_token_content`. I rejected that because
   the whole value of the decomposition is that the *vocabulary* cannot diverge between the two
   families — if stage 2 could see the family, a future edit could quietly teach `[...]` a tag that
   `{...}` does not know. So stage 2 returns the separator as part of a complete parse and the one
   genuinely family-specific policy is a single, visible three-line guard in `find_provider_tokens`
   (`mvcommon.py:775-778`). Cost: stage 2 returns a 4-tuple whose `tag` element the current caller
   ignores (bound as `_tag`) — a complete parse result rather than a minimal one.

3. **`has_tmdb_token` is DERIVED from the parser, not a second regex.** This is the point of the whole
   task: the drift class (IMP-C22/C23) existed because a predicate and a finder were two hand-written
   regexes. There is now exactly one pattern set and one parse path in the codebase; the predicate is
   `any(t["provider"] == "tmdb" for t in find_provider_tokens(name))`. It is impossible for the
   predicate to accept a format the finder rejects, or vice versa — not "tested for agreement",
   structurally identical. Cost: `has_tmdb_token` builds the full token list instead of
   short-circuiting on a `re.search`. For folder-leaf names (tens of characters, 0-3 bracket spans)
   this is nanoseconds, and the hot-ish caller (the artwork ancestor walk) runs it a handful of times
   per lookup.

4. **The vocabulary is a derived table, not six literals.** `_PROVIDERS = ("tmdb", "tvdb", "imdb")` plus
   `_PROVIDER_BY_TAG = {tag: provider for provider in _PROVIDERS for tag in (provider, provider + "id")}`.
   Adding a 4th provider (`anidb`) is one string, and the bare/`…id` spelling pair can never be added
   for one provider and forgotten for another. I considered a literal 6-entry dict (marginally more
   greppable) and rejected it as the more drift-prone shape for exactly the class of bug this task exists
   to close.

5. **No type annotations, matching the file.** The plan's contract block shows `def has_tmdb_token(name:
   str) -> bool`. `mvcommon.py` is 100% unannotated (`retry`, `episode_num_from_id`, every getter), and
   CLAUDE.md §3 says match existing style. The locked contract — symbol names, values, semantics, dict
   keys — is honoured exactly; annotations are cosmetic and non-enforcing in Python, and adding them
   later is a non-breaking one-liner if a reviewer prefers.

## How future drift between call sites is made structurally impossible
One module, one parser, one vocabulary table, one bracket-pair definition. Step 2 deletes
`_PROVIDER_TOKEN_RE` and makes `main._has_tmdb_token` a one-line forwarder, so after that step **no
regex describing a provider token exists anywhere outside this section**. Emission is bound to the same
section by `CANONICAL_TMDB_TOKEN_FMT` / `CANONICAL_TVDB_TOKEN_FMT`, so what we write and what we
recognize are literally adjacent lines — a future format change that updated one and not the other would
be visible in a three-line diff window.

**Binding hazard (IMP-A1) — for the wiring step:** consumers must call these **module-qualified**
(`mvcommon.has_tmdb_token(...)`, `mvcommon.CANONICAL_TMDB_TOKEN_FMT`), NOT
`from mvcommon import has_tmdb_token`, so a monkeypatch of `mvcommon.*` is honoured. `main.py` already
imports `mvcommon` as a module for exactly this reason (`main.py:36-41`); the new names must NOT be
added to the `from mvcommon import (...)` tuple at `main.py:30`.

**Also useful to the migration step:** "is this token already canonical (including casing)?" is
decidable with no extra API — `tok["match"] == CANONICAL_TMDB_TOKEN_FMT.format(id=tok["id"])`. Verified
True for `[tmdbid-70523]` and False for `{tmdb-70523}` / `[TMDBID-70523]` / `[tmdbid=70523]`.

## Acceptance cases — every one verified
| Case | Input | Result |
|---|---|---|
| (a) | `Dune Part Two (2024) {tmdb-603692}` | PASS — 1 token `('tmdb','603692','curly','{tmdb-603692}')`, `has_tmdb_token` True |
| (b) | `… [tmdb-603692]` | PASS — 1 token, square, `has_tmdb_token` True |
| (c) | `… [tmdbid-603692]` | PASS — 1 token, square, `has_tmdb_token` True |
| (d) | `Run (2002) {TMDB-69590}` | PASS — found; also `[TMDBID-69590]` and `[TmDbId-69590]` |
| (e) | `… [tmdbid=603692]` | PASS — found; `[tmdb=603692]` found; `{tmdb=603692}` correctly NOT found |
| (f) | `Peaky.Blinders…-FLUX[rartv] [tmdbid-60574]` | PASS — exactly **1** token (`[tmdbid-60574]`); `[rartv]` rejected |
| (g) | `{tmdb-123]`, `[tmdb-123}` (+ `{tmdbid-123]`, `[tmdbid-123}`, embedded-in-text variants, and the cross-family `{tmdb-123] [tmdbid-456}`) | PASS — `[]` and `has_tmdb_token` False for all |
| (h) | `Dark (2017) [tvdbid-266189] [tmdbid-70523]` | PASS — **both** returned in left-to-right order, `has_tmdb_token` True |
| (i) | `Dark (2017) [tvdbid-266189]` | PASS — tvdb token returned, `has_tmdb_token` **False** |
| span | 10 names incl. `[[tmdbid-123]]`, `{tmdb-1}{tmdb-2}`, mixed-family, empty | PASS — `name[start:end] == match` for every token; starts sorted; no overlaps |

Beyond the required list: all 6 tags (`tmdb/tmdbid/tvdb/tvdbid/imdb/imdbid`) in **both** families;
`[imdbid-tt0111161]` keeps its non-numeric id; rejection of `[rartv]`, `[FraMeSToR]`, `[a1b2c3]`,
`Movie [a1b2c3].chunk.001.mkv`, `[1080p]`, `[x264-GROUP]`, `[HDR-DV]`, `{}`, `[]`, `[tmdb-]`, `[tmdb]`,
`[-123]`, `[tmdb - 123]`, `[notmdb-123]`, `[tmdbx-123]`, `[tmdbidx-123]`, `[atmdb-123]`;
`None`/`""` → `[]` / False; and a legacy-parity sweep proving the new predicate is a strict **superset**
of the old `re.search(r"\{tmdb-[^}]+\}", …, re.IGNORECASE)` (nothing that matched before stops matching).

## Strengths
- The mismatched-bracket trap is closed by construction, not by a test — `mvcommon.py:712-713`, and the
  reasoning is written down at `mvcommon.py:704-711` so a future editor cannot "simplify" the character
  class without reading why it is that way.
- Each stage is independently trivial to reason about: stage 1 knows nothing about TMDB, stage 2 knows
  nothing about brackets. `_parse_token_content` (`mvcommon.py:728-748`) can be reasoned about, and
  later extended, purely as a string-shape question.
- `span` is exact by construction (`m.span()` of the full bracket match, `match = m.group(0)`), spans
  are non-overlapping and returned left-to-right — the migration command can replace one token by span
  with no coordinate bookkeeping and leave `[tvdbid-…]`/`[rartv]` byte-identical (`mvcommon.py:779-781`).
- Zero new imports; `re` was already imported. `mvcommon.py`'s stdlib-only / never-import-main rule is
  preserved. Purely additive diff — no existing line changed, so no regression surface.

## Weaknesses
- **The id charclass is permissive (`.+`).** `{tmdb-not a number}` is reported as a tmdb token with id
  `"not a number"`. Deliberate — it exactly preserves the legacy `[^}]+` behaviour and errs toward "do
  not stamp a second token" — but it means a *malformed* token is treated as a real one. A stricter
  `[^\s]+` or digits-only rule would reject some real-world sloppiness; I chose behavioural continuity.
- **Ids are not stripped.** `{tmdb-123 }` → id `"123 "`, so a naive span-rewrite would emit
  `[tmdbid-123 ]`. No such folder is known to exist. I kept fidelity (the returned parts round-trip to
  the matched text) rather than silently normalizing inside a *detection* helper; if it ever matters,
  the normalization decision belongs to the dry-run-by-default migration command, not here.
- **`has_tmdb_token` does strictly more work than the old single `re.search`** (builds the token list,
  no short-circuit). Irrelevant at folder-name scale, but it is not free; a whole-library scan of
  hundreds of thousands of names would want a short-circuiting variant.
- **Non-`str`, non-`None` input raises `TypeError`** (e.g. a `pathlib.Path`). The contract says `str`,
  and I did not add speculative isinstance handling (CLAUDE.md §2), but a caller passing a `Path` gets
  an exception rather than `False`.
- **Nested brackets inside a token are unsupported** (`[tmdb-[x]]`). By design — no convention nests —
  but it is a stated limit of the bracket-free-interior rule.
- Stage 2 returns a `tag` element no current caller uses (bound `_tag` at `mvcommon.py:774`).

## Tests run
Acceptance harness (kept OUTSIDE the repo — `tests/test_provider_tokens.py` belongs to a later step):
`python <scratchpad>/verify_step1_b.py` → **120 assertions, 120 PASS, 0 FAIL**

```
== (f) [rartv] must not match; exactly ONE token ==
  PASS f.count  got=1 want=1
  PASS f.tokens  got=[('tmdb', '60574', 'square', '[tmdbid-60574]')] want=[('tmdb', '60574', 'square', '[tmdbid-60574]')]
== (g) mismatched brackets must NOT match ==
  PASS g.tokens {tmdb-123]  got=[] want=[]
  PASS g.tokens [tmdb-123}  got=[] want=[]
  PASS g.cross-family  got=[] want=[]
== (h) both [tvdbid-...] and [tmdbid-...] ==
  PASS h.tokens  got=[('tvdb', '266189', 'square', '[tvdbid-266189]'), ('tmdb', '70523', 'square', '[tmdbid-70523]')] ...
== (i) tvdb-only -> has_tmdb_token is False ==
  PASS i.has  got=False want=False

ALL ACCEPTANCE CHECKS PASSED
```

Regression (required by the step):
```
$ python -m pytest tests/test_rename_folder.py tests/test_enrich_metadata.py -q
..................................................................       [100%]
66 passed in 42.24s
```

Smoke gate (mandatory — `mvcommon.py` was touched):
```
$ python -m pytest tests/smoke -q
........................................................................ [ 90%]
........                                                                 [100%]
80 passed, 1 warning in 117.18s (0:01:57)
```

Extra safety net (existing `mvcommon`, brace-escape and artwork-inheritance suites):
```
$ python -m pytest tests/test_mvcommon.py tests/test_split_brace_escape.py tests/test_web_media_image.py -q
............................................                             [100%]
44 passed, 1 warning in 5.79s
```

`python -m compileall -q mvcommon.py` → COMPILE OK. `git status --short` → `M mvcommon.py` only.

## Assumptions
1. The plan's `_parse_token_content(content) -> (provider, tag, id) | None` was an illustrative shape
   ("e.g."), not a locked signature; I return `(provider, tag, separator, id)` because the composer
   needs the separator to apply the square-only `=` rule while stage 2 stays bracket-agnostic. It is a
   private helper (`_`-prefixed) with one call site, so no downstream step is written against it.
2. "`=` tolerated on the square-bracket family only" means curly `{tmdb=123}` is **not** a token at all
   (not merely not-emitted). Implemented as a hard reject.
3. `id` is the verbatim substring, not a normalized/validated value — imdb ids are `tt0111161`, so no
   digits-only rule is possible anyway.
4. Detection is a superset of the old behaviour: anything the legacy brace regex matched must still
   match (asserted in the harness).
5. Folder names are `str`; `None` is tolerated (the legacy call site did `name or ""`) but other types
   are out of contract.
6. Left-to-right ordering of the returned list is not in the written contract but is the only sane
   contract for a span-based rewrite, so it is guaranteed and documented.
7. Nothing here goes near the auto-rollback contract or `ENTRY_TYPE_KEYS` — no journal, no PONR, no
   `cmd_*`, no library entry type or shared field. Confirmed by reading the full diff.

## Confidence
high

Reasoning: every acceptance case is verified by executed output rather than by reading (120 assertions,
0 failures), including the two that a plausible implementation gets wrong — the `[rartv]` false positive
and the mismatched-bracket pair, plus the harder cross-family `{tmdb-123] [tmdbid-456}` variant that the
naive "own pair only" character class would have matched. The diff is purely additive to one stdlib-only
module with no existing line touched, and the smoke gate plus the rename/enrich/artwork suites are green,
so the regression surface is essentially nil. The residual uncertainties are judgement calls I made
consciously and listed above (permissive id charclass, ids not stripped, no type annotations) rather than
things I did not get to; nothing was skipped to make the core path work.
