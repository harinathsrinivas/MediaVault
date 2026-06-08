# Decision: Step 2 — cmd_restore SPLIT-path verify-or-bless (deferred-rehash core) + disk pre-check

## Outcome
Winner: Candidate B
Branch: `fix/split_hash_deterministic_rehash__cand_b`
Commit: `82b2be7`

## Step requirements
`cmd_restore` SPLIT-path **verify-or-bless** (the deferred-rehash core) + a restore-side disk pre-check + the end-to-end cycle. Replace `cmd_restore`'s blind post-merge hash overwrite (`main.py` ~1727-1734 at the parent commit) with:
- a pre-merge disk pre-check (room for the ~original-size merged output in `local_folder`; hard-stop + return False leaving chunks if short);
- pick/persist `seed` (the entry `short_id`) BEFORE the merge; merge with `merge_video_files(..., seed=seed)` (deterministic);
- compute `new_hash`; then: if `entry["re_hashed"]` is True → VERIFY `new_hash == entry["hash"]` (on match keep hash + proceed to PONR + cleanup; on MISMATCH print a loud greppable alarm and return False BEFORE the PONR, reusing the existing pre-PONR reproducible-output `journal.rollback`, NOT deleting chunks); else (first bless) → set `entry["hash"]=new_hash`, `re_hashed=True`, and `split_info.{merge_seed, merge_tool, rehashed_at}`, then proceed to the EXISTING PONR + commit + cleanup unchanged.

CHANGE-GATE (load-bearing): the PONR location (`journal.mark_point_of_no_return()`) must NOT move; journal format/durability unchanged; bless/verify writes pre-PONR; alarm returns before PONR and reuses the existing reproducible-output rollback; chunks NOT deleted on alarm; the standard (non-split) restore path UNTOUCHED.

## Judge criteria applied (in priority order)
1. Correctness of the verify-vs-bless branch AND the no-PONR-on-alarm guarantee (integrity contract).
2. Change-gate fidelity: PONR position, journal calls, pre-PONR reproducible-output rollback preserved on both happy and alarm/merge-fail paths.
3. Minimal, surgical diff confined to the split-restore block (+ seed persistence) — weighed against any testability/clarity benefit a candidate's structure provides.
4. Readability of the alarm message (greppable; names id + expected vs actual + the stored `merge_tool`).

## Candidate summaries

### Candidate A
- Approach: INLINE verify-or-bless directly at the former blind-overwrite site; no decision helper.
- Files modified: `main.py`, `tests/conftest.py`, `tests/test_baseline_happy_path.py`, `tests/test_cmd_restore_quarantine.py`.
- Lines changed: +75 / -8.
- Tests: 77 passed, 1 skipped (matches baseline).
- Self-critique highlights: change-gate invariants mechanically verified (PONR stays put, alarm returns pre-PONR reusing the merge-fail rollback, chunks survive); flags the seed-fallback widening to `generate_short_id(manual_id)` as the one intentional deviation, argued behavior-identical for real entries; notes the inline structure is not independently unit-testable.
- Independent assessment:
  - Strengths:
    - PONR marker untouched at `main.py:1911`, `commit()` at `1912`; standard path (`main.py:1929+`) byte-identical.
    - Alarm path returns before the PONR reusing the existing `journal.rollback(library)` — integrity contract holds.
    - Seed fallback `... or entry.get("short_id") or generate_short_id(manual_id)` reproduces the *real* `short_id` value (since `short_id == md5(manual_id)[:6] == generate_short_id(manual_id)`), so even a legacy entry missing the field gets the faithful seed.
    - Slightly smaller diff (+75/-8).
  - Weaknesses:
    - Decision logic not extractable for isolated unit testing — Step 9 (planned helper tests) must drive it end-to-end through `cmd_restore`.
    - The verify-match branch carries its own `save_library` and the bless branch another; the two save calls and status set are duplicated across the if/else arms rather than funneled to one site.

