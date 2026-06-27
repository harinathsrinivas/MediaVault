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
> **Last updated:** 2026-06-27 (IMP-R6 restore-merge temp-stage + IMP-R7 crash-re-run auto-recover — both done on `feature/imp_r6_r7_restore_journal_crashsafe`. Earlier: IMP-E16 — UI dossier (hover/long-press cinematic glass panel) + /api/detail (TMDB full detail + merged OMDb ratings + trivia) + `refresh_online`/`fetch_trivia` commands (mvonline.json/mvextra.json caches; OMDb/EXA/GROQ config keys) + EXA web-search auto-resolve waterfall in `enrich_metadata` + grouped grid/drill-down view + poster-driven ambient glow (`swatch.js`) + ⌘K command palette (`palette.js`) + View-Transitions morphs + cinematic parallax hero (`hero.js`) + lazy-load perf — on `feature/imp_e16_ui_wow`. Phase 5 IMP-E3/U3/D17 — TMDB enrich + rename_folder + /api/media-image + real posters/titles in SPA — on `feature/imp_e3_u3_d17_tmdb_posters_rename`. IMP-E14 fully done. IMP-D17 done. IMP-E15 done.)

---

## 👉 SUGGESTED NEXT TASK: **IMP-S1 (Jellyfin stand-up)** + **IMP-S2 (mvdaemon)** — the daemon path + couch-vault plumbing

**IMP-R6 + IMP-R7 done** (crash-safety fixes — restore merge-to-temp + journal auto-recovery on re-run
— on `feature/imp_r6_r7_restore_journal_crashsafe`). **Band 0 is now clear** (no remaining decision-gated
critical items). **Phase 5 done** (IMP-E3 partial / IMP-U3 partial / IMP-D17 done). **IMP-E14 fully done**.
**IMP-E15 done**.

**Recommended next starts (pick one or run in parallel):**
1. **IMP-S1** — stand up Jellyfin using `improvements/JELLYFIN_SETUP_GUIDE.md`: zero code, immediate
   couch value; the NFOs + posters from Phase 5 will populate the Jellyfin library on first scan.
2. **IMP-S2** — mvdaemon service: the one new component the end goal needs (the web worker is its seed).
3. **IMP-E3 / IMP-U3 breadth** — AniDB/AniList/TheTVDB + per-episode NFOs + anime ids in NFOs +
   backfill review-diff mode (the partial slices still have pending scope).
4. **IMP-A2 → A5 config chain** — argparse CLI + `--json` + full mvconfig migration (unblocks daemon).

**Headline NEXT: IMP-S1** (zero code, do anytime — Jellyfin is the couch surface the vault is being built for).

---

## 🔴 BAND 0 — CRITICAL: bugs that break / data-integrity decisions (do first)

**Band 0 is clear** — all decision-gated critical items are done.

| # | Task | Why it's critical | Risk to fix | Gate |
|---|---|---|---|---|
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

## ✅ DONE (30)

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
`D17` (rename_folder — crash-safe cascading folder rename + `{tmdb-…}` token stamp).

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
