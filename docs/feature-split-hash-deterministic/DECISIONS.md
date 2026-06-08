# Split-Hash Deterministic — Decision Record

Status legend: ✅ confirmed / user-pre-authorized  ·  ☑️ accepted default (planner-recommended,
user did not object)  ·  ◉ resolved open decision (defaults shown)

All decisions were locked during the 2026-06-07 planning session and applied across
Steps 1–9. See `PLAN.md` (the canonical plan) and `STATUS.md` (as-built record) for
the full context. Step 11 (docs architect) will record the memory-retirement flag and
the stale-rationale reversals in `ARCHITECTURE.md`/`README.md` — that is out of scope here.

---

## ✅ D-1. Core approach: Way A + `mkvmerge --deterministic <seed>`

**What was decided:** Reconstruct split files using `mkvmerge --deterministic <seed>` and
treat the resulting stable merged hash as the canonical whole-file hash.

**Why:** An empirical spike (mkvmerge v97.0, 5 GB file) proved the two failure modes of the
pre-existing approach:

- **Default mkvmerge merge is NON-deterministic.** Two merges of the same chunks produced
  different SHA256 digests (`8595b46b…` vs `5f007b6e…`) because mkvmerge injects a random
  segment UID and a current-time mux timestamp on every invocation. The `cmd_restore` blind
  hash-overwrite (the pre-existing `library[id]["hash"] = new_hash` at `main.py:1727-1734`)
  was therefore a circular no-op: the stored hash could never be verified on a subsequent
  restore.

- **`--deterministic <seed>` produces byte-identical output across separate runs.** The same
  chunks merged twice with the same seed yielded `a0b239a1…` both times. That stable hash is
  verifiable on every future restore.

**Way B rejected.** Raw-byte split (splitting the original file byte-for-byte so the
re-concatenated halves are byte-identical to the original) was considered and rejected:

1. Unproven Google Photos ingestion of non-playable raw-byte parts.
2. Strands the ~130 existing archived mkvmerge-chunk entries (they used mkvmerge splitting,
   not raw-byte splitting; they cannot be retroactively converted without re-fetching and
   re-splitting every one).
3. Loses per-chunk playability, which is a feature of the current mkvmerge-split approach.

**Note — this decision reverses a previously documented assumption.** `improvements_tierA.md:8`
stated "do not fix this — mkvmerge never byte-identical." The empirical spike proved that
claim false for the deterministic mode. The user PRE-AUTHORIZED this reversal. The Step-11
architect will update `ARCHITECTURE.md`, `README.md`, and `improvements_tierA.md:8`
accordingly; the `feedback_mkvmerge_hash_divergence` saved memory is stale and is flagged for
human retirement (only the Step-11 note flags it — Claude does not edit memory).

---

## ✅ D-2. Default DEFERRED / opt-in EAGER rehash

**What was decided:** Two modes, with deferred as the default:

### DEFERRED (default — no extra token)

`cmd_push` is UNCHANGED in disk profile. The canonical hash is blessed at the FIRST
`cmd_restore`, which already merges the chunks today. The only added cost is passing
`--deterministic <seed>` to the merge and replacing the blind overwrite with a
**verify-or-bless** branch:

- If `re_hashed` is already `True` → **VERIFY**: compare the newly merged hash against the
  stored `entry["hash"]`. A match continues normally (hash is not changed). A mismatch fires a
  corruption alarm, returns `False`, and does NOT cross the PONR or delete chunks.
- If `re_hashed` is `False` / absent → **BLESS**: set `entry["hash"] = merged_hash`, flip
  `re_hashed=True`, and store `merge_seed` / `merge_tool` / `rehashed_at` under `split_info`.

Disk profile: 1X extra at restore (the merged output; chunks are already present). This is
zero added cost relative to today's `cmd_restore`, which already merges.

### EAGER (opt-in — `rehash` token at push)

After split + chunk-hash at push, merge the just-created chunks once with
`--deterministic <seed>`, hash the merged temp, **delete the temp**, and store the blessed
canonical under `split_info.canonical_hash` (plus `merge_seed` / `merge_tool`). At push time
`entry["hash"]` still holds the original hash and `re_hashed` is absent/False — the canonical
is NOT promoted yet (see D-3). Any eager merge failure gracefully falls back to deferred
(the push is not aborted).

Disk profile: 2X extra at push (1X chunks + 1X eager merge temp, overlapping; temp is
deleted immediately after hashing).

### 3X-vs-2X reasoning that led to deferred-default

A naive "bless-at-push with the merge output kept until replace" design would require
holding: original (1X) + chunks (1X) + merge output (1X) = 3X. The chosen eager design
deletes the temp immediately → 2X. Deferred needs no temp at push → 1X. 3X was rejected as
the default disk profile.

