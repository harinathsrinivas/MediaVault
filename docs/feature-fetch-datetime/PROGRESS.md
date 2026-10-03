# IMP-C25 — Execution Journal (resumable, cross-session / cross-account)
- Task: IMP-C25 (+C26 fix, C27 tooling) — exact Google Photos item fetch. Framework: v2.
- Branch: feature/imp_c25_fetch_exact_gp_item · **Working folder: C:\Users\harin\PycharmProjects\MediaVault** (the main
  folder; the branch is checked out HERE since 2026-09-28 ~20:00). This follows a RELAYED instruction (DECISIONS.md
  §1b W3); first-hand confirmation is pending on the "Work folder" card. The old worktree `MediaVault-c25` is detached
  at 31e8214, clean and idle — don't dispatch to it; remove it once the card says "keep". `MediaVault-c26` holds only
  Step H's branch until PR #61 merges.
- Plan: docs/feature-fetch-datetime/PLAN.md (root copy `PLAN.md`, gitignored, byte-identical) · Decisions: DECISIONS.md ·
  Execution log: root `STATUS.md` (gitignored, shared across tasks — C25 entries at its end) · Python:
  C:\Users\harin\PycharmProjects\MediaVault\.venv\Scripts\python.exe
- Baseline: full 971 passed · smoke 81 passed (70635cb, 2026-09-25) — **re-confirmed 2026-09-28 at `d29bb03`: full 971
  passed (140 s) · smoke 81 passed (14 s), idle machine, 0 failures** (one pre-existing StarletteDeprecationWarning each).
  Current gate (Step 3, `31e8214`): full 1544 · smoke 81 — re-confirmed in the main folder 2026-09-28 20:04 (full
  1544 passed in 172 s · smoke 81 passed in 30 s).
- Last updated: 2026-10-02 (Step 9 done → Step 1 re-sync with main, then 🚦 R1; "At a glance" below is older — the
  Step status table is authoritative)

## At a glance
- **Size:** 30 tracked items = **23 agent steps** (0, 1, H, 2, 2b, 3–12, 13, 13b, 13c, 14–18) + **7 user gates**
  (G0, RH, R0, R1, R2, R3, R4). Stop-points inside steps: the Step H PR approval, 🚦 G12 candidate pick, the Step 17
  NEXT-pointer question, Checkpoints 1 and 2.
- **Done:** Steps 0, 1, 2, 2b, 3 (5 of 23) · Step 4 in progress (running in the old worktree — see NEXT ACTION) ·
  Step H built and green — PR #61 waits for your approval · G0 partial (only P-12 open).
- **Remaining:** 17 agent steps (4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 13b, 13c, 14, 15, 16, 17, 18) + Step 1 again after
  PR #61 merges · user gates RH, R0, R1, R2, R3, R4 + the rest of G0.
- **Roadmap:** foundations 4 → 5 → 6 → 7 → 8 · backfill 9 → 🚦 R1 (your dry-run review) · fetch rebuild 10 → 11
  (needs P-12) → 12 (3 candidates, 🚦 G12 your pick) → 13 → 13b → 13c → 14 · hardening + live 15 → 🚦 R2 → 🚦 R3 → 16
  → 🚦 R4 · wrap-up 17 → 18 (PR → Checkpoint 1).
- **Decisions waiting for you** (asked in chat as decision cards — the answer you pick or type is recorded verbatim
  in DECISIONS.md): (1) Work folder — keep the C25 branch checked out in your main folder (and how you archive
  meanwhile) or revert · (2) merge PR #61? · (3) RH — fill `fetch_account_overrides` for you or paste it yourself? ·
  (4) P-12 download design (before Step 11). Later: PR #60 branch archive (Checkpoint 2, after card 1) · Others
  account (R0) · Step 12 candidate isolation (before Step 12).

