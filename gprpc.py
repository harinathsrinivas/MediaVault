"""gprpc — the ONE read-only Google Photos RPC client (IMP-C25 Step 2b; OD-9 ruling).

Every internal Google Photos call MediaVault makes goes through call() in this module — no other module
builds a batchexecute request (PLAN Step 2b convention) — and every answer is read by gpweb's typed
parsers only. Consumers: the inventory crawler (Step 7), the locator and the direct download (Steps
10–12, P-20), check_photos and the post-push auto-confirm (Steps 13/13b), resolve_photos (Step 14).

Safety contract (PLAN P-9, P11–P13, P-20)
  * READ-ONLY. Only the rpcids in ALLOWED_RPCIDS are ever sent. Any other id — the mutating ids of PLAN
    P13 included — raises RpcRefused before the driver is touched, and the check is repeated in the one
    function that sends. Nothing in IMP-C25 needs a mutating call (pinned by tests/test_gprpc.py).
  * Page context. Each request is a fetch() made by the signed-in photos.google.com tab of the
    automation Chrome (credentials: 'include'), so the cookies never leave the browser. The page tokens
    (WIZ_global_data) are read per request into local variables and sent to photos.google.com only.
    Tokens, the account email and download URLs are never printed, logged, stored or put into an
    exception message, and gprpc raises nothing of its own while a frame holds one of them — so not even
    a locals-capturing traceback of a gprpc error shows them. gprpc prints nothing and writes no file.
  * Paced: at least RPC_MIN_INTERVAL_S + jitter between two requests. Retried: RPC_RETRIES times with
    backoff on HTTP 429 / 5xx / no answer envelope / a failed page-context request. Circuit-broken:
    CIRCUIT_BREAKER_FAILURES consecutive failed attempts turn RPC off for the rest of that browser
    session (RpcUnavailable, no further request). Consumers print summary_line(driver) once per run.
  * Evidence, not authority: callers still verify identity (dedupKey / name), and SHA-256 after a
    download stays the final gate.
  * No Selenium import: gprpc drives the driver object it is given; mainfetch (the session check) is
    imported lazily. Not thread-safe — one thread per driver, like Selenium itself.

Errors
  RpcRefused        the rpcid is not allowlisted — a MediaVault bug; nothing was sent. Deliberately NOT an
                    RpcUnavailable, so a "no RPC answer -> UI path" handler can never swallow it.
  RpcUnavailable    no usable RPC answer — use the UI path (the one fallback trigger consumers catch).
                    Raised as itself for missing page tokens, exhausted retries, a non-retryable HTTP
                    status or an open circuit breaker; two subclasses name a more specific cause:
    RpcShapeError   an answer arrived but is not the documented shape (fail closed — never guess).
    RpcServerError  the server answered with an error status instead of a payload (.code);
                    STATUS_NOT_FOUND is the "no such item" answer the helpers turn into None.
  mainfetch.SessionExpiredError propagates unchanged: the Chrome profile is signed out — or, as its subclass
  mainfetch.PhotosThrottledError (IMP-C25 thfix), Google showed the tab another page than Google Photos.

Public API
  call(driver, rpcid, request_data)         one allowlisted RPC -> its typed gpweb parser's result
  hash_lookup(driver, sha1_hex_list)        swbisb: which items hold exactly these bytes
  item_info(driver, photo_id)               VrseUb + fDcn4b: one item's record, file name and size
  batch_info(driver, photo_ids)             EWgK9e: file name, size, times, quality for many items
  timeline_at(driver, taken_ms, ...)        lcxiM: a timeline page by taken time (or from the newest)
  search(driver, query, max_pages=3)        EzkLib: exactly what the web UI's search box returns
  recent_uploads(driver, stop_at_ids)       EzkLib: the recently-uploaded listing, until a known id
  download_url(driver, photo_id)            P-20: a fresh original download link, as a redacted DownloadUrl
  signed_in_account(driver)                 the signed-in email, for a terminal-only identity check
  stats(driver) / summary_line(driver)      this browser session's RPC accounting
"""
import json
import random
import re
import time
import urllib.parse

import gpweb

# ============================================================================================ policy

ALLOWED_RPCIDS = frozenset({"lcxiM", "EzkLib", "swbisb", "VrseUb", "fDcn4b", "EWgK9e", "pLFTfd"})  # PLAN P12

