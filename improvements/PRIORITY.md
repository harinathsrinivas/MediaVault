# MediaVault — Priority List (the always-current "what to do next")

> **This file is the single source of truth for task ordering.** It is **updated every time a
> task, bug, or improvement is added, completed, or re-prioritized** (see the maintenance protocol
> at the bottom — this rule is also recorded in `CLAUDE.md` and `improvement_details.md`).
> Visual version: [`../docs/priority-graph/priority-graph.html`](../docs/priority-graph/priority-graph.html)
> — an interactive task graph; click any node to see its details and jump to the tier file.
>
> Full task text lives in `improvements_tier*.md`; this file only orders them. Legend:
> 🔴 critical · 🟠 high · 🟡 medium · ⚪ low · ✅ done · 🚦 needs a user decision (change-gate).
>
> **Last updated:** 2026-09-07 (**IMP-U6 DONE** — provider folder token `{tmdb-…}` → `[tmdbid-…]` + Plex NFO id-pinning shipped on PR #51; live library migrated: 199 folders + 200 NFOs, 1 locked dir pending idempotent retry. **IMP-U6 opened** — provider folder token `{tmdb-…}` → `[tmdbid-…]`
> + Plex NFO id-pinning; D1 `[tmdbid-…]`, D2 suggestions unify on TMDB, D6 NFO-at-stamp all
> user-ruled; in progress on `feature/imp_u6_token_brackets`; plan + evidence:
> `docs/feature-token-brackets/PLAN.md`. Earlier: 2026-09-03 (**IMP-C24 + IMP-D23 registered** — a real data-integrity incident:
> the user ran a season prep+push, the push failed (ADB disconnected), so they resumed with
> `push_group` in one shell while running `replace` per-episode in a SECOND shell to reclaim disk in
> parallel — a legitimate workflow the system has never protected. `mvcommon.load_library()` merges
> all four library JSONs into one dict and `save_library()` rewrites all four from that dict on every
> call, with **no lock anywhere**. Two concurrent mutating commands each hold their own stale
> in-memory snapshot across a slow operation (a multi-GB ADB push, a whole-file hash) and whichever
> saves last silently overwrites the other's change — a classic lost update whose blast radius is
> the ENTIRE library set, not one file. This actually cost 13 corrupted entries and one 9,672-byte
> dummy uploaded to Google Photos in place of a real episode (repaired; the cloud holds the correct
> real files for all 13; see `docs/feature-library-concurrency/SESSION_HANDOFF.md` for the full
> incident record). **IMP-C24** tracks the fix (🚦 change-gated — several candidate approaches touch
> the auto-rollback contract; needs a user ruling before implementation) and is now the
> `👉 SUGGESTED NEXT TASK`, outranking IMP-C22 (both Band 0, but C24 is an *active* silent-corruption
> mechanism vs. C22's already-bounded, already-understood parsing gap). **IMP-D23** tracks the
> companion fix: `cmd_prep` re-hashes an already-prepped `local_ready` entry on every resume attempt
> (the exact expense that pushed the user toward the risky manual-parallel workaround that triggered
> C24's incident) — a cheap resume path removes that incentive. Full options, recommendation, and
> implementation steps for both: `docs/feature-library-concurrency/PLAN.md`. Earlier: **IMP-D22 and
> IMP-C23 both merged to `main`** (PR #47 → `e5e94aa`, PR #48 → `abc2333`). Earlier: **IMP-C23 done** — `_has_tmdb_token` (`main.py`) was missing `re.IGNORECASE`, so an uppercase/mixed-case `{TMDB-…}` folder token (the user has one real folder in this shape, `Run (2002) {TMDB-69590}`) read as "no token" and the idempotency guard let `cmd_enrich_metadata` append a SECOND token on the next pass. Fixed by adding `re.IGNORECASE` to bring it into lockstep with the sibling `_PROVIDER_TOKEN_RE` (which was always case-insensitive) — the same drift-between-duplicated-parsers class as IMP-C18 and IMP-C22, now a third instance. 10 new tests incl. a drift pin asserting the two predicates agree; smoke 80/80, `test_enrich_metadata`+`test_rename_folder`+`test_set_tmdb` 74/74. Shipped on `fix/imp_c23_has_tmdb_token_ignorecase`. Earlier: **IMP-C22 registered** — new Band 0 bug found during D22 anime test coverage: `_episode_se_of` (`main.py` ~line 1725) mis-parses season-glued anime ids instead of delegating to the shared `mvcommon.episode_num_from_id` that IMP-C18 introduced for exactly this class of drift — it's a 4th copy that never got wired to that helper. Silent, no error: per-episode stills + overview/title backfill never land for either real anime id shape (145 affected entries), while show-level enrichment appears to succeed. Pending, not yet fixed; sits in Band 0 alongside the existing `_has_tmdb_token` pointer below — see that section for which to pick first. Earlier: **IMP-D22 done** — `prep_push_rep_enrich`/`prep_push_rep_season_enrich` on `feature/imp_d22_prep_push_rep_enrich`: folds archive + TMDB enrichment into one command over the untouched `cmd_prep_push_rep`/`cmd_prep_push_rep_season` autopilots (both provably zero-diff); `-tmdbid` skip-search or the existing auto-resolve waterfall fallback; `-tvdbid` refused outright (TMDB-only, different numbering space); `{tmdb-…}` folder rename gated behind `--yes`/`--no-rename` confirmation; richer opt-in `.nfo` (`<imdbid>`/`<genre>`/`<runtime>`/`<premiered>`/`<studio>`/`<director>`/`<actor>`, never `<tvdbid>`); no new PONR, `ENTRY_TYPE_KEYS` untouched, 65 new tests, suite 768/smoke 76+. Earlier: **IMP-D21 done** — extras split-failure hardening on `fix/imp_d21_extras_split_hardening`: `push_one_extra`'s SPLIT branch now gates on `refuse_if_unsplittable` before `makedirs` — parity with `cmd_push` and the two autopilots, closing the gap the parallel IMP-C19/C20/C21 work left open for extras — and a failed split now removes any partial chunk it wrote, so the next run's RESUME branch can never inherit them and silently flip `uploaded=True` with no `split_info` (audit finding D1 from the post-D19 extras fix journal). **IMP-D20 also newly registered** — it shipped to `main` via PR #43 (`f40e952`) but was never tracked in this file / the tier file / the priority graph until now: extras checksum-sidecar parity (master + per-chunk `.sha256`), integrity-command coverage (`verify_library`/`check`/`repair_dummies`/`verify_restore`/`local_status`), a `push --extras` no-op warning, and the season-autopilot resume command carrying `--extras`/`--extras-size`. Earlier: **IMP-C21 done** — `tools/remux_unsplittable.py`, the manual fix for a file mkvmerge cannot split: dry-run by default, never destructive, and **never invoked by MediaVault** (guard-tested) per the user's standing decision that codec changes stay a human call. Earlier that day: **IMP-C19 + IMP-C20 done** on `fix/imp_c19_c20_split_diagnostics` — mkvmerge's real error is no longer discarded, and `cmd_push` now refuses a file carrying a track mkvmerge cannot split (FLAC) *before* prep spends a deep scan + whole-file hash. **IMP-R10 opened and left pending by user decision** — a transient lock on the journal during `cmd_replace`'s PONR write is misread as a locked media file and reports a spurious IRREVERSIBLE; change-gated, so it needs a decision before any code moves. Both incidents are written up under `docs/edge-case-*/`. Earlier: IMP-D19 extras option — **done** on `feature/imp_d19_extras`: `--extras`/`--extras-size` on prep/prep_season/push/push_group/prep_push_rep/prep_push_rep_season, new `add_extras`, flag-only `--fetchExtras` on fetch/fetch_restore, grouped `extras` block on title entries (no new entry type), full push→dummy→fetch→restore lifecycle reusing rollback primitives additively (E1), 37 unit tests + 4 smoke cases (suite 648/smoke 76); PR to `main` pending. Earlier: IMP-D18 Others/sports content category — **done** on `feature/imp_d18_others_category`: 4th `oth-` category — `library_others.json`, season_map/leaf reuse, list-capable disk roots via `CATEGORY_ROOTS`, enrichment-skip, Others Chrome profile + Pixel. Earlier: IMP-R6 restore-merge temp-stage + IMP-R7 crash-re-run auto-recover — both done on `feature/imp_r6_r7_restore_journal_crashsafe`. IMP-E16 — UI dossier + /api/detail + EXA auto-resolve + grid + palette + parallax hero + lazy-load perf — on `feature/imp_e16_ui_wow`. Phase 5 IMP-E3/U3/D17 — TMDB enrich + rename_folder + /api/media-image + real posters/titles in SPA. IMP-E14 fully done. IMP-D17 done. IMP-E15 done.)

---

## 👉 SUGGESTED NEXT TASK: fix IMP-C24 — concurrent library writes silently lose updates (no lock)

> **IMP-C24 is next**: registered 2026-09-03 after a real incident — parallel `push_group` (one shell)
> + `replace` (a second shell, to reclaim disk as each episode finished) lost updates on 13 library
> entries and uploaded a dummy file to Google Photos in place of a real episode. `mvcommon.load_library`
> merges all four library JSONs into one dict; `save_library` rewrites all four from that dict on
> every call; there is no lock anywhere. An active silent-corruption bug outranks IMP-C22's
> already-bounded parsing gap — see `improvements_tierC.md` IMP-C24 and the full fix plan at
> `docs/feature-library-concurrency/PLAN.md`. **🚦 Change-gated**: the plan's recommended fix sits
> adjacent to the auto-rollback contract and needs an explicit user ruling before implementation
> (see the plan's Open Decisions) — so the *actionable* next start today is still IMP-C22 (not
> gated, ready to implement) or IMP-S1, while IMP-C24 awaits that ruling.

> **IMP-D23 registered alongside it**: `cmd_prep` re-hashes an already-prepped `local_ready` entry on
> every resume attempt — the exact cost that pushed the user toward the risky manual-parallel
> workaround that triggered C24's incident. See `improvements_tierD.md` IMP-D23 (Band 1 below).

> **IMP-C22 remains ready to implement** (registered 2026-08-29, surfaced by D22 test coverage):
> `_episode_se_of` silently mis-parses season-glued anime ids instead of delegating to the IMP-C18
> shared helper (`mvcommon.episode_num_from_id`), so per-episode enrichment (stills + overview/title
> backfill) never lands for either real anime id shape (145 affected entries), while show-level
> enrichment appears to succeed. See `improvements_tierC.md` IMP-C22.

> **IMP-D18 is done** (4th "Others"/sports content category) on `feature/imp_d18_others_category` — PR to `main` pending. It creates two follow-ons: **IMP-X1** (replicate the Others account's chunks to a 2nd Google account — the same CSAM-ban single-point-of-failure applies to the 4th account) and the open **OD-2** question (whether to ever add a sports metadata scraper, e.g. TheSportsDB; today it's filename-as-title, no scraper).

**IMP-R6 + IMP-R7 done** (crash-safety fixes — restore merge-to-temp + journal auto-recovery on re-run
— on `feature/imp_r6_r7_restore_journal_crashsafe`). **Band 0 is NOT clear** — **IMP-R10** (change-gated,
deferred by user) and now **IMP-C24** (change-gated, awaiting ruling) are both open. **Phase 5 done**
(IMP-E3 partial / IMP-U3 partial / IMP-D17 done). **IMP-E14 fully done**. **IMP-E15 done**.

**Recommended next starts (pick one or run in parallel):**
0. **IMP-C24** — concurrent library writes / lost updates (see pointer above); resolve the Open
   Decisions in `docs/feature-library-concurrency/PLAN.md` first, then implement. Highest urgency.
0b. **IMP-C22** — anime per-episode enrichment mis-parse; not gated, do this if C24's decision is still pending.
0c. **IMP-D23** — prep re-hash on resume (see pointer above); pairs naturally with C24's implementation window.
1. **IMP-S1** — stand up Jellyfin using `improvements/JELLYFIN_SETUP_GUIDE.md`: zero code, immediate
   couch value; the NFOs + posters from Phase 5 will populate the Jellyfin library on first scan.
2. **IMP-S2** — mvdaemon service: the one new component the end goal needs (the web worker is its seed).
3. **IMP-E3 / IMP-U3 breadth** — AniDB/AniList/TheTVDB + per-episode NFOs + anime ids in NFOs +
   backfill review-diff mode (the partial slices still have pending scope).
4. **IMP-A2 → A5 config chain** — argparse CLI + `--json` + full mvconfig migration (unblocks daemon).

**Headline NEXT: IMP-C24 (concurrent library writes — needs a change-gate ruling), then IMP-C22 and
IMP-D23** (both actionable today without waiting on any decision).

> ⚠️ **Band 0 now has two open items: IMP-R10 and IMP-C24, both change-gated.** R10 is blocked on a
> decision the user deferred (2026-08-24, "document more - can fix later"); C24 is blocked on a fresh
> decision (which concurrency-fix approach — see the plan's Open Decisions). Neither blocks IMP-C22,
> which remains actionable today with no gate.

---

## 🔴 BAND 0 — CRITICAL: bugs that break / data-integrity decisions (do first)

**Three open items: IMP-R10** (change-gated, deferred), **IMP-C24** (change-gated, awaiting ruling —
new), and **IMP-C22** (pending, not gated). R10's fix is deferred on a user decision (2026-08-24,
"document more - can fix later"). C24 (registered 2026-09-03) already caused a real incident (13
corrupted entries, one dummy uploaded to Google Photos) and needs a user ruling on which fix approach
before implementation — see `docs/feature-library-concurrency/PLAN.md`. C22 (registered 2026-08-29)
is documented in full and ready to implement — no change-gate blocks it, only implementation capacity.
**IMP-C23 done** (2026-08-31); **IMP-D22 done**, both now merged to `main`.

| # | Task | Why it's critical | Risk to fix | Gate |
|---|---|---|---|---|
| 1 | 🚦 **IMP-C24** | `mvcommon.load_library`/`save_library` have no lock; two concurrent mutating commands (any two of prep/push/replace/restore/`web`) each hold a stale in-memory snapshot across a slow operation and the later save silently erases the earlier one's change — blast radius is all four library JSONs, not one. Already caused a real incident: 13 corrupted entries + one dummy uploaded to Google Photos in place of a real episode | **gated** — the recommended fix touches the wrapping of `cmd_prep`/`cmd_push`/`cmd_replace`/`cmd_restore` and `RollbackJournal`'s own `save_library()` call | 🚦 **needs a user decision** (`CLAUDE.md` change-gate); options + recommendation in `docs/feature-library-concurrency/PLAN.md` |
| 2 | 🚦 **IMP-R10** | a transient lock on `.mediavault_txn.json` during `cmd_replace`'s PONR write is caught by the retry loop meant for a locked *media* file → spurious `IRREVERSIBLE`, and the handler advises `fetch_restore` (a 62 GB re-download) while the master sits on local disk as `.tobedeleted` | **gated** — touches `mark_point_of_no_return()` placement | 🚦 **needs a user decision** (`CLAUDE.md` change-gate); documented in `docs/edge-case-replace-ponr-journal-lock/` |
| 3 | 🟠 **IMP-C22** | `_episode_se_of` mis-parses season-glued anime ids instead of delegating to the IMP-C18 shared helper — a 4th copy of the episode-number parser that drifted; per-episode stills + overview/title backfill silently never land for either real anime id shape (145 affected entries), while show-level enrichment appears to succeed | low-medium — narrows parsing to the existing shared helper; Shape-B's default-season needs a small decision | not change-gated — ready to implement; see `improvements_tierC.md` IMP-C22 |
| ✅ | ✅ **IMP-C23** | `_has_tmdb_token` missing `re.IGNORECASE` — an uppercase `{TMDB-…}` folder token read as "no token" and the idempotency guard let a SECOND token get appended on the next enrich/rename pass; third instance of the drift-between-duplicated-parsers class also seen in IMP-C18/IMP-C22 | low | done — `fix/imp_c23_has_tmdb_token_ignorecase`; added `re.IGNORECASE` + a drift-pin test against `_PROVIDER_TOKEN_RE` |
| ✅ | ✅ **IMP-C19** | mkvmerge's `Error:` lines go to stdout, which `split_video_file` sent to DEVNULL → every split failure was an uninterpretable `exit status 2`, after a 62 GB prep | low | done — `1af16a3`, both split + merge call sites |
| ✅ | ✅ **IMP-C20** | no pre-flight for tracks mkvmerge cannot split (FLAC) → a whole class of sources burned a full prep before failing | low | done — `e2b799c` + `1b1a899` (gate moved ahead of prep in both auto-pilots); detect-and-stop only, never auto-converts |
| ✅ | ✅ **IMP-R6** | failed restore-merge leaves NO file at the path → title disappears from every media server | medium | done — merge-to-temp + os.replace on verify (option a) |
| ✅ | ✅ **IMP-R7** | re-running a command after a crash silently destroys the leftover recovery journal → orphaned artifacts | medium | done — auto-recover pre-PONR leftover on journal-open (option b) |
| ✅ | ✅ **IMP-D4 (partial)** | 107 legacy text-dummy entries hand-reconciled (all verified in Google Photos); `verify_library` status-to-disk invariant + pipeline post-conditions now guard against recurrence — see `docs/feature-legacy-reconcile/REPORT.md` | low | done |

## 🟠 BAND 1 — SUGGESTED NEXT after Band 0 (cheap foundations + immediate value)

| # | Task | Payoff | Risk |
|---|---|---|---|
| 7 | ✅ **IMP-U6** (done, PR #51) | provider folder token `{tmdb-…}` → `[tmdbid-…]` (Jellyfin/Emby-native) + `movie.nfo`/`tvshow.nfo` written at every stamp so Plex pins the id via its official NFO agent — then a journal-backed migration tool converts every existing folder (🚦 dry-run report user-gated before apply). Makes every future stamp useful in all 3 servers; pre-stages IMP-S1's Jellyfin scan. Plan + ruled decisions: `docs/feature-token-brackets/PLAN.md` | low-medium — no rollback/`ENTRY_TYPE_KEYS` change; enrich stamp path + ~145 test literals; live rename sequential + journal-backed |
| 8 | 🟠 **IMP-D23** | `cmd_prep` re-hashes an already-prepped `local_ready` entry on every resume attempt (minutes per large file); new additive `push_rep`/`push_rep_season` commands (or an opt-in `--assume-unchanged` flag) skip the re-hash entirely — removes the incentive for the risky manual workaround that caused IMP-C24's incident | low for the new-command option; low-medium for the opt-in heuristic flag (honestly imperfect, never default) |
| 9 | 🟠 **IMP-S1** | stand up Jellyfin (the `JELLYFIN_SETUP_GUIDE.md` run) — **zero code, immediate couch value**, can run in parallel | low |
| 10 | ⚪ **IMP-A11** | repo hygiene (gitignore stale root `STATUS.md`, drop stray files, clean leftover worktrees) | low |

## 🟡 BAND 2 — FOUNDATIONS that unblock the daemon + new commands

`A2` (argparse) → `A4` (`--json`) → `A5` (config) → `A3` (logging) — this chain underpins the Tier S
daemon and every new command. Then `C3` (doctor), `D4` (verify_library) + `D5` (repair_library),
`C5` (fetch fallback; `C6` session-expiry now ✅ done via IMP-C17).

## 🟢 BAND 3 — ROBUSTNESS / REDUNDANCY (urgent — the CSAM-ban single-point-of-failure)

Research found a Feb-2026 wave of instant, unrecoverable Google bans from CSAM-AI false positives.
The vault's three accounts are a single point of failure. Prioritize:

| Task | Role |
|---|---|
| 🟠 **IMP-X1** | multi-account chunk **replication** (every chunk in ≥2 accounts, all free-unlimited) — the real backup |
| 🟠 **IMP-X2** | backup **topology + account-loss runbook** (records the sharing-vs-replication decision) |
| 🟡 **IMP-X5** | account-health **canary** (early ban warning before a fetch reveals it weeks late) |
| 🟡 **IMP-X4** | cross-account restore + **self-healing** replica repair |
| 🟡 **IMP-X3** | **encrypted/obfuscated** upload (defeats copyright-match + CSAM-AI) — gated on the upload spike + change-gate |

(Depends on / pairs with `IMP-E7` multi-device push and `IMP-E5` phone cleanup.)

## 🔵 BAND 4 — THE END GOAL (couch-vault daemon path — `ROADMAP_END_GOAL.md`)

`S2` (mvdaemon) → `S3` (in-client fetch + notify) → `S4` (grace-archive) → `S5` (smart prefetch),
with `E4` (watch-state), `E9` (library refresh), `E5` (phone cleanup), `U1` (enrichment-before-archive),
`U2` (status home rows). Library beauty: `E3` (metadata) → `E16` (dossier + ratings/trivia + EXA auto-resolve + grid + palette; in_progress on `feature/imp_e16_ui_wow`) → `U3` (NFO/artwork), `U4` (DV-FEL paths).
Feels-instant spikes: `G2` (gphotosdl) → `S7` (fetch hardening); `S6` (watch-while-fetching); `S8`
(proxy-stream). Polish: `U5` (C# plugin), `E12` ✅ /`F10` (ops web UI — E12 shipped; its card-grid SPA is the substrate the future media UI grows on).

## ⚪ BAND 5 — QUALITY / PERF / UTILITY (opportunistic, any time)

Perf: `B1`–`B10`. Utility commands: `D2`,`D3`,`D6`–`D15`. Integration long-tail: `E1`,`E2`,`E6`,
`E8`,`E10`,`E11`. Rollback hardening: `R1`,`R3`,`R4`,`R5`,`R8`,`R9` (R4/R8/R9 are 🚦 change-gated; R6/R7 ✅ done).
Moonshots: `F1`–`F9`. Research-only: `G3`,`G5`,`H2`,`A6`.

## ✅ DONE (36)

`A1` (mvcommon) · `A7` (pytest harness) · `C2` (retry) · `C4` (device pinning) · `C8` (post-push verify) ·
`C9` (atomic replace) · `C11` (restore quarantine) · `C12` (alias crash: scan/local_status) ·
`C13` (single-id alias handling) · `C14` (CLI parser papercuts) · `C15` (micro-robustness) · `C16` (anime fetch profile) · `C17` (fetch-session keep-alive + logged-out detector) · `C18` (anime sSSEE episode-range filter: shared episode_num_from_id + 0-match guard) · `C6` (session-expiry detect — via C17) · `E13` (multi-episode) · `G1` (chunker patterns) ·
`H1` (Opus 4.8 effort tiers) · `H3` (smoke gate + consumer-impact guardrail + data-request protocol) ·
`R2` (recover CLI) · `R6` (restore merge-to-temp + os.replace on verified success — no dummy loss on merge failure) · `R7` (journal-open auto-recovery of pre-PONR leftover + timestamped-preserve of post-PONR leftover) ·
`E12` (web ops console — Disk Reclaim view + suggested next-commands + integrated sort/replace) ·
`A10` (requirements truth-up) · `D16` (scan_reclaimable — four-state reclaim scan behind `web`) · `A12` (CI pipeline — GitHub Actions full + smoke gate on every PR) ·
`D4` (partial: integrity guard — verify_library status-to-disk invariant + warn-only pipeline post-conditions; broader audits remain in_progress) ·
`E14` (web media-type UI — all 5 phases: tabs + fetch/progress + motion/PWA + grouped folder + remote auth + TMDB posters/titles) ·
`E15` (mobile + Tailscale + admin-minted token auth) ·
`D17` (rename_folder — crash-safe cascading folder rename + `{tmdb-…}` token stamp) ·
`D18` (4th "Others"/sports content category — `oth-` prefix + `library_others.json`; season_map/leaf reuse, no new entry type, no rollback change; list-capable disk roots via `CATEGORY_ROOTS`; enrichment-skip; Others Chrome profile + Pixel; web Others tab populated — `feature/imp_d18_others_category`) ·
`D19` (extras option — Specials/Trailers/Behind-the-Scenes archival; `--extras`/`--extras-size` + `add_extras` + flag-only `--fetchExtras`; grouped `extras` block on title entries, no new entry type, no rollback change (E1 additive reuse); 37 unit tests + 4 smoke cases — `feature/imp_d19_extras`) ·
`D20` (extras checksum-sidecar parity (master + per-chunk `.sha256`) + integrity-command coverage (`verify_library`/`check`/`repair_dummies`/`verify_restore`/`local_status`) + `push --extras` no-op warning + season-autopilot resume command carrying `--extras`/`--extras-size` — shipped on `fix/imp_d20_extras_sidecars`, PR #43, squash `f40e952`) ·
`C19` (surface mkvmerge's real error — both split + merge call sites capture stdout+stderr and echo the reason; mkvmerge reports on STDOUT, which was being DEVNULL'd) · `C20` (pre-flight for tracks mkvmerge cannot split — `mkvmerge -J` probe refuses a FLAC-bearing file before prep spends a deep scan + whole-file hash; detect-and-stop only, never auto-converts — `fix/imp_c19_c20_split_diagnostics`) ·
`C21` (`tools/remux_unsplittable.py` — the manual, dry-run-by-default remux tool that makes such a file splittable; computes the `-c:a:N` index instead of assuming it, verifies DV record + duration + optional per-stream checksums, never overwrites or deletes, and is never called by MediaVault — `feature/imp_c21_remux_unsplittable`) ·
`D21` (extras split-failure hardening — `push_one_extra`'s SPLIT branch gated on `refuse_if_unsplittable` before `makedirs`, parity with `cmd_push`/the autopilots; a failed split now removes any partial chunk it wrote so the next run can never resume-and-upload it without `split_info` — closes audit finding D1 — `fix/imp_d21_extras_split_hardening`) ·
`D22` (`prep_push_rep_enrich`/`prep_push_rep_season_enrich` — archive + TMDB enrichment folded into one command over the untouched `cmd_prep_push_rep`/`cmd_prep_push_rep_season` autopilots; `-tmdbid` skip-search, auto-resolve waterfall fallback, `-tvdbid` refused outright (TMDB-only), `{tmdb-…}` rename gated behind `--yes`/`--no-rename` confirmation, richer opt-in `.nfo` (`<imdbid>`/`<genre>`/`<runtime>`/`<premiered>`/`<studio>`/`<director>`/`<actor>`, never `<tvdbid>`); both autopilots provably zero-diff, no new PONR, 65 new tests, suite 768/smoke 76+ — `feature/imp_d22_prep_push_rep_enrich`) ·
`C23` (`_has_tmdb_token` missing `re.IGNORECASE` — an uppercase `{TMDB-…}` folder token read as "no token" and got a SECOND token appended on the next enrich/rename pass; third instance of the drift-between-duplicated-parsers class also seen in IMP-C18/IMP-C22; added `re.IGNORECASE` + a drift-pin test asserting agreement with `_PROVIDER_TOKEN_RE`; 10 new tests, smoke 80/80 — `fix/imp_c23_has_tmdb_token_ignorecase`).

---

## Maintenance protocol (KEEP THIS FILE CURRENT)

Whenever you **add, complete, or re-prioritize** a task:
1. Update its row/band here AND bump the **Last updated** date + the **👉 SUGGESTED NEXT TASK** line.
2. Update the matching task in its `improvements_tier*.md` (status / attributes).
3. Update the graph data in `../docs/priority-graph/priority-graph.html` (the `TASKS`/`EDGES` arrays
   near the top of the `<script>` block — add the node, set its `p` priority + `s` status, wire any
   dependency edges). The graph and this file must always agree.
4. If it's a new bug that breaks something, it goes into **Band 0** and becomes a candidate for the
   👉 NEXT pointer.

> Rule of thumb for ordering: **breakage > data-loss risk > cheap-unblocker > foundation >
> redundancy > end-goal > polish.** Critical bugs always sort above features.
