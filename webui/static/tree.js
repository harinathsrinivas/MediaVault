/* MediaVault Console — grouped / hierarchical folder view (IMP-E14 polish).
 * ES module; `node --check tree.js` covers it.
 *
 * renderTree(container, roots) paints a COLLAPSIBLE folder hierarchy that mirrors
 * the real on-disk structure under the active media-type tab's category root
 * (movies / series / anime / other). The structure comes from GET /api/tree
 * (see data.js loadTree() — fetched once, cached). Each node is one of:
 *
 *   folder : { type:"folder", name, path, size_bytes, has_image, children[] }
 *   leaf   : an items_payload() row + { type:"leaf" }  (id/category/state/…)
 *
 * REUSE (the key subtlety, per the scout's reuseNotes): a leaf node from the tree
 * is the RAW items row and lacks the reclaim enrichment (suggested_command /
 * suggested_folder / guessed) that buildCard() reads. So we JOIN each leaf onto the
 * already-merged MODEL row by id and pass THAT enriched row through the SAME
 * buildCard() — otherwise Copy-command/folder silently vanish. Structure comes from
 * the tree; the enriched card payload comes from MODEL. Leaves rendered this way
 * keep their state badge + Fetch & Restore + progress ring + glow + hover border +
 * expandable terminal for free, because they ARE the exact same .card element.
 *
 * Folder nodes render: a poster <img> (only when has_image; src=/api/folder-image,
 * onerror -> gradient fallback), the folder name, the REAL recursive folder size
 * (humanSize(size_bytes)), an open-folder button, and an expand/collapse disclosure
 * that reveals the children (sub-folders first, then leaf cards). The whole tree is
 * rendered INSIDE #panel so the delegated cursor-glow (bound once to #panel in
 * app.js) covers tree leaf cards with zero extra wiring.
 *
 * Touch-friendly: the disclosure is a real <button> with a large tap target; no
 * core action is hover-only. XSS-safe: every name/path/size renders via textContent
 * (never innerHTML) and the image src is encodeURIComponent'd.
 */

"use strict";

import { humanSize, loadTree, openFolder } from "./data.js";
import { buildCard, destroyRingsIn } from "./card.js";
import { compareNodes } from "./sort.js";

// Per-folder expand/collapse state, keyed by the folder's absolute path, kept for
// the life of the session so re-rendering the tree (e.g. after a post-fetch model
// refresh) preserves which folders the user opened. A path absent from the map
// uses the depth-based default in defaultOpen().
var EXPANDED = {};

// Tasteful default: top-level folders (depth 0) start EXPANDED so the user sees
// the shape immediately; deeper folders start collapsed to keep the initial DOM
// light on big TV trees (show -> season -> episode). The user's explicit toggles
// (stored in EXPANDED) always win over this default.
function defaultOpen(depth) {
  return depth === 0;
}

function isOpen(node, depth) {
  var v = EXPANDED[node.path];
  return v === undefined ? defaultOpen(depth) : v;
}

// ---------------------------------------------------------------------------
// State prune (PURE, DOM-free — importable by the node guard like compareNodes).
//
// The grouped view's rail can filter the folder tree to ONE lifecycle state. The
// rule (user-explicit): keep a LEAF only if its effective state === `state`, and
// keep a FOLDER only if it has at least one descendant leaf (nested at ANY depth)
// matching `state` — folders with no matching descendant are DROPPED. "English"
// shows under "Unprepped" iff some leaf under English is actually unprepped.
//
// effectiveLeafState mirrors renderLeaf's JOIN so the prune filters on the SAME
// state the badge shows: prefer the enriched MODEL row's state (modelById[id]),
// fall back to the raw tree leaf's own `state`. (The backend now stamps `state` on
// every leaf; the model row is authoritative when present, and the two agree.)
//
// Returns a NEW array of pruned node COPIES — the cached /api/tree (data.js
// loadTree) is NEVER mutated, so toggling the filter back to All (or to another
// state) re-prunes the pristine tree every time. Folder copies carry NO sort memo
// (_minYear/_maxYear) so renderTree's compareNodes recomputes year bounds against
// the PRUNED children, not the full tree.
//
// Folder size while filtered: the AGGREGATE size of the VISIBLE (matching)
// descendant leaves, summed bottom-up from the kept children (a kept leaf
// contributes its own size_bytes; a kept sub-folder contributes its already-
// aggregated size). So the displayed folder size reflects exactly what is shown.
// ---------------------------------------------------------------------------

export function effectiveLeafState(leaf, modelById) {
  if (leaf && leaf.id != null && modelById && modelById[leaf.id]) {
    var row = modelById[leaf.id];
    if (row.state != null) return row.state;
  }
  return leaf && leaf.state;
}

function sizeOf(node) {
  var n = Number(node && node.size_bytes);
  return isFinite(n) && n > 0 ? n : 0;
}

