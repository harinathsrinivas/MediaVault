# Task: Multi-episode combined-file support (one .mkv covers E19 + E20)

Suggested branch: feat/multi-ep-combined-file

## Context
A single physical file such as
`Battlestar.Galactica.S04E19E20.Daybreak.2009.1080p...mkv` covers two episodes
(19 and 20), but `cmd_prep_season`'s parser (`main.py:1030`,
`re.search(r"[sS]\d+[eE](\d+)", filename)`) only captures the FIRST episode
number, so only `...e19` is registered. `e20` never enters the library, so
`prep_push_rep_season ... episodes 18-20` silently skips ep20 and a direct fetch
of ep20 fails. We add transparent "combined-episode" handling modeled on the
existing decimal half-episode treatment (`e16.5`), so `e20` resolves to the same
physical file as `e19` without duplicating data or touching the rollback core.

## Goal
After `cmd_prep_season` on a folder containing an `S04E19E20` file:
- Both `...e19` and `...e20` keys exist in the merged library and both are in the
  season_map's `children`.
- `...e20` is a thin alias entry (`type == "multi_ep_alias"`, `alias_of` ->
  `...e19`) — zero duplicated `hash`/`tech_spec`/`split_info`.
- Range filter `episodes 18-20` and `episodes 20-20` both include the file, and a
  push/replace processes the underlying file EXACTLY ONCE (no double push).
- `fetch ... episodes 20-20` and `fetch ... episodes 19-19` both queue the same
  physical file.
- Every existing single-episode flow (canonical `S04E19`, dotted-title
  `S03E20.6`, anime `16x05.5`) behaves byte-for-byte as before (regression-free).
- `cmd_push`, `cmd_replace`, `cmd_prep`, `RollbackJournal`, `RollbackHardFail`,
  and all PONR markers are UNCHANGED.

## Files affected
- `main.py` — `cmd_prep_season` (parser extension + alias creation); a new
  `_resolve_alias` helper; alias-aware filtering in `cmd_prep_push_rep_season`
  (disk pre-flight loop + main loop + `_season_resume_cmd`) and in the
  `cmd_push_group` / `cmd_replace_group` range filter.
- `mainfetch.py` — `resolve_targets` resolves aliases to their primary entry
  before building the download queue.
- `ARCHITECTURE.md` — document the new `multi_ep_alias` entry type and combined
  episode behavior in §6.3 (entry schemas) and the `cmd_prep_season` notes (§7.8).
- `improvements_tierE.md` — (decision 4) optionally add `IMP-E13`.
- `tests/test_prep_season_episode_parse.py` — new tests F–K.

## Approach
Use **Approach A (thin alias)**. `cmd_prep_season` keeps calling `cmd_prep` ONCE
for the primary (lowest) episode number, so the heavy lifting (hash, tech_spec,
parent-link, sidecars, rollback journaling) is unchanged. It then detects the
extra episode numbers in the same `S04E19E20` cluster and writes a tiny alias
entry per secondary episode:

```jsonc
"tv-...-s04e20": {
  "type":     "multi_ep_alias",
  "alias_of": "tv-...-s04e19",
  "parent_id":"tv-...-s04"
}
```

The alias is appended to the season_map's `children` (so range filters and
`resolve_targets` naturally see it). A single helper `_resolve_alias(lib, mid)`
returns the real (primary) entry for an alias or the entry itself otherwise.

Consumers behave differently by intent:
- **Fetch** (`resolve_targets`): resolve alias -> primary entry so the download
  queue uses the primary's `filename`/`hash`/`split_info`. Two episodes in range
  resolving to the same primary collapse to one queue item (dedup by id).
- **Push/replace season loop** (`cmd_prep_push_rep_season`): the alias points at
  the SAME physical file as its primary, and the primary is already in
  `children`. So we DROP alias ids from `target_ids` before the disk pre-flight
  and the push loop. The file is pushed/replaced exactly once via its primary.
  This also avoids touching the unchanged `cmd_push` inner logic (an alias has no
  `filename`, so it must never reach it).
