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
**Step 9 (map_photos) dispatched 2026-10-01 to executor-fable** in the main folder. Steps 0–8 done (Step 8 = `bd78a7d`).
After Step 9: 🚦 R1 (real-library mapping dry-run, user reads).
Open user items: device `HT74D0202839` is attached but not in DEVICE_ALIASES (movies phone `FA69H0300200` not
attached) → R1 would show ~1,559 objects `unchecked`; new bug found by the 🚦 RH test fetch 2026-10-01 (routing to
'movies' worked; Selenium attached to Chrome 154's Gemini panel webview `gemini.google.com/glic` instead of the
photos.google.com tab → 0 results; item verified present by a read-only probe) → IMP-C29 decision pending.
Parallel: IMP-C28 hotfix (`fix/imp_c28_push_parts_substring`, worktree MediaVault-c28) running; user approved fix.

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
| 8 | device cross-check | done | executor-fable (harness attribution Opus 5.5 = fable fallback for 8.a–8.c; session env claude-fable-5-1) | 8.a ca038b5 · 8.b b55b278 · 8.c 3bebc60 · feat: this step's final commit | full 1879 · smoke 82 · test_device_inventory 114 · disarm/mutation probes 23/23 (scratch copy) · live read-only check 2 phones | main.device_inventory(aliases) + classify_device_objects(library, inventory, objects=None), return shapes in their docstrings; 4th verdict `unchecked` (a configured phone not listed in full → Step 9 must never call such an object missing); a same-named file of other size never counts on_device; Step 13c must NOT widen the read-only allowlist `_dev_argv_readonly` (its pull/push/mv needs its own --apply-gated path); LIVE: HT74D0202839 attached but not in DEVICE_ALIASES, `movies` FA69H0300200 not attached → complete=False until the user decides (see operations log) |
| 9 | map_photos | in_progress | | | | |
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
| 2026-10-01 | Step 8 read-only live sanity check (scratch script: main.device_inventory + classify_device_objects over the real library; counts only; nothing written) | DEVICE_ALIASES as configured: movies FA69H0300200 not-connected · series FA75V0303405 ok, 431 files (420 sidecars, 11 media, 0 stat errors) · others skipped (placeholder) · 1 attached phone NOT in DEVICE_ALIASES: HT74D0202839 (28 files: 26 sidecars, 2 media) → complete=False; 2,108 uploaded objects: on_device 10 · freed 539 · absent 0 · unchecked 1,559. Both attached phones (ad-hoc aliases): complete → on_device 12 · freed 624 · absent 1,472 · unchecked 0; 12/12 copies size-agree; 32 adb calls (devices 1 · find 2 · stat 29), ~5–10 s | n/a — read-only, re-runnable |

