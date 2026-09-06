# PROGRESS — IMP-U6 (provider token `[tmdbid-…]` + Plex NFO id-pinning)

> Resumable-state journal (protocol of `docs/feature-extras/PROGRESS.md`). Updated + committed in
> the SAME commit as every step. Root `/PLAN.md` carries the step ticks; the canonical plan is
> `docs/feature-token-brackets/PLAN.md`; rulings in `DECISIONS.md`.

## ▶ NEXT ACTION

Step 1 — predicate core: token regex family + `_has_provider_token` + `format_tmdb_token` +
`strip_tmdb_tokens` in `main.py`; rewrite `_has_tmdb_token` to either-shape; re-point
`_PROVIDER_TOKEN_RE`; rework the IMP-C23 drift-pin tests.

## Standing hazards

- **Explicit pathspec commits only** — never `git add -A` (root carries user files: `.zcode/`,
  `docs/ZCODE_ONBOARDING.md`; see `docs/STANDALONE_TOOLS.md` §5).
- **IMP-C24 discipline** — never run two mutating MediaVault commands in parallel; the Step 8
  rename loop is strictly sequential.
- **Change-gate NOT tripped** — no rollback-journal/PONR/`RollbackHardFail`/`ENTRY_TYPE_KEYS` edits.
- Root `/PLAN.md` was rotated from the parked IMP-C24/D23 live plan (byte-identical to the tracked
  `docs/feature-library-concurrency/PLAN.md` — verified before rotation, nothing lost).

## Blockers / human gates

| Gate | State |
|---|---|
| Step 8 — live-library rename (`--apply`) | 🚦 Human-gated: requires the user's explicit approval of the dry-run report |
| Checkpoint 1 — merge PR to `main` | 🚦 Human-gated (never `gh pr merge` without explicit approval) |
| Checkpoint 2 — archive merged branch | 🚦 Human-gated, separate |

## Step status

| Step | Description | Status | Commit | Tests | Notes |
|---|---|---|---|---|---|
| 0 | Bootstrap: branch + IMP-U6 registration + graph + DECISIONS/PROGRESS | done | (this commit) | graph node/edge syntax check | branch `feature/imp_u6_token_brackets` from `main` @ `562fb4a` |
| 1 | Predicate core (regex family, `_has_tmdb_token` either-shape, `_PROVIDER_TOKEN_RE`) | pending | | | |
| 2 | Stamp sites + NFO-at-stamp + `suggest_target_folder` + UI/help text | pending | | | |
| 3 | Artwork walk any-provider/any-shape | pending | | | |
| 4 | Test sweep (13 files) + semantic pins + split-brace rework + new tests | pending | | | |
| 5 | `tools/migrate_token_brackets.py` + tests | pending | | | |
| 6 | Docs (ARCHITECTURE / README / OPERATIONS_QA / DECISIONS addenda) | pending | | | |
| 7 | Full gates: `python -m pytest -q` + `tests/smoke` + node JS test | pending | | | |
| 8 | 🚦 Live-library rename + NFO backfill (dry-run → user gate → apply → verify) | pending | | | |
| 9 | PR + closeout (STOP at Checkpoint 1) | pending | | | |

## Run history (append-only — every interruption recorded)

- **2026-09-07 (planning session):** full repo sweep (code/tests/docs) + web research
  (Jellyfin/Emby/Plex id-tag syntaxes; Plex NFO agent). PLAN.md written (root live + canonical);
  D1 `[tmdbid-<id>]`, D2 unify-on-TMDB, D6 NFO id-pinning ruled by the user; D3/D4/D5 defaulted.
  Root PLAN.md rotated from C24/D23 (verified byte-identical to tracked canonical first).
- **2026-09-07 (execution session start):** user said "execute all steps". Branch created from
  up-to-date `main` (`562fb4a`). Step 0 done.

## Resume protocol (what a fresh session does FIRST)

1. Read this file's `▶ NEXT ACTION` + Step-status table; read `DECISIONS.md` rulings; read
   root `/PLAN.md` (live) + `docs/feature-token-brackets/PLAN.md` (canonical).
2. `git status` — if uncommitted edits exist, inspect `git diff` and reconcile against the table
   (**trust git over the table on disagreement**); park or discard explicitly.
3. `git log --oneline main..HEAD` vs the Commit column; re-run the last step's Verification before
   continuing (never resume on assumed-green).
4. Never resume across a 🚦 human gate (Step 8 apply, PR merge, branch archive) without the user's
   explicit word.
