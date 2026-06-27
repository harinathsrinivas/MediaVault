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
  ALL_STATE,
  metaFor,
} from "./data.js";
import { buildCard, runAction, setRefreshHandler, destroyRingsIn } from "./card.js";
import { wireModal } from "./modal.js";
import { getSort, setSort, sortItems, SORT_KEYS } from "./sort.js";
import { wireCardGlow } from "./glow.js";
import { wireHoverPreview, openPreviewForCard } from "./preview.js";
// palette.js is intentionally NOT imported statically — it is lazy-loaded on first
// use (⌘K / Ctrl-K / "/" or the header Search button) via ensurePalette() below,
// which keeps it out of the first-paint module graph (IMP-E16 D5). Do not restore a
// static import here: that would re-eager it for every visitor.
import { wireHero } from "./hero.js";
import {
  buildTreeFragment,
  buildGridFragment,
  treeRootsFor,
  pruneTreeByState,
} from "./tree.js";
import { authFetch, bootstrapToken } from "./auth.js";
// admin.js is intentionally NOT imported statically — it is lazy-loaded ONLY for the
// local owner (after the tiny /api/whoami probe reports is_admin) via initAdminLazy()
// below, so a remote/token device never fetches it (IMP-E16 D5). Do not restore a
// static import here: that would re-eager the ~25KB Access console for everyone.

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

// Cinematic parallax hero strip (IMP-E16 D4). Built once in init() over the #hero
// section; its { refresh } re-picks the featured set for the active category. Null
// until init wires it, so every call site guards with `hero && hero.refresh()`.
var hero = null;

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

// Grouped-mode presentation STYLE: "list" = today's collapsible folder tree
// (tree.js renderFolder), "grid" = the drill-down grid of folder boxes
// (buildGridFragment). Only meaningful while viewMode === "grouped"; decluttered
// ignores it. Persisted separately in sessionStorage with a "list" default so the
// existing behavior is unchanged on first run and the choice survives a reload.
var GROUPED_STYLE_KEY = "mv_grouped_style";
var groupedStyle = readGroupedStyle();

function readGroupedStyle() {
  try {
    var v = window.sessionStorage.getItem(GROUPED_STYLE_KEY);
    return v === "grid" ? "grid" : "list";
  } catch (e) {
    return "list";
  }
}

function writeGroupedStyle(style) {
  groupedStyle = style === "grid" ? "grid" : "list";
  try {
    window.sessionStorage.setItem(GROUPED_STYLE_KEY, groupedStyle);
  } catch (e) {
    /* sessionStorage unavailable — keep the in-memory style. */
  }
}

function isGridStyle() {
  return isGrouped() && groupedStyle === "grid";
}

// Grid drill-down nav stack: an array of folder NAMES from the active category
// root (e.g. ["English"] or ["Show","Season 1"]). [] = the category root level.
// Reset to root whenever the media-type tab OR the state filter changes (the two
// resets the spec mandates); kept across List<->Grid toggles so re-entering the
// grid resumes where you were. gridPendingScrollTop, when non-null, pins the next
// paint's scroll position (0 on a drill / jump so a new level starts at the top;
// null preserves the live scroll for an in-place re-render like a sort change or a
// post-job refresh).
var gridPath = [];
var gridPendingScrollTop = null;

function resetGridNav() {
  gridPath = [];
  gridPendingScrollTop = 0;
}

// Drill into / jump to a level: set the path and repaint. Always lands at the top
// of the new level. Wired into every folder box + breadcrumb crumb by tree.js.
function navigateGrid(nextPath) {
  gridPath = (nextPath || []).slice();
  gridPendingScrollTop = 0;
  renderPanel(true); // morph the drill-in / breadcrumb jump (VT when available)
}

// Sub-view order = the leading "All" segment, THEN the 5 known states, PLUS any
// unexpected state that actually appears (appended after ARCHIVED) so an odd
// out-of-sync row is still reachable rather than silently dropped. "All" leads in
// BOTH view modes and is the default. Computed per render from the live model.
function subViewStatesFor(category) {
  var counts = (MODEL && MODEL.counts.byCatState[category]) || {};
  var order = [ALL_STATE].concat(STATE_ORDER);
  // Append any present-but-unknown states in first-seen order.
  Object.keys(counts).forEach(function (s) {
    if (order.indexOf(s) === -1) order.push(s);
  });
  return order;
}

// Count shown on a rail segment. "All" shows the category total; every real state
// shows its per-(category,state) bucket count.
function countFor(category, state) {
  if (state === ALL_STATE) return categoryCount(category);
  var byCat = (MODEL && MODEL.counts.byCatState[category]) || {};
  return byCat[state] || 0;
}

