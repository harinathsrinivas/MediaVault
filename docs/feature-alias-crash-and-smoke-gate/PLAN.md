# Task: Fix the multi_ep_alias production crash (IMP-C12/C13) AND harden the agent workflow + testing strategy so a feature change can never silently break another command

Suggested branch: fix/alias_crash_and_smoke_gate

## Context
`python main.py scan_unprepped` crashes with `KeyError: 'folder_path'` at `main.py:2461` while scanning the Series library. The crash is a regression introduced by PR #21 / IMP-E13 (commit `988b491`, merged 2026-06-10), which added a new top-level library entry type `multi_ep_alias` (entries holding ONLY `type`, `alias_of`, `parent_id`). PR #21 taught the group push/replace/restore loops and `mainfetch.resolve_targets` to de-alias, but missed the whole-library-iterating and direct single-id commands. This exact bug is already tracked as **IMP-C12** (scan_unprepped/local_status crash) and **IMP-C13** (single-id commands crash on a secondary-episode id) — see `improvements/improvements_tierC.md` and `improvements/PRIORITY.md` Band 0 (IMP-C12 is the current 👉 SUGGESTED NEXT TASK). Part B addresses the user's larger ask: the real gap that let PR #21 ship a breaking change is *process* (no consumer-impact audit, no fast cross-command integration gate), not raw model capability.

## Goal
- **Part A (done-when):** Every command that iterates the merged library or dereferences `entry['folder_path']`/`entry['filename']` either de-aliases via `_resolve_alias` or skips both `season_map` AND `multi_ep_alias`. `scan_unprepped`, `local_status`, `check`, `push`, `replace`, `restore`, `verify_restore` all run clean on a library containing a `multi_ep_alias` entry. Regression tests prove it.
- **Part B (done-when):** A fast (`pytest tests/smoke -q` in well under ~30s, no real device/browser/`C:\Media`) full-command smoke suite exists and is the mandated pre-PR gate; an enforced consumer-impact guardrail exists (an entry-type schema registry + a static safety test that fails if a new whole-library iterator dereferences alias-absent keys, plus a planner-mandated Consumer Impact Analysis step); and `planner.md`, `orchestrator.md`, `executor-*.md`, `CLAUDE.md` are edited so every agent must (a) do cross-command impact analysis and (b) run the smoke suite before declaring a step/PR done.

## Confirmed root cause (verified against code + git this planning session)
- `cmd_scan_unprepped` loop `main.py:2459-2462`: `for entry in cat_lib.values(): if entry.get("type") == "season_map": continue; p = os.path.join(entry['folder_path'], entry['filename'])`. A `multi_ep_alias` entry has no `folder_path` → uncaught `KeyError`. ORIGINAL code (commit `ebf72db`); never updated by `988b491` (git-confirmed `988b491` diff does not touch `cmd_scan_unprepped`).
- `multi_ep_alias` is created at `main.py:1075-1079` inside `cmd_prep_season`. Schema (ARCHITECTURE §6.3): `{ "type": "multi_ep_alias", "alias_of": <primary_id>, "parent_id": <base_id> }` — no `folder_path`, `filename`, `hash`, `tech_spec`, `split_info`, `status`, `uploaded`.
- `_resolve_alias(lib, mid)` lives at `main.py:1612` (mirror at `mainfetch.py:333`); the de-alias loops PR #21 added are at `main.py:1670` (push_group), `1888` (replace_group), `2277` (restore_group), `2588` (prep_push_rep_season).

## CONSUMER AUDIT (the real fix — every site that iterates the merged library or dereferences entry keys)
Verdicts verified by reading the code this session. "BROKEN" = crashes or misbehaves on a `multi_ep_alias` entry today.

| # | Site | Line(s) | Access | Verdict | Why |
|---|------|--------|--------|---------|-----|
| 1 | `cmd_scan_unprepped` | 2459-2462 | `entry['folder_path']`/`['filename']` after skipping only `season_map` | **BROKEN (the reported crash; IMP-C12)** | alias lacks `folder_path` → KeyError |
| 2 | `cmd_local_status` | 2358-2369, 2415 | counts non-`season_map` as pending; renders `item['filename'][:40]` | **BROKEN (IMP-C12)** | alias has no `uploaded` → counted pending; `filename=None` → `None[:40]` TypeError |
| 3 | `cmd_check` | 1090-1096 | `entry['folder_path']`/`['filename']`, no `_resolve_alias` | **BROKEN on direct alias id (IMP-C13)** | `check tv-…e20` → KeyError |
| 4 | `cmd_push` | 1217-1228 | `entry['folder_path']`/`['filename']` at lookup head | **BROKEN on direct alias id (IMP-C13)** | `push tv-…e20` → KeyError |
| 5 | `cmd_replace` | 1741-1752 | `entry['folder_path']`/`['filename']`; returns False silently on unknown id | **BROKEN on direct alias id (IMP-C13)** | `replace tv-…e20` → KeyError |
| 6 | `cmd_restore` | 2032-2041 | `entry['folder_path']`/`['filename']` at lookup head | **BROKEN on direct alias id (IMP-C13)** | `restore tv-…e20` → KeyError |
| 7 | `cmd_verify_restore` | ~1963-2003 | `entry['folder_path']`/`entry['filename']` | **BROKEN on direct alias id (IMP-C13)** | same pattern as restore |
| 8 | `cmd_fetch_restore` (single branch) | 2748-2756 | checks `season_map` else calls `cmd_restore(manual_id)` | **BROKEN via cmd_restore on direct alias id (IMP-C13)** | fetch resolves fine, then restore crashes — the exact inconsistency IMP-C13 calls out |
| 9 | `cmd_repair_dummies` | 1912-1921 | skips `season_map`, then `entry.get("status") != "archived"` continue, then `folder_path` | **SAFE** | alias has no `status` → skipped before any `folder_path` access |
| 10 | `cmd_sort` `sort_key` | 2312-2338 | parses key string; `entry.get('tech_spec',{}).get('size_bytes',0)` | **SAFE** | all entry access via `.get()` chains; alias sorts with size 0, no deref |
| 11 | `cmd_push_group` de-alias loop | 1670-1674 | `_resolve_alias` then dedup | **SAFE** | PR #21 |
| 12 | `cmd_replace_group` de-alias loop | 1888-1892 | `_resolve_alias` | **SAFE** | PR #21 |
| 13 | `cmd_restore_group` de-alias loop | 2277-2281 | `_resolve_alias` | **SAFE** | PR #21 |
| 14 | `cmd_prep_push_rep_season` de-alias loop | 2588-2609 | `_resolve_alias` | **SAFE** | PR #21; later `entry['folder_path']` at 2636/2692 operates on resolved real ids |
| 15 | `write_remote_mvmeta` | 1114-1168 | `entry.get(...)` everywhere | **SAFE** | called only on a resolved primary during push; all `.get()` |
| 16 | `mainfetch.resolve_targets` / single-id | 333-400, 449 | `_resolve_alias` (line 392 resolves the single-id path too) | **SAFE** | PR #21 mirror |

