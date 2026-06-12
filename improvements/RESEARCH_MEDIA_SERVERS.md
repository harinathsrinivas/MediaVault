# Research — Media Servers & Clients (Jellyfin-first, Emby/Plex delta)

Researched 2026-06-12 (fable-review session). Decision context: user chose **Jellyfin-first**, owns
**Emby Premiere lifetime**, can buy **Plex lifetime before the July 2026 price window** (but see §3 —
the price already jumped), and requires **in-client-only** notify/archive interaction.

---

## 1. Jellyfin deep dive (the chosen platform)

### 1.1 Platform state (10.10 → 10.11, 2026)

- **10.10** shipped the **Media Segments plugin API** — plugins can mark typed time-spans
  (intro/credits/recap) that clients render as Netflix-style Skip buttons; and major
  **trickplay** performance work (keyframe extraction up to ~100× faster), making scrub-preview
  thumbnails practical on big libraries.
- **10.11** brought EF Core database migration, **built-in backups**, FFmpeg 7.1.
- Trickplay (scrub thumbnails), chapter images, intro skipping (via plugin/segments), watched-state,
  Continue Watching, and Next Up are all **native or first-party-plugin** features now — most of the
  "Netflix basics" exist out of the box.

### 1.2 Integration surfaces MediaVault can use (ranked by fit)

| Surface | What it gives us | Fit for the end goal |
|---|---|---|
| **Webhook plugin** (`jellyfin-plugin-webhook`) | Server → our daemon HTTP POSTs on `PlaybackStart`, `PlaybackStop`/progress, item added, auth, session events; Handlebars templating; generic destination = our localhost daemon | ⭐ The **trigger backbone**: "user finished watching X" → archive-prompt flow; "user selected request item" → fetch |
| **Sessions API** (`/Sessions`, `/Sessions/{id}/Message`, `/Sessions/{id}/Command` `DisplayMessage`) | Server-initiated **popup messages on the playing client** — clients advertise `DisplayMessage` in `SupportedCommands` (web, Android TV, iOS support it; duration handling varies by client) | ⭐ The **in-client notify channel**: "✅ Inception is ready to play" with no phone involved |
| **Library scan API** (`/Library/Refresh`, per-folder refresh) | Tell Jellyfin a dummy was replaced by the real file (or vice-versa) immediately | ⭐ Required after every fetch/restore/archive |
| **Server plugin (C#/.NET)** | Custom scheduled tasks, item resolvers, metadata providers, **Media Segments**, virtual folders, config UI; full server-side power | ⭐⭐ The eventual "MediaVault for Jellyfin" plugin (apple_tv_ui_roadmap path, modernized) |
| **`.strm` files / virtual items** | Library items whose content resolves elsewhere | ⚠️ Useful but flawed — see §1.4 |
| **Web-client JS injection** (Jellyfin-Enhanced pattern) | Rich custom UI in the *web* client only | Secondary (TV clients don't load it) |

**The JellyBridge precedent (key validation).** `JellyBridge` bridges Jellyfin↔Jellyseerr by
materializing *discover/request placeholder items inside a normal Jellyfin library*, so browsing and
"requesting" happens **from any client, including Android TV and Kodi** — no separate app. This proves
the **placeholder-item-as-button** pattern that MediaVault needs for in-client *fetch requests* and
*archive prompts*: a tiny stub item whose selection/playback the daemon (via webhook `PlaybackStart`)
interprets as a command. MediaVault is actually *better* positioned than JellyBridge here, because our
archived items ALREADY exist as playable 10 KB dummies — "playing" a dummy **is** the fetch request.

### 1.3 The in-client interaction design space (decision #4 = in-client only)

1. **Fetch request** = user plays/selects the archived item's dummy. Daemon sees webhook
   `PlaybackStart` on an item whose path matches a known dummy → queue fetch → immediately
   `DisplayMessage` "⏳ Fetching Inception — I'll tell you when it's ready" (the dummy itself plays its
   2-second black frame and stops — acceptable; a nicer variant generates dummies that render a
   "FETCHING…" title card).
2. **Fetch-done notify** = `DisplayMessage` to the requesting session (or all active sessions) +
   library refresh so the tile flips to playable; optionally also mark it into a "Ready to Watch"
   collection (Home-row visibility — see §1.5).
3. **Archive prompt** = webhook `PlaybackStop` with `PlayedToCompletion` (or ≥ ~90% progress) →
   policy engine. In-client options, in increasing interactivity:
   - **a. Grace-period auto-archive + notify** (recommended default): "🗄️ Inception watched — will
     re-archive in 48 h unless you play it again / it's in 'Keep' collection." Fully in-client, zero new UI.
   - **b. Action-stub items**: a "MediaVault Actions" library holding tiny stub videos like
     "🗄️ Archive Inception now" / "📌 Keep Inception" — playing one = pressing the button (webhook sees
     it; daemon acts; stub disappears on next refresh). Works on EVERY client incl. Infuse and Kodi.
   - **c. Collections-as-checkboxes**: user adds the item to a "Keep" collection (native client UI) →
     daemon never auto-archives items in that collection.
4. **Progress visibility** = the daemon can update a "Fetching now" collection, and/or re-write the
   dummy's overview text ("38% fetched, ETA 12 min") + refresh — crude but visible on every client.

This stack needs **zero custom client code** — only the webhook plugin + Sessions/Collections/Refresh
APIs + our daemon. A real C# plugin upgrades polish later (custom segments, config page, virtual
status rows) per `apple_tv_ui_roadmap.md`'s Phase 1-3 (whose §5 dummy-detection must be redesigned:
dummies are now real video files; detect by size + `uid` sidecar / library API instead of the dead
`"Original Hash:"` text marker).

