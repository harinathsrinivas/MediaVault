# Fable Review — Merged-PR Review (PRs #1–#21)

What each merged PR changed, with deep dives on the two "big changes" the user called out:
**#14 auto-rollback** and **#20 deterministic split-hash**. Reviewed 2026-06-12 from `gh pr list/diff`,
the feature docs under `docs/`, and a full read of the current code.

## The two big changes

### PR #14 — Feature: auto-rollback for multi-step commands (merged 2026-06-01) · +2814 −380, 20 files

**What it is.** One unified failure-handling mechanism replacing two ad-hoc paths
(`prep_push_rep`'s `_parts/` rmtree, and the season pilot's bare `break` "to prevent mess").

**Mechanism (verified in current code).**
- `RollbackJournal` (`main.py:550`) — per-command, per-id durable on-disk journal
  (`<folder>/.mediavault_txn.json`), each intended mutation recorded BEFORE the forward action,
  fsync + `os.replace` flushed (survives hard kills). Chosen via a 3-candidate user-decided bake-off
  (Candidate C — on-disk journal — over A: context-manager transaction, B: compensating-action stack);
  full record in `docs/feature-auto-rollback/rollback-architecture/DECISION.md` and `DECISIONS.md` N-6.
- Reversible failure → `rollback()` replays inverses LIFO, reverts the in-memory library, saves,
  deletes journal. Partial rollback (file locks) reported honestly, journal kept for retry.
- `mark_point_of_no_return()` — after the PONR marker, failures raise `RollbackHardFail(state, reason,
  resume_cmd)` where `resume_cmd` always names an EXISTING command (N-2; in practice `fetch_restore <id>`).
- Exactly two true PONRs: `cmd_replace`'s commit rename (`main.py:1804`) and `cmd_restore`'s split-path
  chunk delete (PONR marker at `main.py:2185`). Push is O-1: never a PONR, always resumable
  (`any_upload_done` decides journal-commit-and-resume-message vs pre-upload rollback).