// Prune ONE node; returns the pruned copy, or null if it (and all descendants)
// fail the filter. Leaves are kept iff their effective state matches; folders are
// kept iff at least one descendant leaf matches.
function pruneNode(node, state, modelById) {
  if (node && node.type === "leaf") {
    return effectiveLeafState(node, modelById) === state ? shallowLeafCopy(node) : null;
  }
  // Folder: recurse, keep only surviving children.
  var kids = (node && node.children) || [];
  var kept = [];
  var aggBytes = 0;
  for (var i = 0; i < kids.length; i += 1) {
    var pk = pruneNode(kids[i], state, modelById);
    if (pk) {
      kept.push(pk);
      aggBytes += sizeOf(pk);
    }
  }
  if (kept.length === 0) return null; // no matching descendant → drop the folder
  return {
    type: "folder",
    name: node && node.name,
    path: node && node.path,
    has_image: !!(node && node.has_image),
    // Size reflects the visible (matching) descendants, not the real folder size.
    size_bytes: aggBytes,
    children: kept,
  };
}

// A leaf is rendered through buildCard from modelById, so the copy only needs to
// carry the fields the tree itself reads (id for the join, type/state for prune &
// sort, size_bytes/year for compareNodes). Shallow-copying the whole leaf is
// simplest and keeps any extra backend fields intact for buildCard's fallback.
function shallowLeafCopy(leaf) {
  var out = {};
  for (var k in leaf) {
    if (Object.prototype.hasOwnProperty.call(leaf, k)) out[k] = leaf[k];
  }
  return out;
}

// Prune a category's root nodes to `state`. Pure: returns a fresh array, never
// mutating `roots`. Used by app.js when a state filter (not "All") is active.
export function pruneTreeByState(roots, state, modelById) {
  var out = [];
  var list = roots || [];
  for (var i = 0; i < list.length; i += 1) {
    var p = pruneNode(list[i], state, modelById || {});
    if (p) out.push(p);
  }
  return out;
}

// ---------------------------------------------------------------------------
// Public entry — render the category's roots into `container` (#panel).
// `roots` is the array of top-level nodes for the active category (may be []).
// `modelById` maps leaf id -> the enriched MODEL row for buildCard reuse.
// ---------------------------------------------------------------------------

export function renderTree(container, roots, modelById) {
  if (!container) return;
  // Same teardown invariant as the flat paint(): dispose any fetch-ring
  // ResizeObservers under cards we're about to remove, so none leak across
  // re-renders of the grouped view.
  destroyRingsIn(container);
  container.textContent = "";

  var list = roots || [];
  if (list.length === 0) {
    container.appendChild(emptyTree());
    return;
  }

  // Order the TOP level by the active sort (size/title/year). Sort a COPY so the
  // cached /api/tree roots array is never mutated — toggling sort back and forth
  // (and re-sorting after a model refresh) must stay correct and leave the model
  // untouched. Every deeper level is sorted the same way in renderFolder below.
  var ordered = list.slice().sort(compareNodes);

  var tree = document.createElement("div");
  tree.className = "tree";
  var frag = document.createDocumentFragment();
  ordered.forEach(function (node) {
    frag.appendChild(renderNode(node, 0, modelById || {}));
  });
  tree.appendChild(frag);
  container.appendChild(tree);
}

function emptyTree() {
  var wrap = document.createElement("div");
  wrap.className = "empty-state";
  var big = document.createElement("div");
  big.className = "big";
  big.textContent = "∅";
  var p = document.createElement("div");
  p.textContent = "No folders on disk for this media type.";
  wrap.appendChild(big);
  wrap.appendChild(p);
  return wrap;
}

// Dispatch a node to its folder or leaf renderer.
function renderNode(node, depth, modelById) {
  if (node && node.type === "leaf") return renderLeaf(node, modelById);
  return renderFolder(node, depth, modelById);
}

// ---------------------------------------------------------------------------
// Folder node — disclosure header (poster / name / size / open) + children.
// ---------------------------------------------------------------------------

