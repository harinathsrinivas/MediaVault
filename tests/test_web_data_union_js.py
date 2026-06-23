"""Pytest wrapper that runs the node data-bucket regression test (IMP-E14 polish).

The pure web-console merge lives in webui/static/data.js (a DOM-free ES module).
Its one-state-per-id invariant is exercised by tests/js/test_data_buckets.mjs,
which stubs the global `fetch`, imports the REAL data.js, and exits non-zero on a
failed assertion. This wrapper shells to node so the JS guard participates in the
normal `pytest -q` run (CI has no separate JS harness yet).

Skips cleanly when node is not on PATH — mirrors the real-binary skip pattern used
for ffmpeg / mkvmerge fixtures in conftest.py, so the suite stays green on a box
without node. Touches no library files and no real C:\\Media.
"""
import shutil
import subprocess
from pathlib import Path

import pytest

# tests/ -> repo root -> tests/js/test_data_buckets.mjs
_REPO_ROOT = Path(__file__).resolve().parent.parent
_NODE_TEST = _REPO_ROOT / "tests" / "js" / "test_data_buckets.mjs"


def test_data_union_one_state_per_id():
    """Run the node guard; fail with its stdout/stderr if it exits non-zero."""
    node = shutil.which("node")
    if node is None:
        pytest.skip("node not on PATH — skipping JS data-bucket guard")
    assert _NODE_TEST.exists(), f"missing node test: {_NODE_TEST}"

    proc = subprocess.run(
        [node, str(_NODE_TEST)],
        capture_output=True,
        text=True,
        cwd=str(_REPO_ROOT),
    )
    if proc.returncode != 0:
        pytest.fail(
            "node data-bucket guard failed (exit %d)\n--- stdout ---\n%s\n--- stderr ---\n%s"
            % (proc.returncode, proc.stdout, proc.stderr)
        )
