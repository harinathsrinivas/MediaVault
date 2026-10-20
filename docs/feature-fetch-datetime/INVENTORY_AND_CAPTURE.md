# IMP-C25 interim tools — Google Photos inventory + identity capture

Two pieces that run **before** the IMP-C25 plan is implemented, so that no identity data is lost
while the plan is being built (user request, 2026-09-24/25). Both are read-only towards Google
Photos and neither changes any existing command's behaviour.

**Last updated:** 2026-10-20

> **Status (2026-10-20, IMP-C25 Steps 2–15 done):** both interim pieces are now productized parts of
> IMP-C25 and no longer "interim". The crawler gained an **rpc mode** (the default — read-only internal
> calls, minutes instead of hours), a run log with a completeness proof, a lock + heartbeat, a profile
> pre-flight and `--status` (Step 7; `ARCHITECTURE.md` §9.3). The capture is **v2** (device timezone,
> width / height, duration, `approach_hint`) and covers extras scans and extras pushes too (Step 5;
> `ARCHITECTURE.md` §6.5). Its readers exist: `check_photos pending`, the fetch's locator, `map_photos`
> and the apply engine (Steps 6–13). Section 3's plan has been implemented as `map_photos` /
> `check_photos` / `resolve_photos` (`ARCHITECTURE.md` §8.10; how to run it: `docs/OPERATIONS_QA.md`
> §6d). The sections below keep the original interim description, with the changes marked.

---

## 1. Inventory crawler — `tools/gp_inventory.py`

Opens every item of one Google account's Photos library, one by one, and records what the web UI
exposes. Research basis: `RESEARCH.md` F12, F20–F23.

| Field | Source | Meaning |
|---|---|---|
| `photo_id` | item URL `/photo/<id>` | stable Google Photos item id (direct-open key) |
| `filename` | info panel | exact uploaded name, e.g. `… [92396f].mkv` / `… [9a0355].chunk.002.mkv` |
| `date_text` / `time_text` / `tz_text` | info panel | as displayed (year omitted for current-year items) |
| `taken_ms` | item page record | = the MKV `DateUTC` (chunks of one split share it) |
| `dedup_key` | item page record | urlsafe-base64 **SHA-1 of the exact uploaded bytes** |
| `tz_offset_ms` | item page record | e.g. 28800000 = GMT+08:00 |
| `upload_ms` | item page record | when the Pixel's backup reached Google Photos |
| `width` / `height` | item page record | pixel dimensions |
| `tile_label` | timeline tile | `Video - Landscape - Feb 5, 2022, 7:49:46 PM` (seconds) |

**Output** (outside the repo — it holds private item ids): `D:\MediaVault_gp_inventory\`
`<account>.tiles.jsonl` (phase 1: every id), `<account>.items.jsonl` (phase 2: one line per item,
fsync'd), `<account>.log`.

**Resumable by construction.** Every item is appended the moment it is read; a crash, Ctrl+C, reboot or
a Claude session limit loses nothing. Re-running skips finished items and retries errored ones.

```
python tools/gp_inventory.py tv --port 9225          # launch the TV profile on its own debug port
python tools/gp_inventory.py movies --port 9223
python tools/gp_inventory.py anime --port 9224       # needs the anime profile signed in
python tools/gp_inventory.py tv --attach 9222        # or attach to an already-open Chrome
python tools/gp_inventory.py tv --port 9225 --phase details --max 3   # smoke
python tools/gp_inventory.py all --status            # (Step 7) offline counts: no Chrome, writes nothing
python tools/gp_inventory.py tv --port 9225 --mode dom   # (Step 7) the original browser crawl
```
Do not run a MediaVault `fetch` on the same account while it runs (same Chrome profile). Since Step 7
the crawler takes the fetch session lock without waiting (refused, exit 3, while a fetch / warm-up /
another crawl holds it — so crawl the accounts one after another), and writes `<account>.runs.jsonl`
(the run log whose `enumerated` line proves a complete listing — `map_photos` needs one ≤ 24 h old).

**Run log.** 2026-09-25 00:06 — tv (1,109 ids) and movies (795 ids) crawling as detached processes;
anime STOPPED: `ChromeProfile_Anime` is signed out (user to sign in, then re-run); others: no
`C:\Media\Utils\ChromeProfile_Others` exists (user to create + sign in). Progress: `tail <account>.log`.

## 2. Identity capture — `gpcapture.py` (+ SHA-1 side-channel in `mvcommon.calculate_file_hash`)

After a **successful** `prep` / `push` (post-commit, never journalled, never raises) MediaVault writes
`<folder>/<short_id>.gpcapture.json` beside the `uid` and `<short_id>.sha256` sidecars:

```json
{ "capture_version": 1, "manual_id": "…", "short_id": "…", "note": "…", "updated_at": "…Z",
  "prep":   { "captured_at": "…Z", "uploaded_name": "<name> [<short_id>].mkv",
              "source": { "local_name", "size_bytes", "mtime_utc", "sha256", "sha1", "dedup_key", "date_utc" },
              "tech": { "duration_mins", "width_height", "resolution", "size_bytes" } },
  "pushes": [ { "captured_at", "device_id", "remote_dir", "chunk_range", "complete",
                "objects": [ { "role": "whole|chunk|holder", "index", "uploaded_name",
                               "size_bytes", "sha256", "sha1", "dedup_key", "date_utc", … } ] } ] }
```

- `sha1` comes from the **same read pass** as the existing SHA-256 (worker thread; measured cost within
  noise: 55/51 vs 53/49 MB/s). It is present when the file was hashed in the same process — always for
  new split chunks and holders; for an unsplit file when prep and push ran in one process (the
  `prep_push_rep*` autopilots) or from the prep record otherwise.
- `date_utc` is read from the Matroska header in pure Python (validated against mkvmerge/ffprobe on
  every probe file). `dedup_key` is what Google Photos will show for that exact upload.
- Covered: `cmd_prep`, `cmd_push` (whole, chunks, FLAC holder, partial `chunk_range` pushes) and every
  autopilot built on them. ~~Not yet covered: extras pushes~~ — **covered since Step 5** (the extras scan
  and `push_one_extra`, at `<title>/<group_rel>/<dir of sub_rel>/<item short_id>.gpcapture.json`).
- ~~No MediaVault command reads the capture yet~~ — since Steps 6–13 `mvcommon.read_capture` feeds
  `check_photos pending`, the fetch's locator (content key, taken second), `map_photos` and the apply
  engine. Capture v2 (`capture_version: 2`) adds per push `device_tz` / `device_tz_offset_min` and per
  object `width` / `height` / `duration_s` / `approach_hint`; v1 files are still read.

## 3. What happens next (IMP-C25 plan) — implemented (Steps 9–14)

Done as planned, with one change from the user's R1 ruling (2026-10-03, Step 9b): the "missing" verdict
needs no phone — an object found in no account is `not-backed-up-yet` when pushed within 7 days before
(or after) its account's crawl, else `possible-loss`; phones are an opt-in (`--devices`). The real
library's map / check apply (🚦 R3) and the full sweep (🚦 R4): results pending R2/R3.

1. Finish the crawl for every account (anime/others need the profiles signed in).
2. **Map** each library object (whole file / chunk / holder / extra) to its inventory item by exact
   uploaded filename; cross-check `dedup_key` against a capture's `sha1` where one exists.
3. Report mapped / ambiguous / missing; the user finds the missing ones by hand and supplies date/time.
4. `--apply` (after a dry-run the user reads) writes photo_id / taken time / dedupKey / approach term into
   the library nodes; fetch then uses the exact-item flow for everything.
