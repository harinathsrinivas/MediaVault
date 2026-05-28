# Auto-Rollback — Related Improvements & Prerequisite Guidance

The user chose to implement the closely-related improvements **separately and
first**, then return to auto-rollback. This file describes each related item from
`improvements_tier*.md`, how it connects to auto-rollback, and — for the
prerequisites — **what seam to leave behind** so the later rollback work plugs in
cleanly.

Suggested order: **C9 → C11 → G1**, with C1 / C2 / A1 / A7 as complementary.

> **If you are an agent implementing ONE of these:** read your section below and
> `FAILURE_ANALYSIS.md`, honor the shared constraints in `README.md`, and when
> done, mark your item's status in its `improvements_tier*.md` file and note in
> the PR that it was done as an auto-rollback prerequisite.

---

## Summary table

| Imp     | Tier | One-liner                                                                  | Relation to auto-rollback                                            | Role                                |
| ------- | ---- | -------------------------------------------------------------------------- | -------------------------------------------------------------------- | ----------------------------------- |
| **C9**  | C    | Atomic `cmd_replace` via two-rename                                        | Hardens the single true point-of-no-return (Example B)               | **Prerequisite — do FIRST**         |
| **C11** | C    | Hash-mismatch quarantine in `cmd_restore`                                  | The restore-side "clean state on failure" behavior (in scope now)    | **Prerequisite**                    |
| **G1**  | G    | rclone patterns: `.partial` upload + atomic remote rename + `.mvmeta.json` | Removes the "partial upload looks complete" wrinkle in push rollback | **Prerequisite (optional, bigger)** |
| **C1**  | C    | Season auto-resume from a progress file                                    | We *print* the resume command; C1 *auto-resumes*                     | Complementary                       |
| **C2**  | C    | Exponential-backoff retry for ADB + Selenium                               | Fewer transient failures → rollback/hard-fail triggers less often    | Complementary                       |
| **A1**  | A    | Extract `mvcommon.py` (shared lib I/O + hashing)                           | Plumbing our snapshot/rollback uses                                  | Complementary (foundation)          |
| **A7**  | A    | Pytest harness with library fixtures                                       | This feature creates the first tests; A7 formalizes the harness      | Complementary                       |

---

## C9 — Atomic `cmd_replace` via two-rename  *(Tier C — do FIRST)*

**What it is.** Today `cmd_replace` writes a dummy temp, **deletes the original**,
then renames the dummy into place. Between the delete and the rename the disk has
neither file; a power-loss/kill there leaves nothing at the expected path. C9
replaces that with: (1) write dummy temp; (2) rename `original → <original>.tobedeleted`
(atomic on NTFS); (3) rename dummy → original (atomic); (4) delete `.tobedeleted`.
A crash now always leaves *either* the original *or* the dummy.

**Code it touches.** `cmd_replace` (`main.py:857-904`); the irreversible
`os.remove(original)` is currently at `main.py:884`, the final rename at `899`.

**Why it's the top prerequisite.** `cmd_replace`'s delete-of-original is the one
genuine data-loss point of no return for the whole archive pipeline
(`FAILURE_ANALYSIS.md` Example B). C9 makes that step crash-safe, which:
- lets auto-rollback treat replace as "commit point" with a clean, well-defined
  pre/post state instead of a dangerous window, and
- means the rollback hard-fail message for replace only has to cover the genuine
  post-commit case, not a torn intermediate state.

**Seam to leave for auto-rollback.** Keep the "point of no return" identifiable —
ideally the commit becomes the *first* rename (`original → .tobedeleted`).
Auto-rollback will set the replace point-of-no-return at that rename. Don't bury
the rename sequence behind a helper that swallows which step failed; rollback
needs to know whether the failure was before or after the commit rename.

---

## C11 — Hash-mismatch quarantine in `cmd_restore`  *(Tier C — prerequisite, restore now in scope)*

**What it is.** On a SHA256 mismatch during restore, instead of leaving the bad
file in `restore/` (where the next fetch may skip re-downloading it and trap the
user), move it to `restore/quarantine/<filename>.<timestamp>` and print a clear
diagnostic so a fresh fetch self-heals.

**Code it touches.** `cmd_restore` hash check (`main.py:1096-1098` for the
standard path; the split path verifies during merge). `mainfetch.py`'s
`os.path.exists` skip is what currently traps the user.

**Why it relates.** With restore now in scope (D-1), "leave the system in a clear,
self-healing state on failure" IS the restore-side expression of auto-rollback.
C11 is essentially the restore clean-state behavior; doing it first (or folding it
in) avoids two mechanisms fighting over the same `restore/` folder.

**Seam to leave.** Centralize "where does a bad restore file go" so auto-rollback's
restore handling can reuse it. Keep the quarantine path predictable and the
diagnostic message machine-greppable.

---

## G1 — rclone chunker patterns for push  *(Tier G — prerequisite, optional/bigger)*

**What it is.** Adopt two rclone `chunker` patterns: (1) upload each chunk to a
`<final>.partial` name then `adb shell mv` to the final name (atomic remote
rename) so a partially-uploaded chunk is never observable as "complete" by Google
Photos; (2) write a `<base>.mvmeta.json` remote sidecar mirroring `split_info`
for disaster recovery if the local library is lost.

**Code it touches.** `cmd_push` upload loop (`main.py:754-806`) — changes the
remote upload protocol.

**Why it relates.** It directly fixes Example A's wrinkle: today chunks 1-4 land
on the phone under their final names, so a push rollback can't be sure Photos
hasn't ingested them as complete. With `.partial` + atomic rename, an interrupted
push leaves only `.partial` files that rollback can safely `adb shell rm` without
Photos having grabbed a "complete" chunk. This is what would make **O-1 option 2
(full rollback)** safe and clean.

**Seam to leave.** Make the remote naming convention (final vs `.partial`)
discoverable so rollback can enumerate and remove only `.partial` remnants. Keep
`split_info` and the new `.mvmeta.json` in sync.

---

## Complementary (not prerequisites)

### C1 — Season auto-resume  *(Tier C)*
Auto-resumes a failed season from a `.mediavault_progress.json` instead of just
printing the resume command. Auto-rollback delivers the *messaging* on season
failure; C1 delivers *automation*. If C1 lands first, the season failure path
should integrate with its progress file rather than only printing a command.

### C2 — Exponential-backoff retry for ADB + Selenium  *(Tier C)*
Wraps `adb push` and the Selenium ops in `retry(attempts=3, backoff=(1,4,16))`.
Orthogonal: fewer transient USB/network blips means rollback/hard-fail fires less
often. Reduces the *frequency* of every scenario in `FAILURE_ANALYSIS.md`.

### A1 — Extract `mvcommon.py`  *(Tier A)*
Moves shared constants + `load_library`/`save_library`/`calculate_file_hash`/etc.
into one module imported by both `main.py` and `mainfetch.py`. Pure plumbing.
Auto-rollback's snapshot/restore uses `load_library`/`save_library`; if A1 lands
first, some rollback code may live in `mvcommon`. Not required.

### A7 — Pytest harness with library fixtures  *(Tier A)*
Formal pytest harness using the gitignored `resources/library_*.json` snapshots
as read-only fixtures. Auto-rollback creates the **first** real tests in `tests/`
and bootstraps a minimal harness (conftest, sandbox fixtures, failure-injection
monkeypatches). If you want the full harness, do A7 first and auto-rollback builds
on it; otherwise A7 later extends what auto-rollback seeds. Coordinate naming.
