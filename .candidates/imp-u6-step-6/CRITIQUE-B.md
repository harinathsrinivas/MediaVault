# Step 6 self-critique — `migrate_provider_tokens`

## Approach taken

Discovery is a **top-down disk walk of `CATEGORY_ROOTS`**, not a library walk. `os.walk` runs over
`LOCAL_ROOT/{Movies,Series,Anime,Sports}` using the same idiom and the same exclusion set
(`_RECLAIM_EXCLUDE_DIRS`) that `cmd_scan_unprepped` / `cmd_recover --scan` / `collect_reclaimable`
already use, and every directory's **own basename** is tested with `mvcommon.find_provider_tokens`.
Each hit is then cross-referenced against the library with `_collect_folder_descendants` — the very
helper `cmd_rename_folder` uses for its own "is anything pointing here?" refusal — to decide
migrate (≥1 referencing entry) versus report-as-orphan (0). The full walk completes *before* anything
is renamed; the candidate list is then sorted deepest-first and each entry is renamed by one
`cmd_rename_folder(old, new_leaf_name)` call. No renaming, journalling, or path primitive is
reimplemented; the command chooses only the string.

New code is confined to `main.py`: one section header block, three module constants, three small
helpers (`_stale_tmdb_tokens`, `_canonicalize_tmdb_name`, `_folder_depth`),
`cmd_migrate_provider_tokens`, one CLI dispatch branch, and one usage line. `mvcommon.py` is
untouched; `cmd_rename_folder`, the journal format, the PONR placement, `recover_journal` and
`RollbackHardFail` are untouched.

## Design decisions and tradeoffs

