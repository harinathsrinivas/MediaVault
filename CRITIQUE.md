# Candidate A Self-Critique

## Approach taken
Built `cmd_prep_push_rep_enrich` as a fully isolated command: it calls the unmodified `cmd_prep_push_rep` for the archive leg, then a new private helper `_enrich_after_archive` duplicates ONLY `cmd_enrich_metadata`'s small per-unit resolve-waterfall + apply loop (preset-id / title-search / EXA fallback -> write tmdb_id/title/year/overview -> confirm-gated stamp -> download images -> optional NFO), scoped to the single unit containing the just-archived id. Two small shared helpers (`_refuse_tvdbid`, `_make_rename_confirm`) were added exactly as the plan specifies. Separately (shared, non-forked work), `_write_nfo` gained the richer optional element set plus a new `_tmdb_company_names` helper, and the one existing `_write_nfo` call site now threads `api_key`. Zero lines of `cmd_enrich_metadata` were touched.

## Strengths
- **Zero-diff autopilots, verified structurally, not just visually.** `git diff main -- main.py` shows exactly 5 hunks: 3 inside `_write_nfo`/its call site (shared NFO work), 1 adding `_tmdb_company_names`, and 1 purely-additive 269-line insertion immediately after `cmd_prep_push_rep_season` ends (`main.py:7524`, hunk header `@@ -7456,6 +7524,269 @@`, confirmed 0 `-` lines inside that hunk via `awk`). `cmd_prep_push_rep` (`main.py:7204`) and `cmd_prep_push_rep_season` (`main.py:7261`) bodies are byte-for-byte untouched.
- **`cmd_enrich_metadata` (main.py:2412) has zero lines changed** — confirmed by `git diff` showing no hunk inside its body; only the one `_write_nfo(...)` call site gained an `api_key=api_key,` line (shared NFO extension, not a candidate-fork change).
- **Signature fidelity.** `cmd_prep_push_rep_enrich`'s signature (`main.py:7727-7735`) matches the plan's binding block character-for-character (param names, order, defaults, `rename_choice="ask"`).
- **`_make_rename_confirm`'s body (`main.py:7582-7598`) reproduces the plan's exact code** — the `will be changed to` phrase and double-quoted full paths are verbatim; the `input()` call is reached ONLY when `choice == "ask"` and `sys.stdin.isatty()`, proven by every test running non-interactively without hanging.
- **Independent guards replicated correctly**: the "no TMDB API key" bail (`main.py:7620-7624`) and the `-tvdbid` refusal (`main.py:7548-7561`, exact wording from the plan) both run BEFORE any TMDB call, proven by dedicated tests.
- **Test coverage exceeds the 4 required scenarios** (`tests/test_prep_push_rep_enrich.py`, 6 tests): added two extra boundary tests — archive-not-completed (enrich never even checks for a TMDB key) and no-TMDB-key (archive still completes, `cmd_set_tmdb` still runs, resolve is never attempted) — both defended with monkeypatches that raise `AssertionError` if a call happens that shouldn't.
- **NFO extension is thorough and defensive**: wrapped in its own `try/except` so `_write_nfo`'s "NEVER raises" contract is preserved even if a helper misbehaves; every added field independently optional; 3 new tests (`tests/test_enrich_metadata.py`) prove both a richer-data case (imdbid/genre/runtime/premiered/studio populate; director/actor gracefully omit since no credits fixture data was supplied) and full graceful omission, plus explicit `<tvdbid>` absence for both movie and show NFOs.
- **Full verification suite green**: `test_enrich_metadata.py` 48/48 (45 original + 3 new, zero existing lines changed), `tests/smoke` 76/76 (matches documented baseline exactly), full suite 709/709 (706 + 3), `test_entry_schema_guard.py` 4/4.

