/* MediaVault Console — ⌘K / Ctrl-K command palette (IMP-E16 D2).
 *
 * A power-user launcher: fuzzy-jump to any title in the library and run the
 * global view / sort / state actions, all from the keyboard. ES module;
 * `node --check palette.js` covers it on its own (no build step — hard constraint).
 *
 * DECOUPLED BY DESIGN
 *   This module never imports app.js — it would close a circular graph (app.js
 *   imports THIS). Instead app.js hands it a small `api` object via
 *   wireCommandPalette(api): the live model (getItems) plus every navigation
 *   action (jumpToItem / selectCategory / selectState / setViewMode /
 *   setGroupedStyle / setSortKey). Each api callback routes to the SAME internal
 *   function the visible header chrome uses, so the palette can never drift from
 *   the on-screen controls. For labels it imports only the leaf data modules
 *   (title.js displayTitle, data.js metadata) — no DOM coupling at import time.
 *
 * OPEN / CLOSE
 *   ⌘K (mac) / Ctrl-K (win) toggles; "/" opens when you're NOT already typing in
 *   a field. Escape, a backdrop click, or activating a row closes it. While open,
 *   focus is trapped in the input (Tab is swallowed) and restored to the prior
 *   element on close. A small "Search" affordance is injected into the header (it
 *   shows the shortcut on desktop and doubles as the tap target on mobile).
 *
 * FUZZY MATCH
 *   A tiny, dependency-free subsequence matcher (fuzzyMatch) scores case-
 *   insensitively with contiguity / prefix / word-boundary bonuses. Titles match
 *   on BOTH the displayed label and the raw id (separators normalised to spaces,
 *   so "dark s01e01" finds tv-en-2017-dark-s01e01). Results are ranked by score,
 *   capped at MAX_RESULTS, and the matched characters are emphasised in the label.
 *
 * XSS-safe: every text node is set via textContent; the emphasised label is built
 * from DOM <mark> spans (never innerHTML). The only interpolated values are the
 * library's own titles / ids, and even those only ever become text nodes.
 *
 * Stable hooks for inspection / the verify harness: overlay `#cmd-palette`, input
 * `.cmdk-input`, result rows `.cmdk-item`, open class `.is-open`.
 */

"use strict";

import { displayTitle } from "./title.js";
import {
  metaFor,
  CATEGORY_META,
  CATEGORY_ORDER,
  STATE_ORDER,
  ALL_STATE,
} from "./data.js";

// Cap rendered rows (actions + titles) so a broad query never floods the list.
var MAX_RESULTS = 30;

// ---------------------------------------------------------------------------
// Module state
// ---------------------------------------------------------------------------
var _api = null; // injected by wireCommandPalette()
var _els = null; // cached overlay DOM (built once)
var _open = false;
var _index = []; // candidate title index, rebuilt from the live model on each open
var _actions = []; // the global action commands (built once on wire)
var _results = []; // the row descriptors currently rendered
var _selected = -1; // index into _results of the highlighted row
var _lastFocus = null; // element to restore focus to on close

// ---------------------------------------------------------------------------
// Fuzzy subsequence matcher
// ---------------------------------------------------------------------------
//
// Greedy left-to-right subsequence match (every query char must appear in order).
// Returns { score, positions } where positions index into `text`, or null when
// `q` is not a subsequence of `text`. Both inputs are expected pre-lowercased.
// Scoring rewards contiguity, a match at the very start, and matches that begin a
// word (after a separator), then mildly prefers an earlier first hit + a shorter
// haystack — enough to rank "Dark" above "Darkest Hour" for the query "dark".

function isBoundary(code) {
  return (
    code === 32 || // space
    code === 45 || // -
    code === 95 || // _
    code === 46 || // .
    code === 47 || // /
    code === 58 || // :
    code === 40 || // (
    code === 41 || // )
    code === 0x2014 || // — em dash (e.g. "Dark — S01E01")
    code === 0xb7 // · middle dot
  );
}

