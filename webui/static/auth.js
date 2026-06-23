/* MediaVault Console — shared-token client auth (IMP-E15 Phase 4 FRONTEND).
 *
 * ES module; `node --check auth.js` covers it on its own. Owns EVERYTHING about
 * the optional access token so the rest of the SPA stays auth-unaware:
 *
 *   • TOKEN BOOTSTRAP (runs at module-evaluation time, before the first /api/
 *     fetch): read `token` from the URL (?token=… or #token=…). If present, store
 *     it (sessionStorage + a Strict cookie) and STRIP it from the visible URL via
 *     history.replaceState so it is never left in the address bar / shared. If
 *     absent, recover any token previously stored this session (sessionStorage,
 *     then cookie).
 *
 *   • THE COOKIE: a `mv_token` cookie is set whenever a token is known. fetch()
 *     can header-auth, but an <img> (the grouped-view /api/folder-image thumbs and
 *     the PWA icons) cannot — only the cookie makes those carry the token. The
 *     cookie + the X-MediaVault-Token header are redundant on fetch() (belt and
 *     braces); the cookie is the ONLY thing covering <img>.
 *
 *   • authFetch(url, opts): the single wrapper every /api/* fetch() goes through.
 *     It injects the X-MediaVault-Token header when a token is known, keeps
 *     credentials:"same-origin" (default) so the cookie rides along, and — if the
 *     server answers 401 — shows a mobile-friendly TOKEN PROMPT overlay and PARKS
 *     the request until the user supplies a token, then transparently retries the
 *     SAME request once. Callers therefore need no 401 logic of their own: an
 *     initial load or a button action that hits 401 simply recovers after the user
 *     pastes the token (and re-prompts, with an error note, on a wrong token).
 *
 * AUTH CONTRACT (server side, implemented in parallel):
 *   token configured  -> every /api/* needs the token via the mv_token cookie OR
 *                        the X-MediaVault-Token header OR a ?token= query param,
 *                        else 401 {"detail":"Token required"}. STATIC files are
 *                        never gated (the page always loads).
 *   no token           -> no auth (today's behavior); the SPA must work untouched.
 *   local auto-open    -> the server opens the browser with ?token=… (frictionless
 *                        local: this bootstrap captures it, no prompt ever shows).
 *
 * XSS-safe: the prompt overlay is built with createElement + textContent only; no
 * untrusted string is ever interpolated as HTML. The entered/stored token is used
 * ONLY as a header value, a cookie value (encodeURIComponent), and the input's
 * .value — never inserted into the DOM as markup.
 */

"use strict";

// sessionStorage + cookie key. Matches the server-side cookie name in the
// contract (mv_token). Session-scoped on purpose: "remembered for the session"
// per the remote UX, and it clears when the tab/PWA is closed.
var TOKEN_KEY = "mv_token";

// In-memory cache of the active token (source of truth at runtime; the storage
// is just persistence). null/"" means "no token known".
var currentToken = "";

// ---------------------------------------------------------------------------
// Storage helpers (defensive: sessionStorage can throw in private mode / when
// the quota is exhausted; cookies can be disabled). Never let a storage failure
// break auth — the in-memory token still works for the rest of the session.
// ---------------------------------------------------------------------------

function readStoredToken() {
  // Prefer sessionStorage (set by us); fall back to the cookie (covers a PWA
  // relaunch where the cookie persisted but sessionStorage was cleared, or a
  // page the server itself cookie-stamped).
  try {
    var v = window.sessionStorage.getItem(TOKEN_KEY);
    if (v) return v;
  } catch (e) {
    /* sessionStorage unavailable — fall through to the cookie. */
  }
  return readCookieToken();
}

function readCookieToken() {
  try {
    var parts = (document.cookie || "").split(";");
    for (var i = 0; i < parts.length; i += 1) {
      var p = parts[i].trim();
      if (p.indexOf(TOKEN_KEY + "=") === 0) {
        return decodeURIComponent(p.slice(TOKEN_KEY.length + 1));
      }
    }
  } catch (e) {
    /* cookies disabled / unreadable. */
  }
  return "";
}

