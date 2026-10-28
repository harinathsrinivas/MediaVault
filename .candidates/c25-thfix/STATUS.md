STATE: IN PROGRESS

# IMP-C25 follow-up — a throttled Google Photos search is never a "not found"; map: confirmed-but-unlisted is no loss (thfix) — resumable status

- User decision 2026-10-28 (card): "Fix inside C25 (Recommended)". Two fixes: (1) a throttle guard shared by fetch,
  check_photos (pending, the auto-confirm, the R4 sweep) and resolve_photos — a zero search answer counts as "not found"
  only when the search demonstrably works; a throttle is never reported as signed out; (2) map_photos: an object whose
  library block holds a CONFIRMED record in a freshly crawled account whose latest enumeration does not list that
  photo_id is `not-listed`, not `possible-loss`.
- Worktree `C:\Users\harin\PycharmProjects\MediaVault-thfix` · branch `tmp/c25_thfix` · base cd0ef94. The main folder
  is never touched. No live runs; evidence read only (no photo id, URL, cookie or email printed or written here).
- Python: `C:\Users\harin\PycharmProjects\MediaVault\.venv\Scripts\python.exe -m pytest …`, run from the worktree.
  Gates SEQUENTIAL, pytest's default basetemp, no xdist (low memory). Baseline (dispatch): full 3309 · smoke 102.
  Known load flake: tests/test_gpweb.py::test_fake_delayed_download_completes_in_the_background (re-run alone first).

