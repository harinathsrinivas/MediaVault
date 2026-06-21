# Decision: Step 3 — FastAPI app + action-execution model in `webui/server.py` (IMP-E12)

## Outcome
Winner: Candidate C
Branch: `feature/web_console__s3cand_c`

## Step requirements
`create_app()` returns a FastAPI app exposing a FIXED HTTP CONTRACT (identical across candidates — they differ ONLY in the action-execution model behind `POST /api/action`):
- `create_app()` import-safe + TestClient-friendly: NO `uvicorn.run`, NO module-top/route `uvicorn` import.
- `GET /api/reclaim` → `main.collect_reclaimable()` JSON (`{items,total_reclaimable_bytes,total_reclaimable_human}`).
- `GET /api/library` → slim status-counts-by-category summary, reusing the in-process library load (no disk re-walk).
- `POST /api/action/{name}` body `{id?,filepath?,confirm?,options?}`: allow-list EXACTLY `{prep,push,replace,sort,prep_push_rep}` (404 otherwise); `replace` requires `confirm is True` else **409**; else run the matching `main.cmd_*`, register a job `{id,name,status:running|done|error,output,started_at}` in a module dict under a `Lock`, return `{job_id}` with **202**.
- `GET /api/job/{job_id}` → the job record (404 unknown).
- `StaticFiles(directory=<webui/static>, html=True)` mounted at `/` (LAST, so it can't shadow `/api/*`).
- Progress = POLLING. Actions call the EXISTING `main.cmd_*` UNCHANGED. Server localhost-only (bound by step 5). A placeholder `webui/static/index.html` exists so the mount succeeds.

## Judge criteria applied (from the plan, most important first)
1. **Safety of partly-destructive actions** — the two mutating actions (push/replace) must not corrupt each other's state or the SINGLE shared ADB device / shared `library_*.json`.
2. **Correctness + isolation of per-job captured output** — no cross-job / cross-thread stdout bleed.
3. **Testability with `TestClient`** — deterministic completion, no port binding, no real subprocess flakiness.
4. **Simplicity + fit as the Tier-S daemon seed** (`webui/server.py` grows into the IMP-S2 daemon).

## Candidate summaries

### Candidate A — thread-per-action + thread-local stdout proxy
- Approach: each action runs in its own `threading.Thread`; a process-global `sys.stdout` is permanently replaced at `create_app()` by `_ThreadLocalStdout`, which dispatches `write`/`flush` per `threading.current_thread()` to a bound per-thread buffer (unbound threads fall through to the real stream); all mutations serialize behind one `_action_lock` held for the whole `cmd_*`.
- Files modified: `webui/server.py` (398 lines), `webui/__init__.py`, `webui/static/index.html`.
- Lines changed: server.py ~398 lines (largest of the three).
- Tests: read-only contract verified live (reclaim/library 200, replace-no-confirm 409, bogus 404, unknown-job 404); job lifecycle 202→done with own stdout captured. Self-reported 24/24 ad-hoc + 6-thread no-bleed probe.
- Self-critique highlights: honest about the process-global `sys.stdout` replacement persisting for process lifetime (not uninstalled on teardown), stderr not captured, `_jobs` unbounded.
- Independent assessment:
  - Strengths:
    - `_action_lock` is genuinely held across the entire `cmd_*` call (`server.py:266-268`), so two mutations cannot interleave on the device or library — criterion 1 satisfied.
    - The thread-local proxy IS sound as written: dispatch is purely from thread-local state at write time (`_ThreadLocalStdout._target`, `server.py:175-176`), so no shared "current redirect" exists for a second thread to clobber. Verified live that `sys.stdout` becomes `_ThreadLocalStdout` and the job captured only its own line.
    - Richest, most faithful arg mapping — passes `parent_id`, `device_id` via `main.resolve_device`, `eager_rehash`, `temp_dir` (`server.py:99-135`), matching the real `cmd_push`/`cmd_prep_push_rep` kwargs.
    - Adds `422` validation for missing `id`/`filepath` before spawning.
  - Weaknesses:
    - `create_app()` has a **permanent, process-wide side effect**: it swaps `sys.stdout` for the life of the interpreter and never restores it (confirmed live — `sys.stdout` is `_ThreadLocalStdout` after a mere test `create_app()`). For a module whose headline contract is "import-safe + TestClient-friendly," mutating a process global at app-construction time is the least clean fit (criteria 3 and 4). Step-4 tests and any other test in the same process inherit the swapped stdout.
    - Most code and the most moving parts (a bespoke stdout-proxy class with `__getattr__`/`isatty`/`flush` fallbacks) — the proxy is correct but is a non-trivial mechanism to maintain, and its correctness is a property a future editor must preserve.
    - Since every allow-listed action mutates and all serialize behind one lock, the thread-per-action shape buys nothing over a queue except an extra thread per job and the need for the proxy.

### Candidate B — subprocess-per-action
- Approach: each action is a child process `[sys.executable, <abs main.py>, name, *argv]`, `cwd=repo root`, `stdout=PIPE`, `stderr=STDOUT`, `text=True`; a per-job thread streams the pipe into the job record; status from returncode; one `_DEVICE_LOCK` acquired before spawn and held until the child exits.
- Files modified: `webui/server.py` (333 lines), `webui/__init__.py`, `webui/static/index.html`.
- Lines changed: server.py ~333 lines.
- Tests: read-only contract verified live (reclaim/library 200, replace-no-confirm AND `confirm:1` truthy-not-True both 409, bogus 404, unknown-job 404, root 200); `sys.stdout` confirmed untouched. Self-reported 38/38 ad-hoc + serialization probe (max 1 concurrent child).
- Self-critique highlights: honest about per-action Python spawn + full library reload, text-only output (no shared in-memory state), no cancellation/timeout (a wedged child holds the device lock), queued jobs read `running`.
- Independent assessment:
  - Strengths:
    - TRUE output isolation by OS construction — each child owns its own real stdout, so there is no `redirect_stdout`/`sys.stdout` race surface at all (the cleanest answer to criterion 2).
    - `_DEVICE_LOCK` is acquired before `Popen` and released only after `proc.wait()` (`server.py:178-207`), so at most one child ever touches the device — criterion 1 satisfied.
    - `sys.stdout` is left completely untouched at the process level (verified live) — the cleanest process hygiene of the three.
    - Robust spawn-failure handling records an `error` job instead of crashing.
  - Weaknesses:
    - **Worst fit as the Tier-S daemon seed (criterion 4).** The action runs in a *separate process* with no shared in-memory state, so the daemon can never observe structured progress, share the loaded library object, or coordinate beyond parsing text — exactly the capabilities IMP-S2 will want. It re-shells the CLI rather than driving the in-process data layer the rest of the app already uses.
    - **Testability friction (criterion 3).** Real actions spawn a real Python interpreter + full `load_library()` per call; step-4's committed endpoint tests must stub the subprocess (B's own ad-hoc tests used `python -c` stand-ins) to stay deterministic and fast. Real subprocess flakiness/latency is a standing risk the other two avoid.
    - Couples to the CLI argv surface (`_build_argv`, `server.py:111-164`) — a second contract to keep in sync with `main.py`'s `sys.argv` dispatch, on top of the `cmd_*` signatures. More surface to drift.
    - `bufsize=1` line buffering on a `text=True` pipe is fine but pairs with no timeout, so a wedged child holds the device lock indefinitely with no recovery path.