- **`cmd_push_group` / `cmd_replace_group`**: same drop-aliases-when-primary-
  present rule in the range filter.

This is purely additive. No PONR, journal, or `cmd_push`/`cmd_replace`/`cmd_prep`
internals change.

## Steps

- [ ] 1. [model: opus] [effort: high] Extend `cmd_prep_season` to detect combined-episode files and create thin alias entries.
  - Files: `main.py` (`cmd_prep_season`, lines 1014–1058)
  - Details:
    - After the existing Strategy-1/Strategy-2 block computes `ep_num` for a TV
      file, add a combined-episode detector that runs ONLY for the SxxExx case
      (NOT anime): test the filename against
      `re.search(r"[sS]\d+(?:[eE]\d+){2,}", filename)`. If it matches, extract
      ALL episode numbers from that matched cluster with
      `re.findall(r"[eE](\d+)", match.group(0))`. The FIRST number is the primary
      (already used as `ep_num`); the rest are secondaries.
    - Keep the current single `cmd_prep(full_id, full_path, parent_id=base_id)`
      call for the PRIMARY unchanged. Only AFTER it returns truthy, load the
      library, and for each secondary episode `s` build
      `alias_id = f"{base_id}e{s}"`, and if `alias_id not in library` create:
      `{"type": "multi_ep_alias", "alias_of": full_id, "parent_id": base_id}`.
      Append `alias_id` to `library[base_id]["children"]`, re-`sort()`, set
      `total_episodes = len(children)`, then `save_library(library)` ONCE after
      the loop over secondaries.
    - Do NOT create an alias if its primary's `cmd_prep` was skipped/failed
      (e.g. dummy file) — guard on the return value and on the primary key being
      present in the library.
    - Decision 2: write the detector to support 3+ episodes generically
      (`E17E18E19` yields primary 17, aliases 18 and 19). Implementation handles
      N>=2 with no extra code.
    - Use a normalization for the alias episode token consistent with the primary
      key: the primary key is `f"{base_id}e{ep_num}"` where `ep_num` is the raw
      captured digit string (e.g. `"19"`), so aliases use the raw captured digit
      strings too (`"20"`), giving `...e20`. Do not zero-pad or reformat.
  - Acceptance: a unit/functional test (Step 7, Test F/G) confirms both `...e19`
    and `...e20` exist after `cmd_prep_season`, `...e20` has
    `type=="multi_ep_alias"` and `alias_of=="...e19"`, both are in `children`,
    and the canonical-single-episode tests A/B/C still pass.

- [ ] 2. [model: sonnet] [effort: medium] Add the `_resolve_alias` helper in `main.py`.
  - Files: `main.py` (new module-level helper, place near other small helpers,
    e.g. just above `cmd_push_group`)
  - Details:
    - Implement `def _resolve_alias(lib, mid):` returning `(real_id, entry)` where
      if `lib.get(mid, {}).get("type") == "multi_ep_alias"` it follows
      `entry["alias_of"]` once to the primary (return the primary id + primary
      entry); otherwise returns `(mid, lib[mid])`. If the alias target is missing
      from `lib`, return `(mid, alias_entry)` so callers can detect/skip rather
      than KeyError.
    - Single-hop only (aliases never point at other aliases by construction); do
      not add multi-hop chasing.
  - Acceptance: `_resolve_alias(lib, "...e20")` returns `("...e19", <e19 entry>)`;
    `_resolve_alias(lib, "...e19")` returns `("...e19", <e19 entry>)`. Covered by
    Step 7 (used indirectly by Tests H/I) and a direct assertion.

