# MediaVault Improvements — Reading Guide

> This file is the index and operating manual for the `improvements_tier*.md` set. Start here.

---

## 1. What these files are

On 2026-05-25, a deep audit of `main.py`, `mainfetch.py`, `ARCHITECTURE.md`, the live `C:\Media\library_*.json` files, and the historical `usage_commands.txt` produced **70 improvement tasks**, partitioned across **seven tier files** at the project root:

| File | Items | Theme | Avg Effort |
|---|---:|---|---|
| `improvements_tierA.md` | A1–A9 (9) | Code architecture & refactoring | small–medium |
| `improvements_tierB.md` | B1–B8 (8) | Performance optimizations | mostly small |
| `improvements_tierC.md` | C1–C11 (11) | Robustness & reliability | small–medium |
| `improvements_tierD.md` | D1–D15 (15) | New CLI commands | mostly small |
| `improvements_tierE.md` | E1–E12 (12) | Integration & workflow features | medium–large |
| `improvements_tierF.md` | F1–F10 (10) | Creative / moonshot features | mostly large |
| `improvements_tierG.md` | G1–G5 (5) | Lessons from similar projects | research-heavy |

> **Added 2026-05-30 — Tier H (outside the original audit):**
>
> | File | Items | Theme | Avg Effort |
> |---|---:|---|---|
> | `improvements_tierH.md` | H1–H2 (2) | Agentic workflow & tooling | small–large |
>
> Tier H tracks changes to the multi-agent Claude Code pipeline that *builds* MediaVault (`.claude/agents/`), not the product runtime. **IMP-H1** (migrate the agent pipeline to Opus 4.8 effort tiers) is `done`; see `ARCHITECTURE.md` §19 and `.claude/AGENT_WORKFLOW_NOTES.md`. **IMP-H2** (evaluate Opus 4.8 "dynamic workflows") is `pending`.

