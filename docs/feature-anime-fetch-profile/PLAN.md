# >>> AWAITING USER CONFIRMATION <<<
# This plan needs review before execution. Read the "Issue Explanation + Real-World
# Scenario" and "Open Decisions" sections below and confirm (or pick options) before
# any code is written.

---

# Issue Explanation + Real-World Scenario (READ FIRST)

## What IMP-C16 is (the exact bug)

`mainfetch.py` knows about only **two** Chrome profiles:

```python
# mainfetch.py:31-34
CHROME_PROFILES = {
    "default": r"C:\Media\Utils\ChromeProfile",      # signed into the MOVIES Google account
    "tv":      r"C:\Media\Utils\ChromeProfile_TV"     # signed into the SERIES + anime Google account
}
```

and `cmd_fetch_route` picks the profile purely by id prefix (`mainfetch.py:442-447`):

```python
active_profile = "default"
if manual_id.startswith("tv") or manual_id.startswith("ani"):   # <-- ani folded into tv
    print("   > 📺 TV Series detected: Using 'ChromeProfile_TV'")
    active_profile = "tv"
else:
    print("   > 🎬 Movie (or other) detected: Using Default Profile")
```

So **`tv-*` and `ani-*` both drive the `tv` profile** (the *series* account's logged-in
Chrome session).

The user has since confirmed (2026-06-12, fable-review follow-up; recorded as the answer
to the IMP-X2 topology question) that the real storage topology is **three separate Google
accounts: movies, series, and anime**. Anime chunks are uploaded *to the anime account*
(by a Pixel signed into the anime account). But anime **fetch** searches Google Photos in
the **series** account's session — a different account that does not contain the anime
files. The search finds **0 thumbnails**, the harvester downloads nothing, and the restore
reports "ENTRY INCOMPLETE." There is no error explaining *why*: it looks exactly like a
session expiry (the IMP-C6 symptom), so the user would burn time re-logging-in the series
profile — which can never fix it, because the files are in a different account entirely.

This is **latent today, not yet observed in production**, for one specific reason
(ARCHITECTURE §6.2): **0 of 140 anime leaves have `split_info`** — no anime title has ever
been chunk-restored. The first real archived-anime restore is the moment this bug fires.

## Why it is a real bug and not a theoretical one

The whole point of the system is *restore* — pushing to the cloud is only half the loop.
An entire third of the library (anime: 140 leaves, 108 already `archived`) is currently
**un-restorable** the moment a user tries, and the failure is silent + misdiagnosable. It
also blocks the couch-vault end goal for anime entirely, and it is the seam that the
multi-account work (IMP-X1 replication, IMP-X4 cross-account restore fallback) builds on —
both are wired to depend on C16 in the priority graph (`C16 → X1`, `C16 → S3`).

## Concrete real-world scenario (step by step)

Setup (true today): the user has three Google accounts — movies, series, anime — each with
its own Pixel and its own Photos library. `C:\Media\Utils\ChromeProfile` is logged into the
movies account; `C:\Media\Utils\ChromeProfile_TV` is logged into the series account. There
is **no** anime profile. An anime season, say *Death Note*, was archived months ago: it was
pushed to the **anime** account (split into chunks, because the user used `SIZE_MB 9900` on
a large 4K rip), then `replace`d locally — so the only copy of each episode now lives in the
anime account's Photos, and the local files are tiny dummies.

1. The user wants to watch *Death Note* on the couch tonight. They run:

   ```
   python main.py fetch_restore ani-ja-2006-deathnote episodes 1-3
   ```

2. `cmd_fetch_route("ani-ja-2006-deathnote01")` sees the id starts with `ani`, prints
   `📺 TV Series detected: Using 'ChromeProfile_TV'`, and launches Chrome on the **series**
   account.

3. For each chunk, `trigger_download` opens `photos.google.com` (series account), types the
   chunk filename into search, waits, and finds **0 matching thumbnails** — because the
   chunk lives in the *anime* account, not this one. It prints `⚠️ Not found (Found 0).`,
   retries once (IMP-C2), still 0, returns False.

4. The harvester waits the full timeout watching an empty `~/Downloads`, then times out.
   `cmd_restore` finds no chunks in `restore/`, skips, and the run ends with
   `❌ ENTRY INCOMPLETE.`

