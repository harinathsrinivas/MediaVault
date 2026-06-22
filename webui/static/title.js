/* MediaVault Console — id → human title (IMP-E14 Phase 2 follow-up).
 * ES module; `node --check title.js` covers it (pure string math, no DOM).
 *
 * Shared by card.js (the prominent card title) AND sort.js (Title-key sorting),
 * so the text the user reads is the exact text we sort by.
 *
 * FORWARD-COMPATIBLE WITH PHASE 5 (TMDB):
 *   displayTitle(item) returns item.title when the backend has populated a REAL
 *   metadata.title (i.e. title is present AND differs from the raw id); otherwise
 *   it falls back to humanizeId(id). So the moment Phase 5 fills metadata.title
 *   from TMDB, cards auto-show it with NO further change here.
 *
 * humanizeId() does a best-effort parse of the canonical id shape
 *   cat-lang-year-slug[...]      cat ∈ mov | tv | ani
 * stripping the cat / 2-letter lang / 4-digit year prefix, lifting a trailing
 * tv -sNNeMM (shown as "SxxEyy") or a glued anime episode number (shown as
 * "Ep N"), then title-casing the slug and appending "(year)".
 *
 * NOTE on "smushed" slugs: ids like `deathnote` cannot be split back into
 * "Death Note" without a dictionary — we title-case what we have ("Deathnote").
 * Real, correctly-spaced titles arrive via TMDB in Phase 5; this is a readable
 * stopgap, deliberately not a perfect un-smusher.
 */

"use strict";

// Replace separators with spaces and Title-Case each word. Leaves embedded
// digits attached to their word (e.g. "f1" -> "F1", "s01" handled before here).
function titleCase(slug) {
  var s = String(slug || "").replace(/[-_.]+/g, " ").trim();
  if (!s) return "";
  return s.replace(/\S+/g, function (word) {
    return word.charAt(0).toUpperCase() + word.slice(1);
  });
}

// Parse a canonical id into its readable parts. Returns
//   { base, year, episode }  where
//     base    = the title-cased slug (never empty: falls back to the raw id)
//     year    = "2025" | null
//     episode = "S01E10" | "Ep 06" | null
// Defensive: anything that doesn't match the shape still yields a sensible base.
export function parseId(id) {
  var raw = String(id == null ? "" : id);
  var rest = raw;
  var year = null;
  var episode = null;

  // 1) Strip the category prefix (mov-/tv-/ani-). Other prefixes pass through.
  var catMatch = /^(mov|tv|ani)-/.exec(rest);
  var cat = catMatch ? catMatch[1] : null;
  if (cat) rest = rest.slice(catMatch[0].length);

  // 2) Strip a 2-letter language code segment (en-, ja-, …) if present.
  rest = rest.replace(/^[a-z]{2}-/, "");

  // 3) Lift a 4-digit year segment (….2025-… or a leading 2025-).
  var yearMatch = /(?:^|-)(\d{4})(?:-|$)/.exec(rest);
  if (yearMatch) {
    year = yearMatch[1];
    rest = (rest.slice(0, yearMatch.index) + "-" +
            rest.slice(yearMatch.index + yearMatch[0].length))
      .replace(/^-+|-+$/g, "");
  }

  // 4) Episode markers.
  //    tv: a trailing -sNNeMM  (also tolerate sNNeMM without the leading dash).
  var tvEp = /-?s(\d{1,2})e(\d{1,3})$/i.exec(rest);
  if (cat === "tv" && tvEp) {
    episode =
      "S" + pad2(tvEp[1]) + "E" + pad2(tvEp[2]);
    rest = rest.slice(0, tvEp.index);
  } else if (cat === "ani") {
    // anime: a trailing glued episode number (e.g. deathnote06 -> Ep 06).
    var aniEp = /(\d{1,3})$/.exec(rest);
    if (aniEp && aniEp.index > 0) {
      // Only treat trailing digits as an episode when there is a non-digit slug
      // before them (avoid turning a pure-number slug into an empty base).
      episode = "Ep " + pad2(aniEp[1]);
      rest = rest.slice(0, aniEp.index);
    }
  }

  rest = rest.replace(/^-+|-+$/g, "");
  var base = titleCase(rest);
  if (!base) base = raw; // never return an empty title
  return { base: base, year: year, episode: episode };
}

function pad2(n) {
  var s = String(n);
  return s.length < 2 ? "0" + s : s;
}

// Humanize a raw id into a single display string:
//   "F1 (2025)" | "Dark — S01E10 (2017)" | "Death Note — Ep 06 (2006)"
export function humanizeId(id) {
  var parts = parseId(id);
  var out = parts.base;
  if (parts.episode) out += " — " + parts.episode;
  if (parts.year) out += " (" + parts.year + ")";
  return out;
}

// The title to show prominently on a card. Prefers a real backend title (Phase 5
// TMDB metadata.title); otherwise humanizes the id. Kept tiny + pure so card.js
// and sort.js agree byte-for-byte on what "the title" is.
export function displayTitle(item) {
  if (item && item.title && item.title !== item.id) return String(item.title);
  return humanizeId(item ? item.id : "");
}
