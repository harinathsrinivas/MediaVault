# Task: Fix episode-number extraction so filenames whose title begins with a dotted number (e.g. `Fringe.S03E20.6.02.AM.EST...`) prep, push, and replace correctly

Suggested branch: fix/episode_title_dotted_number

## Context
`cmd_prep_season` derives each episode's library ID from the filename using the regex
`[sS]\d+[eE](\d+(?:\.\d+)?)` (`main.py:834`). For
`Fringe.S03E20.6.02.AM.EST.2011.1080p.BluRay.REMUX.AVC.DTS-HD.MA.5.1-NOGRP.mkv`
the episode title is "6.02 AM EST", so the `.6` immediately after `E20` is the start of the
title, not a fractional episode. The optional decimal group `(?:\.\d+)?` greedily captures `.6`,
producing ID `...s03e20.6` instead of `...s03e20`. Downstream, the `episodes 20-20` range filter
parses the ID's episode segment as the float `20.6`, and `20.0 <= 20.6 <= 20.0` is False, so the
season auto-pilot finds 0 episodes and silently skips push and replace.

The user's constraint is explicit: **"fix this without touching the core logic. I want all the
commands to work for this type of file name."** Core logic = rollback journal / PONR markers /
push-replace-restore business logic / library schema. The episode-extraction regex is NOT core
logic and is the only thing this fix touches.

**Decision-closing clarification from the user (2026-06-05):** *"Yes I have .5 use-cases in anime
for now. I've not seen it in movies or series for now."* This resolves Open Decision 1 definitively
(see below). `.5` fractional episodes exist **only in anime** in this library; series and movies
use `SxxExx` and have no `.5` half-episodes. The two regex branches are independent, so the
`SxxExx` branch can drop the decimal capture entirely (no lookahead needed), while the anime
branches keep their decimal capture so anime `.5` entries keep working.

## Goal
Running, for the Fringe S03 folder:
`python main.py prep_push_rep_season tv-en-2010-fringe-s03 "<folder>" SIZE_MB 9900 episodes 20-20 device movies`
produces a library entry with ID `tv-en-2010-fringe-s03e20` (NOT `...e20.6`), and the
`episodes 20-20` filter selects exactly that episode so push and replace run. Anime `.5`
half-episodes parsed through the `NxYY` (`\d+[xX]`) / absolute-numbering branches continue to keep
their `.5`. Definition of done: new unit tests covering the dotted-title `SxxExx` filename, the
canonical `SxxExx` filename, and an anime `NxYY` `.5` half-episode all pass under `pytest -q`, and
the full suite stays green.

## Files affected
- `main.py` — the `SxxExx` episode-extraction regex in `cmd_prep_season` (`main.py:834`); this is
  the ONE behavioral change. The `NxYY` (`\d+[xX]`) branch on `main.py:835` keeps its existing
  `(?:\.\d+)?` decimal capture unchanged (anime `.5` must keep working). See Approach.
- `tests/test_prep_season_episode_parse.py` — NEW test file covering the dotted-title `SxxExx`
  filename, canonical `SxxExx`, and an anime `NxYY` `.5` half-episode.
- `docs/feature-fix-episode-title-parse/PLAN.md` + `docs/feature-fix-episode-title-parse/DECISIONS.md`
  — tracked plan + decision record (created by this planning pass / git-agent).

## Approach
The defect is entirely in how `cmd_prep_season` builds the ID for `SxxExx` filenames at
`main.py:834`. The downstream filter sites (`cmd_push_group` 1311, `cmd_restore_group` 1770,
`cmd_prep_push_rep_season` 2072, `mainfetch.resolve_targets` 351) are *correct given a correct ID*
— they read back whatever episode segment the ID carries. So the surgical, minimal fix is to make
the `SxxExx` extraction regex stop capturing the optional decimal entirely.

The user has confirmed that `.5` half-episodes exist **only in anime**, never in series/movies.
Series and movies are exactly the files that flow through the `SxxExx` branch. Anime flows through
the separate `\d+[xX]` (`NxYY`) branch on `main.py:835` and the absolute-numbering Strategy-2 branch
on `main.py:845`. Because the branches are independent, dropping the decimal capture from the
`SxxExx` branch:
- correctly captures `20` from `...S03E20.6.02.AM...` (the `.6` title start is no longer consumed),
- correctly captures `19` from a canonical `...S03E19.1080p...`,
- cannot regress any real half-episode, because no series/movie has a `.5` (user-confirmed).

This is **Option A**, now safe and the simplest correct fix:

```python
# main.py:834-835  (BEFORE)
match = re.search(r"[sS]\d+[eE](\d+(?:\.\d+)?)", filename)
if not match: match = re.search(r"\d+[xX](\d+(?:\.\d+)?)", filename)

# (AFTER) — SxxExx drops decimal capture (no series/movie .5); NxYY keeps decimal for anime .5
match = re.search(r"[sS]\d+[eE](\d+)", filename)
if not match: match = re.search(r"\d+[xX](\d+(?:\.\d+)?)", filename)
```

