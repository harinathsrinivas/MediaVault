STATE: IN PROGRESS

# IMP-C25 follow-up to Step 13b — auto-confirm checks only objects old enough (acfix) — resumable status

- Finding (user, real run 2026-10-26; DECISIONS.md §1g last bullet, 1f00913): after a `prep_push_rep_enrich` of a
  9-chunk movie the auto-confirm ran `check_photos pending --apply --max-minutes 15`, which looked up all 36 pending
  objects — the 9 chunks pushed minutes earlier included (each through hash, taken_second, exact_name, title_sid,
  legacy -> `not-backed-up-yet`). Cause: `maybe_auto_confirm` gates on the OLDEST pending object's age, then spawns a
  check of EVERY pending object (main.py comment "the check then covers every pending object, young ones included").
- Fix: `check_photos … --min-age-h <hours>` (objects whose capture push time is younger are not looked up) and the
  auto-confirm child passes `--min-age-h AUTO_CONFIRM_MIN_AGE_H`; its lines count only the due objects.
- Worktree `C:\Users\harin\PycharmProjects\MediaVault-acfix` · branch `tmp/c25_acfix` · base 1f00913 (Steps 0–16).
  The main folder is never touched (the user archives from it). No live runs.
- Python: `C:\Users\harin\PycharmProjects\MediaVault\.venv\Scripts\python.exe -m pytest …`, run from the worktree.
  Gates SEQUENTIAL. Baseline (dispatch): full 3278 · smoke 102.

## Resume rule
Read this file, then `git log --oneline -10` (WIP commits `wip(imp-c25): acfix.<x> — …`) and `git status`. An
uncommitted diff belongs to the first unticked sub-task: keep it when coherent and its targeted tests pass, else
`git checkout -- <its files>` (record that here). Commit THIS file (force-added: `.candidates/` is gitignored) with
every WIP commit; tick a sub-task only after its tests pass, with its SHA. Pathspec commits only; never -A, amend,
rebase or stash. Never edit PROGRESS.md / PRIORITY.md (the orchestrator does).

## Sub-tasks (one WIP commit each)
- [x] acfix.0 this status file: the plan + the design decisions below (no code) — 18f3a33
- [x] acfix.a main: `check_photos … --min-age-h <hours>` — parser (positive number; refused with --apply-run / --list),
      CHECK_USAGE, `_cp_resolve(…, min_age_h)` filter (`_cp_young`) + the one skip line, run.json `min_age_h` + the
      resume key (`_cp_unfinished`, `_cp_check_command`), `cmd_check_photos(min_age_h=…)`, the dispatcher comment —
      tests/test_check_photos.py 183 passed (158 + 25: the command line ×2 + bad values ×9 (no value ×2, 0, −3, a word,
      nan, inf, with --apply-run, with --list), the dispatcher's exit 1 + usage line ×4, the core case (30 h + no push
      time looked up and confirmed; 2 h + 23.9 h never looked up — no RPC / page trace, no row, no learned record, no
      failed row, no block, still pending), without the option (all four, run.json unchanged), only young (no child,
      no Chrome, nothing written), the resume key (a plain and a min-age run never continue each other; "continue it
      with" names --min-age-h), the boundary ×5 (24 h looked up, 24.01, 23.99 skipped, no push time looked up, a future
      push skipped), every scope after --account (+ the `:g` note, + unchanged without it)). Neighbours
      (test_auto_confirm, test_c25_regression_pins, test_map_photos, test_resolve_photos) 406 passed; smoke 102
      passed — 805cf2c