> **Added 2026-06-01 — Tier R (auto-rollback follow-ups & storage efficiency):**
>
> | File | Items | Theme | Avg Effort |
> |---|---:|---|---|
> | `improvements_tierR.md` | R1–R9 (9) | Auto-rollback hardening & storage efficiency | small–large |
>
> Tier R holds forward-looking work that builds on the merged auto-rollback feature (see `docs/feature-auto-rollback/ROLLBACK_MECHANISM.md`) — e.g. **IMP-R1** (cut the ~40 GB split/upload disk peak via streaming split-upload-delete). **IMP-R2** (the `recover` CLI) is `done` (PR #18). R6–R9 were added by the 2026-06-12 review as **gate-flagged decision requests** (merge-failure dummy loss, journal clobber on re-run, unjournalled eager temp, alias block outside journal). **Any change that alters rollback behavior is change-gated — see `CLAUDE.md`.**

> **Added 2026-06-12 — Tiers S & U (the couch-vault end goal), by the fable-review session:**
>
> | File | Items | Theme | Avg Effort |
> |---|---:|---|---|
> | `improvements_tierS.md` | S1–S8 (8) | Streaming & media-server integration (Jellyfin daemon, in-client fetch/notify/archive flows, fetch hardening, watch-while-fetching) | medium–large |
> | `improvements_tierU.md` | U1–U5 (5) | Couch UX & clients (enrichment-before-archive, status rows, NFO/artwork, DV-FEL paths, eventual C# plugin) | small–large |
>
> These two tiers implement the end goal: browse the vault from Apple TV / the Ugoos projector via **Jellyfin**, select → background fetch → in-client "ready" notify → watch → in-client-governed auto-archive. The **master phasing lives in [`ROADMAP_END_GOAL.md`](ROADMAP_END_GOAL.md)**; research grounding in `../docs/feature-fable-review/RESEARCH_*.md`. Locked session decisions: Jellyfin-first (Emby lifetime = fallback, Plex = do-not-buy at $749), in-client-only interaction, 4× Pixel unlimited-upload path untouchable.

> **Added 2026-06-12 — Tier X (cloud resilience & privacy), fable-review follow-up:**
>
> | File | Items | Theme | Avg Effort |
> |---|---:|---|---|
> | `improvements_tierX.md` | X1–X5 (5) | Multi-account redundancy + encrypted/anti-scanning upload | medium–large |
>
> Tier X answers the user's "make the storage super robust + encrypt against content scanning" follow-up. Key research finding it acts on: a **Feb-2026 wave of instant, unrecoverable Google account bans from CSAM-AI false positives** makes the 3-account (movies/series/anime) layout a real single point of failure. **X1** = direct multi-account chunk replication (the only backup that keeps the free-unlimited Pixel path AND survives an account loss — Google Photos *sharing* does NOT, see the tier's §0 decision table). **X3** = encrypt + wrap-in-valid-MKV upload to defeat copyright hash-matching and the CSAM-AI classifier (gated on an upload-acceptance spike + the change-gate). The §0 table also definitively answers "does deleting the main account remove the backup": view-only shares vanish for everyone; only an independent replica survives.

## 1a. The always-current priority list (READ THIS to know what to do next)

[`/PRIORITY.md`](PRIORITY.md) is the **single source of truth for task ordering** — critical bugs
first, a always-updated **👉 SUGGESTED NEXT TASK** pointer, and five priority bands. Its visual twin is
[`../docs/priority-graph/priority-graph.html`](../docs/priority-graph/priority-graph.html) — an interactive
concentric task graph (critical in the center), click any node for details + a jump to its tier file.
**Both must be updated whenever a task is added, completed, or re-prioritized** — protocol in
`PRIORITY.md` and `CLAUDE.md`.

> **2026-06-12 fable-review changes to the EXISTING tiers:**
> - Every pending task gained two attributes: **`Risk`** and **`If skipped`** (see §2).
> - New tasks from the full code read: **A10–A12** (requirements truth-up, repo hygiene, CI), **B9–B10** (hash-print throttle, harvester re-hash), **C12–C15** (multi_ep_alias crashers in scan_unprepped/local_status, single-id alias handling, parser papercuts, micro-robustness).
> - Status corrections: **IMP-A7** → done (the pytest harness shipped organically with PRs #14–#21; CI remainder → A12), **IMP-C4** → done (device pinning shipped in PR #2).
> - Reorientations: E4 (Jellyfin-webhook-primary watch state), E9 (Jellyfin-first + split vs daemon), E10 (demoted to optional fallback — in-client-only decision), E12 (= the daemon's web UI layer), F4/F6/F10 (re-homed against Tiers S), G2 (raised: gphotosdl spike), G4 (graduated: Jellyfin direction confirmed).
> - Tier F header documents the **container constraint** (raw encrypted/CDC/parity blobs aren't valid videos → Pixel Photos app likely won't upload them; blocks F1/F2/F3 raw forms).

Plus two companion files at the root:

- **`apple_tv_ui_roadmap.md`** — the long-form Apple TV UI plan and Jellyfin integration design (downstream of the tier work).
- **`improvement_details.md`** (this file) — how to use everything above.

---

## 2. The IMP-XN task format

Every task in the tier files follows an identical shape:

```
## IMP-A1: <short title>

- Category: <performance | code quality | refactor | bug | security | integration | experiment | other>
- Priority: <high | medium | low>
- Files: <affected files with line numbers where useful>
- Current behavior: <what exists today, with code references>
- Proposed change: <detailed what to do, sometimes with sub-bullets>
- Rationale: <why this matters>
- Goal: <observable end state>
- Effort estimate: <small | medium | large>
- Risk: <low | medium | high> — <blast radius: which working commands/behaviors the CHANGE touches; "(change-gated)" where the rollback gate applies>
- If skipped: <the failure/limitation that persists if never done, with a concrete scenario>
- Status: pending
```

**Field semantics:**

- **Category** — what TYPE of change it is. Drives reviewer focus.
- **Risk** *(added 2026-06-12)* — how big the impact of MAKING the change is: which working commands it modifies, whether it brushes the auto-rollback change-gate, what a regression would break. `high` ≈ rewrites a daily-driver path; `low` ≈ additive/read-only.
- **If skipped** *(added 2026-06-12)* — the failure or issue that persists if the task is never done, ideally with a concrete scenario. The "cost of inaction" counterweight to Risk.
- **Priority** — `high` = pain felt today or unblocks downstream work; `medium` = clear win, not urgent; `low` = nice-to-have or speculative.
- **Files** — concrete paths plus line numbers from the active `main.py` (1621 lines) and `mainfetch.py` (507 lines).
- **Current behavior** — written so an agent who hasn't read the code can still understand the problem. References specific function names and line ranges from the architecture.
- **Proposed change** — the WHAT, not the HOW. Concrete enough that the planner agent can decompose it into steps. Does not prescribe implementation details (regex form, exact function signatures, etc.).
- **Rationale** — the WHY. Often references concrete production data (e.g., "the Mr Robot S02 `episodes 11-13` re-run in usage_commands.txt").
- **Goal** — the observable success criterion. Aligns with the karpathy "Goal-Driven Execution" principle from `~/.claude/CLAUDE.md`.
- **Effort estimate** — `small` = ~half-day; `medium` = 1-3 days; `large` = a week or more.
- **Status** — `pending` initially. Update to `in_progress` when started, `done` when merged, `blocked` if stuck, `wontdo` if abandoned (with a note added).

---

## 3. Priority distribution

> ⚠️ This table is the **2026-05-25 snapshot** of the original A–G audit and has drifted
> (new tasks A10–A12/B9–B10/C12–C15/R6–R9/S1–S8/U1–U5; reprioritizations; 11 items now done:
> A1, A7, C2, C4, C8, C9, C11, E13, G1, H1, R2 — ~104 tasks total as of 2026-06-12).
> **The tier files are authoritative**; treat this as historical context only.

| Tier | High | Medium | Low |
|---|---:|---:|---:|
| A | 3 | 3 | 3 |
| B | 1 | 3 | 4 |
| C | 4 | 4 | 3 |
| D | 4 | 4 | 7 |
| E | 3 | 4 | 5 |
| F | 0 | 2 | 8 |
| G | 1 | 2 | 2 |
| **Total** | **16** | **22** | **32** |

The **16 high-priority items** (~23% of the total) are the ones that directly address pain visible in your `usage_commands.txt` history or block downstream work:

| Tier | High-priority IMPs |
|---|---|
| A | A1, A2, A4 |
| B | B1 |
| C | C1, C2, C3, C5, C6 |
| D | D1, D4, D10, D11 (wait, D11 is `medium` — actual: D1, D4, D10) → **Verify in file** |
| E | E3, E5, E12 |
| G | G2 |

> **Note on D11:** `push_auto_batch` was listed as `medium` in the file. The "high" set above is the canonical short list; if a discrepancy arises, the tier file is authoritative.

---

## 4. Dependency map

Many tasks build on each other. Doing them out of order forces rework. The key dependency chains:

### Foundation chain (do first)
```
A1 (mvcommon.py)
  └─→ A6 (type hints on mvcommon)
  └─→ A7 (pytest harness uses mvcommon)
  └─→ B1 (cache library — needs shared load/save)
  └─→ B2 (dirty save — needs shared save_library)

A2 (argparse)
  └─→ A4 (--json mode — needs argparse for flag)
  └─→ A5 (config file — argparse loads it)
  └─→ Every Tier D command (they all need argparse)
```

### Robustness chain
```
A3 (logging) ──→ D14 (tail_progress)
C4 (device pinning) ──→ E7 (multi-device push)
C2 (retries) ──→ C3 (doctor) and all push/fetch ops
C5 (real fallback search) ──→ C6 (session expiry detection)
```

### UI chain (the long road)
```
A4 (--json) + E3 (TMDB) ──→ D1 (library_stats with rich data)
       └─→ D3 (where_is with rich data)
       └─→ E12 (web UI)
              └─→ Apple TV UI roadmap (see apple_tv_ui_roadmap.md)
                     └─→ G4 (Jellyfin plugin path)
```

### Fetch chain
```
G2 (gphotosdl evaluation)
  ├─→ if YES: replace mainfetch.py internals
  └─→ if NO: cherry-pick C5, C6, retry from gphotosdl patterns
```

### Storage chain (speculative)
```
F1 (encryption) ──→ F3 (erasure coding) — encrypted chunks distributed across accounts
F2 (CDC dedup) ──→ G5 (restic / borg reference) — research before implementing
F9 (multi-cloud) ──→ unlocks all the above against Google policy risk
```

---

## 5. Recommended phased rollout

If you tackle these in any reasonable order, the **eight-phase plan** below minimizes rework. Each phase is roughly 1-3 weeks of part-time work.

### Phase 1 — Foundations (no behaviour change, big leverage)
1. **A1** — `mvcommon.py`
2. **A2** — argparse migration
3. **A4** — `--json` output mode on read-only commands
4. **A8** — delete dead code in mainfetch
5. **A9** — fix `[eE|xX]` regex typo
6. **A5** — `mvconfig.json` (lighter than A1/A2 but slot it in here)

After Phase 1: `main.py` is half its current size; everything is testable.

### Phase 2 — Safety net (catches the most common failures)
7. **C3** — `doctor` command
8. **C2** — retry logic with backoff
9. **C1** — auto-resume in `prep_push_rep_season`
10. **A7** — pytest harness (regex + library round-trip tests)
11. **A3** — logging module

After Phase 2: most failures self-heal. Manual re-runs with narrower ranges become rare.

### Phase 3 — Fetch hardening
12. **C5** — real fallback search query
13. **C6** — Google Photos session-expiry detection
14. **G2** — gphotosdl evaluation spike (decide replace-vs-cherry-pick)
15. **C11** — quarantine on hash mismatch

After Phase 3: the most fragile piece (Selenium fetch) is the most reliable.

### Phase 4 — Operational visibility
16. **D4** — `verify_library`
17. **D5** — `repair_library`
18. **D1** — `library_stats`
19. **D2** — `find`
20. **D3** — `where_is`
21. **E6** — quota tracker
22. **D14** — `tail_progress`

After Phase 4: you have a full operational dashboard from the CLI.

### Phase 5 — Workflow ergonomics
23. **D10** — `prep_auto` wizard
24. **D11** — `push_auto_batch`
25. **D7** — `unarchive` alias
26. **D8** — `relocate`
27. **C7, C8, C9** — set_uploaded verify, post-push verify, atomic replace
28. **B1, B2, B3, B4** — performance batch

After Phase 5: daily MediaVault work is dramatically less manual.

### Phase 6 — External integrations
29. **E5** — phone auto-cleanup
30. **E3** — TMDB / TheTVDB / AniDB enrichment
31. **E1** — subtitle pre-extraction
32. **E9** — Plex/Jellyfin library-refresh
33. **E11** — sidecar-aware ID lookup
34. **E10** — Telegram bot (optional but huge QoL)

After Phase 6: MediaVault is connected to the ecosystem.

### Phase 7 — Web UI (the bridge to the couch UI)
35. **E12** — `web` command (local web UI — now the Tier S daemon's UI layer)
36. **F10** — websocket status broadcaster (now the daemon's event bus)
37. ~~Apple TV UI roadmap Phase 0-1~~ → superseded by `ROADMAP_END_GOAL.md` Phase 0-1 (Jellyfin stand-up + daemon)

### Phase 8 — Couch UI proper
- **SUPERSEDED 2026-06-12**: the end-goal phasing now lives in
  [`ROADMAP_END_GOAL.md`](ROADMAP_END_GOAL.md)
  (Tiers S/U), which absorbs `apple_tv_ui_roadmap.md` Phases 0-4 with corrections
  (video-dummy detection, daemon-first instead of plugin-first, in-client-only interaction).
- The plugin polish phase is **IMP-U5** (ex-G4 direction).

> **Note (2026-06-12):** Phases 1–6 above remain the valid ordering for the core-CLI work and
> interleave with the end-goal phases — `ROADMAP_END_GOAL.md` §"Dependency weave" maps which
> core-phase items each S/U phase needs.

### Speculative / opportunistic (any time)
- **B5–B8** — micro-perf
- **D6, D9, D12, D13, D15** — utility commands
- **C10** — sidecar reconciliation
- **E2, E4, E7, E8** — workflow extensions
- **F1–F9** — moonshots, only when their gating conditions arise

---

## 6. How to use these files with the planner agent

The intended hand-off flow:

1. **Read this file first**, then the architecture (`ARCHITECTURE.md`), then any memory entries the relevant tasks reference (`[[memory-name]]` style).
2. **Pick a task** (or short batch of dependent tasks) by IMP-XN.
3. **Invoke the planner agent** with:
   - The full text of the task entry (from the tier file).
   - The "Cross-cutting context" preamble of that tier file.
   - Any IMPs the task depends on whose status is not yet `done`.
4. Planner produces a `PLAN.md` with concrete numbered steps and model assignment (haiku/sonnet/opus per step).
5. **Invoke the orchestrator agent** to execute `PLAN.md`.
6. On success, **mark the task `done`** in its tier file. On partial completion, mark `in_progress` and note what's left.

**One task at a time** is the rule unless tasks are explicitly co-designed (e.g., A1+A2+A4 form a natural batch because they share argparse refactoring touch points).

---

## 7. Updating task status

Status transitions are manual edits to the tier files. Use sed-friendly forms:

```
- Status: pending          →   - Status: in_progress (2026-05-26)
- Status: in_progress      →   - Status: done (2026-06-02, commit abc1234)
- Status: pending          →   - Status: wontdo (reason: ...)
- Status: in_progress      →   - Status: blocked (blocker: needs IMP-X first)
```

When marking `done`, add a one-line note about the commit hash or PR.

When adding a NEW task discovered during work:
- Choose the appropriate tier file based on category.
- Pick the next available IMP-XN number (e.g., if A9 is the last, add A10).
- Use the same format. Add to the END of the file.
- Cross-reference it from this `improvement_details.md` if it's high-priority.

---

## 8. What's deliberately NOT in the tier files

To keep them focused, several things were excluded:

- **The merge-hash divergence concern** — covered in `[[feedback-mkvmerge-hash-divergence]]` memory. Do not propose "fix" tasks against it.
- **Architecture-level facts** — covered in `ARCHITECTURE.md`. Tier files reference but don't repeat.
- **The Aindham Vedham orphan** — already manually fixed on 2026-05-25. The class of problem is covered by IMP-D4 / IMP-D5.
- **The Apple TV UI long-form design** — covered in `apple_tv_ui_roadmap.md`. The tier files only contain its prerequisites (A4, E3, E12, F10) and the integration approach (G4).

---

## 9. File ownership

| File | Edit frequency | Owner |
|---|---|---|
| `improvements_tier*.md` | Mark status as work progresses; add tasks rarely | Human + planner agent |
| `improvement_details.md` (this file) | Update after major restructures | Human |
| `apple_tv_ui_roadmap.md` | Update as Apple TV plan firms up | Human |
| `ARCHITECTURE.md` | Update after each `done` IMP that changes behaviour | Human + architect agent |
| Memory files under `~/.claude/projects/...../memory/` | Update as facts about the user/project shift | Claude (automatic) |

---

## 10. Quick-start cheat sheet

> If you just want to start NOW, do this:

1. Read `improvements_tierA.md` top-to-bottom (~10 min).
2. Pick **IMP-A1** (mvcommon.py extraction). It's small, blocks nothing, unblocks much.
3. Spawn the planner agent against IMP-A1's task entry.
4. Approve the resulting `PLAN.md`.
5. Spawn the orchestrator agent.
6. Mark IMP-A1 `done`. Move to **IMP-A2**.

By the end of one focused week you can plausibly have **all of Phase 1** done. The compounding leverage starts there.
