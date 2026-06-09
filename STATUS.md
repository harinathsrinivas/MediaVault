# Execution Log

Task: Auto-rollback for multi-step commands (feature/auto_rollback)
Branch: feature/auto_rollback (from origin/main @ a70d118; carried the three uncommitted plan docs over). Initial plan commit: 8b8896a.
Scope for THIS run: Steps 1 -> 2 -> 3 (flagship uncapped bake-off), then PAUSE at the user-decides gate. Steps 4 + merge + push are OUT OF SCOPE this run (D-2 user-selects the Step 3 winner first).
Note: the Task subagent tool is UNAVAILABLE in this run (same as the A1/C2/C8 runs), so the orchestrator executes every step directly with Read/Write/Edit/Bash and performs git operations inline (the git-agent's exact step sequences are followed by hand). STATUS.md is a per-step scratchpad artifact committed with the branch.
Baseline (pre-change, unmodified main.py): `pytest -q` -> 58 passed.
Model/effort policy (DECISIONS.md N-5): every logic-bearing step is opus/max; no downgrade; 3-candidate cap lifted for Step 3 (uncapped). No effort mismatches — the orchestrator runs at opus effort directly.

## Step 1 — [status: done] Behavior-baseline characterization + happy-path smoke harness
- Executor: orchestrator (direct; Task subagent tool unavailable). Model: opus, effort max (matches the step tag — no mismatch).
- Files changed: tests/conftest.py (extended, additive only), tests/test_baseline_happy_path.py (new).
- Outcome:
  - conftest.py additions (all additive; existing fixtures untouched):
    - `stub_tech_specs` — deterministic get_tech_specs stub so cmd_prep does not need pymediainfo on the fake fixture file.
    - `FailNthSubprocess` class + `fail_nth_subprocess` factory fixture — fail the Nth *matching* subprocess.run (e.g. Nth `push`) with CalledProcessError, composing over an optional inner run (e.g. mock_device) so path math / library writes still execute; records `.calls`. Also stubs mvcommon.time.sleep so retry() backoff is instant. (Extends the FakeAdb(fail_push_n=...) pattern rather than duplicating it.)
    - `fail_merge` factory fixture — patch merge_video_files to return False or raise, for the cmd_restore pre-PONR (reversible) merge-failure scenarios; records attempt count.
    - `ffmpeg_multichunk_mkv` fixture — ffmpeg-generated ~6 MB testsrc MKV for GENUINE-split tests; `pytest.skip`s cleanly when ffmpeg is absent or the invocation fails (testing-strategy §4/§11).
  - test_baseline_happy_path.py (the regression ORACLE for D-4): 7 tests snapshotting the happy-path post-state of all five target paths — cmd_prep (no-split movie: entry + uid + <short_id>.sha256, no parent), cmd_prep (season episode: parent season_map created + child linked + total_episodes), cmd_prep early-skip (uploaded -> True, ZERO artifacts), cmd_push (split via mock_device: chunk bytes land at final names, _parts cleaned, entry onboarded/uploaded), cmd_replace (fake_dummy: dummy live, status archived, no .dummy_tmp/.tobedeleted), cmd_restore split (stubbed merge: chunks merge to target + status restored_local + chunks deleted), cmd_restore standard (verified move + status restored_local + restore/ cleaned).
- Key decisions:
  - cmd_prep fixture files must exceed DUMMY_MAX_BYTES (200_000) or the @316-318 dummy-detection safety net early-skips them (hit during first run; fixed by writing DUMMY_MAX_BYTES+1 bytes). This is itself a captured fact for the Step 3 candidates: the early-skips create no artifacts and must never roll back.
  - cmd_push happy path uses `mock_device` (data round-trip) not FakeAdb — O-1 means push FAILURE is resume-message, so only the SUCCESS path is the oracle here; failure scenarios live in Step 3 candidate matrices.
  - cmd_restore split uses a stubbed merge_video_files (no real mkvmerge) — mirrors test_cmd_restore_quarantine's split-success test.
- Acceptance: `pytest tests/test_baseline_happy_path.py -q` -> 7 passed against UNMODIFIED main.py. Full `pytest -q` -> 58 passed (was 58 before; conftest additions caused zero regressions). New helpers patch via the existing `sandbox` fixture (both mvcommon.LIBRARY_* and main.LIBRARY_*) — no DIY redirect; no fixture references real C:\Media or real library_*.json. PASS.

## Step 2 — [status: done] In-code PONR + artifact-map spec (design-only comments)
- Executor: orchestrator (direct; Task subagent tool unavailable). Model: opus, effort max (matches the step tag — no mismatch).
- Commit: `01b18de` (`docs(main): in-code PONR + artifact-map spec for rollback — Step 2`).
- Files changed: main.py ONLY (+123 lines, all comments — NO behavior change; `python -m py_compile main.py` clean; `git diff --stat` = main.py only).
- Outcome — transcribed the re-derived PONR table + snapshot/restore contract into main.py as the authoritative implementation spec every Step 3 candidate builds against:
  - Module-level `AUTO-ROLLBACK SPEC` block inserted before CORE COMMANDS header (location-agnostic per N-4 — does NOT presume the primitive's final home): O-2 invariant (master = source of truth; exactly two PONRs); D-6 snapshot shape (entry_existed, prior_status/uploaded, parent_id/parent_existed, child_already_linked, split_info_existed, preexisting_paths set); inverse action per reversible artifact (entry / split_info / status+uploaded / uid / <short_id>.sha256 / _parts / checksums / restore merged target / parent child-link / parent season_map per D-7); the partial-rollback-on-Windows-lock honesty rule; the PONR toggle -> structured hard-fail naming `fetch_restore <id>` (N-2); D-9 leave-remote-dir; and a per-command PONR summary.
  - Per-site `[ROLLBACK SPEC]` markers with CURRENT verified line refs: cmd_prep fully reversible + both early-skips @311-318 flagged as zero-artifact / never-roll-back; cmd_push NO rollback PONR (O-1) at the resume `_parts` branch @700 + the split_info save @736 + the chunk delete @858 (all noted resumable, never delete pre-existing _parts); cmd_replace dummy-temp @957 as the only pre-PONR artifact + the PONR seam @990 (augmented the existing C9 seam comment with pre/at-PONR behavior + the don't-double-handle-C9-stale-sweep note); cmd_restore pre-PONR C11 quarantine reuse @1202 + the chunk-delete PONR @1232 + standard-path-no-torn-window note.
- Acceptance: map names every in-scope artifact (entry, parent season_map + child link, uid, <short_id>.sha256, _parts, checksums, split_info, status, uploaded, restore merge output) with current line refs; matches D-1/D-4/D-6/D-7/D-9 + O-1/O-2. Step 1 baseline remains green: full `pytest -q` -> 58 passed (no runtime change). PASS.

## Step 3 — [status: in_progress] FLAGSHIP UNCAPPED BAKE-OFF (JUDGE-REVIEWS-ONLY / USER-DECIDES)
- Mode: multi-candidate, executed inline (Task subagent unavailable). Worktrees under .candidates/step-03/{A,B,C} (gitignored), each branched off feature/auto_rollback HEAD 80f7711 (Steps 1+2). Three genuinely-distinct schools per plan: A snapshot/transaction context-manager, B compensating-action stack, C on-disk journal. No D/E (no further genuinely-distinct strategy that isn't a cosmetic variant of these three).
- Per-candidate deliverable: primitive (3a) + wrapping integration of cmd_prep/push/replace/restore (3b) + orchestrator unification of both ad-hoc paths with season resume-range (3c) + full scenario matrix tests/test_rollback.py (3d) + DESIGN.md. Each committed in its own worktree as it goes green (checkpointing).

### Candidate A — [status: done & committed]
- Architecture: snapshot/transaction context-manager (`RollbackContext` class + `RollbackHardFail` carrier), placement main.py only.
- Worktree: .candidates/step-03/A · Branch: feature/auto_rollback__cand_a · Commit: e6fde22.
- pytest tests/ -q -> 66 passed, 1 skipped (ffmpeg-gated genuine-split test skips cleanly; ffmpeg absent on this machine). Baseline oracle (test_baseline_happy_path.py) unchanged & green.
- DESIGN.md: docs/feature-auto-rollback/rollback-architecture/CANDIDATE_A.md.
- git diff --stat (vs 80f7711): main.py + tests/test_rollback.py (new) + tests/test_cmd_replace.py (1 except broadened to accept the structured hard-fail) + CANDIDATE_A.md. ~492 ins net. mainfetch.py untouched. Ad-hoc strings gone (only in [ROLLBACK A] comments paraphrasing the removal — reworded to avoid literal forbidden strings).
- Note: one pre-existing test (test_cmd_replace::test_crash_between_renames) caught only OSError; A raises RollbackHardFail post-PONR. Broadened its `except OSError` -> `except Exception` (the test itself states "raise or return False — we don't care which"; its data-safety assertion is preserved). This is a candidate-specific contract change the judge/user should weigh under D-4.

### Candidate B — [status: done & committed]
- Architecture: explicit per-command compensating-action stack (`UndoStack` LIFO of inverse closures pushed next to each forward mutation; `mark_point_of_no_return()` clears the stack) + `RollbackHardFail`. No global snapshot. Placement main.py only.
- Worktree: .candidates/step-03/B · Branch: feature/auto_rollback__cand_b · Commit: 32d21c5.
- pytest tests/ -q -> 66 passed, 1 skipped (ffmpeg-gated). Baseline oracle unchanged & green.
- DESIGN.md: docs/feature-auto-rollback/rollback-architecture/CANDIDATE_B.md.
- git diff --stat (vs 80f7711): main.py + tests/test_rollback.py (new, portable behavior-only matrix) + tests/test_cmd_replace.py (same 1 except broadened) + CANDIDATE_B.md. ~704 ins net. mainfetch.py untouched. No live ad-hoc strings.
- Same post-PONR-raises contract change as A (broadened the one except). Distinctive vs A: inverses are local/adjacent (no central snapshot), LIFO makes D-7 fall out naturally; more small closures.

### Candidate C — [status: done & committed]
- Architecture: durable on-disk operation journal (`RollbackJournal` writes `<folder>/.mediavault_txn.json` with fsync+os.replace, records each intent before acting; `recover_journal()` finishes an interrupted rollback after a hard kill) + `RollbackHardFail`. Placement main.py only.
- Worktree: .candidates/step-03/C · Branch: feature/auto_rollback__cand_c · Commit: 613fe24.
- pytest tests/ -q -> 67 passed, 1 skipped (66 shared matrix + a C-only durable-journal crash-recovery test; ffmpeg-gated split skips). Baseline oracle unchanged & green.
- DESIGN.md: docs/feature-auto-rollback/rollback-architecture/CANDIDATE_C.md.
- git diff --stat (vs 80f7711): main.py + tests/test_rollback.py (new, matrix + crash-recovery test) + tests/test_cmd_replace.py (same 1 except broadened) + CANDIDATE_C.md. ~932 ins net. mainfetch.py + mvcommon.py untouched. No live ad-hoc strings.
- Distinctive: only candidate whose REVERT survives a hard process kill (durable journal + recover_journal). Cost: largest main.py diff + an fsync/os.replace per mutation + a transient dot-file per media folder (happy path still byte-identical per the oracle).

## Step 3 — [status: PAUSED at the user-decides gate]
- All three genuinely-distinct candidates complete, green, committed in their worktrees. mainfetch.py + mvcommon.py untouched across all three; diffs confined to main.py + tests/ + each CANDIDATE_*.md.
- Comparative review (NO WINNER, D-2): docs/feature-auto-rollback/rollback-architecture/DECISION.md. The three CANDIDATE_{A,B,C}.md were also copied onto feature/auto_rollback alongside DECISION.md for one-place review.
- Equivalent on correctness/happy-path/in-process failure (all pass the same matrix + unchanged oracle). Differentiators: B smallest/most-local diff; A single cohesive snapshot object; C uniquely survives a hard kill mid-rollback (durable journal) at a larger-diff + per-mutation-fsync cost.
- PAUSED per the resume brief: NOT auto-selected, NO candidate merged into feature/auto_rollback, Step 4 NOT run, nothing pushed, no PR, no main merge, no archiving. Awaiting the user's winner pick → record as DECISIONS.md N-6 before merge.

## Step 3 — [status: done] WINNER SELECTED + MERGED (2026-06-01)
- User selected **Candidate C** (on-disk operation journal `RollbackJournal` + `recover_journal`), used WHOLESALE for all operations. Recorded as DECISIONS.md N-6 (commit b2041bd). The hybrid (A-for-small / C-for-big) was explicitly considered and rejected (O-1 makes the headline 100GB push a resume-message not a rollback; rollback duration doesn't scale with file size; a hybrid carries both mechanisms + a size-threshold dispatch for marginal gain).
- Merge: `git merge --squash feature/auto_rollback__cand_c` (613fe24) → squash commit `c27b05e` (`feat: auto-rollback via on-disk journal (RollbackJournal) — Candidate C`). Merge applied cleanly with NO conflict (the candidate's CANDIDATE_C.md / review docs matched the canonical copies already on the feature branch).
- git diff --stat of the squash: main.py (+) + tests/test_rollback.py (new) + tests/test_cmd_replace.py (1-line except broadened). mainfetch.py + mvcommon.py UNTOUCHED (verified by name-only grep).
- VERIFY: full `python -m pytest -q` on the merged feature branch → **67 passed, 1 skipped** (the ffmpeg-gated genuine-split test skips cleanly; ffmpeg absent on this machine) — exactly the Candidate-C totals.
- Step 3 ticked [x] in BOTH PLAN.md (root) and docs/feature-auto-rollback/PLAN.md (byte-identical, MD5 EB15F985...).
- Losing candidate branches feature/auto_rollback__cand_a (e6fde22) + __cand_b (32d21c5) left in place for a later human-gated archive/delete decision.

## Step 1 — Add cmd_recover to main.py
Status: complete
Key decisions: Inserted `cmd_recover(target=None, scan=False)` between `recover_journal` (line 592) and the `# CORE COMMANDS` banner; scan branch walks LOCAL_ROOT/{Movies,Series,Anime} read-only reporting journals; resolve branch strips quotes, checks library for id→folder_path, falls back to direct path, then calls `recover_journal`.
Acceptance: `python -c "import main; print(main.cmd_recover.__name__)"` → `cmd_recover`; `recover_journal` lines 561-592 byte-for-byte unchanged (verified by read-back).

## Step 2 — Wire recover dispatch + usage
Status: complete
Key decisions: Dispatch joins sys.argv[2:] so space-containing folder paths work; --scan takes priority over positional target; no-args case prints usage error without calling cmd_recover.
Acceptance: `python main.py` (no args) usage block lists `recover [id|folder]  (or: recover --scan)`; `python main.py recover` (no args) prints `❌ Usage: recover [id|folder]   (or: recover --scan)`. Both passed.

## Step 3 — Add tests/test_recover_cli.py
Status: complete
Key decisions: Hand-wrote journals as JSON (no RollbackJournal constructor needed); _seed_library helper writes lib_movies entry + empty series/anime; scan test monkeypatches main.LOCAL_ROOT to tmp_path/Media with C:\Media guard; crossed-PONR test asserts journal survives and result is falsy.
Acceptance: pytest tests/test_recover_cli.py -v: 5 passed in 0.26s; pytest -q: 72 passed, 1 skipped (no regressions).

## Step 4 — [status: done] Architect docs (docs-only) (2026-06-01)
- Executor: orchestrator (direct; Task subagent unavailable). Model: opus, effort high (matches the step tag — no mismatch).
- Files changed (DOCS ONLY — `git diff --name-only` confirmed zero `.py` files): ARCHITECTURE.md, docs/feature-auto-rollback/README.md.
- ARCHITECTURE.md: added §12a "Auto-Rollback for Multi-Step Commands" (the single RollbackJournal mechanism + RollbackHardFail + recover_journal crash recovery; the verified PONR table with current main.py line refs — cmd_prep@599 / cmd_push@992 / cmd_replace@1335 PONR@1398 / cmd_restore@1598; the O-1 resume-message vs O-2 hard-fail split; orchestrator unification + season resume-range messaging; D-4/D-6/D-7/D-9 + C9/C11 seam reuse). Updated the stale §12 bullets that described the two old ad-hoc paths to point at §12a.
- docs/feature-auto-rollback/README.md: status PLANNING → IMPLEMENTED; cross-links DECISIONS.md N-6 + rollback-architecture/DECISION.md (Candidate C won, wholesale) and ARCHITECTURE.md §12a; notes pytest 67 passed / 1 skipped.
- Descriptive only — NO code change. Step 4 ticked [x] in BOTH PLAN.md copies (byte-identical, MD5 AA8906AB...).

## Step 4 — Document recover in README + ARCHITECTURE
Status: complete
Key decisions: additive doc rows only; recover_journal semantics unchanged
Acceptance: README row added at line 139 after `sort` (describes `recover [id|folder]` and `recover --scan`); ARCHITECTURE §5 row added at line 226 after `fetch` (describes `cmd_recover` dispatch); ARCHITECTURE §12a notes CLI entry point at line 1394 (new sentence after the alternatives paragraph, before PONR table section).

## Step 5 — DECISIONS.md + IMP-R2 status flip
Status: complete
Key decisions: wrapper-only/change-gate; id-first resolution; scan read-only; argv join
Acceptance: DECISIONS.md created with 4 entries; improvements_tierR.md IMP-R2 Status: done

## Step 1 — [status: done] Fix SxxExx episode-extraction regex
- Executor: executor-opus. Model: opus, effort: high (plan) / max (baked) — over-powered, acceptable.
- Files changed: main.py line 890 only.
- Outcome: Dropped the optional decimal group `(?:\.\d+)?` from the `SxxExx` branch on line 890, changing `re.search(r"[sS]\d+[eE](\d+(?:\.\d+)?)", filename)` to `re.search(r"[sS]\d+[eE](\d+)", filename)`. The `NxYY` branch on line 891 (`\d+[xX](\d+(?:\.\d+)?)`) is untouched. REPL checks all printed expected values: `Fringe.S03E20.6.02.AM.EST.2011.1080p.BluRay.mkv` → `20`; `Fringe.S03E19.1080p.BluRay.mkv` → `19`; `[Grp] Show 16x05.5 [hash].mkv` (NxYY) → `05.5`. `git diff` confirmed only line 890 changed. `python -m pytest -q` → 72 passed, 1 skipped (no regressions).
- Key decisions: SxxExx decimal capture dropped (Option A); NxYY line 891 untouched; rollback code untouched.

## Step 2 — [status: done] Add unit tests for episode-ID extraction
- Executor: executor-sonnet. Model: sonnet, effort: medium (plan) / medium (baked) — no mismatch.
- Files changed: tests/test_prep_season_episode_parse.py (new, 3 test cases).
- Outcome: Tests A (dotted-title e20 fix), B (canonical e19), C (anime NxYY .5). pytest 75 passed, 1 skipped.
- Key decisions: Used sandbox + stub_tech_specs fixtures; created separate tmp subfolders per test group. Fake .mkv files must write 210_000 bytes (exceeding DUMMY_MAX_BYTES=200_000) or cmd_prep early-skips them as dummy files.

## Step 3 — [status: done] Add filter-arithmetic regression tests
- Executor: executor-sonnet. Model: sonnet, effort: medium (plan) / medium (baked) — no mismatch.
- Files changed: tests/test_prep_season_episode_parse.py (extended, +2 pure-function tests D and E).
- Outcome: Test D (e20 included by 20-20), Test E (e20.6 excluded by 20-20). pytest 77 passed, 1 skipped.
- Key decisions: Approach (i) — inline filter logic, pure function, no I/O. Documents the invariant that a clean `e20` ID yields ep_num==20.0 and passes the 20-20 filter.

## Step 4 — [status: done] Record decisions and update tracked plan
- Executor: executor-haiku. Model: haiku, effort: low (plan) / low (baked) — no mismatch.
- Files changed: docs/feature-fix-episode-title-parse/DECISIONS.md (new), docs/feature-fix-episode-title-parse/PLAN.md (steps marked done).
- Outcome: DECISIONS.md records the 4 decision points. Tracked PLAN.md steps all marked [x].
- Key decisions: doc-only step; no code changed.

## Step 1 — Extend cmd_prep_season for combined-episode aliases
- Status: done
- Files changed: main.py
- Key decision: Combined-episode detector scoped to SxxExx TV branch only; alias loop runs after primary cmd_prep returns truthy; save_library called once per file after all secondaries.
- Acceptance: Verified by inspection that S04E19E20 creates e19 primary + e20 alias; S04E19 single creates only e19; anime NxYY unaffected.

## Step 2 — Add _resolve_alias helper
- Status: done
- Files changed: main.py
- Key decision: Single-hop only; returns (real_id, entry); fallback to (mid, alias_entry) if alias target missing.
- Acceptance: _resolve_alias(lib, "...e20") returns ("...e19", <e19 entry>); _resolve_alias(lib, "...e19") returns ("...e19", <e19 entry>).

## Step 3 — Make cmd_prep_push_rep_season alias-aware
- Status: done
- Files changed: main.py
- Key decision: De-alias pass runs after range filter, before disk pre-flight; target_ids contains only primary ids from that point on; alias-only range (episodes 20-20) resolves to the primary (e19).
- Acceptance: With seeded library containing e19(real)+e20(alias), episodes 18-20 and episodes 20-20 both resolve target_ids to [e19] only.

## Step 4 — Defensive resolve in _season_resume_cmd
- Status: done
- Files changed: main.py
- Key decision: ep_str derived from real_id (resolved) not rid (raw); library is in-scope closure variable. Step 3 already de-aliases target_ids so this is belt-and-suspenders only.
- Acceptance: _season_resume_cmd emits the primary episode number; the RollbackHardFail resume_cmd contract (must name an existing command) is preserved.

## Step 6 — Resolve aliases in mainfetch.resolve_targets
- Status: done
- Files changed: mainfetch.py
- Key decision: Local _resolve_alias helper added (mirrors main._resolve_alias, no import); season_map branch de-aliases children before building target_entries; single-id branch resolves alias to primary. fetch episodes 20-20 and fetch episodes 19-19 both queue the same physical file.
- Acceptance: fetch by alias id (e20) returns primary's filename/hash/split_info; season range 19-20 queues one entry, not two.

## Step 5 — cmd_push_group / cmd_replace_group alias-aware
- Status: done
- Files changed: main.py
- Key decision: De-alias pass inserted after range filter, before downstream loop, in each group command that accesses entry fields directly. cmd_restore_group was also included (not just push/replace) because cmd_restore accesses entry['folder_path']/entry['filename'] directly and would KeyError on an alias entry.
- Acceptance: Group push/replace over a library with a multi_ep_alias resolves to a single primary id; existing non-alias group behaviour unchanged (transform is a no-op for non-alias ids).

## Step 7 — Tests F–K
- Status: done
- Files changed: tests/test_prep_season_episode_parse.py
- Key decision: Tests H and I are pure unit tests of the de-alias transform (no cmd_prep_season call); others use the standard sandbox+tmp_path fixture pattern.
- Acceptance: pytest tests/test_prep_season_episode_parse.py -v green (A–K all pass).

## Step 8 — ARCHITECTURE.md update
- Status: done
- Files changed: ARCHITECTURE.md
- Key decision: Added multi_ep_alias as a third entry type in §6.3; added one sentence to the cmd_prep_season bullet in §7.8.
- Acceptance: §6.3 lists three entry types; §7.8 cmd_prep_season mentions combined-episode aliasing.
