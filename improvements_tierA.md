# Improvements — Tier A · Code Architecture & Refactoring

> Structural cleanup that reduces divergence between files and makes every future change cheaper. None of these change runtime behaviour on the happy path; they make the codebase legible, testable, and ready for new features (Tiers D–F build on top of them).

> **Cross-cutting context** that applies to many items below:
> - The two active files are `main.py` (1621 lines) and `mainfetch.py` (507 lines) at the project root. Everything under `archive/` is git history; do not touch.
> - The library JSON files (`C:\Media\library_movies.json`, `library_series.json`, `library_anime.json`) are the source of truth. Live snapshots are mirrored read-only under `resources/` (gitignored).
> - `cmd_restore`'s old blind `entry["hash"]` overwrite after merge has been REPLACED by a verifiable canonical re-hash. `mkvmerge`'s *default* merge is non-deterministic, but `mkvmerge --deterministic <seed>` produces a byte-identical container, so split entries now get a verifiable canonical whole-file hash (blessed at first restore or eager-push→promote-at-replace). See `docs/feature-split-hash-deterministic/`.
> - The Aindham Vedham orphan parent_id was the only known data-integrity case and was manually patched on 2026-05-25. Today's `cmd_prep` would not reproduce that state.

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
- Files: `main.py` (entire `if __name__ == "__main__"` block, lines 1390-1622), `mainfetch.py` (lines 495-507)
- Current behavior: Both entry points walk `sys.argv` by hand with nested `if/elif` chains, manual keyword sniffing for `SIZE_MB`/`SIZE_GB`/`COUNT`/`episodes`/`chunks`. The `episodes` literal-keyword footgun is documented in ARCHITECTURE §5: `fetch tv-X 1-3` silently ignores the range because `sys.argv[3] != "episodes"`. There is no `--help`, no type validation, no default values, no error if a required arg is missing.
- Proposed change:
  - Migrate to stdlib `argparse` with subparsers (one per `cmd_*`).
  - Replace positional keyword arguments with proper flags: `--episodes 1-3`, `--size-mb 9900`, `--size-gb 10`, `--count 6`, `--chunks 1-4`, `--device <serial>` (foundation for IMP-C4), `--force` (foundation for IMP-D10 ID validator), `--dry-run` (foundation for IMP-D5 repair_library), `--json` (foundation for IMP-A4), `--quiet`, `--verbose`.
  - Keep BACKWARDS-COMPATIBLE shims for the existing positional forms during a transition window so usage_commands.txt history still works. A small dispatch layer can detect "old shape" vs "new shape" by checking if the first non-id arg starts with `-`.
- Rationale: The `episodes` footgun has cost real time (visible in usage_commands.txt where the user re-types the same command 2–3 times). `argparse` gives `--help` and validation for free, and is the prerequisite for ANY new flag in Tiers C/D.
- Goal: Self-documenting CLI with `python main.py --help` and `python main.py <subcmd> --help`. No more silently-ignored arguments. Lays the groundwork for flag-driven options across all future commands.
- Effort estimate: medium
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
- Rationale: The user runs hours-long batch operations (Mr Robot S04 push of 13 episodes, full anime seasons). When something fails partway, the only diagnostic today is "scroll up if you can". A persistent log is the foundation for IMP-D14 (`tail-progress`) and the Telegram dispatch (IMP-E10).
- Goal: Every run leaves a forensics trail. Batch failures can be debugged after the fact without re-running. Foundation for any monitoring/notification feature.
- Effort estimate: medium
- Status: pending

---

## IMP-A4: Add `--json` output mode to all read-only commands

- Category: refactor
- Priority: high
- Files: `main.py` (every `cmd_*` that prints structured data — `local_status` 1071, `scan_unprepped` 1155, `check` 514, `verify_restore` 831, future `library_stats` D1, `where_is` D3, `verify_library` D4)
- Current behavior: All output is human-formatted emoji prose intended for interactive reading. There is no machine-readable surface. The future Apple TV UI ([[project_future_apple_tv_ui]]) and any web/Telegram integration (IMP-E10, E12) would have to scrape `print` output, which is brittle and a maintenance trap.
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
- Rationale: This is the **hard prerequisite for the future Apple TV UI**, the web command (IMP-E12), the Telegram bot (IMP-E10), and any external integration. Building those on top of emoji-prose scraping is a non-starter.
- Goal: A stable machine-readable contract for every command. Lets the future UI consume `main.py` as a backend without scraping stdout.
- Effort estimate: medium
- Status: pending

---

## IMP-A5: External configuration file `mvconfig.json`

- Category: refactor
- Priority: medium
- Files: new `mvconfig.json` (gitignored, with a checked-in `mvconfig.example.json`), `main.py` (15-32), `mainfetch.py` (25-46)
- Current behavior: Every path and tunable is a hardcoded Python constant at the top of each file: `LIBRARY_*` paths, `LOCAL_ROOT`, `REMOTE_ROOT`, `MKVMERGE_PATH`, `CHROME_PROFILES` map, `CHROME_PROFILE_NAME`, `SYSTEM_DOWNLOADS_FOLDER`, balanced-split `+10 MB` buffer, hash block size (65536), fetch base_timeout (300s), Replace retry count (3), language priority map (en=1, ta=2, hi=3). Changing any of them = source edit.
- Proposed change:
  - Add `mvconfig.json` loaded once at startup by `mvcommon.py` (after IMP-A1). Schema-validate it.
  - Keep the current values as defaults so an absent config file is a no-op.
  - Surface ALL of the above as config keys, plus future ones: ADB device serial (IMP-C4), retry counts/backoffs (IMP-C2), Telegram bot token (IMP-E10), TMDB API key (IMP-E3), etc.
  - Add a `python main.py config show` and `config set <key> <value>` for ergonomic editing.
