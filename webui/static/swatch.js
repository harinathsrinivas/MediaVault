/* MediaVault Console — poster dominant-color extractor (IMP-E16 D1).
 *
 * Pulls ONE representative "film accent" colour out of a card's already-loaded
 * poster <img> so the card's ambient effects (cursor glow, rotating ring, scrim)
 * can tint to the artwork — Inception → amber, The Dark → cold blue — instead of
 * the fixed mint. Pure, side-effect-free, and cheap: card.js calls extractAccent()
 * exactly ONCE per card, on the poster's `load` event, then writes the result as
 * CSS custom properties on the card. Nothing here samples per-frame or per-hover.
 *
 * HOW IT STAYS CHEAP + ROBUST:
 *   • Hard downscale to 16×24 (≈ a poster's 2:3 aspect) on a single reused offscreen
 *     canvas, one drawImage blit, one getImageData read — 384 pixels, one pass.
 *   • The poster is served SAME-ORIGIN (/api/media-image/<id>), so the canvas is not
 *     tainted and the pixels are readable. Everything is still wrapped in try/catch:
 *     a tainted canvas / decode failure / missing 2d context returns null and the
 *     caller simply keeps the default mint — extraction never throws into render.
 *   • We want the FILM's accent, not the letterbox or the title text, so each pixel
 *     is filtered in HSL: near-black + near-white + low-saturation (grey) pixels are
 *     dropped. The survivors vote into 18 hue buckets (20° each) WEIGHTED by their
 *     saturation, so a vivid region outweighs a large muddy one. The dominant bucket
 *     (smoothed with its two neighbours so a colour straddling a boundary isn't
 *     split) gives a saturation-weighted average colour.
 *   • The result is pushed back into a PLEASANT band (saturation 0.40–0.85, lightness
 *     0.48–0.70 — the same vividness/brightness family as the mint default) so the
 *     tint always reads as a soft, premium aura rather than neon or mud. If too few
 *     coloured pixels survive, or the dominant colour is still washed out, we return
 *     null → the card keeps mint (the task's blessed "ugly/muddy → mint" fallback).
 *
 * Returns { accent: [r,g,b], bright: [r,g,b] } (ints 0–255) or null. `bright` is a
 * lighter, slightly more saturated sibling of the same hue for the ring's trailing
 * edge, so the rotating arc reads as one film-coloured highlight sweeping brighter.
 *
 * ES module. `node --check swatch.js` covers it (pure DOM/canvas + maths).
 */

"use strict";

var SAMPLE_W = 16;
var SAMPLE_H = 24;
var NBUCKETS = 18; // 20°-wide hue buckets

// Per-pixel filters (HSL space): drop the letterbox, the blown-out titles, and the
// greys, so only the film's actual colour votes.
var MIN_ALPHA = 128; // ignore (near-)transparent pixels
var SKIP_DARK_L = 0.12; // near-black: shadows / letterbox bars
var SKIP_LIGHT_L = 0.92; // near-white: title text / blown highlights
var SKIP_GRAY_S = 0.18; // desaturated: not a "colour"

// Reliability gates → null (keep mint) when the artwork isn't usefully colourful.
var MIN_GOOD_PIXELS = 10;
var MIN_RESULT_SAT = 0.2;

// Clamp the final accent into a tasteful, premium band (roughly the mint's own
// vividness/brightness) so no poster yields a neon or muddy tint.
var CLAMP_S_LO = 0.4;
var CLAMP_S_HI = 0.85;
var CLAMP_L_LO = 0.48;
var CLAMP_L_HI = 0.7;

// The brighter ring sibling: same hue, lifted lightness + a touch more saturation.
var BRIGHT_S_ADD = 0.06;
var BRIGHT_L_ADD = 0.14;
var BRIGHT_S_CAP = 0.9;
var BRIGHT_L_CAP = 0.8;

// One reused offscreen canvas for every extraction (created lazily; cards load one
// at a time, so a single 16×24 buffer is plenty and avoids per-card allocation).
var _canvas = null;
var _ctx = null;

function ensureCtx() {
  if (_ctx) return _ctx;
  try {
    _canvas = document.createElement("canvas");
    _canvas.width = SAMPLE_W;
    _canvas.height = SAMPLE_H;
    // willReadFrequently: we only ever read this canvas back, never composite it.
    _ctx = _canvas.getContext("2d", { willReadFrequently: true });
  } catch (e) {
    _ctx = null;
  }
  return _ctx;
}

function clamp(v, lo, hi) {
  return v < lo ? lo : v > hi ? hi : v;
}

// Standard RGB(0–255) → HSL. h in [0,360), s/l in [0,1].
function rgbToHsl(r, g, b) {
  r /= 255;
  g /= 255;
  b /= 255;
  var max = Math.max(r, g, b);
  var min = Math.min(r, g, b);
  var l = (max + min) / 2;
  if (max === min) return [0, 0, l]; // achromatic
  var d = max - min;
  var s = l > 0.5 ? d / (2 - max - min) : d / (max + min);
  var h;
  if (max === r) h = (g - b) / d + (g < b ? 6 : 0);
  else if (max === g) h = (b - r) / d + 2;
  else h = (r - g) / d + 4;
  return [h * 60, s, l];
}