### Candidate C — serialized single-worker queue
- Approach: a `queue.Queue` + exactly one long-lived daemon worker (started idempotently under `_WORKER_LOCK`) runs actions in-process one at a time; per-job stdout via plain `contextlib.redirect_stdout(io.StringIO())`, race-free because only one action ever runs; worker catches `SystemExit` first, then any `BaseException`, recording `error` and continuing.
- Files modified: `webui/server.py` (316 lines), `webui/__init__.py`, `webui/static/index.html`.
- Lines changed: server.py ~316 lines (smallest of the three).
- Tests: read-only contract verified live (reclaim/library 200, replace-no-confirm 409, bogus 404, unknown-job 404, root 200); exactly **1 worker after 3× `create_app()`** (verified live); job lifecycle 202→done capturing only its own line, with `started_at`; `sys.stdout` untouched at rest. Self-reported FIFO/isolation/error-survival/`SystemExit(1)`-path probes all green; existing `tests/test_web_datafns.py` 36/36 still pass.
- Self-critique highlights: the standout honest caveat — `redirect_stdout` isolates against other *actions* (guaranteed by the single worker) but NOT against an unrelated process-wide stdout writer; production-benign under uvicorn (no competing writer) but a silent precondition. Also: throughput (long push blocks the queue), `_JOBS`/queue unbounded, ids global-monotonic, one shared worker per process.
- Independent assessment:
  - Strengths:
    - **Both safety problems vanish by construction.** The single worker means push/replace can never run concurrently against the one device/library (criterion 1) AND only one action's stdout is ever redirected at a time (criterion 2) — no explicit device lock and no bespoke stdout machinery needed. The simplest mechanism that is provably safe on the two heaviest criteria.
    - **`SystemExit`-first catch is load-bearing and correct** (`server.py:148-158`): `load_library()` does `sys.exit(1)` on a corrupt library and `SystemExit` is not an `Exception`; catching it before the generic `except BaseException` keeps the worker alive (verified: `SystemExit(1)`→error, `SystemExit(0)`→done, next job still served). A naive `except Exception` would silently wedge the queue — C is the only candidate whose model makes worker-survival the explicit central concern, and it gets it right.
    - **Cleanest process hygiene + testability (criteria 3, 4).** `sys.stdout` is untouched at rest (only swapped transiently inside the worker), no subprocess to stub, no port. Idempotent single-worker start verified live (1 worker after 3× `create_app()`). It IS the daemon seed: a single long-lived in-process worker draining a queue is structurally what IMP-S2 grows into.
    - Smallest, most direct code (316 lines); thin explicit body→`cmd_*` mapping (`server.py:55-78`); job reads/writes under `JOBS_LOCK` with a `dict(record)` copy on read (no torn reads).
  - Weaknesses:
    - The `redirect_stdout` capture relies on the silent precondition that no unrelated thread prints to `sys.stdout` while a job runs. Real under adversarial conditions, but benign under the actual uvicorn deployment (the executor even hit and documented it). Not coded as a guard.
    - Throughput: a long `push` blocks every queued action (this is the *source* of the safety, and correct for a single-user single-device console).
    - Queued jobs report `running` not a distinct `queued`; `_JOBS`/queue unbounded; ids global-monotonic across `create_app()` calls; one shared worker per process (tests must reset module globals for isolation).
    - Thinner arg mapping than A — only positional `id`/`filepath` and `split_method`/`split_val` (`server.py:55-78`); does not thread through `device_id`/`eager_rehash`/`temp_dir`/`parent_id`. All default cleanly in `main.cmd_*`, so it is correct, but it exposes fewer knobs than A (a step-6/follow-up gap, not a contract miss).