function renderFolder(node, depth, modelById) {
  var open = isOpen(node, depth);

  var wrap = document.createElement("div");
  wrap.className = "folder-node";
  wrap.dataset.depth = String(depth);

  // The header is the clickable disclosure. A real <button> so it's keyboard- and
  // touch-accessible; it toggles the children region. The open-folder button is a
  // SEPARATE control nested in the header (its click is stopped from toggling).
  var header = document.createElement("button");
  header.type = "button";
  header.className = "folder-head";
  header.setAttribute("aria-expanded", open ? "true" : "false");

  var caret = document.createElement("span");
  caret.className = "folder-caret";
  caret.setAttribute("aria-hidden", "true");
  caret.textContent = "▸";
  header.appendChild(caret);

  // Poster thumbnail (only when the backend says an image exists somewhere under
  // this folder). Real <img> from /api/folder-image; onerror falls back to the
  // gradient tile so a missing/!200 image never shows a broken-image glyph.
  var thumb = document.createElement("span");
  thumb.className = "folder-thumb";
  if (node.has_image) {
    var img = document.createElement("img");
    img.className = "folder-thumb-img";
    img.alt = "";
    img.loading = "lazy";
    img.decoding = "async";
    img.src = "/api/folder-image?path=" + encodeURIComponent(node.path || "");
    img.addEventListener("error", function () {
      // Drop the broken <img> and mark the tile so the CSS gradient shows through.
      if (img.parentNode) img.parentNode.removeChild(img);
      thumb.classList.add("fallback");
    });
    thumb.appendChild(img);
  } else {
    thumb.classList.add("fallback");
  }
  header.appendChild(thumb);

  var meta = document.createElement("span");
  meta.className = "folder-meta";

  var name = document.createElement("span");
  name.className = "folder-name";
  name.textContent = node.name || "";
  meta.appendChild(name);

  var sub = document.createElement("span");
  sub.className = "folder-sub";
  sub.textContent = humanSize(node.size_bytes) + " · " + childSummary(node);
  meta.appendChild(sub);

  header.appendChild(meta);

  // Open-in-Explorer button, pinned to the header's right edge. Stop propagation
  // so clicking it never toggles the disclosure.
  var openBtn = makeOpenFolderButton(node.path);
  header.appendChild(openBtn);

  var children = document.createElement("div");
  children.className = "folder-children";
  if (!open) children.hidden = true;

  // Build children lazily: only populate when first opened, so a deep collapsed
  // TV tree doesn't build thousands of cards up front. Re-opening reuses the
  // already-built DOM.
  var built = false;
  function buildChildren() {
    if (built) return;
    built = true;
    // Order THIS level by the active sort, recursively at every depth (English
    // -> movie folders, show -> seasons -> episodes, …). Sort a COPY so the
    // backend node.children stays in its original order — re-sorting on a sort
    // toggle (full renderTree re-render) and after a model refresh must not
    // corrupt the cached tree. Built at render time, so a collapsed folder
    // expanded later picks up whatever sort is active then.
    var kids = (node.children || []).slice().sort(compareNodes);
    var cfrag = document.createDocumentFragment();
    kids.forEach(function (child) {
      cfrag.appendChild(renderNode(child, depth + 1, modelById));
    });
    children.appendChild(cfrag);
  }
  if (open) buildChildren();

  header.addEventListener("click", function () {
    var nowOpen = children.hidden; // toggling: if currently hidden, we're opening
    EXPANDED[node.path] = nowOpen;
    header.setAttribute("aria-expanded", nowOpen ? "true" : "false");
    wrap.classList.toggle("open", nowOpen);
    if (nowOpen) {
      buildChildren();
      children.hidden = false;
    } else {
      children.hidden = true;
    }
  });

  if (open) wrap.classList.add("open");
  wrap.appendChild(header);
  wrap.appendChild(children);
  return wrap;
}

// "3 folders · 12 items" style summary of a folder's immediate children.
function childSummary(node) {
  var kids = node.children || [];
  var folders = 0;
  var items = 0;
  kids.forEach(function (k) {
    if (k && k.type === "leaf") items += 1;
    else folders += 1;
  });
  var parts = [];
  if (folders) parts.push(folders + (folders === 1 ? " folder" : " folders"));
  if (items) parts.push(items + (items === 1 ? " item" : " items"));
  return parts.length ? parts.join(" · ") : "empty";
}

// ---------------------------------------------------------------------------
// Leaf node — REUSE buildCard() with the enriched MODEL row (joined by id).
// ---------------------------------------------------------------------------

function renderLeaf(leaf, modelById) {
  // Prefer the merged/enriched MODEL row (carries suggested_command/folder/guessed
  // that the raw /api/tree leaf lacks); fall back to the raw leaf so a leaf the
  // model somehow didn't include still renders (without the reclaim extras). The
  // per-leaf open-folder button is added inside buildCard() itself, so tree leaves
  // get it identically to flat-view cards with no extra wiring here.
  var row = (leaf && leaf.id != null && modelById[leaf.id]) || leaf;
  return buildCard(row);
}

// ---------------------------------------------------------------------------
// Shared open-folder button factory (used by folder headers AND leaf cards).
// ---------------------------------------------------------------------------

function makeOpenFolderButton(path) {
  var btn = document.createElement("button");
  btn.type = "button";
  btn.className = "open-folder-btn";
  btn.title = "Open this folder in Explorer (local PC only)";
  btn.setAttribute("aria-label", "Open folder in Explorer");
  // Inline glyph (no external asset): a small "open folder / external" mark.
  btn.textContent = "⤢";
  btn.addEventListener("click", function (e) {
    // Never let this toggle a parent disclosure or trigger a card action/link.
    e.preventDefault();
    e.stopPropagation();
    openFolder(path);
  });
  return btn;
}

// ---------------------------------------------------------------------------
// Tree data accessor — load (cached) /api/tree and return one category's roots.
// ---------------------------------------------------------------------------

// Resolve the tree for `category`, fetching+caching /api/tree on first use. Returns
// a Promise of the category's roots array (possibly []). Errors propagate to the
// caller (app.js shows a status line and falls back to the flat view).
export function treeRootsFor(category) {
  return loadTree().then(function (tree) {
    var roots = (tree && tree.roots) || {};
    return roots[category] || [];
  });
}
