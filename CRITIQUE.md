# Candidate A Self-Critique — Step 2 (route `oth-` in `save_library`)

## Approach taken
Minimal `elif`. In `mvcommon.save_library` I added an `oth_data = {}` bucket, inserted
`elif key.startswith("oth"): oth_data[key] = val` immediately before the existing `else`,
appended `(LIBRARY_OTHERS, oth_data)` to the atomic-write list, and bumped the docstring
from "3 files" to "4 files". The pre-existing `else: mov_data[key] = val` legacy fallback
is kept **verbatim**. Net diff: 4 added lines + 1 docstring word, no lines removed or
re-ordered. `LIBRARY_OTHERS` was already a module-level constant (`mvcommon.py:24`), so no
import change was needed.

## Design decisions and tradeoffs
- **Placement of the `oth` branch — last, right before `else`.** The four prefixes
  (`mov`/`tv`/`ani`/`oth`) are mutually disjoint (none is a prefix of another), so branch
  order does not affect correctness. I appended `oth` last to (a) leave the three existing
  branches untouched for the smallest, most reviewable diff and (b) match the "newest
  category appended at the end" reading order. The only keys whose destination changes are
  those starting with `oth`, which previously fell into the `else → mov_data` trap — exactly
  the bug being fixed.
- **Kept the silent `else → movies` fallback verbatim (the consequential tradeoff).** The
  step explicitly scopes me to *not* change unknown-prefix behavior, and the round-trip /
  schema-guard tests pin legacy no-prefix keys (e.g. `legacy_nopfx`) landing in movies. So an
  unknown/typo'd prefix is still silently absorbed into `library_movies.json` with no warning.
  Candidate B makes this explicit (stderr warn on an unrouted key). I deliberately did not,
  to stay surgical and behavior-preserving — see Weaknesses for the honest cost.
- **Always write all 4 files, including an empty `{}`.** Adding `(LIBRARY_OTHERS, oth_data)`
  unconditionally means a save with no `oth-` entries writes `{}` to `library_others.json`,
  mirroring exactly how movies/series/anime are always written (and matching what Step 1's
  `load_library` already expects to read). No conditional/special-casing — same idiom for all 4.

## Strengths
- **Correctness, proven:** standalone proof shows `oth-football-2026-fifaworldcup-s01e01`
  lands ONLY in the others file and is absent from movies (trap fixed); `mov-x`+`legacy_nopfx`
  → movies, `tv-x` → series, `ani-x` → anime; and `load_library()` round-trips byte-identically
  to the input. The three existing prefixes route exactly as before (`mvcommon.py:576-581`
  unchanged).
- **Surgical / house-style fit:** mirrors the existing `if/elif … startswith(...)` idiom
  one-for-one (`mvcommon.py:582-583`) and the existing `(LIBRARY_*, *_data)` tuple-list idiom
  (`mvcommon.py:588`). Nothing adjacent refactored.
- **No-regression:** `test_entry_schema_guard.py` + full `tests/smoke` → 71 passed.
- **Extensible:** the next category is the same 4-line shape (bucket + `elif` + write-list
  tuple), so the pattern is obvious to the next author.

## Weaknesses
- **Silent unknown-prefix fallback remains** (accepted, per step scope). A misspelled prefix
  (e.g. `oht-…`) is still routed to `library_movies.json` with no signal — the latent
  data-placement footgun is narrowed to `oth` but not eliminated for arbitrary typos. Candidate
  B's explicit-warn fallback is strictly louder here; my approach trades that observability for
  a smaller, behavior-preserving diff.
- **Not table-driven:** a 5th category still means touching three sites (bucket decl, `elif`,
  write list) rather than one map entry. For a single new prefix this is cheap, but it scales
  linearly rather than via a single data structure.
- **Stylistic dash inconsistency (pre-existing, untouched):** branches match bare `"oth"` /
  `"mov"` (no dash), so a hypothetical key like `"others_legacy"` would also match `oth`. This
  mirrors the existing `mov`/`tv`/`ani` behavior exactly; I intentionally did not "fix" it, to
  stay surgical and preserve current semantics.

## Tests run
Standalone routing proof (throwaway script, run from the worktree with all 4 `LIBRARY_*`
patched to temp paths; temp files + script removed afterward):
```
ROUTE PROOF OK
  movies : ['legacy_nopfx', 'mov-x']
  series : ['tv-x']
  anime  : ['ani-x']
  others : ['oth-football-2026-fifaworldcup-s01e01']
  round-trip == original: True
```
No-regression:
```
python -m pytest tests/test_entry_schema_guard.py tests/smoke -q
71 passed, 1 warning in 80.42s
```
(The 1 warning is a pre-existing `StarletteDeprecationWarning` in the smoke suite, unrelated
to this change.)

## Confidence
high

Reasoning for confidence: the change is four mechanical lines mirroring an idiom already
proven by the existing three prefixes; disjoint-prefix routing is verified end-to-end (oth
isolation + trap-not-leaked + full round-trip) and the pinned schema-guard/smoke suites are
green. The one genuine judgment call — keeping the silent `else → movies` fallback — is an
explicit requirement of my assigned approach, not an oversight, and is disclosed above as the
deliberate tradeoff versus Candidate B.
