# Auto-Rollback — Consolidated Re-Plan Brief (start here for the new session)

> **Purpose.** This is the single, self-contained brief to resume the auto-rollback
> feature. It consolidates the original (pre-pause) plan, every confirmed decision,
> the corrected failure analysis, and everything that changed while the feature was
> paused (all prerequisites are now merged). Paste the prompt in §7 into a **fresh**
> Claude Code session to run the planner.
>
> **Status as of 2026-05-31:** all prerequisites merged into `main`; all open
> decisions resolved (O-1 = resume-message). Ready for the planner re-run. Nothing
> blocks it.

---

## 1. What the feature is (one paragraph)

MediaVault's multi-step commands (`prep` → `push` → `replace`, the
`prep_push_rep` / `prep_push_rep_season` orchestrators, and the
`fetch` → `restore` side) can fail half-way and leave a confusing, undocumented
state. Auto-rollback unifies failure handling into ONE predictable mechanism:
a failure **before** a command's point-of-no-return **auto-rolls-back** to the
exact pre-command state (removing only what *this run* created); a failure
**at/after** the point-of-no-return **hard-fails** with an actionable message
naming an existing resume command (no new "fetch-to-fix" command invented, no
fake/partial rollback); a **batch/season** failure keeps completed items and
prints exactly how to resume the rest.

Original task (verbatim, from `SESSION_LOG.md`):
> Use the planner agent to design an automatic rollback feature: when any
> multi-step command (prep → push → replace, or the prep_push_rep orchestrators,
> or fetch → restore) fails partway, the system should either cleanly roll back
> everything it created so far (reversible case) or, if it has passed the point of
> no return, hard-fail with an actionable message that names the exact existing
> command to recover — never invent a new fetch-to-fix command, and never leave a
> half-finished state with no guidance.

---

## 2. Confirmed decisions (authoritative — see `DECISIONS.md` for full rationale)

| ID | Decision | Status |
|---|---|---|
| D-1 | **Restore / fetch_restore IS in scope** (not just the archive pipeline). | ✅ confirmed |
| D-2 | Architecture step = **UNCAPPED candidate bake-off**; the **judge writes reviews but does NOT auto-pick — the USER selects the winner.** | ✅ confirmed |
| D-3 | Branch from `origin/main`. | ✅ confirmed |
| D-4 | **Happy path stays byte-for-byte identical.** Prefer wrapping over rewriting. | ✅ confirmed |
| D-5 | Tests use **COPIES only** — never touch real `C:\Media\*` or real `library_*.json`. | ✅ confirmed |
| D-6 | Detection mechanism = **snapshot-before** (only remove the set-difference created this run). | ☑️ accepted |
| D-7 | Season parent `season_map`: delete only if **this run created it AND** rolling back its child leaves 0 children; if it pre-existed, only remove this run's child-link and recompute `total_episodes`. | ☑️ accepted |
| D-8 | Rollback is **automatic**, with a clear message (no y/N prompt). | ☑️ accepted |
| D-9 | **Leave** the empty remote `adb mkdir` dir on rollback. | ☑️ accepted |
| D-10 | Sandbox via monkeypatched `LIBRARY_*` / `LOCAL_ROOT`; ffmpeg-generated MKV for split tests (skip if absent). | ☑️ accepted |
| **O-1** | **Push-failure boundary = RESUME-MESSAGE.** A failed multi-chunk push is reversible/resumable (the master file survives), so leave the partial upload in place and print the resume command. **NOT** a point of no return. Drop the original draft's "first chunk-delete = PONR" assumption. | ✅ **confirmed 2026-05-31** |
| **O-2** | **Hard-fail list confirmed:** the only true points of no return are (a) `cmd_replace` after `os.remove(original)`, and (b) `cmd_restore` (split) after deleting merged chunks from `restore/`. Everything else (prep, split, push) is reversible/resumable. `cmd_set_uploaded` is out of scope (pure metadata, not multi-step). | ✅ **confirmed 2026-05-31** |
| **O-3** | **All prerequisites done/merged.** C1 + A7 remain optional and are NOT folded in. | ✅ **resolved 2026-05-31** |

---

## 3. Prerequisites — ALL merged into `main` (re-derive against current code)