5. **What the user expected:** the three episodes download and play.
   **What goes wrong:** nothing downloads; the message looks like a session problem. The
   user opens the series Chrome profile, sees they are logged in fine, re-runs, gets the
   same 0-thumbnail result, and is stuck — because the real fix (search the *anime*
   account) is impossible with today's two-profile routing. A whole third of the library is
   a confusing dead-end.

## The fix in one sentence

Add a third Chrome profile `anime` → `C:\Media\Utils\ChromeProfile_Anime` (signed into the
anime account), route `ani-*` there, and make the id-prefix → profile selection a small
**data-driven map** (the seam IMP-X1/X4 extend, fully wired to external config later by
IMP-A5) so movies→movies-account, `tv-*`→series-account, `ani-*`→anime-account.

---

# Open Decisions (please confirm before execution)

These are the only choices that need your input. Defaults are marked; if you are fine with
the defaults, just say "go with the defaults."

### OD-1 — How "data-driven" should the prefix→profile map be, given IMP-A5 is NOT done yet?

The IMP-C16 task text says "make the id-prefix → profile map **data-driven (config,
IMP-A5)**." But **IMP-A5 (`mvconfig.json`) is still `pending`** (verified in
`improvements_tierA.md`). C16 is sequenced *before* A5 (Band 1 vs Band 2) and is meant to
be small + low-risk. So we cannot literally read the map from a config file that does not
exist yet.

- **Option A (DEFAULT, recommended): in-module data-driven map now; external config later.**
  Replace the hardcoded `if startswith` chain with a small ordered list/dict constant in
  `mainfetch.py`, e.g.
  ```python
  CHROME_PROFILES = {
      "movies": r"C:\Media\Utils\ChromeProfile",
      "tv":     r"C:\Media\Utils\ChromeProfile_TV",
      "anime":  r"C:\Media\Utils\ChromeProfile_Anime",
  }
  # ordered, longest-prefix-first so "ani" wins before any shorter prefix
  ID_PREFIX_PROFILE = [("ani", "anime"), ("tv", "tv"), ("mov", "movies")]
  DEFAULT_PROFILE = "movies"
  ```
  and a tiny pure `profile_for_id(manual_id)` that consults the list. Adding a 4th account
  is then a one-line edit to two constants (the "config edit, not code edit" intent),
  unit-testable with no Selenium, and IMP-A5 later just feeds these two constants from
  `mvconfig.json`. This fully satisfies the C16 Goal ("the profile map is config-driven")
  at the data-structure level without taking on the A5 lift.

- **Option B: do the full `mvconfig.json` config wiring now (pull IMP-A5 forward).**
  Larger blast radius (every module reads constants at import time; the testing-strategy
  §6.3 dual-binding hazard; new example config file) — this is exactly the A5 task, which
  is medium-risk and a separate Band-2 item. Not recommended for a "small, low-risk" C16.

- **Option C: literal hardcode (just add an `elif startswith("ani")` branch).**
  Smallest possible change, but it ignores the C16 Goal's "data-driven" requirement and
  leaves the same brittle prefix chain X1/X4 would have to rip out again. Not recommended.

**Plan below assumes Option A.**

### OD-2 — The `default` vs `movies` profile-key rename.

Today the movies profile key is `"default"` (an implementation-name), while the others are
content-named (`"tv"`). For a clean data-driven map, Option A renames the key to
`"movies"`.

- **Option A (DEFAULT): rename `"default"` → `"movies"`.** Internal-only — the key is never
  persisted to disk or shown to the user except in a log line; the on-disk *path*
  (`C:\Media\Utils\ChromeProfile`) is unchanged, so no profile directory is renamed and no
  re-login is needed for movies/series. `init_driver`'s `profile_key` default and its
  `.get(profile_key, CHROME_PROFILES["default"])` fallback are updated in lockstep.
- **Option B: keep `"default"` as the movies key.** Map becomes
  `[("ani","anime"),("tv","tv")]` with `DEFAULT_PROFILE = "default"`. Slightly less tidy but
  zero rename. Acceptable if you prefer minimal churn.

**Plan below assumes Option A (rename to `movies`), but every step works under B with the
key string swapped — call it out at confirmation if you prefer B.**

### OD-3 — New profile directory path + one-time manual login (operational, not code).

The new profile path is `C:\Media\Utils\ChromeProfile_Anime` (from the C16 task text). Like
the other two profiles, it must be **manually logged into the anime Google account once**
before any anime fetch can work. This is a human setup step, not something code can do.

