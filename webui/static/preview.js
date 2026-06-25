/* MediaVault Console — cinematic hover detail-window (IMP-E16).
 *
 * THE SIGNATURE "aweee" MOMENT. Resting the pointer on any card — movie, series,
 * episode, or anime — for a short dwell auto-opens a large, translucent glass
 * "dossier": the show's backdrop as a dimmed cover, its real title + year, the
 * episode line when it's an episode, the synopsis, and a tight meta row (state ·
 * size · category). A personal film vault, so it reads like a now-showing card.
 *
 * DELEGATION (mirrors glow.js): ONE set of listeners on the stable #panel
 * (created once in index.html; only child-cleared on re-render), so every
 * freshly-rendered card — flat grid AND grouped-tree leaf — is covered with no
 * per-card listener to add or leak across sort / tab / sub-view switches and the
 * post-job /api/items refresh. The hovered .card is mapped back to its item via
 * `card.__mvItem`, stamped by buildCard (card.js).
 *
 * DESKTOP-POINTER ONLY: gated on `(any-hover: hover) and (any-pointer: fine)` and
 * only `mouse`/`pen` pointers ever arm it (the SAME gate the cursor glow uses).
 * On touch nothing wires, so a tap still navigates the card and the panel never
 * appears or gets stuck. The panel is `position:fixed; pointer-events:none`, so it
 * is purely informational and can never block a click, tap, focus, or the ⤢
 * expand arrow.
 *
 * PERF: exactly ONE reusable panel element is built (lazily, on the first open),
 * its contents + backdrop src are swapped per open, and the fanart request only
 * fires when a preview actually opens — never speculatively on render. A
 * per-open token ignores late load/error events from a superseded card so a slow
 * image can't paint into the wrong dossier.
 *
 * A11y: keyboard focus on a card ALSO opens it (focusin) and blur closes it, so
 * the dossier isn't mouse-only. prefers-reduced-motion → instant open, no
 * transform, no Ken-Burns drift (handled in CSS; this module just toggles state).
 *
 * XSS-safe: every text node is set via textContent; the only interpolated value
 * is the library's own canonical id, URL-encoded into the image path (identical
 * to card.js's poster request). ES module; `node --check preview.js` covers it.
 */

"use strict";

import { metaFor, humanSize, CATEGORY_META } from "./data.js";
import { displayTitle } from "./title.js";

// Rest-this-long over a card before the dossier opens. Long enough that sweeping
// the pointer ACROSS the grid to reach something never flashes a dozen panels;
// short enough that a deliberate hover feels responsive.
var DWELL_MS = 380;

// A true hovering pointer is AVAILABLE (desktop mouse / trackpad / pen). Uses
// any-hover/any-pointer so a mouse on a touch-capable Windows box (PRIMARY
// pointer reported coarse) still counts; on a pure-touch phone both are false and
// we skip wiring entirely. Mirrors glow.js exactly.
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

// Friendly category word for the meta row ("Movie" / "Series" / "Anime" / "Other").
// Singularises the tab label so a single item doesn't read "Movies".
function categoryWord(category) {
  var meta = CATEGORY_META[category];
  var label = (meta && meta.label) || category || "";
  if (label === "Movies") return "Movie";
  if (label === "TV series") return "Series";
  if (label === "Others") return "Other";
  return label; // "Anime"
}

// ---------------------------------------------------------------------------
// The single reusable panel.
// ---------------------------------------------------------------------------
//
// Built once on the first open and reused for the lifetime of the page. Returns a
// handle of the parts we rewrite per open so we never re-query the DOM mid-hover.
var _panel = null;

