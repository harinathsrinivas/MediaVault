"""gpweb — the ONE shared Google Photos web + RPC parser module (IMP-C25 Step 2).

Every MediaVault component that reads a Google Photos item page, a result tile, the info panel or a
batchexecute RPC answer uses THESE functions — no private copies anywhere else (the drift lesson of
IMP-C18/C22/C23/U7). Consumers: tools/gp_inventory.py (crawler), gprpc.py (read-only RPC client),
map_photos / check_photos / the fetch locator, and tests/gphotos_fake.py (the fake Google Photos).

Contract
  * Pure functions over strings and numbers: stdlib only, no Selenium, no file or network I/O, nothing
    printed. Bad or missing input comes back as None / {} / [] — never IndexError, KeyError or
    TypeError. The only exceptions raised are rpc_payload()'s envelope errors (RpcEnvelopeError).
  * Identity and time come from the RECORD (photo_id, dedupKey, taken_ms, tz_offset_ms, upload_ms —
    PLAN P3, P-7). Display text (tile labels, the info panel) is a locale-tolerant HINT: a parse
    failure only disables a pre-filter, it never decides identity.
  * Error messages never contain an answer's content: an answer can carry a bearer download URL (P-20).
  * Import rule: gpweb imports no MediaVault module at import time, so mvcommon (IMP-C25 Step 3) may
    import gpweb without a cycle (gpcapture imports mvcommon); dedup_key() reaches gpcapture lazily.

RPC layouts follow PLAN P3/P11/P12/P14 and the Step 2 text. Positions the plan does not pin are marked
UNVERIFIED in the parser docstrings and are confirmed live at 🚦 R2 — this module is the one place to fix.

Sections: 1 page record · 2 RPC envelope + record harvest · 3 typed RPC parsers · 4 hash formats ·
5 display text · 6 names · 7 time math · 8 query builders · 9 DOM snippets.
"""
import base64
import json
import os
import re
import unicodedata
from collections.abc import Mapping
from datetime import date, datetime, timedelta

MEDIA_KEY_RE = re.compile(r"^[A-Za-z0-9_-]{10,}$")  # a photo_id (mediaKey), e.g. "AF1Qip…"
DEDUP_KEY_RE = re.compile(r"^[A-Za-z0-9_-]{27}$")   # PLAN P2: urlsafe base64 of a SHA-1, no padding
PHOTO_ID_RE = re.compile(r"/photo/([^/?#]+)")       # …/photo/<id>, /search/<token>/photo/<id>, /u/1/photo/<id>

_JSON = json.JSONDecoder()
_EPOCH = datetime(1970, 1, 1)


def _is_int(v):
    return isinstance(v, int) and not isinstance(v, bool)


