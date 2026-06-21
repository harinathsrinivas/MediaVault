# Completion Report — IMP-E12 Web Operations Console (`feature/web_console`)

Built via the multi-agent pipeline (main session as orchestrator; executor / judge / git-agent sub-agents), 2026-06-21 → 2026-06-22. All 9 plan steps done; full suite + smoke green. PR to `main` pending (Checkpoint C1, human-gated).

## What shipped
`python main.py web [--port N] [--host H] [--no-browser]` → a local FastAPI **operations console** at `http://127.0.0.1:8765` (localhost-only). One merged **Disk Reclaim** view of reclaimable local files, each with a state badge (`UNPREPPED` / `LOCAL·NOT-PUSHED` / `PUSHED·NOT-ARCHIVED` / `RESTORED·REPLACE-AGAIN`), a total-reclaimable-GB header, filter chips, a deterministic suggested next command + suggested target folder, and one-click `prep`/`push`/`replace`/`sort` actions (confirm-modal-gated `replace`, polled jobs). Operations only — never plays video (Jellyfin keeps viewing), never moves/renames files (suggest-only).

- **`main.py`** — 5 pure read-only data-functions (`collect_reclaimable`, `classify_entry_state`, `guess_manual_id`, `suggest_target_folder`, `suggest_next_command`) + `cmd_web` (lazy fastapi/uvicorn import) + the `web` dispatch arm + usage line.
- **`webui/`** — `server.py` (`create_app()` + a serialized single-worker job queue) + `static/` (no-build card-grid SPA). The seed of the Tier-S daemon (IMP-S2).
- **Tests** — `tests/test_web_datafns.py` (36), `tests/test_web_endpoints.py` (5, TestClient), smoke wiring (`test_web_collect_reclaimable` + the anti-PR#21 `test_web_reclaim_alias`). `make_video` promoted to `tests/conftest.py`.
- **`requirements.txt`** — bundled IMP-A10 (fastapi, uvicorn[standard], requests, webdriver-manager + the reserved undetected-chromedriver comment) + httpx (TestClient test dep).

## The three multi-candidate bake-offs (Checkpoint C3 — human-gated each)
| Step | Decision | Judge pick | User pick | Why |
|---|---|---|---|---|
| 1 | reclaim-scan data model | **A** disk-first | **A** | gates UNPREPPED on real on-disk size; zero contract deviations |
| 3 | action-execution model | **C** single-worker queue | **C** | device-safety + stdout-isolation by construction; the daemon seed |
| 6 | frontend IA | A (table) | **B card grid** | user override — B is the substrate for the future Apple-like media UI (posters/titles/fetch) |

Each candidate is preserved as a `candidates/step-N/<letter>-{chosen,rejected}` tag; `DECISION.md` for each is committed under `.candidates/step-N/`.

## Notable engineering decisions
- **UNPREPPED is gated on actual on-disk size** (`>= DUMMY_MAX_BYTES`) inside `collect_reclaimable`, so sub-threshold stray files don't show phantom GB (step-1 A; pinned by a unit test).
- **Single-worker queue** makes the two mutating actions (push/replace) non-concurrent on the one ADB device and makes per-job stdout capture race-free by construction; the worker catches `SystemExit` first (corrupt-library `sys.exit(1)`).
- **Pre-PR safety fix:** the worker mapped a falsy `cmd_*` return → `error`, so a successful `cmd_sort()` (returns `None`) showed "error". Fixed via a per-action `_NONE_IS_SUCCESS = {"sort"}` convention — deliberately NOT a blanket "None→done", because `cmd_prep_push_rep` also returns `None` on failure (and swallows a post-PONR `RollbackHardFail`), so a blanket rule would have marked a failed destructive autopilot as "done". Also folded candidate C's auto-refresh-after-job + 409/404 handling into the card-grid frontend.

## Guardrails honored
- **`ENTRY_TYPE_KEYS` unchanged** — no new entry type / shared field; the new whole-library iterator skips `season_map`/`multi_ep_alias` (swept by smoke `TestAliasSweep`).
- **Auto-rollback change-gate NOT tripped** — `replace` reuses `cmd_replace` verbatim; no journal/PONR/`RollbackHardFail` surface touched.

## IMP bookkeeping (tier files + PRIORITY.md + priority-graph all agree)
IMP-E12 → done · IMP-A10 → done · IMP-D16 (`scan_reclaimable`) → added(done) · IMP-D1 → advanced (reclaimable-GB slice delivered; full dashboard still pending). 👉 NEXT repointed to IMP-A12 (CI).

## Verification
- `python -m pytest -q` → **286 passed**. `python -m pytest tests/smoke -q` → **58 passed** (< 30 s, the last gate).
- Manual: all three candidate UIs were launched live (ports 8771–8773) against the real read-only reclaim list (38 items / 160.84 GB) for the C3 visual choice.

## Follow-ups (logged on the board)
- Future "Apple-like" media UI on the card-grid substrate: posters/titles → IMP-D10/E3; fetch-in-UI + always-on service → IMP-S2.
- Flaky `test_push_real_split` (transient OSError under disk pressure) — stabilization candidate.
- Optional: thread A's richer `cmd_*` kwargs (`device_id`/`eager_rehash`/`temp_dir`/`parent_id`) + 422 validation into the server; `_JOBS` eviction before the daemon is long-lived.
