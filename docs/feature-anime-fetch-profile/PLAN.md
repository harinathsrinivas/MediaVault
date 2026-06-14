# IMP-C16 — Route `ani-*` fetches to a dedicated anime Chrome profile
# Status: COMPLETE (all 10 steps done, branch fix/anime_fetch_profile)

## Resolved Open Decisions

| # | Decision | Choice |
|---|----------|--------|
| OD-1 | How data-driven? | **Option A** — in-module `ID_PREFIX_PROFILE` list + `profile_for_id()` now; IMP-A5 wires to `mvconfig.json` later |
| OD-2 | Rename `"default"` key? | **Option A** — rename to `"movies"`; on-disk path unchanged, no re-login for movies/series |
| OD-3 | Profile path + manual login | **Confirmed** — `C:\Media\Utils\ChromeProfile_Anime` (folder created); user did one-time login |
| OD-4 | Test scope | **Live end-to-end** — Chrome opened on each of the 3 profiles, Selenium attached, routing verified live |

## Bug Summary

`mainfetch.py` had only two Chrome profiles (`"default"` → movies, `"tv"` → series). The
routing block folded both `tv-*` and `ani-*` into the `tv`/series profile. But anime files
live in a **third** separate Google account. Any anime fetch searched the series account,
found 0 thumbnails, and failed silently — identical to a session-expiry symptom (IMP-C6).
Every archived anime entry (108 of 140 leaves) was un-restorable, and the failure was
misdiagnosable.

**The fix:** added a third `anime` profile key → `C:\Media\Utils\ChromeProfile_Anime`,
replaced the brittle `if startswith` chain with a data-driven `ID_PREFIX_PROFILE` list +
pure `profile_for_id()` function, routing `ani-*` → anime account.

## Branch and PR

- **Branch:** `fix/anime_fetch_profile`
- **PR title:** `fix: route ani-* fetches to a dedicated anime Chrome profile — IMP-C16`

## Files Changed

- `mainfetch.py` — CHROME_PROFILES (3 keys: movies/tv/anime), ID_PREFIX_PROFILE, DEFAULT_PROFILE, profile_for_id(), cmd_fetch_route uses profile_for_id(), init_driver default → "movies"
- `tests/test_anime_fetch_routing.py` (new) — 12 pure unit tests incl. regression guard
- `tests/smoke/test_smoke_all_commands.py` — test_anime_fetch_routing_profile_selection added
- `ARCHITECTURE.md` — §3 Utils, §8.1 config snippet + routing prose, §8.6 cmd_fetch_route
- `README.md` — prerequisites: Two → Three Chrome profiles, added ChromeProfile_Anime
- `improvements/improvements_tierC.md` — IMP-C16 marked done
- `improvements/PRIORITY.md` — NEXT → IMP-A10; C16 moved to DONE (count 16→17)
- `docs/priority-graph/priority-graph.html` — banner → A10; C16 node → done/done

## Completed Steps

- [x] 1. Add anime profile + data-driven map + profile_for_id() — commit 693f8f4
- [x] 2. Rewrite cmd_fetch_route + fix init_driver default key — commit 97bea2c
- [x] 3. Pure unit tests (tests/test_anime_fetch_routing.py, 12 tests) — commit caf8e47
- [x] 4. Smoke test for anime routing (51/51 green) — commit 1387156
- [x] 4b. ASCII-safe log line (emoji cp1252 fix) — commit bbe6248
- [x] 5. Live end-to-end verification — all 3 Chrome profiles opened correct accounts
- [x] 6. ARCHITECTURE.md + README.md updated — commit cf0890c
- [x] 7. IMP-C16 marked done in improvements_tierC.md — commit cf0890c
- [x] 8. PRIORITY.md updated (next=A10, C16 in done list) — commit cf0890c
- [x] 9. Priority-graph HTML updated (C16 node done, banner→A10) — commit cf0890c

## Verification Results

- `python -m pytest tests/test_anime_fetch_routing.py -q` → 12 passed
- `python -m pytest tests/test_cli_parsers.py -q` → 21 passed
- `python -m pytest tests/test_entry_schema_guard.py -q` → passed
- `python -m pytest -q` → full suite green
- `python -m pytest tests/smoke -q` → 51 passed
- Live Chrome test: anime → ChromeProfile_Anime, tv → ChromeProfile_TV, movies → ChromeProfile (all Selenium-attached, all closed cleanly)

## Next Task

**IMP-A10** — truth-up `requirements.txt` (missing `requests`/`webdriver-manager`)
