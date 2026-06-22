/* MediaVault Console — client-side sort state + comparator (IMP-E14 follow-up).
 * ES module; `node --check sort.js` covers it (pure logic, no DOM, no fetch).
 *
 * The grid is sorted CLIENT-SIDE from the already-loaded model — switching the
 * sort never refetches. The chosen key+direction live here in module state so
 * they survive tab / sub-view switches AND the post-fetch /api/items refresh.
 *
 * DEFAULT: size, descending (largest first) — applied on first load and to
 * every view until the user changes it.
 *
 * Sorting is STABLE: every comparison breaks ties by id (ascending) so equal
 * keys keep a deterministic, repeatable order across re-renders.
 */

"use strict";

import { displayTitle } from "./title.js";

export var SORT_KEYS = ["size", "title", "year"];

// Module-level current sort. Mutated only via setSort().
var current = { key: "size", dir: "desc" };

export function getSort() {
  return { key: current.key, dir: current.dir };
}

// Update the sort. Ignores unknown keys/dirs so a stray caller can't wedge it.
export function setSort(key, dir) {
  if (SORT_KEYS.indexOf(key) !== -1) current.key = key;
  if (dir === "asc" || dir === "desc") current.dir = dir;
  return getSort();
}

// Numeric year for sorting: prefer the row's parsed year, else 0 (unknown years
// sort as the smallest — they sink to the bottom under the default desc).
function yearValue(item) {
  var y = Number(item && item.year);
  return isFinite(y) && y > 0 ? y : 0;
}

function sizeValue(item) {
  var n = Number(item && item.size_bytes);
  return isFinite(n) ? n : -1; // missing sizes sink below any real size
}

// Compare two items by the ACTIVE key in the ACTIVE direction, with a stable
// id-ascending tiebreak applied AFTER the direction (so ties always read the
// same regardless of asc/desc on the primary key).
export function compareItems(a, b) {
  var dir = current.dir === "asc" ? 1 : -1;
  var primary = 0;

  if (current.key === "title") {
    primary = collate(displayTitle(a), displayTitle(b));
  } else if (current.key === "year") {
    primary = yearValue(a) - yearValue(b);
  } else {
    primary = sizeValue(a) - sizeValue(b);
  }

  if (primary !== 0) return primary * dir;
  // Stable tiebreak: id ascending, INDEPENDENT of `dir`.
  return collate(String(a && a.id), String(b && b.id));
}

// Locale-aware-ish, case-insensitive string compare with a deterministic
// fallback. localeCompare gives natural-ish ordering; we lowercase first so
// "f1" and "F1" don't split. Numeric option keeps "Ep 2" before "Ep 10".
function collate(x, y) {
  var lx = (x || "").toLowerCase();
  var ly = (y || "").toLowerCase();
  if (lx === ly) return 0;
  try {
    return lx.localeCompare(ly, undefined, { numeric: true, sensitivity: "base" });
  } catch (e) {
    return lx < ly ? -1 : 1;
  }
}

// Return a NEW array of `rows` ordered by the current sort. Does not mutate the
// input (callers hold the canonical model order).
export function sortItems(rows) {
  return (rows || []).slice().sort(compareItems);
}
