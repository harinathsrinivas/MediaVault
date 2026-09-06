# DECISIONS — IMP-U6 (provider token `[tmdbid-…]` + Plex NFO id-pinning)

> Rulings locked with the user on 2026-09-07 during planning (`docs/feature-token-brackets/PLAN.md`
> §Open Decisions). This file records them as the locked decision digest executors must not re-open.

## D1 — Token format: stamp `[tmdbid-<id>]` — RULED (user confirmed)

Jellyfin (official docs: `[tmdbid-12345]`, `[imdbid-tt…]`, `[tvdbid-…]`; curly braces NOT parsed —
jellyfin#14928) and Emby (`[tmdbid-…]` anywhere in the path) parse the square form natively. Plex
parses only curly `{tmdb-…}`; brackets are ignored as hints → Plex falls back to name+year matching
(the id tag is optional in Plex). The user's literal `[tmdb-603692]` proposal would be parsed by NO
server (Jellyfin's keyword is `tmdbid-`, one word). Rejected: `[tmdb-…]` (inert everywhere), dual-tag
`{tmdb-…} [tmdbid-…]` (each server displays the other's tag as literal title text).

## D2 — Suggestion placeholder provider: unify on TMDB — RULED (user confirmed)

Movies AND series/anime both suggest `[tmdbid-0000000]`, `editable_provider_field = "tmdb"`. The
old series/anime `{tvdb-000000}` placeholder (IMP-E12 era) is superseded: MediaVault is
TMDB-for-everything (no TVDB client exists; §6.3a), the enricher already stamps `[tmdbid-…]` on
shows, Plex's TV agent is TMDB-powered, Emby defaults to TheMovieDb, and Jellyfin reads `tmdbid`
tags regardless of scraper order. What TVDB would have offered: absolute/DVD ordering variants and
occasionally richer legacy-show episode data — relevant only under a TVDB-first scraper order, and
the D6 NFO already pins TMDB identity. TVDB ids remain a possible future IMP-E3 breadth item; the
generalized detection regex already recognizes and preserves `[tvdbid-…]` tags in the wild
(e.g. the user's real `Dark (2017) [tvdbid-334824]` folder). Legacy folders untouched.

## D3 — Legacy curly detection: kept permanently

`{tmdb-…}` / `{TMDB-…}` stay recognized forever (case-insensitive, IMP-C23 generalized) so a
pre-migration folder can never be double-stamped. Zero runtime cost; removable only by a future
cleanup IMP.

## D4 — Migration tool placement: `tools/migrate_token_brackets.py` one-shot

Dry-run by default, `--apply` loops `cmd_rename_folder` strictly sequentially (journal-backed,
crash-safe, idempotent re-runs), `--library` filter + `--limit N`. Manual-only — never invoked by
MediaVault, NOT a new CLI verb (keeps the CLI surface and the smoke-gate contract stable). Template:
`tools/migrate_rehash_flag.py`.

## D5 — Live-rename sequencing: after gates, BEFORE the PR

Steps: backup `library_*.json` → dry-run report → 🚦 **user approval** → apply → post-verify
(`verify_library`, `check` ×2, web UI, Plex/Emby re-scan) → open PR. The PR ships a validated end
state. (Merging the PR remains Checkpoint 1 — always the user's.)

## D6 — Plex id-pinning via NFO sidecars — RULED (user-initiated)

Write `movie.nfo` / `tvshow.nfo` (via the unchanged `_write_nfo`) at every stamp into the NEW
folder, **default ON**, `--no-nfo` opt-out on `enrich_metadata` / `prep_push_rep_enrich` /
`prep_push_rep_season_enrich`; never overwrite an existing `.nfo`; the migration tool writes the
NFO for every folder it renames (id from the token text; title/year from `metadata` when present;
minimal id-first otherwise, offline — no api_key). Plex reads it via the official **Plex NFO Agent**
(PMS 1.43+, 2025 — support.plex.tv/articles/using-nfo-metadata-files-with-plex); the agent switch is
a documented one-time manual user step. Season ids follow the show-level model: flat-layout season
folders each get their own token + `tvshow.nfo` (per-season different ids, the anime case); nested
layouts inherit the show's. `.nfo` is not in `VIDEO_EXTENSIONS` — invisible to scan/push/dummy.

## Consumer Impact Analysis (verdict summary)

| Consumer | Verdict |
|---|---|
| `_has_tmdb_token` (idempotency) | rewritten — either TMDB shape; drift-pin tests updated |
| `_PROVIDER_TOKEN_RE` / `_ancestor_show_folder_image` | rewritten — any provider, either shape |
| enrich stamp sites (`cmd_enrich_metadata`, `_enrich_after_archive`) | rewritten — shared `format_tmdb_token` + NFO write |
| `suggest_target_folder` (+`/api/reclaim` → card.js hint) | unified `[tmdbid-…]` placeholder |
| help/usage strings, code comments | text-only updates |
| `build_tree` / `/api/tree` / `/api/detail` | no change (name-blind / uses `metadata.tmdb_id`) |
| chunk names, `search_term`, `uid`, `short_id` | verified never token-derived — no change |
| remote phone paths, `.mvmeta.json` | path-component only — no code change |
| `scan_unprepped` / push / dummy pipeline | no change (`.nfo` not a video extension) |
| `_write_nfo` | reused unchanged; new callers only |
| rollback journal / PONR / `ENTRY_TYPE_KEYS` | UNTOUCHED (not change-gated) |
