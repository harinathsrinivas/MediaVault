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

from fastapi import Body, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

import main
import mvcommon


# ---------------------------------------------------------------------------
# Minted-token auth + genuine-local-admin detection (IMP-E15).
#
# Access is NO LONGER a static shared secret. Two independent notions:
#
# 1. A request presents a VALID MINTED TOKEN via ANY ONE of three carriers
#    (the FIXED contract the device-side auth.js implements against):
#      a. the `mv_token` cookie          (set once by the SPA, sent automatically)
#      b. the `X-MediaVault-Token` header (fetch/XHR from the SPA)
#      c. the `?token=` query parameter   (a shareable link; also lets a fresh
#         browser bootstrap the cookie)
#    Each candidate is checked against the token store via
#    mvcommon.validate_token (sha256 + expiry); the first that validates wins.
#
# 2. The request is from the GENUINE LOCAL ADMIN (the owner's own Alienware
#    browser) — see _is_genuine_local_admin. This is the security hinge: it lets
#    the owner manage tokens and use the console with NO token, while a remote
#    tailnet peer proxied to 127.0.0.1 can NEVER be mistaken for it.
# ---------------------------------------------------------------------------

_COOKIE_NAME = "mv_token"
_HEADER_NAME = "X-MediaVault-Token"
_QUERY_NAME = "token"

# Loopback hosts that COULD be the local admin (necessary, not sufficient — see
# _is_genuine_local_admin, which additionally requires NO forwarding/identity
# headers). Reused by /api/open-folder so its localhost rule == the admin check.
_LOCALHOST_HOSTS = {"127.0.0.1", "::1", "localhost"}

# Headers that betray a PROXIED request. `tailscale serve` proxies remote tailnet
# peers to 127.0.0.1 but injects forwarding/identity headers; an upstream reverse
# proxy adds X-Forwarded-*. The presence of ANY of these means the request is NOT
# the genuine local admin, even if request.client.host reads as loopback. Matched
# case-insensitively against the request's header keys.
_PROXY_HEADER_NAMES = (
    "x-forwarded-for",
    "x-forwarded-host",
    "x-forwarded-proto",
    "forwarded",
    "tailscale-user-login",
    "tailscale-user-name",
)


def _is_genuine_local_admin(request: Request) -> bool:
    """True iff this request is the owner's OWN local browser (the admin).

    THE security hinge. ADMIN iff BOTH:
      * request.client.host is loopback (127.0.0.1 / ::1 / localhost), AND
      * NONE of the proxy/identity headers in _PROXY_HEADER_NAMES are present.

    Rationale: `tailscale serve` proxies REMOTE tailnet peers to 127.0.0.1 but
    adds forwarding/identity headers, so requiring loopback AND no-such-headers
    means a proxied remote request can never masquerade as the local admin. Any
    of those headers, OR a non-loopback host, => NOT admin (conservative)."""
    client = request.client
    host = client.host if client else None
    if host not in _LOCALHOST_HOSTS:
        return False
    # Reject if ANY proxy/identity header is present (case-insensitive).
    for name in _PROXY_HEADER_NAMES:
        if name in request.headers:  # Starlette Headers lookup is case-insensitive
            return False
    return True


def _request_token_is_valid(request: Request) -> bool:
    """True iff `request` carries a VALID minted token via cookie, header, or
    query. Each presented candidate is validated against the token store
    (mvcommon.validate_token: sha256 match + not expired); the first that
    validates wins. A missing carrier contributes nothing."""
    candidates = (
        request.cookies.get(_COOKIE_NAME),
        request.headers.get(_HEADER_NAME),
        request.query_params.get(_QUERY_NAME),
    )
    for presented in candidates:
        if presented and mvcommon.validate_token(presented):
            return True
    return False


def _is_authed(request: Request) -> bool:
    """True iff the request may use normal /api/* endpoints: it is the genuine
    local admin OR it presents a valid minted token."""
    return _is_genuine_local_admin(request) or _request_token_is_valid(request)


