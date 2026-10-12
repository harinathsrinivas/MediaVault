# Decision: Step 12 — Locator engine (S0–S4 incl. hash lookup + user query) + read-only `locate` — IMP-C25

## Outcome — read before acting

**Recommended winner: Candidate C (hybrid arbitration).** Runner-up: **Candidate B (RPC-first)**. Third: **Candidate A
(UI-first)**. Confidence: medium-high that C is the right pick; B over A is a close call (reasons in "Why not the
others").

This is the G12 user checkpoint (OD-17 ruling "yess ok": judge-v2 recommends, **the user picks**). **Nothing has been
merged, tagged, removed or committed.** All three branches and worktrees are exactly as the candidates left them; I
wrote only this file.

If you agree: merge `feature/imp_c25_fetch_exact_gp_item__cand_c` @ `5e7f0f6` (its code is final at `3b0025d`; the
two later commits touch only its own status/critique files).

**The verdict in five lines**
1. All three pass every Step 12 scenario test, edit no existing test and keep every pin (my own re-runs: locator tests
   65 / 84 / 74 green; 401 pinned regression tests green in each; each merges with today's feature head without a
   conflict and stays green).
2. C is the only one that survives both kinds of Google change I simulated. With Google's internal API (RPC) gone, A and
   C still fetch a 10-episode season 10/10 with zero wrong downloads, while B falls back to today's blind search. With
   the item page's format changed, B and C fetch 10/10, while A falls back to today's blind search. In both blind cases a
   search that lists another episode first made the candidate download 10 wrong files (SHA-256 deleted them all).
3. C has the strongest live evidence: 15/15 §M4 objects via RPC (2–11 s each), plus the only live runs of the content-key
   stages (S0a, S0b, S1) and of its web-page fallback. A located 15/15 on the web pages (9–28 s each); B probed 3 objects.
4. C found and fixed two real wrong-download/miss defects in its own code. B has neither. A does not have the
   day-listing one and has only a narrower form of the Shift+D one (see "Defects found").
5. What C costs: the biggest diff (+1,388 lines, two code paths), one extra page load per run (its drift check), and four
   small fixes worth taking before Step 13 (see "Cherry-picks", items 1, 2, 5 and 3).

---

## What each approach is — plain language

| | A — UI-first | B — RPC-first | C — hybrid |
|---|---|---|---|
| How it finds an item | Types searches into Google Photos, opens each candidate's own page, proves it by the record the page embeds (or the file name in the info panel) | Asks Google Photos' read-only internal API (search, hash lookup, timeline, item info, names) and proves each candidate by its content key or exact file name — never loads a page to locate | Asks the internal API first, stage by stage; if a call fails, the same stage runs on the web pages like A; cross-checks the first API answer of each run against the item page |
| If Google changes the internal API | Unaffected (locating never uses it) | Every object falls back to today's blind two attempts | That stage moves to the web pages; a silent change is caught by the drift check |
| If Google changes the web page | Every object falls back to today's blind two attempts | Unaffected (locating never reads the page) | Unaffected (API path); its page checks degrade gracefully |
| Live speed per object (self-reported) | 9–28 s (anime S3 case 39 s) | 1.7–2.1 s (13.2 s for the anime case, heavy load) | 2.0–11.2 s |
| Item pages per day-one season of 10 | 10 pages + 10 searches | 0 | 1 (the drift check) |
| Size (mainfetch.py) | +1,324 / −28 | +1,110 / −25 | +1,388 / −8 |

### Candidate A — what you get, what you give up
**You get** a locator that never asks Google's internal API to find anything. Every candidate is opened on its own page
and proven by the record that page embeds, or by the file name its info panel shows. Only the shared P-20 direct
download uses RPC. A located all 15 §M4 objects live, including the wrong-pick episode, the wrong-account X-Files
episode, both chunk sets and the anime episode. It also produced the bare-short-id finding the other two then adopted.
**You give up** speed and independence from the page:
- it costs one search and one page per object (9–28 s live);
- it depends entirely on the web page — when the item page's embedded record stops parsing, every object goes back to
  today's blind search, which in my probe downloaded 10 wrong items;
- it reads legacy file names from the info panel (display text, locale-sensitive);
- it skips the S0b hash lookup by design;
- its Shift+D check trusts the item's record anywhere in the page after its own navigation (a defence-in-depth gap, see
  "Defects found" item 2).

### Candidate B — what you get, what you give up
**You get** the smallest, fastest and cleanest implementation:
- it locates with read-only RPCs only and verifies every candidate by dedupKey or exact name;
- it reads no display text and loads no page to locate: 0 pages, about 2 s per object live;
- it checks the page's own address before Shift+D;
- it has the most thorough tests: 84 cases, a read-only-allowlist check after every test, 31/31 disarm probes.

**You give up** resilience to RPC trouble:
- When RPC is unavailable (page tokens missing, 5 consecutive failures, a changed answer shape), every object goes to
  today's blind two attempts. My probe of that case shows it downloading wrong items and waiting about 5 minutes per
  object; this is B's documented, plan-sanctioned fallback.
- Its live evidence is the thinnest: 3 objects; no movies/UK account, chunk set, X-Files or `--probe-direct`.
- A stored photo_id is marked stale on one RPC "not found" status whose code is still UNVERIFIED (`gprpc.STATUS_NOT_FOUND`).
- Silent RPC drift goes straight into learned facts.

### Candidate C — what you get, what you give up
**You get** B's API-first speed, with A's page path as a per-stage fallback and a drift detector:
- it survived both RPC loss and an item-page format change with zero wrong downloads, and recorded true facts under
  silent RPC drift (my probes);
- it refuses Shift+D unless the page's own address names the item;
- it reads a release day whole before filtering to the second;
- it marks a stored photo_id stale only when RPC and the page agree;
- its live evidence is the broadest, including the content-key stages and the page fallback.

