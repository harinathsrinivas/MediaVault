/* MediaVault Console — card rendering, copy, action dispatch + job polling.
 * (IMP-E14, Candidate B). ES module; `node --check card.js` covers it.
 *
 * This preserves the existing reclaim-console card EXACTLY for the non-archived
 * states: id / size / path, suggested command (+Copy), suggested folder with
 * editable provider id (+Copy), action button(s) gated by the confirm modal for
 * `replace`, the POST /api/action/* -> poll /api/job/{id} flow, and XSS-safe
 * textContent-only rendering of captured stdout.
 *
 * New for IMP-E14: a poster SLOT element (gradient + initial placeholder; real
 * posters land Phase 5) and, for the ARCHIVED sub-view, a disabled
 * "Fetch & Restore — coming next" affordance (the working button arrives in
 * Phase 2). The poster slot exists in the template now so Phase 2/5 can fill it.
 */

"use strict";

import { metaFor, humanSize } from "./data.js";
import { openConfirmModal } from "./modal.js";

var POLL_MS = 1000;

// After a terminal job we re-fetch the model so badges reflect the new state.
// We wait this long first so the just-shown job result stays visible briefly
// instead of being yanked away the instant the job ends.
var REFRESH_AFTER_JOB_MS = 2500;

// Callback invoked (debounced) after any terminal job so app.js can reload the
// model + repaint. Wired once via setRefreshHandler().
var _refreshHandler = null;
var _refreshTimer = null;

export function setRefreshHandler(fn) {
  _refreshHandler = fn;
}

function scheduleRefresh() {
  if (!_refreshHandler) return;
  if (_refreshTimer) clearTimeout(_refreshTimer);
  _refreshTimer = setTimeout(function () {
    _refreshTimer = null;
    _refreshHandler();
  }, REFRESH_AFTER_JOB_MS);
}

// ---------------------------------------------------------------------------
// Small helpers
// ---------------------------------------------------------------------------

function $(sel, root) {
  return (root || document).querySelector(sel);
}

