# Step 1 Self-Critique — shared provider-token detection helper (`mvcommon.py`)

## Approach taken
Added `CANONICAL_TMDB_TOKEN_FMT`, `CANONICAL_TVDB_TOKEN_FMT`, `has_tmdb_token`, and
`find_provider_tokens` to `mvcommon.py` as a pure append after `episode_num_from_id` (no existing
line touched, no other file touched). The core is **one compiled regex**
(`_PROVIDER_TOKEN_PATTERN`) built from two full alternation branches — a curly-brace branch and a
square-bracket branch — each with its own named groups (`c_tag`/`c_id` vs `s_tag`/`s_id`, since
Python's stdlib `re` on the project's declared minimum (3.11, per `.github/workflows/ci.yml`)
does not support duplicate group names across alternation branches — that landed in 3.12).
`find_provider_tokens` runs `finditer` once and dispatches on which pair of groups is non-`None`;
`has_tmdb_token` is implemented **in terms of** `find_provider_tokens` (`any(tok["provider"] ==
"tmdb" for tok in find_provider_tokens(name))`), not as a second regex.

## Design decisions and tradeoffs

**1. Two full alternatives, not a shared bracket character class — this is the mismatched-bracket
fix.** The regex is:
```
\{(?P<c_tag>ALT)-(?P<c_id>[^}]+)\}
|
\[(?P<s_tag>ALT)[-=](?P<s_id>[^\]]+)\]
```
Each branch's id-class excludes *only its own* closing bracket (`[^}]+` for curly, `[^\]]+` for
square), and each branch requires *its own* closing bracket literally. A naive single pattern like
`[{\[](?:tag-id)[}\]]` (a shared open/close character class) would let a curly-open pair with a
square-close, or vice versa, both match — exactly the false positive the task calls out. With two
disjoint branches, `{tmdb-123]` fails the curly branch (no `}` anywhere in the string, since the
`[^}]+` is greedy and there's nothing to stop it before running off the string with no `}` to
close on) *and* fails the square branch (no `[` character exists in the string at all, so the
branch never even opens) — verified directly, not just asserted (see Tests run below).

