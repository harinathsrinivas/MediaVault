# Task: Add optional `device` arg to push commands to target a specific ADB device

Suggested branch: feature/adb-device-select

## Context
MediaVault's push pipeline (`cmd_push`, `cmd_push_group`, `cmd_prep_push_rep`, `cmd_prep_push_rep_season`) shells out to `adb shell` / `adb push -p` with no `-s <serial>` flag, so with two Pixel phones connected at once ADB cannot pick the right target. The user keeps a "movies" Pixel and a "series" Pixel and wants to parallelise archival work across them. This is purely additive plumbing — when no `device` is given, behaviour must be byte-identical to today.

Architecture note: ADB integration today is described in ARCHITECTURE.md §7.5 ("ADB device detection is implicit"). This task is the first half of the §17.6 "Multi-device push" future-work seam, scoped down to just per-call device selection (no per-device `REMOTE_ROOT`).

## Goal
The four push commands accept an optional `device <id_or_name>` keyword on the CLI. The value is either a raw ADB serial (e.g. `FA69H0300200`) or a user-friendly alias from a small hardcoded `DEVICE_ALIASES` dict (e.g. `movies`, `series`). When supplied, every `adb shell` / `adb push` subprocess call in that invocation runs as `adb -s <serial> ...`. When not supplied, every ADB call is unchanged (`adb ...`) and the tool's behaviour is identical to today. ARCHITECTURE.md and README.md document the new option.

Definition of done:
- `python main.py push <id> SIZE_MB 9900 device movies` resolves `movies` -> `FA69H0300200` and runs `adb -s FA69H0300200 shell mkdir ...` / `adb -s FA69H0300200 push -p ...`.
- `python main.py push <id> SIZE_MB 9900` (no `device` arg) issues calls identical to today: `adb shell mkdir ...` / `adb push -p ...`.
- Same applies to `push_group`, `prep_push_rep`, `prep_push_rep_season`.
- Passing an unknown alias falls through as a raw serial (so users can pass any serial without registering it).
- ARCHITECTURE.md §5 command table, §7.5 ADB push flow, §14 configuration constants table, and §18 quick-reference are updated. README.md gains a short section on multi-device usage.

## Files affected
- `main.py` — only file with code changes. Adds `DEVICE_ALIASES` constant + `resolve_device()` helper, threads `device_id` through 4 functions, updates 2 subprocess.run sites in `cmd_push`, updates 4 CLI parser blocks.
- `ARCHITECTURE.md` — doc-only update by architect agent.
- `README.md` — doc-only update by architect agent.

## Approach
The change is mechanical thread-through:

1. Add a config constant `DEVICE_ALIASES` (dict) and a tiny helper `resolve_device(arg)` near the top of `main.py` next to the other constants.
2. In `cmd_push`, compute a single `adb_base = ["adb", "-s", device_id] if device_id else ["adb"]` at the top, then replace the two literal `["adb", ...]` lists at line 656 and line 755 with `adb_base + [...]`. No other change in that function.
3. Thread an optional `device_id=None` keyword argument through the four push-related functions, passing it down to nested `cmd_push` calls in `cmd_push_group` (line 839), `cmd_prep_push_rep` (line 1380), and `cmd_prep_push_rep_season` (line 1465).
4. In the four CLI argv-parser blocks, add `device` as a new positional keyword (mirroring how `chunks` and `episodes` are parsed). For `prep_push_rep` specifically, the parser today uses a fragile `rest[-2]` shape — switch to the token-scanner pattern used by `prep_push_rep_season` so `device` can appear at any position without breaking the filepath join. The keyword scan must happen before the filepath is reconstructed from the remaining tokens.
5. After every code step passes, hand off to the architect agent to update ARCHITECTURE.md and README.md.

`resolve_device(None)` returns `None`, which means `device_id=None` propagates unchanged and `adb_base` is exactly `["adb"]` — identical to today's call shape.

## Steps

- [x] 1. [model: haiku] Create the new feature branch from `main`.
  - Files: git only (no files touched)
  - Details: Use the git-agent. Fetch latest origin, then create branch `feature/adb-device-select` based on `origin/main` (not on the currently-checked-out `feature/video_dummy`). Switch to the new branch. Do not modify any files.
  - Acceptance: `git status` reports `On branch feature/adb-device-select`, working tree clean, branch's merge-base with `origin/main` is the tip of `origin/main`.