// Standard HSL → RGB(0–255), rounded. h in [0,360), s/l in [0,1].
function hslToRgb(h, s, l) {
  h /= 360;
  if (s === 0) {
    var v = Math.round(l * 255);
    return [v, v, v];
  }
  var q = l < 0.5 ? l * (1 + s) : l + s - l * s;
  var p = 2 * l - q;
  return [
    Math.round(hue2rgb(p, q, h + 1 / 3) * 255),
    Math.round(hue2rgb(p, q, h) * 255),
    Math.round(hue2rgb(p, q, h - 1 / 3) * 255),
  ];
}

function hue2rgb(p, q, t) {
  if (t < 0) t += 1;
  if (t > 1) t -= 1;
  if (t < 1 / 6) return p + (q - p) * 6 * t;
  if (t < 1 / 2) return q;
  if (t < 2 / 3) return p + (q - p) * (2 / 3 - t) * 6;
  return p;
}

// Extract one tasteful accent (+ a brighter sibling) from a loaded same-origin
// poster <img>, or null on any failure / too-monochrome artwork. Never throws.
export function extractAccent(img) {
  try {
    if (!img || !img.naturalWidth || !img.naturalHeight) return null;
    var ctx = ensureCtx();
    if (!ctx) return null;

    ctx.clearRect(0, 0, SAMPLE_W, SAMPLE_H);
    ctx.drawImage(img, 0, 0, SAMPLE_W, SAMPLE_H); // hard one-pass downscale
    // Throws SecurityError on a tainted canvas; we're same-origin so it won't, but
    // the surrounding try/catch keeps a hostile/edge case from reaching render.
    var data = ctx.getImageData(0, 0, SAMPLE_W, SAMPLE_H).data;

    var wsum = new Array(NBUCKETS);
    var rsum = new Array(NBUCKETS);
    var gsum = new Array(NBUCKETS);
    var bsum = new Array(NBUCKETS);
    for (var k = 0; k < NBUCKETS; k += 1) {
      wsum[k] = rsum[k] = gsum[k] = bsum[k] = 0;
    }

    var good = 0;
    for (var i = 0; i < data.length; i += 4) {
      if (data[i + 3] < MIN_ALPHA) continue;
      var R = data[i];
      var G = data[i + 1];
      var B = data[i + 2];
      var hsl = rgbToHsl(R, G, B);
      var h = hsl[0];
      var s = hsl[1];
      var ll = hsl[2];
      if (ll < SKIP_DARK_L || ll > SKIP_LIGHT_L || s < SKIP_GRAY_S) continue;
      good += 1;
      var bi = Math.floor(h / 20) % NBUCKETS;
      if (bi < 0) bi = 0;
      var w = s; // saturation weight: vivid pixels speak louder than muddy ones
      wsum[bi] += w;
      rsum[bi] += R * w;
      gsum[bi] += G * w;
      bsum[bi] += B * w;
    }
    if (good < MIN_GOOD_PIXELS) return null;

    // Pick the dominant hue bucket, smoothing with its two circular neighbours so a
    // colour sitting on a bucket boundary isn't split across two and under-counted.
    var best = -1;
    var bestScore = -1;
    for (var j = 0; j < NBUCKETS; j += 1) {
      var left = wsum[(j - 1 + NBUCKETS) % NBUCKETS];
      var right = wsum[(j + 1) % NBUCKETS];
      var score = wsum[j] + 0.5 * (left + right);
      if (score > bestScore) {
        bestScore = score;
        best = j;
      }
    }
    if (best < 0) return null;

    // Representative colour = saturation-weighted average across the winning bucket
    // and its two neighbours (a stable read of the dominant hue family).
    var idx = [(best - 1 + NBUCKETS) % NBUCKETS, best, (best + 1) % NBUCKETS];
    var WR = 0;
    var WG = 0;
    var WB = 0;
    var WW = 0;
    for (var t = 0; t < idx.length; t += 1) {
      var b3 = idx[t];
      WR += rsum[b3];
      WG += gsum[b3];
      WB += bsum[b3];
      WW += wsum[b3];
    }
    if (WW <= 0) return null;

    var rep = rgbToHsl(WR / WW, WG / WW, WB / WW);
    var H = rep[0];
    var S = rep[1];
    var L = rep[2];
    // Even the dominant colour can come out washed out → keep mint rather than tint
    // the card a sad grey (the task's saturation-threshold mint fallback).
    if (S < MIN_RESULT_SAT) return null;

    var Sc = clamp(S, CLAMP_S_LO, CLAMP_S_HI);
    var Lc = clamp(L, CLAMP_L_LO, CLAMP_L_HI);
    var accent = hslToRgb(H, Sc, Lc);
    var bright = hslToRgb(
      H,
      Math.min(BRIGHT_S_CAP, Sc + BRIGHT_S_ADD),
      Math.min(BRIGHT_L_CAP, Lc + BRIGHT_L_ADD)
    );
    return { accent: accent, bright: bright };
  } catch (e) {
    return null;
  }
}
