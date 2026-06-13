# Task: IMP-C14 — CLI parser papercuts (push_group hang, mainfetch argv guard, silent replace)

Suggested branch: fix/cli_parser_papercuts

## Context
The 2026-06-12 fable-review full code read found three CLI trip-hazards (REVIEW_NOTES §A4/§A5/§A6), tracked as IMP-C14 in `improvements/improvements_tierC.md`. Both entry points (`main.py`, `mainfetch.py`) parse argv by hand-walking `sys.argv` (no argparse — see ARCHITECTURE §5). The bugs are: (A) the `push_group` argv parser is missing the `else:` "missing value" arms that its sibling `push` parser has, so a value-keyword as the final token spins `while i < len(args)` forever (console hangs, Ctrl-C needed); (B) `mainfetch.py`'s `__main__` guard checks `len(sys.argv) < 2` but then reads `sys.argv[2]`, so `python mainfetch.py fetch` raises `IndexError`; (C) `cmd_replace` on an unknown id returns `False` with no output, so a typo'd id looks like success. All three are on the exact commands the user types most under stress (re-pushing after a failure).

## Goal
- `python main.py push_group <id> SIZE_GB 8 device` (trailing value-keyword) FAILS FAST with a "missing value" usage message + `sys.exit(1)` — no hang.
- `python mainfetch.py fetch` (no id) prints the usage line + `sys.exit(1)` — no `IndexError` traceback.
- `python main.py replace <bad-id>` prints a clear "id not found in library" error before returning `False` — no silent no-op.
- Unknown/typo'd tokens still SILENTLY SKIP exactly as `push` does today (behavior unchanged — only the missing-value arms are added).
- The buggy parsing in (A) and (B) is extracted into small pure, in-process-testable functions; new unit tests prove fail-fast, valid-parse, and silent-skip behavior with NO subprocess and NO Selenium. `pytest -q` and `pytest tests/smoke -q` are green.

## Files affected
- `main.py` — extract `parse_push_group_args(args)`; wire the `elif cmd == "push_group":` block to call it; add the "id not found in library" print to `cmd_replace`.
- `mainfetch.py` — extract `parse_fetch_args(argv)`; wire the `__main__` guard to call it (fix `< 2` → `< 3` + require `argv[1] == "fetch"`).
- `tests/test_cli_parsers.py` — NEW. Unit tests for `parse_push_group_args`, `parse_fetch_args`, and the `cmd_replace` not-found path.
- `ARCHITECTURE.md` — §5 CLI dispatch / commands notes (push_group now fails fast; mainfetch bare-invoke prints usage). ARCHITECT-owned.
- `README.md` — any user-facing CLI usage notes affected. ARCHITECT-owned.
- `improvements/improvements_tierC.md` — mark IMP-C14 `done`.
- `improvements/PRIORITY.md` — Band 0 row, Last updated, 👉 SUGGESTED NEXT TASK pointer → IMP-C15.
- `docs/priority-graph/priority-graph.html` — IMP-C14 node `s` status → `done`; header "Next" → IMP-C15.

## Approach
The user has RESOLVED the design (see Open Decisions): refactor the two `__main__`/parser-embedded bugs into pure functions so they are unit-testable in-process without risking a pytest hang (Bug A) or spawning Selenium (Bug B). Bug C is already a real function (`cmd_replace`) — it only needs one print added; no extraction.

