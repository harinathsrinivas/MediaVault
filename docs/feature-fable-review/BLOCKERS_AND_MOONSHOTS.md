# Blockers & Moonshots — the honest register

**Written 2026-06-12 (fable-review session).** The user asked for two explicit lists: things that are
**hard blockers / can-never-be-done** (so alternatives can be hunted later), and **1%-possible ideas**
worth tracking even if nobody would bet on them today. Statuses here should be re-checked yearly or
on any Google/Jellyfin platform announcement.

---

## A. Hard blockers (under current platform policy — revisit on policy change)

### A1. Official-API access to the vault — BLOCKED, permanently under current policy
Since 2025-03-31 the Google Photos Library API serves only content the calling app itself uploaded;
the Picker API requires manual per-item user selection. MediaVault's bytes are uploaded by the
Pixel's Photos app → **no official programmatic read path exists**, killing: API-based fetch
(ARCHITECTURE §17.10 — formally dead), rclone-native restore, and any headless-server fetch without
a browser session. *Alternative locked in:* browser-session automation (mainfetch today; gphotosdl/
CDP hardening via IMP-G2/S7). *Watch for:* any future Google API reopening (treat as moonshot D2).

### A2. Direct TV → cloud streaming (no Alienware in the path) — BLOCKED
Consequence of A1 plus: web-player streams are transcodes (not your original bytes), and no TV client
can drive the Photos web session. The Alienware stays in the loop by design. *Alternative:* none
needed — the always-on PC is an accepted constraint of the end goal.

### A3. Plex as the integration platform — BLOCKED (by Plex)
No server-side plugin API, no virtual-item surface, webhooks outbound-only. The request/notify/
archive flow cannot be built on it at any price (and the price is now $749). *Alternative locked in:*
Jellyfin (chosen), Emby (fallback).

### A4. Apple TV TrueHD/DTS bitstream — BLOCKED (Apple hardware/OS)
tvOS never bitstreams TrueHD/DTS-HD regardless of app; DV Profile 7 FEL likewise out of reach.
*Alternative locked in:* the Ugoos AM6B+/CoreELEC path IS the reference-quality output (IMP-U4);
Apple TV remains the convenience screen.

### A5. Raw non-video artifacts in Google Photos — BLOCKED pending a wrapping spike
The "container constraint" (Tier F header): encrypted blobs (F1), CDC chunks (F2), parity shards (F3)
are not valid videos; the Pixel Photos uploader will likely ignore or reject them — and even accepted
uploads must survive byte-exact round-trips to be useful. *Alternatives:* the wrapping spike (payload
inside a tiny valid MKV — moonshot M4), plain second-account replication (F3-lite, available NOW),
or real object storage via F9.

## B. Soft blockers (friction, not walls)

- **B1. DisplayMessage client coverage unknown** for Swiftfin/Infuse/Kodi until measured (IMP-S1).
  Universal fallback exists (action stubs + collections), so this only shapes UX, never blocks it.
- **B2. `.strm` client quirks** (seek/range proxying gaps, Infuse #12306) — keep `.strm` out of the
  core design (placeholder-upgrade chosen instead); matters only for the T3 spike.
- **B3. Effort-per-Task-invocation upstream gap** (agent pipeline, IMP-H1's "hybrid advisory") —
  development-process friction only.
- **B4. Selenium fetch requires an interactive-ish logged-in session** — constrains how the daemon
  service runs (as the logged-in user, not LocalSystem); solvable, documented in IMP-S2.
- **B5. The 4-Pixel topology question** — blocks only lane-count decisions (E7/S5 parallelism, F3
  shard math). One user answer dissolves it.

## C. Moonshots — tracked because they're ≥1% possible

| # | Idea | Today's odds | What would change the odds |
|---|---|---|---|
| M1 | **T3 proxy-streaming** (play originals through a local gphotosdl-style proxy as they download; IMP-S8) | Plausible spike, fragile product | Range-request behavior on GP download streams proving seek-able; gphotosdl maturing further |
| M2 | **T2 watch-while-fetching as a first-class mode** (IMP-S6 productized: growing-file or seamless part-handoff) | Good — parts ARE playable; client behavior is the unknown | One client (likely Kodi on the Ugoos) handling playlist-append seamlessly |
| M3 | **True instant-start via tiny "primer" chunks** (F4: split emits a 1-2 GB first chunk by design) | Decent, but change-gated (split invariants) | S6 verdict showing chunk-1 latency is the dominant wait |
| M4 | **Arbitrary-bytes-in-valid-video wrapping** (defeats A5; unlocks F1/F2/F3 on Photos) | Genuine research project; transcoding-on-download is the killer risk | A spike proving byte-exact round-trip of MKV-attachment payloads through Pixel upload + Shift+D download |
| M5 | **Library-from-remote cold-boot** (rebuild all three JSONs purely from on-phone/cloud `.mvmeta.json` sidecars + chunk listings; C10 grown up) | Already half-designed (mvmeta exists, never read) | Implementing the reader half (C10 `--repair`); a test proving a from-zero rebuild |
| M6 | **Google API reopening for personal-use automation** | ~1% (policy direction is the opposite) | Any Photos API announcement re user-owned-content scopes — re-check yearly |
| M7 | **Whole-vault semantic search** (F8 CLIP/Whisper over restored-window content + F7 sub-grep) | Technically fine, GPU-time expensive | The enrichment window (U1) making per-title analysis a natural free rider |
| M8 | **Family multi-home** (F5 git-synced library + a second vault PC + shared Jellyfin) | Mechanically straightforward, operationally fiddly | An actual second household user appearing |
| M9 | **Telegram-as-second-backend via tdl patterns** (G3 + F9: real file storage, no container constraint, generous limits) | Real option if Google sours | First Google policy tremor; the F9 Backend ABC landing |
| M10 | **"Pixel farm" scaling** (more grandfathered Pixel 1s → more accounts/lanes/replicas) | Hardware is cheap & available used | Topology answer; any throughput or redundancy pressure |

## D. Standing re-check ritual

Once a year (or on any platform announcement): re-test A1 (API scopes), A5 (upload acceptance of a
wrapped test file), B1 (client DisplayMessage matrix after major client releases), M6; skim rclone/
gphotosdl + Jellyfin release notes; update this file's dates. The Tier S daemon's doctor task should
eventually automate the testable subset (A1 probe, session health, upload acceptance canary).
