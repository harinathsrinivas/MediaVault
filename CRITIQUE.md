# Candidate B Self-Critique — Step 4 (single-source CATEGORY_ROOTS)

## Approach taken
Introduced ONE ordered source-of-truth table near the config —
`CATEGORY_ROOTS = {"movies": ["Movies"], "series": ["Series"], "anime": ["Anime"], "other": ["Sports"]}`
(category → list of subdir names under `LOCAL_ROOT`) — and rewired all three disk
walkers to derive their roots from it, so no walker hardcodes folder names anymore.
`cmd_recover --scan` (`main.py:885-887`) and `collect_reclaimable` PASS 1
(`main.py:6237-6241`) each flatten `CATEGORY_ROOTS.values()` into
`os.path.join(LOCAL_ROOT, d)` paths; `cmd_scan_unprepped` (`main.py:5487-5504`)
builds one `(display, lib_file, folder)` triple per `(category, subdir)` from
`CATEGORY_ROOTS` plus a small call-time `lib_files` map. `build_tree` and
`_CATEGORY_ROOT_SUBDIR` are left functionally untouched (the dict line is
byte-identical; only its comment was expanded).

## Design decisions and tradeoffs
- **Read `LIBRARY_OTHERS` module-qualified (`mvcommon.LIBRARY_OTHERS`), not imported.**
  main.py deliberately does NOT import `LIBRARY_OTHERS` (the conftest at
  `tests/conftest.py:83-90` patches it on `mvcommon` ONLY and warns that
  `setattr(main, "LIBRARY_OTHERS", …)` would raise). So in `cmd_scan_unprepped` I
  read it as `mvcommon.LIBRARY_OTHERS` — matching the existing module-qualified
  precedent (`main.py:36-41`). Alternative considered: add `LIBRARY_OTHERS` to
  main's `from mvcommon import (...)`. Rejected — it would create a separate
  `main.LIBRARY_OTHERS` binding that the sandbox's mvcommon-only patch can't
  redirect (binding hazard → a test would read real `C:\Media\library_others.json`),
  and it would falsify the Step-6 conftest comment. My choice keeps the sandbox
  patch correct with ZERO conftest changes.
- **`lib_files` is built at CALL TIME inside the function, not at module scope.**
  A module-level dict would capture `LIBRARY_*` by value at import (the real
  `C:\Media` paths) and defeat conftest's `monkeypatch.setattr(main, "LIBRARY_*", …)`.
  Building it per-call preserves today's call-time global lookup exactly.
- **Did NOT unify `_CATEGORY_ROOT_SUBDIR` into `CATEGORY_ROOTS`.** They are
  genuinely opposite for `"other"`: walkers need `other → ["Sports"]` (a real walk
  root), but `build_tree` needs `other → LOCAL_ROOT` (so an oth- leaf nests with
  `Sports/…` as a top folder under the Others bucket). Unifying would force a
  special-case inside the load-bearing `build_tree`, adding blast radius for
  marginal gain. I kept them separate and added a cross-reference comment on each.
- **Subdir IS the display label in `cmd_scan_unprepped`.** For the 3 known
  categories the subdir equals today's display name ("Movies"/"Series"/"Anime"), so
  output is byte-identical; "other"'s folder prints the informative "Sports". No
  separate display-name map needed.

## Strengths
- **One-line future edit (judge #2):** adding Documentary is literally
  `"other": ["Sports", "Documentary"]` at `main.py:111` — no walker code changes.
- **Drift-resistance (judge #3):** all three walkers reference ONE table; zero
  hardcoded folder literals remain in any walker.
- **Existing-3 invariant (judge #1):** flatten order is Movies→Series→Anime→Sports;
  proven `derived[:3]` equals the historical trio paths byte-for-byte.
- **build_tree untouched:** its logic is byte-identical (only a comment changed),
  so the `other → LOCAL_ROOT` nesting invariant is preserved by construction, not
  just by test.
- **Binding-hazard-safe:** `mvcommon.LIBRARY_OTHERS` read at call time; conftest
  needs no edit; no `main.LIBRARY_OTHERS` binding introduced.

## Weaknesses
- **Larger diff than Candidate A** (+50/−12 vs. per-site append). The refactor
  rewrites all three walk-site bodies rather than appending Others subdirs at each.
  Justified purely by drift elimination + the single-source story; if the judge
  weights surgical-ness (criterion #4) above drift-resistance (#3), A's minimal
  edits are smaller.
- **Two category tables still exist** (`CATEGORY_ROOTS` for walks,
  `_CATEGORY_ROOT_SUBDIR` for build_tree). Adding a wholly NEW category key (not a
  new "other" subdir) would require touching both `CATEGORY_ROOTS` and the
  `lib_files` map in `cmd_scan_unprepped`. The stated D18 follow-up (Documentary)
  is unaffected — it's a new subdir under the existing "other" key, one line.
- **oth- build_tree nesting is not directly exercised by the no-regression suite**
  (no oth- fixtures yet — Step 7 owns those). It is guaranteed unchanged because
  build_tree's code is byte-identical; but there is no green oth- assertion for it
  in this candidate's run.
- **Relies on dict insertion order** (Python 3.7+) for walk-order parity —
  documented in the `CATEGORY_ROOTS` comment; safe for this codebase.

## Tests run
Focused proof (throwaway script in scratchpad, since removed): patched
`mvcommon`+`main` `LOCAL_ROOT` and the `LIBRARY_*` exactly as conftest does
(`LIBRARY_OTHERS` on mvcommon only; asserted `main` has no such attr), created
250 KB UNPREPPED files at `…/Media/Sports/Football/Cup/2026/match - First Half.mkv`
and `…/Media/Movies/X/movie.mkv`, and a journal at `…/Media/Sports/Z/`:

```
--- RESULTS ---
PASS scan_unprepped sees sports file
PASS scan_unprepped sees movies file
PASS scan_unprepped labels Sports folder
PASS scan_unprepped Movies label unchanged
PASS scan_unprepped Series label unchanged
PASS scan_unprepped Anime label unchanged
PASS reclaimable includes sports file (exactly 1 row)
PASS reclaimable sports badge == UNPREPPED
PASS reclaimable includes movies file (exactly 1 row)
PASS recover --scan found >=1 journal
PASS recover --scan lists the Sports journal dir
PASS CATEGORY_ROOTS flatten order == Movies,Series,Anime,Sports
PASS first three roots are exactly the historical trio

ALL PASS
```

No-regression (mandatory smoke — touches main.py):
```
$ python -m pytest tests/test_web_datafns.py tests/smoke -q
105 passed, 1 warning in 268.98s (0:04:28)
```
(The 1 warning is a pre-existing StarletteDeprecationWarning, unrelated.)

## Confidence
high

Reasoning for confidence: The proof script directly exercises all three walkers
against a real temp Media tree and confirms both the new Sports coverage and the
byte-identical historical trio order; the full smoke suite (which drives every
command, including scan_unprepped and recover) is green. The one residual is that
build_tree's oth- nesting has no direct oth- assertion here — but I verified by
reading that its code is byte-identical (only a comment changed), so that invariant
cannot have regressed.
