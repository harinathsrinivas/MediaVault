---
title: "C8 — Post-push remote verification"
type: prerequisite-task
improvement: IMP-C8
tier: C
role: complementary
order: 6
status: done
branch: feature/post_push_verify
feature: auto-rollback
tags: [claude, mediavault, complementary, tier/C, status/done]
created: 2026-05-29
---

# C8 — Post-push remote verification

> **At a glance:** After each successful `adb push` + `adb shell mv` (G1 is done),
> run `adb shell md5sum` on the remote final-name file and compare to the local chunk
> hash already in `split_info`. A mismatch raises a `CalledProcessError` which
> C2's `retry()` wrapper (also done) catches and re-runs the push+mv pair. Gates on
> a `push.verify_remote` flag (default false). Catches silent USB/driver corruption
> before the local chunk is deleted. **Both G1 and C2 are done — branch from
> `origin/main` and integrate directly.**
> Related: [[RELATED_IMPROVEMENTS]] · [[_TRACKER]]

## Claude Code prompt (paste into a fresh session)

```
Use the planner agent.

Task: Plan the implementation of improvement IMP-C8 ("Post-push remote verification") from improvements_tierC.md.

Read these FIRST, in this order, before planning:
1. improvements_tierC.md -> the IMP-C8 section. The spec for WHAT and WHY.
2. docs/feature-auto-rollback/README.md, then docs/feature-auto-rollback/RELATED_IMPROVEMENTS.md ("C8" subsection). C8 is COMPLEMENTARY to the upcoming auto-rollback feature: it catches silent corruption at push time so rollback never has to deal with a chunk that looks uploaded but is corrupt. The one hard requirement: the failure contract callers rely on must not change.
3. ARCHITECTURE.md and the code: the adb push call in cmd_push in main.py (the adb push + per-chunk local delete loop); split_info structure (chunks[i].hash is already computed before push); and IMP-C2's retry helper in mvcommon.py.

Confirmed state — do not re-derive these, treat as facts:
- IMP-G1 (push partial + atomic rename) is DONE and merged into origin/main (PR #7). Verify the file at the FINAL remote path (after `adb shell mv` from `.partial` to final name). Do not write a code path for the pre-G1 case — it no longer exists.
- IMP-C2 (retry helper) is DONE and merged into origin/main (feature/adb_selenium_retry, 2026-05-30). The retry() signature is: retry(fn, attempts=3, backoff=(1,4,16), jitter=1.0, retry_on=(SubprocessError, TimeoutError), on_retry=None). The adb push call site in cmd_push uses retry_on=(CalledProcessError,). On a hash mismatch, raise a CalledProcessError (or subclass of SubprocessError) so the existing retry wrapper catches it and re-runs the push+mv pair (including pre-retry .partial cleanup). Do not treat this as conditional or leave a "future integration" note.
- IMP-A1 (mvcommon) is DONE. The retry() helper lives in mvcommon.py. C8's verification helper (if extracted) may also live there.
- Branch C8 from origin/main AFTER confirming A1 and C2 are merged (check git log origin/main — look for the A1 and C2 merge commits).

What to build: after each successful `adb push` AND after G1's `adb shell mv` from `.partial` to final name, run `adb shell md5sum <remote_final_path>` (fall back to sha256sum if md5sum is unavailable). Parse the device-side hash and compare to `split_info.chunks[i].hash`. On mismatch, raise a retryable exception so C2's retry wrapper handles the re-push. Gate the entire verification step behind a config flag `push.verify_remote` (default: false) — a hardcoded module-level constant for now (configurability is IMP-A5, out of scope).

Constraints (full list in docs/feature-auto-rollback/README.md):
- Branch from origin/main (after A1 + C2 merge). Suggested branch: feature/post_push_verify.
- Happy path with verify_remote=false IDENTICAL to today. With verify_remote=true, first-push-success-and-hash-match is also identical. A failed hash check must return the SAME failure signal callers rely on — do not change the failure contract.
- Tests in tests/, COPIES only — never touch real C:\Media files or real library_*.json; mock adb (subprocess.run) so no real device is needed; test: hash matches (passes), hash mismatches (C2 retry fires), adb shell hash command unavailable (graceful fallback or skip with warning).
- Surgical: the post-push verification step + config flag constant + tests; don't touch archive/ or the pre-push split logic.

DOCUMENTATION: save your PLAN.md into docs/feature-auto-rollback/C8-post-push-verify/PLAN.md; keep all task artifacts there; fill the "Completion report" in docs/feature-auto-rollback/C8-post-push-verify/C8-post-push-verify.md when done. (Keep /PLAN.md at root in sync if needed.)

Pause and ask me about open decisions, at minimum: md5sum vs sha256sum (md5sum is faster; sha256sum already used locally — do we re-hash on device or accept the algorithm difference?); what to do if adb shell hash command is unavailable (skip silently with warning / hard-fail); where to put the verification helper (inline in cmd_push vs extracted to mvcommon).

Deliverables: PLAN.md only (no code, no branches) with steps + tests + verification + "Open Decisions". Note IMP-C8 is marked done in improvements_tierC.md on implementation, the architect updates ARCHITECTURE.md/README if needed, and I want branch name, PR to main, and manual test commands at the end.
```

## What & why
ADB push has its own integrity checks but is not bulletproof against certain USB
cable / driver issues. Silent corruption can land a corrupt chunk on the phone
that only surfaces during `cmd_restore` weeks later. A post-push `md5sum` catches
this at the earliest possible moment — before the local chunk is deleted.