function fuzzyMatch(q, text) {
  var ql = q.length;
  if (ql === 0) return { score: 0, positions: [] };
  var tl = text.length;
  if (ql > tl) return null;

  var positions = [];
  var score = 0;
  var qi = 0;
  var prev = -2; // index of the previous match (for the contiguity bonus)
  var first = -1;

  for (var ti = 0; ti < tl && qi < ql; ti += 1) {
    if (text.charCodeAt(ti) === q.charCodeAt(qi)) {
      positions.push(ti);
      if (first < 0) first = ti;
      var bonus = 1;
      if (ti === prev + 1) bonus += 4; // contiguous run
      if (ti === 0) bonus += 6; // very start of the haystack
      else if (isBoundary(text.charCodeAt(ti - 1))) bonus += 5; // word boundary
      score += bonus;
      prev = ti;
      qi += 1;
    }
  }

  if (qi < ql) return null; // ran out of haystack before matching all of `q`
  if (first === 0) score += 4; // prefix kicker
  score -= first * 0.4; // an earlier first hit reads as more relevant
  score -= (tl - ql) * 0.04; // mild brevity preference among equal matches
  return { score: score, positions: positions };
}

// ---------------------------------------------------------------------------
// Candidate index (titles) + action commands
// ---------------------------------------------------------------------------

// One typeglyph per media category. The secondary line also names the category,
// so the glyph is a quick scanning aid, not the sole signal.
function categoryGlyph(cat) {
  if (cat === "movies") return "🎬";
  if (cat === "series") return "📺";
  if (cat === "anime") return "🌀";
  return "🗂";
}

// Build the searchable title index from the live model. Cheap (a map over the
// rows) and rebuilt on every open so it always reflects the latest /api refresh.
function buildIndex() {
  var items = (_api && _api.getItems && _api.getItems()) || [];
  _index = [];
  for (var i = 0; i < items.length; i += 1) {
    var item = items[i];
    if (!item || item.id == null) continue;
    var label = displayTitle(item);
    // Normalise the raw id's separators to spaces so an id-shaped query with
    // spaces ("dark s01e01") still subsequence-matches tv-en-2017-dark-s01e01.
    var idNorm = String(item.id).replace(/[-_.]+/g, " ");
    _index.push({
      kind: "item",
      item: item,
      label: label,
      labelLower: label.toLowerCase(),
      idNormLower: idNorm.toLowerCase(),
    });
  }
}

// The curated global actions. `common` ones lead the empty-query "Actions"
// section; every action is searchable by name once you start typing. Each `run`
// routes through the injected api → the same path the header chrome uses.
function buildActions() {
  var list = [];

  function push(label, hint, glyph, run, common, cssKey) {
    list.push({
      kind: "action",
      label: label,
      labelLower: label.toLowerCase(),
      hint: hint,
      glyph: glyph,
      run: run,
      common: !!common,
      cssKey: cssKey || null,
    });
  }

  // Media-type tabs.
  CATEGORY_ORDER.forEach(function (cat) {
    var name = (CATEGORY_META[cat] && CATEGORY_META[cat].label) || cat;
    push(
      "Switch to " + name,
      "Media type",
      categoryGlyph(cat),
      function () {
        _api.selectCategory(cat);
      },
      true
    );
  });

  // View mode.
  push("Grouped view", "On-disk folder hierarchy", "▤", function () {
    _api.setViewMode("grouped");
  }, true);
  push("Decluttered view", "Flat, grouped by disk state", "▦", function () {
    _api.setViewMode("decluttered");
  }, true);

  // Grouped-mode layout (enters grouped mode first — see app.js setGroupedStyle).
  push("Grid layout", "Drill-down grid of folder boxes", "⊞", function () {
    _api.setGroupedStyle("grid");
  });
  push("List layout", "Collapsible folder list", "☰", function () {
    _api.setGroupedStyle("list");
  });

  // Sort key (client-side reorder of the current grid).
  push("Sort by Size", "Largest first", "⇅", function () {
    _api.setSortKey("size");
  }, true);
  push("Sort by Title", "Alphabetical", "⇅", function () {
    _api.setSortKey("title");
  });
  push("Sort by Year", "By release year", "⇅", function () {
    _api.setSortKey("year");
  });

  // Disk-state sub-view filters (within the active tab). The state dot is tinted
  // by the per-state palette via `cssKey`.
  [ALL_STATE].concat(STATE_ORDER).forEach(function (st) {
    var m = metaFor(st);
    push(
      "Show " + m.short,
      "Disk-state filter",
      "●",
      function () {
        _api.selectState(st);
      },
      st === ALL_STATE || st === "ARCHIVED",
      m.cssKey
    );
  });

  return list;
}