function initialFor(id) {
  var s = String(id || "?");
  var parts = s.split("-");
  var seg = parts.length > 3 ? parts[3] : parts[parts.length - 1] || s;
  var ch = (seg || s).replace(/[^A-Za-z0-9]/g, "").charAt(0);
  return (ch || "?").toUpperCase();
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
// Card rendering
// ---------------------------------------------------------------------------

// Build one card element for a merged render row. `item.state` selects the
// presentation/action via metaFor(). ARCHIVED renders the poster-forward,
// read-only variant; every other state renders the familiar reclaim card.
export function buildCard(item) {
  var m = metaFor(item.state);
  var isArchived = item.state === "ARCHIVED";
  var tpl = $("#card-tpl");
  var node = tpl.content.firstElementChild.cloneNode(true);
  node.dataset.state = item.state;
  if (isArchived) node.classList.add("archived");

  // Poster slot (gradient + big initial placeholder). Phase 5 fills a real
  // image; the slot must exist now so later phases have a mount point.
  var poster = $(".poster", node);
  poster.classList.add("p-" + m.cssKey);
  $(".initial", poster).textContent = initialFor(item.id);

  // Badge (color-coded by state).
  var badge = $(".badge", poster);
  badge.classList.add("b-" + m.cssKey);
  $(".badge-label", badge).textContent = m.label;

  // Id + size.
  var idEl = $(".item-id", node);
  idEl.textContent = item.title && item.title !== item.id ? item.title : item.id;
  if (item.guessed) {
    var g = document.createElement("span");
    g.className = "guess-tag";
    g.title = "Editable guessed id for an unprepped file";
    g.textContent = "GUESS";
    idEl.appendChild(g);
  }
  $(".item-size", node).textContent = humanSize(item.size_bytes);

  // Secondary id line (the canonical id, when the title differs from it).
  var subId = $(".item-subid", node);
  if (item.title && item.title !== item.id) {
    subId.textContent = item.id;
  } else {
    subId.style.display = "none";
  }

  // Path.
  $(".item-path", node).textContent = item.path || "";

  // Suggested command (read-only) + Copy. Absent for ARCHIVED and any row the
  // reclaim feed didn't enrich.
  var cmd = item.suggested_command || "";
  var cmdField = $(".field-cmd", node);
  if (cmd) {
    $(".cmd-text", cmdField).textContent = cmd;
    wireCopy($(".cmd-copy", cmdField), function () {
      return cmd;
    });
  } else {
    cmdField.style.display = "none";
  }

  // Suggested folder with editable provider id + Copy.
  var sf = item.suggested_folder || null;
  var folderField = $(".field-folder", node);
  if (sf && (sf.folder || sf.applies)) {
    var folderInput = $(".folder-input", node);
    var folderNote = $(".folder-note", node);
    folderInput.value = sf.folder || "";
    if (sf.applies) {
      folderInput.disabled = false;
      folderInput.setAttribute(
        "aria-label",
        "Editable suggested folder — replace the " +
          (sf.editable_provider_field || "provider") +
          " placeholder"
      );
      folderNote.textContent =
        "New-item suggestion — edit the {" +
        (sf.editable_provider_field || "provider") +
        "-…} id before creating.";
      folderNote.classList.add("editable");
    } else {
      folderInput.disabled = true;
      folderInput.setAttribute("aria-label", "Existing folder (read-only)");
      folderNote.textContent = "Existing folder — never renamed (informational).";
    }
    wireCopy($(".folder-copy", node), function () {
      return folderInput.value;
    });
  } else {
    folderField.style.display = "none";
  }

  // Action zone.
  var actions = $(".card-actions", node);
  var jobPanel = $(".item-job", node);

  if (isArchived) {
    // Phase 1: disabled "coming next" stub. Phase 2 replaces this with the real
    // Fetch & Restore action against /api/action/fetch_restore.
    var stub = document.createElement("button");
    stub.type = "button";
    stub.className = "action-btn coming-soon";
    stub.disabled = true;
    stub.textContent = "Fetch & Restore — coming next";
    stub.title = "Cloud fetch + restore lands in Phase 2";
    actions.appendChild(stub);
  } else if (m.action) {
    var btn = document.createElement("button");
    btn.type = "button";
    btn.className = "action-btn " + (m.confirm ? "destructive" : "primary");
    btn.textContent = m.verb;
    if (m.confirm) btn.title = "Destructive — confirmation required";
    btn.addEventListener("click", function () {
      onActionClick(item, m, btn, jobPanel);
    });
    actions.appendChild(btn);
  } else {
    actions.style.display = "none";
  }

  return node;
}

// ---------------------------------------------------------------------------
// Action dispatch + job polling (preserved from the reclaim console)
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
    openConfirmModal(item, function () {
      runAction(m.action, item, btn, jobPanel);
    });
    return;
  }
  runAction(m.action, item, btn, jobPanel);
}

// Turn a non-202 action response into a readable, human-facing error message.
function actionHttpError(status, detail) {
  if (status === 409) {
    return "Refused (409): this action needs explicit confirmation.";
  }
  if (status === 404) {
    return "Unknown action (404): the server does not expose this action.";
  }
  var base = "Request failed (HTTP " + status + ")";
  return detail ? base + ": " + detail : base + ".";
}

export function runAction(action, item, btn, jobPanel) {
  if (btn) btn.disabled = true;
  renderJob(jobPanel, { status: "running", name: action, output: "" }, true);

  fetch("/api/action/" + action, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(bodyForAction(action, item)),
  })
    .then(function (res) {
      // ONLY 202 returns {job_id}. Handle every other status explicitly so we
      // never read a job_id that isn't there.
      if (res.status === 202) return res.json();
      return res.text().then(function (t) {
        var detail = t;
        try {
          var parsed = JSON.parse(t);
          if (parsed && parsed.detail) detail = parsed.detail;
        } catch (e) {
          /* non-JSON body — keep the raw text */
        }
        throw new Error(actionHttpError(res.status, detail));
      });
    })
    .then(function (data) {
      pollJob(data.job_id, jobPanel, btn, action);
    })
    .catch(function (err) {
      renderJob(
        jobPanel,
        { status: "error", name: action, output: String((err && err.message) || err) },
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
        } else {
          if (btn) btn.disabled = false; // re-enable on done OR error
          scheduleRefresh();
        }
      })
      .catch(function (err) {
        renderJob(
          jobPanel,
          { status: "error", name: action, output: String((err && err.message) || err) },
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