---

## ✅ D-3. Keep the master until `cmd_replace` (unchanged)

The original file is NOT deleted after split. It is retained until `cmd_replace` (the true
PONR where the master leaves disk). This was already the behavior; the task does not change
it. It is load-bearing for O-1/O-2 resume+rollback and is explicitly out-of-scope for
revision.

**EAGER promote-at-replace (not at push).** Because the master is on disk through the
push→replace window, `entry["hash"]` must stay consistent with the on-disk file so that
`cmd_check` (`main.py:935-936`) passes correctly. The eager-blessed canonical is therefore
held in the transient `split_info.canonical_hash` field (Step 3, bake-off winner A) and
PROMOTED into `entry["hash"]` at `cmd_replace`, after the replace PONR has already been
crossed — adding no new PONR and no un-journalled rollback-relevant state.

---

## ✅ D-4. Schema (locked — no `original_hash` field)

The idea of adding a separate `original_hash` field to record the pre-split SHA256 was
considered and **dropped**. Instead, `entry["hash"]` is REPURPOSED: it holds the original
hash until the canonical is blessed, then the canonical merged hash thereafter.

New fields added:

| Field | Location | Value | Written at |
|---|---|---|---|
| `re_hashed` (bool) | entry top level | `False` → migration stamp; `True` → blessed | migration / first bless |
| `merge_seed` (string) | `split_info` | = entry's `short_id`; reused verbatim forever | first bless (deferred) or push (eager) |
| `merge_tool` (string) | `split_info` | e.g. `"mkvmerge v97.0"`; captured at bless | first bless (deferred) or push (eager) |
| `rehashed_at` (ISO-8601 UTC) | `split_info` | when `re_hashed` flipped `True` | first bless or promote-at-replace (eager) |

**`merge_tool` rationale.** Stored so a future MKVToolNix upgrade degrades to a graceful
re-bless (bless path) rather than a false corruption alarm. Chunk hashes still guarantee
content integrity regardless of tool version.

**`re_hashed` reset on re-split.** If an already-blessed entry is re-pushed (new split),
`re_hashed` is reset to `False` and all stale canonical fields (`merge_seed`, `merge_tool`,
`rehashed_at`, `canonical_hash`) are cleared from `split_info`. This closes the re-push
false-alarm hole: without the reset, a subsequent restore would verify a new merge against a
canonical computed from OLD chunks, and alarm. A RESUME of an existing `_parts/` (same
chunks) does NOT reset — the canonical is still valid.

**Remote `.mvmeta.json` sidecar.** `write_remote_mvmeta` (`main.py:984-998`) writes an
`original_hash` field sourced from `entry.get("hash")`. After bless that key will carry the
CANONICAL hash for entries pushed after a bless. This is acceptable (the sidecar is
disaster-recovery redundancy; chunk hashes are the source of truth). The sidecar field is NOT
renamed in this task.

---

## ✅ D-5. End-to-end fetch→restore cycle (explicit scope, 2026-06-07)

The full archived→restored cycle must complete the canonical verification loop:

- **Not-yet-`re_hashed` entry**: verify chunk hashes (existing) → deterministic merge → bless
  → mark `re_hashed=True`.
- **Already-`re_hashed` entry**: verify chunk hashes → deterministic merge → verify merged
  hash against stored canonical (hash unchanged on match; alarm on mismatch without crossing
  PONR or deleting chunks).

This is INHERITED by `cmd_fetch_restore` (`main.py:2224`) and `cmd_restore_group`
(`main.py:1809`) via their existing call through `cmd_restore` — no new code paths are needed.
An explicit end-to-end test covers both the bless and verify paths (test_rehash.py §E2E).

---

## ✅ D-6. Hard disk pre-flight + optional off-volume `tempdir` (2026-06-07)

### Hard disk pre-flight

A push/season/eager run that would exceed the target volume's free space STOPS before the
split with a clear remedy message. Never starts and fails mid-split.

- **Deferred**: requires 1X (`file_size`) extra free + buffer = `max(1% of need, 2 GB)`.
- **Eager**: requires 2X (`2 * file_size`) extra free + same buffer.
- **No split**: 0 extra → check always passes.
- **Season/group**: episodes are processed sequentially with per-item `_parts/` cleanup, so
  peak disk need is the LARGEST single splitting episode, not the sum. The season/group
  pre-flight computes `max(required_extra_bytes per episode)` and checks it ONCE before
  processing any episode.
- **Insufficient disk → HARD-STOP with remedies** (free space, pass `tempdir`, or drop
  `rehash` to halve 2X→1X). No silent fallback — predictable "the mode you asked for, or
  stop."
