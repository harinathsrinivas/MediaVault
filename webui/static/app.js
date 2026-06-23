/* MediaVault Console — media-type tabs + segmented sub-nav rail (IMP-E14).
 * CANDIDATE B: "Tabs + segmented sub-nav rail (one sub-view at a time)".
 *
 * ES module entrypoint (loaded via <script type="module">). Splits into
 * data.js (fetch+merge model), card.js (card render + actions + job poll),
 * modal.js (confirm modal). Each module passes `node --check` on its own; there
 * is NO build step (hard constraint).
 *
 * LAYOUT
 *   Top tab bar     : Movies / TV series / Anime / Others, with count badges.
 *                     role=tablist, roving tabindex, Left/Right/Home/End nav.
 *   Sub-nav rail    : a SECONDARY segmented control under the tabs listing the 5
 *                     sub-views (Unprepped → Local·not-pushed → Pushed·not-
 *                     archived → Fetched·not-archived → Archived) with counts.
 *                     Selecting ONE shows a SINGLE grid for it (one screen per
 *                     selection — the eventual couch-UI feel), not all stacked.
 *                     aria-selected segments, keyboard navigable. Default = the
 *                     first non-empty sub-view of the active tab.
 *   Grid panel      : role=tabpanel; the single sub-view's cards, with a smooth
 *                     fade/slide swap on every selection change.
 *
 * State grouping follows the SHARED data model in data.js (union of /api/items +
 * reclaim, UNPREPPED sourced from reclaim, ARCHIVED included). XSS-safe: all
 * id/title/path/output render via textContent (see card.js / modal.js).
 */

"use strict";

import {
  loadModel,
  CATEGORY_ORDER,
  CATEGORY_META,
  STATE_ORDER,
  metaFor,
} from "./data.js";
import { buildCard, runAction, setRefreshHandler, destroyRingsIn } from "./card.js";
import { wireModal } from "./modal.js";
import { getSort, setSort, sortItems, SORT_KEYS } from "./sort.js";
import { wireCardGlow } from "./glow.js";
import { renderTree, treeRootsFor } from "./tree.js";

function $(sel, root) {
  return (root || document).querySelector(sel);
}

// ---------------------------------------------------------------------------
// UI state
// ---------------------------------------------------------------------------

// The full merged model (from data.js) and the two selection coordinates.
var MODEL = null;
var activeCategory = CATEGORY_ORDER[0];
var activeState = null; // chosen sub-view within the active category

// id -> enriched MODEL row, for the grouped (tree) view to JOIN raw /api/tree
// leaves back onto their reclaim-enriched card payload. Rebuilt on every load().
var MODEL_BY_ID = {};

// View mode: "decluttered" = the existing flat by-state grid; "grouped" = the
// on-disk folder hierarchy (tree.js). Persisted in sessionStorage so it survives a
// reload within the session. Applies WITHIN whichever media-type tab is active.
var VIEW_KEY = "mv.viewMode";
var viewMode = readViewMode();

function readViewMode() {
  try {
    var v = window.sessionStorage.getItem(VIEW_KEY);
    return v === "grouped" ? "grouped" : "decluttered";
  } catch (e) {
    return "decluttered";
  }
}

function writeViewMode(mode) {
  viewMode = mode === "grouped" ? "grouped" : "decluttered";
  try {
    window.sessionStorage.setItem(VIEW_KEY, viewMode);
  } catch (e) {
    /* sessionStorage unavailable (private mode quota) — keep the in-memory mode. */
  }
}

function isGrouped() {
  return viewMode === "grouped";
}

// Sub-view order = the 5 known states, PLUS any unexpected state that actually
// appears (appended after ARCHIVED) so an odd out-of-sync row is still reachable
// rather than silently dropped. Computed per render from the live model.
function subViewStatesFor(category) {
  var counts = (MODEL && MODEL.counts.byCatState[category]) || {};
  var order = STATE_ORDER.slice();
  // Append any present-but-unknown states in first-seen order.
  Object.keys(counts).forEach(function (s) {
    if (order.indexOf(s) === -1) order.push(s);
  });
  return order;
}

function countFor(category, state) {
  var byCat = (MODEL && MODEL.counts.byCatState[category]) || {};
  return byCat[state] || 0;
}

function categoryCount(category) {
  return (MODEL && MODEL.counts.byCategory[category]) || 0;
}