- `recover_journal()` + (since PR #18) the `recover` CLI — finishes an interrupted pre-PONR rollback;
  never on the happy path (D-4: happy path byte-for-byte identical).
- Season pilot prints an exact **resume-range command** (reconstructs SIZE/device/episodes args).
- Also bootstrapped the first pytest suite (conftest + baseline-happy-path + rollback scenario matrix).

**Why it matters for the end-goal roadmap:** any future daemon/REST layer can re-use these semantics —
a failed background fetch/restore is either cleanly rolled back or carries a machine-readable
`resume_cmd`. This is exactly the error contract a Jellyfin-integration service needs.
**Change-gate:** all of the above is frozen behavior — `CLAUDE.md` + `ROLLBACK_MECHANISM.md` §10.

### PR #20 — fix: verifiable canonical hash for split files (merged 2026-06-08) · +3547 −50, 16 files

**The problem.** mkvmerge's default merge is non-deterministic (random segment UID + mux timestamp), so
a split file's re-merged container could NEVER match a stored hash — historically `cmd_restore` blindly
overwrote `entry["hash"]` with whatever the merge produced (unverifiable; "don't fix" was the old stance,
reversed 2026-06-07).

**The fix (verified in current code).**
- `mkvmerge --deterministic <seed>` (seed = entry `short_id`) makes merges byte-identical →
  the merged hash becomes a stable, *verifiable* canonical whole-file hash.
- **Verify-or-bless** (`bless_or_verify_merged_hash`, pure, `main.py:286`): first restore of an
  unblessed entry BLESSES (`hash`=merged, `re_hashed=true`, store `merge_seed`/`merge_tool`/
  `rehashed_at`); later restores VERIFY and on mismatch alarm loudly + return PRE-PONR (chunks kept) —
  corruption/tool-drift can no longer be silently absorbed.
- **EAGER mode** (`push ... rehash`): merge fresh chunks once at push, stage transient
  `split_info.canonical_hash`, promote into `entry["hash"]` at replace. Failure falls back to deferred.
- **Re-split reset** (`main.py:1381`): a NEW split clears `re_hashed` + canonical fields so the next
  restore re-blesses instead of false-alarming. Resume of existing `_parts/` does NOT reset.
- **Disk pre-flight**: deferred 1X / eager 2X + max(1%, 2 GB) buffer, hard-stop BEFORE splitting;
  season/group sizes to the largest single splitting item; `tempdir <path>` redirects `_parts/` +
  eager temp off-volume (journal + `checksums/` stay put).
- `tools/migrate_rehash_flag.py` stamped `re_hashed=false` onto existing split entries.
- Rollback contract untouched except the two pre-authorized seams (blind-overwrite reversal, `_parts`
  path value).

**Why it matters:** split files now have end-to-end verifiable integrity, which any future
streaming/serving layer can trust; and `tempdir` is the seam a daemon can use to keep chunk scratch
off the media volume.

## The rest, in order

| PR | Merged | Summary | Notes for this review |
|---|---|---|---|
| #1/#3 | 05-28 | **Video dummies**: replace text-blob placeholders with real ~10 KB ffmpeg-generated videos per container recipe (`DUMMY_RECIPE_BY_EXT`); `repair_dummies` regenerator; live run regenerated 423 dummies | ⚠️ Invalidates `apple_tv_ui_roadmap.md` §5 (dummy detection via `"Original Hash:"` text marker no longer exists — detection must use size threshold + `uid` sidecar / library lookup) |
| #2 | 05-28 | **ADB device select**: opt-in `device <id_or_name>` on all four push commands; `DEVICE_ALIASES` (movies/series serials) | Means **IMP-C4 status `pending` is wrong → effectively done** |
| #4 | 05-28 | Auto-rollback planning docs | |
| #5 | 05-28 | **IMP-C9** atomic replace (two-rename + stale sweep) | |
| #6 | 05-29 | **IMP-C11** restore hash-mismatch quarantine | |
| #7 | 05-29 | **IMP-G1** `.partial` upload + atomic remote `mv` (rclone-chunker pattern) | |
| #8 | 05-30 | **IMP-A1** extract `mvcommon.py`; strict loud `load_library` both entry points; hash progress bar | |
| #9 | 05-30 | **IMP-C2** retry w/ backoff (ADB push+mv ×3; Selenium trigger ×2) | |
| #10/#11 | 05-30 | Agent pipeline → Opus 4.8 effort tiers (**IMP-H1**); git/PR conventions + human gates (Checkpoints 1/2) | |
| #12 | 05-30 | **IMP-C8** post-push remote sha256 verify, gated `PUSH_VERIFY_REMOTE=False` until config (IMP-A5) | |
| #13 | 05-31 | Docs sync; resolve O-1; LF/EOL policy | |
| #15 | 06-01 | Gitignore Obsidian vault + transcript dumps | Stray transcript .txt files at root are ignored but still on disk |
| #16 | 06-02 | Planner prompts for IMP-R2/C1/R1 (`docs/next-tasks-planner-prompts.md`) | |
| #17 | 06-02 | Agents: top-level orchestration rule (no nested Task) + no-silent-handling rule | |
| #18 | 06-03 | **IMP-R2** `recover` CLI (`recover <id\|folder>`, `recover --scan`) | |
| #19 | 06-05 | Episode parsing fix: SxxExx regex no longer captures decimals (`[sS]\d+[eE](\d+)`) so dotted TITLES (Fringe "S03E20 .6:02 AM EST") parse as e20, not e20.6 | ⚠️ ARCHITECTURE §7.8 still documents the old regex; `main.py:1030` comment "handles .5" now misleading for the SxxExx branch |
| #21 | 06-09 | **IMP-E13** multi-episode combined files: `multi_ep_alias` entry type, alias creation in `cmd_prep_season`, `_resolve_alias` + de-alias in the four group loops and `mainfetch.resolve_targets` | ⚠️ Library-wide iterators NOT in that list were missed: `cmd_scan_unprepped` (KeyError crash) and `cmd_local_status` (TypeError crash) — see REVIEW_NOTES A1/A2; single-id commands also crash on alias ids (A3) |

## Process observations

- Root `STATUS.md` is a tracked per-run scratchpad, re-written by PRs #14/18/19/21 and stale between
  runs — should be gitignored like root `PLAN.md` (canonical copies already live in `docs/<feature>/`).
- `.candidates/step-*/DECISION.md` judge artifacts got committed by PR #20 (reasonable — decision
  provenance), but the convention isn't written anywhere.
- Two leftover locked agent worktrees (`.claude/worktrees/agent-a7378bcf…`, `agent-a79b36f…`) are still
  registered with stale tier-file copies inside — candidates for `git worktree remove` after checking
  for uncommitted work.
- PR #19 carries the lesson recorded in memory: git-agent invented an IMP code (IMP-R1) for a PR whose
  plan said "no IMP code" — verify PR titles against the tier files.
