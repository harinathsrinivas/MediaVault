# Fetch exact Google Photos items (timestamp locator + SHA-1 content key) — research log (pre-plan)

**Status:** RESEARCH — no code, no branch, nothing committed. Findings are complete enough to plan; still
pending: C1c (Daredevil, after its backup finishes) and the user decisions in §6.
**Started:** 2026-09-24 · main session Opus 5.5 (`/effort max`) · Fable probe `FABLE_PROBE_OK claude-fable-5-1` (AVAILABLE)
**Framework for the plan:** v2 (planner-v2 → orchestrator-v2 playbook), per `.claude/MODEL_WATERFALL.md`
**Probe artifacts + scripts (outside the repo, read-only, safe to delete):** `D:\MediaVault_date_probe\` (§7)

> ▶ **NEXT ACTION (2026-09-25):** §6 decisions are resolved (all recommendations accepted). Branch
> `feature/imp_c25_fetch_exact_gp_item` exists (from main @ 6eaef93). Interim tools are built — see
> `INVENTORY_AND_CAPTURE.md` (identity capture at prep/push + the Google Photos inventory crawl, running).
> Resume planner-v2 (it was interrupted twice by account session limits before writing PLAN.md) with the
> expanded scope; then map library ↔ inventory. Pending user actions: sign in the anime Chrome profile;
> create + sign in `C:\Media\Utils\ChromeProfile_Others`; C1c (Daredevil) after its backup.

---

## 1. The ask (restated)

Every uploaded Google Photos item (whole file, split chunk, FLAC holder, extra) carries a date/time in
Google Photos. Record it in the library node when we push, and use it at fetch time to select exactly the
right item — instead of the filename/search_term search that gets ambiguous as the library grows. Today's
search flow stays as a fallback waterfall; the SHA-256 hash stays the final identity check. Already-archived
items need a backfill path. No existing behavior may regress.

## 2. Verified facts (2026-09-24)

### Where the Google Photos date comes from

| # | Fact | Evidence |
|---|---|---|
| F1 | **Google Photos' date for an MKV = the MKV `DateUTC`** (segment-info muxing date) — not the file mtime, not the upload time. **CONFIRMED.** | S02E03 `DateUTC 2022-02-05T11:49:46Z`; Google Photos info panel: **Feb 5, 2022 · Sat, 7:49 PM · GMT+08:00** (user screenshot); device mtime 2026-09-22, upload 2026-09-22 play no part |
| F2 | Android MediaStore `datetaken` is NULL for every MKV on the Pixel (Android 10) — Google Photos reads the container itself. | `content query` on FA75V0303405 |
| F3 | `adb push` copies the PC file's mtime to the second onto the device; the `.partial`→final `mv` keeps it. | S02E03 `Modify 22:28:49.000000000` vs `Change 22:57:20.13` |
| F4 | `mkvmerge --split` (the `split_video_file` argv shape) stamps **all chunks of one split with the same DateUTC** = split start. | local 3-chunk test: all `06:03:02Z`; Google Photos: Chernobyl S01E02 `[ddaef4]` chunks 001–003 all `Jan 31, 2026, 12:29:27 AM` |
| F5 | `mkvmerge -J` exposes `container.properties.date_utc` — MediaVault already runs `mkvmerge -J` (`find_unsplittable_tracks`, `probe_track_manifest`). | `mkvmerge -J` output |
| F6 | Dummies are fresh ffmpeg files → an archived title's original DateUTC is gone from `C:\Media`. | `make_video_dummy` |

### Uniqueness — the timestamp alone is NOT an identity

| # | Fact | Evidence |
|---|---|---|
| F13 | **A release batch shares one second — here, every season.** Day search `February 5, 2022` = 38 videos = Silicon Valley S01–S04: **S01 8/8 at 7:49:29 PM, S02 10/10 at 7:49:46 PM, S03 10/10 at 7:52:19 PM, S04 10/10 at 7:52:28 PM** (S02E02 = S02E03). | probe run 2 (all 38 opened, filenames read) |
| F16 | **Short id alone is inconsistent:** `92396f` → 1 (correct), `ddaef4` → 3 (exactly its chunks), `0003a8` → **133**, first hits unrelated (The Expanse, Battlestar Galactica). | probe run 2 |
| F18 | **Text search is phrasing-sensitive:** `0003a8 February 5, 2022` → **0**, while `0003a8 silicon february 5 2022` → 1 (correct). An exact chunk filename → 1 (correct). | probe run 2 + user |

### What the Google Photos web UI exposes

| # | Fact | Evidence |
|---|---|---|
| F11 | **Day search works in any format** — `February 5, 2022`, `Feb 5 2022`, `5 Feb 2022`, `2022-02-05` all → the same 38. Adding a time (`… 7:49 PM`) or `Silicon Valley` changes nothing (time-of-day is not a search filter). | probe run 2 + user |
| F22 | **Day search uses the item's LOCAL date** (its timezone), not UTC: the Chernobyl chunks (UTC Jan 30 16:29:27 = local Jan 31 00:29:27 +08:00) come back for `January 31, 2026`, not `January 30, 2026`. | `gp_dayprobe.py` |
| F12 | **Result tiles carry the local time to the SECOND** in `aria-label`: `Video - Landscape - Feb 5, 2022, 7:49:46␯PM` (`␯` = U+202F before AM/PM). | `gp_diag2.py` |
| F1b / F23 | **Info panel:** date, weekday + time **to the minute**, `GMT+08:00`, the **exact uploaded filename**, resolution, "Backed up · Original quality". No file size. **The year is omitted for current-year items** (`Jan 31` / `Sat, 12:29 AM`). | user screenshot; `gp_tzprobe.py` |
| F20 | **The item page embeds a machine-readable record:** `[<photo_id>, [<thumb url>, width, height, …], <taken_ms>, "<dedupKey>", <tz_offset_ms>, <upload_ms>, …]` — e.g. S02E03: `1644061786000` (= DateUTC exactly), `"hd9mASyG9pJhisPXFS3Zr7Mqr28"`, `28800000` (+8 h), `1790104139274` (backed up ~4 h after the push). | `gp_tzprobe.py` (URLs/ids redacted) |
| F21 | **`dedupKey` = URL-safe base64 of the SHA-1 of the exact uploaded bytes. CONFIRMED.** S02E03 SHA-1 computed on the Pixel = `85df66012c86f692618ac3d7152dd9afb32aaf6f` → `hd9mASyG9pJhisPXFS3Zr7Mqr28` = the page's dedupKey. A content-proof of identity, readable **before** downloading. | `adb shell sha1sum` vs page data |
| F14 | **mainfetch's primary tile selector is dead on search pages:** search tiles link as `./search/<token>/photo/<id>`, so `a[href*='./photo/']` matches only the hidden timeline behind the results (filtered out by `is_displayed()`); fetch works today only via the `background-image` div fallback. A **background tab never renders results** — automation needs the tab in front (`Page.bringToFront` + focus emulation). | probe v1 (0 everywhere) vs `gp_diag*.py` |
| F15 / F24 | `mkvpropedit <f> --edit info --set date=<ISO>` rewrites DateUTC in place: size unchanged, SHA-256 changes, **322 bytes differ, all within offsets 56–4288** (header only). | `propedit_test_*` |

### Legacy search reliability + account readiness (2026-09-24/25)

| # | Fact | Evidence |
|---|---|---|
| F25 | **Legacy text search is unreliable** (TV sample, 15 objects, read-only). Full `search_term`: exactly-one 6/11 whole files, present-but-not-first 2 (The Wire @2, Mr. Robot @1), not in the first 4 results 3 (Fringe, Devs, Dark with 124+ results). Plain filename (= today's attempt 1): exactly-one 3, **zero results 4** (X-Files, The Office, The Expanse…). Title + short id: 1/11. Exact **chunk** filename: 3/3 exactly-one. Hence: map by exact filename from a full inventory, not by search. | `D:\MediaVault_date_probe\gp_sample_191302.json` |
| F26 | Accounts: TV 1,109 items vs ~1,126 expected objects (≈13 recent pushes not yet backed up); movies 795 items vs ~717 expected (extra items to explain in mapping); **anime profile signed out** (lands on the Google Photos "about" page — a fetch would fail too); **no `ChromeProfile_Others` exists** although `mainfetch.CHROME_PROFILES` points at it. Movies are mostly split (128 of 176, up to 15 chunks). | `tools/gp_inventory.py` runs; library scan |

### Library / device state

| # | Fact | Evidence |
|---|---|---|
| F7 | No entry has any timestamp/SHA-1 field (only `reconstructed_at` ×27). Scope: 1,295 leaves (1,281 archived), 259 split entries / **963 chunks**, 1 FLAC holder, 23 extras items → ~2,000 Google Photos items. 1,291 `.mkv`, 3 `.mp4`, 1 `.mkv.ts` (split). | scan of the 4 library JSONs |
| F8 | FA75V0303405 holds only 7 videos now: S02E03 + Daredevil S01E01–06 (pushed 2026-09-24 12:06–12:13, backup not finished). S02E02's `.mkv` is gone (sidecar only). | `ls` / MediaStore |

**F9 — Daredevil S01 (for C1c once backed up):** E01 `[4a9028]` 2024-05-03 23:18:02Z (= **May 4** 07:18:02 local) ·
E02 `[254a3e]` 05-04 06:48:48Z · E03 `[0ec44f]` 07:00:40Z · E04 `[43de53]` 08:09:41Z · E05 `[d0b52b]` 08:15:39Z ·
E06 `[dd24dd]` 08:25:00Z. Device mtimes 2026-05-12 (must NOT appear in Google Photos).

**F10 — what fetch does today (`mainfetch.py`):** attempt 1 queries `entry["filename"]` (unsplit: the local name
*without* the ` [short_id]` the upload carries) and clicks tile 0; attempt 2 queries `search_term` and clicks tile `i`
for chunk `i` (blind order assumption). One download per query; the harvester SHA-256s every new `.mkv/.mp4` in
`~/Downloads`; a wrong download is **left** there. Only entry point: `mainfetch.py` via `cmd_dispatch_fetch`
(CLI `fetch`/`fetch_restore` and the web UI). `ARCHITECTURE.md` §8.3 drifted (claims attempt 1 uses the tagged name).

## 3. Design direction (evidence-backed inputs for planner-v2 — not locked decisions)

1. **Identity = SHA-1 content key (F21).** Compute SHA-1 in the same read pass as the existing SHA-256 for every
   uploaded object — whole file at prep, each chunk and the FLAC holder at push, extras likewise — and store it next
   to the existing `hash`. The expected dedupKey is `urlsafe_b64(sha1).rstrip("=")`.
2. **Locator = taken instant + item timezone (F1, F4, F22).** Store the UTC instant (DateUTC — the source's for an
   unsplit file, the split run's for chunks, the holder's own) and the Pixel's timezone at push. Fetch searches the
   item's **local day**, keeps tiles whose `aria-label` second matches (F12), opens only those, and downloads the one
   whose page `dedupKey` equals the stored SHA-1 — proven before any bytes move. SHA-256 stays the final gate.
3. **Remember the item (F20).** After the first successful locate, store the Google Photos `photo_id` (+ the confirmed
   taken/upload times from the page record). Next fetch = direct `/photo/<id>` → dedupKey check → download. Aligns
   with IMP-G2/S7 (gphotosdl addresses items by photo id).
4. **Legacy items (~2,000, no SHA-1 / date stored):** locate with today's queries but **verify before downloading**
   (exact filename from the info panel), then after the SHA-256-verified download record SHA-1 + photo_id + taken time
   (learn-on-fetch). Optional: a resumable, read-only **index** command that learns photo_id / dedupKey / taken time
   for archived items without downloading (overnight, per account).
5. **No file modification is needed.** Season batches sharing a second (F13) are separated by the content key. The
   user's idea — rewrite DateUTC before push to make times unique — is feasible (header-only, F24) but costs byte
   identity with the release, a new in-place write on the only copy during prep (rollback change-gate), and helps
   only new archivals. Recorded as an alternative in §6 D1.
6. **Timezone/parsing rules:** day = local date in the stored timezone; tile labels and the info panel show local
   time; current-year items omit the year (F23); U+202F before AM/PM (F12).
7. **Selection-step fixes that come with it:** F14 (dead primary selector, foreground tab), F10 (blind index, wrong
   downloads left in `~/Downloads`), F16/F18 (text queries are not identities).
8. **Guardrails:** new shared fields → `ENTRY_TYPE_KEYS` + `tests/test_entry_schema_guard.py`; writes into the
   journalled `split_info` during `cmd_push` → check against the rollback change-gate and surface to the user if it
   alters recorded state; smoke gate; SHA-256 remains the final authority; alias/season_map-safe iteration.
9. **Synergies:** IMP-E5 (phone cleanup) can confirm "backed up" by content key — the strongest possible check;
   IMP-C5 is absorbed; IMP-B10/B4 are adjacent harvester/wait fixes; IMP-A8 dead code lives in the same file.
10. **No-DateUTC files** (3 MP4s; MKVs muxed without a date): the predicted day is unknown, but the content key still
    identifies them → locate by text search + dedupKey. C7 is optional.

## 4. Checks — results

Use the **TV Google account** (`C:\Media\Utils\ChromeProfile_TV`).

| # | Check | Result |
|---|---|---|
| C1 | S02E03 info panel | ✅ Feb 5, 2022 · Sat, 7:49 PM · GMT+08:00; exact filename; no size |
| C1b | S02E02 info panel | ✅ same second as S02E03 (7:49:46 PM) — the whole S02 shares it (F13) |
| C1c | Daredevil S01E01 `[4a9028]` after its backup | ⏳ expect **May 4, 2024 · 7:18 AM · GMT+08:00** (not May 12, 2026 / Sep 24, 2026) |
| C2 | Day-search formats | ✅ all four formats → same 38; time-of-day ignored (F11) |
| C2b | Local vs UTC day | ✅ local day (F22, via the Chernobyl chunks) |
| C3 | `Silicon Valley February 5 2022` | ✅ no narrowing; `0003a8 silicon february 5 2022` → exactly 1 |
| C4 | Short id alone | ✅ inconsistent: 1 / 3 / 133 (F16) |
| C5 | Seconds in tile `aria-label` | ✅ yes (F12) |
| C6 | Filename / size in info panel | ✅ filename yes, size no (F1b) |
| C7 | *(optional)* no-DateUTC upload test | not needed for identity (§3.10); only for predicting the day |
| C8 | *(next archival)* dates before prep / after push / after backup | ⏳ plan's manual verification can cover it |

## 5. Side findings (not part of this feature)

- **Fable→Opus waterfall already exists** (commit `71871a9`, 2026-09-07). Nuance: an Opus fallback of
  `executor-fable` / `judge-v2` inherits their baked `effort: xhigh`, not `max` (§6 D5).
- **U6 bracket check:** library ↔ disk agree — all 1,331 library `folder_path`s use `{tmdb-…}` and exist. 17 **empty,
  untracked** movie placeholder folders under `C:\Media\Movies` still carry `[tmdbid-…]` (created 2026-06-20 and
  2026-09-12/13). Device folders `Dark (2017) [tvdbid-334824]` / `Fringe (2008) [tvdbid-82066]` keep push-time names.
- **C: has 7.9 GB free (99 % full).** A fetch/restore of anything larger needs `tempdir` on D: (218 GB free).

## 6. Decisions (resolved 2026-09-24 — user: "ok go ahead with all these")

All six recommendations below were accepted; D5 landed as PR #59. Later additions from the user
(2026-09-24/25): a per-node approach term; check mode by series/season/item; download hygiene across
failures; `--manual` mode; all four categories + chunks; announce manual steps first; a full Google
Photos inventory crawl mapped to the library by filename; interim capture at prep/push (done).

| # | Decision | Recommendation |
|---|---|---|
| D1 | Rewrite DateUTC before push to make times unique? | **No** — the SHA-1 content key already separates siblings; keep originals byte-exact |
| D2 | Adopt the SHA-1 content key (computed at prep/push) as the fetch identity? | **Yes** |
| D3 | Store `photo_id` after the first locate (direct URL next time)? | **Yes** |
| D4 | Legacy backfill: learn-on-fetch only, or also a read-only overnight index command? | both; index as its own step |
| D5 | Raise `executor-fable` / `judge-v2` to `effort: max` so an Opus fallback runs at max? | user's call |
| D6 | Is the `feature/imp_u6_token_brackets` paragraph meant for this task? | appears carried over from the U6 prompt |

## 7. Probe scripts (all read-only; `D:\MediaVault_date_probe\`)

`gp_probe.py` (searches + info panels, attaches to the open Chrome on :9222, own tab, never downloads) ·
`gp_diag.py` / `gp_diag2.py` (why v1 saw nothing: background tab / hidden timeline) · `gp_tzprobe.py` (page record
around the known epoch; URLs + ids redacted) · `gp_dayprobe.py` (local vs UTC day) · `analyze_probe.py` (summary of
`gp_probe_*.json`). Header pulls: `s02e03_head_8MB.mkv`, `dd_head_1..6.mkv`.
