/* MediaVault Console — full-screen terminal overlay (IMP-E14 Phase 2 follow-up).
 * ES module; `node --check terminal.js` covers it on its own.
 *
 * WHAT THIS IS
 *   A per-job inline output panel (.item-job / #sort-job-panel) renders the live
 *   fetch output + progress. This module adds an EXPANDED, full-screen mirror of
 *   that same job: a header with the equivalent CLI command (selectable mono), a
 *   live progress readout (done/total + %), and the full streaming output in a
 *   scrollable <pre> that auto-scrolls to the bottom as it grows.
 *
 * SINGLE POLL, NO DOUBLE-SUBMIT (the load-bearing constraint)
 *   The overlay starts NO poll of its own. card.js funnels EVERY job-state update
 *   through renderJob(panel, job, running); after rendering the inline panel it
 *   calls notifyJob(panel, job, running, command) here. If the overlay is open and
 *   bound to THAT panel, it re-renders from the same job object the inline panel
 *   just received. So the expanded view is a pure subscriber to the existing poll
 *   — opening/closing it never issues another /api/action or /api/job request.
 *
 * XSS-safety: output + command + every label go through textContent only. This
 * module never touches innerHTML. The backdrop/dialog skeleton is created with
 * createElement; nothing is interpolated from data.
 *
 * Close affordances mirror the confirm modal: X button, Esc, backdrop click.
 */

"use strict";

// The panel the overlay is currently mirroring (null when closed). Identity
// comparison against the panel passed to notifyJob() decides whether a given
// job tick should also refresh the overlay.
var boundPanel = null;

// Cached DOM refs for the singleton overlay (built lazily on first open).
var els = null;

function buildOverlay() {
  var backdrop = document.createElement("div");
  backdrop.className = "term-backdrop";
  backdrop.setAttribute("role", "dialog");
  backdrop.setAttribute("aria-modal", "true");
  backdrop.setAttribute("aria-label", "Job terminal");
  backdrop.hidden = true;

  var win = document.createElement("div");
  win.className = "term-window";

  // Header: status pill + the equivalent command (selectable) + close button.
  var head = document.createElement("div");
  head.className = "term-head";

  var state = document.createElement("span");
  state.className = "term-state";

  var progress = document.createElement("span");
  progress.className = "term-progress";

  var cmdWrap = document.createElement("div");
  cmdWrap.className = "term-cmd-wrap";
  var cmdLabel = document.createElement("span");
  cmdLabel.className = "term-cmd-label";
  cmdLabel.textContent = "Equivalent command";
  var cmd = document.createElement("code");
  cmd.className = "term-cmd";
  cmdWrap.appendChild(cmdLabel);
  cmdWrap.appendChild(cmd);

  var close = document.createElement("button");
  close.type = "button";
  close.className = "term-close";
  close.setAttribute("aria-label", "Close terminal");
  close.textContent = "✕"; // ✕

  head.appendChild(state);
  head.appendChild(progress);
  head.appendChild(cmdWrap);
  head.appendChild(close);

  // Body: the full streaming output.
  var body = document.createElement("pre");
  body.className = "term-output";

  win.appendChild(head);
  win.appendChild(body);
  backdrop.appendChild(win);
  document.body.appendChild(backdrop);

  close.addEventListener("click", closeTerminal);
  backdrop.addEventListener("click", function (e) {
    if (e.target === backdrop) closeTerminal();
  });

  els = {
    backdrop: backdrop,
    win: win,
    state: state,
    progress: progress,
    cmd: cmd,
    body: body,
    close: close,
  };
  return els;
}

// Esc closes — registered once, guarded by `boundPanel` so it is inert when the
// overlay is closed (and never collides with the confirm modal's own handler).
var _escWired = false;
function wireEscOnce() {
  if (_escWired) return;
  _escWired = true;
  document.addEventListener("keydown", function (e) {
    if (e.key === "Escape" && boundPanel) closeTerminal();
  });
}

// Render the overlay from a job record. `cmd` is the equivalent CLI command
// string; it changes rarely so we only write it when provided.
function paint(job, cmd) {
  if (!els) return;
  var status = (job && job.status) || "running";

  els.state.className = "term-state " + status;
  if (status === "running") {
    els.state.textContent = "● Running";
  } else if (status === "error") {
    els.state.textContent = "✕ Error";
  } else {
    els.state.textContent = "✓ Done";
  }

  // Progress readout (done/total + %). Only shown when a total is known.
  var p = (job && job.progress) || null;
  if (p && Number(p.total) > 0) {
    var done = Number(p.done) || 0;
    var total = Number(p.total);
    if (done > total) done = total;
    var pct = Math.round((done / total) * 100);
    els.progress.textContent = done + "/" + total + " · " + pct + "%";
    els.progress.hidden = false;
  } else {
    els.progress.textContent = "";
    els.progress.hidden = true;
  }

  if (typeof cmd === "string" && cmd) els.cmd.textContent = cmd;

  // Output: replace wholesale (textContent — XSS-safe) and keep pinned to the
  // bottom IF the user was already at the bottom (don't fight a manual scroll-up).
  var atBottom =
    els.body.scrollHeight - els.body.scrollTop - els.body.clientHeight < 24;
  var out = ((job && job.output) || "").toString();
  els.body.textContent = out || (status === "running" ? "Waiting for output…" : "");
  if (atBottom) els.body.scrollTop = els.body.scrollHeight;
}

// Open the overlay mirroring `panel`. Reads the latest job + command that
// renderJob() stashed on the panel element, paints once, then subscribes: every
// later notifyJob(panel, …) for THIS panel repaints. No poll is started here.
export function openTerminal(panel) {
  if (!panel) return;
  if (!els) buildOverlay();
  wireEscOnce();
  boundPanel = panel;

  var job = panel._lastJob || { status: "running", output: "" };
  paint(job, panel._jobCommand || "");

  els.backdrop.hidden = false;
  els.backdrop.classList.add("show");
  // Jump to the bottom on open so the freshest output is visible immediately.
  els.body.scrollTop = els.body.scrollHeight;
  els.close.focus();
}

export function closeTerminal() {
  boundPanel = null;
  if (!els) return;
  els.backdrop.classList.remove("show");
  els.backdrop.hidden = true;
}

// Called by card.js after EVERY inline renderJob(). If the overlay is open and
// bound to this exact panel, repaint it from the same job object. This is the
// subscription that keeps the expanded view live off the single existing poll.
export function notifyJob(panel, job, command) {
  if (panel !== boundPanel) return;
  paint(job, command);
}

// True while the overlay is mirroring the given panel (used to keep the inline
// expand button's pressed state in sync, if desired). Currently informational.
export function isOpenFor(panel) {
  return boundPanel === panel;
}
