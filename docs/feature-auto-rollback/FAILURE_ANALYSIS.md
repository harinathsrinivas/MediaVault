# Auto-Rollback — Failure & Point-of-No-Return Analysis

This is the technical core of the feature. It documents, for each multi-step
command, what state it creates/mutates, the precise point at which a failure
stops being reversible, and concrete failure walk-throughs.

> **Line references are against `main.py` as of 2026-05-28 (the pause date).**
> They WILL shift once prerequisite improvements (C9/C11/G1) land — re-verify
> before relying on them. The improvement tier docs cite *older* line numbers
> for the same functions; trust behavior descriptions over either set of numbers.

---

## 1. State each command creates / mutates

Persistent state lives in: the per-category library JSON (selected by id prefix
`mov-`/`tv-`/`ani-`, via `load_library`/`save_library`), per-media sidecar files,
and per-media working folders.

| Command | Creates / mutates |
|---|---|
| `cmd_prep` (388) | sidecars `uid` + `<short_id>.sha256`; auto-creates/links parent `season_map` (its `children` list + `total_episodes`); the library entry (`status=local_ready`, `uploaded=False`, hash, tech_spec). Has early-skip short-circuits (already uploaded/archived, or file < `DUMMY_MAX_BYTES`) that return `True` **without** creating anything. |
| `cmd_push` (634) | `adb shell mkdir` remote dir; `_parts/` (SPLIT_DIR_NAME) + `_checksums/` (CHECKSUM_DIR_NAME); chunk files + chunk `.sha256` sidecars; `split_info` in the library entry; on full success flips `uploaded=True`, `status=onboarded`. **Deletes each local chunk right after it uploads.** |
| `cmd_replace` (857) | writes a tiny dummy temp (`<original>.dummy_tmp<ext>`); **deletes the original**; renames dummy → original; sets `status=archived`. |
| `cmd_restore` (1034) | split path: merges chunks → `target_path`, re-hashes, overwrites `entry["hash"]`, sets `status=restored_local`, **deletes chunks from `restore/`**. standard path: verifies hash → moves file from `restore/` → cleans up. |
| Orchestrators / batch | `cmd_prep_push_rep` (1383), `cmd_prep_push_rep_season` (1425), `cmd_fetch_restore` (1506); `cmd_prep_season` (566), `cmd_push_group` (809), `cmd_replace_group` (907), `cmd_restore_group` (1126). |

### Existing ad-hoc rollback (to be unified away)
- `cmd_prep_push_rep` already deletes `_parts/` and prints a "local_ready" message
  on push failure (`main.py:~1399-1411`).
- `cmd_prep_push_rep_season` just `break`s "to prevent mess" (`main.py:~1483`),
  leaving half the season archived and half local with no guidance.

These two disagree with each other and are exactly what the feature replaces with
one mechanism.

---

## 2. The corrected point-of-no-return picture

The planner's first pass treated "the first delete of an uploaded chunk" as the
point of no return. Tracing the code more carefully corrected this:

**The original master file is the source of truth. As long as it exists on disk,
everything is reversible** — any prep/split/push artifact can be deleted and the
file re-prepared/re-split from scratch. The master is only destroyed in two
places:

- **`cmd_replace`**, at `os.remove(original)` (`main.py:884`). After this the
  local master is gone; the bytes exist only in the cloud.
- **`cmd_restore`** (split path), when it deletes the merged chunks from
  `restore/` after merging (`main.py:~1071-1082`) — re-merge then needs a re-fetch.

So:

| Window | Reversible? | Behavior on failure |
|---|---|---|
| Any `prep` failure | ✅ fully | auto-rollback (entry/sidecars/parent-link removed if this run created them) |
| Any `split` failure (e.g. disk full mid-chunk) | ✅ fully | auto-rollback (remove `_parts`/`_checksums`/`split_info`/entry created this run) |
| `push` failure mid-upload, **original still present** | ⚠️ *resumable*, not data-loss | **OPEN decision O-1** — resume-message / full rollback / hard-fail |
| `replace` after `os.remove(original)` | ❌ irreversible | hard-fail, actionable message (file is in the cloud; use fetch+restore) |
| `restore` after chunks deleted from `restore/` | ❌ needs re-fetch | hard-fail / quarantine (see C11) |

