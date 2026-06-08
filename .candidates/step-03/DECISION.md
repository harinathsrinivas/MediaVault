# Decision: Step 3 — EAGER bless-at-push + promote-at-replace + re_hashed-reset on re-split

## Outcome
Winner: Candidate A
Branch: `fix/split_hash_deterministic_rehash__step3_a`
Commit: `91b0532`

## Step requirements
1. Thread `eager_rehash=False` kwarg through `cmd_push` + 3 callers (`cmd_push_group`, `cmd_prep_push_rep`, `cmd_prep_push_rep_season`). CLI token is Step 6 — dormant/False for now.
2. RE-SPLIT RESET (deferred + eager): in the NEW-SPLIT branch ONLY (NOT the resume branch reusing existing `_parts/`) set `re_hashed=False` and ensure the fresh `split_info` drops the stale canonical fields (`merge_seed`/`merge_tool`/`rehashed_at`/`canonical_hash`). Closes a re-push false-alarm hole.
3. EAGER (only when `eager_rehash` True AND a new split happened, after the reset): seed = `short_id or manual_id`; merge new chunks deterministically into a temp in `local_folder`; hash as `canonical`; ALWAYS delete the temp; store `merge_seed`/`merge_tool`/`canonical_hash` in `split_info` (NOT on `entry["hash"]`, NOT `re_hashed`). GRACEFUL FALLBACK on any merge/hash failure: clean temp, warn, write no canonical, continue as deferred; NEVER abort an otherwise-successful push.
4. PROMOTE-AT-REPLACE: in `cmd_replace`, after the replace PONR, just before `status="archived"` + final save: if a canonical is pending AND `re_hashed` not already True → set `entry["hash"]=canonical`, `re_hashed=True`, stamp `rehashed_at`, clear the transient(s). No-op otherwise.

## Judge criteria applied (priority order)
1. Correctness — `entry["hash"]` consistent with on-disk file across push→replace; eager writes confined to `split_info`; promotion ONLY at replace; reset fires on NEW split but NOT on resume.
2. Change-gate fidelity — no new PONR; push stays PONR-less (O-1); replace PONR unchanged; no new `RollbackJournal` record kinds / no journal-scope changes; reset writes the SAFE unblessed state.
3. Surgical diff + clean kwarg threading through the three orchestrators; no unintended edits.
4. Temp always cleaned up (finally) even on merge failure; fallback never aborts the push.

## Candidate summaries

### Candidate A
- Approach: Transient `split_info["canonical_hash"]` as the sole promotion signal; `cmd_replace` promotes by detecting `canonical_hash` PRESENCE (+ `re_hashed` not True) and drops it on promotion. No extra top-level schema field.
- Files modified: `main.py` only.
- Lines changed: +73 / -7.
- Tests: 77 passed, 1 skipped (re-run by judge in worktree A, confirmed). Worktree clean except untracked `CRITIQUE.md`; no stray `*.rehash_tmp.mkv`.
- Self-critique highlights: Names the transient-only tradeoff honestly (implicit "pending" state, possible lingering `canonical_hash` if eager-pushed-but-never-replaced); confirms reset-before-eager ordering; confirms `finally` cleanup and best-effort fallback; ~0.9 confidence on change-gate.
- Independent assessment:
  - Strengths:
    - Reset + eager strictly gated to the `should_split` new-split branch (`main.py:1298`, `1311`), after the fresh 5-key `split_info` assignment (`main.py:1283-1286`); resume branch (`main.py:1233`) and single-file paths untouched — reset cannot fire on resume. Verified by reading the surrounding branch structure.
    - Eager merges the UNFILTERED `files_to_upload_paths` (the `chunk_range` filter is at `main.py:1342+`, strictly after), so the canonical always represents the whole file.
    - Temp cleanup in an unconditional `finally` with an `os.path.exists` guard + inner swallow (`main.py:1328-1333`); all three failure modes (merge False, merge raise, hash None) fall through to deferred without aborting.
    - Promote block (`main.py:1606-1621`) runs after the PONR `os.rename(original→.tobedeleted)`, before `status="archived"` + `save_library`; guarded by `re_hashed is not True`; reads live `library[manual_id]` so no aliasing risk; `del`s the transient on promotion.
    - Canonical is never written to `entry["hash"]` at push time — only at replace. `cmd_check` correctness preserved across the window.
    - Change-gate clean: no `mark_point_of_no_return()` added, no new record kinds, `record_set_field("split_info")` scope unchanged; the transient rides inside the already-journalled `split_info` for new entries.
  - Weaknesses:
    - `re_hashed=False` is written unconditionally on every new-split push, including brand-new entries (absent → explicit False) — a cosmetic, harmless schema touch.
    - If a user eager-pushes but never replaces, `canonical_hash` lingers in `split_info` until a future re-split/restore consumes it. Implicit "pending" state is slightly less self-documenting than a named boolean.

