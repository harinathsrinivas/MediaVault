STATE: COMPLETE

# IMP-C25 Step 13b — auto-confirm unverified objects at the end of the next push (OD-6 ruling) — resumable status

- Plan: docs/feature-fetch-datetime/PLAN.md Step 13b (+ P-11, P-19, P-18; OD-6) · Step 13's contract (a62e7d4,
  .candidates/c25-step13/STATUS.md "Contracts for later steps — 13b").
- Worktree `C:\Users\harin\PycharmProjects\MediaVault-s13b` · branch `tmp/c25_step13b` · base cefd777 (Steps 0–13 done).
  The main folder is never touched (the user archives from it).
- Python: `C:\Users\harin\PycharmProjects\MediaVault\.venv\Scripts\python.exe -m pytest …`, run from the worktree.
  Gates run SEQUENTIALLY. Baseline: full suite 2738 · smoke 82 (Step 13, orchestrator re-run).

## Resume rule
Read this file, then `git log --oneline -15` (WIP commits: `wip(imp-c25): step 13b.<x> — …`) and `git status`. An
uncommitted diff belongs to the first unticked sub-task: keep it when coherent and its targeted tests pass, else
`git checkout -- <its files>` (record that here). Continue at the first unticked sub-task. Commit THIS file
(force-added: `.candidates/` is gitignored) with every WIP commit; tick a sub-task only after its tests pass, with its
SHA. Pathspec commits only; never -A, amend, rebase or stash. Never edit PROGRESS.md (the orchestrator does).

## Sub-tasks (one WIP commit each)
- [x] 13b.0 this status file: the sub-task plan + the design decisions below (no code) — fa30871
- [x] 13b.a main: AUTO_CONFIRM_MIN_AGE_H (measured choice) + the config switch (mvconfig `auto_confirm_after_push`,
      mvconfig.example.json documents it) + `maybe_auto_confirm(library=None)`: disabled → silent · nothing pending →
      silent · web job / only young / no profile for any due account / fetch lock held → the ONE reminder line ·
      else one line + `check_photos pending --apply --max-minutes 15` through `_cp_spawn` (the check's streaming
      idiom) · non-zero / interrupted / any error → ONE warning line · never raises — tests/test_auto_confirm.py 47
      passed (exact argv + header + summary last, lock free at spawn and none left, the caller's library, young →
      reminder, the 24 h boundary ×3, unknown push time = old, one old is enough, nothing pending ×2, off in mvconfig
      reads nothing, on ×4, non-boolean → off + one stderr warning ×7, web marker ×3 / not set ×3 / web + nothing
      pending, lock held (left alone) / stale (reclaimed), profiles: none / one of two / only the old objects' / the
      routing override, child exit 1 / interrupted ×3 / partial (exit 0) / not startable, any error ×3 never raises, a
      corrupt library (SystemExit contained), the auto-confirm itself writes nothing); tests/conftest.py gained the
      autouse `_hermetic_auto_confirm` (maybe_auto_confirm is an inert recorder in every test) + the opt-in
      `real_auto_confirm` (the real one, its fetch lock under tmp_path, hard-guarded). Neighbours (test_check_photos,
      test_prep_push_rep_enrich_cli, test_prep_keeps_split_record, test_cli_parsers, test_fetch_child_stream) 322
      passed; smoke 82 passed — ea3e4bd
