/* MediaVault Console — Candidate C: master-detail SPA.
 *
 * Information architecture: a LEFT filterable rail of compact item rows
 * (badge dot + id + size) and a RIGHT detail pane that shows the SELECTED
 * item in full. Act on one item at a time with full context. Vanilla JS,
 * no framework, no build step. All untrusted strings (paths, ids, job
 * output) are inserted via textContent — never innerHTML — for XSS safety.
 */
"use strict";

/* ------------------------------------------------------------------ *
 * Badge metadata — the four reclaim states. `value` is the API's
 * underscore form (ground truth); `label` is the human display form;
 * `action`/`verb` map the badge to its single reclaim action.
 * ------------------------------------------------------------------ */
const BADGES = [
  { value: "UNPREPPED",              label: "UNPREPPED",          action: "prep",    verb: "Prep" },
  { value: "LOCAL_NOT_PUSHED",       label: "LOCAL·NOT-PUSHED",   action: "push",    verb: "Push" },
  { value: "PUSHED_NOT_ARCHIVED",    label: "PUSHED·NOT-ARCHIVED", action: "replace", verb: "Replace" },
  { value: "RESTORED_REPLACE_AGAIN", label: "RESTORED·REPLACE-AGAIN", action: "replace", verb: "Replace" },
];
const BADGE_BY_VALUE = Object.fromEntries(BADGES.map((b) => [b.value, b]));

const POLL_MS = 1000;

/* ------------------------------------------------------------------ *
 * Application state.
 * ------------------------------------------------------------------ */
const state = {
  items: [],                  // raw items from /api/reclaim
  totalHuman: "—",
  filters: new Set(BADGES.map((b) => b.value)), // badges currently SHOWN
  selectedId: null,           // currently selected item id (detail pane)
  provider: {},               // edited provider-id text, keyed by item id
  pendingReplaceId: null,     // item awaiting modal confirmation
};

/* Small DOM helpers. */
const $ = (sel) => document.querySelector(sel);
function el(tag, cls, text) {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text != null) n.textContent = text; // textContent => safe
  return n;
}
function clear(node) { while (node.firstChild) node.removeChild(node.firstChild); }

/* Human-readable byte size — mirrors main.human_readable_size (base 1024). */
function humanBytes(n) {
  if (n == null || isNaN(n)) return "—";
  if (n < 1024) return `${n} B`;
  const units = ["KB", "MB", "GB", "TB", "PB"];
  let size = n / 1024;
  let i = 0;
  while (size >= 1024 && i < units.length - 1) { size /= 1024; i += 1; }
  return `${size.toFixed(2)} ${units[i]}`;
}

/* ------------------------------------------------------------------ *
 * Clipboard — navigator.clipboard with an execCommand fallback. On
 * localhost (a non-secure context) the async clipboard API is often
 * unavailable, so the textarea+execCommand fallback is the real path.
 * ------------------------------------------------------------------ */
function copyText(text) {
  if (navigator.clipboard && window.isSecureContext) {
    return navigator.clipboard.writeText(text).then(
      () => true,
      () => legacyCopy(text)
    );
  }
  return Promise.resolve(legacyCopy(text));
}
function legacyCopy(text) {
  try {
    const ta = document.createElement("textarea");
    ta.value = text;
    ta.setAttribute("readonly", "");
    ta.style.position = "fixed";
    ta.style.top = "-1000px";
    ta.style.opacity = "0";
    document.body.appendChild(ta);
    ta.select();
    ta.setSelectionRange(0, text.length);
    const ok = document.execCommand("copy");
    document.body.removeChild(ta);
    return ok;
  } catch (_e) {
    return false;
  }
}

/* Transient toast. */
let toastTimer = null;
function toast(msg, isErr) {
  const t = $("#toast");
  t.textContent = msg;
  t.classList.toggle("err", !!isErr);
  t.classList.add("show");
  if (toastTimer) clearTimeout(toastTimer);
  toastTimer = setTimeout(() => t.classList.remove("show"), 2200);
}

