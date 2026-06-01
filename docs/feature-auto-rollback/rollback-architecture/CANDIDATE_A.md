# Candidate A — Snapshot / Transaction Context-Manager (`RollbackContext`)

## Architecture

A single class, `RollbackContext`, models one command operating on one id as a
transaction. At construction it captures the D-6 snapshot (entry/parent existence,
prior `status`/`uploaded`, child-link state, `split_info` presence, deep-copied
prior entry + parent dicts). The wrapped command body **registers** each artifact
it creates as it creates it (`created_path`, `created_dir`, `created_entry`,
`created_split_info`, `created_parent`, `linked_child`). A `mark_point_of_no_return()`
toggle flips the context past its PONR. On a reversible failure the body calls
`ctx.rollback()`, which:

1. deletes the registered created-this-run files then dirs (chmod + remove / rmtree),
2. reverts the in-memory library — parent season_map per D-7 (delete only if this run
   created it AND removing this child leaves 0 children, else unlink + recompute
   `total_episodes`), then the entry (pop if created-this-run, else restore the deep-
   copied prior snapshot, else pop just `split_info` if that was the only addition),
3. `save_library()`.

A post-PONR failure instead raises `RollbackHardFail(state, reason, resume_cmd)`,
which names the existing `fetch_restore <id>` pipeline (N-2). The orchestrators
catch it and print the actionable message.

Placement: entirely in `main.py` (N-4 default); `mvcommon.py` untouched.

## Integration (wrapping, not rewriting — D-4)

- **cmd_prep**: no PONR. Parent resolved up-front so the snapshot is accurate; the
  whole body runs in a `try`; any failure (hash failure or unexpected exception)
  calls `rollback()`. Early-skips return `True` before the context exists — never
  rolled back.
- **cmd_push**: no rollback PONR (O-1). `_parts/` and `checksums/` are registered as
  created-this-run only if they did not pre-exist (a resume `_parts/` is never
  registered, so never deleted). `split_info` is registered only if it did not
  pre-exist. `any_upload_done` flips `True` after the first successful chunk
  upload+rename: a failure before it rolls back this-run artifacts; a failure after
  it leaves the partial upload and prints the O-1 `push <id>` resume-message.
- **cmd_replace**: PONR at the commit rename. The dummy temp is the only registered
  pre-PONR artifact. `mark_point_of_no_return()` fires immediately after the rename
  succeeds; a later failure is converted to `RollbackHardFail` naming `fetch_restore`.
  C9's stale-sweep is left fully intact (the wrapper does not touch it).
- **cmd_restore (split)**: the merge is pre-PONR; a merge failure (return False or
  raise) drops the reproducible target output and keeps the chunks for a re-merge.
  The C11 corrupt-chunk quarantine + reproducible-output cleanup is reused as-is.
  `mark_point_of_no_return()` fires after the library save, just before the chunk
  delete. The standard path (single `shutil.move`) is unchanged — no PONR.
- **Orchestrators**: `cmd_prep_push_rep` drops the ad-hoc `_parts` rmtree + messages
  and delegates to the wrapped `cmd_push` (O-1) and `cmd_replace` (catches
  `RollbackHardFail`). `cmd_prep_push_rep_season` replaces the bare break with
  completed-items-stay + a reconstructed resume-range command (`_season_resume_cmd`)
  that faithfully reproduces `SIZE_*`/`device`/`episodes` and handles `.5` episodes.

## Self-critique across the Judge criteria

1. **Correctness & safety**: strong. The deep-copied snapshot makes the in-memory
   revert exact; created-this-run registration means a resume `_parts/` and a
   pre-existing `split_info` are provably never deleted (covered by
   `test_push_resume_does_not_delete_preexisting_parts`). D-7 parent handling is
   explicit. Risk: the body must remember to call each `created_*` — a missed
   registration silently leaks an artifact (mitigated by the scenario tests but
   not structurally enforced).
2. **Minimal happy-path intrusion (D-4)**: medium. The happy path is byte-identical
   (the baseline oracle passes unchanged), but wrapping each body in a `try` and
   threading `ctx.created_*()` calls through the success path adds visual noise and
   indentation. This is the largest-diff candidate.
3. **Crash-survival & debuggability**: weakest of the three. The snapshot lives only
   in memory; a process kill mid-command leaves whatever artifacts exist with no
   journal to drive recovery (it relies on the same re-run/resume semantics the code
   already had — C9 stale-sweep, push resume from `_parts/`). A kill mid-`rollback()`
   can leave a half-removed artifact set; `rollback()` reports partial removal
   honestly but cannot resume itself.
4. **Readability/maintainability**: medium. One well-documented class is easy to
   understand in isolation, but the registration calls are scattered through each
   command body, so a maintainer must read the whole body to know what will roll back.

### Worked scenario — reversible split-push pre-upload
Genuine ffmpeg split succeeds; the first `adb push` fails. `_parts/`+`checksums/`
were registered (did not pre-exist), `split_info` registered, `any_upload_done`
is still False → `rollback()` rmtrees both dirs and pops `split_info`; the master
and the `local_ready` entry are untouched. (`test_push_split_fail_before_upload_rolls_back`.)

### Worked scenario — irreversible replace post-commit
The commit rename succeeds → `mark_point_of_no_return()`. The dummy-temp rename then
raises → caught, `ctx.crossed_ponr` is True → `RollbackHardFail(..., resume_cmd=
"fetch_restore <id>")`. C9 guarantees the bytes are still at `.tobedeleted`.
(`test_replace_fail_post_ponr_hard_fails`.)