## In-progress sub-state
`Step 9 — dispatched 2026-10-01 to executor-fable (fable) · resumed after a usage-limit pause (nothing of Step 9 was
on disk; the read-only calibration was redone) · sub-tasks per PLAN Step 9 details 1–10 plus 9.0 (DECISIONS §1d):
[x] 9.0 extra movies phone HT74D0202839 in the read-only device cross-check (main.DEVICE_CHECK_EXTRA_SERIALS +
device_check_aliases(); push aliases unchanged; test_device_inventory 131, smoke 82) (wip 5f495b6) ·
[x] 9.1 scaffold + freshness gate (main.py section "THE MAPPER", CLI `map_photos`, parse_map_photos_args, scope /
--account by routing, inventory via tools.gp_inventory.inventory_state, stale-inventory + re-crawl command, run dir
with mapping.jsonl + summary.txt; interim ladder = exact name in the default account; test_map_photos 27, smoke 82)
(wip 096ed70) · [x] 9.2 match ladder (matching groups: a split's chunks decided together; default account exact →
normalized → another account = wrong-account (copies of the same bytes recorded, other bytes noted; answers from a
stale inventory refused; "in no account" needs every crawled inventory fresh + fully detailed) → `[short_id]`-tag
probable; possibly-deleted items never match (reported); override coverage covers/none/differs + report lines;
test_map_photos 41, smoke 82) (wip 1d5cc4c) · [x] 9.3 sha1↔dedupKey cross-check (content key = trusted block, else
capture; found by its bytes in any account — proof dedupkey, name_match exact / normalized / `content` (another
name → --repair names); same-named items with other bytes → `conflict`, never applied; content proof outranks a
same-named item; same bytes twice → one item + alternates; no probable for content-keyed objects; test_map_photos
48, smoke 82) (wip 044863d) · [x] 9.4 duplicates/re-split sets (same bytes → one item + alternates; a split's
chunks grouped by taken second ONLY when a chunk name has different-bytes duplicates: the newest complete upload
when no newer upload exists, the other uploads' items alternates; a newer incomplete upload or no complete one →
ambiguous; a content-proven sibling pins the upload (other-second items → conflict); unique-name splits map per
chunk (250/250 real sets sit on one second); test_map_photos 57, smoke 82) (wip d0f4ad5) · [x] 9.5 C27 detectors +
repairs ((a) split_info lost: chunks of the whole's name in GP → inconsistent + a repair plan PROVEN only from one
upload of chunks 1..N, no holder, exact push names and every local checksums/<chunk>.sha256 → _mp_write_split_info
(fingerprint-guarded, pinned to the unchanged unsplit node); (b) old untagged name → inconsistent + --repair names
(gphotos_apply gained `names=` — same single writer + OD-2 guard; a name two objects share is ambiguous); (e)
partial / non-original upload → inconsistent; (c)/(d) read-only device check (device_check_aliases incl. the extra
movies phone) → awaiting-backup / missing-after-backup / missing-everywhere / device-check-incomplete, partial_set;
device block + LOUD block in the report; test_map_photos 75, engine/device/schema 195, smoke 82) (wip 2b3313a) ·
[x] 9.6 unmatched report (`map_photos all` without --account, fresh accounts only: superseded-split / duplicate /
cross-account-copy / mediavault-unknown / non-mediavault / possibly-deleted / errored → unmatched_<account>.csv
(UTF-8 BOM, CRLF) + one report line; never touched; test_map_photos 78, smoke 82) (wip 4136cbf) · [x] 9.7 outputs
(each unresolved row's P-18 reason incl. partial-set; failed records validated by mvcommon's normalizer;
resolutions_todo.csv = mvcommon._failed_row + write_failed_csv (the failed_objects.csv columns, status = the precise
verdict, expected_taken_local from the capture); inconsistencies.csv; an edited todo in a reused --out is kept →
.new.csv; "To resolve" + Outputs lines; test_map_photos 83, smoke 82) (wip fd5c4d5) · [x] 9.8 --resolutions
(read_failed_csv; each row pinned by manual_id + sha256 (else label), alias-resolved, in scope; url → learned
{photo_id, proof manual, source manual} + hint; datetime → hint; search → learned search_override; not_uploaded →
LOUD "you say not uploaded" + inconsistencies.csv; skip stays; bad rows listed, never imported; NEW mvcommon hints
store hints.jsonl (append_hint / read_hints / _normalize_hint) for Step 13's `check_photos hinted`; objects decided
earlier (manual photo_id / search_override in the block, or a hint) leave resolutions_todo.csv; a dry run records
nothing; test_map_photos 98, primitives 215, smoke 82) (wip commit "step 9.8") · [ ] 9.9 --apply via learned
sidecar + apply engine ← next · [ ] 9.10 --devices-only · then tests, disarm probes, full + smoke`
`resumed 2026-10-01 by executor-fable (usage-limit pause #2): uncommitted 9.1 changes KEPT (main.py,
tests/test_map_photos.py) — the diff was coherent; 26/27 green, the 1 failure a test-fixture expectation (fixed)`
`resumed 2026-10-01 by executor-fable (usage-limit pause #3): uncommitted 9.4 changes KEPT (main.py,
tests/test_map_photos.py) — coherent; 57/57 green, smoke 82`
`resumed 2026-10-01 by executor-fable (usage-limit pause #4): uncommitted 9.8 changes KEPT (main.py, mvcommon.py,
tests/test_map_photos.py) — coherent; map 98 + primitives 215 + smoke 82 green`
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
