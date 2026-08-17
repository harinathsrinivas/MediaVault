# Extras follow-up fixes — Execution Journal (resumable, cross-session / cross-account)

> **Single machine-readable "where we are" for the post-IMP-D19 extras fix work.** Update + commit at
> the START of a dispatch (intent) and at the END (outcome), so a limit-crash mid-agent still leaves a
> durable trace. A fresh session — even on another Claude account — resumes from this file + `git log`.
> Companion to `PROGRESS.md` (the IMP-D19 build journal, now complete).

- **Branch:** `fix/imp_d20_extras_sidecars` (pushed; base `ffda336` = IMP-D19 merge on `main`).
- **Origin of the work:** the user hit a live parity gap (extras wrote no `.sha256` sidecars), which
  triggered a full extras-vs-main-content parity audit. The audit's findings are D1–D11 + A5.
- **Framework:** v2 (`.claude/agents/orchestrator-v2.md` playbook; executor-fable for complex work).
- **Last updated:** 2026-08-17.

## ⚠️ Standing hazard for EVERY commit on this repo
The user's personal `Master_Stream_Archiver*.py` / `MatchArchiver*.py` files are **STAGED but
uncommitted** in the index (and have been for weeks). **Every commit MUST use an explicit pathspec**
(`git commit -m "…" -- <paths>`), NEVER `git add -A` and NEVER a bare `git commit`. A git-agent once
omitted the `--` and swept them into a commit; recovered via `git reset --soft HEAD~1` (branch was
unpushed). Since then the orchestrator commits these directly rather than delegating.

## ▶ NEXT ACTION
**🚦 CHECKPOINT 1 — awaiting the user's merge approval on PR #43**
(https://github.com/harinathsrinivas/MediaVault/pull/43). All four items (IMP-D20, D3, A5, D4) are
done, committed, pushed and covered by the PR. Do NOT `gh pr merge` without explicit user approval.
After merge, **Checkpoint 2** (annotated `archive/fix/imp_d20_extras_sidecars` tag + branch delete) is a
separate user-gated step. Nothing on this branch is in flight; the deferred table below stays deferred
unless the user says otherwise.

## Status
| Item | Status | Commit | Tests | Notes |
|---|---|---|---|---|
| **IMP-D20** — extras checksum-sidecar parity | done | `e995ec8` | extras 45✓ smoke 76✓ full 656✓ | master `<short_id>.sha256` at register + `checksums/` chunk sidecars at push + idempotent back-fill that survives the D19-B1 skip. Closes judge `DECISION.md` follow-up #2 |
| **D3** — integrity commands extras-aware | done | `64fc8fa` | extras 59✓ smoke 76✓ full 670✓ | `verify_library`, `check`, `repair_dummies`, `verify_restore`, `local_status`. Reuses `_disk_shape`/`_status_disk_violation` verbatim. Live-verified on the REAL library: `extras: scanned 2, OK 2, MISMATCH 0` (no false positives on the user's archived Stranger Things extras) |
| **A5** — `push --extras` silent no-op | done | `64fc8fa` | (same run) | `_push_title_extras_or_warn` at the 3 call sites where `--extras` is provably explicit; `push_title_extras` untouched so `replace_group` on an extras-less title stays silent |
| **D4** — resume command drops extras | done | `33eb523` | extras 61✓ smoke 76✓ full 672✓ | Approved at the change-gate 2026-08-17 after being shown exactly what differs. Additive only: 2 `if` blocks appended after the existing `device` line in `_season_resume_cmd`. Untouched: range math, `.5`-episode handling, split/device reproduction, O-1 mechanism, PONR, journal, `RollbackHardFail`. **Change-gate regression pin added**: `test_season_resume_command_unchanged_without_extras` asserts the extras-less resume line is byte-for-byte the pre-existing format |

## Deferred — audit findings the user explicitly left for later
Do NOT fix these without a new instruction; they were consciously deferred.
| # | Finding | Severity | Note |
|---|---|---|---|
| **D1** | `push_one_extra` has no rollback journal → a split that fails partway leaves chunks; the next run's RESUME branch uploads those partial chunks and flips `uploaded=True` **without `split_info`**, so a later fetch asks for a whole file that isn't in the cloud | **HIGH — data loss** | **Only reachable when a split actually occurs.** `_will_split` is False when the chunk size ≥ the file, so omitting `--extras-size` (or using `9900mb` with files < 9.9 GB) cannot trigger it. Fix = delete the parts dir on split failure, mirroring `cmd_push` |
| D2 | No `tempdir` redirect for extras (this is OD-1, deferred at plan time) | High if splitting | Also the main trigger for D1 |
| D5 | `replace_title_extras` catches only `RollbackHardFail`, so another error escapes into `cmd_replace` post-PONR → false "❌ IRREVERSIBLE" on a main file that archived fine. Its docstring wrongly claims it never raises | Medium | Fix = also catch `Exception` per item, as `restore_title_extras` does |
| D6 | `cmd_prep`'s extras call sits inside the try AFTER `journal.commit()` → an extras failure rolls back the just-created library entry | Medium-low | Fix = move it out of the try |
| D7 | `scan_extras_folders` stores `hash: None` on a hash failure (`cmd_prep` aborts instead) → item can never be hash-matched at fetch | Medium-low, narrow loss | Fix = skip `None`-hash items |
| D8 | `recover <id>` can't reach an extras journal (`recover --scan` does) | Low | Workaround exists |
| D9 | No `set_search` / `set_uploaded` escape hatches for extras | Low | The `check`/`verify_restore` half was covered by D3 |
| D10 | `--extras` pointed AT the title folder itself is unguarded (`group_rel` becomes `"."`) | Low | User error; undocumented |
| D11 | `ROLLBACK_MECHANISM.md` never updated for the two new extras PONR sites | Low (doc) | Extras rollback story lives only in `ARCHITECTURE.md` §12a |

Also open, unrelated to extras: `mov-en-2013-coherence` is `status=restored_local` but its file is
MISSING on disk (surfaced by the live `verify_library` run — pre-existing, not caused by this work).
And `DEVICE_ALIASES["others"]` is still the literal `<NEW_PIXEL_SERIAL>` placeholder (IMP-D18 prereq).

## Resume protocol (what a fresh session does FIRST)
1. `git fetch && git checkout fix/imp_d20_extras_sidecars`; read this file + `git log --oneline main..HEAD`.
2. Reconcile the Status table's SHAs against `git log`. On disagreement, trust git.
3. If an item is `in_progress`, check the working tree (`git diff --stat`) — a crashed agent's EDITS
   SURVIVE in the tree even when its transcript is gone. Inspect the diff before re-dispatching, so
   completed work is never redone.
4. A crashed agent can often be resumed with its full context via `SendMessage` to its agent id
   (the transcript is saved server-side); prefer that over a cold re-dispatch.
5. Gate before committing: `python -m pytest tests/test_extras.py tests/smoke -q`, then
   `python -m pytest tests -q` (NOTE: bare `pytest -q` collects nothing — no `testpaths` configured).
6. Commit by pathspec only (see the standing hazard above), update this file in the same commit.
