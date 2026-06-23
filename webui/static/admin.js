/* MediaVault Console — admin "Access" panel (IMP-E15 FRONTEND).
 *
 * ES module; `node --check admin.js` covers it on its own. This is the OWNER-only
 * surface for the admin-minted, expiring shared-token system. The device side of
 * the token UX (capture ?token=, cookie/header auth, the 401 prompt) lives in
 * auth.js and is UNTOUCHED by this module — here we only ADD the minting console.
 *
 * GATING — whoami is the single source of truth:
 *   On init we call GET /api/whoami (no-auth; returns {is_admin, authed}). The
 *   "Access" header button + the whole panel are injected ONLY when is_admin is
 *   true (i.e. the genuine local Alienware browser / the owner). A remote device
 *   never sees the button; even if it somehow did, the mint/list/revoke endpoints
 *   are server-gated (403 remote), so this is defence-in-depth, not the only lock.
 *   A failed/absent whoami (e.g. the backend half not merged yet, or a network
 *   blip) is treated as NOT-admin — fail-safe: the console renders unchanged and
 *   the device 401 flow still works.
 *
 * THE PANEL (a modal mirroring auth.js's backdrop/card, dark + mobile-friendly):
 *   • Mint form  — a Label field + a TTL <select> (1h / 8h / 1d / 7d / 30d /
 *     Custom / Never). Custom reveals a number + unit (hours/days). "Create token"
 *     POSTs {label, ttl_seconds} (ttl_seconds = null for Never; computed seconds
 *     otherwise) and then shows the RAW token ONCE plus a ready-to-share URL
 *     (location.origin + "/?token=" + encodeURIComponent(raw)) with Copy buttons
 *     and a clear "copy it now — it won't be shown again" warning.
 *   • Active list — GET /api/token rendered as {label, expires-countdown} rows,
 *     each with a Revoke button (DELETE /api/token/{id}); the list refreshes after
 *     every mint and every revoke.
 *
 * XSS-safe: every dynamic string (label, id, dates, the raw token, the share URL)
 * is written via textContent or an input's .value — never as HTML. The raw token
 * is shown only inside the panel and is wiped from the DOM when the panel closes
 * or another token is minted; it is never logged.
 */

"use strict";

import { authFetch } from "./auth.js";

// ---------------------------------------------------------------------------
// TTL presets. value = seconds; null = "Never" (no expiry). "custom" is a
// sentinel that reveals the number+unit row. Order matches the spec.
// ---------------------------------------------------------------------------
var TTL_PRESETS = [
  { id: "1h", label: "1 hour", seconds: 3600 },
  { id: "8h", label: "8 hours", seconds: 8 * 3600 },
  { id: "1d", label: "1 day", seconds: 86400 },
  { id: "7d", label: "7 days", seconds: 7 * 86400 },
  { id: "30d", label: "30 days", seconds: 30 * 86400 },
  { id: "custom", label: "Custom…", seconds: "custom" },
  { id: "never", label: "Never (no expiry)", seconds: null },
];

// Default selection: 7 days (a sensible, safe-by-default lifetime for a device
// token — long enough to be useful, short enough to limit a leaked link).
var DEFAULT_TTL_ID = "7d";

var _panelEl = null; // the backdrop element (built lazily, reused)
var _refs = null; // cached references to the panel's interactive sub-elements

// ---------------------------------------------------------------------------
// Entry point — probe whoami, build the admin surface only for the owner.
// ---------------------------------------------------------------------------

export function initAdmin() {
  // whoami is a no-auth endpoint (per the contract), so authFetch will not fire
  // the 401 token prompt here; a non-OK/needs-token answer simply means "not the
  // owner" and we stay quiet. Any failure is swallowed -> fail-safe to not-admin.
  authFetch("/api/whoami")
    .then(function (res) {
      return res && res.ok ? res.json() : null;
    })
    .then(function (data) {
      if (data && data.is_admin === true) {
        mountAccessButton();
      }
    })
    .catch(function () {
      /* whoami unreachable (or backend not merged) — render no admin surface. */
    });
}

