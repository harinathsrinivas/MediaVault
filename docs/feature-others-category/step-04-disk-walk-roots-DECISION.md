# Decision: Step 4 — Make the disk-walk category roots list-capable and wire `"other" → ["Sports"]`

## Outcome
Winner: Candidate B
Branch: `feature/imp_d18_others_category__s4cand_b`

## Step requirements
Make the three disk walkers in `main.py` see the sports files under `LOCAL_ROOT\Sports` for the `"other"` category, while the three existing single-folder categories (Movies/Series/Anime) keep walking byte-identically, AND make adding `"Documentary"` later a one-line DATA edit. The three walk sites: `cmd_recover --scan` (~865), `cmd_scan_unprepped` (~5466, `(name, lib_file, folder)` triples), `collect_reclaimable` PASS 1 (~6203). `build_tree` (~7040) must KEEP resolving `"other"` to `LOCAL_ROOT`; neither candidate may set a single `_CATEGORY_ROOT_SUBDIR["other"]` subdir. `LIBRARY_OTHERS` must be read module-qualified as `mvcommon.LIBRARY_OTHERS` (main never binds it; the Step-6 conftest patches only the mvcommon binding).

## Judge criteria applied (priority order, from the step)
1. Correctness + the existing-3-unchanged invariant (heaviest weight) — all 3 walkers see `LOCAL_ROOT/Sports`; Movies/Series/Anime each still walk exactly their one folder; `build_tree` nesting unchanged; `mvcommon.LIBRARY_OTHERS` sandbox-safe.
2. List-capability — appending `"Documentary"` is a pure one-line data edit, no walker code change.
3. Drift-resistance — single source of truth vs. three sites that can disagree.
4. Surgical-ness / blast radius (lowest weight).

## Candidate summaries

### Candidate A
- Approach: Per-site minimal — one module constant `_OTHER_ROOT_SUBDIRS = ["Sports"]` (main.py:90) appended at each of the 3 walk sites; the three known-category folder literals left byte-identical; `build_tree` unchanged + clarifying comment.
- Files modified: `main.py` (+ identical `DEVICE_ALIASES` "others" entry and `cmd_prep_season` `is_other` block — see shared note).
- Lines changed: ~+76 / −1 (walk-site portion is ~5 small edits).
- Tests: focused proof 11/11 PASS; `pytest tests/test_web_datafns.py tests/smoke -q` → 105 passed.
- Self-critique highlights: smallest blast radius; the existing-3 literals are untouched (append-only) so the invariant holds by construction; module-qualified `mvcommon.LIBRARY_OTHERS` is the forced-correct choice under the "edit only main.py" + Step-6 conftest constraints; flags an N-fold re-parse of `library_others.json` per Others root and a cosmetic double-"Others" section header as weaknesses.
- Independent assessment:
  - Strengths:
    - Existing-3 invariant is preserved *textually* — `cmd_scan_unprepped` (`main.py:5482`) and `collect_reclaimable` PASS 1 (`main.py:6223`) keep the original 3-element list literals verbatim and only `+=` the Others roots; `cmd_recover --scan` keeps the 3 literals inside the tuple `("Movies","Series","Anime", *_OTHER_ROOT_SUBDIRS)`. Zero risk to those three walks.
    - `mvcommon.LIBRARY_OTHERS` read module-qualified (`main.py:~5486`); no `main`-side binding introduced — sandbox-safe.
    - `build_tree`/`_CATEGORY_ROOT_SUBDIR` genuinely untouched; the added comment correctly warns against the `"other":"Sports"` footgun.
    - Adding `"Documentary"` is one line in `_OTHER_ROOT_SUBDIRS` — list-capability satisfied.
  - Weaknesses:
    - The Movies/Series/Anime roots remain **triplicated** as hardcoded literals across the 3 sites. There is a single source of truth only for the *Others* roots, not for category roots as a whole — the three sites CAN drift on the known-3 (exactly what criterion 3 probes).
    - `cmd_scan_unprepped` labels the Others section "Others" regardless of subdir; with >1 Others root every section header reads "Others" (self-noted, cosmetic).
    - Re-parses `library_others.json` once per Others root (trivial today at 1 root).

