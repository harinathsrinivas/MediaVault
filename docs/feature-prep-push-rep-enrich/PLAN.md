# Task: Combined `prep_push_rep_enrich` (+ season variant) — archive-then-TMDB-enrich autopilot

Suggested branch: feature/imp_d22_prep_push_rep_enrich
Framework: v2

## Context

MediaVault has two "autopilot" commands — `prep_push_rep` (movie: prep -> push -> replace)
and `prep_push_rep_season` (whole TV/anime season, sequential) — and a separate
`enrich_metadata` command (TMDB backfill: real title/year, `metadata.tmdb_id`, a
`{tmdb-<id>}` folder-token stamp via `rename_folder`, poster/fanart/NFO). Today a user
who wants both must run the autopilot, then run `enrich_metadata <id> --apply` by hand.
This task adds `prep_push_rep_enrich` (movies) and `prep_push_rep_season_enrich` (TV/anime
seasons) that fold the enrich step onto the END of the existing autopilots, with an id
suppliable on the command line (`-tmdbid <id>`, no search) or auto-resolved exactly as
`enrich_metadata` does today, plus a NEW confirmation gate before the folder is renamed to
carry the `{tmdb-…}` token (the project's first interactive prompt — main.py currently has
zero `input()` calls).

The **primary constraint, restated because it drives every design choice below**: the
existing `prep_push_rep` / `prep_push_rep_season` commands must keep working byte-for-byte.
Every step is therefore written to COMPOSE the existing, already-rollback-wrapped commands
(`cmd_prep_push_rep`, `cmd_prep_push_rep_season`, `cmd_set_tmdb`, `cmd_enrich_metadata`,
`cmd_rename_folder`) rather than modify them, per the project's own D19/D20/D21/D17
precedent (write a sibling function, never touch the wrapped command's contract) and the
CLAUDE.md auto-rollback change-gate (no new PONR, no journal-format change).

## Goal

- New commands `prep_push_rep_enrich <id> "<filepath>" [SIZE_GB 8] [-tmdbid <id>]` (movies)
  and `prep_push_rep_season_enrich <id> "<folder>" [SIZE_GB 8] [episodes 1-5] [-tmdbid <id>]`
  (series/anime seasons), both non-interactively safe by default (never hang the smoke gate).
- `cmd_prep_push_rep` / `cmd_prep_push_rep_season` are **provably untouched** — zero lines
  changed in either function; the full existing test suite (incl. `test_baseline_happy_path.py`
  and the smoke suite's `TestEachCommand.test_prep_push_rep*`) passes unmodified. **Scope of
  this guarantee, restated precisely (Decision 4 follow-up, 2026-08-28):** "byte-for-byte
  unchanged" covers the ARCHIVE pipeline (`cmd_prep`/`cmd_push`/`cmd_replace` and their
  autopilot wrappers) end to end, and `cmd_enrich_metadata`'s own RESOLUTION logic and
  existing print/apply behaviour outside the NFO writer. It does **NOT** cover `_write_nfo`'s
  emitted element set, which Decision 4 deliberately extends (richer TMDB-sourced fields,
  still off-by-default, still `--nfo`-gated) — see Step 1. A later reviewer should read that
  as an intentional, ruled scope addition, not a violated invariant.
- An explicit id on the command line resolves TMDB by id (no search); no id falls back to
  `enrich_metadata`'s existing waterfall (title search -> wordninja -> EXA) verbatim.
- A folder lacking a `{tmdb-…}` token gets a confirmation gate (exact before/after path
  message, y/N) before any rename; declining leaves the folder untouched and the rest of
  the command still completes. Non-interactive invocations (no TTY, e.g. the smoke suite)
  default to **not** renaming unless `--yes` is passed.
- An enrich-leg failure (TMDB miss, ambiguous match, no API key, or even a rename hard-fail)
  **warns and continues** — the archive already succeeded and must never be treated as failed.
- `-tvdbid` is refused with a clear, actionable message (never silently written into
  `metadata.tmdb_id`, which is a TMDB-only field — see Decision 1).
- No new library entry type, no new shared JSON field, no rollback/journal/PONR change.
- Fully resumable across sessions/accounts via `docs/feature-prep-push-rep-enrich/PROGRESS.md`.

## Files affected

- `main.py` — two new top-level commands + 3 small helpers; ONE small additive hook
  candidate-dependent in `cmd_enrich_metadata` (see Step 1); two new `elif` dispatcher
  blocks + two usage-banner lines. **NEW (Decision 4, 2026-08-28): `_write_nfo` (main.py:2373)
  gains a richer, still-optional element set, and ONE tiny new helper `_tmdb_company_names`
  (mirrors the existing `_tmdb_genre_names`/`_tmdb_network_names` shape) is added next to its
  siblings — see Step 1.** `_resolve_imdb_id`, `_tmdb_detail_movie`, `_tmdb_detail_tv`,
  `_tmdb_genre_names`, `_tmdb_cast_names`, `_tmdb_directors_from_crew`, `_tmdb_network_names`
  are REUSED verbatim (zero changes — they already exist for the hover dossier, IMP-E16).
  **`cmd_prep_push_rep`, `cmd_prep_push_rep_season`, `cmd_set_tmdb`, `cmd_rename_folder`,
  `RollbackJournal`, `RollbackHardFail` are NOT touched.**
- `tests/test_prep_push_rep_enrich.py` (new) — movie command: full existing-flag regression
  matrix + new-behaviour scenarios.
- `tests/test_prep_push_rep_season_enrich.py` (new) — season command: same, show-centric.
- `tests/smoke/test_smoke_all_commands.py` — a new `TestPrepPushRepEnrich` class (2-4 cases).
- `ARCHITECTURE.md`, `README.md`, `docs/README.md` — new commands, the confirmation-gate
  pattern, and (new) an explicit "TMDB-for-everything" convention statement.
- `improvements/improvements_tierD.md`, `improvements/PRIORITY.md`,
  `docs/priority-graph/priority-graph.html` — register IMP-D22, marked `done` on shipping.
- `docs/feature-prep-push-rep-enrich/PLAN.md`, `DECISIONS.md`, `PROGRESS.md` (new — created
  by Step 0, the tracked/canonical copies per the PLAN.md location convention).

## Approach

Both new commands follow the same 3-phase shape:

1. **Refuse fast.** If `-tvdbid` was supplied, print the refusal and return — nothing else
   runs (no archive, no enrich). This is a user-input error, not a resolution failure, so it
   gets the same "abort before doing anything" treatment as `cmd_prep`'s
   file-not-found guard.
2. **Archive, unchanged.** Call `cmd_prep_push_rep` / `cmd_prep_push_rep_season` with every
   existing parameter passed straight through, completely unmodified. This is the ONLY way
   to guarantee byte-for-byte existing behaviour — no wrapper re-derives or duplicates any
   part of the prep/push/replace/extras pipeline.
3. **Enrich, gated, warn-and-continue.** Reload the library and check whether THIS
   invocation's target actually finished archiving (movie: `status == "archived"`; season:
   every episode this run targeted — honouring `episode_range` — reached `archived`). If not,
   print a clear "enrich skipped, archive incomplete" note and stop (no partial enrich on an
   unfinished archive). If it did: optionally preset `metadata.tmdb_id` via the EXISTING,
   unmodified `cmd_set_tmdb` (on the movie's own leaf, or — because `set_tmdb` refuses a
   season_map — on one episode leaf of the season, which `enrich_metadata`'s show-centric
   unit-gathering then finds regardless of which leaf carries it), then call
   `cmd_enrich_metadata(scope, "--apply", ...)` — the SAME command a user would run by hand —
   with a **confirmation gate on the folder rename only**. `RollbackHardFail` from a
   post-PONR `rename_folder` failure is caught and turned into a warning naming the existing
   resume command; it must never abort the whole autopilot (the archive already succeeded).

The confirmation-gate mechanism itself (how a "decline" actually prevents
`cmd_enrich_metadata`'s otherwise-unconditional stamp) is a genuine two-school design
question — see Step 1's multi-candidate split.

### Why "prompt-early" (before the archive) was considered and rejected

The task material flags "prompt-early / apply-late" as the safer alternative to
renaming before `cmd_prep` runs (impossible anyway — `cmd_rename_folder` refuses when no
library entry references the folder). "Apply-late" (perform the rename only after the
library entry exists) is adopted without reservation. A literal "prompt-early" (asking
the y/N question BEFORE the multi-GB archive starts) was evaluated and **rejected** in
favour of resolving+prompting once, right after the archive, for two concrete reasons:
(1) **Fidelity.** `enrich_metadata`'s resolution prefers an already-curated
`metadata.title`/`year` on the entry over an id-derived guess. Before `cmd_prep` runs there
IS no entry (for the common brand-new-title case this happens to converge on the same
query, but for a title that already exists with curated metadata — e.g., a second
`prep_push_rep_season_enrich` run on a later season of an already-partly-enriched show — a
pre-archive synthetic resolution would silently diverge from "exactly as `enrich_metadata`
does today"). (2) **Simplicity.** Prompting early would require a SECOND, independent
resolution code path (since the real one only operates on existing entries via
`_gather_enrich_units`), doubling the surface that must stay in sync. The accepted cost:
the user is asked about the rename only after the archive completes, not before. Since
declining never un-does the archive (it already happened; only the rename is skippable),
there is no actual harm — see Decision 2.

### Why the series (season) variant is harder than the movie variant

Requirement 5 asks this to be stated explicitly. **Revised 2026-08-28** after the user ruled
on Decision 6 and a live audit of the user's REAL library materially changed the picture —
the season variant is harder for FIVE reasons now, two of them (1 and 2) rewritten from the
original draft because the original draft's assumptions did not survive contact with the
real data:

1. **Folder-layout duality — `_show_folder_of`'s branch selection actually matters.**
   *(Verified against the user's real library 2026-08-28 — supersedes the original
   single-layout assumption.)* The user's library has BOTH shapes side by side: (i) the
   classic `<Show>/Season NN/` structure the code's docstring assumes (e.g.
   `Dark Season 01 (2017) {tmdb-70523}`, `The X-Files Season 01 (1993) {tmdb-4087}`), where
   `_show_folder_of` climbs from the season folder to its PARENT (the show folder); and
   (ii) — the DOMINANT shape, **46 of the user's shows** — a single, non-season-named
   release folder holding the episodes DIRECTLY, e.g.
   `Peaky.Blinders.S06.2022.2160p.iP.WEB-DL.x265.10bit.HDR.HLG.DDP5.1-FLUX[rartv]
   {tmdb-60574}`, `The.Expanse.S06.2021.2160p.AMZN.WEB-DL.x265.10bit.HDR10Plus.DDP5.1-MZABI
   [rartv] {tmdb-63639}`, `Devs.S01.2020.2160p.WEB.HDR {tmdb-81349}`,
   `Chernobyl (Miniseries) 2019 2160p.DTS-HD.MA.5.1.DV {tmdb-87108}`. For shape (ii),
   `_show_folder_of`'s third branch returns THAT SAME folder as both the season folder AND
   the show folder — the user's own words for this were *"whole season one in root
   level."* Consequence: the show-level `poster.jpg`/`fanart.jpg` write and the per-season
   poster write target the IDENTICAL path. `_download_unit_images` writes the show poster
   FIRST; the per-season loop's `os.path.exists(dest)` check is then already True, and the
   season poster is (correctly) skipped as "kept" — the single `poster.jpg` that results is
   the SHOW-level artwork, not a season-specific image. This is graceful degradation, not a
   bug, but it means BOTH layouts are first-class scenarios, not an edge case footnote — see
   Step 2's revised "Confirmation gate note" bullet (the "this is the SHOW folder — parent of
   the season" note is WRONG and must be suppressed for layout (ii), since there they are the
   literal same folder) and Step 5's expanded test matrix.
