/* MediaVault Console — Candidate B (card grid).
 *
 * Single-page, no framework, no build step. All logic lives here so
 * `node --check app.js` covers the behavior. Rendering uses DOM APIs and
 * textContent only — captured job output is NEVER injected via innerHTML
 * (XSS safety on a localhost console that shells out to main.cmd_*).
 *
 * Behavior contract:
 *   - On load, GET /api/reclaim, render one card per item.
 *   - Header shows the reclaimable-GB hero stat + 4 filter chips (client-side).
 *   - Per card: badge / id / path / size / suggested command (+Copy) /
 *     suggested folder with editable provider-id (+Copy) / action button(s).
 *   - Action -> POST /api/action/{name} (202 {job_id}) -> poll
 *     GET /api/job/{job_id} every ~1s until done|error -> show status+output.
 *   - `replace` is gated by an unmissable confirm modal; only on confirm do we
 *     POST {id, confirm:true}. Server returns 409 without confirm:true.
 */

"use strict";

// Badge (underscore value from the API) -> presentation + action mapping.
// `action` is the POST /api/action/{name}; `confirm` flags the gated modal.
var BADGE_META = {
  UNPREPPED: {
    label: "UNPREPPED",
    cssKey: "unprepped",
    action: "prep",
    verb: "Prep",
    confirm: false,
  },
  LOCAL_NOT_PUSHED: {
    label: "LOCAL·NOT-PUSHED",
    cssKey: "local",
    action: "push",
    verb: "Push",
    confirm: false,
  },
  PUSHED_NOT_ARCHIVED: {
    label: "PUSHED·NOT-ARCHIVED",
    cssKey: "pushed",
    action: "replace",
    verb: "Replace",
    confirm: true,
  },
  RESTORED_REPLACE_AGAIN: {
    label: "RESTORED·REPLACE-AGAIN",
    cssKey: "restored",
    action: "replace",
    verb: "Replace",
    confirm: true,
  },
};

// Stable chip order.
var BADGE_ORDER = [
  "UNPREPPED",
  "LOCAL_NOT_PUSHED",
  "PUSHED_NOT_ARCHIVED",
  "RESTORED_REPLACE_AGAIN",
];

var POLL_MS = 1000;

// Active filter: badge value -> shown? Default: all on.
var filterState = {
  UNPREPPED: true,
  LOCAL_NOT_PUSHED: true,
  PUSHED_NOT_ARCHIVED: true,
  RESTORED_REPLACE_AGAIN: true,
};

// id -> card element, for filter re-application after data load.
var cardIndex = [];

// ---------------------------------------------------------------------------
// Small helpers
// ---------------------------------------------------------------------------

function $(sel, root) {
  return (root || document).querySelector(sel);
}

function meta(badge) {
  return BADGE_META[badge] || {
    label: String(badge || "UNKNOWN"),
    cssKey: "unprepped",
    action: null,
    verb: "—",
    confirm: false,
  };
}

function initialFor(id) {
  var s = String(id || "?");
  // Prefer the slug segment of a canonical id (cat-lang-year-slug); else first char.
  var parts = s.split("-");
  var seg = parts.length > 3 ? parts[3] : (parts[parts.length - 1] || s);
  var ch = (seg || s).replace(/[^A-Za-z0-9]/g, "").charAt(0);
  return (ch || "?").toUpperCase();
}