// Inject a small "Access" button into the header's tab row (right cluster, next
// to "Sort library"). Styled via .btn-access (styles.css) to match .btn-sort.
function mountAccessButton() {
  var row = document.querySelector(".tabbar-row");
  if (!row) return; // header markup changed — never throw, just no-op.
  if (document.getElementById("btn-access")) return; // idempotent

  var btn = document.createElement("button");
  btn.type = "button";
  btn.className = "btn-access";
  btn.id = "btn-access";
  btn.title = "Manage device access tokens";
  btn.setAttribute("aria-haspopup", "dialog");

  var key = document.createElement("span");
  key.setAttribute("aria-hidden", "true");
  key.textContent = "🔑"; // key glyph
  btn.appendChild(key);
  btn.appendChild(document.createTextNode(" Access"));

  btn.addEventListener("click", openPanel);

  // Place it just before the spacer so it sits with the sort button on the right;
  // fall back to appending if the spacer is gone.
  var spacer = row.querySelector(".spacer");
  if (spacer) {
    row.insertBefore(btn, spacer);
  } else {
    row.appendChild(btn);
  }
}

// ---------------------------------------------------------------------------
// Panel construction (once) — backdrop + card with two sections.
// ---------------------------------------------------------------------------

function ensurePanel() {
  if (_panelEl) return _panelEl;

  var backdrop = document.createElement("div");
  backdrop.className = "access-backdrop";
  backdrop.setAttribute("role", "dialog");
  backdrop.setAttribute("aria-modal", "true");
  backdrop.setAttribute("aria-labelledby", "access-title");

  var card = document.createElement("div");
  card.className = "access-modal";

  card.appendChild(buildHead());
  var body = document.createElement("div");
  body.className = "access-body";
  body.appendChild(buildMintSection());
  body.appendChild(buildMintedResult());
  body.appendChild(buildListSection());
  card.appendChild(body);

  backdrop.appendChild(card);
  document.body.appendChild(backdrop);

  // Dismiss on a backdrop click (but not when clicking inside the card) and Esc.
  backdrop.addEventListener("click", function (e) {
    if (e.target === backdrop) closePanel();
  });
  document.addEventListener("keydown", function (e) {
    if (e.key === "Escape" && backdrop.classList.contains("show")) closePanel();
  });

  _panelEl = backdrop;
  return _panelEl;
}

function buildHead() {
  var head = document.createElement("div");
  head.className = "access-head";

  var icon = document.createElement("div");
  icon.className = "access-icon";
  icon.setAttribute("aria-hidden", "true");
  icon.textContent = "🔑"; // key
  head.appendChild(icon);

  var h2 = document.createElement("h2");
  h2.id = "access-title";
  h2.textContent = "Device access";
  head.appendChild(h2);

  var close = document.createElement("button");
  close.type = "button";
  close.className = "access-close";
  close.setAttribute("aria-label", "Close");
  close.textContent = "✕"; // ✕
  close.addEventListener("click", closePanel);
  head.appendChild(close);

  return head;
}

// --- Mint form -------------------------------------------------------------

