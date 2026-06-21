# Candidate C Self-Critique

Approach C: **serialized single-worker FIFO queue, in-process action execution.**

This is a RESUME critique. A prior executor wrote `webui/server.py`, `webui/__init__.py`,
and `webui/static/index.html` in this worktree; its session died before validating the
queue machinery. I reviewed the existing code against the FIXED contract, validated the
headline queue feature, and made **no code changes** (no contract violation found).

## Approach taken
Every mutating action is appended to one module-level `queue.Queue` (`WORK_QUEUE`,
`server.py:102`) and executed by exactly ONE long-lived daemon thread
(`_worker_loop`, `server.py:123`) strictly FIFO, one at a time. `POST /api/action/{name}`
validates against the allow-list, gates `replace` behind `confirm is True` (409 otherwise),
builds a job record, enqueues it, and returns `202 {"job_id": ...}` immediately
(`server.py:283-298`). The client polls `GET /api/job/{job_id}` (`server.py:300-308`),
which returns a shallow copy of the record taken under `JOBS_LOCK`. The worker wraps each
`main.cmd_*` call in `contextlib.redirect_stdout(io.StringIO())` (`server.py:140-147`) to
capture exactly that job's output — race-free **because only one action ever runs**. Static
files mount LAST at `/` with `html=True` (`server.py:313-314`) so they cannot shadow `/api/*`.
No `uvicorn` import anywhere.

## Design decisions and tradeoffs
- **The queue IS the device lock, and IS the stdout-race eliminator — both by construction.**
  Because the single worker admits exactly one action at a time, two hard problems vanish
  without any explicit lock around `sys.stdout` or the ADB device: (1) `push`/`replace` can
  never run concurrently against the single shared ADB device; (2) a plain process-global
  `redirect_stdout` cannot interleave two actions' output because there is never a second
  concurrent action. This is the central, elegant property of approach C.
  **The tradeoff is throughput: actions run strictly one-at-a-time, so a long `push` blocks
  every queued action behind it.** For a single-user, single-device local console this is the
  *correct* tradeoff — and it is precisely the source of the model's safety. (Approach A's
  thread-per-action would need an explicit device lock AND a non-`redirect_stdout` capture
  strategy to be safe; approach B's subprocess pays process-spawn cost and loses in-process
  library state. C trades latency-under-contention for zero shared-state hazard.)
- **The worker must never die — and the `BaseException` layering proves the author thought
  about it.** `SystemExit` is caught FIRST (`server.py:148`), separately from the generic
  `except BaseException` (`server.py:159`). This matters because `load_library()` does
  `sys.exit(1)` on a corrupt library (`mvcommon.py:186`), and `SystemExit` subclasses
  `BaseException`, not `Exception` — a naive `except Exception` would let it propagate and
  silently kill the worker thread, wedging the whole queue. The code treats exit code `1` as
  `error` and a clean `0`/`None` as `done`. `RollbackHardFail` and anything else land in the
  generic `BaseException` arm and become `error`. There is even a last-resort guard around the
  bookkeeping itself (`server.py:175-181`) and a `finally: task_done()` (`server.py:182-186`).
  I verified every one of these branches empirically (see Tests run).
- **Idempotent worker start under a dedicated lock.** `_ensure_worker` (`server.py:189-200`)
  holds `_WORKER_LOCK` and returns early if a live worker already exists, so repeated
  `create_app()` (as tests do) never spawns duplicate workers. I confirmed exactly one worker
  thread after 3× `create_app()`.
- **Job status stays inside the contract's three-value enum (`running`/`done`/`error`).**
  A queued-but-not-yet-running job is registered as `"running"` (`server.py:212-218`) with a
  documented rationale: the contract enum has no `"queued"` state, so the author stays inside
  the allowed values rather than inventing one. Honest tradeoff — slightly less precise, but
  contract-faithful. I'd have leaned the same way.

## Strengths
- Single worker started exactly once, idempotently — `_ensure_worker` at `server.py:189`;
  verified one thread after 3× `create_app()`.
- Worker cannot die on a job: `SystemExit` (incl. corrupt-library `sys.exit(1)`),
  `RollbackHardFail`, and arbitrary exceptions are each caught and recorded as a job outcome,
  then the loop continues — `server.py:138-186`. Empirically the next job after a failure still
  ran to `done`.
- Per-job stdout capture is correct and isolated under the single-action invariant —
  `server.py:140-147`; each probed job's `output` held ONLY its own marker.
- All job-record reads/writes hold `JOBS_LOCK` and `GET /api/job` returns a `dict(record)`
  copy under the lock (`server.py:117-120`, `300-308`) — no torn reads.
- Contract surface is exact: allow-list `{prep,push,replace,sort,prep_push_rep}`
  (`server.py:81-87`), 404 on unknown action, 409 on `replace` without `confirm is True`
  (`server.py:290-296`), 202 `{job_id}` on success, 404 on unknown job, StaticFiles mounted
  LAST, no `uvicorn` import.
- Static dir resolved relative to `__file__` (`server.py:313`), so it is CWD-independent.
- The body→`cmd_*` arg mapping is thin and explicit (`server.py:55-78`) and matches the real
  `main.cmd_*` signatures I checked (`main.py:831` prep, `1269` push, `1864` replace, `2442`
  sort, `2661` prep_push_rep).

