# Research — Google Photos Constraints, Streaming Feasibility & OSS Steal List

Researched 2026-06-12 (fable-review session). This doc answers the user's direct question — **"Can I
do streaming playback on the fly?"** — with evidence, and catalogs what we should steal from the
open-source ecosystem. Constraint honored throughout: **the 4× Pixel 1 XL unlimited original-quality
upload path is sacred** and remains the write side of the vault.

---

## 1. Google Photos in 2026 — what is and isn't possible

### 1.1 The March 31, 2025 Library API lockdown (verified)

Google removed the `photoslibrary`, `photoslibrary.readonly`, and `photoslibrary.sharing` scopes.
Since then an app can only touch **content it uploaded itself**; everything else returns
`403 PERMISSION_DENIED`. The sanctioned alternative, the **Picker API**, requires the *user* to
hand-pick items in a Google-rendered picker each time — useless for automation. Consequences:

- ❌ **No official API path** to programmatically list/download the Pixel-app-uploaded vault. Ever
  (under current policy). This kills "replace Selenium with the official API" (ARCHITECTURE §17.10 —
  now formally a dead end).
- ❌ rclone's native gphotos backend can no longer read non-rclone-uploaded media; **also note** the
  long-standing rclone caveat that API-downloaded videos were transcoded anyway — originals were
  *never* available via the official API.
