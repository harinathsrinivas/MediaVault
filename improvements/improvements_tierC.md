# Improvements — Tier C · Robustness & Reliability

> The category that `usage_commands.txt` actually screams for. Most repeated commands in your history are re-runs after a partial failure. This tier addresses the specific failure modes that cause those re-runs.

> **Cross-cutting context:**
> - Since PR #14 the auto-pilots no longer bare-`break`: a failed season prints an exact **resume-range command** (auto-rollback orchestrator unification). IMP-C1 builds the *automatic* resume on top of that messaging.
> - `mvcommon.retry()` (IMP-C2, done) wraps ADB push+mv (3 attempts, 1/4/16 s + jitter) and the Selenium trigger (one retry). `cmd_replace`'s 3-retry PermissionError loop predates it.
> - The Aindham Vedham orphan ([[project_followup_library_integrity]]) is the only known library integrity gap. Today's code would not produce it, but no command exists to AUDIT for similar drift (IMP-D4).
> - `cmd_set_uploaded` is a pure metadata override with no ADB-side sanity check.
> - mainfetch's `init_driver` returns None on failure and `cmd_fetch_route` exits cleanly, but trigger_download swallows per-chunk exceptions without escalating to "the session is dead, stop" (IMP-C6).
> - **2026-06-12 review additions:** IMP-C12–C15 are concrete bugs found by the fable-review full code read (`../docs/feature-fable-review/REVIEW_NOTES.md` §A).
> - **Attribute key (added 2026-06-12):** `Risk` = blast radius of MAKING the change. `If skipped` = the failure that keeps happening, with a scenario.

---

## IMP-C1: Auto-resume from last completed episode in cmd_prep_push_rep_season

- Category: other
- Priority: high
- Files: `main.py` — `cmd_prep_push_rep_season`; new progress-file schema in the season folder or under `~/.mediavault/state/`
- Current behavior: On failure the season pilot now keeps completed episodes and PRINTS the exact resume command (`prep_push_rep_season <id> "<folder>" SIZE_MB 9900 episodes 7-13 device series` — reconstructed by `_season_resume_cmd`), but the USER still has to copy/paste/run it. The historical pain (Mr Robot S02 `episodes 1-10` then `11-13`; BSG `1-10` then `11-11`; The Wire S01–S04; Peaky Blinders S05) is half-solved: no more range arithmetic, still a manual re-run.
- Proposed change:
  - At each step, before processing episode `mid`, write `<season_folder>/.mediavault_progress.json` with `{ "base_id": ..., "last_completed_ep": "<mid>", "started_at": "...", "status": "in_progress" }`.
  - On any failure, write `status: "failed", last_attempt: <ep>, failure_reason: "<message>"` and exit (keep printing the resume-range command — it stays the source of truth for manual override).
  - On invocation, BEFORE prep_season, check for an existing progress file: if `in_progress`/`failed`, banner + auto-skip already-completed episodes; if `complete`, archive the file (timestamped) and start fresh. `--restart` ignores it.
  - On final success, write `status: "complete"`.
