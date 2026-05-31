# Candidate C — On-Disk Operation Journal (`RollbackJournal`)

## Architecture

Each command opens a durable per-run journal file
`<folder>/.mediavault_txn.json` and appends a record describing each intended
mutation **before** performing it. Records are a small fixed vocabulary
(`create_file`, `create_dir`, `create_entry`, `set_field`, `link_child`,
`create_reproducible`) plus a `crossed_ponr` flag. Every append is flushed durably
(write-temp → `fsync` → `os.replace`), so a hard process kill leaves a complete,
consistent inverse record on disk. On a reversible failure `rollback(library)`
replays `_replay_inverses()` LIFO, reverts the in-memory library, saves, and deletes
the journal. On clean success `commit()` deletes the journal. Crossing the PONR
writes the marker and refuses further rollback; a post-PONR failure raises
`RollbackHardFail` naming `fetch_restore <id>` (N-2) and leaves the journal on disk
for inspection.

The distinguishing capability is `recover_journal(folder_path)`: an explicit
crash-recovery entry point that, if a journal survives from a killed run that never
crossed its PONR, replays its inverses to finish the interrupted rollback. This
covers the `save_library`-mid-rollback / process-kill edge that PLAN.md Judge
criterion 3 calls out — the in-memory candidates (A/B) cannot. `recover_journal()` is
**not** called on the happy path, so unrelated commands stay byte-identical (D-4).

Placement: `main.py` only (N-4); `mvcommon.py` untouched.

## Integration (wrapping, not rewriting — D-4)

- **cmd_prep**: no PONR. Journal opened; each created sidecar / parent-link / entry is
  recorded before the mutation. Created-this-run guards mean pre-existing artifacts get
  no record. `link_child` carries `created_parent` so the inverse applies D-7. `commit()`
  on success; `rollback()` on failure.
- **cmd_push**: no rollback PONR (O-1). `create_dir` records for `_parts/`/`checksums/`
  only if not pre-existing; a `set_field("split_info", existed=False)` record only if it
  did not pre-exist. A resume `_parts/` is never recorded → never removed. `any_upload_done`
  flips after the first chunk; pre-upload failure replays the journal, post-upload failure
  `commit()`s the journal and prints the O-1 `push <id>` message (the artifacts are now
  legitimate resumable state).
- **cmd_replace**: PONR at the commit rename. The dummy temp is recorded up front;
  `mark_point_of_no_return()` writes the marker after the rename. Post-PONR raises
  `RollbackHardFail`. C9 stale-sweep untouched.
- **cmd_restore (split)**: merge is pre-PONR; the reproducible target is recorded as
  `create_reproducible` before merging, so a merge failure removes it and keeps chunks.
  C11 quarantine reused. `mark_point_of_no_return()` + `commit()` before the chunk delete.
  Standard path unchanged (no PONR).
- **Orchestrators**: same unification as the other candidates (drop ad-hoc cleanup;
  season completed-kept + reconstructed resume range, handles `.5`).

## Self-critique across the Judge criteria

1. **Correctness & safety**: strong, same artifact-scoping discipline as A/B (created-this-run
   only; resume `_parts/` never recorded). The durable journal additionally makes the revert
   recoverable rather than purely best-effort. Risk: the journal is a second on-disk artifact
   that must itself be managed (committed/deleted) — a missed `commit()` leaks a journal file
   (caught by the baseline oracle staying green and by the crash-recovery test).
2. **Minimal happy-path intrusion (D-4)**: the *largest* runtime intrusion of the three — every
   create/mutate is preceded by a journal append that does an fsync + `os.replace`. The happy
   path is behaviorally identical (oracle green) but does extra disk I/O it did not before, and
   writes a transient dot-file into each media folder. For a single-user archival tool this
   cost is negligible, but it is real and worth the user weighing.
3. **Crash-survival & debuggability**: clearly the best. A hard kill mid-command or
   mid-rollback leaves a consistent, human-readable `.mediavault_txn.json` that `recover_journal()`
   can replay (proved by `test_journal_survives_hard_kill_and_recovers`). The journal is also a
   forensic record of exactly what a run did. This is the criterion C exists to win.
4. **Readability/maintainability**: medium. The journal vocabulary is a clean, closed set and the
   inverse replay lives in one function (`_replay_inverses`), which is easy to audit centrally —
   but it adds the most concepts (durable file format, recovery entry point) for a solo maintainer
   to keep in mind, and the dot-file lifecycle is one more thing that can go wrong.

### Worked scenario — reversible split-push pre-upload
Split succeeds → the journal holds `create_dir _parts/`, `create_dir checksums/`,
`set_field split_info`. The first push fails with `any_upload_done` False →
`rollback()` replays them LIFO (pop split_info, rmtree checksums/, rmtree _parts/),
saves, deletes the journal; master + `local_ready` entry untouched.
(`test_push_split_fail_before_upload_rolls_back`.)

### Worked scenario — irreversible replace post-commit
The dummy-temp `create_file` is journalled; the commit rename succeeds →
`mark_point_of_no_return()` writes the marker. The next rename raises → `crossed_ponr`
True → `RollbackHardFail(resume_cmd="fetch_restore <id>")`; the journal (with its PONR
marker) is left on disk so `recover_journal()` will correctly DECLINE to undo it.
(`test_replace_fail_post_ponr_hard_fails`.)