/* A Copy button wired to copy `getText()` and flash "Copied". */
function makeCopyButton(getText) {
  const btn = el("button", "btn small", "Copy");
  btn.type = "button";
  btn.addEventListener("click", () => {
    Promise.resolve(copyText(getText())).then((ok) => {
      if (ok) {
        btn.textContent = "Copied";
        btn.classList.add("copied");
        toast("Copied to clipboard");
        setTimeout(() => {
          btn.textContent = "Copy";
          btn.classList.remove("copied");
        }, 1200);
      } else {
        toast("Copy failed — select and copy manually", true);
      }
    });
  });
  return btn;
}

/* ------------------------------------------------------------------ *
 * Data load.
 * ------------------------------------------------------------------ */
async function loadReclaim() {
  const banner = $("#load-banner");
  banner.hidden = true;
  try {
    const res = await fetch("/api/reclaim");
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    state.items = Array.isArray(data.items) ? data.items : [];
    state.totalHuman =
      data.total_reclaimable_human || humanBytes(data.total_reclaimable_bytes);

    // Keep selection if it still exists, else select the first visible item.
    if (!state.items.some((it) => it.id === state.selectedId)) {
      state.selectedId = null;
    }
    renderHeader();
    renderChips();
    renderRail();
    const visible = visibleItems();
    if (state.selectedId == null && visible.length) {
      selectItem(visible[0].id);
    } else {
      renderDetail();
    }
  } catch (err) {
    banner.hidden = false;
    banner.textContent = `Failed to load reclaim report: ${err.message}`;
  }
}

/* ------------------------------------------------------------------ *
 * Header: reclaimable total.
 * ------------------------------------------------------------------ */
function renderHeader() {
  $("#reclaim-total-num").textContent = state.totalHuman;
}

/* ------------------------------------------------------------------ *
 * Filter chips — one per badge; toggles client-side visibility.
 * aria-pressed reflects ON (shown) / OFF (filtered out).
 * ------------------------------------------------------------------ */
function renderChips() {
  const wrap = $("#filter-chips");
  clear(wrap);
  const counts = {};
  for (const it of state.items) counts[it.badge] = (counts[it.badge] || 0) + 1;

  for (const b of BADGES) {
    const on = state.filters.has(b.value);
    const chip = el("button", `chip b-${b.value}`);
    chip.type = "button";
    chip.setAttribute("aria-pressed", on ? "true" : "false");
    chip.title = on ? `Showing ${b.label} — click to hide` : `Hidden — click to show ${b.label}`;

    const dot = el("span", "dot");
    const lbl = el("span", null, b.label);
    const cnt = el("span", "count", String(counts[b.value] || 0));
    chip.append(dot, lbl, cnt);

    chip.addEventListener("click", () => {
      if (state.filters.has(b.value)) state.filters.delete(b.value);
      else state.filters.add(b.value);
      renderChips();
      renderRail();
      // If the selected item just got filtered out, move selection to the
      // first still-visible item (keeps the detail pane coherent).
      const visible = visibleItems();
      if (!visible.some((it) => it.id === state.selectedId)) {
        selectItem(visible.length ? visible[0].id : null);
      }
    });
    wrap.appendChild(chip);
  }
}

/* ------------------------------------------------------------------ *
 * Left rail.
 * ------------------------------------------------------------------ */
function visibleItems() {
  return state.items.filter((it) => state.filters.has(it.badge));
}

function renderRail() {
  const list = $("#rail-list");
  clear(list);
  const visible = visibleItems();

  $("#rail-shown").textContent = `${visible.length} / ${state.items.length}`;

  if (!state.items.length) {
    list.appendChild(el("div", "rail-empty", "Nothing reclaimable — disk is clean."));
    return;
  }
  if (!visible.length) {
    list.appendChild(el("div", "rail-empty", "No items match the active filters."));
    return;
  }

  for (const it of visible) {
    const badge = BADGE_BY_VALUE[it.badge];
    const row = el("div", `rail-row b-${it.badge}`);
    row.setAttribute("role", "option");
    row.dataset.id = it.id;
    row.setAttribute("aria-selected", it.id === state.selectedId ? "true" : "false");

    const dot = el("span", "rdot");
    const meta = el("div", "rmeta");
    meta.appendChild(el("div", "rid", it.id));
    meta.appendChild(el("div", "rsub", badge ? badge.label : it.badge));
    const size = el("span", "rsize", humanBytes(it.size_bytes));

    row.append(dot, meta, size);
    row.addEventListener("click", () => selectItem(it.id));
    list.appendChild(row);
  }
}

