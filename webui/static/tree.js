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
// Grid drill-down level extraction (PURE, DOM-free — importable by the node guard
// like pruneTreeByState / compareNodes).
//
// The grouped tab's GRID style shows ONE level at a time: a folder's child FOLDERS
// as navigable boxes, plus any LEAF items at that level as cards. The nav path is
// an array of folder NAMES from the category root (e.g. ["English"] or
// ["Show","Season 1"]); [] is the root level. gridChildrenAt walks that path
// through `roots` and returns { folders, leaves } for the level it lands on —
// folders and leaves in their natural input order (the renderer sorts each group
// via compareNodes, so this stays free of sort.js's mutable state and is trivially
// testable).
//
// `roots` is whatever the caller passes: the raw category roots for "All", or the
// output of pruneTreeByState(...) for an active state filter — so the SAME prune
// the list view uses carries straight over (a box appears only if it has a matching
// descendant leaf; folder sizes/counts reflect the filter). A path that no longer
// resolves (the folder was pruned away, or never existed) returns an EMPTY level
// rather than throwing, so a stale breadcrumb is always recoverable.
//
// Pure: never mutates `roots`; the returned arrays hold references to the existing
// nodes (callers only read name/path/size/children/id off them).
// ---------------------------------------------------------------------------

export function gridChildrenAt(roots, path) {
  var level = roots || [];
  var segments = path || [];
  for (var i = 0; i < segments.length; i += 1) {
    var next = null;
    for (var j = 0; j < level.length; j += 1) {
      var n = level[j];
      if (n && n.type === "folder" && n.name === segments[i]) {
        next = n;
        break;
      }
    }
    if (!next) return { folders: [], leaves: [] };
    level = next.children || [];
  }
  var folders = [];
  var leaves = [];
  for (var k = 0; k < level.length; k += 1) {
    var node = level[k];
    if (!node) continue;
    if (node.type === "leaf") leaves.push(node);
    else folders.push(node);
  }
  return { folders: folders, leaves: leaves };
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
  container.appendChild(buildTreeFragment(roots, modelById));
}