def _ttl_seconds_from_body(value):
    """Coerce a JSON `ttl_seconds` body value to int-seconds or None (never).

    Accepts None (-> never), an int, or an all-digits string. A non-positive /
    unparseable value is treated as None (never) so a malformed ttl never
    accidentally mints an already-dead token via this endpoint (the CLI path,
    which has explicit named windows, is the place to mint short-lived tokens)."""
    if value is None:
        return None
    if isinstance(value, bool):  # bool is an int subclass — reject explicitly
        return None
    if isinstance(value, int):
        return value if value > 0 else None
    if isinstance(value, str) and value.strip().isdigit():
        n = int(value.strip())
        return n if n > 0 else None
    return None


# ---------------------------------------------------------------------------
# Static file serving with revalidate-always caching (the blank-page fix).
#
# THE BUG (verified): the default StaticFiles emits only etag + last-modified and
# NO Cache-Control. A standalone-mode browser (notably iOS Safari "Add to Home
# Screen") may then serve a freely-cached, possibly STALE copy of a static file
# without revalidating. Because the UI is a no-build ES-module graph, serving one
# stale module against newer siblings makes an `import` resolve a symbol that no
# longer exists -> the whole module graph fails to evaluate -> the page renders
# blank (only background.js, an independent module, survives).
#
# THE FIX: stamp `Cache-Control: no-cache` on EVERY static response. "no-cache"
# does NOT mean "don't cache" — it means "you may store it, but you MUST
# revalidate with the origin before reuse". Starlette's FileResponse already sends
# a strong etag + last-modified, so an unchanged file still returns 304 Not
# Modified (cheap), while a CHANGED file is always re-fetched. A mismatched/stale
# module set can therefore never be served again.
#
# WHY a StaticFiles subclass (not an app-wide middleware): this is the surgical
# option. It touches ONLY responses produced by the static mount; every /api/*
# route is provably unaffected because they never flow through StaticFiles. The
# mount stays LAST (after all /api/* routes), so route ordering is unchanged.
# `file_response` is the single funnel StaticFiles uses to build a file response
# (200 and 304 alike), so adding the header there covers html/js/css/manifest/
# icons in one place.
# ---------------------------------------------------------------------------

class _NoCacheStaticFiles(StaticFiles):
    """StaticFiles that forces revalidation on every served file.

    Overrides ``file_response`` (Starlette's single construction point for both
    200 and 304 static responses) to set ``Cache-Control: no-cache`` while
    leaving the etag/last-modified validators intact, so unchanged files still
    304 but a changed/stale file is never served from a blind cache.
    """

    def file_response(self, *args, **kwargs):
        resp = super().file_response(*args, **kwargs)
        # Revalidate-always: keep the file cacheable but require an etag/
        # last-modified check before reuse. Set (not setdefault) so we win over
        # any default Starlette may add in future versions.
        resp.headers["Cache-Control"] = "no-cache"
        return resp

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


# ---------------------------------------------------------------------------
# DEMO / SAFE-mode simulator (IMP-E14).
#
# When create_app(demo=True) is used (the `python main.py web --demo` review
# build), the action path NEVER invokes a real ACTION_TABLE runner. Instead the
# HTTP handler enqueues THIS simulator in place of the real runner. It is the
# SAME callable for EVERY allow-listed action, so there is no action name —
# known or otherwise — that can route to a real main.cmd_* in demo mode.
#
# SAFETY (provable by inspection of this function):
#   * It references NO main.cmd_* — it cannot mutate the library.
#   * It spawns NO subprocess and drives NO Selenium / browser.
#   * It only prints synthetic lines and sleeps briefly.
# The synthetic lines deliberately match the EXACT mainfetch markers the
# server-side progress parser scans for (🔹 PROCESSING / Detected Split File /
# ✅ MOVED) so the real progress dict + the UI border animate identically to a
# real fetch — letting a reviewer exercise the whole flow with zero risk.
#
# Return value: True (truthy) so the worker's success convention marks the job
# "done" for EVERY action name — including ones whose real return is None-on-
# failure (replace / prep_push_rep). The real (non-demo) path and the per-action
# _NONE_IS_SUCCESS convention are untouched; this just keeps a simulated job
# from ever being mis-scored as "error".