## ▶ NEXT ACTION
**Steps 0–9 done; Step 1 re-sync #5 done (2026-10-03: main `bfb9833` = PRs #65 IMP-C29 + #66 IMP-C33 merged here;
full 2177 · smoke 82).** All hotfixes found during C25 are merged to main: #61 C26, #62 C28, #63 C30/C31, #64 C32,
#65 C29, #66 C33. 🚦 RH PASSED 2026-10-03 (X-Files S02E05 from the movies account: `✅ MOVED`, SHA-256 = library hash,
twice incl. a cold Chrome start; test copies deleted per the user).
Next, in parallel:
1. **Step 10** (fetch plumbing) — unblocked. Carries three extra requirements by user ruling 2026-10-03 (DECISIONS
   §1e): retry a broken transfer once; never click the home timeline (poll for real results / locate by photo_id);
   unbuffered child output in `cmd_dispatch_fetch` (`PYTHONUNBUFFERED=1`). Registered as IMP-C34/C35/C36.
2. **🚦 R1 prerequisites** — re-crawl movies / tv / anime one at a time (`python tools/gp_inventory.py <acct> --port
   9223|9225|9224`, then `… all --status`); the user attaches all three phones (`FA69H0300200`, `HT74D0202839`,
   `FA75V0303405`); then `python main.py map_photos all --devices-only` and `python main.py map_photos all`
   (dry-run) → the user reads the report (STATUS.md Step 9 "R1 reader guide"). No push to an account between its
   crawl and the mapping; map within 24 h of the crawl.
User's own archive `mov-en-2024-duneparttwo` (series phone = TV account) — hands-off; revisit after the plan.

## Resume protocol
1. Open a session in the main folder `C:\Users\harin\PycharmProjects\MediaVault` (branch
   feature/imp_c25_fetch_exact_gp_item — not a worktree; relayed instruction 2026-09-28, confirmation pending —
   DECISIONS.md §1b W3); `git status` (do not assume clean; never add -A).
