# Fable Review — Code & Docs Review Notes

Running findings from the P1 deep read (2026-06-12). Each finding is tagged:
**[BUG]** broken behavior · **[STALE-DOC]** docs contradict code · **[SMELL]** works but fragile ·
**[PERF]** performance · **[IMP-CAND]** improvement-task candidate for P5 tier work ·
**[GATE]** touches the auto-rollback change-gate — needs explicit user decision before any fix.

Status of source reads: `main.py` (3081 lines) ✅ full · `mvcommon.py` ✅ full · `mainfetch.py` ✅ full ·
`ARCHITECTURE.md` ✅ full. Pending: README, tier files, docs/, tests/, PRs.

---

## A. Bugs found in current code

### A1. [BUG][IMP-CAND] `cmd_scan_unprepped` crashes if any `multi_ep_alias` exists
`main.py:2459-2462` — builds `known_paths` via `entry['folder_path']` / `entry['filename']` for every
non-season_map entry. `multi_ep_alias` entries (PR #21) have **neither key** → uncaught `KeyError` →
traceback. Live data has aliases (E19E20 was the motivating case), so the command is likely broken in
production **today**. Fix shape: skip `type == "multi_ep_alias"` (one line). Severity: HIGH (a daily-driver
command), risk of fix: trivial.

### A2. [BUG][IMP-CAND] `cmd_local_status` crashes / shows phantom rows for aliases
`main.py:2358-2369` skips only `season_map`. An alias has no `uploaded` → counted as pending; no
`filename` → `item['filename'][:40]` at `main.py:2415` is `None[:40]` → `TypeError` crash. Same one-line
skip fix. Severity: HIGH (daily-driver), trivial fix.

### A3. [BUG][IMP-CAND] Direct single-id commands crash with raw tracebacks on alias IDs
`cmd_push` (1221), `cmd_replace` (1744 — silent False), `cmd_restore` (2036), `cmd_check` (1096),
`cmd_verify_restore` (1960), `cmd_fetch_restore` single-branch (2756) all do `entry['folder_path']`-style
access without `_resolve_alias`. Passing a secondary episode id (e.g. `tv-...-s04e20`) → KeyError
traceback. Group loops and mainfetch `resolve_targets` DO de-alias (even for direct single ids —
`mainfetch.py:392`), so `fetch` works but `restore` on the same id crashes. Fix shape: resolve-or-friendly-
message at lookup in each single-id command. Severity: MEDIUM (user must type a secondary-episode id).

### A4. [BUG][IMP-CAND] `push_group` argv parser infinite-loops on trailing keyword
`main.py:3017-3042` — `SIZE_MB|SIZE_GB|COUNT|episodes|device` as the LAST token: `i+1 < len(args)` is
false → no `i` increment → infinite `while` loop (hang, must Ctrl-C). The `push` parser has proper
`else: sys.exit(1)` arms; `push_group` doesn't. Severity: LOW-MED (typo-triggered), trivial fix.

### A5. [BUG][IMP-CAND] `mainfetch.py` bare-invocation IndexError
`mainfetch.py:481-486` — guard is `len(sys.argv) < 2` but then reads `sys.argv[2]`;
`python mainfetch.py fetch` (len 2) → IndexError instead of usage text. Guard should be `< 3` (and
arguably verify `sys.argv[1] == "fetch"`). Severity: LOW, trivial.

### A6. [BUG][IMP-CAND] `cmd_replace` on unknown id is silent
`main.py:1743` — `if manual_id not in library: return False` with **no message**. A typo'd
`replace` prints nothing and exits 0. Severity: LOW (UX/logging), trivial.

### A7. [SMELL][IMP-CAND][GATE] Restore merge-failure rollback leaves NO file at the original path
`cmd_restore` split path merges directly **onto** `target_path` — overwriting the archived dummy. On
merge failure, rollback removes the (reproducible) partial target → the dummy is gone too. Entry stays
`archived` but no file exists on disk → media-server libraries (Plex/Jellyfin/Emby) drop the item;
`repair_dummies` counts it as `missing` and does NOT regenerate (`main.py:1923-1926`). Fix options:
merge to a temp name then `os.replace`, or regenerate the dummy in the failure path, or let
`repair_dummies` recreate missing dummies. **[GATE]** any fix moves/changes restore artifacts near the
PONR — must be cleared via the change-gate first.

### A8. [SMELL][IMP-CAND][GATE] Re-running a command silently clobbers a leftover pre-PONR journal
`RollbackJournal.__init__` immediately `_flush()`es an empty journal over any leftover
`.mediavault_txn.json` (`main.py:574-581`). The documented contract is "leftovers are handled by
recover_journal()" — but a user who re-runs the command (the natural reflex) instead of running
`recover` first destroys the crashed run's inverses; its artifacts become permanently rollback-orphaned
(they now look "pre-existing" to the new run). Fix shape: detect leftover at journal open → warn + offer
`recover` (or auto-run pre-PONR recovery, which IS idempotent). **[GATE]** — this is journal-lifecycle
behavior, explicitly change-gated.

### A9. [SMELL] Eager-rehash temp merge file isn't journalled
`cmd_push` eager path writes `<base>.rehash_tmp.mkv` (master-sized!) and cleans it in `finally` — but a
hard kill mid-merge leaves it on disk, untracked by the journal, invisible to `recover`. Disk-leak only.
**[GATE-adjacent]** (eager rehash was pre-authorized, but journalling a new record type touches the
journal vocabulary).

### A10. [SMELL] `_verify_chunk_hash` can IndexError on empty sha256sum stdout
`main.py:1210` — `result.stdout.strip().split()[0]` with empty stdout → IndexError, which is NOT in
`retry_on` → escapes as generic failure. Cosmetic robustness.

### A11. [SMELL] `cmd_prep_season` multi-ep alias creation is outside the rollback journal
`main.py:1069-1083` re-loads the library and writes alias entries + parent links directly, after
`cmd_prep`'s journal committed. A crash mid-alias-loop leaves partial aliases with no rollback/recover
coverage. Low practical risk (idempotent-ish re-run), but inconsistent with everything else. **[GATE]**.

---

## B. Stale documentation (fix in P3)

1. **[STALE-DOC]** `ARCHITECTURE.md` §3/§7/§8 line counts: says main.py 1621 / mainfetch.py 507; actual
   3081 / 491. Footer says "Last updated 2026-05-30" yet content includes §6.4a (PR #20, 2026-06-08) and
   multi-ep aliases (PR #21, 2026-06-10) — refresh footer + counts.
2. **[STALE-DOC]** §3 repo layout: `docs/` "placeholder (.gitkeep only)" — actually ~50 files across 9
   feature folders; `tests/` "placeholder; no test suite exists" — contradicts §13 (67 passing tests).
   `assets/` claim needs verify.
3. **[STALE-DOC]** §16 "No transactional save" bullet contradicts §7.2 and §16's own first bullet
   (atomic save SHIPPED). Remove/mark fixed.
4. **[STALE-DOC]** §16 "mainfetch.load_library swallows errors silently" — fixed by IMP-A1 (mvcommon
   strict loader, `mainfetch.py:24-28`). Mark fixed.
5. **[STALE-DOC]** §16 code smell "Heavy code duplication … a shared mvcommon.py would DRY this" —
   mvcommon.py exists. Mark done.
6. **[STALE-DOC]** §17 Future Work: item 1 (mvcommon) DONE; item 2 (atomic saves) DONE except `.bak`
   rotation; item 6 (multi-device push) DONE via `device` flag (IMP-?); reframe remaining items.
7. **[STALE-DOC]** §7.8/§5: `cmd_prep_season` Strategy-1 regex documented as `[sS]\d+[eE](\d+(?:\.\d+)?)`
   — PR #19 changed it to `[sS]\d+[eE](\d+)` (no decimal, dotted-title fix). Comment at `main.py:1030`
   ("handles .5") is also stale in the SxxExx branch (only the `x`-separator + anime branches handle .5).
8. **[STALE-DOC]** README setup says `pip install requests   # missing from requirements.txt` — verify
   requirements.txt (46 bytes) and either fix the file or the claim. webdriver-manager too.
9. **[STALE-DOC]** `docs/git-pr-conventions.md` hardcodes `Co-Authored-By: Claude Opus 4.8` — propose
   "the executing model" wording (this session commits as Claude Fable 5).
10. **[STALE-DOC]** §12/§16: "Bare `except: pass` throughout load_library" — load_library is strict now;
    the bare-except claim survives only for cleanup paths. Tighten wording.

---

## C. Smaller improvement candidates (new, from this read)

1. **[PERF]** `calculate_file_hash` prints a `\r` progress line **every 64 KB block** (~16k prints/GB;
   Windows console I/O is slow). Throttle to time-based (e.g. 10/s) or every N MB. Low risk, nice win on
   60 GB files.
2. **[PERF]** mainfetch harvester resets `processed_files` between attempts → re-hashes every unmatched
   Downloads file (multi-GB) on attempt 2. Hoist the set.
3. **[IMP-CAND]** `repair_dummies` swap is `os.remove` + `os.rename` (`main.py:1945-1946`) — use
   `os.replace` for a no-gap atomic swap.
4. **[IMP-CAND]** `cmd_dispatch_fetch` uses literal `"python"` (`main.py:2719`) — use `sys.executable`
   (already a known §16 item; still unfixed).
5. **[IMP-CAND]** Dead scaffolding in mainfetch (`wait_for_download`, `automation_download_file`,
   `build_download_queue`) — known §8.7/§16 items; still present.
6. **[IMP-CAND]** No `--help`/argparse (known); no logging-to-file (print-only UX) — relevant to the
   daemon/service future: a service can't rely on console scraping.
7. **[IMP-CAND]** `fallback_query == specific_query` for non-chunk files (known §16) — still true.
8. **[IMP-CAND]** `parse_metadata_from_id` takes the FIRST 4-digit token as year — an id like
   `tv-en-1899-1899` (show "1899") would also match; fine, but a title-slug with a 4-digit number
   (`mov-en-2049-blade2049`? slug `2049` only if 4-digit) can mis-year. Cosmetic.
9. **[IMP-CAND]** `DEVICE_ALIASES` only maps 2 of the user's 4 Pixel 1 XLs (movies/series). The 4-device
   parallel-upload workflow is not first-class in code (no round-robin, no per-device queue). Relevant to
   the end-goal fetch/push daemon. **OPEN QUESTION for user** (see E1).
10. **[IMP-CAND]** `save_library` writes all three files on every save even when only one changed; no
    `.bak` rotation (the §17.2 leftover half).
11. **[IMP-CAND]** `cmd_sort`'s language priority map covers en/ta/hi only (others sink to 99) — fine,
    but `kor`/`ja`/`te`/`ma` exist in production; make the map data-driven when config lands (IMP-A5).

---

## D. Cross-checks against ARCHITECTURE claims (verified-true highlights)

- Balanced-split `+10 MB` fudge: confirmed (`main.py:190,204`).
- Atomic `save_library` via `mkstemp` + `os.replace`: confirmed (`mvcommon.py:104-113`).
- Journal durability (fsync + `os.replace`): confirmed (`main.py:583-592`).
- PONR placements: replace commit-rename `main.py:1804`; restore split-path `mark_point_of_no_return()`
  at `main.py:2185` **before** the chunk-delete loop. Matches §12a table.
- O-1 push semantics incl. `any_upload_done` branch: confirmed (`main.py:1592-1609`).
- Verify-or-bless (`bless_or_verify_merged_hash`): pure helper confirmed (`main.py:286-308`); eager
  promote-at-replace confirmed (`main.py:1842-1849`); re-split reset confirmed (`main.py:1381`).
- Multi-ep de-alias in all four group loops + mainfetch: confirmed.
- Disk pre-flight (1X/2X + max(1%,2GB) buffer; season = largest item): confirmed.

## E. Open questions for the user (ask at next interaction point)

- **E1.** The prompt says uploads run via **4** Pixel 1 XLs in parallel, but `DEVICE_ALIASES` maps only 2
  serials (movies/series) and the fetch side has 2 Chrome profiles (2 Google accounts). What's the real
  topology? (4 phones × 1 account each? 2 accounts × 2 phones? Are 2 of the 4 unmapped spares?) Shapes
  the multi-device push/fetch roadmap items + how many parallel upload lanes the daemon can assume.

---

*Last updated: 2026-06-12 (after full main.py/mvcommon.py/mainfetch.py/ARCHITECTURE.md read).*
