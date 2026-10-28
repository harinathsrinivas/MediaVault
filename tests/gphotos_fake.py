"""Fake Google Photos — the test double every IMP-C25 test runs against (PLAN Step 2j).

A deterministic in-memory model of the Google Photos web app for the four accounts, driven through the
exact Selenium surface MediaVault uses. No network, no browser, no real ~/Downloads: every file it writes
must land under the world's root (the test's tmp_path) — anything else fails loudly.

WORLD — FakePhotos(root)  (the `fake_photos` fixture in tests/conftest.py builds one)
  add_item(account, filename, data=…, taken='YYYY-MM-DDTHH:MM:SSZ' | taken_ms=…, tz_offset_min=480, …)
      -> FakeItem  (photo_id, filename = the exact uploaded name, bytes → sha1 / dedup_key / sha256, taken_ms,
                    tz_offset_ms, upload_ms, width/height, duration, archived, partial_upload,
                    original_quality, thumb_shape "short"|"long", deleted = a stale photo_id)
  driver(account) -> FakeDriver     one Chrome session on that account's profile
  search(account, query)            the UI / EzkLib search semantics (below)
  accounts: movies renders UK English, tv / anime / others US English (PLAN P4; anime observed US).
  knobs:  session[account] = "ok" | "expired" (→ accounts.google.com) | "signed_out" (→ the about page) | "sorry"
          (→ Google's "unusual traffic" page — IMP-C25 thfix) · throttle(account, after=N, rpc="zero" | "error") /
          unthrottle(account) (IMP-C25 thfix, the R3 evidence of 2026-10-27: after N searches every further text or
          day search of the account answers nothing — the search box shows "No results", EzkLib an empty page or,
          rpc="error", an error frame; timelines, hash look-ups and item pages keep working)
          wiz_token_missing[account] · download_mode "instant" | "manual" (finish_downloads()) | "delayed"
          shift_d_requires_click (Shift+D ignored on a directly loaded /photo/ page) · render_requires_focus
          (grids render only after Page.bringToFront + focus emulation — RESEARCH F14) · direct_mode "ok" |
          "html" | "wrong_bytes" · mint_refused · vrseub_original_url · refuse_download_redirect ·
          viewport_tiles · scroll_step_tiles · ezklib_page_size · escape_slashes_in_pages · now (year omission)
          script_search(account, query, photo_ids) · fail_next_rpc(kind, count=1, rpcid=None) ·
          expire_download_urls() · swbisb_compact / ewgk9e_live (IMP-C25 Step 14 d: answer swbisb / EWgK9e in the
          layouts a live probe of a real item showed on 2026-10-14 — compact_array, batch_info_row(live=True))
  logs:   rpc_log (every batchexecute call MediaVault SENT, refused ids included) · downloads

SEARCH SEMANTICS — scripted results first; an ISO day 'YYYY-MM-DD' → the items taken on that LOCAL date
(RESEARCH F22); any other text → a substring of the normalized uploaded name (NFKC, whitespace runs
collapsed, casefold). Results are newest first (taken, then upload time). A plain library filename without
its ' [short_id]' tag therefore finds nothing — the F25 zero-result case.

DRIVER SURFACE — get · current_url · page_source · title · find_element(s): body (live innerText), result
anchors (`a[href*='…']` — search pages show `./search/<token>/photo/<id>` tiles while the `./photo/<id>`
timeline anchors behind them are hidden: RESEARCH F14), `//div[contains(@style,'background-image')]`
thumbnails, `[aria-label='Open info']`-style buttons · execute_script: gpweb.TILES_JS, gpweb.SCROLL_JS,
gpweb.SEARCH_STATE_JS / gpweb.TIMELINE_LINKS_JS (IMP-C35: results vs timeline links; a background-image div
sits inside its tile's link), "arguments[0].click();", scripts naming WIZ_global_data,
document.visibilityState / hasFocus() — any other
script raises NotImplementedError (extend the fake explicitly) · execute_async_script: the batchexecute
emulator · execute_cdp_cmd: Page.bringToFront, Emulation.setFocusEmulationEnabled,
Browser/Page.setDownloadBehavior, Network.enable, Network.getResponseBody · get_log("performance") ·
execute(W3C actions): real selenium ActionChains work unmodified — keys "/", text, ENTER, Shift+D, "i",
ESC and pointer clicks on elements, recorded in driver.actions (semantic) and driver.key_log (raw) ·
window_handles / current_window_handle / switch_to.new_window / switch_to.window / close / quit · back ·
service.stop · session_id. Elements subclass selenium's WebElement (ActionChains.move_to_element works).

DOWNLOADS — Shift+D on an item page, or driver.get(<minted download URL>), writes `<name>.crdownload`
then renames it to `<name>` (Chrome's ` (n)` suffix when the name is taken) in the directory set by
Browser/Page.setDownloadBehavior (default: <root>/Downloads).

BATCHEXECUTE EMULATOR — the Step 2b page-context call. CONTRACT (gprpc must follow it):
  execute_script(<js naming WIZ_global_data>) -> {key: value} for every WIZ key the script names
      (eptZe, FdrFJe, cfb2h, SNlM0e, oPEP7c — the account email; all keys if none is named), or None when
      the tab is not on a signed-in photos.google.com page; SNlM0e is absent in wiz_token_missing mode.
  execute_async_script(<js>, url, form_body) -> {"status": int, "text": str}
      url  = https://photos.google.com{eptZe}data/batchexecute?rpcids=<id>&source-path=…&f.sid=…&bl=…&pageId=none&rt=c
      form = f.req=<urlencoded JSON [[[rpcid, JSON(request), null, "generic"]]]>&at=<urlencoded SNlM0e>&
      (url / form may instead be embedded in the script text). Not on a signed-in photos page →
      {"status": 0, "text": "TypeError: Failed to fetch"}.
  Serves only PLAN P12's read-only ids from the account's items; every other id (the P13 mutating ids
  included) is logged and refused with HTTP 400. Wrong / missing `at` token → 400.
  swbisb matches the STANDARD padded base64 SHA-1 exactly; lcxiM pages newest first from items taken at or
  before the timestamp; EzkLib = the UI search, or the recently-uploaded listing (newest upload first);
  VrseUb [7] / pLFTfd answer minted URLs whose driver.get downloads (direct_mode) — minting refused
  (mint_refused) is an error frame. Unknown or deleted ids answer an error frame with status 5 (the real
  code for "not found" is UNVERIFIED). Failure modes (fail_next_rpc): http429, http500, no_envelope,
  changed_shape (payload becomes a JSON object), changed_items (every record's fields moved), empty_payload.
"""
import base64
import hashlib
import html
import itertools
import json
import os
import re
import threading
import time
import unicodedata
import urllib.parse
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from selenium.common.exceptions import (JavascriptException, NoSuchElementException, NoSuchWindowException,
                                        StaleElementReferenceException, WebDriverException)
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.remote.command import Command
from selenium.webdriver.remote.webelement import WebElement

import gpweb  # the SHARED page scripts only (TILES_JS / SCROLL_JS / SEARCH_STATE_JS / TIMELINE_LINKS_JS); every fact below is computed here

PHOTOS_URL = "https://photos.google.com"
ACCOUNT_LOCALES = {"movies": "UK", "tv": "US", "anime": "US", "others": "US"}
READ_ONLY_RPCIDS = ("lcxiM", "EzkLib", "swbisb", "VrseUb", "fDcn4b", "EWgK9e", "pLFTfd")      # PLAN P12
MUTATING_RPCIDS = frozenset({"XwAOJf", "StLnCe", "Pp2Xxe", "ycV3Nd", "OXvT9d", "E1Cajb", "LjmOue", "V8RKJ",
                             "Es7fke", "EtUHOe", "DaSgWe", "AQNOFd", "yCLA7", "dnv2s"})                # PLAN P13
