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

// ---------------------------------------------------------------------------
// Grouped (tree) comparator — mixed folder + leaf nodes, sorted by the ACTIVE
// key+dir, used at EVERY nesting level by tree.js. Pure (no DOM/fetch) so
// `node --check sort.js` covers it.
//
// Node shapes:
//   folder = { type:"folder", name, path, size_bytes, has_image, children:[...] }
//   leaf   = an items_payload row { type:"leaf", id, title, state, size_bytes, year }
//
// Value extraction per key:
//   size  : node.size_bytes for both (folders carry a recursive size; missing
//           sinks via sizeValue's -1).
//   title : folder -> node.name; leaf -> displayTitle(node). Compared via collate
//           (case-insensitive, numeric so "Season 2" < "Season 10").
//   year  : leaf -> its own year (yearValue). folder -> DIRECTIONAL: ascending
//           uses the folder's MIN descendant-leaf year, descending uses its MAX,
//           so a folder's effective year tracks the sort direction. Folders with
//           no descendant year sink (0), exactly like an unknown-year leaf.
// Tiebreak (AFTER direction, independent of dir): folder -> name, leaf -> id,
// ascending — mirrors compareItems' stable-tiebreak discipline.
// ---------------------------------------------------------------------------

function isLeafNode(node) {
  return !!(node && node.type === "leaf");
}

// The string compared/tiebroken on for the Title key.
function nodeTitle(node) {
  return isLeafNode(node) ? displayTitle(node) : (node && node.name) || "";
}

// Walk a folder's descendant LEAF nodes and memoize its min & max leaf year on
// the node (_minYear/_maxYear) so repeated comparisons in one sort are cheap.
// Leaves contribute their own yearValue; a leaf/folder with no real year (0) is
// skipped so it never drags the folder's effective year to 0 spuriously. A
// folder with NO dated descendant gets {min:0, max:0} (sinks like an unknown).
function folderYearBounds(node) {
  if (node._minYear !== undefined && node._maxYear !== undefined) {
    return { min: node._minYear, max: node._maxYear };
  }
  var min = 0;
  var max = 0;
  var seen = false;
  var stack = (node && node.children) ? node.children.slice() : [];
  while (stack.length) {
    var n = stack.pop();
    if (!n) continue;
    if (isLeafNode(n)) {
      var y = yearValue(n);
      if (y > 0) {
        if (!seen) {
          min = y;
          max = y;
          seen = true;
        } else {
          if (y < min) min = y;
          if (y > max) max = y;
        }
      }
    } else if (n.children) {
      for (var i = 0; i < n.children.length; i += 1) stack.push(n.children[i]);
    }
  }
  node._minYear = min;
  node._maxYear = max;
  return { min: min, max: max };
}

// A node's effective year for the CURRENT direction: leaves use their own year;
// folders use MIN descendant year ascending, MAX descending.
function nodeYear(node, asc) {
  if (isLeafNode(node)) return yearValue(node);
  var b = folderYearBounds(node);
  return asc ? b.min : b.max;
}

// Compare two tree nodes (folder or leaf, fully mixed) by the active key+dir.
export function compareNodes(a, b) {
  var asc = current.dir === "asc";
  var dir = asc ? 1 : -1;
  var primary = 0;

  if (current.key === "title") {
    primary = collate(nodeTitle(a), nodeTitle(b));
  } else if (current.key === "year") {
    primary = nodeYear(a, asc) - nodeYear(b, asc);
  } else {
    primary = sizeValue(a) - sizeValue(b);
  }

  if (primary !== 0) return primary * dir;
  // Stable tiebreak: name (folder) / id (leaf) ascending, INDEPENDENT of `dir`.
  var ak = isLeafNode(a) ? String(a && a.id) : (a && a.name) || "";
  var bk = isLeafNode(b) ? String(b && b.id) : (b && b.name) || "";
  return collate(ak, bk);
}

// Return a NEW sorted array of mixed folder+leaf nodes. Never mutates the input
// (tree.js holds the canonical backend child order and must not corrupt it).
export function sortNodes(nodes) {
  return (nodes || []).slice().sort(compareNodes);
}