**2. Separator scope is branch-local, not global.** `[-=]` (tolerating Emby's `=`) is written
*inside* the square branch only; the curly branch hard-codes literal `-`. This makes "curly is
`-`-only" structurally true rather than something a caller has to remember to enforce.

**3. Vocabulary lives in one dict, the alternation string is derived from it.** `_PROVIDER_TAG_TO_NAME`
maps all 6 recognized spellings (`tmdb`/`tmdbid`/`tvdb`/`tvdbid`/`imdb`/`imdbid`) to their 3
canonical provider names; `_PROVIDER_TAG_ALT` is `"|".join(sorted(_PROVIDER_TAG_TO_NAME, key=len,
reverse=True))`, so the regex alternation is *generated* from the dict rather than hand-duplicated
in the pattern text. Adding a 4th provider (e.g. `anidb`/`anidbid`) is a two-line dict addition;
the regex, `find_provider_tokens`, and `has_tmdb_token` need no changes at all. This is the direct
answer to "how does this stay extensible."

**4. `has_tmdb_token` is a thin wrapper over `find_provider_tokens`, not a parallel regex.** This
is what makes the historical drift class (three prior incidents across separate copies in
`main.py`) structurally impossible going forward *inside this module*: there is exactly one
compiled pattern and one vocabulary dict in the file; every other symbol reads through them. The
external wiring risk (a caller doing `from mvcommon import has_tmdb_token` instead of calling
`mvcommon.has_tmdb_token(...)`, defeating monkeypatching per the project's binding-hazard rule) is
outside this file's control — flagging it here for whoever wires `main.py`'s call sites next: call
these two functions **module-qualified** (`mvcommon.has_tmdb_token(...)`,
`mvcommon.find_provider_tokens(...)`), never via a `from mvcommon import ...` alias.

**5. `find_provider_tokens` returns exactly the contract's 5 keys** (`provider`, `id`, `bracket`,
`match`, `span`) — no extra `tag` field, even though the raw matched tag text (`tmdb` vs `tmdbid`,
etc.) is available mid-parse. A caller that needs to distinguish "old-style `tmdb`/no-id tag" from
"canonical `tmdbid`" (relevant to a future migration command) can do so from `match` (e.g.
`"tmdbid" in tok["match"].lower()`) or by comparing `match` against
`CANONICAL_TMDB_TOKEN_FMT.format(id=tok["id"])`. I considered adding a `tag` key for that
convenience but declined — it's not in the locked contract, and speculative fields not requested by
the contract are exactly what the project's "no features beyond what was asked" rule warns against.
Noting the tradeoff here in case a later step finds the derivation from `match` awkward.

## Strengths
- `mvcommon.py:703-717` — the mismatched-bracket trap is closed by construction (two disjoint
  alternatives with branch-local closing-bracket exclusion), not by an extra guard condition bolted
  onto a shared pattern.
- `mvcommon.py:712-729` — `has_tmdb_token` (720-729) cannot see a different vocabulary than
  `find_provider_tokens` (732-762) because it calls it; there is only one compiled regex
  (`_PROVIDER_TOKEN_PATTERN`, 712-717) and one tag dictionary (`_PROVIDER_TAG_TO_NAME`, 693-697) in
  the whole module.
- `mvcommon.py:693-701` — extending to a 4th provider is a 2-line dict edit
  (`_PROVIDER_TAG_TO_NAME`); `_PROVIDER_TAG_ALT` regenerates from it automatically, so the regex
  source itself never needs to be touched or re-derived by hand.
- Zero incidental changes: `git diff` shows a pure append after `episode_num_from_id`, no existing
  line in `mvcommon.py` modified, no other file touched.

## Weaknesses
- Using distinct group names per branch (`c_tag`/`s_tag`, `c_id`/`s_id`) instead of one shared pair
  is slightly more verbose than the newer (3.12+) duplicate-group-name alternation feature would
  allow — a deliberate compatibility tradeoff against the project's CI-declared Python 3.11 floor,
  not an oversight, but worth flagging since it does mean the `finditer` loop body has a small
  if/else dispatch instead of reading a single pair of group names unconditionally.
- `find_provider_tokens`'s `id` class (`[^}]+` / `[^\]]+`) is permissive of any character except its
  own closing bracket — e.g. it would happily capture whitespace or punctuation inside an id. This
  matches the precedent already in `main.py`'s original (now-superseded) `_PROVIDER_TOKEN_RE =
  re.compile(r"\{tmdb-[^}]+\}", ...)`, so it is not a new risk, but it is not a stricter
  digits/alnum-only id validator either. No acceptance case calls for tighter validation, so I did
  not add one (would be scope beyond the locked contract).
- I did not add `re.escape()` around the tag literals when building `_PROVIDER_TAG_ALT` — unneeded
  today since every current tag is plain lowercase ASCII, but a future tag containing a regex
  metacharacter would silently break without it. Left out deliberately (nothing in scope today
  needs it, and adding unrequested defensive code is discouraged by the project's simplicity rule),
  flagging so a future editor of `_PROVIDER_TAG_TO_NAME` is aware.
- I did not write `tests/test_provider_tokens.py` (explicitly out of scope for this step — it
  collides with a later step's file) — verification below is an inline script only, run from
  outside the tracked test tree, per this step's verification-duties instruction.

## Tests run
Inline verification script (`python -c "..."`, not committed — no new test file was created, per
this step's guardrail that `tests/test_provider_tokens.py` belongs to a later step) covering all 9
locked acceptance cases (a)–(i), span correctness for every token found across every case, plus the
extra vocabulary-reject cases from the plan (`[rartv]`, `[FraMeSToR]`, `[a1b2c3]`) and an `imdb`
sanity check:

```
PASS: (a) tmdb curly found
PASS: (a) has_tmdb_token True
PASS: (b) tmdb square found
PASS: (b) has_tmdb_token True
PASS: (c) tmdbid square found
PASS: (d) uppercase TMDB found
PASS: (d) has_tmdb_token True
PASS: (e) emby = variant found
PASS: (f) exactly one token found
PASS: (f) it is the tmdbid one
PASS: (f) rartv excluded
PASS: (g1) mismatched brackets 1 not found
PASS: (g2) mismatched brackets 2 not found
PASS: (g1) has_tmdb_token False
PASS: (g2) has_tmdb_token False
PASS: (h) both tokens found
PASS: (h) providers are tmdb+tvdb
PASS: (h) has_tmdb_token True
PASS: (i) only tvdb found
PASS: (i) has_tmdb_token False
PASS: span correct for '{tmdb-603692}' in '{tmdb-603692}'
PASS: span correct for '[tmdb-603692]' in '[tmdb-603692]'
PASS: span correct for '[tmdbid-603692]' in '[tmdbid-603692]'
PASS: span correct for '{TMDB-69590}' in '{TMDB-69590}'
PASS: span correct for '[tmdbid=603692]' in '[tmdbid=603692]'
PASS: span correct for '[tmdbid-60574]' in 'Peaky.Blinders.S06.2022.2160p.iP.WEB-DL.x265.10bit.HDR.HLG.DDP5.1-FLUX[rartv] [tmdbid-60574]'
PASS: span correct for '[tvdbid-266189]' in 'Show [tvdbid-266189] [tmdbid-70523]'
PASS: span correct for '[tmdbid-70523]' in 'Show [tvdbid-266189] [tmdbid-70523]'
PASS: span correct for '[tvdbid-266189]' in 'Show [tvdbid-266189]'
PASS: [rartv] rejected
PASS: [FraMeSToR] rejected
PASS: [a1b2c3] rejected
PASS: CANONICAL_TMDB_TOKEN_FMT
PASS: CANONICAL_TVDB_TOKEN_FMT
PASS: imdb tag recognized
ALL CHECKS PASSED
```

Regression check (adjacent code paths, per this step's verification duties):
```
python -m pytest tests/test_rename_folder.py tests/test_enrich_metadata.py -q
..................................................................       [100%]
66 passed in 87.51s (0:01:27)
```

Smoke gate:
```
pytest tests/smoke -q
........................................................................ [ 90%]
........                                                                 [100%]
80 passed, 1 warning in 123.66s (0:02:03)
```
(The one warning is a pre-existing `StarletteDeprecationWarning` about `httpx`/`starlette`
unrelated to this change — present before this diff, not introduced by it. The 123s wall time is
this environment's characteristic, not a function of this diff, since this step added zero new
tests and zero calls into the smoke suite's own timed paths; it is reported here for completeness
against the verification-duties instruction to paste actual output, not because it reflects a
regression caused by this step.)

## Confidence
high

Reasoning for confidence: every locked acceptance case (a)–(i) is independently verified with an
explicit assertion, not just "the function returned something truthy" — including the two
mismatched-bracket negative cases (g), which is the specific failure mode this step exists to
close and which I traced through by hand (documented above) in addition to running it. The two
full-suite regression commands specified in the verification duties both pass with zero deltas.
The one area I'm not fully certain about is unrequested-but-plausible future needs (a `tag` field,
digit-only id validation, `re.escape` on tag literals) — I deliberately did not add any of these
since none is in the locked contract, but a downstream step could conceivably want one; I've called
each out explicitly above rather than silently deciding for them.