- **Confirm:** is `C:\Media\Utils\ChromeProfile_Anime` the path you want, and will you do
  the one-time login? (The plan adds a verification step that the directory exists, and ties
  into IMP-C6's future session-detection so a logged-out anime profile fails loudly — but
  C6 is not in scope here, so for now a logged-out profile still produces 0 thumbnails. We
  will document that explicitly.)

### OD-4 — Scope: routing only, or also add a logged-out fast-fail?

IMP-C6 (session-expiry early detection) is a separate `pending` task. C16's job is purely
the routing fix.

- **Option A (DEFAULT): routing only.** Keep C16 small as the priority list intends; note
  in docs that a logged-out anime profile still silently yields 0 thumbnails until C6.
- **Option B: fold a minimal "is the anime profile dir present?" warning into C16.** Cheap,
  but starts blurring into C3/C6. Not recommended.

**Plan below assumes Option A.**

---

# Task: Route `ani-*` fetches to a dedicated anime Chrome profile via a data-driven prefix→profile map (IMP-C16)

Suggested branch: fix/anime_fetch_profile

(type `fix` — this corrects a latent routing bug; lowercase, under 50 chars.)

## Context

`mainfetch.cmd_fetch_route` routes both `tv-*` and `ani-*` to the single `tv` Chrome
profile (the series Google account), but the user now keeps anime in its **own** Google
account (three accounts total: movies/series/anime — ARCHITECTURE §6.2, IMP-X2 topology).
The first archived-anime restore will therefore search the wrong account, find 0
thumbnails, and fail silently like a session expiry. This task adds a third `anime` profile,
routes `ani-*` to it, and replaces the brittle `if startswith` chain with a small
data-driven prefix→profile map (the seam IMP-X1/X4 build on; IMP-A5 later sources it from
config).

## Goal

`fetch` / `fetch_restore` of any `ani-*` id drives the **anime** account's Chrome session
(`C:\Media\Utils\ChromeProfile_Anime`); `tv-*` still drives the series profile and movies
still drive the movies profile — all selected through a single data-driven map. Adding a
future account is a two-constant edit, not a logic edit. `python -m pytest -q` and
`python -m pytest tests/smoke -q` are green; the routing is covered by a new pure unit test.

## Files affected

- `mainfetch.py` — add the `anime` profile to `CHROME_PROFILES`; add the ordered
  `ID_PREFIX_PROFILE` map + `DEFAULT_PROFILE` constant + a pure `profile_for_id()` helper;
  rewrite the `cmd_fetch_route` selection block to call it; update `init_driver`'s default
  key + fallback to the renamed `movies` key (OD-2 Option A).
- `tests/test_anime_fetch_routing.py` (new) — pure unit tests for `profile_for_id` covering
  `ani-*` → `anime`, `tv-*` → `tv`, `mov-*`/other → `movies`, and that the map is the only
  routing authority (no Selenium, no library).
- `tests/smoke/test_smoke_all_commands.py` — extend the fetch smoke so an `ani-*` id is
  exercised through `cmd_fetch_route`'s profile-selection without launching a browser
  (assert the chosen profile, driver init stubbed). (Touches the smoke suite but is small.)
