STATE: COMPLETE

# IMP-C25 — land the Step 14.g guard pins: the Step 14 behaviours no test caught (pins) — resumable status

- Source (read only, gitignored, main folder): `C:\Users\harin\PycharmProjects\MediaVault\.candidates\c25-step14g-pins\`
  — README.md (the 17 behaviours), tests/test_resolve_photos_guards.py + tests/test_fetch_manual_guards.py (16 pins
  written 2026-10-20/28 against Step 14's code), probes/ (the sweep: probes_14 / 14b / 14c, results.jsonl — 104 probes,
  86 caught by Step 14's own tests, 18 survivors; c10 was left out on purpose: its sharper twin c10b was caught).
- The 17: c17 open_best_start's item-page check · c18 a signed-out session during `--manual` · d09 / d10 two resume-hint
  rules · d13 the unreadable-sidecar tolerance · f14 the lock heartbeat · f25 the lock-lost stop · f09 the port check
  before a second account · f10 the profile pre-flight · f19 the start-up cleanup · f27 the run journal · f17 `_end_run`
  · f26 a signed-out account not stopping the walk · f15 `--id` exclusion · f16 the CSV account override · f22 the C27
  list excluding confirmed objects · f34 a URL pasted straight at the question.
- Worktree `C:\Users\harin\PycharmProjects\MediaVault-pins` · branch `tmp/c25_pins` · base 64a54f1. The main folder,
  the real library, C:\Media, Chrome, Google and adb are never touched. Tests only — no product code changes.
- Python: `C:\Users\harin\PycharmProjects\MediaVault\.venv\Scripts\python.exe -m pytest …`, run from the worktree.
  Gates SEQUENTIAL (full suite, then smoke), pytest's default basetemp, no xdist (low memory). Baseline (dispatch) at
  64a54f1: full 3443 · smoke 102 (re-collected here: 3443 / 102). Known load flake:
  tests/test_gpweb.py::test_fake_delayed_download_completes_in_the_background (re-run it alone first).
- Scratch work (outside the repo): `C:\Users\harin\AppData\Local\Temp\claude\C--Users-harin-PycharmProjects-MediaVault\
  63b4ae7b-212c-4a85-8e4a-1436806bc271\scratchpad\pins\` — the isolated `git archive HEAD` copies, the tripwire map,
  the disarm script and its results (resumable JSONL).

## Resume rule
Read this file, then `git log --oneline -12` (WIP commits `wip(imp-c25): pins.<x> — …`) and `git status`. An
uncommitted diff belongs to the first unticked sub-task: keep it when coherent and its targeted tests pass, else
`git checkout -- <its files>` (record that here). Commit THIS file (force-added: `.candidates/` is gitignored) with
every WIP commit; tick a sub-task only after its tests pass, with its SHA (a sub-task's own commit says "this commit";
the next commit writes the SHA in). Pathspec commits only; never -A, amend, rebase or stash; `git diff --cached` + the
secrets scan before every commit. Never edit docs/feature-fetch-datetime/PROGRESS.md / PLAN.md / DECISIONS.md or
improvements/PRIORITY.md (the orchestrator's). A pin that, adapted correctly, FAILS on today's code is a real
regression: STOP and report it — never change product code here.

## Sub-tasks (one WIP commit each)
- [x] pins.0 overlap check — the 17 probes re-targeted at today's code; each disarmed alone on an isolated `git archive
      HEAD` copy against EVERY existing test that enters the disarmed function (a tripwire map from one full-suite run
      on the copy); every file restored byte for byte. RESULT: 17/17 SURVIVED in run 1 and again in run 2 (a fresh
      copy, the coordinator's re-run); f09 against the whole suite: 3443 passed — no existing test catches any of the
      17, so all 17 land and none is skipped (section "pins.0" below). Design decisions D1–D9, assumptions A1–A4. No
      code — 4ac105c
- [x] pins.a the resolve_photos pins -> tests/test_resolve_photos.py (a "14.g" section reusing the `rp` harness): 11
      tests (12 items) for 12 behaviours — `_movies_object` (a movies-account failed object: the walk visits movies
      first) · f14 test_the_lock_heartbeat_is_beaten_at_every_question (3 beats: k, a re-asked x, n) · f25
      test_a_lock_taken_over_mid_walk_stops_it_keeps_every_answer_and_applies_nothing (`lock-lost`, the line, a
      verified paste kept, no apply question, library byte-equal) · f09
      test_the_next_accounts_chrome_is_never_launched_while_the_debug_port_is_held (port asked once, before tv; only
      movies launched; `port`; the line) · f10 test_a_missing_chrome_profile_is_skipped_with_its_line_and_never_launched
      (movies' profile missing: skipped with the remediation line, tv walked) · f19 + f27 + f17
      test_the_run_starts_with_the_fetch_cleanup_and_a_journal_and_ends_through_the_shared_end (cleanup, launch movies,
      launch tv, end(False); the journal on disk `running` at every question naming each session, `clean` at the end)
      · f17 test_ctrl_c_ends_the_run_as_interrupted_through_the_shared_end (`_end_run(interrupted=True)` once; journal
      interrupted) · f26 test_a_signed_out_or_throttled_account_does_not_stop_the_walk_of_the_next_one [signed-out |
      throttled] · f15 test_id_leaves_the_other_objects_out · f16 test_a_csv_rows_account_wins_over_the_failed_lists ·
      f22 test_the_c27_list_leaves_out_an_object_that_is_confirmed_by_now (the unconfirmed one stays listed) · f34
      test_a_photo_url_pasted_straight_at_the_question_is_the_paste_answer. + the module docstring's 14.g line. Runs:
      tests/test_resolve_photos.py 86 passed (74 + 12) — 9813e04
- [x] pins.b the fetch-side pins -> tests/test_fetch_manual.py (a "14.g" section reusing `flow` / `_hidden` / `_locator`):
      5 tests (8 items) — c17 test_a_deleted_items_page_is_not_a_best_start_the_next_choice_is (a live item's page
      opens; a deleted one's does not: the day search) · c18
      test_manual_stops_on_a_signed_out_or_throttled_session_instead_of_waiting_for_a_download [signed-out | throttled
      | signed-out-page-after-signed-in-photos] (the session switched as the --manual round starts: the real classifier
      decides; the right line, no 30-minute wait, no "Batch Processing Complete") · d13
      test_an_unreadable_learned_sidecar_never_stops_a_fetch [unreadable OSError | undecodable UnicodeDecodeError] · d09
      test_the_sidecar_identity_never_replaces_the_librarys_own_photo_id · d10
      test_the_librarys_own_ids_are_not_repeated_as_alternates. Synthetic ids `AF1QipFAKEhint…`. + the module
      docstring's 14.g line. Runs: tests/test_fetch_manual.py 39 passed (31 + 8) — b7453d7
- [x] pins.c the registry: tests/test_c25_regression_pins.py EXISTING_PINS + its docstring entry — the 16 tests
      (11 test_resolve_photos.py, 5 test_fetch_manual.py) after the mpfix group, and the docstring entry "the Step 14
      guards no test caught". Runs: tests/test_c25_regression_pins.py 95 passed (79 + 16) — ae03816
- [x] pins.d disarm table — each landed pin's behaviour disarmed alone on a fresh `git archive HEAD` copy: its pin
      turns red; every file restored byte for byte (SHA-256 checked). On `copy_pins` = HEAD ae03816 (368 files
      byte-equal, before and after): control BEFORE — the three pin modules 220 passed; 17 probes (the pins.0 edits)
      + 1 registry probe, each run against its landed pin(s) only, every one RED on an EXPECTED pin (`results_pins.jsonl`,
      `pins_run.log`); control AFTER 220 passed. Every parameter of a parametrized pin is a detector (c18 3/3, d13 2/2,
      f26 2/2). No code changed in this sub-task. The table (behaviour · probe · pin · red?) is below — ceeb4f5
- [x] pins.e docs — ARCHITECTURE.md item 7a names IMP-C44 (one short mention); any doc claim a landed pin proved wrong:
      §17 item 7a "(suggested after IMP-C25; tracked as IMP-C44)" (its three lines re-wrapped; IMP-C44 = tier C's
      "resumable direct transfer", registered at Step 17). Doc claims checked against the pins — none proved wrong, so
      nothing else changed: ARCHITECTURE §8.10 resolve_photos ("a throttle stops that account's objects … and the
      next account goes on") and the §8.8 line table ("the next account goes on (as after a sign-out)"); README
      (resolve_photos / --manual, "When Google Photos throttles its search"); OPERATIONS_QA §6c ("resolve_photos (only
      that account; the file at hand comes back next time)", "A real sign-in page still says … logged out") and the
      "Fetch can't find something" options — dcb102c
- [x] pins.f gates (full suite, then smoke), the secrets scan, the self-review, STATE: COMPLETE, the final commit
      `test(imp-c25): land the Step 14.g guard pins — behaviours no test caught (incl. the port check before a second
      account)`:
      · gates on dcb102c (= the final code: the final commit changes only this file), one after the other, in the
        worktree, pytest's default basetemp, no xdist (`-p no:cacheprovider` only, so no .pytest_cache is left — it is
        not gitignored): full suite 3479 passed, 1 warning (the pre-existing starlette/httpx deprecation), 249 s = the
        baseline 3443 + 36 new items (test_resolve_photos +12, test_fetch_manual +8, test_c25_regression_pins +16);
        the known load flake did not fire. THEN smoke 102 passed, 1 warning, 12.9 s. The tree clean after both.
      · secrets: the staged diff scanned before every commit (scratchpad `thfix/secrets_scan.py`: credential
        patterns + P-15 private data — real photo ids, e-mail addresses, Google URLs with a query — + risky file
        names) and the whole range 64a54f1..dcb102c (5 files, 604 added lines) — 0 hits every time.
      · self-review above · STATE: COMPLETE · the final commit (this commit — its SHA is in the final report and
        `git log -1`)

## Log
- 2026-11-03 dispatched (executor-fable; the environment reports Fable 5.1, no MODEL-FALLBACK banner; the harness's
  commit-attribution notice named Claude Fable 5.1, then Claude Opus 5.5 — every commit carries the trailer the
  harness gives when it is made, as the dispatch asks). Read-in: CLAUDE.md (global + project), the base contract
  (executor-opus.md), the source README / both pin files / the probe harness, tables and results; Step 15 DISARM.md;
  the thfix, mpfix, Step 14 and Step 17 STATUS files; main.py's resolve_photos section (14347–14898) in full;
  mainfetch.py: init_driver, the session classifier (PhotosThrottledError, _page_kind, check_session_alive,
  throttled_head / hint), profile_preflight, FetchJournal, _startup_cleanup, _note_session, _close_session, _end_run,
  FetchContext, _resume_hints / _with_resume_hints / _locate_item, open_best_start, _manual_wait / _manual_round /
  _help_unfetched / _fetch_items, _debug_port_free, cmd_fetch_route, _check_lock, object_locators, use_photos_tab,
  _ui_search, _shows_item; tests/conftest.py in full; tests/test_resolve_photos.py and tests/test_fetch_manual.py in
  full; the helpers they import (test_check_photos `_Beat` / `cw` / `clock`, test_locator's harness and `flow`,
  test_gphotos_primitives' CSV helpers); tests/gphotos_fake.py's session states and item pages;
  tests/test_c25_regression_pins.py in full; the smoke's resolve_photos test.
- 2026-11-03 overlap run 1 (scratchpad `pins/`): the tripwire map (one full-suite run on the copy: 3443 passed with
  the tripwires in; main.py / mainfetch.py restored byte for byte), then the 17 probes — 17/17 SURVIVED, each probe's
  file restored and SHA-256-checked (`results_overlap_run1.jsonl`, `overlap_run1.log`, exit 0). Its closing verify
  printed "2 differ": it compared the copy with the WORKTREE, which by then held this run's uncommitted drafts (the
  copy itself was HEAD byte for byte — re-checked against HEAD's archive on 2026-11-04).
- 2026-11-04, after a usage-limit restart (coordinator: "the overlap run died with the limit; apply the in-step
  checkpoint rule; re-run the overlap check"). Run 1 had in fact finished before the pause (above). The worktree held
  ONE uncommitted diff, written in this same session with its context intact, ahead of pins.0's commit: the drafts of
  pins.a (tests/test_resolve_photos.py +224) and pins.b (tests/test_fetch_manual.py +111). KEPT: coherent, and their
  targeted tests pass on today's code — the 16 pins = 20 items (3 parametrized) 20 passed; test_resolve_photos +
  test_fetch_manual + test_c25_regression_pins 204 passed. pins.c's registry edit (tests/test_c25_regression_pins.py)
  was then drafted too; each file is committed with its own sub-task. The overlap check re-run on a FRESH copy of HEAD
  (run 2, below), as the coordinator asked.
- 2026-11-04, a slip of mine, undone: `check_old_probes.py` (2026-11-03 22:50) imported the source's probe tables
  without `-B`, so Python wrote `probes/__pycache__/` (probes_14 + probes_14b .pyc) into the READ-ONLY source folder.
  Removed exactly that folder; no file of the source is newer than its README (2026-10-28) — the source is as it was.
  Nothing imports from the source any more (disarm.py carries its own re-targeted probe list).
- Provenance: the sweep's files are dated 2026-10-28 (the dispatch says 2026-10-20); the code it probed is b1d31d1
  (its transcript's name and `SRC = MediaVault-s14`), which equals 5b21c3f's code — 5b21c3f changed only Step 14's
  STATUS.md.

## pins.0 — the overlap check: which of the 17 does an existing test already catch?
- By reading (the dispatch's first step): no probe of Step 15's DISARM.md (G1–G15, S1–S7 — 62/62), of thfix's (63)
  or of mpfix's (18) edits any of the 17 code sites. The nearest: G2c / G2d probe `_shows_item` ITSELF (the Shift+D
  guard), not open_best_start's use of it (c17); G15 the start-up cleanup's own rules, not resolve_photos calling it
  (f19); S7 resolve_photos' TTY refusal (the only resolve_photos smoke test — it never enters the walk); thfix B13 /
  C29 / C33 resolve_photos' throttle line and S3u — all single-account, so none can see whether the walk goes on
  (f26). Keyword search was NOT the test: every behaviour was disarmed (below).
- The tripwire map (one full-suite run on the copy, 3443 passed with the tripwires in; `trip_map.json`) — the tests
  that ENTER each edited function: `_rp_walk` 23 · `_rp_object` 22 · `_rp_id_matches` 24 · `_rp_csv_rows` 2 ·
  `_rp_not_uploaded_lines` 22 — all in tests/test_resolve_photos.py (74 tests); `open_best_start` 34 (test_fetch_manual
  + test_resolve_photos: 105); `_manual_round` 12 (test_fetch_manual: 31); `_resume_hints` / `_with_resume_hints` 52
  (test_fetch_manual + test_locator + tests/smoke/test_smoke_all_commands.py: 254). No hit outside a test.
- Each probe alone, against every FILE holding a test that enters its function (the selections above), on an
  isolated copy, the edited file restored and SHA-256-checked after every probe:

  | probe | behaviour disarmed | function | selection | run 1 (11-03) | run 2 (11-04) |
  |---|---|---|---|---|---|
  | c17 | open_best_start counts an item page as opened although it does not show the item | open_best_start | 105 | SURVIVED | SURVIVED |
  | c18 | --manual swallows a signed-out / throttled session while opening the best start | _manual_round | 31 | SURVIVED | SURVIVED |
  | d09 | the sidecar identity overrides the library's own photo_id | _with_resume_hints | 254 | SURVIVED | SURVIVED |
  | d10 | the library's own ids are repeated as alternates | _with_resume_hints | 254 | SURVIVED | SURVIVED |
  | d13 | an unreadable learned sidecar stops the fetch | _resume_hints | 254 | SURVIVED | SURVIVED |
  | f09 | no debug-port check before a second account's Chrome | _rp_walk | 74 | SURVIVED | SURVIVED |
  | f10 | the profile pre-flight skipped (Chrome launched on a missing profile) | _rp_walk | 74 | SURVIVED | SURVIVED |
  | f14 | the fetch-lock heartbeat never beaten while questions are asked | _rp_object | 74 | SURVIVED | SURVIVED |
  | f15 | --id selects every row | _rp_id_matches | 74 | SURVIVED | SURVIVED |
  | f16 | a CSV row's account no longer wins over the failed list's | _rp_csv_rows | 74 | SURVIVED | SURVIVED |
  | f17 | the walk's run never ended (_end_run) | _rp_walk | 74 | SURVIVED | SURVIVED |
  | f19 | the fetch start-up cleanup skipped before the first session | _rp_walk | 74 | SURVIVED | SURVIVED |
  | f22 | the IMP-C27 list includes confirmed objects | _rp_not_uploaded_lines | 74 | SURVIVED | SURVIVED |
  | f25 | a taken-over lock (LockLost) handled as a generic fault | _rp_walk | 74 | SURVIVED | SURVIVED |
  | f26 | a signed-out / throttled account stops the whole walk | _rp_walk | 74 | SURVIVED | SURVIVED |
  | f27 | the walk keeps no fetch journal | _rp_walk | 74 | SURVIVED | SURVIVED |
  | f34 | a photo URL pasted straight at the question no longer accepted | _rp_object | 74 | SURVIVED | SURVIVED |

  Run 1: copy `copy_base` of HEAD eda4a69 (= 64a54f1's code and tests), `results_overlap_run1.jsonl`. Run 2: a FRESH
  copy `copy_overlap2` of the same HEAD, `results_overlap.jsonl` / `overlap_run2.log`, exit 0; after it the copy was
  HEAD's archive byte for byte (368 files, 0 differing, nothing extra). The worktree differed from HEAD only in this
  run's own uncommitted files (STATUS.md, ARCHITECTURE.md, the three test files) — never in product code.
- Cross-check of the reduction (D2) on the probe that matters most: f09 (no port check) against the WHOLE suite on the
  same copy — 3443 passed (263 s, `results_full.jsonl`, `full_f09.log`): SURVIVED; the copy HEAD byte for byte after.
- RESULT: no existing test catches any of the 17 today (Step 15's pins and smoke, thfix's / mpfix's / acfix's tests
  included) — all 17 are still unpinned, so all 17 get a pin (16 tests); none is skipped.

## Self-review (the whole range 64a54f1..HEAD, read in full: 5 files, 604 lines added, 3 removed)
(a) The dispatch, item by item:
- Overlap check — met: Step 15's DISARM.md and the thfix / mpfix tables read (none of their probes edits the 17
  sites); then every behaviour DISARMED on an isolated `git archive HEAD` copy against every existing test that enters
  its function — twice (run 1, and the coordinator's re-run on a fresh copy), plus the port check against the whole
  suite: none caught. Landed 17 (16 tests), skipped 0 — so no "existing test that catches it" to name.
- Land — met: each pin adapted to today's code in the module it belongs to (D1), on the repo's own harnesses (the fake
  Google Photos world, conftest's autouse guards, `rp` / `flow`); hermetic — no Chrome, Google, real library or adb
  (the stand-in drivers, the tmp-path state; a CHROME_PROFILES path under tmp_path for the pre-flight). The signed-out
  pins assert thfix's semantics (D4); the papercut IMP-C43 will change is deliberately not pinned (D5).
- Prove each pin — met: 17/17 + the registry probe RED on an isolated copy, each on an expected pin, every edited file
  restored byte for byte (SHA-256), the copy green before and after (220 / 220) and HEAD byte for byte after.
- Registry — met: EXISTING_PINS + its docstring entry; the renamed-pin probe proves the registration bites.
- Doc — met: ARCHITECTURE 7a names IMP-C44 (one mention); the doc claims about the 17 checked, none proved wrong.
  PROGRESS.md / PLAN.md / DECISIONS.md / PRIORITY.md untouched.
- Protocol — STATUS resumable, committed first and with every sub-task; one WIP commit per sub-task, pathspec only,
  the secrets scan before each (0 hits each time; the whole range: 0); gates sequential (below).
- Regression stop rule — not triggered: every adapted pin passed on today's code at its first run (20/20).
(b) Downstream consumers: tests only, no product behaviour changes, so no command, file format or web consumer is
  affected. Step 18 (the PR) gets 16 more registered pins. IMP-C43 (port / lock-lost exit status) can land without
  re-pinning: the pins assert the walk's outcome and its effects, never the status, and after `lock-lost` the apply
  stays unoffered in C43's own proposal. IMP-C39 (the lock primitive) does not change the walk's beat per question.
  IMP-C42 is the warm-up's pre-flight, not resolve_photos'.
(c) Change-gates: rollback untouched (no product file in the range; no RollbackJournal / recover_journal /
  RollbackHardFail / PONR / txn name in the diff — grep: none); ENTRY_TYPE_KEYS untouched (no entry type or shared
  field — the tests build library entries with the existing helpers' shapes); smoke after the full suite (below).
- My own slip (Log 2026-11-04): a `__pycache__` written into the read-only source folder — found, removed, the source
  verified as it was.

## pins.d — the disarm table: each behaviour, disarmed alone, turns its pin red
| behaviour | probe (the edit) | pin (module :: test [items]) | red? |
|---|---|---|---|
| open_best_start's item-page check | c17 `if _shows_item(…):` → `if True:` | test_fetch_manual :: test_a_deleted_items_page_is_not_a_best_start_the_next_choice_is | RED 1/1 |
| --manual stops on a session stop | c18 the `except SessionExpiredError: raise` arm removed | test_fetch_manual :: test_manual_stops_on_a_signed_out_or_throttled_session_instead_of_waiting_for_a_download [signed-out · throttled · signed-out-page-after-signed-in-photos] | RED 3/3 |
| resume hint: the library's photo_id first | d09 the `not loc.get("photo_id")` condition dropped | test_fetch_manual :: test_the_sidecar_identity_never_replaces_the_librarys_own_photo_id | RED 1/1 |
| resume hint: no repeated library ids | d10 `p not in have` dropped | test_fetch_manual :: test_the_librarys_own_ids_are_not_repeated_as_alternates | RED 1/1 |
| the unreadable-sidecar tolerance | d13 `except (OSError, ValueError)` → `except KeyError` | test_fetch_manual :: test_an_unreadable_learned_sidecar_never_stops_a_fetch [unreadable · undecodable] | RED 2/2 |
| the port check before a second account | f09 `if sessions and not _debug_port_free():` → `if False:` | test_resolve_photos :: test_the_next_accounts_chrome_is_never_launched_while_the_debug_port_is_held | RED 1/1 |
| the profile pre-flight | f10 `problem = profile_preflight(account)` → `None` | test_resolve_photos :: test_a_missing_chrome_profile_is_skipped_with_its_line_and_never_launched | RED 1/1 |
| the lock heartbeat | f14 `state["heartbeat"].beat()` → `pass` | test_resolve_photos :: test_the_lock_heartbeat_is_beaten_at_every_question + test_a_lock_taken_over_mid_walk_… | RED 2/2 |
| --id exclusion | f15 `_rp_id_matches` → `return True` | test_resolve_photos :: test_id_leaves_the_other_objects_out | RED 1/1 |
| the CSV account override | f16 `rec["account"] = row["account"]` → `pass` | test_resolve_photos :: test_a_csv_rows_account_wins_over_the_failed_lists | RED 1/1 |
| `_end_run` | f17 `_end_run(ctx, interrupted)` → `pass` | test_resolve_photos :: test_the_run_starts_with_…_ends_through_the_shared_end + test_ctrl_c_ends_the_run_as_interrupted_through_the_shared_end | RED 2/2 |
| the start-up cleanup | f19 `_startup_cleanup(ctx)` → `pass` | test_resolve_photos :: test_the_run_starts_with_the_fetch_cleanup_and_a_journal_and_ends_through_the_shared_end | RED 1/1 |
| the C27 list excluding confirmed objects | f22 `… and obj["cloud_state"] != "confirmed"` dropped | test_resolve_photos :: test_the_c27_list_leaves_out_an_object_that_is_confirmed_by_now | RED 1/1 |
| the lock-lost stop | f25 `except gp_inventory.LockLost` → `except KeyError` | test_resolve_photos :: test_a_lock_taken_over_mid_walk_stops_it_keeps_every_answer_and_applies_nothing | RED 1/1 |
| a signed-out / throttled account not stopping the walk | f26 `outcome = "quit"` in the SessionExpiredError arm | test_resolve_photos :: test_a_signed_out_or_throttled_account_does_not_stop_the_walk_of_the_next_one [signed-out · throttled] | RED 2/2 |
| the run journal | f27 `ctx.journal = FetchJournal(…)` → `pass` | test_resolve_photos :: test_the_run_starts_with_the_fetch_cleanup_and_a_journal_and_ends_through_the_shared_end | RED 1/1 |
| a URL pasted straight at the question | f34 `or gpweb.photo_id_from_url(answer)` dropped | test_resolve_photos :: test_a_photo_url_pasted_straight_at_the_question_is_the_paste_answer | RED 1/1 |
| (the registry, pins.c) | reg: the port-check pin renamed away | test_c25_regression_pins :: test_the_pin_still_exists [that entry] | RED 1/80 |

## Design decisions (binding — recorded first so a resumed session stays consistent)
- D1 Placement: the pins go into the modules they belong to — tests/test_resolve_photos.py (11 tests for the 12 walk
  guards) and tests/test_fetch_manual.py (5 tests for the 5 fetch guards), each in a "14.g" section with a docstring
  line, reusing that module's own harness (`rp` / `_object`; `flow` / `_hidden` / `_locator`) and autouse fixtures. Not
  the source's two extra files (they import another module's harness and its autouse fixtures), not one combined file
  (both harnesses' autouse fixtures share names: `clock`, `_only_read_only_rpcids_ever`).
- D2 "Already pinned" = disarming the behaviour alone turns an EXISTING test red on an isolated `git archive HEAD` copy.
  The candidate tests = every existing test that enters the edited function — a tripwire map from one full-suite run on
  the copy (a child process inherits PYTEST_CURRENT_TEST, so it counts too); a test that never enters a function cannot
  see an edit inside its body (all 17 edits are in function bodies; the only tests that read product source pin
  trigger_download, the search wait and webui._run_fetch_restore). Each probe runs the whole FILES holding those tests.
  Cross-check: the highest-consequence probe (f09, the port check) also against the WHOLE suite.
- D3 The probes are the 14.g tables' exact edits; 16 still occur exactly once today. f26: thfix turned the arm
  `except mainfetch.SessionExpiredError:` into `except mainfetch.SessionExpiredError as e:` (sign-out vs throttle
  line) — the same edit (`outcome = "quit"` in that arm) is re-targeted at it. c10 stays out (the sweep's own reason:
  its edit never reached the covered scenario; its sharper twin c10b was caught by Step 14's tests).
- D4 Today's semantics for the signed-out pins (thfix D1 / D5 / D6): f26 is parametrized over a sign-in page (signed
  out: "❌ Profile 'movies' is logged out.") and Google's "unusual traffic" page (a throttle: the ⏸️ resolve line, never
  "logged out") — either way only that account's objects stop; the next account's Chrome is launched and its object
  answered. c18 is parametrized over the sign-in page, "unusual traffic", and Google Photos' signed-out page AFTER the
  session showed signed-in Photos (the stages ran on it: a throttle, thfix D5) — --manual stops with the right line,
  never "waiting up to 30 min", never "Batch Processing Complete". The session is switched as the --manual round
  starts, so the REAL classifier decides (the source pin stubbed open_best_start with a bare SessionExpiredError).
- D5 Not asserted as intended: resolve_photos' status `ok` (exit 0) after a port block or a lost lock — IMP-C43
  registers it as a papercut to change. Those pins assert the walk's outcome (`port` / `lock-lost`) and its effects,
  never the status (the source's lock-lost pin asserted `status == "ok"`).
- D6 Claims made real: the lock-lost pin's first answer is a verified paste (a learned record an apply WOULD fold), so
  "applies nothing" is observable — no apply question, the library byte-equal, the answer and its record kept; the
  C27 pin keeps an unconfirmed not-uploaded object as the positive control next to the confirmed one; the heartbeat
  pin re-asks a question (one beat per question, not per object); the port pin records the check is made once —
  before the second account only.
- D7 The run journal is pinned on disk — what a crash leaves the next start-up cleanup: at each question the run's
  journal is `running` and names every session so far; at the end it is `clean`, not interrupted, both sessions with
  their profiles (the source checked only `ctx.journal is not None`). The start-up cleanup and `_end_run` stay spied
  (once, in order: cleanup before the first Chrome; the end once, interrupted on Ctrl+C): their own behaviour is pinned
  by test_fetch_hygiene / test_chrome_robustness (Step 11 / 14.a).
- D8 d13 is parametrized over both exceptions the tolerance catches: OSError (unreadable) and UnicodeDecodeError (a
  ValueError — an undecodable sidecar).
- D9 Tests only: no product code, no fixture or conftest change, no doc claim beyond ARCHITECTURE item 7a's IMP-C44.

## Assumptions (surfaced — the dispatch does not fix these)
- A1 A test that only reads source is no pin of a behaviour (none reads these 9 functions anyway).
- A2 The candidate-set reduction (D2) is sound for edits inside function bodies; the f09 whole-suite cross-check tested
  it on the probe that matters most — the whole suite (3443) passed, as the reduction predicted.
- A3 The c18 about-page case relies on the stages having shown signed-in Photos on that driver (gprpc checks — and
  marks — the session on every request, thfix B6); the pin passing in the fake world confirms it there.
- A4 Git: the base contract's single-executor mode runs no git, but the dispatch assigns pathspec WIP commits in this
  worktree (the thfix / mpfix / Step 17 pattern) — followed, commit by commit, with the secrets scan first.

## pins — [status: done]
- Executor: executor-fable
- Model: fable (the environment reports Fable 5.1; no MODEL-FALLBACK banner; the harness's commit-attribution notice
  alternated between Claude Fable 5.1 and Claude Opus 5.5 — each commit carries the one current when it was made)
- Mode: single-executor in its own worktree (MediaVault-pins, branch tmp/c25_pins, base 64a54f1), pathspec WIP commits
  per sub-task (the dispatch's instruction); one usage-limit restart, resumed by the checkpoint rule (Log).
- Files changed: tests/test_resolve_photos.py, tests/test_fetch_manual.py, tests/test_c25_regression_pins.py,
  ARCHITECTURE.md, this file. No product code, no conftest / fixture.
- Commits: pins.0 eda4a69 + 4ac105c · pins.a 9813e04 · pins.b b7453d7 · pins.c ae03816 · pins.d ceeb4f5 · pins.e dcb102c
  · final (this).
- Outcome: none of the 17 Step 14 behaviours the 14.g sweep found unpinned is caught by any test today — disarmed alone
  on an isolated copy against every existing test entering its function, twice, the port check against the whole suite
  too. All 17 now have a pin (16 tests, 20 items) adapted to today's code — the signed-out pins to thfix's sign-out /
  throttle semantics — in tests/test_resolve_photos.py and tests/test_fetch_manual.py, registered in the C25 registry;
  each behaviour disarmed alone turns its own pin red (17/17, + the registry probe). ARCHITECTURE 17.7a names IMP-C44.
- Key decisions: D1–D9 and A1–A4 above (D4 the signed-out semantics, D5 the exit status left to IMP-C43).
- Verification: full suite 3479 passed (249 s) then smoke 102 passed (12.9 s) on dcb102c, sequential, no xdist;
  overlap 17/17 survived ×2 + f09 whole-suite 3443 passed; pin disarm 18/18 red, restored byte for byte, the copy
  green before and after (220 / 220); secrets 0 hits over 64a54f1..dcb102c.
