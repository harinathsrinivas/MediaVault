# Decision: IMP-D19 Step 3 — Extras upload phase (`push_one_extra` / `push_title_extras`)

## Verdict
**Winner: Candidate B (isolated duplication).** Branch `feature/imp_d19_extras__cand_b` (tip `083d34a`).

Both candidates are functionally correct and pass their acceptance checks, so the decision is
settled by the criteria the plan ranks 2nd and 3rd — **blast radius on the proven `cmd_push`
path** and **rollback-contract (E1) safety** — and on both of those B is decisively ahead. The
plan's own tie-break rule is explicit: *"If they tie on correctness, criterion 2/3 (blast radius +
rollback safety) is the tie-breaker per the change-gate emphasis."* That rule points straight at B.
A's single-source-of-truth win is real, but it lands on criterion 4 (the lowest-weighted axis) and
is bought by relocating the change-gated journal lifecycle out of `cmd_push` — exactly the kind of
"wrapping of `cmd_push`" modification CLAUDE.md's load-bearing rollback change-gate tells us to avoid.

> ⚠️ This is the plan's **USER CANDIDATE CHECKPOINT** step. This DECISION.md is a recommendation
> only. Nothing is merged. The user chooses A or B; the orchestrator merges only the user's pick.

---

## Step requirements (PLAN.md Step 3)
Add the extras upload phase — `push_one_extra(...)` + `push_title_extras(library, title_id,
extras_size, device_id)` — wired into the `# IMP-D19 Step 3` markers in `cmd_push`,
`cmd_push_group`, `cmd_prep_push_rep`, `cmd_prep_push_rep_season`, and `cmd_add_extras`. For each
not-yet-uploaded extra item: compute the remote dir from the extra file's on-disk folder via
`os.path.relpath(<extra folder>, LOCAL_ROOT)` (mirrors the `Specials`/`Extra` subfolder on the
phone); split by `extras_size` **independent** of the main split (`None` ⇒ inherit the main split;
`('NONE',None)` ⇒ whole-file); hash chunks; upload to `<final>.partial` → atomic `mv` with
`mvcommon.retry`; delete the local chunk; write chunk hashes into the item's `extras` `split_info`;
write a per-extra `write_remote_mvmeta`; flip the item `uploaded=True` / `status="onboarded"`.
**O-1 resumable per file (NO PONR).** `push <id> --extras` on an already-archived main processes
ONLY extras. **The existing `cmd_push` journal/PONR/contract must remain byte-for-byte intact (E1 /
change-gate).**

### Judge criteria (most important first)
1. **Correctness** — mirrored remote paths, chunk hashes stored, `uploaded` flips, resumable on
   re-run, independent chunk size (incl. inherit-main default), archived-main → extras-only.
2. **Blast radius on the proven `cmd_push` path** — existing push/replace/restore + smoke green AND
   how much of `cmd_push`'s battle-tested body is actually disturbed.
3. **Rollback-contract safety (E1)** — journal format/durability, PONR locations, O-1/O-2 split,
   `RollbackHardFail` contract byte-for-byte intact.
4. **Duplication vs single-source-of-truth** — maintainability of the upload protocol going forward.

---

## Per-criterion comparison