RPC_MIN_INTERVAL_S = 0.5         # P-9: at least this long between two requests …
RPC_JITTER_S = 0.25              # … plus up to this much random jitter (also added to every backoff)
RPC_RETRIES = 3                  # P-9: retries after a retryable failure — at most 4 attempts per call
RPC_BACKOFF_S = (1.0, 2.0, 4.0)  # the wait before retry 1, 2, 3
CIRCUIT_BREAKER_FAILURES = 5     # P-9: consecutive failed attempts that turn RPC off for the session
MAX_BATCH_KEYS = 50              # P-9: at most this many hashes / ids per swbisb / EWgK9e request
MAX_PAGE_SIZE = 500              # PLAN P12: the lcxiM pageSize limit
TIMELINE_SOURCE = 3              # PLAN P12: lcxiM source — 1 library · 2 archive · 3 both
STATUS_NOT_FOUND = 5             # the error status believed to mean "no such item" (UNVERIFIED — 🚦 R2)
RECENT_UPLOADS_MAX_PAGES = 500   # safety cap for recent_uploads() (a first run pages the whole library)

# The clock: module attributes, so tests substitute a fake clock (tests/test_gprpc.py).
_monotonic = time.monotonic
_sleep = time.sleep
_uniform = random.uniform

_PHOTOS_URL = "https://photos.google.com"  # == mainfetch.PHOTOS_URL (drift-pinned by a test)
_PHOTOS_HOST = urllib.parse.urlparse(_PHOTOS_URL).hostname


# ============================================================================================ errors

class RpcRefused(Exception):
    """The rpcid is not on the read-only allowlist (ALLOWED_RPCIDS). Raised before the driver is
    touched — nothing was sent. Deliberately NOT an RpcUnavailable: a refused id is a MediaVault bug to
    fix, never a reason to fall back to the UI path."""


class RpcUnavailable(Exception):
    """No usable RPC answer for this request — the caller falls back to the UI path. Raised as itself
    when the page tokens are missing, the retries are exhausted, the answer had a non-retryable HTTP
    status, or the circuit breaker is open (then for the rest of this browser session); the two
    subclasses below name a more specific cause. Messages never carry a token, the account email, a
    download URL or answer content."""


class RpcShapeError(RpcUnavailable):
    """An answer arrived but is not the documented shape: its typed gpweb parser rejected it, the
    payload is not JSON, every record failed its signature, or two answers contradict each other. Fail
    closed — nothing is ever guessed from it."""


class RpcServerError(RpcUnavailable):
    """The server answered this request with an error status instead of a payload (gpweb
    EmptyPayloadError). `code` is that status, or None; STATUS_NOT_FOUND means "no such item"
    (UNVERIFIED — 🚦 R2)."""

    def __init__(self, rpcid, code=None):
        super().__init__(f"{rpcid} answered with an error status ({code})")
        self.rpcid = rpcid
        self.code = code

    @property
    def not_found(self):
        return self.code == STATUS_NOT_FOUND


# ====================================================================================== download URL

class DownloadUrl:
    """A freshly minted original-quality download link (P-20) — a short-lived BEARER CREDENTIAL.

    Redacted by construction: str(), repr() and format() — so print, logging, f-strings, exception
    messages and tracebacks — show `DownloadUrl(<redacted>, …)`, and json.dumps() refuses it. reveal()
    is the one way to the value: pass it straight to driver.get() and drop it. Never store it — it
    expires within about an hour (P14) and would be a secret in any file (CLAUDE.md §5)."""

    __slots__ = ("_url", "source")

    def __init__(self, url, source):
        self._url = url
        self.source = source  # the rpcid that minted it: "VrseUb" (item info [7]) or "pLFTfd"

    def reveal(self):
        """The link itself — for driver.get() only."""
        return self._url

    def __repr__(self):
        return f"DownloadUrl(<redacted>, source={self.source!r})"

    __str__ = __repr__


def _wrap_link(url, source):
    """The one place a raw link becomes a DownloadUrl (None for no link)."""
    return DownloadUrl(url, source) if url else None


# ================================================================================ session accounting

class _Session:
    """The RPC accounting of one browser session (one driver)."""

    __slots__ = ("calls", "errors", "consecutive_failures", "breaker_open", "last_request_end")

    def __init__(self):
        self.calls = 0                  # attempts (retries included; refused / short-circuited calls not)
        self.errors = 0                 # attempts that gave no answer ("not found" is an answer)
        self.consecutive_failures = 0
        self.breaker_open = False
        self.last_request_end = None    # _monotonic() when the previous request ended

    def succeeded(self):
        self.consecutive_failures = 0

    def failed(self):
        self.errors += 1
        self.consecutive_failures += 1
        if self.consecutive_failures >= CIRCUIT_BREAKER_FAILURES:
            self.breaker_open = True