## Weaknesses
- **`redirect_stdout` is process-global, so capture is isolated only w.r.t. other *actions*,
  not w.r.t. unrelated stdout writers in the same process.** If any non-action code printed to
  `sys.stdout` while a job ran, it would be swept into that job's buffer. In production
  (`cmd_web`/uvicorn, no competing stdout writer) this is a non-issue, but it is a real latent
  coupling. I hit this directly: my first probe printed enqueue diagnostics from the main
  thread *while a job was running* and those lines were captured into the job's buffer — NOT a
  server bug (I confirmed it reproduces with bare `redirect_stdout` + a concurrent thread), but
  it exposes the precondition the model silently relies on. Worth a one-line caveat in code.
- **No bound on the queue or the `JOBS` dict.** Both grow without limit; long-lived servers
  leak job records (no eviction/TTL). Fine for a local single-user console, but unbounded.
- **No `cancel`, no queue-depth/position introspection.** A caller cannot see how many jobs are
  ahead of theirs, only `running` vs terminal. Given the one-at-a-time model, position would be
  genuinely useful UX, and its absence is felt more here than in a concurrent model.
- **Worker is a hard module-global singleton.** Two `create_app()` instances in one process
  share ONE worker, ONE `WORK_QUEUE`, and ONE `JOBS` store. Correct for the real single-app
  deployment, but it means tests cannot get an isolated queue per app without resetting module
  globals — a coupling the committed endpoint tests (step 4) must be aware of.
- **`_job_counter` is process-global and monotonic, not reset across apps** — job ids continue
  climbing across `create_app()` calls in the same process (visible in my probe: ids 1-5 then 4
  in a fresh interpreter). Harmless, but ids are not per-app.

## Tests run
I reviewed the code, then validated the queue with an ad-hoc `TestClient` script
(`_queue_probe.py`) that monkeypatched the allow-listed runners with harmless no-ops printing a
per-job marker, polled jobs to terminal, and asserted FIFO / isolation / status / error-survival
/ single-worker. The script was DELETED after running (step 4 owns committed endpoint tests). Real
output:

Contract baseline (orchestrator's checks, re-confirmed):
```
reclaim 200 ['items', 'total_reclaimable_bytes', 'total_reclaimable_human']
replace-no-confirm 409
bogus-action 404
unknown-job 404
library 200 ['by_category', 'by_status', 'total']
static-root 200 True
```

Queue machinery probe:
```
=== probe 1: FIFO + per-job stdout isolation + running->done ===
  enqueued id='alpha' action=sort -> job_id=1 status=done output='MARKER::alpha::only-mine\n'
  enqueued id='bravo' action=prep -> job_id=2 status=done output='MARKER::bravo::only-mine\n'
  enqueued id='charlie' action=sort -> job_id=3 status=done output='MARKER::charlie::only-mine\n'
  FIFO order observed by worker: ['alpha', 'bravo', 'charlie']  [OK]
  per-job stdout isolation: each record holds ONLY its marker  [OK]

=== probe 2: action raises -> status 'error', worker keeps serving ===
  raising job 4 status=error output='MARKER::boom::about-to-raise\n\n[RuntimeError] boom from action'
  follow-up job 5 status=done output='MARKER::survivor::only-mine\n'
  worker survived the exception and drained the next job  [OK]

=== probe 3: single worker (exactly one) ===
  mediavault-web-worker threads alive after 3x create_app(): 1  [OK]

ALL PROBES PASSED
```

`SystemExit` branch probe (the corrupt-library `sys.exit(1)` path the worker is built for):
```
SystemExit(1): error 'about-to-exit\n\n[exited with code 1]'
SystemExit(0): done 'clean-exit\n'
after-SystemExit-jobs: done 'still-serving\n'
workers: 1
SYSTEMEXIT PATHS OK
```

Existing step-2 web tests (regression check — undisturbed):
```
$ python -m pytest tests/test_web_datafns.py -q
....................................                                     [100%]
36 passed in 0.69s
```

NOTE on the first probe attempt: it falsely "failed" a bleed assertion because the harness
printed enqueue diagnostics from the main thread *concurrently with a running job*, and
`redirect_stdout` (being process-global) captured them into the job buffer. I confirmed this is
a property of `redirect_stdout` itself (reproduced with a bare thread), fixed the harness to
emit all diagnostics to stderr and only after jobs were terminal, and it passed cleanly. This is
recorded above as a real (if production-benign) weakness of the model, not a defect introduced
or a server bug.

## Confidence
**high**

Reasoning for confidence: I exercised every load-bearing branch of the queue empirically — FIFO
ordering, per-job stdout isolation, running→done, `False`/`None`→error, raised-exception→error
with worker survival, the `SystemExit(1)` corrupt-library path→error, `SystemExit(0)`→done, and
idempotent single-worker start — and all passed. I verified the body→`cmd_*` arg mapping against
the real `main.cmd_*` signatures and confirmed `load_library`'s `sys.exit(1)` is what the
`SystemExit` handler is built for. The contract surface (allow-list, 404/409/202/404, static
mounted last, no uvicorn) re-confirmed green. I made zero code changes because I found no actual
violation and no way for the worker to die or duplicate. The one real limitation
(process-global `redirect_stdout` only isolates against other *actions*) is documented and is a
non-issue under the real uvicorn deployment; it is the only thing keeping me from "absolute"
certainty about stdout capture in adversarial multi-writer conditions.
