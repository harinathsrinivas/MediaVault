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
> **R6–R9 below (added by the 2026-06-12 fable-review) are ALL gate-flagged: they
> exist as decision requests, not pre-approved work.**

## Cross-cutting context

The auto-rollback mechanism lives entirely in `main.py` (`RollbackJournal` ~550,
`recover_journal` ~701, `RollbackHardFail` ~538 as of 2026-06-12) and wraps
`cmd_prep`/`cmd_push`/`cmd_replace`/`cmd_restore` + the `prep_push_rep` /
`prep_push_rep_season` orchestrators. The two true points of no return are
`cmd_replace`'s commit rename and `cmd_restore`'s split chunk-delete; push is
resumable (O-1). Rollback removes only what *this run* created and duplicates
**zero** media bytes. These items extend that foundation; none of them should
change the happy path (D-4) without an explicit, change-gated decision.

**Attribute key (added 2026-06-12):** `Risk` = blast radius of MAKING the change.
`If skipped` = the failure that persists, with a scenario.

---

## IMP-R1: Reduce the split/upload disk peak (streaming split-upload-delete)

- Category: performance
- Priority: medium
- Files: `main.py` `cmd_push`, `split_video_file`
- Current behavior: `cmd_push` splits the **entire** file into `_parts/` up front
  (all chunks written), hashes them, writes `split_info`, then enters the upload
  loop which `adb push`es each chunk and `os.remove`s it after a successful upload.
  Peak local disk during the window between "split done" and "first uploads" is
  `original + all chunks` — e.g. a 20 GB file split into 4 × 5 GB chunks needs
  ~40 GB transient. Auto-rollback adds nothing to this (it journals the `_parts/`
  *directory*, not the bytes); the peak is purely a property of split-then-upload.
  (Interim relief shipped with PR #20: the disk pre-flight FAILS FAST before
  splitting, and `tempdir <path>` moves the peak to another volume — the peak
  itself is unchanged.)
- Proposed change: split → upload → delete **one chunk at a time** (or a small
  bounded window of K chunks) so chunks never all coexist on disk. Peak drops to
  `original + K × chunk_size` (≈ 25 GB for K=1 in the example). Must preserve:
  the G1 `.partial` + atomic-rename upload, the C8 post-push verify gate, the
  per-chunk `.sha256` sidecars, `split_info` accuracy, the eager-rehash option
  (which needs ALL chunks at once to pre-merge — eager + streaming are mutually
  exclusive; define precedence), and the existing **resume** semantics (a re-run
  resumes from whatever is in `_parts/`).
- Rationale: the transient peak is the single biggest local-disk constraint when
  archiving very large files; halving-or-better it directly enables archiving files
  larger than free space allows today. (A planner prompt exists in
  `docs/next-tasks-planner-prompts.md`.)
- Goal: pushing a 20 GB / 4-chunk file never holds more than `original + 1 chunk`
  of chunk data on disk, with identical final library/remote state and working
  resume after an interruption.
- Effort estimate: large
- Risk: high **(change-gated)** — re-sequences the push pipeline that O-1 resume,
  the journal's created-this-run scoping, the disk pre-flight (whose 1X/2X math
  changes!), and mkvmerge's whole-file split all assume. mkvmerge cannot split
  incrementally, so this likely needs split-in-stages or a different splitter —
  a design decision in itself.
- If skipped: archiving a file bigger than ~half the free space stays impossible
  without `tempdir` to another volume; the 80 GB remux + 100 GB free scenario
  hard-stops at the pre-flight (correctly, but unsatisfyingly).
- Status: pending
- **Change-gate:** interacts with O-1 resume + the push journaling — pause and
  confirm the resume/journal contract with the user before implementing.

## IMP-R2: Expose a `recover` CLI subcommand for `recover_journal()`

