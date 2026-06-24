"""Permanent tests for the web worker's INCREMENTAL progress flush + the
`fetch_restore` action (IMP-E14 Phase 2).

These pin the two new behaviors of the serialized single-worker model in
`webui/server.py`:

  1. The `_JobTee` republishes partial `output` AND a freshly parsed
     `progress {done,total}` (chunk units) on EVERY print — so a polling client
     sees output grow and `progress.done` climb 0 -> N WHILE the job is still
     `running`, not only at the terminal flush. Terminal completeness is also
     pinned: `done == total` (clamped) and the full output is captured.
  2. `fetch_restore` is an allow-listed action that streams the mainfetch
     subprocess stdout; the worker neither wedges nor dies on it, on the error
     path, and on the alias/season_map library.

Design notes (serialized-worker-safe):
  * The single daemon worker is FAST. To OBSERVE growth/advancement mid-run we
    inject a FAKE runner that prints mainfetch-style markers with small sleeps
    BETWEEN them, so successive `GET /api/job/{id}` polls land between writes.
  * The fake runner is injected the same way every real action is dispatched —
    via a temporary `ACTION_TABLE` entry — so the FULL real pipeline runs
    (`api_action` -> `_enqueue` -> `_worker_loop` -> `_JobTee` ->
    `_parse_progress` -> `_terminal_progress`). The entry is always restored in
    a `finally` (the `temp_action` fixture) so no other test is affected.
  * `fetch_restore` is exercised end-to-end under `mock_device`, which fakes
    BOTH `subprocess.run` AND `subprocess.Popen` — so NO real Selenium / mainfetch
    / merge ever runs; the fake Popen yields a few mainfetch-style lines + wait()=0,
    which the streaming loop re-prints, so the captured job output is non-empty.

All tests operate exclusively on the `sandbox` / `sandbox_alias` fixtures (which
dual-patch mvcommon.LIBRARY_* AND main.LIBRARY_* + LOCAL_ROOT to a tmp tree and
hard-guard against real C:\\Media). No real C:\\Media file or real library_*.json
is ever touched.
"""

import json
import time as _time

import pytest

# Skip the whole module if fastapi (or httpx, its TestClient dep) is absent, so
# the suite stays green on machines without it.
pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient  # noqa: E402 (after importorskip guard)

import main  # noqa: E402
import mvcommon  # noqa: E402 (kept for parity / future use)

from webui import server as web_server  # noqa: E402

# Secure-by-default auth (IMP-E15) is always enforced on /api/*; these progress/
# job/action endpoint tests drive the API as the LOCAL OWNER (TestClient host
# "testclient" is non-loopback => would 401), so run the module as the
# genuine-local admin. See the web_as_local_admin fixture docstring.
pytestmark = pytest.mark.usefixtures("web_as_local_admin")


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

@pytest.fixture()
def temp_action():
    """Register a temporary (name, runner, requires_confirm) into the live
    ACTION_TABLE for the duration of one test, then RESTORE the table exactly.

    Yields a callable: register(name, runner, requires_confirm=False) -> name.
    Multiple registrations in one test are all rolled back. We snapshot any
    pre-existing value for each name and restore/delete it in teardown so the
    real allow-list (prep/push/replace/sort/prep_push_rep/fetch_restore) is
    byte-identical after the test — other tests sharing the module-level table
    are unaffected.
    """
    saved = {}  # name -> (existed_before, previous_value)

    def register(name, runner, requires_confirm=False):
        if name not in saved:
            saved[name] = (name in web_server.ACTION_TABLE,
                           web_server.ACTION_TABLE.get(name))
        web_server.ACTION_TABLE[name] = (runner, requires_confirm)
        return name

    try:
        yield register
    finally:
        for name, (existed, prev) in saved.items():
            if existed:
                web_server.ACTION_TABLE[name] = prev
            else:
                web_server.ACTION_TABLE.pop(name, None)