// Persist a token to BOTH sessionStorage and the cookie. The cookie is what lets
// <img> requests (folder thumbnails, icons) carry the token — fetch alone can't
// header-auth an <img>. SameSite=Strict + path=/ scopes it to this origin's
// same-site navigations only.
function storeToken(token) {
  currentToken = token || "";
  try {
    if (currentToken) {
      window.sessionStorage.setItem(TOKEN_KEY, currentToken);
    } else {
      window.sessionStorage.removeItem(TOKEN_KEY);
    }
  } catch (e) {
    /* keep the in-memory token; persistence is best-effort. */
  }
  setCookie(currentToken);
}

function setCookie(token) {
  try {
    // No Max-Age/Expires => a session cookie (cleared when the browser/PWA
    // closes), matching the session-scoped sessionStorage above.
    document.cookie =
      TOKEN_KEY + "=" + encodeURIComponent(token || "") + ";path=/;samesite=Strict";
  } catch (e) {
    /* cookies disabled — fetch() still header-auths; only <img> would miss it. */
  }
}

export function getToken() {
  return currentToken;
}

// ---------------------------------------------------------------------------
// Bootstrap — capture ?token= / #token= from the URL, persist it, strip it from
// the visible URL; otherwise recover a stored token. Exported so app.js can call
// it explicitly at the very top of init() too (idempotent), but it ALSO runs at
// module load (below) so it is guaranteed to precede the first /api/ fetch.
// ---------------------------------------------------------------------------

var _bootstrapped = false;

export function bootstrapToken() {
  if (_bootstrapped) return currentToken;
  _bootstrapped = true;

  var fromUrl = tokenFromUrl();
  if (fromUrl) {
    storeToken(fromUrl);
    stripTokenFromUrl();
  } else {
    // No token in the URL — recover any token stored earlier this session and
    // re-affirm the cookie (so a sessionStorage-only token still reaches <img>).
    var stored = readStoredToken();
    if (stored) storeToken(stored);
  }
  return currentToken;
}

// Read `token` from either the query string (?token=…) or the hash (#token=… or
// #…&token=…). The server auto-opens with ?token=; the hash form is supported so
// a token shared via a fragment (never sent to the server in the request line)
// also works.
function tokenFromUrl() {
  try {
    var fromSearch = paramFrom(window.location.search || "");
    if (fromSearch) return fromSearch;
    var hash = window.location.hash || "";
    // Hash may be "#token=…" or "#demo&token=…"; normalize the leading '#'.
    return paramFrom(hash.charAt(0) === "#" ? "?" + hash.slice(1) : hash);
  } catch (e) {
    return "";
  }
}

function paramFrom(qs) {
  if (!qs) return "";
  try {
    // URLSearchParams handles a leading '?' and decoding. Guard for very old
    // engines by falling back to a manual scan.
    if (typeof URLSearchParams === "function") {
      var sp = new URLSearchParams(qs.charAt(0) === "?" ? qs.slice(1) : qs);
      return sp.get("token") || "";
    }
  } catch (e) {
    /* fall through to manual parse */
  }
  var clean = qs.charAt(0) === "?" || qs.charAt(0) === "#" ? qs.slice(1) : qs;
  var pairs = clean.split("&");
  for (var i = 0; i < pairs.length; i += 1) {
    var kv = pairs[i].split("=");
    if (kv[0] === "token") return decodeURIComponent((kv[1] || "").replace(/\+/g, " "));
  }
  return "";
}

// Remove the token from BOTH the query and the hash of the visible URL without a
// navigation/reload (history.replaceState), so the secret is not left in the
// address bar, history, or anything the user might copy-share. Other params
// (e.g. ?demo) are preserved.
function stripTokenFromUrl() {
  try {
    if (!window.history || typeof window.history.replaceState !== "function") return;
    var loc = window.location;
    var search = stripParam(loc.search || "", "token");
    var hash = stripHashToken(loc.hash || "");
    var next = loc.pathname + search + hash;
    window.history.replaceState(null, document.title, next);
  } catch (e) {
    /* replaceState unavailable — the token stays in the URL but auth still works. */
  }
}

function stripParam(search, key) {
  if (!search || search === "?") return "";
  var clean = search.charAt(0) === "?" ? search.slice(1) : search;
  var kept = clean.split("&").filter(function (pair) {
    return pair && pair.split("=")[0] !== key;
  });
  return kept.length ? "?" + kept.join("&") : "";
}

