# Task: IMP-D18 — add a 4th content category "Others" (sports now; documentaries later) end-to-end

Suggested branch: feature/imp_d18_others_category

> Canonical plan folder: `docs/feature-others-category/` (root `/PLAN.md` is the gitignored live copy; this
> identical copy is the tracked one). Decisions log: `docs/feature-others-category/DECISIONS.md`.

## Context
MediaVault encodes content type by **ID prefix** (`mov`/`tv`/`ani` → one of three `library_*.json` files); there is
no explicit category field. We are adding a **4th category, "Others"** — sports today (FIFA football, IPL cricket),
documentaries later — stored under `C:\Media\Sports\...` with a new `C:\Media\library_others.json` keyed by an
`oth-` prefix. The existing web SPA already ships an "Others" tab (`webui/static/data.js`
`CATEGORY_ORDER=["movies","series","anime","other"]`, label "Others") and `category_of_id`/`_category_of` already
fold unknown prefixes to `"other"` — so most of the work is teaching the **library I/O, the disk walkers, the
ID-numbering, the cloud-profile routing, and the enrichment-skip** about the new prefix, then proving every command
works for an `oth-` entry via the smoke gate. All design decisions are LOCKED (see Decisions Locked below); this plan
bakes them in.

## Goal (verifiable definition of done)
Every user-facing command works correctly for an `oth-` entry, proven by the smoke gate plus a manual FIFA-edition walk:
1. `prep_season oth-football-2026-fifaworldcup-s01 "C:\Media\Sports\Football\FIFA World Cup\FIFA World Cup 2026 (USA-Canada-Mexico)"`
   creates a `season_map` + 6 leaf episodes `…-s01e01..e06` (numbered by filename sort order), written to
   `C:\Media\library_others.json` (NOT `library_movies.json`).
2. `scan_unprepped` and the web reclaim/tree views SEE the 6 sports files (the `C:\Media\Sports` root is walked for "other").
3. `push_group` / `replace_group` / `restore_group` / `fetch_restore … episodes 1-2` / `prep_push_rep_season` all
   operate on the `oth-` season exactly as they do for a TV season (range filter, resume messaging, rollback all reuse TV machinery verbatim).
