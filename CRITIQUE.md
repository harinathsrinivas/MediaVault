# Candidate B Self-Critique

## Approach taken
Added ONE keyword-only parameter `confirm_rename=None` to `cmd_enrich_metadata`'s signature and
replaced its single unconditional `if will_stamp:` stamp block with a confirm-gated version
(`do_stamp = True if confirm_rename is None else confirm_rename(folder, new_folder_full)`) — every
existing caller omits the kwarg, so the gate is `None` for them and the stamp stays unconditional.
`cmd_prep_push_rep_enrich` then composes: refuse `-tvdbid`, run the UNTOUCHED `cmd_prep_push_rep`,
check the entry reached `status == "archived"`, optionally `cmd_set_tmdb(real_id, tmdb_id)`, build a
`gate = _make_rename_confirm(rename_choice)`, and call
`cmd_enrich_metadata(real_id, "--apply", [--nfo], [--no-web], confirm_rename=gate)` inside a
`try/except RollbackHardFail`. Zero resolve/apply logic is duplicated — the entire enrich leg is one
delegated call. I also implemented the shared (non-forked) NFO element-set extension in `_write_nfo`
plus the new `_tmdb_company_names` helper, exactly as specified.

## Strengths
- **True zero duplication**: `cmd_prep_push_rep_enrich`'s enrich leg is 9 lines (`main.py:7639-7650`
  region — preset + flags + one call). The entire resolve waterfall (preset-id vs. search vs. EXA
  fallback), the additive tmdb_id/title/year write loop, the stamp decision, the image download, the
  per-episode overview refinement, and the NFO write are all reused via the single delegated call —
  none of that logic exists twice in the codebase. This directly serves the "no-limits depth /
  whole-task" concern: Step 2 (season variant) and any FUTURE change to `cmd_enrich_metadata`'s
  resolve waterfall (e.g. a new fallback provider) benefits both call sites automatically — Candidate
  A's isolated reimplementation would need that change applied twice and could silently drift.
- **The `cmd_enrich_metadata` edit is minimal and structurally isolated to ONE existing block**: a
  1-word signature addition, one added docstring paragraph, and the `if will_stamp:` body (see "Exact
  diff" below). No other line inside the function changed. The short-circuit is provable by
  construction (`confirm_rename is None` is checked first, `True if ... else ...` — no other branch
  can reach the `do_stamp=True` path differently than before), and I additionally get the "no TMDB
  API key -> print + bail" guard, the preset-id waterfall, and the EXA fallback boundary "for free"
  from delegation — Candidate A's plan explicitly calls out having to replicate the API-key guard
  itself, which is a duplication/drift risk B does not have.
- **Full-suite proof, not just narrative**: `test_enrich_metadata.py`'s existing 45 tests pass with
  ZERO existing assertions modified (I only ADDED 4 new tests + 2 new assertion lines inside 2
  existing tests, per the plan's own narrow, deliberate NFO-content exception). Combined with the 9
  new tests in `tests/test_prep_push_rep_enrich.py`, the full suite is 713/713 (700 baseline + 13
  new), smoke is 76/76 (baseline).
- **`_write_nfo`'s "NEVER raises" contract is defended in depth**: the entire additive fetch block
  (imdb/detail/credits) is wrapped in its own `try/except Exception`, separate from the file-write
  `try/except`, so a future change to any of the `_tmdb_*` helpers that started raising would still
  degrade to a printed warning + a smaller (but still written) NFO — matching the plan's explicit
  "any lookup/IO failure degrades to a printed warning and a still-written (smaller) NFO."

## Weaknesses
- **`_write_nfo`'s additive block issues up to 3 extra network calls per NFO write** (imdb-id lookup,
  detail, credits) even though `_resolve_imdb_id` and my own `detail` fetch can hit the IDENTICAL URL
  for a movie (`/movie/{id}`) — I rely on `_tmdb_get`'s on-disk cache to dedupe this within one run,
  which is correct but means the code issues what LOOKS like 2 logical calls for 1 real HTTP request;
  a tighter version could thread the already-fetched `detail` dict into `_resolve_imdb_id` instead of
  letting it re-fetch. I judged this not worth the added coupling for a rarely-hit, cached, opt-in
  (`--nfo`) path, but it's a real (small) inefficiency.
- **The "not archived yet" and "prep did not create a library entry" print wording is my own choice**,
  not verbatim-specified by the plan (only the `-tvdbid` refusal and `_make_rename_confirm`'s phrases
  are binding-verbatim) — a downstream step's test that greps for a DIFFERENT exact substring here
  would need updating. I kept the plan's REQUIRED substring ("prep did not create a library entry")
  verbatim and left the rest to my judgment per the plan's own "print a warning naming X, Y, Z" (not
  "print exactly this string") phrasing.
