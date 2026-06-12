# Apple TV-style UI — Long-form Roadmap

> ⚠️ **PARTIALLY SUPERSEDED (2026-06-12, fable-review session).** The current master plan is
> [`improvements/ROADMAP_END_GOAL.md`](improvements/ROADMAP_END_GOAL.md)
> with task tracking in `improvements/improvements_tierS.md` / `improvements/improvements_tierU.md`. What changed since this
> file was written (2026-05):
> - **§5 dummy detection is STALE**: dummies are no longer `<1 KB` text blobs starting with
>   `"Original Hash:"` — the video-dummy feature (PRs #1/#3) made them ~10 KB *valid playable videos*.
>   Detection = size < `DUMMY_MAX_BYTES` (200 KB) + `uid` sidecar / daemon API lookup.
> - **Sequencing changed**: a webhook-driven daemon (IMP-S2/S3/S4) now delivers ~90% of the experience
>   BEFORE any C# plugin is written; the plugin described here became the polish phase (IMP-U5).
>   "Play on a dummy" is no longer a failure to intercept — it IS the fetch-request signal.
> - **Interaction is in-client-only** (user decision): the "phone buzzes" notification in §10 is now a
>   Jellyfin `DisplayMessage` / home-row update instead.
> - **Still valid and confirmed**: the Path-D Jellyfin decision (§3) — re-affirmed by the 2026-06-12
>   session; the prerequisite analysis (§2); the API-surface sketch (§7), which now describes the
>   daemon's API; most of §8's open questions (carried into Tier S/U tasks).
> This file is kept as the original design record — read it WITH the corrections above.

---

## 1. Vision

A smooth, Apple TV-like UI for the archive. Large posters and fanart. Icon-driven navigation. Fluid transitions. Every entry across `library_movies.json`, `library_series.json`, and `library_anime.json` appears as a tile. Each tile shows its state at a glance:

- 🟢 **Local** — file is on disk, click to play.
- ☁️ **Archived** — file lives in Google Photos. Click to **Restore**, then play.
- ⏳ **Restoring** — fetch in progress. Live progress bar.
- 🔄 **Fetched** — chunks are downloaded, awaiting merge. One-click finalize.

Clicking an archived tile gives a single, prominent **[Restore & Play]** button. The user presses it; MediaVault dispatches the fetch via `mainfetch.py`, runs `cmd_restore`, hands the resulting file to a player. Done.

---

## 2. Why this is the LAST piece, not the next

Three hard prerequisites must be in place before any UI work begins. Each is a Tier A or Tier E task in the existing improvement set.

| Prerequisite | Why it blocks the UI | IMP reference |
|---|---|---|
| **JSON output mode on every command** | The UI consumes data via JSON, not by scraping emoji stdout. | IMP-A4 |
| **TMDB / TheTVDB / AniDB metadata enrichment** | Without real titles, posters, synopses, the UI is a wall of slugs. | IMP-E3 |
| **A small local REST API around `main.py`** | UIs need stable async APIs, not subprocess spawns. | IMP-E12 (web command) |

Plus one soft prerequisite:
- **Websocket status broadcaster** for live progress during long ops. Without it, "click Restore" feels like a black hole. | IMP-F10

If any of these is missing, the UI will either feel broken (no progress) or look bad (no posters / fake titles), and that first impression is what kills adoption.

---

## 3. Why Jellyfin (vs build-from-scratch / Plex / Stremio)

I considered four paths. Summary of trade-offs:

### Path A: Build a custom Electron/Tauri/web SPA from scratch
- **Pro**: total design control. No dependency on third-party server.
- **Con**: 2-4 weeks of frontend work to get to Apple TV-feel polish. Player integration is a project of its own. Reinvents library browsing that Jellyfin already does well.
- **Verdict**: rejected — too expensive for a solo dev who just wants the UI to work.

### Path B: Plex plugin
- **Pro**: Plex has the slickest Apple TV native client. Most "Apple TV-like" out of the box.
- **Con**: Plex's third-party plugin API was deprecated years ago. Modern Plex is closed to extensions of the kind needed (intercept Play, show Restore). Cannot replace the [Play] button on dummy files; Plex would just try to play a 1 KB text file and fail.
- **Verdict**: rejected — the API surface required does not exist on modern Plex.

### Path C: Stremio addon
- **Pro**: Stremio's addon system is permissive. Apple TV client exists.
- **Con**: Stremio's data model is streaming-link-centric, not library-centric. Doesn't model "I own this file but it's currently in cold storage". Apple TV client is less polished than Plex/Jellyfin.
- **Verdict**: rejected — wrong abstraction for the cold-storage use case.

### Path D: Jellyfin plugin ← **CHOSEN**
- **Pro**:
  - Open source. Modern .NET plugin model. Active community (https://github.com/awesome-jellyfin/awesome-jellyfin).
  - The default web UI is already poster-grid, tile-driven, and themeable to look very Apple TV-like.
  - Jellyfin clients exist for Apple TV (Swiftfin, Infuse), Android TV (ARVIO, Findroid), web, mobile — all consuming the same server.
  - The "media item with an action button" pattern is supported via plugins.
  - 95% of the UI work is done by Jellyfin's team. MediaVault contributes the "archived/restore" semantic on top.
- **Con**:
  - Plugins are written in **C# / .NET**, not Python. Skill mismatch with the rest of MediaVault.
  - Requires running a Jellyfin server. Resource overhead (~500 MB RAM, ~200 MB disk).
  - The dummy-file detection layer needs careful design (see Section 5).
- **Verdict**: ✅ chosen. The trade-off — learn a bit of C# to inherit a polished media browser — is the right one.

---

## 4. Architecture overview

```
┌──────────────────────────────────────────────────────────────────┐
│   USER DEVICE (Apple TV / iPad / Web Browser / Android TV)        │
│                                                                    │
│   ┌────────────────────────────────────────────────────────┐      │
│   │  Jellyfin client (Swiftfin / Infuse / web / etc.)      │      │
│   │  - poster grid, episode list, search, playback         │      │
│   └────────────────────┬───────────────────────────────────┘      │
└────────────────────────┼─────────────────────────────────────────┘
                         │ HTTPS (Jellyfin's normal API)
                         ▼
┌──────────────────────────────────────────────────────────────────┐
│   JELLYFIN SERVER (host PC, same machine as MediaVault)           │
│                                                                    │
│   ┌────────────────────────────────────────────────────────┐      │
│   │  Jellyfin core + standard library scanner              │      │
│   │  (scans C:\Media\{Movies,Series,Anime})                │      │
│   └────────────────────┬───────────────────────────────────┘      │
│                        │                                          │
│   ┌────────────────────▼───────────────────────────────────┐      │
│   │   MEDIAVAULT-JELLYFIN PLUGIN (this file's deliverable) │      │
│   │   - detects <1 KB dummy files                          │      │
│   │   - marks them as "Archived" with a Restore button     │      │
│   │   - intercepts Play on dummy files → triggers Restore  │      │
│   │   - shows live progress during restore                 │      │
│   │   - calls Jellyfin's library refresh after restore done│      │
│   └────────────────────┬───────────────────────────────────┘      │
└────────────────────────┼─────────────────────────────────────────┘
                         │ HTTP (localhost only)
                         ▼
┌──────────────────────────────────────────────────────────────────┐
│   MEDIAVAULT REST API (FastAPI shim around main.py)               │
│                                                                    │
│   - GET  /library                  → all entries as JSON          │
│   - GET  /entry/<id>               → one entry                    │
│   - POST /fetch_restore/<id>       → kick off fetch + restore     │
│   - GET  /status/<job_id>          → progress for a running op    │
│   - WS   /events                   → live event stream            │
│                                                                    │
│   Wraps: subprocess calls to `python main.py <cmd> --json`        │
└────────────────────────┬─────────────────────────────────────────┘
                         │ subprocess
                         ▼
┌──────────────────────────────────────────────────────────────────┐
│   MEDIAVAULT CLI (main.py / mainfetch.py)                         │
└──────────────────────────────────────────────────────────────────┘
```

Key property: the Jellyfin plugin is the only NEW long-running surface. The MediaVault REST API is just `main.py` with a thin HTTP wrapper. Existing logic doesn't move.

---

## 5. How the Jellyfin plugin detects "archived"

This is the trickiest part. Jellyfin's library scanner sees a `.mkv` file with size 245 bytes and tries to probe it as a video. ffprobe fails (it's a text file). Jellyfin marks it as "unrecognized" or unplayable.

**The fix**: the plugin hooks Jellyfin's `IItemResolver` interface. Before Jellyfin's default video resolver looks at a file:

1. Plugin checks file size. If <1024 bytes, peek the first line.
2. If the first line starts with `"Original Hash: "` (the exact `cmd_replace` dummy format from `main.py:772`), the plugin classifies the file as `BaseItemKind.Video` AND attaches a custom metadata flag: `MediaVaultArchived = true`.
3. The plugin reads the entry's `metadata` from MediaVault's API (`GET /entry/<id>` keyed by the dummy's path) and injects:
   - `Name`, `Year`, `Overview`, `Genre`, `Runtime`, `OfficialRating`, `CommunityRating` — from TMDB enrichment (IMP-E3).
   - `Images[Primary]` ← `<folder>/poster.jpg`
   - `Images[Backdrop]` ← `<folder>/fanart.jpg`
   - Custom property: `IsArchived = true`.

Now Jellyfin's library shows the item with full metadata and posters, just like any normal movie. The UI doesn't see a difference yet.

**The Play interception**:

4. Plugin registers a `PlaybackInterceptor` (or equivalent — exact API depends on Jellyfin version).
5. When the user clicks **Play** on an archived item, the interceptor checks `IsArchived`. If true:
   - Cancel the standard play action.
   - Show a confirmation modal: `"This file is archived. Restore from cloud? (~X MB, ~Y minutes)"`.
   - On confirmation, POST `/fetch_restore/<id>` to the MediaVault API.
   - Open a live-progress overlay subscribed to the WebSocket `/events` stream.
   - On completion, trigger Jellyfin's library refresh for the folder.
   - Auto-start playback once the refresh registers the now-real file.

---

## 6. Phased plan

### Phase 0 — Prerequisites (gated)
*This phase is the Phase 6 / 7 of `improvement_details.md`. Don't skip it.*

- ✅ IMP-A1, A2, A4, A5 complete (mvcommon, argparse, --json, config).
- ✅ IMP-E3 complete (TMDB metadata enrichment).
- ✅ IMP-E12 complete (REST API web command).
- ✅ IMP-F10 complete (websocket status).
- ✅ Jellyfin server running on the user's PC, configured to scan `C:\Media\{Movies,Series,Anime}`.

**Exit criteria**: a user can browse Jellyfin's web UI and see all entries (with stale/missing metadata, since dummies confuse the scanner). The dummies show up as "unknown" items. This is the baseline we improve from.

### Phase 1 — Jellyfin plugin scaffold
*Effort: 1 week*

- Set up the C# / .NET project from Jellyfin's plugin template (https://github.com/jellyfin/jellyfin-plugin-template).
- Create the basic plugin metadata (name, description, icon, version).
- Build, sign, install to local Jellyfin. Confirm it loads and shows in Jellyfin's plugin list.
- Add an empty `IItemResolver` that does nothing yet.
- Add a config page (Jellyfin plugins have a web config UI) for: MediaVault API URL (default `http://localhost:8765`), API token if any.

**Exit criteria**: plugin loads in Jellyfin, no errors, no behaviour change.

### Phase 2 — Dummy detection + metadata override
*Effort: 1-2 weeks*

- Implement `IsArchivedDummy(path)` — open file, read first 32 bytes, check for the `Original Hash:` marker.
- Implement `MediaVaultItemResolver : IItemResolver`:
  - Returns `BaseItemKind.Video` for dummies.
  - Sets custom `ProviderIds["MediaVault"] = <manual_id>` (extracted from `uid` sidecar).
- Implement a metadata provider `MediaVaultMetadataProvider : IRemoteMetadataProvider<Movie, MovieInfo>`:
  - On Jellyfin's metadata fetch, query MediaVault API for the entry.
  - Return enriched `MetadataResult<Movie>` populated from MediaVault's TMDB-derived metadata.
- Same for `Episode` and `Series` kinds.

**Exit criteria**: archived items appear in Jellyfin's grid with real poster, real title, real synopsis. Clicking shows correct details. Play button still does the wrong thing (tries to play the dummy, fails).

### Phase 3 — Play interception + restore flow
*Effort: 1-2 weeks*

- Implement `PlaybackInterceptor` (Jellyfin's exact extension point varies by version; investigate during the spike).
- On play of an archived item, show a custom modal via Jellyfin's `IServerEntryPoint`.
- POST to `MediaVaultClient.FetchRestoreAsync(id)`; returns a `job_id`.
- Subscribe to WebSocket `/events`, filter by `job_id`, update modal progress.
- On `status == "done"`, call Jellyfin's library refresh API for the folder.
- Once Jellyfin re-scans and replaces the dummy with the real file, auto-resume the play action.

**Exit criteria**: user clicks Play on an archived item → confirmation modal → fetch progress → file restored → playback starts. End-to-end happy path works.

### Phase 4 — Apple TV / multi-client polish
*Effort: 1 week*

- Install **Swiftfin** (https://github.com/jellyfin/Swiftfin) on the user's Apple TV (if owned) or as an iOS app.
- Verify the archived-item flow works end-to-end on Apple TV. The custom modal is a server-side concept rendered by the Jellyfin web view; Swiftfin should inherit it via the standard Jellyfin API. If it doesn't, consider a fallback "restore in browser, play on Apple TV" handoff pattern.
- Theme the Jellyfin web UI to feel more Apple TV-like:
  - Use a community theme from `awesome-jellyfin` (e.g., **Ultrachromic**, **Finamp**, **KefinTweaks**) as starting point.
  - Custom CSS for tile sizes, focus rings, transitions.
- Test on tablet / phone via the Jellyfin mobile apps.

**Exit criteria**: the user can navigate, browse, and restore from a real Apple TV in their living room. The experience FEELS like Apple TV.

### Phase 5 — Stretch features
*Effort: open-ended*

- **Continue Watching rail** powered by MediaVault watch-state (IMP-E4).
- **Smart prefetch suggestions** ("You watched 3 Marvel movies this week; prefetch the next 2?") — ties into IMP-F6 smart pruning.
- **Family multi-user**: Jellyfin's built-in multi-user model. Each user's watch state and restore actions logged separately.
- **Voice control** via Apple TV's Siri remote (Swiftfin supports it natively).
- **Library health badge** on the home page surfaced from IMP-D4 verify_library.

---

## 7. Required API surface on MediaVault side

Concrete endpoints the plugin needs. To be added under IMP-E12 (or a follow-up):

### `GET /library`
Returns: array of all entries across all three libraries. Schema = the existing JSON schemas with parent_id links resolved. Pagination optional (current scale ~570 entries — fits in one payload).

### `GET /entry/<id>`
Returns: single entry by manual_id. Includes everything: status, hash, split_info, metadata, watch_state (if E4 done), folder_path, on-disk-status (`local | dummy | partial`).

### `POST /fetch_restore/<id>`
Body: `{ "episodes": "1-3" | null }`
Returns: `{ "job_id": "uuid", "estimated_seconds": 1234 }`
Side effect: kicks off `cmd_fetch_restore` in a subprocess. Streams events to `/events`.

### `GET /status/<job_id>`
Returns: `{ "job_id": "...", "status": "running|done|failed", "progress_pct": 73, "current_step": "downloading chunk 4 of 7", "log_tail": [...] }`

### `WS /events`
WebSocket. Server emits events: `{ "type": "progress", "job_id": "...", "progress_pct": 50, "message": "..." }`. Plugin subscribes once at startup, filters by job_id.

### `GET /healthz`
Returns: `{ "status": "ok", "doctor_passes": true, "library_count": 532 }`. Plugin can show a health badge.

### Auth
- Default: localhost-only, no auth (plugin runs on same machine).
- Optional: HMAC token in `mvconfig.json`. Plugin reads token from its config page.

---

## 8. Open questions to resolve before Phase 1

These are deliberately deferred. Each unlocks during the spike or early Phase 1.

1. **Jellyfin plugin API stability.** Jellyfin's plugin API has changed across major versions. Pin to Jellyfin 10.9+ or whichever LTS is current at build time. Decide whether to track latest or pin.
2. **Dummy detection performance.** Reading the first 32 bytes of every file in a 530-entry library on every scan is fine. But what about a 50k-entry library years from now? Cache dummy-detection results by `(path, mtime)`.
3. **Restore-while-Jellyfin-is-scanning race conditions.** What if Jellyfin starts a scan in the middle of a restore? The file is half-merged. Plugin should hold a soft lock during restore, or Jellyfin's scanner should be told to ignore the folder.
4. **iOS / Apple TV custom modal rendering.** Verify that the custom restore confirmation modal renders correctly in Swiftfin. If not, build a fallback: clicking Play on an archived item from Apple TV opens a "Restoring on PC… please wait" placeholder, and the user gets a push notification when it's playable.
5. **Encryption (IMP-F1) interaction.** If the user enables per-entry encryption, the restored file is encrypted on disk too — Jellyfin can't play it directly. Plugin would need to invoke decryption before refresh. Defer until F1 ships.
6. **Multi-Jellyfin-user trust model.** If the user shares Jellyfin with family, only the owner should be allowed to trigger Restores (because they cost real bandwidth and time). Plugin enforces a "Restorers" role configured in the plugin settings.
7. **Whether to publish the plugin.** Once it works for the user, others with the same MediaVault-style setup could benefit. Publishing to the Jellyfin plugin catalog requires meeting their submission criteria. Defer until the plugin is stable.

---

## 9. Decisions already made

- **C# / .NET plugin** is the path (vs Python frontend, vs Plex, vs Stremio, vs custom).
- **Jellyfin's existing UI is the chrome.** No custom Electron/Tauri shell.
- **Apple TV experience via Swiftfin** (Jellyfin's open-source Apple TV client) — not a custom tvOS app. Falls back to web/iPad if Apple TV hardware isn't owned.
- **Restore is a foreground action with explicit user confirmation.** No "auto-restore the moment you click Play" — restores are expensive enough that confirming is the right default.
- **Plugin reads MediaVault's REST API**, not direct JSON files. Decoupling is worth the small extra latency.

---

## 10. What success looks like

A user sits on their sofa. Apple TV remote in hand. They open Swiftfin. They see a tile for *Inception* with a poster, fanart, rating, synopsis. Just like Apple TV's own UI. They click Play. A modal: *"Inception is archived. Restore from cloud (76 GB, est. 45 min)?"* They tap Restore. Twenty minutes later their phone buzzes: *"Inception restore complete."* They tap Play. The movie streams instantly.

That's the goal. Everything in this file is in service of it.

---

## 11. Cross-references

| Topic | Where |
|---|---|
| The original captured vision | Memory: `[[project-future-apple-tv-ui]]` |
| Prerequisite tasks | `improvements_tierA.md` (A1, A2, A4, A5), `improvements_tierE.md` (E3, E12), `improvements_tierF.md` (F10) |
| Jellyfin plugin path rationale | `improvements_tierG.md` (G4) |
| Watch state ingestion | `improvements_tierE.md` (E4) |
| Live status stream | `improvements_tierF.md` (F10) |
| TMDB enrichment | `improvements_tierE.md` (E3) |
| Local web UI (precursor) | `improvements_tierE.md` (E12) |
