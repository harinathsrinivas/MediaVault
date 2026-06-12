# Planner prompts for the next tasks

Ready-to-paste prompts for the **planner agent**, one per upcoming task. Each is
**self-contained** — it carries the load-bearing context from the auto-rollback
session (the merged `RollbackJournal` mechanism, the change-gate, the harness
limitation, and the repo conventions) and points the planner at the docs so the
details stay accurate as code drifts.

**How to use:** copy one fenced block below and hand it to the planner agent. Add
or tighten constraints as needed before running. The planner produces `PLAN.md`
only; review it, then run the orchestrator.

**Recommended order:** IMP-R2 (small, unblocks clean manual testing of rollback) →
IMP-C1 (builds on the season resume-range messaging) → IMP-R1 (the storage win,
largest + most invasive).

**Notes that apply to all three:**
- Line numbers below are indicative — instruct the planner to **re-derive against
  current `main.py`** (it grows over time).
- **Change-gate (`CLAUDE.md` → "Auto-rollback is load-bearing"):** C1 and R1 both
  touch the rollback orchestration/journaling, so their prompts require an explicit
  *pause-and-ask* step before any rollback-behavior change. R2 only *exposes*
  `recover_journal`, so it's additive — but still must not change rollback semantics.
- The tasks are tracked in `improvements/improvements_tierR.md` (R1, R2) and `improvements/improvements_tierC.md`
  (C1). The full rollback spec is `docs/feature-auto-rollback/ROLLBACK_MECHANISM.md`.

---

## IMP-R2 — `recover` CLI command (small)

```text
Plan IMP-R2 (expose a `recover` CLI command for recover_journal) for the MediaVault repo. Produce PLAN.md only — no code, no branch.

CONTEXT (as of 2026-06): Auto-rollback is MERGED to main (PR #14); the mechanism is the on-disk RollbackJournal in main.py. READ FIRST: docs/feature-auto-rollback/ROLLBACK_MECHANISM.md (esp. §7 recover_journal, §10 change-gate), ARCHITECTURE.md §12a, improvements_tierR.md (the IMP-R2 entry). recover_journal(folder_path) already exists (main.py ~561) and is tested (tests/test_rollback.py::test_journal_survives_hard_kill_and_recovers) but is NOT a CLI subcommand — today it's only callable via `python -c "import main; main.recover_journal(...)"`.

SCOPE: add `python main.py recover <id|folder>` — resolve the media folder by library id, or accept a direct path — call recover_journal(), print the outcome. Optional `recover --scan` to sweep the media roots for leftover .mediavault_txn.json journals and report each (defer the deep version to IMP-R3 unless trivial). A journal that crossed its PONR must be declined read-only, as recover_journal already does. Wire into the sys.argv dispatch, the README CLI table, and ARCHITECTURE.

CONSTRAINTS:
- CHANGE-GATE (CLAUDE.md "Auto-rollback is load-bearing"): EXPOSE recover_journal, do NOT change its semantics or the journal format. A CLI wrapper is additive and fine. If the plan finds it must modify recover_journal/journal/any rollback path, PAUSE and ask the user with the exact diff first.
- D-4 happy path byte-identical; D-5 tests use COPIES only (sandbox), never real C:\Media or library_*.json; build on existing tests/ + conftest fixtures per docs/testing-strategy.md.
- Per-step [model:][effort:] tags (Opus 4.8 tiers, ARCHITECTURE.md §19). This is small → likely single-executor.
- HARNESS NOTE: in this environment a sub-agent (orchestrator) CANNOT spawn Task sub-agents; multi-candidate steps run inline/sequentially in .candidates/step-NN/{A,B,...} worktrees with the orchestrator as executor. (Likely no multi-candidate here.)
- PR title must include the code (… — IMP-R2). PLAN.md location: write byte-identical to /PLAN.md (root, gitignored) AND a tracked docs/imp-r2-recover-cli/PLAN.md.

DELIVERABLE: PLAN.md with numbered steps, per-step model/effort, files + line refs (re-derive against current main.py), acceptance (incl. a sandbox test of the subcommand dispatch), manual verification commands. End with a suggested branch name and the step list.
```

---

## IMP-C1 — season auto-resume (medium · ⚠️ touches rollback)

