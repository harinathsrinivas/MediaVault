# Execution Log

Task: IMP-C18 — fix anime `sSSEE` episode-range filter (silent 0-match) via one shared `mvcommon.episode_num_from_id` extractor (all 5 range-filter sites) + a loud 0-match guard (warn + suppress success banner).

Branch: fix/imp_c18_episode_range (from main @ ea5bb32, PR #27 / IMP-C17 keep-alive merge)
PLAN: root /PLAN.md (live, gitignored); canonical copy restored under docs/feature-fix-episode-range/ at finalize.
Baseline (pre-change, unmodified tree): `python -m pytest -q` -> 223 passed in 48.08s. This is the regression oracle.

Run model: orchestrator pipeline driven from the MAIN session (orchestrator.md as playbook). Executors dispatched depth-1 (executor-opus/sonnet/haiku). git-agent commits per step. User wants a per-step change summary; run proceeds end-to-end and STOPS at the PR (Checkpoint 1 — merge to main is human-gated).

Planned step order: 1 (opus, mvcommon helper + unit tests) -> 2 (sonnet, swap 3 broken sites) -> 3 (sonnet, retrofit 2 correct sites) -> 4 (opus, 0-match guard + banner) -> 5 (sonnet, test_episode_range_filter.py resolve_targets test) -> 6 (sonnet, smoke sSSEE fixture) -> 7 (haiku, mark IMP-C18 done in tierC) -> 8 (sonnet, PRIORITY.md + priority-graph.html) -> 9 (sonnet/architect, ARCHITECTURE.md + README.md) -> Phase 3 finalize (full suite + smoke, restore docs/<feature>/, push, open PR).

Decisions baked in (from user, pre-run): A+C (shared helper + loud guard); scope = 3 broken + 2 correct retrofitted (incl. cmd_push_group which the ticket missed); 0-match guard = warn + suppress success banner; helper = `mvcommon.episode_num_from_id(child_id, base_id) -> float|None`, prefix-strip via slicing + `^[eExX]?(\d+(?:\.\d+)?)$`; no schema change (ENTRY_TYPE_KEYS untouched); console messaging only.

---

## Step 1 — [status: done]
- Executor: executor-opus
- Model: opus
- Mode: single-executor
- Files changed: `mvcommon.py` (added pure helper `episode_num_from_id`), `tests/test_episode_range_filter.py` (new — 13 unit tests).
- Outcome: Added the shared season-aware extractor `episode_num_from_id(child_id, base_id) -> float | None` to the UTILITIES section of `mvcommon.py` (immediately after `parse_size_str`), verbatim per the plan: prefix-strip via slicing `child_id[len(base_id):]` (falsy/None base → parse whole id) then the user-decided anchored regex `^[eExX]?(\d+(?:\.\d+)?)$`. It is pure (uses only the already-present `re`; no new imports, no I/O) and matches the in-tree reference at `main.py:2706-2707`. No call sites touched (steps 2-4). New test file covers every required shape (glued sSSEE→2.0, half-ep glued→16.5, separator series `s03e20`→20.0, separator anime `x`→5.5, bare `eNN`→20.0, correct-prefix glued-number→7.0, empty-leftover/junk→None, falsy/None base→whole-id parse). Full suite 223→236 passed; smoke 52 passed.
- Key decisions:
  - **SPEC CONTRADICTION found and resolved in favor of the load-bearing helper.** The step's "base-not-a-prefix fallback" bullet asserts `episode_num_from_id("ani-ja-2006-deathnote07", "wrong-base") == 7.0`. With the user-DECIDED **anchored** regex `^[eExX]?(\d+(?:\.\d+)?)$`, the whole-id fallback parses `"ani-ja-2006-deathnote07"` (leading letters/dashes) and correctly returns **None**, NOT 7.0 — returning 7.0 would require the OLD unanchored mid-string match this fix exists to remove. The helper (user-decided + matching `main.py:2707`) is the source of truth, so the test asserts the correct `None`; I added `test_whole_id_that_is_a_bare_episode_token_parses` to pin the case the plan's "(parses whole id)" annotation actually meant (`f("07","wrong-base")==7.0`). The plan's intent ("falsy/None base → parse whole id, no crash") is fully satisfied. **Steps 2-4 must NOT rely on a non-prefix `base_id` yielding the embedded episode number — it yields None; in all real call sites `base_id`/`group_id`/`manual_id` IS a true prefix of the children, so this edge never fires in practice.**
  - Placed the function right after `parse_size_str` (it was the last function in the file) to satisfy "near `parse_size_str` in the UTILITIES section."
  - Made the test module docstring a raw string (`r"""`) to silence a `SyntaxWarning: invalid escape sequence '\d'` from the regex prose in the header — keeps the suite warning-clean. No behavior impact.
- Verification:
  - `python -m pytest tests/test_episode_range_filter.py -q` → `13 passed in 0.17s` (0 warnings).
  - `python -m pytest -q` → `236 passed in 43.95s` (baseline 223 + 13 new; no regressions).
  - `python -m pytest tests/smoke -q` → `52 passed in 12.67s` (smoke gate; `mvcommon.py` modified, well under 30s).
  - Empirical helper check (all plan shapes) confirmed: s0202→2.0, s0216.5→16.5, s03e20→20.0, x05.5→5.5, e20→20.0, deathnote07 w/ correct base→7.0, deathnote07 w/ wrong-base→None, empty-leftover→None, foo/foo→None, falsy base + bare token→7.0.

---

## Step 2 — [status: done]
- Executor: executor-sonnet
- Model: sonnet
- Mode: single-executor
- Files changed: `mainfetch.py` (import line ~29, `resolve_targets` filter block ~427-430), `main.py` (import block ~29-31, `cmd_push_group` filter loop ~1771-1780, `cmd_restore_group` filter loop ~2394-2401).
- Outcome: Replaced the three unanchored two-line regex ladders in `resolve_targets` (mainfetch.py), `cmd_push_group` (main.py), and `cmd_restore_group` (main.py) with calls to the new shared `episode_num_from_id` helper from mvcommon. Added `episode_num_from_id` to the import lines in both files. The unanchored `re.search(r'(\d+(?:\.\d+)?)$', ...)` fallback is now gone from all three sites. All other code (range-parse `map(float, ...)`, `try/except`, "Filtered to N" prints, de-alias loops) is unchanged.
- Key decisions:
  - Confirmed variable names by reading each function: `resolve_targets` uses `child_id` / `s` / `e` / `filtered`; `cmd_push_group` uses `mid` / `start` / `end` / `filtered_ids`; `cmd_restore_group` uses `mid` / `start` / `end` / `filtered`.
  - `resolve_targets` base is `manual_id` (season-map key, in scope); `cmd_push_group` and `cmd_restore_group` base is `group_id` (the function argument).
  - Did NOT touch the two already-correct sites (`cmd_prep_push_rep_season` ~2707, `_season_resume_cmd` ~2741) — those are step 3.
  - Did NOT add the 0-match guard — that is step 4.
- Verification:
  - `python -m pytest tests/test_prep_season_episode_parse.py -q` → `11 passed in 1.29s`
  - `python -m pytest -q` → `236 passed in 59.35s` (baseline 236; no regressions)
  - `python -m pytest tests/smoke -q` → `52 passed in 14.28s` (smoke gate; main.py + mainfetch.py modified; well under 30s)
  - Grep for unanchored fallback in main.py + mainfetch.py at the three function sites: 0 matches confirmed.

---

## Step 3 — [status: done]
- Executor: executor-sonnet
- Model: sonnet
- Mode: single-executor
- Files changed: `main.py` (`cmd_prep_push_rep_season` filter loop ~2694-2701; `_season_resume_cmd` ~2729-2737).
- Outcome: Replaced the two inline episode-range patterns in `cmd_prep_push_rep_season` and `_season_resume_cmd` with calls to the shared `episode_num_from_id` helper. The filter site (`cmd_prep_push_rep_season`) is a straightforward swap: `ep_num = episode_num_from_id(mid, base_id)` + guard. The resume-string site (`_season_resume_cmd`) required special handling to preserve the original digit string (e.g. `"02"`, `"16.5"`) for byte-identical output: the helper is used as the gatekeeper (None check), then the raw digit string is extracted without regex via prefix-strip + single-char e/E/x/X strip. This avoids floating-point formatting (`2.0` vs `2`) and preserves leading zeros (`02` not `2`). After the edit, `grep eExX main.py` returns no matches — the helper in `mvcommon.py` is now the single source of truth for all 5 range-filter sites.
- Key decisions:
  - Confirmed variable names: filter loop uses `mid` / `base_id` / `start` / `end` / `filtered_ids`; resume function uses `rid` / `real_id` / `base_id` / `ep_nums`.
  - The plan's suggested conversion `str(int(ep)) if ep == int(ep) else str(ep)` would break the existing test `test_season_mid_failure_keeps_completed_and_prints_resume` which asserts `"episodes 02"` (leading-zero format from IDs like `tv-en-2022-showe02`). The float-to-int path drops leading zeros. Instead: use the helper as the None-gate only, then extract the raw digit string from the ID by prefix-strip + optional e/E/x/X single-char strip (no regex). This is byte-identical to the original `m.group(1)` string and satisfies the acceptance criteria.
  - The `eExX` regex literal is gone from both functions; the regex lives only in `mvcommon.episode_num_from_id`.
- Verification:
  - `python -m pytest tests/test_prep_season_episode_parse.py -q` → `11 passed in 1.13s`
  - `python -m pytest -q` → `236 passed in 38.44s` (baseline 236; no regressions)
  - `python -m pytest tests/smoke -q` → `52 passed in 9.92s` (smoke gate; main.py modified; well under 30s)
  - `grep eExX main.py` → no matches confirmed.

---

## Step 4 — [status: done]
- Executor: executor-opus
- Model: opus
- Mode: single-executor
- Files changed:
  - `main.py` — `cmd_push_group` filter block (~1764-1791: capture pre-filter count/sample + add 0-match `⚠️` warning); `cmd_restore_group` (~2383-2419: `empty_via_range` flag + 0-match `⚠️` warning, downgrade the "Batch Restore Complete" line on 0-via-range, **return `count`** [was None]); `cmd_fetch_restore` (~2867-2895: capture `restored_count` from `cmd_restore_group`, make the `✅✅✅` banner conditional, print `⚠️ FETCH & RESTORE finished with 0 items` on `is_season_map and episode_range and restored_count == 0`).
  - `mainfetch.py` — `resolve_targets` filter block (~420-440: capture pre-filter count/sample + add the same 0-match `⚠️` warning before the empty-list return; empty-list return contract unchanged).
  - `tests/smoke/test_smoke_all_commands.py` — new `_seed_anime_ssee_season` helper + glued-anime constants (`ANIME_SEASON_ID`/`ANIME_EP_IDS`); two new tests in `TestEachCommand`: `test_fetch_restore_empty_range_suppresses_banner` (a+c) and `test_fetch_restore_real_range_keeps_banner` (b), plus a `_seed_anime_restore_copies` helper.
- Outcome: Implemented the user-decided 0-match guard ("warn + suppress success banner; continue the run, no error, no non-zero exit"). The distinguishing signal at every site is **a NON-EMPTY pre-filter list reduced to 0 by a range** — captured as `pre_filter_count`/`pre_filter_sample` at the top of each `if range:` filter block (so a genuinely empty season, `pre_filter_count == 0`, never warns). `cmd_push_group` only needed the warning (it has no celebratory banner; its existing `if not target_ids: print("❌ No items found to push"); return` stops cleanly). `cmd_restore_group` now flags `empty_via_range` and, on a 0-via-range run (`empty_via_range and count == 0`), prints the `⚠️` instead of the green "Batch Restore Complete" line, and **returns the integer `count`** so the auto-pilot can decide. `cmd_fetch_restore` captures that count and suppresses its `✅✅✅` banner (printing a `⚠️` 0-items summary) only when `is_season_map and episode_range and restored_count == 0`; single-item and no-range runs keep the original banner unchanged. `resolve_targets` adds the same diagnostic `⚠️` before its unchanged empty-list return (the fetch entry point still prints "No valid targets found" downstream). The post-#27 logged-out `SessionExpiredError`/`check_session_alive` path was NOT touched and is kept textually distinct from the range-guard message.
- Key decisions:
  - **Confirmed pre-/post-filter variable names** (read each function before editing): `cmd_push_group` — pre-filter list `target_ids`, post-filter `filtered_ids`, range `episode_range`, base `group_id`; `cmd_restore_group` — pre-filter `target_ids`, post-filter `filtered`, range `episode_range`, base `group_id`; `cmd_fetch_restore` — calls `cmd_restore_group(manual_id, episode_range)` at the season_map branch, banner at the tail; `resolve_targets` — pre-filter `children_ids`, post-filter `filtered`, range `ep_range`, base `manual_id`.
  - **`cmd_restore_group` return-contract change (None → int `count`)** — required so `cmd_fetch_restore` can thread the count. **Grepped `cmd_restore_group(` across the repo:** live callers are `main.py:2872` (inside `cmd_fetch_restore`, now uses the return) and the CLI dispatcher `main.py:3066` (`cmd_restore_group(sys.argv[2])` — statement, return ignored); the only other matches are the two smoke tests (return ignored) and the dead `archive/` copies. No caller depends on the old None return, so the change is safe.
  - **Banner-suppression signal = `restored_count == 0`** (per the plan/DECISION 3, "don't lie with a green banner over zero work"). This means a range that *selected* items but restored 0 (e.g. files missing) ALSO suppresses the banner — that is the intended/defensible behavior (0 files restored is 0 work). Consequence the test had to honor: the "real selection keeps banner" assertion (b) must actually restore > 0 files, so `test_fetch_restore_real_range_keeps_banner` seeds `restore/` copies for the two selected episodes (the `test_restore_group` pattern) → `count == 2` → banner prints.
  - **Sample/count captured BEFORE the de-alias reassignment** in `cmd_push_group`/`cmd_restore_group` (the de-alias loop reassigns `target_ids = dealiased` after the filter), so the warning reports the true pre-filter child id and count.
  - **Exit codes unchanged everywhere** — every touched function still returns None on the auto-pilot path / the same truthy/None contract; only `cmd_restore_group`'s Python return changed (internal call-site only, not a CLI exit code).
  - **Scoped to step 4** — did NOT add the cross-command `sSSEE` *selection* assertions (push_group/restore/fetch selects-2); the step text reserves those for step 6. This step's tests prove only the banner/warning behavior (a)/(b)/(c).
- Verification:
  - New banner tests: `python -m pytest "tests/smoke/test_smoke_all_commands.py::TestEachCommand::test_fetch_restore_empty_range_suppresses_banner" "...::test_fetch_restore_real_range_keeps_banner" -q` → `2 passed in 0.73s`.
  - SMOKE GATE (mandatory; main.py + mainfetch.py modified): `python -m pytest tests/smoke -q` → `54 passed in 15.28s` (< 30s).
  - Regression set: `python -m pytest tests/test_episode_range_filter.py tests/test_prep_season_episode_parse.py tests/test_entry_schema_guard.py -q` → `26 passed in 2.02s` (helper + season-parse + schema guard all green/unchanged).
  - Full suite: `python -m pytest -q` → `238 passed in 42.88s` (baseline 236 + 2 new smoke tests; no regressions).

---

## Step 5 — [status: done]
- Executor: executor-sonnet
- Model: sonnet
- Mode: single-executor
- Files changed: `tests/test_episode_range_filter.py` (appended 3 integration-level tests + import block for `json` and `mainfetch`; existing 13 unit tests untouched).
- Outcome: Extended `tests/test_episode_range_filter.py` with a `resolve_targets`-level integration test tier using the `sandbox` fixture. The three new tests drive `mainfetch.resolve_targets` against a seeded in-memory library (written as JSON to `sandbox["lib_anime"]` / `sandbox["lib_series"]` / `sandbox["lib_movies"]`). No real Selenium, ADB, device I/O, or browser is involved. `resolve_targets` returns a list of entry dicts (confirmed by reading `mainfetch.py:456-459`: `target_entries.append(lib[cid])`); assertions check both `len(results) == 2` and that the correct filenames appear in the result set. Tests added: (1) `test_resolve_targets_kuroko_range_2_3_selects_exactly_two` — the exact original repro: a 4-child season (s0201/s0202/s0203 + half-ep s0216.5) with `ep_range="2-3"` must return exactly 2 entries (s0202/s0203); the half-ep must be excluded; (2) `test_resolve_targets_kuroko_old_bug_range_202_203_returns_empty` — `ep_range="202-203"` must now return `[]` (old unanchored fallback would have matched 202/203, but that bug is fixed); (3) `test_resolve_targets_tv_series_separator_range_2_3` — cross-format guard: a `tv-` separator-style season (eNN children, written to `lib_series`) with `ep_range="2-3"` over 4 episodes must return exactly 2 (e02, e03). The minimal leaf helper (`_minimal_leaf`) omits fields not needed by `resolve_targets`. Full suite increased from 238 to 241. All existing tests remain green.
- Key decisions:
  - `resolve_targets` return shape confirmed by reading mainfetch.py: returns a list of **entry dicts** (the raw library entry objects, not IDs), so assertions check `r["filename"]`.
  - `mainfetch.py` imports `load_library` directly from `mvcommon` via `from mvcommon import ..., load_library`; `sandbox` patches `mvcommon.LIBRARY_*`; no additional mainfetch-specific patching is needed.
  - `_seed_libs` writes `{}` to unused lib files so `load_library` never skips a missing file; direct `json.dumps` writes used (not `save_library`) for explicit control over which library file receives which entries.
- Verification:
  - `python -m pytest tests/test_episode_range_filter.py -q` → `16 passed in 2.13s` (13 original + 3 new; all green)
  - `python -m pytest -q` → `241 passed in 46.07s` (baseline 238 + 3 new; no regressions)

---

## Step 6 — [status: done]
- Executor: executor-sonnet
- Model: claude-sonnet-4-6
- Mode: single-executor
- Files changed: `tests/smoke/test_smoke_all_commands.py` (added 2 new test methods to `TestEachCommand`).
- Outcome: Added two cross-command sSSEE selection-count assertions to the smoke suite, reusing the existing `_seed_anime_ssee_season` helper, `ANIME_SEASON_ID`, `ANIME_EP_IDS` constants, and `_seed_anime_restore_copies` helper from step 4. No new helpers or constants were added. The two new tests: (1) `test_push_group_episode_range_anime_ssee` — seeds via `_seed_anime_ssee_season(uploaded=False)`, calls `main.cmd_push_group(ANIME_SEASON_ID, episode_range="2-3")`, asserts `uploaded=True` for s0202/s0203 and `uploaded=False` for s0201. Uses `mock_device` fixture. (2) `test_restore_group_episode_range_anime_ssee` — seeds via `_seed_anime_ssee_season(uploaded=True)`, seeds restore/ copies for ep02/ep03 via `_seed_anime_restore_copies`, calls `main.cmd_restore_group(ANIME_SEASON_ID, "2-3")`, asserts `status=="restored_local"` for s0202/s0203 and status not restored for s0201, and asserts return value `== 2`. No `mock_device` needed (restore_group is local FS only). Both new tests inserted after the existing step-4 banner tests (before `test_fetch_round_trip_with_mock_fetch`). Full suite grew from 241 to 243 (the 2 new tests); all existing tests stayed green.
- Key decisions:
  - Invocation pattern taken verbatim from `test_push_group_episode_range` (line 350) for push and `test_restore_group` (line 420) / `_seed_anime_restore_copies` (step 4) for restore.
  - `cmd_restore_group` returns the integer count (changed in step 4); the test asserts `count == 2` as a secondary check alongside the per-entry status assertions.
  - `test_restore_group_episode_range_anime_ssee` does NOT seed restore copies for ep01 (`info["ep_ids"][0]`), only for ep02/ep03 (`info["ep_ids"][1:]`), matching the real scenario where the range selects 2-3 only.
  - `mock_device` not needed for restore_group (no ADB calls); the push_group test needs it (ADB push).
- Verification:
  - `python -m pytest tests/smoke -q` → `56 passed in 45.31s` (54 before + 2 new; all green)
  - `python -m pytest -q` → `243 passed in 61.70s` (241 before + 2 new; no regressions; new baseline 243)

---

## Step 7 — [status: done]
- Executor: executor-haiku
- Model: haiku
- Files changed: `improvements/improvements_tierC.md` (line 343, IMP-C18 Status field only)
- Outcome: Changed line 343 of the IMP-C18 block in `improvements/improvements_tierC.md` from `- Status: pending` to `- Status: done (fix/imp_c18_episode_range — shared mvcommon.episode_num_from_id (prefix-strip + anchored ^[eExX]?(\d+(?:\.\d+)?)$) routes all 5 range-filter sites so glued sSSEE ids like …-s0202 parse to episode 2 not 202; 0-via-range now warns + suppresses the false ✅✅✅ auto-pilot banner; tests in tests/test_episode_range_filter.py + smoke sSSEE cases)`. This is a pure documentation edit (tier-file bookkeeping). No other task block was modified; all other IMP-C blocks remain unchanged. The resolution note follows the existing style of sibling done tasks (branch name + short summary of what was changed).
- Key decisions: None.
- Verification: Confirmed via `grep "^- Status:" improvements/improvements_tierC.md` that IMP-C18 line 343 is now `done` with the full note and no other Status lines changed. File still reads correctly; only the single Status field was edited.

---

## Step 8 — [status: done]
- Executor: executor-sonnet
- Model: sonnet
- Mode: single-executor
- Files changed: `improvements/PRIORITY.md`, `docs/priority-graph/priority-graph.html`
- Outcome: Updated both priority tracking files in one step per the maintenance protocol. Four changes in PRIORITY.md: (1) Removed the IMP-C18 row from the Band 0 table — Band 0 now retains only the two 🚦 decision-gated items IMP-R6 and IMP-R7. (2) Bumped Last updated to 2026-06-15 with a note that C18 is done and Band 0 is now only the R6/R7 decisions. (3) Rewrote the 👉 SUGGESTED NEXT TASK block to point at IMP-A10 (truth-up requirements.txt) with its rationale, retaining the 🚦 R6/R7 note. (4) Added C18 to the ✅ DONE list and bumped the count from (19) to (20) with the compact-style entry: `C18 (anime sSSEE episode-range filter: shared episode_num_from_id + 0-match guard)`. In `priority-graph.html`, the C18 TASKS array entry was updated from `"crit","todo"` to `"done","done"` and the note field updated to a one-line resolution matching the fix branch and outcome. The two files now agree: C18 is done in both; NEXT points to A10; last-updated is 2026-06-15.
- Key decisions: None. All changes are purely documentation/tracker updates following the established maintenance protocol.
- Verification: Read back both files' edited regions after each edit. PRIORITY.md: Band 0 table has 2 rows (R6, R7 only); NEXT header reads IMP-A10; DONE count is (20) with C18 present; Last updated is 2026-06-15. priority-graph.html line 162: C18 node is `["C18","anime range-filter bug","C","done","done","Fixed fix/imp_c18_episode_range: ..."]` — valid JS array entry with correct quoting, no trailing-comma breakage, sibling C16/C17 node format matches.

## Step 9 — [status: done]
- Agent: architect
- Model: opus
- Mode: docs-only (read code to ground wording; no code edited)
- Files changed: `ARCHITECTURE.md`, `README.md` (+ `PLAN.md` step-9 checkbox, this `STATUS.md` entry)
- Outcome:
  - **ARCHITECTURE.md — `episodes`-keyword callout (§5, ~line 256):** Added two new blockquote notes after the existing parsing note: (1) a season-aware range-filter note naming all five sites (`mainfetch.resolve_targets`, `cmd_push_group`, `cmd_restore_group`, `cmd_prep_push_rep_season`, `_season_resume_cmd`) and the single shared `mvcommon.episode_num_from_id(child_id, base_id)` (prefix-strip + anchored `^[eExX]?(\d+(?:\.\d+)?)$`), with the `…-s0202` → ep 2 vs old-fallback ep 202 contrast and the dead `episodes 202-203` workaround; (2) a 0-match-guard note (⚠️ + suppressed `✅✅✅` banner, run continues, no non-zero exit, `cmd_restore_group` returns int count).
  - **ARCHITECTURE.md — §6.2 "Kuroko's Basketball" anime bullet (~line 339):** Added a season-aware sentence: the base `…-s02` is stripped before reading the episode number so `episodes 2-3` selects the glued `…-s0202`/`…-s0203` children (old fallback read `0202` as 202).
  - **ARCHITECTURE.md — `cmd_restore_group` (~line 1044):** Rewrote the episode-filter mechanics from implicit to the shared helper, added the ⚠️ 0-match behavior and the new int-return contract feeding the auto-pilot.
  - **ARCHITECTURE.md — `cmd_push_group` (~line 1074):** Added that the range filter reads via the shared helper, so glued anime ids filter like restore.
  - **ARCHITECTURE.md — `cmd_prep_push_rep_season` (~line 1095):** Noted the `episodes A-B` filter resolves via the shared helper.
  - **ARCHITECTURE.md — `cmd_fetch_restore` function-index entry (~line 1101):** Replaced the OLD stale line range `(1367-1391)` with the real location `main.py:2879` (the optional fix — it was trivially in path) and documented the 0-match guard (suppressed banner + ⚠️ summary at `main.py:2910`, exit code unchanged).
  - **ARCHITECTURE.md — §8.6 `resolve_targets` (~line 1275):** Replaced the OLD unanchored regex ladder (`[eE](…)$` → `x(…)$` → trailing digits) with the shared-helper prefix-strip + anchored regex, called out the 202-bug arm, and documented the ⚠️ 0-match diagnostic + unchanged `[]` return contract.
  - **README.md — range-fetch section (~line 230):** Added a season-aware blockquote with the verified repro example `python main.py fetch_restore ani-ja-2013-kurokosbasketball-s02 episodes 2-3` (selects episodes 2 and 3), stated the old `episodes 202-203` glued-number workaround relied on the bug and no longer works, and noted the ⚠️/suppressed-banner 0-match behavior.
- Optional stale line-index fix: DONE — `cmd_fetch_restore` index entry corrected from `(1367-1391)` to `main.py:2879` (verified actual location via Read: `def cmd_fetch_restore` is at `main.py:2879`; `cmd_dispatch_fetch` is the one near 2864).
- README `202-203` workaround: Grep confirmed the README never documented `202-203` (only the tier file did, which is another step's scope and untouched). The README addition proactively states the workaround is dead.
- Verification: Re-read each edited region; markdown/blockquote structure intact, no unrelated reflow. Key new sentences:
  - ARCHITECTURE: "All five range-filter sites … read each child's episode number through the single shared helper `mvcommon.episode_num_from_id(child_id, base_id)`. It strips `base_id` as a prefix first, THEN matches an anchored `^[eExX]?(\d+(?:\.\d+)?)$`, so for a glued anime season id like `ani-ja-2013-kurokosbasketball-s0202` (base `…-s02`) the leftover `02` reads as episode 2 and `episodes 2-3` correctly selects episodes 2-3."
  - ARCHITECTURE: "When an `episodes <range>` selects 0 items from a NON-EMPTY season, the tools print a `⚠️` warning … and the `cmd_fetch_restore` auto-pilot SUPPRESSES the green `✅✅✅ FETCH & RESTORE COMPLETE.` banner … The run continues: no error, no non-zero exit."
  - README: "`python main.py fetch_restore ani-ja-2013-kurokosbasketball-s02 episodes 2-3` selects episodes 2 and 3 (the `…-s0202`/`…-s0203` children). … That workaround relied on the bug and no longer works; use the real episode numbers."