- Category: new CLI command
- Priority: medium
- Files: `main.py` `recover_journal` + the `sys.argv` dispatch block
- Current behavior (pre-fix): `recover_journal(folder_path)` was only callable programmatically.
- Proposed change: `python main.py recover <id|folder>` + `recover --scan` sweep.
- Rationale: the durable journal's whole value is that recovery can run *later*.
- Goal: after a simulated hard kill mid-command, `recover <id>` restores the exact pre-command state.
- Effort estimate: small
- Status: done (PR #18, 2026-06-03 — including the `--scan` sweep, which de-facto delivered IMP-R3's core)

## IMP-R3: Stale-journal detection in a `doctor`/health check

- Category: robustness
- Priority: low
- Files: `main.py` (ties to IMP-C3 `doctor`)
- Current behavior: `recover --scan` (shipped with R2/PR #18) already sweeps all
  media roots and reports each leftover `.mediavault_txn.json` with crossed_ponr
  state, record count, and the recommended action — the original ask is ~done.
  What remains is the *integration* half: surfacing the same sweep inside `doctor`
  (IMP-C3) and adding journal AGE to the report.
- Proposed change: when IMP-C3 lands, call the scan as a doctor check (WARN on any
  pre-PONR journal, suggesting the exact `recover` command; INFO on post-PONR ones);
  add an age column to both surfaces. The Tier S daemon runs doctor on schedule,
  making stale-journal detection fully proactive.
- Rationale: makes orphaned-state detection proactive instead of incidental.
- Goal: `doctor` lists every leftover journal, its age, and the recommended action.
- Effort estimate: small
- Risk: low — read-only reporting reuse.
- If skipped: stale journals are only found when someone remembers to run
  `recover --scan`; a pre-PONR journal can silently age for weeks (its artifacts
  meanwhile look "pre-existing" to re-runs — see IMP-R7).
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
  (2026-06-12 read confirms: `cmd_push_group` just loops `cmd_push` with no
  group-level resume message; `cmd_replace_group` loops `cmd_replace` and does not
  even catch `RollbackHardFail` — a post-PONR replace failure mid-group ESCAPES as
  a raw traceback, skipping the remaining items without a summary.)
- Proposed change: audit each `*_group` command; ensure a mid-batch failure keeps
  completed items, lets the in-flight item self-handle via the wrapped command,
  catches `RollbackHardFail` (replace_group!), and prints a reconstructed resume
  command — matching `cmd_prep_push_rep_season`. Add scenario tests mirroring the
  season test.
- Rationale: consistency — a failure in `push_group` should behave like a failure
  in `prep_push_rep_season`, not fall back to an ad-hoc path.
- Goal: a forced mid-batch failure in each `*_group` command leaves completed items
  intact and prints an accurate resume command; covered by tests.
- Effort estimate: medium
- Risk: medium **(change-gated)** — adds orchestrator-level messaging in the
  rollback domain; the per-item contracts stay untouched but the gate requires
  stating exactly that before implementation.
- If skipped: a post-PONR failure inside `replace_group` keeps crashing the whole
  batch with a traceback and NO "to recover: fetch_restore <id>" guidance — the
  one place the structured hard-fail message currently gets lost.
- Status: pending
- **Change-gate:** touches the rollback wrapping/messaging — pause and confirm.

## IMP-R5: Journal observability — make `.mediavault_txn.json` self-describing

- Category: other (operability)
- Priority: low
- Files: `main.py` `RollbackJournal`
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
- Risk: low-medium **(change-gated formally)** — it IS a journal-format change, so
  the gate applies even though the change is additive-only; present the exact new
  header fields and the recover-ignores-unknowns proof when asking.
- If skipped: triage of leftover journals keeps requiring code-context knowledge
  ("which command writes link_child records?") instead of reading a header line.
- Status: pending

## IMP-R6: Restore merge-failure leaves NO file at the original path (dummy lost)

- Category: bug (found 2026-06-12, REVIEW_NOTES §A7)
- Priority: medium
- Files: `main.py` `cmd_restore` (split path), possibly `cmd_repair_dummies`
- Current behavior: the split-path merge writes directly ONTO `target_path` —
  overwriting the archived dummy that lived there. On merge failure the rollback
  correctly removes the (reproducible) partial merge output… which means the dummy
  is gone too: the entry stays `archived` but NO file exists at the path. Jellyfin/
  Plex/Emby drop the item from the library at next scan; `repair_dummies` counts
  the path as `missing` and does NOT regenerate. The state self-heals only on a
  later successful restore.
- Proposed change (OPTIONS — user decision required, this is inside the gate):
  - (a) Merge to a temp name (`<target>.merge_tmp.mkv`) and `os.replace` into place
    only on success — the dummy survives any failure; PONR placement unchanged
    (chunk delete still happens after the swap). Cleanest; changes the journal's
    reproducible-output path value.
  - (b) Keep merging onto target, but on the failure path regenerate the dummy via
    `make_video_dummy` after removing the partial output.
  - (c) Leave restore untouched; teach `repair_dummies` to regenerate MISSING
    dummies for archived entries (today it skips them).
  - Recommendation: (a) + (c) (belt and suspenders); (c) alone is gate-free.
- Rationale: the dummy IS the couch catalog entry (Tier S makes it the fetch
  button); losing it on a transient merge failure silently removes the title from
  every client until someone notices.
- Goal: no failure path leaves an archived entry with zero bytes at its path.
- Effort estimate: small-medium
- Risk: medium **(change-gated)** — option (a) moves the reproducible-output
  seam recorded in the journal; option (c) is outside the gate entirely.
- If skipped: any mkvmerge failure during a daemon-triggered restore (bad chunk
  pairings, tool drift, disk hiccup) makes the title VANISH from Jellyfin —
  the user on the couch sees the movie disappear instead of an error.
- Status: pending
- **Change-gate:** options (a)/(b) alter restore-failure artifact handling — ask first.

## IMP-R7: Re-running a command silently clobbers a leftover pre-PONR journal

- Category: bug (found 2026-06-12, REVIEW_NOTES §A8)
- Priority: medium
- Files: `main.py` `RollbackJournal.__init__`
- Current behavior: the constructor immediately `_flush()`es a fresh empty journal
  over any leftover `.mediavault_txn.json` at the same path. The documented
  contract says leftovers are handled by `recover_journal()` — but the natural
  user reflex after a crash is to RE-RUN the command, which destroys the crashed
  run's inverses before recovery could replay them. The crashed run's artifacts
  then look "pre-existing" to the new run and become permanently rollback-orphaned.
- Proposed change (decision required): on journal open, if a leftover exists and
  `crossed_ponr` is false → either (a) WARN loudly and proceed (status quo but
  visible), (b) auto-run `recover_journal()` first (pre-PONR recovery is
  idempotent and restores clean state, then the command proceeds normally —
  recommended), or (c) refuse and instruct the user to run `recover`. Post-PONR
  leftovers: keep current overwrite-after-inspection-note behavior or preserve
  under a timestamped name.
- Rationale: the journal's crash-survival guarantee currently has a one-step hole:
  surviving the crash but not the user's instinctive retry.
- Goal: a crash → re-run sequence never silently loses recovery information.
- Effort estimate: small
- Risk: medium **(change-gated)** — touches journal lifecycle AND puts recovery
  adjacent to the happy path (D-4 tension: option (b) runs recovery code on a
  command invocation; argue it as "not the happy path — a leftover journal means
  the previous run was NOT happy").
- If skipped: the most likely real-world crash sequence (crash → re-run) keeps
  leaking artifacts out of rollback scope; e.g. a killed prep leaves sidecars that
  no future rollback will ever remove.
- Status: pending
- **Change-gate:** journal lifecycle — ask first with the (a)/(b)/(c) options.

## IMP-R8: Journal the eager-rehash merge temp

- Category: bug (found 2026-06-12, REVIEW_NOTES §A9)
- Priority: low
- Files: `main.py` `cmd_push` eager-rehash block
- Current behavior: the eager path writes `<base>.rehash_tmp.mkv` (master-sized!)
  and removes it in `finally` — but a hard kill mid-merge leaves it on disk,
  untracked by the journal, invisible to `recover`/`recover --scan`. Pure disk
  leak (tens of GB) until manually noticed.
- Proposed change: `journal.record_create_file(rehash_tmp)` (or
  `record_create_reproducible`) before the merge, so a crash leaves an inverse on
  disk and recovery deletes the temp. The `finally` cleanup stays (journal record
  becomes a no-op when the file is already gone — `_replay_inverses` already
  guards on existence).
- Rationale: closes the one crash-window where the rollback system loses track of
  a multi-GB artifact it caused.
- Goal: a kill during eager merge leaves nothing un-recoverable; `recover` removes
  the temp.
- Effort estimate: small
- Risk: low **(change-gated formally)** — adds one record to an existing vocabulary
  on a pre-PONR path; the gate asks for exactly this statement before doing it.
- If skipped: each crashed eager push can strand a file-sized temp on the media
  volume; with `tempdir` redirection the leak lands on the scratch volume instead.
- Status: pending
- **Change-gate:** adds a journal record on the push path — ask first (small ask).

## IMP-R9: Bring cmd_prep_season's alias creation under journal coverage

- Category: bug (found 2026-06-12, REVIEW_NOTES §A11)
- Priority: low
- Files: `main.py` `cmd_prep_season` (combined-episode alias block)
- Current behavior: after `cmd_prep`'s journal commits, the multi-episode alias
  block re-loads the library and writes alias entries + parent child-links + a
  save_library DIRECTLY — no journal. A crash mid-alias-loop leaves partial
  aliases with no rollback/recover coverage (low practical risk: re-running
  prep_season is idempotent-ish, but it's the only library mutation outside the
  journal system since PR #14 unified everything).
- Proposed change: wrap the alias block in its own `RollbackJournal`
  (record_create_entry + record_link_child per alias), or extend `cmd_prep`'s
  journal scope to cover it. Pure consistency work; the alias records fit the
  existing vocabulary exactly.
- Rationale: "exactly one rollback mechanism in the codebase" (ARCHITECTURE §12a)
  is currently 99% true; this closes the 1%.
- Goal: every library mutation in the prep family is journal-covered; a crash
  anywhere in prep_season leaves a recoverable journal.
- Effort estimate: small
- Risk: low **(change-gated formally)** — extends wrapping to a new block; the
  records used are all existing vocabulary.
- If skipped: a crash in the alias loop can leave an alias whose parent link is
  missing (or vice versa) — exactly the inconsistency class IMP-D4's alias checks
  would then flag, but with no automated repair path.
- Status: pending
- **Change-gate:** extends cmd_* journal wrapping — ask first (small ask).
