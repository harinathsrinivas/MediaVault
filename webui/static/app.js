/* MediaVault Console — Candidate A: media-type-first console (IMP-E14 Phase 1).
 *
 * Layout: a top TAB BAR (Movies / TV series / Anime / Others). Selecting a tab
 * shows ONLY that category's items, grouped into the five disk-state sub-views,
 * each rendered as a LABELLED COLLAPSIBLE SECTION (accordion) stacked in one
 * scrolling column. Denser, fewer moving parts; everything for a category is
 * visible by scrolling.
 *
 * No framework, no build step, no CDNs/external fonts. Split into ES modules
 * loaded via <script type="module">; each file passes `node --check`.
 *   - data.js : pure merge/categorize/group of /api/items + /api/reclaim.
 *   - card.js : card rendering + action/job flow + confirm modal (preserved).
 *   - app.js  : this orchestrator — tabs, accordions, load, refresh.
 *
 * Accessibility: tab bar is role="tablist" with role="tab"/aria-selected and
 * LEFT/RIGHT (and Home/End) arrow-key navigation; one tab active at a time; the
 * panel is role="tabpanel". Accordion headers are <button> with aria-expanded
 * controlling an aria-labelledby region.
 */

"use strict";

import {
  STATE_ORDER,
  STATE_TITLE,
  CATEGORY_ORDER,
  CATEGORY_TITLE,
  buildRenderList,
  groupByCategoryState,
  categoryCounts,
} from "./data.js";

import { STATE_META, buildCard, wireModal, runAction, setRefreshHook } from "./card.js";

function $(sel, root) {
  return (root || document).querySelector(sel);
}

// After a job reaches a terminal state we re-fetch both endpoints so states
// reflect the new reality. We wait this long first so the just-shown job result
// stays visible briefly instead of being yanked away the instant the job ends.
var REFRESH_AFTER_JOB_MS = 2500;

// --------------------------------------------------------------------------
// View state (session-scoped, in-memory)
// --------------------------------------------------------------------------

var state = {
  groups: null,          // {category: {state: [records]}}
  counts: { movies: 0, series: 0, anime: 0, other: 0 },
  activeCategory: "movies",
  // Per-(category,state) accordion open flag. Key "category|state". When a key is
  // absent we fall back to the default-open rule (non-empty sections open).
  open: Object.create(null),
};

function openKey(category, st) {
  return category + "|" + st;
}

// Whether a section should render expanded. Explicit user toggles win; otherwise
// non-empty sections default open, empty ones default collapsed.
function isOpen(category, st, count) {
  var k = openKey(category, st);
  if (k in state.open) return state.open[k];
  return count > 0;
}

// --------------------------------------------------------------------------
// Tab bar
// --------------------------------------------------------------------------

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
    label.textContent = CATEGORY_TITLE[cat];
    tab.appendChild(label);

    var count = document.createElement("span");
    count.className = "tab-count";
    count.textContent = String(state.counts[cat] || 0);
    tab.appendChild(count);

    tab.addEventListener("click", function () {
      selectCategory(cat, true);
    });
    bar.appendChild(tab);
  });
  // Keyboard: roving tabindex + Left/Right/Home/End on the tablist.
  bar.addEventListener("keydown", onTablistKeydown);
  syncTabSelection();
}

function tabEls() {
  return Array.prototype.slice.call(document.querySelectorAll("#tabbar .tab"));
}

// Reflect activeCategory onto aria-selected + roving tabindex (only the active
// tab is tabbable; arrows move among the rest).
function syncTabSelection() {
  tabEls().forEach(function (tab) {
    var active = tab.dataset.cat === state.activeCategory;
    tab.setAttribute("aria-selected", active ? "true" : "false");
    tab.tabIndex = active ? 0 : -1;
    tab.classList.toggle("active", active);
  });
}