### Candidate B
- Approach: Single source of truth — `CATEGORY_ROOTS = {"movies":["Movies"],"series":["Series"],"anime":["Anime"],"other":["Sports"]}` (main.py:~107); all 3 walkers derive their roots from it; no folder-name literal remains in any walker. `build_tree`/`_CATEGORY_ROOT_SUBDIR` functionally unchanged (dict line byte-identical, comment expanded).
- Files modified: `main.py` (+ identical `DEVICE_ALIASES` "others" entry and `cmd_prep_season` `is_other` block — see shared note).
- Lines changed: ~+166 / −12 overall; the walk-site refactor itself is ~+50 / −12.
- Tests: focused proof 13/13 PASS (incl. `derived[:3] == historical trio` byte-for-byte and flatten-order assertion); `pytest tests/test_web_datafns.py tests/smoke -q` → 105 passed.
- Self-critique highlights: one table feeds all walkers (drift eliminated); deliberately did NOT unify `CATEGORY_ROOTS` into `_CATEGORY_ROOT_SUBDIR` because `"other"` is genuinely opposite for walkers (→`["Sports"]`) vs `build_tree` (→`LOCAL_ROOT`); `lib_files` built at call time for monkeypatch safety; honest that the diff is larger, that two tables still coexist, that a wholly-new category key touches both tables, and that oth- `build_tree` nesting has no direct assertion here (Step 7 owns those fixtures).
- Independent assessment:
  - Strengths:
    - True single source of truth: `cmd_recover --scan` (`main.py:~886`), `cmd_scan_unprepped` (`main.py:~5487`), and `collect_reclaimable` PASS 1 (`main.py:~6237`) all derive from `CATEGORY_ROOTS`. The three sites **cannot** disagree — directly satisfies criterion 3.
    - Existing-3 invariant verified, not just asserted: dict insertion order Movies→Series→Anime→other flattens to `["Movies","Series","Anime","Sports"]`, and the proof asserts the first three derived paths equal the historical trio byte-for-byte.
    - `mvcommon.LIBRARY_OTHERS` read module-qualified via a call-time `lib_files` map (`main.py:~5489`) — sandbox-safe AND monkeypatch-safe (no value captured at import).
    - `build_tree`/`_CATEGORY_ROOT_SUBDIR` logic byte-identical; nesting invariant preserved by construction.
    - Adding `"Documentary"` is one line: `"other": ["Sports", "Documentary"]`.
    - `cmd_scan_unprepped` uses the subdir as the display label, so the Others folder prints the informative "Sports"; Movies/Series/Anime labels are unchanged.
  - Weaknesses:
    - Larger diff; all three walk-site bodies are rewritten to comprehensions, so the existing-3 walks are reproduced by *derivation* rather than left textually intact (mitigated by the byte-for-byte proof + green smoke).
    - Relies on dict insertion order (3.7+) for walk-order parity (documented; safe for this codebase).
    - Two category tables still coexist (`CATEGORY_ROOTS` for walks, `_CATEGORY_ROOT_SUBDIR` for `build_tree`); a wholly-new category key would touch both plus the `lib_files` map — but the stated D18 follow-up (a new Others subdir) is unaffected.

## Head-to-head comparison
**A vs B — criterion 1 (correctness + existing-3 invariant):** Effectively tied. A preserves the three known walks *textually* (the literals are physically unchanged), which is the lowest-risk way to guarantee no regression. B reproduces them by flattening `CATEGORY_ROOTS` but proves `derived[:3]` equals the historical trio byte-for-byte and passes the same 105-test smoke run. Both read `mvcommon.LIBRARY_OTHERS` module-qualified and leave `build_tree` untouched. A's edge here is marginal and is about *form of assurance* (textual identity vs verified derivation), not about a real behavioral difference.

**A vs B — criterion 2 (list-capability):** Tied. Both make adding `"Documentary"` a one-line data edit (A in `_OTHER_ROOT_SUBDIRS`, B in `CATEGORY_ROOTS["other"]`), with no walker code change.

**A vs B — criterion 3 (drift-resistance):** B wins decisively. B has ONE table that all three walkers derive from; no folder literal remains in any walker, so the three sites cannot disagree about any category. A single-sources only the *Others* roots; Movies/Series/Anime stay hardcoded independently at three sites, so those three can still drift apart. The criterion explicitly asks "is there a single source of truth for the category roots, or can the 3 sites disagree?" — A answers "partially," B answers "fully."

**A vs B — criterion 4 (surgical-ness):** A wins. ~+76/−1 with the known-3 literals physically untouched and no comprehension rewrites; B is larger and rewrites all three walk bodies. This is the lowest-weighted criterion.

## Rationale for chosen winner
Candidate B is the winner. Criterion 1 (the heavily-weighted correctness + existing-3 invariant) is a genuine tie: both candidates leave `build_tree` and `_CATEGORY_ROOT_SUBDIR` functionally byte-identical, both read `mvcommon.LIBRARY_OTHERS` module-qualified (no `main`-side binding, so the Step-6 sandbox patch is honored), both make all three walkers see `LOCAL_ROOT/Sports`, and both pass the identical 105-test smoke suite. B additionally proves its derived first-three roots equal the historical Movies/Series/Anime paths byte-for-byte, so its derivation is verified rather than assumed.

