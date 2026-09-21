# IMP-U6 — Provider-token bracket format: Decisions

**Task:** canonical `[tmdbid-<id>]` square-bracket provider-token format (replacing `{tmdb-…}`) + a
crash-safe, ancestor-aware `migrate_provider_tokens` command for the real library — IMP-U6.
**Branch:** `feature/imp_u6_provider_tokens`.
**Source:** `docs/feature-token-brackets/PLAN.md` §"Open Decisions" (planner recommendations), plus this
run's explicit user rulings recorded below.
**Status:** MIXED — Cards #1, #2, #3, #6, and the new #10 are **USER-CONFIRMED** for this run (see each
card). Cards #4, #5, #7, #8, #9 remain `DRAFT — planner recommendation, not yet user-confirmed`; a resuming
session must NOT treat those as locked and should surface them again at the PR checkpoint.

---

## D1 — Canonical emit format — **USER-CONFIRMED: single-token `[tmdbid-<id>]`**
The user was offered a dual-token option (`{tmdb-…} [tmdbid-…]`, which would satisfy Plex's strict
curly-brace match AND Emby's/Jellyfin's square-bracket convention simultaneously) and explicitly chose
**single-token `[tmdbid-<id>]`** instead.

**Known, accepted tradeoff (record verbatim):** Plex's scanner deliberately ignores content inside square
brackets, so a folder carrying only `[tmdbid-<id>]` loses Plex's forced-exact-ID hint and falls back to
Plex's normal fuzzy title/year match. This does not remove the title from Plex's library or break playback
— it only reduces match certainty for edge-case titles. Emby (community-confirmed, not 100%
primary-source-pinned on separator/tag leniency — see D9) and Jellyfin (officially documented) both read
`[tmdbid-<id>]` natively.

**Escape hatch:** every emission site routes through `mvcommon.CANONICAL_TMDB_TOKEN_FMT` /
`CANONICAL_TVDB_TOKEN_FMT` — switching to dual-token emission later, if a specific Plex title is ever found
to strict-match incorrectly, is a one-line format-string change, not a redesign.

*(Planner's PLAN.md Open Decisions #1 and #7 are the same decision from two angles; both are resolved by
this single ruling.)*

## D2 — Finish the ~202 stragglers forward vs. restore-and-redo — **USER-CONFIRMED (accepted by proceeding)**
The user did not explicitly re-litigate this point but approved proceeding with this plan, whose entire
migration design (Step 6) is built on the "finish forward" premise (idempotent, additive, ancestor-gap-only
renames). Restoring the other tool's `03:52` backup and re-running from scratch would needlessly re-touch
1396 already-correctly-migrated folders for zero benefit, and risks losing already-verified-safe work — so
proceeding with this plan constitutes accepting the planner's recommendation.

## D3 — Remote-path (block 3g) remedy — **USER-CONFIRMED: report-only `remote_bearing` field, no schema change, no adb reconciliation**
**Corrected finding, verified by re-reading the code (not assumed):** `mainfetch.py` never derives a
filesystem path from `REMOTE_ROOT`. Fetch and restore work entirely via Google-Photos search on
`search_term`/`filename`; a folder rename on disk does **not** break the ability to find or restore an
already-archived item. The original PLAN.md framing ("renames orphan the remote copy") was broader than
the code actually supports.

Given that, the chosen remedy is deliberately light: `migrate_provider_tokens --apply`'s per-run JSON
report records `"remote_bearing": true` for every rename that touched an entry with `uploaded` truthy or
`status` in `("onboarded", "archived", "restored_local")` — for future reference only. Explicitly rejected
for this PR: (a) adding a persisted `remote_path` field to the library schema, (b) any live `adb`-side
phone-directory reconciliation. Neither is needed today; both would add real scope/risk against a problem
that is currently theoretical (relevant only if/when IMP-E5 phone-cleanup tooling is ever built).

## D4 — Keep the mkvmerge brace-escape in `split_video_file`? — DRAFT — planner recommendation, not yet user-confirmed
**Recommended: keep unchanged.** It is harmless for square-bracket-only folder names (proven by the
existing `test_split_no_braces_path_is_unchanged` test), still actively needed for the one residual real
brace folder and any future legacy import, and is already regression-tested. CLAUDE.md's standing rule is
explicit that a regression-tested behavior must not be silently deleted. Step 10 adds one new pin test
(`test_split_square_bracket_path_is_unchanged`) rather than touching the function itself.

