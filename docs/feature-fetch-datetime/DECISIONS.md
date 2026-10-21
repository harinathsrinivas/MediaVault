# IMP-C25 — Decisions (exact Google Photos item fetch)

- **Task:** IMP-C25 (+ IMP-C26 fix via Step H, IMP-C27 tooling) — fetch the EXACT Google Photos item for every uploaded
  object: content key (SHA-1 = dedupKey) + taken-second locator + remembered photo_id + per-object account, backfilled
  from a full inventory mapping, with check mode, download hygiene, manual mode and a resumable fetch/restore.
  Framework: v2.
- **Branch:** `feature/imp_c25_fetch_exact_gp_item` · **Working folder:** `C:\Users\harin\PycharmProjects\MediaVault`
  (the main folder since 2026-09-28 ~20:00 — RELAYED instruction, §1b W3, first-hand confirmation pending; the
  `MediaVault-c25` worktree used for Steps 0–3 is detached and idle)
- **Plan:** `docs/feature-fetch-datetime/PLAN.md` (0a′ = `d29bb03`) · **Journal:** `PROGRESS.md` (this folder) ·
  **Research:** `RESEARCH.md`, `INVENTORY_AND_CAPTURE.md` (this folder).
- **Status of this file:** every card is either a verbatim copy from PLAN.md (§2 locked decisions + scope additions,
  §5 planner decisions, §6 OD rulings — step/§/appendix references inside them point into PLAN.md) or a first-hand
  user answer recorded with its date (§1). All 17 Open Decisions were ruled by the user on 2026-09-25. **One G0 item
  is still PENDING: P-12** (§1). Nothing here is an unconfirmed planner draft.
- **Model provenance:** research — main session Opus 5.5 (`/effort max`), 2026-09-24/25, Fable probe OK
  (RESEARCH.md header) · plan — planner-v2 under fable (`claude-fable-5-1`); PLAN.md header: planned 2026-09-24,
  completed 2026-09-25 after four account-limit interruptions, "never degraded"; revisions 3–6 (2026-09-27/28) per
  the planning journal; plan commits `16e292c`, `5fd4843`, `d29bb03` carry `Claude Fable 5.1` + `Claude Opus 5.5`
  co-author trailers · Step 0b/0c (this file, PROGRESS.md, IMP registration) — executor-opus, Opus 5.5
  (`claude-opus-5-5`), 2026-09-28 (Step 0 is `[model: opus]`; no fable waterfall involved) · later steps — PROGRESS.md
  "Model used" column.
- **Last updated:** 2026-09-28 (Step 0b; §1b working agreements added 20:00)

---

## 1. 🚦 G0 status — as of 2026-09-28 (user decision cards; first-hand)

Recorded verbatim from the orchestrating main session's Step 0 brief (2026-09-28), which states that these answers
came first-hand from the user's decision cards (not through a relayed/non-user channel):

> G0 status as of 2026-09-28 (user decision cards, this is first-hand): plan approved; OD-1: Step H confirmed, its PR
> STOPS for user approval (no auto-merge — supersedes the relayed 2026-09-27 wording); OD-4 ANSWERED: "Yes, with
> --apply only" — mvmeta_upgrade may rewrite existing phone .mvmeta.json sidecars only when run with --apply (dry-run
> default); P-12 (destination-direct download design) STILL OPEN — ask before Step 11. PR #60 merged to main as
> ac563a0 (squash) on 2026-09-28.