RPC_FAILURES = ("http429", "http500", "no_envelope", "changed_shape", "changed_items", "empty_payload")
WIZ_KEYS = ("eptZe", "FdrFJe", "cfb2h", "SNlM0e", "oPEP7c")
DOWNLOAD_HOST = "video-downloads.fake.invalid"
THUMB_HOST = "lh3.fake.invalid"
SIGNIN_URL = "https://accounts.google.com/v3/signin/identifier?continue=https%3A%2F%2Fphotos.google.com%2F"
ABOUT_URL = "https://www.google.com/photos/about/"
SORRY_URL = "https://www.google.com/sorry/index"  # Google's "unusual traffic" page (its real query string left out)
NOT_FOUND = 5  # error-frame status for an unknown / deleted id (UNVERIFIED against the real service)
THROTTLED = 8  # error-frame status of a throttled EzkLib search, rpc="error" (UNVERIFIED: the live code was not logged)

NNBSP = "\N{NARROW NO-BREAK SPACE}"  # before AM/PM in US labels and panels (RESEARCH F12)
EN_DASH = "\N{EN DASH}"               # the UK label separator
TIMES = "\N{MULTIPLICATION SIGN}"     # "1920 × 1080"
_US_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
_UK_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sept", "Oct", "Nov", "Dec")
_WEEKDAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
_IMAGE_EXTS = (".jpg", ".jpeg", ".png", ".heic", ".webp", ".gif")
_EPOCH = datetime(1970, 1, 1)
_ER_BODY = ")]}'\n\n[[\"er\",null,null,null,null,400,null,null,null,3],[\"di\",10],[\"af.httprm\",10,\"-1\",1]]\n"


def _norm(s):
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", s or "")).strip().casefold()


