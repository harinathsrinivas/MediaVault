# Decision: Step 1 — IMP-E12 web console read-only data layer (five pure functions in `main.py`)

## Outcome
Winner: Candidate A
Branch: `feature/web_console__cand_a`

## Step requirements
Five module-level pure functions added to `main.py`, FIXED OUTPUT CONTRACT (identical across candidates — they differ ONLY in internal scan/index/de-dup strategy):

1. `classify_entry_state(entry, on_disk_real)` → `"UNPREPPED" | "LOCAL_NOT_PUSHED" | "PUSHED_NOT_ARCHIVED" | "RESTORED_REPLACE_AGAIN" | "ARCHIVED" | None`. `entry=None` ⇒ `"UNPREPPED"`. `return None` when `entry.get("type") in ("season_map","multi_ep_alias")`. In-library + `on_disk_real=False` ⇒ `"ARCHIVED"` (if status archived) or `None` — never a reclaimable badge.
2. `guess_manual_id(path)` → editable id string, NEVER raises (`mov-<lang2>-<year>-<slug>` / `tv-…-sNNeMM` / `ani-…<EE>`; default lang `en`; category from which root).
3. `suggest_target_folder(item)` → `{folder, provider_tag, editable_provider_field, applies}`; NEW items only; in-library ⇒ existing `folder_path` + `applies=False`. Provider tags (curly): movies `{tmdb-…}`, TV/anime `{tvdb-…}`.
4. `suggest_next_command(item)` → exact command string from the State table.
5. `collect_reclaimable()` → `{"items":[{id,badge,path,size_bytes,suggested_command,suggested_folder,guessed}], "total_reclaimable_bytes":N, "total_reclaimable_human":"..."}`. Loads library once; walks Movies/Series/Anime roots reusing `cmd_scan_unprepped`'s exclusion set; `known_paths` from physical leaves only (skip `season_map`/`multi_ep_alias`); `on_disk_real = size >= DUMMY_MAX_BYTES`; MUST de-dupe by normpath-lower (a library leaf and its on-disk file = ONE row); human total via `human_readable_size`.

STATE TABLE: UNPREPPED = not-in-library + real on-disk file → `prep <guess_id> "<path>"`. LOCAL_NOT_PUSHED = `local_ready`+!uploaded+real → `push <id> SIZE_GB 8`. PUSHED_NOT_ARCHIVED = `onboarded`+uploaded+real → `replace <id>`. RESTORED_REPLACE_AGAIN = `restored_local`+uploaded+real → `replace <id>`. ARCHIVED = `archived`+dummy → EXCLUDED. Disk is source of truth for "occupies space": an in-library entry whose on-disk file is already a dummy (size < DUMMY_MAX_BYTES) is excluded even if status says local.

Constraint (highest risk): every whole-library iteration MUST skip `season_map`/`multi_ep_alias` (or `_resolve_alias(lib, mid)`) BEFORE touching `folder_path`/`filename` — the IMP-C12 / PR#21 crash class. No new entry type (`ENTRY_TYPE_KEYS` untouched).

