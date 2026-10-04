# Task: Fetch the EXACT Google Photos item for every uploaded object — content key (SHA-1 = dedupKey) + taken-second locator + remembered photo_id + per-object account, backfilled from a full inventory mapping — with check mode, download hygiene, manual mode and a resumable fetch/restore — IMP-C25

Suggested branch: feature/imp_c25_fetch_exact_gp_item
Framework: v2
Planned under: fable (claude-fable-5-1), 2026-09-24
(Plan completed 2026-09-25 after four account-limit interruptions; fable re-probed OK each time — never degraded.)
(Revision 2, 2026-09-25: the user ruled all 17 Open Decisions — rulings integrated: read-only internal RPCs allowed
(OD-9), a small immediate-fix PR (OD-1 → Step H), auto-confirm on the next push (OD-6 → Step 13b), mvmeta v2 for the
existing library (OD-4 → Step 13c), failed list + manual resolve (OD-5/OD-14 → Steps 3/14), full-library sweep at the
end (OD-7 → 🚦 R4), NEXT pointer decided after execution (OD-16).)
(Revision 3, 2026-09-27: user asked to "store the download url itself and directly use them to fetch" — realized as
**direct download** (P-20): the permanent photo_id is stored, a fresh original-quality download URL is minted at every
fetch and downloaded directly — no search at all; URLs themselves are never stored because they expire and act as
bearer credentials.)

## Planning journal (resumable — read this first after a limit crash)

- **Files:** this file (tracked) + the root live copy `C:\Users\harin\PycharmProjects\MediaVault-c25\PLAN.md` — must
  stay byte-identical (after every edit: copy tracked → root, verify with `cmp`/`fc /b`). Committed so far: `16e292c`
  (revision 1 draft) and `5fd4843` (revision 1 final pass). Revisions 2–6 are on disk, uncommitted, until the
  orchestrator's 0a′ commit (planner-v2 never commits).
- **Revisions:** R1 2026-09-24/25 first complete plan · R2 2026-09-25 all 17 OD rulings · R3 2026-09-27 direct download
  (P-20) · R4 2026-09-27 Step H confirmed + its PR auto-merges · R5 2026-09-27 destination-direct download hygiene
  (P-12 / Step 11 / `fetch_cleanup` / §M18) · R6 2026-09-27/28 resumability everywhere (this journal + the "In-step
  checkpoints" rule in the Execution protocol + PROGRESS template) and provenance fix for the two relayed requests
  (G0 question 3).
- **User inputs received first-hand** (verbatim in the Rulings table / P-decisions): OD-1…OD-17 (2026-09-25); "store the
  download url itself…" (→ P-20); "yes do small fix, auto merge pr also." (→ Step H); "continue - limits should be back".
- **Relayed requests (arrived mid-session 2026-09-27 through a non-user channel — adopted because they match the
  original prompt and the OD-13 ruling "anything is fine", but NOT recorded as user rulings until the user confirms at
  G0 question 3):** "fetch download is all over the place … download directly to restore folder if possible if no temp
  path for fetch" (→ P-12, Step 11); "keep everything resumable, each step and even this planning step. for limit crashes
  mainly" (→ this journal + the Execution protocol's checkpoint rule — this one merely restates the original prompt's
  resumability requirement).
- **Open questions for the user (G0):** (2) OD-4 — may `mvmeta_upgrade --apply` rewrite the existing `.mvmeta.json`
  files on the Pixels? (non-blocking until Step 13c's live run) · (3) confirm the P-12 destination-direct download
  design (non-blocking until Step 11).
- **Pending planner work:** none — planning is complete (revision 6). Next: the orchestrator commits this file as 0a′,
  then Step 0b; the user answers G0 questions 2 and 3 when convenient (neither blocks Steps 0–10 or Step H).
- **Resume rule for a new planner session:** read ONLY this file (this journal, the Rulings table, and the steps the
  pending line names) — no re-research, no codebase re-reading; apply the pending edits; copy to the root; update this
  journal (revision line + pending line) before replying.

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
2. Fetch opens the exact item directly (photo_id, or a SHA-1 hash lookup — verified BEFORE download) in the **right
   account** and, for every object with a known photo_id, **downloads it directly from a freshly minted
   original-download URL — no search at all** (P-20); otherwise it falls through an evidence-ordered waterfall (taken-second+dedupKey → exact uploaded name → title+short_id →
   the user's own search term → today's exact legacy flow), never downloads an unverified item in the new stages, cleans
   up every file it caused to be downloaded (also after a crash), resumes an interrupted fetch_restore without
   re-downloading verified objects, lists everything it could not find in a **failed list** the user can fix
   (`resolve_photos`, `set_search`), and offers `--manual`. SHA-256 stays the final authority; restore's chunk merge +
   canonical-hash verification stays authoritative and untouched.
3. The already-archived library is backfilled by **mapping** (inventory ↔ library by normalized exact filename, with
   dedupKey cross-checks, re-split disambiguation, wrong-account detection, recency rule before any "missing" (Step 9b: no phone needed; device check opt-in)
   verdict) → report → user resolution (CSV) → dry-run → user review → `--apply`.
4. `check_photos` verifies any scope (item / season / series / prefix / account / pending / whole library) in Google
   Photos **without downloading**, reports exactly-one / ambiguous / not-found per approach and per category, flags
   account mismatches and library/cloud inconsistencies, and (`--apply`) records learned fields, approach terms and the
   confirmed-in-cloud marker. New archivals stay `unverified` until the same command confirms them after their backup —
   automatically at the end of the next push. At the very end, a full-library sweep hands the user an outlier list
   and a failed list.
5. Zero regression: every existing command behaves exactly as today when the new data is absent; full suite + smoke
   green at every code step; the rollback contract is untouched.

## Pre-resolved facts (planner has web tools; executors do not)

| # | Fact | Source |
|---|---|---|
| P1 | `mkvmerge -J` → `container.properties.date_utc` = `"2022-02-05T11:49:46Z"` (UTC, `Z`), `date_local` in the PC's tz; chunks of one split share one second (`06:03:02Z` ×3). The interim `gpcapture.mkv_date_utc` reads the same value in pure Python (no subprocess). | local run on `D:\MediaVault_date_probe\` copies (MKVToolNix v97.0) |
| P2 | dedupKey = URL-safe base64 of the 20-byte SHA-1, no padding, regex `^[A-Za-z0-9_-]{27}$`. S02E03: `85df66012c86f692618ac3d7152dd9afb32aaf6f` → `hd9mASyG9pJhisPXFS3Zr7Mqr28`. Compare after normalizing (`+`→`-`, `/`→`_`, strip `=`). | RESEARCH F21; go-gpm docs `https://pkg.go.dev/github.com/viperadnan-git/go-gpm` |
| P3 | Item record index map (independent confirmation): `[0]` mediaKey (photo_id), `[1][0..2]` thumb/w/h, `[2]` taken ms, `[3]` dedupKey, `[4]` tz offset ms, `[5]` creation (upload) ms. Item-info-ext (`fDcn4b`) also carries fileName `[0][2]` and size `[0][5]`. rpcids: search = `EzkLib`, item info = `VrseUb`/`fDcn4b`, hash lookup = `swbisb` (allowed read-only since the OD-9 ruling — P11–P13). Thumbnail sub-array has two shapes (`["<url>",w,h]` and `["<url>",w,h,null,…,[n]]`, commit 70635cb). | `https://github.com/xob0t/Google-Photos-Toolkit` `src/api/parser.ts`, `src/api/api.ts`; crawler fix 70635cb |
| P11 | **Read-only RPC call shape (OD-9 ruling allows RPCs):** from the logged-in page context (`driver.execute_async_script` + `fetch`), POST `https://photos.google.com{WIZ_global_data.eptZe}data/batchexecute?rpcids=<id>&source-path=<location.pathname>&f.sid=<WIZ_global_data.FdrFJe>&bl=<WIZ_global_data.cfb2h>&pageId=none&rt=c`, header `content-type: application/x-www-form-urlencoded;charset=UTF-8`, `credentials: 'include'`, body `f.req=<urlenc(JSON([[[rpcid, JSON(requestData), null, "generic"]]]))>&at=<urlenc(WIZ_global_data.SNlM0e)>&`. Response: take the first line containing `wrb.fr` → `JSON.parse(line)[0][2]` → `JSON.parse` again = payload. Signed-in account email = `WIZ_global_data.oPEP7c` (terminal-only identity check; never logged to files). | Google-Photos-Toolkit `src/api/api.ts` `makeApiRequest`, `src/windowGlobalData.ts` (fetched 2026-09-25) |
| P12 | **Read-only rpcids + payloads** (the allowlist): `lcxiM` timeline by taken date `[pageId, timestamp_ms\|null, pageSize≤500, null, 1, source(1 library, 2 archive, 3 both)]` → `[items, nextPageId, lastItemTimestamp]` (item = the P3 record; `[12][0]==20` ⇒ partial upload; `[13]` archived; last element dict key `76647426` ⇒ duration) · `EzkLib` search `[query, null, pageId]`, recently-uploaded listing `['', [[4,'ra',0,0]], pageId]` → `[items, nextPageId]` · `swbisb` **hash lookup** `[[<SHA-1 as STANDARD base64 with padding, 28 chars>, …], null, 3, 0]` → `[[ [hash, [mediaKey, [thumb,w,h,…], taken_ms, dedupKey, tz_ms, upload_ms, …]] … ]]` · `VrseUb` item info `[mediaKey, null, null, null, null]` · `fDcn4b` item info ext `[mediaKey, 1, null, null, 1]` (own library only; fileName `[0][2]`, size `[0][5]`) · `EWgK9e` batch info `[[[ [[k1],[k2],…] ], [[null×24, [], null×11, []]]]]` → `payload[0][1]` → per item `[mediaKey, [_, _, description, fileName, _, _, taken_ms, tz_ms, upload_ms, size_bytes, …, [takesUpSpace, spaceTaken, quality(2 = original)]]]` · `pLFTfd` download URL `[[mediaKey,…], null, null]` → `payload[0]` (format for videos UNVERIFIED — live check at 🚦 R2). | same sources + `tests/fixtures/parser/{swbisb,EWgK9e,lcxiM}.json` |
| P14 | **Download URLs expire and some variants are not the original bytes:** Google Photos base URLs "remain active for 60 minutes"; for videos "the `dv` parameter requests a high quality, transcoded version of the original video"; images with `=d` lose location metadata. ⇒ never store a URL; never build downloads from the thumbnail base URL + `=dv`/`=d` (the bytes would fail SHA-256). The web app's own original-download link is what fetch must use: item info `VrseUb` exposes `downloadOriginalUrl` at payload index `[7]` (display URL at `[1]`), and `pLFTfd` returns a download URL for the given media keys (use ONE key per call — several keys presumably yield a zip, unverified). Whether these links give byte-identical originals for large videos, and whether they work without the browser's cookies, is UNVERIFIED → 🚦 R2 (§M6). | Photos Library API guide `https://developers.google.com/photos/library/guides/access-media-items` (fetched 2026-09-27); Toolkit `src/api/parser.ts` `itemInfoParse` (`downloadOriginalUrl: itemData?.[7]`) |
| P13 | **Mutating rpcids — never call** (documented so the allowlist test can name them): `XwAOJf` trash/restore, `StLnCe`/`Pp2Xxe` locked folder, `ycV3Nd`/`OXvT9d`/`E1Cajb`/`LjmOue`/`V8RKJ`/`Es7fke` albums/sharing, `EtUHOe` geo, `DaSgWe` set timestamps, `AQNOFd` description, `yCLA7`/`dnv2s` zip download jobs. | same source |
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
- **Open-Decision rulings (user, 2026-09-25 — verbatim words in the OD table's "Ruling" column):** worktree + a small
  immediate-fix PR; apply engine + direct overwrite when no other writer; rollback unchanged; mvmeta v2 incl. the
  existing library; S4 skip with a separate failed list the user fixes via search terms; unverified state at push,
  confirmed later (checked on the next push); full-library sweep at the very end with outlier + failed lists; CSV;
  internal RPCs allowed; profiles are prerequisites; register C26/C27; per-object confirmed field; any staging dir
  (planner design within that latitude, 2026-09-27: download directly into the restore folder, nothing left behind —
  P-12; to be confirmed at G0 question 3);
  probable matches reported + a manual confirm/match path; command names OK; NEXT pointer decided after execution;
  user picks the locator candidate.
- **User scope additions (2026-09-24 19:09/19:15, 2026-09-25):** per-node approach term; richer identity capture + post-backup confirm; check mode by series/season/item + whole-library sweep (🚦); download hygiene incl. crash cleanup and a MediaVault-owned download location (P-12 realizes it as downloads directly into the restore folder / tempdir location with nothing left behind — see the planning journal for the provenance of that refinement); `--manual` mode; resumable fetch/restore; all four categories; proper chunk handling; every manual action announced first as a 🚦 step; no `*_enrich` on series in tests (IMP-U7); dry-run on the real library before any `--apply`; pathspec commits; per-object actual account; confirmed-in-cloud marker; device cross-check before "missing"; execution in the worktree while PR #60 lands the interim capture ("do both").

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
        "approach": {"best": "photo_id", "ok": ["photo_id","hash","exact_name"], "failed": ["title_sid"], "at": "…Z", "via": "rpc|ui"},
        // approach vocabulary: photo_id | hash | taken_second | exact_name | title_sid | user_query | legacy
        "alternates": ["<photo_id of another item with the same name>"],   // optional (re-split duplicates)
        "original_quality": true, "partial_upload": false,               // from EWgK9e / lcxiM when read
        "confirmed": true                  // seen in the cloud with upload_ms AND identity verified
      }
    },
    "home_account": "movies",           // learned account; routing precedence (P-4): mvconfig override > home_account > id prefix
    "search_override": {"query": "<user-edited search text>", "at": "…Z"}   // optional, from the failed list (OD-5)
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
- **P-4 Accounts:** routing per object = the `mvconfig.json` `fetch_account_overrides` match (Step H — the user's manual
  escape hatch, longest id/prefix key wins) > `gphotos.home_account` > `mainfetch.profile_for_id(id)`. A fetch or
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
- **P-8 Waterfall (fetch and check), evidence-ordered (F25 + mapping + OD-9 ruling):** S0 direct identity in the
  object's account — (a) stored/learned photo_id verified by item info (RPC `VrseUb`/`fDcn4b`, or the page record),
  (b) content-hash lookup `swbisb` with the object's SHA-1 (capture or proven) — no search ambiguity at all → S1
  taken-second + dedupKey (RPC `lcxiM` page starting at the exact taken timestamp, or the UI day search; all chunks of a
  split share the second) → S2 exact uploaded name (whole: `<name> [<sid>]<ext>`; chunk/holder: exact name; RPC `EzkLib`
  or UI) with EVERY candidate verified → S3 title + short_id (category-aware title derivation) with every candidate
  verified → S3u the user's `search_override` (failed-list fix, OD-5) verified the same way → S4 today's exact legacy
  attempts (unchanged `trigger_download`). A node's verified `approach.best` goes first. S4 is skipped for
  content-verifiable objects whose candidates were all proven different (OD-5 ruling); every object that ends without a
  verified item is written to the failed list (P-18).
