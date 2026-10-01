"""IMP-C29 — tools/gp_inventory.py crawls in its OWN tab, in BOTH modes.

On Chrome 154, chromedriver attaches to the Gemini side panel's webview, so the launch mode
(--port: a fresh Chrome on about:blank) would otherwise crawl inside Gemini. The attach mode
(--attach) already opened its own tab so the user's tabs are left alone; the launch mode now
does the same. Merge note: IMP-C25 rewrites main() into _session(); keep this rule there and
point this test at it.

Hermetic: no Chrome is launched (subprocess.Popen and webdriver.Chrome are stand-ins), the
crawl phases are stubbed, the profile folder and the output live under tmp_path.
"""
import importlib.util
import os
import sys
import types

import pytest

_spec = importlib.util.spec_from_file_location(
    "gp_inventory_own_tab", os.path.join(os.path.dirname(os.path.dirname(__file__)), "tools", "gp_inventory.py"))
gp_inventory = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gp_inventory)


class _FakeDriver:
    """The attached Chrome as chromedriver 154 reports a fresh launch: the Gemini webview
    plus the about:blank launch tab, with the attach starting in the webview."""

    def __init__(self, events):
        self.events = events
        self.window_handles = ["GLIC-WEBVIEW", "BLANK-TAB"]
        self.current_window_handle = "GLIC-WEBVIEW"
        self.switch_to = types.SimpleNamespace(new_window=self._new_window)
        self.service = types.SimpleNamespace(stop=lambda: events.append(("service.stop",)))

    def _new_window(self, kind):
        self.events.append(("new_window", kind))
        self.window_handles = self.window_handles + ["OWN-TAB"]
        self.current_window_handle = "OWN-TAB"

    def execute_cdp_cmd(self, cmd, params):
        self.events.append((cmd, self.current_window_handle))
        return {}

    def close(self):
        self.events.append(("close", self.current_window_handle))


@pytest.mark.parametrize("mode, port", [("--port", "9300"), ("--attach", "9222")])
def test_the_crawl_runs_in_its_own_tab_never_the_gemini_webview(tmp_path, monkeypatch, mode, port):
    events = []
    driver = _FakeDriver(events)
    profile = tmp_path / "profile"
    profile.mkdir()
    monkeypatch.setitem(gp_inventory.CHROME_PROFILES, "tv", str(profile))
    monkeypatch.setattr(gp_inventory.subprocess, "Popen", lambda *a, **k: events.append(("launch",))
                        or types.SimpleNamespace(terminate=lambda: events.append(("terminate",))))
    monkeypatch.setattr(gp_inventory.time, "sleep", lambda *_a, **_k: None)
    monkeypatch.setattr(gp_inventory.webdriver, "Chrome", lambda options=None: driver)
    monkeypatch.setattr(gp_inventory, "enumerate_tiles",
                        lambda drv, *a, **k: events.append(("enumerate", drv.current_window_handle)))
    monkeypatch.setattr(gp_inventory, "crawl_details",
                        lambda drv, *a, **k: events.append(("details", drv.current_window_handle)))
    monkeypatch.setattr(sys, "argv", ["gp_inventory.py", "tv", mode, port, "--out", str(tmp_path / "out")])

    gp_inventory.main()

    work = [e for e in events if e[0] in ("Page.bringToFront", "Emulation.setFocusEmulationEnabled",
                                          "enumerate", "details")]
    assert [e[0] for e in work] == ["Page.bringToFront", "Emulation.setFocusEmulationEnabled",
                                    "enumerate", "details"]
    assert all(e[1] == "OWN-TAB" for e in work), events
    assert events.index(("new_window", "tab")) < events.index(work[0])
    if mode == "--attach":
        assert ("launch",) not in events and ("close", "OWN-TAB") in events   # closes only its own tab
    else:
        assert ("launch",) in events and ("terminate",) in events             # closes the Chrome it launched