### Candidate B
- Approach: EXTRACT a pure `bless_or_verify_merged_hash(entry, new_hash) -> "bless"|"ok"|"mismatch"` at module level; `cmd_restore` acts on the returned string and performs ALL side effects (mutation, `save_library`, journal) itself.
- Files modified: `main.py`, `tests/conftest.py`, `tests/test_baseline_happy_path.py`, `tests/test_cmd_restore_quarantine.py`.
- Lines changed: +92 / -6.
- Tests: 77 passed, 1 skipped (matches baseline).
- Self-critique highlights: pure helper isolates the bless/verify/alarm policy for a 3-line table test (Step 9) with no sandbox; `re_hashed is True` identity check; mismatch reuses the existing reproducible-output rollback; seed fallback to `manual_id`; flags the test-stub sync as the only non-`main.py` change.
- Independent assessment:
  - Strengths:
    - PONR marker untouched at `main.py:1930`, `commit()` at `1931`; standard path (`main.py:1948+`) byte-identical.
    - Alarm path (`decision == "mismatch"`) returns before the PONR reusing the existing `journal.rollback(library)`; chunks not deleted — integrity contract holds.
    - Pure helper has a precise docstring contract and zero side effects (no I/O, no mutation, no journal), making the three-way policy directly unit-testable in isolation — exactly the seam Step 9 is planned to test.
    - Single funnel: after the helper decision, the `bless` arm writes its fields, `ok` is a no-op, then ONE shared `library[manual_id]["status"] = "restored_local"; save_library(library)` runs — less duplication than A.
    - Alarm message names id, expected vs actual hash, AND both the stored `split_info.merge_tool` and the current tool — the richest drift-triage output of the two.
  - Weaknesses:
    - Seed fallback uses raw `manual_id`, which is a *different* string than the real `short_id` for a legacy entry missing the field. Still deterministic, non-empty, and entry-specific (safe), but not the value the entry "should" have had.
    - Adds a module-level function (+17 lines of helper + docstring), so the raw diff is larger (+92/-6).

## Head-to-head comparison

**A vs B — Correctness (criterion 1):** Tie. Both implement the three-way branch correctly: `re_hashed is True` → verify; mismatch → loud alarm + pre-PONR rollback + return False with chunks kept; else first-bless writing `hash`, `re_hashed`, and the three `split_info` fields. Both compute `new_hash` inside the journalled reproducible-output window. No correctness gap in either.

**A vs B — Change-gate fidelity (criterion 2):** Tie. In both, `journal.mark_point_of_no_return()` sits after the merge + `save_library` and before the chunk-delete loop, unmoved relative to parent; the merge-fail and alarm branches both call the *existing* `journal.rollback(library)`; the standard (section B) path is byte-identical to parent in both worktrees. No journal-format or durability change in either.

**A vs B — Diff minimality vs structure (criterion 3):** A's raw diff is smaller (+75/-8 vs +92/-6) and avoids a new top-level symbol — the more literally "surgical" reading. B's larger diff is entirely the pure helper + its docstring; in exchange B isolates the decision policy behind a side-effect-free seam and de-duplicates the post-decision status/save into a single funnel. Criterion 3 explicitly says minimality is "weighed against any testability/clarity benefit a candidate's structure provides," and Step 9 of this same PLAN is dedicated to unit-testing exactly this policy — so B's structure is not speculative abstraction, it is building the seam a later planned step consumes. Edge to B.

**A vs B — Alarm readability (criterion 4):** Both are loud and greppable and name id + expected + actual + stored `merge_tool`. B additionally prints the *current* run's tool alongside the stored one (`stored ...; this run: ...`), which is strictly more useful for the version-drift triage the field exists for. Slight edge to B.

**Seed fallback (cross-cutting):** A's `generate_short_id(manual_id)` reproduces the genuine `short_id` byte-for-byte; B's `manual_id` is a different but equally safe deterministic value. A is more faithful here. This is the one dimension where A is clearly better, but it only matters for legacy entries lacking `short_id`; real entries always carry it (asserted `short_id == generate_short_id(manual_id)`), so both behave identically in practice.