def _poll(client, job_id, timeout=20):
    """Poll GET /api/job/{job_id} until status is done or error, or timeout.

    Mirrors tests/test_web_endpoints.py::_poll (same contract, same enum).
    """
    deadline = _time.monotonic() + timeout
    data = None
    while _time.monotonic() < deadline:
        r = client.get(f"/api/job/{job_id}")
        assert r.status_code == 200, f"job poll returned {r.status_code}: {r.text}"
        data = r.json()
        if data["status"] in ("done", "error"):
            return data
        _time.sleep(0.05)
    raise TimeoutError(
        f"job {job_id} did not finish within {timeout}s; last record: {data}"
    )


def _collect_running_snapshots(client, job_id, timeout=20):
    """Poll TIGHTLY (no inter-poll sleep) and record a snapshot
    (output_len, progress, status) on every read until the job is terminal.

    Returns (snapshots, terminal) where `snapshots` is the full ordered list of
    records observed (including the terminal one) and `terminal` is the final
    record. A tight loop maximises the chance of catching the worker mid-run so
    the incremental-flush assertions are not flaky against scheduler timing.
    """
    deadline = _time.monotonic() + timeout
    snapshots = []
    terminal = None
    while _time.monotonic() < deadline:
        r = client.get(f"/api/job/{job_id}")
        assert r.status_code == 200, f"job poll returned {r.status_code}: {r.text}"
        data = r.json()
        snapshots.append({
            "output_len": len(data.get("output", "")),
            "progress": dict(data.get("progress", {})),
            "status": data["status"],
        })
        if data["status"] in ("done", "error"):
            terminal = data
            break
        # No sleep: tight poll to observe intermediate states.
    if terminal is None:
        raise TimeoutError(
            f"job {job_id} did not finish within {timeout}s; "
            f"last snapshot: {snapshots[-1] if snapshots else None}"
        )
    return snapshots, terminal


# Per-chunk pause used by the incremental fake runners. Long enough that a tight
# polling loop reliably observes intermediate output/progress between writes,
# short enough that the whole job (3 chunks) stays well under the poll timeout.
_CHUNK_PAUSE_S = 0.25


def _fake_split_runner(body):
    """A FAKE action runner that emits mainfetch-style markers for a 3-chunk
    split entry with a pause BEFORE each MOVED line, then returns True (truthy ->
    the worker scores it 'done' regardless of _NONE_IS_SUCCESS).

    Marker strings match exactly what webui.server._parse_progress scans for:
      "🔹 PROCESSING:"  -> starts an entry segment
      "   > Detected Split File (3 chunks)" -> total += 3
      "     ✅ MOVED: cN" (x3) -> done counts up 1,2,3
      "✅ ENTRY COMPLETE." -> entry end
    Output is produced via print() so it flows through redirect_stdout(_JobTee).
    """
    print("🔹 PROCESSING: fake-split-entry")
    print("   > Detected Split File (3 chunks)")
    for n in range(1, 4):
        _time.sleep(_CHUNK_PAUSE_S)  # pause so polls land between MOVED writes
        print(f"     ✅ MOVED: c{n}")
    print("✅ ENTRY COMPLETE.")
    return True


# ---------------------------------------------------------------------------
# (a) Incremental output + advancing progress, observed WHILE running.
# ---------------------------------------------------------------------------