End-to-end:
1. Extract `parse_push_group_args(args)` from the `push_group` argv block in `main.py`. It takes the post-subcommand token list (`sys.argv[2:]`) and returns the parsed tuple `(group_id, method, val, ep_range, dev, eager, tdir)`, or signals a usage error. The error signal must TERMINATE rather than loop: for each value-keyword (`SIZE_MB`/`SIZE_GB`/`COUNT`, `episodes`, `device`) that is the final token with no following value, the function fails fast — mirroring `push`'s `else: print(...); sys.exit(1)` arms. `tempdir` and `rehash` already have correct arms; `rehash` is a flag (no value). Unknown tokens keep the `else: i += 1` silent-skip (do NOT change). The `__main__` block then becomes: `group_id, method, val, ep_range, dev, eager, tdir = parse_push_group_args(sys.argv[2:])` followed by the existing `cmd_push_group(...)` call.
2. Extract `parse_fetch_args(argv)` from `mainfetch.py`'s `__main__`. It takes the full `sys.argv` (or an explicit argv list) and returns `(mid, epr)`, or signals a usage error. The guard becomes: require `len(argv) >= 3` AND `argv[1] == "fetch"` — otherwise print the usage line and signal error. The existing `episodes` parse (`len(argv) >= 5 and argv[3] == "episodes"`) is preserved verbatim. The function must NOT call `cmd_fetch_route` — the `__main__` block keeps that call so the parser stays pure and Selenium-free under test.
3. Add to `cmd_replace`: replace the bare `if manual_id not in library: return False` with a version that prints a clear not-found error first (e.g. `print(f"❌ Error: '{manual_id}' not found in library.")`) then `return False`. The "not uploaded" path already prints — leave it untouched.
4. Write `tests/test_cli_parsers.py` calling the extracted functions directly + the `cmd_replace` not-found path under `sandbox`.
5. ARCHITECT updates `ARCHITECTURE.md` + `README.md` for the documented behavior change.
6. Mark IMP-C14 done across the three tracking artifacts.

### Error-signal pattern (executor decision, constrained)
Pick the cleanest of: (a) keep the existing `print(...); sys.exit(1)` inline in the extracted function (simplest, mirrors `push` exactly, and is unit-testable via `pytest.raises(SystemExit)`); or (b) raise a small `UsageError` that `__main__` catches and converts to `print + sys.exit(1)`. DEFAULT TO (a) `sys.exit(1)` — it is the smallest diff, preserves `push`'s exact behavior and exit semantics, and `pytest.raises(SystemExit)` cleanly asserts fail-fast without any hang. Whichever is chosen, the test must assert the missing-value case TERMINATES (does not loop) and that valid args return the correct tuple.

## Steps

- [x] 1. [model: opus] [effort: high] Extract `parse_push_group_args(args)` in `main.py` and rewire the `push_group` dispatch block to use it; fix Bug A (missing-value fail-fast).
  - Files: `main.py`
  - Details: Add a module-level pure function `parse_push_group_args(args)` (place it near `cmd_push_group` or with the other CLI helpers — keep it importable as `main.parse_push_group_args`). It takes the token list `args` (= `sys.argv[2:]`) and reproduces the current `push_group` parse loop (`main.py:3060-3098`) EXACTLY, with these required changes vs today: (1) the empty-args guard (`if not args: print("❌ Usage: push_group [id] ..."); sys.exit(1)`) moves into the function; (2) `SIZE_MB`/`SIZE_GB`/`COUNT` (currently `main.py:3074`, no else), `episodes` (`:3079`, no else), and `device` (`:3083`, no else) each GAIN an `else:` arm that mirrors `push`'s exact wording — `print("❌ Error: Missing value for split method.")` / `"... for episodes range."` / `"... for device."` then `sys.exit(1)`. Use the `push` parser at `main.py:3019-3054` as the authoritative reference for arm wording and structure. `rehash` stays a no-value flag (`eager = True; i += 1`); `tempdir` already has its correct else arm — keep both as-is. The final `else: i += 1` (silent-skip of unknown tokens) is PRESERVED UNCHANGED — do not turn unknown-token handling into fail-fast (resolved decision #2). Return `(group_id, method, val, ep_range, dev, eager, tdir)`. Then replace the body of `elif cmd == "push_group":` with: `group_id, method, val, ep_range, dev, eager, tdir = parse_push_group_args(sys.argv[2:])` immediately followed by the EXISTING `cmd_push_group(group_id, method, val, ep_range, device_id=resolve_device(dev), eager_rehash=eager, temp_dir=tdir)` call (note: `resolve_device(dev)` stays in `__main__`, NOT inside the pure parser — the parser must not import device-resolution side effects). Do not touch the `push` (single-id) parser — out of scope.
  - Acceptance: `parse_push_group_args(["g","SIZE_GB","8","device"])` raises `SystemExit` (does not hang/loop); `parse_push_group_args(["g","SIZE_GB","8","device","series","rehash"])` returns `("g","SIZE_GB","8",None,"series",True,None)`; `parse_push_group_args(["g","episdoes","1-3"])` silently skips the typo and returns the group_id with `ep_range=None`. `python -c "import main"` imports clean. `pytest -q` green.