- [x] 2. [model: sonnet] Add `DEVICE_ALIASES` constant and `resolve_device()` helper.
  - Files: `C:\Users\harin\PycharmProjects\MediaVault\main.py`
  - Details: In the CONFIGURATION block (between `MAINFETCH_SCRIPT` on line 67 and the "Folder Naming Conventions" comment on line 69), add a new constant `DEVICE_ALIASES = {"movies": "FA69H0300200", "series": "FA75V0303405"}` with a 1-2 line comment explaining it maps human names to ADB serials. Then in the UTILITIES section (just after `load_library` / `save_library`, before `generate_short_id` around line 95), add:
    ```python
    def resolve_device(device_arg):
        """Map a CLI device alias to an ADB serial. Returns None if arg is None.
        Unknown aliases pass through as-is so any raw serial works."""
        if device_arg is None:
            return None
        return DEVICE_ALIASES.get(device_arg, device_arg)
    ```
    Do NOT touch any other code in this step.
  - Acceptance: `python main.py` (with no args) still prints the usage block (i.e. file parses cleanly). `python -c "import main; print(main.resolve_device('movies'), main.resolve_device('FA00X'), main.resolve_device(None))"` prints `FA69H0300200 FA00X None`.

- [x] 3. [model: sonnet] Thread `device_id` through `cmd_push` and replace the two ADB call sites.
  - Files: `C:\Users\harin\PycharmProjects\MediaVault\main.py`
  - Details:
    1. Change the signature on line 622 from `def cmd_push(manual_id, split_method=None, split_val=None, chunk_range=None):` to `def cmd_push(manual_id, split_method=None, split_val=None, chunk_range=None, device_id=None):`.
    2. Immediately after the line that prints `   > Target: {remote_target_dir}` (around line 648), add: `adb_base = ["adb", "-s", device_id] if device_id else ["adb"]`. Also add a print so the user sees which device they hit when one is set, e.g. `if device_id: print(f"   > Device: {device_id}")` — placed right above the `adb_base` assignment.
    3. Replace `subprocess.run(["adb", "shell", "mkdir", "-p", f"'{safe_remote_dir}'"], check=True)` (line 656) with `subprocess.run(adb_base + ["shell", "mkdir", "-p", f"'{safe_remote_dir}'"], check=True)`.
    4. Replace `subprocess.run(["adb", "push", "-p", f, remote_full_path], check=True)` (line 755) with `subprocess.run(adb_base + ["push", "-p", f, remote_full_path], check=True)`.
    5. Do NOT touch any other ADB call elsewhere in `main.py` (there are none in scope — verify with a grep before finishing).
  - Acceptance: `python main.py` still prints the usage block. Grep for `["adb"` inside `main.py` shows no remaining literal `["adb", ...]` list inside `cmd_push`. Calling `cmd_push(...)` with no `device_id` produces the exact same subprocess argv lists as today (the conditional collapses to `["adb"]`).

- [x] 4. [model: sonnet] Thread `device_id` through `cmd_push_group`, `cmd_prep_push_rep`, `cmd_prep_push_rep_season`.
  - Files: `C:\Users\harin\PycharmProjects\MediaVault\main.py`
  - Details:
    1. `cmd_push_group` (line 794): add `device_id=None` to signature; in the loop at line 839 change `cmd_push(mid, split_method, split_val)` to `cmd_push(mid, split_method, split_val, device_id=device_id)`. Do NOT pass `chunk_range` — that arg has no meaning at group level today and is not in scope.
    2. `cmd_prep_push_rep` (line 1368): add `device_id=None` to signature; change `cmd_push(manual_id, split_method, split_val)` at line 1380 to `cmd_push(manual_id, split_method, split_val, device_id=device_id)`.
    3. `cmd_prep_push_rep_season` (line 1410): add `device_id=None` to signature; change `cmd_push(mid, split_method, split_val)` at line 1465 to `cmd_push(mid, split_method, split_val, device_id=device_id)`.
    4. Do NOT change `cmd_replace` call sites — replace does not touch ADB.
  - Acceptance: `python main.py` still prints the usage block (file parses). All three functions accept `device_id=None` and pass it through unchanged when the caller omits it (default behaviour preserved).

- [x] 5. [model: sonnet] Wire `device <id_or_name>` into the CLI parser blocks for `push` and `push_group`.
  - Files: `C:\Users\harin\PycharmProjects\MediaVault\main.py`
  - Details:
    1. `push` parser (lines 1665-1696): in the `while i < len(args):` loop, add a new `elif args[i] == "device":` branch that, when followed by a value, sets `dev = args[i + 1]; i += 2`. Initialise `dev = None` next to `c_range = None`. At the bottom, change `cmd_push(mid, method, val, c_range)` to `cmd_push(mid, method, val, c_range, device_id=resolve_device(dev))`.
    2. `push_group` parser (lines 1698-1723): same shape — add `elif args[i] == "device":` branch, initialise `dev = None`, and change the final call to `cmd_push_group(group_id, method, val, ep_range, device_id=resolve_device(dev))`.
    3. Update the usage-block strings at lines 1536 / 1537 to include `[device <id_or_name>]`.
  - Acceptance: `python main.py push` (no args) shows the new usage line including `[device <id_or_name>]`. Running `python main.py push <known_id> device movies` (with no real phone connected — failure is expected) prints the new "Device: FA69H0300200" line before failing on the ADB mkdir call. Running without `device` prints no device line.