_SESSION_ATTR = "_mv_rpc_session"  # kept on the driver object, like mainfetch's _mv_zero_streak


def _session(driver):
    session = getattr(driver, _SESSION_ATTR, None)
    if not isinstance(session, _Session):
        session = _Session()
        setattr(driver, _SESSION_ATTR, session)
    return session


def stats(driver):
    """This browser session's RPC accounting -> {calls, errors, breaker_open}. calls = request attempts
    (retries included; refused ids and calls short-circuited by an open breaker excluded); errors =
    attempts that gave no answer (a "not found" answer is an answer)."""
    session = _session(driver)
    return {"calls": session.calls, "errors": session.errors, "breaker_open": session.breaker_open}


def summary_line(driver):
    """The one end-of-run line, `RPC calls: N (errors E)` — consumers print it once per browser session.
    It never contains a P-13 web progress marker."""
    session = _session(driver)
    return f"RPC calls: {session.calls} (errors {session.errors})"


# ========================================================================= the request (PLAN P11)

_WIZ_KEYS = ("eptZe", "FdrFJe", "cfb2h", "SNlM0e")  # endpoint path · f.sid · bl · the XSRF `at` token
_WIZ_JS = ("const w = window.WIZ_global_data || {};\n"
           "return {eptZe: w.eptZe ?? null, FdrFJe: w.FdrFJe ?? null, cfb2h: w.cfb2h ?? null, "
           "SNlM0e: w.SNlM0e ?? null};")
_ACCOUNT_JS = "const w = window.WIZ_global_data || {};\nreturn {oPEP7c: w.oPEP7c ?? null};"
_ENDPOINT_PATH_RE = re.compile(r"/(?:[A-Za-z0-9._~-]+/)*")  # eptZe, e.g. "/_/PhotosUi/" — a plain path
_FETCH_JS = """
const url = arguments[0], body = arguments[1], done = arguments[arguments.length - 1];
fetch(url, {method: 'POST', credentials: 'include', body: body,
            headers: {'content-type': 'application/x-www-form-urlencoded;charset=UTF-8'}})
  .then(r => r.text().then(t => done({status: r.status, text: t})))
  .catch(e => done({status: 0, text: String(e)}));
"""


def _require_allowed(rpcid):
    """PLAN P-9 / P13: only the read-only allowlist is ever sent."""
    if not (isinstance(rpcid, str) and rpcid in ALLOWED_RPCIDS):
        raise RpcRefused(f"rpcid {rpcid!r} is not on the read-only allowlist — refused, nothing was sent")


def _mainfetch():
    import mainfetch  # lazy: mainfetch imports Selenium at import time, gprpc must not (Step 2b)
    return mainfetch


def _on_photos(url):
    return isinstance(url, str) and urllib.parse.urlparse(url).hostname == _PHOTOS_HOST


def _photos_url(driver):
    """The tab's current photos.google.com URL — the tab is first sent to the Photos start page when it
    shows anything else — after mainfetch.check_session_alive (SessionExpiredError when signed out; its
    PhotosThrottledError when Google shows another page). [IMP-C25 thfix] The check runs for EVERY request, not
    only after a navigation: on Google Photos it notes that this browser session is signed in, so a later bounce to
    Google Photos' signed-out page reads as a throttle, not as a sign-out (an RPC-only session navigates nowhere).
    A browser fault propagates as raised."""
    url = driver.current_url
    if not _on_photos(url):
        driver.get(_PHOTOS_URL)
        url = None
    _mainfetch().check_session_alive(driver)
    return url if url is not None else driver.current_url


def _pace(session):
    """Wait until RPC_MIN_INTERVAL_S + jitter have passed since this session's previous request ended."""
    if session.last_request_end is not None:
        wait = session.last_request_end + RPC_MIN_INTERVAL_S + _uniform(0, RPC_JITTER_S) - _monotonic()
        if wait > 0:
            _sleep(wait)


