# Task: IMP-G1 — adopt rclone chunker patterns (`.partial` upload + atomic remote rename + `.mvmeta.json` sidecar) for push reliability

Suggested branch: feature/push_partial_atomic_rename

## Context
MediaVault's `cmd_push` (`main.py:634-806`) `adb push`es each chunk directly to its final remote name under `/sdcard/Media/...`. A push that dies mid-chunk leaves a partially-transferred file at the final name, which the phone's Google Photos app can ingest as a "complete" chunk — the exact wrinkle that blocks a clean push rollback (FAILURE_ANALYSIS.md, Example A). This task adopts two rclone `chunker` patterns: (1) upload each chunk to `<final>.partial`, then `adb shell mv` it to the final name only after the transfer succeeds (atomic remote rename), so a partial transfer is never observable as complete; and (2) write a `<base>.mvmeta.json` sidecar alongside the chunks on the phone that mirrors `split_info`, so the library can be rebuilt from the remote if the local `library_*.json` is ever lost. This is a PREREQUISITE for the paused auto-rollback feature: it makes DECISIONS.md open item O-1 option 2 (full push rollback) safe, because rollback can then enumerate and `adb shell rm` only `.partial` remnants without Photos having grabbed a complete chunk.

## Goal
Concrete, testable definition of done:
- Every chunk upload goes to `<remote_full_path>.partial` via `adb push -p`, then is renamed to `<remote_full_path>` via `adb shell mv` only after the push returns success.
- After a fully successful push, the remote has exactly the same final chunk names as today, PLUS one `<base>.mvmeta.json` sidecar. Library state (`uploaded=True`, `status="onboarded"`, `split_info`) is byte-for-byte identical to today.
- A failed push returns the SAME signal callers rely on today: `return False` on ADB/connection errors, and the same exception-or-`False` behavior for the surrounding orchestrators. No new exception types escape `cmd_push`.
- On a mid-push failure, a `.partial` file may remain on the phone, but no NEW complete-named chunk is created by the failed transfer. The local `_parts/` resume artifacts behave exactly as today.
- The remote naming convention (`.partial` suffix) and the `.mvmeta.json` location/schema are exposed as discoverable module-level constants/helpers so auto-rollback can reuse them.
- New tests in `tests/` pass with `adb` (`subprocess.run`) fully monkeypatched — no real device, no real `C:\Media`.

## Files affected
- `main.py` — `cmd_push` upload loop (754-806): route uploads through `.partial` + `adb shell mv`; add the `.mvmeta.json` writer; add module-level constants for the `.partial` suffix and mvmeta filename. Touch ONLY the upload loop and the new helper(s); leave split/hash/resume/library-bookkeeping logic unchanged.
- `tests/test_cmd_push_partial.py` — new test file (mocked adb) covering happy path, mid-push failure, mvmeta sidecar, resume interaction, and the failure-contract signal. Reuses the `sandbox`/`sandbox_entry` fixtures from `tests/conftest.py`.
- `tests/conftest.py` — possibly add a small shared fixture for a fake split entry with `split_info` + populated `_parts/` and a fake-adb recorder (only if the new test file needs it beyond what exists; keep additive).
- `ARCHITECTURE.md` — update §7.5 (ADB push flow) and §10 Stage 2 to document the `.partial` + atomic-rename protocol and the `.mvmeta.json` sidecar. Add the new constants to §14.
- `README.md` — note the documented behavior change (remote sidecar + partial-upload safety) if the README describes the push protocol.
- `improvements_tierG.md` — flip IMP-G1 `Status: pending` → `done`.
- `docs/feature-auto-rollback/G1-push-partial-atomic-rename/G1-push-partial-atomic-rename.md` — fill the "Completion report" section at the very end (NOT now — only when implementation is complete).

## Approach
The change is confined to the upload loop (`main.py:754-806`) and one new sidecar-writer helper. Today the loop, per file, computes `remote_full_path`, runs `adb push -p <local> <remote_full_path>`, prints a checkmark, then deletes the local chunk if it lives under `_parts/`. The new loop changes only the transfer mechanics:

1. Compute `remote_full_path` exactly as today.
2. Compute `remote_partial_path = remote_full_path + PARTIAL_SUFFIX` (a new module constant, e.g. `".partial"`).
3. `adb push -p <local> <remote_partial_path>` (check=True).
4. `adb shell mv <remote_partial_path> <remote_full_path>` (check=True), with both paths single-quote-escaped the same way the existing `mkdir` path is escaped (`main.py:667`).
5. On success: print the checkmark and do the existing `_parts`-gated `os.remove(local)`. Key the local delete on the `mv` succeeding (the chunk is only "uploaded" once it sits at its final name), preserving the existing "upload success, not delete success" boundary semantics noted in FAILURE_ANALYSIS.md §4.
6. On any `subprocess.CalledProcessError`/`Exception` from push OR mv: set `all_success = False`, `break` — identical to today's failure handling, so the same `return False` flows out.

