/* MediaVault Console — fetch progress ring (IMP-E14 Phase 2, CANDIDATE B).
 *
 * BORDER TECHNIQUE B: an inline <svg> rounded-rect outline overlaying the card
 * edge. A <path> whose `stroke-dasharray` == the path perimeter and whose
 * `stroke-dashoffset` animates from `perimeter` (empty) down to 0 (closed loop)
 * draws a border that GROWS proportional to the fetch's chunk fraction. A CSS
 * transition on `stroke-dashoffset` smooths the jumps between discrete chunk
 * ticks (buttery, not steppy); at 100% we snap closed and add a gentle glow.
 *
 * Why measure real geometry instead of a unit viewBox + non-scaling dashes:
 * the dash PATTERN lives in user units, so a stretched viewBox would distort
 * how much border each chunk paints per side and "% complete" would stop
 * mapping to "% of the visible outline". So we size the SVG to the card's true
 * pixel box (getBoundingClientRect), build the rounded-rect path in those same
 * px, take its real perimeter via getTotalLength(), and recompute on resize.
 * That yields an EXACT perimeter-proportional fill and an exact numeric %.
 *
 * ES module. `node --check ring.js` covers it on its own (pure DOM wiring; the
 * geometry helper below is plain math and is unit-safe to read in isolation).
 *
 * XSS-safety: this module creates no markup from data — the <svg>/<path> nodes
 * come from the static card template; here we only set numeric attributes and a
 * textContent label. Nothing here ever touches innerHTML.
 */

"use strict";

// Stroke width (px) of the progress outline. The track sits under it at lower
// opacity. Kept here so the geometry inset and the CSS agree.
var STROKE = 3;

// Corner radius (px) of the rounded-rect outline. Visually nested ~2px inside
// the card's own --radius (14px) so the ring reads as a halo on the card edge
// rather than fighting the card's clipped corner.
var CORNER = 12;

// Build the `d` for a rounded rectangle inset by `pad` within a `w` x `h` box,
// STARTING at top-centre and going CLOCKWISE, so the outline grows symmetrically
// down both sides from the top — a satisfying "wrapping" feel. Returns an SVG
// path data string (numbers only; no interpolation of untrusted input).
export function roundedRectPath(w, h, pad, r) {
  var x0 = pad;
  var y0 = pad;
  var x1 = w - pad;
  var y1 = h - pad;
  // Clamp the radius so it never exceeds half of the shorter side.
  var rr = Math.max(0, Math.min(r, (x1 - x0) / 2, (y1 - y0) / 2));
  var midX = (x0 + x1) / 2;
  // Start top-centre -> top-right corner -> down -> bottom -> up -> back to
  // top-centre. Arc flags: sweep=1 (clockwise), large-arc=0 (quarter turns).
  return (
    "M " + midX + " " + y0 +
    " L " + (x1 - rr) + " " + y0 +
    " A " + rr + " " + rr + " 0 0 1 " + x1 + " " + (y0 + rr) +
    " L " + x1 + " " + (y1 - rr) +
    " A " + rr + " " + rr + " 0 0 1 " + (x1 - rr) + " " + y1 +
    " L " + (x0 + rr) + " " + y1 +
    " A " + rr + " " + rr + " 0 0 1 " + x0 + " " + (y1 - rr) +
    " L " + x0 + " " + (y0 + rr) +
    " A " + rr + " " + rr + " 0 0 1 " + (x0 + rr) + " " + y0 +
    " Z"
  );
}

// Clamp a value into [0, 1]; non-finite -> 0 (guards total === 0 upstream too).
function clamp01(x) {
  var n = Number(x);
  if (!isFinite(n)) return 0;
  if (n < 0) return 0;
  if (n > 1) return 1;
  return n;
}

