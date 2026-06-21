"""FastAPI app for the MediaVault local operations console (IMP-E12).

Action-execution model — SUBPROCESS-PER-ACTION (candidate B)
------------------------------------------------------------
Every mutating action (`prep`/`push`/`replace`/`sort`/`prep_push_rep`) runs as a
CHILD PROCESS:

    [sys.executable, "<repo>/main.py", <name>, *mapped_args]

launched with ``subprocess.Popen(stdout=PIPE, stderr=STDOUT, text=True)`` and
``cwd=<repo root>`` so the child runs the existing CLI verb UNCHANGED against the
repo. A per-job daemon thread reads the child's pipe line-by-line into the job
record's ``output`` and, on process exit, sets ``status`` to ``"done"``
(returncode 0) or ``"error"`` (non-zero).

Why a child process? TRUE per-job output isolation: each child owns its own real
OS stdout, so there is no shared ``sys.stdout`` between concurrent actions and no
``redirect_stdout`` race to defeat. The cost (acknowledged): a Python interpreter
spawn + a full library reload per action, and the web layer no longer shares
in-memory state with the running action.

Process isolation does NOT solve DEVICE/library contention
----------------------------------------------------------
Subprocesses are isolated in MEMORY, but every action in the allow-list touches
the ONE physical ADB device and/or rewrites the same ``library_*.json`` (prep
writes files+library; push/replace/prep_push_rep drive the device + library;
sort reorganises files + rewrites the library). Two such children running at
once would corrupt the device transfer and race the library JSON. So a single
process-wide ``_DEVICE_LOCK`` serialises the launches: a job's worker thread
acquires the lock BEFORE spawning its child and holds it until the child exits.
The POST still returns 202 immediately; a second action simply waits (its job
record stays ``running``) until the device frees. Max one device-touching child
at any instant.

Import-safety: this module never imports or runs uvicorn — serving is owned by
``cmd_web`` in main.py (plan step 5). ``create_app()`` is side-effect-free beyond
``import main`` (itself read-only) and is TestClient-friendly.
"""

import os
import sys
import uuid
import threading
import subprocess
from datetime import datetime, timezone

from fastapi import FastAPI, Body
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

import main  # read-only data layer (CLI is guarded under `if __name__ == "__main__"`)


# ----------------------------------------------------------------------------
# Paths — resolved relative to THIS file so they work regardless of CWD.
# ----------------------------------------------------------------------------
_HERE = os.path.dirname(os.path.abspath(__file__))
_STATIC_DIR = os.path.join(_HERE, "static")
_REPO_ROOT = os.path.dirname(_HERE)          # parent of webui/  -> repo root
_MAIN_PY = os.path.join(_REPO_ROOT, "main.py")

# FIXED action allow-list (identical across candidates). Anything else -> 404.
_ALLOWED_ACTIONS = {"prep", "push", "replace", "sort", "prep_push_rep"}

# Default split for `push` / `prep_push_rep` when the body supplies no options,
# per the plan contract (`push <id> SIZE_GB 8`).
_DEFAULT_SPLIT_METHOD = "SIZE_GB"
_DEFAULT_SPLIT_VAL = "8"

# ----------------------------------------------------------------------------
# Job registry + device serialisation (module-level, lock-guarded).
# ----------------------------------------------------------------------------
# JOBS: job_id -> {id, name, status, output, started_at, [returncode], [error]}
_JOBS = {}
_JOBS_LOCK = threading.Lock()

# Serialises every device/library-mutating child. ALL allow-listed actions are
# mutating, so a single global gate == "max 1 concurrent action against the one
# ADB device + the one library JSON".
_DEVICE_LOCK = threading.Lock()


def _now_iso():
    return datetime.now(timezone.utc).isoformat()


def _job_set(job_id, **changes):
    """Atomically merge `changes` into a job record."""
    with _JOBS_LOCK:
        job = _JOBS.get(job_id)
        if job is not None:
            job.update(changes)


def _job_append_output(job_id, line):
    """Atomically append a line to a job record's accumulated output."""
    with _JOBS_LOCK:
        job = _JOBS.get(job_id)
        if job is not None:
            job["output"] += line


def _public_job(job):
    """Strip internal fields before returning a job over HTTP."""
    return {k: v for k, v in job.items() if not k.startswith("_")}


# ----------------------------------------------------------------------------
# POST body -> CLI argv mapping.
# ----------------------------------------------------------------------------
def _build_argv(name, body):
    """Map a validated action name + request body to a full subprocess argv.

    Returns the list ``[sys.executable, <main.py>, name, *mapped_args]``. Args
    are passed as DISCRETE list elements (no shell), so a filepath with spaces
    needs no quoting. Mirrors main.py's CLI dispatch for each verb.
    """
    body = body or {}
    options = body.get("options") or {}
    mid = body.get("id")
    filepath = body.get("filepath")

    argv = [sys.executable, _MAIN_PY, name]

    if name == "prep":
        # CLI: prep [id] [filepath]
        if mid:
            argv.append(str(mid))
        if filepath:
            argv.append(str(filepath))

    elif name == "push":
        # CLI: push [id] [SIZE_GB/SIZE_MB/COUNT] [val] [chunks <range>]
        if mid:
            argv.append(str(mid))
        method = options.get("split_method", _DEFAULT_SPLIT_METHOD)
        val = options.get("split_val", _DEFAULT_SPLIT_VAL)
        if method and val is not None:
            argv += [str(method), str(val)]
        chunks = options.get("chunks")
        if chunks:
            argv += ["chunks", str(chunks)]

    elif name == "replace":
        # CLI: replace [id]
        if mid:
            argv.append(str(mid))

    elif name == "sort":
        # CLI: sort  (no args)
        pass

    elif name == "prep_push_rep":
        # CLI: prep_push_rep [id] [filepath] [SIZE_GB/SIZE_MB/COUNT val]
        if mid:
            argv.append(str(mid))
        if filepath:
            argv.append(str(filepath))
        method = options.get("split_method")
        val = options.get("split_val")
        if method and val is not None:
            argv += [str(method), str(val)]

    return argv