## Resume rule
Read this file, then `git log --oneline -12` (WIP commits `wip(imp-c25): thfix.<x> — …`) and `git status`. An
uncommitted diff belongs to the first unticked sub-task: keep it when coherent and its targeted tests pass, else
`git checkout -- <its files>` (record that here). Commit THIS file (force-added: `.candidates/` is gitignored) with
every WIP commit; tick a sub-task only after its tests pass, with its SHA. Pathspec commits only; never -A, amend,
rebase or stash; `git diff --cached` + the secrets scan before every commit. Never edit PROGRESS.md / PLAN.md /
DECISIONS.md / PRIORITY.md (the orchestrator's).

## Sub-tasks (one WIP commit each)
- [x] thfix.0 this file: the findings, the design decisions, the sub-task list (no code) — 5bab898
- [x] thfix.a the fake Google Photos (tests/gphotos_fake.py): `throttle(account, after=N, rpc="zero"|"error")` /
      `unthrottle(account)` — after N searches every search box submit (text or day) shows "No results" and every new
      EzkLib text search answers an empty page or an error frame (status THROTTLED = 8); the recent-uploads listing,
      lcxiM, swbisb, item info and item pages keep working and are not counted; session "sorry" -> SORRY_URL (Google's
      "unusual traffic" page: no Photos page, no tokens, RPC status 0). test_gpweb.py +4 (unusual traffic · throttle
      on the web pages · throttle over RPC ×2): fake self-tests 36 passed; test_gpweb + test_gprpc + test_locator +
      test_gp_inventory 523 passed — this commit (`wip(imp-c25): thfix.a — …`)
- [x] thfix.b Fix 1.4 — the page classification (D1, D4–D6). mainfetch: PhotosThrottledError(SessionExpiredError,
      .why/.page), THROTTLE_RETRY_MIN, _page_kind, check_session_alive (+ the `_mv_photos_seen` mark), throttled_head /
      throttled_hint; the legacy zero streak -> PhotosThrottledError (TRIGGER_DOWNLOAD_DIGEST re-pinned, registry
      docstring says so); handlers: cmd_fetch_route (+ the locator phase syncs the journal before re-raising),
      _locate_sessions, _check_session (stop `throttled`); gprpc._photos_url checks on every request; main: _rp_walk
      (like a sign-out: that account's objects stop, the next account follows), report.txt line for `throttled`,
      docstrings; crawler EXIT_THROTTLED = 4 (+ the dom crawl's ensure_logged_in asks the classifier first; crawl_details
      re-raises SessionExpiredError instead of booking it as an item error); warm-up THROTTLED (no toast, exit 0).
      Tests +22: test_session_detector +12 (page kinds ×15 params in 1, first-look about = sign-out, about after
      signed-in = throttle, sign-in page after signed-in = sign-out, unusual traffic / other / blank ×3, the legacy
      streak, the fetch route line ×2), test_gprpc +1 (an RPC-only session is marked; bounce -> throttle; expired ->
      sign-out), test_locator +3 (mid-fetch bounce: throttle line, nothing requested, no failed row, chunk 1 journaled
      located · unusual traffic stops a fetch · locate says throttled), test_check_photos +2 (mid-run bounce: stop
      `throttled`, no line for e02, report line, re-run continues · unusual traffic with --apply records nothing),
      test_resolve_photos +1, test_gp_inventory +2 (unusual traffic exit 4 · mid-crawl bounce in the dom crawl ->
      exit 4, run result "throttled"), test_warm_profiles +1; re-pinned: test_fetch_photos_tab's C6 streak test
      (PhotosThrottledError, the new message), test_fetch_search_wait's digest; registry +4 pins. Runs: session
      detector + gprpc + warm-up + search-wait + pins 210 passed · locator + check + resolve + crawler + gpweb 671
      passed · the other fetch modules 549 + photos-tab 48 passed · smoke 102 passed — this commit
- [x] thfix.c Fix 1.1–1.3 — the canary guard (D7–D12). mainfetch: CANARY_REUSE_S = 10, CANARY_TRIES = 2,
      _LISTING_APPROACHES, _MV_TAG_RE; _LocatorRun.search_ok / canaries / canary_pool / canary_bad; _text_ids notes a
      fresh search that listed an item; _canary_rank / _canary / _inventory_canaries (latest COMPLETE enumeration, not
      archived / partial / errored) / _library_canaries (ONLY ctx.library — a dict, or the reader the fetch / locate
      pass; the guard never reads a library nobody named: a bare load_library() read the REAL library in a sandbox-less
      test and was removed) / _canary_pool / _pick_canary (never the object itself, never a failed one) /
      _canary_lists (uncached; no answer = False) / _zero_check; locate_object records text-query finds (source 1) and
      runs the guard when nothing pinned the object (res["zero_check"]); the fetch: _record_unfetched books no
      not-found row and _locate_item no s4-skipped row when the zero answers are unverified (status still failed, one
      line); `locate` prints "unverified"; the check line carries zero_check, its printed text marks unverified;
      FetchContext.library (+ _check_run / _rp_walk / cmd_fetch_route / cmd_locate set it). main: _cp_rows
      `unverified` (+ note), _cp_settle reason None for it, the report's "Unverified: N object(s) …" line, _rp_search's
      unverified marker. Tests +25: test_locator 12C.f +19 (canary stands ×2 · canary fails = throttle ×2 (empty pages;
      error frames -> the pages + a page canary) · unverified + never reads a library · recent proof + source-1 query as
      the next canary · the window expires · a failed canary confirmed by another, never asked again · _pick_canary
      exclusions · the pool's order / exclusions / read once · the library source (confirmed only, read once, none
      without ctx.library, not read when the inventory gives one) · both transports -> page canary · S0a / S0b never ask
      · the fetch stops and books nothing · the OD-5 skip booked only when verified ×2 · the not-found booked only when
      verified ×2), test_check_photos 13.k +4 (canary then booked, routed + 3 cross looks · throttled check: no line, no
      failed row, no learned record, re-run continues and books · the auto-confirm's `pending --apply --min-age-h 24`
      under a throttle · unverified: reported, never booked), test_resolve_photos +1 (S3u under a throttle: the term
      kept, no answer); adapted on purpose (their worlds had no item to test the search with, so their zero answers are
      now unverified): 6 check tests + 1 manual-fetch test get `_canary_inventories` (they test what IS booked), the
      resolve not-found S3u line gains its unverified marker; registry +7 pins. Runs: the 22 affected modules 1599
      passed · smoke 102 passed — this commit
- [x] thfix.d Fix 2 — map_photos `not-listed` (D13). main: _mp_index adds each account's `listed` ids (the latest
      complete enumeration); _mp_unlisted (a trusted block's CONFIRMED record — confirmed + a verified proof + a
      photo_id — in a FRESH account whose latest enumeration does not list it; the routing account first); _mp_nowhere
      returns `not-listed` after the freshness gates and before the IMP-C27 detectors (item = the record's facts, proof
      = its verified_by, the note "in '<a>' per its confirmed record, but the crawl does not list it — maybe archived or
      in the trash; check it with: python main.py check_photos <id>"); _mp_recency skips it; MAP_STATES gains it after
      wrong-account; no failed reason / todo row / learned record; not in the LOUD block or inconsistencies.csv; a
      partial-set sibling counts it present; the summary's "Not listed by the crawl — NOT a loss: …" block + one line
      per object (no photo id); docstrings (section comment, _mp_index, _mp_nowhere, cmd_map_photos). Tests +7
      (test_map_photos: the R3 case — verdict, note, item, proof, no partial set, no LOUD block, the block, Verdicts
      line, off todo / inconsistencies, mapping.csv status + note · --apply leaves the block byte-equal, no learned
      record, no failed row · today's verdicts kept ×4: unconfirmed, manual proof, stale crawl, photo_id listed (other
      bytes -> conflict)); registry +3 pins. A slip caught by the run: the edit had dropped the `def` line of the next
      test (test_with_devices_an_old_object_gets_the_phones_verdict) — restored before the commit. Runs: map + check +
      apply engine + primitives + pins + resolve 717 passed · smoke 102 passed — this commit
