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
   worker can wrap each ``main.cmd_*`` in a plain
   ``contextlib.redirect_stdout(io.StringIO())`` to capture exactly that job's
   output. There is no second concurrent thread to bleed into ``sys.stdout``.
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
import io
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
# Progress parsing (IMP-E14 phase 2).
#
# The progress UNIT is fetch *chunks*: chunks-done / total_chunks. We derive it
# by parsing mainfetch.py's stdout (mainfetch is NEVER modified) line by line:
#
#   * total  — sum over each "🔹 PROCESSING:" block. A block contributes the N
#     from its "   > Detected Split File (N chunks)" line if it has one; a block
#     with NO split line counts as 1 chunk (single-file entry). season_map runs
#     emit several PROCESSING blocks, so we sum across all of them.
#   * done   — count of "     ✅ MOVED: <filename>" lines.
#
# Non-fetch actions (push / replace / sort) print neither PROCESSING nor chunk
# lines, so total stays 0 and progress is status-only {done:0,total:0} while
# running. This function NEVER raises and NEVER reports done>total: when total is
# 0 we report {0,0} regardless of any stray MOVED line, and otherwise done is
# clamped to total. The trailing-period variant emitted by cmd_prep
# ("(N chunks).") is matched too, but such a line outside a PROCESSING block does
# not contribute (only chunk lines inside a PROCESSING block are summed).
# ---------------------------------------------------------------------------

# "🔹 PROCESSING:" — start of a per-entry fetch block (preceded by a newline in
# mainfetch; we match the bare marker anywhere on a line).
_PROC_MARKER = "🔹 PROCESSING:"
# "✅ MOVED:" — one moved/verified chunk file.
_MOVED_MARKER = "✅ MOVED:"
# Capture N from "Detected Split File (N chunks)" with or without a trailing dot.
_SPLIT_RE = re.compile(r"Detected Split File \((\d+) chunks\)")

# Default progress on every job record from enqueue onward (always present).
_DEFAULT_PROGRESS = {"done": 0, "total": 0}


def _parse_progress(text):
    """Parse {done,total} (chunk units) from captured mainfetch stdout.

    Pure + total-tolerant: accepts a partial buffer mid-run and never raises.
    Guarantees 0 <= done <= total. See the module comment above for the rules.
    """
    if not text:
        return {"done": 0, "total": 0}

    total = 0
    in_block = False          # are we inside a "🔹 PROCESSING:" block yet?
    block_has_split = False   # did the current block already declare its chunks?

    for line in text.splitlines():
        if _PROC_MARKER in line:
            # New entry block. The PREVIOUS block, if it never declared a split,
            # is a single-file entry worth 1 chunk.
            if in_block and not block_has_split:
                total += 1
            in_block = True
            block_has_split = False
            continue
        if in_block and not block_has_split:
            m = _SPLIT_RE.search(line)
            if m:
                total += int(m.group(1))
                block_has_split = True
    # Close out the final block (single-file entries contribute 1).
    if in_block and not block_has_split:
        total += 1

    if total == 0:
        # No fetch blocks at all (push/replace/sort) -> status-only progress.
        return {"done": 0, "total": 0}

    done = text.count(_MOVED_MARKER)
    if done > total:
        done = total  # invariant: never done>total (defensive clamp)
    return {"done": done, "total": total}


def _flush_loop(job_id, buf, stop_event, interval=0.4):
    """Background flusher: periodically snapshot ``buf`` and publish it (plus a
    re-parsed progress) onto the job record under JOBS_LOCK.

    Runs as a short-lived daemon thread for the duration of ONE job. It NEVER
    holds JOBS_LOCK across the sleep/getvalue (only around the dict update), so
    it cannot deadlock with request threads or the worker. ``stop_event`` is set
    by the worker in its finally; we wait on the event (not a bare sleep) so
    shutdown is prompt. We do NOT mutate ``status`` here — only the worker owns
    terminal-state transitions.
    """
    while not stop_event.is_set():
        # Snapshot OUTSIDE the lock (StringIO.getvalue is cheap; keep the
        # critical section to the dict write only).
        snapshot = buf.getvalue()
        progress = _parse_progress(snapshot)
        with JOBS_LOCK:
            record = JOBS.get(job_id)
            if record is not None:
                record["output"] = snapshot
                record["progress"] = progress
        # Wait on the event so a stop is observed immediately; the timeout is
        # the snapshot cadence. Never sleep while holding the lock.
        stop_event.wait(interval)


