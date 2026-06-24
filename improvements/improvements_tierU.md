# Tier U — Couch UX & Clients (making the vault feel like Netflix)

> **Added 2026-06-12 (fable-review session).** Client-side and presentation-layer work on
> top of Tier S's plumbing: the polish that makes browsing/watching the vault from the
> Apple TV or the Ugoos projector feel like a first-party streaming service. Research
> grounding: `RESEARCH_MEDIA_SERVERS.md` (plugin shelf, client
> matrix, hardware paths) and the Netflix feature mapping in
> `RESEARCH_STORAGE_STREAMING.md` §4. Phasing: `ROADMAP_END_GOAL.md`.
>
> **Attribute key:** `Risk` = blast radius of MAKING the change. `If skipped` = the
> experience gap that remains, with a scenario.

---

## IMP-U1: Post-restore enrichment window (trickplay, chapters, intro-fingerprints BEFORE archive)

- Category: UX / integration
- Priority: high
- Files: `mvdaemon.py` hook (Tier S) + Jellyfin scheduled-task triggering; no main.py changes
- Current behavior: Jellyfin generates trickplay scrub-thumbnails, chapter images, and Intro-Skipper fingerprints only while a real file is local — and knows nothing about MediaVault's archive cycle. A title restored then re-archived before generation ran loses scrub previews and skip-intro forever (until next restore).
- Proposed change: The S4 archive flow gains a mandatory **enrichment gate**: before grace-expiry `replace` runs, the daemon (a) triggers a targeted Jellyfin refresh/scan for the item, (b) waits for trickplay + chapter images + Intro Skipper fingerprinting to complete for it (poll Jellyfin's task/items API), (c) only then archives. Generated artifacts live in Jellyfin's data dir (tiny — KB-to-MB per title) and persist across archive cycles, so every once-restored title scrubs and skip-intros forever — even while its bytes are in the cloud.
- Rationale: This is what makes archived items feel "real" on the couch: rich scrubbing and Netflix-style skip buttons on a library whose bytes are 95% remote. One-time cost per title, permanent payoff.
- Goal: Any title watched once via the vault flow has trickplay + skip-intro permanently; re-archive never costs polish.
- Effort estimate: small-medium (daemon sequencing + Jellyfin task polling)
- Risk: low — delays the auto-archive by minutes; no MediaVault core changes. Guard: a generation failure must not block archiving forever (timeout → archive anyway, log).
- If skipped: titles archived before generation ran scrub blind (no previews) and lose skip-intro; the library feels visibly second-class vs streaming services, undermining the whole UX goal.
- Status: pending

## IMP-U2: Status-driven collections & Netflix-style home rows

- Category: UX
- Priority: high
- Files: `mvdaemon.py` collection sync; Jellyfin Home Screen Sections + Collection Sections plugin config
- Current behavior: The Jellyfin home screen shows generic recently-added rows; vault state (fetching / ready / leaving-soon) is invisible.
- Proposed change: The daemon maintains status collections — **"⏳ Fetching now"**, **"✅ Ready to watch"** (fetched, unwatched), **"🗄️ Leaving local soon"** (in grace), **"📌 Kept"**, optionally **"⚠️ Needs attention"** — and Home Screen Sections surfaces them as rows. Add curated rows from library data: "Recently vaulted", per-language rows (en/ta/hi/ja...), "Big premieres" (largest 4K remuxes). Collections are visible in ALL clients (rows render natively in web; as collections elsewhere).
- Rationale: This is the operations dashboard IN the TV UI — the in-client-only answer to "what's my vault doing?" — plus the Netflix-style merchandising rows that make browsing pleasant.
- Goal: Opening Jellyfin on any client immediately shows what's ready, what's coming, and what's leaving — no PC, no web dashboard required.
- Effort estimate: small-medium (on top of S2's collection client)
- Risk: low — collections are additive metadata; worst case is row clutter (make each row toggleable in daemon config).
- If skipped: vault state lives only in DisplayMessage popups (ephemeral) and the ops web UI (PC-side) — the couch user can't answer "did my fetch finish?" by glancing at the home screen.
- Status: pending

## IMP-U3: NFO + artwork pipeline (rich presentation for every entry, even dummies)

- Category: UX / metadata
- Priority: high (delivery vehicle for IMP-E3's Jellyfin-facing half)
- Files: extends IMP-E3's enrichment (`enrich_metadata`) with NFO emission; `set_poster`/`set_fanart` bulk mode
- Current behavior: Jellyfin identifies titles by parsing release-style filenames — decent for mainstream movies, weak for anime absolute numbering and regional titles (`mov-ta-2024-maharaja`); `metadata.title` is the raw slug; most folders lack poster/fanart.
- Proposed change: After IMP-E3's API lookups, write **Kodi/Jellyfin NFO files** (`movie.nfo`, `tvshow.nfo`, per-episode NFOs) + `poster.jpg`/`fanart.jpg` into each media folder (Jellyfin's local-metadata readers treat these as authoritative). Backfill command for the existing ~570 entries; hook into prep for new ones. Special care: combined-episode files get NFOs naming BOTH episodes; anime NFOs carry AniDB/AniList ids so Jellyfin's ordering matches the vault's absolute numbering.
- Rationale: Presentation quality is decided here — with NFOs+art, even a 10 KB dummy renders like a Netflix tile (poster, synopsis, rating); without them, the anime third of the library is a wall of misidentified slugs.
- Goal: 100% of entries render with correct title/poster/synopsis in Jellyfin on first scan, dummy or real.
- Effort estimate: medium (after E3's API layer exists)
- Risk: low-medium — writes new files into media folders (NFO/JPG are inert to MediaVault's scanners — non-video extensions); ID-mismatch risk (wrong TMDB match → wrong poster) mitigated by the curated manual-id → lookup mapping + a review-diff mode.
- If skipped: Jellyfin's own scrapers carry the load — fine for English movies, visibly wrong for anime/regional content; the "browse all the movies, series, anime" half of the end goal looks broken for exactly the harder thirds.
- Status: in_progress — **NFO/artwork down-payment delivered** on `feature/imp_e3_u3_d17_tmdb_posters_rename` (2026-06-24): `enrich_metadata --nfo` writes `movie.nfo`/`tvshow.nfo` (title/year/plot/rating/`<uniqueid type="tmdb">`); `poster.jpg`/`fanart.jpg` auto-downloaded per show/season (never overwrites locals); `/api/media-image/{id}` + `resolve_artwork_path` serve artwork to the web UI SPA. **Remaining:** per-episode NFOs; combined-episode NFOs (naming both episodes); AniDB/AniList ids in anime NFOs; full backfill pipeline with review-diff mode; `set_poster`/`set_fanart` bulk mode.

## IMP-U4: Reference-quality playback paths (Ugoos DV-FEL + Apple TV guidance, recorded)

- Category: UX / documentation + configuration
- Priority: medium
- Files: `docs/` (CLIENT_MATRIX.md from S1 extended into a per-content-type playback guide); CoreELEC/Kodi + Infuse/Swiftfin settings
- Current behavior: The user owns the *only* consumer box that does DV Profile 7 FEL + TrueHD Atmos bitstream (Ugoos AM6B+ w/ CoreELEC) and an Apple TV (which can never bitstream TrueHD — hardware limit). Which device/client to use for which file is tribal knowledge.
- Proposed change: Produce the definitive per-content-type playback map and apply the settings: 4K DV-FEL remuxes + TrueHD/Atmos → Ugoos via Jellyfin-for-Kodi (add-on mode, settings per the AVS/holy-grail guides); DV P5/HDR10 streaming-style content → Apple TV (Infuse for codec breadth, Swiftfin for native UX); phones/web → transcode path (NVENC + tone-mapping verified). Encode the mapping into the library where useful (e.g., a `playback_hint` derived from tech_spec.hdr/audio at prep time, surfaced in the item overview or a collection like "▶️ Best on projector").
- Rationale: The vault stores reference-grade rips; the end-to-end goal includes playing them at reference grade. The hardware is already owned — this task is the configuration + knowledge capture that guarantees the right pixels/bits reach the screen.
- Goal: For any title, the user (or the overview text itself) knows the optimal device; DV-FEL content verifiably plays with FEL active and TrueHD bitstreamed on the projector path.
- Effort estimate: small-medium (mostly testing + docs; tiny code if playback_hint is added)
- Risk: low — client settings + docs; the optional playback_hint is an additive metadata field.
- If skipped: quality outcomes stay device-luck — a DV-FEL remux watched on Apple TV silently plays as HDR10 with lossy audio, defeating the point of archiving remuxes.
- Status: pending

## IMP-U5: MediaVault Jellyfin plugin (the polish phase — C# "vault-aware" server plugin)

- Category: UX / integration (the apple_tv_ui_roadmap.md successor, corrected)
- Priority: medium (LAST — only after S1-S5 prove the daemon flow)
- Files: new separate plugin repo (jellyfin-plugin-template based); MediaVault side: daemon API consumed by the plugin
- Current behavior: After Tier S, the flow works via conventions (dummy-play = request, collections = status). Remaining rough edges only a server plugin can fix: dummies report absurd probed runtime (2 s); request/archive interactions are convention-based rather than explicit UI; vault status isn't a first-class item property.
- Proposed change: A C#/.NET Jellyfin plugin (per IMP-G4's graduated direction + `apple_tv_ui_roadmap.md` Phases 1-3, **with the §5 correction**: detect archived items by size < 200 KB + `uid` sidecar / daemon API — the `"Original Hash:"` text marker died with the video-dummy feature):
  - Metadata override: archived items show library-true runtime/resolution (from `tech_spec`) instead of the dummy's probe.
  - Item badges/custom property for vault state (Archived ☁️ / Fetching ⏳ / Local 🟢) rendered at least in the web client.
  - Optional Media Segments emission from MediaVault data; config page pointing at the daemon.
  - Explicit "Restore" UI where the client surface allows it (web first; TV clients keep the dummy-play convention).
- Rationale: Converts the convention-based flow into first-class UI where the platform permits — the final 10% of polish.
- Goal: Vault state visible as proper UI affordances; dummy items indistinguishable from real ones in the browse experience.
- Effort estimate: large (C# learning curve + plugin ABI churn)
- Risk: medium — separate component (server plugin) with version coupling to Jellyfin releases; zero risk to MediaVault core. Pin to an LTS Jellyfin line; keep the daemon flow as the always-working fallback.
- If skipped: the experience stays at "S-tier conventions" — fully functional, slightly visible seams (2-second runtimes on archived tiles, request-by-playing-a-dummy). Perfectly acceptable to skip until the daemon flow has months of mileage.
- Status: pending
