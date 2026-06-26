/* MediaVault Console — cinematic detail-window / "dossier" (IMP-E16).
 *
 * THE SIGNATURE "aweee" MOMENT. Resting the pointer on any card — movie, series,
 * episode, or anime — for a short dwell auto-opens a LARGE, translucent glass
 * "dossier": a wide hero backdrop band with the title overlaid on a scrim, the
 * real title + year, the episode line when it's an episode, the synopsis, and a
 * tight meta row (state · size · category). A personal film vault, so it reads
 * like a now-showing card — but cinematic in scale (it is deliberately allowed to
 * rise UP over the header / tabs chrome while a leaf is hovered).
 *
 * TWO-PHASE PAINT (IMP-E16 enrichment):
 *   1. INSTANT — on open we render everything already in the items_payload row
 *      (backdrop + title + year + episode + the short synopsis + state/size/
 *      category) so the dossier appears with zero latency.
 *   2. RICH — the same instant we ALSO lazy-fetch GET /api/detail/{id} (the
 *      authFetch wrapper carries the access token). When it resolves AND the
 *      preview is still showing the SAME open (open-token guard, exactly like the
 *      backdrop image), we enrich the panel with ★ rating + vote count, genre
 *      chips, runtime, the tagline, the FULL overview (replacing the clamped
 *      short one), top cast, director(s)/creator(s), show seasons·episodes·
 *      networks / episode air-date·S·E, and a clickable IMDb / TMDB link row.
 *      When the title has been refreshed online the detail ALSO carries external
 *      critic scores (`ratings`: IMDb / Rotten Tomatoes / Metacritic), an MPAA
 *      certificate (`rated`), an awards summary (`awards`) and box office
 *      (`boxoffice`); those paint a prominent ratings-COIN strip, a certificate
 *      chip, a 🏆 awards line and a box-office meta pill — all OPTIONAL, so a
 *      not-yet-refreshed title's dossier looks exactly as it did before.
 *      A 404 ("no tmdb_id"), a fetch error, or a stale open → we simply keep the
 *      basic dossier (no error flash). Per-id detail is cached in memory so
 *      re-hovering the same card never refetches.
 *
 * PERSISTENT + INTERACTIVE (IMP-E16 follow-up): the panel is pointer-events:AUTO
 * so its contents — chiefly the IMDb / TMDB chips — are clickable. Leaving the
 * card no longer closes it instantly: an "active region" = (the hovered card) OR
 * (the panel itself), and leaving BOTH starts a short close-grace timer; entering
 * either cancels it. So the user can glide card→panel and click a link without it
 * vanishing. The hrefs are validated (must start with https://www.imdb.com or
 * https://www.themoviedb.org) before being set, and open in a new tab with
 * rel=noopener. A small ✕ closes it.
 *
 * DELEGATION (mirrors glow.js): ONE set of listeners on the stable #panel
 * (created once in index.html; only child-cleared on re-render), so every
 * freshly-rendered card — flat grid AND grouped-tree leaf — is covered with no
 * per-card listener to add or leak across sort / tab / sub-view switches and the
 * post-job /api/items refresh. The hovered .card is mapped back to its item via
 * `card.__mvItem`, stamped by buildCard (card.js).
 *
 * TWO PRESENTATIONS, ONE CONTENT (IMP-E16 mobile):
 *   • DESKTOP-POINTER — gated on `(any-hover: hover) and (any-pointer: fine)`. A
 *     dwell over a card opens the dossier ANCHORED beside the card (flips to the
 *     side with room, may overlap the header). Keyboard focus opens it too.
 *   • TOUCH — when that gate is FALSE we wire a LONG-PRESS instead: holding a card
 *     ~500ms (without sliding > ~10px, which would be a scroll) opens the SAME
 *     dossier as a CENTERED MODAL over a dim backdrop, dismissable by tapping the
 *     backdrop, the ✕, or Escape. The long-press suppresses the click it would
 *     otherwise generate (so it never trips a card button); a normal short tap is
 *     untouched and still behaves as today.
 *   Both presentations share ONE render path (renderInto); they differ only in
 *   positioning, the backdrop, and a `.is-modal` class.
 *
 * PERF: exactly ONE reusable panel element is built (lazily, on the first open),
 * its contents + backdrop src are swapped per open, and the fanart + the
 * /api/detail request only fire when a preview actually opens — never
 * speculatively on render. A per-open token ignores late image-load AND late
 * detail-fetch results from a superseded card so neither can paint into the wrong
 * dossier.
 *
 * A11y: keyboard focus on a card ALSO opens it (focusin) and blur closes it, so
 * the dossier isn't mouse-only. prefers-reduced-motion → instant open, no
 * transform, no Ken-Burns drift (handled in CSS; this module just toggles state).
 *
 * XSS-safe: every text node is set via textContent; the only interpolated values
 * are the library's own canonical id (URL-encoded into the image + detail paths)
 * and the IMDb/TMDB hrefs, which are validated against an exact https:// origin
 * allow-list before assignment. ES module; `node --check preview.js` covers it.
 */

"use strict";

import { metaFor, humanSize, CATEGORY_META } from "./data.js";
import { displayTitle } from "./title.js";
import { authFetch } from "./auth.js";

// Rest-this-long over a card before the dossier opens. Long enough that sweeping
// the pointer ACROSS the grid to reach something never flashes a dozen panels;
// short enough that a deliberate hover feels responsive.
var DWELL_MS = 380;

// Grace window after the pointer leaves BOTH the card and the panel before the
// dossier closes — long enough to glide across the small card→panel gap without
// it vanishing, short enough that it doesn't linger once you've truly left.
var CLOSE_GRACE_MS = 140;

