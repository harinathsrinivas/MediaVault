STATE: IN PROGRESS

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
      code — this commit (its SHA is recorded with pins.a)
- [ ] pins.a the resolve_photos pins -> tests/test_resolve_photos.py (a "14.g" section reusing the `rp` harness)
- [ ] pins.b the fetch-side pins -> tests/test_fetch_manual.py (a "14.g" section reusing `flow` / `_hidden` / `_locator`)
- [ ] pins.c the registry: tests/test_c25_regression_pins.py EXISTING_PINS + its docstring entry
- [ ] pins.d disarm table — each landed pin's behaviour disarmed alone on a fresh `git archive HEAD` copy: its pin
      turns red; every file restored byte for byte (SHA-256 checked)
- [ ] pins.e docs — ARCHITECTURE.md item 7a names IMP-C44 (one short mention); any doc claim a landed pin proved wrong
- [ ] pins.f gates (full suite, then smoke), the secrets scan, the self-review, STATE: COMPLETE, the final commit
      `test(imp-c25): land the Step 14.g guard pins — behaviours no test caught (incl. the port check before a second
      account)`

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
- A2 The candidate-set reduction (D2) is sound for edits inside function bodies; run 2's f09 whole-suite cross-check
  tests it on the probe that matters most.
- A3 The c18 about-page case relies on the stages having shown signed-in Photos on that driver (gprpc checks — and
  marks — the session on every request, thfix B6); the pin passing in the fake world confirms it there.
- A4 Git: the base contract's single-executor mode runs no git, but the dispatch assigns pathspec WIP commits in this
  worktree (the thfix / mpfix / Step 17 pattern) — followed, commit by commit, with the secrets scan first.
