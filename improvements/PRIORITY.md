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
> **Last updated:** 2026-08-29 (**IMP-C22 registered** — new Band 0 bug found during D22 anime test coverage: `_episode_se_of` (`main.py` ~line 1725) mis-parses season-glued anime ids instead of delegating to the shared `mvcommon.episode_num_from_id` that IMP-C18 introduced for exactly this class of drift — it's a 4th copy that never got wired to that helper. Silent, no error: per-episode stills + overview/title backfill never land for either real anime id shape (145 affected entries), while show-level enrichment appears to succeed. Pending, not yet fixed; sits in Band 0 alongside the existing `_has_tmdb_token` pointer below — see that section for which to pick first. Earlier: **IMP-D22 done** — `prep_push_rep_enrich`/`prep_push_rep_season_enrich` on `feature/imp_d22_prep_push_rep_enrich`: folds archive + TMDB enrichment into one command over the untouched `cmd_prep_push_rep`/`cmd_prep_push_rep_season` autopilots (both provably zero-diff); `-tmdbid` skip-search or the existing auto-resolve waterfall fallback; `-tvdbid` refused outright (TMDB-only, different numbering space); `{tmdb-…}` folder rename gated behind `--yes`/`--no-rename` confirmation; richer opt-in `.nfo` (`<imdbid>`/`<genre>`/`<runtime>`/`<premiered>`/`<studio>`/`<director>`/`<actor>`, never `<tvdbid>`); no new PONR, `ENTRY_TYPE_KEYS` untouched, 65 new tests, suite 768/smoke 76+. Earlier: **IMP-D21 done** — extras split-failure hardening on `fix/imp_d21_extras_split_hardening`: `push_one_extra`'s SPLIT branch now gates on `refuse_if_unsplittable` before `makedirs` — parity with `cmd_push` and the two autopilots, closing the gap the parallel IMP-C19/C20/C21 work left open for extras — and a failed split now removes any partial chunk it wrote, so the next run's RESUME branch can never inherit them and silently flip `uploaded=True` with no `split_info` (audit finding D1 from the post-D19 extras fix journal). **IMP-D20 also newly registered** — it shipped to `main` via PR #43 (`f40e952`) but was never tracked in this file / the tier file / the priority graph until now: extras checksum-sidecar parity (master + per-chunk `.sha256`), integrity-command coverage (`verify_library`/`check`/`repair_dummies`/`verify_restore`/`local_status`), a `push --extras` no-op warning, and the season-autopilot resume command carrying `--extras`/`--extras-size`. Earlier: **IMP-C21 done** — `tools/remux_unsplittable.py`, the manual fix for a file mkvmerge cannot split: dry-run by default, never destructive, and **never invoked by MediaVault** (guard-tested) per the user's standing decision that codec changes stay a human call. Earlier that day: **IMP-C19 + IMP-C20 done** on `fix/imp_c19_c20_split_diagnostics` — mkvmerge's real error is no longer discarded, and `cmd_push` now refuses a file carrying a track mkvmerge cannot split (FLAC) *before* prep spends a deep scan + whole-file hash. **IMP-R10 opened and left pending by user decision** — a transient lock on the journal during `cmd_replace`'s PONR write is misread as a locked media file and reports a spurious IRREVERSIBLE; change-gated, so it needs a decision before any code moves. Both incidents are written up under `docs/edge-case-*/`. Earlier: IMP-D19 extras option — **done** on `feature/imp_d19_extras`: `--extras`/`--extras-size` on prep/prep_season/push/push_group/prep_push_rep/prep_push_rep_season, new `add_extras`, flag-only `--fetchExtras` on fetch/fetch_restore, grouped `extras` block on title entries (no new entry type), full push→dummy→fetch→restore lifecycle reusing rollback primitives additively (E1), 37 unit tests + 4 smoke cases (suite 648/smoke 76); PR to `main` pending. Earlier: IMP-D18 Others/sports content category — **done** on `feature/imp_d18_others_category`: 4th `oth-` category — `library_others.json`, season_map/leaf reuse, list-capable disk roots via `CATEGORY_ROOTS`, enrichment-skip, Others Chrome profile + Pixel. Earlier: IMP-R6 restore-merge temp-stage + IMP-R7 crash-re-run auto-recover — both done on `feature/imp_r6_r7_restore_journal_crashsafe`. IMP-E16 — UI dossier + /api/detail + EXA auto-resolve + grid + palette + parallax hero + lazy-load perf — on `feature/imp_e16_ui_wow`. Phase 5 IMP-E3/U3/D17 — TMDB enrich + rename_folder + /api/media-image + real posters/titles in SPA. IMP-E14 fully done. IMP-D17 done. IMP-E15 done.)