function buildPanel() {
  if (_panel) return _panel;

  var root = document.createElement("aside");
  root.id = "hover-preview";
  root.className = "hover-preview";
  root.setAttribute("aria-hidden", "true"); // informational mirror of the card
  // Belt-and-braces: even if a stylesheet failed, never let the panel eat clicks.
  root.style.pointerEvents = "none";

  // Backdrop layer (dimmed cover image + scrim). The <img> is lazily pointed at
  // the fanart on each open; the scrim is a CSS gradient over it for legibility.
  var media = document.createElement("div");
  media.className = "hp-media";
  var img = document.createElement("img");
  img.className = "hp-backdrop";
  img.alt = "";
  img.decoding = "async";
  img.draggable = false;
  media.appendChild(img);
  var scrim = document.createElement("div");
  scrim.className = "hp-scrim";
  media.appendChild(scrim);
  root.appendChild(media);

  // Foreground content.
  var body = document.createElement("div");
  body.className = "hp-body";

  var kicker = document.createElement("div");
  kicker.className = "hp-kicker";
  kicker.textContent = "Now showing";
  body.appendChild(kicker);

  var title = document.createElement("h3");
  title.className = "hp-title";
  body.appendChild(title);

  var ep = document.createElement("div");
  ep.className = "hp-episode";
  body.appendChild(ep);

  var synopsis = document.createElement("p");
  synopsis.className = "hp-synopsis";
  body.appendChild(synopsis);

  var meta = document.createElement("div");
  meta.className = "hp-meta";
  // State badge (chip with a coloured dot, reusing the per-state palette).
  var stateChip = document.createElement("span");
  stateChip.className = "hp-state";
  var stateDot = document.createElement("span");
  stateDot.className = "hp-state-dot";
  var stateLabel = document.createElement("span");
  stateLabel.className = "hp-state-label";
  stateChip.appendChild(stateDot);
  stateChip.appendChild(stateLabel);
  meta.appendChild(stateChip);
  // Size + category as quiet pills.
  var sizePill = document.createElement("span");
  sizePill.className = "hp-pill hp-size";
  meta.appendChild(sizePill);
  var catPill = document.createElement("span");
  catPill.className = "hp-pill hp-cat";
  meta.appendChild(catPill);
  body.appendChild(meta);

  root.appendChild(body);
  document.body.appendChild(root);

  _panel = {
    root: root,
    media: media,
    img: img,
    kicker: kicker,
    title: title,
    ep: ep,
    synopsis: synopsis,
    stateChip: stateChip,
    stateDot: stateDot,
    stateLabel: stateLabel,
    sizePill: sizePill,
    catPill: catPill,
  };
  return _panel;
}

// ---------------------------------------------------------------------------
// Backdrop image with a fanart -> poster -> state-gradient fallback waterfall.
// ---------------------------------------------------------------------------
//
// We try the wide fanart first (only when items_payload reported one on disk),
// then the poster (only when one exists), then give up and let the state-tinted
// gradient stand alone — so a broken-image icon NEVER flashes and we never fire a
// request that is certain to 404. `openToken` guards against a stale image from a
// card the pointer has already left painting into the current dossier.
function loadBackdrop(p, item, openToken) {
  var stages = [];
  if (item.backdrop_available) stages.push("fanart");
  if (item.poster_available) stages.push("poster");

  var m = metaFor(item.state);
  // The gradient floor is always present underneath; .p-<key> picks the state hue.
  p.media.className = "hp-media p-" + m.cssKey;

  var img = p.img;
  // Detach any handlers from a previous open before re-binding.
  img.onload = null;
  img.onerror = null;

  if (stages.length === 0) {
    // Nothing on disk — show the gradient floor only, request nothing.
    img.removeAttribute("src");
    p.media.classList.remove("has-art");
    return;
  }

  var idx = 0;
  function tryStage() {
    if (openToken !== _openToken) return; // superseded by a newer open
    if (idx >= stages.length) {
      img.removeAttribute("src");
      p.media.classList.remove("has-art");
      return;
    }
    var kind = stages[idx];
    idx += 1;
    img.src =
      "/api/media-image/" + encodeURIComponent(item.id) + "?kind=" + kind;
  }

  img.onload = function () {
    if (openToken !== _openToken) return;
    p.media.classList.add("has-art"); // fade the backdrop in (CSS)
  };
  img.onerror = function () {
    if (openToken !== _openToken) return;
    p.media.classList.remove("has-art");
    tryStage(); // fall to the next source (or the gradient floor)
  };

  p.media.classList.remove("has-art");
  tryStage();
}

// ---------------------------------------------------------------------------
// Smart positioning: anchor to the CARD's rect (never the raw cursor, so it
// can't jitter), prefer the side with the most room, and clamp to the viewport
// so it never runs off-screen.
// ---------------------------------------------------------------------------
var VIEWPORT_MARGIN = 12; // keep this far from every screen edge
var CARD_GAP = 14; // breathing room between the card and the panel

