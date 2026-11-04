STATE: COMPLETE

# IMP-C25 Step 12 — candidate C (hybrid arbitration) — resumable status

- Worktree: `C:\Users\harin\PycharmProjects\MediaVault-c25-cand\step-12\C` · branch
  `feature/imp_c25_fetch_exact_gp_item__cand_c` · base 8968f26 (Steps 0–11 + 9b).
- Brief: scratchpad `s12_brief_C.md` (incl. its COMPLETION GATE) · contract: PLAN Step 12 · approach C: RPC-first
  per stage, automatic per-stage fallback to the web pages on RpcUnavailable / RpcShapeError, one record cache fed by
  both, drift detector on the first RPC-located object of each run (mismatch → one warning line + RPC off for the run).
- Python: `C:\Users\harin\PycharmProjects\MediaVault\.venv\Scripts\python.exe` (run from this worktree).
- Baseline here (2026-10-05, 8968f26): full **2421 passed** (415 s, three candidates in parallel) · smoke 82.

## Resume rule
Read this file, then `git log --oneline` (the WIP commits name their sub-task) and `git status`. An uncommitted
diff belongs to the first unticked sub-task: keep it when coherent and its targeted tests pass, else
`git checkout -- <its files>` (record which here). Continue at the first unticked sub-task. Commit THIS file with
every WIP commit; tick a sub-task only after its tests pass, with its SHA.

## Sub-tasks (each one WIP commit; tests named in the scenario map below)
- [x] 12C.a1 locator core in mainfetch.py — constants, per-run state `ctx.locator` (record cache + listings +
      drift), P-5 verification, stages S0a/S0b/S1/S2/S3/S3u (RPC first, per-stage web-page fallback), the S4
      preview (OD-5), the drift detector, `locate_object` + first locate tests (S0a fast path, drift once per run,
      S0b, S1 rpc + ui, S2 exact name vs the live distractors, UK normalized name, stale photo_id, RPC-gone
      overruled by the page, re-split sets) — c670b6b (11 tests)
- [x] 12C.a2 remaining locate scenarios: no-DateUTC → S2 · search_override at S3u (+ verified negative) · nothing
      verifiable → S4 preview → skip · RPC unavailable per-stage fallback · one failing stage falls back alone · drift
      mismatch → warning + RPC off · chunk set shares one S1 lookup (rpc + ui) · read-only · logged out · stand-in ·
      approaches "all" · no mutating rpcid — 97e017f (25 tests in test_locator.py)
- [x] 12C.b1 triggers: `_download_started`, `_direct_trigger` (fresh mint → `_rearm_downloads` → bounded get
      (DIRECT_PAGE_LOAD_S 15 s, restored after — coordinator 2026-10-07) → confirm; HTML / mint refused → False,
      tab back on Photos), `_shift_d_trigger` (item page + focus + Shift+D only on a page showing the item; click
      path by photo_id) — 4b41ce1 (34 tests)
- [x] 12C.m merge feature head (`git merge feature/imp_c25_fetch_exact_gp_item` = 387a5e0, which contains d714613:
      74d662d wrong-item rows + `_wrong` mark + shared `_record_failed`; 8dacbad "Most relevant" strip tiles never
      clickable, strip-only page = miss) — merge ae1da02 (clean auto-merge; no helper-name clash). Adapted: the DOM
      listing and the click path both read `_visible_result_links` → the shared `_drop_timeline`, so strip tiles are
      never candidates nor clicked; proven by 3 tests (37 tests; merged fetch suites 578 green).
      Note for 12C.b2: `_note_losses` now marks `_wrong` on a round with an unmatched arrival; for a VERIFIED direct
      download that means a transcode (P14), not a wrong pick — clear it after the direct round; book my own rows
      through `_record_failed` and run my post-S4 hook AFTER `_record_wrong_items` (never booked twice) — 312442c
