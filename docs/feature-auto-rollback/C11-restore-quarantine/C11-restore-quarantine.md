---
title: "C11 — Restore hash-mismatch quarantine"
type: prerequisite-task
improvement: IMP-C11
tier: C
role: prerequisite
order: 2
status: not-started
branch: feature/restore_quarantine
feature: auto-rollback
tags: [claude, mediavault, prereq, tier/C, status/not-started]
created: 2026-05-28
---

# C11 — Restore hash-mismatch quarantine

> **At a glance:** On a SHA256 mismatch during restore, move the bad file to
> `restore/quarantine/` instead of leaving it (where it traps the next fetch).
> Restore is now in scope for auto-rollback, so this is the restore-side
> "clean state on failure" behavior.
> Related: [[RELATED_IMPROVEMENTS]] · [[FAILURE_ANALYSIS]] (Example C) · [[_TRACKER]]

## Claude Code prompt (paste into a fresh session)

```
Use the planner agent.

Task: Plan the implementation of improvement IMP-C11 ("Hash-mismatch quarantine in cmd_restore") from improvements_tierC.md.

Read these FIRST, in this order, before planning:
1. improvements_tierC.md -> the IMP-C11 section. The spec for WHAT and WHY.
2. docs/feature-auto-rollback/README.md, then docs/feature-auto-rollback/RELATED_IMPROVEMENTS.md ("C11" subsection), then docs/feature-auto-rollback/FAILURE_ANALYSIS.md (Example C). C11 is a PREREQUISITE for the upcoming auto-rollback feature, which now covers the restore side: "leave restore/ in a clear, self-healing state on failure" IS the restore expression of rollback. RELATED_IMPROVEMENTS tells you the seam to leave.
3. ARCHITECTURE.md and the code: cmd_restore in main.py (~1034-1123), standard-path hash check ~1096-1098 and split-path verification during merge; plus the os.path.exists "skip re-download" check in mainfetch.py that currently traps the user.

What to build: on a SHA256 mismatch during restore, instead of leaving the bad file in <folder>/restore/, move it to <folder>/restore/quarantine/<filename>.<timestamp> and print a clear, greppable diagnostic ("Hash mismatch. Bad file quarantined at <path>. A fresh fetch will re-download."). A re-fetch then self-heals.

Constraints (full list in docs/feature-auto-rollback/README.md):
- Branch from origin/main. Suggested branch: feature/restore_quarantine.
- Happy path (successful restore) byte-for-byte identical.
- Tests in tests/, COPIES only — never touch real C:\Media files or real library_*.json; simulate a hash mismatch in a sandboxed restore/ folder.
- Surgical: cmd_restore + tests; don't touch archive/. A cleanup_quarantine command is OUT of scope.
- Leave the seam: centralize "where does a bad restore file go" (one helper / one predictable path) so the later auto-rollback restore handling reuses it.

DOCUMENTATION: save your PLAN.md into docs/feature-auto-rollback/C11-restore-quarantine/PLAN.md and keep all task artifacts in that subfolder; fill the "Completion report" in docs/feature-auto-rollback/C11-restore-quarantine/C11-restore-quarantine.md when done. (Keep /PLAN.md at root in sync if your orchestrator reads it.)

Pause and ask me about open decisions, at minimum: split-path chunk mismatch handling (which file is quarantined, whether the partial merge output is removed); whether the merge-verification failure path is also covered.

Deliverables: PLAN.md only (no code, no branches) with steps + tests + verification + "Open Decisions". Note IMP-C11 is marked done in improvements_tierC.md on implementation, the architect updates docs if needed, and I want branch name, PR to main, and manual test commands at the end.
```

## What & why
A corrupt downloaded file left in `restore/` makes the next fetch skip
re-downloading it, trapping the user. Quarantining it lets a fresh fetch heal.

## Relationship to auto-rollback / seam to leave
Restore is in scope for rollback (see [[DECISIONS]] D-1). Centralize the
quarantine path/helper so rollback reuses it. Details: [[RELATED_IMPROVEMENTS]] → C11.

## Definition of Done
- [ ] Planner run; `PLAN.md` in this subfolder; open decisions confirmed
- [ ] Branched `feature/restore_quarantine` off `origin/main`
- [ ] Quarantine-on-mismatch implemented; success path unchanged
- [ ] Tests in `tests/` (copies only) for mismatch -> quarantine; passing
- [ ] Seam left: single predictable quarantine path/helper
- [ ] `IMP-C11` marked done in `improvements_tierC.md`
- [ ] `ARCHITECTURE.md` / `README` updated if needed
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