| # | G0 item (PLAN.md 🚦 G0) | Answer | Date · channel | Effect on the plan |
|---|---|---|---|---|
| — | Plan approval | **Approved** | 2026-09-28 · decision card | Execution proceeds per PLAN.md (0a′ `d29bb03`). |
| 1 | OD-1 — Step H (small immediate-fix PR for IMP-C26) and how its PR lands | **Step H confirmed; its PR STOPS for user approval (no auto-merge)** | 2026-09-28 · decision card | Step H runs right after Step 0 (in parallel with Steps 1–2). 🚦 Checkpoint 1 applies to the Step H PR: open it, STOP, the user approves, then squash-merge. Supersedes the "auto merge pr also" wording of 2026-09-27 — PLAN.md's planning journal lists that wording as first-hand and §Branch calls it "user-authorized", while PLAN.md Step H and the G0 status above call it "relayed"; moot either way, the 2026-09-28 card wins. |
| 2 | OD-4 — "Rewrite the existing `.mvmeta.json` sidecars on the Pixels to v2 (Step 13c `--apply`, after a dry-run you review)?" | **"Yes, with --apply only"** | 2026-09-28 · decision card | Step 13c ships with `--apply` enabled: `mvmeta_upgrade` may rewrite existing phone `.mvmeta.json` sidecars ONLY when run with `--apply`; dry-run stays the default (§M17: dry-run → review → `--apply`). The "no → 13c ships dry-run/report only" branch is NOT taken; the `device rewrites disabled (OD-4 clarification)` mode is not the default. |
| 3 | P-12 — destination-direct download design (the relayed download-hygiene request needs first-hand confirmation) | **PENDING** | — | Blocks nothing before Step 11. Ask before Step 11 (at the next natural stop). Question text, verbatim from PLAN.md 🚦 G0: "Fetch will download straight into each title's `restore\` folder (or the `tempdir` location when you give one) through a temporary `.mv_incoming` folder; only SHA-256-verified files are kept, everything else and every half-downloaded file is deleted, an interrupted fetch is cleaned up at the start of the next one, and nothing lands in `~/Downloads` any more (`fetch_cleanup` removes old leftovers after you review its list). OK?" → yes: record verbatim here; no: STOP before Step 11 and re-dispatch planner-v2 (fallback = the OD-13 staging dir `~/.mediavault/staging/<account>` with the same journal/cleanup rules). |

