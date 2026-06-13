# Execution Log

Task: Fix the multi_ep_alias production crash (IMP-C12/C13) AND harden the agent workflow + testing strategy so a feature change can never silently break another command

Branch: fix/alias_crash_and_smoke_gate (from main)
PLAN: root /PLAN.md (live, gitignored); canonical copy to be restored under docs/feature-alias-crash-and-smoke-gate/ at finalize.
Baseline (pre-change, unmodified tree): `pytest -q` -> 99 passed, 2 skipped (the 2 skips are ffmpeg/mkvmerge-gated). This is the regression oracle.

Run model: USER-GATED, ONE STEP AT A TIME. The user reviews after each step and says "continue". This log + PLAN.md checkboxes + per-step git commits are the resume record — a fresh session can resume by reading this file, `git log --oneline`, and the PLAN.md `[x]` marks.

Planned step order: A1 -> A2 -> A3 -> B1 -> B2 -> B3 -> B4 -> B5 -> B6 -> B7 -> (bookkeeping: tierC/tierH/PRIORITY/priority-graph) -> (architect: ARCHITECTURE.md/README) -> Phase 3 finalize (restore docs/<feature>/PLAN.md, push, open PR). Merge to main is HUMAN-GATED.

Note on the in-scope pre-existing change: `.claude/agents/planner.md` was already modified (frontmatter) before this run; it is carried onto the feature branch and will be committed as part of step B3 (which edits planner.md), NOT with earlier steps.

---

## RUN BLOCKER — paused before Step A1 (RESUME POINT)

Date: 2026-06-13. State on disk is safe; nothing committed yet on this branch beyond the carried working-tree changes.

**Blocker:** the named executor sub-agents `executor-opus` / `executor-sonnet` / `executor-haiku` are NOT dispatchable in this session. `Task(subagent_type="executor-opus")` returns: `Agent type 'executor-opus' not found. Available agents: architect, claude, claude-code-guide, Explore, general-purpose, git-agent, judge, orchestrator, Plan, planner, statusline-setup`. So only 5 of the 8 project agents in `.claude/agents/` register (architect, git-agent, judge, orchestrator, planner); the 3 executors do not.

**Diagnosis (done this session):** no global `~/.claude/agents/` dir; no agent allowlist in `.claude/settings*.json`; executor frontmatter is structurally identical to the registered architect/judge (same `name/model/effort/tools` fields, same CRLF). No file-level defect found. Cause is environmental (session-fixed agent registry — likely a harness quirk or a custom-agent count cap). This is the same class of failure noted on prior runs (A1/C2/C8/auto-rollback: "Task subagent tool UNAVAILABLE → orchestrator executed inline").

**User decision (2026-06-13):** FIX AGENT REGISTRATION FIRST (do not fall back to inline or generic-agent execution).

**RESUME PROCEDURE after a Claude Code restart:**
1. Re-set session: `/model` → Opus 4.8, `/effort` → xhigh (saved as defaults, but confirm).
2. Verify the fix: attempt a trivial `Task(subagent_type="executor-opus")` or check the available-agents list — confirm all of executor-opus/sonnet/haiku now appear.
3. If registered → resume execution at **Step A1** (the branch `fix/alias_crash_and_smoke_gate` is already created and checked out; baseline is 99 passed/2 skipped). Follow `.claude/agents/orchestrator.md` as the playbook from Phase 2, step A1. Dispatch A1 → executor-opus; commit via git-agent; report; await user "continue".
4. If STILL not registered → it's a harness cap/limit, not transient. Surface to user; options then are (a) consolidate the 3 executors into fewer agent files, (b) generic `claude` agent + model override, or (c) explicit inline execution.

**Steps completed this run:** none of A1–B7 (the code steps). Setup + registration-fix done: branch `fix/alias_crash_and_smoke_gate` created (27cd911 → 4ce9e4a), baseline captured (99 passed/2 skipped), STATUS.md reset, **2 stale locked worktrees pruned (commit `4ce9e4a`)**. ⏸️ **NEXT ACTION = USER RESTARTS Claude Code** to re-register the executor agents, then `/agents` to verify, then resume at Step A1.

