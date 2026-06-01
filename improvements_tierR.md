# Tier R — Auto-Rollback Hardening & Storage Efficiency

> **Added 2026-06-01.** Forward-looking follow-ups that build on the merged
> auto-rollback feature (PR #14, Candidate C — the on-disk `RollbackJournal`).
> Read [`docs/feature-auto-rollback/ROLLBACK_MECHANISM.md`](docs/feature-auto-rollback/ROLLBACK_MECHANISM.md)
> first. This tier is **enrichable** — add `IMP-R<N>` items in the standard
> format (see `improvement_details.md` §2) as ideas arise.
>
> ⚠️ **Change-gate.** Any item here that alters rollback *behavior* (journal
> format, PONR locations, created-this-run scoping, the `cmd_*` wrapping,
> `recover_journal` semantics, season resume-range messaging, or the
> `RollbackHardFail` contract) must, before implementation, **pause and ask the
> user** with the exact diff from the documented behavior — see `CLAUDE.md`
> ("Auto-rollback is load-bearing — change-gate") and `ROLLBACK_MECHANISM.md` §10.

## Cross-cutting context

The auto-rollback mechanism lives entirely in `main.py` (`RollbackJournal` ~410,
`recover_journal` ~561, `RollbackHardFail` ~398) and wraps `cmd_prep`/`cmd_push`/
`cmd_replace`/`cmd_restore` + the `prep_push_rep` / `prep_push_rep_season`
orchestrators. The two true points of no return are `cmd_replace`'s commit rename
and `cmd_restore`'s split chunk-delete; push is resumable (O-1). Rollback removes
only what *this run* created and duplicates **zero** media bytes. These items
extend that foundation; none of them should change the happy path (D-4) without an
explicit, change-gated decision.

---

## IMP-R1: Reduce the split/upload disk peak (streaming split-upload-delete)

- Category: performance
- Priority: medium
- Files: `main.py` `cmd_push` (~992), `split_video_file` (~155)
- Current behavior: `cmd_push` splits the **entire** file into `_parts/` up front
  (all chunks written), hashes them, writes `split_info`, then enters the upload
  loop which `adb push`es each chunk and `os.remove`s it after a successful upload.
  Peak local disk during the window between "split done" and "first uploads" is
  `original + all chunks` — e.g. a 20 GB file split into 4 × 5 GB chunks needs
  ~40 GB transient. Auto-rollback adds nothing to this (it journals the `_parts/`
  *directory*, not the bytes); the peak is purely a property of split-then-upload.
- Proposed change: split → upload → delete **one chunk at a time** (or a small
  bounded window of K chunks) so chunks never all coexist on disk. Peak drops to
  `original + K × chunk_size` (≈ 25 GB for K=1 in the example). Must preserve:
  the G1 `.partial` + atomic-rename upload, the C8 post-push verify gate, the
  per-chunk `.sha256` sidecars, and `split_info` accuracy. Must keep the existing
  **resume** semantics working (a re-run resumes from whatever is in `_parts/`).
- Rationale: the transient peak is the single biggest local-disk constraint when
  archiving very large files; halving-or-better it directly enables archiving files
  larger than free space allows today.
- Goal: pushing a 20 GB / 4-chunk file never holds more than `original + 1 chunk`
  of chunk data on disk, with identical final library/remote state and working
  resume after an interruption.
- Effort estimate: large
- Status: pending
- **Change-gate:** interacts with O-1 resume + the push journaling — pause and
  confirm the resume/journal contract with the user before implementing.

## IMP-R2: Expose a `recover` CLI subcommand for `recover_journal()`

- Category: new CLI command
- Priority: medium
- Files: `main.py` `recover_journal` (~561) + the `sys.argv` dispatch block
- Current behavior: `recover_journal(folder_path)` is implemented and tested but is
  only callable programmatically — there is no user-facing subcommand to finish an
  interrupted rollback after a hard kill / power loss.
- Proposed change: add `python main.py recover <id|folder>` that resolves the media
  folder (by id via the library, or a direct path) and calls `recover_journal()`,
  printing the outcome. Optionally `recover --scan` to sweep all media roots for
  leftover `.mediavault_txn.json` files (see IMP-R5). Read-only on a journal that
  crossed its PONR (it declines, as documented).
- Rationale: the durable journal's whole value is that recovery can run *later*;
  without a subcommand the user can't trigger it without writing Python.
- Goal: after a simulated hard kill mid-command, `python main.py recover <id>`
  restores the exact pre-command state and removes the journal.
- Effort estimate: small
- Status: pending

## IMP-R3: Stale-journal detection in a `doctor`/health check

- Category: robustness
- Priority: low
- Files: `main.py` (new or existing health-check command; ties to IMP-C3 `doctor`)
- Current behavior: a `.mediavault_txn.json` left by a crashed run is only found if
  the user happens to re-run that exact command or call `recover` on that folder.
  There is no global "are there leftover journals?" check.
- Proposed change: scan the media roots for `.mediavault_txn.json` files and report
  each (id, whether it crossed its PONR, record count, age), recommending `recover`
  for pre-PONR journals and inspection for post-PONR ones. Fold into IMP-C3
  `doctor` if/when that lands.
- Rationale: makes orphaned-state detection proactive instead of incidental.
- Goal: `doctor` (or `recover --scan`) lists every leftover journal and the
  recommended action.
- Effort estimate: small
- Status: pending

## IMP-R4: Verify/extend rollback coverage to the group commands

- Category: robustness
- Priority: medium
- Files: `main.py` `push_group`, `replace_group`, `restore_group`
- Current behavior: the single-item commands (`cmd_prep`/`push`/`replace`/`restore`)
  and the `prep_push_rep` / `prep_push_rep_season` orchestrators are wrapped/unified.
  The `*_group` variants iterate the per-item commands (so they inherit per-item
  rollback) but it has not been confirmed they emit the same **group-level**
  completed-items-stay + resume-range messaging as the season orchestrator.
- Proposed change: audit each `*_group` command; ensure a mid-batch failure keeps
  completed items, lets the in-flight item self-handle via the wrapped command, and
  prints a reconstructed resume command — matching `cmd_prep_push_rep_season`. Add
  scenario tests mirroring the season test.
- Rationale: consistency — a failure in `push_group` should behave like a failure
  in `prep_push_rep_season`, not fall back to an ad-hoc path.
- Goal: a forced mid-batch failure in each `*_group` command leaves completed items
  intact and prints an accurate resume command; covered by tests.
- Effort estimate: medium
- Status: pending
- **Change-gate:** touches the rollback wrapping/messaging — pause and confirm.

## IMP-R5: Journal observability — make `.mediavault_txn.json` self-describing

- Category: other (operability)
- Priority: low
- Files: `main.py` `RollbackJournal` (~410)
- Current behavior: the journal records `manual_id`, `crossed_ponr`, and the
  `records` list. It has no timestamp, command name, or schema version, so a
  leftover journal is slightly harder to triage after the fact.
- Proposed change: add `created_at`, the `command` that opened it, and a
  `schema_version` to the journal header (additive — `recover_journal` ignores
  unknown header fields). Purely a forensic/operability improvement; **no behavior
  change** to rollback or recovery.
- Rationale: a stale journal becomes self-explanatory for the user/`doctor`.
- Goal: a leftover journal shows when/what created it; `recover_journal` and the
  scenario tests are unchanged.
- Effort estimate: small
- Status: pending