function buildMintSection() {
  var sec = document.createElement("section");
  sec.className = "access-section access-mint";

  var title = document.createElement("h3");
  title.className = "access-section-title";
  title.textContent = "Create a token";
  sec.appendChild(title);

  var hint = document.createElement("p");
  hint.className = "access-hint";
  hint.textContent =
    "Mint a link to share with a phone/tablet over Tailscale. Set a label and how long it stays valid.";
  sec.appendChild(hint);

  // Label field.
  var labelField = document.createElement("label");
  labelField.className = "access-field";
  var labelCap = document.createElement("span");
  labelCap.className = "access-field-label";
  labelCap.textContent = "Label";
  labelField.appendChild(labelCap);
  var labelInput = document.createElement("input");
  labelInput.type = "text";
  labelInput.className = "provider-input access-label-input";
  labelInput.placeholder = "e.g. iPhone, Living-room iPad";
  labelInput.autocomplete = "off";
  labelInput.spellcheck = false;
  labelInput.maxLength = 80;
  labelField.appendChild(labelInput);
  sec.appendChild(labelField);

  // TTL selector.
  var ttlField = document.createElement("label");
  ttlField.className = "access-field";
  var ttlCap = document.createElement("span");
  ttlCap.className = "access-field-label";
  ttlCap.textContent = "Valid for";
  ttlField.appendChild(ttlCap);
  var ttlSelect = document.createElement("select");
  ttlSelect.className = "provider-input access-ttl-select";
  TTL_PRESETS.forEach(function (p) {
    var opt = document.createElement("option");
    opt.value = p.id;
    opt.textContent = p.label;
    if (p.id === DEFAULT_TTL_ID) opt.selected = true;
    ttlSelect.appendChild(opt);
  });
  ttlField.appendChild(ttlSelect);
  sec.appendChild(ttlField);

  // Custom amount + unit row (revealed only when "Custom…" is chosen).
  var customRow = document.createElement("div");
  customRow.className = "access-custom-row";
  customRow.hidden = true;
  var customNum = document.createElement("input");
  customNum.type = "number";
  customNum.className = "provider-input access-custom-num";
  customNum.min = "1";
  customNum.step = "1";
  customNum.value = "12";
  customNum.setAttribute("aria-label", "Custom duration amount");
  customRow.appendChild(customNum);
  var customUnit = document.createElement("select");
  customUnit.className = "provider-input access-custom-unit";
  customUnit.setAttribute("aria-label", "Custom duration unit");
  [
    { v: "hours", t: "hours" },
    { v: "days", t: "days" },
  ].forEach(function (u) {
    var o = document.createElement("option");
    o.value = u.v;
    o.textContent = u.t;
    customUnit.appendChild(o);
  });
  customRow.appendChild(customUnit);
  sec.appendChild(customRow);

  ttlSelect.addEventListener("change", function () {
    customRow.hidden = ttlSelect.value !== "custom";
  });

  // Create button + inline error note.
  var note = document.createElement("p");
  note.className = "access-error";
  note.setAttribute("role", "alert");
  note.hidden = true;
  sec.appendChild(note);

  var create = document.createElement("button");
  create.type = "button";
  create.className = "access-create";
  create.textContent = "Create token";
  create.addEventListener("click", function () {
    submitMint();
  });
  sec.appendChild(create);

  // Submit on Enter from the label field for a fast one-handed flow.
  labelInput.addEventListener("keydown", function (e) {
    if (e.key === "Enter") {
      e.preventDefault();
      submitMint();
    }
  });

  // Stash references the rest of the module needs.
  _refs = _refs || {};
  _refs.labelInput = labelInput;
  _refs.ttlSelect = ttlSelect;
  _refs.customRow = customRow;
  _refs.customNum = customNum;
  _refs.customUnit = customUnit;
  _refs.error = note;
  _refs.create = create;

  return sec;
}

// --- Minted-token result (hidden until a successful mint) -------------------

function buildMintedResult() {
  var box = document.createElement("section");
  box.className = "access-section access-minted";
  box.hidden = true;

  var title = document.createElement("h3");
  title.className = "access-section-title ok";
  title.textContent = "Token created";
  box.appendChild(title);

  var warn = document.createElement("p");
  warn.className = "access-once-warn";
  warn.textContent = "Copy it now — it won’t be shown again.";
  box.appendChild(warn);

  // Share URL (the primary thing to copy) — a readonly input + Copy button.
  box.appendChild(
    buildCopyRow("Share link", "access-url-input", "access-url-copy", true)
  );
  // Raw token on its own, for pasting into the device's token prompt directly.
  box.appendChild(
    buildCopyRow("Raw token", "access-token-input", "access-token-copy", false)
  );

  var meta = document.createElement("p");
  meta.className = "access-minted-meta";
  box.appendChild(meta);

  _refs = _refs || {};
  _refs.mintedBox = box;
  _refs.urlInput = box.querySelector(".access-url-input");
  _refs.urlCopy = box.querySelector(".access-url-copy");
  _refs.tokenInput = box.querySelector(".access-token-input");
  _refs.tokenCopy = box.querySelector(".access-token-copy");
  _refs.mintedMeta = meta;

  wireCopyButton(_refs.urlCopy, _refs.urlInput);
  wireCopyButton(_refs.tokenCopy, _refs.tokenInput);

  return box;
}

