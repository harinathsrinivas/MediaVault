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


# name -> (runner, requires_confirm). Only "replace" is destructive and gated.
ACTION_TABLE = {
    "prep":          (_run_prep,          False),
    "push":          (_run_push,          False),
    "replace":       (_run_replace,       True),
    "sort":          (_run_sort,          False),
    "prep_push_rep": (_run_prep_push_rep, False),
}

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
            job_id, runner, body = WORK_QUEUE.get()
        except Exception:
            # A failure pulling from the queue is not attributable to any job;
            # keep the worker alive and retry.
            continue

        try:
            _set_job(job_id, status="running")
            buf = io.StringIO()
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
                _set_job(
                    job_id,
                    status="done" if ok else "error",
                    output=buf.getvalue()
                    + ("" if ok else f"\n[exited with code {code}]"),
                )
            except BaseException as exc:  # RollbackHardFail + anything else
                _set_job(
                    job_id,
                    status="error",
                    output=buf.getvalue() + f"\n[{type(exc).__name__}] {exc}",
                )
            else:
                # cmd_* return falsey (typically False/None) on a handled
                # validation failure and truthy on success. `False`/`None` is
                # reported as an error so the UI can surface it.
                ok = result is not False and result is not None
                _set_job(
                    job_id,
                    status="done" if ok else "error",
                    output=buf.getvalue(),
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
        }
    WORK_QUEUE.put((job_id, runner, body))
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
