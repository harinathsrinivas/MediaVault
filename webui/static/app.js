/* MediaVault Console — Candidate A: mission-control data table.
 *
 * One dense, sortable, keyboard-navigable table. Each reclaim item is a row;
 * filter chips above toggle row visibility by badge; a shared modal gates the
 * destructive `replace`. All logic lives here so `node --check app.js` covers
 * the whole behavior contract. Vanilla JS, no framework, no build step.
 */
"use strict";

/* ------------------------------------------------------------------ *
 * Constants: the four badges (underscore = API truth) + display map. *
 * ------------------------------------------------------------------ */
const BADGES = [
  "UNPREPPED",
  "LOCAL_NOT_PUSHED",
  "PUSHED_NOT_ARCHIVED",
  "RESTORED_REPLACE_AGAIN",
];

// Readable labels (middot-separated) + the per-state accent color + sort rank.
const BADGE_META = {
  UNPREPPED:              { label: "UNPREPPED",          color: "var(--b-unprepped)", rank: 0 },
  LOCAL_NOT_PUSHED:       { label: "LOCAL·NOT-PUSHED",   color: "var(--b-localpush)", rank: 1 },
  PUSHED_NOT_ARCHIVED:    { label: "PUSHED·NOT-ARCHIVED",color: "var(--b-pusharch)",  rank: 2 },
  RESTORED_REPLACE_AGAIN: { label: "RESTORED·REPLACE",   color: "var(--b-restored)",  rank: 3 },
};

// badge -> primary action descriptor. `action` is the /api/action/{name} path
// segment; `verb` is the button label; `build` returns the POST body (sans the
// confirm flag, which the modal adds for replace).
const ACTION_FOR_BADGE = {
  UNPREPPED: {
    action: "prep",
    verb: "Prep",
    cls: "act--prep",
    build: (it) => ({ id: it.id, filepath: it.path }),
    destructive: false,
  },
  LOCAL_NOT_PUSHED: {
    action: "push",
    verb: "Push",
    cls: "act--push",
    build: (it) => ({ id: it.id }),
    destructive: false,
  },
  PUSHED_NOT_ARCHIVED: {
    action: "replace",
    verb: "Replace",
    cls: "act--replace",
    build: (it) => ({ id: it.id }),
    destructive: true,
  },
  RESTORED_REPLACE_AGAIN: {
    action: "replace",
    verb: "Replace",
    cls: "act--replace",
    build: (it) => ({ id: it.id }),
    destructive: true,
  },
};

const POLL_MS = 1000;

/* ------------------------------------------------------------------ *
 * Module state.                                                       *
 * ------------------------------------------------------------------ */
const state = {
  items: [],                       // raw rows from /api/reclaim, with a stable .__key
  visible: new Set(BADGES),        // badges currently shown (chips)
  sortKey: "size_bytes",
  sortDir: -1,                     // -1 desc (largest reclaim first), 1 asc
  focusIdx: -1,                    // index into the *rendered* row order
};

let _keySeq = 0;

/* ------------------------------------------------------------------ *
 * Small DOM + format helpers.                                        *
 * ------------------------------------------------------------------ */
const $ = (sel, root = document) => root.querySelector(sel);

function el(tag, attrs = {}, children = []) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v === null || v === undefined || v === false) continue;
    if (k === "class") node.className = v;
    else if (k === "text") node.textContent = v;
    else if (k === "html") node.innerHTML = v;
    else if (k.startsWith("on") && typeof v === "function") {
      node.addEventListener(k.slice(2).toLowerCase(), v);
    } else node.setAttribute(k, v === true ? "" : String(v));
  }
  for (const c of [].concat(children)) {
    if (c == null) continue;
    node.appendChild(typeof c === "string" ? document.createTextNode(c) : c);
  }
  return node;
}

// Binary-unit size formatter (mirrors the server's human_readable_size feel).
// Returns [number-string, unit] so the UI can style them separately.
function fmtSize(bytes) {
  if (!Number.isFinite(bytes) || bytes <= 0) return ["0", "B"];
  const units = ["B", "KB", "MB", "GB", "TB", "PB"];
  let i = 0;
  let v = bytes;
  while (v >= 1024 && i < units.length - 1) { v /= 1024; i += 1; }
  const num = i === 0 ? String(v) : v.toFixed(v >= 100 ? 0 : v >= 10 ? 1 : 2);
  return [num, units[i]];
}

