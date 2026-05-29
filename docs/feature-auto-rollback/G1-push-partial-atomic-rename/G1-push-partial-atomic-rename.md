---
title: "G1 — Push .partial + atomic remote rename + mvmeta"
type: prerequisite-task
improvement: IMP-G1
tier: G
role: prerequisite
order: 3
status: done
branch: feature/push_partial_atomic_rename
feature: auto-rollback
tags: [claude, mediavault, prereq, tier/G, status/done]
created: 2026-05-28
---

# G1 — Push `.partial` + atomic remote rename + `mvmeta`

> **At a glance:** Upload each chunk to `<final>.partial` then `adb shell mv` to
> the final name, so Google Photos never sees a partial upload as complete; plus
> a remote `.mvmeta.json` for disaster recovery. Removes the wrinkle that blocks
> a clean push rollback. Bigger change (touches the upload protocol).
> Related: [[RELATED_IMPROVEMENTS]] · [[FAILURE_ANALYSIS]] (Example A) · [[_TRACKER]]

## Claude Code prompt (paste into a fresh session)

```
Use the planner agent.

Task: Plan the implementation of improvement IMP-G1 ("Adopt rclone chunker patterns for push reliability") from improvements_tierG.md.

Read these FIRST, in this order, before planning:
1. improvements_tierG.md -> the IMP-G1 section. The spec for WHAT and WHY.
2. docs/feature-auto-rollback/README.md, then docs/feature-auto-rollback/RELATED_IMPROVEMENTS.md ("G1" subsection), then docs/feature-auto-rollback/FAILURE_ANALYSIS.md (Example A). G1 is a PREREQUISITE for the upcoming auto-rollback feature: it removes the "a partially-uploaded chunk looks complete to Google Photos" wrinkle, which is what makes a clean push rollback safe. RELATED_IMPROVEMENTS tells you the seam to leave.
3. ARCHITECTURE.md and the code: cmd_push in main.py, the upload loop (~754-806) — adb push ~770, per-chunk local os.remove ~775-779, split_info written ~716-720.

What to build (two rclone patterns): (1) upload each chunk to a temporary remote name <final>.partial, then "adb shell mv" it to the final name (atomic remote rename) so Google Photos never indexes a partial as a complete chunk; (2) write a <base>.mvmeta.json sidecar alongside the chunks on the phone, mirroring split_info, so the library can be rebuilt from the remote if the local library JSON is lost.

Constraints (full list in docs/feature-auto-rollback/README.md):
- Branch from origin/main. Suggested branch: feature/push_partial_atomic_rename.
- Keep the success-path RESULT identical (all chunks end on the phone under final names; library state unchanged) even though the upload mechanism changes. Do NOT change the failure contract callers rely on (a failed push must still return the same False/exception signal).
- Tests in tests/, COPIES only — never touch real C:\Media files or real library_*.json; mock/monkeypatch adb (subprocess.run) so no real device is needed.
- Surgical: cmd_push upload loop + the mvmeta writer + tests; don't touch archive/.
- Leave the seam: make the remote naming convention (final vs .partial) discoverable so auto-rollback can enumerate/remove only .partial remnants on a push rollback; keep split_info and .mvmeta.json in sync.

DOCUMENTATION: save your PLAN.md into docs/feature-auto-rollback/G1-push-partial-atomic-rename/PLAN.md; keep all task artifacts there; fill the "Completion report" in docs/feature-auto-rollback/G1-push-partial-atomic-rename/G1-push-partial-atomic-rename.md when done. (Keep /PLAN.md at root in sync if your orchestrator reads it.)

This changes the remote upload protocol — pause and ask me about open decisions, at minimum: how resume interacts with a leftover .partial on the phone; whether to verify remote size/hash after the mv; the .mvmeta.json schema; back-compat with chunks already pushed under the old naming.

Use Opus for the protocol-change step. Multi-candidate only if strategies genuinely differ (e.g., post-upload verification), per your guardrails.

Deliverables: PLAN.md only (no code, no branches) with steps + tests + verification + "Open Decisions". Note IMP-G1 is marked done in improvements_tierG.md on implementation, the architect updates ARCHITECTURE.md/README (documented behavior change), and I want branch name, PR to main, and manual test commands at the end.
```

## What & why
Chunks currently land under final names immediately, so a partial upload can be
ingested by Photos as complete. `.partial` + atomic remote rename fixes that;
`.mvmeta.json` enables rebuild-from-remote.

