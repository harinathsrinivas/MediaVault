# Decision: Step 5 (redo) — optional `temp_dir` redirect for `cmd_push` chunk/eager-merge artifacts

## Outcome
Winner: Candidate A
Branch: `…__step5r_a` (commit `814b29f`)

> This is a legitimate redo. The prior run's "A" pick was void (disrupted by a
> session limit before B was a complete, fair candidate). This decision was made
> fresh on two complete implementations. It happens to land on A again — but on
> the merits below, not by anchoring.

## Step requirements
Add an optional `temp_dir` kwarg so `cmd_push` puts the `_parts/` chunk dir AND the
eager merge temp on another volume, while `checksums/` + the `RollbackJournal` STAY
on `local_folder`. `temp_dir=None` MUST reproduce today's behavior byte-for-byte.
Thread `temp_dir` through `cmd_push` + `cmd_push_group` + `cmd_prep_push_rep` +
`cmd_prep_push_rep_season` (incl. every per-item `cmd_push` call and the season/group
disk pre-flight targeting). Validate a bad temp_dir → hard-stop. Resume re-passes
temp_dir. CRITICAL trap: the disk pre-flight must stat an EXISTING dir (`temp_dir`
when set, else `local_folder`) — NOT the per-entry base `temp_dir/<safe-id>` (doesn't
exist until `makedirs` later → FileNotFoundError → false hard-stop).

## Judge criteria applied (in priority order)
1. Correctness — chunks + eager temp on temp volume; checksums + journal on
   local_folder; `temp_dir=None` byte-identical; resume scans temp; cleanup removes
   temp `_parts/` + empty per-entry parent ONLY when created-this-run; disk-check
   stats an existing dir.
2. Change-gate fidelity — journal format/durability unchanged (only the recorded
   `parts_dir` PATH may move); created-this-run scoping preserved; `cmd_push` stays
   PONR-less; no new/removed/reordered `RollbackJournal` calls.
3. Surgical diff + clean threading vs. any clarity/maintainability benefit earned.
4. Completeness — all four signatures + every per-item call threaded; pre-flights
   target the temp volume; nothing half-wired.

## Candidate summaries

### Candidate A
- Approach: a single derived `base_dir` local in `cmd_push` (via the shared
  `_parts_base`), with `parts_dir`, the eager `rehash_tmp`, and the cleanup target
  all deriving from it; the disk-check uses an inline `check_dir = temp_dir if
  temp_dir else local_folder`. No new type.
- Files modified: `main.py`.
- Lines changed: +70 / -19.
- Tests: 77 passed, 1 skipped (re-run by judge in worktree A — confirmed). 3/3
  required temp_dir smoke scenarios pass per CRITIQUE.
- Self-critique highlights: honestly flags the three-site `check_dir` duplication as
  the deliberate "no abstraction" cost, the shared-volume assumption, and the
  `"_probe"` second `_parts_base` call in group/season.
- Independent assessment:
  - Strengths:
    - `temp_dir=None` byte-identical by construction: `base_dir == local_folder`
      (`_parts_base` returns `local_folder` for falsy `temp_dir`), so `parts_dir`
      (`main.py:1211`), `rehash_tmp` (`main.py:1370`), `check_dir` and the cleanup
      guard all collapse to today's exact values.
    - Disk-check trap closed correctly: `check_dir = temp_dir if temp_dir else
      local_folder` (`main.py:1296`) stats the validated-existing temp_dir, never
      the not-yet-created `base_dir`.
    - Cleanup guard is triple-locked: `temp_dir and not parts_preexisted and
      base_dir != local_folder` (`main.py:1540`) — a pre-existing temp `_parts/`
      parent is never removed, and `local_folder` is structurally unreachable.
    - Group/season pre-flight validates the temp_dir via `_parts_base(<folder>,
      temp_dir, "_probe")` (`main.py:1650`, `main.py:2558`), so a temp_dir that is
      missing / not-a-dir / **not writable** hard-stops the whole batch early with
      cmd_push's message style — the strictly stronger pre-flight.
    - Journal untouched and rooted at `local_folder` (`main.py:1253`); diff grep for
      rollback-API lines returns nothing.
  - Weaknesses:
    - `check_dir` is recomputed at three call sites rather than carried once.
    - The group/season `"_probe"` does a second `_parts_base` (a stat + access
      check) whose resolved base is discarded.