def _tokens(wiz):
    """The P11 page tokens from a WIZ_global_data read, or None when one is missing or the endpoint path
    is not a plain path (the XSRF token must only ever go to photos.google.com)."""
    if not isinstance(wiz, dict):
        return None
    tokens = {k: wiz.get(k) for k in _WIZ_KEYS}
    if not all(isinstance(v, str) and v for v in tokens.values()):
        return None
    return tokens if _ENDPOINT_PATH_RE.fullmatch(tokens["eptZe"]) else None


def _quote(value):
    return urllib.parse.quote(value, safe="")


def _request(rpcid, inner_json, tokens, source_path):
    """PLAN P11 -> (url, form body) of one batchexecute request."""
    query = "&".join(f"{_quote(k)}={_quote(v)}" for k, v in (
        ("rpcids", rpcid), ("source-path", source_path), ("f.sid", tokens["FdrFJe"]),
        ("bl", tokens["cfb2h"]), ("pageId", "none"), ("rt", "c")))
    f_req = json.dumps([[[rpcid, inner_json, None, "generic"]]], separators=(",", ":"), ensure_ascii=False)
    return (f"{_PHOTOS_URL}{tokens['eptZe']}data/batchexecute?{query}",
            f"f.req={_quote(f_req)}&at={_quote(tokens['SNlM0e'])}&")


def _exchange(driver, session, rpcid, inner_json):
    """One request/answer exchange -> ("ok", result) · ("retry", reason) · ("fail", RpcUnavailable).

    It raises only RpcRefused (first thing) and SessionExpiredError (before the tokens are read). The
    tokens, the request and the raw answer live in this frame alone, and it has returned before call()
    raises anything — so no traceback of a gprpc error can hold them."""
    _require_allowed(rpcid)  # the second allowlist check, in the one function that sends (P-9)
    mf = _mainfetch()
    try:
        page_url = _photos_url(driver)
    except mf.SessionExpiredError:
        raise
    except Exception as e:
        return "retry", f"the tab could not be put on Google Photos ({type(e).__name__})"
    if not _on_photos(page_url):
        return "retry", "the tab is not on Google Photos"
    _pace(session)
    try:
        wiz = driver.execute_script(_WIZ_JS)
    except Exception as e:
        return "retry", f"the page tokens could not be read ({type(e).__name__})"
    tokens = _tokens(wiz)
    if tokens is None:
        return "fail", RpcUnavailable("the Google Photos page tokens (WIZ_global_data) are missing or "
                                      "unexpected — use the UI path")
    url, body = _request(rpcid, inner_json, tokens, urllib.parse.urlparse(page_url).path or "/")
    try:
        answer = driver.execute_async_script(_FETCH_JS, url, body)
    except Exception as e:
        return "retry", f"the page-context request failed ({type(e).__name__})"
    finally:
        session.last_request_end = _monotonic()
    status = answer.get("status") if isinstance(answer, dict) else None
    text = answer.get("text") if isinstance(answer, dict) else None
    if not (isinstance(status, int) and not isinstance(status, bool)):
        return "retry", "the page-context request returned no HTTP status"
    if status == 0:
        return "retry", "the request did not complete (network error)"
    if status == 429 or 500 <= status <= 599:
        return "retry", f"HTTP {status}"
    if status != 200:
        return "fail", RpcUnavailable(f"{rpcid} answered HTTP {status} — use the UI path")
    try:
        payload = gpweb.rpc_payload(text if isinstance(text, str) else "", rpcid)
    except gpweb.NoEnvelopeError:
        return "retry", "no wrb.fr envelope in the answer"
    except gpweb.EmptyPayloadError as e:
        return "fail", RpcServerError(rpcid, e.code)
    except gpweb.MalformedPayloadError:
        return "fail", RpcShapeError(f"{rpcid}: the answer's payload is not JSON")
    result = gpweb.PARSERS[rpcid](payload)
    if result is None:
        return "fail", RpcShapeError(f"{rpcid}: the answer is not the documented shape")
    records = result["items"] if "items" in result else result.get("matches")
    if result.get("rejected") and not records:
        return "fail", RpcShapeError(f"{rpcid}: all {result['rejected']} record(s) of the answer failed their "
                                     "signature")
    return "ok", result