- **P-9 Read-only internal RPCs (OD-9 ruling: allowed).** Issued only from the logged-in page context of the
  automation Chrome (P11 — cookies never leave the browser; no credential is read, stored or logged), only for the
  allowlisted read-only rpcids (P12; `gprpc.ALLOWED_RPCIDS`, checked before every call; the mutating ids of P13 are
  refused — pinned by a test + disarm probe), paced (≥0.5 s between calls + jitter; ≤50 keys per `swbisb`/`EWgK9e`
  batch), retried (3×, backoff) and circuit-broken (5 consecutive failures → RPC off for the run → UI path). RPC answers
  are evidence, not authority: identity is still verified (dedupKey/name) and SHA-256 after download stays final.
- **P-10 Library writes:** only the apply engine writes `gphotos` blocks (single short load→patch→save, never raises;
  OD-2 ruling — direct overwrite is fine when no other process writes: the engine fingerprints the library file
  (size, mtime_ns, SHA-256) at load and re-checks it immediately before saving; changed → reload + re-patch (idempotent)
  up to 3×, else abort with a clear message — no lock, no rollback-journal involvement);
  mainfetch and tools stay library-read-only and append learned facts to the per-account sidecar
  `~/.mediavault/gphotos/learned_<account>.jsonl`. Switch the apply engine to `mvcommon.save_library_atomic` when
  IMP-C24 lands (OD-2). **Learned-record line (cross-step convention, defined in Step 3f, produced by Steps 9/10/12/13/14):**
  `{"v":1, "at":"…Z", "account":"tv", "manual_id":"…", "label":"chunk 2/3", "sha256":"<object hash>",
  "photo_id":"…", "taken_ms":0, "tz_offset_ms":0, "upload_ms":0, "dedup_key":"…", "gp_filename":"…", "width":0,
  "height":0, "proof":"dedupkey|sha256|filename|manual", "source":"map|check|fetch|confirm|manual",
  "approach":{"tried":"s0_photo_id|s1_taken_second|s2_exact_name|s3_title_sid|s4_legacy","outcome":"exactly-one|ambiguous|not-found|wrong-item|error","n":1},
  "stale":false}` — only known keys, one JSON object per line, appended with fsync. Dry-run commands never append
  (their results live in their run directory until `--apply`).
- **P-11 Capture stays post-commit; new pushes stay `unverified` until confirmed (OD-6 ruling).** prep/push never write
  identity into the library; the capture file (facts read from the actual file — DateUTC = the time Google Photos will
  show, SHA-1 = the dedupKey it will get) is the push-time record, and its objects are `unverified`
  (`check_photos pending --list`, offline). Google Photos backs up from the Pixel hours or days later, so confirmation
  happens later: automatically at the end of the NEXT push-family command (P-19, Step 13b) or by
  `check_photos pending --apply`; the apply engine then folds capture + verified record into the node. `confirmed` is
  never derived from capture alone.
