# Decision Card — Step 1.1 (IMP-E14): `items_payload()` builder in `main.py`

> **This is a RANK + RECOMMEND card. Nothing merges until YOU choose.** I evaluated three blinded candidates (A/B/C), independently re-ran tests, and probed each with a constructed library. The final call is yours — see "👉 Your choice" at the bottom.

## 1. Step

- **ID / title:** Step 1.1 (IMP-E14) — Add a read-only, library-centric, season-aware, alias/season_map-safe `items_payload()` returning `{"items":[...], "by_category":{...}}`. Each row carries `id, category (movies|series|anime|other), state, size_bytes, path, title, year, tmdb_id, poster_available, chunk_count` (+ `parent_id` when present). MUST include ARCHIVED rows (unlike `/api/reclaim`). The plan lists FIVE `state` values: `UNPREPPED | LOCAL_NOT_PUSHED | PUSHED_NOT_ARCHIVED | RESTORED_REPLACE_AGAIN | ARCHIVED`, and the UI nests an "Unprepped" sub-view per media tab.
- **Judge criteria:**
  1. Correctness + alias/season_map-safety (no KeyError on season_map/multi_ep_alias; no virtual rows; ARCHIVED included; four reclaim states match `classify_entry_state`) — **plus explicit UNPREPPED handling**.
  2. Read-only purity (no mutation, no `save_library`).
  3. Reuse vs duplication of classify/category logic.
  4. Payload-shape ergonomics for the SPA (flat, JSON-serializable, season context, all sub-views renderable).

---

## 🚩 Headline finding (corrects the brief's stated differentiator)

The brief says "A surfaces UNPREPPED; B and C do not." **My independent probe disproves this for A.** I seeded a library plus a real (>200KB) on-disk file unknown to the library (`stranger.mkv`) and ran all three:

| | unknown-on-disk file (`stranger.mkv`) emitted? |
|---|---|
| A | **No** |
| B | No |
| C | No |

**NONE of the three actually surface UNPREPPED.** A's CRITIQUE claims "INCLUDES UNPREPPED via a disk walk," but A's PASS 1 does `found = known_paths.get(norm_key); if found is None: continue` (`A/main.py:3443`) — an unknown file is skipped, and PASS 2 only iterates library entries. So A pays the full cost of a disk walk (`os.walk` over three roots, +184 lines) **and still does not produce a single UNPREPPED row.** A's `_resolve_state` UNPREPPED branch is dead code for emission purposes. This materially changes the trade-off: A's extra complexity buys nothing the others lack on the UNPREPPED axis.

The real, verified output difference is elsewhere: **C is the only candidate that surfaces a library leaf whose file is absent on disk** (`mov-gone`, status `onboarded`, file deleted) — C emits it as `state: "ONBOARDED"` via a status fallback. A and B drop it.

**Implication for the UI's "Unprepped" sub-view:** with ALL THREE, the per-tab Unprepped sub-view cannot be populated from `/api/items` — it needs a second data source (`/api/reclaim`, which does walk disk and invents UNPREPPED ids via `guess_manual_id`). This is acceptable and arguably correct: an UNPREPPED file has no library id/category/metadata, so it cannot be a clean media-type row. The SPA wiring this Unprepped sub-view to `/api/reclaim` is a known, small follow-up regardless of which candidate wins. **Do not pick A on the belief that it gives you UNPREPPED for free — it does not.**

---

## 2. Per candidate

