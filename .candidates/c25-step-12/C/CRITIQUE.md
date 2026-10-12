# Candidate C Self-Critique

Step 12 of IMP-C25: the exact-item locator (S0–S4) and the read-only `python mainfetch.py locate`. Approach C is hybrid arbitration.
Branch `feature/imp_c25_fetch_exact_gp_item__cand_c`. It is based on 8968f26 and merges the feature head 387a5e0, which contains d714613.

## Approach taken

**`locate_object(driver, locator, ctx, approaches="best")`** (`mainfetch.py:2913`) runs the waterfall in this order:
- S0a photo_id, then its alternates
- S0b swbisb
- S1 taken second
- S2 exact name
- S3 title + short_id, then the bare short id
- S3u search_override

The object's `approach.best` goes first.

**RPC first, per stage.** Each stage asks the read-only RPC first. If that stage's RPC call fails (`RpcUnavailable` / `RpcShapeError` / `RpcServerError`), the same stage runs on the web pages:
- the search box, past the shared strip/timeline guard;
- the item page's embedded record;
- its info panel.

**One record cache.** Both paths feed one per-run cache (`_LocatorRun`, `:2223`). Each record keeps a per-source view.

**Drift detector.** It runs once per run (`_arbitrate`, `:2880`). The first RPC-located object is compared with its item page record on dedupKey, taken, tz and upload. If they disagree, it prints one warning line, turns RPC off for the run and re-locates the object from the pages.

**Identity.** A candidate only counts when its record proves it:
- its dedupKey equals the object's content key; or
- for objects without a key, its file name matches the exact (or P-6 normalized) uploaded name.

Never a tile position.

**`_run_locator_phase`** (`:3149`):
1. It locates every object, sending the entry's content keys in one swbisb batch.
2. It fetches located objects at most 3 at a time:
   - (ii) a freshly minted direct link (`_rearm_downloads`, a bounded page load, confirmed in the incoming dir), then a harvest round;
   - then (i) Shift+D on a page whose own address names the item. A click path by photo_id covers a direct load that Shift+D ignores. Then another harvest round.
3. OD-5 skips get an `s4-skipped` row.
4. Dead photo_ids get stale learned records.
5. After today's attempts, objects that are still unlocated get a failed-list row (`_record_unlocated`, `:3729`).

**`cmd_locate`** (`:4552`) uses the same engine. It takes the fetch lock without waiting, prints per object (terminal only) and closes Chrome. With `--probe-direct` it sends a cookie-less HEAD to the minted link and reports yes/no + content type.

## Design decisions and tradeoffs

1. **The arbitration unit is the stage, not the run.**
   - When one RPC answer fails, only that stage moves to the pages. The next stage, and the next object, try RPC again. gprpc's circuit breaker (5 consecutive failures) is what turns RPC off for a whole session.
   - Alternatives considered:
     - "RPC off at the first failure", which is simpler but throws away a working path after one transient 5xx;
     - a candidate-level fallback, which mixes sources inside one verdict.
   - Cost: a stage can pay twice (a failed RPC attempt, then the pages).
   - Pinned by `test_one_failing_rpc_stage_falls_back_alone_and_rpc_stays_on`.

2. **The drift detector is paid once per run.**
   - The first RPC-found object costs one extra page load, before its trigger.
   - That breaks the shared contract's "fast path: zero page loads" for exactly ONE object per run. Every later object has zero page loads.
   - I kept it before the trigger because `locate_object` is shared with the read-only `locate` and Step 13's check. Both write learned facts that silent RPC drift would corrupt, and SHA-256 cannot catch wrong taken/upload facts.
   - Measured: a 2-episode fast-path season costs 1 page load and 4 RPC calls in total, 0 searches.

3. **"Gone" needs both sources.**
   - A learned photo_id is only marked stale when RPC answers "not found" AND its item page shows no record. The status mapping (`STATUS_NOT_FOUND = 5`) is unverified.
   - If RPC says gone but the page has the item, the page wins (`test_s0a_rpc_calling_an_item_gone_is_overruled_by_its_page`).
   - Cost: one page per dead photo_id.

4. **A verified item's link that serves other bytes is not a wrong pick.**
   - P14 allows a transcode. The merged wrong-item booking (74d662d) would have filed it as `wrong-item`. I clear the `_wrong` mark after the direct round and fall to Shift+D.
   - When the proof was a content key, direct downloads switch off for the rest of the run (`direct_off`, `:3318`). Otherwise every later object would pay the harvester's 300 s timeout for a transcode.
   - This is my own policy, not in the plan text.

5. **OD-5 previews today's queries.**
   - For a content-keyed object that nothing pinned, the S4 queries are listed and verified like any stage.
   - A verified hit is fetched as approach `legacy`. When every candidate is proven different, or there are none, S4 is skipped with an `s4-skipped` row.
   - Legacy objects keep S4 unchanged.