1. **Disk-first discovery instead of per-entry ancestor walk-up.** The alternative (climb from every
   physical entry's `folder_path` up to `LOCAL_ROOT`) touches fewer directories but can only ever see
   folders the library already implies. The remaining real-world migration is *an ancestor nobody
   names*, and the same blind spot that produced it (a tool that only rewrote leaf segments) is
   exactly what a library-derived candidate set cannot audit. The walk sees the disk as it is: it
   found the Friends-shaped parent, the nested pair, and the orphan in the same pass. Cost is
   discussed honestly below.

2. **Orphans are report-only by construction, and I added no opt-in flag to rename them.** This was
   a real decision, not an omission. `cmd_rename_folder` *refuses* a folder no library entry
   references ("No library entries reference … — nothing to rename"), and it is change-gated. An
   `--include-orphans` flag would therefore need a second, un-journalled rename path — precisely the
   thing the step forbids. So the safety property is structural rather than policy-based: there is no
   code path in this command that can move an unreferenced folder. They are printed and recorded in
   the report for the user to handle by hand. (`main.py` section header + the `orphans` branch.)

3. **Rewrite by span, right-to-left, for *every* stale tmdb token.** `_canonicalize_tmdb_name` never
   rebuilds a name from parsed parts; it splices `CANONICAL_TMDB_TOKEN_FMT` into the exact `span`
   `find_provider_tokens` reported. Right-to-left means an earlier splice cannot invalidate a later
   token's offsets, which makes the multi-token case fall out of the same three lines as the
   single-token case — no special-casing, and `[rartv]`, `[tvdbid-266189]`, spacing and title casing
   survive byte-identically. "Stale" is *derived* (`token["match"] != CANONICAL_FMT.format(id=…)`),
   not matched against a hardcoded `[tmdbid-` literal, so `[tmdbid=1]` and `[TMDBID-1]` are correctly
   treated as needing migration and the predicate follows the emit format if it ever changes again.

4. **Two deliberate deviations from `enrich_metadata`'s UX, both hardening.** (a) A missing or
   unrecognized `--library` value is a hard refusal that scans nothing, where `enrich_metadata`
   silently falls through to the whole library — a typo must never quietly widen the blast radius of
   a `--apply` run. (b) Report filenames de-collide with a `-1`/`-2` suffix (the same idiom
   `RollbackJournal._preserve_leftover` uses), because a second `--apply` inside the same second —
   which is exactly what the idempotent no-op re-run does — otherwise **silently overwrote the first
   run's audit trail**. I found this in verification, not by inspection; the first version had the
   bug.

5. **Reporting counts all referencing entries, scoping counts only in-scope ones.** A folder enters
   the candidate list if ≥1 referencing entry passes the id/`--library` filter, but the printed count
   and `remote_bearing` are computed over *every* referencing entry, because `cmd_rename_folder`
   re-points every descendant regardless of the filter. Reporting only the in-scope subset would
   understate what a rename actually touches.

## Exact CLI surface and report shape (locked by this step — downstream consumers)

```
python main.py migrate_provider_tokens [id_or_prefix] [--apply] [--library movies|series|anime|others]
```

- `--library` accepts `movies` | `series` | `anime` | `others` (also `other`, the `CATEGORY_ROOTS`
  spelling). Anything else, or a missing value → prints `❌ Unknown --library …`, **returns `None`**,
  scans nothing.
- Dry-run is the default. Per candidate it prints two lines
  (`🔁 <old abs path>` / `-> <new basename>   (<n> entr(y/ies), <id>|ancestor of <n> entr(y/ies), e.g. <id>[, REMOTE-BEARING])`),
  then the orphan block (suppressed when an `id_or_prefix` scope is given), then exactly:
  `   > scanned N, would-rename M, already-canonical K, remote-bearing R`.
- `--apply` prints `   > scanned N, renamed M, already-canonical K, remote-bearing R, errors E`
  followed by `   > report: <path>`.

**Return value** (both modes), useful to Step 7:

```python
{"mode": "dry-run"|"apply", "scanned": int, "renamed": [record, ...],
 "already_canonical": int, "errors": [record + {"error": str}, ...],
 "orphans": [abs_path, ...], "report_path": str|None}
```

`scanned` counts **directories examined on disk**. `already_canonical` counts directories whose
basename carries only canonical TMDB token(s). In dry-run `renamed` is the **plan** (nothing was
executed) and `report_path` is `None`.

**Report file** — `--apply` only, always written (even for a 0-rename run), at
`<LOCAL_ROOT>/migration_reports/token_format_<YYYYMMDD>T<HHMMSS>Z.json`
(basic-format ISO 8601; `:` is illegal in a Windows filename — a colon-bearing extended-format name
cannot be created on this platform, so the basic form is the only workable reading of "UTC ISO 8601").
Same-second collisions get `…Z-1.json`, `…Z-2.json`.

```json
{"scanned": 9,
 "renamed": [{"id_or_note": "ancestor of 2 entr(y/ies), e.g. tv-friends-s01",
              "old_folder": "…\\Series\\Friends (1994) {tmdb-1668}",
              "new_folder": "…\\Series\\Friends (1994) [tmdbid-1668]",
              "remote_bearing": true}],
 "already_canonical": 2,
 "errors": [],
 "orphans": ["…\\Series\\Orphan Show {tmdb-999}"]}
```

The four locked keys are present with the locked names and the locked per-item shape;
**`orphans` is one additive key**, required by this approach's audit deliverable, appended last.
`errors` items are a `renamed` record plus an `"error"` string.

## How the four mechanisms work

- **Discovery.** `for dirpath, dirs, _files in os.walk(root)`, `dirs[:]` pruned against
  `_RECLAIM_EXCLUDE_DIRS` *before* being iterated, then each `name in dirs` tested. Iterating `dirs`
  rather than `dirpath` visits every directory under a root exactly once and never tests the category
  root itself. Pruning before iteration means `_parts` / `checksums` / `restore` are neither descended
  into nor tested (verified: a planted `_parts/Bogus {tmdb-777}` is invisible to the run).
- **Cross-reference.** `_collect_folder_descendants(library, folder)` → `[]` means orphan, otherwise
  the entries a rename would re-point. It is alias-safe by construction (skips `multi_ep_alias`,
  which has no `folder_path`; includes `season_map`, which does), so this command inherits the
  PR #21 crash-class guard rather than re-deriving it.
- **Dedup.** Candidates live in a dict keyed by `_norm_path(folder)` — one rename per physical folder
  however many entries sit under it (the Friends parent is the ancestor of 244 entries on the real
  library and is renamed once).
- **Deepest-first.** `sorted(..., key=lambda c: (-c["_depth"], c["old_folder"]))` where `_depth` is
  `len(_norm_path(folder).split(os.sep))`. Renaming an ancestor first would move a not-yet-processed
  descendant out from under the path recorded during the walk, leaving a stale path that no longer
  resolves; leaf-most-first guarantees each folder is renamed while its own path is still the one on
  disk, and `cmd_rename_folder`'s cascade then re-points the already-renamed descendants under the
  new ancestor prefix. The rationale is in the code as a comment. Path is the tie-break so runs are
  deterministic.
- **Idempotence / resumability.** No new journal, no state file. A re-run re-detects migrated folders
  as already-canonical (`_stale_tmdb_tokens` returns `[]`) and skips them.

## Acceptance bullets — verified results

| Bullet | Result |
|---|---|
| Dry-run never calls `cmd_rename_folder`, writes no report | **PASS** — spy records 0 calls; tree + all 4 library files byte-identical before/after; `migration_reports/` does not exist |
| Friends shape: only the ancestor renamed, child leaf name untouched, child `folder_path` reflects the new prefix | **PASS** — `Friends (1994) [tmdbid-1668]/Season 01 [tmdbid-1668]`; both the `season_map` and the episode leaf `folder_path` re-pointed by the cascade; the `uid` sidecar moved with the folder |
| `[rartv]` byte-identical, only the tmdb portion migrated | **PASS** — `Peaky.Blinders.S06.1080p.FLUX[rartv] [tvdbid-266189] {tmdb-60574}` → `…FLUX[rartv] [tvdbid-266189] [tmdbid-60574]` (coexisting `[tvdbid-…]` also untouched) |
| `--apply` twice is a no-op the second time | **PASS** — 2nd run: 0 renamed, 0 errors, `already_canonical` 8; media tree and libraries byte-identical; a following dry-run reports 0 candidates |
| hash/status/uploaded/split_info untouched | **PASS** — every entry dict compared field-by-field before/after with `folder_path` popped; identical for all three seeded shapes, incl. an `archived`/`uploaded`/`split_info` entry |
| **MULTI-LEVEL** nested ancestors in one run | **PASS** — `Nested Show {tmdb-1}/Season 01 {tmdb-1}` → `Nested Show [tmdbid-1]/Season 01 [tmdbid-1]`; both the `season_map` and the leaf `folder_path` equal the fully migrated path. Plan order asserted monotonically non-increasing in depth |
| Orphan reported, not renamed | **PASS** — `Orphan Show {tmdb-999}` still on disk, present in `result["orphans"]` and in the report |
| `remote_bearing` correct | **PASS** — true only for the `status="archived"` / `uploaded=True` Friends ancestor; false for the local-only ones |

Extra coverage beyond the bullets: `--library series|others` scoping (including reaching the Sports
root via the `others`→`other` mapping), unknown/missing `--library` refusal, id-prefix scoping,
orphan-print suppression under an id scope, pruned working directories, alias entry left untouched,
report key order and filename legality, and the CLI dispatch branch exercised through
`runpy.run_path(main.py, run_name="__main__")` with sandboxed constants.

## Tests run

Fixture harness (throwaway temp dir; `LOCAL_ROOT` + all four `LIBRARY_*` re-pointed on **both**
`mvcommon` and `main` with a `C:\Media` hard guard; script lived outside the repo and was deleted
after the run):

```
70/70 checks passed
```

Real console output of the dry-run over the fixture tree (temp path elided):

```
=== MIGRATE PROVIDER TOKENS (DRY-RUN) ===
   > target format: [tmdbid-<id>]
   > walking: <TMP>\Media\Movies
   > walking: <TMP>\Media\Series
   > walking: <TMP>\Media\Anime
   > walking: <TMP>\Media\Sports
   🔁 <TMP>\Media\Series\Nested Show {tmdb-1}\Season 01 {tmdb-1}
      -> Season 01 [tmdbid-1]   (2 entr(y/ies), tv-nested-s01)
   🔁 <TMP>\Media\Anime\Steins Gate {tmdb-42}
      -> Steins Gate [tmdbid-42]   (1 entr(y/ies), ani-steins)
   🔁 <TMP>\Media\Series\Friends (1994) {tmdb-1668}
      -> Friends (1994) [tmdbid-1668]   (ancestor of 2 entr(y/ies), e.g. tv-friends-s01, REMOTE-BEARING)
   🔁 <TMP>\Media\Series\Nested Show {tmdb-1}
      -> Nested Show [tmdbid-1]   (ancestor of 2 entr(y/ies), e.g. tv-nested-s01)
   🔁 <TMP>\Media\Series\Peaky.Blinders.S06.1080p.FLUX[rartv] [tvdbid-266189] {tmdb-60574}
      -> Peaky.Blinders.S06.1080p.FLUX[rartv] [tvdbid-266189] [tmdbid-60574]   (1 entr(y/ies), tv-peaky-s06e01)
   🔁 <TMP>\Media\Sports\Match Day {tmdb-7}
      -> Match Day [tmdbid-7]   (1 entr(y/ies), oth-match)
   ⚠️ 1 folder(s) carry an old-style token but are referenced by NO library entry — reported only, never renamed:
        - <TMP>\Media\Series\Orphan Show {tmdb-999}
   > scanned 9, would-rename 6, already-canonical 2, remote-bearing 1
   (dry-run: nothing was written — re-run with --apply to perform it.)
```

Required suites:

```
$ python -m pytest tests/test_rename_folder.py tests/test_provider_tokens.py -q
........................                                                 [100%]
24 passed in 0.69s

$ python -m pytest tests/test_rename_folder.py tests/test_provider_tokens.py tests/test_entry_schema_guard.py -q
............................                                             [100%]
28 passed in 0.84s

$ python -m pytest tests/smoke -q
FAILED tests/smoke/test_smoke_all_commands.py::TestPrepPushRepEnrich::test_prep_push_rep_enrich_movie_round_trip
FAILED tests/smoke/test_smoke_all_commands.py::TestPrepPushRepEnrich::test_prep_push_rep_season_enrich_stamps_show_folder
2 failed, 78 passed, 1 warning in 12.56s
```

Both smoke failures are the two pre-existing, Step-11-owned ones (they assert the old
`SMK.S01.2020 {tmdb-424242}` stamp that Step 3 already replaced). Nothing else in smoke is red.

Full suite, for completeness:

```
$ python -m pytest tests -q
41 failed, 862 passed, 1 warning in 85.04s

$ python -m pytest tests -q | grep ^FAILED | sed 's/::.*//' | sort | uniq -c
      2 FAILED tests/smoke/test_smoke_all_commands.py
     16 FAILED tests/test_enrich_metadata.py
      7 FAILED tests/test_prep_push_rep_enrich.py
     13 FAILED tests/test_prep_push_rep_season_enrich.py
      3 FAILED tests/test_web_datafns.py
```

Every failing file is one of the four Step 9 owns plus the two Step 11 owns — the exact set the plan
predicts, all format-literal assertions (`AssertionError: assert '{tmdb-' in 'Darkriver (2024)
[tmdbid-0000000]'`). No failure outside that set, and this change adds only a new function plus a new
dispatch branch, which none of those files reach.

## What the full-tree walk costs, and what it buys

**Cost.** It is `O(directories under LOCAL_ROOT)` regardless of how much work there is. On the real
library that is a stat-walk of the whole media tree to find one folder — on the order of seconds,
dominated by directory enumeration; `os.walk` also enumerates filenames it never uses. The
entry-driven alternative would touch roughly `entries × depth` paths with no directory listing at
all and would be meaningfully faster on a cold cache over a large tree. **Every `--apply` run pays
this cost twice in practice** (the dry-run review, then the apply). It also means `--library series`
is the user's only lever for shortening the scan.

**What it buys.** (1) The candidate set is derived from the disk, so a stale folder is found whether
or not the library implies it — including the ancestor case that is the entire remaining migration,
and including a shape nobody has thought of yet. (2) The orphan audit exists at all: a stray
`Orphan Show {tmdb-999}` that no entry references is invisible to a library-derived walk, and it is
precisely the kind of residue a half-finished third-party migration leaves behind. (3)
`already_canonical` is a real disk statistic (~1400 on the user's library), so the summary line is a
genuine post-migration audit rather than a restatement of the library. On a ~1600-entry library the
runtime difference is not the deciding factor; the audit is.

## Weaknesses (honest)

- **`remote_bearing` ignores extras.** It reads the top-level entry's `uploaded`/`status`, per the
  literal spec. A title whose main content is local but whose `extras` items have been pushed will
  report `remote_bearing: false`. I chose spec-literal over broader because the field's contract is
  entry-level and Step 7 tests against that contract, but it is an under-report and I would rather
  flag it than hide it.
- **No coverage of a mid-run interruption at the process level.** Resumability is argued from
  `cmd_rename_folder`'s own crash-safety plus the re-detection path, and I proved the *re-run* half
  (a second `--apply` is a clean no-op and a partially-migrated tree continues correctly, since each
  folder is judged independently). I did **not** kill a process mid-loop. Likewise the
  `RollbackHardFail` branch (stop the loop, still write the report, re-raise) is reasoned and typed
  but **not exercised by a test** — no fault was injected into `save_library` past the PONR.
- **The no-op `--apply` re-run still writes a report file.** Defensible (the report is an audit
  artifact, not a mutation of library or media) but it does mean "no-op" is not literally true on
  disk, and `migration_reports/` accumulates one file per `--apply` invocation with no pruning.
- **`-1` collision suffix sorts *before* the unsuffixed name** lexicographically (`-` < `.`), so in
  the rare same-second case a plain `dir` listing is not in chronological order. I kept `-` to match
  `_preserve_leftover`'s existing idiom rather than optimize the listing.
- **`scanned` is a directory count, not an entry count.** It is the honest number for a disk walk but
  is not comparable to what an entry-driven implementation would print under the same label, and the
  summary line does not spell out the unit.
- **Symlinks / junctions / reparse points are not considered.** `os.walk` does not follow directory
  symlinks by default, so a junction into the media tree would be listed as a directory (and could
  be a candidate) but not descended. No media library in this project uses them, and I did not add
  speculative handling.
- **Orphan printing is suppressed under an `id_or_prefix` scope** (they stay in the returned dict and
  the report). That is a judgement call about console noise, not a property anyone asked for.
- The report write is not atomic (plain `open`/`json.dump`, no temp+`os.replace`). It is a fresh
  file in its own directory, written after all mutation is finished, so a torn write loses only that
  run's audit copy — but it is weaker than `save_library`'s durability.

## Confidence

**High** on the specified behaviour: every acceptance bullet including the multi-level nested-ancestor
case is proven against a fixture tree with real before/after byte comparisons of both the media tree
and all four library files, and the two required suites plus the entry-schema guard are green with
smoke showing only its two known, externally-owned failures.

**Medium** on two specific things. The `RollbackHardFail` stop-and-re-raise path is reasoned but
untested — if a real `--apply` crosses a PONR I believe it stops cleanly, writes the report and
re-raises with `cmd_rename_folder`'s own `resume_cmd` intact, but I have not injected that fault.
And the extras-blind `remote_bearing` is a deliberate under-report that a reviewer may judge
differently than I did. The same-second report overwrite I found and fixed during verification is a
reminder that this command's failure modes are quiet ones; the dry-run-by-default surface is what
keeps that from mattering before `--apply`.

## Assumptions (explicit)

1. "UTC ISO 8601 timestamp" in a filename means the **basic** format (`20260921T140305Z`) — the
   extended form's colons cannot exist in a Windows filename.
2. `--library others` maps to the `CATEGORY_ROOTS` key `"other"` and the `Sports` root; `other` is
   accepted as a synonym.
3. `--library` narrows **both** the walked roots and the entry scope; they agree for any
   conventionally-placed folder.
4. Adding `orphans` to the report is in the spirit of the locked shape (additive, last) rather than a
   violation of it — this approach's audit output has nowhere else to live.
5. The report is written on every `--apply`, including a 0-rename run.
6. A folder is a candidate on its **own basename** only; a parent's stale token never makes a
   canonical child a candidate (the Friends child is correctly left alone).
7. `scanned` counts directories, not entries.
8. Multiple stale tmdb tokens in one name are each re-spelled in place; none is dropped or merged
   (no folder in the real library is double-stamped, so this never triggers there).
9. `_collect_folder_descendants` returning `[]` is a sufficient and safe definition of "orphan",
   because it is the same predicate `cmd_rename_folder` refuses on.