**You give up** simplicity:
- the largest diff, with two paths to maintain;
- one extra page load per run, including on the fast path's first object;
- four rough edges best fixed before Step 13 (all listed in "Cherry-picks"):
  - its `per_approach` entries carry an extra `via` key, which the failed list refuses;
  - its failed-list row drops the stages' evidence when today's search then picks a wrong item;
  - it keeps a minted-link page open for 20 s when the link answers with HTML;
  - its download-start check does not exclude the other downloads running in the same window.

---

## Defects found — read this

A wrong download is always deleted by the harvester's SHA-256 check (no wrong file is ever kept), but each one costs a
multi-GB transfer and ends the object on the failed list. Every finding below was checked against the actual code, and
items 2–7 were reproduced by my own hermetic probes (scratch copies only).

1. **C's two self-found defects — both fixed in C, both pinned.**
   - (a) **Shift+D could fire on a neighbouring item.** An item page also embeds its neighbours' records, so "the
     target's record is in this page" does not prove which item is open. C now requires the page's own address to name
     the item: C `mainfetch.py:3058-3074` (`_shows_item`, address check at `:3066-3069`). Pinned by
     `test_shift_d_is_never_pressed_on_a_neighbours_page` and disarm probe P25.
   - (b) **A release day was cut at 20 tiles before filtering to the second.** A season batch at tiles 21–30 was missed.
     C now reads the whole day first: C `:2556` (`cap=None`), with the cap applied after the filter in `_collect` `:2625`.
     Pinned by `test_s1_on_the_web_pages_reads_the_whole_release_day_before_filtering_to_the_second` and disarm probe P28.
   - **Does B have them?** No. B checks the address before Shift+D (B `mainfetch.py:2772`, `_page_is_item`) and never
     lists a day (S1 is one `lcxiM` page at the second, B `:2303`).
     - Probes: a 38-video release day with the target batch at tiles 31–38 is found by S1. A simulated redirect to a
       neighbour is refused. A click that opens a neighbour is refused.
   - **Does A have them?**
     - (b) No. A reads up to 400 tiles over 25 scrolls (A `:2198/:2200`, `_scan_results` `:2464`) and caps candidates
       only after the second filter (`_verify`, `:2402`). Probe: the 38-video day is found by S1.
     - (a) Not as C had it. A's click path checks the address (A `:2981`), and A loads the item's exact address before
       Shift+D (A `:2956`). See item 2.
2. **A — the Shift+D check does not re-read the address after A's own navigation** (A `mainfetch.py:2956-2959`).
   - A checks the address only to decide whether to navigate. After `driver.get(<item address>)` it accepts the item's
     record anywhere in the page.
   - Probe: I made the navigation land on a neighbour whose page embeds the target's record. **A pressed Shift+D and
     downloaded the neighbour.** B refused; C refused, then recovered through its click path and downloaded the right
     item.
   - Likelihood: low — I know of no case where Google Photos answers `/photo/<id>` with another item.
   - Fix: one line (`_on_item_page` after the navigation).
   - Severity: hardening gap, not a demonstrated real-world defect.
3. **B — RPC loss means today's blind search for everything** (B `_run_stage` `:2539`: every `RpcUnavailable` becomes
   `error`; nothing is located; S4 runs).
   - Probe (10-episode legacy season, RPC tokens missing):
     - plain search: 10/10 restored through today's attempts, about 3,225 s of built-in waits (5-minute harvest timeouts);
     - search listing another episode first: **0/10 restored, 10 wrong items downloaded** then deleted.
   - A and C restored 10/10 with zero wrong downloads in both cases.
   - Plan status: this is B's documented, plan-sanctioned fallback ("RpcUnavailable → S4 legacy UI"), not a bug. It
     re-exposes today's wrong-pick risk whenever RPC is down.
4. **A — an item-page format change means today's blind search for everything** (the symmetric case).
   - Probe (`gpweb.page_record` returning nothing, same season):
     - plain search: 10/10 via today's attempts, about 3,407 s of waits;
     - another episode listed first: **0/10 restored, 10 wrong items downloaded** then deleted.
   - B and C restored 10/10 with zero wrong downloads.
5. **C — contract shape: `per_approach` entries carry an extra `via` key** (C `:2865`). The contract and Step 13 expect
   `{outcome, n}` "unchanged".
   - Probe: feeding C's entries as failed-list `tried` rows makes `mvcommon.append_failed` raise "tried entries are
     {approach, outcome, n}" (`mvcommon.py:2060`), so a Step 13 that passes them through would silently lose failed rows.
   - A (`:2717`) and B (`:2610`) emit exactly `{outcome, n}`; B pins it in `test_the_result_carries_the_whole_contract`.
   - Fix: trivial — cherry-pick 1.
6. **C — the failed-list row loses the stages' evidence** when an unlocated object's legacy attempt then picks a wrong
   item. C books unlocated objects after `_record_wrong_items` (C `:3811-3813`).
   - Probe: the object's single row is `wrong-item` with only `[legacy wrong-item]` — no candidates, no expected time.
   - A (`_list_unlocated` before it, A `:3733`) and B (`_note_unverified` before it, B `:3512`) write one row carrying
     every stage's verdict, the candidates and the wrong pick (A
     `test_one_failed_row_for_an_object_the_stages_missed_and_todays_search_picked_wrong`, B
     `test_a_legacy_object_nothing_proves_keeps_todays_attempts_and_lands_on_the_failed_list`).
   - This matters for `resolve_photos` (Step 14), which shows those candidates.
7. **Silent RPC drift reaches the learned record in A (fast path) and B.**
   - Probe: item-info answers with the timezone and upload time swapped (still valid integers). A's and B's learned
     records stored the wrong values.
   - C's drift detector caught it, printed one warning, turned RPC off for the run and recorded the page's true values
     (C `_arbitrate` `:2880`).
   - This is a data-quality issue, not a wrong download.