// ---------------------------------------------------------------------------
// Scoring + result assembly
// ---------------------------------------------------------------------------

// Compute the ordered row list for `q` (already lowercased + trimmed). Empty →
// the curated "common" actions. Non-empty → actions + titles scored together,
// ranked by score, capped at MAX_RESULTS.
function computeResults(q) {
  if (q === "") {
    var out = [];
    for (var a = 0; a < _actions.length; a += 1) {
      if (_actions[a].common) {
        out.push({ kind: "action", action: _actions[a], positions: [] });
      }
    }
    return out;
  }

  var scored = [];

  for (var i = 0; i < _actions.length; i += 1) {
    var am = fuzzyMatch(q, _actions[i].labelLower);
    if (am) {
      scored.push({
        kind: "action",
        action: _actions[i],
        // A small affinity so a clearly-named command (e.g. "grouped") surfaces
        // at the top when its name matches, mixed in among the titles.
        score: am.score + 2,
        positions: am.positions,
      });
    }
  }

  for (var j = 0; j < _index.length; j += 1) {
    var e = _index[j];
    var lm = fuzzyMatch(q, e.labelLower);
    var im = e.idNormLower ? fuzzyMatch(q, e.idNormLower) : null;
    if (!lm && !im) continue;
    var labelScore = lm ? lm.score : -Infinity;
    // The id is the hidden handle, so an id-only hit ranks a notch below a label
    // hit and carries no label-highlight positions.
    var idScore = im ? im.score - 3 : -Infinity;
    var best, positions;
    if (labelScore >= idScore) {
      best = labelScore;
      positions = lm ? lm.positions : [];
    } else {
      best = idScore;
      positions = [];
    }
    scored.push({ kind: "item", entry: e, score: best, positions: positions });
  }

  scored.sort(function (x, y) {
    if (y.score !== x.score) return y.score - x.score;
    // Stable, readable tiebreak: actions before titles, then alphabetical.
    if (x.kind !== y.kind) return x.kind === "action" ? -1 : 1;
    var xn = x.kind === "action" ? x.action.labelLower : x.entry.labelLower;
    var yn = y.kind === "action" ? y.action.labelLower : y.entry.labelLower;
    return xn < yn ? -1 : xn > yn ? 1 : 0;
  });

  return scored.slice(0, MAX_RESULTS);
}

// ---------------------------------------------------------------------------
// Rendering
// ---------------------------------------------------------------------------

// Read the input, recompute, and repaint the rows. Selection resets to the top
// match so Enter activates the best result immediately.
function renderResults() {
  if (!_els) return;
  var raw = _els.input.value.trim();
  var q = raw.toLowerCase();
  _results = computeResults(q);

  var list = _els.results;
  list.textContent = "";

  if (_results.length === 0) {
    var empty = document.createElement("div");
    empty.className = "cmdk-empty";
    empty.textContent = raw
      ? "No matches for “" + raw + "”"
      : "Type to search titles and commands…";
    list.appendChild(empty);
    _selected = -1;
    updateActiveDescendant();
    return;
  }

  if (q === "") {
    var head = document.createElement("div");
    head.className = "cmdk-section";
    head.setAttribute("aria-hidden", "true");
    head.textContent = "Actions";
    list.appendChild(head);
  }

  var frag = document.createDocumentFragment();
  for (var i = 0; i < _results.length; i += 1) {
    frag.appendChild(buildRow(_results[i], i));
  }
  list.appendChild(frag);

  // Pre-select the first row (no scroll — we're already at the top).
  setSelected(0, false);
}