- **I did not add a dedicated test for the season-variant `note=` parameter of `_make_rename_confirm`**
  (e.g. asserting the extra context line prints) since Step 1 is movie-only and `note` is unused by
  `cmd_prep_push_rep_enrich` (it passes no `note`, matching the plan's "note=None for the movie case").
  Step 2 will need to test that path when it wires the season variant.
- **My new test file duplicates a ~15-line minimal TMDB HTTP stub** rather than reusing
  `test_enrich_metadata.py`'s `FakeTMDB` (which is file-local, not exported/importable without an
  `__init__.py`-based package or a fragile same-basename import). This mirrors the project's own
  stated interim state ("Step 5.5 will promote a shared `mock_tmdb` conftest fixture") but is
  duplication I chose deliberately rather than fight the import boundary; a future step should
  promote a shared fixture as the docstring anticipates.
- **I did not exercise `--no-web` end-to-end** in the new test file (no EXA-fallback scenario for the
  composed command) — EXA behaviour itself is already covered by `test_enrich_metadata.py`'s D5
  tests, and my flag-composition logic (`no_web and flags.append("--no-web")`) is a one-line, visually
  verifiable pass-through, so I judged a dedicated EXA test for the composed command lower value than
  the scenarios I did cover, given Step 4/5 will build the exhaustive suite.

## Acceptance self-check
- (1) autopilots zero-diff: **PASS**. `git diff main -- main.py` hunks touch only `_write_nfo`,
  `cmd_enrich_metadata` (signature/docstring/the one `if will_stamp:` block/one NFO-call kwarg), the
  new insertion strictly AFTER `cmd_prep_push_rep_season` ends (all `+`-only lines, `@@ -7456,6
  +7535,125 @@ def cmd_prep_push_rep_season(...)`), and the new `_tmdb_company_names` helper. Zero
  lines inside either `cmd_prep_push_rep` or `cmd_prep_push_rep_season`'s bodies changed.
- (2) test_enrich_metadata.py green: **PASS**. 45 pre-existing tests pass with ZERO of their
  assertions removed or changed in VALUE — I only (a) added one new assertion pair (`<tvdbid>`
  absence) to each of the 2 existing NFO tests (the plan's own narrow, deliberate NFO-content
  exception — no OTHER assertion in either test changed), and (b) added 4 brand-new test functions
  (`test_nfo_extended_elements_populated_when_tmdb_detail_available`,
  `test_confirm_rename_omitted_still_stamps_unconditionally`,
  `test_confirm_rename_callback_can_decline_the_stamp`,
  `test_confirm_rename_callback_can_confirm_the_stamp`). Full file: 49/49 passed.
- (3) self-written scenario tests: **PASS**. New file `tests/test_prep_push_rep_enrich.py`, 9/9
  passed: `test_happy_path_archives_presets_confirms_renames_and_downloads_poster`,
  `test_no_rename_leaves_folder_unchanged_but_still_writes_tmdb_id`,
  `test_tvdbid_refuses_before_any_prep_and_leaves_disk_and_library_untouched`,
  `test_tvdbid_and_tmdbid_together_still_refuses`,
  `test_rollback_hard_fail_from_rename_is_caught_warned_and_still_returns_true`,
  `test_archive_not_complete_skips_enrich_and_returns_false`,
  `test_prep_did_not_create_entry_skips_enrich_and_returns_false`,
  `test_write_nfo_flag_is_forwarded_to_the_enrich_leg`,
  `test_write_nfo_default_off_writes_no_nfo`.
- (4) zero new RollbackJournal/PONR/journal touches: **PASS**. `grep -n
  "RollbackJournal(\|mark_point_of_no_return\|TXN_JOURNAL_NAME" main.py` shows only pre-existing call
  sites (all inside `cmd_prep`/`cmd_push`/`cmd_replace`/`cmd_rename_folder`/extras functions, none in
  my diff — confirmed via `git diff main -- main.py | grep` for the same three tokens: empty output).
  `raise RollbackHardFail(` count is still exactly 3 (unchanged).
