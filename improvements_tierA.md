# Improvements — Tier A · Code Architecture & Refactoring

> Structural cleanup that reduces divergence between files and makes every future change cheaper. None of these change runtime behaviour on the happy path; they make the codebase legible, testable, and ready for new features (Tiers D–F and the new Tiers S/U build on top of them).

> **Cross-cutting context** that applies to many items below:
> - The two active files are `main.py` (3081 lines as of 2026-06-12) and `mainfetch.py` (491 lines) at the project root, plus shared `mvcommon.py` (168 lines). Everything under `archive/` is git history; do not touch.
> - The library JSON files (`C:\Media\library_movies.json`, `library_series.json`, `library_anime.json`) are the source of truth. Live snapshots are mirrored read-only under `resources/` (gitignored).
> - `cmd_restore`'s old blind `entry["hash"]` overwrite after merge has been REPLACED by a verifiable canonical re-hash. `mkvmerge`'s *default* merge is non-deterministic, but `mkvmerge --deterministic <seed>` produces a byte-identical container, so split entries now get a verifiable canonical whole-file hash (blessed at first restore or eager-push→promote-at-replace). See `docs/feature-split-hash-deterministic/`.
> - The Aindham Vedham orphan parent_id was the only known data-integrity case and was manually patched on 2026-05-25. Today's `cmd_prep` would not reproduce that state.
> - **Attribute key (added 2026-06-12):** `Risk` = blast radius of MAKING the change (which working commands/behaviors it touches). `If skipped` = the failure or limitation that persists if this is never done, with a concrete scenario.

---

## IMP-A1: Extract shared module `mvcommon.py`

- Category: refactor
- Priority: high
- Files: new `mvcommon.py`; modifies `main.py`, `mainfetch.py`
- Current behavior: `load_library`, `calculate_file_hash`, and the three `LIBRARY_*` path constants plus `LOCAL_ROOT`, `MKVMERGE_PATH`, `SPLIT_DIR_NAME`, `CHECKSUM_DIR_NAME`, `RESTORE_DIR_NAME`, `VIDEO_EXTENSIONS` are duplicated verbatim between `main.py` (lines 20-33, 39-54, 94-108) and `mainfetch.py` (lines 27-46, 52-80, 83-97). The two `load_library` copies have **already drifted**: `main.load_library` calls `sys.exit(1)` on corrupt JSON, while `mainfetch.load_library` swallows the exception with a bare `except: pass` and treats a corrupt file as empty.
- Proposed change:
  - Create `mvcommon.py` at project root.
  - Move the constants (paths, folder names, extensions) and the helpers (`load_library`, `save_library`, `calculate_file_hash`, `generate_short_id`, `human_readable_size`, `parse_size_str`) into it.
  - Make `save_library` available to `mainfetch.py` too (currently mainfetch never writes; this opens the door for fetch-side state updates such as IMP-C1 progress, IMP-E5 cloud-confirmation, etc.).
  - Both entry points just `from mvcommon import ...`.
  - Adopt `main.py`'s strict error handling (`sys.exit(1)` on corrupt load, atomic temp-file save) as the unified contract.
- Rationale: One drift case is already in production. Every shared change today has to be made twice and tested twice. The asymmetric error handling between the two `load_library`s means a corrupt library fails LOUDLY for `main.py` users but SILENTLY (zero entries found) for `mainfetch.py` users.
- Goal: A single source of truth for library I/O and hashing. Eliminates the drift category permanently and is the foundation for IMP-A6 (type hints), IMP-A7 (pytest), and several Tier C robustness items.
- Effort estimate: small
- Status: done (refactor/extract_mvcommon, PR to main 2026-05-30)

---

## IMP-A2: Migrate CLI parsing from manual sys.argv to argparse