- [x] 2. [model: sonnet] [effort: medium] Extract `parse_fetch_args(argv)` in `mainfetch.py` and fix Bug B (bare-invoke `IndexError`).
  - Files: `mainfetch.py`
  - Details: Add a module-level pure function `parse_fetch_args(argv)` (importable as `mainfetch.parse_fetch_args`) that takes a full argv list. Guard: `if len(argv) < 3 or argv[1] != "fetch": print("Usage: fetch [id] [episodes] [range]"); sys.exit(1)` — this fixes the current bug where the guard is `len(sys.argv) < 2` (`mainfetch.py:481`) but `sys.argv[2]` is read at `:486`. Then reproduce the existing parse verbatim: `mid = argv[2]; epr = None; if len(argv) >= 5 and argv[3] == "episodes": epr = argv[4]`. Return `(mid, epr)`. The function MUST NOT call `cmd_fetch_route` (keep it pure / Selenium-free for tests). Rewire `if __name__ == "__main__":` to: `mid, epr = parse_fetch_args(sys.argv)` followed by the EXISTING `cmd_fetch_route(mid, epr)` call. Keep the usage string identical to today's (`"Usage: fetch [id] [episodes] [range]"`).
  - Acceptance: `parse_fetch_args(["mainfetch.py","fetch"])` raises `SystemExit` (no `IndexError`); `parse_fetch_args(["mainfetch.py","fetch","tv-en-2016-strangerthings-s01e03"])` returns `("tv-en-2016-strangerthings-s01e03", None)`; `parse_fetch_args(["mainfetch.py","fetch","tv-x","episodes","1-3"])` returns `("tv-x","1-3")`; `parse_fetch_args(["mainfetch.py","wrongverb","x"])` raises `SystemExit`. `python -c "import mainfetch"` imports clean (note: importing mainfetch must not require a browser — confirm it imports without side effects). `pytest -q` green.

- [x] 3. [model: sonnet] [effort: low] Fix Bug C — add a not-found error message to `cmd_replace`.
  - Files: `main.py`
  - Details: At `main.py:1790`, change `if manual_id not in library: return False` to print a clear error first, e.g.:
    `if manual_id not in library:`
    `    print(f"❌ Error: '{manual_id}' not found in library.")`
    `    return False`
    Keep the rest of `cmd_replace` byte-identical (the `_resolve_alias` call at `:1791` and the already-printing "not marked as uploaded" path at `:1796-1798` are untouched). Match the existing `❌ Error:` message style used elsewhere in the file. Do NOT add a message to `cmd_replace_group` (it already prints `❌ No items found.` on an empty group, `main.py:1936`) — that consistency question is logged in Open Decisions as out-of-scope.
  - Acceptance: `cmd_replace("definitely-not-a-real-id")` prints a not-found error and returns `False`. `pytest -q` green.

- [x] 4. [model: sonnet] [effort: medium] Write `tests/test_cli_parsers.py` — unit tests for the two extracted parsers + the cmd_replace not-found path.
  - Files: `tests/test_cli_parsers.py` (new)
  - Details: Read `docs/testing-strategy.md` first (fixture selection). The parser tests are PURE — they import `main` / `mainfetch` and call `parse_push_group_args` / `parse_fetch_args` directly; NO subprocess, NO Selenium, NO `sandbox` needed for those. Assert, for `parse_push_group_args`: (a) valid full invocation returns the exact expected 7-tuple (cover SIZE_GB + device alias + rehash + tempdir); (b) a TRAILING value-keyword (`["g","SIZE_GB","8","device"]`, `["g","SIZE_MB"]`, `["g","g-id","episodes"]`) raises `SystemExit` via `pytest.raises(SystemExit)` — this is the fail-fast/no-hang assertion; (c) an UNKNOWN/typo token (`["g","episdoes","1-3"]`) is silently skipped (returns with `ep_range=None`, group_id preserved) — the mirror-push behavior; (d) empty args (`[]`) raises `SystemExit`. For `parse_fetch_args`: (a) `["mainfetch.py","fetch"]` raises `SystemExit` (the IndexError regression); (b) `["mainfetch.py","fetch","tv-x"]` → `("tv-x", None)`; (c) `["mainfetch.py","fetch","tv-x","episodes","1-3"]` → `("tv-x","1-3")`; (d) wrong verb / too-few args raises `SystemExit`. For `cmd_replace`: use the `sandbox` fixture (do NOT DIY library redirection — sandbox patches BOTH `mvcommon.LIBRARY_*` AND `main.LIBRARY_*`); seed an empty (or unrelated) library, call `main.cmd_replace("bad-id")`, assert it returns `False` AND that stdout (via `capsys`) contains the not-found message. Constraints (MUST appear in test code intent): Never touch real C:\Media files or real library_*.json. Run `pytest -q` and fix failures before marking the step done.
  - Acceptance: `pytest tests/test_cli_parsers.py -q` passes; `pytest -q` (full suite) green. Tests demonstrably do NOT hang (the trailing-keyword cases assert `SystemExit`, proving no infinite loop) and never import a browser/Selenium driver at module load.