- [x] 12C.b2a `_run_locator_phase` code: locate pass (+ the entry's one swbisb batch), windows of ≤3, direct round →
      harvest → Shift+D round → harvest (`_fire`/`harvest_downloads`/`_note_losses`, `_wrong` cleared after the
      direct round), `_trigger` + `_journal_sync`, OD-5 s4-skipped rows (`_record_failed` + candidates +
      expected_taken_local — 2 optional kwargs added to the merged helper), stale learned records; flows: fast-path
      season, same-second batch, wrong-account home, partly staged chunk set — dc909fa (41 tests)
- [x] 12C.b2b flows: ≤3 concurrent direct downloads (5-chunk set: 3 + 2) · minted link → HTML → Shift+D · other
      bytes → deleted → Shift+D → SHA-256, no wrong-item row, and NEW `direct_off` for the run after a
      content-verified item's link served other bytes (P14 transcode safety valve: the 300 s harvest timeout is paid
      once, not per object) · mint refused → Shift+D · Shift+D ignored on a direct load → click path — 9062b16
      (46 tests)
- [x] 12C.b2c flows: OD-5 s4-skipped row (no request, no S4; tried + candidates + expected_taken_local) · stale
      photo_id → stale learned record (the reducer marks it dead) + re-upload fetched · signed out → remediation ·
      the live wrong-click case (exact name lists another item first) fetched directly, S4 never runs — c04a02f
      (50 tests)
- [x] 12C.b3 `_record_unlocated` in `_fetch_items` AFTER `_record_wrong_items` (unlocated + still pending → failed,
      one row: ambiguous / wrong-item / not-found + tried + candidates + expected_taken_local; never booked twice),
      counters per account (`_session_stats`), one `🔎 Exact item ('<account>'): … · RPC calls: N (errors E)` line
      per session in cmd_fetch_route (only when the stages ran), kill switch → today's two attempts, no RPC —
      ce13619 (53 tests; fetch suites 335 green). (The d714613 merge the coordinator asked for is 12C.m / ae1da02;
      `git merge-base --is-ancestor d714613 HEAD` → yes.)
- [x] 12C.c1 `python mainfetch.py locate <id> [episodes <r>] [--fetchExtras] [--probe-direct]` — `parse_locate_args`,
      `cmd_locate` (fetch lock taken NON-blocking → refuses while a fetch runs; P-4 account sessions like the fetch;
      per object stage / proof / approach / via / cost + photo_id on the terminal only; the success line only when
      every session completed; gprpc summary + Chrome closed per session), `cli` dispatch (fetch argv untouched);
      `_probe_direct` + `_head_without_cookies` implemented here, tested in 12C.c2 — 2fa3c39 (60 tests)
- [x] 12C.c2 `--probe-direct` tests: yes (media type) / no (HTTP 403, an HTML page, a network error, no link
      minted); the HEAD is cookie-less (no Cookie header, its own timeout), the link never printed nor returned, the
      browser never opens it; urllib stubbed — no network in tests — ebf8b88 (66 tests)
- [x] 12C.d1a cross-cutting scan: one run through every path (direct / other bytes / Shift+D / stale / OD-5 skip /
      failed row / locate --probe-direct) — no minted link, link token, download host, page XSRF token or account
      email in stdout, stderr, the journal, the learned sidecar or the failed list; read-only rpcids only — 4fa3d4a
      (67 tests)
- [x] 12C.d1b full suite + smoke green on 4fa3d4a (counts in Gates); change-gates checked: my diff vs the feature
      head touches only mainfetch.py + tests/test_locator.py + this file; no rollback symbol; main.py /
      ENTRY_TYPE_KEYS untouched; trigger_download byte-identical (digest 49578db2…) — b23864c
- [x] 12C.d2a (coordinator 2026-10-08, live finding of candidate A: anime "Death Note 01" — "No results" for the exact
      name, the title and the plain name; exactly the item for its bare short id) S3 ends with the BARE short id
      (`SHORT_ID_QUERY_MIN` = 6 chars) when the title query pinned nothing — one code path for RPC and the web pages
      (`_stage_listing`), candidates still verified on their records (`_merge_stage` folds both listings into one
      title_sid verdict); 3 of my own tests' expectations updated (their 6-char short ids are now searched), the
      S3u test scripts the short id to "no results" — cfdf1ac (70 tests)