- Rationale: Today's hardcoded paths make MediaVault non-portable to another machine (different drive letter, different Chrome install path, different Pixel ADB layout). It also makes "I want to try splitting at 8000 MB this time" require a source edit + remember-to-revert.
- Goal: One config file owns all tunables. The codebase is portable to a fresh machine with zero edits. Tests can inject test config easily.
- Effort estimate: small
- Status: pending

---

## IMP-A6: Type hints and mypy strict on the shared module

- Category: code quality
- Priority: low
- Files: `mvcommon.py` (from IMP-A1), eventually `main.py` and `mainfetch.py` (incrementally)
- Current behavior: Zero type hints anywhere. Function signatures like `cmd_push(manual_id, split_method=None, split_val=None, chunk_range=None)` give no information about what types those args accept. IDE autocomplete (the user opened `main.py` in PyCharm) is starved of information.
- Proposed change:
  - Add complete type hints to `mvcommon.py` first (smallest surface, highest reuse).
  - Define `TypedDict`s for the entry schema (`LeafEntry`, `SeasonMap`, `SplitInfo`, `Chunk`, `TechSpec`, `Metadata`) so dictionary access becomes type-checked.
  - Run `mypy --strict mvcommon.py` in CI (IMP-A7).
  - Gradually annotate the rest of the codebase; gate with `# type: ignore` where genuine `Any` is justified.
- Rationale: The dict-of-dict library structure is the most error-prone shape in Python without help. The Aindham Vedham orphan was a TypedDict invariant violation (a `parent_id` that didn't resolve). Type-checked dicts make a class of bugs unrepresentable.
- Goal: PyCharm autocomplete works fully. Renames are safe. New contributors see the schema in the code.
- Effort estimate: medium (incremental)
- Status: pending

---

## IMP-A7: Pytest harness with library fixtures

- Category: code quality
- Priority: medium
- Files: new `tests/` (the placeholder directory already exists), new `tests/conftest.py`, new `tests/test_*.py`
- Current behavior: Zero automated tests. The architecture explicitly notes the legacy snapshots under `archive/` serve as "informal regression baselines". Every fix is verified manually by running a real push.
- Proposed change:
  - Use the gitignored `resources/library_*.json` snapshots as read-only fixtures.
  - Initial test coverage (no Selenium / no ADB / no real mkvmerge yet):
    - Auto-parent detection regex on every ID convention in the wild — canonical movies, canonical series, anime, Chernobyl shortcut (`tv-en-2019-chernobyle01`), Kuroko hybrid (`ani-...-s0125`), half-eps (`...s0325.5`).
    - `parse_metadata_from_id` — including the `mov-en-20013-conjuring` 5-digit-year edge case.
    - `parse_size_str` (`"9900mb"`, `"10gb"`, garbage).
    - `generate_short_id` deterministic.
    - Library round-trip: load 3 files → mutate → save → reload → compare.
    - Atomic save: kill mid-write (mock `os.replace` to raise) → verify no partial corruption.
  - Add `tests/test_split.py` with a 30 MB dummy MKV (generated via `ffmpeg` in conftest if `ffmpeg` is on PATH; else skipped). Verifies balanced split produces expected chunk count.
  - Integrate into CI later (IMP-A7-followup, GitHub Actions on push).
- Rationale: Manual integration tests via real pushes are the slowest, most expensive feedback loop. Regex bugs in particular (the `[eE|xX]` typo, the Kuroko-style trip case, the `cmd_push_group` x-gap) would all have been caught by a 20-line regex test.
- Goal: Catch regressions in 5 seconds instead of mid-push 90 minutes in. Enable confident refactoring.
- Effort estimate: medium
- Status: pending

---

## IMP-A8: Delete dead code in mainfetch.py

- Category: code quality
- Priority: low
- Files: `mainfetch.py` lines 217-219 (`wait_for_download`), 222-224 (`automation_download_file`), 411-450 (`build_download_queue`)
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
- Status: pending

---

## IMP-A9: Fix the [eE|xX] character-class typo in cmd_prep

- Category: bug
- Priority: low
- Files: `main.py` line 329 (auto-parent regex in `cmd_prep`)
- Current behavior: The regex `re.match(r"^(.*)[eE|xX](\d+(?:\.\d+)?)$", manual_id)` includes a literal `|` inside the character class. Inside `[]`, the pipe is NOT alternation — it is a literal character. So today the regex matches `e`, `E`, `|`, `x`, or `X`. Harmless because no real manual_id contains a literal pipe, but it is a confusing trap for anyone reading the code.
- Proposed change:
  - Change `[eE|xX]` to `[eExX]` (drop the literal pipe).
  - Same fix may apply to other regexes in the same family if grep finds them; search for `[eE|xX]` and `[eExX]` across both files.
  - Add a unit test (under IMP-A7) confirming `re.match(...).group(2)` returns the same value for representative IDs before and after.
- Rationale: Code clarity. The current pattern looks like the author thought `|` is OR inside `[]`. A new contributor seeing this will either copy the mistake or waste time confirming it works.
- Goal: Zero-behaviour-change cleanup that removes a confusing antipattern.
- Effort estimate: small
- Status: pending