// A captioned readonly input + copy button. `wide` widens the URL field.
function buildCopyRow(caption, inputClass, btnClass, wide) {
  var wrap = document.createElement("div");
  wrap.className = "access-copy-field";

  var cap = document.createElement("span");
  cap.className = "access-field-label";
  cap.textContent = caption;
  wrap.appendChild(cap);

  var row = document.createElement("div");
  row.className = "access-copy-row";

  var input = document.createElement("input");
  input.type = "text";
  input.readOnly = true;
  input.className =
    "provider-input " + inputClass + (wide ? " access-url" : " access-token");
  input.spellcheck = false;
  input.setAttribute("aria-label", caption);
  // Select-all on focus so a manual copy (no clipboard API) is one gesture.
  input.addEventListener("focus", function () {
    try {
      input.select();
    } catch (e) {
      /* selection is a convenience; never throw. */
    }
  });
  row.appendChild(input);

  var btn = document.createElement("button");
  btn.type = "button";
  btn.className = "copy-btn " + btnClass;
  btn.title = "Copy " + caption.toLowerCase();
  btn.textContent = "⧉"; // ⧉ matches the card copy buttons
  row.appendChild(btn);

  wrap.appendChild(row);
  return wrap;
}

// --- Active tokens list -----------------------------------------------------

function buildListSection() {
  var sec = document.createElement("section");
  sec.className = "access-section access-list-section";

  var title = document.createElement("h3");
  title.className = "access-section-title";
  title.textContent = "Active tokens";
  sec.appendChild(title);

  var list = document.createElement("div");
  list.className = "access-list";
  sec.appendChild(list);

  _refs = _refs || {};
  _refs.list = list;
  return sec;
}

// ---------------------------------------------------------------------------
// Open / close
// ---------------------------------------------------------------------------

function openPanel() {
  ensurePanel();
  resetMintForm();
  _panelEl.classList.add("show");
  refreshList();
  // Focus the label field for an immediate type-and-go.
  window.requestAnimationFrame(function () {
    try {
      _refs.labelInput.focus();
    } catch (e) {
      /* focus is a nicety. */
    }
  });
}

function closePanel() {
  if (!_panelEl) return;
  _panelEl.classList.remove("show");
  // Never leave a freshly-minted secret sitting in the DOM after the owner walks
  // away: wipe the raw token + share link and re-hide the result block.
  clearMintedResult();
}

function resetMintForm() {
  if (!_refs) return;
  _refs.error.hidden = true;
  _refs.error.textContent = "";
  clearMintedResult();
}

function clearMintedResult() {
  if (!_refs || !_refs.mintedBox) return;
  _refs.mintedBox.hidden = true;
  if (_refs.urlInput) _refs.urlInput.value = "";
  if (_refs.tokenInput) _refs.tokenInput.value = "";
  if (_refs.mintedMeta) _refs.mintedMeta.textContent = "";
}

// ---------------------------------------------------------------------------
// Mint
// ---------------------------------------------------------------------------

// Resolve the chosen TTL to seconds (or null for "Never"). Returns
// {seconds: <int|null>} on success, or {error: <msg>} for an invalid custom value.
function resolveTtlSeconds() {
  var id = _refs.ttlSelect.value;
  if (id === "custom") {
    var n = parseInt(_refs.customNum.value, 10);
    if (!isFinite(n) || n <= 0) {
      return { error: "Enter a positive number for the custom duration." };
    }
    var perUnit = _refs.customUnit.value === "days" ? 86400 : 3600;
    return { seconds: n * perUnit };
  }
  for (var i = 0; i < TTL_PRESETS.length; i += 1) {
    if (TTL_PRESETS[i].id === id) return { seconds: TTL_PRESETS[i].seconds };
  }
  return { seconds: null };
}