## Relationship to auto-rollback / seam to leave
Makes [[DECISIONS]] open item O-1 option 2 (full push rollback) safe — rollback
can delete only `.partial` remnants. Keep naming discoverable; keep `split_info`
and `.mvmeta.json` in sync. Details: [[RELATED_IMPROVEMENTS]] → G1.

## Definition of Done
- [x] Planner run; `PLAN.md` in this subfolder; open decisions confirmed
- [x] Branched `feature/push_partial_atomic_rename` off `origin/main`
- [x] `.partial` upload + atomic `adb shell mv` + `.mvmeta.json` implemented
- [x] Success-path result identical; failure contract unchanged
- [x] Tests in `tests/` (mocked adb, copies only); passing
- [x] Seam left: discoverable remote naming; metadata in sync
- [x] `IMP-G1` marked done in `improvements_tierG.md`
- [x] `ARCHITECTURE.md` / `README` updated (behavior change)
- [x] PR to `main` opened
- [x] Completion report below filled in

## Completion report
- **Branch:** `feature/push_partial_atomic_rename` (off `origin/main`)
- **PR:** opened to `main` — see PR link in the final orchestrator summary / `gh pr view`
- **Merged commit:** not yet merged (PR open, awaiting review/merge to `main`)
- **Files changed:**
  - `main.py` — added `PARTIAL_SUFFIX` / `MVMETA_SUFFIX` constants and `MVMETA_SCHEMA_VERSION`; added `write_remote_mvmeta()` helper; rewired the `cmd_push` upload loop to push to `<final>.partial` then `adb shell mv` to the final name (both paths single-quote-escaped); call the mvmeta writer on full success before flipping state.
  - `tests/test_cmd_push_partial.py` — new, 5 tests, fully-mocked adb (`FakeAdb` recorder); no `conftest.py` change needed.
  - `ARCHITECTURE.md` — §6.5 (on-phone sidecar), §7.5 (upload loop steps 6-7), §10 Stage 2 step 7-9, §14 config table.
  - `README.md` — push bullet extended with partial-upload safety + remote sidecar.
  - `improvements_tierG.md` — IMP-G1 `Status: done`.
- **Tests added:** 5 in `tests/test_cmd_push_partial.py` — (1) happy path (push-to-`.partial` then `shell mv` per chunk, one `.mvmeta.json` push, state flips, `_parts` cleaned), (2) mid-push failure (returns `False`, no `mv` for failed chunk, surviving chunks kept for resume), (3) `mv` failure treated like push failure, (4) mvmeta-write failure does NOT fail the push, (5) failure-contract parity (no exception escapes). Full suite: 20 passed (15 prior C9/C11 + 5 new).
- **Manual test commands:**
  - `pytest -q` and `pytest tests/test_cmd_push_partial.py -v` (no device needed).
  - `python -c "import main; print(main.PARTIAL_SUFFIX, main.MVMETA_SUFFIX)"` → `.partial .mvmeta.json`.
  - On-device (real Pixel, NON-destructive throwaway entry): `python main.py push <test_id> SIZE_MB 50` against a small multi-chunk file; on the phone confirm chunks appear as `*.partial` then get renamed, and one `*.mvmeta.json` lands in `/sdcard/Media/...`. Interrupt mid-push, confirm a `.partial` remnant, re-run `push` to resume, and confirm Google Photos did not back up the `.partial`. `adb shell cat '/sdcard/Media/.../<base> [uid].mvmeta.json'` returns the expected JSON.
- **Open decisions resolved:** (1) Resume re-uploads to `.partial`, overwriting any stale partial — pure local `_parts/` resume, no remote `ls`. (2) No post-`mv` verification — trust the exit code (hashing is IMP-C8). (3) Full mvmeta schema, UID-tagged name `<base> [<short_id>].mvmeta.json`, written for non-split single-file uploads too (1-element `chunks` list). (4) No back-compat migration of the 412 existing archived remotes; new pushes only.
- **Notes / surprises:** mvmeta write is best-effort — a sidecar push failure logs a WARNING and returns `False` but cannot flip a successful push to `False` (verified by test 4). The `_parts` local delete now keys on the `mv` succeeding, not just the push. No `cmd_push` signature or caller change. Atomicity of `adb shell mv` on the phone's sdcardfs/FUSE layer (same-dir, metadata-only rename) and whether Google Photos ignores the `.partial` extension both still need on-device confirmation on the real Pixel (the crux of the pattern).
- **Follow-ups created:** none. The rebuild-from-remote tool that consumes `.mvmeta.json`, post-push hash/size verification (IMP-C8), retry/backoff (IMP-C2), and the chunk-filename 260-char path audit remain out of scope, as does the auto-rollback feature itself (this task only leaves the seam).
