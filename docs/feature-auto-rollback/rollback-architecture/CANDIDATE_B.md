# Candidate B — Explicit Compensating-Action Stack (`UndoStack`)

## Architecture

A small `UndoStack` holds a LIFO list of `(callable, label)` compensating actions.
As each command performs a forward mutation it immediately pushes the *inverse*
closure right next to it (`undo.push(_undo_rmtree(parts_dir), "rm _parts/")`). On a
reversible failure `undo.run()` replays the actions last-in-first-out, so artifacts
are undone in the exact reverse order of creation (e.g. the child-link is unlinked
*before* the this-run parent is considered for deletion, which makes the D-7 "0
children" check correct without a snapshot). Crossing the PONR calls
`undo.mark_point_of_no_return()`, which **clears the stack** — past the PONR there is
nothing to undo, so a later failure raises `RollbackHardFail(state, reason,
resume_cmd)` naming the existing `fetch_restore <id>` (N-2).

Unlike Candidate A there is **no global snapshot**: each inverse captures exactly the
state it needs in its own closure. Library-field reverts are expressed as small
closures (`_undo_split_info`, `_undo_entry`, `_undo_child`, `_undo_parent`) that
re-`save_library()`. Two shared helpers (`_undo_remove_file`, `_undo_rmtree`) cover
the file/dir cases. Placement: `main.py` only (N-4); `mvcommon.py` untouched.

## Integration (wrapping, not rewriting — D-4)

- **cmd_prep**: no PONR. Each created artifact (uid, sha256, parent, child-link,
  entry) pushes its inverse as it is created; the body runs in a `try` and any
  failure calls `undo.run()`. Created-this-run guards (`uid_preexisted` etc.) mean a
  pre-existing sidecar gets no inverse. The child unlink is pushed before the parent
  delete, so LIFO replay leaves the parent at 0 children when its inverse runs (D-7).
- **cmd_push**: no rollback PONR (O-1). `_parts/`/`checksums/` push `_undo_rmtree`
  inverses only if they did not pre-exist; `split_info` pushes a pop-inverse only if
  it did not pre-exist (a resume `_parts/` therefore never has an inverse and is never
  removed). `any_upload_done` flips True after the first chunk lands; a pre-upload
  failure replays the stack, a post-upload failure prints the O-1 `push <id>` message.
- **cmd_replace**: PONR at the commit rename. The dummy temp's inverse is pushed up
  front; `mark_point_of_no_return()` clears the stack the instant the rename succeeds.
  A later failure raises `RollbackHardFail` naming `fetch_restore`. C9 stale-sweep
  untouched.
- **cmd_restore (split)**: the merge is pre-PONR; on a merge failure the reproducible
  target output's inverse is pushed and replayed (chunks kept for re-merge). C11
  corrupt-chunk quarantine reused as-is. `mark_point_of_no_return()` fires after the
  library save, before the chunk delete. Standard path unchanged (no PONR).
- **Orchestrators**: identical unification to the other candidates —
  `cmd_prep_push_rep` drops the ad-hoc cleanup and delegates; `cmd_prep_push_rep_season`
  replaces the bare break with completed-items-stay + a reconstructed resume range
  (handles `.5`).

## Self-critique across the Judge criteria

1. **Correctness & safety**: strong, and arguably the most *locally* auditable — the
   inverse sits next to its forward action, so a reviewer verifies a create/undo pair
   in one place. LIFO ordering makes the D-7 parent/child interaction fall out
   naturally. Risk: an inverse closure that captures a loop variable or stale value
   can revert the wrong thing (mitigated here with default-arg binding `_p=parent_id`
   and by closing over the live `library` dict).
2. **Minimal happy-path intrusion (D-4)**: medium — similar diff size to A. The
   `try` wrapper plus `undo.push(...)` lines interleave with the happy path, but each
   push is a single adjacent line, which some maintainers find *less* intrusive than
   A's separate registration vocabulary.
3. **Crash-survival & debuggability**: weak, same class as A — the stack is in memory
   only. A process kill mid-command loses the stack; recovery relies on the existing
   re-run/resume semantics (C9 stale-sweep, push resume). A kill mid-`run()` can leave
   a partially-undone set; `run()` reports which actions failed but cannot resume.
4. **Readability/maintainability**: arguably the best of the three for a solo
   maintainer — there is no central registry to keep in sync with the body; "what
   gets undone" is literally the list of `undo.push` lines you can read top to bottom.
   The cost is several small closures per command.

### Worked scenario — reversible split-push pre-upload
Split succeeds → `_undo_rmtree(parts_dir)`, `_undo_rmtree(checksum_dir)`, and
`_undo_split_info` are pushed. The first `adb push` fails with `any_upload_done`
False → `undo.run()` replays them LIFO (pop split_info, rm checksums/, rm _parts/);
master + `local_ready` entry untouched. (`test_push_split_fail_before_upload_rolls_back`.)

### Worked scenario — irreversible replace post-commit
The dummy-temp inverse is pushed; the commit rename succeeds →
`mark_point_of_no_return()` clears the stack. The next rename raises → `crossed_ponr`
True → `RollbackHardFail(resume_cmd="fetch_restore <id>")`. C9 leaves the bytes at
`.tobedeleted`. (`test_replace_fail_post_ponr_hard_fails`.)
