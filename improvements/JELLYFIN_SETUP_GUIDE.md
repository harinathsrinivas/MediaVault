# Jellyfin from Scratch — MediaVault-Tuned Setup Guide (Windows, 2026)

The "super detailed steps of what all to change in Jellyfin from scratch install to everything"
deliverable (session decision #1). Target machine: the always-on Alienware that already runs
MediaVault. Written against Jellyfin **10.11.x** (current stable line, 2026-06).

> Phase-0 of `ROADMAP_END_GOAL.md` = this guide + its validation checklist at the end.
> Nothing here modifies MediaVault itself; it is safe to do immediately.

---

## 0. Pre-flight (10 min)

1. Confirm the media roots exist and are on always-available volumes: `C:\Media\Movies`,
   `C:\Media\Series`, `C:\Media\Anime` (these are `LOCAL_ROOT` categories in `mvcommon.py`).
2. Update the NVIDIA driver to ≥ 522.25 (Jellyfin 10.10+ NVENC floor; just take the latest
   Game-Ready/Studio driver).
3. Pick the service account: install Jellyfin as a **Windows service** running as your user (it must
   read `C:\Media`). Plan for the server to be ON whenever the TV might be used — pair with the
   Alienware's "never sleep, display off" power profile.
4. Note for later: Jellyfin must **never** be allowed to delete library files (it would delete
   dummies or originals — MediaVault owns the filesystem). This is enforced in §4 and §6.

## 1. Install the server (15 min)

1. Download the **Windows x64 installer** from https://jellyfin.org/downloads/server (Stable).
2. Run it: choose *Install as a Windows Service*; service account = the user that owns `C:\Media`
   (NOT Local System — drive mappings and profile paths behave better); default ports (8096 HTTP).
3. Allow the Windows Firewall prompt (Private networks). If no prompt: add inbound TCP 8096 (+ 8920
   if you later enable HTTPS) for Private profile.
4. Open `http://localhost:8096` → the first-run wizard starts.

## 2. First-run wizard (5 min)

1. UI language → English (or preference).
2. Create the admin user (e.g. `harinath`) **with a real password** (TV clients will use Quick
   Connect, so the password is rarely typed — make it strong).
3. **Skip adding libraries inside the wizard** (we add them with exact settings in §3).
4. Metadata language: English / country as preferred.
5. Remote access: **leave "Allow remote connections" OFF for now** (LAN-only until you consciously
   decide otherwise — see §8).

## 3. Libraries — exact settings per library (20 min)

Create four libraries (Dashboard → Libraries → Add Media Library):

### 3.1 Movies
- Content type: **Movies**; Display name: Movies; Folder: `C:\Media\Movies`.
- **Enable real-time monitoring: ON** (instant pickup when MediaVault restores/replaces a file;
  the daemon will additionally call targeted refreshes).
- Metadata downloaders: TheMovieDb, OMDb. Image fetchers: TheMovieDb. **Keep "Save artwork into
  media folders" OFF for now** (MediaVault already writes `poster.jpg`/`fanart.jpg`; Jellyfin reads
  them as local images automatically — local images win, no clutter).
- **NFO settings**: enable "Prefer embedded titles over filenames" OFF (filenames are release names;
  NFOs will come later via IMP-E3 / IMP-S7). When MediaVault starts writing NFOs, Jellyfin picks
  them up with zero re-config (Local Metadata is always read first).
- Trickplay: **ON** (see §5.3); Chapter images: ON (optional, cheap at restore-time only).
- ⚠️ Expectation setting: an **archived** title is a 10 KB / 2-second / 128×72 dummy — Jellyfin will
  probe it as a *valid* video with absurd technical metadata (2 s duration). Title/poster/synopsis
  still render fine (folder images + TMDB by name). The daemon (Phase 1+) overlays status via
  collections; the eventual C# plugin (Phase 4) replaces probed runtime with library-true values.
  **Do not "fix" dummies by excluding small files** — they must stay visible: they ARE the catalog.

### 3.2 Series
- Content type: **Shows**; Folder: `C:\Media\Series`; same toggles as Movies.
- Seasons/episodes resolve from the existing `.../Show/Season X/...SxxExx...` naming; combined
  episodes (`S04E19E20`) are natively understood by Jellyfin's episode parser (it shows E19-E20) —
  consistent with MediaVault's `multi_ep_alias` model (one file, two episode numbers).

### 3.3 Anime
- Content type: **Shows**; Folder: `C:\Media\Anime`.
- Install the **AniDB** and/or **AniList** metadata plugins (§5.1) BEFORE the first full scan of this
  library, then set this library's metadata downloader order: AniDB/AniList first, TVDB fallback.
