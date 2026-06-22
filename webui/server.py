"""FastAPI app for the MediaVault local operations console (IMP-E12).

Candidate C — SERIALIZED SINGLE-WORKER QUEUE execution model.

Concurrency model
-----------------
Every mutating action is appended to one module-level ``queue.Queue`` and run by
exactly ONE long-lived daemon worker thread, strictly FIFO, one at a time. The
HTTP handler builds the job record, enqueues it, and returns ``202`` with a
``job_id`` immediately; the client polls ``GET /api/job/{job_id}``.

Two hard problems vanish *by construction* because nothing ever runs
concurrently:

1. **stdout capture is race-free.** Since only one action runs at a time, the
   worker can wrap each ``main.cmd_*`` in a single
   ``contextlib.redirect_stdout(_JobTee(job_id))`` to capture exactly that job's
   output (and republish it incrementally — IMP-E14 Phase 2). There is no second
   concurrent thread to bleed into ``sys.stdout``.
2. **device serialization is automatic.** ``push`` / ``replace`` can never run
   concurrently against the single shared ADB device, because the queue admits
   exactly one action at a time. The queue *is* the device lock.

The tradeoff: throughput. Long actions block later ones. For a single-user,
single-device local console this is the correct tradeoff — and it is the source
of this model's safety.

Import-safety
-------------
This module never imports ``uvicorn`` (that belongs to the ``cmd_web``
entrypoint added later). ``create_app()`` is TestClient-friendly and has no
network side effects. Worker startup is idempotent so repeated ``create_app()``
calls (as in tests) never spawn duplicate workers.
"""

import contextlib
import os
import queue
import re
import threading
import time

from fastapi import Body, FastAPI, HTTPException
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

import main

# ---------------------------------------------------------------------------
# Action allow-list (FIXED contract). Maps an action name to a callable that
# takes the validated request body and invokes the matching main.cmd_* with the
# correct positional/keyword arguments. The body shape is {id?, filepath?,
# confirm?, options?}; options is an optional dict of extra knobs.
# ---------------------------------------------------------------------------

def _run_prep(body):
    return main.cmd_prep(body.get("id"), body.get("filepath"))


def _run_push(body):
    opts = body.get("options") or {}
    return main.cmd_push(
        body.get("id"),
        split_method=opts.get("split_method"),
        split_val=opts.get("split_val"),
    )


def _run_replace(body):
    return main.cmd_replace(body.get("id"))


def _run_sort(body):
    return main.cmd_sort()


def _run_prep_push_rep(body):
    return main.cmd_prep_push_rep(body.get("id"), body.get("filepath"))


def _run_fetch_restore(body):
    # episodes is an optional season range (e.g. "1-3"); None means the whole entry.
    return main.cmd_fetch_restore(body.get("id"), (body.get("options") or {}).get("episodes"))


# name -> (runner, requires_confirm). Only "replace" is destructive and gated.
ACTION_TABLE = {
    "prep":          (_run_prep,          False),
    "push":          (_run_push,          False),
    "replace":       (_run_replace,       True),
    "sort":          (_run_sort,          False),
    "prep_push_rep": (_run_prep_push_rep, False),
    "fetch_restore": (_run_fetch_restore, False),
}