function buildRow(row, index) {
  var el = document.createElement("div");
  el.className = "cmdk-item" + (row.kind === "action" ? " cmdk-action" : "");
  el.id = "cmdk-opt-" + index;
  el.dataset.index = String(index);
  el.setAttribute("role", "option");
  el.setAttribute("aria-selected", "false");

  // Glyph tile.
  var glyph = document.createElement("span");
  glyph.className = "cmdk-glyph";
  if (row.kind === "action") {
    glyph.textContent = row.action.glyph || "•";
    if (row.action.cssKey) glyph.classList.add("s-" + row.action.cssKey);
  } else {
    glyph.textContent = categoryGlyph(row.entry.item.category);
  }
  el.appendChild(glyph);

  // Text block: title (emphasised for items) + a dim secondary line.
  var text = document.createElement("div");
  text.className = "cmdk-text";

  var title = document.createElement("div");
  title.className = "cmdk-title";
  if (row.kind === "action") {
    title.textContent = row.action.label;
  } else {
    emphasizeInto(title, row.entry.label, row.positions);
  }
  text.appendChild(title);

  var sub = document.createElement("div");
  sub.className = "cmdk-sub";
  if (row.kind === "action") {
    sub.textContent = row.action.hint || "Action";
  } else {
    buildItemSub(sub, row.entry.item);
  }
  text.appendChild(sub);
  el.appendChild(text);

  // Trailing ↵ affordance (revealed on the active row via CSS).
  var tail = document.createElement("span");
  tail.className = "cmdk-tail";
  tail.setAttribute("aria-hidden", "true");
  tail.textContent = "↵";
  el.appendChild(tail);

  // Mouse: hover highlights, click activates. mousedown is swallowed so the
  // click never blurs the input (keeps the focus trap + typing intact).
  el.addEventListener("mousemove", function () {
    setSelected(index, false);
  });
  el.addEventListener("mousedown", function (e) {
    e.preventDefault();
  });
  el.addEventListener("click", function () {
    activate(index);
  });

  return el;
}

// The dim "state chip · year · category" secondary line for a title row.
function buildItemSub(sub, item) {
  var m = metaFor(item.state);

  var chip = document.createElement("span");
  chip.className = "cmdk-state s-" + m.cssKey;
  var dot = document.createElement("span");
  dot.className = "cmdk-state-dot";
  chip.appendChild(dot);
  var lab = document.createElement("span");
  lab.textContent = m.short;
  chip.appendChild(lab);
  sub.appendChild(chip);

  var bits = [];
  if (item.year) bits.push(String(item.year));
  var catLabel =
    (CATEGORY_META[item.category] && CATEGORY_META[item.category].label) ||
    item.category;
  if (catLabel) bits.push(catLabel);
  if (bits.length) {
    var rest = document.createElement("span");
    rest.className = "cmdk-sub-rest";
    rest.textContent = " · " + bits.join(" · ");
    sub.appendChild(rest);
  }
}

// Emphasise the matched characters of `label` by wrapping contiguous matched
// runs in <mark>. Batches runs so a long match is a few nodes, not one per char.
// XSS-safe: text only ever enters via textContent / createTextNode.
function emphasizeInto(container, label, positions) {
  if (!positions || positions.length === 0) {
    container.textContent = label;
    return;
  }
  var matched = {};
  for (var p = 0; p < positions.length; p += 1) matched[positions[p]] = true;

  var run = "";
  var runMatched = false;

  function flush() {
    if (run === "") return;
    if (runMatched) {
      var mk = document.createElement("mark");
      mk.className = "cmdk-hl";
      mk.textContent = run;
      container.appendChild(mk);
    } else {
      container.appendChild(document.createTextNode(run));
    }
    run = "";
  }

  for (var c = 0; c < label.length; c += 1) {
    var isM = !!matched[c];
    if (isM !== runMatched) {
      flush();
      runMatched = isM;
    }
    run += label.charAt(c);
  }
  flush();
}

// ---------------------------------------------------------------------------
// Selection
// ---------------------------------------------------------------------------

function setSelected(index, scroll) {
  _selected = index;
  if (!_els) return;
  var rows = _els.results.querySelectorAll(".cmdk-item");
  for (var i = 0; i < rows.length; i += 1) {
    var on = Number(rows[i].dataset.index) === index;
    rows[i].classList.toggle("is-active", on);
    rows[i].setAttribute("aria-selected", on ? "true" : "false");
    if (on && scroll) {
      rows[i].scrollIntoView({ block: "nearest" });
    }
  }
  updateActiveDescendant();
}

function moveSelection(delta) {
  if (_results.length === 0) return;
  var n = _results.length;
  var next =
    _selected < 0
      ? delta > 0
        ? 0
        : n - 1
      : (_selected + delta + n) % n; // wrap around the ends
  setSelected(next, true);
}

function updateActiveDescendant() {
  if (!_els) return;
  if (_selected >= 0 && _results.length > 0) {
    _els.input.setAttribute("aria-activedescendant", "cmdk-opt-" + _selected);
  } else {
    _els.input.removeAttribute("aria-activedescendant");
  }
}

