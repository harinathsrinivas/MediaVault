STATE: COMPLETE

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
      config keys, state dirs), file layout, status — 249ada0
- [x] 16.c docs/OPERATIONS_QA.md — §6b updated to per-file routing (refs re-verified), §6c IMP-C25 note + the
      stale "fetch leaves its Chrome open" fixed, NEW §6d with every Q&A of PLAN Step 16 (Feb-2022 date, shared
      second, dedupKey check, how fetch finds the item, where downloads go, interruptions, fetch_cleanup, disk
      refusal, Chrome close / no "Restore pages?", direct download + no stored links, internal calls, force an
      account, UK dates, backfill, possible-loss report, archived vs confirmed, auto-confirm lines + off switch,
      confirm by hand, can't-find options incl. kill switch, failed list in Excel, outliers, mvmeta_upgrade);
      R2/R3 placeholder at the top of §6d; Last updated bumped. (§6d instead of "§6b": §6b/§6c already existed
      from IMP-C26/C29.) — c809a50
- [x] 16.d docs/testing-strategy.md — §3 two new mock boundaries, §4.6 mock_fetch status, NEW §4.11 fake_photos, §4.12 the IMP-C25 autouse guards (+ opt-outs), §4.13 smoke Group 5 helpers + the swallowed-raise rule + disarm probes, §11 fetch round-trip done, §12 C25 test commands — 89070ba
- [x] 16.e docs/README.md (feature-fetch-datetime row, testing-strategy + OPERATIONS_QA rows) + INVENTORY_AND_CAPTURE.md (status banner, crawler Step 7, capture v2 + readers, §3 implemented + Step 9b) — a3377ec
- [x] 16.f parser replays (offline) 68/68 OK + smoke 102 passed (74.7 s, machine busy with R2) + final commit — the docs(imp-c25) Step 16 commit

## Parser replays
Offline, 2026-10-20, from the worktree root with the main venv python: scratch script
`<scratchpad>\s16_replay\replay.py` (outside the repo). Before anything ran it replaced `mainfetch.init_driver`,
`subprocess.Popen/run` and `main.save_library` with raisers; every `cmd_*` the dispatcher reaches was a recorder.
No Chrome, no adb, no library read or write, no network. Placeholder ids only (`tv-en-YYYY-show-s01e01`).
**68 replayed · 68 OK · 0 FAIL**:
- main parsers: `map_photos` ×9 (all, --apply, --resolutions [+ --apply], --devices, --devices-only, --account,
  --repair split-info, an id) · `check_photos` ×11 (season --approaches all, series:, account:anime --limit 20,
  account:tv --approaches best|all, --apply-run latest, pending --list, pending --apply [--max-minutes 15], hinted,
  all --approaches all) · `resolve_photos` ×3 · `mvmeta_upgrade` ×4 (incl. `device movies#2 --apply`) · 3 negative
  controls refused (account:tv --list; --apply + --apply-run; --devices-only --apply) · `auto_confirm_argv()` =
  `check_photos pending --apply --max-minutes 15`.
- mainfetch parsers: `fetch` ×3 (tempdir; episodes + --fetchExtras + --manual; bare) · `locate` ×3 (incl.
  --probe-direct) · `check --run X --limit 5 --max-minutes 15 --pace 1`.
- main.py's REAL dispatcher tail (compiled alone, like smoke `_c25_dispatcher`): fetch (tempdir; --manual),
  fetch_restore (tempdir; episodes + --fetchExtras + --manual), verify_restore (with / without tempdir),
  fetch_cleanup (dry; --apply; `--bogus` → exit 1 + `❌ Usage: fetch_cleanup [--apply]`), map_photos ×2,
  check_photos ×3, resolve_photos ×2, mvmeta_upgrade ×2, restore tempdir, set_search — each reached its cmd_* with
  the documented arguments.
- the real `cmd_dispatch_fetch` builds `python mainfetch.py fetch <id> tempdir D:\MV_fetch --manual` (Popen
  recorded, merge stubbed).
- `tools/gp_inventory.py` argparse ×8 (--port per account, all --status, --status, --mode dom, --attach,
  --phase details --max 3) · the OPERATIONS_QA one-liner `gpcapture.dedup_key(<40 hex>)` → 27 chars.

Smoke gate: `python -m pytest tests/smoke -q` → 102 passed, 1 pre-existing warning, 74.7 s (slow: the
orchestrator's live R2 checks were running on the machine).

## Log
- 2026-10-20 dispatched; read-in done (see Sources). Resumed after a usage-limit pause on the coordinator's word;
  nothing had been written yet. Coordinator note: R2 M4–M9 / M18 run live from the main folder — keep replays offline.