- [x] 6. [model: sonnet] Wire `device <id_or_name>` into the CLI parser for `prep_push_rep_season` and refactor `prep_push_rep` to a token scanner.
  - Files: `C:\Users\harin\PycharmProjects\MediaVault\main.py`
  - Details:
    1. `prep_push_rep_season` parser (lines 1577-1607): in the existing `while i < len(args):` loop, add an `elif arg == "device":` branch that sets a new `device_arg = args[i + 1]; i += 2`. Initialise `device_arg = None` next to `ep_range = None`. At the bottom, change `cmd_prep_push_rep_season(group_id, folder_path, method, val, ep_range)` to `cmd_prep_push_rep_season(group_id, folder_path, method, val, ep_range, device_id=resolve_device(device_arg))`.
    2. `prep_push_rep` parser (lines 1556-1575): this one currently uses the brittle `rest[-2] in [...]` shape. Replace the whole body with a token-scanner pattern modeled on `prep_push_rep_season`: walk `rest`, peel off `SIZE_MB`/`SIZE_GB`/`COUNT <val>` pairs and `device <val>` pairs into local variables, accumulate remaining tokens into `filepath_parts`, then `filepath = " ".join(filepath_parts)`. Finally call `cmd_prep_push_rep(mid, filepath, method, val, device_id=resolve_device(device_arg))`. This means `device` can appear anywhere after the ID without breaking filepath reconstruction (mirrors the constraint stated in the task brief).
    3. Update the usage-block strings at lines 1525 / 1526 to include `[device <id_or_name>]`.
  - Acceptance: `python main.py prep_push_rep` (no args) shows the new usage. `python main.py prep_push_rep_season` (no args) shows the new usage. Existing call shapes without `device` still parse to the same `(method, val, ep_range)` tuple as today — manually verify by reading the parser flow against one example like `prep_push_rep_season tv-en-2024-x "C:\Media\Series\Show S01" SIZE_MB 9900 episodes 1-5`.

- [x] 7. [model: haiku] Smoke-test the CLI end-to-end (no real ADB push).
  - Files: none modified
  - Details: Run, from the repo root, each of these and confirm the output:
    - `python main.py` -> prints usage; the push lines mention `[device <id_or_name>]`.
    - `python main.py push` -> "Usage: push [id] ..." (the existing usage error path).
    - `python -c "import main; print(main.resolve_device('movies'), main.resolve_device('series'), main.resolve_device('FA00XYZ'), main.resolve_device(None))"` -> `FA69H0300200 FA75V0303405 FA00XYZ None`.
    - `python -c "import main, inspect; print(inspect.signature(main.cmd_push)); print(inspect.signature(main.cmd_push_group)); print(inspect.signature(main.cmd_prep_push_rep)); print(inspect.signature(main.cmd_prep_push_rep_season))"` -> each signature must end with `device_id=None`.
  - Acceptance: All four checks pass. Do NOT attempt a real `adb push` — there is no test fixture for the phone.