// Create a driver bound to ONE card. The card must contain the static template's
//   <svg class="fetch-ring"> with .fetch-ring-track + .fetch-ring-fill paths
// and a numeric label is created/owned here inside the card-actions row.
//
// Returns an object:
//   setChunks(d,t) -> grow the outline to done/total of the perimeter (guards
//                     t<=0 -> hold at 0), update the "k/N chunks" label
//   complete()     -> snap to a fully-closed, gently-glowing outline (done state)
//   reset()        -> hide the ring + clear the label (back to no-fetch)
//   destroy()      -> stop observing resize (call before the card is discarded)
export function createRing(card, labelHost) {
  var svg = card.querySelector(".fetch-ring");
  var track = card.querySelector(".fetch-ring-track");
  var fill = card.querySelector(".fetch-ring-fill");

  // Numeric a11y label ("k/N chunks" / "%"). Lives next to the action button so
  // it is announced and visible even under prefers-reduced-motion (where the
  // animation is suppressed but the number still tells the whole story).
  var label = document.createElement("span");
  label.className = "fetch-ring-label";
  label.setAttribute("role", "status");
  label.setAttribute("aria-live", "polite");
  label.hidden = true;
  if (labelHost) labelHost.appendChild(label);

  var perimeter = 0; // px length of the current path (recomputed on resize)
  var fraction = 0; // last requested fill fraction [0,1]
  var active = false; // a fetch is in progress / shown
  var done = false; // reached the glowing complete state

  // Lay the SVG out over the card's CURRENT pixel box and rebuild the path so
  // the perimeter matches what the user sees. Safe to call repeatedly.
  function layout() {
    if (!svg || !track || !fill) return;
    var rect = card.getBoundingClientRect();
    var w = Math.max(0, Math.round(rect.width));
    var h = Math.max(0, Math.round(rect.height));
    if (w === 0 || h === 0) return; // not laid out yet (e.g. display:none)
    svg.setAttribute("width", String(w));
    svg.setAttribute("height", String(h));
    svg.setAttribute("viewBox", "0 0 " + w + " " + h);
    // Inset the outline by half the stroke so it isn't clipped by the card's
    // overflow:hidden rounded corner.
    var pad = STROKE / 2 + 1;
    var d = roundedRectPath(w, h, pad, CORNER);
    track.setAttribute("d", d);
    fill.setAttribute("d", d);
    perimeter = fill.getTotalLength ? fill.getTotalLength() : 2 * (w + h);
    fill.style.strokeDasharray = String(perimeter);
    // Re-apply the current fraction against the new perimeter WITHOUT animating
    // (a resize should not look like progress moving).
    applyOffset(false);
  }

  // Write the dashoffset for the current fraction. `animated` toggles the CSS
  // transition: true between chunk ticks (smooth), false on resize/reset (snap).
  function applyOffset(animated) {
    if (!fill) return;
    fill.classList.toggle("no-anim", !animated);
    var off = done ? 0 : perimeter * (1 - fraction);
    fill.style.strokeDashoffset = String(off);
  }

  function show() {
    if (!svg) return;
    active = true;
    svg.hidden = false;
    label.hidden = false;
    card.classList.add("fetching");
    // Geometry may be stale (card just rendered / sub-view just swapped in).
    layout();
  }

  function setLabel(text) {
    label.textContent = text;
  }

  function setChunks(d, t) {
    var total = Number(t);
    var doneN = Number(d);
    if (!active) show();
    // Any growth update leaves the completed/glowing state (e.g. a re-fetch after
    // a previous run finished but before the card auto-flips away).
    done = false;
    if (svg) svg.classList.remove("complete");
    if (!isFinite(total) || total <= 0) {
      // No usable total yet (job just enqueued: progress {0,0}). Hold at 0 but
      // keep the ring visible so the affordance appears immediately.
      fraction = 0;
      applyOffset(true);
      setLabel("0 chunks");
      return;
    }
    if (!isFinite(doneN) || doneN < 0) doneN = 0;
    if (doneN > total) doneN = total; // mirror the server-side clamp
    fraction = clamp01(doneN / total);
    applyOffset(true);
    setLabel(doneN + "/" + total + " chunks");
  }

  function complete(labelText) {
    if (!active) show();
    done = true;
    fraction = 1;
    if (svg) svg.classList.add("complete");
    applyOffset(true); // animate the final snap to a closed loop, then glow
    setLabel(labelText || "100%");
  }

  function reset() {
    active = false;
    done = false;
    fraction = 0;
    if (svg) {
      svg.hidden = true;
      svg.classList.remove("complete");
    }
    card.classList.remove("fetching");
    label.hidden = true;
    setLabel("");
  }

  // Keep the ring matched to the card box across responsive reflow / zoom.
  var ro = null;
  if (typeof ResizeObserver !== "undefined") {
    ro = new ResizeObserver(function () {
      if (active) layout();
    });
    ro.observe(card);
  }

  function destroy() {
    if (ro) {
      ro.disconnect();
      ro = null;
    }
  }

  return {
    setChunks: setChunks,
    complete: complete,
    reset: reset,
    destroy: destroy,
  };
}