# Per-action success convention for the no-exception (else) branch of the worker.
#
# The five cmd_* functions do NOT share one return convention — verified by
# reading main.py:
#   * cmd_prep / cmd_push / cmd_replace : return True on success, and an
#     explicit `return False` on every HANDLED failure (never None). Internal
#     exceptions are caught and turned into `return False`; cmd_replace is the
#     sole one that re-raises (RollbackHardFail) past its point-of-no-return,
#     which the worker's BaseException branch already maps to "error".
#   * cmd_sort / cmd_prep_push_rep : fall off the end (return None) on SUCCESS,
#     but ALSO `return` None on a HANDLED FAILURE (cmd_sort on an empty library;
#     cmd_prep_push_rep on prep/push/replace failure — and it even swallows a
#     post-PONR RollbackHardFail into a bare `return`). For these two, the
#     return value ALONE cannot distinguish success from failure.
#
# Because some actions return None-on-failure, a blanket `ok = result is not
# False` is UNSAFE: it would mark a FAILED prep_push_rep (file pushed but NOT
# archived, or an original lost past the PONR) as "done" — a safety regression.
#
# So None counts as success ONLY for actions listed here. We list "sort" and
# "fetch_restore":
#   * "sort" is non-destructive and its single None-on-failure path is a
#     read-only "library empty" check that creates nothing, so None->done is
#     benign.
#   * "fetch_restore" (cmd_fetch_restore) also returns None on every path: it
#     prints ✅✅✅ / ⚠️ banners rather than returning a bool, so a no-exception
#     completion IS the success signal and the captured banner tells the user the
#     real outcome (full restore vs. a 0-item range vs. the only handled-failure
#     path, "ID not found", which is a read-only library check that creates
#     nothing — benign to mark done). It is also not destructive of local-only
#     data (fetch downloads; restore verifies and only quarantines on mismatch),
#     so requires_confirm=False above.
# "prep_push_rep" is deliberately EXCLUDED: its None is ambiguous, and the safe
# direction for a destructive autopilot is to NOT auto-mark it "done" — a
# successful run still prints "AUTO-PILOT COMPLETE" in the captured output and
# the subsequent reclaim refresh shows the archived state. (We do NOT change any
# cmd_* return value — main.py is intentionally left untouched.)
_NONE_IS_SUCCESS = {"sort", "fetch_restore"}

# ---------------------------------------------------------------------------
# Incremental progress parsing (IMP-E14 Phase 2).
#
# The progress UNIT is chunks-done / total_chunks, derived purely server-side by
# regex-scanning the captured stdout — mainfetch.py is NOT modified. The exact
# marker strings mainfetch emits (verified by reading mainfetch.py) are:
#
#   entry start : "🔹 PROCESSING: <filename> (<short_id>)"   (one per entry)
#   total       : "   > Detected Split File (N chunks)"       (one per SPLIT entry)
#   done        : "     ✅ MOVED: <filename>"                  (one per moved chunk)
#   entry end   : "   ✅ ENTRY COMPLETE."                      (entry finished)
#
# TOTAL rule: a season_map fetch processes several entries, so we count per
# PROCESSING block. A block that contains a "Detected Split File (N chunks)"
# line contributes N; a block with no such line is a single non-split file and
# contributes 1. DONE = the running count of "✅ MOVED:" lines.
#
# If no PROCESSING/MOVED markers appear at all (push / replace / sort), progress
# stays {done:0,total:0} while running and the worker promotes it to {1,1} on a
# clean terminal "done" (status-only degrade — never crash, never done>total).
# ---------------------------------------------------------------------------

# Anchored at line start (re.M). "🔹 PROCESSING:" tolerates the leading "\n"
# mainfetch prints because that newline ends the previous line, leaving this
# marker at the start of its own line. "Detected Split File (N chunks)" captures
# N. "✅ MOVED:" is counted by occurrence.
_RE_PROCESSING = re.compile(r"^🔹 PROCESSING:", re.M)
_RE_SPLIT = re.compile(r"^\s*> Detected Split File \((\d+) chunks\)", re.M)
_RE_MOVED = re.compile(r"^\s*✅ MOVED:", re.M)

# Cheap pre-filter: only re-parse the accumulated buffer when a freshly written
# chunk actually contains one of these literal substrings. This keeps writing
# large non-marker output O(1) per write instead of O(n) (avoids O(n²) overall),
# while guaranteeing every marker still triggers a recompute.
_PROGRESS_SIGNALS = ("🔹 PROCESSING:", "Detected Split File", "✅ MOVED:")


def _parse_progress(text):
    """Return {'done': d, 'total': t} parsed from accumulated captured stdout.

    Pure function over the full buffer (idempotent — re-running on the same text
    yields the same result). ``total`` sums N per split entry plus 1 per
    non-split PROCESSING block; ``done`` counts ✅ MOVED lines. ``done`` is
    clamped to never exceed ``total`` so a UI bar can never read >100%.
    """
    # Split the buffer into per-entry segments on the PROCESSING marker. The
    # text before the first marker is preamble (no entry) and is dropped.
    segments = _RE_PROCESSING.split(text)
    total = 0
    if len(segments) > 1:
        for seg in segments[1:]:
            split_matches = _RE_SPLIT.findall(seg)
            if split_matches:
                # Sum in case (defensively) more than one split line appears in
                # one entry block; normally there is exactly one.
                total += sum(int(n) for n in split_matches)
            else:
                # A PROCESSING block with no "Detected Split File" line is a
                # single non-split file -> 1 chunk.
                total += 1

    done = len(_RE_MOVED.findall(text))
    if total and done > total:
        done = total  # clamp: never let done exceed total
    return {"done": done, "total": total}


