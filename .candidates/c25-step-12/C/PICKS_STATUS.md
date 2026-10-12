STATE: IN PROGRESS — next: 2 failed-list rows

# IMP-C25 Step 12 follow-up — the judge's 10 cherry-picks into the merged locator (candidate C) — resumable status

- Ruling: DECISIONS.md §1f (🚦 G12, 2026-10-12) — card answer "Apply all 10 (Recommended)".
- Source of the items: `.candidates/c25-step-12/DECISION.md` "Cherry-picks worth taking into C" (+ "Defects found" 5–6).
  Losers' code read-only via `git show candidates/imp_c25/step-12/{a-rejected,b-rejected}:<path>`.
- Worktree `C:\Users\harin\PycharmProjects\MediaVault-picks` · branch `tmp/c25_step12_picks` · base 26c8f89 (merged
  locator 6553390 + the "Restore pages?" fix 537ff7e). The main folder is never touched (the user archives from it).
- Python: `C:\Users\harin\PycharmProjects\MediaVault\.venv\Scripts\python.exe -m pytest …`, run from the worktree.
- Baseline at 26c8f89: tests/test_locator.py 74 passed · full suite and smoke: see Gates.

## Resume rule
Read this file, then `git log --oneline -15` (WIP commits are named `wip(imp-c25): step 12 picks — <item>`) and
`git status`. An uncommitted diff belongs to the first unticked item: keep it when coherent and its targeted tests
pass, else `git checkout -- mainfetch.py tests/test_locator.py` (record that here). Continue at the first unticked
item. Commit THIS file (force-added: `.candidates/` is gitignored) with every WIP commit; tick an item only after its
tests pass, with its SHA. Pathspec commits only; never -A, amend, rebase or stash.

## Items (one WIP commit each)
- [x] 1 contract shape — `per_approach[<approach>]` exactly `{outcome, n}`; `via` in a parallel map
      `per_approach_via` ({approach: rpc|ui}, same keys and order); pinned by `test_the_result_carries_the_whole_contract`
      [fast-path / approaches-all / web-pages] (contract keys; every entry exactly {outcome, n}; the entries passed
      UNCHANGED as failed-list `tried` rows are accepted by `mvcommon.append_failed` — the judge's probe) — WIP commit
      "1 contract shape" (SHA backfilled below) · tests/test_locator.py 77 passed
- [ ] 2 failed-list rows — one row per unlocated object with the stages' evidence, booked BEFORE
      `_record_wrong_items`, today's wrong pick folded into that row
- [ ] 3 `inconsistent` reason — a located object whose verified item's downloads keep arriving with other bytes
- [ ] 4 start confirmation ignores the other downloads of the same window
- [ ] 5 a link page answering with HTML is left at once (no 20 s wait on a bearer-credential address)
- [ ] 6 another verified copy (re-locate with `exclude`) before today's blind S4 attempts
- [ ] 7 Shift+D reuses the open item page when its address already names the item (C's own-address check kept)
- [ ] 8 RPC allowlist check after every locator test (tests only)
- [ ] 9 no 20-candidate cap when the RPC records already carry dedupKeys
- [ ] 10 `locate` early stop passes `last=True` to `_close_session`
- [ ] gates — disarm probes (scratch copy, incl. the new rules) · full suite · smoke · notes · final commit

## Edits to existing tests (each required by an item — why)
- Item 1: 14 existing tests asserted `per_approach` entries WITH `via` (the shape the item removes):
  test_s0a_a_learned_photo_id_is_pinned_by_item_info_with_zero_searches, test_s0b_the_hash_lookup_pins_a_content_keyed_
  object_directly, test_s1_on_the_web_pages_when_rpc_is_unavailable, test_s1_on_the_web_pages_reads_the_whole_release_
  day_before_filtering_to_the_second, test_s2_a_legacy_object_is_pinned_by_its_exact_name_never_by_tile_position,
  test_s0a_a_stale_photo_id_is_reported_and_the_next_stage_finds_the_reupload, test_a_no_date_utc_object_skips_s1_and_
  is_pinned_at_s2, test_s3_ends_with_the_bare_short_id_when_name_and_title_find_nothing, test_s3u_the_users_search_
  term_is_tried_and_still_verified, test_nothing_verifiable_previews_todays_queries_and_marks_the_skip,
  test_rpc_unavailable_every_stage_runs_on_the_web_pages, test_approaches_all_evaluates_every_stage_for_check_photos,
  test_a_most_relevant_strip_tile_is_never_a_candidate_on_the_web_pages, test_a_search_showing_only_the_strip_is_a_miss.
  Each assertion was split, nothing weakened: `per_approach` now compared WITHOUT `via`, and the same `via` values
  asserted through `per_approach_via` (the evidence each test pinned — which path a stage took — is kept).

## Decisions / assumptions

## Gates

## Log
- 2026-10-12 started (executor-fable, max effort). Baseline tests/test_locator.py 74 passed at 26c8f89.
