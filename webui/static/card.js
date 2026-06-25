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
 * posters land Phase 5).
 *
 * Phase 2 (Candidate B): the ARCHIVED card's disabled "coming next" stub is
 * replaced by a WORKING "Fetch & Restore" button. It POSTs
 * /api/action/fetch_restore {id, options:{episodes}} (default = whole entry,
 * episodes omitted), then polls the job and drives a GROWING SVG ring around the
 * card (see ring.js) from job.progress {done,total}. On terminal "done" the ring
 * snaps to a glowing closed loop and, after REFRESH_AFTER_JOB_MS, the model is
 * re-fetched so the card leaves Archived and reappears under Fetched·not-
 * archived. On "error" the captured output is shown faithfully and the button
 * re-enables. A ?demo / #demo URL drives the SAME ring with synthetic progress.
 */

"use strict";

import { metaFor, humanSize, openFolder } from "./data.js";
import { authFetch } from "./auth.js";
import { openConfirmModal } from "./modal.js";
import { createRing } from "./ring.js";
import { displayTitle } from "./title.js";
import { openTerminal, notifyJob } from "./terminal.js";

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

// The folder that contains a leaf = the directory part of its joined path. The
// item `path` is folder_path joined with the filename, so drop the final segment.
// Tolerates both Windows backslashes (the real case here) and forward slashes.
function folderOfPath(p) {
  var s = String(p || "");
  if (!s) return "";
  var idx = Math.max(s.lastIndexOf("\\"), s.lastIndexOf("/"));
  return idx > 0 ? s.slice(0, idx) : s;
}

// Add the small top-right open-folder button to a card's poster. XSS-safe (inline
// glyph + textContent only). Click is isolated (preventDefault/stopPropagation) so
// it never toggles a grouped-view disclosure or trips another card control.
function addOpenFolderButton(poster, item) {
  var folder = folderOfPath(item && item.path);
  if (!poster || !folder) return;
  var btn = document.createElement("button");
  btn.type = "button";
  btn.className = "open-folder-btn on-poster";
  btn.title = "Open this folder in Explorer (local PC only)";
  btn.setAttribute("aria-label", "Open folder in Explorer");
  btn.textContent = "⤢"; // inline "open / external" glyph, no external asset
  btn.addEventListener("click", function (e) {
    e.preventDefault();
    e.stopPropagation();
    openFolder(folder);
  });
  poster.appendChild(btn);
}