**Net broken set to fix:** #1, #2 (IMP-C12) and #3, #4, #5, #6, #7, #8-via-#6 (IMP-C13). Everything else is already safe — confirmed, not assumed.

**Two fix strategies, applied deliberately:**
- Whole-library iterators (#1, #2): **skip** `multi_ep_alias` (mirror the existing `season_map` skip) — these commands report on physical files; an alias has no physical file of its own. This matches IMP-C12's proposed change exactly.
- Direct single-id commands (#3-#8): **de-alias** at the lookup head via `_resolve_alias` and print one info line, so the user can type any episode id of a combined file. This matches IMP-C13's proposed change exactly. `cmd_prep` must additionally refuse to prep OVER an existing alias id (it would overwrite the alias with a leaf entry and corrupt the chain — IMP-C13 calls this out).

## Files affected
- `main.py` — fix #1/#2 (skip alias) and #3-#8 (de-alias at lookup head); add `cmd_prep` refuse-over-alias guard; add the entry-type schema registry constant (Part B guardrail). (executor: opus for the cross-command lookup-head edits; the skips are mechanical.)
- `tests/smoke/__init__.py`, `tests/smoke/conftest.py`, `tests/smoke/test_smoke_all_commands.py` — NEW fast full-command smoke package (Part B-1).
- `tests/conftest.py` — add a `multi_ep_alias`-bearing library fixture (`sandbox_alias`) reused by both the regression tests and the smoke suite. (executor: opus — conftest binding hazard.)
- `tests/test_alias_consumers.py` — NEW regression tests for #1-#8 (Part A-3).
- `tests/test_entry_schema_guard.py` — NEW static safety test asserting the entry-type registry stays in sync and no whole-library iterator dereferences alias-absent keys (Part B-2).
- `.claude/agents/planner.md` — add a mandated "Consumer Impact Analysis" planning step + a smoke-gate requirement (body only; the uncommitted `M` is frontmatter-only, no conflict).
- `.claude/agents/orchestrator.md` — require the smoke suite green before COMMIT_STEP and before PUSH_BRANCH/CREATE_PR.
- `.claude/agents/executor-sonnet.md`, `.claude/agents/executor-opus.md`, `.claude/agents/executor-haiku.md` — require running the smoke suite when the step touches `main.py`/`mainfetch.py`/`mvcommon.py` and respecting the entry-type registry.
- `CLAUDE.md` — add a short "Cross-command integrity + smoke gate" subsection (load-bearing, loads into every agent).
- `docs/testing-strategy.md` — document the smoke suite layer + `sandbox_alias` fixture + the entry-type registry.
- `improvements/improvements_tierC.md` (mark C12/C13 in_progress→done), `improvements/improvements_tierH.md` (add the new workflow-hardening IMP — see Open Decision 4), `improvements/PRIORITY.md`, `docs/priority-graph/priority-graph.html` — backlog bookkeeping (per CLAUDE.md, update all three together).
- `ARCHITECTURE.md`, `README.md` — module-layout note for the new `tests/smoke/` package (architect updates during implementation — see Implementation note).

## Approach
Land Part A first as a tight, well-tested fix (so the production crash is resolved even if Part B scope is trimmed), then build the Part B prevention layer on top, then wire the agents to enforce it, then bookkeeping. The smoke suite reuses the EXISTING `mock_device`/`mock_fetch`/`sandbox` infra (no new mocking philosophy) and drives each `cmd_*` against a tiny in-repo fixture so a single executor finally OWNS cross-command integrity end-to-end. The entry-type registry (`ENTRY_TYPE_KEYS` in `main.py`) plus its guard test turn "audit every consumer when a data shape changes" from a memory note into an automated check.

## Part A — the fix

- [x] A1. [model: opus] [effort: high] Fix the two whole-library iterators (IMP-C12) and de-alias the direct single-id commands (IMP-C13) in one coherent edit.
  - Files: `main.py`
  - Details:
    - **Iterator skips (#1, #2):** in `cmd_scan_unprepped` (loop at 2459) and `cmd_local_status` (loop at 2358), change the existing `if entry.get("type") == "season_map": continue` to also skip `multi_ep_alias`. Recommended robust form: `if entry.get("type") in ("season_map", "multi_ep_alias"): continue`. (Equivalently a belt-and-suspenders `or "folder_path" not in entry` — pick the explicit type check to match the codebase's existing style; note the choice in STATUS.md.)
    - **Single-id de-alias (#3-#8):** at the entry-lookup head of `cmd_check`, `cmd_push`, `cmd_replace`, `cmd_restore`, `cmd_verify_restore`, immediately after confirming `manual_id in library`, call `real_id, entry = _resolve_alias(library, manual_id)`. If `real_id != manual_id`, print exactly one info line: `print(f"ℹ️  {manual_id} is part of the combined file registered as {real_id} — operating on that.")` and use `entry`/`real_id` thereafter. For commands that re-load or re-key later (e.g. `cmd_push` saves under the id), operate on `real_id` consistently. `cmd_fetch_restore` (#8) needs no direct change — it delegates to `cmd_restore`, which now resolves. Preserve byte-identical behavior for non-alias ids (the `real_id == manual_id` path must be unchanged).
    - **`cmd_prep` refuse-over-alias guard:** in `cmd_prep`, if `manual_id` already exists in the library with `type == "multi_ep_alias"`, print an error (`❌ {manual_id} is a combined-episode alias of {alias_of}; prep the primary instead.`) and `return False` BEFORE writing anything. Do NOT overwrite the alias.
    - This step touches the lookup head of six commands and is cross-cutting → opus. Behavior for normal ids must stay identical; guard every change with the A3 tests.
    - **Rollback change-gate:** these edits are at command lookup heads and read-only iterators; they do NOT touch `RollbackJournal`, `recover_journal`, PONR markers, journal format, or `mark_point_of_no_return()`. No rollback behavior changes. (If, while editing `cmd_push`/`cmd_replace`/`cmd_restore`, the executor finds the de-alias would move the PONR or alter what the journal records, STOP and surface as a decision per CLAUDE.md — it should not.)
  - Acceptance: `python main.py scan_unprepped` and `python main.py local_status` run clean against a library containing an alias (proven by A3 tests, not real data); `check/push/replace/restore/verify_restore <secondary-ep-id>` operate on the primary with one info line; `prep <existing-alias-id> <path>` refuses; non-alias paths unchanged. `pytest -q` green.

- [x] A2. [model: opus] [effort: medium] Add the `sandbox_alias` fixture (a sandbox library seeded with a season_map + primary leaf + multi_ep_alias) to `tests/conftest.py`.
  - Files: `tests/conftest.py`
  - Details: Extend the existing `sandbox` fixture (do NOT DIY the `LIBRARY_*` patching — `sandbox` already patches BOTH `mvcommon.LIBRARY_*` AND `main.LIBRARY_*`; this is THE binding hazard). Seed `library_series.json` with: a `season_map` parent `tv-en-2009-bsg-s04` (children = `[…s04e19, …s04e20]`), a leaf primary `tv-en-2009-bsg-s04e19` (real `folder_path` under the sandbox media dir, a real ~tiny `.mkv` on disk, `status:"local_ready"`, `uploaded:False`), and a `multi_ep_alias` `tv-en-2009-bsg-s04e20` = `{type:"multi_ep_alias", alias_of:"tv-en-2009-bsg-s04e19", parent_id:"tv-en-2009-bsg-s04"}`. Yield a dict with `primary_id`, `alias_id`, `season_id`, `media_dir`, `orig_path`. Never touch real `C:\Media` files or real `library_*.json`. Conftest change with binding hazard → opus. Run `pytest -q` and fix failures before marking the step done.
  - Acceptance: `mvcommon.load_library()` under the fixture returns the three entries with the alias intact; existing suite still green (`pytest -q`).

- [x] A3. [model: sonnet] [effort: medium] Regression tests for every fixed consumer (#1-#8).
  - Files: `tests/test_alias_consumers.py`
  - Details: Use `sandbox_alias` (+ `mock_device`/`mock_fetch`/`fake_dummy` where a command needs the device/browser/ffmpeg boundary). Assert:
    - `cmd_scan_unprepped()` runs without raising and does not list the alias as unprepped (capture stdout via `capsys`).
    - `cmd_local_status()` runs without raising and the alias never appears as a pending row (no `None` filename).
    - `cmd_check(alias_id)`, `cmd_push(alias_id, …)`, `cmd_replace(alias_id)`, `cmd_restore(alias_id)`, `cmd_verify_restore(alias_id)` each operate on `primary_id` (assert via the info line in stdout and/or the side effect landing on the primary's file), never raise `KeyError`.
    - `cmd_prep(alias_id, <some path>)` returns False and leaves the alias entry unchanged.
    - A control assertion that calling each command with the PRIMARY id behaves exactly as before (no behavior drift for non-alias ids).
    - Follow `docs/testing-strategy.md`: ADB data-integrity → `mock_device`; fetch → `mock_fetch`; never assert absolute device paths (use `rglob("*.mkv")` + `.name`); never touch real `C:\Media` or real `library_*.json`. Run `pytest -q` and fix failures before marking the step done; paste output in STATUS.md.
  - Acceptance: new tests pass; full `pytest -q` green.

## Part B — workflow + testing hardening

- [x] B1. [model: opus] [effort: high] Build the fast full-command smoke suite (`tests/smoke/`) — the mandated pre-PR gate.
  - Files: `tests/smoke/__init__.py`, `tests/smoke/conftest.py`, `tests/smoke/test_smoke_all_commands.py`
  - Details:
    - **Goal:** one fast package that drives EVERY user-facing command + its major options against a tiny fixture and the existing stub device/browser, asserting "no crash + correct top-level effect". This is the integration gate a single owner runs; it is NOT a replacement for the focused unit/command tests.
    - **Fixtures (reuse, do not reinvent):** import/extend `sandbox`, `sandbox_alias`, `mock_device`, `mock_fetch`, `fake_dummy` from `tests/conftest.py`. The video fixture is a **<100KB real `.mkv`** generated once by ffmpeg if available (mirror `ffmpeg_multichunk_mkv`) and otherwise a tiny byte-stub for the commands that don't need a real container; gate the real-mkvmerge/real-ffmpeg cases with the existing `_ffmpeg_available()`/`_mkvmerge_available()` skips so the suite stays green on machines without the binaries. Device/ADB is stubbed by `mock_device` (stateful) and protocol-only paths by `FakeAdb`; fetch by `mock_fetch` (no Selenium, no real browser); library by `sandbox` (no real `C:\Media`).
    - **Directory layout:** new `tests/smoke/` package so the gate can be run/excluded as a unit (`pytest tests/smoke -q`). `tests/smoke/conftest.py` may re-export root fixtures via `pytest_plugins`/imports.
    - **Coverage matrix (one fast test per command, parametrized where natural):** `prep`, `prep_season`, `prep_push_rep`, `prep_push_rep_season` (incl. `episodes <range>`), `scan_unprepped`, `local_status` (incl. a size limit), `check`, `push` (single + split via SIZE_MB + `chunks 1-N` range + `rehash` eager + `tempdir`), `push_group` (+ `episodes`), `replace`, `replace_group`, `repair_dummies`, `verify_restore`, `restore`, `restore_group`, `fetch` (via `cmd_dispatch_fetch`/`mock_fetch`), `fetch_restore`, `sort`, `recover`/`recover --scan`, `set_search`/`set_poster`/`set_fanart`/`set_uploaded`. **Crucially, every command runs at least once against the `sandbox_alias` library** so the presence of a `multi_ep_alias` entry is exercised across the whole surface — that single assertion is what would have caught PR #21.
    - **Speed budget:** target the whole package well under ~30s on a dev box; prefer pre-seeded `_parts/` over real splits except in the one real-split smoke case; keep fixture videos tiny. Document the measured wall-time in STATUS.md.
    - **Invocation:** documented in `docs/testing-strategy.md` and required by the agents (B5/B6). Suggested CLI: `pytest tests/smoke -q`.
    - Cross-cutting integration design + conftest binding hazard → opus. Never touch real `C:\Media` files or real `library_*.json`. Run `pytest -q` (and `pytest tests/smoke -q`) and fix failures before marking the step done.
  - Acceptance: `pytest tests/smoke -q` passes and exercises every command in the matrix at least once, including against `sandbox_alias`; total runtime recorded and within budget; `pytest -q` (whole suite) green.

- [x] B1a. [model: opus] [effort: medium] Harden the `sandbox` fixture: redirect `LOCAL_ROOT` so no test can write to real `C:\Media` (added 2026-06-13 per user request; discovered during B1 — the `sandbox` fixture redirects `LIBRARY_*` but not `LOCAL_ROOT`, so whole-tree walkers read real `C:\Media`).
  - Files: `tests/conftest.py` (+ reconcile `tests/smoke/conftest.py`'s `smoke_local_root`).
  - Details: Extend the existing `sandbox` fixture to ALSO patch `mvcommon.LOCAL_ROOT` AND `main.LOCAL_ROOT` (binding hazard — patch both) to a media root INSIDE the sandbox temp dir, so the whole-tree walkers (`cmd_scan_unprepped`, `cmd_recover --scan`) and any `LOCAL_ROOT`-derived write land in the sandbox, never real `C:\Media`. Point `LOCAL_ROOT` at the base under which the fixtures create media (so scan tests stay meaningful, walking the fixture files). Extend the hard-guard assertion to also assert `LOCAL_ROOT` is not the real path. Reconcile the now-redundant `smoke_local_root` patch in `tests/smoke/conftest.py` (remove or no-op it). USER CONSTRAINT: read-only access to real `C:\Media` is acceptable, but NO writes/modifications — this makes that guarantee structural. Conftest binding hazard → opus.
  - Acceptance: full `pytest -q` green (158 passed, 3 skipped, or adjusted-but-all-green) and `pytest tests/smoke -q` green; A3's `test_scan_unprepped_skips_alias_no_crash` and `cmd_recover --scan` no longer touch real `C:\Media`.

- [x] B1b. [model: opus] [effort: medium] Reuse production `main.resolve_ffmpeg()` in the test fixtures; remove the divergent PATH-only ffmpeg skip logic so the real-binary tests actually run (added 2026-06-13 per user request).
  - Files: `tests/conftest.py` (`_ffmpeg_available` + the `ffmpeg_multichunk_mkv` / `mkvmerge_split_chunks` subprocess calls), `tests/smoke/test_smoke_all_commands.py` (the `test_push_real_split` skipif), and any other test with its own ffmpeg detection (sweep `tests/` for `shutil.which("ffmpeg")` / hardcoded `"ffmpeg"`).
  - Rationale: production resolves ffmpeg via `main.resolve_ffmpeg()` → configured `FFMPEG_PATH` (Emby's bundled `…\Emby-Server\system\ffmpeg.exe`, present on this box) then PATH. The tests only did `shutil.which("ffmpeg")` (PATH-only), so they skipped the 3 real-binary tests even though the ffmpeg production uses is available. mkvmerge is also present (configured `MKVMERGE_PATH`).
  - Details: (1) `_ffmpeg_available()` → `return main.resolve_ffmpeg() is not None`. (2) `ffmpeg_multichunk_mkv` and `mkvmerge_split_chunks` must invoke the RESOLVED binary (`main.resolve_ffmpeg()`), not bare `"ffmpeg"`. (3) Remove duplicate/divergent ffmpeg-availability detection elsewhere in `tests/`. (4) Keep a SINGLE genuine-absence fallback: skip cleanly only when `resolve_ffmpeg()` is None (and `_mkvmerge_available()` False where relevant) — do NOT introduce a hard collection/import failure on a machine with no ffmpeg. The 3 previously-skipped tests (`test_rehash.py::test_deterministic_merge_same_seed_yields_identical_hash`, `test_rollback.py::test_push_split_fail_before_upload_rolls_back`, `tests/smoke::test_push_real_split`) must now RUN and PASS on this machine.
  - Acceptance: `pytest -q` → the 3 ffmpeg tests RUN (no longer skipped) and pass; full suite green with skip count dropping (was 3 → expected 0 on this box, since Emby ffmpeg + mkvmerge are present); `pytest tests/smoke -q` green.

- [x] B2. [model: opus] [effort: high] Entry-type schema registry + static consumer-safety guard test (the enforced data-shape guardrail).
  - Files: `main.py` (add `ENTRY_TYPE_KEYS` registry constant near the schema/config block), `tests/test_entry_schema_guard.py`
  - Details:
    - In `main.py`, add a small, documented registry mapping each entry type to its required + optional keys and a "is this a physical-file entry?" flag, e.g.:
      `ENTRY_TYPE_KEYS = { "leaf": {"required": {"folder_path","filename","status"}, "physical": True}, "season_map": {"required": {"folder_path","children"}, "physical": False}, "multi_ep_alias": {"required": {"alias_of","parent_id"}, "physical": False} }`
      (Leaf is the implicit no-`type` entry; treat a missing `type` as `"leaf"`.) This is the single documented source of truth for "what keys does each entry type have" and the seam future features extend.
    - In `tests/test_entry_schema_guard.py`, add: (a) a test that every type in `ENTRY_TYPE_KEYS` round-trips through `save_library`/`load_library` under `sandbox` without loss; (b) a **guard test** that builds a `sandbox` library containing one entry of EACH non-physical type (`season_map`, `multi_ep_alias`) and asserts the "library-status" read commands (`cmd_scan_unprepped`, `cmd_local_status`, `cmd_sort`) complete without raising — i.e. any new whole-library iterator that blindly dereferences a physical-only key fails this test. Document at the top of the file: "When you add or change an entry type, update `ENTRY_TYPE_KEYS` AND add it to this guard's non-physical set; every whole-library iterator must tolerate every non-physical type."
    - This is the lightest enforced mechanism that actually catches the PR #21 class: a single test that fails the moment a new iterator (or a new entry type) breaks the read commands. Heavier AST/static-analysis of every `entry[...]` access path is explicitly NOT done (see Out of scope) — it is brittle and high-effort for marginal gain over the guard test + smoke suite.
    - Registry design + guard semantics are cross-cutting and must be right long-term → opus. Never touch real `C:\Media` files or real `library_*.json`. Run `pytest -q` and fix failures before marking the step done.
  - Acceptance: `ENTRY_TYPE_KEYS` documented in `main.py`; guard test passes today and is demonstrated (in STATUS.md) to FAIL if `cmd_scan_unprepped`'s alias skip is temporarily reverted; `pytest -q` green.

- [x] B3. [model: opus] [effort: high] Planner agent: mandate a Consumer Impact Analysis step + smoke gate.
  - Files: `.claude/agents/planner.md`
  - Details: Edit the BODY only (the uncommitted `M` is frontmatter — model/effort/tools — so there is no body conflict; leave the frontmatter as-is). Add two things:
    1. A new mandatory rule in the WORKFLOW + a required PLAN.md sub-section: **"Consumer Impact Analysis (REQUIRED when a step adds/changes/removes an entry type, a library field, an ID shape, a status value, or any shared data contract)."** It must instruct the planner to (a) grep every consumer of the changed shape (`.values()`/`.items()` iterators, `entry['<key>']`/`.get('<key>')` derefs, `_resolve_alias` callers) and enumerate each with a safe/needs-fix verdict + line number IN THE PLAN (exactly like this plan's CONSUMER AUDIT table), and (b) consult `ENTRY_TYPE_KEYS` in `main.py` as the source of truth for entry shapes. Reference IMP-E13/PR #21 as the cautionary example.
    2. A standing instruction that any plan whose steps touch `main.py`/`mainfetch.py`/`mvcommon.py` MUST include a final verification line `pytest tests/smoke -q` (the full-command gate) in addition to `pytest -q`.
    Cross-cutting process change that must be precise and durable → opus.
  - Acceptance: `planner.md` body contains the Consumer Impact Analysis requirement and the smoke-gate verification mandate; frontmatter unchanged; the rule references the registry and PR #21.

- [x] B4. [model: opus] [effort: high] Orchestrator agent: require smoke suite green before commit and before PR.
  - Files: `.claude/agents/orchestrator.md`
  - Details: In Phase 2A/2B step-completion (before invoking git-agent COMMIT_STEP) add: "If the step modified `main.py`/`mainfetch.py`/`mvcommon.py`, run `pytest tests/smoke -q`; if it is red, do NOT commit — STOP and report (treat like a failed acceptance check)." In Phase 3 (Finalize) add `pytest tests/smoke -q` to the Verification gate that must pass before PUSH_BRANCH/CREATE_PR, alongside the existing `pytest -q`. Keep the existing NO-SILENT-HANDLING and human-gated-merge rules intact. Cross-cutting → opus.
  - Acceptance: `orchestrator.md` requires the smoke gate at both the per-step (code-touching) commit point and the pre-PR finalize point.

- [x] B5. [model: sonnet] [effort: medium] Executor agents: run the smoke suite on code-touching steps; respect the registry.
  - Files: `.claude/agents/executor-sonnet.md`, `.claude/agents/executor-opus.md`, `.claude/agents/executor-haiku.md`
  - Details: Add to each executor's workflow: "If your step modified `main.py`/`mainfetch.py`/`mvcommon.py`, run `pytest tests/smoke -q` in addition to the step's own acceptance check, and fix failures before marking the step `[x]`; paste the smoke result into STATUS.md Verification." Add to the WHEN-WRITING/EDITING-CODE guidance: "If you add or change a library entry type or a shared entry field, update `ENTRY_TYPE_KEYS` in `main.py` and ensure every whole-library iterator skips/resolves the new type." This is a well-understood, parallel edit across three files following an existing doc pattern → sonnet (no genuinely-different approaches; not a candidate step).
  - Acceptance: all three executor docs carry the smoke-gate + registry instruction.

- [x] B6. [model: sonnet] [effort: medium] CLAUDE.md + testing-strategy.md: document the cross-command integrity gate, smoke suite, `sandbox_alias`, and `ENTRY_TYPE_KEYS`.
  - Files: `CLAUDE.md`, `docs/testing-strategy.md`
  - Details: In `CLAUDE.md` add a short subsection "Cross-command integrity + smoke gate" stating the working principle ("a change to one feature must never break another command") and the two enforced mechanisms (the `tests/smoke` gate run before any PR; the `ENTRY_TYPE_KEYS` registry + guard test that every entry-type/data-shape change must update). In `docs/testing-strategy.md` add: a "Smoke suite" entry to the pyramid (a new INTEGRATION-tier row), the `sandbox_alias` fixture in the fixture catalogue (§4), the `ENTRY_TYPE_KEYS` registry note, and the `pytest tests/smoke -q` invocation in §12. Doc-writing following the file's existing structure → sonnet.
  - Acceptance: both docs updated; `docs/testing-strategy.md` lists the smoke suite, `sandbox_alias`, the registry, and the run command.

- [x] B7. [model: opus] [effort: high] Out-of-band data-request protocol — centralize web/doc access to planner + orchestrator + architect; executors must pause and ask the orchestrator. **(per user Decision 3.)**
  - Files: `.claude/agents/orchestrator.md`, `.claude/agents/planner.md`, `.claude/agents/architect.md`, `.claude/agents/executor-sonnet.md`, `.claude/agents/executor-opus.md`, `.claude/agents/executor-haiku.md`, and a short subsection in `CLAUDE.md`.
  - Rationale (user-decided): only **planner, orchestrator, and architect** may hold `WebSearch`/`WebFetch`. Executors stay deterministic and side-effect-bounded (no web). When a web-less sub-agent genuinely needs external/library/doc data mid-task, it must NOT guess or fabricate — it pauses and routes the request through the orchestrator, which performs the fetch and hands back the result in the requested shape. This keeps the network/research surface to three trusted roles while no executor is ever blocked for lack of a fact.
  - Details — specify this EXACT protocol in the agent files:
    1. **Tool grants (frontmatter):** confirm `executor-*.md` frontmatter does NOT include `WebSearch`/`WebFetch`; ensure `.claude/agents/architect.md` frontmatter DOES (add if missing); leave `planner.md` (already has them) and `orchestrator.md` (Task + the web tools needed to service requests — add `WebSearch`/`WebFetch` to its frontmatter if absent) as web-capable.
    2. **Executor side (`executor-*.md`):** add a "Need external data? Do NOT browse — raise a DATA_REQUEST" rule. The executor STOPS at a clean point and returns to the orchestrator a fenced ```DATA_REQUEST``` block with fields: `step` (id), `purpose` (why it's needed to finish the step), `query_or_url` (exact search string or URL), `fields_needed` (the specific facts wanted), `return_format` (the exact shape wanted back — e.g. "the stable version string", "the function signature", "a JSON object {…}"), `blocking` (true = cannot proceed without it). It must mark the step in-progress (not failed, not done) and not invent data.
    3. **Orchestrator side (`orchestrator.md`):** add an "Out-of-band data requests" handler to the dispatch loop — if a dispatched sub-agent returns a `DATA_REQUEST`, the orchestrator performs the `WebSearch`/`WebFetch` itself, distills the answer to exactly `return_format`, records the request+response in STATUS.md, then RE-DISPATCHES the same executor for the same step supplying a fenced ```DATA_RESPONSE``` block (echoing the `step` + `fields_needed` and the formatted data) so the executor resumes with the data in hand. The orchestrator never delegates the fetch back to the executor.
    4. **Planner side (`planner.md`):** instruct the planner to PRE-RESOLVE external facts during planning (it has web) and bake them into the step text so executors rarely need to pause; when a step is still likely to need a lookup, the planner tags it `may require a DATA_REQUEST: <what>` so the orchestrator expects it.
    5. **`CLAUDE.md`:** add a 3-4 line "Out-of-band data requests" note stating the rule (executors don't browse; they raise DATA_REQUEST; orchestrator fetches and returns DATA_RESPONSE; web tools live only on planner/orchestrator/architect) so it loads into every session/sub-agent.
  - Cross-cutting protocol that must be precise and consistent across six files → opus.
  - Acceptance: executor docs contain the DATA_REQUEST rule and have no web tools; orchestrator doc contains the DATA_REQUEST→fetch→DATA_RESPONSE→re-dispatch handler and has the web tools; architect has web tools; planner pre-resolve + tagging rule present; CLAUDE.md note added.

## Part B — assessment of the user's hypotheses (recorded here per the task; carried verbatim into the PR body)
- **(a) "Is the model not good enough / how can this happen with good plans + orchestrators + tests?"** Honest assessment: this was **not** primarily a model-capability failure. PR #21 introduced a new top-level entry type and correctly updated the consumers it was thinking about (group push/replace/restore + fetch). The miss was a **process gap**: nothing forced an exhaustive *consumer-impact audit* of the new data shape, and there was no fast *cross-command integration gate* that runs every command against a library containing the new shape. A strong model with no such gate will still miss a distant consumer it wasn't looking at — the repo's own memory even predicted this bug class, which proves a *note* is not enforcement. The fix is the audit step (B3) + the registry guard (B2) + the smoke gate (B1/B4/B5), which catch the failure regardless of which model wrote the change.
- **(b) "Is too much task breakdown causing this?"** Partly yes, indirectly. Over-fragmentation means **no single executor owns cross-command integrity** — each executor sees only its step and its local tests, and the planner's decomposition can omit the "audit all consumers" work entirely because it isn't any one step's job. The remedy is structural, not "fewer steps": a **plan-level Consumer Impact Analysis step** (B3) makes the cross-command audit an explicit owned step, and the **smoke gate** (B1) makes "did this break another command?" a single check every executor and the orchestrator must pass before commit/PR. With those two, the integrity guarantee holds at any granularity of breakdown.

## Tests
- Part A regression: `tests/test_alias_consumers.py` (A3) — proves #1-#8 fixed and non-alias behavior unchanged, using `sandbox_alias` + the existing device/fetch/ffmpeg stubs.
- Part B guard: `tests/test_entry_schema_guard.py` (B2) — registry round-trip + the iterator-safety guard (demonstrated to fail if the alias skip is reverted).
- Part B smoke: `tests/smoke/test_smoke_all_commands.py` (B1) — every command exercised against the tiny fixture and `sandbox_alias`.
- Constraints honored in every test step: never touch real `C:\Media` files or real `library_*.json`; never issue real `adb`/open a real browser; `pytest -q` must be green before each test step is marked done.

## Verification
Run after all steps complete:
```
pytest -q                     # full suite must be green
pytest tests/smoke -q         # the new full-command gate must be green and fast
pytest tests/test_alias_consumers.py -q
pytest tests/test_entry_schema_guard.py -q
```
Manual sanity (read-only, safe on real data — these are the two reported-broken commands):
```
python main.py scan_unprepped     # must NOT crash on the Series library
python main.py local_status       # must NOT crash; no phantom alias rows
```

## Risks and edge cases
- **`cmd_push`/`cmd_replace`/`cmd_restore` rekeying after de-alias:** ensure that once resolved to `real_id`, library saves and journal records use `real_id`, not the alias id, so state lands on the primary. Tests in A3 must assert the side effect lands on the primary's entry/file.
- **Rollback change-gate (HARD STOP if violated):** the de-alias edits sit at the lookup heads of three rollback-wrapped commands. They must NOT move a PONR, change `mark_point_of_no_return()` placement, alter the journal format/durability, or change what `recover_journal` does. The plan asserts they don't; if the executor finds otherwise, STOP and surface a decision per CLAUDE.md — do not silently modify rollback behavior.
- **Smoke-suite flakiness / machine variance:** real-binary cases (ffmpeg/mkvmerge) must use the existing skip gates so the suite is green on boxes without them; the alias-exercise assertions must NOT depend on a real binary.
- **Speed regression of the gate:** if the smoke suite creeps over budget it stops being run; B1 must keep fixtures tiny and prefer pre-seeded `_parts/` over real splits. Record the wall-time.
- **Registry drift:** `ENTRY_TYPE_KEYS` only helps if kept current; the guard test (B2) is what forces that, and the agent edits (B3/B5) point at it.
- **Double-counting IMP codes:** this PR resolves IMP-C12 and IMP-C13; the audit also confirms IMP-C14's silent-`replace` and `mainfetch` argv issues are adjacent but OUT OF SCOPE here (don't fold them in silently) — note them as still-pending in PRIORITY.md.

## Out of scope
- IMP-C14 (push_group trailing-keyword hang, mainfetch bare-invoke IndexError, silent `replace` on unknown id) and IMP-C15 (repair_dummies atomic swap, `_verify_chunk_hash` empty-stdout guard) — separate tracked bugs; do not bundle.
- A full AST/static-analysis pass over every `entry[...]` access (heavier than the guard test + smoke gate warrant; rejected in favor of B2).
- A CI pipeline wiring the suite into GitHub Actions (that is IMP-A12; the smoke gate here is the local pre-PR gate IMP-A12 will later run in CI).
- An always-on monitor/watcher agent (Decision 2 = NO; CI is IMP-A12).
- Giving executors web tools or "enable all tools for all agents" (Decision 3 = NO — executors stay web-less and instead raise a DATA_REQUEST handled by the orchestrator; see B7). Web tools remain limited to planner/orchestrator/architect.
- Any change to the auto-rollback mechanism (change-gated; explicitly avoided).

## Open Decisions — RESOLVED (answered by the user via AskUserQuestion in the main session, 2026-06-13)
> The four scope decisions were put to the user with recommendations; their answers are recorded below and are now baked into the plan above. Decision 5 (IMP code) was not separately surfaced — the Recommended option stands as the default (overridable).

1. **Scope of this PR.** ✅ DECIDED: **A + B in one PR** (Recommended). Fix (Part A) plus the smoke suite, guardrail, agent-instruction edits, and the data-request protocol (Part B) ship as one coherent change. The prevention layer is small/additive and is what makes the fix stick.

2. **Always-on monitor/watcher agent.** ✅ DECIDED: **NO standing watcher** (Recommended). The smoke suite is a mandatory gate at step-commit and pre-PR (B4); real CI is adopted later via IMP-A12. No continuously-running agent.

3. **Agent tooling — web access.** ✅ DECIDED with a refinement (user's words): keep web tools on **planner only among the original two**, but ALSO allow **architect**, and — critically — add an **out-of-band data-request protocol**: executors get NO web tools; when a sub-agent needs external/doc/web data it must **pause the step, send the orchestrator a structured request stating exactly what data is needed and in what format, and the orchestrator performs the fetch and returns the data in that format**, after which the sub-agent resumes. Net: web tools live ONLY on **planner, orchestrator, architect**; executors stay minimal. This is specified as new step **B7** and is reflected in the agent-file edits (B3/B4/B5/B7) and CLAUDE.md.

4. **Enforcement strength of the consumer-impact guardrail.** ✅ DECIDED: **Both** (Recommended) — the registry + guard test (B2) AND the planner-mandated Consumer Impact Analysis step (B3). Full AST/static analysis remains rejected (Out of scope).

5. **IMP code for the Part B workflow-hardening work** (not separately surfaced; Recommended default stands).
   - PR title cites the fix codes (`… — IMP-C12, IMP-C13`); create a new Tier H task (`IMP-H3: cross-command smoke gate + consumer-impact guardrail + agent enforcement + data-request protocol`) to track Part B, referenced in the body. Tier H ("Agentic dev workflow") is the natural home (H1 done, H2 research-only, so H3 is free). Override if you'd rather fold Part B under C12/C13 with no new code.

## Branch name
`fix/alias_crash_and_smoke_gate`

## PR-to-main plan (per `docs/git-pr-conventions.md` + `CLAUDE.md`)
- **Title:** include the IMP code(s). Proposed: `fix: multi_ep_alias crash in scan_unprepped/single-id commands + cross-command smoke gate — IMP-C12, IMP-C13` (and reference the new `IMP-H3` in the body if Open Decision 5 = Recommended). The fix maps to existing tracked codes IMP-C12 (scan_unprepped/local_status) and IMP-C13 (single-id commands); the workflow-hardening is new and should get its own Tier H code (IMP-H3) rather than being shoe-horned into a C-tier bug.
- **PR body order (mandatory):** (1) the auto-generated Claude Code summary FIRST (Summary / Changes / Test plan, composed from the executed steps, including the two hypothesis assessments above); then (2) a `## Original task prompt` section containing the COMPLETE verbatim initial task prompt (reproduced at the bottom of this plan); then (3) the `🤖 Generated with Claude Code` trailer.
- **Checkpoint 1 (human-gated):** create the PR, then STOP and ask the user before any `gh pr merge`/merge/push to `main`. Do not merge.
- **Checkpoint 2 (human-gated):** after merge, ask before archiving; on approval, annotated `archive/<branch>` tag (merge info + revive steps) then delete local+remote branch.
- **Backlog bookkeeping in the SAME PR (per CLAUDE.md):** flip IMP-C12 + IMP-C13 to `done` in `improvements/improvements_tierC.md`; add `IMP-H3` (done) to `improvements/improvements_tierH.md`; update `improvements/PRIORITY.md` (move C12/C13 to DONE, bump the 👉 NEXT pointer to the next Band-0 item — IMP-C14 — and the Last-updated date) and the matching nodes/edges in `docs/priority-graph/priority-graph.html`. All three must agree.

## Manual test commands (the user can run by hand)
Safe, read-only on real data (these are the two commands that were crashing):
```
python main.py scan_unprepped        # was: KeyError 'folder_path' on Series; now lists Series unprepped files cleanly
python main.py local_status          # now runs; combined-episode aliases never show as phantom pending uploads
```
Exercise the new single-id de-alias on a real combined-episode secondary id (e.g. a `…e20` whose file is registered under `…e19`) — read-only `check` first:
```
python main.py check tv-en-2009-bsg-s04e20    # prints "ℹ️ … operating on tv-en-2009-bsg-s04e19" then verifies the primary
```
Run the gates:
```
pytest -q
pytest tests/smoke -q
```

## Implementation note (do NOT do during planning)
During implementation the **architect** agent must update `ARCHITECTURE.md` (§3 Repository Layout — add the `tests/smoke/` package; §13 test inventory — note the smoke suite + `sandbox_alias`; §6.3 — cross-link `ENTRY_TYPE_KEYS` as the entry-type source of truth) and `README.md` (test-running section — mention `pytest tests/smoke -q` as the pre-PR gate) for the module-layout change. Per CLAUDE.md, the backlog trio (`improvements/PRIORITY.md` + `docs/priority-graph/priority-graph.html` + the relevant tier file) must be updated when IMP-C12/C13 are completed and IMP-H3 is added — in the same change. None of the planned steps may alter the auto-rollback mechanism; A1 explicitly flags the lookup-head edits as a hard STOP/decision if they would touch PONR/journal behavior.

---

## Original task prompt (verbatim)

Produce **PLAN.md only** (no code changes, no branches, no commits) at the repo root `C:\Users\harin\PycharmProjects\MediaVault\PLAN.md`. This is a two-part task: (A) fix a production crash, and (B) harden the multi-agent workflow + testing strategy so this class of bug can't recur. The user wants a single coherent plan covering both.

## Root cause — ALREADY CONFIRMED by the main session (verify, then build on it; don't re-derive from scratch)
The user ran `python main.py scan_unprepped` and got `KeyError: 'folder_path'` at `main.py:2461`, while scanning the Series library (Movies scanned fine).

- `cmd_scan_unprepped` (`main.py:2433`, loop at 2459-2462) is ORIGINAL code from the initial commit `ebf72db`. It iterates every top-level entry in each `library_*.json` and only skips `type == "season_map"`, then does `os.path.join(entry['folder_path'], entry['filename'])`.
- PR #21 / **IMP-E13** (commit `988b491`, merged 2026-06-10, "multi-episode combined-file support") introduced a NEW top-level entry type `multi_ep_alias` (`main.py:1075`) whose entries contain ONLY `type`, `alias_of`, `parent_id` — no `folder_path`, no `filename`. So `cmd_scan_unprepped` dies on the first alias entry.
- PR #21 added de-alias guards (`_resolve_alias`, `main.py:1612`) only to the consumers it knew about — push/replace/restore/prep_push_rep_season (de-alias loops at lines ~1670, ~1888, ~2277, ~2588). It did NOT touch `cmd_scan_unprepped` (git-confirmed: not in the `988b491` diff). The repo memory even predicted this bug class but it was never enforced.

## Part A — the fix (must be in the plan)
1. Fix `cmd_scan_unprepped` so it tolerates `multi_ep_alias` entries (skip them, or skip any entry lacking `folder_path`/`filename`). Pick the most robust, minimal approach and justify it.
2. **CONSUMER AUDIT (critical — this is the real fix):** systematically audit EVERY place that iterates `library.values()` / `cat_lib.values()` or does `entry['folder_path']` / `entry.get('folder_path')` / `entry['filename']`, and confirm each one either de-aliases via `_resolve_alias` or correctly skips `season_map` AND `multi_ep_alias`. Do this audit NOW as part of planning (you have Grep/Read/Bash) and enumerate in PLAN.md every consumer found, with a verdict (safe / needs-fix) and line number. Candidates already spotted: `cmd_check` at `main.py:1096`, and `main.py:2692`. Treat ANY top-level-iterating or folder_path-dereferencing consumer as in-scope. The deliverable plan must fix all that are broken, not just `scan_unprepped`.
3. Add regression tests: a direct test for `cmd_scan_unprepped` with a library fixture containing a `multi_ep_alias` entry, plus tests for any other consumer you fix.

## Part B — workflow + testing hardening (the user's bigger ask)
The user's core complaint: "working on one feature/fix should NEVER break another part of the code, but our plans/orchestrator/executors/tests let PR #21 ship a breaking change." Design concrete, enforceable mechanisms. Address ALL of these and put recommendations in the plan:

1. **Fast full-command smoke suite.** Design a quick end-to-end test package that exercises EVERY command + major options (prep, prep_season, push, push episode-range/episode push, replace, restore, fetch, sort, scan_unprepped, rehash, recover, etc.) against a tiny (~<100KB) fixture video file and a mock/stub device (there is already a mock-device pattern — see `tests/test_cmd_push_mock_device.py`, `tests/conftest.py`). It must run fast and be the gate run before every PR. Specify: the fixture(s), how device/ADB is stubbed, directory layout (e.g. `tests/smoke/`), and how it's invoked. Reuse existing test infra; don't reinvent.
2. **New-data-shape / consumer-impact guardrail.** Propose an ENFORCED mechanism (not just a memory note) so that whenever a change adds/changes an entry type or data field, every consumer is audited. Options to evaluate and recommend among: a planner-mandated "Consumer Impact Analysis" step; a lightweight static check / test that asserts every `entry[...]` access path is alias/season_map-safe; a "schema of entry types + required keys" doc/registry; a checklist gate. Recommend the lightest thing that actually works.
3. **Agent-instruction changes.** Specify exact edits to `.claude/agents/planner.md`, `.claude/agents/orchestrator.md`, and the executor instructions (and/or `CLAUDE.md`) so EVERY planner/orchestrator/executor must (a) consider cross-command impact, and (b) run the smoke suite before declaring a step/PR done. Note: `.claude/agents/planner.md` already has uncommitted local edits (`git status` shows `M`) — read it first and account for that.
4. **Evaluate the user's hypotheses explicitly** in the plan: (a) "is the model not good enough / how can this happen with good plans+orchestrators+tests?" — give an honest assessment that the real gap is process (no consumer audit, no integration smoke gate), not raw model capability; (b) "is too much task breakdown causing this?" — assess whether over-fragmentation means no single executor owns cross-command integrity, and how the smoke gate + a plan-level integrity step fixes that regardless of fragmentation.

## Open Decisions (REQUIRED — surface to the user as questions before finalizing)
The user explicitly wants to decide scope on the bigger architectural items, WITH your recommendations. Use the `AskUserQuestion` tool to ask the 3-4 highest-leverage decisions BEFORE you finalize PLAN.md, and also record them (with your recommendation) in an "## Open Decisions" section of PLAN.md. Candidate decisions (pick the ones that genuinely change the plan):
- **Scope of this PR:** fix-only (Part A) vs fix + smoke-suite + agent-instruction changes (A+B) in one PR, vs split into two PRs. Recommend.
- **A always-on monitor/watcher agent** (e.g. a CI-like agent or a `/loop` agent that runs the smoke suite continuously / on every change) — yes/no, and which mechanism. Recommend, with cost/benefit.
- **Agent tooling:** should planner/executors get WebSearch/WebFetch, or "enable all tools for all agents (they only use what's needed)"? Give the advantages/risks and recommend.
- **Enforcement strength of the consumer-impact guardrail:** doc/checklist (cheap) vs an actual automated test/static check (stronger, more work). Recommend.
Frame each question with your recommended option FIRST and labeled "(Recommended)".

## PLAN.md required structure & conventions
- Sections: Problem/root-cause summary (cite the PR + lines), Part A steps, Part B steps, then **Tests**, **Verification**, **Open Decisions**, and at the END: **Branch name** (propose one), **PR-to-main plan** (per repo conventions: PR title must include the IMP code if one applies — check `improvements/improvements_tier*.md`; if no existing IMP fits, say so and propose creating one; PR body order = Claude summary first, then `## Original task prompt` verbatim, then trailer; merging to main is human-gated), and **Manual test commands** the user can run by hand.
- For EACH step assign an executor model: `haiku` / `sonnet` / `opus`, with a one-line justification. Mark any step for multi-candidate evaluation ONLY if it genuinely has multiple legitimate approaches.
- **Rollback change-gate:** none of the proposed changes may alter the auto-rollback mechanism (RollbackJournal/recover_journal/PONR/journal format) without an explicit user decision — see `CLAUDE.md`. If any step touches rollback behavior, flag it as a hard STOP/decision, don't plan around it silently.
- **Implementation note for the plan (do NOT do it now):** state that during implementation the architect agent must update `ARCHITECTURE.md` and `README` for any module-layout change (e.g. a new `tests/smoke/` package), and that `improvements/PRIORITY.md` + `docs/priority-graph/priority-graph.html` + the relevant tier file must be updated when a task is added/completed (per `CLAUDE.md`).
- Read `improvements/PRIORITY.md`, `improvements/README.md`, and `improvements/improvement_details.md` to slot this work correctly and find/propose the right IMP code. The current top-priority bug per memory was IMP-C12 (alias crash) — check whether THIS crash is the same as or related to IMP-C12, and reconcile.

## Hard constraints
- Deliverable is PLAN.md ONLY. Do not edit code, create branches, or commit.
- Verify my root-cause claims against the actual code/git before relying on them (quick check), but don't waste effort re-investigating what's already confirmed above.
- Append the COMPLETE VERBATIM original user task prompt at the very bottom of PLAN.md under `## Original task prompt (verbatim)` so the eventual PR can reuse it. I will paste it below for you:

<<<ORIGINAL TASK PROMPT VERBATIM>>>
I just ran this command and got the following error: 
$ python main.py scan_unprepped
[... KeyError: 'folder_path' traceback at main.py:2461 in cmd_scan_unprepped, while scanning Series; Movies listed 8 unprepped files fine ...]
This was running few days back. Why is this breaking now? Find out exactly which change or PR caused this issue and how this was missed in our executions and tests? what are we missing in this task execution and testing strategy? Find out what exact change was done and track all the decisions, to find how it can do such a breakable change. or is the model not that good? how can this happen when we have such good plans and orchestrators and tests? 
Along with fixing this error, I also want to fix the way we work on the tasks, make any modifications to the agents, change the approach if needed, make it do the fix strategically so that other commands which are working are also always taken into consideration and made sure it works flawlessly. Also our testing strategy, need to improve and execute all the commands quickly as a test package - maybe in a fast way to make sure all commands work fine. for example, prep, push, replace everything can test on some small 100kb or lesser test video file. Also fetch can use a similar file from other location to make sure no issues. also other commands like prep, sort, and any other commands and all options including rehash or episode push, everything needs to be tested properly with a full suite for each of our change. Maybe finally before creating the merge req - that needs to be tested also. 
but before testing, each of the executor, planner, orchestrator need to keep this in mind always. Working on one feature, or a fix or improvement should not break any other part of the code or functionality. Make sure this is properly induced in every approach we follow maybe by using any of the best practices or approaches or any coding practice. If needed to add a pipeline, add that, if you think anything else is important do that. But I need a proper working set of multiple agents which works coherently and in sync and doesn't break stuff. Even if you want to add any other agents, which keep running always and monitor these let me know or any other approach with multiple agents also you can suggest. or if too much breakdown of tasks into steps is causing this also lets find out. 
in general also if you have any other suggestions on our agent setup or if you think any other projects or agent setups does these better suggest those features also and ask me as questions with your recommendations. I will tell if we need to implement that also along with this task. Also check if we should add any tools for any agents, what are the advantages? for example for planner or executor to add web search or fetch so that it can also lookup and use all the tools available. or should we just enable all tools for all agents anyway it will use only when needed? 
Do deep dive into our practices, any loopholes or any other strategies we need to follow to make our system robust? 
Come up with a complete plan for both fixing this and making our system fool proof and super strong. 
Deliverables: PLAN.md only (no code, no branches) with steps + tests + verification + "Open Decisions". Note on implementation, the architect updates ARCHITECTURE.md/README (module layout change), and I want branch name, PR to main, and manual test commands at the end.
<<<END ORIGINAL TASK PROMPT>>>
