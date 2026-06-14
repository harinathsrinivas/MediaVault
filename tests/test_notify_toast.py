"""Unit tests for tools/notify_toast.py — send_toast().

Constraints:
    Never touch real C:\\Media files or real library_*.json.
    Run `python -m pytest` and fix failures before marking the step done.

All tests mock subprocess.run at the boundary so no PowerShell process is ever
spawned.  sys.platform is also patchable via monkeypatch.setattr.
"""

import subprocess
import sys
import types

import pytest


# ---------------------------------------------------------------------------
# Import the module under test
# ---------------------------------------------------------------------------
import tools.notify_toast as notify_toast


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class _Recorder:
    """Captures args passed to subprocess.run and returns a fake CompletedProcess."""

    def __init__(self, returncode=0, side_effect=None):
        self.calls = []
        self._returncode = returncode
        self._side_effect = side_effect

    def run(self, args, **kwargs):
        self.calls.append(list(args))
        if self._side_effect is not None:
            raise self._side_effect
        return types.SimpleNamespace(returncode=self._returncode)

    @property
    def called(self):
        return bool(self.calls)


# ---------------------------------------------------------------------------
# Test: happy path on win32
# ---------------------------------------------------------------------------

def test_send_toast_invokes_powershell_correctly(monkeypatch):
    """send_toast must call powershell with -NoProfile/-Command and embed both
    ToastNotificationManager and the title/message strings in the script."""
    recorder = _Recorder(returncode=0)
    monkeypatch.setattr(notify_toast.subprocess, "run", recorder.run)
    # Ensure the platform guard passes
    monkeypatch.setattr(sys, "platform", "win32")

    result = notify_toast.send_toast("MyTitle", "MyMessage")

    assert result is True, "Expected True on returncode=0"
    assert recorder.called, "subprocess.run was never called"

    argv = recorder.calls[0]
    assert argv[0] == "powershell", f"First arg must be 'powershell', got {argv[0]!r}"
    assert "-NoProfile" in argv, f"-NoProfile missing from argv: {argv}"
    assert "-Command" in argv, f"-Command missing from argv: {argv}"

    # The PowerShell script is the argument following -Command
    cmd_idx = argv.index("-Command")
    ps_script = argv[cmd_idx + 1]
    assert "ToastNotificationManager" in ps_script, (
        "PS script must reference ToastNotificationManager"
    )
    assert "MyTitle" in ps_script, "Title must appear in PS script"
    assert "MyMessage" in ps_script, "Message must appear in PS script"


def test_send_toast_returns_false_on_nonzero_exit(monkeypatch):
    """send_toast must return False when powershell exits with a non-zero code."""
    recorder = _Recorder(returncode=1)
    monkeypatch.setattr(notify_toast.subprocess, "run", recorder.run)
    monkeypatch.setattr(sys, "platform", "win32")

    result = notify_toast.send_toast("T", "M")
    assert result is False


# ---------------------------------------------------------------------------
# Test: exceptions never propagate
# ---------------------------------------------------------------------------

def test_send_toast_returns_false_on_oserror(monkeypatch):
    """OSError (e.g. powershell not on PATH) must return False, never raise."""
    recorder = _Recorder(side_effect=OSError("not found"))
    monkeypatch.setattr(notify_toast.subprocess, "run", recorder.run)
    monkeypatch.setattr(sys, "platform", "win32")

    result = notify_toast.send_toast("T", "M")
    assert result is False, "Expected False when subprocess.run raises OSError"


def test_send_toast_returns_false_on_timeout(monkeypatch):
    """TimeoutExpired must return False, never propagate."""
    exc = subprocess.TimeoutExpired(cmd="powershell", timeout=20)
    recorder = _Recorder(side_effect=exc)
    monkeypatch.setattr(notify_toast.subprocess, "run", recorder.run)
    monkeypatch.setattr(sys, "platform", "win32")

    result = notify_toast.send_toast("T", "M")
    assert result is False, "Expected False when subprocess.run raises TimeoutExpired"


# ---------------------------------------------------------------------------
# Test: non-Windows short-circuit — no subprocess called
# ---------------------------------------------------------------------------

def test_send_toast_noop_on_linux(monkeypatch):
    """On non-Windows platforms send_toast must return False without touching subprocess."""
    guard = _Recorder(returncode=0)  # would flag if called
    monkeypatch.setattr(notify_toast.subprocess, "run", guard.run)
    monkeypatch.setattr(sys, "platform", "linux")

    result = notify_toast.send_toast("T", "M")

    assert result is False, "Expected False on non-Windows"
    assert not guard.called, "subprocess.run must NOT be called on non-Windows"
