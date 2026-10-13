STATE: IN PROGRESS — next: 13.a main CLI parser + dispatcher + pending --list

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
- [ ] 13.a main: `parse_check_photos_args` + CHECK_USAGE + dispatcher branch + `check_photos pending --list` (offline)
      — tests
- [ ] 13.b main: scope resolution — item (alias resolves) · season_map (children, anime glued ids) ·
      `series:<season id | show folder>` (show-folder rule + same-show guard) · id prefix · `account:<a>` · pending ·
      hinted · all · `--account` filter; ALWAYS printed before running — tests (flat + nested series, anime, oth-)
- [ ] 13.c main: run directory (create / resume by key / `--recheck`), run.json, child spawn with the streaming idiom
      (Ctrl+C-safe: the parent waits for the child to close Chrome), conftest guard (no test spawns the real child) —
      tests
- [ ] 13.d mainfetch engine: `parse_check_args` + `cmd_check` (`python mainfetch.py check --run <dir> …`): fetch
      lock (non-blocking) + heartbeat (the crawler's Heartbeat), start-up cleanup + fetch journal (crash-cleanup of
      the Chrome it launched), one session per account, locators via build_fetch_queue (folder that never exists),
      locate_object per object, results.jsonl (fsync per line), progress.json, pacing, circuit breaker, --limit,
      --max-minutes, Ctrl+C, robust Chrome close (kill the launched Chrome by its Popen handle when the shared close
      leaves it running; profile marked clean) — tests
- [ ] 13.e mainfetch: RPC acceleration (batch hash_lookup of content keys + mapped cloud dedupKeys, batch_info names,
      ≤50 per call, warming the locator's own record cache — no second verifier) + quality post-pass
      (original_quality / partial_upload / archived) + UI fallback — tests
- [ ] 13.f mainfetch: cross-account pass (object not found in its routed account → looked for in every other account
      whose profile exists: "account mismatch" / "in no account") + hinted locators (url → photo_id, datetime → taken
      second) — tests
- [ ] 13.g main: verdicts (found / account mismatch / inconsistent / ambiguous / wrong-item / Step 9b
      not-backed-up-yet · possible-loss (+ `--devices` phone opt-in) / partial sets / error), report.txt (per account ×
      category × approach), outliers.csv (8 OD-7 classes + stale photo_id), summary line — tests (each outlier class)
- [ ] 13.h main: apply — `--apply` (check, then apply) and `--apply-run <dir|latest>` (no search; scope must match;
      latest = newest completed run): learned records (source check | confirm), stale records, failed rows (P-18, apply
      only), idempotent appends, the apply engine, failed_objects.csv, applied.json — tests
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
4. Per object: routed account first (override > learned home > prefix; a hint's account for `hinted`); not exactly-one
   there → the other accounts whose profile exists (cross pass, stop at the first find) → found elsewhere = account
   mismatch; found nowhere = "in no account" → Step 9b: pushed within MAP_RECENT_DAYS before the CHECK time →
   not-backed-up-yet (no failed row), else / push time unknown → possible-loss; `--devices` = the phone opt-in
   (map's `_mp_device_check`). A newest-set pick (several same-name uploads, different bytes) is NOT confirmed
   (ambiguous). A found item flagged partial_upload / not original quality → inconsistent (never confirmed).
5. Resume: re-running the same key (scope + --account + approaches) continues the newest unfinished run: objects with
   a final result are skipped, `error` results are retried; `--recheck` starts a fresh run; a completed run is never
   resumed (the next run starts fresh). Library-confirmed objects are NOT skipped (R3's check after map --apply and
   R4's sweep must verify every object).
6. Learned records on apply: found → P-10 record (source `confirm` for `pending`, else `check`; proof dedupkey |
   filename; approach of the pinning stage; same-bytes alternates; quality flags) + one verdict-only record per other
   evaluated approach (approach.ok / approach.failed); stale photo_ids → stale records; NOT found → failed list only
   (no library block is created for an unconfirmed object). Appends are idempotent (map's `_mp_append_new` /
   `_mp_append_failed`).
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