function submitMint() {
  if (!_refs) return;
  _refs.error.hidden = true;
  _refs.error.textContent = "";

  var label = (_refs.labelInput.value || "").trim();
  if (!label) {
    showMintError("Give the token a label so you can recognise it later.");
    try {
      _refs.labelInput.focus();
    } catch (e) {
      /* ignore */
    }
    return;
  }

  var ttl = resolveTtlSeconds();
  if (ttl.error) {
    showMintError(ttl.error);
    return;
  }

  setBusy(_refs.create, true, "Creating…");

  authFetch("/api/token", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ label: label, ttl_seconds: ttl.seconds }),
  })
    .then(function (res) {
      if (!res.ok) {
        return res
          .json()
          .catch(function () {
            return null;
          })
          .then(function (body) {
            throw new Error(mintErrorMessage(res.status, body));
          });
      }
      return res.json();
    })
    .then(function (data) {
      showMinted(data);
      _refs.labelInput.value = "";
      refreshList();
    })
    .catch(function (err) {
      showMintError((err && err.message) || "Could not create the token.");
    })
    .then(function () {
      setBusy(_refs.create, false, "Create token");
    });
}

function mintErrorMessage(status, body) {
  if (status === 403) {
    return "Only the local (Alienware) browser can create tokens.";
  }
  if (body && body.detail) return String(body.detail);
  return "Could not create the token (HTTP " + status + ").";
}

// Reveal the raw token + share URL exactly once. The raw token is placed ONLY in
// readonly input .value (never as DOM text / markup) and is never logged.
function showMinted(data) {
  if (!data || !data.token) {
    showMintError("The server did not return a token.");
    return;
  }
  var raw = String(data.token);
  var shareUrl = window.location.origin + "/?token=" + encodeURIComponent(raw);

  _refs.urlInput.value = shareUrl;
  _refs.tokenInput.value = raw;

  // Human "expires" line for the freshly minted token.
  var expiry = data.expires_at
    ? "Expires " + humanExpiry(data.expires_at)
    : "Never expires.";
  _refs.mintedMeta.textContent =
    (data.label ? "“" + data.label + "” · " : "") + expiry;

  _refs.mintedBox.hidden = false;
  // Surface the share link for an immediate copy (selects the field too).
  window.requestAnimationFrame(function () {
    try {
      _refs.urlInput.focus();
    } catch (e) {
      /* ignore */
    }
  });
}

function showMintError(msg) {
  _refs.error.textContent = String(msg || "");
  _refs.error.hidden = false;
}

// ---------------------------------------------------------------------------
// List + revoke
// ---------------------------------------------------------------------------

function refreshList() {
  if (!_refs || !_refs.list) return;
  setListMessage("Loading…");

  authFetch("/api/token")
    .then(function (res) {
      if (!res.ok) throw new Error("HTTP " + res.status);
      return res.json();
    })
    .then(function (data) {
      renderList((data && data.tokens) || []);
    })
    .catch(function () {
      setListMessage("Could not load active tokens.");
    });
}

function setListMessage(msg) {
  _refs.list.textContent = "";
  var p = document.createElement("p");
  p.className = "access-list-empty";
  p.textContent = msg;
  _refs.list.appendChild(p);
}

function renderList(tokens) {
  _refs.list.textContent = "";
  if (!tokens.length) {
    setListMessage("No active tokens. Create one above to share with a device.");
    return;
  }

  tokens.forEach(function (tok) {
    _refs.list.appendChild(buildListRow(tok));
  });
}