- **Resume branch skips the check** (chunks already exist; nothing new to create).

The check is read-only (`shutil.disk_usage`), runs before any artifact creation, and has zero
rollback interaction.

### Optional `tempdir <path>` redirect

A `tempdir <path>` token at push redirects `_parts/` chunks and the eager merge temp to
`temp_dir/<safe_manual_id>/` on a different volume. `checksums/` sidecars and the
`RollbackJournal` always stay in `local_folder` (small + recovery-critical).

- **Resume**: must re-pass the same `tempdir <path>` (the temp path is not persisted in the
  library; keeping it out of the journal preserves the rollback contract unchanged — see D-7).
- **Disk pre-flight**: targets the `temp_dir` volume when set (validates `temp_dir` exists +
  is writable before any work).
- A bad/read-only `temp_dir` → hard-stop with a clear message before any work.
- Non-split push → `tempdir` is a no-op (no chunks created).

---

## ✅ D-7. Change-gate stance — rollback contract UNCHANGED

The auto-rollback mechanism is load-bearing and change-gated per `CLAUDE.md` and
`docs/feature-auto-rollback/ROLLBACK_MECHANISM.md` §10. The user PRE-AUTHORIZED exactly two
changes; ALL OTHER rollback behavior is frozen:

### Pre-authorized change (a): blind hash-overwrite reversed to verify-or-bless

`cmd_restore`'s post-merge blind `entry["hash"] = new_hash` (the circular overwrite) is
replaced by the verify-or-bless branch. Everything else about the restore PONR and journal
calls is preserved byte-for-byte:

- PONR location (`main.py:1746`) does NOT move.
- Journal format/durability (fsync + `os.replace`) unchanged.
- Bless/verify writes are PRE-PONR (inside the already-journalled reproducible-output window).
- The corruption-alarm path returns BEFORE the PONR and reuses the existing pre-PONR
  reproducible-output rollback (`main.py:1722-1725`); chunks are NOT deleted on alarm.

### Pre-authorized change (b): `_parts/` dir may live under `tempdir`

The journal FORMAT and durability are unchanged. Only the recorded created-this-run dir PATH
value may differ (it can now live under `temp_dir`). Created-this-run scoping is preserved: a
pre-existing temp `_parts/` is NEVER journalled or deleted. `cmd_push` remains PONR-less
(O-1). `checksums/` and the `RollbackJournal` always stay on `local_folder`.

### Everything else is unchanged (frozen)

PONR locations, journal format/durability, D-6/D-7 created-this-run scoping, the wrapping of
`cmd_prep`/`cmd_push`/`cmd_replace`/`cmd_restore`, `recover_journal()` semantics,
season resume-range messaging, and the `RollbackHardFail` contract are all byte-for-byte
preserved.

**Eager writes at push are change-gate-safe** because they are confined to `split_info` fields
that are already journalled this-run via `record_set_field("split_info")`. No new
un-journalled rollback-relevant state is introduced.

**Promote-at-replace is change-gate-safe** because it runs AFTER the replace PONR has already
been crossed and mutates only in-memory fields saved by the existing
`save_library`/`journal.commit()`. It adds no new PONR and does not move the replace PONR
(`main.py:1454`).

---

## ✅ D-8. Migration approach — lazy bless, no bulk re-hash

A one-time metadata-only script (`tools/migrate_rehash_flag.py`) stamps `re_hashed:false` on
all existing `is_split:true` library entries. Non-split entries are untouched. No hashes are
computed; no chunks are fetched. The script is idempotent and safe to re-run.

**Existing archived split entries bless lazily on their next restore.** The deterministic
merge works on whatever chunks are already stored for that entry — NO re-splitting is needed.
Back-filling canonical hashes for all ~130 existing entries in bulk is explicitly out of scope
(they bless for free at the next `fetch_restore`).

---

## ◉ Resolved open decisions