// Human-readable bytes (binary units), mirrors human_readable_size on the server.
function humanSize(bytes) {
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

function setStatus(msg, isErr) {
  var el = $("#status-line");
  el.textContent = msg || "";
  el.classList.toggle("err", !!isErr);
}

// Clipboard with execCommand fallback (localhost is a non-secure context where
// navigator.clipboard may be undefined).
function copyText(text) {
  if (navigator.clipboard && window.isSecureContext) {
    return navigator.clipboard.writeText(text).catch(function () {
      return legacyCopy(text);
    });
  }
  return Promise.resolve(legacyCopy(text));
}

function legacyCopy(text) {
  try {
    var ta = document.createElement("textarea");
    ta.value = text;
    ta.setAttribute("readonly", "");
    ta.style.position = "fixed";
    ta.style.top = "-1000px";
    ta.style.opacity = "0";
    document.body.appendChild(ta);
    ta.select();
    var ok = document.execCommand("copy");
    document.body.removeChild(ta);
    return ok;
  } catch (e) {
    return false;
  }
}

function flashCopied(btn) {
  var prev = btn.textContent;
  btn.classList.add("copied");
  btn.textContent = "✓";
  setTimeout(function () {
    btn.classList.remove("copied");
    btn.textContent = prev;
  }, 1100);
}

function wireCopy(btn, getText) {
  btn.addEventListener("click", function () {
    copyText(getText()).then(function () {
      flashCopied(btn);
    });
  });
}

// ---------------------------------------------------------------------------
// Filter chips
// ---------------------------------------------------------------------------

function buildChips(counts) {
  var row = $("#chip-row");
  row.textContent = "";
  BADGE_ORDER.forEach(function (badge) {
    var m = meta(badge);
    var chip = document.createElement("button");
    chip.type = "button";
    chip.className = "chip c-" + m.cssKey;
    chip.setAttribute("aria-pressed", filterState[badge] ? "true" : "false");
    chip.dataset.badge = badge;

    var dot = document.createElement("span");
    dot.className = "dot";
    chip.appendChild(dot);

    var lbl = document.createElement("span");
    lbl.textContent = m.label;
    chip.appendChild(lbl);

    var ct = document.createElement("span");
    ct.className = "ct";
    ct.textContent = String(counts[badge] || 0);
    chip.appendChild(ct);

    chip.addEventListener("click", function () {
      filterState[badge] = !filterState[badge];
      chip.setAttribute("aria-pressed", filterState[badge] ? "true" : "false");
      applyFilters();
    });

    row.appendChild(chip);
  });
}

function applyFilters() {
  var shown = 0;
  cardIndex.forEach(function (rec) {
    var on = !!filterState[rec.badge];
    rec.el.dataset.hidden = on ? "false" : "true";
    if (on) shown += 1;
  });
  var grid = $("#grid");
  var emptyEl = $("#filter-empty");
  if (cardIndex.length > 0 && shown === 0) {
    if (!emptyEl) {
      emptyEl = document.createElement("div");
      emptyEl.id = "filter-empty";
      emptyEl.className = "empty-state";
      var big = document.createElement("div");
      big.className = "big";
      big.textContent = "⌕";
      var p = document.createElement("div");
      p.textContent = "No items match the active filters.";
      emptyEl.appendChild(big);
      emptyEl.appendChild(p);
      grid.parentNode.insertBefore(emptyEl, grid.nextSibling);
    }
    emptyEl.style.display = "";
  } else if (emptyEl) {
    emptyEl.style.display = "none";
  }
}

// ---------------------------------------------------------------------------
// Card rendering
// ---------------------------------------------------------------------------

function buildCard(item) {
  var m = meta(item.badge);
  var tpl = $("#card-tpl");
  var node = tpl.content.firstElementChild.cloneNode(true);

  // Poster + badge (color-coded by state).
  var poster = $(".poster", node);
  poster.classList.add("p-" + m.cssKey);
  $(".initial", poster).textContent = initialFor(item.id);
  var badge = $(".badge", poster);
  badge.classList.add("b-" + m.cssKey);
  $(".badge-label", badge).textContent = m.label;

  // Id + size.
  var idEl = $(".item-id", node);
  idEl.textContent = item.id;
  if (item.guessed) {
    var g = document.createElement("span");
    g.className = "guess-tag";
    g.title = "Editable guessed id for an unprepped file";
    g.textContent = "GUESS";
    idEl.appendChild(g);
  }
  $(".item-size", node).textContent = humanSize(item.size_bytes);

  // Path.
  $(".item-path", node).textContent = item.path || "";

  // Suggested command (read-only) + Copy.
  var cmd = item.suggested_command || "";
  var cmdField = $(".field-cmd", node);
  if (cmd) {
    $(".cmd-text", cmdField).textContent = cmd;
    wireCopy($(".cmd-copy", cmdField), function () { return cmd; });
  } else {
    cmdField.style.display = "none";
  }

  // Suggested folder with editable provider id + Copy. When applies=false the
  // folder is an existing path shown informationally (input disabled).
  var sf = item.suggested_folder || {};
  var folderInput = $(".folder-input", node);
  var folderNote = $(".folder-note", node);
  folderInput.value = sf.folder || "";
  if (sf.applies) {
    folderInput.disabled = false;
    folderInput.setAttribute(
      "aria-label",
      "Editable suggested folder — replace the " +
        (sf.editable_provider_field || "provider") + " placeholder"
    );
    folderNote.textContent =
      "New-item suggestion — edit the {" +
      (sf.editable_provider_field || "provider") + "-…} id before creating.";
    folderNote.classList.add("editable");
  } else {
    folderInput.disabled = true;
    folderInput.setAttribute("aria-label", "Existing folder (read-only)");
    folderNote.textContent = "Existing folder — never renamed (informational).";
  }
  wireCopy($(".folder-copy", node), function () { return folderInput.value; });

  // Action button(s) for this badge.
  var actions = $(".card-actions", node);
  var jobPanel = $(".item-job", node);
  if (m.action) {
    var btn = document.createElement("button");
    btn.type = "button";
    btn.className = "action-btn " + (m.confirm ? "destructive" : "primary");
    btn.textContent = m.verb;
    if (m.confirm) {
      btn.title = "Destructive — confirmation required";
    }
    btn.addEventListener("click", function () {
      onActionClick(item, m, btn, jobPanel);
    });
    actions.appendChild(btn);
  } else {
    actions.style.display = "none";
  }

  cardIndex.push({ el: node, badge: item.badge, id: item.id });
  return node;
}

// ---------------------------------------------------------------------------
// Action dispatch + job polling
// ---------------------------------------------------------------------------

function bodyForAction(action, item) {
  if (action === "prep") return { id: item.id, filepath: item.path };
  if (action === "push") return { id: item.id };
  if (action === "replace") return { id: item.id, confirm: true };
  if (action === "sort") return {};
  return { id: item.id };
}

function onActionClick(item, m, btn, jobPanel) {
  if (m.confirm) {
    // Gate destructive `replace` behind the modal. POST only on confirm.
    openConfirmModal(item, function () {
      runAction(m.action, item, btn, jobPanel);
    });
    return;
  }
  runAction(m.action, item, btn, jobPanel);
}

function runAction(action, item, btn, jobPanel) {
  if (btn) btn.disabled = true;
  renderJob(jobPanel, { status: "running", name: action, output: "" }, true);

  fetch("/api/action/" + action, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(bodyForAction(action, item)),
  })
    .then(function (res) {
      if (res.status === 202) return res.json();
      // Surface any non-202 (e.g. 409 if confirm somehow missing, 404 unknown).
      return res.text().then(function (t) {
        throw new Error("HTTP " + res.status + (t ? ": " + t : ""));
      });
    })
    .then(function (data) {
      pollJob(data.job_id, jobPanel, btn, action);
    })
    .catch(function (err) {
      renderJob(
        jobPanel,
        { status: "error", name: action, output: String(err && err.message || err) },
        false
      );
      if (btn) btn.disabled = false;
    });
}