- Category: refactor
- Priority: high
- Files: `main.py` (entire `if __name__ == "__main__"` block, now lines 2764-3081), `mainfetch.py` (lines 480-491)
- Current behavior: Both entry points walk `sys.argv` by hand with nested `if/elif` chains, manual keyword sniffing for `SIZE_MB`/`SIZE_GB`/`COUNT`/`episodes`/`chunks`/`device`/`rehash`/`tempdir`. The `episodes` literal-keyword footgun is documented in ARCHITECTURE §5: `fetch tv-X 1-3` silently ignores the range because `sys.argv[3] != "episodes"`. There is no `--help`, no type validation, no default values, no error if a required arg is missing. The 2026-06-12 review found two concrete parser bugs this would erase wholesale: the `push_group` infinite loop on a trailing keyword and the `mainfetch.py` bare-invocation IndexError (both tracked as IMP-C14 for the quick tactical fix).
- Proposed change:
  - Migrate to stdlib `argparse` with subparsers (one per `cmd_*`).
  - Replace positional keyword arguments with proper flags: `--episodes 1-3`, `--size-mb 9900`, `--size-gb 10`, `--count 6`, `--chunks 1-4`, `--device <serial>`, `--rehash`, `--tempdir <path>`, `--force` (foundation for IMP-D10 ID validator), `--dry-run` (foundation for IMP-D5 repair_library), `--json` (foundation for IMP-A4), `--quiet`, `--verbose`.
  - Keep BACKWARDS-COMPATIBLE shims for the existing positional forms during a transition window so usage_commands.txt history still works. A small dispatch layer can detect "old shape" vs "new shape" by checking if the first non-id arg starts with `-`.
- Rationale: The `episodes` footgun has cost real time (visible in usage_commands.txt where the user re-types the same command 2–3 times). `argparse` gives `--help` and validation for free, and is the prerequisite for ANY new flag in Tiers C/D and for the daemon (Tier S) reusing command plumbing cleanly.
- Goal: Self-documenting CLI with `python main.py --help` and `python main.py <subcmd> --help`. No more silently-ignored arguments. Lays the groundwork for flag-driven options across all future commands.
- Effort estimate: medium
- Risk: high — rewrites the argv entry layer of EVERY command; a parsing regression could mis-route arguments for daily-driver commands (push/replace/restore). Mitigate with the compat shim + a parser-equivalence test matrix (old form vs new form → same cmd_* call) before merging.
- If skipped: every new option keeps being hand-spliced into five separate while-loops (the `rehash`/`tempdir` tokens already had to be added in 4 places each); the `episodes` silent-ignore footgun keeps eating real sessions — e.g. `fetch tv-TheBoys 1-3` quietly fetches NOTHING ranged and the user discovers it after the 5-minute harvester timeout.
- Status: pending

---

## IMP-A3: Replace print() with the logging module

- Category: code quality
- Priority: medium
- Files: `main.py` (~250 `print(...)` calls), `mainfetch.py` (~80 `print(...)` calls), new `mvlog.py` or section of `mvcommon.py`
- Current behavior: Every status/progress/error line is `print(f"emoji {msg}")`. There is no log file. A failed `cmd_prep_push_rep_season` running for 3 hours has only the terminal scrollback; if the terminal was closed or the buffer was exceeded, the failure context is lost.
- Proposed change:
  - Configure stdlib `logging` with two handlers: a colored console formatter that mirrors today's emoji output (for human readability), and a rotating file handler at `~/.mediavault/logs/YYYY-MM-DD.log` (or under `C:\Media\logs\`) writing structured records.
  - Standard levels: DEBUG (chunk-level detail), INFO (each cmd_* boundary), WARNING (resumable failures), ERROR (aborted operation), CRITICAL (data-integrity risk).
  - Generate a `correlation_id` (uuid4 prefix) at the top of each `cmd_*` invocation and include it in every log line so a single batch run can be grep'd out of a busy log file.
  - Keep the print-friendly emojis on the console only; the file log gets plain text.
- Rationale: The user runs hours-long batch operations (Mr Robot S04 push of 13 episodes, full anime seasons). When something fails partway, the only diagnostic today is "scroll up if you can". A persistent log is the foundation for IMP-D14 (`tail-progress`), the web UI (IMP-E12), and — critically — the **Tier S daemon**, which has no terminal at all: a background service without a log file is undebuggable.
- Goal: Every run leaves a forensics trail. Batch failures can be debugged after the fact without re-running. Foundation for any monitoring/notification feature.
- Effort estimate: medium
- Risk: medium — touches every command's output path; emoji console behavior must stay byte-comparable (tests assert on emoji markers, e.g. `test_mvcommon` checks `🔍`). Pure-additive file handler first, console swap second.
- If skipped: the Tier S daemon ships blind — when an overnight background fetch fails at 3 AM there is NO record of why; the user wakes to a "fetch failed" tile and has to re-run interactively hoping it reproduces. Already true today for closed-terminal batch runs.
- Status: pending