/* ------------------------------------------------------------------ *
 * Selection + keyboard navigation.
 * ------------------------------------------------------------------ */
function selectItem(id) {
  state.selectedId = id;

  // Update rail row highlight without a full re-render.
  const list = $("#rail-list");
  for (const row of list.querySelectorAll(".rail-row")) {
    const sel = row.dataset.id === id;
    row.setAttribute("aria-selected", sel ? "true" : "false");
    if (sel) row.scrollIntoView({ block: "nearest" });
  }

  // On narrow viewports, drill into the detail pane.
  if (id != null) document.body.dataset.view = "detail";

  renderDetail();
}

function moveSelection(delta) {
  const visible = visibleItems();
  if (!visible.length) return;
  let idx = visible.findIndex((it) => it.id === state.selectedId);
  if (idx === -1) idx = delta > 0 ? -1 : 0;
  let next = idx + delta;
  next = Math.max(0, Math.min(visible.length - 1, next));
  selectItem(visible[next].id);
}

function backToList() {
  document.body.dataset.view = "list";
  $("#rail-list").focus();
}

/* ------------------------------------------------------------------ *
 * Detail pane — full view of the selected item.
 * ------------------------------------------------------------------ */
function selectedItem() {
  return state.items.find((it) => it.id === state.selectedId) || null;
}

function renderDetail() {
  const inner = $("#detail-inner");
  const placeholder = $("#detail-placeholder");
  const it = selectedItem();

  if (!it) {
    inner.hidden = true;
    placeholder.style.display = "";
    return;
  }
  placeholder.style.display = "none";
  inner.hidden = false;
  clear(inner);

  const badge = BADGE_BY_VALUE[it.badge];

  // Back button (visible only in stacked/narrow layout via CSS).
  const back = el("button", "btn ghost small detail-back", "← Back to list");
  back.type = "button";
  back.addEventListener("click", backToList);
  inner.appendChild(back);

  // Badge pill.
  const pill = el("span", `d-pill b-${it.badge}`);
  pill.append(el("span", "dot"), el("span", null, badge ? badge.label : it.badge));
  inner.appendChild(pill);

  // Id (+ guessed flag for UNPREPPED).
  const idH = el("h1", "d-id", it.id);
  if (it.guessed) {
    const flag = el("span", "guess-flag", "guessed id — edit before prep");
    idH.appendChild(flag);
  }
  inner.appendChild(idH);

  // Size.
  inner.appendChild(el("div", "d-size", `${humanBytes(it.size_bytes)} reclaimable`));

  // Path.
  inner.appendChild(field("Path", pathBlock(it.path)));

  // Suggested command (read-only + copy).
  inner.appendChild(
    field(
      "Suggested next command",
      copyRow(it.suggested_command || "(no reclaim action)", null, () => it.suggested_command || "")
    )
  );

  // Suggested folder (+ editable provider-id when applies).
  inner.appendChild(suggestedFolderField(it));

  // Actions.
  inner.appendChild(actionsBlock(it, badge));

  // Job result (rebuilt fresh per render; cleared until an action runs).
  const jobMount = el("div");
  jobMount.id = "job-mount";
  inner.appendChild(jobMount);
}

function field(labelText, contentNode, hintText) {
  const f = el("div", "field");
  const lab = el("label", null, labelText);
  f.appendChild(lab);
  f.appendChild(contentNode);
  if (hintText) f.appendChild(el("div", "hint", hintText));
  return f;
}

function pathBlock(path) {
  return el("div", "path-box", path || "");
}

/* A read-only value box + Copy button. If `inputNode` is supplied it is used
 * as the value element (for editable fields); otherwise a static box. */
function copyRow(displayText, inputNode, getCopyText) {
  const row = el("div", "copy-row");
  const value = inputNode || el("div", "value", displayText);
  row.appendChild(value);
  row.appendChild(makeCopyButton(getCopyText));
  return row;
}

/* Suggested-folder field. When applies=true it's a NEW-item suggestion with an
 * editable provider id (the {tmdb-…}/{tvdb-…} placeholder). When false it's an
 * existing folder shown informationally: the editable field is disabled and
 * annotated. The Copy button copies the (possibly edited) folder string. */
