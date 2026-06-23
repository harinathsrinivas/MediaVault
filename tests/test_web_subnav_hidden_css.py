"""Structural guard: the state sub-nav rail is actually HIDDEN in grouped mode.

THE BUG (IMP-E14 polish): in grouped (folder-tree) mode the tree spans ALL states
of a category, so the per-state rail (#subnav) is meaningless over it. app.js's
syncViewChrome() already set `subnav.hidden = grouped`, but the `.subnav` CSS rule
carries an explicit `display: flex`, which OVERRIDES the User-Agent default
`[hidden] { display: none }`. With no `.subnav[hidden]` override the rail stayed
VISIBLE in grouped mode, so an ARCHIVED title appeared under "Unprepped" /
"Local · not pushed" segments even though each leaf card's badge read "Archived".

This test pins BOTH halves of the fix so the defect cannot silently return:
  (1) styles.css carries a `.subnav[hidden] { ... display: none }` rule, AND
  (2) app.js's syncViewChrome() still sets `subnav.hidden = grouped`.

A future edit that re-adds `display: flex` to `.subnav` without the `[hidden]`
override would pass (2) but FAIL (1) — exactly the regression we want to catch.

Pure file-content asserts (no DOM harness): cheap, deterministic, no node needed.
If a jsdom harness is later added, upgrade this to render viewMode="grouped", call
syncViewChrome(), and assert getComputedStyle($("#subnav")).display === "none".
"""
import re
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
_STYLES = _REPO_ROOT / "webui" / "static" / "styles.css"
_APP_JS = _REPO_ROOT / "webui" / "static" / "app.js"


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
        "styles.css must contain a `.subnav[hidden] { ... display: none }` rule so "
        "the `hidden` attribute set by syncViewChrome() actually hides the rail in "
        "grouped mode (the `.subnav` rule's explicit `display:flex` otherwise wins "
        "over the UA `[hidden]` default)."
    )


def test_app_js_syncviewchrome_hides_subnav_in_grouped():
    """syncViewChrome() must set the #subnav `hidden` attribute from `grouped`."""
    js = _APP_JS.read_text(encoding="utf-8")
    assert "function syncViewChrome" in js, "syncViewChrome() missing from app.js"

    # Isolate the syncViewChrome body so the assertion is about THAT function.
    start = js.index("function syncViewChrome")
    # The next top-level `\nfunction ` after it bounds the body well enough.
    nxt = js.find("\nfunction ", start + 1)
    body = js[start: nxt if nxt != -1 else len(js)]

    assert re.search(r"subnav\.hidden\s*=\s*grouped", body), (
        "syncViewChrome() must set `subnav.hidden = grouped` so the state rail is "
        "hidden in grouped mode and shown in decluttered mode."
    )