---

## IMP-A4: Add `--json` output mode to all read-only commands

- Category: refactor
- Priority: high
- Files: `main.py` (every `cmd_*` that prints structured data — `local_status`, `scan_unprepped`, `check`, `verify_restore`, future `library_stats` D1, `where_is` D3, `verify_library` D4)
- Current behavior: All output is human-formatted emoji prose intended for interactive reading. There is no machine-readable surface. The Jellyfin-integration daemon (Tier S), the future Apple TV UI ([[project_future_apple_tv_ui]]), and any web integration (IMP-E12) would have to scrape `print` output, which is brittle and a maintenance trap.
- Proposed change:
  - After IMP-A2 (argparse), every read-only command accepts `--json`.
  - With `--json`, suppress all `print` calls and emit a single `json.dumps(result, indent=2)` of a well-defined schema at the end.
  - Schema for each command should be documented inline (docstring) and validated in tests (IMP-A7).
  - Example payload for `local_status --json`:
    ```json
    { "total_pending": 120, "total_pending_bytes": 1234567890,
      "items": [{ "id": "...", "filename": "...", "size_bytes": ... }],
      "selected_for_batch": [...], "limit_bytes": ... }
    ```
  - Mutation commands (`push`, `replace`, `restore`) get `--json` too, emitting a final result object with success/failure and side-effect summary.
- Rationale: This is the **hard prerequisite for the Tier S daemon and the couch UI path**: the daemon must know "what is archived", "what failed and why", and "what resume command applies" without parsing emoji. Building those on top of prose scraping is a non-starter.
- Goal: A stable machine-readable contract for every command. Lets the daemon/UI consume `main.py` as a backend without scraping stdout.
- Effort estimate: medium
- Risk: medium — adds a parallel output path through many commands; the human path must remain untouched when `--json` is absent (guard with output-equivalence tests). Depends on A2.
- If skipped: the Tier S daemon must wrap `cmd_*` Python functions directly and interpret their `print` side effects / boolean returns — workable but fragile, and every internal print-format tweak becomes a daemon-breaking change. Example: parsing "✅ SUCCESS." vs "✅ Partial Upload Complete" to distinguish outcomes.
- Status: pending

---

## IMP-A5: External configuration file `mvconfig.json`

- Category: refactor
- Priority: medium
- Files: new `mvconfig.json` (gitignored, with a checked-in `mvconfig.example.json`), `main.py`, `mainfetch.py`, `mvcommon.py`
- Current behavior: Every path and tunable is a hardcoded Python constant at the top of each file: `LIBRARY_*` paths, `LOCAL_ROOT`, `REMOTE_ROOT`, `MKVMERGE_PATH`, `FFMPEG_PATH`, `CHROME_PROFILES` map, `CHROME_PROFILE_NAME`, `SYSTEM_DOWNLOADS_FOLDER`, `DEVICE_ALIASES`, `PUSH_VERIFY_REMOTE` (the IMP-C8 toggle explicitly waiting on this task), balanced-split `+10 MB` buffer, hash block size (65536), fetch base_timeout (300s), Replace retry count (3), language priority map (en=1, ta=2, hi=3). Changing any of them = source edit.
- Proposed change:
  - Add `mvconfig.json` loaded once at startup by `mvcommon.py`. Schema-validate it.
  - Keep the current values as defaults so an absent config file is a no-op.
  - Surface ALL of the above as config keys, plus future ones: retry counts/backoffs (IMP-C2), TMDB API key (IMP-E3), daemon port/webhook secret (Tier S), etc.
  - Add a `python main.py config show` and `config set <key> <value>` for ergonomic editing.