def _to_ms(taken):
    if isinstance(taken, datetime):
        dt = taken if taken.tzinfo else taken.replace(tzinfo=timezone.utc)
    else:
        dt = datetime.strptime(taken, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    return (dt - datetime(1970, 1, 1, tzinfo=timezone.utc)) // timedelta(milliseconds=1)


def _gmt_text(offset_ms):
    minutes = round(offset_ms / 60000)
    if minutes == 0:
        return "GMT"
    h, m = divmod(abs(minutes), 60)
    return f"GMT{'+' if minutes > 0 else '-'}{h:02d}:{m:02d}"


def _search_token(query):
    return "FAKEQ" + base64.urlsafe_b64encode(query.encode("utf-8")).decode("ascii").rstrip("=")


def _query_from_token(token):
    if token.startswith("FAKEQ"):
        raw = token[5:] + "=" * (-len(token[5:]) % 4)
        try:
            return base64.urlsafe_b64decode(raw).decode("utf-8")
        except ValueError:
            return ""
    return urllib.parse.unquote(token)


@dataclass
class FakeItem:
    account: str
    photo_id: str
    filename: str                 # the exact uploaded name (what the info panel shows)
    data: bytes                   # the uploaded bytes
    taken_ms: int
    tz_offset_ms: int
    upload_ms: int
    width: int = 1920
    height: int = 1080
    duration_ms: int = 1_320_000
    archived: bool = False
    partial_upload: bool = False
    original_quality: bool = True
    thumb_shape: str = "short"    # "short" ["url",w,h] | "long" ["url",w,h,null…,[n]] (commit 70635cb)
    deleted: bool = False         # a stale photo_id: pages and RPCs answer "not found"
    seq: int = 0

    @property
    def sha1_hex(self):
        return hashlib.sha1(self.data).hexdigest()

    @property
    def sha256_hex(self):
        return hashlib.sha256(self.data).hexdigest()

    @property
    def dedup_key(self):  # computed here, independently of the code under test (RESEARCH F21)
        return base64.urlsafe_b64encode(hashlib.sha1(self.data).digest()).decode("ascii").rstrip("=")

    @property
    def hash_b64(self):   # the swbisb request format: STANDARD base64, padded
        return base64.b64encode(hashlib.sha1(self.data).digest()).decode("ascii")

    @property
    def size_bytes(self):
        return len(self.data)

    @property
    def local(self):
        """The naive local time Google Photos displays for the item."""
        return _EPOCH + timedelta(milliseconds=self.taken_ms + self.tz_offset_ms)

    @property
    def orientation(self):
        return "Landscape" if self.width > self.height else "Portrait" if self.height > self.width else "Square"

    @property
    def kind(self):
        return "Photo" if self.filename.lower().endswith(_IMAGE_EXTS) else "Video"


def item_array(item):
    """The P3 library item as pages and RPC answers carry it (PLAN P3/P12): [0] photo_id, [1] thumbnail,
    [2] taken_ms, [3] dedupKey, [4] tz_offset_ms, [5] upload_ms, [12] [20] when partially uploaded,
    [13] archived, last element the extension object (76647426 → duration)."""
    thumb = [f"https://{THUMB_HOST}/p/{item.photo_id}", item.width, item.height]
    if item.thumb_shape == "long":
        thumb += [None] * 6 + [[item.size_bytes % 1_000_000]]
    return [item.photo_id, thumb, item.taken_ms, item.dedup_key, item.tz_offset_ms, item.upload_ms,
            None, None, None, None, None, None, [20] if item.partial_upload else None, item.archived, None,
            {"76647426": [item.duration_ms]}]


def ext_array(item):
    """Item-info-ext (fDcn4b payload[0]): [0] photo_id, [2] file name, [3] taken_ms, [4] tz_offset_ms,
    [5] size, [6]/[7] width/height, [11] dedupKey (PLAN P3/P12, Step 2 text)."""
    return [item.photo_id, None, item.filename, item.taken_ms, item.tz_offset_ms, item.size_bytes,
            item.width, item.height, None, None, None, item.dedup_key]


def compact_array(item):
    """The swbisb record as a live probe of a real item showed it (2026-10-14, IMP-C25 Step 14 d): COMPACT and
    field-masked — dense only up to index 8 ([0]–[5] the P3 fields, [6] / [7] null although the full record sets [7],
    [8] 2), every later field in a trailing object keyed by FIELD NUMBER ("15": the value the full record holds at [14];
    "76647426": the duration extension). Partial upload / archived are NOT carried."""
    return item_array(item)[:6] + [None, None, 2, {"15": 4242, "76647426": [item.duration_ms]}]


def batch_info_row(item, live=False):
    """EWgK9e per item (PLAN P12): [photo_id, [_, _, description, file name, _, _, taken_ms, tz_ms,
    upload_ms, size, …, [takesUpSpace, spaceTaken, quality]]] — the triple is both index 30 and last.
    live=True: the layout a live probe showed (2026-10-14, IMP-C25 Step 14 d) — a 26-entry info array with NO quality
    triple ([10] 100, [18] 2, [23] 1, [25] an int) and a 6-entry row."""
    if live:
        info = [None, None, "", item.filename, None, None, item.taken_ms, item.tz_offset_ms, item.upload_ms,
                item.size_bytes, 100] + [None] * 7 + [2] + [None] * 4 + [1, None, 4242]
        return [item.photo_id, info, None, None, None, [None, None]]
    triple = [0, 0, 2] if item.original_quality else [1, item.size_bytes, 1]
    info = [None, None, "", item.filename, None, None, item.taken_ms, item.tz_offset_ms, item.upload_ms,
            item.size_bytes] + [None] * 20 + [triple]
    return [item.photo_id, info]


def envelope(rpcid, payload=None, code=None):
    """A batchexecute answer body (rt=c): XSSI guard, then length-prefixed JSON chunks; payload None →
    an error frame carrying `code`."""
    text = None if payload is None else json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
    frame = ["wrb.fr", rpcid, text, None, None, [code] if code is not None else None, "generic"]
    chunk = json.dumps([frame, ["di", 17], ["af.httprm", 16, "-1000000000000000000", 3]],
                       separators=(",", ":"), ensure_ascii=False)
    tail = f'[["e",4,null,null,{len(chunk) + 2}]]'
    return f")]}}'\n\n{len(chunk)}\n{chunk}\n{len(tail)}\n{tail}\n"


def _scramble(obj):
    """changed_items: move every record's fields so that no typed parser accepts them."""
    if isinstance(obj, dict):
        return {k: _scramble(v) for k, v in obj.items()}
    if not isinstance(obj, list):
        return obj
    out = [_scramble(v) for v in obj]
    if out and isinstance(out[0], str) and out[0].startswith("AF1QipFAKE"):
        if len(out) >= 4:
            out[2], out[3] = out[3], out[2]
        elif len(out) == 2:
            out[0], out[1] = out[1], out[0]
    return out


class FakeAccount:
    def __init__(self, name, locale):
        self.name, self.locale = name, locale
        self.items = {}      # photo_id -> FakeItem, insertion order
        self.scripted = {}   # normalized query -> [photo_id, …]
        self.email = f"fake.{name}@example.invalid"
        digits = int(hashlib.sha1(name.encode()).hexdigest()[:15], 16)
        self.wiz = {"eptZe": "/_/PhotosUi/", "FdrFJe": f"-{digits}", "cfb2h": f"boq_photosuiserver_fake_{name}",
                    "SNlM0e": f"FAKE-XSRF-{name.upper()}:1790000000000", "oPEP7c": self.email}


class FakePhotos:
    """The fake Google Photos world: accounts, items, knobs and logs (see the module docstring)."""

    def __init__(self, root, now=None, downloads_dir=None):
        self.root = os.path.abspath(str(root))
        self.now = now or datetime(2026, 9, 28, 12, 0, 0)
        self.default_download_dir = os.path.abspath(str(downloads_dir or os.path.join(self.root, "Downloads")))
        self._guard_path(self.default_download_dir)
        self.accounts = {name: FakeAccount(name, loc) for name, loc in ACCOUNT_LOCALES.items()}
        self.session = {name: "ok" for name in self.accounts}
        self.wiz_token_missing = {name: False for name in self.accounts}
        self.download_mode = "instant"
        self.download_delay_s = 0.2
        self.shift_d_requires_click = False
        self.render_requires_focus = False
        self.direct_mode = "ok"
        self.mint_refused = False
        self.vrseub_original_url = True
        self.refuse_download_redirect = False
        self.viewport_tiles = 24
        self.scroll_step_tiles = 12
        self.ezklib_page_size = 50
        self.escape_slashes_in_pages = False
        self.swbisb_compact = False   # swbisb records in the live compact layout (compact_array)
        self.ewgk9e_live = False      # EWgK9e rows in the live layout (batch_info_row(live=True))
        self.throttles = {}           # account -> {"after", "rpc", "served"} (throttle(); IMP-C25 thfix)
        self.rpc_log = []
        self.downloads = []
        self.minted = {}
        self._failures = []
        self._timers = []
        self._seq = itertools.count(1)
        self._mint_seq = itertools.count(1)
        self._drivers = itertools.count(1)
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ items
    def add_item(self, account, filename, data=None, taken=None, taken_ms=None, tz_offset_min=480,
                 tz_offset_ms=None, upload_ms=None, **attrs):
        n = next(self._seq)
        acc = self.accounts[account]
        pid = ("AF1QipFAKE" + account[:3].upper() + f"{n:06d}"
               + hashlib.sha1(f"{account}:{n}:{filename}".encode()).hexdigest()[:25])
        if taken_ms is None:
            taken_ms = _to_ms(taken) if taken is not None else 1644061786000 + n * 60_000
        tz = tz_offset_ms if tz_offset_ms is not None else tz_offset_min * 60_000
        item = FakeItem(account=account, photo_id=pid, filename=filename,
                        data=data if data is not None else f"FAKE-BYTES:{account}:{n}:{filename}".encode(),
                        taken_ms=taken_ms, tz_offset_ms=tz,
                        upload_ms=upload_ms if upload_ms is not None else taken_ms + 86_400_000,
                        seq=n, **attrs)
        acc.items[pid] = item
        return item

    def item(self, photo_id):
        return next((a.items[photo_id] for a in self.accounts.values() if photo_id in a.items), None)

    def items(self, account):
        return list(self.accounts[account].items.values())

    def driver(self, account="tv"):
        return FakeDriver(self, account, next(self._drivers))

    # ------------------------------------------------------------------ listings
    @staticmethod
    def _newest_first(items):
        return sorted(items, key=lambda it: (-it.taken_ms, -it.upload_ms, it.seq))

    def _live(self, account):
        return [it for it in self.accounts[account].items.values() if not it.deleted]

    def timeline(self, account):
        return self._newest_first(it for it in self._live(account) if not it.archived)

    def search(self, account, query):
        acc = self.accounts[account]
        q = (query or "").strip()
        key = _norm(q)
        if key in acc.scripted:
            return [acc.items[p] for p in acc.scripted[key] if p in acc.items and not acc.items[p].deleted]
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", q):
            return self._newest_first(it for it in self._live(account) if it.local.date().isoformat() == q)
        if not key:
            return []
        return self._newest_first(it for it in self._live(account) if key in _norm(it.filename))

    def script_search(self, account, query, photo_ids):
        """Make `query` return exactly these items in this order (the UI and EzkLib alike)."""
        self.accounts[account].scripted[_norm(query)] = list(photo_ids)

    # ------------------------------------------------------------------ the search throttle (IMP-C25 thfix)
    def throttle(self, account, after=0, rpc="zero"):
        """From now on the account answers its first `after` searches as usual, then every further one with
        nothing — the R3 dry runs of 2026-10-27: a search box submit (a text or a day search) shows "No results",
        an EzkLib text search (its first page) answers an empty page (rpc="zero") or an error frame with status
        THROTTLED (rpc="error": what the live RPC did). The recently-uploaded listing, timelines (lcxiM), hash
        look-ups, item info and item pages keep working."""
        if rpc not in ("zero", "error"):
            raise ValueError(f"rpc must be 'zero' or 'error', not {rpc!r}")
        self.throttles[account] = {"after": after, "rpc": rpc, "served": 0}

    def unthrottle(self, account):
        self.throttles.pop(account, None)

    def _throttled_search(self, account):
        """Count one search served for `account`; True when it is throttled (beyond the throttle's `after`)."""
        t = self.throttles.get(account)
        if t is None:
            return False
        t["served"] += 1
        return t["served"] > t["after"]

    # ------------------------------------------------------------------ RPC failure injection
    def fail_next_rpc(self, kind, count=1, rpcid=None):
        if kind not in RPC_FAILURES:
            raise ValueError(f"unknown failure kind {kind!r}; one of {RPC_FAILURES}")
        self._failures.append({"kind": kind, "rpcid": rpcid, "left": count})

    def _take_failure(self, rpcid):
        for f in self._failures:
            if f["rpcid"] in (None, rpcid):
                f["left"] -= 1
                if f["left"] <= 0:
                    self._failures.remove(f)
                return f["kind"]
        return None

    def rpc_ids_sent(self):
        return [e["rpcid"] for e in self.rpc_log]

    # ------------------------------------------------------------------ downloads
    def _guard_path(self, path):
        p = os.path.abspath(str(path))
        try:
            inside = os.path.normcase(os.path.commonpath([p, self.root])) == os.path.normcase(self.root)
        except ValueError:  # different drives (e.g. a --basetemp on D:)
            inside = False
        if not inside:
            raise AssertionError(f"fake Google Photos refuses to write outside its root {self.root!r}: {p!r}")
        return p

    def _complete(self, rec):
        with self._lock:
            if rec["completed"] or not os.path.exists(rec["partial"]):
                rec["cancelled"] = not rec["completed"]
                return
            with open(rec["partial"], "wb") as fh:
                fh.write(rec["data"])
            os.replace(rec["partial"], rec["path"])
            rec["completed"] = True

    def finish_downloads(self):
        for rec in list(self.downloads):
            if not rec["completed"]:
                self._complete(rec)

    def close(self):
        for t in self._timers:
            t.cancel()

    def _mint(self, account, keys, kind):
        n = next(self._mint_seq)
        token = f"FAKE{kind.upper()}{n:04d}" + hashlib.sha1(f"{account}:{keys}:{n}".encode()).hexdigest()[:16]
        self.minted[token] = {"account": account, "keys": list(keys), "kind": kind}
        return f"https://{DOWNLOAD_HOST}/{kind}/{token}"

    def expire_download_urls(self):
        self.minted.clear()

    # ------------------------------------------------------------------ batchexecute emulator
    def _batchexecute(self, account, url, form_body):
        acc = self.accounts[account]
        parsed = urllib.parse.urlparse(url)
        params = {k: v[0] for k, v in urllib.parse.parse_qs(parsed.query, keep_blank_values=True).items()}
        form = {k: v[0] for k, v in urllib.parse.parse_qs(form_body, keep_blank_values=True).items()}
        rpcid = params.get("rpcids")
        at_ok = (not self.wiz_token_missing[account]) and form.get("at") == acc.wiz["SNlM0e"]
        entry = {"account": account, "rpcid": rpcid, "request": None, "at_ok": at_ok, "status": None,
                 "path": parsed.path, "params": params}
        self.rpc_log.append(entry)
        try:
            inner = json.loads(form["f.req"])[0][0]
            entry["rpcid_freq"] = inner[0]
            entry["request"] = json.loads(inner[1]) if inner[1] is not None else None
        except (KeyError, IndexError, TypeError, ValueError):
            return self._http(entry, 400, _ER_BODY)
        if (entry["rpcid_freq"] != rpcid or (parsed.hostname or "") != "photos.google.com"
                or parsed.path != acc.wiz["eptZe"] + "data/batchexecute"):
            return self._http(entry, 400, _ER_BODY)
        failure = self._take_failure(rpcid)
        if failure == "http429":
            return self._http(entry, 429, "<html><body>Error 429 (Too Many Requests)</body></html>")
        if failure == "http500":
            return self._http(entry, 500, "<html><body>Error 500 (Server Error)</body></html>")
        if failure == "no_envelope":
            return self._http(entry, 200, ")]}'\n\n25\n[[\"e\",4,null,null,25]]\n")
        handler = {"lcxiM": self._h_lcxiM, "EzkLib": self._h_EzkLib, "swbisb": self._h_swbisb,
                   "VrseUb": self._h_VrseUb, "fDcn4b": self._h_fDcn4b, "EWgK9e": self._h_EWgK9e,
                   "pLFTfd": self._h_pLFTfd}.get(rpcid)
        if not at_ok or handler is None:
            return self._http(entry, 400, _ER_BODY)  # mutating / unknown ids are logged and refused
        payload, code = handler(acc, entry["request"])
        if failure == "empty_payload":
            payload, code = None, 13
        elif failure == "changed_shape" and payload is not None:
            payload = {"changed": payload}
        elif failure == "changed_items" and payload is not None:
            payload = _scramble(payload)
        return self._http(entry, 200, envelope(rpcid, payload, code))

    @staticmethod
    def _http(entry, status, text):
        entry["status"] = status
        return {"status": status, "text": text}

    @staticmethod
    def _page(kind, items, start, size):
        chunk = items[start:start + size]
        nxt = f"FAKEP:{kind}:{start + size}" if start + size < len(items) else None
        return chunk, nxt

    @staticmethod
    def _offset(page_id, kind):
        if isinstance(page_id, str) and page_id.startswith(f"FAKEP:{kind}:"):
            return int(page_id.rsplit(":", 1)[1])
        return None

    def _h_lcxiM(self, acc, req):
        r = (req if isinstance(req, list) else []) + [None] * 6
        page_id, ts, size, source = r[0], r[1], r[2], r[5]
        size = min(size if isinstance(size, int) and size > 0 else 500, 500)
        live = self._newest_first(self._live(acc.name))
        items = [it for it in live if source not in (1, 2) or it.archived == (source == 2)]
        start = self._offset(page_id, "lcxiM")
        if start is None:
            start = next((i for i, it in enumerate(items) if ts is None or it.taken_ms <= ts), len(items))
        chunk, nxt = self._page("lcxiM", items, start, size)
        return [[item_array(it) for it in chunk], nxt, chunk[-1].taken_ms if chunk else None], None

    def _h_EzkLib(self, acc, req):
        req = req if isinstance(req, list) else []
        query, filters, page_id = (req + [None] * 3)[:3]
        if filters == [[4, "ra", 0, 0]]:
            items = sorted(self._live(acc.name), key=lambda it: (-it.upload_ms, it.seq))
        elif page_id is None and self._throttled_search(acc.name):   # a new text search, throttled (IMP-C25 thfix)
            if self.throttles[acc.name]["rpc"] == "error":
                return None, THROTTLED
            items = []
        else:
            items = self.search(acc.name, query if isinstance(query, str) else "")
        start = self._offset(page_id, "EzkLib") or 0
        chunk, nxt = self._page("EzkLib", items, start, self.ezklib_page_size)
        return [[item_array(it) for it in chunk], nxt], None

    def _h_swbisb(self, acc, req):
        hashes = req[0] if isinstance(req, list) and req and isinstance(req[0], list) else []
        record = compact_array if self.swbisb_compact else item_array
        pairs = [[h, record(it)] for h in hashes for it in self._newest_first(self._live(acc.name))
                 if isinstance(h, str) and it.hash_b64 == h]
        return ([pairs] if pairs else []), None

    def _live_item(self, acc, key):
        it = acc.items.get(key) if isinstance(key, str) else None
        return it if it is not None and not it.deleted else None

    def _vrseub_payload(self, acc, item):
        orig = self._mint(acc.name, [item.photo_id], "orig") if self.vrseub_original_url else None
        return [item_array(item), f"https://{THUMB_HOST}/d/{item.photo_id}", None, None, None, None, None, orig]

    def _h_VrseUb(self, acc, req):
        item = self._live_item(acc, req[0] if isinstance(req, list) and req else None)
        return (self._vrseub_payload(acc, item), None) if item else (None, NOT_FOUND)

    def _h_fDcn4b(self, acc, req):
        item = self._live_item(acc, req[0] if isinstance(req, list) and req else None)
        return ([ext_array(item)], None) if item else (None, NOT_FOUND)

    def _h_EWgK9e(self, acc, req):
        try:
            wrapped = req[0][0][0]  # [[k1],[k2],…] — the P12 request shape
        except (IndexError, KeyError, TypeError):
            wrapped = []
        keys = [k[0] for k in (wrapped if isinstance(wrapped, list) else []) if isinstance(k, list) and k]
        rows = [batch_info_row(it, live=self.ewgk9e_live) for it in (self._live_item(acc, k) for k in keys) if it]
        return [[None, rows]], None

    def _h_pLFTfd(self, acc, req):
        keys = req[0] if isinstance(req, list) and req and isinstance(req[0], list) else []
        if self.mint_refused or not keys:
            return None, 7 if self.mint_refused else 3
        return [self._mint(acc.name, keys, "dl")], None


class _FakeService:
    def __init__(self):
        self.stopped = False

    def stop(self):
        self.stopped = True


class _FakeSwitchTo:
    def __init__(self, driver):
        self._d = driver

    def new_window(self, type_hint=None):
        self._d._check_alive()
        self._d._save_tab()
        handle = f"FAKE-TAB-{len(self._d._tabs) + 1}-{next(self._d._handle_seq)}"
        self._d._handles.append(handle)
        self._d._current_handle = handle
        self._d._reset_tab()
        return None

    def window(self, handle):
        self._d._check_alive(require_window=False)
        if handle not in self._d._handles:
            raise NoSuchWindowException(f"fake: no such window {handle!r}")
        if self._d._current_handle is not None:
            self._d._save_tab()
        self._d._current_handle = handle
        self._d._load_tab(handle)


class FakeElement(WebElement):
    """A DOM element of the fake page (a WebElement, so ActionChains accepts it)."""

    _ids = itertools.count(1)

    def __init__(self, driver, kind, attrs=None, text="", displayed=True, width=200, height=200, target=None):
        super().__init__(driver, f"fake-el-{next(FakeElement._ids)}")
        self.kind, self.attrs, self._text = kind, dict(attrs or {}), text
        self._displayed, self._w, self._h, self.target = displayed, width, height, target
        self.page_serial = driver._page_serial
        driver._elements[self.id] = self

    @property
    def text(self):
        return self._parent._body_text() if self.kind == "body" else self._text

    @property
    def tag_name(self):
        return {"anchor": "a", "bgdiv": "div", "button": "button", "body": "body"}[self.kind]

    def is_displayed(self):
        return self._displayed

    @property
    def size(self):
        return {"width": self._w, "height": self._h}

    @property
    def rect(self):
        return {"x": 0, "y": 0, "width": self._w, "height": self._h}

    @property
    def location(self):
        return {"x": 0, "y": 0}

    def get_attribute(self, name):
        return self.attrs.get(name)

    def get_dom_attribute(self, name):
        return self.attrs.get(name)

    def click(self):
        self._parent._click(self)


class FakeDriver:
    """One automation Chrome session on one account's profile (see the module docstring)."""

    _TAB_STATE = ("url", "page", "history", "search_focused", "search_buffer", "scroll_offset", "_page_serial")

    def __init__(self, world, account, n):
        self.world, self.account = world, account
        self.session_id = f"fake-session-{account}-{n}"
        self.service = _FakeService()
        self.switch_to = _FakeSwitchTo(self)
        self._handle_seq = itertools.count(1)
        self._handles = ["FAKE-TAB-1"]
        self._current_handle = "FAKE-TAB-1"
        self._tabs = {}
        self.quit_called = False
        self.panel_open = False           # sticky for the session, like the real info panel
        self.shift_down = False
        self.fronted = False
        self.focus_emulated = False
        self.download_dir = world.default_download_dir
        self.downloads_allowed = True
        self.download_events = False
        self.actions = []                 # semantic recorder: search_focus / search / shift_d / info_toggle / escape / open
        self.key_log = []                 # raw W3C key events
        self.cdp_log = []
        self.script_log = []
        self.unknown_selectors = []
        self._elements = {}
        self._pointer_target = None
        self._perf, self._bodies, self._req_seq = [], {}, 0
        self._page_serial = 0
        self._reset_tab()

    # ------------------------------------------------------------------ tabs / lifecycle
    def _reset_tab(self):
        self.url, self.page, self.history = "about:blank", {"kind": "blank"}, []
        self.search_focused, self.search_buffer, self.scroll_offset = False, "", 0
        self._page_serial += 1

    def _save_tab(self):
        self._tabs[self._current_handle] = {a: getattr(self, a) for a in self._TAB_STATE}

    def _load_tab(self, handle):
        for a, v in self._tabs.get(handle, {}).items():
            setattr(self, a, v)

    def _check_alive(self, require_window=True):
        if self.quit_called:
            raise WebDriverException("fake: invalid session id (the driver was quit)")
        if require_window and self._current_handle is None:
            raise NoSuchWindowException("fake: no such window (the current tab was closed)")

    @property
    def window_handles(self):
        self._check_alive(require_window=False)
        return list(self._handles)

    @property
    def current_window_handle(self):
        self._check_alive()
        return self._current_handle

    def close(self):
        self._check_alive()
        self._handles.remove(self._current_handle)
        self._tabs.pop(self._current_handle, None)
        self._current_handle = None

    def quit(self):
        self.quit_called = True
        self._handles = []
        self._current_handle = None

    # ------------------------------------------------------------------ navigation
    def _acc(self):
        return self.world.accounts[self.account]

    def _session_ok(self):
        return self.world.session.get(self.account, "ok") == "ok"

    def _on_photos(self):
        return self._session_ok() and self.page["kind"] in ("home", "search", "item", "photos_other")

    @property
    def current_url(self):
        self._check_alive()
        return self.url

    @property
    def title(self):
        self._check_alive()
        return {"signin": "Sign in - Google Accounts", "about": "Google Photos: Edit, Organize, Search",
                "sorry": SORRY_URL, "item": "Photo - Google Photos", "blank": ""}.get(self.page["kind"],
                                                                                     "Google Photos")

    def get(self, url):
        self._check_alive()
        self._navigate(url)

    def back(self):
        self._check_alive()
        if self.history:
            self._navigate(self.history.pop(), push=False)

    def _set_page(self, url, page, push=True):
        if push:
            self.history.append(self.url)
        self.url, self.page = url, page
        self._page_serial += 1
        self.search_focused, self.search_buffer, self.scroll_offset = False, "", 0

    def _navigate(self, url, push=True, opened_directly=True, throttled=False):
        parsed = urllib.parse.urlparse(url)
        host = (parsed.hostname or "").lower()
        if url.startswith("about:"):
            return self._set_page(url, {"kind": "blank"}, push)
        if host == DOWNLOAD_HOST:
            return self._direct_download(url)
        if not host.endswith("photos.google.com"):
            return self._set_page(url, {"kind": "other"}, push)
        state = self.world.session.get(self.account, "ok")
        if state == "expired":
            return self._set_page(SIGNIN_URL, {"kind": "signin"}, push)
        if state == "signed_out":
            return self._set_page(ABOUT_URL, {"kind": "about"}, push)
        if state == "sorry":
            return self._set_page(SORRY_URL, {"kind": "sorry"}, push)
        path = parsed.path or "/"
        photo = re.search(r"/photo/([^/?#]+)", path)
        search = re.search(r"/search/([^/?#]+)", path)
        context = ({"kind": "search", "query": _query_from_token(search.group(1)), "token": search.group(1),
                    "throttled": throttled} if search else {"kind": "home"})
        if photo:
            return self._open_item(photo.group(1), context, url, opened_directly, push)
        if search:
            self._set_page(url, dict(context), push)
            self._app_rpc("EzkLib", [[item_array(it) for it in self._grid()], None])
            return None
        if path in ("/", "") or re.fullmatch(r"/u/\d+/?", path):
            return self._set_page(url, {"kind": "home"}, push)
        return self._set_page(url, {"kind": "photos_other"}, push)

    def _open_item(self, photo_id, context, url, opened_directly, push=True):
        acc = self._acc()
        item = acc.items.get(photo_id)
        missing = item is None or item.deleted
        self._set_page(url, {"kind": "item", "photo_id": photo_id, "missing": missing, "context": context,
                             "opened_directly": opened_directly}, push)
        if not missing:
            self._app_rpc("VrseUb", self.world._vrseub_payload(acc, item))

    def _context_grid(self, context):
        if context["kind"] == "search":
            return self.world.search(self.account, context["query"])
        return self.world.timeline(self.account)

    def _grid(self):
        """The items the current grid shows (all of them; the viewport is a window onto this list)."""
        if self.world.render_requires_focus and not (self.fronted and self.focus_emulated):
            return []
        if self.page["kind"] == "search":
            if self.page.get("throttled"):
                return []  # a throttled search shows nothing (IMP-C25 thfix)
            return self.world.search(self.account, self.page["query"])
        if self.page["kind"] == "home":
            return self.world.timeline(self.account)
        return []

    def _window(self):
        grid = self._grid()
        return grid[self.scroll_offset:self.scroll_offset + self.world.viewport_tiles]

    # ------------------------------------------------------------------ rendering
    def _tile_label(self, item):
        t = item.local
        if self._acc().locale == "UK":
            return (f"{item.kind} {EN_DASH} {item.orientation} {EN_DASH} {t.day} {_UK_MONTHS[t.month - 1]} "
                    f"{t.year}, {t:%H:%M:%S}")
        return (f"{item.kind} - {item.orientation} - {_US_MONTHS[t.month - 1]} {t.day}, {t.year}, "
                f"{t.hour % 12 or 12}:{t:%M:%S}{NNBSP}{'AM' if t.hour < 12 else 'PM'}")

    def _panel_lines(self, item):
        t, uk = item.local, self._acc().locale == "UK"
        this_year = t.year == self.world.now.year
        mp = f"{item.width * item.height / 1e6:.1f}"
        if uk:
            date_text = f"{t.day} {_UK_MONTHS[t.month - 1]}" + ("" if this_year else f" {t.year}")
            time_text = f"{_WEEKDAYS[t.weekday()]}, {t:%H:%M}"
            mp, source = mp + " MP", "Uploaded from an Android device"
        else:
            date_text = f"{_US_MONTHS[t.month - 1]} {t.day}" + ("" if this_year else f", {t.year}")
            time_text = f"{_WEEKDAYS[t.weekday()]}, {t.hour % 12 or 12}:{t:%M}{NNBSP}{'AM' if t.hour < 12 else 'PM'}"
            mp, source = mp + "MP", "Uploaded from Android device"
        return ["Info", "Add a description", "Details", date_text, time_text, _gmt_text(item.tz_offset_ms),
                re.sub(r"\s+", " ", item.filename),  # innerText collapses whitespace runs (PLAN P4)
                mp, f"{item.width} {TIMES} {item.height}", source, "Backed up",
                "Original quality. Learn more" if item.original_quality else "Storage saver. Learn more",
                "This item doesn't take up space in your account storage. Learn more", "Add a location"]

    def _body_text(self):
        kind = self.page["kind"]
        if kind == "signin":
            return "Sign in\nto continue to Google Photos\nEmail or phone\nForgot email?\nNext"
        if kind == "about":
            return "Google Photos\nThe home for your memories\nGo to Google Photos"
        if kind == "sorry":
            return ("About this page\nOur systems have detected unusual traffic from your computer network. This page "
                    "checks to see if it's really you sending the requests, and not a robot.")
        if kind == "html":
            return re.sub(r"<[^>]+>", "", self.page["html"])
        if kind in ("home", "search", "photos_other"):
            head = f"Photos\n{self.page.get('query', 'Search your photos')}\n"
            return head + ("" if self._grid() or kind == "photos_other" else "No results found\n")
        if kind == "item":
            if self.page["missing"]:
                return "Photos\nThis photo is no longer available\nGo back"
            chrome = "Photos\nShare\nZoom in\nInfo\nFavourite\nDelete\nMore options\n"
            if not self.panel_open:
                return chrome
            return chrome + "\n".join(self._panel_lines(self._acc().items[self.page["photo_id"]])) + "\n"
        return ""

    @property
    def page_source(self):
        self._check_alive()
        kind = self.page["kind"]
        if kind == "html":
            return self.page["html"]
        if kind == "item" and not self.page["missing"]:
            return self._item_source()
        body = html.escape(self._body_text())
        return f"<html><head><title>{self.title}</title></head><body>{body}</body></html>"

    def _item_source(self):
        acc = self._acc()
        item = acc.items[self.page["photo_id"]]
        grid = self._context_grid(self.page["context"])
        idx = next((i for i, it in enumerate(grid) if it.photo_id == item.photo_id), None)
        neighbours = [] if idx is None else [grid[i] for i in (idx - 1, idx + 1) if 0 <= i < len(grid)]

        def js(obj):
            text = json.dumps(obj, separators=(",", ":"), ensure_ascii=False)
            return text.replace("/", "\\/") if self.world.escape_slashes_in_pages else text

        ds0 = js([item_array(item), None, [item.photo_id]])
        ds1 = js([[item_array(n) for n in neighbours]])
        return ("<!doctype html><html><head><title>Photo - Google Photos</title>"
                f"<script class=\"ds:0\" nonce=\"fake\">AF_initDataCallback({{key: 'ds:0', hash: '1', data:{ds0}, "
                "sideChannel: {}});</script>"
                f"<script class=\"ds:1\" nonce=\"fake\">AF_initDataCallback({{key: 'ds:1', hash: '2', data:{ds1}, "
                "sideChannel: {}});</script></head>"
                f"<body>{html.escape(self._body_text())}</body></html>")

    # ------------------------------------------------------------------ elements
    def find_elements(self, by, value):
        self._check_alive()
        if by == By.TAG_NAME and str(value).lower() == "body":
            return [FakeElement(self, "body")]
        anchor = re.fullmatch(r"""a\[href\*=['"]([^'"]+)['"]\]""", str(value)) if by == By.CSS_SELECTOR else None
        if anchor:
            return [a for a in self._anchors() if anchor.group(1) in a.attrs["href"]]
        if by == By.XPATH and "background-image" in str(value):
            return [FakeElement(self, "bgdiv", {"style": f'background-image: url("https://{THUMB_HOST}/p/'
                                                         f'{it.photo_id}=w200-h200");'}, target=it.photo_id)
                    for it in self._window()]
        labels = re.findall(r"""aria-label=['"]([^'"]+)['"]""", str(value)) if by == By.CSS_SELECTOR else []
        if labels:
            return [b for b in self._buttons() if b.attrs["aria-label"] in labels]
        self.unknown_selectors.append((by, value))
        return []

    def find_element(self, by=By.ID, value=None):
        found = self.find_elements(by, value)
        if not found:
            raise NoSuchElementException(f"fake: no element for {by}={value!r}")
        return found[0]

    def _anchors(self):
        kind = self.page["kind"]
        if kind == "search":
            visible = [FakeElement(self, "anchor", {"href": f"./search/{self.page['token']}/photo/{it.photo_id}",
                                                    "aria-label": self._tile_label(it)}, target=it.photo_id)
                       for it in self._window()]
            hidden = [FakeElement(self, "anchor", {"href": f"./photo/{it.photo_id}",
                                                   "aria-label": self._tile_label(it)},
                                  displayed=False, width=0, height=0, target=it.photo_id)
                      for it in self.world.timeline(self.account)[:self.world.viewport_tiles]]
            return visible + hidden  # RESEARCH F14: the timeline behind the results stays in the DOM, hidden
        if kind == "home":
            return [FakeElement(self, "anchor", {"href": f"./photo/{it.photo_id}", "aria-label": self._tile_label(it)},
                                target=it.photo_id) for it in self._window()]
        return []

    def _buttons(self):
        if self.page["kind"] != "item" or self.page["missing"]:
            return []
        label = "Close info" if self.panel_open else "Open info"
        return [FakeElement(self, "button", {"aria-label": label}, width=40, height=40, target="info")]

    def _click(self, el):
        if not isinstance(el, FakeElement) or el.parent is not self:
            raise WebDriverException("fake: not an element of this driver")
        if el.page_serial != self._page_serial:
            raise StaleElementReferenceException("fake: stale element reference (the page changed)")
        if el.kind in ("anchor", "bgdiv") and el.target:
            href = el.attrs.get("href", "")
            if self.page["kind"] == "search" and (el.kind == "bgdiv" or "/search/" in href):
                ctx = {"kind": "search", "query": self.page["query"], "token": self.page["token"]}
                url = f"{PHOTOS_URL}/search/{ctx['token']}/photo/{el.target}"
            else:
                ctx, url = {"kind": "home"}, f"{PHOTOS_URL}/photo/{el.target}"
            self.actions.append({"kind": "open", "photo_id": el.target, "via": "click"})
            self._open_item(el.target, ctx, url, opened_directly=False)
        elif el.kind == "button":
            self._toggle_info()

    # ------------------------------------------------------------------ scripts
    def execute_script(self, script, *args):
        self._check_alive()
        self.script_log.append(str(script)[:80])
        s = str(script).strip()
        if script == gpweb.TILES_JS:
            return [{"href": a.attrs["href"], "label": a.attrs["aria-label"]}
                    for a in self._anchors() if a.is_displayed() and a.size["width"] > 40]
        if script == gpweb.SCROLL_JS:
            return self._scroll()
        if script == gpweb.SEARCH_STATE_JS:
            return self._search_state()
        if script == gpweb.TIMELINE_LINKS_JS:
            return [self._is_timeline_link(el) for el in (args[0] if args else [])]
        if re.fullmatch(r"arguments\[0\]\.click\(\);?", s) and args:
            self._click(args[0])
            return None
        if "WIZ_global_data" in s:
            return self._wiz(s)
        if "document.visibilityState" in s:
            return "visible" if self.fronted else "hidden"
        if "document.hasFocus()" in s:
            return bool(self.focus_emulated)
        raise NotImplementedError(f"FakeDriver.execute_script: unsupported script {s[:70]!r} — "
                                  "extend tests/gphotos_fake.py explicitly")

    def _search_state(self):
        """gpweb.SEARCH_STATE_JS: the VISIBLE links to items (TILES_JS's rule) counted as search results
        (./search/<token>/photo/<id>) or home-timeline thumbnails (./photo/<id>), and whether the page says
        "No results". On a search page the timeline anchors behind the results are hidden (RESEARCH F14)."""
        results = timeline = 0
        for a in self._anchors():
            href = a.attrs.get("href") or ""
            if not (a.is_displayed() and a.size["width"] > 40 and "photo/" in href):
                continue
            if re.search(r"(^|/)search/", href):
                results += 1
            else:
                timeline += 1
        return {"results": results, "timeline": timeline,
                "relevant": 0,  # the fake has no "Most relevant to your search" strip (tests/test_fetch_search_wait.py)
                "empty": re.search(r"\bno results\b", self._body_text(), re.IGNORECASE) is not None}

    def _is_timeline_link(self, el):
        """gpweb.TIMELINE_LINKS_JS for one element: is it — or does it sit inside — a link to an item that is not
        a search result? An anchor answers by its own href; a background-image div by its tile's link (on a
        search page ./search/<token>/photo/<id> — the link its click opens — elsewhere ./photo/<id>)."""
        if not isinstance(el, FakeElement) or el.parent is not self:
            raise WebDriverException("fake: not an element of this driver")
        if el.page_serial != self._page_serial:
            raise StaleElementReferenceException("fake: stale element reference (the page changed)")
        href = ""
        if el.kind == "anchor":
            href = el.attrs.get("href") or ""
        elif el.kind == "bgdiv" and el.target:
            href = (f"./search/{self.page['token']}/photo/{el.target}" if self.page["kind"] == "search"
                    else f"./photo/{el.target}")
        return "photo/" in href and not re.search(r"(^|/)search/", href)

    def _scroll(self):
        n, view = len(self._grid()), self.world.viewport_tiles
        self.scroll_offset = min(self.scroll_offset + self.world.scroll_step_tiles, max(0, n - view))
        return [self.scroll_offset * 100, n * 100]

    def _wiz(self, script):
        if not self._on_photos():
            return None
        wiz = dict(self._acc().wiz)
        if self.world.wiz_token_missing[self.account]:
            wiz.pop("SNlM0e")
        named = [k for k in WIZ_KEYS if k in script]
        return {k: wiz[k] for k in named if k in wiz} if named else wiz

    def execute_async_script(self, script, *args):
        self._check_alive()
        self.script_log.append(str(script)[:80])
        url = next((a for a in args if isinstance(a, str) and "/data/batchexecute" in a), None)
        form = next((a for a in args if isinstance(a, str) and "f.req=" in a), None)
        if url is None:
            m = re.search(r"https://photos\.google\.com[^'\"`\s]*/data/batchexecute[^'\"`\s]*", str(script))
            url = m.group(0) if m else None
        if form is None:
            m = re.search(r"f\.req=[^'\"`\s]*", str(script))
            form = m.group(0) if m else None
        if url is None or form is None:
            raise JavascriptException("fake: execute_async_script is emulated only for the batchexecute POST "
                                      "(url + form body, see tests/gphotos_fake.py)")
        if not self._on_photos():
            return {"status": 0, "text": "TypeError: Failed to fetch"}
        return self.world._batchexecute(self.account, url, form)

    # ------------------------------------------------------------------ CDP + logs
    def execute_cdp_cmd(self, cmd, cmd_args):
        self._check_alive()
        self.cdp_log.append((cmd, dict(cmd_args or {})))
        params = cmd_args or {}
        if cmd == "Page.bringToFront":
            self.fronted = True
            return {}
        if cmd == "Emulation.setFocusEmulationEnabled":
            self.focus_emulated = bool(params.get("enabled"))
            return {}
        if cmd in ("Browser.setDownloadBehavior", "Page.setDownloadBehavior"):
            if self.world.refuse_download_redirect:
                raise WebDriverException(f"fake: {cmd} is not allowed in this session")
            behavior = params.get("behavior")
            if behavior in ("allow", "allowAndName"):
                self.download_dir = self.world._guard_path(params["downloadPath"])
                self.downloads_allowed = True
            elif behavior == "deny":
                self.downloads_allowed = False
            elif behavior == "default":
                self.download_dir, self.downloads_allowed = self.world.default_download_dir, True
            else:
                raise WebDriverException(f"fake: invalid download behavior {behavior!r}")
            self.download_events = bool(params.get("eventsEnabled"))
            return {}
        if cmd == "Network.enable":
            return {}
        if cmd == "Network.getResponseBody":
            body = self._bodies.get(params.get("requestId"))
            if body is None:
                raise WebDriverException("fake: No resource with given identifier found")
            return {"body": body, "base64Encoded": False}
        raise NotImplementedError(f"FakeDriver.execute_cdp_cmd: unsupported command {cmd!r} — extend the fake")

    def _app_rpc(self, rpcid, payload):
        """The web app's own RPC traffic for the page just shown — visible only in the performance log
        (never in world.rpc_log, which records what MediaVault sends)."""
        self._req_seq += 1
        rid = f"FAKE-REQ-{self._req_seq}"
        url = (f"{PHOTOS_URL}{self._acc().wiz['eptZe']}data/batchexecute?rpcids={rpcid}"
               f"&source-path={urllib.parse.quote(urllib.parse.urlparse(self.url).path)}&rt=c")
        self._bodies[rid] = envelope(rpcid, payload)
        now_ms = int(time.time() * 1000)
        for method, extra in (("Network.requestWillBeSent", {"request": {"url": url, "method": "POST"}}),
                              ("Network.responseReceived", {"response": {"url": url, "status": 200,
                                                                         "mimeType": "application/json"}}),
                              ("Network.loadingFinished", {})):
            message = {"message": {"method": method, "params": {"requestId": rid, "type": "XHR", **extra}},
                       "webview": self._current_handle}
            self._perf.append({"level": "INFO", "message": json.dumps(message), "timestamp": now_ms})

    def get_log(self, log_type):
        self._check_alive()
        if log_type == "performance":
            out, self._perf = self._perf, []
            return out
        return []

    # ------------------------------------------------------------------ W3C actions (ActionChains)
    def execute(self, driver_command, params=None):
        self._check_alive()
        if driver_command == Command.W3C_ACTIONS:
            self._perform((params or {}).get("actions", []))
            return {"value": None}
        if driver_command == Command.W3C_CLEAR_ACTIONS:
            self.shift_down = False
            return {"value": None}
        raise NotImplementedError(f"FakeDriver.execute: unsupported command {driver_command!r} — extend the fake")

    def _perform(self, sources):
        ticks = max((len(s.get("actions", [])) for s in sources), default=0)
        for i in range(ticks):
            for src in sources:
                acts = src.get("actions", [])
                if i >= len(acts):
                    continue
                act, kind = acts[i], src.get("type")
                if kind == "key" and act.get("type") in ("keyDown", "keyUp"):
                    self.key_log.append((act["type"], act.get("value")))
                    if act["type"] == "keyDown":
                        self._key_down(act.get("value") or "")
                    elif act.get("value") in (Keys.SHIFT, Keys.RIGHT_SHIFT):
                        self.shift_down = False
                elif kind == "pointer":
                    self._pointer(act)

    def _pointer(self, act):
        if act.get("type") == "pointerMove":
            origin = act.get("origin")
            self._pointer_target = (self._elements.get(next(iter(origin.values()), None))
                                    if isinstance(origin, dict) else None)
        elif act.get("type") == "pointerUp" and act.get("button", 0) == 0 and self._pointer_target is not None:
            self._click(self._pointer_target)

    def _key_down(self, v):
        if v in (Keys.SHIFT, Keys.RIGHT_SHIFT):
            self.shift_down = True
        elif v in (Keys.ENTER, Keys.RETURN):
            if self.search_focused:
                self._submit_search()
        elif v == Keys.ESCAPE:
            self._escape()
        elif len(v) == 1 and not 0xE000 <= ord(v) <= 0xF8FF:  # WebDriver special keys live in that range
            if self.search_focused:
                self.search_buffer += v
            elif v == "/":
                if self._on_photos():
                    self.search_focused, self.search_buffer = True, ""
                    self.actions.append({"kind": "search_focus"})
            elif v == "D" or (v == "d" and self.shift_down):
                self._shift_d()
            elif v == "i":
                self._toggle_info()

    def _submit_search(self):
        query = self.search_buffer
        self.actions.append({"kind": "search", "query": query})
        self._navigate(f"{PHOTOS_URL}/search/{_search_token(query)}",
                       throttled=self.world._throttled_search(self.account))

    def _escape(self):
        if self.search_focused:
            self.search_focused, self.search_buffer = False, ""
            self.actions.append({"kind": "escape", "from": "search"})
        elif self.page["kind"] == "item":
            ctx = self.page["context"]
            self.actions.append({"kind": "escape", "from": "item"})
            self._navigate(f"{PHOTOS_URL}/search/{ctx['token']}" if ctx["kind"] == "search" else PHOTOS_URL)
        else:
            self.actions.append({"kind": "escape", "from": self.page["kind"]})

    def _toggle_info(self):
        if self.page["kind"] == "item" and not self.page["missing"]:
            self.panel_open = not self.panel_open
            self.actions.append({"kind": "info_toggle", "open": self.panel_open})

    # ------------------------------------------------------------------ downloads
    def _shift_d(self):
        page = self.page
        pid = page.get("photo_id") if page["kind"] == "item" and not page.get("missing") else None
        blocked = pid is not None and self.world.shift_d_requires_click and page.get("opened_directly")
        rec = None
        if pid is not None and not blocked:
            item = self._acc().items[pid]
            rec = self._write_download(item.filename, item.data, "shift_d", pid)
        self.actions.append({"kind": "shift_d", "photo_id": pid, "downloaded": rec is not None})

    def _direct_download(self, url):
        token = url.rstrip("/").rsplit("/", 1)[-1]
        minted = self.world.minted.get(token)
        if (minted is None or minted["account"] != self.account or not self._session_ok()
                or self.world.direct_mode == "html"):
            html_doc = "<html><head><title>Sign in</title></head><body>Sign in to continue</body></html>"
            return self._set_page(url, {"kind": "html", "html": html_doc})
        items = [self._acc().items.get(k) for k in minted["keys"]]
        items = [it for it in items if it is not None and not it.deleted]
        if len(minted["keys"]) > 1:
            data = b"PK\x03\x04FAKE-ZIP:" + b"".join(it.data for it in items)
            self._write_download("Photos-001.zip", data, "direct", None)
        elif items:
            item = items[0]
            data = item.data if self.world.direct_mode == "ok" else b"FAKE-WRONG-BYTES:" + item.data[:16]
            self._write_download(item.filename, data, "direct", item.photo_id)
        return None  # a download never navigates: the current page stays

    def _write_download(self, filename, data, via, photo_id):
        if not self.downloads_allowed:
            return None
        folder = self.world._guard_path(self.download_dir)
        os.makedirs(folder, exist_ok=True)
        stem, ext = os.path.splitext(filename)
        name, n = filename, 0
        while os.path.exists(os.path.join(folder, name)) or os.path.exists(os.path.join(folder, name + ".crdownload")):
            n += 1
            name = f"{stem} ({n}){ext}"
        final = os.path.join(folder, name)
        partial = final + ".crdownload"
        with open(partial, "wb") as fh:
            fh.write(data[:max(1, len(data) // 2)])
        rec = {"account": self.account, "photo_id": photo_id, "filename": name, "path": final,
               "partial": partial, "via": via, "completed": False, "cancelled": False, "data": data}
        self.world.downloads.append(rec)
        if self.world.download_mode == "instant":
            self.world._complete(rec)
        elif self.world.download_mode == "delayed":
            timer = threading.Timer(self.world.download_delay_s, self.world._complete, (rec,))
            timer.daemon = True
            self.world._timers.append(timer)
            timer.start()
        return rec