```text
Plan IMP-C1 (season auto-resume) for the MediaVault repo. Produce PLAN.md only — no code, no branch.

CONTEXT (as of 2026-06): Auto-rollback is MERGED (PR #14). cmd_prep_push_rep_season (main.py ~2048) already, on a mid-batch failure, keeps completed episodes, lets the in-flight item self-handle (rollback if reversible / RollbackHardFail if irreversible), and PRINTS a reconstructed resume-range command (SIZE_*/device/episodes, handles .5 episodes) — but does NOT auto-resume. READ FIRST: improvements_tierC.md (C1 entry), docs/feature-auto-rollback/C1-season-auto-resume/C1-season-auto-resume.md (original design notes), docs/feature-auto-rollback/ROLLBACK_MECHANISM.md (§6 orchestrators, §10 change-gate), ARCHITECTURE.md §12a, DECISIONS.md. Note: C1 was deliberately NOT folded into auto-rollback (auto-rollback prints the command; C1 adds auto-resume on top).

SCOPE: turn the printed resume-range into an actual auto-resume of the remaining episodes after a REVERSIBLE interruption (e.g. a .mediavault_progress.json progress file, or re-driving the loop from library state). Completed episodes stay; resume only the not-yet-done ones with the same args. An IRREVERSIBLE in-flight item (RollbackHardFail) must STOP — never auto-retry across a point-of-no-return.

CRITICAL — CHANGE-GATE: C1 modifies cmd_prep_push_rep_season, which IS part of the merged auto-rollback orchestrator unification (the season resume-range messaging). This is squarely "affecting rollback" per CLAUDE.md. The PLAN MUST include an explicit early step that PAUSES and presents the user the exact behavioral diff vs the documented season failure handling (ROLLBACK_MECHANISM.md §6) and gets sign-off BEFORE any code change. Do not silently alter rollback/resume semantics.

CONSTRAINTS:
- D-4 happy path byte-identical; D-5 tests COPIES only; build on existing tests/ + conftest fixtures.
- Several legitimate approaches likely exist (progress-file vs in-memory loop re-drive vs library-state-driven). Consider a multi-candidate architecture step (judge reviews, surface to the user — do not auto-pick if the user should choose).
- HARNESS NOTE: orchestrator sub-agent CANNOT spawn Task sub-agents — multi-candidate runs inline/sequentially in .candidates/step-NN/{A,B,...} worktrees (orchestrator is the executor). Size candidate counts accordingly.
- Per-step [model:][effort:] tags (Opus 4.8 tiers). PR title … — IMP-C1. PLAN.md to /PLAN.md (root) + tracked docs/feature-auto-rollback/C1-season-auto-resume/PLAN.md.

DELIVERABLE: PLAN.md with steps, model/effort, the mandatory change-gate pause step, files + re-derived line refs, acceptance + manual verification (incl. a kill-mid-season → auto-resume test on copies). End with a suggested branch name and the step list.
```

---

## IMP-R1 — streaming split-upload-delete (large · ⚠️ touches rollback)

```text
Plan IMP-R1 (reduce the split/upload disk peak via streaming split-upload-delete) for the MediaVault repo. Produce PLAN.md only — no code, no branch.

CONTEXT (as of 2026-06): Auto-rollback is MERGED (PR #14). Today cmd_push (main.py ~992) splits the ENTIRE file into _parts/ up front via split_video_file (~155), hashes all chunks, writes split_info, THEN the upload loop adb-pushes each chunk (G1 .partial + atomic rename), C8-verifies it, and os.removes it after a successful upload. Peak local disk = original + ALL chunks (a 20 GB file → 4×5 GB chunks → ~40 GB transient). READ FIRST: improvements_tierR.md (IMP-R1 entry), docs/feature-auto-rollback/ROLLBACK_MECHANISM.md (§5 PONR, §9 storage, §10 change-gate), ARCHITECTURE.md §12a, and docs/feature-auto-rollback/G1-* and C8-* notes.

SCOPE: split → upload → delete one chunk at a time (or a bounded window of K chunks) so chunks never all coexist; target peak = original + K×chunk (~25 GB for K=1). MUST preserve: G1 .partial + atomic rename, C8 post-push remote verify, per-chunk .sha256 sidecars, split_info accuracy, and the O-1 resume semantics (a re-run resumes from whatever is in _parts/).

CRITICAL — CHANGE-GATE: R1 reworks cmd_push's split/upload flow and INTERACTS with rollback: (a) the journal records the _parts/ DIRECTORY as one create_dir (not per-chunk), and (b) O-1 push-resume relies on a full _parts/ surviving. Incremental split/delete changes WHEN chunks exist on disk, so the journaling AND resume logic likely need rework. This is squarely "affecting rollback" per CLAUDE.md. The PLAN MUST include an explicit early step that PAUSES and presents the user the exact diff vs the documented push journaling + O-1 resume (ROLLBACK_MECHANISM.md §5/§6/§9) and gets sign-off BEFORE any code change.

CONSTRAINTS:
- D-4: the FINAL state must be byte-identical (same library/remote result, same final hashes) even though intermediate disk usage changes; D-5 tests COPIES only; the ffmpeg-gated multi-chunk fixture (conftest ffmpeg_multichunk_mkv) is needed for genuine-split tests.
- Several approaches likely (true split-on-demand vs split-then-upload-window-of-K vs split-N-ahead). Consider a multi-candidate architecture step (judge reviews; user picks).
- HARNESS NOTE: orchestrator sub-agent CANNOT spawn Task sub-agents — multi-candidate runs inline/sequentially in .candidates/step-NN/{A,B,...} worktrees. Plan accordingly.
- Per-step [model:][effort:] tags (Opus 4.8 tiers; large → mostly opus). PR title … — IMP-R1. PLAN.md to /PLAN.md (root) + tracked docs/imp-r1-stream-split-upload/PLAN.md.

DELIVERABLE: PLAN.md with steps, model/effort, the mandatory change-gate pause step, files + re-derived line refs, acceptance (incl. a peak-disk assertion on a genuine multi-chunk split + a resume-after-interruption test), manual verification. End with a suggested branch name and the step list.
```