### Candidate A — disk-anchored two-pass (branch `__cand_a`, 184 ins.)
- **Approach:** Mirrors `collect_reclaimable` structure. PASS 1 walks the three category roots and emits rows only for files that match a known library leaf; PASS 2 sweeps the library for leaves the walk missed (the new ARCHIVED path). Adds `_LIFECYCLE_STATE_BY_STATUS` constant + `_resolve_state` fallback + local `_items_category_of`.
- **Files / diff:** `main.py` only, **+184 / -0**. New code only; no existing function touched.
- **My independent tests:** import OK; `test_web_datafns.py` + `test_web_endpoints.py` = **41 passed** (unchanged); probe — 5 rows, ARCHIVED (mov-c, ani-f) included, no virtual leak, read-only confirmed, JSON OK; **unknown file NOT emitted; absent-file leaf NOT emitted.**
- **Self-critique highlights:** Honest that `_resolve_state` fallback is reasoning/grep-verified not asserted; notes the two-pass design is "heavier than a single library pass." Claims UNPREPPED inclusion — **not borne out by my probe.**
- **Pros/cons vs criteria:**
  - (1) Correctness/alias-safety: ✓ states correct, alias-safe (skip before deref, `A/main.py:3372,3459`), ARCHIVED ✓. UNPREPPED: ✗ not actually emitted despite the claim. Drops absent-file leaves.
  - (2) Read-only: ✓ single `load_library()`, OSError-guarded, verified byte-identical library.
  - (3) Reuse: △ reuses `classify_entry_state` but ADDS a parallel `_LIFECYCLE_STATE_BY_STATUS` map + `_resolve_state` — a second state-derivation surface that can drift from `classify_entry_state`.
  - (4) Ergonomics: ✓ correct shape, flat, JSON-serializable, `parent_id` conditional.
- **Risks:** Largest line count for no functional gain over B; the two state sources (`classify_entry_state` + the status map) are a latent drift hazard; disk walk re-stats and adds runtime cost with zero payoff here.
- **Sample row (from CRITIQUE):** `{"id":"tv-en-2017-show-s01e01","category":"series","state":"RESTORED_REPLACE_AGAIN","chunk_count":3,"parent_id":"tv-en-2017-show-s01"}`

### Candidate B — library-anchored single pass (branch `__cand_b`, 133 ins.)
- **Approach:** Iterate `load_library()` once, skip virtual types up front, stat each leaf, feed `on_disk_real` into `classify_entry_state` verbatim, emit iff state is non-None (so ARCHIVED is kept; `None`-state leaves are dropped). Local `_category_of_id`.
- **Files / diff:** `main.py` only, **+133 / -0**. Smallest, new code only.
- **My independent tests:** import OK; reclaim suites **41 passed**; probe — 5 rows, ARCHIVED included, no virtual leak, read-only ✓, JSON OK; unknown file NOT emitted; absent-file leaf NOT emitted (B sets `size=0` then `classify_entry_state`→None for onboarded→dropped).
- **Self-critique highlights:** Clear, honest about excluding UNPREPPED and about dropping disk-anomaly/`None`-state leaves; ran the widest regression set (101 passed incl. endpoints + schema guard + smoke).
- **Pros/cons vs criteria:**
  - (1) Correctness/alias-safety: ✓ states correct, alias-safe (`B/main.py:3378`), ARCHIVED ✓. UNPREPPED: ✗ (documented). Drops `None`-state/absent leaves → a leaf in a transient state momentarily vanishes from its tab.
  - (2) Read-only: ✓ proven by mtime snapshot.
  - (3) Reuse: ✓✓ **zero duplicated classification** — `state = classify_entry_state(entry, on_disk_real)` verbatim, the single source of truth. (Only `_category_of_id` is a 4-line mirror, same as A/C.)
  - (4) Ergonomics: ✓ correct shape, `by_category` pre-counted.
- **Risks:** Dropping `None`-state leaves means a leaf that is out-of-sync (file gone, or real file under archived status) is invisible in the UI — for a *library inventory* that powers per-tab views, a silently-missing item is a worse failure mode than showing it with a best-effort state.
- **Sample (from CRITIQUE):** `items: 5 by_category: {'movies':3,'series':1,'anime':1,'other':0}` / states `['ARCHIVED','LOCAL_NOT_PUSHED','PUSHED_NOT_ARCHIVED','RESTORED_REPLACE_AGAIN']`

