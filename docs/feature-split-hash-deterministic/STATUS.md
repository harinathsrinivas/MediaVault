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

---

## Step 3 — EAGER bless-at-push + promote-at-replace + re_hashed-reset on re-split — status: done

Mode: MULTI-CANDIDATE (2 candidates, both general-purpose @ opus, parallel isolated worktrees). Winner: **A** (transient `split_info["canonical_hash"]`, no extra schema field). Decision: `.candidates/step-03/DECISION.md`. Merge commit `1dc1fbe` (squash of `__step3_a`). Candidate tags FEATURE-SCOPED to avoid a cross-feature collision (the generic `candidates/step-03/*` already belong to the C8 feature): `candidates/split_hash/step-03/A-chosen` (`91b0532`), `candidates/split_hash/step-03/B-rejected` (`b4c97b9`); worktrees removed.

Files changed (merged): `main.py` only (+73/-7).

What changed:
- `eager_rehash=False` kwarg threaded through `cmd_push` (`main.py:1171`) + `cmd_push_group` (`1514`→pass `1559`), `cmd_prep_push_rep` (`2316`→`2334`), `cmd_prep_push_rep_season` (`2354`→`2440`). Dormant/False until the Step-6 CLI `rehash` token.
- RE-SPLIT RESET (`main.py:1298`): in the NEW-SPLIT branch ONLY, set `re_hashed=False`; the fresh `split_info` dict naturally drops stale `merge_seed/merge_tool/rehashed_at/canonical_hash`. Resume branch untouched → never resets. Closes the re-push false-alarm hole the user flagged.
- EAGER bless-at-push (`main.py:1311-1333`, gated on `eager_rehash` + a fresh split): seed = `short_id or manual_id`; merges the UNFILTERED chunk list (`files_to_upload_paths`, before the chunk_range filter at ~`1342`) into `<local_folder>/<base>.rehash_tmp.mkv`; hashes → `canonical`; stores `split_info.{merge_seed,merge_tool,canonical_hash}` (NOT `entry["hash"]`, NOT `re_hashed`); `finally` ALWAYS deletes the temp; any merge/hash failure warns + continues as DEFERRED (never aborts the push — Step 4's proactive disk pre-flight doesn't exist yet, so this graceful fallback is the interim safety).
- PROMOTE-AT-REPLACE (`main.py:1655-1670`): after the replace PONR, before `status="archived"` + save — if `split_info.canonical_hash` present AND `re_hashed` not already True → `entry["hash"]=canonical`, `re_hashed=True`, stamp `rehashed_at`, drop `canonical_hash`. No-op for non-eager/non-split.

Key decision: Judge chose A over B — both correct + change-gate-clean; A's transient `canonical_hash` is a single self-clearing promote signal, avoiding B's extra top-level `pending_promote` field (which had to stay coupled with the canonical across 3 sites). Schema stays minimal.

Change-gate — VERIFIED in merged code: `cmd_push` stays PONR-less (O-1); replace PONR (`os.rename`) unmoved (promote runs strictly after it); NO new `RollbackJournal` record kinds; `record_set_field` scope unchanged; the reset writes the SAFE unblessed state (no journalling needed — a push rollback leaving `re_hashed=False` is correct). Eager writes confined to `split_info`. `entry["hash"]` is never set to the canonical at push time (only at replace) → `cmd_check` stays correct across the push→replace window.

Mechanism notes (playbook naming gaps worked around): step-3 candidate BRANCHES are step-qualified (`__step3_a/b`) to avoid colliding with step-2's `__cand_a/b`; candidate TAGS are feature-scoped because the generic `candidates/step-03/*` were already taken by C8.

Verification: orchestrator re-ran `.venv/Scripts/python.exe -m pytest -q` on the merged branch → **77 passed, 1 skipped**.

---

## Step 4 — Hard disk pre-flight (cmd_push single-item + season + group) — status: done

Executor: executor-opus (single-executor mode). Model: opus 4.8, effort high. `main.py` only, additive (+119 / -0). NO rollback/PONR/journal/split/merge code touched.