function position(p, card) {
  var root = p.root;
  var cardRect = card.getBoundingClientRect();
  // Measure the panel now that its content is set (it's display:block but still
  // visually hidden via opacity, so it has a real box to measure).
  var pw = root.offsetWidth;
  var ph = root.offsetHeight;
  var vw = window.innerWidth;
  var vh = window.innerHeight;

  // Horizontal: prefer the side of the card with more space; flip if it won't fit.
  var spaceRight = vw - cardRect.right;
  var spaceLeft = cardRect.left;
  var left;
  if (spaceRight >= pw + CARD_GAP + VIEWPORT_MARGIN || spaceRight >= spaceLeft) {
    left = cardRect.right + CARD_GAP; // open to the right
  } else {
    left = cardRect.left - CARD_GAP - pw; // open to the left
  }
  // Clamp horizontally so a panel wider than the side's gap still stays on-screen.
  left = Math.max(
    VIEWPORT_MARGIN,
    Math.min(left, vw - pw - VIEWPORT_MARGIN)
  );

  // Vertical: align the panel's top to the card's top, then clamp so the whole
  // panel stays within the viewport (tall panels near the bottom ride up).
  var top = cardRect.top;
  top = Math.max(VIEWPORT_MARGIN, Math.min(top, vh - ph - VIEWPORT_MARGIN));

  root.style.left = Math.round(left) + "px";
  root.style.top = Math.round(top) + "px";
}

// ---------------------------------------------------------------------------
// Open / close.
// ---------------------------------------------------------------------------
var _openToken = 0; // bumped on every open; stale image/dwell callbacks bail
var _openCard = null; // the card the dossier is currently bound to (or null)

function openFor(card) {
  var item = card && card.__mvItem;
  if (!item) return;

  var p = buildPanel();
  var token = ++_openToken;
  _openCard = card;

  // Heading: real TMDB title via displayTitle ("Inception" / "Dark — S01E01"),
  // with the year appended when present.
  var heading = displayTitle(item);
  if (item.year) heading += "  ·  " + item.year;
  p.title.textContent = heading;

  // Episode line — only for an actual episode. We pair the SxxEyy marker (parsed
  // for the title) with the episode's own name when enrich wrote one.
  if (item.episode_title) {
    p.ep.textContent = String(item.episode_title);
    p.ep.classList.add("show");
  } else {
    p.ep.textContent = "";
    p.ep.classList.remove("show");
  }

  // Synopsis (clamped to a few lines in CSS). Interface voice when absent — a
  // statement, not an apology.
  var overview = (item.overview || "").trim();
  if (overview) {
    p.synopsis.textContent = overview;
    p.synopsis.classList.remove("empty");
  } else {
    p.synopsis.textContent = "No synopsis yet.";
    p.synopsis.classList.add("empty");
  }

  // Meta row: state chip (palette-coloured), humanized size, category word.
  var m = metaFor(item.state);
  p.stateChip.className = "hp-state b-" + m.cssKey;
  p.stateLabel.textContent = m.label;
  p.sizePill.textContent = humanSize(item.size_bytes);
  p.catPill.textContent = categoryWord(item.category);

  // Backdrop (lazy; only fires the request here, on open).
  loadBackdrop(p, item, token);

  // Make it measurable (block) but still hidden (opacity), position against the
  // card's rect, THEN reveal so the entrance animation runs from the final spot
  // — never a jump from a stale position.
  p.root.classList.add("is-measuring");
  position(p, card);
  // Next frame: drop the measuring guard and flip on .is-open so the CSS
  // entrance transition plays from the already-correct coordinates.
  window.requestAnimationFrame(function () {
    if (token !== _openToken) return;
    p.root.classList.remove("is-measuring");
    p.root.classList.add("is-open");
  });
}

function close() {
  _openCard = null;
  _openToken += 1; // invalidate any in-flight image/dwell callbacks
  if (!_panel) return;
  _panel.root.classList.remove("is-open");
  // Clear the measuring guard too, in case close() landed between openFor()'s
  // position() and its reveal frame (the frame then bails on the token); the
  // panel is already hidden (opacity:0), this just keeps its class state tidy.
  _panel.root.classList.remove("is-measuring");
  // Stop pulling on a backdrop we're no longer showing.
  _panel.img.onload = null;
  _panel.img.onerror = null;
}

// ---------------------------------------------------------------------------
// Dwell scheduling.
// ---------------------------------------------------------------------------
var _dwellTimer = 0;
var _dwellCard = null; // the card the pending dwell is for

function cancelDwell() {
  if (_dwellTimer) {
    window.clearTimeout(_dwellTimer);
    _dwellTimer = 0;
  }
  _dwellCard = null;
}

// Arm (or re-arm) the dwell for `card`. Moving within the SAME card keeps the
// existing timer running (no reset → a steady hover opens promptly); moving to a
// DIFFERENT card restarts the dwell so we never open the wrong one.
function armDwell(card) {
  if (card === _openCard) {
    // Already showing this card; nothing to schedule.
    cancelDwell();
    return;
  }
  if (card === _dwellCard && _dwellTimer) {
    return; // dwell already counting down for this exact card
  }
  cancelDwell();
  _dwellCard = card;
  _dwellTimer = window.setTimeout(function () {
    _dwellTimer = 0;
    var target = _dwellCard;
    _dwellCard = null;
    if (target && target.isConnected) openFor(target);
  }, DWELL_MS);
}

