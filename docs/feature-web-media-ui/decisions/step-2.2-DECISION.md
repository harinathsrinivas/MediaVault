# Decision Card — Step 2.2 (IMP-E14 Phase 2): web job worker incremental progress

> **This is a RANK + RECOMMEND card. Nothing merges until you choose.** I am the judge; the final
> call (which mechanism, and whether to also add subprocess streaming) is yours. See **👉 Your choice**
> at the bottom.

## 1. Step

- **ID / title:** Step 2.2 (IMP-E14 Phase 2) — make `_worker_loop` in `webui/server.py` publish a
  running job's partial stdout incrementally + a parsed `progress {done,total}` (chunks-done /
  total_chunks), so `GET /api/job/{id}` polls see it advance. Job record gains `progress`;
  serialized-worker invariants preserved.
- **Judge criteria (scored per candidate):**
  1. **Thread-safety + serialized-worker contract** — all `JOBS` writes under `JOBS_LOCK`; no torn
     records / deadlock / leaked threads; `task_done()` in `finally`; `SystemExit`-then-`BaseException`
     ordering.
  2. **Faithfulness of progress to REAL chunk completion** (no fake timers) — and, given the subprocess
     finding below, which candidate can reflect real download progress IF paired with subprocess streaming.
  3. **Blast radius** — A/B are server-only; C edits `mainfetch.py`.
  4. **Robustness** — non-fetch actions degrade to status-only (never crash); final output complete.

---

### ⚠️ Architectural finding: the fetch subprocess's stdout is NOT captured (verified)

**This is the dominant fact for this step. Read it before the candidate scores.**

I independently verified, not just trusted the CRITIQUEs:

- `cmd_dispatch_fetch` (**main.py:3097–3111**) runs the actual download as
  `subprocess.run(["python", MAINFETCH_SCRIPT, "fetch", manual_id, ...])` **with no `stdout=`**. The
  child writes to the inherited OS file descriptor, **not** to the worker's in-process `sys.stdout`.
- The worker's `contextlib.redirect_stdout(...)` only captures **in-process** `sys.stdout` writes.
  So the download-phase prints live **only** in `mainfetch.py` and are **not captured** today:
  `🔹 PROCESSING:` (mainfetch.py:258), `Detected Split File (N chunks)` (mainfetch.py:271),
  `✅ MOVED:` (mainfetch.py:373), `✅ ENTRY COMPLETE` (mainfetch.py:390).
- I grepped `main.py` for `🔹 PROCESSING` and `✅ MOVED`: **zero occurrences.** The in-process
  RESTORE phase (`cmd_restore` ~main.py:2395+, `cmd_restore_group`) that **is** captured prints
  `--- RESTORING ---`, `> Detected Split File (N chunks).` (main.py:2334), `> Merging…`,
  `✅ SUCCESS:` — but **no `🔹 PROCESSING:` block and no per-chunk `✅ MOVED:` line.**

**What this means in plain terms — what each candidate actually yields:**

