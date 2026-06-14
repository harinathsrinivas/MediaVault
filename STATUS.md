# Execution Log

Task: Fix the multi_ep_alias production crash (IMP-C12/C13) AND harden the agent workflow + testing strategy so a feature change can never silently break another command

Branch: fix/alias_crash_and_smoke_gate (from main)
PLAN: root /PLAN.md (live, gitignored); canonical copy to be restored under docs/feature-alias-crash-and-smoke-gate/ at finalize.
Baseline (pre-change, unmodified tree): `pytest -q` -> 99 passed, 2 skipped (the 2 skips are ffmpeg/mkvmerge-gated). This is the regression oracle.

Run model: USER-GATED, ONE STEP AT A TIME. The user reviews after each step and says "continue". This log + PLAN.md checkboxes + per-step git commits are the resume record — a fresh session can resume by reading this file, `git log --oneline`, and the PLAN.md `[x]` marks.

Planned step order: A1 -> A2 -> A3 -> B1 -> B2 -> B3 -> B4 -> B5 -> B6 -> B7 -> (bookkeeping: tierC/tierH/PRIORITY/priority-graph) -> (architect: ARCHITECTURE.md/README) -> Phase 3 finalize (restore docs/<feature>/PLAN.md, push, open PR). Merge to main is HUMAN-GATED.

Note on the in-scope pre-existing change: `.claude/agents/planner.md` was already modified (frontmatter) before this run; it is carried onto the feature branch and will be committed as part of step B3 (which edits planner.md), NOT with earlier steps.

## Step 1 — [status: done]
- Executor: executor-sonnet
- Model: sonnet
- Mode: single-executor
- Files changed: `main.py`
- Outcome: Two surgical edits to `cmd_repair_dummies` (lines ~2028-2062). (A) Added explicit `if entry.get("type") == "multi_ep_alias": continue` guard immediately after the existing `season_map` skip at line 2029, making the whole-library iterator explicitly alias-safe per CLAUDE.md/ENTRY_TYPE_KEYS guardrail. (B) Replaced the non-atomic two-line swap `os.remove(current_path)` + `os.rename(tmp_path, current_path)` with the single atomic `os.replace(tmp_path, current_path)`, eliminating the window where a crash between the two calls would leave no file at `current_path`. Both changes are identical in behavior for all valid non-alias entries under normal operation.
- Key decisions: `multi_ep_alias` skip placed immediately after `season_map` skip (before `prefix_filter` and `status` guards), matching the pattern established by IMP-C12 in `cmd_scan_unprepped`/`cmd_local_status`. The atomic `os.replace` idiom matches `make_video_dummy` at line 469.
- Verification: `python -m pytest tests/smoke -q` → 50 passed in 13.83s. `python -m pytest tests/smoke/test_smoke_all_commands.py -q -k repair_dummies` → 2 passed, 48 deselected in 0.33s.

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

---