def test_progress_advances_incrementally_while_running(temp_action):
    """The job's `output` must GROW across successive polls (not just at the
    end) AND `progress.done` must take an intermediate value (0 < done < 3)
    while the job is still `running`, BEFORE the terminal state.

    This pins the _JobTee write-through republish: each print updates the job
    record live, so a tight poller sees partial output and climbing progress
    mid-run rather than a single 0 -> done jump at completion.
    """
    from webui.server import create_app

    name = temp_action("__test_split_progress__", _fake_split_runner)

    client = TestClient(create_app())

    r = client.post(f"/api/action/{name}", json={})
    assert r.status_code == 202, f"Expected 202, got {r.status_code}: {r.text}"
    job_id = r.json()["job_id"]

    snapshots, terminal = _collect_running_snapshots(client, job_id)

    # --- output length grows across successive RUNNING snapshots ---
    running = [s for s in snapshots if s["status"] == "running"]
    assert running, (
        "never observed a 'running' snapshot — the worker finished before any "
        "poll caught it mid-run (increase _CHUNK_PAUSE_S if this is flaky)"
    )
    output_lens = [s["output_len"] for s in running]
    # Non-decreasing AND at least one STRICT increase while running -> proof that
    # output was republished incrementally, not only at the terminal flush.
    assert all(b >= a for a, b in zip(output_lens, output_lens[1:])), (
        f"output length went BACKWARDS across running polls: {output_lens}"
    )
    assert any(b > a for a, b in zip(output_lens, output_lens[1:])), (
        f"output never GREW across running polls (no incremental flush "
        f"observed): {output_lens}"
    )

    # --- progress.done advances to an intermediate value WHILE running ---
    running_dones = [s["progress"].get("done", 0) for s in running]
    # total is parsed from "Detected Split File (3 chunks)" and should read 3 on
    # any running snapshot taken after that line was printed.
    assert any(s["progress"].get("total") == 3 for s in running), (
        f"never observed total==3 while running; running progress: "
        f"{[s['progress'] for s in running]}"
    )
    assert any(0 < d < 3 for d in running_dones), (
        f"progress.done never took an intermediate value (0 < done < 3) while "
        f"running — it appears to have jumped straight to terminal. "
        f"running dones: {running_dones}"
    )
    # done is monotonic non-decreasing across the running snapshots.
    assert all(b >= a for a, b in zip(running_dones, running_dones[1:])), (
        f"progress.done went BACKWARDS while running: {running_dones}"
    )


# ---------------------------------------------------------------------------
# (b) Terminal completeness: status done, clamped {3,3}, full output captured.
# ---------------------------------------------------------------------------

def test_progress_terminal_is_complete_and_clamped(temp_action):
    """On terminal, status == 'done', progress == {done:3,total:3} (the
    success-with-chunks clamp), and the final output contains every printed
    marker line."""
    from webui.server import create_app

    name = temp_action("__test_split_terminal__", _fake_split_runner)

    client = TestClient(create_app())

    r = client.post(f"/api/action/{name}", json={})
    assert r.status_code == 202, f"Expected 202, got {r.status_code}: {r.text}"
    job_id = r.json()["job_id"]

    job = _poll(client, job_id)

    assert job["status"] == "done", (
        f"fake split job ended status={job['status']!r}. "
        f"Output:\n{job.get('output', '')}"
    )
    assert job["progress"] == {"done": 3, "total": 3}, (
        f"terminal progress {job['progress']} != {{done:3,total:3}}"
    )
    # progress_unit is the always-present documentation field.
    assert job.get("progress_unit") == "chunks"

    out = job["output"]
    for marker in (
        "🔹 PROCESSING: fake-split-entry",
        "Detected Split File (3 chunks)",
        "✅ MOVED: c1",
        "✅ MOVED: c2",
        "✅ MOVED: c3",
        "✅ ENTRY COMPLETE.",
    ):
        assert marker in out, f"final output missing marker {marker!r}:\n{out}"


# ---------------------------------------------------------------------------
# (c) fetch_restore end-to-end (neutralized via mock_device).
# ---------------------------------------------------------------------------

