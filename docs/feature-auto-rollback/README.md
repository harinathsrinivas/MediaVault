# Feature: Auto-Rollback for Multi-Step Commands

> **STATUS: PLANNING — all prerequisites merged; ready for the auto-rollback re-plan.**
> An archived draft implementation plan exists; it now needs to be re-planned
> against current `main` (all prerequisites have landed).
> **Done & merged:** A1 (`mvcommon.py`), G1 (`.partial` + atomic rename, PR #7),
> C9 (atomic `cmd_replace`, PR #5), C11 (restore quarantine, PR #6), C2
> (ADB/Selenium retry, PR #9), C8 (post-push remote verification, PR #12).
> **Next up:** the auto-rollback re-plan itself — resolve the open `DECISIONS.md`
> items (O-1 push-failure boundary) and refresh `PLAN.md`. See `_TRACKER.md`.

This folder is the single source of truth for the auto-rollback feature. It was
produced during a planning session (the planner agent + a Q&A round with the
user). It exists so that **any future session or sub-agent — including ones
working only on a prerequisite improvement (C9 / C11 / G1) — can get full
context without re-deriving it.**

---

## What this feature is (one paragraph)

MediaVault's multi-step commands (`prep` → `push` → `replace`, the
`prep_push_rep` / `prep_push_rep_season` orchestrators, and the
`fetch` → `restore` side) can fail half-way and leave a confusing, undocumented
state. Auto-rollback unifies failure handling into one predictable mechanism:
a failure **before** a command's point-of-no-return **auto-rolls-back** to the
exact pre-command state (removing only what *this run* created); a failure
**at/after** the point-of-no-return **hard-fails** with an actionable message
naming an existing resume command (no new "fetch-to-fix" command is invented,
and no fake/partial rollback is attempted); a **batch/season** failure keeps the
completed items and prints exactly how to resume the rest.

---

## File index — what each file covers

| File | What it contains | Read it if you… |
|---|---|---|
| `README.md` | This outline + status + onboarding for prerequisite-improvement agents. | …are starting cold. **Start here.** |
| `PLAN.md` | The full draft implementation plan (planner output): steps, model assignments, the multi-candidate architecture step, risks, verification, out-of-scope. Archived snapshot as of the pause. | …will implement or re-plan auto-rollback itself. |
| `DECISIONS.md` | Every decision: confirmed, accepted-default, and still-open — each with rationale and status. The authoritative decision record. | …need to know *why* something was chosen, or what is still undecided. |
| `FAILURE_ANALYSIS.md` | The technical core: what each command creates/mutates, the precise point-of-no-return per command (with current `main.py` line refs), the reversible-vs-irreversible boundary, and concrete failure walk-throughs (Examples A/B/C). | …are touching `cmd_push`, `cmd_replace`, or `cmd_restore` for **any** reason. |
| `RELATED_IMPROVEMENTS.md` | The prerequisite + complementary improvements (C9, C11, G1, C1, C2, A1, A7): how each relates to auto-rollback, what auto-rollback will expect from it, suggested order, and per-item "leave-the-seam" guidance. | …are implementing one of the prerequisite improvements. **Required reading for that.** |
| `SESSION_LOG.md` | The full narrative of the planning session: original task, investigation findings, planner dispatch, the verbatim Q&A with the user, and the analysis corrections. | …want the complete history / "how did we get here." |
| `_TRACKER.md` | Live dashboard of the prerequisite tasks (statuses, links, suggested order). | …want the current state of the prerequisite work. |
| `_VAULT-GUIDE.md` | Obsidian-vs-Notion recommendation + free cross-device sync setup (iPad/iPhone/Mac/Windows). | …are setting this up in a notes app across devices. |
| `<ID>-<slug>/` subfolders | One per prerequisite improvement — its ready-to-paste Claude prompt, definition-of-done checklist, completion report, and (once started) its `PLAN.md` + test notes. | …are implementing a specific prerequisite (C9/C11/G1/C1/C2/A1/A7). |

Each prerequisite has its own subfolder so it becomes a self-contained record:
`C9-atomic-replace/`, `C11-restore-quarantine/`, `G1-push-partial-atomic-rename/`,
`C1-season-auto-resume/`, `C2-adb-selenium-retry/`, `A1-extract-mvcommon/`,
`A7-pytest-harness/`. Start from `_TRACKER.md`.

---

## Onboarding for an agent working on a PREREQUISITE improvement (C9 / C11 / G1)

You may have been spawned to implement **just one** of the improvements that the
user chose to do *before* auto-rollback. You are NOT implementing auto-rollback.
But auto-rollback depends on the code you are about to touch, so:

1. **Read `RELATED_IMPROVEMENTS.md` → your improvement's section.** It tells you
   exactly how your change interacts with the planned rollback work and what seam
   to leave behind.
2. **Read `FAILURE_ANALYSIS.md`.** It documents the current point-of-no-return in
   the exact functions you are editing (`cmd_replace` for C9, `cmd_restore` for
   C11, `cmd_push` for G1). Your change will move or harden that boundary —
   understand it before you edit.
3. **Honor the shared constraints below** (they apply to your task too).
4. **When your improvement is complete**, mark its item status in the relevant
   `improvements_tier*.md` file (the user tracks this), and note in your PR/commit
   that it was done as a prerequisite for auto-rollback so the rollback re-plan
   can account for it.

### Shared hard constraints (apply to every task in this repo)

- **Never touch real media files** under `C:\Media\Movies`, `C:\Media\Series`,
  `C:\Media\Anime`, or the real library JSONs `C:\Media\library_*.json`.
  Re-hashing or rewriting a real file causes a hash mismatch in the library.
  Tests and experiments use **copies** in a temp sandbox only.
- **Keep the happy path byte-for-byte identical.** Prefer wrapping/adding over
  rewriting working success-path logic. Do not introduce new failure modes for
  normal scenarios.
- **Branch from `origin/main`**, not from a feature branch. (As of 2026-05-28
  `origin/main` is code-identical to the working tree and fully up to date.)
- **Tests** go in `tests/` (currently empty except `.gitkeep`). This feature
  bootstraps the first real tests; coordinate naming with IMP-A7.
- Do not touch anything under `archive/` (historical snapshots).

---

## How this feature resumes

When the user returns, the planner agent will be re-run **against the updated
code** (after any prerequisite improvements have landed) and will refresh
`PLAN.md`, incorporating:

- **Restore / `fetch_restore` is IN scope** (confirmed).
- The architecture step is an **uncapped candidate bake-off**: the planner
  proposes as many genuinely-distinct rollback-mechanism approaches as exist,
  each runs to completion, the judge writes a review, and **the user makes the
  final selection** (the judge does NOT auto-pick).
- The user's **push-failure boundary** choice (see `DECISIONS.md`, open item).
- Whatever prerequisite improvements were completed (which change the
  point-of-no-return analysis).

**PLAN.md location convention.** The live working copy the orchestrator/planner
consume is `/PLAN.md` at the repo root — it is **gitignored and never committed.**
The **canonical, tracked** copy is this folder's `docs/feature-auto-rollback/PLAN.md`
(plus `DECISIONS.md` and the per-task completion reports), which ships with the
feature branch. Today the folder copy is the archived draft as of the pause;
re-sync it (and root `/PLAN.md`) after a re-plan.