### Candidate C — extracted shared `_classify_item` + reclaim refactor (branch `__cand_c`, 160 ins / 16 del)
- **Approach:** Factor per-leaf classification into one read-only helper `_classify_item(mid, entry)` (`C/main.py:3185`) returning a neutral fact bundle (`state`, `file_present`, `size_bytes`, `on_disk_real`, `norm_key`, …) or `None` for virtual/no-file. `items_payload` is built on it AND `collect_reclaimable`'s Pass 2 is refactored onto the SAME helper. Adds `category_of_id`. `state` falls back to `status.upper()` when `classify_entry_state` returns None, so EVERY physical leaf (incl. absent-file) carries a state.
- **Files / diff:** `main.py` only, **+160 / -16** — the only candidate touching existing code (Pass 2 of `collect_reclaimable`). Pass 1 (disk walk) untouched.
- **My independent tests (the critical one for C):** `test_web_datafns.py` + `test_web_endpoints.py` = **41 passed** — reclaim is byte-identical after the refactor (C's whole premise holds). Diff inspection confirms only Pass 2 rerouted, Pass 1 intact. Probe — **6 rows**: ARCHIVED included, no virtual leak, read-only ✓, JSON OK; **absent-file leaf `mov-gone` emitted as `ONBOARDED`** (only C surfaces it); unknown-on-disk file still NOT emitted (correctly — no UNPREPPED).
- **Self-critique highlights:** Most thorough; correctly identifies that only Pass 2 is the shared computation and deliberately leaves Pass 1 (disk-anchored, UNPREPPED-inventing) out of the shared seam; flags the `entry` passthrough field as one field wider than today's callers need; honest that this is the largest blast radius.
- **Pros/cons vs criteria:**
  - (1) Correctness/alias-safety: ✓ states correct, alias-safe by construction in the shared helper (`C/main.py:3214`), ARCHIVED ✓. UNPREPPED: ✗ (correctly reserved for unknown files). **Most complete physical-leaf coverage** — absent-file leaves still surface (status fallback) rather than vanishing.
  - (2) Read-only: ✓ helper + both callers only `load_library` + `os.path.getsize`.
  - (3) Reuse: ✓✓✓ **true single source of truth** — `_classify_item` is the ONLY place mapping entry+disk→state for both endpoints; they provably cannot drift. Best of the three on this axis.
  - (4) Ergonomics: ✓ correct shape; never-null `state` string is friendlier for the SPA than a dropped row.
- **Risks:** Largest blast radius — it edits the load-bearing reclaim path. Mitigated and proven (41 passed, Pass 1 untouched, surgical Pass 2 swap), but per CLAUDE.md the reclaim/`collect_reclaimable` area is sensitive, so a human should eyeball the Pass 2 diff. The `status.upper()` fallback (`ONBOARDED`, `UNKNOWN`) emits state strings OUTSIDE the documented 5-value enum — a deliberate, documented ergonomics choice the SPA must tolerate.
- **Sample (from probe):** `{"id":"mov-gone","category":"movies","state":"ONBOARDED","size_bytes":0,...}` alongside the four reclaim states + ARCHIVED.

---

## 3. Comparison table

| Criterion | A | B | C |
|---|---|---|---|
| 1. Correctness: 4 reclaim states match `classify_entry_state` | ✓ | ✓ | ✓ |
| 1. Alias/season_map-safe (no KeyError, no virtual rows) | ✓ | ✓ | ✓ |
| 1. ARCHIVED included | ✓ | ✓ | ✓ |
| **UNPREPPED actually surfaced?** | ✗ (claimed, but NOT emitted) | ✗ (honestly excluded) | ✗ (correctly reserved) |
| Absent / out-of-sync physical leaf still shown | ✗ dropped | ✗ dropped | ✓ shown (status fallback) |
| 2. Read-only purity | ✓ | ✓ | ✓ |
| 3. Reuse vs duplication (no parallel state logic) | △ adds 2nd state map | ✓ verbatim reuse | ✓✓ single shared core |
| 4. SPA payload ergonomics | ✓ | ✓ | ✓ (never-null state) |
| Blast radius (existing code touched) | low (new only) | **lowest** (new only) | medium (reclaim Pass 2) |
| Reclaim regression (41 tests) | 41✓ | 41✓ | 41✓ (byte-identical) |
| Lines | 184+ | **133+** | 160+/16- |

Legend: ✓ good · △ partial/with caveat · ✗ absent.

---

## 4. Ranked recommendation

**1st — Candidate C.** It best satisfies the two criteria that distinguish a *library inventory* from the reclaim scan: completeness and non-duplication. It is the only candidate that surfaces every physical leaf (including absent / out-of-sync ones) instead of silently dropping them — for a UI whose entire job is to render the full per-tab inventory, a vanishing row is the worst failure mode, and C is the only one that avoids it. Its `_classify_item` is a genuine single source of truth: `state` can never drift between `/api/items` and `/api/reclaim`, which is exactly the invariant the project's "no command silently breaks another" rule wants. The headline risk — touching the load-bearing reclaim path — is the one I verified hardest: reclaim is byte-identical (41 passed), Pass 1 is untouched, and only Pass 2 is rerouted. The cost is the largest diff and a `status.upper()` fallback that emits state strings outside the 5-value enum; both are documented and easily reviewed.

**2nd — Candidate B.** The cleanest *minimal* reading and the smallest, lowest-risk diff (133 lines, nothing existing touched, widest green regression set at 101 passed). It reuses `classify_entry_state` verbatim with zero new state logic. It loses to C only on completeness: it drops `None`-state leaves (absent-file / out-of-sync), so such items disappear from their tab. If you weight "smallest blast radius + don't touch reclaim" above "show every leaf," B is the pragmatic pick — and grafting C's status-fallback into B closes the only real gap (see graft note).

**3rd — Candidate A.** Functionally identical OUTPUT to B (same 5 rows in my probe) but at +184 lines with a disk walk and a second `_LIFECYCLE_STATE_BY_STATUS` state map. The disk-anchored design's only justification was surfacing UNPREPPED — and my probe shows it does NOT (unknown files are skipped). So A pays the most complexity for no functional advantage over B, and introduces a parallel state-derivation surface that can drift from `classify_entry_state`. Solid, honest work, but dominated by B (simpler, same result) and by C (more complete, single source of truth).

### Graft suggestion (if you want the best of both)
- **Winner C is already the most complete.** If you instead prefer **B** (for minimal blast radius), **graft C's status-fallback** (`state = status.upper() if status else "UNKNOWN"`, `C/main.py:3418-3420`) into B so absent/out-of-sync leaves stop vanishing — that is B's one substantive gap.
- Regardless of winner, **wire the SPA's per-tab "Unprepped" sub-view to `/api/reclaim`** (none of the three populate UNPREPPED from `/api/items`, and that is the correct design). Track as a follow-up in the Phase-1 UI step.
- Minor: C's `entry` passthrough field in `_classify_item` is one field wider than today's callers need — harmless, optionally trim.

---

## 👉 Your choice

Pick one (nothing is merged until you say so):

- **C** — most complete + single source of truth; accept the reclaim-Pass-2 refactor (verified byte-identical, 41 passed) and the `status.upper()` out-of-enum fallback. **(my recommendation)**
- **B** — leanest, lowest risk, doesn't touch reclaim; accepts that absent/out-of-sync leaves don't show.
- **B + graft** — B as the base, plus C's status-fallback so no leaf vanishes (closes B's only gap without touching reclaim).
- **A** — only if you specifically want the disk-anchored structure; note it does NOT actually give UNPREPPED and is otherwise dominated by B.

Independent of the pick: the UI's "Unprepped" sub-view should source from `/api/reclaim`, not `/api/items` — confirm that fits your Phase-1 plan.

_Verification status: all three pass alias/season_map-safety, read-only purity, ARCHIVED inclusion, JSON-serializability, and the 41-test reclaim suite; C's reclaim output is byte-identical post-refactor. No candidate emits UNPREPPED (by design / verified). No candidate fails an acceptance criterion outright._