4. `fetch` routes `oth-` ids to the **Others** Chrome profile + the **Others** Pixel (not the Movies account).
5. `enrich_metadata` / `refresh_online` / `fetch_trivia` **never touch, mis-tag, or crash on** `oth-` entries (sports isn't on TMDB/OMDb).
6. `local_status`, `verify_library`, `sort`, `check`, `web` (Others tab shows the data) all work for `oth-` entries.
7. `pytest -q` AND `pytest tests/smoke -q` are green, with NEW `oth-` smoke coverage (round-trip + season sweep + enrich-skip).
8. `tests/test_entry_schema_guard.py` stays **green and UNCHANGED** (no new entry type), and **no rollback contract changes**.

## Decisions Locked (baked in — do NOT re-ask; full rationale in DECISIONS.md)
- **Category identity:** name **"Others"**, ID prefix **`oth-`**, new file **`C:\Media\library_others.json`** (underscore form).
- **No folder move.** Files stay at `C:\Media\Sports\...`. "Others" maps to a **LIST** of top-level subdirs under `C:\Media`:
  `["Sports"]` now; `"Documentary"` appended later with no code change.
- **Data model = TV season reused, NO new entry type.** The edition folder (`FIFA World Cup 2026 (USA-Canada-Mexico)`) is
  ONE season; its 6 half-files become 6 episodes; a match = 2 adjacent episodes. Reuse `season_map` parent + `leaf` children.
- **Chosen ID scheme (tournament-as-season; trade-off in Risks):**
  - season/base id `oth-<sport>-<year>-<competition>-s01` → `oth-football-2026-fifaworldcup-s01` (sport + competition spelled out: `football`, `cricket`).
  - episode ids `…-s01e01 … -s01e06`, numbered by **filename sort order** (each half = one episode).
  - match = `(e01,e02)`, `(e03,e04)`, `(e05,e06)`; `episodes 1-2` = the Spain match.
  - The `-s01` segment is REQUIRED so `mvcommon.episode_num_from_id(child, base)` strips a clean `-s01` and reads
    `e01`→1.0. A bare `oth-…-fifaworldcup-e01` would strip to `-e01`, which the anchored `^[eExX]?(\d+…)$` regex rejects
    (leading dash) → silent 0-match. Using `-s01e01` reuses the TV convention with **zero new parsing rules**.
- **Media-server presentation = "Other / Home Videos" library, filename-as-title, NO scraper.** Keep the user's
  filenames; exact tech spec is already auto-captured by `get_tech_specs` (MediaInfo, main.py:144) into `tech_spec`.
- **Cloud topology = one new Google account + Pixel.** Others profile `C:\Media\Utils\ChromeProfile_Others`; new
  `DEVICE_ALIASES["others"]` (serial is a user PREREQUISITE — `<NEW_PIXEL_SERIAL>` placeholder). Replication to a 2nd
  account is **deferred to IMP-X1** (Open Decision).

## Files affected
- `mvcommon.py` — add `LIBRARY_OTHERS` constant; `load_library` reads the 4th file; `save_library` routes `oth-` (Step 2 candidate).
- `main.py` — `DEVICE_ALIASES["others"]`; `cmd_prep_season` `oth-` episode numbering; the 4 disk-walk root sites
  (`cmd_recover --scan` 865, `cmd_scan_unprepped` 5457, `collect_reclaimable` 6195, `_CATEGORY_ROOT_SUBDIR`/`build_tree`
  6852/7031) made list-capable (Step 4 candidate); `_gather_enrich_units` enrich-skip (1875/1889).
- `mainfetch.py` — `CHROME_PROFILES["others"]`; `ID_PREFIX_PROFILE += ("oth","others")`.
- `tools/warm_profiles.py` — **docstring nit only** (it auto-derives profiles from `CHROME_PROFILES.keys()` — no code change).
- `webui/static/data.js`, `webui/server.py` — VERIFY only (`oth-`→"other" + `by_category` already present); add a guard test.
- `tests/conftest.py` — `sandbox` fixture: redirect `LIBRARY_OTHERS` (mvcommon-only — `main` doesn't bind it; binding-hazard safety-critical).
- `tests/test_others_category.py` (NEW), `tests/test_web_datafns.py`, `tests/smoke/test_smoke_all_commands.py` — `oth-` coverage.
- `improvements/improvements_tierD.md`, `improvements/PRIORITY.md`, `docs/priority-graph/priority-graph.html` — register IMP-D18.
- `ARCHITECTURE.md`, `README.md`, `docs/README.md`, `BEST_PRACTICES.md`, `improvements/JELLYFIN_SETUP_GUIDE.md` — docs.

## Approach (end-to-end before the steps)
The change is "teach the prefix-encoded system about one more prefix." Foundationally (Step 1) declare the new category's
identity constants across `mvcommon`/`mainfetch`/`main`. Then the two genuinely-ambiguous structural pieces — the
**JSON write-routing** (Step 2) and the **disk-walk roots going list-capable** (Step 4) — are multi-candidate with a
**user checkpoint after the judge**. The data-model core (Step 3) adds position-based `e01..eNN` numbering for sports
files in `cmd_prep_season` (the rollback-gated `cmd_prep_push_rep_season` calls it and otherwise reuses everything, so it
needs NO change). The cross-command-integrity guard (Step 5) makes the three online-enrichment commands skip `oth-` at
their single shared chokepoint `_gather_enrich_units`. Tests (Steps 6–7) close the conftest safety hole and add `oth-`
coverage. Steps 8–10 verify the web, register the IMP, update docs, and run the smoke gate last.

## Execution protocol — multi-candidate user-checkpoints (OVERRIDES auto-commit for THIS plan)
For EVERY step marked `[candidates: N]` (Steps 2 and 4), after the N candidate executors run and the **judge** produces
its DECISION, the orchestrator (the top-level session) MUST **NOT auto-commit and NOT auto-proceed**. It MUST:
1. **PAUSE** and present the judge's full analysis + recommended winner to the **USER** as a checkpoint.
2. **WAIT** for the user to either (a) accept the judge's recommended candidate, or (b) choose a different candidate.
3. Only after the user's explicit choice: merge that candidate, commit, and move on.
Each such step is tagged **⛔ USER-CHECKPOINT after judge** so it cannot be missed. This overrides the normal
auto-commit-after-judge behavior for this plan only.

## Steps

- [x] 1. [model: sonnet] [effort: low] Declare the "Others" category identity constants across mvcommon / mainfetch / main.
  - Files: `mvcommon.py` (~21-23 + `load_library` ~554), `mainfetch.py` (~32-40), `main.py` (`DEVICE_ALIASES` ~82).
  - Details:
    - `mvcommon.py`: add `LIBRARY_OTHERS = r'C:\Media\library_others.json'` directly after `LIBRARY_ANIME` (line 23).
      In `load_library` (line 554) add `LIBRARY_OTHERS` to the read list: `for path in [LIBRARY_MOVIES, LIBRARY_SERIES,
      LIBRARY_ANIME, LIBRARY_OTHERS]:`. (Do NOT touch `save_library` here — that routing is Step 2's candidate.)
    - `mainfetch.py`: add `"others": r"C:\Media\Utils\ChromeProfile_Others",` to `CHROME_PROFILES` (line 32-36); add
      `("oth", "others")` to `ID_PREFIX_PROFILE` (line 39) — place it anywhere (no prefix-collision with ani/tv/mov),
      keep `DEFAULT_PROFILE = "movies"` unchanged. `profile_for_id("oth-…")` must then return `"others"`.
    - `main.py`: extend `DEVICE_ALIASES` (line 82) to `{"movies": "FA69H0300200", "series": "FA75V0303405", "others":
      "<NEW_PIXEL_SERIAL>"}`. Leave the literal placeholder `<NEW_PIXEL_SERIAL>` and a `# TODO(user): real Others Pixel
      serial — prerequisite` comment. (Until replaced, `device others` will fail at adb — expected; it is a documented prerequisite.)
    - Do NOT add the JSON write-routing or the disk roots here (Steps 2 and 4).
  - Acceptance: `python -c "import mvcommon, mainfetch; print(mvcommon.LIBRARY_OTHERS); print(mainfetch.profile_for_id('oth-football-2026-fifaworldcup-s01e01'))"`
    prints the others path and `others`. `python -c "import main; print(main.DEVICE_ALIASES['others'])"` runs. `python -m pytest tests/smoke -q` stays green.

- [x] 2. [model: opus] [effort: high] [candidates: 2] **⛔ USER-CHECKPOINT after judge** — route `oth-` entries in `save_library` (and harden the `else→movies` trap).
  - Files: `mvcommon.py` (`save_library` ~568-596).
  - Details: Today `save_library` (line 574-583) buckets by prefix with a dangerous `else → mov_data` fallback — so an
    `oth-` entry is **silently written into `library_movies.json`** (a latent data-loss trap). Add `oth-` routing into
    `library_others.json` and ensure the write loop (line 587) persists all 4 files. The 3 existing prefixes must keep
    routing exactly as before (pinned by `tests/test_entry_schema_guard.py` round-trip + the smoke suite).
  - Acceptance: an `oth-…` entry round-trips through `save_library`→`load_library` landing ONLY in `library_others.json`;
    `mov-`/`tv-`/`ani-` entries land unchanged; `python -m pytest tests/test_entry_schema_guard.py tests/smoke -q` green.
  - Judge criteria (most important first): (1) **correctness** — `oth-` lands in `library_others.json`, the other three
    files are byte-for-byte unchanged for the same input (round-trip + schema-guard green); (2) **safety of the
    unknown-prefix fallback** — does an unrecognized prefix still silently go to movies, or is it surfaced/explicit so a
    future 5th category can't be lost?; (3) **surgical-ness & house-style fit** (diff size, mirrors existing idiom);
    (4) **extensibility** for the next category prefix.
  - Candidate approaches:
    - A: **Minimal `elif`.** Add `elif key.startswith("oth"): oth_data[key] = val`, keep the existing `else → mov_data`
      legacy fallback verbatim, and add `(LIBRARY_OTHERS, oth_data)` to the atomic-write list. Smallest possible diff;
      exactly mirrors the current if/elif idiom.
    - B: **Table-driven prefix→file map.** Replace the if/elif chain with an ordered
      `_PREFIX_TO_LIB = [("mov", LIBRARY_MOVIES), ("tv", LIBRARY_SERIES), ("ani", LIBRARY_ANIME), ("oth", LIBRARY_OTHERS)]`
      consumed by a loop, and make the no-match fallback **explicit** (still routes legacy/no-prefix keys to movies for
      back-compat, but emits a one-line stderr warning naming the unrouted key so a future unknown prefix can never vanish
      silently). Hardens the `else→movies` trap and makes the next category a one-line table edit.

- [x] 3. [model: opus] [effort: high] Teach `cmd_prep_season` to number `oth-` sports files `e01..eNN` by filename sort order.
  - Files: `main.py` (`cmd_prep_season` ~3563-3611). Read `docs/testing-strategy.md` before the test note below.
  - Details: The Tivimate-derived sports filenames (`<date> - <TeamA> vs <TeamB> - <Half> - <Stage> - <Group> [<res> UHD].mkv`)
    carry **no `SxxExx` and no absolute-episode number**, so the existing Strategy-1 (`[sS]\d+[eE](\d+)`) and Strategy-2
    (anime, gated `elif is_anime`) extract NOTHING and every file is silently SKIPPED. Add a branch: when
    `base_id.startswith("oth")` (a NEW `is_other = base_id.startswith("oth")` alongside `is_anime` at line 3572), assign
    `ep_num` from the **1-based index of the file in the already-`sorted(files)` list** (line 3568), formatted
    zero-padded as `f"{idx:02d}"` so ids are `…-s01e01`. The id is built by the existing non-anime arm
    `full_id = f"{base_id}e{ep_num}"` (line 3608) → `oth-football-2026-fifaworldcup-s01e01`. Do NOT run the SxxExx combined-alias
    logic for `oth-` (sports halves are never combined-episode files). Leave TV/anime behavior byte-identical.
    - Confirm (do NOT change): `cmd_prep_push_rep_season` (line 5578) calls `cmd_prep_season` for the prep, then only
      FILTERS children via `mvcommon.episode_num_from_id(mid, base_id)` (line 5594) and builds the rollback resume
      messaging from it (5625-5630). For `oth-…-s01e01` with base `oth-…-s01` the helper strips `-s01`→`e01`→1.0, so the
      range filter, the resume `episodes N-M` string, and the rollback path all work UNCHANGED → **no rollback-contract
      touch** (the season resume-range messaging is change-gated; this step does not alter it).
    - Document the on-disk naming convention in the docstring + the architect step: name halves/periods so they sort in
      play order (`First`/`Second`, or `1`/`2`, or `Q1..Q4`); verified that the sample folder sorts to Spain-1st,
      Spain-2nd, Uruguay-1st, Uruguay-2nd, Norway-1st, Norway-2nd = e01..e06.
    - Test note (covered in Step 7): assert on the sample 6 filenames that ids are `…-s01e01..e06` in the documented
      order, via the `sandbox` fixture. Never touch real `C:\Media` files or real `library_*.json`. Run `python -m pytest`
      and fix failures before marking done.
  - Acceptance: in the sandbox, `cmd_prep_season("oth-football-2026-fifaworldcup-s01", <6-file folder>)` creates a `season_map` +
    6 leaves `…-s01e01..e06` mapped to the files in sort order; TV/anime prep unaffected (existing
    `tests/test_prep_season_episode_parse.py` green). opus: this is the data-model core and edits a shared, rollback-adjacent function.

- [x] 4. [model: opus] [effort: high] [candidates: 2] **⛔ USER-CHECKPOINT after judge** — make the disk-walk category roots list-capable and wire `"other" → ["Sports"]`.
  - Files: `main.py` — `cmd_recover --scan` roots (865), `cmd_scan_unprepped` categories (5457-5461), `collect_reclaimable`
    categories (6194-6198), `_CATEGORY_ROOT_SUBDIR` (6852) + its consumer `build_tree` (7031-7032).
  - Details: Today "other" has **no on-disk root**, so sports files are invisible to `scan_unprepped`, the web reclaim
    walk (`collect_reclaimable`), the web `tree`, and `recover --scan`. A category's roots must become a **LIST** of
    top-level subdirs (so `["Sports"]` now, `+["Documentary"]` later with no code change) while the 3 existing
    single-folder categories keep working byte-identically. All FOUR walk sites must walk `C:\Media\Sports` for "other":
    - `cmd_recover --scan` (865) — so a leftover `.mediavault_txn.json` journal in a Sports edition folder (from a
      push/replace rollback) is found. **(This site was easy to miss — it is not optional.)**
    - `cmd_scan_unprepped` (5457) — its loop `for cat_name, lib_file, folder_path in categories` must handle a list of
      folders for "other" (the `known_paths` build already skips `season_map`/`multi_ep_alias`; `oth-` leaves are
      physical, so no KeyError risk).
    - `collect_reclaimable` (6194) — add the Sports root to the disk-first walk.
    - `_CATEGORY_ROOT_SUBDIR`/`build_tree` (6852/7031) — give "other" a real root so its leaves nest under the correct
      bucket (today `.get("other")` is `None` → falls back to `LOCAL_ROOT`).
  - Acceptance: with a seeded `oth-` library + a fake file under `<sandbox>/Media/Sports/...`, `scan_unprepped`,
    `collect_reclaimable`, `build_tree`, and `recover --scan` all SEE the Sports subtree; `Movies`/`Series`/`Anime`
    walking is unchanged (smoke + `test_web_datafns.py` green); appending a 2nd subdir for "other" needs no walker code change.
  - Judge criteria (most important first): (1) **correctness + the existing-3-unchanged invariant** — all 4 walkers see
    `C:\Media\Sports` for "other" AND each of Movies/Series/Anime still walks exactly its one folder (smoke + scan tests
    green); (2) **list-capability** — appending `"Documentary"` later is a pure data edit, no walker code change; (3)
    **drift-resistance** — can two walkers disagree on the roots, or is there one source of truth?; (4) **surgical-ness /
    blast radius** vs the existing code.
  - Candidate approaches:
    - A: **Per-site minimal.** Make `_CATEGORY_ROOT_SUBDIR` values lists (`{"movies":["Movies"], …, "other":["Sports"]}`),
      and at each of the 4 walk sites iterate the per-category folder list (a 1–2 line loop change per site). Smallest,
      most local diff; each site keeps its own root literal except the shared subdir map.
    - B: **Single source of truth.** Introduce one `CATEGORY_ROOTS = {"movies":["Movies"], "series":["Series"],
      "anime":["Anime"], "other":["Sports"]}` (category → list of root subdirs) and refactor ALL four walk sites
      (+ the `_CATEGORY_ROOT_SUBDIR` consumer) to derive their roots from it, so no walker hardcodes folder names and
      adding "Documentary" is one line. Larger diff, but eliminates the 4-way drift surface.

- [x] 5. [model: opus] [effort: high] Make `enrich_metadata` / `refresh_online` / `fetch_trivia` SKIP `oth-` entries (cross-command integrity).
  - Files: `main.py` (`_gather_enrich_units` ~1855-1924 — the single chokepoint all three commands call at 2604 / 3085 / the enrich path).
  - Details: All three online commands route through `_gather_enrich_units`. Today that loop buckets a `season_map`
    (line 1895) and any leaf with a `parent_id` (line 1912) into "show" units **regardless of category**, so `oth-`
    seasons/episodes WOULD be sent to TMDB — which for sports would (a) match a wrong title, (b) stamp a wrong
    `metadata.tmdb_id`, (c) call `rename_folder` to stamp a bogus `{tmdb-…}` token on the Sports edition folder, and
    (d) download wrong posters. Add an early skip at the top of the `for mid, entry in library.items()` loop (line 1889):
    skip when `category_of_id(mid) == "other"` (chokepoint guard — one place covers all three commands; `refresh_online`
    and `fetch_trivia` then never even see an `oth-` unit). Keep the existing `multi_ep_alias` skip. No `--library other`
    option is added (out of scope); the guard is unconditional so even a no-arg whole-library `enrich_metadata` run is safe.
  - Acceptance: with a seeded library mixing `mov-`/`tv-`/`oth-` entries, `_gather_enrich_units(...)` returns ZERO units
    whose ids start with `oth`; a dry-run `enrich_metadata` over the whole library reports 0 `oth-` units and makes no
    TMDB call / no `rename_folder` for `oth-`; `refresh_online`/`fetch_trivia` likewise skip them. opus: a mis-tag here is
    destructive (wrong folder rename + wrong art), so the guard must be airtight; this is the cross-command risk the smoke gate exists for.
  - Single-executor rationale (NOT multi-candidate): although the prompt floated this as a candidate, the correct location
    is the obvious single chokepoint `_gather_enrich_units` and the obvious key is `category_of_id(mid) == "other"`. The
    alternatives collapse — a per-command guard would triplicate the same check, and `--library` exclusion would NOT
    protect the no-arg whole-library run. Per the multi-candidate guardrails ("the right answer is obvious from context"),
    this is single-executor.

- [x] 6. [model: opus] [effort: high] Close the conftest safety hole: redirect `LIBRARY_OTHERS` (mvcommon-only) in the `sandbox` fixture.
  - Files: `tests/conftest.py` (`sandbox` fixture ~57-104). Read `docs/testing-strategy.md` first.
  - Details: The `sandbox` fixture monkeypatches `LIBRARY_MOVIES/SERIES/ANIME` + `LOCAL_ROOT` on BOTH `mvcommon` and
    `main` (the binding hazard: `from mvcommon import LIBRARY_*` creates a separate `main`-namespace binding). It does NOT
    patch `LIBRARY_OTHERS` — so after Step 2, any test that saves an `oth-` entry would write `library_others.json` to the
    **REAL `C:\Media`**. Add `lib_others = lib_dir / "library_others.json"`, include `("LIBRARY_OTHERS", str(lib_others))`
    just AFTER the dual-patch loop (lines 71-79 — keep the `assert "C:\\Media" not in path` guard so a missed patch trips it),
    and add `"lib_others": lib_others` to the yielded dict. Patch ONLY `mvcommon.LIBRARY_OTHERS`
    (`main` does NOT import/bind `LIBRARY_OTHERS` — patching it would raise; place the setattr just after the loop). Never touch real `C:\Media`/`library_*.json`. Run `python -m pytest`
    and fix failures before marking done.
  - Acceptance: `python -m pytest tests/test_entry_schema_guard.py tests/test_web_datafns.py -q` green; a quick assertion
    that `mvcommon.LIBRARY_OTHERS` points under `tmp_path` (not `C:\Media`) inside the fixture (`main` has no `LIBRARY_OTHERS` binding).
    opus per the testing rules: conftest fixture changes carry the binding hazard and are a correctness trap.

- [x] 7. [model: sonnet] [effort: medium] Add `oth-` test coverage: routing + prep numbering unit tests, smoke round-trip + season sweep + enrich-skip, web datafns prefix.
  - Files: `tests/test_others_category.py` (NEW), `tests/test_web_datafns.py`, `tests/smoke/test_smoke_all_commands.py`.
    Read `docs/testing-strategy.md` first to confirm fixtures (`sandbox` for library I/O; `mock_device` for push/restore; `mock_fetch`/`capsys` as needed).
  - Details:
    - `tests/test_others_category.py` (NEW, `sandbox` fixture): (a) **routing** — save an `oth-football-2026-fifaworldcup-s01e01` leaf,
      assert it lands ONLY in the sandbox `library_others.json` and reloads via `load_library`; assert `category_of_id`
      and `mainfetch.profile_for_id` return `"other"`/`"others"`. (b) **prep numbering** — create a folder of the 6 sample
      filenames (zero-byte/stub via the established make-file helper) and assert `cmd_prep_season("oth-football-2026-fifaworldcup-s01",
      folder)` yields children `…-s01e01..e06` mapped to the files in the documented sort order, plus a `season_map`
      parent. (c) **enrich-skip** — seed a mixed `mov-`/`oth-` library and assert `_gather_enrich_units` returns no
      `oth-` unit.
    - `tests/test_web_datafns.py`: add an assertion that an `oth-` id classifies as category `"other"` (mirror the existing
      prefix→category tests) so the web bucketing is pinned.
    - `tests/smoke/test_smoke_all_commands.py`: add a small `_seed_others_season(sandbox, make_video)` (mirroring the
      existing `_seed_*_season` helpers) creating `season_map` `oth-football-2026-fifaworldcup-s01` + 3 real (>200KB) leaf episodes
      `…-s01e01..e03` under `<LOCAL_ROOT>/Sports/...`, all written via the 4-file `save_library`. Then: an **oth- round-trip**
      (prep/scan/local_status/sort/verify_library don't crash and see the entries) and an **oth- season sweep**
      (`push_group`/`restore_group`/`fetch_restore … episodes 1-2` under `mock_device`, asserting the right 2 episodes flip
      and no crash) and an **enrich-skip** assertion (a dry-run `enrich_metadata` over the fixture touches 0 `oth-` units,
      makes no `rename_folder`). Keep each smoke test to "no crash + correct top-level effect"; use rglob+`.name`, not
      bracketed-id globs. Never touch real `C:\Media`/`library_*.json`. Run `python -m pytest` and fix failures before marking done.
  - Acceptance: `python -m pytest tests/test_others_category.py tests/test_web_datafns.py -q` green; `python -m pytest tests/smoke -q`
    green in < ~30s with the new `oth-` round-trip + season-sweep + enrich-skip assertions passing.

- [x] 8. [model: sonnet] [effort: low] Verify the web "Others" tab renders `oth-` data (light wiring confirm; no new feature code).
  - Files: `webui/static/data.js` (~97-132), `webui/server.py` (`_category_of` 655, `_library_summary` 666); confirm `main.items_payload`/`collect_reclaimable`/`build_tree` "other" buckets.
  - Details: The SPA already ships the Others tab (`CATEGORY_ORDER` includes `"other"`, `CATEGORY_META.other.label="Others"`,
    client `_category_of` folds unknown→`"other"`). VERIFY (and only fix if a real gap is found): (a) `data.js`'s
    client-side category function returns `"other"` for an `oth-` id (it should via the else-branch); (b) `server.py`
    `_category_of`/`_library_summary` count `oth-` leaves under "other"; (c) `items_payload`'s `by_category` (main.py:6397,
    already `{… ,"other":0}`) increments for `oth-` leaves; (d) `/api/items` + `/api/reclaim` + `/api/tree` render the
    Sports subtree (depends on Step 4). If everything is already correct, this step is a documented VERIFICATION (note it in
    the completion report) plus the `test_web_datafns.py` assertion from Step 7 — do NOT add speculative UI code (the
    Others tab is pre-built). Surgical only.
  - Acceptance: launching `python main.py web` (manual, in Manual test commands) shows the FIFA edition under the Others
    tab once an `oth-` season is prepped; `python -m pytest tests/test_web_datafns.py tests/smoke -q` green.

- [x] 9. [model: sonnet] [effort: medium] Register IMP-D18 in the tier file, PRIORITY.md, and the priority graph (one change, all three).
  - Files: `improvements/improvements_tierD.md`, `improvements/PRIORITY.md`, `docs/priority-graph/priority-graph.html`.
  - Details: Per the maintenance protocol at the bottom of PRIORITY.md, update all three together:
    - `improvements_tierD.md`: add an `## IMP-D18: "Others" content category (sports now; documentaries later)` block in
      the same shape as D17 (Category/Priority/Files/Current behavior/Proposed change/Rationale/Goal/Effort/Risk/If
      skipped/Status). Set `Status: pending` now; the **architect/PR flips it to `done` on implementation** (note this in
      the block, mirroring how D16/D17 were marked done on their branch).
    - `PRIORITY.md`: add an IMP-D18 row to **Band 1** (high-value, user-requested, moderate risk — new category plumbing);
      bump **Last updated**; update the `👉 SUGGESTED NEXT TASK` note to mention IMP-D18 is in flight on its branch (keep
      IMP-S1/S2 as the standing headline next). On completion the architect moves D18 into the ✅ DONE list and bumps the count.
    - `priority-graph.html`: add a `D18` node to the `TASKS` array mirroring the existing tuple shape (e.g.
      `["D18","Others category (sports)","D","high","todo", "<one-line note>"]` — match the exact columns the file uses,
      see the C18/D17 nodes) and an EDGE from `D18` to `E14` (it feeds the existing Others tab) and a note that it relates
      to deferred `X1` (replication) and `S1` (Jellyfin). Keep the array valid JS (no trailing-comma/quote breakage).
  - Acceptance: IMP-D18 present in tierD with a status line; a Band-1 row + refreshed Last-updated + NEXT note in
    PRIORITY.md; a `D18` node (+ edge to E14) in the graph; the three agree. (Final `done` flip happens in Step 10's PR.)

- [x] 10. [model: opus] [effort: high] Architect: document the behavior change + flip IMP-D18 to done.
  - Files: `ARCHITECTURE.md`, `README.md`, `docs/README.md`, `BEST_PRACTICES.md`, `improvements/JELLYFIN_SETUP_GUIDE.md`;
    plus the `done` flips in `improvements/improvements_tierD.md` + `PRIORITY.md` + `priority-graph.html`.
  - Details (documented behavior change — call it out explicitly, keep edits surgical):
    - `ARCHITECTURE.md`: §6.1 (the three library files → now FOUR; add `LIBRARY_OTHERS`/`library_others.json` + the
      `oth-` routing in `save_library`); §6.2 (ID format — add the `oth-<sport>-<year>-<competition>-s01eNN` canonical
      shape + the position-numbered sports convention + the spelled-out sport codes (`football`, `cricket`)); §3 repo layout
      (the new `C:\Media\Sports` root + `ChromeProfile_Others`); the category/prefix system + §14 config (the new
      `DEVICE_ALIASES["others"]`, `CHROME_PROFILES["others"]`, `ID_PREFIX_PROFILE` entry); the §2178 shared-constants
      table (add `LIBRARY_OTHERS`). State that the disk-walk roots are now per-category LISTS (list-capable) and that
      enrichment skips `oth-`.
    - `README.md`: the CLI reference + "Manual ID conventions" (~472) + "File layout" + "Requirements/prerequisites" +
      the Chrome-profiles list (~99-102) — add the Others category, the `oth-` id scheme, the sports folder layout, the
      4th account/profile, and the "name halves so they sort in play order" convention.
    - `docs/README.md`: add the `docs/feature-others-category/` entry to the index.
    - `BEST_PRACTICES.md`: a short "archiving sports / Others videos" note (folder layout + id scheme + half-naming).
    - `improvements/JELLYFIN_SETUP_GUIDE.md`: a "Sports / Others library" section — set up an **"Other Videos / Home
      Videos"**-type Jellyfin/Plex/Emby library pointed at `C:\Media\Sports`, filename-as-title, **no metadata scraper**
      (2026 guidance for Plex "Other Videos", Jellyfin "Home Videos", Emby "Mixed/Home videos": these library types do
      filename-as-title with no online agent — exactly the sports use-case; cite in the guide).
    - Flip IMP-D18 `Status: pending → done` in `improvements_tierD.md`, move it to ✅ DONE in `PRIORITY.md` (+bump count),
      and set the graph node `…,"done","done",…` — keeping all three in sync.
  - Acceptance: docs describe the 4th category accurately; `docs/README.md` indexes the feature folder; IMP-D18 reads
    `done` in all three trackers consistently. opus: cross-file documented-behavior change spanning architecture + user docs + the media-server guide.

- [ ] 11. [model: sonnet] [effort: medium] Final verification + smoke gate (the cross-command gate, last).
  - Files: none (runs the suites).
  - Details: Run the full Verification block below from the repo root using `python -m pytest`. Confirm
    `tests/test_entry_schema_guard.py` is green AND its diff is empty (no new entry type). Fix any failure before the PR.
  - Acceptance: every command in the Verification block is green; `python -m pytest tests/smoke -q` (the FINAL gate) green in < ~30s.

## Risks and edge cases
- **ID-scheme dash trap (load-bearing):** `oth-…-fifaworldcup-e01` (bare, no `-s01`) strips under `episode_num_from_id` to
  `-e01`, which the anchored `^[eExX]?(\d+…)$` regex REJECTS (leading dash) → silent 0-match (the exact IMP-C18 failure
  mode). The chosen `-s01e01` shape avoids it. Do NOT change `episode_num_from_id` (it is the change-gated shared helper) —
  fix it in the id scheme, not the regex.
- **Sports filenames have no episode marker:** without Step 3's position-based branch, `cmd_prep_season` silently SKIPS
  every sports file (Strategy-1 finds no `SxxExx`, Strategy-2 is anime-only). Step 3 is required, not optional.
- **Filename sort order = viewing order assumption:** position-numbering trusts `sorted(files)` to equal play order. The
  sample folder verifies (Spain/Uruguay/Norway, First<Second). Documented convention: name halves/periods so they sort
  (`First`/`Second`, `1`/`2`, `Q1..Q4`). A mis-sorting name would mis-number — an editing concern, not a crash.
- **`else→movies` latent trap:** until Step 2 lands, an `oth-` entry written by any path goes into `library_movies.json`.
  Step 1 intentionally does NOT write any `oth-` entry before Step 2; Step 2's candidate B hardens the trap.
- **conftest safety hole (Step 6):** without the `LIBRARY_OTHERS` redirect, an `oth-` save in a test escapes to real
  `C:\Media`. Step 6 must precede any test that saves an `oth-` entry (Steps 7+). The `assert "C:\\Media" not in path` guard backstops it.
- **`recover --scan` blind spot (Step 4):** a push/replace crash on an `oth-` season leaves a journal in the Sports folder;
  if Step 4 misses site 865, `recover --scan` won't find it. Enumerated explicitly in Step 4.
- **`cmd_recover --scan` and whole-library iterators on `oth-` entries:** `oth-` leaves are physical (have
  `folder_path`/`filename`) and `oth-` season_maps are `type=="season_map"` (skipped by existing guards keyed on `type`,
  not prefix) — so NO new `KeyError`/`multi_ep_alias` crash class is introduced. Verified against the ENTRY_TYPE_KEYS rule.
- **`guess_manual_id` (web UNPREPPED suggestion) defaults a Sports file to `mov-`** (it only special-cases Series/Anime
  paths, main.py:5909). Cosmetic only — the user preps `oth-` ids by hand; the suggested id is an editable placeholder,
  never auto-prepped. Optional polish (add a `Sports`→`oth` branch) is OUT OF SCOPE.
- **`<NEW_PIXEL_SERIAL>` placeholder:** `device others` fails at adb until the user supplies the real serial — a
  documented prerequisite, not a bug.

## Consumer Impact Analysis
Adding `library_others.json` + the `oth-` routing prefix + the list-capable category roots IS a shared-data-contract
change. `ENTRY_TYPE_KEYS` is consulted as the authority: **no entry type is added/changed** (sports reuse `season_map` +
`leaf`), so `ENTRY_TYPE_KEYS` and `tests/test_entry_schema_guard.py` stay UNCHANGED (asserted in Step 11). Every consumer
of `load_library`/`save_library` and the category/prefix/root maps is enumerated below.

| # | Site | Line(s) | Access | Verdict | Why |
|---|------|--------|--------|---------|-----|
| 1 | `mvcommon.load_library` | 554 | reads `[LIBRARY_MOVIES,SERIES,ANIME]` | needs-fix | add `LIBRARY_OTHERS` to the read list — Step 1 |
| 2 | `mvcommon.save_library` | 574-587 | prefix-route, `else→mov_data` | needs-fix | `oth-` silently lands in movies today — Step 2 |
| 3 | `main.category_of_id` | 6065-6078 | `else → "other"` | safe | `oth-` already returns `"other"` (verified) |
| 4 | `webui.server._category_of` | 655-663 | `else → "other"` | safe | `oth-` already returns `"other"`; counts under "other" |
| 5 | `mainfetch.profile_for_id` | 509-513 | `ID_PREFIX_PROFILE` then `DEFAULT_PROFILE` | needs-fix | without `("oth","others")` an `oth-` fetch uses the **movies** account — Step 1 |
| 6 | `main._gather_enrich_units` | 1889-1924 | buckets season_map (1895) + any `parent_id` leaf (1912) regardless of category | needs-fix | would TMDB-resolve/mis-tag/rename sports — Step 5 skip |
| 7 | `main.cmd_scan_unprepped` | 5457-5481 | `categories` triple; skips season_map/alias then derefs `folder_path`/`filename` | needs-fix | Sports root not walked → sports invisible (no crash: `oth-` leaves are physical) — Step 4 |
| 8 | `main.collect_reclaimable` | 6194-6214 | disk-walk `categories` triple | needs-fix | Sports root not walked → sports invisible in web reclaim — Step 4 |
| 9 | `main.cmd_recover --scan` | 865-874 | `roots` triple | needs-fix | a journal in a Sports folder is unfindable by `--scan` — Step 4 |
| 10 | `main._CATEGORY_ROOT_SUBDIR` / `build_tree` | 6852 / 7031-7032 | `.get(cat)` → None for "other" → `LOCAL_ROOT` | needs-fix | give "other" a real root list so leaves nest correctly — Step 4 |
| 11 | `main.items_payload` | 6397-6414 | iterates `library.items()`, `by_category` has "other" | safe | `oth-` leaves surface under "other" once `load_library` returns them (Step 1) |
| 12 | `webui.static/data.js` | 97-132 | `CATEGORY_ORDER`/`CATEGORY_META` include "other"; `_category_of` else→"other" | safe | Others tab pre-built; pinned by the Step 7 datafns test |
| 13 | `main.cmd_prep_season` | 3563-3611 | episode-id derivation | needs-fix | sports files have no episode marker → skipped; add position numbering — Step 3 |
| 14 | `cmd_prep_push_rep_season` (+ resume msg) | 5578/5594/5625 | calls `cmd_prep_season`, filters via `episode_num_from_id` | safe | inherits Step 3; helper parses `-s01e01`; **rollback contract untouched** |
| 15 | whole-library iterators (sort, local_status, reclaim, items) derefing physical keys | various | guard on `type in (season_map,multi_ep_alias)` | safe | `oth-` leaves are physical; `oth-` season_maps are `type=="season_map"` → existing skips apply; no new crash class |
| 16 | `tests/conftest.py` `sandbox` | 71-104 | dual-patches `LIBRARY_MOVIES/SERIES/ANIME` | needs-fix | missing `LIBRARY_OTHERS` patch → an `oth-` save escapes to real `C:\Media` — Step 6 |
| 17 | `tests/test_entry_schema_guard.py` | whole file | ENTRY_TYPE_KEYS-driven, `tv-` ids | safe | no new entry type → stays green, UNCHANGED (asserted Step 11) |
| 18 | `main.guess_manual_id` | 5909-5914 | Series/Anime path → tv/ani, else `mov` | safe (cosmetic) | a Sports file is suggested as `mov-`; editable placeholder, never auto-prepped; out of scope |

Every grepped consumer of `load_library`/`save_library` and the category/prefix/root maps appears above with a verdict;
each `needs-fix` row names its fixing step. No changed key was found with zero consumers.

## Cross-cutting guards (stated explicitly)
- **No rollback-contract change.** This feature reuses `cmd_push`/`cmd_replace`/`cmd_restore`/`cmd_push_group`/
  `cmd_restore_group`/`cmd_prep_push_rep_season` **verbatim** for `oth-` ids. Nothing in the journal format/durability
  (`fsync`+`os.replace`), the PONR locations/`mark_point_of_no_return()`, what is recorded, the O-1/O-2 split, the
  `RollbackHardFail` contract, or the `cmd_prep_push_rep_season` season resume-range messaging is modified — the resume
  messaging already parses `oth-…-s01e01` correctly via `episode_num_from_id`. Per CLAUDE.md / `docs/feature-auto-rollback/
  ROLLBACK_MECHANISM.md` §10 (change-gate), this plan does NOT trip the rollback change-gate. If any executor finds a step
  would alter rollback behavior, STOP and surface it as a user decision.
- **`ENTRY_TYPE_KEYS` unchanged.** Sports reuse `season_map` + `leaf`; no structural entry type is added/renamed/removed.
  `tests/test_entry_schema_guard.py` stays green and its diff stays empty (Step 11 asserts this).

## Related-improvements impact
- **IMP-E14 (media-type SPA / Others tab):** the pre-built Others tab finally gets REAL data — `items_payload`/`/api/items`
  + the reclaim/tree walks surface `oth-` leaves once Steps 1+4 land. The graph edge `D18→E14` records this.
- **IMP-E3 / IMP-E16 (TMDB enrich + dossier/ratings/trivia):** enrichment MUST skip `oth-` (Step 5) — sports isn't on
  TMDB/OMDb; without the skip, `enrich_metadata` would mis-tag and rename the Sports folder. The dossier simply shows no
  online data for `oth-` (graceful — it already degrades on a missing `tmdb_id`).
- **IMP-X1 / IMP-X2 (replication + topology runbook):** the Others account is the **4th** Google account in the topology.
  Replicating Others chunks to a 2nd account is **deferred to IMP-X1** (Open Decision). X2's topology doc should record
  the 4th account once this lands.
- **IMP-S1 (Jellyfin stand-up):** Step 10 adds a "Sports / Others library" section to `JELLYFIN_SETUP_GUIDE.md` (an
  "Other Videos / Home Videos"-type library, filename-as-title, no scraper).
- **`tools/warm_profiles.py`:** auto-derives profiles from `CHROME_PROFILES.keys()`, so it picks up "others"
  automatically once Step 1 adds it — no code change, only a docstring nit (it currently says "movies / tv / anime").

## Prerequisites for the user (before/at execution)
1. Create a **new Google account** for Others/sports.
2. Sign that account into a fresh Chrome profile at **`C:\Media\Utils\ChromeProfile_Others`** once (so Selenium fetch can attach).
3. Configure the **new Pixel's Google Photos** to back up `/sdcard/Media` at **ORIGINAL quality**.
4. `adb` **authorize** the new Pixel and send its **device serial** (replaces `<NEW_PIXEL_SERIAL>` in `DEVICE_ALIASES`).
5. Nothing else — the `.ts → MKV` conversion + half-split is already done; files already live under `C:\Media\Sports\...`.

## Verification (run from repo root; use `python -m pytest`, never bare `pytest`)
1. `python -m pytest tests/test_others_category.py -q` — routing + prep-numbering + enrich-skip unit tests pass.
2. `python -m pytest tests/test_web_datafns.py -q` — `oth-`→"other" bucketing pinned.
3. `python -m pytest tests/test_entry_schema_guard.py -q` — green AND its diff is empty (no new entry type).
4. `python -m pytest tests/test_prep_season_episode_parse.py -q` — existing TV/anime prep unaffected.
5. `python -m pytest -q` — full suite green.
6. `python -m pytest tests/smoke -q` — **MANDATORY FINAL GATE** (cross-command). This plan touches `main.py`,
   `mainfetch.py`, AND `mvcommon.py`, so per the SMOKE-GATE rule this is the last gate before the plan is done; must be
   green and complete in < ~30s, including the new `oth-` round-trip + season-sweep + enrich-skip coverage.

Per CLAUDE.md, every code-touching step (1-8) must run `python -m pytest tests/smoke -q` green BEFORE its commit; the
git-agent commits per step. The pipeline runs from the MAIN session (orchestrator.md as a playbook — do NOT launch
`orchestrator` via `Task`; nesting depth = 1 would silently fall back to inline execution).

## Out of scope
- **Replication of Others to a 2nd account** (IMP-X1) — deferred; Others is single-account for now.
- **Any sports metadata scraper / online artwork** (TheSportsDB etc.) — filename-as-title only; future Open Decision.
- **`Documentary` as a sub-folder of Others vs its own category** — defer; the list-capable root (Step 4) lets
  `"Documentary"` be appended to "other" with no code change if that path is chosen.
- **A `--library other` enrich option, `guess_manual_id` Sports branch, or any new web UI** — the Others tab is pre-built; no new feature code.
- **No `ENTRY_TYPE_KEYS`/schema change, no rollback change, no `episode_num_from_id` change.**

## Open Decisions (deferred / non-blocking)
- **OD-1 — Replication (IMP-X1):** mirror Others chunks into a 2nd Google account for redundancy (the CSAM-ban
  single-point-of-failure applies to the 4th account too). Deferred; not in scope.
- **OD-2 — Sports metadata/scraper path:** if richer artwork/metadata is ever wanted, evaluate **TheSportsDB** (free
  sports API) + an NFO emitter; today filename-as-title with no scraper is the decision.
- **OD-3 — Documentary placement:** when documentaries arrive, decide `Documentary` as a 2nd subdir under "Others"
  (one-line root edit) vs its own 5th category (`doc-` prefix + `library_docs.json`). The list-capable root keeps both open.
- **OD-4 — Recurring-tournament grouping:** whether IPL 2024/2025 become `…-s01`/`…-s02` of ONE show vs separate
  year-keyed shows. Current scheme keeps year in the id (separate); revisit if season-grouping is desired.
- **OD-5 — New Pixel ADB serial:** pending hardware; `<NEW_PIXEL_SERIAL>` placeholder until the user supplies it.

## Branch name
`feature/imp_d18_others_category` (per `docs/git-pr-conventions.md`: type `feature`, lowercase, underscores, 31 chars < 50).
Canonical plan folder: `docs/feature-others-category/`.

## PR to main (Checkpoint 1 — human-gated; the plan ENDS at "PR created, then STOP")
- **Title (MUST include the IMP code):** `feature: add "Others" content category (sports) end-to-end — IMP-D18`
- **Body order (per `docs/git-pr-conventions.md`):**
  1. The auto-generated Claude Code summary FIRST (Summary / Changes / Test plan).
  2. Then a `## Original task prompt` section containing the **COMPLETE VERBATIM** original task prompt (the Appendix below).
  3. Then the `🤖 Generated with [Claude Code](https://claude.com/claude-code)` trailer.
- The smoke gate (`python -m pytest tests/smoke -q`) must be green before the PR is opened.
- **STOP after creating the PR.** Do NOT `gh pr merge` / merge / push to `main`. Ask the user for explicit confirmation
  (Checkpoint 1). Archiving the merged branch is a later, separate human-gated step (Checkpoint 2).

## Manual test commands (the real FIFA-edition walk — touches REAL library/ADB/Selenium)
Use the chosen ids + the real folder. `fetch_restore`/`push` perform real device/cloud work — run against the edition you
intend to archive, or stop after the "Filtered to N"/scan lines.
1. **Scan sees the 6 sports files:** `python main.py scan_unprepped` → the 6 `.mkv` under
   `C:\Media\Sports\Football\FIFA World Cup\FIFA World Cup 2026 (USA-Canada-Mexico)` are listed as unprepped (the
   `.tivimate_index` sidecar is ignored — not a video extension).
2. **Prep the edition as one season (6 episodes by sort order):**
   `python main.py prep_season oth-football-2026-fifaworldcup-s01 "C:\Media\Sports\Football\FIFA World Cup\FIFA World Cup 2026 (USA-Canada-Mexico)"`
   → creates `season_map` `oth-football-2026-fifaworldcup-s01` + `…-s01e01..e06` (e01/e02 = Spain, e03/e04 = Uruguay, e05/e06 = Norway),
   written to `C:\Media\library_others.json`.
3. **Push + replace one match (or use the one-shot):**
   `python main.py push_group oth-football-2026-fifaworldcup-s01 SIZE_GB 8 episodes 1-2 device others` then
   `python main.py replace_group oth-football-2026-fifaworldcup-s01` — OR the auto-pilot:
   `python main.py prep_push_rep_season oth-football-2026-fifaworldcup-s01 "C:\Media\Sports\...\FIFA World Cup 2026 (USA-Canada-Mexico)" SIZE_GB 8 episodes 1-2 device others`.
   (Each half is 5–7 GB → typically 1 chunk/half at SIZE_GB 8.)
4. **Status:** `python main.py local_status` → the not-yet-pushed `oth-` episodes appear; `python main.py local_status 40gb` bin-packs them.
5. **Fetch + restore the Spain match (episodes 1-2):** `python main.py fetch_restore oth-football-2026-fifaworldcup-s01 episodes 1-2`
   → routes to the **Others** Chrome profile, fetches `…-s01e01`/`…-s01e02`, restores both; `episodes 1-2` selects exactly the Spain match.
6. **Integrity + ordering:** `python main.py verify_library` (no orphans/status-drift for the `oth-` season) and
   `python main.py sort` (the `oth-` entries re-order without error).
7. **Web Others tab:** `python main.py web` → the **Others** tab shows the FIFA edition (folder/season/episode tree,
   real tech-spec chips, archived/local badges); the enrichment dossier shows no online data (expected — sports is skipped).
8. **Enrichment safety:** `python main.py enrich_metadata --no-web` (dry-run default) → reports 0 `oth-` units, performs
   no `rename_folder` and no `{tmdb-…}` stamp on the Sports folder; `python main.py refresh_online` / `fetch_trivia` likewise skip `oth-`.

## Next tasks to start (after IMP-D18)
Re-read `improvements/PRIORITY.md`, but the standing order is unchanged: **IMP-S1** (stand up Jellyfin — now also gains a
Sports/Others library section from this work), then **IMP-S2** (mvdaemon), then the **IMP-A2→A5** config chain. New
follow-ons this feature creates: **IMP-X1** (replicate Others to a 2nd account) and the **OD-2** sports-scraper question.

---

## Appendix: COMPLETE VERBATIM original task prompt (for the PR body)
> You know we have mainly - 3 types of content. Movies . Anime  and TV Series.
>
> Now we are going to add one more type - Others.
> For now this others I will only have sports related videos like IPL for cricket and Fifa videos for Football.
> This will be stored in separate location.
> For example - I have some foot ball videos here
> C:\Media\Sports\Football\FIFA World Cup\FIFA World Cup 2026 (USA-Canada-Mexico)
> I store each of the match in 2 files - one First half - other second half -- these are direct files stored from Tivimate dvr record as .ts files. Later processed converted to Mkv and split into 2 files manually without losing any quality.
>
> I want you to add this other - or name it as something better to sound more like a generic name for all sports . other types of videos -- later we can add other stuff on top of the sports folder inside this. Maybe like some documentary  - maybe that needs a different folder. Basically other videos which are not movie or series or anime.
> I want all of the command to work perfectly . I will create a different google account and configure the pixel for that. But from your side make sure all the command works for the new category also.
> If any decision or ambiguous question or any other approach related question - you can ask me first as decision card. Do not directly or blindly choose any option for this implemetnation.
> I want a full through plan - created using ultra code - or any number of agents or resource usage. DOnt worry about limits for this task. Come up with a complete - comprehensive plan - then we can start working on that first then once confirmed start executing.
>
> As you can see in the folder I already have some sample matches - 1st half and 2nd half stored as files. If you think some different file name or folder structure would work better for our Plex  . jellyfin or Emby usecase let me know also. For now I want all date who played and exact tech spec of the file also . so storing it in this format.
> let me know if any other change or modification you think of for storing all this archival of matches or videoS.
>
> If any decision pending, give me live example in real world usecase complete step by step and  ask me about the different options before you finalize the plan.
> Also, any other related improvements , how this approach will affect that can you eloborate. Also any prerequisite small task you want me to complete before we start this implemenation?
>
> Deliverables: PLAN.md only (no code, no branches) with steps + tests + verification + "Open Decisions".
> Note if we are solving any improvement tasks with this task say C18 for example - IMP-C18 needs to be marked done in improvements_tierC.md on implementation, the architect updates ARCHITECTURE.md/README (documented behavior change), and I want branch name, PR to main, and manual test commands at the end. Also you also need to update the priority graph and suggest me the next starts we can start.
> But since it is a brand new addition for generic or other videdos dont think it will affect any improvements.
>
> For this particular plan add one more change after each of the candidate steps  and the judge's decision - don't auto commit. make the orchestrator - which is you get all details and recommendations from judge and give that decision to me. I will look at them and decide if its good to continue or to use a different candidate approach. Once I do that proceed as usual with that candidate selected. as this as a checkpoint after each multi candidate step and judge's anaysis. Do not worry about limits or usage for this task. You can also set multi candidate for a step if you think that is actually helpfull or more than one way or ambiguous task.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
