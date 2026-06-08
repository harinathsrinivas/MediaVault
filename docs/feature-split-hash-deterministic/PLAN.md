# Task: Give split files a verifiable canonical whole-file hash via deterministic mkvmerge re-merge (Way A + --deterministic), default deferred / opt-in eager, with a hard disk pre-flight + optional off-volume temp dir

Suggested branch: fix/split_hash_deterministic_rehash

> **No IMP code applies.** This is net-new integrity work that maps to no single tracked
> `improvements_tier*` item. The git-agent MUST NOT invent an IMP code for the PR title
> (lesson from PR #19 / `feedback_git_agent_imp_code`). The PR title is plain
> `fix: …` with no `— IMP-XN` suffix. If the user later wants it tracked, file a new Tier R item.

---

## Context / Problem

Split files lose a verifiable whole-file hash. Confirmed in code:

1. **`cmd_prep` stores the ORIGINAL (pre-split) hash.** `main.py:692` computes
   `file_hash = calculate_file_hash(filepath)` and `main.py:768` stores it as `entry["hash"]`
   (SHA256 of the original, undivided file).
2. **`cmd_push` splits + hashes each chunk and never reconciles.** `main.py:1138` splits via
   `split_video_file` (mkvmerge `--split`), `main.py:1146-1163` hashes each chunk into
   `split_info.chunks[].hash` and writes `split_info`. The original whole-file hash is never
   reconciled with what a merge would produce. Today's integrity for split files rests entirely on
   per-chunk hashes (verified pre-merge at `main.py:1684-1688`).
3. **`cmd_restore` blindly OVERWRITES the original hash with the merged-container hash.**
   `main.py:1727-1734`: after `merge_video_files`, it computes `new_hash` and does
   `library[manual_id]["hash"] = new_hash`. Because mkvmerge re-muxes (new segment UID + mux
   timestamps), the merged container differs byte-for-byte from the original, so this overwrite is
   "circular": the original hash was never verifiable, and the new hash is just "whatever mkvmerge
   produced this once" — itself unverifiable on a future merge.

### The locked decision — "Way A + --deterministic"

An empirical spike (mkvmerge v97.0, 5 GB file) PROVED:
- **Default mkvmerge merge is NON-deterministic** — two merges of the same chunks produced different
  SHA256 (`8595b46b…` vs `5f007b6e…`) due to random segment UID + mux timestamp.
- **`mkvmerge --deterministic <seed>` makes the merged file BYTE-IDENTICAL across separate runs**
  (`a0b239a1…` both times).
- Way B (raw-byte split that reconstructs byte-identical to the original) was REJECTED: unproven
  Google Photos ingestion, strands the ~130 existing archived mkvmerge-chunk entries, loses chunk
  playability. **Not in scope.**

So: reconstruct split files with `mkvmerge --deterministic <seed>` and treat the resulting stable
merged hash as the **canonical whole-file hash**.

**DEFAULT = DEFERRED rehash; OPT-IN = EAGER rehash.**
- **DEFERRED (default):** `cmd_push` is UNCHANGED in disk profile (no merge at push; stays at
  today's 2X peak). The canonical hash is blessed at the FIRST `cmd_restore` — which already merges
  + hashes the merged file today, so the only added cost is adding `--deterministic <seed>` to the
  merge and switching the blind overwrite to **verify-or-bless**:
  - if `re_hashed` already true → VERIFY `merged_hash == entry["hash"]`, alarm on mismatch
    (real corruption / tool drift), do NOT change the hash, do NOT cross the PONR / delete chunks;
  - else → SET `entry["hash"] = merged_hash`, set `re_hashed=true`, store `merge_seed` + `merge_tool`
    + `rehashed_at`.
- **EAGER (opt-in flag):** at push, after split + chunk-hash, merge the chunks once with
  `--deterministic <seed>`, hash the merged temp, DELETE the temp, and store the blessed canonical +
  seed + tool under `split_info` (proves determinism while the master is still on disk). The canonical
  is **PROMOTED into `entry["hash"]` at `cmd_replace`** (when the original leaves disk), NOT at push —
  this keeps `entry["hash"]` consistent with the on-disk file so `cmd_check` (`main.py:935-936`) stays
  correct in the pre-replace window.

**KEEP THE ORIGINAL MASTER until `cmd_replace`** (do NOT delete after split) — load-bearing for
O-1/O-2 resume+rollback. This is unchanged and not up for revision.

### Two added scopes confirmed by the user (2026-06-07)

A. **End-to-end fetch→restore verification cycle (explicit).** The full archived→restored cycle must
   verify the canonical: a fetch of an already-`re_hashed` entry verifies each chunk hash (existing),
   then merges deterministically and **verifies the merged hash against the stored canonical** to
   complete the cycle; a not-yet-`re_hashed` entry verifies chunks, merges, then **blesses** and marks
   `re_hashed=true`. This is already produced by the Step 2 `cmd_restore` change and is INHERITED by
   `cmd_fetch_restore` (`main.py:2224` → `cmd_restore`/`cmd_restore_group`) and `cmd_restore_group`
   (`main.py:1809` → `cmd_restore`). No new code path — make it explicit + add an end-to-end test.
B. **Hard disk pre-flight + optional off-volume temp dir** (Steps 4 & 5). Stop BEFORE the split if the
   target volume can't hold what we're about to create; optionally redirect chunks + the eager merge
   temp to a different volume via a `tempdir <path>` token.

---

## Goal (definition of done)

1. **Deferred path:** the first `cmd_restore` of a split entry merges with `--deterministic <seed>`,
   blesses `entry["hash"]` to the merged hash, sets `re_hashed=true`, stores
   `merge_seed`/`merge_tool`/`rehashed_at`. A second `cmd_restore` VERIFIES (hash unchanged) and a
   tampered/mismatching merged hash with `re_hashed=true` raises a corruption alarm WITHOUT crossing
   the PONR or deleting chunks.
2. **End-to-end cycle:** `fetch_restore` of an archived `re_hashed` entry verifies chunks → merges →
   verifies against the canonical; of a not-yet-`re_hashed` entry → verifies chunks → merges →
   blesses → marks `re_hashed`. (Inherited via `cmd_restore`; covered by an explicit test.)
3. **Eager path:** `push … rehash` merges + blesses the canonical into `split_info` at push (master
   still present); `cmd_replace` promotes it into `entry["hash"]`.
4. **Determinism is proven by a test:** the same chunks merged 2–3 times with the stored seed yield
   identical SHA256 (real-mkvmerge test, gated/skipped when mkvmerge is absent).
5. **`cmd_check` stays correct** across the eager pre-replace window.
6. **Hard disk pre-flight:** a push/season/eager run that would exceed the target volume's free space
   STOPS before the split with a clear remedy message — never starts and fails mid-split. A
   `tempdir <path>` redirects chunks + eager merge temp off the media volume (and the check then
   targets the temp volume).
7. **Migration:** existing `is_split:true` entries across all three libraries get `re_hashed:false`
   stamped (metadata-only, no hashing); non-split entries untouched.
8. **Regression:** full `pytest -q` stays green; `tests/test_baseline_happy_path.py` and
   `tests/test_rollback.py` are unaffected (the rollback PONR/journal contract is unchanged — see
   Change-gate).

---

## Schema changes (locked)

- **DROP** the idea of a separate `original_hash`. `entry["hash"]` is REPURPOSED to hold the
  canonical merged hash once blessed (it holds the original hash until then).
- **ADD `re_hashed` (bool)** at the entry top level — has the canonical merged hash been blessed for
  this split entry. Meaningful only for `is_split:true` entries.
- **ADD `merge_seed` (string)** under `split_info` (split-only) — the `--deterministic` seed.
  **Value = the entry's `short_id`** (deterministic, already unique per entry, zero generation). It is
  stored explicitly and REUSED VERBATIM on every future merge (Open Decision 1, resolved: short_id).
- **ADD `merge_tool` (string)** under `split_info`, e.g. `"mkvmerge v97.0"`, captured at bless — so a
  future MKVToolNix upgrade degrades to a graceful re-bless rather than a false corruption alarm
  (version-drift handling: chunk hashes still guarantee content).
- **ADD `rehashed_at` (string, ISO-8601 UTC)** — when the canonical was blessed (i.e. when `re_hashed`
  flipped true). Recorded for "when was this blessed" observability; NOT used as the seed. Location:
  under `split_info` alongside `merge_seed`/`merge_tool` (split-only).

> Note `write_remote_mvmeta` (`main.py:984-998`) already writes an `original_hash` field into the
> remote `.mvmeta.json` sidecar sourced from `entry.get("hash")`. After bless that key will carry the
> CANONICAL hash for entries pushed after a bless. This is acceptable (the sidecar is disaster-
> recovery redundancy, and chunk hashes are the source of truth) and is left as-is; it is NOT the
> `original_hash` schema field this plan drops. Confirm and state this in the implementation diff;
> do not rename the sidecar field in this task.

### Before / after example (the real `mov-en-2013-coherence` entry supplied by the user)

The user supplied this exact archived split entry. The implementer mirrors the real fields verbatim
and only adds the new keys (`re_hashed` at top level; `merge_seed`/`merge_tool`/`rehashed_at` under
`split_info`). The canonical hash shown in the final block is illustrative.

BEFORE (the user's current entry — `hash` is the SHA256 of the ORIGINAL pre-split file):
```jsonc
"mov-en-2013-coherence": {
  "short_id": "f6b674",
  "filename": "Coherence.2013.BluRay.1080p.DTS-HD.MA.5.1.AVC.REMUX-ROLAND.mkv",
  "folder_path": "C:\\Media\\Movies\\English\\Mind Boggling\\Coherence.2013.BluRay.1080p.DTS-HD.MA.5.1.AVC",
  "status": "archived",
  "uploaded": true,
  "search_term": "Coherence.2013.BluRay.1080p.DTS-HD.MA.5.1.AVC.REMUX-ROLAND [f6b674].mkv",
  "hash": "a83fa4b49c78ab603d50a74b1e00ba4862c6276e82cd495898961930128e0514", // ORIGINAL file SHA256
  "metadata": { "title": "mov-en-2013-coherence", "year": 2013, "genre": [], "added_date": "2026-01-16" },
  "tech_spec": { "...": "... (size_bytes 17654342137, etc.) ..." },
  "split_info": {
    "is_split": true,
    "method": "COUNT",
    "val": "2",
    "total_chunks": 2,
    "chunks": [
      { "filename": "Coherence...REMUX-ROLAND [f6b674].chunk.001.mkv", "hash": "5012117ac418b67a36933510f82a4842558984438e4e397422fea63020f91732" },
      { "filename": "Coherence...REMUX-ROLAND [f6b674].chunk.002.mkv", "hash": "69b9ee0da15a09985603ac3d82ef2c512ea6b353917d7b6d0f20dc03480359cd" }
    ]
  }
}
```

AFTER migration only (re_hashed stamped, nothing blessed yet — `hash` still the ORIGINAL):
```jsonc
"mov-en-2013-coherence": {
  "...": "... (all fields above unchanged) ...",
  "hash": "a83fa4b49c78ab603d50a74b1e00ba4862c6276e82cd495898961930128e0514", // still ORIGINAL
  "re_hashed": false,                    // <-- NEW (migration stamp; top level)
  "split_info": { "... (unchanged, no merge_seed/merge_tool/rehashed_at yet) ..." }
}
```

AFTER first deferred restore blesses (DEFERRED), or after eager promote-at-replace (EAGER):
```jsonc
"mov-en-2013-coherence": {
  "...": "...",
  "hash": "<deterministic merged hash of the 2 chunks>", // <-- now the CANONICAL hash (illustrative)
  "re_hashed": true,                     // <-- flipped true
  "split_info": {
    "is_split": true, "method": "COUNT", "val": "2", "total_chunks": 2,
    "chunks": [ { "...": "..." }, { "...": "..." } ],
    "merge_seed": "f6b674",               // <-- NEW (= short_id; reused verbatim forever)
    "merge_tool": "mkvmerge v97.0",       // <-- NEW (captured at bless)
    "rehashed_at": "2026-06-07T14:03:22Z" // <-- NEW (when re_hashed flipped true)
  }
}
```

> EAGER stores `merge_seed` + `merge_tool` (+ the blessed canonical) under `split_info` at PUSH;
> `entry["hash"]`, `re_hashed`, and `rehashed_at` are written at REPLACE-promote. DEFERRED writes all
> of `hash`/`re_hashed`/`merge_seed`/`merge_tool`/`rehashed_at` at the first restore bless. Either way
> the resulting blessed shape above is identical.

---

## Steps

- [x] 1. [model: opus] [effort: high] Add merge/seed/tool + disk-calc + temp-path helpers
  - Files: `main.py` (near `merge_video_files` at `main.py:231`; constants block `main.py:15-41`)
  - Details:
    - (a) **Deterministic merge.** Extend `merge_video_files(chunk_paths, output_path, seed=None)` so
      that when `seed` is not None the argv becomes `[MKVMERGE_PATH, "--deterministic", seed, "-o",
      output_path, chunk1, "+chunk2", ...]`; when `seed is None` the argv is byte-for-byte what it is
      today (so existing callers and `tests/conftest.py fail_merge` stay valid). `--deterministic
      <seed>` is a global option and must precede `-o` (confirm via `mkvmerge --deterministic --help`).
    - (b) **Seed = short_id.** No generator helper needed — the seed is the entry's `short_id`. Add a
      tiny `_rehashed_at()` returning `datetime.now(timezone.utc)` as a compact ISO-8601 `Z` string.
    - (c) `_current_merge_tool()` → run `mkvmerge --version`, parse the `vNN.N` token, return
      `"mkvmerge vNN.N"`; on ANY failure return `"mkvmerge (unknown)"` and never raise.
    - (d) **Disk-calc helpers.** `_required_extra_bytes(file_size, will_split, eager)` → `0` if not
      will_split; `file_size` if deferred split; `2*file_size` if eager split. `_disk_buffer(need)` →
      `0` if need==0 else `max(int(0.01*need), 2*1024**3)`. `_free_space_ok(target_dir, file_size,
      will_split, eager)` → `shutil.disk_usage(target_dir).free >= _required_extra_bytes(...) +
      _disk_buffer(...)`, plus a sibling that RETURNS the shortfall numbers for the message. Never raise
      (a missing dir → treat as not-ok with a clear reason).
    - (e) **Temp-path helper.** `_parts_base(local_folder, temp_dir, manual_id)` → returns the directory
      that should hold `_parts` (and the eager merge temp): `temp_dir/<safe manual_id>` when `temp_dir`
      is given, else `local_folder`. Validate a provided `temp_dir` exists and is writable; return a
      clear error signal if not. The `checksums/` sidecars and the `RollbackJournal` ALWAYS stay in
      `local_folder` regardless of `temp_dir`.
    - Do NOT touch any rollback journal / PONR code in this step.
  - Acceptance: `merge_video_files(paths, out)` argv unchanged when `seed=None`; with a seed the argv
    contains `--deterministic` immediately before the seed and before `-o`. `_rehashed_at()` returns a
    stable ISO string; `_current_merge_tool()` never raises; disk helpers return correct numbers for
    {no-split, deferred, eager}; `_parts_base` returns `local_folder` with no temp_dir and the temp
    location with one. `pytest -q` still green.

- [x] 2. [model: opus] [effort: max] [candidates: 2] `cmd_restore` split-path verify-or-bless (DEFERRED core) + restore-side disk check + end-to-end cycle
  - Files: `main.py:1727-1734` (merge + hash + overwrite block in `cmd_restore`); the pre-merge area
    `main.py:1711-1718`
  - Details:
    - **Restore-side disk pre-check (before the merge):** the merged output (~original size) is written
      to `local_folder`; the chunks are already in `restore/`. Before `merge_video_files`, require
      `local_folder` free >= original_size + `_disk_buffer`. If insufficient: print a clear hard-stop
      message and RETURN FALSE leaving the chunks in `restore/` (no merge attempted, nothing to roll
      back — this is pre-PONR and pre-merge). (Use sum of chunk sizes in `restore/` or `tech_spec.size_bytes`
      as the estimate.)
    - **Seed:** `seed = entry["split_info"].get("merge_seed") or entry["short_id"]` — choose/persist
      BEFORE the merge so the canonical-producing merge uses exactly the stored seed.
    - Merge with `merge_video_files(chunk_paths_in_restore, target_path, seed=seed)` (Step 1 helper).
    - Compute `new_hash = calculate_file_hash(target_path)`.
    - If `entry.get("re_hashed") is True`: VERIFY `new_hash == entry["hash"]`. On match: do NOT change
      `hash`; proceed to `status="restored_local"` + normal cleanup. On MISMATCH: raise a loud,
      greppable corruption alarm (names id, expected vs actual hash, and stored `merge_tool` for drift
      triage), set a failed-restore status, and RETURN FALSE **before** `mark_point_of_no_return()` —
      do NOT cross the PONR, do NOT delete chunks (leave them in `restore/`); reuse the existing
      pre-PONR `journal.rollback(library)` of the reproducible merged output exactly as the merge-fail
      branch at `main.py:1722-1725`.
    - Else (first bless): SET `entry["hash"] = new_hash`, `entry["re_hashed"] = True`,
      `entry["split_info"]["merge_seed"] = seed`, `entry["split_info"]["merge_tool"] =
      _current_merge_tool()`, `entry["split_info"]["rehashed_at"] = _rehashed_at()`,
      `status="restored_local"`, then proceed to the EXISTING PONR
      (`journal.mark_point_of_no_return()` at `main.py:1746`), `journal.commit()`, chunk cleanup UNCHANGED.
    - **End-to-end:** state in the diff that `cmd_fetch_restore` (`main.py:2224`) and
      `cmd_restore_group` (`main.py:1809`) inherit this unchanged — no edits there; an end-to-end test
      covers both the verify and bless paths (Step 9).
    - CHANGE-GATED INVARIANTS (state in PR/commit and keep true): PONR location `main.py:1746` does NOT
      move; journal format/durability unchanged; bless/verify writes are PRE-PONR (inside the
      already-journalled reproducible-output window); the alarm path returns before the PONR and reuses
      the existing reproducible-output rollback; chunks NOT deleted on alarm. Only the merge command
      (`--deterministic <seed>`) and the overwrite→verify-or-bless logic change.
  - Acceptance: first restore blesses (hash=merged, `re_hashed` true, seed=short_id, tool + rehashed_at
    stored); second restore leaves `hash` unchanged and returns True; a forced mismatch with
    `re_hashed=true` returns False, prints the alarm, does NOT call `mark_point_of_no_return`, leaves
    chunks; the restore-side disk check hard-stops when free space is short. `tests/test_rollback.py`
    stays green. `pytest -q` green.
  - Judge criteria: (1) Correctness of verify-vs-bless + the no-PONR-on-alarm guarantee (the integrity
    contract — most important); (2) Change-gate fidelity: PONR position, journal calls, pre-PONR
    reproducible-output rollback byte-for-byte preserved on happy + merge-fail paths; (3) Minimal
    surgical diff confined to the restore block (+ seed persistence + the pre-merge disk check); (4)
    Readable alarm message (greppable; id + expected/actual + merge_tool).
  - Candidate approaches:
    - A: Inline the verify-or-bless branch directly at `main.py:1727-1734`, persisting the seed in place
      just before the merge, keeping all journal calls exactly where they are.
    - B: Extract a pure helper `bless_or_verify_merged_hash(entry, new_hash) -> ("bless"|"ok"|"mismatch")`
      that returns a decision the `cmd_restore` body acts on (mutations + journal stay in `cmd_restore`),
      isolating the decision logic for unit-testability without touching the rollback seam.

- [x] 3. [model: opus] [effort: max] [candidates: 2] EAGER bless-at-push + promote-at-replace + re_hashed-reset on re-split
  - Files: `main.py:1144-1164` (post-split chunk-hash block in `cmd_push`), `cmd_push` signature
    `main.py:1048`, `cmd_replace` `main.py:1483-1487`, Step-1 helper at `main.py:231`
  - Details: EAGER mode (only when the new `eager_rehash` kwarg is True AND a split actually happened
    this run): immediately after the chunk-hash loop populates `chunk_metadata` and `split_info` is
    written (`main.py:1158-1164`): generate `seed = entry["short_id"]`, merge the just-created chunks
    into a temp under `_parts_base(...)` (e.g. `<parts_base>/<base>.rehash_tmp.mkv`) with
    `merge_video_files(chunk_paths, tmp, seed=seed)`, compute `canonical = calculate_file_hash(tmp)`,
    DELETE the temp, and store under `split_info` (NOT on `entry["hash"]`, NOT `re_hashed`):
    `split_info["merge_seed"]=seed`, `split_info["merge_tool"]=_current_merge_tool()`,
    `split_info["canonical_hash"]=canonical` (transient holding field for the eager-blessed hash pending
    promotion). Persist via the existing `save_library` at `main.py:1164` (these fields live under the
    already-journalled `split_info` — eager's push-time writes stay confined to `split_info`, so NO new
    un-journalled rollback-relevant state is introduced; STATE THIS in the diff). The disk feasibility
    for eager is guaranteed by the Step-4 pre-flight (eager-can't-fit hard-stops before the split), so
    by the time we reach the eager merge here, space is known-sufficient.
    PROMOTE-AT-REPLACE: in `cmd_replace`, just before `library[manual_id]["status"] = "archived"` and
    the final `save_library` (`main.py:1483`), if `split_info.get("canonical_hash")` is present and
    `re_hashed` is not already True: set `entry["hash"] = split_info["canonical_hash"]`,
    `entry["re_hashed"] = True`, `entry["split_info"]["rehashed_at"] = _rehashed_at()`, and drop the
    transient `canonical_hash`. This happens AFTER the replace PONR has already been crossed and mutates
    only in-memory library fields saved by the existing `save_library`/`journal.commit()` — adds NO new
    PONR, does NOT move the replace PONR (`main.py:1454`), no-op for non-eager / non-split entries.
    RE-SPLIT REHASH RESET (applies to BOTH deferred and eager — closes the re-push false-alarm hole the
    user flagged): whenever `cmd_push` writes a NEW `split_info` from a freshly-performed split (the
    new-split branch ~`main.py:1158-1164`, NOT the resume branch that reuses an existing `_parts/`), set
    `library[manual_id]["re_hashed"] = False` and DROP any stale `split_info` canonical fields
    (`merge_seed`/`merge_tool`/`rehashed_at`/`canonical_hash`) when writing the new chunk list — the old
    canonical pertains to the OLD chunks and must not survive new chunks. Effect: a re-push that
    re-splits an already-blessed entry yields NEW chunks with a CLEARED canonical, so the next restore
    re-blesses (deferred) — or the eager block above re-computes the canonical for the new chunks —
    instead of the next restore false-alarming a mismatch against a stale canonical. A brand-new entry is
    a no-op (re_hashed was absent). A RESUME (existing `_parts/`, same chunks) must NOT reset (same chunks
    → canonical still valid) — gate the reset on the new-split branch ONLY. This reset is a plain library
    field write under the already-journalled `split_info`/entry save; it adds NO PONR and no new
    rollback-relevant state.
    Thread `eager_rehash` (default False) through `cmd_push` and the callers `cmd_push_group`
    (`main.py:1388`), `cmd_prep_push_rep` (`main.py:2084`), `cmd_prep_push_rep_season` (`main.py:2190`).
  - Acceptance: `push … rehash` on a split entry writes `merge_seed`/`merge_tool`/`canonical_hash` into
    `split_info` while `entry["hash"]` still equals the original and `re_hashed` is absent/False; a
    subsequent `cmd_replace` promotes `canonical_hash` into `entry["hash"]`, sets `re_hashed=true`, and
    stamps `rehashed_at`. The replace PONR and `tests/test_rollback.py` are unchanged. `pytest -q` green.
    RE-SPLIT RESET: a deferred `push` that re-splits an already-`re_hashed=true` entry leaves it
    `re_hashed=false` with the stale `split_info` canonical fields cleared; a RESUME of an existing
    `_parts/` does NOT reset; a brand-new push is a no-op (re_hashed stays absent/False).
  - Judge criteria: (1) `entry["hash"]` consistent with on-disk file across the whole window so
    `cmd_check` is correct; eager writes confined to `split_info`; promotion only at replace (most
    important); (2) Change-gate fidelity: no new PONR, no un-journalled rollback-relevant state,
    replace/push PONR positions unchanged; (3) Surgical diff + clean kwarg threading through the three
    orchestrators; (4) temp file always cleaned up (even on merge failure — fall back to deferred, do
    not abort the push for an eager-merge hiccup).
  - Candidate approaches:
    - A: transient `split_info["canonical_hash"]` at push, promoted into `entry["hash"]` at replace.
    - B: store the canonical at push AND a top-level `pending_promote: true` flag so `cmd_replace` keys
      off a single explicit boolean (tradeoff: one more top-level field vs. clearer promote trigger).

- [x] 4. [model: opus] [effort: high] Hard disk pre-flight (push single-item + season + restore-side already in Step 2)
  - Files: `cmd_push` `main.py:1048` (just before the split decision ~`main.py:1115-1135`);
    `cmd_prep_push_rep_season` `main.py:2104` (after `target_ids` built, before the loop ~`main.py:2140`);
    `cmd_push_group` `main.py:1343`
  - Details:
    - **Single push (`cmd_push`):** after computing `should_split` (the existing logic at
      `main.py:1118-1126` for SIZE_*, plus COUNT≥2) and BEFORE `split_video_file`, compute
      `will_split` and call `_free_space_ok(_parts_base(local_folder, temp_dir, manual_id),
      file_size, will_split, eager_rehash)`. If NOT ok: print a hard-stop message with the exact
      shortfall and remedies — *free space*, *pass `tempdir <other-volume-path>`*, and (if eager)
      *drop the `rehash` token to use deferred (1X)* — and RETURN FALSE **before** creating `_parts`/
      `checksums` or splitting (nothing created yet → nothing to roll back). The resume branch (a
      pre-existing `_parts/`) SKIPS the check (chunks already exist). Non-split → `_required_extra_bytes`
      is 0 → always passes.
    - **Season / group pre-flight (the user's refinement):** episodes are processed SEQUENTIALLY with
      per-item `_parts` cleanup, so the PEAK disk is the LARGEST SINGLE episode that will split, NOT the
      sum. In `cmd_prep_push_rep_season` (and `cmd_push_group`), after the `target_ids` list is built
      (range-filtered), compute, for each target episode, its `_required_extra_bytes(os.path.getsize(file),
      will_split_for_that_file, eager_rehash)`, take the MAX, and check it ONCE against the target volume
      (`temp_dir` if provided else the season folder volume) before processing ANY episode. If the max
      doesn't fit → hard-stop before starting, naming the offending (largest) episode and the remedies.
      If NO episode will split → 0 extra → proceed. (Per-item `cmd_push` still does its own guard as
      defense-in-depth; the season guard is the "don't even start" early failure.)
    - The check is READ-ONLY (`shutil.disk_usage`) and runs before any artifact creation → it has NO
      rollback interaction (returns cleanly with nothing to undo). State this.
  - Acceptance: a single push whose target volume lacks `1X` (deferred) / `2X` (eager) + buffer
    hard-stops before splitting with the remedy message and creates no `_parts`; a season run where the
    largest splitting episode won't fit hard-stops before processing any episode (and names it); a
    season of all-non-split files proceeds with no extra-space requirement; a resume (existing `_parts/`)
    skips the check. `pytest -q` green.

- [x] 5. [model: opus] [effort: max] [candidates: 2] Optional `tempdir <path>` — redirect chunks + eager merge temp off the media volume
  - Files: `cmd_push` `main.py:1048-1342` (parts_dir/checksum_dir derivation `main.py:1062-1063`, the
    resume branch `main.py:1110-1113`, the split/mkdirs `main.py:1130-1135`, the eager temp from Step 3,
    the cleanup `main.py:1304-1305`); the `RollbackJournal` dir records `main.py:1130-1135`
  - Details: Add a `temp_dir` kwarg (default None) to `cmd_push` (threaded from the CLI in Step 6 and
    through the three orchestrators). When `temp_dir` is provided:
    - `parts_dir = os.path.join(_parts_base(local_folder, temp_dir, manual_id), SPLIT_DIR_NAME)` — i.e.
      chunks live under `temp_dir/<safe manual_id>/_parts`. The eager merge temp (Step 3) also lives
      under that base. `checksum_dir` STAYS at `os.path.join(local_folder, CHECKSUM_DIR_NAME)` and the
      `RollbackJournal(local_folder, manual_id)` STAYS in `local_folder` (small + recovery-critical).
    - **Resume:** the resume branch (`main.py:1110`) checks THIS `parts_dir` (the temp location). The
      user must re-pass the SAME `tempdir <path>` when resuming (Open Decision: re-pass, resolved). If a
      resume is attempted without it, the resume branch simply finds no `_parts/` and re-splits per
      normal — document this clearly in the hard-stop/usage text.
    - **Rollback:** `journal.record_create_dir(parts_dir)` is called with the temp path (only when
      created-this-run, exactly as today). The journal FORMAT is unchanged — only the recorded path
      value differs; created-this-run scoping is preserved (a pre-existing temp `_parts/` is NEVER
      journalled/deleted); the PONR is unchanged (push is O-1, no PONR). The pre-upload rollback removes
      the temp `_parts/` it created. STATE these invariants in the diff — this is change-gate-ADJACENT
      (it changes WHERE a this-run dir is created, not the journal format/durability/PONR/scoping).
    - Validate `temp_dir` exists + is writable up front (reuse the Step-1 `_parts_base` validation); a
      bad temp_dir hard-stops with a clear message before any work.
    - Non-split push → `temp_dir` is a no-op (no chunks).
  - Acceptance: `push <id> SIZE_MB <v> tempdir D:\test` writes chunks under `D:\test\<id>\_parts`, the
    eager temp under the same base, `checksums/` + the journal under the media folder; chunks upload +
    delete normally; a pre-upload failure rolls back and removes the temp `_parts/` (and never a
    pre-existing one); resume with the same `tempdir` finds and continues the chunks. `tests/test_rollback.py`
    green. `pytest -q` green.
  - Judge criteria: (1) Correctness: chunks/eager-temp on the temp volume, checksums/journal in
    `local_folder`, resume + cleanup correct (most important); (2) Change-gate fidelity: journal format/
    durability/PONR/created-this-run scoping byte-for-byte preserved, only the recorded path value moves;
    (3) Disk pre-check (Step 4) correctly targets the temp volume when `temp_dir` is set; (4) Surgical
    diff + clean threading + good validation/messaging for a bad/full temp dir.
  - Candidate approaches:
    - A: compute a single `parts_base` early in `cmd_push` and derive `parts_dir`/eager-temp/resume from
      it; minimal branching, `temp_dir=None` reproduces today's `local_folder` paths exactly.
    - B: a thin `TempLayout` helper object encapsulating {parts_dir, checksum_dir, eager_tmp, journal_dir}
      so every path decision routes through one place (clearer separation, slightly larger diff).

- [ ] 6. [model: opus] [effort: high] CLI: thread the `rehash` + `tempdir <path>` tokens through the dispatch + usage
  - Files: `main.py:2256-2280` (usage), `main.py:2417-2456` (`push`), `main.py:2458-2488` (`push_group`),
    `main.py:2290-2321` (`prep_push_rep`), `main.py:2323-2359` (`prep_push_rep_season`)
  - Details: Add a bareword `rehash` flag (no value → `eager_rehash=True`) and a `tempdir <path>` token
    (value → `temp_dir=<path>`) to all four parsers, matching the existing `device`/`chunks`/`episodes`
    token style. CRITICAL: both MUST be matched explicitly in each `while` loop (like `device`) so they
    are not swallowed into the filepath/folder positional accumulation in `prep_push_rep` /
    `prep_push_rep_season`. Thread `eager_rehash=` and `temp_dir=` into the corresponding command calls
    (`cmd_push`, `cmd_push_group`, `cmd_prep_push_rep`, `cmd_prep_push_rep_season` — add the kwargs to
    those signatures and pass through). Update the usage strings (`main.py:2258-2259`, `2269-2270`) to
    show `[rehash] [tempdir <path>]`. Token spellings `rehash` / `tempdir` are Open Decisions 2 / (new),
    defaults — change only the token string if the user prefers others.
  - Acceptance: `push <id> SIZE_MB 9900 rehash tempdir D:\test device movies` parses id/size/eager/
    temp_dir/device correctly without corrupting the path; `prep_push_rep_season <id> "<folder>" SIZE_MB
    9900 episodes 1-3 rehash tempdir E:\scratch device series` parses folder/size/episodes/eager/temp/
    device all correctly; omitting both keeps `eager_rehash=False`, `temp_dir=None` (byte-identical to
    today). Usage shows the tokens. `pytest -q` green.

- [ ] 7. [model: sonnet] [effort: medium] One-time metadata migration: stamp `re_hashed:false` on existing split entries
  - Files: new `tools/migrate_rehash_flag.py` (one-shot, mirrors `tools/migrate_lib.py` style)
  - Details: Idempotent one-shot — load all three libraries (`mvcommon.load_library`/`save_library`),
    and for every leaf entry with `entry.get("split_info", {}).get("is_split") is True` that lacks a
    `re_hashed` key, set `re_hashed = false`. Leave non-split entries and `season_map` parents untouched.
    Print a summary (`scanned / stamped / already_had_flag / skipped_non_split`). Do NOT compute hashes;
    do NOT touch `merge_seed`/`merge_tool`/`rehashed_at` (bless-time only). Metadata-only, safe to re-run.
    Module docstring MUST state "Never touch real C:\\Media files or real library_*.json" — TESTS run it
    against the `sandbox` libraries; the real run is a manual command the user invokes.
  - Acceptance: against a sandbox library with one split + one non-split entry, stamps `re_hashed:false`
    on the split entry only, no-op on re-run, never writes a hash.

- [ ] 8. [model: opus] [effort: high] Add the deterministic real-mkvmerge test fixture + binding-safe scaffolding to conftest
  - Files: `tests/conftest.py`
  - Details: Read `docs/testing-strategy.md` first. Add a fixture yielding real mkvmerge chunks for the
    determinism test, GATED on real mkvmerge (skip cleanly when `MKVMERGE_PATH` is missing AND
    `shutil.which("mkvmerge")` is None, mirroring `ffmpeg_multichunk_mkv`). Build on `ffmpeg_multichunk_mkv`
    to produce a real source MKV, split it with the real `split_video_file` (small `SIZE_MB` to force ≥2
    chunks) into a temp `_parts`, and yield chunk paths + a temp output dir. Conftest is a binding hazard
    → opus per testing-strategy §10/§4.6. Do NOT redirect `LIBRARY_*` here. Guard every path against
    `C:\Media`. Document the fixture in a one-line comment.
  - Acceptance: the fixture skips cleanly without mkvmerge/ffmpeg and, when present, yields ≥2 real chunk
    paths + an output dir under `tmp_path`. `pytest -q` green with and without mkvmerge.

- [ ] 9. [model: sonnet] [effort: medium] Write the rehash test suite
  - Files: new `tests/test_rehash.py`
  - Details: Read `docs/testing-strategy.md` first. Module docstring MUST state "Never touch real
    C:\\Media files or real library_*.json." and "Run `pytest -q` and fix failures before marking the
    step done." Cover:
    - **DETERMINISM (linchpin):** using the Step-8 fixture, merge the same chunks 2–3 times with the
      SAME seed via `main.merge_video_files(paths, out, seed="f6b674")` → assert identical SHA256. Skip
      without mkvmerge.
    - **DEFERRED restore (sandbox):** first `cmd_restore` blesses (`hash` set, `re_hashed` true,
      `merge_seed`=short_id, `merge_tool`+`rehashed_at` under `split_info`); second `cmd_restore`
      verifies and does NOT change `hash`; a tampered/mismatching merged hash with `re_hashed=true`
      raises the alarm, returns False, does NOT cross the PONR (no `crossed_ponr` / chunks remain).
      Use the `sandbox` fixture (patches BOTH `mvcommon.LIBRARY_*` AND `main.LIBRARY_*`). Where a real
      merge is unavailable, monkeypatch `merge_video_files` to write fixed bytes + stub the hash so the
      bless/verify LOGIC is testable without mkvmerge.
    - **END-TO-END fetch→restore (point A):** drive `cmd_restore` (the path `cmd_fetch_restore` calls)
      for (i) a not-yet-`re_hashed` entry → chunk-verify → merge → bless → `re_hashed` true; and (ii) an
      already-`re_hashed` entry → chunk-verify → merge → verify-against-canonical (hash unchanged). A
      corrupt chunk is caught by the existing pre-merge chunk check BEFORE any bless/verify.
    - **EAGER push:** with the disk-guard mocked OK + a recorded merge, `cmd_push(…, eager_rehash=True)`
      writes `merge_seed`/`merge_tool`/`canonical_hash` into `split_info` while `entry["hash"]` stays
      original and `re_hashed` False; `cmd_replace` promotes into `entry["hash"]`, sets `re_hashed=true`,
      stamps `rehashed_at`. Use `sandbox` + `mock_device` + `fake_dummy`.
    - **DISK pre-flight:** monkeypatch `shutil.disk_usage`/`main._free_space_ok` to report insufficient
      space → `cmd_push` hard-stops BEFORE creating `_parts` (assert no `_parts`, returns False);
      season pre-flight picks the MAX single-episode requirement (build a 2-episode sandbox where only
      the larger splits, assert the max is used) and hard-stops when it doesn't fit; non-split → always
      passes.
    - **tempdir:** `cmd_push(…, temp_dir=tmp_path/"scratch")` lands chunks under
      `scratch/<id>/_parts`, keeps `checksums/` + journal in the media folder, uploads + cleans up; a
      forced pre-upload failure rolls back and removes the temp `_parts/` (and not a pre-existing one).
    - **cmd_check window:** in the eager pre-replace window (`canonical_hash` set but `entry["hash"]`
      still original, original on disk) `cmd_check` PASSES; after promote the file is a dummy so
      `cmd_check` short-circuits (does not falsely FAIL).
    - **Migration:** run `tools/migrate_rehash_flag.py` against a sandbox library → `re_hashed:false` on
      split entries only; non-split untouched; idempotent.
    - Library-I/O uses `sandbox`; ADB uses `mock_device`; never real adb/browser.
  - Acceptance: `pytest tests/test_rehash.py -q` passes (determinism skips cleanly without mkvmerge);
    full `pytest -q` stays green incl. `tests/test_rollback.py` + `tests/test_baseline_happy_path.py`.

- [ ] 10. [model: sonnet] [effort: low] Record decisions in the tracked DECISIONS.md and mirror the plan
  - Files: `docs/feature-split-hash-deterministic/DECISIONS.md` (new), `…/PLAN.md` (identical to root)
  - Details: Record the locked decisions: Way A + `--deterministic` (spike hashes); DEFERRED default /
    EAGER opt-in; keep-master-until-replace; schema (drop `original_hash`, repurpose `entry["hash"]`,
    add `re_hashed`/`merge_seed`=short_id/`merge_tool`/`rehashed_at`); end-to-end cycle; hard disk
    pre-flight (1X/2X + max(1%,2GB), season=max-episode, eager-cant-fit=hard-stop); `tempdir` redirect
    (resume re-pass); the change-gate statement that PONR/journal contract is unchanged and only the
    blind hash-overwrite is reversed (user pre-authorized); the resolved Open Decisions. Doc-only — do
    NOT edit rollback docs here (architect step). Mirror PLAN.md into the docs folder verbatim.
  - Acceptance: both files exist under `docs/feature-split-hash-deterministic/`; docs PLAN.md == root
    PLAN.md; DECISIONS.md captures the above.

- [ ] 11. [model: opus] [effort: high] (ARCHITECT, final) Update docs to reverse the stale rationale + document the new mechanism
  - Files: `ARCHITECTURE.md` (§6.4 note `ARCHITECTURE.md:457`; §7.7 split flow `ARCHITECTURE.md:858`;
    §10 Stage 5 `ARCHITECTURE.md:1252-1253`; the command table for the new tokens), `improvements_tierA.md:8`,
    `README.md`
  - Details: Reverse the "overwrite is intentional / mkvmerge never byte-identical" claim with the
    empirical finding (default merge non-deterministic; `--deterministic <seed>` byte-identical →
    canonical hash; blessed at first restore or eager→promote-at-replace; `re_hashed`/`merge_seed`/
    `merge_tool`/`rehashed_at` schema). Update `improvements_tierA.md:8` from "Do not fix this" to a
    pointer that this was fixed via deterministic re-merge (DOC update, NOT marking any IMP done — the
    IMP-to-mark-done list is EMPTY). Document the new `rehash` + `tempdir <path>` tokens and the hard
    disk pre-flight in README + ARCHITECTURE command tables. Add an ARCHITECTURE note that the
    `cmd_restore` change + the `tempdir` `_parts` relocation do NOT alter the rollback PONR/journal
    contract (cross-ref §12a / ROLLBACK_MECHANISM §10). FLAG (in DECISIONS.md / PR body) that the saved
    memory `feedback_mkvmerge_hash_divergence` is now stale and should be updated/retired — human
    manages memory; do NOT edit memory. Note in passing (do not fix) the stale `apple_tv_ui_roadmap.md`
    §5 "Original Hash:" marker.
  - Acceptance: ARCHITECTURE §6.4/§7.7/§10 + `improvements_tierA.md:8` no longer claim the overwrite is
    intentional/unfixable; README documents `rehash`/`tempdir` + the disk pre-flight; memory-retirement
    flag recorded for the human. No IMP marked done.

---

## Tests (summary)

Unit/integration (in CI via `pytest -q`):
- Determinism: real-mkvmerge merge×2–3 with the stored seed (= short_id) → identical hash (skip without mkvmerge).
- Deferred restore: bless on first; verify-no-change on second; corruption alarm on mismatch that does NOT cross the PONR or delete chunks.
- End-to-end fetch→restore: not-yet-`re_hashed` → bless; already-`re_hashed` → verify; corrupt chunk caught pre-merge.
- Eager push: bless into `split_info` at push; promote into `entry["hash"]` at replace.
- Disk pre-flight: single push hard-stops before `_parts` when short; season uses MAX single-episode requirement; non-split always passes.
- tempdir: chunks/eager-temp under the temp volume, checksums/journal in the media folder, rollback removes the temp `_parts/`.
- `cmd_check` correctness across the eager pre-replace window.
- Migration: `re_hashed:false` on split entries only; idempotent; non-split untouched.
- Regression: full `pytest -q` green; `test_baseline_happy_path.py` + `test_rollback.py` unaffected.

Fixture rules honored: library-I/O uses `sandbox`; ADB uses `mock_device`; the determinism fixture is
real-mkvmerge and skips when absent; conftest additions are opus (binding hazard). Every test docstring
states "Never touch real C:\\Media files or real library_*.json" and "Run `pytest -q` and fix failures
before marking the step done." No test issues real adb or opens a browser.

### Manual / integration scenario matrix (run DURING implementation, NOT in CI)
Use COPIES of `C:\Media\Test\hash_logic\{Black Friday 2004.mp4 (5 GB), Hot Spot 2 Much (2026).mkv
(7.7 GB)}` split into 2/5/10 chunks. **NEVER modify the real media files, the two source test files,
or the real library JSONs — always copy first.** Free disk is ~47 GB, so eager (2X extra) limits which
file sizes can be eager-tested on `C:` (the 5 GB file needs ~10 GB free for eager; the 7.7 GB ~15 GB);
the `tempdir` option lets you push a copy with chunks on another volume to keep `C:` free. Scenarios:
- with-split deferred restore (twice — bless then verify) + end-to-end `fetch_restore`;
- with-split EAGER push (`rehash`) → replace promote → `check`;
- without-split (single-file) regression (no rehash fields ever appear);
- push-fail mid-chunk (resume still works; re-pass `tempdir` if used; no eager bless on a partial);
- prep-fail / fetch-fail (no canonical written);
- disk pre-flight: attempt a push/season that exceeds free space → confirm it hard-stops BEFORE the
  split with the remedy message and creates no `_parts`; then retry with `tempdir <other volume>`;
- season: a folder where only the largest episode splits → confirm the pre-flight sizes to that episode;
- version-drift: hand-edit a copy entry's `split_info.merge_tool` to a different version, then restore;
  confirm graceful drift messaging (chunk hashes still guarantee content) rather than a hard corruption claim.

---

## Change-gate callouts (CRITICAL)

This task touches rollback-ADJACENT code. The auto-rollback mechanism is load-bearing and change-gated
(`CLAUDE.md`, `docs/feature-auto-rollback/ROLLBACK_MECHANISM.md` §10, `ARCHITECTURE.md` §12a). The user
has PRE-AUTHORIZED two documented changes — (i) reversing the blind hash-overwrite in `cmd_restore` into
verify-or-bless, and (ii) allowing the `_parts/` directory (and eager merge temp) to live under an
optional `tempdir` — and NOTHING ELSE about rollback. The following MUST stay true and be stated in the diff:

1. **`cmd_restore` split path (Step 2):** PONR location (`main.py:1746`) does NOT move; journal format/
   durability (fsync + `os.replace`) unchanged; bless/verify writes are PRE-PONR (inside the already-
   journalled reproducible-output window); the corruption-alarm path returns BEFORE the PONR and reuses
   the existing pre-PONR reproducible-output rollback (`main.py:1722-1725`); chunks NOT deleted on alarm.
2. **EAGER push (Step 3):** eager's push-time writes are confined to `split_info`, ALREADY journalled
   this-run via `record_set_field("split_info")` (`main.py:1158-1159`). No new un-journalled
   rollback-relevant state in the pre-upload window. The canonical is PROMOTED at `cmd_replace` AFTER the
   replace PONR — promotion mutates only in-memory fields saved by the existing `save_library`/
   `journal.commit()`, adds NO new PONR, does NOT move the replace PONR (`main.py:1454`).
3. **`tempdir` `_parts` relocation (Step 5):** the journal FORMAT/durability is unchanged; only the
   recorded created-this-run dir PATH value differs; created-this-run scoping is preserved (a pre-existing
   temp `_parts/` is NEVER journalled or deleted); push remains O-1 (no PONR); resume requires re-passing
   the same `tempdir`. The `checksums/` sidecars and the `RollbackJournal` stay in `local_folder`.
4. **Disk pre-flight (Step 4):** read-only (`shutil.disk_usage`), runs BEFORE any artifact creation, so it
   has zero rollback interaction (a hard-stop returns with nothing created and nothing to undo).
5. **Any deviation from 1–4 is itself a change-gated decision** — STOP and surface it to the user rather
   than silently working around it (CLAUDE.md "Surface fundamental contradictions").

---

## Related-improvements handling (DOCUMENT only — do NOT implement, do NOT bundle)

None are prerequisites and none are bundled. Documented relationships:
- **IMP-R1** (streaming split-upload-delete): DEFERRED mode is no-conflict; EAGER conflicts only WHEN used.
  The new `tempdir` redirect + the hard disk pre-flight partially address R1's disk-peak motivation (offload
  off-volume / fail fast) without implementing the streaming optimization itself — note this overlap for R1's
  future planner.
- **IMP-F2:** alternative integrity philosophy — not blocked, not addressed.
- **IMP-F1:** canonical-on-encrypted sequencing note — if F1 lands, bless on the pre-encryption bytes.
- **IMP-D4/D5/D8:** must be schema-aware when built (`re_hashed`/`merge_seed`/`merge_tool`/`rehashed_at`);
  D5 (`repair_library`) overlaps our one-time `re_hashed` stamp and should subsume it long-term.
- **IMP-C8:** complementary (remote post-push verify) — unaffected.
- **IMP-C9/C10:** the `<short_id>.sha256` sidecar still holds the ORIGINAL hash after bless (we are NOT
  updating it — Open Decision 3); reconcile work must account for sidecar(original) vs entry(canonical).
- **IMP-B3/B6:** complementary — unaffected.

**No improvement is marked done by THIS task.** The closest item is the INVERSE of the
`improvements_tierA.md:8` "do not fix" note — a DOC update (Step 11), NOT an IMP closure. Rule: mark a
related IMP done ONLY if implementation actually closes it; that list is currently EMPTY. If an executor
judges a tracked IMP is fully satisfied, STOP and surface it rather than silently marking it.

---

## Open Decisions (RESOLVED defaults shown; user may still override)

1. **Seed value — RESOLVED: `short_id`.** Stored as `split_info.merge_seed`, reused verbatim forever.
   A separate `split_info.rehashed_at` ISO-timestamp records "when blessed" (not part of the seed).
2. **Eager token spelling — `rehash` (DEFAULT).** Bareword. Change the token string in Step 6 only if preferred.
3. **Update the `<short_id>.sha256` sidecar at bless? — RESOLVED: no.** It keeps the ORIGINAL hash;
   nothing reads it today; IMP-C9/C10 will own reconciliation.
4. **Eager merged-temp location — RESOLVED: under the `_parts` base** (same volume as the chunks: the
   `tempdir` volume if provided, else `local_folder`).
5. **Insufficient disk — RESOLVED: HARD-STOP with remedies** (free space / pass `tempdir` / drop `rehash`
   for deferred). No silent fallback — predictable "the mode you asked for, or stop." Applies to deferred
   (1X) and eager (2X); a non-split needs no extra space.
6. **`entry["hash"]` promotion timing — RESOLVED: promote-at-replace** for eager (keeps `cmd_check`
   correct); deferred writes at first-restore bless.
7. **`tempdir` token spelling — `tempdir <path>` (DEFAULT).** Resume requires re-passing the same value
   (no recorded temp-dir state → keeps the rollback contract untouched).
8. **Disk requirement model — RESOLVED: additional-free + buffer.** Check FREE space on the target volume
   ≥ bytes-to-create (deferred 1X / eager 2X) + `max(1% of need, 2 GB)`. Season/group = MAX single-episode
   requirement (sequential per-item cleanup → peak = largest splitting item, not the sum).

---

## Verification (after all steps)

```powershell
pytest tests/test_rehash.py -q
pytest -q                                 # full suite must stay green (incl. test_rollback, baseline)
python main.py                            # usage shows [rehash] [tempdir <path>] on push / push_group / autopilots
git diff --stat
git diff main.py
```

Manual (user, real data — see the scenario matrix and Closing Handoff for exact commands).

---

## Out of scope

- Way B (raw-byte split / byte-identical reconstruction) — rejected.
- Deleting the master earlier than `cmd_replace` — explicitly kept (O-1/O-2).
- Streaming split-upload-delete (IMP-R1 proper) — only the disk pre-flight + `tempdir` offload are added.
- Any change to the rollback journal FORMAT/durability, PONR locations, created-this-run scoping (D-6/D-7),
  `cmd_*` wrapping, `recover_journal` semantics, season resume-range messaging, or `RollbackHardFail` — only
  the blind hash-overwrite reversal and the `tempdir` `_parts`-path relocation are in scope (both change-gated).
- Back-filling canonical hashes for the ~130 existing archived split entries (migration only stamps
  `re_hashed:false`; they bless lazily on their next restore).
- Renaming the `original_hash` field in the remote `.mvmeta.json` sidecar.
- Updating the saved memory `feedback_mkvmerge_hash_divergence` (human-managed; only flagged).
- `apple_tv_ui_roadmap.md` §5 stale "Original Hash:" marker.
- Marking any IMP done (none are closed by this task).

---

## CLOSING HANDOFF

**Final branch name:** `fix/split_hash_deterministic_rehash`

**PR to `main` (Checkpoint-1 human-gated — create the PR, then STOP and ask the user before merging):**

`gh pr create` title (NO IMP code — net-new, maps to no tracked IMP):
```
fix: verifiable canonical hash for split files via deterministic mkvmerge re-merge
```

PR body order per `docs/git-pr-conventions.md` (Claude summary FIRST, then verbatim original prompt,
then trailer):
```markdown
## Summary
- Split files now get a VERIFIABLE canonical whole-file hash. mkvmerge's default merge is
  non-deterministic, but `--deterministic <seed>` (seed = short_id) reproduces a byte-identical merge;
  that stable merged hash is now treated as canonical.
- DEFERRED (default): the first `cmd_restore` blesses `entry["hash"]` to the deterministic merged hash
  (sets `re_hashed`, stores `merge_seed`/`merge_tool`/`rehashed_at`); later restores VERIFY and alarm on
  mismatch without crossing the restore PONR. Inherited by `fetch_restore`/`restore_group` (end-to-end).
- EAGER (opt-in `rehash` token): blesses the canonical into `split_info` at push (master still on disk)
  and PROMOTES it into `entry["hash"]` at `cmd_replace` (keeps `cmd_check` correct).
- Hard disk pre-flight: a push/season/eager run that would exceed the target volume's free space STOPS
  before the split (deferred 1X / eager 2X + max(1%,2GB) buffer; season = largest splitting episode).
  Optional `tempdir <path>` redirects chunks + the eager merge temp off the media volume.
- Schema: drop a separate `original_hash`; repurpose `entry["hash"]`; add `re_hashed` (entry) +
  `merge_seed`/`merge_tool`/`rehashed_at` (split_info). One-time metadata migration stamps `re_hashed:false`.
- Rollback contract UNCHANGED: PONR locations, journal format/durability, created-this-run scoping, and
  `cmd_*` wrapping are byte-for-byte preserved; only the user-pre-authorized blind hash-overwrite is
  reversed and the `_parts/` dir may live under `tempdir`.

## Changes
- main.py: deterministic merge helper + seed(short_id)/tool/disk-calc/temp-path helpers; verify-or-bless
  in cmd_restore split path (+ restore-side disk check); eager bless-at-push + promote-at-replace; hard
  disk pre-flight in cmd_push + season/group (max-episode); `tempdir` `_parts` relocation; `rehash` +
  `tempdir` tokens in push/push_group/prep_push_rep/prep_push_rep_season + usage.
- tools/migrate_rehash_flag.py: one-time `re_hashed:false` stamp (metadata-only, idempotent).
- tests/conftest.py + tests/test_rehash.py: determinism (real-mkvmerge, gated), deferred bless/verify/
  alarm, end-to-end fetch→restore, eager push+promote, disk pre-flight, tempdir, cmd_check window, migration.
- Docs: ARCHITECTURE §6.4/§7.7/§10 + improvements_tierA.md:8 reversed; README updated; flagged the stale
  `feedback_mkvmerge_hash_divergence` memory for human retirement.

## Test plan
- `pytest -q` (full suite green; determinism test skips without mkvmerge; test_rollback +
  test_baseline_happy_path unaffected).
- Manual scenario matrix run against COPIES of the two `C:\Media\Test\hash_logic` files (2/5/10 chunks;
  deferred-restore-twice + end-to-end fetch_restore, eager-push→replace→check, push-fail resume, disk
  pre-flight hard-stop + tempdir retry, season max-episode sizing, version-drift).

---

## Original task prompt
> <PASTE THE COMPLETE VERBATIM TASK PROMPT THAT KICKED OFF THIS WORK HERE — do not trim or paraphrase.>

🤖 Generated with [Claude Code](https://claude.com/claude-code)
```

**MANUAL TEST COMMANDS the user can run from the feature branch** (against real data — always copy test
files first; never touch the real library or the two source files):

```powershell
# 0. One-time metadata migration (stamps re_hashed:false on existing split entries; metadata-only)
python tools\migrate_rehash_flag.py

# 1. DEFERRED restore (default): bless on first restore, verify on second (end-to-end fetch->restore).
python main.py fetch_restore mov-en-2013-coherence
#    -> first run blesses: entry hash becomes the deterministic merged hash; re_hashed=true;
#       split_info gains merge_seed (=f6b674) + merge_tool + rehashed_at. Inspect library_movies.json.
python main.py fetch_restore mov-en-2013-coherence
#    -> second run VERIFIES (hash unchanged); a mismatch would print the corruption alarm.

# 2. EAGER push via the new token (master on disk; disk pre-flight guards the 2X need). Use a COPY.
python main.py prep mov-test-rehash "C:\Media\Test\hash_logic\copies\Black Friday 2004 copy.mp4"
python main.py push mov-test-rehash SIZE_MB 2000 rehash device movies
#    -> split_info gains merge_seed/merge_tool/canonical_hash; entry hash still original; re_hashed false.
python main.py replace mov-test-rehash
#    -> promote: entry hash becomes canonical; re_hashed=true; rehashed_at stamped. Verify in the json.

# 3. Off-volume temp dir (keeps C:\Media free; chunks + eager temp go to D:\test):
python main.py push mov-test-rehash SIZE_MB 2000 rehash tempdir D:\test device movies
#    -> chunks under D:\test\mov-test-rehash\_parts; checksums/ + journal stay in the media folder.
#    If interrupted, resume by RE-PASSING the same tempdir:
python main.py push mov-test-rehash tempdir D:\test device movies

# 4. Hard disk pre-flight (don't even start when it won't fit):
#    Point at a file/volume where free space < the requirement, with NO tempdir, and confirm it
#    hard-stops BEFORE splitting and creates no _parts; then retry with `tempdir <volume with space>`.

# 5. EAGER season auto-pilot (token threaded through; pre-flight sizes to the largest splitting episode):
python main.py prep_push_rep_season tv-en-2020-someshow-s01 "C:\Media\Test\hash_logic\copies\Season 01" SIZE_MB 9900 episodes 1-2 rehash device series

# 6. cmd_check across the eager pre-replace window (run BETWEEN `push ... rehash` and `replace`):
python main.py check mov-test-rehash
#    -> PASS against the on-disk original (entry hash still original in that window).

# 7. Version-drift simulation (graceful re-bless, NOT a false corruption claim):
#    Hand-edit a COPY-backed entry's split_info.merge_tool to a different version, then restore;
#    confirm the message reports tool drift (chunk hashes guarantee content) rather than hard corruption.

# Always finish by confirming the rollback suite + full suite are green on the branch:
pytest -q
```

> Checkpoint-1 reminder: do NOT merge to `main` without the user's explicit confirmation. Create the PR,
> then STOP and ask. Checkpoint-2 (archive tag + branch delete) is also human-gated.