## Judge criteria applied (priority order, from the plan)
1. **Correctness** — correct badges + de-dup + GB total, including `season_map`/`multi_ep_alias`/ARCHIVED exclusions, honoring the fixed contract + State table precisely.
2. **Alias/`season_map` crash-safety** of the whole-library walk (the PR#21 class).
3. **Performance** on a large library (no needless double-walk / re-stat).
4. **Readability** + how cleanly the FIXED return contract is honored.

## Candidate summaries

### Candidate A — DISK-FIRST + targeted library second pass
- Approach: Build a `known_paths` index from physical leaves once, `os.walk` the three roots cross-referencing each video against the index and classifying; PASS 2 over reclaimable-status library leaves catches real-file leaves the walk missed. A single `seen` normpath-lower set is the only anti-double-count source.
- Files modified: `main.py` (+383).
- Tests: acceptance importable+callable ok; smoke gate 56 passed; live read-only run 38 items / 160.84 GB, badges `{UNPREPPED:31, LOCAL_NOT_PUSHED:6, RESTORED_REPLACE_AGAIN:1}`; library mtimes unchanged.
- Self-critique highlights: identifies the deliberate `on_disk_real` gate on UNPREPPED emission (49→38, driven by a live 126-byte stub `.mkv`); honest about `guess_manual_id` slug quality on noisy multi-episode names; notes PASS 2 adds nothing under normal full-disk conditions (by design).
- Independent assessment:
  - Strengths:
    - **Only candidate that applies "disk is source of truth for occupies space" UNIFORMLY** — for both known leaves (`classify_entry_state` + `on_disk_real`) AND unknown files (`main.py:3257` gates UNPREPPED emission on `on_disk_real`). This is the most faithful reading of the State-table UNPREPPED row ("real on-disk file") + the reclaim framing.
    - Crash-class guard present in every library iteration: `known_paths` build (`main.py:3188`) and PASS 2 (`main.py:3271`) both skip `season_map`/`multi_ep_alias` before touching `folder_path`/`filename`, plus a `.get()` null-guard.
    - `classify_entry_state` honors the literal contract: `entry=None ⇒ UNPREPPED` (`main.py:2975`); real-file-under-archived returns `None` (not a badge) via `_RECLAIMABLE_STATUS_BADGE.get(status)` (`main.py:2992`) — no invented badges.
    - `suggest_target_folder` in-library branch returns `provider_tag=None`, `editable_provider_field=None`, `applies=False` (`main.py:3107`) — matches the "informational only" contract spirit exactly.
    - Clean row assembly: an internal `work` dict carries `entry` to the suggesters; the public row is built with exactly the 7 contract keys, no `entry` leak (`main.py:3212`).
  - Weaknesses:
    - `guess_manual_id` produces ugly concatenated slugs on pathological multi-episode names (e.g. `Mr.Robot.S02E01E02…`); contract-permitted (editable, never auto-prepped) but imperfect.
    - PASS 2 is dead weight under normal full-disk conditions (only fires when a root is absent/unwalkable). It is correct and de-duped, but adds a small amount of code that rarely executes — a mild simplicity cost.

### Candidate B — LIBRARY-FIRST (stat leaves directly), single walk for unknowns
- Approach: PASS 1 iterates physical leaves and `os.getsize`s each path directly to classify real/dummy/absent; PASS 2 a single `os.walk` emits only unknown on-disk videos as UNPREPPED. Shared `seen` set keyed by normpath-lower.
- Files modified: `main.py` (+378).
- Tests: acceptance ok; smoke 56 passed; live read-only run 49 items / 160.84 GB, badges `{LOCAL_NOT_PUSHED:6, RESTORED_REPLACE_AGAIN:1, UNPREPPED:42}`; library mtimes unchanged.
- Self-critique highlights: notes the `f1themovie` slug-quality tradeoff; honest that PASS-2 inherits `cmd_scan_unprepped`'s exclusions verbatim (so a `Sample.mkv` can surface); archived+real silently None.
- Independent assessment:
  - Strengths:
    - Cleanest I/O profile of the three: known leaves are stat-ed directly, the single walk only discovers unknowns — no walk-then-match on known paths. Strong on criterion 3.
    - Single source of truth for the reclaimable set (`_RECLAIM_STATUS_TO_BADGE`, `main.py:2693`) referenced by both `classify_entry_state` and the collect filter — eliminates drift.
    - Crash-class guard present (`main.py:2950`, skip before `folder_path`/`filename`).
    - `classify_entry_state` honors the literal contract and the archived/real → None rule precisely (`main.py:2745`); in-library `suggest_target_folder` returns `provider_tag=None`/`applies=False` (`main.py:2864`) — matches the contract spirit like A.
  - Weaknesses:
    - **Does NOT gate UNPREPPED emission on `on_disk_real`** (`main.py:3014` emits any unknown video regardless of size) → emits the 126-byte stub `.mkv` as a reclaimable UNPREPPED row. This is the less-faithful reading of the State table's "real on-disk file" qualifier and the reclaim framing (a sub-threshold stub frees no space).
    - `_RELEASE_NOISE_TOKENS` divergence: B's set is partly designed for a "stop slug at first noise token" strategy; the resolution tokens (`2160p`, etc.) are handled as noise rather than via a resolution regex — a stylistic but slightly less robust approach for the never-raise placeholder.

### Candidate C — UNIFIED NORMPATH INDEX (one dict seeded from both sources)
- Approach: Build one `dict[normpath_lower → record]` seeded from library leaves (carrying `entry`/`id`) and from an `os.walk` (carrying real size), merged per key, classified once. The dict key is the only de-dup authority. Lazy-stats only library leaves the walk did not cover.
- Files modified: `main.py` (+433, the largest).
- Tests: acceptance ok; smoke 56 passed; live read-only run 49 items / 160.84 GB, badges `{UNPREPPED:42, LOCAL_NOT_PUSHED:6, RESTORED_REPLACE_AGAIN:1}`; library MD5 unchanged.
- Self-critique highlights: emphasizes the structural (set-free) de-dup; notes year-detection only scans stem + immediate parent; notes genre `"Unsorted"` skeleton path.
- Independent assessment:
  - Strengths:
    - The single-index design is genuinely elegant: de-dup is structural (impossible to double-count by construction), and the size comes "free" from the walk with a lazy fallback stat — best-in-class on criterion 3's "no needless re-stat" and arguably the most readable `collect_reclaimable` body.
    - Crash-class guard present in the seed loop (`main.py:2910`), with an extra `isinstance(entry, dict)` guard.
  - Weaknesses (two contract deviations — the deciding factor against C):
    - **`classify_entry_state` invents a reclaimable badge for unknown/legacy statuses**: `main.py:2702` returns `LOCAL_NOT_PUSHED` for ANY unrecognized status when the file is real. The State table defines exactly four reclaimable statuses; a real file under an unexpected status (e.g. a future/typo status) would be mislabeled reclaimable and emit a `push` command. A and B both return `None` here (via `dict.get`). This is a correctness divergence from the fixed contract.
    - **`suggest_target_folder` for in-library items returns non-None `provider_tag` and `editable_provider_field`** (`main.py:2821-2822`) instead of the `None`/informational shape A and B produce. The contract says in-library ⇒ existing `folder_path` + `applies=False`; C does set `applies=False` and the existing folder, but populates the two provider fields, which the "informational only" framing does not call for. A downstream consumer keying on a populated `provider_tag` to decide "renamable" could be misled.
    - Like B, C does NOT gate UNPREPPED on `on_disk_real` (`main.py:2946` makes any unknown on-disk video a candidate; `on_disk_real` only affects known leaves) → the 126-byte stub surfaces as reclaimable.
    - Largest diff (+433); `suggest_target_folder` fabricates a full `Movies/<Lang>/Unsorted/<leaf>` path including an `_LANG_DISPLAY` table and a `genre="Unsorted"` segment — more speculative construction than the leaf-name-only reading A/B took, and more surface to maintain.

## Head-to-head comparison

**A vs B.** A and B are the two most contract-faithful candidates and agree on `classify_entry_state` semantics, the crash-class guard, and the in-library `suggest_target_folder` shape (`provider_tag=None`, `applies=False`). They diverge on exactly one material point: A gates UNPREPPED emission on `on_disk_real` (38 items), B does not (49 items). On the State table's UNPREPPED row ("not-in-library + **real on-disk file**") and the header note "disk is source of truth for occupies space," A's reading is the more faithful one for a *reclaim* view — a sub-threshold stub frees no space. B's I/O profile is marginally cleaner (direct stat of known leaves, no walk-then-match), so B wins criterion 3 by a hair, but criterion 1 (correctness) outranks it and A wins criterion 1 on the size-gate question.

**A vs C.** C's unified-index design is the most elegant on de-dup and re-stat avoidance (criterion 3) and is very readable. But C carries two deviations from the FIXED contract that A does not: (1) `classify_entry_state` returns `LOCAL_NOT_PUSHED` for unknown statuses with a real file (inventing a badge outside the State table), and (2) the in-library `suggest_target_folder` populates `provider_tag`/`editable_provider_field` rather than the informational `None` shape. C also shares B's missing UNPREPPED size-gate. Since correctness against the fixed contract is the top criterion, A's stricter adherence beats C's superior internal architecture.

**B vs C.** Both emit 49 (neither gates UNPREPPED on size). B is stricter on the contract: B's `classify_entry_state` returns `None` for unknown statuses (no invented badge) and B's in-library `suggest_target_folder` returns the `None`/informational shape. C is more elegant internally (structural de-dup, free sizes) and adds an ordering sort, but pays for it with the two contract deviations and the largest diff. B is the more contract-faithful of the two; C is the better-engineered of the two.

## Rationale for chosen winner

**Candidate A wins on criterion 1 (correctness), which the plan ranks first and explicitly flags the UNPREPPED size question as "the single most important correctness question in the step."** The State table defines UNPREPPED as "not-in-library + **real on-disk file**," and the contract header states "disk is source of truth for 'occupies space'... excluded even if status says local." `collect_reclaimable` is by name a *reclaim* view — its total is "total_reclaimable_bytes." A 126-byte unknown stub occupies no reclaimable space; surfacing it as a reclaimable UNPREPPED row with a `prep` command (as B and C do) is internally inconsistent with the reclaim framing and with how A/B/C all treat known leaves (a dummy known-leaf is uniformly excluded). A is the only candidate that applies the "real file present" rule *uniformly* to both known and unknown paths (`main.py:3257`), which is the reading that best honors the contract as written.

A is also the only candidate with zero deviations from the fixed contract on the supporting functions. Its `classify_entry_state` (`main.py:2992`) uses `_RECLAIMABLE_STATUS_BADGE.get(status)`, so any status outside the four-row State table yields `None` — it never invents a `push`-able badge the way C's `main.py:2702` does. Its in-library `suggest_target_folder` (`main.py:3107`) returns `provider_tag=None`/`editable_provider_field=None`/`applies=False`, matching the "informational only" contract, where C populates those fields. The crash-class guard (criterion 2) is fully present in both of A's library iterations (`main.py:3188`, `main.py:3271`), each skipping `season_map`/`multi_ep_alias` before touching `folder_path`/`filename`, with an added null-guard — equal to B and C on this criterion.

What A does WORSE: on criterion 3 (performance), A is the weakest of the three. Its disk-first walk cross-references every walked video against `known_paths` (a dict lookup, cheap) but its PASS 2 is essentially dead code under normal full-disk conditions — it only fires for absent/unwalkable roots, so it adds lines that rarely run. B's library-first profile (direct stat, no walk-then-match) and C's unified-index (free sizes from the walk, lazy fallback stat) are both cleaner I/O designs and slightly more readable `collect_reclaimable` bodies. A's `guess_manual_id` is also no better than the others on noisy filenames (all three produce concatenated slugs).

Those weaknesses are acceptable because the plan's criterion order puts correctness first and performance third, and the performance gap here is small in absolute terms (a dict lookup per walked file, plus a rarely-executed second pass) on a library this size — all three completed the live run without issue. A trades a little I/O elegance for being the single candidate that gets every fixed-contract detail right, including the step's explicitly-flagged most-important correctness question.

## Why not the others?

**Candidate B** is a strong, clean implementation and was the runner-up — its library-first I/O profile is the tidiest of the three and it matches A on every supporting-function contract detail. It loses on the one correctness question the plan singled out: it does not gate UNPREPPED emission on `on_disk_real` (`main.py:3014`), so it reports a sub-threshold stub as reclaimable, which is inconsistent with the reclaim framing and with its own uniform exclusion of dummy known-leaves. Given correctness outranks the I/O-profile edge it holds, it falls just short of A.

**Candidate C** has the most elegant internal architecture (structural, set-free de-dup; sizes obtained free from the walk; the most readable collect body) and would be a fine choice if internals were the top criterion. But it carries two deviations from the FIXED contract that the winner does not: `classify_entry_state` invents a `LOCAL_NOT_PUSHED` badge for unrecognized statuses with a real file (`main.py:2702`), outside the four-row State table, risking a spurious `push` suggestion for an unexpected status; and its in-library `suggest_target_folder` populates `provider_tag`/`editable_provider_field` rather than the informational `None` shape (`main.py:2821`). It also shares B's missing UNPREPPED size-gate and has the largest, most speculative diff (a fabricated `Lang/Unsorted/leaf` path). Contract fidelity is the top criterion, so these deviations outweigh its architectural elegance.

## What we keep from losing candidates (follow-up suggestions, NOT auto-synthesized)
- **From C — the unified-normpath-index design.** C's single-dict-seeded-from-both-sources approach is the cleanest de-dup and re-stat story of the three (de-dup is structural; sizes come free from the walk; lazy stat only for uncovered leaves). If a future step revisits `collect_reclaimable` for performance on a much larger library, adopting C's index structure (while keeping A's strict `classify` semantics, A's UNPREPPED size-gate, and A's informational in-library `suggest_target_folder`) would be the ideal combination. This is the one place the winner is materially weaker (its rarely-executed PASS 2).
- **From C — deterministic ordering.** C sorts items largest-first (`main.py:3003`), matching `cmd_scan_unprepped`/`local_status` convention. A and B leave items in scan order. A cheap, nice-to-have consistency the winner could adopt later.
- **From B — the direct-stat library-first profile** is also worth keeping in mind as the simplest I/O pattern if the unified index is deemed too clever.

## Verification status
Confirmed. Candidate A satisfies all acceptance criteria for the step:
- All five functions are module-level, importable, and callable (acceptance smoke passed).
- `classify_entry_state` implements `entry=None ⇒ UNPREPPED` literally (`main.py:2975`), returns `None` for `season_map`/`multi_ep_alias` (`main.py:2978`), and never emits a reclaimable badge for a dummy/absent or archived file (`main.py:2983-2992`).
- The crash-class (PR#21/IMP-C12) guard is present in EVERY whole-library iteration, before any `folder_path`/`filename` access (`main.py:3188`, `main.py:3271`).
- `collect_reclaimable` loads the library once, reuses `cmd_scan_unprepped`'s exact exclusion set (`_RECLAIM_EXCLUDE_DIRS`, `main.py:3235`), computes `on_disk_real = size >= DUMMY_MAX_BYTES`, de-dupes by normpath-lower (single `seen` set; live run shows zero duplicate keys), and produces the human total via `human_readable_size` (`main.py:3296`).
- It is the only candidate that applies the "real on-disk file" / "disk is source of truth for occupies space" rule uniformly (the explicitly-flagged most-important correctness question), and the only one with zero supporting-function contract deviations.
- Full cross-command smoke gate green (56 passed); proven read-only (library mtimes unchanged across a live run).
- `ENTRY_TYPE_KEYS` untouched; no new entry type introduced.