- [x] 12C.d2b disarm run 1 (on 4fa3d4a): 22/24 caught; P9 (click path picks by position) and P11 (a verified item's
      other-bytes mark kept) MISSED. Closing P9 exposed a REAL SAFETY BUG: `_shows_item` accepted the located item's
      record anywhere in the page source, and an item page embeds its NEIGHBOURS' records — Shift+D could have
      downloaded a neighbour. Fixed: the open page's own address must name the located photo_id. Tests:
      test_shift_d_is_never_pressed_on_a_neighbours_page, test_the_click_path_opens_the_located_item_not_the_first_result,
      test_a_verified_items_other_bytes_are_never_booked_as_a_wrong_pick — 89abdb7 (73 tests)
- [x] 12C.d2c all 27 probes re-run on a fresh scratch copy of 89abdb7: **27/27 caught** (baseline + restored copy 73
      green; copy removed) — f5e06c9
- [x] 12C.d3 code f5e06c9: full suite **2501 passed** (exit 0; 8,929 s wall clock — it ran beside the live probe and
      across a usage-limit pause), smoke **82 passed** (15 s) — 6b60e25
- [x] 12C.l live read-only `locate` probes — the whole §M4 set + the anime case DONE (run 1 + run 2; table in "Live
      results" below) — 96825f3. Run 1 (2026-10-08 00:53,
      scratchpad c12_live_20261008_005312.txt, ids NOT copied anywhere): S02E03 and S02E02 (legacy: no key, no
      learned id) both FOUND at S2 exact_name, proof filename, via rpc, exactly-one(1) — 1 search, 2 RPC calls, 1 page
      (the drift check; it agreed), 8.6 s / 11.2 s located (118 s / 277 s per target incl. Chrome start + close); the
      live RPC search of the exact tagged name returned ONE candidate (the web page shows 4 tiles incl. the strip).
      FINDING (shared Step 11 code, not changed here): after each TV session `_close_session` printed "did not
      close" (machine under heavy load: two full suites beside it); the X-Files target's movies Chrome then could not
      bind 9222 → "Read timed out (120 s)"; the last 3 targets were skipped (port busy); the coordinator closed the
      leftover Chrome. Run 2 (2026-10-08 21:24–21:28, idle machine, scratchpad c12_live2_20261008_212452.txt; probe
      script with a per-target lock/port wait and a defensive close): 4 of 4 targets located, every Chrome closed by
      `_close_session` itself ("closed" / "closed (its windows)"), port 9222 free after each, no config copy left
- [x] 12C.l2a (found while preparing the web-page S1 probe against the real F13 day: 38 videos in four seconds) the
      web-page day listing was cut at LOCATE_MAX_CANDIDATES BEFORE the same-second filter, so a batch at tiles 21+
      could be missed → `_ui_tiles(cap=None)` for day listings: the whole day is read (≤ LOCATE_MAX_SCROLLS), the
      cap applies to the candidates after the filter; test_s1_on_the_web_pages_reads_the_whole_release_day_before_
      filtering_to_the_second — 3b0025d (74 tests)
- [x] 12C.l2b live STAGE probes (read-only, one TV session, 2026-10-11 20:32–20:36, scratchpad
      c12_stages_20261011_203201.txt — no id in it; downloads denied via CDP; Chrome closed by `_close_session`
      ("closed"), port 9222 + the fetch lock free after): every stage of candidate C ran live — table in "Live
      results" — c01ac07
- [x] 12C.d4 FINAL gates on the final code (no Python change after 3b0025d; later commits touch only .candidates/):
      disarm **28/28 caught** (2026-10-11 20:37, scratch copy; baseline + restored copy 74 green; copy removed) ·
      full suite **2502 passed** (exit 0, 310 s, idle machine) · smoke **82 passed** (41 s, on a4675a3) — the final
      commit
- [x] 12C.e CRITIQUE.md written (blinded; judge criteria, measured counts per scenario from the scenario tests on a
      scratch copy, live results, size) — a4675a3; STATE: COMPLETE in the final commit; tree clean after it