// Touch long-press: hold a card this long (without sliding past the slop) to open
// the modal dossier. ~500ms is the familiar "press-and-hold" feel.
var LONGPRESS_MS = 500;
// Slide further than this between pointerdown and the timer firing and it's a
// scroll/drag, not a long-press — cancel.
var LONGPRESS_SLOP_PX = 10;

// A true hovering pointer is AVAILABLE (desktop mouse / trackpad / pen). Uses
// any-hover/any-pointer so a mouse on a touch-capable Windows box (PRIMARY
// pointer reported coarse) still counts; on a pure-touch phone both are false and
// we take the long-press path instead. Mirrors glow.js exactly.
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

// "148" -> "2h 28m"; "47" -> "47m"; missing/0 -> "". Pure formatting.
function formatRuntime(minutes) {
  var n = Number(minutes);
  if (!isFinite(n) || n <= 0) return "";
  var h = Math.floor(n / 60);
  var m = Math.round(n % 60);
  if (h <= 0) return m + "m";
  if (m <= 0) return h + "h";
  return h + "h " + m + "m";
}

// "1,284" — thousands-separated vote count for the faint ★ suffix. Defensive.
function formatVotes(n) {
  var v = Number(n);
  if (!isFinite(v) || v <= 0) return "";
  try {
    return v.toLocaleString("en-US");
  } catch (e) {
    return String(Math.round(v));
  }
}

// "2026-06-25" -> "Jun 25, 2026"; passes through anything it can't parse so a
// non-ISO air_date never throws or shows "Invalid Date".
function formatDate(value) {
  var s = String(value || "").trim();
  if (!s) return "";
  var m = /^(\d{4})-(\d{2})-(\d{2})/.exec(s);
  if (!m) return s;
  var months = [
    "Jan", "Feb", "Mar", "Apr", "May", "Jun",
    "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
  ];
  var mi = Number(m[2]) - 1;
  if (mi < 0 || mi > 11) return s;
  return months[mi] + " " + Number(m[3]) + ", " + m[1];
}

// "S03E07" from season/episode numbers (defensive: omits a missing half).
function formatSxxEyy(season, episode) {
  function pad(n) {
    var x = String(n);
    return x.length < 2 ? "0" + x : x;
  }
  var out = "";
  if (season != null && season !== "") out += "S" + pad(season);
  if (episode != null && episode !== "") out += "E" + pad(episode);
  return out;
}

// Best-effort: the FIRST number in a string ("87%" -> 87, "74" -> 74, "8.8" ->
// 8.8). NaN when there's no digit run — callers treat that as "no colour band"
// (e.g. a non-numeric Rotten Tomatoes / Metacritic value renders neutrally).
function leadingNumber(value) {
  var m = /(\d+(?:\.\d+)?)/.exec(String(value || ""));
  return m ? Number(m[1]) : NaN;
}

// "$292,587,330" -> "$292M"; "$1,250,000,000" -> "$1.2B". Pulls the magnitude out
// of OMDb's formatted figure and TRUNCATES (matching the product spec's examples);
// a small (< $1M) or unparseable amount is shown as the raw string unchanged.
function formatBoxOffice(value) {
  var raw = String(value || "").trim();
  if (!raw) return "";
  var n = Number(raw.replace(/[^0-9.]/g, ""));
  if (!isFinite(n) || n <= 0) return raw;
  if (n >= 1e9) return "$" + Math.floor(n / 1e8) / 10 + "B";
  if (n >= 1e6) return "$" + Math.floor(n / 1e6) + "M";
  return raw;
}

// The IMDb / TMDB links are interactive, so we are strict about what we will turn
// into a real href: an exact-origin https:// allow-list (anything else is omitted).
function safeExternalUrl(url) {
  var s = String(url || "").trim();
  if (
    s.indexOf("https://www.imdb.com/") === 0 ||
    s.indexOf("https://www.themoviedb.org/") === 0
  ) {
    return s;
  }
  return "";
}

// ---------------------------------------------------------------------------
// In-memory per-id detail cache.
// ---------------------------------------------------------------------------
//
// Keyed by item id. A successful payload is stored as-is; a 404 / "no tmdb_id"
// is stored as the NO_DETAIL sentinel so re-hovering a tmdb-less card never
// refetches. A transient network/parse error is NOT cached (we leave the id
// absent so a later hover can retry). The cache lives for the page lifetime.
var _detailCache = Object.create(null);
var NO_DETAIL = { __none: true };

// ---------------------------------------------------------------------------
// The single reusable panel (+ its modal backdrop).
// ---------------------------------------------------------------------------
//
// Built once on the first open and reused for the lifetime of the page. Returns a
// handle of the parts we rewrite per open so we never re-query the DOM mid-hover.
var _panel = null;

