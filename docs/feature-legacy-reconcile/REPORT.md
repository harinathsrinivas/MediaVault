# Legacy Text-Dummy Reconciliation -- 2026-06-22

## Background

Discovered 2026-06-22 while building the IMP-E14 media-type web UI: 107 leaf entries
were polluting the "Local not-pushed" view (entries classified as `status=local_ready,
uploaded=false` but physically present only as tiny text-placeholder files on disk).

---

## The Bug

107 library leaves (75 series + 32 anime) had all three of:

- `status = local_ready`
- `uploaded = false`
- **no `split_info`** (even though many were split at push time)

Yet the on-disk `.mkv` at each entry's `folder_path/filename` was a **126- or 81-byte
legacy TEXT placeholder** whose content begins:

```
Original Hash: <sha256>
Status: SPLIT into N chunks / Status: ARCHIVED
```

These are **not video dummies** -- they are text stubs left by the old `main_perfect.py`
era, before the current video-dummy format (`make_video_dummy`) was standardised.

### Consequence

- The library believed these were local (real) files ready to push -- they are not.
- There was no `split_info`, so the fetch pipeline had no chunk list to restore from.
- The entries were un-fetchable and un-replaceable in their mislabeled state.

### Likely Cause

A library migration from `main_perfect.py` to the current `main.py` schema did not carry
`uploaded`, `status`, or `split_info` forward from the old system. The text stubs were
the old system's marker for "I have archived this"; the migration script imported them as
though they were real local files.

---

## Scale

| Bucket | Count | Details |
|---|---|---|
| Non-split (series) | 48 | 48 series episodes; no `split_info` needed |
| Non-split (anime) | 32 | 32 anime episodes; no `split_info` needed |
| Split into 2 chunks | 24 | All series |
| Split into 3 chunks | 3 | All series |
| **Total** | **107** | 75 series + 32 anime |

### Chunk-count caveat: The Wire s01e03 / s01e04

The user's manual labels for The Wire s01e03 and s01e04 were swapped (e03 labelled
"2 chunks", e04 labelled "3 chunks"). The on-disk `checksums/*.sha256` sidecars are
authoritative -- they were written at push time and cannot be mislabelled. The
reconstruction used the checksum-sidecar truth:

- `tv-en-2002-thewire-s01e03` => 3 chunks (sidecars show 3 `.chunk.NNN.` files)
- `tv-en-2002-thewire-s01e04` => 2 chunks (sidecars show 2 `.chunk.NNN.` files)

Since `cmd_fetch_restore` re-verifies every chunk hash on download, a label slip in the
decisions JSON cannot corrupt a restore -- it would merely fail loudly if wrong.

---

## Verification

All 107 entries were confirmed present in the user's Google Photos accounts via an
interactive review tool. Decisions were saved to `C:\Media\legacy_reconcile_decisions.json`
(all `verdict = "yes"`). This file is gitignored (lives with the live data, not in the
repo).

The 6 entries that ARE genuine `local_ready` real files were identified during review and
left **completely untouched**:

| ID | Note |
|---|---|
| `mov-ta-2013-soodhukavvum` | real local file, on-disk size >= DUMMY_MAX |
| `mov-ta-2012-3` | real local file |
| `tv-en-2017-dark-s01e10` | real local file |
| `tv-en-2004-battlestargalactica-s01e11` | real local file |
| `tv-en-2004-battlestargalactica-s01e12` | real local file |
| `tv-en-2004-battlestargalactica-s01e13` | real local file |

---

## The Fix

Applied via `C:\Media\_reconcile_apply.py` (a copy is kept at
`docs/feature-legacy-reconcile/reconcile_apply.py` for provenance).

### Steps (in order of execution)

1. **Non-split entries (80 total):** set `status = archived`, `uploaded = true`.
   No `split_info` needed -- these are single-file entries.

2. **Split entries (27 total):** reconstruct `split_info` from the local
   `<folder_path>/checksums/<chunk>.sha256` sidecars:
   - Parse each sidecar: `<sha256_hash> *<chunk_filename>` format.
   - Order chunks by `.chunk.NNN.` sequence number.
   - Build `split_info = {is_split, method, val, total_chunks, chunks: [{filename, hash}]}`.
   - Then set `status = archived`, `uploaded = true`.
   - Stamp `split_info_reconstructed = true` + `reconstructed_at` markers so these 27
     can be test-fetched and individually verified before bulk operations.

3. **Dummy conversion (all 107):** convert the legacy 126/81-byte text stubs to proper
   video dummies using `main.make_video_dummy` -- the same recipe `cmd_repair_dummies`
   uses. This ensures `verify_library`'s status-to-disk invariant passes immediately
   (archived entry must have a video dummy, not a text blob).

---

## Safety and Reversibility

- **Pre-apply backup:** the three live library JSONs were copied to
  `C:\Media\_mvbackup_reconcile_<ts>\` before any mutation.
- **Provenance record:** full apply log written to `C:\Media\reconcile_applied_<ts>.json`
  (lists every changed ID, its previous status/uploaded values, and the reconstructed
  chunk count for split entries).
- **No entries deleted or renamed.** Only field-level mutations: `status`, `uploaded`,
  `split_info`, `split_info_reconstructed`, `reconstructed_at`.
- **Text stubs to video dummies:** the text stubs had no archival value (they were never
  real video data); replacing them with conformant video dummies is lossless from a
  data-integrity perspective.

---

## Result

```
python main.py verify_library
-> scanned 656, OK 656, MISMATCH 0
```

All 107 previously-mislabeled entries now show `status=archived` with video dummies on
disk and (for the 27 split entries) a fully reconstructed `split_info` ready for
`cmd_fetch_restore`.

---

## Recurrence Prevention

This data-integrity gap motivated **IMP-D4** (delivered as a slice on branch
`fix/imp_d4_library_integrity_guard`):

- `cmd_verify_library` enforces the **status-to-disk invariant**: every physical leaf's
  status must match its on-disk file shape:
  - `archived` => video dummy (tiny, non-playable placeholder)
  - `local_ready` / `onboarded` / `restored_local` => real file (>= DUMMY_MAX bytes)
- **Warn-only post-conditions** wired into `cmd_push`, `cmd_replace`, and `cmd_restore`
  happy paths (post-commit, no rollback/PONR impact) so future status drift is flagged
  immediately rather than discovered months later.

A slice of **IMP-D5** (`verify_library --fix-dummies`) reuses `cmd_repair_dummies` to
regenerate legacy text-stub dummies for archived entries.

---

## Durable Record

The live `C:\Media\library_*.json` files are gitignored -- the live data is the source of
truth, not the repo. The durable records for this reconciliation are:

| Artifact | Location |
|---|---|
| Reconciliation script (copy) | `docs/feature-legacy-reconcile/reconcile_apply.py` |
| User decisions (gitignored) | `C:\Media\legacy_reconcile_decisions.json` |
| Apply log with full ID list (gitignored) | `C:\Media\reconcile_applied_<ts>.json` |
| Library backups (gitignored) | `C:\Media\_mvbackup_reconcile_<ts>\` |
| This report | `docs/feature-legacy-reconcile/REPORT.md` |
