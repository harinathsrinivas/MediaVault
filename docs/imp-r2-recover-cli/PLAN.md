# Task: IMP-R2 — expose a `recover` CLI subcommand for `recover_journal()`

Suggested branch: feature/recover_cli

## Context
The durable auto-rollback journal (`.mediavault_txn.json`) survives a hard kill / power loss, and `recover_journal(folder_path)` (`main.py:561`) finishes an interrupted rollback. Today it is only callable programmatically (`python -c "import main; main.recover_journal(...)"`); there is no user-facing subcommand. IMP-R2 (`improvements_tierR.md:58`) asks for `python main.py recover <id|folder>` that resolves the media folder (by library id or direct path), calls the **existing** `recover_journal()`, and prints the outcome, plus an optional `recover --scan` that sweeps the media roots for leftover journals (folds in IMP-R5's read-only sweep).

## Goal
- `python main.py recover <id|folder>` resolves the media folder and calls `recover_journal()`, printing a clear outcome (recovered / nothing-to-do / crossed-PONR / partial). Resolution: if the arg is a key in the merged library, use that entry's `folder_path`; otherwise treat the arg as a direct folder path. Season-map ids resolve to their own `folder_path`.
- `python main.py recover --scan` walks `C:\Media\{Movies,Series,Anime}` for `.mediavault_txn.json` files and reports each (path, `crossed_ponr`, record count), recommending `recover <folder>` for pre-PONR journals and inspection for post-PONR ones. Read-only — never replays.
- README CLI table and ARCHITECTURE.md entry-point list document the new subcommand.
- Existing `pytest -q` stays green; new tests cover id-resolution, path-resolution, the not-found case, and `--scan`.

## Non-negotiable constraint (CHANGE-GATE)
This task is **purely additive**: a CLI wrapper + dispatch branch + docs + tests. It MUST NOT change `recover_journal()` semantics, the journal format/durability, PONR logic, `mark_point_of_no_return()` placement, the created-this-run scoping, the `cmd_*` wrapping, the season resume-range messaging, or the `RollbackHardFail` contract. If, while implementing, any executor finds it must modify `recover_journal` or any rollback path to make the wrapper work, it MUST STOP, state the exact diff needed, and ask the user (per `CLAUDE.md` "Auto-rollback is load-bearing — change-gate" and `ROLLBACK_MECHANISM.md` §10). The wrapper only *reads* the library (for id→folder resolution) and *calls* the existing function.

## Files affected
- `main.py` — add `cmd_recover(target, scan=False)` near `recover_journal` (~561), add a `recover` branch to the `sys.argv` dispatch (~2436, alongside `sort`/`fetch`), and add a usage line to the `__main__` usage block (~2222).
- `tests/test_recover_cli.py` — NEW test file (sandbox-based) for `cmd_recover` resolution + `--scan`.
- `README.md` — add a `recover` row to the CLI table (~138, after `sort`).
- `ARCHITECTURE.md` — add `recover` to the §5 subcommand table (~225, after the `fetch` row) and a one-line note in §12a (~1367) that `recover_journal` now has a CLI entry point.
- `docs/imp-r2-recover-cli/PLAN.md` + `docs/imp-r2-recover-cli/DECISIONS.md` — tracked plan copy + decision record (git-agent commits these).

## Steps

- [x] 1. [model: sonnet] [effort: medium] Add `cmd_recover(target, scan=False)` to `main.py`.
- [x] 2. [model: sonnet] [effort: medium] Wire `recover` into the `sys.argv` dispatch and usage block.
- [x] 3. [model: sonnet] [effort: medium] Add `tests/test_recover_cli.py` covering resolution + scan.
- [x] 4. [model: haiku] [effort: low] Document `recover` in README and ARCHITECTURE.
- [x] 5. [model: haiku] [effort: low] Record the decision and mark IMP-R2 status.

## Step list summary
1. [sonnet] Add `cmd_recover(target, scan=False)` to `main.py` (wrapper only; `recover_journal` untouched). ✅
2. [sonnet] Wire `recover` into `sys.argv` dispatch + usage block. ✅
3. [sonnet] Add `tests/test_recover_cli.py` (sandbox-based; id/path/not-found/crossed-PONR/scan). ✅
4. [haiku] Document `recover` in README CLI table + ARCHITECTURE §5/§12a. ✅
5. [haiku] Add `docs/imp-r2-recover-cli/DECISIONS.md` + mark IMP-R2 status. ✅
