# Improvements — Tier D · New CLI Commands

> Commands you'd type. Most are 30-80 lines of Python and lean heavily on Tier A foundations (argparse, mvcommon, --json). Several are precursors to the future Apple TV UI ([[project_future_apple_tv_ui]]).

> **Cross-cutting context:**
> - Library scale today: 102 movies, 290 series episodes + 28 season_maps, 140 anime episodes + 5 season_maps. State distribution: 412 archived, 120 local_ready.
> - User's manual ID composition is error-prone — `usage_commands.txt` shows typos like `tv-en-strangerthings-s01` (missing year segment), `tv-en-2016-mrrobot-s02` pointing to S04 folder, `mov-en-20013-conjuring` (5-digit year).
> - The legacy `media_library.json` under `archive/legacy/` is unused; do not confuse with the live three.
> - Most of these commands consume but do not mutate library state. The few that mutate (`repair_library`, `relocate`, `rebuild_search_terms`) must follow the atomic-save pattern from `save_library`.

---

## IMP-D1: `library_stats` dashboard command

- Category: other
- Priority: high
- Files: new `cmd_library_stats` in `main.py`; new subcommand under argparse (IMP-A2)
- Current behavior: There is no single-command view of the library's state. The closest is `local_status` (1071-1152), which only shows pending uploads. The user has to mentally compose answers like "how many GB are in the cloud?" from multiple commands.
- Proposed change:
  - New `python main.py library_stats` prints a single-screen dashboard:
    - Per-library totals (count, total cloud bytes, total local bytes, count by status).
    - Language distribution (en/ta/hi/ja/te/ma/kor counts).
    - Top 5 largest archived items.
    - Top 5 oldest local_ready items (by `metadata.added_date`) — these are stale backlog candidates.
    - Total chunks pushed across all entries with `split_info`.
    - Last activity timestamp from the log (after IMP-A3).
  - `--json` mode returns the same data as a structured object for UI consumption.
  - Optional `--per-year`, `--per-language` breakdowns.
- Rationale: Operational visibility. Today the user has no concise way to know "is my archive 1 TB or 5 TB". This is also the first command a UI (IMP-E12, future Apple TV) will need.
- Goal: At-a-glance view of the entire archive state in <2 seconds.
- Effort estimate: small
- Status: pending

---

## IMP-D2: Fuzzy `find` command

- Category: other
- Priority: medium
- Files: new `cmd_find` in `main.py`
- Current behavior: To find an ID the user has to remember its exact spelling. With 532 leaf entries across three libraries and free-form naming, this is the cause of typos like the multiple Inception/Avatar invocations in usage_commands.txt.
- Proposed change:
  - New `python main.py find <query>` does case-insensitive substring matching across:
    - The library key (ID)
    - `entry.filename`
    - `entry.metadata.title`
    - Optional: the folder name component of `folder_path`
  - Returns top 10 matches, each line: `<status_emoji> <id>  <filename> (<size>, <year>)`.
  - Optional `--library movies|series|anime` to scope.
  - Optional `--regex` for power users.
  - `--json` output.
- Rationale: Speeds up every interactive session. Eliminates "what was that movie called again" friction.
- Goal: Find any ID in <3 keystrokes of recall. Reduces typo-driven re-prep.
- Effort estimate: small
- Status: pending

---

## IMP-D3: `where_is` per-entry diagnostic