## Scenario map (PLAN Step 12 — one test per scenario; filled as tests land)
| PLAN scenario | test in tests/test_locator.py | status |
|---|---|---|
| same-second batch of 10 (correct pick, 9 never triggered) | test_a_same_second_batch_of_ten_downloads_only_the_right_episode (+ locate: test_s1_a_same_second_batch_of_ten_is_resolved_by_dedupkey_by_rpc, test_s1_on_the_web_pages_when_rpc_is_unavailable) | ✓ |
| swbisb finds a content-keyed object directly (S0b) | test_s0b_the_hash_lookup_pins_a_content_keyed_object_directly | ✓ |
| wrong-account object fetched in its home account | test_a_wrong_account_object_is_fetched_in_its_home_account | ✓ |
| legacy object by normalized name (UK panel, current year, collapsed spaces) | test_s2_on_the_uk_web_page_a_legacy_name_matches_after_normalizing | ✓ |
| stale photo_id → next stage | test_s0a_a_stale_photo_id_is_reported_and_the_next_stage_finds_the_reupload | ✓ |
| re-split duplicate set → dedupKey/newest-set rule | test_a_re_split_duplicate_set_resolves_to_the_set_the_library_holds[content-key / names-only] | ✓ |
| chunk set partially staged → only missing chunks fetched | test_a_partly_staged_chunk_set_fetches_only_its_missing_chunks_in_one_hash_batch | ✓ |
| no-DateUTC object → S2 | test_a_no_date_utc_object_skips_s1_and_is_pinned_at_s2 | ✓ |
| search_override used at S3u and still verified | test_s3u_the_users_search_term_is_tried_and_still_verified[verified / only-other-items] | ✓ |
| nothing verifiable → no trigger in S0–S3u, S4 per OD-5, failed-list row | test_nothing_verifiable_triggers_nothing_skips_todays_search_and_lands_on_the_failed_list (content key: s4-skipped) · test_a_legacy_object_nothing_verifies_gets_todays_attempts_then_a_failed_row (no key: S4 runs, then the row) (+ locate: test_nothing_verifiable_previews_todays_queries_and_marks_the_skip) | ✓ |
| RPC unavailable → the candidate's documented fallback | test_rpc_unavailable_every_stage_runs_on_the_web_pages · test_one_failing_rpc_stage_falls_back_alone_and_rpc_stays_on · test_s1_on_the_web_pages_when_rpc_is_unavailable | ✓ |
| no mutating rpcid ever sent (emulator log) | test_no_link_token_or_account_secret_reaches_any_output_journal_or_sidecar (whole fetch + locate run) (+ _assert_read_only_rpc in test_approaches_all_evaluates_every_stage_for_check_photos, test_locate_object_never_downloads_clicks_or_writes, test_fast_path_…, test_locate_lists_…) | ✓ |
| logged out → SessionExpiredError + remediation | test_a_signed_out_profile_stops_the_batch_with_its_remediation (+ locate: test_a_signed_out_profile_raises_session_expired[rpc / web-pages]) | ✓ |
| stale photo_id → stale learned record (fetch) | test_a_stale_photo_id_is_recorded_stale_and_the_reupload_is_fetched | ✓ |
| direct download of a known photo_id with zero searches (fast path) | test_fast_path_a_season_with_learned_photo_ids_downloads_directly_with_zero_searches (+ locate: test_s0a_a_learned_photo_id_is_pinned_by_item_info_with_zero_searches) | ✓ |
| a 3-chunk set downloads with ≤3 concurrent direct downloads | test_a_chunk_set_downloads_with_at_most_three_direct_downloads_at_once (5 chunks → 3 + 2 partials at once) | ✓ |
| minted URL serves HTML / wrong bytes → Shift+D → SHA-256 decides | test_a_minted_link_answering_html_falls_to_shift_d · test_a_link_serving_other_bytes_is_deleted_and_shift_d_fetches_the_original (+ trigger: test_a_link_answering_with_html_starts_nothing_and_the_tab_goes_back_to_photos) | ✓ |
| mint refused → Shift+D | test_a_refused_mint_falls_to_shift_d (+ trigger: test_a_refused_mint_is_no_direct_download) | ✓ |
| direct link page load bounded (coordinator 2026-10-07) | test_opening_the_link_bounds_the_page_load_and_restores_it | ✓ |
| no URL in any output, journal or sidecar (scan) | test_no_link_token_or_account_secret_reaches_any_output_journal_or_sidecar (+ the trigger / probe tests' own scans) | ✓ |
| Shift+D ineffective on direct page load → click path | test_shift_d_ignored_on_a_direct_page_load_falls_to_the_click_path (+ trigger: test_shift_d_ignored_on_a_direct_page_load_opens_the_item_from_its_results, test_shift_d_on_the_located_items_page, test_shift_d_is_never_pressed_on_a_page_that_does_not_show_the_located_item) | ✓ |
| `locate` never triggers a download | test_locate_lists_every_object_and_never_downloads_clicks_or_writes (+ test_locate_refuses_while_a_fetch_holds_the_lock, test_locate_follows_the_objects_account_and_closes_its_chrome, test_locate_on_a_signed_out_profile_prints_the_remediation, test_the_locate_command_line, test_locate_without_an_id_prints_its_usage) | ✓ |
| `EXACT_LOCATOR_ENABLED=False` → today's sequence | test_the_kill_switch_restores_todays_sequence_exactly (+ the frozen oracles of test_fetch_harvester, unedited, kill switch on and off) | ✓ |
| per-session RPC summary line (P-9) | test_each_account_session_ends_with_one_exact_item_line_naming_its_rpc_calls | ✓ |
| `locate --probe-direct` (yes/no + content type, never the URL) | test_probe_direct_asks_without_cookies_and_says_only_yes_or_no[4 answers] · test_probe_direct_without_a_minted_link · test_the_cookie_less_head_request_never_returns_or_raises_its_url | ✓ |
| C: drift detector (one warning + RPC off) | test_the_drift_cross_check_is_paid_once_per_run · test_the_drift_detector_turns_rpc_off_for_the_run_on_a_mismatch | ✓ |
| C: one record cache fed by both (a set / a batch reads each item once) | test_a_chunk_set_shares_one_taken_second_lookup[rpc / ui] | ✓ |
| Step 13 hook: approaches "all" | test_approaches_all_evaluates_every_stage_for_check_photos | ✓ |
| locate_object is read-only | test_locate_object_never_downloads_clicks_or_writes · test_a_stand_in_driver_locates_nothing_and_today_flow_is_untouched | ✓ |
| C: per-stage arbitration (RPC says gone, the page decides) | test_s0a_rpc_calling_an_item_gone_is_overruled_by_its_page | ✓ |
| live 2026-10-05 wrong click (exact name lists other items first) | test_the_live_wrong_click_case_is_fetched_by_its_exact_name_and_todays_search_never_runs (+ locate: test_s2_a_legacy_object_is_pinned_by_its_exact_name_never_by_tile_position) | ✓ |
| live 2026-10-07 (A's probe): S3 ends with the bare short id (≥ 6 chars), verified | test_s3_ends_with_the_bare_short_id_when_name_and_title_find_nothing[rpc / web-pages] · test_a_short_id_under_six_characters_is_never_searched_alone | ✓ |
| merged 8dacbad: "Most relevant" strip never a result / never clicked; strip-only = miss | test_a_most_relevant_strip_tile_is_never_a_candidate_on_the_web_pages · test_a_search_showing_only_the_strip_is_a_miss · test_the_click_path_never_clicks_a_strip_tile | ✓ |

## Gates (filled at 12C.d1 / 12C.d2)
- Full suite on 4fa3d4a: **2495 passed**, 1 pre-existing StarletteDeprecationWarning, 476.7 s, exit 0 (baseline 2421 at
  8968f26 + 7 tests from the merged feature head + 67 in tests/test_locator.py)
- Smoke on 4fa3d4a: **82 passed** (43.4 s)
- Code f5e06c9 (after the S3 bare short id + the item-page address check): full **2501 passed** (exit 0) · smoke
  **82 passed** · tests/test_locator.py 73
- **FINAL code (3b0025d's Python = the final commit's)**: full **2502 passed**, 1 pre-existing warning, 310 s, exit 0
  (2026-10-11 20:40–20:45) · smoke **82 passed** (41 s) · tests/test_locator.py **74 passed** · disarm **28/28**
- Disarm probes (scratchpad c12_disarm.py — scratch copy, the worktree never mutated): run 1 on 4fa3d4a 22/24 (2
  test gaps: P9, P11 → closed in 89abdb7, which also fixed the neighbour-page bug P9 exposed); run 2 on 89abdb7
  **27/27 caught**; run 3 on the final code **28/28 caught** (+ P28 the day listing cut before the filter): P1 P-5 removed · P2 names unchecked · P3 OD-5 skip removed · P4 strip/timeline tiles kept · P5 drift
  detector off · P6 per-stage fallback removed · P7 page load unbounded · P8 Shift+D without the page check · P9 click by
  position · P10 no ≤3 window · P11 other-bytes mark kept · P12 direct-download valve removed · P13 stale on RPC alone
  · P14 probe leaks the exception text · P15 locate waits for the lock · P16 no swbisb batch · P17 no S1 cache · P18
  approach.best ignored · P19 oldest set picked · P20 no failed row after S4 · P21 requests bypass _fire · P22
  capability gate removed · P23 tab left on the link's page · P24 no stale record · P25 record anywhere in the page
  trusted · P26 no bare short id · P27 any short id alone

## Live results (read-only `python mainfetch.py locate`, real library + real Chrome profiles; redacted — no ids)
| Target (§M4) | Account | Objects | Result | Stage · proof · path | Searches | Pages | RPC calls | Locate time | Wall clock |
|---|---|---|---|---|---|---|---|---|---|
| tv-en-2014-siliconvalley-s02e03 | tv | 1 | found | S2 exact_name · filename · rpc | 1 | 1 (drift check) | 2 | 8.6 s | 118 s (loaded machine) |
| tv-en-2014-siliconvalley-s02e02 (the live wrong-click case) | tv | 1 | found | S2 exact_name · filename · rpc | 1 | 1 | 2 | 11.2 s | 277 s (loaded machine) |
| tv-en-1994-xfiles-s02e05 (wrong-account, override → movies) | movies | 1 | found | S2 exact_name · filename · rpc | 1 | 1 | 2 | 5.0 s | 24.5 s |
| tv-en-2019-chernobyle02 (3 chunks, one second) | tv | 3 | 3 found | S2 exact_name · filename · rpc | 1 each | 1 / 0 / 0 | 2 each (6) | 8.7 / 2.0 / 2.5 s | 40.9 s |
| mov-en-1957-12angrymen (8 chunks) | movies | 8 | 8 found | S2 exact_name · filename · rpc | 1 each | 1 then 0 | 3 each (24) | 3.4–7.4 s | 72.9 s |
| ani-ja-2006-deathnote01 | anime | 1 | found | S3 title_sid (bare short id) · filename · rpc | 3 | 1 | 4 | 9.7 s | 37.0 s |
- 15 of 15 objects located, each "exactly-one(1)"; RPC errors 0; no drift warning in any of the 3 accounts (the one
  page per session is the drift cross-check: RPC and the item page agreed on dedupKey / taken / tz / upload).
- Every object is name-proven (legacy): the real library has no gphotos block and these objects no capture SHA-1, so
  S0a / S0b / S1 had nothing to work with (PLAN §M4's "S02E03 → S0/S1, proof dedupkey" needs the R3 apply or a
  capture) → 12C.l2 probes those stages with S02E03's published facts.
- Live facts learned: the RPC search of an exact tagged name lists exactly the item (the web page shows the strip
  tiles too); Death Note 01: exact name → 0, title → 0, bare short id → exactly the item (A's finding, confirmed via
  RPC); EWgK9e names match exactly, also names with spaces.

### Live STAGE probes (12C.l2b) — S02E03 driven with its PUBLISHED facts (PLAN P2 / RESEARCH F20, F21)
| Probe | Result | Proof · path | n | Pages | Searches | RPC | Locate time |
|---|---|---|---|---|---|---|---|
| A S2 by RPC, the library's own legacy locator | found; its record = the published facts (dedupKey, taken_ms, tz_offset_ms all equal; upload_ms known; exact name) | filename · rpc | 1 | 1 (drift check: ok) | 1 | 2 | 17.4 s |
| B S0a item info (VrseUb + fDcn4b) with the content key | found, same item; fDcn4b gave the exact file name | dedupkey · rpc | 1 | 1 (drift) | 0 | 2 | 5.2 s |
| B2 S0a by name only | found | filename · rpc | 1 | 1 (drift) | 0 | 2 | 8.2 s |
| C S0b swbisb (the SHA-1) | found, the same item as S2 | dedupkey · rpc | 1 | 1 (drift) | 0 | 1 | 4.4 s |
| D S1 lcxiM at the taken second | found, the same item; the second held exactly 10 items = all of S02 (RESEARCH F13) | dedupkey · rpc | 10 | 1 (drift) | 0 | 1 | 33.1 s |
| E S2 on the WEB PAGES, RPC off — S02E02, the live wrong-click case | found; the "Most relevant" strip dropped (n = 1, the date group's tile); info-panel name exact | filename · ui | 1 | 1 | 1 | 0 | 46.7 s |
| F S1 on the WEB PAGES, RPC off — the 38-video release day | found, the same item; the whole day read, 10 tiles at the second (S02's batch = tiles 21–30: the 12C.l2a fix was needed), 2 pages opened | dedupkey · ui | 10 | 2 | 1 | 0 | 56.4 s |
| G the direct link (P-20) | item info carried no link; **pLFTfd minted one**; a cookie-less HEAD answered **200, video/mkv** | — | — | — | — | — | — |
- RPC accounting of the session: 10 calls, 0 errors, breaker closed. No drift warning (each fresh run's first RPC
  find was cross-checked against its page: all agreed).
- First live verification of VrseUb + fDcn4b (item info), swbisb (hash lookup), lcxiM at a second and pLFTfd (a
  video's link) — all UNVERIFIED in the plan until now. G is the §M6 evidence for Suggested next task #10: the
  minted link answers WITHOUT the browser's cookies (HEAD only; nothing downloaded).
- Not exercised live (by design — this step's probes are read-only): the direct download itself, Shift+D, the
  click path, the OD-5 skip (R2's §M6–M7 fetch checks).

## Decisions / assumptions (kept current)
- Engages only for a driver that can navigate and run page scripts (`get`, `execute_script`,
  `execute_async_script` — every real Chrome driver and the fake); stand-ins (None, the Step 10/11 recorders) keep
  today's flow byte-for-byte → zero edits to existing tests (`_run_locator_phase(None, …)` stays None).
- Live evidence read (scratchpad probe_sv_*.txt, 2026-10-05): the exact-name search lists unrelated items first
  ("Most relevant" strip) and the right item twice → every candidate is verified by its record; tiles deduplicated.
- The drift cross-check costs ONE page load per run (the first RPC-located object) — candidate C's price; every
  later object of the run keeps the fast path's zero page loads.
- A stale verdict needs RPC "not found" AND an item page without its record (arbitration) — never one source alone.
- Name-only objects with several same-name items holding different bytes: the newest set is picked (outcome
  ambiguous, rule newest-set); SHA-256 decides after the download.
- No git-agent tool is available to this executor; the brief orders direct pathspec WIP commits — done that way.

## Log
- 2026-10-05 started; one usage-limit stop before any code (tree clean at 8968f26); resumed.
- 2026-10-05/06 two more limit stops during 12C.a1; the uncommitted mainfetch.py + tests/test_locator.py were coherent
  and green on resume → kept and committed as 12C.a1 (two test-harness bugs fixed first, no code change).
- Commit trailers follow the harness attribution at commit time (it alternates Fable 5.1 / Opus 5.5 mid-step; the
  session environment reports claude-fable-5-1).