- [x] thfix.e docs — every claim checked against the code. README: the warm-up's THROTTLED; map's `not-listed`; a new
      paragraph "When Google Photos throttles its search" (what the user sees, what is recorded, how to resume).
      ARCHITECTURE: §8.3 the zero-streak backstop; §8.4 the throttle-guard bullet + `zero_check` in the result contract;
      §8.7; §8.8 rewritten (the classifier, the session-history rule, PhotosThrottledError, the per-command line
      table); §8.10 map step 4 `not-listed`, "A throttled check", resolve; §9.1b warm-up; §9.3 crawler exit 4; §12; §13
      the fake's throttle + coverage; §14 THROTTLE_RETRY_MIN / CANARY_REUSE_S / CANARY_TRIES; Last updated.
      OPERATIONS_QA: §6c the three-zeros stop is no longer "logged out"; §6d the possible-loss pointer and two new Q&As —
      "`map_photos` says possible loss, but fetch finds the file — why?" and "Why did a big `check_photos` run stop with
      "Google Photos is throttled"?" (the dispatch's title "…'search is throttled'" aligned with the words the line
      really prints, so searching the message finds it); Last updated. Line refs: every ref the code shift moved was
      re-pointed and then verified forward (refs_verify: 313 paired refs; the only mismatches are the known historical
      refs and 2 heading-attribution false positives). The verification caught what refs.py had missed: two bare §14
      refs it had read as main.py (`:2496` / `:5536` -> mainfetch `:2588` / `:5844`), the 2-digit gprpc refs it never
      read (`:60` -> `:61`, `:62-67` -> `:63-68`), and the §11 / §18 "name (N)" tables (17 numbers). Refs that were
      already off at the base (OPERATIONS_QA's point-in-time refs in §3 / §5 / §6c, ARCHITECTURE `:413` and
      `:391-392`) are left as they are. Code, one line: resolve_photos' throttle line said "nothing was recorded for the
      one at hand", but a search term the user just typed IS recorded (its search_override is written before S3u runs —
      by design, and its test says so) -> "no verdict for the one at hand: it comes back next time" (main.py, the same
      4 lines; 2 test assertions). Pin registry: +5 per-command throttle-line pins (fetch, locate, resolve, crawler,
      warm-up) + its docstring. Runs: test_resolve_photos + test_c25_regression_pins 134 passed · smoke 102 passed —
      this commit
- [ ] thfix.f disarm probes on a `git archive` scratch copy (each part reverted alone turns a test red; restored byte
      for byte)
- [ ] thfix.g gates (full, then smoke), secrets scan, self-review, STATE: COMPLETE, the final commit
      `fix(imp-c25): a throttled Google Photos search is never a "not found"; map: confirmed-but-unlisted is not a loss`

## Findings (evidence read 2026-10-28; counts and times only)
- F-a The false "logged out" (movies run 20261027T122740Z, stop 13:32:12Z). RPC was off for the whole session from its
  first object (5 batchexecute answers, all error frames -> RpcServerError -> circuit breaker: "RPC calls: 5 (errors
  5)"), so every stage ran on the web pages and the SessionExpiredError can only have come from check_session_alive
  after a page load. The movies Chrome profile's own History (a private copy, hosts + first path segment only) shows it:
  at 13:31:59Z `driver.get("https://photos.google.com/")` was SERVER-REDIRECTED to `https://www.google.com/photos/about`
  — Google Photos' landing page for signed-out visitors. It is the ONLY non-Photos page among 5,537 visits of the three
  profiles during the three runs (movies 1,934 · anime 797 · tv 2,806): no accounts.google.com, no google.com/sorry,
  no captcha. The profile's next visit (16:04:41Z, the re-crawl: 1,012 ids, 0 errors) loaded Google Photos normally.
  So a SIGNED-IN session was bounced to the signed-out landing page in the middle of a throttle — the same page a really
  signed-out profile lands on (RESEARCH F26, live 2026-09-24: the anime profile). The bounce is not distinguishable
  from a sign-out by the page alone; by session history it is (D5).
