# Decision: Step 6 — `cmd_migrate_provider_tokens` (IMP-U6)

## Outcome — read before acting

**Recommended: Candidate A** (library-entry-driven, per-entry ancestor walk-up), **moderate-high
confidence**, not unanimous. This is a 🚦 user-gated checkpoint per PLAN.md — **the user makes the
final pick**, not the orchestrator. **Nothing is merged, and no worktree/branch is touched, until the
user confirms a candidate.** This document is the full comparison the user needs to decide directly;
skip to "Verdict rationale in one paragraph per candidate" and the comparison table if short on time.

Both candidates are **purely additive** to `main.py` (confirmed via `git diff --numstat`: A is
`308 insertions(+), 0 deletions(-)`; B is `350 insertions(+), 0 deletions(-)`) and neither touches
`cmd_rename_folder`, the rollback journal, `RollbackHardFail`'s contract, or `ENTRY_TYPE_KEYS` — see
"Blast radius" below for the literal proof.

## Step requirements (from PLAN.md, Step 6)

CLI: `python main.py migrate_provider_tokens [id_or_prefix] [--apply] [--library movies|series|anime|others]`.
Dry-run by default; `--apply` executes. Every rename goes through the unmodified `cmd_rename_folder`
(journalled, PONR at `os.rename`, forward self-heal, cascading `folder_path` rewrite, no re-hash).
`--apply` writes a JSON report under `<LOCAL_ROOT>/migration_reports/`. Deepest-first ordering
required. Idempotent/resumable with no new state file. Tokens rewritten via `find_provider_tokens`'s
`span` so a coexisting `[tvdbid-…]`/`[rartv]` survives byte-identically. Two assigned discovery
strategies: **A** = derive the worklist from library entries' `folder_path` + ancestors up to
`LOCAL_ROOT`, deduplicated; **B** = walk `CATEGORY_ROOTS` top-down and cross-reference the library to
decide migrate-vs-report-as-orphan.

## Judge criteria applied (ranked, from PLAN.md)

1. Correctness on the ancestor case (Friends shape fixture).
2. Idempotency/resumability, proven by simulating an interruption partway through and re-running.
3. Never touches a folder that doesn't need it — no false positive on `[rartv]`, no mutation of an
   orphan folder the library doesn't reference (disk-driven discovery must prove orphan-reporting is
   safe).
4. Practicality on the real ~1600-entry / one-folder-remaining library — full disk walk vs. targeted
   walk-up; state the tradeoff, don't assume a winner.

## How this was judged — methodology note

Both `CRITIQUE.md` self-reports were read but **not** trusted at face value (V2 delta 1). I built one
fixture harness (outside the repo, in the system temp dir, deleted after use — never touched real
`C:\Media` or real `library_*.json`) and ran it **identically** against both worktrees by inserting
each candidate's `main.py`/`mvcommon.py` onto `sys.path` and dual-patching `LOCAL_ROOT` +
`LIBRARY_MOVIES`/`LIBRARY_SERIES`/`LIBRARY_ANIME` on both `mvcommon` and `main` (the IMP-A1 binding
hazard), plus `mvcommon.LIBRARY_OTHERS` (main does not import that name, per `tests/conftest.py`'s own
documented reason). Every result below is something I directly observed (stdout captured, JSON report
files parsed, on-disk tree diffed, library JSON diffed field-by-field), not something either
`CRITIQUE.md` merely asserted. I additionally ran `pytest tests/smoke -q` in each worktree myself
(not adopted from either critique) and inspected `git diff <base> --numstat` / hunk locations myself.

I did **not** run the full `pytest tests -q` suite for either candidate (both self-report `41 failed /
862 passed` with the same pre-existing 39+2 failure set cross-checked against the base commit — this
is plausible and consistent with the smoke-scoped failures I did verify myself, but I did not
independently re-derive the full-suite baseline; flagging as unverified, not adopted).

## Candidate summaries

### Candidate A — library-entry-driven, per-entry ancestor walk-up
- Approach: for every in-scope physical entry (`leaf`+`season_map`, `multi_ep_alias` skipped), climb
  from its `folder_path` to `LOCAL_ROOT`, collecting/deduping every directory in the chain by
  `_norm_path`; classify each collected directory's own basename via a derived-from-canonical-format
  string-equality test (`main.py:3806` `_old_style_tmdb_token`); rewrite by span (`main.py:3830`
  `_apply_token_span`); sort deepest-first (`main.py:4007`); rename each via `cmd_rename_folder`,
  catching `RollbackHardFail` **per candidate and continuing** (`main.py:4054`).
