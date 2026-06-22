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
 *
 * Phase 2 (IMP-E14, CANDIDATE A — CSS conic-gradient ring): the Archived card's
 * stub is replaced by a WORKING "Fetch & Restore" button that POSTs to
 * /api/action/fetch_restore and polls /api/job/{id}. Each poll reads the job's
 * `progress {done,total}` (chunk units) and drives a GROWING conic-gradient RING
 * around the card via a single CSS custom property --progress (0..1), smoothed
 * between discrete chunk ticks by a CSS transition. On terminal `done` the ring
 * snaps to a glowing closed loop and (after REFRESH_AFTER_JOB_MS) the model is
 * re-fetched so the card flips out of Archived into "Fetched·not-archived". A
 * numeric "k/N chunks · %" label rides alongside for legibility without motion.
 * driveBorder() is the SINGLE faithful code path; the ?demo preview feeds it
 * synthetic numbers (see runDemo()).
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
    // Phase 2 (Candidate A): the WORKING Fetch & Restore action. Per the Open
    // Decision we surface ONLY Fetch & Restore (no download-only button). The
    // conic-gradient ring overlay + the numeric progress label are created here
    // so the live ?demo can target the first archived card after render.
    buildFetchAffordance(item, node, actions, jobPanel);
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
// Fetch & Restore (ARCHIVED) — CANDIDATE A: CSS conic-gradient progress ring
// ---------------------------------------------------------------------------

// Build the Archived card's working action: the Fetch & Restore button, the
// conic-gradient ring overlay element, and the numeric progress label. The ring
// + label exist from build time (hidden until a fetch runs) so the ?demo
// preview can drive the REAL code path on the first archived card.
function buildFetchAffordance(item, node, actions, jobPanel) {
  // Ring overlay: a single child the CSS paints as a hollow conic-gradient ring
  // driven by --progress. pointer-events:none keeps it click-through.
  var ring = document.createElement("div");
  ring.className = "fetch-ring";
  ring.setAttribute("aria-hidden", "true"); // numeric label is the a11y channel
  node.appendChild(ring);

  // Numeric progress label (legible without motion). Two spans: a % chip and a
  // "k/N chunks" detail. aria-live=polite so a screen reader hears advances.
  var prog = document.createElement("div");
  prog.className = "fetch-progress";
  prog.setAttribute("role", "status");
  prog.setAttribute("aria-live", "polite");
  var pct = document.createElement("span");
  pct.className = "pct";
  pct.textContent = "0%";
  var chunks = document.createElement("span");
  chunks.className = "chunks";
  chunks.textContent = "starting…";
  prog.appendChild(pct);
  prog.appendChild(chunks);

  // Button.
  var btn = document.createElement("button");
  btn.type = "button";
  btn.className = "action-btn fetch";
  btn.textContent = "Fetch & Restore";
  btn.title = "Fetch from cloud and restore locally";
  btn.addEventListener("click", function () {
    runFetchRestore(item, btn, node, prog, jobPanel);
  });

  actions.appendChild(btn);
  actions.appendChild(prog);

  // Stash the handles the demo needs without a separate lookup.
  node._fetchBtn = btn;
  node._fetchProg = prog;
  node._fetchJobPanel = jobPanel;
}

// Fraction in [0,1] from a job progress dict. total===0 is "indeterminate" — we
// return 0 so the ring sits at its origin rather than reading a bogus value.
function fractionOf(progress) {
  if (!progress) return 0;
  var total = Number(progress.total) || 0;
  if (total <= 0) return 0;
  var done = Number(progress.done) || 0;
  var f = done / total;
  if (f < 0) return 0;
  if (f > 1) return 1; // server already clamps, but guard the UI regardless
  return f;
}

// THE SINGLE FAITHFUL BORDER DRIVER. Sets --progress on the card (the conic
// sweep follows it, tweened by the CSS transition) and updates the numeric
// label. `phase` is "running" | "done". Called identically by real polls and by
// the ?demo preview — only the numbers differ.
//   node     : the .card element (carries --progress + state classes)
//   prog     : the .fetch-progress label element
//   progress : {done,total} (chunk units); total===0 -> indeterminate
//   phase    : "running" snaps nothing; "done" closes the loop + glows
function driveBorder(node, prog, progress, phase) {
  var total = Number(progress && progress.total) || 0;
  var done = Number(progress && progress.done) || 0;
  // A "done" phase ALWAYS closes the loop to a full ring — even when the server
  // degraded progress to a status-only {1,1} (push/replace style, no chunk
  // markers) or to {0,0}: completion means the border is full, full stop.
  var frac = phase === "done" ? 1 : fractionOf(progress);

  // Drive the ring. Setting the custom property is all the conic needs; the CSS
  // transition on --progress does the buttery smoothing between ticks.
  node.style.setProperty("--progress", String(frac));

  // State classes flip the ring's visibility + the complete glow.
  if (phase === "done") {
    node.classList.remove("fetching");
    node.classList.add("fetch-complete");
  } else {
    node.classList.remove("fetch-complete");
    node.classList.add("fetching");
  }

  // Numeric label. Percent is rounded for the chip; chunks show the raw count.
  var pctEl = prog.querySelector(".pct");
  var chunkEl = prog.querySelector(".chunks");
  if (phase === "done") {
    var dTotal = total > 0 ? total : done > 0 ? done : 1;
    pctEl.textContent = "100%";
    chunkEl.textContent = dTotal + "/" + dTotal + " chunks · done";
  } else if (total > 0) {
    pctEl.textContent = Math.round(frac * 100) + "%";
    chunkEl.textContent = done + "/" + total + " chunks";
  } else {
    // Indeterminate: no chunk markers yet (e.g. before the first PROCESSING
    // line, or a non-split single file). Be honest rather than fake a number.
    pctEl.textContent = "…";
    chunkEl.textContent = done > 0 ? done + " chunks" : "preparing…";
  }
}