- F-b The throttle is per account and comes in waves (working / zero windows from results.jsonl, rows pinned by
  photo_id or hash left out):
  · anime (anime account): searches worked 11:06:23–11:08:43 (~56 searches in 157 s, RPC); from 11:09:00 every search
    answered nothing for 41 min (132 objects; RPC answers turned into error frames -> RpcServerError -> breaker, the
    UI search box then showed "No results"). One isolated zero before that (deathnote06, 11:08:34) sat between two
    working searches 4 s apart (05 before, 07 after).
  · anime's look-elsewhere pass (movies account, 11:50–12:27): a few working rows, then zero windows of 12, 8, 11 min.
  · movies (movies account, 12:28–13:32): zero from the first object (the account was already throttled), two short
    recoveries (13:02:45, and 13:03:50–13:09:17: 29 rows / 30 searches at ~5.5 searches/min), zero again until the stop.
  · tv (tv account, 13:32–16:02): 64 searches in 262 s worked, then zero for 55 min, recoveries of ~5–6 min (~31
    searches each) after 55 min and 46 min, zero again; the run was killed at 16:02.
  · S0a photo_id look-ups (item pages) kept working throughout (movies: 108 found by photo_id among the zeros).
  · One anime run also died launching the TV Chrome (chromedriver exit 0xC0000142, low memory) — out of scope.
- F-c Pacing evidence (Fix 1.5): after a recovery the throttle came back after ~30 searches even at the web pages'
  ~5.5–6.6 searches/min, i.e. a quarter of the RPC rate that first triggered it (17–22/min). The evidence holds no rate
  that avoided it, so a modest minimum spacing is not supported — CHECK_* pacing stays as it is (D3).
- F-d Fix 2 case: mov-en-2003-piratesofthecaribbeanthecurseoftheblackpearl chunk 6/6 (dispatch; not re-read here).

## Design decisions (binding — recorded first so a resumed session stays consistent)
- D1 Exception: `mainfetch.PhotosThrottledError(SessionExpiredError)` with `.why` (a short phrase for the line). A
  SUBCLASS on purpose: every existing handler that ends a session or a batch on a sign-out (fetch route, check
  session, resolve walk, locate, crawler, warm-up, every `except SessionExpiredError: raise` re-raise arm in the stages,
  gprpc's `_exchange`) ends it on a throttle as well — never a verdict, never a swallowed error. The user-facing handlers
  test `isinstance(e, PhotosThrottledError)` first and print the throttle line and stop reason instead of "logged out".
- D2 Advice only: `THROTTLE_RETRY_MIN = 30` ("re-run … in about 30 minutes"; the evidence's zero windows ended after
  4–55 min while searching went on). No adaptive scheme, no retry loop.
- D3 Pacing unchanged (F-c).
- D4 The throttle line (user-facing; `{why}` from the exception). [Amended as built — thfix.b, and the resolve tail in
  thfix.e: every line starts with ⏸️ (a pause, not a failure); these are the exact texts the code prints:]
  · check:   `   ⏸️ Google Photos is throttled in '<account>' ({why}) — nothing was recorded for the object it was
             checking; re-run the same command in about 30 minutes: it continues where it stopped.`
  · fetch:   `⏸️ Google Photos is throttled in '<account>' ({why}) — the fetch stops here; files already fetched are kept
             and skipped. Re-run the same fetch in about 30 minutes.`
  · resolve: `⏸️ Google Photos is throttled in '<account>' ({why}) — its objects stop here (no verdict for the one at
             hand: it comes back next time; the answers given so far are kept). Re-run resolve_photos in about 30
             minutes.`
  · locate:  `⏸️ … — the locate stops here; re-run it in about 30 minutes.` · crawler: `STOPPED: Google Photos is
    throttled in '<account>' ({why}) — not a sign-out; progress is kept, re-run in about 30 minutes.` · warm-up:
    `   ⏸️ Profile '<key>': Google showed another page than Google Photos (throttled?) — not a sign-out; the next
    warm-up looks again.` The about-page variant (D5) appends (throttled_hint): ` If Chrome on this profile still
    shows Google Photos' signed-out page after the pause, sign in (--user-data-dir=…).`
  `{why}` phrases: canary — "a search for an item known to be there found nothing"; unusual traffic — "Google showed
  its 'unusual traffic' page"; about page — "Google showed its signed-out page to a session that was signed in"; other
  page — "the browser landed on <host>, not on Google Photos"; the legacy streak — "3 searches in a row found nothing
  although the profile is signed in".
- D5 One page classifier, `check_session_alive` (still navigation-free): photos.google.com -> alive, and the driver is
  marked (`driver._mv_photos_seen`) as having shown signed-in Google Photos; accounts.google.com (any sign-in /
  verify page) -> SessionExpiredError, exactly today's message; the about page (google.com …/photos/about) ->
  SessionExpiredError only while this browser session has NOT shown signed-in Photos yet (F26: a signed-out profile
  lands there at once), else PhotosThrottledError (F-a); google.com /sorry/ -> PhotosThrottledError; any other page ->
  PhotosThrottledError. gprpc._photos_url runs the check on EVERY request (not only after a navigation), so an RPC-only
  session is marked by its first request. The legacy trigger_download zero streak raises PhotosThrottledError (its
  check_session_alive already proved the profile signed in) — TRIGGER_DOWNLOAD_DIGEST re-pinned on purpose.
  Residual (documented): an about-page bounce at a session's very FIRST look reads as signed out (with today's line).