function stripHashToken(hash) {
  if (!hash || hash === "#") return "";
  var clean = hash.charAt(0) === "#" ? hash.slice(1) : hash;
  // A bare "#demo" (no '=') is a flag, not a token param — keep it intact.
  if (clean.indexOf("=") === -1) return "#" + clean;
  var kept = clean.split("&").filter(function (pair) {
    return pair && pair.split("=")[0] !== "token";
  });
  return kept.length ? "#" + kept.join("&") : "";
}

// ---------------------------------------------------------------------------
// authFetch — the single wrapper for every /api/* fetch().
// ---------------------------------------------------------------------------

// Inject the token header into an options object without mutating the caller's.
function withTokenHeader(opts) {
  var next = Object.assign({}, opts || {});
  var headers = Object.assign({}, (opts && opts.headers) || {});
  if (currentToken) headers["X-MediaVault-Token"] = currentToken;
  next.headers = headers;
  // credentials defaults to "same-origin"; set it explicitly so the cookie is
  // unambiguously sent even if a caller passed other options.
  if (next.credentials == null) next.credentials = "same-origin";
  return next;
}

// fetch() that carries the token (header + cookie) and, on a 401, shows the token
// prompt, waits for the user to supply a token, then retries the SAME request
// ONCE. Every concurrent 401 parks on the same prompt; a wrong token re-prompts
// with an error note. Non-401 responses (and network errors) pass straight
// through so callers keep their existing status/`res.ok` handling untouched.
export function authFetch(url, opts) {
  return fetch(url, withTokenHeader(opts)).then(function (res) {
    if (res.status !== 401) {
      // A non-401 means the current token (if any) is accepted: clear the
      // wrong-token signal so a LATER genuine 401 opens a fresh prompt with no
      // stale error note.
      _awaitingRetry = false;
      return res;
    }
    // 401: the server requires a token (or the one we sent is wrong). Park on the
    // shared prompt; when a token arrives, retry the original request. A retry that
    // 401s again re-parks (recursively), re-opening the prompt with the error note
    // so the user can correct a wrong token — this is the only intended recursion.
    return promptForToken(res).then(function () {
      return authFetch(url, opts);
    });
  });
}

// ---------------------------------------------------------------------------
// Token prompt overlay (mobile-friendly, dark-theme, XSS-safe). Single instance,
// built lazily and reused. All concurrent 401s share ONE prompt: callers get a
// promise that resolves when the user submits a token (after it is stored), so
// each parked request then retries with the new token.
// ---------------------------------------------------------------------------

var _promptEl = null; // the backdrop element (reused)
var _promptWaiters = []; // resolve callbacks of every parked authFetch
// True once the user has submitted a token AND the resulting retries have not yet
// succeeded — i.e. the NEXT 401 to arrive is a wrong-token retry, so the error
// note should show. Reset when a fresh prompt opens cleanly (no prior submit) and
// when any /api/ request finally succeeds.
var _awaitingRetry = false;

// Park on the shared prompt for a 401. Returns a promise that resolves once the
// user submits a non-empty token (stored before we resolve), so the caller can
// retry its original request. `res` only signals a 401; we intentionally do NOT
// interpolate the server's detail string into the DOM (static copy, XSS-safe).
function promptForToken(res) {
  void res;
  return new Promise(function (resolve) {
    _promptWaiters.push(resolve);
    // A 401 arriving while we are awaiting a retry means the just-submitted token
    // was wrong → show the error note. The flag is the reliable signal (the prompt
    // is briefly hidden between submit and the retry's response, so "is it open"
    // would miss this).
    showPrompt(_awaitingRetry);
  });
}