- [ ] 3. [model: opus] [effort: high] Make `cmd_prep_push_rep_season` alias-aware (drop aliases whose primary is present, before disk pre-flight and the push loop).
  - Files: `main.py` (`cmd_prep_push_rep_season`, lines 2474–2622)
  - Details:
    - After `target_ids` is computed/range-filtered (the existing block at
      2488–2507), add a de-alias pass: build `present = set(target_ids)`; rebuild
      `target_ids` keeping every id that is NOT a `multi_ep_alias`, PLUS any alias
      whose `alias_of` is NOT already in `present` (orphan-safety: a lone alias
      with primary out of range should still process via its primary — resolve it
      to the primary id and include the primary instead, deduped). Concretely:
      iterate the filtered ids; for each, `real_id, _ = _resolve_alias(library,
      mid)`; collect `real_id` into an order-preserving de-duplicated list. This
      yields a list of PRIMARY ids only, each once. Re-load nothing new — reuse
      the already-loaded `library`.
    - This single transform feeds BOTH the disk pre-flight loop (2543) and the
      main processing loop (2583), so neither loop ever sees an alias entry
      (which has no `filename`) — `cmd_push`/`cmd_replace` internals stay
      untouched.
    - Edge: range `20-20` (only the alias matched the filter) must still process
      the underlying file — the resolve-to-primary step pulls in `...e19`
      automatically. Confirm `...e19`'s file is what gets pushed.
    - Do NOT alter the PONR, `RollbackHardFail` handling, `_season_resume_cmd`
      call sites, or any `cmd_push`/`cmd_replace` arguments.
  - Acceptance: with a seeded library containing `e19`(real)+`e20`(alias), an
    `episodes 18-20` run resolves `target_ids` to a single primary `...e19`
    (assert via a unit test on the de-alias transform, or by asserting the push
    loop is entered once); `episodes 20-20` also resolves to `[...e19]`.

- [ ] 4. [model: opus] [effort: high] Update `_season_resume_cmd` so alias ids don't distort the resume range.
  - Files: `main.py` (`_season_resume_cmd`, lines 2512–2531)
  - Details:
    - Because Step 3 makes `target_ids` contain only primary ids, `_season_resume_cmd`
      already iterates primaries — so the simplest correct change is: NONE to the
      episode-number extraction loop itself. BUT add a defensive resolve: when
      building `ep_nums`, if a remaining id were ever an alias, resolve it to its
      primary first (`real_id, _ = _resolve_alias(library, rid)`) and use the
      primary's episode segment. This keeps the resume hint emitting the PRIMARY
      episode number, satisfying decision 5 (a `19-20` combined file resumes as
      part of `...19...`, not as a standalone `20`).
    - Verify the produced resume command still names an existing command
      (`prep_push_rep_season`) and an existing range — the `RollbackHardFail`
      `resume_cmd` contract (CLAUDE.md change-gate) is preserved because we only
      change which episode NUMBERS are listed, never the command shape.
  - Acceptance: a unit test calling `_season_resume_cmd` after a failure at the
    combined item emits `episodes 19-...` (primary), never `episodes 20-20` for
    the alias alone.

- [ ] 5. [model: sonnet] [effort: medium] Make `cmd_push_group` / `cmd_replace_group` range filter alias-aware.
  - Files: `main.py` (range filter at lines 1597–1619; confirm both
    `cmd_push_group` and `cmd_replace_group` share this filter — search the file
    and apply to each that does)
  - Details:
    - After the range-filter produces `filtered_ids`, apply the same
      resolve-to-primary + order-preserving dedup transform as Step 3 (reuse
      `_resolve_alias`) so the group push/replace loop processes the underlying
      file once. If `cmd_replace_group` has a separate filter block, apply the
      same transform there.
    - Keep behavior identical when no alias is present (the transform is a no-op
      for non-alias ids).
  - Acceptance: a seeded `push_group ... episodes 18-20` over a library with an
    alias resolves to a single primary id; existing non-alias group tests (if
    any) unchanged. (If there is no existing group test harness, rely on the
    unit-level transform assertion.)