- Rationale: Today's hardcoded paths make MediaVault non-portable to another machine (different drive letter, different Chrome install path). It also makes "I want to try splitting at 8000 MB this time" require a source edit + remember-to-revert. IMP-C8's `PUSH_VERIFY_REMOTE` flag is already parked waiting for this.
- Goal: One config file owns all tunables. The codebase is portable to a fresh machine with zero edits. Tests can inject test config easily.
- Effort estimate: small
- Risk: medium — every module reads constants at import time today; moving to config-at-startup changes initialization order (test fixtures monkeypatch `mvcommon.LIBRARY_*` and `main.LIBRARY_*` bindings — the sandbox fixture and docs/testing-strategy.md §6.3 dual-binding hazard must be updated in the same PR).
- If skipped: enabling IMP-C8 remote verification keeps requiring a source edit; any second machine (or a rebuilt Alienware) needs hand-patched sources; the Tier S daemon's settings (port, policy timers) end up as yet more hardcoded constants.
- Status: pending

---

## IMP-A6: Type hints and mypy strict on the shared module

- Category: code quality
- Priority: low
- Files: `mvcommon.py`, eventually `main.py` and `mainfetch.py` (incrementally)
- Current behavior: Zero type hints anywhere. Function signatures like `cmd_push(manual_id, split_method=None, split_val=None, chunk_range=None, device_id=None, eager_rehash=False, temp_dir=None)` give no information about what types those args accept. IDE autocomplete (the user opened `main.py` in PyCharm) is starved of information.
- Proposed change:
  - Add complete type hints to `mvcommon.py` first (smallest surface, highest reuse).
  - Define `TypedDict`s for the entry schema (`LeafEntry`, `SeasonMap`, `MultiEpAlias`, `SplitInfo`, `Chunk`, `TechSpec`, `Metadata`) so dictionary access becomes type-checked.
  - Run `mypy --strict mvcommon.py` in CI (IMP-A7 follow-up).
  - Gradually annotate the rest of the codebase; gate with `# type: ignore` where genuine `Any` is justified.
