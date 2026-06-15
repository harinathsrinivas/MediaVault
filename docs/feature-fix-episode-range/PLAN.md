# Task: IMP-C18 — fix anime `sSSEE` episode-range filter (silent 0-match) via one shared `mvcommon` extractor + loud 0-match guard

Suggested branch: fix/imp_c18_episode_range

> **Reconciliation (2026-06-15):** re-verified against `main` @ `ea5bb32` (PR #27 — IMP-C17
> fetch-session keep-alive — merged after this plan was drafted). #27 touched `mainfetch.py`,
> `mvcommon.py`, `main.py`'s area, the smoke suite, `ARCHITECTURE.md`, `README.md`, `PRIORITY.md`,
> the priority graph, and `improvements_tierC.md`. ALL code/doc/test/tracker line references below
> were re-confirmed exact (this plan was authored against post-#27 content) — **no drift from the
> merge.** The only adjustments made on re-check are the two clarifying notes in steps 4 and 9
> (the new logged-out detector now sits next to `resolve_targets`; ARCHITECTURE has deeper
> function-docs to refresh). No new decisions.

## Context
`episodes N-M` on an anime season whose child IDs glue season+episode with no `e`/`x` separator
(e.g. `ani-ja-2013-kurokosbasketball-s0202` = season 02, episode 02) silently filters to **0**
in BOTH the fetch path and the restore path, yet the auto-pilot still prints
`✅✅✅ FETCH & RESTORE COMPLETE.` over zero files — the worst kind of bug (wrong result, no
error, no non-zero exit). Root cause: a regex ladder that falls through to an unanchored
fallback `re.search(r'(\d+(?:\.\d+)?)$', child_id)` which captures the WHOLE trailing digit run
(`…-s0202` → `0202` → `202.0`), so `[2,3]` matches nothing. A CORRECT reference already exists at
`main.py:2707` (`cmd_prep_push_rep_season`) and `main.py:2741` (`_season_resume_cmd`): strip the
base id first (`mid.replace(base_id, "")`), then anchor `^[eExX]?(\d+(?:\.\d+)?)$`. The fix
extracts that correct logic into ONE shared `mvcommon` helper, routes all 5 sites through it
(3 broken + 2 already-correct, eliminating the drift surface), and adds a loud guard so a
range that yields 0 from a non-empty children list warns and suppresses the green success banner.

## Goal
- `fetch` / `fetch_restore` / batch restore / `push_group` with `episodes 2-3` on an `sSSEE`
  anime season (child IDs like `ani-…-s0202`, `…-s0203`) selects **exactly** episodes 2 and 3.
- Separator-style IDs (TV `…s03e20`, anime `…16x05.5`, `eNN`/`xNN`) keep parsing correctly.
- Half-episodes parse to their float (`…-s0216.5` → `16.5`).
- A range that selects 0 items from a NON-EMPTY children list prints a `⚠️` warning naming the
  parsed range and a sample child id, CONTINUES (no error, no non-zero exit), and does NOT print
  the `✅✅✅ … COMPLETE` banner. A genuinely empty result is informative, not a crash.
- One shared extractor `mvcommon.episode_num_from_id(child_id, base_id) -> float | None` is the
  single implementation; all 5 range-filter sites call it; zero duplicated regex ladders remain.
- No schema change: `ENTRY_TYPE_KEYS` and `tests/test_entry_schema_guard.py` stay green untouched.
- `python -m pytest tests/smoke -q` (the cross-command gate) stays green.

## Files affected
- `mvcommon.py` — NEW shared helper `episode_num_from_id(child_id, base_id)` (already imports `re`).
- `main.py` — import the helper from `mvcommon`; swap 3 filter sites onto it (`cmd_push_group`
  ~1774, `cmd_restore_group` ~2395, plus retrofit `cmd_prep_push_rep_season` ~2707 and the
  `_season_resume_cmd` helper ~2741); add the 0-match guard at `cmd_push_group` /
  `cmd_restore_group` and suppress the auto-pilot banner in `cmd_fetch_restore` (~2888).
- `mainfetch.py` — add the helper to its `from mvcommon import (...)` line; swap `resolve_targets`
  (~427) onto it; add the 0-match guard (warn + the fetch path already prints "No valid targets").
- `tests/test_episode_range_filter.py` — NEW: unit tests for the helper + a `resolve_targets`-level
  test seeding a kuroko `sSSEE` season via the boundary-injection pattern.
- `tests/smoke/test_smoke_all_commands.py` — NEW `sSSEE` season fixture + cross-command assertions
  (`episodes 2-3` selects 2 across fetch + restore + push_group; a real empty range warns + no banner).
- `improvements/improvements_tierC.md` — mark IMP-C18 `done`.
- `improvements/PRIORITY.md` — clear IMP-C18 from Band 0; refresh `👉 SUGGESTED NEXT TASK` + Last-updated.
- `docs/priority-graph/priority-graph.html` — flip C18 node status `crit/todo` → `done/done`.
- `ARCHITECTURE.md` — note the `episodes` keyword / §6.2 ID-shape filter is now season-aware
  (documented behavior change).
- `README.md` — add/refresh range-fetch examples incl. the `sSSEE` anime case (behavior change).

## Approach
1. Write ONE correct extractor in `mvcommon` modeled exactly on the working `main.py:2707`
   pattern (prefix-strip, then anchored `^[eExX]?(\d+(?:\.\d+)?)$`), returning `float | None`.
   It is pure (stdlib `re` only), so it is unit-testable with no fixtures.
2. Route every range-filter site through it. The 3 broken sites change semantics (now correct);
   the 2 already-correct sites are refactors that must leave behavior identical (their existing
   tests pin that). After this step there is a single source of truth and zero drift surface.
3. Add the 0-match guard. The signal that distinguishes "buggy silent no-op" from "genuinely empty
   range" is: a NON-EMPTY children/target list reduced to 0 by a range filter. At each filter site,
   detect that case, print a `⚠️` warning (parsed range + a sample child id), and set a flag the
   command uses to skip its success banner. `cmd_fetch_restore`'s `✅✅✅` banner is suppressed when
   the run processed 0 items via a range.
4. Add tests at three levels: the pure helper (table of ID shapes), a `resolve_targets`-level test
   (kuroko `sSSEE` season, boundary-injection — no Selenium/ADB/FS), and the cross-command smoke.
5. Update docs + trackers; run the smoke gate last.

This is a bug fix with a known root cause and an in-tree correct reference, so every step is
single-executor — no multi-candidate steps (see guardrails: known-cause bug fixes and refactors
following an existing pattern do NOT qualify).

## Steps

- [x] 1. [model: opus] [effort: high] Add the shared extractor `episode_num_from_id` to `mvcommon.py` (+ exhaustive unit tests).
  - Files: `mvcommon.py`, `tests/test_episode_range_filter.py` (new file; helper unit tests live here).
  - Details: Add a module-level function in `mvcommon.py` (it already `import re` at line 5), placed
    near `parse_size_str` in the UTILITIES section:
    ```python
    def episode_num_from_id(child_id, base_id):
        """Episode number from a library child ID, season-aware.

        Strips base_id as a prefix first (so glued anime ids like
        '…-s0202' with base '…-s02' leave '02' = episode 2, NOT 0202=202),
        then matches an optional e/E/x/X separator + number at end-of-string.
        Returns a float (handles half-eps like 16.5) or None if unparseable.
        """
        if base_id and child_id.startswith(base_id):
            ep_str = child_id[len(base_id):]
        else:
            ep_str = child_id            # base not a prefix -> parse the whole id
        m = re.search(r'^[eExX]?(\d+(?:\.\d+)?)$', ep_str)
        return float(m.group(1)) if m else None
    ```
    IMPORTANT — use the user-decided regex `^[eExX]?(\d+(?:\.\d+)?)$` (NOT the `[eExXsS]?` the
    ticket text floated): the prefix-strip already removes the `…-s02` season segment, so the
    leftover is `02`/`e20`/`16x05.5`-tail and the separator class must be only `e/E/x/X`. Use
    slicing `child_id[len(base_id):]` (DECISION 4), not `str.replace`, so an accidental mid-string
    occurrence of base_id can't be removed twice. `base_id` may be falsy/None → treat as
    "not a prefix" and parse the whole id.
    In the new test file `tests/test_episode_range_filter.py`, write unit tests (`import mvcommon`)
    covering every required shape:
    - glued `sSSEE`: `episode_num_from_id("ani-ja-2013-kurokosbasketball-s0202",
      "ani-ja-2013-kurokosbasketball-s02") == 2.0`
    - half-ep glued: `…-s0216.5` with base `…-s02` → `16.5`
    - separator series: `…-s03e20` with base `…-s03` → `20.0`
    - separator anime `x`: a child `…16x05.5` with the matching base → `5.5`
    - bare `eNN` with base stripped to `e20` → `20.0` (the `e` separator after strip)
    - base-not-a-prefix fallback: `episode_num_from_id("ani-ja-2006-deathnote07",
      "wrong-base") == 7.0` (parses whole id) AND with the correct base `ani-ja-2006-deathnote` → `7.0`
    - no-match → None: `episode_num_from_id("tv-en-2016-strangerthings-s01", base) is None`
      (parent id, empty leftover) and a junk id like `"foo"` with base `"foo"` → None
    - `base_id=None` and `base_id=""` both parse the whole id (no crash)
    Header docstring MUST include: "Never touch real C:\\Media files or real library_*.json."
    and "Run `python -m pytest` and fix failures before marking the step done."
  - Acceptance: `python -m pytest tests/test_episode_range_filter.py -q` passes; helper is pure
    (no I/O), returns `float | None`. opus because the regex/prefix-strip boundary semantics
    (glued vs separator vs half-ep vs fallback) are the load-bearing correctness core of the fix.

- [x] 2. [model: sonnet] [effort: medium] Swap the 3 BROKEN filter sites onto the shared helper.
  - Files: `mainfetch.py` (`resolve_targets`, ~427-428), `main.py` (`cmd_push_group`, ~1774-1775;
    `cmd_restore_group`, ~2395-2396).
  - Details:
    - `mainfetch.py`: add `episode_num_from_id` to the existing
      `from mvcommon import RESTORE_DIR_NAME, load_library, calculate_file_hash, fetch_session_lock`
      line (mainfetch.py:29). In `resolve_targets`, the base id is the season-map `manual_id`
      (in scope). Replace the two-line regex ladder (the `[eE](\d+)$ or x(\d+)$` then unanchored
      fallback) with:
      ```python
      ep = episode_num_from_id(child_id, manual_id)
      if ep is not None and s <= ep <= e:
          filtered.append(child_id)
      ```
    - `main.py`: add `episode_num_from_id` to the `from mvcommon import (...)` block (main.py:26-31).
      In `cmd_push_group` (~1771-1782) the season/group id is `group_id` (in scope); replace the
      ladder with `ep = episode_num_from_id(mid, group_id)` then the `start <= ep <= end` check
      (guard `ep is not None`). This is the site the ticket originally MISSED — it must be fixed.
      In `cmd_restore_group` (~2394-2401) the season/group id is `group_id` (in scope); same swap.
    - Do NOT change the `map(float, ...split('-'))` range parse, the `try/except` arms, the
      "Filtered to N" prints, or the de-alias loops below each filter — those stay as-is (the
      0-match guard is step 4). Keep edits surgical.
    - Note: in prefix-match mode (no season_map), `group_id` is still the right base to strip
      (children share that prefix); for a single non-season id there is no range filtering.
  - Acceptance: `python -m pytest tests/test_prep_season_episode_parse.py -q` still green; the
    three ladders are gone; `grep -n "(\\\\d+(?:\\\\.\\\\d+)?)\$" main.py mainfetch.py` shows no
    remaining unanchored episode fallback in these functions. (Full behavior pinned in steps 5-6.)

- [x] 3. [model: sonnet] [effort: medium] Retrofit the 2 ALREADY-CORRECT sites onto the shared helper (pure refactor).
  - Files: `main.py` (`cmd_prep_push_rep_season` filter ~2704-2711; `_season_resume_cmd` ~2738-2743).
  - Details: Both sites currently inline `ep_str = mid.replace(base_id, "")` then
    `re.search(r'^[eExX]?(\d+(?:\.\d+)?)$', ep_str)`. Replace each with a call to
    `episode_num_from_id`:
    - `cmd_prep_push_rep_season` (~2704): `base_id` is in scope. Use
      `ep_num = episode_num_from_id(mid, base_id)` then `if ep_num is not None and start <= ep_num <= end:`.
    - `_season_resume_cmd` (~2738): it currently extracts `m.group(1)` (a STRING) and appends to
      `ep_nums` to rebuild `episodes {first}-{last}`. The helper returns a float, which would change
      the resume string formatting (e.g. `2.0` instead of `2`). Preserve the exact current string
      output: convert back without a trailing `.0` for whole numbers, e.g.
      `ep = episode_num_from_id(real_id, base_id); if ep is not None: ep_nums.append(str(int(ep)) if ep == int(ep) else str(ep))`.
      Verify the produced resume command matches the pre-refactor format. (Note: the helper strips
      via slicing, the old code used `.replace`; for these well-formed `base_id` + child shapes the
      leftover is identical — confirm with the existing tests.)
    - This step must NOT change behavior — it removes the last two regex copies so the helper is the
      single source of truth.
  - Acceptance: `python -m pytest tests/test_prep_season_episode_parse.py -q` green (Tests D/E and
    the alias/range tests still pass unchanged); no `re.search(r'^[eExX]?...` episode literal remains
    anywhere in `main.py` (`grep -n "eExX" main.py` → only inside `mvcommon` import usage / none in
    these two functions).

- [x] 4. [model: opus] [effort: high] Implement the 0-match guard (warn + suppress success banner) at the filter sites and the auto-pilot banner.
  - Files: `main.py` (`cmd_push_group` ~1782/1798, `cmd_restore_group` ~2402/2423, `cmd_fetch_restore`
    ~2880-2888), `mainfetch.py` (`resolve_targets` ~432-433 / its caller's "No valid targets" print).
  - Details: The distinguishing signal is **a NON-EMPTY children/target list reduced to 0 by a range
    filter**. Implement:
    - `cmd_push_group`: after the filter, if `episode_range` was given AND `target_ids` (pre-filter)
      was non-empty AND `filtered_ids` is empty, print a warning naming the parsed range and a sample
      pre-filter child id, e.g.
      `⚠️ Range {episode_range} matched 0 of {N} episodes (e.g. id '{sample}'). Nothing to push — check the range vs the season's episode numbers.`
      Then proceed (existing `if not target_ids: print("❌ No items found to push."); return` already
      stops cleanly with no success banner — `cmd_push_group` has no `✅✅✅` banner, so warning is the
      whole job here).
    - `cmd_restore_group`: same warning after the filter when range-given + non-empty-pre-filter +
      empty-post-filter. `cmd_restore_group` ends with `=== Batch Restore Complete: {count} files
      restored. ===`. Change this so that when 0 files were restored due to a range filtering
      everything out, it prints the `⚠️` line instead of (or clearly in addition to, without the
      celebratory framing) the "Complete" line. Keep it returning normally (no exception, no exit
      code). Have `cmd_restore_group` return its `count` (currently returns None) so `cmd_fetch_restore`
      can decide on the banner. Confirm no other caller depends on the old None return (grep
      `cmd_restore_group(`).
    - `cmd_fetch_restore` (~2864): this is the auto-pilot that unconditionally prints
      `✅✅✅ FETCH & RESTORE COMPLETE.` at line 2888. Make the banner conditional: when the entry is a
      season_map AND a range was supplied AND `cmd_restore_group` reported 0 restored, suppress the
      `✅✅✅` banner and instead print a `⚠️` summary (e.g.
      `⚠️ FETCH & RESTORE finished with 0 items (range {episode_range} selected nothing).`). For a
      single item or a non-range run, keep the existing banner. Do NOT change exit codes (the function
      returns None throughout; keep it that way) — "continue the run, just don't lie with a green
      banner" per DECISION 3.
    - `mainfetch.resolve_targets`: when range-given + non-empty children + empty filtered, print the
      same `⚠️` warning (range + sample child id) before returning the empty list. The fetch entry
      point already prints "No valid targets found" downstream; the warning ADDS the diagnostic
      (which range, which id shape) so a user sees WHY 0 matched. Do not raise / do not change the
      empty-list return contract. Verified anchors on current `main`: `resolve_targets` is at
      `mainfetch.py:409` (signature `resolve_targets(manual_id, ep_range=None)`; the buggy fallback
      ladder is at `mainfetch.py:428`), and `❌ No valid targets found.` is at `mainfetch.py:516`.
      NOTE (post-#27): `mainfetch.py` now ALSO has logged-out detection (`SessionExpiredError` /
      `check_session_alive`, ~lines 99-143 / 202-233) in the trigger/download path — that is a
      DISTINCT failure mode (session expired) from this 0-match range guard (range matched nothing
      from a real list). Keep the two separate: the `⚠️` range-guard text must NOT be confused with
      or routed through the logged-out remediation message, and this step must NOT touch the
      session-alive code.
    - A genuinely empty children list (season with no episodes) is NOT a range failure — only warn
      when the PRE-filter list was non-empty (DECISION 3: empty range is informative, not a failure;
      the warning must specifically flag the "you asked for a range and got nothing from a real list").
    - Tests (add to `tests/test_episode_range_filter.py` and/or the smoke file in step 6): assert the
      `⚠️` warning fires and the green `✅✅✅` banner is SUPPRESSED on a 0-via-range run, while a real
      selection (`episodes 2-3` over a kuroko season) still prints the banner. Use `capsys`.
  - Acceptance: `python -m pytest tests/test_episode_range_filter.py -q` green; a unit/command test
    proves (a) warning present + no `✅✅✅` on 0-via-range, (b) `✅✅✅` still present on a real
    selection, (c) no exception / no `sys.exit` on the empty-range path. opus: the banner-suppression
    threads state across `cmd_fetch_restore` → `cmd_restore_group` and must not change exit-code/return
    contracts other callers rely on — the kind of cross-cutting control-flow change that warrants max
    deliberation.

- [x] 5. [model: sonnet] [effort: medium] New `tests/test_episode_range_filter.py` — helper table + a `resolve_targets`-level `sSSEE` test.
  - Files: `tests/test_episode_range_filter.py` (extend the file created in step 1; this step adds the
    integration-level test). Read `docs/testing-strategy.md` first to confirm fixture choice.
  - Details: In addition to the step-1 helper unit tests, add a `resolve_targets`-level test that
    seeds a kuroko `sSSEE` season and asserts `episodes 2-3` selects exactly the 2 episodes, using the
    EXISTING boundary-injection / monkeypatch pattern from
    `tests/smoke/test_smoke_all_commands.py:444-494` (`test_fetch_route_logged_out_aborts`) — i.e.
    drive `mainfetch.resolve_targets` against a seeded library via the `sandbox` fixture, with NO real
    Selenium/ADB/FS. Concretely:
    - Use the `sandbox` fixture (library I/O). Seed a season_map `ani-ja-2013-kurokosbasketball-s02`
      with children `…-s0201`, `…-s0202`, `…-s0203`, `…-s0204` (plus one half-ep `…-s0216.5` to prove
      it is excluded by `2-3`), each a minimal leaf entry, via `mvcommon.save_library` /
      `_write_all_libs`-style seeding (all three lib files written).
    - Call `mainfetch.resolve_targets("ani-ja-2013-kurokosbasketball-s02", ep_range="2-3")` and assert
      it returns exactly the entries for `…-s0202` and `…-s0203` (length 2). This is the EXACT original
      repro shape and is the regression pin for the bug.
    - Add a complementary assertion that `ep_range="202-203"` now returns `[]` (the old bug-reliant
      behavior is correctly gone — it should warn + return empty), and that a separator season
      (`tv-…-s03` children `…-s03e01..e04`) with `episodes 2-3` still selects 2 (cross-format guard).
    - Header docstring MUST include: "Never touch real C:\\Media files or real library_*.json." and
      "Run `python -m pytest` and fix failures before marking the step done."
  - Acceptance: `python -m pytest tests/test_episode_range_filter.py -q` passes; the kuroko `2-3` →
    2-entries assertion and the `202-203` → empty assertion both hold; no real device/browser/FS
    touched.

- [x] 6. [model: sonnet] [effort: medium] Extend the smoke suite with an `sSSEE` season fixture (fetch + restore + push_group).
  - Files: `tests/smoke/test_smoke_all_commands.py`.
  - Details: Add a small seeding helper mirroring `_seed_season_two` (lines 125-144) but for the glued
    anime shape — e.g. `_seed_anime_ssee_season(sandbox, make_video)` creating season_map
    `ani-ja-2013-kurokosbasketball-s02` with children `…-s0201`, `…-s0202`, `…-s0203` (3 leaf episodes,
    real >200KB files via `make_video`, all three lib files written via `_write_all_libs`). Add tests
    asserting `episodes 2-3` selects exactly 2 across the commands, reusing existing stubs
    (`mock_device`, `capsys`) and the established patterns from `test_push_group_episode_range`
    (line 314) and `test_restore_group` (line 384):
    - `push_group`: `main.cmd_push_group(season_id, episode_range="2-3")` → exactly `…-s0202` and
      `…-s0203` flip `uploaded=True`; `…-s0201` stays `uploaded=False`.
    - restore: seed `restore/` copies for `…-s0202`/`…-s0203`, run `main.cmd_restore_group(season_id,
      "2-3")` → exactly those two reach `restored_local`.
    - fetch: drive `main.cmd_fetch_restore(season_id, "2-3")` under `mock_device` (the existing
      no-op-subprocess fetch path) and assert no crash + the `✅✅✅` banner present for the real
      2-item selection.
    - ALSO add a 0-via-range smoke: `main.cmd_fetch_restore(season_id, "98-99")` → asserts the `⚠️`
      warning fires and the `✅✅✅` banner is ABSENT (the guard from step 4), no exception.
    - Keep each test asserting only "no crash + correct top-level effect" per the smoke suite's stated
      philosophy (file header lines 1-50). Never touch real C:\\Media / library_*.json; use rglob+.name
      not bracketed-id globs (anti-patterns, header line 47-49).
  - Acceptance: `python -m pytest tests/smoke -q` green and completes in < ~30s; the new `sSSEE`
    selects-2 assertions and the 0-via-range suppressed-banner assertion all pass.

- [x] 7. [model: haiku] [effort: low] Mark IMP-C18 done in the tier file.
  - Files: `improvements/improvements_tierC.md`.
  - Details: In the `## IMP-C18:` block (line ~327), change `- Status: pending` → `- Status: done`.
    Optionally append a one-line resolution note in the same style as sibling done tasks (e.g.
    "Fixed fix/imp_c18_episode_range: shared `mvcommon.episode_num_from_id` (prefix-strip + anchored
    `^[eExX]?(\d+(?:\.\d+)?)$`) routes all 5 range-filter sites; 0-via-range warns + suppresses the
    auto-pilot banner."). Do not edit any other task block.
  - Acceptance: `grep -n "Status: done" improvements/improvements_tierC.md` includes the IMP-C18 block;
    no other tier task changed.

- [x] 8. [model: sonnet] [effort: medium] Update PRIORITY.md AND the priority graph together (Band 0 cleared; refresh NEXT pointer).
  - Files: `improvements/PRIORITY.md`, `docs/priority-graph/priority-graph.html`.
  - Details: Per the maintenance protocol at the bottom of PRIORITY.md, update BOTH in the same step:
    - `PRIORITY.md`: (1) remove the IMP-C18 row from the Band 0 table (lines ~30-36) — Band 0 then
      retains only the two 🚦 decision-gated items IMP-R6 / IMP-R7. (2) Bump **Last updated** (line 12).
      (3) Rewrite the `👉 SUGGESTED NEXT TASK` block (lines 16-26): the next actionable (non-🚦,
      non-decision) task after C18 clears is **IMP-A10** (truth-up `requirements.txt` — Band 1, low
      risk, "a clean install is half-broken today"). Point the NEXT arrow at IMP-A10, keep the note
      that IMP-R6/R7 still await user decisions. (4) Move IMP-C18 into the ✅ DONE list (line ~82-89)
      and bump the done count (19 → 20). Re-read PRIORITY.md before editing to confirm A10 is still the
      right next pointer.
    - `priority-graph.html`: in the `TASKS` array, change the C18 node (line 162) from
      `["C18","anime range-filter bug","C","crit","todo", …]` to `…,"done","done", …` and update its
      note to a one-line resolution (mirror the tier-file note). No new EDGES needed (C18 has no
      dependents). The graph and PRIORITY.md must agree.
  - Acceptance: IMP-C18 no longer in Band 0 and present in ✅ DONE in PRIORITY.md; the NEXT pointer
    reads IMP-A10; the graph C18 node is `done/done`; Last-updated bumped. Open the HTML mentally —
    arrays still valid JS (no trailing-comma/quote breakage).

- [x] 9. [model: sonnet] [effort: medium] Document the behavior change in ARCHITECTURE.md and README.md.
  - Files: `ARCHITECTURE.md` (the `episodes`-keyword callout ~lines 247-255 and the §6.2 ID-shapes
    "Kuroko's Basketball" note ~lines 339-348), `README.md` (range-fetch examples ~lines 228-245).
  - Details: This is a DOCUMENTED behavior change — call it out explicitly.
    - `ARCHITECTURE.md`: add a sentence to the `episodes`-keyword note (and/or the §6.2 `sSSEE` anime
      bullet) stating the range filter is now **season-aware**: it strips the base/season id before
      reading the episode number via the shared `mvcommon.episode_num_from_id`, so `episodes 2-3` on a
      glued `ani-…-s0202` season correctly selects episodes 2-3 (previously the unanchored fallback
      read `0202` as 202 and matched nothing). Note all 5 range-filter sites share the one helper.
      ALSO refresh the deeper function-level docs that quote the OLD regex mechanics, since the
      mechanism is what changes: the `cmd_restore_group` / `cmd_push_group` episode-filter
      descriptions (~`ARCHITECTURE.md:1045`, the `\d+[xX](\d+(?:\.\d+)?)` "XxYY" note ~`1067`,
      ~`1076`, ~`1097`) and the `resolve_targets` episode-range parse note (~`1275-1278`). They
      should describe the shared `mvcommon.episode_num_from_id` prefix-strip + anchored regex, not
      the old ladder. (Pre-existing, OPTIONAL: ARCHITECTURE's function-line index at ~`1101` lists
      `cmd_fetch_restore` at `1367-1391` but it is actually at `main.py:2864` — stale before this
      work; fix only if trivially in the architect's path, otherwise out of scope.)
    - `README.md`: add a range-fetch example for the anime `sSSEE` shape, e.g.
      `python main.py fetch_restore ani-ja-2013-kurokosbasketball-s02 episodes 2-3` and state it
      selects episodes 2 and 3. If the README documented the `episodes 202-203` glued-number
      workaround anywhere, update/remove it (that workaround relied on the bug and no longer applies).
    - Keep edits surgical — do not reflow unrelated doc sections.
  - Acceptance: ARCHITECTURE.md and README.md both describe season-aware range filtering and the shared
    helper; the example uses the verified repro id; no stale "use 202-203" workaround remains.

## Risks and edge cases
- **`_season_resume_cmd` formatting drift (step 3):** the helper returns a float; the old code kept a
  string `m.group(1)`. The resume command string must stay byte-identical for whole-number episodes
  (`2` not `2.0`). Mitigated by the `int(ep)`-when-whole conversion and the existing parse tests.
- **Banner-suppression return-contract change (step 4):** making `cmd_restore_group` return `count`
  (was None) could affect a caller that treats the return as truthy. Grep `cmd_restore_group(` —
  only `cmd_fetch_restore` and the CLI dispatcher call it; the CLI ignores the return. Verify.
- **What counts as "0-via-range" vs "empty season":** only warn/suppress when the PRE-filter list was
  non-empty (DECISION 3). An empty season_map (no children) is not a range failure and must not warn.
- **Prefix-match (non-season) mode in `cmd_push_group`/`cmd_restore_group`:** when the id is not a
  season_map, children are `startswith(group_id)` matches; `group_id` is still the correct base to
  strip. A single non-season id is not range-filtered (no children list). Confirm the guard doesn't
  fire spuriously there.
- **Separator-after-strip:** for TV `…-s03e20` with base `…-s03`, the leftover is `e20` → the `[eExX]?`
  prefix in the anchored regex consumes the `e`. For glued `…-s0202` with base `…-s02`, the leftover is
  `02` → no separator, still matches. Both covered by step-1 unit tests.
- **`base_id` not actually a prefix** (data anomaly): helper falls back to parsing the whole id, which
  reproduces the OLD behavior for that one id only — acceptable and explicitly tested; it never crashes.
- **No schema change:** this is a read-time parse only; `ENTRY_TYPE_KEYS` and the entry-schema guard
  test are untouched and must stay green (invariant — see Out of scope).
- **Half-ep across formats:** anime half-eps appear as both `…16.5` and `…165.5`/glued shapes (§6.2);
  the `2-3` range excludes `16.5`, which the test pins. Ranges that intentionally include a half-ep
  (e.g. `16-17` including `16.5`) are out of scope to add but must not break (float compare handles it).

## Consumer Impact Analysis
Not required. No step adds, changes, or removes a shared data contract: no library entry type, no
library field/key, no ID shape, and no `status` value is added/renamed/removed. The change parses
existing IDs at read time and introduces one pure helper. `ENTRY_TYPE_KEYS` and
`tests/test_entry_schema_guard.py` are deliberately untouched (stated as an invariant). The only
cross-function contract touched is `cmd_restore_group`'s Python return value (None → int count),
which is an internal call-site concern handled in step 4's risk note (grep confirms only
`cmd_fetch_restore` + the CLI dispatcher call it), not a persisted/shared data shape — so no
Consumer Impact table applies.

## Verification
Run from the repo root (use `python -m pytest`, never bare `pytest`):
1. `python -m pytest tests/test_episode_range_filter.py -q` — new helper + resolve_targets `sSSEE`
   tests pass (kuroko `2-3` → 2 entries; `202-203` → empty; separator format → 2).
2. `python -m pytest tests/test_prep_season_episode_parse.py -q` — existing season-parse + range +
   alias tests STILL green (proves the step-3 retrofit changed no behavior).
3. `python -m pytest tests/test_entry_schema_guard.py -q` — green and unchanged (no schema drift).
4. `python -m pytest -q` — full suite green.
5. `python -m pytest tests/smoke -q` — **MANDATORY FINAL GATE** (cross-command). Touches `main.py`,
   `mainfetch.py`, and `mvcommon.py`, so per the SMOKE-GATE rule this is the last gate before the plan
   is done; must be green and complete in < ~30s.

Per CLAUDE.md: every code-touching step (1-6) must run `python -m pytest tests/smoke -q` green
BEFORE its commit; the git-agent commits per step. Non-trivial work runs through the multi-agent
pipeline driven from the MAIN session (orchestrator.md as a playbook — do NOT launch `orchestrator`
via `Task`, nesting depth = 1 would silently fall back to inline execution).

## Out of scope
- No JSON output mode (DECISION 6: console messaging only; no `--json` exists yet — that is IMP-A4).
- No data migration and no new stored episode-number field (DECISION 5: parse IDs at read time).
- No `ENTRY_TYPE_KEYS` / schema change (invariant — keep `test_entry_schema_guard.py` green untouched).
- No change to single-id fetch/restore, whole-season (no-range) fetch, the `map(float, range)` parse,
  the de-alias loops, or any rollback behavior (rollback is change-gated — not touched here).
- No broadening of range semantics (e.g. auto-including half-eps in integer ranges) beyond the
  current float-compare behavior.
- No refactor of adjacent code in the touched functions beyond removing the duplicated regex ladders.

---

## Open Decisions
All resolved by the user during investigation; no open items.
- **Approach = A + C** (RESOLVED): one shared `mvcommon` extractor routes every site (fixes bug +
  drift) PLUS a loud 0-match guard.
- **Scope = 3 broken + 2 correct retrofitted** (RESOLVED): single implementation, zero drift surface;
  explicitly INCLUDES `cmd_push_group` (main.py:1774), which the ticket missed.
- **0-match guard = warn + suppress success banner** (RESOLVED): non-empty children reduced to 0 by a
  range → `⚠️` (parsed range + sample child id), CONTINUE (no error / no non-zero exit), suppress the
  `✅✅✅ … COMPLETE` banner. A genuinely empty range is informative, not a failure. **Banner-suppression
  scope = the `cmd_fetch_restore` auto-pilot `✅✅✅` banner** (the only celebratory banner on the
  range-filtered path); `cmd_push_group` has no banner (warning only), `cmd_restore_group`'s
  "Batch Restore Complete" line is downgraded to the `⚠️` on a 0-via-range run.
- **Helper signature/name** (RESOLVED): `mvcommon.episode_num_from_id(child_id, base_id) -> float | None`,
  prefix-anchored strip via slicing `child_id[len(base_id):]` (else whole-id fallback), then
  `^[eExX]?(\d+(?:\.\d+)?)$`. Name confirmed as `episode_num_from_id` (the ticket's proposed name).
  Regex uses `[eExX]?` (NOT the ticket's stray `[eExXsS]?`) because the prefix-strip already removes the
  `…-s02` season segment — the separator class is only e/E/x/X.
- **No schema change** (RESOLVED): read-time parse; `ENTRY_TYPE_KEYS` + entry-schema guard untouched.
- **Console messaging only** (RESOLVED): no JSON output mode.

## Branch name
`fix/imp_c18_episode_range` (alt considered: `fix/episode_range_sSSEE`). Under 50 chars, lowercase,
type `fix`. Canonical plan folder: `docs/feature-fix-episode-range/` (lowercase-kebab, matching the
repo's existing `docs/feature-*` convention, e.g. `docs/feature-fix-episode-title-parse/`).

## PR to main
- **Title (MUST include the IMP code):**
  `Fix anime sSSEE episode-range filter: shared extractor + 0-match guard — IMP-C18`
- **Body order (per CLAUDE.md docs/git-pr-conventions.md):**
  1. The auto-generated Claude Code summary FIRST.
  2. Then a `## Original task prompt` section containing the COMPLETE verbatim initial task prompt
     for this work (the full PHASE 2 prompt that produced this plan).
  3. Then the `🤖 Generated with Claude Code` trailer.
- **Checkpoint 1 (human-gated):** merging into `main` is human-gated. Create the PR, then STOP and
  ask the user for explicit confirmation before any `gh pr merge` / merge / push to main.
- The smoke gate (`python -m pytest tests/smoke -q`) must be green before the PR is opened.

## Manual test commands
Run these to validate by hand (these touch the REAL library/Selenium/ADB — use the live anime season
that exposed the bug; no destructive writes are performed by fetch dry-runs, but a real fetch_restore
will download — run against a season you intend to restore, or stop after the "Filtered to N" line):
1. **Original failing repro — now selects 2:**
   `python main.py fetch_restore ani-ja-2013-kurokosbasketball-s02 episodes 2-3`
   → expect `🎯 Filtered to 2 episodes (2-3)` in both fetch and restore phases (was "Filtered to 0").
2. **Separator-style ID still works (regression guard):**
   `python main.py fetch <a tv season id with eNN children> episodes 1-3`
   → expect exactly episodes 1-3 selected (unchanged behavior).
3. **Half-episode exclusion:** a range that should EXCLUDE a half-ep, e.g.
   `python main.py fetch_restore ani-ja-2012-kurokosbasketball-s03 episodes 24-25`
   → expect `…-s0325.5` is NOT selected (only whole eps 24 and 25).
4. **Now-fixed push_group case (the site the ticket missed):**
   `python main.py push_group ani-ja-2013-kurokosbasketball-s02 SIZE_MB 9900 episodes 2-3`
   → expect exactly episodes 2 and 3 processed (was 0).
5. **Genuine empty range — warn + suppressed banner:**
   `python main.py fetch_restore ani-ja-2013-kurokosbasketball-s02 episodes 98-99`
   → expect a `⚠️` warning naming the range + a sample child id, and NO `✅✅✅ FETCH & RESTORE COMPLETE.`
   banner; the command exits cleanly (exit code 0, no traceback).

## Next tasks to start (after IMP-C18 clears Band 0)
Re-read `improvements/PRIORITY.md` to confirm, but the ordering after C18 is:
1. **IMP-A10** (Band 1, 🟠 low risk) — truth-up `requirements.txt` (missing `requests` /
   `webdriver-manager`); a clean install is half-broken today. The next actionable non-decision task,
   and the new `👉 SUGGESTED NEXT TASK` pointer.
2. **IMP-S1** (Band 1, 🟠 low) — stand up Jellyfin (the `JELLYFIN_SETUP_GUIDE.md` run): zero code,
   immediate couch value, can run in parallel with anything.
3. **IMP-A12** (Band 1, 🟠 low) — CI pipeline to lock the test suite so nothing merges red (pairs well
   with this PR, since IMP-C18 adds the smoke `sSSEE` case CI would enforce).
   (Band 0's remaining IMP-R6 / IMP-R7 are 🚦 decision-gated — they need a user decision before any
   code, so they are not "start next" coding tasks.)
