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
- Last updated: 2026-09-28 20:00 (working location moved to the main folder; Step 3 SHA back-filled; decision cards
  listed)

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
**Step 8 (read-only device cross-check) dispatched 2026-09-29 to executor-fable** in the main folder. Steps 0–7 are
done (Step 6 = `856fd0c`). After Step 8: Step 9 (map_photos) → 🚦 R1 (real-library mapping dry-run, user reads).
Open user items: 🚦 RH X-Files test fetch (go-ahead pending); IMP-C28 (`_parts` path treated as chunk in cmd_push)
registration/fix approval.

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
| 1 | sync with main (after #60 / after H) | done (2×) | orchestrator (Opus 5.5) | 3029f6f (ac563a0/#60) · 5ec9ef0 (494fbec/#61) | full 1718 · smoke 82 | #61 merge resolved tierC/PRIORITY/graph/conftest keeping both sides; C25→C26 edge added; graph 137/78/0/0 |
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
| 8 | device cross-check | in_progress | | | | |
| 9 | map_photos | pending | | | | |
| R1 | real mapping dry-run (user) | pending | user | | | |
| 10 | fetch plumbing + routing | pending | | | | |
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

## In-progress sub-state
`Step 8 — dispatched 2026-09-29 to executor-fable (fable) · sub-tasks: [x] 8.a device_inventory(aliases) helper
(read-only adb find + batched stat; placeholder serials skipped; offline/unauthorized reported) + its tests in
tests/test_device_inventory.py (83 passed, smoke 82) (wip ca038b5) · [x] 8.b classify_device_objects: on_device /
freed / absent / unchecked (all four categories, chunks, holders, extras; size from block > capture > tech_spec for
whole objects) + its tests (113 passed, smoke 82) (wip b55b278) · [x] 8.c round-trip drift pin through the real
cmd_push / push_title_extras on the mock device + swallowed-violation recorder + disarm probes 23/23 (scratch copy) +
full 1879 · smoke 82 (wip commit "step 8.c") · final: PROGRESS row 8 + STATUS entry + feat commit ← next`
`resumed 2026-10-01 by fable (usage-limit interruption during 8.b): uncommitted 8.b changes KEPT (main.py,
tests/test_device_inventory.py) — checkpoint rule 3: the diff was coherent (classifier section + its tests, complete)
and its targeted tests passed (113) before it was committed`
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
