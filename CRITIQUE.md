# Candidate C Self-Critique

## Approach taken
I added five read-only module-level functions to `main.py` (inserted just before `cmd_scan_unprepped` @2579, among the library helpers) implementing the IMP-E12 reclaim data layer. The core is `collect_reclaimable()` built as Candidate C's **unified normpath index**: a single `dict[normpath_lower -> record]` seeded from BOTH the library physical leaves (carrying `entry`+`id`) and an `os.walk` of the three roots (carrying the real on-disk size), merged per key, then classified exactly once. The dict key is the only de-dup authority, so a library leaf and its on-disk file are structurally guaranteed to produce exactly one row.

## Design decisions and tradeoffs
- **Structural de-dup via the dict key (the assigned approach).** Instead of a `seen` set layered on top of two passes, both sources write into the same `index[key]` and merge in place (`main.py` `collect_reclaimable` (b)-branch: `rec["size"]=size; rec["path"]=full_path`). Double-counting is impossible by construction. Verified on real data: all 49 emitted items have unique normpath-lower keys.
- **Lazy stat only for library leaves the walk did not cover.** The walk supplies the on-disk size for every path it visits. A library leaf whose root is absent/unwalked has `size=None` after merge; I `os.getsize()` it lazily in the classify loop (absent -> `None` -> `on_disk_real=False`). This means: when a root is present the size comes free from the walk (no double stat), and when a root is absent we still classify the leaf correctly (graceful degradation, matching `cmd_scan_unprepped`'s warn-and-continue).
- **`on_disk_real` is disk-truth, not status-truth.** `on_disk_real = size is not None and size >= DUMMY_MAX_BYTES`, matching `cmd_check`@1144 / `cmd_repair_dummies`@2058 (real ⇔ size ≥ threshold). An `onboarded`/`local_ready`/`restored_local` entry whose on-disk file is already a dummy is correctly excluded (classify returns `None`); an `archived` entry whose file is somehow real is flagged `ARCHIVED` and excluded from items. Both verified.
- **`classify_entry_state` uses explicit per-status branches** rather than a status-set membership test. Slightly more lines, but it makes the `archived`+real anomaly and the unknown-status fallback (`LOCAL_NOT_PUSHED` when a real file exists under an unrecognized status) explicit and individually testable for step 2.
- **`guess_manual_id` is wrapped in a total try/except** with a `_slugify`/parent-folder/`"untitled"` fallback chain so it can never raise — the contract demands a plausible editable string, never a crash. Year uses a 1900–2099 window on 4-digit tokens (so `2160p` resolution is not mistaken for a year), scanning file stem then parent folder.

## Strengths
- Structural, provable de-dup — `collect_reclaimable` (`main.py` index dict) needs no `seen` set; verified 49/49 unique keys on real data.
- Every library iteration skips `season_map`/`multi_ep_alias` BEFORE touching `folder_path`/`filename` (PR #21 / IMP-C12 guard): the leaf-seed loop checks `entry.get("type") in (...)`, and `classify_entry_state`/`suggest_target_folder` both early-return `None`/existing-folder for alias rows.
- READ-ONLY proven empirically: I MD5-snapshotted all three `library_*.json` before and after a real `collect_reclaimable()` call — unchanged. No write/delete/rename anywhere in the five functions.
- Real-data run is clean: 49 items, 160.84 GB, correct badge split (42 UNPREPPED all `guessed=True`; 6 LOCAL_NOT_PUSHED + 1 RESTORED `guessed=False`), exact 7-key item shape, ARCHIVED dummies excluded.
- Reuses the canonical exclusion set, `.temp_dummy`/`.chunk.` skips, and `VIDEO_EXTENSIONS` filter verbatim from `cmd_scan_unprepped`.

## Weaknesses
- `guess_manual_id` year detection only looks at the file stem and the *immediate* parent folder, not grandparents. For `Anime\Classic\Death Note\Death.Note.E07.mkv` where the year lives two folders up, it yields `ani-en-0000-death-note07`. This is acceptable per the contract (editable placeholder, wrong guess is fine) but a disk-first candidate that walks more path components could guess the year more often.
- `suggest_target_folder` derives the proposed Title from the guessed-id slug (humanized via `.title()`) and always uses genre `"Unsorted"` and language from the id's lang2 — pre-prep there is no genre signal, so the proposed path is a reasonable skeleton the user edits, not a final destination. I judged a no-lookup skeleton correct for this step (the contract says NO TMDB/TVDB lookup here).
- The anime episode parse assumes a 1–3 digit trailing/`Eyy` number; an absolute-numbered anime file with no episode token defaults to `01`. Again editable, not auto-prepped.
- I did not add a `prep_season` variant string for multi-file UNPREPPED folders in `suggest_next_command` (the table notes it parenthetically); I emit the single-file `prep` form per item. Per-file is the deterministic, testable choice; folder-level grouping is a UI concern for later steps.

## Tests run
Acceptance check (required):
```
$ python -c "import main; assert all(callable(getattr(main,n)) for n in ['collect_reclaimable','classify_entry_state','guess_manual_id','suggest_next_command','suggest_target_folder']); print('importable+callable ok')"
importable+callable ok
```

Pure-function contract exercise (synthetic inputs, no mutation):
```
classify_entry_state ok
mov: mov-en-2025-f1-the-movie
tv : tv-en-2016-stranger-things-s01e03
ani: ani-en-0000-death-note07
none: mov-en-0000-none | empty: mov-en-0000-untitled
suggest_next_command ok
suggest_target_folder ok: C:\Media\Movies\English\Unsorted\F1 (2025) {tmdb-0000000}
```

`collect_reclaimable()` against the REAL library/disk (read-only):
```
contract keys ok | items: 49 | total: 160.84 GB
item-shape + de-dup ok over 49 items
badges: {'UNPREPPED': 42, 'RESTORED_REPLACE_AGAIN': 1, 'LOCAL_NOT_PUSHED': 6}
guessed True: 42 | False: 7
  > UNPREPPED | 19230183087 | tv-en-2019-dark-s02e03
     cmd: python main.py prep tv-en-2019-dark-s02e03 "C:\Media\Series\English\Mind Bending\Dark (2017) [tvdbid-334824]\Dark Season 02 (2019)\Dark.S02E03.2160p.mkv"
  > RESTORED_REPLACE_AGAIN | 17654341815 | mov-en-2013-coherence
     cmd: python main.py replace mov-en-2013-coherence
READ-ONLY verified: library files unchanged
```

Smoke gate (main.py was modified):
```
$ python -m pytest tests/smoke -q
........................................................                 [100%]
56 passed in 10.17s
```

## Confidence
high

Reasoning for confidence: The acceptance check, a synthetic contract exercise covering every badge/branch, a real-data read-only run (correct shape, correct badges, unique-key de-dup, ARCHIVED exclusion, library-MD5-unchanged), and the full smoke gate (56 passed) all pass. The assigned unified-normpath-index approach is implemented faithfully and its de-dup guarantee is empirically confirmed. My one genuine uncertainty is whether step-2's tests expect `guess_manual_id` to find a year that lives in a grandparent folder (mine only scans stem + immediate parent) — but the contract explicitly allows wrong guesses since the id is an editable, never-auto-prepped placeholder, so I do not consider this a correctness risk for this step.