function suggestedFolderField(it) {
  const sf = it.suggested_folder || {};
  const folder = sf.folder || "";
  const applies = sf.applies === true;

  const input = el("input", "value");
  input.type = "text";
  input.spellcheck = false;
  input.autocapitalize = "off";
  input.setAttribute("autocomplete", "off");

  // The editable text is the full folder string; the user edits the provider
  // placeholder inline. We seed from any prior edit for this item.
  const seeded = state.provider[it.id];
  input.value = seeded != null ? seeded : folder;

  if (applies) {
    input.addEventListener("input", () => { state.provider[it.id] = input.value; });
    const fieldNode = field(
      "Suggested folder (editable provider id)",
      copyRow(null, input, () => input.value),
      sf.editable_provider_field
        ? `New-item folder — replace the ${sf.editable_provider_field} placeholder in the braces before prepping.`
        : "New-item folder — edit the provider placeholder in the braces."
    );
    return fieldNode;
  }

  // Existing folder — informational only.
  input.disabled = true;
  return field(
    "Existing folder (informational)",
    copyRow(null, input, () => input.value),
    "Existing library folder — folders are never renamed, so this is read-only."
  );
}

/* ------------------------------------------------------------------ *
 * Actions.
 * ------------------------------------------------------------------ */
function actionsBlock(it, badge) {
  const wrap = el("div", "actions");
  if (!badge) {
    wrap.appendChild(el("div", "hint", "No reclaim action for this state."));
    return wrap;
  }

  const destructive = badge.action === "replace";
  const btn = el("button", `btn ${destructive ? "danger" : "primary"}`, badge.verb);
  btn.type = "button";
  btn.dataset.action = badge.action;
  btn.addEventListener("click", () => {
    if (destructive) {
      openReplaceModal(it.id);
    } else {
      runAction(badge.action, actionBody(badge.action, it));
    }
  });
  wrap.appendChild(btn);
  return wrap;
}

/* Build the POST body for a non-destructive action, per the contract. */
function actionBody(action, it) {
  if (action === "prep") return { id: it.id, filepath: it.path };
  if (action === "push") return { id: it.id };
  return { id: it.id };
}

/* ------------------------------------------------------------------ *
 * Replace confirm modal (gates the destructive POST).
 * ------------------------------------------------------------------ */
function openReplaceModal(id) {
  state.pendingReplaceId = id;
  const it = state.items.find((x) => x.id === id);
  $("#confirm-target").textContent = it ? it.id : id;
  const modal = $("#confirm-modal");
  modal.hidden = false;
  $("#confirm-ok").focus();
}
function closeReplaceModal() {
  state.pendingReplaceId = null;
  $("#confirm-modal").hidden = true;
}
function confirmReplace() {
  const id = state.pendingReplaceId;
  closeReplaceModal();
  if (id == null) return;
  // ONLY here do we POST replace, and ALWAYS with confirm:true (else 409).
  runAction("replace", { id: id, confirm: true });
}

/* ------------------------------------------------------------------ *
 * Action runner: POST -> 202 {job_id} -> poll job until done/error.
 * Renders status + captured output inline in the detail pane. Disables
 * action buttons while a job runs.
 * ------------------------------------------------------------------ */
let activeJob = false;