- [ ] 6. [model: sonnet] [effort: medium] Resolve aliases in `mainfetch.resolve_targets` before building the download queue.
  - Files: `mainfetch.py` (`resolve_targets`, lines 333–368)
  - Details:
    - Add a local single-hop resolver mirroring `main._resolve_alias` (mainfetch
      must not import main; duplicate the ~4-line helper or inline it). In the
      season_map branch, after `children_ids` is range-filtered, map each child
      through the resolver, then build `target_entries` from the resolved primary
      ids with order-preserving dedup (so `19-20` over a combined file yields ONE
      queue entry, not two identical downloads).
    - In the single-id branch (`else`), if the looked-up entry is a
      `multi_ep_alias`, resolve to and return the primary entry (so `fetch ...e20`
      directly downloads the combined file).
    - Do not change hash routing, queue shape, or the chunk/single-file branches
      in `build_download_queue`.
  - Acceptance: Step 7 fetch-path reasoning holds — a manual verification
    (Verification section) confirms `fetch episodes 20-20` and
    `fetch episodes 19-19` both queue the same physical file. If a `mock_fetch`
    harness exists, add a small assertion; otherwise this is covered by the
    manual checklist.

- [ ] 7. [model: sonnet] [effort: medium] Add tests F–K to `tests/test_prep_season_episode_parse.py`.
  - Files: `tests/test_prep_season_episode_parse.py`
  - Details:
    - Use the existing `sandbox`, `stub_tech_specs`, `tmp_path` fixtures exactly
      as Tests A–C do (copy/write a >210_000-byte fake .mkv into a `tmp_path`
      subfolder, init the three sandbox library files to `{}`).
    - "Never touch real C:\\Media files or real library_*.json."
    - "Run `pytest -q` and fix failures before marking the step done."
    - Test F: filename `Battlestar.Galactica.S04E19E20.Daybreak...mkv`,
      `cmd_prep_season("tv-en-2009-bsg-s04", folder)` -> both
      `tv-en-2009-bsg-s04e19` and `...e20` present in `load_library()`, and both
      in `library["tv-en-2009-bsg-s04"]["children"]`.
    - Test G: `...e20` entry has `type == "multi_ep_alias"` and
      `alias_of == "tv-en-2009-bsg-s04e19"`; the primary `...e19` is a normal leaf
      (has `hash`/`tech_spec`, no `type` key or not `multi_ep_alias`).
    - Test H: seed a library dict with a real `...e19` leaf + `...e20` alias under
      a season_map, then assert the Step-3/Step-5 de-alias transform for range
      `18-20` resolves to `["...e19"]` (a single primary). Prefer testing the
      pure transform — if Step 3 exposes a small helper (e.g. `_dealias_ids(lib,
      ids)`), call it directly; otherwise replicate the documented transform
      inline like Tests D/E do for the range filter.
    - Test I: same seeded library, range `20-20` -> de-alias transform still
      yields `["...e19"]` (alias-only range still pulls the primary).
    - Test J (regression guard): `Battlestar.Galactica.S04E19.1080p...mkv` (single
      E) -> only `...e19` created, NO `...e20`, and `...e19` is NOT a
      `multi_ep_alias`.
    - Test K (generalization, decision 2 = yes): `...S04E17E18E19...mkv` ->
      `...e17` is the real leaf; `...e18` and `...e19` are aliases of `...e17`;
      all three in `children`.
  - Acceptance: `pytest -q` green; Tests A–E (existing) still pass.