// Build the tree as a detached DocumentFragment WITHOUT touching any container.
// This lets the caller (app.js paintTree) assemble the whole tree off-DOM and
// swap it into #panel in ONE atomic operation — so switching to Grouped never
// flashes an empty/loading panel while the async /api/tree resolves. The returned
// fragment holds either the .tree element or the empty-state node. renderTree()
// above is the in-place wrapper (clear container, then append this) used by the
// post-fetch refresh, so its behavior is unchanged.
//
// NOTE: this does NOT dispose fetch-rings — it only constructs new DOM. Ring
// teardown for the OUTGOING content is the swapping caller's responsibility
// (app.js destroys rings in #panel immediately before the atomic replace), which
// keeps the no-flash swap path and the teardown invariant both intact.
export function buildTreeFragment(roots, modelById) {
  var frag = document.createDocumentFragment();
  var list = roots || [];
  if (list.length === 0) {
    frag.appendChild(emptyTree());
    return frag;
  }

  // Order the TOP level by the active sort (size/title/year). Sort a COPY so the
  // cached /api/tree roots array is never mutated — toggling sort back and forth
  // (and re-sorting after a model refresh) must stay correct and leave the model
  // untouched. Every deeper level is sorted the same way in renderFolder below.
  var ordered = list.slice().sort(compareNodes);

  var tree = document.createElement("div");
  tree.className = "tree";
  var inner = document.createDocumentFragment();
  ordered.forEach(function (node) {
    inner.appendChild(renderNode(node, 0, modelById || {}));
  });
  tree.appendChild(inner);
  frag.appendChild(tree);
  return frag;
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
// GRID drill-down view (IMP-E16) — one level at a time as boxes + leaf cards.
//
// buildGridFragment renders the level identified by `path` (folder names from the
// category root): a .crumbs breadcrumb (Back + clickable trail), the level's child
// FOLDERS as a responsive .grid-view of navigable .folder-box tiles (folders
// first), then any LEAF items at that level as the SAME buildCard cards the list /
// flat views use — so posters, badges, actions and the hover dossier all carry
// over for free. Folder boxes are NOT cards: each is just a tile whose click drills
// in via the onNavigate callback (app.js owns the nav stack + repaints).
//
// `roots` is already pruned by the caller for an active state filter (or the raw
// roots for "All"), and each level is ordered by the active sort (compareNodes) —
// exactly like the list view. Built entirely off-DOM so app.js can swap it into
// #panel atomically. XSS-safe: names / sizes via textContent, image src
// encodeURIComponent'd.
// ---------------------------------------------------------------------------

export function buildGridFragment(roots, modelById, path, opts) {
  opts = opts || {};
  var onNavigate =
    typeof opts.onNavigate === "function" ? opts.onNavigate : function () {};
  var rootLabel = opts.rootLabel || "All";
  var navPath = (path || []).slice();
  var byId = modelById || {};

  var frag = document.createDocumentFragment();
  var level = gridChildrenAt(roots, navPath);
  var isEmpty = level.folders.length === 0 && level.leaves.length === 0;

  // Breadcrumb whenever we're drilled in (so Back / jump always works); at the
  // EMPTY root we drop it and show the same empty-state the list view uses.
  if (navPath.length > 0 || !isEmpty) {
    frag.appendChild(buildCrumbs(navPath, rootLabel, onNavigate));
  }

  if (isEmpty) {
    frag.appendChild(emptyGrid(navPath.length === 0));
    return frag;
  }

  // Folders first — a responsive grid of navigable boxes, ordered by active sort.
  if (level.folders.length) {
    var folders = level.folders.slice().sort(compareNodes);
    var grid = document.createElement("div");
    grid.className = "grid-view";
    folders.forEach(function (folder) {
      grid.appendChild(buildFolderBox(folder, navPath, onNavigate));
    });
    frag.appendChild(grid);
  }

  // Then leaf items at this level — the SAME enriched cards as every other view
  // (joined onto the MODEL row by id, exactly like renderLeaf above).
  if (level.leaves.length) {
    var leaves = level.leaves.slice().sort(compareNodes);
    var cardGrid = document.createElement("div");
    cardGrid.className = "grid grid-view-cards";
    leaves.forEach(function (leaf) {
      var row = (leaf && leaf.id != null && byId[leaf.id]) || leaf;
      cardGrid.appendChild(buildCard(row));
    });
    frag.appendChild(cardGrid);
  }

  return frag;
}

// One navigable folder tile: cover image (or gradient + folder-glyph fallback),
// name, real size and a child summary. The whole tile is a <button>; clicking it
// drills in by pushing this folder's name onto the nav path. Not a card (no
// dossier / actions) — just a tile, so no per-leaf wiring leaks here.
function buildFolderBox(folder, parentPath, onNavigate) {
  var box = document.createElement("button");
  box.type = "button";
  box.className = "folder-box";
  box.setAttribute(
    "aria-label",
    "Open folder " + (folder.name || "") + " — " + childSummary(folder)
  );

  var cover = document.createElement("span");
  cover.className = "folder-box-cover";
  if (folder.has_image) {
    var img = document.createElement("img");
    img.className = "folder-box-img";
    img.alt = "";
    img.loading = "lazy";
    img.decoding = "async";
    img.src = "/api/folder-image?path=" + encodeURIComponent(folder.path || "");
    img.addEventListener("error", function () {
      // Drop the broken <img> so the CSS gradient + folder glyph shows through.
      if (img.parentNode) img.parentNode.removeChild(img);
      cover.classList.add("fallback");
    });
    cover.appendChild(img);
  } else {
    cover.classList.add("fallback");
  }
  box.appendChild(cover);

  var meta = document.createElement("span");
  meta.className = "folder-box-meta";

  var name = document.createElement("span");
  name.className = "folder-box-name";
  name.textContent = folder.name || "";
  meta.appendChild(name);

  var sub = document.createElement("span");
  sub.className = "folder-box-sub";
  sub.textContent = humanSize(folder.size_bytes) + " · " + childSummary(folder);
  meta.appendChild(sub);

  box.appendChild(meta);

  box.addEventListener("click", function () {
    onNavigate(parentPath.concat([folder.name]));
  });
  return box;
}

// Breadcrumb bar: a Back affordance (pops one level; disabled at root) then the
// clickable trail rootLabel / seg / seg…. The last crumb is the CURRENT level
// (plain text, aria-current); each earlier crumb jumps straight to that level.
function buildCrumbs(path, rootLabel, onNavigate) {
  var bar = document.createElement("nav");
  bar.className = "crumbs";
  bar.setAttribute("aria-label", "Folder breadcrumb");

  var back = document.createElement("button");
  back.type = "button";
  back.className = "crumb-back";
  back.textContent = "‹ Back";
  if (!path || path.length === 0) {
    back.disabled = true;
  } else {
    back.setAttribute("aria-label", "Back one level");
    back.addEventListener("click", function () {
      onNavigate(path.slice(0, path.length - 1));
    });
  }
  bar.appendChild(back);

  var trail = [{ label: rootLabel, to: [] }];
  (path || []).forEach(function (seg, i) {
    trail.push({ label: seg, to: (path || []).slice(0, i + 1) });
  });

  trail.forEach(function (crumb, i) {
    if (i > 0) {
      var sep = document.createElement("span");
      sep.className = "crumb-sep";
      sep.setAttribute("aria-hidden", "true");
      sep.textContent = "/";
      bar.appendChild(sep);
    }
    if (i === trail.length - 1) {
      var cur = document.createElement("span");
      cur.className = "crumb crumb-current";
      cur.setAttribute("aria-current", "page");
      cur.textContent = crumb.label;
      bar.appendChild(cur);
    } else {
      var link = document.createElement("button");
      link.type = "button";
      link.className = "crumb";
      link.textContent = crumb.label;
      link.addEventListener("click", function () {
        onNavigate(crumb.to);
      });
      bar.appendChild(link);
    }
  });

  return bar;
}

// Empty-level placeholder. Mirrors the list view's emptyTree at the root and gives
// a filter-aware message deeper in (where the breadcrumb above still escapes back).
function emptyGrid(atRoot) {
  var wrap = document.createElement("div");
  wrap.className = "empty-state";
  var big = document.createElement("div");
  big.className = "big";
  big.textContent = "∅";
  var p = document.createElement("div");
  p.textContent = atRoot
    ? "No folders on disk for this media type."
    : "This folder is empty in the current filter.";
  wrap.appendChild(big);
  wrap.appendChild(p);
  return wrap;
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