- (5) NFO: **PASS**. Reviewed every existing NFO assertion in `test_enrich_metadata.py` — none pinned
  a "minimal element set" (all use `root.find("field").text ==` per-field checks, no element-count
  assertion), so no existing assertion needed changing; I only ADDED the `<tvdbid>`-absence check to
  both existing NFO tests plus a full new test proving the extended set populates correctly
  (genre/runtime/premiered/studio/director/actor/imdbid/second uniqueid) when TMDB detail data is
  available, with an explicit `<tvdbid>` absence check there too (both `root.find("tvdbid") is None`
  and a raw-text substring check).
- (6) short-circuit proof: **PASS** —
  `test_confirm_rename_omitted_still_stamps_unconditionally` calls `cmd_enrich_metadata(id, "--apply")`
  with NO `confirm_rename` argument and asserts the folder is stamped unconditionally (byte-identical
  to the pre-D22 behaviour every other test in the file already exercises).

## Exact diff to cmd_enrich_metadata
Signature + docstring addition:
```python
-def cmd_enrich_metadata(arg=None, *flags):
+def cmd_enrich_metadata(arg=None, *flags, confirm_rename=None):
     """Local-first TMDB backfill (SHOW-CENTRIC, IMP-E3/U3/D17 — Phase 5 step 5.4).
     ...
     TMDB-API behaviour. Any other flag is ignored.
+
+    `confirm_rename` (keyword-only, INTERNAL/programmatic use only — NOT a CLI
+    flag) lets a caller like `cmd_prep_push_rep_enrich` (IMP-D22) gate the
+    folder-token rename behind a confirmation callback `confirm(old, new) ->
+    bool`. Every existing caller (the CLI dispatcher, every existing test)
+    omits it, so it defaults to None and the stamp stays unconditional —
+    unchanged from today.
     """
```
The `if will_stamp:` block (the only behavioral change in the function body):
```python
         if will_stamp:
             new_name = f"{base_name} {{tmdb-{tmdb_id}}}"
-            ok = cmd_rename_folder(folder, new_name)
-            if ok:
-                n_stamped += 1
-                # The folder moved; recompute season folders for the image step.
-                new_folder = os.path.join(os.path.dirname(os.path.normpath(folder)), new_name)
-                unit = _retarget_unit_folders(unit, folder, new_folder)
-                folder = new_folder
+            new_folder_full = os.path.join(os.path.dirname(os.path.normpath(folder)), new_name)
+            do_stamp = True if confirm_rename is None else confirm_rename(folder, new_folder_full)
+            if do_stamp:
+                ok = cmd_rename_folder(folder, new_name)
+                if ok:
+                    n_stamped += 1
+                    # The folder moved; recompute season folders for the image step.
+                    new_folder = new_folder_full
+                    unit = _retarget_unit_folders(unit, folder, new_folder)
+                    folder = new_folder
+            else:
+                print("     ⏭️  folder rename declined — run rename_folder later to add the token.")
```
Plus a one-line kwarg addition at the existing `_write_nfo` call site (`api_key=api_key,`) — required
so the (shared, non-forked) NFO element-set extension can make its extra TMDB calls; not part of the
A-vs-B fork.

## Tests run
```
$ python -m pytest tests/test_enrich_metadata.py -q
49 passed in 4.24s

$ python -m pytest tests/test_prep_push_rep_enrich.py -q
9 passed in 1.44s

$ python -m pytest tests/smoke -q
76 passed, 1 warning in 11.09s

$ python -m pytest tests -q
713 passed, 1 warning in 54.92s   (700 baseline + 13 new: 4 in test_enrich_metadata.py, 9 in
                                   test_prep_push_rep_enrich.py)

$ grep -n "RollbackJournal(\|mark_point_of_no_return\|TXN_JOURNAL_NAME" main.py
(only pre-existing call sites — none inside my diff, confirmed separately via
 `git diff main -- main.py | grep` for the same tokens -> empty)

$ grep -c "raise RollbackHardFail(" main.py
3   (unchanged baseline)

$ git diff main -- main.py | grep -n "^@@"
(8 hunks — all inside _write_nfo / cmd_enrich_metadata / the new post-cmd_prep_push_rep_season
 insertion / the new _tmdb_company_names helper; none inside cmd_prep_push_rep or
 cmd_prep_push_rep_season)
```

## Confidence
**high** — the composition is small, delegates entirely to already-tested logic, and every
acceptance/judge criterion is backed by a passing test I can point to. The one thing I'd flag for the
judge/orchestrator explicitly: this candidate necessarily touches `cmd_enrich_metadata` (Candidate A
does not), which is a real, if small and short-circuit-proof, blast-radius tradeoff — Judge criterion
(3) is where that tradeoff should be weighed, and I've tried to make the evidence for "how small" as
concrete as possible above rather than asserting it.