---

## 3. Concrete failure walk-throughs

### Example A — split push fails mid-upload
Command: `prep_push_rep mov-en-2024-bigmovie "...\big.mkv" SIZE_GB 9` (80 GB → 9 chunks).
1. `prep`: hashes original, writes `uid` + `.sha256`, creates entry. *(reversible)*
2. `push`: splits into `_parts\...chunk.001..009.mkv`, hashes them, writes
   `split_info`. *(reversible — chunks on local disk)*
3. Upload loop (`main.py:754-806`): chunk 001 `adb push` ✅ → `os.remove(chunk.001)`
   (`main.py:777`). Same for 002, 003, 004.
4. Chunk **005 `adb push` fails** (phone disconnects / phone full).

Resulting state: phone has chunks 1-4; local `_parts` has 5-9; **the 80 GB
original is still on disk**; `uploaded=False`, `status=local_ready`.

Key point: re-running `push <id>` already **resumes** — it lists the surviving
`_parts` (5-9) and uploads them (`main.py:680-683`). No data loss. The only
wrinkle for a *clean* rollback is that chunks 1-4 already sit on the phone (and
possibly in Google Photos). → This is exactly why **O-1** is an open decision.

### Example B — replace fails after deleting the original (the real PONR)
`cmd_replace` (`main.py:857-904`): make dummy temp → `os.remove(original)`
(`main.py:884`) → `os.rename(dummy → original)` (`main.py:899`).

If the process dies (or rename fails) **between** 884 and 899, the disk has
**neither the original nor a renamed dummy** — there is no file at the expected
path and the master is gone (bytes only in the cloud). This is the genuine
irreversible window, and it is precisely what improvement **C9** (atomic
two-rename) closes. After C9, a crash here leaves either the original or the
dummy — never nothing.

### Example C — restore failure (in scope per D-1)
- Split restore (`main.py:1061-1082`): merges chunks → re-hashes → **deletes the
  chunks from `restore/`**. A death after the delete needs a re-fetch to retry.
- Hash mismatch (`main.py:1096-1098`): a corrupt downloaded file is **left in
  `restore/`**, and the next fetch may skip re-downloading it — trapping the
  user. Improvement **C11** (quarantine) is the clean-state fix.

---

## 4. Edge cases the rollback mechanism must respect

- **`cmd_prep` early-skips** (already uploaded/archived; dummy < `DUMMY_MAX_BYTES`)
  return `True` without creating artifacts → must be treated as success, never
  rolled back.
- **Resume collisions:** `cmd_push` intentionally resumes from a pre-existing
  `_parts/`. The snapshot must record `_parts`/`_checksums`/`split_info` presence
  at entry so rollback never deletes artifacts that pre-existed this run.
- **Shared season parent:** in a season run the parent `season_map` is created
  during episode 1's prep. Rolling back episode 5 must NOT delete the parent or
  episodes 1-4's child links — per-id snapshots (D-6/D-7) handle this.
- **Chunk delete is `try/except: pass`** (`main.py:776-779`): a successful upload
  whose local delete silently fails still counts as "past the chunk-upload point"
  — key the boundary on upload success, not delete success.
- **Windows file locks** (Plex / Windows Search) can make a rollback
  `os.remove`/`rmtree` fail; rollback must report partial-rollback honestly
  rather than claim a success it didn't achieve.
- **`save_library` rewrites all three JSONs**; a rollback that reverts the
  in-memory dict then calls `save_library` is consistent with existing behavior,
  but a rollback that itself dies mid-`save_library` is a rare hard edge (the
  on-disk-journal candidate survives this best).
