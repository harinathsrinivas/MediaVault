/* MediaVault Console — Candidate A: card rendering + action/job flow.
 *
 * Preserves the existing console's working patterns verbatim:
 *   - card template clone (id / size / path, suggested command +Copy, suggested
 *     folder with editable provider id +Copy, action button[s]);
 *   - POST /api/action/{name} (202 {job_id}) -> poll GET /api/job/{job_id} until
 *     done|error, render output through textContent (NEVER innerHTML);
 *   - `replace` gated by the shared confirm modal (POST only on confirm).
 *
 * New for IMP-E14 Phase 1: the ARCHIVED sub-view renders a card with a POSTER
 * SLOT (gradient + initial placeholder; real posters land Phase 5) and a
 * DISABLED "Fetch & Restore — coming next" affordance (the working fetch button
 * arrives Phase 2). The poster slot element exists in the template now so later
 * phases can fill it.
 */

"use strict";

// state -> presentation + action mapping. cssKey selects the BADGE_META palette
// classes reused from the original styling. ARCHIVED has no live action in
// Phase 1 (fetch is disabled).
export var STATE_META = {
  UNPREPPED: { label: "UNPREPPED", cssKey: "unprepped", action: "prep", verb: "Prep", confirm: false },
  LOCAL_NOT_PUSHED: { label: "LOCAL·NOT-PUSHED", cssKey: "local", action: "push", verb: "Push", confirm: false },
  PUSHED_NOT_ARCHIVED: { label: "PUSHED·NOT-ARCHIVED", cssKey: "pushed", action: "replace", verb: "Replace", confirm: true },
  RESTORED_REPLACE_AGAIN: { label: "RESTORED·REPLACE-AGAIN", cssKey: "restored", action: "replace", verb: "Replace", confirm: true },
  ARCHIVED: { label: "ARCHIVED", cssKey: "archived", action: null, verb: "—", confirm: false },
};

var POLL_MS = 1000;

// Callback invoked after a job reaches a terminal (done|error) state, so the app
// can schedule a debounced data refresh. Set by the app via setRefreshHook().
var onJobTerminal = null;
export function setRefreshHook(fn) {
  onJobTerminal = fn;
}

function meta(state) {
  return STATE_META[state] || { label: String(state || "UNKNOWN"), cssKey: "unprepped", action: null, verb: "—", confirm: false };
}

function $(sel, root) {
  return (root || document).querySelector(sel);
}

function initialFor(id) {
  var s = String(id || "?");
  var parts = s.split("-");
  var seg = parts.length > 3 ? parts[3] : (parts[parts.length - 1] || s);
  var ch = (seg || s).replace(/[^A-Za-z0-9]/g, "").charAt(0);
  return (ch || "?").toUpperCase();
}