def _finalize_flusher(flusher, stop_event, buf):
    """Stop + join the flusher and return ONE authoritative final snapshot.

    Idempotent: safe to call from a terminal branch AND again from the outer
    finally (the second call sees the event already set and the thread already
    joined). ``buf`` may be None when called only to guarantee the thread is
    stopped (outer-finally cleanup) — then we return None.

    The join uses a bounded timeout so the worker can never hang on a wedged
    flusher; since the flusher waits on ``stop_event`` (not a bare sleep), it
    returns within one ``wait()`` wakeup, well under the timeout.
    """
    stop_event.set()
    if flusher is not None and flusher.is_alive():
        flusher.join(timeout=2.0)
    if buf is None:
        return None
    return buf.getvalue()


def _terminal_progress(final_output, ok):
    """Compute the progress dict to publish at a terminal state.

    On success: report completion. For a fetch (total>0) that is
    {done:total,total:total} (every chunk MOVED); the parser already yields that
    once all MOVED lines are present, but we re-clamp done=total defensively so a
    missed tail line can never leave a "done" job at 99%. For a non-fetch success
    (total==0, e.g. push/replace/sort) we report {done:1,total:1} so the UI shows
    a clean 100% on completion.

    On failure: keep the last truthful parse (do NOT fabricate completion) so an
    errored fetch shows how far it actually got. Guaranteed done<=total.
    """
    prog = _parse_progress(final_output)
    if not ok:
        return prog
    total = prog["total"]
    if total > 0:
        return {"done": total, "total": total}
    return {"done": 1, "total": 1}


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

        stop_event = threading.Event()
        flusher = None
        try:
            _set_job(job_id, status="running")
            buf = io.StringIO()
            # Start the background flusher: it snapshots `buf` every ~0.4s and
            # publishes partial output + a re-parsed progress onto the job
            # record. Capture itself (redirect_stdout below) is UNCHANGED — the
            # flusher only reads the buffer. It is a transient PUBLISHER thread,
            # not a second worker; the single-worker serialization is intact.
            flusher = threading.Thread(
                target=_flush_loop,
                args=(job_id, buf, stop_event),
                name=f"mediavault-web-flush-{job_id}",
                daemon=True,
            )
            flusher.start()
            # SAFE BY CONSTRUCTION: only one action runs at a time, so
            # redirecting the process-global stdout here cannot race another
            # action's output. This is the headline advantage of the serialized
            # single-worker model.
            try:
                with contextlib.redirect_stdout(buf):
                    result = runner(body)
            except SystemExit as exc:
                # load_library() calls sys.exit(1) on a corrupt library. Treat a
                # non-zero/None exit as an error; a clean exit(0) as success.
                code = exc.code
                ok = code in (0, None)
                # Stop the flusher and take ONE authoritative final snapshot so
                # terminal output is complete (no lost tail between the last tick
                # and now) and progress is consistent with that final output.
                final_output = _finalize_flusher(flusher, stop_event, buf)
                _set_job(
                    job_id,
                    status="done" if ok else "error",
                    output=final_output
                    + ("" if ok else f"\n[exited with code {code}]"),
                    progress=_terminal_progress(final_output, ok),
                )
            except BaseException as exc:  # RollbackHardFail + anything else
                final_output = _finalize_flusher(flusher, stop_event, buf)
                _set_job(
                    job_id,
                    status="error",
                    output=final_output + f"\n[{type(exc).__name__}] {exc}",
                    progress=_terminal_progress(final_output, False),
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
                final_output = _finalize_flusher(flusher, stop_event, buf)
                _set_job(
                    job_id,
                    status="done" if ok else "error",
                    output=final_output,
                    progress=_terminal_progress(final_output, ok),
                )
        except BaseException as exc:
            # Last-resort guard: even a failure in our own bookkeeping must not
            # kill the worker. Best-effort record, then keep serving.
            try:
                _set_job(job_id, status="error", output=f"[worker error] {exc}")
            except Exception:
                pass
        finally:
            # Belt-and-suspenders: GUARANTEE the flusher is stopped + joined for
            # this job, even if the body raised before its per-branch finalize
            # (e.g. _set_job itself threw). _finalize_flusher is idempotent, so a
            # second call after a normal terminal path is a cheap no-op. This is
            # what prevents a leaked flusher thread bleeding into the next job.
            try:
                _finalize_flusher(flusher, stop_event, None)
            except Exception:
                pass
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
            # progress is ALWAYS present from enqueue (default 0/0); the flusher
            # advances it while running and the terminal path finalizes it. Each
            # record gets its OWN dict (never the shared default) so concurrent
            # jobs can't alias each other's progress.
            "progress": dict(_DEFAULT_PROGRESS),
            "progress_unit": "chunks",
            "started_at": time.time(),
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