- `ARCHITECTURE.md` — update §3 repo-layout `Utils\` listing, §6.2/§8.1 profile routing
  prose, and the §8.6 `cmd_fetch_route` description to reflect three profiles + the
  data-driven map.
- `README.md` — update the "two persistent Chrome user-data directories" prerequisite to
  three (add `ChromeProfile_Anime` for anime).
- `improvements/improvements_tierC.md` — mark IMP-C16 `done` with the branch summary.
- `improvements/PRIORITY.md` — move C16 out of "suggested next" into DONE; bump Last
  updated; set a new 👉 NEXT pointer.
- `docs/priority-graph/priority-graph.html` — flip the `C16` node `s`/`p` to `done`; update
  the `⚡ Next` banner; refresh the node tooltip text.
- `docs/feature-anime-fetch-profile/PLAN.md` + `docs/feature-anime-fetch-profile/DECISIONS.md`
  — tracked copies of this plan + the recorded OD decisions.

## Approach

The selection logic moves from an inline `if manual_id.startswith(...)` chain to a small,
ordered, data-driven lookup. `CHROME_PROFILES` gains the `anime` entry (and the movies key
is renamed `default` → `movies`, OD-2). A module-level `ID_PREFIX_PROFILE` list of
`(prefix, profile_key)` pairs — ordered **longest/most-specific prefix first** so `ani`
matches before any shorter prefix and `mov`/`tv` are explicit — plus `DEFAULT_PROFILE`
drives a tiny pure function `profile_for_id(manual_id) -> profile_key`. `cmd_fetch_route`
calls `profile_for_id(manual_id)` and keeps the same user-facing log line shape (now naming
the resolved account). `init_driver` already takes a `profile_key` and looks it up in
`CHROME_PROFILES`; only its default arg and `.get(...)` fallback key change. Because routing
is now a pure function over plain data, it is unit-testable with zero Selenium and the
future IMP-A5 config simply populates the two constants. No library entry shape, field, ID
shape, or `status` value changes — so there is no shared-data-contract impact and no
consumer audit is required.

## Steps

- [ ] 1. [model: sonnet] [effort: medium] Add the `anime` profile + data-driven prefix→profile map and `profile_for_id()` helper in `mainfetch.py`.
  - Files: `mainfetch.py`
  - Details: In the CONFIGURATION block (currently `mainfetch.py:31-35`):
    (a) Rewrite `CHROME_PROFILES` to three content-named keys (OD-2 Option A):
    ```python
    CHROME_PROFILES = {
        "movies": r"C:\Media\Utils\ChromeProfile",
        "tv":     r"C:\Media\Utils\ChromeProfile_TV",
        "anime":  r"C:\Media\Utils\ChromeProfile_Anime",
    }
    ```
    The on-disk paths for `movies` and `tv` are byte-identical to the old `default`/`tv`
    paths — only the dict KEY for movies changed (`default` → `movies`). `anime` is the new
    `ChromeProfile_Anime` path from the C16 task text.
    (b) Add, directly under `CHROME_PROFILES`:
    ```python
    # Ordered id-prefix -> profile map. MOST-SPECIFIC prefix FIRST (so "ani" is
    # matched before any shorter/movies prefix). This is the data-driven seam IMP-X1/
    # IMP-X4 extend and IMP-A5 will source from mvconfig.json; today it is an in-module
    # constant (no config file yet). Add a new account by adding one tuple + one
    # CHROME_PROFILES entry — no logic change.
    ID_PREFIX_PROFILE = [("ani", "anime"), ("tv", "tv"), ("mov", "movies")]
    DEFAULT_PROFILE = "movies"
    ```
    (c) Add a pure helper (no I/O, no Selenium) near the config block or just above
    `cmd_fetch_route`:
    ```python
    def profile_for_id(manual_id):
        """Map a manual library id to its Chrome profile key via ID_PREFIX_PROFILE.
        Falls back to DEFAULT_PROFILE for unknown prefixes (e.g. legacy unprefixed ids).
        Pure: data-only, unit-testable without a browser."""
        for prefix, profile_key in ID_PREFIX_PROFILE:
            if manual_id.startswith(prefix):
                return profile_key
        return DEFAULT_PROFILE
    ```
    Keep `CHROME_PROFILE_NAME` and `SYSTEM_DOWNLOADS_FOLDER` unchanged. Do NOT change
    `load_library`/`calculate_file_hash` imports.
  - Acceptance: `python -c "import mainfetch; print(mainfetch.profile_for_id('ani-ja-2006-deathnote01'), mainfetch.profile_for_id('tv-en-2016-x-s01e01'), mainfetch.profile_for_id('mov-en-2025-f1'), mainfetch.profile_for_id('weird-legacy-id'))"` prints `anime tv movies movies`. `CHROME_PROFILES["anime"]` ends in `ChromeProfile_Anime`.

- [ ] 2. [model: sonnet] [effort: medium] Rewrite `cmd_fetch_route` profile selection to use `profile_for_id`, and fix `init_driver`'s default/fallback key.
  - Files: `mainfetch.py`
  - Details:
    (a) In `cmd_fetch_route` (`mainfetch.py:438-453`), replace the hardcoded block:
    ```python
    active_profile = "default"
    if manual_id.startswith("tv") or manual_id.startswith("ani"):
        print("   > 📺 TV Series detected: Using 'ChromeProfile_TV'")
        active_profile = "tv"
    else:
        print("   > 🎬 Movie (or other) detected: Using Default Profile")
    ```
    with:
    ```python
    active_profile = profile_for_id(manual_id)
    print(f"   > 🗂️  Account/profile for {manual_id}: '{active_profile}' "
          f"({CHROME_PROFILES.get(active_profile, '?')})")
    ```
    Keep the rest of `cmd_fetch_route` (targets resolve, `init_driver(active_profile)`,
    the loop, try/except/finally) byte-identical.
    (b) In `init_driver` (`mainfetch.py:42-45`), change the signature default and the
    fallback key from `default` to `movies`:
    ```python
    def init_driver(profile_key="movies"):
        ...
        user_data_dir = CHROME_PROFILES.get(profile_key, CHROME_PROFILES["movies"])
    ```
    This is the ONLY place that referenced the old `"default"` key — grep `mainfetch.py`
    for the literal `"default"` after editing to confirm none remain referring to the
    profile dict (note: `CHROME_PROFILE_NAME = "Default"` is the Chrome `--profile-directory`
    value and is UNRELATED — leave it).
  - Acceptance: grep shows no remaining `CHROME_PROFILES["default"]` / `profile_key="default"`. `cmd_fetch_route` calls `profile_for_id(manual_id)`. Manual trace: an `ani-*` id resolves `active_profile == "anime"` (assert in step 4's smoke without launching Chrome).

- [ ] 3. [model: sonnet] [effort: medium] Add a pure unit test file for the routing map.
  - Files: `tests/test_anime_fetch_routing.py` (new)
  - Details: New test module, no fixtures needed (pure function — follows the
    `TestParseFetchArgs` precedent in `tests/test_cli_parsers.py`). Import `mainfetch`.
    Cover:
    - `profile_for_id("ani-ja-2006-deathnote01") == "anime"` (and a half-ep
      `ani-ja-2012-kurokosbasketball-s0325.5`).
    - `profile_for_id("tv-en-2016-strangerthings-s01e03") == "tv"` and the no-`s` Chernobyl
      shape `tv-en-2019-chernobyle01`.
    - `profile_for_id("mov-en-2025-f1") == "movies"`.
    - A legacy/unprefixed id (e.g. `"weird-legacy-id"`, `""`) → `DEFAULT_PROFILE` (`movies`).
    - Regression guard for the bug: `profile_for_id("ani-...") != profile_for_id("tv-...")`
      (anime and series must NOT share a profile — this is the exact assertion that would
      have caught the bug).
    - `CHROME_PROFILES` has all three keys and the `anime` path ends with
      `ChromeProfile_Anime`; every value in `ID_PREFIX_PROFILE` is a key in
      `CHROME_PROFILES`, and `DEFAULT_PROFILE` is a key in `CHROME_PROFILES` (map-integrity
      guard so a future typo can't desync the two constants).
    Constraints (include in the test module docstring): "Never touch real C:\\Media files or
    real library_*.json." and "Run `python -m pytest tests/test_anime_fetch_routing.py -q`
    and fix failures before marking the step done." (Pure function — no library/device I/O,
    so no sandbox fixture needed.)
  - Acceptance: `python -m pytest tests/test_anime_fetch_routing.py -q` passes (all cases green).

- [ ] 4. [model: sonnet] [effort: medium] Extend the fetch smoke so an `ani-*` id exercises profile selection without a browser.
  - Files: `tests/smoke/test_smoke_all_commands.py`
  - Details: Add ONE smoke test (in `TestEachCommand`) that calls
    `mainfetch.cmd_fetch_route("ani-ja-2006-deathnote01")` (or `fetch_restore` of an anime
    id) with `mainfetch.init_driver` monkeypatched to return `None` (so no Chrome launches —
    `cmd_fetch_route` returns cleanly when `init_driver` returns None, see `mainfetch.py:460`),
    capture stdout, and assert the chosen profile is `anime` (assert the log line contains
    `'anime'` and `ChromeProfile_Anime`). Seed a minimal anime entry via the existing
    `sandbox` + `make_video` fixtures (an `ani-*` leaf, written through `save_library` like
    `_seed_single` does for `tv-*`) so `resolve_targets` finds a target before `init_driver`
    is reached — OR, simpler and preferred, monkeypatch `init_driver` to return None which
    short-circuits BEFORE target iteration matters; pick whichever keeps the smoke under a
    second. Reuse `monkeypatch.setattr(mainfetch, "init_driver", lambda *_a, **_k: None)`.
    Do NOT add a real anime-account network path. Follow the file's existing anti-patterns
    note (capsys for stdout; never touch real C:\\Media; never assert absolute device paths).
    Constraints (the suite already enforces these; restate in the test's docstring):
    "Never touch real C:\\Media files or real library_*.json." and "Run
    `python -m pytest tests/smoke -q` and fix failures before marking the step done."
  - Acceptance: `python -m pytest tests/smoke -q` passes including the new anime-routing
    smoke; the new test asserts `cmd_fetch_route` selected the `anime` profile for an
    `ani-*` id with no browser launched. The alias sweep and all other smokes stay green.

- [ ] 5. [model: sonnet] [effort: low] Architect doc update — ARCHITECTURE.md + README.md.
  - Files: `ARCHITECTURE.md`, `README.md`
  - Details:
    - `ARCHITECTURE.md` §3 repo-layout `C:\Media\Utils\` block (lines ~174-177): add
      `ChromeProfile_Anime\  Selenium-attached Chrome user data dir for anime` and update the
      `ChromeProfile_TV` comment from "TV + anime" to "TV (series account)".
    - `ARCHITECTURE.md` §6.2 "0 anime entries have split_info … never run against the TV
      Chrome profile" note: add a sentence that anime now routes to its OWN `anime` profile
      (the C16 fix) so the first anime chunk-restore drives the anime account, not the TV one.
    - `ARCHITECTURE.md` §8.1 (lines ~1112-1131): replace the two-profile `CHROME_PROFILES`
      snippet + the `if startswith("tv") or startswith("ani")` routing snippet with the
      three-profile dict and the data-driven `ID_PREFIX_PROFILE` / `profile_for_id` map;
      note the keys are now content-named (`movies`/`tv`/`anime`) and that IMP-A5 will source
      these constants from `mvconfig.json`.
    - `ARCHITECTURE.md` §8.6 `cmd_fetch_route` bullet: note it now calls `profile_for_id`.
    - `README.md` prerequisites (lines ~99-104): change "Two persistent Chrome user-data
      directories" to "Three", and add `ChromeProfile_Anime` — signed into the Google account
      that holds your anime; change the `ChromeProfile_TV` line to "TV series" (not "and
      anime"). Note each of the three must be signed in manually once.
    - Keep line-number references illustrative; prefer grep-by-name. Do not touch any
      rollback/change-gate prose (this change does not affect rollback).
  - Acceptance: ARCHITECTURE.md and README.md describe three Chrome profiles and the
    data-driven prefix→profile map; no stale "tv profile handles anime" text remains
    (grep `ChromeProfile_TV` / "TV + anime" / "TV/anime" to confirm). `python -m pytest -q`
    still green (docs-only).

- [ ] 6. [model: haiku] [effort: low] Mark IMP-C16 done in the tier file.
  - Files: `improvements/improvements_tierC.md`
  - Details: In the IMP-C16 block (lines ~290-305), change `- Status: pending` to:
    `- Status: done (fix/anime_fetch_profile — added 3rd Chrome profile `anime` →
    `ChromeProfile_Anime`; id-prefix→profile routing extracted to data-driven
    `mainfetch.ID_PREFIX_PROFILE` + pure `profile_for_id()`; `ani-*` now drives the anime
    account, `tv-*` series, movies movies; external-config sourcing deferred to IMP-A5; unit
    tests in tests/test_anime_fetch_routing.py + an anime-routing smoke)`. Do not edit any
    other task. Match the existing done-status wording style of C12–C15.
  - Acceptance: IMP-C16 shows `Status: done` with the branch + summary; no other task block
    changed (git diff shows only the C16 status line region).

- [ ] 7. [model: haiku] [effort: low] Update PRIORITY.md ordering + NEXT pointer.
  - Files: `improvements/PRIORITY.md`
  - Details:
    - Bump `**Last updated:**` (line 12) to `2026-06-14 (IMP-C16 done — fix/anime_fetch_profile).`
    - Rewrite the `👉 SUGGESTED NEXT TASK` block (lines 16-22): C16 is now done; set the new
      pointer. Per the band ordering, Band 1's next code-first item after C16 is
      **IMP-A10** (truth-up `requirements.txt` — a clean install is half-broken; missing
      `requests`/`webdriver-manager`). State that succinctly as the new 👉 NEXT, and keep the
      reminder that the two 🚦 R6/R7 decisions still await a user decision (no code first).
    - Remove the C16 row (line 41) from BAND 1's table (it is now done) and move C16 into the
      `## ✅ DONE` list (line ~84-88): bump the count `(16)` → `(17)` and add `C16 (anime
      fetch profile)` to the inline done list next to `C15`.
    - Do NOT change any other band/row.
  - Acceptance: PRIORITY.md no longer lists C16 as pending/next; C16 is in the DONE list
    with the count incremented; the NEXT pointer names IMP-A10 (with the R6/R7 decision note
    retained); Last updated bumped.

- [ ] 8. [model: haiku] [effort: low] Update the priority-graph HTML (node + banner).
  - Files: `docs/priority-graph/priority-graph.html`
  - Details: Three edits (the maintenance protocol's step 3):
    (a) Line 84 banner: change `⚡ Next: <b>IMP-C16</b> … anime fetch profile routing` to
    `⚡ Next: <b>IMP-A10</b>` with label `truth-up requirements.txt` (matching the new NEXT
    pointer from step 7).
    (b) Line 160 `C16` node tuple: change the 4th and 5th array fields (priority `"high"`,
    status `"todo"`) to `"done","done"`, and replace the tooltip text (6th field) with
    `"Fixed fix/anime_fetch_profile: 3rd Chrome profile anime->ChromeProfile_Anime; data-driven mainfetch.ID_PREFIX_PROFILE + profile_for_id(); ani-* routes to anime account (was series); external config deferred to IMP-A5"`.
    Use the exact same tuple shape as the C15 line (159) — `["C16","anime fetch profile","C","done","done","..."]`.
    (c) Leave the `["C16","X1"]` and `["C16","S3"]` edges (line 252) intact — those
    dependencies still hold (X1/S3 build on the now-shipped fetch-routing seam).
  - Acceptance: open the HTML / grep confirms the `C16` node is `"done","done"`, the banner
    points at IMP-A10, and the C16→X1 / C16→S3 edges are unchanged. (No runtime test; visual
    + grep check.)

- [ ] 9. [model: haiku] [effort: low] Write the tracked feature docs (PLAN + DECISIONS).
  - Files: `docs/feature-anime-fetch-profile/PLAN.md`, `docs/feature-anime-fetch-profile/DECISIONS.md`
  - Details: Copy this finalized `/PLAN.md` verbatim to
    `docs/feature-anime-fetch-profile/PLAN.md` (the tracked canonical copy). Create
    `DECISIONS.md` recording the resolved OD-1…OD-4 choices (with the option chosen and a
    one-line rationale each), mirroring the style of
    `docs/feature-micro-robustness-c15/DECISIONS.md`. These are the artifacts the git-agent
    commits (root `/PLAN.md` stays gitignored). Do this AFTER the user confirms the Open
    Decisions so the recorded decisions are final.
  - Acceptance: both files exist under `docs/feature-anime-fetch-profile/`; PLAN.md matches
    root `/PLAN.md`; DECISIONS.md lists the four OD outcomes.

## Risks and edge cases

- **No shared-data-contract change.** This task changes only mainfetch's *routing* logic; it
  does not add/rename/remove any library entry type, field, ID shape, or `status` value.
  Therefore no `## Consumer Impact Analysis` table is required, and `ENTRY_TYPE_KEYS` /
  `tests/test_entry_schema_guard.py` are untouched and must stay green (the smoke gate + full
  suite confirm).
