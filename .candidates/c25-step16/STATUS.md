STATE: IN PROGRESS — next: 16.c docs/OPERATIONS_QA.md

# IMP-C25 Step 16 — Documentation (architect) — resumable status

- Plan: docs/feature-fetch-datetime/PLAN.md Step 16 (file list, known drift, Q&A list).
- Worktree `C:\Users\harin\PycharmProjects\MediaVault-s16` · branch `tmp/c25_step16` · base de5620b (Steps 0–15 done).
  The main folder is never touched. Docs only; code is read-only.
- Sources read: PLAN.md, DECISIONS.md (§1c–§1f), PROGRESS.md, root STATUS.md (main folder, read only) Steps 0–11 +
  the live wrong-item pick, `.candidates/c25-step-12/DECISION.md` + C/PICKS_STATUS.md, c25-step13/13b/13c/14/15
  STATUS.md, and the code (main.py, mainfetch.py, gprpc.py, mvcommon.py, gpweb.py, gpcapture.py, tools/, webui/).
- Live checks R2/R3: pending (the orchestrator runs them). Placeholders marked "results pending R2/R3".
- Parser replays: offline only (no Chrome, no devices, no real library) — results recorded below.

## Resume rule
Read this file, then `git log --oneline -10` (WIP commits `wip(imp-c25): step 16.<x> — …`) and `git status`. An
uncommitted doc diff belongs to the first unticked sub-task: keep it if coherent, else `git checkout -- <file>`.
Commit THIS file (force-added: `.candidates/` is gitignored) with every WIP commit. Pathspec commits only.

## Sub-tasks (one WIP commit each)
- [x] 16.a ARCHITECTURE.md — §1, §3 (+ state tree), §4, §5 (new commands, auto-confirm note, mainfetch verbs,
      fetch-parsing drift), §6.3 `gphotos` block (+ stale extras refs re-verified), §6.5 capture v2 + mvmeta v2 +
      mvmeta_upgrade, §7.5 capture, §7.7 verify_restore tempdir, §7.8, §8 rewritten (8.1 four profiles + routing,
      8.3 S4 drift fixed: plain filename / F14 / trigger_download :433, 8.4 locator, 8.5 hygiene + Chrome lifecycle,
      8.6 manual/resume, 8.7, 8.8 lock, 8.9 read-only RPC, 8.10 identity commands), §9.1b, §9.3, §10 Stage 4, §11,
      §12, §12a addendum, §13, §14, §15, §16/§17 (C5/A8/B10 struck), §18, last-updated — 7fe816b
- [x] 16.b README.md — overview + fetch bullet (drift fixed: no `~/Downloads`), map/check/resolve bullet, tech
      stack state, command table (fetch/fetch_restore `--manual`, verify_restore tempdir, fetch_cleanup,
      map_photos, check_photos, resolve_photos, mvmeta_upgrade), `mainfetch.py locate`, learned routing, new
      section "Exact Google Photos item" (downloads, crawler modes, backfill, auto-confirm lines, failed list,
      config keys, state dirs), file layout, status — WIP SHA: the commit that ticks this line
- [ ] 16.c docs/OPERATIONS_QA.md — §6b Q&As (PLAN Step 16 list), Last updated
- [ ] 16.d docs/testing-strategy.md — fake Google Photos, hermetic redirects, guards, smoke Group 5
- [ ] 16.e docs/README.md (index) + docs/feature-fetch-datetime/INVENTORY_AND_CAPTURE.md (status update)
- [ ] 16.f parser replays (offline) + smoke gate + final commit, STATE: COMPLETE

## Parser replays
(filled in at 16.f)

## Log
- 2026-10-20 dispatched; read-in done (see Sources). Resumed after a usage-limit pause on the coordinator's word;
  nothing had been written yet. Coordinator note: R2 M4–M9 / M18 run live from the main folder — keep replays offline.
