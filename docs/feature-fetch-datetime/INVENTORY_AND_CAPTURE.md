# IMP-C25 interim tools — Google Photos inventory + identity capture

Two pieces that run **before** the IMP-C25 plan is implemented, so that no identity data is lost
while the plan is being built (user request, 2026-09-24/25). Both are read-only towards Google
Photos and neither changes any existing command's behaviour.

**Last updated:** 2026-09-25

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
```
Do not run a MediaVault `fetch` on the same account while it runs (same Chrome profile).

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
  autopilot built on them. **Not yet covered:** extras pushes (`push_one_extra`) — the inventory covers
  them by filename.
- No MediaVault command reads the capture yet; the IMP-C25 sweep will.

## 3. What happens next (IMP-C25 plan)

1. Finish the crawl for every account (anime/others need the profiles signed in).
2. **Map** each library object (whole file / chunk / holder / extra) to its inventory item by exact
   uploaded filename; cross-check `dedup_key` against a capture's `sha1` where one exists.
3. Report mapped / ambiguous / missing; the user finds the missing ones by hand and supplies date/time.
4. `--apply` (after a dry-run the user reads) writes photo_id / taken time / dedupKey / approach term into
   the library nodes; fetch then uses the exact-item flow for everything.
