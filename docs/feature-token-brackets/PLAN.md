# PLAN — Provider folder token `{tmdb-…}` → `[tmdbid-…]` + Plex NFO id-pinning (IMP-U6)

> **Task:** Migrate the provider-id folder-name token from curly braces (`Midnight in Paris (2011)
> {tmdb-59436}`) to media-server-native square brackets (`John Wick Chapter 4 (2023)
> [tmdbid-603692]`), everywhere in code, tests, and docs — and keep Plex's forced-id match via
> **NFO sidecar files** (D6) instead of the curly tag — then bulk-rename every existing tokened
> folder in the live library via the existing crash-safe `rename_folder` machinery.
>
> **Framework:** v2 · **Suggested branch:** `feature/imp_u6_token_brackets` (from up-to-date `main`)
> **Plan/Decisions file paths:** root `/PLAN.md` (live, gitignored) · `docs/feature-token-brackets/PLAN.md`
> (canonical, tracked) · journal: `docs/feature-token-brackets/PROGRESS.md` · rulings:
> `docs/feature-token-brackets/DECISIONS.md`
> **Planning date:** 2026-09-07 · **IMP:** IMP-U6 (Tier U, registered at Step 0) · **Status:** planning
> complete — **D1 RULED 2026-09-07: stamp `[tmdbid-<id>]`** · **D2 RULED 2026-09-07: suggestion
> placeholders unify on TMDB (series/anime too)** · **D6 RULED 2026-09-07: Plex id-pinning via NFO
> sidecars** — ready for Step 0
>
> ⚠️ **Change-gate check:** this task does NOT touch the auto-rollback contract (journal format,
> PONR locations, `RollbackHardFail`, `recover_journal` semantics) — `cmd_rename_folder` is reused
> verbatim with its existing journal behavior. No `ENTRY_TYPE_KEYS` change. Not change-gated under
> `CLAUDE.md`; the live-library rename (Step 8) still runs behind its own explicit user gate.
> ⚠️ **IMP-C24 discipline** (no two mutating commands in parallel) applies to the Step 8 rename loop
> and to every live run until C24 ships.

---

## 1. Context

### 1.1 What the token is today

`enrich_metadata` / `prep_push_rep_enrich` / `prep_push_rep_season_enrich` stamp a provider-id
token onto the show-level (or movie) folder **once**, via `cmd_rename_folder`:

```
Death Note (Complete Series) {tmdb-12345}
Dark Season 01 (2017) {tmdb-70523}          <- season folders inherit by being under it
F1 The Movie {tmdb-1003159}
```

The token is consumed by three consumers in MediaVault: (1) the enrich idempotency guard
(`_has_tmdb_token`, main.py:1687 — "already has a token, skip stamp"), (2) the artwork
season-inheritance walk (`_PROVIDER_TOKEN_RE` at main.py:9585 used by
`_ancestor_show_folder_image` main.py:9665 — "walk up to the nearest `{tmdb-…}` show folder"),
and (3) the web console's suggested-folder placeholder (`suggest_target_folder` main.py:8360 —
`{tmdb-0000000}` movies / `{tvdb-000000}` series+anime). Verified: the token is **folder-name-only**
— it never enters chunk filenames, `search_term`, `uid`/`.sha256` sidecars, or `short_id`. It does
propagate as a path component to the Pixel's remote dirs and `.mvmeta.json` (harmless; future
pushes of a renamed folder simply target the new path).

### 1.2 The research verdict (is `[]` actually the proper archival format?)

**Yes for Jellyfin + Emby — but the keyword must be `tmdbid-` (one word), not `tmdb-`.**