function announce(msg) {
  const live = $("#aria-live");
  if (live) live.textContent = msg;
}

/* ------------------------------------------------------------------ *
 * Copy-to-clipboard with a robust fallback (offline/insecure-context *
 * safe: navigator.clipboard may be unavailable on plain http).       *
 * ------------------------------------------------------------------ */
async function copyText(text, btn) {
  let ok = false;
  try {
    if (navigator.clipboard && window.isSecureContext) {
      await navigator.clipboard.writeText(text);
      ok = true;
    }
  } catch (_) { ok = false; }
  if (!ok) {
    // Fallback: temporary textarea + execCommand.
    try {
      const ta = el("textarea", { value: text });
      ta.style.position = "fixed";
      ta.style.opacity = "0";
      document.body.appendChild(ta);
      ta.focus();
      ta.select();
      ok = document.execCommand("copy");
      document.body.removeChild(ta);
    } catch (_) { ok = false; }
  }
  if (btn) {
    const prev = btn.textContent;
    btn.textContent = ok ? "COPIED" : "ERR";
    btn.classList.toggle("copied", ok);
    setTimeout(() => { btn.textContent = prev; btn.classList.remove("copied"); }, 1200);
  }
  announce(ok ? "Copied to clipboard" : "Copy failed");
}

/* ------------------------------------------------------------------ *
 * API calls.                                                          *
 * ------------------------------------------------------------------ */
async function apiReclaim() {
  const r = await fetch("/api/reclaim");
  if (!r.ok) throw new Error(`/api/reclaim -> ${r.status}`);
  return r.json();
}