- Files modified: `main.py` only.
- Lines changed: +308 / −0.
- Tests run by candidate: `pytest tests/test_rename_folder.py tests/test_provider_tokens.py -q` → 24
  passed; smoke → 2 failed/78 passed (both pre-existing, named).
- Self-critique highlights: explicitly flags the multi-level nested-ancestor case as **not
  fixture-proven** ("the algorithm should handle it correctly by construction... a notch below 'did,
  and I watched it'"); flags the library-driven blind spot to orphan folders as an inherent,
  acknowledged limitation.
- Independent assessment:
  - Strengths: minimal scan surface (only touches directories the library actually implies);
    RollbackHardFail handling matches the codebase's own "Decision 7" precedent exactly and was
    verified empirically to continue past an unrelated failure and persist a fully actionable
    `resume_cmd` in the report (see "Hard-fail behavior" below); the "not fixture-proven" multi-level
    case **passed** when I tested it myself.
  - Weaknesses: structurally blind to a stray, library-unreferenced folder carrying an old token (no
    orphan audit at all — confirmed, not merely self-reported); `id_or_note` is a best-effort label,
    not a stable machine key (a documented, low-stakes tradeoff).

### Candidate B — category-root disk walk with library cross-reference
- Approach: `os.walk` over `CATEGORY_ROOTS` (same idiom/exclusion set as `cmd_scan_unprepped`), testing
  every directory's own basename (`main.py:3976`/`3853` `_stale_tmdb_tokens`); cross-references
  `_collect_folder_descendants` to decide migrate vs. orphan (`main.py:3999` `orphans.append`); rewrite
  by span, right-to-left (`main.py:3867` `_canonicalize_tmdb_name`); deepest-first via `_folder_depth`
  (`main.py:3882`); on `RollbackHardFail`, **stops the batch and re-raises** after writing the report
  (`main.py:4077`, `4085`, `4132`).
- Files modified: `main.py` only.
- Lines changed: +350 / −0.
- Tests run by candidate: same two suites, same 24 passed / 2 known smoke failures; additionally
  self-reports a 70/70-check standalone fixture harness and a full-suite cross-check.
- Self-critique highlights: explicitly names the `RollbackHardFail` stop-and-re-raise path as
  "reasoned and typed but not exercised by a test"; explicitly names `remote_bearing` as ignoring
  pushed `extras` items as "a deliberate under-report... I would rather flag it than hide it."
- Independent assessment:
  - Strengths: genuine orphan-detection capability, proven safe by fixture (orphan left on disk,
    untouched, surfaced in the report's additive `orphans` key); multi-level nested-ancestor case
    explicitly claimed proven — verified true; clean, well-commented, honestly self-critical.
  - Weaknesses: the untested `RollbackHardFail` path, when I actually exercised it, stops the batch
    (skipping a later, unrelated, otherwise-successful candidate) **and re-raises the exception
    uncaught out of `cmd_migrate_provider_tokens`**, which — verified against the `if __name__ ==
    "__main__":` dispatch (a bare `if/elif` chain, no enclosing `try`/`except` anywhere) — would
    surface as a raw Python traceback to the user running `--apply` for real, not a clean message. The
    persisted report's error record for that entry also omits `resume_cmd` entirely (present in A's).
    Full disk walk costs a stat-walk of the entire media tree on every invocation (dry-run AND
    apply — twice per the documented real-migration procedure) to find a benefit (orphan audit) that,
    per the task brief's verified 2026-09-21 real-library state, currently has nothing to find.

## Candidate × case matrix — what I actually observed

All cases run identically against both worktrees in a fresh fixture per case (never real `C:\Media`).

| Case | Candidate A | Candidate B |
|---|---|---|
| Friends ancestor shape (parent stale, child already `[tmdbid-]`, no entry names the parent) | **PASS** — only ancestor renamed, child leaf name untouched, season_map + leaf `folder_path` correctly re-pointed via cascade | **PASS** — identical result |
| Multi-level nested ancestors (`Show {tmdb-1}/Season 01 {tmdb-1}`, both stale, one run) | **PASS** — both levels migrated correctly in one run (A's own CRITIQUE said this was NOT fixture-proven; I proved it empirically myself) | **PASS** — matches B's own claim of having proven this |
| `[rartv]` coexistence (only tmdb portion rewritten) | **PASS** | **PASS** |
| Coexisting `[tvdbid-…]` preserved byte-identically | **PASS** | **PASS** |
| Dry-run mutates nothing (spy on `cmd_rename_folder`; tree + all library files diffed before/after) | **PASS** — 0 calls, tree/library byte-identical, `migration_reports/` never created | **PASS** — identical |
| Idempotent `--apply` twice (2nd run) | **PASS** — 0 `cmd_rename_folder` calls on re-run | **PASS** — identical |
| Orphan folder (old token, referenced by no entry) | **PASS** on safety (folder untouched) — **but never discovered or reported at all** (by design; not a defect, a scope boundary) | **PASS** — untouched AND explicitly reported (`orphans` key, printed to console) |
| Entry `hash`/`status`/`uploaded`/`split_info` untouched by ancestor rename | **PASS** — verified field-by-field | **PASS** — identical |
| Report same-second de-collision (two `--apply` calls back-to-back) | **PASS** — 2nd report gets a `-1` suffix, both files distinct and valid | **PASS** — identical de-collision scheme |
| `--library movies` scoping (movie renamed, series untouched) | **PASS** | **PASS** |
| Unknown `--library` value → hard refusal, nothing touched | **PASS** | **PASS** |
| `remote_bearing` when only a pushed `extras` sub-item is remote (main content local-only) | **`False`** — same spec-literal under-report B self-discloses, but A's own CRITIQUE never mentions having this gap | **`False`** — self-disclosed, and confirmed identical to A |
| **Mid-batch `RollbackHardFail` injected on one of three independent candidates** (Alpha/Bravo/Charlie; fault forced on Bravo) | Alpha **and** Charlie both renamed (continues past the fault); `renamed=2, errors=1`; error record carries a full, correct `resume_cmd`; **no exception escapes** `cmd_migrate_provider_tokens` | Only Alpha renamed; **Charlie — unrelated, otherwise ready to succeed — is skipped**; `RollbackHardFail` **propagates uncaught** out of the function (verified: `type(exc).__name__ == "RollbackHardFail"`); persisted report's error record has **no `resume_cmd` key** |

Case not run: an actual crash-mid-process kill (both candidates' resumability rests on
`cmd_rename_folder`'s own already-verified IMP-D17 crash-safety, which is out of scope to
re-prove here — re-verifying `cmd_rename_folder` itself is not this step's job).

## Blast radius — change-gated surfaces (both candidates)

```
$ git diff 4f31606 --numstat -- main.py
A: 308  0  main.py
B: 350  0  main.py
```

Zero deletions in either diff — nothing pre-existing was touched, only new code was inserted. Hunk
locations confirm the insertions land in exactly three places for each (a new function block after
`_rewrite_folder_path`, one new usage-help `print`, one new CLI dispatch `elif` branch); neither
touches `cmd_rename_folder`'s body (main.py lines 3636–3777 on the base commit), `RollbackJournal`,
`mark_point_of_no_return`, `recover_journal`, or `ENTRY_TYPE_KEYS` (grepped both diffs directly — zero
hits for any of those symbols in either). **The rollback contract and change-gated surfaces are
byte-for-byte untouched by both candidates** — this specific claim, made by both critiques, is
verified, not adopted on faith.

## Smoke gate — spot-checked myself, not adopted from either critique

```
cand_a: pytest tests/smoke -q → 2 failed, 78 passed, 1 warning in 55.25s
cand_b: pytest tests/smoke -q → 2 failed, 78 passed, 1 warning in 25.40s
```
Both failures are identical in both runs:
`TestPrepPushRepEnrich::test_prep_push_rep_enrich_movie_round_trip` and
`test_prep_push_rep_season_enrich_stamps_show_folder`, both asserting on the literal (pre-Step-11)
`SMK.S01.2020 {tmdb-424242}` stamped-folder string that Step 3 already changed to `[tmdbid-…]` —
confirmed by reading the actual assertion failure (`is_dir()` on the old brace-form path). These are
the Step-11-owned failures named in both dispatches; neither candidate introduces a new smoke failure.

## Head-to-head comparison

**A vs. B on criterion 1 (ancestor correctness):** tied. Both pass the Friends shape and — contrary to
A's own self-reported uncertainty — both pass the multi-level nested-ancestor case when I tested it
directly. A's "should work by construction, not fixture-proven" hedge turned out to be correct; I
closed that gap myself rather than taking either side's word for it.

**A vs. B on criterion 2 (idempotency/resumability under interruption):** A is materially stronger,
verified, not asserted. My injected mid-batch `RollbackHardFail` test (three independent candidates,
fault forced on the middle one) shows A completing the run for every unrelated candidate and leaving a
fully actionable report (with `resume_cmd`) for the one that failed — a clean, non-crashing return. B
stops the batch early (an unrelated, otherwise-ready candidate is left un-migrated for no reason tied
to it) and **re-raises the exception uncaught**, which — because the CLI's `if __name__ ==
"__main__":` dispatch has no enclosing `try`/`except` anywhere (verified by reading it) — would
surface as a raw Python traceback to the user on a real `--apply` run, not a clean, printed message.
Every one of the seven other `RollbackHardFail` handling sites already in this codebase (checked all
seven: `main.py:5590`, `5774`, `5822`, `7356`, `7525`, `7548`, `8013` on the base commit) either
warn-and-continue (the "Decision 7" precedent A explicitly follows) or catch-print-and-`return`
cleanly; **none of them bare-propagate an exception to the CLI boundary**. B's design is a real,
verified deviation from that established convention, not merely an untested corner.

**A vs. B on criterion 3 (never touches an unneeded folder / orphan safety):** B is stronger here,
also verified, not asserted. B's orphan detection is real and proven safe (the orphan folder is left
on disk, untouched, and is explicitly surfaced in `result["orphans"]` and the JSON report). A cannot
see an orphan at all — not a safety failure (an orphan is, definitionally, never a candidate for A, so
it can never be mistakenly renamed), but it is a real capability gap: a folder with a stale token that
no entry's `folder_path` chain touches is permanently invisible to A's design, with no audit trail
telling the user it exists.

**A vs. B on criterion 4 (practicality on the real library):** tradeoff, as the plan asks me to state
rather than resolve unilaterally — but the task brief's verified 2026-09-21 real-library state tips it
concretely toward A for *this* run. The remaining real work is exactly one ancestor folder (Friends),
cascading to 244 entries; zero folders are currently double-stamped, and the brief names no known
orphan. A's walk-up touches only the directories ~1600 entries' `folder_path` chains actually imply —
bounded, fast, and precisely sufficient for the known problem. B's full `os.walk` over all four
`CATEGORY_ROOTS` enumerates every directory in the entire on-disk media tree (seasons, extras
subfolders, everything) on **every** invocation — the documented real-migration procedure runs it four
times (dry-run, `--library series` dry-run, `--apply`, confirm dry-run) — to prove, each time, that
nothing beyond the one known folder needs it. That is a real, non-trivial cost paid today for a
capability (orphan audit) that is valuable in general but unrealized against the actual current state
of this library.

## Verdict rationale — one paragraph per candidate

**What you get with Candidate A / what you give up:** you get the minimal, precisely-targeted
implementation that matches the real library's actual remaining need exactly, verified graceful
degradation under a mid-batch failure (continues unrelated work, leaves a complete, machine-actionable
recovery record), and a codebase-convention-consistent exception-handling story. You give up any
audit capability for a stray, library-unreferenced folder — if one exists today or appears later
(e.g. from a future partial third-party migration, exactly the failure mode that created this whole
step), A will never see it or tell you about it.

**What you get with Candidate B / what you give up:** you get a self-auditing design that finds
everything a stale-token folder could be, referenced or not, and reports orphans explicitly rather
than staying silent about them — genuinely valuable if this library's history repeats itself. You give
up a full recursive directory walk on every invocation for a benefit that, right now, has nothing to
find, and — more importantly — a verified, non-hypothetical hard-fail path that skips unrelated,
otherwise-successful work and lets an exception escape uncaught to the CLI, producing a raw traceback
instead of the clean, resumable message every comparable failure path elsewhere in this codebase
produces, with a persisted report that is missing the one field (`resume_cmd`) a user would need to
recover cleanly from exactly that failure.

## If the user picks Candidate A anyway with B's orphan-audit value in mind

No fix is required to merge A — it satisfies every locked acceptance bullet and both top-ranked
criteria. A reasonable, low-risk, **optional** follow-up (not required for this merge, and not
something I'd block on): a separate, additive, read-only `--audit-disk` flag on A's implementation
that does a supplementary `os.walk` purely to print/report stray old-token folders the library-driven
pass didn't reach — never renaming anything found this way. This is future-scope, not a fixable flaw
in what A already does.

## If the user picks Candidate B instead

The concrete, precise follow-up edit needed before a real `--apply` run against `C:\Media`: in
`cmd_migrate_provider_tokens` (candidate B, `main.py:4077-4132`), (1) include `hf.resume_cmd` as an
explicit key in the `errors` record appended at `main.py:4077` (currently only `f"RollbackHardFail:
{e}"` is stored — `e.resume_cmd` is available and simply not captured), and (2) either stop
re-raising `hard_fail` after the report is written (`main.py:4132`) and instead print the same
clean, non-crashing message the codebase's other `RollbackHardFail` sites use (mirroring
`main.py:8013`'s "Decision 7" wording) and `return result`, **or**, if the intentional stop-the-batch
behavior is kept, wrap the `elif cmd == "migrate_provider_tokens":` CLI dispatch branch itself in a
`try/except RollbackHardFail` that prints `hf.state`/`hf.reason`/`hf.resume_cmd` cleanly before
exiting, so a real user never sees a raw Python traceback. Either sub-option is a small, targeted,
merge-time-appliable patch — not a re-run of the step.

## Why not the other, if A is picked

B was not chosen primarily because its one materially-tested weakness — the `RollbackHardFail`
handling — lands on exactly the two top-ranked judge criteria for this step (idempotency/resumability
under interruption, and correctness of what the report leaves behind for recovery), and because this
is the step whose `--apply` output the user will actually run against the real, un-backed-up-by-this-PR
`C:\Media` library. Its orphan-audit strength is real but does not offset that, given the real
library's currently-verified state has nothing for that audit to find.

## Why not the other, if B is picked

A was not chosen primarily because it cannot detect or report a stray, library-unreferenced folder
carrying a stale token at all — a real, structural scope boundary rather than a bug, but one that
forecloses exactly the kind of drift (a future partial migration tool) that created this step's Friends
gap in the first place.

## What we keep from the losing candidate, whichever is chosen

- From B (if A wins): the orphan-audit idea itself, and its proof pattern (cross-reference every
  disk-walk hit against `_collect_folder_descendants`, exactly the same predicate `cmd_rename_folder`
  refuses on) — a good template for the optional `--audit-disk` follow-up noted above.
- From A (if B wins): the "Decision 7"-consistent warn-and-continue handling of a mid-batch
  `RollbackHardFail`, and persisting `resume_cmd` directly into the JSON report — both are small,
  concrete, precisely what B's follow-up patch (above) should adopt.

## Verification status

Both candidates satisfy every locked acceptance bullet from PLAN.md Step 6, verified directly (not
adopted from either CRITIQUE.md): the Friends-shape ancestor rename, `[rartv]`/`[tvdbid-…]`
preservation, dry-run zero-mutation, idempotent double-apply, and — going beyond the locked bullets —
the multi-level nested-ancestor case and orphan safety. Neither touches the rollback contract,
`ENTRY_TYPE_KEYS`, or any other change-gated surface. The one place they genuinely diverge in
observed (not self-reported) behavior is mid-batch hard-failure handling, which favors A.

## Confidence and what I could NOT verify

**Moderate-high**, not unanimous, and this is a user-gated checkpoint by design — the user should read
the head-to-head section above and may reasonably weigh the orphan-audit capability more heavily than
I did, in which case B plus the small follow-up patch above is a legitimate, low-risk pick. I did NOT
run this judging pass on fable-tier — this session was invoked directly as the V2 judge without a
model-fallback banner, so no re-judge-on-fable flag applies here. What I could not verify: (1) the
full, untouched-suite claim (`41 failed, 862 passed` for both, with 39+2 pre-existing) — I did not
re-run `pytest tests -q` myself for either candidate, only the smoke subset and the two targeted
suites both critiques named; this is plausible given what I did verify but is stated here as
unverified, not silently adopted. (2) A genuine process-level crash mid-rename (kill signal, not a
simulated exception) — both candidates' resumability ultimately rests on `cmd_rename_folder`'s own
already-battle-tested IMP-D17 crash-safety, which this step correctly does not re-implement and which
I did not re-verify (out of scope for this step's own correctness). (3) Emby/Jellyfin's actual runtime
behavior on either candidate's exact output string — out of scope for this step (format is locked by
Steps 1–3, already merged).

**Nothing is merged, and neither worktree/branch is touched, until the user confirms a pick from this
document.**
