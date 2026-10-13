STATE: IN PROGRESS — next: 13.i resumability + never-downloads pin + whole-diff self-review

# IMP-C25 Step 13 — `check_photos`: download-free verification, sweep and confirm — resumable status

- Plan: docs/feature-fetch-datetime/PLAN.md Step 13 (+ P-1…P-20, OD-5/OD-7/OD-9, Step 9b rule, DECISIONS §1c–§1f).
- Worktree `C:\Users\harin\PycharmProjects\MediaVault-s13` · branch `tmp/c25_step13` · base 5c46140 (Steps 0–12 + 9b + the
  G12 cherry-picks + the "Restore pages?" fix). The main folder is never touched (the user archives from it).
- Python: `C:\Users\harin\PycharmProjects\MediaVault\.venv\Scripts\python.exe -m pytest …`, run from the worktree.
  Gates run SEQUENTIALLY (parallel full suites slow the machine badly).
- Baseline at 5c46140: tests/test_locator.py + tests/test_map_photos.py 230 passed (48 s); full suite 2580 · smoke 82
  (orchestrator, 1e6a66f).

## Resume rule
Read this file, then `git log --oneline -15` (WIP commits: `wip(imp-c25): step 13.<x> — …`) and `git status`. An
uncommitted diff belongs to the first unticked sub-task: keep it when coherent and its targeted tests pass, else
`git checkout -- <its files>` (record that here). Continue at the first unticked sub-task. Commit THIS file
(force-added: `.candidates/` is gitignored) with every WIP commit; tick a sub-task only after its tests pass, with its
SHA. Pathspec commits only; never -A, amend, rebase or stash. Never edit PROGRESS.md (the orchestrator does).

## Sub-tasks (one WIP commit each)
- [x] 13.0 locator: `per_approach_pick` (parallel map {approach: photo_id of its pick | None}, for the OD-7
      "approaches disagree" outlier) + `if __name__ == "__main__"` registers the script as module `mainfetch`
      (gprpc's lazy `import mainfetch` and tools.gp_inventory otherwise load a SECOND copy whose
      SessionExpiredError the script never catches) — tests: tests/test_check_photos.py 5 passed (per_approach_pick
      ×4, the one-module subprocess test — disarm-checked on a scratch copy: without the alias it prints
      TWO-COPIES); tests/test_locator.py 96 passed unchanged — WIP SHA in the commit that ticks this line
- [x] 13.a main: `parse_check_photos_args` + CHECK_USAGE + dispatcher branch + `check_photos pending --list` (offline)
      — tests: tests/test_check_photos.py 37 passed (parser 7 + 20 errors, dispatcher 3, pending --list 2);
      test_map_photos + test_prep_push_rep_enrich_cli 236 passed (the dispatcher harness) — WIP SHA in the commit
      that ticks this line (cmd_check_photos is a stub for every other scope until 13.c)
- [x] 13.b main: scope resolution — item (alias resolves) · season_map (children, anime glued ids) ·
      `series:<season id | show folder>` (show-folder rule + same-show guard) · id prefix · `account:<a>` · pending ·
      hinted · all · `--account` filter; ALWAYS printed before running — tests (flat + nested series, anime, oth-):
      tests/test_check_photos.py 49 passed (+12: item/alias, season/anime, series ×3 targets, folder = same scope,
      flat show, language folder, anime series, Others + a 2nd list root, prefix/all/account/pending/--account/errors,
      hinted) — WIP SHA in the commit that ticks this line
- [x] 13.c main: run directory (create / resume by key / `--recheck`), run.json, child spawn with the streaming idiom
      (Ctrl+C-safe: the parent waits for the child to close Chrome), conftest guard (no test spawns the real child) —
      tests: tests/test_check_photos.py 67 passed (+18: new run + argv, resume / --recheck / other key, complete ->
      afresh, 9 statuses, refused, empty scope, the guard, the real spawn streaming + Ctrl+C waiting) — WIP SHA in the
      commit that ticks this line. tests/conftest.py gained the autouse `_hermetic_check_child` (a child process sees
      no monkeypatch: it would read the REAL library and launch a REAL Chrome — required for P-17 hermeticity)