class _JobTee:
    """Write-through stdout target for one running job.

    Passed to ``contextlib.redirect_stdout`` in place of a plain ``StringIO``.
    Every ``print`` in the running action calls ``.write()``, which (a) appends
    to an internal buffer and (b) under ``JOBS_LOCK`` republishes the
    accumulated text to ``JOBS[job_id]["output"]`` and recomputes
    ``JOBS[job_id]["progress"]`` — so partial output and advancing progress are
    visible to ``GET /api/job/{id}`` polls while the job is still running.

    Thread-safety: the only writer is the single serialized worker thread; the
    only readers are request threads (which hold ``JOBS_LOCK`` in api_job). This
    tee therefore holds ``JOBS_LOCK`` solely for the in-memory dict mutation —
    it does NO I/O under the lock and NEVER calls ``_set_job`` (which would
    re-acquire the non-reentrant lock and deadlock); it updates the record
    fields directly. ``getvalue()`` returns the full buffer for the terminal
    capture so final output is never truncated.
    """

    def __init__(self, job_id):
        self._job_id = job_id
        self._parts = []          # accumulated chunks (joined lazily)
        self._cache = ""          # memoized join of self._parts

    # -- file-like protocol expected by redirect_stdout / print --------------

    def write(self, s):
        if not s:
            return 0
        # Coerce defensively; print() always passes str, but a stray bytes/obj
        # write must not crash the worker (it would surface as a job "error").
        if not isinstance(s, str):
            s = str(s)
        self._parts.append(s)
        self._cache = ""  # invalidate memoized join
        accumulated = self.getvalue()

        # Only re-parse when this chunk carries a progress marker; otherwise we
        # still publish the new output but skip the scan (keeps bulk output
        # cheap). Re-affirm the publish under the lock regardless so partial
        # output is always live.
        reparse = any(sig in s for sig in _PROGRESS_SIGNALS)
        progress = _parse_progress(accumulated) if reparse else None

        with JOBS_LOCK:
            record = JOBS.get(self._job_id)
            if record is not None:
                record["output"] = accumulated
                if progress is not None:
                    record["progress"] = progress
        return len(s)

    def flush(self):
        # No-op: there is no buffering layer to drain (each write already
        # publishes). Present because redirect_stdout targets are expected to be
        # flushable and some callers call sys.stdout.flush().
        return None

    def getvalue(self):
        if not self._cache:
            self._cache = "".join(self._parts)
        return self._cache


# ---------------------------------------------------------------------------
# Job registry + the single serialized worker.
#
# JOBS is the module-level job-record store, guarded by JOBS_LOCK. Every read or
# mutation of a job record holds the lock so the worker thread and the request
# threads never see a torn record.
#
# WORK_QUEUE carries (job_id, runner, body) tuples FIFO to the one worker.
# ---------------------------------------------------------------------------

JOBS = {}
JOBS_LOCK = threading.Lock()

WORK_QUEUE: "queue.Queue" = queue.Queue()

_WORKER_LOCK = threading.Lock()
_WORKER_THREAD = None  # the single daemon worker; created once via _ensure_worker()

_job_counter = 0  # monotonic id source, only mutated under JOBS_LOCK


def _next_job_id():
    """Allocate a unique job id. Caller must hold JOBS_LOCK."""
    global _job_counter
    _job_counter += 1
    return str(_job_counter)


def _set_job(job_id, **fields):
    """Atomically merge ``fields`` into a job record."""
    with JOBS_LOCK:
        JOBS[job_id].update(fields)