After the loop, if `all_success` and no `chunk_range` filter, BEFORE flipping `uploaded/status`, write the `<base>.mvmeta.json` sidecar to the remote dir (mirroring `split_info`) via a new helper `write_remote_mvmeta(adb_base, remote_target_dir, manual_id, entry, ...)`. The mvmeta write is best-effort-but-logged: a failure to write the sidecar should be reported but must NOT flip a fully-successful chunk upload into a `False` return (the chunks are the source of truth; the sidecar is disaster-recovery redundancy — same philosophy as the on-disk `.sha256` sidecars per ARCHITECTURE.md §6.5). This keeps the success-path result (library state) identical even if the phone rejects the metadata write.

The `.partial`-then-`mv` change is the only place the externally-observable remote intermediate state changes; the final state and the in-memory/on-disk library bookkeeping are untouched.

## Steps

- [x] 1. [model: haiku] Add module-level constants and update the task placeholder header is NOT needed — instead add discoverable constants for the new convention.
  - Files: `main.py`
  - Details: Near the existing config constants (around `SPLIT_DIR_NAME`/`CHECKSUM_DIR_NAME`, `main.py:~15-34`), add two named constants: `PARTIAL_SUFFIX = ".partial"` and `MVMETA_SUFFIX = ".mvmeta.json"` (or `REMOTE_MVMETA_NAME` if a fixed sidecar name is chosen per Decision 3). Add a one-line comment on `PARTIAL_SUFFIX` stating it is the auto-rollback seam: "remnant `<chunk>.partial` files are the only thing a push rollback must `adb shell rm`." Do NOT use these constants yet (that's step 3); this step only declares them so the seam is discoverable and greppable.
  - Acceptance: `python -c "import main; print(main.PARTIAL_SUFFIX, main.MVMETA_SUFFIX)"` prints the two values; no behavior change; `pytest -q` (existing C9/C11 tests) still green.

- [x] 2. [model: sonnet] Add the `write_remote_mvmeta` helper.
  - Files: `main.py`
  - Details: Add a standalone function `write_remote_mvmeta(adb_base, remote_target_dir, manual_id, entry)` placed near `cmd_push`. It builds the mvmeta dict from `entry` (fields per Decision 3 below; at minimum: `base_filename`, `short_id`, original `hash`, `split_info` mirror = method/val/total_chunks/chunks-with-hashes, and a schema `version`), serializes it to JSON, writes it to a local temp file (e.g. via `tempfile.mkstemp`), `adb push`es it to `<remote_target_dir>/<base>.mvmeta.json` (single-quote-escaped path, matching the existing escaping at `main.py:667`), then removes the local temp. It returns `True`/`False`. It must NOT raise on failure — catch `subprocess.CalledProcessError`/`Exception`, print a greppable WARNING (e.g. `⚠️ mvmeta sidecar write failed (chunks are safe): ...`), and return `False`. Do NOT call it from `cmd_push` yet (that's step 3).
  - Acceptance: Unit-callable with a monkeypatched `subprocess.run` recorder; asserts the pushed JSON parses and contains the agreed fields and that the local temp is cleaned up. A simulated adb failure returns `False` without raising.

- [x] 3. [model: opus] Rewire the `cmd_push` upload loop to `.partial` + atomic `adb shell mv`, and call the mvmeta writer on full success.
  - Files: `main.py`
  - Details: This is the protocol change; edit ONLY the upload loop (`main.py:754-806`) and the immediately-following success block. (a) Inside the per-file loop, push to `remote_full_path + PARTIAL_SUFFIX` instead of `remote_full_path`; on push success, run `adb_base + ["shell", "mv", "'<escaped_partial>'", "'<escaped_final>'"]` with `check=True`, reusing the single-quote escaping pattern from `main.py:667` for BOTH the partial and final paths. Treat a `mv` failure exactly like a push failure (`all_success=False; break`) so the existing `return False` contract is preserved. Keep the `_parts`-gated `os.remove(f)` after a successful `mv` (the chunk is "done" only once renamed to final). (b) Preserve every other line: the resume branch, split/hash, `chunk_range` filter, the empty-`_parts` rmdir, and the `uploaded=True`/`status="onboarded"`/`save_library` bookkeeping must be unchanged. (c) In the `all_success and not chunk_range` branch, call `write_remote_mvmeta(...)` BEFORE flipping state; ignore its return for the function's own `True`/`False` (a sidecar miss does not fail the push). Honor Decision 1 (resume vs leftover `.partial`) and Decision 2 (post-mv verification) once the user answers — implement the chosen behavior here. Do NOT change the function signature or any caller.
  - Acceptance: New tests in step 4 pass; a manual read confirms the diff touches only the loop + success block + the new `write_remote_mvmeta` call. Existing C9/C11 tests remain green. The function still `return False` on the first ADB/mv failure and still `return True` (with identical library mutations) on full success.

- [x] 4. [model: sonnet] Add `tests/test_cmd_push_partial.py` with a fully-mocked adb.
  - Files: `tests/test_cmd_push_partial.py`, `tests/conftest.py` (additive fixture only if needed)
  - Details: Build a fake-adb recorder that monkeypatches `main.subprocess.run` to record every argv, succeed by default, and be configurable to fail on the Nth `push` or `mv`. Seed a sandbox split entry (reuse `sandbox`/`sandbox_entry`; add a variant that writes `split_info` with 3 chunks and creates a local `_parts/` with 3 fake chunk files). Tests:
    (1) Happy path: assert each chunk is pushed to a `*.partial` remote, immediately followed by a `shell mv` of that `.partial` to the final name; assert a final `.mvmeta.json` push occurs; assert library ends `uploaded=True`/`status="onboarded"`; assert local `_parts` chunks were removed.
    (2) Mid-push failure: make the 2nd `push` fail; assert `cmd_push` returns `False`, no `mv` for the failed chunk ran, and the surviving local chunks remain for resume (matching today).
    (3) `mv` failure: make the 2nd `mv` fail; assert `cmd_push` returns `False` and treats it like a push failure (loop breaks, state not flipped).
    (4) mvmeta-write failure does NOT fail the push: make only the `.mvmeta.json` push fail; assert `cmd_push` still returns `True` and library still flips to onboarded.
    (5) Failure-contract parity: assert the return value and that no exception escapes — same signal callers see today.
    Never touch real `C:\Media` or real `library_*.json`; the conftest hard-guard already enforces this.
  - Acceptance: `pytest tests/test_cmd_push_partial.py -q` passes; full `pytest -q` stays green.

- [x] 5. [model: sonnet] Update `ARCHITECTURE.md` and `README.md` for the documented behavior change.
  - Files: `ARCHITECTURE.md`, `README.md`
  - Details: In `ARCHITECTURE.md` §7.5 (ADB push flow, step 6 "Upload loop"), document the `.partial` → `adb shell mv` atomic rename and the post-success `.mvmeta.json` sidecar write; update §10 Stage 2 step 7 similarly; add `PARTIAL_SUFFIX`, `MVMETA_SUFFIX`, and the `.mvmeta.json` remote sidecar to §14 (Configuration) and §6.5 (Sidecar files — note this one lives on the phone, unlike the local `.sha256` sidecars). In `README.md`, add a brief note if/where it describes the push protocol. Keep edits surgical — describe the new behavior, do not rewrite surrounding prose.
  - Acceptance: The two files mention `.partial`, `adb shell mv`, and `.mvmeta.json`; `git diff` shows only additive/clarifying edits in the relevant sections.

- [ ] 6. [model: haiku] Mark IMP-G1 done and fill the completion report.
  - Files: `improvements_tierG.md`, `docs/feature-auto-rollback/G1-push-partial-atomic-rename/G1-push-partial-atomic-rename.md`
  - Details: In `improvements_tierG.md`, change the IMP-G1 `Status: pending` line to `Status: done`. In the G1 task doc, fill the "Completion report (fill in when done)" section (branch, PR, files changed, tests added, manual test commands, open decisions resolved, notes). Also update the front-matter `status: not-started` → `done` and check the Definition-of-Done boxes. Do this LAST, after implementation and tests are green.
  - Acceptance: IMP-G1 reads `Status: done`; completion report has no empty placeholder fields.

## Open decisions — RESOLVED

1. **Resume interaction with a leftover `.partial` on the phone.** → **(a) Re-upload.** `adb push` to the `.partial` name overwrites the stale partial, then `mv` clobbers/creates the final. No remote `ls` needed; resume stays driven entirely by local `_parts/` listing. Naturally self-correcting.

2. **Post-`mv` verification.** → **(a) No verification.** Trust `adb push` exit code. Remote-hash verification is IMP-C8's job; G1 is scoped to the rename only.

3. **`.mvmeta.json` schema.** Full schema (all fields included):
   - `version` (schema int, start at 1)
   - `manual_id`, `short_id`, `base_filename`
   - `original_hash`
   - `is_split`, `method`, `val`, `total_chunks`
   - `chunks`: list of `{filename, hash}` mirroring `split_info.chunks`
   - `folder_path` / `remote_target_dir` (included — helps disaster recovery)
   - `tech_spec` and `metadata` (year, title — included for fuller library reconstruction)
   - Sidecar name: **UID-tagged** → `<base> [<short_id>].mvmeta.json` (collision-proof, matches chunk naming convention)
   - Write mvmeta for **non-split single-file uploads too** — `chunks` is a 1-element list referencing the renamed `<name> [<short_id>]<ext>` remote name.

4. **Back-compat with chunks already pushed under the old naming.** → **Agreed.** No retroactive migration. New pushes and resumes use the new protocol; existing 412 archived remotes stay as-is and are restorable exactly as today. Resume of a partially-old push is driven entirely by local `_parts/` — no remote inspection, no special-casing for chunks already at final names.

## Risks and edge cases
- **`adb shell mv` across the same remote dir is atomic on the phone's filesystem** (same-directory rename on the sdcard FUSE/sdcardfs layer). If the phone's storage layer makes `mv` non-atomic or slow for large files, the "never partially visible" guarantee weakens; the rename is metadata-only on the same volume so this should hold, but call it out for manual verification on the real Pixel.
- **Path escaping for `mv`:** both the `.partial` and final paths must be single-quote-escaped exactly like the existing `mkdir` path (`main.py:667`), including titles with apostrophes (`Sorcerer's Apprentice`). A missed escape silently mv-fails → push reported failed.
- **Google Photos timing:** Photos may pick up the `.partial` file before the `mv`. Photos backs up media extensions; a `.partial` extension is not a recognized media type, so it should be ignored by the Photos backup until renamed. This assumption should be confirmed on-device (it is the crux of the whole pattern). If Photos DOES ingest `.partial`, the pattern needs a non-media holding extension — flag for manual confirmation.
- **mvmeta write failing the push:** must NOT happen — the helper swallows errors and returns False; step 3 must ignore that return for its own success signal. A regression here would change the success contract.
- **Resume + stale `.partial`:** covered by Decision 1; the recommended overwrite-on-re-push avoids orphan accumulation, but a chunk whose local copy was deleted yet whose remote `mv` never ran would be skipped by local-`_parts`-driven resume — this is the same gap as today and out of scope to fix here.
- **`chunk_range` partial pushes** must still NOT write mvmeta / flip state (mvmeta write is gated on `all_success and not chunk_range`), matching today's partial-push semantics.
- **Windows local temp for mvmeta:** use `tempfile.mkstemp` and clean it up; on failure to delete, log but don't fail (matches existing best-effort cleanup style).

## Verification
After all steps:
- `pytest -q` (from repo root) — all tests green, including existing C9/C11 and the new push tests.
- `pytest tests/test_cmd_push_partial.py -v` — inspect that each chunk shows push-to-`.partial` then `shell mv`, and the mvmeta push fires once on success.
- `python -c "import main; print(main.PARTIAL_SUFFIX, main.MVMETA_SUFFIX)"` — constants exist.
- `git diff --stat` — confirms only `main.py`, the new test file (+ maybe `conftest.py`), `ARCHITECTURE.md`, `README.md`, `improvements_tierG.md`, and the G1 task doc changed; nothing under `archive/`.
- Manual on-device test (real Pixel, NON-destructive — use a throwaway test entry / copy, never a real archived title):
  - `python main.py push <test_id> SIZE_MB 50` against a small multi-chunk test file; while running, on the phone confirm chunks appear first as `*.partial` then get renamed; after completion confirm final names + one `*.mvmeta.json` in `/sdcard/Media/...`.
  - Interrupt mid-push (unplug/`Ctrl-C`); confirm a `.partial` remnant and that re-running `python main.py push <test_id>` resumes and completes; confirm Google Photos did not back up the `.partial`.
  - Confirm `adb shell cat '/sdcard/Media/.../<base> [uid].mvmeta.json'` returns the expected JSON.

## Out of scope
- Post-push remote hash/size verification (that is IMP-C8; this task deliberately stops at the rename per RELATED_IMPROVEMENTS sequencing — unless the user pulls a size check in via Decision 2b).
- Retry/backoff on transient ADB failures (IMP-C2).
- Building the rebuild-from-remote tool that consumes `.mvmeta.json` (this task only WRITES the sidecar; a `repair_library`/rebuild command is separate deferred work).
- Migrating already-pushed remote chunks to the new naming or back-filling `.mvmeta.json` for the 412 existing archived entries.
- The auto-rollback feature itself (G1 only leaves the seam; rollback is the paused follow-up).
- Chunk-filename-length / 260-char path audit mentioned in the IMP-G1 spec's third bullet — separate hardening, not part of the `.partial`/mvmeta change.
- Any change to `mainfetch.py`, the restore path, or `archive/`.
