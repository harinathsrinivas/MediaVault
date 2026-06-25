/* MediaVault Console — cinematic hover detail-window (IMP-E16).
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
 *      A 404 ("no tmdb_id"), a fetch error, or a stale open → we simply keep the
 *      basic dossier (no error flash). Per-id detail is cached in memory so
 *      re-hovering the same card never refetches.
 *
 * LINK ROW (the one interactive exception): the panel is pointer-events:none so
 * it can never block a click / tap / the ⤢ expand arrow. The IMDb / TMDB row is
 * the SOLE element re-enabled to pointer-events:auto, so the user can click
 * through to the external page; everything else stays inert. The hrefs are
 * validated (must start with https://www.imdb.com or https://www.themoviedb.org)
 * before being set, and open in a new tab with rel=noopener.
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
 * is purely informational (bar the one link row) and can never block a click.
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

// The link row is the ONE interactive part of an otherwise inert panel, so we are
// strict about what we will turn into a real href: an exact-origin allow-list.
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

  // External link row — the SOLE pointer-events:auto element. Built empty; the
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
    media: media,
    img: img,
    kicker: kicker,
    title: title,
    ep: ep,
    rich: rich,
    tagline: tagline,
    synopsis: synopsis,
    credits: credits,
    stateChip: stateChip,
    stateDot: stateDot,
    stateLabel: stateLabel,
    sizePill: sizePill,
    catPill: catPill,
    links: links,
    imdb: imdb,
    tmdb: tmdb,
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
// Rich detail — lazy GET /api/detail/{id}, painted into the panel once it
// resolves IF the same open is still showing (open-token guard).
// ---------------------------------------------------------------------------

// Clear every detail-only region so a re-open starts from the basic dossier with
// no stale rich content bleeding through from the previously hovered item.
function clearRich(p) {
  p.rich.textContent = "";
  p.rich.classList.remove("show");
  p.tagline.textContent = "";
  p.tagline.classList.remove("show");
  p.credits.textContent = "";
  p.credits.classList.remove("show");
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

// Paint the resolved /api/detail payload. `item` is the card's row (for the
// id/episode context); `d` is the server detail object.
function renderRich(p, item, d) {
  if (!d || d.__none) return; // 404 / no-tmdb_id → keep the basic dossier

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

  // ---- External link row (the one clickable region). ------------------------
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
// guarded so a close that raced the fetch doesn't move a hidden panel.
function reanchor(p) {
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
var CARD_GAP = 16; // breathing room between the card and the panel

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
// Open / close.
// ---------------------------------------------------------------------------
var _openToken = 0; // bumped on every open; stale image/detail callbacks bail
var _openCard = null; // the card the dossier is currently bound to (or null)

function openFor(card) {
  var item = card && card.__mvItem;
  if (!item) return;

  var p = buildPanel();
  var token = ++_openToken;
  _openCard = card;

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
  _openToken += 1; // invalidate any in-flight image/detail/dwell callbacks
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
