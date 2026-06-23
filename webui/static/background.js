// background.js — procedural deep-space / galaxy backdrop for the MediaVault web console.
//
// Self-contained ES module. On import it:
//   * injects its OWN <style> (never edits styles.css),
//   * creates a single full-viewport fixed <canvas> pinned at the very back
//     (z-index -10, pointer-events:none) so it never captures clicks/scroll,
//   * draws a deep-space gradient base (matching --bg #0b0f17), slow-drifting
//     nebula clouds, and a multi-layer parallax twinkling starfield.
//
// Hard constraints honored: vanilla, no build step, no external assets/CDNs
// (purely procedural canvas + gradients), XSS-safe (no innerHTML), passes
// `node --check`, honors prefers-reduced-motion, never interferes with the
// foreground UI. Everything is wrapped in try/catch so a canvas failure can
// never break the app.

(function initSpaceBackground() {
  "use strict";

  try {
    if (typeof document === "undefined" || !document.createElement) return;

    // ---- one-time guard (in case the module is somehow imported twice) ----
    if (document.getElementById("mv-space-bg")) return;

    // ---- capability check: bail gracefully if canvas/2d is unsupported ----
    var probe = document.createElement("canvas");
    if (!probe.getContext || !probe.getContext("2d")) return;

    // ===================================================================
    // Style injection (own <style>, never touches styles.css)
    // ===================================================================
    var style = document.createElement("style");
    style.id = "mv-space-bg-style";
    style.textContent =
      "#mv-space-bg{" +
      "position:fixed;top:0;left:0;width:100vw;height:100vh;" +
      "z-index:-10;pointer-events:none;display:block;" +
      // a deep, very-dark base so the canvas has something even before the
      // first paint (and if rAF is throttled). Matches --bg #0b0f17.
      "background:radial-gradient(120% 120% at 50% 0%,#0c1220 0%,#0b0f17 45%,#070a11 100%);" +
      "}";
    (document.head || document.documentElement).appendChild(style);

    // ===================================================================
    // Canvas element (back layer, inert)
    // ===================================================================
    var canvas = document.createElement("canvas");
    canvas.id = "mv-space-bg";
    canvas.setAttribute("aria-hidden", "true");
    var ctx = canvas.getContext("2d");
    if (!ctx) return;

    function attach() {
      // Put it as the very first child of <body> so it sits behind everything.
      if (document.body) {
        document.body.insertBefore(canvas, document.body.firstChild);
      }
    }
    if (document.body) {
      attach();
    } else {
      document.addEventListener("DOMContentLoaded", attach, { once: true });
    }

    // ===================================================================
    // Reduced-motion: render a static frame, no animation loop.
    // ===================================================================
    var reduceMQ =
      typeof window !== "undefined" && window.matchMedia
        ? window.matchMedia("(prefers-reduced-motion: reduce)")
        : null;
    var prefersReduced = !!(reduceMQ && reduceMQ.matches);

    // ===================================================================
    // Per-load randomization — "keeps changing often": new palette + star
    // seed on every page load, then continuous slow drift while running.
    // ===================================================================
    function rand(min, max) {
      return min + Math.random() * (max - min);
    }
    function pick(arr) {
      return arr[(Math.random() * arr.length) | 0];
    }

    // Tasteful space hues (HSL). Kept toward indigo/violet/teal/magenta so the
    // backdrop stays dark + readable; alpha is applied very low when drawn.
    var HUE_POOL = [
      252, // indigo
      268, // violet
      288, // magenta-violet
      210, // deep blue
      188, // teal (echoes --accent #38e0c8)
      168, // green-teal
      316, // magenta
    ];

    // Build 3–4 nebula blobs with random hue/position/size/drift.
    function makeNebulae() {
      var n = 3 + ((Math.random() * 2) | 0); // 3 or 4
      var blobs = [];
      // Pick a small, harmonious hue set so blobs feel related, not random soup.
      var baseHue = pick(HUE_POOL);
      for (var i = 0; i < n; i++) {
        var hue = i === 0 ? baseHue : pick(HUE_POOL);
        blobs.push({
          // normalized 0..1 position + radius (scaled to viewport at draw time)
          x: rand(0.05, 0.95),
          y: rand(0.05, 0.95),
          r: rand(0.35, 0.7),
          hue: hue,
          sat: rand(55, 85),
          light: rand(45, 60),
          // very low peak opacity -> stays dark & text stays readable
          alpha: rand(0.06, 0.13),
          // slow drift velocities (normalized units per second)
          vx: rand(-0.012, 0.012),
          vy: rand(-0.012, 0.012),
          // gentle breathing of radius
          phase: rand(0, Math.PI * 2),
          pulse: rand(0.05, 0.12),
        });
      }
      return blobs;
    }

    var nebulae = makeNebulae();

    // Starfield: three parallax depth layers. Density adapts to viewport area
    // & devicePixelRatio so an iPhone portrait isn't sparse and a 4K monitor
    // isn't over-rendered.
    var STAR_LAYERS = [
      { depth: 0.25, size: [0.5, 1.0], speed: 0.004, twinkle: 0.6, density: 0.00009 },
      { depth: 0.55, size: [0.8, 1.6], speed: 0.009, twinkle: 0.9, density: 0.00006 },
      { depth: 1.0, size: [1.2, 2.4], speed: 0.018, twinkle: 1.3, density: 0.00003 },
    ];

    var stars = []; // populated in rebuildStars()
    var cssW = 0,
      cssH = 0,
      dpr = 1;

    function rebuildStars() {
      stars = [];
      var area = cssW * cssH;
      for (var li = 0; li < STAR_LAYERS.length; li++) {
        var layer = STAR_LAYERS[li];
        // Cap per-layer count so huge displays stay cheap.
        var count = Math.min(700, Math.max(20, (area * layer.density) | 0));
        for (var s = 0; s < count; s++) {
          stars.push({
            layer: li,
            x: Math.random(), // normalized; multiplied by cssW at draw time
            y: Math.random(),
            size: rand(layer.size[0], layer.size[1]),
            // each star twinkles on its own phase/speed
            tphase: rand(0, Math.PI * 2),
            tspeed: rand(0.5, 1.6) * layer.twinkle,
            base: rand(0.35, 0.95), // base brightness
            // a faint tint on some stars for variety (mostly white-blue)
            tint: Math.random() < 0.18 ? pick(HUE_POOL) : null,
          });
        }
      }
    }

    // ===================================================================
    // Resize handling (debounced). Clamp DPR so retina doesn't over-render.
    // ===================================================================
    function applySize() {
      cssW = Math.max(1, window.innerWidth || document.documentElement.clientWidth || 1);
      cssH = Math.max(1, window.innerHeight || document.documentElement.clientHeight || 1);
      // Clamp DPR: 1.5 is plenty for a soft starfield; saves a lot of fill on
      // 3x phones / retina iPads.
      dpr = Math.min(1.5, (window.devicePixelRatio || 1));
      canvas.width = Math.round(cssW * dpr);
      canvas.height = Math.round(cssH * dpr);
      // Draw in CSS pixels; the transform maps to device pixels.
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      rebuildStars();
    }

    var resizeTimer = null;
    function onResize() {
      if (resizeTimer) clearTimeout(resizeTimer);
      resizeTimer = setTimeout(function () {
        resizeTimer = null;
        applySize();
        if (prefersReduced || paused) {
          // No running loop — repaint a single static frame at the new size.
          drawFrame(performance && performance.now ? performance.now() : Date.now());
        }
      }, 160);
    }
    window.addEventListener("resize", onResize, { passive: true });
    if (window.visualViewport) {
      // iOS toolbar collapse changes visualViewport without firing resize.
      window.visualViewport.addEventListener("resize", onResize, { passive: true });
    }

    // ===================================================================
    // Drawing
    // ===================================================================
    function drawBase() {
      // Vertical-ish deep-space gradient (canvas equivalent of the CSS base,
      // so the canvas fully owns its pixels and never shows transparency).
      var g = ctx.createLinearGradient(0, 0, cssW * 0.3, cssH);
      g.addColorStop(0, "#0c1220");
      g.addColorStop(0.5, "#0a0e16");
      g.addColorStop(1, "#070a11");
      ctx.fillStyle = g;
      ctx.fillRect(0, 0, cssW, cssH);
    }

    function drawNebulae(tSec) {
      // Soft additive-ish radial gradients. We use "lighter" so overlapping
      // blobs glow gently rather than muddy out — but keep alpha low.
      var prevOp = ctx.globalCompositeOperation;
      ctx.globalCompositeOperation = "lighter";
      for (var i = 0; i < nebulae.length; i++) {
        var b = nebulae[i];
        // drift (wrap softly within a padded range)
        var nx = b.x + b.vx * tSec;
        var ny = b.y + b.vy * tSec;
        nx = ((nx % 1) + 1) % 1;
        ny = ((ny % 1) + 1) % 1;
        var pulse = 1 + Math.sin(tSec * 0.15 + b.phase) * b.pulse;
        var cx = nx * cssW;
        var cy = ny * cssH;
        var rad = b.r * Math.max(cssW, cssH) * 0.8 * pulse;

        var grd = ctx.createRadialGradient(cx, cy, 0, cx, cy, rad);
        var core = "hsla(" + b.hue + "," + b.sat + "%," + b.light + "%,";
        grd.addColorStop(0, core + b.alpha + ")");
        grd.addColorStop(0.45, core + b.alpha * 0.45 + ")");
        grd.addColorStop(1, core + "0)");
        ctx.fillStyle = grd;
        ctx.fillRect(0, 0, cssW, cssH);
      }
      ctx.globalCompositeOperation = prevOp;
    }

    function drawStars(tSec) {
      for (var i = 0; i < stars.length; i++) {
        var st = stars[i];
        var layer = STAR_LAYERS[st.layer];
        // Parallax: drift each layer slowly leftward by depth-scaled speed.
        var drift = (tSec * layer.speed) % 1;
        var x = ((st.x - drift) % 1 + 1) % 1;
        var px = x * cssW;
        var py = st.y * cssH;

        // Twinkle: brightness oscillates around base.
        var tw = prefersReduced
          ? st.base
          : st.base * (0.6 + 0.4 * (0.5 + 0.5 * Math.sin(tSec * st.tspeed + st.tphase)));
        if (tw <= 0.02) continue;

        if (st.tint != null) {
          ctx.fillStyle = "hsla(" + st.tint + ",70%,80%," + tw.toFixed(3) + ")";
        } else {
          // mostly white-blue
          ctx.fillStyle = "rgba(224,234,255," + tw.toFixed(3) + ")";
        }
        ctx.beginPath();
        ctx.arc(px, py, st.size, 0, Math.PI * 2);
        ctx.fill();

        // A faint glow halo on the brightest near-layer stars only (cheap).
        if (st.layer === 2 && tw > 0.7) {
          ctx.fillStyle = "rgba(180,210,255," + (tw * 0.12).toFixed(3) + ")";
          ctx.beginPath();
          ctx.arc(px, py, st.size * 2.4, 0, Math.PI * 2);
          ctx.fill();
        }
      }
    }

    function drawFrame(nowMs) {
      // tSec drives all motion; for reduced-motion we pass a fixed seed so the
      // single static frame still looks composed (drift/twinkle frozen).
      var tSec = prefersReduced ? staticSeed : nowMs / 1000;
      drawBase();
      drawNebulae(tSec);
      drawStars(tSec);
    }

    // A fixed pseudo-time so the static (reduced-motion) frame is deterministic
    // per load but still varied across loads.
    var staticSeed = Math.random() * 1000;

    // ===================================================================
    // Animation loop — capped to ~30fps, paused when the tab is hidden.
    // ===================================================================
    var TARGET_FPS = 30;
    var FRAME_MS = 1000 / TARGET_FPS;
    var lastDraw = 0;
    var rafId = null;
    var paused = false;

    function loop(nowMs) {
      rafId = window.requestAnimationFrame(loop);
      if (nowMs - lastDraw < FRAME_MS) return; // throttle to target FPS
      lastDraw = nowMs;
      drawFrame(nowMs);
    }

    function start() {
      if (prefersReduced) return; // static only
      if (rafId != null) return;
      lastDraw = 0;
      rafId = window.requestAnimationFrame(loop);
    }
    function stop() {
      if (rafId != null) {
        window.cancelAnimationFrame(rafId);
        rafId = null;
      }
    }

    // Pause on tab hidden (save battery on iPad/iPhone); resume on visible.
    document.addEventListener("visibilitychange", function () {
      if (document.hidden) {
        paused = true;
        stop();
      } else {
        paused = false;
        start();
      }
    });

    // React to prefers-reduced-motion toggling at runtime.
    function onReduceChange() {
      prefersReduced = !!reduceMQ.matches;
      if (prefersReduced) {
        stop();
        drawFrame(performance && performance.now ? performance.now() : Date.now());
      } else {
        start();
      }
    }
    if (reduceMQ) {
      if (reduceMQ.addEventListener) reduceMQ.addEventListener("change", onReduceChange);
      else if (reduceMQ.addListener) reduceMQ.addListener(onReduceChange); // older Safari
    }

    // ===================================================================
    // Kick off
    // ===================================================================
    applySize();
    if (prefersReduced) {
      // single static composed frame, no loop
      drawFrame(performance && performance.now ? performance.now() : Date.now());
    } else if (document.hidden) {
      // start paused; draw one frame so it isn't blank if user returns slowly
      paused = true;
      drawFrame(performance && performance.now ? performance.now() : Date.now());
    } else {
      // draw an immediate first frame, then run the loop
      drawFrame(performance && performance.now ? performance.now() : Date.now());
      start();
    }
  } catch (e) {
    // Never let a backdrop failure break the app.
    if (typeof console !== "undefined" && console.warn) {
      console.warn("[background.js] disabled:", e);
    }
  }
})();