// First sub-view of a category that has at least one item; null if the category
// is entirely empty.
function firstNonEmptyState(category) {
  var states = subViewStatesFor(category);
  for (var i = 0; i < states.length; i += 1) {
    if (countFor(category, states[i]) > 0) return states[i];
  }
  return null;
}

// ---------------------------------------------------------------------------
// Main tab bar (role=tablist)
// ---------------------------------------------------------------------------

function buildTabs() {
  var bar = $("#tabbar");
  bar.textContent = "";
  CATEGORY_ORDER.forEach(function (cat) {
    var tab = document.createElement("button");
    tab.type = "button";
    tab.className = "tab";
    tab.id = "tab-" + cat;
    tab.setAttribute("role", "tab");
    tab.setAttribute("aria-controls", "panel");
    tab.dataset.cat = cat;

    var label = document.createElement("span");
    label.className = "tab-label";
    label.textContent = CATEGORY_META[cat].label;
    tab.appendChild(label);

    var ct = document.createElement("span");
    ct.className = "tab-count";
    ct.textContent = String(categoryCount(cat));
    tab.appendChild(ct);

    tab.addEventListener("click", function () {
      selectCategory(cat, { focus: true });
    });
    tab.addEventListener("keydown", function (e) {
      handleBarKeydown(e, CATEGORY_ORDER, activeCategory, function (next) {
        selectCategory(next, { focus: true });
      });
    });

    bar.appendChild(tab);
  });
}

// Reflect the active category on the tab bar (aria-selected + roving tabindex).
function refreshTabSelection() {
  var bar = $("#tabbar");
  CATEGORY_ORDER.forEach(function (cat) {
    var tab = $("#tab-" + cat, bar);
    if (!tab) return;
    var on = cat === activeCategory;
    tab.setAttribute("aria-selected", on ? "true" : "false");
    tab.tabIndex = on ? 0 : -1;
    tab.classList.toggle("active", on);
    var ct = $(".tab-count", tab);
    if (ct) ct.textContent = String(categoryCount(cat));
  });
}

// ---------------------------------------------------------------------------
// Sub-nav segmented rail
// ---------------------------------------------------------------------------

function buildSubnav() {
  var rail = $("#subnav");
  rail.textContent = "";
  var states = subViewStatesFor(activeCategory);

  states.forEach(function (state) {
    var n = countFor(activeCategory, state);
    var m = metaFor(state);
    var seg = document.createElement("button");
    seg.type = "button";
    seg.className = "seg s-" + m.cssKey;
    seg.id = "seg-" + state;
    seg.setAttribute("role", "tab");
    seg.setAttribute("aria-controls", "panel");
    seg.dataset.state = state;
    if (n === 0) seg.classList.add("empty");

    var dot = document.createElement("span");
    dot.className = "seg-dot";
    seg.appendChild(dot);

    var label = document.createElement("span");
    label.className = "seg-label";
    label.textContent = m.short;
    seg.appendChild(label);

    var ct = document.createElement("span");
    ct.className = "seg-count";
    ct.textContent = String(n);
    seg.appendChild(ct);

    seg.addEventListener("click", function () {
      selectState(state, { focus: true });
    });
    seg.addEventListener("keydown", function (e) {
      // Arrow nav across the rail; skip empty segments so the keyboard lands on
      // a sub-view that actually has content.
      handleBarKeydown(
        e,
        states,
        activeState,
        function (next) {
          selectState(next, { focus: true });
        },
        function (s) {
          return countFor(activeCategory, s) > 0;
        }
      );
    });

    rail.appendChild(seg);
  });

  refreshSubnavSelection();
}

function refreshSubnavSelection() {
  var rail = $("#subnav");
  var states = subViewStatesFor(activeCategory);
  states.forEach(function (state) {
    var seg = $("#seg-" + state, rail);
    if (!seg) return;
    var on = state === activeState;
    seg.setAttribute("aria-selected", on ? "true" : "false");
    // Roving tabindex: the active segment is the single tab stop; empty
    // segments are removed from the tab order entirely.
    var empty = countFor(activeCategory, state) === 0;
    seg.tabIndex = on ? 0 : -1;
    if (empty && !on) seg.tabIndex = -1;
    seg.classList.toggle("active", on);
  });
}

// ---------------------------------------------------------------------------
// Shared keyboard navigation for a roving-tabindex bar.
//   e        : the keydown event
//   order    : ordered list of values (categories or states)
//   current  : currently-active value
//   onSelect : called with the next value
//   isEnabled: optional predicate; values failing it are skipped (empty segs)
// ---------------------------------------------------------------------------

