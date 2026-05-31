---
title: "C11 — Restore hash-mismatch quarantine"
type: prerequisite-task
improvement: IMP-C11
tier: C
role: prerequisite
order: 2
status: done
branch: feature/restore_quarantine
feature: auto-rollback
tags: [claude, mediavault, prereq, tier/C, status/done]
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
- [x] Planner run; `PLAN.md` in this subfolder; open decisions confirmed
- [x] Branched `feature/restore_quarantine` off `origin/main`
- [x] Quarantine-on-mismatch implemented; success path unchanged
- [x] Tests in `tests/` (copies only) for mismatch -> quarantine; passing
- [x] Seam left: single predictable quarantine path/helper
- [x] `IMP-C11` marked done in `improvements_tierC.md`
- [x] `ARCHITECTURE.md` / `README` updated if needed
- [x] PR to `main` opened
- [x] Completion report below filled in

## Completion report (fill in when done)
- **Branch:** `feature/restore_quarantine` (branched off `origin/main` @ 4b7e7b6)
- **PR:** https://github.com/harinathsrinivas/MediaVault/pull/6 (base `main`)
- **Merged commit:** merged via PR #6 on 2026-05-29 (squash-merged into `main`).
- **Files changed:**
  - `main.py` — added `quarantine_restore_file(restore_folder, filename)` helper (the centralized seam) and wired it into both `cmd_restore` paths: standard single-file failure branch (quarantine + greppable diagnostic + defensive lock fallback) and split path (pre-merge per-chunk SHA256 verification → quarantine offending chunks, keep clean chunks, delete stale partial output, return False before merge).
  - `tests/test_cmd_restore_quarantine.py` — new 9-test module (6 standard + 3 split), reusing the C9 `tests/conftest.py` sandbox fixtures (conftest unchanged).
  - `ARCHITECTURE.md` — §7.7 (`cmd_restore` flow) and §12 (Error Handling) updated to describe quarantine + the helper seam.
  - `improvements_tierC.md` — IMP-C11 status flipped to `done`.
  - `docs/feature-auto-rollback/RELATED_IMPROVEMENTS.md` — noted the `quarantine_restore_file` helper name as the seam auto-rollback should reuse.
  - `STATUS.md`, `docs/feature-auto-rollback/C11-restore-quarantine/PLAN.md`, this report.
- **Tests added:** 9 (`tests/test_cmd_restore_quarantine.py`). Standard: `test_mismatch_moves_file_to_quarantine`, `test_mismatch_prints_greppable_diagnostic`, `test_mismatch_returns_false`, `test_success_path_unchanged`, `test_requarantine_no_collision`, `test_self_heal_contract`. Split: `test_split_mismatch_quarantines_offending_chunk_only`, `test_split_mismatch_deletes_partial_merge_output`, `test_split_success_path_unchanged`. Full suite (with the 6 C9 tests): 15 passed, 0 failures.
- **Manual test commands:**
  - `.venv\Scripts\python.exe -c "import main"`
  - `.venv\Scripts\python.exe -m pytest tests/ -v`
  - `.venv\Scripts\python.exe -m pytest tests/test_cmd_restore_quarantine.py -v`
  - Grep contract: `python main.py restore <sandbox-test-id> 2>&1 | findstr /C:"Bad file quarantined at"` (sandbox only — never real `C:\Media`).
- **Open decisions resolved:** All 5 from PLAN.md — (1) split path quarantines offending chunk(s) only, deletes the partial merged output; (2) split path is in scope (pre-merge per-chunk verification); (3) quarantine path `<folder>/restore/quarantine/<filename>.<YYYYmmddTHHMMSS>` (NTFS-safe, colon-free); (4) diagnostic to stdout, concise, no hash values; (5) root `/PLAN.md` left untouched (C11 plan lives only in this subfolder).
- **Notes / surprises:** `datetime` is imported as `from datetime import datetime` in `main.py`, so the helper uses `datetime.now()`. `shutil` was already imported. The single-file success path and the split all-clean merge path are byte-for-byte unchanged (guarded by `test_success_path_unchanged` and `test_split_success_path_unchanged`). The split-success test stubs `merge_video_files` to stay hermetic and deterministic; the corrupt-chunk tests never reach the merge. `mainfetch.py` was intentionally NOT modified — its existing `os.path.exists` skip self-heals once the bad file is gone from `restore/`.
- **Follow-ups created:** None. (A `cleanup_quarantine` retention/purge command remains explicitly out of scope — deferred IMP-D extension. Auto-rollback can now reuse the `quarantine_restore_file` seam.)