- **Auto-rollback is NOT touched.** `mainfetch.py` carries no rollback machinery (the
  journal/PONR mechanism lives entirely in `main.py`, ARCHITECTURE §12a). This change does
  not alter any PONR, journal, or `RollbackHardFail` behavior — no change-gate decision is
  needed. (Flagged here explicitly because CLAUDE.md requires surfacing any rollback-adjacent
  work; this is confirmed NOT rollback-adjacent.)
- **Latent-bug verification limit.** Because 0/140 anime leaves have `split_info` and no
  anime has been chunk-restored, there is no live data to end-to-end verify the anime-account
  fetch actually downloads. The unit + smoke tests verify *routing* (the right profile is
  chosen); the real network round-trip can only be confirmed manually once the
  `ChromeProfile_Anime` is logged in (see Manual test commands). State this in the PR.
- **Logged-out anime profile still fails silently (until IMP-C6).** Until session-expiry
  detection (C6) ships, a not-yet-logged-in anime profile still yields 0 thumbnails with the
  old confusing symptom. C16 fixes *which account* is searched; it does not add fast-fail.
  Documented in DECISIONS (OD-4) and the PR body.
- **Prefix-order correctness.** `ID_PREFIX_PROFILE` must list `ani` before any broader
  prefix; since `mov`/`tv`/`ani` are mutually non-overlapping there is no real ambiguity, but
  the ordered-list + explicit-prefix design keeps it robust if a future prefix overlaps.
  Covered by the step-3 ordering/integrity tests.