8. **Shared, not candidate-specific.**
   - None of the three exercised a download trigger live (direct link, Shift+D, click path); that is R2 §M6–M7.
   - C's live run 1 logged "The Chrome this fetch started ('tv') did not close" twice on a heavily loaded machine. This is
     the shared Step 11 `_close_session`, and the orchestrator already lists it as a Step 14/15 follow-up.

---

## Step requirements (PLAN.md Step 12, verbatim)

> ### Step 12 — [model: fable] [effort: max] [fallback: opus] [candidates: 3] 🔀 Locator engine (S0–S4 incl. hash lookup + user query) + read-only `locate` diagnostic
> - Depends on: Steps 2, 2b, 5, 6, 9, 10, 11.
> - Consumed by: Steps 13 (check engine = `locate_object`), 13b, 14, 🚦 R2/R3/R4.
> - Contract (all candidates; judged): `locate_object(driver, locator, ctx)` read-only → `{found, photo_id, record, proof,
>   stage, approach, via: rpc|ui, candidates_seen, pages_opened, searches, rpc_calls, elapsed, per_approach}` where
>   `per_approach[<approach>] = {outcome: exactly-one|ambiguous|not-found|wrong-item|error, n}` (Step 13 reuses it
>   unchanged); `_run_locator_phase` = locate → `trigger_verified` → confirm a download started in the entry's incoming dir (P-12) within
>   `TRIGGER_CONFIRM_S` → `item["_trigger"]`. Trigger order is FIXED for every candidate (P-20, user direction — not a
>   candidate differentiator): (ii) **direct download** — `gprpc.download_url` minted for the verified photo_id +
>   `driver.get(url)` — first whenever RPC is available, then (i) item page + focus + Shift+D; each confirmed by the
>   incoming-dir check, a failure or SHA-256 mismatch falls to the next. Fast path: an object whose trusted photo_id
>   verifies through one `item_info` call goes straight to (ii) — zero searches, zero page loads. Stage rules per P-8 (S0a photo_id, S0b `swbisb` hash lookup, S1 taken second,
>   S2 exact name, S3 title+short_id, S3u `search_override`, S4 legacy) with `approach.best` first and alternates after
>   the primary photo_id; verification per P-5 (dedupKey when a key is known, else normalized exact name from the
>   record/`batch_info` + panel); chunk sets: one S1 lookup serves all chunks (shared second), each chunk picked by
>   dedupKey or exact `.chunk.NNN` name — never by tile index; `check_session_alive` after every navigation; `stale`
>   learned records for dead photo_ids; objects that end unverified → `append_failed` (P-18, reason incl. `s4-skipped`);
>   per-run record cache so a season opens/queries each item at most once; bounded caps; only allowlisted RPCs via
>   `gprpc` (P-9); no web markers. `python mainfetch.py locate <id> [episodes <r>] [--fetchExtras] [--probe-direct]` —
>   read-only, no downloads, no writes, prints per object stage/proof/approach/via/timing (ids shown only on the user's
>   terminal); `--probe-direct` additionally mints the P-20 URL and reports only whether a cookie-less HEAD request
>   answers with media (yes/no + content type — evidence for the Suggested-next-task #10 transfer), never the URL.
> - Candidate approaches (all `[candidate-model: fable]`; on a degraded run all three run on opus — the approach is the
>   differentiator, keep all three):
>   - **A — UI-first, no RPC (conservative baseline, immune to RPC drift):** tiles from the visible DOM
>     (`a[href*="photo/"]`, checkVisibility, width>40), each candidate verified by loading
>     `https://photos.google.com/photo/<id>` and `gpweb.page_record`; UI day search for S1; S0b skipped. "No RPC" applies
>     to LOCATING only — A still uses the shared P-20 direct-download trigger (RPC mint) with the Shift+D fallback.
>   - **B — RPC-first:** S0a `item_info`, S0b `hash_lookup`, S1 `timeline_at(taken_ms)` filtered to the exact second, S2/S3/
>     S3u `search` (`EzkLib`) with every candidate verified through `batch_info`/`item_info` (filename + dedupKey) — no
>     DOM parsing except the Shift+D fallback; `RpcUnavailable` → S4 legacy UI.
>   - **C — hybrid arbitration:** RPC-first per stage with an automatic per-stage fallback to A's DOM path on
>     `RpcUnavailable`/`RpcShapeError`; one record cache fed by both; on the first object of each run the RPC record is
>     cross-checked against the page record (drift detector → one warning line + RPC off for the run on mismatch).
> - Scenario tests every candidate must pass (fake Google Photos + emulator): same-second batch of 10 (correct pick, 9
>   never triggered); `swbisb` finds a content-keyed object directly (S0b — A documents its skip); wrong-account object
>   fetched in its home account; legacy object by normalized name (UK panel, current year, collapsed spaces); stale
>   photo_id → next stage; re-split duplicate set → dedupKey/newest-set rule; chunk set partially staged → only missing
>   chunks fetched; no-DateUTC object → S2; `search_override` used at S3u and still verified; nothing verifiable → no
>   trigger in S0–S3u, S4 per OD-5, failed-list row written; RPC unavailable → the candidate's documented fallback;
>   no mutating rpcid ever sent (emulator log); logged out → SessionExpiredError + remediation; direct download of a
>   known photo_id with zero searches (fast path); a 3-chunk set downloads with ≤3 concurrent direct downloads; minted
>   URL serves HTML / wrong bytes → Shift+D fallback → SHA-256 decides; mint refused → Shift+D; no URL in any output,
>   journal or sidecar (scan); Shift+D ineffective on direct page load → click path; `locate` never triggers a download;
>   `EXACT_LOCATOR_ENABLED=False` → today's sequence.
> - Judge criteria (ranked): (1) contract + scenario tests pass with zero edits to existing tests and pins intact;
>   (2) 🚦 live read-only `locate` results (§M4: S02E03 content-verified; S02E02 legacy; one X-Files episode in MOVIES; one
>   movie chunk set; one anime episode) — correctness, wall-clock, pages + RPC calls; (3) resilience to DOM/RPC/locale
>   changes (falls through, never downloads wrongly); (4) efficiency (pages + searches + RPC calls per object on a season
>   batch); (5) clarity/size.
> - 🚦 G12: after judge-v2 writes `.candidates/c25-step-12/DECISION.md`, STOP; relay; optional live probe per worktree
>   (§M4); the user picks; merge; full suite + smoke; archive tags; remove worktrees. Commit PROGRESS after each candidate,
>   after judging and after the pick.

## Judge criteria applied (ranked, from the plan)
1. Contract + scenario tests pass, zero edits to existing tests, pins intact.
2. Live read-only `locate` results (§M4): correctness, wall-clock, pages and RPC calls.
3. Resilience to DOM / RPC / locale changes: falls through, never downloads wrongly.
4. Efficiency: pages, searches and RPC calls per object on a season batch.
5. Clarity and size.

Change-gate weighting (V2 delta 3): the step's criteria do not name blast radius, and no candidate touches a
change-gated surface (next section). So the gates break no tie here.

## Change-gated surfaces — blast radius (actual)

- **Files changed** (`git diff --name-status <merge-base> <branch>`). It is the same for all three: `M mainfetch.py`,
  `A tests/test_locator.py`, `A .candidates/c25-step-12/<X>/CANDIDATE_STATUS.md`, `A .../CRITIQUE.md`. The merge-bases
  are `94dc13f` for A and B and `387a5e0` for C, whose `mainfetch.py` is byte-identical to `94dc13f`'s (verified with
  `cmp`).
