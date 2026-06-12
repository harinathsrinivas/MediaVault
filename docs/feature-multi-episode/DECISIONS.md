# DECISIONS — feature-multi-episode (combined-file E19E20 support)

Load-bearing choices for the multi-episode combined-file feature. Plan lives in
`docs/feature-multi-episode/PLAN.md` (canonical) and the gitignored root
`/PLAN.md` (live working copy, identical content).

## D-1 — Alias model: thin alias (Approach A)
A single physical file covering E19+E20 is represented by ONE real leaf entry
(the primary, lowest episode = `...e19`) plus a thin alias entry per secondary
episode:

```jsonc
"tv-...-s04e20": {"type":"multi_ep_alias","alias_of":"tv-...-s04e19","parent_id":"tv-...-s04"}
```

Chosen over Approach B (a `multi_ep:[19,20]` list field with no secondary key)
and Approach C (two full duplicate entries). Rationale:
- Zero duplication of `hash`/`tech_spec`/`split_info` — the primary is the single
  source of truth, so a `replace`/`push`/re-hash on the primary never leaves a
  stale secondary.
- The secondary KEY exists, so the existing range filters and
  `resolve_targets` see it with no new scan — only an additive resolve step.
- B needs a linear containment scan in BOTH the range filter and the fetch
  lookup-miss path (more touchpoints, easy to miss). C duplicates data and leaves
  the secondary's status out of sync after a replace.

STATUS: recommended; pending user confirmation (Open Decision 1 in PLAN).

## D-2 — Core rollback/push/replace untouched (change-gate compliance)
Per CLAUDE.md's auto-rollback change-gate, this feature makes NO change to
`cmd_push`, `cmd_replace`, `cmd_prep`, `RollbackJournal`, `recover_journal`,
`RollbackHardFail`, PONR markers, journal format/durability, or created-this-run
scoping. The mechanism that keeps the core untouched: the season/group loops
DROP alias ids to their primary BEFORE the disk pre-flight and push loop, so an
alias entry (which has no `filename`) never reaches `cmd_push`/`cmd_replace`. The
only resume-message change is which episode NUMBERS are listed (always the
primary), never the command shape — so the `RollbackHardFail.resume_cmd` contract
(`resume_cmd` must name an existing command) is preserved.

STATUS: hard constraint; no user decision needed.

## D-3 — Generalize to N>=2 episodes per file
The detector (`S\d+(?:E\d+){2,}` + `findall(r"[eE](\d+)")`) and the alias-creation
loop handle `E17E18E19` (3+) with no extra code. Test K covers it.

STATUS: recommended in scope; pending user confirmation (Open Decision 2).

## D-4 — Terminology
Prose/tests/branch use "combined episode"; the on-disk `type` token is
`multi_ep_alias` (terse, greppable).

STATUS: recommended; pending user confirmation (Open Decision 3).

## D-5 — IMP code
Track as `IMP-E13` in `improvements_tierE.md` (Tier E = integration/workflow). PR
title carries `— IMP-E13`. If user declines, ship without a code and drop the
suffix. Verify the next-free E number at implementation time (E1–E12 exist today).

STATUS: recommended; pending user confirmation (Open Decision 4).

## D-6 — Resume range hint emits the primary's episode number
A failure at the combined item resumes inside `...19...`, never as a standalone
`20-20`. Step 3 collapses `target_ids` to primaries (so the resume loop naturally
lists 19); Step 4 adds a defensive resolve.

STATUS: recommended; pending user confirmation (Open Decision 5).
