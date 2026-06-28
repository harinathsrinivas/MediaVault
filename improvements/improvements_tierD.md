# Improvements — Tier D · New CLI Commands

> Commands you'd type. Most are 30-80 lines of Python and lean heavily on Tier A foundations (argparse, mvcommon, --json). Several are precursors to the couch/Apple-TV experience ([[project_future_apple_tv_ui]], Tier S/U, `ROADMAP_END_GOAL.md`).

> **Cross-cutting context:**
> - Library scale today: 102 movies, 290 series episodes + 28 season_maps, 140 anime episodes + 5 season_maps. State distribution: 412 archived, 120 local_ready.
> - User's manual ID composition is error-prone — `usage_commands.txt` shows typos like `tv-en-strangerthings-s01` (missing year segment), `tv-en-2016-mrrobot-s02` pointing to S04 folder, `mov-en-20013-conjuring` (5-digit year).
> - The legacy `media_library.json` under `archive/legacy/` is unused; do not confuse with the live three.
> - Most of these commands consume but do not mutate library state. The few that mutate (`repair_library`, `relocate`, `rebuild_search_terms`) must follow the atomic-save pattern from `save_library` and SHOULD reuse the `RollbackJournal` for their mutations.
> - **Any new whole-library iterator must skip/resolve `multi_ep_alias` entries** (the IMP-C12 lesson — scan_unprepped and local_status both crashed on this).
> - **Attribute key (added 2026-06-12):** `Risk` = blast radius of MAKING the change. `If skipped` = the failure/limitation that persists, with a scenario.

---

## IMP-D1: `library_stats` dashboard command

- Category: other
- Priority: high
- Files: new `cmd_library_stats` in `main.py`; new subcommand under argparse (IMP-A2)
- Current behavior: There is no single-command view of the library's state. The closest is `local_status`, which only shows pending uploads. The user has to mentally compose answers like "how many GB are in the cloud?" from multiple commands.
- Proposed change:
  - New `python main.py library_stats` prints a single-screen dashboard:
    - Per-library totals (count, total cloud bytes, total local bytes, count by status).
    - Language distribution (en/ta/hi/ja/te/ma/kor counts).
    - Top 5 largest archived items.
    - Top 5 oldest local_ready items (by `metadata.added_date`) — these are stale backlog candidates.
    - Total chunks pushed across all entries with `split_info`.
    - Last activity timestamp from the log (after IMP-A3).
  - Skip `season_map` AND `multi_ep_alias` entries in all aggregations (C12 lesson).
  - `--json` mode returns the same data as a structured object for UI consumption.
  - Optional `--per-year`, `--per-language` breakdowns.
- Rationale: Operational visibility. Today the user has no concise way to know "is my archive 1 TB or 5 TB". This is also the first command a UI (IMP-E12, Tier S daemon status page, future Apple TV) will need.
- Goal: At-a-glance view of the entire archive state in <2 seconds.
- Effort estimate: small
- Risk: low — new read-only command; no existing path changes.
- If skipped: no breakage — but capacity questions ("how much would a second cloud account need to hold?", "what's my local-disk exposure?") keep requiring ad-hoc JSON spelunking, and the daemon's status surface has nothing to serve.
- Partial delivery (2026-06-22): the **total-reclaimable-GB** slice is now delivered by `web` / `collect_reclaimable` (IMP-E12 / IMP-D16); D1's full per-library / per-language `library_stats` dashboard remains pending (consume `collect_reclaimable` + add the other aggregations).
- Status: pending

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
  - Resolve `multi_ep_alias` hits to their primary (show both ids: `…s04e20 → …s04e19`).
  - Optional `--library movies|series|anime` to scope.
  - Optional `--regex` for power users.
  - `--json` output.