function pollJob(jobId, jobPanel, btn, action) {
  function tick() {
    fetch("/api/job/" + encodeURIComponent(jobId))
      .then(function (res) {
        if (!res.ok) {
          return res.text().then(function (t) {
            throw new Error("HTTP " + res.status + (t ? ": " + t : ""));
          });
        }
        return res.json();
      })
      .then(function (job) {
        var running = job.status === "running";
        renderJob(jobPanel, job, running);
        if (running) {
          setTimeout(tick, POLL_MS);
        } else if (btn) {
          btn.disabled = false; // re-enable on done OR error
        }
      })
      .catch(function (err) {
        renderJob(
          jobPanel,
          { status: "error", name: action, output: String(err && err.message || err) },
          false
        );
        if (btn) btn.disabled = false;
      });
  }
  tick();
}

// Render a job record into a panel. Output goes through textContent (a <pre>),
// never innerHTML. `error` status is shown faithfully — output is NOT hidden.
function renderJob(panel, job, running) {
  panel.classList.add("show");
  panel.textContent = "";

  var head = document.createElement("div");
  head.className = "job-head";

  var state = document.createElement("span");
  var status = job.status || (running ? "running" : "done");
  state.className = "job-state " + status;
  if (status === "running") {
    var sp = document.createElement("span");
    sp.className = "spinner";
    head.appendChild(sp);
    state.textContent = "Running";
  } else if (status === "error") {
    state.textContent = "✕ Error";
  } else {
    state.textContent = "✓ Done";
  }
  head.appendChild(state);

  var name = document.createElement("span");
  name.className = "job-name";
  name.textContent = job.name ? "· " + job.name : "";
  head.appendChild(name);

  panel.appendChild(head);

  var out = (job.output || "").toString();
  if (out.trim()) {
    var pre = document.createElement("pre");
    pre.className = "job-output";
    pre.textContent = out; // XSS-safe rendering of captured stdout.
    panel.appendChild(pre);
  } else if (status === "running") {
    var pre2 = document.createElement("pre");
    pre2.className = "job-output";
    pre2.textContent = "Waiting for output…";
    panel.appendChild(pre2);
  }
}