Why the `NxYY` (`\d+[xX]`) line keeps `(?:\.\d+)?` as-is rather than taking a lookahead guard:
anime `.5` entries are real and must work, and the `NxYY` convention is compact
(`Show 16x05`, `Show 16x05.5`) — it does not carry dotted release-title tokens after the episode
the way the dotted `SxxExx` REMUX scene names do, so the greedy-decimal ambiguity that bit the
`SxxExx` branch does not occur here in practice. Keeping the existing capture is the simplest
correct choice and preserves anime `.5` parsing with zero risk to current entries. (The
absolute-numbering Strategy-2 branch on `main.py:845`, which also handles anime `.5`, is likewise
left untouched.) If the implementer discovers a real dotted-title anime filename in the `NxYY`
branch that mis-parses, STOP and surface it as a decision rather than silently changing that line.

Critically, `search_term` (what `push`/`fetch` type into Google Photos) is built from the raw
**filename**, not from the ID (`main.py:700-702`), so correcting the ID does NOT change or break the
Google Photos search at all (Open Decision 2 — no second patch needed). Confirmed: both `cmd_push`
→ `write_remote_mvmeta`/upload and `mainfetch` (`resolve_targets`/`build_download_queue`,
`mainfetch.py:378-408`) search by `entry["search_term"]` / `entry["filename"]`, never by the ID's
episode segment.

The implementer MUST verify the `SxxExx` behavior with a quick Python REPL/`re` check before
editing, and confirm the chosen regex against the test fixtures in Step 2 (see Acceptance).

## Steps

- [x] 1. [model: opus] [effort: high] Fix the `SxxExx` episode-extraction regex in `cmd_prep_season`
  - Files: `main.py:834-835`
  - Details: Change ONLY the `SxxExx` pattern on `main.py:834` from
    `r"[sS]\d+[eE](\d+(?:\.\d+)?)"` to `r"[sS]\d+[eE](\d+)"` (drop the optional decimal group).
    LEAVE the `NxYY` pattern on `main.py:835` exactly as it is: `r"\d+[xX](\d+(?:\.\d+)?)"` (anime
    `.5` must keep working). Do NOT touch the Strategy-2 anime absolute-numbering branch
    (`main.py:845`), the auto-parent regex (`main.py:663`), or any of the four range-filter sites
    (`cmd_push_group` 1311, `cmd_restore_group` 1770, `cmd_prep_push_rep_season` 2072,
    `mainfetch.resolve_targets` `mainfetch.py:351`) — they are correct once the ID is correct.
    Before editing, run a throwaway `python -c "import re; ..."` against
    `Fringe.S03E20.6.02.AM.EST...` and `Fringe.S03E19.1080p...` to confirm 20.6→20 and 19→19; and
    against an anime `Show 16x05.5` to confirm the untouched `NxYY` branch still yields `16x05.5`'s
    `05.5`. Do not touch rollback/PONR/journal code (change-gated — none of it is in scope here;
    confirm the diff is limited to line 834).
  - Acceptance: `re.search(r"[sS]\d+[eE](\d+)", "Fringe.S03E20.6.02.AM.EST.2011.mkv").group(1) == "20"`;
    `re.search(r"[sS]\d+[eE](\d+)", "Fringe.S03E19.1080p.mkv").group(1) == "19"`;
    `re.search(r"\d+[xX](\d+(?:\.\d+)?)", "[Grp] Show 16x05.5 [hash].mkv").group(1) == "05.5"`
    (NxYY line unchanged); `git diff` shows only `main.py:834` changed.