def _is_number(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _is_key(v):
    return isinstance(v, str) and MEDIA_KEY_RE.match(v) is not None


def _is_dedup(v):
    return isinstance(v, str) and DEDUP_KEY_RE.match(v) is not None


def _int_or_none(v, allow_str=False):
    if _is_int(v):
        return v
    if allow_str and isinstance(v, str) and re.fullmatch(r"-?\d+", v.strip()):
        return int(v)
    return None


def _str_or_none(v):
    return v if isinstance(v, str) and v else None


def _url_or_none(v):
    return v if isinstance(v, str) and v.startswith(("https://", "http://")) else None


def _get(obj, *path):
    """obj[p0][p1]… without ever raising: None when an index is missing or a container has the wrong
    type. Integer steps index lists; dict steps use the str() of the key (JSPB extension objects)."""
    for p in path:
        if isinstance(obj, list):
            if not _is_int(p) or not -len(obj) <= p < len(obj):
                return None
            obj = obj[p]
        elif isinstance(obj, dict):
            obj = obj.get(p if isinstance(p, str) else str(p))
        else:
            return None
    return obj


# ================================================================================ 1. page record

# The crawler's proven record regex (commit 70635cb). The thumbnail sub-array has two observed shapes,
# ["<url>",w,h] and ["<url>",w,h,null,…,null,[n]] — so its tail is optional nulls, numbers or one flat
# array. Group 1 is the photo_id; groups 2–7 are width, height, taken_ms, dedupKey, tz_offset_ms, upload_ms.
_THUMB_TAIL = r'(?:,(?:null|-?\d+(?:\.\d+)?|\[[^\[\]]*\]))*'
_RECORD_TAIL = (r'",\["[^"]*",(\d+),(\d+)' + _THUMB_TAIL
                + r'\],(-?\d{10,14}),"([A-Za-z0-9_-]{27})",(-?\d+),(\d{12,14})')
_ANY_RECORD_RE = re.compile(r'([A-Za-z0-9_-]{10,})' + _RECORD_TAIL)
_RECORD_OPEN_RE = re.compile(r'\["([A-Za-z0-9_-]{10,})",\["')


def _digits(n):
    return len(str(abs(n)))


def _p3(arr, strict):
    """PLAN P3 library-item signature [photo_id, [thumb_url, w, h, …], taken_ms, dedupKey, tz_offset_ms,
    upload_ms, …] → {photo_id, width, height, taken_ms, dedup_key, tz_offset_ms, upload_ms}, or None.

    strict (item pages): all six fields present and typed exactly as the crawler's regex demands.
    lenient (RPC answers): width / height / tz_offset_ms / upload_ms may be missing (→ None)."""
    if not isinstance(arr, list) or len(arr) < (6 if strict else 4):
        return None
    key, thumb, taken, dedup = arr[0], arr[1], arr[2], arr[3]
    if not (_is_key(key) and _is_int(taken) and _is_dedup(dedup)):
        return None
    width, height, tz, upload = _get(thumb, 1), _get(thumb, 2), _get(arr, 4), _get(arr, 5)
    if strict:
        if not (isinstance(thumb, list) and thumb and isinstance(thumb[0], str)):
            return None
        if not (_is_int(width) and width >= 0 and _is_int(height) and height >= 0 and _is_int(tz)
                and _is_int(upload) and upload >= 0):
            return None
        if not (10 <= _digits(taken) <= 14 and 12 <= _digits(upload) <= 14):
            return None
    else:
        if thumb is not None and not isinstance(thumb, list):
            return None
        width, height, tz, upload = (v if _is_int(v) else None for v in (width, height, tz, upload))
    return {"photo_id": key, "width": width, "height": height, "taken_ms": taken, "dedup_key": dedup,
            "tz_offset_ms": tz, "upload_ms": upload}


def _record_at(src, key_pos, legacy_re):
    """The record whose photo_id starts at src[key_pos]: decoded structurally (raw_decode + signature)
    when the id opens an array, else matched by the crawler's regex (which also tolerates non-JSON
    content elsewhere in the record). None when neither holds."""
    if src[max(0, key_pos - 2):key_pos] == '["':
        try:
            arr, _ = _JSON.raw_decode(src, key_pos - 2)
        except ValueError:
            arr = None
        rec = _p3(arr, strict=True)
        if rec is not None:
            return rec
    m = legacy_re.match(src, key_pos)
    if m is None:
        return None
    return {"photo_id": m.group(1), "width": int(m.group(2)), "height": int(m.group(3)),
            "taken_ms": int(m.group(4)), "dedup_key": m.group(5), "tz_offset_ms": int(m.group(6)),
            "upload_ms": int(m.group(7))}


def page_record(page_source, photo_id):
    """The record an item page embeds for `photo_id` (RESEARCH F20, PLAN P3) →
    {photo_id, width, height, taken_ms, dedup_key, tz_offset_ms, upload_ms}, or {} when absent.

    Every occurrence of the id is tried in page order: structurally first (json raw_decode + the P3
    signature), then with the crawler's regex. The first occurrence that passes wins."""
    if not isinstance(page_source, str) or not isinstance(photo_id, str) or not photo_id:
        return {}
    pid = re.escape(photo_id)
    legacy = re.compile("(" + pid + ")" + _RECORD_TAIL)
    for m in re.finditer(pid + r'",\["', page_source):
        rec = _record_at(page_source, m.start(), legacy)
        if rec:
            return rec
    return {}


def page_records(page_source):
    """Every record an item page embeds (the item itself and its neighbours), in page order, one per
    photo_id (first occurrence wins) — the same checks as page_record()."""
    if not isinstance(page_source, str):
        return []
    out, seen = [], set()
    for m in _RECORD_OPEN_RE.finditer(page_source):
        rec = _record_at(page_source, m.start(1), _ANY_RECORD_RE)
        if rec and rec["photo_id"] not in seen:
            seen.add(rec["photo_id"])
            out.append(rec)
    return out


def photo_id_from_url(url):
    """The photo_id in a Google Photos item URL or tile href (…/photo/<id>[/?#…]), or None."""
    m = PHOTO_ID_RE.search(url) if isinstance(url, str) else None
    return m.group(1) if m and MEDIA_KEY_RE.match(m.group(1)) else None


# ================================================================ 2. RPC envelope + record harvest

XSSI_PREFIX = ")]}'"  # the anti-XSSI guard line every batchexecute answer starts with


class RpcEnvelopeError(ValueError):
    """A batchexecute answer could not be unwrapped (base class of the three below)."""


class NoEnvelopeError(RpcEnvelopeError):
    """No decodable `wrb.fr` frame in the answer — an HTML error page, an `er` frame, a truncated
    stream. Usually transient: retry."""


class EmptyPayloadError(RpcEnvelopeError):
    """A `wrb.fr` frame carries no payload — the server's error answer for this request. `code` is the
    frame's status code when present (UNVERIFIED mapping; e.g. 5 is believed to mean not found)."""

    def __init__(self, message, rpcid=None, code=None):
        super().__init__(message)
        self.rpcid = rpcid
        self.code = code


class MalformedPayloadError(RpcEnvelopeError):
    """The frame's payload is not JSON — the answer format changed."""


def rpc_envelopes(body):
    """Every `wrb.fr` frame of a batchexecute answer in order, as (rpcid, payload_text, code) — payload_text
    None for an error frame, code the frame's [5][0] status when present. Tolerates the `)]}'` guard,
    `rt=c` chunk-length lines, several frames per line and lines that do not decode. Never raises."""
    if isinstance(body, (bytes, bytearray)):
        body = bytes(body).decode("utf-8", "replace")
    if not isinstance(body, str):
        return []
    frames = []
    for line in body.splitlines():
        if "wrb.fr" not in line:
            continue
        start = line.find("[")
        if start < 0:
            continue
        try:
            chunk, _ = _JSON.raw_decode(line, start)
        except ValueError:
            continue
        for f in (chunk if isinstance(chunk, list) else ()):
            if isinstance(f, list) and len(f) >= 3 and f[0] == "wrb.fr":
                code = _get(f, 5, 0)
                frames.append((f[1] if isinstance(f[1], str) else None,
                               f[2] if isinstance(f[2], str) else None,
                               code if _is_int(code) else None))
    return frames


def rpc_payload(body, rpcid=None):
    """PLAN P11: the first `wrb.fr` frame (the first one for `rpcid` when given) → its payload string
    decoded as JSON (JSON-in-JSON). Raises NoEnvelopeError (no frame), EmptyPayloadError (the frame's
    payload is null / empty — its status code on .code) or MalformedPayloadError (payload not JSON)."""
    frames = rpc_envelopes(body)
    if rpcid is not None:
        frames = [f for f in frames if f[0] == rpcid]
    if not frames:
        size = len(body) if isinstance(body, (str, bytes, bytearray)) else 0
        raise NoEnvelopeError(f"no wrb.fr frame{' for ' + rpcid if rpcid else ''} in the batchexecute "
                              f"answer ({size} bytes)")
    rid, text, code = frames[0]
    if text is None or not text.strip():
        raise EmptyPayloadError(f"{rid or 'rpc'} answered without a payload (status {code})", rid, code)
    try:
        payload = json.loads(text)
    except ValueError:
        raise MalformedPayloadError(f"{rid or 'rpc'} payload is not JSON ({len(text)} chars)") from None
    if payload is None:
        raise EmptyPayloadError(f"{rid or 'rpc'} answered with a null payload (status {code})", rid, code)
    return payload


def rpc_records(body):
    """Harvest every record in a batchexecute answer — every frame, every nesting level — as dicts with
    `kind` ("item": a P3 library item + partial_upload/archived/duration_ms, as parse_lcxiM gives;
    "ext": an item-info-ext record, as parse_fDcn4b gives) and the frame's `rpcid`. One per
    (kind, photo_id), first occurrence wins, page order. Meant for passively observed traffic
    (performance log); never raises — an undecodable answer yields []."""
    out, seen = [], set()
    for rid, text, _code in rpc_envelopes(body):
        if not text:
            continue
        try:
            payload = json.loads(text)
        except ValueError:
            continue
        stack = [payload]
        while stack:
            node = stack.pop()
            if isinstance(node, list):
                kind, rec = "item", _rpc_item(node)
                if rec is None:
                    kind, rec = "ext", _ext_record(node)
                if rec is not None and (kind, rec["photo_id"]) not in seen:
                    seen.add((kind, rec["photo_id"]))
                    out.append({"kind": kind, "rpcid": rid, **rec})
                stack.extend(reversed(node))
            elif isinstance(node, dict):
                stack.extend(reversed(list(node.values())))
    return out


# ============================================================================ 3. typed RPC parsers
#
# One parser per read-only rpcid (PLAN P12). Each takes the DECODED payload (rpc_payload()) and returns
# a dict — or None when the payload's top-level shape is not the documented one (the caller's
# "changed shape" signal). Records failing their signature are dropped and counted in `rejected`.

_DURATION_EXT_KEY = "76647426"  # PLAN P12: the item's trailing extension object → duration


def _partial_upload(arr):
    """PLAN P12: item[12][0] == 20 ⇒ partial upload. None when the item is too short to tell."""
    if not isinstance(arr, list) or len(arr) <= 12:
        return None
    v = arr[12]
    if v is None:
        return False
    if isinstance(v, list):
        return bool(v) and v[0] == 20
    return None


def _archived(arr):
    """PLAN P12: item[13] = archived. None when absent."""
    if not isinstance(arr, list) or len(arr) <= 13:
        return None
    v = arr[13]
    if v is None:
        return False
    if isinstance(v, bool):
        return v
    return v != 0 if _is_int(v) else None


def _duration_ms(arr):
    """PLAN P12: the item's last element is an extension object; key 76647426 ⇒ duration (believed ms)."""
    for v in (reversed(arr) if isinstance(arr, list) else ()):
        if isinstance(v, dict):
            d = v.get(_DURATION_EXT_KEY)
            d = d[0] if isinstance(d, list) and d else d
            return d if _is_int(d) else None
    return None


def _rpc_item(arr):
    """A P3 library item from an RPC answer (lenient signature) + its P12 flags, or None."""
    rec = _p3(arr, strict=False)
    if rec is None:
        return None
    rec["partial_upload"] = _partial_upload(arr)
    rec["archived"] = _archived(arr)
    rec["duration_ms"] = _duration_ms(arr)
    return rec


def _ext_record(arr):
    """Item-info-ext signature (fDcn4b payload[0]; Step 2 text): [0] photo_id, [2] file name, [3] taken_ms,
    [4] tz_offset_ms, [5] size in bytes, [11] dedupKey → dict, or None."""
    if not isinstance(arr, list) or len(arr) < 12:
        return None
    key, name, dedup = arr[0], arr[2], arr[11]
    if not (_is_key(key) and isinstance(name, str) and name.strip() and _is_dedup(dedup)):
        return None
    return {"photo_id": key, "filename": name, "size_bytes": _int_or_none(arr[5]), "dedup_key": dedup,
            "taken_ms": _int_or_none(arr[3]), "tz_offset_ms": _int_or_none(arr[4])}


def _items_page(payload):
    """[items, nextPageId, …] → {items, next_page_id, rejected}, or None for another shape."""
    if not isinstance(payload, list):
        return None
    raw = _get(payload, 0)
    if raw is None:
        raw = []  # JSPB omits an empty repeated field: an empty page
    elif not isinstance(raw, list):
        return None
    items, rejected = [], 0
    for r in raw:
        rec = _rpc_item(r)
        if rec is None:
            rejected += 1
        else:
            items.append(rec)
    nxt = _get(payload, 1)
    return {"items": items, "next_page_id": nxt if isinstance(nxt, str) and nxt else None,
            "rejected": rejected}


def parse_lcxiM(payload):
    """Timeline by taken date — request [pageId, timestamp_ms|null, pageSize≤500, null, 1, source]
    (source 1 library, 2 archive, 3 both) → payload [items, nextPageId, lastItemTimestamp] (PLAN P12) →
    {items: [record + partial_upload, archived, duration_ms], next_page_id, last_timestamp_ms, rejected}."""
    page = _items_page(payload)
    if page is not None:
        page["last_timestamp_ms"] = _int_or_none(_get(payload, 2), allow_str=True)
    return page


def parse_EzkLib(payload):
    """Search — request [query, null, pageId] — and the recently-uploaded listing — request
    ['', [[4, 'ra', 0, 0]], pageId] — → payload [items, nextPageId] (PLAN P12) →
    {items: [record + partial_upload, archived, duration_ms], next_page_id, rejected}."""
    return _items_page(payload)


def parse_swbisb(payload):
    """Hash lookup — request [[<SHA-1 as STANDARD base64, padded>, …], null, 3, 0] → payload
    [[[hash, record], …]] (PLAN P12) → {matches: [record + flags + hash_b64 + dedup_consistent], rejected}.
    `dedup_consistent` is True when the record's dedupKey is the queried hash (it should always be);
    unmatched hashes are simply absent."""
    if not isinstance(payload, list):
        return None
    pairs = _get(payload, 0)
    if pairs is None:
        pairs = []
    elif not isinstance(pairs, list):
        return None
    matches, rejected = [], 0
    for pair in pairs:
        rec = _rpc_item(_get(pair, 1)) if isinstance(_get(pair, 0), str) else None
        if rec is None:
            rejected += 1
            continue
        rec["hash_b64"] = pair[0]
        rec["dedup_consistent"] = dedup_keys_equal(std_b64_to_dedup_key(pair[0]), rec["dedup_key"])
        matches.append(rec)
    return {"matches": matches, "rejected": rejected}


def parse_VrseUb(payload):
    """Item info — request [mediaKey, null, null, null, null] → the item record + partial_upload, archived,
    duration_ms + `display_url` (payload[1]) + `download_original_url` (payload[7], PLAN P14), or None when
    payload[0] is not an item.

    The dedupKey is read at [0][3] (P3); only when that slot holds no key is [0][11] tried (the
    item-info-ext position — UNVERIFIED for VrseUb). `original_quality` is always None here: no
    pre-resolved fact places the quality flag in this payload — read it from parse_EWgK9e (PLAN P12).
    The two URLs are short-lived bearer credentials: never store, print or log them (P-20)."""
    if not isinstance(payload, list):
        return None
    item = _get(payload, 0)
    rec = _rpc_item(item)
    if rec is None and isinstance(item, list) and len(item) > 11 and not _is_dedup(item[3]) \
            and _is_dedup(item[11]):
        rec = _rpc_item(item[:3] + [item[11]] + item[4:])
    if rec is None:
        return None
    rec["display_url"] = _url_or_none(_get(payload, 1))
    rec["download_original_url"] = _url_or_none(_get(payload, 7))
    rec["original_quality"] = None
    return rec


def parse_fDcn4b(payload):
    """Item info ext (own library only) — request [mediaKey, 1, null, null, 1] → payload[0] =
    [photo_id, _, file name, taken_ms, tz_offset_ms, size, …, [11] dedupKey, …] (PLAN P3/P12, Step 2
    text) → {photo_id, filename, size_bytes, dedup_key, taken_ms, tz_offset_ms}, or None."""
    return _ext_record(_get(payload, 0)) if isinstance(payload, list) else None


def _quality_triple(v):
    """[takesUpSpace, spaceTaken, quality] (PLAN P12: quality 2 = original)."""
    return (isinstance(v, list) and len(v) >= 3 and _is_int(v[2])
            and (v[0] is None or isinstance(v[0], bool) or _is_int(v[0]))
            and (v[1] is None or _is_int(v[1])))


def parse_EWgK9e(payload):
    """Batch media info — request [[[ [[k1],[k2],…] ], [[null×24, [], null×11, []]]]] → payload[0][1] =
    per item [photo_id, [_, _, description, file name, _, _, taken_ms, tz_ms, upload_ms, size, …,
    [takesUpSpace, spaceTaken, quality]]] (PLAN P12) → {items: [{photo_id, filename, size_bytes,
    taken_ms, tz_offset_ms, upload_ms, original_quality}], rejected}.
    The quality triple is read as the LAST element of the info array, exactly as P12 lists it (its
    absolute index is elided there — UNVERIFIED); original_quality is None when it is absent."""
    if not isinstance(payload, list):
        return None
    head = _get(payload, 0)
    if head is None:
        rows = []  # nothing known about any requested key
    elif isinstance(head, list):
        rows = _get(head, 1)
        rows = [] if rows is None else rows
    else:
        return None
    if not isinstance(rows, list):
        return None
    items, rejected = [], 0
    for row in rows:
        key, info = _get(row, 0), _get(row, 1)
        if not (_is_key(key) and isinstance(info, list)):
            rejected += 1
            continue
        triple = info[-1] if info and _quality_triple(info[-1]) else None
        items.append({"photo_id": key, "filename": _str_or_none(_get(info, 3)),
                      "size_bytes": _int_or_none(_get(info, 9)), "taken_ms": _int_or_none(_get(info, 6)),
                      "tz_offset_ms": _int_or_none(_get(info, 7)), "upload_ms": _int_or_none(_get(info, 8)),
                      "original_quality": (triple[2] == 2) if triple else None})
    return {"items": items, "rejected": rejected}


def parse_pLFTfd(payload):
    """Download URL — request [[mediaKey, …], null, null]; send ONE key per call (several keys presumably
    yield a zip — P14) → payload[0] (PLAN P12; the video format is UNVERIFIED until 🚦 R2) →
    {download_url}, or None. The URL is a short-lived bearer credential: never store, print or log it."""
    if not isinstance(payload, list):
        return None
    url = _url_or_none(_get(payload, 0))
    return {"download_url": url} if url else None


PARSERS = {"lcxiM": parse_lcxiM, "EzkLib": parse_EzkLib, "swbisb": parse_swbisb, "VrseUb": parse_VrseUb,
           "fDcn4b": parse_fDcn4b, "EWgK9e": parse_EWgK9e, "pLFTfd": parse_pLFTfd}


# ================================================================================ 4. hash formats

def dedup_key(sha1_hex):
    """Google Photos' dedupKey for bytes whose SHA-1 is `sha1_hex` (RESEARCH F21) — a re-export of
    gpcapture.dedup_key, the single implementation (imported lazily: see the import rule above)."""
    import gpcapture
    return gpcapture.dedup_key(sha1_hex)


def _key_bytes(key):
    """27-char urlsafe key → its 20 bytes when it is the canonical encoding of 20 bytes, else None."""
    try:
        raw = base64.urlsafe_b64decode(key + "=")
    except ValueError:
        return None
    if len(raw) != 20 or base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=") != key:
        return None
    return raw


def normalize_dedup_key(key):
    """Any base64 spelling of a 20-byte SHA-1 (standard or urlsafe, padded or not) → the canonical
    dedupKey (urlsafe, unpadded, 27 chars), or None. PLAN P2: `+`→`-`, `/`→`_`, strip `=`."""
    if not isinstance(key, str):
        return None
    k = key.strip().replace("+", "-").replace("/", "_").rstrip("=")
    return k if DEDUP_KEY_RE.match(k) and _key_bytes(k) is not None else None


def dedup_keys_equal(a, b):
    """True when both are valid dedupKeys of the same bytes, whatever their base64 spelling."""
    na = normalize_dedup_key(a)
    return na is not None and na == normalize_dedup_key(b)


def sha1_hex_to_std_b64(sha1_hex):
    """SHA-1 hex → STANDARD base64 WITH padding (28 chars) — the swbisb request format — or None."""
    try:
        raw = bytes.fromhex(sha1_hex)
    except (TypeError, ValueError):
        return None
    return base64.b64encode(raw).decode("ascii") if len(raw) == 20 else None


def std_b64_to_dedup_key(b64):
    """A swbisb hash (standard base64, padded) → the dedupKey form (urlsafe, unpadded), or None."""
    return normalize_dedup_key(b64)


def dedup_key_to_sha1_hex(key):
    """A dedupKey (any base64 spelling) → the SHA-1 hex it encodes, or None — e.g. to look a mapped
    cloud item up with swbisb when no local SHA-1 is known."""
    k = normalize_dedup_key(key)
    return _key_bytes(k).hex() if k else None


# ================================================================================= 5. display text

_TZ_LINE_RE = re.compile(r"^GMT(?:[+\-]\d{1,2}:\d{2})?$")
_FILENAME_LINE_RE = re.compile(r"^[^\n/\\]{1,300}\.[A-Za-z0-9]{2,5}$")
_MEGAPIXEL_RE = re.compile(r"^\d+(?:\.\d+)?\s?MP$")  # "2.1MP" (US) / "2.1 MP" (UK) — never a filename
_NOT_FILENAME_PREFIXES = ("learn more", "original quality")
_GMT_RE = re.compile(r"^\s*(?:GMT|UTC)?\s*(?:([+\-\N{MINUS SIGN}])\s*(\d{1,2})(?::?(\d{2}))?)?\s*$",
                     re.IGNORECASE)


def gmt_offset_minutes(text):
    """'GMT' → 0 · 'GMT+08:00' → 480 · 'GMT-5:30' → -330 · also '+08:00', '+0800', 'UTC+8' (e.g. a
    CSV `gmt` column) → minutes east of UTC, or None."""
    if not isinstance(text, str) or not text.strip():
        return None
    m = _GMT_RE.match(text)
    if not m:
        return None
    sign, hours, minutes = m.groups()
    if sign is None:
        return 0 if text.strip().upper() in ("GMT", "UTC") else None
    h, mi = int(hours), int(minutes or 0)
    if h > 14 or mi >= 60:
        return None
    return -(h * 60 + mi) if sign in "-\N{MINUS SIGN}" else h * 60 + mi


def info_panel(text):
    """The info panel's fields from the item page's body text, or None when no "Details" block is shown:
    {details_text, date_text, time_text, tz_text, gmt_offset_min, filename} (None for any field not found).

    Locale-tolerant by construction: the GMT line anchors the block and the two lines above it are the
    date and the time AS DISPLAYED — US `Feb 5, 2022` / `Sat, 7:49 PM`, UK `21 Sept` / `Sun, 09:00`,
    the year omitted for current-year items. The filename is the first line shaped like `name.ext`
    that is not a megapixel line (`2.1MP` / `2.1 MP`); innerText has collapsed its whitespace runs —
    compare it with names_match(). `details_text` is the block's first 14 lines (≤ 700 chars), the raw
    text the crawler stores. A hint only (P-7): the page record carries the real time."""
    if not isinstance(text, str):
        return None
    i = text.find("\nDetails\n")
    if i < 0:
        i = text.find("Details")
        if i < 0:
            return None
    lines = [ln.strip() for ln in text[i:].split("\n")[1:40] if ln.strip()]
    out = {"details_text": "\n".join(lines[:14])[:700], "date_text": None, "time_text": None,
           "tz_text": None, "gmt_offset_min": None, "filename": None}
    for idx, ln in enumerate(lines[:12]):
        if _TZ_LINE_RE.match(ln):
            out["tz_text"] = ln
            if idx >= 1:
                out["time_text"] = lines[idx - 1]
            if idx >= 2:
                out["date_text"] = lines[idx - 2]
            break
    for ln in lines[:14]:
        if (_FILENAME_LINE_RE.match(ln) and not _MEGAPIXEL_RE.match(ln)
                and not ln.lower().startswith(_NOT_FILENAME_PREFIXES)):
            out["filename"] = ln
            break
    out["gmt_offset_min"] = gmt_offset_minutes(out["tz_text"])
    return out


_MONTHS = ("january", "february", "march", "april", "may", "june", "july", "august", "september",
           "october", "november", "december")
_TILE_DT_RE = re.compile(
    r"(?:(?P<mon_a>[^\W\d_]{3,9})\.?\s+(?P<day_a>\d{1,2})|(?P<day_b>\d{1,2})\s+(?P<mon_b>[^\W\d_]{3,9})\.?)"
    r"(?:,?\s+(?P<year>\d{4}))?,\s+(?P<hour>\d{1,2}):(?P<minute>\d{2}):(?P<second>\d{2})"
    r"(?:\s*(?P<ampm>[AaPp])\.?\s*[Mm]\.?)?")


def _month_number(token):
    t = token.lower()
    return next((i for i, name in enumerate(_MONTHS, 1) if len(t) >= 3 and name.startswith(t)), None)


def tile_label(label, default_year=None):
    """A result tile's aria-label → its naive LOCAL datetime to the second (RESEARCH F12), or None.

    US  `Video - Landscape - Feb 5, 2022, 7:49:46\\u202fPM`  (hyphens, month-day, 12 h, U+202F)
    UK  `Video – Landscape – 21 Sept 2026, 09:00:20`        (en-dashes, day-month, `Sept`, 24 h)
    Any kind/orientation prefix; U+202F / U+00A0 spaces; `Sep`/`Sept`/full month names. A label without
    a year takes `default_year` (None → None); a label without seconds gives None — a hint only (P-7)."""
    if not isinstance(label, str):
        return None
    matches = list(_TILE_DT_RE.finditer(unicodedata.normalize("NFKC", label)))
    if not matches:
        return None
    g = matches[-1].groupdict()
    month = _month_number(g["mon_a"] or g["mon_b"])
    year = int(g["year"]) if g["year"] else default_year
    if month is None or not _is_int(year):
        return None
    hour, minute, second = int(g["hour"]), int(g["minute"]), int(g["second"])
    if g["ampm"]:
        if not 1 <= hour <= 12:
            return None
        hour = hour % 12 + (12 if g["ampm"] in "Pp" else 0)
    try:
        return datetime(year, month, int(g["day_a"] or g["day_b"]), hour, minute, second)
    except ValueError:
        return None


# ========================================================================================= 6. names

_CHUNK_NAME_RE = re.compile(r"\.chunk\.(\d+)\.mkv$", re.IGNORECASE)
_HOLDER_NAME_RE = re.compile(r"\.holder\.mkv$", re.IGNORECASE)


def normalize_name(s):
    """PLAN P-6: NFKC + collapse every whitespace run to one space + strip + casefold ('' for non-text)."""
    if not isinstance(s, str):
        return ""
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", s)).strip().casefold()


def names_match(a, b):
    """'exact' when a == b, 'normalized' when only normalize_name() makes them equal (e.g. innerText
    collapsed a double space — PLAN P-6), else None (also for empty or non-text input)."""
    if not (isinstance(a, str) and isinstance(b, str) and a and b):
        return None
    if a == b:
        return "exact"
    return "normalized" if normalize_name(a) == normalize_name(b) else None


def chunk_index(name):
    """The NNN of `<base> [<sid>].chunk.NNN.mkv` as an int, or None for any other name."""
    m = _CHUNK_NAME_RE.search(name) if isinstance(name, str) else None
    return int(m.group(1)) if m else None


# ===================================================================================== 7. time math

def local_days(taken_ms, tz_offset_ms=None, tz_offset_min=None, window=1):
    """The local calendar days to day-search for an item taken at `taken_ms` (Google Photos files an
    item under its LOCAL date — RESEARCH F22), best first, as datetime.date:
      learned offset (tz_offset_ms, from a Google Photos record) → that day, then +1, −1 … ±window;
      predicted offset (tz_offset_min, e.g. the device timezone at push) → likewise;
      unknown → the UTC day, then UTC+1, UTC−1 (every real offset is within one day of UTC).
    [] for a missing time."""
    if not _is_number(taken_ms):
        return []
    if _is_number(tz_offset_ms):
        offset_ms = tz_offset_ms
    elif _is_number(tz_offset_min):
        offset_ms = tz_offset_min * 60_000
    else:
        offset_ms = 0
    spread = max(0, window) if _is_int(window) else 1
    try:
        center = (_EPOCH + timedelta(milliseconds=taken_ms + offset_ms)).date()
        days = [center]
        for k in range(1, spread + 1):
            days += [center + timedelta(days=k), center - timedelta(days=k)]
    except OverflowError:
        return []
    return days


def day_query(day):
    """A day as the search query: 'YYYY-MM-DD' (RESEARCH F11 — every date format finds the same items on
    the US account; the UK account is checked live at 🚦 R2). Accepts a date, a datetime or an ISO
    string; None otherwise."""
    if isinstance(day, datetime):
        day = day.date()
    if isinstance(day, date):
        return day.isoformat()
    if isinstance(day, str):
        try:
            return date.fromisoformat(day.strip()).isoformat()
        except ValueError:
            return None
    return None


def same_second(tile_dt, taken_ms, offset_ms=None):
    """True when a tile's naive local time (tile_label()) is the same second as `taken_ms`.
    offset_ms known → exact equality with the local time. offset_ms None → true when the two differ
    by a whole number of quarter hours within UTC−12 … UTC+14, i.e. the same second in SOME real zone."""
    if not isinstance(tile_dt, datetime) or not _is_number(taken_ms):
        return False
    try:
        utc = _EPOCH + timedelta(seconds=int(taken_ms // 1000))
        tile = tile_dt.replace(tzinfo=None, microsecond=0)
        if _is_number(offset_ms):
            return tile == utc + timedelta(milliseconds=offset_ms)
    except OverflowError:
        return False
    diff = (tile - utc).total_seconds()
    return diff % 900 == 0 and -12 * 3600 <= diff <= 14 * 3600


# =============================================================================== 8. query builders

def exact_name_query(obj):
    """The S2 query (PLAN P-8): the object's exact uploaded name. Uses obj['uploaded_name'] when set; else
    derives it from obj['filename'] + obj['short_id'] with the push naming rule — a whole file uploads as
    '<stem> [<short_id>]<ext>', a chunk or FLAC holder keeps its own (already tagged) name. None when
    neither is possible.

    >>> exact_name_query({"filename": "Example.Show.S01E02.1080p.mkv", "short_id": "a1b2c3"})
    'Example.Show.S01E02.1080p [a1b2c3].mkv'
    >>> exact_name_query({"filename": "Example.Movie.2019 [d4e5f6].chunk.002.mkv", "short_id": "d4e5f6"})
    'Example.Movie.2019 [d4e5f6].chunk.002.mkv'
    """
    if not isinstance(obj, Mapping):
        return None
    name = obj.get("uploaded_name")
    if isinstance(name, str) and name:
        return name
    filename, short_id = obj.get("filename"), obj.get("short_id")
    if not isinstance(filename, str) or not filename:
        return None
    if chunk_index(filename) is not None or _HOLDER_NAME_RE.search(filename):
        return filename
    if not short_id:
        return None
    stem, ext = os.path.splitext(filename)
    return f"{stem} [{short_id}]{ext}"


_LEADING_TAG_RE = re.compile(r"^\s*\[[^\[\]]*\]\s*")
_TRAILING_TAG_RE = re.compile(r"\s*\[[^\[\]]*\]\s*$")
_BOUNDARY = r"(?:^|(?<=[\s._\-]))"
_SERIES_EPISODE_RES = (re.compile(_BOUNDARY + r"S\d{1,3}\s?E\d{1,4}", re.IGNORECASE),
                       re.compile(_BOUNDARY + r"\d{1,2}x\d{2,3}(?![0-9A-Za-z])", re.IGNORECASE),
                       re.compile(_BOUNDARY + r"(?:Episode|Ep)[\s._]*\d{1,4}(?!\d)", re.IGNORECASE))
_ANIME_DASH_NUMBER_RE = re.compile(r"\s-\s\d{1,4}(?:\.\d)?(?![\d:])")  # "Show - 01 (1080p)"
_YEAR_TOKEN_RE = re.compile(r"(?<![0-9A-Za-z])(?:19|20)\d{2}(?![0-9A-Za-z])")
_RESOLUTION_TOKEN_RE = re.compile(r"(?<![0-9A-Za-z])(?:\d{3,4}[pi]|[248]K|UHD)(?![0-9A-Za-z])", re.IGNORECASE)


def _title_stem(filename):
    base = os.path.basename(filename)
    m = _CHUNK_NAME_RE.search(base) or _HOLDER_NAME_RE.search(base)
    stem = base[:m.start()] if m else os.path.splitext(base)[0]
    stem = _LEADING_TAG_RE.sub("", stem)
    while _TRAILING_TAG_RE.search(stem):
        stem = _TRAILING_TAG_RE.sub("", stem)
    return stem


def _movie_cut(stem):
    """Before the release year — the last year-like token ahead of the first resolution token, never one
    at the very start (`Blade.Runner.2049.2017` → `Blade.Runner.2049.`, `2001.A.Space.Odyssey.1968` keeps
    `2001`) — else before the resolution token, else the whole stem."""
    res = _RESOLUTION_TOKEN_RE.search(stem)
    limit = res.start() if res else len(stem)
    years = [y.start() for y in _YEAR_TOKEN_RE.finditer(stem, 0, limit) if y.start() > 0]
    return years[-1] if years else limit


def _title(filename, category):
    stem = _title_stem(filename)
    cat = (category or "").lower()
    cut = None
    if not cat.startswith("mov"):
        patterns = _SERIES_EPISODE_RES + ((_ANIME_DASH_NUMBER_RE,) if cat.startswith("ani") else ())
        starts = [m.start() for m in (p.search(stem) for p in patterns) if m]
        cut = min(starts) if starts else None
    if cut is None:
        cut = _movie_cut(stem)
    title = re.sub(r"[._]+", " ", stem[:cut])
    return re.sub(r"\s+", " ", title).strip(" -_,([")


def title_short_id_query(filename, short_id, category):
    """The S3 query (PLAN P-8): '<title> <short_id>' with a category-aware title — series/others: the
    stem up to the SxxEyy / NxNN / "Episode N" token; anime: the same or up to a bare ' - NN' episode
    number; movies (and any name without an episode token): the words before the release year / the
    resolution token. Leading `[group]` and trailing `[tag]` groups and the chunk/holder suffix are
    dropped. None without a short_id.

    >>> title_short_id_query("Example.Show.S02E03.Pilot.2015.1080p.BluRay.mkv", "a1b2c3", "tv")
    'Example Show a1b2c3'
    >>> title_short_id_query("Example.Movie.2019.2160p.UHD.BluRay [d4e5f6].chunk.001.mkv", "d4e5f6", "movies")
    'Example Movie d4e5f6'
    >>> title_short_id_query("[Grp] Sample Anime - 05 (1080p) [0a1b2c].mkv", "0a1b2c", "anime")
    'Sample Anime 0a1b2c'
    >>> title_short_id_query("Sample Saga - Episode 12 - Finale 1080p [SUB] [9f8e7d].mkv", "9f8e7d", "anime")
    'Sample Saga 9f8e7d'
    """
    if not short_id or not isinstance(filename, str):
        return None
    title = _title(filename, category)
    return f"{title} {short_id}" if title else str(short_id)


# ================================================================================= 9. DOM snippets
# Shared page-context scripts (the crawler's, verbatim). TILES_JS lists the visible result/timeline
# tiles as [{href, label}] (a[href*="photo/"], checkVisibility, width > 40 — RESEARCH F14); SCROLL_JS
# scrolls the largest scrollable pane (the virtualised grid) and returns [scrollTop, scrollHeight].

TILES_JS = """
const out=[];
document.querySelectorAll('a[href*="photo/"]').forEach(a=>{ const r=a.getBoundingClientRect();
  if(a.checkVisibility() && r.width>40) out.push({href:a.getAttribute('href'), label:a.getAttribute('aria-label')}); });
return out;"""
SCROLL_JS = """
const els=[...document.querySelectorAll('*')].filter(e=>{const s=getComputedStyle(e);
  return (s.overflowY=='auto'||s.overflowY=='scroll')&&e.scrollHeight>e.clientHeight+50&&e.checkVisibility()});
els.sort((a,b)=>b.scrollHeight-a.scrollHeight);
const el=els[0]; if(!el){window.scrollBy(0,900);return [window.scrollY, document.body.scrollHeight];}
el.scrollTop+=Math.max(400, Math.floor(el.clientHeight*0.8)); return [el.scrollTop, el.scrollHeight];"""
