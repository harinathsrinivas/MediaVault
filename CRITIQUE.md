# Candidate B Self-Critique

## Approach taken
Built `webui/server.py` with a `create_app()` factory exposing the FIXED contract,
plus `webui/__init__.py` and a placeholder `webui/static/index.html`. GET routes
(`/api/reclaim`, `/api/library`) call the merged `main.py` data layer IN-PROCESS.
Every POST action runs as a **child process** — `[sys.executable, <abs main.py>,
name, *mapped_args]` via `subprocess.Popen(stdout=PIPE, stderr=STDOUT, text=True,
cwd=<repo root>)` — with a per-job daemon thread streaming the child's pipe
line-by-line into the job record and setting `done`/`error` from the returncode. A
single process-wide `_DEVICE_LOCK` (held by the worker for the child's whole
lifetime) serialises every mutating action so only one ever touches the one ADB
device / `library_*.json` at a time. No `uvicorn` anywhere (serving is step 5's
`cmd_web`).

## Design decisions and tradeoffs
- **Subprocess for true stdout isolation, but a single device gate for safety.**
  Each child owns its own real OS stdout, so there is NO shared `sys.stdout` and
  NO `redirect_stdout` race to defeat (candidate A's problem). Process isolation,
  however, does nothing for *device/library* contention — two children would still
  fight over the one phone and race the JSON. I therefore acquire `_DEVICE_LOCK`
  **before spawning** and hold it until the child exits (`server.py:_run_job`).
  Alternative considered: a bounded `Semaphore(1)` (identical effect) or letting
  pushes overlap (rejected — corruption). All five allow-listed verbs are mutating,
  so one global lock is both correct and the simplest gate.
- **Worker blocks on the lock, POST does not.** `_start_job` registers the record
  as `running` and returns the `job_id` immediately (202); the worker thread may
  then block on `_DEVICE_LOCK` if another action is in flight. Tradeoff: a *queued*
  job reads as `running` while it is actually waiting for the device. I chose this
  over a separate `"queued"` state to keep the job-record schema exactly as the
  contract names it (`running|done|error`); it is honest enough (the job IS
  accepted and in flight from the server's view).
- **Argv built as a discrete list, no shell.** A filepath with spaces is passed as
  one argv element, so no quoting/escaping bugs (`_build_argv`, verified in tests).
  Defaults mirror the plan: `push`→`SIZE_GB 8`; `options.{split_method,split_val,
  chunks}` override. Paths derive from `__file__` (`_REPO_ROOT`, `_MAIN_PY`,
  `_STATIC_DIR`), so everything is CWD-independent — validated by running the test
  from `C:\Users\harin`.
- **`/api/library` category from id prefix.** `load_library()` merges all three
  JSONs into one id-keyed dict; `mvcommon.save_library` itself splits by `mov`/
  `tv`/`ani` prefix, so I reuse that exact rule to bucket per category and tally by
  `status`, skipping `season_map`/`multi_ep_alias` (no physical status) per
  `ENTRY_TYPE_KEYS`. No disk re-walk.

## Strengths
- True per-job output isolation with zero stdout-race surface (`server.py:_run_job`
  reads each child's own pipe).
- Device safety proven, not just claimed: a 3-action concurrency probe observed
  **max 1** simultaneous device child and all 3 drained `done` (no deadlock /
  starvation).
- Contract fully honoured and tested: 404 (bad action / unknown job), 409 (replace
  without `confirm is True`, including a truthy `confirm:1` -> 409), 202 + `job_id`,
  static mounted LAST and not shadowing `/api/*`.
- Robust failure handling: a failed spawn is recorded as an `error` job, never
  crashes the server (`_run_job` try/except around `Popen`).
- CWD-independent path resolution; import-safe (no `uvicorn`, `import main` is
  read-only).

## Weaknesses
- **Spawn + full library reload per action** (the approach's inherent cost): each
  action pays a fresh Python interpreter start and a complete `load_library()` in
  the child. For interactive single actions this is fine; it is wasteful under
  rapid fire.
- **No in-memory state sharing with the action.** Because the action runs in a
  child, the web layer cannot observe its live progress beyond captured stdout
  lines (no structured % — just text), and cannot share the loaded library object.
- **Queued jobs report `running`, not a distinct `queued`** (deliberate, see
  above) — a polling UI cannot distinguish "waiting for device" from "executing".
- **No cancellation / timeout.** A wedged child holds `_DEVICE_LOCK` indefinitely
  and blocks later actions; there is no kill path here (out of scope for step 3).
- `_JOBS` grows unbounded for the process lifetime (no eviction). Acceptable for a
  localhost console; noted for later.
- The library tally surfaces an `"other"` bucket for non-`mov/tv/ani` ids — defensive
  but normally empty.

## Tests run
Primary acceptance:
```
$ python -c "from webui.server import create_app; create_app(); print('ACCEPTANCE: create_app() OK')"
ACCEPTANCE: create_app() OK
```
`uvicorn` grep on `webui/server.py`: only the docstring line "never imports or runs
uvicorn" — NO import.

Ad-hoc TestClient suite (harmless subprocess stand-in — `python -c "print(...)"` /
`sys.exit(3)`; NO real action, run from CWD `C:\Users\harin`): 38/38 PASS, incl.
- `GET /api/reclaim` -> 200 + `{items,total_reclaimable_bytes,total_reclaimable_human}`
- `GET /api/library` -> 200 + categories/totals/total_entries
- `POST /api/action/frobnicate` -> 404; `GET /api/job/unknown` -> 404
- `POST replace` (no confirm / confirm=false / confirm=1) -> 409 x3
- `POST sort` stand-in -> 202 + job_id; polled running→**done**, stdout captured
  (both lines), record carries name+started_at
- `POST push` error stand-in -> 202; polled →**error**, returncode==3
- `GET /` -> 200 placeholder html; `/api/library` still 200 after static mount
- argv mapping: push default `SIZE_GB 8`; options override `COUNT 4 chunks 1-2`;
  prep filepath-with-spaces as ONE element; replace/sort/prep_push_rep shapes;
  argv[0]==sys.executable, argv[1]==absolute main.py.

Serialization probe (3 concurrent actions, sleeping stand-in children writing
marker files):
```
max concurrent device children observed: 1
final job states: ['done', 'done', 'done']
SERIALIZATION OK
```

## Confidence
high

Reasoning for confidence: The full fixed contract is implemented and every clause
is exercised by a passing TestClient check, including the destructive-action 409
gate and the 202→poll→done/error lifecycle. The two non-obvious risks for this
approach — capturing child stdout correctly and serialising the single ADB device
— are both verified empirically (output captured line-by-line; observed max
concurrency = 1 with no deadlock). The acknowledged costs (per-action spawn +
reload, no live structured progress, no cancellation) are inherent to the
subprocess model and were explicitly in scope to accept, not bugs.