function buildPanel() {
  if (_panel) return _panel;

  // Dim full-screen backdrop, used ONLY in the mobile modal presentation. Always
  // in the DOM (cheap, empty) but display:none until .show; tapping it dismisses.
  var backdrop = document.createElement("div");
  backdrop.className = "hover-preview-backdrop";
  backdrop.setAttribute("aria-hidden", "true");
  document.body.appendChild(backdrop);

  var root = document.createElement("aside");
  root.id = "hover-preview";
  root.className = "hover-preview";
  root.setAttribute("aria-hidden", "true"); // informational mirror of the card
  // Interactive so the IMDb/TMDB chips + ✕ are clickable. (Mirrored in CSS; this
  // is belt-and-braces in case a stylesheet failed to load.)
  root.style.pointerEvents = "auto";

  // Backdrop layer (dimmed cover image + scrim). The <img> is lazily pointed at
  // the fanart on each open; the scrim is a CSS gradient over it for legibility.
  // The hero band also carries the title overlay so the name reads OVER the art.
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

  // Close affordance (✕) — pinned to the hero band's top-right. Subtle on desktop,
  // prominent in the mobile modal (CSS). Clicking it closes whichever mode is open.
  var closeBtn = document.createElement("button");
  closeBtn.type = "button";
  closeBtn.className = "hp-close";
  closeBtn.setAttribute("aria-label", "Close");
  closeBtn.title = "Close";
  closeBtn.textContent = "✕";
  media.appendChild(closeBtn);

  // Title overlay, anchored to the bottom of the hero band (over the scrim).
  var hero = document.createElement("div");
  hero.className = "hp-hero";
  var kicker = document.createElement("div");
  kicker.className = "hp-kicker";
  kicker.textContent = "Now showing";
  hero.appendChild(kicker);
  var title = document.createElement("h3");
  title.className = "hp-title";
  hero.appendChild(title);
  var ep = document.createElement("div");
  ep.className = "hp-episode";
  hero.appendChild(ep);
  media.appendChild(hero);
  root.appendChild(media);

  // Foreground content.
  var body = document.createElement("div");
  body.className = "hp-body";

  // External ratings strip (IMDb / Rotten Tomatoes / Metacritic coins). Detail-
  // only and OPTIONAL — hidden (display:none, so it contributes NO flex gap) until
  // a refreshed title's detail carries `ratings`. Sits first so the critic scores
  // read just under the hero title; the ★ TMDB rating stays in .hp-rich below.
  var ratings = document.createElement("div");
  ratings.className = "hp-ratings";
  body.appendChild(ratings);

  // Rich strip (★ rating · runtime · genres). Populated only once detail loads;
  // hidden (empty) until then so the instant paint has no blank gap.
  var rich = document.createElement("div");
  rich.className = "hp-rich";
  body.appendChild(rich);

  // Tagline — italic, sits above the synopsis. Detail-only.
  var tagline = document.createElement("p");
  tagline.className = "hp-tagline";
  body.appendChild(tagline);

  var synopsis = document.createElement("p");
  synopsis.className = "hp-synopsis";
  body.appendChild(synopsis);

  // Credits block (cast / directors / show or episode facts). Detail-only.
  var credits = document.createElement("div");
  credits.className = "hp-credits";
  body.appendChild(credits);

  // Awards line (🏆 + a dim, ≤2-line accolades summary). Detail-only/OPTIONAL.
  var awards = document.createElement("p");
  awards.className = "hp-awards";
  body.appendChild(awards);

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
  // Box office pill (detail-only/OPTIONAL) — built hidden, revealed in renderRich
  // when detail carries `boxoffice`. Last in the meta row so it trails the state/
  // size/category pills. .hp-pill has no author `display`, so the `hidden`
  // attribute (UA display:none) reliably hides it — same idiom as the link chips.
  var boxoffice = document.createElement("span");
  boxoffice.className = "hp-pill hp-boxoffice";
  boxoffice.hidden = true;
  meta.appendChild(boxoffice);
  body.appendChild(meta);

  // External link row — now part of the fully-interactive panel. Built empty; the
  // anchors are revealed only when detail carries a valid imdb_url / tmdb_url.
  var links = document.createElement("div");
  links.className = "hp-links";
  var imdb = document.createElement("a");
  imdb.className = "hp-link hp-link-imdb";
  imdb.target = "_blank";
  imdb.rel = "noopener noreferrer";
  imdb.hidden = true;
  links.appendChild(imdb);
  var tmdb = document.createElement("a");
  tmdb.className = "hp-link hp-link-tmdb";
  tmdb.target = "_blank";
  tmdb.rel = "noopener noreferrer";
  tmdb.hidden = true;
  links.appendChild(tmdb);
  body.appendChild(links);

  root.appendChild(body);
  document.body.appendChild(root);

  _panel = {
    root: root,
    backdrop: backdrop,
    media: media,
    img: img,
    closeBtn: closeBtn,
    kicker: kicker,
    title: title,
    ep: ep,
    ratings: ratings,
    rich: rich,
    tagline: tagline,
    synopsis: synopsis,
    credits: credits,
    awards: awards,
    stateChip: stateChip,
    stateDot: stateDot,
    stateLabel: stateLabel,
    sizePill: sizePill,
    catPill: catPill,
    boxoffice: boxoffice,
    links: links,
    imdb: imdb,
    tmdb: tmdb,
  };

  // ---- Persistence wiring (active region = card OR panel). ------------------
  // Entering the panel keeps the dossier alive (cancel any pending close AND any
  // pending dwell for another card); leaving it starts the close-grace timer.
  // Harmless on touch: the hover path never opens there, and the modal path
  // ignores these (scheduleClose() no-ops in modal mode).
  root.addEventListener("pointerenter", function () {
    _pointerInPanel = true;
    cancelClose();
    cancelDwell();
  });
  root.addEventListener("pointerleave", function () {
    _pointerInPanel = false;
    scheduleClose();
  });

  // ✕ closes whichever presentation is open.
  closeBtn.addEventListener("click", function (e) {
    e.preventDefault();
    e.stopPropagation();
    close();
  });

  // Tapping the dim backdrop dismisses the modal.
  backdrop.addEventListener("click", function () {
    close();
  });

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
// Rich detail — lazy GET /api/detail/{id}, painted into the panel once it
// resolves IF the same open is still showing (open-token guard).
// ---------------------------------------------------------------------------

// Clear every detail-only region so a re-open starts from the basic dossier with
// no stale rich content bleeding through from the previously hovered item.
function clearRich(p) {
  p.ratings.textContent = "";
  p.ratings.classList.remove("show");
  p.rich.textContent = "";
  p.rich.classList.remove("show");
  p.tagline.textContent = "";
  p.tagline.classList.remove("show");
  p.credits.textContent = "";
  p.credits.classList.remove("show");
  p.awards.textContent = "";
  p.awards.classList.remove("show");
  p.boxoffice.hidden = true;
  p.boxoffice.textContent = "";
  p.imdb.hidden = true;
  p.imdb.removeAttribute("href");
  p.imdb.textContent = "";
  p.tmdb.hidden = true;
  p.tmdb.removeAttribute("href");
  p.tmdb.textContent = "";
  p.links.classList.remove("show");
  p.root.classList.remove("has-rich");
}

// Small helper: a labelled chip (genre / fact) appended to a row.
function appendChip(row, text, extraClass) {
  if (!text) return;
  var chip = document.createElement("span");
  chip.className = "hp-chip" + (extraClass ? " " + extraClass : "");
  chip.textContent = text;
  row.appendChild(chip);
}

// One external-rating "coin" for the ratings strip: a brand label (`src`) plus a
// pre-built value node (a plain `.hp-coin-val` span, or the Metacritic colour
// box). Returns the coin so the caller can flag it (RT fresh/rotten). XSS-safe —
// `src` is set via textContent and the value node was built the same way.
function appendCoin(strip, src, valueNode, coinClass) {
  var coin = document.createElement("span");
  coin.className = "hp-coin" + (coinClass ? " " + coinClass : "");
  var label = document.createElement("span");
  label.className = "hp-coin-src";
  label.textContent = src;
  coin.appendChild(label);
  coin.appendChild(valueNode);
  strip.appendChild(coin);
  return coin;
}

// Paint the resolved /api/detail payload. `item` is the card's row (for the
// id/episode context); `d` is the server detail object.
function renderRich(p, item, d) {
  if (!d || d.__none) return; // 404 / no-tmdb_id → keep the basic dossier

  // ---- External ratings strip: IMDb / Rotten Tomatoes / Metacritic coins. ----
  // Each source is OPTIONAL (any subset of detail.ratings may be present/absent),
  // rendered in pure text/CSS reproducing each brand's signature: IMDb gold, RT
  // fresh (green ≥60%) vs rotten (red <60%), and the Metacritic colour-banded
  // metascore square. The TMDB ★ stays in the rich strip below (not duplicated).
  p.ratings.textContent = "";
  var ratings = d.ratings && typeof d.ratings === "object" ? d.ratings : null;
  var hasRatings = false;
  if (ratings) {
    var imdbScore = String(ratings.imdb || "").trim();
    if (imdbScore) {
      var imdbVal = document.createElement("span");
      imdbVal.className = "hp-coin-val";
      imdbVal.textContent = imdbScore;
      appendCoin(p.ratings, "IMDb", imdbVal, "hp-coin-imdb");
      hasRatings = true;
    }
    var rt = String(ratings.rotten_tomatoes || "").trim();
    if (rt) {
      var rtVal = document.createElement("span");
      rtVal.className = "hp-coin-val";
      rtVal.textContent = rt;
      var rtCoin = appendCoin(p.ratings, "RT", rtVal, "hp-coin-rt");
      var rtNum = leadingNumber(rt);
      if (isFinite(rtNum)) {
        rtCoin.classList.add(rtNum >= 60 ? "is-fresh" : "is-rotten");
      }
      hasRatings = true;
    }
    var mc = String(ratings.metacritic || "").trim();
    if (mc) {
      var mcBox = document.createElement("span");
      mcBox.className = "hp-coin-val hp-mc-box";
      mcBox.textContent = mc;
      var mcNum = leadingNumber(mc);
      if (isFinite(mcNum)) {
        mcBox.classList.add(
          mcNum >= 61 ? "is-green" : mcNum >= 40 ? "is-yellow" : "is-red"
        );
      }
      appendCoin(p.ratings, "Metacritic", mcBox, "hp-coin-mc");
      hasRatings = true;
    }
  }
  if (hasRatings) p.ratings.classList.add("show");

  // ---- Rich strip: ★ rating (+ faint votes) · runtime · genre chips. --------
  p.rich.textContent = "";
  var hasStrip = false;

  var rating = Number(d.rating);
  if (isFinite(rating) && rating > 0) {
    var ratingEl = document.createElement("span");
    ratingEl.className = "hp-rating";
    var star = document.createElement("span");
    star.className = "hp-star";
    star.textContent = "★"; // ★
    ratingEl.appendChild(star);
    var score = document.createElement("span");
    score.className = "hp-score";
    score.textContent = rating.toFixed(1);
    ratingEl.appendChild(score);
    var votes = formatVotes(d.vote_count);
    if (votes) {
      var voteEl = document.createElement("span");
      voteEl.className = "hp-votes";
      voteEl.textContent = votes;
      ratingEl.appendChild(voteEl);
    }
    p.rich.appendChild(ratingEl);
    hasStrip = true;
  }

  var runtime = formatRuntime(d.runtime);
  if (runtime) {
    appendChip(p.rich, runtime, "hp-runtime");
    hasStrip = true;
  }

  // MPAA certificate (e.g. "PG-13") — a boxy, bordered chip reading like a ratings
  // certificate, grouped with the runtime/genre facts. Detail-only/OPTIONAL.
  var rated = String(d.rated || "").trim();
  if (rated) {
    appendChip(p.rich, rated, "hp-rated");
    hasStrip = true;
  }

  var genres = Array.isArray(d.genres) ? d.genres : [];
  for (var gi = 0; gi < genres.length && gi < 4; gi += 1) {
    var g = String(genres[gi] || "").trim();
    if (g) {
      appendChip(p.rich, g, "hp-genre");
      hasStrip = true;
    }
  }
  if (hasStrip) p.rich.classList.add("show");

  // ---- Tagline (italic, above the synopsis). --------------------------------
  var tagline = String(d.tagline || "").trim();
  if (tagline) {
    p.tagline.textContent = tagline;
    p.tagline.classList.add("show");
  }

  // ---- Full overview replaces the clamped short one (CSS relaxes the clamp). -
  var fullOverview = String(d.overview || "").trim();
  if (fullOverview) {
    p.synopsis.textContent = fullOverview;
    p.synopsis.classList.remove("empty");
    p.synopsis.classList.add("hp-synopsis-full");
  }

  // ---- Credits: cast · directors/creators · show/episode facts. -------------
  p.credits.textContent = "";
  var hasCredits = false;

  // Top cast — "Name, Name, …" (the character is offered as a title tooltip).
  var cast = Array.isArray(d.cast) ? d.cast : [];
  var castNames = [];
  for (var ci = 0; ci < cast.length && ci < 6; ci += 1) {
    var c = cast[ci];
    if (c && c.name) castNames.push(String(c.name));
  }
  if (castNames.length) {
    var castRow = document.createElement("div");
    castRow.className = "hp-cred-row";
    var castLab = document.createElement("span");
    castLab.className = "hp-cred-label";
    castLab.textContent = "Cast";
    castRow.appendChild(castLab);
    var castVal = document.createElement("span");
    castVal.className = "hp-cred-value";
    castVal.textContent = castNames.join(", ");
    castRow.appendChild(castVal);
    p.credits.appendChild(castRow);
    hasCredits = true;
  }

  // Director(s) / creator(s). For a TV show TMDB returns creators in `directors`
  // upstream-side too (the backend maps created_by → directors), so one label
  // adapts: "Director" for a movie/episode, "Creator" for a show.
  var directors = Array.isArray(d.directors) ? d.directors : [];
  var dirNames = [];
  for (var di = 0; di < directors.length && di < 4; di += 1) {
    if (directors[di]) dirNames.push(String(directors[di]));
  }
  if (dirNames.length) {
    var isShow = d.kind === "tv";
    var dirRow = document.createElement("div");
    dirRow.className = "hp-cred-row";
    var dirLab = document.createElement("span");
    dirLab.className = "hp-cred-label";
    dirLab.textContent =
      (isShow ? "Creator" : "Director") + (dirNames.length > 1 ? "s" : "");
    dirRow.appendChild(dirLab);
    var dirVal = document.createElement("span");
    dirVal.className = "hp-cred-value";
    dirVal.textContent = dirNames.join(", ");
    dirRow.appendChild(dirVal);
    p.credits.appendChild(dirRow);
    hasCredits = true;
  }

  // Kind-specific facts row (small chips).
  if (d.kind === "tv") {
    var showRow = document.createElement("div");
    showRow.className = "hp-cred-row hp-facts";
    var seasons = Number(d.number_of_seasons);
    if (isFinite(seasons) && seasons > 0) {
      appendChip(showRow, seasons + (seasons === 1 ? " season" : " seasons"), "hp-fact");
    }
    var episodes = Number(d.number_of_episodes);
    if (isFinite(episodes) && episodes > 0) {
      appendChip(showRow, episodes + (episodes === 1 ? " episode" : " episodes"), "hp-fact");
    }
    var networks = Array.isArray(d.networks) ? d.networks : [];
    for (var ni = 0; ni < networks.length && ni < 2; ni += 1) {
      var net = String(networks[ni] || "").trim();
      if (net) appendChip(showRow, net, "hp-fact hp-network");
    }
    if (d.status) appendChip(showRow, String(d.status), "hp-fact hp-status");
    if (showRow.childNodes.length) {
      p.credits.appendChild(showRow);
      hasCredits = true;
    }
  } else if (d.kind === "episode") {
    var epRow = document.createElement("div");
    epRow.className = "hp-cred-row hp-facts";
    var sxe = formatSxxEyy(d.season_number, d.episode_number);
    if (sxe) appendChip(epRow, sxe, "hp-fact");
    var air = formatDate(d.air_date);
    if (air) appendChip(epRow, "Aired " + air, "hp-fact");
    if (epRow.childNodes.length) {
      p.credits.appendChild(epRow);
      hasCredits = true;
    }
  }

  if (hasCredits) p.credits.classList.add("show");

  // ---- Awards line (🏆 prefix, dim, clamped to ≤2 lines). -------------------
  // Detail-only/OPTIONAL. Shown faithfully (raw awards string) — the clamp keeps a
  // long "X wins & Y nominations" line tidy without fragile text surgery.
  p.awards.textContent = "";
  var awards = String(d.awards || "").trim();
  if (awards) {
    var trophy = document.createElement("span");
    trophy.className = "hp-awards-icon";
    trophy.textContent = "🏆";
    var awardsText = document.createElement("span");
    awardsText.className = "hp-awards-text";
    awardsText.textContent = awards;
    p.awards.appendChild(trophy);
    p.awards.appendChild(awardsText);
    p.awards.classList.add("show");
  }

  // ---- Box office meta pill (abbreviated $; raw string if small/unparseable). -
  // Detail-only/OPTIONAL — trails the state/size/category pills in the meta row.
  var box = formatBoxOffice(d.boxoffice);
  if (box) {
    p.boxoffice.textContent = "Box office " + box;
    p.boxoffice.hidden = false;
  }

  // ---- External link row (clickable IMDb / TMDB chips). ---------------------
  var imdbUrl = safeExternalUrl(d.imdb_url);
  var tmdbUrl = safeExternalUrl(d.tmdb_url);
  var anyLink = false;
  if (imdbUrl) {
    p.imdb.href = imdbUrl;
    p.imdb.textContent = "IMDb ↗"; // ↗
    p.imdb.hidden = false;
    anyLink = true;
  }
  if (tmdbUrl) {
    p.tmdb.href = tmdbUrl;
    p.tmdb.textContent = "TMDB ↗";
    p.tmdb.hidden = false;
    anyLink = true;
  }
  if (anyLink) p.links.classList.add("show");

  // Flag the panel as enriched (CSS can grow/relax now there's real content, and
  // the verify harness keys off .has-rich).
  p.root.classList.add("has-rich");
}

// Kick the lazy fetch for `item`, painting into the panel if this open survives.
// Re-anchors after a successful paint because the panel may have grown taller.
function fetchRich(p, item, openToken) {
  var id = item.id;
  if (id == null) return;

  // Cache hit (success payload OR a NO_DETAIL sentinel) → paint synchronously,
  // no network. The sentinel renders nothing (renderRich early-returns).
  var cached = _detailCache[id];
  if (cached !== undefined) {
    renderRich(p, item, cached);
    if (openToken === _openToken && _openCard) reanchor(p);
    return;
  }

  authFetch("/api/detail/" + encodeURIComponent(id))
    .then(function (res) {
      if (res.status === 404) {
        _detailCache[id] = NO_DETAIL; // remember: this id has no tmdb detail
        return null;
      }
      if (!res.ok) return null; // other error → do NOT cache; allow a retry
      return res.json().catch(function () {
        return null;
      });
    })
    .then(function (data) {
      if (data) _detailCache[id] = data;
      // Open-token guard: only paint if THIS open is still the current one (the
      // same id the user is still hovering). A stale resolve is dropped silently.
      if (openToken !== _openToken) return;
      if (!data) return; // 404 / error → keep the basic dossier, no flash
      renderRich(p, item, data);
      if (_openCard) reanchor(p);
    })
    .catch(function () {
      // Network failure → keep the basic dossier (no error flash), no cache.
    });
}

// Re-position after the panel changes height (rich content can make it taller),
// guarded so a close that raced the fetch doesn't move a hidden panel. A no-op in
// the modal presentation (it's centred by CSS, not anchored to the card).
function reanchor(p) {
  if (_mode === "modal") return;
  if (_openCard && _openCard.isConnected) position(p, _openCard);
}

// ---------------------------------------------------------------------------
// Smart positioning: anchor to the CARD's rect (never the raw cursor, so it
// can't jitter), prefer the side with the most room, and clamp to the viewport
// so it never runs off-screen. The panel is z-indexed ABOVE the header/banner
// chrome and is allowed to rise up over it — when the card sits low or right, the
// bottom-clamp pushes the panel's top up INTO the header band (that overlap is
// the intended cinematic effect), still kept a small margin from the very edge so
// nothing is clipped.
// ---------------------------------------------------------------------------
var VIEWPORT_MARGIN = 10; // keep this far from every screen edge
// A touch tighter at the TOP so the panel may ride a little further up over the
// header chrome before clamping (the deliberate overlap), without ever clipping.
var TOP_MARGIN = 6;
// Small gap between the card and the panel. Kept tight so gliding card→panel
// crosses almost no dead space (the close-grace timer covers the rest).
var CARD_GAP = 10;

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

  // Vertical: try to centre the (now tall) panel on the card so a big dossier
  // doesn't shoot far below a top-row card, then clamp into the viewport. The
  // top clamp uses the tighter TOP_MARGIN so the panel may overlap UP into the
  // header chrome (intended); the bottom clamp keeps it fully on-screen, which
  // for a low/right card naturally raises its top over the header.
  var cardMid = cardRect.top + cardRect.height / 2;
  var top = cardMid - ph / 2;
  // Don't float far above a card that has plenty of room below it — bias the top
  // toward the card's own top when the panel comfortably fits beneath it.
  if (cardRect.top + ph + VIEWPORT_MARGIN <= vh) {
    top = Math.min(top, cardRect.top);
  }
  var maxTop = vh - ph - VIEWPORT_MARGIN;
  top = Math.min(top, maxTop);
  top = Math.max(TOP_MARGIN, top);

  root.style.left = Math.round(left) + "px";
  root.style.top = Math.round(top) + "px";
}