function ensurePromptEl() {
  if (_promptEl) return _promptEl;

  var backdrop = document.createElement("div");
  backdrop.className = "auth-backdrop";
  backdrop.setAttribute("role", "dialog");
  backdrop.setAttribute("aria-modal", "true");
  backdrop.setAttribute("aria-labelledby", "auth-title");

  var card = document.createElement("div");
  card.className = "auth-modal";

  var head = document.createElement("div");
  head.className = "auth-head";
  var icon = document.createElement("div");
  icon.className = "auth-icon";
  icon.setAttribute("aria-hidden", "true");
  icon.textContent = "🔒"; // 🔒
  head.appendChild(icon);
  var h2 = document.createElement("h2");
  h2.id = "auth-title";
  h2.textContent = "Access token required";
  head.appendChild(h2);
  card.appendChild(head);

  var body = document.createElement("div");
  body.className = "auth-body";
  var p = document.createElement("p");
  p.className = "auth-line";
  p.textContent =
    "This MediaVault console requires an access token (set in mvconfig.json on the Alienware).";
  body.appendChild(p);

  // The error note (hidden until a wrong token is submitted). textContent only.
  var note = document.createElement("p");
  note.className = "auth-note";
  note.id = "auth-note";
  note.setAttribute("role", "alert");
  note.hidden = true;
  note.textContent = "That token was not accepted. Check it and try again.";
  body.appendChild(note);

  var input = document.createElement("input");
  input.type = "password";
  input.className = "auth-input provider-input";
  input.id = "auth-input";
  input.autocomplete = "off";
  input.spellcheck = false;
  input.setAttribute("autocapitalize", "off");
  input.setAttribute("autocorrect", "off");
  input.setAttribute("aria-label", "Access token");
  input.placeholder = "Paste your access token";
  body.appendChild(input);
  card.appendChild(body);

  var actions = document.createElement("div");
  actions.className = "auth-actions";
  var connect = document.createElement("button");
  connect.type = "button";
  connect.className = "auth-connect";
  connect.textContent = "Connect";
  actions.appendChild(connect);
  card.appendChild(actions);

  backdrop.appendChild(card);
  document.body.appendChild(backdrop);

  // Submit on click or Enter.
  connect.addEventListener("click", function () {
    submitPrompt(input);
  });
  input.addEventListener("keydown", function (e) {
    if (e.key === "Enter") {
      e.preventDefault();
      submitPrompt(input);
    }
  });

  _promptEl = backdrop;
  _promptEl._input = input;
  _promptEl._note = note;
  return _promptEl;
}

function showPrompt(isReprompt) {
  var el = ensurePromptEl();
  // Surface the error note when this open is a wrong-token re-prompt. Once shown
  // for an error it stays shown until a successful submit clears it (so a second
  // concurrent waiter arriving in the same error wave keeps the note visible).
  el._note.hidden = !isReprompt;
  el.classList.add("show");
  // Focus the input so a paste-and-go is one tap on mobile. Defer a frame so the
  // element is laid out (and not stolen by the show transition).
  window.requestAnimationFrame(function () {
    try {
      el._input.focus();
    } catch (e) {
      /* focus is a nicety; never let it throw. */
    }
  });
}

function hidePrompt() {
  if (!_promptEl) return;
  _promptEl.classList.remove("show");
}

// User pressed Connect / Enter. A blank entry is a no-op (keep waiting). A real
// token is stored, the prompt is closed, the wrong-token signal armed, and EVERY
// parked waiter resolved so each retries its original request with the new token.
function submitPrompt(input) {
  var value = (input.value || "").trim();
  if (!value) {
    // Nudge the field; do not resolve waiters (nothing to retry with yet).
    try {
      input.focus();
    } catch (e) {
      /* ignore */
    }
    return;
  }
  storeToken(value);
  hidePrompt();
  input.value = ""; // don't leave the secret sitting in the DOM input
  // We have handed out a token; the next 401 (if any) is a wrong-token retry, so
  // arm the error-note signal. Cleared on the first successful /api/ response.
  _awaitingRetry = true;

  // Resolve all parked waiters so each retries its original request with the new
  // token. If a retry 401s again, it re-enters promptForToken, which re-opens the
  // prompt with the error note (because _awaitingRetry is now true). Snapshot +
  // clear the queue first so a synchronous re-park starts on a clean list.
  var waiters = _promptWaiters;
  _promptWaiters = [];
  waiters.forEach(function (resolve) {
    resolve();
  });
}

// Run the bootstrap NOW, at module evaluation. Because app.js imports this module
// (transitively, via being the entry that imports data.js/card.js which import
// it, AND directly), this executes before app.js's init() fires the first /api/
// fetch — guaranteeing the token + cookie are in place first.
bootstrapToken();