- [x] 5. [model: opus] [effort: medium] ARCHITECT AGENT: update `ARCHITECTURE.md` and `README.md` for the documented behavior change. ORCHESTRATOR: dispatch this step to the **architect** agent (architect owns `ARCHITECTURE.md` and `README.md`).
  - Files: `ARCHITECTURE.md`, `README.md`
  - Details: This is a user-visible behavior change, so the docs the architect owns must reflect it. In `ARCHITECTURE.md` §5 (Entry Points / CLI dispatch): note that `push_group`'s parser now fails fast with a "missing value" usage message (mirroring `push`) instead of hanging on a trailing value-keyword, and that the parse logic for `push_group` and the `mainfetch.py fetch` bare-invoke now live in extracted pure functions (`main.parse_push_group_args`, `mainfetch.parse_fetch_args`) for testability. Update the `mainfetch.py` invocation note (around the §5 "python mainfetch.py fetch ..." subsection / lines ~256-262) to reflect that a bare `python mainfetch.py fetch` (no id) now prints usage + exits instead of throwing `IndexError`. In `README.md`: if the CLI usage / commands section documents `push_group`, `replace`, or the `mainfetch.py fetch` invocation, add a one-line note that malformed invocations now fail fast with usage text and that `replace` on an unknown id reports "not found". Keep edits surgical — only the lines that describe the changed behavior; do not restructure these docs. Do NOT touch the §12a rollback contract or any rollback-adjacent text (unrelated). No code changes in this step.
  - Acceptance: `ARCHITECTURE.md` §5 mentions push_group fail-fast + the two extracted parse functions + the mainfetch bare-invoke usage behavior; `README.md` reflects the fail-fast/not-found behavior where it documents these commands. No stale "hangs"/"IndexError" claims remain about these paths.

- [x] 6. [model: haiku] [effort: low] Mark IMP-C14 done across the three tracking artifacts (PRIORITY.md, tier file, priority-graph.html) — keep all three in agreement.
  - Files: `improvements/improvements_tierC.md`, `improvements/PRIORITY.md`, `docs/priority-graph/priority-graph.html`
  - Details: Per the maintenance protocol at the bottom of `improvements/PRIORITY.md`:
    (1) `improvements/improvements_tierC.md` IMP-C14: change `- Status: pending` to `- Status: done (fix/cli_parser_papercuts — push_group missing-value fail-fast arms mirroring push; parse logic extracted to main.parse_push_group_args / mainfetch.parse_fetch_args; mainfetch bare-invoke guard fixed; cmd_replace prints not-found; unit tests in tests/test_cli_parsers.py)`.
    (2) `improvements/PRIORITY.md`: in the Band 0 table, change the IMP-C14 row (currently row 3, `| 3 | 🔴 **IMP-C14** | ... | low | — |`) to reflect done (mark its status — match exactly how the C12/C13 done-rows are styled in this file; if done items are moved to the "✅ DONE" list rather than left in Band 0, move IMP-C14 there too and bump the DONE count from 14 to 15). Update the `👉 SUGGESTED NEXT TASK` line (currently IMP-C14) to point at the next Band-0 item **IMP-C15** (micro-robustness batch — repair_dummies atomic swap + `_verify_chunk_hash` IndexError guard) with a one-line summary. Update the **Last updated** line to `2026-06-14 (IMP-C14 done — fix/cli_parser_papercuts).` Add `C14 (CLI parser papercuts)` to the ✅ DONE roster line.
    (3) `docs/priority-graph/priority-graph.html`: in the `TASKS` array, the C14 node is `["C14","parser papercuts","C","crit","todo","push_group infinite-loop hang; mainfetch IndexError; silent replace"]` (line ~158). Set its status field — match exactly what was done for C12/C13 (which became `["C12",...,"C","done","done",...]`, i.e. BOTH the 4th `p` field and the 5th `s` field set to `"done"`), and update its note to a short "Fixed fix/cli_parser_papercuts: ..." string. Update the header "Next" line (line ~84, `⚡ Next: <b>IMP-C14</b>`) to point at IMP-C15 with its short label. The graph and PRIORITY.md must agree.
  - Acceptance: All three files show IMP-C14 done and IMP-C15 as the next Band-0 task; no file still lists IMP-C14 as pending/next. Grep `C14` across the three files shows only done/historical references.