function handleBarKeydown(e, order, current, onSelect, isEnabled) {
  var key = e.key;
  var horizontal = key === "ArrowRight" || key === "ArrowLeft";
  if (!horizontal && key !== "Home" && key !== "End") return;
  e.preventDefault();

  var enabledOf = function (v) {
    return isEnabled ? isEnabled(v) : true;
  };
  var candidates = order.filter(enabledOf);
  if (candidates.length === 0) candidates = order.slice(); // never trap focus

  if (key === "Home") {
    onSelect(candidates[0]);
    return;
  }
  if (key === "End") {
    onSelect(candidates[candidates.length - 1]);
    return;
  }

  var step = key === "ArrowRight" ? 1 : -1;
  // Find current position within the enabled candidates; if the current value
  // isn't enabled (e.g. it became empty), start from the nearest end.
  var idx = candidates.indexOf(current);
  if (idx === -1) idx = step > 0 ? -1 : candidates.length;
  var next = candidates[(idx + step + candidates.length) % candidates.length];
  onSelect(next);
}

// ---------------------------------------------------------------------------
// Selection -> render
// ---------------------------------------------------------------------------

function selectCategory(cat, opts) {
  if (CATEGORY_ORDER.indexOf(cat) === -1) return;
  activeCategory = cat;
  // Reset the sub-view to the first non-empty one for the new category.
  activeState = firstNonEmptyState(cat) || subViewStatesFor(cat)[0];
  refreshTabSelection();
  buildSubnav();
  renderPanel(true);
  if (opts && opts.focus) {
    var tab = $("#tab-" + cat);
    if (tab) tab.focus();
  }
}

function selectState(state, opts) {
  if (state === activeState) {
    // Re-focus only; no transition needed.
    if (opts && opts.focus) {
      var same = $("#seg-" + state);
      if (same) same.focus();
    }
    return;
  }
  activeState = state;
  refreshSubnavSelection();
  renderPanel(true);
  if (opts && opts.focus) {
    var seg = $("#seg-" + state);
    if (seg) seg.focus();
  }
}

// Show/hide the controls that only make sense in the flat (decluttered) view. In
// grouped mode the tree spans ALL states of a category, so the state sub-view rail
// (#subnav) is hidden (it would imply a filter the tree can't honor). The
// Size/Title/Year sort bar (#sortbar) STAYS VISIBLE: the grouped tree applies the
// active sort RECURSIVELY at every nesting level (tree.js sorts each level via
// compareNodes), and its key/dir handlers call renderPanel(false), which in
// grouped mode does a full renderTree re-render — so changing the sort instantly
// re-orders the whole tree (the EXPANDED map preserves which folders are open).
function syncViewChrome() {
  var grouped = isGrouped();
  var subnav = $("#subnav");
  var sortbar = $("#sortbar");
  // Hide the per-state rail in grouped mode. We set BOTH the `hidden` attribute
  // (semantics) and an explicit `.is-hidden` class (belt-and-suspenders): the
  // `.subnav` rule has an explicit `display:flex`, which would override the UA
  // `[hidden]{display:none}` default unless a `.subnav[hidden]` rule restores it.
  // styles.css carries that rule AND `.subnav.is-hidden`, so the rail is hidden
  // regardless of UA `[hidden]` handling. Without this, grouped mode showed every
  // state segment over a whole-category tree, making one ARCHIVED title appear
  // under multiple state "blocks" while each leaf badge still read "Archived".
  if (subnav) {
    subnav.hidden = grouped;
    subnav.classList.toggle("is-hidden", grouped);
  }
  if (sortbar) sortbar.hidden = false;
  document.body.classList.toggle("grouped-view", grouped);
}

// Render the panel for the active media-type tab. In DECLUTTERED mode this is the
// single flat grid for (activeCategory, activeState). In GROUPED mode it is the
// on-disk folder hierarchy for activeCategory (all states), rendered by tree.js.
// When `animate` is true, fade/slide the panel: hide -> swap -> show.
function renderPanel(animate) {
  var panel = $("#panel");
  syncViewChrome();
  if (isGrouped()) {
    panel.setAttribute("aria-label", CATEGORY_META[activeCategory].label + " folders");
    panel.removeAttribute("aria-labelledby");
  } else {
    panel.setAttribute("aria-labelledby", "seg-" + activeState);
  }

  function paint() {
    // Dispose any active fetch-rings BEFORE emptying the panel so their
    // ResizeObservers are disconnected (teardown graft, change #4). This is the
    // single grid-clear path — every tab/sub-view switch and post-fetch refresh
    // routes through here. (The tree's own clear ALSO calls destroyRingsIn, so the
    // invariant holds whichever branch paints.)
    if (isGrouped()) {
      paintTree(panel);
    } else {
      paintFlat(panel);
    }

    // Reveal (next frame so the transition runs).
    requestAnimationFrame(function () {
      panel.classList.remove("swapping");
    });
  }

  if (animate) {
    panel.classList.add("swapping");
    // Wait one frame for the fade-out, then swap + fade-in.
    requestAnimationFrame(function () {
      setTimeout(paint, 110);
    });
  } else {
    paint();
  }
}

