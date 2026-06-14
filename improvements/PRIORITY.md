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
> **Last updated:** 2026-06-14 (IMP-C15 done — fix/micro_robustness_c15).

---

## 👉 SUGGESTED NEXT TASK: **IMP-C16** — anime fetch profile routing

Anime is now its own Google account, but `cmd_fetch_route` still routes `ani-*` to the *series*
Chrome profile — so the first real archived-anime restore silently fails (0 thumbnails, looks like
a session expiry). Add the 3rd Chrome profile + make the id-prefix→profile map data-driven. Small,
low-risk, and it lays the per-account fetch seam IMP-X1/X4 build on. IMP-C15 is now done — Band 0 is
clear of code-first items (only the two 🚦 R6/R7 decisions remain).

**Also awaiting your decision (do not need code first):** 🚦 **R6** and 🚦 **R7** — two change-gated
rollback findings with options written in `improvements_tierR.md`. R6 (a failed restore-merge deletes
the dummy → the title vanishes from Jellyfin/Plex) is the more urgent; pick an option when convenient.

---

## 🔴 BAND 0 — CRITICAL: bugs that break / data-integrity decisions (do first)

| # | Task | Why it's critical | Risk to fix | Gate |
|---|---|---|---|---|
| 1 | 🚦 **IMP-R6** | failed restore-merge leaves NO file at the path → title disappears from every media server | medium | **decision** |
| 2 | 🚦 **IMP-R7** | re-running a command after a crash silently destroys the leftover recovery journal → orphaned artifacts | medium | **decision** |

## 🟠 BAND 1 — SUGGESTED NEXT after Band 0 (cheap foundations + immediate value)

| # | Task | Payoff | Risk |
|---|---|---|---|
| 7 | 🟠 **IMP-C16** | anime is its own Google account now, but fetch routes `ani-*` to the *series* profile → first archived-anime restore silently fails. Add the 3rd Chrome profile + data-driven routing | low |
| 8 | 🟠 **IMP-A10** | truth-up `requirements.txt` (a clean install is half-broken today: missing `requests`/`webdriver-manager`) | low |
| 9 | 🟠 **IMP-S1** | stand up Jellyfin (the `JELLYFIN_SETUP_GUIDE.md` run) — **zero code, immediate couch value**, can run in parallel | low |
| 10 | ⚪ **IMP-A11** | repo hygiene (gitignore stale root `STATUS.md`, drop stray files, clean leftover worktrees) | low |
| 11 | 🟠 **IMP-A12** | CI pipeline — lock the 13-file suite so nothing merges red | low |

## 🟡 BAND 2 — FOUNDATIONS that unblock the daemon + new commands

`A2` (argparse) → `A4` (`--json`) → `A5` (config) → `A3` (logging) — this chain underpins the Tier S
daemon and every new command. Then `C3` (doctor), `D4` (verify_library) + `D5` (repair_library),
`C5`/`C6` (fetch fallback + session-expiry — also Band-3-critical for unattended fetch).

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
(proxy-stream). Polish: `U5` (C# plugin), `E12`/`F10` (ops web UI).

## ⚪ BAND 5 — QUALITY / PERF / UTILITY (opportunistic, any time)

Perf: `B1`–`B10`. Utility commands: `D2`,`D3`,`D6`–`D15`. Integration long-tail: `E1`,`E2`,`E6`,
`E8`,`E10`,`E11`. Rollback hardening: `R1`,`R3`,`R4`,`R5`,`R8`,`R9` (R4/R8/R9 are 🚦 change-gated).
Moonshots: `F1`–`F9`. Research-only: `G3`,`G5`,`H2`,`A6`.

## ✅ DONE (16)

`A1` (mvcommon) · `A7` (pytest harness) · `C2` (retry) · `C4` (device pinning) · `C8` (post-push verify) ·
`C9` (atomic replace) · `C11` (restore quarantine) · `C12` (alias crash: scan/local_status) ·
`C13` (single-id alias handling) · `C14` (CLI parser papercuts) · `C15` (micro-robustness) · `E13` (multi-episode) · `G1` (chunker patterns) ·
`H1` (Opus 4.8 effort tiers) · `H3` (smoke gate + consumer-impact guardrail + data-request protocol) ·
`R2` (recover CLI).

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