- [x] 8. [model: sonnet] Update ARCHITECTURE.md and README.md with the new `device` option.
  - Files: `C:\Users\harin\PycharmProjects\MediaVault\ARCHITECTURE.md`, `C:\Users\harin\PycharmProjects\MediaVault\README.md`
  - Details: Hand off to the architect agent. Required updates:
    - **ARCHITECTURE.md §5** (Entry Points): update the `push`, `push_group`, `prep_push_rep`, `prep_push_rep_season` rows in the subcommand table to show the new `[device <id_or_name>]` suffix on the signature.
    - **ARCHITECTURE.md §7.5** (ADB push flow): add a short note at the top of the section explaining that `cmd_push` now accepts a `device_id` kwarg, that it prepends `-s <serial>` to every ADB call when set, and that `None` (the default) preserves the old behaviour exactly. Also update the closing paragraph that currently says "ADB device detection is implicit" to reflect the new opt-in selector.
    - **ARCHITECTURE.md §14** (Configuration constants): add a new row for `DEVICE_ALIASES` (in `main.py`, near line 68) with the current dict value, and a sentence saying it's a hardcoded user-edited mapping.
    - **ARCHITECTURE.md §18** (Quick Reference): add a row "How to pin a push to a specific phone" -> "`main.py:resolve_device()` + `cmd_push(... device_id=...)`; alias dict at `main.py:DEVICE_ALIASES`".
    - **README.md**: add a short subsection under the push command docs (or wherever push usage lives) showing the new syntax with an example for each of the four commands. Mention that aliases are edited in source today (matching the project's hardcoded-config convention noted in §14).
    - Do NOT add new sections to ARCHITECTURE.md beyond what's listed; keep changes surgical and matching the existing tone (the document is already very long).
  - Acceptance: A diff of `ARCHITECTURE.md` shows only the four bulleted updates above. A diff of `README.md` shows the new device-pinning example. Both documents still render cleanly (no broken markdown tables).

## Risks and edge cases
- **Currently-checked-out branch is `feature/video_dummy`.** The task requires branching from `main`, not from the current HEAD. Step 1 must explicitly base the new branch on `origin/main` (or local `main` after a fast-forward). If the local `main` is stale, fetch first.
- **`prep_push_rep`'s old parser** treats the LAST two args as the SIZE/COUNT pair. Refactoring to a token scanner is a behavioural change for one obscure case: a filepath whose last two path components literally are `SIZE_MB <number>`. That's vanishingly unlikely in practice (movie/episode filenames don't look like that), but worth noting — the scanner will mis-interpret such a filepath as a split directive. Acceptable risk; matches how `prep_push_rep_season` already works.
- **Unknown alias passthrough.** `resolve_device("typoo")` returns `"typoo"`, which then becomes `adb -s typoo ...` and ADB will error with "device 'typoo' not found". This is the desired behaviour (so any raw serial works), but it means a typo in the alias name silently becomes a runtime ADB error rather than a config-time error. Acceptable.
- **Hardcoded serials in source.** The two Pixel serials live in `DEVICE_ALIASES` in code, matching the project's hardcoded-config convention (ARCHITECTURE.md §14). If those phones are swapped, the user edits the dict. This is consistent with how every other "config" works in this project — no env vars, no JSON config.
- **Other ADB call sites.** A grep before Step 3 finishes should confirm `["adb"` appears only inside `cmd_push`. If a future ADB call gets added elsewhere (e.g. a hypothetical `adb devices` check), the same `adb_base` pattern would need to be threaded — out of scope for now.
- **No real-phone test.** The smoke test (Step 7) only exercises argument parsing and helper logic. A genuine multi-device push test requires both Pixels plugged in — only the user can run that. Plan accepts this as a limitation.

## Verification
After all steps complete, run from the repo root:

1. `python main.py` — usage block prints; push lines include `[device <id_or_name>]`.
2. `python -c "import main; print(main.resolve_device('movies'))"` -> `FA69H0300200`.
3. `python -c "import main; print(main.resolve_device('series'))"` -> `FA75V0303405`.
4. `python -c "import main; print(main.resolve_device('FA00FOO'))"` -> `FA00FOO`.
5. `python -c "import main; print(main.resolve_device(None))"` -> `None`.
6. `python -c "import main, inspect; [print(n, inspect.signature(getattr(main, n))) for n in ['cmd_push','cmd_push_group','cmd_prep_push_rep','cmd_prep_push_rep_season']]"` — every signature ends with `device_id=None`.
7. Grep check: searching `main.py` for `subprocess.run(["adb"` returns **no** matches (every ADB subprocess call should now go through `adb_base`).
8. Optional, with one phone connected: `python main.py push <some_local_ready_id> SIZE_MB 9900 device movies` should print "Device: FA69H0300200" then proceed (or fail with a real ADB error). Without `device`, no device line prints and behaviour is unchanged.
9. `git diff main -- main.py` shows changes confined to: config constants block, one new helper function, four function signatures, two `subprocess.run` call sites, and four CLI parser blocks. No unrelated edits.

## Out of scope
- Per-device `REMOTE_ROOT` (e.g. different `/sdcard/...` paths per phone). Today one constant; stays one constant.
- Auto-detection of multiple connected devices via `adb devices` parsing. The user is fine with the normal ADB error message when no `device` is given and multiple are connected.
- Moving `DEVICE_ALIASES` into a config file or env var. Matches existing hardcoded-config convention (ARCHITECTURE.md §14).
- Touching `mainfetch.py`. Mainfetch has no ADB code.
- Touching any other ADB-adjacent command (`cmd_replace`, `cmd_check`, `cmd_restore`, `cmd_repair_dummies`, etc.). None of those shell out to ADB.
- Threading `device_id` through `cmd_replace`, `cmd_replace_group`, `cmd_restore`, `cmd_restore_group`, `cmd_fetch_restore`. They do not push.
- Updating the usage `print("  fetch [id]")` and other non-push subcommand lines.
- Adding any test infrastructure (project has no tests; ARCHITECTURE.md §13).
