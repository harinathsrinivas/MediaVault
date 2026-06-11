# Fable Review — STATUS (living tracker)

**Branch:** `feature_fable_review` · **Started:** 2026-06-12 · **Scope:** docs + roadmap only (no feature code)

## Resume protocol (read this first in a fresh session)

1. Read this file top to bottom, then `SESSION_BRIEF.md` (verbatim task + decisions).
2. `git log --oneline main..feature_fable_review` — what is already committed.
3. Find the first unchecked item in the phase checklist below; continue from there.
4. Keep this file updated as you work (check items off, append to the log) and commit it with each batch of work. Findings go in `REVIEW_NOTES.md` as you read — never hold them only in context.
5. At session end: push branch, open PR (body = Claude summary first, then verbatim prompt from `SESSION_BRIEF.md`, then trailer). **STOP before merging — Checkpoint 1 is human-gated.**

## Phase checklist

### P0 — Setup ✅
- [x] Branch `feature_fable_review` created from `main`
- [x] Housekeeping commit `5000533` (README re-format + PR #21 docs)
- [x] `SESSION_BRIEF.md` (verbatim prompt + the 4 user decisions)
- [x] This tracker
- [x] Memory-dir entry (`project_fable_review.md` + MEMORY.md index; split-hash memory marked MERGED)

### P1 — Repo deep read ✅ (findings in `REVIEW_NOTES.md`)
- [x] `ARCHITECTURE.md` (2013 lines) — full read; 10 stale items logged (§B of notes)
- [x] `main.py` (3081 lines) — full read; 11 bugs/smells logged (§A), incl. 2 alias crashers
- [x] `mainfetch.py` (491 lines) — full read
- [x] `mvcommon.py` (168 lines) — full read
- [x] `README.md` — full read (claims verified; tests claim stale, requirements gap confirmed)
- [x] `improvement_details.md` + IMP status inventory via grep (9 done: A1,C2,C8,C9,C11,E13,G1,H1,R2;
      status errors found: C4 actually shipped PR #2, A7 effectively done by the test suite)
- [x] `apple_tv_ui_roadmap.md` — read; §5 dummy-detection design INVALIDATED by video-dummy feature
- [x] Root `PLAN.md` (multi-ep leftover, gitignored) / `STATUS.md` (auto-rollback-era, **tracked & stale**
      → should be gitignored like PLAN.md); `2026-06-07.md` = empty stray file
- [x] `docs/`: testing-strategy.md read (2 small stale items: short_id "8-char sha256" wrong — 6-char md5;
      md5sum vs sha256sum); rollback docs covered via §12a + spec blocks + PR #14 artifacts;
      next-tasks-planner-prompts.md skimmed (prompts for R2✅/C1/R1)
- [x] `tests/` (13 files), `tools/` (migrate_lib + migrate_rehash_flag — latter undocumented in ARCH §9),
      `requirements*.txt` (missing requests + webdriver-manager confirmed), `.claude/agents/` (8 agents),
      `step2_validate.ps1` (stray root helper script — cleanup candidate)
- NOTE: full tier-file texts will be read tier-by-tier during P5 (only statuses inventoried so far)

### P2 — Recent PR review ✅ (→ `PR_REVIEW.md`)
- [x] All 21 merged PRs inventoried with dates/branches
- [x] Deep: #14 auto-rollback (+2814/−380, 20 files), #20 split-hash (+3547/−50, 16 files)
- [x] Medium: #18 recover CLI, #19 dotted-title parse, #21 multi-episode (+ its missed iterators)
- [x] Light: #1–#13, #15–#17; process observations logged (tracked STATUS.md, worktree leftovers)

### P3 — Architecture & README updates
- [ ] Fix/refresh stale ARCHITECTURE.md sections found in P1/P2 (verify against code, don't trust docs)
- [ ] README.md corrections (e.g., missing commands, requirements gaps)
- [ ] Reconcile §17 Future Work / §19 agent workflow with current reality

### P4 — Architecture graphs (graphify-style)
- [ ] `ARCHITECTURE_GRAPH.md` — Mermaid: system overview, command pipeline, data model, status state machine, rollback flow, fetch flow
- [ ] `docs/architecture-graph/graph.html` — interactive vis.js graph (nodes = commands/functions/data/externals; edges = calls/reads/writes; search + filter), self-contained

### P5 — Improvement tier overhaul
- [ ] Add `Risk` + `If skipped` (impact_if_skipped) attributes to every **pending** task in tiers A–H, R (done tasks: skip)
- [ ] Re-review every pending task: still valid? superseded? priority right?
- [ ] Add newly found improvements from P1 code review
- [ ] New tiers for the end goal (planned: **S** = serving/streaming & Jellyfin integration, **U** = UX/clients, **W** = web/ops UI if warranted) — same format as existing tiers
- [ ] Update `improvement_details.md` master list accordingly

### P6 — Web research dossier (→ `RESEARCH_*.md`)
- [ ] Jellyfin deep dive: plugin API, webhooks, .strm/virtual libraries, session-message API (in-client notify!), Collections, watched-state events, Swiftfin/Android TV/Infuse clients
- [ ] `JELLYFIN_SETUP_GUIDE.md`: scratch install → fully configured for MediaVault (user explicitly wants super-detailed steps)
- [ ] Emby + Plex delta analysis: what buying/using each adds; user owns Emby lifetime, may buy Plex before price increase (next month, i.e., July 2026)
- [ ] Google Photos constraints 2026: API lockdown, fetch options, ranged access, ToS risk — feasibility backbone for streaming-on-the-fly
- [ ] OSS landscape: riven/zurg/debrid-style on-demand ecosystems, Sonarr/Radarr patterns, Stremio, gphotos tooling, comparable "cloud vault" projects
- [ ] Netflix/streaming feature steal list (trickplay, continue-watching, intro skip, autoplay, smart-fetch-next-episode, "still watching?" → archive prompt analog)
- [ ] Hardware notes: Ugoos AM6B (CoreELEC/DV-FEL path), Apple TV (Infuse vs Swiftfin), Alienware as 24/7 server (NVENC transcode)

### P7 — End-goal roadmap
- [ ] `ROADMAP_END_GOAL.md`: phased path from today's CLI → couch-only Netflix-like flow (browse → fetch/play → in-client notify → watch → in-client archive prompt)
- [ ] Reconcile/supersede `apple_tv_ui_roadmap.md` (mark its status explicitly)
- [ ] `BLOCKERS_AND_MOONSHOTS.md`: hard blockers, 1%-possible ideas (user explicitly wants these tracked)
- [ ] Streaming-playback-on-the-fly: explicit feasibility verdict with evidence

### P8 — Master docs index + consistency pass
- [ ] `docs/README.md`: master index — every doc file, what it means/contains (user: "master .md file")
- [ ] Root README pointer to the index
- [ ] Cross-link pass; stale-reference sweep (e.g., git-pr-conventions Co-Authored-By model name)

### P9 — Wrap-up
- [ ] Final STATUS update; memory-dir update
- [ ] Push branch; create PR (IMP code: check tier files — likely none yet exists for "review" itself; new-tier tasks will be created BY this PR, reference them)
- [ ] **STOP — ask user before merge (Checkpoint 1)**

## Deliverable file map (planned)

| File | Content |
|---|---|
| `docs/feature-fable-review/SESSION_BRIEF.md` | Verbatim prompt + decisions ✅ |
| `docs/feature-fable-review/STATUS.md` | This tracker ✅ |
| `docs/feature-fable-review/REVIEW_NOTES.md` | Running findings from the code/docs deep read |
| `docs/feature-fable-review/PR_REVIEW.md` | What each recent PR changed (focus #14, #20) |
| `docs/feature-fable-review/RESEARCH_MEDIA_SERVERS.md` | Jellyfin deep dive + Emby/Plex delta |
| `docs/feature-fable-review/JELLYFIN_SETUP_GUIDE.md` | Scratch-install → fully-configured guide |
| `docs/feature-fable-review/RESEARCH_STORAGE_STREAMING.md` | Google Photos constraints + streaming feasibility |
| `docs/feature-fable-review/RESEARCH_OSS_LANDSCAPE.md` | Comparable projects + Netflix feature steals |
| `docs/feature-fable-review/ROADMAP_END_GOAL.md` | The phased master roadmap |
| `docs/feature-fable-review/BLOCKERS_AND_MOONSHOTS.md` | Hard blockers + 1% ideas |
| `ARCHITECTURE_GRAPH.md` (root) | Mermaid graph views of the architecture |
| `docs/architecture-graph/graph.html` | Interactive vis.js architecture graph |
| `docs/README.md` | Master index of all documentation |
| `ARCHITECTURE.md`, `README.md` | Updated in place |
| `improvements_tier*.md`, `improvement_details.md` | Attributes + new tasks + new tiers |

## Decisions log

- 2026-06-12: Branch name `feature_fable_review` kept exactly as user specified (conventions would say `feature/fable_review`; explicit user instruction wins).
- 2026-06-12: Commit trailer uses `Claude Fable 5` (truthful model attribution; `git-pr-conventions.md` hardcodes Opus 4.8 — flagged for a conventions-doc touch-up in P8).
- 2026-06-12: Git operations run inline in the main session (not via git-agent) — this is an interactive docs/research session, not a pipeline plan execution; human gates still honored.
- 2026-06-12: In-client-only interaction design per user decision #4 — research must prioritize Jellyfin session-message API / plugin surfaces; fallbacks only where in-client is provably impossible, explicitly marked.

## Work log

- 2026-06-12: Session start. Orientation scan, 4 setup questions answered, branch created, housekeeping commit `5000533`, tracking files written.
