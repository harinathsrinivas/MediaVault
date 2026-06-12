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

### P3 — Architecture & README updates ✅ (commit bee7432)
- [x] ARCHITECTURE.md: line counts, layout, §7.8 PR#19 regex, §9.1a migrate_rehash_flag, §12a anchors
      re-verified, §13 rewritten (13-file suite), §16 strikethroughs + new alias findings, §17 done-markers, footer
- [x] README.md: install note (webdriver-manager + dev deps), layout block, test-coverage claim
- [x] requirements.txt itself deliberately NOT changed (docs-only scope) — tracked as quick-win IMP instead

### P4 — Architecture graphs (graphify-style) ✅ (commit bee7432)
- [x] `ARCHITECTURE_GRAPH.md` — 7 Mermaid views (system, lifecycle, state machine, ER data model, rollback flow, fetch sequence, seam map)
- [x] `docs/architecture-graph/graph.html` — interactive vis.js graph, hand-curated 40+ nodes/70+ edges, search/filter/details/physics (vis-network via CDN, offline fallback note)

### P5 — Improvement tier overhaul ✅ (commits 6631926, 940005b, 4e6397f, 42f9d3f)
- [x] `Risk` + `If skipped` attributes on every pending task across A–H, R (~70 tasks)
- [x] Re-review pass: statuses fixed (A7→done, C4→done), reorientations (E4/E9/E10/E12/F4/F6/F10/G2/G4),
      stale text refreshed (C1 resume-msg reality, R3 scan-shipped, G1 leftover sub-item)