### Candidate B
- Approach: Explicit top-level `pending_promote: True` boolean set at eager push alongside `split_info["canonical_hash"]`; `cmd_replace` promotes off the boolean and clears BOTH; the reset also pops `pending_promote`.
- Files modified: `main.py` only.
- Lines changed: +74 / -7.
- Tests: 77 passed, 1 skipped (per critique).
- Self-critique highlights: Frames the explicit-flag tradeoff (one unambiguous trigger vs an extra field coupled with `canonical_hash` in three places — set/cleared/reset); defensive `canonical` truthiness guard at promote; honest that the extra field is the one real cost.
- Independent assessment:
  - Strengths:
    - Same correct gating: reset + eager in the new-split branch only (`main.py:1297-1298`, `1311`), after the fresh `split_info` (`main.py:1283-1286`); resume branch untouched.
    - Same unfiltered-chunks merge, same `finally` temp cleanup (`main.py`), same best-effort fallback.
    - Promote block (`main.py:1662-1671`) after the PONR (`os.rename` at `main.py:1624`, marker at `1626`), before `status="archived"`; `entry = library[manual_id]` (`main.py:1564`) so `entry.get("pending_promote")` reads live data correctly; defensive `if canonical:` guard avoids corrupting `hash` if the flag is ever set without a canonical; clears both transients.
    - Change-gate clean by the same reasoning as A; the boolean is persisted by the existing `save_library`, no new journal vocabulary.
    - The named flag is more self-documenting for anyone inspecting library JSON between push and replace.
  - Weaknesses:
    - Adds a top-level schema field (`pending_promote`) that must be kept in lockstep with `split_info["canonical_hash"]` across THREE sites (set at eager, popped at reset, popped at promote). A future edit touching one without the other risks a stray canonical (flag-less, never promoted) or a flag-less promote skip.
    - The defensive `if canonical:` guard exists precisely because the two fields can drift — additional logic that only matters because of the second field.

## Head-to-head comparison
A vs B: On criteria 1 (correctness), 2 (change-gate), and 4 (temp cleanup / fallback) the two are equivalent — same branch gating, same unfiltered merge, same `finally`, same post-PONR promote, same no-new-journal-state property. I verified each of these independently rather than trusting the critiques. The only material difference is the promotion-trigger design (criterion 1's robustness sub-dimension and criterion 3's surgical-cleanliness dimension). A keys promotion off the presence of `split_info["canonical_hash"]`, which is the exact field it must consume and clear — one field, self-clearing, no cross-field invariant. B introduces a parallel top-level boolean `pending_promote` that duplicates the "a canonical is pending" signal and must be maintained in three places alongside `canonical_hash`. B's own defensive `if canonical:` guard is evidence of the drift risk that the second field creates. B's advantage is readability (a named boolean self-describes the pending state, and the lingering-field concern A notes is more legible in B). That readability gain is real but minor; A's single-source-of-truth design is the stronger position on coupling, schema cleanliness, and consistency risk — which the step's "weigh the central tradeoff" instruction explicitly elevates.

## Rationale for chosen winner
Both candidates fully satisfy the acceptance criteria and are change-gate clean, so the decision turns on the central tradeoff the step calls out: implicit transient trigger (A) vs explicit coupled flag (B). A wins on criterion 1's robustness sub-dimension and criterion 3 (surgical/clean schema). A's promotion keys off the single field it consumes — `split_info["canonical_hash"]` at `main.py:1606` — which is created at eager push (`main.py:1322`), checked-and-`del`'d exactly once at promote (`main.py:1606-1621`), and otherwise naturally dropped by the fresh-`split_info` replacement on any re-split. There is exactly one source of truth and it is self-clearing; there is no cross-field invariant to maintain and no possibility of the trigger and the data drifting apart.