**Diagnosis update (clutter found):** `.claude/` contains duplicate agent definitions:
- backup dirs `.claude/agents_copy2/`, `.claude/agents_old/`, `.claude/agents_pre_opus48/` (all git-TRACKED) — each a full copy of the 8 agents with DIFFERING content (older pre-Opus-4.8 versions; e.g. their `executor-opus.md` lacks the `effort: max` line).
- two STALE LOCKED git worktrees `.claude/worktrees/agent-a7378bcf38009429a` (@00e4216) and `.claude/worktrees/agent-a79b36f292a96e1d9` (@d246430), leftover from May-26 candidate runs — each carries its own `.claude/agents/` (+ agents_copy2/old) copy.
Claude Code docs: agents load at session start (no hot-reload); duplicate `name:` across the scanned tree is "kept once, others discarded without warning"; `/agents` shows the loaded registry. NOTE: `architect.md` also differs across the backup copies yet still registers — so the backup dirs are likely NOT scanned; the 2 git worktrees (real `.claude/agents/` dirs) are the leading scannable-duplicate suspect. Mechanism not 100% proven from inside the session.

**FIX SEQUENCE (requires a Claude Code restart — only the user can do it):**
1. (optional, zero-risk) run `/agents` in the interactive session to view the loaded registry / any conflict on the executors.
2. ✅ DONE (2026-06-13, commit `4ce9e4a`): pruned the 2 stale locked worktrees via `git worktree remove --force` + `git worktree prune`. Backup dirs `agents_copy2`/`agents_old`/`agents_pre_opus48` were INTENTIONALLY KEPT (user decision; not named `agents` so almost certainly not scanned — `architect.md` is duplicated there too yet registers fine).
3. FULLY quit + restart Claude Code; run `/agents` (or attempt `Task(subagent_type="executor-opus")`) to confirm all of executor-opus/sonnet/haiku now register.
4. Re-set `/model` Opus 4.8 + `/effort` xhigh, then resume execution at Step A1 per orchestrator.md.
5. If executors STILL missing after a clean restart with no duplicates → it's a harness bug; report via `/feedback`; fall back (with user consent) to generic `claude` agent + model override or explicit inline execution.

---

## Step A1 — [status: done]
- Executor: executor-opus
- Model: opus
- Mode: single-executor
- Files changed: `main.py` (only)
- Outcome: Fixed the two whole-library iterators (IMP-C12) and de-aliased the five direct single-id commands plus added the `cmd_prep` refuse-over-alias guard (IMP-C13), in one coherent edit. `cmd_local_status` and `cmd_scan_unprepped` now skip `multi_ep_alias` entries alongside `season_map`, so neither dereferences the alias's absent `folder_path`/`filename` (the reported `KeyError`) nor counts it as a phantom pending row / slices `None[:40]`. `cmd_check`, `cmd_push`, `cmd_replace`, `cmd_restore`, `cmd_verify_restore` now resolve a secondary-episode id to the primary at the lookup head via `_resolve_alias`, print exactly one info line, and operate on the primary thereafter. `cmd_prep` refuses to prep over an existing alias id before writing anything. `cmd_fetch_restore` (#8) needed no change — it delegates to `cmd_restore`, which now resolves. Verified by careful code reading, a throwaway sanity snippet (since deleted), and the full suite staying at baseline.
- Key decisions:
  - **Iterator-skip form used (both sites):** `if entry.get("type") in ("season_map", "multi_ep_alias"): continue` — the explicit tuple form, matching the existing `season_map` skip style (chose this over the belt-and-suspenders `or "folder_path" not in entry`). Applied at `cmd_local_status` (loop head, now `main.py:2369`) and `cmd_scan_unprepped` (loop head, now `main.py:2475`).
  - **De-alias + rekey strategy:** at each command's lookup head I call `real_id, entry = _resolve_alias(library, manual_id)`, and when `real_id != manual_id` I print the info line then REBIND `manual_id = real_id`. Rebinding the variable (not just `entry`) is what makes every downstream `library[manual_id]` assignment, the `RollbackJournal(local_folder, manual_id)` construction, and the resume-message `push {manual_id}` all land consistently on the PRIMARY id — the rekeying concern in PLAN Risks. The info line prints the ORIGINAL typed id and the resolved id (captured before the rebind), so the user sees both. For a non-alias id `real_id == manual_id`, so `manual_id` is unchanged and the whole path is byte-identical (the info `print` is guarded by `real_id != manual_id`).
  - **`cmd_prep` guard placement:** added as the FIRST check inside the existing `if manual_id in library:` block (`main.py:808-812`), before the uploaded/archived early-skip. It `return False`s before `folder_path`/`short_id`/journal are computed (those are all after `main.py:824`), so zero artifacts are created and the alias entry is left untouched. Message: `❌ {manual_id} is a combined-episode alias of {alias_of}; prep the primary instead.` (uses `entry.get('alias_of')`).