- Rationale: Speeds up every interactive session. Eliminates "what was that movie called again" friction.
- Goal: Find any ID in <3 keystrokes of recall. Reduces typo-driven re-prep.
- Effort estimate: small
- Risk: low — read-only.
- If skipped: ID recall friction persists; every mistyped id costs a "❌ ID not found" round-trip (or worse, a near-miss id that silently targets the wrong entry).
- Status: pending

---

## IMP-D3: `where_is` per-entry diagnostic

- Category: other
- Priority: medium
- Files: new `cmd_where_is` in `main.py`
- Current behavior: To diagnose an entry the user must mentally combine: read the JSON for the entry, check disk for original/dummy, check `_parts/` for chunks, check `checksums/` for sidecars, check `restore/` for fetched chunks, check ADB for the remote files. There is no command that does this in one call.
- Proposed change:
  - New `python main.py where_is <id>` prints:
    - Library entry summary (status, uploaded, parent_id, chunks count, re_hashed/canonical state)
    - Local: existence and size of `folder_path/filename` (and whether it's a dummy or real file)
    - Local: existence of `folder_path/_parts/`, list of files within (with hashes vs split_info)
    - Local: existence of `folder_path/checksums/`, list of sidecars
    - Local: existence of `folder_path/restore/` (+ `quarantine/`), list of files within (and hash-routing match)
    - Local: leftover `.mediavault_txn.json` journal (pre/post PONR state)
    - Sidecars: `uid` and `<short_id>.sha256` presence
    - Remote: `adb shell ls /sdcard/Media/<rel_path>/` listing (incl. `.mvmeta.json` sidecar)
    - Cloud: optional — search Google Photos for the short_id (slow; gated behind `--check-cloud`)
  - `--json` output.
- Rationale: Diagnostic clarity. When something goes wrong, this command tells you EVERYTHING about an entry's current state in one shot. The daemon's "why is this tile stuck?" debugging tool.
- Goal: Replace "five separate commands and a mental model" with one truth-revealing call.
- Effort estimate: medium
- Risk: low — read-only (ADB `ls` only).
- If skipped: every stuck-state investigation stays a multi-command archaeology session; with a daemon adding background mutations, ad-hoc diagnosis gets harder, not easier.
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
    3. **Alias integrity (new, post-PR #21)**: every `multi_ep_alias.alias_of` resolves to an existing LEAF (not another alias); every alias appears in its parent's `children`; no leaf was overwritten onto a former alias id.
    4. **ID-format violations**: regex-mismatches against the canonical conventions (mov-/tv-/ani- prefix, 2-3 char lang code, 4-digit year, sNNeMM where applicable). Whitelist known non-canonical shapes (Chernobyl, Kuroko).
    5. **Metadata.year = None**: detect 5-digit-year typos like `mov-en-20013-conjuring`.
    6. **tech_spec=error**: entries where MediaInfo parsing failed.
    7. **Hash duplicates**: any SHA256 appearing on two leaves (would confuse fetch routing).
    8. **Chunk hash duplicates within a single entry's split_info**.
    9. **Folder paths that no longer exist** on disk.
    10. **Status drift**: entries with `status=archived` but no dummy file on disk; entries with a dummy file but `status≠archived`.
    11. **Dangling _parts**: `_parts/` folders sitting on disk for entries that show `uploaded=True`.
    12. **Split-hash state sanity**: `re_hashed=true` entries missing `merge_seed`, or a transient `canonical_hash` surviving past replace.
  - Each check prints a count and an example. Exit code 0 if all clean, non-zero otherwise.
  - `--json` mode emits a structured report.
- Rationale: Aindham Vedham proved that integrity drift can hide for months. A periodic audit catches new cases within one run — and the daemon should run it nightly.
- Goal: Recurring confidence that the library is internally consistent. CI-able (IMP-A12) and daemon-schedulable.
- Effort estimate: medium
- Risk: low — strictly read-only.
- If skipped: integrity drift keeps being discovered by accident (the orphan sat invisible for months); the daemon would happily automate fetch/archive cycles on top of silently inconsistent state, amplifying any drift.
- Status: in_progress
- Note (2026-06-22): Partially delivered on branch fix/imp_d4_library_integrity_guard: `cmd_verify_library` status-to-disk integrity invariant (every physical leaf's status must match its on-disk shape; archived=>video-dummy, local_ready/onboarded/restored_local=>real) + warn-only post-conditions wired into cmd_push/cmd_replace/cmd_restore happy paths (post-commit, no rollback/PONR impact). Remaining D4 scope: orphan-parent, stale-season-map, hash-format audits.
- Note (2026-06-23, IMP-D4 upload-integrity additions on `feature/imp_e14_fetch_in_ui`): `cmd_prep`'s short-circuit guard was widened to refuse re-prepping ANY cloud-bearing entry (`uploaded` truthy OR status in `{onboarded, archived, restored_local}`), closing the re-prep clobber path that produced the 107-entry + battlestar danglers. `cmd_verify_library` now additionally flags `possibly_dangling` leaves (entries with status `local_ready` or `uploaded=false` that nonetheless have on-disk cloud evidence such as a `checksums/` dir or a `_parts/` remnant).

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
    - **Broken aliases**: re-point `alias_of` when the primary was renamed, or remove an alias whose primary is gone (with confirmation).
    - **Status drift (status=archived, no dummy)**: prompt for user input (file is missing entirely vs needs `replace` re-run vs library is wrong).
    - **Dangling _parts**: remove the directory IF the entry is `uploaded=True` and chunks are confirmed on the remote.
  - NEVER deletes leaf entries automatically — only creates/updates skeletal rows.
  - Wrap `--apply` mutations in a `RollbackJournal` (the existing record vocabulary covers create_entry/set_field/link_child).
  - Each repair logged via IMP-A3 logging.
- Rationale: Pairs with IMP-D4. Audit + fix is one mental loop; users won't run audit if there's no fix-it button.
- Goal: One-shot recovery from common integrity drift, with explicit user consent.
- Effort estimate: medium
- Risk: medium — mutates library state by design; dry-run default + journal + never-delete-leaves rule contain it. Reuses (not modifies) the rollback journal, so no change-gate trigger — but say so explicitly in the PR.
- If skipped: every future integrity finding means another hand-edited-JSON session (the 2026-05-25 method) — error-prone exactly when the library is already inconsistent.
- Status: pending
- Note (2026-06-22): Slice delivered on branch fix/imp_d4_library_integrity_guard: `verify_library --fix-dummies` reuses `cmd_repair_dummies` to regenerate legacy text-stub dummies for archived entries. Broader opt-in repair_library (orphan cleanup etc.) still pending.

---

## IMP-D6: `prune_dummies` cleanup command

- Category: other
- Priority: low
- Files: new `cmd_prune_dummies` in `main.py`
- Current behavior: There is no inventory of dummy files. If `cmd_replace` partially fails or a library entry gets corrupted, dummy files can be sitting around for entries that don't reflect them as archived.
- Proposed change:
  - New `python main.py prune_dummies` walks `C:\Media\{Movies,Series,Anime}` for files under `DUMMY_MAX_BYTES` (200 KB — the modern video dummies are ~10 KB, not the old <1 KB text blobs).
  - For each dummy: look up by folder_path+filename in the library.
    - If entry exists AND `status=archived`: OK (normal state).
    - If entry exists AND `status≠archived`: orphan dummy — entry says local file exists but disk says dummy. Print warning.
    - If no library entry: orphan dummy with no record. Print warning. Optional `--delete-orphans` to remove.
  - Read-only by default.
- Rationale: Defensive sweep. Catches the rare partial-replace or library-corruption case.
- Goal: Surface dummies that disagree with library state. Optional cleanup of true orphans.
- Effort estimate: small
- Risk: low read-only; `--delete-orphans` is destructive — require an explicit confirmation listing each file before deleting.
- If skipped: rare-path inconsistencies (dummy ↔ status disagreements) stay invisible until a fetch/restore trips over them.
- Status: pending

---

## IMP-D7: `unarchive` alias

- Category: other
- Priority: low
- Files: `main.py` dispatch (argparse after IMP-A2, or the manual chain today)
- Current behavior: `cmd_fetch_restore` exists as the combined fetch+restore. Its name describes the pipeline steps but not the user intent. The user's mental model is "I want to UNARCHIVE this" — restore from the cloud.
- Proposed change:
  - Add `unarchive` as an alias for `fetch_restore` in the dispatcher.
  - Both names work; documentation prefers `unarchive`.
- Rationale: Mental-model alignment. A user thinks "this is archived, I want to unarchive it" — that's the verb. `fetch_restore` describes the implementation.
- Goal: More discoverable command name; zero behaviour change.
- Effort estimate: small
- Risk: low — pure alias.
- If skipped: cosmetic only.
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
    3. Update `entry.folder_path` (and all children if it's a season_map; aliases carry no folder_path — skip them).
    4. Save atomically.
  - Optional `--also-move-files` to actually move the files (uses `shutil.move`, handles cross-drive).
  - Dry-run by default.
- Rationale: A real workflow gap. Cross-drive moves are common when libraries grow. Hand-editing JSON is risky.
- Goal: Safe, atomic relocation of media files with library bookkeeping.
- Effort estimate: medium
- Risk: medium — `folder_path` is load-bearing EVERYWHERE (sidecars, `_parts/`, `restore/`, the rollback journal location, remote-path derivation `os.path.relpath(folder, LOCAL_ROOT)`). A relocate to a different drive changes the REMOTE target dir for future pushes (cross-drive fallback path) — document that consequence in the command output. Refuse to relocate while a `.mediavault_txn.json` journal or `_parts/` exists in the source folder.
- If skipped: drive-pressure reorganizations keep requiring hand-edited JSON across N entries + manual sidecar moves — the exact conditions that created historical integrity drift.
- Status: pending

---

## IMP-D9: `rebuild_search_terms` command

- Category: other
- Priority: low
- Files: new `cmd_rebuild_search_terms` in `main.py`
- Current behavior: `cmd_prep` writes the `search_term` once at prep time as `<filename_base> [<short_id>]<ext>`. If a user renames a file later or the `cmd_prep` formula ever changes, existing entries' search_terms become stale.
- Proposed change:
  - New `python main.py rebuild_search_terms [--id <id>] [--all] [--library movies|series|anime]`.
  - For each target entry, recompute `search_term = f"{filename_base} [{short_id}]{ext}"` from the CURRENT `entry.filename`.
  - Dry-run by default; `--apply` to write.
- Rationale: Bulk fix after filename changes or formula tweaks. Matches the spirit of `cmd_set_search` but at scale.
- Goal: One command to bring all `search_term` fields back into formula consistency.
- Effort estimate: small
- Risk: medium-for-a-small-command — the search_term must match the name that was UPLOADED, not the current local name. A local rename AFTER upload means the rebuilt term would point at a nonexistent remote name and silently break fetch for that entry. The command must warn when `uploaded=True` (the remote name is frozen at push time) and only auto-apply for `local_ready` entries.
- If skipped: stale search terms surface as mysterious fetch misses; today fixed one-at-a-time via `set_search`.
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
- Risk: low-medium — wraps existing `cmd_prep`/`cmd_prep_season` (journaled); the only new failure mode is proposing a WRONG id that the user rubber-stamps — mitigate with the TMDB confirmation step and a loud diff vs the canonical pattern.
- If skipped: ID typos keep entering the library at hand-typing rates; each one is a future "why doesn't sort/fetch find this" mystery (the 20013 typo still sits at the bottom of every sorted listing).
- Status: pending

---

## IMP-D11: `push --auto-batch <size>` end-to-end

- Category: other
- Priority: medium
- Files: `main.py` — `cmd_local_status` (already does selection) extended; or new `cmd_push_auto_batch`
- Current behavior: `local_status 40gb` selects items that fit in 40 GB and prints suggested `python main.py push <id>` lines. The user then copy-pastes them one by one.
- Proposed change:
  - New `python main.py push_auto_batch 40gb` (or `local_status 40gb --execute`):
    - Runs the existing greedy bin-packing selection.
    - Prompts: "Push these N items (~M GB)? [y/N]".
    - On confirmation, sequentially pushes each. Uses progress tracking (IMP-C1 pattern) to allow resume on failure.
    - On any failure, stops with clear message (or `--keep-going` to continue past failures).
  - Default split params from config (IMP-A5); `device <alias>` forwarding.
- Rationale: Closes the copy-paste loop. Today the user does the human-in-the-loop part for no real reason; just having the script accept "yes go" saves keystrokes.
- Goal: Push the next 40 GB batch with one command + one keystroke.
- Effort estimate: small
- Risk: low-medium — orchestrates existing journaled `cmd_push` calls only; per-item failure semantics are already O-1 resumable. The batch loop itself must not invent new cleanup behavior (stay out of the change-gate).
- If skipped: Pixel-batch loading stays a copy-paste ritual; the daemon's "keep the upload pipeline fed" loop (Tier S) has no command to drive.
- Status: pending

---

## IMP-D12: `seal` paranoid replace

- Category: other
- Priority: low
- Files: new `cmd_seal` in `main.py`
- Current behavior: For high-value items the user might want maximum verification before `replace` (which destroys the original). Today this means running `check`, then remote verification by hand, then `replace` — three commands.
- Proposed change:
  - New `python main.py seal <id>` macro:
    1. Run `check` (re-hash local file vs library).
    2. Run remote verification via ADB (`adb shell sha256sum` of expected chunks — the C8 helper).
    3. Compare local + remote + library hashes for full agreement.
    4. Only if all agree, run `replace`.
  - Aborts loudly on any disagreement.
- Rationale: For users who archive irreplaceable content. A "belt + suspenders + parachute" command for high-anxiety operations.
- Goal: Maximum verification before irreversible destruction.
- Effort estimate: small
- Risk: low — composes existing commands; the destructive step remains `cmd_replace` with its existing PONR/journal semantics untouched.
- If skipped: paranoid verification stays a manual 3-command ritual, so in practice it doesn't happen — `replace` keeps trusting the upload-confirmation flag alone.
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
    - Library `entry.hash` (canonical merged hash if `re_hashed` — label it as such)
    - Sidecar `<short_id>.sha256` file content (note: for split entries this still holds the ORIGINAL pre-split hash, which legitimately differs from a blessed canonical — the report must explain, not alarm)
    - For each chunk in `entry.split_info.chunks`: hash vs `checksums/<chunk>.sha256` vs actual chunk file (in `_parts/` or `restore/` if present).
  - Reports any disagreement with a verdict line per source.
- Rationale: Maximum confidence diagnostic. Useful before/after risky operations (relocate, hand-edited JSON, etc.). The original-vs-canonical distinction (PR #20) makes a tool that EXPLAINS hash provenance genuinely valuable.
- Goal: Pinpoint EXACTLY which hash source disagrees when something looks off.
- Effort estimate: small
- Risk: low — read-only.
- If skipped: hash-provenance confusion (original vs canonical vs chunk) gets debugged by hand each time; the split-hash feature made this MORE likely to come up, not less.
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
- Risk: low — read-only over log files.
- If skipped: long-batch monitoring stays "watch the original console or nothing"; superseded longer-term by the daemon's status API (Tier S), so deprioritize if S lands first.
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
- Risk: low — export read-only; import only SUGGESTS ids (never preps automatically).
- If skipped: nothing operational; external-tracker users keep manual lists. (Jellyfin+Trakt plugin covers much of the watch-state half once Tier S lands.)
- Status: pending

---

## IMP-D16: `scan_reclaimable` — four-state reclaim scan (data layer behind `web`)

- Category: other
- Priority: high
- Files: `main.py` data-functions (`collect_reclaimable` + `classify_entry_state` / `suggest_target_folder` / `suggest_next_command` / `guess_manual_id`)
- Current behavior: `scan_unprepped` only finds on-disk files NOT in any library; there was no single scan that ALSO surfaces prepped/pushed-but-still-local files occupying reclaimable space, nor what to do next about each.
- Proposed change (SHIPPED with IMP-E12): `collect_reclaimable()` loads the library once + walks the three category roots and classifies every physical file into one of four reclaim badges — `UNPREPPED` / `LOCAL_NOT_PUSHED` / `PUSHED_NOT_ARCHIVED` / `RESTORED_REPLACE_AGAIN` — excluding `archived`+dummy and the `season_map`/`multi_ep_alias` aliases; reclaimability is decided by **actual on-disk size** (`>= DUMMY_MAX_BYTES`), de-duped by normpath; returns `{items, total_reclaimable_bytes, total_reclaimable_human}`. Each item also carries a deterministic suggested next command + suggested target folder. Pure, read-only, alias/`season_map`-safe.
- Rationale: the data layer behind the `web` console (IMP-E12); also delivers IMP-D1's total-reclaimable-GB slice and is the reuse surface for IMP-A4's `--json`.
- Goal: one read-only scan that answers "what local files can I reclaim, and what's the next command for each".
- Effort estimate: small-medium
- Risk: low — read-only; the only hazard is the new whole-library iterator (the PR#21 / IMP-C12 class), handled by the alias-skip + the smoke `TestAliasSweep` sweep.
- If skipped: N/A — shipped.
- Status: **done** (`feature/web_console` / IMP-E12; PR to `main` pending).

---

## IMP-D17: `rename_folder` — crash-safe cascading folder rename

- Category: other
- Priority: high
- Files: new `cmd_rename_folder` in `main.py`
- Current behavior: Renaming a media folder requires hand-editing `folder_path` in every library entry that references it (leaf + season_map + any children). Missing even one breaks every downstream operation for that entry.
- Proposed change (SHIPPED on `feature/imp_e3_u3_d17_tmdb_posters_rename`):
  - New `python main.py rename_folder <old_id_or_path> "<new_name>"` command.
  - Renames the on-disk directory via `os.rename` (atomic on Windows within the same volume).
  - Atomically rewrites `folder_path` in the library for every descendant: season_map entries + leaf entries; `multi_ep_alias` entries are skipped (they carry no `folder_path`).
  - Uses the **existing `RollbackJournal`** — journal written in the parent directory of the folder being renamed; `os.rename` on disk = PONR; `folder_path` JSON rewrite is post-PONR. **ADDITIVE to the rollback contract** — does NOT change the journal format, `fsync`+`os.replace` durability, `RollbackHardFail` semantics, or any PONR location in other commands (cross-ref §12a and `docs/feature-auto-rollback/ROLLBACK_MECHANISM.md` §10).
  - **Hash-safe:** moves a directory + rewrites JSON paths only; no file bytes are changed; SHA256 hashes remain valid; `uid`/`.sha256` sidecars travel with the folder.
  - Works on archived dummies (the dummy file travels with the folder).
  - Used internally by `enrich_metadata` to stamp the `{tmdb-…}` folder token on show directories.
- Rationale: Foundation for `enrich_metadata`'s `{tmdb-…}` folder-token stamping, and a long-standing operational need. The `{tmdb-…}` convention is how Plex/Emby/Jellyfin associate a folder with a specific TMDB entry — without it, media servers rely on filename matching alone.
- Goal: Safe, atomic folder renames with full library bookkeeping; enables the TMDB folder-token convention.
- Effort estimate: small
- Risk: low — uses the existing rollback mechanism (additive, no contract change); the only failure mode is an `os.rename` that fails mid-way (PONR not yet crossed) or a JSON save that fails (post-PONR, recoverable via `recover`).
- If skipped: TMDB folder-token stamping requires manual renames + hand-edited JSON; `enrich_metadata` cannot stamp tokens automatically; folders keep accumulating stale names after a TMDB match.
- Status: **done** (`feature/imp_e3_u3_d17_tmdb_posters_rename`, 2026-06-24)

---

## IMP-D18: "Others" content category (sports now; documentaries later)

- Category: other
- Priority: high
- Files: `mvcommon.py` (LIBRARY_OTHERS / OTHERS_PREFIX / OTHERS_ROOT constants), `mainfetch.py` (oth- fetch profile), `main.py` (oth- routing in load_library / save_library / all cmd_* walkers; ENTRY_TYPE_KEYS guard; new Chrome profile + DEVICE_ALIASES entry), `tests/` (smoke + unit tests for oth- routing and walker safety)
- Current behavior: MediaVault has three content categories — movies (`mov-`), TV series (`tv-`), anime (`ani-`) — each with its own library JSON and root folder. Sports content (FIFA football, IPL cricket) and future documentaries have no home: they would have to be shoehorned into movies or series, losing semantic identity and requiring per-entry manual category overrides.
- Proposed change:
  - Add a 4th content category **Others** (`oth-` prefix, `C:\Media\library_others.json`, root `C:\Media\Sports\...`).
  - `LIBRARY_OTHERS` / `OTHERS_PREFIX` / `OTHERS_ROOT` declared in `mvcommon.py` (parallel to the existing three constants); `mainfetch.py` routes `oth-` IDs to a dedicated Chrome profile; `main.py` wires `oth-` into `load_library`, `save_library`, and every whole-library walker/iterator.
  - **ID scheme**: `oth-football-2026-fifaworldcup-s01e01` (tournament-edition = TV season; each half = an episode). Reuses the existing `season_map` + leaf structure — NO new `ENTRY_TYPE_KEYS` entry type; no rollback-contract change.
  - Storage layout uses an `"other"` → list-of-subdirs mapping (list-capable so multiple sports can coexist).
  - **Enrichment** skips `oth-` entries (no TMDB/AniDB match for sports; guard added in `enrich_metadata`).
  - New Chrome profile for oth- fetch; new Pixel DEVICE_ALIASES entry (serial is a user prerequisite — not automated).
  - All existing commands (`push`, `replace`, `fetch_restore`, `local_status`, `scan_reclaimable`, `rename_folder`, `verify_library`, etc.) work transparently for `oth-` entries via the shared walker; the `multi_ep_alias` / `season_map` skip-or-resolve rules apply unchanged.
- Rationale: Sports content is a real archiving use case (FIFA 2026, IPL 2025) that does not fit movies or series semantically. A dedicated category keeps the library clean and lets the web UI surface an "Others" tab (IMP-E14 already has the tab slot). Documentaries can follow the same path later with zero further structural change.
- Goal: Store, push, fetch, and browse sports/others content with the same pipeline reliability as movies/series/anime; web UI Others tab populated from `library_others.json`.
- Effort estimate: small-medium
- Risk: medium — new-category plumbing touches `mvcommon`, `mainfetch`, `main` load/save paths, every whole-library walker, and the web API; the `ENTRY_TYPE_KEYS` guard + smoke gate catch regressions. No rollback-contract change (reuses existing season_map/leaf types).
- If skipped: sports and future documentary content either pollutes the movies/series libraries (confusing stats + UI) or stays unarchived entirely — the vault misses a growing share of the user's media diet.
- Status: in_progress (being built on `feature/imp_d18_others_category`; the architect/PR step will flip this to `done` on merge)
