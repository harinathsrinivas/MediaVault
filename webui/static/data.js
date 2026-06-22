/* MediaVault Console — shared data model (IMP-E14, Candidate B).
 *
 * ES module. `node --check data.js` covers it on its own. No DOM access here:
 * pure data fetching + merging so the grouping logic is testable in isolation
 * and can never be confused with rendering concerns.
 *
 * THE MERGE (identical contract for both candidates):
 *   /api/items   -> authoritative library-centric list (incl. ARCHIVED), but
 *                   NO UNPREPPED rows and NO suggested_command/suggested_folder.
 *   /api/reclaim -> carries UNPREPPED rows (the ONLY source for them) AND the
 *                   suggested_command / suggested_folder / guessed enrichments.
 * Render list = UNION:
 *   • every /api/items row, enriched by the reclaim row with the same id; PLUS
 *   • every reclaim row whose badge === "UNPREPPED" (not present in /api/items),
 *     given state = "UNPREPPED".
 * Dedupe by id (prefer the /api/items row for shape, enriched by reclaim).
 */

"use strict";

// Lifecycle states, in the fixed display order used by the sub-view rail.
// (UNPREPPED first, ARCHIVED last — matches the reclaim-then-cold flow.)
export var STATE_ORDER = [
  "UNPREPPED",
  "LOCAL_NOT_PUSHED",
  "PUSHED_NOT_ARCHIVED",
  "RESTORED_REPLACE_AGAIN",
  "ARCHIVED",
];

// Presentation + action metadata per lifecycle state. `cssKey` reuses the
// existing BADGE_META palette (amber/blue/violet/orange) plus a cold key for
// ARCHIVED. `action` is the POST /api/action/{name}; `confirm` gates the modal.
// ARCHIVED is read-only in Phase 1 (the fetch button lands in Phase 2).
export var STATE_META = {
  UNPREPPED: {
    label: "UNPREPPED",
    short: "Unprepped",
    cssKey: "unprepped",
    action: "prep",
    verb: "Prep",
    confirm: false,
  },
  LOCAL_NOT_PUSHED: {
    label: "LOCAL·NOT-PUSHED",
    short: "Local · not pushed",
    cssKey: "local",
    action: "push",
    verb: "Push",
    confirm: false,
  },
  PUSHED_NOT_ARCHIVED: {
    label: "PUSHED·NOT-ARCHIVED",
    short: "Pushed · not archived",
    cssKey: "pushed",
    action: "replace",
    verb: "Replace",
    confirm: true,
  },
  RESTORED_REPLACE_AGAIN: {
    label: "RESTORED·REPLACE-AGAIN",
    short: "Fetched · not archived",
    cssKey: "restored",
    action: "replace",
    verb: "Replace",
    confirm: true,
  },
  ARCHIVED: {
    label: "ARCHIVED",
    short: "Archived",
    cssKey: "archived",
    action: null, // Phase 2 wires Fetch & Restore; Phase 1 shows a disabled stub.
    verb: "Fetch & Restore",
    confirm: false,
  },
};

// Category tabs, in fixed display order. Keys mirror the server `by_category`.
export var CATEGORY_ORDER = ["movies", "series", "anime", "other"];

export var CATEGORY_META = {
  movies: { label: "Movies" },
  series: { label: "TV series" },
  anime: { label: "Anime" },
  other: { label: "Others" },
};

// Lifecycle state for an arbitrary string (defensive default for an odd
// out-of-sync library status the server may pass through). Returns the matching
// STATE_META, or a synthesized fallback that buckets under ARCHIVED-style cold
// styling but never crashes rendering. We DO NOT invent a new sub-view: an
// unknown state is surfaced as its own pseudo sub-view only if it appears, via
// bucketUnknown below.
export function metaFor(state) {
  return (
    STATE_META[state] || {
      label: String(state || "UNKNOWN"),
      short: String(state || "Unknown"),
      cssKey: "unknown",
      action: null,
      verb: "—",
      confirm: false,
    }
  );
}

// Category from an id prefix — mirrors the server `_category_of` / category_of_id.
// Works for real ids and guessed UNPREPPED ids alike.
export function categoryOf(id) {
  var s = String(id || "");
  if (s.indexOf("mov") === 0) return "movies";
  if (s.indexOf("tv") === 0) return "series";
  if (s.indexOf("ani") === 0) return "anime";
  return "other";
}