// ---------------------------------------------------------------------------
// Shared content paint (both presentations call this).
// ---------------------------------------------------------------------------
//
// Fills the panel from the card's row + kicks the lazy backdrop and /api/detail
// fetches, all guarded by `token`. Does NOT position or reveal — the caller picks
// the presentation (anchored hover vs centred modal).
function renderInto(p, card, token) {
  var item = card.__mvItem;

  // Reset every detail-only region so this open starts as the basic dossier with
  // nothing stale from the previously hovered card.
  clearRich(p);

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

  // Synopsis (clamped to a few lines in CSS until the full overview arrives).
  // Interface voice when absent — a statement, not an apology.
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

  // Rich detail (lazy GET /api/detail/{id}; enriches in place when it resolves
  // AND this same open is still showing — guarded by `token`).
  fetchRich(p, item, token);
}

// ---------------------------------------------------------------------------
// Open / close.
// ---------------------------------------------------------------------------
var _openToken = 0; // bumped on every open; stale image/detail callbacks bail
var _openCard = null; // the card the dossier is currently bound to (or null)
var _mode = null; // "hover" (anchored) | "modal" (centred) | null (closed)

// DESKTOP: open the dossier ANCHORED beside the card with the entrance animation.
function openFor(card) {
  var item = card && card.__mvItem;
  if (!item) return;

  var p = buildPanel();
  var token = ++_openToken;
  _openCard = card;
  _mode = "hover";
  // Anchored mode never shows the modal chrome.
  p.root.classList.remove("is-modal");
  p.backdrop.classList.remove("show");
  cancelClose();

  renderInto(p, card, token);

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

// MOBILE: open the SAME dossier as a CENTERED MODAL over a dim backdrop. No card
// anchoring (CSS centres it); the body scrolls if the content is tall.
function openModalFor(card) {
  var item = card && card.__mvItem;
  if (!item) return;

  var p = buildPanel();
  var token = ++_openToken;
  _openCard = card;
  _mode = "modal";
  cancelClose();
  cancelDwell();

  renderInto(p, card, token);

  // Centred by CSS — clear any stale anchored coordinates so .is-modal's centring
  // (left/top 50% + translate) isn't fighting an inline left/top from a prior open.
  p.root.style.left = "";
  p.root.style.top = "";
  p.backdrop.classList.add("show");
  p.root.classList.add("is-modal");
  // No measuring pass needed (position is CSS-driven); reveal next frame so the
  // entrance still animates.
  p.root.classList.remove("is-measuring");
  window.requestAnimationFrame(function () {
    if (token !== _openToken) return;
    p.root.classList.add("is-open");
  });
}

function close() {
  _openCard = null;
  _mode = null;
  _openToken += 1; // invalidate any in-flight image/detail/dwell callbacks
  cancelClose();
  // Drop the active-region tracking so a stale "still inside" flag can never make
  // a later grace timer skip its close (re-set on the next real enter).
  _pointerInCard = false;
  _pointerInPanel = false;
  if (!_panel) return;
  _panel.root.classList.remove("is-open");
  _panel.root.classList.remove("is-modal");
  _panel.backdrop.classList.remove("show");
  // Clear the measuring guard too, in case close() landed between openFor()'s
  // position() and its reveal frame (the frame then bails on the token); the
  // panel is already hidden (opacity:0), this just keeps its class state tidy.
  _panel.root.classList.remove("is-measuring");
  // Stop pulling on a backdrop we're no longer showing.
  _panel.img.onload = null;
  _panel.img.onerror = null;
}

// ---------------------------------------------------------------------------
// Close-grace scheduling (desktop persistence).
// ---------------------------------------------------------------------------
//
// Active region = the hovered card OR the panel. We track whether the pointer is
// inside each; leaving either schedules a close, and it only actually closes when
// the pointer is outside BOTH for CLOSE_GRACE_MS. Entering either cancels it.
var _closeTimer = 0;
var _pointerInCard = false;
var _pointerInPanel = false;

function cancelClose() {
  if (_closeTimer) {
    window.clearTimeout(_closeTimer);
    _closeTimer = 0;
  }
}

function scheduleClose() {
  // Only the anchored hover presentation auto-closes on pointer-out; the modal is
  // dismissed explicitly (backdrop / ✕ / Esc), so never grace-close it.
  if (_mode !== "hover") return;
  if (!_openCard) return;
  cancelClose();
  _closeTimer = window.setTimeout(function () {
    _closeTimer = 0;
    // Re-check the live state: if the pointer slipped back onto the card or panel
    // during the grace window, an enter already cancelled us — but guard anyway.
    if (_pointerInCard || _pointerInPanel) return;
    close();
  }, CLOSE_GRACE_MS);
}

// ---------------------------------------------------------------------------
// Dwell scheduling (desktop hover-to-open).
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

  if (hasHoverPointer()) {
    wireDesktopHover(container);
  } else {
    wireTouchLongPress(container);
  }

  // Escape closes whichever presentation is open (parity with the app's modals).
  document.addEventListener("keydown", function (e) {
    if (e.key === "Escape" && _mode) close();
  });
}