With correctness tied, the decision falls to the step's stated *point* — criteria 2 and 3 — and there B is clearly stronger. The step explicitly frames the goal as "list-capable" roots with a single source of truth and warns (criterion 3) against the three sites being able to disagree. B's `CATEGORY_ROOTS` (`main.py:~107`) is consumed by all three walkers (`main.py:~886`, `~5487`, `~6237`) with zero hardcoded folder literals remaining; the sites are drift-proof by construction. A leaves the Movies/Series/Anime literals triplicated across the three sites and single-sources only the Others roots — it satisfies the letter of "adding Documentary is one line" but not the spirit of "single source of truth for the category roots."

B's deliberate decision NOT to fold `CATEGORY_ROOTS` into `_CATEGORY_ROOT_SUBDIR` is the correct call and is well-reasoned in its critique: walkers need `other → ["Sports"]` while `build_tree` needs `other → LOCAL_ROOT`, so unifying them would force a special-case inside the load-bearing `build_tree` — more blast radius for no gain. Keeping the two tables separate with cross-referencing comments is the right tradeoff. B also displays the Others folder as the informative "Sports" rather than A's generic (and, with multiple roots, ambiguous) repeated "Others" header.

What B does worse: it is the larger, less surgical diff (criterion 4), and it reproduces the existing-3 walks by derivation rather than leaving them textually intact — a reviewer must trust the flatten order plus the byte-for-byte proof rather than reading three unchanged literals. It also leans on dict insertion order for walk-order parity. These are acceptable because criterion 4 is the lowest weight, the byte-for-byte assertion plus the green smoke run close the derivation-risk gap, and insertion-order reliance is safe and documented on Python 3.7+.

## Why not the other?
**Candidate A** is a clean, low-risk, genuinely correct implementation and would be a perfectly safe merge — its loss is not about a bug. It simply under-delivers on the step's headline goal: it creates a single source of truth only for the *Others* roots while leaving the three known-category roots hardcoded at three separate sites, so criterion 3 (drift-resistance / "can the 3 sites disagree?") is only partially met. Given that correctness and list-capability are tied between the two, the drift-resistance gap is the deciding factor, and A is on the wrong side of it. Its real advantage — the smallest blast radius — is the lowest-weighted criterion and not enough to overcome the criterion-3 deficit.

## What we keep from losing candidates
From Candidate A, the *form* of its existing-3 guarantee is worth remembering: keeping the original list literals physically untouched (append-only) is the most reviewer-legible way to prove "the known walks didn't change." If B's derivation ever feels too clever for a future maintainer, an inline comment in B spelling out "first three flattened entries == the historical Movies/Series/Anime literals (asserted in tests)" would recover that legibility. No code synthesis is required — this is a documentation nicety, not a defect.

## Shared observation (non-differentiating)
Both candidates also added an identical `DEVICE_ALIASES["others"]` placeholder and an identical `is_other` position-numbering block in `cmd_prep_season` (~3580). These are outside the literal scope of Step 4 (which is about the three disk walkers) and appear in BOTH candidates byte-for-byte, so they do not affect this comparison. If they belong to a different plan step, the orchestrator may want to confirm they aren't double-applied downstream; that is a process note, not a reason to prefer either candidate.

## Verification status
Confirmed for the winner (Candidate B):
- All 3 walkers derive `LOCAL_ROOT/Sports` for `"other"` (proof: scan_unprepped, collect_reclaimable, recover --scan all see Sports). PASS.
- Movies/Series/Anime each still walk exactly one folder; derived first-three roots equal the historical trio byte-for-byte (proof assertion). PASS.
- `build_tree`/`_CATEGORY_ROOT_SUBDIR` logic byte-identical; `"other"` still resolves to `LOCAL_ROOT` (no `_CATEGORY_ROOT_SUBDIR["other"]` set). PASS.
- `LIBRARY_OTHERS` read module-qualified as `mvcommon.LIBRARY_OTHERS` via a call-time map; no `main` binding introduced — sandbox-safe. PASS.
- Adding `"Documentary"` is a one-line data edit in `CATEGORY_ROOTS`. PASS.
- `pytest tests/test_web_datafns.py tests/smoke -q` → 105 passed. PASS.
Residual (acknowledged, not blocking): oth- `build_tree` nesting has no direct oth- assertion in this step's run (Step 7 owns those fixtures); guaranteed unchanged because `build_tree` code is byte-identical.