// ---------------------------------------------------------------------------
// Wiring (call once with #panel).
// ---------------------------------------------------------------------------
export function wireHoverPreview(container) {
  if (!container) return;
  // No hovering pointer (pure touch) → never wire. A tap keeps navigating the
  // card and the dossier never appears or sticks. Unlike the cursor glow, this
  // does NOT run under a coarse-only pointer.
  if (!hasHoverPointer()) return;

  // pointerover bubbles (unlike pointerenter), so one delegated listener on the
  // stable container sees the pointer crossing into any current-or-future card.
  container.addEventListener(
    "pointerover",
    function (e) {
      if (e.pointerType === "touch") return; // mouse/pen only
      var card = e.target && e.target.closest ? e.target.closest(".card") : null;
      if (!card || !container.contains(card)) return;
      armDwell(card);
    },
    { passive: true }
  );

  // pointermove keeps the dwell honest: if the pointer is moving over a card that
  // isn't the one we're showing/arming, (re)arm for it. Cheap — armDwell early-
  // outs when it's already the open or pending card, so this is a couple of
  // comparisons per move, no timer churn.
  container.addEventListener(
    "pointermove",
    function (e) {
      if (e.pointerType === "touch") return;
      var card = e.target && e.target.closest ? e.target.closest(".card") : null;
      if (!card || !container.contains(card)) return;
      armDwell(card);
    },
    { passive: true }
  );

  // Leaving a card: cancel a pending open and close an open dossier. pointerout
  // bubbles; relatedTarget is where the pointer went. If it moved to ANOTHER card
  // the pointerover above re-arms; if it left every card (or the grid), close.
  container.addEventListener(
    "pointerout",
    function (e) {
      if (e.pointerType === "touch") return;
      var fromCard =
        e.target && e.target.closest ? e.target.closest(".card") : null;
      if (!fromCard) return;
      var to = e.relatedTarget;
      var toCard = to && to.closest ? to.closest(".card") : null;
      if (toCard === fromCard) return; // still inside the same card
      // Cancel a dwell queued for the card we just left.
      if (_dwellCard === fromCard) cancelDwell();
      // Close the open dossier when we leave its card (a new card's dwell, if
      // any, will open the next one).
      if (_openCard === fromCard) close();
    },
    { passive: true }
  );

  // Backstop: the pointer leaving the whole grid (e.g. shooting up into the
  // header) cancels everything. pointerleave does NOT bubble, so bind it on the
  // container directly.
  container.addEventListener(
    "pointerleave",
    function () {
      cancelDwell();
      close();
    },
    { passive: true }
  );

  // Keyboard parity: focusing a card (Tab) opens its dossier; blurring closes it.
  // focusin/focusout bubble, so they delegate like the pointer events. This makes
  // the detail-window reachable without a mouse. (Reduced-motion users get the
  // same content, just without the entrance/Ken-Burns motion — see CSS.)
  container.addEventListener("focusin", function (e) {
    var card = e.target && e.target.closest ? e.target.closest(".card") : null;
    if (!card || !container.contains(card)) return;
    cancelDwell();
    openFor(card); // focus is deliberate — open immediately, no dwell
  });
  container.addEventListener("focusout", function (e) {
    var card = e.target && e.target.closest ? e.target.closest(".card") : null;
    if (!card) return;
    var to = e.relatedTarget;
    var toCard = to && to.closest ? to.closest(".card") : null;
    if (toCard === card) return; // focus moved within the same card
    if (_openCard === card) close();
  });

  // If the page scrolls or the window resizes while a dossier is open, its
  // card-anchored position would go stale. Re-anchor on scroll/resize, and close
  // if the card scrolled out of view. Passive + coalesced to one rAF.
  var reflowFrame = 0;
  function reflow() {
    reflowFrame = 0;
    if (!_openCard || !_panel) return;
    if (!_openCard.isConnected) {
      close();
      return;
    }
    position(_panel, _openCard);
  }
  function scheduleReflow() {
    if (!_openCard) return;
    if (!reflowFrame) reflowFrame = window.requestAnimationFrame(reflow);
  }
  window.addEventListener("scroll", scheduleReflow, { passive: true });
  window.addEventListener("resize", scheduleReflow, { passive: true });
}