- D6 Stop reasons / exits: check_photos' child stops with progress reason `throttled` (main: status `stopped`, exit 1 —
  like signed-out; the report says why); the throttled object gets NO results line (it stays unchecked; a re-run looks
  it up again); an `--apply` applies only what was settled before. Fetch: the batch stops (the fetch journal is synced
  first, so a re-run's resume hints hold what was already located), exit unchanged (the child returns normally; main
  ignores the child's code) — the fetch never prints "not found" for it. resolve_photos: exactly like today's sign-out
  handling, that account's objects stop (answers kept, the object at hand is asked again next time) and the walk goes
  on with the next account (a throttle is per account — F-b). Crawler: `EXIT_THROTTLED = 4`. Warm-up: status
  `THROTTLED` (one line, no toast, not a failure).
- D7 The canary guard lives in `locate_object` (after the waterfall and the drift check, only when nothing pinned the
  object), so fetch (_locate_item, _another_copy), check_photos (every scope incl. pending / the auto-confirm / the R4
  sweep), resolve_photos' S3u and `mainfetch.py locate` share it. Zero answers = the LISTING stages (taken_second,
  exact_name, title_sid, user_query, legacy preview) whose outcome is not-found (n == 0). S0a / S0b never trigger it; a
  found object never does.
- D8 Proof per (account, transport): a fresh search on that transport that listed ≥ 1 item, or a passed canary, within
  `CANARY_REUSE_S = 10` s (`_canary_clock`, a test seam) proves it — no new canary then (`zero_check` "recent"). The
  transport needing proof: `ui` when any zero came by the web pages (RPC failed inside the object: the run continues on
  the pages), else `rpc`; an rpc canary when RPC is not usable now runs on the pages. RPC search = EzkLib; an lcxiM
  (S1 timeline) answer proves nothing (not a search) but its zero needs proof like any other (the rpc canary: a proxy).
- D9 Canary sources, in order (the dispatch's): (1) items a TEXT query pinned in this run and account (query + photo_id,
  newest first); else (2) the account's crawl inventory — the latest complete enumeration, not archived / partial /
  errored, MediaVault-tagged chunk names first (F18: an exact chunk name finds exactly one), then tagged holders,
  tagged whole names, other names; query = the exact file name; else (3) library objects with a CONFIRMED record in
  that account (query = the uploaded name; ctx.library ONLY — amended in thfix.c: no fallback read, see Log). Never the object itself (its photo ids,
  its uploaded name, its content key). A canary passes only when ITS photo_id is listed (uncached: the transport is
  asked directly).
- D10 A failed canary is CONFIRMED by one second canary (another item) before anything stops; a failed canary item is
  never picked again in the run. Normal cost: ≤ 1 canary per zero-answer object (none within CANARY_REUSE_S of a working
  search); the second one only on the way to a stop — one deleted, archived or unsearchable canary item can never stop
  a run (it would stop every re-run too). Both fail (or one fails and there is no other) -> PhotosThrottledError.