- [x] 13b.b main: `after_push_command(command, result, target, episode_range)` — the success decision (False → nothing;
      None → the run's end state for the three None-returning commands) then maybe_auto_confirm; the post-condition
      helpers (`_push_command_completed`, `_push_group_run_target_ids` — cmd_push_group's own target derivation;
      prep_push_rep_season reuses D22's `_season_run_target_ids`); tests/conftest.py: the guard now also makes
      main.after_push_command an inert recorder, `real_auto_confirm` restores both — tests/test_auto_confirm.py 84
      passed (+37: False reads nothing, truthy ×3, off reads nothing ×3, an invalid switch warns once, prep_push_rep
      ×4 states + an alias id, prep_push_rep_season ×7 (states, ranges, invalid / empty range) + no season + an alias
      child, push_group on a season ×4 + by id prefix ×3 + nothing matched, a None from the other commands ×4, an
      unreadable library / Ctrl+C in the decision → one warning, the guard in every other test). Neighbours (+
      test_prep_push_rep_enrich, test_prep_push_rep_season_enrich) 390 passed; smoke 82 passed — c5c06b9
- [x] 13b.c main: the CLI dispatcher wiring after the six push-family branches (push incl. its extras-only path,
      push_group, prep_push_rep, prep_push_rep_season, prep_push_rep_enrich, prep_push_rep_season_enrich) — tests:
      call-site pins per verb, success + old → exact argv, every failure / exception → nothing, the child failing
      never changes the output contract or the exit code, the six return contracts documented in the test file —
      each branch now keeps its command's return value (`_ac_result = …`, arguments unchanged) and then calls
      `after_push_command(<verb>, _ac_result, <id>[, ep_range])`; no cmd_* body touched. tests/test_auto_confirm.py
      158 passed (+74: wiring ×8 (order cmd → hook, the hook's arguments) + the extras-only path, no other command
      ×8, the auto-confirm's own child line parses as `check_photos pending --apply --max-minutes 15` and never
      reaches the hook, an exception ×24 (6 branches × RuntimeError / RollbackHardFail / SystemExit /
      KeyboardInterrupt) never reaches the REAL hook, usage errors ×9, success → the exact argv ×8 (incl. the
      extras-only path and a push_group range), the REAL commands' documented failures ×7 → output identical to a run
      without the hook + their return values pinned, the plan's no-Chrome cases at the CLI ×4, the child failing ×3
      and a library broken after the push → the dispatcher still returns normally). The module docstring documents
      the six return contracts. Neighbours (enrich_cli, keeps_split_record, cli_parsers, both enrich autopilots,
      cmd_push_partial, cmd_push_mock_device, extras) 303 passed; smoke 82 passed — 8fb4d6a
- [x] 13b.d rollback + web pins: journal-op instrumentation (identical rollback records with and without the hook,
      the hook after the last journal op, no journal file on disk at spawn time) on a real prep_push_rep with a PONR;
      the web UI's push / prep_push_rep actions run in-process and never reach the hook (no webui change: it spawns
      no push-family command) — tests/test_auto_confirm.py 162 passed (+4: a REAL `prep_push_rep <id> <master> COUNT
      2` through the dispatcher run twice in the same folder — every RollbackJournal op (open / each record / PONR /
      commit / rollback, instrumented with the originals still running) identical without and with the hook, the
      hook's check started after the last op, no `.mediavault_txn*` file on disk at that moment, the archived entry
      and the archive's own output unchanged; a push rolled back before any upload journals the same and never reaches
      the check; the web console's push / prep_push_rep actions call main.cmd_* in-process — the hook is never reached;
      webui/server.py starts no Python child (no subprocess / multiprocessing / Popen / os.system / spawn / exec /
      sys.executable — its open-folder os.startfile opens Explorer, allowed)). Rollback / web neighbours (test_rollback,
      test_baseline_happy_path, test_web_endpoints, test_prep_keeps_split_record, test_cmd_replace) 62 passed; smoke 82
      passed — d016010
- [x] 13b.e gates: disarm probes (isolated scratch copy) · full suite · smoke · self-review · STATE: COMPLETE · final
      feat commit — disarm 24/24 caught on a `git archive` copy of d016010 (the copy green before and after: 162
      passed; the worktree never modified; probes: P1 the hook before the success check in after_push_command · P2 the
      push branch calling the hook BEFORE its command — the PLAN's two named probes — · P3 None as success (the literal
      rule) · P4 a branch without its hook · P5/P6 the switch ignored (confirm / hook) · P7/P8 the web marker (absent /
      strict) · P9/P10/P11 the minimum age (none / 12 h / unknown time = young) · P12/P13 profiles (no check / all→any)
      · P14/P15 the lock (no probe / existence only) · P16 a failing child unreported · P17/P18 exceptions escaping
      (confirm / hook) · P19 the argv · P20 the alias end state · P21 push_group's range · P22 the hook INSIDE cmd_push
      before its journal commit (caught by the journal-op instrumentation test) · P23 the reminder text · P24 the
      conftest guard removed); full suite 2900 passed (= 2738 + 162), 1 warning (the pre-existing httpx deprecation) —
      on d016010 (471 s) and again on the final code (= d016010 + two docstring fixes from the self-review, 679 s);
      smoke 82 passed (20 s on d016010, 13 s on the final code), run one after the other; `--collect-only` 2900;
      self-review below. The final commit is the feat commit that sets STATE: COMPLETE

## Design decisions (binding for the rest of this step — recorded early so a resumed session stays consistent)
1. The hook is `main.after_push_command(command, result, target=None, episode_range=None)`, called by the CLI
   dispatcher right AFTER each push-family `cmd_*` call returned — never inside a cmd_* function (rollback change-gate:
   every journal of the command was committed / rolled back before its function returned). An exception (incl.
   RollbackHardFail / SystemExit / KeyboardInterrupt) propagates exactly as today and never reaches it. It decides
   success, then calls `maybe_auto_confirm(library)` (PLAN P-19's name).
2. "Succeeded" (PLAN Step 13b: returned normally and not False) — refined for a None result: cmd_push_group,
   cmd_prep_push_rep and cmd_prep_push_rep_season return None on success AND on every handled failure (prep_push_rep
   even swallows a post-PONR RollbackHardFail), so None alone would run the auto-confirm after their failures, against
   P-19's "never on failure". None counts as success only when the run's targets reached the command's end state,
   read from the library the command saved — the signal the IMP-D22 enrich autopilots already use: prep_push_rep →
   the entry (its id as given) archived; prep_push_rep_season → every `_season_run_target_ids` target archived;
   push_group → every target of the run (season children or id prefix, episode range, de-aliased) uploaded.
   webui/server.py's `_NONE_IS_SUCCESS` reached the same conclusion for prep_push_rep. True / False results are taken
   as they are (cmd_push, the extras-only push, the two enrich autopilots).
3. The child: exactly `[sys.executable, <abs main.py>, "check_photos", "pending", "--apply", "--max-minutes", "15"]`,
   spawned through `main._cp_spawn` (Step 13's streaming idiom: UTF-8 + unbuffered child env, every line re-printed,
   Ctrl+C waits for the child to close its Chrome). Its exit 0 (ok / partial) → its own last line is the summary;
   non-zero / interrupted / not startable → ONE warning line. The conftest guard `_hermetic_check_child` already
   refuses `_cp_spawn` in every test; tests install a runner.
4. Gates in order: config off → silent (before any library read) · nothing pending → silent · web job
   (MEDIAVAULT_WEB_JOB set, not "0") → reminder · no pending object pushed ≥ AUTO_CONFIRM_MIN_AGE_H ago (unknown push
   time counts as old — Step 13's verdict rule) → reminder · no Chrome profile for ANY account the due objects route
   to → reminder (one missing of several: the check child reports it per object and confirms the rest) · fetch lock
   held (probed with the same non-blocking acquire the child uses; a stale lock is reclaimable) → reminder · else run.
   Reminder text verbatim from the PLAN with N = all pending objects.
5. AUTO_CONFIRM_MIN_AGE_H = 24 (measured — see Log): 12 h finds 59 % of objects not yet backed up; 24 h ≈ "next day"
   (OD-6's words) and 84 % backed up; the curve flattens after 24 h (88 % at 36 h), 99 % at 48 h.
6. Config: `auto_confirm_after_push` absent / null / true → on; false → off (silent); any other value → off + one
   stderr warning (a present non-boolean is an attempt to configure the switch — the safe reading is "don't open
   Chrome"), mirroring mainfetch's fetch_account_overrides warnings.
7. webui/server.py is NOT changed: it runs push / prep_push_rep IN-PROCESS (main.cmd_* under redirect_stdout), never a
   push-family child, so a web job never reaches the CLI-dispatcher hook (no Chrome, no reminder). The
   MEDIAVAULT_WEB_JOB check stays as the guard the PLAN names for any dispatcher run a web job might spawn.

## Assumptions (also listed in the final report)
- (A1) "their profiles exist" (P-19) = at least one account the due objects route to has its Chrome profile (decision 4).
- (A2) The reminder's N counts every pending object (young ones included); nothing pending → no line at all.
- (A3) A hook header line before the child ("… confirming them now …; turn off: …") is part of the auto-confirm's own
  output, printed after the push command's last line — the push's own output is unchanged.
- (A4) Ctrl+C inside the hook (before or during the child) ends it with one warning line; the push's exit code stays 0.

## Log
- 2026-10-13 dispatched; read-in: base contract (executor-opus.md), PLAN (protocol, P-11, P-19, Steps 13/13b/15/16,
  consumer row 29/31/32, risks, §M14), DECISIONS (OD-6, §1c–§1f), Step 13 STATUS + a62e7d4, main.py dispatcher + the six
  push-family functions + RollbackJournal/recover_journal + cmd_prep/cmd_replace PONR, mvcommon unverified_objects /
  capture reader / fetch_session_lock / config, mainfetch profile_preflight + the check child's missing-profile path,
  webui/server.py (in-process actions, _NONE_IS_SUCCESS), tests/conftest.py, the dispatcher harnesses
  (test_prep_push_rep_enrich_cli, test_prep_keeps_split_record, test_check_photos, test_map_photos),
  docs/testing-strategy.md.
- Model: session env claude-fable-5-1; the harness's commit-attribution notice alternates Fable 5.1 / Opus 5.5 (the quirk
  Steps 8–13 recorded) — each commit carries the notice current at commit time.
- MEASURED (read-only, aggregates only — P-15): D:\MediaVault_gp_inventory\map_r1b_20261003_1714\mapping.jsonl, objects
  with a capture push time AND a cloud upload time: 69 in 14 pushes (one artifact excluded from the reading: a re-push
  matched to an older cloud copy, negative delay). Backed up within 6 h 10.1 % · 12 h 40.6 % · 18 h 68.1 % · 24 h
  84.1 % · 30 h 85.5 % · 36 h 88.4 % · 48 h 98.6 % · 50 h 100 % (median 13.6 h, p90 38.2 h, max 49.6 h — the PLAN's
  numbers). Whole pushes fully backed up: 24 h 71.4 % · 48 h 92.9 %.
- 2026-10-13 a usage-limit pause hit during 13b.c (main.py's dispatcher wiring written, uncommitted); resumed on the
  coordinator's word: the diff was coherent (six call sites, arguments unchanged), its tests written and green → kept
  and committed as 8fb4d6a.
- 2026-10-13/14 13b.e: the self-review corrected two docstrings (conftest: Step 15's push-family smoke runs — today's
  smoke does not drive the dispatcher; the test module: webui starts no Python child — its open-folder os.startfile is
  allowed). A second usage-limit pause hit while the full suite re-ran on that final code in the background; it had
  completed (2900 passed, exit 0); smoke then ran after it. One stray no-op `sed -i` (its pattern matched nothing) was
  issued during 13b.d — the diff confirmed no change; every file edit otherwise went through Edit / Write.

## Step 13b — [status: done]
- Executor: executor-fable
- Model: fable (session env claude-fable-5-1; each commit's trailer is the harness attribution notice current at commit
  time, which alternated Opus 5.5 / Fable 5.1 — see Log; two usage-limit pauses, resumed on the coordinator's word)
- Mode: single-executor (own worktree, WIP commits per the PLAN's execution protocol)
- Files changed: main.py (a new section after cmd_check_photos — AUTO_CONFIRM_MIN_AGE_H = 24, AUTO_CONFIRM_MAX_MINUTES,
  AUTO_CONFIRM_REMINDER, AUTO_CONFIRM_WEB_JOB_ENV, auto_confirm_argv, _auto_confirm_on, _auto_confirm_warning,
  _auto_confirm_why, maybe_auto_confirm, _push_group_run_target_ids, _push_command_completed, after_push_command — and
  the six push-family branches of the CLI dispatcher: keep the command's return value, then call the hook),
  mvconfig.example.json (documents `auto_confirm_after_push`), tests/conftest.py (the `_REAL_*` captures,
  `_InertAutoConfirm`, the autouse `_hermetic_auto_confirm`, the opt-in `real_auto_confirm`), tests/test_auto_confirm.py
  (new, 162 tests), .candidates/c25-step13b/STATUS.md (this file). NOT changed: webui/server.py (it runs push /
  prep_push_rep in-process, spawns no push-family child), any cmd_* body, RollbackJournal / recover_journal /
  RollbackHardFail / PONR placement / the journal format / the season resume messaging, ENTRY_TYPE_KEYS, mvcommon,
  mainfetch, gprpc, gpweb, gpcapture, any existing test.
- Outcome: once a push-family command (push incl. its extras-only path, push_group, prep_push_rep, prep_push_rep_season,
  prep_push_rep_enrich, prep_push_rep_season_enrich) has RETURNED successfully, the CLI dispatcher calls
  main.after_push_command, which confirms the pushed objects Google Photos has had time to back up: `python main.py
  check_photos pending --apply --max-minutes 15` (exact argv) runs as a child through _cp_spawn (Step 13's streaming
  idiom), after one line saying so; the child's last line is the check's summary. Config off → silent (nothing read);
  nothing pending → silent; a web job, only pushes younger than 24 h, no Chrome profile for any account the old objects
  route to, or a held fetch lock → the PLAN's one reminder line; a failing / interrupted / unstartable child or any
  error → one warning line; it never raises, so the push's output and exit code are unchanged. A failed command (False,
  an exception, a usage error — or a None whose run did not reach its end state) gets nothing at all.
- Key decisions: design decisions 1–7 and assumptions A1–A4 above. Deviations from the PLAN text, each surfaced: (D1)
  "succeeded" for a None result is read from the run's end state, because three of the six commands return None on
  their handled failures too (the PLAN's literal rule would have run the auto-confirm after e.g. "Auto-Pilot Paused"
  or a caught post-PONR hard fail — against P-19's "never on failure"); (D2) AUTO_CONFIRM_MIN_AGE_H = 24, not 12, from
  the measurement the PLAN asked for; (D3) one header line before the child (what runs, how to turn it off); (D4)
  mvconfig.example.json documents the switch (consumer row 31); (D5) no webui change — the PLAN's change was conditional
  on the web UI spawning push-family commands, which it does not; (D6) a non-boolean switch value reads as off with one
  stderr warning. Contracts for later steps — 15: `main.after_push_command.calls` (the conftest stand-in) shows the
  dispatcher reached the hook and with what; probes P1–P3 + P22 are the "auto-confirm only after success" mutations; the
  real hook needs `real_auto_confirm` + a runner for main._cp_spawn. 16: the reminder / header / warning lines (exact
  text in tests/test_auto_confirm.py), the 24 h minimum age, the switch (`auto_confirm_after_push`, false = off), that
  web-console jobs never auto-confirm, docs/testing-strategy.md entries for `_hermetic_auto_confirm` /
  `real_auto_confirm` (like Steps 3/11/13's guards, not documented there yet). R2 §M14: run the next push at least 24 h
  after M11's push (earlier, it prints the reminder instead); the disable test needs an mvconfig.json in the worktree.
- Verification: tests/test_auto_confirm.py 162 passed (≈12–18 s); neighbours per sub-task (see 13b.a–d); disarm 24/24
  (scratchpad disarm_13b.py on a `git archive` copy of d016010, the copy green before and after); full suite 2900
  passed, 1 warning (the pre-existing httpx deprecation) on d016010 (471 s) and on the final code (679 s); smoke 82
  passed (20 s / 13 s), run one after the other. Self-review (V2 delta 4): (a) the Step 13b bullets one by one — files,
  details (exactly P-19 + the six contracts documented + the exact argv + the streaming idiom + the reminder text +
  the silent switch), the test list (success + old → exact argv · only young → reminder · failure / exception →
  nothing · lock held → reminder · disabled → nothing · web marker → reminder only · the child failing never changes
  the output or exit code · journal-op instrumentation identical with and without the hook · the disarm probe moving
  the hook before the success check) and the acceptance (full + smoke; smoke cannot start Chrome: the guards) — all
  covered; (b) consumers R2 §M14 / Step 15 / Step 16 — contracts above; (c) change-gates — no rollback code touched
  (pinned by the instrumentation test + P22), ENTRY_TYPE_KEYS untouched, no new entry type or shared field, the one new
  whole-library pass (_push_group_run_target_ids' prefix mode) de-aliases through _resolve_alias, smoke green.
