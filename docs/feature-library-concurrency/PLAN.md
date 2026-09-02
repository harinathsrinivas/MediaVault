# Task: Fix concurrent library-write data loss (IMP-C24) + expensive prep re-hash on resume (IMP-D23)

Suggested branch: fix/library_concurrency_and_prep_resume
Framework: v2

> **Planning-only artifact.** This plan proposes a concrete, recommended design for both fixes, but
> IMP-C24's design is deliberately **not yet locked** — it requires a user ruling at the change-gate
> before Step 1 begins (see "Open Decisions" and the 🚦 checkpoint below). IMP-D23 has one smaller,
> non-gated open question. Whoever executes this plan must resolve those first.

## Context

A real incident (verified, not speculation — full record in the sibling
`docs/feature-library-concurrency/SESSION_HANDOFF.md`): the user ran
`prep_push_rep_season tv-en-1994-friends-s03 episodes 1-13`; prep succeeded, push failed (ADB
disconnected). To avoid re-hashing 13 UHD episodes, they resumed manually with
`push_group … episodes 1-13` in one shell while running `replace tv-en-1994-friends-s03e12` (etc.)
in a **second shell, concurrently**, to reclaim disk as each push finished — a legitimate workflow
the system has never protected. The result: 13 entries ended in mixed, wrong states, and days later a
mistaken re-run of `push_group` caused `cmd_push` to upload a 9,672-byte **dummy** file to Google
Photos in place of a real episode. The D4 integrity guard caught it — but only *after* the upload.
Root cause (verified in code, not assumed): `mvcommon.load_library()` merges all four library JSONs
into one dict; `mvcommon.save_library()` rewrites all four from that dict on **every** call; there is
**no lock anywhere**. Two concurrent mutating commands each hold their own stale in-memory snapshot
across a slow operation (a multi-GB ADB push, a whole-file SHA256) and whichever saves last silently
overwrites the other's change — a classic lost update whose blast radius is the **entire library
set**, not the one file either command thought it was touching. Separately, the user's own workaround
exists because `cmd_prep` has no way to skip re-hashing an entry it already hashed moments earlier —
exactly the gap IMP-D23 closes. `main.py web` (a FastAPI console, `webui/server.py`) is a further,
currently-running actor in this same hazard: its `/api/action/{name}` routes dispatch to the same
`main.cmd_prep`/`cmd_push`/`cmd_replace`/`cmd_prep_push_rep`/`cmd_fetch_restore` functions through a
single in-process FIFO worker (`_worker_loop`, `webui/server.py:523`) — safe against *itself*, but
just another OS process racing a concurrent CLI command exactly like two CLI shells race each other.

## Goal

1. **IMP-C24**: two concurrent library-mutating commands (any two of `prep`/`push`/`replace`/
   `restore`/their group & autopilot wrappers/`web`) can never silently lose either one's update —
   proven by a regression test that reproduces today's loss and passes after the fix — **without**
   serializing the user's legitimate parallel workflow (a slow push in one shell, a fast replace in
   another) into a multi-minute wait.
2. **IMP-D23**: resuming a failed push after a successful prep costs zero re-hashing, either via a
   new dedicated command or an explicit opt-in flag — never a silent, unproven skip by default.
3. Both ship with a full green suite (`python -m pytest tests -q`, baseline **887 passed**) and smoke
   gate (`python -m pytest tests/smoke -q`, baseline **80 passed**), and the auto-rollback contract
   (`docs/feature-auto-rollback/ROLLBACK_MECHANISM.md` §10) is either provably untouched or its
   deltas are explicitly user-approved beforehand, exactly like IMP-R6/R7/D20 were.

## Files affected

- `mvcommon.py` — new `library_lock()` context manager + `LibraryLockTimeout` + `save_library_atomic()` helper (IMP-C24); `LIBRARY_LOCK` constant alongside the existing `MV_LOCK_DIR`/`FETCH_SESSION_LOCK`.
- `main.py` — every `load_library()`→mutate→`save_library()` site (enumerated below) migrated to the new primitive (IMP-C24); `RollbackJournal.rollback()`/`recover_journal()`'s own `save_library()` calls (IMP-C24, the one piece that is genuinely change-gated); `cmd_prep`'s early-skip block (IMP-D23 option b); new `cmd_push_rep`/`cmd_push_rep_season` + CLI dispatch wiring (IMP-D23 option a); `ENTRY_TYPE_KEYS` comment (IMP-D23, new optional `tech_spec.local_mtime` field).
- `webui/server.py` — no code change expected (it calls the now-fixed `main.cmd_*` functions), but gets one new proof test that its background worker is equally protected.
- `tests/test_library_concurrency.py` (new) — the regression test proving the lost update, and its post-fix flip to green.
- `tests/test_mvcommon.py` or a new `tests/test_library_lock.py` — unit tests for the lock primitive.
- `tests/test_entry_schema_guard.py` — one new round-trip case for `tech_spec.local_mtime`.
- `tests/test_push_rep.py` (new) or alongside `tests/test_prep_push_rep_enrich.py` — tests for `push_rep`/`push_rep_season` and `--assume-unchanged`.
- `ARCHITECTURE.md` — §6.1 (load/save description), §12a (short addendum, mirroring the existing IMP-R6/R7/extras addenda), §5 (new entry points), §6.3 (new `tech_spec.local_mtime` field).
- `improvements/improvements_tierC.md` (IMP-C24) / `improvements_tierD.md` (IMP-D23) / `improvements/PRIORITY.md` / `docs/priority-graph/priority-graph.html` — **already registered by this plan's author**; Step 10 below is a confirm-and-reconcile pass once implementation ships (flip `Status: pending` → `done`, fill in the real branch/PR).
- `docs/feature-library-concurrency/PROGRESS.md` (new, Step 0) / `DECISIONS.md` (new, Step 0) — the v2 execution journal.

## Approach