// ---------------------------------------------------------------------------
// Activation
// ---------------------------------------------------------------------------

// Close FIRST (restores focus to the prior element), then run the action / jump.
// The jump's dossier open and the action's renders don't grab focus, so closing
// first keeps focus handling clean.
function activate(index) {
  var row = _results[index];
  if (!row) return;
  close();
  if (row.kind === "action") {
    if (row.action && typeof row.action.run === "function") {
      row.action.run();
    }
  } else if (_api && typeof _api.jumpToItem === "function") {
    _api.jumpToItem(row.entry.item.id);
  }
}

// ---------------------------------------------------------------------------
// Open / close / toggle
// ---------------------------------------------------------------------------

function isOpen() {
  return _open;
}

function open() {
  if (_open) return;
  buildPalette();
  _lastFocus =
    document.activeElement && document.activeElement !== document.body
      ? document.activeElement
      : null;
  buildIndex(); // refresh candidates from the live model
  _open = true;
  _els.root.classList.add("is-open");
  _els.root.setAttribute("aria-hidden", "false");
  _els.input.value = "";
  renderResults();
  // Focus after the .is-open display flip so the input can take focus.
  _els.input.focus();
}

function close() {
  if (!_open) return;
  _open = false;
  if (_els) {
    _els.root.classList.remove("is-open");
    _els.root.setAttribute("aria-hidden", "true");
  }
  _selected = -1;
  _results = [];
  // Restore focus to wherever it was, if that element still exists + is focusable.
  var prev = _lastFocus;
  _lastFocus = null;
  if (prev && prev.isConnected && typeof prev.focus === "function") {
    try {
      prev.focus();
    } catch (e) {
      /* focusing a detached/odd element is best-effort */
    }
  }
}

function toggle() {
  if (_open) close();
  else open();
}

// ---------------------------------------------------------------------------
// DOM construction (once)
// ---------------------------------------------------------------------------

function buildPalette() {
  if (_els) return _els;

  var root = document.createElement("div");
  root.id = "cmd-palette";
  root.className = "cmdk";
  root.setAttribute("role", "dialog");
  root.setAttribute("aria-modal", "true");
  root.setAttribute("aria-label", "Command palette");
  root.setAttribute("aria-hidden", "true");

  var panel = document.createElement("div");
  panel.className = "cmdk-panel";

  // Input row: search glyph + the text field + an Esc hint pill.
  var inputRow = document.createElement("div");
  inputRow.className = "cmdk-input-row";

  var sg = document.createElement("span");
  sg.className = "cmdk-search-glyph";
  sg.setAttribute("aria-hidden", "true");
  sg.textContent = "⌕";
  inputRow.appendChild(sg);

  var input = document.createElement("input");
  input.type = "text";
  input.className = "cmdk-input";
  input.setAttribute("placeholder", "Search titles or run a command…");
  input.setAttribute("aria-label", "Search titles and commands");
  input.setAttribute("role", "combobox");
  input.setAttribute("aria-expanded", "true");
  input.setAttribute("aria-autocomplete", "list");
  input.setAttribute("aria-controls", "cmdk-listbox");
  input.setAttribute("autocomplete", "off");
  input.setAttribute("autocorrect", "off");
  input.setAttribute("autocapitalize", "off");
  input.spellcheck = false;
  inputRow.appendChild(input);

  var esc = document.createElement("kbd");
  esc.className = "cmdk-esc";
  esc.setAttribute("aria-hidden", "true");
  esc.textContent = "Esc";
  inputRow.appendChild(esc);

  panel.appendChild(inputRow);

  // Results listbox.
  var results = document.createElement("div");
  results.id = "cmdk-listbox";
  results.className = "cmdk-results";
  results.setAttribute("role", "listbox");
  results.setAttribute("aria-label", "Titles and commands");
  panel.appendChild(results);

  // Footer key hints.
  var foot = document.createElement("div");
  foot.className = "cmdk-foot";
  foot.appendChild(footHint());
  panel.appendChild(foot);

  root.appendChild(panel);
  document.body.appendChild(root);

  // --- Listeners ---
  input.addEventListener("input", renderResults);
  input.addEventListener("keydown", onInputKeydown);
  // A click on the dim backdrop (never the panel) closes; preventDefault avoids a
  // focus flicker before we tear down.
  root.addEventListener("mousedown", function (e) {
    if (e.target === root) {
      e.preventDefault();
      close();
    }
  });

  _els = { root: root, panel: panel, input: input, results: results };
  return _els;
}