- **Rollback journal / PONR / `RollbackHardFail` / `recover_journal` / `.mediavault_txn.json` / `ENTRY_TYPE_KEYS`:**
  - 0 changed code lines in every candidate;
  - the only matching line in each diff is a sentence in the candidate's own status file ("ENTRY_TYPE_KEYS untouched");
  - `git diff <merge-base> <branch> -- main.py` is empty for all three;
  - `mvcommon.py`, `gprpc.py`, `gpweb.py`, `tests/conftest.py`, `tests/gphotos_fake.py` and every existing test are
    unchanged in all three.
- **Shared (non-gated) code each one touched outside its new section:**
  - **A:** `FetchContext` gains `locator`; `_fetch_items` gains one call, `_list_unlocated(queue, ctx)` (A `:3733`),
    before `_record_wrong_items`; `cmd_fetch_route` gains `_rpc_session_line(driver)` (A `:4306`); `cli` gains the
    `locate` dispatch.
  - **B:** `FetchContext` gains `locator_cache`; `_fetch_items` gains `_note_unverified(queue, ctx)` (B `:3512`) before
    `_record_wrong_items`; `cmd_fetch_route` gains the `_rpc_summary` line print; `cli` gains `locate`.
  - **C:** `FetchContext` gains `locator`; `_fetch_items` gains `_record_unlocated(queue, ctx)` (C `:3813`) after
    `_record_wrong_items`; `cmd_fetch_route` prints `_locator_session_line` in its `finally` (C `:4403`); `cli` gains
    `locate`. C also extends the merged shared helper `_record_failed` with two optional keyword arguments
    (`candidates`, `expected_taken_local`; C `:3629`, `:3646`). I checked existing callers:
    `mvcommon._normalize_failed` drops `None` values (`mvcommon.py:2035`), so rows from `_record_lost_twice` and
    `_record_wrong_items` are unchanged.
- `trigger_download` (pinned digest) and the search-wait code are untouched by all three: no diff hunk lands in them, and
  their digest pins pass.

## Per-criterion comparison

