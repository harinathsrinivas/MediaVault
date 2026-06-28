# Candidate A Self-Critique — Step 4 (PER-SITE MINIMAL)

## Approach taken
Introduced ONE module-level constant `_OTHER_ROOT_SUBDIRS = ["Sports"]` (main.py:90, in the top config cluster, before all consumers) and minimally extended each of the three disk-walk sites to also walk those roots, sourcing them from that one constant. The three known-category literals (`Movies`/`Series`/`Anime`) are left byte-identical; only the Others roots are appended. `_CATEGORY_ROOT_SUBDIR` and `build_tree` are unchanged (build_tree already nests `"other"` correctly under `LOCAL_ROOT`); I added a 4-line clarifying comment there documenting why `"other"` is deliberately absent from the subdir map.

## Design decisions and tradeoffs
- **Read `mvcommon.LIBRARY_OTHERS` module-qualified, NOT a bare imported name (the key decision).** The step text literally said `("Others", LIBRARY_OTHERS, …)`, but `main.py` does NOT import `LIBRARY_OTHERS`, and the committed Step 6 `sandbox` fixture patches `mvcommon.LIBRARY_OTHERS` ONLY (its comment explicitly states `main.LIBRARY_OTHERS` doesn't exist and patching it "would raise AttributeError"). A bare name would force `from mvcommon import LIBRARY_OTHERS`, creating a separate `main.LIBRARY_OTHERS` binding the fixture can't patch — and `cmd_scan_unprepped` opens `lib_file` directly, so Step 7's `sandbox`-based tests would read the REAL `C:\Media\library_others.json`, re-opening the exact safety hole Step 6 closed. I'm constrained to edit ONLY `main.py`, so I cannot dual-patch conftest. Module-qualified access honors the mvcommon-only patch, matches main.py's deliberate `import mvcommon` pattern (lines 36-41), and is verified by both my focused proof and the green smoke suite. Alternative considered: import the bare name + accept the binding hazard — rejected as incorrect under the constraints.
- **`categories += [...]` append (sites 2 & 3) instead of editing the existing list literal.** Keeps the 3 known triples/paths 100% byte-identical (zero risk to the existing-3 invariant), at the cost of one extra statement. For `cmd_recover` I instead unpacked `*_OTHER_ROOT_SUBDIRS` into the existing tuple comprehension source — tighter there and the 3 literals still read verbatim.
- **Constant placement: top config cluster (before consumers), not next to `_CATEGORY_ROOT_SUBDIR`.** Three of the four references sit ABOVE `_CATEGORY_ROOT_SUBDIR` (line 6861), so co-locating there would define the constant below its consumers. The top cluster precedes all uses and is discoverable; I bridged the conceptual link with the `build_tree` comment that cross-references `_OTHER_ROOT_SUBDIRS`.

## Strengths
- Single source of truth: `_OTHER_ROOT_SUBDIRS` defined once (`main.py:90`), iterated at all three walkers (`main.py:876`, `:5488`, `:6229`). Adding `"Documentary"` is a pure one-line data edit — no walker code changes (judge criteria #2/#3).
- Existing-3 invariant preserved exactly: `cmd_scan_unprepped`/`collect_reclaimable` list literals are untouched (append-only); `cmd_recover`'s 3 literals are verbatim inside the tuple. Movies/Series/Anime each still walk exactly one folder (`main.py:5488`, `:6229`, `:876`).
- `build_tree` untouched (behavior); added comment (`main.py:7062-7066`) documents the `LOCAL_ROOT` fallback and warns against the `"other":"Sports"` footgun the step calls out.
- Smallest blast radius: 5 edits, all in `main.py`; no import added; no fixture/conftest change needed.

## Weaknesses
- `cmd_scan_unprepped` re-reads `library_others.json` once per Others root. Harmless for Sports-only (1 root); with N Others roots the small JSON is parsed N times. `known_paths` is built from the full Others library each time, so detection stays correct — only a trivial re-parse cost. Documented at the site (`main.py:5483-5487`).
- The 4th `cmd_scan_unprepped` tuple uses `mvcommon.LIBRARY_OTHERS` while the other three use bare names — an intentional asymmetry (dual-patched vs mvcommon-only) that I documented inline, but a reader skimming may find it surprising.
- With multiple Others roots, `cmd_scan_unprepped` prints two sections both labeled "Others" (disambiguated only by the folder path in the header). Cosmetic.

## Tests run
Focused proof (throwaway, scratchpad; mirrors the real `sandbox` patch — `LIBRARY_OTHERS` on mvcommon ONLY, plus a seeded PREPPED oth- entry):
```
PASS: main does NOT bind LIBRARY_OTHERS (module-qualified access required)
PASS: cmd_scan_unprepped reports the UNPREPPED movies file
PASS: cmd_scan_unprepped reports the UNPREPPED sports file (Others walk added)
PASS: cmd_scan_unprepped EXCLUDES the prepped oth- file (read mvcommon-only LIBRARY_OTHERS)
PASS: collect_reclaimable includes the movies file (Movies walk unchanged)
PASS: collect_reclaimable includes the sports file (Others walk added)
PASS: collect_reclaimable marks the sports file UNPREPPED (got: 'UNPREPPED')
PASS: cmd_recover --scan returns >=1 journal (got 1)
PASS: cmd_recover --scan reports the Sports journal dir
PASS: _OTHER_ROOT_SUBDIRS is the single ['Sports'] source
ALL PASS (0 failures)
```
(The two "Folder not found: Series/Anime" lines are the expected graceful-degrade warnings — those folders weren't created.)

No-regression + mandatory smoke (touches `main.py`):
```
$ python -m pytest tests/test_web_datafns.py tests/smoke -q
105 passed, 1 warning in 337.88s (0:05:37)
```
(The 1 warning is a pre-existing `StarletteDeprecationWarning` in `tests/smoke/test_smoke_all_commands.py:318`, unrelated to this change.) `python -c py_compile` on `main.py`: OK.

## Confidence
high

Reasoning for confidence: All three walkers are proven to see `LOCAL_ROOT/Sports` while Movies/Series/Anime stay byte-identical, list-capability is a single-line edit, and the smoke suite (which drives these commands under the real `sandbox` fixture) is green — directly validating the module-qualified `LIBRARY_OTHERS` decision in the actual harness, not just my script. The main judgment call (deviating from the literal `LIBRARY_OTHERS` bare-name in the step text) is forced by the committed Step 6 conftest contract plus the "edit only main.py" constraint, and I verified it both ways; I'm confident it's the correct call rather than a regression.