- **`"default"` literal collision.** `CHROME_PROFILE_NAME = "Default"` (the Chrome
  `--profile-directory` arg) is unrelated to the renamed map key and must be left alone — the
  step-2 grep guard explicitly distinguishes them.
- **IMP-A5 not done.** The map is in-module, not config-file-driven, by design (OD-1
  Option A). If the user picks Option B, the blast radius and step list change materially
  (this becomes an A5-shaped task) — that is why it is an Open Decision, not a silent choice.

## Verification

Run from the repo root (`C:\Users\harin\PycharmProjects\MediaVault`), using
`python -m pytest` (NOT bare `pytest`, per the project memory note):

1. `python -c "import mainfetch; print(mainfetch.profile_for_id('ani-x'), mainfetch.profile_for_id('tv-x'), mainfetch.profile_for_id('mov-x'), mainfetch.profile_for_id('legacy'))"`
   → expect `anime tv movies movies`.
2. `python -m pytest tests/test_anime_fetch_routing.py -q` → new routing unit tests green.
3. `python -m pytest tests/test_cli_parsers.py -q` → the existing `parse_fetch_args` tests
   (same file family) still green (no regression in mainfetch).
4. `python -m pytest tests/test_entry_schema_guard.py -q` → entry-schema guard unaffected.
5. `python -m pytest -q` → full suite green.
6. `python -m pytest tests/smoke -q` → **FINAL GATE** (mandatory because this change touches
   `mainfetch.py`): every command + the alias sweep + the new anime-routing smoke pass.