| Criterion | A — UI-first | B — RPC-first | C — hybrid |
|---|---|---|---|
| **1 Contract + scenarios, zero edits, pins** | Pass. 65 cases (53 functions), one+ per PLAN scenario (status table). S0b skipped as allowed (`test_candidate_a_skips_the_swbisb_hash_lookup_and_locates_without_any_rpc`). `candidates_seen` is a list (B/C: a count). | **Pass, cleanest.** 84 cases (75 functions). Contract keys and exact `{outcome, n}` pinned (`test_the_result_carries_the_whole_contract`). Allowlist checked after EVERY test (`_only_read_only_rpcids_ever`, B test file `:156`). | Pass. 74 cases (65 functions). Two deviations: `per_approach` adds `via` (C `:2865`; refused as failed-list `tried`, probe), and the fast path's first object per run costs 1 page (the drift check the plan itself specifies for C). |
| **2 Live §M4 (self-reported; see Confidence)** | 15/15 objects (6 targets incl. 3- and 8-chunk sets, X-Files in MOVIES/UK, anime). All S2/S3 filename proof, via ui, 1 page/object, 0 RPC, 9–28 s/object (anime 38.7 s). `--probe-direct`: yes, HTTP 200 video/mkv. | 3 objects (SV S02E02 — the wrong-pick case — S02E03, Death Note 01). Via rpc, 0 pages, 2–4 RPC, 1.7 / 2.1 / 13.2 s. Not probed: X-Files, chunk sets, movies/UK, `--probe-direct`. | **15/15 objects** via rpc, 2.0–11.2 s/object, 1 page/session (drift check, no drift), 2–4 RPC/object. Plus the only live runs of S0a/S0b/S1 (S02E03's published facts: all agree, proof dedupkey) and of the web-page fallback (S2 46.7 s, S1 56.4 s). pLFTfd link: cookie-less HEAD 200 video/mkv. **Corroborated against C's raw run logs** (masked). |
| **3 Resilience** | Survives RPC loss (probe 10/10, 0 wrong). Falls to today's blind search on an item-page format change (probe 0/10, 10 wrong with a decoy). Shift+D address not re-checked after its own navigation (probe). Legacy names read from the info panel (display text). Stale only after the page parser is proven (A `:2712-2716`). | Survives an item-page change (probe 10/10). Falls to today's blind search on RPC loss (probe 0/10, 10 wrong with a decoy; about 5 min of waits per object). Address-checked Shift+D (B `:2772`). Locale-independent names (RPC). Stale on one UNVERIFIED status code. Drifted facts recorded. | **Survives both** (probes 10/10, 0 wrong). Drift detector (C `:2880`, probe: true facts kept). Address-checked Shift+D (C `:3066`). Two-source stale rule (C `:2701`, `test_s0a_rpc_calling_an_item_gone_is_overruled_by_its_page`). Fixed its own two defects. |
| **4 Efficiency (uniform probe, 10-episode season, fake world)** | Day one (legacy): 10 pages + 10 searches + 10 RPC. New pushes: 10 pages + 1 search. After R3: 0 pages, 30 RPC. 24-in-one-second batch: 24 pages + 7 searches. | **Best.** Day one: 0 pages, 0 UI searches, 30 RPC. New pushes: 0 pages, 20 RPC. After R3: 30 RPC. Batch of 24: 25 RPC. | Day one: 1 page + 30 RPC. New pushes: 1 page + 20 RPC. After R3: 1 page + 30 RPC. Batch of 24: 1 page + 29 RPC. On the page path, 2 pages per object (it reloads for Shift+D). |
| **5 Clarity / size** | +1,324 / −28; one path; clear sectioning. | **Smallest:** +1,110 / −25; one path; reads top-down. | Largest: +1,388 / −8; two paths + arbitration; long but well sectioned and documented. |

Ranking per criterion: (1) B ≥ A > C (all pass) · (2) C > A > B · (3) C > B ≈ A · (4) B > C > A · (5) B > A > C.

---

## Candidate summaries

### Candidate A
- Approach: finds every object through the web page (keystroke search → result links through the shared strip/timeline
  guard → each candidate opened by its URL → page record / info-panel name). A fast path (one `item_info` call) and the
  P-20 mint are its only RPC.
- Files: `mainfetch.py` (+1,324 / −28), `tests/test_locator.py` (new, 1,328 lines), its status/critique.
- Tests: 65 passed (my re-run); orchestrator full 2493 / smoke 82; disarm 20/20 (re-run by me on a scratch copy).
- Self-critique highlights: "never a download of an unverified candidate"; weaknesses it admits are page-load cost, live
  coverage S2/filename only, 3 RPC calls on the fast path, up to 7 searches for a content-keyed object found nowhere,
  and untuned timings.
- Independent assessment:
  - Strengths:
    - Identity always comes from the item's own record (`_check` `:2369`), with a per-run cache (`_LocateRun`).
    - Never clicks by tile position; the click path clicks the result whose link names the photo_id, then checks the
      address (`:2968-2981`).
    - Leaves an HTML link page after 3 s (`:2905`).
    - One combined failed row (`_list_unlocated` `:3185` before `_record_wrong_items`).
    - Reuses the open item page for Shift+D (`:2956`).
    - The broadest UI live coverage.
  - Weaknesses:
    - Single dependency on the web page (probe item 4).
    - No address re-check after its own navigation (`:2956-2959`, probe item 2).
    - Slowest (10 pages + 10 searches per day-one season).
    - Display-text dependency for legacy proof.
    - Drifted RPC facts recorded on the fast path.

### Candidate B
- Approach: locates with gprpc only (S0a `item_info` + alternates, S0b one `swbisb` batch per entry, S1 `lcxiM` at the
  second, S2/S3/S3u `EzkLib` + `batch_info` names). The DOM is touched only by the fixed Shift+D/click fallback.
  `RpcUnavailable` → S4.
- Files: `mainfetch.py` (+1,110 / −25), `tests/test_locator.py` (new, 1,573 lines), its status/critique.
- Tests: 84 passed (my re-run); orchestrator full 2512 / smoke 82; disarm 31/31 (re-run by me on a scratch copy).
- Self-critique highlights: names its own RPC dependency, the UNVERIFIED shapes it relies on, the late-start wave edge
  case, the thin live coverage, and the bare-short-id gap found by another candidate's probe and fixed in `c10ffee`
  (verified in its history).
- Independent assessment:
  - Strengths:
    - Cleanest contract (`{outcome, n}`).
    - Address + record + bytes check before every Shift+D (`_page_is_item` `:2768`).
    - Excludes other running downloads from start confirmation (`_download_started(..., others)` `:2680`).
    - Leaves an HTML link page at once (`:2756`).
    - Re-locates once excluding a failed item (`trigger_verified` `:2948`).
    - Most accurate failed-row reasons (`inconsistent` for a located item whose downloads keep arriving with other
      bytes, `:3023`).
    - Cheapest.
  - Weaknesses:
    - Single dependency on RPC (probe item 3).
    - Single-source stale rule (`_stage_photo_id` `:2442` → `_record_dead` `:2884` on the unverified "not found" status).
    - Drifted facts recorded.
    - `cmd_locate` does not catch Ctrl+C (A/C print "Stopped by user").

### Candidate C
- Approach: B's RPC stages, each with an automatic same-stage fallback to A's page path (`_stage_listing` `:2671`). One
  record cache feeds both paths, and a once-per-run drift cross-check compares RPC with the page (`_arbitrate`). OD-5
  preview of today's queries.
- Files: `mainfetch.py` (+1,388 / −8), `tests/test_locator.py` (new, 1,525 lines), its status/critique.
- Tests: 74 passed (my re-run); orchestrator full 2502 / smoke 82; disarm 28/28 (re-run by me on a scratch copy).
- Self-critique highlights: names the drift check's page cost as its one contract deviation, its untested-live triggers,
  the shared Step 11 close issue under load, its size, and its own policy `direct_off` (direct downloads switched off
  for the run after a content-verified link served other bytes) — not in the plan, but contained and tested.
- Independent assessment:
  - Strengths:
    - Survives both failure classes (probes).
    - Drift detector.
    - Address-checked Shift+D.
    - Whole-day S1 listing.
    - Two-source stale rule.
    - Per-stage (not per-run) fallback (`test_one_failing_rpc_stage_falls_back_alone_and_rpc_stays_on`).
    - The live evidence across every stage.
  - Weaknesses:
    - `per_approach` `via` (item 5).
    - Failed-row ordering (item 6).
    - Holds an HTML link page up to 20 s before leaving (`:3044-3045`).
    - No exclusion of other downloads in `_download_started` (`:2969`).
    - Reloads the item page for Shift+D (20 pages vs A's 10 on the page path).
    - A 20-candidate cap even on RPC listings whose records carry dedupKeys (batch of 24: +4 hash lookups, still
      found).
    - `TRIGGER_CONFIRM_S` is 20 s against A/B's 15 s.
    - In `locate`, an early stop passes `last` by position (`:4618`).
    - Largest diff.

## Head-to-head

**A vs B.** Both are single-path, and each falls back to today's blind search when its one dependency breaks: A when
the page changes, B when RPC fails. My two symmetric probes show exactly that.
- **A is better** on live coverage (15/15 vs 3) and does not care about RPC.
- **B is better** in every other respect that criterion 3 also covers:
  - it re-checks the address before Shift+D;
  - it reads names without display text (locale);
  - it is 5–10x faster, with zero pages (criterion 4), and smaller (criterion 5);
  - its contract is the cleanest (criterion 1).
- The real-world breakages fetch has met so far (the Gemini panel attach, the "Most relevant" strip) were
  browser/page-side, and RPC shapes were confirmed live by C. That points slightly against A's single dependency.
- C's 15/15 live runs used the same RPC calls B uses for names (`EzkLib` + `EWgK9e`), which makes B's missing live
  coverage likely benign. That is an inference, not a verified fact.

**A vs C.** C contains A's page path as its fallback, with the defects C found fixed (address check, whole-day listing)
and A's missing address re-check closed. C is also 5–10x cheaper per object while RPC works.
- **A is better** in:
  - page reuse on the UI path;
  - its 3-second exit from an HTML link page;
  - its single combined failed row;
  - being smaller and single-path.

**B vs C.** C contains B's RPC stages plus a fallback for every stage and the drift detector. Cost: one page per run,
+278 lines, two paths.
- **B is better** in:
  - exact contract shape;
  - start-confirmation that ignores other running downloads;
  - immediate HTML exit;
  - re-locating with `exclude`;
  - the `inconsistent` reason;
  - its allowlist check on every test;
  - size.

Every one of these B-side items is a small, local change that C can take (Cherry-picks). C's resilience advantage over
B is not something B can take cheaply.

## Rationale for the recommended winner

**Criteria 1–3 favour C as a whole.**
- **Criterion 1 is a pass for all three:** contract keys, every scenario test, no edits to existing tests, pins intact.
  I re-ran it rather than adopting it. C's two deviations are small, and one of them (the drift check's page load) is
  how the plan itself defines approach C.
- **Criterion 2 goes to C** on evidence I could partly check: its raw run logs agree with its status table. They cover
  all 15 §M4 objects at 2–11 s each, and they are the only live evidence that the content-key stages (S0a `VrseUb` +
  `fDcn4b`, S0b `swbisb`, S1 `lcxiM` at a second) and the `pLFTfd` mint answer as the plan assumed.
- **Criterion 3 — "falls through, never downloads wrongly" — is where C is decisive.** It is the only candidate that
  kept 10/10 with zero wrong downloads under both RPC loss and an item-page change. A and B each return to today's
  blind search under one of them.
  - Under silent RPC drift, only C kept the learned facts true.
  - In the simulated redirect, only C refused the neighbour and still fetched the right item.
- The code evidence behind these: `_stage_listing` (`:2671`), `_arbitrate` (`:2880`), `_shows_item` (`:3058-3074`),
  `_ui_second` (`:2556`), `_stage_photo_id` (`:2701`).

**What C does worse.**
- It is the largest and most complex (two paths).
- It pays one page per run.
- It loses failed-row evidence when today's search picks wrong.
- It emits an extra `via` key that Step 13 must not pass through.
- It lingers 20 s on an HTML link page.
- It lacks B's exclusion of other running downloads when confirming a start.

These are acceptable because none of them can cause a wrong download, all of them are local fixes (four are one- to
ten-line cherry-picks from B or A), and day-one fetches — every real object is legacy until R3 — run on C's RPC path at
B's cost plus one page.

**Merge readiness.** C merges with today's feature head `7d9b1c7` (which now carries the "Restore pages?" fix,
`537ff7e`) without a conflict: a file-level 3-way merge of `mainfetch.py` gave 0 conflicts. In the merged tree, C's
locator tests plus the restore-bubble, hygiene, harvester, photos-tab and search-wait suites pass (365). The same holds
for A (356) and B (375), so merging is not a differentiator.

