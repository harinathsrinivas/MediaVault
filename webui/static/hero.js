/* MediaVault Console — cinematic parallax hero strip (IMP-E16 D4).
 *
 * A wide backdrop band that opens each media-type tab on its most characteristic
 * content: a small rotating set of the tab's "recently archived" / featured titles
 * shown one at a time as a full-bleed dimmed cover with a slow Ken-Burns drift, a
 * gentle scroll parallax, a left-aligned title / overview / state overlay, and a
 * crossfading auto-rotation. ES module; `node --check hero.js` covers it (no build
 * step — hard constraint).
 *
 * MOUNT + SELECTION
 *   wireHero(container, getModel, getCategory, onActivate) builds the band ONCE
 *   inside the `#hero` <section> (placed above #panel in index.html) and returns a
 *   small controller { refresh }. app.js calls refresh() on init, on every tab
 *   change, and after each model reload. refresh() re-picks the featured set for the
 *   ACTIVE category from the loaded model:
 *     1. titles that HAVE a backdrop (backdrop_available), ARCHIVED first, then any;
 *     2. else titles with a poster (poster_available), same ordering;
 *     3. else nothing — the band collapses (hidden) for that tab.
 *   Capped at MAX_FEATURED and lightly de-duped by show/movie group so one series
 *   doesn't fill the whole rotation. A cheap signature de-dupes refresh() so a sort
 *   / sub-view change (same category + same set) never restarts the rotation.
 *
 * THE CINEMATIC EFFECT
 *   • Ken-Burns — a slow scale 1.04→1.10 + few-% pan over ~16s, CSS-animated on the
 *     FRONT backdrop layer only (compositor-friendly transform); each new slide
 *     restarts it fresh. Paused (animation-play-state) when the tab is hidden.
 *   • Parallax — on scroll the backdrop layer translates slower than the page
 *     (transform-only, rAF-coalesced, clamped to a safe fraction of the band so it
 *     never reveals an edge). A separate wrapper element carries the parallax
 *     transform so it never fights the Ken-Burns transform on the <img>.
 *   • Auto-rotate — every ROTATE_MS the band crossfades to the next slide using TWO
 *     reused <img> layers; only the active + the next image are ever requested.
 *     Pauses on hover and when the tab is hidden; dot indicators jump to any slide.
 *   • Click / Enter on the band jumps to that title (onActivate → app.js jumpToItem:
 *     switch tab + sub-view, scroll the card into view, pulse it, open its dossier).
 *
 * PERF + SAFETY
 *   Exactly two <img> elements are reused (src swapped per slide); the image
 *   requests fire only on rotation, guarded by a per-show token so a stale load can
 *   never paint into the wrong slide. A backdrop that 404s waterfalls fanart →
 *   poster, then skips the slide. The animation pauses on visibilitychange (mirrors
 *   background.js's discipline). prefers-reduced-motion → NO Ken-Burns, NO
 *   auto-rotate, NO parallax: a single static featured backdrop is shown.
 *
 * XSS-safe: every text node is set via textContent; the only interpolated value is
 * the library's own canonical id, URL-encoded into the /api/media-image path. The
 * hero is in normal flow above #panel, so it stays BEHIND the fixed overlays and
 * does NOT carry #panel's view-transition-name (it rides the held-still ::*-(root)
 * snapshot, so a tab switch cuts it instantly rather than morphing/flickering).
 *
 * Stable hooks for inspection / the verify harness: `#hero`, `.hero-backdrop`,
 * `.hero-title`, and the `.is-front` active-state class on the live backdrop layer.
 */

"use strict";

import { displayTitle } from "./title.js";
import { metaFor } from "./data.js";

// Up to this many titles rotate in the band.
var MAX_FEATURED = 5;
// Crossfade to the next slide this often (ms). Pauses on hover / hidden tab.
var ROTATE_MS = 7000;
// Backdrop translate per scrolled pixel — small, so the band "lags" the page
// subtly. Clamped in applyParallax to a fraction of the band height so the
// oversized parallax layer never exposes a gap at the band's edge.
var PARALLAX_FACTOR = 0.15;