| Imp | What it gave us | PR |
|---|---|---|
| **A1** | `mvcommon.py` — shared `load_library`/`save_library`/`calculate_file_hash`/`generate_short_id`/`retry`. | #8 |
| **C9** | Atomic `cmd_replace` two-rename — the original-delete is now crash-safe (but replace is still irreversible once committed). | #5 |
| **C11** | `cmd_restore` hash-mismatch quarantine via the `quarantine_restore_file()` helper — the restore-side clean-state seam auto-rollback should reuse. | #6 |
| **G1** | Push uploads to `<final>.partial` then `adb shell mv` to final name; remote `.mvmeta.json` sidecar. A partial upload is never observable as complete. | #7 |
| **C2** | `retry()` wrapper around adb push + Selenium ops. | #9 |
| **C8** | Post-push remote `sha256sum` verify, gated on `PUSH_VERIFY_REMOTE` (default False), mismatch feeds C2's retry. | #12 |
| **H1** | Agent pipeline migrated to Opus 4.8 effort tiers. | #10 |

**Consequence for the re-plan:** the point-of-no-return analysis and ALL line
numbers in the archived `PLAN.md` / `FAILURE_ANALYSIS.md` are STALE — they predate
these merges. The planner MUST re-derive them against current `main.py`.

---

## 4. Current code map (re-derived 2026-05-31, post-merge — verify before relying)

Functions in `main.py`:
- `cmd_prep` @ **302**, `cmd_set_uploaded` @ 464 (out of scope), `cmd_prep_season` @ 480
- `cmd_push` @ **654** (push chunk delete `os.remove(f)` @ **860**, inside the G1/C2 `_push_and_rename` + `retry` block)
- `cmd_push_group` @ 895
- `cmd_replace` @ **943** (C9 atomic two-rename; the genuine PONR is the original→`.tobedeleted`/remove step in this function)
- `cmd_replace_group` @ 1021
- `cmd_restore` @ **1167** (uses `quarantine_restore_file` @ 1148; split merge via `merge_video_files`; chunk deletes around 1207–1269 — the restore-side PONR)
- `cmd_restore_group` @ 1292
- `cmd_prep_push_rep` @ **1549** (ad-hoc rollback strings to unify away: "Reverting temporary files" @ ~1563, "local_ready … run 'push' manually" @ ~1577)
- `cmd_prep_push_rep_season` @ **1591** ("Stopping Auto-Pilot to prevent mess" `break` @ ~1649 — replace with resume-range messaging)
- `cmd_fetch_restore` @ 1672

`mvcommon.py`: `retry` @ 34, `load_library` @ 67, `save_library` @ 85, `generate_short_id` @ 116, `calculate_file_hash` @ 122.

