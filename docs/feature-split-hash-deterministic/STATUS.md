# Execution Log

Task: Give split files a verifiable canonical whole-file hash via deterministic
mkvmerge re-merge (Way A + --deterministic), default deferred / opt-in eager,
with a hard disk pre-flight + optional off-volume temp dir.

---

## Step 1 — Add merge/seed/tool + disk-calc + temp-path helpers — status: done

Executor: executor-opus (single-executor mode). Model: opus 4.8, effort high.

What changed (all in `main.py`, additive only — no call sites touched, no
rollback/PONR/cmd_* logic altered):

- `main.py:12` — import widened to `from datetime import datetime, timezone`
  (only `timezone` added; `shutil`, `subprocess`, `re` were already imported).
- `main.py:231` — `merge_video_files(chunk_paths, output_path, seed=None)`:
  new optional `seed`. When `seed is None` the argv is byte-for-byte the
  original `[MKVMERGE_PATH, "-o", out, chunk1, "+chunk2", ...]`. When a seed is
  given, the GLOBAL `--deterministic <seed>` is injected immediately before
  `-o`: `[MKVMERGE_PATH, "--deterministic", seed, "-o", out, ...]`. Confirmed
  via `mkvmerge --help` that `--deterministic <seed>` is a global option
  ("Enables the creation of byte-identical files"); spike already proved
  byte-identical merges on v97.0.
- `main.py:262` — `_rehashed_at()` → compact ISO-8601 UTC `Z` string, e.g.
  `2026-06-07T10:02:29Z`.
- `main.py:268` — `_current_merge_tool()` → parses `mkvmerge --version` to
  `"mkvmerge vNN.N"` (verified `"mkvmerge v97.0"` against the real binary);
  returns `"mkvmerge (unknown)"` on ANY failure; NEVER raises (verified under a
  bogus binary path and under a hostile monkeypatched subprocess.run).
- `main.py:286` — `_required_extra_bytes(file_size, will_split, eager)` →
  0 / 1X / 2X for {no-split, deferred split, eager split}.
- `main.py:295` — `_disk_buffer(need)` → 0 when need==0, else
  `max(int(0.01*need), 2*1024**3)`.
- `main.py:303` — `_disk_shortfall(target_dir, file_size, will_split, eager)` →
  `(free, required, shortfall)` for messaging (`required` includes the buffer).
  Never raises; an unstattable dir returns `free=-1` (impossible value) and
  `shortfall=required` so callers can both message and fail the check.
- `main.py:318` — `_free_space_ok(target_dir, file_size, will_split, eager)` →
  bool. Non-splitting op needs 0 extra → returns True without stat'ing the dir;
  otherwise `free >= required` (False on the -1 sentinel). Never raises.
- `main.py:328` — `_parts_base(local_folder, temp_dir, manual_id)` →
  `(base_dir, error)`. No temp_dir → `(local_folder, None)`. With a temp_dir,
  validates it exists + is writable; bad → `(None, reason)`; good →
  `(temp_dir/<filesystem-safe manual_id>, None)` (id sanitized via
  `re.sub(r"[^A-Za-z0-9._-]", "_", manual_id)`; the dir is NOT created here —
  later steps journal + mkdir it). NEVER raises.

Design choice — `_parts_base` invalid-temp_dir signal: returns a
`(base_dir, error)` tuple rather than raising `ValueError`. Rationale:
(1) matches this codebase's return-sentinel + print-and-hard-stop style
(`split_video_file` returns `[]`, `make_video_dummy` returns False);
(2) composes safely with the never-raise disk helpers — Step 4 calls
`_free_space_ok(_parts_base(...)[0], ...)` and a raising `_parts_base` would
defeat the disk helpers' never-raise guarantee; (3) Step 5 wants an up-front
"hard-stop with a clear message before any work", which a reason string serves
directly. Documented in the helper's docstring.

Verification: wrote a throwaway acceptance script exercising every helper +
both argv forms (all assertions passed), then deleted it. `_current_merge_tool`
confirmed returning `"mkvmerge v97.0"` in a clean process.

pytest: `python -m pytest -q` → **77 passed, 1 skipped** (the skip is the
pre-existing real-mkvmerge/ffmpeg-gated test, unrelated). The `fail_merge`
fixture + the baseline/quarantine merge stubs stay valid: they fully replace
`main.merge_video_files`, and the live `cmd_restore` still calls it with no seed
(unchanged this step).

---

## Step 2 — cmd_restore split-path verify-or-bless + restore-side disk check (deferred rehash core) — status: done

Mode: MULTI-CANDIDATE (2 candidates, both general-purpose @ opus in isolated worktrees, run in parallel). Winner: **B** (extracted pure helper). Decision: `.candidates/step-02/DECISION.md`. Merge commit `2b84a37` (squash of `__cand_b`). Candidates tagged `candidates/step-02/A-rejected` (`dd49616`) and `candidates/step-02/B-chosen` (`82b2be7`); worktrees removed.

Files changed (merged):
- `main.py` — new PURE helper `bless_or_verify_merged_hash(entry, new_hash) -> "bless"|"ok"|"mismatch"` (`main.py:286`, no mutation/journal/IO); `cmd_restore` SPLIT path: restore-side disk pre-check (`~1834-1854`), seed selection (`1864`), deterministic seeded merge (`1871`), verify-or-bless block (`1887-1918`).
- `tests/conftest.py`, `tests/test_baseline_happy_path.py`, `tests/test_cmd_restore_quarantine.py` — synced the 3 existing `merge_video_files` stubs to `(chunk_paths, output_path, seed=None)`. FORCED by Step 1's signature once Step 2 became the first seed-passing caller (else `TypeError`); NO assertion changes (both candidates' test diffs were byte-identical).

Key decisions:
- Judge chose B over A (both correct + change-gate-faithful): B isolates the bless/verify/alarm policy in a pure, trivially unit-testable helper (the seam Step 9 table-tests), funnels status+save once, and prints stored-vs-current `merge_tool` in the alarm. A's edges (slightly smaller diff + `generate_short_id(manual_id)` seed fallback) did not outweigh.
- Seed = `split_info.merge_seed or short_id or manual_id`. The `manual_id` fallback only triggers for fixtures lacking `short_id`; real entries always carry `short_id`, so it never affects production. FOLLOW-UP (judge): could switch the fallback to `generate_short_id(manual_id)` for exactness — cosmetic, NOT acted on.
- Restore-side disk pre-check estimates merged size from `sum(getsize(chunk))` (fallback `tech_spec.size_bytes`), treats it as a deferred split (1X + buffer), hard-stops BEFORE the merge with a free-vs-required message, chunks untouched.

Change-gate — VERIFIED in the merged code (not just the CRITIQUE):
- PONR unchanged: `journal.mark_point_of_no_return()` at `main.py:1930`, still AFTER merge + `save_library`, BEFORE the chunk-delete loop (`1932+`).
- Mismatch path returns at `main.py:1905` — BEFORE the PONR — reusing the existing pre-PONR reproducible-output `journal.rollback(library)`; chunks are NOT deleted (cleanup loop never reached).
- Journal format/durability + `RollbackJournal` calls unchanged; standard (non-split) restore path byte-identical.
- End-to-end: inherited unchanged by `cmd_fetch_restore` (`main.py:2224`) → `cmd_restore` / `cmd_restore_group` (`main.py:1993`).

Verification: orchestrator re-ran `.venv/Scripts/python.exe -m pytest -q` on the merged feature branch → **77 passed, 1 skipped**.