// ---------------------------------------------------------------------------
// Confirm modal (shared, for `replace`)
// ---------------------------------------------------------------------------

var modalOnConfirm = null;

function openConfirmModal(item, onConfirm) {
  modalOnConfirm = onConfirm;
  var modal = $("#modal");
  var target = $("#modal-target");
  target.textContent = "";
  var idline = document.createElement("div");
  var b = document.createElement("b");
  b.textContent = item.id;
  idline.appendChild(b);
  target.appendChild(idline);
  var pathline = document.createElement("div");
  pathline.textContent = item.path || "";
  pathline.style.marginTop = "4px";
  pathline.style.opacity = "0.8";
  target.appendChild(pathline);

  modal.hidden = false;
  modal.classList.add("show");
  // Focus the safe default (Cancel).
  $("#modal-cancel").focus();
}

function closeModal() {
  var modal = $("#modal");
  modal.classList.remove("show");
  modal.hidden = true;
  modalOnConfirm = null;
}

function wireModal() {
  $("#modal-cancel").addEventListener("click", closeModal);
  $("#modal-confirm").addEventListener("click", function () {
    var fn = modalOnConfirm;
    closeModal();
    if (fn) fn();
  });
  // Backdrop click cancels (but not clicks inside the dialog).
  $("#modal").addEventListener("click", function (e) {
    if (e.target === $("#modal")) closeModal();
  });
  // Esc cancels.
  document.addEventListener("keydown", function (e) {
    if (e.key === "Escape" && !$("#modal").hidden) closeModal();
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
// Load + render
// ---------------------------------------------------------------------------

function render(data) {
  var items = (data && data.items) || [];

  // Hero total.
  var totalEl = $("#reclaim-total");
  var human =
    data && (data.total_reclaimable_human || humanSize(data.total_reclaimable_bytes));
  totalEl.textContent = human || "0 B";
  totalEl.classList.toggle("empty", !items.length);
  $("#reclaim-count").textContent =
    items.length === 1 ? "1 reclaimable item" : items.length + " reclaimable items";

  // Counts per badge for chips.
  var counts = {};
  items.forEach(function (it) {
    counts[it.badge] = (counts[it.badge] || 0) + 1;
  });
  buildChips(counts);

  // Cards.
  cardIndex = [];
  var grid = $("#grid");
  grid.textContent = "";
  if (!items.length) {
    var empty = document.createElement("div");
    empty.className = "empty-state";
    var big = document.createElement("div");
    big.className = "big";
    big.textContent = "✓";
    var p = document.createElement("div");
    p.textContent = "Nothing reclaimable. Local disk is clean.";
    empty.appendChild(big);
    empty.appendChild(p);
    grid.appendChild(empty);
  } else {
    var frag = document.createDocumentFragment();
    items.forEach(function (it) {
      frag.appendChild(buildCard(it));
    });
    grid.appendChild(frag);
  }

  applyFilters();
  setStatus("");
}

function load() {
  setStatus("Scanning reclaimable disk…");
  fetch("/api/reclaim")
    .then(function (res) {
      if (!res.ok) throw new Error("HTTP " + res.status);
      return res.json();
    })
    .then(render)
    .catch(function (err) {
      setStatus("Failed to load /api/reclaim — " + (err && err.message || err), true);
    });
}

function init() {
  wireModal();
  wireSort();
  load();
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", init);
} else {
  init();
}