def _terminal_progress(text, ok):
    """Compute the FINAL progress dict for a job that has reached a terminal
    state, given the full captured ``text`` and whether it succeeded (``ok``).

    * Success with parsed chunks      -> {done: total, total: total} (fully done)
    * Success with NO chunk markers   -> {done: 1, total: 1} (status-only degrade
      so a UI bar reads 100% for push/replace/sort that print no chunk lines)
    * Failure                         -> last parsed progress, unmodified (honest
      "got this far"); ``done`` is still clamped so it can never exceed total.
    """
    progress = _parse_progress(text)
    if ok:
        total = progress["total"]
        if total > 0:
            return {"done": total, "total": total}
        # No chunk/processing markers at all -> promote to a completed 1/1.
        return {"done": 1, "total": 1}
    return progress


def _worker_loop():
    """The single worker. Pulls jobs FIFO and runs each main.cmd_* in-process,
    one at a time, capturing that job's stdout. It must NEVER die: every job is
    wrapped so that ANY outcome (False return, SystemExit from a corrupt-library
    load, RollbackHardFail, or any other exception) is recorded on the job and
    the loop continues serving the next job.
    """
    while True:
        try:
            job_id, name, runner, body = WORK_QUEUE.get()
        except Exception:
            # A failure pulling from the queue is not attributable to any job;
            # keep the worker alive and retry.
            continue

        try:
            _set_job(job_id, status="running")
            # Write-through tee: every print during the run publishes partial
            # output + recomputed progress to the job record (incremental
            # visibility). getvalue() still yields the COMPLETE buffer for the
            # terminal capture, so final output is never truncated.
            #
            # SAFE BY CONSTRUCTION: only one action runs at a time, so
            # redirecting the process-global stdout here cannot race another
            # action's output. This is the headline advantage of the serialized
            # single-worker model.
            buf = _JobTee(job_id)
            try:
                with contextlib.redirect_stdout(buf):
                    result = runner(body)
            except SystemExit as exc:
                # load_library() calls sys.exit(1) on a corrupt library. Treat a
                # non-zero/None exit as an error; a clean exit(0) as success.
                code = exc.code
                ok = code in (0, None)
                final = buf.getvalue()
                _set_job(
                    job_id,
                    status="done" if ok else "error",
                    output=final + ("" if ok else f"\n[exited with code {code}]"),
                    progress=_terminal_progress(final, ok),
                )
            except BaseException as exc:  # RollbackHardFail + anything else
                final = buf.getvalue()
                _set_job(
                    job_id,
                    status="error",
                    output=final + f"\n[{type(exc).__name__}] {exc}",
                    progress=_terminal_progress(final, False),
                )
            else:
                # No exception was raised — decide done vs error from the return
                # value, using each action's success convention (see
                # _NONE_IS_SUCCESS above for the per-command analysis):
                #   * An explicit `return False` is ALWAYS a handled failure ->
                #     "error" (covers prep/push/replace's validation failures).
                #   * Truthy (e.g. True) is success -> "done".
                #   * None is success ONLY for actions in _NONE_IS_SUCCESS (sort,
                #     which returns None on success); for any other action None is
                #     treated as failure, so a None-on-failure action (e.g.
                #     prep_push_rep) is never wrongly marked "done".
                if result is False:
                    ok = False
                elif result is None:
                    ok = name in _NONE_IS_SUCCESS
                else:
                    ok = True
                final = buf.getvalue()
                _set_job(
                    job_id,
                    status="done" if ok else "error",
                    output=final,
                    progress=_terminal_progress(final, ok),
                )
        except BaseException as exc:
            # Last-resort guard: even a failure in our own bookkeeping must not
            # kill the worker. Best-effort record, then keep serving.
            try:
                _set_job(job_id, status="error", output=f"[worker error] {exc}")
            except Exception:
                pass
        finally:
            try:
                WORK_QUEUE.task_done()
            except Exception:
                pass


def _ensure_worker():
    """Start the single daemon worker exactly once, idempotently. Repeated
    create_app() calls (e.g. across tests) reuse the same worker."""
    global _WORKER_THREAD
    with _WORKER_LOCK:
        if _WORKER_THREAD is not None and _WORKER_THREAD.is_alive():
            return
        t = threading.Thread(
            target=_worker_loop, name="mediavault-web-worker", daemon=True
        )
        t.start()
        _WORKER_THREAD = t


