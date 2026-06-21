# Candidate B Self-Critique

## Approach taken
Implemented the five read-only data-layer functions in `main.py` (inserted right
after `cmd_scan_unprepped`, lines ~2666-3000, NOT inside `__main__`) using the
**library-first** strategy. `collect_reclaimable()` has two passes: PASS 1
iterates the merged library's physical leaves and `os.stat`s each
`folder_path/filename` path **directly** (no walk-then-match) to decide
real/dummy/absent and its badge; PASS 2 does a **single** `os.walk` of the three
category roots whose only job is to emit on-disk videos NOT in `known_paths` as
UNPREPPED. De-dupe is a shared `seen` set keyed by normpath-lower, so a library
leaf and its on-disk file produce exactly one row. The four supporting functions
(`classify_entry_state`, `guess_manual_id`, `suggest_target_folder`,
`suggest_next_command`) are pure and table-driven.

## Design decisions and tradeoffs
- **Status→badge as a module-level dict (`_RECLAIM_STATUS_TO_BADGE`), archived
  deliberately absent.** `classify_entry_state` returns
  `dict.get(status)` for the real-file case, so any status not in
  {local_ready, onboarded, restored_local} (including a real file under an
  `archived` entry) yields `None`. Alternative: an explicit if/elif ladder with
  an `archived`→handle arm. I chose the dict because it makes "the reclaimable
  set" a single literal the test and `collect_reclaimable`'s membership filter
  both reference (`badge in _RECLAIM_STATUS_TO_BADGE.values()`), eliminating
  drift between "what classify returns" and "what collect keeps". The cost is
  that the `archived`+real anomaly is silently `None` rather than logged.
- **`guess_manual_id` stops the title slug at the first of {4-digit year,
  release-noise token, season/episode marker}.** I added an `ep_marker` regex
  (`sNN`, `sNNeMM`, `eNN`, `xNN`) plus an anime-only "bare trailing number"
  rule, because without it the episode tail leaked into the slug
  (`strangerthingss01e03-s01e03`, `deathnotee0707`). Alternative: strip episode
  tokens post-hoc from the assembled slug. Stopping during tokenisation is
  cleaner and keeps the title boundary explicit. Years genuinely absent from a
  filename become `0000`/`????` placeholders (correct per the 4-digit-token
  rule — the id is editable, never auto-prepped).
- **`suggest_target_folder` proposes only the LEAF folder NAME for new items,
  not the full `Movies/<Lang>/<Genre>/...` path.** The provider-tag template
  only defines the leaf name (`<Title> (<Year>) {tmdb-0000000}`), and the
  Language/Genre segments require a TMDB/TVDB lookup this step explicitly does
  NOT do. Fabricating `English/Unknown/...` would be a misleading guess; the
  leaf name with an editable provider placeholder is the honest deliverable.
  For in-library items I return the existing `folder_path` verbatim with
  `applies=False` (existing folders are never renamed).
- **The two `suggest_*` helpers take the partially-built item dict** (carrying
  `id`/`badge`/`path`/`guessed`/optional `folder_path`) so `collect_reclaimable`
  builds each row incrementally. This keeps the contract's exact function
  signatures (`suggest_target_folder(item)` / `suggest_next_command(item)`)
  while letting the row be assembled in one place.