## Head-to-head comparison

**A vs B.** Both serialize mutations correctly (A's `_action_lock` held across the whole `cmd_*`; B's `_DEVICE_LOCK` held across the whole child lifetime), so both are safe on criterion 1. On criterion 2, B is cleaner in principle (OS-level isolation, no shared stdout) while A is correct but via a bespoke proxy that permanently mutates `sys.stdout`. On criteria 3 and 4 they diverge sharply: A keeps everything in-process (good daemon fit) but at the cost of a permanent process-global stdout swap; B keeps `sys.stdout` pristine but loses all in-process state and adds real-subprocess latency/flakiness plus a second (CLI argv) contract to maintain. A has the richer, more faithful arg mapping; B has the cleaner process hygiene.

**A vs C.** Functionally near-identical safety outcome — both serialize all mutations and both end up running actions one-at-a-time (A because every allow-listed action takes the single `_action_lock`; C because the single worker admits one at a time). The decisive difference is *how* they get there. A pays for a thread-per-job it never benefits from (everything serializes anyway) PLUS a bespoke thread-local stdout proxy that permanently replaces `sys.stdout` at `create_app()`. C achieves the same serialization and the same per-job isolation with a plain `redirect_stdout` that is race-free for free — no proxy, no global side effect at construction, fewer lines. A captures more `cmd_*` kwargs and adds `422` validation; C is simpler and cleaner as the daemon seed. The extra knobs A threads through are a genuine plus, but they are recoverable in a later step; A's permanent `sys.stdout` mutation is a structural cost paid on every import-for-test.

