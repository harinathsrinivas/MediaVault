# Decision Record: Episode-number extraction for dotted-title filenames

## Point 1 — Open Decision 1 RESOLVED

**Context:** How to handle fractional episodes in `SxxExx` filenames (the disambiguation strategy).

**Decision:** The user confirmed: *"Yes I have .5 use-cases in anime for now. I've not seen it in movies or series for now."*

**Implication:** `.5` fractional episodes exist **only in anime**, never in series or movies. The `SxxExx` branch (used for series/movies) can safely drop the decimal capture entirely (Option A: `[sS]\d+[eE](\d+(?:\.\d+)?)` → `[sS]\d+[eE](\d+)`). The `NxYY` branch (`main.py:891`) keeps `(?:\.\d+)?` so anime `.5` continues to parse correctly.

**Options Considered and Rejected:**
- Option B (lookahead guard on `SxxExx`): unnecessary complexity; user confirmed no series/movie `.5` exists.
- Option C (filter-side floor on range logic): would affect core business logic (change-gated); not taken.

**Result:** `SxxExx` drops the decimal capture; `NxYY` and absolute-numbering anime branches keep theirs.

---

## Point 2 — Why the NxYY line is unchanged

**Context:** Should the untouched `NxYY` regex (`\d+[xX](\d+(?:\.\d+)?)`, `main.py:835`) also be modified to prevent the dotted-title ambiguity?

**Decision:** No. The `NxYY` line is intentionally left as-is.

**Reason:** Anime `.5` half-episodes are real and must keep working. The `NxYY` convention is compact (`Show 16x05`, `Show 16x05.5`) — it does not carry dotted release-title tokens after the episode number the way scene-named REMUX files do. Therefore, the greedy-decimal ambiguity that broke `SxxExx` does not arise in practice for `NxYY` filenames. Keeping the existing capture is the simplest correct choice and preserves anime `.5` parsing with zero risk to current entries.

If a future dotted-title anime `NxYY` filename is discovered that mis-parses, that will be surfaced as an explicit decision at that time, rather than silently modified now.

---

## Point 3 — Why `search_term` needs no change

**Context:** Does fixing the library ID require a separate patch to the `search_term` generation or Google Photos search logic?

**Decision:** No. `search_term` needs no change.

**Reason:** `search_term` is built from the **raw filename**, not from the ID (`main.py:700-702`). It is independent of the library ID's episode segment. Both `cmd_push` (`write_remote_mvmeta`/upload) and `mainfetch` (`resolve_targets`, `build_download_queue`, `mainfetch.py:378-408`) search Google Photos by `entry["search_term"]` / `entry["filename"]`, never by the ID's episode segment. Fixing the ID alone is sufficient; no search-logic change is required.

---

## Point 4 — Stale `...e20.6` entries from prior runs

**Context:** If the user ran `prep` before this fix, a stale `...e20.6` key may exist in `library_series.json` (e.g., as a season-map child under `S03` → `episodes`). Should this be cleaned up?

**Decision:** No cleanup is performed here; this is out of scope.

**Reason:** The correct long-term tool for library repair is the planned `repair_library` command (IMP-D5). Cleaning up one-off bad entries as part of this fix would be a special case and would not address the general problem.

**Workaround for the user:** If a stale `...e20.6` season-map child exists in `library_series.json` from a prior run, manually remove it. Navigate to the JSON, find the season entry (e.g., `tv-en-2010-fringe-s03` → `"episodes"` → `"20"` → `"children"`), and delete the `"...e20.6"` child entry if present. The next `prep` run will create the correct `...e20` entry.

---

## Summary

This fix is purely regex-surgical: the `SxxExx` episode-extraction pattern in `cmd_prep_season` (`main.py:834`) drops the optional decimal group so dotted-title filenames like `Fringe.S03E20.6.02.AM.EST...` no longer mis-capture `.6` as a fractional episode. All other code paths (NxYY anime branches, search logic, range filters, rollback journal) remain unchanged and continue to work correctly.