## Relationship to auto-rollback / seam to leave
Catches silent corruption at the push layer so rollback never inherits a corrupt
remote state. C8's mismatch path is the natural trigger for C2's retry; keep
the failure signal unchanged so auto-rollback's push-failure branch works
unchanged. Gate behind `push.verify_remote` so it can be promoted to default-true
later without touching rollback logic. Details: [[RELATED_IMPROVEMENTS]] → C8.

## Definition of Done
- [x] Planner run; `PLAN.md` in this subfolder; open decisions confirmed
- [x] Branched `feature/post_push_verify` off `origin/main`
- [x] `adb shell sha256sum` verification after each push (OD-1: sha256sum, not md5sum)
- [x] Hash compared to `split_info.chunks[i].hash`; mismatch triggers retry / failure
- [x] Gated behind `PUSH_VERIFY_REMOTE` flag (default false); happy path unchanged
- [x] G1 is done — verify at final name (after `adb shell mv` from `.partial`); no pre-G1 code path
- [x] C2 is done — mismatch raises `CalledProcessError` so retry() wrapper handles re-push automatically
- [x] Tests in `tests/` (mocked adb): match / mismatch / command-unavailable (+ verify-off + retry-then-match)
- [x] `IMP-C8` marked done in `improvements_tierC.md`
- [x] `ARCHITECTURE.md` updated (§7.5); `README` unchanged (not needed)
- [x] PR to `main` opened
- [x] Completion report below filled in

## Completion report
- **Branch:** `feature/post_push_verify` (cut from `origin/main` @ `59932be`; A1 `1aac738` / C2 `cf79684` / G1 `8c12680` confirmed ancestors).
- **PR:** https://github.com/harinathsrinivas/MediaVault/pull/12 (title `Feature: post-push remote hash verification — IMP-C8`).
- **Merged commit:** merged via PR #12 on 2026-05-30 (squash-merged into `main` as `c1ec88d`).
- **Files changed:**
  - `main.py` — `PUSH_VERIFY_REMOTE = False` constant; new `_verify_chunk_hash()` helper; `_chunk_hashes` builder before the upload loop; verify call inside `_push_and_rename()`; retry print "ADB push failed" -> "ADB push/verify failed".
  - `tests/test_cmd_push_verify.py` — new (5 scenarios; inline `FakeAdbVerify` recorder — Step 3 winner, candidate A).
  - `improvements_tierC.md` — IMP-C8 Status -> done.
  - `ARCHITECTURE.md` — §7.5 post-push verification sub-bullet.
  - Docs: `_TRACKER.md` (C8 done), this report, `STEP3_DECISION.md` (multi-candidate verdict), `STATUS.md` (execution log).
- **Tests added:** 5 in `tests/test_cmd_push_verify.py` — (a) verify-off zero sha256sum calls; (b) hash match -> onboarded, one call/chunk; (c) mismatch-then-match -> C2 retry self-heals (2 calls, one `rm '.partial'`); (d) all-3-mismatch -> False, entry unchanged, `_parts/` populated, 3 calls; (e) sha256sum unavailable -> warn-and-skip, True, one call, no retry. Full suite: 51 passed (46 baseline + 5).
- **Manual test commands:**
  - `pytest -q` ; `pytest tests/test_cmd_push_verify.py -q` ; `pytest tests/test_cmd_push_retry.py -q`
  - `python main.py push <small-mov-id>` (verify off — no behaviour change). Then temporarily set `PUSH_VERIFY_REMOTE = True` and re-push a small split entry; on a clean cable it completes with no extra output. To simulate a mismatch, corrupt the remote file via `adb shell` after push and re-run push on the `_parts/` chunks — expect `⏳ Retry` lines then self-heal or `❌ FAILED`.
- **Open decisions resolved:** OD-1 sha256sum (direct compare to stored SHA-256, no re-hash, no schema change); OD-2 warn-and-skip when the hash command is unavailable (one warning line, push continues); OD-3 helper inline in `main.py` as module-level `_verify_chunk_hash()` (keeps `mvcommon` stdlib-only).
- **Notes / surprises:**
  - Step 3 was multi-candidate (2). Candidate A (inline recorder, no conftest change) beat candidate B (extend `mock_device` for a real-bytes round trip) on the ranked criteria. Key finding: B required an UNPLANNED behavioural change to the shared `mock_device` `mv` handler (`Path.rename` -> `Path.replace`) — the mock `mv` does not overwrite on Windows, so the retry-after-mismatch path raised `WinError 183` and masked the verify retry as an mv error. That widened blast radius (the `mv` handler is shared by the G1/C2 suites) decided #4 in A's favour. See `STEP3_DECISION.md`.
  - The Task subagent / git-agent / judge tools were unavailable in this run, so the orchestrator executed every step, the candidate worktrees, and the judging directly (same situation noted in the A1/C2 STATUS logs).
  - Pre-existing dirty working tree carried a stray `README.md` regression (reverted already-merged G1/A1/H1 docs) and CRLF-only artifacts on `C8-post-push-verify.md`/`improvements_tierC.md`; the README regression was discarded before cutting the branch and is NOT part of C8.
- **Follow-ups created:** none new. IMP-A5 (config-file toggle for `PUSH_VERIFY_REMOTE`) remains the documented future work to flip the flag without editing source.
