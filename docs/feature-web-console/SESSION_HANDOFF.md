# SESSION HANDOFF — IMP-E12 Web Operations Console

> **Purpose:** This file lets a fresh Claude session (different account, no access to the original
> conversation) continue this work seamlessly. Read it top-to-bottom, then read the three artifacts it
> points to. Written 2026-06-21.
>
> **You are continuing a PLANNING-COMPLETE task. No code has been written yet.** The plan is finalized,
> every decision is locked, and the next action is to either (a) refine the plan if the user asks, or
> (b) drive the implementation pipeline when the user gives the go-ahead.

---

## 0. TL;DR — current status & immediate next action

- **Task:** turn `python main.py scan_unprepped` into a futuristic local **web operations console**
  (FastAPI) — a merged "Disk Reclaim" view that scans unprepped + prepped-but-still-local files,
  suggests the exact next command + a media-server-correct target folder per item, integrates `sort`,
  and offers a confirm-gated one-click `replace`. This is **IMP-E12**.
- **Deliverable so far:** `PLAN.md` (plan only — NO code, NO branches). Done and finalized.
- **All decisions are LOCKED.** There are **zero open decisions**. Prerequisites are all confirmed.
- **Pre-flight fixes applied 2026-06-21 (post-handoff, after a 3-planner re-audit):** 5 grounding corrections to PLAN.md — (1) promote `make_video` to `tests/conftest.py` so the top-level step-2/4 tests can see it; (2) `on_disk_real = size >= DUMMY_MAX_BYTES` to match `cmd_check`/`cmd_repair_dummies`; (3) `_will_split` (NOT the non-existent `pure_should_split`), `main.py:347`; (4) pin `_resolve_alias(lib, mid)` as 2-arg; (5) step-9 graph edits the `TASKS`/`EDGES` arrays, sets `priority="done"` for done nodes, and repoints the `⚡ Next` banner. PLUS **step 6 (frontend) bumped sonnet→opus ×3** (user decision — the headline UI). Locked decisions W-1…W-13 are otherwise unchanged; model split is now **4 opus / 4 sonnet / 1 haiku**.
- **Immediate next action:** WAIT for the user to say "go / kick off / start implementation." Do NOT
  start writing code unprompted. When they say go, follow §6 (Execution model) to run the pipeline.
- **Do NOT** try to resume any old sub-agent by ID — sub-agent contexts do not survive across sessions.
  Start fresh from `PLAN.md`.

---

## 1. Read these three files first (in this order)

1. `docs/feature-web-console/PLAN.md`  — the finalized implementation plan (9 steps, tests,
   verification, open decisions, branch/PR, manual tests). **This is the single source of truth for
   what to build.** (A byte-identical live copy is at repo-root `/PLAN.md`, which is gitignored.)
2. `docs/feature-web-console/DECISIONS.md` — W-1…W-13: the load-bearing decisions + WHY, plus the
   "ALL CONFIRMED" prerequisites block.