// ---- Desktop: dwell-to-open, anchored, with the card↔panel grace persistence. -
function wireDesktopHover(container) {
  // pointerover bubbles (unlike pointerenter), so one delegated listener on the
  // stable container sees the pointer crossing into any current-or-future card.
  container.addEventListener(
    "pointerover",
    function (e) {
      if (e.pointerType === "touch") return; // mouse/pen only
      var card = e.target && e.target.closest ? e.target.closest(".card") : null;
      if (!card || !container.contains(card)) return;
      // Entering the (open) card keeps it alive; entering any card arms the dwell.
      _pointerInCard = true;
      if (card === _openCard) cancelClose();
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
      _pointerInCard = true;
      armDwell(card);
    },
    { passive: true }
  );

  // Leaving a card: cancel a pending open for it, and start the close-grace timer
  // (which only fires if the pointer is outside BOTH the card and the panel by
  // then — so gliding card→panel keeps it open). pointerout bubbles; relatedTarget
  // is where the pointer went. If it moved to ANOTHER card the pointerover above
  // re-arms; if it moved onto the panel, the panel's pointerenter cancels the close.
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
      _pointerInCard = false;
      // Cancel a dwell queued for the card we just left.
      if (_dwellCard === fromCard) cancelDwell();
      // Begin the grace close for an open dossier bound to the card we left. If
      // the pointer is heading into the panel, its pointerenter cancels this.
      if (_openCard === fromCard) scheduleClose();
    },
    { passive: true }
  );

  // Backstop: the pointer leaving the whole grid (e.g. shooting up into the
  // header) cancels a pending dwell and starts the grace close. pointerleave does
  // NOT bubble, so bind it on the container directly. (It does NOT hard-close, so
  // a pointer that left the grid straight onto the panel still survives.)
  container.addEventListener(
    "pointerleave",
    function () {
      _pointerInCard = false;
      cancelDwell();
      scheduleClose();
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
    // Focus moving INTO the panel (e.g. tabbing to a link) must not close it.
    if (_panel && _panel.root.contains(to)) return;
    if (_openCard === card) close();
  });

  // If the page scrolls or the window resizes while a dossier is open, its
  // card-anchored position would go stale. Re-anchor on scroll/resize, and close
  // if the card scrolled out of view. Passive + coalesced to one rAF.
  var reflowFrame = 0;
  function reflow() {
    reflowFrame = 0;
    if (!_openCard || !_panel) return;
    if (_mode !== "hover") return; // the modal is CSS-centred, not card-anchored
    if (!_openCard.isConnected) {
      close();
      return;
    }
    position(_panel, _openCard);
  }
  function scheduleReflow() {
    if (!_openCard || _mode !== "hover") return;
    if (!reflowFrame) reflowFrame = window.requestAnimationFrame(reflow);
  }
  window.addEventListener("scroll", scheduleReflow, { passive: true });
  window.addEventListener("resize", scheduleReflow, { passive: true });
}

