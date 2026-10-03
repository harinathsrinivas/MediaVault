"""IMP-C36 (IMP-C25 Step 10 i) — `python main.py fetch` streams mainfetch's output live.

cmd_dispatch_fetch runs mainfetch.py as a child and re-prints each line of its piped output. A PIPEd child's print()
is block-buffered, so nothing appeared for minutes and then everything at once. Under test:

  - main._mainfetch_child_env(): the parent's environment + PYTHONIOENCODING=utf-8 + PYTHONUNBUFFERED=1 — the env of
    every mainfetch child (cmd_dispatch_fetch now; IMP-C25 Steps 13 / 13b's check_photos children later);
  - a REAL child process (this interpreter, a tiny script in tmp_path) whose first line must reach the parent while
    the child is still running — it waits for a file the parent creates on seeing that line;
  - webui.server._parse_progress keeps today's counts on today's transcripts (pin).

Hermetic: the child is a stand-in script under tmp_path (never mainfetch, never a browser); "python" in the spawned
argv is pointed at this interpreter; the learned-record merge finds nothing (the autouse state redirect).
"""
import ast
import contextlib
import io
import os
import subprocess
import sys
import time

import pytest

import main


def test_the_fetch_child_runs_unbuffered_and_in_utf8_and_inherits_the_rest(monkeypatch):
    monkeypatch.setenv("MV_STEP10_SENTINEL", "kept")
    seen = []

    class _Child:
        stdout = []

        def wait(self):
            return 0

    monkeypatch.setattr(main.subprocess, "Popen", lambda cmd, **k: seen.append((list(cmd), k)) or _Child())
    main.cmd_dispatch_fetch("tv-x", "1-3")

    [(argv, kwargs)] = seen
    assert argv == ["python", "mainfetch.py", "fetch", "tv-x", "episodes", "1-3"]       # unchanged
    env = kwargs["env"]
    assert (env["PYTHONUNBUFFERED"], env["PYTHONIOENCODING"], env["MV_STEP10_SENTINEL"]) == ("1", "utf-8", "kept")
    assert (kwargs["stdout"], kwargs["stderr"], kwargs["text"], kwargs["bufsize"]) == (
        subprocess.PIPE, subprocess.STDOUT, True, 1)


CHILD = '''\
import os, sys, time
go = sys.argv[2]                                         # argv: [script, "fetch", <id slot = the go-file>]
print("🔹 PROCESSING: Demo.mkv (abc123)")             # no flush: only the environment can make this stream
deadline = time.time() + 20
while not os.path.exists(go) and time.time() < deadline:
    time.sleep(0.05)
print("     ✅ MOVED: Demo.mkv")
print("GO" if os.path.exists(go) else "TIMEOUT")
'''


class _Terminal(io.StringIO):
    """The parent's stdout: on the child's first line it creates the go-file the child is waiting for."""

    def __init__(self, go):
        super().__init__()
        self.go, self.first_line_at = go, None

    def write(self, s):
        if "PROCESSING" in s and self.first_line_at is None:
            self.first_line_at = time.monotonic()
            open(self.go, "w").close()
        return super().write(s)


def test_mainfetch_output_reaches_the_parent_while_the_child_still_runs(tmp_path, monkeypatch):
    monkeypatch.delenv("PYTHONUNBUFFERED", raising=False)   # e.g. PyCharm sets it: the test must not lean on it
    script = tmp_path / "child_fetch.py"
    script.write_text(CHILD, encoding="utf-8")
    go = tmp_path / "go.flag"
    monkeypatch.setattr(main, "MAINFETCH_SCRIPT", str(script))
    real_popen = subprocess.Popen

    def popen(cmd, **kwargs):                              # "python" on PATH -> this very interpreter
        return real_popen([sys.executable] + list(cmd[1:]), **kwargs)

    monkeypatch.setattr(main.subprocess, "Popen", popen)
    terminal = _Terminal(str(go))
    started = time.monotonic()

    with contextlib.redirect_stdout(terminal):
        main.cmd_dispatch_fetch(str(go))                   # the go-file path rides in the id slot (argv[3])

    out = terminal.getvalue()
    assert "GO" in out and "TIMEOUT" not in out, out       # the first line arrived while the child waited for it
    assert "🔹 PROCESSING: Demo.mkv (abc123)" in out and "✅ MOVED: Demo.mkv" in out   # UTF-8 end to end
    assert terminal.first_line_at - started < 15


def test_every_mainfetch_child_spawn_uses_the_child_env():
    # Structural pin for later spawns (IMP-C25 Steps 13 / 13b): a main.py function that names mainfetch's script and
    # starts a process passes env=_mainfetch_child_env() — directly or through a local name bound to it.
    with open(main.__file__, encoding="utf-8") as fh:
        tree = ast.parse(fh.read())
    checked = 0
    for fn in ast.walk(tree):
        if not isinstance(fn, ast.FunctionDef) or fn.name == "_mainfetch_child_env":
            continue
        source = ast.unparse(fn)
        if "MAINFETCH_SCRIPT" not in source and "mainfetch.py" not in source:
            continue
        child_env_names = {t.id for node in ast.walk(fn) if isinstance(node, ast.Assign)
                           and isinstance(node.value, ast.Call) and getattr(node.value.func, "id", None) == "_mainfetch_child_env"
                           for t in node.targets if isinstance(t, ast.Name)}
        for call in ast.walk(fn):
            if isinstance(call, ast.Call) and getattr(call.func, "attr", None) == "Popen":
                env = next((k.value for k in call.keywords if k.arg == "env"), None)
                direct = isinstance(env, ast.Call) and getattr(env.func, "id", None) == "_mainfetch_child_env"
                named = isinstance(env, ast.Name) and env.id in child_env_names
                assert direct or named, f"{fn.name}: a mainfetch child must get env=_mainfetch_child_env()"
                checked += 1
    assert checked >= 1                                    # cmd_dispatch_fetch at least


def test_the_web_progress_counts_of_todays_transcripts_are_unchanged():
    web = pytest.importorskip("webui.server")
    from test_fetch_harvester import ORACLES
    # Frozen pre-Step-10 transcripts: a season (2 episodes + 1 extra), a split entry with a holder (the holder is
    # not in the chunk total: done clamps to it), a partly staged split, a whole file.
    assert web._parse_progress(ORACLES["route_season_with_extra"]) == {"done": 3, "total": 3}
    assert web._parse_progress(ORACLES["split_holder"]) == {"done": 2, "total": 2}
    assert web._parse_progress(ORACLES["split_partly_staged"]) == {"done": 1, "total": 2}
    assert web._parse_progress(ORACLES["whole_attempt2"]) == {"done": 1, "total": 1}
    assert web._PROGRESS_SIGNALS == ("🔹 PROCESSING:", "Detected Split File", "✅ MOVED:")