- Rationale: The dict-of-dict library structure is the most error-prone shape in Python without help. The Aindham Vedham orphan was a TypedDict invariant violation (a `parent_id` that didn't resolve), and the 2026-06-12 alias crash findings (IMP-C12/C13) are exactly the "entry kind not narrowed before key access" bug class a `LeafEntry | SeasonMap | MultiEpAlias` union would have flagged at write time.
- Goal: PyCharm autocomplete works fully. Renames are safe. New contributors see the schema in the code.
- Effort estimate: medium (incremental)
- Risk: low — annotations only; no runtime change (TypedDicts are erased). Worst case is mypy noise.
- If skipped: the alias-crash bug class stays representable — the next new entry `type` (there WILL be one; season_map and multi_ep_alias arrived within a year of each other) repeats the IMP-C12 pattern in whatever iterators it touches.
- Status: pending

---

## IMP-A7: Pytest harness with library fixtures

- Category: code quality
- Priority: medium
- Files: `tests/` (conftest.py + test files)
- Current behavior (2026-05-25, superseded): zero automated tests existed.
- Proposed change: bootstrap a pytest harness with sandboxed library fixtures, regex/round-trip/atomic-save coverage, and a split test.
- Rationale: Manual integration tests via real pushes are the slowest, most expensive feedback loop.
- Goal: Catch regressions in 5 seconds instead of mid-push 90 minutes in. Enable confident refactoring.
- Effort estimate: medium
- Status: done (delivered organically: PR #14 bootstrapped `tests/` + conftest fixtures, and PRs #18-#21 grew it to 13 files — sandbox/sandbox_entry/fake_dummy/mock_device/FakeAdb fixtures, library round-trip, episode-parse matrix, rollback scenario matrix; see `docs/testing-strategy.md`. Marked done 2026-06-12 in the fable-review; remaining wishes from the original text — CI integration, a real-mkvmerge split test — are folded into IMP-A12.)

---

## IMP-A8: Delete dead code in mainfetch.py

- Category: code quality
- Priority: low
- Files: `mainfetch.py` lines 177-179 (`wait_for_download`), 182-184 (`automation_download_file`), 396-435 (`build_download_queue`)
- Current behavior: Three functions are kept "for compatibility":
  - `wait_for_download(filename_snippet, timeout=300)` — empty body, returns None. Not called from anywhere.
  - `automation_download_file(driver, search_queries, filename_expected, dest_folder, target_index=0)` — empty body, returns False. Not called from anywhere.
  - `build_download_queue(entries)` — full implementation (40 lines) that's effectively dead because `fetch_single_entry` inlines an equivalent queue builder. Not called from `cmd_fetch_route`.
- Proposed change:
  - Delete all three function bodies and any imports they uniquely require.
  - Add a one-line comment in each deletion commit noting the architectural intent (e.g., "removed empty wait_for_download stub; harvester loop replaced this in 2025-12-XX commit").
- Rationale: Dead code creates two failure modes. (a) A future contributor wires up a stub and calls it, then can't figure out why nothing happens. (b) Tests targeting dead code provide false confidence. The "kept for compatibility" comment is misleading — nothing external calls these.
- Goal: 60-ish lines lighter codebase. New readers see exactly what runs.
- Effort estimate: small
- Risk: low — deletions of provably-uncalled code; grep + test suite confirm.
- If skipped: harmless today, but `build_download_queue` silently drifts from the inline queue builder it duplicates (it already lacks no logic-difference guarantees) — a future fetch fix applied to only one of the two wastes a debugging session.
- Status: pending

---

## IMP-A9: Fix the [eE|xX] character-class typo in cmd_prep

- Category: bug
- Priority: low
- Files: `main.py` line 859 (auto-parent regex in `cmd_prep`)
- Current behavior: The regex `re.match(r"^(.*)[eE|xX](\d+(?:\.\d+)?)$", manual_id)` includes a literal `|` inside the character class. Inside `[]`, the pipe is NOT alternation — it is a literal character. So today the regex matches `e`, `E`, `|`, `x`, or `X`. Harmless because no real manual_id contains a literal pipe, but it is a confusing trap for anyone reading the code.
- Proposed change:
  - Change `[eE|xX]` to `[eExX]` (drop the literal pipe).
  - Same fix may apply to other regexes in the same family if grep finds them; search for `[eE|xX]` and `[eExX]` across both files.
  - Add a unit test (extending `test_prep_season_episode_parse.py`) confirming `re.match(...).group(2)` returns the same value for representative IDs before and after.
- Rationale: Code clarity. The current pattern looks like the author thought `|` is OR inside `[]`. A new contributor seeing this will either copy the mistake or waste time confirming it works.
- Goal: Zero-behaviour-change cleanup that removes a confusing antipattern.
- Effort estimate: small
- Risk: low — one character class; covered by the existing episode-parse test file.
- If skipped: purely cosmetic risk — a copied-by-example regex with the same typo lands somewhere it DOES matter (e.g., an id sanitizer where a literal `|` becomes accepted input).
- Status: pending

---

## IMP-A10: Truth-up requirements.txt (add requests + webdriver-manager, drop undetected-chromedriver decision)

- Category: bug
- Priority: medium
- Files: `requirements.txt`, `README.md` (install section)
- Current behavior: `requirements.txt` lists `pymediainfo`, `undetected-chromedriver`, `selenium`. But `main.py` imports `requests` (set_poster/set_fanart) and `mainfetch.py` imports `webdriver_manager` — both missing, so a clean `pip install -r requirements.txt` produces an environment where `prep` works but `set_poster` crashes (`ModuleNotFoundError: requests`) and `mainfetch` warns + cannot start a driver. Meanwhile `undetected-chromedriver` is installed but never imported. Known in ARCHITECTURE §16 since 2026-05-25; README documents the workaround instead of the fix.
- Proposed change:
  - Add `requests` and `webdriver-manager` to `requirements.txt`.
  - Decide `undetected-chromedriver`: either delete the line (clean) or keep with a `# reserved: anti-bot fallback for Google Photos (see RESEARCH_STORAGE_STREAMING.md §1.3)` comment — recommend KEEP with comment, since Google bot-detection is a live risk and the package is the ready lever.
  - Simplify README's install section accordingly.
- Rationale: A rebuilt machine (or the future daemon's service environment) installs from requirements; today that gives a half-working install with two delayed-fuse crashes.
- Goal: `pip install -r requirements.txt` yields a fully functional environment; README no longer documents a workaround.
- Effort estimate: small
- Risk: low — dependency manifest only; no code change.
- If skipped: every machine rebuild / venv refresh re-discovers the gap at the worst time (mid-fetch `ModuleNotFoundError` after a Chrome update forces a driver refetch).
- Status: pending

---

## IMP-A11: Repo hygiene — root scratchpads, stray files, stale worktrees

- Category: code quality
- Priority: low
- Files: `.gitignore`, root `STATUS.md`, `2026-06-07.md`, `step2_validate.ps1`, root transcript `.txt` dumps, `.claude/worktrees/*`
- Current behavior (2026-06-12 audit):
  - Root `STATUS.md` is a **tracked** per-run pipeline scratchpad (committed by PRs #14/18/19/21, content perpetually stale — currently auto-rollback-era) while its sibling `PLAN.md` is correctly gitignored; the canonical copies live under `docs/<feature>/`.
  - `2026-06-07.md` is an empty stray file at root; six transcript `.txt` dumps sit at root (gitignored since PR #15 but still on disk).
  - `step2_validate.ps1` is a one-off GitHub-token validation helper stranded at root.
  - Two **locked leftover agent worktrees** (`.claude/worktrees/agent-a7378bcf…`, `agent-a79b36f…`) hold stale tier-file copies and pollute `Glob`/`Grep` results.
- Proposed change:
  - Gitignore root `STATUS.md` (and `git rm --cached` it) to match the PLAN.md convention; update `docs/git-pr-conventions.md` §"Plan & docs artifacts" to mention STATUS.md explicitly.
  - Delete `2026-06-07.md`; move `step2_validate.ps1` to `archive/transcripts/` or `tools/` (decide); optionally relocate the root transcript dumps into `archive/transcripts/`.
  - Inspect the two worktrees for uncommitted work, then `git worktree remove` (+ delete their branches if merged/abandoned).
- Rationale: Each item is tiny, but together they make every repo-wide search/grep noisier and confuse future agents (the stale worktree tier files almost polluted this review's IMP inventory).
- Goal: `git status` clean, root directory minimal, searches return only live files.
- Effort estimate: small
- Risk: low — no runtime code touched; worktree removal needs the uncommitted-work check first (the lock flag suggests deliberate keeping — confirm with the user before removing).
- If skipped: future sessions keep tripping over stale copies (this review's tier-status grep matched worktree duplicates), and the tracked-but-stale STATUS.md keeps shipping misleading "current task" content to anyone reading the repo on GitHub.
- Status: pending

---

## IMP-A12: CI pipeline (GitHub Actions) for the test suite

- Category: code quality
- Priority: medium
- Files: new `.github/workflows/ci.yml`, possibly `requirements-dev.txt`
- Current behavior: The 13-file pytest suite runs only when someone remembers to run `pytest -q` locally. Nothing runs on push/PR; a regression can merge silently (the suite exists BECAUSE regressions were expensive — but it's only as good as its invocation discipline). This absorbs the CI + real-mkvmerge-split leftovers from the original IMP-A7 scope.
- Proposed change:
  - GitHub Actions workflow: `windows-latest`, Python 3.11, `pip install -r requirements.txt -r requirements-dev.txt`, `pytest -q`. (Windows runner because the code is Windows-pathed; the suite's sandbox fixtures already avoid real `C:\Media`.)
  - Gate: required status check on PRs into `main` (pairs with the existing human merge gate).
  - Stretch: a second job that installs MKVToolNix (choco) and runs an `@pytest.mark.mkvmerge` split/merge round-trip on a generated 30 MB file (the original A7 stretch goal, still unbuilt).
- Rationale: The repo's merge discipline is strong (human-gated PRs) but verification is manual. Every PR in the history says "pytest -q → N passed" — automate the claim.
- Goal: A red ✗ on a PR before a human ever reviews it; the mkvmerge job exercises the only logic the mock suite can't (real container splitting).
- Effort estimate: small
- Risk: low — additive workflow file; no runtime code. Main risk is runner-path assumptions in fixtures (sandbox already tmp-pathed).
- If skipped: a future PR (or a multi-candidate pipeline run) merges with a silently broken suite — the exact failure mode the suite was built to prevent, one `pytest` invocation away from being caught.
- Status: pending