## Why not the others?

**B (runner-up).** B is the best-engineered single path: smallest, fastest, cleanest contract, best tests. If you prefer
to trust Google's internal API and keep the code simple, B is a sound choice. It is not the recommendation because its
one dependency is also its failure mode. When RPC is unavailable, every object returns to today's blind two attempts,
which in my probe downloaded wrong items and spent about 5 minutes of built-in waiting per object. Its live evidence
also covers only 3 of the §M4 objects. If you pick B, consider C's per-stage page fallback as the follow-up, since that
is what closes the gap.

**A (third).** A is the most conservative locator: it never uses RPC to find anything, and it located all 15 §M4 objects
live. It ranks last because its single dependency — the web page — is the side that has actually changed in this
project's history. Under a page-format change it returns to today's blind search (probe). It is the slowest (a page per
candidate) and reads legacy names from display text. Its Shift+D check would trust a neighbour's page after an
unexpected redirect (probe; one-line fix).

## Cherry-picks worth taking into C (concrete; you may ask for them)

Ordered by value. Each is a local change in `mainfetch.py` unless noted. Nothing has been applied.

1. **Contract shape** (from B; do before Step 13). Make `per_approach[<approach>]` exactly `{outcome, n}`. Today C
   writes `{outcome, n, via}` at C `:2865`. Keep `via` in a parallel map if wanted. Model: B `:2610`, plus B's pin
   `test_the_result_carries_the_whole_contract`.