def _seed_archived_split(sandbox):
    """Seed lib_movies with one ARCHIVED split entry (uploaded, a dummy on disk,
    split_info present incl. total_chunks). Returns the entry id.

    The on-disk file is a tiny dummy (< DUMMY_MAX_BYTES) under the sandbox media
    dir, matching a real post-archive entry. split_info carries total_chunks +
    per-chunk metadata so the entry is schema-complete, but the restore path
    short-circuits at the missing 'restore' folder (no chunks were really
    fetched, because Popen is faked) and returns False without merging — exactly
    the neutralized outcome we want.
    """
    entry_id = "mov-en-2019-archivedsplitfilm"
    media_dir = sandbox["media_dir"]
    filename = "archived_split_film.mkv"
    file_path = media_dir / filename
    # A dummy original (archived state): smaller than DUMMY_MAX_BYTES.
    file_path.write_bytes(b"DUMMY-ARCHIVED" * 100)  # ~1.4 KB
    assert file_path.stat().st_size < main.DUMMY_MAX_BYTES

    entry = {
        entry_id: {
            "status": "archived",
            "uploaded": True,
            "folder_path": str(media_dir),
            "filename": filename,
            "type": "movie",
            "split_info": {
                "is_split": True,
                "total_chunks": 3,
                "chunks": [
                    {"filename": f"{filename}.chunk.{i:03d}", "hash": "deadbeef"}
                    for i in range(1, 4)
                ],
            },
        }
    }
    sandbox["lib_movies"].write_text(json.dumps(entry), encoding="utf-8")
    sandbox["lib_series"].write_text("{}", encoding="utf-8")
    sandbox["lib_anime"].write_text("{}", encoding="utf-8")
    return entry_id


def test_fetch_restore_end_to_end_neutralized(sandbox, mock_device, temp_action):
    """POST /api/action/fetch_restore on an archived split entry, under
    mock_device (which fakes BOTH subprocess.run AND subprocess.Popen so NO real
    Selenium / mainfetch / merge runs):

      - returns 202 + job_id
      - polls to a TERMINAL state 'done' (cmd_fetch_restore returns None on every
        path; "fetch_restore" is in _NONE_IS_SUCCESS, so None -> done)
      - the streamed fake-Popen lines are captured -> output is non-empty and
        reflects the fake mainfetch stream
      - the single worker did NOT wedge: a SUBSEQUENT action still runs to done.
    """
    from webui.server import create_app

    entry_id = _seed_archived_split(sandbox)

    client = TestClient(create_app())

    r = client.post("/api/action/fetch_restore", json={"id": entry_id})
    assert r.status_code == 202, f"Expected 202, got {r.status_code}: {r.text}"
    job_id = r.json()["job_id"]

    job = _poll(client, job_id)
    assert job["status"] == "done", (
        f"fetch_restore job ended status={job['status']!r}. "
        f"Output:\n{job.get('output', '')}"
    )

    # The fake Popen yields a few mainfetch-style lines that cmd_dispatch_fetch
    # re-prints through the worker's tee -> captured output is non-empty and
    # carries the streamed marker(s).
    assert job["output"].strip(), "expected non-empty captured output from the fake stream"
    assert "PROCESSING:" in job["output"] or "ENTRY COMPLETE" in job["output"], (
        f"captured output does not reflect the fake mainfetch stream:\n{job['output']}"
    )

    # The worker did not wedge: a subsequent action still drains to a terminal
    # state. Use a trivially-succeeding fake runner.
    name = temp_action("__test_after_fetch__", lambda body: True)
    r2 = client.post(f"/api/action/{name}", json={})
    assert r2.status_code == 202, f"Expected 202, got {r2.status_code}: {r2.text}"
    job2 = _poll(client, r2.json()["job_id"])
    assert job2["status"] == "done", (
        f"follow-up action did not complete (worker wedged?): status="
        f"{job2['status']!r}\n{job2.get('output', '')}"
    )


# ---------------------------------------------------------------------------
# (d) Error path: a runner that RAISES -> status 'error' with the exception text
#     in output, AND the single worker SURVIVES (next action still completes).
# ---------------------------------------------------------------------------

def _raising_runner(body):
    """A FAKE runner that prints a line then raises, to drive the worker's
    BaseException branch (status='error', output += '[<ExcType>] <msg>')."""
    print("about to explode")
    raise RuntimeError("synthetic boom-xyzzy")