- ✅ **The web UI session remains fully capable** (it's a first-party surface): search, Shift+D
  "download original", batch downloads. This is exactly what `mainfetch.py` automates today — the
  Selenium approach was accidentally the *most future-proof* choice.
- ✅ **Takeout** still exports originals in bulk (slow, whole-library granularity, async) — fine as a
  disaster-recovery escape hatch, not for on-demand.

### 1.2 Tools that still work (because they ride the browser session)

| Tool | How it works | Relevance |
|---|---|---|
| **rclone `gphotosdl`** (official rclone org) | A headless-browser **local proxy**: rclone asks `http://localhost:8282/id/<photoID>`, gphotosdl drives a logged-in Chrome to trigger the *original-quality* download and **streams it through** | ⭐⭐ IMP-G2's evaluation target. Two prizes: (a) rclone-grade robustness patterns for our fetcher; (b) proof that a **local HTTP proxy that streams a Google-Photos original WHILE it downloads** is workable — the seed of quasi-streaming (§2) |
| **gphotos-cdp** (+ maintained forks) | Chrome DevTools Protocol automation for bulk original-quality export incl. metadata | Pattern source for hardening `mainfetch` (CDP > keystroke simulation: deterministic selectors, download events instead of folder polling) |
| **MediaVault `mainfetch.py`** | Selenium keystrokes + Downloads-folder hash harvester | Works today; fragile selectors; the two tools above show the upgrade path |

### 1.3 Risk register for the storage backend

- **ToS gray zone**: file-in-video cold storage via Pixel unlimited uploads has survived for years, but
  Google could (a) end the Pixel-1 unlimited grandfather, (b) add web-automation bot detection,
  (c) rate-limit bulk downloads. Mitigations: keep originals' hashes + mvmeta sidecars (done), keep
  the 4-phone fleet (account diversification), track IMP-F9 (multi-cloud abstraction) as the strategic
  hedge, and consider IMP-F3 (erasure coding across accounts) so no single account loss is fatal.
- **Bot-detection**: `undetected-chromedriver` is already in requirements (unused) — a ready lever if
  Google starts flagging the Selenium profile.
- **Single-machine session fragility**: Chrome profile cookie expiry silently breaks fetch (IMP-C6
  already tracks early expiry detection — raise its priority; a daemon multiplies the impact).

---

## 2. "Can I do streaming playback on the fly?" — the tiered verdict

**Short answer: true instant Netflix-style streaming straight from Google Photos is a moonshot
(blocked by platform constraints), but a *fetch-while-watching* experience that FEELS on-demand is
achievable in stages:**

| Tier | Experience | Mechanism | Feasibility |
|---|---|---|---|
| **T0 (today)** | Select → wait for full fetch+restore → play | Current CLI, manually | ✅ shipped |
| **T1: Couch-triggered background fetch** | Select on TV → background fetch+restore → in-client "ready" popup → play. Wait ≈ download time, but you never leave the couch | Daemon + webhook trigger + Sessions message + library refresh (RESEARCH_MEDIA_SERVERS §1.3) | ✅ **High — the roadmap centerpiece (no new storage tech needed)** |
| **T2: Watch-while-fetching (split files)** | Start watching ~minutes in: chunk 1 of a split file is **already a valid playable MKV** (mkvmerge split parts are independently playable Matroska segments). Daemon fetches chunk 1 first, exposes it (or a growing pre-merge) while chunks 2..N stream down; full deterministic merge + verify happens after, in place | Ordered chunk fetch (already how mainfetch queues), serve part-1 early; later parts via Kodi playlist hand-off or a growing-file remux experiment | 🟡 **Medium — needs experimentation** (client behavior on growing/segmented files varies; verify-or-bless still runs at the end so integrity is preserved) |
| **T3: Proxy-streaming originals** | Press play → local daemon proxies the Google-Photos original download as a progressive HTTP stream (gphotosdl pattern) → Jellyfin `.strm`/proxy item plays it with ~seconds of buffer; no full pre-fetch | gphotosdl-style headless-browser proxy + local HTTP range-handling + `.strm`-to-localhost items | 🟠 **Low-medium — moonshot.** Single-file titles only (multi-chunk needs T2 logic anyway); seek = re-trigger download or clever range caching; Google could break it any day; `.strm` client quirks (Infuse #12306). Worth a spike AFTER T1/T2; never the primary path |
| **T4: Direct cloud streaming (no PC)** | TV streams from Google straight | — | ❌ **Blocked**: no API; web-player streams are transcoded (not your original bytes); would bypass integrity layer entirely. Tracked as a "1%" idea only if Google ever ships a usable API again |

Two structural notes that make T1 feel far better than it sounds:
- **Smart prefetch kills most waits** (Netflix "smart downloads" pattern): when the user watches
  episode N of a season, the daemon auto-fetches N+1 (and N+2) in the background and auto-archives
  N−2 after the grace period. Sequential binge = zero perceived wait from episode 2 onward.
- **Parallel fetch lanes**: mainfetch already fires all chunk triggers in parallel per entry; a daemon
  can additionally parallelize across the two Chrome profiles (movies + TV accounts) — and the 4-Pixel
  topology question (§5) may unlock more accounts/lanes.

## 3. OSS landscape — architecture patterns worth stealing

| Project / stack | The steal |
|---|---|
| **Zurg + rclone + symlinks (debrid stacks: DUMB, riven, cli_debrid, plex_debrid)** | The *virtual library* model: media server sees a normal folder; a resolver service materializes bytes on demand. MediaVault's analog: dummies as virtual items + daemon as resolver. Also their hard lesson: keep the resolver's state machine (requested → fetching → ready → watched → archived) explicit and crash-safe — we already have RollbackJournal discipline to extend |
| **Sonarr/Radarr (*arr) architecture** | Queue + worker + per-item state + retry/backoff + event webhooks + REST status — the daemonization blueprint for `main.py`'s CLI verbs. Also their "import after verification" gate = our verify-or-bless |
| **JellyBridge** | In-library request placeholders (validated §1.2 of media-servers doc) |
| **Jellyseerr/Seerr** | Request lifecycle UX (request → approved → available + notifications) — vocabulary to mirror in our daemon's states |
| **gphotosdl** | Local streaming proxy over a logged-in browser (T3 seed; robustness patterns for fetch) |
| **gphotos-cdp** | CDP eventing instead of keystrokes + folder polling (fetch hardening) |
| **rclone chunker/crypt** | Already mined for G1 (.partial+rename). crypt = IMP-F1's reference design |
| **Intro Skipper / Media Segments** | Netflix skip UX, free |
| **JellyHookDebouncer** | Webhook noise-filtering (PlaybackStop fires often; debounce before acting on archive prompts) |

## 4. Netflix/streaming-service features mapped onto MediaVault

| Netflix feature | MediaVault translation | Cost |
|---|---|---|
| Smart downloads (download next ep, delete watched) | **Smart prefetch + grace-period auto-archive** (§2) — the single highest-leverage feature in the whole roadmap | Daemon policy logic only |
| Continue Watching / Next Up | Native Jellyfin | Free |
| Skip intro/credits | Intro Skipper plugin + Media Segments API | Free |
| Trickplay scrub previews | Native (10.10+, fast) — generate at restore time, BEFORE archive, store thumbnails locally forever (tiny) so even archived items scrub-preview | Config + daemon hook |
| "Are you still watching?" | Inverted: our **archive grace prompt** after completion | Daemon policy |
| Top 10 / personalized rows | Home Screen Sections + collections driven by daemon ("Ready to watch", "Fetched this week", "Leaving local soon") | Plugin + daemon |
| Instant start | T2/T3 (§2); pragmatically: smart prefetch | Varies |
| Profiles | Native Jellyfin users | Free |
| Offline downloads | Infuse/Swiftfin/Jellyfin app downloads of *local* items (only matters pre-archive) | Free |
| Beautiful artwork/metadata | IMP-E3 (TMDB/TVDB/AniDB enrichment) feeding Jellyfin NFOs/posters — replaces slug-titles with real metadata | Tracked (E3) |

## 5. Open question for the user (topology) — carried from REVIEW_NOTES E1

Code knows **2** ADB serials (`movies`, `series`) and **2** Chrome profiles (2 Google accounts), but
the brief says **4× Pixel 1 XL in parallel**. Clarify: 4 phones × 1 account? 2×2? spares? This decides
how many parallel upload AND fetch lanes the daemon can schedule, and whether erasure-coding across
accounts (IMP-F3) has 2 or 4 shards to play with.

## 6. Sources

- [Google Photos API updates (official)](https://developers.google.com/photos/support/updates) · [Picker API launch blog](https://developers.googleblog.com/en/google-photos-picker-api-launch-and-library-api-updates/) · [gphotos-sync #511](https://github.com/gilesknap/gphotos-sync/issues/511) · [memoryKPR deprecation analysis](https://memorykpr.com/blog/google-photos-api-deprecation-what-it-means-for-third-party-apps-and-how-to-prepare/) · [Snapwood FAQ](https://www.snapwoodapps.com/google2025/)
- [rclone gphotosdl](https://github.com/rclone/gphotosdl) · [rclone Google Photos docs](https://rclone.org/googlephotos/) · [rclone forum: syncing after the lockdown](https://forum.rclone.org/t/has-anyone-found-a-clever-way-to-sync-with-google-photos-since-you-cant-use-rclone-to-download-at-full-quality/35482) · [gphotos-cdp image](https://hub.docker.com/r/davidecavestro/gphotos-cdp)
- [Zurg](https://github.com/debridmediamanager/zurg-testing) · [ElfHosted RD+Jellyfin guide](https://docs.elfhosted.com/guides/media/stream-from-real-debrid-with-jellyfin-radarr-sonarr-prowlarr/) · [DUMB guide](https://corelab.tech/ultimate-plex-debrid-guide/) · [Sailarr's guide](https://savvyguides.wiki/sailarrsguide/)
- Media-server-side sources: see `RESEARCH_MEDIA_SERVERS.md` §5.