What changed (all in `main.py`):
- **`_will_split(file_size, split_method, split_val)`** at `main.py:311` (immediately before `_required_extra_bytes`, with the Step-1 disk helpers). Pure, never raises. Mirrors cmd_push's `should_split`: `SIZE_MB` → `file_size >= val*1024**2`; `SIZE_GB` → `file_size >= val*1024**3` (file AT/OVER the target splits, under it does not); `COUNT` → `True`; no/empty `split_method` → `False`.
- **cmd_push single-item check** inserted inside `if should_split:` at `main.py:1267` — BEFORE the journal `record_create_dir` calls + `os.makedirs` (which now start at ~`1290`). Computes `file_size = os.path.getsize(local_file_path)`; if `not _free_space_ok(local_folder, file_size, True, eager_rehash)` → prints a hard-stop built from `_disk_shortfall(...)` (`human_readable_size(required)` vs `(free)`) PLUS remedies (free up space / pass a temp dir on another volume / if eager, drop `rehash` to halve 2X→1X) and `return False`. Clean early return — nothing created yet (mirrors the existing `chunk_range` "no chunks" return). Targets `local_folder` (temp_dir redirection is Step 5; no temp_dir param added). The RESUME branch (`if os.path.exists(parts_dir) and os.listdir(parts_dir)`) never reaches this block, so an existing `_parts/` correctly SKIPS the check.
- **Group pre-flight** in `cmd_push_group` at `main.py:1593` — AFTER `target_ids` finalized (range filter + empty check) and BEFORE the `for mid in target_ids:` loop. MAX logic: skip already-`uploaded`; for each remaining item resolve `folder_path/filename`, if it exists `fsize=getsize`, `ws=_will_split(...)`, `req=_required_extra_bytes(fsize, ws, eager_rehash)`, track MAX req + its mid/size/folder. If `max_req > 0`: `buffer=_disk_buffer(max_req)`, `free=shutil.disk_usage(worst_dir).free` (worst item's own folder volume; `-1` on stat failure), and if `free < max_req + buffer` → hard-stop naming the largest splitting item + size + free-vs-needed + same remedies, `return`. NOT added to `cmd_replace_group`/`cmd_restore_group` (they don't split).
- **Season pre-flight** in `cmd_prep_push_rep_season` at `main.py:2492` — AFTER `target_ids` finalized (range filter) + the `_season_resume_cmd` helper def, BEFORE the `for idx, mid in enumerate(target_ids):` loop. Identical MAX logic; target volume = the season `folder_path` parameter (`shutil.disk_usage(folder_path).free`). Hard-stop names the largest splitting episode + size + free-vs-needed + remedies, `return` before processing ANY episode. Already-uploaded episodes skipped (won't push/split); if NO episode will split → `max_req=0` → proceeds (0 extra).

Season/group MAX shape (both identical):
```python
max_req = 0; worst_mid = None; worst_size = 0   # (+worst_dir in the group)
for mid in target_ids:
    if library[mid].get("uploaded") == True: continue
    f = os.path.join(library[mid]["folder_path"], library[mid]["filename"])
    if not os.path.exists(f): continue
    fsize = os.path.getsize(f)
    ws = _will_split(fsize, split_method, split_val)
    req = _required_extra_bytes(fsize, ws, eager_rehash)
    if req > max_req: max_req = req; worst_mid = mid; worst_size = fsize
if max_req > 0:
    buffer = _disk_buffer(max_req)
    free = shutil.disk_usage(<target_dir>).free  # season: folder_path; group: worst_dir
    if free < max_req + buffer: <named hard-stop + remedies>; return
```

CHANGE-GATE — confirmed NOTHING rollback-related was touched. This is a purely READ-ONLY pre-check (`shutil.disk_usage` / `_free_space_ok` / `_disk_shortfall`) that runs BEFORE any artifact creation (before `os.makedirs`, before the journal `record_create_dir` records). A hard-stop creates nothing and has nothing to roll back → ZERO rollback interaction. The `RollbackJournal`, PONR markers, `mark_point_of_no_return`, `record_*`/`rollback` calls, the journal format/durability, created-this-run scoping, and the split/merge logic are all UNTOUCHED (verified: the diff adds no line referencing any of those — the single grep hit is an explanatory comment). cmd_push stays PONR-less (O-1).

Verification: throwaway smoke test (`tests/test_step4_smoke.py`, since DELETED — no stray files) asserted (i) `cmd_push` returns False + creates NO `_parts/`/`checksums/` when `_free_space_ok` is forced False; (ii) the season pre-flight picks the LARGEST splitting episode (200 MB vs 5 MB) and hard-stops before any `cmd_push` (the loop's `cmd_push` was rigged to throw if reached) → **2 passed**. Then full suite `.venv/Scripts/python.exe -m pytest -q` → **77 passed, 1 skipped** (baseline held; genuine-split tests pass the new gate on this ~45 GB-free machine, no assertions weakened).

---

## Step 5 — Optional `tempdir <path>` redirect for chunks + eager merge temp — status: done (CLEAN REDO)

**This step was REDONE.** Attempt 1 was disrupted by a session limit: candidate B was left a non-functional STUB and candidate A was completed+patched — never a fair comparison. So the attempt-1 Step-5 merge was **reset off the feature branch** (`git reset --hard 82b5c28`, safe — branch unpushed, attempt-1 work preserved at tag `candidates/split_hash/step-05/A-chosen`) and BOTH candidates were re-implemented FULLY from the Step-4 base, then judged FRESH on two complete implementations.

Winner: **A** (single `base_dir` variable). Decision: `.candidates/step-05-redo/DECISION.md`. Merge commit `32eb442` (squash of `__step5r_a` @ `814b29f`, +70/-19). Redo tags: `candidates/split_hash/step-05-redo/A-chosen` (`814b29f`), `B-rejected` (`788c48a`). (Attempt-1 candidates also preserved: `candidates/split_hash/step-05/A-chosen` `4775023`, `B-rejected` `f3a84a9`.)

Files changed (merged): `main.py` only (+70/-19).

What changed:
- `temp_dir=None` threaded through ALL FOUR functions (`cmd_push` `main.py:1188`, `cmd_push_group` `1583`, `cmd_prep_push_rep` `2436`, `cmd_prep_push_rep_season` `2474`) AND every per-item `cmd_push` call (group loop, season loop, prep_push_rep).
- `cmd_push` derives `base_dir, _tmperr = _parts_base(local_folder, temp_dir, manual_id)` (`1207`; hard-stop on `_tmperr`); `parts_dir` + eager `rehash_tmp` (`1370`) under `base_dir`; `checksum_dir` + `RollbackJournal` STAY on `local_folder`.
- Disk-check target correct from the start: `check_dir = temp_dir if temp_dir else local_folder` (`1293`) — stats an EXISTING dir (the per-entry `base_dir = temp_dir/<safe-id>` doesn't exist at check time → would `FileNotFoundError` → false hard-stop). Group (`1659`) + season (`2567`) pre-flights target `temp_dir` when set, validated via a `_parts_base` probe (which ALSO enforces W_OK, hard-stopping a read-only temp_dir at the batch gate — the judge noted this as A's edge over B).
- Cleanup (`1540`): removes the temp `_parts/` + the empty `temp_dir/<safe-id>/` parent on success ONLY when created-this-run (`temp_dir and not parts_preexisted and base_dir != local_folder`).

Net behavior: a `temp_dir` (kwarg only; the CLI `tempdir` token is Step 6) sends `_parts/` chunks + the eager merge temp to `temp_dir/<safe-id>/`; `checksums/` + the journal stay on the media volume; a bad temp_dir hard-stops before any work; resume re-passes `tempdir`; `temp_dir=None` is byte-identical.

Change-gate — VERIFIED (both candidates + judge): journal FORMAT/durability UNCHANGED (only the recorded `parts_dir` PATH string may live under temp_dir); created-this-run scoping preserved; `cmd_push` PONR-less; `checksums/` + journal on `local_folder`; no rollback-API calls changed.

Judge (FAIR comparison of two COMPLETE impls; explicitly NOT anchored on the void attempt 1): **Winner A** — both correct + complete + change-gate-clean; A more surgical (+70/-19 vs B's +130/-28 `TempLayout` "abstraction ahead of need for a single consumer") and a strictly stronger batch W_OK pre-flight. Legitimately confirms the same pick as the disrupted run.

Verification: each candidate's 3-scenario temp_dir smoke passed (first-time split routes to temp + checksums/journal stay + cleanup; bad temp_dir hard-stop; resume). Full suite → **77 passed, 1 skipped** (re-run by orchestrator on the merged branch `32eb442`).

---

## Step 6 — CLI: thread the `rehash` + `tempdir <path>` tokens through the dispatch + usage — status: done

Executor: executor-opus (single-executor mode). Model: opus 4.8, effort high. `main.py` only — the four argv command handlers in the `if __name__ == "__main__"` dispatch + the four usage lines. NO `cmd_*` bodies, NO rollback code, nothing else touched. (The four `cmd_*` signatures already accepted `eager_rehash=False`/`temp_dir=None` from Steps 3 & 5 — this step only wires the CLI tokens that were dormant.)

Two tokens added to each of the four handlers:
- **`rehash`** — bareword flag (no value) → `eager=True` → passed as `eager_rehash=eager`.
- **`tempdir <path>`** — consumes the NEXT argv element as the path → `tdir=<next>` → passed as `temp_dir=tdir`. A quoted path-with-spaces is a single argv element, so it is captured whole.

EXACT branches added per handler (each inits `eager = False`, `tdir = None` alongside the existing locals):

1. **`push`** (has `else: i += 1`; existing branches use `if i+1<len … else: sys.exit(1)`). Added BEFORE `else: i += 1`:
   ```python
   elif args[i] == "rehash":
       eager = True
       i += 1
   elif args[i] == "tempdir":
       if i + 1 < len(args):
           tdir = args[i + 1]
           i += 2
       else:
           print("❌ Error: Missing value for tempdir.")
           sys.exit(1)
   ```
2. **`push_group`** (same `else: i += 1` structure). Added the IDENTICAL two branches (bareword `rehash`; `tempdir` with the missing-value `sys.exit(1)` per the task instruction — note `push_group`'s own pre-existing SIZE_*/episodes/device branches lack an `else` and silently fall through, but the task explicitly specified the error+exit form for `tempdir`, which I followed).
3. **`prep_push_rep`** (TRAP: unmatched tokens `filepath_parts.append(arg)`; matched branches use `continue`). Added BEFORE the append fallback, mirroring the `device` branch's `continue` style (no `else` — a `tempdir` with no following value falls through to the append, consistent with how `device`/`SIZE_*` behave locally):
   ```python
   elif arg == "rehash":
       eager = True
       i += 1
       continue
   elif arg == "tempdir":
       if i + 1 < len(rest):
           tdir = rest[i + 1]
           i += 2
           continue
   ```
4. **`prep_push_rep_season`** (same TRAP: `folder_parts.append(arg)`). Added the IDENTICAL two `continue`-style branches before the append fallback (reading `args` instead of `rest`).

Four call-site changes (appended `eager_rehash=eager, temp_dir=tdir` as the last kwargs):
- `cmd_push(mid, method, val, c_range, device_id=resolve_device(dev), eager_rehash=eager, temp_dir=tdir)`
- `cmd_push_group(group_id, method, val, ep_range, device_id=resolve_device(dev), eager_rehash=eager, temp_dir=tdir)`
- `cmd_prep_push_rep(mid, filepath, method, val, device_id=resolve_device(device_arg), eager_rehash=eager, temp_dir=tdir)`
- `cmd_prep_push_rep_season(group_id, folder_path, method, val, ep_range, device_id=resolve_device(device_arg), eager_rehash=eager, temp_dir=tdir)`

Four usage-line changes (appended ` [rehash] [tempdir <path>]`):
- `push [id] [SIZE_GB/SIZE_MB] [val] [chunks 1-4] [device <id_or_name>] [rehash] [tempdir <path>]`
- `push_group [id] [SIZE_GB/SIZE_MB] [val] [episodes 1-3] [device <id_or_name>] [rehash] [tempdir <path>]`
- `prep_push_rep [id] [filepath] [optional: SIZE_GB/COUNT val] [device <id_or_name>] [rehash] [tempdir <path>]`
- `prep_push_rep_season [id] [folder] [optional: SIZE..] [OPT: episodes] [device <id_or_name>] [rehash] [tempdir <path>]`

CHANGE-GATE: nothing rollback-related touched. This is argv parsing in `__main__` only; the `cmd_*` bodies (incl. all journal/PONR/`_parts`/merge logic from Steps 1–5) are byte-for-byte unchanged. The tokens just flip the already-existing `eager_rehash`/`temp_dir` kwargs from their dormant defaults.

Verification:
- `.venv/Scripts/python.exe -m pytest -q` → **77 passed, 1 skipped** (CLI parsing isn't covered by existing tests; confirms no syntax break).
- `python main.py` (no args) → usage block shows `[rehash] [tempdir <path>]` on all four lines (push / push_group / prep_push_rep / prep_push_rep_season). (Exits 1 — the pre-existing usage `sys.exit(1)`, expected.)
- PARSE-SMOKE (subprocess; ids don't exist so commands bail after parsing):
  - `python main.py push __nonexistent_id__ SIZE_MB 2000 rehash tempdir C:\Temp device movies`:
    ```
    --- PUSHING: __nonexistent_id__ ---
    ❌ ID not found.
    ```
    → SIZE_MB/device still parse, rehash/tempdir neither crashed nor got mis-consumed; no traceback, no "Missing value".
  - `python main.py prep_push_rep __nonexistent_id__ "C:\some\path\file.mkv" SIZE_MB 2000 rehash tempdir C:\Temp device movies`:
    ```
    === 🚀 AUTO-PILOT: PREP -> PUSH -> REPLACE for __nonexistent_id__ ===

    >>> STEP 1: PREP
    ❌ File not found: C:\some\path\file.mkv
    ❌ Auto-Pilot Aborted: Prep failed.
    ```
    → **TRAP AVOIDED**: the reported filepath is exactly `C:\some\path\file.mkv`, NOT polluted with `rehash`/`tempdir`/`C:\Temp`. (Then aborts at prep on file-not-found, as expected.)
