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
> **Last updated:** 2026-08-24 (**IMP-C19 + IMP-C20 done** on `fix/imp_c19_c20_split_diagnostics` — mkvmerge's real error is no longer discarded, and `cmd_push` now refuses a file carrying a track mkvmerge cannot split (FLAC) *before* prep spends a deep scan + whole-file hash. **IMP-R10 opened and left pending by user decision** — a transient lock on the journal during `cmd_replace`'s PONR write is misread as a locked media file and reports a spurious IRREVERSIBLE; change-gated, so it needs a decision before any code moves. Both incidents are written up under `docs/edge-case-*/`. Earlier: IMP-D19 extras option — **done** on `feature/imp_d19_extras`: `--extras`/`--extras-size` on prep/prep_season/push/push_group/prep_push_rep/prep_push_rep_season, new `add_extras`, flag-only `--fetchExtras` on fetch/fetch_restore, grouped `extras` block on title entries (no new entry type), full push→dummy→fetch→restore lifecycle reusing rollback primitives additively (E1), 37 unit tests + 4 smoke cases (suite 648/smoke 76); PR to `main` pending. Earlier: IMP-D18 Others/sports content category — **done** on `feature/imp_d18_others_category`: 4th `oth-` category — `library_others.json`, season_map/leaf reuse, list-capable disk roots via `CATEGORY_ROOTS`, enrichment-skip, Others Chrome profile + Pixel. Earlier: IMP-R6 restore-merge temp-stage + IMP-R7 crash-re-run auto-recover — both done on `feature/imp_r6_r7_restore_journal_crashsafe`. IMP-E16 — UI dossier + /api/detail + EXA auto-resolve + grid + palette + parallax hero + lazy-load perf — on `feature/imp_e16_ui_wow`. Phase 5 IMP-E3/U3/D17 — TMDB enrich + rename_folder + /api/media-image + real posters/titles in SPA. IMP-E14 fully done. IMP-D17 done. IMP-E15 done.)

---

## 👉 SUGGESTED NEXT TASK: **IMP-S1 (Jellyfin stand-up)** + **IMP-S2 (mvdaemon)** — the daemon path + couch-vault plumbing

> **IMP-D18 is done** (4th "Others"/sports content category) on `feature/imp_d18_others_category` — PR to `main` pending. It creates two follow-ons: **IMP-X1** (replicate the Others account's chunks to a 2nd Google account — the same CSAM-ban single-point-of-failure applies to the 4th account) and the open **OD-2** question (whether to ever add a sports metadata scraper, e.g. TheSportsDB; today it's filename-as-title, no scraper). IMP-S1/S2 remain the headline next.

**IMP-R6 + IMP-R7 done** (crash-safety fixes — restore merge-to-temp + journal auto-recovery on re-run
— on `feature/imp_r6_r7_restore_journal_crashsafe`). **Band 0 is NOT clear as of 2026-08-24** — **IMP-R10** is open and change-gated
(see below); the user has deferred the fix but the decision is still outstanding. **Phase 5 done** (IMP-E3 partial / IMP-U3 partial / IMP-D17 done). **IMP-E14 fully done**.
**IMP-E15 done**.

**Recommended next starts (pick one or run in parallel):**
1. **IMP-S1** — stand up Jellyfin using `improvements/JELLYFIN_SETUP_GUIDE.md`: zero code, immediate
   couch value; the NFOs + posters from Phase 5 will populate the Jellyfin library on first scan.
2. **IMP-S2** — mvdaemon service: the one new component the end goal needs (the web worker is its seed).
3. **IMP-E3 / IMP-U3 breadth** — AniDB/AniList/TheTVDB + per-episode NFOs + anime ids in NFOs +
   backfill review-diff mode (the partial slices still have pending scope).
4. **IMP-A2 → A5 config chain** — argparse CLI + `--json` + full mvconfig migration (unblocks daemon).

**Headline NEXT: IMP-S1** (zero code, do anytime — Jellyfin is the couch surface the vault is being built for).

> ⚠️ **Band 0 has one open item again (2026-08-24): IMP-R10.** It is blocked on a *decision*, not on work — the fix touches `mark_point_of_no_return()` placement and is change-gated. The user deferred it ("can fix later"), so IMP-S1 remains the actionable next start, but R10 outranks everything here the moment the gate is answered. Its sibling bugs from the same incident, IMP-C19 and IMP-C20, are done.

---

## 🔴 BAND 0 — CRITICAL: bugs that break / data-integrity decisions (do first)

**One open item: IMP-R10** (change-gated). The user deferred the fix on 2026-08-24 ("document more - can fix later"), so it is documented in full and waiting on a change-gate decision — not on implementation capacity.

| # | Task | Why it's critical | Risk to fix | Gate |
|---|---|---|---|---|
| 1 | 🚦 **IMP-R10** | a transient lock on `.mediavault_txn.json` during `cmd_replace`'s PONR write is caught by the retry loop meant for a locked *media* file → spurious `IRREVERSIBLE`, and the handler advises `fetch_restore` (a 62 GB re-download) while the master sits on local disk as `.tobedeleted` | **gated** — touches `mark_point_of_no_return()` placement | 🚦 **needs a user decision** (`CLAUDE.md` change-gate); documented in `docs/edge-case-replace-ponr-journal-lock/` |
| ✅ | ✅ **IMP-C19** | mkvmerge's `Error:` lines go to stdout, which `split_video_file` sent to DEVNULL → every split failure was an uninterpretable `exit status 2`, after a 62 GB prep | low | done — `1af16a3`, both split + merge call sites |
| ✅ | ✅ **IMP-C20** | no pre-flight for tracks mkvmerge cannot split (FLAC) → a whole class of sources burned a full prep before failing | low | done — `e2b799c`, detect-and-stop only; never auto-converts |
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

## ✅ DONE (32)

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
`C19` (surface mkvmerge's real error — both split + merge call sites capture stdout+stderr and echo the reason; mkvmerge reports on STDOUT, which was being DEVNULL'd) · `C20` (pre-flight for tracks mkvmerge cannot split — `mkvmerge -J` probe refuses a FLAC-bearing file before prep spends a deep scan + whole-file hash; detect-and-stop only, never auto-converts — `fix/imp_c19_c20_split_diagnostics`).

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