3. `BEST_PRACTICES.md` (repo root) — compounding decisions the user locked (esp. §A1 split-size = 8 GB,
   §A5 don't-delete-until-replicated). The plan is already aligned to these; keep it that way.

Also auto-loaded every session (no need to open unless relevant): `CLAUDE.md` (project rules),
`ARCHITECTURE.md` (engineering reference), `improvements/PRIORITY.md` + `improvements/README.md` (the
task board), `~/.claude/CLAUDE.md` (global rules), and the memory index `MEMORY.md`.

---

## 2. The original task (verbatim — preserve for the PR's "Original task prompt" section)

> $ python main.py scan_unprepped
> You know this command right? this checks all my local files which are unprepped and ready to be prepped.
> Modify this Fully with some futuristic nice UI application or webpage. It should open this nice UI have multiple buttons:
> 1. to scan prepped — have all existing commands which this covers properly
> 2. add new functionality to scan and get prepped and pushed also — but currently in Local — which is not archived which occupies space. You can merge the above 2 also and find a way to distinguish above 2 scenarios. Also suggest on the next commands to run for the items in scan if I select a particular item based on our naming conventions and folder structure also.
> Note that it should work well in plex and emby and jellyfin servers. Give option to move the new unprepped file to some proper suggested folders also in the same UI.
> 3. also add the option to sort also in same UI — this sort is already an existing command but separate one. lets integrate into the same UI which covers all operations.
> also for 2 — already pushed files when I just have in my local and already watched — add a button to just replace — it should do current replace step which checks if already uploaded properly before deleting the original file and replacing with dummy.
>
> Give me different options to fix this. let me check that and decide how to proceed. Once I confirm, I want you to create an elaborate plan to fix this in the best and optimal way. If any decision pending, give me live example in real world usecase complete step by step and ask me about the different options before you finalize the plan. Also, any other related improvements, how this approach will affect that can you elaborate. Also any prerequisite small task you want me to complete before we start this implementation?
>
> Deliverables: PLAN.md only (no code, no branches) with steps + tests + verification + "Open Decisions". Note if we are solving any improvement tasks with this task say C18 - IMP-C18 needs to be marked done in improvements_tierC.md on implementation, the architect updates ARCHITECTURE.md/README (documented behavior change), and I want branch name, PR to main, and manual test commands at the end. Also you also need to update the priority graph and suggest me the next starts we can start.

(Note: "C18" in the prompt was only an EXAMPLE of the mark-the-IMP-done pattern. IMP-C18 is already
merged/done — do NOT touch it. The actual IMP this implements is **E12**; see §4.)

---

## 3. All locked decisions (the full decision log)

The user reviewed options (with a worked real-world example) and chose. Everything below is FINAL and
already baked into PLAN.md + DECISIONS.md.

| # | Decision | Chosen | Notes |
|---|----------|--------|-------|
| 1 | Scope | **Operations console** | NOT a viewing UI. Jellyfin keeps viewing (locked 2026-06-12). Viewing UI is out-of-scope. |
| 2 | UI mechanism | **Local web app** | `python main.py web` → `http://127.0.0.1:8765` (port/host overridable; `--no-browser`). FastAPI + uvicorn backend + static HTML/CSS/JS (no build step). Shaped to become the Tier-S daemon UI (IMP-S2) — not throwaway. |
| 3 | Scan view | **One merged "Disk Reclaim" view** | Per-item state **badge**: `UNPREPPED` / `LOCAL·NOT-PUSHED` / `PUSHED·NOT-ARCHIVED` / `RESTORED·REPLACE-AGAIN`; filter chips; total-reclaimable-GB. Badge = the distinguisher between the two scan scenarios. Reclaimability decided by ACTUAL on-disk size (>= `DUMMY_MAX_BYTES`), not status alone. |
| 4 | Move-to-folder | **Suggest-only (v1)** | UI shows a target folder + copyable command; never moves/renames files. Real one-click move = follow-up **IMP-D8** (`relocate`). |
| 5 | Folder tag | **Current MediaVault layout + curly-brace provider tag** | NEW suggestions use `{tmdb-<id>}` for movies, `{tvdb-<id>}` for TV & anime (the officially cross-compatible Plex/Emby/Jellyfin syntax — user's explicit refinement over the older square `[tvdbid-…]`). Applied to **NEW items only**; existing folders are NEVER renamed (breaks hashing/sidecars); user migrates existing manually. Provider id is an **editable placeholder** (no auto-lookup here). |
| 6 | Next-command suggestions | **Deterministic + guessed-editable id** | Rule-based from the state tuple → exact command. UNPREPPED items get a guessed, editable MediaVault id (`mov-<lang2>-<year>-<slug>`, `tv-…-sNNeMM`, `ani-…`). **Push suggestion uses `SIZE_GB 8`** (BEST_PRACTICES §A1 lock; NOT `SIZE_MB 9900` which is ~10.38 GB and exceeds Google's 10 GB cap). No TMDB enrichment (deferred to D10/E3). |
| 7 | Action safety | **UI runs real `cmd_*` UNCHANGED** | `prep`/`push`/`replace`/`sort`/`prep_push_rep` via the existing functions; `replace` requires server-enforced confirm (HTTP 409 without `confirm:true`) + a UI modal; server is localhost-only; long actions report via a polled job mechanism. |
| 8 | Foundations | **Thin in-process data layer NOW** | New pure functions (`collect_reclaimable`, `classify_entry_state`, `suggest_next_command`, `suggest_target_folder`, `guess_manual_id`) return dicts, called directly. NO subprocess, NO dependency on the pending `--json`/argparse refactor (IMP-A2/A4/A5 are RELATED, not prerequisite — A4 will later reuse these). |
| — | Provider-id source | **Editable placeholder now** | Auto TMDB/TVDB lookup deferred to IMP-D10/E3. |
| — | Progress mechanism | **Polling** (`GET /api/job/{id}`) | Not SSE; SSE/WebSocket is the Tier-S/IMP-F10 upgrade. |
| — | requirements.txt scope | **Bundle full IMP-A10** | Add fastapi+uvicorn AND requests+webdriver-manager + the undetected-chromedriver comment. A10 is marked DONE in step 9. |
| — | Default port | **8765** (localhost, overridable) | |

### Process decisions the user added (THIS plan only)
- **Multi-candidate bake-offs on 3 steps** (1, 3, 6): each runs N candidate worktrees → judge.
  These are the genuinely fork-worthy decisions (data model / action-execution-concurrency model / UI
  information-architecture). Each pins its output/HTTP/behavior contract so candidates differ only on
  internal strategy → no downstream ripple. The other 6 steps stay single-executor. ("Don't worry
  about usage" was explicitly granted.)
- **Checkpoint C3 — HUMAN judge gate** (DECISIONS W-13): after EACH multi-candidate step's judge
  produces its decision, the orchestrator must **NOT auto-merge/auto-commit**. It STOPS and presents to
  the user: the judge's chosen candidate, full rationale, per-candidate analysis vs Judge criteria, and
  its OWN recommendation. The user accepts the pick OR selects a different candidate; only then does the
  winner get merged + committed and the pipeline proceeds. This is in addition to the standard human
  gates: **C1** (merge→main) and **C2** (branch archival).

### BEST_PRACTICES.md alignment already done in the plan
- §A1: push suggestion is `SIZE_GB 8` (8 GiB ≈ 8.59 GB decimal, ~14% margin under the 10 GB cap).
- §A5/B1/B2: a Risks note flags that one-click `replace` reuses `cmd_replace`'s verify-before-delete but
  adds NO replication gate (IMP-X1) or pre-delete subtitle/enrichment (IMP-E1/U1) — consciously
  accepted, out-of-scope here. Keep the confirm-modal copy to "verifies the cloud upload" only.

---

## 4. IMP mapping (task board bookkeeping — done at IMPLEMENTATION time, NOT now)

- **Implements IMP-E12** (`web` command — `improvements/improvements_tierE.md:243`). → mark done.
- **Introduces NEW IMP-D16** (`scan_reclaimable` — the four-state reclaim scan / data layer behind
  `web`). → add to `improvements_tierD.md` + `PRIORITY.md` + `docs/priority-graph/priority-graph.html`
  in the same change.
- **Closes IMP-A10** (requirements truth-up, bundled into step 8). → mark done in `improvements_tierA.md`
  + PRIORITY.md + graph.
- **Advances IMP-D1** (stats): delivers only the total-reclaimable-GB slice; do NOT mark D1 done.
- Architect updates `ARCHITECTURE.md` (§5 subcommand table + a `web`/data-functions subsection) and
  `README.md` (the new `web` command) — documented behavior change. (This is step 9.)
- priority-graph node schema is `[id, label, tier, priority, status, note]` — flip the `status` field
  (5th) to `"done"` for E12 & A10, add a D16 node `["D16","scan_reclaimable","D","high","done","…"]`,
  add edge `["E12","D16"]`, keep `["A4","E12"]`. Graph and PRIORITY.md must agree.

---

## 5. Plan structure (what to build) — summary; full detail in PLAN.md

9 steps. Model split: **4 opus, 4 sonnet, 1 haiku.** Multi-candidate: steps **1, 3, 6** (`[candidates: 3]`).

1. **[opus, candidates:3]** Pure data-functions in `main.py` (`collect_reclaimable`, `classify_entry_state`,
   `suggest_next_command`, `suggest_target_folder`, `guess_manual_id`). FIXED output contract; candidates
   differ on scan/index/de-dup strategy (disk-first / library-first / unified-normpath-index). MUST skip/
   `_resolve_alias` `season_map` + `multi_ep_alias` (the PR#21 crash class). → **C3 gate after judge.**
2. **[sonnet]** Unit tests `tests/test_web_datafns.py` (use `sandbox` + `make_video` fixtures).
3. **[opus, candidates:3]** FastAPI app + action-execution model in `webui/server.py`. FIXED HTTP contract
   (`GET /api/reclaim`, `GET /api/library`, `POST /api/action/{name}` w/ allow-list + 409-without-confirm
   + 202 job_id, `GET /api/job/{id}`, StaticFiles SPA). Candidates differ on concurrency model
   (thread+captured-stdout / subprocess-per-action / serialized-worker-queue). → **C3 gate after judge.**
4. **[sonnet]** Endpoint tests `tests/test_web_endpoints.py` (FastAPI `TestClient`; `pytest.importorskip("fastapi")`).
5. **[sonnet]** `cmd_web(host,port,open_browser)` + the `web` dispatch arm in `main.py` (lazy-import
   fastapi/uvicorn inside the function so importing `main` never hard-requires them).
6. **[opus, candidates:3]** Frontend SPA `webui/static/{index.html,app.js,styles.css}`. FIXED behavior
   contract; candidates differ on IA/interaction model (mission-control table / card grid / master-detail).
   → **C3 gate after judge.**
7. **[opus]** Wire `web`/`collect_reclaimable` into `tests/smoke` (per-command test + `TestAliasSweep`
   entry — the anti-PR#21 guard for the new whole-library iterator).
8. **[sonnet]** Truth-up `requirements.txt` — bundle full IMP-A10 (fastapi, uvicorn[standard], requests,
   webdriver-manager, undetected-chromedriver comment).
9. **[haiku]** Docs + IMP bookkeeping (README, ARCHITECTURE.md, tier files, PRIORITY.md, priority-graph;
   mark E12+A10 done, add D16, advance D1).

**New package:** `webui/` (`__init__.py`, `server.py`, `static/`). **Touches** `main.py` (data-functions
+ `cmd_web` + dispatch). No new library entry type → `ENTRY_TYPE_KEYS` and its guard test are unchanged.

---

## 6. Execution model — how to run the implementation (when the user says go)

**CRITICAL (from CLAUDE.md):** the multi-agent pipeline runs from the **main/top-level session**, which
acts as the orchestrator by following `.claude/agents/orchestrator.md` as a *playbook* and spawning
executor/judge/git sub-agents itself (depth-1 works). **Do NOT launch the `orchestrator` agent via the
Task tool** to execute the plan — sub-agents can't spawn sub-agents (nesting depth = 1), and it would
silently fall back to running everything inline. Spawn `git-agent`, `executor-*`, and `judge` directly.

Pipeline outline:
1. `git-agent` creates branch `feature/web_console` from up-to-date `origin/main`.
2. For each step in order: dispatch to the correct `executor-<model>` with tailored context from PLAN.md.
   - For single-executor steps: execute → run that step's `verify:` → `git-agent` commits the step.
   - For multi-candidate steps (1, 3, 6): create N candidate worktrees via `git-agent` → run the same
     step in each → `judge` produces DECISION → **STOP at Checkpoint C3**: present judge decision +
     per-candidate analysis + your recommendation to the user → user picks/overrides → `git-agent`
     merges the SELECTED candidate, commits → continue.
3. Run the smoke gate `python -m pytest tests/smoke -q` (MUST be green, < 30s) as the final verification
   because `main.py` is touched. Also `python -m pytest -q` for the full suite.
4. `git-agent` pushes; open the PR to `main` — **STOP at Checkpoint C1** (human-gated merge). PR title
   MUST include `— IMP-E12`; body order: auto Claude summary → `## Original task prompt` (verbatim from
   §2) → `🤖 Generated with Claude Code` trailer.
5. After the user approves & merges: **Checkpoint C2** (human-gated) — archive the branch as an annotated
   `archive/feature/web_console` tag, then delete the branch (local + remote).

**Run tests as `python -m pytest …`, not bare `pytest`** (project convention; memory `project_cli_parser_papercuts`).

---

## 7. Standing project rules the continuing Claude MUST follow (from CLAUDE.md)

- **Human gates:** never merge to `main` or archive a branch without explicit user confirmation
  (Checkpoints 1 & 2). Plus the new **Checkpoint C3** judge gate above.
- **Smoke gate:** `pytest tests/smoke -q` before any PR and before committing any code-touching step.
- **ENTRY_TYPE_KEYS registry + guard:** any new/renamed/removed entry type or shared field must update
  `main.py:ENTRY_TYPE_KEYS` and keep whole-library iterators alias/`season_map`-safe. (This plan adds
  NO new entry type, so the registry is untouched — but the new iterator MUST skip aliases.)
- **Auto-rollback change-gate:** do NOT alter rollback behavior. This plan reuses `cmd_replace`
  verbatim → gate NOT tripped (stated explicitly in PLAN.md). Keep it that way.
- **Editing `.claude/agents/`:** snapshot the dir to `.claude/agent-backups/<date>/` first; watch the two
  silent footguns (invalid YAML frontmatter with unquoted `:`; duplicate `name:` across the scanned tree).
  (Not expected for this task.)
- **Surface contradictions:** if a capability gap or contradiction with the plan appears, STOP and surface
  it to the user as an explicit decision — don't silently work around it.
- **PLAN.md location convention:** root `/PLAN.md` = live, gitignored; `docs/feature-web-console/PLAN.md`
  = tracked canonical. Keep both identical. git-agent commits only the `docs/` copy.
- **Keep the board current:** when E12/D16/A10/D1 change status, update the tier file + `PRIORITY.md` +
  `docs/priority-graph/priority-graph.html` in the SAME change.

---

## 8. Environment & git state (as of handoff)

- **Repo:** `C:\Users\harin\PycharmProjects\MediaVault` · branch `main` · synced with `origin/main` at
  `ee70e8a` (the `docs: add BEST_PRACTICES.md (#29)` commit). Nothing to pull.
- **Platform:** Windows 10, shell is **PowerShell** (a Bash tool is also available for POSIX scripts).
  Use `python -m pytest`. Paths use `C:\...`.
- **Git status:** clean except the untracked `docs/feature-web-console/` (this handoff + PLAN.md +
  DECISIONS.md). Root `/PLAN.md` is gitignored. **No branch created, nothing committed** — plan-only
  deliverable so far, exactly as intended.
- **Deps to be added at impl time:** `fastapi`, `uvicorn[standard]`, `requests`, `webdriver-manager`
  (bundled IMP-A10). Not installed yet.

---

## 9. Tooling caveats learned this session (so you don't repeat them)

- **`SendMessage` is NOT available** in this Claude Code build (absent from the tool list, deferred-tool
  registry, and ToolSearch — even though the Agent tool docs reference it). You cannot "continue" a prior
  sub-agent. For small plan revisions, **edit `PLAN.md`/`DECISIONS.md` directly yourself** (do NOT spawn
  a fresh cold planner for minor edits — that re-derives context expensively and annoyed the user).
- **`AskUserQuestion` works at the top level** (main session) but NOT inside the `planner` sub-agent's
  sandbox. To ask the user structured questions, ask from the main session.
- Sub-agent IDs from a prior session are dead on a new session — start fresh from PLAN.md.

---

## 10. Memory pointers (auto-loaded context on this machine)

`MEMORY.md` indexes prior project memories. Most relevant here: the multi-step pipeline conventions,
`project_cli_parser_papercuts` (run `python -m pytest`), `project_future_apple_tv_ui` (viewing UI is a
LATER goal — this E12 task is the *operations* console, a different surface), and the auto-rollback /
ENTRY_TYPE_KEYS guardrails. The Jellyfin-owns-viewing vs E12-owns-operations split was locked in the
2026-06-12 fable-review.

---

## 11. EXACT next steps for the continuing Claude

1. Read PLAN.md, DECISIONS.md, BEST_PRACTICES.md (§1 above).
2. Greet the user briefly, confirm you've absorbed the context, and state the one open question:
   **"Ready to kick off implementation of `feature/web_console`, or do you want to refine the plan first?"**
3. If they say refine → edit PLAN.md + DECISIONS.md inline (keep both copies in sync via
   `cp PLAN.md docs/feature-web-console/PLAN.md`), don't cold-spawn a planner.
4. If they say go → follow §6 exactly. Create the branch, run steps in order, **pause at each C3 judge
   gate (steps 1, 3, 6)** and at the C1 merge gate. Keep the smoke gate green.
5. Do NOT mark any IMP done or touch the board until the implementing steps actually land (step 9).