| Criterion (weight) | Candidate A — refactor-for-reuse | Candidate B — isolated duplication | Edge |
|---|---|---|---|
| **1. Correctness** | Extras land at mirrored remote via `relpath` (`push_one_extra` `main.py:3813`), chunk hashes → item `split_info`, `uploaded`/`status` flip, idempotent re-run, independent size incl. inherit-main default + `('NONE',None)` (`push_title_extras` `main.py:3884`). Verified vs `mock_device` (whole-file + split-resume + idempotent) and the **full 603-test suite** green. | Same mirrored remote + `relpath`/basename fallback, chunk hashes → item `split_info` (persisted *before* upload), `uploaded`/`status` flip, idempotent, independent size incl. inherit-default (`push_title_extras`). Verified vs `mock_device` (whole-file + chunk-resume). | **~Tie** (slight A on breadth of verification; both meet acceptance) |
| **2. `cmd_push` blast radius** | `cmd_push`'s ENTIRE per-file core was extracted into a new shared `_upload_file(...)` (`main.py:4170`, 17-param signature); `cmd_push` rewritten as a ~70-line thin caller. Net main.py diff **+328 / −180**; the proven body is heavily restructured (observably byte-for-byte, defended by tests). | `cmd_push` body **byte-for-byte unchanged** except a 2-line wire at its success marker (`main.py:4649-4650`) + hoisting `resolve_device` to a local. Net diff **+275 / −7**. New code is two standalone functions placed *before* `cmd_push`. | **B (decisive)** |
| **3. Rollback safety (E1)** | The journal `commit`/`rollback` + O-1 resume-message tail **physically moved into `_upload_file`**. Gated so defaults reproduce `cmd_push` exactly; full rollback matrix (63 incl. D-4 baseline oracle + O-1/O-2 scenarios) green. But the *code that implements* the change-gated contract was relocated — A's own CRITIQUE admits "a strict reading … is not literally met." | **No `RollbackJournal` / `mark_point_of_no_return` anywhere in the new code.** The extras phase is a separate O-1 phase with no PONR; `cmd_push`'s journal/PONR/O-1/O-2 branches are untouched. Byte-for-byte intact, trivially. | **B (decisive)** |
| **4. Duplication / single-source** | One source of truth for the upload protocol (`_upload_file`); future push fixes flow to extras automatically. | The `.partial`+rename+`retry` idiom, `relpath` math, chunk-name/mvmeta logic exist **twice** (`cmd_push` + `push_one_extra`); a future protocol fix must be applied in both or extras drift. | **A** |

---

## The actual `cmd_push` blast radius (criteria 2 & 3 — the deciding evidence)

### Candidate A — `cmd_push` is rewritten; the journal lifecycle is relocated
`git diff 3d959a4 -- main.py` shows the original `cmd_push` body (resume-detect → split → hash →
`.partial`+rename+retry → delete chunk → mvmeta → flip → **journal `commit`/`rollback` + O-1
messaging**) deleted and re-emitted inside a new `def _upload_file(...)` (`main.py:4170`). `cmd_push`
(`main.py:4500`) now ends with:

```python
ok = _upload_file(
    local_file_path, local_folder, base_dir, short_id, remote_target_dir,
    adb_base, library[manual_id], manual_id, library,
    split_method=split_method, split_val=split_val, chunk_range=chunk_range,
    eager_rehash=eager_rehash, temp_dir=temp_dir,
)
```

and the journal open / `journal.commit()` / `journal.rollback(library)` / the
`"Resume with: {resume_hint}"` O-1 message now all live in `_upload_file`. The extras-only knobs
(`journal_split_info=True`, `run_consistency_warn=True`, `resume_hint=None`) default to reproduce
`cmd_push` byte-for-byte, and `library[manual_id]` references became the generic `entry` param. The
behavior is provably preserved (603 + 72 smoke + 63 rollback/baseline green). **But CLAUDE.md's
change-gate explicitly lists "the wrapping of `cmd_push`" and "the PONR locations / O-1/O-2 split" as
things not to modify without an explicit user decision, and the plan's E1 says the contract must be
"byte-for-byte unchanged."** A relocated that wrapping into a shared helper. Observably faithful;
structurally a real change to the change-gated surface. A surfaces this honestly as its central risk.

**E1 assessment for A:** observably intact (strong test evidence), but the change-gated code was
physically moved — the literal "byte-for-byte unchanged" mandate is not met. Residual risk =
transcription fidelity of a large refactor of the most safety-critical function, mitigated (not
eliminated) by the green full suite.

### Candidate B — `cmd_push` body untouched; zero new rollback surface
The entire `cmd_push` change is the two-line wire at the existing success marker:

```python
            print("✅ SUCCESS.\n")
            if extras:
                push_title_extras(library, _extras_title_id(library, manual_id), extras_size, device_id, split_method, split_val)
            return True
```

(`main.py:4649-4651`, inside the `if not chunk_range:` full-success branch, *after* `save_library`
and `journal.commit()`). The journal, PONR, resume-detect, split, upload loop, and O-1/O-2 failure
branches above it are diff-identical to base. The archived-main → extras-only case is handled in the
`push` **dispatch** (argv, `main.py:~8539`), not inside `cmd_push`, precisely to keep the proven body
intact. No `RollbackJournal` / `mark_point_of_no_return` appears anywhere in the new code.

**E1 assessment for B:** byte-for-byte intact, trivially and verifiably. The change-gate is not
tripped because the new phase neither touches nor wraps the existing journal.

---

## Key tradeoff (what you get / what you give up)

**Choose B (recommended):** You get the smallest possible disturbance to the battle-tested
`cmd_push`/rollback path — its body is literally unchanged, so the E1 "byte-for-byte" mandate and
the load-bearing rollback change-gate are satisfied by construction, and the extras phase can't
regress main-content push. What you give up: the `.partial`+rename+`retry` upload idiom now lives in
two places, so a future upload-protocol fix (a new verify step, a quoting/backoff change) must be
applied to both `cmd_push` and `push_one_extra` or extras silently drift. B also skips two optional
main-path niceties for extras (local `checksums/` `.sha256` sidecars and the default-off
`PUSH_VERIFY_REMOTE` check).

**Choose A:** You get one source of truth — `_upload_file` — so the upload protocol is defined once
and every future push fix reaches extras automatically (the cleanest long-term maintainability). What
you give up: a large diff on the most safety-critical function in the codebase, and the change-gated
journal/PONR lifecycle physically relocated out of `cmd_push` into the shared helper — observably
byte-for-byte (proven by the full 603-test suite + the 63-test rollback/baseline matrix) but not
*literally* the "leave `cmd_push`'s wrapping untouched" that E1 and the change-gate ask for. You are
trusting the refactor's fidelity rather than its absence.

---

## Recommendation rationale (by criteria order)

1. **Correctness is ~a tie.** Both mirror the remote path via `os.path.relpath(<extra folder>,
   LOCAL_ROOT)`, store chunk hashes in the item's `split_info`, flip `uploaded`/`status`, honor the
   independent extras size (including the inherit-main default and the `('NONE',None)` whole-file
   sentinel), and resume a half-pushed extra. A verified slightly more breadth (idempotent re-run +
   independent-size cases + the full suite); B verified whole-file + chunk-resume. Neither has a
   correctness defect that eliminates it, and both satisfy the Step-3 acceptance bullets. B even has
   a structurally cleaner resume model: per-item chunk dir `<extra folder>/_parts/<short_id>`
   isolates each extra, so B can safely continue past a failed extra; A uses a *flat* shared
   `_parts/` and is correct only because it stops on the first failure (a tighter coupling A
   acknowledges).

2. **Blast radius (criterion 2) goes to B decisively.** B's `cmd_push` is byte-for-byte unchanged
   bar a 2-line wire; A's `cmd_push` is rewritten and its core extracted into a 17-param helper. The
   plan weights this above duplication, and the test asymmetry confirms it: B needed only the push +
   smoke + parser suites to be confident because it changed nothing proven; A *had* to run the full
   603-test suite + rollback matrix to prove a large refactor didn't break the path.

3. **Rollback safety (criterion 3) goes to B decisively.** E1 and the CLAUDE.md change-gate demand
   the existing journal/PONR/O-1/O-2 wrapping of `cmd_push` stay byte-for-byte intact. B achieves
   that literally (no rollback code touched or added). A relocates the journal lifecycle into
   `_upload_file` — observably faithful but, by A's own admission, not the literal "byte-for-byte"
   guarantee, and it is precisely the "wrapping of `cmd_push`" the change-gate names.