def call(driver, rpcid, request_data):
    """Send ONE allowlisted read-only RPC from the signed-in Photos tab (PLAN P11) and return the typed
    gpweb parser's result for that rpcid (P12): lcxiM / EzkLib -> {items, next_page_id, …}, swbisb ->
    {matches, rejected}, VrseUb / fDcn4b -> the item dict, EWgK9e -> {items, rejected}, pLFTfd ->
    {download_url}. The helpers below wrap it; RPC traffic never goes around it.

    Raises RpcRefused for an id outside ALLOWED_RPCIDS and TypeError / ValueError for a request that is
    not JSON-serialisable — both before the driver is touched — then RpcUnavailable, RpcShapeError,
    RpcServerError or mainfetch.SessionExpiredError."""
    _require_allowed(rpcid)  # the first allowlist check: nothing has touched the driver yet (P-9)
    inner_json = json.dumps(request_data, separators=(",", ":"), ensure_ascii=False)
    session = _session(driver)
    reason = None
    for attempt in range(RPC_RETRIES + 1):
        if session.breaker_open:
            raise RpcUnavailable(f"{rpcid}: RPC is off for this browser session after "
                                 f"{CIRCUIT_BREAKER_FAILURES} consecutive failures — use the UI path")
        if attempt:
            _sleep(RPC_BACKOFF_S[min(attempt, len(RPC_BACKOFF_S)) - 1] + _uniform(0, RPC_JITTER_S))
        session.calls += 1
        kind, value = _exchange(driver, session, rpcid, inner_json)
        if kind == "ok":
            session.succeeded()
            return value
        if kind == "fail" and isinstance(value, RpcServerError) and value.not_found:
            session.succeeded()  # a definitive answer about the item, not a failure of the RPC path
            raise value
        session.failed()
        if kind == "fail":
            raise value
        reason = value
    raise RpcUnavailable(f"{rpcid}: no answer after {RPC_RETRIES + 1} attempts (last: {reason})")


# =========================================================================================== helpers

_RECORD_KEYS = ("photo_id", "width", "height", "taken_ms", "dedup_key", "tz_offset_ms", "upload_ms",
                "partial_upload", "archived", "duration_ms")
_SHA1_HEX_RE = re.compile(r"[0-9a-f]{40}")


def _record(rec):
    """The one item-record shape the helpers return: the PLAN P3 fields + the P12 flags (None = unknown)."""
    return {k: rec.get(k) for k in _RECORD_KEYS}


def _is_int(value):
    return isinstance(value, int) and not isinstance(value, bool)


def _require_media_key(photo_id):
    if not (isinstance(photo_id, str) and gpweb.MEDIA_KEY_RE.fullmatch(photo_id)):
        raise ValueError("not a Google Photos media key (photo_id)")


def _require_count(name, value, limit=None):
    if not (_is_int(value) and value >= 1 and (limit is None or value <= limit)):
        raise ValueError(f"{name} must be a whole number from 1" + (f" to {limit}" if limit else ""))


def _contradiction(driver, message):
    """Answers that contradict each other: counted as a failed attempt (it feeds the circuit breaker)
    and returned as the RpcShapeError to raise."""
    _session(driver).failed()
    return RpcShapeError(message)


def _pages(driver, rpcid, request_for, max_pages):
    """Successive pages of a paged answer ({…, next_page_id}) — up to the last page or max_pages. A page
    id the server already handed out is a paging loop: RpcShapeError, never followed."""
    page_id, seen = None, set()
    for _ in range(max_pages):
        if page_id is not None:
            if page_id in seen:
                raise _contradiction(driver, f"{rpcid} handed out the same page id twice — paging loop not followed")
            seen.add(page_id)
        page = call(driver, rpcid, request_for(page_id))
        yield page
        page_id = page["next_page_id"]
        if page_id is None:
            return


def hash_lookup(driver, sha1_hex_list):
    """Which items of this account hold exactly these bytes? (swbisb, PLAN P12 — locator stage S0b,
    check_photos batch confirm) -> {sha1_hex: [record, …]} for every distinct requested SHA-1 (lower-case
    hex, request order) — [] when the account holds no such bytes.

    A record is kept only when its OWN dedupKey is the requested hash (content proof, P-5); an answer
    pairing a hash with another item is dropped. The request carries each SHA-1 as STANDARD base64 with
    padding (P12), at most MAX_BATCH_KEYS per request. ValueError for a malformed digest — raised before
    anything is sent."""
    found, by_key = {}, {}
    for digest in sha1_hex_list:
        hx = digest.strip().lower() if isinstance(digest, str) else ""
        if not _SHA1_HEX_RE.fullmatch(hx):
            raise ValueError("hash_lookup needs SHA-1 hex digests (40 hex characters each)")
        if hx not in found:
            found[hx] = []
            by_key[gpweb.normalize_dedup_key(gpweb.sha1_hex_to_std_b64(hx))] = hx
    digests = list(found)
    for i in range(0, len(digests), MAX_BATCH_KEYS):
        batch = [gpweb.sha1_hex_to_std_b64(hx) for hx in digests[i:i + MAX_BATCH_KEYS]]
        answer = call(driver, "swbisb", [batch, None, 3, 0])
        for match in answer["matches"]:
            hx = by_key.get(gpweb.normalize_dedup_key(match["dedup_key"]))
            if hx is not None and all(r["photo_id"] != match["photo_id"] for r in found[hx]):
                found[hx].append(_record(match))
    return found