- Verification:
  - `python -c "ast.parse(main.py)"` → parses OK.
  - Throwaway sanity snippet (created, run, then deleted — no test file added): confirmed `_resolve_alias` one-hops the secondary id to the primary and leaves non-alias ids unchanged; confirmed both iterator-skip loop bodies no longer KeyError on `folder_path`/`filename` and no longer slice a `None` filename. Output: `ALIAS SANITY OK`.
  - `python -m pytest -q` → **99 passed, 2 skipped in 15.15s** — identical to the recorded baseline (the 2 skips are the ffmpeg/mkvmerge-gated cases). No regressions.
  - `git status --short` → only `main.py` modified by this step (the pre-existing `M STATUS.md` was carried in before the run; this append updates it).
  - **Rollback change-gate: RESPECTED — nothing touched.** All de-alias edits sit at the command lookup heads, strictly BEFORE any journal is opened (push journal `main.py:1293`, replace `1775`, restore `2143`) and before any `mark_point_of_no_return()` (replace `1820`, restore `2205`). The edits are read-only alias resolution. No PONR moved, no `mark_point_of_no_return()` placement changed, no `.mediavault_txn.json` journal format/durability changed, no change to what `RollbackJournal` records, and `recover_journal()` is untouched. When an alias id is passed, the journal is now constructed with the resolved PRIMARY id — which is the only coherent behavior (the alias has no physical artifacts/PONR of its own); for non-alias ids the journal id is unchanged.

---