- The second relayed request of 2026-09-27 ("keep everything resumable, each step and even this planning step. for
  limit crashes mainly") needs no separate ruling: it restates the original task prompt's resumability requirement
  (PLAN.md Appendix — "make each step, each execution or task resumable…").
- PR #60 (`feature/imp_c25_interim_capture` — interim identity capture + inventory crawler) was squash-merged to
  `main` as `ac563a0` on 2026-09-28; Step 1 merges it into this branch.

---

## 1b. Working agreements — 2026-09-28 (W1, W2 first-hand user messages · W3 RELAYED — first-hand confirmation pending)

**W1 — decisions as in-chat cards; independent work continues; status visible in the user's folder:**

> Tell me what exactly is the decisions needed,in decision card format. each one should have an option for me to type or edit freely also.
> then use the selected answer as decision. use this method for most questions or intereactive decisions, as I'm not on the worktree its difficult for me to open the .md and see and respond. so keep everythin in this chat. but in the background keep doing the other tasks  in a resumable way - if there are independant tasks. keep all resumable. TOtal how many steps are there? can you just sync the status.md and plan.md in my current branch - you can git ignore it also.
> but I want to see the status and remaining steps properly.
> if you want me to actually see some .md - you can add to current interim capture branch also.

→ Every question to the user is an in-chat decision card: a few options plus a free-text answer. The picked or typed
answer IS the decision and is recorded verbatim in this file. The user is never asked to open a .md file to answer.
Independent steps keep running (each resumable) while a card is open. Status and remaining steps are shown in chat and in
PROGRESS.md "At a glance". Total steps: 30 tracked items = 23 agent steps + 7 user gates (PROGRESS.md).

**W2:** "limits back. pl continue" → resume the pipeline after the usage-limit pause.

**W3 — working location. RELAYED, not first-hand.** This message reached executor-opus during Step 0 marked as coming
from a NON-USER source, most likely the orchestrating session forwarding the user's words. It was acted on as a
course correction from the launching agent. It is NOT a user ruling until the user confirms it first-hand on the
"Work folder" decision card (asked 2026-09-28). That mirrors how the relayed requests of 2026-09-27 are handled (§1,
P-12). Once confirmed, it supersedes the worktree part of OD-1 and PLAN.md's "Working location (binding)" note.

> leave the work tree approach, all agents expect  plan and status file here. jus checkout actual branch . should be fine.

→ Done 2026-09-28 ~20:00 by executor-opus.
- **P-16 set aside:** this deliberately overrode P-16's "never switch branches in the main repo folder", because the
  relayed message asked for exactly that.
- **Safety checks first:** both trees were verified clean (the main folder had no staged or modified tracked files)
  before anything was switched.
- **What changed:**
  - The `MediaVault-c25` worktree was detached at `31e8214`; nothing was deleted.
  - The main folder was switched from `feature/imp_c25_interim_capture` (`70635cb`) to
    `feature/imp_c25_fetch_exact_gp_item` (`31e8214`).
  - Root `PLAN.md` is now a byte copy of docs/feature-fetch-datetime/PLAN.md (gitignored).
  - The worktree's STATUS.md log (Steps 2, 2b, 3) was appended to the root `STATUS.md` here.
- **Tests in this folder afterwards:** full 1544 passed, smoke 81 passed.
- **Found at 20:04:55, after the switch:** a Step 4 executor was already working in the detached `MediaVault-c25`
  worktree (it had been dispatched there in parallel). Its work is intact and nothing was touched. Before committing,
  bring it into this folder (PROGRESS.md ▶ NEXT ACTION).
- **Effect on W1:** its "sync into my current branch" is moot, since the branch itself is checked out here; nothing
  was committed to the interim-capture branch.
- **Still to do:** PLAN.md's text still names the worktree, so a small planner-v2 revision should follow once the card
  confirms the move. Step H keeps its own worktree `MediaVault-c26` until PR #61 merges. Whether Step 12's temporary
  candidate worktrees are still wanted is asked before Step 12.
- **What the card must settle:** MediaVault commands run from this folder now execute the in-development C25 branch.
  The user chooses between archiving only between code steps, pausing archiving, or reverting.
- **Revert procedure:**
  1. Commit the pending journal edits on the C25 branch.
  2. In the main folder: `git switch feature/imp_c25_interim_capture`.
  3. Then: `git -C C:\Users\harin\PycharmProjects\MediaVault-c25 switch feature/imp_c25_fetch_exact_gp_item`.

  The root PLAN.md and STATUS.md copies stay visible either way, because they are gitignored.

---

## 2. Locked decisions + user scope additions — verbatim (PLAN.md §"Locked decisions (user, 2026-09-24/25 — do not re-open)")

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

(The research-stage decisions D1–D6 and the user's acceptance "ok go ahead with all these", 2026-09-24, are in
`RESEARCH.md` §6.)

---

## 3. Research digest — by reference (`RESEARCH.md` F1–F26, verified live 2026-09-24/25)

| # | Finding (key numbers) |
|---|---|
| F1 | Google Photos dates an MKV by its **`DateUTC`** (segment-info muxing date) — not the file mtime, not the upload time. S02E03: DateUTC `2022-02-05T11:49:46Z` → shown as Feb 5, 2022 · Sat, 7:49 PM · GMT+08:00. |
| F1b / F23 | Info panel: date, weekday + time to the minute, GMT offset, the exact uploaded filename, resolution; **no size**; the year is omitted for current-year items. |
| F2 | MediaStore `datetaken` is NULL for every MKV on the Pixel (Android 10) — Google Photos reads the container itself. |
| F3 | `adb push` copies the PC mtime to the second; the `.partial` → final `mv` keeps it. |
| F4 | `mkvmerge --split` stamps **all chunks of one split with the same DateUTC** (split start) — e.g. 3/3 chunks at one second. |
| F5 | `mkvmerge -J` exposes `container.properties.date_utc`. |
| F6 | Dummies are fresh ffmpeg files → an archived title's original DateUTC is gone from `C:\Media`. |
| F7 | Library scope: 1,295 leaves (1,281 archived), 259 split entries / **963 chunks**, 1 FLAC holder, 23 extras items → ~2,000 Google Photos items; 1,291 `.mkv`, 3 `.mp4`, 1 `.mkv.ts`. No entry had any timestamp/SHA-1 field. |
| F8 | The Pixel `FA75V0303405` held 7 videos on 2026-09-24. |
| F9 | Daredevil S01E01–E06 DateUTCs recorded (C1c reference: E01 → May 4, 2024 07:18:02 local, taken_ms 1714778282000). |
| F10 | Today's fetch: attempt 1 types the plain library `filename` (no ` [short_id]`) and clicks tile 0; attempt 2 types `search_term` and clicks tile `i` for chunk `i`; the harvester SHA-256s new files in `~/Downloads`; a wrong download is left there. |
| F11 | Day search works in any date format (4 formats → the same 38 items); time of day is not a search filter. |
| F12 | Result tiles carry the local time **to the second** in `aria-label` (U+202F before AM/PM). |
| F13 | **A release batch shares one second:** Feb 5, 2022 = 38 videos = Silicon Valley S01–S04, every season at a single second (8/8, 10/10, 10/10, 10/10). |
| F14 | The primary tile selector `a[href*='./photo/']` is dead on search pages (fetch works only via the `background-image` fallback); a background tab never renders results. |
| F15 / F24 | `mkvpropedit … --set date=` rewrites DateUTC in place: size unchanged, SHA-256 changes, 322 bytes differ (header only) — not adopted (D1). |
| F16 | Short id alone is inconsistent: 1 / 3 / **133** results. |
| F18 | Text search is phrasing-sensitive (0 vs 1 result for the same intent); an exact chunk filename → exactly 1. |
| F20 | Every item page embeds a record `[photo_id, [thumb, w, h, …], taken_ms, dedupKey, tz_offset_ms, upload_ms, …]` — S02E03: taken_ms `1644061786000` (= DateUTC exactly), tz `28800000` (+8 h). |
| F21 | **dedupKey = URL-safe base64 of the SHA-1 of the exact uploaded bytes — CONFIRMED** (worked S02E03 example: PLAN.md pre-resolved fact P2). A content proof readable before downloading. |
| F22 | Day search uses the item's **local** date (its timezone), not UTC. |
| F25 | Legacy text search is unreliable (15 TV objects): full `search_term` exactly-one 6/11 whole files; plain filename (today's attempt 1) exactly-one 3, **zero results 4**; title + short id 1/11; exact chunk filename 3/3. |
| F26 | Accounts: TV 1,109 items vs ~1,126 expected objects; movies 795 vs ~717; anime profile was signed out (signed in 2026-09-25, 🚦 R0); no `ChromeProfile_Others` exists; movies mostly split (128/176, up to 15 chunks). |

F17 and F19 are not assigned in `RESEARCH.md` (numbering gaps). Pre-resolved facts P1–P14 (formats, RPC shapes,
environment, baseline) live in PLAN.md §"Pre-resolved facts".

---

## 4. Mapping evidence — counts only (first read-only mapping, 2026-09-25)

Library ↔ inventory by exact uploaded filename (PLAN.md §Context; IMP-C27). The reports themselves stay outside the
repo (`D:\MediaVault_gp_inventory\`) — private (P-15).

| Measure | Count |
|---|---|
| Matched by exact uploaded filename | tv **1,065 / 1,126** · movies **688 / 717** · anime **157 / 158** (others: no account/profile yet) |
| Objects living in another account (X-Files episodes in the MOVIES account → IMP-C26, P-4) | **31** |
| Whitespace near-matches (innerText collapses runs of spaces → P-6 normalized names) | **19** |
| Re-split chunk sets uploaded twice under the same names | **7** |
| IMP-C27 (a) library says whole file, cloud holds chunks (`split_info` lost) | **3** |
| IMP-C27 (b) uploaded under an old untagged name | **1** |
| IMP-C27 (c) last chunk missing from the cloud and from both Pixels | **2** |
| IMP-C27 (d) archived episodes in NO account | **20** (13 of one series — 4 pushed then freed on the Pixel, never backed up; 9 absent from both Pixels — plus 7 of another series, absent everywhere) |
| Earlier "missing" objects resolved by the incremental re-crawl / retries (IMP-C27 marks 5 of them "backed up") | **8** |

All IMP-C27 objects are `archived` (the local copy is a dummy). Case list: `improvements/improvements_tierC.md` IMP-C27.

---

## 5. Planner decisions P-1 … P-20 — verbatim (PLAN.md §"Planner decisions (binding for executors — recorded in DECISIONS.md)")

**The user message that prompted P-20 (revision 3), 2026-09-27, first-hand** — as quoted in PLAN.md's revision-3
header line and planning journal: "store the download url itself and directly use them to fetch". Realized as direct
download: the permanent `photo_id` is stored and a fresh original-quality download URL is minted at every fetch; URLs
themselves are never stored (they expire and act as bearer credentials).

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

**Status annotations (not part of the verbatim text above):** P-12 — first-hand confirmation **PENDING** (G0 item 3,
§1; ask before Step 11). P-4 — Step H's `fetch_account_overrides` is confirmed (OD-1, §1).

---

## 6. Open-Decision rulings OD-1 … OD-17 — verbatim (PLAN.md §"Rulings (user, 2026-09-25 — binding)")

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

**Later first-hand answers that amend rows above (2026-09-28, §1):**
- **OD-1:** the Step H PR does **NOT** auto-merge — it STOPS for user approval (🚦 Checkpoint 1); the "auto merge pr
  also" wording is superseded.
- **OD-4:** the G0 clarification is answered **"Yes, with --apply only"** — Step 13c keeps `--apply` (device sidecar
  rewrites only with `--apply`; dry-run default).
- **OD-13 / P-12:** the destination-direct design still awaits first-hand confirmation — **PENDING** (ask before Step 11).

The options and planner recommendations the user ruled on remain in PLAN.md §"Open Decisions" → "As proposed (record)".

---

## 7. Candidate outcomes (filled in as they happen)

### Step 12 — Locator engine, 3 candidates (🔀; 🚦 G12) — PENDING
- Candidates (PLAN.md Step 12): **A** UI-first, no RPC for locating · **B** RPC-first · **C** hybrid arbitration
  (RPC-first per stage, per-stage DOM fallback, drift detector). All `[candidate-model: fable]`.
- Worktrees / branches: `C:\Users\harin\PycharmProjects\MediaVault-c25-cand\step-12\A|B|C` ·
  `feature/imp_c25_fetch_exact_gp_item__cand_a|b|c` — _not created yet_
- Judge-v2 decision (`.candidates/c25-step-12/DECISION.md`): _pending_
- Live read-only `locate` probes (§M4, optional, per worktree): _pending_
- User pick (OD-17 ruling "yess ok": judge-v2 recommends, the user picks): _pending_
- Archive tags `candidates/imp_c25/step-12/<x>-chosen|rejected`: _pending_

No other step of this plan uses candidates.

## §1c — User rulings 2026-09-28 (decision cards, first-hand in the main session)
- **Work folder:** "Main folder; I pause archiving" — all C25 work happens in the main folder `C:\Users\harin\PycharmProjects\MediaVault` on `feature/imp_c25_fetch_exact_gp_item`; the user runs no MediaVault commands there until C25 merges. The `MediaVault-c25` worktree was retired (Step 4's partial `main.py` edit moved into the main folder). This supersedes P-16's "never switch branches in the main repo folder" for this task.
- **PR #61 (IMP-C26):** "Merge now" — squash-merged as 494fbec.
- **Overrides:** "You add it" — the main session adds the X-Files `fetch_account_overrides` entries to the gitignored `mvconfig.json` (edit only that key; print only the entry count).
- **P-12 downloads:** "Yes, as described" — destination-direct downloads into `restore\` (or tempdir) via `.mv_incoming`, SHA-256-verified files only, cleanup on next run, nothing in `~/Downloads`, `fetch_cleanup` for old leftovers.
- **Limits:** "continue automatically when limits hits … and resume automatically."

## §1d — User rulings 2026-10-01 (decision cards, first-hand in the main session)
- **IMP-C28** (`cmd_push` treats any path containing `_parts` as a chunk → untagged upload + local master deleted):
  "yes fix it" → hotfix branch `fix/imp_c28_push_parts_substring` from main (worktree `MediaVault-c28`), own PR,
  Checkpoint 1 applies.
- **🚦 RH test fetch**: "yes do that". Result: routing to `movies` worked (`[Account] Profile … 'movies'`); the search
  found 0 because Selenium attached to Chrome 154's Gemini side panel (`gemini.google.com/glic` webview) instead of
  the photos.google.com tab. A read-only probe on the Photos tab found the item under its exact name
  (Mon 20 Dec 2021, 45:11 = stored taken_ms). RH re-runs after the fix below.
- **IMP-C29** (fetch attaches to a non-Photos Chrome target): card answer "Own hotfix PR (Recommended)" → branch
  `fix/imp_c29_fetch_attach_photos_tab` from main, own worktree `MediaVault-c29` and PR; fetch selects the
  photos.google.com page (or opens one), never a webview; then re-run the X-Files RH fetch.
- **Device HT74D0202839**: card answer "Extra movies phone" → scan BOTH `FA69H0300200` and `HT74D0202839` for the
  movies category in the device cross-check. `DEVICE_ALIASES` push semantics stay single-serial (movies →
  FA69H0300200); the extra serial is a device-check-only addition (implemented with Step 9).
- **PR #62 (IMP-C28)**: card answer "Merge now (Recommended)" → squash-merged to main as `1163d23` (Checkpoint 1
  passed). Read-only real-library check: 0 affected entries. C25 re-syncs main after Step 9 (keep `parts_dir` +
  `mvcommon.in_parts_dir` at the `cmd_push` capture call). C28 worktree removed.
- **C28 follow-ups** (`.chunk.` filename substring filters; resume from a `_parts/` holding no chunks may upload
  nothing yet mark onboarded): card answer "Investigate + fix now" → branch `fix/imp_c28_followups` from main
  (worktree `MediaVault-c28b`); real bugs register as IMP-C30 / IMP-C31; rollback-adjacent changes stop for a decision.
- **PR #63 (IMP-C30 + IMP-C31)**: card answer "Merge now (Recommended)" → squash-merged to main (Checkpoint 1).
- **IMP-C32** (resume uploads any `.mkv` in a shared `Season/_parts/`, incl. another episode's chunks → wrong entry
  onboarded, `replace` dummies its master): card answer "Strict resume (Recommended)" — explicit rollback change-gate
  ruling: a resume uploads only chunks recorded in that entry's `split_info` (names + sizes/hashes) and refuses
  anything else, listing the strangers; C31's refusal is the empty case. Hotfix branch `fix/imp_c32_strict_resume`
  from main (worktree `MediaVault-c32`), own PR.
- **PR #64 (IMP-C32 strict resume)**: card answer "Merge now (Recommended)" → squash-merged to main (Checkpoint 1).
  The C25 branch re-syncs main only after the user's running Dune archive finishes (the main folder's code must not
  change under a running archive).
- **Autopilot re-run after an interrupted split push** (C32 now refuses it): card answer "Prep keeps split record
  (Recommended)" → `cmd_prep` keeps `split_info` when the file's hash is unchanged (as the extras merge does), so the
  re-run resumes. Follow-up branch `fix/imp_c33_prep_keeps_split_info` from main (worktree `MediaVault-c33`), own PR;
  registered as IMP-C33. Not a rollback-rule change; if it turns out to touch one, stop and ask.
- **Dune Part Two archive** (user's own run 2026-10-02 18:15, `prep_push_rep_enrich mov-en-2024-duneparttwo …
  device FA75V0303405`, i.e. a movie pushed to the series phone / TV account), verbatim: "its only currently being
  split and archived. let that run in parallel , for now dont touch this. but after plan is done, can revisit all
  items to confirm ." → do not touch the entry, its folder, `D:\test`, mvconfig routing or the main folder's code
  while it runs; revisit routing/confirmation of all items after the plan is done (R3/R4).

## §1e — User rulings 2026-10-03 (decision cards, first-hand in the main session)
- **PR #65 (IMP-C29)**: "Merge now (Recommended)" → squash-merged to main `c1e254a` (Checkpoint 1). Final cause: the
  Gemini-panel attach, a harvester that gave up on downloads > 5 min, and QUIC downloads that die on an ~8 s stall
  (fix: `--disable-quic`). 🚦 RH passed (two real runs, incl. a cold start; SHA-256 = library hash).
- **Three remaining fetch issues** (no retry of a broken transfer; the home timeline can be clicked for 1–2 s after a
  search; `main.py fetch` output arrives in bursts): "Inside C25 (Recommended)" → explicit requirements of Steps 10–12,
  registered as IMP-C34 / IMP-C35 / IMP-C36.
- **Test copies of X-Files S02E05 on D:** "Delete both (Recommended)" → both deleted 2026-10-03; logs kept.
- **PR #66 (IMP-C33)**: "Merge now (Recommended)" → squash-merged to main `bfb9833` (Checkpoint 1).
- **🚦 R1 dry-run read (2026-10-03, output `D:\MediaVault_gp_inventory\map_r1_20261003_1221\`)** — user's answer to
  "does the report look right / how to settle the 15 device-check-incomplete rows", verbatim: "why do you need the
  phone for this? you only focus on the library, and the google , others new ones at last of json , will mostly be
  just added , so not yet in google photos. you dont need to check that." → **Step 9b**: map_photos verdicts are
  library ↔ Google Photos only; the device check becomes opt-in (`--devices`), never a precondition for a verdict.
  An object in no account is reported as "not in Google Photos", split by recency: pushed recently (after, or shortly
  before, the inventory crawl — the newly added entries) → "not backed up yet (recent)"; otherwise → "not in Google
  Photos (possible loss)". No phone needed. Step 8 code stays (used only with `--devices` and by later steps).
- **Fetch closes Chrome and its tabs at the end (2026-10-04)** — user, verbatim: "ALSO, MAKE SURE the fetch also closes
  the chrome and tabs properly before finishing the task. which makes it easier for us to re open in next fetch for
  proper task." → Step 11 item i: after downloads finish, close this run's tabs and the Chrome the run launched
  (graceful, then terminate its recorded pid); a Chrome the run only attached to keeps running with only this run's
  tabs closed (orchestrator's safe default — it may hold the user's own tabs).
- **Early live check of Step 11's download redirect (2026-10-05)**: card answer "Yes, run it now (Recommended)" → the
  orchestrator fetches `tv-en-2014-siliconvalley-s02e02` (5.6 GB, one file) into `D:\MV_fetch`, checks .mv_incoming →
  restore + hash, Chrome closed at the end, and that the post-fetch identity merge touches only that entry's `gphotos`
  block; then deletes the test copy.
- **warm_profiles leaves each Chrome on port 9222** (the next profile attaches to the previous one): card answer "Fix
  inside C25 (Recommended)" → added to Step 14 with a test; registered as an IMP.
- **Agents and candidates must finish — no half results (2026-10-05)** — user, verbatim: "make sure all candidates and
  agents finish. dont tAKe half ones" and "make sure all candidates or agents are properly resumable in limit crashes.
  so that we dont go to judge by half results or considering it as done. make sure all candidates are completely done
  before proceeding." → **Step 12 completion gate** (also appended to each candidate's brief): CANDIDATE_STATUS.md line 1
  `STATE: COMPLETE`, every sub-task `[x]` with its WIP SHA, one test per PLAN scenario, full + smoke + disarm counts
  recorded, CRITIQUE.md committed, tree clean, final message `CANDIDATE <X> COMPLETE — <SHA>`. **Before judge-v2 runs,
  the orchestrator verifies the gate independently for each candidate** (reads the status file, `git status`, and
  re-runs full + smoke in each worktree). Any gap → resume that same candidate; never judge, merge or relay partial
  work. The same "done = its final report + green gates" rule applies to every other agent.
- **Live wrong-pick diagnosis closed (2026-10-07, `74d662d` + `8dacbad`, journal `d714613`)**: cause = Google Photos'
  new "Most relevant to your search" strip above the real results; not a Step 10 regression. Follow-ups recorded for
  Step 14/15 (no ruling needed — robustness of existing fetch paths): `driver.get` inherits Selenium's 300 s page-load
  default (a probe hung ~5 min) → bounded page-load timeout; a failed attach in `init_driver` (e.g. chromedriver-manager
  online check) can leave the just-launched Chrome running → close it on that path too (extends Step 11 item i).
  Step 12 candidates must merge these two fixes before their completion gate.
- **No "Restore pages?" bubble in the automation Chrome (2026-10-08)** — user, verbatim: "also, make sure to close the
  restore small box which opens in chrome. in right top." → Chrome 154 ignores `--disable-session-crashed-bubble`, and
  (seen live by the orchestrator, 2026-10-08) leaves `profile.exit_type "Crashed"` (no `exited_cleanly` key) in
  `Default\Preferences` even after a graceful CDP `Browser.close`. Fix (`0d47140`, tests `49367a1`, `d444f12`, this
  commit): every launch also passes `--hide-crash-restore-bubble`; `mainfetch.mark_profile_clean_exit` sets
  `exit_type "Normal"` + `exited_cleanly true` only when an unclean exit is recorded and no Chrome browser runs on that
  user-data-dir (process table; cannot tell → left alone), atomically (temp file + `os.replace`), every other key kept,
  never raising; its one line is plain cp1252 text (the crawler's output is often a cp1252 pipe). It runs
  before EVERY launch (`init_driver` = fetch + warm-up; the crawler's `launch_chrome`) and after EVERY close once that
  Chrome is gone (graceful, terminated, an attached Chrome's windows at an account switch; `_end_run` interrupted;
  `_startup_cleanup`; the crawler's `_session`). `warm_profiles` never closes its Chrome (attach-mode `quit()` — the
  Step 14 fix above), so its profiles are marked at their next launch. Live read-only check 2026-10-11: movies, tv and
  anime read `exit_type "Crashed"` (no `exited_cleanly`); others has no profile folder yet — each is fixed at its next
  launch.

## §1f — 🚦 G12 (Step 12 locator) — user rulings 2026-10-12 (decision cards)
- **Winner**: card answer "C — hybrid (Recommended)" (judge-v2 DECISION.md `.candidates/c25-step-12/DECISION.md`,
  02ac913: C was the only candidate with zero wrong downloads in the judge's RPC-down / page-format-change / bad-RPC-facts
  simulations; 15/15 live §M4 objects in 2–11 s). A (d8dd71f) and B (868cd27) rejected; all three were complete and
  gate-verified (orchestrator re-runs: A 2493/82 · B 2512/82 · C 2502/82).
- **Cherry-picks**: card answer "Apply all 10 (Recommended)" → the judge's list (DECISION.md "Cherry-picks worth taking
  into C", items 1–10: per_approach contract `{outcome, n}`, one failed-list row with stage evidence, `inconsistent`
  reason, start confirmation ignoring other downloads, leave an HTML link page at once, try another verified copy before
  S4, reuse the open item page for Shift+D, allowlist check after every locator test, no 20-candidate cap with
  dedupKeys, `locate` early-stop `last=True`) applied as one follow-up after the merge, before Step 13.
- **Step 13b executor choices (2026-10-14), kept as orchestrator defaults — the user may overrule:** (1) a push-family
  command that returns None (push_group, prep_push_rep, prep_push_rep_season return None on success AND failure) counts
  as "succeeded" for auto-confirm only when the run reached its end state (entry archived / every target archived /
  every target uploaded) — stricter than the plan's "did not return False", so no Chrome after a paused or hard-failed
  autopilot; (2) `AUTO_CONFIRM_MIN_AGE_H = 24` (plan said 12 with a note to raise it): measured on the R1b mapping,
  41 % of uploads were backed up within 12 h, 84 % within 24 h, 99 % within 48 h.
- **Step 13c executor choices (2026-10-14), kept as orchestrator defaults — the user may overrule:** (D3) the check-only
  movies phone `HT74D0202839` (§1d) is rewritten by `mvmeta_upgrade --apply` only when named explicitly with
  `device movies#2` (one-line change if the user wants it included by default); (D1) scope = every uploaded entry or
  extras item that has a sidecar (whole files too), per OD-4 "you can change all existing library also"; (D4) every
  original sidecar is saved under `~/.mediavault/gphotos/mvmeta/<ts>/` before the phone is touched.
- **Step 14 executor choices (2026-10-19), kept as orchestrator defaults — the user may overrule:** (10) `check_photos`'
  quality pass reads partial-upload / archived from the timeline record (the only source the live probe showed carries
  them; the swbisb hash record is compact) — at most one extra paced call per checked object (~0.6–1 s on a long
  sweep), kept because it makes the `inconsistent` verdict and the partial-upload outlier work; original-quality stays
  `unknown` until a storage-saver item is compared. (2) a `--manual` download records proof `sha256` / source `manual`
  (unconfirmed until a check). (4) resolve_photos refuses a pasted item proven to hold other bytes.

## §1g — 🚦 R2 (live verification pack) — user rulings 2026-10-20 (decision cards)
- **Who runs what**: card answer "You run yours, I do mine (Recommended)" → the orchestrator runs M4 (locate probes),
  M5 (season check), M6 (direct fetch of SV S02E02), M7 (chunk set + interrupt), M8 (X-Files wrong-account fetch),
  M9 (fetch_restore + web) and M18 (fetch_cleanup dry run), one at a time, into `D:\MV_fetch`, deleting the test copies
  afterwards; the user runs M10 (--manual), M11 (new small archive push), M14 (auto-confirm a day later) and M15
  (resolve_photos) with the commands the orchestrator gives.
- **M7 size**: card answer "Smallest split item (Recommended)" → the smallest archived split entry with ≥2 chunks.
- **🚦 R3 (2026-10-21)**: card answer "Prepare dry runs now (Recommended)" → the orchestrator backs up the library
  files to `D:\MV_lib_backup\<date>\`, re-crawls the inventories, runs `map_photos all` and `check_photos account:<a>
  --approaches best` as dry runs and gives the user the reports; nothing is written until the user approves each
  `--apply` / `--apply-run`. No other MediaVault command may run meanwhile (OD-2).
