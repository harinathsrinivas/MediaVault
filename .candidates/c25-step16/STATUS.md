STATE: IN PROGRESS — next: 16.a ARCHITECTURE.md

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
- [ ] 16.a ARCHITECTURE.md — §5 entry points, §6.3 `gphotos` block, §6.5 capture + mvmeta v2, §7.5 capture, §8
      rewrite (§8.1 four profiles, §8.3 drift, trigger_download line), new §8.9 read-only RPC, §10 Stage 4, §11,
      §12a rollback-neutral addendum, §13, §14, §16/§17 strikes (C5/A8/B10), §18, last-updated line
- [ ] 16.b README.md — fetch bullet, command table, where downloads go, locate, crawler modes, config keys,
      auto-confirm line, state dirs
- [ ] 16.c docs/OPERATIONS_QA.md — §6b Q&As (PLAN Step 16 list), Last updated
- [ ] 16.d docs/testing-strategy.md — fake Google Photos, hermetic redirects, guards, smoke Group 5
- [ ] 16.e docs/README.md (index) + docs/feature-fetch-datetime/INVENTORY_AND_CAPTURE.md (status update)
- [ ] 16.f parser replays (offline) + smoke gate + final commit, STATE: COMPLETE

## Parser replays
(filled in at 16.f)

## Log
- 2026-10-20 dispatched; read-in done (see Sources). Resumed after a usage-limit pause on the coordinator's word;
  nothing had been written yet. Coordinator note: R2 M4–M9 / M18 run live from the main folder — keep replays offline.
