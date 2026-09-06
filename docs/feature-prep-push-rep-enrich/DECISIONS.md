# IMP-D22 `prep_push_rep_enrich` — Locked Decisions

> **All 7 decisions were RULED by the user on 2026-08-28 and are LOCKED.** No executor may
> re-open one. This file is the decision digest injected as block 2 ("LOCKED DECISIONS") of
> every v2 executor / judge dispatch. Full options-and-tradeoffs rationale lives in
> `PLAN.md`'s `## Decisions` section; this file is the operative summary.

---

## D1 — `-tvdbid` is refused, TMDB only

**Ruling:** accept `-tmdbid` only. `-tvdbid` gets a clear, immediate, actionable refusal —
nothing runs (no archive, no enrich). User's words: *"ok refuse - take only tmdb"*.

**Why it matters:** TVDB and TMDB ids are **different numbering spaces**. A TVDB id written
into `metadata.tmdb_id` would make `_resolve_unit_by_id` fetch a *different title* and download
its artwork — silent library corruption. The project has zero TVDB machinery to validate one.

**Evidence — the project is TMDB-for-everything (movies, series AND anime):** no TVDB client
exists; no `tvdb_id` is ever written. `cmd_enrich_metadata` stamps `{tmdb-…}` regardless of kind
(`main.py:2596` — no branch on `unit["kind"]`). TV resolves via TMDB TV endpoints
(`/3/search/tv`, `/3/tv/{id}`, `/3/tv/{id}/season/{n}`, `/3/tv/{id}/external_ids`). The user's
real folders agree: `Dark Season 01 (2017) {tmdb-70523}`, `The X-Files Season 01 (1993)
{tmdb-4087}`; all 145 anime entries carry a `tmdb_id`.

**The one contradiction, deliberately left alone:** `suggest_target_folder` (`main.py:7774`,
tag at `:7822`) proposes `{tvdb-000000}` for series/anime. It is a **web-console placeholder
string only** — no lookup, editable by the user — decided under IMP-E12
(`docs/feature-web-console/DECISIONS.md:18,47`) before the enricher existed, and pinned by
`tests/test_web_datafns.py:166,174`. It is NOT evidence of a TVDB convention. Do not change it.

---

## D2 — Resolve and prompt ONCE, immediately after the archive

**Ruling:** the folder-rename confirmation happens *after* the archive completes, not before.
User's words: *"after archive is fine."*

**Why:** rename-early is **impossible** — `cmd_rename_folder` refuses when no library entry
references the folder (`main.py:3624`), and the entry does not exist until `cmd_prep` runs.
Within apply-late, prompting *before* the archive would need a second, independent resolution
code path (the real one only operates on existing entries via `_gather_enrich_units`), which
would drift from `enrich_metadata` and would ignore curated `metadata.title`/`year` on an
already-partly-enriched show.

**Accepted cost:** the user is asked about the rename only after the archive finishes. Harmless
— declining never undoes the archive; only the rename is skippable.

---

## D3 — `--yes` / `--no-rename` + TTY detection, defaulting to "do not rename"

**Ruling:** explicit flags plus `sys.stdin.isatty()` detection; when non-interactive or
ambiguous, **do not rename**.

**Why it is load-bearing:** `main.py` contains **no `input()` anywhere** — the CLI is entirely
non-interactive today, so this is a new interaction pattern. `tests/smoke/test_smoke_all_commands.py`
drives every command; an unguarded `input()` would **hang the smoke gate**. Tests must pin that
`input()` is unreachable without an interactive TTY.

---

## D4 — `--nfo` OFF by default, AND the NFO writer is enriched (FINAL)

**Ruling:** default stays **OFF** (opt-in via `--nfo`), and `_write_nfo` itself is extended to
emit more fields. User's exact words: *"yes, there are some nfo examples in the media folder.
use that implement the nfo fetch also to get more info. so lets implement that also - but
default keep it off."*

**Shape reference — the user's own example**, `C:\Media\Series\English\Sci-Fi\Fringe (2008)
[tvdbid-82066]\tvshow.nfo`:

```xml
<?xml version="1.0" encoding="utf-8" standalone="yes"?>
<tvshow>
  <title>Fringe</title>
  <imdbid>tt1119644</imdbid>
  <tmdbid>1701</tmdbid>
  <tvdbid>82066</tvdbid>
