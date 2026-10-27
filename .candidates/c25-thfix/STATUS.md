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
- [x] thfix.0 this file: the findings, the design decisions, the sub-task list (no code) — its own commit
      `wip(imp-c25): thfix.0 — …` (the SHA is filled in by the next sub-task's commit)
- [ ] thfix.a the fake Google Photos (tests/gphotos_fake.py): a per-account search throttle (after N searches every
      text / day search answers nothing — UI "No results", EzkLib an empty page or an error frame), a Google
      "unusual traffic" (/sorry/) session state; the fake's own tests
- [ ] thfix.b Fix 1.4 — the page classification (D4–D6): PhotosThrottledError, check_session_alive, gprpc's per-request
      look, the legacy zero streak (re-pin TRIGGER_DOWNLOAD_DIGEST), every handler's line + the `throttled` stop reason
      (check, fetch, resolve, locate, crawler EXIT_THROTTLED, warm-up THROTTLED); tests
- [ ] thfix.c Fix 1.1–1.3 — the canary guard in locate_object (D7–D12), the check line / results `zero_check`, main's
      verdicts (unverified: off the failed list, a note + one report line), the fetch's unverified booking, the
      throttled report line; tests (zero + working canary · zero + failing canary · re-run continues · fetch · the
      auto-confirm · resolve S3u · no canary source)
- [ ] thfix.d Fix 2 — map_photos `not-listed` (D13); tests
- [ ] thfix.e docs — README, ARCHITECTURE, docs/OPERATIONS_QA.md (two new Q&As), the pin registry
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
- D4 The throttle line (user-facing; `{why}` from the exception):
  · check:   `   ⏸️ Google Photos is throttled in '<account>' ({why}) — nothing was recorded for the object it was
             checking; re-run the same command in about 30 minutes: it continues where it stopped.`
  · fetch:   `❌ Google Photos is throttled in '<account>' ({why}) — the fetch stops here; files already fetched are kept
             and skipped. Re-run the same fetch in about 30 minutes.`
  · resolve: `❌ Google Photos is throttled in '<account>' ({why}) — stopping; the answers given so far are kept. Re-run
             resolve_photos in about 30 minutes.`
  · locate / crawler / warm-up: the same phrase with their own tail. The about-page variant (D5) adds: `(if that
    Chrome still shows Google Photos' signed-out page after the pause, sign in: --user-data-dir=…)`.
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
  ignores the child's code) — the fetch never prints "not found" for it. resolve_photos: the walk stops ("answers kept",
  the object is asked again next time). Crawler: `EXIT_THROTTLED = 4`. Warm-up: status `THROTTLED` (not LOGGED_OUT).
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
  that account (query = the uploaded name; ctx.library, else a guarded load). Never the object itself (its photo ids,
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