- [x] New tasks from the review: A10-A12, B9-B10, C12-C15, R6-R9 (R6-R9 gate-flagged as decision requests)
- [x] New tiers: **S** (8 tasks — daemon, in-client flows, fetch hardening, T2/T3 spikes) and
      **U** (5 tasks — enrichment-before-archive, home rows, NFO pipeline, DV-FEL paths, C# plugin);
      W dropped (E12 covers the ops web UI)
- [x] `improvement_details.md`: tier registration, format spec gains Risk/If-skipped, change log,
      priority table marked historical, Phase 7/8 superseded pointer
- [x] Bonus findings during the pass: Tier F **container constraint**; `cmd_replace_group` doesn't
      catch RollbackHardFail (folded into R4)

### P6 — Web research dossier ✅ (done BEFORE P5 so new tiers cite findings)
- [x] `RESEARCH_MEDIA_SERVERS.md`: Jellyfin 10.10/10.11 state, integration surfaces (Webhook plugin,
      Sessions DisplayMessage, refresh API, C# plugin, .strm caveats), JellyBridge placeholder-as-button
      precedent, in-client interaction design (dummy-play = fetch request; grace auto-archive + action
      stubs + Keep-collection), Netflix-ification plugin shelf, NVENC notes, Emby delta (webhooks are
      Premiere-gated; viable fallback), Plex delta (lifetime now $749, no plugin surface → DON'T BUY),
      client matrix (Infuse+Swiftfin / Ugoos CoreELEC DV-FEL path)
- [x] `RESEARCH_STORAGE_STREAMING.md`: GP API lockdown verified (2025-03-31, self-uploaded-only;
      Picker useless for automation) → Selenium/browser-session is the ONLY automated path; gphotosdl
      (rclone org, local streaming proxy!) + gphotos-cdp as hardening/upgrade targets; **streaming
      verdict: T0 today / T1 couch-triggered fetch = roadmap centerpiece / T2 watch-chunk-1-while-
      fetching = experiment / T3 proxy-streaming = moonshot / T4 direct = blocked**; OSS steal table
      (zurg/arr/JellyBridge/Seerr/JellyHookDebouncer); Netflix feature map (smart-prefetch+auto-archive
      = highest leverage); topology question for user (4 Pixels vs 2 serials/2 accounts)
- [x] `JELLYFIN_SETUP_GUIDE.md`: scratch→configured (install/wizard/3 libraries with exact toggles/
      users/plugins now-vs-skip/trickplay-before-archive policy/NVENC/clients incl. CoreELEC add-on
      mode/network policy/backups) + Phase-0 validation checklist incl. DisplayMessage client test

### P5/P7 pre-decisions (LOCKED — keep consistent post-compaction)
- New-bug IMP codes: **IMP-C12** alias crashes scan_unprepped/local_status (high) · **IMP-C13**
  single-id alias handling (med) · **IMP-C14** CLI parser papercuts: push_group hang + mainfetch argv
  guard + silent replace (low-med) · **IMP-C15** micro-robustness: repair_dummies os.replace +
  _verify_chunk_hash IndexError (low)
- Rollback-adjacent (ALL change-gated, decision-first): **IMP-R6** restore-merge-failure leaves no
  dummy on disk · **IMP-R7** journal clobber on re-run · **IMP-R8** eager rehash_tmp not journalled ·
  **IMP-R9** prep_season alias creation outside journal
- Perf: **IMP-B9** hash progress-print throttle · **IMP-B10** harvester processed_files persistence
- Status fixes: IMP-C4 → done (PR #2); IMP-A7 → done (suite exists, conftest+13 files)
- New tiers: **S = Streaming & media-server integration** (daemon, webhook flow, Jellyfin wiring,
  smart prefetch, archive policy, fetch hardening via gphotosdl/CDP patterns) and
  **U = Couch UX & clients** (trickplay-before-archive, collections/rows, metadata→NFO, client
  validation, DV-FEL path docs). NO new W tier (E12 covers web UI).

### P7 — End-goal roadmap ✅ (commit 072f186)
- [x] `ROADMAP_END_GOAL.md`: phases 0-6, requirement→mechanism map, dependency weave, Emby/Plex
      adoption verdicts, risk register, topology question
- [x] `apple_tv_ui_roadmap.md` superseded with a corrections banner (stale §5 detection, daemon-first
      resequencing, in-client-only; Jellyfin choice re-confirmed)
- [x] `BLOCKERS_AND_MOONSHOTS.md`: 5 hard blockers, 5 soft blockers, 10 tracked moonshots, yearly ritual
- [x] Streaming verdict: tiered T0-T4 answer in roadmap §4 backed by RESEARCH_STORAGE_STREAMING §2

### P8 — Master docs index + consistency pass ✅
- [x] `docs/README.md`: master index (orientation order, root docs, tiers, fable-review dossier,
      cross-cutting docs, per-feature archives, agent docs, maintenance rules)
- [x] Root README pointer + CLAUDE.md pointer (tiers A–H,R,S,U + docs index + roadmap)
- [x] `git-pr-conventions.md` Co-Authored-By made model-truthful (was hardcoded Opus 4.8)

### P9 — Wrap-up (in progress)
- [x] Final STATUS update; memory-dir update
- [ ] Push branch; create PR (no pre-existing IMP code applies — this PR *creates* the new tasks;
      title carries no IMP code by design, body references the created tiers)
- [ ] **STOP — ask user before merge (Checkpoint 1)**

## Post-merge follow-ups for the user (the answer queue)
1. **4-Pixel topology** (REVIEW_NOTES §E1 / roadmap §6) — answer unblocks E7/S5 lane counts.
2. **R6/R7 gate decisions** when convenient (options laid out in improvements_tierR.md).
3. Start **Phase 0** (JELLYFIN_SETUP_GUIDE.md) whenever ready — zero code required.

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
- 2026-06-12: P1+P2 complete (full code/docs read, 21-PR review) — commits `d945742`, `4f2c50f`.
- 2026-06-12: P3+P4 complete (ARCHITECTURE/README truth-up, Mermaid + vis.js graphs) — commit `bee7432`.
- 2026-06-12: P6 research dossier (3 docs) — commit `b4e1fc6`. (Usage-limit pause hit during P5 tier D read; resumed cleanly from this tracker.)
- 2026-06-12: P5 tier overhaul A–U complete — commits `6631926`, `940005b`, `4e6397f`, `42f9d3f`.
- 2026-06-12: P7 roadmap + blockers/moonshots + supersession banner — commit `072f186`.
- 2026-06-12: P8 master index + consistency pass; P9 wrap-up: branch pushed, PR #22 opened.
- 2026-06-12: **Follow-up round** (user reviewed PR, requested additions before merge): added **Tier X**
  (cloud resilience & privacy — multi-account replication, encryption/anti-scanning, ban canary),
  **IMP-C16** (anime-account fetch routing), `PRIORITY.md` + the interactive `docs/priority-graph/priority-graph.html`,
  resolved the 3-account topology question across all docs, wired the keep-priority-current rule into
  CLAUDE.md/improvement_details.md/docs README. Pushed; PR updated. **Still awaiting Checkpoint-1 merge approval.**

## Follow-up round deliverables (P10)
- [x] Tier X (X1–X5) with §0 sharing-vs-replication research table + the deletion-cascade answer
- [x] IMP-C16 (3 accounts ⇒ anime needs its own Chrome profile; fetch currently mis-routes)
- [x] `PRIORITY.md` (always-current ordering, critical-first, suggested-next pointer, maintenance protocol)
- [x] `docs/priority-graph/priority-graph.html` (futuristic concentric task graph, click→details + jump)
- [x] Topology question resolved in REVIEW_NOTES/ROADMAP/RESEARCH/tierE; keep-current rule wired in
- [x] Tier X + PRIORITY registered in ARCHITECTURE/README/CLAUDE/docs-README/improvement_details
