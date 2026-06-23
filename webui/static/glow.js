/* MediaVault Console — cursor-following card glow (IMP-E14).
 *
 * Adds a soft radial glow that sits BEHIND each card's content and follows the
 * pointer as it moves inside the card. It is purely cosmetic: a `.card::before`
 * pseudo-element (z-index:-1, pointer-events:none) painted above the card's own
 * background but below the poster / body / fetch-ring / action buttons, so it can
 * never block a click, tap, focus, or the ⤢ expand arrow. See styles.css
 * (`.card::before` + the `isolation:isolate` on `.card`) for the layer itself;
 * this module only writes the gradient centre as two CSS custom properties.
 *
 * Pointer tracking is DELEGATED to one stable container (#panel, created once in
 * index.html and only ever child-cleared on re-render) so it survives every
 * sort / tab / sub-view switch and the post-job /api/items refresh WITHOUT any
 * per-card listener to add or tear down — nothing leaks across re-renders. Moves
 * are COALESCED to one CSS write per animation frame (requestAnimationFrame) so
 * it stays 60fps-smooth and cheap.
 *
 * Accessibility / touch:
 *   • prefers-reduced-motion: tracking is NOT wired at all — the glow falls back
 *     to a static, centred, non-animated highlight (handled entirely in CSS).
 *   • Touch: only `mouse`/`pen` pointers update the centre; `touch` is ignored,
 *     and the CSS hover rule is gated to `(any-hover: hover) and (any-pointer: fine)`, so
 *     a tap never lights the glow and nothing can get stuck on after a finger
 *     lifts. No layout shift, no interference with scrolling or tapping.
 *
 * ES module. `node --check glow.js` covers it on its own (pure DOM wiring).
 */

"use strict";

// A fine hovering pointer is AVAILABLE (desktop mouse / trackpad / pen) — using
// any-hover/any-pointer so a mouse on a touch-capable Windows box (where the
// PRIMARY pointer is reported coarse) still counts. On a pure-touch phone both are
// false, the CSS hover rule does not apply, and we skip tracking entirely.
function hasHoverPointer() {
  try {
    return (
      typeof window.matchMedia === "function" &&
      window.matchMedia("(any-hover: hover) and (any-pointer: fine)").matches
    );
  } catch (e) {
    return false;
  }
}

function prefersReducedMotion() {
  try {
    return (
      typeof window.matchMedia === "function" &&
      window.matchMedia("(prefers-reduced-motion: reduce)").matches
    );
  } catch (e) {
    return false;
  }
}

// Wire the cursor-following glow on a STABLE container (call once with #panel).
// Delegated so freshly-rendered cards are covered automatically and no listener
// outlives the card it was attached to.
export function wireCardGlow(container) {
  if (!container) return;

  // Reduced motion → no following glow (CSS provides a static highlight). Touch /
  // no-hover devices → the CSS hover gate keeps opacity at 0, so tracking would
  // be wasted work. In both cases we simply don't observe pointer movement.
  if (prefersReducedMotion() || !hasHoverPointer()) return;

  // One coalesced update per frame: remember the latest pointer + its card, then
  // write the CSS vars on the next animation frame.
  var pendingCard = null;
  var pendingX = 0;
  var pendingY = 0;
  var frame = 0;

  function flush() {
    frame = 0;
    var card = pendingCard;
    pendingCard = null;
    if (!card || !card.isConnected) return;
    var rect = card.getBoundingClientRect();
    if (rect.width === 0 || rect.height === 0) return; // not laid out
    card.style.setProperty("--mx", (pendingX - rect.left).toFixed(1) + "px");
    card.style.setProperty("--my", (pendingY - rect.top).toFixed(1) + "px");
  }

  container.addEventListener(
    "pointermove",
    function (e) {
      // Only a real hovering pointer drives the glow. Ignoring `touch` means a
      // finger-drag never flashes it and we never read geometry during a scroll.
      if (e.pointerType === "touch") return;
      var card = e.target && e.target.closest ? e.target.closest(".card") : null;
      if (!card || !container.contains(card)) return;
      pendingCard = card;
      pendingX = e.clientX;
      pendingY = e.clientY;
      if (!frame) frame = window.requestAnimationFrame(flush);
    },
    { passive: true }
  );

  // Drop any queued frame whose card the pointer just left, so we don't write a
  // now-stale position. The fade-out itself is CSS (:hover ends). pointercancel
  // (e.g. an interrupted gesture) is treated the same. Both bubble to #panel.
  function dropIfLeaving(e) {
    var card = e.target && e.target.closest ? e.target.closest(".card") : null;
    if (card && card === pendingCard) {
      pendingCard = null;
      if (frame) {
        window.cancelAnimationFrame(frame);
        frame = 0;
      }
    }
  }
  container.addEventListener("pointerleave", dropIfLeaving, { passive: true });
  container.addEventListener("pointercancel", dropIfLeaving, { passive: true });
}