2. Read PLAN.md + DECISIONS.md + this file.
3. `git log --oneline` must match the SHAs below; trust git on disagreement.
4. Re-probe fable (.claude/MODEL_WATERFALL.md); record the model used per step.
5. Continue at ▶ NEXT ACTION; never re-run a `done` step; commit this file with every step.
6. If `## In-progress sub-state` names a running step: apply PLAN "In-step checkpoints" rule 3 (keep or discard the
   interrupted sub-task's uncommitted changes, record it) and re-dispatch that step from the named sub-task.
7. If the operations log shows a live command without a result line: re-run its recorded resume command.
8. Ask the user every question as an in-chat decision card (options + a free-text answer); the picked or typed
   answer IS the decision — record it verbatim in DECISIONS.md. Never ask the user to open a .md file to answer.
   Keep independent steps running while a card is open (user, 2026-09-28 — DECISIONS.md §1b).
9. Keep root `PLAN.md` byte-identical to docs/feature-fetch-datetime/PLAN.md after any plan edit; executors append
   their STATUS entries to the root `STATUS.md` of this folder.

## Step status
| Step | Description | Status | Model used | Completing SHA | Tests | Notes |
|---|---|---|---|---|---|---|
| 0 | plan (0a 16e292c/5fd4843 + 0a′ d29bb03) + journal + IMP registration | done | opus (executor-opus, claude-opus-5-5) | 16e292c · 5fd4843 · d29bb03 · 0b 20a10ae · 0c c2085e3 | full 971 · smoke 81 (baseline re-run 2026-09-28) | 0a = 16e292c (rev-1 draft) + 5fd4843 (rev-1 final pass); 0a′ = d29bb03 (revisions 2–6); root PLAN.md == tracked copy (cmp) · 0b = PROGRESS + DECISIONS · 0c = IMP-C25 + IMP-C27 registered (tier C, PRIORITY Band 0/1, graph TASKS 136 · EDGES 76 · badTasks 0 · badEdges 0); C5/A8/B10 → in_progress (absorbed); B4 note; C26 comes via Step H; both NEXT pointers untouched (OD-16) |
| G0 | rulings recorded (2026-09-25) + clarifications | partial (P-12 pending before Step 11) | user | 20a10ae | | plan approved 2026-09-28 · OD-1: H confirmed, PR STOPS for user approval (decision card 2026-09-28; supersedes the auto-merge wording) · q2 OD-4 ANSWERED 2026-09-28: "Yes, with --apply only" — mvmeta_upgrade may rewrite phone sidecars with --apply only (dry-run default) · q3 P-12 download design PENDING — ask before Step 11 |
| 1 | sync with main (after #60 / H / #62+#63 / #64 / #65+#66) | done (5×) | orchestrator (Opus 5.5) | 3029f6f (ac563a0/#60) · 5ec9ef0 (494fbec/#61) · re-sync #3 = this merge commit (e35b624/#62+#63) | full 2068 · smoke 82 (re-sync #3) | #61 merge resolved tierC/PRIORITY/graph/conftest keeping both sides; C25→C26 edge added; graph 137/78/0/0 |
| H | C26 hotfix PR (worktree MediaVault-c26) | done — PR #61 open, awaiting user approval (Checkpoint 1) | fable (executor-fable) | 2893d98 (branch fix/imp_c26_fetch_account_routing) | full 1003 · smoke 82 · disarm 3/3 | +switch guard (close prev Chrome, port 9222 free) beyond plan text; RH after merge; then Step 1 re-merges main |
| RH | user fills fetch_account_overrides + tests | pending | user | | | after H merges |
| 2 | gpweb + RPC parsers + fake Google Photos | done | fable (executor-fable, claude-fable-5-1) | eea6759 | full 1215 · smoke 81 · crawler golden byte-identical · disarm/mutation probes 8/8 | new gpweb.py + tests/gphotos_fake.py + tests/test_gpweb.py; tools/gp_inventory.py delegates; conftest `fake_photos`; VrseUb original_quality not pre-resolved → None (read EWgK9e); dedup_key re-export is a lazy delegate (import-cycle guard for Step 3) |
| 2b | gprpc read-only RPC client | done | fable (executor-fable, claude-fable-5-1) | 18cbb91 | full 1329 · smoke 81 · test_gprpc 114 · disarm/mutation probes 19/19 | new gprpc.py + tests/test_gprpc.py; consumers catch `RpcUnavailable` (base of RpcShapeError/RpcServerError) for the UI fallback, never RpcRefused; pacing + circuit breaker are per browser session (driver); tests that drive gprpc must patch its clock (`clock` fixture in test_gprpc.py) |
| 3 | mvcommon identity primitives + failed list | done | fable (executor-fable, claude-fable-5-1) | 31e8214 | full 1544 · smoke 81 · test_gphotos_primitives 215 · disarm/mutation probes 17/17 | mvcommon section "GOOGLE PHOTOS IDENTITY" + conftest autouse `_hermetic_gphotos_state`; vocab extensions flagged (s0_hash / s3u_user_query stages, learned alternates / quality flags / search_override / via, failed `candidates`, CSV `sha256` column); Step 5 must write extras captures in the same doc shape; producers of proof sha256 set dedup_key = the verified bytes' key |
| 4 | ENTRY_TYPE_KEYS + guard | done | | 04ad2d5 | | fallback none · bring its files here before committing (NEXT ACTION) |
| 5 | capture v2 | done | | ba2e799 | | |
| 6 | apply engine | done | fable (executor-fable) | 856fd0c | full 1765 · smoke 82 · disarm 29/29 | edited failed_objects.csv never overwritten (Step 9/14 regenerate after import); home_account stored only when learned; 3 attempts total |
| 7 | crawler productization | done | fable (executor-fable, claude-fable-5-1) | 46d0532 | full 1615 · smoke 81 · test_gp_inventory 88 (30 → 88) · disarm/mutation probes 31/31 | tools/gp_inventory.py rpc-first (lcxiM + EzkLib + EWgK9e via gprpc) with auto dom fallback, crawl_lock + progress-driven heartbeat (60 s; yields on takeover), profile pre-flight + landing check, run log `<account>.runs.jsonl` (start / seen / "enumerated" / end), offline reader `inventory_state()` (Steps 9/13) + `--status`; `mainfetch.profile_preflight(account)` → None or a message; reads the real tv/movies/anime files unchanged (checked on a copy; D:\ fingerprint unchanged); rpc upgrades dom records once (+size/quality/exact name); ONE crawl at a time (R0/§M1: run accounts sequentially) |
| R0 | prerequisites (user) | pending | user | | | others blocked until profile |
| 8 | device cross-check | done | executor-fable (harness attribution Opus 5.5 = fable fallback for 8.a–8.c; session env claude-fable-5-1) | 8.a ca038b5 · 8.b b55b278 · 8.c 3bebc60 · feat: this step's final commit | full 1879 · smoke 82 · test_device_inventory 114 · disarm/mutation probes 23/23 (scratch copy) · live read-only check 2 phones | main.device_inventory(aliases) + classify_device_objects(library, inventory, objects=None), return shapes in their docstrings; 4th verdict `unchecked` (a configured phone not listed in full → Step 9 must never call such an object missing); a same-named file of other size never counts on_device; Step 13c must NOT widen the read-only allowlist `_dev_argv_readonly` (its pull/push/mv needs its own --apply-gated path); LIVE: HT74D0202839 attached but not in DEVICE_ALIASES, `movies` FA69H0300200 not attached → complete=False until the user decides (see operations log) |
| 9 | map_photos | done | executor-fable (harness attribution Claude Opus 5.5 at all 16 WIP commits = fable fallback, as in Step 8; session env claude-fable-5-1; no MODEL-FALLBACK banner in the dispatch) | 9.0 5f495b6 · 9.1 096ed70 · 9.2 1d5cc4c · 9.3 044863d · 9.4 d0f4ad5 · 9.5 2b3313a · 9.6 4136cbf · 9.7 fd5c4d5 · 9.8 93b153f · 9.9 6c645ab · 9.10 7b2ce0c · review 77109ef · 97e6b3b · 93cdc51 · 7a41cad · abbf96e · feat: this step's final commit | full 2021 · smoke 82 · test_map_photos 125 · test_device_inventory 131 (114 → 131) · disarm/mutation probes 43/43 (scratch copy) · live read-only runs (operations log) | CLI `map_photos [all\|<id>\|<prefix>] [--account …] [--devices-only] [--resolutions <csv>] [--repair split-info\|names] [--accept-probable] [--apply] [--out <dir>]`, dry-run default; run dir `~/.mediavault/gphotos/map/<ts>/` or --out: summary.txt · mapping.jsonl · mapping.csv · inconsistencies.csv · resolutions_todo.csv · unmatched_<account>.csv (devices.csv for --devices-only). **R1 needs first:** a re-crawl of movies / tv / anime (the Sep-25 inventories have no run log → every object reads `stale-inventory`) and all three phones attached (FA69H0300200 + HT74D0202839 + FA75V0303405). DEVIATIONS / assumptions (full list: STATUS.md Step 9): files beyond the plan's list — mvcommon.py (NEW hints store hints.jsonl: append_hint / read_hints, the Step 13 `check_photos hinted` input) and tests/test_device_inventory.py (9.0, DECISIONS §1d: DEVICE_CHECK_EXTRA_SERIALS + device_check_aliases(); push aliases untouched) · extra output mapping.csv (the R1 reader's sheet) · gphotos_apply gained `names=` (--repair names; still the only writer of blocks, same OD-2 guard) · --repair split-info has its own fingerprint-guarded writer (split_info is not a gphotos block) and writes {is_split, total_chunks, chunks} without method/val · wrong-account is relative to the id prefix, each row says whether an mvconfig override covers it · freshness is strict (no verdict from a stale inventory; "in no account" needs every crawled account fresh and fully detailed) · a re-split maps to the newest complete upload only when no newer upload exists (else ambiguous) · `unchecked` → device-check-incomplete, never missing · learned map records carry no approach verdict · partial / non-original items are never applied · awaiting-backup rows are not put on the failed list · a dry run records nothing (pinned: library bytes + mtime, state dir) · no rollback journal, ENTRY_TYPE_KEYS literal unchanged (comment only) · mainfetch / gpweb / gpcapture / cmd_push untouched · main NOT merged (the orchestrator re-syncs #62/#63) |
| R1 | real mapping dry-run (user) | pending | user | | | |
| 10 | fetch plumbing + routing | in_progress | | | | |
| 11 | download hygiene | pending | | | | needs G0 q3 (P-12) answered first; "no" → STOP + re-dispatch planner-v2 |
| 12 | locator engine (3 candidates) | pending | | | | 🚦 G12 |
| 13 | check_photos | pending | | | | |
| 13b | auto-confirm on next push | pending | | | | |
| 13c | mvmeta_upgrade | pending | | | | --apply ENABLED (G0 q2 answered 2026-09-28: "Yes, with --apply only"); dry-run default |
| 14 | --manual + resolve_photos + resume | pending | | | | |
| 15 | smoke + pins + disarm probes | pending | | | | |
| R2 | live verification pack (user) | pending | user | | | |
| R3 | real-library apply (user) | pending | user | | | |
| 16 | docs (architect) | pending | | | | |
| R4 | full-library sweep + outlier/failed lists (user) | pending | user | | | at the very end (OD-7) |
| 17 | IMP bookkeeping + NEXT pointer question | pending | | | | OD-16 |
| 18 | final verification + PR | pending | | | | 🚦 Checkpoint 1 |

## Multi-candidate tracking (Step 12)
Worktrees C:\Users\harin\PycharmProjects\MediaVault-c25-cand\step-12\A|B|C · branches …__cand_a|b|c · DECISION .candidates/c25-step-12/DECISION.md · tags candidates/imp_c25/step-12/<x>-chosen|rejected · user pick: PENDING

## Operations log (crawls, dry-runs, applies — redacted counts only)
| When | Command | Result (counts) | Resume command |
|---|---|---|---|
| 2026-10-01 | Step 8 read-only live sanity check (scratch script: main.device_inventory + classify_device_objects over the real library; counts only; nothing written) | DEVICE_ALIASES as configured: movies FA69H0300200 not-connected · series FA75V0303405 ok, 431 files (420 sidecars, 11 media, 0 stat errors) · others skipped (placeholder) · 1 attached phone NOT in DEVICE_ALIASES: HT74D0202839 (28 files: 26 sidecars, 2 media) → complete=False; 2,108 uploaded objects: on_device 10 · freed 539 · absent 0 · unchecked 1,559. Both attached phones (ad-hoc aliases): complete → on_device 12 · freed 624 · absent 1,472 · unchecked 0; 12/12 copies size-agree; 32 adb calls (devices 1 · find 2 · stat 29), ~5–10 s | n/a — read-only, re-runnable |
| 2026-10-02 | Step 9 strict dry-run: `python main.py map_photos all --out <scratchpad>` (read-only; the real library sha256 + size + mtime identical before / after; no ~/.mediavault/gphotos created; D:\MediaVault_gp_inventory untouched) | 2,108 objects → stale-inventory 2,108 (anime 158 · movies 788 · others 35 · tv 1,127): movies / tv / anime "no complete enumeration yet" (796 / 1,114 / 158 ids, all from the Sep-25 dom crawl, no run log), others "not crawled yet"; no device check ran (nothing was "in no account") → unchecked 0; ~1–3 s; exit 0. Unknown scope / usage error → exit 1 | `python main.py map_photos all` after the re-crawl (NEXT ACTION) |
| 2026-10-02 | Step 9 `python main.py map_photos all --devices-only --out <scratchpad>` (read-only adb) | movies FA69H0300200 not-connected · series FA75V0303405 ok 431 files · others skipped (placeholder) · movies#2 HT74D0202839 ok 28 files (no longer "unconfigured") → INCOMPLETE; 2,108 objects: on_device 12 · freed 624 · absent 0 · unchecked 1,472; 12–32 s | re-run with FA69H0300200 attached |
| 2026-10-02 | Step 9 gate-BYPASSED validation (scratch script: main._mp_compute + the engine's dry-run preview over the real library and the Sep-25 inventories; movies / tv / anime treated as fresh; NOTHING written; counts only) — what R1 would have shown on the old inventories | mapped 1,926 · mapped-normalized 19 (movies; whitespace only) · wrong-account 31 (tv ids in movies; mvconfig override covers 31, none 0, differs 0) · inconsistent 4 (split-info-lost 3, all 3 provable from local checksums; untagged-name 1) · awaiting-backup 12 · missing-after-backup 29 · device-check-incomplete 52 · stale-inventory 35 (others) · probable / ambiguous / conflict 0. Re-split: 13 chunks in 3 entries (8 alternates); copies in another account on 18 rows; content-proven 1 of 72 captured. LOUD block 93 = 71 captured objects pushed after the Sep-25 crawl (12 entries) + 22 legacy (20 tv episodes + 2 last chunks = partial sets in 2 entries). Device check INCOMPLETE (movies not-connected): on_device 12 · freed 29 · unchecked 52. Unmatched: movies 42 (mediavault-unknown 13 · non-mediavault 29) · tv 18 (superseded-split 14 · duplicate 4) · anime 0. To resolve 85 (not-found 52 · missing-after-backup 29 · inconsistent 4). Apply preview: 1,976 objects would change / be confirmed (1,994 records; 0 conflicts, 0 orphans); 1,977 with --repair names | n/a — R1 replaces it |
- 2026-10-03 R1 re-crawl started by the orchestrator, one account at a time (rpc mode, read-only): movies → tv → anime. Resume command (re-run the same; progress is kept): `python tools/gp_inventory.py <movies|tv|anime> --port <9223|9225|9224>`; check with `python tools/gp_inventory.py all --status`. Logs: D:\MediaVault_gp_inventory\<account>.log + crawl_r1_<account>.out.

## In-progress sub-state
`Step 10 — dispatched 2026-10-03 to executor-fable (fable) · executing in dependency order e → b → d → c → g → a →
f → h → i · sub-tasks: [ ] 10.a cmd_fetch_route per-object account routing · [x] 10.b build_fetch_queue + locator
dict (parity oracle; FetchContext; ctx threaded through cmd_fetch_route — the C26/smoke fetch stubs gained `ctx=None`)
· [ ] 10.c harvest_downloads (on the merged C29 harvester) + learn-on-fetch · [ ] 10.d locator seam +
EXACT_LOCATOR_ENABLED ← next · [x] 10.e A8 deletions + build_extras_entries forwards gphotos/labels/title (wip 85866bc) ·
[ ] 10.f parse_fetch_options/--manual · [ ] 10.g IMP-C34 one retry of a lost download · [ ] 10.h IMP-C35 never click
the home timeline · [ ] 10.i IMP-C36 unbuffered child output · then disarm probes, full + smoke`
Pre-change oracles captured from HEAD b1cff7d before any edit (scratch harness, C29 fakes) — pasted into the Step 10
tests as frozen transcripts.
Known load-dependent flake: tests/test_gpweb.py::test_fake_delayed_download_completes_in_the_background — passes
alone; re-run it before treating a red full suite as a regression.
Template — when a step is running (Execution protocol "In-step checkpoints"):
`Step N — dispatched <time> to <model> · sub-tasks: [x] N.a <what> (wip SHA) · [ ] N.b <what> ← next · [ ] N.c …`
plus, after a crash, one line `resumed <time> by <model>: uncommitted N.b changes kept|discarded (<files>)`.

## Blockers / human gates
- G0 clarification left: q3 P-12 download design (before Step 11) · Step H PR 🚦 Checkpoint 1 (user approves its
  merge) · RH override list · R0 prerequisites · R1/R2/R3/R4 real-data gates · G12 candidate pick · Step 17
  NEXT-pointer question (OD-16) · Checkpoint 1 · Checkpoint 2
- Asked as decision cards 2026-09-28 (see "At a glance"): Work folder (confirm the relayed switch + archiving
  meanwhile, or revert) · PR #61 merge · RH mode · P-12. Later cards: PR #60 branch archive (Checkpoint 2; the branch is no longer checked out anywhere) ·
  Others account/profile (R0) · Step 12 candidate isolation (the plan uses temporary candidate worktrees outside this
  folder — confirm, given the 2026-09-28 "leave the work tree approach" instruction).
- Housekeeping for the orchestrator, once the Work-folder card says "keep":
  - Remove the old worktree: `git worktree remove C:\Users\harin\PycharmProjects\MediaVault-c25`. Do this only AFTER
    Step 4's files are brought here and restored there (so it is clean — no --force). Its STATUS.md entries for Steps
    2/2b/3 are already in this folder's STATUS.md; Step 4's entry still has to be carried over.
  - PLAN.md still names the worktree as the binding working location → a small planner-v2 revision.
  - If the card says "revert", follow the procedure in DECISIONS.md §1b W3 instead.
- (G0 q2 OD-4 was answered 2026-09-28 — "Yes, with --apply only" — and is no longer a blocker.)