// Flat (decluttered) grid for the active (category, state) — the original path.
function paintFlat(panel) {
  destroyRingsIn(panel);
  panel.textContent = "";

  var rows = (MODEL ? MODEL.items : []).filter(function (it) {
    return it.category === activeCategory && it.state === activeState;
  });
  // Client-side sort (change #1): re-order the already-loaded rows by the
  // persisted key+direction (default size-desc). No refetch.
  rows = sortItems(rows);

  if (rows.length === 0) {
    panel.appendChild(buildEmptyState());
  } else {
    var grid = document.createElement("div");
    grid.className = "grid";
    var frag = document.createDocumentFragment();
    rows.forEach(function (it) {
      frag.appendChild(buildCard(it));
    });
    grid.appendChild(frag);
    panel.appendChild(grid);
  }
}

// Grouped (folder-tree) view for the active category. The tree comes from the
// cached /api/tree (loadTree in data.js); leaf cards are joined back onto the
// enriched MODEL rows (MODEL_BY_ID) so their Copy command/folder survive. The
// fetch is async; while it resolves we leave the current panel contents in place
// (the fade is already running) rather than flashing an empty grid. A failure
// shows a status line and clears the panel (the toggle stays on Grouped so the
// user can retry by re-selecting the tab).
function paintTree(panel) {
  // Capture the category this paint is for; if the user switches tabs before the
  // tree resolves, a stale resolution must NOT overwrite the newer view.
  var forCategory = activeCategory;
  treeRootsFor(forCategory)
    .then(function (roots) {
      if (!isGrouped() || activeCategory !== forCategory) return; // superseded
      renderTree(panel, roots, MODEL_BY_ID);
    })
    .catch(function (err) {
      if (!isGrouped() || activeCategory !== forCategory) return;
      destroyRingsIn(panel);
      panel.textContent = "";
      setStatus(
        "Failed to load the folder tree — " + ((err && err.message) || err),
        true
      );
    });
}

function buildEmptyState() {
  var wrap = document.createElement("div");
  wrap.className = "empty-state";
  var big = document.createElement("div");
  big.className = "big";
  big.textContent = "∅";
  var p = document.createElement("div");
  var m = metaFor(activeState);
  p.textContent =
    "No items in this view — " +
    CATEGORY_META[activeCategory].label +
    " · " +
    m.short +
    ".";
  wrap.appendChild(big);
  wrap.appendChild(p);
  return wrap;
}

// ---------------------------------------------------------------------------
// Header stats + status line
// ---------------------------------------------------------------------------

function setStatus(msg, isErr) {
  var el = $("#status-line");
  el.textContent = msg || "";
  el.classList.toggle("err", !!isErr);
}

function refreshHero() {
  var totalEl = $("#reclaim-total");
  var human = MODEL && MODEL.reclaimTotalHuman;
  totalEl.textContent = human || "0 B";
  totalEl.classList.toggle("empty", !MODEL || MODEL.reclaimCount === 0);
  var countEl = $("#reclaim-count");
  var n = MODEL ? MODEL.reclaimCount : 0;
  countEl.textContent = n === 1 ? "1 reclaimable item" : n + " reclaimable items";
}

// ---------------------------------------------------------------------------
// Load + (re)render
// ---------------------------------------------------------------------------

