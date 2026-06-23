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
> **Last updated:** 2026-06-23 (IMP-E15 — mobile + Tailscale serve + shared-token auth + mvconfig.json minimal slice — on `feature/imp_e15_mobile_tailscale_auth`: mvconfig.json loader in mvcommon (web.host/port/token + tmdb.api_key); shared-token auth middleware on all /api/* (cookie/header/query, constant-time compare, 401); non-localhost startup guard; tailscale serve HTTPS setup (tools/tailscale_serve_setup.ps1 + REMOTE_ACCESS.md); auth.js token capture → cookie/header on every fetch → 401 prompt; IMP-A5 minimal slice. Earlier: IMP-E14 grouped folder view + space background + open-in-Explorer + iOS no-cache fix; hover motion + PWA; fetch-in-UI; media-type tabs; IMP-D4 integrity guard; IMP-A12 CI pipeline; IMP-E12 web ops console + IMP-D16).

---

## 👉 SUGGESTED NEXT TASK: **IMP-E3 / IMP-U3 / IMP-D17** — Phase 5: TMDB posters + real titles + rename_folder

**IMP-E15 is done** (Phase 4 of the web media-UI plan on `feature/imp_e15_mobile_tailscale_auth`:
mvconfig.json minimal config; shared-token auth on all `/api/*`; non-localhost startup guard; Tailscale
HTTPS serve setup script; auth.js client token UX; IMP-A5 minimal slice).
**Phase 5 is next**: TMDB metadata enrichment (real titles + posters + NFO for Jellyfin), rename_folder
command, and NFO/artwork pipeline. Tracked as **IMP-E3** (metadata enrichment), **IMP-U3** (NFO + artwork
pipeline), **IMP-D17** (rename_folder command). Full tracked plan at `docs/feature-web-media-ui/PLAN.md`.
A cheap zero-code parallel win is still available: **IMP-S1** — stand up Jellyfin (immediate couch value).

> 🚦 **IMP-R6** and **IMP-R7** still await user decisions before any code — see Band 0 table.

---

## 🔴 BAND 0 — CRITICAL: bugs that break / data-integrity decisions (do first)

| # | Task | Why it's critical | Risk to fix | Gate |
|---|---|---|---|---|
| 1 | 🚦 **IMP-R6** | failed restore-merge leaves NO file at the path → title disappears from every media server | medium | **decision** |
| 2 | 🚦 **IMP-R7** | re-running a command after a crash silently destroys the leftover recovery journal → orphaned artifacts | medium | **decision** |
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
`U2` (status home rows). Library beauty: `E3` (metadata) → `U3` (NFO/artwork), `U4` (DV-FEL paths).
Feels-instant spikes: `G2` (gphotosdl) → `S7` (fetch hardening); `S6` (watch-while-fetching); `S8`
(proxy-stream). Polish: `U5` (C# plugin), `E12` ✅ /`F10` (ops web UI — E12 shipped; its card-grid SPA is the substrate the future media UI grows on).

## ⚪ BAND 5 — QUALITY / PERF / UTILITY (opportunistic, any time)

Perf: `B1`–`B10`. Utility commands: `D2`,`D3`,`D6`–`D15`. Integration long-tail: `E1`,`E2`,`E6`,
`E8`,`E10`,`E11`. Rollback hardening: `R1`,`R3`,`R4`,`R5`,`R8`,`R9` (R4/R8/R9 are 🚦 change-gated).
Moonshots: `F1`–`F9`. Research-only: `G3`,`G5`,`H2`,`A6`.

## ✅ DONE (25)

`A1` (mvcommon) · `A7` (pytest harness) · `C2` (retry) · `C4` (device pinning) · `C8` (post-push verify) ·
`C9` (atomic replace) · `C11` (restore quarantine) · `C12` (alias crash: scan/local_status) ·
`C13` (single-id alias handling) · `C14` (CLI parser papercuts) · `C15` (micro-robustness) · `C16` (anime fetch profile) · `C17` (fetch-session keep-alive + logged-out detector) · `C18` (anime sSSEE episode-range filter: shared episode_num_from_id + 0-match guard) · `C6` (session-expiry detect — via C17) · `E13` (multi-episode) · `G1` (chunker patterns) ·
`H1` (Opus 4.8 effort tiers) · `H3` (smoke gate + consumer-impact guardrail + data-request protocol) ·
`R2` (recover CLI) · `E12` (web ops console — Disk Reclaim view + suggested next-commands + integrated sort/replace) ·
`A10` (requirements truth-up) · `D16` (scan_reclaimable — four-state reclaim scan behind `web`) · `A12` (CI pipeline — GitHub Actions full + smoke gate on every PR) ·
`D4` (partial: integrity guard — verify_library status-to-disk invariant + warn-only pipeline post-conditions; broader audits remain in_progress).

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