## D5 — Also normalize `[tvdbid-…]`? — DRAFT — planner recommendation, not yet user-confirmed
**Recommended: nothing to normalize.** `[tvdbid-…]` is already canonical square-bracket, source-provided
(release-group naming), never MediaVault-generated. Detection recognizes it (Step 1), emission never
touches it, and the migration command preserves it verbatim when it coexists with a migrated tmdb token in
the same folder name (Step 6, via `find_provider_tokens`'s `span` for a substring-only replace).

## D6 — Touch the real library in this PR at all? — **USER-CONFIRMED: no**
Ship code + tests only, on the feature branch, exercised only against fixtures (`sandbox`/`sandbox_alias`).
Never touch real `C:\Media` or real `library_*.json`. The actual `migrate_provider_tokens --apply` run
against the real library is a separate, explicit, user-run procedure documented at the end of PLAN.md,
performed only after this PR merges to `main` — matching the user's own instruction: "for now, just create
the plan to do this."

## D7 — Single canonical token vs. dual-token emission — DRAFT — planner recommendation, not yet user-confirmed (superseded in substance by D1)
This card is the same decision as D1 viewed from the "make literally all 3 servers work" angle. **D1 has
already ruled on this** (single-token `[tmdbid-<id>]`), so this card is effectively decided in substance.
It is kept on the record, unmarked as DRAFT-superseded rather than deleted, because PLAN.md documents
dual-token emission as a legitimate, low-risk future follow-up (via `CANONICAL_TMDB_TOKEN_FMT`) if the user
later finds a specific Plex title failing to strict-match — that option should stay visible for a future
session, not be erased.

## D8 — IMP code / tier / Band placement — DRAFT — planner recommendation, not yet user-confirmed
**Recommended: IMP-U6** (Tier U — Couch UX & Clients; confirmed free, U1–U5 already exist), registered as a
**single** task rather than split into a separate Tier-C bug code for the live artwork-inheritance
regression, since both are fixed by the identical code change in the same PR — splitting would only create
bookkeeping duplication. Recommended elevation into **PRIORITY.md Band 0** (critical) specifically because
of the live regression (artwork-inheritance ancestor walk matching almost nothing against the
already-migrated real library) plus the active double-stamp corruption risk on the real library today,
mirroring IMP-C23's precedent (same drift-bug-class, was also Band 0). Finalized at Step 14.

## D9 — Emby separator/tag-name leniency — DRAFT — planner recommendation, not yet user-confirmed
Not 100%-primary-source-pinned: TRaSH-Guides' documented preset says `[tmdb-…]` (no "id", `-` separator),
while a staff-confirmed Emby community forum post shows `[tmdbid=…]` (with "id", `=` separator) also
working in practice. **Recommended:** detection tolerates both spellings (`tmdb`/`tmdbid`) and both
separators (`-`/`=`) on the square-bracket family only (cheap, safe, already specified in Step 1's locked
contract). Emission stays fixed at the single canonical `[tmdbid-<id>]` form regardless of this
uncertainty — so ambiguity on Emby's exact leniency cannot cause a regression in either direction, only a
currently-unquantifiable, likely-small difference in match confidence versus Emby's own most-conservative
documented preset.

## D10 — Candidate checkpoints waived for this run — **USER-CONFIRMED**
PLAN.md marks Steps 1 and 6 with 🚦 "user picks the winner" — i.e. after `judge-v2` writes its `DECISION.md`
for each of these two multi-candidate steps, the orchestrator would normally STOP and relay the full
comparison to the user for an explicit pick before merging either candidate.

**The user has explicitly overridden that for this execution:** `judge-v2` decides, the orchestrator merges
the judge's recommended candidate, and execution continues immediately without stopping for either
checkpoint. The user reviews both steps' `DECISION.md` files later, at the PR checkpoint (Checkpoint 1),
rather than in real time. This override is recorded here explicitly so that a future session reading
PLAN.md alone — which still shows the 🚦 markers as written — does not mistakenly expect two pauses that
never actually happened during this run. The two REAL, still-active human gates for this task are
**Checkpoint 1** (PR merge to `main`) and **Checkpoint 2** (branch archive after merge) — both unchanged
and still fully human-gated, per CLAUDE.md's git-and-PR conventions.

---

## Added requirement — Cross-session / cross-account resumable execution
Every step/execution must be resumable so any fresh session — including a different Claude account or
machine — can pick up exactly where work stopped and never re-decide or re-run a completed step. Mechanism:
the feature branch + its commit history + the two tracked files under `docs/feature-token-brackets/`
(`PLAN.md`, this file, and the companion `PROGRESS.md` execution journal — updated + committed after every
step, in the same commit as that step's own work). Full spec + resume protocol: see `PROGRESS.md`'s "Resume
protocol" section and Step 0 above (mirrors the `docs/feature-extras/` IMP-D19 precedent exactly).

## D11 — Canonical format is `[tmdb-<id>]` (SUPERSEDES D1's `[tmdbid-<id>]`)

**Status: USER-CONFIRMED, 2026-09-22, on empirical evidence.**

D1 chose `[tmdbid-<id>]` and recorded "Plex ignores bracketed content" as an accepted tradeoff.
**That premise was wrong**, and so was the follow-on advice to consider dual-token emission.

A 20-folder matrix was built at `D:\MediaVaultTokenTest` and scanned by the user's real Plex, Emby
and Jellyfin servers. Every folder used a nonsense title (`Zyrquat …`) and a deliberately wrong year,
so a correct match could only come from reading the token. Both controls behaved (no-token folder
matched nothing; real-title folder matched), and per-server item counts reconciled exactly against
same-movie merges, which is how the Plex `[tmdb-]` result was confirmed.

| Token | Plex | Emby | Jellyfin |
|---|---|---|---|
| `{tmdb-680}` | yes | yes | yes |
| **`[tmdb-27205]`** | **yes** | **yes** | **yes** |
| `[tmdbid-603]` | **NO** | yes | yes |
| `{tmdbid-550}` | NO | yes | yes |
| `{tmdb=13}` / `[tmdb=238]` / `[tmdbid=11]` | NO | yes | yes |
| `(tmdb-105)` | yes | **NO** | yes |
| `(tmdbid-78)` | NO | NO | yes |
| dual `{tmdb-12} [tmdbid-12]` | yes | yes | **folder vanished from the library** |

**What this actually shows:** Plex does **not** ignore square brackets — the widely-repeated claim,
which this branch's own docs had adopted. Plex rejects the `tmdbid` keyword and the `=` separator;
bracket style is irrelevant to it. The `id` suffix was the entire defect, and it is what made ~1400
already-migrated folders invisible to the user's primary media server.

Two formats survived on all three servers: `{tmdb-<id>}` and `[tmdb-<id>]`. **`[tmdb-<id>]` is
chosen** — square brackets were the user's original request, and it is one character shorter than
what the library already had.

**Also retired by this evidence:**
- **Dual-token emission** — floated as "the only way to satisfy all three". Jellyfin drops such a
  folder from the library entirely, which is worse than either single format. Never ship it.
- **NFO-at-stamp** (the competing branch's headline feature, which this branch was advised to graft).
  Its sole purpose was rescuing Plex from bracketed tokens. Plex reads `[tmdb-]` natively, so the
  feature would cost a per-library agent change and the loss of Plex watch-state sync to solve a
  problem that no longer exists. See `COMPARISON.md` — that recommendation is withdrawn.

**Consequence for the migration:** `[tmdbid-<id>]` is no longer canonical, so it is now itself an old
format to convert. The live worklist went from **1 folder to 237** (dry-run 2026-09-22:
`scanned=317 would-rename=237 already-canonical=0 remote-bearing=236`). Detection deliberately still
accepts `[tmdbid-…]` — the library is entirely in that shape until the migration runs.

**Still open before `--apply`:** 21 of the 237 folders carry another bracket group *before* the token
(`… [1080p] (Dual Audio) [tmdbid-13916]`, `…-NOGRP[rartv] [tmdbid-60574]`, `… [Tam + Tel + Hin] […]`).
Test row P — that exact shape with `[tmdbid-]` — failed on **both Plex and Jellyfin**, while the same
token without a preceding group passed on Jellyfin. Rows Q/R/S/T at `D:\MediaVaultTokenTest` isolate
whether the preceding bracket or the keyword caused it. Do not run `--apply` until that is answered:
if position matters, the migration should move the token ahead of the noise group for those 21 rather
than leaving them broken on two of three servers.