- [ ] 8. [model: haiku] [effort: low] Document the new `multi_ep_alias` entry type and combined-episode behavior in `ARCHITECTURE.md`.
  - Files: `ARCHITECTURE.md` (§6.3 entry schemas — add a third entry type; §7.8
    `cmd_prep_season` bullet — note combined-episode detection and alias
    creation)
  - Details: add a short schema block for `multi_ep_alias`
    (`type`/`alias_of`/`parent_id` only), state it carries no `hash`/`filename`/
    `tech_spec`/`split_info`, is resolved to its primary by `_resolve_alias`
    (main) / the local resolver (mainfetch), and is dropped-to-primary in the
    push/replace/group loops so the underlying file is processed once. One
    sentence in §7.8 that `cmd_prep_season` detects `S\d+(?:E\d+){2,}` and emits
    aliases for episodes beyond the first. Do not restructure surrounding text.
  - Acceptance: §6.3 lists three entry types; the `cmd_prep_season` description
    mentions combined-episode aliasing. No unrelated edits.

## Open Decisions (recommended choice marked; confirm before/with implementation)

1. **Alias model — A (thin alias) vs B (`multi_ep` field, no second key) vs
   C (two full entries).** RECOMMEND **A**. Zero data duplication; secondary key
   exists so range filters and `resolve_targets` see it with no scan; minimal
   core-logic touch (the unchanged `cmd_push`/`cmd_replace` never see aliases
   because the season/group loops drop them to the primary). B requires a linear
   `multi_ep`-containment scan in BOTH the range filter and fetch lookup-miss
   path (more touchpoints, easy to miss one). C duplicates `hash`/`tech_spec`/
   `split_info` and leaves `e20`'s status stale after a `replace` on `e19`.

   | Dim | A thin alias | B multi_ep field | C full duplicate |
   |---|---|---|---|
   | Secondary key exists | yes | no | yes |
   | Data duplicated | none | none | hash+tech_spec+split_info |
   | Range filter change | drop-to-primary | scan for containment | dedup by filename |
   | Fetch change | resolve alias | scan on lookup-miss | none |
   | Status stays in sync | yes (single source) | yes | NO (e20 stale) |
   | Core-logic touch | minimal | medium | low but risky dedup |
   | Recommended | YES | no | no |

2. **Generalize to 3+ episodes (`E17E18E19`)?** RECOMMEND **yes, in scope** — the
   detector and alias loop handle N>=2 with no extra code (Test K covers it).

3. **Naming term.** RECOMMEND **"combined episode"** for prose/tests/branch and
   `multi_ep_alias` for the on-disk `type` value (keeps the schema token terse and
   greppable). Confirm so code, docs, and test names stay consistent.

4. **IMP code.** RECOMMEND **track as `IMP-E13`** in `improvements_tierE.md`
   (Tier E = integration/workflow; combined-file handling fits) so the PR title
   carries a code per CLAUDE.md rule. If the user prefers no code, ship without
   one and the PR title drops the `— IMP-E13` suffix. (If E13 is taken by the
   time of implementation, use the next free E number — verify before writing.)

5. **Resume range hint for an alias.** RECOMMEND **emit the primary's number**
   (so a `19-20` combined file resumes inside `...19...`, never as a standalone
   `20-20`). Step 3 already collapses `target_ids` to primaries, so
   `_season_resume_cmd` naturally lists `19`; Step 4 adds a defensive resolve.

## Risks and edge cases
- **Alias must never reach `cmd_push`/`cmd_replace`/`cmd_prep`.** A thin alias has
  no `filename`/`folder_path`/`hash`; the push pre-flight loop (`main.py:2546`)
  and main loop (`2602`) dereference `entry["filename"]` and would KeyError.
  Step 3/5's drop-to-primary transform is the guard — verify it runs BEFORE both
  loops and before the disk pre-flight.
- **`cmd_prep` short-circuit.** If the combined file is a dummy or already
  uploaded, `cmd_prep` returns True having created nothing meaningful; Step 1
  must only create aliases when the primary key actually exists as a real leaf in
  the library (guard on presence + not-already-archived), else an alias could
  point at a non-existent/placeholder primary.