4. **Duplication (criterion 4) goes to A** — its single `_upload_file` is the better long-term
   maintainability story. But this is the lowest-weighted criterion, and the plan explicitly
   anticipated and accepted duplication as approach B's deliberate tradeoff.

Applying the criteria in order — correctness tie, then the change-gate-emphasized 2 & 3 as the
tie-breaker — **B wins.** A's advantage sits entirely on the lowest-priority axis and is paid for on
the two axes the plan and CLAUDE.md flag as load-bearing.

---

## Why not Candidate A?
A is genuinely well-engineered and its single-source-of-truth `_upload_file` is the more elegant
end state — if this were greenfield code, A would likely win. It is not chosen because it spends its
budget on the wrong axis for *this* step: it heavily restructures the most safety-critical, explicitly
change-gated function in the codebase, and physically relocates the rollback journal lifecycle out of
`cmd_push`. The plan's E1 decision and CLAUDE.md both demand that contract be *byte-for-byte*
unchanged; A meets it only *observably* (via a green full suite), not *literally*, and A's own
CRITIQUE flags this as its central risk. With correctness a tie, the plan's stated tie-breaker
(blast radius + rollback safety) decides against A.

## What we keep from Candidate B's losing-axis weaknesses (follow-ups for later steps)
These are documentation for future improvement steps — not synthesized in automatically:

1. **Single-source-of-truth (A's strength).** B's duplicated `.partial`+rename+`retry` idiom is a
   maintenance liability. A future low-risk refactor could extract the shared upload inner loop into
   a helper that *both* call **without** moving the journal/PONR out of `cmd_push` — capturing most
   of A's benefit while preserving B's zero rollback blast radius. Worth a dedicated, separately
   change-gated step.
2. **Extras parity gaps in B (own up-front):** no local `checksums/` `.sha256` sidecar for extras
   chunks (hashes live only in `split_info`) and no `PUSH_VERIFY_REMOTE` post-push check for extras.
   `PUSH_VERIFY_REMOTE` defaults off so this is not an acceptance miss, but Step 6 (restore verify)
   must read the chunk hashes from `split_info`; confirm that path. Add extras sidecar parity if the
   local-verify story is later extended to extras.
3. **No group-level peak-disk pre-flight** in B's `push_title_extras` (each `push_one_extra` self-
   guards with `_free_space_ok`, so worst case is a per-item mid-run stop, not data loss). A future
   step could add an upfront aggregate check, matching `cmd_push_group`/season.

---

## Verification status
- **Acceptance bullets (Step 3):** B satisfies all — extras chunks land at the mirrored remote
  subfolder, chunk hashes are stored in the item's `split_info`, both items flip `uploaded=True`/
  `status="onboarded"`, a half-pushed extra resumes on re-run, the independent `--extras-size` is
  honored (incl. inherit-main default), and `push <id> --extras` on an archived main processes only
  extras (handled in the `push` dispatch).
- **Existing path green:** B reports `test_cmd_push_partial` + `test_cmd_push_mock_device` (11),
  `test_cmd_push_retry` + `test_cmd_push_verify` (13), smoke (72), and a parser+schema sweep (55) all
  green; because `cmd_push`'s body is byte-for-byte unchanged, the rollback matrix cannot be affected.
- **Caveats I could NOT fully verify (applies to BOTH):**
  - I did not re-run the suites myself; I corroborated the reported results against the actual diffs,
    which match the claims (A's `cmd_push` is restructured; B's is untouched). B did **not** run the
    full 603-test suite or the rollback/baseline matrix that A ran — acceptable here only *because*
    B's zero blast radius means those suites are a priori unaffected, but a reviewer who wants belt-
    and-suspenders should run `pytest -q` on the B worktree before merge.
  - Neither candidate exercised a real `mkvmerge` split end-to-end (no `mkvmerge` in the test env);
    both rely on `split_video_file` being the same proven primitive the main path uses, and validated
    the upload loop via the resume-from-pre-seeded-chunks path.

**Conclusion: Candidate B is the recommended winner and meets all Step-3 acceptance criteria.**
