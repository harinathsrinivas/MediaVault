"""Structural guard: the card cursor-glow + rotating "wave" border must keep
working on (a) touch-capable desktops and (b) reduced-motion systems.

HISTORY (IMP-E14): the cursor-follow glow (`glow.js` + `.card::before`) and the
rotating conic hover border (`.card::after`) twice appeared "broken" on the user's
Alienware:

  1. They were gated on `(hover: hover) and (pointer: fine)` — the PRIMARY pointer.
     A touch-capable Windows box reports the primary pointer as `coarse` even with a
     mouse attached, so the effects silently turned off. Fix: gate on availability —
     `(any-hover: hover) and (any-pointer: fine)` (true whenever a mouse/trackpad/pen
     exists, still false on a pure-touch phone).

  2. The rig reports `prefers-reduced-motion: reduce` (a "best performance" perf
     tweak), which froze the cursor glow to a static centre and stopped the ring
     rotation. Since this constantly-moving glow is a small, non-vestibular, EXPLICITLY
     user-requested effect, it now animates regardless of the reduced-motion setting:
     glow.js dropped its reduced-motion early-return, and the two card reduced-motion
     CSS overrides were removed. (Reduced-motion fallbacks for OTHER components —
     modals, tree-loading, the space background — are intentionally kept.)

This guard pins both fixes so neither silently rolls back. Pure file-content assert
(no DOM harness): cheap, deterministic, no node needed.
"""
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
_STYLES = (_REPO_ROOT / "webui" / "static" / "styles.css").read_text(encoding="utf-8")
_GLOW = (_REPO_ROOT / "webui" / "static" / "glow.js").read_text(encoding="utf-8")

_ANY = "(any-hover: hover) and (any-pointer: fine)"
_PRIMARY = "(hover: hover) and (pointer: fine)"


def test_card_effects_gate_on_any_pointer_not_primary():
    """Card hover effects must gate on a fine pointer being AVAILABLE (any-*), so a
    mouse on a touch-capable Windows desktop still triggers them."""
    assert _ANY in _STYLES, "styles.css must gate card hover effects on any-hover/any-pointer"
    assert _ANY in _GLOW, "glow.js must check any-hover/any-pointer (mouse available)"
    # The old PRIMARY-pointer gate is what broke the touch-capable desktop — it must
    # not come back in either file.
    assert _PRIMARY not in _STYLES, (
        "styles.css must NOT use the primary-pointer gate `(hover: hover) and "
        "(pointer: fine)` — it disables hover effects on touch-capable desktops."
    )
    assert _PRIMARY not in _GLOW, (
        "glow.js must NOT use the primary-pointer gate — use the any-* form."
    )


def _reduced_motion_blocks(css):
    """Bodies of all `@media (prefers-reduced-motion: reduce)` blocks, brace-balanced
    so nested `@supports`/rules are included."""
    blocks = []
    marker = "@media (prefers-reduced-motion: reduce)"
    i = 0
    while True:
        idx = css.find(marker, i)
        if idx == -1:
            break
        brace = css.find("{", idx)
        if brace == -1:
            break
        depth, j = 0, brace
        while j < len(css):
            if css[j] == "{":
                depth += 1
            elif css[j] == "}":
                depth -= 1
                if depth == 0:
                    break
            j += 1
        blocks.append(css[brace : j + 1])
        i = j + 1
    return blocks


def test_card_glow_and_ring_animate_regardless_of_reduced_motion():
    """The cursor-follow glow + the rotating ring are deliberately user-requested and
    must animate even when the system reports prefers-reduced-motion: reduce."""
    # glow.js must NOT functionally gate pointer tracking on reduced motion. (A code
    # COMMENT mentioning the policy is fine — we pin the actual matchMedia query + the
    # removed helper, not the word.)
    assert 'matchMedia("(prefers-reduced-motion' not in _GLOW, (
        "glow.js must not query prefers-reduced-motion to gate tracking — the glow is a "
        "deliberate, always-on requested effect."
    )
    assert "prefersReducedMotion" not in _GLOW, (
        "glow.js must not re-introduce a reduced-motion early-return helper."
    )
    # The following radial (cursor-follow) and the ring spin must be present and live.
    assert "circle at var(--mx" in _STYLES, (
        "the cursor-follow glow (radial centred on var(--mx)/var(--my)) must exist."
    )
    assert "animation: cardRingSpin" in _STYLES, (
        "the rotating 'wave' border (animation: cardRingSpin) must exist."
    )
    # No reduced-motion @media block may target the card glow / ring — that is what
    # froze them. Other components' reduced-motion fallbacks (modals, tree-loading,
    # space background) are intentionally untouched.
    for block in _reduced_motion_blocks(_STYLES):
        assert ".card::before" not in block, (
            "a reduced-motion block must not override .card::before (freezes the cursor glow)."
        )
        assert ".card:hover::after" not in block, (
            "a reduced-motion block must not override .card:hover::after (stops the ring spin)."
        )
        assert "cardRingSpin" not in block and "--ring-angle" not in block, (
            "a reduced-motion block must not touch the ring animation."
        )