## Strengths

- **The live wrong-click case is fixed by construction.** S02E02's exact-name search lists other items first.
  - Every candidate is verified on its record (`_verdict`/`_judge`, `:2328`/`:2344`).
  - Live, RPC off: the strip was dropped and the item was proven by its info-panel name. The Expanse was never a candidate.
- **Every stage was verified live on one item**, all agreeing, read-only (S02E03 with its published facts):
  - S0a item info (VrseUb + fDcn4b)
  - S0b swbisb
  - S1 lcxiM (10 items at the second = all of S02, RESEARCH F13)
  - S1 and S2 on the web pages, with RPC off
  - pLFTfd minting a link that answers a cookie-less HEAD with 200 video/mkv
- **The §M4 set located 15/15 objects** across three accounts (tv, movies via override, anime), with 0 RPC errors and no drift warning.
- **Shift+D safety.**
  - `_shows_item` (`:3058`) requires the page's own address to name the located photo_id, because an item page also embeds its neighbours' records.
  - The click path clicks by photo_id, never by position (`_open_from_results`, `:3085`).
- **Release days are read whole.** The web-page day listing is read in full before the same-second filter (`_ui_tiles(cap=None)`, `:2483`). Live, S02's batch sat at tiles 21–30 of the 38-item day, beyond the earlier cut of 20.
- **Zero edits to existing tests; stand-ins keep today's flow.**
  - The capability gate (`_can_locate`, `:2262`) means stand-in drivers keep today's flow byte-for-byte. The frozen oracles with the kill switch on and off pass unedited.
  - `trigger_download` is byte-identical (digest 49578db2…).
- **No secret leaks.** Across every path (direct, other bytes, Shift+D, stale, OD-5, failed row, `--probe-direct`), no link, link token, page XSRF token or account email reaches stdout, stderr, the journal, the learned sidecar or the failed list. Only read-only rpcids are sent (`test_no_link_token_or_account_secret_reaches_any_output_journal_or_sidecar`).

## Weaknesses

- **Not exercised live:** the download triggers themselves. That covers:
  - the direct download landing in `.mv_incoming`;
  - Shift+D on a directly loaded page;
  - the click path;
  - `_download_started` timing (TRIGGER_CONFIRM_S = 20 s is a guess);
  - the bounded page load (15 s) when a navigation becomes a download;
  - the OD-5 skip on real data.

  These are R2 §M6–M7.
- **Live finding — Chrome sometimes not closed.** On a heavily loaded machine (two full suites running), the Step 11 `_close_session` twice printed "did not close". The next session then could not bind 9222. This is shared Step 11 code that I did not change. On an idle machine every session closed gracefully.
- **Live finding — the direct-download path costs more RPC calls than planned.** For the probed video, VrseUb's `[7]` carried no link, so minting needs VrseUb plus pLFTfd. The fast path is then 4 RPC calls per object live (item_info 2 + mint 2).
- **No real-library verdict yet beyond S2/S3.** It has no gphotos blocks (R3 has not run), so §M4's "S02E03 → S0/S1, dedupkey" was only reachable with hand-built locators.
- **Newest-set rule.** Name-only re-split duplicates are picked by the rule (outcome `ambiguous`, rule `newest-set`). If the newest set is not the library's, the wrong set's chunk is downloaded and rejected by SHA-256. It then falls to Shift+D of the same item (the same bytes) and finally S4. That wastes downloads; it never keeps a wrong file.
- **The arbitration costs time.** A failed RPC stage on the web pages is slow: live, web-page S2 took 47 s and web-page S1 56 s, against 5–17 s by RPC.
- **Drift check scope.** It compares only dedupKey, taken, tz and upload. A drift in width/height or in EWgK9e's name positions is caught only by verification failing (missed, never wrong).
- **Size.** `mainfetch.py` +1,396 / −8 lines (70 new functions/classes, long docstrings), which is large. A separate module would read better, but nearly every helper it uses lives in mainfetch.
- **Small changes to merged shared helpers.**
  - `_record_failed` gained 2 optional kwargs (candidates, expected_taken_local).
  - `cmd_fetch_route` gained one guarded summary-line print.
  - `_fetch_items` gained one `_record_unlocated` call.

## Against the judge criteria

1. **Contract + scenarios.**
   - Every PLAN Step 12 scenario has a test: the scenario map is in CANDIDATE_STATUS.md, 74 cases in `tests/test_locator.py`.
   - Zero edits to existing tests; pins intact; the kill switch gives today's sequence.
   - The one deviation: the fast path's first object of a run costs 1 page load (decision 2).
