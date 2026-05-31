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