function onTablistKeydown(e) {
  var tabs = tabEls();
  if (!tabs.length) return;
  var idx = tabs.findIndex(function (t) {
    return t.dataset.cat === state.activeCategory;
  });
  if (idx < 0) idx = 0;
  var next = null;
  if (e.key === "ArrowRight" || e.key === "ArrowDown") {
    next = (idx + 1) % tabs.length;
  } else if (e.key === "ArrowLeft" || e.key === "ArrowUp") {
    next = (idx - 1 + tabs.length) % tabs.length;
  } else if (e.key === "Home") {
    next = 0;
  } else if (e.key === "End") {
    next = tabs.length - 1;
  } else {
    return;
  }
  e.preventDefault();
  var cat = tabs[next].dataset.cat;
  selectCategory(cat, false);
  tabs[next].focus();
}

function selectCategory(cat, fromClick) {
  if (state.activeCategory === cat && fromClick) {
    // Re-clicking the active tab is a no-op (keeps accordion state).
    return;
  }
  state.activeCategory = cat;
  syncTabSelection();
  renderPanel();
}

// --------------------------------------------------------------------------
// Panel: stacked accordion sub-view sections for the active category
// --------------------------------------------------------------------------

function renderPanel() {
  var panel = $("#panel");
  panel.textContent = "";
  panel.setAttribute("aria-labelledby", "tab-" + state.activeCategory);

  var cat = state.activeCategory;
  var byState = (state.groups && state.groups[cat]) || {};

  var totalInCat = state.counts[cat] || 0;
  if (totalInCat === 0) {
    panel.appendChild(emptyCategoryNotice());
    return;
  }

  var frag = document.createDocumentFragment();
  STATE_ORDER.forEach(function (st) {
    var records = byState[st] || [];
    frag.appendChild(buildAccordionSection(cat, st, records));
  });
  panel.appendChild(frag);
}

function emptyCategoryNotice() {
  var el = document.createElement("div");
  el.className = "empty-state";
  var big = document.createElement("div");
  big.className = "big";
  big.textContent = "✓";
  var p = document.createElement("div");
  p.textContent = "No items in this category.";
  el.appendChild(big);
  el.appendChild(p);
  return el;
}

function buildAccordionSection(category, st, records) {
  var m = STATE_META[st] || { cssKey: "unprepped" };
  var count = records.length;
  var open = isOpen(category, st, count);

  var section = document.createElement("section");
  section.className = "accordion s-" + m.cssKey;
  if (count === 0) section.classList.add("is-empty");

  var headId = "acc-h-" + category + "-" + st;
  var bodyId = "acc-b-" + category + "-" + st;

  // Header is a <button> so it is natively focusable / Enter+Space activatable.
  var header = document.createElement("button");
  header.type = "button";
  header.className = "accordion-head";
  header.id = headId;
  header.setAttribute("aria-expanded", open ? "true" : "false");
  header.setAttribute("aria-controls", bodyId);

  var caret = document.createElement("span");
  caret.className = "acc-caret";
  caret.setAttribute("aria-hidden", "true");
  caret.textContent = "▸";
  header.appendChild(caret);

  var dot = document.createElement("span");
  dot.className = "acc-dot";
  header.appendChild(dot);

  var title = document.createElement("span");
  title.className = "acc-title";
  title.textContent = STATE_TITLE[st] || st;
  header.appendChild(title);

  var ct = document.createElement("span");
  ct.className = "acc-count";
  ct.textContent = String(count);
  header.appendChild(ct);

  header.addEventListener("click", function () {
    toggleSection(category, st, section, header, body);
  });

  // Body region: a height/opacity transition wrapper around the grid.
  var body = document.createElement("div");
  body.className = "accordion-body";
  body.id = bodyId;
  body.setAttribute("role", "region");
  body.setAttribute("aria-labelledby", headId);

  var inner = document.createElement("div");
  inner.className = "accordion-inner";

  if (count === 0) {
    var none = document.createElement("div");
    none.className = "sub-empty";
    none.textContent = "No items";
    inner.appendChild(none);
  } else {
    var grid = document.createElement("div");
    grid.className = "grid";
    var gridFrag = document.createDocumentFragment();
    records.forEach(function (rec) {
      gridFrag.appendChild(buildCard(rec));
    });
    grid.appendChild(gridFrag);
    inner.appendChild(grid);
  }
  body.appendChild(inner);

  section.appendChild(header);
  section.appendChild(body);

  // Apply initial expand/collapse without animating the first paint.
  setSectionState(section, header, body, open, false);

  return section;
}

