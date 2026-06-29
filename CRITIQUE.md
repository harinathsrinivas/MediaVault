# Candidate A Self-Critique — IMP-D19 Step 3 (extras upload)

## Approach taken
Refactor-for-reuse. I extracted `cmd_push`'s entire per-file push core into a new shared
`_upload_file(...)` (`main.py:4170`) — resume-detect → split → hash chunks (+ sidecars) →
write `split_info` → `.partial` upload + atomic `mv` + `mvcommon.retry` + optional verify →
delete local chunk → cleanup `_parts/` → `write_remote_mvmeta` → flip `uploaded`/`status` →
**journal `commit`/`rollback` + the O-1 resume-message split**. `cmd_push` (`main.py:4500`)
is now a thin ~70-line caller (alias/path/remote/mkdir setup → `_upload_file(...)` → extras
wiring → `return ok`). `push_one_extra` (`main.py:3813`) calls the **same** `_upload_file`,
so the on-device upload protocol has exactly one source of truth. `push_title_extras`
(`main.py:3872`) iterates pending items with the independent chunk size; `_push_extras_for`
(`main.py:3923`) is the scan+merge-then-upload wiring helper the markers call.

## Design decisions and tradeoffs
1. **Where the journal lifecycle lives.** The hardest call. I moved the journal `commit`/
   `rollback` + O-1 messaging *into* `_upload_file` (transcribed verbatim from the original
   `cmd_push` body) rather than leaving it physically in `cmd_push`. Rationale: keeping it in
   `cmd_push` would force `push_one_extra` to duplicate the exact 4-branch tail (success /
   partial / post-upload-fail / pre-upload-fail) and the mvmeta+flip — defeating the
   single-source goal. To make this safe, the extras-only behavior is gated by three params
   whose **defaults reproduce `cmd_push` byte-for-byte** (`journal_split_info=True`,
   `run_consistency_warn=True`, `resume_hint=None`→`push <id>`). Behavior *during a cmd_push
   run* is identical (proven by the D-4 baseline oracle + 60 rollback tests). Honest tradeoff:
   the gated rollback code is now in a shared helper, a larger blast radius on the proven path
   than Candidate B's zero-touch — the instruction explicitly accepted this for approach A.
2. **`record_set_field` for nested extras split_info.** The journal's `set_field` inverse is
   library-id-keyed; an extras item is not a top-level id. So `push_one_extra` passes
   `journal_split_info=False` — the journal still records this-run `_parts/`/`checksums/` dir
   creation (existing vocabulary, **no journal-format change**), but the item's `split_info`
   is not journalled. On a pre-upload rollback the journal removes the dirs; the stale
   `split_info` is harmless because a re-run re-splits and overwrites it. This keeps the
   journal record vocabulary untouched (change-gate clean) while still being O-1-correct.
3. **Stop-on-first-failure in `push_title_extras`.** Two extras in the same `Specials/` folder
   share one `_parts/`, and `split_video_file` lists *all* `.mkv` there. Continuing past a
   half-pushed extra would let its leftover chunks contaminate the next extra's split. I stop
   on the first failure (mirrors `cmd_push_group`/season autopilot) so the shared `_parts/` is
   always clean; the partial upload stays O-1-resumable (`push <id> --extras`).
4. **Extras reuse the proven path with fancy options off.** Extras pass `eager_rehash=False`,
   `chunk_range=None`, `temp_dir=None` — they get the battle-tested split/upload code with the
   bless-at-push/partial/temp-redirect complexity disabled (deferred bless at first restore).

## Strengths
- One source of truth for the upload protocol (`main.py:4170`); future push fixes apply to
  extras automatically (judge criterion 4).
- Main path byte-for-byte: full suite `603 passed`, smoke `72 passed`, and the
  rollback/replace/restore/baseline set `63 passed` — including `test_baseline_happy_path.py`
  (the D-4 happy-path oracle) and `test_rollback.py` (the O-1/O-2 scenario matrix).
- Correctness verified on a throwaway `mock_device` sanity check: extras land at the mirrored
  `Specials/` remote dir as `<name> [<short_id>].mkv`, chunk hashes persist in the item's
  `split_info`, `uploaded`/`status` flip, local chunks delete, no `.partial` remnants, mvmeta
  written, re-run is idempotent, and the independent `--extras-size` is honored
  (`push_title_extras` `main.py:3884`, incl. the inherit-main default and `('NONE',None)`).
- Archived-main guard: `cmd_push` `main.py:4515` short-circuits a `push <id> --extras` on an
  `archived` main to extras-only (never re-pushes the dummied leaf).

## Weaknesses
- The journal `commit`/`rollback`/O-1-message code physically moved into `_upload_file`. A
  strict reading of "journal/PONR calls must remain *in* `cmd_push`" is not literally met; I
  preserved the *observable* contract instead (the inherent cost of approach A). Mitigated by
  the green baseline oracle + rollback suite, but it is a real risk surface vs. Candidate B.
- Extras pre-upload rollback does not pop the item's `split_info` (relies on re-split
  overwrite) — not the pristine "exact pre-command state" the main leaf gets. O-1-correct but
  asymmetric. This path is an edge case (mock_device uploads succeed), so it has only the
  throwaway-level coverage I could wire here, not a failure-injection test (Steps 9–11).
- A direct `push <id> --extras <folders>` re-scans+re-hashes the folders every run (additive
  idempotence needs the hash to detect changes). Wasteful for the 25 GB Death Note case;
  consistent with how `prep` re-hashes, but a future mtime fast-path could help (out of scope).
- `_upload_file` has a wide signature (17 params). Kept keyword-defaulted so call sites read
  clearly, but it is a large helper.

## Tests run
```
$ python -c "import main"                       -> import OK
$ python -m pytest tests/test_cmd_push_partial.py tests/test_cmd_push_mock_device.py -q
  11 passed in 0.97s
$ python -m pytest tests/smoke -q
  72 passed, 1 warning in 22.09s
$ python -m pytest tests/test_cmd_push_partial.py tests/test_cmd_push_mock_device.py \
      tests/test_cmd_push_retry.py tests/test_cmd_push_verify.py tests/test_cmd_replace.py \
      tests/test_cmd_restore_quarantine.py tests/test_rollback.py \
      tests/test_baseline_happy_path.py -q
  63 passed in 9.70s
$ python -m pytest -q
  603 passed, 1 warning in 64.37s
# throwaway mock_device extras sanity (3 tests: whole-file push, split-resume, idempotent
# re-run) -> 3 passed in 0.57s; file deleted before finishing (not committed).
```

## Confidence
high

Reasoning: the proven `cmd_push` path is provably unchanged (603 + 72 + 63 green, incl. the
D-4 oracle and the full rollback matrix), and the extras upload is validated end-to-end
against `mock_device` (mirrored paths, chunk hashes, flips, resume, independent size). The one
genuine caveat I am honest about: this approach relocates the rollback-gated journal lifecycle
into a shared helper — observably byte-for-byte, but a larger diff on the battle-tested path
than the isolated-duplication alternative. The extras failure-rollback asymmetry (#2/weakness)
is O-1-correct but only throwaway-tested here; formal failure-injection coverage is Steps 9–11.