// Human-readable bytes (binary), mirrors human_readable_size on the server.
export function humanSize(bytes) {
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

// --------------------------------------------------------------------------
// Clipboard (execCommand fallback for non-secure localhost contexts)
// --------------------------------------------------------------------------

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

// --------------------------------------------------------------------------
// Card builder
// --------------------------------------------------------------------------

export function buildCard(item) {
  var m = meta(item.state);
  var tpl = $("#card-tpl");
  var node = tpl.content.firstElementChild.cloneNode(true);
  var isArchived = item.state === "ARCHIVED";

  // Poster slot + badge (color-coded by state). The slot stays in the DOM for
  // every state; Phase 5 fills it with a real <img> for archived items.
  var poster = $(".poster", node);
  poster.classList.add("p-" + m.cssKey);
  $(".initial", poster).textContent = initialFor(item.id);
  var badge = $(".badge", poster);
  badge.classList.add("b-" + m.cssKey);
  $(".badge-label", badge).textContent = m.label;

  // Id + size, with the GUESS tag for editable guessed (unprepped) ids.
  var idEl = $(".item-id", node);
  idEl.textContent = item.id; // textContent — XSS-safe
  if (item.guessed) {
    var g = document.createElement("span");
    g.className = "guess-tag";
    g.title = "Editable guessed id for an unprepped file";
    g.textContent = "GUESS";
    idEl.appendChild(g);
  }
  $(".item-size", node).textContent = humanSize(item.size_bytes);

  // Path (textContent — XSS-safe).
  $(".item-path", node).textContent = item.path || "";

  // Suggested command (read-only) + Copy. Absent on archived rows -> hide field.
  var cmd = item.suggested_command || "";
  var cmdField = $(".field-cmd", node);
  if (cmd) {
    $(".cmd-text", cmdField).textContent = cmd;
    wireCopy($(".cmd-copy", cmdField), function () { return cmd; });
  } else {
    cmdField.style.display = "none";
  }

  // Suggested folder with editable provider id + Copy.
  var sf = item.suggested_folder || null;
  var folderField = $(".field-folder", node);
  if (sf) {
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
  } else {
    folderField.style.display = "none";
  }

  // Actions.
  var actions = $(".card-actions", node);
  var jobPanel = $(".item-job", node);
  if (isArchived) {
    // Phase 1: disabled placeholder. The working fetch button arrives Phase 2.
    poster.classList.add("archived");
    var fetchBtn = document.createElement("button");
    fetchBtn.type = "button";
    fetchBtn.className = "action-btn fetch-soon";
    fetchBtn.disabled = true;
    fetchBtn.textContent = "Fetch & Restore — coming next";
    fetchBtn.title = "Fetch & Restore lands in Phase 2";
    actions.appendChild(fetchBtn);
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

// --------------------------------------------------------------------------
// Action dispatch + job polling (unchanged flow)
// --------------------------------------------------------------------------

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

function actionHttpError(status, detail) {
  if (status === 409) return "Refused (409): this action needs explicit confirmation.";
  if (status === 404) return "Unknown action (404): the server does not expose this action.";
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
      if (res.status === 202) return res.json();
      return res.text().then(function (t) {
        var detail = t;
        try {
          var parsed = JSON.parse(t);
          if (parsed && parsed.detail) detail = parsed.detail;
        } catch (e) {
          /* non-JSON body — keep raw text */
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
          if (btn) btn.disabled = false;
          if (onJobTerminal) onJobTerminal();
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

// Render a job record. Output goes through textContent (a <pre>), never
// innerHTML. `error` is shown faithfully — output is NOT hidden.
export function renderJob(panel, job, running) {
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
    pre.textContent = out; // XSS-safe captured stdout
    panel.appendChild(pre);
  } else if (status === "running") {
    var pre2 = document.createElement("pre");
    pre2.className = "job-output";
    pre2.textContent = "Waiting for output…";
    panel.appendChild(pre2);
  }
}

// --------------------------------------------------------------------------
// Confirm modal (shared, for destructive `replace`)
// --------------------------------------------------------------------------

var modalOnConfirm = null;

function openConfirmModal(item, onConfirm) {
  modalOnConfirm = onConfirm;
  var modal = $("#modal");
  var target = $("#modal-target");
  target.textContent = "";
  var idline = document.createElement("div");
  var b = document.createElement("b");
  b.textContent = item.id; // textContent — XSS-safe
  idline.appendChild(b);
  target.appendChild(idline);
  var pathline = document.createElement("div");
  pathline.textContent = item.path || "";
  pathline.style.marginTop = "4px";
  pathline.style.opacity = "0.8";
  target.appendChild(pathline);

  modal.hidden = false;
  modal.classList.add("show");
  $("#modal-cancel").focus();
}

function closeModal() {
  var modal = $("#modal");
  modal.classList.remove("show");
  modal.hidden = true;
  modalOnConfirm = null;
}

export function wireModal() {
  $("#modal-cancel").addEventListener("click", closeModal);
  $("#modal-confirm").addEventListener("click", function () {
    var fn = modalOnConfirm;
    closeModal();
    if (fn) fn();
  });
  $("#modal").addEventListener("click", function (e) {
    if (e.target === $("#modal")) closeModal();
  });
  document.addEventListener("keydown", function (e) {
    if (e.key === "Escape" && !$("#modal").hidden) closeModal();
  });
}