function toggleSection(category, st, section, header, body) {
  var willOpen = header.getAttribute("aria-expanded") !== "true";
  state.open[openKey(category, st)] = willOpen;
  setSectionState(section, header, body, willOpen, true);
}

// Drive the open/closed visual + a11y state. When `animate` is false we set the
// final state immediately (initial paint). When true we animate max-height from
// the measured content height, then release to `none` so later content changes
// don't get clipped.
function setSectionState(section, header, body, open, animate) {
  header.setAttribute("aria-expanded", open ? "true" : "false");
  section.classList.toggle("open", open);

  if (!animate) {
    body.style.maxHeight = open ? "none" : "0px";
    body.style.opacity = open ? "1" : "0";
    return;
  }

  if (open) {
    body.style.maxHeight = body.scrollHeight + "px";
    body.style.opacity = "1";
    var release = function () {
      // Only release if still open (user may have re-collapsed mid-transition).
      if (section.classList.contains("open")) body.style.maxHeight = "none";
      body.removeEventListener("transitionend", release);
    };
    body.addEventListener("transitionend", release);
  } else {
    // From "none" we must first pin to the current pixel height so the
    // transition has a start value, then collapse on the next frame.
    body.style.maxHeight = body.scrollHeight + "px";
    body.style.opacity = "1";
    requestAnimationFrame(function () {
      body.style.maxHeight = "0px";
      body.style.opacity = "0";
    });
  }
}

// --------------------------------------------------------------------------
// Global Sort action
// --------------------------------------------------------------------------

function wireSort() {
  var btn = $("#btn-sort");
  var panel = $("#sort-job-panel");
  btn.addEventListener("click", function () {
    runAction("sort", {}, btn, panel);
  });
}

// --------------------------------------------------------------------------
// Load + render
// --------------------------------------------------------------------------

function setStatus(msg, isErr) {
  var el = $("#status-line");
  el.textContent = msg || "";
  el.classList.toggle("err", !!isErr);
}

function fetchJson(url) {
  return fetch(url).then(function (res) {
    if (!res.ok) throw new Error(url + " — HTTP " + res.status);
    return res.json();
  });
}

function applyData(itemsPayload, reclaimPayload) {
  var records = buildRenderList(itemsPayload, reclaimPayload);
  state.groups = groupByCategoryState(records);
  state.counts = categoryCounts(records);

  // Refresh tab count badges in place (tabs already built).
  tabEls().forEach(function (tab) {
    var c = $(".tab-count", tab);
    if (c) c.textContent = String(state.counts[tab.dataset.cat] || 0);
  });

  renderPanel();

  var total = records.length;
  setStatus(
    total === 0
      ? "Library is empty."
      : total + (total === 1 ? " item across all categories." : " items across all categories.")
  );
}

function load() {
  setStatus("Loading library…");
  Promise.all([fetchJson("/api/items"), fetchJson("/api/reclaim")])
    .then(function (res) {
      applyData(res[0], res[1]);
    })
    .catch(function (err) {
      setStatus("Failed to load — " + ((err && err.message) || err), true);
    });
}

// Debounced post-job refresh — coalesces several jobs finishing close together.
var _refreshTimer = null;
function scheduleRefresh() {
  if (_refreshTimer) clearTimeout(_refreshTimer);
  _refreshTimer = setTimeout(function () {
    _refreshTimer = null;
    load();
  }, REFRESH_AFTER_JOB_MS);
}

function init() {
  wireModal();
  wireSort();
  setRefreshHook(scheduleRefresh);
  buildTabs();
  load();
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", init);
} else {
  init();
}