# Demo simulator pacing. A short per-chunk sleep so a polling client observes
# progress.done advance 0 -> total across separate /api/job/{id} reads (rather
# than seeing the job already complete on the first poll). 4 chunks * ~0.4s is
# well under any test/poll timeout.
_DEMO_CHUNK_DELAY_S = 0.4
_DEMO_CHUNKS = 4


def _run_demo_sim(body):
    """SIMULATED action runner used for ALL actions in demo mode. Emits a clear
    banner + mainfetch-style progress markers (so the live parser/border animate)
    and returns True. Never touches the library, a subprocess, or Selenium."""
    print("⚠️  DEMO MODE — no real command executed (simulated).")
    # A synthetic id for the PROCESSING block; prefer the request's id so the
    # output reads coherently, else a placeholder.
    sim_id = (body or {}).get("id") or "demo-entry"
    print(f"🔹 PROCESSING: {sim_id}")
    print(f"   > Detected Split File ({_DEMO_CHUNKS} chunks)")
    for n in range(1, _DEMO_CHUNKS + 1):
        # Sleep BEFORE publishing each MOVED line so polls see done climb.
        time.sleep(_DEMO_CHUNK_DELAY_S)
        print(f"     ✅ MOVED: chunk{n:03d}")
    print("   ✅ ENTRY COMPLETE.")
    return True

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