- [x] 13.d mainfetch engine: `parse_check_args` + `cmd_check` (`python mainfetch.py check --run <dir> …`): fetch
      lock (non-blocking) + heartbeat (the crawler's Heartbeat), start-up cleanup + fetch journal (crash-cleanup of
      the Chrome it launched), one session per account, locators via build_fetch_queue (folder that never exists),
      locate_object per object, results.jsonl (fsync per line), progress.json, pacing, circuit breaker, --limit,
      --max-minutes, Ctrl+C, robust Chrome close (kill the launched Chrome by its Popen handle when the shared close
      leaves it running; profile marked clean) — tests: tests/test_check_photos.py 96 passed (+29: engine end to end
      on the fake world (only run files written, download-free, no photo id printed, P-13), invocations, max-minutes
      + continue, pacing ×3, breaker, sign-out, Ctrl+C, lock held / no-wait / taken over, missing profile, the Chrome
      guarantee ×6, every session closed, the child's command line ×8, the child CLI + _SafeStream) — [x] when its
      WIP commit lands (the code for 13.f's cross pass and hints is in this commit; its tests come with 13.f)
- [x] 13.e mainfetch: RPC acceleration (batch hash_lookup of content keys + mapped cloud dedupKeys, batch_info names,
      ≤50 per call, warming the locator's own record cache — no second verifier) + quality post-pass
      (original_quality / partial_upload / archived) + UI fallback — tests: tests/test_check_photos.py 103 passed (+7:
      pending by one swbisb batch, mapped legacy objects batch-confirmed with proof filename (never dedupkey), 120
      objects -> swbisb 50+50+20 and no item info / search, RPC down -> web pages and no quality claimed, a failing
      batch -> per-object lookups, quality lines, a gone mapped item -> stale + re-upload by name); test_locator 96 —
      WIP SHA in the commit that ticks this line. `_check_locators` also hands back each object's learned record (the
      fetch's locator shape is pinned EXACTLY by test_fetch_routing — nothing was added to it)
- [x] 13.f mainfetch: cross-account pass (object not found in its routed account → looked for in every other account
      whose profile exists: "account mismatch" / "in no account") + hinted locators (url → photo_id, datetime → taken
      second) — tests: tests/test_check_photos.py 112 passed (+9: found in another account (2 sessions only), found
      nowhere (every account with a profile), a recent push not looked for elsewhere, ambiguous not looked for
      elsewhere, wrong-item looked for elsewhere (by its content key), the session order, url hint ×2, datetime hint,
      the hint leaves the fetch's locator alone) — WIP SHA in the commit that ticks this line. REFINED RULE (decision
      4): the cross pass runs only for an object whose own account answered not-found / wrong-item AND that is not a
      push of the last MAP_RECENT_DAYS (run.json `elsewhere`, set by main) — P-19's auto-confirm then opens Chrome only
      for the accounts it needs; sessions: routed accounts first (round from the last), then the look-elsewhere work from
      the first account — each account at most twice
- [x] 13.g main: verdicts (found / account mismatch / inconsistent / ambiguous / wrong-item / Step 9b
      not-backed-up-yet · possible-loss (+ `--devices` phone opt-in) / partial sets / error), report.txt (per account ×
      category × approach), outliers.csv (8 OD-7 classes + stale photo_id), summary line — tests: tests/
      test_check_photos.py 127 passed (+15: every outlier class from its own synthetic case (approaches-disagree,
      single-approach, legacy-only, ambiguous-everywhere + duplicates, account-mismatch found-elsewhere + override,
      duplicates same bytes, partial-upload + not-original-quality, taken-time-differs, stale-photo-id), the report
      (verdicts, per account/category/approach, LOUD block, recent block, failed-list line, Next, Outputs, the summary
      line, outliers.csv BOM + CRLF + header), a name held by other bytes (recent -> not-backed-up-yet, later ->
      possible-loss / reason wrong-item), --devices (awaiting-backup / missing-after-backup / missing-everywhere), a
      partial chunk set, an unfinished run, errors never failed) — WIP SHA in the commit that ticks this line.
      REFINED: no separate `wrong-item` verdict — S1 / S3 "wrong-item" only means other items were seen (a release
      batch's other episodes, a split's sibling chunks); an unpinned object is Step 9b's not-backed-up-yet /
      possible-loss, its failed-list reason keeps wrong-item (the fetch's own reason chain), and a content-keyed object
      whose exact-name search finds only other bytes gets a note
- [x] 13.h main: apply — `--apply` (check, then apply) and `--apply-run <dir|latest>` (no search; scope must match;
      latest = newest completed run): learned records (source check | confirm), stale records, failed rows (P-18, apply
      only), idempotent appends, the apply engine, failed_objects.csv, applied.json — tests: tests/test_check_photos.py
      147 passed (+20: the whole apply on a 4-mode season (records, proofs, stages, quality, blocks confirmed, a legacy
      object's cloud key never a block content key, the failed row + failed_objects.csv, applied.json, report, summary),
      a dry run then --apply-run latest / by directory (no Chrome, no child, appended once), refusals (scope · account ·
      approaches · not a run dir), latest needs a complete run (names the unfinished one; none at all), an unfinished
      run applied by its directory (its unfinished object stays unchecked, no failed row), pending --apply (source
      confirm, the pending list empties), found in another account (recorded there, home_account, routed there next),
      a gone photo_id (stale record, the re-upload the identity), inconsistent copies (no identity, failed rows,
      nothing-to-apply is no failure), Ctrl+C (nothing applied; the re-run applies the whole run), --max-minutes +
      --apply then the rest, --approaches all (ok / failed per stage), ambiguous stages recorded failed, an exactly-one
      on ANOTHER item makes no ok claim, the look elsewhere decided at the check, a failed look elsewhere (error, no
      loss), an ambiguous look elsewhere (ambiguous there), an apply that cannot record (sidecar · engine) fails);
      test_locator + test_map_photos 230 passed — WIP SHA in the commit that ticks this line. Two 13.b assertions and
      one 13.g assertion of this file changed with the refined rules below (`elsewhere` left run.json; a complete
      run's retry hint)
- [ ] 13.i resumability (resume / --recheck / limit + max-minutes continue / complete → new run) + never-downloads pin
      + whole-diff self-review fixes — tests
- [ ] 13.j gates: disarm probes (isolated scratch copy) · full suite · smoke · live read-only dry run if port 9222 is
      free and no MediaVault fetch/crawl runs · self-review · STATE: COMPLETE · final feat commit

## Design decisions (binding for the rest of this step — recorded early so a resumed session stays consistent)
1. Split of work: main.py owns the CLI, scope resolution, the run directory, the report / outliers, Step 9b and every
   write (learned sidecar, failed list, apply engine); the child `python mainfetch.py check --run <dir> [--limit N]
   [--max-minutes M] [--pace S]` owns the browser: it reads run.json, writes ONLY results.jsonl + progress.json in the
   run dir (never the sidecar, never the library) and its fetch journal. `--apply` = the same child run, then main
   applies the run; `--apply-run` applies a reviewed run's results.jsonl without any browser.
2. Engine = `mainfetch.locate_object` unchanged in behavior (one additive parallel map `per_approach_pick`). Locators
   come from `build_fetch_queue(entry, <folder that never exists>, ctx, entry_id)` exactly like `cmd_locate`
   (extras: build_extras_entries); a hinted object's locator gets the hint's photo_id / taken second.
3. RPC acceleration warms the locator's own record cache (`_remember` + the hash listing) with batched swbisb
   (content keys AND mapped cloud dedupKeys) and EWgK9e (names) — S0a / S0b then hit the cache; the verdict logic is
   the locator's (`_verdict` / `_judge`). A mapped cloud dedupKey is NEVER treated as the object's content key: a
   legacy object's proof stays `filename` (P-5; IMP-E5 relies on dedupkey/sha256 meaning "our bytes").
4. Per object: routed account first (override > learned home > prefix; a hint's account for `hinted`); not-found or
   wrong-item there AND not a push of the last MAP_RECENT_DAYS → the other accounts whose profile exists (cross pass,
   stop at the first find) → found elsewhere = account mismatch; found nowhere = "in no account" → Step 9b: pushed
   within MAP_RECENT_DAYS before the CHECK time → not-backed-up-yet (no failed row; NOT looked for elsewhere — the
   auto-confirm opens only the accounts it needs, P-19), else / push time unknown → possible-loss; `--devices` = the
   phone opt-in (map's `_mp_device_check`). An ambiguous object (its name IS in its account) is not looked for
   elsewhere. A newest-set pick (several same-name uploads, different bytes) is NOT confirmed
   (ambiguous). A found item flagged partial_upload / not original quality → inconsistent (never confirmed).
   REFINED in 13.h (so an apply never books a verdict that is not final): the look-elsewhere decision is taken by the
   child AT THE CHECK (run.json `recent_days` + each object's `pushed_at` vs its routed line's `at` — exactly main's
   verdict rule; the creation-time `elsewhere` flag is gone: a run resumed across the 7-day boundary stays
   consistent); an object whose look elsewhere ERRORED → `error` ("RuntimeError in 'anime'"; no loss claimed, no
   failed row); while the run is not complete, an account still to look in → `unchecked` (note names them); a look
   elsewhere that was AMBIGUOUS → `ambiguous` in that account (its failed row names it and its candidates). In a
   complete run, accounts never looked in had no Chrome profile (the possible-loss note says so).
5. Resume: re-running the same key (scope + --account + approaches) continues the newest unfinished run: objects with
   a final result are skipped, `error` results are retried; `--recheck` starts a fresh run; a completed run is never
   resumed (the next run starts fresh). Library-confirmed objects are NOT skipped (R3's check after map --apply and
   R4's sweep must verify every object).
6. Learned records on apply: found → P-10 record (source `confirm` for `pending`, else `check`; proof dedupkey |
   filename; approach of the pinning stage; same-bytes alternates; quality flags) + one verdict-only record per other
   evaluated approach (approach.ok / approach.failed); stale photo_ids → stale records; NOT found → failed list only
   (no library block is created for an unconfirmed object). Appends are idempotent (map's `_mp_append_new` /
   `_mp_append_failed`). REFINED in 13.h: no verdict-only record for a stage that errored, or that answered
   exactly-one with ANOTHER item than the recorded one or its same-bytes alternates (a contradicting `ok` — the
   approaches-disagree outlier is the user's to review); an ambiguous stage IS recorded failed (true: it does not pin
   the object). `--apply` applies after any run but an interrupted one (Ctrl+C: nothing applied; the re-run applies
   the whole run); `--apply-run <dir>` may apply an unfinished run (its unfinished objects stay unchecked);
   `latest` = the newest COMPLETE run of the scope (else the error names the unfinished run). An apply that cannot
   append, or whose engine errs / aborts → status error (exit 1).
7. Chrome: every session ends with `_close_session` (Step 11 i) and then, if the Chrome this run LAUNCHED still runs
   (its Popen handle: poll() is None), it is killed through that handle and its profile marked cleanly exited — also
   after an error or Ctrl+C. The run's fetch journal records each session, so a hard-killed check is cleaned up by the
   next fetch/check start-up.

## Assumptions (also listed in the final report)
- (A1) "re-run skips confirmed objects unless --recheck" = resume semantics (decision 5), not "skip objects the library
  already confirms" — the M12/R4 flows check confirmed objects on purpose.
- (A2) Step 9b's reference time for a live check is the moment the routed account was checked (the check IS that
  account's look), not an inventory crawl start.
- (A3) "Account mismatch (found in a non-home account)": home = the trusted block's learned home_account, else the id
  prefix's account.
- (A4) `--pace S` = minimum seconds between two objects' checks in a session (+ up to 25 % jitter); default 3 s after an
  object that used the web pages, 0.5 s after an RPC-only one (gprpc's own per-call pacing stays).

## Log
- 2026-10-13 dispatched; read-in (PLAN, DECISIONS §1c–§1f, PROGRESS, Step 12 DECISION/CRITIQUE/PICKS, mainfetch
  locator + hygiene + routing + locate, mvcommon identity, main apply engine + map_photos, gprpc, gpweb parsers, fake
  Google Photos, conftest, test_locator harness, testing-strategy). A usage-limit pause hit during the read-in; resumed
  on the coordinator's word (tree clean at 5c46140).
- Model: session env claude-fable-5-1; the harness's commit-attribution notice alternates Fable 5.1 / Opus 5.5 (the
  quirk Steps 8–12 recorded) — each commit carries the notice current at commit time.
- 2026-10-13 resumed after a limit (coordinator: 13.e–13.g committed at c79b58b, port 9222 free). The uncommitted 13.h
  main.py diff (the apply functions) was coherent: kept, completed and tested; the verdict refinements of decisions 4 /
  6 were found while writing its tests (an apply must never book a verdict that is not final).