2. **The show-centric "find a sibling season's preset" design does NOT reliably work for
   this user's id convention — scope by `base_id`, not a derived show id.**
   *(Verified against the user's real library 2026-08-28 — supersedes the original draft's
   "scope by `_show_id_of`" design.)* The user's real series ids embed the YEAR PER SEASON,
   not one show-level year: `tv-en-2022-peakyblinders-s06` vs. `tv-en-2019-peakyblinders-s05`;
   `tv-en-2021-theexpanse-s06` vs. `tv-en-2020-theexpanse-s05`. `_show_id_of` strips only the
   trailing `-sNN`, so these yield DIFFERENT derived show ids
   (`tv-en-2022-peakyblinders` vs. `tv-en-2019-peakyblinders`) and land in SEPARATE
   `_gather_enrich_units` buckets — each season is its OWN unit under this convention. This
   cuts both ways: it REDUCES the original blast-radius worry (a season run essentially never
   reaches into a sibling season, because they are different units) — but it ALSO means
   deriving a show id buys NOTHING for finding a preset a prior run set on a sibling season
   (that preset lives in a different unit with a different key), and for `_show_folder_of`'s
   own computation it changes nothing either (a unit scoped by `base_id` directly still yields
   exactly one season entry in `unit["seasons"]`, identically to scoping by a derived show id,
   for this id convention — so deriving `show_id` at all bought nothing real). **Design
   consequence (binding on Step 2): scope `enrich_metadata`/the resolve waterfall directly by
   `base_id`** (this season's own season_map id) — the SAME pattern the movie command uses
   (`id_or_prefix=real_id`) — rather than deriving and scoping by a computed show id. This is
   simpler than the original design AND avoids a subtle, rare failure mode the original design
   would have had (accidentally conflating two sibling seasons that happen to share the same
   embedded year into one unit). `-tmdbid` on the command line is therefore the PRIMARY,
   reliable mechanism for enriching a series/season — automatic sibling-season preset reuse is
   explicitly NOT attempted and is documented as an accepted, by-design limitation (Step 5
   pins this with a "scope isolation" regression test), not a gap to silently paper over.
3. **`set_tmdb` cannot target a season_map.** `cmd_set_tmdb` explicitly refuses a
   `season_map` container ("targets a leaf entry, not a season_map container"). Presetting a
   CLI-supplied `-tmdbid` therefore requires picking ONE episode leaf of `base_id`'s children
   (any one — `_unit_preset_tmdb_id` scans every id of the unit and uses the first truthy hit)
   rather than the season id itself. A movie's CLI id is always its own leaf.
4. **"Did THIS run finish" is multi-item, not a single boolean.** A movie's completion check
   is `status == "archived"`. A season can be partially processed (an `episode_range`
   sub-selection, or a mid-loop failure that stops `cmd_prep_push_rep_season` early and
   prints a resume command) — the new command must reconstruct the SAME
   `episode_range`-filtered, de-aliased target-id list `cmd_prep_push_rep_season` computes
   internally (that function does not expose it) and require ALL of them archived before
   attempting enrich.
5. **More network work per unit, writing to more DISTINCT places than a movie ever does.** A
   show pulls per-season posters (`GET /3/tv/{id}/season/{n}/images`) and per-episode stills
   (`GET /3/tv/{id}/season/{s}/episode/{e}/images`, `_download_unit_images`'s show branch)
   plus per-episode overview/title backfill (`_apply_episode_overviews`, one cached
   `GET /3/tv/{id}/season/{n}` per season) — not just materially more TMDB calls than a
   movie's single poster/fanart pair, but artifacts landing in THREE distinct places (the show
   folder, each season folder, and next to each episode file) that must each be verified
   independently — see the full inventory immediately below.

### Full artifact inventory for a series/season enrich (verified 2026-08-28 — Step 5 must test every row, in BOTH folder layouts from reason 1 above)

The user's added requirement under Decision 6 — "make sure all that works as expected" for
season-specific images/data/root-level cases — means every artifact a series enrich can
produce must be explicitly enumerated and pinned by a test, not assumed to work because the
underlying helper functions are individually well-tested elsewhere. This is the complete list
(all of it existing, unmodified `main.py` machinery this feature composes — nothing here is
new code):

| # | Artifact | Lands at | Source | Notes |
|---|---|---|---|---|
| 1 | `poster.jpg` (w342) + `fanart.jpg` (w780) | the SHOW folder per `_show_folder_of` (main.py:1807) | the unit's own confident-match `res["poster_path"]`/`res["backdrop_path"]` | For layout (ii) (flat/root-level) this IS the folder the episodes sit in. |
| 2 | `{tmdb-…}` token stamp | same SHOW folder | `cmd_rename_folder`, gated by the confirmation prompt | ONE stamp regardless of layout. |
| 3 | per-season `poster.jpg` | EACH season folder | `GET /3/tv/{id}/season/{n}/images` -> `posters[0].file_path`; season number via `_season_number_of` parsing the season_map id's `-sNN` suffix (main.py:1710) | For layout (ii), this is the SAME path as #1 — `os.path.exists` already True -> skipped as "kept", NOT an error, NOT overwritten. |
| 4 | per-episode `<episode-basename>-thumb.jpg` | next to EACH episode video file | `GET /3/tv/{id}/season/{s}/episode/{e}/images`, best still chosen by `_pick_still_path`; filename via `_episode_thumb_name` (main.py:1773) | Unaffected by layout — always keyed off the episode's own folder + filename, never `poster.jpg`. |
| 5 | `metadata.overview` + `metadata.episode_title` | EACH episode leaf (JSON, not disk) | ONE cached `GET /3/tv/{id}/season/{n}` per season via `_apply_episode_overviews` (main.py:~3392); never raises, degrades to the show synopsis on a failed call | |
| 6 | `metadata.tmdb_id` + real `title`/`year`/`overview` | EVERY leaf AND season_map of the unit | the confident match result | Same for both layouts. |
| 7 | `tvshow.nfo` | the SHOW folder | `_write_nfo`, only with `--nfo` (Decision 4 — LOCKED, off by default) | Richer element set (Decision 4, 2026-08-28): `<title>`, `<year>`, `<plot>`, `<rating>`, plain `<tmdbid>` + `<uniqueid type="tmdb" default="true">`, `<imdbid>` + `<uniqueid type="imdb">` (omitted, not empty, on a failed/None lookup), `<genre>`, `<runtime>`, `<premiered>`, `<studio>` via `_tmdb_network_names`, `<director>`/`<actor>` entries; `<tvdbid>` never emitted (Decision 1). |

**LOCAL-ALWAYS-WINS must be pinned for every row that writes a file** (1, 3, 4, 7): a
pre-existing file of each kind, seeded with known bytes before the enrich leg runs, must be
byte-identical afterward (mirrors `test_enrich_metadata.py`'s own
`test_apply_never_overwrites_local_poster` / `test_apply_never_overwrites_existing_episode_still`).

### No shared-data-contract change

Neither command adds, renames, or removes a library entry type or a shared field.
`ENTRY_TYPE_KEYS` (main.py:166-169, three types: `leaf`/`season_map`/`multi_ep_alias`) is
unaffected; `tests/test_entry_schema_guard.py` needs no change. Per planner.md's own rule,
the `## Consumer Impact Analysis` section is therefore OMITTED (not applicable) — this line
is the explicit "say so."

### No rollback/auto-rollback contract change

Grep confirms exactly 3 `RollbackHardFail(` raise sites in `main.py` today (rename_folder,
cmd_replace, replace_one_extra) — this plan adds ZERO new ones. Every mutation this feature
performs runs through an EXISTING, unmodified, already-journaled primitive
(`cmd_prep_push_rep(_season)`, `cmd_set_tmdb` — a zero-byte JSON edit with no journal at
all, `cmd_rename_folder` — its own existing journal/PONR). Step 1's Candidate B touches
`cmd_enrich_metadata`'s SIGNATURE (one new keyword-only, default-`None` parameter) and ONE
`if` block around its EXISTING `cmd_rename_folder(...)` call site — it does not add a
journal, a PONR, or change `RollbackJournal`/`TXN_JOURNAL_NAME`/`recover_journal`/the
`RollbackHardFail` class in any way. **If any candidate, during implementation, is found to
require touching `RollbackJournal`, a PONR location, or the journal format to satisfy this
task, STOP and ask the user per the change-gate — do not implement it.**

## Fable reachability protocol (standing rule — consulted every session)

Fable availability was verified for THIS planning session (`FABLE_PROBE_OK`), but the user
wants this re-checked **every time this plan is executed**, including on a resumed/cold
session. Before dispatching the FIRST `[model: fable]` step in any session:

1. Dispatch a trivial probe: a `Task` to `executor-fable` with a prompt that does no file
   work and asks it to reply with EXACTLY the sentinel `FABLE_PROBE_OK` and nothing else.
2. If the probe returns `FABLE_PROBE_OK` — fable is available this session; proceed to
   route `[model: fable]` steps to `executor-fable` normally.
3. If the probe errors, times out, or returns anything else (including a message indicating
   a weekly/usage limit) — fable is UNAVAILABLE this session. **Fall back to `executor-opus`
   at max/xhigh effort for every step this plan marks `[model: fable]`**, and record the
   substitution explicitly in `docs/feature-prep-push-rep-enrich/PROGRESS.md` (a dedicated
   "Fable availability" line for that session, plus a note on each affected step's row).
4. This is NOT a one-time Step-0-only check — Step 0 performs the FIRST probe of the run,
   but a session that resumes hours/days later (per the Resume Protocol below) MUST re-run
   the probe before touching Step 1 or Step 2 (the two fable-tagged steps) again, even if a
   PRIOR session already recorded `FABLE_PROBE_OK`. Availability can change between runs
   (weekly limits reset/trip); never trust a stale probe result across a session boundary.

## Steps

- [x] 0. [model: sonnet] [effort: medium] Fable reachability probe (first of this session) + scaffold the resumability journal.
  - Depends on: nothing (first step).
  - Consumed by: every later step reads `docs/feature-prep-push-rep-enrich/PROGRESS.md` for
    resume state; Steps 1-8 each update it at dispatch (intent) and completion (outcome).
  - Establishes convention: the PROGRESS.md step-table format (columns: Step | Description |
    Status | Completing SHA | Tests | Notes) — every later step's journal update MUST use
    this exact table, matching `docs/feature-extras/PROGRESS.md`'s proven shape.
  - Files: `docs/feature-prep-push-rep-enrich/PLAN.md` (new — copy of the final root
    `/PLAN.md`), `docs/feature-prep-push-rep-enrich/DECISIONS.md` (new),
    `docs/feature-prep-push-rep-enrich/PROGRESS.md` (new).
  - Details: **May be performed by the orchestrator directly** (mirrors the IMP-D19 Step
    0/Step 14 precedent — file scaffolding + a Task-dispatch probe are both orchestrator
    capabilities) **or** dispatched to `executor-sonnet`; either is acceptable, but the
    fable-probe sub-step (see the standing protocol above) MUST be performed by the
    orchestrator itself since spawning a `Task` is not an executor capability. (a) Run the
    Fable reachability probe; record the result. (b) Copy the (possibly user-adjusted) root
    `/PLAN.md` verbatim into `docs/feature-prep-push-rep-enrich/PLAN.md`. (c) Write
    `DECISIONS.md` recording the 7 Decisions below: the user RULED on all 7 on 2026-08-28 (see
    the `## Decisions` section) — ALL 7 are LOCKED verbatim as ruled, including Decision 4
    (`--nfo` default): the FINAL RULING is OFF by default, kept to a one-line-flip default
    (that implementation shape remains correct now the ruling is final — it was never
    conditioned on awaiting confirmation) — cite the 2026-08-28 real-library evidence, 0 NFO
    files across 173 folders, as the supporting record. Also cite the 2026-08-28 real-library
    audit findings behind Decision 6's added scope (the flat/root-level folder layout and the
    per-season-year id convention — see "Why the series variant is harder" / "Full artifact
    inventory" under Approach) as DECISIONS.md's evidence base for the Step 2/5 redesign.
    (d) Write `PROGRESS.md` with: the Status/Branch/Plan/Decisions header block (of
    the exact shape `docs/feature-extras/PROGRESS.md` uses), a `▶ NEXT ACTION` pointer (initially
    "Step 1"), an empty Step table pre-populated with rows for Steps 0-9 (status=`pending`
    except Step 0 itself), the Resume Protocol (5 numbered steps, mirroring
    `docs/feature-extras/FIXES_PROGRESS.md`'s: fetch/checkout branch -> read PLAN+DECISIONS+PROGRESS
    -> reconcile git log against the table (trust git on disagreement) -> resume at the first
    non-`done` step, honouring any `in_progress` sub-state notes -> finish a step by updating +
    committing PROGRESS.md and ticking PLAN.md's checkbox in the SAME commit), and a
    "Blockers / human gates" block (Checkpoint 1 merge gate, Checkpoint 2 archive gate). (e)
    git-agent creates the feature branch (`feature/imp_d22_prep_push_rep_enrich`) and commits
    these three files with an explicit pathspec (standing hazard — see Verification).
  - Acceptance: all three files exist under `docs/feature-prep-push-rep-enrich/`, committed
    on the new branch; the Fable-probe result is recorded in PROGRESS.md; `git status` clean
    apart from the pre-existing staged `Master_Stream_Archiver*`/`MatchArchiver*` hazard.

- [ ] 1. [model: fable] [effort: xhigh] [candidates: 2] Core enrich-composition mechanism + `cmd_prep_push_rep_enrich` (movie command).
  - Depends on: Step 0 (branch exists).
  - Consumed by: Step 2 (reuses whichever candidate wins, applied to the season case), Step 3
    (CLI wiring — depends on the EXACT function signature below), Step 4 (tests), Step 7
    (docs describe the winning mechanism).
  - Establishes convention (BINDING on Steps 2/3/4/5 regardless of which candidate wins —
    only the internals may differ):
    ```
    def cmd_prep_push_rep_enrich(manual_id, filepath, split_method=None, split_val=None,
                                   device_id=None, eager_rehash=False, temp_dir=None,
                                   extras=None, extras_size=None,
                                   tmdb_id=None, tvdb_id=None,
                                   write_nfo=False,  # Decision 4 (2026-08-28, LOCKED): OFF
                                                      # by default; kept a one-line flip (this
                                                      # value + its Step-3 dispatcher mirror)
                                                      # in case a future ruling changes it.
                                                      # The NFO CONTENT enrichment below is
                                                      # independent of this default.
                                   no_web=False,
                                   rename_choice="ask"):
        """Returns True once the archive itself completed (regardless of whether the
        enrich leg fully succeeded — warn-and-continue). Returns False if the archive
        did not complete (enrich is then skipped entirely) or if -tvdbid was supplied
        (refused before anything runs)."""
    ```
    `rename_choice` is one of the literal strings `"ask"` (default — interactive prompt
    when `sys.stdin.isatty()`, else auto-"no"), `"yes"` (from CLI `--yes` — auto-confirm),
    `"no"` (from CLI `--no-rename` — auto-decline). Both new top-level functions and their
    two small shared helpers (`_refuse_tvdbid()`, `_make_rename_confirm(choice, note=None)`)
    live in `main.py`, inserted immediately AFTER `cmd_prep_push_rep_season` ends and BEFORE
    `cmd_dispatch_fetch` begins (grep `"^def cmd_prep_push_rep_season\b"` /
    `"^def cmd_dispatch_fetch\b"` to confirm — verified this session at lines 7261-7456 /
    7459, but re-verify; do not trust the number alone).
  - Files: `main.py` (new: `cmd_prep_push_rep_enrich`, `_refuse_tvdbid`,
    `_make_rename_confirm`; Candidate B additionally edits `cmd_enrich_metadata`'s signature
    + one `if will_stamp:` block, both at main.py:~2412-2648 — grep `"^def cmd_enrich_metadata"`
    to confirm. ALSO MODIFIED, identically by BOTH candidates — orthogonal to the A-vs-B
    fork, excluded from judging (see Judge criteria): `_write_nfo` (main.py:2373, grep
    `"^def _write_nfo"` to confirm) for the NFO element-set extension below; new helper
    `_tmdb_company_names`, added next to `_tmdb_genre_names`/`_tmdb_network_names`
    (main.py:~8366, already earmarked here by Files affected and Step 7), for a movie's
    `<studio>`).
  - Details:
    - **`-tvdbid` refusal** (`_refuse_tvdbid()`): if `tvdb_id is not None`, print (exact
      wording, both candidates MUST match so tests can assert a stable substring):
      ```
      ❌ -tvdbid is not supported: MediaVault is TMDB-only for movies, series, and anime
         (no TVDB client exists, and a TVDB id is a DIFFERENT numbering space from a TMDB
         id — writing it into metadata.tmdb_id would silently corrupt the entry / fetch the
         wrong title's artwork). Look the title up on themoviedb.org and pass -tmdbid <id>.
      ```
      and return `False` immediately — `cmd_prep_push_rep` must NOT be called.
    - **Archive phase**: `cmd_prep_push_rep(manual_id, filepath, split_method, split_val,
      device_id=device_id, eager_rehash=eager_rehash, temp_dir=temp_dir, extras=extras,
      extras_size=extras_size)` — every argument forwarded, nothing added, nothing dropped.
      Do not inspect or rely on its return value (it returns `None` on some paths today).
    - **Completion check**: reload `library = load_library()`; if `manual_id not in library`,
      warn "prep did not create a library entry" and return `False`. Else
      `real_id, entry = _resolve_alias(library, manual_id)`; if
      `entry.get("status") != "archived"`, print a warning naming `real_id`, its current
      `status`, and the exact remedy (`enrich_metadata {real_id} --apply` once the archive is
      finished, or simply re-run this same command), then return `False` (enrich never
      attempted). This is the warn-and-continue boundary for "the archive itself didn't
      finish" — distinct from "the archive finished but enrich failed" below.
    - **Preset (only when `tmdb_id is not None`)**: `cmd_set_tmdb(real_id, tmdb_id)` —
      called EXACTLY as a user would type `set_tmdb <id> <tmdb_id>`; do not reimplement any
      part of it.
    - **Enrich + confirm + apply**: build `gate = _make_rename_confirm(rename_choice)`
      (`note=None` for the movie case) and perform the resolve+apply+confirm sequence per
      whichever candidate below is chosen, scoped to `real_id`. Wrap ONLY this call in
      `try: ... except RollbackHardFail as hf:` — on catch, print
      `f"⚠️  Enrich folder rename left incomplete: {hf.state} — {hf.reason}"` and
      `f"   > To finish it: {hf.resume_cmd}"`, then fall through to the success banner (do
      NOT re-raise, do NOT return False — the archive already succeeded).
    - Final: always print `"\n✅✅✅ AUTO-PILOT COMPLETE (archive + enrich)."` when the archive
      itself completed (even if enrich warned), and return `True`.
    - **Candidate A — isolated, zero edits to `cmd_enrich_metadata`.** Never call
      `cmd_enrich_metadata` in `--apply` mode. Reuse ONLY the already-standalone primitives:
      `_gather_enrich_units(library, id_or_prefix=real_id)` (expect exactly one unit — a
      movie never has siblings sharing its exact id, modulo the pre-existing
      startswith-prefix edge case `enrich_metadata` itself already has, which is NOT this
      task's to fix), then the SAME resolve waterfall `cmd_enrich_metadata`'s loop body
      uses (`_unit_preset_tmdb_id` -> `_resolve_unit_by_id` when preset; else `_resolve_unit`
      then, if `not no_web` and `mvcommon.exa_api_key()` and status is `none`/`ambiguous`,
      `_exa_resolve_tmdb_id` + a validating `_resolve_unit_by_id` call — mirror
      `cmd_enrich_metadata`'s exact branching, main.py:~2486-2506), then the SAME
      apply steps: write `tmdb_id`/`title`/`year`/`overview` onto every id in
      `unit["ids"]` (mirror main.py:~2572-2591's loop verbatim — same
      `_title_is_id_shaped`/`cur == tmdb_title` guard), compute `will_stamp` (same formula:
      `bool(folder) and not _has_tmdb_token(base_name)`), call `gate(folder, new_folder)` and
      only call the UNMODIFIED `cmd_rename_folder(folder, new_name)` when it returns `True`
      (print a `"rename declined"` note otherwise), then call the ALREADY-standalone
      `_download_unit_images`, and if `unit["kind"] == "show"` `_apply_episode_overviews`,
      and if `write_nfo` `_write_nfo`. Must ALSO replicate `cmd_enrich_metadata`'s own
      "no TMDB API key -> print + bail gracefully" guard independently (do not assume the
      caller checked). Zero lines of `cmd_enrich_metadata` touched.
    - **Candidate B — minimal additive hook in `cmd_enrich_metadata`.** Add ONE keyword-only
      parameter to its signature: `def cmd_enrich_metadata(arg=None, *flags,
      confirm_rename=None):` (existing callers — the CLI dispatcher, every existing test —
      never pass it, so `confirm_rename` is always `None` for them: PROVABLY unchanged
      behaviour via short-circuit). In the APPLY block, replace the existing
      ```
      if will_stamp:
          new_name = f"{base_name} {{tmdb-{tmdb_id}}}"
          ok = cmd_rename_folder(folder, new_name)
          if ok:
              ...
      ```
      with
      ```
      if will_stamp:
          new_name = f"{base_name} {{tmdb-{tmdb_id}}}"
          new_folder_full = os.path.join(os.path.dirname(os.path.normpath(folder)), new_name)
          do_stamp = True if confirm_rename is None else confirm_rename(folder, new_folder_full)
          if do_stamp:
              ok = cmd_rename_folder(folder, new_name)
              if ok:
                  ...  # unchanged body
          else:
              print(f"     ⏭️  folder rename declined — run rename_folder later to add the token.")
      ```
      (the unchanged inner body still computes `new_folder`/`unit`/`folder` exactly as
      today — only reuse the already-computed `new_folder_full` instead of recomputing it a
      second time). Add one docstring line noting `confirm_rename` is an internal/
      programmatic-only hook, not a CLI flag. Then `cmd_prep_push_rep_enrich` composes:
      `flags = ["--apply"]; write_nfo and flags.append("--nfo"); no_web and
      flags.append("--no-web"); cmd_enrich_metadata(real_id, *flags, confirm_rename=gate)`.
      Zero duplicated resolve/apply logic.
    - **NFO element-set extension (BOTH candidates, identical work — not a judging
      axis)**: extend `_write_nfo(folder, kind, title, year, tmdb_id, overview="",
      vote_average=None, api_key=None)` (main.py:2373; new `api_key` kwarg, threaded
      from the SAME `api_key` already in scope at the existing call site,
      main.py:~2624) to emit, in addition to today's `title`/`year`/`plot`/`rating`/
      `<uniqueid type="tmdb" default="true">`:
      - a plain `<tmdbid>` element alongside the existing `<uniqueid>` (the Fringe
        example's form — Kodi accepts both; see Decision 4).
      - `<imdbid>` AND `<uniqueid type="imdb">`, resolved via the EXISTING
        `_resolve_imdb_id(tmdb_id, kind, api_key)` (main.py:2672, already used by
        `cmd_refresh_online`) — movie -> `GET /3/movie/{id}`.`imdb_id`, tv ->
        `GET /3/tv/{id}/external_ids`.`imdb_id`; both funnel through the cached,
        None-on-failure `_tmdb_get` (main.py:1348).
      - `<genre>` (one element each) via `_tmdb_genre_names` (main.py:8366).
      - `<runtime>`, `<premiered>`.
      - `<studio>` — movie: production companies via the NEW small helper
        `_tmdb_company_names` (Files line above), mirroring `_tmdb_genre_names`/
        `_tmdb_network_names`'s exact shape; show: the EXISTING `_tmdb_network_names`
        (main.py:8433).
      - `<director>` via `_tmdb_directors_from_crew` (main.py:8395) and
        `<actor><name>…</name></actor>` entries via `_tmdb_cast_names`
        (main.py:8378). (All main.py line numbers above verified this session —
        grep `"^def <name>"` to reconfirm; do not trust a number alone.)
      - **`<tvdbid>` MUST NEVER be emitted** (Decision 1 — no TVDB source; a
        fabricated one is exactly the silent-corruption class Decision 1 refuses).
      - **Every added element is OPTIONAL** — omitted cleanly when TMDB has no value
        or `api_key` is falsy; a failed/None lookup omits the element rather than
        writing an empty one.
      - **`_write_nfo`'s existing "NEVER raises" contract is preserved verbatim**
        (main.py:2380-2382) — any lookup/IO failure degrades to a printed warning
        and a still-written (smaller) NFO.
      - State explicitly **which fields need an EXTRA TMDB call vs. which are
        already in the confident-match payload**: `title`/`year`/`overview`/
        `vote_average` are already in `res` (today's existing call params,
        unchanged); `imdbid` needs ONE extra call (`_resolve_imdb_id`);
        `genre`/`runtime`/`studio`/`director`/`actor` need the detail(+credits)
        endpoint(s) — prefer the already-cached `_tmdb_get` path over any new,
        uncached fetch.
      - Shared-helper consequence: this ALSO changes `cmd_enrich_metadata --nfo`'s
        existing output (`_write_nfo` is one shared function, reused verbatim by
        both callers) — deliberate and desirable, explicitly carved OUT of the
        "existing behaviour byte-for-byte unchanged" guarantee (which covers the
        autopilots and archive pipeline, NOT `_write_nfo`'s element set — see Files
        affected).
    - `_make_rename_confirm(choice, note=None)` returns a callable
      `confirm(old_folder, new_folder) -> bool`. Its body (identical regardless of which
      candidate wins — shared code):
      ```python
      def _confirm(old_folder, new_folder):
          print(f'   > "{old_folder}" will be changed to "{new_folder}"')
          if note:
              print(f"   > {note}")
          if choice == "yes":
              print("     > auto-confirmed (--yes).")
              return True
          if choice == "no":
              print("     > auto-declined (--no-rename) — leaving the folder as-is.")
              return False
          if not sys.stdin.isatty():
              print("     > non-interactive session — defaulting to NOT renaming "
                    "(pass --yes to auto-confirm, or run rename_folder yourself later).")
              return False
          answer = input("     > Rename this folder now? [y/N]: ").strip().lower()
          return answer in ("y", "yes")
      return _confirm
      ```
      The exact phrase `will be changed to` and the double-quoted full paths MUST match the
      task's required wording verbatim; wording elsewhere in the callback is not
      load-bearing. This is the ONE new `input()` call in the entire codebase — it is
      reached ONLY when `choice == "ask"` AND `sys.stdin.isatty()` is `True`, so a
      non-interactive invocation (any pytest run, the smoke suite, a cron/script call with
      no flag) NEVER calls `input()`.
  - Acceptance: both candidates satisfy — (1) `cmd_prep_push_rep`/`cmd_prep_push_rep_season`
    have a ZERO-line diff (`git diff main -- main.py` shows no change inside either
    function's body); (2) the EXISTING `pytest tests/test_enrich_metadata.py -q` suite
    (60+ tests) passes UNMODIFIED; (3) a self-written smoke-level test proves: id-supplied
    happy path (archive -> preset -> resolve-by-id -> confirm(yes, via `--yes`) -> rename ->
    poster downloaded); id-supplied + `--no-rename` leaves the folder name unchanged but
    still writes `metadata.tmdb_id`; `-tvdbid` refuses before any file touches disk (assert
    on the folder/library being byte-identical to before the call); a `RollbackHardFail`
    raised from a monkeypatched `cmd_rename_folder` is caught and printed, and the function
    still returns `True`. (4) zero new `RollbackJournal`/PONR/journal-format touches (grep
    the diff for `RollbackJournal(`, `mark_point_of_no_return`, `TXN_JOURNAL_NAME` — none
    outside the untouched existing call sites). (5) `tests/test_enrich_metadata.py`'s
    EXISTING NFO assertions must be reviewed; if any pin today's minimal element set — this
    is the one narrow, deliberate exception to (2)'s "UNMODIFIED" (only NFO-content
    assertions may change, nothing else in the file) — they must be updated IN THIS STEP
    (not deferred to Step 4/5/7), and the full suite must be green afterward. Also assert
    `<tvdbid>` never appears in ANY generated NFO (`movie.nfo` or `tvshow.nfo`). Each
    candidate self-reports all 5 checks in its `CRITIQUE.md`.
  - Judge criteria (ranked): (1) fidelity — `test_enrich_metadata.py` unmodified-and-green,
    and `cmd_prep_push_rep`/`cmd_prep_push_rep_season` provably zero-diff; (2) rollback
    change-gate compliance — no new journal/PONR/RollbackHardFail-class touch; (3) blast
    radius / surgical-changes — lines of EXISTING code touched (0 for A; the small, provably
    short-circuit-safe diff for B); (4) correctness on every scenario in Acceptance; (5)
    maintainability / DRY (duplication-drift risk for A vs. shared-logic for B) — lowest
    weight, a tiebreaker only. The NFO element-set extension specified above is IDENTICAL
    in both candidates (shared, non-forked work) and is therefore EXCLUDED from judging
    entirely — the judge compares ONLY the enrich-composition mechanism (the A-vs-B fork).
  - Candidate approaches:
    - A: Fully isolated reimplementation that never calls `cmd_enrich_metadata`'s apply
      path — reuses only the already-standalone low-level primitives
      (`_gather_enrich_units`, `_resolve_unit(_by_id)`, `_unit_preset_tmdb_id`,
      `_exa_resolve_tmdb_id`, `_download_unit_images`, `_apply_episode_overviews`,
      `_write_nfo`, `cmd_rename_folder`) and duplicates the small tmdb_id/title/year-write
      loop + the stamp-decision, so `cmd_enrich_metadata` is not touched at all.
    - B: Minimal additive hook — one new keyword-only `confirm_rename=None` parameter on
      `cmd_enrich_metadata` (short-circuits to today's unconditional stamp when `None`),
      with the new command composing `cmd_set_tmdb` + `cmd_enrich_metadata(..., "--apply",
      confirm_rename=gate)` directly — zero duplicated resolve/apply logic.

- [ ] 2. [model: fable] [effort: xhigh] `cmd_prep_push_rep_season_enrich` (season command), built on Step 1's winning mechanism.
  - Depends on: Step 1 (reuses its chosen candidate's internal pattern verbatim — do not
    re-open that design fork; apply the SAME strategy to the show-centric case).
  - Consumed by: Step 3 (CLI wiring), Step 5 (tests), Step 7 (docs).
  - Establishes convention (BINDING on Step 3/5):
    ```
    def cmd_prep_push_rep_season_enrich(base_id, folder_path, split_method=None, split_val=None,
                                          episode_range=None, device_id=None, eager_rehash=False,
                                          temp_dir=None, extras=None, extras_size=None,
                                          tmdb_id=None, tvdb_id=None,
                                          write_nfo=False,  # Decision 4 (2026-08-28, LOCKED): OFF
                                                             # by default; same one-line-flip
                                                             # contract as Step 1's signature.
                                          no_web=False,
                                          rename_choice="ask"):
    ```
    Same return contract as Step 1's function. New helper `_season_run_target_ids(library,
    base_id, episode_range)` (module-level, near this function) is the convention Step 5's
    tests may also exercise directly.
  - Files: `main.py` (new: `cmd_prep_push_rep_season_enrich`, `_season_run_target_ids`),
    placed immediately after `cmd_prep_push_rep_enrich` (Step 1's insertion point).
  - Details:
    - `-tvdbid` refusal: `_refuse_tvdbid()` (Step 1's helper, reused verbatim) before
      anything else.
    - Archive phase: `cmd_prep_push_rep_season(base_id, folder_path, split_method, split_val,
      episode_range, device_id=device_id, eager_rehash=eager_rehash, temp_dir=temp_dir,
      extras=extras, extras_size=extras_size)` — forwarded unmodified, matching the existing
      dispatcher's own call shape exactly (positional `episode_range` in the same slot).
    - `_season_run_target_ids(library, base_id, episode_range)`: reload the library; if
      `base_id not in library`, return `[]`. Otherwise **duplicate, verbatim, the exact
      target-id derivation `cmd_prep_push_rep_season` performs internally** (main.py:~7294-7319,
      grep `"^def cmd_prep_push_rep_season\b"` to re-locate) — `target_ids =
      library[base_id]["children"]`; if `episode_range`, `start, end =
      map(float, episode_range.split('-'))` inside a `try/except ValueError: return []`
      (an invalid range means `cmd_prep_push_rep_season` itself already printed "❌ Invalid
      range." and processed nothing — this helper degrading to "nothing to check" is
      correct, not a new failure mode), filter via `episode_num_from_id(mid, base_id)`
      exactly as the original; then de-alias via `_resolve_alias` + a `seen` set, exactly as
      the original. Comment this function as "mirrors cmd_prep_push_rep_season's own STEP 2
      — keep in sync; that function does not expose this list so it must be reconstructed
      identically."
    - Completion check: `target_ids = _season_run_target_ids(library, base_id,
      episode_range)`; if empty, warn "nothing was archived this run — enrich skipped" and
      return `False`. Else require EVERY id in `target_ids` (after `_resolve_alias`) to have
      `status == "archived"` in the freshly-reloaded library; on any that don't, warn (name
      the first incomplete id + its status) and return `False`.
    - **Enrich scope — REVISED 2026-08-28 (Decision 6 / "Why harder" reasons 1-2): scope
      directly by `base_id`, do NOT derive or scope by a show id.** Call
      `_gather_enrich_units(library, id_or_prefix=base_id)` (the SAME `id_or_prefix=<this
      unit's own id>` pattern the movie command uses) and expect exactly one unit — this
      season's own season_map + its own episode leaves. Do NOT call `_show_id_of` for scoping
      purposes (the original design's premise — that a derived show id would reach a sibling
      season's preset — does not hold for the user's real per-season-year id convention; see
      "Why harder" reason 2). `_show_folder_of` still receives the unit's own `seasons` dict
      (one entry) and correctly returns either the parent-of-`Season NN` folder (nested
      layout) or that SAME folder (flat/root-level layout) per "Why harder" reason 1 — this is
      unaffected by the scoping choice.
    - Preset (only when `tmdb_id is not None`): `cmd_set_tmdb` CANNOT target `base_id`
      (a `season_map` — it would refuse). Pick the first child of `library[base_id]["children"]`
      that resolves (via `_resolve_alias`) to a LEAF (has a `filename` key) and call
      `cmd_set_tmdb(<that leaf id>, tmdb_id)`. If `library[base_id]["children"]` is somehow
      empty (should not happen post-archive-completion-check), skip presetting and let the
      normal search waterfall run instead of crashing.
    - **Confirmation gate note — REVISED 2026-08-28 (conditional, per "Why harder" reason
      1): only show the "this is the SHOW folder" note when the show folder actually DIFFERS
      from the season folder.** Compare the unit's resolved `folder` (post
      `_gather_enrich_units`) against the season_map's own `folder_path` (both normalized via
      the same case/separator-insensitive comparison `_norm_path` already uses elsewhere in
      `main.py`). If they differ (the classic `<Show>/Season NN/` layout), build
      `gate = _make_rename_confirm(rename_choice, note="(this is the SHOW folder — the parent
      of the season you just archived)")`. If they are the SAME path (the flat/root-level
      layout — 46 of the user's real shows), build
      `gate = _make_rename_confirm(rename_choice, note=None)` — printing the "parent of the
      season" note would be FACTUALLY WRONG when they are the literal same directory, and
      would confuse rather than clarify.
    - Same `RollbackHardFail` catch-and-warn wrapping as Step 1, same final banner/return
      contract.
    - Explicitly confirm (state in a code comment) that `_season_resume_cmd` (the private
      closure inside `cmd_prep_push_rep_season`, which prints the season resume command on a
      mid-loop failure) is NOT touched, NOT called, and NOT reimplemented by this function —
      a season archive failure still prints the EXISTING resume command exactly as today; this
      new command adds nothing to that message (change-gate: the season resume-range
      messaging is explicitly listed as rollback-adjacent in `CLAUDE.md` — reinforcing why it
      must not be touched here).
  - Acceptance: `cmd_prep_push_rep_season` has a zero-line diff; a self-written smoke-level
    test proves, for BOTH the nested `<Show>/Season NN/` layout AND the flat/root-level
    layout (Decision 6 / "Why harder" reason 1): full-season id-supplied happy path completes
    without error and stamps the token on the correct folder (the parent, for nested; that
    SAME folder, for flat); an `episode_range` sub-selection run correctly reports "enrich
    skipped" only when the range itself did not finish — precisely: when the RANGE ITSELF
    fully archives, enrich proceeds even though sibling episodes outside the range are
    untouched (target_ids is range-scoped, not whole-season); a preset id set via `-tmdbid`
    lands on an episode LEAF (assert directly in the library), never attempted on the
    season_map; `_season_run_target_ids` returns `[]` (not a crash) on an invalid
    `episode_range` string; scoping by `base_id` (not a derived show id, per reason 2) means a
    SIBLING season's preset is correctly NOT reached by this unit — assert this explicitly
    (a positive proof of the revised, simpler design, not a bug). No new RollbackJournal/PONR
    touch.
  - Judge criteria: n/a (single-executor — the design fork was already resolved in Step 1;
    re-forking the SAME decision for the season case would not be genuine differentiation
    per the multi-candidate quality rules, so this step is intentionally NOT multi-candidate
    despite the no-limits policy).

- [ ] 3. [model: opus] [effort: high] CLI dispatcher wiring for both new commands.
  - Depends on: Step 1 (movie function signature), Step 2 (season function signature).
  - Consumed by: Step 4/5's tests may invoke either via direct function call OR via this
    dispatcher; Step 6 (smoke) exercises the dispatcher indirectly through `main.cmd_*`
    calls (the smoke suite calls `cmd_*` functions directly, not through `sys.argv`, per the
    existing `TestEachCommand` convention — confirm this remains true, do not introduce a
    subprocess-based smoke path).
  - Establishes convention: none new — this step MIRRORS the existing `prep_push_rep` /
    `prep_push_rep_season` dispatcher blocks (main.py:~9431-9550, grep
    `'elif cmd == "prep_push_rep"'` / `'elif cmd == "prep_push_rep_season"'` to re-locate)
    token-for-token, adding only the new `-tmdbid`/`-tvdbid`/`--yes`/`--no-rename`/`--nfo`/
    `--no-web` recognitions.
  - Files: `main.py` (the `if __name__ == "__main__":` block only).
  - Details: Insert TWO new `elif` blocks immediately after the existing
    `elif cmd == "prep_push_rep_season":` block ends and BEFORE `elif cmd == "set_search":`
    begins (main.py:~9552 — re-verify via grep). Each new block is a byte-for-byte copy of
    its sibling's existing token-walking loop (`SIZE_MB|SIZE_GB|COUNT`, `device`, `rehash`,
    `tempdir`, `episodes` for the season variant only, `--extras`/`-extras`/`--extras-size`/
    `-extras-size` exactly as today) PLUS new `elif` arms inside the SAME while-loop:
      - `arg in ("-tmdbid", "--tmdbid")`: consume the next token as `cli_tmdb_id`.
      - `arg in ("-tvdbid", "--tvdbid")`: consume the next token as `cli_tvdb_id`.
      - `arg == "--yes"`: `rename_choice = "yes"`.
      - `arg == "--no-rename"`: `rename_choice = "no"`.
      - `arg == "--nfo"`: `write_nfo_flag = True`.
      - `arg == "--no-web"`: `no_web_flag = True`.
    Everything NOT matched still falls through to `filepath_parts.append(arg)` /
    `folder_parts.append(arg)` exactly as today (do not add an `else: continue` that would
    change the fallthrough). Call the new `cmd_prep_push_rep_enrich(...)` /
    `cmd_prep_push_rep_season_enrich(...)` with EVERY parsed value, using
    `resolve_device(device_arg)` for `device_id` exactly as the sibling blocks do, and
    `parse_extras_tokens(...)` exactly as the sibling blocks do. Also add TWO new lines to
    the usage banner printed when `len(sys.argv) < 2` (main.py:~9369-9370, immediately after
    the existing `prep_push_rep_season` usage line), matching the existing terse style, e.g.:
    ```
    print("  prep_push_rep_enrich [id] [filepath] [SIZE_GB/SIZE_MB/COUNT val] [device <id_or_name>] [rehash] [tempdir <path>] [-tmdbid <id>] [--yes|--no-rename] [--nfo] [--no-web]  — archive then TMDB-enrich; no id -> auto-resolve exactly like enrich_metadata")
    print("  prep_push_rep_season_enrich [id] [folder] [SIZE_GB/SIZE_MB/COUNT val] [episodes <range>] [device <id_or_name>] [rehash] [tempdir <path>] [-tmdbid <id>] [--yes|--no-rename] [--nfo] [--no-web]  — season autopilot, then show-centric enrich")
    ```
  - Acceptance: `git diff main -- main.py` shows the EXISTING `prep_push_rep` /
    `prep_push_rep_season` `elif` blocks with ZERO changed lines (only new, separate `elif`
    blocks added); `python main.py` (no args) prints the two new usage lines; a manual
    `python main.py prep_push_rep_enrich` (too few args) prints a `❌ Usage:` message and
    exits 1 without touching the library. Run `pytest tests/test_cli_parsers.py -q` (must
    stay green — proves the pure parser tests it covers are unaffected) plus a quick manual
    `python main.py prep_push_rep mov-en-2024-x "C:\doesnotexist" SIZE_GB 8 device foo rehash
    tempdir C:\t --extras "A;B" --extras-size 9900mb` against a throwaway sandboxed library
    to eyeball that the pre-existing block's parse result is unchanged (do this via a
    Python `-c` harness that imports `main` and calls the pure parsing loop in isolation if
    a helper exists, or by temporarily instrumenting; do NOT run this against a real
    library).

- [ ] 4. [model: opus] [effort: high] Movie-command tests — `tests/test_prep_push_rep_enrich.py`.
  - Depends on: Step 1 (and indirectly Step 3, if any test drives argv parsing rather than
    calling `cmd_prep_push_rep_enrich` directly — prefer direct calls, matching
    `test_baseline_happy_path.py`'s and the smoke suite's own convention of calling `main.cmd_*`
    functions directly rather than shelling out).
  - Consumed by: Step 9 (final verification runs this file); Step 6 (smoke) is a SEPARATE,
    much smaller companion, not a duplicate of this file's scenarios.
  - Establishes convention: none new — reuses ONLY existing fixtures (`sandbox`, `make_video`,
    `mock_device`, `fake_dummy`, `stub_tech_specs`, `mock_tmdb` from `tests/conftest.py`). No
    new fixture is added by this step (a new fixture would need `[model: fable]` per the
    testing-strategy.md binding-hazard rule — this step needs none).
  - Files: `tests/test_prep_push_rep_enrich.py` (new).
  - Details: Two groups of tests.
    - **Group A — existing-behaviour regression matrix (proves requirement 1).** For
      EVERY flag permutation the task lists — no-split, `SIZE_MB`, `SIZE_GB`, `COUNT`,
      `device <alias>`, `rehash`, `tempdir <path>`, `--extras "<folders>"`, `--extras-size
      <v>`, and at least 2 COMBINATIONS of these — call `main.cmd_prep_push_rep(...)` (the
      EXISTING function, imported and called directly, NOT through the new command) and
      assert the exact same post-state `test_baseline_happy_path.py` and the smoke suite
      already assert (status `archived`, chunk placement via `mock_device`, no `.dummy_tmp`/
      `.tobedeleted` leftovers). This is a regression PIN, not new coverage of behaviour
      that's already tested elsewhere — its value is being a SINGLE co-located matrix that
      documents "every combination the new sibling command must not have broken," and it
      MUST still pass even though `cmd_prep_push_rep` was never touched (that's the point:
      if this file's Group A ever goes red, something in this task's OTHER changes leaked
      into the wrong function).
    - **Group B — new-command scenarios**, each using `sandbox` + `make_video` +
      `mock_device` + `fake_dummy` + `stub_tech_specs` + `mock_tmdb` (a `MockTMDB(search=...)`
      or `movie_by_id=...` seeded per scenario, mirroring `test_enrich_metadata.py`'s own
      seeding style):
      1. **id-supplied happy path**: `cmd_prep_push_rep_enrich(id, path, "SIZE_GB", "8",
         tmdb_id=<id>, rename_choice="yes")` — archive completes, `metadata.tmdb_id` set via
         the by-id path (assert NO `/search/` call reached `mock_tmdb`, mirroring
         `test_apply_movie_with_preset_id_fetches_by_id_not_search`), folder renamed with the
         `{tmdb-…}` token, poster/fanart downloaded, hash of the (now-dummy) main file
         untouched by the enrich leg.
      2. **auto-resolve fallback** (no `tmdb_id`): seed `mock_tmdb`'s `search` dict so the
         title resolves; assert a `/search/movie` call was made and the SAME confident-match
         apply happened — proving fidelity to "exactly as enrich_metadata does today."
      3. **confirmation gate — decline**: `rename_choice="no"` (or default `"ask"` with
         `monkeypatch.setattr(main.sys.stdin, "isatty", lambda: False)`) — folder name
         UNCHANGED, but `metadata.tmdb_id` (and title/year/poster) still written; assert the
         printed before/after message appears verbatim (`will be changed to`).
      4. **confirmation gate — accept via `--yes`-equivalent** (`rename_choice="yes"`):
         folder renamed.
      5. **confirmation gate — non-TTY default with NO explicit flag**
         (`rename_choice="ask"` + isatty patched False): behaves identically to scenario 3 —
         THIS is the test that pins the "never hangs the smoke gate" requirement; assert
         `input` is never called (monkeypatch `builtins.input` to raise if invoked).
      6. **`-tvdbid` refusal**: `cmd_prep_push_rep_enrich(id, path, tvdb_id="12345")` —
         assert the refusal message substring, assert `manual_id not in
         mvcommon.load_library()` (NOTHING was prepped — the archive never ran), assert
         `mock_device` received zero pushes.
      7. **archive-incomplete skip**: seed a scenario where `cmd_push` fails (e.g. via a
         `FakeAdb`/failing `mock_device` injection, or simply omit `mock_device` so ADB calls
         error) — assert the function returns `False`, prints the "enrich skipped, archive
         incomplete" warning, and `mock_tmdb` receives ZERO calls (enrich truly never runs).
      8. **`RollbackHardFail` warn-and-continue**: monkeypatch `main.cmd_rename_folder` to
         raise `main.RollbackHardFail(state="x", reason="y", resume_cmd="z")` — assert the
         function still returns `True`, prints the warning + `resume_cmd`, and does NOT
         propagate the exception.
      9. **`--nfo`**: `write_nfo=True` — assert `movie.nfo` written (mirrors
         `test_nfo_movie_written_on_apply_with_flag`); **NFO default off**: without the flag,
         assert no `.nfo` file appears.
      10. **`--no-web`**: assert the EXA fallback path is not invoked when the TMDB search
          misses (mirrors `test_no_web_flag_disables_exa_fallback`'s pattern).
      11. **extras interplay**: `extras=[...]`, `tmdb_id=<id>` together — assert the extras
          group still ends up pushed+archived (unaffected) AND the enrich leg still runs (no
          interference either direction).
    - Every test's Details constraint (mandatory, per docs/testing-strategy.md): never touch
      real `C:\Media` or real `library_*.json`; run `pytest -q` and fix failures before
      marking the step done.
  - Acceptance: `python -m pytest tests/test_prep_push_rep_enrich.py -q` all green; Group A's
    assertions are IDENTICAL in shape to `test_baseline_happy_path.py`'s oracles (proving no
    silent drift); `python -m pytest tests/test_enrich_metadata.py -q` still green (proves
    Step 1's chosen candidate did not regress the existing suite).

- [ ] 5. [model: opus] [effort: high] Season-command tests — `tests/test_prep_push_rep_season_enrich.py`.
  - Depends on: Step 2.
  - Consumed by: Step 9.
  - Establishes convention: **REVISED 2026-08-28 (Decision 6's added scope) — TWO new
    per-file seed helpers, both mirroring the project's existing convention of self-contained
    per-file seed helpers (no conftest change):** (i) `_seed_nested_season_show` — the
    classic `<Show>/Season NN/` layout, may closely mirror
    `test_enrich_metadata.py`'s `_seed_two_season_show` shape (copy the pattern, do not
    import across test files); (ii) `_seed_flat_layout_season` (NEW — did not exist before
    this audit) — ONE release-named season folder whose basename does NOT match
    `_show_folder_of`'s season-like regex (`^season[\s_]*\d+$|^s\d+$`), holding the episode
    files DIRECTLY with no `Season NN` subdirectory at all, matching the shape of the user's
    real `Peaky.Blinders.S06.2022...{tmdb-60574}`-style folders (2026-08-28 audit finding —
    46 of the user's real shows use this shape, making it the DOMINANT case, not an edge
    case). Reuses `sandbox`, `make_video`, `mock_device`, `fake_dummy`, `stub_tech_specs`,
    `mock_tmdb` — no conftest.py change.
  - Files: `tests/test_prep_push_rep_season_enrich.py` (new).
  - Details: Mirror Step 4's two-group structure.
    - **Group A — regression matrix for `cmd_prep_push_rep_season`**: no-split, `SIZE_MB`/
      `SIZE_GB`/`COUNT`, `episodes 1-1` / a multi-episode range, `device`, `rehash`,
      `tempdir`, `--extras`, `--extras-size`, and combinations — call
      `main.cmd_prep_push_rep_season(...)` directly (existing function, untouched) and assert
      the same post-state the smoke suite's
      `test_prep_push_rep_season_with_episode_range` already asserts (only the IN-RANGE
      episode(s) reach `archived`).
    - **Group B — new-command scenarios (REVISED 2026-08-28 per Decision 6 — every row of
      the "Full artifact inventory" table under Approach must be asserted for BOTH layouts;
      the sibling-preset scenario is replaced with a scope-isolation proof per "Why harder"
      reason 2):**
      1. **Nested layout (`_seed_nested_season_show`) — id-supplied happy path, FULL
         artifact-inventory checklist**: run over a 2-episode season (`episodes` unset ->
         whole season). Assert EVERY row of the inventory table: show folder `poster.jpg` +
         `fanart.jpg` (row 1), token stamped on the SHOW folder — parent of `Season NN`, which
         remains intact underneath (row 2), a season-folder `poster.jpg` DISTINCT from the
         show poster (row 3 — assert the season poster's bytes came from the season-images
         endpoint, not the show endpoint, since for this layout they are DIFFERENT files),
         a per-episode `-thumb.jpg` next to each episode video (row 4 — mirrors
         `test_apply_show_with_preset_id_fetches_by_id_not_search`'s still assertions),
         `metadata.overview`/`episode_title` on each episode leaf (row 5), `tmdb_id`/`title`/
         `year`/`overview` on the season_map AND both episode leaves (row 6).
      2. **Flat/root-level layout (`_seed_flat_layout_season`) — id-supplied happy path, FULL
         artifact-inventory checklist**: same assertions as scenario 1 EXCEPT row 3 — assert
         the season-images endpoint IS still called (no crash, no duplicate-write error) but
         its result is DISCARDED because `poster.jpg` already exists (the show write from row
         1 landed in the SAME folder) — assert the printed output contains a "kept" (not an
         error) message for this skip, and that exactly ONE `poster.jpg`/ONE `fanart.jpg`
         exist in that folder afterward, with bytes matching the SHOW-level (not
         season-level) TMDB image. Assert row 4 (per-episode stills) still lands correctly —
         it is keyed off each episode's own filename, never `poster.jpg`, so the folder
         collision does not affect it. Assert exactly ONE rename occurs (not two).
      3. `episode_range` sub-selection (either layout — nested is fine, no need to duplicate
         across both) where the RANGE fully archives: enrich proceeds (assert token stamped)
         even though the season has MORE episodes outside the range still `local_ready`.
      4. **Scope isolation (REPLACES the original "sibling-season preset reuse" scenario —
         Decision 6 / "Why harder" reason 2):** seed TWO seasons of the SAME show with
         DIFFERENT embedded years in their ids (e.g. `tv-en-2019-x-s05` / `tv-en-2022-x-s06`,
         matching the real Peaky Blinders/Expanse pattern) — season 05 already carries a
         preset `metadata.tmdb_id` on one episode leaf (from an earlier run); run
         `cmd_prep_push_rep_season_enrich` on season 06 WITHOUT `-tmdbid`. Assert season 05's
         preset is NOT found or used (no `_resolve_unit_by_id` call reachable from season 05's
         id; season 06 goes through its OWN search/EXA waterfall, or is reported
         none/ambiguous if `mock_tmdb` has no matching entry seeded for it) — a POSITIVE
         regression pin proving the revised `base_id`-scoped design stays correctly isolated
         per season, matching the user's real id convention (this is expected, documented
         behaviour, not a bug to fix).
      5. `-tmdbid` presets onto an episode LEAF, never the `season_map`: assert directly via
         `mvcommon.load_library()[base_id].get("metadata")` is absent/unchanged (season_maps
         carry no `metadata` key) while an episode leaf's `metadata.tmdb_id` is set.
      6. `-tvdbid` refusal: same shape as Step 4's scenario 6, season variant.
      7. archive-incomplete (mid-season failure stops the loop early): assert enrich is
         skipped, AND assert the EXISTING season resume-command message (unmodified,
         `_season_resume_cmd`'s own text) still printed exactly as it does today with no new
         command wired in front of it or trailing after it.
      8. `_season_run_target_ids` unit tests (no fixtures needed beyond `sandbox`): a valid
         range, an invalid range string (`"abc"`) returns `[]` without raising, a
         combined-episode-alias season (reuse `sandbox_alias`) de-aliases correctly.
      9. `RollbackHardFail` warn-and-continue (mirrors Step 4 scenario 8).
      10. **Confirmation-gate note text — REVISED (conditional per Step 2's redesign):**
          assert the "(this is the SHOW folder…)" clarifying note IS printed for the nested
          layout (scenario 1) and is ABSENT for the flat/root-level layout (scenario 2), since
          there the show folder and the season folder are the literal same path and the note
          would be factually wrong.
      11. **LOCAL-ALWAYS-WINS across the full inventory (new, per the artifact-inventory
          table under Approach):** pre-seed a show `poster.jpg`, a season `poster.jpg` (nested
          layout only — meaningless for flat), and one episode's `-thumb.jpg`, each with
          known non-TMDB bytes, before running enrich; assert ALL THREE are byte-identical
          afterward (mirrors `test_enrich_metadata.py`'s
          `test_apply_never_overwrites_local_poster` /
          `test_apply_never_overwrites_existing_episode_still`).
      12. **`--nfo` — richer `tvshow.nfo` element set, BOTH layouts (Decision 4, LOCKED):**
          run once per layout (`_seed_nested_season_show` and `_seed_flat_layout_season`) with
          `write_nfo=True`; assert `tvshow.nfo` lands in the SHOW folder (row 1/2's folder —
          for the flat layout this is the literal season folder) and contains `<title>`,
          `<year>`, `<plot>`, `<rating>`, a plain `<tmdbid>` element AND
          `<uniqueid type="tmdb" default="true">`, `<genre>`, `<runtime>`, `<premiered>`,
          `<studio>` (via `_tmdb_network_names`), and `<director>`/`<actor>` entries. Seed
          `mock_tmdb` so `_resolve_imdb_id` succeeds and assert BOTH `<imdbid>` and
          `<uniqueid type="imdb">` are present; in a second case make `_resolve_imdb_id`
          return `None` and assert those two elements are OMITTED entirely rather than
          written empty. Assert `<tvdbid>` is NEVER present in the written XML, in either
          case (Decision 1's guarantee). **NFO default off**: without `--nfo`, run the same
          happy path (either layout) and assert NO `.nfo` file of any name exists anywhere
          under the show/season folder afterward.
    - Same "never touch real C:\Media" / "run pytest -q and fix failures" constraints as
      Step 4.
  - Acceptance: `python -m pytest tests/test_prep_push_rep_season_enrich.py -q` all green,
    covering every row of the "Full artifact inventory" table for BOTH layouts;
    `python -m pytest tests/test_enrich_metadata.py tests/test_rename_folder.py
    tests/test_set_tmdb.py -q` still green (proves `_show_folder_of`/`_gather_enrich_units`/
    `cmd_set_tmdb`/`cmd_rename_folder` — all reused, none modified — still behave).

- [ ] 6. [model: opus] [effort: high] Smoke-suite coverage — `tests/smoke/test_smoke_all_commands.py`.
  - Depends on: Steps 1-3 (both commands + CLI wiring exist).
  - Consumed by: Step 9's mandatory final `pytest tests/smoke -q` gate; every future PR's
    pre-merge smoke gate per `CLAUDE.md`.
  - Establishes convention: a new `class TestPrepPushRepEnrich:` in this file, matching the
    file's existing per-feature-area class convention (`TestExtras`, `TestOthersCategory`,
    `TestAliasSweep`).
  - Files: `tests/smoke/test_smoke_all_commands.py`.
  - Details: 2-4 FAST cases (this suite has a <30s budget) using `mock_tmdb` +
    `mock_device` + `fake_dummy` + `stub_tech_specs`:
    1. Movie: a full `cmd_prep_push_rep_enrich(id, path, tmdb_id=<seeded id>,
       rename_choice="yes")` round trip — archived, token stamped, poster present.
    2. Movie, default flags (no `rename_choice` override, no `--yes`) — the exact
       non-interactive-default scenario: assert the call RETURNS (does not hang) and the
       folder is unrenamed while `metadata.tmdb_id` is still written. This is the specific
       case the hard finding calls out as the smoke-hang risk; make it explicit and named
       clearly (e.g. `test_prep_push_rep_enrich_default_is_non_interactive_no_hang`).
    3. Season: one small 1-2 episode season through `cmd_prep_push_rep_season_enrich` with
       an explicit id, asserting the SHOW folder gets the token.
    4. `-tvdbid` refusal smoke case (cheap, no TMDB calls at all): assert nothing was
       prepped.
    Add these WITHOUT touching any existing class or test in this file.
  - Acceptance: `python -m pytest tests/smoke -q` — total suite still completes in well under
    30s (record the before/after count + timing delta in PROGRESS.md, matching how
    `docs/feature-extras/PROGRESS.md` records "smoke 76✓ (+0.6s delta)" for its own smoke
    step); zero pre-existing smoke tests changed.

- [ ] 7. [model: opus] [effort: high] Architect docs — `ARCHITECTURE.md`, `README.md`, `docs/README.md`.
  - Depends on: Steps 1-6 complete (docs describe the SHIPPED, tested behaviour, not a plan).
  - Consumed by: any future session/agent reading these docs (including a future planner);
    Step 8's PRIORITY.md/tier entries cross-reference these doc locations.
  - Establishes convention: the canonical written statement of the "TMDB-for-everything"
    convention (does not exist anywhere today per the anchor table's finding) — later work
    on TVDB/AniDB integration (IMP-E3/U3 breadth, tracked in PRIORITY.md Band 1) should treat
    this paragraph as the point where that changes.
  - Files: `ARCHITECTURE.md`, `README.md`, `docs/README.md`.
  - Details:
    - `ARCHITECTURE.md` §6.3a (TMDB enrichment conventions, ~line 973-1034): add a clearly
      labelled paragraph, e.g. **"TMDB-for-everything (no TVDB/AniDB client exists)."**
      stating plainly: MediaVault resolves movies, series, AND anime exclusively against
      TMDB (`/3/search/movie`, `/3/search/tv`, `/3/tv/{id}/season/{n}`, etc.); there is no
      TVDB or AniDB client; `metadata.tmdb_id` is the ONLY provider-id field on a leaf; the
      `{tmdb-…}` folder token (never `{tvdb-…}`) is used for every category including
      series/anime — with a pointer to the ONE known inconsistency:
      `suggest_target_folder`'s web-console placeholder string, which proposes a
      `{tvdb-000000}` PLACEHOLDER (never resolved, never written) for series/anime — a
      pre-IMP-E12 relic, pinned by `tests/test_web_datafns.py`, explicitly NOT touched by
      this task (see Decision 1 and Out of scope).
    - ARCHITECTURE.md §6.3a, immediately after the "TMDB-for-everything" paragraph above: add
      a paragraph documenting `_write_nfo`'s richer element set (Decision 4, 2026-08-28):
      `<title>`, `<year>`, `<plot>`, `<rating>`, a plain `<tmdbid>` element AND
      `<uniqueid type="tmdb" default="true">`, `<imdbid>` + `<uniqueid type="imdb">` (both
      OMITTED, not written empty, when the imdb lookup fails/returns `None`), `<genre>`,
      `<runtime>`, `<premiered>`, `<studio>` (via `_tmdb_company_names` for `movie.nfo` /
      `_tmdb_network_names` for `tvshow.nfo`), and `<director>`/`<actor>` entries. State
      explicitly that this ALSO changes `enrich_metadata --nfo`'s existing output
      (`_write_nfo` is one shared function, reused verbatim by both callers) — a deliberate,
      desirable extension per Decision 4, NOT a regression. State why `<tvdbid>` is never
      emitted: the project has no TVDB client (the same TMDB-for-everything convention
      documented immediately above), and there is no way to derive a correct TVDB id from
      what MediaVault stores (cross-ref Decision 1).
    - ARCHITECTURE.md §6.3a, immediately after the existing `_show_folder_of` /
      "Season-inheritance artwork resolution order" paragraph: add a short note (evidence
      verified 2026-08-28) that `_show_folder_of`'s third branch — a SINGLE season folder
      whose basename is NOT season-like — is the DOMINANT real-world shape for this user (46
      of their real shows), not a corner case; when it fires, the show folder and the season
      folder are the SAME path, so the show poster write and the per-season poster write
      target the identical file (the season write is gracefully skipped as already-existing —
      not an error, not a second download). Cross-reference IMP-D22's Step 2/5 as where this
      is exercised end-to-end for the first time.
    - ARCHITECTURE.md §10 "Combined auto-pilots" (~line 2085-2092): add two bullets for
      `prep_push_rep_enrich` / `prep_push_rep_season_enrich`, one line each, cross-referencing
      IMP-D22 and this file/section. Note explicitly that the season variant scopes its enrich
      unit by the season's OWN id (not a derived show id) — a sibling season's
      `metadata.tmdb_id` preset is therefore NOT auto-discovered; `-tmdbid` is the primary
      per-season mechanism (Decision 6, 2026-08-28).
    - ARCHITECTURE.md §12a's Extras-lifecycle-style blockquote pattern (~line 2296-2321): add
      a short new blockquote **"Combined archive+enrich autopilot (IMP-D22, §6.3a):"**
      stating explicitly that both new commands compose EXISTING wrapped commands only —
      zero new PONR, zero journal-format change, zero `RollbackHardFail`-contract change —
      the ONE new interactive `input()` call in the codebase is entirely OUTSIDE the
      rollback-relevant call graph (it only gates whether `cmd_rename_folder` — itself
      unmodified — gets called at all).
    - `README.md`: add two rows to the command table (mirroring the exact column style of
      the existing `prep_push_rep`/`prep_push_rep_season` rows at ~195-196); add a new prose
      subsection immediately after the existing `enrich_metadata` prose block (~line 355-420),
      titled `### Combined archive + enrich (prep_push_rep_enrich / prep_push_rep_season_enrich)`,
      covering: the `-tmdbid`/`-tvdbid` distinction (link back to the ARCHITECTURE.md
      TMDB-for-everything paragraph), the confirmation gate + `--yes`/`--no-rename`, the
      non-interactive default, and 2-3 copy-pasteable examples matching the task's own
      example commands.
    - `docs/README.md`: add one row to the per-feature index table (§5, ~line 92-107),
      matching the exact column format of the `feature-extras/` row:
      `| feature-prep-push-rep-enrich/ | IMP-D22 (shipped <date>) — combined
      prep_push_rep(_season)+enrich autopilot | PLAN.md, DECISIONS.md (locked: -tvdbid
      refusal, apply-late confirmation gate, command naming, NFO default off,
      warn-and-continue), PROGRESS.md |`.
  - Acceptance: every new/changed CLI example in README.md is REPLAYED through the real
    argv-parsing path (mirroring how IMP-D19's Step 12 "24/24 CLI examples replayed thru real
    parsers" verified its own README additions) — i.e., construct each example's argv list
    and feed it through the SAME token-walking loop Step 3 added (or call the resulting
    `cmd_*` function with the parsed values) to prove the documented syntax actually parses
    the way the prose claims. No prose contradicts Step 1/2's actual implemented behaviour
    (re-read the merged code before writing, do not describe the PLAN's intent if the
    winning candidate ended up differing in some print-wording detail).

- [ ] 8. [model: sonnet] [effort: medium] Register IMP-D22 — `improvements/improvements_tierD.md`, `improvements/PRIORITY.md`, `docs/priority-graph/priority-graph.html`.
  - Depends on: Steps 1-7 complete and green (registered as `done`, never as `pending` —
    per the task's explicit instruction).
  - Consumed by: any future prioritization pass; the priority-graph visual.
  - Establishes convention: none new — follow the IMP-D19/D20/D21 entry format exactly
    (Category/Priority/Files/Current behavior/Proposed change/Rationale/Goal/Effort
    estimate/Risk/If skipped/Status), and the IMP-D19 Step-13 precedent for a THREE-FILE
    registration in one step.
  - Files: `improvements/improvements_tierD.md`, `improvements/PRIORITY.md`,
    `docs/priority-graph/priority-graph.html`.
  - Details:
    - `improvements_tierD.md`: append a new `## IMP-D22: combined prep_push_rep_enrich (+
      season) archive-then-enrich autopilot` section, `Status: **done**
      (feature/imp_d22_prep_push_rep_enrich)`, cross-referencing IMP-D17 (rename_folder) and
      IMP-E3/U3 (TMDB enrichment) as the primitives it composes, and explicitly stating "no
      new entry type, no rollback-contract change (composes existing wrapped commands only)"
      — matching the risk-line style of D17/D19/D20/D21's own entries.
    - `PRIORITY.md`: add a row under **Band 5 — Quality/Perf/Utility** (alongside `D2`,`D3`,
      `D6`-`D15` — this is a workflow-convenience utility command, not a Band 0-1 critical/
      foundation item); update the `## ✅ DONE` list (append a `D22 (...)` bullet, one line,
      matching the existing bullets' terse style); update the top **"Last updated"** banner
      date + a one-sentence summary of what shipped; the **"👉 SUGGESTED NEXT TASK"** headline
      stays **S1/S2** (unchanged — D22 does not block or supersede the daemon-path priority),
      but add an "Earlier:" sentence noting D22 shipped, mirroring how D18/D19/D20/D21 are
      each folded into that same running sentence today.
    - `priority-graph.html`: add one `TASKS` row —
      `["D22","prep_push_rep_enrich (+season) autopilot","D","med","done","<one-sentence
      summary mirroring the tier-file entry>"]` — and two `EDGES` entries: `["D17","D22"]`
      (uses `rename_folder`) and `["E3","D22"]` (uses the TMDB enrichment machinery); bump
      the footer's task count and "updated" date.
    - Confirm all three files AGREE (same status, same one-line description in spirit) —
      this is the explicit "maintenance protocol" requirement at the bottom of PRIORITY.md.
  - Acceptance: `grep -c "D22" improvements/improvements_tierD.md improvements/PRIORITY.md
    docs/priority-graph/priority-graph.html` all >= 1; no other task's row/status
    accidentally altered (diff review).

- [ ] 9. [model: opus] [effort: medium] Final verification + smoke gate (last).
  - Depends on: Steps 0-8 all `done`.
  - Consumed by: Phase 3 finalize (push, PR, Checkpoint 1).
  - Details: **Normally performed by the ORCHESTRATOR directly, not dispatched** — mirrors
    the IMP-D19 Step-14 precedent ("executed by the ORCHESTRATOR directly (Phase-3
    verification duty)") and Phase 3 of the orchestrator playbook, which already runs the
    Verification section itself. The `[model: opus]` tag is a safe fallback ONLY if the
    orchestrator's process prefers to delegate this step explicitly rather than run it
    inline. Run every command in the `## Verification` section below, in order; all must be
    green. Confirm (by `git diff main`) that `cmd_prep_push_rep`, `cmd_prep_push_rep_season`,
    `cmd_set_tmdb`, `cmd_rename_folder`, `RollbackJournal`, `RollbackHardFail`,
    `mark_point_of_no_return`, `TXN_JOURNAL_NAME`, and `recover_journal` are ALL untouched
    (zero diff). Reconcile `docs/feature-prep-push-rep-enrich/PROGRESS.md`'s step table
    against `git log --oneline` (trust git on any disagreement, per the Resume Protocol) and
    back-fill any missing commit SHAs. Update PROGRESS.md's `▶ NEXT ACTION` to
    "🚦 CHECKPOINT 1 — awaiting the user's merge approval."
  - Acceptance: every command in `## Verification` exits 0; the rollback-contract zero-diff
    check passes; PROGRESS.md fully reconciled and committed.

## Risks and edge cases

- **The confirmation-gate mechanism is the highest-risk piece** — it is the ONLY new
  interaction pattern in a codebase with zero prior `input()` calls, and it sits right next
  to (Candidate B) or duplicates (Candidate A) a well-tested, delicate function. Mitigated by
  the multi-candidate judge weighting fidelity/blast-radius highest, and by Step 1's
  Acceptance requiring the existing `test_enrich_metadata.py` suite to pass UNMODIFIED.
- **A `-tmdbid` typo (wrong id) silently enriches the WRONG title.** This is inherent to
  `_resolve_unit_by_id`'s existing "confident-only, no validation beyond a successful fetch"
  contract — not something this task changes or can meaningfully harden (the user explicitly
  asserted the id; `set_tmdb` has the identical property today). Not a regression.
- **`_has_tmdb_token` case-sensitivity gap (pre-existing, main.py:1687, no
  `re.IGNORECASE`)** means a folder already carrying an UPPERCASE `{TMDB-…}` token (the
  user's real `Run (2002)` folder does exactly this) reads as "no token" and would get a
  SECOND, lowercase token appended if enriched via either the existing `enrich_metadata` OR
  these new commands. This is EXPLICITLY out of scope per the task ("the user said
  folder-rename work is a separate feature") — see Decision 5 (RULED, LOCKED, out of scope).
  Flagged prominently here and in Suggested next tasks so it is not lost.
- **Prefix-collision in `_gather_enrich_units`.** Both `cmd_enrich_metadata <prefix>` today
  and this task's `id_or_prefix=real_id`/`base_id` scoping (movies/seasons respectively —
  revised 2026-08-28 to scope seasons by `base_id` directly, not a derived show id; see "Why
  the series variant is harder" reason 2) share the pre-existing
  `mid.startswith(id_or_prefix)` sharp edge (one id being a literal string-prefix of
  another). Inherited, not introduced; not fixed here.
- **No automatic cross-season preset reuse (accepted, by design — verified 2026-08-28).**
  Because the user's real series ids embed a different year per season, `cmd_set_tmdb`ing one
  season of a show does NOT make a LATER run on a sibling season find that preset — each
  season is its own `_gather_enrich_units` unit. `-tmdbid` must be supplied per season/run for
  a series (Decision 6). This is a real UX cost worth knowing about up front, not a defect —
  see "Suggested next tasks" for a possible future smarter-grouping enhancement.
- **Flat/root-level layout write-order coupling (verified 2026-08-28, 46 of the user's real
  shows).** When the show folder and the season folder are the same path, `_download_unit_
  images`'s show-level poster/fanart write happening BEFORE its per-season loop means the
  single resulting `poster.jpg`/`fanart.jpg` carries the SHOW-level artwork, not a
  season-specific image — the season-images endpoint is still called but its result is
  silently discarded via the existing `os.path.exists` LOCAL-ALWAYS-WINS check. This is
  correct (there is only one folder for one file to live in) and is UNCHANGED existing
  `_download_unit_images` behaviour this task merely exercises for the first time via an
  explicit test (Step 5, scenario 2) rather than relying on it being correct by accident.
- **IMP-R10 (open, change-gated)**: a contended `.mediavault_txn.json` during `cmd_replace`'s
  PONR write can be misread as a locked media file. This feature introduces NO new journal
  in the SAME directory during the archive phase (it only composes the existing autopilot,
  whose journals are exactly as many as today), and `rename_folder`'s own journal lives in
  the PARENT directory of a DIFFERENT folder (the show/movie folder, not the media file being
  replaced) at a different point in time (after the archive, not concurrent with it) — so
  this feature does not create a new journal-contention scenario.
- **Season enrich scope surprises the user.** For the nested `<Show>/Season NN/` layout, the
  SHOW folder (not the season folder typed on the command line) is what gets renamed —
  mitigated by the explicit clarifying note in the confirmation message (Step 2). For the
  flat/root-level layout this risk does not apply — the "show folder" IS the folder the user
  typed on the command line, so Step 2's conditional note logic correctly stays silent there.
- **`mvconfig.json` has no TMDB key configured** (e.g., a fresh clone, or the CI/smoke
  environment): both new commands still complete the ARCHIVE fully; the enrich leg prints
  `cmd_enrich_metadata`'s own "No TMDB API key configured" message and returns — the
  warn-and-continue contract already covers this with zero special-casing needed.
- **Windows path length / `os.rename` cross-volume**: inherited entirely from
  `cmd_rename_folder`, unmodified; not a new risk surface.

## Verification

```
python -m pytest tests/test_prep_push_rep_enrich.py tests/test_prep_push_rep_season_enrich.py -q
python -m pytest tests/test_enrich_metadata.py tests/test_rename_folder.py tests/test_set_tmdb.py -q
python -m pytest tests/test_cli_parsers.py tests/test_baseline_happy_path.py -q
python -m pytest tests -q
python -m pytest tests/smoke -q
```
(Bare `pytest -q` collects nothing — there is no `testpaths` configured, per
`docs/feature-extras/FIXES_PROGRESS.md`'s own resume-protocol note; always target `tests/`
explicitly. The final line, `pytest tests/smoke -q`, is the mandatory smoke gate per
CLAUDE.md's cross-command integrity rule — it MUST be green before any PR.)

## Out of scope

- Fixing `_has_tmdb_token`'s case-sensitivity (Decision 5) — explicitly deferred by the
  user's own framing ("folder-rename work is a separate feature").
- Automatic cross-season `metadata.tmdb_id` preset discovery/reuse (verified 2026-08-28 not
  to reliably work for the user's real per-season-year id convention — see "Why the series
  variant is harder" reason 2 and the "no automatic cross-season preset reuse" risk above). A
  smarter show-grouping heuristic is noted in Suggested next tasks, not attempted here.
- Parsing an EXISTING `{tmdb-NNNN}` folder token back into an id to skip re-resolution (noted
  as a "Suggested next task" — real value given 12 of the user's 17 unenriched movies already
  carry a correct id in their folder name, but a distinct, separable feature).
- A real TVDB (or AniDB) provider/client (Decision 1, option 3) — a multi-week feature
  in its own right, not attempted here.
- Storing an inert `metadata.tvdb_id` field (Decision 1, option 2) — rejected (see
  Decision 1's reasoning).
- Changing `suggest_target_folder`'s `{tvdb-000000}` web-console placeholder string (IMP-E12,
  pinned by `tests/test_web_datafns.py`) — a different, unrelated, already-decided surface.
- Any change to `_season_resume_cmd`'s printed message, `RollbackJournal`, `PONR` placement,
  `recover_journal`, or the `RollbackHardFail` class — all explicitly change-gated and
  explicitly untouched by this task.
- A `--dry-run` mode for the new commands' enrich leg — the whole point of the composed
  autopilot is a one-shot APPLY; a user wanting a preview should run `enrich_metadata <id>`
  (no `--apply`) by hand before archiving, exactly as available today.
- Extending the confirmation-gate pattern to the plain `rename_folder` / `enrich_metadata`
  commands themselves — only the two NEW composed commands gain it.

## Decisions (ruled by the user 2026-08-28)

All 7 items below were RULED and LOCKED by the user on 2026-08-28. Decision 4 (`--nfo`
default) carries a FINAL RULING — off by default, plus an added NFO-content scope
requirement (see Decision 4) — and is, like the other six, no longer provisional; its
implementation is still kept to a single, trivially-flippable default value, not as a hedge
on the ruling itself but so a later, unrelated decision could flip the default with a
one-line change. The options/tradeoffs analysis is kept below each ruling as the recorded
rationale (this is the content Step 0 copies into
`docs/feature-prep-push-rep-enrich/DECISIONS.md`).

**1. `-tvdbid` handling.**
Options: (a) accept `-tmdbid` only; make `-tvdbid` a clear, immediate, actionable refusal
(nothing runs — no archive, no enrich). (b) store a separate, inert `metadata.tvdb_id`
field that nothing currently reads. (c) build a real TVDB provider/client.
Tradeoffs: (a) is the only option with zero risk of silent data corruption — TVDB and TMDB
ids are different numbering spaces, and the codebase has ZERO existing TVDB machinery to
validate one. (b) adds a new, permanently-unused shared field (a schema change with no
consumer — the CONSUMER IMPACT rule would flag an unread field as suspicious) and, worse,
would make a user who typed `-tvdbid` BELIEVE something happened when in fact enrich fell
through to auto-search silently — a confusing, non-obvious divergence from "the id supplied
runs with no search" (requirement 2). (c) is a legitimate multi-week feature, honestly out
of scope for a command-composition task.
**RULING (2026-08-28): (a) — ACCEPTED as recommended.** User's words: *"ok refuse - take
only tmdb."* Refuse clearly and immediately, before any archive work begins, naming
`-tmdbid` as the correct flag and pointing at themoviedb.org. LOCKED.

**2. Rename-early-with-path-remap vs. prompt-early/apply-late.**
Rename-early is not just riskier but IMPOSSIBLE (`cmd_rename_folder` refuses when no library
entry references the folder — the entry does not exist until `cmd_prep` runs). Within
"apply-late" (mandatory), the sub-question is WHEN to prompt: before the archive (requires an
independent, pre-entry resolution pass) or after (reuses the real, existing resolution
exactly). **RULING (2026-08-28): resolve-and-prompt-once, immediately after the archive
completes — ACCEPTED as recommended.** User's words: *"after archive is fine."* Reuses
`enrich_metadata`'s real resolution path unmodified — see the "Why prompt-early was
considered and rejected" subsection under Approach for the full reasoning (fidelity to
pre-existing curated metadata + avoiding a second, drift-prone resolution code path).
Accepted cost: the user is asked about the rename only after the archive finishes, not
before — harmless, since declining never undoes the archive. LOCKED.

**3. Non-interactive confirmation mechanism + default.**
Options: (a) `--yes` / `--no-rename` explicit flags, plus `sys.stdin.isatty()` detection
defaulting to "do not rename" when ambiguous/non-interactive. (b) always auto-confirm
(matches today's `enrich_metadata` unconditional-stamp behaviour). (c) always require
`--yes` explicitly (refuse silently defaulting either way).
Tradeoffs: (b) reintroduces exactly the "unconditional stamp, no gate" behaviour requirement
4 asks to change, and would make the new command's SAFETY property purely cosmetic. (c) is
maximally safe but means a fresh smoke/CI run with no flags at all would print a "declined"
note EVERY time — noisy but not wrong; functionally identical in outcome to (a)'s default.
**RULING (2026-08-28): (a) — ACCEPTED as recommended.** `--yes` / `--no-rename` plus
`sys.stdin.isatty()` detection, defaulting to "do not rename" when ambiguous/non-interactive
— exactly as the task's own hard finding specifies. This is the option that both satisfies
requirement 4 (a real, honoured decline) and guarantees the smoke gate never hangs
(Step 1/4/6's tests pin `input()` is unreachable without an interactive TTY). LOCKED.

**4. Is `--nfo` on by default in the new commands, and what should the NFO contain?**
Options (default question): (a) off by default (matches `cmd_enrich_metadata`'s own existing
default — an explicit opt-in). (b) on by default for the new commands specifically.
Tradeoffs: (b) is a real convenience but is an inconsistency between `enrich_metadata --apply`
(no NFO) and this new command (NFO) that a user would need to remember, and it writes a NEW
file type (`.nfo`) as a side effect with no flag typed — a surprise, however benign.
**FINAL RULING (2026-08-28): (a) — LOCKED, plus an added scope requirement.** User's exact
words: *"yes, there are some nfo examples in the media folder. use that implement the nfo
fetch also to get more info. so lets implement that also - but default keep it off."* This
resolves both halves: the default stays **OFF** (LOCKED, no longer provisional), AND the NFO
WRITER ITSELF is now in scope to be enriched with more fields, using the real example the
user pointed at as the shape reference.

**Evidence — the user's real media root has 12 `.nfo` files, two distinct kinds:**
1. **ONE real Kodi/Jellyfin NFO** — the file the user means —
   `C:\Media\Series\English\Sci-Fi\Fringe (2008) [tvdbid-82066]\tvshow.nfo`:
   ```xml
   <?xml version="1.0" encoding="utf-8" standalone="yes"?>
   <tvshow>
     <title>Fringe</title>
     <imdbid>tt1119644</imdbid>
     <tmdbid>1701</tmdbid>
     <tvdbid>82066</tvdbid>
   </tvshow>
   ```
   Notable: it carries THREE provider ids, where `_write_nfo` today emits only a single
   `<uniqueid type="tmdb">`.
2. **Eleven scene-release `.nfo` files** (e.g. `The.Autopsy.of.Jane.Doe.2016...FraMeSToR.nfo`)
   — MediaInfo text dumps + bbcode screenshot links. NOT Kodi NFOs, NOT machine-readable, NOT
   a template for anything. Mentioned only as a **non-collision note**: the bare `.nfo`
   extension already coexists on this user's disk alongside our own `movie.nfo`/`tvshow.nfo`
   names without conflict (different basenames), so nothing new to guard against there.

This does **NOT** contradict Decision 4's earlier "0 NFO files across 173 folders" finding —
that scan covered library `folder_path` values specifically, and the Fringe show-root folder
(where `tvshow.nfo` actually sits) is a level above/outside that set, not one of the 173.

**Interpretation adopted (explicit assumption, stated here so it is never silently
re-litigated):** "use that … to get more info" means **enrich the NFO OUTPUT with more
TMDB-sourced fields**, using the Fringe file's shape as the reference for WHICH kinds of
fields a real Kodi NFO carries (multiple provider ids being the standout one). The
alternative reading — treat existing `.nfo` files as an INPUT source, harvesting ids out of
them into the library — is explicitly **OUT OF SCOPE** for this task (see Out of scope and
Suggested next tasks; it pairs naturally with the already-listed "parse an existing
`{tmdb-NNNN}` folder token back into an id" idea, since both are cheap, already-on-disk id
sources for a future task to mine).

**`<tvdbid>` MUST NOT be emitted — cross-ref Decision 1.** The Fringe example's THIRD id
(`<tvdbid>82066</tvdbid>`) is exactly the class of thing Decision 1 refuses to fabricate: the
project has no TVDB client, TVDB and TMDB ids are different numbering spaces, and there is no
way to derive a correct `tvdbid` from what MediaVault stores. Our NFO output therefore
carries TWO of the Fringe example's three ids (`<title>`, `<imdbid>`, `<tmdbid>` — both the
existing `<uniqueid type="tmdb">` form and a plain `<tmdbid>` element, matching what the
example actually uses) and never a third. State this plainly in the NFO section of Step 1 and
in Step 7's docs so a future reader does not mistake the omission for an oversight.

**Implementation requirement (binding on Steps 1/2/3): the default stays a ONE-LINE flip,
independent of the NFO-content enrichment.** The single source of truth remains
`write_nfo=False` on BOTH `cmd_prep_push_rep_enrich` and `cmd_prep_push_rep_season_enrich`
(Steps 1/2), mirrored by the CLI dispatcher's `write_nfo_flag = False` initial value
(Step 3) — comment both sites `# Decision 4 (2026-08-28, LOCKED): OFF by default; flip this
value (and its dispatcher mirror) if a future decision changes the default — the NFO
CONTENT enrichment (see Step 1) is independent of this default and applies either way`. This
default question is fully decided; the richer NFO CONTENT is the new, additional scope (see
Step 1, which now owns the `_write_nfo` extension, and Steps 4/5/7 for its tests/docs).
LOCKED.

**5. Is `_has_tmdb_token`'s case-sensitivity gap in scope?**
Per the task's own explicit instruction, this is NOT to be silently fixed — the user
characterized folder-rename work as a separate feature. **RULING (2026-08-28): leave it
untouched — ACCEPTED as recommended.** Both new commands inherit the SAME behaviour
`enrich_metadata` already has today (a real, pre-existing, unrelated limitation, not a
regression this task introduces). Flagged in Risks and Suggested next tasks so it stays
visible. LOCKED (out of scope).

**6. Command naming — the season variant's name.**
Options: (a) `prep_push_rep_season_enrich` (append `_enrich` to the existing
`prep_push_rep_season` name — keeps that established prefix intact, groups with other
`_season` commands alphabetically/visually). (b) `prep_push_rep_enrich_season` (insert
`_season` after `_enrich`, grouping with other `_enrich`-suffixed things instead).
Tradeoffs: both are readable; (a) matches the codebase's existing suffix-appending
convention more closely (`push`->`push_group`, `replace`->`replace_group`,
`restore`->`restore_group`, `fetch`->`fetch_restore`: a qualifier is APPENDED to an already-
stable base command name, never spliced into the middle).
**RULING (2026-08-28): (a) `prep_push_rep_season_enrich` — ACCEPTED, plus an added scope
requirement.** The user's exact words, folded into this ruling verbatim: *"that enrich may
be even more complex - like it sometimes downloads season specific images, or data, or
whole season one in root level, etc. make sure all that works as expected."* This is NOT a
cosmetic add-on — a live audit of the user's REAL library (2026-08-28) found the season
variant materially harder than originally scoped, for two concrete, verified reasons:
(i) **46 of the user's shows use a FLAT layout** — one release-named season folder with NO
`Season NN` subfolder, holding the episodes directly at "root level" (exactly the user's own
phrasing) — where the show folder and the season folder are the SAME path; and (ii) **the
user's series ids embed a DIFFERENT year per season**, which breaks the plan's original
sibling-preset-discovery assumption. Both are now fully worked through in the revised "Why
the series variant is harder" section under Approach, and drive concrete redesigns of Step 2
(scope by `base_id`, not a derived show id; a conditional confirmation note) and Step 5 (an
explicit, both-layouts, full-artifact-inventory test matrix — see "Full artifact inventory
for a series/season enrich" under Approach). LOCKED, with the added scope folded into Steps
2/5 below.

**7. Does an enrich-leg failure fail the whole command, or warn-and-continue?**
The task itself recommends warn-and-continue ("the archive already succeeded").
**RULING (2026-08-28): warn-and-continue — ACCEPTED as recommended.** User's words:
*"enrich shd just be not done, but command can finish."* Implemented as: (i) a
TMDB none/ambiguous/no-api-key outcome is ALREADY warn-and-continue in the existing
`cmd_enrich_metadata` (it never raises for these); (ii) a `RollbackHardFail` from a
post-PONR `rename_folder` failure — the one genuine exception `cmd_enrich_metadata` can
propagate — is explicitly caught by the new commands and turned into a printed warning +
the exact resume command, never an aborted/failed return. Both new commands' overall return
value reflects ONLY "did the archive complete" — the enrich leg's own success/failure is
reported via prints, exactly like every other warn-and-continue path already in this
codebase (e.g. `_push_title_extras_or_warn`). LOCKED.

## Resumability — execution journal

`docs/feature-prep-push-rep-enrich/PROGRESS.md` (created by Step 0) is the single
machine-readable "where we are," following the proven `docs/feature-extras/PROGRESS.md` /
`docs/feature-extras/FIXES_PROGRESS.md` shape. It carries:
- A header block: Task, Framework (v2), Branch, Plan/Decisions file paths, Last updated.
- A `▶ NEXT ACTION` line, updated at the START of every dispatch (what's about to run) and
  the END (outcome), so a mid-agent crash leaves a durable trace — never only at completion.
- A Step-status table with columns `Step | Description | Status | Completing SHA | Tests |
  Notes`; the Notes column records, per the task's explicit requirement: the model actually
  used (and any Fable -> `executor-opus` substitution per the standing protocol above), the
  candidate count + judge decision for Step 1, and a one-line summary of what changed.
- A "Fable availability" line per session (probe result + timestamp), distinct from the
  step table, since the SAME step could in principle be resumed under a different
  availability state than when it started.
- A "Blockers / human gates" block naming Checkpoint 1 (PR merge) and Checkpoint 2 (branch
  archive) as the two points requiring the user.

### Resume protocol (first thing a new session — or a different Claude account — does)

1. `git fetch && git checkout feature/imp_d22_prep_push_rep_enrich` (create it from `main`
   if it does not exist yet — first run only).
2. Read `docs/feature-prep-push-rep-enrich/PLAN.md` + `DECISIONS.md` + `PROGRESS.md`.
3. Reconcile: `git log --oneline main..HEAD` must match the per-step commit SHAs in
   PROGRESS.md's table; `git status` clean apart from the standing
   `Master_Stream_Archiver*`/`MatchArchiver*` staged-file hazard. On any disagreement, trust
   git over the table and correct the table.
4. **Before dispatching Step 1 or Step 2 (the two `[model: fable]` steps) — even if a PRIOR
   session already recorded a successful Fable probe — RE-RUN the Fable reachability probe**
   (see the standing protocol above). Availability can change between sessions.
5. Resume at the first non-`done` step, honouring any `in_progress` sub-state notes in
   PROGRESS.md (a crashed agent's edits SURVIVE in the working tree even when its transcript
   is gone — inspect `git diff --stat` before re-dispatching, so completed work is never
   redone; a crashed agent can often be resumed via its saved transcript rather than a cold
   re-dispatch).
6. Finish a step -> update + commit PROGRESS.md (status + SHA + tests + notes) and tick the
   PLAN.md checkbox in the SAME commit. Commit by explicit pathspec ONLY (see the standing
   hazard below) — never `git add -A`, never a bare `git commit`.

### Standing hazard for EVERY commit on this branch

The user's personal `Master_Stream_Archiver*.py` / `MatchArchiver*.py` files are STAGED but
uncommitted in the repo's index (and `Master_Stream_Archiver_MultiModel_v6.py` is untracked).
**Every commit MUST use an explicit pathspec** (`git commit -m "…" -- <paths>`) — never
`git add -A`, never a bare `git commit`. This has bitten a prior git-agent run on this exact
repo (recovered via `git reset --soft HEAD~1` before it was pushed); the orchestrator should
commit these directly rather than delegating, per the `docs/feature-extras/FIXES_PROGRESS.md`
precedent.

## Branch name

`feature/imp_d22_prep_push_rep_enrich`

## PR-to-main procedure (per `docs/git-pr-conventions.md`)

- Title: `feature: prep_push_rep_enrich + prep_push_rep_season_enrich — combined
  archive+TMDB-enrich autopilot — IMP-D22`
- Body, in this exact order:
  1. The auto-generated Claude Code summary (Summary / Changes / Test plan bullets, composed
     from the executed steps).
  2. `## Original task prompt` — the COMPLETE, VERBATIM initial task prompt the user gave for
     this work (do not trim, paraphrase, or summarize it).
  3. The standard trailer: `🤖 Generated with [Claude Code](https://claude.com/claude-code)`.
- **🚦 Checkpoint 1 (human-gated, mandatory):** create the PR, then STOP. Never run
  `gh pr merge`, `git merge` into `main`, or push to `main` without the user's EXPLICIT
  confirmation — not even for docs-only follow-up commits.
- On the user's approval: `gh pr merge <#> --squash` (matches this repo's `(#N)` history);
  do NOT delete the branch at merge time.
- After merge: `git checkout main && git pull --ff-only`.
- **🚦 Checkpoint 2 (human-gated, mandatory, SEPARATE from Checkpoint 1):** after merge, ASK
  the user before archiving. On approval: create an annotated
  `archive/feature/imp_d22_prep_push_rep_enrich` tag at the branch tip (message includes the
  merge info + the revive steps, per the template in `docs/git-pr-conventions.md`), push the
  tag, then delete the branch locally and remotely.

## Manual test commands (copy-pasteable, against the REAL library — run only after the PR is reviewed and you are ready to try it live)

```powershell
# Movie — explicit id, interactive confirm (run in a real terminal so isatty() is True)
python main.py prep_push_rep_enrich mov-en-2016-autopsy "C:\Media\Movies\English\Horror\The Autopsy of Jane Doe (2016)\The Autopsy of Jane Doe.mkv" SIZE_GB 8 -tmdbid 397243

# Movie — auto-decline the rename, still archives + writes metadata.tmdb_id
python main.py prep_push_rep_enrich mov-en-2016-autopsy "C:\Media\Movies\English\Horror\The Autopsy of Jane Doe (2016)\The Autopsy of Jane Doe.mkv" SIZE_GB 8 -tmdbid 397243 --no-rename

# Movie — no id supplied, falls back to auto-resolve exactly like enrich_metadata
python main.py prep_push_rep_enrich mov-en-2016-autopsy "C:\Media\Movies\English\Horror\The Autopsy of Jane Doe (2016)\The Autopsy of Jane Doe.mkv" SIZE_GB 8 --yes

# Movie — -tvdbid is refused; nothing runs
python main.py prep_push_rep_enrich mov-en-2016-autopsy "C:\Media\Movies\English\Horror\The Autopsy of Jane Doe (2016)\The Autopsy of Jane Doe.mkv" SIZE_GB 8 -tvdbid 12345

# Season — explicit show id, non-interactive, also pushes extras
python main.py prep_push_rep_season_enrich tv-en-2016-mrrobot-s02 "C:\Media\Series\Mr Robot\Season 02" SIZE_GB 8 device series -tmdbid 62127 --yes --extras "Specials" --extras-size 9900mb

# Season — partial range, --nfo
python main.py prep_push_rep_season_enrich tv-en-2016-mrrobot-s02 "C:\Media\Series\Mr Robot\Season 02" SIZE_GB 8 episodes 1-3 -tmdbid 62127 --yes --nfo

# Sanity — confirm the plain existing autopilots still behave identically (no flags changed)
python main.py prep_push_rep mov-en-2024-sometitle "C:\Media\Movies\...\file.mkv" SIZE_GB 8 device movies rehash
python main.py prep_push_rep_season tv-en-2024-someshow-s01 "C:\Media\Series\...\Season 01" SIZE_GB 8 episodes 1-5 device series
```

## Suggested next tasks

- **Fix `_has_tmdb_token`'s missing `re.IGNORECASE`** (Decision 5, deliberately deferred
  here) — a small, separately-scoped fix that would stop a second token from being appended
  to the user's `Run (2002) … {TMDB-69590}`-shaped folders.
- **Parse an existing `{tmdb-NNNN}` folder token back into an id** so `enrich_metadata` (and
  these new commands) can skip a redundant search/EXA-resolve when the folder ALREADY
  encodes the correct id — real value given the anchor table's finding that 12 of the user's
  17 unenriched movies already carry a correct id in their folder name.
- True TVDB integration (Decision 1, option (c)) — only if the project ever needs a
  provider-id space distinct from TMDB (e.g., for content TMDB genuinely lacks).
- **A smarter cross-season show-grouping heuristic** (surfaced by the 2026-08-28 audit,
  "Why the series variant is harder" reason 2): today `_gather_enrich_units` groups seasons of
  one show by a NAIVE `-sNN`-stripped id prefix, which does not reunite seasons whose ids
  embed different years (the user's real, dominant id convention). A future enhancement could
  group by the ALREADY-RESOLVED `metadata.tmdb_id` instead (once ANY season is enriched, every
  OTHER season sharing that same tmdb_id could be recognised as the same show without a fresh
  search) — this would restore automatic sibling-preset reuse without depending on id-string
  shape. Not attempted here (would touch `_gather_enrich_units` itself, a shared, heavily-used
  chokepoint — a separate, carefully-scoped task).
- Extend the confirmation-gate pattern (`_make_rename_confirm`) to the plain `rename_folder`
  command itself, for a consistent "always confirm a rename unless `--yes`" UX project-wide.
- Consider folding the SAME "archive then enrich" composition into `push_auto_batch`
  (IMP-D11, still pending) once that command exists, for a fully hands-off multi-title batch
  pipeline.