- [x] 2. [model: sonnet] [effort: medium] Add unit tests for filename → episode-ID extraction
  - Files: `tests/test_prep_season_episode_parse.py` (new)
  - Details: Read `docs/testing-strategy.md` first. This is a library-I/O test of `cmd_prep_season`,
    so use the `sandbox` fixture (it patches BOTH `mvcommon.LIBRARY_*` AND `main.LIBRARY_*` — do not
    DIY). Seed a temp media folder under `sandbox["media_dir"]` (or `tmp_path`) with fake `.mkv`
    files (write a few hundred bytes each so `cmd_prep` does not treat them as dummies;
    `get_tech_specs` failures are tolerated — assert on the resulting library keys, not on
    tech_spec): (a) `Fringe.S03E20.6.02.AM.EST.2011.1080p.BluRay.mkv`, (b) a canonical
    `Fringe.S03E19.1080p.BluRay.mkv`, (c) an anime `NxYY` half-episode
    `[Grp] Show 16x05.5 [hash].mkv` (exercises the untouched `\d+[xX]` branch). Call
    `main.cmd_prep_season("tv-en-2010-fringe-s03", <folder>)` for the Fringe set and assert the
    resulting `mvcommon.load_library()` contains key `tv-en-2010-fringe-s03e20` (NOT `...e20.6`) and
    `tv-en-2010-fringe-s03e19`. For the anime file, prep it under an anime base ID with
    `is_anime`/the appropriate code path and assert the derived key keeps the `.5` (proving the
    `NxYY` branch still captures decimals). If `pymediainfo` / `MediaInfo` is unavailable in CI
    causing `cmd_prep` to bail, monkeypatch `main.get_tech_specs` to return `{}` so the test
    isolates ID derivation. Constraints (state in the test module docstring):
    "Never touch real C:\\Media files or real library_*.json." and
    "Run `pytest -q` and fix failures before marking the step done."
  - Acceptance: `pytest tests/test_prep_season_episode_parse.py -q` passes; the dotted-title case
    asserts the `e20` key exists and the `e20.6` key does NOT; the anime case asserts the `.5` is
    preserved; full `pytest -q` stays green.

- [x] 3. [model: sonnet] [effort: medium] Add a filter-arithmetic regression test for the range filter
  - Files: `tests/test_prep_season_episode_parse.py` (extend) — pure-function-style, no I/O
  - Details: Add focused assertions that, GIVEN a correctly-formed ID `tv-en-2010-fringe-s03e20`, the
    season filter logic selects it for `episodes 20-20`. Two acceptable shapes (pick the lighter one
    that does not require touching core logic): (i) a direct unit assertion replicating the filter's
    regex+float math from `main.py:2072-2076` against the corrected ID (documents the invariant that a
    clean `e20` ID yields `ep_num == 20.0`), OR (ii) drive `main.cmd_prep_push_rep_season` with the
    push/replace steps monkeypatched to no-op recorders and assert the corrected episode is the one
    selected by `episodes 20-20`. Prefer (i) for simplicity unless (ii) is needed to prove the
    end-to-end selection. Same C:\\Media / `pytest -q` constraints in the docstring.
  - Acceptance: test demonstrates `tv-en-2010-fringe-s03e20` is included by `episodes 20-20` and a
    hypothetical `...e20.6` would be excluded; `pytest -q` green.

- [x] 4. [model: haiku] [effort: low] Record the decision and update the tracked plan
  - Files: `docs/feature-fix-episode-title-parse/DECISIONS.md` (new),
    `docs/feature-fix-episode-title-parse/PLAN.md` (copy of this plan)
  - Details: In `DECISIONS.md`, record: (1) Open Decision 1 RESOLVED — the user confirmed `.5`
    exists only in anime (never series/movies), so the `SxxExx` branch safely drops the decimal
    capture (Option A) while the `NxYY`/absolute anime branches keep it; (2) why the `NxYY` line is
    left unchanged rather than lookahead-guarded (anime `.5` must work; compact `NxYY` names carry no
    dotted title tokens); (3) why `search_term` needs no change (built from filename,
    `main.py:700-702`); (4) Open Decision 3 — stale `...e20.6` entries from prior runs are NOT
    cleaned up here (out of scope; `repair_library` IMP-D5 is the right tool; manual workaround:
    remove the stale season-map child). Do NOT alter rollback documentation. This is a doc-only step.
  - Acceptance: both files exist under `docs/feature-fix-episode-title-parse/`; `DECISIONS.md` names
    the four points above and the `docs/.../PLAN.md` is identical to this plan.

## Risks and edge cases
- **A future series/movie `.5` half-episode would now lose its `.5`.** With the `SxxExx` decimal
  capture removed, a real `S02E13.5` series special would parse as `13`. This is acceptable *per the
  user's explicit confirmation that no series/movie `.5` exists* (only anime). If that ever changes,
  the documented workaround is to prep that one episode explicitly with
  `python main.py prep <id>e13.5 "<file>"`, and the longer-term fix is to revisit the `SxxExx`
  branch. Recorded in DECISIONS.md.
- **Regex engine behavior must be verified, not assumed.** Step 1 mandates a REPL check for both the
  changed `SxxExx` line (20.6→20, 19→19) and the unchanged `NxYY` line (anime `.5` preserved) before
  and after the edit.
- **Idempotency / re-prep**: if the user already ran prep and created the bad `...e20.6` entry plus a
  bad `...e20.6` season-map child, re-running prep with the fix creates the correct `...e20` entry but
  leaves the stale `...e20.6` child in the season map. This fix does not clean up pre-existing bad
  entries (out of scope; IMP-D5 `repair_library` is the right tool). Noted in DECISIONS.md so the
  user can manually remove a stale child if one exists from prior runs.
