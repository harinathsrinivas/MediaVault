# Execution Log

Task: IMP-C8 — Post-push remote verification
Branch: feature/post_push_verify (from origin/main @ 59932be; A1 1aac738 / C2 cf79684 / G1 8c12680 all confirmed ancestors)
Baseline (pre-change): pytest -q -> 46 passed.
Note: Task subagent tool is unavailable in this run, so the orchestrator executes steps directly (same situation as the A1 and C2 runs). STATUS.md is committed per-step as a scratchpad artifact. Pre-existing dirty working tree had a stray README.md regression (reverted already-merged G1/A1/H1 docs) and a CRLF-only change on C8-post-push-verify.md — both discarded before cutting the branch from origin/main; they are NOT part of C8.

## Step 1 — [status: done]
- Executor: orchestrator (direct; Task subagent tool unavailable)
- Model: haiku (effort tag: low — matches executor-haiku baked low; no mismatch)
- Files changed: main.py
- Outcome: Added module-level `PUSH_VERIFY_REMOTE = False` immediately after `MVMETA_SUFFIX` (near the other push constants PARTIAL_SUFFIX/MVMETA_SUFFIX/REMOTE_ROOT), with a two-line comment noting it is gated off here until IMP-A5 adds config-file support. Purely additive; no logic changes.
- Acceptance: `python -c "import main; assert main.PUSH_VERIFY_REMOTE is False"` -> "OK PUSH_VERIFY_REMOTE is False". No other files changed. PASS.

## Step 2 — [status: done]
- Executor: orchestrator (direct)
- Model: sonnet (effort tag: none/medium — matches executor-sonnet baked medium; no mismatch)
- Files changed: main.py
- Outcome:
  - 2b: Added module-level `_verify_chunk_hash(adb_base, remote_path, safe_path, expected_sha256)` just above `cmd_push`. Runs `adb shell sha256sum '<safe_path>'` with `check=True, capture_output=True, text=True`; catches the call's own `CalledProcessError` (cmd-not-found / file-not-found) and warns+returns (OD-2a); on success parses `stdout.strip().split()[0]` and raises `subprocess.CalledProcessError(1, ...)` on mismatch so C2's retry wrapper re-runs the closure.
  - 2a: Before `# 3. UPLOAD LOOP` added `_chunk_hashes: dict` mapping local_filename->expected_sha256 from `chunk_metadata` (new split) OR `library[manual_id]["split_info"]["chunks"]` (resume); empty for single-file push. Lives in the closure scope.
  - 2c: Extended the existing `_push_and_rename()` closure with `if PUSH_VERIFY_REMOTE: expected = _chunk_hashes.get(local_fname); if expected: _verify_chunk_hash(adb_base, remote_full_path, safe_final, expected)`. No signature changes — all names already in closure scope.
  - Accuracy tweak (per plan risk note): retry print "(ADB push failed)" -> "(ADB push/verify failed)" since a mismatch now also feeds the retry. Behaviour unchanged.
- Key decision: `PUSH_VERIFY_REMOTE=False` keeps the `if` body dead, so the happy path is byte-for-byte identical to pre-C8 (regression proven by existing push tests). `expected is None` (single-file or hash not found) skips verification silently — a missing stored hash is not a corruption signal.
- Acceptance: import OK + `_verify_chunk_hash` callable; `pytest tests/test_cmd_push_retry.py tests/test_cmd_push_partial.py -q` -> 8 passed (regression: zero extra subprocess calls when False); full `pytest -q` -> 46 passed (unchanged from baseline). The =True match/mismatch/unavailable paths are asserted by Step 3 tests.

## Step 3 — [status: done] [MULTI-CANDIDATE, 2 candidates]
- Executor: orchestrator (direct; ran both candidates sequentially in git worktrees under .candidates/step-03/{A,B}, then judged)
- Model: sonnet (effort tag: none/medium — matches executor-sonnet baked medium; no mismatch)
- Mode: multi-candidate (candidates: 2). Winner: **A**. DECISION.md: docs/feature-auto-rollback/C8-post-push-verify/STEP3_DECISION.md
- Files changed (winner A merged onto feature branch): tests/test_cmd_push_verify.py (new), plus the DECISION record. Candidate A made NO conftest change (its design).
- Candidate A (inline FakeAdbVerify recorder): self-contained recorder in the new test file, sha256sum answered from a `sha256_mode` flag (correct/wrong/wrong_then_correct/unavailable); no real bytes; no conftest change; sandbox-only isolation. 5/5 scenarios pass, full suite 51 passed.
- Candidate B (extend mock_device): added the `sha256sum` branch to conftest + REQUIRED an unplanned `mv` handler change (Path.rename -> Path.replace) because the mock mv doesn't overwrite on Windows, so the retry-after-mismatch path raised WinError 183 and masked the verify retry as an mv error. Real-bytes round trip; 5/5 scenarios pass, full suite 51 passed.
- Judge rationale (ranked criteria): #1 correctness TIE (both 5/5, full contract). #2 fault-injection clarity -> A (single constructor flag vs indirect byte corruption). #3 regression strength -> A (slight; literal empty-list assert, no wrapper). #4 blast radius -> A decisively (one new file, zero shared-fixture change vs B's behavioural change to a fixture the G1/C2 suites depend on — the exact risk the plan flagged). #5 fidelity -> B (real round trip; lowest-ranked). Ranking favors A.
- Scenarios proven (a) verify-off zero sha256sum calls; (b) match -> onboarded, 1 call/chunk; (c) mismatch-then-match -> C2 retry self-heals, 2 calls, 1 `rm '.partial'`; (d) all-3-mismatch -> False, entry unchanged, _parts/ populated, 3 calls; (e) unavailable -> warn+skip, True, 1 call, no retry.
- Acceptance: `pytest tests/test_cmd_push_verify.py -q` -> 5 passed; full `pytest -q` -> 51 passed (no regressions). PASS.