B's explicit `pending_promote` (`main.py`, set at `1666`-adjacent eager block; popped at reset `1298`-adjacent; popped at promote `1671`) is genuinely clearer to a reader, but it pays for that clarity by introducing a top-level schema field that must stay coupled with `canonical_hash` in three locations. B's own `if canonical:` defensive guard at `main.py:1665` exists only because those two fields can fall out of sync — that is the coupling risk made concrete in code. Per the global "Simplicity First" guidance and the step's instruction to weigh schema cleanliness and coupling/consistency risk, fewer fields with no invariant beats one extra field with a maintained invariant.

A is not strictly better on every axis. B's named boolean makes the push→replace pending window self-describing in the library JSON, whereas A's lingering `canonical_hash` (when an eager-pushed entry is never replaced) is a slightly surprising resident field that relies on the reader knowing the field name carries the meaning. A also writes `re_hashed=False` unconditionally on every new-split push, a harmless cosmetic touch. These are acceptable: the lingering-field case is bounded (it is consumed at the next replace or dropped at the next re-split, and Step 2's restore keys off `re_hashed`, not canonical presence, so no false-bless is possible), and the unconditional write is a single, branch-local, easy-to-audit line that matches the deferred path's implied state.

Net: equivalent on correctness/change-gate/cleanup, A ahead on schema cleanliness and consistency risk, B ahead only on readability of the pending state. The priority order makes A the winner.

## Why not the other?
Candidate B is a solid, correct, change-gate-clean implementation and would be a perfectly safe merge. It is not chosen only because its explicit `pending_promote` boolean adds a top-level schema field that must be maintained in lockstep with `split_info["canonical_hash"]` across three sites, introducing a coupling/consistency invariant (and a defensive guard to tolerate its violation) that A avoids entirely with a single self-clearing source of truth. Given the candidates are otherwise equivalent on every weighted criterion, the simpler, lower-coupling schema is the deciding factor.

## What we keep from losing candidates
From B: the readability benefit of an explicit, named pending-promotion signal is worth remembering. If, in a later step, the push→replace pending window needs to be inspected, surfaced (e.g. in a `status`/JSON output mode), or reasoned about by code other than `cmd_replace`, consider making A's transient self-documenting — either via a comment at the `split_info["canonical_hash"]` write naming it as the pending-promotion marker (already present in A's comment at `main.py:1300-1310`) or, if a real second consumer appears, revisiting B's explicit-flag design. B's defensive `if canonical:` truthiness guard at promote is also a cheap robustness touch A could adopt if the promote block ever gains additional entry points; not needed today because A's trigger and data are the same field.

## Follow-up notes for later steps
- Step 4 (disk pre-flight): both candidates rely on graceful fallback for out-of-disk during the eager merge; A's eager block at `main.py:1311-1333` is the integration point for a pre-flight check before `merge_video_files`.
- Step 5 (`temp_dir` redirection): both write the eager temp to `local_folder` (`main.py:1314`); redirect here when Step 5 lands.
- Step 6 (CLI token): `eager_rehash` is threaded and dormant/False through `cmd_push` and all three orchestrators (`cmd_push_group` `main.py:1466`, `cmd_prep_push_rep` `main.py:2250`, `cmd_prep_push_rep_season` `main.py:2288`); Step 6 only needs to flip it from the CLI parser.
- Step 9 (if it touches restore/check): the winner keeps `entry["hash"]` == on-disk file across the whole push→replace window, and the eager path converges on the same `split_info` shape (`merge_seed`/`merge_tool`/`rehashed_at`) as Step 2's deferred restore bless, so the two bless paths cannot drift.

## Verification status
Confirmed. Candidate A passes all Step 3 acceptance criteria:
- kwarg threaded through `cmd_push` + all three callers (dormant/False) — verified in diff.
- re-split reset fires in the new-split branch only, fresh `split_info` drops stale canonical fields, resume branch untouched — verified against `main.py:1233` vs `1283-1298`.
- eager confined to `split_info`, never `entry["hash"]`, never `re_hashed`; temp always deleted in `finally`; graceful fallback never aborts the push — verified `main.py:1311-1333`.
- promote runs strictly after the replace PONR, no-op for non-eager/non-split, sets `hash`/`re_hashed`/`rehashed_at` and clears the transient — verified `main.py:1606-1621`.
- no new PONR, push stays PONR-less (O-1), replace PONR position unchanged, no new `RollbackJournal` record kinds — verified.
- suite: 77 passed, 1 skipped (re-run by judge in worktree A); worktree clean apart from untracked `CRITIQUE.md`.