### 1.4 `.strm` reality check

`.strm` items resolve a URL/path at play time — the debrid world uses them heavily. BUT (verified in
issues): Jellyfin's proxying of external `.strm` URLs has **HTTP Range/seek gaps** (#13974), external
subtitles mis-path (#13976), and **Infuse fails to play Jellyfin `.strm` items** (#12306); Kodi has its
own local-path strm quirks (jellyfin-kodi #1084). Conclusion: for MediaVault, **placeholder-upgrade
(dummy → real file in place) beats `.strm`** as the library mechanism — it keeps every client's
direct-play path natural. `.strm` stays an option only for a future "stream-from-proxy" experiment
(Tier moonshot), ideally pointing at a **local** daemon URL (localhost proxy avoids the worst issues).

### 1.5 Netflix-ification plugin shelf (steal list, all free)

- **Home Screen Sections** (IAmParadox27) + **Collection Sections** — server-provided dynamic home
  rows ("Ready to Watch", "Because You Watched", per-mood rows) → our daemon can drive rows via
  collections. Web client (and ecosystem) oriented.
- **Editors-Choice / MonWUI slider** — full-width hero carousel.
- **Intro Skipper** (audio-fingerprint intro detection) + **Media Segments API** — Netflix skip
  buttons.
- **Native trickplay** — scrub previews (enable + let it index).
- **Jellyfin-Enhanced** — web-UI enhancement layer (shortcuts, Seerr auto-request, calendar).
- **Jellyseerr/Seerr (+ mobile app, TV support in progress)** — discovery/request manager; optional
  alongside the dummy-as-request pattern (MediaVault's vault IS the catalog, so Seerr matters only if
  the user later wants *new-content acquisition*, which is out of MediaVault's scope today).
- Themes (Ultrachromic etc.) for Apple-TV-like web look.

### 1.6 Hardware transcoding on the Alienware (Windows)

NVENC is the NVIDIA path on Windows (driver ≥ 522.25 for 10.10+); consumer GeForce = ~5 concurrent
session limit (plenty for 1-2 TVs); HDR/DV→SDR **tone mapping via CUDA works on Windows** (slightly
worse than Linux but fine at 1-2 streams). Action items: enable NVENC + tone mapping, untick unsupported
codecs, pin transcode temp to a fast SSD. Most living-room playback should **direct play** anyway
(Infuse/CoreELEC decode everything client-side); transcoding matters for phones/tablets/remote.

---

## 2. Emby delta (lifetime already owned — what it buys us)

- **What Premiere adds**: hardware transcoding, offline downloads, DVR, **Webhooks (Premiere-gated!)**,
  WebStreams, premium Cover Art. Note the irony: the webhook backbone MediaVault needs is *paid* on
  Emby, free on Jellyfin.
- **Plugin story**: C# SDK (dev.emby.media), catalog submission; active 2025-26 community plugins
  (Home Screen Companion, Segment Reporting, Watch Party, EmbyIcons…). Core server is **closed
  source** — deep integration debugging is harder; community is smaller than Jellyfin's
  (Jellyfin started as the Emby 3.5 fork when Emby closed up).
- **Kodi**: *Emby for Kodi Next Gen* v11 — mature, arguably smoother than jellyfin-kodi on some
  setups; works on the Ugoos/CoreELEC path too.
- **Infuse**: supports Emby exactly like Jellyfin.
- **Verdict**: Emby = a **fully viable fallback** with one paid feature gap closed by the owned
  lifetime key. If Jellyfin ever breaks for us, ~90% of the roadmap ports (webhooks/sessions/collections
  all have Emby equivalents). Worth keeping the server installed for A/B testing during Phase 0;
  **don't build primary automation against it** (closed core + smaller ecosystem).
  Bonus already banked: MediaVault's `FFMPEG_PATH` literally points at the Emby-bundled ffmpeg.

## 3. Plex delta (would need a new purchase — recommendation: skip)

- **Price**: lifetime pass jumped to **$749** (Apr 2025 increase; remote streaming paywalled for
  non-pass users). The "buy before next month's increase" window the user remembers has effectively
  already happened.
- **Integration surface**: server-side plugins are **dead** (deprecated years ago); webhooks exist
  (Pass) but are outbound-only; no item-resolver/virtual-item surface. The MediaVault flow (dummy
  intercept, in-client prompts, library-as-request-UI) is **impossible** to build properly on Plex —
  the 2026-05 `apple_tv_ui_roadmap.md` already rejected it for exactly this, and that analysis still
  holds, now with a worse price.
- **What you'd actually get for $749**: the most polished first-party clients on every platform,
  best-in-class remote streaming UX, Plexamp. None of it advances the couch-vault flow.
- **Verdict**: **do not buy** for this project. Re-evaluate only if a non-MediaVault need appears
  (e.g., sharing with remote family on dumb TVs). Infuse already gives Apple TV polish on Jellyfin.

## 4. Client matrix for YOUR hardware

| Device | Best client(s) | Notes |
|---|---|---|
| **Apple TV 4K** | **Infuse Pro** (codec king: direct-plays DTS/TrueHD *containers* via its decoder — but tvOS can NEVER bitstream TrueHD/DTS to the AVR, hardware limit; DV P5 ok, P7 falls back HDR10) + **Swiftfin** (free, native Jellyfin UI, 1.4 Jan-2026 overhaul, Jellyfin 10.11 support) | Install BOTH: Infuse for playback quality/scrubbing, Swiftfin for native UX. `DisplayMessage` popups: web/AndroidTV/iOS confirmed; verify on Swiftfin/Infuse during Phase 0 (Infuse may ignore them — the action-stub pattern still works there) |
| **Ugoos AM6B+ (projector)** | **CoreELEC + Kodi + Jellyfin for Kodi addon** (or Emby for Kodi NG) | The *only* consumer box doing **DV Profile 7 FEL + TrueHD Atmos bitstream** from local files — this is the reference-quality path for your 4K remuxes; 148-page AVS thread + dedicated guides exist. Kodi addon syncs watched state back to Jellyfin → webhooks still fire → archive flow works |
| Phones/tablets | Jellyfin official apps / Findroid / Swiftfin iOS | Also the emergency remote-admin surface (Jellyfin web) |
| Anywhere | Jellyfin web | Richest plugin UI surface (Home Sections etc.) |

## 5. Sources

- [JellyWatch — Jellyfin 10.10/10.11 upgrade guide](https://jellywatch.app/blog/jellyfin-10-10-10-11-upgrade-guide-new-features-2026) · [Jellyfin 10.10.0 release post](https://jellyfin.org/posts/jellyfin-release-10.10.0/) · [Media Segments docs](https://jellyfin.org/docs/general/server/metadata/media-segments/)
- [.strm range/seek issue #13974](https://github.com/jellyfin/jellyfin/issues/13974) · [.strm subtitle issue #13976](https://github.com/jellyfin/jellyfin/issues/13976) · [Infuse .strm issue #12306](https://github.com/jellyfin/jellyfin/issues/12306) · [jellyfin-kodi strm #1084](https://github.com/jellyfin/jellyfin-kodi/issues/1084) · [strm behavior discussion #11448](https://github.com/orgs/jellyfin/discussions/11448)
- [jellyfin-plugin-webhook](https://github.com/jellyfin/jellyfin-plugin-webhook) · [Webhook notifications docs](https://jellyfin.org/docs/general/server/notifications/) · [JellyHookDebouncer](https://github.com/rodrigocabraln/JellyHookDebouncer) · [DisplayMessage support — jellyfin-androidtv #3428](https://github.com/jellyfin/jellyfin-androidtv/issues/3428) / [#2782](https://github.com/jellyfin/jellyfin-androidtv/issues/2782)
- [JellyBridge (Jellyseerr-in-Jellyfin)](https://github.com/kinggeorges12/JellyBridge) · [Seerr mobile app](https://github.com/seerr-team/mobile-app) · [Jellyfin-Enhanced](https://github.com/n00bcodr/Jellyfin-Enhanced) · [Jellyseerr guide](https://www.rapidseedbox.com/blog/jellyseerr-guide)
- [Home Screen Sections](https://github.com/IAmParadox27/jellyfin-plugin-home-sections) · [Collection Sections](https://github.com/IAmParadox27/jellyfin-plugin-collection-sections) · [awesome-jellyfin](https://github.com/awesome-jellyfin/awesome-jellyfin) · [JellyWatch 60+ plugins guide](https://jellywatch.app/blog/awesome-jellyfin-plugins-complete-guide-2026)
- [JellyWatch — Jellyfin vs Plex vs Emby 2026](https://jellywatch.app/blog/jellyfin-vs-plex-2026-comparison) · [selfhosting.sh Jellyfin vs Emby](https://selfhosting.sh/compare/jellyfin-vs-emby/) · [Emby Premiere](https://emby.media/premiere.html) · [JellyWatch Emby ecosystem 2026](https://jellywatch.app/blog/awesome-emby-plugins-ecosystem-complete-guide-2026) · [Emby for Kodi Next Gen](https://github.com/MediaBrowser/plugin.video.emby)
- [JellyWatch — Infuse vs Swiftfin 2026](https://jellywatch.app/blog/infuse-jellyfin-apple-tv-ios-setup-2026) · [Swiftfin App Store](https://apps.apple.com/us/app/swiftfin/id1604098728)
- [Ugoos AM6B+ DV FEL AVS thread](https://www.avsforum.com/threads/ugoos-am6b-coreelec-and-dv-profile-7-fel-playback.3294526/) · [DV FEL + TrueHD holy-grail guide](https://tech.grahammiranda.com/the-holy-grail-a-definitive-guide-to-dolby-vision-fel-truehd-atmos-with-ugoos-coreelec-and-kodi/) · [JellyWatch 2026 device comparison](https://jellywatch.app/blog/best-streaming-device-jellyfin-4k-hdr-av1-2026) · [Schaka media-client-guide](https://github.com/Schaka/media-client-guide)
- [Jellyfin NVIDIA HWA docs](https://jellyfin.org/docs/general/post-install/transcoding/hardware-acceleration/nvidia/) · [JellyWatch transcoding hardware 2026](https://jellywatch.app/blog/jellyfin-hardware-transcoding-2026-comprehensive-guide)