- **Re-running `cmd_prep_season`** (idempotency): aliases are created only
  `if alias_id not in library`, and `children` append is guarded by membership —
  re-prep must not duplicate children or overwrite a primary that has since been
  pushed. Mirror `cmd_prep`'s existing membership guards.
- **Regex over-match.** `S\d+(?:E\d+){2,}` must NOT fire on a single `S04E19`
  (Test J) nor on dotted titles like `S03E20.6` (the `.6` is not `E6`, so
  `(?:E\d+){2,}` won't match — but verify against Test A's filename). Anime
  `16x05.5` uses the `x` convention and is excluded by scoping the combined
  detector to the SxxExx (TV) branch only.
- **Range float comparison unchanged.** Alias keys like `...e20` flow through the
  existing `^[eExX]?(\d+(?:\.\d+)?)$` filter (Test D-style) and yield `20.0`
  before being de-aliased — order of operations: filter first, THEN drop to
  primary, so a `20-20` range still includes the alias and then resolves it.
- **mainfetch cannot import main** — the resolver helper is duplicated (~4 lines).
  Keep the two definitions semantically identical; note this in a comment.
- **Sort/scan commands** (`cmd_sort`, `cmd_scan_unprepped`, `cmd_local_status`)
  iterate leaves and skip `season_map` by `type`. They must also skip
  `multi_ep_alias` (no `size_bytes`/`filename`). VERIFY: out-of-scope to fully
  rework, but if any of these would crash on an alias entry, add a `type ==
  "multi_ep_alias"` skip alongside the existing `season_map` skip. Flag to user
  if encountered.

## Verification
After all steps (PowerShell):

```powershell
# Unit/functional suite (must be green; Tests A–E unchanged, F–K new)
pytest -q

# Just the affected file, verbose
pytest tests/test_prep_season_episode_parse.py -v
```

Manual end-to-end (NEVER touch C:\Media — copy into a temp folder, delete after):

```
# 1. Copy ONLY the Battlestar S04E19E20 file into a fresh temp folder
#    (e.g. %TEMP%\mv_multiep\bsg_s04\) — do not point at C:\Media.
# 2. python main.py prep_season tv-en-2009-bsg-s04 "<temp folder>"
#    -> expect "...e19" leaf + "...e20" alias both reported / in children.
# 3. Inspect the sandboxed/temp library JSON: confirm
#    "...e20": {"type":"multi_ep_alias","alias_of":"...e19","parent_id":"...s04"}.
# 4. python main.py push_group tv-en-2009-bsg-s04 SIZE_GB 9 episodes 18-20
#    (against a fake/mock device or dry inspection) -> the file is processed ONCE
#    via ...e19; ...e20 is not pushed separately.
# 5. python mainfetch.py fetch tv-en-2009-bsg-s04 episodes 19-19  -> queues the file.
# 6. python mainfetch.py fetch tv-en-2009-bsg-s04 episodes 20-20  -> queues the SAME file.
# 7. Delete the temp folder.
```

## Out of scope
- Any change to `cmd_push`, `cmd_replace`, `cmd_prep` internals, the
  `RollbackJournal` / `recover_journal` / `RollbackHardFail` mechanism, PONR
  markers, journal format/durability, or the season resume-message contract
  beyond the additive episode-number resolution in Step 4.
- Editing or moving real media files; all tests use `tmp_path` + sandbox.
- A migration/backfill command to retro-create aliases for combined files already
  in the live library (could be a follow-up `IMP` if the user wants it).
- Splitting one combined file into two SEPARATE physical files.
- Reworking `cmd_sort` / `cmd_scan_unprepped` / `cmd_local_status` beyond a
  minimal alias-skip guard if (and only if) one of them would crash on an alias.

---

## PR metadata
- Branch: `feat/multi-ep-combined-file`
- PR title: `feat: multi-episode combined-file support (E19E20) — IMP-E13`
  (drop `— IMP-E13` if decision 4 resolves to "no IMP code")
- PR target: `main`