def _vrseub(driver, photo_id):
    """VrseUb for one item -> (record, DownloadUrl | None), or (None, None) when this account has no such
    item. The answer's links leave it at once — the original-download link wrapped, the display link
    dropped — so no raw URL outlives this function (P-20)."""
    try:
        answer = call(driver, "VrseUb", [photo_id, None, None, None, None])
    except RpcServerError as e:
        if e.not_found:
            return None, None
        raise
    link = _wrap_link(answer.get("download_original_url"), "VrseUb")
    record = _record(answer)
    answer.clear()  # the raw links go no further than this line
    if record["photo_id"] != photo_id:
        raise _contradiction(driver, "VrseUb answered about another item than the one asked for")
    return record, link


def item_info(driver, photo_id):
    """What this account holds for one item (VrseUb + fDcn4b, PLAN P12 — locator stage S0a,
    resolve_photos) -> {photo_id, width, height, taken_ms, dedup_key, tz_offset_ms, upload_ms,
    partial_upload, archived, duration_ms, filename, size_bytes}, or None when the account has no such
    item (STATUS_NOT_FOUND). filename / size_bytes are None when the item-info-ext call answers an error
    status (it serves the own library only). Both answers must name the same item with the same dedupKey,
    else RpcShapeError. Never contains a URL (see download_url); quality comes from batch_info."""
    _require_media_key(photo_id)
    info, _link = _vrseub(driver, photo_id)
    if info is None:
        return None
    info["filename"] = info["size_bytes"] = None
    try:
        ext = call(driver, "fDcn4b", [photo_id, 1, None, None, 1])
    except RpcServerError:
        return info
    if ext["photo_id"] != photo_id or not gpweb.dedup_keys_equal(ext["dedup_key"], info["dedup_key"]):
        raise _contradiction(driver, "the two item-info answers disagree about the item's identity")
    info["filename"], info["size_bytes"] = ext["filename"], ext["size_bytes"]
    if info["tz_offset_ms"] is None:
        info["tz_offset_ms"] = ext["tz_offset_ms"]
    return info


def batch_info(driver, photo_ids):
    """File name, size, times and quality for many items (EWgK9e, PLAN P12 — crawler details,
    check_photos) -> {photo_id: {photo_id, filename, size_bytes, taken_ms, tz_offset_ms, upload_ms,
    original_quality} | None} for every distinct requested id (request order); None when the answer left
    the id out. At most MAX_BATCH_KEYS ids per request. ValueError for a malformed id — raised before
    anything is sent."""
    found = {}
    for photo_id in photo_ids:
        _require_media_key(photo_id)
        found.setdefault(photo_id, None)
    ids = list(found)
    for i in range(0, len(ids), MAX_BATCH_KEYS):
        field_mask = [None] * 24 + [[]] + [None] * 11 + [[]]  # PLAN P12
        answer = call(driver, "EWgK9e", [[[[[k] for k in ids[i:i + MAX_BATCH_KEYS]]], [field_mask]]])
        for item in answer["items"]:
            if item["photo_id"] in found and found[item["photo_id"]] is None:
                found[item["photo_id"]] = dict(item)
    return found