// One shared reduced-motion query (mirrors background.js / preview.js). prefers-
// reduced-motion gates ALL hero motion: Ken-Burns, auto-rotate, and parallax.
var reduceMQ =
  typeof window !== "undefined" && window.matchMedia
    ? window.matchMedia("(prefers-reduced-motion: reduce)")
    : null;

function prefersReducedMotion() {
  return !!(reduceMQ && reduceMQ.matches);
}

function imageUrl(id, kind) {
  return "/api/media-image/" + encodeURIComponent(id) + "?kind=" + kind;
}

// Wide /api/media-image is the cover; an item is featurable when it reports a
// backdrop (preferred) OR at least a poster (fallback). Build the band's DOM once,
// wire its listeners, and return { refresh } for app.js to call on tab/model change.
export function wireHero(container, getModel, getCategory, onActivate) {
  if (!container) {
    return { refresh: function () {} };
  }

  var els = buildHeroDom(container);

  // ---- instance state (closure) -------------------------------------------
  var featured = []; // [{ item, kind, broken? }] for the active category
  var current = -1; // index into `featured` currently shown (-1 = none)
  var lastSig = null; // de-dupe signature: category + the featured id list
  var rotateTimer = 0; // setTimeout handle for the auto-rotate tick
  var showToken = 0; // bumped per show(); stale image load/error callbacks bail
  var frontIsA = true; // which of the two backdrop <img> layers is the front one
  var hovering = false; // pointer is over the band → pause rotation
  var hidden = !!(typeof document !== "undefined" && document.hidden); // tab hidden
  var parallaxFrame = 0; // rAF handle coalescing scroll → one transform write

  container.classList.toggle("is-paused", hidden);

  // ---- featured selection --------------------------------------------------

  // Order a candidate pool: ARCHIVED first (the user's "recently archived"), then
  // the rest in model order; de-dupe by show/movie group (parent_id when present,
  // else the id) so one series' episodes don't fill the whole rotation; cap.
  function orderAndPick(rows, kind) {
    var archived = [];
    var rest = [];
    rows.forEach(function (it) {
      (it.state === "ARCHIVED" ? archived : rest).push(it);
    });
    var ordered = archived.concat(rest);
    var seen = Object.create(null);
    var out = [];
    for (var i = 0; i < ordered.length && out.length < MAX_FEATURED; i += 1) {
      var it = ordered[i];
      var key = it.parent_id != null ? "p:" + it.parent_id : "i:" + it.id;
      if (seen[key]) continue;
      seen[key] = true;
      out.push({ item: it, kind: kind });
    }
    return out;
  }

  function pickFeatured(model, category) {
    var rows = (model && model.items ? model.items : []).filter(function (it) {
      return it && it.category === category;
    });
    var withBackdrop = rows.filter(function (it) {
      return it.backdrop_available;
    });
    if (withBackdrop.length) return orderAndPick(withBackdrop, "fanart");
    var withPoster = rows.filter(function (it) {
      return it.poster_available;
    });
    if (withPoster.length) return orderAndPick(withPoster, "poster");
    return [];
  }

  // First non-broken slide AFTER `fromIndex` (wraps); -1 when every slide's image
  // has failed (so the caller hides the band rather than spinning).
  function nextLiveSlide(fromIndex) {
    var n = featured.length;
    for (var step = 1; step <= n; step += 1) {
      var j = (((fromIndex + step) % n) + n) % n;
      if (!featured[j].broken) return j;
    }
    return -1;
  }

  // ---- showing a slide (crossfade between the two reused <img> layers) ------

  // Load slide `index`'s cover into the BACK layer; on load, paint the overlay +
  // crossfade it to the front. Only the active + this next image are ever
  // requested. A per-call token drops a stale resolve. A cover that 404s
  // waterfalls fanart → poster, then the slide is marked broken and skipped.
  function show(index) {
    var n = featured.length;
    if (!n) return;
    index = ((index % n) + n) % n;
    var slide = featured[index];
    var token = ++showToken;

    var backImg = frontIsA ? els.imgB : els.imgA;

    // Per-slide source waterfall. A fanart pick falls back to its poster; a poster
    // pick (no backdrop on disk) has only the one source.
    var stages = [];
    if (slide.kind === "fanart") {
      stages.push("fanart");
      if (slide.item.poster_available) stages.push("poster");
    } else {
      stages.push("poster");
    }

    // Try stage `k`; on error, fall to the next source; when exhausted, mark the
    // slide broken and skip to the next live one (or hide if all failed).
    function attempt(k) {
      if (token !== showToken) return; // superseded by a newer show()
      if (k >= stages.length) {
        slide.broken = true;
        var next = nextLiveSlide(index);
        if (next < 0) {
          hideHero();
          return;
        }
        show(next);
        return;
      }
      var url = imageUrl(slide.item.id, stages[k]);
      // If the back layer already holds this exact decoded source (e.g. rotating
      // back to a recent slide), the `load` event would NOT re-fire for an
      // unchanged, already-complete src — commit straight away instead of stalling.
      if (backImg.__heroUrl === url && backImg.complete && backImg.naturalWidth > 0) {
        commit(index, backImg);
        return;
      }
      backImg.onload = function () {
        if (token !== showToken) return;
        commit(index, backImg);
      };
      backImg.onerror = function () {
        if (token !== showToken) return;
        attempt(k + 1);
      };
      backImg.__heroUrl = url;
      backImg.src = url;
    }
    attempt(0);
  }

  // Promote `loadedImg` to the front (crossfade in via CSS), demote the other, and
  // paint the text overlay + dot for `index`. Runs only from a live (non-stale) load.
  function commit(index, loadedImg) {
    renderOverlay(featured[index].item);
    updateDots(index);
    var otherImg = loadedImg === els.imgA ? els.imgB : els.imgA;
    loadedImg.classList.add("is-front");
    otherImg.classList.remove("is-front");
    frontIsA = loadedImg === els.imgA;
    current = index;
    scheduleParallax(); // keep the new band aligned with the current scroll
  }

  function renderOverlay(item) {
    var name = displayTitle(item);
    els.title.textContent = item.year ? name + "  ·  " + item.year : name;
    els.hit.setAttribute(
      "aria-label",
      "Open " + name + (item.year ? " (" + item.year + ")" : "")
    );

    var m = metaFor(item.state);
    els.chip.className = "hero-chip s-" + m.cssKey;
    els.chipLabel.textContent = m.short;

    // ARCHIVED is the user's "recently archived"; everything else is just featured.
    els.kicker.textContent =
      item.state === "ARCHIVED" ? "Recently archived" : "Featured";

    var overview = (item.overview || "").trim();
    if (overview) {
      els.overview.textContent = overview;
      els.overview.hidden = false;
    } else {
      els.overview.textContent = "";
      els.overview.hidden = true;
    }
  }

  // ---- dot indicators ------------------------------------------------------

  function buildDots() {
    els.dots.textContent = "";
    if (featured.length <= 1) return; // a single slide needs no indicators
    for (var i = 0; i < featured.length; i += 1) {
      els.dots.appendChild(makeDot(i));
    }
  }

  function makeDot(idx) {
    var b = document.createElement("button");
    b.type = "button";
    b.className = "hero-dot";
    b.setAttribute(
      "aria-label",
      "Show featured " + (idx + 1) + " of " + featured.length
    );
    b.addEventListener("click", function () {
      show(idx); // manual jump (its own element; never reaches the .hero-hit)
      maybeStartRotate(); // restart the dwell so it doesn't immediately advance
    });
    return b;
  }

  function updateDots(index) {
    var kids = els.dots.children;
    for (var i = 0; i < kids.length; i += 1) {
      var on = i === index;
      kids[i].classList.toggle("is-active", on);
      if (on) kids[i].setAttribute("aria-current", "true");
      else kids[i].removeAttribute("aria-current");
    }
  }

  // ---- rotation ------------------------------------------------------------

  function stopRotate() {
    if (rotateTimer) {
      window.clearTimeout(rotateTimer);
      rotateTimer = 0;
    }
  }

  function maybeStartRotate() {
    stopRotate();
    if (hovering || hidden || prefersReducedMotion()) return;
    if (featured.length <= 1) return;
    rotateTimer = window.setTimeout(tick, ROTATE_MS);
  }

  function tick() {
    rotateTimer = 0;
    var next = nextLiveSlide(current);
    if (next >= 0) show(next);
    maybeStartRotate();
  }

  // ---- show / hide the whole band ------------------------------------------

  function showHero() {
    container.hidden = false;
  }

  function hideHero() {
    container.hidden = true;
    stopRotate();
    // Drop both layers so a later re-show (e.g. switching back to a featurable
    // tab) opens on the dark floor, never a stale image from another category.
    els.imgA.classList.remove("is-front");
    els.imgB.classList.remove("is-front");
    current = -1;
  }

  // ---- scroll parallax (transform-only, rAF-coalesced) ---------------------

  function scheduleParallax() {
    if (parallaxFrame) return;
    parallaxFrame = window.requestAnimationFrame(applyParallax);
  }

  function applyParallax() {
    parallaxFrame = 0;
    if (prefersReducedMotion()) {
      els.parallax.style.transform = ""; // static: no parallax under reduced motion
      return;
    }
    if (container.hidden) return;
    var y = window.scrollY || window.pageYOffset || 0;
    var band = container.offsetHeight || 1;
    var maxOffset = band * 0.2; // stay within the layer's vertical bleed (CSS)
    var off = y * PARALLAX_FACTOR;
    if (off < 0) off = 0;
    if (off > maxOffset) off = maxOffset;
    els.parallax.style.transform = "translate3d(0," + off.toFixed(1) + "px,0)";
  }

  // ---- listeners (wired once) ---------------------------------------------

  // Click / Enter anywhere on the band (the transparent full-bleed .hero-hit
  // button) jumps to the shown title. The dots sit above it as separate elements,
  // so a dot click never reaches here.
  els.hit.addEventListener("click", function () {
    if (current >= 0 && featured[current] && typeof onActivate === "function") {
      onActivate(featured[current].item.id);
    }
  });

  // Hover pauses ONLY the auto-rotate (the Ken-Burns keeps drifting so the band
  // stays alive); leaving resumes it.
  container.addEventListener("pointerenter", function () {
    hovering = true;
    stopRotate();
  });
  container.addEventListener("pointerleave", function () {
    hovering = false;
    maybeStartRotate();
  });

  // Hidden tab: stop the rotation AND freeze the Ken-Burns (battery), mirroring
  // background.js. Visible again: resume both and re-align the parallax.
  if (typeof document !== "undefined") {
    document.addEventListener("visibilitychange", function () {
      hidden = !!document.hidden;
      container.classList.toggle("is-paused", hidden);
      if (hidden) {
        stopRotate();
      } else {
        maybeStartRotate();
        scheduleParallax();
      }
    });
  }

  window.addEventListener("scroll", scheduleParallax, { passive: true });
  window.addEventListener("resize", scheduleParallax, { passive: true });

  // React to prefers-reduced-motion toggling at runtime: stop/clear motion when it
  // turns on, (re)start the rotation when it turns off. The Ken-Burns itself is
  // gated purely in CSS, so it follows the query with no JS here.
  if (reduceMQ) {
    var onReduceChange = function () {
      stopRotate();
      scheduleParallax(); // resets the transform when motion is now reduced
      maybeStartRotate();
    };
    if (reduceMQ.addEventListener) {
      reduceMQ.addEventListener("change", onReduceChange);
    } else if (reduceMQ.addListener) {
      reduceMQ.addListener(onReduceChange); // older Safari
    }
  }

  // ---- public: re-pick for the active category -----------------------------

  // Re-evaluate the featured set for the CURRENT (model, category). A cheap
  // signature (category + the featured id list) means a sort / sub-view change —
  // same category, same set — is a no-op that leaves the rotation undisturbed; a
  // real change (tab switch, post-job model reload) re-picks and restarts.
  function refresh() {
    var model = typeof getModel === "function" ? getModel() : null;
    var category = typeof getCategory === "function" ? getCategory() : null;
    var picked = pickFeatured(model, category);
    var sig =
      String(category) +
      "::" +
      picked
        .map(function (f) {
          return f.item.id;
        })
        .join(",");
    if (sig === lastSig) return;
    lastSig = sig;
    featured = picked;

    if (!featured.length) {
      hideHero();
      return;
    }
    showHero();
    buildDots();
    current = -1;
    show(0);
    maybeStartRotate();
    scheduleParallax();
  }

  return { refresh: refresh };
}