2. **Live.** The full §M4 set is done:
   - S02E03 / S02E02 — S2, filename
   - X-Files — movies account
   - Chernobyl E02 — 3 chunks
   - 12 Angry Men — 8 chunks
   - Death Note 01 — S3 bare short id

   S0a/S0b/S1/web pages/pLFTfd were verified with published facts. Wall clock is 24–73 s per target on an idle machine, including the Chrome start and close.
3. **Resilience.**
   - Per-stage fallback (tests for unavailable, shape error, one failing stage), drift → RPC off, gone needs two sources.
   - Strip and timeline tiles are never candidates nor clicked, and a page must show the item before Shift+D.
   - The UK, collapsed-space and year-less panel is handled; release days are read whole; SHA-256 stays final.
4. **Efficiency.** See the table below.
   - Per object on a season: 2 RPC calls (+2 mint live), 0 searches; 1 page per run.
   - Chunk set: one S1 lookup or one swbisb batch for all chunks.
5. **Clarity / size.** Long but sectioned, with docstrings on every function. 28/28 disarm probes.

## Measured cost per scenario (from the scenario tests on a scratch copy; per test = summed over its objects)

| Scenario | Objects | Stage · path | Pages | Searches | RPC calls |
|---|---|---|---|---|---|
| Fast path, 1 object (S0a) | 1 | S0a · rpc | 1 (drift) | 0 | 2 |
| Fast path, 3 objects one run | 3 | S0a · rpc | 1 | 0 | 6 |
| Fetch season fast path | 2 | S0a · rpc | 1 | 0 | 4 (+ mint per object) |
| S0b hash lookup | 1 | S0b · rpc | 1 | 0 | 1 |
| Partly staged chunk set (2 missing) | 2 | S0b · rpc | 1 | 0 | 1 (one batch) |
| 5-chunk set | 5 | S0b · rpc | 1 | 0 | 1 |
| Same-second batch of 10, RPC | 1 | S1 · rpc | 1 | 0 | 1 |
| Same-second batch of 10, web pages | 1 | S1 · ui | 7 | 1 | 1 (failed attempt) |
| Release day of 38 (4 batches), web pages | 1 | S1 · ui | 5 | 1 | 1 (failed attempt) |
| Chunk set, one S1 lookup, RPC | 3 | S1 · rpc | 1 | 0 | 1 |
| Chunk set, one S1 lookup, web pages | 3 | S1 · ui | 3 | 1 | 3 (failed attempts) |
| Live wrong-click case (legacy, S2) | 1 | S2 · rpc | 1 | 1 | 2 |
| UK normalized name (web pages) | 1 | S2 · ui | 1 | 1 | 1 (failed attempt) |
| No DateUTC → S2 | 1 | S2 · rpc | 1 | 1 | 2 |
| S3 bare short id, RPC / web pages | 1 | S3 | 1 / 2 | 3 / 3 | 4 / 3 |
| S3u verified | 1 | S3u · rpc | 1 | 4 | 5 |
| Re-split, content key / names only | 2 | S0b / S2 | 1 / 1 | 0 / 2 | 2 / 4 |
| Stale photo_id → re-upload | 1 | S0b · rpc | 2 | 0 | 2 |
| Nothing verifiable → OD-5 skip | 1 | — | 0 | 4 | 6 |
| Drift mismatch (2 objects) | 2 | S0a · ui after | 3 | 0 | 2 |

Live (redacted): the §M4 objects took 1 search + 2 RPC calls (S2), or 3 searches + 4 RPC calls (Death Note S3), and 1 page per session. Locate times were 2.0–11.2 s per object.

## Size

- `mainfetch.py`: +1,396 / −8 against the feature head.
- `tests/test_locator.py`: 1,525 lines, 65 test functions / 74 cases.
- No other production file is touched.

## Tests run

- `python -m pytest tests/test_locator.py -q` → `74 passed`
- `python -m pytest tests -q`
  - on 4fa3d4a → `2495 passed, 1 warning in 476.68s`
  - on f5e06c9 → `2501 passed, 1 warning`
  - final run on the final code → `2502 passed, 1 warning in 310.33s`
- `python -m pytest tests/smoke -q` → `82 passed`
- Disarm probes on a scratch copy (`c12_disarm.py`) → `RESULT: 28/28 caught`. Baseline and restored copy: 74 passed. The copy was removed.

## Confidence

medium-high

Reasoning:
- **Locating is proven in two ways.** Every scenario passes hermetically with 28/28 disarm probes. Live, every stage found the same item, and 15/15 real §M4 objects were located with the exact proof the design expects.
- **The download half is proven against the fake only.** That covers the direct link landing, Shift+D, the click path and the timing constants, and it is where I expect live tuning (R2 §M6–M7).
- **One known contract deviation:** the drift check's single page load per run.