function categoryCount(category) {
  return (MODEL && MODEL.counts.byCategory[category]) || 0;
}

// First sub-view of a category that has at least one item; null if the category
// is entirely empty. "All" leads the order and counts the whole category, so a
// non-empty category resolves to "All" here — making it the natural default on
// first load and the post-job fallback.
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
  // Reset the sub-view to "All" (the first non-empty sub-view for any non-empty
  // category; falls back to "All" for an entirely empty category too).
  activeState = firstNonEmptyState(cat) || ALL_STATE;
  resetGridNav(); // a media-type change restarts the grid drill-down at root
  refreshTabSelection();
  buildSubnav();
  // Re-pick the hero BEFORE the panel's view-transition snapshot so the band
  // settles in the held-still root layer (instant cut, no morph flicker).
  if (hero) hero.refresh();
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
  resetGridNav(); // a state-filter change restarts the grid drill-down at root
  refreshSubnavSelection();
  renderPanel(true);
  if (opts && opts.focus) {
    var seg = $("#seg-" + state);
    if (seg) seg.focus();
  }
}

// ---------------------------------------------------------------------------
// Command-palette jump (IMP-E16 D2)
// ---------------------------------------------------------------------------

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

// ---------------------------------------------------------------------------
// View-Transitions morph (IMP-E16 D3)
// ---------------------------------------------------------------------------

// True only when the native View-Transitions API is present AND the user has not
// requested reduced motion. Guarded so older Safari/Firefox (no
// startViewTransition) and reduced-motion users transparently keep the classic
// atomic swap with no morph.
function canUseViewTransition() {
  return (
    typeof document !== "undefined" &&
    typeof document.startViewTransition === "function" &&
    !prefersReducedMotion()
  );
}

// Run `fn` (a SYNCHRONOUS DOM mutation — the panel repaint) inside a native View
// Transition so the browser cross-fades the #panel region from its old content to
// its new content (the morph is scoped to #panel + tuned in styles.css). When the
// API is unavailable or motion is reduced, `fn` is called directly — identical end
// state, today's instant swap. NEVER throws: any unexpected failure degrades to a
// direct call so a repaint can't be dropped.
function withViewTransition(fn) {
  if (!canUseViewTransition()) {
    fn();
    return;
  }
  try {
    document.startViewTransition(function () {
      fn();
    });
  } catch (e) {
    fn();
  }
}

// Jump straight to a library entry by id (the palette's title activation). The
// flat DECLUTTERED grid renders synchronously from the already-loaded MODEL and
// always contains the card for (category, state), so a jump is reliable and
// flash-free — unlike the grouped tree, which is async + folder-collapsed +
// state-pruned. So: force decluttered, switch to the item's category + its own
// state sub-view, repaint synchronously, then scroll + pulse + open its dossier.
// Returns true when the item exists in the current model.
function jumpToItem(id) {
  if (!MODEL) return false;
  var item = MODEL_BY_ID[id];
  if (!item) return false;

  // The flat grid is the dependable target — leave grouped mode if we're in it.
  if (isGrouped()) {
    writeViewMode("decluttered");
    refreshViewbar();
  }

  // Land on the item's exact coordinates (its category tab + its state sub-view).
  // item.state is always a present state for this category, so its rail segment
  // exists and paintFlat's `it.state === activeState` filter keeps the card.
  activeCategory = item.category;
  activeState = item.state;
  resetGridNav();
  refreshTabSelection();
  buildSubnav();
  // Keep the hero in sync if the jump crossed into another media-type tab.
  if (hero) hero.refresh();
  renderPanel(false); // synchronous paintFlat — the card is now in #panel

  revealCardForId(id);
  return true;
}

// Find the rendered .card for an id (identity match on the stamped __mvItem, so
// any odd id is handled without CSS-selector escaping), scroll it into view,
// pulse a highlight ring, and open its dossier. Best-effort: a missing card (e.g.
// filtered out) simply does nothing.
function revealCardForId(id) {
  var panel = $("#panel");
  if (!panel) return;
  var cards = panel.querySelectorAll(".card");
  var target = null;
  for (var i = 0; i < cards.length; i += 1) {
    var it = cards[i].__mvItem;
    if (it && it.id === id) {
      target = cards[i];
      break;
    }
  }
  if (!target) return;

  try {
    target.scrollIntoView({
      block: "center",
      inline: "nearest",
      behavior: prefersReducedMotion() ? "auto" : "smooth",
    });
  } catch (e) {
    target.scrollIntoView();
  }

  // Highlight pulse — restart the animation if the same card was just jumped to.
  target.classList.remove("cmdk-flash");
  void target.offsetWidth; // force reflow so re-adding the class replays it
  target.classList.add("cmdk-flash");
  window.setTimeout(function () {
    target.classList.remove("cmdk-flash");
  }, 1600);

  // Open the cinematic dossier for the jumped-to card (reuses preview.js). The
  // smooth scroll fires scroll events that preview.js re-anchors against, so the
  // anchored panel tracks the card as it settles. Deferred one frame so the card
  // has its post-scroll rect before the first position() measurement.
  window.requestAnimationFrame(function () {
    if (target.isConnected) openPreviewForCard(target);
  });
}