// ---- Touch: long-press a card to open the centred modal dossier. --------------
//
// A pointerdown on a card starts a LONGPRESS_MS timer; sliding past the slop
// (a scroll) or releasing early cancels it and the tap behaves normally. When the
// timer fires we open the modal AND arm click-suppression so the synthetic click
// the press generates can't trip a card button. A short tap is never intercepted.
function wireTouchLongPress(container) {
  var pressTimer = 0;
  var pressCard = null;
  var startX = 0;
  var startY = 0;
  var suppressNextClick = false;
  var suppressClearTimer = 0;

  function clearPress() {
    if (pressTimer) {
      window.clearTimeout(pressTimer);
      pressTimer = 0;
    }
    pressCard = null;
  }

  // Arm/disarm swallowing the very next click (the one the long-press generates on
  // release). Auto-clears after a short window so a later legitimate tap is never
  // eaten if no click happened to fire.
  function armClickSuppression() {
    suppressNextClick = true;
    if (suppressClearTimer) window.clearTimeout(suppressClearTimer);
    suppressClearTimer = window.setTimeout(function () {
      suppressNextClick = false;
      suppressClearTimer = 0;
    }, 700);
  }

  container.addEventListener("pointerdown", function (e) {
    // Only the touch path lives here; ignore an actual mouse/pen if one appears.
    if (e.pointerType === "mouse") return;
    var card = e.target && e.target.closest ? e.target.closest(".card") : null;
    if (!card || !container.contains(card)) return;
    clearPress();
    pressCard = card;
    startX = e.clientX;
    startY = e.clientY;
    pressTimer = window.setTimeout(function () {
      pressTimer = 0;
      var target = pressCard;
      pressCard = null;
      if (!target || !target.isConnected) return;
      // The press became a long-press: open the modal and make sure the click that
      // the OS will synthesise on lift-off doesn't also fire a card button.
      armClickSuppression();
      try {
        if (window.getSelection) window.getSelection().removeAllRanges();
      } catch (err) {
        /* selection clearing is best-effort */
      }
      openModalFor(target);
    }, LONGPRESS_MS);
  });

  // Sliding past the slop is a scroll/drag, not a long-press — cancel the pending
  // open (but don't suppress the tap; the user is scrolling).
  container.addEventListener(
    "pointermove",
    function (e) {
      if (!pressTimer) return;
      var dx = e.clientX - startX;
      var dy = e.clientY - startY;
      if (dx * dx + dy * dy > LONGPRESS_SLOP_PX * LONGPRESS_SLOP_PX) clearPress();
    },
    { passive: true }
  );

  // Released before the timer fired → a normal tap; let it navigate as today.
  // (If the timer already fired, clearPress is a no-op and suppression is armed.)
  container.addEventListener("pointerup", clearPress, { passive: true });
  container.addEventListener("pointercancel", clearPress, { passive: true });

  // Swallow the synthetic click that follows a long-press so it can't activate a
  // card button (Fetch & Restore / open-folder / copy). Capture phase so it runs
  // BEFORE the button's own bubbling handler. A click on the dossier's own links /
  // ✕ is fine — those live outside `container`, so this never sees them.
  container.addEventListener(
    "click",
    function (e) {
      if (!suppressNextClick) return;
      suppressNextClick = false;
      if (suppressClearTimer) {
        window.clearTimeout(suppressClearTimer);
        suppressClearTimer = 0;
      }
      e.preventDefault();
      e.stopPropagation();
    },
    true
  );

  // Suppress the OS callout / context menu that a long-press would otherwise raise
  // while a press is pending or just opened the modal.
  container.addEventListener("contextmenu", function (e) {
    if (pressTimer || suppressNextClick || _mode === "modal") e.preventDefault();
  });
}