**B vs C.** Both leave `sys.stdout` clean and both serialize the device safely. B's isolation is OS-strong; C's relies on the single-worker invariant plus the documented "no concurrent unrelated writer" precondition. But on criteria 3 and 4 C wins clearly: C runs in-process (deterministic under TestClient, no subprocess to stub, shares the data layer) and is structurally the Tier-S daemon (one long-lived worker draining a queue), whereas B re-shells the CLI per action, can't share state, and introduces real subprocess latency the daemon would inherit. C is also the smaller, single-contract implementation.

## Rationale for chosen winner

Candidate C wins on the two heaviest criteria *and* on the two lighter ones, with the smallest, most direct code. On **criterion 1 (device/library safety)**, the single FIFO worker makes concurrent device/library mutation structurally impossible — there is exactly one worker (verified live: 1 thread after 3× `create_app()`), so push and replace can never overlap. This is the same safety A and B achieve with an explicit lock, but C gets it "for free" from the execution shape rather than from a lock a future editor could misplace. On **criterion 2 (output isolation)**, the same single-worker invariant makes the plain `contextlib.redirect_stdout(io.StringIO())` at `server.py:146` race-free by construction — no bespoke proxy (A) and no subprocess (B) required. C's `SystemExit`-first catch (`server.py:148`, before the generic `except BaseException` at `server.py:159`) is the one genuinely subtle correctness hazard in this whole step — `load_library()` calls `sys.exit(1)` on a corrupt library and `SystemExit` is not an `Exception` — and C is the candidate that centers worker-survival as its explicit design concern and handles it correctly (verified: `SystemExit(1)`→error, next job still served).

On **criterion 3 (testability)** C is in-process and deterministic: I drove a no-op action through 202→`done` with isolated captured output under `TestClient`, and `sys.stdout` is untouched at rest (unlike A, which permanently replaces it at `create_app()`). On **criterion 4 (daemon seed / simplicity)** C is the best structural fit for IMP-S2 — a single long-lived worker draining a `queue.Queue` is precisely the daemon shape — and it is the smallest implementation (316 lines) with a single contract surface (the `cmd_*` signatures only), no CLI-argv duplication (B) and no stdout-proxy machinery (A).

What C does **worse**: its arg mapping is the thinnest — it threads only `id`/`filepath`/`split_method`/`split_val` into `main.cmd_*`, omitting `device_id`/`eager_rehash`/`temp_dir`/`parent_id` that A wires up. It is fully correct (those parameters default cleanly in `main.py`), but A exposes more of the command surface today. C also omits the `422` pre-validation A adds, and its `redirect_stdout` carries the documented "no unrelated concurrent stdout writer" precondition that B's process isolation avoids entirely.

Those weaknesses are acceptable given the plan's priority order. The missing kwargs and `422` validation are additive and recoverable in step 6 / a follow-up without touching the execution model. The `redirect_stdout` precondition is benign under the real uvicorn deployment (the contract has no concurrent in-process stdout writer) — and crucially it is a *documented* precondition, not a latent bug, whereas A's remedy for the same concern is a permanent process-global `sys.stdout` mutation that is itself a more invasive side effect for a module whose headline promise is import-safety. Trading a documented benign precondition for zero global side effects, less code, and a cleaner daemon seed is the right call under criteria 1–4.