// Point a card's poster slot at its real artwork: GET /api/media-image/<id>?kind=
// poster (the server applies season-inheritance + serves the local jpg as-is).
//
// GATED on item.poster_available (Phase 5.7): items_payload() now reports, per
// row, whether resolve_artwork_path finds a poster on disk — the SAME resolver
// this endpoint uses. When it is FALSE we create NO <img> at all and the gradient
// + initial placeholder stands alone, so a posterless card never fires a
// speculative request that would just 404. When it is TRUE we still keep the
// belt-and-suspenders `error` fallback (the file could vanish between the scan
// and the request): on error the <img> hides and the gradient shows through, so a
// missing poster never flashes a broken-image icon.
//
// XSS-safe: the id is the library's own canonical id, URL-encoded into the path;
// no markup is ever interpolated.
function addPosterImage(poster, item) {
  var id = item && item.id;
  if (!poster || !id) return;
  // No poster on disk -> leave the gradient placeholder; request nothing.
  if (!item.poster_available) return;

  var img = document.createElement("img");
  img.className = "poster-img";
  img.alt = "";              // decorative; the title is the accessible label
  img.loading = "lazy";
  img.decoding = "async";
  img.draggable = false;
  // Start invisible via CSS opacity (NOT `hidden`/display:none). A loading="lazy"
  // img with display:none has NO layout box, so the browser never sees it near the
  // viewport and NEVER fetches it -> the `load` event never fires -> the poster
  // stays invisible forever (the deadlock this replaces). opacity:0 keeps a layout
  // box so lazy still fetches it; .is-loaded fades it in on load. On error we drop
  // the <img> so the gradient placeholder + initial show through (no broken icon).
  img.addEventListener("load", function () {
    img.classList.add("is-loaded");
    poster.classList.add("has-poster"); // enables the bottom scrim (styles.css)
  });
  img.addEventListener("error", function () {
    img.remove();
    poster.classList.remove("has-poster");
  });
  img.src = "/api/media-image/" + encodeURIComponent(id) + "?kind=poster";
  // Prepend so it sits beneath the badge/open-folder button (which are appended
  // after) and over the gradient background. The CSS gives it z-index:1.
  poster.insertBefore(img, poster.firstChild);
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

// The equivalent CLI command shown in the expanded terminal header. For
// fetch_restore this is the documented invocation (plus ` episodes <range>` when
// a range was used); other actions prefer the reclaim-provided suggested command
// and fall back to a plain `python main.py <action> <id>`.
function commandFor(action, item) {
  if (action === "fetch_restore") {
    var base = "python main.py fetch_restore " + (item && item.id ? item.id : "");
    if (item && item.episodes) base += " episodes " + item.episodes;
    return base.trim();
  }
  if (action === "sort") return "python main.py sort";
  if (item && item.suggested_command) return item.suggested_command;
  var id = item && item.id ? " " + item.id : "";
  return "python main.py " + action + id;
}

// Dispose every fetch-ring under `container` before its cards are removed. app.js
// calls this on the SINGLE grid-clear path (panel re-paint on tab/sub-view switch
// and post-fetch refresh), so no ResizeObserver accumulates across re-renders.
export function destroyRingsIn(container) {
  if (!container) return;
  var cards = container.querySelectorAll(".card");
  for (var i = 0; i < cards.length; i += 1) {
    var r = cards[i]._fetchRing;
    if (r && typeof r.destroy === "function") {
      r.destroy();
      cards[i]._fetchRing = null;
    }
  }
}

// ---------------------------------------------------------------------------
// Archived/real-version info under the title (IMP-E16 B1)
//
// An ARCHIVED tile's on-disk file is a tiny dummy, so item.size_bytes (shown on
// the right) is e.g. "0.45 KB". The library stores the REAL fetched size +
// print (actual_size_bytes / tech / release_name), surfaced here as a small line
// UNDER the title: a humanized real size with a tiny label, plus a capped row of
// dim tech chips. Everything is XSS-safe (textContent only) and best-effort —
// missing fields just drop their chip.
// ---------------------------------------------------------------------------

// Channel COUNT -> the conventional layout label. 8->7.1, 6->5.1, 2->2.0, 1->Mono;
// any other count falls back to "<n>ch". Returns "" for a non-positive/NaN count.
function channelLayout(ch) {
  var n = Number(ch);
  if (!isFinite(n) || n <= 0) return "";
  if (n === 1) return "Mono";
  if (n === 2) return "2.0";
  if (n === 6) return "5.1";
  if (n === 8) return "7.1";
  return n + "ch";
}

// Shorten a verbose MediaInfo audio commercial-name to the useful codec keywords.
// "Dolby TrueHD with Dolby Atmos" -> "TrueHD Atmos"; "DTS-HD Master Audio" ->
// "DTS-HD MA"; falls back to the trimmed original (first ~24 chars) when nothing
// known matches, so an unrecognised codec still shows something sensible.
function shortAudio(audio) {
  var s = String(audio || "").trim();
  if (!s) return "";
  var low = s.toLowerCase();
  var parts = [];
  if (/true\s*hd/.test(low)) parts.push("TrueHD");
  else if (/dts-?hd\s*ma|dts-?hd\s*master/.test(low)) parts.push("DTS-HD MA");
  else if (/dts-?hd/.test(low)) parts.push("DTS-HD");
  else if (/\bdts\b/.test(low)) parts.push("DTS");
  else if (/e-?ac-?3|ddp|dolby digital plus|dd\+/.test(low)) parts.push("DD+");
  else if (/ac-?3|dolby digital/.test(low)) parts.push("DD");
  else if (/\baac\b/.test(low)) parts.push("AAC");
  else if (/\bflac\b/.test(low)) parts.push("FLAC");
  if (/atmos/.test(low) && parts.indexOf("Atmos") < 0) parts.push("Atmos");
  if (parts.length) return parts.join(" ");
  return s.length > 24 ? s.slice(0, 24).trim() : s;
}

// Best-effort token parser over the full release filename. Pure: returns
// { dv, source, edition } where each is a short display string or null. Used to
// surface print details (DV profile, source/encode, edition) that the structured
// tech_spec does not carry. Patterns mirror the step's spec and are intentionally
// forgiving (case-insensitive, dot/space tolerant); we show only what matches.
function parseReleaseTokens(releaseName) {
  var out = { dv: null, source: null, edition: null };
  var s = String(releaseName || "");
  if (!s) return out;

  // Dolby Vision profile / FEL — "DV.P8", "DV P7", "Profile 7", "FEL".
  var m = s.match(/DV[.\s_-]?P?(\d)/i);
  if (m) {
    out.dv = "DV P" + m[1];
  } else if (/\bFEL\b/i.test(s)) {
    out.dv = "DV FEL";
  } else {
    var mp = s.match(/Profile[.\s_-]?(\d)/i);
    if (mp) out.dv = "DV P" + mp[1];
  }

  // Source / encode — first match wins, normalized to a canonical label.
  var sources = [
    [/\bREMUX\b/i, "REMUX"],
    [/\bBlu-?Ray\b/i, "BluRay"],
    [/\bBDRip\b/i, "BDRip"],
    [/\bWEB-?DL\b/i, "WEB-DL"],
    [/\bWEB-?Rip\b/i, "WEBRip"],
    [/\bHDTV\b/i, "HDTV"],
  ];
  for (var i = 0; i < sources.length; i += 1) {
    if (sources[i][0].test(s)) {
      out.source = sources[i][1];
      break;
    }
  }

  // Edition — first match wins.
  var editions = [
    [/\biMAX\b/i, "iMAX"],
    [/\bExtended\b/i, "Extended"],
    [/\bDirector'?s[.\s_-]?Cut\b/i, "Director's Cut"],
    [/\bTheatrical\b/i, "Theatrical"],
    [/\bRemastered\b/i, "Remastered"],
  ];
  for (var j = 0; j < editions.length; j += 1) {
    if (editions[j][0].test(s)) {
      out.edition = editions[j][1];
      break;
    }
  }
  return out;
}

// Maximum chips shown on the tile; the rest can surface in the hover dossier.
var MAX_TECH_CHIPS = 5;

// Compose the ordered, de-duplicated, capped list of chip strings from the
// compact `tech` dict + tokens parsed from the release filename. Order is
// most-useful-first: resolution, HDR/DV, source/edition, codec, audio. DV (from
// the filename) supersedes a plain HDR/DV value from tech when present.
function buildTechChips(tech, releaseName) {
  var t = tech || {};
  var tokens = parseReleaseTokens(releaseName);
  var chips = [];

  function add(label) {
    var s = String(label || "").trim();
    if (s && chips.indexOf(s) < 0) chips.push(s);
  }

  // 1) Resolution (e.g. "2160p").
  add(t.resolution);

  // 2) HDR / Dolby Vision. A DV profile parsed from the name ("DV P8") is richer
  //    than tech.hdr's "Dolby Vision", so prefer it; otherwise show tech.hdr.
  if (tokens.dv) add(tokens.dv);
  else add(t.hdr);

  // 3) Source/encode + edition from the filename ("REMUX", "iMAX").
  add(tokens.source);
  add(tokens.edition);

  // 4) Video codec ("HEVC").
  add(t.video_codec);

  // 5) Audio — combine the short codec name with the channel layout
  //    ("TrueHD Atmos" + 8 -> "TrueHD Atmos 7.1").
  var audio = shortAudio(t.audio);
  var layout = channelLayout(t.audio_channels);
  var audioChip = [audio, layout].filter(Boolean).join(" ");
  add(audioChip);

  return chips.slice(0, MAX_TECH_CHIPS);
}

// Decide whether the archived/real-size line should render for this item. It is
// meant for a tile whose on-disk file is a fetched/archived DUMMY — so the real
// size + print is informative and the on-disk size_bytes is misleadingly tiny.
// Gate: actual_size_bytes is present AND either the state is ARCHIVED, or the
// real size is clearly larger than the on-disk size (a fetched dummy). For a
// fully-restored REAL file (the big file IS on disk, so actual ~= on-disk) we do
// NOT show it — labelling a local file "archived 82 GB" would be misleading.
function shouldShowArchivedSize(item) {
  var actual = Number(item && item.actual_size_bytes);
  if (!isFinite(actual) || actual <= 0) return false;
  if (item.state === "ARCHIVED") return true;
  var onDisk = Number(item.size_bytes);
  if (!isFinite(onDisk) || onDisk < 0) return false;
  // "Clearly larger": the real size dwarfs what's on disk (a dummy/placeholder).
  // 4x with a small floor avoids tripping on rounding for a near-complete file.
  return actual > Math.max(onDisk * 4, onDisk + 1024 * 1024);
}

// Render the archived/real-size line + tech chip row UNDER the title, into the
// provided container. XSS-safe (textContent only). No-op when the gate is off or
// nothing meaningful is available, so non-archived / no-tech leaves are untouched.
function renderArchivedInfo(container, item) {
  if (!container) return;
  if (!shouldShowArchivedSize(item)) return;

  var line = document.createElement("div");
  line.className = "tech-line";

  // The humanized REAL size, tagged so it reads as the archived/real version size
  // (distinct from the on-disk dummy shown on the right).
  var sizeWrap = document.createElement("span");
  sizeWrap.className = "tech-size";
  var sizeLabel = document.createElement("span");
  sizeLabel.className = "tech-size-label";
  sizeLabel.textContent = "Archived";
  sizeLabel.title = "Real fetched file size (the local copy is a tiny placeholder)";
  var sizeVal = document.createElement("span");
  sizeVal.className = "tech-size-val";
  sizeVal.textContent = humanSize(item.actual_size_bytes);
  sizeWrap.appendChild(sizeLabel);
  sizeWrap.appendChild(sizeVal);
  line.appendChild(sizeWrap);

  // Capped row of dim tech chips (resolution / HDR-DV / source / codec / audio).
  var chips = buildTechChips(item.tech, item.release_name);
  if (chips.length) {
    var chipRow = document.createElement("span");
    chipRow.className = "tech-chips";
    chips.forEach(function (label) {
      var chip = document.createElement("span");
      chip.className = "tech-chip";
      chip.textContent = label;
      chipRow.appendChild(chip);
    });
    line.appendChild(chipRow);
  }

  container.appendChild(line);
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

  // Stash the item on the node so the delegated hover detail-window (preview.js)
  // can map a hovered/focused .card back to its data with no per-card listener or
  // side index. An own-property on the element, GC'd with the node (no leak); the
  // double-underscore namespaces it like node._fetchRing. Covers BOTH the flat
  // grid AND grouped-tree leaf cards, since every leaf card is built here.
  node.__mvItem = item;

  // Poster slot (gradient + big initial placeholder). Phase 5.2 wires a real
  // image on top: addPosterImage points an <img> at /api/media-image/<id> and
  // hides it on error, so a missing poster falls back to this gradient.
  var poster = $(".poster", node);
  poster.classList.add("p-" + m.cssKey);
  $(".initial", poster).textContent = initialFor(item.id);
  addPosterImage(poster, item);

  // Badge (color-coded by state).
  var badge = $(".badge", poster);
  badge.classList.add("b-" + m.cssKey);
  $(".badge-label", badge).textContent = m.label;

  // Open-folder affordance: a small top-right icon button on EVERY card (flat AND
  // grouped views). It POSTs /api/open-folder for the leaf's containing folder
  // (dirname of its path). Mounted on the poster (position:relative) at the same
  // z-layer as .card-actions so it stays clickable above the hover ring / glow and
  // never intercepts the badge or the Fetch & Restore button. The server enforces
  // localhost-only + path safety + demo simulation; data.js handles the toasts.
  addOpenFolderButton(poster, item);

  // Prominent TITLE (real metadata.title once Phase 5/TMDB lands; humanized id
  // until then — see title.js) + size. The raw id moves to the card foot.
  var titleEl = $(".item-title", node);
  titleEl.textContent = displayTitle(item);
  if (item.guessed) {
    var g = document.createElement("span");
    g.className = "guess-tag";
    g.title = "Editable guessed id for an unprepped file";
    g.textContent = "GUESS";
    titleEl.appendChild(g);
  }
  $(".item-size", node).textContent = humanSize(item.size_bytes);

  // Archived/real-version line UNDER the title (IMP-E16 B1): for an archived (or
  // otherwise dummy-on-disk) leaf, show the REAL fetched size + a capped tech chip
  // row built from tech_spec + tokens parsed from the release filename. Mounted
  // between the title row and the path so it reads as title metadata. No-op (and
  // unchanged layout) for non-archived / no-tech leaves. The right-side on-disk
  // size above stays exactly as-is.
  var pathEl = $(".item-path", node);
  var techHost = document.createElement("div");
  techHost.className = "tech-host";
  renderArchivedInfo(techHost, item);
  if (techHost.firstChild) pathEl.parentNode.insertBefore(techHost, pathEl);

  // Path.
  pathEl.textContent = item.path || "";

  // Raw canonical id at the foot — small/dim/monospace, visually subordinate to
  // the title above. Always shown (it's the stable handle for the entry).
  $(".item-rawid", node).textContent = item.id;

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
    // Phase 2: WORKING Fetch & Restore. Surfaces ONLY this action (no plain
    // download). Default fetches the whole entry (episodes omitted). The button
    // is disabled while its job runs and re-enabled on terminal done OR error.
    var fetchBtn = document.createElement("button");
    fetchBtn.type = "button";
    fetchBtn.className = "action-btn fetch-restore";
    fetchBtn.textContent = m.verb; // "Fetch & Restore"
    fetchBtn.title = "Fetch this entry from cloud storage and restore it locally";

    // The SVG ring overlay + numeric chunk label live on this card. The label is
    // mounted into the action row so it sits beside the button.
    var ring = createRing(node, actions);
    // Track the ring on the card so the single grid-clear path (app.js, before it
    // empties #panel) can call ring.destroy() and disconnect its ResizeObserver —
    // otherwise an observer leaks per archived card on every fetch/re-render.
    node._fetchRing = ring;

    fetchBtn.addEventListener("click", function () {
      runFetchRestore(item, fetchBtn, jobPanel, ring);
    });
    actions.appendChild(fetchBtn);

    // ?demo / #demo: claim the FIRST archived card and run a safe, backend-free
    // synthetic animation through the real ring code path (faithful path, fake
    // numbers). Marked with a small DEMO tag.
    maybeStartDemo(node, fetchBtn, jobPanel, ring);
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
  if (jobPanel) jobPanel._jobCommand = commandFor(action, item);
  renderJob(jobPanel, { status: "running", name: action, output: "" }, true);

  authFetch("/api/action/" + action, {
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
    authFetch("/api/job/" + encodeURIComponent(jobId))
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

// ---------------------------------------------------------------------------
// Fetch & Restore (ARCHIVED) — working button + SVG progress ring (Phase 2).
// ---------------------------------------------------------------------------

// Read the optional episode range from the item, if a future UI adds one. Today
// there is no range input on the card, so we always fetch the WHOLE entry and
// OMIT episodes (matching the server contract: episodes=None => whole entry).
function fetchRestoreBody(item) {
  var options = {};
  // item.episodes would be a "1-3"-style string if a range control existed; it
  // does not yet, so options.episodes stays omitted (whole-entry fetch).
  if (item.episodes) options.episodes = item.episodes;
  return { id: item.id, options: options };
}

function runFetchRestore(item, btn, jobPanel, ring) {
  if (btn.disabled) return; // guard against double-submit
  btn.disabled = true;
  if (jobPanel) jobPanel._jobCommand = commandFor("fetch_restore", item);
  ring.setChunks(0, 0); // show the ring immediately at 0 (job not enqueued yet)
  renderJob(jobPanel, { status: "running", name: "fetch_restore", output: "" }, true);

  authFetch("/api/action/fetch_restore", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(fetchRestoreBody(item)),
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
      pollFetchRestore(data.job_id, jobPanel, btn, ring);
    })
    .catch(function (err) {
      // Network / non-202 failure before a job exists: faithful error, re-enable,
      // hide the ring (nothing actually ran).
      renderJob(
        jobPanel,
        {
          status: "error",
          name: "fetch_restore",
          output: String((err && err.message) || err),
        },
        false
      );
      ring.reset();
      btn.disabled = false;
    });
}

// Poll variant that ALSO drives the progress ring from job.progress each tick.
// Terminal done -> ring.complete() glow + scheduleRefresh() (auto-flip). Terminal
// error -> faithful output, re-enable, leave the ring at its last fraction
// (honest "got this far"), no glow.
function pollFetchRestore(jobId, jobPanel, btn, ring) {
  function tick() {
    authFetch("/api/job/" + encodeURIComponent(jobId))
      .then(function (res) {
        if (!res.ok) {
          return res.text().then(function (t) {
            throw new Error("HTTP " + res.status + (t ? ": " + t : ""));
          });
        }
        return res.json();
      })
      .then(function (job) {
        var p = job.progress || { done: 0, total: 0 };
        var running = job.status === "running";

        if (job.status === "done") {
          ring.complete(); // snap to a full, glowing closed outline
        } else {
          ring.setChunks(p.done, p.total); // grow toward done/total (guards 0)
        }

        renderJob(jobPanel, job, running);

        if (running) {
          setTimeout(tick, POLL_MS);
        } else {
          btn.disabled = false; // re-enable on done OR error
          if (job.status === "done") {
            // Auto-flip: after the visible-result delay, reload the model so the
            // card leaves Archived and appears under Fetched·not-archived.
            scheduleRefresh();
          }
          // On error we deliberately do NOT scheduleRefresh: the card stays in
          // Archived for an obvious retry, ring frozen at its last fraction.
        }
      })
      .catch(function (err) {
        renderJob(
          jobPanel,
          {
            status: "error",
            name: "fetch_restore",
            output: String((err && err.message) || err),
          },
          false
        );
        btn.disabled = false;
        // Leave the ring as-is: the failure is a polling error, not necessarily a
        // fetch rollback, so we keep the last honest fraction visible.
      });
  }
  tick();
}

// ---------------------------------------------------------------------------
// ?demo / #demo live preview — IDENTICAL contract in both candidates.
//
// When the URL carries ?demo (or #demo), the FIRST archived card built runs a
// safe, client-side-only animation that drives the SAME ring code path with
// SYNTHETIC progress: total=8, done 1..8 every ~600ms, then "done" -> glow. NO
// backend call, NO real fetch. A small DEMO tag marks the card. Faithful path
// (real ring.setChunks/complete), fake numbers.
// ---------------------------------------------------------------------------

function demoRequested() {
  try {
    var s = window.location.search || "";
    var h = window.location.hash || "";
    return /(?:^|[?&])demo(?:=|&|$)/.test(s) || /(?:^|#)demo$/.test(h);
  } catch (e) {
    return false;
  }
}

var _demoClaimed = false; // only the FIRST archived card runs the demo

function maybeStartDemo(node, btn, jobPanel, ring) {
  if (_demoClaimed || !demoRequested()) return;
  _demoClaimed = true;

  // Small DEMO tag on the card so it's obviously a preview, not a real fetch.
  var tag = document.createElement("span");
  tag.className = "demo-tag";
  tag.textContent = "DEMO";
  tag.title = "Synthetic progress preview — no backend fetch is running";
  var poster = $(".poster", node);
  if (poster) poster.appendChild(tag);

  btn.disabled = true; // mirror a real run: no double-submit during the demo
  jobPanel._jobCommand = "python main.py fetch_restore <id>  # demo (no real fetch)";
  renderJob(
    jobPanel,
    { status: "running", name: "fetch_restore (demo)", output: "Synthetic preview — no real fetch." },
    true
  );

  var TOTAL = 8;
  var doneN = 0;

  // The card is built into a detached fragment, so its box has no size yet. Wait
  // two frames for it to be attached + laid out before sizing the ring; otherwise
  // getBoundingClientRect() is 0x0 and the outline can't be measured. (A real
  // fetch starts from a click, when the card is already attached, so only the
  // demo needs this.)
  requestAnimationFrame(function () {
    requestAnimationFrame(startDemoAnimation);
  });

  function startDemoAnimation() {
    ring.setChunks(0, TOTAL);
    var timer = setInterval(function () {
      doneN += 1;
      if (doneN >= TOTAL) {
        clearInterval(timer);
        ring.setChunks(TOTAL, TOTAL);
        // Brief beat, then snap to the glowing complete state (same as real done).
        setTimeout(function () {
          ring.complete();
          renderJob(
            jobPanel,
            {
              status: "done",
              name: "fetch_restore (demo)",
              output:
                "Synthetic preview complete — this is what a finished fetch looks like.",
            },
            false
          );
          btn.disabled = false; // demo never auto-flips (no model change)
        }, 500);
        return;
      }
      ring.setChunks(doneN, TOTAL);
    }, 600);
  }
}

// Render a job record into a panel. Output goes through textContent (a <pre>),
// never innerHTML. `error` status is shown faithfully — output is NOT hidden.
//
// Side effects that power the expandable terminal (change #3): the latest job is
// stashed on the panel (panel._lastJob) so openTerminal() can paint immediately,
// and notifyJob() is fired AFTER rendering so an already-open overlay bound to
// this panel repaints from the SAME job object — the expanded view subscribes to
// this single existing poll rather than starting a second one.
function renderJob(panel, job, running) {
  panel.classList.add("show");
  panel.textContent = "";
  panel._lastJob = job;

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

  // Expand affordance (change #3): a diagonal-arrow button pinned to the panel's
  // bottom-right corner. Available in EVERY state (running / done / error) so the
  // full log + equivalent command can be reviewed after the fact. Opening the
  // overlay starts no new poll — it subscribes to this panel's renderJob stream.
  var expand = document.createElement("button");
  expand.type = "button";
  expand.className = "job-expand";
  expand.title = "Expand to full-screen terminal";
  expand.setAttribute("aria-label", "Expand to full-screen terminal");
  expand.textContent = "⤢"; // ⤢ diagonal arrows
  expand.addEventListener("click", function () {
    openTerminal(panel);
  });
  panel.appendChild(expand);

  // Fan this same job out to an open, bound terminal overlay (single-poll live
  // mirror). No-op when the overlay is closed or bound to a different panel.
  notifyJob(panel, job, panel._jobCommand || "");
}