## Rationale for chosen winner

Both candidates are correct and both preserve every change-gate invariant verbatim — I confirmed the PONR marker position, the pre-PONR alarm return reusing the existing reproducible-output `journal.rollback(library)`, the chunks-kept-on-mismatch guarantee, and the byte-identical standard path directly in each worktree's source rather than trusting the critiques. On the two highest-weight criteria (correctness and change-gate fidelity) the candidates tie. The decision therefore turns on criteria 3 and 4.

Candidate B wins on the combination. Its pure `bless_or_verify_merged_hash` helper (`main.py:286`) keeps the bless/verify/alarm *policy* free of I/O, mutation, and journal calls, while every side effect stays in `cmd_restore`. This is the precise seam that Step 9 of this PLAN is scheduled to unit-test, so B's extra ~17 lines are not speculative abstraction (which the project guidelines rightly discourage) — they are infrastructure a later planned step consumes, and they let that step be a no-sandbox table test instead of a full end-to-end `cmd_restore` drive. B also funnels the post-decision `status`/`save_library` into a single shared site rather than duplicating it across the if/else arms as A does, and B's alarm message is marginally more useful (it prints both the stored and the current `merge_tool` for drift triage).

Candidate B is genuinely WORSE than A on the seed fallback: A's `generate_short_id(manual_id)` reproduces the entry's true `short_id` value, whereas B's raw `manual_id` is a different (though still deterministic, non-empty, entry-specific, and safe) seed. B is also the larger diff. These are acceptable given the priorities: the fallback divergence only affects legacy entries that lack `short_id` at all — every real entry carries `short_id`, so the seed both candidates actually use in production is identical — and "smaller raw diff" is explicitly subordinated by criterion 3 to the testability/clarity benefit B's structure provides for the very next step in this feature's plan.

## Why not the other?

Candidate A is an excellent, slightly smaller, fully-correct implementation with the marginally better seed fallback. It was not chosen only because its inline structure leaves the verify-or-bless policy un-isolatable for the dedicated unit test Step 9 is planned to add, it duplicates the status-set/save across both branches, and its alarm prints only the stored `merge_tool` (not the current one). Against B those are small losses, but with criteria 1 and 2 tied they are the deciding ones. This was a close call.

## What we keep from losing candidate A (follow-up note)

Candidate A's seed fallback `entry["split_info"].get("merge_seed") or entry.get("short_id") or generate_short_id(manual_id)` is strictly more faithful than B's `... or manual_id`: it reproduces the entry's real `short_id` (since `short_id == generate_short_id(manual_id)`). For real entries this never matters (both have `short_id`), but if a later step ever blesses a legacy entry that lacks `short_id`, A's fallback would persist a `merge_seed` consistent with the value the entry would otherwise have. Suggested follow-up: change B's final fallback from `manual_id` to `generate_short_id(manual_id)` for that faithfulness, or — better — backfill `short_id` on load so the fallback is never exercised. Not synthesized here; documentation for a future step.

## Verification status
Confirmed: the winning candidate (B) passes all acceptance criteria — disk pre-check is pre-merge/pre-PONR and hard-stops leaving chunks; `seed` is chosen before the merge and passed to `merge_video_files(..., seed=seed)`; the verify branch keeps the hash on match and raises a pre-PONR alarm (rollback, chunks kept, return False) on mismatch; the first-bless branch writes `hash`/`re_hashed`/`split_info.{merge_seed,merge_tool,rehashed_at}` then proceeds to the unchanged PONR + commit + cleanup. Change-gate invariants hold (PONR at `main.py:1930`, journal format/durability untouched, standard path byte-identical). Full suite: 77 passed, 1 skipped, matching baseline. The two spec deviations (mechanical test-stub `seed=None` signature sync in three files; defensive seed fallback) are both present, justified, and assertion-preserving.