## Weaknesses
- **`_enrich_after_archive` is an extra private helper not explicitly named in the plan's Files bullet** (which lists only `cmd_prep_push_rep_enrich`, `_refuse_tvdbid`, `_make_rename_confirm` as new). I factored the ~90-line resolve+apply sequence out of the top-level function for readability rather than inlining it, since the plan's prose describes behavior, not layout. This is a deviation from the letter of the Files list, though not from the described logic — flagged here rather than left silent, per this tier's assumption-surfacing rule.
- **`<director>` for a SHOW will very often be empty in real usage.** The plan explicitly names `_tmdb_directors_from_crew` (which reads `crew` entries with `job == "Director"`) for both movie and show NFOs, and I followed that literally. In practice TMDB's `/tv/{id}/credits` aggregate-credits crew rarely carries a show-level "Director" (direction is per-episode), so a show's `<director>` will usually be omitted even when data exists — `_tmdb_created_by_names` (already used elsewhere for the web dossier's show "directors") would populate more reliably. I did not substitute it, since (a) the plan named a specific function, (b) NFO is shared/non-forked and explicitly excluded from judging, and (c) unilaterally redesigning it risks disagreeing with Candidate B's identical implementation. Worth a follow-up decision, not silently "fixed" here.
- **The plan's Acceptance (2) text says "60+ tests" for `test_enrich_metadata.py`; the actual count is 45** (before my 3 additions, 48 after). Not something in my control to fix — noting the discrepancy for transparency rather than silently treating my own count as ground truth.
- **A prefix-collision edge case in `_gather_enrich_units(id_or_prefix=real_id)`** (multiple units matching a startswith-prefix) is handled by simply taking `units[0]`, exactly as the plan explicitly scopes out of this task's responsibility. Not fixed, not silently mishandled — just the documented pre-existing limitation.
- **`_write_nfo`'s new detail/credits calls are not deduplicated against calls already made earlier in the same run** (e.g. `_resolve_unit_by_id`'s `/movie/{id}` fetch and `_write_nfo`'s own `/movie/{id}` fetch are the same URL); this is harmless because `_tmdb_get` disk-caches successful responses, but a failed first call (e.g. 404) is NOT cached, so a second identical failing call re-hits the fake/network. Matches the plan's explicit guidance ("prefer the already-cached `_tmdb_get` path over any new, uncached fetch") — I did not invent a new caching layer beyond what already exists.

## Acceptance self-check
- (1) autopilots zero-diff: **PASS** — `git diff main -- main.py` shows the only hunk touching the 7204-7456 region is a purely-additive insertion starting exactly at old-line 7456 (context only, 0 deletions); `cmd_prep_push_rep`/`cmd_prep_push_rep_season` source is unchanged.
- (2) test_enrich_metadata.py unmodified-and-green: **PASS, with a clarification.** No existing test's code/assertions were changed (`git diff main -- tests/test_enrich_metadata.py` shows zero `-` lines besides the diff header). I ADDED 3 new tests (45 -> 48, all green) to satisfy Acceptance item (5)'s explicit requirement to assert `<tvdbid>` never appears — this is a pure addition, not a modification of the 45 existing tests, and I deliberately did NOT touch the shared `FakeTMDB` class to stay maximally conservative about "nothing else in the file" changing.
- (3) self-written scenario tests: **PASS** — `tests/test_prep_push_rep_enrich.py`, all 4 required scenarios plus 2 extra: `test_id_supplied_happy_path_archives_presets_resolves_renames_downloads`, `test_no_rename_leaves_folder_unchanged_but_still_writes_tmdb_id`, `test_tvdbid_refuses_before_anything_touches_disk`, `test_rollback_hard_fail_from_rename_is_caught_prints_warning_returns_true`, `test_archive_not_completed_skips_enrich_entirely_and_returns_false`, `test_no_tmdb_api_key_warns_and_skips_enrich_but_archive_still_completes`. 6/6 pass.
- (4) zero new RollbackJournal/PONR/journal touches: **PASS** — `grep -n "RollbackJournal(\|mark_point_of_no_return\|TXN_JOURNAL_NAME\|raise RollbackHardFail(" main.py` returns 34 lines on this branch, identical to 34 on `main`; `raise RollbackHardFail(` count is still exactly 3 (unchanged). My code only CATCHES `RollbackHardFail`, never raises a new instance.
- (5) NFO: **PASS** — reviewed all 5 existing NFO tests (`test_nfo_movie_written_on_apply_with_flag`, `test_nfo_show_written_on_apply_with_flag`, `test_nfo_not_written_without_flag`, `test_nfo_not_written_in_dry_run`, `test_nfo_is_well_formed_xml`, `test_nfo_write_failure_warns_but_enrich_still_completes`); none pinned the old minimal element set (no assertion enumerates/counts all children), so none needed updating — all pass unmodified. Added `test_nfo_movie_richer_fields_populate_when_available`, `test_nfo_movie_omits_richer_fields_gracefully_without_detail_data`, `test_nfo_show_never_emits_tvdbid`, each asserting `root.find("tvdbid") is None`.

## Tests run
```
$ python -m pytest tests/test_enrich_metadata.py -q
................................................                         [100%]
48 passed in 3.76s

$ python -m pytest tests/test_prep_push_rep_enrich.py -q
......                                                                   [100%]
6 passed in 1.02s

$ python -m pytest tests/smoke -q
........................................................................ [ 94%]
....                                                                     [100%]
76 passed, 1 warning in 9.04s

$ python -m pytest tests -q
........................................................................ [ 10%]
........................................................................ [ 20%]
........................................................................ [ 30%]
........................................................................ [ 40%]
........................................................................ [ 50%]
........................................................................ [ 60%]
........................................................................ [ 71%]
........................................................................ [ 81%]
........................................................................ [ 91%]
.............................................................            [100%]
709 passed, 1 warning in 57.34s

$ git diff main -- main.py --stat
 main.py | 352 +++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++-
 1 file changed, 348 insertions(+), 4 deletions(-)
(4 deletions are all inside _write_nfo's signature/docstring — the shared, non-forked NFO work.)

$ git diff main -- main.py | awk '/^@@ -7456,6/{flag=1} flag{print} /^@@ -8375,6/{exit}' | grep -c "^-"
0

$ grep -n "RollbackJournal(\|mark_point_of_no_return\|TXN_JOURNAL_NAME\|raise RollbackHardFail(" main.py | wc -l
34
$ git show main:main.py | grep -n "RollbackJournal(\|mark_point_of_no_return\|TXN_JOURNAL_NAME\|raise RollbackHardFail(" | wc -l
34

$ python -m pytest tests/test_entry_schema_guard.py -q
....                                                                     [100%]
4 passed in 0.28s
```

## Confidence
**high** — every acceptance item has direct evidence (diff hunks inspected line-by-line, not just line counts), the full suite plus smoke gate are green at the documented baseline, and all 4 mandated scenario tests plus 2 extra pass with assertions that would fail loudly (via `AssertionError`-raising monkeypatches) if the implementation drifted from the intended call sequence. The one place I'd flag for a human/judge decision rather than claim full certainty on is the `<director>` field for shows (see Weaknesses) — it is faithful to the plan's literal wording but may under-populate in real-world TMDB data; since NFO is explicitly excluded from judging and identical in both candidates, this does not affect the A-vs-B comparison.