2. **One failed-list row with the stages' evidence** (from A/B). Book unlocated objects before `_record_wrong_items` and
   fold today's wrong pick into that row. Source: A `_list_unlocated` `:3185` (called at `:3733`), or B
   `_note_unverified` `:3023` (called at `:3512`). Target: C `_record_unlocated` `:3729` and its call at `:3813`.
3. **`inconsistent` reason** (from B). A located object whose downloads keep arriving with other bytes is an IMP-C27
   case, not "the search picked another item". Source: B `_note_unverified` `:3023-3054`.
4. **Start confirmation that ignores the other downloads of the same window** (from B). Source:
   `_download_started(ctx, before, others)` `:2680`. Target: C `:2969`.
5. **Leave an HTML-answering link page at once** (from B, or A's 3 s). Source: B `:2756`, A `:2905`. Target: C
   `_direct_trigger` `:3044-3045`, which waits up to 20 s on that page. The page's address is a bearer credential (P-20),
   and the wait costs 17 s per HTML answer.
6. **Try another verified copy before today's blind attempts** (from B or A). Source: B `trigger_verified` `:2948`
   (re-locate with `exclude`) or A `_new_job` `:3007` (verified alternates). C sends a located item whose two triggers
   failed straight to S4.
7. **Reuse the open item page for Shift+D** when its address already names the item (from A `:2956`; keep C's address
   check). This saves one page per object on C's page path: 20 → 10 pages for 10 episodes when RPC is down.
8. **Allowlist check after every locator test** (from B's test file, `_only_read_only_rpcids_ever` `:156`). Tests only.
9. **No 20-candidate cap when the records already carry dedupKeys** (RPC listings with a content key). Source: B `_judge`
   `:2398`. Target: C `_collect` `:2625`. Minor.
10. **C's own nit:** in `locate`, pass `last=True` to `_close_session` on an early stop (C `:4618`; A and B do
    `last=stopped or …`).

If you pick B instead, the item worth porting from C is its per-stage page fallback (C `_stage_listing` `:2671` and the
page helpers around it), with the drift detector as optional. If you pick A, port B's address re-check after navigation
(B `:2772`).

---

## What to test live at G12 (§M4 read-only; §M6 optional)

Run these in the worktree of the candidate you lean to; C's paths are shown. To compare, run the same commands from
`...\step-12\A` or `...\step-12\B`. Run one command at a time on an idle machine. C's run logged "did not close"
(shared Step 11 code) when two full suites ran beside it.

```powershell
$PY = "C:\Users\harin\PycharmProjects\MediaVault\.venv\Scripts\python.exe"
Set-Location C:\Users\harin\PycharmProjects\MediaVault-c25-cand\step-12\C
# before EVERY command: no MediaVault fetch/crawl running, every MediaVault Chrome window closed, and this prints nothing:
Get-NetTCPConnection -LocalPort 9222 -ErrorAction SilentlyContinue
```

**§M4 — read-only `locate` (downloads nothing, writes nothing; photo ids appear on your terminal only):**

```powershell
& $PY mainfetch.py locate tv-en-2014-siliconvalley-s02e02 --probe-direct   # the 2026-10-05 wrong-pick episode
& $PY mainfetch.py locate tv-en-2014-siliconvalley-s02e03
Copy-Item C:\Users\harin\PycharmProjects\MediaVault\mvconfig.json .\mvconfig.json   # the X-Files account overrides
& $PY mainfetch.py locate tv-en-1994-xfiles-s02e05
Remove-Item .\mvconfig.json                                                           # gitignored here, but delete it now
& $PY mainfetch.py locate tv-en-2019-chernobyle02                                     # 3 chunks, one second
& $PY mainfetch.py locate mov-en-1957-12angrymen                                      # 8 chunks, MOVIES (UK)
& $PY mainfetch.py locate ani-ja-2006-deathnote01                                     # found only by its bare short id
```

What to look for, and the risks it covers:
- **Correct item:** for S02E02, open the printed `https://photos.google.com/photo/<id>` address in the TV profile and
  check it is Silicon Valley S02E02, not The Expanse.
- **Expected answers:**
  - SV and X-Files: `FOUND · S2 exact_name · proof filename · via rpc`.
  - Death Note: `S3 title_sid`.
  - X-Files: the `[Account] … 'movies'` switch line.
  - Every chunk: FOUND.
  - S02E03 shows filename proof, because the library holds no content key until R3. C already drove S0a/S0b/S1 live with
    its published facts.
- **Drift / fallback:** "pages 1" only on the first object of each Chrome session (C's drift check). There should be no
  "disagree with the item page" line (RPC drift) and no "internal call failed" line (C fell back to the web pages —
  note the timing if it happens).
- **`--probe-direct`:** `yes (video/…)`. A and C both reported yes / HTTP 200 / video/mkv.
- **Clean ending:** each session ends with `RPC calls: N (errors 0)`, a Chrome-closed line and "Nothing was downloaded
  or written". If you see "did not close", close that Chrome before the next command.

**§M6 — the download triggers (NOT read-only; the plan places it in R2 after Step 15, so this is optional at G12).** No
candidate has run a direct download, Shift+D or the click path live, so this is the biggest open risk for every
candidate. It downloads about 5.6 GB.

```powershell
& $PY -c "import mvcommon;e=mvcommon.load_library()['tv-en-2014-siliconvalley-s02e02'];print(e['tech_spec'].get('size_bytes'))"
& $PY mainfetch.py fetch tv-en-2014-siliconvalley-s02e02 tempdir D:\MV_fetch
& $PY main.py verify_restore tv-en-2014-siliconvalley-s02e02 tempdir D:\MV_fetch
Get-ChildItem D:\MV_fetch -Recurse -Force -Filter *.crdownload        # must print nothing
Remove-Item -Recurse -Force D:\MV_fetch\tv-en-2014-siliconvalley-s02e02
```

- **What it writes:** `mainfetch.py fetch` writes no library file. It appends one SHA-256-verified learned record to
  `~\.mediavault\gphotos\learned_tv.jsonl`, which a later `main.py fetch` folds into the library. Running
  `main.py fetch …` instead would also merge every learned record into the real library right away
  (`merge_learned_gphotos`, `main.py:10347`). Avoid that from an unmerged worktree unless you want it.
- **Expected lines, in order:**
  - `=== EXACT ITEM (1 files) ===`;
  - `S2 exact_name … via rpc`;
  - `⬇️ … direct download` — the URL must never be printed;
  - the file appears in `D:\MV_fetch\tv-en-2014-siliconvalley-s02e02\restore\.mv_incoming\`, then `✅ MOVED`;
  - `.mv_incoming` is gone and nothing is new in `~\Downloads`;
  - the `Exact item ('tv'): 1 of 1 … 1 direct download(s)` line.
- **The P14 risk to record:**
  - Did SHA-256 match, meaning the link served the original bytes?
  - If C prints "its direct link served other bytes", it switches direct downloads off for the run and uses Shift+D.
    That is the case to report, together with whether Shift+D then started a download on the directly loaded page.

## Verification status — what I ran, what I did not

- **Read in full, no sampling:** each candidate's whole `mainfetch.py` diff, its whole `tests/test_locator.py`, its
  CANDIDATE_STATUS.md and CRITIQUE.md. Also read: the plan's Steps 2/2b/3/9/10/11/12/13/13b/14, P-1…P-20 and OD-5, and
  the base `gprpc.py`, `gpweb.py`, fake and seams.
- **Re-runs, in isolated scratch copies of each final commit** (`git archive`; worktrees never written):
  - each `tests/test_locator.py`: A 65, B 84, C 74 passed;
  - 11 pinned suites — harvester (frozen oracles), search-wait (digest pins), trigger retry, session detector, CLI
    parsers, routing, account routing, photos tab, schema guard, hygiene, rollback: 401 passed in each;
  - each candidate's own disarm script, pointed at my copy: A 20/20, B 31/31, C 28/28;
  - the merge check against today's feature head (above).
- **My own hermetic probes** (results quoted above; files in my session scratchpad, not in any repo):
  - per-candidate files — 38-video release day, unreadable labels, simulated redirect, wrong click, RPC-down decoy,
    silent drift, `per_approach` as `tried`, failed-row content: `probes\probe_A.py`, `probes\probe_B.py`,
    `probes\probe_C.py`, `probes\probe_C_rows.py`;
  - one shared file, `probes\test_judge_uniform.py` — the same season in three library states, RPC down with and
    without a decoy, an item-page format change, a 24-episode one-second batch.
  - Scratchpad: `C:\Users\harin\AppData\Local\Temp\claude\C--Users-harin-PycharmProjects-MediaVault\63b4ae7b-212c-4a85-8e4a-1436806bc271\scratchpad\`.
- **Not re-run by me:**
  - The full suites and smoke. The orchestrator re-ran them sequentially (A 2493/82, B 2512/82, C 2502/82); my targeted
    runs cover every file the candidates changed plus the pinned suites around them, and a fourth full run per
    candidate would not change the verdict.
  - Anything live. That was not allowed, and nothing here touched Google Photos, Chrome or account data.

## Confidence and what I could not verify

- **Confidence:** medium-high that C is the best pick against the ranked criteria. The resilience result is
  reproducible, symmetric and large. B over A as runner-up is a judgement call (see Head-to-head).
- **Live results:** not verifiable by me.
  - A's and B's live numbers are self-reported only; neither kept raw logs, and A says so explicitly.
  - C's live numbers match its own raw run logs, which I read with ids masked. They are still the candidate's own output.
- **Download triggers:** unverified live for all three — direct link, Shift+D on a directly loaded page, the click path,
  `TRIGGER_CONFIRM_S`, Chrome's "Unconfirmed N.crdownload" naming, and whether a minted video link serves original bytes.
- **Probe realism:**
  - The redirect and wrong-click cases are simulations; I know of no real redirect.
  - The "another episode listed first" decoy is a plausible search result, not an observed one. The observed live case
    was the "Most relevant" strip, which all three — and today's legacy click — now skip.
- **Unverified RPC details:** `gprpc.STATUS_NOT_FOUND = 5` (B's stale rule depends on it), and the RPC answer shapes
  beyond what C probed live.
- **Model:** this judgement ran on fable (the session reports `claude-fable-5-1`); no MODEL-FALLBACK banner was given.
  The harness's commit-attribution notice switched to "Opus 5.5" mid-session — the same quirk candidate C recorded in
  its status. If you want the verdict re-checked on a confirmed fable run, the comparisons that matter are the
  criterion-3 probes. They are scripted and re-runnable from the scratchpad files above.
