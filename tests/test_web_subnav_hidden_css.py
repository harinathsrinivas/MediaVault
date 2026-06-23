"""Structural guard: the defensive `.subnav[hidden]` display:none rule exists.

HISTORY (IMP-E14 polish): an earlier fix HID the per-state rail (#subnav) in
grouped (folder-tree) mode, on the theory that a per-state rail over a whole-
category tree was meaningless. That fix was REVERSED: the rail now shows in BOTH
view modes and, in grouped mode, FILTERS the folder tree (All → the whole tree; a
state → the tree pruned to that state). So app.js no longer sets
`subnav.hidden = grouped` — there is nothing to assert there anymore.

What REMAINS valid, and what this test pins, is the DEFENSIVE CSS rule: the
`.subnav` rule carries an explicit `display: flex`, which overrides the User-Agent
default `[hidden] { display: none }`. Without a `.subnav[hidden]` override, any
future code that sets the `hidden` attribute on the rail would be silently ignored
and the rail would stay visible. The rule below keeps `hidden` authoritative.

Pure file-content assert (no DOM harness): cheap, deterministic, no node needed.
"""
import re
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
_STYLES = _REPO_ROOT / "webui" / "static" / "styles.css"


def test_styles_css_hides_subnav_when_hidden_attr_set():
    """`.subnav[hidden]` (or `.subnav.is-hidden`) must resolve to display:none."""
    css = _STYLES.read_text(encoding="utf-8")
    # Match a selector whose target is `.subnav[hidden]` (possibly grouped with
    # other selectors via commas) whose block contains `display: none`.
    # e.g.  `.subnav[hidden],\n.subnav.is-hidden { display: none; }`
    pattern = re.compile(
        r"\.subnav\[hidden\][^{}]*\{[^}]*display\s*:\s*none", re.IGNORECASE | re.DOTALL
    )
    assert pattern.search(css), (
        "styles.css must keep the defensive `.subnav[hidden] { ... display: none }` "
        "rule so the `hidden` attribute stays authoritative over the `.subnav` rule's "
        "explicit `display:flex` (the rail is shown in both view modes in normal flow, "
        "but this guard keeps `hidden` working if it is ever set)."
    )