## Out of scope

- IMP-A5 external `mvconfig.json` wiring (the map stays an in-module constant; A5 will source
  it later). [Unless OD-1 Option B is chosen.]
- IMP-C6 session-expiry fast-fail for a logged-out anime profile (separate task).
- IMP-X1 multi-account replication / IMP-X4 cross-account fetch fallback (this only lays the
  routing seam; no `replicas` schema, no backup-account routing here).
- Any change to `main.py`, `mvcommon.py`, the library entry schemas, `ENTRY_TYPE_KEYS`, or
  the auto-rollback mechanism.
- Renaming the on-disk profile *directories* for movies/series (only the in-memory dict KEY
  for movies changes; no profile re-login for movies/series).
- Backfilling routing for any non-`mov`/`tv`/`ani` legacy ids beyond the default fallback.

## Manual test commands (after merge + one-time anime-profile login)

These require the real anime Google account + `C:\Media\Utils\ChromeProfile_Anime` logged in
(OD-3), so they are POST-merge human checks, not CI:

1. **One-time setup:** launch Chrome once with the anime profile and log into the anime
   Google account:
   `"C:\Program Files\Google\Chrome\Application\chrome.exe" --user-data-dir="C:\Media\Utils\ChromeProfile_Anime" --profile-directory=Default`
   → sign into the anime account, confirm Photos loads, close it.
