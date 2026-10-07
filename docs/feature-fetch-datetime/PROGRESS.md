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
- Last updated: 2026-10-05 (Step 11 done → Step 12 next; "At a glance" below is older — the Step status table is
  authoritative)

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
**Steps 0–11 + 9b done.** Step 11 (2026-10-05, the `feat(imp-c25): Step 11 …` commit): download hygiene a–i — full 2421 ·
smoke 82 · disarm 32/32 · live `fetch_cleanup` dry run 0 leftovers. Next: **Step 12** (locator engine, 3 candidates →
🚦 G12) on Step 11's contracts (STATUS.md Step 11 + the docstrings of `_prepare_downloads`, `harvest_downloads`,
`_rearm_downloads`, `_run_locator_phase`, `FetchJournal`). Open for the user (from the Step 11 report): the CDP download
redirect is unverified live until 🚦 R2 §M6–M7, yet this folder's fetches (CLI and the running `main.py web`) already
use it; `tools/warm_profiles.py` warming several profiles attaches to the first one's still-open Chrome (pre-existing,
a follow-up IMP candidate).
Step 10 (2026-10-04, `0690546`): full 2293 · smoke 82 · disarm 29/29, incl. IMP-C34/C35/C36.
Step 9b (2026-10-04, `3fd911a`, merged here): map_photos verdicts need no phone (user R1 ruling, DECISIONS §1e) —
`not-backed-up-yet` (pushed after / within `MAP_RECENT_DAYS = 7` before the routed account's crawl) vs `possible-loss`;
`--devices` opt-in; full 2302 · smoke 82 · disarm 63/63. Step 1 re-sync #5 (main `bfb9833`) done; all C25-found
hotfixes are on main (#61–#66). 🚦 RH PASSED. 🚦 R1 DONE (2026-10-03): read by the user; re-run with 9b at
`D:\MediaVault_gp_inventory\map_r1b_20261003_1714` — mapped 2,020 · wrong-account 31 (covered) · inconsistent 4 ·
not-backed-up-yet 23 · possible-loss 15 · others stale 35 · apply preview 2,051 / 0 conflicts. No apply until 🚦 R3.
Then Step 12 (3 candidates → 🚦 G12), 13, 13b, 13c, 14, 15 → 🚦 R2/R3 → 16–18.
The user archives from this folder (5 movies on 10-03/10-04 + Dune) — check for a running `main.py` before any
fast-forward/merge here; the user's archive `mov-en-2024-duneparttwo` stays hands-off until after the plan.

## Resume protocol
1. Open a session in the main folder `C:\Users\harin\PycharmProjects\MediaVault` (branch
   feature/imp_c25_fetch_exact_gp_item — not a worktree; relayed instruction 2026-09-28, confirmation pending —
   DECISIONS.md §1b W3); `git status` (do not assume clean; never add -A).
2. Read PLAN.md + DECISIONS.md + this file.
3. `git log --oneline` must match the SHAs below; trust git on disagreement.
4. Re-probe fable (.claude/MODEL_WATERFALL.md); record the model used per step.
5. Continue at ▶ NEXT ACTION; never re-run a `done` step; commit this file with every step.
6. If `## In-progress sub-state
`Step 12 — 3 candidates dispatched 2026-10-05 (executor-fable each); worktrees C:/Users/harin/PycharmProjects/MediaVault-c25-cand/step-12/{A,B,C} on branches feature/imp_c25_fetch_exact_gp_item__cand_{a,b,c} from 8968f26 · A UI-first (no RPC for locating) · B RPC-first · C hybrid arbitration · each: WIP commits on its own branch + .candidates/c25-step-12/<X>/CANDIDATE_STATUS.md + CRITIQUE.md · then judge-v2 → .candidates/c25-step-12/DECISION.md → 🚦 G12 (the user picks; optional live locate per worktree, §M4)`
`Live check 2026-10-05 (SV S02E02 → The Expanse S01E02 twice) — diagnosed by the Step 10 executor with read-only live probes (TV account, nothing clicked): NOT a Step 10 regression. Google Photos now shows a "Most relevant to your search" strip ABOVE the date-grouped results; the legacy click takes the first tile in DOM order = the strip's first, a loose match. Untagged name: strip [The Expanse] only, no result at all; tagged name: strip [The Expanse, Mr. Robot chunk, SV] then the "Sat, Feb 5, 2022" group [SV]. The pre-Step-10 read (3 s, no filter) picks the same tile; the page is stable 3–12 s. Fix 1 `74d662d`: wrong item downloaded (deleted) + "not a broken transfer" instead of the lost-download note; no IMP-C34 retry; the object ends on the failed list (wrong-item); a re-downloaded wrong file under the same name is deleted in its round (Step 11's handled-set kept it until the entry ended). Fix 2 `8dacbad`: the legacy click skips the strip (gpweb SEARCH_STATE_JS `relevant` / TIMELINE_LINKS_JS via its container's data-disable-shift-select; a strip-only page is a miss after 4 looks); trigger_download digest unchanged, wait digest re-pinned. Live re-check (read-only, 3 searches): the fixed read picks SV for the tagged name, the untagged name reads Not found. Full 2428 · smoke 82 · disarm 11/11 (scratch copy). Step 12 candidates (cut from 8968f26) must merge both fixes; they share gpweb §9 and harvest_downloads.`
`Step 12 candidate status (orchestrator, 2026-10-08): A COMPLETE d8dd71f (live 15/15 objects in 6 probes incl. the strip case and Death Note 01; full 2493 · smoke 82 · disarm 20/20) · B COMPLETE 2d785c4 — reopened for one check: Death Note 01 is findable only by its bare short id (A's live finding) → B verifies its RPC search live and adds the query if needed · C in progress at 12C.d1b (fixes merged; told about the short-id finding). Judge only after all three pass the orchestrator's independent gate check (DECISIONS: completion gate).`
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
