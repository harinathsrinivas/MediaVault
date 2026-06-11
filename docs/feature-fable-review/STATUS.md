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

### P5 — Improvement tier overhaul
- [ ] Add `Risk` + `If skipped` (impact_if_skipped) attributes to every **pending** task in tiers A–H, R (done tasks: skip)
- [ ] Re-review every pending task: still valid? superseded? priority right?
- [ ] Add newly found improvements from P1 code review
- [ ] New tiers for the end goal (planned: **S** = serving/streaming & Jellyfin integration, **U** = UX/clients, **W** = web/ops UI if warranted) — same format as existing tiers
- [ ] Update `improvement_details.md` master list accordingly

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
