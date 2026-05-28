---
title: "G1 — Push .partial + atomic remote rename + mvmeta"
type: prerequisite-task
improvement: IMP-G1
tier: G
role: prerequisite
order: 3
status: not-started
branch: feature/push_partial_atomic_rename
feature: auto-rollback
tags: [claude, mediavault, prereq, tier/G, status/not-started]
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
- [ ] Planner run; `PLAN.md` in this subfolder; open decisions confirmed
- [ ] Branched `feature/push_partial_atomic_rename` off `origin/main`
- [ ] `.partial` upload + atomic `adb shell mv` + `.mvmeta.json` implemented
- [ ] Success-path result identical; failure contract unchanged
- [ ] Tests in `tests/` (mocked adb, copies only); passing
- [ ] Seam left: discoverable remote naming; metadata in sync
- [ ] `IMP-G1` marked done in `improvements_tierG.md`
- [ ] `ARCHITECTURE.md` / `README` updated (behavior change)
- [ ] PR to `main` opened
- [ ] Completion report below filled in

## Completion report (fill in when done)
- **Branch:**
- **PR:**
- **Merged commit:**
- **Files changed:**
- **Tests added:**
- **Manual test commands:**
- **Open decisions resolved:**
- **Notes / surprises:**
- **Follow-ups created:**