## Step B1 — [status: done]
- Executor: executor-opus
- Model: opus
- Mode: single-executor
- Files changed: `tests/smoke/__init__.py` (NEW), `tests/smoke/conftest.py` (NEW), `tests/smoke/test_smoke_all_commands.py` (NEW). Did NOT touch `main.py`, `tests/conftest.py`, or any existing test (verified `git diff --stat main.py` empty after the revert-probe below).
- Outcome: Built the fast full-command smoke package — ONE suite that drives every user-facing command + its major options against tiny in-repo fixtures and the existing stub device/browser, asserting "no crash + correct top-level effect". 50 tests total: **49 passed, 1 skipped** (the single real-binary split case). The whole package runs in **~5.5–10.4s** (measured across 4 runs: 21.4s cold first-run, then 6.64s / 10.37s / 5.54s / 8.43s warm) — well under the ~30s budget. Full suite went **109 passed/2 skipped → 158 passed/3 skipped** (+49 passed, +1 skipped), no regressions.
- Key decisions:
  - **Two-group structure.** `TestEachCommand` = one fast per-command smoke against a plain `sandbox` library (asserts the top-level effect). `TestAliasSweep` = every user-facing command run against the `sandbox_alias` library (asserts no-crash) — the anti-PR#21 gate. Group 1b is the ONE gated real-binary split push.
  - **`sandbox_alias` sweep implementation (the crucial anti-PR#21 requirement).** Implemented as a dedicated `TestAliasSweep` class (NOT a fixture-parametrization) so each command can be invoked the exact way the CLI dispatches it and given the ALIAS id where that specifically exercises de-aliasing. Whole-library iterators (`scan_unprepped`, `local_status`, `sort`, `repair_dummies`) run over the alias-bearing library; single-id commands (`check`/`push`/`replace`/`restore`/`verify_restore`/`fetch`/`fetch_restore`) are given the **alias id** (`tv-…e20`) to drive `_resolve_alias`; season/group commands (`push_group`/`replace_group`/`restore_group`/`recover --scan`) run over the season_map whose children include the alias; plus `prep` over the alias id asserts the refuse-guard. **DEMONSTRATED to catch the regression:** temporarily reverting `cmd_scan_unprepped`'s alias skip (`("season_map","multi_ep_alias")` → `"season_map"`) makes `TestAliasSweep::test_scan_unprepped_alias` FAIL with the exact `KeyError: 'folder_path'` from PR #21, while `TestEachCommand::test_scan_unprepped` (plain library) still passes — proving the catch is alias-specific. `main.py` was restored byte-for-byte immediately (git diff empty).
  - **Root-fixture sharing.** `tests/smoke/` is a package *below* `tests/`, so `tests/conftest.py` fixtures (`sandbox`, `sandbox_alias`, `mock_device`, `mock_fetch`, `fake_dummy`, `stub_tech_specs`, `FakeAdb`, `_ffmpeg_available`/`_mkvmerge_available`, `mkvmerge_split_chunks`) are inherited automatically — NO `pytest_plugins` re-export needed. `tests/smoke/conftest.py` holds ONLY smoke-specific helpers. The skip-gate helpers and `FAKE_DUMMY_BYTES` are imported via `from conftest import …` (root conftest is importable on `sys.path`, same pattern the root `test_alias_consumers.py` already uses).
  - **New smoke-local fixtures (no new mocking philosophy):** (1) `smoke_local_root` patches **both** `mvcommon.LOCAL_ROOT` and `main.LOCAL_ROOT` to the sandbox `Media/` tree — required because `sandbox` redirects the three `LIBRARY_*` constants but NOT `LOCAL_ROOT`, and the two WALKERS (`cmd_scan_unprepped` @2459-2461, `cmd_recover(scan=True)` @738) build their walk roots from `LOCAL_ROOT/{Movies,Series,Anime}`; without the patch they would read real `C:\Media`. Same import-by-value binding hazard as `LIBRARY_*`, so both bindings are patched + hard-guarded. (This mirrors `test_recover_cli.py::test_scan_read_only`.) (2) `make_video` writes a deterministic ~264 KB `.mkv` (> `DUMMY_MAX_BYTES`). (3) `seed_split_parts` pre-seeds a `_parts/` chunk dir + returns `split_info` so `cmd_push` takes the RESUME branch (@1299) instead of a real split.
  - **DUMMY_MAX_BYTES choice (the called-out gotcha).** `make_video` writes **~264 KB (> 200_000)** so `cmd_check`/`cmd_prep`/`cmd_restore` exercise the REAL-media path (hash verify / merge) and never hit the dummy early-skip. The ONE place the dummy path is the point — `test_repair_dummies` — DELIBERATELY writes a `<200 KB` file and asserts `regenerated 1` + the file becomes `FAKE_DUMMY_BYTES`. Both are valid, asserted smokes.
  - **Which command used which mock:**
    - Library (all): `sandbox` / `sandbox_alias`.
    - `push`/`push_group`/`prep_push_rep`/`prep_push_rep_season`/`fetch`/`fetch_restore` + every alias-sweep command issuing adb: `mock_device` (stateful). The split-push cases pre-seed `_parts/` (resume) so NO real split runs on the hot path.
    - `replace`/`replace_group`/`repair_dummies`: `fake_dummy` (ffmpeg dummy stub).
    - `prep`/`prep_season`/`prep_push_rep*`: `stub_tech_specs` (no pymediainfo).
    - `scan_unprepped`/`recover --scan`: `smoke_local_root` (LOCAL_ROOT redirect).
    - `fetch`/`fetch_restore`: NOTE — `cmd_dispatch_fetch` runs `subprocess.run(["python","mainfetch.py",...])`; under `mock_device` that argv is parsed by the fake adb runner, matches none of push/shell/devices, and returns a no-op success, so fetch is exercised WITHOUT spawning a real subprocess/browser/device. `mock_fetch` (the in-process `mainfetch.trigger_download` stub) is exercised by its own round-trip test (`test_fetch_round_trip_with_mock_fetch`).
    - `set_poster`/`set_fanart`: monkeypatch `main.requests.get` with a fake 200 response so NO network is hit.
  - **Real-binary-gated skips: 1.** `test_push_real_split` (the one genuine `split_video_file`→mkvmerge split push) is gated on `_ffmpeg_available() and _mkvmerge_available()`. On this box ffmpeg is NOT on PATH (mkvmerge IS, via the configured `MKVMERGE_PATH`), so it skips cleanly — exactly the intended behavior; the suite stays green without the binaries. All other split cases pre-seed `_parts/` and need no real binary.
  - **Anti-patterns honored:** never touch real `C:\Media`/`library_*.json` (sandbox + `smoke_local_root` hard-guards); device files asserted via `rglob("*.mkv")` + `.name` (never a bracketed `[id]` glob); stdout via `capsys`.
- Verification:
  - `python -m pytest tests/smoke -q` → **49 passed, 1 skipped** in 5.54–10.37s (warm; 21.44s cold first run). Wall-time recorded: ~**6–10s typical**, well under the ~30s budget.
  - Regression-catch proof: with `cmd_scan_unprepped`'s alias skip temporarily reverted → `tests/smoke/.../TestAliasSweep::test_scan_unprepped_alias` **FAILED** (`KeyError: 'folder_path'`, main.py:2481) while `TestEachCommand::test_scan_unprepped` passed → **1 failed, 1 passed**; fix restored, `git diff --stat main.py` empty, sweep green again (**18 passed**).
  - `python -m pytest -q` (full suite) → **158 passed, 3 skipped in 22.10s** (was 109 passed/2 skipped; +49 passed, +1 skipped from smoke; no regressions).

---

## Step B1a — [status: done]
- Executor: executor-opus
- Model: opus
- Mode: single-executor
- Files changed: `tests/conftest.py` (`sandbox` fixture: dual-patch + guard + new `local_root` yield key), `tests/smoke/conftest.py` (`smoke_local_root` reconciled to a no-op confirm), `tests/test_b1a_localroot_probe_tmp.py` (DELETED — the prior executor's throwaway probe).
- Outcome: Completed across a SESSION-LIMIT INTERRUPTION — the prior B1a executor had already written the `tests/conftest.py` + `tests/smoke/conftest.py` edits (uncommitted in the working tree) and left a throwaway probe; this pass VERIFIED that work is correct/complete, deleted the probe, and ran the gates. The `sandbox` fixture now redirects `LOCAL_ROOT` (in addition to the three `LIBRARY_*` constants) so the whole-tree WALKERS — `cmd_scan_unprepped` (main.py:2459-2461) and `cmd_recover(scan=True)` (main.py:738), which build their walk roots from `LOCAL_ROOT/{Movies,Series,Anime}` — read the sandbox temp tree, never real `C:\Media`. Read-only access to real `C:\Media` was the prior risk; this makes the "no writes to real media" guarantee STRUCTURAL. Full suite + smoke suite both green and unchanged in count.
- Key decisions:
  - **LOCAL_ROOT redirect target = `tmp_path / "Media"`** (captured as `media_root` at `tests/conftest.py:45`, also yielded under the new dict key `local_root`). This is exactly `media_dir.parent.parent` (the `sandbox` media file lives at `tmp_path/"Media"/"Movies"/"TestMovie"`), and it is the SAME base under which `sandbox_alias` creates its Series media (`tmp_path/"Media"/"Series"/BSG/Season 04`, conftest.py:164). So the walk roots `LOCAL_ROOT/{Movies,Series,Anime}` resolve to the real fixture media — scan tests stay MEANINGFUL (they walk actual fixture files, not an empty dir) while never escaping `tmp_path`. This matches the value `test_recover_cli.py`'s read-only scan test already used.
  - **Dual-patch (the binding hazard).** `LOCAL_ROOT` was added to the SAME `for attr, path in [...]` loop that patches `LIBRARY_*` (conftest.py:61-69), so it is patched on BOTH `mvcommon.LOCAL_ROOT` AND `main.LOCAL_ROOT`. `main` does `from mvcommon import LOCAL_ROOT` (import-by-value → a separate binding), identical to the `LIBRARY_*` hazard documented in CLAUDE.md, so patching only one would leave a reader pointed at real `C:\Media`. The hard-guard `assert "C:\\Media" not in path` runs for `LOCAL_ROOT` too (conftest.py:67) — a future regression that forgets either patch trips the guard.
  - **`smoke_local_root` reconciliation = converted to a thin no-op confirm (NOT deleted).** `tests/smoke/conftest.py`'s `smoke_local_root` previously patched `LOCAL_ROOT` itself; now that `sandbox` owns the dual-patch, it would be redundant double-patching. It was reconciled to a fixture that patches NOTHING and instead ASSERTS the redirect is structurally live on both bindings (`str(media_root) == str(mvcommon.LOCAL_ROOT) == str(main.LOCAL_ROOT)` and no `C:\Media`) then yields the sandbox Media root. Kept (not removed) so existing smoke signatures that already list `smoke_local_root` keep working unchanged and the structural guarantee is double-checked at every use site.
  - **Guard.** The `C:\Media` hard-guard in `sandbox` now covers all four constants (`LIBRARY_MOVIES/SERIES/ANIME` + `LOCAL_ROOT`); `smoke_local_root` re-asserts the `LOCAL_ROOT` half belt-and-suspenders.
- Verification:
  - Verified on this box: `main.resolve_ffmpeg()` returns the configured Emby `FFMPEG_PATH` (exists); `MKVMERGE_PATH` exists; neither binary is on PATH (`shutil.which` → None) — so the 3 ffmpeg/mkvmerge tests skip under the OLD PATH-only `_ffmpeg_available()`, which B1b fixes.
  - `python -m pytest -q` (full, after deleting the probe) → **159 passed, 3 skipped in 11.35s**. (Was 158/3 at end of B1; +1 is the prior executor's now-deleted-but-already-counted probe net-out — the committed B1a tree is 159/3.)
  - Probe deletion confirmed: `ls tests/test_b1a_localroot_probe_tmp.py` → "No such file or directory".
  - `git status --short` before this pass showed only ` M tests/conftest.py`, ` M tests/smoke/conftest.py`, `?? tests/test_b1a_localroot_probe_tmp.py` — i.e. B1a touched exactly the two conftests (plus the now-deleted probe); no stray edits.

---

## Step B1b — [status: blocked]
- Executor: executor-opus
- Model: opus
- Mode: single-executor
- Files changed: `tests/conftest.py` (`_ffmpeg_available` body; `ffmpeg_multichunk_mkv` + `mkvmerge_split_chunks` subprocess `cmd[0]`). NO change to `main.py`. NO change to `tests/smoke/test_smoke_all_commands.py` (its `test_push_real_split` skipif already used `_ffmpeg_available() and _mkvmerge_available()` — once `_ffmpeg_available()` was fixed it runs, no edit needed). NO change to `_mkvmerge_available()` (already configured-path-or-PATH, production-aligned).
- BLOCKED REASON: B1b's own deliverable (reuse `main.resolve_ffmpeg()`, remove the divergent PATH-only ffmpeg detection, single genuine-absence skip) is DONE and correct, and 2 of the 3 previously-skipped tests now RUN and PASS. But the 3rd target — `tests/test_rollback.py::test_push_split_fail_before_upload_rolls_back` — now RUNS and **FAILS**, surfacing a PRE-EXISTING test-setup gap (not a rollback/production bug). Per the step's explicit instruction ("if any of them now FAILS rather than passes, STOP, do NOT mark done, diagnose and report as a finding; do not paper over it or re-skip it"), B1b is NOT marked `[x]`. The conftest edits are left in place (they correctly make the tests RUN — the whole point of B1b); the block is purely the surfaced failing test, which needs a separate decision.
- B1b edits (exact):
  - `_ffmpeg_available()` (conftest.py:500-513): body replaced with `return main.resolve_ffmpeg() is not None` — reuses the PRODUCTION resolver (configured `FFMPEG_PATH` if it exists on disk, else `shutil.which("ffmpeg")`), so it now finds Emby's bundled ffmpeg that is NOT on PATH. Docstring rewritten to explain the reuse + why PATH-only was wrong.
  - `ffmpeg_multichunk_mkv` (conftest.py:~517-533): capture `ffmpeg = main.resolve_ffmpeg()` once; the genuine-absence fallback is now `if ffmpeg is None: pytest.skip(...)`; the subprocess `cmd` list's first element changed from bare `"ffmpeg"` to the resolved `ffmpeg` path.
  - `mkvmerge_split_chunks` (conftest.py:~592-595): capture `ffmpeg = main.resolve_ffmpeg()` (guaranteed non-None here — this fixture depends on `ffmpeg_multichunk_mkv`, which already skips if ffmpeg is absent) and use it as the subprocess `cmd[0]` in place of bare `"ffmpeg"`. Its OWN top-level skip stays `_mkvmerge_available()`.
  - **Single genuine-absence skip pattern:** `ffmpeg_multichunk_mkv` skips iff `main.resolve_ffmpeg() is None`; everything else inherits ffmpeg-presence either by depending on that fixture or via the `_ffmpeg_available()` (== `resolve_ffmpeg() is not None`) skipif. mkvmerge absence is the separate `_mkvmerge_available()` skip (left as-is). NO hard collection/import failure on a box without ffmpeg.
  - Sweep result: the ONLY divergent ffmpeg detection in `tests/` was in `tests/conftest.py`; `tests/test_baseline_happy_path.py` only mentions ffmpeg in comments (no detection). After the edits, every live ffmpeg invocation in `tests/` goes through `main.resolve_ffmpeg()`; no remaining bare-`"ffmpeg"` invocation (only docstring/comment mentions).
- THE FINDING (diagnosis of the failing test — for the orchestrator/user to decide):
  - `tests/test_rollback.py::test_push_split_fail_before_upload_rolls_back` (test_rollback.py:106) documents the scenario "a GENUINE ffmpeg split succeeds, then the FIRST adb push fails → this-run _parts/checksums/split_info rolled back, master intact" and asserts `cmd_push(..., split_method="SIZE_MB", split_val="2")` returns `False`.
  - It uses the `ffmpeg_multichunk_mkv` fixture as its source file. That fixture's `testsrc` recipe compresses to **~50 KB** (measured: 49,998 bytes = 0.048 MB) — the fixture's OWN docstring already admits its "~6 MB" claim is false, and `mkvmerge_split_chunks` was written specifically to generate its own ~60 MB high-entropy source BECAUSE `ffmpeg_multichunk_mkv` is too small to split.
  - With a 0.048 MB source and `split_val="2"` (2 MB), `cmd_push`'s pre-split size check (`main.py:1311`: `if fsize_mb < target_mb`) prints "File size (0MB) is smaller than split limit (2MB). Skipping split." and falls through to a SINGLE-file push. The forced first-push failure (`fail_nth_subprocess(1, match=push)`) is then absorbed by `cmd_push`'s retry loop ("Retry 1/3 … ✅"), the push SUCCEEDS, so `result is True` and `assert result is False` fails.
  - Even at the smallest `SIZE_MB` ("1" = 1 MB), `split_video_file` adds a +10 MB per-chunk buffer (`main.py:190`), so a 50 KB source can NEVER produce ≥2 chunks. The test's premise (a real multi-chunk split) is structurally unrealizable with `ffmpeg_multichunk_mkv` as-is.
  - **Conclusion:** this is a genuine TEST-SETUP gap that real-binary coverage correctly exposed — the test never validated its documented split-rollback scenario because it was always SKIPPED (ffmpeg off PATH). It is NOT a rollback/production defect (the other real-binary rollback-adjacent path, `test_push_real_split`, passes). The auto-rollback mechanism itself was NOT touched and is not implicated.
  - **Recommended fix (NOT applied — out of B1b scope; needs a decision):** give this test a genuinely-splittable source so `cmd_push` takes the real split path. Cleanest option: depend on the existing `mkvmerge_split_chunks`-style high-entropy source, or generate a >~30 MB high-entropy `.mkv` for this test and split with `SIZE_MB "10"`, mirroring `mkvmerge_split_chunks` (which already reliably yields ≥2 real chunks). That is a test-only change in `tests/test_rollback.py` (and would also pull in mkvmerge, so gate on `_ffmpeg_available() and _mkvmerge_available()`). It does NOT alter any rollback behavior. This should be its own small step/decision, not folded silently into B1b.
- Verification (exact output):
  - 3 target tests in isolation (`-v`): `test_rehash.py::test_deterministic_merge_same_seed_yields_identical_hash` **PASSED**; `tests/smoke/test_smoke_all_commands.py::test_push_real_split` **PASSED**; `tests/test_rollback.py::test_push_split_fail_before_upload_rolls_back` **FAILED** (`assert True is False` at test_rollback.py:130) → "1 failed, 2 passed in 12.95s". (Pre-change baseline of the same 3: "3 skipped in 0.19s".)
  - `python -m pytest -q` (full) → **1 failed, 160 passed in 25.04s** (skip count dropped 3 → 0; all 3 ffmpeg tests now RUN; the single failure is the diagnosed test-setup gap, not a regression). Was 159 passed/3 skipped pre-B1b.
  - `python -m pytest tests/smoke -q` → **50 passed in 8.60s** (cold) / 8.99s / 9.55s (warm). Was 49 passed/1 skipped — `test_push_real_split` now RUNS and PASSES.
  - SMOKE-BUDGET CHECK: `test_push_real_split` setup (the `mkvmerge_split_chunks` ~60 MB ffmpeg generation) adds ~4.4s, but total `pytest tests/smoke -q` is ~8.6–9.6s — comfortably under the ~30s fast-gate budget. NO `@pytest.mark.slow`/opt-in needed; the case runs and the gate stays fast.

---

## Step B1b — resolution [status: done]
- Executor: executor-opus
- Model: opus
- Mode: single-executor
- Files changed: `tests/conftest.py` (ADDED a new `ffmpeg_splittable_master_mkv` fixture), `tests/test_rollback.py` (rewrote `test_push_split_fail_before_upload_rolls_back` + added a `from conftest import _ffmpeg_available, _mkvmerge_available`). NO change to `main.py` (change-gate honored — nothing in RollbackJournal / recover_journal / PONR / journal format touched). NO change to `ffmpeg_multichunk_mkv` (other tests rely on its ~50 KB size + `bigsample.mkv` name) or to `mkvmerge_split_chunks`.
- Outcome: Resolves the prior `blocked` finding above. The B1b `resolve_ffmpeg()` reuse (the prior pass's conftest edits) is in the tree and correct; this pass fixes the one test that was left FAILING by giving it a GENUINELY-splittable master so it actually exercises the live split-during-push → pre-upload-rollback path (the first time this scenario has ever run — it was always skipped before). The target test, the full `test_rollback.py` module, the full suite, and the smoke suite are all green; skip count is 0.
- The fix (exact):
  - NEW conftest fixture `ffmpeg_splittable_master_mkv` (factored out, reusable, gated): generates a HIGH-ENTROPY ~60 MB MKV via the PROVEN `mkvmerge_split_chunks` recipe — `main.resolve_ffmpeg()` + `color=c=black:s=640x480:d=6:r=25` + `-vf geq=random(1)*255:128:128` + `-c:v libx264 -qp 0 -pix_fmt yuv420p` → `splittable_master.mkv`. Skips cleanly via `_ffmpeg_available()` (the production resolver) on genuine absence; hard-guards the output path against real `C:\Media`. Left `ffmpeg_multichunk_mkv` and `mkvmerge_split_chunks` untouched.
  - `test_push_split_fail_before_upload_rolls_back` rewritten: now depends on `ffmpeg_splittable_master_mkv` (the ~60 MB master) instead of `ffmpeg_multichunk_mkv`; gated with `@pytest.mark.skipif(not (_ffmpeg_available() and _mkvmerge_available()), …)` (the live split inside cmd_push invokes mkvmerge via `split_video_file`, so both binaries are required); calls `cmd_push(split_method="SIZE_MB", split_val="10")` → `num_chunks=ceil(60/10)=6` → ~20 MB/chunk → ~3 real chunks (main.py:184-194). ALL existing post-state assertions are UNCHANGED (this-run `_parts/` removed, `checksums/` removed, `split_info` popped, master kept, entry `local_ready`/`uploaded=False`).
- KEY DECISION — failure injection changed from `fail_nth_subprocess(1, match=…push)` to an inline "fail EVERY push" `monkeypatch` (mirroring the established sibling pattern in `test_push_resume_does_not_delete_preexisting_parts`): cmd_push wraps each chunk push in `retry(attempts=3)` (main.py:1537). With a genuine multi-chunk split, failing ONLY the 1st matching push lets the SAME first chunk SUCCEED on retry attempt 2 → `any_upload_done=True` → the O-1 resume-message branch (main.py:1605), which does NOT roll back — so the asserted pre-upload-rollback post-state would be unreachable and the test would fail for a test-construction reason. To genuinely realize "first push fails → roll back this run" (the documented scenario + the assertions), every push must fail so the first chunk never lands and `any_upload_done` stays False → the pre-any-upload rollback branch (main.py:1614). This faithfully keeps the "forced first-push failure" SCENARIO and every assertion; it does NOT weaken the test. Rationale is documented in the test's docstring + an inline comment so it isn't "fixed back" to fail_nth_subprocess by a future editor. Alternative considered: keep `fail_nth_subprocess` and add a "fail all matching" mode — rejected as a needless infra change when the proven inline pattern already exists in this file.
- Verification (exact output, this machine — Emby ffmpeg + mkvmerge both present, neither on PATH):
  - `python -m pytest "tests/test_rollback.py::test_push_split_fail_before_upload_rolls_back" -v` → **1 passed in 3.99s** (PASSED; the split-push-fail→rollback scenario validated for the first time).
  - `python -m pytest tests/test_rollback.py -q` → **10 passed in 4.02s**.
  - `python -m pytest -q` (full) → **161 passed in 26.46s** (skip count 0; was 159 passed/3 skipped pre-B1b, 160 passed/1 failed at the blocked mid-point — now 161/0).
  - `python -m pytest tests/smoke -q` → **50 passed in 8.18s**.

## Step B2 — [status: done]
- Executor: executor-opus
- Model: opus
- Mode: single-executor
- Files changed: `main.py` (added the `ENTRY_TYPE_KEYS` registry constant + its documentation block; the temporary revert-demonstration edit to `cmd_scan_unprepped` was reverted and `main.py` left byte-for-byte in the alias-skipping final state), `tests/test_entry_schema_guard.py` (NEW).
- Outcome: Added `ENTRY_TYPE_KEYS` — the single documented source of truth for the three top-level library entry shapes (leaf / season_map / multi_ep_alias) and a per-type `physical` flag ("owns a file via folder_path+filename"). It is additive and documentation/test-only — NOT wired into any cmd_* path (the guard test is the enforcement). Added `tests/test_entry_schema_guard.py` with two tests: (a) a round-trip test that builds one entry of EACH registered type and asserts each survives `save_library`→`load_library` intact (byte-for-byte, type discriminator preserved, leaf stays type-less, no required key lost); (b) the load-bearing GUARD test that seeds a `sandbox` library with one entry of every non-physical type (season_map, multi_ep_alias) + a real leaf, then calls `cmd_scan_unprepped()`, `cmd_local_status()` (incl. the size-limit branch) and `cmd_sort()` and asserts none raises. The guard's non-physical set is DERIVED from `ENTRY_TYPE_KEYS` (`physical=False`) so it auto-extends when a new non-physical type is registered; the round-trip's type iteration is likewise registry-driven, so a new type cannot be added without exercising it here. Full suite green (163 passed, 0 skipped) and smoke suite green.
- Key decisions:
  - **Verified complete entry-type set (the B2 FIRST requirement):** grepped EVERY `"type": "..."` write and every `.get("type")`/`["type"]` read across `main.py`, `mainfetch.py`, `mvcommon.py`. There are exactly TWO `type` writes in the whole codebase — `season_map` (main.py:882, cmd_prep) and `multi_ep_alias` (main.py:1081, cmd_prep_season/IMP-E13). All `.get("type")` reads compare only against `"season_map"` and `"multi_ep_alias"`. `mvcommon.py` has ZERO type usage. The third type is the implicit LEAF (no `type` key — the cmd_prep entry at main.py:906). **No fourth/unexpected type exists — nothing to flag.** The registry's three types are the complete, verified set.
  - **Registry placement:** put `ENTRY_TYPE_KEYS` in the top-level config/schema constant block (right after `PUSH_VERIFY_REMOTE`, before the `UTILITIES` divider — main.py:84-116). Chose this over near `MVMETA_SCHEMA_VERSION` (line 1119) because that constant sits mid-function-region (inside the write_remote_mvmeta area) whereas the top-of-module block is where module-level schema/config constants belong and is import-safe + clearly additive.
  - **Registry `required` key sets (refined to match what the code ACTUALLY writes, per the step):** `leaf` → `{"folder_path","filename","status"}` physical=True (cmd_prep writes these always, main.py:906-916, plus uploaded/hash/short_id/search_term/metadata/tech_spec, optional parent_id/split_info — `required` is the minimal distinguishing set, documented as such); `season_map` → `{"folder_path","children"}` physical=False (main.py:881-886 also writes total_episodes; it HAS folder_path but NO filename, so it owns no physical file → physical=False); `multi_ep_alias` → `{"alias_of","parent_id"}` physical=False (main.py:1081-1083 writes EXACTLY type/alias_of/parent_id and nothing else). These match the plan's example key sets exactly because the example was already correct against the code. `physical` is documented as "owns a physical file on disk (has folder_path AND filename)" — only leaf qualifies.
  - **Test fixture choice:** used the `sandbox` fixture (LOCAL_ROOT-hermetic per B1a) directly and built the entries inside the test (rather than reusing `sandbox_alias`), because B2 explicitly specifies "build a `sandbox` library containing one entry of EACH type" and the round-trip test must assert against the registry's exact `required` key sets — a self-contained build keeps the test bound to `ENTRY_TYPE_KEYS`. All ids use the `tv-` prefix so `save_library` routes them into a single library file; the leaf's `.mkv` is created under the sandbox media dir (never real C:\Media).
  - **Two test names:** `test_every_entry_type_round_trips` (a) and `test_guard_read_commands_tolerate_non_physical_entries` (b).
- Revert-demonstration result (acceptance): temporarily reverted `cmd_scan_unprepped`'s skip from `if entry.get("type") in ("season_map", "multi_ep_alias"): continue` back to `if entry.get("type") == "season_map": continue` (main.py:2516, the pre-A1 / pre-PR-#21 state). The GUARD test then FAILED with exactly the production crash:
  ```
  >   p = os.path.join(entry['folder_path'], entry['filename'])
  E   KeyError: 'folder_path'
  main.py:2517: KeyError
  ```
  (raised when the iterator hit the `multi_ep_alias` entry, which has no `folder_path` — the exact PR #21 class). Then RESTORED `main.py` byte-for-byte to the alias-skipping form; re-ran the guard test → **2 passed**. Confirmed both whole-library iterators are back to the correct state (cmd_local_status main.py:2415 and cmd_scan_unprepped main.py:2516, both `in ("season_map", "multi_ep_alias")`). `main.py` left in the correct final state; rollback code and all other type checks untouched.
- Verification:
  - `python -m pytest tests/test_entry_schema_guard.py -q` → **2 passed in 0.17s** (after restore).
  - (demonstration) guard test with the alias skip reverted → **1 failed** with `KeyError: 'folder_path'` at main.py:2517; restored and re-ran → passes.
  - `python -m pytest -q` (full suite) → **163 passed in 33.15s** (0 skipped; was 161/0 pre-B2 → 161 + 2 new tests = 163/0). No regressions.
  - `python -m pytest tests/smoke -q` → **50 passed in 9.29s** (additive registry changes no behavior; smoke gate still green and within budget).

---

## Step B3 — [status: done]
- Executor: executor-opus
- Model: opus
- Mode: single-executor
- Files changed: `.claude/agents/planner.md` (BODY only — frontmatter byte-identical, validated). No other file touched. (PLAN.md B3 marked `[x]`; this STATUS.md append.)
- Outcome: Added two durable, enforced planning rules to the planner agent so the PR #21 bug class (a new shared data shape silently breaking a distant consumer + no cross-command gate) cannot recur. (1) A mandatory **Consumer Impact Analysis** rule + a required `## Consumer Impact Analysis` PLAN.md sub-section, REQUIRED whenever a step adds/changes/removes a shared data contract (entry type, library field/key, ID shape, `status` value, or any cross-module schema). It instructs the planner to (a) consult `ENTRY_TYPE_KEYS` in `main.py` as the authoritative source of truth for entry-type key shapes, and (b) grep EVERY consumer — `.values()`/`.items()` iterators, `entry['<key>']`/`entry.get('<key>')` derefs, and every `_resolve_alias` caller — and enumerate each in the plan with a `safe`/`needs-fix` verdict + `file:line` in a table that mirrors this project's CONSUMER AUDIT format. It cites PR #21 / IMP-E13 as the cautionary example (added `multi_ep_alias`, missed `cmd_scan_unprepped` → production `KeyError`). (2) A standing **SMOKE-GATE** rule: any plan whose steps touch `main.py`/`mainfetch.py`/`mvcommon.py` MUST include `pytest tests/smoke -q` as the final Verification line, in addition to `pytest -q`. Both rules are wired in three places (WORKFLOW step, PLAN.md STRUCTURE section list, and a standalone rule block) so they are unmissable.
- Key decisions:
  - **Three-point wiring for each rule (not just one mention), to match how existing mandates are enforced in this file.** The Consumer Impact Analysis is referenced in (i) a new WORKFLOW step 4 ("perform it NOW, during planning … This is not optional"), (ii) the PLAN.md STRUCTURE as a new `## Consumer Impact Analysis` output section placed between `## Risks and edge cases` and `## Verification` (it is risk analysis, and reads naturally there; explicitly marked "Omit … entirely when no step touches a shared data contract"), and (iii) a full standalone `CONSUMER IMPACT ANALYSIS (MANDATORY …)` rule block after the STRUCTURE. This mirrors how TESTING STEP RULES already governs required plan content from a standalone uppercase block. A single buried mention would be easy for the planner to skip; the WORKFLOW step makes it part of the planning loop, the STRUCTURE entry makes it part of the output skeleton, and the rule block carries the how-to + table format.
  - **WORKFLOW renumber, not ins-in-place.** Inserted the Consumer Impact Analysis as WORKFLOW step 4 and shifted the old 4/5 to 5/6 (single-vs-multi-candidate decision, then Produce PLAN.md). Placing it BEFORE the per-step candidate decision and the "Produce PLAN.md" step is correct: the audit informs how many fix steps the plan needs.
  - **CONSUMER AUDIT table format reproduced with the exact 6 columns** from this project's PLAN.md (`# | Site | Line(s) | Access | Verdict | Why`), with two worked example rows (one `needs-fix` = `cmd_scan_unprepped`, one `safe` = `cmd_sort`) drawn from this very task's audit, so the planner has a concrete template. Added the completeness guard: "Every consumer found by the greps MUST appear with a verdict — an empty or partial table is a failed analysis; each `needs-fix` row MUST name the step that fixes it." This is what makes the audit exhaustive rather than illustrative (the exact gap that sank PR #21).
  - **Grep targets enumerated explicitly** (whole-library `.values()`/`.items()` iterators; `entry['<key>']` and `entry.get('<key>')` derefs incl. renamed/removed keys; every `_resolve_alias` caller) so the planner runs concrete searches, not a vague "consider impact". Verdicts are required against the NEW shape, not the old one.
  - **`ENTRY_TYPE_KEYS` made the authoritative source of truth** (B2's registry): step 1 of the rule says to consult it for key shapes, and — when the change adds/alters an entry type — the plan must include updating `ENTRY_TYPE_KEYS` and its guard test. This closes the loop with B2 (registry) and B5 (executor "update the registry" instruction).
  - **Smoke-gate placed adjacent to the Verification structure + as its own rule block, with the module trigger explicit.** The `## Verification` STRUCTURE line now states the `pytest tests/smoke -q` requirement inline, and a standalone `SMOKE-GATE (MANDATORY …)` block states the trigger (`main.py`/`mainfetch.py`/`mvcommon.py`), the position (FINAL line, last gate), the rationale ("the single check that answers 'did this change break another command?' — the gap that let PR #21 ship"), and the explicit exception (docs-only / agent-file-only plans omit it). Kept it `pytest tests/smoke -q` (matching the suite invocation B1 created and the PLAN's own Verification block) IN ADDITION to `pytest -q`, never replacing it.
  - **Style match + surgical scope.** Used the file's existing uppercase rule-block header convention and declarative MUST phrasing; touched only the WORKFLOW list, the STRUCTURE Risks/Verification/Out-of-scope region, and inserted the two new blocks between `## Out of scope` and `TESTING STEP RULES`. No other section (MODEL ASSIGNMENT, MULTI-CANDIDATE GUARDRAILS, etc.) was altered. Frontmatter left byte-identical.
- Verification:
  - **Frontmatter footgun check (REQUIRED):** `python -c "import re,yaml; t=open('.claude/agents/planner.md',encoding='utf-8').read(); m=re.match(r'^---\r?\n(.*?)\r?\n---', t, re.S); d=yaml.safe_load(m.group(1)); print('frontmatter OK, name=', d['name'])"` → **`frontmatter OK, name= planner`** (no exception). Re-read of lines 1-7 confirms the `---` block (name/description/model/effort/tools) is byte-identical to the pre-edit original — the agent will still register.
  - Acceptance grep (`ENTRY_TYPE_KEYS|PR #21|IMP-E13|pytest tests/smoke -q|Consumer Impact Analysis|SMOKE-GATE`) → all present in the BODY: `ENTRY_TYPE_KEYS` at line 87; `PR #21`/`IMP-E13` cautionary citation at line 84; `pytest tests/smoke -q` at lines 75 & 105; `## Consumer Impact Analysis` section at line 71; `SMOKE-GATE` rule block at line 103. Both acceptance requirements satisfied (Consumer Impact Analysis referencing the registry + PR #21/IMP-E13; smoke-gate verification mandate).
  - No code/tests touched by this step (agent-file-only), so `pytest`/`pytest tests/smoke` were not re-run — unchanged from B2's green state (163 passed / 0 skipped; smoke 50 passed).

---

## Step B4 — [status: done]
- Executor: executor-opus
- Model: opus
- Mode: single-executor
- Files changed: `.claude/agents/orchestrator.md` (BODY only — frontmatter byte-identical, validated). No other file touched. (PLAN.md B4 marked `[x]`; this STATUS.md append.)
- Outcome: Wired the `pytest tests/smoke -q` cross-command gate into the orchestrator playbook at BOTH mandated points so a code-touching change can never be committed or shipped without proving it didn't break another command (the PR #21 class). (1) **Per-step gate (Phase 2A):** a new step 5 "SMOKE GATE (code-touching steps)" inserted BEFORE the COMMIT_STEP call (old step 5 renumbered to 6) — if the step modified `main.py`/`mainfetch.py`/`mvcommon.py`, run `pytest tests/smoke -q`; if RED, do NOT commit, treat as a failed acceptance check (STOP, don't mark done, report per ESCALATION RULES); proceed to commit only when green or no trigger-module was touched. (2) **Multi-candidate merged-result gate (Phase 2B):** a new step 8a runs the SAME gate on the MERGED result on the feature branch after MERGE_CANDIDATE_WINNER/ARCHIVE_CANDIDATES and before marking the step done (step 9) — judged candidates pass their own isolated tests, but only this confirms the merged code doesn't break another command. (3) **Pre-PR finalize gate (Phase 3):** the Verification gate now MUST include BOTH `pytest -q` AND `pytest tests/smoke -q` green before PUSH_BRANCH/CREATE_PR (with an explicit "run it anyway if PLAN.md omits it but a trigger module was touched"). The existing NO-SILENT-HANDLING preamble, the full ESCALATION RULES block (incl. the capability-gap NEVER-silently-degrade rule), and the Checkpoint 1 human-gated-merge rule are all left fully intact.
- Key decisions:
  - **Per-step gate placed BEFORE COMMIT_STEP, not after, in Phase 2A.** Inserted as a new step 5 between "Verify acceptance check" (step 4) and "Invoke git-agent COMMIT_STEP" (renumbered step 6), so the gate genuinely blocks the commit — a red smoke run STOPS before any commit, mirroring how a failed acceptance check is already handled. Wording explicitly says "treat it exactly like a failed acceptance check … (per ESCALATION RULES)" so it inherits the existing STOP/don't-commit/don't-mark-done escalation rather than introducing a parallel mechanism.
  - **Multi-candidate path handled honestly via a post-merge step 8a, not a pre-commit gate.** In Phase 2B the COMMIT is the squash-MERGE itself (MERGE_CANDIDATE_WINNER), so a literal "before commit" gate is impossible on this path — the winner's code only exists on the feature branch AFTER the merge. I added step 8a to run the gate on the merged feature branch and, if red, STOP before marking the step done (step 9), noting the merge commit "can be reverted as a failed step". This satisfies the plan's "applies to the 2B multi-candidate path's merged result too" requirement without misrepresenting when the commit happens. The per-step gate (Phase 2A step 5) explicitly cross-references step 8a so the reader knows the multi-candidate equivalent.
  - **Trigger-module set + rationale phrasing kept consistent with B3's planner.md edit.** Used the identical trigger set (`main.py`/`mainfetch.py`/`mvcommon.py`) and the same one-line rationale ("the single check that answers 'did this change break another command?' — the gap that let PR #21 ship") so the planner-mandated smoke-gate and the orchestrator-enforced smoke-gate read as one coherent rule across the two agent files.
  - **Phase 3 gate made belt-and-suspenders.** Beyond requiring both `pytest -q` and `pytest tests/smoke -q` green, added "If PLAN.md's Verification omits `pytest tests/smoke -q` but any step touched [trigger modules], run it anyway" and cross-referenced `CLAUDE.md`'s cross-command integrity gate — so a plan that under-specifies its Verification block still gets gated. Also updated the step-2 guard text from "If verification passes:" to "If verification passes (both `pytest -q` and `pytest tests/smoke -q` green):" so the pass condition is unambiguous.
  - **Surgical scope / style match.** Edited only the Phase 2A step-completion list, the Phase 2B step 8/9 boundary, and the Phase 3 Finalize step 1-2 — the three places the plan named. Used the file's existing numbered-step + declarative-MUST style. No change to WORKFLOW Phase 1, EFFORT TAG HANDLING, context-packaging sections, ESCALATION RULES, or the final-summary section. Frontmatter (name/description/model/effort/tools) left byte-identical.
- Verification:
  - **Frontmatter footgun check (REQUIRED):** `python -c "import re,yaml; t=open('.claude/agents/orchestrator.md',encoding='utf-8').read(); m=re.match(r'^---\r?\n(.*?)\r?\n---', t, re.S); d=yaml.safe_load(m.group(1)); print('frontmatter OK, name=', d['name'])"` → **`frontmatter OK, name= orchestrator`** (no exception). Re-read of lines 1-7 confirms the `---` block is byte-identical to the pre-edit original — the agent will still register.
  - Acceptance: smoke gate present at the per-step (code-touching) commit point — Phase 2A step 5 (line 102) + Phase 2B step 8a (line 178, merged-result) — AND at the pre-PR finalize point — Phase 3 step 1-2 (lines 188-189). Both required.
  - Preserved-rules check (grep `NO SILENT HANDLING|human-gated \(Checkpoint 1\)|NEVER silently degrade`): NO SILENT HANDLING preamble at line 11; Checkpoint 1 human-gated-merge at line 197; ESCALATION RULES capability-gap NEVER-silently-degrade at line 287 — all intact, none altered.
  - No code/tests touched by this step (agent-file-only), so `pytest`/`pytest tests/smoke` were not re-run — unchanged from B2's green state (163 passed / 0 skipped; smoke 50 passed).

---

## Step B5 — [status: done]
- Executor: executor-sonnet
- Model: sonnet
- Mode: single-executor
- Files changed: `.claude/agents/executor-sonnet.md`, `.claude/agents/executor-opus.md`, `.claude/agents/executor-haiku.md` (BODY edits only — all three frontmatters unchanged)
- Outcome: Added both mandated additions to all three executor agent files. (1) **Smoke-gate on code-touching steps** — in executor-sonnet.md: appended to step 4 of the SINGLE-EXECUTOR WORKFLOW ("Run tests / linters") as an indented note; in executor-opus.md: appended to step 5 of the SINGLE-EXECUTOR WORKFLOW ("Run acceptance checks") as an indented note with additional rationale about regressions; in executor-haiku.md: appended to step 4 of the WORKFLOW ("Run the step's acceptance check") as an indented note. The instruction reads: if the step modified `main.py`, `mainfetch.py`, or `mvcommon.py`, ALSO run `pytest tests/smoke -q`, fix any failure BEFORE marking the step done, and paste the smoke result into STATUS.md Verification. (2) **Respect the entry-type registry** — in executor-sonnet.md and executor-opus.md: added as a new numbered item 8 at the end of the WHEN WRITING TESTS section; in executor-haiku.md: appended after the existing `pytest -q` output instruction in the WHEN TOUCHING TESTS section. The instruction states: if you add or change a library entry type or shared entry field, update `ENTRY_TYPE_KEYS` in `main.py` AND ensure every whole-library iterator skips or `_resolve_alias`-resolves the new type. The opus instruction has additional context about reasoning carefully; the haiku instruction includes a "report blocked if unsure" safeguard matching its more conservative mandate. Each file's existing voice and structure were preserved; no unrelated sections normalized.
- Key decisions: Smoke-gate placed at the workflow step rather than in FAILURE HANDLING, so it is part of the forward path (not just the error path). Entry-type registry instruction placed in the "when writing/editing code" guidance section (WHEN WRITING TESTS is where all three files already scope code-writing constraints). Haiku's entry-type note includes an explicit "report blocked if unsure" because haiku is restricted from inventing fixes for non-trivial changes.
- Verification:
  - Frontmatter validation (all three at once):
    `python -c "import glob,re,yaml; [print('OK', f, '->', yaml.safe_load(re.match(r'^---\r?\n(.*?)\r?\n---', open(f,encoding='utf-8').read(), re.S).group(1))['name']) for f in sorted(glob.glob('.claude/agents/executor-*.md'))]"`
    Output:
    ```
    OK .claude/agents\executor-haiku.md -> executor-haiku
    OK .claude/agents\executor-opus.md -> executor-opus
    OK .claude/agents\executor-sonnet.md -> executor-sonnet
    ```
    All three frontmatters valid; no YAML exception; all three agent names print correctly.
  - No code or test files were modified by this step; `pytest` results are unchanged from the B4 state (163 passed / 0 skipped; smoke 50 passed).

---

## Step B6 — [status: done]
- Executor: executor-sonnet
- Model: sonnet
- Mode: single-executor
- Files changed: `CLAUDE.md`, `docs/testing-strategy.md`
- Outcome: Documented the cross-command integrity gate, smoke suite, `sandbox_alias` fixture, and `ENTRY_TYPE_KEYS` registry in both load-bearing docs. No code or test files were touched; `pytest` results unchanged from B5 state.
- Key decisions:
  - **CLAUDE.md — placement:** inserted the new "Cross-command integrity + smoke gate" subsection between "Improvement tasks" and "Auto-rollback is load-bearing — change-gate". This is the natural position — it is a peer enforcement rule alongside the auto-rollback change-gate, and it reads after the backlog/priority guidance where a new contributor would already be thinking about what can change.
  - **testing-strategy.md — pyramid row:** added the Smoke suite as a new tier box ABOVE the existing INTEGRATION tier (it spans all commands, so it sits above per-command integration tests), keeping the ASCII art pyramid consistent.
  - **testing-strategy.md — §4.7 / §4.8:** added `sandbox_alias` as §4.7 (with its yielded-dict API and a minimal usage example) and `ENTRY_TYPE_KEYS` + `test_entry_schema_guard.py` as §4.8 (data-shape guardrail). Placed immediately before the existing §5 decision tree so all fixture/guardrail documentation stays in §4.
  - **testing-strategy.md — §12:** added `pytest tests/smoke -q` as a named command with a comment ("cross-command integrity (<30 s)") immediately after the existing `pytest -q` full-suite line, so the pre-PR gate invocation is visible right at the top of the run section.
- Verification: No code or tests changed; `pytest`/`pytest tests/smoke` results are unchanged from B5 (163 passed / 0 skipped; smoke 50 passed). Docs verified by reading back both files post-edit and confirming all four required elements are present: smoke-suite pyramid row, `sandbox_alias` §4.7 entry, `ENTRY_TYPE_KEYS` §4.8 note, `pytest tests/smoke -q` in §12, and the "Cross-command integrity + smoke gate" subsection in CLAUDE.md.

---

## Bookkeeping (IMP-C12/C13 done, IMP-H3 added) — [status: done]
- Executor: executor-sonnet
- Model: sonnet
- Mode: single-executor
- Files changed:
  - `improvements/improvements_tierC.md` — IMP-C12 status: `pending` → `done` (note: iterators skip `multi_ep_alias`; regression tests in `test_alias_consumers.py` + smoke suite). IMP-C13 status: `pending` → `done` (note: `_resolve_alias` at lookup head of five commands; `cmd_prep` refuses alias; regression tests added).
  - `improvements/improvements_tierH.md` — IMP-H3 entry ADDED as `done`: "Cross-command smoke gate + consumer-impact guardrail + agent enforcement + out-of-band data-request protocol" with full deliverable summary (50-test smoke suite, `ENTRY_TYPE_KEYS` registry + guard test, planner Consumer Impact Analysis mandate, orchestrator per-step/pre-PR enforcement, executor smoke-gate + registry instructions, out-of-band DATA_REQUEST protocol across all 8 agent files + CLAUDE.md + docs/testing-strategy.md). Placed after IMP-H2 to match the existing H1/H2 entry format.
  - `improvements/PRIORITY.md` — (a) IMP-C12 and IMP-C13 removed from Band 0 table; remaining Band-0 rows renumbered (R6, R7, C14, C15). (b) `👉 SUGGESTED NEXT TASK` updated from IMP-C12 → **IMP-C14** (next highest-priority unfixed Band-0 item: the `push_group` infinite-loop hang + `mainfetch` IndexError + silent `replace`). (c) "Last updated" bumped to 2026-06-13. (d) DONE count updated 11 → 14; C12, C13, H3 added to the done list.
  - `docs/priority-graph/priority-graph.html` — (a) Header banner `⚡ Next: IMP-C12` → `IMP-C14` (text updated to match new NEXT pointer). (b) C12 and C13 node entries changed from `"crit","todo"` → `"done","done"` (rendered in the done ring, green color). (c) IMP-H3 node ADDED as `"done","done"` with a short note string. (d) Footer date 2026-06-12 → 2026-06-13, task count ~110 → ~111.
- Outcome: All four backlog/doc files now agree: IMP-C12 and IMP-C13 are done, IMP-H3 is a new done entry, and the NEXT pointer is IMP-C14.
- Key decisions:
  - **NEXT pointer = IMP-C14**, not any R-tier decision task. The Band-0 table after removing C12/C13 has R6 and R7 at rows 1-2, but both are `🚦 decision` (change-gated — they need a user choice, not code). IMP-C14 is the next actionable, code-level Band-0 bug (no gate, low risk, directly fixable). The NEXT pointer convention in PRIORITY.md is for the "do this first" task, so a decision-gated item shouldn't be the pointer when a code-ready bug is right behind it. The NEXT banner now also surfaces that C12/C13 are done, so the context is clear.
  - **HTML: no dependency-edge changes.** The existing `["C12","B7"]` edge (C12 unblocks B7 memoize) remains valid and is kept. Done-status nodes still appear in the EDGES filtering (`EDGES.filter(e=>idMap[e[0]]&&idMap[e[1]])`) so the edge remains visible on the graph.
  - **IMP-H3 tier file placement:** added after H2's `- Status: pending` line using the same `---` separator + `## IMP-H3: …` heading format as H1/H2.
- Verification: All four files read back and confirmed consistent: C12/C13 status=done in tierC.md; H3 entry present in tierH.md; PRIORITY.md DONE count=14, C12+C13+H3 in done list, Band-0 table has 4 rows (R6/R7/C14/C15), NEXT pointer = C14; priority-graph.html header = C14, C12/C13 nodes = `"done","done"`, H3 node present.

---

## Step B7 — [status: done]
- Executor: executor-opus
- Model: opus
- Mode: single-executor (completed across two sessions — an earlier run landed the bulk and was cut off by a session limit; this run finished the remaining pieces and validated the whole step)
- Files changed (WHOLE B7 step):
  - `.claude/agents/orchestrator.md` — FRONTMATTER `tools:` now includes `WebSearch, WebFetch`; BODY has the "OUT-OF-BAND DATA REQUESTS" handler in the Phase 2A dispatch loop (step 3a) that fetches and re-dispatches with a `DATA_RESPONSE` block. *(landed by the prior run; verified coherent this run)*
  - `.claude/agents/architect.md` — FRONTMATTER `tools:` includes `WebSearch, WebFetch` *(prior run)*; BODY one-line "WEB-CAPABLE (research)" note added *(this run — it was the only missing piece among the 5 pre-edited files)*.
  - `.claude/agents/executor-haiku.md`, `.claude/agents/executor-opus.md`, `.claude/agents/executor-sonnet.md` — each has the "NEED EXTERNAL DATA? RAISE A DATA_REQUEST — DO NOT BROWSE" rule + fenced `DATA_REQUEST` block; frontmatters correctly have NO web tools. *(landed by the prior run; verified coherent and not truncated this run)*
  - `.claude/agents/planner.md` — BODY: added the "PRE-RESOLVE EXTERNAL FACTS" rule block (after the SMOKE-GATE rule) + a new WORKFLOW step 5 wiring it in (renumbered subsequent steps); frontmatter untouched. *(this run)*
  - `CLAUDE.md` — BODY: added the "## Out-of-band data requests" subsection (after "Cross-command integrity + smoke gate", before "Auto-rollback"). *(this run)*
- Outcome: Completed the out-of-band data-request protocol across all six agent files + CLAUDE.md. Web tools (`WebSearch`/`WebFetch`) live ONLY on planner, orchestrator, and architect; the three executors stay web-less and instead raise a fenced `DATA_REQUEST`, which the orchestrator services and returns as a fenced `DATA_RESPONSE` before re-dispatching the same executor for the same step. This run added the four still-missing pieces: the architect body web-capable note, the planner pre-resolve+tag rule (rule block + workflow step), and the CLAUDE.md subsection; and sanity-checked the five files the interrupted run had already edited — all were coherent and not truncated mid-edit (no fixes needed beyond the architect body note, which the task flagged as a verify-and-add item).
- Key decisions:
  - **Field-name consistency (point 3 of the task):** before writing, read the exact block format the prior run used in `orchestrator.md` and the executors and matched it verbatim. Canonical DATA_REQUEST fields: `step`/`purpose`/`query_or_url`/`fields_needed`/`return_format`/`blocking`; DATA_RESPONSE fields: `step`/`fields_needed`/`data`/`source`. CLAUDE.md and planner.md reuse these exact names and the "web/doc access lives only on planner, orchestrator, and architect" phrasing so the protocol reads identically across all files.
  - **Planner tag wording:** used the exact tag `may require a DATA_REQUEST: <what>` specified in PLAN.md B7 detail #4, and steered the rule to PRESS pre-resolution (bake the fact into step Details) as the strongly-preferred path, with the tag as the sparing fallback — matching the rationale that every executor pause is a stall.
  - **Placement:** planner rule placed adjacent to the existing SMOKE-GATE / Consumer-Impact-Analysis process rules (peer mandatory-process rules); CLAUDE.md subsection placed between the B6 "Cross-command integrity + smoke gate" section and the "Auto-rollback" change-gate (peer agent-governance subsections). No unrelated content reflowed; no frontmatter touched except the prior run's intended grants.
  - **Architect body note:** the task listed the architect body note under "verify it also has … if missing, add it"; it was missing, so I added a single concise line rather than restructuring the architect doc.
- Verification:
  - Frontmatter + web-grant validation (all 8 agents, the footgun-safety gate):
    `python -c "import glob,re,yaml; [print(yaml.safe_load(re.match(r'^---\r?\n(.*?)\r?\n---', open(f,encoding='utf-8').read(), re.S).group(1)).get('name'), '::', 'web=' + str(('WebSearch' in str(yaml.safe_load(re.match(r'^---\r?\n(.*?)\r?\n---', open(f,encoding='utf-8').read(), re.S).group(1)).get('tools')) ))) for f in sorted(glob.glob('.claude/agents/*.md'))]"`
    Output:
    ```
    architect :: web=True
    executor-haiku :: web=False
    executor-opus :: web=False
    executor-sonnet :: web=False
    git-agent :: web=False
    judge :: web=False
    orchestrator :: web=True
    planner :: web=True
    ```
    All 8 names print, no exception thrown; web grants exactly as required (planner/orchestrator/architect True; executor-haiku/opus/sonnet + git-agent/judge False).
  - No code or test files were modified by this step (agent-doc + CLAUDE.md only); `pytest`/`pytest tests/smoke` state is unchanged from B6 (163 passed / 0 skipped; smoke 50 passed).

---

## Architect — ARCHITECTURE.md + README updates
- Verified the shipped changes against the code (`ENTRY_TYPE_KEYS` at `main.py:114`; `tests/smoke/` package; `tests/test_alias_consumers.py`; `tests/test_entry_schema_guard.py`) before editing docs.
- `ARCHITECTURE.md` sections edited (additive, section-targeted):
  - **§3 Repository Layout** — added the `tests/smoke/` package to the layout tree; bumped the `tests/` file count note (13 → 15) and listed the two new test files.
  - **§6.3 Entry schemas** — added an authoritative cross-link to `ENTRY_TYPE_KEYS` in `main.py` (type key shapes + `physical` flag) and the rule that whole-library iterators must skip/`_resolve_alias` non-physical types (the PR #21 / IMP-C12 crash class).
  - **§13 Testing Approach** — updated counts (163 passed + 50 smoke); added the smoke suite description, `test_alias_consumers.py`, `test_entry_schema_guard.py`, the `sandbox_alias` and `ffmpeg_splittable_master_mkv` fixtures, and the `sandbox` LOCAL_ROOT redirect note.
  - **§19 Agentic Development Workflow** — added §19.5 (IMP-H3): smoke-gate enforcement + Consumer Impact Analysis + the out-of-band DATA_REQUEST web-tool protocol.
- `README.md` — Status/disclaimers test bullet: added `pytest tests/smoke -q` as the pre-PR cross-command gate alongside `pytest -q`, briefly described the smoke suite, and added the `multi_ep_alias` consumers to the covered areas.
- Left out intentionally: did not bump the doc's "Last updated" footer / per-file line counts (no code-line changes to re-measure; out of this targeted update's scope), and did not restructure any unrelated section.

---

# Execution Log

Task: IMP-C14 — CLI parser papercuts (push_group hang, mainfetch argv guard, silent replace)

Branch: fix/cli_parser_papercuts (from main)

## Step 1 — [status: done]
- Executor: executor-opus
- Model: opus
- Mode: single-executor
- Files changed: main.py, PLAN.md
- Outcome: Extracted a module-level pure function `parse_push_group_args(args)` (placed immediately before `cmd_push_group`, ~`main.py:1679`) that reproduces the old `push_group` argv parse loop verbatim, moves the empty-args usage guard into the function, and ADDS the three missing `else:` fail-fast arms (for `SIZE_MB/SIZE_GB/COUNT` → "Missing value for split method.", `episodes` → "Missing value for episodes range.", `device` → "Missing value for device.") mirroring the sibling `push` parser's wording. `rehash` (no-value flag) and `tempdir` (already had its else arm) are unchanged; the final `else: i += 1` unknown-token silent-skip is preserved exactly. The function returns the 7-tuple `(group_id, method, val, ep_range, dev, eager, tdir)` and is side-effect-free apart from the print+`sys.exit(1)` on bad input (no `resolve_device`, no `cmd_push_group`). The `elif cmd == "push_group":` dispatch block was collapsed to one parse call + the EXISTING `cmd_push_group(...)` call, with `resolve_device(dev)` left in `__main__`. Bug A (a trailing value-keyword spinning `while i < len(args)` forever) is fixed: it now terminates via `sys.exit(1)`. The `push` (single-id) parser, `cmd_push_group`, and everything else were left untouched.
- Key decisions: extracted pure parser mirroring push; fail-fast via sys.exit (chose Open-Decision option (a) — smallest diff, matches `push`'s exit semantics, assertable via `pytest.raises(SystemExit)` with no hang); silent-skip of unknown tokens preserved unchanged (resolved decision #2). Used `episodes range.` (not `chunks range.`) in the new episodes arm to match `push_group`'s keyword name while keeping the `push` arm style.
- Verification:
  - `python -c "import main"` → `import OK` (clean import, no syntax/import-time error).
  - Inline acceptance script (imports main, asserts):
    - `parse_push_group_args(["g","SIZE_GB","8","device"])` → raised `SystemExit(1)` after printing `❌ Error: Missing value for device.` (does NOT hang).
    - `parse_push_group_args(["g","SIZE_GB","8","device","series","rehash"])` → returned `('g','SIZE_GB','8',None,'series',True,None)` (exact expected tuple).
    - `parse_push_group_args(["g","episdoes","1-3"])` → returned `('g',None,None,None,None,False,None)` (typo silently skipped, ep_range None, group_id preserved).
    - Bonus: trailing `SIZE_MB` and trailing `episodes` each raised `SystemExit` with the correct mirrored message.
  - `python -m pytest -q` → `163 passed in 74.02s`.
  - Smoke gate (required — step touched main.py): `python -m pytest tests/smoke -q` → `50 passed in 14.15s`.

---

## Step 2 — [status: done]
- Executor: executor-sonnet
- Model: sonnet
- Mode: single-executor
- Files changed: `mainfetch.py` (only)
- Outcome: Extracted a module-level pure function `parse_fetch_args(argv)` placed immediately before the `if __name__ == "__main__":` block (~`mainfetch.py:480`). The function takes the full argv list, guards with `if len(argv) < 3 or argv[1] != "fetch": print("Usage: fetch [id] [episodes] [range]"); sys.exit(1)` (fixing the bug where `len(sys.argv) < 2` passed but `sys.argv[2]` still raised `IndexError`), then parses `mid = argv[2]` and `epr = argv[4]` (when `len(argv) >= 5 and argv[3] == "episodes"`), and returns `(mid, epr)`. The function has no Selenium/browser side effects — it is pure. The `__main__` block was collapsed to `mid, epr = parse_fetch_args(sys.argv)` followed by the EXISTING `cmd_fetch_route(mid, epr)` call. Bug B (bare `python mainfetch.py fetch` raising `IndexError`) is now fixed: it prints the usage line and exits cleanly.
- Key decisions: extracted pure parser; guard now `len < 3 or argv[1] != 'fetch'`; cmd_fetch_route stays in __main__; usage string kept byte-identical to existing `"Usage: fetch [id] [episodes] [range]"`; error signal = `sys.exit(1)` (option (a), mirrors push/push_group, assertable via `pytest.raises(SystemExit)`).
- Verification:
  - `python -c "import mainfetch"` → `import OK` (no browser/Selenium instantiated at import time).
  - Inline acceptance checks (all four cases):
    - `parse_fetch_args(["mainfetch.py","fetch"])` → raised `SystemExit(1)` after printing usage (was `IndexError`).
    - `parse_fetch_args(["mainfetch.py","fetch","tv-en-2016-strangerthings-s01e03"])` → returned `('tv-en-2016-strangerthings-s01e03', None)`.
    - `parse_fetch_args(["mainfetch.py","fetch","tv-x","episodes","1-3"])` → returned `('tv-x', '1-3')`.
    - `parse_fetch_args(["mainfetch.py","wrongverb","x"])` → raised `SystemExit(1)` after printing usage.
  - `python -m pytest -q` → `163 passed in 64.27s` (0 skipped, no regressions).
  - Smoke gate (required — step touched mainfetch.py): `python -m pytest tests/smoke -q` → `50 passed in 24.62s`.

---

## Step 3 — [status: done]
- Executor: executor-sonnet
- Model: sonnet
- Mode: single-executor
- Files changed: `main.py` (only)
- Outcome: Expanded the one-liner `if manual_id not in library: return False` in `cmd_replace` (main.py:1853) into a three-line block that prints `❌ Error: '<id>' not found in library.` before returning False. Previously a typo'd id returned False with zero output, indistinguishable from success. The rest of `cmd_replace` is byte-identical — the `_resolve_alias` call and the existing "not marked as uploaded" `⚠️ Skipping` path are untouched. `cmd_replace_group` was not touched (out of scope per Open Decision #3).
- Key decisions: `cmd_replace` now prints `❌ Error: '<manual_id>' not found in library.` before `return False`; `cmd_replace_group` left untouched (it already prints `❌ No items found.` for an empty group, and any per-child not-found message is now covered for free via the updated `cmd_replace`).
- Verification:
  - Edit verified: `cmd_replace` at main.py:1851 now has the three-line guard (print then return False).
  - Smoke gate (required — step touched main.py): `python -m pytest tests/smoke -q` → **50 passed in 39.62s**.
  - Full suite: `python -m pytest -q` → **163 passed in 68.35s** (0 skipped, no regressions).

---

## Step 4 — [status: done]
- Executor: executor-sonnet
- Model: sonnet
- Mode: single-executor
- Files changed: `tests/test_cli_parsers.py` (NEW — 21 tests)
- Outcome: Created a new focused unit-test file covering the two extracted parsers and the cmd_replace not-found path. Tests are grouped into `class TestParsePushGroupArgs` (12 tests), `class TestParseFetchArgs` (7 tests), and two standalone `test_cmd_replace_*` functions. All 21 tests pass. Full suite grew from 163 to 184 passed (0 skipped). Smoke suite confirmed green at 50 passed.
- Key decisions:
  - **Fixture choice for parsers: NONE (pure function calls).** `parse_push_group_args` and `parse_fetch_args` are pure — they take a plain list and return a tuple or call `sys.exit(1)`. No library I/O, no ADB, no Selenium. All parser tests call the functions directly inside the test body with no fixtures, matching the "unit test (no fixtures)" stratum of docs/testing-strategy.md §7.1.
  - **Fixture choice for cmd_replace: `sandbox` + `capsys`.** `cmd_replace` calls `load_library()`, which reads `mvcommon.LIBRARY_*` bindings. The `sandbox` fixture (already in `tests/conftest.py`) redirects ALL three `LIBRARY_*` constants on BOTH `mvcommon` and `main` (the binding-hazard dual-patch), hard-guards against real `C:\Media`, and yields a dict of temp-dir paths. No DIY redirection was done. `capsys` (built-in pytest) captures stdout for the not-found message assertion.
  - **Trailing-value-keyword tests prove no-hang.** Each `pytest.raises(SystemExit)` case returns immediately because the production code calls `sys.exit(1)` — there is no infinite loop to time out on. If the old looping behavior had been left in place, these tests would hang pytest, making them a structural guard against regression.
  - **Unknown-token test mirrors push behavior.** `["myshow", "episdoes", "1-3"]` passes `"episdoes"` and `"1-3"` both through the silent-skip branch (`else: i += 1`) — neither is a recognised keyword, so both are skipped and `ep_range` remains `None`. This confirms resolved decision #2 is preserved.
- Verification:
  - `python -m pytest tests/test_cli_parsers.py -q` → **21 passed in 2.05s**.
  - `python -m pytest -q` (full suite) → **184 passed in 195.09s** (0 skipped, no regressions; prior 163 still pass + 21 new tests).
  - `python -m pytest tests/smoke -q` → **50 passed in 70.64s** (step is test-only, no main.py/mainfetch.py/mvcommon.py changes; smoke confirms no inadvertent breakage).

---

## Step 5 — [status: done]
- Executor: architect
- Model: opus
- Mode: docs-only (read-only on code)
- Files changed: `ARCHITECTURE.md`, `README.md` (no code files touched)
- Outcome: Documented the IMP-C14 user-visible behavior change across the two architect-owned docs. Surgical edits only — no restructuring of either doc, §12a (rollback) untouched.
- ARCHITECTURE.md changes:
  - §5 commands table: the `replace` row now notes that an unknown id prints `❌ Error: '<id>' not found in library.` before returning `False` (IMP-C14).
  - §5: added a new "CLI parsing seams (IMP-C14)" blockquote after the `episodes`-keyword note — documents the extracted pure functions `main.parse_push_group_args(args)` → `(group_id, method, val, ep_range, dev, eager, tdir)` with fail-fast "Missing value" arms mirroring `push` (replacing the prior trailing-value-keyword `while i < len(args)` hang), the preserved silent-skip of unknown tokens, and `mainfetch.parse_fetch_args(argv)` → `(mid, epr)` as Selenium-free/unit-testable.
  - §5 `python mainfetch.py fetch ...` subsection: rewrote the argv-parsing note (was "argv parsing in mainfetch.py:497-507 ... reads sys.argv[2]") to describe the extracted `parse_fetch_args`, the `len(argv) < 3 or argv[1] != "fetch"` guard, and that a bare `python mainfetch.py fetch` now prints `Usage: fetch [id] [episodes] [range]` and exits cleanly instead of raising `IndexError`.
  - §16 known-issues: struck through the stale "`push_group` argv parser can infinite-loop" bullet and marked it FIXED (IMP-C14), summarising the three fixes (push_group fail-fast, mainfetch bare-invoke IndexError, cmd_replace not-found message) and pointing at §5. No stale "hangs"/"IndexError" claims remain about these paths.
- README.md changes:
  - Commands section (after the `main.py fetch` wrapper note, before the `episodes` literal-trigger callout): added a one-line "Malformed invocations fail fast" blockquote covering push_group/replace trailing-value-keyword fail-fast, the bare `python mainfetch.py fetch` usage exit, and `replace <unknown_id>` reporting not-found.
- Key decision: documented push_group fail-fast + extracted parsers + mainfetch usage-guard + replace not-found; surgical, no restructure.
- Verification:
  - Grep for "IndexError"/"infinite-loop"/"hang" against these paths: the only remaining mentions are the historical/struck-through references in the now-FIXED §16 bullet and the §5 "instead of raising IndexError ... as it did previously" framing — no claim that the current behavior still hangs or throws.
  - No code files edited (Read-only architect; only `ARCHITECTURE.md` + `README.md` written).

---

## Step 6 — [status: done]
- Executor: executor-haiku
- Model: haiku
- Mode: docs-only (find/replace only; no code touched)
- Files changed: `improvements/improvements_tierC.md`, `improvements/PRIORITY.md`, `docs/priority-graph/priority-graph.html`, `PLAN.md`
- Outcome: Marked IMP-C14 done across the three tracking artifacts per the maintenance protocol. All three files now agree: IMP-C14 is `done` with the fix/cli_parser_papercuts branch summary; the 👉 SUGGESTED NEXT TASK and header "Next" pointers advanced to IMP-C15 (micro-robustness batch — repair_dummies atomic swap + _verify_chunk_hash IndexError guard). The DONE count in PRIORITY.md bumped from 14 to 15, and C14 was added to the DONE roster line. Band 0 table removed the C14 row and renumbered C15 from 4 to 3. No code files touched; pure bookkeeping.
- Key decisions: None — exact edits per the PLAN step 6 instructions, using the Edit tool for surgical find/replace.
- Verification:
  - Edits confirmed:
    - `improvements/improvements_tierC.md` IMP-C14: `- Status: done (fix/cli_parser_papercuts — ... unit tests in tests/test_cli_parsers.py)`
    - `improvements/PRIORITY.md`: Last updated line = "2026-06-14 (IMP-C14 done — fix/cli_parser_papercuts)"; 👉 SUGGESTED NEXT = IMP-C15 (micro-robustness batch); DONE count = 15; Band 0 table C14 row removed, C15 renumbered 4→3; C14 added to DONE roster.
    - `docs/priority-graph/priority-graph.html`: C14 TASKS array node = `["C14","parser papercuts","C","done","done","Fixed ..."]`; header "Next" = IMP-C15 with micro-robustness label.
    - `PLAN.md`: Step 6 marked `[x]`.
  - No code files modified; `python -c "import main, mainfetch"` clean (prior steps' changes remain in tree).
  - No test run required per step specification; full suite still green from prior step verification (184 passed, 0 skipped, smoke 50 passed).

---

## Step 2 (IMP-C15) — [status: done]
- Executor: executor-sonnet
- Model: sonnet
- Mode: single-executor
- Files changed: `main.py` (`_verify_chunk_hash` parse-and-compare block + docstring)
- Outcome: Replaced the single-line `result.stdout.strip().split()[0]` parse with a hex-validating guard. Empty stdout (`""`) produces `[]` from `.split()`, and the old `split()[0]` would raise `IndexError` — not in the C2 `retry_on=(CalledProcessError,)` set, so it escaped as a raw traceback aborting the push. The new code extracts `first = parts[0] if parts else ""` and validates it with `re.fullmatch(r"[0-9a-fA-F]{64}", first)`. Empty or garbled output (e.g. "sha256sum: applet not found") warns-and-returns, keeping the push alive (same pattern as the command-not-found `except` arm already in place). Only a well-formed 64-hex token that differs from `expected_sha256` raises `CalledProcessError` — the C2 retry path is byte-for-byte unchanged on genuine mismatches. The docstring was extended to document both warn-and-skip paths (command-not-found + empty/garbled stdout).
- Key decisions: Used `re.fullmatch` (already imported at `main.py:6`) for the hex validation. Warning message matches the style of the existing `sha256sum unavailable` message at line 1251 exactly (same two-space indent, same ⚠️ emoji, same "remote verification skipped for {basename}" tail). Case-sensitive comparison preserved (`first != expected_sha256`).
- Verification:
  - `python -m pytest tests/smoke -q` → **50 passed in 14.91s**
  - `python -m pytest tests/test_cmd_push_verify.py -q` → **5 passed in 0.54s**

---

## Step 3 (IMP-C15) — [status: done]
- Executor: executor-sonnet
- Model: sonnet
- Mode: single-executor
- Files changed: `tests/test_cmd_push_verify.py` (extended with 5 new unit tests), `tests/test_repair_dummies.py` (NEW file with 2 tests)
- Outcome: Added direct unit tests for `_verify_chunk_hash` (Bug 2) and an atomic-swap regression test for `cmd_repair_dummies` (Bug 1). For Bug 2, 5 new tests monkeypatch `main.subprocess.run` directly against `_verify_chunk_hash`, covering: empty stdout (IndexError regression guard), garbled stdout (warn-and-skip), valid matching hash (no warning/raise), valid mismatched hash (raises `CalledProcessError`), and command-not-found (existing arm guard). For Bug 1, 2 new tests: `test_repair_dummies_atomic_swap` seeds a tiny archived file, runs the repair, asserts the result at `current_path` contains `FAKE_DUMMY_BYTES`, asserts no `.repair_tmp` orphan, and uses a spy on `main.os.remove` to assert `current_path` was NOT passed to `os.remove` (regression guard against reverting to remove+rename); `test_repair_dummies_skips_alias` seeds both an archived entry and a `multi_ep_alias` entry, asserts the repair runs clean (no KeyError), and asserts the alias entry is untouched.
- Key decisions: Used two module-level helper functions `_stub_run(returncode, stdout)` and `_stub_run_raise(exc)` to build fake subprocess.run callables without adding fixtures or conftest entries. The `os.remove` spy wraps the real `os.remove` (via `monkeypatch.setattr(main.os, "remove", _spy_remove)`) so unrelated cleanup is unaffected; the assertion checks that `current_path`'s absolute path is not in the recorded-removed set. The alias case seeds the alias entry directly into the movies lib JSON alongside the archived entry, mirroring how the sandbox_alias fixture structures the multi_ep_alias schema (3 keys only: type/alias_of/parent_id).
- Verification:
  - `python -m pytest tests/test_cmd_push_verify.py tests/test_repair_dummies.py -q` → **12 passed in 0.81s**
  - `python -m pytest -q` (full suite) → **191 passed in 39.58s**
  - `python -m pytest tests/smoke -q` → **50 passed in 11.42s**

---

## Step 4 (IMP-C15) — [status: done]
- Executor: architect
- Model: opus
- Mode: docs-only (Read-only architect; only `ARCHITECTURE.md` + `README.md` written)
- Files changed: `ARCHITECTURE.md` (§7.5 push-verify warn-and-skip clause, §7.6 dummy-system iterator note + step 5 atomic-swap), `README.md` (`repair_dummies` command-table row)
- Outcome: Grounded every edit against the shipped Step 1-2 code (`main.py:_verify_chunk_hash` @1235-1266, `cmd_repair_dummies` @2027-2073) before writing. (A) ARCHITECTURE.md §7.5: extended the IMP-C8 post-push-verification warn-and-skip clause to state that warn-and-skip now covers TWO non-fatal cases — `sha256sum` unavailable (non-zero exit) AND empty/garbled device stdout (the verifier requires a well-formed 64-hex first token; empty or non-hex output → one warning + `return`, push stays alive, no `IndexError`); only a well-formed 64-hex token that differs from the expected hash still raises `CalledProcessError` and is retried under IMP-C2. (B) ARCHITECTURE.md §7.6: (1) added a sentence to the `cmd_repair_dummies` intro noting the whole-library iterator now explicitly skips both `season_map` and `multi_ep_alias` (an explicit `continue` for each), alias-safe by design per the ENTRY_TYPE_KEYS guardrail; (2) rewrote per-candidate step 5 to state the replacement is a SINGLE ATOMIC `os.replace(tmp, current)` — no window in which the path has no file (the prior `os.remove` + `os.rename` left such a gap) — mirroring `make_video_dummy`'s own atomic write and the IMP-C9 atomic-swap lesson. (C) README.md: appended a brief "; atomic swap" note to the `repair_dummies` command-table row (~line 138). Left the remote-verify test-coverage line (~line 294) untouched — it already reads "remote verify" accurately and needed no behavioral restructure.
- Key decisions: Surgical edits only — described the two shipped behaviors without restructuring either ARCHITECTURE.md section. README touch kept minimal (one-row note) per the plan's "do not over-edit; README has no behavioral push-verify section to restructure"; the §294 test line was judged accurate as-is and left alone. No code files edited (Read-only architect).
- Verification: docs-only, no tests run.

---

## Step 5 (IMP-C15) — [status: done]
- Executor: executor-haiku
- Model: haiku
- Mode: single-executor
- Files changed: `improvements/improvements_tierC.md` (IMP-C15 status line), `improvements/PRIORITY.md` (Last updated, SUGGESTED NEXT TASK block, BAND 0 table, DONE count+list), `docs/priority-graph/priority-graph.html` (C15 task node, Next banner), `PLAN.md` (step 5 marked `[x]`)
- Outcome: Marked IMP-C15 done in the tracking trio (tierC + PRIORITY.md + priority graph) with synchronized updates. (1) `improvements_tierC.md`: changed IMP-C15 `Status: pending` to `Status: done (fix/micro_robustness_c15 — cmd_repair_dummies non-atomic remove+rename replaced with single atomic os.replace + explicit multi_ep_alias skip; _verify_chunk_hash hex-validates the device sha256 first token (empty/garbled → warn-and-skip, only a well-formed differing hash raises CalledProcessError); unit tests in tests/test_repair_dummies.py + new cases in tests/test_cmd_push_verify.py)`. (2) PRIORITY.md: (a) bumped `Last updated` to `2026-06-14 (IMP-C15 done — fix/micro_robustness_c15)`; (b) rewrote the `## 👉 SUGGESTED NEXT TASK` block to point at **IMP-C16** (anime fetch profile routing) with its rationale, keeping the R6/R7 decision-awaiting note; (c) removed IMP-C15 from BAND 0, leaving only R6 and R7; (d) updated DONE count `## ✅ DONE (15)` → `## ✅ DONE (16)` and added `C15` (micro-robustness) to the done list. (3) priority-graph.html: updated C15 task node from `["C15","…","C","high","todo",…]` to `["C15","…","C","done","done",…]` with the final detail string, and updated the ⚡ Next banner from C15 to C16. All three files now agree: C15 is done, the next code task is C16, DONE count is 16.
- Key decisions: None. Mechanical edits following the established done-status pattern used by C12/C13/C14.
- Verification: No code touched → no smoke gate needed. No pytest run. All three tracking files edited; consistency confirmed by re-reading changed regions (no target string drifts, all edits applied cleanly).

---

## Step 1 — Add anime profile + data-driven map + profile_for_id()
Status: done
Key decisions: Added CHROME_PROFILES["anime"] -> ChromeProfile_Anime; added ID_PREFIX_PROFILE ordered list + DEFAULT_PROFILE + pure profile_for_id() helper above cmd_fetch_route. CHROME_PROFILES["default"] renamed to "movies" (key only; path unchanged). init_driver not touched (Step 2 scope).
Verification:
- `python -c "import mainfetch; print(mainfetch.profile_for_id('ani-x'), mainfetch.profile_for_id('tv-x'), mainfetch.profile_for_id('mov-x'), mainfetch.profile_for_id('legacy'))"` → `anime tv movies movies`
- `python -c "import mainfetch; print(mainfetch.CHROME_PROFILES)"` → `{'movies': 'C:\\Media\\Utils\\ChromeProfile', 'tv': 'C:\\Media\\Utils\\ChromeProfile_TV', 'anime': 'C:\\Media\\Utils\\ChromeProfile_Anime'}`

---

## Step 2 — Rewrite cmd_fetch_route + fix init_driver default key
Status: done
Key decisions: cmd_fetch_route now calls profile_for_id(manual_id) for routing; init_driver default and fallback changed from "default" to "movies"; no remaining "default" references in CHROME_PROFILES context.
Verification:
- `grep -n "default" mainfetch.py` → only `CHROME_PROFILE_NAME = "Default"` (line 40) and `--no-default-browser-check` (line 66) — zero CHROME_PROFILES["default"] or profile_key="default" references.
- `python -c "import mainfetch; print(mainfetch.profile_for_id('ani-ja-2006-deathnote01'))"` → `anime`
- `python -c "import mainfetch; print(mainfetch.init_driver.__defaults__)"` → `('movies',)`
- `python -m pytest tests/smoke -q` → 50 passed in 10.49s

---

## Step 3 — Pure unit tests for the routing map
Status: done
Key decisions: Created tests/test_anime_fetch_routing.py with 12 test cases covering all prefix patterns, the regression guard (ani != tv profile), and map-integrity guards.
Verification: python -m pytest tests/test_anime_fetch_routing.py -q → 12 passed in 0.62s; python -m pytest tests/test_cli_parsers.py -q → 21 passed in 0.76s (regression check).

---

## Step 4 — Smoke test for anime routing without a browser
Status: done
Key decisions: Added test_anime_fetch_routing_profile_selection to TestEachCommand; uses sandbox (empty libs) + optional init_driver monkeypatch; asserts 'anime' and 'ChromeProfile_Anime' in stdout; regression guard asserts no 'tv' routing for ani-* id.
Verification: python -m pytest tests/smoke -q → 51 passed in 12.41s (all green).

---

## Step 7 — Mark IMP-C16 done in improvements_tierC.md
Status: done
Key decisions: IMP-C16 status changed from pending to done with branch + summary.
Outcome: Updated improvements/improvements_tierC.md line 305 from "- Status: pending" to "- Status: done (fix/anime_fetch_profile — added 3rd Chrome profile `anime` → ChromeProfile_Anime; id-prefix→profile routing extracted to data-driven mainfetch.ID_PREFIX_PROFILE + pure profile_for_id(); ani-* now drives anime account, tv-* series, movies movies; external-config sourcing deferred to IMP-A5; unit tests in tests/test_anime_fetch_routing.py + smoke coverage in test_anime_fetch_routing_profile_selection)".
Verification: File edited successfully; IMP-C16 block (starts at line 290) now shows status: done with complete summary.

---

## Step 8 — Update PRIORITY.md
Status: done
Key decisions: Last updated bumped to C16; NEXT pointer changed from C16 to A10; C16 removed from Band 1; DONE count 16→17, C16 added to done list.
Outcome: Made four targeted edits to improvements/PRIORITY.md: (1) bumped "Last updated" line from "2026-06-14 (IMP-C15 done — fix/micro_robustness_c15)" to "2026-06-14 (IMP-C16 done — fix/anime_fetch_profile)"; (2) rewrote "## 👉 SUGGESTED NEXT TASK" block from IMP-C16 (anime fetch profile routing) to IMP-A10 (truth-up requirements.txt) with updated rationale; (3) removed the C16 row from BAND 1 table (previously row 7); (4) updated "## ✅ DONE (16)" to "## ✅ DONE (17)" and added "C16 (anime fetch profile)" to the done list immediately after C15.
Verification: All four edits applied cleanly via the Edit tool. PRIORITY.md now reflects: Last updated is C16, NEXT task is A10 (not C16), DONE count is 17 with C16 in the list, Band 1 no longer mentions C16.

---

## Step 9 — Update priority-graph HTML
Status: done
Key decisions: Banner updated from C16 to A10; C16 node flipped from "high","todo" to "done","done" with new tooltip; C16->X1 and C16->S3 edges confirmed present and unchanged.
Outcome: Made three edits to docs/priority-graph/priority-graph.html: (1) Updated the ⚡ Next banner (line 84) from "IMP-C16 · anime fetch profile routing" to "IMP-A10 · truth-up requirements.txt", reflecting the new highest-priority next task from PRIORITY.md. (2) Changed the C16 node in the TASKS array (line 160) from priority "high" / status "todo" to "done" / "done", with updated tooltip text documenting the fix/anime_fetch_profile implementation (3rd profile, data-driven routing, ani-* now routes anime). (3) Verified EDGES array contains both required edges ["C16","X1"] and ["C16","S3"] (found at line 252) — both present, no changes needed. No other changes made.
Verification: File edited; C16->X1 and C16->S3 edges present and intact. No syntax errors.

---

## Step 6 — [status: done]
- Executor: executor-haiku
- Model: haiku
- Files changed: `ARCHITECTURE.md`, `README.md`, `PLAN.md`
- Outcome: Updated documentation to reflect the three Chrome profiles (anime profile added and routed separately from TV). Four targeted edits to ARCHITECTURE.md: (1) §3 repo-layout Utils block — updated ChromeProfile_TV comment from "TV + anime" to "TV (series account)" and added ChromeProfile_Anime entry for anime. (2) §8.1 CHROME_PROFILES configuration snippet — replaced the two-key dict with three keys ("movies", "tv", "anime") pointing to their respective paths. (3) §8.1 routing prose — replaced the hardcoded `if startswith("tv") or startswith("ani")` chain with description of the data-driven `ID_PREFIX_PROFILE` ordered list and pure `profile_for_id()` function, noting that IMP-A5 will source this from mvconfig.json. (4) §8.6 cmd_fetch_route bullet — added note that profile selection is now data-driven via `profile_for_id(manual_id)` (IMP-C16). One edit to README.md: prerequisites section — changed "Two persistent Chrome user-data directories" to "Three", added ChromeProfile_Anime entry for the anime account, updated ChromeProfile_TV description to say "TV series" (not "and anime"), clarified that each profile must be manually logged in once.
- Key decisions: All edits are pure documentation of the already-shipped code behavior from Steps 1-5; no behavioral changes introduced by this step; all target strings found and replaced cleanly per the spec; README section rewritten as specified.
- Verification: `python -m pytest tests/smoke -q` → **51 passed in 15.35s** (smoke gate passed; documentation-only step, no code changes, but all smoke tests remain green)

---

## Step 10 — Write tracked feature docs
- Status: done
- Executor: executor-haiku
- Model: haiku
- Files changed: `docs/feature-anime-fetch-profile/PLAN.md` (overwritten with finalized version), `docs/feature-anime-fetch-profile/DECISIONS.md` (created)
- Outcome: Replaced the draft `docs/feature-anime-fetch-profile/PLAN.md` with the finalized version documenting all 10 completed steps, resolved Open Decisions (OD-1 to OD-4), bug summary, files changed, verification results, and next task (IMP-A10). Created `docs/feature-anime-fetch-profile/DECISIONS.md` recording the four decisions (OD-1: data-driven constant now, config later via IMP-A5; OD-2: rename "default" to "movies" for consistency; OD-3: confirmed anime profile path + user one-time login; OD-4: live end-to-end test with all 3 profiles verified). Both files now serve as permanent archived artifacts of the IMP-C16 task completion.
- Key decisions: Overwrote PLAN.md with finalized version (all 10 steps checked, verification results confirmed). Created DECISIONS.md as a per-decision summary table documenting the option chosen and rationale for each OD.
- Verification: Both files written successfully; PLAN.md reflects complete task status with all steps marked done and verification results listed; DECISIONS.md captures all four decisions with clear rationale per the OD format.
