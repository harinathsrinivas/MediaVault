# Task: Fetch the EXACT Google Photos item for every uploaded object — content key (SHA-1 = dedupKey) + taken-second locator + remembered photo_id + per-object account, backfilled from a full inventory mapping — with check mode, download hygiene, manual mode and a resumable fetch/restore — IMP-C25

Suggested branch: feature/imp_c25_fetch_exact_gp_item
Framework: v2
Planned under: fable (claude-fable-5-1), 2026-09-24
(Plan completed 2026-09-25 after four account-limit interruptions; fable re-probed OK each time — never degraded.)

> **Working location (binding):** all execution happens in the git worktree
> `C:\Users\harin\PycharmProjects\MediaVault-c25` (branch `feature/imp_c25_fetch_exact_gp_item`). The main repo
> folder `C:\Users\harin\PycharmProjects\MediaVault` is the user's operations tree (currently branch
> `feature/imp_c25_interim_capture` = PR #60) — never write plan/code there. Python for every command:
> `C:\Users\harin\PycharmProjects\MediaVault\.venv\Scripts\python.exe` (below: `$PY`).

---

## Context

MediaVault archives large video files to Google Photos through a Pixel (push) and brings them back with a Selenium
fetch that types filename/`search_term` queries into Google Photos, clicks a result tile by position, downloads it and
routes it by SHA-256 (ARCHITECTURE §8; RESEARCH F10). Live research on 2026-09-24/25 (`docs/feature-fetch-datetime/
RESEARCH.md`, F1–F26) proved that text search is not an identity (F16/F18/F25: `search_term` pinpoints exactly one
item for only 6 of 11 sampled whole files; today's attempt-1 query returns **zero** results for 4 of 11), that a
release batch shares one taken second (F13), and that every Google Photos item page embeds a locale-independent record
`[photo_id, [thumb…], taken_ms, dedupKey, tz_offset_ms, upload_ms, …]` where **dedupKey = urlsafe-base64(SHA-1 of the
uploaded bytes)** (F20/F21). Interim commits on this branch already capture identity at prep/push
(`gpcapture.py`, `<folder>/<short_id>.gpcapture.json`, SHA-1 computed in the same read pass by
`mvcommon.calculate_file_hash` → `mvcommon.cached_sha1`) and crawl every Google account into an inventory
(`tools/gp_inventory.py` → `D:\MediaVault_gp_inventory\<account>.items.jsonl`). A first read-only mapping of the
library to that inventory by exact uploaded filename matched **tv 1,065/1,126, movies 688/717, anime 157/158** — and
exposed real problems the current fetch cannot handle: **31 X-Files episodes live in the MOVIES account** (fetch routes
by id prefix → unfetchable today), 19 whitespace near-matches (innerText collapses double spaces), 7 re-split chunk
sets uploaded twice under the same names, library/cloud inconsistencies (whole-vs-chunks, untagged old names, missing
last chunks) and a set of `archived` items found in no account (their local copy is already a dummy).

## Goal

1. Every uploaded object (whole file, split chunk, FLAC-carry-out holder, extras item or extras chunk; all four
   categories `mov-`/`tv-`/`ani-`/`oth-`) carries a versioned `gphotos` identity block in its library node: content key
   (SHA-1/dedupKey), SHA-256 pin, size, dimensions, DateUTC, uploaded name, push facts (device, timezone, time) and — per
   Google account it actually lives in — the learned record (photo_id, taken_ms, tz_offset_ms, upload_ms, dedupKey), a
   **verified approach term** and a **confirmed-in-cloud** marker.
2. Fetch opens the exact item directly (photo_id → dedupKey/filename verified BEFORE download) in the **right
   account**, falls through an evidence-ordered waterfall (taken-second+dedupKey → exact uploaded name → title+short_id →
   today's exact legacy flow), never downloads an unverified item in the new stages, cleans up every file it caused to
   be downloaded (also after a crash), resumes an interrupted fetch_restore without re-downloading verified objects, and
   offers `--manual` when automation cannot find an object. SHA-256 stays the final authority; restore's chunk merge +
   canonical-hash verification stays authoritative and untouched.
3. The already-archived library is backfilled by **mapping** (inventory ↔ library by normalized exact filename, with
   dedupKey cross-checks, re-split disambiguation, wrong-account detection, device cross-check before any "missing"
   verdict) → report → user resolution (CSV) → dry-run → user review → `--apply`.
4. `check_photos` verifies any scope (item / season / series / prefix / account / pending / whole library) in Google
   Photos **without downloading**, reports exactly-one / ambiguous / not-found per approach and per category, flags
   account mismatches and library/cloud inconsistencies, and (`--apply`) records learned fields, approach terms and the
   confirmed-in-cloud marker. New archivals are confirmed by the same command after their backup.
5. Zero regression: every existing command behaves exactly as today when the new data is absent; full suite + smoke
   green at every code step; the rollback contract is untouched.

## Pre-resolved facts (planner has web tools; executors do not)

| # | Fact | Source |
|---|---|---|
| P1 | `mkvmerge -J` → `container.properties.date_utc` = `"2022-02-05T11:49:46Z"` (UTC, `Z`), `date_local` in the PC's tz; chunks of one split share one second (`06:03:02Z` ×3). The interim `gpcapture.mkv_date_utc` reads the same value in pure Python (no subprocess). | local run on `D:\MediaVault_date_probe\` copies (MKVToolNix v97.0) |
| P2 | dedupKey = URL-safe base64 of the 20-byte SHA-1, no padding, regex `^[A-Za-z0-9_-]{27}$`. S02E03: `85df66012c86f692618ac3d7152dd9afb32aaf6f` → `hd9mASyG9pJhisPXFS3Zr7Mqr28`. Compare after normalizing (`+`→`-`, `/`→`_`, strip `=`). | RESEARCH F21; go-gpm docs `https://pkg.go.dev/github.com/viperadnan-git/go-gpm` |
| P3 | Item record index map (independent confirmation): `[0]` mediaKey (photo_id), `[1][0..2]` thumb/w/h, `[2]` taken ms, `[3]` dedupKey, `[4]` tz offset ms, `[5]` creation (upload) ms. Item-info-ext (`fDcn4b`) also carries fileName `[0][2]` and size `[0][5]`. rpcids: search = `EzkLib`, item info = `VrseUb`/`fDcn4b`, hash lookup = `swbisb` (NOT used — OD-9). Thumbnail sub-array has two shapes (`["<url>",w,h]` and `["<url>",w,h,null,…,[n]]`, commit 70635cb). | `https://github.com/xob0t/Google-Photos-Toolkit` `src/api/parser.ts`, `src/api/api.ts`; crawler fix 70635cb |
| P4 | Locale: TV account renders US English (`Video - Landscape - Feb 5, 2022, 7:49:46\u202fPM`, panel `Feb 5, 2022` / `Sat, 7:49 PM`); MOVIES account renders UK English (`Video – Landscape – 21 Sept 2026, 09:00:20`, en-dashes, day-month, 24 h, panel `21 Sept` / `Sun, 09:00`). Current-year items omit the year in the panel. innerText collapses runs of whitespace. ⇒ the page record is the PRIMARY source; display text is parsed only as a hint and must be locale-tolerant. | coordinator evidence 2026-09-25; tests/test_gp_inventory.py |
| P5 | Automation Chrome needs `--disable-backgrounding-occluded-windows --disable-renderer-backgrounding --disable-background-timer-throttling` plus CDP `Page.bringToFront` + `Emulation.setFocusEmulationEnabled` or result grids do not render (F14/F26). | chrome-launcher flag docs `https://github.com/GoogleChrome/chrome-launcher/blob/main/docs/chrome-flags-for-tools.md`; probes |
| P6 | CDP download routing: `Browser.setDownloadBehavior {behavior:"allow", downloadPath, eventsEnabled}` (fallback `Page.setDownloadBehavior {behavior:"allow", downloadPath}`) via `driver.execute_cdp_cmd`, issued while the tab is on `about:blank`; in-progress files are `*.crdownload` in the target dir. Download events/cancel (`Browser.downloadWillBegin` guid → `Browser.cancelDownload`) need an event channel — attach-mode availability UNVERIFIED (live check at 🚦 R2). | Selenium CDP docs `https://www.selenium.dev/documentation/webdriver/bidi/cdp/`; ChromeDriver perf log `https://developer.chrome.com/docs/chromedriver/logging/performance-log` |
| P7 | Env: Python 3.13.5 (venv), selenium 4.39.0, pytest 9.0.3, trio 0.32 + websocket-client present, node v24.16.0 (graph validation). `tzdata` NOT installed (`zoneinfo.ZoneInfo('Asia/Singapore')` fails) ⇒ use fixed offsets (minutes/ms), no zoneinfo. | local checks |
| P8 | Disk (2026-09-25): C: 74 GB free, D: 308 GB free (C: was 2.4 GB on 2026-09-24 — the repo's split tests need ≥2 GB free on the pytest temp volume; if C: drops below 5 GB use `--basetemp=D:\MV_pytest\<label>`). | measured |
| P9 | Android: `adb -s <serial> shell getprop persist.sys.timezone` (Pixel: `Asia/Singapore`) and toybox `date +%z` (`+0800`); read-only `find`/`stat` over `/sdcard/Media`. | RESEARCH F2/F3/F8 |
| P10 | Test baseline in the worktree at 70635cb (2026-09-25): **971 passed** full suite, **81 passed** smoke (smoke took 63 s while three crawls were running; ~13 s on an idle machine). | planning run |

## Locked decisions (user, 2026-09-24/25 — do not re-open)

- **D1** Never modify media files (no DateUTC rewrite). Originals stay byte-exact.
- **D2** SHA-1 content key (= expected dedupKey) is the fetch identity; computed at prep/push for new objects (done, interim).
- **D3** Store the Google Photos `photo_id` once located; later fetches go direct and still verify before downloading.
- **D4** Legacy backfill: learn-on-fetch AND a read-only backfill — realized as **full inventory crawl + mapping** (user direction 2026-09-25) with check mode for ongoing verification.
- **D5** Agent effort — done separately (PR #59). Not part of this plan.
- **D6** The `feature/imp_u6_token_brackets` paragraph is not part of this task.
- **User scope additions (2026-09-24 19:09/19:15, 2026-09-25):** per-node approach term; richer identity capture + post-backup confirm; check mode by series/season/item + whole-library sweep (🚦); download hygiene incl. crash cleanup and a MediaVault-owned staging dir; `--manual` mode; resumable fetch/restore; all four categories; proper chunk handling; every manual action announced first as a 🚦 step; no `*_enrich` on series in tests (IMP-U7); dry-run on the real library before any `--apply`; pathspec commits; per-object actual account; confirmed-in-cloud marker; device cross-check before "missing"; execution in the worktree while PR #60 lands the interim capture ("do both").

## Planner decisions (binding for executors — recorded in DECISIONS.md)

- **P-1 `gphotos` block v1** nests on each uploaded object: the unsplit leaf entry, `split_info.chunks[i]`,
  `split_info.carried_out_tracks[i]`, an extras item, and an extras item's `split_info.chunks[i]`. It is written ONLY
  by the apply engine (Step 6). Shape:
  ```jsonc
  "gphotos": {
    "v": 1,
    "sha256": "<hex64>",              // PIN: the bytes this block describes; must equal the owning object's hash
    "sha1": "<hex40>", "dedup_key": "<27>",
    "size_bytes": 0, "width": 0, "height": 0, "duration_s": 0.0,
    "date_utc": "YYYY-MM-DDTHH:MM:SSZ",
    "uploaded_name": "<exact name pushed to the device>",
    "pushed": {"at": "…Z", "device_id": "FA75V0303405", "device_tz": "Asia/Singapore", "device_tz_offset_min": 480},
    "accounts": {                      // keyed by Chrome-profile/account key: movies | tv | anime | others
      "movies": {
        "photo_id": "…", "taken_ms": 0, "tz_offset_ms": 0, "upload_ms": 0, "dedup_key": "…",
        "gp_filename": "…", "width": 0, "height": 0,
        "verified_at": "…Z", "verified_by": "dedupkey|filename|sha256|manual",
        "source": "map|check|fetch|confirm|manual",
        "approach": {"best": "photo_id", "ok": ["photo_id","exact_name"], "failed": [], "at": "…Z", "via": "check"},
        "alternates": ["<photo_id of another item with the same name>"],   // optional (re-split duplicates)
        "confirmed": true                  // seen in the cloud with upload_ms AND identity verified
      }
    },
    "home_account": "movies"            // the account fetch must use (learned; absent ⇒ id-prefix default)
  }
  ```
  Only known facts are written (no `null`s). Capture/mapping facts are deterministic; timestamps appear only in
  `verified_at`/`approach.at`/`pushed.at`.
- **P-2 Pin rule:** a block is trusted only if `gphotos.sha256 == <owning object's current SHA-256>`
  (`hash` / chunk `hash` / `holder_hash` / item `hash`). An untrusted block is ignored by readers and replaced by the
  apply engine. A re-split replaces `split_info` wholesale, so stale per-chunk data disappears with it; learned records
  keyed by the old chunk hashes become orphans (reported, never applied).
- **P-3 Split entries:** the uploaded objects are the chunks (+ holders), never the whole file; no entry-level block is
  created for a split entry. `bless_or_verify_merged_hash` and the canonical-hash lifecycle are untouched.
- **P-4 Accounts:** routing per object = `gphotos.home_account` if set, else `mainfetch.profile_for_id(id)`. A fetch or
  check batch groups its objects by account and runs one Chrome session per account, sequentially, under one
  `fetch_session_lock`. When an item exists in several accounts (e.g. Kuroko copies in TV), `home_account` = the
  prefix-default account if it holds a verified copy, else the account with the verified copy; other copies are
  reported as `extra copies` and never touched.
- **P-5 Identity proof ladder:** `dedupkey` (page/RPC dedupKey equals the stored SHA-1's key — content proof, no
  download) > `sha256` (downloaded bytes matched) > `filename` (normalized exact uploaded name + record read from the
  item page) > `manual` (user-supplied; must be verified by check before it becomes `confirmed`).
- **P-6 Name comparison:** exact first, then normalized = NFKC + collapse all whitespace runs to one space + strip +
  casefold (the crawler's innerText collapses spaces — 19 Tamil movie names). A normalized-only match is recorded as
  `filename` proof with `name_normalized: true` in the report.
- **P-7 Display text is a hint only:** identity/time come from the page record (`taken_ms`, `tz_offset_ms`, `upload_ms`,
  `dedupKey`, w/h). Tile-label and info-panel parsers are locale-tolerant (US + UK formats, en-dash/hyphen, 12/24 h,
  `Sept`, missing year) and a parse failure only disables a pre-filter, never an identity decision.
- **P-8 Waterfall (fetch and check), evidence-ordered (F25 + mapping):** S0 stored/learned photo_id in the object's
  account → S1 taken-second + dedupKey (day search; all chunks of a split share the second) → S2 exact uploaded name
  (whole: `<name> [<sid>]<ext>`; chunk/holder: exact name) with EVERY visible tile verified → S3 title + short_id
  (category-aware title derivation) with every tile verified → S4 today's exact legacy attempts (unchanged
  `trigger_download`). A node's verified `approach.best` is tried first. S4 for content-verifiable objects is governed
  by OD-5.
- **P-9 No issued internal RPCs.** The new code drives the UI and may passively read responses the UI itself requested;
  it never calls `batchexecute` itself (account safety; OD-9).
- **P-10 Library writes:** only the apply engine writes `gphotos` blocks (single short load→patch→save, never raises);
  mainfetch and tools stay library-read-only and append learned facts to the per-account sidecar
  `~/.mediavault/gphotos/learned_<account>.jsonl`. Switch the apply engine to `mvcommon.save_library_atomic` when
  IMP-C24 lands (OD-2). **Learned-record line (cross-step convention, defined in Step 3f, produced by Steps 9/10/12/13/14):**
  `{"v":1, "at":"…Z", "account":"tv", "manual_id":"…", "label":"chunk 2/3", "sha256":"<object hash>",
  "photo_id":"…", "taken_ms":0, "tz_offset_ms":0, "upload_ms":0, "dedup_key":"…", "gp_filename":"…", "width":0,
  "height":0, "proof":"dedupkey|sha256|filename|manual", "source":"map|check|fetch|confirm|manual",
  "approach":{"tried":"s0_photo_id|s1_taken_second|s2_exact_name|s3_title_sid|s4_legacy","outcome":"exactly-one|ambiguous|not-found|wrong-item|error","n":1},
  "stale":false}` — only known keys, one JSON object per line, appended with fsync. Dry-run commands never append
  (their results live in their run directory until `--apply`).
- **P-11 Capture stays post-commit** (interim design): prep/push never write identity into the library; the capture
  file is the push-time record, folded into nodes by the apply engine (mapping / check / confirm). `confirmed` is never
  derived from capture alone.
- **P-12 Download hygiene:** downloads go to a MediaVault-owned staging dir (marker file `.mediavault_staging`);
  MediaVault deletes only files inside that dir that its fetch journal recorded or that are partial `*.crdownload`
  from a recorded run; it never deletes anything in `~/Downloads` or elsewhere.
- **P-13 Web progress contract:** mainfetch keeps printing `🔹 PROCESSING:`, `> Detected Split File (N chunks)`,
  `✅ MOVED:` and `✅ ENTRY COMPLETE.` exactly as today; no new line may contain those three markers.
- **P-14 Kill switch:** `mainfetch.EXACT_LOCATOR_ENABLED = True`; `False` reproduces today's fetch selection exactly
  (the learning/hygiene layers stay on).
- **P-15 Privacy:** never commit real photo ids, thumbnail URLs, dedupKeys (other than the published S02E03 example),
  inventory/mapping reports or account data. Reports live in `~/.mediavault/gphotos/` and `D:\MediaVault_gp_inventory\`.
  PROGRESS/STATUS/CRITIQUE/DECISION files record live results redacted (counts, stages, timings — no ids).
- **P-16 Git:** pathspec-only commits (`git commit -- <paths>`); never `git add -A`, never `git stash`, never switch
  branches in the main repo folder; do not assume a clean tree (another Claude session uses the repo). Two-dot diffs
  (`git diff origin/main..HEAD`) for branch-status checks (squash-merge trap).
- **P-17 Tests:** synthetic fixtures only; all four prefixes covered; every new guard gets a disarm (mutation) probe
  that proves its test fails when the guard is removed; hermetic redirects for `~/.mediavault/gphotos` and the
  inventory dir; no live Google/adb/network.

## Open Decisions

🚦 = must be ruled before the gated step starts. Everything else: the recommendation is applied unless the user
objects at G0. The orchestrator relays all of them at G0; "accept all recommendations" is a valid answer.

| # | Decision | Options | Recommendation |
|---|---|---|---|
| **OD-1 🚦** | Execution isolation (user keeps archiving from the main folder) | (a) worktree `MediaVault-c25` on the feature branch + PR #60 lands the interim capture on main (user-approved, in place) · (b) execute in the main folder | **(a)** — already set up. The orchestrator session is started IN the worktree; candidate worktrees live outside both trees (`C:\Users\harin\PycharmProjects\MediaVault-c25-cand\…`). After #60 is squash-merged, Step 1 merges `main` into the feature branch. |
| **OD-2 🚦** | Library writes vs IMP-C24 (no lock; concurrent writers lose updates) | (a) wait for C24 · (b) single apply engine + append-only per-account sidecar; mainfetch/tools library-read-only; short load→patch→save · (c) direct writes from every writer | **(b)** — no dependency on the unruled C24, durable (the sidecar survives any lost update and is re-applied idempotently), same risk class as every existing command. Real-library `--apply` runs only when no other MediaVault command runs (announced at 🚦 R3). Migrate to `save_library_atomic` when C24 lands. |
| **OD-3 🚦** | Rollback change-gate assessment | confirm / reject | **Confirm: no rollback behavior change.** Interim hooks are post-commit, un-journalled, try/except-guarded (already on the branch); planned additions: extras capture after a successful extras push/registration (extras have no journal), device tz/id queried post-commit in `cmd_push`, mvmeta v2 content (best-effort remote write, OD-4). NOT touched: journal format/records, PONR placement, D-6/D-7 scoping, cmd_* wrapping, `recover_journal`, season resume-range messaging, `RollbackHardFail`, `cmd_restore`, `restore_one_extra`, `bless_or_verify_merged_hash`. |
| **OD-4** | Remote `.mvmeta.json` v2 (per chunk `sha1`/`dedup_key`/`date_utc`/`uploaded_name`) | yes / no | **Yes** — no reader exists; v1 keys unchanged; gives disaster recovery and IMP-E5 the content key. |
| **OD-5** | S4 (legacy blind click) for objects whose content key is known | (a) always run S4 · (b) skip S4 when S0–S3 examined every visible candidate of the S4 queries and proved each a different item | **(b)** — S4 could only download proven-wrong multi-GB items; legacy objects (no key) keep S4 exactly. |
| **OD-6** | Where the approach term for NEW pushes lives before backup | (a) capture-file `approach_hint` at push, verified term written at confirm · (b) write the node at push (inside cmd_push) | **(a)** — rollback-neutral; fetch reads the capture file until confirm. |
| **OD-7** | Whole-library sweep depth | `best` (stop at the first approach that pinpoints) · `all` (every approach per object, ~5 searches each) | **`best` for the whole library; `all` on a sample of ~20 objects per category** (account safety, hours not days). |
| **OD-8** | Manual-resolution input | CSV · JSON | **CSV** (`resolutions.csv`, UTF-8 with BOM tolerated, Excel-friendly) — columns in Step 9. |
| **OD-9** | Issue internal RPCs (e.g. `swbisb` hash lookup) | yes / no | **No** — passive observation only (account safety; IMP-X5). Revisit under IMP-G2/S7. |
| **OD-10** | F26 profile gaps (anime was signed out; no `ChromeProfile_Others`) | register Band-0 bug / treat as prerequisite | **Prerequisite, not a code bug**: signed-out profiles already fail loudly (`check_session_alive`); C25 adds a profile pre-flight (missing dir → clear message instead of launching an empty profile). Others stays in scope; its live steps are `blocked (profile)` until the user creates + signs in `C:\Media\Utils\ChromeProfile_Others` (🚦 R0). |
| **OD-11** | New Band-0 registrations from the mapping | register / don't | **Register IMP-C26** (wrong-account objects unfetchable: 31 X-Files episodes in MOVIES — fixed by P-4) and **IMP-C27** (library↔cloud inconsistencies / possible cloud loss on archived items — detection + repair tooling in C25, per-case user decisions). |
| **OD-12** | Confirmed-in-cloud representation | new `status` value · per-object field | **Per-object field** `gphotos.accounts[a].confirmed` (+ `upload_ms`, `verified_at`) set only by verified check/mapping/fetch records; no `status` vocabulary change (status is a load-bearing contract). `archived` keeps meaning "pushed + replaced". `confirmed` always travels with `verified_by`: legacy objects can only reach `filename` proof (their bytes are dummies), new pushes reach `dedupkey`. IMP-E5 (phone cleanup) should require `confirmed` AND `verified_by ∈ {dedupkey, sha256}` before deleting a phone copy. |
| **OD-13** | Staging dir default | `~/.mediavault/staging/<account>` (C:) · always tempdir | **`~/.mediavault/staging/<account>`**, or `<tempdir>\_mv_staging\<account>` when `tempdir` is given; if the CDP redirect fails → today's `~/Downloads` harvesting with NO deletions + a warning. |
| **OD-14** | Probable matches (only the `[short_id]` tag matches) | auto-apply · report + CSV accept | **Report + CSV accept** (never auto-applied). |
| **OD-15** | Command names | — | **`map_photos`** (mapping/apply/repairs), **`check_photos`** (verification; scopes incl. `pending` = post-backup confirm), **`python mainfetch.py locate`** (read-only diagnostic); the crawler stays **`tools/gp_inventory.py`**. |
| **OD-16** | PRIORITY.md shows two NEXT pointers (header IMP-C24; "Headline NEXT" IMP-U7) | — | Make **IMP-U7** the single 👉 pointer (active regression, not gated), C24 second; C25 does not take the pointer. Applied in Step 0 once ruled. |
| **OD-17 🚦** | Locator engine candidate pick (Step 12) | A / B / C | Judge-v2 recommends; **the user picks** after the optional live read-only probe (§M4). |

## Execution protocol (v2 + resumability)

- **Journal:** `docs/feature-fetch-datetime/PROGRESS.md` (Appendix B template) is the single resumable state; updated and
  committed in the SAME commit as every step (status, model used, completing SHA, tests, notes, ▶ NEXT ACTION, sub-state,
  blockers, operations log). `DECISIONS.md` holds locked decisions, planner decisions, OD rulings and candidate outcomes.
- **Resume protocol (any session/account):** open a Claude Code session IN `C:\Users\harin\PycharmProjects\MediaVault-c25`
  → read PLAN.md, DECISIONS.md, PROGRESS.md → `git log --oneline` must match the journal SHAs (trust git on
  disagreement) → re-probe fable (`.claude/MODEL_WATERFALL.md`) → continue at ▶ NEXT ACTION; never re-run a `done` step.
- **Waterfall:** fable steps carry `[fallback: opus]`, except Step 4 (`[fallback: none]` — ENTRY_TYPE_KEYS). A degraded
  run parks `none` steps and records the model used.
- **Commits:** pathspec only (P-16); message `<type>(imp-c25): …`, body `Refs: improvements_tierC.md IMP-C25`, trailer
  `Co-Authored-By: Claude <model actually used> <noreply@anthropic.com>`.
- **Test commands** (worktree root): `& $PY -m pytest tests -q` (full) and `& $PY -m pytest tests/smoke -q` (smoke; run
  before every code-touching commit; < 30 s on an idle machine). Add `--basetemp=D:\MV_pytest\<label>` if C: < 5 GB free.
- **Sync with main:** at every step boundary check `git log --oneline HEAD..origin/main`; if main moved (PR #60 or other
  merges) run Step 1's merge procedure before the next code step, and always before Step 18.
- **Candidates:** worktrees under `C:\Users\harin\PycharmProjects\MediaVault-c25-cand\step-<N>\<A|B|C>` (outside both repo
  trees), branches `feature/imp_c25_fetch_exact_gp_item__cand_<a|b|c>`, judge output `.candidates/c25-step-<N>/DECISION.md`
  (committed), archive tags `candidates/imp_c25/step-<N>/<letter>-chosen|rejected`; worktrees removed after judging.
- **Manual steps:** every action that needs the user (sign-in, device, live Google Photos, reading a report, `--apply`
  on the real library) is a 🚦 step with exact commands and what to look for — never implied inside an executor step.

---

## Steps

(Each step lists: tags · Depends on · Consumed by · Files · Details · Tests · Acceptance. Standard test rules for every
test-writing step: never touch real `C:\Media` files, real `library_*.json`, `~/.mediavault` or
`D:\MediaVault_gp_inventory`; run `& $PY -m pytest tests -q` and fix failures before marking the step done; smoke before
commit when `main.py`/`mainfetch.py`/`mvcommon.py`/`gpcapture.py`/`gpweb.py` changed.)

### Step 0 — [model: opus] [effort: medium] Commit the plan, scaffold the journal, register the IMPs
- Depends on: nothing. Runs in the worktree (docs only).
- Consumed by: every later step; G0; any resuming session.
- Files: `docs/feature-fetch-datetime/PLAN.md` (this file, byte-identical to the root copy), `PROGRESS.md` (new),
  `DECISIONS.md` (new), `improvements/improvements_tierC.md`, `improvements_tierA.md`, `improvements_tierB.md`,
  `improvements/PRIORITY.md`, `docs/priority-graph/priority-graph.html`.
- Details:
  1. Commit 0a (FIRST, alone): `docs/feature-fetch-datetime/PLAN.md` — recoverability before anything else.
  2. Commit 0b: `PROGRESS.md` from Appendix B (rows `pending`; ▶ NEXT ACTION = "🚦 G0"; baseline 971/81 from P10 —
     re-run full + smoke once to confirm) and `DECISIONS.md` (locked decisions + user scope additions verbatim from this
     plan; research digest F1–F26 by reference with key numbers; mapping evidence summary (counts only); planner
     decisions P-1…P-17 verbatim; OD-1…OD-17 with `Ruling: PENDING (G0)`; candidate section placeholders;
     model-provenance line).
  3. Commit 0c: register **IMP-C25**, **IMP-C26**, **IMP-C27** in `improvements_tierC.md` (Appendix A text verbatim);
     IMP-C5 → `in_progress (absorbed by IMP-C25 — S2/S3 verified queries)`, IMP-A8 → `in_progress (absorbed by IMP-C25
     Step 10)`, IMP-B10 → `in_progress (absorbed by IMP-C25 Step 10)`, IMP-B4 gains a one-line note (partially
     addressed: new stages poll conditions instead of fixed sleeps; `trigger_download` untouched); PRIORITY.md: Last
     updated entry, Band 0 rows for C26 (🔴) and C27 (🚦), Band 1 row for C25 (🟠 in progress); apply OD-16 only after
     the G0 ruling (otherwise leave the pointers untouched); graph rows
     `["C25",…,"C","high","todo",…]`, `["C26",…,"C","crit","todo",…]`, `["C27",…,"C","crit","decision",…]`, edges
     `["C16","C25"],["C17","C25"],["C5","C25"],["C25","C26"],["C25","C27"],["C25","E5"],["C25","S7"],["C25","X5"],["C24","C25"]`,
     footer + task count; validate with the Appendix C node command (`badEdges 0 badTasks 0`).
- Acceptance: three pathspec commits; root PLAN.md == tracked copy (`fc /b`); graph validates; PROGRESS shows the baseline.

### 🚦 G0 — Open Decisions ruling (STOP)
Relay OD-1…OD-17 (table above) verbatim. Record each ruling with the user's words and date in DECISIONS.md; set ▶ NEXT
ACTION to Step 1; one commit. OD-1/OD-2/OD-3 must be explicit before Step 2. If OD-2 ≠ (b) or OD-3 is rejected → STOP and
re-dispatch planner-v2 to amend Steps 5/6/9–14.

### Step 1 — [model: opus] [effort: high] Sync the feature branch with main (after PR #60 lands) — repeatable
- Depends on: G0. If #60 is not merged yet, mark `waiting (#60)` and continue with Step 2; re-check at every step
  boundary; mandatory before Step 18.
- Details: `git fetch origin` → `git log --oneline HEAD..origin/main` → `git merge --no-ff origin/main -m "merge main
  into feature/imp_c25_fetch_exact_gp_item"` (the squash commit of #60 carries identical content → clean merge; on a
  conflict STOP and report) → verify with two-dot `git diff origin/main..HEAD --stat` that only feature files differ →
  full suite + smoke.
- Acceptance: merge commit present; suite/smoke green; PROGRESS row updated with the merged main SHA.

### Step 2 — [model: fable] [effort: max] [fallback: opus] Shared Google Photos web module `gpweb.py` + fake Google Photos test double
- Depends on: G0.
- Consumed by: Steps 7 (crawler), 9 (mapper name rules), 10–14 (fetch/locator/check), 15 (pins). ALL later code must use
  these parsers — no private copies (drift lesson C18/C22/C23/U7).
- Files: `gpweb.py` (new, stdlib only, no Selenium import), `tools/gp_inventory.py` (import the shared parsers; record
  format unchanged), `tests/gphotos_fake.py` (new test double), `tests/conftest.py` (fixture `fake_photos`),
  `tests/test_gpweb.py` (new), `tests/test_gp_inventory.py` (keep; extend).
- Details (pure functions):
  a. `page_record(page_source, photo_id)` — the crawler's regex (both thumbnail shapes, 70635cb) generalized + a
     `json.JSONDecoder.raw_decode` validator; returns `{photo_id, width, height, taken_ms, dedup_key, tz_offset_ms,
     upload_ms}` or `{}`; `page_records(page_source)` for all records (neighbors).
  b. `rpc_records(body)` — batchexecute body (`)]}'` guard, `wrb.fr` envelopes, JSON-in-JSON) → library-item records
     (P3 signature) and item-info-ext records (`photo_id`, `filename` `[0][2]`, `size` `[0][5]`, `dedup_key` `[0][11]`,
     `taken_ms` `[0][3]`, `tz_offset_ms` `[0][4]`), signature-validated.
  c. `info_panel(text)` — locale-tolerant (US/UK, year-less, 12/24 h, collapsed whitespace, `2.1MP` not a filename) →
     `{date_text, time_text, tz_text, gmt_offset_min, filename}`; the crawler's `parse_details` delegates to it
     (identical outputs on its existing tests).
  d. `tile_label(label, default_year)` — US `Video - Landscape - Feb 5, 2022, 7:49:46\u202fPM` and UK
     `Video – Landscape – 21 Sept 2026, 09:00:20` (en-dash/hyphen, `Sept`, 24 h, U+202F/U+00A0) → naive local datetime
     with seconds, or None.
  e. `normalize_name(s)` (P-6), `names_match(a, b)` → `"exact"|"normalized"|None`.
  f. Dedup helpers: re-export `gpcapture.dedup_key`; add `normalize_dedup_key`, `dedup_keys_equal`.
  g. Time math: `local_days(taken_ms, tz_offset_ms=None, tz_offset_min=None, window=1)` (learned offset → one day then
     ±1; predicted → predicted then ±1; unknown → utc, utc+1, utc−1), `day_query(date)` → `YYYY-MM-DD` (verified on the
     US account; UK checked live at 🚦 R2), `same_second(tile_dt, taken_ms, offset_ms|None)`.
  h. Query builders: `exact_name_query(obj)`, `title_short_id_query(filename, short_id, category)` — series/others:
     stem up to the `SxxEyy`/episode token; movies: words before the year/resolution token; anime: stem up to the episode
     number; plus the short_id — docstring examples with synthetic names.
  i. `chunk_index(name)` for `.chunk.NNN.mkv`.
  j. Fake Google Photos (`tests/gphotos_fake.py`): accounts (movies/tv/anime/others) with synthetic items (photo_id,
     filename, bytes → sha1/dedupKey, taken_ms, tz_offset_ms, upload_ms, w/h, `locale="US"|"UK"`); a driver implementing
     the Selenium surface used (get, current_url, page_source, body text, find_elements, execute_script tiles/scroll,
     execute_cdp_cmd incl. setDownloadBehavior, get_log("performance") with synthetic batchexecute bodies, window
     handles, quit), an ActionChains recorder (`/`, query, ENTER, `Shift+D`, `i`, ESC), search semantics (ISO day →
     items on that LOCAL date; text → substring over normalized filename), US/UK tile labels and panels, current-year
     year omission, whitespace collapse in panel text, Shift+D writes `<name>.crdownload` then `<name>` into the
     configured download dir (optionally ineffective on direct `/photo/` loads), logged-out mode, signed-out "about" URL.
- Tests: tables for every parser (US/UK/year-less/U+202F/en-dash/24 h/collapsed spaces/both thumbnail shapes/escaped
  slashes/invalid shapes); crawler parity (existing `test_gp_inventory.py` unchanged and green; new test: crawler
  functions delegate to `gpweb`); fake self-tests.
- Acceptance: full suite + smoke green; `tools/gp_inventory.py` output format byte-compatible (golden record test).

### Step 3 — [model: fable] [effort: max] [fallback: opus] Identity primitives in `mvcommon.py` + hermetic test redirects
- Depends on: Step 2 (name/dedup helpers).
- Consumed by: Steps 4–15.
- Files: `mvcommon.py`, `tests/conftest.py` (autouse redirects), `tests/test_gphotos_primitives.py` (new).
- Details (stdlib; module-qualified runtime access for constants — binding hazard; mvcommon never imports mainfetch/main):
  a. Constants `GPHOTOS_STATE_DIR = <MV_STATE_DIR>/gphotos`, `GP_INVENTORY_DIR = r"D:\MediaVault_gp_inventory"`.
  b. Block helpers: `new_gphotos_block(sha256, **facts)` (drops None), `block_trusted(block, sha256)`,
     `account_record(block, account)`, `home_account(block, manual_id, prefix_map)` (P-4; the caller passes
     `mainfetch.ID_PREFIX_PROFILE`/`DEFAULT_PROFILE` — a drift pin asserts the default equals `profile_for_id`).
  c. Object inventory: `iter_entry_objects(manual_id, entry)` / `iter_uploaded_objects(library, ids=None)` → dicts
     `{manual_id, category, kind: whole|chunk|holder|extra_whole|extra_chunk, index, label, sha256, uploaded_name,
     slot, block, cloud_state}`; alias-safe (skip `multi_ep_alias`; season_map → extras only); split leaf → chunks +
     holders only (P-3); uploaded name = trusted block's `uploaded_name` > capture record > `remote_upload_name(filename,
     short_id)`; deterministic order; `category` from the id prefix (movies/tv/anime/others).
  d. `remote_upload_name(filename, short_id)` + drift pins against `cmd_push` (mock_device device file name), `cmd_prep`'s
     default `search_term`, `push_one_extra`, and `gpcapture.snapshot_push_objects`.
  e. Capture reader `read_capture(folder, short_id)` (v1 + v2 tolerant) → per-object facts keyed by sha256 (latest
     complete push wins; union with partial `chunk_range` pushes; prep record for whole files).
  f. Learned store (defines the P-10 learned-record line — every producer in Steps 9/10/12/13/14 uses these helpers, never
     its own writer): `append_learned(account, record)` (validates keys against the P-10 shape, JSONL, fsync),
     `read_learned(account=None)` (torn-line tolerant), `reduce_learned(records)` → best per `(account, sha256)` by proof
     ladder (P-5) then recency; `stale` supersedes older records for the same photo_id; per-approach outcomes are folded
     into `approach.ok`/`approach.failed`/`approach.best`; `apply_learned(block, record, account)` →
     `(block, changed, conflict)` pure.
  g. conftest: autouse fixture redirecting `mvcommon.GPHOTOS_STATE_DIR` and `mvcommon.GP_INVENTORY_DIR` into `tmp_path`
     for every test (hard-guard assertions that the patched paths are under tmp_path).
- Tests: all four prefixes; unsplit/split/holder/extras whole/extras split; alias + season_map-without-extras; capture
  v1/v2 fixtures; reducer/apply edge cases (pin mismatch, sha1 conflict, stale, alternates); disarm probe for the pin rule.
- Acceptance: full suite + smoke green.

### Step 4 — [model: fable] [effort: max] [fallback: none] `ENTRY_TYPE_KEYS` documentation + schema guard for `gphotos`
- Depends on: Step 3 (`new_gphotos_block`).
- Consumed by: every writer/reader of the block; docs.
- Files: `main.py` (ENTRY_TYPE_KEYS comment only; `required`/`physical` unchanged), `tests/test_entry_schema_guard.py`.
- Details: document the optional nested block (placements P-1, pin P-2, accounts/home_account/approach/confirmed, no new
  entry type, in no required set). Tests: canonical `_gphotos_block()` (synthetic ids) round-trips byte-for-byte on an
  unsplit leaf, split chunks + holder, season_map extras whole + split; every whole-library read command (scan_unprepped,
  local_status, sort, collect_reclaimable, items_payload, verify_library, repair_dummies with fake_dummy, build_tree)
  tolerates it without mutation; registry literal unchanged.
- Acceptance: guard + full suite + smoke green.

### Step 5 — [model: fable] [effort: max] [fallback: opus] Capture v2: extras, device facts, approach hint, mvmeta v2
- Depends on: Steps 3–4; OD-3/OD-4/OD-6 rulings.
- Consumed by: Steps 6, 9, 10–13; 🚦 R2 new-archival test (§M11).
- Files: `gpcapture.py`, `main.py` (post-commit hook sites in `cmd_push`; `_extras_scan_merge_and_save`;
  `push_one_extra`/`push_title_extras`; `write_remote_mvmeta` + `MVMETA_SCHEMA_VERSION`; new `_device_facts(adb_base)`),
  `tests/test_gp_capture.py` (extend).
- Details:
  a. `CAPTURE_VERSION = 2`: per object `approach_hint` (`taken_second+dedupkey` when date_utc+sha1 known, else
     `exact_name`), `width/height/duration_s` when known (prep tech), push `device_tz`/`device_tz_offset_min`; readers
     accept v1 files written since cb4dd91.
  b. `_device_facts(adb_base)`: ONE best-effort call `adb_base + ["shell", "getprop persist.sys.timezone; date +%z"]`
     (timeout 15 s; any error → `{}`), called only in the post-commit capture branches of `cmd_push` (after
     `journal.commit()`), passed to `capture_after_push`. Must use `adb_base` (pinned by
     `test_regression_season_device_id_reaches_every_adb_call`).
  c. Extras: after `_extras_scan_merge_and_save` saved → `capture_after_extras_scan` per item (sha1 via `cached_sha1`,
     date_utc) at `<item dir>/<item short_id>.gpcapture.json`; after `push_one_extra` succeeded → `capture_after_push`
     for the item (whole or chunks); `push_title_extras` queries device facts once per title.
  d. mvmeta v2 (OD-4): per chunk add `sha1`/`dedup_key`/`date_utc`/`uploaded_name` when known (`cached_sha1` of the
     chunk, `mkv_date_utc`); v1 keys unchanged; still best-effort.
- Tests: extras capture (whole + split), device facts parsed from a fake runner, FakeAdb without stdout → no facts and
  push still True, every adb argv carries `-s <serial>` when pinned, mvmeta v2 content on the mock device, v1 capture
  files still readable; `test_rollback.py`/`test_baseline_happy_path.py`/`test_extras.py` unchanged and green.
- Acceptance: full suite + smoke; a journal-op instrumentation test proves identical journal records before/after.

### Step 6 — [model: fable] [effort: max] [fallback: opus] The apply engine — the only writer of `gphotos` blocks
- Depends on: Steps 3–5; OD-2.
- Consumed by: Steps 9 (map --apply), 10 (post-fetch merge), 13 (check --apply), 14.
- Files: `main.py` (`gphotos_apply(records, capture=True, dry_run=True)`, `merge_learned_gphotos()`,
  `cmd_dispatch_fetch` post-subprocess hook), `tests/test_gphotos_apply.py` (new).
- Details: input = reduced learned records (sidecar) + capture facts + mapper/check results; fresh `load_library()`
  immediately before patching; per object: ensure a trusted block (create from capture/sha256; replace untrusted),
  fold identity facts, fold account records (P-5 ladder, alternates, approach, `confirmed` only from verified
  map/check/fetch records), set `home_account` (P-4), never touch any other key; dry-run returns a diff report (objects
  changed, fields, conflicts, orphans) and writes NOTHING; `--apply` saves ONCE; never raises (warning line); no web
  markers in output. `cmd_dispatch_fetch` calls `merge_learned_gphotos()` after the child exits (argv/streaming
  unchanged).
- Tests: all four prefixes; chunks/holders/extras; pin replacement; conflicts; stale; wrong-account home_account;
  multi-account copies; no records → no load/save (files byte-identical); alias library; disarm probes (dry-run writes
  nothing; untrusted blocks ignored; capture alone never sets `confirmed`).
- Acceptance: full suite + smoke.

### Step 7 — [model: fable] [effort: max] [fallback: opus] Productize the inventory crawler (incremental, locked, pre-flighted)
- Depends on: Step 2.
- Consumed by: Step 9 (mapper freshness gate), Step 13 (account item cache), 🚦 R0/R1/R3.
- Files: `tools/gp_inventory.py`, `mainfetch.py` (shared `profile_preflight(account)`), `tests/test_gp_inventory.py`.
- Details: (1) take `mvcommon.fetch_session_lock(blocking=False)` (refuse if a fetch runs) and refresh the lock mtime every
  ≤10 min (heartbeat) so warm_profiles/stale-reclaim never pre-empts a long crawl; (2) `profile_preflight`: missing
  profile dir → clear message, no Chrome launch; signed-out landing → stop with the sign-in remediation; (3) incremental
  enumeration: each run appends `{"photo_id","seen_run"}` for every id seen (not only new ones) + a run header/footer;
  the mapper treats ids absent from the latest COMPLETE enumeration as `possibly_deleted`; details only for new or
  errored ids; (4) `--status` (offline): per account ids, detailed, errored, last complete enumeration time;
  (5) record format backward compatible with the files already crawled (tv/movies/anime).
- Tests: fake driver; resume after a crash mid-details; lock held → refuse; heartbeat; incremental run adds only new ids;
  deletion detection; signed-out and missing-profile paths.
- Acceptance: full suite + smoke.

### 🚦 R0 — Prerequisites the user performs (announced; blocks only the dependent live steps)
1. Anime profile signed in (done 2026-09-25) — confirm with `& $PY tools\gp_inventory.py anime --port 9224 --phase enumerate` (no "STOPPED").
2. Others: create + sign in `C:\Media\Utils\ChromeProfile_Others` when the Others account/Pixel exist (IMP-D18 prerequisite). Until then every live `oth-` step is `blocked (profile)`; offline `oth-` code paths are still built and tested.
3. Let the incremental re-crawl finish for tv/movies/anime (§M1) before any mapping verdict.

### Step 8 — [model: fable] [effort: max] [fallback: opus] Read-only device cross-check
- Depends on: Step 3.
- Consumed by: Step 9 (no "missing from the cloud" verdict without it), Step 13, IMP-E5 later.
- Files: `main.py` (`device_inventory(aliases)` helper), `tests/test_device_inventory.py`.
- Details: for each configured device alias (`DEVICE_ALIASES`; skip `<NEW_PIXEL_SERIAL>` placeholders) run read-only
  `adb -s <serial> shell find /sdcard/Media -type f` (+ `stat -c '%s %Y %n'` in batches) → `{remote path, name, size,
  mtime}`; classify each library object by uploaded name: `on_device` (awaiting backup), `freed` (only the
  `.mvmeta.json` sidecar remains), `absent`; offline/unauthorized devices reported, never an error; NEVER writes to the
  device.
- Tests: fake adb outputs (all four categories, chunks, holders, extras); device offline.

### Step 9 — [model: fable] [effort: max] [fallback: opus] Mapper `map_photos` (+ resolutions + repairs)
- Depends on: Steps 2, 3, 6, 7, 8.
- Consumed by: 🚦 R1, 🚦 R3, Steps 10–13 (photo_ids and home accounts), docs.
- Files: `main.py` (`cmd_map_photos` + CLI `map_photos`), `tests/test_map_photos.py`.
- CLI: `map_photos [all|<id>|<prefix>] [--account movies|tv|anime|others] [--devices-only] [--resolutions <csv>] [--repair split-info|names] [--accept-probable] [--apply] [--out <dir>]` — dry-run default.
- Details (per account, all categories; library read-only until `--apply`):
  1. Freshness gate: refuse a verdict (report `stale-inventory`) for any account whose latest complete enumeration is
     older than the newest capture push for that account or older than 24 h — print the re-crawl command.
  2. Match per object: exact name → normalized name (P-6) → wrong-account (the name exists only in another account's
     inventory → candidate `home_account`) → `[short_id]`-tag probable (OD-14).
  3. Cross-check: capture sha1 → dedupKey must equal the item's (else `conflict`, never applied).
  4. Duplicates: same bytes (same dedupKey) → pick one, list the rest as alternates; different bytes (re-split sets) →
     group the object's candidates by taken second (all chunks of one split share it), keep complete sets (every chunk
     name + holder present), choose by dedupKey when capture exists, else the newest complete set (`upload_ms`/taken
     second) with `alternates` recorded; incomplete/unresolvable → `ambiguous` for the user; stale cloud copies are never
     deleted.
  5. Library/cloud inconsistency detectors (IMP-C27): (a) library whole file but the cloud holds `<name> [sid].chunk.NNN`
     items (split_info lost) → `--repair split-info` offer: rebuild `split_info.chunks` from the cloud names + local
     `checksums/<chunk>.sha256` sidecars when present (dry-run diff; applied only with `--repair split-info --apply`;
     anything it cannot prove stays a user decision); (b) uploaded under an old untagged name (`<name><ext>` without
     `[sid]`) → record the actual `uploaded_name` (`--repair names`); (c) partial chunk sets (some chunks in no account) →
     device cross-check (Step 8) → report; (d) object in no account → device cross-check: `on_device` →
     `awaiting-backup`; `freed` → `missing-after-backup` (possible cloud loss); `absent` → `missing-everywhere` — every
     (c)/(d) row is printed in a LOUD block (the local copy of an `archived` entry is a dummy).
  6. Unmatched inventory items → categorized report only (superseded split sets, duplicates, cross-account copies,
     non-MediaVault uploads); never touched.
  7. Outputs (under `~/.mediavault/gphotos/map/<ts>/` or `--out`): `summary.txt` (per account × category counts),
     `mapping.jsonl`, `inconsistencies.csv`, `unmatched_<account>.csv`, and `resolutions_todo.csv` with columns:
     `account, category, manual_id, object, uploaded_name, status, candidates, suggested_photo_url,
     expected_taken_local, decision(url|datetime|not_uploaded|skip), photo_url, date(YYYY-MM-DD),
     time(HH:MM[:SS], 12 h with AM/PM or 24 h), gmt(+08:00), note` (UTF-8 BOM, CRLF).
  8. `--resolutions <csv>`: validates rows; `url` rows → hint (photo_id from the URL); `datetime` rows → hint (local time
     + offset); hints are verified by `check_photos hinted` before they become confirmed; `not_uploaded` rows are listed
     in the C27 report.
  9. `--apply` → recompute the mapping (offline, cheap), append the accepted rows to the learned sidecar (source `map`,
     via Step 3f helpers — the durable record survives an IMP-C24 lost update) and run the apply engine (Step 6) for
     `mapped`/`mapped-normalized`/resolved duplicates/wrong-account home accounts (+ repairs only when requested). A
     dry-run never appends to the sidecar.
  10. `--devices-only` → only the Step 8 device report (§M2).
- Tests: synthetic library + inventory across all four prefixes reproducing every class above (incl. a 31-episode
  wrong-account block, whitespace near-match, re-split duplicate sets, untagged name, missing last chunk, freed vs on
  device, stale inventory); dry-run writes nothing to the library; disarm probes.
- Acceptance: full suite + smoke.

### 🚦 R1 — Real-library mapping DRY-RUN (user reads the report; no apply) — §M2–M3
Look for: per-account/category counts, the LOUD possible-loss block, the wrong-account list, near-matches, duplicates,
inconsistencies. Defects found here become Step 9b fix-ups (recorded in PROGRESS) before anything else.

### Step 10 — [model: fable] [effort: max] [fallback: opus] Fetch plumbing: account-grouped routing, queue builder, harvester + learn-on-fetch
- Depends on: Steps 2, 3, 6, 9.
- Consumed by: Steps 11–14.
- Files: `mainfetch.py`, `main.py` (fetch/fetch_restore CLI: `--manual` token forwarded only when present),
  `tests/test_fetch_harvester.py`, `tests/test_fetch_routing.py`.
- Details:
  a. `cmd_fetch_route`: resolve targets (unchanged `resolve_targets`/`resolve_target_ids`), compute each object's account
     (P-4), group by account, run one Chrome session per account in a fixed order under the existing
     `fetch_session_lock`; profile pre-flight per account; existing batch print lines unchanged.
  b. `build_fetch_queue(entry, restore_folder, ctx)` — legacy keys/values/order IDENTICAL to today's inline builder
     (parity test against a frozen oracle copy) + a `locator` dict (label, sha256, sha1/dedup_key from trusted block or
     capture, uploaded_name, taken_ms/date_utc, tz offsets, account, photo_id + alternates, approach.best, category,
     short_id, S2/S3/S4 query lists).
  c. `harvest_downloads(queue, ctx)` — today's loop verbatim (timeouts, extension, prints) with: processed files persisted
     across rounds (B10); after a SHA-256 match `mvcommon.cached_sha1(path)` gives the SHA-1 → learned record (proof
     `sha256`; photo_id only when the item's `_trigger` record dedupKey equals it).
  d. Seam `_run_locator_phase(driver, queue, ctx)` (no-op here; filled by Step 12) before today's two attempts, which run
     unchanged for still-pending items; `EXACT_LOCATOR_ENABLED` (P-14).
  e. A8: delete `wait_for_download`, `automation_download_file`, `build_download_queue`; `build_extras_entries` forwards
     `gphotos` + labels.
  f. `parse_fetch_options(argv)` (new; `parse_fetch_args` unchanged) → `{manual: bool}`; main.py forwards `--manual` only
     when given (spawned argv otherwise byte-identical).
- Tests: routing (X-Files-like wrong-account objects fetched in the right account; mixed batch → two sessions), parity,
  harvester golden transcript + `webui.server._parse_progress` counts unchanged, learned records, A8 deletions,
  `EXACT_LOCATOR_ENABLED=False` call sequence identical to today, `trigger_download` source digest pinned.
- Acceptance: full suite + smoke; `tests/test_trigger_download_retry.py`, `test_session_detector.py`,
  `test_cli_parsers.py` untouched and green.

### Step 11 — [model: fable] [effort: max] [fallback: opus] Download hygiene: staging dir, fetch journal, crash cleanup, verify_restore tempdir
- Depends on: Step 10; OD-13.
- Consumed by: Steps 12–14; 🚦 R2 (§M6–M7).
- Files: `mainfetch.py`, `main.py` (`cmd_verify_restore(manual_id, temp_dir=None)` + CLI), `tests/test_fetch_hygiene.py`.
- Details: after `init_driver` (tab on about:blank) create the staging dir (OD-13) with marker `.mediavault_staging` and
  route downloads there (P6: Browser → Page fallback); on failure keep `~/Downloads` with deletions disabled + one
  warning. Fetch journal `<staging>\journal\<run_id>.json` (atomic rewrite): run start, pid, per object
  `located/triggered/downloaded/verified/staged/failed`, trigger times, photo_ids, expected names. On start: finish any
  unfinished journal — delete its `*.crdownload` and unmatched files inside the staging dir, mark it cleaned. On
  KeyboardInterrupt/exception: `driver.quit()`, terminate the Chrome process launched by `init_driver` (handle kept as an
  attribute; additive), then clean this run's partials. Best-effort CDP cancel when an event channel exists (P6). At the
  end of each object: wrong/duplicate downloads in staging deleted and listed (name + size). `verify_restore` accepts
  `tempdir` (reuses `_parts_base` exactly like `cmd_restore`).
- Tests: redirect params; fallback path deletes nothing in `~/Downloads`; crash mid-download → next run cleans only
  journal-recorded/staging files; a pre-existing unrelated file outside staging survives; verify_restore tempdir; disarm
  probes for never-delete-foreign.

### Step 12 — [model: fable] [effort: max] [fallback: opus] [candidates: 3] 🔀 Locator engine (S0–S4) + read-only `locate` diagnostic
- Depends on: Steps 2, 5, 6, 9, 10, 11.
- Consumed by: Steps 13 (check engine = `locate_object`), 14, 🚦 R2/R3.
- Contract (all candidates; judged): `locate_object(driver, locator, ctx)` read-only → `{found, photo_id, record, proof,
  stage, approach, candidates_seen, pages_opened, searches, elapsed, per_approach}`; `_run_locator_phase` = locate →
  `trigger_verified` (focus + Shift+D) → confirm a download started in the staging dir within `TRIGGER_CONFIRM_S` →
  `item["_trigger"]`; stage rules per P-8 with `approach.best` first and alternates tried after the primary photo_id;
  verification per P-5 (dedupKey when the key is known, else normalized exact name from the record + panel); chunk sets:
  one S1 day search serves all chunks (shared second), each chunk picked by dedupKey or exact `.chunk.NNN` name — never by
  tile index; `check_session_alive` after every navigation; `stale` sidecar records for dead photo_ids; per-run record
  cache so a season opens each item at most once; bounded caps; no RPC calls (P-9); no web markers.
  `python mainfetch.py locate <id> [episodes <r>] [--fetchExtras]` — read-only, no downloads, no writes, prints per object
  stage/proof/approach/timing (ids shown only on the user's terminal).
- Candidate approaches (all `[candidate-model: fable]`; on a degraded run all three run on opus — the approach is the
  differentiator, keep all three):
  - **A — DOM + page record:** tiles from the visible DOM (`a[href*="photo/"]`, checkVisibility, width>40), each candidate
    verified by loading `https://photos.google.com/photo/<id>` and `gpweb.page_record`; Shift+D on the verified item page
    (click-path fallback if no download starts).
  - **B — passive network capture first:** performance logging requested through an additive `init_driver(...,
    perf_log=True)`; records for all result tiles read from the UI's own search/item-info responses (`gpweb.rpc_records`)
    — no per-item page loads; trigger by clicking the verified tile + Shift+D; capture unavailable → skip to S4.
  - **C — hybrid arbitration:** DOM-first discovery, capture used when available to pre-verify and to read names, page
    record verification whenever capture yields nothing; one record cache fed by both.
- Scenario tests every candidate must pass (fake Google Photos): same-second batch of 10 (correct pick, 9 never
  triggered); wrong-account object fetched in its home account; legacy object by normalized name (UK panel, current year,
  collapsed spaces); stale photo_id → S1/S2; re-split duplicate set → dedupKey/newest-set rule; chunk set partially staged
  → only missing chunks fetched; no-DateUTC object → S2; nothing verifiable → no Shift+D in S0–S3 and S4 per OD-5; logged
  out → SessionExpiredError + remediation; Shift+D ineffective on direct load → alternate path; locate never sends
  Shift+D; `EXACT_LOCATOR_ENABLED=False` → today's sequence.
- Judge criteria (ranked): (1) contract + scenario tests pass with zero edits to existing tests and pins intact;
  (2) 🚦 live read-only `locate` results (§M4: S02E03 content-verified; S02E02 legacy; one X-Files episode in MOVIES; one
  movie chunk set; one anime episode) — correctness, wall-clock, pages; (3) resilience to DOM/RPC/locale changes (falls
  through, never downloads wrongly); (4) efficiency (pages + searches per object on a season batch); (5) clarity/size.
- 🚦 G12: after judge-v2 writes `.candidates/c25-step-12/DECISION.md`, STOP; relay; optional live probe per worktree
  (§M4); the user picks; merge; full suite + smoke; archive tags; remove worktrees. Commit PROGRESS after each candidate,
  after judging and after the pick.

### Step 13 — [model: fable] [effort: max] [fallback: opus] `check_photos` — download-free verification, sweep and confirm
- Depends on: Steps 6, 8, 9, 12 (winner).
- Consumed by: 🚦 R2/R3; IMP-E5 later.
- Files: `mainfetch.py` (`cmd_check` engine, `__main__` `check`), `main.py` (`cmd_check_photos` + CLI; spawns
  `sys.executable <abs mainfetch.py> check …` with the streaming idiom; runs the apply engine after `--apply`),
  `tests/test_check_photos.py`.
- CLI: `check_photos <scope> [--approaches best|all] [--account a] [--recheck] [--apply | --apply-run <run dir|latest>] [--limit N] [--pace S] [--max-minutes M]`
  where scope = item id (movie/episode; alias resolves) · season_map id · `series:<any season id | show folder>` · id
  prefix · `account:<movies|tv|anime|others>` · `pending` (pushed but not confirmed) · `hinted` (manual resolutions) · `all`.
- Details: series resolution = the show folder (parent of the season folder when the season folder name matches the
  season-folder patterns, or when ≥2 season_maps share the parent; else the folder itself) → every season_map under it;
  ALWAYS print the resolved seasons and object count before running (multi-year id years: IMP-U7 memory). Per object
  and per approach the outcome is one of `exactly-one` (one verified item), `ambiguous(n)` (n candidates, none or several
  verified), `not-found` (0 candidates), `wrong-item` (every candidate proven a different item), `error`; `best` = stop at
  the first `exactly-one`; `all` = evaluate every approach and record `approach.ok`/`approach.failed`. Download-free:
  identity via page record (dedupKey) or normalized exact name. A dry-run writes its verified records only to its run
  directory (`results.jsonl`); `--apply` runs the check and appends + applies; `--apply-run <dir|latest>` applies a
  REVIEWED dry-run's records without searching again (the run's recorded scope must equal the given scope, else refuse;
  `latest` = newest completed run for that scope; the apply engine re-checks every pin, so stale runs cannot corrupt a
  node). Flags: account mismatch
  (found in a non-home account), library/cloud inconsistencies (C27), `confirmed` on success (upload_ms + verified).
  Per-account sequential sessions, pacing (default 3 s + jitter), circuit breaker (5 consecutive errors), lock +
  heartbeat, resumable (sidecar + per-run progress file; re-run skips confirmed objects unless `--recheck`), Ctrl+C safe,
  per account × category report + JSON under `~/.mediavault/gphotos/check/<ts>/`.
- Tests: every scope incl. series resolution for flat and nested layouts, anime glued ids, `oth-` (list-capable roots),
  pending/hinted; best vs all; resumability; never Shift+D; apply via the engine.

### Step 14 — [model: fable] [effort: max] [fallback: opus] `--manual` mode, robust fetch_restore resume, web behavior
- Depends on: Steps 10–13.
- Consumed by: Step 15 (smoke/pins), 🚦 R2 (§M9–M10), Step 16 (README/OPERATIONS_QA wording of the identification block).
- Files: `mainfetch.py`, `main.py` (`cmd_fetch_restore(..., manual=False)`), `webui/server.py` (no functional change
  expected; verify), `tests/test_fetch_manual.py`.
- Details: after all stages fail for an object — without `--manual`: print a clear identification block (account, exact
  uploaded name, local date/time, dedupKey/SHA-1, size, what was tried), clean up, existing return/exit contracts
  unchanged; with `--manual` (CLI only): the same block + open the browser at the best candidate or search page, watch
  the staging dir for up to 30 min per object (no stdin), SHA-256-verify the user's download, record a learned `manual`
  proof, continue. The web UI never passes `--manual`; its job output shows the block and the CLI command to run.
  fetch_restore resume: verified objects already in `restore/` are skipped (as today); the fetch journal + learned
  sidecar make a re-run go straight to S0; restore unchanged.
- Tests: manual flow with the fake driver writing the file after a delay; timeout; web action path unchanged (smoke).

### Step 15 — [model: opus] [effort: high] Smoke coverage, regression pins, mutation probes
- Depends on: Steps 2–14.
- Consumed by: 🚦 R2 (the pack starts only on a green suite), Step 18 (checklist), the PR test plan.
- Files: `tests/smoke/test_smoke_all_commands.py`, `tests/test_c25_regression_pins.py`.
- Details: smoke (< 30 s idle): prep/push capture present; fetch + apply merge with a seeded sidecar; `map_photos` dry-run
  and `check_photos --approaches best` against fakes in-process; alias sweep over the new iterators/apply/check; all four
  prefixes. Pins: `parse_fetch_args` shape; spawned fetch argv; web markers; `trigger_download` source digest;
  `calculate_file_hash` SHA-256/return contract; `ENTRY_TYPE_KEYS` literal; mvmeta v1 keys. Mutation/disarm probes for
  every new guard (pin rule, verification-before-trigger, dry-run-writes-nothing, never-delete-foreign, home-account
  routing, freshness gate, capture-never-confirms) — each recorded in PROGRESS as `disarm N/N ✓`.
- Acceptance: full suite + smoke green; smoke runtime recorded.

### 🚦 R2 — Live verification pack (user-run or user-authorized) — §M4–M11 (and optional §M13)
### 🚦 R3 — Real-library APPLY (dry-run → user reads → `--apply`) — §M12

### Step 16 — [agent: architect] [model: opus] [effort: high] Documentation
- Depends on: Steps 2–15 and R2/R3 results (redacted).
- Files: `ARCHITECTURE.md` (§8 rewrite incl. fixing §8.1 profiles and §8.3 drift; §6.3 `gphotos` block; §6.5 capture +
  mvmeta v2; §5 entry points; §7.5 capture; §10 walkthrough; §12a rollback-neutral addendum; §13 tests; §14 constants;
  §16/§17 strike C5/A8/B10; §18), `README.md` (fetch bullet; commands `map_photos`, `check_photos`, `verify_restore
  tempdir`, `--manual`; `mainfetch.py locate`; crawler; state dirs), `docs/OPERATIONS_QA.md` (new §6b Q&As),
  `docs/testing-strategy.md` (fake Google Photos, hermetic redirects), `docs/README.md` (index),
  `docs/feature-fetch-datetime/INVENTORY_AND_CAPTURE.md` (status update).
- Known drift to fix (verified against the code 2026-09-25; re-verify line refs at execution time):
  - ARCHITECTURE §8.1 lists three profiles and `ID_PREFIX_PROFILE` without `("oth", "others")`; the code has four
    (`CHROME_PROFILES["others"] = C:\Media\Utils\ChromeProfile_Others`, mainfetch.py:32–40; the directory does not exist
    yet — IMP-D18 prerequisite). Section-heading line refs are stale.
  - ARCHITECTURE §8.3 says the attempt-1 query is typically `<filename> [<short_id>].mkv`; the code's attempt 1 types the
    plain library `filename` for a whole file (no `[short_id]` — the uploaded name has it, hence F25's zero results) and
    the exact chunk/holder name for split objects; attempt 2 types `search_term` (else `filename`) and clicks tile index =
    chunk index. The primary selector `a[href*='./photo/']` matches nothing on search pages (F14), so the
    `background-image` XPath fallback does the clicking. `trigger_download` is at mainfetch.py:129 (doc says 150–216).
  - README "What it does" (`fetch` — "searches Google Photos for the embedded short-id … into `~/Downloads`") and the
    command table (`fetch`: "`tempdir` stages downloads off the media volume"): Chrome's download always lands in
    `~/Downloads` on C: first; only the verified move goes to `tempdir` — true until Step 11's staging dir; rewrite both
    to the new behavior.
- Q&As: why Google Photos shows Feb 5, 2022 for a file pushed in 2026; why a season / all chunks share one second; what
  the dedupKey is and how to check it (`adb shell sha1sum` → base64url); how fetch finds the exact item now; why some items
  live in another account and how routing handles it; how to backfill (crawl → map → resolve → apply); how to confirm
  new archivals; what `archived` vs `confirmed` mean; what to do when fetch can't find something (`--manual`,
  resolutions, kill switch); why the movies account shows UK dates; how to read the possible-loss report.
- Acceptance: every documented command replayed through the real parsers; no real ids.

### Step 17 — [model: opus] [effort: medium] IMP bookkeeping on implementation
- Depends on: Step 16 and the R3 outcome (C27 status). Consumed by: Step 18, the PR body.
- C25 → `done (feature/imp_c25_fetch_exact_gp_item, PR pending)`; C26 → done; C27 → status per the R3 findings (done if
  every case is resolved or explicitly accepted by the user, else in_progress with the open list); C5/A8/B10 → done
  (absorbed); B4 note; C24 cross-reference line; synergy lines on E5/G2/S7/X1/X5/C7/D9; PRIORITY.md + graph (+ node
  validation); three-file consistency.

### Step 18 — [model: sonnet] [effort: medium] Final verification checklist → PR (git-agent) → 🚦 Checkpoint 1
- Depends on: Steps 0–17 done, R2/R3 recorded. If `git log --oneline HEAD..origin/main` is non-empty, the orchestrator
  first re-dispatches **Step 1 (opus)** — a merge never runs inside this checklist step.
- Checklist (read-only checks + one mechanical PROGRESS edit — the only reason this step may run on sonnet): full suite;
  privacy grep `git grep -nE "AF1Qip[A-Za-z0-9_-]{8,}" -- tests docs improvements` → only synthetic ids
  (`AF1Qip…FAKE…`/`AF1QipTEST…`); `git grep -n "hd9mASyG9pJhisPXFS3Zr7Mqr28"` → only the published S02E03 example;
  `git diff origin/main..HEAD --stat` touches only the files named in this plan; PROGRESS SHAs back-filled; smoke LAST.
  Any red item → STOP and report (no fixes in this step).
- PR: the orchestrator dispatches git-agent to push and open the PR with the title/body below (body = summary →
  `## Original task prompt` copied byte-for-byte from the appendix → trailer); then STOP at 🚦 Checkpoint 1.

---

## Consumer Impact Analysis

(Line numbers on this branch at 70635cb; function names are authoritative.)

| # | Site | Line(s) | Access | Verdict | Why / fixing step |
|---|------|--------|--------|---------|-----|
| 1 | `ENTRY_TYPE_KEYS` | main.py:171 | registry doc | needs-fix | document `gphotos` (Step 4); required sets unchanged |
| 2 | `cmd_prep` capture hook | ~1519 | post-commit capture | safe | unchanged; library untouched (test pins it) |
| 3 | `scan_extras_folders` / `merge_extras_into_title` | 5234 / 5370 | item hash/data | safe | no library identity writes; identical-rescan no-op tests preserved |
| 4 | `_extras_scan_merge_and_save` | 5493 | post-save | needs-fix | extras capture (Step 5) |
| 5 | `write_remote_mvmeta` | 5661 | chunks → remote JSON | needs-fix | v2 per OD-4 (Step 5) |
| 6 | `push_one_extra` / `push_title_extras` | 5791 / 6029 | upload + status | needs-fix | post-success capture + device facts (Step 5) |
| 7 | `cmd_push` capture hooks | ~6429, ~6555, ~6569 | snapshot + post-commit capture | needs-fix | device facts post-commit (Step 5) |
| 8 | `_carry_out_flac_track` | 851 | holder record | safe | holder sha1 via `cached_sha1` (hashed in-process) |
| 9 | `cmd_push` `_chunk_hashes` / resume | ~6410 | chunk filename/hash | safe | named keys |
| 10 | `cmd_replace` / `replace_one_extra` | 6892 / 7089 | canonical promotion | safe | untouched |
| 11 | `_dangling_evidence` / `cmd_verify_library` | 7388 / 7457 | named keys | safe | unchanged by design |
| 12 | `_verify_title_extras_restore` / `cmd_verify_restore` | 7761 / 7814 | chunk filename/hash | safe / needs-fix | tempdir parameter added (Step 11) |
| 13 | `cmd_restore` / `restore_one_extra` | 7895 / 8308 | chunks, holder, bless | safe | NOT modified (change-gated, authoritative) |
| 14 | whole-library iterators (`cmd_sort` 8540, `cmd_local_status` 8593, `cmd_scan_unprepped` 8707, `collect_reclaimable` 10005, `items_payload` 10256, enrich/rename/migrate/normalize, webui `/api/library`) | — | named keys | safe | nested block invisible; guard test (Step 4) |
| 15 | `cmd_dispatch_fetch` | 9519 | spawn + stream | needs-fix | post-fetch apply hook; `--manual` forwarded only when given (Steps 6/10) |
| 16 | `cmd_fetch_restore` + CLI `fetch`/`fetch_restore`/`verify_restore` | 9571 / ~12149 / ~12170 / ~11941 | args | needs-fix | `--manual`; tempdir for verify_restore (Steps 10/11/14) |
| 17 | `mainfetch.fetch_single_entry` | 267 | queue + attempts + harvester | needs-fix | Steps 10–14 |
| 18 | `mainfetch.build_download_queue` / stubs | 531 / 240 / 245 | dead | removed | A8 (Step 10) |
| 19 | `mainfetch.build_extras_entries` | 612 | synthetic entries | needs-fix | forward `gphotos` + labels (Step 10) |
| 20 | `mainfetch.cmd_fetch_route` | 678 | one profile per batch | needs-fix | per-object account grouping (Step 10) |
| 21 | `mainfetch.trigger_download` | 129 | legacy S4 | safe | byte-for-byte; pinned |
| 22 | `mainfetch.parse_fetch_args` | 744 | 4-tuple | safe | unchanged; new `parse_fetch_options` |
| 23 | `webui/server.py` `_parse_progress` / `_run_fetch_restore` | 375 / 234 | stdout markers / action | safe | markers pinned (P-13); web never passes `--manual` |
| 24 | `tools/gp_inventory.py` | — | own parsers | needs-fix | delegate to `gpweb` (Step 2), lock/incremental (Step 7) |
| 25 | `gpcapture.py` | — | capture v1 | needs-fix | v2 + extras (Step 5) |
| 26 | tests: `test_gp_capture.py`, `test_gp_inventory.py`, `test_entry_schema_guard.py`, smoke | — | — | extend | Steps 2–5, 15 |
| 27 | tests pinning today's behavior (`test_rollback.py`, `test_baseline_happy_path.py`, `test_extras.py`, `test_trigger_download_retry.py`, `test_session_detector.py`, `test_cli_parsers.py`, push tests, `test_prep_push_rep_season_enrich.py::test_regression_season_device_id_reaches_every_adb_call`) | — | — | safe | must pass unchanged |
| 28 | `profile_for_id` consumers (fetch route, crawler, warm_profiles) | mainfetch.py:671 | prefix map | safe | still the default; `home_account` overrides per object |

New contracts introduced: the `gphotos` block (P-1), capture v2, the learned sidecar JSONL, inventory `seen_run`
records, the mapping/check report files, the staging dir + fetch journal, `map_photos`/`check_photos`/`locate`.

## Risks and edge cases

- Google UI/DOM/locale drift → page record primary (P-7); every stage falls through; S4 legacy unchanged; kill switch.
- Wrong-account objects (31 X-Files in MOVIES; Kuroko copies in TV) → P-4 routing + check flags; mixed batches spawn one
  session per account (longer runs).
- Enumeration timing (items backed up after the crawl) → incremental re-crawl + freshness gate + device cross-check before
  any "missing" verdict.
- Possible cloud loss on `archived` items (local dummy) → LOUD reports, never auto-fixed; user decides (C27).
- Re-split duplicate chunk sets → set rule + alternates; SHA-256 after download is authoritative.
- IMP-C24 lost update during a real `--apply` → run when idle; the sidecar makes re-apply idempotent.
- Long crawls/sweeps vs fetch/warm-up collisions → lock + heartbeat; crawler/check yield if the lock is stolen.
- Shift+D on a directly loaded item page unverified → download-start confirmation + click-path fallback; 🚦 R2 (§M6).
- CDP download redirect/events in attach mode unverified → fallback without deletions; 🚦 R2 (§M6–M7).
- Account safety (search volume) → no RPC calls, pacing, circuit breaker, `best` sweep default.
- IMP-U7: no `*_enrich` / `enrich_metadata --library series` in any test or manual step.
- Another Claude session in the repo → pathspec commits; worktree isolation; no branch switches in the main folder.
- Machine load: the full suite took 12 min and smoke 63 s while crawls ran (P10) — judge timing only on an idle machine.

## Verification

- `& $PY -m pytest tests/test_gpweb.py tests/test_gp_inventory.py tests/test_gp_capture.py tests/test_gphotos_primitives.py tests/test_gphotos_apply.py tests/test_device_inventory.py tests/test_map_photos.py tests/test_fetch_routing.py tests/test_fetch_harvester.py tests/test_fetch_hygiene.py tests/test_check_photos.py tests/test_fetch_manual.py tests/test_c25_regression_pins.py tests/test_entry_schema_guard.py -q`
- `& $PY -m pytest tests/test_rollback.py tests/test_baseline_happy_path.py tests/test_extras.py tests/test_trigger_download_retry.py tests/test_session_detector.py tests/test_cli_parsers.py -q` (unchanged files, must be green)
- `& $PY -m pytest tests -q`
- `& $PY -m pytest tests/smoke -q`

## Out of scope

Rewriting DateUTC (D1); agent effort (D5); the U6 token-brackets branch (D6); issuing internal RPCs (OD-9); gphotosdl
adoption (IMP-G2/S7); multi-account replication (IMP-X1 — the `accounts` map is ready for it); fixing IMP-C24 itself;
deleting stale/duplicate cloud items; `cmd_restore`/`restore_one_extra` changes; a new `status` value; sidecar/report
compaction; tzdata; the Others Pixel serial placeholder (IMP-D18).

---

## Branch, PR and checkpoints

- **Branch:** `feature/imp_c25_fetch_exact_gp_item` (worktree `C:\Users\harin\PycharmProjects\MediaVault-c25`). PR #60
  (`feature/imp_c25_interim_capture`) lands the interim capture/crawler on main first (user-gated); Step 1 merges main back.
- **PR title:** `feature: fetch the exact Google Photos item (content key, taken-second locator, per-object account, map/check) — IMP-C25/C26/C27`
- **PR body order:** auto-generated summary (Summary / Changes / Test plan incl. redacted real-library dry-run + apply
  numbers) → `## Original task prompt` with the COMPLETE verbatim prompt from the appendix → `🤖 Generated with [Claude Code](https://claude.com/claude-code)`.
- **🚦 Checkpoint 1:** STOP after opening the PR — merging to main is the user's call.
- **🚦 Checkpoint 2:** after merge and only on approval: annotated tag `archive/feature/imp_c25_fetch_exact_gp_item`
  (merge info + revive steps), push the tag, delete the branch locally and remotely; remove the worktree
  (`git worktree remove C:\Users\harin\PycharmProjects\MediaVault-c25`).

## Manual tests (user-run; each is announced as a 🚦 step before it is needed)

(PowerShell, in `C:\Users\harin\PycharmProjects\MediaVault-c25`:
`$PY = "C:\Users\harin\PycharmProjects\MediaVault\.venv\Scripts\python.exe"`. Close every MediaVault Chrome profile window
first. Do not run prep/push/replace/restore in the main folder while an `--apply` runs. No `*_enrich` /
`enrich_metadata` on series (IMP-U7).)

- **M0 — config:** only if a command needs API keys (none of the C25 commands do; `main.py web` works on defaults):
  `Copy-Item C:\Users\harin\PycharmProjects\MediaVault\mvconfig.json C:\Users\harin\PycharmProjects\MediaVault-c25\`.
- **M1 — crawl status / incremental re-crawl:** `& $PY tools\gp_inventory.py tv --port 9225` · `movies --port 9223` ·
  `anime --port 9224` (others: blocked until the profile exists); `& $PY tools\gp_inventory.py tv --status`. Look for:
  every account "enumeration complete", 0 errored items.
- **M2 — device cross-check (read-only):** `& $PY main.py map_photos all --devices-only` → lists objects still on a Pixel
  (awaiting backup) vs freed.
- **M3 — mapping dry-run (🚦 R1):** `& $PY main.py map_photos all` → read `summary.txt`, the LOUD possible-loss block,
  `inconsistencies.csv`, `resolutions_todo.csv`. Expect roughly tv ≈1,065 + 31 wrong-account, movies ≈688 + 19 normalized,
  anime ≈157/158.
- **M4 — locate probes (read-only, 🚦 G12):** `& $PY mainfetch.py locate tv-en-2014-siliconvalley-s02e03` (expect S0/S1,
  proof dedupkey), `… s02e02` (filename proof), one X-Files episode from the wrong-account list (expect account movies),
  `… tv-en-2019-chernobyle02` (3 chunks, one shared second), one split movie, one anime episode.
- **M5 — check samples:** `& $PY main.py check_photos tv-en-2014-siliconvalley-s02 --approaches all` ·
  `check_photos series:tv-en-2014-siliconvalley-s02` (prints the resolved seasons first) ·
  `check_photos account:anime --limit 20`.
- **M6 — fetch direct (S0):** size check first
  (`& $PY -c "import mvcommon;e=mvcommon.load_library()['tv-en-2014-siliconvalley-s02e02'];print(e['tech_spec'].get('size_bytes'))"`),
  then `& $PY main.py fetch tv-en-2014-siliconvalley-s02e02 tempdir D:\MV_fetch` → expect S0, download into
  `D:\MV_fetch\_mv_staging\tv`, `✅ MOVED`, learned record; `& $PY main.py verify_restore tv-en-2014-siliconvalley-s02e02 tempdir D:\MV_fetch`.
- **M7 — chunk set + interrupt:** `& $PY main.py fetch tv-en-2019-chernobyle02 tempdir D:\MV_fetch` → 3 verified chunks;
  interrupt once with Ctrl+C mid-download, re-run → partials cleaned (note whether the log says "cancelled via CDP" or
  "Chrome terminated"), verified chunks not re-downloaded.
- **M8 — wrong-account fetch:** fetch one X-Files episode from the list → runs in the MOVIES profile.
- **M9 — fetch_restore end-to-end + web:** `& $PY main.py fetch_restore <small archived id> tempdir D:\MV_fetch` →
  `& $PY main.py check <id>` → `& $PY main.py replace <id>`; then `& $PY main.py web` → Fetch & Restore a small title →
  progress ring correct.
- **M10 — manual mode:** `& $PY main.py fetch <id listed as missing> --manual` → follow the printed block.
- **M11 — new archival + confirm:** `prep` + `push … device series` a new small MKV (from the worktree), wait for backup,
  `& $PY main.py check_photos pending --apply` → the node shows `confirmed`, approach `taken_second+dedupkey`/`photo_id`.
  Include C1c: Daredevil S01E01 `[4a9028]` should read May 4, 2024 7:18 AM GMT+08:00 (taken_ms 1714778282000).
- **M12 — real-library apply (🚦 R3; no other MediaVault command running — OD-2):** back up the libraries first
  (`Copy-Item C:\Media\library_*.json D:\MV_lib_backup\<date>\`); `& $PY main.py map_photos all --resolutions <filled csv>`
  (dry-run) → review → the same command + `--apply`; then per account `& $PY main.py check_photos account:<a> --approaches best`
  (dry-run, long; resumable) → review the report → `& $PY main.py check_photos account:<a> --apply-run latest` (no new
  searches); finally `& $PY main.py check_photos hinted` → review → `--apply-run latest`. Expect: every mapped object
  `confirmed`, the X-Files episodes with `home_account` movies, the C27 list unchanged unless the user resolved rows.
- **M13 — kill switch sanity (optional):** set `EXACT_LOCATOR_ENABLED = False` locally (do not commit), fetch → today's
  behavior; revert.

## Suggested next tasks after IMP-C25

1. **IMP-U7** (Band 0, not gated — the "Headline NEXT"; also required before enriching series again).
2. **IMP-C24** (🚦 change-gate ruling; then switch the apply engine to `save_library_atomic`).
3. **IMP-C27 follow-through** — resolve every possible-loss case the mapping/check surfaced (user decisions).
4. **IMP-C22**, **IMP-U8** (Band 0, not gated).
5. **IMP-E5** phone cleanup — now has a safe signal (`gphotos.accounts[a].confirmed`).
6. **IMP-D23** (prep re-hash on resume).
7. **IMP-X1 → X5** (replication + canary; the `accounts` map and photo-id sentinels are ready).
8. **IMP-G2 → S7** (fetch engine hardening; photo-id addressing).
9. **IMP-B4** remainder; **IMP-C7** (verify `set_uploaded` via `check_photos`); **IMP-D9** (search_term rebuild informed by
   the mapped uploaded names).

---

## Appendix A — IMP registration text (Step 0c, verbatim into `improvements_tierC.md`)

### IMP-C25: Fetch the EXACT Google Photos item — content key + taken-second locator + remembered photo_id + per-object account; map/check/confirm the archived library

- Category: other
- Priority: high
- Files: `gpweb.py` (new), `gpcapture.py`, `mvcommon.py`, `main.py`, `mainfetch.py`, `tools/gp_inventory.py`, tests, docs. Plan: `docs/feature-fetch-datetime/PLAN.md`; research: `docs/feature-fetch-datetime/RESEARCH.md`.
- Current behavior: fetch types filename/`search_term` queries, clicks a tile by position, downloads it and routes by SHA-256; wrong downloads are left in `~/Downloads` (RESEARCH F10). Text search is not an identity (F16/F18/F25), a release batch shares one second (F13), the primary tile selector is dead on search pages (F14), fetch routes by id prefix although 31 archived X-Files episodes live in the MOVIES account, and `tempdir` does not keep downloads off C:.
- Proposed change: SHA-1 content key (= dedupKey) and DateUTC captured at prep/push (interim capture, PR #60); per-object versioned `gphotos` identity block with per-account learned record, approach term and confirmed-in-cloud marker; inventory crawl → mapping (normalized names, dedupKey cross-checks, re-split sets, wrong-account, device cross-check) → user resolutions → `--apply`; `check_photos` (download-free verification by item/season/series/account/pending/all); evidence-ordered waterfall with today's flow as the unchanged last resort; per-object account routing; MediaVault-owned staging dir + fetch journal + crash cleanup; `--manual`; resumable fetch_restore.
- Rationale: identity by content, proven before any bytes move; every archived object becomes directly addressable.
- Goal: every archived object fetchable by direct photo_id in its real account; new archivals confirmed after backup; zero regression when data is absent.
- Effort estimate: large
- Risk: medium — rewrites fetch selection behind a kill switch with today's flow as last resort; capture is post-commit (rollback-neutral, OD-3); `gphotos` is an optional nested block (ENTRY_TYPE_KEYS doc + guard); library writes through one apply engine (IMP-C24 note).
- If skipped: fetch keeps guessing by text and tile order; 31 wrong-account episodes stay unfetchable; possible cloud-loss cases stay invisible until a restore fails.
- Cross-references: absorbs C5, A8, B10; partial B4; fixes C26; tooling for C27; synergies E5, G2/S7, X1/X5, C7, D9; interplay C24.
- Status: in_progress (feature/imp_c25_fetch_exact_gp_item)

### IMP-C26: Fetch routes by id prefix — objects that live in another Google account are unfetchable

- Category: bug
- Priority: high (Band 0 — breaks restore for real archived items)
- Files: `mainfetch.py` (`profile_for_id`, `cmd_fetch_route`)
- Current behavior: `mainfetch.profile_for_id` picks the Chrome profile from the id prefix; the 2026-09-25 mapping found 31 X-Files episodes (`tv-en-1994/1995/1996-xfiles`, seasons 2–4) in the MOVIES account (Kuroko's Basketball copies also sit in the TV account besides the anime account).
- Proposed change: learned per-object `home_account` and account-grouped fetch/check sessions (IMP-C25 P-4).
- Effort estimate: medium (inside IMP-C25) · Risk: low — the prefix default still applies when nothing is learned.
- If skipped: those 31 episodes cannot be restored.
- Status: in_progress (fixed by IMP-C25)

### IMP-C27: Library ↔ Google Photos inconsistencies on archived items — possible cloud loss

- Category: bug
- Priority: high (Band 0 🚦 — per-case user decisions; the local copy of an `archived` item is a dummy)
- Files: data (library ↔ cloud); tooling in IMP-C25 (`map_photos`, `check_photos`, device cross-check)
- Current behavior (verified 2026-09-25 13:30 after the incremental re-crawl, a day-search of each release day, and a read-only per-id search of both Pixels): (a) library says whole file but the cloud holds chunks (The Wire S02E12, Dark S01E10, BSG S01E11 — split_info lost; the Pixel still holds the Dark/BSG `.mvmeta.json` sidecars, which mirror split_info and can seed the `--repair split-info`); (b) uploaded under an old untagged name (Fanaa); (c) last chunk missing from the cloud and from both Pixels (A Tale of Two Sisters #10, The Wailing #9); (d) **13 Friends episodes in no account**: S04E16–19 (pushed 2026-09-03 to FA75V0303405 — only their `.mvmeta.json` sidecars remain on the Pixel = pushed, then freed, never backed up), S05E15/16/18/19/20 and S06E21–24 (absent from both Pixels); the day search of each release day (Jan 19/20/22, 2025) lists every other episode of those seasons but not these; and **Daredevil: Born Again S02E01–07** (added 2026-09-24; in no account and on neither Pixel). All are `archived` (local copy = dummy). Resolved by the re-crawl/retries: Death Note 09, Gentleman, Daredevil S01E07–11 (backed up), Friends S06E01.
- Proposed change: IMP-C25's mapper/check detect, report loudly and offer dry-run repairs for (a)/(b); (c)/(d) become an explicit user decision list after the incremental re-crawl + device cross-check.
- Effort estimate: small per case (decisions) · Risk: none from detection (read-only); repairs are dry-run-first.
- If skipped: a restore of these items fails only when the user needs them — possibly after the phone copy is gone.
- Status: pending (tooling in IMP-C25)

## Appendix B — PROGRESS.md template (Step 0b)

```markdown
# IMP-C25 — Execution Journal (resumable, cross-session / cross-account)
- Task: IMP-C25 (+C26 fix, C27 tooling) — exact Google Photos item fetch. Framework: v2.
- Branch: feature/imp_c25_fetch_exact_gp_item · Worktree: C:\Users\harin\PycharmProjects\MediaVault-c25
- Plan: docs/feature-fetch-datetime/PLAN.md · Decisions: DECISIONS.md · Python: C:\Users\harin\PycharmProjects\MediaVault\.venv\Scripts\python.exe
- Baseline: full 971 passed · smoke 81 passed (70635cb, 2026-09-25)
- Last updated: <date>

## ▶ NEXT ACTION
<one paragraph: the exact next step/gate and what it needs>

## Resume protocol
1. Open a session in the worktree; `git status` (do not assume clean; never add -A).
2. Read PLAN.md + DECISIONS.md + this file.
3. `git log --oneline` must match the SHAs below; trust git on disagreement.
4. Re-probe fable (.claude/MODEL_WATERFALL.md); record the model used per step.
5. Continue at ▶ NEXT ACTION; never re-run a `done` step; commit this file with every step.

## Step status
| Step | Description | Status | Model used | Completing SHA | Tests | Notes |
|---|---|---|---|---|---|---|
| 0 | plan + journal + IMP registration | pending | | | | |
| G0 | Open Decisions | pending | user | | | |
| 1 | sync with main (after #60) | pending | | | | repeatable |
| 2 | gpweb + fake Google Photos | pending | | | | |
| 3 | mvcommon identity primitives | pending | | | | |
| 4 | ENTRY_TYPE_KEYS + guard | pending | | | | fallback none |
| 5 | capture v2 | pending | | | | |
| 6 | apply engine | pending | | | | |
| 7 | crawler productization | pending | | | | |
| R0 | prerequisites (user) | pending | user | | | others blocked until profile |
| 8 | device cross-check | pending | | | | |
| 9 | map_photos | pending | | | | |
| R1 | real mapping dry-run (user) | pending | user | | | |
| 10 | fetch plumbing + routing | pending | | | | |
| 11 | download hygiene | pending | | | | |
| 12 | locator engine (3 candidates) | pending | | | | 🚦 G12 |
| 13 | check_photos | pending | | | | |
| 14 | --manual + resume | pending | | | | |
| 15 | smoke + pins + disarm probes | pending | | | | |
| R2 | live verification pack (user) | pending | user | | | |
| R3 | real-library apply (user) | pending | user | | | |
| 16 | docs (architect) | pending | | | | |
| 17 | IMP bookkeeping | pending | | | | |
| 18 | final verification + PR | pending | | | | 🚦 Checkpoint 1 |

## Multi-candidate tracking (Step 12)
Worktrees C:\Users\harin\PycharmProjects\MediaVault-c25-cand\step-12\A|B|C · branches …__cand_a|b|c · DECISION .candidates/c25-step-12/DECISION.md · tags candidates/imp_c25/step-12/<x>-chosen|rejected · user pick: PENDING

## Operations log (crawls, dry-runs, applies — redacted counts only)
| When | Command | Result (counts) | Resume command |
|---|---|---|---|

## In-progress sub-state
_(none)_

## Blockers / human gates
- G0 Open Decisions · R0 prerequisites · R1/R2/R3 real-data gates · G12 candidate pick · Checkpoint 1 · Checkpoint 2
```

## Appendix C — graph validation command (Step 0c / Step 17)

```bash
node -e "const fs=require('fs');const s=fs.readFileSync('docs/priority-graph/priority-graph.html','utf8');function arr(n){const i=s.indexOf('const '+n+' = [');const j=s.indexOf('];',i);return eval(s.slice(i+('const '+n+' = ').length,j+1));}const T=arr('TASKS'),E=arr('EDGES');const ids=new Set(T.map(t=>t[0]));const P=new Set(['crit','high','med','low','done']),S=new Set(['todo','decision','done']);const badT=T.filter(t=>t.length!==6||!P.has(t[3])||!S.has(t[4]));const badE=E.filter(([a,b])=>!ids.has(a)||!ids.has(b));console.log('TASKS',T.length,'EDGES',E.length,'badTasks',badT.length,'badEdges',badE.length);process.exit(badT.length||badE.length?1:0)"
```

## Appendix — Original task prompt (verbatim)

```
Use the Agents v2 - Also for the agent v2 mode - when there is no limis on respources , and no limits on number of candidates to use . Before we start when using fable executor or fable for any other step - lets make sure to check if fable is reachable and available to use in current session.
reason being - some times based on weekly usage or some limits, the fable is disabled - in this case Opus with max or ultra code needs to be used instead. Make a change in the agentic approach itself for this fable - opus waterfall logic whereever necessary. 


I want  to do a new task, 
use planner v2 and all v2 agents. I want best of best code and good practices in the changes. No limit for the usage and credits and candidate usage for this task.
Go through the entire fetch , fetch restore and verify part from achitecture and from actual code logic files. 
Basically I want to enrich the fetch - currently the fetch be it series or movie , just uses the full file name , or the id, or tries to match only short id and download all matches, and keep only the file which matches hash id, 
I want to take this forward , or make it better. 
What I see is a date and time in google photos attached to each of these files or chunk files once we upload from the pixel. THis is usually the creation date of the file. In case we split the chunks it I think is the date on which we created , 
pretty sure it stores the time also. It becomes increasingly difficult as and when our list or library keeps growing. Once we have 1000s of files or movies, it becomes super hard to match exactly. I see it shows so many related 
entries in our fetch search from google chrome selenium code. Thats why we added this waterfall logic to fetch with same name, search id, etc all to find specific files. 
BUt what we have here is the exact date time -- it is added exactly same in google photos , and google photos allows us to filter with date , (maybe date time also not sure we can test), I want to add this information in the code or library. 
We know the exact time / datetime from the actual file when we push. I want to store this exact value in library within each node. THen use this information when fetching the file. The date time should not change once we initially upload to google photos from pixel.
I can give you some proper sample examples - of files which are archived already. I can tell you exactly what is the date I see in google photos. For example: 
FA75V0303405 in this adb device currently connected. I have this file - Silicon.Valley.S02E02.Runaway.Devaluation.2015.1080p.BluRay.REMUX.AVC.DTS-HD.MA.5.1-NOGRP in the actual pixel phone now along wiwth the id. The date I see in google photos for this 
file is -  Feb 5, 2022 -- mostly this file was created at that time. This is the actual file before archive I'm talking about - not the dummy one in current C drive. 
Lets see if we can use this information - see if you can find this time also in the file and search in google photos with this information. If this works, we can add this Feb 5, 2022 and the date inside the respective node in library . and if I 
fetch this file, it will use this date or date time in proper format whichever works to filter the exact files. the current method with name , short id , etc can be a waterfall logic if this fails. 
Best case is we are able to store the exact date time and also search with this date time , or a range like from: date time to date time + few seconds. It should fetch the exact file only. Lets try it out first to see what works. 
Even in a new archival which I'm going to do lets understand all the dates associated, then I do the enrich . then see in google photos actually what works. 
Once we are clear with the approach and know what works and what doesn't, I want you to properly form a full plan. 

Deliverables: PLAN.md only (no code, no branches) with steps + tests + verification + "Open Decisions". 
Note if we are solving any improvement tasks with this task say C18 for example - IMP-C18 needs to be marked done in improvements_tierC.md on implementation, the architect updates ARCHITECTURE.md/README (documented behavior change), and I want branch name, PR to main, and manual test commands at the end. Also you also need to update the priority graph and suggest me the next starts we can start. 
 make each step, each execution or task resumable. each session or operation needs to be stored so that any      
  other claude account or new session also can just pick it up from where things were left of and dont need to re evaluate and execute everything.
  DOnt worry about limits for this task. Come up with a complete - comprehensive plan. main goal is to not affect any existing functionalities. also no limits on the candidate usage, number of candidates for the step, or opus / fable (if it works) usage. for this task. 
Also note that same or similar change should be in - feature/imp_u6_token_brackets. dont see this or use any logic from this branch. no need to read also the implemeentation to see what it does. 
thats a different tool which did the same task  - want to compare that with claude's implementation. maybe the actual files in c/mdeia already got changed to [] from that execution. check if any discrepancies or changes compared to actual json or what you have. 
lets implement this in a new branch from main. 
 Come up with a complete - comprehensive plan. main goal is to not affect any existing functionalities.
 
 for now, before jumping into this plan, understand the ask.. see what all we need to do manually - or check. see if you can get the file from adb, analyze - eeping in local, any c or d test folder which has space. 
 tell me clear steps if you want me to check something. 
```
