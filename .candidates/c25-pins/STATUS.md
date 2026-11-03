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
- [ ] pins.0 overlap check — the 17 probes re-targeted at today's code; each disarmed alone on an isolated `git archive
      HEAD` copy against EVERY existing test that enters the disarmed function (a tripwire map from one full-suite run
      on the copy); every file restored byte for byte. Findings: which of the 17 an existing test already catches
      (skipped, with that test) and which are still unpinned (landed). Design decisions. No code.
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