- Rationale: The single highest-frequency pain point in usage history. Eliminating manual re-runs entirely (vs today's copy-paste) also makes unattended/daemon-driven season pushes possible.
- Goal: A failed batch run is automatically resumable by re-running the SAME command, no edits, no paste.
- Effort estimate: medium
- Risk: medium **(change-gate adjacent)** — sits directly on top of the season orchestrator whose resume-range messaging is part of the frozen rollback contract; the progress file must stay messaging-compatible and must NOT alter PONR/journal behavior. Run the change-gate checklist (`ROLLBACK_MECHANISM.md` §10) at plan time; a planner prompt already exists in `docs/next-tasks-planner-prompts.md`.
- If skipped: every failed season run still needs a human to paste the resume command — fine interactively, a hard blocker for the Tier S daemon's unattended season pushes (the daemon would have to parse its own stdout to find the resume command).
- Status: pending

---

## IMP-C2: Exponential-backoff retry logic for ADB and Selenium ops

- Category: other
- Priority: high
- Files: `mvcommon.py` (`retry()`), `main.py` `cmd_push`, `mainfetch.py` `trigger_download`
- Current behavior (pre-fix): single-attempt ADB push and Selenium triggers; transient USB/browser blips killed whole season runs.
- Proposed change: shared `retry()` helper with exponential backoff + jitter; wrap push+mv and the trigger body.
- Rationale: USB and browser-automation are inherently flaky.
- Goal: 95% of transient failures self-heal without user touch.
- Effort estimate: medium
- Status: done (feature/adb_selenium_retry, PR to main 2026-05-30)

---

## IMP-C3: Pre-flight health check command `doctor`

- Category: other
- Priority: high
- Files: new `cmd_doctor` in `main.py`; new subcommand (argparse arrives with IMP-A2 but doctor can ship on the manual parser first)
- Current behavior: There is no way to verify the environment is sane before starting a long operation. A 3-hour `prep_push_rep_season` can fail 5 minutes in because `mkvmerge` is missing, because ADB doesn't see the phone, because the Chrome profile is logged out, or because `C:\` is full.
- Proposed change:
  - New `python main.py doctor` command that runs in <5 seconds and prints PASS/FAIL/WARN for each check:
    1. `mkvmerge --version` succeeds at `MKVMERGE_PATH`; ffmpeg resolves (`resolve_ffmpeg`).
    2. `adb devices` lists the expected device(s) with state `device` (not `unauthorized`/`offline`); cross-check `DEVICE_ALIASES` serials.
    3. The three library JSONs exist, parse as JSON.
    4. The Chrome profile directories (`ChromeProfile`, `ChromeProfile_TV`) exist.
    5. Free disk space on `C:\Media` (warn <50 GB) and on any drive hosting entry folder_paths.
    6. No leftover `_parts/` folders containing chunks for entries marked `archived` (orphaned post-failure state).
    7. No dummy-sized files for entries marked `local_ready` (half-archived inconsistency).
    8. The Downloads folder is not full of `.crdownload` files from another tool.
    9. Stale rollback journals: fold in `recover --scan` (IMP-R3) so doctor lists pre-PONR journals with the exact `recover` command.
    10. Optional integrity quick-checks: `verify_library` summary (IMP-D4) — count orphans, missing parents, alias targets.
  - Exit code: 0 if all PASS or WARN, non-zero if any FAIL. `--json` output supported (after IMP-A4).
- Rationale: Cheap pre-flight catches the failure modes that account for most "ran for 30 minutes, failed at chunk 7" stories. The Tier S daemon should run doctor on startup and before every scheduled batch.
- Goal: Run `doctor` before any big batch. 80% of mid-run failures become surfaced as pre-flight errors instead.
- Effort estimate: medium
- Risk: low — a new read-only command; no existing path changes.
- If skipped: long runs keep discovering environment breakage mid-flight; the daemon especially needs this (an expired Chrome session at 3 AM otherwise = a night of failed fetches, see IMP-C6).
- Status: pending

---

## IMP-C4: ADB device serial pinning

- Category: other
- Priority: medium
- Files: `main.py` (`DEVICE_ALIASES`, `resolve_device`, `device` keyword on all four push commands)
- Current behavior (pre-fix): bare `adb ...` calls failed with "more than one device" when 2+ devices were connected.
- Proposed change: `device <id_or_name>` flag resolving aliases→serials, `adb -s <serial>` plumbing.
- Rationale: Multi-phone workflows (the user runs multiple Pixels) need deterministic targeting.
- Goal: Deterministic device selection regardless of how many devices ADB sees.
- Effort estimate: small
- Status: done (feature/adb-device-select, PR #2, merged 2026-05-28 — `device <id_or_name>` + `DEVICE_ALIASES{movies,series}` + `resolve_device()`; status corrected 2026-06-12, was wrongly still "pending". The original sub-items "config key in mvconfig" and "doctor lists serials" are folded into IMP-A5 and IMP-C3 respectively.)

---

## IMP-C5: Real fallback search query (strip UID and extension)

- Category: bug
- Priority: high
- Files: `mainfetch.py` — `fetch_single_entry`, `trigger_download`
- Current behavior: The two-attempt structure reuses `entry["search_term"]` for both attempts when the file is non-chunked, just changing the click index. `search_term` is built in `cmd_prep` as `<name> [<short_id>]<ext>` — verbose, long, and includes the bracketed UID and extension. Google Photos search is fuzzy and SHORTER queries often find results when the verbatim filename misses (e.g., punctuation differences). Today's "fallback" is not really a fallback.
- Proposed change:
  - Add a `fallback_query_strip()` helper that produces a broader query from a leaf filename:
    - Strip `[<short_id>]` (the bracketed UID).
    - Strip the file extension.
    - Strip trailing tags like `-FraMeSToR`, `[rartv]`, `[Ben The Men]`, `-FGT`, release-group identifiers.
    - Squash dots / underscores / hyphens into spaces.
    - Truncate to ~40 chars.
  - Use this broader query as the SECOND attempt's `fallback_query`. Keep attempt 1 as the precision (full search_term) attempt.
  - Document examples in the codebase: `"F1.The.Movie.2025.2160p.UHD.BluRay.Remux.DV.P7.HDR.MULTi[Ben The Men] [68b7b8].mkv"` → fallback `"F1 The Movie 2025"`.
- Rationale: Today's fallback is identical to the precision query, so it never recovers from a real query failure. A genuinely shorter query has a much higher hit rate on Google Photos' search. Hash-routing in the harvester keeps broader queries safe (a wrong match is rejected by hash, never mis-filed).
- Goal: Significantly increased fetch success rate on the second attempt, especially for files with verbose filenames or release-group tags.
- Effort estimate: small
- Risk: low — second-attempt query construction only; first attempt and hash-routing unchanged.
- If skipped: titles whose precision search misses (punctuation/fuzzy-tokenizer quirks) are simply unfetchable without a manual `set_search` — for a daemon-triggered fetch that means a "fetch failed" tile and a trip to the PC, the exact thing the end goal abolishes.
- Status: pending

---

## IMP-C6: Detect Google Photos session expiry early

- Category: bug
- Priority: high
- Files: `mainfetch.py` — `cmd_fetch_route`, `trigger_download`
- Current behavior: When `trigger_download` finds 0 thumbnails, it returns False (after the C2 one-retry). For a multi-chunk movie, this cascades into "0 of 10 chunks succeeded" with no clear cause. The most likely real-world cause: the Chrome profile's Google session has expired and `photos.google.com` is showing a login screen rather than search results.
- Proposed change:
  - In `trigger_download`, after `driver.get(...)` and wait, check the page URL/title: redirected to `accounts.google.com` → raise `SessionExpiredError`; body shows "Sign in" without Photos content → same.
  - In `cmd_fetch_route`, catch `SessionExpiredError` at the top and abort with the remediation message ("profile `{profile}` is logged out; open Chrome with `--user-data-dir={path}`, log in, re-run").
  - Heuristic backstop: 3 consecutive 0-thumbnail results on the SAME profile → same error.
  - (Daemon tie-in, Tier S: the daemon turns this error into an in-client "vault needs attention" alert + doctor FAIL.)
- Rationale: Silent session-expiry is the failure mode that wastes the most user time — a 90-minute wait for a fetch that never had a chance.
- Goal: Session-expiry fails fast with a clear remediation. No 90-minute wait on a doomed run.
- Effort estimate: small
- Risk: low — adds early-exit detection; trigger behavior on healthy sessions unchanged.
- If skipped: the single most likely silent killer of unattended fetches stays silent. Scenario: cookies expire while you're on vacation; every couch fetch that week "times out" after 5+ minutes with no explanation until someone checks the PC.
- Status: done (satisfied by IMP-C17 — shared mainfetch.SessionExpiredError + check_session_alive; trigger_download/cmd_fetch_route abort a logged-out fetch fast with a remediation message + a 3-consecutive-zero backstop; tests in tests/test_session_detector.py)

---

## IMP-C7: cmd_set_uploaded should verify via ADB

- Category: bug
- Priority: medium
- Files: `main.py` — `cmd_set_uploaded`
- Current behavior: Pure metadata override. Sets `uploaded=True, status="onboarded"` with no check that the chunks are actually on the phone (or in Google Photos). If misused, the user can mark something uploaded that isn't, then run `replace` and delete the only copy.
- Proposed change:
  - Before flipping the flags, do a single `adb shell ls /sdcard/Media/<rel_path>/` and parse the listing.
  - For a split entry, confirm that at least N-1 of N expected chunks are listed remotely (allow one missing chunk because Photos may have already cleaned up after upload). Also accept the `.mvmeta.json` sidecar as corroborating evidence.
  - For a single-file entry, confirm the renamed file `<name> [<short_id>]<ext>` exists remotely.
  - If the check fails, abort with an error. Add `--force` flag to skip the check for the genuine emergency-rescue case.
- Rationale: This command is the most dangerous in the codebase — it short-circuits the upload-confirmation safety. A misclick or wrong-id paste could make `replace` delete the only copy of a 70 GB file. A 1-second ADB check is cheap insurance.
- Goal: Convert `set_uploaded` from a foot-cannon into a verified, safe override.
- Effort estimate: small
- Risk: low-medium — adds a guard to an emergency command; `--force` preserves the old behavior for the cases the command exists for (post-Photos-cleanup, multi-session pushes where the phone copy is already gone — expect `--force` to be needed often; message must say so).
- If skipped: one wrong-id paste away from `replace` destroying the only local copy of a file whose upload never happened. The dummy swap makes the loss invisible until a fetch months later returns nothing.
- Status: pending

---

## IMP-C8: Post-push remote verification

- Category: other
- Priority: medium
- Files: `main.py` — `cmd_push` (`_verify_chunk_hash`, `PUSH_VERIFY_REMOTE` gate)
- Current behavior (pre-fix): no after-push integrity check of remote bytes.
- Proposed change: optional `adb shell sha256sum` per chunk vs stored hash, retried under C2.
- Rationale: Defense against silent in-transit corruption.
- Goal: Catch corruption at push time, not at restore weeks later.
- Effort estimate: small
- Status: done (feature/post_push_verify, PR to main 2026-05-30 — shipped gated OFF via `PUSH_VERIFY_REMOTE=False`; turning it on without a source edit waits on IMP-A5 config)

---

## IMP-C9: Atomic cmd_replace via two-rename pattern

- Category: bug
- Priority: medium
- Files: `main.py` — `cmd_replace`
- Current behavior (pre-fix): delete-then-rename left a window with neither original nor dummy on disk.
- Proposed change: two-rename pattern (`original→.tobedeleted`, `dummy→original`) + stale sweep.
- Rationale: Power-loss safety for 70 GB irreplaceable files.
- Goal: At any instant, either the original or the dummy exists at the expected path.
- Effort estimate: small
- Status: done (fix/atomic_replace, PR to main 2026-05-29; the commit rename is now also the rollback PONR)

---

## IMP-C10: Sidecar reconciliation command

- Category: other
- Priority: low
- Files: new `cmd_reconcile_sidecars` in `main.py`; uses sidecar files `uid` and `<short_id>.sha256` written by `cmd_prep` and `<chunk>.sha256` files in `checksums/`; since PR #14-era work also the remote `.mvmeta.json`
- Current behavior: Sidecar files are written but NEVER read by any code path. They exist as "belt and suspenders backup" per the architecture, but no command actually uses them. If the library JSONs are destroyed, the sidecars contain enough information to partially reconstruct them — but reconstruction is manual. (The remote `.mvmeta.json` written on full push success extends this redundancy to the phone/cloud side and is likewise never read.)
- Proposed change:
  - New `python main.py reconcile_sidecars [folder_or_root]` command.
  - Walks the folder, finds all `uid` and `<short_id>.sha256` files.
  - For each: library entry exists → verify hash agreement, report drift; no entry → report "orphan sidecar", offer re-prep under a suggested ID.
  - For `checksums/<chunk>.sha256`: cross-check against `entry["split_info"]["chunks"]`.
  - Read-only by default; `--repair` flag to attempt fixes (e.g., re-create library entry from sidecar; future: rebuild from remote `.mvmeta.json` listings).
- Rationale: Sidecars are written religiously but never used. Either delete them entirely or actually use them as the disaster-recovery layer they were designed to be. This task picks "use them".
- Goal: Sidecar files serve a real purpose. Library JSON destruction becomes recoverable from disk state.
- Effort estimate: medium
- Risk: low (read-only default); `--repair` writes library entries — keep it explicitly opt-in and journaled.
- If skipped: the disaster-recovery story stays theoretical — after a hypothetical triple-JSON loss (single SSD, no .bak rotation yet), rebuilding ~570 entries means hand-reading sidecars for a week.
- Status: pending

---

## IMP-C11: Hash-mismatch quarantine in cmd_restore

- Category: bug
- Priority: medium
- Files: `main.py` — `cmd_restore`, `quarantine_restore_file`
- Current behavior (pre-fix): a bad restore file stayed in `restore/`, trapping re-fetches behind the existence check.
- Proposed change: quarantine to `restore/quarantine/<name>.<ts>`; self-healing re-fetch.
- Rationale: Bad chunks shouldn't require manual deletion.
- Goal: Hash mismatches recoverable by simply re-running fetch.
- Effort estimate: small
- Status: done (feature/restore_quarantine, PR #6, merged 2026-05-29; later extended by PR #20 with pre-merge per-chunk verification on the split path)

---

## IMP-C12: Fix multi_ep_alias crashes in scan_unprepped and local_status

- Category: bug
- Priority: high
- Files: `main.py` — `cmd_scan_unprepped` (known-paths build), `cmd_local_status` (pending filter) · found 2026-06-12 (REVIEW_NOTES §A1/§A2)
- Current behavior: PR #21's `multi_ep_alias` entries carry only `type`/`alias_of`/`parent_id`. Both commands iterate the whole library skipping ONLY `season_map`:
  - `cmd_scan_unprepped` does `os.path.join(entry['folder_path'], entry['filename'])` → **uncaught KeyError** on the first alias → the command crashes with a traceback. Live data contains aliases (the E19E20 case PR #21 was built for), so this daily-driver command is likely broken in production right now.
  - `cmd_local_status` counts aliases as pending (no `uploaded` key → falsy) and then renders `item['filename'][:40]` where filename is `None` → **TypeError** crash; even if rendering were guarded, aliases would appear as phantom pending uploads.
- Proposed change: skip `entry.get("type") == "multi_ep_alias"` in both iterators (mirror the season_map skip). Add a regression test seeding an alias into the sandbox library and invoking both commands.
- Rationale: These are the two "what's the state of my library" commands; both crash on data the system itself now writes. The memory rule "any new code iterating season_map children must resolve/skip aliases" missed that whole-library iterators are also in scope — update that memory note when fixing.
- Goal: Both commands run clean on a library containing aliases; alias entries never appear as pending items.
- Effort estimate: small
- Risk: low — two one-line skips in read-only commands + tests.
- If skipped: `scan_unprepped` and `local_status` remain crash-on-invoke for any library containing a combined-episode file — i.e., **already today**: the user runs `local_status` to plan the next Pixel batch and gets a TypeError instead of the list.
- Status: done (fix/alias_crash_and_smoke_gate — multi_ep_alias alias-safety; both iterators now skip `multi_ep_alias` alongside `season_map`; regression tests in `tests/test_alias_consumers.py` and `tests/smoke/test_smoke_all_commands.py`)

---

## IMP-C13: Graceful alias handling in single-id commands

- Category: bug
- Priority: medium
- Files: `main.py` — `cmd_push`, `cmd_replace`, `cmd_restore`, `cmd_check`, `cmd_verify_restore`, `cmd_fetch_restore` (single-item branch) · found 2026-06-12 (REVIEW_NOTES §A3)
- Current behavior: Group loops and `mainfetch.resolve_targets` de-alias correctly (PR #21), but every direct single-id command accesses `entry['folder_path']`-style keys without `_resolve_alias`. Passing a secondary episode id (`tv-en-2009-bsg-s04e20`) → raw KeyError traceback (`cmd_replace` returns False silently). Inconsistent with `fetch`, which resolves the same id fine — so `fetch tv-...e20` works and the follow-up `restore tv-...e20` crashes.
- Proposed change: at entry lookup in each command, call `_resolve_alias`; if resolution happened, print one info line ("ℹ️ tv-…e20 is part of the combined file registered as tv-…e19 — operating on that") and proceed on the primary. `cmd_prep` additionally must refuse to prep OVER an existing alias id (it currently would overwrite the alias with a leaf entry, corrupting the alias chain).
- Rationale: The user thinks in episode numbers; which one is "primary" for a combined file is an implementation detail they shouldn't have to remember at the CLI.
- Goal: Any command accepts any episode id of a combined file and operates on the right entry, with a visible note.
- Effort estimate: small
- Risk: low-medium — touches the entry-lookup head of six commands; behavior for non-alias ids must stay byte-identical (guard with tests per command).
- If skipped: every direct operation on a secondary episode id keeps crashing with a traceback; worst variant is the `cmd_prep` overwrite corrupting an alias into a fake leaf (then group ops process the same file twice).
- Status: done (fix/alias_crash_and_smoke_gate — multi_ep_alias alias-safety; `_resolve_alias` called at lookup head of `cmd_check`/`cmd_push`/`cmd_replace`/`cmd_restore`/`cmd_verify_restore`; `cmd_prep` refuses to prep over an alias; regression tests in `tests/test_alias_consumers.py` and `tests/smoke/test_smoke_all_commands.py`)

---

## IMP-C14: CLI parser papercuts — push_group hang, mainfetch argv guard, silent replace

- Category: bug
- Priority: medium
- Files: `main.py` — `push_group` argv parser, `cmd_replace` lookup; `mainfetch.py` — `__main__` guard · found 2026-06-12 (REVIEW_NOTES §A4/§A5/§A6)
- Current behavior:
  1. `push_group` parser: a value-taking keyword (`SIZE_MB`/`SIZE_GB`/`COUNT`/`episodes`/`device`) as the FINAL token never increments `i` → **infinite loop** (process hangs, Ctrl-C). The `push` parser has proper `else: sys.exit(1)` arms; `push_group`'s doesn't.
  2. `mainfetch.py` `__main__`: guard checks `len(sys.argv) < 2` but then reads `sys.argv[2]` → IndexError on `python mainfetch.py fetch`; should be `< 3` (+ verify `sys.argv[1] == "fetch"`).
  3. `cmd_replace` on an unknown id returns False with **no output at all** — a typo'd `replace` looks like success at a glance.
- Proposed change: add the missing `else: print usage; sys.exit(1)` arms to `push_group` (mirror `push`); fix the mainfetch guard; add the "ID not found" message to `cmd_replace`. One small PR, three tests.
- Rationale: All three are trip hazards on the exact commands the user types most under stress (re-pushing after failures).
- Goal: malformed invocations fail fast with usage text; no hangs; no silent no-ops.
- Effort estimate: small
- Risk: low — parser error-arms and one print; happy paths untouched. (Made obsolete by IMP-A2 eventually — do this cheap fix first anyway; argparse migration is a bigger lift.)
- If skipped: a forgotten trailing `device` token freezes the console mid-session and the user must kill the process, wondering if a push was in flight; typo'd replaces keep masquerading as successes.
- Status: done (fix/cli_parser_papercuts — push_group missing-value fail-fast arms mirroring push; parse logic extracted to main.parse_push_group_args / mainfetch.parse_fetch_args; mainfetch bare-invoke guard fixed; cmd_replace prints not-found; unit tests in tests/test_cli_parsers.py)

---

## IMP-C15: Micro-robustness batch — repair_dummies atomic swap, _verify_chunk_hash guard

- Category: bug
- Priority: low
- Files: `main.py` — `cmd_repair_dummies` (remove+rename), `_verify_chunk_hash` (stdout parse) · found 2026-06-12 (REVIEW_NOTES §C3/§A10)
- Current behavior:
  1. `cmd_repair_dummies` swaps via `os.remove(current)` then `os.rename(tmp, current)` — a kill between the two leaves no file at the path (the C9 lesson, un-applied here; low stakes since it's "just" a dummy, but the library row then points at nothing until the next repair run).
  2. `_verify_chunk_hash` parses `result.stdout.strip().split()[0]` — empty stdout (device quirk) → IndexError, which is not in `retry_on` and surfaces as a raw failure instead of the warn-and-skip the function promises.
- Proposed change: use `os.replace(tmp, current)` (single atomic call) in repair_dummies; guard the sha256sum parse (empty/garbled stdout → warn-and-skip path). Two tests.
- Rationale: Same safety idioms the codebase already adopted elsewhere (C9 two-rename, OD-2a warn-and-skip) applied to the two spots that missed them.
- Goal: No window without a file during dummy repair; remote-verify never crashes on odd device output.
- Effort estimate: small
- Risk: low — strictly-narrower failure behavior in two helpers.
- If skipped: cosmetic-to-rare failures; the repair_dummies window mainly matters during bulk runs (423 dummies regenerated in one 2026-05-27 sweep — that's 423 windows).
- Status: done (fix/micro_robustness_c15 — cmd_repair_dummies non-atomic remove+rename replaced with single atomic os.replace + explicit multi_ep_alias skip; _verify_chunk_hash hex-validates the device sha256 first token (empty/garbled → warn-and-skip, only a well-formed differing hash raises CalledProcessError); unit tests in tests/test_repair_dummies.py + new cases in tests/test_cmd_push_verify.py)

---

## IMP-C16: Fetch profile must match the per-content Google account (anime is its own account now)

- Category: bug
- Priority: high
- Files: `mainfetch.py` — `CHROME_PROFILES`, `cmd_fetch_route`; relates to IMP-X2 (topology) · found 2026-06-12 (user confirmed the account topology)
- Current behavior: there are only **two** Chrome profiles — `default` (movies account) and `tv` — and `cmd_fetch_route` sends BOTH `tv-*` and `ani-*` to the `tv` profile. The user has confirmed the real topology is **three separate Google accounts: movies, series, anime**. So anime chunks are uploaded to the *anime* account, but anime fetch drives the *series* account's logged-in Chrome session → the search finds 0 thumbnails and the restore fails, looking exactly like a session expiry (IMP-C6). This is latent today only because anime has never been chunk-restored in production (0 of 140 anime leaves have `split_info`; ARCHITECTURE §6.2) — the first real anime restore would hit it.
- Proposed change:
  - Add a third profile `anime` → `C:\Media\Utils\ChromeProfile_Anime` (signed into the anime Google account), and route `ani-*` there in `cmd_fetch_route` (movies→default, `tv-*`→tv, `ani-*`→anime).
  - Generalize: make the id-prefix → profile map data-driven (config, IMP-A5) so adding a 4th account (e.g., a backup account for X1) is a config edit, not a code edit. This is the fetch-side mirror of the per-account push routing X1 needs.
  - One-time setup: log the new ChromeProfile_Anime into the anime account (same manual login the other two profiles required).
- Rationale: with three accounts, the two-profile routing is simply wrong for anime; fixing it is a precondition for ever restoring an archived anime title, and the data-driven map is the seam X1/X4's multi-account fetch fallback builds on.
- Goal: `fetch`/`restore` of an `ani-*` id drives the anime account's session and succeeds; the profile map is config-driven.
- Effort estimate: small
- Risk: low — additive profile + a routing branch; movies/series routing unchanged. Verify the new profile is logged in (pairs with IMP-C6 session detection so a logged-out anime profile fails loudly, not silently).
- If skipped: the first attempt to restore an archived anime title silently fails (0 thumbnails on the wrong account) and looks like a session problem — a confusing dead-end for a whole third of the library, and it blocks the couch-vault flow for anime entirely.
- Status: done (fix/anime_fetch_profile — added 3rd Chrome profile `anime` → ChromeProfile_Anime; id-prefix→profile routing extracted to data-driven mainfetch.ID_PREFIX_PROFILE + pure profile_for_id(); ani-* now drives anime account, tv-* series, movies movies; external-config sourcing deferred to IMP-A5; unit tests in tests/test_anime_fetch_routing.py + smoke coverage in test_anime_fetch_routing_profile_selection)

---

## IMP-C17: Fetch-session keep-alive + shared logged-out detector (prevents the silent logged-out dead-end)

- Category: other
- Priority: high
- Files: `mainfetch.py`, `mvcommon.py`, `tools/warm_profiles.py`, `tools/notify_toast.py`, `tools/mediavault_warm_profiles.xml`
- Current behavior: When a Chrome profile's Google session expires during an idle period between fetches, the next fetch silently fails (0 thumbnails, looks like session expiry). The user doesn't learn the session is dead until attempting a manual fetch or waiting for a scheduled one. Meanwhile, time and bandwidth are wasted.
- Proposed change:
  - Hybrid approach: (1) shared `SessionExpiredError` + `check_session_alive()` in mainfetch.py reused by both the live fetch path (IMP-C6 fast-fail + remediation) and the keep-alive runner; (2) a daily idle-gated Selenium keep-alive `tools/warm_profiles.py` (per-profile OK/LOGGED_OUT/LAUNCH_FAIL → console + `~/.mediavault/logs/warm_profiles.log` + Windows toast via `tools/notify_toast.py` + non-zero exit), registered via `tools/mediavault_warm_profiles.xml` (Task Scheduler, daily 03:00, run-only-if-idle); (3) a single-flight `mvcommon.fetch_session_lock` so the warm-up never collides with a live fetch on CDP port 9222. One-time profile-hardening checklist in README.
- Rationale: Silent session-expiry between fetches is a failure mode that wasted the most user time in initial testing — the warm-up catches it fast, and the shared check routine lets the live fetch abort early with a clear remediation. Together they ensure a logged-out session is detected within hours, not days.
- Goal: Sessions stay warm or get detected fast. No 90-minute fetch waits. Unattended multi-day pipelines no longer silently fail at the session.
- Effort estimate: medium
- Risk: low — a daemon/scheduler component that touches the session but does not mutate the library. The shared `check_session_alive` routine undergoes the same testing as IMP-C6, and the lock mechanism is proven in prior work (atomic commit patterns).
- If skipped: unattended fetch pipelines (e.g., couch-vault daemon) will silently fail on session expiry and require manual intervention to diagnose (the C6 fix only helps live fetches, not idle detection). One logged-out profile can orphan days of scheduled fetches.
- Note: satisfies IMP-C6 session-expiry detection (shares the check routine with it); the ban-sentinel (`IMP-X5`) stays out of scope.
- Status: done (feature/fetch_session_keepalive — shared SessionExpiredError + check_session_alive in mainfetch.py reused by both live fetch (IMP-C6) and keep-alive runner; daily Selenium warm_profiles.py (per-profile status log + Windows toast); Task Scheduler registration via mediavault_warm_profiles.xml; single-flight fetch_session_lock in mvcommon.py; tests in tests/test_session_detector.py, tests/test_warm_profiles.py, tests/test_notify_toast.py + smoke test_fetch_route_logged_out_aborts)

---

## IMP-C18: Episode-range filter mis-parses season-glued anime IDs (sSSEE) → range fetch/restore silently filters to 0

- Category: bug
- Priority: high (Band 0 — silent breakage; reports success while doing nothing)
- Files: `mainfetch.py` — `resolve_targets` (fetch range filter, the `[eE](\d+)$` → `x(\d+)$` → `(\d+)$` ladder); `main.py` — the batch-restore range filter (~line 2393-2403). A CORRECT reference implementation already exists at `main.py` (~line 2705) which strips the base id first. Found 2026-06-14 via `python main.py fetch_restore ani-ja-2013-kurokosbasketball-s02 episodes 2-3`.
- Current behavior / WHEN IT OCCURS: For anime season-maps whose child IDs glue the season and episode together with NO `e`/`x` separator — e.g. `ani-ja-2013-kurokosbasketball-s0202` = season 02 + episode 02 — the range filter's fallback regex `(\d+(?:\.\d+)?)$` captures the WHOLE trailing run of digits (`0202` → **202**), i.e. the season digits glued to the episode, instead of just the episode (`2`). So `… episodes 2-3` compares 201 / 202 / 203 … against the range [2, 3], matches NONE, prints "🎯 Filtered to 0 episodes" → "❌ No valid targets found", the restore phase likewise prints "Filtered to 0 items", and the run still reports "✅✅✅ FETCH & RESTORE COMPLETE." with 0 files — a misleading silent success. Triggers on ANY `sSSEE`-style anime season whenever an `episodes <range>` is supplied to fetch / fetch_restore / batch restore. NOT affected: single-id fetches (`…-s0202` directly), whole-season fetches (no range), and TV/anime IDs that use an `e`/`x` separator (the first two regexes match those correctly). Verified 2026-06-14: naive filter `'2-3'` → `[]`; `'202-203'` → `[s0202, s0203]`; base-stripped leftover for `s0202` = `'02'` (parses correctly to episode 2).
- Proposed change:
  - Make both broken filters season-aware by STRIPPING the base/season id before reading the episode number — exactly the approach already working at `main.py` (~line 2705): `ep_str = mid.replace(base_id, "")` then `re.search(r'^[eExXsS]?(\d+(?:\.\d+)?)$', ep_str)`. For the fetch path the base id is the season-map `manual_id` (in scope in `resolve_targets`); for the batch-restore path the group/season id is already in scope. Then `…-s0202`.replace(`…-s02`) → `02` → episode 2, and `episodes 2-3` works as the user expects.
  - Keep the existing `eNN`/`xNN` handling for separator-style IDs (they already parse correctly).
  - Factor the episode-number extraction into ONE shared helper (e.g. `mvcommon.episode_num_from_id(child_id, base_id)` or a `mainfetch`/`main` shared util) used by ALL range-filter sites (fetch + batch restore + the existing main.py:2705 site) so the three copies can't drift again.
- Rationale: A user typing the obvious `episodes 2-3` silently gets nothing AND a success message — the worst kind of bug (wrong result, no error, no exit code). It affects a whole class of anime seasons (every `sSSEE` season), and the same regex is duplicated across fetch + restore so both halves of the couch flow are broken in lockstep.
- Goal: `fetch` / `fetch_restore` / batch restore with `episodes 2-3` on an `sSSEE` anime season selects EXACTLY episodes 2 and 3; a regression test pins the `ani-…-s0202` ID shape (and a `…s0216.5` half-episode); all existing TV/`eNN` ranges still pass.
- Effort estimate: small (one shared extractor + 2-3 call-site swaps + a unit/smoke test).
- Risk: low — it narrows the extraction; separator-style IDs are unchanged. Cross-command smoke gate (`tests/smoke`) must stay green; add a case covering BOTH `sSSEE` and `…eNN` so the two formats can't regress.
- Workaround until fixed: fetch per-episode by full child id (`python main.py fetch_restore ani-ja-2013-kurokosbasketball-s0202`), or use the glued numbers as the range (`episodes 202-203`) — the latter RELIES on the bug and will stop working once it is fixed.
- If skipped: every range-based anime season fetch/restore for the `sSSEE` ID format silently no-ops while reporting success — users believe episodes were fetched/restored when nothing happened, and only notice later when the files are missing.
- Status: done (fix/imp_c18_episode_range — shared mvcommon.episode_num_from_id (prefix-strip + anchored ^[eExX]?(\d+(?:\.\d+)?)$) routes all 5 range-filter sites so glued sSSEE ids like …-s0202 parse to episode 2 not 202; 0-via-range now warns + suppresses the false ✅✅✅ auto-pilot banner; tests in tests/test_episode_range_filter.py + smoke sSSEE cases)

---

## IMP-C19: mkvmerge failures are undiagnosable — its error text is discarded

- Category: bug
- Priority: high (Band 0 — a failure with no recoverable cause, after expensive work)
- Files: `main.py` — `split_video_file` (the `subprocess.run` + `CalledProcessError` handler), `merge_video_files` (same defect) · found 2026-08-24 archiving `mov-kor-2003-ataleoftwosisters`
- Current behavior: mkvmerge writes its `Error:` / `Warning:` lines to **stdout**, not stderr. `split_video_file` ran it with `stdout=subprocess.DEVNULL` and, although it passed `stderr=subprocess.PIPE`, the handler printed only `f"...: {e}"` — the `CalledProcessError` repr — and never touched `e.stderr`. The diagnosis was therefore destroyed twice over, and a real archival run surfaced as nothing but `Command '[...]' returned non-zero exit status 2`, *after* prep had already deep-scanned and whole-file hashed a 62 GB master. The in-code comment claimed the opposite ("Added stderr capture to output real error messages"). `merge_video_files` had the same defect with no stderr capture at all — worse, because a merge failure happens during **restore**, when the chunks are the only surviving copy.
- Proposed change: capture both streams at both call sites and echo mkvmerge's own diagnosis on failure via one shared helper — the named `Error:`/`Warning:` lines, falling back to the output tail when mkvmerge dies without one (the libfmt exit-3 crash prints only `terminate called ... fmt::v11::format_error`). Success paths print nothing new.
- Rationale: without this, every mkvmerge-side failure — unsplittable codec, permissions, disk, corrupt input — is indistinguishable at the console. The 2026-08-24 incident cost a full 62 GB prep plus a manual re-run of the command by hand just to read the one line mkvmerge had already printed.
- Goal: any non-zero mkvmerge exit prints the reason mkvmerge gave.
- Effort estimate: small
- Risk: low — reporting-only; no behavior change on success.
- If skipped: every future mkvmerge failure costs a manual reproduction to learn its cause.
- Status: done (fix/imp_c19_c20_split_diagnostics, commit `1af16a3` — `_print_mkvmerge_failure()` helper wired into both `split_video_file` and `merge_video_files`; both now capture stdout+stderr with `text=True`; tests in `tests/test_mkvmerge_error_surfacing.py` (5 cases incl. the libfmt tail fallback and a bytes-stream guard); smoke 76/76, suite 683/683). Incident: `docs/edge-case-unsplittable-tracks/`.

---

## IMP-C20: no pre-flight for tracks mkvmerge cannot split (FLAC) — fails after a full prep

- Category: bug
- Priority: high (Band 0 — wastes an entire prep, and recurs on a whole class of sources)
- Files: `main.py` — `cmd_push` (the split branch, alongside the `_free_space_ok` pre-flight) · found 2026-08-24
- Current behavior: mkvmerge **cannot** `--split` a file containing a FLAC audio track, and refuses identically across every split mode — `size:`, `duration:`, `timestamps:`, `parts:`, `chapters:`, `frames:` (all six measured; see `docs/edge-case-unsplittable-tracks/CODEC-SPLIT-MATRIX.md`). The refusal lives in the FLAC packetizer, so no flag overrides it and no alternative split strategy exists. `cmd_push` discovered this only when the split ran — after `prep` had deep-scanned and hashed the master. On the incident file (62.5 GB, Italian FLAC dub in a DV P7 BD remux) that was a fully wasted run, rolled back cleanly but repeated on every retry. Neither of the tempting escapes is available: an unsplit push exceeds the ~10 GB Google Photos per-video cap, and raw byte-splitting produces fragments Photos will not ingest — chunks must stay valid playable MKVs.
- Proposed change: probe with a header-only `mkvmerge -J` before splitting; if any track's codec id is on a measured unsplittable list, stop and name the track(s) plus the runbook path. Place it with the existing `_free_space_ok` check — read-only, before `makedirs`/journal records, so it is a clean pre-PONR early return with nothing to roll back. The resume branch (existing `_parts/`) never reaches it.
- **Deliberately NOT auto-fixed** (user decision, 2026-08-24): MediaVault must never convert or drop a track on its own. A codec change is permanent and irreversible against what becomes the only surviving copy once `replace` swaps in the dummy. The command tells the operator what to do; the operator decides what and when. Assisted remux, if ever built, is explicit opt-in only — see `docs/edge-case-unsplittable-tracks/CODE-GAPS.md` Gap 3.
- Rationale: the failure is deterministic and detectable from the file header in milliseconds, but was only discovered after the most expensive step in the pipeline.
- Goal: a file with an unsplittable track is refused before `prep` work is spent, naming the track and the fix.
- Effort estimate: small
- Risk: low — a false positive would block a legitimate archive, so the registry stays conservative (only measured codecs; a probe failure returns empty rather than blocking).
- If skipped: every FLAC-bearing source — common on European remuxes with lossless-wrapped dubs — burns a full prep before failing.
- Follow-up (DONE, commit `1b1a899`): the first commit gated only at `push`, which still let `prep_push_rep` spend its deep scan + whole-file hash first — the exact waste this task was about. Both auto-pilots now gate BEFORE their prep leg via a shared `refuse_if_unsplittable()`; the season variant probes every episode because `cmd_prep_season` hashes the whole season before the first push. Both no-op when no split was requested.
- Follow-up (still not done): `cmd_push_group` pre-checks disk space only, so a group push still learns about an unsplittable member at that item's turn.
- Status: done (fix/imp_c19_c20_split_diagnostics, commits `e2b799c` + `1b1a899` — `UNSPLITTABLE_CODEC_IDS = {"A_FLAC"}` + `find_unsplittable_tracks()` probe + shared `refuse_if_unsplittable()` gating `cmd_push` AND both auto-pilots ahead of prep; TrueHD suspected but deliberately excluded as unmeasured; tests in `tests/test_unsplittable_preflight.py` (10 cases, incl. asserting zero journal records at abort and that the gate precedes prep); suite 686/686). Incident + measured matrix: `docs/edge-case-unsplittable-tracks/`.

---

## IMP-C21: `tools/remux_unsplittable.py` — the manual fix for a file mkvmerge cannot split

- Category: tooling
- Priority: medium
- Files: `tools/remux_unsplittable.py` (new, in `tools/` beside `warm_profiles.py` — standalone; imports `main` only for `resolve_ffmpeg`/`find_unsplittable_tracks`/`UNSPLITTABLE_CODEC_IDS` so tool and pre-flight cannot disagree) · added 2026-08-25
- Context: IMP-C20 made MediaVault *detect* an unsplittable track and refuse, deliberately without fixing it. This is the fix, delivered as a separate manual tool per the user's standing decision (2026-08-24: *"I dont want to auto convert some track to a lower quality one, or skip that. I want control of deciding what and when to do so"*, and 2026-08-25: *"keep it separate. do not call it automatically if any issues. I will do that manually after checking each case error by error"*).
- Behavior:
  - **Nothing in MediaVault invokes it.** It is not imported by `main.py`/`mainfetch.py`/`mvcommon.py`, not wired to any command, and not offered as an automatic remedy. A guard test asserts none of those modules so much as mention it.
  - **Dry run unless `--run`.** The default prints the offending track, the plan, the exact ffmpeg argv and the disk arithmetic, then stops — so each case can be inspected before committing to it.
  - **Never destructive.** Writes `<name>.remux.mkv` (or `--out`), refuses to overwrite an existing output, and never touches the original. The delete/rename swap is left to the operator, with the runbook's disk-space sequencing quoted back.
  - **Computes the `-c:a:N` index** rather than assuming it. On the incident file the FLAC track is overall stream 4 but audio index 3; using the overall index would have transcoded a subtitle, and `1` would have destroyed the DTS-HD MA main track.
  - **Verifies after remuxing**: stream count, duration drift, and the Dolby Vision configuration record (so a DV P7 remux that silently lost its RPU cannot pass); `--verify-streams` adds a per-stream checksum pass, comparing the converted track as decoded PCM.
  - **Refuses to guess**: more than one unsplittable track (that is several separate decisions), or a mkvmerge/ffprobe disagreement about what sits at that index.
  - `--codec wavpack` (default, lossless) / `pcm` (lossless, universal, several GB larger) / `drop`. **No lossy targets** — the tool will not quietly degrade audio; that stays a manual ffmpeg decision.
- Rationale: the remux recipe was verified end-to-end during the 2026-08-24 incident but lived only as prose in the runbook, where the `-c:a:N` index trap is easy to get wrong and the mistake is silent and irreversible.
- Goal: one command per file, inspectable before it runs, that produces a verified splittable master without MediaVault ever making the quality decision.
- Effort estimate: small
- Risk: low — a standalone script; no MediaVault code path changed (`main.py` is untouched on this branch).
- If skipped: every FLAC-bearing source is a hand-built ffmpeg invocation with a silent-corruption trap in the middle of it.
- Status: done (feature/imp_c21_remux_unsplittable — dry-run-by-default CLI + computed audio-index mapping + DV/duration/stream verification; 11 tests in `tests/test_remux_unsplittable.py` incl. the "MediaVault never invokes this tool" guard; smoke 76/76, suite 697/697; exercised end-to-end against real ffmpeg+mkvmerge — the source refused to split, the remux split cleanly, and all streams verified MATCH incl. decoded PCM on the converted track).

---

## IMP-C22: Anime per-episode enrichment never lands — `_episode_se_of` mis-parses season-glued anime ids (a 4th copy of the episode-number parser that drifted)

- Category: bug
- Priority: high (Band 0 — silent wrong-result, no error, affects every anime entry — the user has 145)
- Files: `main.py` — `_episode_se_of` (~line 1725) and its regex `_ANIME_EP_TAIL_RE = r"(\d{1,4})$"` (~line 1722). Consumers: `_download_unit_images`'s per-episode still loop and `_apply_episode_overviews` (both in the `cmd_enrich_metadata` / `_enrich_after_archive` enrichment path). The CORRECT reference implementation already exists: `mvcommon.episode_num_from_id(child_id, base_id)`, the shared helper introduced by **IMP-C18** precisely to stop this class of drift. Found 2026-08-29 during IMP-D22 anime test coverage; pre-existing, NOT introduced by D22.
- Current behavior: `_episode_se_of` reads bare trailing digits off the leaf id instead of stripping the parent/season id first, so it mis-parses BOTH real anime id shapes in this user's library:
  - Shape A (`…-s03` + glued episode, e.g. `ani-ja-2015-kurokosbasketball-s0324`): the trailing-digits regex swallows the season digits too, yielding episode **324** instead of 24. Verified: `_episode_se_of('ani-ja-2015-kurokosbasketball-s0324', entry)` → `(3, 324)` (WRONG), while `mvcommon.episode_num_from_id('ani-ja-2015-kurokosbasketball-s0324', 'ani-ja-2015-kurokosbasketball-s03')` → `24.0` (correct). The still is requested at `/tv/{id}/season/3/episode/324/images` → 404 → no `<basename>-thumb.jpg` written, and `_apply_episode_overviews` finds no episode 324 in the season payload → no per-episode `overview`/`episode_title` backfilled.
  - Shape B (`ani-ja-2013-attackontitan01`, parent has no `-sNN`): `_episode_se_of` returns `None` (verified) and `_season_number_of` is also `None`, so per-episode stills AND overviews are skipped entirely, and the per-season poster loop `continue`s without ever calling `/season/`.
  - Show-level enrichment (poster, fanart, `tmdb_id`, title/year/overview, folder token) works fine — only the per-episode layer is silently empty. Nothing is corrupted; data is silently absent.
  - **This is exactly the drift IMP-C18 tried to prevent.** C18 fixed the same class of mis-parse (`ani-…-s0202` → 202 instead of 2) in the fetch/restore range filters, and its stated Proposed change was to factor the episode-number extraction into ONE shared helper (`mvcommon.episode_num_from_id(child_id, base_id)`) used by ALL range-filter sites so the three copies couldn't drift again. `_episode_se_of` is a FOURTH copy, added later by the enrichment work (IMP-E3/E16), that was never brought into line with that helper.
- Proposed change (direction, not yet implemented): have `_episode_se_of` derive the episode by stripping the parent/base id first — delegating to `mvcommon.episode_num_from_id(leaf_id, parent_id)` — instead of reading bare trailing digits, so the 4th copy stops existing. Season number still comes from `_season_number_of(parent_id)`. **Open question, not settled**: Shape B's parent carries no `-sNN` at all — what season should be assumed (likely season 1)? Needs a decision before implementing, not a silent default.
- Rationale: silent wrong-result with no error, on the exact bug class C18 already named and tried to prevent by centralizing the parser — the fix path already exists, it's just not wired to this 4th call site.
- Goal: per-episode enrichment (stills + overview/episode_title backfill) lands correctly for both anime id shapes; a regression test pins both `…-s0324`-style and bare-trailing-digit (`…01`) ids against `mvcommon.episode_num_from_id`.
- Effort estimate: small-medium (delegate `_episode_se_of` to the shared helper + resolve the Shape-B default-season question + update the tests in `tests/test_prep_push_rep_season_enrich.py` that currently pin the buggy behavior as a documented "pre-existing limitation").
- Risk: low-medium — narrows/corrects parsing only; the Shape-B season-default decision needs sign-off since it changes behavior for ids that today return `None` (no-op) to instead attempting enrichment under an assumed season.
- If skipped: every anime entry (145 in this user's library) silently never gets per-episode stills or overview/title backfill from the enrichment commands, while show-level enrichment appears to succeed — easy to miss.
- Status: pending

---

## IMP-C23: `_has_tmdb_token` missing `re.IGNORECASE` — an uppercase `{TMDB-…}` folder token reads as "no token" and gets a SECOND token appended

- Category: bug
- Priority: high (was Band 0)
- Files: `main.py` — `_has_tmdb_token` (grep `"^def _has_tmdb_token"`). Sibling `_PROVIDER_TOKEN_RE` (grep `_PROVIDER_TOKEN_RE = `), used by the artwork-inheritance resolver to find the show folder, has ALWAYS been `re.IGNORECASE` — the two predicates are over the same token and had silently diverged.
- Current behavior (before fix): `_has_tmdb_token` was `return re.search(r"\{tmdb-[^}]+\}", name or "") is not None` — no flags. Measured before the fix:
  ```
  False  'Run (2002) {TMDB-69590}'      <- the user's REAL folder
  True   'Drishyam 3 (2026) {tmdb-847742}'
  False  'X {TmDb-1}'
  ```
  Consequence: the idempotency guard failed on any uppercase/mixed-case token, so `cmd_enrich_metadata` computed `will_stamp=True` on an already-stamped folder and the next enrich/rename pass appended a second token — `Run (2002) … {TMDB-69590} {tmdb-69590}`. The user has exactly one real folder in this shape.
- Root cause: DRIFT between two copies of the same predicate over the same token. **This is the same drift-between-duplicated-parsers class as IMP-C18 and IMP-C22 — cross-reference both explicitly**; this is now the third instance in this codebase, which is the interesting pattern worth recording.
- Fix applied: added `re.IGNORECASE` to `_has_tmdb_token`'s `re.search`, plus a docstring stating it is kept deliberately in lockstep with `_PROVIDER_TOKEN_RE`.
- Verified after the fix: all three shapes above return `True`; `'No token here'`, `''`, `None` and `'{tvdb-123}'` still return `False` (not over-eager).
- Tests added (in `tests/test_enrich_metadata.py`, 10 new cases, all green):
  - `test_has_tmdb_token_is_case_insensitive` (4 parametrized shapes incl. the real `{TMDB-69590}`)
  - `test_has_tmdb_token_still_false_without_a_tmdb_token` (5 negative cases)
  - `test_has_tmdb_token_agrees_with_provider_token_re` — a drift pin: asserts the two predicates agree over 7 names, so a future edit to either one fails the suite. This is the guard that prevents a 4th recurrence.
- Rationale: silent idempotency-guard failure that corrupts folder names by double-stamping the TMDB token, with no error surfaced.
- Goal: `_has_tmdb_token` agrees with `_PROVIDER_TOKEN_RE` on case, permanently pinned by a drift test.
- Effort estimate: small
- Risk: low — single-flag fix plus tests; no behavior change for lowercase tokens.
- If skipped: any uppercase/mixed-case `{TMDB-…}` folder keeps accumulating a second token on every enrich/rename pass.
- Surfaced by: IMP-D22 (the enrich-autopilot work) — it was the `👉 SUGGESTED NEXT TASK` pointer in PRIORITY.md.
- Gates: smoke 80/80; `test_enrich_metadata` + `test_rename_folder` + `test_set_tmdb` 74/74.
- Status: done (fix/imp_c23_has_tmdb_token_ignorecase)

---

## IMP-C24: Concurrent library writes silently lose updates — no lock, and `save_library` rewrites all four files from a merged dict (blast radius = the whole library set)

- Category: bug
- Priority: high (Band 0 — silent data-integrity corruption; already caused a real incident that uploaded a dummy file to Google Photos)
- Files: `mvcommon.py` — `load_library()` (~551), `save_library()` (~569), `fetch_session_lock` (~459-540, the proven O_CREAT|O_EXCL lock precedent to mirror). `main.py` — every `load_library()`/`save_library()` pair (~30 call sites; the highest-risk are `cmd_prep` L1038/1180, `cmd_push` L4735/4952+5115, `cmd_replace` L5446/5562, `cmd_restore` L6449, plus `cmd_prep_season`, `cmd_set_search`, `cmd_set_tmdb`, `cmd_set_uploaded`, `cmd_rename_folder`, `cmd_enrich_metadata`, `cmd_fetch_trivia`, `cmd_repair_dummies`, `cmd_verify_library --fix-dummies`, `cmd_sort`, `cmd_add_extras`, and `RollbackJournal.rollback()`/`recover_journal()`'s own `save_library()` calls). `webui/server.py` — `ACTION_TABLE` routes `/api/action/{prep,push,replace,prep_push_rep,fetch_restore}` to the same `main.cmd_*` functions (L210/216-221/223/231/236) through a single in-process FIFO worker (`_worker_loop`, L523) — a separate OS process that races a concurrent CLI invocation exactly like two CLI shells race each other. Full options/steps/recommendation: `docs/feature-library-concurrency/PLAN.md`.
- Current behavior (verified, not speculation — this already caused a real incident, 2026-08/09): `mvcommon.load_library()` reads all four library JSONs and merges them into ONE in-memory dict; `mvcommon.save_library(data)` splits that dict back by id prefix and rewrites ALL FOUR files on every call, whether or not a given library actually changed. The per-file write is atomic (`tempfile.mkstemp` → `json.dump` → `os.replace`), so a *torn* file is impossible — but there is **no lock anywhere** (grepped `flock`/`msvcrt.locking`/`LockFile`/any library-level lock: none exist; the only existing lock in the codebase, `mvcommon.fetch_session_lock`, protects the Selenium/CDP session, not the library). Every mutating command follows the same shape: `library = load_library()` once at the top, hold that in-memory snapshot across the command's own work (for `cmd_prep` a whole-file SHA256 that can take minutes on a 75+ GB file; for `cmd_push` the entire multi-GB ADB upload loop), then `save_library(library)` once near the end using that now-stale snapshot. Two such commands running concurrently — e.g. `push_group` in one shell and `replace` in another, a real and previously-legitimate workflow for reclaiming disk while a slow push runs — produce a classic lost update: whichever command's slow work finishes later saves LAST, from a snapshot taken BEFORE the other command's change, silently erasing it. Verified in the code: `cmd_push_group`'s per-item skip test is exactly `if library[mid].get("uploaded") == True: continue` (main.py:5389) — one flag, no disk/status cross-check — and `cmd_push` (unlike `cmd_prep`, which has a `DUMMY_MAX_BYTES` secondary safety net, and unlike `push_one_extra`, which gained one via IMP-D19-B1) has **no** guard refusing to upload a dummy-sized local file, so a lost `uploaded` flag on a real entry leads straight to re-uploading whatever is on disk — which, after a lost update, can be the dummy. **Blast radius is the entire library set, not one file**: because `save_library` rewrites all four JSONs from the merged dict every call, a concurrent `replace` on a *series* entry can clobber a *movie* or *anime* entry another process changed in the interim — in the real incident the damage happened to stay inside one season (X-Files pushes ran in the same window and escaped), which was luck, not a property of the design. Nothing warns the user; the hazard is undocumented anywhere in `improvements/` or `docs/` prior to this task.
- Proposed change (direction; options evaluated with a recommendation in `docs/feature-library-concurrency/PLAN.md` — **change-gated, needs an explicit user ruling before implementation**, see that plan's Open Decisions): evaluate (a) a cross-process lock, (b) narrowing the load→mutate→save window so it never spans I/O, (c) merge-on-write with dirty-field tracking, (d) detect-and-refuse via a PID/heartbeat file, (e) document-only. The plan's recommendation is a fine-grained, short-held cross-process lock (mirroring the proven `fetch_session_lock` O_CREAT|O_EXCL primitive, but never silently proceeding on a contended timeout — the one respect in which it must deliberately NOT copy that precedent) combined with narrowing every mutator's write window to "load fresh, apply this command's own known changes, save" — never held across ADB/hashing/mkvmerge I/O.
- Rationale: this is not a hypothetical — it already corrupted 13 real library entries and caused a 9,672-byte dummy file to be uploaded to Google Photos in place of a real episode. The stored `hash` field was still correct on the affected entries, so the incident was caught (a restore would have failed loudly, not silently), but that safety margin is luck, not a guarantee for every field that could be lost this way.
- Goal: two concurrent mutating commands (CLI+CLI, or CLI+`web`) can never silently lose either one's update; the fix does not serialize the user's legitimate parallel workflow (a slow push in one shell, a fast replace in another) into a multi-minute wait.
- Effort estimate: medium-large — the primitive is small, but correctly migrating every mutator (~15 commands, ~30 call sites) and proving the fix with a regression test is real, careful work.
- Risk: the change itself is low-risk if scoped correctly (additive primitive + mechanical per-site migration), but it sits **adjacent to the change-gated auto-rollback mechanism** (`RollbackJournal.rollback()`/`recover_journal()` call `save_library()` internally, and the fix changes what happens at the exact moment `cmd_prep`/`cmd_push`/`cmd_replace`/`cmd_restore` persist on their happy path) — per `CLAUDE.md` this MUST be surfaced to the user as an explicit decision before implementation, not silently modified.
- If skipped: the exact incident (or worse — a movie/anime entry clobbered by an unrelated series-entry save) can recur at any time two mutating commands overlap, including today's normal usage pattern of running `main.py web` alongside a CLI command.
- Cross-references: IMP-D23 is what the user was manually working around (avoiding an expensive prep re-hash) when they triggered this incident via a risky parallel `push_group`+`replace` workaround — fixing D23 removes the *motive* for that specific workaround but does not fix the underlying race, which can still occur for other legitimate reasons (e.g. `web` alongside a CLI command, running right now in production). IMP-B1 ("cache library handle across cmd_* calls", `improvements_tierB.md`, pending) proposes the OPPOSITE direction — holding one library handle across an entire season batch — and would make this race's blast radius larger, not smaller, if implemented without C24's lock in place first; B1's own entry already flags it as risk "high" and change-gate-adjacent for this reason.
- Status: pending

---

## IMP-C26: Fetch routes by id prefix — objects that live in another Google account are unfetchable

- Category: bug
- Priority: high (Band 0 — breaks restore for real archived items)
- Files: `mainfetch.py` (`profile_for_id`, `cmd_fetch_route`), `mvconfig.example.json`
- Current behavior: `mainfetch.profile_for_id` picks the Chrome profile from the id prefix; the 2026-09-25 mapping found 31 X-Files episodes (`tv-en-1994/1995/1996-xfiles`, seasons 2–4) in the MOVIES account (Kuroko's Basketball copies also sit in the TV account besides the anime account).
- Proposed change: immediate fix (IMP-C25 Step H, its own small PR from main): a local `mvconfig.json` `fetch_account_overrides` list (exact id or prefix → account) consulted first by `profile_for_id`, and `cmd_fetch_route` runs one Chrome session per account for a mixed batch; then IMP-C25 adds the learned per-object `home_account` beneath the override (P-4).
- Effort estimate: small (hotfix) · Risk: low — no overrides configured ⇒ byte-identical behavior.
- If skipped: those 31 episodes cannot be restored.
- Fix applied (`fix/imp_c26_fetch_account_routing`): `mainfetch._account_overrides()` reads `fetch_account_overrides` through `mvcommon._load_config()` (validated once per config object — an unknown account or a blank key prints one warning and is ignored); `profile_for_id` takes the longest matching key (exact id beats prefix), else today's `ID_PREFIX_PROFILE` loop; `cmd_fetch_route` groups a batch by account (extras by their title id) — the selector's account first, then `CHROME_PROFILES` order — and runs one `init_driver` per group under ONE `fetch_session_lock`, closing the previous account's Chrome windows and requiring debug port 9222 to be free before each switch (a busy port stops the batch loudly instead of attaching to the wrong account). No valid overrides ⇒ a single group, byte-identical transcript (frozen oracle captured from the pre-fix code). Tests: `tests/test_fetch_account_routing.py` (31) + smoke `test_fetch_route_mixed_account_batch`; a new autouse `_hermetic_mvconfig` fixture keeps every test off the machine's real `mvconfig.json`. User steps: README fetch note, `docs/OPERATIONS_QA.md` §6b.
- Status: done (hotfix PR; learned routing follows in IMP-C25)

---

## IMP-C28: `cmd_push` treats any path containing `_parts` as a chunk — a whole file from a `Spare_parts (2015)` folder is uploaded untagged, then its local master is deleted

- Category: bug
- Priority: high (Band 0 — a SUCCESSFUL push deleted the master, the source of truth the auto-rollback contract rests on)
- Files: `main.py` — `cmd_push`'s upload loop (pre-fix `main.py:6442` rename test, `main.py:6509` local delete) and its IMP-C25 capture call (now `main.py:6629`); `gpcapture.py` — `snapshot_push_objects` (pre-fix `gpcapture.py:192`, a copy of the same test); `mvcommon.py` — new `in_parts_dir`. Found by the IMP-C25 Step 5 executor, confirmed with a scratch run on temp dirs, then reproduced hermetically. (Numbering: IMP-C25 and IMP-C27 are registered on `feature/imp_c25_fetch_exact_gp_item`, not yet on `main`.)
- Current behavior (before fix): the upload loop decided "chunk vs whole file" with `if SPLIT_DIR_NAME not in f:`, a substring test on the WHOLE path. It deleted each uploaded file with `if SPLIT_DIR_NAME in f:`. So a master whose path merely contains `_parts` was treated as a chunk. That covers the title folder (`Spare_parts (2015)`, `Body_parts`) and the file name, case-sensitively. It happened on every route that uploads the master whole:
  - a plain push;
  - a split skipped because the file is under the target;
  - a `tempdir` push without a split;
  - a `chunk_range` push.

  The hermetic reproduction: the device received `Spare_parts (2015).mkv` instead of `Spare_parts (2015) [<short_id>].mkv`. `cmd_push`'s own post-commit check then printed `⚠️  INTEGRITY: … status=onboarded but on-disk=MISSING`, just before `✅ SUCCESS`. `gpcapture.snapshot_push_objects` copied the test, so the identity capture recorded that master as an untagged `holder` with no `sha256`.

  Two paths were NOT affected:
  - split pushes, because the master is never in their upload list;
  - `push_one_extra`, because it names and deletes by `is_split` (see the audit below).
- Impact:
  1. **A successful push deleted the master.** The cloud bytes are intact, because the delete ran only after the upload and its rename had succeeded. But the local copy the O-1/O-2 contract relies on was gone, and with `PUSH_VERIFY_REMOTE` off by default, nothing had checked the uploaded bytes.
     - A following `replace` (the `prep_push_rep` autopilots' next leg) then found no master. It skips its rename when the original is absent (`main.py:7154`), writes the dummy into its place and marks the entry `archived` without complaint. The entry therefore looks healthy.
  2. **The cloud item lacks its ` [<short_id>]` tag.** `search_term`, the `.mvmeta.json` sidecar (`main.py:5697`) and the identity capture all record the tagged name. So name-based identification cannot tie the item to its entry; IMP-C25's fetch-exact-item work (fetch-by-id) depends on it.
     - Today's fetch still queries a whole file by its plain local filename (`mainfetch.py:331`) and routes downloads by hash (`mainfetch.py:400-404`). So `fetch_restore` can still bring the master back.
- Proposed change — implemented (`fix/imp_c28_push_parts_substring`): a file counts as a chunk only if it lives DIRECTLY in THIS push's chunk dir.
  - The new `mvcommon.in_parts_dir(path, parts_dir)` compares the normalised parent dir (`abspath` + `normcase`) with the `parts_dir` that `cmd_push` computed: `_parts_base(...)` + `SPLIT_DIR_NAME`, tempdir redirect included.
  - `cmd_push` computes `is_chunk` once per file (`main.py:6643`) and uses it for both the rename and the delete.
  - `gpcapture.snapshot_push_objects` now takes `parts_dir` (it took `split_dir_name`) and uses the same predicate, so the recorded upload name cannot drift from the device's.

  This is one shared rule, following the mvcommon precedent that ended the IMP-C18/C22/C23 duplicated-parser drift.
- Audit of every other `SPLIT_DIR_NAME` / `"_parts"` test in `main.py`, `mainfetch.py`, `mvcommon.py` and `gpcapture.py`:
  - **Fixed:** the two `cmd_push` tests and the `gpcapture` copy.
  - **Safe, left unchanged:**
    - `push_one_extra` — delete at `main.py:6137` (`is_split and SPLIT_DIR_NAME in f`), naming at `main.py:6096` (by `is_split`). `is_split` is True only on its resume/split branches, whose files all live in `<extra folder>/_parts/<short_id>`. So the substring conjunct is redundant and never decides, and a whole extra short-circuits on `is_split`.
    - The `os.walk` prunes compare a whole directory NAME, not a substring: `cmd_recover --scan` (`main.py:1321`), `scan_extras_folders` (`_EXTRAS_EXCLUDE_DIRS`, `main.py:5278`), `cmd_scan_unprepped` (`main.py:8970`) and the reclaim scan (`_RECLAIM_EXCLUDE_DIRS`, `main.py:10299`).
    - The rest are path construction (`_parts_base`, `parts_dir = os.path.join(...)`), comments, or unrelated names (`slug_parts`, `folder_parts`, `_fp_parts`).
    - `mainfetch.py` only mentions `_parts_base` in comments, and `mvcommon.py` only defines the constant.
- Rollback change-gate: not crossed. The fix restores the documented behaviour: push has no PONR, and the master survives it (`docs/feature-auto-rollback/ROLLBACK_MECHANISM.md` §5). The journal format, the PONR placement, what is journalled, the O-1 failure branches and `recover_journal` are all untouched.
- Tests: `tests/test_push_chunk_classification.py` has 13 hermetic tests (`sandbox` + `mock_device`):
  - **The reproduction:** 6 whole-file cases, 2 lookalike folders × 3 routes. `Body_parts` ends in `_parts`, which defeats a separator-anchored substring "fix".
  - **Resumed and fresh splits** (chunk dir in the title, and tempdir-redirected): only this push's chunks are deleted, and the master survives.
  - **Extras** (whole and split) pushed from a lookalike folder.
  - **The shared rule:** its unit semantics, and the capture's use of it.

  `tests/test_gp_capture.py` now passes the chunk dir. Disarm probes: 13/13 caught, each failing exactly the expected tests. Full suite 1016 passed; smoke 82 passed.
- Effort estimate: small · Risk: low. Classification changes only for a file whose path contains `_parts` outside the chunk dir, and that file is now treated as the whole file it is. Every chunk producer writes straight into `parts_dir`. No `ENTRY_TYPE_KEYS` involvement.
- If skipped: any whole-file push of a title stored under a `…_parts…` folder silently deletes the local master and leaves an untagged copy in the cloud.
- Merge note for IMP-C25: that branch keeps the substring test in its `snapshot_push_objects`, and its `cmd_push` still passes `SPLIT_DIR_NAME`. When it next merges `main`, keep this rule in both places. Its `push_one_extra` already passes the item's own chunk dir, which `in_parts_dir` accepts as is.
- Status: done (`fix/imp_c28_push_parts_substring`)

---

## IMP-C30: walkers and the `chunks N-M` filter recognised a chunk by a `.chunk.` substring — a real video named like `the.chunk.2019.1080p.mkv` was hidden from `scan_unprepped` and the reclaim scan, and its chunks were numbered 2019

- Category: bug
- Priority: medium (Band 0 — a silent wrong result in two read-only reports, plus a misleading refusal; no data loss)
- Files: `mvcommon.py` — new `chunk_index` (`mvcommon.py:784`). `main.py` — `cmd_scan_unprepped` (pre-fix `main.py:8778`), `collect_reclaimable` PASS 1 (pre-fix `main.py:10103`), and `cmd_push`'s `chunks N-M` filter (pre-fix `main.py:6393`). Found by the IMP-C28 executor's audit. Investigated and fixed on `fix/imp_c28_followups` after the user's 2026-10-01 ruling ("Investigate + fix now").
- Current behavior (before fix):
  - `cmd_scan_unprepped` and `collect_reclaimable` skipped every video whose file name contained `.chunk.`. `collect_reclaimable` is the read-only scan behind `web`'s Disk Reclaim view (`/api/reclaim`), and it also supplies the unprepped rows of the web folder tree (`build_tree`, `/api/tree`). The test was case-sensitive, so `The.Chunk.Of.Gold.2004.mkv` was never affected, but a lower-case release name such as `the.chunk.2019.1080p.web.h264.mkv` was. That real, unprepped video was missing from both reports, and `scan_unprepped` could end with `✅ All libraries are completely in sync.`
  - `cmd_push`'s `chunks N-M` filter took the FIRST `.chunk.<digits>.` in a chunk's name. Every chunk of such a title (`the.chunk.2019.1080p [<short_id>].chunk.001.mkv`) was numbered 2019, so a range push refused with `No chunks found in range`. Only a range covering 2019 would have selected them, all at once.
  - The skip only ever needed to catch a stray chunk. Real chunks live in `_parts/`, fetched chunks in `restore/`, and extras chunks in `<extra folder>/_parts/<short_id>`, and both walkers prune all of those.
- Impact: no data loss. The walkers are read-only, and a range push never marks an entry onboarded. A hidden file is simply absent from both reports, so it can go unarchived without anyone noticing. Real-library check (read-only, 2026-10-01): 0 on-disk videos falsely skipped, 0 library or extras file names containing `.chunk.`, and 0 titles whose chunks the old filter would misnumber.
- Fix (implemented): one shared rule, `mvcommon.chunk_index(name)`. It returns the number in a name that ENDS in `.chunk.<digits>.mkv` (case-sensitive), else None. That is exactly what `split_video_file`'s own listing accepts (`main.py:428`), so the walkers' notion of a chunk is the producer's. Both walkers skip a file only when `chunk_index` returns a number (`main.py:8980`, `main.py:10306`), and the range filter numbers a chunk by it (`main.py:6576`). This follows the IMP-C18/C22/C23/C28 precedent: one shared mvcommon rule instead of drifting copies.
- Audit of every `.chunk.` test in `main.py`, `mainfetch.py`, `mvcommon.py`, `gpcapture.py`, `tools/` and `webui/` (`gpweb.py` does not exist on `main`):
  - **Fixed:** the two walkers (`".chunk." in f`) and the `chunks N-M` filter (`re.search(r'\.chunk\.(\d+)\.')`, an unanchored first match).
  - **Safe, left unchanged:**
    - `split_video_file`'s listing (`main.py:428`): end-anchored `\.chunk\.\d+\.mkv$`, over its own output dir only. It is the reference the new rule mirrors.
    - `gpcapture._CHUNK_RE` (`gpcapture.py:32`): end-anchored, and consulted only for a file already inside the push's chunk dir (`in_parts_dir`), to label it chunk or holder.
    - `push_one_extra`'s resume (`main.py:5990`): works inside the item's own chunk dir and has no `.chunk.` test. (Since IMP-C32 it uploads only the files the item's `split_info` records.)
    - `mainfetch.py` reads chunk names only from `split_info`, never from a file name. `tools/` has no chunk test, and `webui/server.py` only parses progress lines.
  - **Not chunk tests:** `cmd_prep_season` (`main.py:5522`) and the season autopilot's pre-flight (`main.py:9091`) list a season folder non-recursively, so `_parts/`, a sub-folder, is never listed.
- Rollback change-gate: not crossed. The walkers are read-only. The range filter runs before any upload and only selects which files a range push sends. The journal, PONR placement, what is journalled, the O-1 resume message and `recover_journal` are untouched. No `ENTRY_TYPE_KEYS` involvement.
- Tests: `tests/test_chunk_filename_rule.py` has 17 hermetic tests (`sandbox`, `make_video`, `mock_device`):
  - **The reproduction:** 2 lookalike names × both walkers, and a resumed range push that must number the chunks 1 and 2, not 2019.
  - **Regression pins:** a tagged and an untagged (legacy) chunk lying loose in a title folder, plus one inside `_parts/`, are still skipped by both walkers.
  - **The shared rule:** its unit semantics over 10 names, and a drift pin. The pin drives the real `split_video_file` (mkvmerge stubbed) and proves the rule accepts exactly the files the producer returns.

  Disarm probes: 9/9 caught, each failing exactly the expected tests. Full suite 1033 passed; smoke 82 passed.
- Effort estimate: small · Risk: low. Only a name that contains `.chunk.` without ending like a chunk changes classification. Every chunk the split ever wrote still matches, including untagged legacy names.
- If skipped: a real video whose lower-case name contains `.chunk.` stays invisible to both disk reports, and range pushes of such a title stay unusable.
- Status: done (`fix/imp_c28_followups`)

---

## IMP-C31: `cmd_push` "resumed" a non-empty `_parts/` that held no chunk — it uploaded nothing, marked the entry onboarded, and the next `replace` dummied the master

- Category: bug
- Priority: high (Band 0 — a "successful" push with nothing uploaded; the autopilot's `replace` leg then destroys the only copy)
- Files: `main.py` — `cmd_push`'s resume branch (pre-fix `main.py:6178-6182`). Since IMP-C32 the refusal is the empty case of the strict-resume branch (`main.py:6334-6359`). Found by the IMP-C28 executor while reading that branch. Reproduced and fixed on `fix/imp_c28_followups` after the user's 2026-10-01 ruling ("Investigate + fix now").
- Current behavior (before fix):
  - `cmd_push` took the resume branch whenever `<folder>/_parts/` existed and was non-empty, then uploaded only the `.mkv` files in it. If the dir held no `.mkv` at all, the resume list was empty and the upload loop (pre-fix `main.py:6436`) ran zero times. `all_success` stayed True, so the push wrote the remote `.mvmeta.json` sidecar, set `uploaded=True` / `status="onboarded"` and printed `✅ SUCCESS.` (pre-fix `main.py:6547-6565`). With `chunks N-M` it printed `✅ Partial Upload Complete` instead (pre-fix `main.py:6580`).
  - What can leave such a dir:
    - the transient FLAC extract (`<base> [<short_id>].flac`, or its `.verify.flac`) that `_carry_out_flac_track` writes into `_parts/`, after a kill in a run whose `_parts/` pre-existed, so the journal never recorded it. A crash the journal DID record is recovered by IMP-R7 when the next push opens its journal, so it never reaches the resume branch;
    - a stray or OS file (`notes.txt`, `Thumbs.db`, `desktop.ini`) after the chunks were removed by hand;
    - a sub-folder.
  - The hermetic end-to-end reproduction ran `prep_push_rep` with `_parts/` holding only `notes.txt`. It printed `🔄 Resuming 0 chunks found in temp folder.`, then `✅ SUCCESS.`, then `✅ Replaced/Archived`. The device held only the `.mvmeta.json` sidecar, the 264,000-byte master had become the 5-byte test dummy, and the entry read `archived` / `uploaded=True`. `cmd_replace` gates only on `uploaded` (pre-fix `main.py:6908`), so nothing stopped it.
- Impact: total loss of the title. The master is dummied while none of it is in the cloud, and the library says it is archived. Real-library check (read-only, 2026-10-01): no leaf folder has a `_parts/` today and 0 entries are still `local_ready`, so nothing is affected now.
- Fix (implemented): in the resume branch, when `_parts/` is non-empty but holds no `.mkv`, `cmd_push` refuses and explains (now `main.py:6334-6359`; IMP-C32 widened the condition and the wording). It names the dir, lists what it found, and says nothing was uploaded and the entry is unchanged. It then hands over two commands: delete the folder and re-split the intact master (`push <id> <the recorded split>`), or, only once every chunk is confirmed on the device, `set_uploaded <id>`.
  - A `_parts/` that holds a chunk, or the FLAC holder, resumes exactly as before. An empty `_parts/` is still not a resume.
  - This mirrors `push_one_extra`, whose resume already requires at least one `.mkv` (`main.py:5990`). Unlike extras, the main push refuses instead of re-splitting into the pre-existing dir. Chunks created there would never be journalled (D-6), so a later pre-upload failure would pop `split_info` and leave them behind, to be "resumed" without it: the D1 hazard IMP-D21 closed for extras.
- Rollback change-gate: assessed, not crossed.
  - The refusal sits after the journal opens (`main.py:6312`), so IMP-R7 still recovers a crashed run's leftover first. It comes before any journalled action. It records nothing, marks no PONR, calls neither `rollback` nor `commit`, never touches the pre-existing `_parts/` (D-6) and does not save the library.
  - That is the exit shape of the two existing pre-flight refusals, free space (`main.py:6398`) and unsplittable track (`main.py:6413`), down to the zero-record journal they leave (pinned by `tests/test_unsplittable_preflight.py`). Like them it prints its own remedy instead of the O-1 `Resume with: push <id>` line, which would loop straight back into the refusal.
  - The O-1 failure branches (pre-upload rollback, post-upload resume message), `recover_journal`, PONR placement, the season resume-range messaging and `RollbackHardFail` are untouched.
  - The only behaviour change: a push that wrongly returned True and marked the entry onboarded with nothing uploaded now returns False and changes nothing. That restores the documented rule ("Only mark as 'onboarded' if we uploaded ALL chunks", `main.py:6747`; O-1: the entry stays `local_ready` until its content is up).
- Tests: `tests/test_push_resume_needs_chunks.py` has 17 hermetic tests (`sandbox`, `mock_device`, `make_video`; `stub_tech_specs` + `fake_dummy` for the autopilot):
  - **The reproduction:** 4 leftovers (FLAC extract, stray text file, `.partial` remnant, sub-folder) × 3 calls (plain, with split args, `chunks 1-2`). The push returns False, the library is unchanged, nothing reaches the device, the chunk dir and master are untouched, the journal records nothing, and the refusal names the dir and what it holds. Plus the whole loss through `prep_push_rep`: the master must survive.
  - **Regression pins:** a stray file beside real chunks does not block their resume; a dir holding only the FLAC holder still resumes; a crashed carry-out's journalled `_parts/` is recovered (IMP-R7) before the resume check and the push re-splits; an empty `_parts/` is not a resume.

  Disarm probes: 10/10 caught, each failing exactly the expected tests. Full suite 1050 passed; smoke 82 passed.
- Related finding, found here and since fixed by **IMP-C32** (the user ruled "Strict resume" on 2026-10-02):
  - `_parts/` belongs to a FOLDER, not an entry. `cmd_prep` sets an episode's `folder_path` to its season folder (`main.py:1416`), so every episode of a season shares `Season NN/_parts/`, and the resume branch uploads every `.mkv` there without checking whose chunks they are.
  - A hermetic scratch run left episode 1's chunk 002 in the shared dir (an interrupted push). `push <episode 2>` then uploaded episode 1's chunk, marked episode 2 `onboarded` / `uploaded=True`, and a following `replace` dummied episode 2's master.
  - `push_group` makes this likely, because it ignores a failed `cmd_push` and moves on to the next episode.
  - Real library: 73 folders are shared by more than one leaf (1131 leaves); 0 are exposed today.
  - Same root cause: if `_parts/` already existed before a push whose split or first upload then failed, that push's leftovers stay there, because D-6 never records a pre-existing dir. They can be complete chunks whose `split_info` the rollback popped, a partial `.chunk.NNN.mkv` from a failed or killed mkvmerge, or just the FLAC `.holder.mkv` from a killed carry-out. The next push "resumes" them and marks the entry onboarded with no `split_info`. A hermetic scratch run on the C31-fixed code confirmed it: a holder-only and a partial-chunk remnant each made the push return True and upload only that remnant, and a following `replace` dummied the master.
  - IMP-C32 adopted the one rule that closes all of these: a resume uploads only the files recorded in this entry's `split_info` (its chunks and carried-out holder) and refuses anything else. IMP-C31's refusal is the empty case of that rule.
- Effort estimate: small · Risk: low. Only a non-empty `_parts/` with no `.mkv` changes outcome, from a false success to a refusal. Every genuine resume is unchanged. No `ENTRY_TYPE_KEYS` involvement.
- If skipped: any leftover-only `_parts/` turns the next push of that entry (or of any entry sharing the folder) into a false success, and an autopilot run then destroys the master.
- Status: done (`fix/imp_c28_followups`)

---

## IMP-C32: a resume uploaded every `.mkv` in `_parts/` — another episode's chunks, or chunks no split records — and marked the entry onboarded; a strict resume uploads only what the entry's own `split_info` records

- Category: bug
- Priority: high (Band 0 — a "successful" push uploads the wrong content, or an unrecorded split, and `replace` then dummies the master)
- Files: `main.py` — the shared helpers `_split_recorded_hashes` / `_resume_plan` / `_resume_damaged` / `_resume_stranger_notes` (`main.py:5773-5884`), `cmd_push`'s resume branch (`main.py:6318-6363`) and its pre-upload check (`main.py:6592-6608`), and `push_one_extra`'s resume branch (`main.py:5989-6018`). Found while fixing IMP-C31. The user ruled "Strict resume" on a decision card on 2026-10-02. That is the rollback change-gate ruling for this change, recorded in the IMP-C25 branch's `docs/feature-fetch-datetime/DECISIONS.md` §1d. Branch `fix/imp_c32_strict_resume`.
- Current behavior (before fix): a resume uploaded every `.mkv` in the chunk dir, then marked the entry onboarded. Two shapes made that wrong.
  - **(a) Another entry's chunks.** `cmd_push`'s chunk dir belongs to a folder, not an entry. `cmd_prep` sets an episode's `folder_path` to its season folder (`main.py:1416`), so every episode of a season shares `Season NN/_parts/`. With episode 1's chunk waiting there after an interrupted push, `push <episode 2>` uploaded episode 1's chunk and marked episode 2 `onboarded`. `push_group` made this likely: it ignores a failed `cmd_push` and moves on to the next episode.
  - **(b) Chunks that no split records.** They come from:
    - a failed or killed split in a `_parts/` that already existed. D-6 never records a pre-existing dir, so rollback leaves the partial chunk, or the lone FLAC holder, in it;
    - **an interrupted push whose entry was then re-prepped.** `cmd_prep` rebuilds an existing `local_ready` entry wholesale (`main.py:1495-1512`), which drops its `split_info`. Both autopilots re-prep on a re-run, and the season autopilot's own printed `Resume the rest of the season: prep_push_rep_season … episodes N-M` command does exactly that. Reproduced hermetically on `main` (`e35b624`): the leftover chunk was "resumed", the episode ended `archived` with no `split_info`, and its master was dummied.
  - In both shapes the next `replace` swapped the master for a dummy, because `cmd_replace` gates only on `uploaded` (`main.py:7108`).
  - In shape (b) the bytes can all be in the cloud and the archive is still broken: with no `split_info`, `fetch_restore` queues one whole file and accepts a download only by the whole-file hash (`mainfetch.py:286-335`, `mainfetch.py:404`), so it cannot restore an entry whose cloud copy is chunks.
- Fix (implemented): one rule, shared by both resume sites.
  - **Selection.** `_resume_plan` splits the chunk dir into the files this entry's `split_info` records (its `chunks` and its `carried_out_tracks` holders) and strangers (everything else). Only the recorded files are uploaded. A recorded file that is missing is taken as uploaded by an earlier run, as before; a resume cannot tell that from a chunk deleted by hand.
  - **Content.** Before the first upload, `_resume_damaged` checks each file about to go up against the sha256 the split recorded. A partial or altered chunk under a recorded name is refused. The check runs after the `chunks N-M` filter, so a range push hashes only its own range. A chunk with no recorded hash passes on its name ("where recorded"). `split_info` records no sizes, so the ruling's "sizes/hashes" is met by the hash.
  - **Strangers** are never uploaded and never deleted. They are always listed with their likely owner, read from the ` [<short_id>]` tag: a recorded chunk of another entry, a file tagged for an entry that does not record it, a file tagged for this entry, or no tag.
  - **Refusal.** With nothing recorded left to upload, the push refuses. IMP-C31's refusal is this empty case. The message names each stranger and its owner, then gives the next step: `push <owner>` first for another entry's interrupted push, delete the leftovers and re-split for the rest (it suggests the split this call passed, else the recorded one), and `set_uploaded <id>` only when the entry has a recorded split whose files are all gone.
  - **With strangers beside recorded chunks** the resume proceeds with the recorded chunks only and lists the strangers as left in place. This is the reading of the ruling's "C31's refusal is the empty case": the push refuses when nothing recorded can be uploaded, and strangers are refused individually. Refusing the whole push for any stranger is a one-line change if that is preferred.
  - **Extras.** `push_one_extra`'s resume uses the same helpers on its per-item dir (`<extra folder>/_parts/<short_id>`). It had shape (b) too: a failed split's chunk in a pre-existing item dir, or stale chunks after a re-scan dropped the item's `split_info`, were uploaded and the item marked onboarded.
- `split_info` timing, verified:
  - A new split records `split_info` and saves the library BEFORE the first upload (`main.py:6557`). A push interrupted by an upload failure commits its journal and keeps that record, so `push <id>` resumes under the strict rule. Pinned by a test.
  - **Chunks with no recorded split are refused, not resumed.** Nothing vouches for their bytes or their completeness, and uploading them would leave the entry onboarded with no split record. This matches `ROLLBACK_MECHANISM.md` O-1 (the master survives, so the chunks can always be re-split) and D-6 (the pre-existing dir is left for the user to delete). It needs no change to the journal, PONR placement, `recover_journal` or the season resume-range messaging.
  - A hard kill mid-upload is not a resume either, and was not before: IMP-R7's journal-open recovery rolls that run back to "never split", so the next push re-splits. Unchanged.
- Rollback change-gate: this change IS the gated one, and the user ruled on it. Its scope is resume selection.
  - Every refusal comes before any upload and before anything is journalled. It records nothing, marks no PONR, calls neither `rollback` nor `commit`, never touches the pre-existing chunk dir (D-6) and saves nothing: IMP-C31's exit shape, including the empty journal every pre-flight refusal leaves.
  - Untouched: the journal format, PONR placement, what is journalled, the O-1 failure branches and resume message, `recover_journal`, the season resume-range messaging and `RollbackHardFail`. `cmd_prep` is untouched too.
- **Needed a decision (not changed here): autopilot re-runs after an interrupted split push.** Since settled: the user chose option 2 on 2026-10-02 and **IMP-C33** implements it, together with the stale-record fix. The text below describes the behaviour as IMP-C32 shipped it.
  - What happens. A re-run of `prep_push_rep`, or the season autopilot's printed `Resume the rest of the season: prep_push_rep_season … episodes N-M` line, re-preps the interrupted entry first, and the re-prep drops its recorded split. Before this fix the push then uploaded the leftover chunks and archived the entry with no split record. Now the push refuses them: the leftovers must be deleted, and the episode is re-split and re-uploaded in full. `push <id>` BEFORE any re-prep resumes properly, and the season command then carries on (pinned by a test).
  - This is a workflow the user runs: the real library shows it (see the real-library check below).
  - Options:
    1. Keep it as shipped and resume with `push <id>`. `docs/OPERATIONS_QA.md` §5 already says not to re-run the autopilot after a failed push.
    2. Make `cmd_prep` keep `split_info` and `re_hashed` when the file's hash is unchanged. That is the rule `merge_extras_into_title` already applies to extras. The re-run then resumes under the strict rule. Recommended, together with the stale-record fix below.
    3. Make the season message name `push <id>` first.

    Option 2 changes `cmd_prep`, and option 3 changes the change-gated season resume-range messaging, so both are outside this ruling.
  - Related and pre-existing (fixed since, with IMP-C33): a whole-file push keeps a stale `split_info`. After a refusal, deleting the leftovers and running a plain `push <id>` (no split size) uploads the file whole while the entry still records the old split (confirmed by a scratch run). The refusal therefore always suggests a split size. Option 2 makes that state easier to reach, so the two belong together.
- Tests: `tests/test_push_strict_resume.py` has 26 hermetic tests (`sandbox`, `mock_device`, `make_video`, `sandbox_extras`; `stub_tech_specs` + `fake_dummy` for the season autopilot).
  - **Reproductions (20, all failing before the fix):** another episode's chunk × 3 calls; the `push_group` cascade end to end; unrecorded leftovers (lone holder, partial chunk, complete-looking chunks) × 3 calls; the season autopilot's printed resume command; a recorded chunk or holder with changed bytes; recorded chunks beside four kinds of stranger; a damaged chunk in a later range; two extras shapes.
  - **Genuine resumes pinned:** an interrupted first push of the same entry; `push <id>` followed by the season command (the documented recovery); `chunks N-M` ranges; a recorded FLAC holder (beside a chunk, and alone); the `tempdir` redirect; an interrupted extras push; `push_group` on a season, second run; a recorded chunk with no recorded hash.
  - The season fixtures carry a `multi_ep_alias`, so the owner lookup, a new whole-library iterator, is pinned alias- and season_map-safe.
  - **Existing fixtures corrected:** 8 test files seeded resume chunks whose recorded hash was a placeholder (`"hash1"`, `"x"`, a fixed constant). A real split always records the true sha256 of each chunk and holder (`main.py:6470`, `main.py:6075`, `main.py:912`), so those fixtures described a state only a damaged chunk can produce. They now record the real sha256 of the bytes they seed. No assertion changed, and no production workflow depends on a mismatched recorded hash.

  Disarm probes: 22/22 caught. Full suite 1076 passed; smoke 82 passed.
- Real-library check (read-only, 2026-10-02):
  - Nothing is exposed now: no `_parts/` or extras chunk dir exists, and 0 entries are `local_ready`. 73 folders are shared by more than one leaf (1131 leaves).
  - The content check has full coverage and no false-refusal risk: all 269 recorded splits carry a hash for every chunk and holder, and all 1033 recorded hashes are sha256-shaped.
  - **The old behaviour already bit.** 3 archived episodes have chunk sidecars in `checksums/` but no `split_info`: `tv-en-2003-thewire-s02e12`, `tv-en-2017-dark-s01e10` and `tv-en-2004-battlestargalactica-s01e11`.
    - `tv-en-2017-dark-s01e10` matches shape (b) by its timestamps. It was split on 2026-06-14 and its chunk 001 reached Google Photos that night; on 2026-06-22 the legacy reconcile still found it `local_ready`. The entry was re-prepped at 23:41 on 2026-06-27 (its `<short_id>.sha256` was rewritten), its master was dummied at 23:42, and chunk 002 reached Google Photos about 80 minutes later.
    - `tv-en-2004-battlestargalactica-s01e11` fits the same path: split on 2026-06-23, re-prepped on 2026-06-28, and both chunks reached Google Photos only after that re-prep.
    - `tv-en-2003-thewire-s02e12` was pushed in full on 2026-02-09 and lost its record to an older re-prep over a legacy text dummy.
    - No bytes are lost: the IMP-C25 Google Photos inventory (`D:\MediaVault_gp_inventory\tv.items.jsonl`) lists both chunks of all three in the TV account.
    - `fetch_restore` cannot restore them until their `split_info` is rebuilt from the `checksums/<chunk>.sha256` sidecars, as the legacy reconcile did for 27 entries (`docs/feature-legacy-reconcile/REPORT.md`). Not done here: this task writes nothing to the real library, and no command does it yet (unregistered).
- Merge note for IMP-C25: that branch edits `cmd_push` and `push_one_extra` near both resume branches, so expect textual conflicts there. Keep `_resume_plan` at both sites, and keep the pre-upload check before the capture snapshot: it hashes the resumed files, which also gives the capture their sha1. Its resume fixtures (`_seed_parts` in `tests/test_device_inventory.py` and `tests/test_gphotos_primitives.py`, checked at `d9458bf`) already record real chunk and holder hashes, so they need no change.
- Effort estimate: small-medium · Risk: low-medium. A resume now reads each resumed chunk once more to hash it before uploading. Every recorded, intact resume behaves as before. No `ENTRY_TYPE_KEYS` involvement.
- If skipped: a push in a shared season folder can be marked onboarded on another episode's chunks, and any unrecorded leftover turns the next push into an archive that cannot be restored.
- Status: done (`fix/imp_c32_strict_resume`)

---

## IMP-C33: re-running an autopilot after an interrupted split push was refused — `cmd_prep` rebuilt the entry without its split record; prep now keeps the record of an unchanged file, and a whole-file first archive drops an unfinished one

- Category: bug (the follow-up decision IMP-C32 left open)
- Priority: high (Band 0 — the natural way to continue an interrupted archive, re-running the autopilot or the season autopilot's own printed resume command, could not resume: it was refused since IMP-C32, and archived the entry with no split record before it)
- Files: `main.py` — the keep rule in `cmd_prep`'s entry rebuild (`main.py:1510-1534`) and the whole-file rule in `cmd_push`'s success block (`main.py:6776-6793`). The user ruled on a decision card on 2026-10-02: "Prep keeps split record (Recommended): cmd_prep keeps split_info when the file's hash is unchanged (extras already do this). Re-running the autopilot then resumes correctly. Small follow-up PR; changes cmd_prep, not the rollback rules." Branch `fix/imp_c33_prep_keeps_split_info`.
- Current behavior (before fix):
  - An interrupted split push leaves the entry `local_ready`, with the split recorded in `split_info` (saved before the first upload, `main.py:6584`) and the chunks not yet uploaded in `_parts/`. `push <id>` resumes them under the strict rule.
  - Every autopilot preps first. `cmd_prep` rebuilt an existing `local_ready` entry from scratch (`main.py:1495-1508`), which dropped `split_info` and `re_hashed`. The push that followed found the entry's own leftover chunks with no record of them and refused (`tagged for this entry, which has no recorded split`). Before IMP-C32 it uploaded them and archived the entry with no split record.
  - That covered a re-run of `prep_push_rep`; the `Resume the rest of the season: prep_push_rep_season … episodes N-M` line the season autopilot prints for exactly this case; either one with `tempdir`; and both `_enrich` autopilots, which run the same archive step (the movie one prints `(or simply re-run this same command)` when the archive does not finish, `main.py:9573`).
  - Reproduced hermetically on `main` (`faa62e5`) for all five: interrupt the push at chunk 2, run the same command again, and the entry is still `local_ready` with its chunk waiting in `_parts/`.
- Fix (implemented):
  - **Keep rule (`cmd_prep`).** A re-prep that finds a split recorded on the existing entry compares the hash it has just computed with the stored one. If they are equal, the rebuilt entry keeps `split_info` whole (chunks, method and size, carried-out FLAC holder, a staged canonical hash) and its `re_hashed` flag, and the next push resumes the recorded chunks. `merge_extras_into_title` already treats an unchanged extra this way (`main.py:5507`).
  - Only a still-local entry can reach that point. The IMP-D4 guard returns first for anything uploaded or carrying a cloud-bearing status (`main.py:1389`). It is untouched, and both of its arms are now pinned.
  - The hash decides, not the file name. The same bytes under a new name keep the record, because the record names chunks and those do not move.
  - **A changed file** drops the record and the flag, as before. Prep now says so, and says the old chunks are stale. It does not delete them: a season's episodes share one `_parts/`, and with `tempdir` prep does not know where they are. The next push refuses them by name (IMP-C32): `<chunk>  (tagged for this entry, which has no recorded split)`, then `They are leftovers: delete them, then push again to re-split the master (it is intact), e.g. push <id> COUNT 2`. Pinned, text included.
  - **A whole-file first archive drops an unfinished split record (`cmd_push`).** See the verdict below.
- Stale `split_info` on a whole-file push: same root, fixed here.
  - The hazard (found during IMP-C32, never registered). An entry still records an interrupted split, its leftover chunks are gone, and a push uploads the file whole: no split size was given, or the file is under the size given. The push completed with the old record in place. The remote `.mvmeta.json` then described a split, `replace` promoted that split's staged canonical hash to the entry's hash (after a `rehash` run, `main.py:7249-7256`), and `fetch_restore` looked for chunks the cloud does not fully hold.
  - It is the same root as the refusal above: a split record that does not describe what is on disk or in the cloud. The keep rule alone would also have widened it. Deleting the leftovers and re-running the autopilot without a split size used to be safe only because the re-prep cleared the record.
  - Fix: when a push that is not a `chunks N-M` range completes by uploading the master itself, and the entry had never completed an upload, the record and its `re_hashed` flag are dropped before the remote sidecar is written. The entry then reads exactly like a whole-file archive of a file that was never split. `push_one_extra` already drops an extra's record on a whole-file push (`main.py:6117`).
  - Left alone on purpose: an entry that was already uploaded (a restored split entry's chunks are a complete cloud copy), a `chunks N-M` push (it never marks an entry uploaded), and any push that fails.
  - **One residual, for awareness.** "Records a split, no leftovers, not uploaded" also describes a title finished with `chunks N-M` range pushes. The step after those is `set_uploaded <id>`, as the refusals already say. A plain `push <id>` in that state uploads the whole file and now drops the record of the chunks; before, it kept it. The entry then points at the whole-file upload, which is a complete copy only if Google Photos takes a file of that size. A stricter rule (refuse a whole-file push while the entry records an unfinished split) would cover both cases, but it needs a way to clear a record on purpose, so it is left as a decision.
- Rollback change-gate: assessed, not crossed.
  - `cmd_prep` journals what it did before. A first prep records its two sidecars and its entry; a re-prep of an existing entry records nothing (pinned with a spy on the journal).
  - The kept record is part of the entry `cmd_prep` builds. After the keep decision only the library save can fail. Prep's rollback then saves the library it holds, so the entry on disk equals the previous one, split record included (pinned). A failure before the decision, such as the hash, leaves the entry untouched (pinned).
  - `cmd_push` drops the record in memory after every upload has succeeded, and the save that marks the entry onboarded persists it. Push has no PONR and nothing after that point rolls back. A whole-file push that fails leaves the entry and its record untouched (pinned).
  - Unchanged: the journal format, what prep and push record, PONR placement, `recover_journal`, the O-1 resume message, the season resume-range messaging and `RollbackHardFail`.
  - One consequence of the unchanged D-6 scoping is visible. The push journal records `split_info` only when the run itself created it. A re-run that has to split afresh (the leftovers are gone) now finds a record already there, because prep kept it. If that run fails before any upload, rollback removes its own `_parts/` and `checksums/` and leaves the record in place. A plain `push <id> <split>` re-run always behaved this way; the autopilot path used to pop the record, because its re-prep had cleared it first. The next push splits again, or uploads the file whole and drops the record. Pinned.
- The `_enrich` autopilots: checked, no mismatch is possible. They run the plain autopilot first and enrich only once the entry is `archived` (`main.py:9570`, `main.py:9717`), so no folder is renamed between the interrupted run and the re-run. The record stores chunk file names, not paths, so it still matches the cloud after the rename that follows the archive. Pinned end to end for the movie and the season command, including a restore from the renamed folder.
- Found here, not changed (unregistered; needs a decision):
  - **The season resume line leaves out `tempdir` and `rehash`.** `_season_resume_cmd` (`main.py:9183-9215`) rebuilds the command from the split, `episodes`, `device`, `--extras` and `--extras-size`. After a season run that used `tempdir`, the printed line pushes without the redirect, so it does not look in `<tempdir>\<id>\_parts`: it splits the interrupted episode again in the season folder and uploads it from the start, and the old chunks stay in the tempdir. Adding `tempdir <dir>` to the printed line by hand resumes properly. Without `rehash`, the remaining episodes are split with a deferred canonical hash instead of an eager one. This is the change-gated season resume-range messaging, so it is not touched here.
  - A re-prep still re-hashes the whole file (IMP-D23), so `push <id>` remains the cheaper resume.
- Tests: `tests/test_prep_keeps_split_record.py` has 26 hermetic tests (`sandbox`, `mock_device`, `make_video`, `stub_tech_specs`, `fake_dummy`). 13 of them fail on `main`.
  - **End-to-end reproductions (5):** `prep_push_rep` interrupted at chunk 2 and run again; the season autopilot's own printed resume line, run through `main.py`'s real CLI dispatcher; `prep_push_rep` with `tempdir`; `prep_push_rep_enrich`; `prep_push_rep_season_enrich`. Each must resume the remaining chunks, finish, replace, and end `archived` with its split record intact and matching the cloud. A real `cmd_restore` from the device's objects must then give back the original bytes.
  - **Carried-out FLAC holder (1):** an interrupted carry-out push left its holder beside the last chunk. The re-run uploads both, and the entry still records `carried_out_tracks`.
  - **Keep rule (4):** an unchanged file keeps every field of the record (chunks, method and size, staged canonical hash, carried-out tracks) and its flag, and the entry is identical after the re-prep; a renamed file keeps it; a changed file drops it, leaves the chunks alone, and the next push refuses them with the right text; an entry that was never split preps exactly as before, with no new output.
  - **IMP-D4 guard (7):** every cloud-bearing state, by the `uploaded` flag alone and by each status alone, is still never rebuilt.
  - **Rollback (4):** the journal spy, the two failed re-preps, and the fresh split that fails before any upload.
  - **Whole-file push (5):** the stale record and its flag are dropped, the sidecar says whole file, `replace` promotes no canonical hash and the restore works; an already-uploaded split entry keeps its record; the autopilot re-run without a split size ends as a restorable whole-file archive; a failed whole-file push and a range push leave the record alone.
  - One IMP-C32 test changed. Its season-autopilot reproduction reached "split record gone" through the re-prep. That route is closed, so the test now removes the record by hand. Its assertions are unchanged.

  Disarm probes: 29/29 caught. Full suite 1101 passed, with the one pre-existing test that opens the real library deselected for this run; smoke 82 passed.
- Real-library check (read-only, 2026-10-02; one read of the four library files, no folder access): nothing is affected today. All 1322 physical leaves are uploaded, so prep rebuilds none of them and no entry records an unfinished split. 270 leaves record a split and every one is uploaded, so the whole-file rule cannot touch them. No leaf carries `re_hashed` without a split record.
- Merge note for IMP-C25: that branch replaces the `write_remote_mvmeta` call two lines below the new block in `cmd_push`. Keep the drop before that call, so the sidecar describes the whole file. The branch does not touch `cmd_prep`.
- Effort estimate: small · Risk: low. Only a re-prep of a still-local entry that records a split changes, and a whole-file first archive of one. A first prep, an entry that was never split and every uploaded entry behave as before. No `ENTRY_TYPE_KEYS` involvement.
- If skipped: after an interrupted split push, re-running the autopilot or the season's printed resume command is refused, and the leftovers have to be deleted and the title split and uploaded again from the start.
- Status: done (`fix/imp_c33_prep_keeps_split_info`)