// Render everything from the current MODEL. On the FIRST paint, or when the
// previously-active category/sub-view no longer has any items, fall back to a
// sensible non-empty default; otherwise PRESERVE the user's current view across
// a post-job refresh so a finished action doesn't yank them elsewhere.
function renderAll(isFirst) {
  refreshHero();
  buildTabs();

  // Preserve category if it still exists in the order (it always does), but if
  // it is empty and this is the first paint, jump to the first category that
  // has items so the user lands on something useful.
  if (isFirst) {
    if (categoryCount(activeCategory) === 0) {
      var firstCat = CATEGORY_ORDER.filter(function (c) {
        return categoryCount(c) > 0;
      })[0];
      if (firstCat) activeCategory = firstCat;
    }
    activeState = firstNonEmptyState(activeCategory) || STATE_ORDER[0];
  } else {
    // Re-render after a job. Keep the active sub-view if it still has items;
    // else drop to the first non-empty sub-view of the same category; else the
    // first category that has anything.
    if (countFor(activeCategory, activeState) === 0) {
      var fallback = firstNonEmptyState(activeCategory);
      if (fallback) {
        activeState = fallback;
      } else {
        var nextCat = CATEGORY_ORDER.filter(function (c) {
          return categoryCount(c) > 0;
        })[0];
        if (nextCat) {
          activeCategory = nextCat;
          activeState = firstNonEmptyState(nextCat) || STATE_ORDER[0];
        } else {
          activeState = STATE_ORDER[0];
        }
      }
    }
  }

  refreshTabSelection();
  buildSubnav();
  renderPanel(false);
  setStatus("");
}

function load(isFirst) {
  if (isFirst) setStatus("Scanning library…");
  loadModel()
    .then(function (model) {
      MODEL = model;
      // Index the enriched rows by id so the grouped (tree) view can JOIN raw
      // /api/tree leaves back onto their reclaim-enriched card payload.
      MODEL_BY_ID = {};
      (model.items || []).forEach(function (row) {
        if (row && row.id != null) MODEL_BY_ID[row.id] = row;
      });
      renderAll(isFirst);
    })
    .catch(function (err) {
      setStatus(
        "Failed to load library — " + ((err && err.message) || err),
        true
      );
    });
}

// ---------------------------------------------------------------------------
// Sort library (global action)
// ---------------------------------------------------------------------------

function wireSort() {
  var btn = $("#btn-sort");
  var panel = $("#sort-job-panel");
  btn.addEventListener("click", function () {
    runAction("sort", {}, btn, panel);
  });
}

// ---------------------------------------------------------------------------
// Sort control (client-side ordering of the current grid)
// ---------------------------------------------------------------------------

// Human labels for the sort keys (sort.js owns the key list + comparator).
var SORT_KEY_LABELS = { size: "Size", title: "Title", year: "Year" };

// Build the segmented key buttons + a direction toggle. Re-orders the visible
// grid instantly on change; the chosen key/direction persist in sort.js state so
// they survive tab/sub-view switches and the post-fetch refresh.
function buildSortbar() {
  var bar = $("#sortbar");
  bar.textContent = "";

  var label = document.createElement("span");
  label.className = "sortbar-label";
  label.textContent = "Sort";
  bar.appendChild(label);

  var group = document.createElement("div");
  group.className = "sort-keys";
  group.setAttribute("role", "group");
  group.setAttribute("aria-label", "Sort key");

  SORT_KEYS.forEach(function (key) {
    var b = document.createElement("button");
    b.type = "button";
    b.className = "sort-key";
    b.dataset.key = key;
    b.textContent = SORT_KEY_LABELS[key] || key;
    b.addEventListener("click", function () {
      setSort(key, null);
      refreshSortbar();
      renderPanel(false); // instant reorder, no view swap
    });
    group.appendChild(b);
  });
  bar.appendChild(group);

  var dir = document.createElement("button");
  dir.type = "button";
  dir.className = "sort-dir";
  dir.id = "sort-dir";
  dir.addEventListener("click", function () {
    var cur = getSort();
    setSort(null, cur.dir === "asc" ? "desc" : "asc");
    refreshSortbar();
    renderPanel(false);
  });
  bar.appendChild(dir);

  refreshSortbar();
}

// Reflect the active key (pressed state) + direction (arrow + label) on the bar.
function refreshSortbar() {
  var s = getSort();
  var bar = $("#sortbar");
  var keys = bar.querySelectorAll(".sort-key");
  for (var i = 0; i < keys.length; i += 1) {
    var on = keys[i].dataset.key === s.key;
    keys[i].classList.toggle("active", on);
    keys[i].setAttribute("aria-pressed", on ? "true" : "false");
  }
  var dir = $("#sort-dir");
  if (dir) {
    var asc = s.dir === "asc";
    // Arrow points the way the values run; label spells it out for clarity.
    dir.textContent = (asc ? "↑" : "↓") + " " + (asc ? "Asc" : "Desc");
    dir.setAttribute(
      "aria-label",
      "Sort direction: " + (asc ? "ascending" : "descending") + " (toggle)"
    );
    dir.setAttribute("aria-pressed", asc ? "false" : "true");
  }
}