- Category: other
- Priority: medium
- Files: new `cmd_where_is` in `main.py`
- Current behavior: To diagnose an entry the user must mentally combine: read the JSON for the entry, check disk for original/dummy, check `_parts/` for chunks, check `checksums/` for sidecars, check `restore/` for fetched chunks, check ADB for the remote files. There is no command that does this in one call.
- Proposed change:
  - New `python main.py where_is <id>` prints:
    - Library entry summary (status, uploaded, parent_id, chunks count)
    - Local: existence and size of `folder_path/filename` (and whether it's a dummy or real file)
    - Local: existence of `folder_path/_parts/`, list of files within (with hashes vs split_info)
    - Local: existence of `folder_path/checksums/`, list of sidecars
    - Local: existence of `folder_path/restore/`, list of files within (and hash-routing match)
    - Sidecars: `uid` and `<short_id>.sha256` presence
    - Remote: `adb shell ls /sdcard/Media/<rel_path>/` listing
    - Cloud: optional — search Google Photos for the short_id (slow; gated behind `--check-cloud`)
  - `--json` output.
- Rationale: Diagnostic clarity. When something goes wrong, this command tells you EVERYTHING about an entry's current state in one shot.
- Goal: Replace "five separate commands and a mental model" with one truth-revealing call.
- Effort estimate: medium
- Status: pending

---

## IMP-D4: `verify_library` integrity audit

- Category: other
- Priority: high
- Files: new `cmd_verify_library` in `main.py`. Closely related to [[project_followup_library_integrity]] memory.
- Current behavior: No integrity audit exists. The Aindham Vedham orphan was discovered by manual analysis on 2026-05-25. There is no recurring check; another orphan introduced tomorrow would not be detected for months.
- Proposed change:
  - New `python main.py verify_library` performs read-only checks across all three libraries:
    1. **Orphan children**: any leaf with `parent_id` not present in the library.
    2. **Stale season_maps**: any `season_map` whose `total_episodes` ≠ `len(children)`, or whose `children[]` references missing leaf IDs.
    3. **ID-format violations**: regex-mismatches against the canonical conventions (mov-/tv-/ani- prefix, 2-3 char lang code, 4-digit year, sNNeMM where applicable). Whitelist known non-canonical shapes (Chernobyl, Kuroko).
    4. **Metadata.year = None**: detect 5-digit-year typos like `mov-en-20013-conjuring`.
    5. **tech_spec=error**: entries where MediaInfo parsing failed.
    6. **Hash duplicates**: any SHA256 appearing on two leaves (would confuse fetch routing).
    7. **Chunk hash duplicates within a single entry's split_info**.
    8. **Folder paths that no longer exist** on disk.
    9. **Status drift**: entries with `status=archived` but no dummy file on disk; entries with a dummy file but `status≠archived`.
    10. **Dangling _parts**: `_parts/` folders sitting on disk for entries that show `uploaded=True`.
  - Each check prints a count and an example. Exit code 0 if all clean, non-zero otherwise.
  - `--json` mode emits a structured report.
- Rationale: Aindham Vedham proved that integrity drift can hide for months. A periodic audit catches new cases within one run.
- Goal: Recurring confidence that the library is internally consistent. CI-able if the user ever adopts CI.
- Effort estimate: medium
- Status: pending

---

## IMP-D5: `repair_library` opt-in fixer

- Category: other
- Priority: medium
- Files: new `cmd_repair_library` in `main.py`. Depends on IMP-D4.
- Current behavior: When IMP-D4 finds issues, there is no command to fix them. User has to hand-edit JSON (as we did manually on 2026-05-25 for Aindham Vedham).
- Proposed change:
  - New `python main.py repair_library [--dry-run|--apply] [--checks=orphan_parents,stale_season_maps,...]`.
  - Default mode: `--dry-run` (print what would be changed, write nothing).
  - Repairable categories:
    - **Orphan parents**: rebuild the `season_map` from the children's `parent_id` and shared `folder_path`.
    - **Stale season_maps**: recompute `total_episodes` and re-sort `children`.
    - **Status drift (status=archived, no dummy)**: prompt for user input (file is missing entirely vs needs `replace` re-run vs library is wrong).
    - **Dangling _parts**: remove the directory IF the entry is `uploaded=True` and chunks are confirmed on the remote.
  - NEVER deletes leaf entries automatically — only creates/updates skeletal rows.
  - Each repair logged via IMP-A3 logging.
- Rationale: Pairs with IMP-D4. Audit + fix is one mental loop; users won't run audit if there's no fix-it button.
- Goal: One-shot recovery from common integrity drift, with explicit user consent.
- Effort estimate: medium
- Status: pending

---

## IMP-D6: `prune_dummies` cleanup command

- Category: other
- Priority: low
- Files: new `cmd_prune_dummies` in `main.py`
- Current behavior: There is no inventory of dummy files. If `cmd_replace` partially fails or a library entry gets corrupted, dummy files can be sitting around for entries that don't reflect them as archived.
- Proposed change:
  - New `python main.py prune_dummies` walks `C:\Media\{Movies,Series,Anime}` for files <1024 bytes.
  - For each dummy: look up by folder_path+filename in the library.
    - If entry exists AND `status=archived`: OK (normal state).
    - If entry exists AND `status≠archived`: orphan dummy — entry says local file exists but disk says dummy. Print warning.
    - If no library entry: orphan dummy with no record. Print warning. Optional `--delete-orphans` to remove.
  - Read-only by default.
- Rationale: Defensive sweep. Catches the rare partial-replace or library-corruption case.
- Goal: Surface dummies that disagree with library state. Optional cleanup of true orphans.
- Effort estimate: small
- Status: pending

---

## IMP-D7: `unarchive` alias

- Category: other
- Priority: low
- Files: `main.py` argparse dispatch
- Current behavior: `cmd_fetch_restore` exists (1360-1384) as the combined fetch+restore. Its name describes the pipeline steps but not the user intent. The user's mental model is "I want to UNARCHIVE this" — restore from the cloud.
- Proposed change:
  - Add `unarchive` as an alias for `fetch_restore` in the argparse subparser (IMP-A2).
  - Both names work; documentation prefers `unarchive`.
- Rationale: Mental-model alignment. A user thinks "this is archived, I want to unarchive it" — that's the verb. `fetch_restore` describes the implementation.
- Goal: More discoverable command name; zero behaviour change.
- Effort estimate: small
- Status: pending

---

## IMP-D8: `relocate` command — move folder and update library

- Category: other
- Priority: medium
- Files: new `cmd_relocate` in `main.py`
- Current behavior: When the user moves a media folder (e.g., from `C:\Media\Movies\Old\X` to `D:\Movies\X` because the C: drive is full), the library entries' `folder_path` becomes stale. Today the only way to fix is hand-edit the JSON.
- Proposed change:
  - New `python main.py relocate <id> <new_folder_path>` (or `relocate <parent_id>` for a whole season).
  - Steps:
    1. Verify the new folder exists and contains the expected files.
    2. Move sidecar files if they're at the source.
    3. Update `entry.folder_path` (and all children if it's a season_map).
    4. Save atomically.
  - Optional `--also-move-files` to actually move the files (uses `shutil.move`, handles cross-drive).
  - Dry-run by default.
- Rationale: A real workflow gap. Cross-drive moves are common when libraries grow. Hand-editing JSON is risky.
- Goal: Safe, atomic relocation of media files with library bookkeeping.
- Effort estimate: medium
- Status: pending

---

## IMP-D9: `rebuild_search_terms` command

- Category: other
- Priority: low
- Files: new `cmd_rebuild_search_terms` in `main.py`
- Current behavior: `cmd_prep` writes the `search_term` once at prep time (line 360) as `<filename_base> [<short_id>]<ext>`. If a user renames a file later or the `cmd_prep` formula ever changes, existing entries' search_terms become stale.
- Proposed change:
  - New `python main.py rebuild_search_terms [--id <id>] [--all] [--library movies|series|anime]`.
  - For each target entry, recompute `search_term = f"{filename_base} [{short_id}]{ext}"` from the CURRENT `entry.filename`.
  - Dry-run by default; `--apply` to write.
- Rationale: Bulk fix after filename changes or formula tweaks. Matches the spirit of `cmd_set_search` but at scale.
- Goal: One command to bring all `search_term` fields back into formula consistency.
- Effort estimate: small
- Status: pending

---

## IMP-D10: Interactive `prep_auto` wizard

- Category: other
- Priority: high
- Files: new `cmd_prep_auto` in `main.py`
- Current behavior: The user composes IDs by hand. Live data shows mistakes: `mov-en-20013-conjuring` (5-digit year), `mov-en-inception` (no year — replaced later), `tv-en-strangerthings-s01` (no year in usage_commands.txt, corrected to `tv-en-2016-strangerthings-s01`).
- Proposed change:
  - New `python main.py prep_auto <folder>` interactive wizard:
    1. Scans the folder for video files.
    2. Regex-guesses the show/movie name, year, season, episode from the parent folder name and the file name.
    3. Looks up the slug against TMDB / TheTVDB / AniDB (if IMP-E3 is done) to confirm year and title.
    4. Proposes an ID and prints it.
    5. Prompts: accept / edit / skip.
    6. On accept: runs `cmd_prep` (or `cmd_prep_season` for multi-file folders).
  - `--batch` mode auto-accepts all proposals.
  - Per-prefix templates from config (IMP-A5) so the user can encode their personal conventions.
- Rationale: The TYPO ROOT CAUSE. Removes hand-composition for the easy 80% of cases. Even partial automation (proposing the ID and letting the user accept) eliminates most typos.
- Goal: 80% of prepping is one Y/N keystroke instead of typing a long ID.
- Effort estimate: medium (heavier with TMDB integration; lighter without)
- Status: pending

---

## IMP-D11: `push --auto-batch <size>` end-to-end

- Category: other
- Priority: medium
- Files: `main.py` — `cmd_local_status` (1071-1152, already does selection) extended; or new `cmd_push_auto_batch`
- Current behavior: `local_status 40gb` selects items that fit in 40 GB and prints suggested `python main.py push <id>` lines. The user then copy-pastes them one by one.
- Proposed change:
  - New `python main.py push_auto_batch 40gb` (or `local_status 40gb --execute`):
    - Runs the existing greedy bin-packing selection.
    - Prompts: "Push these N items (~M GB)? [y/N]".
    - On confirmation, sequentially pushes each. Uses progress.json (IMP-C1) to allow resume on failure.
    - On any failure, stops with clear message (or `--keep-going` to continue past failures).
  - Default split params from config (IMP-A5).
- Rationale: Closes the copy-paste loop. Today the user does the human-in-the-loop part for no real reason; just having the script accept "yes go" saves keystrokes.
- Goal: Push the next 40 GB batch with one command + one keystroke.
- Effort estimate: small
- Status: pending

---

## IMP-D12: `seal` paranoid replace

- Category: other
- Priority: low
- Files: new `cmd_seal` in `main.py`
- Current behavior: For high-value items the user might want maximum verification before `replace` (which destroys the original). Today this means running `check`, then `set_uploaded` (if needed), then `replace` — three commands.
- Proposed change:
  - New `python main.py seal <id>` macro:
    1. Run `check` (re-hash local file vs library).
    2. Run remote verification via ADB (`adb shell md5sum` of expected chunks).
    3. Compare local + remote + library hashes for full agreement.
    4. Only if all agree, run `replace`.
  - Aborts loudly on any disagreement.
- Rationale: For users who archive irreplaceable content. A "belt + suspenders + parachute" command for high-anxiety operations.
- Goal: Maximum verification before irreversible destruction.
- Effort estimate: small
- Status: pending

---

## IMP-D13: `compare` deep diagnostic

- Category: other
- Priority: low
- Files: new `cmd_compare` in `main.py`
- Current behavior: `cmd_check` only compares the local file's hash to `entry.hash`. There is no command that cross-checks local hash vs library hash vs sidecar `.sha256` vs chunk hashes in `_parts/` vs chunk hashes in `restore/`.
- Proposed change:
  - New `python main.py compare <id>` runs a paranoid cross-check across all hash sources:
    - Local file (if not dummy) → SHA256
    - Library `entry.hash`
    - Sidecar `<short_id>.sha256` file content
    - For each chunk in `entry.split_info.chunks`: hash vs `checksums/<chunk>.sha256` vs actual chunk file (in `_parts/` or `restore/` if present).
  - Reports any disagreement.
- Rationale: Maximum confidence diagnostic. Useful before/after risky operations (relocate, hand-edited JSON, etc.).
- Goal: Pinpoint EXACTLY which hash source disagrees when something looks off.
- Effort estimate: small
- Status: pending

---

## IMP-D14: `tail_progress` live log viewer

- Category: other
- Priority: low
- Files: new `cmd_tail_progress` in `main.py`. Depends on IMP-A3 logging.
- Current behavior: When a long auto-pilot is running in shell A, the user has no good way to monitor it from shell B. Running `tail -f` on emoji-formatted stdout is awkward; Windows doesn't even have a great `tail -f` equivalent natively.
- Proposed change:
  - New `python main.py tail_progress` reads the current day's log file (`~/.mediavault/logs/YYYY-MM-DD.log`).
  - Pretty-prints recent activity with color (level-based).
  - Optional `--correlation <id>` to filter one specific batch run.
  - Optional `--since <minutes>` window.
- Rationale: With IMP-A3 in place, a log file exists. This command makes it usable.
- Goal: Cheap, ergonomic monitoring of a long-running batch from another shell.
- Effort estimate: small
- Status: pending

---

## IMP-D15: `export_watchlist` / `import_watchlist`

- Category: other
- Priority: low
- Files: new `cmd_export_watchlist` and `cmd_import_watchlist` in `main.py`
- Current behavior: No way to bridge MediaVault and external trackers (Letterboxd, MyAnimeList, TVDB). To know what's in the library, the user has to read JSONs or use Plex.
- Proposed change:
  - `export_watchlist <category> [--format csv|letterboxd|mal]` writes a sharable list of titles, years, and watched-state (after IMP-E4) to a CSV.
  - `import_watchlist <file>` reads such a list and for each row, attempts to find an existing library entry. For rows with no match, suggests an ID to `prep` later. Useful: "I have a Letterboxd list of 50 movies I want to archive; import it, get suggested IDs for each, then run prep_auto on the actual folders."
- Rationale: Bidirectional integration with external tracking services. Long-tail utility.
- Goal: Connect the archive to existing personal tracking habits.
- Effort estimate: medium
- Status: pending
