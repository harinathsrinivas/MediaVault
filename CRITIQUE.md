# Candidate B Self-Critique — IMP-D19 Step 3 (Extras upload phase)

## Approach taken
ISOLATED DUPLICATION. Added two standalone functions in `main.py` immediately
before `cmd_push` — `push_one_extra(...)` and `push_title_extras(...)` — that
re-implement ONLY the upload steps an extra needs (per-item split by an
independent chunk size, chunk hashing, the `.partial`+rename+`mvcommon.retry`
idiom, the `write_remote_mvmeta` sidecar, the `uploaded`/`onboarded` flips),
WITHOUT cmd_push's leaf-specific machinery (no `chunk_range`, no eager re-hash,
no `temp_dir`, no `RollbackJournal`/PONR). `cmd_push`'s body is byte-for-byte
unchanged except its single `# IMP-D19 Step 3` marker comment, which became a
two-line `if extras:` wire (`main.py:4649-4650`). The extras path is its own
O-1-resumable-per-file phase with NO point-of-no-return, so the main rollback
contract (E1) is trivially preserved.

## Design decisions and tradeoffs
- **Per-item chunk dir nested under `SPLIT_DIR_NAME`** (`<extra folder>/_parts/<short_id>`)
  rather than the flat `_parts` cmd_push uses. Two extras share a folder
  (`Specials/` holds 2 files), so a flat dir would mix chunks and break
  resume-detection + `split_video_file`'s `listdir`-based return. Nesting under
  `_parts` keeps it pruned by `scan_extras_folders` (which excludes
  `SPLIT_DIR_NAME`), so leftover chunks are never re-scanned as new extras.
  Alternative considered: a sibling `_parts_<id>` dir — rejected because the
  scan exclude is exact-match and would NOT prune it (latent re-scan bug).
- **"Archived main → only extras" handled in the `push` DISPATCH, not cmd_push**
  (`main.py`, push arm). Detecting an archived main inside cmd_push would have
  violated the byte-for-byte constraint and added blast radius to the proven
  path. The dispatch checks `status == "archived"` and, if so, calls
  `push_title_extras` directly and skips `cmd_push`. The no-extras push path is
  behaviorally identical (only `resolve_device(dev)` got hoisted into a local).
- **Independent chunk size resolution** lives in `push_title_extras`:
  `extras_size` tuple wins (`('NONE',None)`→whole-file; `('SIZE_MB'|…,val)`→split);
  `extras_size is None` inherits the command's `split_method`/`split_val`; no
  main split either ⇒ whole-file. This is exactly Card B's default.
- **Chunk hashes persisted into the item's `split_info` BEFORE the upload loop**
  (then `save_library`), so a crash mid-upload leaves a resumable item whose
  hashes are already recorded — mirrors cmd_push's pre-upload split_info save.
- **Dropped two cmd_push features for leanness**: no local `checksums/` `.sha256`
  sidecar for extras chunks (hashes live in `split_info`, which the Step-6
  restore verify will read), and no `PUSH_VERIFY_REMOTE` post-push check. Both
  are parity gaps I accepted to keep `push_one_extra` minimal.

## Strengths
- **Maximal score on blast-radius + rollback axes (criteria 2 & 3):** cmd_push's
  upload loop, journal, PONR, and O-1/O-2 failure branches are untouched —
  verified by reading `main.py:4636-4651` and by 24 green `test_cmd_push_*` tests
  + 72 green smoke tests. No `RollbackJournal`/`mark_point_of_no_return` call
  appears anywhere in the new code.
- **Correctness verified (criterion 1):** whole-file extras land at the mirrored
  remote subfolder with the `<name> [short_id]<ext>` rename and flip
  `uploaded`/`onboarded`; chunk-resume re-pushes pre-seeded chunks to the
  mirrored dir, deletes them, cleans the per-item `_parts/<short_id>` dir, and
  flips status (both exercised via `mock_device`). Remote path mirrors the extra
  folder via `os.path.relpath(extra_folder, LOCAL_ROOT)` with cmd_push's
  except→basename fallback (`push_one_extra`).
- **All 5 wiring sites covered** (`cmd_push` 4649, `cmd_push_group` 4787,
  `cmd_prep_push_rep`, `cmd_prep_push_rep_season`, `cmd_add_extras`), with a
  fresh `load_library()` before the extras push in the group/season/autopilot
  sites so stale in-memory libraries can't clobber the per-episode saves.
- **Idempotent / resumable:** `push_title_extras` only processes
  `uploaded=False` items; a re-run after full success is a clean no-op.

## Weaknesses (honest about the duplication — my weak axis, criterion 4)
- The `.partial`+rename+`retry` idiom, the `adb_base`/single-quote escaping, the
  `REMOTE_ROOT`/`relpath` math, and the chunk-name/mvmeta logic now exist TWICE
  (cmd_push + `push_one_extra`). A future upload-protocol fix (new verify step,
  quoting fix, backoff change) must be applied in both or extras silently drift.
  This is the deliberate tradeoff of approach B; candidate A's shared
  `_upload_file` would not have it.
- Parity gaps vs main content: no local `checksums/` sidecar and no
  `PUSH_VERIFY_REMOTE` for extras (see above).
- No group-level peak-disk pre-flight in `push_title_extras` (cmd_push_group/
  season do one). Each `push_one_extra` still guards itself with
  `_free_space_ok`, so the worst case is a per-item space failure mid-run rather
  than an upfront stop.
- The split path's `mkvmerge` step was not exercised end-to-end (no mkvmerge in
  the test env); only the resume-from-pre-seeded-chunks path was — but that
  exercises the same upload loop, and `split_video_file` is the identical
  primitive the proven main path uses.
- The dispatch archived-skip keys on `status == "archived"`; a stale status
  (dummy on disk but status not flipped) won't trigger the skip. This is not a
  regression (cmd_push doesn't detect dummies today either), just an uncaught edge.

## Tests run
```
$ python -c "import main; print('import OK')"
import OK

$ python -m pytest tests/test_cmd_push_partial.py tests/test_cmd_push_mock_device.py -q
11 passed in 1.11s

$ python -m pytest tests/smoke -q
72 passed, 1 warning in 23.60s

$ python -m pytest tests/test_cmd_push_retry.py tests/test_cmd_push_verify.py -q
13 passed in 0.85s

# final sweep: 4 push files + cli parsers + entry schema guard
$ python -m pytest tests/test_cmd_push_partial.py tests/test_cmd_push_mock_device.py \
    tests/test_cmd_push_retry.py tests/test_cmd_push_verify.py \
    tests/test_cli_parsers.py tests/test_entry_schema_guard.py -q
55 passed in 2.61s

# throwaway extras sanity (whole-file push + chunk-resume via mock_device),
# deleted after running — formal coverage is Steps 9-11:
2 passed in 0.27s
```
(The throwaway test initially "failed" only due to the documented Windows glob
gotcha — `rglob("name [id].mkv")` is a character class; switched to
`rglob("*.mkv")` + name filter and both passed. The push logic itself was
correct on the first run, as the captured stdout `✅` lines showed.)

## Confidence
high

Reasoning: the new code duplicates a battle-tested idiom and was verified for
both the whole-file and chunk-resume upload paths against `mock_device`, while
the proven `cmd_push` path is provably untouched (read-confirmed + 24 push tests
+ 72 smoke green). The honest caveat: formal extras coverage (Steps 9-11) isn't
in this step, and the real `mkvmerge` split→chunk path was validated only via the
resume branch (same upload loop, same `split_video_file`), not an end-to-end
split. On the single-source-of-truth axis I am deliberately weaker than
candidate A by design.