## Risks and edge cases
- **Behavior parity (the real risk):** the extracted `parse_push_group_args` must reproduce the existing loop EXACTLY except for the added missing-value arms. Easy slips: (a) accidentally turning unknown-token silent-skip into fail-fast (resolved decision #2 forbids this); (b) calling `resolve_device(dev)` inside the pure parser (it must stay in `__main__` so the parser has no side effects and is unit-testable); (c) dropping the empty-args guard. The full suite + smoke gate catch dispatch regressions.
- **`push` parser drift:** wording of the new `push_group` arms should match `push`'s arms (`main.py:3019-3054`) so the two stay consistent (the reason the bug was noticed). Not byte-identical-required, but mirror the style.
- **`sys.exit(1)` under test:** asserting fail-fast via `pytest.raises(SystemExit)` is correct and does NOT hang; a naive test that loops would hang pytest — the tests are written to assert the exception, never to let the old loop run.
- **mainfetch import side effects:** the parser tests import `mainfetch`; confirm module import does not instantiate a webdriver at import time (it should not — the Selenium calls live inside functions). If import-time side effects exist, the test imports the function only and the step must surface that as a blocker (it does not — verified the §480 block is guarded by `__main__`).
- **Line-number drift:** all `main.py:NNNN` / `mainfetch.py:NNN` references are point-in-time (ARCHITECTURE warns of this). Executors should grep by function/keyword name (`elif cmd == "push_group"`, `def cmd_replace`, `if __name__ == "__main__"`) rather than trust line numbers.
- **No shared data contract is touched** — see the explicit note below; no Consumer Impact Analysis is required.

## Consumer Impact Analysis
Not required for this task. IMP-C14 changes NO shared data contract: it adds no library entry type, renames/removes no library field or key, alters no manual-ID shape, and changes no `status` value. The changes are (1) argv-parsing control flow extracted into pure functions + added fail-fast arms, (2) one added `print` in `cmd_replace`. `ENTRY_TYPE_KEYS` is unaffected. Therefore the `## Consumer Impact Analysis` section is intentionally a no-op and the table is omitted by design.

## Open Decisions

**Resolved (recorded here for an explicit trail — do NOT re-ask):**
1. **Testing approach = EXTRACT PARSE FUNCTIONS (in-process unit tests; no subprocess).** Chosen because Bug A (infinite loop) and Bug B live inside `if __name__ == "__main__":` blocks and a hang would freeze pytest if exercised via subprocess. Rationale: extracting `parse_push_group_args` / `parse_fetch_args` makes the guard logic fast, pure, and unit-testable without a hang risk or Selenium. Default-to-minimal: extract ONLY `push_group`'s parser and `mainfetch`'s arg parse; the `push` (single-id) inline parser is left as-is (not buggy, out of scope).
2. **Unknown/typo'd tokens = MIRROR `push` (keep silent-skip).** Chosen for the smaller, consistent diff: only the missing-value fail-fast arms are added (these fix the hang); the final `else: i += 1` silent-skip of unknown tokens is unchanged, so `push_group <id> episdoes 1-3` still silently skips the typo exactly as `push` does today.

**Residual open items (surfaced, with default recommendation):**
3. **`cmd_replace_group` per-id consistency.** Checked: `cmd_replace_group` (`main.py:1926-1951`) is NOT silently broken at the group level — when its group/prefix matches nothing it prints `❌ No items found.` and returns. However, it loops `for mid in target_ids: cmd_replace(mid)`; after this task each missing child id printed the new not-found message via `cmd_replace`. There is no separate silent-return to fix in `replace_group` itself. Open question: should `replace_group` emit its own per-child "not found" summary for consistency? **Default recommendation: OUT OF SCOPE** (surgical). The group path already prints `❌ No items found.` for the common case, and `cmd_replace`'s new message now covers the per-child case for free. Revisit if/when the argparse migration (IMP-A2) reworks these parsers.
4. **Extract `push`'s parser too for symmetry.** Noted as a tempting consistency follow-up. **Out of scope** — `push` is not buggy; extracting it now would widen the diff for no behavior change. Candidate to fold into IMP-A2 (argparse migration), which obsoletes all the hand-rolled parsers.
5. **Deliverable typo confirmed.** The task brief's mention of "IMP-G1 marked done" is a typo for **IMP-C14** — IMP-G1 (chunker patterns) is ALREADY done (see PRIORITY.md ✅ DONE roster). This plan marks **IMP-C14** done. No action on IMP-G1.

## Verification
Run from the repo root, in order:
1. `python -c "import main, mainfetch"` — both modules import clean after the extractions (no syntax error, no import-time side effect).
2. `pytest tests/test_cli_parsers.py -q` — the new parser + cmd_replace tests pass.
3. `pytest -q` — full suite green (catches any dispatch regression in `push_group` / `cmd_replace`).
4. `pytest tests/smoke -q` — FINAL gate: the fast full-command cross-command smoke suite (drives every user-facing command against tiny fixtures incl. the alias sweep). REQUIRED because steps touch BOTH `main.py` and `mainfetch.py`; this is the single check that answers "did this change break another command?" Nothing ships if it is red.

## Out of scope
- The `push` (single-id) inline parser — not buggy; left untouched (only noted as a possible IMP-A2 follow-up).
- Any change to how unknown/typo'd tokens are handled (silent-skip is preserved by resolved decision #2).
- Migrating either entry point to `argparse`/`click` (that is IMP-A2 — a bigger lift that obsoletes these hand-rolled parsers; IMP-C14 is the cheap fix done first).
- Adding a separate per-child "not found" summary to `cmd_replace_group` (Open Decision #3 — out of scope).
- Any rollback-adjacent behavior (PONR, journal, `RollbackHardFail`) — untouched; no change-gate applies.
- Touching `mvcommon.py` (no shared helper changes needed).

## Branch, PR & Manual Verification

### Branch name
`fix/cli_parser_papercuts`

### PR to main (follow `docs/git-pr-conventions.md`)
- **PR title MUST include the IMP code:** e.g. `fix: CLI parser papercuts — push_group hang, mainfetch argv guard, silent replace — IMP-C14`.
- **PR body order (exact):**
  1. The auto-generated Claude Code summary FIRST.
  2. Then a `## Original task prompt` section containing the COMPLETE verbatim original task prompt for this IMP-C14 plan.
  3. Then the `🤖 Generated with Claude Code` trailer.
- **Checkpoint 1 — merging to `main` is HUMAN-GATED.** Create the PR, then STOP and ask the user for explicit confirmation before any `gh pr merge` / merge / push to `main`. Do not merge autonomously.

### Manual test commands (run by hand to feel each fix is gone)
Use a real-shaped id from the conventions (ARCHITECTURE §6.2); these are safe-to-fail invocations (parser/guard exits before any device/library mutation):
- **Bug A (was: infinite hang):** `python main.py push_group tv-en-2016-strangerthings-s01 SIZE_GB 8 device`
  - EXPECT: prints `❌ Error: Missing value for device.` and exits immediately (previously: console hung, needed Ctrl-C). Also try `python main.py push_group tv-en-2016-strangerthings-s01 SIZE_MB` (trailing split method) → `❌ Error: Missing value for split method.` + exit.
- **Bug B (was: IndexError traceback):** `python mainfetch.py fetch`
  - EXPECT: prints `Usage: fetch [id] [episodes] [range]` and exits 1 (previously: `IndexError: list index out of range`).
- **Bug C (was: silent no-op):** `python main.py replace mov-en-2099-doesnotexist`
  - EXPECT: prints `❌ Error: 'mov-en-2099-doesnotexist' not found in library.` and returns/exits (previously: no output at all, looked like success).
- **Regression (silent-skip preserved):** `python main.py push_group tv-en-2016-strangerthings-s01 episdoes 1-3` — note the typo `episdoes`. EXPECT: the typo'd token is silently skipped (range ignored) and the command proceeds to `cmd_push_group` exactly as `push` would — NOT a fail-fast error. (Stop with Ctrl-C if it starts a real push, or run against an id you actually want pushed.)
