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
- Status: pending

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
- Status: pending

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
- Status: pending

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
- Status: pending