Two independent fixes sharing one branch and one incident narrative. **IMP-C24 is the critical-path
item and is change-gated**: several viable designs touch the wrapping of `cmd_prep`/`cmd_push`/
`cmd_replace`/`cmd_restore` and `RollbackJournal`'s own use of `save_library()`, which
`CLAUDE.md`/`ROLLBACK_MECHANISM.md` §10 require the user to explicitly rule on before any code moves.
This plan's recommendation (see "IMP-C24 — Options Analysis" below) is a **fine-grained, short-held
cross-process lock** (`mvcommon.library_lock()`, mirroring the already-proven `fetch_session_lock`
O_CREAT|O_EXCL primitive from IMP-C17, but deliberately NOT copying its "reclaim-and-proceed-on-
timeout" policy — that specific behavior would silently reintroduce this exact bug under contention)
combined with **narrowing every mutator's write window** so the lock is only ever held for a
millisecond-scale "reload fresh, apply my known changes, save" critical section — never across ADB
I/O, whole-file hashing, or mkvmerge splitting. This closes the race *and* preserves the user's real
parallel workflow (a slow push and a fast replace no longer collide, because the fast one's write
lands, and the slow one's later write re-reads fresh state first instead of clobbering it).

**IMP-D23** is not change-gated for its recommended shape: a new additive `cmd_push_rep`/
`cmd_push_rep_season` pair (Option a — wraps the existing, unmodified `cmd_push`+`cmd_replace`,
exactly mirroring how `cmd_prep_push_rep` already composes all three) plus an opt-in
`--assume-unchanged` flag on `cmd_prep` itself (Option b — a THIRD early-skip check alongside the two
that already exist at `main.py:1049-1080`, same zero-artifact/no-journal contract). Option c (editing
the change-gated season resume-range message) is evaluated and explicitly deferred.

The plan sequences C24 first (Steps 1-6, the critical-path fix, gated on the checkpoint below), then
D23 (Steps 7-9, independent of C24's outcome except that it is safer to build new code atop an
already-lock-safe `cmd_push`/`cmd_replace`), then a final registration reconciliation (Step 10).
D23's steps do **not** need to wait for C24's change-gate ruling and may be executed first or in
parallel if the user prefers — they are sequenced this way in the plan for narrative clarity only.

---

## IMP-C24 — Options Analysis

Evaluated against the verified code (`mvcommon.py:551` `load_library`, `:569` `save_library`; every
mutator enumerated in the site table below) and the verified incident mechanism.

| # | Option | How it works | Closes the race? | Preserves parallel workflow? | Touches the rollback change-gate? | Verdict |
|---|---|---|---|---|---|---|
| a | **Coarse cross-process lock** (whole command held) | Acquire a lock before `load_library()`, release after `save_library()`/`commit()`, wrapping the WHOLE command from outside | Yes | **No** — a `replace` in shell B would block for the entire duration of a multi-GB push in shell A (minutes), defeating the exact parallelism the user relies on. If the lock times out and silently proceeds instead of blocking, the race reopens | No (pure outer wrapper around unmodified commands) — but changes observable *concurrent workflow* behavior, which is its own decision, separate from the rollback contract | Not recommended alone — crux failure named in the dispatch |
| b | **Narrow the write window** (no lock) | Every mutator re-reads fresh, applies only its own known changes, writes — right before persisting, never held across I/O | Shrinks the window from minutes to milliseconds, but does **not eliminate** it — two processes can still collide inside the tiny window | Yes | **Yes** — changes what happens at the exact moment `cmd_prep`/`cmd_push`/`cmd_replace`/`cmd_restore` persist on their happy path | Necessary but insufficient alone — a residual race is not an acceptable bar for silent data corruption per this project's own standing philosophy (memory: "no automatic quality decisions... detect, stop, and hand the user the command"; the same "don't silently trust luck" spirit applies here) |
| c | **Merge-on-write** (dirty-field tracking) | `save_library` re-reads current disk state and merges in only the fields THIS process's command is known to have changed; requires every mutation site to record its own deltas instead of mutating a shared dict directly; deletions need an explicit tombstone/sentinel since "absent from my delta-set" is ambiguous between "untouched" and "should be removed" | Yes, and with the best throughput (no serialization at all — true concurrent field-level writes) | Yes | **Yes, and the most invasively** — `save_library`'s core contract changes, and `RollbackJournal.rollback()`'s internal `save_library()` call would need the same delta-tracking machinery | Real merits (best throughput), but the highest engineering cost and the largest blast radius for THIS problem; list-field merges (e.g. two processes each appending a different child to the same `season_map.children`) need real conflict-aware merging or they silently lose one append too — a subtler, harder-to-test failure mode than what it replaces. The codebase's own prior bake-off (`RollbackJournal`'s Candidate C selection, `DECISIONS.md` N-6) explicitly favored a simpler, more provably-correct mechanism over cleverer alternatives for exactly this class of risk |
| d | **Detect-and-refuse** (PID/heartbeat file) | A second mutating command checks a heartbeat file and refuses to start while another is running | Yes (by preventing overlap entirely) | **No** — actively blocks the user's specific, intentional workflow (push in one shell, replace in another to reclaim disk as it goes) rather than making it safe | No (pure outer wrapper) — same workflow-behavior caveat as (a) | Cheapest, but converts a "make it safe" problem into a "forbid it" problem for a workflow the user does on purpose |
| e | **Document only** | No code change; state the hazard loudly in docs/README | N/A | Yes (nothing changes) | No | Honest baseline, but proven insufficient — this exact hazard already caused real damage despite the user being generally careful; reliance on memory/documentation alone is not credible after one incident |

**Recommendation: (a)+(b) combined — a fine-grained, short-held lock wrapping ONLY the narrowed
write window**, not the whole command. Concretely:

- New `mvcommon.library_lock(timeout=30, stale_after=600)` — mirrors `fetch_session_lock`'s proven
  O_CREAT|O_EXCL atomic-create idiom (`mvcommon.py:459-540`) on the SAME `MV_LOCK_DIR`, with one
  deliberate divergence: on a **live, contended** lock, poll up to `timeout` seconds and on
  exhaustion **raise `LibraryLockTimeout`** — never silently reclaim-and-proceed the way
  `fetch_session_lock` does (that policy is correct for a single-flight convenience lock guarding a
  CDP port; it would be a correctness bug here, silently reintroducing this exact race under
  contention). A genuinely **stale** lock (`stale_after=600s` — the critical section this protects is
  milliseconds, so 10 minutes of holding it means the holder crashed, not that it's busy) is still
  reclaimed, exactly like `fetch_session_lock`.
- New `mvcommon.save_library_atomic(apply_fn)`: `with library_lock(): fresh = load_library();
  apply_fn(fresh); save_library(fresh)`. All ADB I/O, hashing, and mkvmerge work happens BEFORE this
  call, never inside it — so the lock is held for single-digit milliseconds regardless of how large
  the file being pushed/replaced/restored is.
- Every mutator refactors its final persist point from `library[id][f] = v; save_library(library)`
  to computing the same field assignments and calling `save_library_atomic(lambda fresh: …)`. The
  function's own long-held `library` variable stays exactly as-is for READS during the command body
  (parent lookups, entry existence checks) — only the final WRITE changes.
- `RollbackJournal.rollback()`'s own `save_library()` call and `recover_journal()`'s own
  `save_library()` call route through the same primitive in "replay mode" (apply the recorded
  inverses onto a freshly-loaded dict instead of the command's stale one). **This is the one place
  that genuinely touches `RollbackJournal` internals — hence the change-gate.**

**Which options touch the rollback change-gate (per `CLAUDE.md` / `ROLLBACK_MECHANISM.md` §10):**
(b) and the recommended (a)+(b) hybrid — **YES**, because they change what happens inside
`cmd_prep`/`cmd_push`/`cmd_replace`/`cmd_restore`'s happy-path persistence and inside
`RollbackJournal.rollback()`/`recover_journal()`. (c) — **YES**, and most invasively (changes
`save_library`'s core contract that rollback also depends on). Pure (a) coarse-wrapper and (d) — **NO**
in the strict journal-format/PONR sense (they wrap unmodified commands from outside), but both change
observable concurrent-workflow behavior (serialize or refuse), which is a separate,
non-rollback decision still worth the user's explicit sign-off. (e) — no code touch at all.

**What does NOT change under the recommendation, and must be verified as unchanged:** the journal
format / `TXN_JOURNAL_NAME` / durability (`fsync`+`os.replace`); the PONR locations (`cmd_replace`'s
commit rename, `cmd_restore`'s merged-chunk delete) and `mark_point_of_no_return()` placement; the
created-this-run scoping (D-6/D-7); the O-1 resume-message vs O-2 hard-fail split; the
`RollbackHardFail` contract; the season resume-range messaging. The fix only changes **when and how**
the already-existing final `save_library()` call is made — never what triggers a rollback, a PONR, or
a hard-fail.

### Which sites mutate the library (enumerated; read-only commands are NOT listed and need no fix)

Verified via `grep -n "save_library(" main.py` (every call site) cross-referenced against
`grep -n "^def cmd_"`. Long-I/O-holding sites are the highest-probability offenders (widest window);
all sites still need the fix for correctness, since even a fast command's short-but-nonzero window can
still collide with a slower command's earlier-taken, still-unsaved snapshot.

| Function | Def line | Save line(s) | Holds `library` across… | Risk class |
|---|---|---|---|---|
| `cmd_prep` | 1038 | 1180 | whole-file SHA256 (minutes on large files) + MediaInfo scan | **HIGH** — verified in code |
| `cmd_push` | 4735 | 4952 (post-split), 5115 (final) | mkvmerge split + chunk hashing + the entire multi-GB ADB upload loop (minutes) | **HIGHEST** — this is the incident's own mechanism, verified in code |
| `cmd_replace` | 5446 | 5562 | dummy creation + two-rename swap (fast, local disk only) | MEDIUM — short window, but still races against a slower concurrent command's earlier snapshot (this is literally what happened in the incident) |
| `cmd_restore` | 6449 | ~6935/6990 (split-path merge+verify) | chunk merge + hash verify — comparable duration class to `cmd_push`'s upload, per `ARCHITECTURE.md` §7.7/§12a | HIGH (inferred from documented merge-to-temp + verify design; confirm exact save-line count during implementation) |
| `cmd_prep_season` | 4147 | 4138* (loops `cmd_prep`-shaped work per episode — *this line number is just BEFORE the def line in the grep dump, meaning the true per-episode save call(s) need re-confirming against current source at implementation time) | per-episode whole-file hash | HIGH (same class as `cmd_prep`, looped) |
| `cmd_push_group` | 5322 | none of its own — delegates to `cmd_push(mid, …)` per item at line 5433 | inherits `cmd_push`'s fix for free once `cmd_push` itself is fixed (Step 3); verify no independent save call exists (Step 5) | LOW additional risk beyond `cmd_push`'s own |
| `cmd_replace_group` / `cmd_restore_group` | 5593 / 6702 | TBD — confirm delegation pattern (same as `cmd_push_group`) during Step 5 | expected to delegate per-item like `cmd_push_group` | LOW additional risk, pending Step 5's confirmation |
| `cmd_set_search` | 1195 | 1202 | none (fast, local field write) | LOW |
| `cmd_set_tmdb` | 1262 | 1280 | none | LOW |
| `cmd_set_uploaded` | 3553 | 3564 | none | LOW |
| `cmd_rename_folder` | 3620 | 3699, 3742, 4138 (three save calls in this range — exact phase boundaries to be re-confirmed at implementation time) | `os.rename` on a directory (fast, local) | MEDIUM (three separate save points need individual review — may combine into fewer locked sections) |
| `cmd_enrich_metadata` | 2505 | 2684 | TMDB/OMDb/EXA network calls (usually fast, but not bounded) | MEDIUM |
| `cmd_fetch_trivia` | 3266 | 3549 | network calls | MEDIUM |
| `cmd_repair_dummies` | 6210 | (within function; grep-confirm exact line at implementation time) | dummy regeneration (local, fast) | LOW |
| `cmd_verify_library --fix-dummies` | 6011 | (within function) | same class as `cmd_repair_dummies` | LOW |
| `cmd_sort` | 7029 | 7077 (note: variable is `sorted_library`, not `library` — same fix shape, different local name) | none (pure in-memory reorder) | LOW |
| `cmd_add_extras` | 8100 | (within function; extras push/merge — reuses `cmd_push`/`cmd_replace`-shaped logic per IMP-D19/D20/D21) | mirrors extras push duration | MEDIUM |
| `cmd_recover` / `RollbackJournal.rollback()` / `recover_journal()` | 976 / (class method) / (module fn) | 875, 881, 964 (pre-`cmd_recover` in the file — these belong to the rollback machinery itself, not a `cmd_*`) | N/A (recovery path) | **Change-gated** — Step 4 below, called out as its own reviewable sub-bullet within that step (kept separable in the diff even though it lands in the same commit as the other mutator migrations) |
| `cmd_prep_push_rep(_season)(_enrich)` (4 orchestrators) | 7298/7355/7762/7882 | none of their own expected (ARCHITECTURE §12a: "commands are wrapped, not rewritten") | inherit whatever their wrapped `cmd_prep`/`cmd_push`/`cmd_replace` calls do | Verify by grep in Step 4, don't assume |
| `webui/server.py` `/api/action/*` → `main.cmd_*` | server.py:210/216-221/223/231/236 | (delegates entirely to the `main.cmd_*` functions above) | inherits the fix once the underlying commands are fixed; the single in-process FIFO worker (`_worker_loop`, server.py:523) serializes the web console against ITSELF but is still a separate OS process racing a concurrent CLI command | Verify with one new test in Step 5 |

**`cmd_local_status`, `cmd_scan_unprepped`, `cmd_check`, `cmd_verify_restore`, `cmd_verify_library`
(default, non-`--fix-dummies`), `cmd_where_is`-style diagnostics — read-only, no fix needed.**

---

## IMP-D23 — Options Analysis

| # | Option | Touches the change-gate? | Risk | Verdict |
|---|---|---|---|---|
| a | New `cmd_push_rep`/`cmd_push_rep_season` — same args as `prep_push_rep(_season)` minus the prep leg, wrapping unmodified `cmd_push`+`cmd_replace` | **No** — purely additive, composes existing unmodified functions, mirrors the IMP-D22 precedent ("wrap the untouched autopilots, provably zero-diff, no new PONR") | Low | **Recommended (primary)** — fully solves the incident's actual resume scenario |
| b | `cmd_prep` early-skip extension: `--assume-unchanged` + size-match heuristic, gated opt-in | **No** — purely additive to the EXISTING early-skip pattern (`main.py:1049-1080`), same zero-artifact/no-journal-opened contract as the two checks already there | Low-medium — a heuristic, not proof (a same-size in-place edit would silently reuse a stale hash); mitigated by opt-in-only, never default | **Recommended (complementary)** — for users who keep using the original command name |
| c | `--skip-prep`/`--resume` flag that edits the printed season resume-range message itself | **YES** — `CLAUDE.md` explicitly names "the season resume-range messaging" as one of the six change-gated aspects; IMP-D20's `--extras` addition to that same message needed its own sign-off | Low code risk, but requires its own separate change-gate conversation | **Deferred** — not needed once (a) exists; revisit as a future task if the user wants the resume-message itself to be smarter |

**Recommendation:** ship (a) as the primary fix — it fully addresses the verified pain point (a failed
push after a successful prep, resumable with zero re-hashing) with no change-gate and minimal risk —
plus (b) as an opt-in complement. Defer (c).

**Honest limitation of (b):** size-matching is a heuristic. A same-byte-count in-place edit (rare for
video, but not impossible — e.g. a corrupted re-download that happens to match size) would silently
trust a stale hash. This plan mitigates by (i) keeping it strictly opt-in, never the default, (ii)
banking `tech_spec.local_mtime` at every normal prep so a future strengthening (require size AND
mtime match, not size alone) has data to work with, and (iii) being explicit in the flag's own help
text and the improvement-task writeup that this trades a small, clearly-flagged risk for a large time
savings — matching this project's standing "no automatic quality/trust decisions" philosophy (the
user rules on a trust decision; the tool never makes one silently).

**Interaction with the rollback contract (explicitly checked, per the dispatch's own instruction):**
`cmd_prep`'s early-skip path is documented as creating **zero artifacts** — no `RollbackJournal` is
even opened (`main.py:1091` opens the journal AFTER both existing early-skip checks return). Option
(b)'s new check MUST sit in the same place, before `journal = RollbackJournal(...)`, and MUST also
`return True` with nothing created — preserving that contract exactly, not "close enough."

---

## Steps

- [ ] 0. [model: sonnet] [effort: low] Scaffold the v2 execution journal
  - Files: `docs/feature-library-concurrency/PROGRESS.md` (new), `docs/feature-library-concurrency/DECISIONS.md` (new)
  - Depends on: nothing (first step).
  - Consumed by: every subsequent step (updated + committed in the SAME commit as each step, per the v2 journal protocol); the orchestrator's resume protocol if a session restarts mid-plan.
  - Details: Copy the exact structure of `docs/feature-extras/PROGRESS.md` (IMP-D19's journal — the cited pattern source): a "Step status" table (Step | Description | Status | Completing SHA | Tests | Notes), a `▶ NEXT ACTION` pointer, a "Resume protocol" section, and a "Blockers / human gates" section. Seed the step table with all steps from THIS plan (0-10), all `pending`. `DECISIONS.md` gets a stub with two headers — "Decision 1 — IMP-C24 approach" and "Decision 2 — IMP-D23 approach" — each marked `AWAITING USER RULING`, cross-referencing this PLAN.md's "Open Decisions" section for the options text (do not duplicate the full options table into DECISIONS.md; link to it).
  - Acceptance: both files exist, committed; PROGRESS.md's step table has 11 rows (0-10) all `pending`; `▶ NEXT ACTION` points at the checkpoint below.

  **🚦 CHECKPOINT — STOP HERE.** Before Step 1 begins, the orchestrator relays this plan's "Open
  Decisions" section (Decision 1: which IMP-C24 approach; Decision 2: which IMP-D23 approach — a
  recommendation is given for both, but this is the user's call, not the orchestrator's) to the user
  verbatim and does not proceed until both are explicitly ruled. Record the ruling in
  `DECISIONS.md` in the SAME commit that unblocks Step 1. The steps below are written against this
  plan's RECOMMENDED options — (a)+(b) hybrid for C24, (a)+(b) for D23 — with enough detail to adapt
  if the user rules differently (the options-analysis tables above give the shape of each alternative).

- [ ] 1. [model: fable] [effort: max] Write the failing regression test proving the IMP-C24 lost-update race
  - Files: `tests/test_library_concurrency.py` (new)
  - Depends on: Step 0 (journal exists to record the red-test evidence) and the Step-0 checkpoint (Decision 1 ruled).
  - Consumed by: Step 3 (this test's two cases flip from `xfail` to plain-passing — that flip IS Step 3's acceptance gate); Step 6 (full-suite verification references these tests by name).
  - Details: Using the `sandbox` fixture (dual-patches `mvcommon.LIBRARY_*` AND `main.LIBRARY_*` — see `docs/testing-strategy.md` §6.3, the binding hazard). Write TWO cases:
    1. **Primary, deterministic — direct load/mutate/save simulation** (no real subprocess/threading needed; this reproduces the EXACT verified mechanism):
       ```python
       import pytest
       import mvcommon

       @pytest.mark.xfail(strict=True, reason="IMP-C24: no lock yet — see docs/feature-library-concurrency/PLAN.md")
       def test_concurrent_save_loses_update_direct(sandbox):
           mvcommon.save_library({"tv-en-1994-friends-s03e11": {"uploaded": False, "status": "local_ready"}})
           lib_a = mvcommon.load_library()   # e.g. cmd_push's snapshot, taken at the START of a slow push
           lib_b = mvcommon.load_library()   # e.g. cmd_replace's snapshot, taken slightly later
           # B finishes FAST: mutate + save first.
           lib_b["tv-en-1994-friends-s03e11"]["status"] = "archived"
           mvcommon.save_library(lib_b)
           # A finishes SLOW: mutate (from its now-stale snapshot) + save last.
           lib_a["tv-en-1994-friends-s03e11"]["uploaded"] = True
           mvcommon.save_library(lib_a)
           final = mvcommon.load_library()
           # Both changes must survive. Today (2026-09), A's stale save clobbers B's status change.
           assert final["tv-en-1994-friends-s03e11"]["status"] == "archived"
           assert final["tv-en-1994-friends-s03e11"]["uploaded"] is True
       ```
    2. **Secondary, coarser — real `cmd_push` (mocked ADB via the `FakeAdb`/`mock_device` fixture) racing real `cmd_replace`** via two `threading.Thread`s synchronized with a `threading.Event` so `cmd_push`'s thread is paused right after its OWN `load_library()` call (monkeypatch a hook, or use the existing fixtures' natural pause points) while `cmd_replace` runs to completion in the other thread, then resume `cmd_push`. Also `@pytest.mark.xfail(strict=True, ...)`. This proves the fix holds at the real command level, not just the primitive level.
  - Constraints: never touch real `C:\Media`/`library_*.json` — sandbox only. Run `python -m pytest tests/test_library_concurrency.py -q` and paste BOTH xfail lines into `PROGRESS.md` as the credible "proven, not assumed" record this task requires.
  - Acceptance: both tests report `XFAIL` (not `ERROR`, not unexpectedly `XPASS`) against current `main.py`/`mvcommon.py`; `python -m pytest tests -q` and `python -m pytest tests/smoke -q` stay fully green (xfail never counts as a suite failure).

- [ ] 2. [model: fable] [effort: max] [candidates: 2] Build the cross-process lock primitive in `mvcommon.py`
  - Files: `mvcommon.py`, `tests/test_library_lock.py` (new)
  - Depends on: Step 1 (the regression test defines the exact contract this primitive must satisfy — both candidates must be validated against it, without yet wiring it into any `cmd_*`).
  - Consumed by: Steps 3-4 (every mutator migration calls this primitive, including `RollbackJournal.rollback()`/`recover_journal()`'s own migration in Step 4).
  - Details: Implement `mvcommon.library_lock(timeout=30, stale_after=600)` (context manager) + `LibraryLockTimeout` (exception) + `save_library_atomic(apply_fn)` (helper: `with library_lock(): fresh = load_library(); apply_fn(fresh); save_library(fresh)`). Do NOT wire this into any `cmd_*` function in this step — that is Steps 3-4. Cover: acquire/release; a second acquire blocks until the first releases (prove with a background thread); timeout raises `LibraryLockTimeout` (never silently proceeds); a stale lock file (mtime forced old via `os.utime`) is reclaimed; `save_library_atomic` provably re-reads fresh state (seed a change between two `save_library_atomic` calls from "different callers" and assert both land).
  - Judge criteria (ranked): (1) correctness — never silently proceeds past a live contended lock (this is THE property the whole fix depends on); (2) crash-safety — behavior when the lock file's holder process dies mid-hold (matches or improves on `fetch_session_lock`'s stale-reclaim story); (3) code size/auditability — this primitive will be read and trusted by every future contributor touching library I/O, so simplicity matters; (4) fit with this Windows-only codebase (no unnecessary cross-platform abstraction) and consistency with the existing `fetch_session_lock` precedent (reuse its proven idiom where the two approaches don't diverge).
  - Candidate approaches:
    - A: **File-based O_CREAT|O_EXCL exclusive-create lock**, directly mirroring `fetch_session_lock`'s existing mechanics (`mvcommon.py:459-540`) — atomic create as the mutex, PID+timestamp written into the lock file, manual `stale_after` reclaim, but with the deliberate divergence that a live contended timeout RAISES `LibraryLockTimeout` instead of reclaiming-and-proceeding. `[candidate-model: fable]`
    - B: **Native OS advisory locking** (`msvcrt.locking()` on Windows — this app is Windows-only, `env` confirms `win32`) directly on a designated lock file, held for the file descriptor's lifetime — the OS automatically releases the lock if the holding process dies (no `stale_after` heuristic needed at all, since a crashed process's lock is freed by the kernel), at the cost of being a genuinely different, less-precedented mechanism in this codebase (no existing `msvcrt` usage — `mvcommon.py:464`'s own docstring today says "no fcntl/msvcrt platform branches needed" for the *existing* lock, which candidate B would have to revisit). `[candidate-model: opus]`
  - Acceptance (both candidates): all new unit tests green; `python -m pytest tests -q` + `python -m pytest tests/smoke -q` green; the primitive is NOT yet called from any `cmd_*` (confirm via grep — this step is additive-only).

- [ ] 3. [model: fable] [effort: max] [candidates: 2] Migrate the HIGH-RISK long-I/O-holding mutators to the lock primitive: `cmd_prep`, `cmd_push`, `cmd_replace`, `cmd_restore`
  - Files: `main.py`
  - Depends on: Step 2 (the chosen/merged lock primitive); Step 1 (this step's acceptance gate is flipping both regression tests from `xfail` to passing).
  - Consumed by: Step 4 (the remaining mutators follow whichever refactor shape wins here — log it verbatim in `PROGRESS.md` as the convention Step 4 must replicate); Step 5 (confirms `cmd_push_group`/`cmd_replace_group`/`cmd_restore_group` inherit this step's fix for free via their per-item delegation, per `main.py:5433` — do not re-touch those functions in this step).
  - Details: For `cmd_prep` (`main.py:1038`, final save `:1180`), `cmd_push` (`:4735`, saves `:4952` + `:5115`), `cmd_replace` (`:5446`, save `:5562`), `cmd_restore` (`:6449`) — the long I/O (hashing, splitting, the ADB upload loop, the merge/verify) happens exactly as today; only the FINAL persist point changes. Do NOT touch `journal.rollback(library)` calls inside these functions in this step (that is Step 4's own explicit sub-bullet, kept isolated as its own reviewable, explicitly change-gated diff — see Step 4's Details).
  - Judge criteria (ranked): (1) correctness — Step 1's two regression tests must go from `xfail` to **passing**, no exceptions; (2) `test_rollback.py`'s full scenario matrix passes with **zero assertion changes needed** (proves PONR/journal-format/created-this-run-scoping are untouched); (3) minimal diff / blast radius per function (favor the candidate that changes the fewest lines around each existing `library[id][...] = ...` tail); (4) clarity of the established pattern for Step 4 to replicate at ~15 more call sites — the winning shape should be easy to explain in one paragraph.
  - Candidate approaches:
    - A: **Explicit changes-callback.** Each mutator computes its intended field writes into a small closure/dict (e.g. `lambda fresh: fresh[manual_id].update({"uploaded": True, "status": "onboarded"})`) and passes it directly to `save_library_atomic`. Every call site's tail is rewritten from `library[id][f]=v; save_library(library)` to `mvcommon.save_library_atomic(lambda fresh: ...)`. Maximally explicit about exactly what is persisted; more lines touched per site. `[candidate-model: fable]`
    - B: **Automatic dirty-diff.** At `load_library()` time, each mutator keeps a `deepcopy` of its own snapshot; at the final save point, instead of an explicit callback, a shared helper diffs the command's mutated `library[id]` dict against that original deep copy to discover exactly which keys THIS command changed, then applies only those diffed keys onto a freshly-reloaded dict before saving. Less code churn per call site (the body of each `cmd_*` function stays closer to today's shape), but more "invisible machinery," and needs explicit handling for list-field mutations (e.g. `season_map.children` appends) where a naive per-key diff could miss a nested change. `[candidate-model: opus]`
  - Acceptance (both candidates): Step 1's two tests are un-marked (remove `xfail`) IN THIS STEP and now PASS. `python -m pytest tests -q` + `python -m pytest tests/smoke -q` fully green. `python -m pytest tests/test_rollback.py -q` green with the scenario matrix's assertions UNCHANGED (only internal mechanics of the final happy-path save changed).

- [ ] 4. [model: fable] [effort: high] Migrate the remaining direct mutators to the winning Step-3 pattern
  - Files: `main.py`
  - Depends on: Step 3 (establishes the exact refactor shape to replicate — read Step 3's `PROGRESS.md` note before starting).
  - Consumed by: Step 5 (verifies group/orchestrator/web coverage); Step 6 (full verification).
  - Details: Apply the SAME shape to: `cmd_prep_season` (`:4147`), `cmd_set_search` (`:1195`/`:1202`), `cmd_set_tmdb` (`:1262`/`:1280`), `cmd_set_uploaded` (`:3553`/`:3564`), `cmd_rename_folder` (`:3620` — THREE save calls at `:3699`/`:3742`/`:4138`; re-audit their exact phase boundaries against current source first, then either fold them into one locked critical section per rename operation or wrap each independently — document the choice made and why in `PROGRESS.md`), `cmd_enrich_metadata` (`:2505`/`:2684` — network calls happen BEFORE the locked section, exactly like `cmd_push`'s ADB calls today), `cmd_fetch_trivia` (`:3266`/`:3549`), `cmd_repair_dummies` (`:6210`), `cmd_verify_library --fix-dummies` (`:6011`), `cmd_sort` (`:7029`/`:7077` — variable name is `sorted_library`, not `library`), `cmd_add_extras` (`:8100`). For `cmd_prep_push_rep`/`cmd_prep_push_rep_season`/`_enrich` variants (`:7298`/`:7355`/`:7762`/`:7882`): grep-CONFIRM (do not assume) that each has no independent `save_library()` call of its own beyond what its wrapped, now-fixed `cmd_prep`/`cmd_push`/`cmd_replace` calls already do. **Separate, explicitly-called-out sub-bullet — the one genuinely change-gated piece of this whole plan**: `RollbackJournal.rollback()` and `recover_journal()`'s own `save_library()` calls (`main.py:875`, `:881`, `:964` — pre-`cmd_recover`, part of the rollback machinery itself) route through `save_library_atomic` in "replay mode": apply the recorded inverses onto a freshly-loaded dict instead of the command's stale one, then save under the lock. Make this its own commit-worthy diff hunk (even though it lands in this same step) so it is easy to review in isolation against the change-gate ruling. Verify `test_rollback.py`'s full scenario matrix (including the durable-journal crash-recovery test and the R6/R7 scenarios) passes with its assertions UNCHANGED — this is the concrete proof that PONR placement, the D-6/D-7 created-this-run scoping, and the `RollbackHardFail` contract are untouched.
  - Acceptance: a fresh `grep -n "save_library(" main.py` shows every remaining call site is either (i) migrated in this step or Step 3, (ii) the `RollbackJournal`/`recover_journal` sub-bullet above (migrated in this step, reviewed as its own diff hunk), or (iii) inside one of the four orchestrators AND confirmed (not assumed) to have no independent save call. `python -m pytest tests -q` + `python -m pytest tests/smoke -q` + `python -m pytest tests/test_rollback.py -q` (assertions unchanged) all green.

- [ ] 5. [model: fable] [effort: high] Confirm group commands / `cmd_web` inherit the fix; add the cross-process web-vs-CLI proof test
  - Files: `main.py` (verification only, expect no changes), `webui/server.py` (verification only, expect no changes), `tests/test_web_concurrency.py` (new) or added to `tests/test_web_endpoints.py`
  - Depends on: Steps 3-4.
  - Consumed by: Step 6.
  - Details: Confirm `cmd_push_group` (`:5322`, delegates to `cmd_push(mid, ...)` per item at `:5433`), `cmd_replace_group` (`:5593`), `cmd_restore_group` (`:6702`) have no independent `save_library()` call beyond their now-fixed per-item delegates. Confirm `webui/server.py`'s `ACTION_TABLE` (`:210`/`:216-221`/`:223`/`:231`/`:236`) routes to the now-fixed `main.cmd_*` functions and that its single in-process FIFO `_worker_loop` (`:523`) needs no additional change — the cross-process protection lives below the web layer, in `mvcommon`, so `cmd_web` is automatically covered without special-casing it. Add ONE integration test using the `sandbox` fixture: simulate a `/api/action/replace`-shaped call (call `main.cmd_replace` directly, as the action table would) racing an in-process `cmd_push`-shaped mutation, using the same interleaving technique as Step 1's primary test, and assert both changes survive.
  - Acceptance: new test green; `python -m pytest tests -q` + `python -m pytest tests/smoke -q` green.

- [ ] 6. [model: opus] [effort: high] IMP-C24 final verification + docs
  - Files: `ARCHITECTURE.md` (§6.1, §12a), `docs/feature-library-concurrency/PROGRESS.md`
  - Depends on: Steps 1-5.
  - Consumed by: nothing further within C24; Step 10's reconciliation references this step's completion.
  - Details: Run and paste into `PROGRESS.md`: `python -m pytest tests -q` (expect **≥887 passed** — the documented baseline plus this plan's new tests; investigate any count that is LOWER, never just note it and move on), `python -m pytest tests/smoke -q` (expect **≥80 passed**), `python -m pytest tests/test_rollback.py -q` called out explicitly (proves the change-gated area's behavior is unchanged), and Step 1's two tests re-run explicitly to reconfirm green. Update `ARCHITECTURE.md` §6.1 (the four-library merge/split description) to mention the lock, and §12a with a short factual addendum in the SAME style/length as the existing IMP-R6/R7 and "Extras lifecycle" addenda (`ARCHITECTURE.md:2461-2484` is the precedent block to mirror) — state plainly that the lock is additive and does not change the journal format, PONR locations, or `RollbackHardFail` contract, and name the ONE genuine delta (`RollbackJournal.rollback()`/`recover_journal()` now route their own `save_library()` call through `save_library_atomic`).
  - Acceptance: all four verification commands' pass counts pasted into `PROGRESS.md`; `ARCHITECTURE.md` updated.

- [ ] 7. [model: opus] [effort: medium] IMP-D23 option (a) — new `cmd_push_rep` / `cmd_push_rep_season` commands
  - Files: `main.py` (new functions adjacent to `cmd_prep_push_rep`/`cmd_prep_push_rep_season` at `:7298`/`:7355`; CLI dispatcher), `tests/test_push_rep.py` (new)
  - Depends on: Step 6 (build on the now-lock-safe `cmd_push`/`cmd_replace`, sequencing choice for safety, not a hard code dependency).
  - Consumed by: Step 9 (verification); the already-written IMP-D23 improvement-task entry references these exact command names — keep them in sync if renamed.
  - Details: `def cmd_push_rep(manual_id, split_method=None, split_val=None, device_id=None, eager_rehash=False, temp_dir=None, extras=None, extras_size=None)` and `def cmd_push_rep_season(base_id, episode_range=None, split_method=None, split_val=None, device_id=None, eager_rehash=False, temp_dir=None, extras=None, extras_size=None)` — literally `cmd_prep_push_rep`/`cmd_prep_push_rep_season`'s existing bodies (`:7298`/`:7355`) minus the `cmd_prep`/`cmd_prep_season` call, reusing their EXISTING `RollbackHardFail` catch/resume-message logic verbatim (copy, do not invent new rollback wiring — matches the IMP-D22 precedent: "wrap the untouched autopilots, provably zero-diff, no new PONR"). GUARD: refuse with a clear message naming `prep`/`prep_push_rep` as the fix if the target id is not already in the library, or its `status` is not `local_ready` (i.e. not yet prepped, or already pushed/archived). Wire into the CLI dispatcher as `elif cmd == "push_rep":` / `"push_rep_season":`, mirroring the exact token-parsing loop at `main.py:9990-10046` (`SIZE_MB`/`SIZE_GB`/`COUNT`, `device`, `rehash`, `tempdir`, `--extras`/`--extras-size` tokens) minus the filepath-collection tail (no filepath argument for this command family). Do NOT touch `_season_resume_cmd`'s printed text in this step — Option (c) is explicitly deferred; the season resume message continues to print `prep_push_rep_season` exactly as today.
  - Acceptance: `python main.py push_rep <id> SIZE_GB 10` on an already-prepped sandbox entry pushes+replaces without ever calling `calculate_file_hash` (assert via a spy/mock). Refusal path tested for a not-yet-prepped id. New tests green; `python -m pytest tests -q` + smoke green.

- [ ] 8. [model: fable] [effort: high] IMP-D23 option (b) — opt-in `--assume-unchanged` early-skip in `cmd_prep` + `tech_spec.local_mtime`
  - Files: `main.py` (`cmd_prep`'s early-skip block `:1049-1080`; CLI token parsing at every `prep`/`prep_season`/`prep_push_rep*` call site; `ENTRY_TYPE_KEYS` comment near `:166`), `tests/test_entry_schema_guard.py`, `tests/test_prep_resume.py` (new) or added to an existing prep test file
  - Depends on: Step 6.
  - Consumed by: Step 9.
  - Details: Add a new `assume_unchanged=False` kwarg to `cmd_prep`, threaded from a new `--assume-unchanged`/`-assume-unchanged` CLI token (parsed the same way `--extras` is today at each call site). Add a THIRD early-skip check, in the SAME block as the two existing ones (`main.py:1049-1080`), BEFORE `journal = RollbackJournal(...)` is opened (`:1091`) — this is the one hard constraint from the dispatch's own "Interaction" note: the new skip path MUST create zero artifacts and open no journal, exactly like the two checks already there: `if assume_unchanged and entry.get("status") == "local_ready" and entry.get("tech_spec", {}).get("size_bytes") == os.path.getsize(filepath): print(...); return True`. Default (flag absent) behavior is BYTE-FOR-BYTE unchanged — pin this with a regression test, the same technique IMP-D20 used for its season-resume-message byte-identical pin. Separately (unconditionally, on every NORMAL — i.e. non-skipped — prep): store `tech_spec["local_mtime"] = os.path.getmtime(filepath)`, banking data for a future mtime+size strengthening; this does NOT change today's skip logic (still size-only, as specified above) and does NOT touch `ENTRY_TYPE_KEYS`'s `required` set (it is an optional nested field, exactly like the IMP-D19 `extras` block's precedent). Update the `ENTRY_TYPE_KEYS` explanatory comment (not the `required` sets) to mention it, mirroring IMP-D19 Step 8's "+10 comment lines only" pattern; add ONE round-trip case to `tests/test_entry_schema_guard.py` proving `local_mtime` survives save/load, mirroring `test_extras_block_round_trips_on_title_entries`.
  - Acceptance: (i) `--assume-unchanged` on a size-matching `local_ready` entry skips hashing (spy/mock `calculate_file_hash`, assert not called), returns `True`, opens no `RollbackJournal` (assert via spy); (ii) `--assume-unchanged` on a size-MISMATCHED entry falls through to a normal full re-hash (no silent trust — assert `calculate_file_hash` IS called); (iii) omitting the flag is byte-identical to today (regression pin); (iv) the new schema-guard round-trip case passes. `python -m pytest tests -q` + smoke green.

- [ ] 9. [model: opus] [effort: medium] IMP-D23 final verification + docs
  - Files: `ARCHITECTURE.md` (§5 entry points, §6.3 `tech_spec` field table, §10 workflow walkthrough), `README.md` (command list), `docs/feature-library-concurrency/PROGRESS.md`
  - Depends on: Steps 7-8.
  - Consumed by: Step 10.
  - Details: Run and paste into `PROGRESS.md`: `python -m pytest tests -q` (expect ≥887 + this plan's new tests) and `python -m pytest tests/smoke -q` (expect ≥80). Update `ARCHITECTURE.md` §5 (add `push_rep`/`push_rep_season` to the entry-points list, one line each, matching the existing `prep_push_rep` entry's format), §6.3 (add `tech_spec.local_mtime` to the documented field set), and §10 (one sentence in the workflow walkthrough noting the resume path — "a failed push after a successful prep resumes via `push_rep`/`push_rep_season`, or `prep --assume-unchanged`, without re-hashing"). Update `README.md`'s command list the same way IMP-D19/D22 did for their own additions (grep for where `prep_push_rep` is documented there and mirror the addition).
  - Acceptance: verification commands' pass counts pasted into `PROGRESS.md`; `ARCHITECTURE.md` and `README.md` updated.

- [ ] 10. [model: sonnet] [effort: low] Confirm-and-reconcile the IMP-C24 / IMP-D23 registrations
  - Files: `improvements/improvements_tierC.md`, `improvements/improvements_tierD.md`, `improvements/PRIORITY.md`, `docs/priority-graph/priority-graph.html`
  - Depends on: Steps 1-9 all complete and merged.
  - Consumed by: nothing further — this is the plan's final step.
  - Details: This planner already wrote the IMP-C24 and IMP-D23 task entries, the PRIORITY.md Band 0/Band 1 placement (with the 👉 SUGGESTED NEXT TASK pointer), and the priority-graph.html nodes/edges/footer as part of producing this plan — do NOT re-invent them. This step is a CONFIRM-AND-RECONCILE pass, run once implementation actually ships: flip each entry's `Status: pending` to `Status: done (<real branch name>, PR #<N>)`, matching the exact convention IMP-D19/D20/D21/D22 used; update `priority-graph.html`'s `p`/`s` fields for `C24` and `D23` from `high`/`decision` and `high`/`todo` to `done`/`done`; update `PRIORITY.md`'s "Last updated" date, the `👉 SUGGESTED NEXT TASK` pointer (move it to whatever is next — likely IMP-C22, which was never gated), and the Band 0 table (move the C24 row to the ✅-done rows, matching the existing C19/C20/C23 formatting). Verify the graph's arrays are still syntactically valid the same way this plan's author did (`node -e` extracting and evaluating the `TASKS`/`EDGES` arrays — see this plan's own verification of the C24/D23 additions for the exact technique).
  - Acceptance: three-file consistency confirmed (mirrors the IMP-D19 Step 13 precedent: "three-file consistency confirmed"); `node -e` syntax check passes on the graph's arrays.

## Consumer Impact Analysis

Required because Step 8 adds a new library field (`tech_spec.local_mtime`) — a shared data contract
per the planner's mandatory rule. `ENTRY_TYPE_KEYS` (`main.py:166`) defines only a `required` MINIMUM
key set per entry type; `tests/test_entry_schema_guard.py`'s own precedent (the IMP-D19 `extras`
block: `assert "extras" not in spec["required"]`) confirms optional additions are the sanctioned,
tested pattern. Searched for the risky consumer class specifically — code that iterates or exactly
compares an entry's FULL key set (the only way a new optional key could break something): `grep -n
"for .+ in entry(\.keys\(\))?:|set\(entry\.keys\(\)\)|list\(entry\.keys\(\)\)|entry\.keys\(\) ==" main.py`
→ **zero matches**. Every existing consumer of `tech_spec` reads it via `.get("tech_spec", {})` /
`.get("tech_spec")` chains and pulls specific NAMED sub-keys (`size_bytes`, `hdr`, `audio_channels`,
`duration_mins`, …) — confirmed by `grep -n "tech_spec" main.py` (18 matches, every one a `.get()`
chain or a named-key read, none a full-dict iteration or comparison).

| # | Site | Line(s) | Access | Verdict | Why |
|---|------|--------|--------|---------|-----|
| 1 | `cmd_prep` (writes the field) | 1171 | `entry_data["tech_spec"] = tech_specs` | safe | this IS the write site; adding a sibling key inside the dict `get_tech_specs()` already returns is additive |
| 2 | `_compact_tech` (UI-facing projection) | 8700-8725 | `tech_spec.get(key)` per named key | safe | picks named keys one at a time; an unknown extra key is silently ignored |
| 3 | `cmd_sort` `sort_key` | 7062 | `entry.get('tech_spec', {}).get('size_bytes', 0)` | safe | named-key `.get()` chain |
| 4 | `cmd_check` (size compare) | ~4343 | `entry.get("tech_spec")` | safe | named-key `.get()` |
| 5 | `cmd_restore`/`cmd_restore_group` (merged-size checks) | 6521, 6874 | `entry.get("tech_spec", {}).get("size_bytes", 0)` | safe | named-key `.get()` chain |
| 6 | `cmd_local_status` (size aggregation) | 7100, 7114 | `.get("tech_spec", {}).get("size_bytes", 0)` | safe | named-key `.get()` chain |
| 7 | `cmd_prep_season` (per-episode scan) | 3928 | `"tech_spec": get_tech_specs(file_path)` | safe | same write pattern as site 1 |
| 8 | reclaim-scan (`collect_reclaimable`-adjacent) | 8833-8835 | `entry.get("tech_spec") or {}`, then named `_compact_tech(tech_spec)` | safe | named-key projection |
| 9 | `test_entry_schema_guard.py` round-trip | (test file) | asserts `required` keys survive; does NOT assert an exact/closed key set | safe by design | this is the guard itself — Step 8 adds one new case, doesn't need to change the guard's own logic |

No consumer found that iterates or exactly compares an entry's or `tech_spec`'s full key set. Zero
`needs-fix` rows — the addition is safe by the same pattern the `extras` block already validated.

## Risks and edge cases

- **IMP-B1 tension**: `improvements_tierB.md` IMP-B1 ("cache library handle across `cmd_*` calls",
  pending, priority high, flagged risk "high... change-gated review required") proposes holding ONE
  library handle across an entire season batch — the OPPOSITE direction from this plan's
  window-narrowing fix. If B1 is ever implemented WITHOUT this plan's lock in place first, it would
  widen the race's blast radius, not shrink it. This plan's IMP-C24 registration cross-references B1
  explicitly; recommend B1 be re-scoped (or its own plan explicitly account for the lock) once this
  ships.
- **Windows-specific lock behavior**: the O_CREAT|O_EXCL idiom is already proven on this exact
  OS/filesystem by `fetch_session_lock` in production — low incremental platform risk for candidate A;
  candidate B (`msvcrt.locking`) is a genuinely new code path in this codebase and needs its own
  careful crash-simulation testing (kill a process mid-hold, confirm the OS actually releases it).
- **A crash while holding `library_lock`**: `stale_after=600s` self-heals a truly dead holder;
  `save_library`'s own per-file atomicity (`tempfile.mkstemp` + `os.replace`) means even a kill
  mid-write inside the lock cannot torn-write a file — worst case is a stale lock file sitting for up
  to 10 minutes, which is a availability cost, not a correctness one.
- **`cmd_rename_folder`'s three save points** (Step 4) may represent genuinely separate, individually
  resumable phases rather than one atomic operation — flagged as an implementation-time judgment call;
  the executor must read the function fully before deciding to fold them into one lock or keep three,
  and document the choice.
- **D23 option (b)'s heuristic caveat** — documented above; opt-in only, by design.
- **Timeout defaults** (`timeout=30`, `stale_after=600`) are this plan's proposal, not empirically
  tuned; see Open Decision 3.

## Verification

- `python -m pytest tests -q` (documented hazard: a bare `pytest -q` collects nothing in this repo —
  always use the `python -m pytest` form; baseline **887 passed**, expect this plan's new tests on top)
- `python -m pytest tests/test_rollback.py -q` (explicit — the change-gated scenario matrix must show
  zero assertion changes needed)
- `python -m pytest tests/test_library_concurrency.py -q` (explicit — both cases passing, not xfail)
- `python -m pytest tests/smoke -q` (baseline **80 passed**) — run this LAST, per the smoke-gate rule (`main.py` is touched)

## Out of scope

- IMP-C24 Option (c) merge-on-write — evaluated, not chosen (see Options Analysis).
- IMP-D23 Option (c) — editing the change-gated season resume-range message — evaluated, deferred.
- IMP-B1 (cache library handle across `cmd_*` calls) — explicitly NOT implemented here; flagged as
  incompatible-until-coordinated with this plan's lock (see Risks).
- Re-hashing or otherwise touching the user's live library; running `main.py` against real data;
  running the full test suite from within this planning session (per this dispatch's own constraints).
- IMP-C22, IMP-R10 — unrelated, pre-existing Band 0 items; untouched by this plan.
- Strengthening D23's `--assume-unchanged` heuristic to require `local_mtime` (not just size) — the
  field is banked for a future task; using it is not in this plan's scope (see Open Decision 4).

## Open Decisions

### Decision 1 — IMP-C24: which concurrency-fix approach? 🚦 CHANGE-GATE — per `CLAUDE.md`, this MUST be explicitly ruled by the user before Step 1 begins.
Full options table above. **Recommendation: (a)+(b) hybrid** — a fine-grained lock (`library_lock` +
`save_library_atomic`) wrapping a narrowed write window in every mutator, plus the same treatment for
`RollbackJournal.rollback()`/`recover_journal()`'s own `save_library()` calls (the one piece that
touches rollback internals). This is the only option that both closes the race completely AND
preserves the user's real parallel workflow. State explicitly: pure coarse-lock (a alone) or
detect-and-refuse (d) would work but would serialize or forbid the exact parallel workflow (slow push
+ fast reclaim-replace in a second shell) that motivated the incident in the first place — the user
should confirm whether that tradeoff is acceptable to them before it's ruled out.

### Decision 2 — IMP-D23: ship both (a) and (b), or just (a)?
**Recommendation: both** — (a) is low-risk and fully solves the verified scenario; (b) is a
complementary, strictly opt-in safety net with an honestly-stated heuristic limitation. If the user
would rather not carry the heuristic's caveat at all (even opt-in), ship (a) alone and drop Step 8
entirely — no other step depends on it.

### Decision 3 — Lock timeout defaults: `timeout=30` (live-contention wait before raising) and `stale_after=600` (dead-holder reclaim window) — reasonable, or does the user want different numbers?
The critical section this lock protects is JSON read+write only (millisecond-scale once the write
window is narrowed per Step 3), so 30 seconds of wait headroom is already generous before treating a
contended lock as a genuine problem worth surfacing via `LibraryLockTimeout`; 600 seconds before
reclaiming a stale lock is comfortably longer than any legitimate hold could ever last, while still
recovering from a crashed holder within 10 minutes rather than requiring manual intervention.
Recommendation: keep these defaults; revisit only if real-world use shows either number is wrong.

### Decision 4 — Should D23's `--assume-unchanged` ever become the default, or should mtime-strengthening (require size AND `local_mtime` match, not size alone) happen now instead of later?
**Recommendation: neither, for now.** Keep it opt-in indefinitely unless the user explicitly decides
otherwise — this matches the project's standing philosophy (memory: "no automatic quality decisions —
never auto-convert/drop a media track; detect, stop, and hand the user the command") applied to a
trust decision instead of a quality decision. `local_mtime` is banked in this plan so a future,
separately-decided strengthening is cheap to add without another schema change.
