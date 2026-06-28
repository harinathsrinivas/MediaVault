# MediaVault — Best Practices & Compounding Decisions

> **What this file is:** the decisions you make *today* (while the vault is still small and
> hand-driven) that will be cheap to get right now and brutally expensive to fix once the library is
> large and the daemon (Tier S) is automating everything. Each entry states the decision, **why it
> compounds**, the recommendation, and where it's tracked.
>
> Created 2026-06-21 from a chunk-sizing question that opened into "find every decision like this."
> This is advisory, not a spec — read it, decide, and fold conclusions back into the relevant
> `improvements/` tier file + `PRIORITY.md` (the keep-current rule in `CLAUDE.md`).
>
> **Orientation:** `ARCHITECTURE.md` (engineering reference) · `improvements/ROADMAP_END_GOAL.md`
> (where we're going) · `improvements/RESEARCH_STORAGE_STREAMING.md` (the streaming/Google facts).

---

## 0. The measured facts this is built on (2026-06-21)

| Quantity | Value | Source / note |
|---|---|---|
| Link download | **567.74 Mbps ≈ 71 MB/s** | Speedtest (lowercase `b` = megabits) |
| Link upload | **145.3 Mbps ≈ 18 MB/s** | Speedtest — the **slower side**; gates archive/push time |
| Effective Google Photos per-file download | **~40 MB/s (suspected, UNVERIFIED)** | likely the origin of the earlier "40 mb ps" figure; *this*, not Speedtest, governs fetch & fly-streaming. **Action: measure it** (§E-Note) |
| Google Photos hard cap | **10 GB per file** | requires chunking any file above it |
| 4K UHD remux playback bitrate | **~60–120 Mbps (≈8–15 MB/s)**, peaks ~128 Mbps | the number a stream must beat to not stall |
| `split_video_file` unit | **GiB (1024³)** | `push <id> N gb` → N×1024³ ceiling, *not* N×10⁹ — matters for the cap margin |

**The one structural truth:** chunk size is set by the **Google 10 GB cap + file-count sanity**, both
*independent of speed*. Download speed only changes *time-to-first-frame* and whether *fly-streaming
sustains* — never the right chunk size. Don't tune chunks for streaming.

---

## A. 🔴 Irreversible-at-scale — fixing later means re-uploading the whole vault

These are the expensive ones. At **18 MB/s upload**, re-pushing 100 GB takes ~95 min; re-pushing a
~5 TB vault is **days of uninterrupted upload** plus phone-storage churn. Get these right *before* the
library grows and *before* you delete local originals.

### A1. Standard chunk size = **8 GB ceiling** — LOCK THIS
- **Decision:** `python main.py push <id> 8 gb` as the default for everything that needs splitting.
- **Why 8, not 9 or 10:** the splitter uses GiB. `10 gb` = 10.7 GB decimal → **exceeds Google's 10 GB
  cap** (corrupt/blocked upload). `9 gb` = 9.66 GB decimal → only ~3% margin (mkvmerge keyframe
  overshoot + the +10 MB buffer at `main.py:226` can eat it). `8 gb` = 8.59 GB decimal → **~14% safety
  margin** and still single-to-low-double-digit chunk counts.
- **Resulting chunk counts** (balanced splitter divides into equal sub-cap chunks):
  100 GB → 13 · 80 GB → 10 · 20 GB ep → 3 · 15 GB ep → 2 · 1080p ep (2–5 GB) → **1 (no split)**.
- **Why it compounds:** a chunk-size change is not editable in place — it means re-download +
  re-split + re-upload **every split title**. Picking a tiny size (e.g. 500 MB → 200 files for a
  100 GB movie) creates the operational nightmare (200 Google items to locate/hash/merge per title);
  picking too large risks the cap. 8 GB is the sweet spot.
- **Note:** chunk count is NOT tile count — `split_info` groups all chunks under one library entry, so
  the UI always shows one tile per title. The "hundreds of files" risk is operational overhead, not
  search breakage.

### A2. Verify Dolby Vision / HDR / lossless-audio survive split→merge — **BEFORE mass-archiving any 4K**
- **Decision:** before you archive your first batch of 4K remuxes, run ONE round-trip test on a real
  **DV Profile 7 FEL** remux: `push` (split) → `restore` (deterministic re-merge) → compare.
- **Why it compounds:** the entire point of archiving remuxes is **reference quality** (IMP-U4 — the
  Ugoos AM6B+ is your only box that does DV P7 FEL + TrueHD Atmos bitstream). mkvmerge stream-copies
  (no transcode), so DV/HDR/audio *should* pass through — **but this must be proven, not assumed.** If
  split/merge silently drops the FEL enhancement layer, the DV RPU, HDR10+ dynamic metadata, a TrueHD
  Atmos track, or chapter/track-language tags, you won't notice until you watch an archived title a
  year later as flat HDR10 with lossy audio — and by then the originals are gone.
- **How to verify:** `mediainfo` the original vs the re-merged file and diff the **elementary stream
  layer** (codec, DV config/profile, HDR metadata, every audio track + language, subtitle tracks,
  chapters). Container muxing metadata *will* differ (that's expected — see A3); the streams must not.
  Confirm DV-FEL actually plays with FEL active on the Ugoos. Tools: `mediainfo`, `dovi_tool info`.
- **If it fails:** that's a blocker to fix in the split/merge path before any 4K archive — surface it.
- **Tracked as:** IMP-U4 (playback paths); this verification is a prerequisite, add it to PRIORITY if
  not already a gate.

### A3. Container canonicalization to MKV is permanent — accept it explicitly
- **Decision (already made, PR #20):** split files are re-merged **deterministically** and the
  canonical hash is the re-merge hash, *not* the original file's hash. Source MP4/other → MKV chunks →
  MKV on restore.
- **Why it compounds:** you can **never** recover the exact original-container bytes of a split title;
  only the canonical MKV re-merge. This is fine (streams are preserved, see A2) but it's a one-way
  door — confirm you're content with "stream-faithful MKV" as the archival format for everything.
- **Tracked as:** `docs/feature-split-hash-deterministic/`; memory `feedback_mkvmerge_hash_divergence`.

### A4. Multi-account replication policy — DECIDE BEFORE THE LIBRARY SCALES (the biggest bite)
- **Decision needed:** every chunk should live in **≥2 Google accounts**, each via a Pixel signed into
  that account (so every replica stays on the free-unlimited original-quality path — a *saved share*
  copy does NOT, see Tier X §0). Decide the replica count + which accounts now.
- **Why it compounds — hard:** today each title lives in exactly ONE account (mov→movies, tv→series,
  ani→anime). A **single CSAM-AI false-positive ban** (documented Feb-2026 wave: instant, no recourse)
  deletes ~1/3 of the vault with **no recovery path**. Retrofitting replication across an
  already-large library = re-uploading the *entire vault* a second time at 18 MB/s. Doing it
  **at archive time, going forward** is nearly free; doing it later is days of upload.
- **Recommendation:** even before IMP-X1 ships, start pushing new archives to a second account
  manually, OR consciously accept the single-account risk for now and **prioritize X1 before the
  daemon auto-archives at scale.**
- **Tracked as:** IMP-X1 (replication), X2 (topology + runbook), X4 (self-heal), X5 (ban canary).

### A5. Do NOT delete a local original until its remote copy is verified AND replicated
- **Decision:** the `replace`→dummy step is a **point of no return**. Treat "delete the only
  full-quality original" as gated on: (a) round-trip hash verified, and (b) ≥2-account replication
  present (A4). Until X1 exists, at minimum require (a) and keep a tickbox awareness of the
  single-account risk.
- **Why it compounds:** once the original is a 200 KB dummy and the sole cloud copy is single-account
  / unverified / quality-degraded (A2), the title is **gone**. This is the irreversible action that
  every other decision in section A protects.
- **Tracked as:** verify-or-bless integrity gate; auto-rollback PONR discipline; IMP-X1.

### A6. Encryption / anti-scanning — decide the posture now (re-upload to retrofit)
- **Decision needed:** will archives be plain video or **encrypted-payload-in-MKV** (X3, anti
  copyright-hash + anti CSAM-AI)? If you'll ever want encryption, going encrypted-from-the-start avoids
  re-uploading the whole vault later.
- **Why it compounds:** switching plain → encrypted later = re-wrap + re-upload everything. Also
  introduces a NEW catastrophic failure mode (**key loss = permanent data loss**) that needs its own
  backup discipline.
- **Recommendation:** treat as **gated** — it requires the mandatory feasibility spike (does the Pixel
  uploader accept a 2-sec-video / multi-GB-attachment MKV and round-trip it byte-exact?). Don't adopt
  encryption-by-default until the spike passes; but **make the conscious choice now** so you're not
  surprised by a re-upload bill later.
- **Tracked as:** IMP-X3 (change-gated); blocker A5 in `BLOCKERS_AND_MOONSHOTS.md`.

### A7. Archive the full remux, never a re-encode
- **Decision:** archive the highest-quality master you have; never let a transcode/compress step sit
  upstream of the push.
- **Why it compounds:** once the original is deleted (A5), the archived quality is the ceiling forever.
  Google Photos *Original Quality* preserves bytes exactly (proven by the deterministic-hash
  round-trip), so there's no quality reason to pre-shrink — and every reason not to.

---

## B. 🟡 Expensive-to-retrofit — re-processing, but mostly local (no full re-upload)

### B1. Enrich BEFORE archiving — bake it into the prep pipeline now
- **Decision:** generate trickplay thumbnails, chapters, intro/credit fingerprints, and extracted
  subtitles **post-restore but BEFORE re-archive**, and store the tiny artifacts locally forever so
  even archived (dummy) titles scrub-preview and skip-intro.
- **Why it compounds:** if you archive without enrichment, adding it later means a
  **fetch → enrich → re-archive** cycle for *every* title — i.e. re-downloading and re-uploading the
  whole vault (A4-scale pain). Enriching at archive time is nearly free.
- **Tracked as:** IMP-U1 (enrichment-before-archive), IMP-E1 (subtitle pre-extraction), IMP-E3/U3
  (metadata/NFO/art).

### B2. Subtitle pre-extraction before `replace` destroys the original
- **Decision:** before `cmd_replace` deletes the original, `mkvextract` every subtitle track to disk.
- **Why it compounds:** `cmd_replace` currently deletes the original and all embedded subs with it;
  recovering subs later requires a full restore. Extracting at archive time is cheap and permanent.
- **Tracked as:** IMP-E1.

### B3. ID scheme, chunk naming, and prefix→account routing are load-bearing — freeze them
- **Decision:** keep the ID prefix map stable: `mov-*` → movies acct/profile, `tv-*` → series,
  `ani-*` → anime (data-driven `ID_PREFIX_PROFILE` / `profile_for_id()`). Keep the chunk naming
  convention `Name [short_id].chunk.NNN.mkv` and the `[short_id]` UID tag stable.
- **Why it compounds:** changing a prefix or the ID/naming scheme later breaks fetch routing AND every
  `.mvmeta.json` sidecar and dummy already on disk. Adding *new* prefixes is safe; mutating existing
  ones is not.
- **Tracked as:** IMP-C16 (anime profile routing); `ENTRY_TYPE_KEYS` registry + guard test.

### B4. Entry-schema changes must stay alias/iterator-safe
- **Decision:** any new library entry type or shared field updates `ENTRY_TYPE_KEYS`, and every
  whole-library iterator either calls `_resolve_alias` or skips `type == "multi_ep_alias"` entries.
- **Why it compounds:** this exact omission shipped live crashes (IMP-C12/C13) that the daemon would
  multiply across the whole library. The guard test (`tests/test_entry_schema_guard.py`) exists to
  catch it — keep it green.
- **Tracked as:** IMP-H3 (smoke gate + guard); CLAUDE.md cross-command integrity section.

### B5. Decide the `C:\Media` folder layout before populating Jellyfin
- **Decision:** fix the library structure (Movies / Series / Anime trees, naming, season folders) per
  `JELLYFIN_SETUP_GUIDE.md` **before** mass-creating dummies and pointing Jellyfin at it.
- **Why it compounds:** Jellyfin scraping, the dummy tiles, and library entry `folder_path`s all
  depend on the on-disk structure. Reorganizing after ~570 entries exist means rewriting paths in the
  library JSON, regenerating dummies, and re-scanning — error-prone at scale.
- **Tracked as:** IMP-S1 (Phase 0 foundation), IMP-U3.

### B6. `.mvmeta.json` sidecar completeness + library-JSON backup = your disaster recovery
- **Decision:** ensure the remote `.mvmeta.json` sidecar carries **enough to rebuild the library from
  the cloud alone** (manual_id, short_id, base_filename, original_hash, split method/val, per-chunk
  filename+hash). Separately, **back up `library_*.json` to git** on every change.
- **Why it compounds:** the chunks are the source of truth, but if you lose `library_*.json` and the
  sidecars are thin, you can't map dummies → chunks → titles. Decide the sidecar schema *before* it's
  written across thousands of chunks (schema-versioned via `MVMETA_SCHEMA_VERSION`, so additions are
  possible but a backfill is work).
- **Tracked as:** `write_mvmeta` (`main.py`), IMP-F5 (library-JSON git backup).

### B7. Archiving sports / "Others" videos — the IMP-D18 conventions
- **Decision (shipped, IMP-D18):** sports (and, later, documentaries) live in a
  4th **Others** category — `oth-` prefix, `C:\Media\library_others.json`, media
  under `C:\Media\Sports\<Sport>\<Competition>\<Edition>\`. The category→subdir
  map (`CATEGORY_ROOTS`) is **list-capable**, so `Documentary` can be added as a
  sibling root later with a one-line edit and no walker code change.
- **Folder + id scheme:** a tournament edition is ONE season. Base id =
  `oth-<sport>-<year>-<competition>-s01`, episodes `…-s01e01..eNN`; sport and
  competition spelled out (`football`, `cricket`). Each match-half is an episode;
  a match is two adjacent episodes (`e01`+`e02`). `prep_season` numbers files by
  **filename sort order**, so **name the halves so they sort in play order**
  (`First`<`Second`, or `1`<`2`, or `Q1`..`Q4`). A mis-sorting name mis-numbers
  the episodes — an editing concern, not a crash.
- **Why it compounds (B3 sibling):** the `oth-`→Others-account/profile routing and
  the id/naming scheme become load-bearing the moment chunks, `.mvmeta` sidecars,
  and dummies are written. Adding a *new* prefix is safe; mutating existing `oth-`
  ids later breaks fetch routing and every sidecar already on disk. Freeze the
  scheme before mass-archiving — and reuse the existing `season_map` + leaf model
  (no new entry type, no rollback-contract change).
- **Media-server presentation:** point an **"Other Videos" (Plex) / "Home Videos"
  (Jellyfin/Emby)** library at `C:\Media\Sports` — **filename-as-title, no online
  metadata scraper** (sports isn't on TMDB; MediaVault auto-captures the exact
  tech spec into `library_others.json`). Enrichment (`enrich_metadata` /
  `refresh_online` / `fetch_trivia`) deliberately **skips** `oth-` entries, so no
  scraper can mis-tag or rename the Sports folders. See
  `improvements/JELLYFIN_SETUP_GUIDE.md` §3.4.
- **Tracked as:** IMP-D18 (shipped); follow-ons IMP-X1 (replicate Others to a 2nd
  account) and OD-2 (an optional sports scraper, e.g. TheSportsDB) stay open.

---

## C. 🟢 Fix-before-automating — operational gates the daemon depends on

These don't corrupt stored data, but if the **daemon (Tier S)** starts making destructive decisions on
weak foundations, it amplifies every gap. Land these before unattended automation runs long.

| # | Gate | Why it must precede automation | Tracked as |
|---|---|---|---|
| C1 | **Durable watch-state** | Smart prefetch/auto-archive decide what to fetch/delete from watch state. If it's not durable, automation archives unwatched titles or fetches wrong ones. | IMP-E4 (before S5/S4) |
| C2 | **Session-expiry detection** | A silently-expired Chrome session makes every fetch fail; a daemon multiplies the silent failures. (Keep-alive partly done in C17.) | IMP-C6 / C17 |
| C3 | **Grace periods + Keep overrides + dry-run** on every destructive loop (S4 auto-replace, E5 phone deletion) | Automation deletes irreversibly; these are the brakes. Decide policy before the loop runs unattended. | IMP-S4, IMP-E5 |
| C4 | **Phone local-copy cleanup** | Pixel auto-upload never cleans `/sdcard/Media/`; phones fill and the upload pipeline stalls mid-binge. | IMP-E5 |
| C5 | **Per-account quota/bandwidth telemetry** | Know an account is near a limit / behaving oddly *before* automation hammers it (also an early ban signal). | IMP-E6, IMP-X5 |
| C6 | **Rollback change-gate respected** | The auto-rollback journal/PONR machinery is load-bearing; the daemon calls the same CLI verbs. Any change to rollback behavior is human-gated. | CLAUDE.md change-gate; Tier R |

---

## D. Streaming-on-the-fly — what's actually true (so you don't optimize the wrong thing)

- **Sustainability is download-speed-vs-bitrate, not chunk size.** Fly-streaming a UHD remux sustains
  iff your *effective* download rate > the file's ~8–15 MB/s. Your link (71 MB/s) clears this with
  4–9× headroom; the open question is the **Google per-file throttle** (suspected ~40 MB/s — still
  ≥2.5× margin, but **unverified**).
- **Chunk size only sets time-to-first-frame.** At 8 GB chunks, chunk 1 is playable in ~2–3.5 min. If
  you ever want sub-minute start, the fix is a **small first chunk only** (the IMP-S6 / T2 spike) — not
  smaller chunks everywhere.
- **The real "feels instant" lever is smart prefetch** (IMP-S5): watch ep N → daemon fetches N+1 in
  the background → episodic binges feel instant from ep 2, with zero dependence on chunk size. Your
  asymmetric upload (18 vs 71 MB/s) reinforces *fetch-on-demand over eager re-archive*.

### E-Note — the one measurement that closes the streaming question
Measure **single-file Google-Photos original-download throughput** (chunk MB ÷ download seconds) from a
real fetch — not Speedtest. That number decides whether fly-streaming UHD sustains. Check whether
`mainfetch.py` already logs per-chunk timing so it can be read off the next real fetch.

---

## Quick reference — the decisions to lock

1. **Split size = `8 gb`** everywhere. Never `10` (exceeds cap). (A1)
2. **Round-trip-test DV P7 FEL / HDR / lossless audio before mass-archiving 4K.** (A2)
3. **Decide replica policy (≥2 accounts) before scaling; X1 before the daemon auto-archives.** (A4)
4. **Never delete an original until verified + replicated.** (A5)
5. **Decide plain-vs-encrypted posture now** (gated on the X3 spike). (A6)
6. **Enrich (trickplay/chapters/subs/intro) BEFORE archiving.** (B1/B2)
7. **Freeze ID prefixes, chunk naming, and folder layout before populating.** (B3/B5)
8. **Back up `library_*.json` to git; keep `.mvmeta` sidecars self-sufficient.** (B6)
9. **Land watch-state, session detection, grace periods, phone cleanup before unattended automation.** (C)
10. **Measure Google's per-file download rate** to close the fly-streaming question. (E-Note)
