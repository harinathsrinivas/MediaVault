STATE: IN PROGRESS

# IMP-C25 Step 17 — IMP bookkeeping on implementation — resumable status

- Plan: docs/feature-fetch-datetime/PLAN.md Step 17 (+ Appendix A/C, "Suggested next tasks after IMP-C25") ·
  DECISIONS.md (all rulings; 2026-10-28: scope rule, R4 = the R3 sweep, mpfix, OD-16 → IMP-C24) · PROGRESS.md (step
  table, operations log, In-progress sub-state "Noticed, not changed (register at Step 17)").
- Worktree `C:\Users\harin\PycharmProjects\MediaVault-s17` · branch `tmp/c25_step17` · base 5002fe8. Docs only — no
  code, no tests. The main folder and the running mpfix worktree are never touched (mpfix read only).
- Python: `C:\Users\harin\PycharmProjects\MediaVault\.venv\Scripts\python.exe`. Gate: `pytest tests/smoke -q` once at
  the end (low memory: nothing else in parallel).
- Files this step may change: improvements/improvements_tier{A,B,C,D,E,G,S,X}.md, improvements/PRIORITY.md,
  docs/priority-graph/priority-graph.html, this file. Never PROGRESS.md / PLAN.md / DECISIONS.md (the orchestrator's).

## Resume rule
Read this file, then `git log --oneline -8` (WIP commits `wip(imp-c25): step17.<x> — …`) and `git status`. An
uncommitted diff belongs to the first unticked sub-task: keep it when coherent, else `git checkout -- <its files>`
(record that here). Commit THIS file (force-added: `.candidates/` is gitignored) with every WIP commit; tick a sub-task
only with its SHA. Pathspec commits only (never -A, amend, rebase or stash); scratchpad secrets scan of the staged
diff before every commit (no real `AF1Qip…` ids, no e-mail addresses, no tokens).

## Sub-tasks (one WIP commit each)
- [x] 17.0 this file: inputs read, every follow-up verified against the code, the registration plan, decisions — dc6e597
- [x] 17.a tier C — C25 done (what shipped, R1–R4 counts), C26 status, C27 in_progress + open list, C5 / C34 / C35 /
      C36 done, C24 interplay line, C7 synergy, C32 repair note, new C39–C44 — (this commit; SHA in 17.b's line)
- [ ] 17.b tiers A / B / D / E / G / S / X — A8 + B10 done, the B4 note, synergy lines E5 / G2 / S7 / X1 / X5 / D9
- [ ] 17.c PRIORITY.md — Last updated, the single 👉 NEXT pointer (IMP-C24; U7 a plain Band 0 entry), Band 0 C27 row
      (+ the C32 note), Band 1 (C25 / C34–C36 done; C39–C43 rows), Band 2 / 4 / 5 prose, DONE list
- [ ] 17.d priority-graph.html — statuses + notes, 6 new nodes, edges, header Next → IMP-C24, footer; Appendix C
      node validation
- [ ] 17.e gates — three-file consistency check, smoke, secrets scan, self-review, STATE: COMPLETE, final commit
      `docs(imp-c25): Step 17 — IMP bookkeeping (C25 done/PR pending, C27 open list, follow-ups registered, NEXT → IMP-C24)`

## Follow-up verification (each "noticed, not changed" item checked against the code at 5002fe8)
Registered (still open):
- (a) fetch session lock, dead holder — `mvcommon.fetch_session_lock` (mvcommon.py:478) judges a held lock only by
  the file's mtime (`stale_after=3600`); it stores the holder pid but never checks it. Non-blocking acquirers refuse
  for up to 1 h after the holder died: the crawler (`tools/gp_inventory.py` crawl_lock, `REFUSED … EXIT_LOCK`),
  check_photos' child (`mainfetch._check_lock`, incl. the auto-confirm gate `maybe_auto_confirm` and the R4 sweep),
  `mainfetch.py locate`, resolve_photos, `fetch_cleanup --apply`, the warm-up. A fetch (`blocking=True`,
  mainfetch.py:5473) reclaims after `timeout=30` s. Seen live: R3 prep (PROGRESS ops log, a killed run's lock removed
  by hand). Two more limits of the same primitive, recorded NOT changed by Step 7 (root STATUS.md Step 7 (11)) and
  still open: (i) a fetch never refreshes the lock (only the crawler / check / resolve heartbeat through
  `gp_inventory.Heartbeat`), so a fetch running > 1 h can be reclaimed as stale mid-run; (ii) the release removes the
  lock file unconditionally (a command that yielded to a takeover deletes the new holder's lock). → IMP-C39.
- (b) chromedriver start crash 0xC0000142 under low memory (thfix F-b; stopped a check run) — `init_driver`
  (mainfetch.py:142–148) prints `❌ Selenium Connection Error` and returns None, no retry; the check sets
  `run.stop = "chrome"` (resumable, mainfetch.py:6473–6476); a fetch returns at once (mainfetch.py:5504). → IMP-C40.
- (c) the crawler's login check — `tools/gp_inventory.ensure_logged_in` (gp_inventory.py:194–199) tests
  `"photos.google.com" not in driver.current_url`, a substring of the WHOLE URL, so a sign-in URL whose continue=
  names photos.google.com passes (mainfetch's `_page_kind` reads the host). Dom crawl only (enumerate + item pages);
  the rpc crawl checks every request through gprpc. `_mark_enumerated` keeps an empty listing from counting as
  complete when ids are known. → IMP-C41.
- (d1) warm_profiles runs no profile pre-flight (Step 14 observation) — `warm_all` / `_warm_one`
  (tools/warm_profiles.py:94, 130) launch every `CHROME_PROFILES` key, `others` included, without
  `mainfetch.profile_preflight`: pointed at the missing `ChromeProfile_Others`, Chrome creates an EMPTY profile that
  lands signed out (LOGGED_OUT + the toast), and from then on the pre-flight of every fetch / check / crawl passes
  that empty folder. → IMP-C42.
- (d2) fetch journals are never pruned (Step 14) — no code deletes `~/.mediavault/fetch_journal/<run_id>.json`
  (`read_fetch_journals`, mainfetch.py:1431, read by `_startup_cleanup`, `fetch_cleanup_plan` and `_resume_hints`
  every run); (d3) the shared Chrome close says "this fetch" (Step 14) — `_close_session` (mainfetch.py:2080–2090) is
  shared by fetch, locate, check_photos, resolve_photos and the warm-up. Both low, both Step 11 session plumbing →
  one batch IMP-C43 (the C14 / C15 batch precedent).
- PLAN "Suggested next tasks after IMP-C25" #10 says "Register as a new IMP after C25": resumable direct transfer
  (HTTP Range resume in Python instead of Chrome's download manager). Its precondition — `mainfetch.py locate <id>
  --probe-direct` showing the minted URL answers without browser cookies — was never recorded (R2 §M6 log has no
  probe line). → IMP-C44, first step = the read-only probe.
Dropped (fixed by a later step, or not a defect — named in the report):
- map_photos' dry-run preview over-count + `not-listed` for an item uploaded after the crawl began → mpfix (user
  ruling 2026-10-28 "Fix both inside C25"; in progress in MediaVault-mpfix, merged before the PR).
- restore-bubble side-fix follow-ups (per-pid query lacks -ErrorAction Stop; the Step 11 close under load; bounded
  driver.get; close a launched Chrome when attach fails; warm-up leaves Chrome on 9222 — also Step 11 known limit
  (ii)) → all done in Step 14.a (`_ProcessTable.query -ErrorAction Stop`, `_terminate_session_chrome`,
  `bound_page_loads`, `_end_launched_chrome`, warm-up `_close_session` + port guard — read in the code).
- Step 13 live finding (quality flags read nothing live) → Step 14.b (`_jspb_field`, timeline pass); original
  quality stays unknown by decision (DECISIONS §1f (10)).
- Step 14: live VrseUb carried no payload[7] → pLFTfd fallback as built; R2 §M6 direct download passed.
- acfix C1–C3 (a harmless leftover run key; M14 wording — docs updated in acfix; bookkeeping); thfix's 121–123-char
  lines (no linter configured); Step 15's docs contract (done in Step 16.d).
- Accepted design limits, not defects: Step 6 (12) (Windows reader vs os.replace — IMP-C24's class), Step 10 known
  limits (i)–(iii), thfix A1 / A9 / A10 residuals.
C37 / C38 keep their status: `_season_resume_cmd` unchanged; tests/test_rehash.py::test_disk_preflight_stops_before_
parts_dir_created still has no device fixture; test_session_detector's logout test still stubs only resolve_targets.
C34 / C35 / C36 → done: Step 10 items g `dd410ed`, h `e673b10` + `77984d3`, i `e333c3d` (feature 0690546); code:
`_retry_lost_downloads` / reason `download-lost`, `_await_search_results` + `_drop_timeline`, `_mainfetch_child_env`
(PYTHONUNBUFFERED=1). C5 / A8 / B10 → done: S2 / S3 / S3u verified stages; the three A8 stubs gone; IMP-B10 hash-once
(`ctx.hashed`). B4 stays pending: trigger_download keeps its 1.5 s / 2 s / 1 s sleeps; only the 3 s search wait became
the bounded poll (C35).

## Design decisions (binding — recorded first so a resumed session stays consistent)
- D1 New codes: C39 lock (3 facets) · C40 chromedriver low-memory start · C41 crawler login check · C42 warm-up
  pre-flight · C43 housekeeping batch (journals + wording) · C44 resumable direct transfer. All `pending`, tier C
  (robustness; C34's lineage for C44). Next free numbers verified: tier C ends at C38; origin/main has nothing newer.
- D2 Bands / priorities: C39 🟡 medium, Band 1 (observed live once; no data loss; a > 1 h fetch reclaimed mid-run is
  inferred from the code, not observed) · C40 / C41 / C42 / C43 ⚪ low, Band 1 (cheap; like C38) · C44 🟡 medium,
  Band 4 "Feels-instant spikes" next to G2 → S7 (a spike gated on a read-only probe).
- D3 OD-16: one 👉 pointer = IMP-C24 (its section already exists). The competing "Headline NEXT: 👉 IMP-U7" line
  becomes the plain order after the pointer; the Band 0 intro loses "highest urgency" / "outranks everything else" (a
  second pointer in other words); the Band 0 table order stays ("U7 stays a Band 0 entry").
- D4 C27: tier `in_progress` (open list + how it closes); graph stays ("crit","decision") — per-case user decisions.
- D5 C25 / C5 / A8 / B10 / C34 / C35 / C36: graph ("done","done") (the done ring, as every done node but D17).
- D6 Graph edges: origins of the new nodes (C17→C39, C25→C39, C25→C40, C25→C41, C17→C42, C25→C42, C25→C43,
  C25→C44, C34→C44) + the synergy edges the tier lines name and the graph lacks (C25→G2, C25→X1, C25→C7, C25→D9;
  C25→E5 / S7 / X5 exist).
- D7 Pre-existing gaps unrelated to C25 (baseline check): D24 has no graph node (PRIORITY Band 5 names it), D2 has a
  node but no tier heading, D17 sits off the done ring, D4 (partial) is in the DONE list, U6 is done but not in it —
  reported, not fixed (surgical).
- D8 DONE list: additive 46 → 53 (+ C25, C5, A8, B10, C34, C35, C36); the existing entries are not re-audited.

## Log
- 2026-11-03 dispatched (executor-opus, Opus 5.5). Read-in: CLAUDE.md (global + project); PLAN Step 17 + Appendix
  A/C + Suggested next; DECISIONS (all); PROGRESS (all); .candidates/c25-{acfix,thfix,step13,step13b,step13c,step14,
  step15,step16}/STATUS.md; the mpfix worktree's STATUS (read only); root STATUS.md Steps 2–11 (main folder, read
  only); tier C (C5, C7, C24–C38), A8, B4, B10, D9, E5, G2, S7, X1, X5; PRIORITY.md; priority-graph.html; the code
  sites above. Baseline graph (Appendix C): TASKS 148 · EDGES 88 · badTasks 0 · badEdges 0.
