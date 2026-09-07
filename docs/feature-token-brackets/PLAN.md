# Task: Canonical `[tmdbid-…]` square-bracket provider-token format (replacing `{tmdb-…}`) + a crash-safe migration command for the real library

Suggested branch: feature/imp_u6_provider_tokens
Framework: v2

## Context

MediaVault stamps a TMDB provider id onto show/movie folder names so Plex/Emby/Jellyfin can force an
exact metadata match instead of fuzzy title search. Today the code only knows one shape —
`{tmdb-12345}` (curly braces, Plex's legacy convention) — in both the sites that WRITE it
(`cmd_enrich_metadata`, `_enrich_after_archive`, `suggest_target_folder`'s placeholder) and the sites
that READ it (`_has_tmdb_token`'s stamping-idempotency guard, `_PROVIDER_TOKEN_RE`'s artwork
ancestor-walk). The user runs Plex Pass lifetime + Emby Premium today and plans to add Jellyfin, and
wants the default to move to square brackets — `[tmdbid-…]`, the Emby/Jellyfin/TRaSH-Guides
convention — while never breaking anything that works today.

**A different tool already ran a similar migration directly against the real library** (branch
`feature/imp_u6_token_brackets`, not read or reasoned from per the user's explicit instruction — this
plan is designed independently). The disk/JSON state that migration left behind (verified fresh, not
taken on faith) is real and load-bearing for this plan:

- `library_movies.json`: 130 `{tmdb-}` → 0 `{tmdb-}`, 135 `[tmdbid-]`. `library_anime.json`: 145
  `{tmdb-}` → 0. Both fully migrated, disk ⇄ JSON consistent (0 broken paths).
- `library_series.json`: 1300 `{tmdb-}` → only 202 STILL carry a brace token, all via ONE gap class —
  the migration touched only the LEAF path segment of each entry's own `folder_path`, so a show whose
  token lives on an ANCESTOR directory (the classic `<Show>/Season NN/` layout) was missed. Concrete
  proof on disk: `Friends (1994) {tmdb-1668}\Friends Season 01 (1994) [tmdbid-1668]` — the season leaf
  is migrated, the show-level ancestor is not. This plan's migration command MUST be ancestor-aware.
- `[tvdbid-…]` (134 series/anime occurrences) is pre-existing, source-provided (release-group naming),
  never MediaVault-generated, and already canonical square-bracket — nothing to migrate there.
- **The CODE was never updated to match.** `_has_tmdb_token`/`_PROVIDER_TOKEN_RE` still only recognize
  `{tmdb-…}`. Verified independently (re-read, not assumed): the very next `enrich_metadata` /
  `prep_push_rep_enrich` run over the now-`[tmdbid-]` library will see "no token" and stamp a SECOND,
  brace-form token — the exact defect class IMP-C23 fixed for case-sensitivity, now for bracket style.
  Worse, `_PROVIDER_TOKEN_RE` also drives the artwork-inheritance ancestor walk
  (`_ancestor_show_folder_image` → `resolve_artwork_path` step iii), so that fallback is **currently
  silently broken for virtually the whole library** (1396 of ~1598 folders are already `[tmdbid-]`/
  `[tvdbid-]` and match nothing). This is a live regression on `main` today, caused by the other tool's
  data migration outrunning this repo's code — fixing detection format-agnosticism fixes it as a
  byproduct of this task, not a separate effort.

## Goal

1. One shared, well-named helper pair (living in `mvcommon.py`, the established single-source-of-truth
   home for exactly this class of previously-duplicated parser — see IMP-C18/A1 precedent) that (a)
   **detects** a provider token in any of `{tmdb-…}`, `[tmdb-…]`, `[tmdbid-…]` (plus `tvdb`/`imdb`
   siblings, case-insensitive, IMP-C23-safe) without ever matching a release-group bracket like
   `[rartv]` or a mismatched-bracket string, and (b) is the **only** place that vocabulary is spelled
   out, so `_has_tmdb_token` and `_PROVIDER_TOKEN_RE` structurally cannot drift apart again (they have
   now drifted three times — C18, C22, C23 — this is the fix that makes a fourth instance impossible,
   not just tested-against).
2. Every site that WRITES a token (stamping, the reclaim-suggestion placeholder, `rename_folder`'s
   help text) emits exactly one canonical format going forward: **`[tmdbid-<id>]`** for TMDB,
   `[tvdbid-<id>]` for the cosmetic TVDB placeholder — see "Research" below for why.
3. A new, real MediaVault command — `migrate_provider_tokens` — built on the already crash-safe
   `cmd_rename_folder`, that finishes what the other tool's migration left undone: ancestor-aware,
   deepest-first, idempotent, dry-run-by-default, with a per-run report.
4. Nothing else changes. Full suite + smoke gate green. No rollback-contract change (confirmed below —
   `cmd_rename_folder` is reused exactly as-is, additive per its own IMP-D17 contract).
5. This PR ships **code + tests only**, on the feature branch, exercised only against fixtures. The
   actual re-run against the real `C:\Media` library is a separate, explicit, user-approved procedure
   documented at the end of this plan — per the user's own words: "for now, just create the plan to do
   this."

## Files affected

| File | Why touched |
|---|---|
| `mvcommon.py` | NEW shared provider-token detect/parse helper + canonical-format constants (Step 1) |
| `main.py` | Rewire `_has_tmdb_token`/`_PROVIDER_TOKEN_RE` onto the shared helper (Step 2); update all 3 emit sites (Step 3); mechanical doc/help-text updates (Step 4); new `cmd_migrate_provider_tokens` + CLI wiring (Step 6) |
| `tests/test_provider_tokens.py` (NEW) | Unit tests for the shared helper (Step 5) |
| `tests/test_migrate_provider_tokens.py` (NEW) | Tests for the migration command (Step 7) |
| `tests/test_web_media_image.py` | NEW parallel `[tmdbid-…]`-format artwork-inheritance cases (Step 8) |
| `tests/test_enrich_metadata.py`, `tests/test_prep_push_rep_enrich.py`, `tests/test_prep_push_rep_season_enrich.py` | Update stamped-format assertions (Step 9) |
| `tests/test_web_datafns.py` | Update `suggest_target_folder` placeholder-format assertions (Step 9) |
| `tests/test_split_brace_escape.py` | ADD one new regression-pin test only; existing 2 tests untouched (Step 10) |
| `tests/smoke/test_smoke_all_commands.py` | New migration-command smoke case + verify existing enrich-stamp assertion (Step 11) |
| `ARCHITECTURE.md`, `README.md`, `docs/OPERATIONS_QA.md` | Documented-behavior updates (Step 13) |
| `improvements/improvements_tierU.md`, `improvements/PRIORITY.md`, `docs/priority-graph/priority-graph.html` | IMP-U6 registration (Step 14) |
| `docs/feature-token-brackets/PROGRESS.md`, `DECISIONS.md` | Resumability journal + locked decisions (Step 0, updated every step) |

## Approach

Fix detection FIRST (stop the active double-stamp/artwork-regression bleeding), then fix emission
(stop generating the old format), THEN build the migration command on top of both (so it emits and
verifies against the already-corrected vocabulary). This ordering is deliberate and load-bearing —
shipping emit-only first would double-stamp any legacy `{tmdb-…}` folder on the very next enrich run
before detection catches up; shipping the migration command before detection/emit are fixed would let
it "fix" folder names into a format the rest of the code still doesn't recognize as already-done.

A single shared regex/helper pair in `mvcommon.py` is both the detection contract every reader uses
AND the vocabulary source the migration command scans for — one definition, not a third or fourth
copy. The migration command is a thin, ancestor-aware, deepest-first driver on top of the existing,
already crash-safe `cmd_rename_folder` (IMP-D17) — it invents no new rollback mechanism, no new PONR,
no new journal record type. Every individual rename it performs is exactly as safe/resumable as a
manual `rename_folder` call already is; the migration command's OWN resumability is therefore free —
re-running it after an interruption just re-detects "already canonical" on everything already done and
continues from wherever it stopped.

---

## Research: "is it actually true that square brackets are the proper archival format?"

Answered with primary sources, not memory (planner has WebSearch/WebFetch; executors do not — baked in
here so no executor needs to browse).

| Server | Bracket | Tag spelling | Separator | Example | Source |
|---|---|---|---|---|---|
| **Plex** | **curly `{}`** | `tmdb` (no "id") | `-` | `Batman Begins (2005) {tmdb-272}` | [Plex Support — Naming and organizing your Movie files](https://support.plex.tv/articles/naming-and-organizing-your-movie-media-files/) |
| **Emby** | **square `[]`** | `tmdb` (TRaSH-documented) — but a staff-confirmed community example also shows `tmdbid` accepted | `-` (TRaSH) or `=` (a confirmed working community example) | `[tmdb-1520211]` (TRaSH); `[tmdbid=11454]` (Emby-staff-confirmed forum example) | [TRaSH-Guides Radarr naming scheme (raw source)](https://github.com/TRaSH-Guides/Guides/blob/master/docs/Radarr/Radarr-recommended-naming-scheme.md); [Emby community — "Metadata system not picking up \[tmdbid=XXXX\]"](https://emby.media/community/index.php?%2Ftopic%2F102371-metadata-system-not-picking-up-tmdbidxxxx-in-folder-name%2F=) |
| **Jellyfin** | **square `[]`** | `tmdbid` (with "id") | `-` | `Best_Movie_Ever (1994) [tmdbid-680]`, `[tvdbid-266189]` (shows only), `[imdbid-tt9362722]` | [Jellyfin official docs — Metadata Provider Identifiers](https://jellyfin.org/docs/general/server/metadata/identifiers/) |
| **TRaSH-Guides / Radarr / Sonarr** | ships **three separate presets**, one per server | — | — | `plex-tmdb` = `{tmdb-…}`; `emby-tmdb` = `[tmdb-…]`; `jellyfin-tmdb` = `[tmdbid-…]` | [TRaSH-Guides Radarr naming scheme (raw source)](https://github.com/TRaSH-Guides/Guides/blob/master/docs/Radarr/Radarr-recommended-naming-scheme.md) |

**Direct answer:** Partially true, and worth being precise about. Square brackets are the modern,
**Emby+Jellyfin** convention — not a universal "all three servers" standard. Plex's shipped agent
still specifically wants **curly** braces for its strict-match path; the *arr* ecosystem's own
official guide ships three DIFFERENT presets for exactly this reason — **there is no single string
that is simultaneously each server's own documented preference.** Moving from `{tmdb-}` to `[tmdbid-]`
does not make "all 3 work" in the literal sense of each getting its own preferred token; it trades
"works with Plex's strict id-match, not Emby/Jellyfin's" for "works with Emby's (with high but not
100%-primary-source-certain confidence) and Jellyfin's, not Plex's strict id-match." **This is the
one thing that most needs a decision from the user** — see Open Decisions #1 and #7 below (single
canonical token vs. stamping BOTH tokens in one folder name, which the Jellyfin docs explicitly say is
supported — "you can specify more than one identifier in a folder name" — and which would be the only
way to literally satisfy "all 3 should work fine").

**What is NOT at risk either way:** none of the three servers *requires* the bracket token to
function. It only forces an exact match instead of the default fuzzy title/year search. A folder with
no token, or with a token in a format the server doesn't recognize, still gets matched by title/year in
the common case — it just loses the "guaranteed correct match" safety net. So changing (or temporarily
lacking) the token format does not "break" a title in the sense of removing it from the library; it
only changes matching *confidence*. This matters for the risk framing below.

**Recommendation baked into this plan (default; confirm/override in Open Decisions):** canonical emit
format = **`[tmdbid-<id>]`**. Rationale: (a) it is Jellyfin's exact, officially documented format, and
Jellyfin is the user's own stated future target (`ROADMAP_END_GOAL.md`, `IMP-S1`); (b) the real library
is *already* 1396/~1598 folders into this exact format via the other tool's migration — reverting would
be pure churn with no upside; (c) it is very likely (community-confirmed, not 100% primary-source
pinned) also accepted by Emby's real-world parser, which appears more permissive than TRaSH's
conservative documented preset; (d) Plex is not "broken" by its absence — title/year fuzzy match still
works, it only loses forced-id matching, which the user can still get per-title via a manual
`{tmdb-…}`-suffixed rename if ever needed for one stubborn mismatch.

Sources used above (also cited inline): [Plex naming docs](https://support.plex.tv/articles/naming-and-organizing-your-movie-media-files/) · [Jellyfin provider-identifier docs](https://jellyfin.org/docs/general/server/metadata/identifiers/) · [TRaSH-Guides Radarr naming scheme](https://github.com/TRaSH-Guides/Guides/blob/master/docs/Radarr/Radarr-recommended-naming-scheme.md) · [Emby community thread on `[tmdbid=XXXX]`](https://emby.media/community/index.php?%2Ftopic%2F102371-metadata-system-not-picking-up-tmdbidxxxx-in-folder-name%2F=).

---

## Shared detection/parsing contract (baked in for Step 1's candidates — behavior is locked, implementation strategy is not)

Both candidates in Step 1 MUST deliver exactly this external behavior and these exact symbol names in
`mvcommon.py` (the internal regex/parsing strategy is what differs between candidates):

```python
# mvcommon.py — new symbols
CANONICAL_TMDB_TOKEN_FMT = "[tmdbid-{id}]"   # str.format(id=...) -> "[tmdbid-603692]"
CANONICAL_TVDB_TOKEN_FMT = "[tvdbid-{id}]"   # placeholder-only use (suggest_target_folder); never a real lookup

def has_tmdb_token(name: str) -> bool:
    """True if `name` already carries a TMDB provider token in ANY recognized format
    ({tmdb-…}, [tmdb-…], [tmdbid-…}), case-insensitive. Replaces main._has_tmdb_token's body."""

def find_provider_tokens(name: str) -> list[dict]:
    """Every recognized provider token found in `name` (there can be more than one —
    e.g. a folder carrying both a source-provided [tvdbid-…] AND a MediaVault [tmdbid-…]).
    Each item: {"provider": "tmdb"|"tvdb"|"imdb", "id": "<the id substring>",
                "bracket": "curly"|"square", "match": "<the exact matched substring>",
                "span": (start, end)}  # span into `name`, for the migration command's replace
    """
```

Recognized vocabulary (bake in — no executor should invent or narrow this):
- **Tag names** (case-insensitive): `tmdb`, `tmdbid`, `tvdb`, `tvdbid`, `imdb`, `imdbid`.
- **Bracket pairs, never cross-matched**: `{...}` (curly) and `[...]` (square). `"{tmdb-123]"` and
  `"[tmdb-123}"` (mismatched open/close) must NOT match — this is an explicit, required test case
  (a naive single-character-class-for-both-brackets regex gets this wrong; see Step 1 Acceptance).
- **Separator**: `-` is canonical (the only one MediaVault ever emits). `=` MUST also be tolerated on
  the square-bracket family only, for DETECTION only (the Emby community-confirmed `[tmdbid=XXXX]`
  variant) — never emitted, never required for curly braces.
- **Must reject a bare bracketed non-token**: `[rartv]`, `[FraMeSToR]`, `[abc123]` (a chunk/short_id
  tag, e.g. `movie [a1b2c3].chunk.001.mkv`) must never match — anchored to the tag vocabulary above,
  never a bare `\[.+?\]` pattern (this is exactly the real-world collision block 3d/the audit warned
  about: `Peaky.Blinders.S06…-FLUX[rartv] [tmdbid-60574]` is real, live data).
- **Case-insensitive** throughout (IMP-C23 precedent: `Run (2002) {TMDB-69590}` is a real folder in the
  user's library and MUST be recognized as already-tokened).

`main.py`'s `_has_tmdb_token` becomes a one-line wrapper (`return mvcommon.has_tmdb_token(name)`), and
`_PROVIDER_TOKEN_RE`'s single call site (`_ancestor_show_folder_image`, main.py:9665) calls
`mvcommon.has_tmdb_token` directly — the module-level `_PROVIDER_TOKEN_RE` constant is deleted (no
second copy left anywhere to drift). This mirrors the IMP-C18 `episode_num_from_id` precedent exactly:
one shared implementation in `mvcommon.py`, thin call sites in `main.py`.

---

## Steps

- [x] 0. [model: sonnet] [effort: low] Scaffold `docs/feature-token-brackets/PROGRESS.md` + `DECISIONS.md`.
  - Files: `docs/feature-token-brackets/PROGRESS.md` (NEW), `docs/feature-token-brackets/DECISIONS.md` (NEW)
  - Depends on: branch created (orchestrator Phase 1).
  - Consumed by: EVERY later step (updates PROGRESS.md's step table + `▶ NEXT ACTION` in the SAME
    commit as its own work — this is the standing convention for the rest of this plan, not repeated
    per step below).
  - Details: Create `PROGRESS.md` using the exact structure of `docs/feature-extras/PROGRESS.md`
    (IMP-D19) as the pattern source: a one-paragraph task/framework/branch header, a `▶ NEXT ACTION`
    pointer, a "Resume protocol" numbered list (git fetch/checkout the branch, read PLAN+DECISIONS+this
    file, reconcile `git log` against the step table, resume at the first non-`done` step, commit
    PLAN-tick + PROGRESS-update together), and a Step-status table with ALL 15 rows (0–14) pre-listed
    as `pending` with their `[model:]` tag and one-line description (copy from this PLAN.md's Steps
    section). Include a "Blockers / human gates" section listing: Checkpoint 🚦 after Step 1's judge
    verdict (user picks the detection-helper candidate), Checkpoint 🚦 after Step 6's judge verdict
    (user picks the migration-command candidate), Checkpoint 1 (PR merge), Checkpoint 2 (branch
    archive). Create `DECISIONS.md` pre-filled with this plan's "Open Decisions" section content,
    EACH one explicitly marked `DRAFT — planner recommendation, not yet user-confirmed` (they become
    locked only when the user actually rules on them, e.g. at the PR checkpoint) — mirrors
    `docs/feature-extras/DECISIONS.md`'s Card format (one `##` heading per decision, the chosen/
    recommended option in **bold**, one-paragraph rationale).
  - Acceptance: both files exist, committed; PROGRESS.md's step table has exactly 15 rows (0–14)
    matching this plan's step numbers/models; `▶ NEXT ACTION` points at Step 1.

- [ ] 1. [model: fable] [fallback: opus] [effort: xhigh] [candidates: 2] 🚦 Design + implement the shared provider-token detect/parse helper in `mvcommon.py`.
  - Files: `mvcommon.py`
  - Depends on: Step 0.
  - Consumed by: Step 2 (main.py detection wiring — calls `mvcommon.has_tmdb_token`), Step 3 (emit
    sites — uses `mvcommon.CANONICAL_TMDB_TOKEN_FMT`/`CANONICAL_TVDB_TOKEN_FMT`), Step 5 (unit tests),
    Step 6 (migration command — uses `mvcommon.find_provider_tokens` to locate what to rewrite).
    **Convention this step MUST establish and log in STATUS.md for those consumers:** the exact 3
    function/constant names and the `find_provider_tokens` return-dict shape given in the "Shared
    detection/parsing contract" section above — do not rename or reshape them; later steps are written
    against those exact names.
  - Details: Implement `has_tmdb_token`, `find_provider_tokens`, `CANONICAL_TMDB_TOKEN_FMT`,
    `CANONICAL_TVDB_TOKEN_FMT` per the locked contract above. Both candidates must satisfy the SAME
    external behavior/vocabulary; they differ in HOW they parse. Place the implementation in
    `mvcommon.py` (not `main.py`) — this is a locked architectural requirement (IMP-C18/A1 precedent:
    `mvcommon.py` is the established home for exactly this "shared parser both entry points would
    otherwise duplicate" class of helper), not a candidate-differentiator.
  - Acceptance: for every one of these inputs, both candidates must produce the documented result —
    (a) `{tmdb-603692}` → tmdb token found; (b) `[tmdb-603692]` → tmdb token found; (c)
    `[tmdbid-603692]` → tmdb token found; (d) `{TMDB-69590}` (IMP-C23's real uppercase folder) → found;
    (e) `[tmdbid=603692]` (Emby `=` variant) → found; (f) `Peaky.Blinders.S06…-FLUX[rartv]
    [tmdbid-60574]` → exactly ONE token found (the tmdbid one; `[rartv]` must NOT match); (g)
    `{tmdb-123]` and `[tmdb-123}` (mismatched brackets) → NOT found; (h) a folder with both
    `[tvdbid-266189]` and `[tmdbid-70523]` → `find_provider_tokens` returns BOTH, `has_tmdb_token` is
    True; (i) a folder with only `[tvdbid-266189]` → `has_tmdb_token` is False (tvdb is not tmdb — the
    idempotency guard must stay TMDB-specific so a source-provided tvdb token never blocks MediaVault's
    OWN tmdb stamp). `re.IGNORECASE` (or equivalent) throughout.
  - Judge criteria (ranked): (1) correctness against every Acceptance case above, zero false
    positive/negative — non-negotiable; (2) whether the design makes a FUTURE drift between two
    call-sites structurally impossible (one shared function everywhere) vs merely tested-for-agreement
    — the whole point of this step is closing the IMP-C18/C22/C23 drift-class permanently; (3)
    readability/extensibility if a 4th provider (e.g. `anidb`) is ever added; (4) total lines touched.
  - Candidate approaches:
    - A: **Single unified compiled regex** — one `re.compile` pattern using named groups over
      alternation between the curly-brace branch and the square-bracket branch (kept as two full
      alternatives, NOT a shared open/close character class, to avoid the mismatched-bracket
      false-positive trap called out in Acceptance case (g)); `find_provider_tokens` uses
      `finditer` + a small dispatch on which alternative matched. `[candidate-model: fable]` — this is
      the trickier, regex-density-heavy approach where a subtly wrong pattern is the exact failure
      mode this task exists to eliminate, so it earns the highest-care tier.
    - B: **Two-stage pipeline** — a first pass finds candidate bracketed spans with two SIMPLE,
      independent regexes (one for `{...}`, one for `[...]`, each just "some non-bracket characters
      inside," no vocabulary awareness yet), then a second, vocabulary-only function
      (`_parse_token_content(content) -> (provider, tag, id) | None`) checks whether a given span's
      inner text starts with a recognized tag + separator, independent of which bracket family
      produced it. `find_provider_tokens` composes stage 1 → stage 2. `[candidate-model: opus]` — a
      more mechanical decomposition; the correctness risk is lower (each stage is individually
      trivial to reason about) at the cost of two functions instead of one regex.
  - 🚦 **User-gated checkpoint**: both candidates are built in isolated worktrees per the orchestrator's
    standard multi-candidate flow, judged by `judge-v2`, and the orchestrator STOPS to relay the full
    judged comparison to the user — the user picks the winner (this is the single highest-blast-radius
    decision in the whole plan: every later token-aware code path is built on top of it). Approach
    diversity (A = unified regex, B = decompose-then-validate) is what differs and survives a model
    fallback unchanged; only the executor's model changes if fable is unavailable.

- [ ] 2. [model: opus] [effort: high] Wire the shared helper into every detection/read call site in `main.py`.
  - Files: `main.py`
  - Depends on: Step 1 (the merged, user-chosen winner).
  - Consumed by: Step 5 (tests exercise these wrappers too), Step 8 (artwork-inheritance tests exercise
    `_ancestor_show_folder_image`), Step 6 (the migration command's "already canonical, skip" check
    reuses the same detection path indirectly via `mvcommon.has_tmdb_token`).
  - Details: (a) `_has_tmdb_token` (main.py:1687-1698) — replace its body with
    `return mvcommon.has_tmdb_token(name)`; keep the function name and docstring (update the docstring
    to say "any recognized format", not just `{tmdb-…}"). (b) Delete the module-level
    `_PROVIDER_TOKEN_RE = re.compile(...)` constant (main.py:9589) and its one call site inside
    `_ancestor_show_folder_image` (main.py:9665, currently `_PROVIDER_TOKEN_RE.search(name or "")`) —
    replace with `mvcommon.has_tmdb_token(name or "")`. Do not touch anything else in
    `_ancestor_show_folder_image`/`resolve_artwork_path` — the walk-up LOGIC is correct today; only the
    token-recognition predicate it calls was wrong.
  - Acceptance: `grep -n "_PROVIDER_TOKEN_RE" main.py` returns zero hits after this step. Both call
    sites now recognize all formats from Step 1's Acceptance list. `pytest tests/test_rename_folder.py
    tests/test_set_tmdb.py -q` still green (these exercise adjacent code paths and must show zero
    incidental regression).

- [ ] 3. [model: opus] [effort: high] Update every EMIT site to the canonical `[tmdbid-…]` / `[tvdbid-…]` format.
  - Files: `main.py`
  - Depends on: Step 2 (detection must already be format-agnostic before emission changes, or a
    legacy `{tmdb-…}` folder would get double-stamped on the very next run — see "Approach" above).
  - Consumed by: Step 9 (test assertion updates), Step 11 (smoke assertion), Step 13 (docs examples).
  - Details: **Two emit sites must change together (the STANDING SYNC OBLIGATION documented in
    ARCHITECTURE.md §6.3a — `_enrich_after_archive` is a deliberate, self-contained duplicate of
    `cmd_enrich_metadata`'s stamping logic; changing one without the other reintroduces exactly the
    drift this whole task fixes)**:
    (a) `cmd_enrich_metadata` (main.py:2633-2696): line 2689
    `new_name = f"{base_name} {{tmdb-{tmdb_id}}}"` → build from
    `mvcommon.CANONICAL_TMDB_TOKEN_FMT.format(id=tmdb_id)`; update the "would stamp"/"stamping" print
    at lines 2637-2638 and the "already has a token" print at line 2640 to show the new format string
    (still print the OLD folder name unchanged — only the NEW-name half of the message changes).
    (b) `_enrich_after_archive` (main.py:7724-7740): identical change at line 7730 (same
    `new_name = f"{base_name} {{tmdb-{tmdb_id}}}"` pattern) and the matching prints at 7729/7739-7740.
    (c) `suggest_target_folder` (main.py:8318-8375): lines 8362-8367,
    `provider_tag = "{tmdb-0000000}"` → `mvcommon.CANONICAL_TMDB_TOKEN_FMT.format(id="0000000")`
    (i.e. `"[tmdbid-0000000]"`), and `provider_tag = "{tvdb-000000}"` →
    `mvcommon.CANONICAL_TVDB_TOKEN_FMT.format(id="000000")` (i.e. `"[tvdbid-000000]"`). This is the
    placeholder shown for a brand-new unprepped item in the reclaim UI's folder suggestion — do NOT
    change anything else about this function (it stays a pure suggestion with no lookup, exactly as
    documented in ARCHITECTURE §6.3a's "one apparent contradiction — it is not one" callout; only the
    bracket punctuation changes). This is also the direct, explicit fix for the user's stated future
    requirement: "if I give the Id in prep and ask it to rename the folder also it should put it inside
    []" — this placeholder is exactly what the web UI shows the user to edit before that rename.
  - Acceptance: `grep -n '{{tmdb-\|{tmdb-0000000}\|{tvdb-000000}' main.py` returns zero hits in these
    three functions after this step. A fresh `cmd_enrich_metadata --apply` (fixture-driven, in Step 9's
    tests) on an entry with no existing token stamps `[tmdbid-<id>]`, never `{tmdb-<id>}`.

- [ ] 4. [model: sonnet] [effort: low] Mechanical doc-string / help-text / comment updates in `main.py`.
  - Files: `main.py`
  - Depends on: Steps 2, 3 (so the comments describe the already-changed code truthfully).
  - Consumed by: Step 13 (README/ARCHITECTURE examples should match the CLI's own help text).
  - Details: find-and-replace `{tmdb-12345}` → `[tmdbid-12345]` (and matching `{tmdb-…}` → `[tmdbid-…]`
    ellipsis form) in every PURE COMMENT/DOCSTRING/PRINT-LITERAL occurrence — this is display/help text
    only, not logic, so it is mechanical: `main.py:373` (comment), `:1291` (comment), `:1816`
    (docstring), `:2024` (comment), `:3623`/`:3672` (cmd_rename_folder docstring examples — these
    describe a CALLER'S example new-name, not a requirement; update the example only), `:7560`
    (comment), `:8327` (docstring — also swap `{tvdb-…}` → `[tvdbid-…]` here), `:8814`/`:8824`
    (resolve_artwork_path-area comments), `:9585`/`:9652`/`:9702`/`:9777` (the
    `_PROVIDER_TOKEN_RE`-area comments — reword to describe the NEW shared-helper mechanism, not the
    now-deleted regex constant), `:9954` (the `rename_folder` CLI help line), `:10540`/`:10544` (the
    `rename_folder` usage comment + error message). Do not change any code logic in this step — string
    literals and comments only.
  - Acceptance: `grep -c '{tmdb-12345}\|{tmdb-…}\|{tvdb-…}\|{tvdb-000000}' main.py` returns 0.
    `python main.py` (bare, no args — prints usage) shows `[tmdbid-12345]` in the `rename_folder` help
    line.

- [ ] 5. [model: sonnet] [effort: medium] New unit tests for the shared detection helper.
  - Files: `tests/test_provider_tokens.py` (NEW)
  - Depends on: Step 2 (tests both `mvcommon`'s functions directly AND `main._has_tmdb_token`'s thin
    wrapper, in one file, so a future regression in the wrapper wiring is caught here too).
  - Consumed by: Step 12 (full-suite run), the smoke gate indirectly (nothing here IS smoke, but a
    break here blocks Step 12).
  - Details: one test per Step 1 Acceptance-list case (a)–(i), plus: a drift-pin test asserting
    `main._has_tmdb_token` and `mvcommon.has_tmdb_token` return IDENTICAL results across the whole
    Acceptance-list input set (mirrors the exact drift-pin pattern IMP-C23 added for the
    `_PROVIDER_TOKEN_RE` vs `_has_tmdb_token` pair — same idea, now enforced structurally AND
    tested). No fixtures/sandbox needed — these are pure string-in/bool-or-list-out unit tests, no
    library or filesystem I/O. Constraints: "Never touch real C:\\Media files or real library_*.json."
    (moot here — pure functions — state it anyway per the standing rule). Run `pytest -q` and fix
    failures before marking the step done.
  - Acceptance: `pytest tests/test_provider_tokens.py -q` green; every Step 1 Acceptance case has a
    corresponding named test.

- [ ] 6. [model: fable] [fallback: opus] [effort: xhigh] [candidates: 2] 🚦 Design + implement `cmd_migrate_provider_tokens` — the real migration command.
  - Files: `main.py` (new function + CLI dispatch wiring)
  - Depends on: Steps 2 + 3 (needs the corrected detection AND canonical emit format to migrate
    TOWARD and verify AGAINST).
  - Consumed by: Step 7 (tests), Step 11 (smoke case), the "Real-library migration procedure" section
    at the end of this plan (the actual command the user runs post-merge — name and flag shape are
    LOCKED by this step, do not rename later).
    **Convention this step MUST establish and log in STATUS.md:** the exact CLI surface (below), the
    exact JSON report shape (below), and the exact ordering guarantee (deepest-first) — Step 7's tests
    and the end-of-plan user-facing commands are written against these.
  - Details: CLI surface, mirroring the existing `enrich_metadata`/`refresh_online` convention exactly
    (dry-run-by-default, `--apply` opts in — do not invent a different UX):
    ```
    python main.py migrate_provider_tokens [id_or_prefix] [--apply] [--library movies|series|anime|others]
    ```
    - No `id_or_prefix` = whole library (all four `library_*.json`, or the one named by `--library`).
    - No `--apply` = **dry-run** (default): print, for every folder that would be renamed, `OLD ->
      NEW`, plus a summary (`scanned N, would-rename M, already-canonical K, remote-bearing R`). Writes
      NOTHING — no library mutation, no disk mutation, no report file (nothing changed, nothing to
      persist).
    - `--apply` = execute for real. Each rename goes through the EXISTING `cmd_rename_folder(old, new)`
      — do not reimplement any part of its journaling/PONR/self-heal; call it exactly as
      `rename_folder` already does. After the run, write a JSON report to
      `C:\Media\migration_reports\token_format_<UTC ISO 8601 timestamp>.json` (auto-`makedirs` the
      folder if absent — mirrors the existing `library_backups/` sibling-folder convention) with:
      `{"scanned": N, "renamed": [{"id_or_note": "...", "old_folder": "...", "new_folder": "...",
      "remote_bearing": bool}], "already_canonical": K, "errors": [...]}`. `"remote_bearing"` is true
      when the entry (or any of its descendants) has `uploaded` truthy or `status` in
      `("onboarded", "archived", "restored_local")` — this is the block-3g exposure-quantification
      deliverable: it does NOT attempt to fix the phone-side path (see Open Decision #3), it records
      which renames touched an already-pushed entry so the mapping is never lost.
    - **Ancestor-aware discovery** (fixes the gap the other tool's migration left, block 3b/"Friends"):
      for every PHYSICAL entry in scope (`leaf` + `season_map`, `multi_ep_alias` skipped — the standard
      alias-safety rule), walk from its `folder_path` UP the directory tree to (not past) `LOCAL_ROOT`,
      collecting every ancestor directory in the chain (reuse the SAME walk-up idiom
      `_ancestor_show_folder_image` already uses, or a small variant of it — do not invent a
      third climbing implementation). For each COLLECTED directory (own folder + every ancestor,
      deduplicated across all entries — the same show folder is an ancestor of many episodes, dedupe
      it to ONE rename), test its OWN basename via `mvcommon.find_provider_tokens`: if it carries an
      OLD-style TMDB token (curly `{tmdb-…}`, or square-no-id `[tmdb-…]`) but not yet the canonical
      `[tmdbid-…]` form, it is a migration candidate. A directory carrying ONLY a `[tvdbid-…]`/
      `[imdbid-…]` token and no old-style tmdb token is NOT a candidate (nothing to migrate — see Open
      Decision #5) but its coexisting tvdb/imdb token must be PRESERVED verbatim in the new name (only
      the tmdb portion of the string is replaced — use `find_provider_tokens`'s `span` to replace just
      that substring, never a blind whole-name rebuild).
    - **Deepest-first ordering** (REQUIRED — not an optimization): sort the deduplicated candidate list
      by path depth (`len(_norm_path(folder).split(os.sep))`), DESCENDING, before renaming anything.
      Rationale (bake this into STATUS.md so a reviewer doesn't need to re-derive it): renaming an
      ancestor before a not-yet-processed descendant would move the descendant out from under any
      RAW-PATH resolution computed before the ancestor moved; processing leaf-most folders first
      guarantees every folder is renamed while its own path is still the one on disk, and
      `cmd_rename_folder`'s own cascade (it already rewrites `folder_path` for every descendant when an
      ancestor moves) correctly re-points already-renamed descendants under the new ancestor prefix
      afterward — the two mechanisms compose cleanly only in this order.
    - **Idempotent + resumable "for free"**: do not add any new journal/state file for the migration
      command itself. Each individual `cmd_rename_folder` call is already crash-safe (IMP-D17); a
      migration run interrupted after folder N of M leaves N renamed and M-N not yet — a RE-RUN simply
      re-detects the N already-renamed folders as "already canonical" (via the same
      `mvcommon.has_tmdb_token`/`find_provider_tokens` check) and skips them, continuing from N+1. This
      is the entire resumability story — do not build anything more elaborate.
  - Acceptance: dry-run mode never calls `cmd_rename_folder` and never writes a report file (verify via
    a monkeypatch spy in tests). `--apply` on a fixture reproducing the Friends shape (ancestor +
    already-migrated leaf) renames ONLY the ancestor, leaves the leaf's own name untouched, and the
    leaf's `folder_path` in JSON correctly reflects the new ancestor prefix afterward (via
    `cmd_rename_folder`'s existing cascade). A folder carrying `[rartv]` alongside an old-style tmdb
    token migrates ONLY the tmdb portion, leaving `[rartv]` byte-identical. Re-running `--apply` twice
    in a row on the same fixture is a no-op the second time (0 renames, 0 errors).
  - Judge criteria (ranked): (1) correctness on the ancestor case — proven by a fixture reproducing the
    exact Friends shape (parent token stale, child already migrated); (2) idempotency/resumability —
    proven by simulating an interruption partway through and re-running; (3) never touches a folder
    that doesn't need it (no false-positive on `[rartv]`, no mutation of an orphan folder the library
    doesn't reference, unless the candidate explicitly opts into disk-driven discovery — see B below,
    in which case orphan-reporting-without-mutating must be proven safe); (4) practicality on the real
    ~1600-entry / ~200-remaining-folder library — a full disk walk is slower but self-auditing; state
    the tradeoff for the judge rather than assuming a winner.
  - Candidate approaches:
    - A: **Library-entry-driven, per-entry ancestor walk-up** (the design detailed in "Details" above)
      — the candidate set is derived ENTIRELY from what the library already references (every physical
      entry's folder_path + its ancestors up to LOCAL_ROOT), deduplicated. Precise, minimal, cannot
      touch a folder the library doesn't know about. `[candidate-model: fable]` — the
      ancestor-dedup + ordering + span-preserving-rewrite logic is the highest-stakes part of this
      whole plan (it runs against the real library), earning the highest-care tier.
    - B: **Category-root disk walk with library cross-reference** — walk `CATEGORY_ROOTS` top-down
      across the whole on-disk media tree (reusing the SAME walking idiom `cmd_scan_unprepped`/
      `cmd_recover --scan` already use — do not invent a new one), testing every directory's basename
      against `find_provider_tokens` as it goes; for each match, cross-reference against the library
      (is this folder, or is it an ancestor of, at least one entry's `folder_path`?) to decide
      migrate-vs-report-as-orphan. Slower (walks the entire tree even when only ~200 entries need
      migration) but self-auditing — it can ALSO surface a stray, unreferenced folder carrying an old
      token that the library-driven approach would never see (report-only, never renamed without an
      explicit opt-in flag). `[candidate-model: opus]` — a more mechanical adaptation of an existing,
      proven walking pattern.
  - 🚦 **User-gated checkpoint**: same flow as Step 1 — isolated worktrees, `judge-v2`, orchestrator
    STOPS and relays the full comparison, user picks the winner. This is the step that will actually
    touch the user's real folders once run with `--apply` post-merge, so it gets the same human gate as
    the detection-helper step. Approach diversity (A = targeted per-entry walk, B = full disk audit)
    is what differs and survives a model fallback unchanged.

- [ ] 7. [model: opus] [effort: high] Tests for the migration command.
  - Files: `tests/test_migrate_provider_tokens.py` (NEW)
  - Depends on: Step 6 (the merged, user-chosen winner).
  - Consumed by: Step 12 (full-suite gate).
  - Details: fixtures built with `sandbox`/`sandbox_alias` (never real `C:\Media`/`library_*.json` —
    standing rule, restate per docs/testing-strategy.md). Cases, each a separate test:
    (1) leaf-only rename (own folder_path carries the old token, no ancestor issue) — renamed,
    `folder_path` updated, hash/status/uploaded untouched (mirrors `test_rename_folder.py`'s existing
    hash-safety assertions — reuse that pattern, don't reinvent it). (2) ancestor-only rename — build a
    fixture reproducing the EXACT Friends shape: a season_map's `folder_path` already carries
    `[tmdbid-…]` (already migrated) but its PARENT directory on disk still carries `{tmdb-…}` (not
    referenced directly by any entry's folder_path) — assert only the ancestor is renamed and the
    season_map's `folder_path` correctly reflects the moved ancestor afterward. (3) idempotent re-run —
    run `--apply` twice, assert the second run renames 0 / reports 0 errors. (4) dry-run makes ZERO
    filesystem or library changes (monkeypatch-spy `cmd_rename_folder` and assert it is never called in
    dry-run mode). (5) mixed-format library — some entries already `[tmdbid-…]`, some still
    `{tmdb-…}`, some carrying a coexisting `[tvdbid-…]` — assert only the non-canonical tmdb ones move,
    and a coexisting tvdb token in a renamed folder's new name is preserved byte-identical. (6) a
    `[rartv]`-adjacent folder (mirrors the real `Peaky.Blinders…FLUX[rartv] [tmdbid-…]` shape) is
    migrated (if it has an old-style tmdb token) WITHOUT ever touching the `[rartv]` substring — assert
    on the exact resulting string. (7) the `--apply` JSON report file is written with the documented
    shape and correctly flags `remote_bearing: true` for a fixture entry whose `status="archived"`/
    `uploaded=True`. Run `pytest -q` and fix failures before marking the step done.
  - Acceptance: `pytest tests/test_migrate_provider_tokens.py -q` green, all 7 cases present and named
    per the list above.

- [ ] 8. [model: opus] [effort: high] Artwork-inheritance regression coverage across all three formats.
  - Files: `tests/test_web_media_image.py`
  - Depends on: Step 2.
  - Consumed by: Step 12.
  - Details: this is the direct proof-of-fix for the live regression this plan found (block: the
    ancestor-walk artwork fallback currently matches almost nothing against the real, already-migrated
    library). Add NEW test cases PARALLEL to the existing `{tmdb-…}`-folder cases in this file — do not
    edit the existing ones (they still pass unchanged post-fix, since braces remain a recognized
    format; editing them would be an unrelated, unasked-for change). For at least the two existing
    scenarios that exercise the ancestor walk-up (the `Dark {tmdb-70523}` show-folder case and the
    fanart-inheritance case around line ~509-608), add a byte-identical sibling case using a
    `[tmdbid-…]`-named show folder instead of `{tmdb-…}`, asserting the SAME resolved artwork path
    behavior. Constraints: `sandbox`/fixtures only, never real `C:\Media`.
  - Acceptance: `pytest tests/test_web_media_image.py -q` green; at least 2 new
    `[tmdbid-…]`-format parallel cases exist alongside the untouched original `{tmdb-…}` cases.

- [ ] 9. [model: sonnet] [effort: medium] Update existing test assertions that hardcode the OLD emitted format.
  - Files: `tests/test_enrich_metadata.py`, `tests/test_prep_push_rep_enrich.py`, `tests/test_prep_push_rep_season_enrich.py`, `tests/test_web_datafns.py`
  - Depends on: Step 3.
  - Consumed by: Step 12.
  - Details: these files assert on the LITERAL stamped/placeholder string, so they must change WITH the
    emit-site behavior (not because they're broken, but because the code they test now correctly does
    something different). `test_enrich_metadata.py` (38 occurrences) and the two
    `test_prep_push_rep*_enrich.py` files: every assertion of the shape `"{tmdb-" in ...` /
    `f"{base} {{tmdb-{id}}}"` → `"[tmdbid-" in ...` / `mvcommon.CANONICAL_TMDB_TOKEN_FMT.format(...)`.
    `test_web_datafns.py` lines 162/170/178: `assert "{tmdb-" in result["folder"]` /
    `assert "{tvdb-" in result["folder"]` → `"[tmdbid-"` / `"[tvdbid-"`. Line 184's fixture
    (`folder_path` already `[tmdb-12345]`, an EXISTING-entry/`applies: False` branch that is never
    renamed) may be left as-is or updated to `[tmdbid-12345]` for realism — either is acceptable since
    that branch doesn't parse the string, it only echoes it back. Do NOT touch anything in these files
    beyond the literal format-string assertions — no other test logic changes.
  - Acceptance: `pytest tests/test_enrich_metadata.py tests/test_prep_push_rep_enrich.py tests/test_prep_push_rep_season_enrich.py tests/test_web_datafns.py -q` green with zero `{tmdb-`/`{tvdb-` assertions remaining that expect them to be produced BY MediaVault's own code (assertions about a pre-existing/seeded fixture folder that merely HAPPENS to use braces to prove "any recognized format still works" are fine and should stay — this step only fixes assertions about what MediaVault ITSELF now emits).

- [ ] 10. [model: sonnet] [effort: low] mkvmerge brace-escape regression pin (no code change).
  - Files: `tests/test_split_brace_escape.py`
  - Depends on: Step 3 (conceptually — sequenced here for narrative order; no actual code dependency).
  - Consumed by: Step 12.
  - Details: **`split_video_file`'s brace-doubling escape needs NO code change** — it targets literal
    `{`/`}` characters only (verified: the existing `test_split_no_braces_path_is_unchanged` test
    already proves a brace-free path passes through byte-identically, with zero `{{`/`}}` in the `-o`
    argument). Square brackets are not special to mkvmerge's libfmt output-name parser and trigger no
    escaping. Do not touch `split_video_file` or either of the 2 existing tests in this file. ADD ONE
    new test, `test_split_square_bracket_path_is_unchanged` (mirrors the existing
    `test_split_no_braces_path_is_unchanged` structure exactly, same fixture shape), using a
    `"3 (2012) [tmdbid-79660]"` folder name in place of the brace-folder fixture, asserting `"[["` /
    `"]]"` never appear in the captured `-o` argument and the literal `[tmdbid-79660]` substring passes
    through unchanged. This permanently pins the "square brackets need no escape" finding as a
    regression guard, per the task's explicit ask to resolve this tension explicitly rather than leave
    it implicit.
  - Acceptance: `pytest tests/test_split_brace_escape.py -q` shows 3 passed (the original 2 unchanged +
    the 1 new one).

- [ ] 11. [model: sonnet] [effort: medium] Smoke-suite coverage.
  - Files: `tests/smoke/test_smoke_all_commands.py`
  - Depends on: Steps 3, 6.
  - Consumed by: Step 12 (this IS part of the mandatory smoke gate).
  - Details: (a) locate the existing `enrich_metadata`/stamping-related smoke case(s) (grep the file for
    `{tmdb-` or `will_stamp` context) and update any literal-format assertion the same way Step 9 did.
    (b) add ONE new smoke case exercising `migrate_provider_tokens` end-to-end on the smoke library's
    tiny fixture: seed one entry with an old-style `{tmdb-…}` folder name, run the command in dry-run
    mode (assert it reports 1 candidate, changes nothing), then `--apply` (assert the rename happened,
    `folder_path` updated, entry otherwise untouched), then re-run `--apply` once more (assert 0
    additional renames — the idempotency case, cheap to prove here too). Keep it FAST — this suite must
    stay under 30s; do not add a large fixture or a real mkvmerge/ffmpeg dependency.
  - Acceptance: `pytest tests/smoke -q` green, completes in well under 30s, includes the new
    `migrate_provider_tokens` case.

- [ ] 12. [model: opus] [effort: high] Full verification pass — run the complete suite and fix any fallout.
  - Files: none (verification + targeted fixes only; if a fix is needed, it must be traceable to one of
    the specific files touched above — no new scope).
  - Depends on: Steps 0-11 (everything).
  - Consumed by: Step 13/14 (docs/registration should describe a GREEN, finished state), the PR.
  - Details: run `python -m pytest tests -q` (the FULL suite — remember there is no `testpaths`
    configured, so a bare `pytest -q` from the repo root collects nothing; must invoke as
    `python -m pytest tests -q` or `pytest tests -q` from repo root) and `pytest tests/smoke -q`.
    Investigate and fix any failure or unexpected count delta before proceeding — do not mark this step
    done with a red suite. If a failure reveals a genuine gap in an EARLIER step's Acceptance criteria
    (not scope creep), fix it in that step's own files and note the correction in STATUS.md/PROGRESS.md
    rather than papering over it here.
  - Acceptance: full suite green (record the exact pass count in PROGRESS.md, matching the
    `docs/feature-extras/PROGRESS.md` convention of citing e.g. "full 648, smoke 76"); smoke green,
    under 30s.

- [ ] 13. [model: sonnet] [effort: medium] Documentation updates.
  - Files: `ARCHITECTURE.md`, `README.md`, `docs/OPERATIONS_QA.md`
  - Depends on: Steps 3, 4, 6 (describes the final, implemented behavior).
  - Consumed by: nothing downstream in this plan — this is a leaf/terminal documentation step, but its
    OUTPUT is what a future session/human reads to understand the feature, so accuracy matters as much
    as any code step.
  - Details: **ARCHITECTURE.md** §6.3a — replace every `{tmdb-…}`/`{tmdb-<id>}` reference in the
    "TMDB enrichment conventions" section with `[tmdbid-…]`/`[tmdbid-<id>]` (the folder-token paragraph,
    the "one apparent contradiction" callout's `{tvdb-000000}` placeholder mention → `[tvdbid-000000]`,
    the `rename_folder` + rollback-contract cross-ref paragraph — ADD one explicit sentence there
    confirming this task changed NEITHER the journal format NOR any PONR, only the string
    `cmd_rename_folder` is CALLED WITH). Add a short new paragraph documenting
    `migrate_provider_tokens` (what it does, dry-run default, where it lives) — place it directly after
    the `rename_folder` paragraph it builds on. **README.md** — every `{tmdb-12345}`/`{tmdb-27205}`/
    `{tmdb-397243}`/`{tmdb-66732}`/`{tmdb-13916}`/`{tmdb-70523}` example (lines ~384-563, the
    `enrich_metadata`/`prep_push_rep_enrich`/`add_extras`/`prep_season` example command lines and the
    "TMDB-for-everything" paragraph's `{tmdb-…}` mention) → `[tmdbid-…]` form; add
    `migrate_provider_tokens [id_or_prefix] [--apply] [--library X]` to the command reference table
    near `rename_folder`'s row. **docs/OPERATIONS_QA.md** — add a new Q&A entry (append to §1
    "Metadata enrichment," matching the file's existing question/answer format exactly — see the
    `enrich_metadata`/`prep_push_rep_season_enrich` entries already there for the tone/structure):
    "Why did my folder suddenly get a second, bracket-style token?" / "What format does MediaVault use
    for provider ids now, and do I need to do anything?" — cite IMP-U6, name `migrate_provider_tokens`,
    and note the artwork-inheritance regression this also fixed. Bump the file's "Last updated" date.
  - Acceptance: `grep -rn '{tmdb-\|{tvdb-' ARCHITECTURE.md README.md` returns zero hits describing
    MediaVault's OWN behavior (a historical/before-state mention inside a changelog-style sentence, if
    any is added, is fine and should say so explicitly). `docs/OPERATIONS_QA.md` has a new dated entry.

- [ ] 14. [model: sonnet] [effort: low] Register IMP-U6.
  - Files: `improvements/improvements_tierU.md`, `improvements/PRIORITY.md`, `docs/priority-graph/priority-graph.html`
  - Depends on: Steps 0-13 (register the task as `done` reflecting the actually-implemented shape).
  - Consumed by: nothing in this plan; this is the durable backlog record future sessions read.
  - Details: **`improvements_tierU.md`** — append `## IMP-U6: <title>` after IMP-U5, in the exact
    `improvement_details.md` field format (Category/Priority/Files/Current behavior/Proposed
    change/Rationale/Goal/Effort estimate/Risk/If skipped/Status). Status = `done` (branch name cited),
    Risk = `low` (additive, no rollback-contract change, no ENTRY_TYPE_KEYS change — say so explicitly,
    mirroring IMP-D17/D19's risk write-ups), and explicitly note in "Current behavior"/"Rationale" that
    this task ALSO fixes a live artwork-inheritance regression + an active double-stamp risk (the
    IMP-C23-class bug) discovered during implementation — this is why it is elevated into Band 0 below
    rather than sitting at Tier U's normal medium/low priority. **`PRIORITY.md`** — add IMP-U6 to 🔴
    BAND 0 (alongside R10/C24/C22), following the maintenance protocol at the file's bottom exactly:
    bump "Last updated", extend the "Band 0 is NOT clear" framing to mention a 4th (now resolved) item,
    update the 👉 SUGGESTED NEXT TASK block to note IMP-U6 shipped and is NOT change-gated (unlike
    R10/C24), add a row to the Band 0 table (`✅ IMP-U6` once merged — while still `in_progress` on the
    branch, list it as such per the file's existing convention for in-flight work), and add one line to
    the "✅ DONE" list at the bottom once implementation is complete on the branch. **priority-graph.html**
    — add a `TASKS` row: `["U6","provider-token bracket format + migration cmd","U","crit","done","<one
    sentence: square-bracket [tmdbid-] canonical format, shared mvcommon detection helper, ancestor-aware
    migrate_provider_tokens command; also fixed a live artwork-inheritance regression + double-stamp risk
    from a prior partial migration">"]` (priority `crit` mirrors how C22/C24 were coded, reflecting the
    live-regression framing above); add an `EDGES` entry `["D17","U6"]` (this task is built directly on
    IMP-D17's `rename_folder`) and `["C23","U6"]` (same drift-bug-class lineage). Keep the three files'
    task-ordering claims mutually consistent (the graph and PRIORITY.md must always agree, per the
    file's own maintenance rule).
  - Acceptance: all three files updated in the SAME commit; `improvements/PRIORITY.md`'s maintenance
    protocol checklist (its own bottom section) fully satisfied; the graph's `TASKS`/`EDGES` arrays
    remain valid JS (no trailing-comma/syntax errors — spot check by eye, this file has no test
    harness).

## Risks and edge cases

- **Windows `MAX_PATH` (260 chars)**: swapping `{tmdb-1520211}` (15 chars) for `[tmdbid-1520211]` (17
  chars) lengthens every migrated folder name by ~2 characters. Negligible in isolation, but combined
  with an already-long release-group folder name near the 260-char ceiling, this could be the
  straw that breaks a rename. `cmd_rename_folder`'s `os.rename` will raise `OSError` in that case, which
  the existing pre-PONR failure path already handles safely (rolls back, prints the error) — no new
  crash mode, just a possible legitimate failure the migration's per-run report's `"errors"` array must
  surface clearly (already specified in Step 6).
- **A folder name that legitimately contains a literal, unrelated `{` or `[`** (extremely unlikely in
  real media naming, but the vocabulary-anchored regex from Step 1 already guards this — only a
  recognized tag+separator+id shape inside the bracket matches).
- **Concurrent library writes during a real `--apply` run** (IMP-C24's known, currently-open,
  change-gated issue: no lock exists on `library_*.json`). This plan does NOT fix or touch that —
  the "Real-library migration procedure" section below explicitly instructs the user to run the
  migration with no other MediaVault command running concurrently, exactly the same caution any
  existing write command already requires today.
- **The migration command is new attack surface for "renamed the wrong thing"** — mitigated by:
  dry-run-by-default (mirrors `enrich_metadata`), building on the already-battle-tested
  `cmd_rename_folder` rather than a new disk-mutation primitive, and the mandatory fresh backup step in
  the real-migration procedure below (independent of the `C:\Media\library_backups\imp-u6-2026-09-07\`
  backup the OTHER tool already took, which this plan does not rely on or assume still matches current
  state).
- **Emby's exact separator/tag-name leniency is not 100%-primary-source-pinned** (TRaSH says
  `[tmdb-…]`, a community-staff exchange shows `[tmdbid=…]` also works) — this plan's detection helper
  tolerates the ambiguity (accepts both), but the EMIT format is fixed regardless of this uncertainty,
  so it cannot cause a regression either way — only a (currently unquantifiable, likely small)
  reduction in Emby's own strict-match confidence versus its own most-conservative documented
  preference. See Open Decision #1/#9.

## Consumer Impact Analysis

Not a literal `ENTRY_TYPE_KEYS`/ENTRY-TYPE change (the token is a substring convention inside
`folder_path`'s free-form string value, not a structured key) — but this task is exactly the "grep
every consumer of a changed shared convention" situation that rule exists for (this convention has
already drifted three times — IMP-C18/C22/C23), so the same discipline is applied here in full, per
the codebase's own explicit warning comments at the two sites being fixed.

| # | Site | Line(s) | Access | Verdict | Why |
|---|------|---------|--------|---------|-----|
| 1 | `_has_tmdb_token` | main.py:1687-1698 | `re.search(r"\{tmdb-…\}", name, IGNORECASE)` | needs-fix | brace-only; fixed in Step 2 |
| 2 | `_PROVIDER_TOKEN_RE` / `_ancestor_show_folder_image` | main.py:9589, 9665 | same brace-only regex, drives artwork ancestor walk | needs-fix | **live regression today** — matches almost nothing against the already-migrated real library; fixed in Step 2 |
| 3 | `cmd_enrich_metadata` stamping | main.py:2689 | `f"{{tmdb-{tmdb_id}}}"` | needs-fix | emits legacy format; fixed in Step 3 |
| 4 | `_enrich_after_archive` stamping | main.py:7730 | same pattern (STANDING SYNC duplicate of #3) | needs-fix | must change WITH #3 or reintroduce the exact drift this task fixes; fixed in Step 3 |
| 5 | `suggest_target_folder` placeholder | main.py:8363, 8366 | literal `"{tmdb-0000000}"` / `"{tvdb-000000}"` strings | needs-fix | user-editable UI suggestion; fixed in Step 3; directly serves the user's stated future "put it inside []" request |
| 6 | `rename_folder` CLI help/usage | main.py:9954, 10540, 10544 | literal print strings | needs-fix | cosmetic only; fixed in Step 4 |
| 7 | Doc-comments (14 sites) | main.py:373/1291/1816/2024/3623/3672/7560/8327/8814/8824/9585/9652/9702/9777 | comments/docstrings | needs-fix | accuracy only, no logic; fixed in Step 4 |
| 8 | `cmd_rename_folder` itself | main.py:3620-3760 | accepts ANY string as the new leaf name; never parses/validates bracket style | safe | format-agnostic primitive — this is the tool Steps 3/6 call INTO, not a detector; verified by re-reading the full function body |
| 9 | `_write_nfo` / NFO XML emission | main.py (`_write_nfo`) | writes `<uniqueid type="tmdb">`/`<tmdbid>` from `metadata.tmdb_id` (an int) | safe | never touches the folder-name bracket convention; unrelated surface, confirmed by re-read |
| 10 | `webui/server.py` `/api/media-image`, all other `/api/*` routes | webui/server.py (repo-wide grep) | calls `main.resolve_artwork_path`; zero independent regex of its own | safe once #2 fixed | verified via `grep -rn "tmdb-\|tvdb-" webui/server.py` — the only 2 hits are a comment and a docstring reference to the same walk-up behavior #2 already covers |
| 11 | `webui/static/*.js` (all 15 files) | grep repo-wide | `preview.js`'s "tmdb" hits are 100% the unrelated TMDB-rating/external-link dossier feature (`d.tmdb_url`); `suggested_folder.folder` is displayed as an opaque string, never parsed | safe | verified — zero consumers found; `tests/js/test_data_buckets.mjs` line 120 has one cosmetic `{tmdb-?}` in test fixture text with zero functional coupling (optional, non-blocking freebie update, not required) |
| 12 | `mainfetch.py` (fetch/restore pipeline) | repo-wide grep | zero hits for `REMOTE_ROOT`, zero token-regex; restore keys off `search_term`/`filename` only | safe | verified — directly narrows the block-3g "remote path" risk framing: a folder rename does NOT break the ABILITY to find/restore an already-uploaded item, since fetch never derives a phone-side path from `folder_path` at all |
| 13 | `mvcommon.py` (pre-existing content) | repo-wide grep for `tmdb-\|tvdb-\|imdb-` | zero pre-existing hits | safe | confirms no duplicate parser already lurking here; this file GAINS the new shared helper in Step 1 (the fix landing, not a consumer needing one) |
| 14 | `tools/*.py` | repo-wide grep | zero hits | safe / out of scope |
| 15 | Rollback mechanism (`RollbackJournal`, PONR table, `recover_journal`) | ARCHITECTURE.md §12a, ROLLBACK_MECHANISM.md | not touched by any step in this plan | safe | `cmd_rename_folder` is reused exactly as IMP-D17 built it — additive, no journal/PONR/contract change; **no change-gate applies to this plan** (stated explicitly per CLAUDE.md's "surface fundamental contradictions" duty — this is the active confirmation, not a silent skip) |
| 16 | `cmd_push` / `push_one_extra` remote-path derivation | main.py:4767-4771, 4481-4485 | `os.path.relpath(local_folder, LOCAL_ROOT)` computed fresh from CURRENT `folder_path` at call time, never stored | safe (narrowed) but noted | does not misbehave — it simply keeps deriving from whatever `folder_path` currently is; the exposure is a FUTURE-tooling / disaster-recovery concern (block 3g), not a correctness bug today; the migration's per-run `remote_bearing` report field (Step 6) is the mitigation — see Open Decision #3 |
| 17 | `split_video_file` brace-escape | main.py (`split_video_file`) | doubles literal `{`/`}` only | safe | confirmed by the EXISTING `test_split_no_braces_path_is_unchanged` test; square brackets never trigger it; kept unchanged (Step 10 adds a pin, no code change) — see Open Decision #4 |

## Test plan

New files: `tests/test_provider_tokens.py` (Step 5), `tests/test_migrate_provider_tokens.py` (Step 7).
Updated files: `tests/test_web_media_image.py` (Step 8, additive cases only), `tests/test_enrich_metadata.py` /
`tests/test_prep_push_rep_enrich.py` / `tests/test_prep_push_rep_season_enrich.py` / `tests/test_web_datafns.py`
(Step 9, assertion-format updates only), `tests/test_split_brace_escape.py` (Step 10, one additive test),
`tests/smoke/test_smoke_all_commands.py` (Step 11, one additive case + one assertion update). Every test
uses the `sandbox`/`sandbox_alias` fixtures per `docs/testing-strategy.md`; nothing touches real
`C:\Media` or real `library_*.json`. Coverage explicitly includes every item block 6/§7 of this task's
brief required: the three-format detector (incl. `[rartv]` non-match and `{TMDB-…}` IMP-C23-style
uppercase), migration-command tests on fixtures, idempotency/re-run tests, an ancestor-rename test
(Friends shape), a regression test for the mkvmerge brace escape, artwork inheritance across all three
formats, and the smoke gate.

## Verification

```powershell
python -m pytest tests -q                          # full suite (no testpaths configured — must scope to tests/)
pytest tests/test_provider_tokens.py tests/test_migrate_provider_tokens.py -q   # the two new files, isolated
pytest tests/test_web_media_image.py tests/test_rename_folder.py tests/test_set_tmdb.py -q   # adjacent-surface spot check
pytest tests/smoke -q                               # MANDATORY final gate — this plan touches main.py
```

## Out of scope

- Any change to the auto-rollback mechanism, journal format, PONR placement, or `RollbackHardFail`
  contract (none needed — see Consumer Impact Analysis #15).
- Any change to `ENTRY_TYPE_KEYS` or the entry-type schema (none needed — no library field/type
  changes; the token lives inside `folder_path`'s free-form string value).
- Normalizing or touching `[tvdbid-…]` folders (already canonical square-bracket, source-provided,
  nothing to migrate — see Open Decision #5).
- Adding a persisted `remote_path` field to the library schema, or any live `adb`-side directory
  reconciliation on the phone (see Open Decision #3 — recommended against for THIS PR).
- Running `migrate_provider_tokens --apply` against the real `C:\Media` library (explicitly deferred to
  the user-run procedure below, per the user's own instruction).
- IMP-C24 (concurrent library writes / no lock), IMP-C22 (anime per-episode enrichment), IMP-R10 — all
  pre-existing, unrelated, already-tracked issues; not touched by this plan.
- Fixing Emby's exact separator/tag-name behavior with 100% primary-source certainty (would require
  reading Emby's non-fully-open-source parser code) — the detection helper is deliberately permissive
  enough that this uncertainty cannot cause a regression either way (see Risks).

---

## Real-library migration procedure (POST-MERGE, user-run — NOT an automated step of this plan)

Per the user's own instruction ("Once the change is completed… for now, just create the plan to do
this"), nothing above touches `C:\Media`. Once this PR is merged and `main` is synced locally, run
these from the repo root, in order, with **no other MediaVault command running concurrently** (IMP-C24
caution — there is no library lock today):

```powershell
# 1. Fresh backup — independent of the other tool's 2026-09-07 03:52 backup, which this
#    procedure does not assume still matches current disk/JSON state.
robocopy "C:\Media" "C:\Media\library_backups\imp-u6-post-merge-$(Get-Date -Format yyyyMMdd-HHmm)" library_movies.json library_series.json library_anime.json library_others.json

# 2. Dry run FIRST — read-only, prints every folder it WOULD rename + a summary. Confirm the
#    count matches expectations (per this plan's audit: 0 for movies/anime, a small number of
#    ANCESTOR folders for series — likely well under 202, since many stragglers share one show folder).
python main.py migrate_provider_tokens

# 2b. Optionally scope to just the series library first (where all known stragglers live):
python main.py migrate_provider_tokens --library series

# 3. Review the dry-run output. If it matches expectations, apply for real:
python main.py migrate_provider_tokens --apply

# 4. Re-run the dry run to confirm zero remaining old-format folders (should report 0 candidates):
python main.py migrate_provider_tokens

# 5. Spot-check the JSON report written under C:\Media\migration_reports\ — review the
#    "remote_bearing" entries list (folders that were already pushed/archived) for your own
#    awareness; no action is required unless you plan to build phone-storage cleanup tooling later.

# 6. Run the smoke gate once more against the real environment as a final sanity check
#    (this does NOT touch real C:\Media — it's the same fixture-only suite):
pytest tests/smoke -q
```

If anything looks wrong at step 3's dry-run review, STOP and do not pass `--apply` — the dry-run output
plus this plan's fixture tests are the safety net; there is no destructive action until `--apply` is
explicitly passed.

---

## Branch, PR, and manual test commands

**Branch:** `feature/imp_u6_provider_tokens` (deliberately distinct from the other tool's
`feature/imp_u6_token_brackets`, per the user's explicit comparison intent — do not reuse their name).

**PR title:** `feat: canonical [tmdbid-] provider-token format + migrate_provider_tokens command — IMP-U6`

**PR body order** (per `docs/git-pr-conventions.md`):
1. Auto-generated Claude Code summary (Summary / Changes / Test plan).
2. `## Original task prompt` — the COMPLETE VERBATIM text of the user's original task message (BLOCK 1
   of this planning brief), not paraphrased or trimmed.
3. `🤖 Generated with [Claude Code](https://claude.com/claude-code)` trailer.

**Checkpoint 1** (human-gated): create the PR, then STOP — never `gh pr merge` without the user's
explicit confirmation. **Checkpoint 2** (human-gated, later): archiving the branch after merge is a
separate ask.

**Copy-pasteable manual test commands** (for the user, after checkout):

```powershell
git fetch && git checkout feature/imp_u6_provider_tokens
python -m pytest tests -q
pytest tests/smoke -q
python -c "import mvcommon; print(mvcommon.has_tmdb_token('Run (2002) {TMDB-69590}'))"          # True
python -c "import mvcommon; print(mvcommon.has_tmdb_token('Peaky.Blinders.S06...FLUX[rartv]'))"  # False
python -c "import mvcommon; print(mvcommon.find_provider_tokens('Show [tvdbid-266189] [tmdbid-70523]'))"
python main.py migrate_provider_tokens          # dry-run against YOUR real library — read-only, safe to run anytime
```

---

## Open Decisions

Every genuine choice below carries a recommendation; none of these block planning — they are surfaced
for the user's ruling at the PR checkpoint (or earlier), per this plan's explicit instruction not to
block on `AskUserQuestion`.

1. **Canonical emit format.** Recommend **`[tmdbid-<id>]`** (Jellyfin-exact, likely-Emby-compatible,
   matches the 1396 folders the other tool already migrated). Alternative: keep `{tmdb-…}` (Plex-exact,
   but reverts already-completed work and moves away from the user's own stated future target).
   Alternative: emit BOTH tokens in one name (`Movie (2020) {tmdb-603692} [tmdbid-603692]`) — the only
   way to literally satisfy every server's own documented preference simultaneously (Jellyfin's docs
   explicitly bless multiple identifiers in one name); costs a longer folder name and doubles the
   token surface. Not recommended as the default (Plex's fuzzy title/year fallback already covers the
   gap adequately for this user's real library), but flagged because the user directly asked "is it
   actually true… let me know if any issues" and this is the one genuine issue found.
2. **Migrate the remaining ~202 stragglers forward vs. restore the 03:52 backup and re-run from
   scratch.** Recommend **finish forward** (idempotent, additive, touches only the ancestor-gap
   folders). Restoring-and-redoing would needlessly re-touch 1396 already-correctly-migrated folders
   for zero benefit (disk ⇄ JSON are already mutually consistent for current state) and risks losing
   the other tool's already-verified-safe work.
3. **Remote-path (block 3g) remedy.** Re-verified finding: `mainfetch.py` never derives a path from
   `REMOTE_ROOT`; fetch/restore work entirely via Google-Photos search on `search_term`, so a folder
   rename does NOT break the ability to find/restore an already-archived item — the original risk
   framing was broader than the code supports. Recommend the LIGHT remedy already built into Step 6
   (the migration's per-run JSON report records every `remote_bearing` rename for future reference)
   and explicitly recommend AGAINST both (a) adding a persisted `remote_path` schema field in this PR,
   and (b) any live `adb`-side phone directory reconciliation — neither is needed today, both add real
   risk/scope for a problem that is currently only theoretical (relevant if/when IMP-E5 phone-cleanup
   tooling is ever built; note this caution in that task's own doc when it starts).
4. **Keep the mkvmerge brace-escape in `split_video_file`?** Recommend **keep unchanged**. It is
   harmless dead-weight for square-bracket-only folders (proven by the existing
   `test_split_no_braces_path_is_unchanged` test), still actively needed for the one residual real
   brace folder and any future legacy import, and is regression-tested — CLAUDE.md is explicit that a
   regression-tested behavior must not be silently deleted.
5. **Also normalize `[tvdbid-…]`?** Nothing to normalize — it is already canonical square-bracket,
   source-provided, never MediaVault-generated. Recommend: detection recognizes it (already planned,
   Step 1), emission never touches it, migration preserves it verbatim when it coexists with a
   migrated tmdb token (already specified, Step 6).
6. **Touch the real library in this PR at all?** Recommend **no** — ship code + tests only, on the
   feature branch, fixtures only. The real migration is the separate, explicit, user-run procedure
   documented above, matching the user's own words exactly.
7. **Single canonical token vs. dual-token emission** (expanded from #1) — see #1; this is really the
   same decision viewed from the "make literally all 3 work" angle. Recommend single-token
   (`[tmdbid-…]` only) as the default; dual-token is a legitimate, low-risk future follow-up if the
   user later finds a specific Plex title failing to strict-match.
8. **IMP code / tier / Band placement.** Recommend **IMP-U6** (Tier U — Couch UX & Clients; confirmed
   free, U1-U5 exist), registered as a SINGLE task (not split into a separate Tier-C bug code for the
   live artwork-inheritance regression) since both are fixed by the identical code change in the same
   PR — splitting would only create bookkeeping duplication. Recommend elevating it into **PRIORITY.md
   Band 0** (critical) specifically because of the live regression + active double-stamp corruption
   risk on the real library today, mirroring IMP-C23's precedent (same drift-bug-class, was Band 0).
9. **Emby separator/tag-name leniency.** Not 100%-primary-source-pinned (TRaSH says `[tmdb-…]`, a
   staff-confirmed community post shows `[tmdbid=…]` also works). Recommend: detection tolerates both
   spellings and both separators on the square-bracket family (cheap, safe, already specified in Step
   1); emission stays fixed at the single canonical `[tmdbid-<id>]` form regardless of this
   uncertainty, so it cannot cause a regression in either direction.