---

## 👉 SUGGESTED NEXT TASK: fix `_has_tmdb_token`'s missing `re.IGNORECASE` — cheap, self-contained bug surfaced by IMP-D22

> **IMP-D22 is done** (`prep_push_rep_enrich`/`prep_push_rep_season_enrich`) on `feature/imp_d22_prep_push_rep_enrich`. It surfaced a real bug worth fixing next, cheaply, before the broader daemon push: `_has_tmdb_token` (`main.py`) has no `re.IGNORECASE`, so it fails to match an uppercase `{TMDB-…}` folder token — the user has one real folder in this exact shape — and such a folder would get a SECOND token appended on the next enrich/rename pass. Small, self-contained, correctness-bug fix; it outranks the deeper "parse an existing `{tmdb-NNNN}` token back into an id" and "read existing `.nfo` files as an id source" follow-ons (both real, both bigger, both queued behind this) because it is an active correctness bug rather than a missed optimization. IMP-S1/S2 remain the headline **strategic** next after this quick fix.

> **IMP-C22 now sits in Band 0 alongside this pointer** (registered 2026-08-29, also surfaced by D22 test coverage): `_episode_se_of` silently mis-parses season-glued anime ids instead of delegating to the C18 shared helper, so per-episode enrichment (stills + overview/title) never lands for either real anime id shape. Bigger than the `_has_tmdb_token` fix (touches a 4th parser copy + an open question about Shape-B's default season) but the same "surfaced by D22, cheap-ish, self-contained" flavor. The `_has_tmdb_token` pointer above is left unchanged; pick between the two, or do both — user's call.

> **IMP-D18 is done** (4th "Others"/sports content category) on `feature/imp_d18_others_category` — PR to `main` pending. It creates two follow-ons: **IMP-X1** (replicate the Others account's chunks to a 2nd Google account — the same CSAM-ban single-point-of-failure applies to the 4th account) and the open **OD-2** question (whether to ever add a sports metadata scraper, e.g. TheSportsDB; today it's filename-as-title, no scraper). IMP-S1/S2 remain the headline next after the D22 bug fix.

**IMP-R6 + IMP-R7 done** (crash-safety fixes — restore merge-to-temp + journal auto-recovery on re-run
— on `feature/imp_r6_r7_restore_journal_crashsafe`). **Band 0 is NOT clear as of 2026-08-24** — **IMP-R10** is open and change-gated
(see below); the user has deferred the fix but the decision is still outstanding. **Phase 5 done** (IMP-E3 partial / IMP-U3 partial / IMP-D17 done). **IMP-E14 fully done**.
**IMP-E15 done**.

**Recommended next starts (pick one or run in parallel):**
0. **`_has_tmdb_token` `re.IGNORECASE` fix** — cheap, self-contained bug from IMP-D22 (see pointer above); do this first.
1. **IMP-S1** — stand up Jellyfin using `improvements/JELLYFIN_SETUP_GUIDE.md`: zero code, immediate
   couch value; the NFOs + posters from Phase 5 will populate the Jellyfin library on first scan.
2. **IMP-S2** — mvdaemon service: the one new component the end goal needs (the web worker is its seed).
3. **IMP-E3 / IMP-U3 breadth** — AniDB/AniList/TheTVDB + per-episode NFOs + anime ids in NFOs +
   backfill review-diff mode (the partial slices still have pending scope).
4. **IMP-A2 → A5 config chain** — argparse CLI + `--json` + full mvconfig migration (unblocks daemon).

**Headline NEXT: the `_has_tmdb_token` bug fix (quick), then IMP-S1** (zero code, do anytime — Jellyfin is the couch surface the vault is being built for).

> ⚠️ **Band 0 has one open item again (2026-08-24): IMP-R10.** It is blocked on a *decision*, not on work — the fix touches `mark_point_of_no_return()` placement and is change-gated. The user deferred it ("can fix later"), so IMP-S1 remains the actionable next start, but R10 outranks everything here the moment the gate is answered. Its sibling bugs from the same incident, IMP-C19 and IMP-C20, are done.

---

## 🔴 BAND 0 — CRITICAL: bugs that break / data-integrity decisions (do first)

**Two open items: IMP-R10** (change-gated) and **IMP-C22** (pending, not gated). R10's fix is deferred on a user decision (2026-08-24, "document more - can fix later"). C22 (registered 2026-08-29) is documented in full and ready to implement — no change-gate blocks it, only implementation capacity.

| # | Task | Why it's critical | Risk to fix | Gate |
|---|---|---|---|---|
| 1 | 🚦 **IMP-R10** | a transient lock on `.mediavault_txn.json` during `cmd_replace`'s PONR write is caught by the retry loop meant for a locked *media* file → spurious `IRREVERSIBLE`, and the handler advises `fetch_restore` (a 62 GB re-download) while the master sits on local disk as `.tobedeleted` | **gated** — touches `mark_point_of_no_return()` placement | 🚦 **needs a user decision** (`CLAUDE.md` change-gate); documented in `docs/edge-case-replace-ponr-journal-lock/` |
| 2 | 🟠 **IMP-C22** | `_episode_se_of` mis-parses season-glued anime ids instead of delegating to the IMP-C18 shared helper — a 4th copy of the episode-number parser that drifted; per-episode stills + overview/title backfill silently never land for either real anime id shape (145 affected entries), while show-level enrichment appears to succeed | low-medium — narrows parsing to the existing shared helper; Shape-B's default-season needs a small decision | not change-gated — ready to implement; see `improvements_tierC.md` IMP-C22 |
| ✅ | ✅ **IMP-C19** | mkvmerge's `Error:` lines go to stdout, which `split_video_file` sent to DEVNULL → every split failure was an uninterpretable `exit status 2`, after a 62 GB prep | low | done — `1af16a3`, both split + merge call sites |
| ✅ | ✅ **IMP-C20** | no pre-flight for tracks mkvmerge cannot split (FLAC) → a whole class of sources burned a full prep before failing | low | done — `e2b799c` + `1b1a899` (gate moved ahead of prep in both auto-pilots); detect-and-stop only, never auto-converts |
| ✅ | ✅ **IMP-R6** | failed restore-merge leaves NO file at the path → title disappears from every media server | medium | done — merge-to-temp + os.replace on verify (option a) |
| ✅ | ✅ **IMP-R7** | re-running a command after a crash silently destroys the leftover recovery journal → orphaned artifacts | medium | done — auto-recover pre-PONR leftover on journal-open (option b) |
| ✅ | ✅ **IMP-D4 (partial)** | 107 legacy text-dummy entries hand-reconciled (all verified in Google Photos); `verify_library` status-to-disk invariant + pipeline post-conditions now guard against recurrence — see `docs/feature-legacy-reconcile/REPORT.md` | low | done |

## 🟠 BAND 1 — SUGGESTED NEXT after Band 0 (cheap foundations + immediate value)

| # | Task | Payoff | Risk |
|---|---|---|---|
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

## ✅ DONE (35)

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
`D22` (`prep_push_rep_enrich`/`prep_push_rep_season_enrich` — archive + TMDB enrichment folded into one command over the untouched `cmd_prep_push_rep`/`cmd_prep_push_rep_season` autopilots; `-tmdbid` skip-search, auto-resolve waterfall fallback, `-tvdbid` refused outright (TMDB-only), `{tmdb-…}` rename gated behind `--yes`/`--no-rename` confirmation, richer opt-in `.nfo` (`<imdbid>`/`<genre>`/`<runtime>`/`<premiered>`/`<studio>`/`<director>`/`<actor>`, never `<tvdbid>`); both autopilots provably zero-diff, no new PONR, 65 new tests, suite 768/smoke 76+ — `feature/imp_d22_prep_push_rep_enrich`).

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