// ---------------------------------------------------------------------------
// DOM construction (once). Layered front-to-back inside #hero:
//   .hero-parallax (the JS-translated layer)  →  two .hero-backdrop <img>s
//   .hero-scrim    (fixed legibility + bottom blend, never parallaxes)
//   .hero-hit      (transparent full-bleed <button>: click/Enter → jump)
//   .hero-overlay  (pointer-events:none text block; clicks fall through to .hero-hit)
//   .hero-dots     (the indicator buttons, above everything)
// ---------------------------------------------------------------------------
function buildHeroDom(container) {
  container.textContent = "";
  container.classList.add("hero-strip");
  container.setAttribute("role", "region");
  container.setAttribute("aria-label", "Featured titles");

  var parallax = document.createElement("div");
  parallax.className = "hero-parallax";

  var imgA = makeBackdropImg();
  var imgB = makeBackdropImg();
  parallax.appendChild(imgA);
  parallax.appendChild(imgB);
  container.appendChild(parallax);

  var scrim = document.createElement("div");
  scrim.className = "hero-scrim";
  scrim.setAttribute("aria-hidden", "true");
  container.appendChild(scrim);

  var hit = document.createElement("button");
  hit.type = "button";
  hit.className = "hero-hit";
  container.appendChild(hit);

  var overlay = document.createElement("div");
  overlay.className = "hero-overlay";

  var kicker = document.createElement("div");
  kicker.className = "hero-kicker";
  overlay.appendChild(kicker);

  var title = document.createElement("h2");
  title.className = "hero-title";
  overlay.appendChild(title);

  var meta = document.createElement("div");
  meta.className = "hero-meta";
  var chip = document.createElement("span");
  chip.className = "hero-chip";
  var chipDot = document.createElement("span");
  chipDot.className = "hero-chip-dot";
  var chipLabel = document.createElement("span");
  chipLabel.className = "hero-chip-label";
  chip.appendChild(chipDot);
  chip.appendChild(chipLabel);
  meta.appendChild(chip);
  overlay.appendChild(meta);

  var overview = document.createElement("p");
  overview.className = "hero-overview";
  overlay.appendChild(overview);

  container.appendChild(overlay);

  var dots = document.createElement("div");
  dots.className = "hero-dots";
  container.appendChild(dots);

  return {
    container: container,
    parallax: parallax,
    imgA: imgA,
    imgB: imgB,
    scrim: scrim,
    hit: hit,
    overlay: overlay,
    kicker: kicker,
    title: title,
    chip: chip,
    chipDot: chipDot,
    chipLabel: chipLabel,
    overview: overview,
    dots: dots,
  };
}

function makeBackdropImg() {
  var img = document.createElement("img");
  img.className = "hero-backdrop";
  img.alt = "";
  img.decoding = "async";
  img.draggable = false;
  return img;
}