## Why not the others?

**Not A.** A is correct and its concurrency claims hold under scrutiny — `_action_lock` genuinely wraps the whole `cmd_*` (`server.py:266-268`) and the thread-local proxy genuinely dispatches per thread with no shared redirect to clobber. But A solves a problem it largely creates: because every allow-listed action mutates and all serialize behind one lock, the thread-per-action shape never buys real concurrency, yet it forces the bespoke `_ThreadLocalStdout` proxy — which **permanently replaces `sys.stdout` at `create_app()` and never restores it** (confirmed live). For a module whose load-bearing promise is "import-safe + TestClient-friendly," mutating a process global at construction time is the least clean fit on criteria 3 and 4, and it is the most code with the most moving parts. C reaches identical safety and isolation with a plain `redirect_stdout`, no global side effect, and ~80 fewer lines. A's richer arg mapping and `422` validation are real merits, but not enough to outweigh the structural cost.

**Not B.** B has the strongest output isolation (separate OS stdout per child) and keeps `sys.stdout` pristine, and its `_DEVICE_LOCK` correctly serializes the device. But it is the weakest fit for criteria 3 and 4: actions run in a *separate process* with no shared in-memory state, so the future IMP-S2 daemon could never see structured progress, share the loaded library, or coordinate beyond parsing text — and it pays a Python spawn + full `load_library()` per action. It also introduces real subprocess latency/flakiness (step-4's deterministic tests would have to stub the subprocess) and adds a second contract — the CLI `sys.argv` surface in `_build_argv` — to keep in sync with `main.py`. For a single-user localhost console seeding an in-process daemon, re-shelling the CLI per action is the wrong default.

## What we keep from losing candidates (follow-up suggestions, NOT auto-synthesized)
- **From A:** thread the remaining `main.cmd_*` knobs through C's body→`cmd_*` mapping — `device_id` (via `main.resolve_device`), `eager_rehash`, `temp_dir`, and `prep`'s `parent_id` — and add A's `422` pre-validation for missing `id`/`filepath`. Both are additive to C's runners (`server.py:55-78`) and would close the only real capability gap vs A.
- **From C's own critique:** add the one-line code caveat (or a lightweight guard) documenting that `redirect_stdout` capture assumes no unrelated concurrent stdout writer; and add `_JOBS`/queue eviction (TTL or cap) before the daemon is long-lived (IMP-S2).
- **From B:** if structured progress is later required, B's line-streaming-into-the-job-record pattern is the template — but keep it in-process (e.g. a per-job buffer the worker appends to) rather than via a child process.

## Verification status
Confirmed: Candidate C passes all acceptance criteria from the step. Verified live under `TestClient`: `create_app()` builds with no `uvicorn` import and no socket; `GET /api/reclaim`→200 with the contract keys; `GET /api/library`→200 (status-counts-by-category, reusing `main.load_library()` with `season_map`/`multi_ep_alias` skipped, no disk re-walk); `POST /api/action/replace` without `confirm is True`→409; bogus action→404; `GET /api/job/<unknown>`→404; `POST /api/action/{name}` for an allow-listed action→202 with `{job_id}` and a Lock-guarded job record carrying `id`/`name`/`status`/`output`/`started_at`, polled to `done` with isolated captured output; static mounted LAST at `/` (root→200) without shadowing `/api/*`. Exactly one worker after 3× `create_app()`; existing `tests/test_web_datafns.py` still green per the candidate's report. Actions call the unchanged `main.cmd_*` whose signatures were confirmed in `main.py` (`cmd_prep` :831, `cmd_push` :1269, `cmd_replace` :1864, `cmd_sort` :2442, `cmd_prep_push_rep` :2661).
