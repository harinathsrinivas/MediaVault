---
title: "C9 — Atomic cmd_replace (two-rename)"
type: prerequisite-task
improvement: IMP-C9
tier: C
role: prerequisite
order: 1
status: not-started
branch: fix/atomic_replace
feature: auto-rollback
tags: [claude, mediavault, prereq, tier/C, status/not-started]
created: 2026-05-28
---

# C9 — Atomic `cmd_replace` (two-rename)

> **At a glance:** Close the crash window in `cmd_replace` where the disk has
> neither the original nor the dummy. This hardens the single true
> point-of-no-return for the whole archive pipeline. **Do this first.**
> Related: [[RELATED_IMPROVEMENTS]] · [[FAILURE_ANALYSIS]] (Example B) · [[_TRACKER]]

## Claude Code prompt (paste into a fresh session)

```
Use the planner agent.

Task: Plan the implementation of improvement IMP-C9 ("Atomic cmd_replace via two-rename pattern") from improvements_tierC.md.

Read these FIRST, in this order, before planning:
1. improvements_tierC.md -> the IMP-C9 section. This is the spec for WHAT and WHY.
2. docs/feature-auto-rollback/README.md, then docs/feature-auto-rollback/RELATED_IMPROVEMENTS.md ("C9" subsection), then docs/feature-auto-rollback/FAILURE_ANALYSIS.md (Example B). C9 is a PREREQUISITE for an upcoming auto-rollback feature: cmd_replace's deletion of the original is the single true point-of-no-return, and C9 hardens it. RELATED_IMPROVEMENTS tells you what "seam" to leave behind.
3. ARCHITECTURE.md and the code: cmd_replace in main.py (~857-904) — make_video_dummy temp at ~872, os.remove(original) retry loop ~880-897, final os.rename ~899, status=archived ~901, and the existing 3-retry PermissionError handling.

What to build: replace "write dummy temp -> delete original -> rename dummy into place" (which leaves the disk with NEITHER file in the gap) with the two-rename pattern: (1) write dummy temp; (2) rename original -> <original>.tobedeleted (atomic on NTFS); (3) rename dummy -> original (atomic); (4) delete <original>.tobedeleted. A crash must always leave EITHER the original OR the dummy, never nothing.

Constraints (full list in docs/feature-auto-rollback/README.md):
- Branch from origin/main. Suggested branch: fix/atomic_replace.
- Happy path byte-for-byte identical for the success case; preserve the 3-retry PermissionError logic and return values.
- Tests in tests/, COPIES only — never touch real C:\Media\{Movies,Series,Anime} or real C:\Media\library_*.json. Sandbox via monkeypatched constants; simulate a crash between the two renames.
- Surgical: only cmd_replace + its tests; don't touch archive/.
- Leave the seam: keep the commit point (first rename original -> .tobedeleted) clearly identifiable so the later auto-rollback work sets the replace point-of-no-return there.

DOCUMENTATION: save your PLAN.md into docs/feature-auto-rollback/C9-atomic-replace/PLAN.md, and put all task artifacts (decisions, test notes) in that same subfolder. When implementation completes, fill in the "Completion report" at the bottom of docs/feature-auto-rollback/C9-atomic-replace/C9-atomic-replace.md. (If your orchestration flow reads /PLAN.md at the repo root, keep that copy in sync.)

Pause and ask me about open decisions, at minimum: what to do if deleting .tobedeleted fails (leave / log / defer to cleanup); how to handle a stale .tobedeleted left by a prior crash on the next run.

Use Opus for the rename-sequence step if tricky; expect mostly single-executor (multi-candidate only if approaches genuinely differ, per your guardrails).

Deliverables: produce PLAN.md only (no code, no branches) with steps + tests + verification + "Open Decisions". Note that on implementation IMP-C9 must be marked done in improvements_tierC.md, the architect updates ARCHITECTURE.md/README if needed, and I want the branch name, a PR to main, and manual test commands at the end.
```

## What & why
Today `cmd_replace` deletes the original before renaming the dummy in; a kill in
that gap leaves no file at the path. Two atomic renames remove the gap.

## Relationship to auto-rollback / seam to leave
The original's deletion is the real point-of-no-return (see [[FAILURE_ANALYSIS]]
Example B). After C9 the commit becomes the first rename — keep that step
identifiable so rollback can pin the PONR there. Details in
[[RELATED_IMPROVEMENTS]] → C9.

## Definition of Done
- [ ] Planner run; `PLAN.md` saved in this subfolder; open decisions confirmed by me
- [ ] Branched `fix/atomic_replace` off `origin/main`
- [ ] Two-rename implemented; success path unchanged; 3-retry PermissionError preserved
- [ ] Tests in `tests/` (copies only) incl. crash-between-renames; passing
- [ ] Seam left: first rename is the identifiable commit point
- [ ] `IMP-C9` marked done in `improvements_tierC.md`
- [ ] `ARCHITECTURE.md` / `README` updated if behavior is documented there
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