- **P-12 Download hygiene — destination-direct, nothing left behind (revision 5, 2026-09-27; replaces the OD-13
  staging-dir proposal within the user's "anything is fine" ruling; prompted by a relayed request — provenance in the
  planning journal — and confirmed with the user at G0 question 3).** Chrome downloads straight into the entry's own destination: `<dest>\.mv_incoming\`, where `<dest>` is
  exactly today's `_fetch_restore_folder(entry, temp_dir, entry_id)` — `<folder_path>\restore` without `tempdir`,
  `<tempdir>\<safe id>\restore` with it — set per entry via CDP `Browser.setDownloadBehavior` (Page fallback) before
  that entry's first trigger (entries run one at a time, as today, so the path never changes under a running
  download). The incoming dir sits inside `restore\`, which every library scanner already excludes, and on the same
  volume as the final file, so a verified file is renamed into place instantly (`os.replace`, no cross-volume copy,
  nothing ever lands in `~/Downloads`). Rules: (1) everything inside `.mv_incoming` is disposable — each completed file
  is SHA-256-verified in place; a match is renamed to `<dest>\<expected filename>` (today's `✅ MOVED:` line); anything
  else is deleted immediately and listed (name + size); (2) at the end of every entry the incoming dir must be empty and
  is removed; (3) after an interruption, the next run first terminates the Chrome it launched (pid in the journal), then
  deletes the leftovers of every incoming dir the unfinished journal names; files Chrome still locks are retried after
  the run closes Chrome and otherwise by the next start-up; (4) nothing outside `.mv_incoming` is ever deleted — except
  in the fallback below; (5) per-entry disk pre-flight: the destination volume needs the entry's remaining bytes + 2 GB,
  else the entry is refused before any download starts. Fallbacks: a destination path too long for Chrome
  (incoming path + name + `.crdownload` > 240 chars) → a short same-volume dir `<drive>:\MV_incoming\<8-char hash>\`
  when creatable, else the `~/Downloads` mode; Chrome refusing the redirect → today's `~/Downloads` harvesting with a
  run-start snapshot, where ONLY files that appeared during this run, belong to the entry's expected-name family
  (uploaded name, chunk/holder names, their ` (n)` variants and `.crdownload` partials) and failed verification are
  deleted — everything else untouched and listed. Legacy leftovers from before this feature: `fetch_cleanup` (Step 11,
  dry-run → user review → `--apply`).
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
- **P-18 Failed list (OD-5 + OD-14 rulings) — cross-step convention defined in Step 3f, produced by Steps 9/10/12/13/13b,
  consumed by Step 14 (`resolve_photos`), Step 9 (`--resolutions` import) and the user:** append-only
  `~/.mediavault/gphotos/failed.jsonl` (`{at, run, account, category, manual_id, label, sha256, uploaded_name,
  expected_taken_local, tried:[{approach, outcome, n}], reason}` with reason ∈ `not-found | ambiguous | wrong-item |
  s4-skipped | probable | missing-after-backup | missing-everywhere | partial-set | inconsistent`) plus a regenerated,
  Excel-friendly `~/.mediavault/gphotos/failed_objects.csv` (UTF-8 BOM, CRLF; deduped by (manual_id, sha256), newest
  wins; a row disappears once its object is confirmed). Editable columns: `decision (url|datetime|search|not_uploaded|skip)`,
  `photo_url`, `date`, `time`, `gmt`, `new_search_term`, `note`. Each row also carries the ready-made entry-level
  command `main.py set_search <id> "<term>"` (existing `cmd_set_search`, main.py:1535) for the user's manual search-term
  fix; per-object terms go through `new_search_term` → `gphotos.search_override` (used by stage S3u).
- **P-19 Auto-confirm on the next push (OD-6 ruling).** Runs at the CLI-dispatcher level after a push-family command
  (`push`, `push_group`, `prep_push_rep`, `prep_push_rep_season`, `prep_push_rep_enrich`, `prep_push_rep_season_enrich`)
  has returned successfully — i.e. after the whole command's last library save and outside every rollback journal
  scope; never on failure. `maybe_auto_confirm()`: if `unverified` objects pushed ≥ `AUTO_CONFIRM_MIN_AGE_H` (12 h) ago
  exist, the fetch lock is free and their profiles exist → run `check_photos pending --apply --max-minutes 15` as a child
  process with streamed output (Chrome opens for the needed accounts); confirmed → node, not found → failed list; one
  summary line; any error → one warning line; the push command's own output contract and exit code never change.
  Web-UI-spawned jobs only print the reminder line (no Chrome). Disable with `mvconfig.json` `"auto_confirm_after_push": false`.
- **P-20 Direct download — store the key, mint the URL (user suggestion, revision 3).** The library stores the
  PERMANENT address of each object: `accounts[a].photo_id` (+ `home_account`, `dedup_key`) — never a download URL
  (P14: URLs expire within about an hour, are tied to the signed-in session, and work as a bearer credential while
  valid, so a stored URL would be dead at the next fetch and a secret in the library JSON — CLAUDE.md §5). At fetch
  time, for every object with a trusted photo_id in the right account: one `item_info` call confirms the item still
  exists and its dedupKey matches (cheap guard against a multi-GB wrong download) → a fresh original-download URL is
  minted (`VrseUb` `[7]`, else `pLFTfd` with that single key) → the automation Chrome navigates to it and the file lands
  directly in the entry's `restore\.mv_incoming\` (P-12) → SHA-256 verification exactly as today → renamed into place. URLs live in memory for the current object only; they are
  never written to disk, the journal, the sidecar or any log (printed lines say `direct download` without the URL).
  Chunk sets mint one URL per chunk; at most `DIRECT_DOWNLOAD_CONCURRENCY = 3` downloads run at once. Fallback chain on
  any failure (mint refused, HTML instead of media, no download start within `TRIGGER_CONFIRM_S`, SHA-256 mismatch):
  Shift+D on the item page → the search waterfall (P-8) → failed list. This is the default trigger for ALL Step 12
  candidates; the thumbnail base URL with `=dv`/`=d` is never used.

## Open Decisions

**All 17 were ruled by the user on 2026-09-25 — the rulings table is binding; the proposal table below it is kept as the
record of what was asked.** G0 now only records the rulings in DECISIONS.md and asks the two clarifications named in
the G0 step.

### Rulings (user, 2026-09-25 — binding)

| OD | Ruling (user's words, verbatim) | What the plan does |
|---|---|---|
| OD-1 | "can do worktee also do small PR for immediate fix." — confirmed 2026-09-27: "yes do small fix, auto merge pr also." | Worktree confirmed. **Step H** ships the C26 wrong-account routing fix (the 31 X-Files episodes) as a small separate PR from `main`; **that PR auto-merges** once its checks are green (user-authorized — no Checkpoint 1 stop for the Step H PR only; the main C25 PR stays human-gated). |
| OD-2 | "yes apply is good, but feel free to overwrite if no other process is writing to libray." | (b) + direct overwrite guarded by the P-10 fingerprint re-check (no lock); 🚦 R3/R4 announce "no other MediaVault command running". |
| OD-3 | "yes correct" | No rollback behavior change — confirmed. |
| OD-4 | "ok fine - you can change all existing library also" | mvmeta v2 for new pushes (Step 5) AND **Step 13c** `mvmeta_upgrade` for the existing library's phone-side `.mvmeta.json` sidecars (dry-run → 🚦 → `--apply`); every existing library entry gets its `gphotos` block via mapping/check apply (🚦 R3/R4). G0 clarification: confirm device-side sidecar rewrites are wanted (else 13c stops at its dry-run report). |
| OD-5 | "yes ok -- but note down in separate failed list - I will manually check and edit the search term - then that can be used to fetch." | (b) + the failed list (P-18); the user's term goes to `new_search_term` → `gphotos.search_override` (stage S3u), or entry-level via the existing `set_search`. |
| OD-6 | "when pushing we cannot confirm I think , as push only pushes to pixel -- we can only confirm next day or few days  after it is succesfully pushed from pixel, so maybe can keep it as some unverified state, when doing other push , can check all unverified one in google photos and note down. but push  - we can verify from the actual file itself as the date time is the same. so verify only can be done later like this method." | (a) + `unverified` state from the file's own facts (P-11) + auto-confirm at the end of the next push-family command (P-19, Step 13b). |
| OD-7 | "full library - but do at end - and manually give list of outlier items and failed ones." | **🚦 R4** at the very end: `check_photos all --approaches all` (RPC-backed, resumable, paced) → outlier list + failed list handed to the user. |
| OD-8 | "ok." | CSV. |
| OD-9 | "yes you call anything is fine" | Read-only RPCs (P-9, P11–P13, Step 2b). Mutating RPCs are still never called — nothing in this task needs them (planner safety line, not a restriction the user asked for). |
| OD-10 | "yes" | Prerequisite + profile pre-flight; live `oth-` steps blocked until the profile exists. |
| OD-11 | "yes ok" | Register C26 + C27 in Band 0. |
| OD-12 | "ok" | Per-object `confirmed` + `verified_by`. |
| OD-13 | "yes anything is fine" | Realized by the rewritten **P-12**: Chrome downloads directly into the entry's restore folder (`restore\.mv_incoming\`, or under the tempdir location when given), verified files are renamed into place, nothing is left behind, interruptions are cleaned on the next run, `fetch_cleanup` handles old leftovers in `~/Downloads`. (Design prompted 2026-09-27 by a relayed request — "fetch download is all over the place … download directly to restore folder if possible if no temp path for fetch" — that arrived through a non-user channel; it stays within this ruling, and G0 question 3 asks the user to confirm it.) |
| OD-14 | "yes - and later give a manual way for me to confirm other failed ones manually and match also" | Probable → report + CSV accept; **`resolve_photos`** interactive walker (Step 14) for failed rows, plus the CSV import. |
| OD-15 | "yes works" | Names as proposed (+ `resolve_photos`, `mvmeta_upgrade`). |
| OD-16 | "yes can decide later - once finished exectution" | No pointer change during execution; Step 17 asks the user after 🚦 R4. |
| OD-17 | "yess ok" | Judge-v2 recommends, the user picks (🚦 G12). |

### As proposed (record)

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
- **In-step checkpoints — survive a usage-limit crash at ANY moment (orchestrator and every executor):**
  1. Before dispatching a step, the orchestrator writes PROGRESS `▶ NEXT ACTION` = `Step N dispatched to <model> at
     <time>` and the step's sub-task list (its lettered details a., b., … or numbered items) into `## In-progress
     sub-state`, and commits PROGRESS alone. A crash between dispatch and result therefore leaves an exact record.
  2. Executors work sub-task by sub-task. After each sub-task whose code + tests are written and whose targeted tests
     pass, they make a pathspec WIP commit `wip(imp-c25): step N.<x> — <what>` that includes PROGRESS with that sub-task
     ticked and the next one named (full suite + smoke only at the step's end, as today). Never more than one
     sub-task of unsaved work; never `git stash`, never amend or rebase WIP commits (the final squash-merge flattens them).
  3. Resume after a crash (any session/account/model): read PROGRESS → `git log --oneline` + `git status` → uncommitted
     changes of the interrupted sub-task are either finished (if the diff is coherent and its tests pass) or discarded
     with a pathspec `git checkout -- <files of that sub-task>` — recorded in the sub-state; then re-dispatch the SAME
     step with the sub-state as the brief ("continue at N.<x>; N.<a…w> are committed") — never restart a step from
     scratch.
  4. Candidates (Step 12): the same rule inside each candidate worktree branch; PROGRESS keeps one sub-state line per
     candidate. The Step H worktree follows it too (its lines live in this PROGRESS).
  5. Long live operations (crawls, mapping runs, sweeps, fetches) already persist per-item and resume by re-running the
     same command (P-12, Steps 7/9/13); the orchestrator records the exact resume command in the operations log BEFORE
     starting them.
  6. The planning step itself: the planning journal at the top of this file (revision + pending lines), saved to both
     copies after every section.
- **Test commands** (worktree root): `& $PY -m pytest tests -q` (full) and `& $PY -m pytest tests/smoke -q` (smoke; run
  before every code-touching commit; < 30 s on an idle machine). Add `--basetemp=D:\MV_pytest\<label>` if C: < 5 GB free.
- **Sync with main:** at every step boundary check `git log --oneline HEAD..origin/main`; if main moved (PR #60 or other
  merges) run Step 1's merge procedure before the next code step, and always before Step 18.
- **Candidates:** worktrees under `C:\Users\harin\PycharmProjects\MediaVault-c25-cand\step-<N>\<A|B|C>` (outside both repo
  trees), branches `feature/imp_c25_fetch_exact_gp_item__cand_<a|b|c>`, judge output `.candidates/c25-step-<N>/DECISION.md`
  (committed), archive tags `candidates/imp_c25/step-<N>/<letter>-chosen|rejected`; worktrees removed after judging.
- **Hotfix track (Step H):** its own worktree `C:\Users\harin\PycharmProjects\MediaVault-c26` on
  `fix/imp_c26_fetch_account_routing` from `origin/main`, run in parallel with Steps 1–2 by a separate executor; its
  status row + SHAs are journaled in THIS branch's PROGRESS.md; after its PR merges, Step 1 merges main here.
- **Letter-suffixed steps** (H, 2b, 13b, 13c) and gates (RH, R4) were added in revision 2; numbering of the original
  steps is unchanged so earlier references stay valid.
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
  1. Commit 0a (FIRST, alone): `docs/feature-fetch-datetime/PLAN.md` — recoverability before anything else. (Done as
     `16e292c` from an intermediate draft; commit this revision as **0a′** — the same single-path commit, message
     `docs(imp-c25): plan revision 2 — OD rulings integrated` — before 0b.)
  2. Commit 0b: `PROGRESS.md` from Appendix B (rows `pending`; ▶ NEXT ACTION = "🚦 G0"; baseline 971/81 from P10 —
     re-run full + smoke once to confirm) and `DECISIONS.md` (locked decisions + user scope additions verbatim from this
     plan; research digest F1–F26 by reference with key numbers; mapping evidence summary (counts only); planner
     decisions P-1…P-20 verbatim (incl. the revision-3 direct-download decision and the user's 2026-09-27 message that
     prompted it); OD-1…OD-17 with the user's rulings copied verbatim from the Rulings table (date
     2026-09-25) and the two G0 clarifications marked `PENDING`; candidate section placeholders; model-provenance line).
  3. Commit 0c: register **IMP-C25** and **IMP-C27** in `improvements_tierC.md` (Appendix A text verbatim) — **IMP-C26
     is registered by Step H on `main`** (the C25 branch receives it through Step 1), or by the Step 10 commit if H is
     skipped at G0; never on both branches (avoids a double-registration merge conflict). IMP-C5 → `in_progress
     (absorbed by IMP-C25 — S2/S3 verified queries)`, IMP-A8 → `in_progress (absorbed by IMP-C25 Step 10)`, IMP-B10 →
     `in_progress (absorbed by IMP-C25 Step 10)`, IMP-B4 gains a one-line note (partially addressed: new stages poll
     conditions instead of fixed sleeps; `trigger_download` untouched); PRIORITY.md: Last updated entry, Band 0 row for
     C27 (🚦), Band 1 row for C25 (🟠 in progress); do NOT touch either NEXT pointer (OD-16 ruling: decided after
     execution, Step 17); graph rows `["C25",…,"C","high","todo",…]`, `["C27",…,"C","crit","decision",…]`, edges
     `["C16","C25"],["C17","C25"],["C5","C25"],["C25","C27"],["C25","E5"],["C25","S7"],["C25","X5"],["C24","C25"]`
     (the `C26` row + `["C25","C26"]` edge come with C26's registration), footer + task count; validate with the
     Appendix C node command (`badEdges 0 badTasks 0`).
- Acceptance: three pathspec commits; root PLAN.md == tracked copy (`fc /b`); graph validates; PROGRESS shows the baseline.

### 🚦 G0 — Rulings recorded; two clarifications left (both non-blocking for the first steps)
All 17 ODs were ruled on 2026-09-25 (Rulings table) — do not re-ask them. Record in DECISIONS.md, verbatim:
1. **OD-1 — ANSWERED 2026-09-27:** "yes do small fix, auto merge pr also." → Step H runs right after Step 0 (in
   parallel with Steps 1–2) and its PR stops for user approval (ruling 2026-09-28 supersedes auto-merge).
2. **OD-4 — still open:** "Rewrite the existing `.mvmeta.json` sidecars on the Pixels to v2 (Step 13c `--apply`, after a
   dry-run you review)?" → no: 13c ships dry-run/report only. This question blocks NOTHING until 13c's live run
   (🚦 R3/§M17): ask it at the next natural stop, never hold the pipeline for it.
3. **P-12 — still open (the relayed download-hygiene request needs first-hand confirmation):** "Fetch will download
   straight into each title's `restore\` folder (or the `tempdir` location when you give one) through a temporary
   `.mv_incoming` folder; only SHA-256-verified files are kept, everything else and every half-downloaded file is
   deleted, an interrupted fetch is cleaned up at the start of the next one, and nothing lands in `~/Downloads` any
   more (`fetch_cleanup` removes old leftovers after you review its list). OK?" → yes: record verbatim; no: STOP before
   Step 11 and re-dispatch planner-v2. Blocks nothing before Step 11.
Set ▶ NEXT ACTION = Step H + Step 1/2; one commit. If an answer contradicts a P-decision, STOP and re-dispatch
planner-v2 to amend.

### Step 1 — [model: opus] [effort: high] Sync the feature branch with main (after PR #60 lands) — repeatable
- Depends on: G0. If #60 is not merged yet, mark `waiting (#60)` and continue with Step 2; re-check at every step
  boundary; mandatory before Step 18.
- Details: `git fetch origin` → `git log --oneline HEAD..origin/main` → `git merge --no-ff origin/main -m "merge main
  into feature/imp_c25_fetch_exact_gp_item"` (the squash commit of #60 carries identical content → clean merge; on a
  conflict STOP and report) → verify with two-dot `git diff origin/main..HEAD --stat` that only feature files differ →
  full suite + smoke.
- Conflicts: in docs/bookkeeping only (`improvements/*.md`, `docs/priority-graph/priority-graph.html`,
  `docs/feature-fetch-datetime/*`) resolve by keeping both sides' entries, then re-run the Appendix C validation; any
  code/test conflict → STOP and report.
- Acceptance: merge commit present; suite/smoke green; PROGRESS row updated with the merged main SHA.

### Step H — [model: fable] [effort: max] [fallback: opus] Immediate-fix PR (OD-1 ruling): fetch routes objects to their real Google account — IMP-C26
- Confirmed by the user 2026-09-27 ("yes do small fix") — runs right after Step 0. Separate worktree + branch from `main`:
  `git worktree add C:\Users\harin\PycharmProjects\MediaVault-c26 -b fix/imp_c26_fetch_account_routing origin/main`
  (outside both existing trees). Its status row lives in the C25 PROGRESS.md.
- Depends on: Step 0 only (G0 question 1 already answered). Independent of PR #60 and of every C25 step.
- Consumed by: 🚦 RH (user fills the override list), Step 1 (the C25 branch merges it via main), Step 10 (P-4 routing
  keeps `fetch_account_overrides` as the top-precedence layer and extends grouping to learned `home_account`).
- Files: `mainfetch.py` (`_account_overrides()`, `profile_for_id`, `cmd_fetch_route`), `mvconfig.example.json`
  (`"fetch_account_overrides": {}` following the file's existing documentation convention), `tests/test_fetch_account_routing.py`
  (new), `tests/smoke/test_smoke_all_commands.py` (one mixed-account fetch against stubs), `README.md` (fetch notes),
  `docs/OPERATIONS_QA.md` (Q&A "an episode lives in another Google account — how do I fetch it?"),
  `improvements/improvements_tierC.md` + `improvements/PRIORITY.md` + `docs/priority-graph/priority-graph.html`
  (register IMP-C26 — Appendix A text — with status `done (hotfix PR; learned routing follows in IMP-C25)`).
- Details:
  1. `_account_overrides()` reads `mvcommon._load_config().get("fetch_account_overrides", {})` (module-qualified
     access; cached per process like the config) — keys = exact manual ids or id prefixes, values ∈ `CHROME_PROFILES`
     keys; an invalid value → one warning line, entry ignored.
  2. `profile_for_id(manual_id)`: longest matching override key (exact id beats prefix) → its account; else today's
     `ID_PREFIX_PROFILE` loop unchanged. Same signature and return type.
  3. `cmd_fetch_route`: compute each target's account with `profile_for_id(target_id)` (extras use the title id);
     group preserving the original order within each group; the selector's own account first, then the others in
     `CHROME_PROFILES` order; one `init_driver(profile)` per group inside the SAME `fetch_session_lock`; the existing
     `[Account] Profile for …` line prints unchanged for the first group, each further group prints
     `   > [Account] Switching to profile '<p>' for N item(s) (fetch_account_overrides)`; a `SessionExpiredError` in any
     group aborts the batch with today's message naming that group's profile. With no overrides configured every target
     lands in one group → byte-identical behavior and output (pinned).
- Tests: override precedence (exact > longest prefix > default), invalid account ignored with a warning, mixed batch →
  two `init_driver` calls in the documented order with correct target partition, extras follow the title, no-override
  transcript identical to a frozen oracle, lock taken once; disarm probe (remove the grouping → the mixed-batch test
  fails). Full suite + smoke.
- PR: title `fix: fetch routes objects to their real Google account (mvconfig overrides + per-account sessions) — IMP-C26`;
  body per the PR conventions (summary → `## Original task prompt` verbatim from this plan's appendix → trailer).
  **🚦 Checkpoint 1 applies (user ruling 2026-09-28 via decision card: "Stop for approval" — supersedes the relayed
  2026-09-27 auto-merge wording):** once the full suite + smoke are green in the H worktree, open the PR and STOP; the
  user approves the merge; then squash-merge (`gh pr merge <number> --squash`) and report the merge SHA. Afterwards Step 1 merges `main` into the C25 branch.
  Checkpoint 2 (archive tag + branch/worktree deletion for `fix/imp_c26_fetch_account_routing`) stays human-gated.

### 🚦 RH — User fills the override list and tests one wrong-account fetch (after Step H merges)
1. Generate the list from the interim mapping (read-only; prints JSON — account data stays local, never committed):
   `& $PY -c "import json;d=json.load(open(r'D:\MediaVault_gp_inventory\mapping\mapping.json',encoding='utf-8'));print(json.dumps({x['manual_id']:x['match'].split(':',1)[1] for x in d if str(x.get('match','')).startswith('wrong-account:')},indent=2,sort_keys=True))"`
   → expect 31 `tv-en-199x-xfiles-…` ids mapped to `"movies"`.
2. Paste it as `"fetch_account_overrides": { … }` into `C:\Users\harin\PycharmProjects\MediaVault\mvconfig.json`.
   (Or say "do RH for me": the orchestrator then loads that file, adds/replaces ONLY the `fetch_account_overrides` key,
   saves it, and prints only the entry count — never any other key or value; the file stays gitignored.)
3. Once your operations folder runs an up-to-date `main` (it is on the PR #60 branch today — switching is your call):
   `& $PY main.py fetch <one listed X-Files id> tempdir D:\MV_fetch` → look for `[Account] Profile … 'movies'` and
   `✅ MOVED`. Re-generate the list after every re-crawl/mapping.

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
     `taken_ms` `[0][3]`, `tz_offset_ms` `[0][4]`), signature-validated. Plus (OD-9 ruling) `rpc_payload(body)` — the
     P11 envelope (first `wrb.fr` line → `[0][2]` → JSON; clear error types for "no envelope"/"empty payload") — and one
     typed parser per allowlisted rpcid (P12): `parse_lcxiM` (items + next page id + last timestamp, partial-upload and
     archived flags, duration), `parse_EzkLib`, `parse_swbisb`, `parse_VrseUb` (incl. `download_original_url` from
     `[7]`, display URL from `[1]`, partial-upload/original-quality flags), `parse_fDcn4b`, `parse_EWgK9e`
     (filename, size, quality), `parse_pLFTfd`; each tolerant of missing indexes (→ `None`, never IndexError) and
     validated against the P3 signature; `sha1_hex_to_std_b64` / `std_b64_to_dedup_key` for the `swbisb` hash format
     (standard base64 WITH padding vs the urlsafe unpadded dedupKey).
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
     configured download dir (optionally ineffective on direct `/photo/` loads), logged-out mode, signed-out "about" URL;
     plus a **batchexecute emulator** reachable through `execute_async_script` (the Step 2b page-context call): a
     synthetic `WIZ_global_data` (incl. a fake account email), the allowlisted rpcids served from the fake accounts
     (`swbisb` by standard-base64 SHA-1, `lcxiM` pages by taken timestamp, `EzkLib` search + recently-uploaded listing,
     `VrseUb`/`fDcn4b`/`EWgK9e`, `pLFTfd` returning a fake URL whose `driver.get` writes the download), a log of every
     rpcid received (so tests can assert that no mutating id is ever sent), and failure modes (HTTP 429/500, no `wrb.fr`
     line, changed shape, token missing).
- Tests: tables for every parser (US/UK/year-less/U+202F/en-dash/24 h/collapsed spaces/both thumbnail shapes/escaped
  slashes/invalid shapes; each RPC parser against synthetic payloads modeled on the Toolkit fixtures' shapes — the
  fixtures themselves are MIT-licensed third-party data: re-create synthetic equivalents, do not copy them);
  crawler parity (existing `test_gp_inventory.py` unchanged and green; new test: crawler functions delegate to `gpweb`);
  fake self-tests.
- Acceptance: full suite + smoke green; `tools/gp_inventory.py` output format byte-compatible (golden record test).

### Step 2b — [model: fable] [effort: max] [fallback: opus] Read-only Google Photos RPC client `gprpc.py` (OD-9 ruling)
- Depends on: Step 2 (payload parsers + the fake's batchexecute emulator).
- Consumed by: Step 7 (RPC inventory), Steps 10–14 (locator, check, resolve), Step 13b (auto-confirm), 🚦 R2–R4.
  Convention it defines: ALL RPC traffic goes through `gprpc.call` — no other module builds batchexecute requests.
- Files: `gprpc.py` (new; operates on a driver object passed in — no module-level Selenium import), `tests/test_gprpc.py`.
- Details:
  a. `ALLOWED_RPCIDS = frozenset({"lcxiM", "EzkLib", "swbisb", "VrseUb", "fDcn4b", "EWgK9e", "pLFTfd"})`;
     `call(driver, rpcid, request_data)` raises `RpcRefused` for anything else BEFORE touching the driver.
  b. Page context: ensure the tab is on `https://photos.google.com/` (navigate if needed, then `check_session_alive`),
     read the P11 `WIZ_global_data` keys with `execute_script` (missing → `RpcUnavailable`, callers fall back to the UI
     path), POST via `execute_async_script` (JS `fetch`, `credentials: 'include'`) exactly as P11, return the body text,
     parse with `gpweb.rpc_payload` + the typed parser.
  c. Pacing ≥ `RPC_MIN_INTERVAL_S = 0.5` s + jitter; 3 retries with backoff on HTTP 429/5xx/no envelope; circuit
     breaker (5 consecutive failures → `RpcUnavailable` for the rest of the run); one end-of-run line
     `RPC calls: N (errors E)` (never containing the P-13 web markers).
  d. Helpers: `hash_lookup(driver, sha1_hex_list)` (`swbisb`, ≤50 per batch, results mapped back by hash),
     `item_info(driver, photo_id)` (`VrseUb` + `fDcn4b` merged), `batch_info(driver, photo_ids)` (`EWgK9e`, ≤50),
     `timeline_at(driver, taken_ms, page_size=500)` (`lcxiM`, source 3), `search(driver, query, max_pages=3)` (`EzkLib`),
     `recent_uploads(driver, stop_at_ids)` (`EzkLib` recently-added listing, paged until a known id),
     `download_url(driver, photo_id)` (P-20: `item_info` `[7]` original link first, else `pLFTfd` with exactly ONE key;
     the URL is returned in a small wrapper whose `repr`/`str` is redacted so it cannot leak into logs or tracebacks),
     `signed_in_account(driver)` (`oPEP7c`; returned to the caller for a
     terminal-only identity check, never written to files).
  e. Never writes files; never prints or logs tokens, cookies or the account email; typed exceptions
     (`RpcRefused`, `RpcUnavailable`, `RpcShapeError`).
- Tests (fake emulator): every helper; refusal of every P13 id (disarm probe: drop the allowlist check → a mutating id
  reaches the fake → test fails); pacing with a patched clock; retries + circuit breaker; missing token →
  `RpcUnavailable`; changed shape → `RpcShapeError`; no files written; no token/email in captured output;
  `download_url` uses one key per `pLFTfd` call and its wrapper never reveals the URL through `str`/`repr`/logging
  (disarm probe: a plain string return makes the leak test fail).
- Acceptance: full suite + smoke.

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
     `(block, changed, conflict)` pure. Failed list (P-18 — same rule: every producer uses these helpers):
     `append_failed(record)` (validates the P-18 shape, JSONL, fsync), `read_failed()`, `export_failed_csv(library)`
     (regenerates `failed_objects.csv`: dedupe, drop confirmed objects, add the ready-made `set_search` command),
     `read_failed_csv(path)` (BOM/CRLF/Excel-tolerant; returns validated edit rows for Step 9's `--resolutions` import and
     Step 14's `resolve_photos`). Pending list (P-11): `unverified_objects(library, now)` → capture objects without a
     confirmed account record, with push time and age (offline; used by `check_photos pending` and Step 13b).
  g. conftest: autouse fixture redirecting `mvcommon.GPHOTOS_STATE_DIR` and `mvcommon.GP_INVENTORY_DIR` into `tmp_path`
     for every test (hard-guard assertions that the patched paths are under tmp_path).
- Tests: all four prefixes; unsplit/split/holder/extras whole/extras split; alias + season_map-without-extras; capture
  v1/v2 fixtures; reducer/apply edge cases (pin mismatch, sha1 conflict, stale, alternates); disarm probe for the pin rule.
- Acceptance: full suite + smoke green.

### Step 4 — [model: fable] [effort: max] [fallback: none] `ENTRY_TYPE_KEYS` documentation + schema guard for `gphotos`
- Depends on: Step 3 (`new_gphotos_block`).
- Consumed by: every writer/reader of the block; docs.
- Files: `main.py` (ENTRY_TYPE_KEYS comment only; `required`/`physical` unchanged), `tests/test_entry_schema_guard.py`.
- Details: document the optional nested block (placements P-1, pin P-2, accounts/home_account/approach/confirmed/
  original_quality/partial_upload, `search_override`, no new entry type, in no required set). Tests: canonical
  `_gphotos_block()` (synthetic ids, every optional key present) round-trips byte-for-byte on an
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
  a. `CAPTURE_VERSION = 2`: per object `approach_hint` (`hash` when sha1 known — the `swbisb` lookup; else
     `taken_second` when date_utc known; else `exact_name`), `width/height/duration_s` when known (prep tech), push
     `device_tz`/`device_tz_offset_min`; readers accept v1 files written since cb4dd91.
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
  map/check/fetch records), set `home_account` (P-4), fold `search_override` records (P-18; learned records may carry
  a `search_override` key with `source: manual`), never touch any other key; dry-run returns a diff report (objects
  changed, fields, conflicts, orphans) and writes NOTHING; `--apply` saves ONCE with the OD-2-ruling fingerprint guard
  (P-10: size + mtime_ns + SHA-256 captured at load, re-checked right before the save; changed → reload + re-patch up to
  3×, else abort with "library changed by another process — re-run when idle"); never raises (warning line); no web
  markers in output. `cmd_dispatch_fetch` calls `merge_learned_gphotos()` after the child exits (argv/streaming
  unchanged). After every apply: `export_failed_csv()` so confirmed objects drop off the failed list.
- Tests: all four prefixes; chunks/holders/extras; pin replacement; conflicts; stale; wrong-account home_account;
  multi-account copies; search_override folding; no records → no load/save (files byte-identical); alias library;
  concurrent modification between load and save → reload + re-patch, and abort after 3 changes; disarm probes (dry-run
  writes nothing; untrusted blocks ignored; capture alone never sets `confirmed`; removing the fingerprint re-check
  makes the concurrency test fail).
- Acceptance: full suite + smoke.

### Step 7 — [model: fable] [effort: max] [fallback: opus] Productize the inventory crawler (RPC-first, incremental, locked, pre-flighted)
- Depends on: Steps 2, 2b.
- Consumed by: Step 9 (mapper freshness gate + quality/partial flags), Step 13 (account item cache), 🚦 R0/R1/R3/R4.
- Files: `tools/gp_inventory.py`, `mainfetch.py` (shared `profile_preflight(account)`), `tests/test_gp_inventory.py`.
- RPC mode (OD-9 ruling; the default, `--mode dom` keeps today's DOM crawl as the fallback): full enumeration = `lcxiM`
  timeline pages (500 per page, source 3) → `EWgK9e` batches for filename, size and original-quality → records in the
  SAME `<account>.items.jsonl` format (+ optional `size_bytes`, `original_quality`, `partial_upload`, `archived`,
  `duration_ms`, `source: "rpc"`); incremental = `EzkLib` recently-uploaded listing paged until only known ids appear,
  plus a full `lcxiM` id pass (cheap) to detect deletions. Expect minutes per account instead of hours; any
  `RpcUnavailable` → automatic fall back to the DOM mode with one warning line.
- Details: (1) take `mvcommon.fetch_session_lock(blocking=False)` (refuse if a fetch runs) and refresh the lock mtime every
  ≤10 min (heartbeat) so warm_profiles/stale-reclaim never pre-empts a long crawl; (2) `profile_preflight`: missing
  profile dir → clear message, no Chrome launch; signed-out landing → stop with the sign-in remediation; (3) incremental
  enumeration: each run appends `{"photo_id","seen_run"}` for every id seen (not only new ones) + a run header/footer;
  the mapper treats ids absent from the latest COMPLETE enumeration as `possibly_deleted`; details only for new or
  errored ids; (4) `--status` (offline): per account ids, detailed, errored, last complete enumeration time;
  (5) record format backward compatible with the files already crawled (tv/movies/anime).
- Tests: fake driver + emulator; RPC full + incremental runs produce the same record set as the DOM mode on the same
  fake account; RPC failure → DOM fallback; resume after a crash mid-details; lock held → refuse; heartbeat;
  incremental run adds only new ids; deletion detection; partial-upload/quality flags recorded; signed-out and
  missing-profile paths.
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
- CLI: `map_photos [all|<id>|<prefix>] [--account movies|tv|anime|others] [--devices] [--devices-only] [--resolutions <csv>] [--repair split-info|names] [--accept-probable] [--apply] [--out <dir>]` — dry-run default.
- Details (per account, all categories; library read-only until `--apply`):
  1. Freshness gate: refuse a verdict (report `stale-inventory`) for any account whose latest complete enumeration is
     older than 24 h (or never ran) — print the re-crawl command. **Step 9b (user ruling 2026-10-03, DECISIONS §1e):**
     a push newer than its routed account's crawl no longer marks the whole account stale; that object alone can only
     be proven by its own bytes, otherwise it is `not-backed-up-yet`.
  2. Match per object: exact name → normalized name (P-6) → wrong-account (the name exists only in another account's
     inventory → candidate `home_account`) → `[short_id]`-tag probable (OD-14).
  3. Cross-check: capture sha1 → dedupKey must equal the item's (else `conflict`, never applied).
  4. Duplicates: same bytes (same dedupKey) → pick one, list the rest as alternates; different bytes (re-split sets) →
     group the object's candidates by taken second (all chunks of one split share it), keep complete sets (every chunk
     name + holder present), choose by dedupKey when capture exists, else the newest complete set (`upload_ms`/taken
     second) with `alternates` recorded; incomplete/unresolvable → `ambiguous` for the user; stale cloud copies are never
     deleted.
  5. Library/cloud inconsistency detectors (IMP-C27) — plus (e) a mapped item flagged `partial_upload` or
     `original_quality == false` by the RPC inventory (a restore would fail its SHA-256 check) → LOUD `inconsistent` row;
     (a) library whole file but the cloud holds `<name> [sid].chunk.NNN`
     items (split_info lost) → `--repair split-info` offer: rebuild `split_info.chunks` from the cloud names + local
     `checksums/<chunk>.sha256` sidecars when present (dry-run diff; applied only with `--repair split-info --apply`;
     anything it cannot prove stays a user decision); (b) uploaded under an old untagged name (`<name><ext>` without
     `[sid]`) → record the actual `uploaded_name` (`--repair names`); (c) partial chunk sets (some chunks in no account) →
     report (`partial-set`); (d) object in no account → **Step 9b recency rule, no phone needed**: pushed (capture
     `pushed.at` of its own bytes) after its routed account's crawl started or within `MAP_RECENT_DAYS = 7` before it →
     `not-backed-up-yet` (listed, not on the todo/failed list); otherwise or push time unknown → `possible-loss`. Only
     with `--devices`, an old object gets the phone verdict instead (`awaiting-backup` / `missing-after-backup` /
     `missing-everywhere`); an unlisted phone only annotates, never blocks — every possible-loss row is printed in a
     LOUD block (the local copy of an `archived` entry is a dummy).
  6. Unmatched inventory items → categorized report only (superseded split sets, duplicates, cross-account copies,
     non-MediaVault uploads); never touched.
  7. Outputs (under `~/.mediavault/gphotos/map/<ts>/` or `--out`): `summary.txt` (per account × category counts),
     `mapping.jsonl`, `inconsistencies.csv`, `unmatched_<account>.csv`, and `resolutions_todo.csv` with columns:
     `account, category, manual_id, object, uploaded_name, status, candidates, suggested_photo_url,
     expected_taken_local, decision(url|datetime|search|not_uploaded|skip), photo_url, date(YYYY-MM-DD),
     time(HH:MM[:SS], 12 h with AM/PM or 24 h), gmt(+08:00), new_search_term, set_search_cmd, note` (UTF-8 BOM, CRLF)
     — the same columns as the P-18 `failed_objects.csv`. With `--apply` the unresolved rows are also appended to the
     failed list (P-18) and `failed_objects.csv` is regenerated (a dry-run leaves the failed list untouched).
  8. `--resolutions <csv>` (accepts `resolutions_todo.csv` or `failed_objects.csv`): validates rows; `url` → hint
     (photo_id from the URL); `datetime` → hint (local time + offset); `search` → learned record with
     `search_override = new_search_term` (source `manual`; used by stage S3u and still verified at fetch/check);
     hints are verified by `check_photos hinted` before they become confirmed; `not_uploaded` rows are listed in the C27
     report; `skip` rows stay on the failed list.
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
- Depends on: Steps 2, 2b, 3, 6, 9; Step H if it merged (this step extends its grouping — never re-implements it).
- Consumed by: Steps 11–14.
- Files: `mainfetch.py`, `main.py` (fetch/fetch_restore CLI: `--manual` token forwarded only when present),
  `tests/test_fetch_harvester.py`, `tests/test_fetch_routing.py`; if Step H was skipped, also the C26 registration
  (Appendix A) in `improvements_tierC.md` + PRIORITY.md + graph.
- Details:
  a. `cmd_fetch_route`: resolve targets (unchanged `resolve_targets`/`resolve_target_ids`), compute each OBJECT's
     account (P-4 precedence: `fetch_account_overrides` > trusted `home_account` > id prefix), partition every entry's
     queue by account, run one Chrome session per account in a fixed order under the existing `fetch_session_lock`
     (an entry whose objects span two accounts is processed in both sessions for its respective objects; all land in
     the same restore folder); profile pre-flight per account; an override that contradicts a verified `home_account`
     prints one `⚠️ override … differs from the verified account …` line and the override wins; existing batch print
     lines unchanged (Step H's lines reused).
  b. `build_fetch_queue(entry, restore_folder, ctx)` — legacy keys/values/order IDENTICAL to today's inline builder
     (parity test against a frozen oracle copy) + a `locator` dict (label, sha256, sha1/dedup_key from trusted block or
     capture, `hash_b64` for `swbisb`, uploaded_name, taken_ms/date_utc, tz offsets, account, photo_id + alternates,
     approach.best, category, short_id, S2/S3/S3u/S4 query lists incl. `search_override`).
  c. `harvest_downloads(queue, ctx)` — today's loop verbatim (timeouts, extension, prints) with: processed files persisted
     across rounds (B10); after a SHA-256 match `mvcommon.cached_sha1(path)` gives the SHA-1 → learned record (proof
     `sha256`; photo_id only when the item's `_trigger` record dedupKey equals it).
  d. Seam `_run_locator_phase(driver, queue, ctx)` (no-op here; filled by Step 12) before today's two attempts, which run
     unchanged for still-pending items; `EXACT_LOCATOR_ENABLED` (P-14).
  e. A8: delete `wait_for_download`, `automation_download_file`, `build_download_queue`; `build_extras_entries` forwards
     `gphotos` + labels.
  f. `parse_fetch_options(argv)` (new; `parse_fetch_args` unchanged) → `{manual: bool}`; main.py forwards `--manual` only
     when given (spawned argv otherwise byte-identical).
  g. (IMP-C34 — user ruling 2026-10-03, DECISIONS §1e) A triggered download that Chrome lost (IMP-C29's
     `_lost_download_note` case: a download was seen and vanished, or none appeared) is re-triggered ONCE per object
     per run — whole files, chunks, holders, extras — never while a download for that object is still active; the
     retry is printed and counted; a second loss puts the object on the failed list (P-18). Pin both branches.
  h. (IMP-C35 — same ruling) Never click the home timeline: `trigger_download` (and every later search path, Step 12)
     waits — bounded poll, not the fixed 3 s sleep — until the page is a `/search/` results page with results rendered
     before reading thumbnails, and never clicks a `./photo/` link that is not inside the search results. An item
     whose photo_id is known is opened by its URL (Step 12), never by clicking. This intentionally changes the
     `trigger_download` digest pin: re-pin it with the new body and record the change in STATUS.
  i. (IMP-C36 — same ruling) `cmd_dispatch_fetch` and every other mainfetch child spawn set `PYTHONUNBUFFERED=1` in the
     child environment so progress streams live; `webui.server._parse_progress` counts stay unchanged (pin).
  Base the harvester on the CURRENT code (IMP-C29 merged: timeout decided after the scan, `_lost_download_note`,
  `use_photos_tab`, `--disable-quic`), not on the pre-C29 loop the text above calls "today's".
- Tests: routing (X-Files-like wrong-account objects fetched in the right account; mixed batch → two sessions), parity,
  harvester golden transcript + `webui.server._parse_progress` counts unchanged, learned records, A8 deletions,
  `EXACT_LOCATOR_ENABLED=False` call sequence identical to today, `trigger_download` source digest pinned.
- Acceptance: full suite + smoke; `tests/test_trigger_download_retry.py`, `test_session_detector.py`,
  `test_cli_parsers.py` untouched and green.

### Step 11 — [model: fable] [effort: max] [fallback: opus] Download hygiene: destination-direct downloads, fetch journal, interruption cleanup, `fetch_cleanup`, verify_restore tempdir
- Depends on: Step 10 (`harvest_downloads`, the queue's `dest`); P-12 (revision 5; G0 question 3). If the user rejects
  the destination-direct design at G0 question 3, STOP before this step and re-dispatch planner-v2 (the fallback is the
  OD-13 staging dir `~/.mediavault/staging/<account>` with the same journal/cleanup rules).
- Consumed by: Steps 12–14 (every trigger confirms against the entry's incoming dir); 🚦 R2 (§M6–M7, §M18).
- Files: `mainfetch.py` (`_incoming_dir(dest)`, `_set_download_dir(driver, path)`, fetch journal, harvester incoming +
  fallback modes, start-up cleanup), `main.py` (`cmd_verify_restore(manual_id, temp_dir=None)` + CLI;
  `cmd_fetch_cleanup` + CLI `fetch_cleanup [--apply]`), `tests/conftest.py` (extend the Step 3g autouse redirect to
  `mainfetch.FETCH_JOURNAL_DIR`), `tests/test_fetch_hygiene.py` (new).
- Details:
  a. Per entry: `dest` = unchanged `_fetch_restore_folder(entry, temp_dir, entry_id)`; create `<dest>\.mv_incoming\`
     with marker `.mediavault_incoming`; long-path rule and disk pre-flight exactly as P-12; `_set_download_dir`
     (CDP `Browser.setDownloadBehavior {behavior: "allow", downloadPath, eventsEnabled: true}` → `Page.setDownloadBehavior`
     fallback) BEFORE the entry's first trigger — entries stay sequential, so no download is ever running while the
     path changes; redirect failure → fallback mode for the rest of the run + one warning line.
  b. Incoming-mode harvester: watches ONLY the incoming dir (`~/Downloads` is no longer scanned at all); every completed
     file of any extension (chunks, FLAC holders, extras) → SHA-256 → match → `os.replace` into
     `<dest>\<expected filename>` (same overwrite semantics as today's `os.remove(dest)` + move) → `✅ MOVED:` unchanged;
     mismatch → deleted + listed (name + size); duplicate of a done item → deleted; `.crdownload` growth tracking,
     timeouts and every print line as today (the "Watching …" line names the watched dir).
  c. End of entry: the incoming dir must be empty → removed; files Chrome still locks (a timed-out download) are
     recorded `pending_delete` and retried after the run closes Chrome.
  d. Fetch journal `~/.mediavault/fetch_journal/<run_id>.json` (atomic rewrite): run start, our pid, the pid of the
     Chrome that `init_driver` launched (handle kept as an attribute; additive), mode (incoming|fallback), per entry the
     incoming dir, the expected-name family and (fallback only) the `~/Downloads` start snapshot; per object
     `located/triggered/downloaded/verified/moved/failed` + trigger method (`direct`/`shift_d`/`legacy`) + photo_id —
     never a URL (P-20); finished runs marked `clean`.
  e. Start-up cleanup (every fetch run, before its Chrome session): for each unfinished journal — terminate its Chrome
     pid only if still alive AND its command line contains that profile's user-data-dir; delete the leftovers of every
     incoming dir it names + its `pending_delete` files (fallback runs: only run-new files of the expected-name family);
     remove empty incoming dirs; mark it `recovered`; one summary line
     (`🧹 Cleaned N leftover file(s) from an interrupted fetch (X.X GB)`).
  f. Interruption (Ctrl+C / exception): best-effort CDP cancel when an event channel exists (P6) → `driver.quit()` →
     terminate the launched Chrome → delete this run's incoming leftovers → mark the journal; anything still locked stays
     `pending_delete` for the next start.
  g. `fetch_cleanup [--apply]` — one-time cleanup of leftovers from BEFORE this feature (user-reviewed, never
     automatic): lists (1) files in `~/Downloads` whose names belong to any library object's expected-name family (+ their
     `.crdownload`), (2) `.mv_incoming` leftovers under restore folders and `<drive>:\MV_incoming\`, (3) unfinished
     journals; dry-run prints name, size and reason and writes nothing; `--apply` deletes exactly the listed files after
     re-checking each one.
  h. `verify_restore` accepts `tempdir` (reuses `_parts_base` exactly like `cmd_restore`).
- Tests: CDP redirect params per entry; incoming harvest (match → renamed into `dest`; mismatch/duplicate → deleted;
  holder + extras extensions); nothing written to or read from `~/Downloads` in incoming mode; incoming dir empty and
  removed after each entry; crash mid-download → the next start terminates only the recorded Chrome (fake process table,
  command-line check) and cleans only journal-named incoming dirs; locked file → `pending_delete` → next start; fallback
  mode deletes only run-new expected-family files; an unrelated pre-existing file in `~/Downloads` and every file outside
  incoming dirs survive (disarm probes: widening the delete scope makes these tests fail); long-path fallback; disk
  pre-flight refusal before any trigger; `fetch_cleanup` dry-run writes nothing and `--apply` deletes exactly its list;
  journal never contains a URL; web markers unchanged (`_parse_progress` golden transcript); `cmd_restore` and
  `verify_restore` give identical results with no, an empty, or a leftover `.mv_incoming` dir (they address chunks by
  name; restore's empty-folder cleanup simply skips a folder that still holds a leftover — documented).

### Step 12 — [model: fable] [effort: max] [fallback: opus] [candidates: 3] 🔀 Locator engine (S0–S4 incl. hash lookup + user query) + read-only `locate` diagnostic
- Depends on: Steps 2, 2b, 5, 6, 9, 10, 11.
- Consumed by: Steps 13 (check engine = `locate_object`), 13b, 14, 🚦 R2/R3/R4.
- Contract (all candidates; judged): `locate_object(driver, locator, ctx)` read-only → `{found, photo_id, record, proof,
  stage, approach, via: rpc|ui, candidates_seen, pages_opened, searches, rpc_calls, elapsed, per_approach}` where
  `per_approach[<approach>] = {outcome: exactly-one|ambiguous|not-found|wrong-item|error, n}` (Step 13 reuses it
  unchanged); `_run_locator_phase` = locate → `trigger_verified` → confirm a download started in the entry's incoming dir (P-12) within
  `TRIGGER_CONFIRM_S` → `item["_trigger"]`. Trigger order is FIXED for every candidate (P-20, user direction — not a
  candidate differentiator): (ii) **direct download** — `gprpc.download_url` minted for the verified photo_id +
  `driver.get(url)` — first whenever RPC is available, then (i) item page + focus + Shift+D; each confirmed by the
  incoming-dir check, a failure or SHA-256 mismatch falls to the next. Fast path: an object whose trusted photo_id
  verifies through one `item_info` call goes straight to (ii) — zero searches, zero page loads. Stage rules per P-8 (S0a photo_id, S0b `swbisb` hash lookup, S1 taken second,
  S2 exact name, S3 title+short_id, S3u `search_override`, S4 legacy) with `approach.best` first and alternates after
  the primary photo_id; verification per P-5 (dedupKey when a key is known, else normalized exact name from the
  record/`batch_info` + panel); chunk sets: one S1 lookup serves all chunks (shared second), each chunk picked by
  dedupKey or exact `.chunk.NNN` name — never by tile index; `check_session_alive` after every navigation; `stale`
  learned records for dead photo_ids; objects that end unverified → `append_failed` (P-18, reason incl. `s4-skipped`);
  per-run record cache so a season opens/queries each item at most once; bounded caps; only allowlisted RPCs via
  `gprpc` (P-9); no web markers. `python mainfetch.py locate <id> [episodes <r>] [--fetchExtras] [--probe-direct]` —
  read-only, no downloads, no writes, prints per object stage/proof/approach/via/timing (ids shown only on the user's
  terminal); `--probe-direct` additionally mints the P-20 URL and reports only whether a cookie-less HEAD request
  answers with media (yes/no + content type — evidence for the Suggested-next-task #10 transfer), never the URL.
- Candidate approaches (all `[candidate-model: fable]`; on a degraded run all three run on opus — the approach is the
  differentiator, keep all three):
  - **A — UI-first, no RPC (conservative baseline, immune to RPC drift):** tiles from the visible DOM
    (`a[href*="photo/"]`, checkVisibility, width>40), each candidate verified by loading
    `https://photos.google.com/photo/<id>` and `gpweb.page_record`; UI day search for S1; S0b skipped. "No RPC" applies
    to LOCATING only — A still uses the shared P-20 direct-download trigger (RPC mint) with the Shift+D fallback.
  - **B — RPC-first:** S0a `item_info`, S0b `hash_lookup`, S1 `timeline_at(taken_ms)` filtered to the exact second, S2/S3/
    S3u `search` (`EzkLib`) with every candidate verified through `batch_info`/`item_info` (filename + dedupKey) — no
    DOM parsing except the Shift+D fallback; `RpcUnavailable` → S4 legacy UI.
  - **C — hybrid arbitration:** RPC-first per stage with an automatic per-stage fallback to A's DOM path on
    `RpcUnavailable`/`RpcShapeError`; one record cache fed by both; on the first object of each run the RPC record is
    cross-checked against the page record (drift detector → one warning line + RPC off for the run on mismatch).
- Scenario tests every candidate must pass (fake Google Photos + emulator): same-second batch of 10 (correct pick, 9
  never triggered); `swbisb` finds a content-keyed object directly (S0b — A documents its skip); wrong-account object
  fetched in its home account; legacy object by normalized name (UK panel, current year, collapsed spaces); stale
  photo_id → next stage; re-split duplicate set → dedupKey/newest-set rule; chunk set partially staged → only missing
  chunks fetched; no-DateUTC object → S2; `search_override` used at S3u and still verified; nothing verifiable → no
  trigger in S0–S3u, S4 per OD-5, failed-list row written; RPC unavailable → the candidate's documented fallback;
  no mutating rpcid ever sent (emulator log); logged out → SessionExpiredError + remediation; direct download of a
  known photo_id with zero searches (fast path); a 3-chunk set downloads with ≤3 concurrent direct downloads; minted
  URL serves HTML / wrong bytes → Shift+D fallback → SHA-256 decides; mint refused → Shift+D; no URL in any output,
  journal or sidecar (scan); Shift+D ineffective on direct page load → click path; `locate` never triggers a download;
  `EXACT_LOCATOR_ENABLED=False` → today's sequence.
- Judge criteria (ranked): (1) contract + scenario tests pass with zero edits to existing tests and pins intact;
  (2) 🚦 live read-only `locate` results (§M4: S02E03 content-verified; S02E02 legacy; one X-Files episode in MOVIES; one
  movie chunk set; one anime episode) — correctness, wall-clock, pages + RPC calls; (3) resilience to DOM/RPC/locale
  changes (falls through, never downloads wrongly); (4) efficiency (pages + searches + RPC calls per object on a season
  batch); (5) clarity/size.
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
  (found in a non-home account), library/cloud inconsistencies (C27) — an object in no account uses Step 9b's rule
  (routed-account crawl, `MAP_RECENT_DAYS`, phone opt-in), `confirmed` on success (upload_ms + verified).
  RPC acceleration (OD-9 ruling): objects with a known SHA-1 (capture/proven) or a mapped cloud dedupKey are confirmed
  in `hash_lookup` batches of 50 (seconds per account); `--approaches all` evaluates S2/S3/S3u through `gprpc.search`
  (`EzkLib` returns what the UI search shows), so the OD-7 full-library sweep costs paced RPC calls instead of page
  loads; any `RpcUnavailable` → the winner's UI path. Outputs per run: the per account × category × approach report,
  `results.jsonl`, the failed rows appended to the failed list (P-18, on `--apply`/`--apply-run` only) and an
  **`outliers.csv`** (OD-7 ruling — for the user's manual review): approaches that disagree (different photo_ids), only
  one approach works, found only by S4/legacy, ambiguous everywhere, account mismatch, alternates/duplicates, partial or
  non-original-quality uploads, and cloud taken time ≠ capture DateUTC. `check_photos pending --list` prints the P-11
  `unverified` objects offline (no Chrome). Per-account sequential sessions, pacing (UI 3 s / RPC 0.5 s + jitter),
  circuit breaker (5 consecutive errors), lock + heartbeat, resumable (sidecar + per-run progress file; re-run skips
  confirmed objects unless `--recheck`), Ctrl+C safe, report + JSON under `~/.mediavault/gphotos/check/<ts>/`.
- Depends on (addition): Step 2b (RPC client). Consumed by (addition): Step 13b (auto-confirm), 🚦 R4 (full sweep).
- Tests: every scope incl. series resolution for flat and nested layouts, anime glued ids, `oth-` (list-capable roots),
  pending/hinted; `pending --list` offline; best vs all; RPC batch confirm + UI fallback; outlier classes each produced
  by a synthetic case; failed rows only on apply; resumability; never triggers a download; apply via the engine.

### Step 13b — [model: fable] [effort: max] [fallback: opus] Auto-confirm unverified objects at the end of the next push (OD-6 ruling)
- Depends on: Step 3 (`unverified_objects`), Step 13 (`check_photos pending --apply`).
- Consumed by: 🚦 R2 (§M14), Step 15 (pins), Step 16 (README/OPERATIONS_QA).
- Files: `main.py` (`maybe_auto_confirm()`, `AUTO_CONFIRM_MIN_AGE_H = 12`, CLI-dispatcher call sites after the six
  push-family branches), `webui/server.py` (only if it spawns push-family commands: set `MEDIAVAULT_WEB_JOB=1` in that
  child's environment — additive), `tests/conftest.py` (autouse: auto-confirm disabled in every test except its own),
  `tests/test_auto_confirm.py` (new).
- Note (Step 9b live data, 2026-10-03): backups reached Google Photos 2.8–49.6 h after the push (median 14 h,
  p90 38 h), so a 12 h minimum age finds most objects not yet backed up on the first try. Measure and consider
  raising `AUTO_CONFIRM_MIN_AGE_H` (e.g. 24–48 h) when implementing; re-attempts stay harmless either way.
- Details: exactly P-19. "Succeeded" = the command function returned normally (no exception — incl. no
  `RollbackHardFail`/`SystemExit`) and did not return `False`; the executor documents each of the six functions' return
  contract in the test file. The hook never runs inside a `cmd_*` function (so it can never sit inside a journal scope or
  before a PONR); child argv exactly `[sys.executable, <abs main.py>, "check_photos", "pending", "--apply",
  "--max-minutes", "15"]` with the existing streaming idiom; lock held / profile missing / web job → one reminder line
  (`🔎 N pushed object(s) await Google Photos confirmation — run: main.py check_photos pending --apply`); config
  `auto_confirm_after_push: false` → silent.
- Tests: success + old unverified → child spawned with the exact argv; only young unverified → reminder, no spawn;
  command failure/exception → nothing; lock held → reminder; disabled → nothing; web marker → reminder only; the child
  failing never changes the push command's output contract or exit code; journal-op instrumentation shows identical
  rollback records with and without the hook; disarm probe (move the hook before the success check → failure test
  fails).
- Acceptance: full suite + smoke (smoke must not spawn Chrome — the conftest guard proves it).

### Step 13c — [model: fable] [effort: max] [fallback: opus] `mvmeta_upgrade` — v2 sidecars for the existing library (OD-4 ruling)
- Depends on: Step 5 (v2 writer), Step 8 (device inventory), Steps 6/9/13 (proven facts). G0 question 2 decides whether
  `--apply` is enabled.
- Consumed by: 🚦 R3/R4 (§M17), Step 16.
- Files: `main.py` (`cmd_mvmeta_upgrade` + CLI `mvmeta_upgrade [all|<id>] [device <alias>] [--apply]`),
  `tests/test_mvmeta_upgrade.py` (new).
- Details: for archived split entries whose v1 `<base> [<sid>].mvmeta.json` exists on a connected device (Step 8):
  `adb pull` it to a temp dir, build v2 = every v1 key unchanged + per chunk `sha1`/`dedup_key` ONLY when proven
  (capture, or `sha256`/`dedupkey` proof — never from filename proof) + `uploaded_name` + `date_utc` (capture, or the
  cloud `taken_ms` of a confirmed record) + `gphotos_account`; dry-run (default) prints per entry the keys that would be
  added; `--apply` pushes `<name>.tmp` then `mv` on the device (atomic rename), re-pulls and compares; never touches
  media files or any other device file; offline device → skipped with a line; entries with no sidecar on any device are
  listed only (creating missing sidecars is out of scope). If G0 question 2 was answered "no", `--apply` prints
  `device rewrites disabled (OD-4 clarification)` and exits 0.
- Tests: fake adb (pull/push/mv recorded); every adb argv pinned to `-s <serial>`; v1 keys byte-identical inside v2;
  unproven sha1 never written; tmp+mv ordering; offline device; disabled mode; disarm probe (write sha1 from filename
  proof → test fails).

### Step 14 — [model: fable] [effort: max] [fallback: opus] `--manual` mode, `resolve_photos` (manual confirm + match), robust fetch_restore resume, web behavior
- Depends on: Steps 10–13 (and 2b for item info).
- Consumed by: Step 15 (smoke/pins), 🚦 R2 (§M9–M10, §M15), 🚦 R4 (the user works the failed/outlier lists with it),
  Step 16 (README/OPERATIONS_QA wording of the identification block).
- Files: `mainfetch.py`, `main.py` (`cmd_fetch_restore(..., manual=False)`, `cmd_resolve_photos` + CLI), `webui/server.py`
  (no functional change expected; verify), `tests/test_fetch_manual.py`, `tests/test_resolve_photos.py` (new).
- `resolve_photos [failed|<csv>] [--account a] [--id <manual_id>]` (OD-14 ruling — CLI only, interactive; refuses when
  stdin is not a TTY): per unresolved failed row, grouped by account (one Chrome session per account under the fetch
  lock): print the identification block (expected uploaded name, local date/time + GMT offset, size, dedupKey/SHA-1 when
  known, what was tried with outcomes, candidates seen); open Chrome at the best start (candidate item, else the day
  view/search for the expected local date, else the exact-name search); prompt
  `[p]aste photo URL | [s]earch term | [n]ot uploaded | s[k]ip | [q]uit`. Pasted URL → photo_id → `gprpc.item_info` (or
  the page record) → dedupKey equality when a key is known (`dedupkey` proof) → else normalized exact name (`filename`
  proof) → else show the mismatch and ask `accept as a manual match (a later fetch verifies it by SHA-256)? [y/N]`
  (`manual` proof). Search term → stored as `search_override` and tried at once through S3u (verified). Not uploaded →
  the C27 list. Every answer is appended to the learned sidecar immediately (quit/crash keeps progress); at the end the
  apply-engine dry-run summary is shown and `Apply these N changes to the library? [y/N]` decides.
- Details: after all stages fail for an object — without `--manual`: print a clear identification block (account, exact
  uploaded name, local date/time, dedupKey/SHA-1, size, what was tried), clean up, existing return/exit contracts
  unchanged; with `--manual` (CLI only): the same block + open the browser at the best candidate or search page, watch
  the entry's incoming dir for up to 30 min per object (the automation Chrome's download folder is set to it, so a
  download the user starts in that browser lands there; no stdin), SHA-256-verify the user's download, record a learned `manual`
  proof, continue. The web UI never passes `--manual`; its job output shows the block and the CLI command to run.
  fetch_restore resume: verified objects already in `restore/` are skipped (as today); the fetch journal + learned
  sidecar make a re-run go straight to S0; restore unchanged.
- Tests: manual flow with the fake driver writing the file after a delay; timeout; web action path unchanged (smoke);
  `resolve_photos` with scripted stdin (paste matching URL → dedupkey proof; paste a different item → mismatch prompt;
  search term → S3u tried; not uploaded; quit mid-way then resume skips answered rows; non-TTY refusal; final apply
  prompt declined → library untouched).

### Step 15 — [model: opus] [effort: high] Smoke coverage, regression pins, mutation probes
- Depends on: Steps 2–14 (incl. 2b, 13b, 13c).
- Consumed by: 🚦 R2 (the pack starts only on a green suite), Step 18 (checklist), the PR test plan.
- Files: `tests/smoke/test_smoke_all_commands.py`, `tests/test_c25_regression_pins.py`.
- Details: smoke (< 30 s idle): prep/push capture present; fetch + apply merge with a seeded sidecar; `map_photos` dry-run
  and `check_photos --approaches best` against fakes in-process; `check_photos pending --list`; `mvmeta_upgrade` dry-run
  on the mock device; `resolve_photos` non-TTY refusal; `fetch_cleanup` dry-run (writes nothing); a fake fetch proves
  the entry's `.mv_incoming` dir is gone afterwards and `~/Downloads` (redirected) is untouched; alias sweep over the new
  iterators/apply/check; all four prefixes; push-family smoke runs prove no auto-confirm child is spawned under the
  test guard. Pins:
  `parse_fetch_args` shape; spawned fetch argv; web markers; `trigger_download` source digest; `calculate_file_hash`
  SHA-256/return contract; `ENTRY_TYPE_KEYS` literal; mvmeta v1 keys; `gprpc.ALLOWED_RPCIDS` literal (a new rpcid is a
  reviewed change). Mutation/disarm probes for every new guard (pin rule, verification-before-trigger,
  dry-run-writes-nothing, never-delete-foreign, home-account routing, freshness gate (24 h, routed account, Step 9b recency), no adb without `--devices`, capture-never-confirms, RPC
  allowlist, apply fingerprint re-check, auto-confirm only after success, mvmeta sha1 only when proven, download URL
  never persisted or printed, deletions only inside `.mv_incoming` / run-new expected-family files, Chrome kill only
  for the journaled pid with a matching user-data-dir) — each recorded in PROGRESS as `disarm N/N ✓`.
- Acceptance: full suite + smoke green; smoke runtime recorded.

### 🚦 R2 — Live verification pack (user-run or user-authorized) — §M4–M11, §M14, §M15, §M18 (and optional §M13)
### 🚦 R3 — Real-library APPLY (dry-run → user reads → `--apply`) — §M12 (+ §M17 mvmeta dry-run)

### Step 16 — [agent: architect] [model: opus] [effort: high] Documentation
- Depends on: Steps 2–15 and R2/R3 results (redacted).
- Files: `ARCHITECTURE.md` (§8 rewrite incl. fixing §8.1 profiles and §8.3 drift; §6.3 `gphotos` block; §6.5 capture +
  mvmeta v2; §5 entry points; §7.5 capture; §10 walkthrough; §12a rollback-neutral addendum; §13 tests; §14 constants;
  §16/§17 strike C5/A8/B10; §18; a new "read-only Google Photos RPC" subsection — allowlist, page-context calls,
  pacing, what is never called), `README.md` (fetch bullet; commands `map_photos`, `check_photos` (incl.
  `pending --list`, `--apply-run`), `resolve_photos`, `mvmeta_upgrade`, `fetch_cleanup`, `verify_restore tempdir`,
  `--manual`; where fetch downloads go now (`restore\.mv_incoming\` → `restore\`, or the tempdir location);
  `mainfetch.py locate`; crawler RPC/DOM modes; config keys `fetch_account_overrides` and `auto_confirm_after_push`;
  the auto-confirm line printed after pushes; state dirs incl. the failed list), `docs/OPERATIONS_QA.md` (new §6b Q&As),
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
    `~/Downloads` on C: first; only the verified move goes to `tempdir` — true until Step 11's destination-direct
    downloads; rewrite both to the new behavior (downloads land directly in `restore\` or the tempdir location; nothing
    in `~/Downloads`; `fetch_cleanup` for old leftovers).
- Q&As: why Google Photos shows Feb 5, 2022 for a file pushed in 2026; why a season / all chunks share one second; what
  the dedupKey is and how to check it (`adb shell sha1sum` → base64url); how fetch finds the exact item now; why some items
  live in another account and how routing handles it; how to backfill (crawl → map → resolve → apply); how to confirm
  new archivals; what `archived` vs `confirmed` mean; what to do when fetch can't find something (`--manual`,
  resolutions, kill switch); why the movies account shows UK dates; how to read the possible-loss report; what the
  "awaiting Google Photos confirmation" line after a push means and how to turn auto-confirm off; how to work the failed
  list (Excel columns, `set_search`, `resolve_photos`); what the outlier list means; which Google Photos internal calls
  MediaVault makes and why it never changes anything in the account; how to force an item's account
  (`fetch_account_overrides`); what `mvmeta_upgrade` changes on the phone; where a fetch puts its downloads, what
  happens to half-downloaded or wrong files, and what the next run does after an interrupted fetch (`🧹 Cleaned …`);
  how to clear old leftovers from `~/Downloads` (`fetch_cleanup`); why fetch refuses when the restore volume is short on
  space; the direct download (P-20) and why MediaVault never stores download links.
- Acceptance: every documented command replayed through the real parsers; no real ids.

### 🚦 R4 — Full-library sweep at the very end (OD-7 ruling) — §M16
- Depends on: Step 16 and 🚦 R3 (the mapping is applied, so the sweep checks known photo_ids first). The user may ask to
  open the PR (Step 18) before R4 finishes; default is R4 first so its numbers go into the PR test plan.
- Runs `check_photos account:<a> --approaches all` for every account (resumable, paced, per-account sessions; RPC-backed
  per OD-9), then the user reviews each run's report + `outliers.csv` and applies with `--apply-run latest`.
- Hand-over to the user (the ruling's "manually give list"): the final `outliers.csv` per account and the regenerated
  `failed_objects.csv`, plus the exact next commands (`resolve_photos failed`, `set_search`, `map_photos --resolutions`).
  Record redacted counts per account × category × outcome in PROGRESS; defects found here become Step 16b fix-ups.

### Step 17 — [model: opus] [effort: medium] IMP bookkeeping on implementation
- Depends on: Step 16 and the R3/R4 outcomes (C27 status). Consumed by: Step 18, the PR body.
- C25 → `done (feature/imp_c25_fetch_exact_gp_item, PR pending)`; C26 → `done` (already done on main if Step H shipped;
  else marked done here); C27 → status per the R3/R4 findings (done if every case is resolved or explicitly accepted by
  the user, else in_progress with the open list); C5/A8/B10 → done (absorbed); B4 note; C24 cross-reference line;
  synergy lines on E5/G2/S7/X1/X5/C7/D9; PRIORITY.md + graph (+ node validation); three-file consistency.
- OD-16 ruling ("decide later — once finished execution"): STOP and ask the user which single 👉 NEXT pointer to keep
  (planner suggestion: IMP-U7, then C24 — see Suggested next tasks); apply the answer in the same commit.

### Step 18 — [model: sonnet] [effort: medium] Final verification checklist → PR (git-agent) → 🚦 Checkpoint 1
- Depends on: Steps 0–17 done, R2/R3 recorded, R4 recorded (or the user chose PR-first at R4). If
  `git log --oneline HEAD..origin/main` is non-empty, the orchestrator
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
| 28 | `profile_for_id` consumers (fetch route, crawler, warm_profiles) | mainfetch.py:671 | prefix map | needs-fix | Step H: `fetch_account_overrides` consulted first (same signature/return); no overrides → identical results |
| 29 | CLI dispatcher branches `push`, `push_group`, `prep_push_rep`, `prep_push_rep_season`, `prep_push_rep_enrich`, `prep_push_rep_season_enrich` | main.py `if cmd == …` block (~11500–12200) | calls after the command returns | needs-fix | Step 13b auto-confirm hook AFTER a successful return only; `cmd_*` bodies untouched; tests call `cmd_*` directly so they never hit the hook (plus the conftest guard) |
| 30 | `cmd_set_search` | main.py:1535 | entry `search_term` | safe | unchanged; the failed list prints ready-made `set_search` commands (P-18) |
| 31 | `mvcommon._load_config` / `mvconfig.example.json` | mvcommon.py:83 | config keys | needs-fix | new optional keys `fetch_account_overrides` (Step H) and `auto_confirm_after_push` (Step 13b); absent keys → today's behavior |
| 32 | `webui/server.py` job runner | — | spawns commands | verify | if it spawns push-family commands: add `MEDIAVAULT_WEB_JOB=1` to that child env (Step 13b) — no other change |
| 33 | devices (`.mvmeta.json` sidecars) | — | remote JSON | needs-fix | `mvmeta_upgrade --apply` rewrites v1 → v2 atomically (Step 13c; G0 question 2) |
| 34 | Google Photos account | — | read-only RPC | new | `gprpc` allowlist only (P-9/P12); mutating rpcids (P13) never sent — pinned |
| 35 | `mainfetch` harvester + `SYSTEM_DOWNLOADS_FOLDER` | mainfetch.py:43, ~360–420 | scans `~/Downloads`, `shutil.move` | needs-fix | Step 11: incoming-dir harvesting (no `~/Downloads` scan); `~/Downloads` only in the fallback mode with run-new/name-family deletion rules |
| 36 | `_fetch_restore_folder` | mainfetch.py:~255 | restore / tempdir location | safe | unchanged — it defines `<dest>`; the incoming dir is created inside it |
| 37 | `cmd_restore` / `restore_one_extra` empty-folder cleanup | main.py ~8142/8196/8462/8494 | `os.listdir(restore_folder)` emptiness | safe | chunks are addressed by name; a leftover `.mv_incoming` only makes the empty-folder cleanup skip (documented); code NOT modified (change-gate) |
| 38 | library scanners (`_EXTRAS_EXCLUDE_DIRS`, `_RECLAIM_EXCLUDE_DIRS`, `cmd_scan_unprepped` exclusions) | main.py 5174 / 9687 / 8764 | skip `restore\` | safe | the incoming dir lives inside `restore\`, so no scanner ever sees partial downloads |

New contracts introduced: the `gphotos` block (P-1), capture v2, the learned sidecar JSONL, the failed list
(`failed.jsonl` + `failed_objects.csv`), inventory `seen_run` + RPC-mode records, the mapping/check report files
(incl. `outliers.csv`), the per-entry `restore\.mv_incoming\` dirs + the fetch journal (`~/.mediavault/fetch_journal/`),
the RPC allowlist, config keys `fetch_account_overrides` / `auto_confirm_after_push`, and the commands
`map_photos`/`check_photos`/`resolve_photos`/`mvmeta_upgrade`/`fetch_cleanup`/`locate`.

## Risks and edge cases

- Google UI/DOM/locale drift → page record primary (P-7); every stage falls through; S4 legacy unchanged; kill switch.
- Wrong-account objects (31 X-Files in MOVIES; Kuroko copies in TV) → P-4 routing + check flags; mixed batches spawn one
  session per account (longer runs).
- Enumeration timing (items backed up after the crawl) → incremental re-crawl + freshness gate + Step 9b recency rule
  (`not-backed-up-yet` vs `possible-loss`; phones opt-in) before any "missing" verdict.
- Possible cloud loss on `archived` items (local dummy) → LOUD reports, never auto-fixed; user decides (C27).
- Re-split duplicate chunk sets → set rule + alternates; SHA-256 after download is authoritative.
- IMP-C24 lost update during a real `--apply` → run when idle; the sidecar makes re-apply idempotent.
- Long crawls/sweeps vs fetch/warm-up collisions → lock + heartbeat; crawler/check yield if the lock is stolen.
- Shift+D on a directly loaded item page unverified → download-start confirmation + click-path fallback; 🚦 R2 (§M6).
- CDP download redirect/events in attach mode unverified → fallback without deletions; 🚦 R2 (§M6–M7).
- Destination-direct downloads (P-12): Chrome resolves the download folder when a download starts → entries stay
  sequential and the folder is set before each entry's first trigger; long Windows paths → short same-volume fallback
  dir or `~/Downloads` mode; files still locked by Chrome after an interruption → `pending_delete`, retried after Chrome
  is closed and at the next start; the killed Chrome must be ours → pid + user-data-dir command-line check; restore's
  empty-folder cleanup skips a folder holding a leftover → `fetch_cleanup` / the next fetch removes it; unverified in
  attach mode until 🚦 R2 (§M6–M7).
- Direct download (P-20): a minted URL is a short-lived bearer credential → memory-only, redacted wrapper, never in
  output/journal/sidecar (scan tests); the link might serve a transcoded or partial file → SHA-256 after download is
  still the gate and a mismatch falls back to Shift+D; minting may be refused for some items → Shift+D fallback.
- Account safety (search/RPC volume, OD-9 ruling allows RPCs) → read-only allowlist only, page-context calls with the
  user's own session, pacing + jitter, batching (≤50 keys), retries with backoff, circuit breaker; the OD-7 full sweep
  is resumable and can be spread over several sessions (`--max-minutes`).
- RPC drift (Google changes rpcids/payload shapes) → typed parsers fail closed (`RpcShapeError`), per-stage UI fallback
  (candidate C) or S4 legacy; the drift detector cross-checks RPC vs page record once per run.
- Auto-confirm after a push opens Chrome windows and holds the fetch lock for up to 15 min → only after success, only
  for objects ≥12 h old, skipped when the lock is held, one-line reminder instead in web jobs, disable switch in config.
- Device writes by `mvmeta_upgrade` → dry-run default, G0 clarification, tmp + atomic `mv`, re-pull verification, v1
  keys untouched, media files never touched.
- Override list vs learned account disagree → the override wins and a warning line names both (P-4).
- IMP-U7: no `*_enrich` / `enrich_metadata --library series` in any test or manual step.
- Another Claude session in the repo → pathspec commits; worktree isolation; no branch switches in the main folder.
- Machine load: the full suite took 12 min and smoke 63 s while crawls ran (P10) — judge timing only on an idle machine.

## Verification

- Step H worktree (hotfix PR): `& $PY -m pytest tests/test_fetch_account_routing.py -q`, then full suite + smoke there.
- `& $PY -m pytest tests/test_gpweb.py tests/test_gprpc.py tests/test_gp_inventory.py tests/test_gp_capture.py tests/test_gphotos_primitives.py tests/test_gphotos_apply.py tests/test_device_inventory.py tests/test_map_photos.py tests/test_fetch_routing.py tests/test_fetch_account_routing.py tests/test_fetch_harvester.py tests/test_fetch_hygiene.py tests/test_check_photos.py tests/test_auto_confirm.py tests/test_mvmeta_upgrade.py tests/test_fetch_manual.py tests/test_resolve_photos.py tests/test_c25_regression_pins.py tests/test_entry_schema_guard.py -q`
- `& $PY -m pytest tests/test_rollback.py tests/test_baseline_happy_path.py tests/test_extras.py tests/test_trigger_download_retry.py tests/test_session_detector.py tests/test_cli_parsers.py -q` (unchanged files, must be green)
- `& $PY -m pytest tests -q`
- `& $PY -m pytest tests/smoke -q`

## Out of scope

Rewriting DateUTC (D1); agent effort (D5); the U6 token-brackets branch (D6); any MUTATING internal RPC (P13 —
nothing in this task needs one, although the OD-9 ruling would allow it); downloading through anything other than the
automation Chrome (no cookie export); creating missing phone-side sidecars (13c only upgrades existing ones); gphotosdl
adoption (IMP-G2/S7); multi-account replication (IMP-X1 — the `accounts` map is ready for it); fixing IMP-C24 itself;
deleting stale/duplicate cloud items; `cmd_restore`/`restore_one_extra` changes; a new `status` value; sidecar/report
compaction; tzdata; the Others Pixel serial placeholder (IMP-D18).

---

## Branch, PR and checkpoints

- **Three PRs:** (1) PR #60 `feature/imp_c25_interim_capture` — interim capture + crawler (exists; merge is
  user-gated); (2) the Step H hotfix PR `fix/imp_c26_fetch_account_routing` (worktree
  `C:\Users\harin\PycharmProjects\MediaVault-c26`) — title `fix: fetch routes objects to their real Google account
  (mvconfig overrides + per-account sessions) — IMP-C26` — **merged only after user approval (ruling 2026-09-28; was: auto-merge, user-authorized
  2026-09-27)**; (3) the main PR below (merge user-gated). (1) and (2) are independent and may land in either order;
  Step 1 merges main into the feature branch after each lands.
- **Branch:** `feature/imp_c25_fetch_exact_gp_item` (worktree `C:\Users\harin\PycharmProjects\MediaVault-c25`).
- **PR title:** `feature: fetch the exact Google Photos item (content key, hash lookup, taken-second locator, per-object account, map/check/resolve) — IMP-C25/C27`
  (append `/C26` only if Step H was skipped and C26 ships here).
- **PR body order:** auto-generated summary (Summary / Changes / Test plan incl. redacted real-library dry-run + apply
  numbers) → `## Original task prompt` with the COMPLETE verbatim prompt from the appendix → `🤖 Generated with [Claude Code](https://claude.com/claude-code)`.
- **🚦 Checkpoint 1:** STOP after opening the PR — merging to main is the user's call.
- **🚦 Checkpoint 2:** after merge and only on approval: annotated tag `archive/feature/imp_c25_fetch_exact_gp_item`
  (merge info + revive steps), push the tag, delete the branch locally and remotely; remove the worktree
  (`git worktree remove C:\Users\harin\PycharmProjects\MediaVault-c25`). Same procedure for the Step H branch
  (`archive/fix/imp_c26_fetch_account_routing`, worktree `MediaVault-c26`) after its merge.

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
  `inconsistencies.csv`, `resolutions_todo.csv`. Ran 2026-10-03 (R1 + 9b re-run `map_r1b_20261003_1714`): mapped 2,020 ·
  wrong-account 31 (override covers 31) · inconsistent 4 · not-backed-up-yet 23 · possible-loss 15 · others stale 35.
- **M4 — locate probes (read-only, 🚦 G12):** `& $PY mainfetch.py locate tv-en-2014-siliconvalley-s02e03` (expect S0/S1,
  proof dedupkey), `… s02e02` (filename proof), one X-Files episode from the wrong-account list (expect account movies),
  `… tv-en-2019-chernobyle02` (3 chunks, one shared second), one split movie, one anime episode.
- **M5 — check samples:** `& $PY main.py check_photos tv-en-2014-siliconvalley-s02 --approaches all` ·
  `check_photos series:tv-en-2014-siliconvalley-s02` (prints the resolved seasons first) ·
  `check_photos account:anime --limit 20`.
- **M6 — fetch direct (S0):** size check first
  (`& $PY -c "import mvcommon;e=mvcommon.load_library()['tv-en-2014-siliconvalley-s02e02'];print(e['tech_spec'].get('size_bytes'))"`),
  then `& $PY main.py fetch tv-en-2014-siliconvalley-s02e02 tempdir D:\MV_fetch` → expect S0 + `direct download`
  (P-20: no search, no page load; the URL is never printed), the download appears in
  `D:\MV_fetch\tv-en-2014-siliconvalley-s02e02\restore\.mv_incoming\` and is renamed into that `restore\` folder
  (`✅ MOVED`), the `.mv_incoming` folder is gone afterwards, nothing new in `~/Downloads`, learned record; `& $PY main.py verify_restore tv-en-2014-siliconvalley-s02e02 tempdir D:\MV_fetch`. Record for P14:
  did the direct download start (yes/no), did SHA-256 match (i.e. original bytes), how long it took vs a Shift+D
  fetch. Optional read-only probe for the follow-up task (Suggested next tasks #10): `& $PY mainfetch.py locate
  tv-en-2014-siliconvalley-s02e02 --probe-direct` reports whether the minted URL also answers without browser cookies
  (HEAD request only; nothing downloaded or printed except yes/no + content type).
- **M7 — chunk set + interrupt + no-tempdir:** `& $PY main.py fetch tv-en-2019-chernobyle02 tempdir D:\MV_fetch` → 3
  verified chunks; interrupt once with Ctrl+C mid-download (and once by closing the terminal window), re-run → the
  `🧹 Cleaned N leftover file(s)` line, no `.crdownload` or `.mv_incoming` left anywhere (check with
  `Get-ChildItem D:\MV_fetch -Recurse -Force -Filter *.crdownload`), verified chunks not re-downloaded; note whether the
  log says "cancelled via CDP" or "Chrome terminated". Then one small title WITHOUT `tempdir`: the files land directly
  in `<title folder>\restore\` (disk pre-flight line first).
- **M18 — clean old leftovers (one-time):** `& $PY main.py fetch_cleanup` (dry-run) → review the list of old fetch
  leftovers in `~/Downloads` (only names that belong to your library) and any `.mv_incoming` leftovers → the same
  command + `--apply` deletes exactly that list.
- **M8 — wrong-account fetch:** fetch one X-Files episode from the list → runs in the MOVIES profile.
- **M9 — fetch_restore end-to-end + web:** `& $PY main.py fetch_restore <small archived id> tempdir D:\MV_fetch` →
  `& $PY main.py check <id>` → `& $PY main.py replace <id>`; then `& $PY main.py web` → Fetch & Restore a small title →
  progress ring correct.
- **M10 — manual mode:** `& $PY main.py fetch <id listed as missing> --manual` → follow the printed block.
- **M11 — new archival + confirm:** `prep` + `push … device series` a new small MKV (from the worktree), then
  `& $PY main.py check_photos pending --list` (offline — the object is listed `unverified`), wait for the Pixel backup,
  `& $PY main.py check_photos pending --apply` → the node shows `confirmed`, `verified_by: dedupkey`, approach
  `hash`/`photo_id`.
  Include C1c: Daredevil S01E01 `[4a9028]` should read May 4, 2024 7:18 AM GMT+08:00 (taken_ms 1714778282000).
- **M12 — real-library apply (🚦 R3; no other MediaVault command running — OD-2):** back up the libraries first
  (`Copy-Item C:\Media\library_*.json D:\MV_lib_backup\<date>\`); `& $PY main.py map_photos all --resolutions <filled csv>`
  (dry-run) → review → the same command + `--apply`; then per account `& $PY main.py check_photos account:<a> --approaches best`
  (dry-run, long; resumable) → review the report → `& $PY main.py check_photos account:<a> --apply-run latest` (no new
  searches); finally `& $PY main.py check_photos hinted` → review → `--apply-run latest`. Expect: every mapped object
  `confirmed`, the X-Files episodes with `home_account` movies, the C27 list unchanged unless the user resolved rows.
- **M13 — kill switch sanity (optional):** set `EXACT_LOCATOR_ENABLED = False` locally (do not commit), fetch → today's
  behavior; revert.
- **M14 — auto-confirm on the next push (OD-6):** a day after M11's push, run your next normal push/`prep_push_rep*`
  from the worktree → at the end look for the auto-confirm summary (Chrome opens briefly for the needed account) →
  `& $PY main.py check_photos pending --list` no longer lists the confirmed objects. Disable test: set
  `"auto_confirm_after_push": false` in the worktree's `mvconfig.json` → the next push prints nothing extra.
- **M15 — manual confirm/match (OD-14):** `& $PY main.py resolve_photos failed --account tv` → paste the right item's
  URL for one row (expect `dedupkey` or `filename` proof), enter a search term for another (expect an S3u try), mark one
  `not uploaded`, press `q`, re-run → answered rows are skipped; decline the final apply prompt once (library unchanged),
  then accept it.
- **M16 — full-library sweep at the end (🚦 R4, OD-7):** per account `& $PY main.py check_photos account:tv --approaches all`
  (long; after any interruption re-run the same command — it resumes) → read `~/.mediavault/gphotos/check/<ts>/`
  `report.txt` + `outliers.csv` → `& $PY main.py check_photos account:tv --apply-run latest`; repeat for `movies`,
  `anime` (and `others` once its profile exists); then open `~/.mediavault/gphotos/failed_objects.csv` in Excel and work
  it (M15, `set_search`, or `map_photos all --resolutions <csv>`).
- **M17 — phone-side sidecars (OD-4):** `& $PY main.py mvmeta_upgrade all device <alias>` (dry-run) → review → only if
  you said yes at G0: the same + `--apply`; spot-check one with
  `adb -s <serial> shell cat "/sdcard/Media/<name> [<sid>].mvmeta.json"` (v1 keys unchanged, new v2 keys present).

## Suggested next tasks after IMP-C25

(OD-16 ruling: the single 👉 NEXT pointer is chosen by the user at Step 17, after execution. Planner suggestion below.)

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
10. **Resumable direct transfer (new, from the P-20 direct-download idea):** if the §M6 `--probe-direct` evidence shows
    the minted original-download URL answers without browser cookies, move the byte transfer from Chrome's download
    manager to Python with HTTP Range resume — multi-GB chunks resume after a crash instead of restarting, with exact
    progress and no browser-download housekeeping. URLs stay in memory only (P-20). Register as a new IMP after C25.

---

## Appendix A — IMP registration text (Step 0c, verbatim into `improvements_tierC.md`)

### IMP-C25: Fetch the EXACT Google Photos item — content key + taken-second locator + remembered photo_id + per-object account; map/check/confirm the archived library

- Category: other
- Priority: high
- Files: `gpweb.py` (new), `gpcapture.py`, `mvcommon.py`, `main.py`, `mainfetch.py`, `tools/gp_inventory.py`, tests, docs. Plan: `docs/feature-fetch-datetime/PLAN.md`; research: `docs/feature-fetch-datetime/RESEARCH.md`.
- Current behavior: fetch types filename/`search_term` queries, clicks a tile by position, downloads it and routes by SHA-256; wrong downloads are left in `~/Downloads` (RESEARCH F10). Text search is not an identity (F16/F18/F25), a release batch shares one second (F13), the primary tile selector is dead on search pages (F14), fetch routes by id prefix although 31 archived X-Files episodes live in the MOVIES account, and `tempdir` does not keep downloads off C:.
- Proposed change: SHA-1 content key (= dedupKey) and DateUTC captured at prep/push (interim capture, PR #60); per-object versioned `gphotos` identity block with per-account learned record, approach term and confirmed-in-cloud marker; inventory crawl (read-only RPC, DOM fallback) → mapping (normalized names, dedupKey cross-checks, re-split sets, wrong-account, device cross-check) → user resolutions → `--apply`; `check_photos` (download-free verification by item/season/series/account/pending/all, RPC hash lookup, outlier list); auto-confirm of unverified pushes at the end of the next push; evidence-ordered waterfall (photo_id → SHA-1 hash lookup → taken second → exact name → title+short_id → user search term) with today's flow as the unchanged last resort; per-object account routing; failed list + `resolve_photos` manual confirm/match; downloads directly into the restore folder (or tempdir location) through a disposable `.mv_incoming` dir + fetch journal + interruption cleanup + `fetch_cleanup` for old `~/Downloads` leftovers; `--manual`; resumable fetch_restore; `mvmeta_upgrade` for the existing phone-side sidecars.
- Rationale: identity by content, proven before any bytes move; every archived object becomes directly addressable.
- Goal: every archived object fetchable by direct photo_id in its real account; new archivals confirmed after backup; zero regression when data is absent.
- Effort estimate: large
- Risk: medium — rewrites fetch selection behind a kill switch with today's flow as last resort; capture is post-commit (rollback-neutral, OD-3); `gphotos` is an optional nested block (ENTRY_TYPE_KEYS doc + guard); library writes through one apply engine with a fingerprint re-check (IMP-C24 note); internal RPCs are read-only and allowlisted.
- If skipped: fetch keeps guessing by text and tile order; 31 wrong-account episodes stay unfetchable; possible cloud-loss cases stay invisible until a restore fails.
- Cross-references: absorbs C5, A8, B10; partial B4; fixes C26; tooling for C27; synergies E5, G2/S7, X1/X5, C7, D9; interplay C24.
- Status: in_progress (feature/imp_c25_fetch_exact_gp_item)

### IMP-C26: Fetch routes by id prefix — objects that live in another Google account are unfetchable

- Category: bug
- Priority: high (Band 0 — breaks restore for real archived items)
- Files: `mainfetch.py` (`profile_for_id`, `cmd_fetch_route`), `mvconfig.example.json`
- Current behavior: `mainfetch.profile_for_id` picks the Chrome profile from the id prefix; the 2026-09-25 mapping found 31 X-Files episodes (`tv-en-1994/1995/1996-xfiles`, seasons 2–4) in the MOVIES account (Kuroko's Basketball copies also sit in the TV account besides the anime account).
- Proposed change: immediate fix (IMP-C25 Step H, its own small PR from main): a local `mvconfig.json` `fetch_account_overrides` list (exact id or prefix → account) consulted first by `profile_for_id`, and `cmd_fetch_route` runs one Chrome session per account for a mixed batch; then IMP-C25 adds the learned per-object `home_account` beneath the override (P-4).
- Effort estimate: small (hotfix) · Risk: low — no overrides configured ⇒ byte-identical behavior.
- If skipped: those 31 episodes cannot be restored.
- Status: done when the Step H PR merges (registered by that PR); if Step H is skipped, registered + fixed inside IMP-C25 Step 10.

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
6. If `## In-progress sub-state` names a running step: apply PLAN "In-step checkpoints" rule 3 (keep or discard the
   interrupted sub-task's uncommitted changes, record it) and re-dispatch that step from the named sub-task.
7. If the operations log shows a live command without a result line: re-run its recorded resume command.

## Step status
| Step | Description | Status | Model used | Completing SHA | Tests | Notes |
|---|---|---|---|---|---|---|
| 0 | plan (0a 16e292c + 0a′ revision 2) + journal + IMP registration | in_progress | | 16e292c | | 0a done |
| G0 | rulings recorded (2026-09-25) + clarifications | pending | user | | | OD-1: H confirmed, PR stops for approval (2026-09-28) · q2 OD-4 ANSWERED 2026-09-28: mvmeta_upgrade may rewrite phone sidecars with --apply only · q3 P-12 download design open (before 11) |
| 1 | sync with main (after #60 / after H) | pending | | | | repeatable |
| H | C26 hotfix PR (worktree MediaVault-c26) | pending | | | | confirmed; PR STOPS for user approval (ruling 2026-09-28) |
| RH | user fills fetch_account_overrides + tests | pending | user | | | after H merges |
| 2 | gpweb + RPC parsers + fake Google Photos | pending | | | | |
| 2b | gprpc read-only RPC client | pending | | | | |
| 3 | mvcommon identity primitives + failed list | pending | | | | |
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
| 13b | auto-confirm on next push | pending | | | | |
| 13c | mvmeta_upgrade | pending | | | | apply per G0 q2 |
| 14 | --manual + resolve_photos + resume | pending | | | | |
| 15 | smoke + pins + disarm probes | pending | | | | |
| R2 | live verification pack (user) | pending | user | | | |
| R3 | real-library apply (user) | pending | user | | | |
| 16 | docs (architect) | pending | | | | |
| R4 | full-library sweep + outlier/failed lists (user) | pending | user | | | at the very end (OD-7) |
| 17 | IMP bookkeeping + NEXT pointer question | pending | | | | OD-16 |
| 18 | final verification + PR | pending | | | | 🚦 Checkpoint 1 |

## Multi-candidate tracking (Step 12)
Worktrees C:\Users\harin\PycharmProjects\MediaVault-c25-cand\step-12\A|B|C · branches …__cand_a|b|c · DECISION .candidates/c25-step-12/DECISION.md · tags candidates/imp_c25/step-12/<x>-chosen|rejected · user pick: PENDING

## Operations log (crawls, dry-runs, applies — redacted counts only)
| When | Command | Result (counts) | Resume command |
|---|---|---|---|

## In-progress sub-state
_(none)_ — when a step is running (Execution protocol "In-step checkpoints"):
`Step N — dispatched <time> to <model> · sub-tasks: [x] N.a <what> (wip SHA) · [ ] N.b <what> ← next · [ ] N.c …`
plus, after a crash, one line `resumed <time> by <model>: uncommitted N.b changes kept|discarded (<files>)`.

## Blockers / human gates
- G0 clarifications left: q2 OD-4 device rewrites (before 13c/R3) · q3 P-12 download design (before Step 11) · RH
  override list · R0
  prerequisites · R1/R2/R3/R4 real-data gates · G12 candidate pick · Step 17 NEXT-pointer question (OD-16) ·
  Checkpoint 1 · Checkpoint 2
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