## Step A2 — [status: done]
- Executor: executor-opus
- Model: opus
- Mode: single-executor
- Files changed: `tests/conftest.py` (only) — added the `sandbox_alias` fixture (inserted between `sandbox_entry` and `mock_device`). No change to `main.py`. (A throwaway `tests/test_sandbox_alias_selfcheck_tmp.py` was created to self-verify the fixture, run green, then DELETED — A3 owns the real tests.)
- Outcome: Added `sandbox_alias`, a Series sandbox library seeded with a full combined-episode (multi_ep_alias) chain that A3 + the B1 smoke suite will reuse. The fixture is built ON TOP OF the existing `sandbox` fixture (it `depends on` sandbox + the same `tmp_path`), so it inherits sandbox's dual `LIBRARY_*` patch (both `mvcommon.LIBRARY_*` AND `main.LIBRARY_*`) and the C:\\Media hard-guard — NO DIY LIBRARY_* patching was added. It seeds three `tv-…` entries via the real `mvcommon.save_library` helper (which routes all three into `library_series.json`, leaving movies/anime `{}`): a `season_map` parent, a leaf primary pointing at a REAL on-disk `.mkv` (> DUMMY_MAX_BYTES), and the `multi_ep_alias`. Verified end-to-end: under the fixture `mvcommon.load_library()` returns exactly the three entries with the alias intact, `_resolve_alias` one-hops the alias to the primary, and `cmd_check(alias_id)` resolves to the primary and the real sha256 verifies (proving the >200_000-byte file + matching hash clear cmd_check's dummy-detection early-skip). Full suite stays at baseline.
- Key decisions:
  - **Reused `sandbox`'s dual-patch — confirmed no DIY redirection.** `sandbox_alias(sandbox, tmp_path)` declares `sandbox` as a dependency; pytest builds `sandbox` first (which patches BOTH `mvcommon.LIBRARY_*` and `main.LIBRARY_*` and asserts no `C:\\Media`), so by the time the body runs, `mvcommon.save_library`/`load_library` already point at the sandbox JSON files. The fixture contains zero `monkeypatch.setattr(..., "LIBRARY_*", ...)` calls. Requesting `tmp_path` alongside `sandbox` yields the SAME temp dir (pytest caches `tmp_path` per test), so the season media dir lives under the same sandbox tree. Documented in the fixture docstring that `mainfetch.LIBRARY_*` would ALSO need patching if a test drives mainfetch — A2 doesn't, so it's noted, not added.
  - **Seeding mechanism = `mvcommon.save_library` (the real helper), not hand-written JSON.** `save_library` splits by id prefix; all three ids start with `tv` so they all land in `library_series.json` (movies/anime become `{}`). This exercises the real persistence path and avoids drift. `load_library` then merges all three files back, returning the three entries.
  - **EXACT leaf-entry key set + status value (the load-bearing detail).** Modeled byte-for-byte on what `cmd_prep` writes (`main.py:906-919`): `short_id`, `filename`, `folder_path`, `status`, `uploaded`, `search_term`, `hash`, `metadata`, `tech_spec`, plus `parent_id` (since this episode has a season_map parent). `status = "local_ready"` and `uploaded = False` — these are the literal values `cmd_prep` writes for a freshly-prepped, not-yet-uploaded local file (confirmed at `main.py:910-911`), and they match the PLAN's suggestion. `short_id = mvcommon.generate_short_id(primary_id)`, `metadata = main.parse_metadata_from_id(primary_id)`, and `tech_spec` is a fixed `{resolution, video_codec, size_bytes}` dict (the value is opaque to the consumers A3 exercises). There is NO `type` key on the leaf — that's correct: a leaf is the implicit no-`type` entry, which the self-check asserts.
  - **Primary `.mkv` size + hash approach.** Wrote `b"BSG-COMBINED-EP-MASTER\n" * 9000` = ~207 KB (> `DUMMY_MAX_BYTES` 200_000) of deterministic bytes, and set `hash = hashlib.sha256(orig_path.read_bytes()).hexdigest()` from the real on-disk bytes. The size matters because `cmd_check`/`cmd_restore` early-skip any file `< DUMMY_MAX_BYTES` as an already-archived dummy (`main.py:1108`); since A3 runs check/push/restore on the resolved primary, the file must clear that threshold AND the stored hash must match so `cmd_check` reports a match. Verified in the throwaway test by calling `cmd_check(alias_id)` and asserting the recomputed sha256 equals the stored `hash`.
  - **season_map shape.** `{type, folder_path, total_episodes, children}` mirroring `cmd_prep`'s season_map creation (`main.py:881-886`). `children = sorted([primary_id, alias_id])` and `total_episodes = 2`, because `cmd_prep_season` appends the alias id to the parent's children and sets `total_episodes = len(children)` (`main.py:1085-1087`) — so a realistic post-combined-prep season_map has BOTH ids in `children`.
  - **Alias schema.** Exactly `{"type":"multi_ep_alias", "alias_of":<primary_id>, "parent_id":<season_id>}` and nothing else (matches `main.py:1080-1084` and the A1 outcome's documented schema). The self-check asserted the alias dict equals this exactly (no stray keys).
  - **Hard guards.** Beyond inheriting sandbox's LIBRARY_* guard, the fixture asserts the primary `.mkv` resolves UNDER `tmp_path` and that `"C:\\Media"` is not in its path, and that its size `> DUMMY_MAX_BYTES`. Used `media_dir.mkdir(parents=True)` and `rglob` was not needed here, but the Windows `[id]` glob gotcha is moot since the fixture creates files by exact name (no bracketed-glob lookups).
  - **Yielded dict keys:** `primary_id`, `alias_id`, `season_id`, `media_dir`, `orig_path`, and `sandbox` (the underlying sandbox paths dict, included so A3 can reach `lib_series`/`media_dir` if helpful). primary_id = `tv-en-2009-bsg-s04e19`; alias_id = `tv-en-2009-bsg-s04e20`; season_id = `tv-en-2009-bsg-s04`.
- Verification:
  - Throwaway self-check `tests/test_sandbox_alias_selfcheck_tmp.py` (created, run, then DELETED): `python -m pytest tests/test_sandbox_alias_selfcheck_tmp.py -q` → **1 passed in 0.21s**. It asserted: all 3 entries present (and exactly 3), alias dict == exact 3-key schema, season_map children == sorted [primary, alias] with total_episodes 2, leaf status `local_ready` / uploaded False / parent_id == season_id / no `type` key, the on-disk `.mkv` exists and is > DUMMY_MAX_BYTES with `hash` == its real sha256, `_resolve_alias(lib, alias_id)` returns `(primary_id, leaf)`, and `cmd_check(alias_id)` runs (resolving to the primary and verifying the hash). File then removed; `ls` confirms it is gone.
  - `python -m pytest -q` (full suite, after deleting the throwaway) → **99 passed, 2 skipped in 8.59s** — identical to the recorded baseline (the 2 skips are the ffmpeg/mkvmerge-gated cases). The new fixture is collectable and breaks nothing; it is not consumed by any committed test until A3.

---

## Step A3 — [status: done]
- Executor: executor-sonnet
- Model: sonnet
- Mode: single-executor
- Files changed: `tests/test_alias_consumers.py` (NEW — 10 test functions)
- Outcome: Created a new regression test file that exercises every fixed consumer (#1-#8) and two control cases, using the `sandbox_alias` fixture. All 10 tests pass. Full suite grew from 99 to 109 passed (2 skipped unchanged). No regressions.
- Key decisions:
  - **`cmd_push` non-split path:** the primary entry has no `split_info` — it's a non-split single-file push. This exercises the `files_to_upload_paths = [local_file_path]` standard path rather than the split/resume path. The library side effect (`uploaded=True`, `status=onboarded`) is the meaningful assertion; the device file lands under a UID-tagged name, so we assert `len(on_device) >= 1` (name lookup) rather than a specific filename, avoiding the `[id]` Windows glob metachar.
  - **`cmd_replace` precondition:** `sandbox_alias` seeds `uploaded=False`. Since `cmd_replace` requires `uploaded=True` to proceed past the early-exit guard, the test mutates the library (loads, sets `uploaded=True`/`status=onboarded`, saves) before calling `cmd_replace(alias_id)`. This mirrors real usage (replace is called after push). The info line still prints (the `_resolve_alias` check is before the uploaded guard), and the side effect is asserted.
  - **`cmd_restore` precondition:** sets up the restore folder with the primary's own bytes (so standard path's hash check passes), places a dummy placeholder at the target, and flips the library to `archived`/`uploaded=True`. Asserts the full side effect (restored bytes at target, `status=restored_local`).
  - **`cmd_verify_restore`:** same restore-folder setup as cmd_restore but without a dummy at target (verify_restore is dry-run; does not move the file). Asserts info line + "SUCCESS" or "Verified" in stdout.
  - **`cmd_scan_unprepped`:** the alias id must NOT appear in stdout. The primary's file IS registered (so it won't appear as unprepped either). This assertion would catch any regression where an alias creates a phantom unprepped row.
  - **Control tests:** two parallel controls — `cmd_check(primary_id)` and `cmd_push(primary_id)` — assert the info line is NOT printed (real_id == manual_id → non-alias path unchanged).
  - **Library reload assertion for alias schema:** several tests call `mvcommon.load_library()` after the command and assert `set(library[alias_id].keys()) == {"type", "alias_of", "parent_id"}` — confirming the alias entry is never mutated by the command.
- Verification:
  - `python -m pytest tests/test_alias_consumers.py -v` → **10 passed in 5.48s** (all tests collected and green).
  - `python -m pytest -q` → **109 passed, 2 skipped in 76.75s** — prior 99 still pass; 10 new tests added; 2 skips unchanged.