| # | Question | Resolution | Rationale |
|---|---|---|---|
| 1 | Seed value | `short_id` (entry's own `short_id`; stored in `merge_seed`, reused verbatim) | Deterministic, already unique per entry, zero generation cost; `rehashed_at` is a separate ISO timestamp (not part of the seed) |
| 2 | Eager token spelling | `rehash` (bareword, no value) | Matches the existing bareword-flag style (`chunks`, `device`); short and unambiguous |
| 3 | Update `<short_id>.sha256` sidecar at bless? | No | The sidecar retains the original hash; nothing reads it today; IMP-C9/C10 will own reconciliation (sidecar-original vs entry-canonical) |
| 4 | Eager merge temp location | Under the `_parts` base (same volume as the chunks: `tempdir` volume if provided, else `local_folder`) | Keeps the disk pre-flight target consistent — the check already stats the `_parts` base; no split-volume accounting |
| 5 | Insufficient disk | HARD-STOP with remedies (free space / pass `tempdir` / drop `rehash` for deferred) | Predictable: the mode requested or a stop; no silent fallback |
| 6 | `entry["hash"]` promotion timing | Promote-at-replace (eager); write-at-first-restore (deferred) | Keeps `cmd_check` correct through the push→replace window (on-disk file is still the original master) |
| 7 | `tempdir` token spelling | `tempdir <path>` (two tokens — keyword + value) | Consistent with `device <id>` and `episodes N-M` style; resume must re-pass the same value (no persisted temp-dir state → rollback contract untouched) |
| 8 | Disk requirement model | Additional-free + `max(1%, 2 GB)` buffer; season/group = MAX single splitting item | Sequential per-item cleanup → peak = largest, not sum; conservative absolute floor (2 GB) avoids margin-of-error failures on large files |

---

## ◉ As-built bake-off winners (from STATUS.md)

| Step | Candidates | Winner | Key reason |
|---|---|---|---|
| Step 2 — verify-or-bless in `cmd_restore` | A (inline branch), B (pure helper) | **B** | Isolated the bless/verify/alarm policy in a pure, trivially unit-testable helper; funnels status+save once; prints stored-vs-current `merge_tool` in the alarm |
| Step 3 — eager bless-at-push + promote-at-replace | A (transient `canonical_hash`), B (`pending_promote` flag) | **A** | Transient `split_info.canonical_hash` is a single self-clearing promote signal; B's extra top-level field had to stay coupled across 3 sites; schema stays minimal |
| Step 5 — `tempdir` `_parts` relocation | A (single `base_dir` variable), B (`TempLayout` helper) | **A** (clean redo) | More surgical (+70/-19 vs B's +130/-28); a strictly stronger batch W_OK pre-flight; `TempLayout` was "abstraction ahead of need" for a single consumer |

Step 5 required a clean redo: attempt 1 was disrupted by a session limit (candidate B left a
stub; A completed and patched — never a fair comparison). The attempt-1 merge was reset and
both candidates were re-implemented fully from the Step-4 base before judging. The redo
legitimately confirmed the same winner (A).

---

## ◉ Related improvements — documented, not bundled, none marked done

No improvement is marked done by this task. The closest item (the inverse of
`improvements_tierA.md:8`) is a DOC update in Step 11, NOT an IMP closure.

| IMP | Relationship |
|---|---|
| IMP-R1 (streaming split-upload-delete) | Deferred mode is no-conflict; eager conflicts only when used. The new `tempdir` redirect + hard disk pre-flight partially address R1's disk-peak motivation (offload off-volume / fail fast) without implementing the streaming optimization itself — note this overlap for R1's future planner |
| IMP-F2 (alt integrity philosophy) | Not blocked, not addressed |
| IMP-F1 (canonical-on-encrypted) | If F1 lands, bless on the pre-encryption bytes |
| IMP-D4/D5/D8 | Must be schema-aware when built (`re_hashed`/`merge_seed`/`merge_tool`/`rehashed_at`); D5 (`repair_library`) overlaps our one-time `re_hashed` stamp and should subsume it long-term |
| IMP-C9/C10 | The `<short_id>.sha256` sidecar still holds the ORIGINAL hash after bless (not updated — resolved OD-3); reconciliation work must account for sidecar(original) vs entry(canonical) |
| IMP-B3/B6 | Complementary — unaffected |

**Rule:** mark a related IMP done ONLY if implementation actually closes it. That list is
currently EMPTY for this task.

---

## ◉ Step 11 (architect) — stale-memory flag for human retirement

The saved memory `feedback_mkvmerge_hash_divergence` ("the `cmd_restore` post-merge hash
overwrite is intentional; mkvmerge re-muxes and never produces a byte-identical container —
don't fix") is now **STALE** as of this feature. The spike proved `mkvmerge --deterministic
<seed>` IS byte-identical, and the blind overwrite was replaced by verify-or-bless against a
deterministic canonical hash. **The human should retire/update that memory AFTER this branch
merges** — Claude (the architect) does NOT edit memory files. `ARCHITECTURE.md`
(§6.4/§6.4a/§7.7/§10/§12a), `README.md`, and `improvements_tierA.md:8` were reversed in Step 11.

Out-of-scope observation (NOT fixed, flagged only): `apple_tv_ui_roadmap.md` §5 references an
"Original Hash:" dummy marker that is already stale (the dummy is a real video, not a text
marker). Left untouched per scope.