- **Do not touch rollback-related code** — change-gated per CLAUDE.md. The diff must be confined to
  the single `SxxExx` regex line plus tests/docs. Confirm with `git diff` (Step 1 acceptance).
- **Anime `NxYY` branch is intentionally left as-is.** It keeps `(?:\.\d+)?` so anime `.5` keeps
  working; no live anime entry exercises it with a dotted release title, so the greedy-decimal issue
  that affected `SxxExx` does not arise here. Covered by the Step 2 anime fixture.

## Verification
```powershell
# 1. Regex behavior spot-check (run before and after the edit)
python -c "import re; print(re.search(r'[sS]\d+[eE](\d+)', 'Fringe.S03E20.6.02.AM.EST.2011.1080p.BluRay.mkv').group(1))"  # -> 20
python -c "import re; print(re.search(r'[sS]\d+[eE](\d+)', 'Fringe.S03E19.1080p.BluRay.mkv').group(1))"                   # -> 19
python -c "import re; print(re.search(r'\d+[xX](\d+(?:\.\d+)?)', '[Grp] Show 16x05.5 [hash].mkv').group(1))"              # -> 05.5

# 2. New tests
pytest tests/test_prep_season_episode_parse.py -q

# 3. Full suite stays green
pytest -q

# 4. Confirm the diff is surgical
git diff --stat
git diff main.py
```

Manual test commands (run by the user against real data, NOT in CI):
```powershell
# Dry confirmation that prep now produces the right ID (against the real Fringe S03 folder)
python main.py prep_season tv-en-2010-fringe-s03 "C:\Media\Series\English\Fringe\Season 03"
# Then inspect library_series.json for key tv-en-2010-fringe-s03e20 (NOT ...e20.6)

# Full auto-pilot for the single episode that previously found 0 matches:
python main.py prep_push_rep_season tv-en-2010-fringe-s03 "C:\Media\Series\English\Fringe\Season 03" SIZE_MB 9900 episodes 20-20 device movies
# Expect: "Filtered to 1 episodes (20-20)" then push + replace actually run.
```

## Open Decisions
1. **How to handle fractional episodes in `SxxExx` (the disambiguation strategy).** **RESOLVED
   (2026-06-05).** The user confirmed: *"Yes I have .5 use-cases in anime for now. I've not seen it
   in movies or series for now."* Implication: `.5` half-episodes exist only in anime, which flows
   through the separate `NxYY` (`\d+[xX]`) and absolute-numbering branches — never through the
   `SxxExx` branch used by series/movies. Therefore **Option A is now safe for `SxxExx`**: drop the
   decimal capture (`[sS]\d+[eE](\d+(?:\.\d+)?)` → `[sS]\d+[eE](\d+)`); no lookahead needed. The
   `NxYY` anime branch keeps its `(?:\.\d+)?` so anime `.5` continues to parse correctly. (The
   earlier lookahead Option B and the filter-side Option C are no longer needed.)
2. **Does the search key need a separate patch?** RESOLVED during research: **No.** `search_term` is
   built from the raw filename at `main.py:700-702` and is independent of the ID; `cmd_push` and
   `mainfetch` search by `search_term`/`filename` (`mainfetch.py:378-408`), never by the ID's episode
   segment. Fixing the ID alone is sufficient; no search-logic change is required.
3. **Clean up pre-existing bad `...e20.6` entries?** RESOLVED: **OUT OF SCOPE here** (the user may
   have one from a prior failed run). The correct tool is the planned `repair_library` (IMP-D5).
   DECISIONS.md notes the manual workaround (remove the stale season-map child).

## Out of scope
- Migrating CLI parsing to flags / `argparse` (IMP-A2).
- A general filename/ID validator or `repair_library` (IMP-D5/D10), including cleanup of any stale
  `...e20.6` entries created by earlier runs.
- Any change to the rollback journal, PONR markers, push/replace/restore business logic, or library
  schema (change-gated).
- Changing `search_term` generation or Google Photos search behavior (confirmed unnecessary).
- Touching the auto-parent detection regex at `main.py:663` (it strips the trailing episode segment
  from the *already-formed* ID; once the ID is `...e20`, it already works correctly).
- Touching the `NxYY` (`main.py:835`) or absolute-numbering (`main.py:845`) anime branches — they
  keep their decimal capture so anime `.5` keeps working.

## Branch name suggestion
`fix/episode_title_dotted_number`

## PR title
`fix: episode-number parsing for dotted-title filenames (Fringe S03E20.6...)`

(No IMP code applies — this is an untracked bug, not a tracked `improvements_tier*` task. The closest
references are IMP-A2's "auto-parent regex on every ID convention" test goal and IMP-D5/D10
validation, but neither owns this fix. If the user wants it tracked, file it under Tier D as a new
parsing-robustness item.)
