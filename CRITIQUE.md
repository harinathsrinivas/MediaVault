# Candidate A Self-Critique

## Approach taken
Built `webui/server.py` with a `create_app()` factory implementing the fixed HTTP
contract, plus `webui/__init__.py` and a placeholder `webui/static/index.html`.
Each `POST /api/action/{name}` registers a Lock-guarded job record and runs the
matching **unchanged** `main.cmd_*` in its own `threading.Thread`, returning
`202 {"job_id": ...}` immediately. Stdout is captured per job WITHOUT the
process-global-clobber hazard of `contextlib.redirect_stdout` by installing a
**thread-local `sys.stdout` proxy** (`_ThreadLocalStdout`) once at app creation:
each job thread binds its own `io.StringIO`; every other thread (read-only GETs,
pytest) falls through to the real interpreter stdout.

## Design decisions and tradeoffs

1. **Thread-local stdout proxy instead of a redirect-lock or per-call swap
   (the crux).** `contextlib.redirect_stdout` reassigns the *process-global*
   `sys.stdout`, so overlapping action threads — and any concurrent
   `GET /api/reclaim` print — would bleed into the wrong buffer. I install one
   `_ThreadLocalStdout` (`server.py:_install_thread_local_stdout`, idempotent) that
   permanently replaces `sys.stdout` and dispatches every `write`/`flush` by
   `threading.current_thread()` via a `threading.local`. A job thread calls
   `proxy.bind(buf)` for the duration of its action and `unbind()` in a `finally`
   (threadpool threads are reused, so restoring fall-through matters). **Why this
   has zero cross-job/cross-thread bleed:** the target buffer is selected purely
   from thread-local state at write time; thread A's writes can only ever resolve
   to the buffer A bound, and a thread that bound nothing resolves to `_real`.
   There is no shared mutable "current redirect" that a second thread could
   overwrite — which is exactly the failure mode of a global redirect.
   *Alternative considered:* a single global lock around a `redirect_stdout`
   critical section. That works for correctness but force-serializes even
   unrelated read-only requests (a long `prep` would block every `GET`'s prints)
   and globally clobbers stdout for the whole process during the action. The
   thread-local proxy is strictly more concurrent and more local. *Tradeoff
   accepted:* the proxy mutates a true process global (`sys.stdout`) for the
   process lifetime. It is installed idempotently and degrades to plain
   pass-through for every unbound thread, so non-web callers see normal stdout.
   I verified the no-bleed property directly (see Tests run).

2. **All mutating actions serialized behind one `_action_lock` (judge criterion
   1).** Every allow-listed action mutates the single real library, and
   push/replace/prep_push_rep also drive the ONE shared ADB device. Two overlapping
   mutations could corrupt library writes or interleave on the device. The worker
   acquires `_action_lock` before calling `main` and holds it for the whole action
   (`server.py:_run_job`). The job is still registered and `202` returns instantly;
   only the worker thread blocks. Since every action in the allow-list mutates,
   they fully serialize in practice — but the thread-per-action shape still gives
   each job its own captured buffer and a non-blocking accept. *Alternative:* a
   per-id lock to allow distinct-id mutations to overlap. Rejected — they still
   share the one ADB device and the single merged library file
   (`save_library` rewrites all three category files), so distinct ids are not
   actually independent. A single device/library lock is the honest model.

3. **Failure taxonomy mapped to `status: "error"`.** `cmd_*` signal handled
   failures by `return False`; I map `False` → `"error"`. I also catch
   `SystemExit` (`main.load_library()` hard-exits on a corrupt library),
   `main.RollbackHardFail`, and any other `Exception` inside the worker so a failed
   action never crashes the server thread — each is recorded as `"error"` with an
   `error` string and the captured output. *Tradeoff:* I do not distinguish
   "clean False failure" from "raised exception" in `status` (both are `"error"`);
   the distinction is preserved in the `error` field and `output` for the UI.

4. **`/api/library` reuses `main.load_library()`, never the disk.** Tallies
   physical entries by exact `status` string under each category (id-prefix
   `mov`/`tv`/`ani`, matching `save_library`), skipping `season_map`/`multi_ep_alias`
   per `ENTRY_TYPE_KEYS`. Counting by raw status string (not a fixed enum) is
   deliberately forward-compatible with new statuses (`restored_local`,
   `some_future_status`, …) that already appear in the codebase.

## Strengths
- No-bleed guarantee is structural and verified under real contention:
  6 concurrent job threads + 1 unbound "reclaim noise" thread, 200 interleaved
  lines each — every job buffer held ONLY its own lines and the unbound prints
  landed only on real stdout (`server.py:_ThreadLocalStdout.write`).
- Import-safe and TestClient-friendly: no module-top/route `uvicorn`, no socket;
  `create_app()` succeeds (acceptance below). `import main` stays side-effect-free.
- Server resilience: `SystemExit` / `RollbackHardFail` / arbitrary exceptions from
  a `cmd_*` are contained per worker and surfaced as job `"error"`
  (`server.py:_run_job`), never tearing down the app.
- Faithful option mapping mirroring the CLI's push dispatch
  (`SIZE_MB`/`SIZE_GB`/`COUNT` + `device` via `main.resolve_device`, `rehash`,
  `tempdir`) without copying any command logic (`server.py:_run_push`).
- `/api` routes registered before the `StaticFiles` mount at `/`, so the SPA mount
  cannot shadow the API.

## Weaknesses
- `_ThreadLocalStdout` replaces the process-global `sys.stdout` for the process
  lifetime. Benign for the web server and idempotent, but a surprising global side
  effect if `create_app()` were ever called inside a host process that itself
  cared about `sys.stdout` identity. It is *not* removed on app teardown (FastAPI
  has no clean "uninstall" hook here); acceptable because step 5 runs this as the
  process's main job.
- `stderr` is not captured — a `cmd_*` that writes diagnostics to `sys.stderr`
  (rather than `print`) would not appear in the job `output`. The codebase routes
  user-facing messages through `print`, so this is low-risk, but it is a real gap.
- Because all actions are serialized, throughput under multiple queued mutations
  is one-at-a-time (by design for safety). There is no max-queue bound or job
  eviction — `_jobs` grows unbounded for the process lifetime (fine for a
  localhost single-user console; a long-running instance would slowly accrete
  records).
- `started_at`/`finished_at` use UTC ISO timestamps; I did not add duration or a
  progress percentage (the contract only requires `started_at`, and progress is
  polling-based, but a richer record could help the step-6 UI).

## Tests run
Ad-hoc in-process check via `fastapi.testclient.TestClient` with every `cmd_*`
and the data layer monkeypatched to harmless print-only stand-ins (NO real
mutation, NO real `C:\Media`/`library_*.json`). The script was **not committed**
(step 4 owns the committed endpoint tests). 24/24 checks passed, exit 0:

```
PASS GET /api/reclaim 200 + contract dict
PASS GET /api/library 200
PASS library total skips season_map -> 4 (got 4)
PASS library movies bucket counts 2
PASS library tallies by status
PASS POST replace without confirm -> 409 (got 409)
PASS POST bogus action -> 404 (got 404)
PASS push without id -> 422
PASS prep without filepath -> 422
PASS GET unknown job -> 404
PASS POST sort -> 202 + job_id
PASS sort job done (got done)
PASS sort job captured its own stdout
PASS job record has started_at
PASS push returning False -> error (got error)
PASS push raising SystemExit -> error (got error)
PASS RollbackHardFail -> error with reason
PASS replace WITH confirm -> 202
PASS confirmed replace runs + captures output
--- concurrency / no-bleed probe ---  (6 jobs x 200 lines + unbound reclaim noise)
PASS no cross-job bleed: each job buffer holds ONLY its own lines
PASS unbound (reclaim) prints fell through to real stdout
PASS reclaim prints never leaked into any job buffer
RESULT: ALL PASS   (exit code 0)
```

Acceptance (required by the step), run after deleting the ad-hoc script:
```
$ python -c "from webui.server import create_app; create_app(); print('ACCEPTANCE OK')"
ACCEPTANCE OK
$ grep -n uvicorn webui/server.py
8:  * There is NO module-top and NO route-level import of ``uvicorn``. ...   (docstring only — no import)
```

## Confidence
high

Reasoning for confidence: The crux — no cross-job/cross-thread stdout bleed — is
solved structurally (per-thread dispatch, not a shared redirect) and I verified it
empirically under heavy contention rather than reasoning about it alone. The full
fixed contract (404/409/202/job-poll/reclaim/library/static mount) is exercised
and green, the acceptance command passes, and there is no `uvicorn` import. The
honest caveats are the process-global `sys.stdout` replacement and the lack of
`stderr` capture; neither affects the contract or the no-bleed guarantee, and both
are documented above.