def create_app(demo=False):
    """Build and return the FastAPI app. Import-safe and TestClient-friendly:
    no uvicorn, no network side effects. Starts the single serialized worker
    (idempotently) so enqueued actions are drained.

    demo=True serves a SAFE review build (IMP-E14): the action path SIMULATES
    every allow-listed action via _run_demo_sim instead of invoking the real
    ACTION_TABLE runner, so no main.cmd_*/Selenium/library mutation can ever run.
    All other behavior (the read-only routes, the 404 on unknown actions, the
    confirm-gate that 409s a replace without confirm, the 202/{job_id}/poll
    contract, and the serialized-worker invariants) is identical to the default.
    The default (demo=False) path is byte-unchanged: real runners via
    ACTION_TABLE."""
    demo = bool(demo)
    _ensure_worker()

    app = FastAPI(title="MediaVault Console", docs_url="/api/docs", redoc_url=None)
    # Expose the flag on app.state for introspection/testing; the closure var
    # `demo` is what the routes below actually read.
    app.state.demo = demo

    # -- Minted-token auth for normal /api/* (IMP-E15) -----------------------
    # The console is destructive, so non-read-only AND read-only /api/* alike are
    # gated. Implemented as path-based HTTP middleware (not per-route deps) so it
    # provably covers EVERY current and future /api/* route without decorating
    # each one, while STATIC files (the SPA shell) — not under /api/ — are always
    # served so the page can load to prompt for a token.
    #
    # AUTH RULE (secure-by-default — ALWAYS enforced, NO empty-store escape):
    #   A normal /api/* request is allowed IFF it is the GENUINE-LOCAL ADMIN
    #   (_is_genuine_local_admin: loopback host AND no proxy/identity headers) OR
    #   it presents a VALID, non-expired minted token (cookie / X-MediaVault-Token
    #   / ?token=). Anything else -> 401. This is _is_authed, evaluated on EVERY
    #   request regardless of how many tokens exist.
    #
    #   Why no "empty store -> auth off" escape (the security fix): binding
    #   0.0.0.0 with an EMPTY store must NOT leave the destructive console open to
    #   the whole LAN/tailnet until the first token is minted. Under the
    #   always-enforce rule an empty store means:
    #     * the genuine-local ADMIN still has full, token-free access (frictionless
    #       local/dev — the owner's own browser is always allowed), but
    #     * ANY remote (non-admin) request gets 401 — no valid token can exist yet,
    #       so remote is LOCKED until the owner mints + shares a token.
    #   That matches the model ("remote devices must always present a token") and
    #   closes the unauthenticated-exposure window.
    #
    # The token store is read at REQUEST time via mvcommon (binding-safe: a
    # monkeypatch of mvcommon.validate_token / MVTOKENS_PATH is honoured); when the
    # request is the genuine-local admin, _is_authed short-circuits and the store
    # is never consulted.
    #
    # Two endpoints are EXEMPT from this guard (handled before the gate):
    #   * GET /api/whoami — must be reachable with no auth so the SPA can learn
    #     whether to show the admin panel / prompt for a token.
    #   * the admin-only token-management endpoints (/api/token*) enforce their
    #     OWN stricter genuine-local-admin check (403, not 401) in their handlers.
    @app.middleware("http")
    async def _api_auth_guard(request: Request, call_next):
        path = request.url.path
        if path.startswith("/api/") and path != "/api/whoami":
            # Admin-only token endpoints do their own 403 gate; don't double-gate
            # them with a 401 here (a non-admin with a valid token must still get
            # 403 from those handlers, not slip through this 401 layer).
            if not path.startswith("/api/token"):
                if not _is_authed(request):
                    return JSONResponse(
                        status_code=401,
                        content={"detail": "Access token required or expired"},
                    )
        return await call_next(request)

    # -- /api/whoami (NO auth — always reachable) ----------------------------
    # The SPA reads this on load to decide whether to render the admin Access
    # panel (is_admin) and whether it already has access (authed). Never gated.
    @app.get("/api/whoami")
    def api_whoami(request: Request):
        return {
            "is_admin": _is_genuine_local_admin(request),
            "authed": _is_authed(request),
        }

    # -- Token management (ADMIN-only: genuine-local browser) ----------------
    # Mint / list / revoke. Each requires the genuine-local admin (mirrors the
    # /api/open-folder localhost rule) -> 403 otherwise. NEVER returns a stored
    # hash; the raw token is returned ONLY by POST (mint), shown once.
    def _require_admin(request: Request):
        if not _is_genuine_local_admin(request):
            raise HTTPException(
                status_code=403,
                detail="Token management is only allowed from the local (Alienware) browser.",
            )

    @app.post("/api/token")
    def api_token_create(request: Request, body: dict = Body(default=None)):
        _require_admin(request)
        body = body or {}
        label = body.get("label") or ""
        ttl = _ttl_seconds_from_body(body.get("ttl_seconds"))
        token_id, raw, expires_at = mvcommon.mint_token(label, ttl)
        return {
            "id": token_id,
            "label": str(label).strip(),
            "token": raw,  # raw shown ONCE — never stored, never returned again
            "expires_at": expires_at,
        }

    @app.get("/api/token")
    def api_token_list(request: Request):
        _require_admin(request)
        return {"tokens": mvcommon.list_tokens()}

    @app.delete("/api/token/{token_id}")
    def api_token_revoke(request: Request, token_id: str):
        _require_admin(request)
        mvcommon.revoke_token(token_id)  # idempotent: ok regardless of prior state
        return {"ok": True}

    @app.get("/api/mode")
    def api_mode():
        # Tiny capability probe the frontend reads on load to decide whether to
        # show the persistent DEMO banner.
        return {"demo": demo}

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
            # Unknown action: 404 in BOTH modes. In demo this also means there is
            # no name (allow-listed or not) that can reach a real runner — the
            # name must be in ACTION_TABLE to get past here, and even then demo
            # swaps in the simulator below.
            raise HTTPException(status_code=404, detail=f"Unknown action: {name}")
        runner, requires_confirm = entry
        body = body or {}
        if requires_confirm and body.get("confirm") is not True:
            # Destructive action (replace) requires explicit confirm. No
            # execution, no job created. This gate is IDENTICAL in demo: a
            # `replace` without confirm still 409s (then, with confirm, only
            # SIMULATES — it never deletes an original).
            raise HTTPException(
                status_code=409,
                detail=f"Action '{name}' is destructive; resend with confirm=true.",
            )
        # SAFETY: in demo mode, DISCARD the real ACTION_TABLE runner and enqueue
        # the simulator instead. The real runner never reaches the queue/worker,
        # so no main.cmd_* can execute. The name/confirm gating above is
        # unchanged, so the 404/409 contract is preserved.
        effective_runner = _run_demo_sim if demo else runner
        job_id = _enqueue(name, effective_runner, body)
        return JSONResponse(status_code=202, content={"job_id": job_id})

    # -- Folder/tree routes (IMP-E14 polish) --------------------------------
    # Read-only structure mirroring on-disk folders, a folder-image streamer,
    # and a localhost-only Explorer opener. Registered BEFORE the StaticFiles
    # mount below so "/" cannot shadow them.

    @app.get("/api/tree")
    def api_tree():
        # Read-only: returns build_tree()'s contract dict verbatim
        # ({"roots": {movies, series, anime, other}}). Real folder sizes +
        # has_image come from a metadata-only os.scandir walk in main.build_tree.
        return main.build_tree()

    @app.get("/api/folder-image")
    def api_folder_image(path: str):
        # Stream poster.jpg (preferred) or fanart.jpg from the folder or first
        # descendant. SECURITY: the resolved file MUST be under LOCAL_ROOT and
        # its basename MUST be exactly poster.jpg/fanart.jpg (case-insensitive).
        if not path:
            raise HTTPException(status_code=400, detail="Missing path")
        # The requested folder must itself be under LOCAL_ROOT (reject traversal
        # before even scanning).
        if not main._is_within_local_root(path):
            raise HTTPException(status_code=403, detail="Path is outside the media root")
        image_path = main.find_folder_image(path)
        if not image_path:
            raise HTTPException(status_code=404, detail="No folder image found")
        # Re-assert the FINAL resolved file: under LOCAL_ROOT and an allowed name.
        real = os.path.realpath(image_path)
        if not main._is_within_local_root(real):
            raise HTTPException(status_code=403, detail="Resolved image outside the media root")
        if os.path.basename(real).lower() not in ("poster.jpg", "fanart.jpg"):
            raise HTTPException(status_code=403, detail="Not an allowed image filename")
        return FileResponse(real, media_type="image/jpeg")

    @app.get("/api/media-image/{id}")
    def api_media_image(id: str, kind: str = "poster"):
        # Resolve+stream a library ENTRY's artwork (poster/fanart) by id, applying
        # the season-inheritance walk (own folder -> season_map folder -> nearest
        # {tmdb-…} show folder). READ-ONLY, path-only.
        #
        # SECURITY: this route is under /api/*, so the always-on auth middleware
        # (IMP-E15) already gates it exactly like every other /api route — the SPA
        # <img> sends the mv_token cookie; the genuine-local admin is exempt. No
        # second auth mechanism is added here.
        #
        # The id only INDEXES the library dict (a crafted id is just a missing key
        # -> None -> 404); resolve_artwork_path derives every candidate from the
        # entry's stored folder_path (+ real ancestors), never from client input,
        # and returns ONLY a poster.jpg/fanart.jpg that realpath-resolves under
        # LOCAL_ROOT. A not-found / out-of-library / no-artwork id -> 404 so the
        # SPA falls back to its gradient placeholder.
        kind = kind if kind in ("poster", "fanart") else "poster"
        library = main.load_library()
        path = main.resolve_artwork_path(library, id, kind=kind)
        if not path:
            raise HTTPException(status_code=404, detail="No artwork for this id")
        # Defence-in-depth: re-assert the resolved file is under LOCAL_ROOT with an
        # allowed basename before streaming (resolve_artwork_path already enforces
        # this; mirror /api/folder-image's final re-check).
        real = os.path.realpath(path)
        if not main._is_within_local_root(real):
            raise HTTPException(status_code=404, detail="Resolved image outside the media root")
        if os.path.basename(real).lower() not in ("poster.jpg", "fanart.jpg"):
            raise HTTPException(status_code=404, detail="Not an allowed image filename")
        return FileResponse(real, media_type="image/jpeg")

    @app.post("/api/open-folder")
    def api_open_folder(request: Request, body: dict = Body(default=None)):
        # Open a folder in Windows Explorer — GENUINE-LOCAL ADMIN ONLY. Over
        # Tailscale/any remote peer this would open Explorer on the SERVER, so it
        # is rejected. Reuses the SAME genuine-local-admin check as token
        # management (loopback host AND no proxy/identity headers), so a valid
        # minted token can never widen this to a remote peer.
        if not _is_genuine_local_admin(request):
            raise HTTPException(
                status_code=403,
                detail="Folder open is only allowed from the local browser.",
            )
        body = body or {}
        path = body.get("path")
        if not path or not isinstance(path, str):
            raise HTTPException(status_code=400, detail="Missing path")
        if not main._is_within_local_root(path):
            raise HTTPException(status_code=403, detail="Path is outside the media root")
        folder = os.path.realpath(path)
        if not os.path.isdir(folder):
            raise HTTPException(status_code=400, detail="Not an existing directory")
        # DEMO mode: simulate — never touch the host shell.
        if demo:
            return JSONResponse(content={"opened": False, "demo": True})
        try:
            os.startfile(folder)  # noqa: S606 — Windows Explorer open of a vetted dir
        except OSError as exc:
            raise HTTPException(status_code=500, detail=f"Could not open folder: {exc}")
        return JSONResponse(content={"opened": True})

    @app.get("/api/job/{job_id}")
    def api_job(job_id: str):
        with JOBS_LOCK:
            record = JOBS.get(job_id)
            if record is None:
                raise HTTPException(status_code=404, detail=f"Unknown job: {job_id}")
            # Return a shallow copy taken under the lock so the client never
            # observes a torn record mid-update.
            return dict(record)

    static_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")

    # Explicit /favicon.ico handler, registered BEFORE the static mount so the
    # "/" mount cannot shadow it. Browsers auto-request /favicon.ico, which would
    # otherwise 404 (the app uses ./icons/* via the manifest/apple-touch link, not
    # a root favicon). Serve an existing PWA icon so the tab gets an icon and the
    # console 404 is gone. Carries the same no-cache header as the rest (see
    # _NoCacheStaticFiles) so a swapped icon is picked up on the next load.
    _favicon = os.path.join(static_dir, "icons", "icon-192.png")

    @app.get("/favicon.ico", include_in_schema=False)
    def favicon():
        if os.path.isfile(_favicon):
            return FileResponse(
                _favicon,
                media_type="image/png",
                headers={"Cache-Control": "no-cache"},
            )
        # No icon on disk -> 204 (still kills the 404) rather than a hard error.
        return Response(status_code=204)

    # Mount static LAST so it can never shadow /api/*. Resolve the directory
    # relative to THIS file so it works regardless of CWD. html=True serves
    # index.html at "/". _NoCacheStaticFiles stamps Cache-Control: no-cache on
    # every static response (html/js/css/manifest/icons) — see its docstring for
    # why this is the primary fix for the stale-ES-module blank-page bug.
    app.mount("/", _NoCacheStaticFiles(directory=static_dir, html=True), name="static")

    return app
