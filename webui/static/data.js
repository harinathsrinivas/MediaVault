/* MediaVault Console — Candidate A: pure data model (no DOM).
 *
 * This module owns the SHARED data model that turns the two read-only endpoints
 * (/api/items + /api/reclaim) into the per-tab, per-sub-view render structure the
 * UI consumes. It is deliberately DOM-free so its merge/categorize/group rules
 * can be reasoned about (and `node --check`ed) in isolation from rendering.
 *
 * The render list is the UNION of:
 *   - every /api/items row (library states incl. ARCHIVED), enriched with
 *     suggested_command / suggested_folder / guessed from the reclaim row that
 *     shares its id (when present);
 *   - PLUS every /api/reclaim row whose badge === "UNPREPPED" (these are NOT in
 *     /api/items) — surfaced with state = "UNPREPPED".
 * De-duped by id (an id appears once; the /api/items row wins for shape).
 */

"use strict";

// Lifecycle states, in their canonical sub-view order. The first four mirror the
// reclaim badges; ARCHIVED is library-only (from /api/items). Anything that does
// not match one of these (a rare out-of-sync library status string) is bucketed
// under its best match or, failing that, dropped into "other" of its category by
// the renderer — never crashing the view.
export var STATE_ORDER = [
  "UNPREPPED",
  "LOCAL_NOT_PUSHED",
  "PUSHED_NOT_ARCHIVED",
  "RESTORED_REPLACE_AGAIN",
  "ARCHIVED",
];

// Friendly section titles for each sub-view (the accordion header label).
export var STATE_TITLE = {
  UNPREPPED: "Unprepped",
  LOCAL_NOT_PUSHED: "Local · not-pushed",
  PUSHED_NOT_ARCHIVED: "Pushed · not-archived",
  RESTORED_REPLACE_AGAIN: "Fetched · not-archived",
  ARCHIVED: "Archived (fetchable)",
};

// Category tabs, in order, with their display labels.
export var CATEGORY_ORDER = ["movies", "series", "anime", "other"];
export var CATEGORY_TITLE = {
  movies: "Movies",
  series: "TV series",
  anime: "Anime",
  other: "Others",
};

// Map a library/guessed id prefix to a category. Mirrors the server
// _category_of / category_of_id split exactly so client tab counts agree with
// the server's by_category.
export function categoryOf(id) {
  var s = String(id || "");
  if (s.indexOf("mov") === 0) return "movies";
  if (s.indexOf("tv") === 0) return "series";
  if (s.indexOf("ani") === 0) return "anime";
  return "other";
}

// Normalize an arbitrary library `state` to one of STATE_ORDER. A value already
// in STATE_ORDER passes through. Anything else (e.g. a raw upper-cased library
// status for a rare out-of-sync leaf) is mapped to its best-match bucket so the
// row is still shown rather than dropped: a leaf whose file is gone / archived
// reads as ARCHIVED; otherwise it falls under Local·not-pushed as the most
// conservative "needs attention locally" bucket. This keeps the union total
// equal to the rendered total — no silent disappearances.
export function normalizeState(state) {
  var s = String(state || "").toUpperCase();
  if (STATE_ORDER.indexOf(s) !== -1) return s;
  if (s.indexOf("ARCHIV") !== -1) return "ARCHIVED";
  return "LOCAL_NOT_PUSHED";
}

// Build the merged, de-duped render list from the two raw payloads. Pure: takes
// the parsed JSON of each endpoint, returns an array of normalized card records.
//
// Each record: {
//   id, category, state (one of STATE_ORDER), size_bytes, path, title, year,
//   tmdb_id, poster_available, chunk_count, parent_id?,
//   suggested_command?, suggested_folder?, guessed
// }
export function buildRenderList(itemsPayload, reclaimPayload) {
  var itemRows = (itemsPayload && itemsPayload.items) || [];
  var reclaimRows = (reclaimPayload && reclaimPayload.items) || [];

  // Index reclaim rows by id for O(1) enrichment + UNPREPPED discovery.
  var reclaimById = Object.create(null);
  reclaimRows.forEach(function (r) {
    if (r && r.id != null) reclaimById[r.id] = r;
  });

  var byId = Object.create(null); // id -> record (dedupe; /api/items wins shape)
  var ordered = [];

  // 1) Every /api/items row, enriched from its reclaim twin when present.
  itemRows.forEach(function (it) {
    if (!it || it.id == null) return;
    if (byId[it.id]) return; // defensive: /api/items already de-duped server-side
    var twin = reclaimById[it.id];
    var rec = {
      id: it.id,
      category: it.category || categoryOf(it.id),
      state: normalizeState(it.state),
      size_bytes: it.size_bytes,
      path: it.path || "",
      title: it.title,
      year: it.year,
      tmdb_id: it.tmdb_id,
      poster_available: !!it.poster_available,
      chunk_count: it.chunk_count,
      suggested_command: twin ? twin.suggested_command : undefined,
      suggested_folder: twin ? twin.suggested_folder : undefined,
      guessed: twin ? !!twin.guessed : false,
    };
    if (it.parent_id != null) rec.parent_id = it.parent_id;
    byId[it.id] = rec;
    ordered.push(rec);
  });

  // 2) Every reclaim UNPREPPED row (these are the ONLY source of Unprepped and
  //    are NOT in /api/items). Skip any id already represented.
  reclaimRows.forEach(function (r) {
    if (!r || r.id == null) return;
    if (r.badge !== "UNPREPPED") return;
    if (byId[r.id]) return;
    var rec = {
      id: r.id,
      category: categoryOf(r.id),
      state: "UNPREPPED",
      size_bytes: r.size_bytes,
      path: r.path || "",
      title: undefined,
      year: undefined,
      tmdb_id: undefined,
      poster_available: false,
      chunk_count: undefined,
      suggested_command: r.suggested_command,
      suggested_folder: r.suggested_folder,
      guessed: !!r.guessed,
    };
    byId[r.id] = rec;
    ordered.push(rec);
  });

  return ordered;
}

// Group a render list into {category: {state: [records]}} with EVERY category in
// CATEGORY_ORDER and EVERY state in STATE_ORDER pre-seeded to [] — so the UI can
// render empty sub-views ("No items") without special-casing missing keys.
export function groupByCategoryState(records) {
  var groups = Object.create(null);
  CATEGORY_ORDER.forEach(function (cat) {
    groups[cat] = Object.create(null);
    STATE_ORDER.forEach(function (st) {
      groups[cat][st] = [];
    });
  });
  records.forEach(function (rec) {
    var cat = groups[rec.category] ? rec.category : "other";
    var st = groups[cat][rec.state] ? rec.state : "LOCAL_NOT_PUSHED";
    groups[cat][st].push(rec);
  });
  return groups;
}

// Per-category totals for the tab count badges, computed from the merged set.
export function categoryCounts(records) {
  var counts = { movies: 0, series: 0, anime: 0, other: 0 };
  records.forEach(function (rec) {
    var cat = counts[rec.category] != null ? rec.category : "other";
    counts[cat] += 1;
  });
  return counts;
}