// Clear all ring/label state from a card (used on error so the card returns to
// a clean Archived presentation before the button is re-enabled).
function clearBorder(node) {
  node.classList.remove("fetching", "fetch-complete");
  node.style.removeProperty("--progress");
}

// POST /api/action/fetch_restore {id, options:{episodes}} and poll. Default =
// whole entry (episodes omitted). Disables the button for the job's lifetime to
// prevent a double-submit; the poll re-enables on terminal done OR error.
export function runFetchRestore(item, btn, node, prog, jobPanel, episodes) {
  if (btn.disabled) return; // guard against a double-click before disable lands
  btn.disabled = true;
  btn.textContent = "Fetching…";

  // Show the ring immediately at 0 (indeterminate) so the user gets instant
  // feedback before the first poll returns real chunk counts.
  driveBorder(node, prog, { done: 0, total: 0 }, "running");
  renderJob(jobPanel, { status: "running", name: "fetch_restore", output: "" }, true);

  var body = { id: item.id, options: {} };
  if (episodes) body.options.episodes = episodes; // omit => whole entry

  fetch("/api/action/fetch_restore", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
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
      pollFetchJob(data.job_id, node, prog, btn, jobPanel);
    })
    .catch(function (err) {
      failFetch(node, prog, btn, jobPanel, String((err && err.message) || err));
    });
}

// Poll a fetch job, driving the ring from job.progress each tick. On terminal
// `done`: snap the ring to a glowing closed loop, show the final output, and
// schedule the model refresh that flips the card to Fetched·not-archived. On
// `error`: surface the captured output faithfully (it may carry a resume hint),
// clear the ring, re-enable the button, leave the card in Archived.
function pollFetchJob(jobId, node, prog, btn, jobPanel) {
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
        var status = job.status;
        if (status === "running") {
          driveBorder(node, prog, job.progress, "running");
          renderJob(jobPanel, job, true);
          setTimeout(tick, POLL_MS);
          return;
        }
        if (status === "done") {
          driveBorder(node, prog, job.progress, "done");
          renderJob(jobPanel, job, false);
          btn.disabled = false;
          btn.textContent = "Fetch & Restore";
          // Auto-flip: after the usual delay the model reloads and the card
          // leaves Archived for Fetched·not-archived (state RESTORED_REPLACE_-
          // AGAIN). scheduleRefresh is debounced so concurrent fetches coalesce.
          scheduleRefresh();
          return;
        }
        // status === "error" (or any non-terminal-unknown -> treat as error).
        failFetch(node, prog, btn, jobPanel, (job.output || "").toString(), job);
      })
      .catch(function (err) {
        failFetch(node, prog, btn, jobPanel, String((err && err.message) || err));
      });
  }
  tick();
}

// Common error landing for a fetch: faithful output, ring cleared, button back.
// The card stays in Archived (NO refresh scheduled) so the user can retry.
function failFetch(node, prog, btn, jobPanel, output, job) {
  clearBorder(node);
  renderJob(
    jobPanel,
    { status: "error", name: "fetch_restore", output: output, progress: job && job.progress },
    false
  );
  btn.disabled = false;
  btn.textContent = "Fetch & Restore";
}

// ?demo / #demo live preview (IDENTICAL contract in both candidates). Drives the
// REAL driveBorder() code path with SYNTHETIC progress on the first archived
// card — NO backend call, NO real fetch. total=8, done ticks 1->8 every ~600ms,
// then status "done" -> the glow finish. A "DEMO" tag marks the card.
export function runDemo() {
  var card = document.querySelector(".card.archived");
  if (!card || !card._fetchProg) return false;
  var prog = card._fetchProg;
  var btn = card._fetchBtn;

  // Mark the card so it's obvious this is a synthetic preview, and lock the
  // button (it would hit the backend for real).
  if (btn) {
    btn.disabled = true;
    btn.textContent = "Demo…";
  }
  if (!card.querySelector(".demo-tag")) {
    var tag = document.createElement("span");
    tag.className = "demo-tag";
    tag.textContent = "Demo";
    card.appendChild(tag);
  }

  var TOTAL = 8;
  var STEP_MS = 600;
  var done = 0;
  driveBorder(card, prog, { done: 0, total: TOTAL }, "running");

  function step() {
    done += 1;
    if (done <= TOTAL) {
      driveBorder(card, prog, { done: done, total: TOTAL }, "running");
      setTimeout(step, STEP_MS);
    } else {
      // Finish: snap to the glowing closed loop.
      driveBorder(card, prog, { done: TOTAL, total: TOTAL }, "done");
      if (btn) btn.textContent = "Demo complete";
    }
  }
  setTimeout(step, STEP_MS);
  return true;
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