def timeline_at(driver, taken_ms, page_size=MAX_PAGE_SIZE, page_id=None):
    """One timeline page by taken time (lcxiM, source 3 = library + archive; PLAN P12): the items taken
    at or before `taken_ms`, newest first — the locator's stage S1 page, whose first items share the
    object's taken second — or, with taken_ms None, from the newest item (the crawler's full
    enumeration). Pass the previous page's next_page_id as `page_id` to continue.
    -> {items: [record, …], next_page_id, last_timestamp_ms, rejected}."""
    if not (taken_ms is None or _is_int(taken_ms)):
        raise ValueError("taken_ms must be a whole number of ms since the epoch, or None")
    _require_count("page_size", page_size, MAX_PAGE_SIZE)
    if not (page_id is None or (isinstance(page_id, str) and page_id)):
        raise ValueError("page_id must be the next_page_id of a previous page, or None")
    page = call(driver, "lcxiM", [page_id, taken_ms, page_size, None, 1, TIMELINE_SOURCE])
    return {"items": [_record(r) for r in page["items"]], "next_page_id": page["next_page_id"],
            "last_timestamp_ms": page["last_timestamp_ms"], "rejected": page["rejected"]}


def search(driver, query, max_pages=3):
    """The web UI's text search (EzkLib [query, null, pageId], PLAN P12) — the results the search box
    shows, paged until they end or max_pages were read -> {items: [record, …] (result order, one per
    photo_id), pages, truncated (more results exist beyond max_pages), rejected}."""
    if not (isinstance(query, str) and query.strip()):
        raise ValueError("search needs a non-empty query")
    _require_count("max_pages", max_pages)
    items, seen, pages, rejected, page = [], set(), 0, 0, None
    for page in _pages(driver, "EzkLib", lambda page_id: [query, None, page_id], max_pages):
        pages += 1
        rejected += page["rejected"]
        for rec in page["items"]:
            if rec["photo_id"] not in seen:
                seen.add(rec["photo_id"])
                items.append(_record(rec))
    return {"items": items, "pages": pages, "truncated": page["next_page_id"] is not None,
            "rejected": rejected}


def recent_uploads(driver, stop_at_ids, max_pages=RECENT_UPLOADS_MAX_PAGES):
    """The recently-uploaded listing (EzkLib ['', [[4, 'ra', 0, 0]], pageId], newest upload first; PLAN
    P12) — the crawler's incremental pass. Pages until a page holds an id from `stop_at_ids` (everything
    older is known already), the listing ends, or max_pages were read -> {items: [record, …] (every id
    NOT in stop_at_ids, listing order), pages, complete (a known id or the end was reached), rejected}."""
    if isinstance(stop_at_ids, (str, bytes)):
        raise TypeError("stop_at_ids must be a collection of photo ids, not one string")
    known = set(stop_at_ids)
    _require_count("max_pages", max_pages)
    items, seen, pages, rejected, complete = [], set(), 0, 0, False
    for page in _pages(driver, "EzkLib", lambda page_id: ["", [[4, "ra", 0, 0]], page_id], max_pages):
        pages += 1
        rejected += page["rejected"]
        reached_known = False
        for rec in page["items"]:
            if rec["photo_id"] in known:
                reached_known = True
            elif rec["photo_id"] not in seen:
                seen.add(rec["photo_id"])
                items.append(_record(rec))
        if reached_known or page["next_page_id"] is None:
            complete = True
            break
    return {"items": items, "pages": pages, "complete": complete, "rejected": rejected}


def download_url(driver, photo_id):
    """P-20 direct download: a FRESH original-quality download link for one item -> DownloadUrl, or None
    when this account has no such item or will not mint a link (the caller falls back to Shift+D).
    The item-info link (VrseUb [7], P14) first, else pLFTfd with exactly ONE key (several keys would
    mint a zip — P14). Verify the item first (item_info + its dedupKey); SHA-256 after the download stays
    the final gate. Mint per fetch and never store the link: it expires within about an hour."""
    _require_media_key(photo_id)
    info, link = _vrseub(driver, photo_id)
    if info is None or link is not None:
        return link
    try:
        minted = call(driver, "pLFTfd", [[photo_id], None, None])
    except RpcServerError:
        return None
    return _wrap_link(minted.pop("download_url"), "pLFTfd")


def signed_in_account(driver):
    """The Google account this automation Chrome is signed in to — WIZ_global_data.oPEP7c, its email
    (P11) — or None when the page does not show one. For a TERMINAL-ONLY identity check ("this profile
    is signed in as …"): gprpc never prints, logs or stores it, and the caller must never write it to a
    file. Opens Google Photos first when needed (SessionExpiredError when signed out). Sends no request."""
    _photos_url(driver)
    got = driver.execute_script(_ACCOUNT_JS)
    email = got.get("oPEP7c") if isinstance(got, dict) else None
    return email if isinstance(email, str) and "@" in email else None