# ----------------------------------------------------------------------------
# Subprocess worker: launch child, stream its stdout into the job record.
# ----------------------------------------------------------------------------
def _run_job(job_id, argv):
    """Acquire the device lock, run the child, stream output, finalise status.

    Runs on a daemon thread. Holds ``_DEVICE_LOCK`` for the whole child lifetime
    so device/library-mutating actions never overlap. Any failure to spawn is
    recorded on the job as an ``error`` (the server never crashes on a bad
    action).
    """
    with _DEVICE_LOCK:
        try:
            proc = subprocess.Popen(
                argv,
                cwd=_REPO_ROOT,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,   # merge stderr into the captured stream
                text=True,
                bufsize=1,                  # line-buffered text reads
            )
        except Exception as exc:  # e.g. interpreter/main.py missing
            _job_set(job_id, status="error",
                     output="❌ failed to launch action: %r\n" % (exc,),
                     error=repr(exc))
            return

        # Stream child stdout line-by-line until EOF (process exit / pipe close).
        try:
            if proc.stdout is not None:
                for line in proc.stdout:
                    _job_append_output(job_id, line)
        finally:
            proc.wait()
            if proc.stdout is not None:
                proc.stdout.close()

        rc = proc.returncode
        _job_set(job_id,
                 status="done" if rc == 0 else "error",
                 returncode=rc)


def _start_job(name, body):
    """Register a running job record and kick off its subprocess worker thread.

    Returns the new job_id. The worker may block on ``_DEVICE_LOCK`` (another
    action in flight); the POST handler does not wait for it.
    """
    job_id = uuid.uuid4().hex
    record = {
        "id": job_id,
        "name": name,
        "status": "running",
        "output": "",
        "started_at": _now_iso(),
    }
    with _JOBS_LOCK:
        _JOBS[job_id] = record

    argv = _build_argv(name, body)
    thread = threading.Thread(
        target=_run_job, args=(job_id, argv), daemon=True,
        name="mv-action-%s-%s" % (name, job_id[:8]),
    )
    thread.start()
    return job_id


# ----------------------------------------------------------------------------
# Library summary: counts by status per category (in-process, no disk walk).
# ----------------------------------------------------------------------------
def _library_summary():
    """Slim summary of the merged library: per-category status tallies.

    Reuses ``main.load_library()`` (the same in-process load the rest of the app
    uses — no disk walk). Category is derived from the id prefix, exactly as
    ``mvcommon.save_library`` splits the files (mov*/tv*/ani*). Non-physical
    entry types (season_map / multi_ep_alias) carry no status and are skipped,
    per the ENTRY_TYPE_KEYS contract.
    """
    library = main.load_library()

    categories = {"movies": {}, "series": {}, "anime": {}, "other": {}}
    totals = {"movies": 0, "series": 0, "anime": 0, "other": 0}

    for mid, entry in library.items():
        if not isinstance(entry, dict):
            continue
        if entry.get("type") in ("season_map", "multi_ep_alias"):
            continue  # virtual containers / aliases — no physical status to tally

        if mid.startswith("mov"):
            cat = "movies"
        elif mid.startswith("tv"):
            cat = "series"
        elif mid.startswith("ani"):
            cat = "anime"
        else:
            cat = "other"

        status = entry.get("status", "unknown")
        bucket = categories[cat]
        bucket[status] = bucket.get(status, 0) + 1
        totals[cat] += 1

    return {
        "categories": categories,
        "totals": totals,
        "total_entries": sum(totals.values()),
    }


# ----------------------------------------------------------------------------
# App factory.
# ----------------------------------------------------------------------------
def create_app():
    """Build and return the FastAPI app (no server is started here)."""
    app = FastAPI(title="MediaVault Console", version="1.0")

    @app.get("/api/reclaim")
    def api_reclaim():
        # Read-only, in-process scan. Returns the contract dict:
        # {items, total_reclaimable_bytes, total_reclaimable_human}.
        return main.collect_reclaimable()

    @app.get("/api/library")
    def api_library():
        return _library_summary()

    @app.post("/api/action/{name}")
    def api_action(name: str, body: dict = Body(default=None)):
        if name not in _ALLOWED_ACTIONS:
            return JSONResponse(
                status_code=404,
                content={"detail": "unknown action: %s" % name},
            )

        if name == "replace" and (body or {}).get("confirm") is not True:
            # Sole destructive action — refuse without explicit confirm.
            # JSON `true` deserialises to Python True; require exactly that.
            return JSONResponse(
                status_code=409,
                content={"detail": "replace requires confirm=true"},
            )

        job_id = _start_job(name, body)
        return JSONResponse(status_code=202, content={"job_id": job_id})

    @app.get("/api/job/{job_id}")
    def api_job(job_id: str):
        with _JOBS_LOCK:
            job = _JOBS.get(job_id)
            snapshot = dict(job) if job is not None else None
        if snapshot is None:
            return JSONResponse(
                status_code=404,
                content={"detail": "unknown job: %s" % job_id},
            )
        return _public_job(snapshot)

    # Mount static LAST so it can never shadow /api/*. html=True serves
    # index.html at "/". The directory must exist (StaticFiles raises otherwise)
    # — step 3 ships a placeholder index.html; step 6 overwrites it.
    app.mount("/", StaticFiles(directory=_STATIC_DIR, html=True), name="static")

    return app