function buildListRow(tok) {
  var row = document.createElement("div");
  row.className = "access-list-row";

  var info = document.createElement("div");
  info.className = "access-list-info";

  var name = document.createElement("div");
  name.className = "access-list-label";
  name.textContent = tok.label || "(unlabelled)";
  info.appendChild(name);

  var sub = document.createElement("div");
  var expired = isExpired(tok);
  sub.className = "access-list-sub" + (expired ? " expired" : "");
  sub.textContent = expiryText(tok);
  info.appendChild(sub);

  row.appendChild(info);

  var revoke = document.createElement("button");
  revoke.type = "button";
  revoke.className = "access-revoke";
  revoke.textContent = "Revoke";
  revoke.setAttribute(
    "aria-label",
    "Revoke token" + (tok.label ? " " + tok.label : "")
  );
  revoke.addEventListener("click", function () {
    revokeToken(tok, revoke);
  });
  row.appendChild(revoke);

  return row;
}

function revokeToken(tok, btn) {
  if (tok.id == null) return;
  setBusy(btn, true, "Revoking…");
  // encodeURIComponent the id so an unusual id can never break the path.
  authFetch("/api/token/" + encodeURIComponent(String(tok.id)), {
    method: "DELETE",
  })
    .then(function (res) {
      if (!res.ok && res.status !== 404) {
        // 404 = already gone; treat as success so the list just refreshes.
        throw new Error("HTTP " + res.status);
      }
      refreshList();
    })
    .catch(function () {
      setBusy(btn, false, "Revoke");
      // Re-affirm via a fresh list load so the UI reflects server truth.
      refreshList();
    });
}

// ---------------------------------------------------------------------------
// Expiry helpers (all client-side, from the ISO expires_at).
// ---------------------------------------------------------------------------

// True if the server already flagged it expired, or expires_at is in the past.
function isExpired(tok) {
  if (tok && tok.expired === true) return true;
  if (!tok || !tok.expires_at) return false;
  var ms = Date.parse(tok.expires_at);
  if (isNaN(ms)) return false;
  return ms <= Date.now();
}

// "Never" / "Expired" / "expires in 6 days" — the at-a-glance lifetime.
function expiryText(tok) {
  if (!tok || !tok.expires_at) return "Never expires";
  if (isExpired(tok)) return "Expired";
  return "Expires in " + relativeFuture(tok.expires_at);
}

// "in 6 days" worth of words, for the minted-result meta line.
function humanExpiry(iso) {
  if (isExpired({ expires_at: iso })) return "soon";
  return "in " + relativeFuture(iso);
}

// Coarse, friendly future span from now to an ISO instant. Picks the largest
// sensible unit (days > hours > minutes) so "expires in 6 days" reads naturally.
function relativeFuture(iso) {
  var ms = Date.parse(iso);
  if (isNaN(ms)) return "—";
  var secs = Math.max(0, Math.round((ms - Date.now()) / 1000));
  var days = Math.floor(secs / 86400);
  if (days >= 1) return days === 1 ? "1 day" : days + " days";
  var hours = Math.floor(secs / 3600);
  if (hours >= 1) return hours === 1 ? "1 hour" : hours + " hours";
  var mins = Math.floor(secs / 60);
  if (mins >= 1) return mins === 1 ? "1 minute" : mins + " minutes";
  return "less than a minute";
}

// ---------------------------------------------------------------------------
// Small shared UI helpers
// ---------------------------------------------------------------------------

// Disable + relabel a button while an async action runs; restore it after.
function setBusy(btn, busy, text) {
  if (!btn) return;
  btn.disabled = !!busy;
  btn.textContent = text;
}

// Clipboard copy with the same execCommand fallback card.js uses (localhost is a
// non-secure context where navigator.clipboard is undefined). Copies from the
// readonly input's .value so the raw token is never re-stringified into the DOM.
function wireCopyButton(btn, input) {
  btn.addEventListener("click", function () {
    copyText(input.value).then(function () {
      flashCopied(btn);
    });
  });
}

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
  btn.textContent = "✓"; // ✓
  setTimeout(function () {
    btn.classList.remove("copied");
    btn.textContent = prev;
  }, 1100);
}