async function runAction(name, body) {
  if (activeJob) {
    toast("An action is already running — wait for it to finish.", true);
    return;
  }
  const mount = $("#job-mount");
  if (!mount) return;

  setButtonsDisabled(true);
  activeJob = true;
  renderJobPanel(mount, { status: "running", name: name, output: "" }, true);

  try {
    const res = await fetch(`/api/action/${encodeURIComponent(name)}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });

    if (res.status === 409) {
      // Should never happen for replace (we always send confirm) — surface it.
      renderJobPanel(mount, {
        status: "error",
        name: name,
        output: "Server rejected the action (409): confirmation required.",
      }, false);
      return;
    }
    if (res.status === 404) {
      renderJobPanel(mount, {
        status: "error", name: name, output: `Unknown action: ${name} (404).`,
      }, false);
      return;
    }
    if (res.status !== 202) {
      renderJobPanel(mount, {
        status: "error", name: name, output: `Unexpected response: HTTP ${res.status}.`,
      }, false);
      return;
    }

    const { job_id: jobId } = await res.json();
    await pollJob(jobId, mount);
  } catch (err) {
    renderJobPanel(mount, {
      status: "error", name: name, output: `Request failed: ${err.message}`,
    }, false);
  } finally {
    activeJob = false;
    setButtonsDisabled(false);
  }
}

function pollJob(jobId, mount) {
  return new Promise((resolve) => {
    const tick = async () => {
      try {
        const res = await fetch(`/api/job/${encodeURIComponent(jobId)}`);
        if (res.status === 404) {
          renderJobPanel(mount, {
            status: "error", name: "", output: `Job ${jobId} not found (404).`,
          }, false);
          return resolve();
        }
        const job = await res.json();
        const running = job.status === "running";
        renderJobPanel(mount, job, running);
        if (running) {
          setTimeout(tick, POLL_MS);
        } else {
          // Refresh the report after a terminal job so the rail reflects the
          // new state (item may have moved badge or disappeared).
          resolve();
          loadReclaim();
        }
      } catch (err) {
        renderJobPanel(mount, {
          status: "error", name: "", output: `Polling failed: ${err.message}`,
        }, false);
        resolve();
      }
    };
    tick();
  });
}

/* Render the job result panel. `output` is inserted via textContent (XSS-safe);
 * the running state shows a spinner. */
function renderJobPanel(mount, job, running) {
  clear(mount);
  const panel = el("div", "job");

  const head = el("div", "job-head");
  if (running) head.appendChild(el("span", "spinner"));
  const status = el("span", `jstatus ${job.status || "running"}`, job.status || "running");
  head.appendChild(status);
  if (job.name) head.appendChild(el("span", "jname", job.name));
  panel.appendChild(head);

  const out = el("pre", "job-out");
  out.textContent = job.output || "";
  panel.appendChild(out);

  mount.appendChild(panel);
}

function setButtonsDisabled(disabled) {
  for (const b of document.querySelectorAll(".actions .btn, #btn-sort")) {
    b.disabled = disabled;
  }
}

/* ------------------------------------------------------------------ *
 * Global wiring.
 * ------------------------------------------------------------------ */
function wireGlobal() {
  $("#btn-refresh").addEventListener("click", () => loadReclaim());

  $("#btn-sort").addEventListener("click", () => {
    // Sort is global; show its job in the detail pane's mount if present,
    // else fall back to a toast-only run by mounting transiently.
    const mount = $("#job-mount");
    if (mount) {
      runAction("sort", {});
    } else {
      // No item selected yet — run sort and surface via toast on completion.
      runSortStandalone();
    }
  });

  // Modal controls.
  $("#confirm-cancel").addEventListener("click", closeReplaceModal);
  $("#confirm-ok").addEventListener("click", confirmReplace);
  $("#confirm-modal").addEventListener("click", (e) => {
    if (e.target === $("#confirm-modal")) closeReplaceModal(); // backdrop click
  });

  // Keyboard: rail navigation + Escape closes modal / drills back.
  $("#rail-list").addEventListener("keydown", (e) => {
    if (e.key === "ArrowDown") { e.preventDefault(); moveSelection(1); }
    else if (e.key === "ArrowUp") { e.preventDefault(); moveSelection(-1); }
    else if (e.key === "Enter") {
      e.preventDefault();
      if (state.selectedId != null) document.body.dataset.view = "detail";
    }
  });

  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") {
      if (!$("#confirm-modal").hidden) { closeReplaceModal(); return; }
      if (document.body.dataset.view === "detail" &&
          window.matchMedia("(max-width: 760px)").matches) {
        backToList();
      }
    }
  });
}

/* Standalone sort run when no item is selected: build a transient mount in the
 * detail placeholder area so the user still sees status + output. */
function runSortStandalone() {
  const placeholder = $("#detail-placeholder");
  const inner = $("#detail-inner");
  placeholder.style.display = "none";
  inner.hidden = false;
  clear(inner);
  inner.appendChild(el("h1", "d-id", "Sort library"));
  const mount = el("div");
  mount.id = "job-mount";
  inner.appendChild(mount);
  runAction("sort", {});
}

/* ------------------------------------------------------------------ *
 * Boot.
 * ------------------------------------------------------------------ */
function boot() {
  wireGlobal();
  loadReclaim();
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", boot);
} else {
  boot();
}