- Absolute-numbered files (`deathnote07`) may need per-show "Display order: Absolute" (Series →
  Edit Metadata → Display order) — fix-as-you-notice, not upfront.

### 3.4 Sports / Others (filename-as-title)
- Content type: **Other Videos** (Plex) / **Home Videos** (Jellyfin/Emby; Emby
  also exposes this as a "Mixed Content / Home videos" type); Display name: Sports
  (or Others); Folder: `C:\Media\Sports`.
- These library types use the **filename as the title with no online metadata
  agent** — exactly right for sports, which isn't on TheMovieDb/TheTVDB. Do
  **not** attach a metadata downloader or image fetcher; leave all scrapers OFF.
  (As of the 2026 server versions, Plex "Other Videos", Jellyfin/Emby "Home
  Videos" are the filename-as-title, no-agent library kinds — verify the exact
  label in your server's "Add Library → Content type" dropdown.)
- This is the home of MediaVault's `oth-` / `library_others.json` category
  (IMP-D18). MediaVault auto-captures the exact technical spec (codecs, HDR,
  audio, duration) into `library_others.json`; the on-disk filenames ARE the
  display titles, so name them meaningfully (date, teams, half/stage, e.g.
  `2026-06-14 Spain vs Portugal - First half ...`).
- Same dummy expectation as §3.1: an archived match-half is a 10 KB / 2-second
  dummy that probes as a *valid* (absurdly short) video — keep it visible, it IS
  the catalog. MediaVault's enrichment commands deliberately skip `oth-` entries,
  so no scraper will mis-tag or rename the Sports folders.
- List-capable: when documentaries arrive they get the same treatment — point the
  same "Other / Home Videos" library at the added subfolder, or create a second
  one (the MediaVault `CATEGORY_ROOTS["other"]` list grows with no code change).

### 3.5 Library-level hygiene
- Dashboard → Libraries → Manage Libraries: confirm **"Delete files" stays admin-only** and never
  enable any "remove missing" auto-cleanup. MediaVault is the only thing that deletes media.
- Scheduled Tasks (Dashboard → Scheduled Tasks): set "Scan media library" to every 12 h (real-time
  monitoring + daemon refreshes do the timely work; the scheduled scan is just a safety net).

### 3.6 Extras (MediaVault's `--extras` folders) — the folder NAME decides recognition

MediaVault (IMP-D19) gives a title's bonus-content subfolder — `…\<Title>\Specials\`,
`…\Extra\`, `…\Trailers\` — the full archive lifecycle, and **restores it in place**: an extras
group is keyed by the folder's path *relative* to the title folder, so a fetched extra lands back
at exactly the path it came from (`fetch_restore <id> --fetchExtras`). What MediaVault cannot do
is make the media server *present* those files as extras — that depends entirely on the folder
**name**, and ⚠️ **the two names this vault currently uses are not recognized extras names**:

| Server | Extras subfolder names it recognizes (placed directly inside the movie / series / season folder) | `Specials` | `Extra` |
|---|---|---|---|
| **Jellyfin** (10.x media docs; same list on the movies and shows pages) | `behind the scenes`, `deleted scenes`, `interviews`, `scenes`, `samples`, `shorts`, `featurettes`, `clips`, `other`, `extras`, `trailers` (plus the non-video `theme-music`, `backdrops`) | ❌ not in the list | ❌ only the **plural** `extras` is listed |
| **Emby** (Movie Naming article) | `extras`, `specials`, `shorts`, `scenes`, `featurettes`, `behind the scenes`, `deleted scenes`, `interviews`, `trailers` | ✅ recognized | ❌ not listed |
| **Plex** (needs the **Local Media Assets** agent enabled for the library) | `Behind The Scenes`, `Deleted Scenes`, `Featurettes`, `Interviews`, `Scenes`, `Shorts`, `Trailers`, `Other` — or the per-file suffixes `-behindthescenes`, `-deleted`, `-featurette`, `-interview`, `-scene`, `-short`, `-trailer`, `-other` | ❌ | ❌ |

The Jellyfin docs write these names lowercase and do **not** explicitly state that matching is
case-insensitive — so mirror the documented lowercase form rather than relying on casing.

**What to do about it:**

1. **Rename the folder to a recognized name BEFORE registering it with MediaVault.** e.g. rename
   `…\Death Note …\Extra` → `…\extras` (or `behind the scenes`), *then* run
   `python main.py add_extras <title_id> "<…\extras>"`.
2. **Never rename an extras folder after registering it.** The stored `group_rel` *is* that
   relative path: renaming afterwards orphans the group (a restore would recreate the OLD folder
   name, and the renamed files would show up in `scan_unprepped` as unknown). Rename first,
   register second. Re-registering a renamed folder means re-uploading it.
3. **A series `Specials` folder is a different concept from extras.** Jellyfin documents show
   specials as **Season-0 episodes** in a `Season 00` folder inside the series folder; a folder
   literally named `Specials` is documented as *neither* extras nor Season 0. So decide what those
   videos are: episodes → move them into `Season 00`; bonus clips → use `extras` /
   `behind the scenes`. (Either way MediaVault archives them the same; only the presentation
   differs.)
4. **An unrecognized folder name is not crisply documented on any of the three servers** — the
   videos may simply be ignored, or picked up as separate items/episodes, depending on server and
   library settings. Treat "unrecognized ⇒ will not appear in the Extras row" as the safe
   expectation and check the library after the first scan.
5. **Naming the files matters:** a local extra is in practice labelled from its filename (Jellyfin
   describes non-parsable filename text becoming the stream title, but does not formally promise
   it for extras), and MediaVault carries the filename through verbatim — so name them
   meaningfully (`Behind the Scenes - Japanese Voice Cast.mkv`, not `extra1.mkv`).
6. ⚠️ **Same dummy expectation as §3.1/§3.4:** an *archived* extra is a 10 KB / 2-second dummy
   that probes as a valid (absurdly short) video. Keep it visible — it is the catalog entry;
   `fetch_restore <id> --fetchExtras` brings the real bytes back and real-time monitoring flips
   the tile.

> **Sources / verify before relying on a name:** jellyfin.org media docs (movies + shows pages),
> emby.media "Movie Naming" article, and Plex's *local files for trailers and extras* articles —
> the Plex **movie** article was inaccessible at the time of writing, so the Plex list above is
> corroborated from Plex naming documentation and search results rather than quoted verbatim.
> Check your own server version's docs (and its library settings) before mass-renaming folders.

## 4. Users & devices (10 min)

1. Admin user = you. Create additional profiles only if family members need separate watch states.
2. For every user: Profile → **disallow media deletion**; allow remote control of other devices for
   the admin only.
3. Enable **Quick Connect** (Dashboard → General) — it is the sane way to log TV apps in (code shown
   on TV, approved from the web UI).

## 5. Plugins & enhancement layer (30 min)

Dashboard → Plugins → Catalog. Install, then **restart Jellyfin once at the end**.

### 5.1 Install now (Phase 0)
| Plugin | Why | Config |
|---|---|---|
| **Webhook** | THE MediaVault integration backbone (events → daemon) | Add a *Generic* destination later when the daemon exists (Phase 1): `http://localhost:8765/jf-events`, enable PlaybackStart / PlaybackStop / Item Added; template = JSON passthrough |
| **Intro Skipper** | Netflix-style skip-intro via audio fingerprinting | Default; it analyzes during scans. Only works on *local* (restored) episodes — fine: it fingerprints at restore-time before re-archive, results persist |
| **AniDB / AniList** | Anime metadata | §3.3 |
| **Home Screen Sections** (+ **Collection Sections**) | Server-driven Netflix rows ("Ready to Watch", "Because You Watched") | Defaults now; the daemon drives collections later |
| **Trakt** *(optional)* | Off-site watch-state backup | Link account if wanted |

### 5.2 Skip (deliberately)
- Any "library cleanup"/dedupe plugin (would fight MediaVault's dummies).
- Transcode-killer/bandwidth plugins (single-house LAN).
- Jellyseerr/JellyBridge — only relevant if you later want *new-content discovery*; MediaVault's own
  vault is the catalog today.

### 5.3 Trickplay policy (important MediaVault nuance)
Dashboard → Playback → Trickplay: enable; generation = **on library scan**; hardware decoding ON.
Trickplay images for a title are generated **only while the real file is local** — i.e., between
restore and archive. They persist in Jellyfin's data dir afterward, so a once-watched title keeps
scrub previews even when re-archived. Phase-2 daemon adds "generate trickplay + chapter images +
Intro-Skipper fingerprint **before** auto-archive" as an explicit post-restore step, so every fetched
item permanently gains the Netflix polish.

## 6. Playback & transcoding (15 min)

Dashboard → Playback → Transcoding:
1. Hardware acceleration: **NVIDIA NVENC**.
2. Enable decoding for H.264, HEVC, HEVC 10-bit, VP9 (skip AV1 *decode* unless the GPU is RTX 30+).
3. **Enable Tone mapping** (CUDA) — for HDR/DV → SDR when a non-HDR client transcodes.
4. Transcode path: a fast SSD scratch folder, e.g. `C:\JellyfinTranscode` (NOT inside `C:\Media` —
   keep MediaVault's scan surfaces clean).
5. Leave "Allow encoding in HEVC" OFF initially (H.264 transcode output = maximum client
   compatibility; revisit if remote streaming over thin pipes appears).
6. Reality check: Infuse (Apple TV) and CoreELEC/Kodi (Ugoos) **direct-play** virtually everything —
   transcoding only kicks in for phones/web. The 5-session consumer-GPU NVENC cap is irrelevant here.

## 7. Clients (per device, 10-20 min each)

### 7.1 Apple TV
1. Install **Swiftfin** (free) → Quick Connect sign-in. Native Jellyfin UX, the daily driver UI.
2. Install/keep **Infuse Pro** → add Jellyfin as a "Media Server" (it speaks the Jellyfin API
   directly) → its decoder direct-plays DTS/TrueHD *content* (tvOS still downmixes — the Ugoos is
   the bitstream path). Use Infuse when codec compatibility or scrubbing smoothness wins.
3. Both sync watch state through the server → webhooks fire → the MediaVault flow works from either.

### 7.2 Ugoos AM6B+ (projector — the quality path)
1. CoreELEC (you already run it for DV FEL): install the **Jellyfin for Kodi** addon
   (repository.jellyfin.kodi zip → addon) and sign in (server `http://<alienware-ip>:8096`).
2. Choose **Add-on playback mode** (Native mode bypasses the Jellyfin server's session tracking;
   Add-on mode keeps watch-state + webhook events flowing — required for the archive flow).
3. Result: browse the same library on the projector with full **DV P7 FEL + TrueHD bitstream**
   for restored items.

### 7.3 Phones / web
Official Jellyfin apps (iOS/Android) + `http://<alienware-ip>:8096` in any browser. The web client is
also where Home Screen Sections / future plugin pages are richest.

## 8. Network & remote access policy

- Phase 0-3: **LAN only.** No port-forward, no reverse proxy, "Allow remote connections" off.
- If/when remote is wanted: Tailscale (zero-config WireGuard) is the recommended path for a
  single-user setup — Jellyfin stays unexposed; your devices join the tailnet. A public reverse
  proxy (Caddy/Traefik + HTTPS 8920) is the heavier alternative; decide at that phase, not now.

## 9. Backups & maintenance

- 10.11 has **built-in backups** (Dashboard → Backups): schedule weekly, target a folder that your
  existing backup routine covers (NOT inside `C:\Media`). It captures DB (users, watch states,
  collections, trickplay index) — the things you can't regenerate quickly.
- After major Jellyfin upgrades: re-run the §10 checklist quickly; plugin ABI breaks are the usual
  casualty (Webhook + Intro Skipper publish matching versions per server release).

## 10. Phase-0 validation checklist (run before building the daemon)

- [ ] All three libraries scanned; counts ≈ library JSON leaf counts (102 movies / ~290 series eps / ~140 anime eps).
- [ ] An **archived** title (10 KB dummy) shows with correct poster + title; "playing" it plays the
      2-second black clip and returns (this exact event becomes the fetch trigger in Phase 1).
- [ ] A **local** title direct-plays on: Swiftfin, Infuse, CoreELEC/Kodi (watch state syncs back).
- [ ] Manually `fetch_restore` one title → within seconds the tile flips playable (real-time monitor)
      → watch 2 minutes → stop → watched-state appears in Jellyfin.
- [ ] Dashboard → Sessions: with the Apple TV app open, "Send message" pops a visible message on the
      client (validates the in-client notify channel; test Swiftfin, Infuse, AndroidTV, Kodi — record
      which render it, fallback = action-stub pattern).
- [ ] Webhook plugin: point a test destination at `https://webhook.site/<id>`, play/stop something,
      confirm PlaybackStart/Stop JSON arrives with item path + user + played-to-completion flag.
- [ ] NVENC: force-transcode in the web client (cap bitrate) → Dashboard shows "(hw)" in the active
      stream; GPU video-engine usage visible in Task Manager.
- [ ] Trickplay generated for at least one local item; scrubbing shows previews.

**Exit criteria = all boxes ticked.** Then Phase 1 (daemon + webhook wiring) starts — see
`ROADMAP_END_GOAL.md`.