- [x] acfix.b main: `auto_confirm_argv()` + `--min-age-h`, maybe_auto_confirm's line (due only, ", N newer
      skipped"), the comments (section comment, AUTO_CONFIRM_MIN_AGE_H — "covers every pending object" is gone —,
      auto_confirm_argv / maybe_auto_confirm docstrings); tests/test_auto_confirm.py 164 passed (162 + 2; the argv pin
      re-pinned with a literal argv and a literal line, `_header(n, newer)`, CHILD_ARGV_TAIL, the "covers every pending
      one" test renamed `…only_the_old_ones_are_counted` (1 counted, 2 newer), the real-archive journal test's line now
      `_header(1, newer=2)` (its new push's 2 chunks — the finding itself), the child's argv parses with min_age_h 24;
      NEW: no younger object -> no "newer" part, and the chain end to end (the auto-confirm's argv through the real
      dispatcher into the real cmd_check_photos: only the 30 h + no-push-time objects are handed to the browser child,
      never the 6 min / 23.9 h ones)); tests/test_c25_regression_pins.py 42 passed (39 + 3: the re-pin recorded in the
      registry docstring, the 3 new pins registered). Neighbours (+ test_check_photos, test_prep_push_rep_enrich_cli,
      test_prep_keeps_split_record, test_cli_parsers, test_fetch_child_stream) 553 passed; smoke 102 passed — (this
      commit; its SHA is recorded by acfix.c)
- [ ] acfix.c docs: README, ARCHITECTURE, OPERATIONS_QA (+ mvconfig.example.json's comment) — the argv, the lines,
      the option; the main.py line references this change shifts
- [ ] acfix.d gates: disarm probes (isolated scratch copy) · full suite · smoke (sequential) · self-review ·
      STATE: COMPLETE · final commit `fix(imp-c25): auto-confirm checks only objects pushed at least
      AUTO_CONFIRM_MIN_AGE_H ago`

## Design decisions (binding — recorded first so a resumed session stays consistent)
1. **The filter.** `_cp_resolve(library, scope, account, now, min_age_h=None)` drops — after the scope and `--account`
   filters — every object whose push time is less than `min_age_h` hours before `now`. The push time is the record's
   `pushed_at` = `_mp_pushed_at(obj)`: the capture's `pushed.at` of the object's OWN bytes, the value Step 9b's
   `_cp_recent` judges recency by. Age = (now − push).total_seconds() / 3600 — `mvcommon.unverified_objects`'
   arithmetic — so exactly H h old is checked, as in maybe_auto_confirm (`age_hours >= AUTO_CONFIRM_MIN_AGE_H`); no
   overflow for a huge H. No push time -> kept (checked: Step 13's verdict rule). A future push time (clock change) ->
   young, as in maybe_auto_confirm. Works for every scope (legacy objects have no push time: unaffected).
2. **Nothing for a skipped object.** It never enters run.json's `objects`: the child looks up only those, verdict rows
   come only from them, learned records and failed rows only from the rows -> no verdict row, no failed row, no learned
   record. One note under the scope lines, only when N > 0: `N object(s) pushed less than H h ago skipped — checked at
   a later push`. All skipped -> `Nothing to check.`, no run directory (nothing written at all).
3. **Part of the resume key.** run.json records `min_age_h` (only when given: a run without it keeps today's run.json)
   and `_cp_unfinished` continues only a run made with the SAME `--min-age-h` (or none). Otherwise the auto-confirm
   (`pending --min-age-h 24`) would continue an unfinished `check_photos pending` run made without it — whose objects
   include young ones — and look them up; and a manual `pending` run would continue an auto-confirm run and leave the
   young ones out. `_cp_check_command` ("continue it with …") adds `--min-age-h H`. `--apply-run` stays age-agnostic
   (an explicit run dir, or `latest` of the scope + account — unchanged).
4. **The parser.** `--min-age-h` is valued like `--max-minutes`: `_cp_number(…, float, 0, strict=True)` (finite, > 0)
   -> bad input is a ValueError -> the dispatcher's existing `❌ check_photos: …` + `   Usage: {CHECK_USAGE}` + exit 1.
   Refused with `--apply-run` (it never searches — named in that message) and with `--list` (offline: only
   `--account`). Without the option nothing changes (opts gains `min_age_h: None`).
5. **The auto-confirm.** `auto_confirm_argv()` = `[sys.executable, <abs main.py>, "check_photos", "pending", "--apply",
   "--min-age-h", str(AUTO_CONFIRM_MIN_AGE_H), "--max-minutes", "15"]` (the argv pin intentionally changed). Its line
   counts only the due objects and adds `, N newer skipped` when N > 0; the parenthetical names what runs (`check_photos
   pending --apply --min-age-h 24`). The reminder and warning lines are unchanged (reminder N = every pending object).
   The gates (switch, web, young, profiles, lock), the hook's success decision and every rollback path: untouched.
6. **Boundary race (accepted).** The parent's `now` precedes the child's by its start-up (~1–2 s) and the child reads
   the push time to the whole second (`_mp_pushed_at`), so an object within ~2 s of the 24 h mark may be counted
   "newer" by the parent yet looked up by the child — never the reverse (the child's age is always ≥ the parent's).

## Assumptions (surfaced — the dispatch does not fix these)
- A1 `--min-age-h` works with every scope (the dispatch writes `check_photos … --min-age-h`), not only `pending`.
- A2 `, N newer skipped` only when N > 0 (no ", 0 newer skipped").
- A3 The header's parenthetical shows `--min-age-h 24` (it names the command that runs, as before).
- A4 The reminder / warning lines keep suggesting `check_photos pending --apply` (a manual run checks everything).
- A5 The skip line is the check's (the child's) scope note; report.txt and the summary line stay rows-only.
- A6 mvconfig.example.json's `_comment_auto_confirm_after_push` quotes the argv, so it is updated with the docs.

## Log
- Baseline on a `git archive 1f00913` scratch copy (acfix_gate.py, run in the background while the code was written):
  full 3265 passed + 13 failed, smoke 101 + 1 failed — all 13 in tests/test_fetch_hygiene.py + the fetch smoke, every
  one an incoming-folder path assertion: the copy's `--basetemp` under the long scratchpad path pushed the title
  folder past INCOMING_PATH_LIMIT (240), so the fetch took its short-root fallback (`mv_short_root\MV_incoming\…`) —
  correct code behaviour, an artefact of the path. The 13 re-run on the same copy with pytest's default basetemp: 13
  passed. Baseline confirmed = full 3278 · smoke 102 (the dispatch's numbers). Rule kept: every gate runs with the
  default basetemp. Per module at 1f00913: test_check_photos 158 · test_auto_confirm 162 · test_c25_regression_pins 39.
- Model / attribution: dispatched to executor-fable (Fable 5.1). After the first tool call the harness switched the
  commit attribution to Claude Opus 5.5 (a usage-limit fallback — no MODEL-FALLBACK banner came with the dispatch), so
  acfix.0 (18f3a33) and acfix.a (805cf2c) carry `Co-Authored-By: Claude Opus 5.5`. The coordinator then resumed the
  work after the limits reset; the attribution flipped to Fable 5.1 and back to Opus 5.5 within minutes. Every commit
  carries the trailer the harness gives at the moment it is made (the model actually running).