### Candidate B
- Approach: a module-level `@dataclass(frozen=True) TempLayout` (`main.py:391`) +
  `_push_layout()` factory (`main.py:428`) bundling `{parts_dir, checksum_dir,
  eager_tmp, check_volume, base_dir, uses_temp}`, computed once and read throughout
  `cmd_push`'s body.
- Files modified: `main.py` (+ `from dataclasses import dataclass`).
- Lines changed: +130 / -28.
- Tests: 77 passed, 1 skipped per CRITIQUE; 3/3 smoke scenarios pass.
- Self-critique highlights: candidly names the larger surface as "arguably
  over-structured today" for a one-consumer layout, and flags that the group/season
  pre-flight uses `os.path.isdir` (no `W_OK` check) — a divergence from cmd_push's
  `_parts_base` validation.
- Independent assessment:
  - Strengths:
    - Single source of truth: every temp-vs-media decision lives in `_push_layout`;
      the body just reads fields. `check_volume` is a named first-class field
      (`main.py:1357`), which documents the trap-avoidance by construction.
    - The docstring deliberately records that the journal is NOT a layout field
      precisely so a future editor cannot move it — a nice guard for the change-gate.
    - `temp_dir=None` byte-identical for the same reasons as A; journal at
      `local_folder` (`main.py:1318`); rollback-API diff is empty.
    - Cleanup guard `layout.uses_temp and not parts_preexisted` (`main.py:1602`) is
      correct (uses_temp is False without temp_dir → local_folder never rmdir'd).
  - Weaknesses:
    - +60 lines of abstraction (a 6-field frozen dataclass + factory + import) for a
      SINGLE consumer; the feature has no other layout consumer now or in the
      near-term plan.
    - Group/season pre-flight uses `os.path.isdir` (`main.py:1716`, `main.py:2618`),
      which — unlike A's `_parts_base` — does NOT check `os.W_OK`. A read-only
      temp_dir passes the batch pre-flight and is only rejected per-item. Correct
      end-state, but weaker, later, and less consistent than cmd_push's own check.

## Head-to-head comparison
**A vs B (correctness):** Functionally equivalent on every required behavior —
chunks+eager on temp, checksums+journal on local_folder, byte-identical `None` path,
resume scanning `parts_dir`, created-this-run cleanup, and the disk-check trap. Both
pass 77/1 and the same 3 smoke scenarios. The one behavioral difference is in
batch-level validation strictness: A's `_parts_base`-based group/season pre-flight
also enforces `os.W_OK`, so a present-but-read-only temp_dir hard-stops the whole
batch with one clear message; B's `os.path.isdir` lets it through the pre-flight and
defers the rejection to the first per-item `cmd_push`. Both reach a correct safe
end-state, but A's is the cleaner, earlier, more consistent failure.

**A vs B (change-gate):** A tie. Neither touches journal format/durability, PONR
placement, created-this-run scoping, or any `RollbackJournal` call (diff grep empty
in both); both keep `RollbackJournal(local_folder, …)`. B earns a small documentation
point for explicitly recording in the `TempLayout` docstring that the journal must
never become a field — but that is a comment, not behavior.

**A vs B (diff / structure):** This is the deciding axis. A is +70/-19 with the
layout logic inline behind one `base_dir` local. B is +130/-28 and introduces a
module-level type + factory. B's structure is genuinely cleaner *if* the layout grows
more fields or gains a second consumer; today it has exactly one consumer
(`cmd_push`) and exactly the fields this step needs. For a single-consumer layout the
dataclass is abstraction ahead of need — almost double the diff for no behavioral
gain — which runs against the project's "Simplicity First / minimum code that solves
the problem / no abstractions for single-use code" guidance.

## Rationale for chosen winner
Candidate A wins. On Criterion 1 (correctness) the two are essentially equal and both
fully correct, so the decision rightly falls to Criteria 2–4. On Criterion 2
(change-gate) they tie. On Criterion 3 (surgical diff weighed against clarity earned)
A is the clear choice: it solves the entire step — all four signatures, every
per-item call, the disk-check trap, the triple-locked cleanup guard — in +70/-19 with
no new module-level surface, deriving everything from a single `base_dir`
(`main.py:1205`). B's `TempLayout` is well-built and well-documented, but it is ~60
extra lines of structure for a layout with one consumer and no second consumer in the
plan. The project's explicit instruction is "no abstractions for single-use code…
would a senior engineer say this is overcomplicated?" Here, for one function, the
dataclass earns clarity that does not yet pay for its cost.

A also has a small but real correctness-adjacent edge: its group/season pre-flight
reuses `_parts_base`, so the batch-level check enforces the same
exists+is-dir+**writable** contract that `cmd_push` enforces per item
(`main.py:1650`, `main.py:2558`). B's batch pre-flight checks only `os.path.isdir`
(`main.py:1716`, `main.py:2618`), so a read-only temp_dir slips past the batch gate
and is caught later, per item. Both end safely, but A fails earlier, more
consistently, and with the better message.

What A does WORSE: it recomputes `check_dir` at three sites and makes a second
"_probe" `_parts_base` call in the batch pre-flights, where B carries `check_volume`
once on the layout object. This is real duplication. But the three sites are short,
individually commented, and each computes the same trivial `temp_dir if temp_dir else
<folder>` expression; the maintenance risk is low, and it is the deliberate,
disclosed cost of the no-abstraction approach. Given Simplicity ranks above this
minor DRY concern for a one-consumer feature, the tradeoff favors A.

## Why not the other?
Candidate B is a correct, complete, change-gate-clean implementation and would be a
perfectly safe merge — it is not rejected for any defect. It loses on the structural
tradeoff: a frozen dataclass + factory + import (~+60 lines over A) is abstraction
ahead of demonstrated need for a layout with a single consumer, which the project's
own simplicity guidance counsels against. Its group/season pre-flight is also
marginally weaker (no `W_OK` check, so read-only temp_dir surfaces per-item rather
than at the batch gate). Neither point is a correctness failure; together they make B
the heavier solution to the same problem.

## What we keep from losing candidates (follow-up notes, NOT auto-synthesized)
- From B: the idea of documenting, at the layout/derivation site, that the journal
  and `checksums/` must stay rooted at `local_folder` and never follow `temp_dir`.
  A's inline comment at `main.py:1199` already conveys this, but a one-line note next
  to the `base_dir`/journal lines reinforcing "journal NEVER moves" would harden the
  change-gate against a future editor. Optional, low priority.
- From B (and acknowledged by A): if a later step adds a SECOND layout consumer or a
  separate eager-temp volume, revisit extracting a small layout helper. Until then,
  A's single `base_dir` is the right size.
- Shared (both flagged): the disk-check assumes `temp_dir` and `temp_dir/<safe-id>`
  share a filesystem. True under the current `_parts_base` contract; worth a note if
  mount-point junctions under temp_dir ever become a supported case.
- Consistency follow-up: align the group/season batch pre-flight validation with
  cmd_push's `_parts_base` (incl. `W_OK`) everywhere — A already does this; ensure it
  stays consistent in future edits.

## Verification status
Confirmed. Candidate A passes all acceptance criteria: chunks + eager merge temp on
the temp volume; `checksums/` + `RollbackJournal` rooted at `local_folder`
(`main.py:1212`, `main.py:1253`); `temp_dir=None` byte-identical (77 passed / 1
skipped, re-run by the judge in worktree A; baseline unchanged); resume scans the
temp `parts_dir` (`parts_preexisted` computed from the redirected `parts_dir`,
`main.py:1254`/`1307`); cleanup removes the temp `_parts/` and the empty per-entry
parent ONLY when created this run and never `local_folder` (`main.py:1540`); the
disk-check stats an existing dir, never the per-entry base (`main.py:1296`); and no
rollback-API call was added/removed/reordered (diff grep empty). All four signatures
and every per-item `cmd_push` call are threaded; group/season pre-flights target the
temp volume.