def _enqueue(name, runner, body):
    """Create a job record (status per the FIXED contract enum) and enqueue it
    for the single worker. Returns the new job id."""
    with JOBS_LOCK:
        job_id = _next_job_id()
        # Registered as "running" to honor the contract enum even while the job
        # waits FIFO in the queue. (A "queued" sub-state would be more precise,
        # but the contract's three values are running/done/error, so we stay
        # inside them.) The worker re-affirms "running" when it picks the job up.
        JOBS[job_id] = {
            "id": job_id,
            "name": name,
            "status": "running",
            "output": "",
            "started_at": time.time(),
            # ADDED (IMP-E14 Phase 2): always-present parsed progress, in chunk
            # units. Default {0,0} at enqueue; the worker's tee advances it live
            # and the terminal branch finalizes it. progress_unit documents the
            # unit for the UI without changing the existing field contract.
            "progress": {"done": 0, "total": 0},
            "progress_unit": "chunks",
        }
    WORK_QUEUE.put((job_id, name, runner, body))
    return job_id


# ---------------------------------------------------------------------------
# Library summary helper for GET /api/library.
# ---------------------------------------------------------------------------

def _category_of(mid):
    """Map a library id prefix to a category, matching save_library's split."""
    if mid.startswith("mov"):
        return "movies"
    if mid.startswith("tv"):
        return "series"
    if mid.startswith("ani"):
        return "anime"
    return "other"


def _library_summary():
    """Slim summary: counts by status, per category. Reuses the in-process
    library load — no disk walk. Non-physical entries (season_map /
    multi_ep_alias) are skipped so counts reflect real leaves only."""
    library = main.load_library()
    categories = {}
    totals_by_status = {}
    total = 0
    for mid, entry in library.items():
        if entry.get("type") in ("season_map", "multi_ep_alias"):
            continue
        cat = _category_of(mid)
        status = entry.get("status") or "unknown"
        cat_bucket = categories.setdefault(cat, {})
        cat_bucket[status] = cat_bucket.get(status, 0) + 1
        totals_by_status[status] = totals_by_status.get(status, 0) + 1
        total += 1
    return {
        "total": total,
        "by_status": totals_by_status,
        "by_category": categories,
    }


# ---------------------------------------------------------------------------
# App factory.
# ---------------------------------------------------------------------------

def create_app():
    """Build and return the FastAPI app. Import-safe and TestClient-friendly:
    no uvicorn, no network side effects. Starts the single serialized worker
    (idempotently) so enqueued actions are drained."""
    _ensure_worker()

    app = FastAPI(title="MediaVault Console", docs_url="/api/docs", redoc_url=None)

    @app.get("/api/reclaim")
    def api_reclaim():
        # Read-only: returns collect_reclaimable()'s contract dict verbatim.
        return main.collect_reclaimable()

    @app.get("/api/items")
    def api_items():
        # Read-only: returns items_payload()'s contract dict verbatim.
        return main.items_payload()

    @app.get("/api/library")
    def api_library():
        return _library_summary()

    @app.post("/api/action/{name}")
    def api_action(name: str, body: dict = Body(default=None)):
        entry = ACTION_TABLE.get(name)
        if entry is None:
            raise HTTPException(status_code=404, detail=f"Unknown action: {name}")
        runner, requires_confirm = entry
        body = body or {}
        if requires_confirm and body.get("confirm") is not True:
            # Destructive action (replace) requires explicit confirm. No
            # execution, no job created.
            raise HTTPException(
                status_code=409,
                detail=f"Action '{name}' is destructive; resend with confirm=true.",
            )
        job_id = _enqueue(name, runner, body)
        return JSONResponse(status_code=202, content={"job_id": job_id})

    @app.get("/api/job/{job_id}")
    def api_job(job_id: str):
        with JOBS_LOCK:
            record = JOBS.get(job_id)
            if record is None:
                raise HTTPException(status_code=404, detail=f"Unknown job: {job_id}")
            # Return a shallow copy taken under the lock so the client never
            # observes a torn record mid-update.
            return dict(record)

    # Mount static LAST so it can never shadow /api/*. Resolve the directory
    # relative to THIS file so it works regardless of CWD. html=True serves
    # index.html at "/". (Step 6 overwrites the placeholder index.html.)
    static_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
    app.mount("/", StaticFiles(directory=static_dir, html=True), name="static")

    return app
