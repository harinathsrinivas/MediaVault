# IMP-C25 — Execution Journal (resumable, cross-session / cross-account)
- Task: IMP-C25 (+C26 fix, C27 tooling) — exact Google Photos item fetch. Framework: v2.
- Branch: feature/imp_c25_fetch_exact_gp_item · Worktree: C:\Users\harin\PycharmProjects\MediaVault-c25
- Plan: docs/feature-fetch-datetime/PLAN.md · Decisions: DECISIONS.md · Python: C:\Users\harin\PycharmProjects\MediaVault\.venv\Scripts\python.exe
- Baseline: full 971 passed · smoke 81 passed (70635cb, 2026-09-25) — **re-confirmed 2026-09-28 at `d29bb03`: full 971
  passed (140 s) · smoke 81 passed (14 s), idle machine, 0 failures** (one pre-existing StarletteDeprecationWarning each)
- Last updated: 2026-09-28 (Step 0b/0c done by executor-opus; G0 partial)

## ▶ NEXT ACTION
Step 3 (mvcommon identity primitives + hermetic redirects) — dispatch executor-fable. Parallel: PR #61 (Step H) awaits user approval; after merge → Step 1 re-merge main + 🚦 RH. P-12 question before Step 11.

## Resume protocol
1. Open a session in the worktree; `git status` (do not assume clean; never add -A).
2. Read PLAN.md + DECISIONS.md + this file.
3. `git log --oneline` must match the SHAs below; trust git on disagreement.
4. Re-probe fable (.claude/MODEL_WATERFALL.md); record the model used per step.
5. Continue at ▶ NEXT ACTION; never re-run a `done` step; commit this file with every step.
6. If `## In-progress sub-state` names a running step: apply PLAN "In-step checkpoints" rule 3 (keep or discard the
   interrupted sub-task's uncommitted changes, record it) and re-dispatch that step from the named sub-task.
7. If the operations log shows a live command without a result line: re-run its recorded resume command.

## Step status
| Step | Description | Status | Model used | Completing SHA | Tests | Notes |
|---|---|---|---|---|---|---|
| 0 | plan (0a 16e292c/5fd4843 + 0a′ d29bb03) + journal + IMP registration | done | opus (executor-opus, claude-opus-5-5) | 16e292c · 5fd4843 · d29bb03 · 0b 20a10ae · 0c c2085e3 | full 971 · smoke 81 (baseline re-run 2026-09-28) | 0a = 16e292c (rev-1 draft) + 5fd4843 (rev-1 final pass); 0a′ = d29bb03 (revisions 2–6); root PLAN.md == tracked copy (cmp) · 0b = PROGRESS + DECISIONS · 0c = IMP-C25 + IMP-C27 registered (tier C, PRIORITY Band 0/1, graph TASKS 136 · EDGES 76 · badTasks 0 · badEdges 0); C5/A8/B10 → in_progress (absorbed); B4 note; C26 comes via Step H; both NEXT pointers untouched (OD-16) |
| G0 | rulings recorded (2026-09-25) + clarifications | partial (P-12 pending before Step 11) | user | 20a10ae | | plan approved 2026-09-28 · OD-1: H confirmed, PR STOPS for user approval (decision card 2026-09-28; supersedes the auto-merge wording) · q2 OD-4 ANSWERED 2026-09-28: "Yes, with --apply only" — mvmeta_upgrade may rewrite phone sidecars with --apply only (dry-run default) · q3 P-12 download design PENDING — ask before Step 11 |
| 1 | sync with main (after #60 / after H) | done | orchestrator (Opus 5.5) | 3029f6f | full 971 · smoke 81 | merged origin/main ac563a0 (PR #60) — clean; two-dot diff shows only feature docs; repeat after Step H merges |
| H | C26 hotfix PR (worktree MediaVault-c26) | done — PR #61 open, awaiting user approval (Checkpoint 1) | fable (executor-fable) | 2893d98 (branch fix/imp_c26_fetch_account_routing) | full 1003 · smoke 82 · disarm 3/3 | +switch guard (close prev Chrome, port 9222 free) beyond plan text; RH after merge; then Step 1 re-merges main |
| RH | user fills fetch_account_overrides + tests | pending | user | | | after H merges |
| 2 | gpweb + RPC parsers + fake Google Photos | done | fable (executor-fable, claude-fable-5-1) | eea6759 | full 1215 · smoke 81 · crawler golden byte-identical · disarm/mutation probes 8/8 | new gpweb.py + tests/gphotos_fake.py + tests/test_gpweb.py; tools/gp_inventory.py delegates; conftest `fake_photos`; VrseUb original_quality not pre-resolved → None (read EWgK9e); dedup_key re-export is a lazy delegate (import-cycle guard for Step 3) |
| 2b | gprpc read-only RPC client | done | fable (executor-fable, claude-fable-5-1) | 18cbb91 | full 1329 · smoke 81 · test_gprpc 114 · disarm/mutation probes 19/19 | new gprpc.py + tests/test_gprpc.py; consumers catch `RpcUnavailable` (base of RpcShapeError/RpcServerError) for the UI fallback, never RpcRefused; pacing + circuit breaker are per browser session (driver); tests that drive gprpc must patch its clock (`clock` fixture in test_gprpc.py) |
| 3 | mvcommon identity primitives + failed list | done — uncommitted, awaiting the orchestrator's commit | fable (executor-fable, claude-fable-5-1) | | full 1544 · smoke 81 · test_gphotos_primitives 215 · disarm/mutation probes 17/17 | mvcommon section "GOOGLE PHOTOS IDENTITY" + conftest autouse `_hermetic_gphotos_state`; vocab extensions flagged (s0_hash / s3u_user_query stages, learned alternates / quality flags / search_override / via, failed `candidates`, CSV `sha256` column); Step 5 must write extras captures in the same doc shape; producers of proof sha256 set dedup_key = the verified bytes' key |
| 4 | ENTRY_TYPE_KEYS + guard | pending | | | | fallback none |
| 5 | capture v2 | pending | | | | |
| 6 | apply engine | pending | | | | |
| 7 | crawler productization | pending | | | | |
| R0 | prerequisites (user) | pending | user | | | others blocked until profile |
| 8 | device cross-check | pending | | | | |
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
(Step 2's sub-state is closed — the step was committed as `eea6759`; Step 2b's as `18cbb91`.)
`Step 3 — dispatched 2026-09-28 to executor-fable (fable, claude-fable-5-1) · single-executor, dispatch says DO NOT
COMMIT (no WIP commits; this line is kept on disk only) · resumed once after a usage-limit reset (no edits existed) ·
sub-tasks: [x] 3.1 mvcommon: constants + block helpers + category/home_account (a, b) · [x] 3.2 mvcommon: object
inventory + remote_upload_name + read_capture (c, d, e) · [x] 3.3 mvcommon: learned store + reducer + apply_learned +
failed list + CSV + unverified_objects (f) · [x] 3.4 conftest autouse redirect (g) — 3.1–3.4 written, import-checked,
existing capture/schema/mvcommon tests green · [x] 3.5 tests/test_gphotos_primitives.py (215 passed, final) ·
[x] 3.6 disarm probes 17/17 caught (10 plugin-injected, re-run on the final code; 7 source-edit, reverted +
sha256-verified) · [x] 3.7 self-review (2 robustness fixes + 1 export dedupe fix, each with a test) + full suite
1544 + smoke 81 + STATUS → STEP COMPLETE: the uncommitted files (mvcommon.py, tests/conftest.py,
tests/test_gphotos_primitives.py, this file) are the finished, verified step — commit them, do not discard ·
baseline note: a full run at 18cbb91 (before any edit) hit a load-dependent flake in
tests/test_gpweb.py::test_fake_delayed_download_completes_in_the_background (passes 5/5 alone)`
Template — when a step is running (Execution protocol "In-step checkpoints"):
`Step N — dispatched <time> to <model> · sub-tasks: [x] N.a <what> (wip SHA) · [ ] N.b <what> ← next · [ ] N.c …`
plus, after a crash, one line `resumed <time> by <model>: uncommitted N.b changes kept|discarded (<files>)`.

## Blockers / human gates
- G0 clarification left: q3 P-12 download design (before Step 11) · Step H PR 🚦 Checkpoint 1 (user approves its
  merge) · RH override list · R0 prerequisites · R1/R2/R3/R4 real-data gates · G12 candidate pick · Step 17
  NEXT-pointer question (OD-16) · Checkpoint 1 · Checkpoint 2
- (G0 q2 OD-4 was answered 2026-09-28 — "Yes, with --apply only" — and is no longer a blocker.)