</tvshow>
```

It carries **three** provider ids where `_write_nfo` (`main.py:2373`) today emits only a single
`<uniqueid type="tmdb">`.

**Element set to emit:** `title`, `year`, `plot`, `rating` (existing) plus plain `<tmdbid>`
alongside `<uniqueid type="tmdb" default="true">`; `<imdbid>` **and** `<uniqueid type="imdb">`
via the existing `_resolve_imdb_id`; `genre` (`_tmdb_genre_names`); `runtime`; `premiered`;
`studio` (movie: production companies / show: `_tmdb_network_names`); `director`
(`_tmdb_directors_from_crew`) and actor entries (`_tmdb_cast_names`).

**Binding constraints:**
- **`<tvdbid>` MUST NEVER be emitted** — no TVDB source exists (see D1). Tests assert its absence.
- Every added field is **optional**; omit the element cleanly when TMDB has no value. A failed
  `imdb_id` lookup omits the element rather than writing an empty one.
- `_write_nfo`'s existing **"NEVER raises"** contract is preserved verbatim.
- The default stays a **one-line flip**: `write_nfo=False` on both new command signatures plus
  the Step-3 dispatcher mirror. (That shape remains correct now the ruling is final — it was
  never conditioned on awaiting confirmation.)

**Scope note:** `_write_nfo` is shared, so this also changes the existing `enrich_metadata --nfo`
output. That is a **deliberate, desirable extension, not a regression** — and it is explicitly
carved out of the "existing behaviour byte-for-byte unchanged" guarantee, which covers the
autopilots and archive pipeline, NOT `_write_nfo`'s element set. `tests/test_enrich_metadata.py`
assertions that pin the current minimal element set must be updated.

**Supporting evidence:** a 2026-08-28 scan of the user's real library found **0
`movie.nfo` / `tvshow.nfo` files across all 173 distinct library folders** — NFO has never been
used in practice, so off-by-default matches actual usage. (The Fringe file sits at a show root
that is not one of those 173 `folder_path` values.)

---

## D5 — `_has_tmdb_token`'s case-sensitivity gap is OUT of scope

**Ruling:** leave it untouched. The user characterised folder-rename work as a separate feature.

**The gap (documented, not fixed):** `_has_tmdb_token` (`main.py:1687`) uses
`re.search(r"\{tmdb-[^}]+\}", name)` with **no `re.IGNORECASE`**, while `_PROVIDER_TOKEN_RE`
(`main.py:9032`) *has* it. The user's real folder `Run (2002)  - 4K SDR - (DD+5.1 - 192Kbps &
AAC)  {TMDB-69590}` therefore reads as "no token" and would get a **second** token appended.

Both new commands inherit exactly the behaviour `cmd_enrich_metadata` already has — this is a
pre-existing limitation, not a regression introduced here. Tracked in Suggested next tasks.

---

## D6 — Season variant is `prep_push_rep_season_enrich`, and season artifacts must all work

**Ruling:** name it `prep_push_rep_season_enrich` (append the qualifier to the stable base name,
matching `push`→`push_group`, `replace`→`replace_group`, `restore`→`restore_group`).

**Added scope requirement.** User's words: *"that enrich may be even more complex - like it
sometimes downloads season specific images, or data, or whole season one in root level, etc.
make sure all that works as expected."* Every artifact a series enrich produces must be
enumerated and pinned by a test — see PLAN.md's "Full artifact inventory" table.

**Two live-audit findings (2026-08-28) that reshaped the design:**

1. **Folder-layout duality.** The user's library has BOTH shapes. (i) Classic
   `<Show>/Season NN/` (e.g. `Dark Season 01 (2017) {tmdb-70523}`), where `_show_folder_of`
   climbs to the parent. (ii) **The dominant shape — 46 of the user's shows** — a single
   non-season-named release folder holding episodes directly (e.g. `Peaky.Blinders.S06.2022…
   {tmdb-60574}`, `Devs.S01.2020.2160p.WEB.HDR {tmdb-81349}`, `Chernobyl (Miniseries) 2019…
   {tmdb-87108}`). There `_show_folder_of`'s third branch returns that SAME folder as both season
   and show folder — the user's *"whole season one in root level"*. Show art and season art then
   target the identical path; the show `poster.jpg` is written first and the season poster is
   correctly skipped as "kept". **Graceful, not a bug — but both layouts are first-class test
   scenarios.** The "this is the SHOW folder — parent of the season" confirmation note is WRONG
   for layout (ii) and must be suppressed there.
2. **Per-season-year id convention breaks show-id derivation.** Real ids embed a different year
   per season: `tv-en-2022-peakyblinders-s06` vs `tv-en-2019-peakyblinders-s05`;
   `tv-en-2021-theexpanse-s06` vs `tv-en-2020-theexpanse-s05`. `_show_id_of` strips only the
   trailing `-sNN`, so these land in **separate** `_gather_enrich_units` buckets. Consequences:
   a season run essentially never reaches sibling seasons (smaller blast radius than feared),
   **but** deriving a show id buys nothing for finding a preset a prior run set on a sibling.
   **Therefore: scope by `base_id` directly, and make the CLI-supplied `-tmdbid` the primary
   mechanism for series.**

Note also that `cmd_set_tmdb` refuses a `season_map`, so presetting a CLI id for a season means
writing it to ONE episode leaf (`_unit_preset_tmdb_id` scans every id of the unit).

---

## D7 — An enrich-leg failure warns and continues

**Ruling:** warn-and-continue. User's words: *"enrich shd just be not done, but command can
finish."*

**Implementation:** (i) TMDB none/ambiguous/no-api-key outcomes are *already* warn-and-continue
inside `cmd_enrich_metadata` (it never raises for these). (ii) A `RollbackHardFail` from a
post-PONR `cmd_rename_folder` failure — the one genuine exception it can propagate — is caught
by the new commands and turned into a printed warning naming the exact resume command, never an
aborted return. (iii) Both commands' return value reflects **only** whether the archive
completed; the enrich leg reports via prints, exactly like `_push_title_extras_or_warn`.

---

## Standing guardrails (not decisions — project rules that bound every step)

- **Auto-rollback change-gate** (`CLAUDE.md`, `docs/feature-auto-rollback/ROLLBACK_MECHANISM.md`
  §10): this feature COMPOSES already-wrapped commands. It adds **no new PONR**, no journal-format
  change, no `recover_journal` change, no `RollbackHardFail` contract change. `main.py` has exactly
  3 `raise RollbackHardFail(` sites today and this plan adds zero. **If any approach is found to
  require touching `RollbackJournal`, a PONR location, or the journal format — STOP and ask the
  user.** Do not implement it.
- **No shared-data-contract change:** `ENTRY_TYPE_KEYS` (`main.py:166-169`, three types) is
  unaffected; `tests/test_entry_schema_guard.py` needs no change.
- **Smoke gate:** `pytest tests/smoke -q` must be green before any commit of a code-touching step
  and before the PR.
- **Never touch the real `C:\Media` or `library_*.json`** — tests use the `sandbox` fixture.
- **Commit hazard:** the user's personal `Master_Stream_Archiver*.py` / `MatchArchiver*.py` files
  are parked in `stash@{0}` for this run (see `PROGRESS.md`). While stashed the tree is clean and
  `git-agent` is safe. If the stash is ever restored mid-run, revert to explicit-pathspec commits.

---

## ADDENDUM 2026-09-07 (IMP-U6 — supersedes parts of D1/D4 above, no history rewritten)

- **D1 (the curly `{tmdb-…}` stamp) is superseded**: since IMP-U6 the stamp is the
  Jellyfin/Emby-native square form `[tmdbid-<id>]` (user-ruled D1 in
  `docs/feature-token-brackets/DECISIONS.md`). The legacy curly shape stays RECOGNIZED forever
  (D3 there) so pre-IMP-U6 folders are never double-stamped, and
  `tools/migrate_token_brackets.py` converts them.
- **D4's NFO context changes**: the stamp now writes the NFO **by default** (D6) — `--nfo`
  remains the force-even-without-a-stamp flag and `--no-nfo` opts out. `_write_nfo` gained a
  NEVER-overwrite guard (a local .nfo always wins), reversing the old "NFOs are regenerable"
  overwrite behavior documented above.
- The `-tvdbid` refusal, the confirmation gate, the richer NFO element set, and the
  never-`<tvdbid>` rule are all UNCHANGED.