// ---------------------------------------------------------------------------
// View toggle (Grouped <-> Decluttered)
// ---------------------------------------------------------------------------

// A small segmented control mirroring the sort bar: two buttons that flip the
// module view mode and instantly re-render the panel (no refetch — the flat view
// renders from the loaded MODEL and the tree from the cached /api/tree). The
// active mode is reflected via aria-pressed. Persisted in sessionStorage.
var VIEW_MODES = [
  { mode: "grouped", label: "Grouped", glyph: "▤", hint: "On-disk folder hierarchy" },
  { mode: "decluttered", label: "Decluttered", glyph: "▦", hint: "Flat, grouped by disk state" },
];

function buildViewbar() {
  var bar = $("#viewbar");
  if (!bar) return;
  bar.textContent = "";

  var label = document.createElement("span");
  label.className = "viewbar-label";
  label.textContent = "View";
  bar.appendChild(label);

  var group = document.createElement("div");
  group.className = "view-modes";
  group.setAttribute("role", "group");
  group.setAttribute("aria-label", "View mode");

  VIEW_MODES.forEach(function (def) {
    var b = document.createElement("button");
    b.type = "button";
    b.className = "view-mode";
    b.dataset.mode = def.mode;
    b.title = def.hint;

    var g = document.createElement("span");
    g.className = "view-mode-glyph";
    g.setAttribute("aria-hidden", "true");
    g.textContent = def.glyph;
    b.appendChild(g);

    var t = document.createElement("span");
    t.className = "view-mode-text";
    t.textContent = def.label;
    b.appendChild(t);

    b.addEventListener("click", function () {
      selectViewMode(def.mode);
    });
    group.appendChild(b);
  });
  bar.appendChild(group);

  refreshViewbar();
}

// Reflect the active mode (pressed state) on the toggle.
function refreshViewbar() {
  var bar = $("#viewbar");
  if (!bar) return;
  var btns = bar.querySelectorAll(".view-mode");
  for (var i = 0; i < btns.length; i += 1) {
    var on = btns[i].dataset.mode === viewMode;
    btns[i].classList.toggle("active", on);
    btns[i].setAttribute("aria-pressed", on ? "true" : "false");
  }
}

function selectViewMode(mode) {
  if (mode === viewMode) return;
  writeViewMode(mode);
  refreshViewbar();
  setStatus(""); // clear any stale tree-load error from a prior attempt
  renderPanel(true); // animated swap; flat from MODEL, tree from cached /api/tree
}

// ---------------------------------------------------------------------------
// Init
// ---------------------------------------------------------------------------

// Server-side DEMO/SAFE mode probe (IMP-E14). On load, ask the backend whether
// it is the simulated review build; if so, reveal the persistent banner. The
// banner text is static markup, so this only flips visibility — no untrusted
// data is interpolated (XSS-safe). A failed/absent probe leaves the banner
// hidden (fail-safe toward the normal real UI; the SERVER still enforces demo).
function checkDemoMode() {
  fetch("/api/mode")
    .then(function (res) {
      return res.ok ? res.json() : null;
    })
    .then(function (data) {
      if (data && data.demo === true) {
        var banner = $("#demo-banner");
        if (banner) {
          banner.hidden = false;
          banner.classList.add("show");
          document.body.classList.add("demo-mode");
          // Pin the sticky header directly below the banner by exposing the
          // banner's MEASURED height as --demo-offset (handles the banner
          // wrapping to two lines on narrow/mobile viewports). Re-measure on
          // resize so the header stays flush if the banner reflows.
          var applyOffset = function () {
            var h = Math.ceil(banner.getBoundingClientRect().height);
            document.documentElement.style.setProperty("--demo-offset", h + "px");
          };
          applyOffset();
          window.addEventListener("resize", applyOffset);
        }
      }
    })
    .catch(function () {
      /* probe failed — leave the banner hidden; server still enforces safety. */
    });
}

function init() {
  checkDemoMode();
  wireModal();
  wireSort();
  buildSortbar();
  buildViewbar();
  // Cursor-following glow on the cards. Delegated to the stable #panel (created
  // once in index.html; only its children are cleared on re-render), so every
  // freshly-rendered card is covered with no per-card listener to leak.
  wireCardGlow($("#panel"));
  // After any terminal job, reload the model and repaint (preserving the view).
  setRefreshHandler(function () {
    load(false);
  });
  load(true);
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", init);
} else {
  init();
}
