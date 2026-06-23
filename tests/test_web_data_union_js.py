"""Pytest wrapper that runs the node data-bucket + tree-prune guard (IMP-E14 polish).

The pure web-console logic lives in webui/static/{data,tree}.js (DOM-free ES
modules). tests/js/test_data_buckets.mjs exercises TWO scenarios against the REAL
shipped modules and exits non-zero on the first failed assertion:
  (1) data.js loadModel()/countBy — the one-state-per-id merge invariant; and
  (2) tree.js pruneTreeByState() — the grouped-view state prune (keep a folder iff
      a descendant leaf matches; folder size = aggregate of visible leaves; "All"
      keeps everything; the input tree is never mutated).
This wrapper shells to node so both JS guards participate in the normal `pytest -q`
run (CI has no separate JS harness yet).

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
