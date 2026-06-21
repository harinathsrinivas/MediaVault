# Candidate A Self-Critique

## Approach taken
Implemented the five read-only data-layer functions in `main.py` (inserted just before the `if __name__ == "__main__"` block, after the last `cmd_*`). My internal strategy is **disk-first** (approach A): `collect_reclaimable()` builds a one-shot `known_paths` index from the library's physical leaves, then `os.walk`s the three category roots (mirroring `cmd_scan_unprepped`'s exclusion set and chunk/temp-dummy skips) classifying each on-disk video against that index. A separate second pass over the library's physical leaves adds reclaimable entries whose on-disk file is still real but were not produced by the walk. A single `seen` normpath-lower set is the only anti-double-count source.

## Design decisions and tradeoffs
- **Where the "real file only" filter for UNPREPPED lives.** `classify_entry_state(None, on_disk_real)` returns `"UNPREPPED"` unconditionally (honoring its literal contract line: `entry=None ⇒ "UNPREPPED"`). The real-vs-dummy gate for unknown files is enforced in `collect_reclaimable` PASS 1 instead — an unknown file only becomes a row when `on_disk_real` is True. This matters: live data has a 126-byte unknown `.mkv` (an out-of-sync dummy) that the old unconditional emission flagged as reclaimable UNPREPPED; gating on `on_disk_real` correctly drops it (49 rows → 38). Alternative considered: make `classify_entry_state` itself return None for a dummy unknown file — rejected because it would violate the function's stated literal contract and conflate the pure classifier with the reclaim-eligibility policy.
- **A real file under `archived` status returns no badge.** The State table ties `archived` strictly to a dummy. A real file under `archived` is an out-of-sync anomaly (dummy overwritten by a real file); returning `None` (not a reclaim badge) is the safe choice — we never suggest "reclaiming" an entry the user believes is archived. Alternative: treat it as PUSHED_NOT_ARCHIVED — rejected as misleading.
- **`suggest_*` receive an internal `work` dict carrying `entry`.** Rather than re-deriving library context, `_add_item` passes a working dict (with `entry`) to the two suggesters, then assembles the public row WITHOUT `entry` so the row keys are exactly the contract's 7. Keeps the suggesters pure and the row contract clean.
- **Title derivation from a canonical slug is lossy by design.** `suggest_target_folder` title-cases the `slug` segment (index 3 of `<cat>-<lang>-<year>-<slug>[-sNNeMM]`); a concatenated slug like `f1themovie` becomes `F1Themovie`. Since this is a NEW-item editable placeholder folder that the user renames, I prioritized correct segment selection (strip trailing `sNNeMM` / anime `<EE>`) over guessing word boundaries.

## Strengths
- IMP-C12 crash class fully guarded: every library iteration (`known_paths` build `main.py:3209`, PASS-2 loop `main.py:3268`) skips `season_map`/`multi_ep_alias` before touching `folder_path`/`filename`, and additionally `.get()`-guards missing `folder_path`/`filename`.
- De-dupe contract verified empirically against the real library (38 rows, zero duplicate normpath-lower keys; library leaf + its on-disk file collapse to one row).
- Strictly read-only — proven by asserting the three `library_*.json` mtimes are unchanged after a full `collect_reclaimable()` run.
- Graceful degradation: a missing category root prints a warning and continues (matches `cmd_scan_unprepped`), and PASS 2 still surfaces reclaimable library leaves for that absent root.
- `guess_manual_id` strips resolution tokens (`\d{3,4}p`) and release noise, lifts the anime trailing episode into `<EE>`, parses `sNNeMM`/`NNxMM`, and never raises (broad `except` → generic placeholder).

## Weaknesses
- `guess_manual_id` on noisy multi-episode filenames produces long ugly slugs (live example: `Mr.Robot.S02E01E02.eps2.0_unm4sk...` → `tv-en-2016-mrrobots02e01e02eps20unm4sktchdma51framestor-s02e01`). It is a wrong-but-editable placeholder (contract-permitted), but it is not pretty. The `S02E01E02` double-episode shape isn't specially handled; only the first `sNNeMM` is parsed for the suffix and the rest leaks into the slug.
- `ani.deathnote.e12` style (an anime file using an `e`-prefixed episode) yields `ani-en-0000-anideathnotee12` — the `e12` is not recognized as the episode because I only lift a *bare* trailing numeric token for anime. Acceptable per "wrong guess is fine," but it is a known gap.
- Title-from-slug cannot restore internal word boundaries (`F1themovie`, `Deathnote`), so NEW-item folder names look concatenated until the user edits them.
- PASS 2 only catches reclaimable leaves the walk missed; it deliberately does NOT re-`os.stat` files the walk already classified (the walk already has the size). Correct, but means PASS 2's value only shows when a root is absent or a path is unwalkable — under normal full-disk conditions it adds nothing (by design; verified the row count is stable).

## Tests run
Official acceptance:
```
$ python -c "import main; assert all(callable(getattr(main,n)) for n in ['collect_reclaimable','classify_entry_state','guess_manual_id','suggest_next_command','suggest_target_folder']); print('importable+callable ok')"
importable+callable ok
```

Pure-function matrix (classify_entry_state full table, suggest_next_command exact strings, suggest_target_folder NEW vs in-library, guess_manual_id never-raise) — all assertions passed:
```
classify_entry_state matrix OK
suggest_next_command OK
suggest_target_folder OK
ALL PURE-FN CHECKS PASS
guess_manual_id samples:
  'mov-en-2025-f1themovie'  (from F1.The.Movie.2025.2160p.BluRay.x265.mkv)
  'tv-en-0000-strangerthings-s01e03'
  'ani-en-0000-deathnote07'
  'tv-en-0000-chernobyl-s01e05'  (1x05 parsed)
  '' -> 'mov-en-0000-untitled'  (no raise)
```

`collect_reclaimable()` against the REAL library/disk (read-only):
```
keys OK; item count = 38
total bytes = 172698681817 | human = '160.84 GB'
badge counts = {'UNPREPPED': 31, 'RESTORED_REPLACE_AGAIN': 1, 'LOCAL_NOT_PUSHED': 6}
de-dupe OK; all 38 rows valid
READ-ONLY confirmed: library mtimes unchanged
COLLECT_RECLAIMABLE CONTRACT OK
```
(verified: every row has exactly the 7 contract keys, no `entry` leak, every `size_bytes >= DUMMY_MAX_BYTES`, badges ∈ valid set, `guessed == (badge=='UNPREPPED')`, `suggested_folder.applies == (badge=='UNPREPPED')`, total == sum of row sizes, no duplicate normpath keys.)

Smoke gate (main.py is a core file):
```
$ python -m pytest tests/smoke -q
........................................................                 [100%]
56 passed in 17.05s
```

## Confidence
high

Reasoning for confidence: The five functions are additive, pure/read-only, and the smoke gate plus a full real-library run both pass with the contract validated key-by-key (shape, de-dupe, read-only proof, badge correctness). The one real-data surprise (a dummy-sized unknown `.mkv`) was caught during validation and fixed by gating UNPREPPED emission on `on_disk_real`, which also tightened conformance to the State table. The honest soft spot is `guess_manual_id` quality on pathological multi-episode/odd-separator filenames — but the contract explicitly treats that output as an editable, never-auto-prepped placeholder, so a wrong guess is acceptable and a crash (the only hard failure mode) is prevented by the never-raise wrapper. Step-2 unit tests against a sandbox will pin down the remaining deterministic edges.