- D11 No canary source (an uncrawled account, nothing found yet) -> `zero_check: "unverified"`: the verdict is recorded
  as today (results line, report, LOUD block / not-backed-up-yet) but it is OFF the failed list (check: reason None;
  fetch: no failed row) with a note and one report line — a failed row is a claim the user acts on, and nothing could
  show the search was answering. A later run with a canary source books it.
- D12 Records: locate_object's result gains `zero_check` (None · recent · canary · unverified); results.jsonl carries
  it (None left out); the printed check line adds " (unverified: no item known to be there could show the search was
  answering)"; canary searches count in the object's `searches` cost.
- D13 Fix 2: in `_mp_nowhere`, after the freshness gates and before the probable / IMP-C27 detectors: a trusted block
  with `accounts[a].confirmed` and a photo_id, account `a` crawled and fresh, and `a`'s latest complete enumeration not
  listing that photo_id -> status `not-listed` (account a; item = the confirmed record's facts; note "in '<a>' per its
  confirmed record, but the crawl does not list it — maybe archived or in the trash; check it with: python main.py
  check_photos <id>"). MAP_STATES gains it after wrong-account; no failed reason (off the failed list and
  resolutions_todo.csv), no learned record (the apply leaves the record as it is), not in the LOUD block, not a
  partial-set member, skipped by _mp_recency; its own short summary block; mapping.csv shows the status + note. The
  index gains each account's `listed` id set.

## Assumptions (surfaced — the dispatch does not fix these)
- A1 (deviates from the dispatch's literal rule "only a real sign-in page says logged out") The about page is ALSO
  where a really signed-out profile lands (F26), so it reads as signed out only before the session showed signed-in
  Photos, as throttled after (D5). Alternatives for the user: always throttled (a real sign-out would then be told
  "re-run in 30 minutes" forever) or always signed out (today's bug).
- A2 "At most one canary per zero-answer object" holds in the normal case; a confirming second canary runs only on the
  way to a stop (D10).
- A3 Unverified zeros stay off the failed list (D11); A4 the fetch's exit is unchanged (D6); A5 a throttle stop is
  `stopped` (exit 1) in check_photos, like a sign-out; A6 CANARY_REUSE_S = 10 s; A7 mixed transports -> a web-page
  canary (D8); A8 the crawler (exit 4) and the warm-up (THROTTLED) are included: both would otherwise print "signed
  out" for a throttle once PhotosThrottledError exists.
- A9 Residual: a zero cached during a flaky throttle phase whose canary passed (the per-run listing cache reuses a
  query's answer) and an isolated flaky zero amid working searches (deathnote06) can still be recorded not-found — a
  later check or the R4 sweep re-looks them; no canary scheme can see them.

## Log
- Model / attribution: dispatched to executor-fable (Fable 5.1), no MODEL-FALLBACK banner. The harness switched the
  commit attribution between Claude Fable 5.1 and Claude Opus 5.5 during the session; every commit carries the trailer
  the harness gives at the moment it is made.
- 2026-10-28, after b395285 (coordinator: limits reset, "apply the in-step checkpoint rule"): the uncommitted diff in
  main.py + mainfetch.py (+229/−10) is thfix.c's code in progress, written in this same session with its context
  intact — the canary guard (_zero_check and its sources), the zero_check field, main's unverified verdicts, the fetch's
  unverified booking. KEPT (coherent; its tests were being adapted when the limit hit). One correction made before the
  checkpoint and kept: the guard never reads a library the caller did not name (a bare `load_library()` fallback read
  the REAL library in a test without the sandbox — removed; ctx.library is a dict or the reader function the fetch /
  locate pass).
- 2026-10-28, after d08a075 (coordinator: limits reset, "apply the in-step checkpoint rule"): the uncommitted diff in
  ARCHITECTURE.md, README.md, docs/OPERATIONS_QA.md, main.py, tests/test_c25_regression_pins.py and
  tests/test_resolve_photos.py is thfix.e in progress, written in this same session with its context intact (the docs,
  the forward ref verification, the resolve line's wording, the per-command pins). KEPT: coherent, and its tests pass
  (test_resolve_photos + test_c25_regression_pins 134 passed · smoke 102 passed).