| Scenario | A (tee+regex) | B (flusher+regex) | C (mainfetch hook) |
|---|---|---|---|
| **Stub test** (in-process prints mainfetch-shaped markers, or fires the hook) | progress advances live ✓ | progress advances live ✓ | progress advances live ✓ |
| **REAL fetch_restore today** — download phase (the long part, the user's "bar growing while it downloads") | **0/0 → 1/1** (markers are in the uncaptured subprocess; A/B's parser sees none) | **0/0 → 1/1** (same) | **0/0 → 1/1** (hook set in worker process is **not inherited** by the child) |
| **REAL fetch_restore today** — in-process RESTORE phase | parser finds **no `🔹 PROCESSING:` / `✅ MOVED:`** → still total=0 → terminal `{1,1}` | same | hook not fired (restore isn't in the hooked driver) → terminal `{1,1}` |

So **all three show only status-only progress (0/0 → 1/1) for a real fetch today.** The headline ask —
a bar growing during the download — is delivered by **none of them as-is.** That requires a **separate,
small `main.py` change none of the candidates made:** stream the child's stdout in `cmd_dispatch_fetch`
(e.g. `subprocess.Popen(..., stdout=PIPE, text=True)` and re-`print()` each line so the worker's
`redirect_stdout` captures it).

- **With** that streaming change: **A and B start working for real** — the child's `🔹 PROCESSING:` /
  `Detected Split File` / `✅ MOVED:` lines flow into the captured buffer and their regex parsers advance.
- **C still would not** for fetch — its hook lives in the parent process; the child never sees it.
  C only goes live for fetch when fetch runs **in-process** (the IMP-F10/S2 direction).

---

## 2. Per candidate

### Candidate A — stdout tee + regex parse (server-only)
- **Approach:** Replaces the plain `StringIO` with a `_JobTee` (server.py:202); each `print` appends
  to a buffer and, under `JOBS_LOCK`, republishes the full text + a re-parsed `progress` via the pure
  `_parse_progress` (server.py:172). Parses `🔹 PROCESSING:` segments, summing `Detected Split File
  (N chunks)` per segment (1 if none), counting `✅ MOVED:` as done.
- **Files / diff:** `webui/server.py` only — **+197 / −13.** No `main.py` / `mainfetch.py` edits.
- **My test results:** `tests/test_web_endpoints.py` → **5 passed.** (Server-only; smoke not required,
  but no mainfetch risk.)
- **Self-critique highlights (confidence: high):** Honest about late-`total` for season_map
  (denominator grows as entries stream), coupling to exact mainfetch strings, and the `All files
  already exist` under-read corrected at terminal. Demonstrated a live 0→1→2 advance in a (deleted)
  stub proof.
- **Criteria scoring:**
  - **(1) Thread-safety:** ✓ Strong. Single short `JOBS_LOCK` hold per write, **no I/O under the lock**,
    deliberately does **not** call `_set_job` from inside `.write()` (would self-deadlock the
    non-reentrant lock) — mutates the record dict directly (server.py:246–251). SystemExit→BaseException
    order kept; `task_done()` in `finally`. No extra threads → nothing to leak.
  - **(2) Faithfulness:** △ Real chunk-count based (no timers), but text-parse is coupled to exact
    mainfetch wording; degrades silently to 0/0 if wording drifts. Finest granularity of the three
    (publishes on the exact `print`).
  - **(3) Blast radius:** ✓ Smallest surface — one file, no mainfetch/CLI risk.
  - **(4) Robustness:** ✓ Non-fetch → `{1,1}`; failure keeps honest partial; `done` clamped ≤ `total`;
    full buffer returned at terminal.
- **Real-fetch progress today:** status-only (0/0→1/1). **With `cmd_dispatch_fetch` streaming added:
  works (the parser then sees the child's real markers).**

### Candidate B — background flusher thread (server-only)
- **Approach:** `redirect_stdout` keeps the plain `StringIO` (capture byte-for-byte unchanged); a
  per-job daemon `_flush_loop` (server.py:209) snapshots+parses every ~0.4s under the lock, stopped via
  a `threading.Event` and joined by `_finalize_flusher` in each terminal branch **and again** in the
  outer `finally` (idempotent, bounded `join(timeout=2.0)`). Same `_parse_progress`.
- **Files / diff:** `webui/server.py` only — **+204 / −7.**
- **My test results:** `tests/test_web_endpoints.py` → **5 passed.** (CRITIQUE also reports smoke 58
  passed; not required for a server-only change but consistent.)
- **Self-critique highlights (confidence: high):** Honest about ~0.4s snapshot lag vs a tee, O(buffer)
  per-tick copy, and one daemon thread per job (mitigated by dual stop+join + bounded join). Proved
  no leaked flusher via `threading.enumerate()` back to baseline.
- **Criteria scoring:**
  - **(1) Thread-safety:** ✓ Strong, and the most explicit about lifecycle. Flusher snapshots/parses
    **outside** the lock, holds it only for the dict write; waits on the event (not bare sleep) so stop
    is prompt; joined **before** the terminal `_set_job` so a late tick can't clobber the final record.
    Dual-finalize prevents any cross-job thread bleed. The one residual: it adds a thread per job — more
    moving parts than A — but the no-leak path is belt-and-suspenders and tested.
  - **(2) Faithfulness:** △ Same regex parser as A → same coupling. Coarser granularity (~0.4s), invisible
    to a human-polling UI.
  - **(3) Blast radius:** ✓ One file; capture path literally unchanged (lowest behavioral-change risk to
    existing output capture).
  - **(4) Robustness:** ✓ Same degrade/clamp policy as A; authoritative terminal snapshot after join.
- **Real-fetch progress today:** status-only (0/0→1/1). **With `cmd_dispatch_fetch` streaming added:
  works** (same parser as A sees the streamed child markers).

### Candidate C — mainfetch `PROGRESS_HOOK` (edits mainfetch.py, no-op default)
- **Approach:** Adds `PROGRESS_HOOK=None` + no-op `_emit_progress` (mainfetch.py:47–63) + a read-only
  `_count_pending_chunks` (mainfetch.py:66). `cmd_fetch_route` computes total up front and passes an
  `on_chunk_done` callback to `fetch_single_entry`, fired right after each `✅ MOVED:` (mainfetch.py:444).
  The worker installs `mainfetch.PROGRESS_HOOK` for one job and **resets it to None in a `finally`**
  (server.py:328–330); exact `{done,total}` with no text parsing. Partial output via a `_LiveBuffer`
  (server.py:221) that mirrors each write.
- **Files / diff:** `mainfetch.py` **+91 / (part of) −0** and `webui/server.py` — total **+220 / −11**,
  **2 files.**
- **My test results:** `tests/test_web_endpoints.py` → **5 passed**; **`tests/smoke -q` → 58 passed**
  (mandatory because mainfetch was edited — confirmed green by me, not just the CRITIQUE).
- **Self-critique highlights (confidence: high for the mechanism):** Foregrounds the subprocess
  limitation itself as the central caveat; deliberately declined to bridge it with a stdout sentinel
  (would break the byte-unchanged CLI and become approach A). `total` is "pending at start".
- **Criteria scoring:**
  - **(1) Thread-safety:** ✓ All publishes under `JOBS_LOCK` via `_publish_progress` / `_finalize_progress`
    / `_LiveBuffer.write`; hook reset in inner `finally`; SystemExit→BaseException order kept; `task_done()`
    in outer `finally`. No extra threads. **One subtlety:** `PROGRESS_HOOK` is process-global module
    state; with the single serialized worker that's fine, but it is shared mutable state across a module
    boundary (slightly more coupling than A/B's purely-local state).
  - **(2) Faithfulness:** ✓ **The most accurate design in principle** — counts come from mainfetch's own
    `matched["status"]="done"` bookkeeping, immune to wording/emoji/`\r` drift. **BUT** it is the only
    one that **cannot** be made to work for a real fetch by adding subprocess streaming — the hook can't
    cross the process boundary. It goes live only when fetch becomes in-process (IMP-F10/S2).
  - **(3) Blast radius:** △ Largest — touches `mainfetch.py` (the load-bearing fetch path). Edits are
    surgical (one optional kwarg, two emit sites, two pure helpers, no-op default) and the smoke gate is
    green, but it is real risk A/B don't carry. `_count_pending_chunks` duplicates a little of
    `build_download_queue`'s pending rule (deliberately, to avoid that function's `os.makedirs` side
    effect).
  - **(4) Robustness:** ✓ No-op default proven; hook exceptions swallowed; non-fetch → `{1,1}`; clamp
    centralized; final output complete.
- **Real-fetch progress today:** status-only (0/0→1/1) — **hook not inherited by the child.**
  **With `cmd_dispatch_fetch` streaming added: STILL does not work for fetch** (wrong process). Becomes
  live only with an in-process fetch refactor. It is the right long-term primitive for that future.

---

## 3. Comparison table

| Criterion | A (tee+regex) | B (flusher+regex) | C (mainfetch hook) |
|---|:---:|:---:|:---:|
| (1) Thread-safety + serialized-worker contract | ✓ | ✓ | ✓ |
| (2) Faithfulness to real chunk completion (design) | △ (text-parse, coupled) | △ (text-parse, coupled) | ✓ (exact bookkeeping) |
| (3) Blast radius | ✓ (server only) | ✓ (server only) | △ (edits mainfetch.py) |
| (4) Robustness (degrade / never crash / full output) | ✓ | ✓ | ✓ |
| **Real-fetch DOWNLOAD progress TODAY?** | ✗ (0/0→1/1) | ✗ (0/0→1/1) | ✗ (0/0→1/1) |
| **Works if we add `cmd_dispatch_fetch` subprocess streaming?** | ✓ | ✓ | ✗ (wrong process) |
| `test_web_endpoints.py` | ✓ 5 passed | ✓ 5 passed | ✓ 5 passed |
| `smoke` (required only for C) | n/a | n/a | ✓ 58 passed |
| Incremental output granularity | finest (per print) | ~0.4s tick | per print |
| Diff size / files | +197/−13, 1 file | +204/−7, 1 file | +220/−11, 2 files |

Legend: ✓ good · △ acceptable-with-caveat · ✗ does not deliver.

---

## 4. Ranked recommendation (with the subprocess reality factored in)

**1st — Candidate A (tee + regex), paired with a small `cmd_dispatch_fetch` subprocess-streaming change.**
A is the simplest correct mechanism (one file, no extra threads, no mainfetch risk), gives the finest
incremental granularity, and — critically — **its regex parser becomes truthful for real downloads the
moment the child's stdout is streamed.** Because the markers it parses (`🔹 PROCESSING:`,
`Detected Split File (N chunks)`, `✅ MOVED:`) are exactly what mainfetch already prints, streaming them
into the captured buffer makes A's bar grow during the real download with no further parser work.

**2nd — Candidate B (flusher), also paired with subprocess streaming.** Functionally equivalent endpoint
behavior and the same "works once streamed" property as A, with arguably the most rigorous thread
lifecycle reasoning (capture path untouched; flusher joined before terminal write; dual idempotent
stop+join). It ranks below A only on simplicity: it introduces one daemon thread per job and a ~0.4s
latency to buy capture/publish isolation — more moving parts than A for a benefit (zero capture-path
overhead) that the bounded, small fetch output does not really need. If you value keeping the existing
`StringIO` capture byte-for-byte unchanged over minimizing thread count, B and A swap.

**3rd — Candidate C (mainfetch hook).** This is the **best long-term design and the most accurate in
principle** (exact counts, immune to text drift, and the exact primitive an in-process fetch will
consume). I rank it third **only** because of today's subprocess reality: it is the **one option that
adding subprocess streaming cannot rescue** for real fetches — the hook can't cross into the child — so
shipping C now buys the same status-only-today behavior as A/B while carrying the largest blast radius
(it edits the load-bearing `mainfetch.py`). Its payoff is deferred to the IMP-F10/S2 in-process-fetch
refactor.

**Should we ALSO add subprocess-stdout streaming to `cmd_dispatch_fetch`?**
**Yes — recommended, as a small companion change**, *if* you want the bar to actually grow during real
downloads now. It is a ~10-line `Popen` + line-iterate-and-`print` graft in `main.py`, and it is what
turns A (or B) from "status-only today" into "real download progress today." Note it is a `main.py`
change that **touches the fetch dispatch path**, so it deserves its own small review, but it does **not**
touch the auto-rollback machinery (it only re-prints child output). The honest **alternative** is to
**ship A (or B) now for the in-process + stub path and defer real download progress to IMP-F10/S2**
(in-process fetch), at which point C's hook would have been the cleaner primitive. There is no
auto-merge / synthesis here; "graft" = A's tee + a tiny streaming helper in `cmd_dispatch_fetch`.

---

## 5. 👉 Your choice

Two independent decisions — **nothing merges until you pick:**

**(i) Which progress MECHANISM:**
- **A — tee + regex** *(my pick: simplest correct, finest granularity, becomes real-download-truthful with streaming).*
- **B — background flusher** *(equivalent; pick if you prefer the capture path untouched over fewer threads).*
- **C — mainfetch hook** *(pick if you want the long-term exact primitive now and accept it stays status-only for fetch until in-process fetch lands; it is the only one streaming can't make live).*

**(ii) Whether to ALSO add the small `cmd_dispatch_fetch` subprocess-streaming change** so the bar
actually grows during a real download:
- **Yes** — pair it with A or B (NOT C — C's hook can't cross the process boundary). This is what
  delivers the user's "bar growing while it downloads" today.
- **No / later** — ship the chosen mechanism for the stub + in-process restore path now; defer real
  download progress to IMP-F10/S2.

**My recommendation: A + add the `cmd_dispatch_fetch` streaming change.** Confirm and I'll hand the
chosen candidate (and, if approved, a separate small streaming step) to the git-agent.

### Verification status
- All three: `tests/test_web_endpoints.py` → 5 passed (verified by me).
- C: `tests/smoke -q` → 58 passed (verified by me; required because it edits `mainfetch.py`).
- Subprocess finding independently confirmed: `subprocess.run` with no `stdout=` at main.py:3097–3111;
  `🔹 PROCESSING` / `✅ MOVED` exist only in `mainfetch.py`, zero in `main.py` (the captured in-process
  restore phase yields no per-chunk parseable lines for A/B).
- All three satisfy the step's acceptance criteria **for the stub / in-process path** and preserve the
  serialized-worker invariants. **None** delivers real-download progress without the additional
  `cmd_dispatch_fetch` streaming change (and C cannot even with it).