## Strengths
- Crash-class guard (IMP-C12/PR#21) honored in every library iteration:
  `main.py:2926` skips `season_map`/`multi_ep_alias` before touching
  `folder_path`/`filename`; verified against the real 532-entry library with
  zero crash and zero non-physical rows leaking into items.
- `classify_entry_state` and the reclaimable-badge filter share one source of
  truth (`_RECLAIM_STATUS_TO_BADGE`, `main.py:2710`), so "what's reclaimable" is
  defined once.
- Library-first I/O profile delivered as specified: known paths are `os.stat`ed
  directly (`main.py:2940`); the single `os.walk` (`main.py:2962`) only
  discovers unknown files. De-dupe verified (no path appears twice) on live data.
- Walk exclusions / dummy rule / VIDEO_EXTENSIONS reuse the exact
  `cmd_scan_unprepped` + `cmd_check`/`cmd_repair_dummies` definitions, so the
  "what is an on-disk video / what is a dummy" semantics are consistent with the
  rest of the codebase. Missing roots degrade gracefully (warn + continue).
- Strictly read-only: no write/delete/rename/save_library anywhere in the five
  functions; smoke gate (56 tests) green.

## Weaknesses
- `guess_manual_id` slug concatenates ALL pre-episode title tokens
  (`f1themovie`, `strangerthings`), so a noisy filename like
  "F1.The.Movie.2025…" yields `f1themovie` not the canonical `f1`. This is an
  accepted tradeoff (the spec says a wrong guess is fine; the id is editable and
  never auto-prepped), but it is a guess a human will frequently correct.
- The PASS-2 walk inherits `cmd_scan_unprepped`'s exclusion set verbatim, which
  does NOT exclude `Sample/` subfolders — so a stray `Sample.mkv` surfaces as
  UNPREPPED (observed in live data). This is consistent with the canonical scan
  (matching it is the contract), not a new divergence, but it means the
  UNPREPPED list can include scene "sample" files.
- `suggest_target_folder` for a new TV id title-cases only the first letter of
  the concatenated slug (`Strangerthings`, not `Stranger Things`) because word
  boundaries were lost when the slug was concatenated in `guess_manual_id`. The
  folder name is an editable placeholder, so this is acceptable but imperfect.
- `classify_entry_state` treats an `archived` entry whose on-disk file is real
  as `None` (no badge) rather than surfacing it as an anomaly to investigate.
  Defensible (status says it should be a dummy), but a genuinely-reclaimable
  mislabeled entry would be silently skipped.

## Tests run
Acceptance check (importable + callable):
```
$ python -c "import main; assert all(callable(getattr(main,n)) for n in ['collect_reclaimable','classify_entry_state','guess_manual_id','suggest_next_command','suggest_target_folder']); print('importable+callable ok')"
importable+callable ok
```

Deterministic pure-function checks (temp `_b_smoke.py`, since removed):
```
classify_entry_state OK
guess mov: mov-en-2025-f1themovie
guess tv : tv-en-0000-strangerthings-s01e03
guess ani: ani-ja-0000-deathnote07
guess uid: mov-en-2013-coherence
guess_manual_id OK
suggest_next_command OK
target new mov: {'folder': 'F1 (2025) {tmdb-0000000}', 'provider_tag': 'tmdb-0000000', 'editable_provider_field': 'tmdb', 'applies': True}
target new tv : {'folder': 'Strangerthings (2016) {tvdb-000000}', ...}
target new ani: {'folder': 'Deathnote (2006) {tvdb-000000}', ...}  # anime -> tvdb
target existing: {'folder': 'C:\\Media\\Movies\\English\\Racing\\F1', 'provider_tag': None, ..., 'applies': False}
suggest_target_folder OK
ALL PURE-FUNCTION CHECKS PASSED
```

Live read-only run of `collect_reclaimable()` against the real library/disk
(temp `_b_live.py`, since removed):
```
contract dict OK | items= 49 | bytes= 172698683023 | human= 160.84 GB
badge tally: {'LOCAL_NOT_PUSHED': 6, 'RESTORED_REPLACE_AGAIN': 1, 'UNPREPPED': 42}
# verified: all 7 contract keys present per item; no ARCHIVED in items;
# no duplicate normpath-lower paths; guessed==True iff badge==UNPREPPED;
# suggested_folder has exactly {folder, provider_tag, editable_provider_field, applies}.
LIVE-DATA CHECKS PASSED
```

Smoke gate:
```
$ python -m pytest tests/smoke -q
........................................................                 [100%]
56 passed in 16.59s
```
(Temp `_b_smoke.py` / `_b_live.py` were deleted before finishing — only `main.py`
is changed.)

## Confidence
high

Reasoning for confidence: All five functions import, are callable, pass
deterministic assertions, and `collect_reclaimable()` runs read-only against the
real 532-entry library returning the exact contract dict with correct de-dupe,
correct ARCHIVED exclusion, and no crash-class regression; the smoke gate is
green. The one area I'm less certain about is `guess_manual_id`'s slug quality
for noisy real filenames (e.g. `f1themovie` vs canonical `f1`) — but the contract
explicitly frames the guess as an editable placeholder, so quality there is a
nice-to-have, not a correctness requirement. Full deterministic correctness is
owned by the step-2 sandbox unit tests.