// Sync the chrome around the panel to the active view mode. The state sub-view
// rail (#subnav) now shows in BOTH modes: in decluttered it picks the flat grid's
// state; in grouped it FILTERS the folder tree (All → whole tree; a state → the
// tree pruned to that state — see paintTree). The Size/Title/Year sort bar
// (#sortbar) also stays visible in both modes: the grouped tree applies the active
// sort RECURSIVELY at every nesting level (tree.js sorts each level via
// compareNodes), and its key/dir handlers call renderPanel(false), which in
// grouped mode does a full renderTree re-render — so changing the sort (or the
// state filter) instantly re-renders the tree (the EXPANDED map preserves which
// folders are open).
//
// We force `subnav.hidden = false` here so the rail is never left hidden by a
// prior build. The defensive `.subnav[hidden]{display:none}` rule in styles.css
// stays (so any future code that DOES set `hidden` still hides it), but in normal
// flow #subnav is simply always shown now.
function syncViewChrome() {
  var grouped = isGrouped();
  var subnav = $("#subnav");
  var sortbar = $("#sortbar");
  if (subnav) {
    subnav.hidden = false;
    subnav.classList.remove("is-hidden");
  }
  if (sortbar) sortbar.hidden = false;
  // The List|Grid layout sub-toggle is only meaningful in grouped mode; hide the
  // whole group in decluttered so the chrome stays clean.
  var styleGroup = $("#grouped-style");
  if (styleGroup) styleGroup.hidden = !grouped;
  document.body.classList.toggle("grouped-view", grouped);
}