// POST an action; returns the job_id. Throws on non-202 with the server detail.
async function postAction(name, body) {
  const r = await fetch(`/api/action/${name}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body || {}),
  });
  if (r.status === 202) {
    const data = await r.json();
    return data.job_id;
  }
  let detail = `HTTP ${r.status}`;
  try { const j = await r.json(); if (j && j.detail) detail = j.detail; } catch (_) {}
  const err = new Error(detail);
  err.status = r.status;
  throw err;
}

async function apiJob(jobId) {
  const r = await fetch(`/api/job/${jobId}`);
  if (!r.ok) throw new Error(`/api/job/${jobId} -> ${r.status}`);
  return r.json();
}

/* ------------------------------------------------------------------ *
 * Job polling: drives the inline readout under a row's action cell.  *
 * Renders status + captured output faithfully (errors are SHOWN).    *
 * ------------------------------------------------------------------ */
function renderJobLog(logEl, { status, output, jobId }) {
  logEl.className = `joblog joblog--${status}`;
  logEl.innerHTML = "";

  const head = el("div", { class: "joblog__head" });
  if (status === "running") head.appendChild(el("span", { class: "spin" }));
  head.appendChild(el("span", { class: "joblog__status", text: status.toUpperCase() }));
  if (jobId) head.appendChild(el("span", { class: "joblog__id", text: `job #${jobId}` }));
  logEl.appendChild(head);

  const body = output && output.trim() ? output : "(no output captured)";
  logEl.appendChild(el("pre", { text: body }));
}

async function pollJob(jobId, logEl) {
  // Loop until done|error. Network hiccups are reported but stop the loop so a
  // dead server doesn't spin forever.
  // eslint-disable-next-line no-constant-condition
  while (true) {
    let rec;
    try {
      rec = await apiJob(jobId);
    } catch (e) {
      renderJobLog(logEl, { status: "error", output: String(e.message || e), jobId });
      return;
    }
    renderJobLog(logEl, {
      status: rec.status,
      output: rec.output,
      jobId: rec.id || jobId,
    });
    if (rec.status === "done" || rec.status === "error") {
      announce(`Job ${jobId} ${rec.status}`);
      return;
    }
    await new Promise((res) => setTimeout(res, POLL_MS));
  }
}

/* Disable an action button while its job runs; re-enable on completion. */
async function runAction(btn, logEl, name, body) {
  btn.disabled = true;
  renderJobLog(logEl, { status: "running", output: "Dispatching…", jobId: null });
  logEl.hidden = false;
  let jobId;
  try {
    jobId = await postAction(name, body);
  } catch (e) {
    // 409 (replace w/o confirm — should never reach here), 404, etc.
    renderJobLog(logEl, { status: "error", output: String(e.message || e), jobId: null });
    btn.disabled = false;
    return;
  }
  await pollJob(jobId, logEl);
  btn.disabled = false;
}

/* ------------------------------------------------------------------ *
 * Replace confirm modal — the only path that may POST replace.       *
 * Resolves a promise true/false; the body's confirm:true is added    *
 * by the CALLER only after a true resolution.                        *
 * ------------------------------------------------------------------ */
let _modalResolve = null;

function setupModal() {
  const modal = $("#modal");
  const onCancel = () => closeModal(false);
  $("#modal-cancel").addEventListener("click", () => closeModal(false));
  $("#modal-confirm").addEventListener("click", () => closeModal(true));
  // Backdrop click (outside the panel) cancels.
  modal.addEventListener("mousedown", (e) => { if (e.target === modal) onCancel(); });
  // Esc cancels.
  document.addEventListener("keydown", (e) => {
    if (!modal.hidden && e.key === "Escape") { e.preventDefault(); onCancel(); }
  });
}

function confirmReplace(item) {
  const modal = $("#modal");
  $("#modal-id").textContent = item.id;
  modal.hidden = false;
  // Default focus on Cancel — destructive confirm should require intent.
  $("#modal-cancel").focus();
  return new Promise((resolve) => { _modalResolve = resolve; });
}

function closeModal(result) {
  const modal = $("#modal");
  modal.hidden = true;
  const r = _modalResolve;
  _modalResolve = null;
  if (r) r(result);
}

/* ------------------------------------------------------------------ *
 * Cell builders.                                                      *
 * ------------------------------------------------------------------ */
function buildBadgeCell(item) {
  const meta = BADGE_META[item.badge] || { label: item.badge, color: "var(--fg)" };
  const td = el("td", { class: "cell-badge", "data-label": "State" });
  td.style.setProperty("--row-color", meta.color);
  const pill = el("span", { class: "pill", text: meta.label });
  pill.style.setProperty("--pill-color", meta.color);
  td.appendChild(pill);
  return td;
}

function buildIdCell(item) {
  const td = el("td", { class: "cell-id", "data-label": "ID / Path" });
  const mid = el("span", {
    class: item.guessed ? "mid is-guessed" : "mid",
    text: item.id,
    title: item.guessed ? "Guessed id — edit before prepping if wrong" : item.id,
  });
  td.appendChild(mid);
  if (item.guessed) td.appendChild(el("span", { class: "guess-tag", text: "guess" }));
  td.appendChild(el("div", { class: "path", text: item.path, title: item.path }));
  return td;
}

function buildSizeCell(item) {
  const [num, unit] = fmtSize(item.size_bytes);
  const td = el("td", { class: "cell-size", "data-label": "Size",
    title: `${item.size_bytes} bytes` });
  td.appendChild(el("span", { class: "sz-num", text: num }));
  td.appendChild(el("span", { class: "sz-unit", text: unit }));
  return td;
}

// Generic copy widget: a read-only code span (or an editable input) + Copy btn.
function copyWidget({ value, editable, disabled, placeholder, onInput }) {
  const wrap = el("div", { class: "copyfield" });
  let valueNode;
  if (editable) {
    valueNode = el("input", {
      class: "provider",
      type: "text",
      value: value || "",
      placeholder: placeholder || "",
      disabled: !!disabled,
      spellcheck: "false",
      autocomplete: "off",
    });
    if (onInput) valueNode.addEventListener("input", onInput);
  } else {
    valueNode = el("code", { text: value || "" });
  }
  wrap.appendChild(valueNode);
  const btn = el("button", { class: "copybtn", type: "button", text: "COPY" });
  btn.addEventListener("click", () => {
    const v = editable ? valueNode.value : value;
    copyText(v, btn);
  });
  wrap.appendChild(btn);
  return wrap;
}

function buildCommandCell(item) {
  const td = el("td", { class: "cell-cmd", "data-label": "Suggested command" });
  td.appendChild(copyWidget({ value: item.suggested_command || "", editable: false }));
  return td;
}

function buildFolderCell(item) {
  const td = el("td", { class: "cell-folder", "data-label": "Suggested folder" });
  const sf = item.suggested_folder || {};
  const applies = sf.applies === true;

  // When applies: NEW-item suggestion — the folder contains a {provider-id}
  // placeholder the operator edits. When not: existing folder, informational
  // (input disabled + annotated so the distinction is unmissable).
  td.appendChild(copyWidget({
    value: sf.folder || "",
    editable: true,
    disabled: !applies,
    placeholder: applies ? `edit ${sf.editable_provider_field || "provider"}-id` : "",
  }));

  if (applies) {
    const field = sf.editable_provider_field
      ? `new · edit ${sf.editable_provider_field}-id in { }`
      : "new · edit provider-id";
    td.appendChild(el("div", { class: "folder-note is-new", text: field }));
  } else {
    td.appendChild(el("div", { class: "folder-note", text: "existing · read-only" }));
  }
  return td;
}

function buildActionCell(item) {
  const td = el("td", { class: "cell-actions", "data-label": "Action" });
  const desc = ACTION_FOR_BADGE[item.badge];
  if (!desc) {
    td.appendChild(el("span", { class: "folder-note", text: "no action" }));
    return td;
  }
  const btn = el("button", {
    class: `act ${desc.cls}`,
    type: "button",
    text: desc.verb,
    "data-primary-action": "1",   // marks the row's primary action for Enter-key
  });
  const log = el("div", { class: "joblog", hidden: true });

  btn.addEventListener("click", () => fireAction(item, desc, btn, log));
  td.appendChild(btn);
  td.appendChild(log);
  return td;
}

// Central action dispatch — applies the 409-safe replace gate.
async function fireAction(item, desc, btn, log) {
  let body = desc.build(item);
  if (desc.destructive) {
    const ok = await confirmReplace(item);
    if (!ok) return;                 // user cancelled — NO post happens
    body = { ...body, confirm: true }; // server demands confirm:true or 409s
  }
  await runAction(btn, log, desc.action, body);
}

/* ------------------------------------------------------------------ *
 * Sorting + filtering -> rendered row order.                          *
 * ------------------------------------------------------------------ */
function sortedFilteredItems() {
  const rows = state.items.filter((it) => state.visible.has(it.badge));
  const key = state.sortKey;
  const dir = state.sortDir;
  rows.sort((a, b) => {
    let av;
    let bv;
    if (key === "size_bytes") { av = a.size_bytes; bv = b.size_bytes; }
    else if (key === "badge") { av = BADGE_META[a.badge]?.rank ?? 99; bv = BADGE_META[b.badge]?.rank ?? 99; }
    else { av = String(a[key] || "").toLowerCase(); bv = String(b[key] || "").toLowerCase(); }
    if (av < bv) return -1 * dir;
    if (av > bv) return 1 * dir;
    // Stable tiebreak by id so equal sizes keep a deterministic order.
    return String(a.id).localeCompare(String(b.id)) * dir;
  });
  return rows;
}

function buildRow(item, idx) {
  const meta = BADGE_META[item.badge] || { color: "var(--fg)" };
  const tr = el("tr", {
    class: "row",
    tabindex: "-1",                 // focusable but not in default tab order
    "data-idx": idx,
    "data-badge": item.badge,
    "data-id": item.id,
  });
  tr.style.setProperty("--row-color", meta.color);
  tr.appendChild(buildBadgeCell(item));
  tr.appendChild(buildIdCell(item));
  tr.appendChild(buildSizeCell(item));
  tr.appendChild(buildCommandCell(item));
  tr.appendChild(buildFolderCell(item));
  tr.appendChild(buildActionCell(item));
  return tr;
}

function renderRows() {
  const tbody = $("#rows");
  const grid = $("#grid");
  const banner = $("#banner");
  const rows = sortedFilteredItems();

  tbody.innerHTML = "";

  if (state.items.length === 0) {
    grid.hidden = true;
    banner.hidden = false;
    banner.className = "banner";
    banner.innerHTML =
      '<h2>Nothing to reclaim</h2><p>No local files are occupying reclaimable space. ' +
      'You are all clear.</p>';
    return;
  }
  if (rows.length === 0) {
    grid.hidden = true;
    banner.hidden = false;
    banner.className = "banner";
    banner.innerHTML =
      '<h2>All states filtered out</h2><p>Enable a filter chip above to show items.</p>';
    return;
  }

  banner.hidden = true;
  grid.hidden = false;
  rows.forEach((item, i) => tbody.appendChild(buildRow(item, i)));

  // Clamp + restore focus highlight after a re-render (sort/filter).
  if (state.focusIdx >= rows.length) state.focusIdx = rows.length - 1;
  applyFocusHighlight();
  updateSortIndicators();
}

function updateSortIndicators() {
  document.querySelectorAll("th.sortable").forEach((th) => {
    const ind = th.querySelector(".sort-ind");
    if (ind) ind.remove();
    if (th.dataset.sort === state.sortKey) {
      th.appendChild(el("span", { class: "sort-ind",
        text: state.sortDir === -1 ? "▼" : "▲" }));
    }
  });
}

/* ------------------------------------------------------------------ *
 * Keyboard navigation over the rendered rows.                         *
 * ------------------------------------------------------------------ */
function currentRows() {
  return Array.from(document.querySelectorAll("#rows tr.row"));
}

function applyFocusHighlight() {
  const rows = currentRows();
  rows.forEach((r) => r.classList.remove("is-focused"));
  if (state.focusIdx >= 0 && state.focusIdx < rows.length) {
    rows[state.focusIdx].classList.add("is-focused");
  }
}

function moveFocus(delta) {
  const rows = currentRows();
  if (rows.length === 0) return;
  let i = state.focusIdx;
  if (i < 0) i = delta > 0 ? 0 : rows.length - 1;
  else i = Math.min(rows.length - 1, Math.max(0, i + delta));
  state.focusIdx = i;
  rows[i].focus();
  rows[i].scrollIntoView({ block: "nearest" });
  applyFocusHighlight();
}

function activateFocusedRow() {
  const rows = currentRows();
  if (state.focusIdx < 0 || state.focusIdx >= rows.length) return;
  const btn = rows[state.focusIdx].querySelector('[data-primary-action="1"]');
  if (btn && !btn.disabled) btn.click();
}

function setupKeyboard() {
  document.addEventListener("keydown", (e) => {
    // Don't hijack typing in the editable provider field or the modal.
    if ($("#modal").hidden === false) return;
    const tag = (e.target.tagName || "").toLowerCase();
    const typing = tag === "input" || tag === "textarea";

    if (e.key === "ArrowDown") {
      if (typing) return;
      e.preventDefault();
      moveFocus(1);
    } else if (e.key === "ArrowUp") {
      if (typing) return;
      e.preventDefault();
      moveFocus(-1);
    } else if (e.key === "Enter") {
      // Enter on the table activates the focused row's primary action, unless
      // focus is in a text field or already on a button (let the button fire).
      if (typing || tag === "button") return;
      const rows = currentRows();
      if (state.focusIdx >= 0 && state.focusIdx < rows.length) {
        e.preventDefault();
        activateFocusedRow();
      }
    }
  });

  // Clicking a row body (not a control) focuses it for keyboard ops.
  $("#rows").addEventListener("focusin", (e) => {
    const tr = e.target.closest("tr.row");
    if (!tr) return;
    const idx = currentRows().indexOf(tr);
    if (idx >= 0) { state.focusIdx = idx; applyFocusHighlight(); }
  });
}

/* ------------------------------------------------------------------ *
 * Filter chips + sort headers + Sort-library button.                  *
 * ------------------------------------------------------------------ */
function buildChips() {
  const container = $("#chips");
  const anchor = $(".chips__spacer");
  // Inject one chip per badge, before the spacer.
  for (const badge of BADGES) {
    const meta = BADGE_META[badge];
    const chip = el("button", {
      class: "chip",
      type: "button",
      "data-badge": badge,
      "aria-pressed": "true",
      title: `Toggle ${badge} rows`,
    });
    chip.style.setProperty("--chip-color", meta.color);
    chip.appendChild(el("span", { class: "chip__dot" }));
    chip.appendChild(document.createTextNode(meta.label));
    chip.appendChild(el("span", { class: "chip__count", "data-count": badge, text: "0" }));
    chip.addEventListener("click", () => toggleBadge(badge));
    container.insertBefore(chip, anchor);
  }
  $("#chip-all").addEventListener("click", toggleAll);
}

function toggleBadge(badge) {
  if (state.visible.has(badge)) state.visible.delete(badge);
  else state.visible.add(badge);
  state.focusIdx = -1;
  syncChipStates();
  renderRows();
}

function toggleAll() {
  // If everything is on, turn all off; otherwise turn all on.
  const allOn = BADGES.every((b) => state.visible.has(b));
  state.visible = new Set(allOn ? [] : BADGES);
  state.focusIdx = -1;
  syncChipStates();
  renderRows();
}

function syncChipStates() {
  for (const badge of BADGES) {
    const chip = document.querySelector(`.chip[data-badge="${badge}"]`);
    if (chip) chip.setAttribute("aria-pressed", String(state.visible.has(badge)));
  }
  const allOn = BADGES.every((b) => state.visible.has(b));
  $("#chip-all").setAttribute("aria-pressed", String(allOn));
}

function updateCounts() {
  const counts = Object.fromEntries(BADGES.map((b) => [b, 0]));
  for (const it of state.items) {
    if (counts[it.badge] !== undefined) counts[it.badge] += 1;
  }
  for (const badge of BADGES) {
    const node = document.querySelector(`.chip__count[data-count="${badge}"]`);
    if (node) node.textContent = String(counts[badge]);
  }
  $("#count-all").textContent = String(state.items.length);
}

function setupSortHeaders() {
  document.querySelectorAll("th.sortable").forEach((th) => {
    th.addEventListener("click", () => {
      const key = th.dataset.sort;
      if (state.sortKey === key) {
        state.sortDir *= -1;
      } else {
        state.sortKey = key;
        // Default: size desc (largest reclaim first), text/badge asc.
        state.sortDir = key === "size_bytes" ? -1 : 1;
      }
      renderRows();
    });
  });
}

function setupSortLibraryButton() {
  const btn = $("#btn-sort");
  btn.addEventListener("click", async () => {
    btn.disabled = true;
    const prev = btn.textContent;
    btn.textContent = "Sorting…";
    // Surface the sort job in a transient log pinned under the chips.
    let log = $("#global-joblog");
    if (!log) {
      log = el("div", { id: "global-joblog", class: "joblog", style: "margin:8px 18px;" });
      $("#topbar").appendChild(log);
    }
    log.hidden = false;
    try {
      const jobId = await postAction("sort", {});
      await pollJob(jobId, log);
    } catch (e) {
      renderJobLog(log, { status: "error", output: String(e.message || e), jobId: null });
    } finally {
      btn.disabled = false;
      btn.textContent = prev;
    }
  });
}

/* ------------------------------------------------------------------ *
 * Header readout + sticky offset wiring.                              *
 * ------------------------------------------------------------------ */
function renderHeader(data) {
  const total = data.total_reclaimable_human
    || `${(fmtSize(data.total_reclaimable_bytes).join(" "))}`;
  $("#reclaim-total").textContent = total;
  const bytes = Number(data.total_reclaimable_bytes || 0);
  $("#reclaim-bytes").textContent = bytes ? `${bytes.toLocaleString()} bytes` : "";
}

// Pin the table header just below the (variable-height) sticky topbar.
function syncStickyOffset() {
  const topbar = $("#topbar");
  if (!topbar) return;
  const h = topbar.getBoundingClientRect().height;
  document.querySelectorAll("table.grid thead th").forEach((th) => {
    th.style.top = `${Math.round(h)}px`;
  });
}

/* ------------------------------------------------------------------ *
 * Boot.                                                               *
 * ------------------------------------------------------------------ */
async function load() {
  const banner = $("#banner");
  try {
    const data = await apiReclaim();
    // Assign stable keys (defensive; ids should be unique but guard anyway).
    state.items = (data.items || []).map((it) => {
      _keySeq += 1;
      return { ...it, __key: `${it.id}#${_keySeq}` };
    });
    renderHeader(data);
    updateCounts();
    syncChipStates();
    renderRows();
    syncStickyOffset();
  } catch (e) {
    banner.hidden = false;
    banner.className = "banner banner--error";
    banner.innerHTML =
      `<h2>Failed to load</h2><p><code>${String(e.message || e)}</code></p>` +
      "<p>Is the console server running?</p>";
    $("#grid").hidden = true;
  }
}

function main() {
  buildChips();
  setupSortHeaders();
  setupSortLibraryButton();
  setupKeyboard();
  setupModal();
  window.addEventListener("resize", syncStickyOffset);
  load();
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", main);
} else {
  main();
}