def test_error_runner_marks_error_and_worker_survives(temp_action):
    """A runner that raises must yield status=='error' with the exception text in
    `output`; a FOLLOWING normal action must still complete 'done' (the single
    serialized worker survived the exception and kept draining the queue)."""
    from webui.server import create_app

    bad = temp_action("__test_raises__", _raising_runner)

    client = TestClient(create_app())

    r = client.post(f"/api/action/{bad}", json={})
    assert r.status_code == 202, f"Expected 202, got {r.status_code}: {r.text}"
    job = _poll(client, r.json()["job_id"])

    assert job["status"] == "error", (
        f"raising runner should be 'error', got {job['status']!r}.\n{job.get('output','')}"
    )
    # The BaseException branch appends "[<ExcType>] <msg>" to the captured output;
    # both the printed line and the exception text must be present.
    assert "about to explode" in job["output"], (
        f"pre-exception output missing:\n{job['output']}"
    )
    assert "synthetic boom-xyzzy" in job["output"], (
        f"exception message missing from output:\n{job['output']}"
    )
    assert "RuntimeError" in job["output"], (
        f"exception TYPE missing from output:\n{job['output']}"
    )

    # Worker survived: a following normal action still completes 'done'.
    good = temp_action("__test_after_raise__", lambda body: True)
    r2 = client.post(f"/api/action/{good}", json={})
    assert r2.status_code == 202, f"Expected 202, got {r2.status_code}: {r2.text}"
    job2 = _poll(client, r2.json()["job_id"])
    assert job2["status"] == "done", (
        f"the single worker did NOT survive the prior exception — follow-up "
        f"action ended status={job2['status']!r}\n{job2.get('output','')}"
    )


# ---------------------------------------------------------------------------
# (e) Alias-safety: GET /api/items and a fetch_restore POST over a library that
#     contains a season_map + multi_ep_alias must not raise / not 500.
# ---------------------------------------------------------------------------

def test_alias_library_items_and_fetch_restore_do_not_500(sandbox_alias, mock_device):
    """Over the `sandbox_alias` library (season_map parent + multi_ep_alias):

      - GET /api/items returns 200 (no crash on the virtual rows).
      - POST /api/action/fetch_restore for the ALIAS id returns 202 and polls to
        a terminal state WITHOUT a 500 / unhandled raise. mock_device fakes
        subprocess.Popen so no real fetch runs; cmd_fetch_restore resolves the
        alias to its primary, finds no 'restore' folder, returns None -> done.
    """
    from webui.server import create_app

    alias_id = sandbox_alias["alias_id"]

    client = TestClient(create_app())

    # --- GET /api/items must not 500 on the alias/season_map rows ---
    r_items = client.get("/api/items")
    assert r_items.status_code == 200, (
        f"/api/items on the alias library -> {r_items.status_code}: {r_items.text}"
    )

    # --- fetch_restore on the alias id: 202, polls terminal, never 500 ---
    r = client.post("/api/action/fetch_restore", json={"id": alias_id})
    assert r.status_code == 202, (
        f"fetch_restore(alias) -> {r.status_code}: {r.text}"
    )
    job = _poll(client, r.json()["job_id"])
    # cmd_fetch_restore returns None on every path; "fetch_restore" is in
    # _NONE_IS_SUCCESS so a clean (no-exception) completion -> 'done'. The key
    # guarantee is NO 500 / NO unhandled raise on the alias-bearing library.
    assert job["status"] in ("done", "error"), (
        f"fetch_restore(alias) did not reach a terminal state: {job['status']!r}"
    )
    assert job["status"] == "done", (
        f"fetch_restore(alias) ended status={job['status']!r} (expected 'done' — "
        f"a non-raising alias resolution + missing-restore short-circuit).\n"
        f"{job.get('output', '')}"
    )