Tests already exist (the re-plan extends, doesn't bootstrap): `tests/conftest.py`
(fixtures `sandbox`, `sandbox_entry`, `mock_device`, `mock_fetch`, `fake_dummy`),
plus `test_cmd_push_partial.py`, `test_cmd_push_retry.py`, `test_cmd_push_verify.py`,
`test_cmd_push_mock_device.py`, `test_cmd_replace.py`, `test_cmd_restore_quarantine.py`,
`test_mvcommon.py`, `test_trigger_download_retry.py`. **Fixture/model rules live in
`docs/testing-strategy.md`** — follow them.

---

## 5. Corrected point-of-no-return picture (carry into the re-plan)

The original master file is the source of truth. **As long as it exists on disk,
everything is reversible.** It is destroyed in only two places:

| Window | Reversible? | Behavior on failure |
|---|---|---|
| Any `prep` failure | ✅ fully | auto-rollback (remove entry/sidecars/parent-link if this run created them) |
| Any `split` failure | ✅ fully | auto-rollback (remove `_parts`/`_checksums`/`split_info`/entry created this run) |
| `push` failure mid-upload, original present | ✅ **resumable (O-1)** | leave partial upload; **print resume command**; entry stays `local_ready`/`uploaded=False` |
| `replace` after original delete | ❌ irreversible | hard-fail, actionable message (file is in the cloud; use fetch+restore) |
| `restore` (split) after chunks deleted from `restore/` | ❌ needs re-fetch | hard-fail / quarantine (reuse C11's `quarantine_restore_file`) |

Edge cases the mechanism must respect (from `FAILURE_ANALYSIS.md §4`): `cmd_prep`
early-skips return True without creating artifacts (never roll back); `cmd_push`
resumes from a pre-existing `_parts/` (snapshot must record its prior presence);
`split_info` may pre-exist; the season parent is shared across episodes (per-id
snapshots); chunk delete is `try/except: pass` (key the boundary on upload
success, not delete success); Windows file locks can make a rollback delete fail
(report partial-rollback honestly).

---

## 6. Scope for the re-plan

**In scope:** `cmd_prep`, `cmd_push`, `cmd_replace`, `cmd_restore`,
`cmd_fetch_restore`, and the orchestrators `cmd_prep_push_rep` /
`cmd_prep_push_rep_season`. Replace BOTH ad-hoc rollback paths with the one
mechanism. Restore is IN (D-1).

**Out of scope:** new CLI commands/flags; any "fetch-to-fix"/repair command;
changing `save_library` atomic-write, the split algorithm, dummy recipes, or any
success-path field values; `cmd_set_uploaded`; anything under `archive/`. C1
(season auto-resume) and A7 (full pytest harness) are NOT folded in.

**The architecture step (D-2):** uncapped, genuinely-distinct candidates
(snapshot/transaction context-manager wrapper; per-command compensating-action
stack; on-disk operation journal; plus any other legitimate approach). Each runs
to completion; the **judge writes reviews but does NOT auto-pick — the user
selects.**

---

## 7. THE PROMPT TO PASTE INTO THE NEW SESSION

```
Use the planner agent to RESUME and RE-PLAN the auto-rollback feature.

Read these FIRST, in order, before planning:
1. docs/feature-auto-rollback/REPLAN_BRIEF.md  (this consolidated brief — start here)
2. docs/feature-auto-rollback/DECISIONS.md      (authoritative decisions; O-1/O-2/O-3 now resolved)
3. docs/feature-auto-rollback/FAILURE_ANALYSIS.md (the point-of-no-return analysis — line numbers are STALE, re-derive)
4. docs/feature-auto-rollback/PLAN.md            (the ARCHIVED pre-pause draft — structure to build on, but its prereq "defer" notes and line numbers are outdated)
5. docs/feature-auto-rollback/RELATED_IMPROVEMENTS.md, README.md, SESSION_LOG.md (context)
6. ARCHITECTURE.md and current main.py / mvcommon.py / tests/ — RE-DERIVE all line numbers and the point-of-no-return against CURRENT code.

Treat these as FACTS (do not re-derive or re-litigate):
- All prerequisites are DONE and merged into origin/main: A1 (#8), C9 (#5), C11 (#6), G1 (#7), C2 (#9), C8 (#12), H1 (#10).
- O-1 push-failure boundary = RESUME-MESSAGE: a failed multi-chunk push is reversible/resumable (the master file survives); leave the partial upload and print the resume command. It is NOT a point of no return. Drop the old draft's "first chunk-delete = PONR" assumption.
- O-2: the only true points of no return are (a) cmd_replace after deleting the original, and (b) cmd_restore (split) after deleting merged chunks from restore/. Everything else (prep/split/push) is reversible/resumable.
- D-1 restore/fetch_restore IS in scope. D-2 the rollback-mechanism step is an UNCAPPED multi-candidate bake-off; the judge writes reviews but does NOT auto-pick — I (the user) make the final selection. D-4 happy path byte-for-byte identical (prefer wrapping over rewriting). D-5 tests use COPIES only. D-6 snapshot-before detection. D-7 season-parent deletion rule. D-8 automatic rollback. D-9 leave the empty remote dir.
- Reuse existing seams: C11's quarantine_restore_file() for restore clean-state; G1's .partial naming; C2's retry(); A1's mvcommon helpers. Build on the existing tests/ + conftest fixtures (sandbox, sandbox_entry, mock_device, mock_fetch, fake_dummy) per docs/testing-strategy.md.

What to produce: a refreshed PLAN.md with
- restore / fetch_restore IN scope,
- the rollback-mechanism step as an UNCAPPED multi-candidate bake-off (as many genuinely-distinct approaches as exist; judge reviews, I pick),
- per-step [model: ...] and [effort: ...] tags,
- re-derived point-of-no-return + artifact map against current main.py,
- the two ad-hoc rollback paths (cmd_prep_push_rep "Reverting temporary files"/"local_ready" and cmd_prep_push_rep_season "to prevent mess" break) unified into the one mechanism, with season resume-range messaging.

PLAN.md LOCATION CONVENTION (per repo policy): write the plan to BOTH /PLAN.md (root, live, gitignored) AND docs/feature-auto-rollback/PLAN.md (tracked, canonical), identical. Record any new load-bearing choices in docs/feature-auto-rollback/DECISIONS.md.

Suggested branch: feature/auto_rollback (off origin/main). PR title must include no IMP code (auto-rollback is a feature, not a tracked IMP) — use a descriptive title.

There are NO open decisions left to ask me about — O-1/O-2/O-3 are resolved above. If you discover a genuinely new ambiguity, pause and ask; otherwise produce PLAN.md only (no code, no branches yet). End with: branch name, the step list with model/effort tags, and the manual verification commands.
```

---

## 8. Anything still to merge?

No. The auto-rollback feature itself has **no code on any branch** — it was paused
at the planning stage (only docs exist). All prerequisite *code* is already on
`main`. The only outstanding item is **PR #13** (this docs sync + O-1 resolution +
EOL policy), which is awaiting your merge approval. Once PR #13 merges, this brief
and all decisions are on `main`, and the new session starts from a fully accurate
doc set.
