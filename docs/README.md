# MediaVault Documentation — Master Index

> **The map of every document in this repository**: what each file means, what it contains, and when
> to read it. Created 2026-06-12 by the fable-review session; update it whenever a doc is added,
> superseded, or moved. (Requested as: *"a proper master .md file to indicate what each of these
> files mean and contain... documented in such a way that this session is never needed again."*)

## 0. Start here (new session / new agent orientation order)

1. [`/CLAUDE.md`](../CLAUDE.md) — project rules: PR conventions pointer, **human merge gates**, the
   **auto-rollback change-gate**, the **keep-PRIORITY.md-current rule**, agent-pipeline execution model.
2. [`improvements/PRIORITY.md`](../improvements/PRIORITY.md) — **what to do next** (critical bugs first, suggested-next pointer).
   Visual twin: [`docs/priority-graph/priority-graph.html`](priority-graph/priority-graph.html).
3. [`/ARCHITECTURE.md`](../ARCHITECTURE.md) — THE engineering reference (data model, every command,
   rollback mechanism §12a, testing §13, config §14, known issues §16, agent workflow §19).
4. [`improvements/README.md`](../improvements/README.md) — the backlog + direction "brain"; its
   `improvement_details.md` explains how the IMP-task system works + the tier map.
5. [`improvements/ROADMAP_END_GOAL.md`](../improvements/ROADMAP_END_GOAL.md) — where
   the project is GOING (the couch-vault phases).
6. The relevant tier file / feature folder for whatever you're touching.

## 1. Top-level documents (repo root)

| File | What it is |
|---|---|
| [`README.md`](../README.md) | User-facing overview: what MediaVault does, install, full CLI reference table, ID conventions, layout |
| [`ARCHITECTURE.md`](../ARCHITECTURE.md) | Definitive engineering reference (~2000 lines, 19 sections). Read before changing `main.py`/`mainfetch.py` |
| [`ARCHITECTURE_GRAPH.md`](../ARCHITECTURE_GRAPH.md) | The architecture as 7 Mermaid graph views (system, lifecycle, state machine, ER model, rollback flow, fetch sequence, seam map) |
| [`BEST_PRACTICES.md`](../BEST_PRACTICES.md) | Compounding decisions: choices to lock now (chunk size, DV/HDR verification, multi-account replication, enrich-before-archive, automation gates) that are cheap today and expensive to fix once the library scales / the daemon automates |
| [`CLAUDE.md`](../CLAUDE.md) | Session rules for AI-assisted work: gates, change-gate, keep-PRIORITY-current rule, pipeline notes |
| [`apple_tv_ui_roadmap.md`](../apple_tv_ui_roadmap.md) | 2026-05 Jellyfin-plugin UI design. **Partially superseded** — carries a correction banner; current plan is `improvements/ROADMAP_END_GOAL.md` |
| `PLAN.md`, `STATUS.md` (root) | **Gitignored live working copies** used by the agent pipeline during runs — both now gitignored and untracked (the root-`STATUS.md` cleanup of IMP-A11). They are session scratch, not durable records; the durable record per feature is its `DECISIONS.md` + the `improvements/` task entry. |

## 1b. The `improvements/` folder — backlog + direction ("the brain")

> Everything about *what we're building and why*. Start at
> [`improvements/README.md`](../improvements/README.md). The keep-current rule (CLAUDE.md) applies to
> `PRIORITY.md` + the priority graph on every task change.

| File | What it is |
|---|---|
| [`improvements/README.md`](../improvements/README.md) | Index of this folder — read first |
| [`improvements/PRIORITY.md`](../improvements/PRIORITY.md) | **The always-current "what to do next"** — critical bugs first, a `👉 SUGGESTED NEXT TASK` pointer, five priority bands, maintenance protocol. Visual twin: `docs/priority-graph/priority-graph.html` |
| [`improvements/improvement_details.md`](../improvements/improvement_details.md) | Operating manual for the IMP-XN task system (format spec incl. the `Risk`/`If skipped` attributes, dependency chains, phased rollout) |
| `improvements/improvements_tierA..H,R,S,U,X.md` | The ~110 tracked improvement tasks — see §2 |
| [`improvements/ROADMAP_END_GOAL.md`](../improvements/ROADMAP_END_GOAL.md) | **The master phased plan** to the couch-vault experience (supersedes apple_tv_ui_roadmap phasing) |
| [`improvements/RESEARCH_MEDIA_SERVERS.md`](../improvements/RESEARCH_MEDIA_SERVERS.md) | Jellyfin integration surfaces + in-client interaction design; Emby/Plex deltas; client matrix incl. Ugoos DV-FEL path |
| [`improvements/RESEARCH_STORAGE_STREAMING.md`](../improvements/RESEARCH_STORAGE_STREAMING.md) | Google Photos 2026 constraints; the tiered T0-T4 streaming-on-the-fly verdict; OSS steal table; Netflix feature mapping |
| [`improvements/JELLYFIN_SETUP_GUIDE.md`](../improvements/JELLYFIN_SETUP_GUIDE.md) | Scratch-install → fully-configured Jellyfin on the Alienware + Phase-0 validation checklist |
| [`improvements/BLOCKERS_AND_MOONSHOTS.md`](../improvements/BLOCKERS_AND_MOONSHOTS.md) | Hard blockers (5), soft blockers (5), tracked moonshots (10) + yearly re-check ritual |

## 2. Improvement tiers (the work backlog)

| Tier | Theme | Notable |
|---|---|---|
| [A](../improvements/improvements_tierA.md) | Code architecture & refactoring | A2 argparse, A4 --json, A5 config; A10-A12 added 2026-06-12 |
| [B](../improvements/improvements_tierB.md) | Performance | B1 library-handle cache (gate-adjacent), B9/B10 added 2026-06-12 |
| [C](../improvements/improvements_tierC.md) | Robustness & reliability | C12 **alias crashers (broken today)**, C3 doctor, C5/C6 fetch fixes |
| [D](../improvements/improvements_tierD.md) | New CLI commands | D4 verify_library, D10 prep_auto wizard |
| [E](../improvements/improvements_tierE.md) | Ecosystem integration | E3 metadata enrichment, E5 phone cleanup, E12 web UI |
| [F](../improvements/improvements_tierF.md) | Moonshots | Header documents the **container constraint**; F9 multi-cloud hedge |
| [G](../improvements/improvements_tierG.md) | Lessons from similar projects | G2 gphotosdl spike (raised), G4 Jellyfin direction (graduated) |
| [H](../improvements/improvements_tierH.md) | Agentic dev workflow | H1 done (effort tiers), H2 dynamic-workflows spike |
| [R](../improvements/improvements_tierR.md) | Auto-rollback hardening | ⚠️ change-gated tier; R6-R9 = 2026-06-12 gate-flagged findings |
| [S](../improvements/improvements_tierS.md) | **Streaming & media-server integration** | The end-goal backbone: daemon, in-client flows, fetch hardening |
| [U](../improvements/improvements_tierU.md) | **Couch UX & clients** | Enrichment-before-archive, home rows, NFO pipeline, DV-FEL paths |
| [X](../improvements/improvements_tierX.md) | **Cloud resilience & privacy** | X1 multi-account replication (the real backup), X2 topology/runbook + sharing decision, X3 encrypted/anti-scanning upload, X4/X5 self-heal + ban canary |

## 3. The fable-review dossier (`docs/feature-fable-review/`, 2026-06-12)

The **session record** of the full-repo review + end-goal research session (provenance — how the
backlog was produced). The durable outputs it created (roadmap, research, Jellyfin guide, blockers)
were promoted into [`improvements/`](../improvements/) — see §1b. **Resume protocol lives in STATUS.md.**

| File | Contents |
|---|---|
| [`SESSION_BRIEF.md`](feature-fable-review/SESSION_BRIEF.md) | The verbatim originating prompt(s) + the locked user decisions (Jellyfin-first, in-client-only, 3-account topology, the follow-up round) |
| [`STATUS.md`](feature-fable-review/STATUS.md) | Phase checklist P0-P10, deliverable map, decisions log, resume protocol |
| [`REVIEW_NOTES.md`](feature-fable-review/REVIEW_NOTES.md) | P1 code-read findings: 11 bugs/smells (incl. the alias crashers), 10 stale-doc items, verified-true cross-checks, the (now-resolved) topology question |
| [`PR_REVIEW.md`](feature-fable-review/PR_REVIEW.md) | All 21 merged PRs reviewed; deep dives on #14 (auto-rollback) and #20 (deterministic split-hash) |

## 4. Cross-cutting engineering docs

| File | Contents |
|---|---|
| [`git-pr-conventions.md`](git-pr-conventions.md) | Branch naming, commit trailer, PR title (IMP code!) + body order (verbatim prompt!), **Checkpoint 1/2 human gates**, archive-tag procedure, PLAN.md location convention |
| [`testing-strategy.md`](testing-strategy.md) | Mock-at-the-boundary philosophy, fixture catalogue (sandbox/mock_device/FakeAdb), dual-binding hazard, Windows gotchas, per-layer examples |
| [`next-tasks-planner-prompts.md`](next-tasks-planner-prompts.md) | Ready-to-paste planner prompts for IMP-R2 (done) / IMP-C1 / IMP-R1 |
| [`architecture-graph/graph.html`](architecture-graph/graph.html) | Interactive vis.js architecture graph (graphify-style: search, kind filters, click-for-details). Open in a browser |
| [`priority-graph/priority-graph.html`](priority-graph/priority-graph.html) | Interactive **priority** task graph — concentric rings by urgency (critical center), color = priority, hue = tier; click a node for details + a jump to its tier file. Keep in sync with `PRIORITY.md` |

## 5. Per-feature design archives (`docs/feature-*/`, `docs/imp-*/`)

Each merged feature ships its planning/decision artifacts here (the squash-merge keeps detailed
history only on archived branch tags — these folders are the readable record).

| Folder | Feature (merged) | Key files |
|---|---|---|
| `feature-auto-rollback/` | PR #14 (2026-06-01) — the RollbackJournal mechanism | **`ROLLBACK_MECHANISM.md`** (THE spec + §10 change-gate), `DECISIONS.md` (D-1..D-9/O/N incl. the N-6 bake-off), `rollback-architecture/CANDIDATE_{A,B,C}.md` + `DECISION.md`, scenario analyses, per-task subfolders (A1/C2/C8/C9/C11/G1...) |
| `feature-split-hash-deterministic/` | PR #20 (2026-06-08) — verifiable canonical hash | `PLAN.md`, `DECISIONS.md`, `STATUS.md` |
| `feature-multi-episode/` | PR #21 (2026-06-10) — multi_ep_alias | `PLAN.md`, `DECISIONS.md` |
| `feature-fix-episode-title-parse/` | PR #19 (2026-06-05) — dotted-title parsing | `PLAN.md`, `DECISIONS.md` |
| `imp-r2-recover-cli/` | PR #18 (2026-06-03) — recover CLI | `PLAN.md`, `DECISIONS.md` |
| `feature-video-dummy/` | PRs #1/#3 (2026-05-28) — real video dummies | `PLAN.md`/`planv2.md`, `DECISION.md`, dummy-size test scripts |
| `feature-adb-device-select/` | PR #2 (2026-05-28) — device pinning | `PLAN.md`, `STATUS.md` |
| `feature-others-category/` | IMP-D18 (2026-06-28) — 4th "Others" content category (sports now; documentaries later) | `PLAN.md`, `step-02-save_library-DECISION.md` + `step-04-disk-walk-roots-DECISION.md` (the two multi-candidate judge decisions) |
| `feature-extras/` | IMP-D19 (in flight on `feature/imp_d19_extras`, from 2026-06-29) — bonus content (`Specials\`/`Extra\`/`Trailers\`) gets the full push→dummy→fetch→restore lifecycle | `PLAN.md`, `DECISIONS.md` (locked Cards A–E: the A2 grouped JSON shape, the `--extras`/`--extras-size` CLI, flag-only `--fetchExtras`, the D1 full lifecycle, E1 additive rollback), `PROGRESS.md` (the per-step execution journal that makes the build cross-session resumable) |
| `feature-fable-review/` | THIS review session | see §3 |

## 5b. Operational edge-case dossiers (`docs/edge-case-*/`)

Real archival runs that failed in ways worth remembering — an external tool's limitation, a
media-format quirk, or a genuine MediaVault bug that only a live 60 GB run surfaces. Each folder
records the incident, the measured facts, the recovery procedure, and any code gap left open.

| Folder | Edge case | Key files |
|---|---|---|
| [`edge-case-unsplittable-tracks/`](edge-case-unsplittable-tracks/README.md) | 2026-08-24 — **`mkvmerge` cannot `--split` a FLAC track.** It surfaced as a bare `exit status 2` *after* a full prep, because the real error went to stdout and was discarded. Operational fix: remux the track to WavPack (lossless, splittable). Code fixed by **IMP-C19** (print mkvmerge's real error) + **IMP-C20** (`mkvmerge -J` pre-flight in `cmd_push` that names the track and refuses before splitting) | `README.md`, `ISSUE-flac-split-failure.md`, `CODEC-SPLIT-MATRIX.md` (measured per-codec + per-split-mode tables), `RUNBOOK-remux-before-split.md`, `CODE-GAPS.md` (C19/C20 done; Gap 3 open, opt-in only) |
| [`edge-case-replace-ponr-journal-lock/`](edge-case-replace-ponr-journal-lock/README.md) | 2026-08-24 — **a transient lock on `.mediavault_txn.json` during `cmd_replace`'s PONR write is misread as a locked media file** and retried as though the master rename had not happened, ending in a spurious `IRREVERSIBLE` banner. No data loss — the two-rename pattern, the C9 stale sweep and `recover` self-healed it. Proposed fix is **change-gated** (`mark_point_of_no_return()` placement) | `README.md` (incident, root cause, the 4-step recovery procedure, gated fix proposal) |

## 6. Agent-pipeline docs (`.claude/`)

`agents/*.md` (8 agent definitions: planner, orchestrator, executors ×3, git-agent, judge,
architect) · `AGENT_WORKFLOW_NOTES.md` (Opus 4.8 effort-tier migration record, top-level
orchestration decision) · see also `ARCHITECTURE.md` §19 and the public portable version at
`harinathsrinivas/claude-agent-pipeline`.

## 7. Maintenance rules for this index

- New doc → add a row here in the same commit.
- Superseding a doc → banner the old one (see `apple_tv_ui_roadmap.md` for the pattern) AND update
  its row here.
- Feature merged → its `docs/<feature>/` folder gets a row in §5 with PR number + date.
- **Task added/completed/re-prioritized → update `PRIORITY.md` AND `docs/priority-graph/priority-graph.html`
  AND the task's tier file, all in the same change** (protocol at the bottom of `PRIORITY.md`; rule also
  in `CLAUDE.md`).