// Render the panel for the active media-type tab. In DECLUTTERED mode this is the
// single flat grid for (activeCategory, activeState). In GROUPED mode it is the
// on-disk folder hierarchy for activeCategory (all states), rendered by tree.js.
//
// Two transition strategies, by mode:
//   * DECLUTTERED is synchronous (renders from the already-loaded MODEL), so the
//     classic fade-out -> swap -> fade-in (`animate`) is smooth and instant.
//   * GROUPED is ASYNC (it awaits /api/tree, a heavy scandir on first use). The
//     old code faded the panel to empty and only rebuilt on resolve, which on the
//     first switch left a blank/faded panel then a jarring reload-flash. So
//     grouped now does an ATOMIC swap (paintTree): it keeps the current content
//     fully visible, builds the whole tree off-DOM, and replaces #panel's children
//     in ONE operation when ready (preserving scroll) — never clearing first.
function renderPanel(animate) {
  var panel = $("#panel");
  syncViewChrome();
  if (isGrouped()) {
    panel.setAttribute("aria-label", CATEGORY_META[activeCategory].label + " folders");
    panel.removeAttribute("aria-labelledby");
    // Atomic, flash-free swap owns its own transition; do NOT add `.swapping`
    // (which would fade the panel to empty while the async tree resolves). The
    // grouped tab has two presentation STYLES: the collapsible list (paintTree)
    // and the drill-down grid (paintGrid); both read the SAME cached /api/tree.
    if (groupedStyle === "grid") {
      paintGrid(panel, animate);
    } else {
      paintTree(panel, animate);
    }
    return;
  }

  panel.setAttribute("aria-labelledby", "seg-" + activeState);

  function paint() {
    // Dispose any active fetch-rings BEFORE emptying the panel so their
    // ResizeObservers are disconnected (teardown graft, change #4). This is the
    // flat grid-clear path — every flat tab/sub-view switch and post-fetch refresh
    // routes through here.
    paintFlat(panel);
    // Reveal (next frame so the transition runs).
    requestAnimationFrame(function () {
      panel.classList.remove("swapping");
    });
  }

  if (animate && canUseViewTransition()) {
    // Native View-Transitions morph: snapshot → swap → cross-fade, scoped to
    // #panel (see styles.css). paintFlat is the synchronous DOM update the API
    // snapshots around. NO `.swapping` here — that would double-animate the swap
    // (a fade-on-fade); the VT cross-fade fully owns the transition.
    withViewTransition(function () {
      paintFlat(panel);
    });
  } else if (animate) {
    // Fallback (API absent / reduced motion): the classic fade-out → swap →
    // fade-in via the `.swapping` opacity+slide transition.
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

  // "All" → every item of the category (all states); a specific state → just that
  // state's items. Sorted below by the persisted key+direction.
  var rows = (MODEL ? MODEL.items : []).filter(function (it) {
    if (it.category !== activeCategory) return false;
    return activeState === ALL_STATE || it.state === activeState;
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

// Grouped (folder-tree) view for the active category — ATOMIC, flash-free swap.
//
// The tree comes from the cached /api/tree (loadTree in data.js); leaf cards are
// joined back onto the enriched MODEL rows (MODEL_BY_ID) so their Copy command/
// folder survive. Because /api/tree is async (a heavy scandir on first use), we
// must NOT clear #panel to an empty/loading state while it resolves — that is the
// "reload flash" the old code produced. Instead:
//
//   1. Keep the CURRENT panel content fully visible (no fade-to-empty).
//   2. If the resolve is slow (first, uncached fetch), reveal a SUBTLE inline
//      loading overlay ON TOP of the existing content after a short grace delay
//      (so a fast cached switch never even shows it — it feels instant).
//   3. Build the ENTIRE tree off-DOM into a DocumentFragment (buildTreeFragment).
//   4. Swap it into #panel in ONE atomic operation (dispose outgoing rings, clear,
//      append the prebuilt fragment), preserving scrollTop across the swap.
//
// A stale resolution (the user switched tab/state filter before /api/tree
// returned) is dropped. A failure shows a status line and leaves the existing
// content in place (no destructive clear), so the user can retry.
//
// The atomic swap is intentionally instant (no fade) because there is no empty
// intermediate state to hide — the content simply changes in place.
function paintTree(panel, animate) {
  // Capture the category AND state filter this paint is for; if the user switches
  // tabs or the state filter before the tree resolves, a stale resolution must NOT
  // overwrite the newer view.
  var forCategory = activeCategory;
  var forState = activeState;

  // Subtle, non-destructive loading overlay shown only if the fetch is slow. It
  // self-suppresses if the user leaves grouped mode before the grace delay fires.
  var overlay = showTreeLoading(panel);

  treeRootsFor(forCategory)
    .then(function (roots) {
      hideTreeLoading(overlay);
      if (!isGrouped() || activeCategory !== forCategory || activeState !== forState) {
        return; // superseded by a tab / state-filter change
      }
      // "All" → the whole category tree (every leaf, all states). A specific
      // state → the tree PRUNED to that state: keep matching leaves, keep a folder
      // only if some descendant leaf matches, and show each kept folder's size as
      // the aggregate of its visible leaves (pruneTreeByState, pure + DOM-free).
      var view =
        forState === ALL_STATE
          ? roots
          : pruneTreeByState(roots, forState, MODEL_BY_ID);

      // Build the whole tree OFF-DOM, then swap atomically so the panel never
      // flashes empty. Preserve scroll position across the swap.
      var fragment = buildTreeFragment(view, MODEL_BY_ID);
      var prevScroll = panel.scrollTop;
      // Atomic swap — optionally inside a View Transition. We snapshot AFTER the
      // async tree resolved (never around the loading state) so the morph cross-
      // fades the real old content → real new content. Teardown invariant: dispose
      // the OUTGOING content's fetch-ring ResizeObservers right before replacing it
      // (buildTreeFragment built only new DOM and did not touch these).
      var swap = function () {
        destroyRingsIn(panel);
        panel.replaceChildren(fragment);
        panel.scrollTop = prevScroll;
      };
      if (animate) {
        withViewTransition(swap);
      } else {
        swap();
      }
    })
    .catch(function (err) {
      hideTreeLoading(overlay);
      if (!isGrouped() || activeCategory !== forCategory) return;
      // Non-destructive on failure: keep whatever is currently shown and surface
      // the error on the status line so the user can retry (re-toggle the tab).
      setStatus(
        "Failed to load the folder tree — " + ((err && err.message) || err),
        true
      );
    });
}

// Grouped GRID drill-down for the active category — same ATOMIC, flash-free swap
// discipline as paintTree (it shares the cached /api/tree + the subtle loading
// overlay + the stale-resolution guard), but renders ONE level (buildGridFragment)
// at the current gridPath instead of the whole collapsible tree. The active state
// filter prunes the tree FIRST (pruneTreeByState — the identical rule the list
// uses), so a folder box appears only if it has a matching descendant leaf and its
// size / count reflect the filter; the sort applies to each level via compareNodes
// inside buildGridFragment.
function paintGrid(panel, animate) {
  // Capture the category AND state filter this paint is for; a tab / state-filter /
  // view-style change before /api/tree resolves must NOT overwrite the newer view.
  var forCategory = activeCategory;
  var forState = activeState;

  // Decide the post-swap scroll NOW (synchronously): a drill / jump sets 0 via
  // gridPendingScrollTop so a new level opens at the top; an in-place re-render
  // (sort change / post-job refresh) preserves the live scrollTop. Consume the
  // pending value so the next paint preserves by default.
  var scrollTarget =
    gridPendingScrollTop != null ? gridPendingScrollTop : panel.scrollTop;
  gridPendingScrollTop = null;

  var overlay = showTreeLoading(panel);

  treeRootsFor(forCategory)
    .then(function (roots) {
      hideTreeLoading(overlay);
      if (
        !isGridStyle() ||
        activeCategory !== forCategory ||
        activeState !== forState
      ) {
        return; // superseded by a tab / state-filter / view-style change
      }
      // "All" → the whole category tree; a specific state → the tree PRUNED to it
      // (same rule + folder-size aggregation as the list view, see paintTree).
      var view =
        forState === ALL_STATE
          ? roots
          : pruneTreeByState(roots, forState, MODEL_BY_ID);

      var fragment = buildGridFragment(view, MODEL_BY_ID, gridPath, {
        rootLabel: CATEGORY_META[forCategory].label,
        onNavigate: navigateGrid,
      });
      // Atomic swap — optionally inside a View Transition (snapshot AFTER the data
      // is ready, never around the loading state). Teardown invariant: dispose the
      // OUTGOING content's fetch-ring observers immediately before the atomic
      // replace (buildGridFragment only built new DOM).
      var swap = function () {
        destroyRingsIn(panel);
        panel.replaceChildren(fragment);
        panel.scrollTop = scrollTarget;
      };
      if (animate) {
        withViewTransition(swap);
      } else {
        swap();
      }
    })
    .catch(function (err) {
      hideTreeLoading(overlay);
      if (!isGridStyle() || activeCategory !== forCategory) return;
      setStatus(
        "Failed to load the folder tree — " + ((err && err.message) || err),
        true
      );
    });
}

// --- Subtle tree-loading overlay (no content clear) ------------------------
// Reveals a small "Loading folders…" chip pinned over #panel ONLY if the async
// /api/tree is slow to resolve. A grace delay means a fast (cached) switch never
// shows it, so subsequent Grouped<->Decluttered toggles feel instant. The overlay
// sits ABOVE the existing content (which stays visible) rather than clearing it.
var TREE_LOADING_GRACE_MS = 180;

function showTreeLoading(panel) {
  var state = { el: null, timer: null, cancelled: false };
  state.timer = window.setTimeout(function () {
    state.timer = null;
    // Suppress if cancelled (resolve/reject arrived) OR the user has already left
    // grouped mode during the grace window — never inject the chip into a flat
    // panel that has since been repainted.
    if (state.cancelled || !isGrouped()) return;
    var el = document.createElement("div");
    el.className = "tree-loading";
    el.setAttribute("role", "status");
    el.setAttribute("aria-live", "polite");
    var chip = document.createElement("span");
    chip.className = "tree-loading-chip";
    chip.textContent = "Loading folders…";
    el.appendChild(chip);
    // Ensure the panel is a positioning context so the absolutely-positioned
    // overlay is scoped to it (styles.css also sets .panel{position:relative}).
    panel.appendChild(el);
    state.el = el;
  }, TREE_LOADING_GRACE_MS);
  return state;
}

function hideTreeLoading(state) {
  if (!state) return;
  state.cancelled = true;
  if (state.timer) {
    window.clearTimeout(state.timer);
    state.timer = null;
  }
  if (state.el && state.el.parentNode) {
    state.el.parentNode.removeChild(state.el);
  }
  state.el = null;
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
    // Default sub-view = "All" (firstNonEmptyState resolves to ALL_STATE for any
    // non-empty category; fall back to ALL_STATE for an empty library too).
    activeState = firstNonEmptyState(activeCategory) || ALL_STATE;
    resetGridNav(); // first paint starts the grid drill-down at the category root
  } else {
    // Re-render after a job. Keep the active sub-view if it still has items;
    // else drop to the first non-empty sub-view of the same category (= "All"
    // for any non-empty category); else the first category that has anything.
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
          activeState = firstNonEmptyState(nextCat) || ALL_STATE;
        } else {
          activeState = ALL_STATE;
        }
      }
      resetGridNav(); // the post-job fallback moved category/state; grid → root
    }
  }

  refreshTabSelection();
  buildSubnav();
  renderPanel(false);
  // Re-pick the hero's featured set for the (possibly changed) active category from
  // the freshly-loaded model. No-op when the category + featured set are unchanged.
  if (hero) hero.refresh();
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

// The two grouped-mode layout styles for the List|Grid sub-toggle. "list" keeps
// today's collapsible tree (default); "grid" is the drill-down folder boxes.
var GROUPED_STYLES = [
  { style: "list", label: "List", glyph: "☰", hint: "Collapsible folder list" },
  { style: "grid", label: "Grid", glyph: "⊞", hint: "Drill-down grid of folder boxes" },
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

  // Grouped-mode LAYOUT sub-toggle (List | Grid). Mounted in the SAME #viewbar
  // chrome but wrapped in #grouped-style so syncViewChrome can hide it wholesale in
  // decluttered mode. Mirrors the view-modes segmented-pill styling.
  var styleWrap = document.createElement("span");
  styleWrap.className = "grouped-style";
  styleWrap.id = "grouped-style";
  // Initial visibility matches the current mode so it never flashes during the
  // first (async) load; syncViewChrome keeps it in sync on every later repaint.
  styleWrap.hidden = !isGrouped();

  var styleLabel = document.createElement("span");
  styleLabel.className = "viewbar-label";
  styleLabel.textContent = "Layout";
  styleWrap.appendChild(styleLabel);

  var styleGroup = document.createElement("div");
  styleGroup.className = "view-modes";
  styleGroup.setAttribute("role", "group");
  styleGroup.setAttribute("aria-label", "Grouped layout style");

  GROUPED_STYLES.forEach(function (def) {
    var sb = document.createElement("button");
    sb.type = "button";
    sb.className = "view-mode grouped-style-btn";
    sb.dataset.style = def.style;
    sb.title = def.hint;

    var sg = document.createElement("span");
    sg.className = "view-mode-glyph";
    sg.setAttribute("aria-hidden", "true");
    sg.textContent = def.glyph;
    sb.appendChild(sg);

    var st = document.createElement("span");
    st.className = "view-mode-text";
    st.textContent = def.label;
    sb.appendChild(st);

    sb.addEventListener("click", function () {
      selectGroupedStyle(def.style);
    });
    styleGroup.appendChild(sb);
  });
  styleWrap.appendChild(styleGroup);
  bar.appendChild(styleWrap);

  refreshViewbar();
}

// Reflect the active mode + layout style (pressed state) on the toggles. The bar
// holds two segmented groups: the Grouped/Decluttered MODE buttons (data-mode) and
// the List/Grid LAYOUT buttons (data-style); each reflects against its own value.
function refreshViewbar() {
  var bar = $("#viewbar");
  if (!bar) return;
  var btns = bar.querySelectorAll(".view-mode");
  for (var i = 0; i < btns.length; i += 1) {
    var b = btns[i];
    var on = b.dataset.style
      ? b.dataset.style === groupedStyle
      : b.dataset.mode === viewMode;
    b.classList.toggle("active", on);
    b.setAttribute("aria-pressed", on ? "true" : "false");
  }
}

function selectViewMode(mode) {
  if (mode === viewMode) return;
  writeViewMode(mode);
  refreshViewbar();
  setStatus(""); // clear any stale tree-load error from a prior attempt
  renderPanel(true); // animated swap; flat from MODEL, tree from cached /api/tree
                     // (renderPanel→syncViewChrome reveals/hides the List|Grid toggle)
}

// Switch the grouped LAYOUT style (List <-> Grid). Persisted; the nav stack is
// intentionally kept so re-entering the grid resumes where you were. The grid
// renders from the SAME cached /api/tree, so this is a synchronous-feeling swap.
function selectGroupedStyle(style) {
  if (style === groupedStyle) return;
  writeGroupedStyle(style);
  refreshViewbar();
  setStatus(""); // clear any stale tree-load error from a prior attempt
  renderPanel(true); // animated swap between the list and grid presentations
}

// ---------------------------------------------------------------------------
// Command palette wiring (IMP-E16 D2)
// ---------------------------------------------------------------------------

// The public surface the ⌘K / Ctrl-K palette (palette.js) drives. Built here so
// palette.js stays a pure UI module (no import of app.js → no circular graph):
// it receives the live model + every navigation action through this object. Each
// callback routes to the SAME internal function a click on the chrome would use,
// so the palette can never drift from the visible controls.
function buildPaletteApi() {
  return {
    // Live candidate list — the merged model rows (all categories/states).
    getItems: function () {
      return MODEL ? MODEL.items : [];
    },
    // Title activation: switch tab + state, scroll, pulse, open the dossier.
    jumpToItem: jumpToItem,
    // Global actions (mirror the header chrome).
    selectCategory: function (cat) {
      selectCategory(cat);
    },
    selectState: function (state) {
      selectState(state);
    },
    setViewMode: function (mode) {
      selectViewMode(mode);
    },
    // Grid/List only apply in grouped mode — enter it first, then set the style.
    setGroupedStyle: function (style) {
      if (!isGrouped()) selectViewMode("grouped");
      selectGroupedStyle(style);
    },
    setSortKey: function (key) {
      setSort(key, null);
      refreshSortbar();
      renderPanel(false); // instant client-side reorder, no view swap
    },
  };
}

// ---------------------------------------------------------------------------
// Lazy command-palette loader (IMP-E16 D5)
// ---------------------------------------------------------------------------
//
// palette.js (the overlay + fuzzy index + its OWN ⌘K/"/" keydown wiring) is only
// needed once the user reaches for it, so it is kept OUT of the first-paint module
// graph and dynamically imported on the first trigger. A LIGHT keydown shim + a
// lightweight header "Search" button live here to catch that first trigger; both
// route through ensurePalette(), which imports + wires the module exactly once
// (guarded by _paletteReady so subsequent triggers reuse the loaded copy).
//
// Hand-off: wireCommandPalette() registers palette.js's OWN keydown listener (the
// original behaviour), so this shim only OWNS the FIRST trigger — it bails the
// moment _paletteReady is set, letting palette.js drive every later ⌘K/"/" exactly
// as before. The opened overlay + index are byte-identical to the old eager path;
// only the load moment moved.
var _paletteReady = false; // wireCommandPalette() has run
var _paletteMod = null; // the loaded palette.js module namespace
var _paletteLoading = null; // in-flight import() promise (dedupes rapid triggers)

function ensurePalette() {
  if (_paletteReady) return Promise.resolve(_paletteMod);
  if (_paletteLoading) return _paletteLoading;
  _paletteLoading = import("./palette.js")
    .then(function (m) {
      if (!_paletteReady) {
        m.wireCommandPalette(buildPaletteApi());
        _paletteMod = m;
        _paletteReady = true;
      }
      return _paletteMod;
    })
    .catch(function (err) {
      _paletteLoading = null; // allow a retry on the next trigger
      console.warn("Command palette (palette.js) failed to load:", err);
      return null;
    });
  return _paletteLoading;
}

// Load (if needed) then open the palette. open() is idempotent (it no-ops when
// already open), so a double call during the brief import window is harmless.
function openPaletteLazy() {
  ensurePalette().then(function (m) {
    if (m && typeof m.open === "function") m.open();
  });
}

// A bare keystroke is text input in these targets, so the global "/" shortcut must
// not steal it (mirrors palette.js's own isTypingTarget).
function isTypingTarget(el) {
  if (!el) return false;
  var tag = el.tagName;
  if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") return true;
  if (el.isContentEditable) return true;
  return false;
}

function isMacPlatform() {
  try {
    var s = navigator.platform || navigator.userAgent || "";
    return /Mac|iPhone|iPod|iPad/i.test(s);
  } catch (e) {
    return false;
  }
}

// Mount the lightweight header "Search" affordance EAGERLY so it exists from first
// paint — it is the mobile tap target (no keyboard shortcut there) and the desktop
// discoverability hint. palette.js can't inject its own until it loads, and on
// mobile there is no shortcut to load it with, so this must be present up front.
// Mirrors palette.js's injectTrigger markup (#cmdk-trigger) so styles.css applies
// and, once palette.js loads, its injectTrigger() guard skips re-mounting.
function mountPaletteTrigger() {
  var row = document.querySelector(".tabbar-row");
  if (!row || document.getElementById("cmdk-trigger")) return;

  var hint = isMacPlatform() ? "⌘K" : "Ctrl K";

  var btn = document.createElement("button");
  btn.type = "button";
  btn.id = "cmdk-trigger";
  btn.className = "cmdk-trigger";
  btn.title = "Search & commands (" + hint + ")";
  btn.setAttribute("aria-label", "Open command palette");

  var g = document.createElement("span");
  g.className = "cmdk-trigger-glyph";
  g.setAttribute("aria-hidden", "true");
  g.textContent = "⌕";
  btn.appendChild(g);

  var t = document.createElement("span");
  t.className = "cmdk-trigger-text";
  t.textContent = "Search";
  btn.appendChild(t);

  var kbd = document.createElement("span");
  kbd.className = "cmdk-trigger-kbd";
  kbd.setAttribute("aria-hidden", "true");
  kbd.textContent = hint;
  btn.appendChild(kbd);

  btn.addEventListener("click", openPaletteLazy);
  row.appendChild(btn);
}

// Wire the light first-trigger shim: the eager Search button + a ⌘K/Ctrl-K/"/"
// keydown listener that bails the instant palette.js has taken over.
function wirePaletteLazy() {
  mountPaletteTrigger();
  document.addEventListener("keydown", function (e) {
    if (_paletteReady) return; // palette.js owns the shortcut once it has loaded
    var k = e.key;
    var combo = (e.metaKey || e.ctrlKey) && (k === "k" || k === "K");
    var slash = k === "/" && !isTypingTarget(e.target);
    if (!combo && !slash) return;
    e.preventDefault();
    openPaletteLazy();
  });
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
  authFetch("/api/mode")
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

// Owner-only "Access" panel (IMP-E15), now LAZY-LOADED (IMP-E16 D5). The tiny
// no-auth /api/whoami probe STAYS here so we can decide BEFORE fetching any JS:
// admin.js (the ~25KB mint/list/revoke console) is dynamically imported ONLY when
// the probe reports the genuine local owner (is_admin). A remote/token device never
// downloads admin.js at all. A failed probe or import is swallowed → no admin
// surface (fail-safe, identical to the old eager behaviour), and the device 401
// token flow in auth.js is untouched. initAdmin() re-checks whoami itself; that
// second tiny no-auth fetch (owner-only, off the first-paint path) is the deliberate
// price of leaving admin.js byte-for-byte unchanged.
function initAdminLazy() {
  authFetch("/api/whoami")
    .then(function (res) {
      return res && res.ok ? res.json() : null;
    })
    .then(function (data) {
      if (data && data.is_admin === true) {
        import("./admin.js")
          .then(function (m) {
            m.initAdmin();
          })
          .catch(function (err) {
            console.warn("Access panel (admin.js) failed to load:", err);
          });
      }
    })
    .catch(function () {
      /* not the owner / probe failed — render no admin surface (fail-safe). */
    });
}

function init() {
  // Capture/restore the access token (and set the cookie that lets <img> requests
  // carry it) BEFORE any /api/ fetch. Idempotent — auth.js also runs this at
  // module load; this explicit call documents the dependency and guards against
  // any future re-ordering of the module graph.
  bootstrapToken();
  checkDemoMode();
  // Owner-only "Access" panel (IMP-E15) — LAZY (IMP-E16 D5). Probes /api/whoami and,
  // ONLY for the genuine local owner, dynamically imports admin.js + mounts the token
  // mint/list/revoke console. A remote/token device never fetches admin.js and keeps
  // the existing 401 token-prompt flow.
  initAdminLazy();
  wireModal();
  wireSort();
  buildSortbar();
  buildViewbar();
  // Cursor-following glow on the cards. Delegated to the stable #panel (created
  // once in index.html; only its children are cleared on re-render), so every
  // freshly-rendered card is covered with no per-card listener to leak.
  wireCardGlow($("#panel"));
  // Cinematic hover detail-window (IMP-E16): resting on any card opens a large
  // translucent "dossier" (backdrop + synopsis + meta). Delegated to the SAME
  // stable #panel as the glow, desktop-pointer only, pointer-events:none (never
  // blocks a click). Covers flat + grouped leaf cards with no per-card listener.
  wireHoverPreview($("#panel"));
  // ⌘K / Ctrl-K command palette (IMP-E16 D2) — LAZY (IMP-E16 D5). A light keydown
  // shim + an eager lightweight Search button live in app.js; palette.js itself
  // (overlay + fuzzy index, driven by buildPaletteApi()) is dynamically imported on
  // the first ⌘K / "/" / Search-button use, then takes over its own shortcut.
  wirePaletteLazy();
  // Cinematic parallax hero strip (IMP-E16 D4): a wide backdrop band over #panel
  // featuring the active tab's archived/backdrop titles with a Ken-Burns drift,
  // scroll parallax, and crossfading auto-rotation. Built once; refresh() re-picks
  // per category (called from renderAll / selectCategory / jumpToItem). Clicking a
  // slide reuses the palette's jump (scroll the card in + pulse + open its dossier).
  hero = wireHero(
    $("#hero"),
    function () {
      return MODEL;
    },
    function () {
      return activeCategory;
    },
    jumpToItem
  );
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