// Fetch both endpoints concurrently and return the merged render list plus
// per-category and per-(category,state) counts. Throws if EITHER endpoint fails
// to load — the caller renders a single error line (both feeds are required to
// paint a correct picture).
export function loadModel() {
  return Promise.all([fetchJson("/api/items"), fetchJson("/api/reclaim")]).then(
    function (results) {
      var itemsPayload = results[0] || {};
      var reclaimPayload = results[1] || {};
      var itemRows = itemsPayload.items || [];
      var reclaimRows = reclaimPayload.items || [];

      // Index reclaim rows by id for O(1) enrichment + UNPREPPED extraction.
      var reclaimById = {};
      reclaimRows.forEach(function (r) {
        if (r && r.id != null) reclaimById[r.id] = r;
      });

      var merged = [];
      var seen = {};

      // 1) Every /api/items row (authoritative shape), enriched by reclaim.
      itemRows.forEach(function (row) {
        if (!row || row.id == null) return;
        var id = row.id;
        if (seen[id]) return;
        seen[id] = true;
        merged.push(mergeRow(row, reclaimById[id]));
      });

      // 2) Every reclaim UNPREPPED row not already present (the only source for
      //    UNPREPPED). Give it state = "UNPREPPED".
      reclaimRows.forEach(function (r) {
        if (!r || r.id == null) return;
        if (r.badge !== "UNPREPPED") return;
        if (seen[r.id]) return;
        seen[r.id] = true;
        merged.push(unpreppedRow(r));
      });

      return {
        items: merged,
        reclaimTotalHuman:
          reclaimPayload.total_reclaimable_human ||
          humanSize(reclaimPayload.total_reclaimable_bytes),
        reclaimCount: reclaimRows.length,
        counts: countBy(merged),
      };
    }
  );
}

// Merge a /api/items row with the reclaim row of the same id (if any). The
// items row defines shape (id/category/state/size/path/title/poster); the
// reclaim row contributes suggested_command / suggested_folder / guessed.
function mergeRow(itemRow, reclaimRow) {
  var category = itemRow.category || categoryOf(itemRow.id);
  var out = {
    id: itemRow.id,
    category: category,
    state: itemRow.state || "UNKNOWN",
    size_bytes: itemRow.size_bytes,
    path: itemRow.path || "",
    title: itemRow.title || itemRow.id,
    year: itemRow.year,
    tmdb_id: itemRow.tmdb_id,
    poster_available: !!itemRow.poster_available,
    chunk_count: itemRow.chunk_count || 1,
    parent_id: itemRow.parent_id,
    suggested_command: "",
    suggested_folder: null,
    guessed: false,
  };
  if (reclaimRow) {
    out.suggested_command = reclaimRow.suggested_command || "";
    out.suggested_folder = reclaimRow.suggested_folder || null;
    out.guessed = !!reclaimRow.guessed;
    // /api/items intentionally lacks size on a few odd leaves; prefer a real
    // reclaim size when the items size is missing.
    if (out.size_bytes == null) out.size_bytes = reclaimRow.size_bytes;
  }
  return out;
}

// Build a render row from a reclaim UNPREPPED row (absent from /api/items).
function unpreppedRow(r) {
  return {
    id: r.id,
    category: categoryOf(r.id),
    state: "UNPREPPED",
    size_bytes: r.size_bytes,
    path: r.path || "",
    title: r.id,
    year: null,
    tmdb_id: null,
    poster_available: false,
    chunk_count: 1,
    parent_id: undefined,
    suggested_command: r.suggested_command || "",
    suggested_folder: r.suggested_folder || null,
    guessed: !!r.guessed,
  };
}

// counts.byCategory[cat] = total; counts.byCatState[cat][state] = N.
function countBy(rows) {
  var byCategory = {};
  var byCatState = {};
  CATEGORY_ORDER.forEach(function (c) {
    byCategory[c] = 0;
    byCatState[c] = {};
  });
  rows.forEach(function (row) {
    var c = row.category;
    if (byCategory[c] == null) {
      byCategory[c] = 0;
      byCatState[c] = {};
    }
    byCategory[c] += 1;
    var s = row.state;
    byCatState[c][s] = (byCatState[c][s] || 0) + 1;
  });
  return { byCategory: byCategory, byCatState: byCatState };
}

function fetchJson(url) {
  return fetch(url).then(function (res) {
    if (!res.ok) throw new Error(url + " -> HTTP " + res.status);
    return res.json();
  });
}

// Human-readable bytes (binary units), mirrors human_readable_size on the server.
export function humanSize(bytes) {
  var n = Number(bytes);
  if (!isFinite(n) || n < 0) return "—";
  if (n < 1024) return n + " B";
  var units = ["KB", "MB", "GB", "TB", "PB"];
  var i = -1;
  do {
    n = n / 1024;
    i += 1;
  } while (n >= 1024 && i < units.length - 1);
  return n.toFixed(n >= 100 ? 0 : n >= 10 ? 1 : 2) + " " + units[i];
}
