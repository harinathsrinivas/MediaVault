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

## IMP-U6: canonical `{tmdb-<id>}` provider-token folder format + `migrate_provider_tokens`

- Category: robustness / metadata (client-compatibility bug + drift-class fix, filed under Tier U because it governs the same folder-token convention IMP-D17/U3 introduced for media-server matching)
- Priority: medium (Tier U's normal placement) — but **elevated into PRIORITY.md's Band 0** below because implementation uncovered a live data-integrity risk (see Rationale)
- Files: `mvcommon.py` (new shared `has_tmdb_token`, `find_provider_tokens`, `CANONICAL_TMDB_TOKEN_FMT`, `CANONICAL_TVDB_TOKEN_FMT`), `main.py` (detection routed through the shared helper; duplicate `_PROVIDER_TOKEN_RE` deleted; all emission sites now build from the canonical constant; new `cmd_migrate_provider_tokens`), `tests/test_provider_tokens.py` (new, 16 cases), `tests/test_migrate_provider_tokens.py` (new, 8 cases), plus 8 new artwork-inheritance cases and 1 mkvmerge-escaping pin in existing suites
- Current behavior: Before this branch, `enrich_metadata`/`rename_folder` stamped provider tokens as `{tmdb-12345}` (curly braces), while an earlier external migration had already hand-renamed ~1,400 real folders on disk to `[tmdbid-12345]` (the bracket form Emby/Jellyfin actually document as canonical). Two independent consequences of that mismatch, both live on `main` today: (1) the poster/fanart ancestor-walk regex in the artwork-inheritance code only matched the brace form, so it silently failed to find an ancestor's poster/fanart for almost the entire library — artwork inheritance was effectively broken library-wide, not a narrow edge case; (2) `_has_tmdb_token`'s brace-only detection saw all ~1,400 bracket-form folders as "untokened", so the next `enrich_metadata` pass on any of them would have appended a SECOND, brace-form token onto an already-tokened folder — the same double-stamp mechanism as IMP-C23 (case-sensitivity), now triggered by bracket-vs-brace format instead of case. A read-only measurement taken 2026-09-21 found 0 folders currently double-stamped (the bug had not fired yet), but the exposure was live and would have fired on the next enrichment pass touching any of the 1,400 folders.
- Proposed change (SHIPPED on `feature/imp_u6_provider_tokens`):
  - One shared detection implementation in `mvcommon.py` — `has_tmdb_token`, `find_provider_tokens`, and the canonical format constants `CANONICAL_TMDB_TOKEN_FMT`/`CANONICAL_TVDB_TOKEN_FMT` — recognizes every spelling actually seen in the wild (`{tmdb-…}`, `{TMDB-…}`, `[tmdbid-…]`, mixed case) so detection can never again silently miss a real folder token.
  - `main.py` routes all detection through the shared helper; the duplicate, narrower `_PROVIDER_TOKEN_RE` regex is deleted outright rather than left to drift a 5th time (IMP-C18/C22/C23 were the first three instances of this exact class).
  - All emission (rename_folder token stamping, enrich_metadata) writes ONE canonical form built from `CANONICAL_TMDB_TOKEN_FMT`, never a literal — which is what made the later format correction a one-line change.
  - **CORRECTION (2026-09-22, decision D12):** the canonical form shipped as `[tmdbid-<id>]` and was then changed to **`{tmdb-<id>}`**. A 20-folder matrix scanned by real Plex, Emby and Jellyfin installs proved `[tmdbid-…]` is **invisible to Plex** — Plex rejects the `id` SUFFIX and the `=` separator and is indifferent to bracket style, so the widely-repeated "Plex ignores square brackets" claim (which this task had adopted, see the tradeoff note below) is false. Dual-token emission was also disproved: Jellyfin drops such a folder from the library entirely. See `docs/FOLDER_NAMING_CONVENTIONS.md` and `docs/feature-token-brackets/DECISIONS.md` D11/D12.
  - New `cmd_migrate_provider_tokens`: converts any remaining `{tmdb-…}`/`{TMDB-…}` folders to `[tmdbid-…]` across the library. Dry-run by default (must pass `--apply` to write), `--library` scoping, ancestor-aware (a season/show-level token converts once and re-points every descendant entry, not per-leaf), deepest-first traversal (children renamed before parents so no path goes stale mid-run), idempotent by re-detection (a second run finds nothing left to do), and emits a JSON audit report of every folder it touched or would touch.
  - Fixed the artwork-inheritance ancestor-walk regex as part of the same shared-helper change (it was the same brace-only pattern, same root cause) — poster/fanart inheritance now correctly finds ancestor art through both token formats.
  - **~~Known tradeoff, accepted by user decision D1~~ — SUPERSEDED.** The original note claimed Plex ignores bracketed folder content and falls back to fuzzy matching. Live testing (D11) disproved it: `[tmdb-27205]` matched on Plex fine; the defect was always the `id` suffix, not the brackets. The premise that no single string satisfies all three servers was also wrong — both `{tmdb-…}` and `[tmdb-…]` work on all three. Canonical is now `{tmdb-<id>}` (D12).
- Rationale: This is filed as a format-standardization task, but implementation surfaced two real, live problems on `main`, not just a naming preference: a library-wide artwork-inheritance regression (consequence 1 above) and an active double-stamp risk structurally identical to IMP-C23, just triggered by a different token-format mismatch (consequence 2 above) — the fourth instance of the drift-between-duplicated-parsers class (after IMP-C18, IMP-C22, IMP-C23). That is why this task is elevated into PRIORITY.md's 🔴 Band 0 rather than sitting at Tier U's normal medium/low priority: it is functionally a bug-fix branch that happens to also standardize the format, and the double-stamp exposure it closes is real even though it measured 0 currently-affected folders.
- Goal: A single shared implementation is the only place that recognizes or emits provider-token folder formats, `{tmdb-<id>}` is the format all NEW tokens are written in, and the existing library can be safely converted to match with a dry-run-first migration command.
- Effort estimate: medium (shared-helper refactor + new command + full-suite regression coverage)
- Risk: **low** — purely additive to `main.py` (+308 lines for the new command, no deletions there); the only deletion is the now-redundant duplicate regex, replaced 1:1 by the shared helper it always should have delegated to. The auto-rollback contract, `RollbackJournal`, PONR placement, and `RollbackHardFail` semantics are **untouched** — the migration command calls the existing `cmd_rename_folder` (IMP-D17), whose journal/PONR behavior is unchanged; only the *string* passed to it (bracket vs. brace) is different. `ENTRY_TYPE_KEYS` is **untouched** — no new entry type, no new shared field. Mirrors IMP-D17/IMP-D19's risk write-ups: additive reuse of an existing primitive, no contract change.
- If skipped: the artwork-inheritance regression stays silently broken library-wide (posters/fanart fail to inherit from ancestor folders for ~1,400 already-migrated folders), and the double-stamp exposure remains live — the next enrichment pass over any of those folders risks writing a second, conflicting token, corrupting the very metadata Emby/Jellyfin use to identify the title.
- Status: **done and merged** — PR #53 (initial, `[tmdbid-…]`), PR #54 (format corrected to `{tmdb-<id>}` + `normalize_season_folders`), PR #55 (`docs/FOLDER_NAMING_CONVENTIONS.md`). Live migration run 2026-09-22: 214 tokened folders, 212 resolving to the correct TMDB title as of 2026-09-23. **Follow-on bug: see IMP-U7** — `_show_folder_of` cannot read the season-folder names `normalize_season_folders` creates, so `enrich_metadata` re-stamps season tokens and undoes this migration.

---

## IMP-U7: `_show_folder_of` does not recognise the season-folder name `normalize_season_folders` creates

- Category: correctness (drift-between-duplicated-parsers — **fifth** instance, after IMP-C18/C22/C23/U6)
- Priority: **critical** — elevated into PRIORITY.md Band 0; running `enrich_metadata` today silently undoes the IMP-U6 season normalization
- Files: `main.py` `_show_folder_of` (~line 2151, the season-name *recogniser*), `main.py` `cmd_normalize_season_folders` (~line 5003, the season-name *builder*)
- Current behavior: the two halves of the season-folder convention live in two places and disagree.
  - **Builder** (`cmd_normalize_season_folders`): `new_season_name = f"{show_name} Season {season_number:02d} ({year})"` → `Peaky Blinders Season 06 (2022)`.
  - **Recogniser** (`_show_folder_of`): `re.match(r"(?i)^season[\s_]*\d+$|^s\d+$", base)` — matches only a bare `Season 06` / `S06`.

  The canonical name the builder produces can **never** match the recogniser. When a unit resolves to exactly one season folder, `_show_folder_of` falls through its season-like test and returns *the season folder itself* as the show folder — so `enrich_metadata` stamps the show's TMDB token straight back onto the season folder, which `FOLDER_NAMING_CONVENTIONS.md` §3 explicitly forbids and `normalize_season_folders` exists to remove.

  It only bites shows whose seasons span **multiple years**, because MediaVault library ids embed the *season's* air year (`tv-en-2013-peakyblinders` … `tv-en-2022-peakyblinders`): each season becomes its own unit holding exactly one season folder, hitting the single-folder branch. A show whose seasons share one id year (e.g. Silicon Valley, all under `tv-en-2014-siliconvalley`) groups into one unit with ≥2 season folders, takes the `commonpath` branch, and resolves correctly — which is why the bug is invisible on some shows. This is the same root cause as the IMP-U6 implementation finding that the season guard refused 52 of 64 seasons.
- Measured blast radius (dry run, 2026-09-23, real library): `enrich_metadata --library series` would re-stamp **47 season folders across 11 shows** — Battlestar Galactica, Dark, Devs, Fringe, Mr. Robot, Peaky Blinders, Stranger Things, The Expanse, The Office, The Wire, The X-Files. Silicon Valley and Aindham Vedham correctly skip.
- Proposed change: give the builder and the recogniser **one** shared definition, the way IMP-U6 closed the token-vocabulary drift — a single `mvcommon` helper that both builds and parses the canonical season-folder name, with `_show_folder_of` delegating to it. A local regex widened in place would fix today's symptom and leave the fifth copy free to drift a sixth time.
- Rationale: this is the exact failure mode IMP-C18/C22/C23/U6 each were — two copies of one vocabulary, silently disagreeing, no error raised. Here the two copies are in the *same file*, one creating names the other cannot read. It is worse than the earlier four because the damage is **self-inflicted on the happy path**: a normal `enrich_metadata` run reverses a completed migration and re-introduces exactly the tokens a media server must not see on a season folder.
- Goal: `enrich_metadata` after `normalize_season_folders` is a no-op with respect to folder names, and a season folder can never acquire a provider token.
- Effort estimate: small (one shared helper + delegation), plus a regression test asserting the builder's output satisfies the recogniser — the pin that would have caught this.
- Risk: low — `_show_folder_of` is pure path logic; no rollback-contract, PONR or `ENTRY_TYPE_KEYS` involvement. The new test is the important part.
- If skipped: every `enrich_metadata` run on a multi-year show silently re-stamps season tokens, undoing IMP-U6's normalization and re-introducing the season-level ids that mislead Plex/Emby/Jellyfin scanners.
- Status: **registered, not fixed** (2026-09-23). Found by dry-running `enrich_metadata --library series --nfo` against the real library — the full fixture suite (955 tests) passes throughout, as it did for all four earlier instances.

---

## IMP-U8: `enrich_metadata` can correct neither a wrong token nor a wrong title

- Category: correctness / operability
- Priority: high — there is currently **no supported command** to repair either field
- Files: `main.py` `cmd_enrich_metadata` (~line 2975 token-stamp guard, ~line 3017 title-write guard), CLI dispatch (`set_tmdb` exists; no `set_title`)
- Current behavior: two separate idempotency guards each refuse to correct wrong data.
  1. **Token.** The stamp is skipped whenever the folder already carries *any* recognised token — `folder already has a TMDB token — skip stamp`. Correct for avoiding double-stamps (IMP-C23/U6), wrong when the existing token is the *wrong id*. Fixing one required `set_tmdb` followed by a manual `rename_folder`.
  2. **Title.** `meta["title"]` is overwritten only when the current title is id-shaped or already equals the TMDB title, so a hand-set title is never clobbered. But a title inherited from a *wrong* match is preserved forever, and there is no `set_title` to correct it.
- Observed, 2026-09-23: `mov-en-2018-antmanandthewasp` carried `tmdb_id=227914` (`Chainsaw Scumfuck`, 1988) and `mov-en-2022-werewolfbynight` carried `927837` (`En Mis Zapatos`). Emby and Jellyfin displayed those wrong titles because they were faithfully mirroring MediaVault's own library. Correcting the two entries needed `set_tmdb` + `enrich_metadata --apply` + a manual `rename_folder` + a direct JSON write of `metadata.title`. An audit of all 214 tokened folders against TMDB found 4 wrong ids in total.
- Proposed change: a `--force`/`--repoint` path that re-stamps a token when the folder's id disagrees with the entry's resolved `tmdb_id`, and a `set_title` command (mirroring the existing `set_tmdb`/`set_search`/`set_poster` shape) so a bad title is repairable without hand-editing a library JSON.
- Rationale: the library is the source of truth every media server mirrors, so a wrong id or title there propagates everywhere and is invisible until someone reads a title and notices. Repair must be a supported operation, not a hand edit.
- Goal: any wrong `tmdb_id` or `title` is repairable with documented commands, and a repaired entry's folder token follows automatically.
- Effort estimate: small
- Risk: low — the `--force` path must stay opt-in so the default double-stamp guard is unchanged.
- Status: **registered, not fixed** (2026-09-23).