2. **Routing sanity (no network needed):**
   `python -c "import mainfetch; print(mainfetch.profile_for_id('ani-ja-2006-deathnote01'))"`
   → `anime`.
3. **Real anime fetch (network):** pick a small archived anime episode id and run
   `python mainfetch.py fetch ani-ja-2006-deathnote episodes 1-1`
   → the log shows `Account/profile … 'anime' (C:\Media\Utils\ChromeProfile_Anime)`, Chrome
   opens on the anime account, the thumbnail is found, and the file lands in the entry's
   `restore/` folder (contrast: before the fix this searched the series account and found 0).
4. **Full anime restore round-trip:**
   `python main.py fetch_restore ani-ja-2006-deathnote episodes 1-1`
   → episode downloads from the anime account and restores into place
   (`status` → `restored_local`).
5. **Regression — movies/series still route correctly:**
   `python mainfetch.py fetch mov-en-2025-f1` (movies profile) and
   `python mainfetch.py fetch tv-en-2016-strangerthings episodes 1-1` (series profile) — each
   names the right account in its log line.

## PR

- Title: `fix: route ani-* fetches to a dedicated anime Chrome profile — IMP-C16`
- Body order (per `docs/git-pr-conventions.md`): auto-generated Claude Code summary FIRST,
  then `## Original task prompt` with the complete verbatim task prompt, then the
  `🤖 Generated with Claude Code` trailer. PR title MUST include `IMP-C16`.
- **Merging into `main` is human-gated** — create the PR and STOP; do not merge. Commit the
  `docs/feature-anime-fetch-profile/` artifacts on the branch (root `/PLAN.md` stays
  gitignored).
