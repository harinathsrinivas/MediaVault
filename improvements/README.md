# `improvements/` — the MediaVault backlog + direction ("the brain")

> Everything about **what we're building and why**. If you're a new session/agent, read this, then
> `PRIORITY.md` (what to do next), then the relevant tier file or `ROADMAP_END_GOAL.md`.
> The repo-wide doc map is [`../docs/README.md`](../docs/README.md); the engineering reference is
> [`../ARCHITECTURE.md`](../ARCHITECTURE.md).

## Start here

| File | What it is |
|---|---|
| **[`PRIORITY.md`](PRIORITY.md)** | **The always-current "what to do next."** Critical bugs first (Band 0), a `👉 SUGGESTED NEXT TASK` pointer, five priority bands, and the maintenance protocol. Visual twin: [`../docs/priority-graph/priority-graph.html`](../docs/priority-graph/priority-graph.html) (interactive task graph — click a node for details + a jump to its tier file). |
| [`improvement_details.md`](improvement_details.md) | The operating manual for the IMP-XN task system: the task format (incl. the `Risk` / `If skipped` attributes), the tier map, dependency chains, the phased rollout, and how to hand a task to the planner agent. |
| [`ROADMAP_END_GOAL.md`](ROADMAP_END_GOAL.md) | **The master phased plan** from today's CLI to the couch-vault end goal (browse on Apple TV / Ugoos via Jellyfin → background fetch → in-client notify → watch → in-client archive). |

## The task tiers

`IMP-<XN>` tasks live in `improvements_tier<X>.md`. ~110 tasks across 12 tiers:

| Tier | Theme |
|---|---|
| [A](improvements_tierA.md) | Code architecture & refactoring |
| [B](improvements_tierB.md) | Performance |
| [C](improvements_tierC.md) | Robustness & reliability *(C12 = a crash that's live in production)* |
| [D](improvements_tierD.md) | New CLI commands |
| [E](improvements_tierE.md) | Ecosystem integration |
| [F](improvements_tierF.md) | Creative / moonshots *(header: the "container constraint")* |
| [G](improvements_tierG.md) | Lessons from similar projects |
| [H](improvements_tierH.md) | Agentic dev workflow |
| [R](improvements_tierR.md) | Auto-rollback hardening **(change-gated — read `../CLAUDE.md`)** |
| [S](improvements_tierS.md) | Streaming & media-server integration *(the end-goal daemon)* |
| [U](improvements_tierU.md) | Couch UX & clients |
| [X](improvements_tierX.md) | Cloud resilience & privacy *(multi-account redundancy + anti-scanning)* |

## The durable research (grounding for the roadmap)

| File | What it is |
|---|---|
| [`RESEARCH_MEDIA_SERVERS.md`](RESEARCH_MEDIA_SERVERS.md) | Jellyfin integration surfaces + in-client interaction design; Emby/Plex adoption deltas; client matrix incl. the Ugoos AM6B+ Dolby-Vision-FEL path. |
| [`RESEARCH_STORAGE_STREAMING.md`](RESEARCH_STORAGE_STREAMING.md) | Google Photos 2026 constraints (API lockdown verified); the tiered T0–T4 "streaming on the fly" verdict; OSS steal table; Netflix feature mapping. |
| [`JELLYFIN_SETUP_GUIDE.md`](JELLYFIN_SETUP_GUIDE.md) | Scratch-install → fully-configured Jellyfin on the Alienware, with exact per-library/plugin/transcode/client settings + a Phase-0 validation checklist. |
| [`BLOCKERS_AND_MOONSHOTS.md`](BLOCKERS_AND_MOONSHOTS.md) | Hard blockers (5), soft blockers (5), and 10 tracked ≥1%-possible moonshots, with a yearly re-check ritual. |

## How this folder is maintained

- **Every task add / complete / re-prioritize updates three things together** (rule in `../CLAUDE.md`):
  the task's `improvements_tier<X>.md` entry, `PRIORITY.md`, and the graph data in
  `../docs/priority-graph/priority-graph.html`. The protocol is at the bottom of `PRIORITY.md`.
- The **session record** that produced this backlog (the 2026-06-12 fable-review) lives separately under
  [`../docs/feature-fable-review/`](../docs/feature-fable-review/) — `SESSION_BRIEF.md` (verbatim prompt +
  decisions), `STATUS.md` (phase log + resume protocol), `REVIEW_NOTES.md` (the code-read findings),
  `PR_REVIEW.md`. Those are provenance; this folder is the live plan.
- Per-feature implementation artifacts (plans/decisions for a specific shipped feature) stay under
  `../docs/<feature>/` per the repo's git-PR conventions; this folder holds the cross-cutting backlog
  and direction.