// "↑↓ navigate · ↵ open · esc close" rendered with small <kbd> glyphs.
function footHint() {
  var frag = document.createDocumentFragment();

  function part(keys, label) {
    var wrap = document.createElement("span");
    wrap.className = "cmdk-foot-part";
    for (var i = 0; i < keys.length; i += 1) {
      var kbd = document.createElement("kbd");
      kbd.className = "cmdk-kbd";
      kbd.textContent = keys[i];
      wrap.appendChild(kbd);
    }
    var t = document.createElement("span");
    t.className = "cmdk-foot-label";
    t.textContent = label;
    wrap.appendChild(t);
    return wrap;
  }

  frag.appendChild(part(["↑", "↓"], "navigate"));
  frag.appendChild(part(["↵"], "open"));
  frag.appendChild(part(["esc"], "close"));
  return frag;
}

// ---------------------------------------------------------------------------
// Keyboard
// ---------------------------------------------------------------------------

function onInputKeydown(e) {
  var k = e.key;
  if (k === "ArrowDown") {
    e.preventDefault();
    moveSelection(1);
  } else if (k === "ArrowUp") {
    e.preventDefault();
    moveSelection(-1);
  } else if (k === "Home") {
    if (_results.length) {
      e.preventDefault();
      setSelected(0, true);
    }
  } else if (k === "End") {
    if (_results.length) {
      e.preventDefault();
      setSelected(_results.length - 1, true);
    }
  } else if (k === "Enter") {
    e.preventDefault();
    if (_selected >= 0) activate(_selected);
  } else if (k === "Escape") {
    e.preventDefault();
    e.stopPropagation(); // don't also trip another module's document Esc handler
    close();
  } else if (k === "Tab") {
    // Focus trap: the input is the only focusable control while open.
    e.preventDefault();
  }
}

// True when the event originated in a field where a bare keystroke is text input
// (so the global "/" shortcut must not steal it).
function isTypingTarget(el) {
  if (!el) return false;
  var tag = el.tagName;
  if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") return true;
  if (el.isContentEditable) return true;
  return false;
}

// ---------------------------------------------------------------------------
// Header trigger affordance (discoverability + mobile tap target)
// ---------------------------------------------------------------------------

function isMac() {
  try {
    var s = navigator.platform || navigator.userAgent || "";
    return /Mac|iPhone|iPod|iPad/i.test(s);
  } catch (e) {
    return false;
  }
}

function shortcutHint() {
  return isMac() ? "⌘K" : "Ctrl K";
}

// Inject a small "Search" button into the header tab row. On desktop it shows the
// shortcut (the discovery hint); on mobile, where the keyboard shortcut isn't
// available, it's the tap target that opens the palette.
function injectTrigger() {
  var row = document.querySelector(".tabbar-row");
  if (!row || document.getElementById("cmdk-trigger")) return;

  var btn = document.createElement("button");
  btn.type = "button";
  btn.id = "cmdk-trigger";
  btn.className = "cmdk-trigger";
  btn.title = "Search & commands (" + shortcutHint() + ")";
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
  kbd.textContent = shortcutHint();
  btn.appendChild(kbd);

  btn.addEventListener("click", open);
  row.appendChild(btn);
}

// ---------------------------------------------------------------------------
// Public entrypoint (call once from app.js init)
// ---------------------------------------------------------------------------

export function wireCommandPalette(api) {
  _api = api || {};
  _actions = buildActions();

  // Build the overlay eagerly so #cmd-palette + .cmdk-input exist for inspection
  // and the verify harness from the first paint (they stay display:none until
  // .is-open). The candidate index is still (re)built lazily on each open.
  buildPalette();
  injectTrigger();

  // Global shortcuts. Cmd/Ctrl+K toggles from anywhere; "/" opens only when the
  // palette is closed AND the user isn't typing in another field.
  document.addEventListener("keydown", function (e) {
    var k = e.key;
    if ((e.metaKey || e.ctrlKey) && (k === "k" || k === "K")) {
      e.preventDefault();
      toggle();
      return;
    }
    if (k === "/" && !isOpen() && !isTypingTarget(e.target)) {
      e.preventDefault();
      open();
    }
  });
}