| Server | Recognized syntax (official) | Our current `{tmdb-59436}` | Proposed `[tmdb-603692]` | Proposed `[tmdbid-603692]` |
|---|---|---|---|---|
| **Jellyfin** (primary, future) | `[tmdbid-12345]`, `[imdbid-tt0106145]`, `[tvdbid-121361]` — square brackets **required**; keyword `tmdbid`/`imdbid`/`tvdbid`; folder or file; multiple tags allowed (`… [tmdbid-680] [imdbid-1234]`) | ❌ not parsed (curly braces unrecognized — jellyfin#14928) | ❌ not parsed (`tmdb-` keyword wrong) | ✅ parsed & hidden from title |
| **Emby** (Premiere, owned) | `[tmdbid-…]`, `[imdbid-…]` anywhere in the path | ❌ | ❌ | ✅ |
| **Plex** (Pass lifetime) | `{tmdb-12345}`, `{tvdb-…}`, `{imdb-tt…}` — curly braces only; brackets ignored as match hints | ✅ parsed | ⚠️ ignored → falls back to name+year matching | ✅ via **NFO sidecar** (D6) — name stays clean |

Sources: [Jellyfin — Metadata Provider Identifiers](https://jellyfin.org/docs/general/server/metadata/identifiers/) ·
[Jellyfin — Movies Naming](https://jellyfin.org/docs/general/server/media/movies/) ·
[jellyfin/jellyfin#14928 (curly braces not supported)](https://github.com/jellyfin/jellyfin/issues/14928) ·
[Plex — Naming and organizing your TV show files](https://support.plex.tv/articles/naming-and-organizing-your-tv-show-files/) ·
[Plex — Naming and organizing your movie media files](https://support.plex.tv/articles/naming-and-organizing-your-movie-media-files/) ·
[Emby community — tmdbid folder tags](https://emby.media/community/topic/102371-metadata-system-not-picking-up-tmdbidxxxx-in-folder-name/).

**The Plex gap is closed by NFO files (D6).** Plex shipped the official **Plex NFO Agent** in 2025
(PMS 1.43+): it reads Kodi-style `movie.nfo` / `tvshow.nfo` from the folder and matches from the
ids inside (`<uniqueid type="tmdb">`). Sources:
[Plex — Using NFO Metadata Files with Plex](https://support.plex.tv/articles/using-nfo-metadata-files-with-plex/) ·
[Plex forums — Plex NFO Agent preview](https://forums.plex.tv/t/plex-nfo-agent-forum-preview/936104).
MediaVault already owns the exact writer: **`_write_nfo`** (shared by `enrich_metadata --nfo` and
the IMP-D22 enrich autopilots) emits `<uniqueid type="tmdb" default="true">`, plain `<tmdbid>`,
`<imdbid>`, title/year/plot/genres/runtime/studio/director/actors — and **never** `<tvdbid>`.
So the final architecture is:

- Folder **name**: `Name (Year) [tmdbid-12345]` → parsed natively by Jellyfin + Emby.
- Folder **contents**: `movie.nfo` / `tvshow.nfo` with the same id → parsed by the Plex NFO agent
  (and by Jellyfin/Emby too — NFO is their strongest signal anyway).
- No curly tag anywhere; no dual-tag title clutter; each server pins the id from its own native signal.

Supporting facts:
- The user's **own pre-Jellyfin-era folders already used the target style** — `Dark (2017) [tvdbid-334824]`
  (recorded in `docs/feature-web-console/PLAN.md:81`: pre-existing square-bracket folders were
  intentionally left untouched when the curly convention was introduced). This migration is a
  **return to the user's own older, generic convention**, now with the correct `tmdbid-` keyword.
- Dual-tagging (`Movie (2024) {tmdb-12345} [tmdbid-12345]`) remains the documented fallback but is
  superseded by D6's NFO approach (cleaner names; NFO also carries the rich metadata Plex displays).
- Windows/mkvmerge safety: `[` `]` are legal Windows folder-name characters (not in the reserved set
  `<>:"/\|?*`), and mkvmerge's `-o` format string only treats `{`/`}` (libfmt) and the split `%d`
  substitution as special — the chunk filename pattern already contains literal `[`/`]`
  (`<base> [<short_id>].chunk.NNN.mkv`, main.py:318) with no bracket handling anywhere. The
  existing `{{`/`}}` brace escape in `split_video_file` (main.py:378) stays defensively. NFO files
  are invisible to the pipeline (`.nfo` is not in `VIDEO_EXTENSIONS` — no scan/push impact).

### 1.3 Blast radius (verified inventories)

- **Code hits (runtime):** `main.py` — `_has_tmdb_token` (1687–1698), `_PROVIDER_TOKEN_RE` (9585–9589),
  `_ancestor_show_folder_image` (9663–9679), enrich stamp decision+apply in `cmd_enrich_metadata`
  (2633–2640, 2686–2696) and `_enrich_after_archive` (7726–7740) — both hardcode
  `f"{base_name} {{tmdb-{tmdb_id}}}"` — `suggest_target_folder` (8360–8375), help/usage strings
  (9928–9929, 9954, 10540–10544), comment blocks (372–377, 1285–1307, 3623, 3672, 7558–7561,
  8814–8824, 9652, 9702, 9777), and `webui/static/card.js:565–568` (renders the
  `"edit the {" + field + "-…}"` hint). `_write_nfo` (2381–2503) gains new callers (stamp sites +
  migration tool) with its behavior unchanged. `mainfetch.py`, `mvcommon.py`, `tools/*.py`: **zero**
  token hits. `webui/server.py`: comment-only (908).
- **Tests:** ~145 literal `{tmdb` occurrences across 13 test files; ~60–80 assertions touch the
  literal, of which ~10 are *semantic* pins (regex/case/idempotency/placeholder/escaping) and the
  rest seed-data strings. Densest: `test_enrich_metadata.py` (44), `test_prep_push_rep_season_enrich.py` (35),
  `test_prep_push_rep_enrich.py` (17), `test_web_media_image.py` (13), `test_web_items.py` (10),
  `tests/smoke/test_smoke_all_commands.py` (9), `test_split_brace_escape.py` (7 — whole-file rework),
  `test_rename_folder.py` (7), `test_web_datafns.py` (8 — placeholder pins), `test_web_detail.py` (3),
  `test_refresh_online.py` (2), `test_fetch_trivia.py` (2), `test_extras.py` (1×5 uses),
  `tests/js/test_data_buckets.mjs` (1).
- **Docs:** ARCHITECTURE.md (§5 rows 231/256, web-console block ~500, §6.3 example 894, §6.3a
  normative block 975–1198 — including the now-superseded "`--nfo` is OFF by default" audit note),
  README.md (384, 419, 436–447 + command examples 197/218/392/453/549/563–564),
  docs/OPERATIONS_QA.md (149–164, 218), docs/feature-prep-push-rep-enrich/DECISIONS.md (D1 + NFO
  example + old-regex quote), docs/feature-web-media-ui/DECISIONS.md (#7/#8), docs/feature-web-console/PLAN.md
  (78–81 suggestion convention), docs/ZCODE_ONBOARDING.md (2 incidental rows), improvements_tierC/D/E
  entries (C23/D17/D22/E3 mention the convention), STATUS.md (historical, incidental).

---

## 2. Goal

1. MediaVault **stamps** `[tmdbid-<tmdb_id>]` (D1) as its provider token on every show/movie folder,
   via a single shared formatter, from every stamping path — including the future "give the id at
   prep + rename" flow (`rename_folder` and the `prep_push_rep_enrich` / `prep_push_rep_season_enrich`
   rename legs, which already route through the same two stamp sites).
2. MediaVault **writes the NFO sidecar** at every stamp (D6) so Plex pins the same id from the file
   inside the folder — reusing `_write_nfo` unchanged.
3. MediaVault **recognizes** provider tokens in both shapes — new square `[tmdbid-…]`/`[tvdbid-…]`/
   `[imdbid-tt…]` AND legacy curly `{tmdb-…}`/`{TMDB-…}` — so no legacy folder is ever
   double-stamped during or after the transition (the IMP-C23 lesson, generalized).
4. The artwork ancestor walk accepts any provider token shape.
5. A one-shot, dry-run-by-default migration tool converts every existing legacy folder **and writes
   its NFO in the same pass**, and the live-library rename is executed and verified end-to-end.
6. **Zero regressions:** no other feature's behavior changes (full suite + smoke gate green; every
   changed line traces to this convention).

Non-goals: stamping imdbid/tvdbid (detection-only generalization; TMDB-for-everything stamping
unchanged), Plex/Emby/Jellyfin server-side configuration (documented only; the one-time Plex agent
switch is a manual user step), phone-side folder renames of already-archived remotes.

---

## 3. Files affected

| Area | Files |
|---|---|
| Core | `main.py` (predicate family, stamp sites + NFO write, artwork walk, suggestions, help text, comments) |
| Web UI | `webui/static/card.js` (note text), `tests/js/test_data_buckets.mjs` |
| New tool | `tools/migrate_token_brackets.py` (new) |
| Tests | the 14 files listed in §1.3 + new `tests/test_token_brackets.py` (predicate/stamp/walk/NFO-at-stamp) + `tests/test_migrate_token_brackets.py` |
| Docs | `ARCHITECTURE.md`, `README.md`, `docs/OPERATIONS_QA.md`, `docs/feature-token-brackets/*` (new), addenda to `docs/feature-prep-push-rep-enrich/DECISIONS.md`, `docs/feature-web-media-ui/DECISIONS.md`, `docs/feature-web-console/PLAN.md` |
| Backlog | `improvements/improvements_tierU.md` (IMP-U6), `improvements/PRIORITY.md`, `docs/priority-graph/priority-graph.html`, `improvements/improvement_details.md` (cross-ref) |

**Explicitly NOT touched:** `mainfetch.py`, `mvcommon.py`, `mvconfig.example.json`, rollback journal
code, `ENTRY_TYPE_KEYS`, `archive/` snapshots, `Master_Stream_Archiver*`/`MatchArchiver*`.

---

## 4. Approach

### 4.1 The token layer (Steps 1–2) — one predicate family, one formatter, two stamp sites

Add near the existing config block (single source of truth, mirroring the IMP-C23 anti-drift rule —
all token regexes live together and a drift-pin test asserts agreement):

```python
# New canonical: square brackets, Jellyfin/Emby-native keyword (D1).
# Legacy: curly braces {tmdb-…} / {TMDB-…} — detected forever (D3) so a
# pre-migration folder can never be double-stamped.
PROVIDER_TOKEN_SQUARE_RE = re.compile(r"\[(tmdbid|tvdbid|imdbid)-([^\]]+)\]", re.IGNORECASE)
PROVIDER_TOKEN_CURLY_RE  = re.compile(r"\{(tmdb|tvdb|imdb)-([^\}]+)\}",       re.IGNORECASE)
_PROVIDER_TOKEN_RE = <combined alternation of both>        # keeps the historical name —
                                                           # used by _ancestor_show_folder_image
TMDB_TOKEN_SQUARE_RE = re.compile(r"\[tmdbid-[^\]]+\]", re.IGNORECASE)
TMDB_TOKEN_CURLY_RE  = re.compile(r"\{tmdb-[^\}]+\}",   re.IGNORECASE)   # legacy, case-insensitive (IMP-C23)

def _has_tmdb_token(name): ...        # either TMDB shape (idempotency guard — unchanged contract)
def _has_provider_token(name): ...    # any provider, either shape (artwork walk)
def format_tmdb_token(tmdb_id):       # the ONE stamp formatter
    return f"[tmdbid-{tmdb_id}]"      # (D1)
def strip_tmdb_tokens(name): ...      # NEW helper: removes any TMDB token (either shape) —
                                      # used by the migration tool + future re-stamp flows
```

- `_ancestor_show_folder_image` switches to `_has_provider_token` semantics (a `[tvdbid-334824]`-only
  show folder now satisfies the walk — it previously did not, despite the old comment claiming `{tvdb-…}` counted).
- Both stamp sites (`cmd_enrich_metadata` apply block; `_enrich_after_archive`) replace the inline
  `f"{base_name} {{tmdb-{tmdb_id}}}"` with `f"{base_name} {format_tmdb_token(tmdb_id)}"` and the
  `will_stamp` guard stays `_has_tmdb_token(base_name)` (now shape-agnostic). The "already has a
  token — skip stamp" message text updates.
- `suggest_target_folder` (D2): the placeholder **unifies on TMDB** — movies `"{tmdb-0000000}"` →
  `"[tmdbid-0000000]"` and series/anime `"{tvdb-000000}"` → `"[tmdbid-0000000]"` (both branches now
  return `editable_provider_field = "tmdb"`; the movie/series split collapses to one). The enricher
  already stamps `[tmdbid-…]` on shows (TMDB-for-everything), so the suggestion now matches what
  the system will actually do next instead of inviting a second tag later.
- Help text (3 sites) + comment blocks + `card.js` note text updated to the new shape.
- `cmd_rename_folder` itself is **unchanged** — it sets whatever leaf name the caller supplies;
  the convention lives in the callers + the shared formatter. So "give the id at prep and ask to
  rename" automatically produces `[tmdbid-…]`.

### 4.2 NFO sidecars at every stamp (D6 — Step 2)

- **Both stamp sites call `_write_nfo`** after a successful rename, into the NEW folder
  (kind = movie → `movie.nfo`, show → `tvshow.nfo`; ids from the unit's resolved tmdb_id; the
  function's "NEVER raises" contract is reused as-is). Default **ON** (it is the point of D6);
  `--no-nfo` opt-out added to `enrich_metadata`, `prep_push_rep_enrich`, `prep_push_rep_season_enrich`.
  The existing `--nfo` flag keeps its meaning for the no-rename path (write NFO into the unchanged
  folder). ARCHITECTURE's 2026-08-28 "off by default matches usage" note is superseded and re-dated.
- **Never overwrite an existing `.nfo`** (same rule as enrich's poster/fanart rule) — a hand-tuned
  NFO is never clobbered by a re-stamp.
- **Seasons:** id-pinning is show-level in all three servers. MediaVault's flat layout means each
  season folder IS a show folder → it gets its own `[tmdbid-…]` **and** its own `tvshow.nfo` (the
  anime case, where each season can be a separate TMDB show — per-season different ids work
  naturally). Nested `<Show>/Season NN/` layouts inherit the show folder's token + `tvshow.nfo` —
  unchanged from today. (Season-level `season.nfo` art/summaries are out of scope.)
- **`.nfo` safety:** not in `VIDEO_EXTENSIONS`, so NFOs are invisible to `scan_unprepped`, the push
  pipeline, and the dummy system — zero interaction with archiving.

### 4.3 Migration tool (Step 5) — `tools/migrate_token_brackets.py`

Template: `tools/migrate_rehash_flag.py` (idempotent one-shot, manual-only, never called by
MediaVault, explicit-pathspec commit rule per `docs/STANDALONE_TOOLS.md` §5).

- **Scan:** load the live library (`mvcommon.load_library`); collect every distinct `folder_path`
  whose leaf name carries a token in ANY shape; classify each into
  `legacy_curly_tmdb` / `already_square_tmdbid` / `square_other_provider` / `mixed`.
- **Plan:** target name = leaf name with every TMDB curly token replaced by `[tmdbid-<id>]`
  (same position), existing square tags preserved verbatim (`[tvdbid-334824]` stays), duplicates
  collapsed (`Dark (2017) [tvdbid-334824] {tmdb-70523}` → `Dark (2017) [tvdbid-334824] [tmdbid-70523]`).
- **NFO leg:** every folder in the plan gets its `movie.nfo`/`tvshow.nfo` written in the same pass
  (id from the token text itself; title/year/overview from the entry's `metadata` when present —
  minimal id-first NFO otherwise). Idempotent: an existing `.nfo` is never overwritten.
- **Dry-run default:** prints the full per-folder report (old → new, entry ids affected, season
  children, NFO will-write/would-write) + counts; exits 0. `--apply` re-checks the plan, then loops
  `main.cmd_rename_folder(old_id_or_path, new_leaf)` **strictly sequentially** (journal-backed,
  crash-safe, resume = re-run the tool; done folders no longer match the scan → idempotent). Never
  parallel (IMP-C24 discipline). A `--library` filter and `--limit N` exist for controlled first runs.

### 4.4 Live-library rename (Step 8) — guarded, resumable, verifiable

1. **Backup:** copy `C:\Media\library_*.json` to a dated sibling (`library_movies.backup-<date>.json`)
   before anything mutates. (Folders are renamed, not deleted; `rename_folder` is hash-safe —
   bytes never change — and journal-backed.)
2. **Dry-run report → 🚦 user review** (the report is pasted into PROGRESS.md Run history).
3. **Apply** (tool, sequential; renames + NFO writes). Expect roughly the number of enriched
   shows+movies (hundreds).
4. **Post-verify:** `python main.py verify_library` (all four libraries); `python main.py check <id>`
   on 2 sampled entries (hash-safe proof); `python main.py web` → tree + a season poster via the
   ancestor walk; confirm no entry still carries a curly token (tool re-scan = 0 pending); spot-check
   2 migrated folders for the new `movie.nfo`/`tvshow.nfo`; then the **one-time manual Plex step**
   (see Step 6) and a Plex/Emby re-scan check (Jellyfin not stood up yet — no watch-state risk).

---

## 5. Steps (each resumable — see §7)

Legend: every step ends with (a) its Verification green, (b) root `/PLAN.md` ticked, (c) PROGRESS.md
Step-status row + `▶ NEXT ACTION` updated, (d) ONE commit on the feature branch (explicit pathspec,
message `Refs: improvements_tierU.md IMP-U6`, `Co-Authored-By` trailer per `docs/git-pr-conventions.md`).

- [ ] **0. Bootstrap** — Branch `feature/imp_u6_token_brackets` from up-to-date `main` (confirm clean tree first). Register **IMP-U6** in `improvements_tierU.md` (full attribute list per `improvement_details.md` §2; Status: `in_progress`) + a cross-ref in `improvement_details.md`; add PRIORITY.md Band-1 row; add priority-graph node `["U6","provider token → [tmdbid-…] brackets + Plex NFO pin","U","med","todo","…"]` + edges `["U6","D17"],["U6","D22"],["U6","C23"]`; bump PRIORITY.md Last-updated. Write `DECISIONS.md` (rulings D1–D6) + seed `PROGRESS.md` (Step-status table all `pending`, `▶ NEXT ACTION`, Resume protocol, Blockers/human-gates sections — structure of `docs/feature-extras/PROGRESS.md`). Root `/PLAN.md` already holds this plan (rotated at planning time; the parked IMP-C24 live copy is preserved in tracked `docs/feature-library-concurrency/PLAN.md` — note the rotation in PROGRESS.md Run history).
  - *Acceptance:* `git log --oneline -1` on the new branch; tier/PRIORITY/graph files parse and agree (graph node count increments; validate with the `node -e` extraction trick).
- [ ] **1. Predicate core** — `main.py`: add the token regex family + `_has_provider_token` + `format_tmdb_token` + `strip_tmdb_tokens`; rewrite `_has_tmdb_token` to either-shape; re-point `_PROVIDER_TOKEN_RE` (keep the name) at the combined pattern; update its comment. Update the IMP-C23 drift-pin tests to the new family (both shapes, case variants, `{tvdb-…}`-negative for TMDB predicate, old+new agreement).
  - *Depends on:* 0 · *Files:* `main.py`, `tests/test_enrich_metadata.py` (C23 pins)
  - *Acceptance:* `python -m pytest tests/test_enrich_metadata.py -q` green.
- [ ] **2. Stamp sites + NFO + UI text** — both enrich stamp sites use `format_tmdb_token` AND call `_write_nfo` into the new folder (D6; `--no-nfo` opt-out on all three enrich commands; never-overwrite rule); "already has a token" message updated; `suggest_target_folder` new placeholders; help/usage strings (3 sites); comment blocks (6 sites); `card.js:565-568` note; `tests/js/test_data_buckets.mjs` fixture.
  - *Depends on:* 1 · *Acceptance:* `python -m pytest tests/test_prep_push_rep_enrich.py tests/test_prep_push_rep_season_enrich.py tests/test_web_datafns.py -q` green (they fail red before this step — that's the point).
- [ ] **3. Artwork walk** — `_ancestor_show_folder_image` → any-provider/any-shape; docstrings at 9652/9702/9777; `resolve_artwork_path` rung-(iii) comment.
  - *Depends on:* 1 · *Acceptance:* `python -m pytest tests/test_web_media_image.py tests/test_web_items.py tests/test_web_detail.py -q` green.
- [ ] **4. Test sweep + rework** — swap every seed literal to the new shape across the 13 files; rewrite the ~10 semantic pins (stamp-once idempotency incl. a **legacy-curl folder is never re-stamped** case; uppercase `{TMDB-…}` case; `-tvdbid` refusals unchanged; rename-call argument pins); **NFO-at-stamp tests** (NFO written into the new folder on stamp; `--no-nfo` skips; existing `.nfo` never overwritten; `--nfo` no-rename path unchanged); **rework `tests/test_split_brace_escape.py`** (keep one literal-brace defense test using a name with a real `{`, add a `[tmdbid-…]` passthrough test proving no escaping needed); add `tests/test_token_brackets.py` (predicate/format/strip unit tests); smoke suite updates land here.
  - *Depends on:* 1–3 · *Acceptance:* `python -m pytest -q` fully green.
- [ ] **5. Migration tool + tests** — `tools/migrate_token_brackets.py` (§4.3, incl. the NFO leg) + `tests/test_migrate_token_brackets.py` (sandbox library; shapes classification, replace-preserve-dedup logic, NFO write/skip behavior, dry-run-vs-apply, idempotent re-run, sequential call-order assertion; hard guard: never touches real `C:\Media`).
  - *Depends on:* 1 · *Acceptance:* new tests green; tool banner/dry-run runs against a sandbox tree.
- [ ] **6. Docs** — ARCHITECTURE.md §6.3a normative rewrite (token definition, both shapes, NFO-at-stamp default + superseded off-by-default note, migration story, artwork walk, placeholder), §5 rows, §6.3 example; README.md normative lines + examples; docs/OPERATIONS_QA.md (incl. the **one-time Plex agent config**: Settings → Metadata Agents → add the NFO agent per the official article, optionally stacked on the Plex Movie agent; + Emby/Jellyfin note that NFO+bracket agree); addenda (dated, no history rewrites) to `docs/feature-prep-push-rep-enrich/DECISIONS.md`, `docs/feature-web-media-ui/DECISIONS.md`, `docs/feature-web-console/PLAN.md`; docs/ZCODE_ONBOARDING.md 2 rows.
  - *Depends on:* 1–5 · *Acceptance:* grep `'{tmdb'` across docs returns only historical/addendum contexts; grep `'\[tmdbid-'` documents the new convention; the Plex NFO agent setup is documented.
- [ ] **7. Full gates** — `python -m pytest -q` (full suite) AND `python -m pytest tests/smoke -q` AND `node tests/js/test_data_buckets.mjs`; all green before anything touches the live library.
  - *Depends on:* 1–6
- [ ] **8. 🚦 Live-library rename + NFO backfill** — §4.4 protocol: backup → dry-run report → **user reviews/approves** → `--apply` → post-verify incl. the manual Plex agent switch + re-scan check. Paste the dry-run report + apply log into PROGRESS.md Run history.
  - *Depends on:* 7 · *Human gate:* explicit user approval of the dry-run report.
- [ ] **9. PR + closeout** — PROGRESS.md final state; tier U6 → `done` on merge (mark at merge time per the user's C18-style protocol), PRIORITY.md row + 👉 NEXT + graph node → `done`; PR title `feat: provider folder token {tmdb-…} → [tmdbid-…] + Plex NFO id-pin — IMP-U6`; body per `docs/git-pr-conventions.md` (Summary/Changes/Test plan → `## Original task prompt` verbatim (captured in §9 below) → trailer). **STOP at Checkpoint 1** — merging to `main` is the user's explicit act; Checkpoint 2 (branch archive tag) separate.

### Consumer Impact Analysis (summary — full table in DECISIONS.md)

Shared-data-contract changes: NONE to `ENTRY_TYPE_KEYS` / entry shapes / status values. Changed
shared *convention*: the folder token string; NEW additive artifact: `.nfo` files (invisible to
`VIDEO_EXTENSIONS` consumers — verified). Consumers greped and verdicted: `_has_tmdb_token`
(rewritten, covered), `_PROVIDER_TOKEN_RE`/`_ancestor_show_folder_image` (rewritten, covered), both
enrich stamp sites (rewritten + NFO write, covered), `suggest_target_folder` (+`/api/reclaim` →
card.js hint text, covered), help text (cosmetic), `build_tree`/`/api/tree` (name-blind — no
change), `/api/detail` (uses `metadata.tmdb_id` — no change), chunk names/`search_term`/`uid`/
`short_id` (verified never token-derived — no change), remote phone paths (path-component only —
no code change), `_show_folder_of` (token-blind — no change), `scan_unprepped`/push/dummy pipeline
(`.nfo` not a video extension — no change), `strip`/parse helpers (none existed; new
`strip_tmdb_tokens` added for the tool only), `_write_nfo` (reused, behavior unchanged).

### Risks and edge cases

1. **Plex NFO agent is version-gated** (PMS 1.43+, 2025) and is a **per-library agent config** — a
   one-time manual user step, documented in Step 6. Before the user flips the agent, migrated
   folders behave per the pre-D6 plan (name+year matching). No MediaVault code depends on the
   agent being configured.
2. **Sparse-NFO display:** a minimal id-first NFO (migration tool on entries with thin `metadata`)
   pins the match but may show sparse metadata in Plex until the agent's online fallback fills it
   (stacked per the forum preview) — cosmetic only.
3. **Plex loses the curly-tag hint on migrated folders** — superseded by D6: the NFO pins the id
   instead. Per-folder fallback remains possible (`rename_folder` with a curly tag) if ever needed.
4. **Mixed-shape folders** (`Dark (2017) [tvdbid-334824] {tmdb-70523}`) — migration preserves the
   square tag and converts only the curly one; enrich idempotency must treat ANY tmdb token
   (either shape) as "has token" (Step 1 test).
5. **Uppercase `{TMDB-69590}`** (real folder, IMP-C23) — legacy regex stays case-insensitive; drift pin kept.
6. **Jellyfin watch-state loss on rename** — N/A today (Jellyfin not stood up; S1 will index the
   migrated names fresh — the ideal order).
7. **Media-server file locks during bulk rename** (Emby running) — `cmd_rename_folder` already
   retries 3× with `chmod S_IWRITE`; the tool loops sequentially and journals each rename; a locked
   folder is reported and re-run is idempotent. Optionally pause Emby scans during Step 8.
8. **A folder already at target shape** — scan classifies `already_square_tmdbid` → no rename; NFO
   written only if missing (idempotency).
9. **`{tvdb-000000}`-style placeholder folders created from old suggestions** — classified
   `square_other_provider`/`legacy_curly` and converted like any other (tmdb-curly ones become
   real `[tmdbid-…]` only if the library has the id; zero-pad placeholders without a library id
   are reported, not invented).

### Verification (the gates)

```
python -m pytest -q                 # full suite — MUST be green (bare `pytest -q` collects nothing; always `python -m pytest`)
python -m pytest tests/smoke -q     # the mandated cross-command smoke gate
node tests/js/test_data_buckets.mjs # JS fixture pin
python tools/migrate_token_brackets.py            # dry-run report (no mutation)
python tools/migrate_token_brackets.py --apply    # only after 🚦 user approval
python main.py verify_library
python main.py check <sampled-id>                  # ×2 — hash-safe proof
```

### Manual test commands (post-implementation, live)

```
python main.py rename_folder mov-en-2010-inception "Inception (2010) [tmdbid-27205]"     # manual rename → new token
python main.py enrich_metadata <unenriched-id> --apply                                    # stamps [tmdbid-…] once + writes movie.nfo/tvshow.nfo
python main.py enrich_metadata <already-stamped-legacy-id>                                # "already has a token — skip stamp" (no double)
python main.py enrich_metadata <id> --apply --no-nfo                                      # stamp without NFO (opt-out proof)
python main.py prep_push_rep_enrich <id> <file> SIZE_GB 8 -tmdbid 397243 --yes            # autopilot stamp in new format + NFO
python main.py prep_push_rep_season_enrich <season-id> <folder> SIZE_GB 8 --no-rename     # season autopilot, enrich-only
python main.py web --demo                                                                 # suggestion placeholder [tmdbid-0000000]
# browser: /api/media-image/<season-id>?kind=poster — ancestor walk works on migrated names
# filesystem: check 2 migrated folders for movie.nfo / tvshow.nfo with <uniqueid type="tmdb">
# Plex: switch library agent to the NFO agent (one-time) → re-scan → spot-check 3 titles
```

### Out of scope

IMDb/TVDB *stamping* (detection-only); AniDB; server-side config automation (the Plex agent switch
and Emby/Jellyfin library scans are documented manual steps); season-level `season.nfo` art/summaries;
renaming phone-side copies of already-archived remotes; `mainfetch.py`/`mvcommon.py` changes; the
IMP-C24 concurrency fix (orthogonal; its discipline is honored).

### Open Decisions

- **D1 — Token format. RULED (2026-09-07, user confirmed):** stamp `[tmdbid-<id>]` — Jellyfin/Emby
  parse it natively; **Plex ignores it as an id hint** and falls back to name+year matching *unless*
  the D6 NFO is present. Rejected alternatives: `[tmdb-…]` (parsed by no server), dual-tag
  `{tmdb-…} [tmdbid-…]` (both servers display the other tag as title text).
- **D2 — Suggestion placeholder provider. RULED (2026-09-07, user confirmed): unify on TMDB.**
  Movies AND series/anime both suggest `[tmdbid-0000000]` with `editable_provider_field = "tmdb"`.
  Rationale: MediaVault is TMDB-for-everything (there is NO TVDB client — TMDB id is the only id the
  system can stamp, store, or enrich with, §6.3a), so a TVDB placeholder invites an id the system
  can never fill or use; the enricher already stamps `[tmdbid-…]` on show folders, so a TVDB
  suggestion would end up as a second tag on the same folder; Plex's current TV agent ("Plex TV
  Series") is TMDB-powered; Emby's default show scraper is TheMovieDb; Jellyfin reads both tag
  forms regardless of scraper order. What TVDB would have offered (the honest answer to "is tvdbid
  better?"): absolute/DVD ordering variants and occasionally richer episode data for legacy shows —
  relevant only if a server runs a TVDB-first scraper order, and even then the D6 NFO already pins
  the TMDB identity while the server scrapes per its own order; mixing both ids in one name can
  make the two numbering spaces disagree (season-mapping conflicts). If TVDB ids are ever wanted,
  that is IMP-E3 breadth (a real TVDB client) — the generalized detection regex already recognizes
  and preserves `[tvdbid-…]` tags that exist in the wild, so nothing breaks in the meantime.
  Legacy `[tvdbid-…]` folders stay untouched; new suggestions are TMDB-only.
- **D3 — Legacy curly detection window.** RULED by default: keep `{tmdb-…}` recognition permanently
  (zero-cost idempotency safety net; removable by a future cleanup IMP if ever desired).
- **D4 — Migration tool placement.** RULED by default: `tools/migrate_token_brackets.py` one-shot,
  dry-run-default, manual-only (pattern of `tools/migrate_rehash_flag.py`), NOT a new CLI verb in
  `main.py` (keeps the CLI surface stable and the tool out of the smoke-gate contract).
- **D5 — Live-rename sequencing.** RULED by default: after Step 7 gates, BEFORE opening the PR (PR
  ships a validated end state); the rename itself is 🚦 user-gated on the dry-run report.
- **D6 — Plex id-pinning via NFO. RULED (2026-09-07, user-initiated):** write `movie.nfo`/`tvshow.nfo`
  at every stamp (default ON, `--no-nfo` opt-out) and in every migration pass, reusing `_write_nfo`
  unchanged; never overwrite an existing `.nfo`; one-time manual Plex NFO-agent config documented.
  Sub-defaults: minimal id-first NFO from the tool on thin-metadata entries; season ids follow the
  show-level model (flat layout = own NFO per season-folder-show; nested = inherit).

### IMP bookkeeping (the C18-style protocol, applied)

This task **registers IMP-U6** (Tier U — library beauty/metadata; next free code, U5 taken). It does
not close any existing IMP; on merge: `improvements_tierU.md` IMP-U6 → `Status: done (<date>, PR #)`,
PRIORITY.md row + graph node → done, ARCHITECTURE.md/README updated as documented behavior change
(Step 6). IMP-C23's drift-pin *tests* evolve but IMP-C23 itself stays done. Related-but-untouched:
IMP-E3 breadth (provider stamping breadth), IMP-S1 (Jellyfin — directly benefits), IMP-D22's
`--nfo` documentation (superseded default noted).

### §9 Original task prompt (verbatim — for the PR body)

> ok lets start with a task.
> USe the best multi model routing, for this task. No usage limit restriction.
> Task:
> For the tmdb link to each movie - or series or any other anime , currently we use the format - "Midnight in Paris.2011.BluRay {tmdb-59436}" with tmdb id inside flower brackets - {}.
> As you know I'm using plex pass lifetime, emby premium , and in future jellyfin to play these. Seems like square brackts to indiacte the number like "John Wick Chapter 4 (2023) [tmdb-603692]" seems to be the more
> starategic and generic path to handle this information or any other imdb or tvdb id. Can you change the default in our code to make {} to [] for all the places referenced inside this code.
> Also - note that - you need to create a deep plan to make sure all the code part is changed to reflect this - and in the future if I givce the Id in prep and ask it to rename the folder also it should put it inside [].
> let me know if any issues with this - also is it actually true for proper archival format to use square brackets.
> Once the change is completed - I also want you to do the actual  rename of all the archived movies and series - to use the rename functionality properly to change all existing ones.
> for now , just create the plan to do this. also give me proper plan and bbreakdown. for this change - branch out from main branch - do all the changes in a feature branch only. only touch the feature branch - not anything in main
> before testing. Also add proper tests - to make sure no other feature is touched or works as intended.
>
> Deliverables: PLAN.md only (no code, no branches) with steps + tests + verification + "Open Decisions". Note if we are solving any improvement tasks with this task say C18 for example - IMP-C18 needs to be marked done in improvements_tierC.md on implementation, the architect updates ARCHITECTURE.md/README (documented behavior change), and I want branch name, PR to main, and manual test commands at the end. Also you also need to update the priority graph and suggest me the next starts we can start.
> make each step, each execution or task resumable. each session or operation needs to be stored so that any limits if exceeded - i can continue after limits are back. DOnt worry about limits for this task. Come up with a complete - comprehensive plan. main goal is to not affect any existing functionalities.
>
> [2026-09-07 follow-ups, verbatim:]
> "yes for d1 - lets go with your suggestion - tmdbid-<id> … also this doesnt work in plex? but current version works?"
> "also can we create some file inside the folder , or inside the season (if different tmdb id for season) so that plex link also will stay but from the plex info file? and then do our change to [] for other 2 to work seamlessly?"

---

## 6. Resumability protocol (any session/limit interruption)

State lives in three synced places: root `/PLAN.md` (step ticks) · `docs/feature-token-brackets/PROGRESS.md`
(journal) · git history on `feature/imp_u6_token_brackets`. A fresh session resumes by:

1. Read PROGRESS.md `▶ NEXT ACTION` + Step-status table; read DECISIONS.md rulings.
2. `git status` — if a crashed agent left uncommitted edits, inspect `git diff` and reconcile
   against the Step table **(trust git over the table on disagreement)**; park or discard explicitly.
3. `git log --oneline main..HEAD` vs the table's Completing-SHA column; the last green gate output
   is recorded in the row's Tests column.
4. Re-run the last step's Verification command before continuing (never resume on assumed-green).
5. Never resume across a 🚦 human gate (Step 8 approval, PR merge) without the user's explicit word.

PROGRESS.md carries an append-only **Run history** (every interruption, gate outcome, dry-run/apply
logs) so the full operation trail survives any session break. Journal rows are updated in the SAME
commit as their step (`Commit: (this commit)` until push).

## 7. Suggested next starts after this ships

1. **IMP-C22** (Band 0, not gated) — anime per-episode enrichment mis-parse; `_episode_se_of` → shared helper.
2. **IMP-C24 ruling → implementation** — the top Band-0 hazard this task's rename loop had to respect.
3. **IMP-D23** — prep re-hash on resume (pairs with C24's window).
4. **IMP-S1** — Jellyfin stand-up, zero code: do it AFTER this migration lands so Jellyfin indexes the `[tmdbid-…]` names + NFOs natively on first scan.
5. **IMP-S2** — mvdaemon (the web worker seed).
