"""IMP-C25 identity capture (v2) — identity facts for every object MediaVault uploads.

Why: Google Photos identifies an uploaded item by dedupKey = urlsafe-base64(SHA-1 of the
exact bytes) and dates it by the Matroska DateUTC in its header (docs/feature-fetch-datetime/
RESEARCH.md F1, F4, F21). Once a pushed master is replaced by a dummy those facts can no
longer be read locally, so prep, push and the extras registration record them as they happen.

Where: <folder>/<short_id>.gpcapture.json — beside the `uid` and `<short_id>.sha256`
sidecars cmd_prep already writes. An extras item (IMP-D19) has its own file beside its own
`<short_id>.sha256`, in the item's directory <title folder>/<group_rel>/<dirname(sub_rel)>.
The reader is mvcommon.read_capture (v1 and v2 files alike); the later IMP-C25 steps fold
these facts into each object's `gphotos` block (PLAN P-11: a capture never proves that
Google Photos holds the object — that is confirmed later).

Format (capture_version 2, PLAN Step 5; v1 files written since cb4dd91 stay valid):
  {capture_version, manual_id, short_id, note,
   prep:   {captured_at, source: FACTS, uploaded_name,
            tech: {duration_mins, width_height, resolution, size_bytes}},
   pushes: [{captured_at, device_id, device_tz, device_tz_offset_min, remote_dir,
             chunk_range, complete,
             objects: [FACTS + {role, uploaded_name, index, width, height, approach_hint}]}],
   updated_at}
  FACTS = {local_name, size_bytes, mtime_utc, sha256, sha1, dedup_key, date_utc, duration_s}
  - manual_id: the library id; for an extras item '<title_id>::<group_rel>/<sub_rel>'
    (the label its remote .mvmeta.json carries too). prep: cmd_prep's master, or the extras
    scan's item. role: whole | chunk | holder (a FLAC carry-out holder).
  - New in v2: duration_s (the Matroska Segment-Info Duration), per push object width /
    height (the tech scan's width_height — a split keeps the master's frame size; never for
    a FLAC holder, whose video is a wrapper's) and approach_hint (the first P-8 stage these
    facts make usable: hash | taken_second | exact_name), per push device_tz /
    device_tz_offset_min (main._device_facts), and the extras captures.
  - Unknown values are null. A capture never loses what it knew about the same bytes: a
    re-capture and a push complete their unknown byte facts (SHA-1, DateUTC, duration) from
    the records already in the file for the same SHA-256 (e.g. the prep record's SHA-1 when
    the push runs in another process).

Contract: every public function is best-effort and NEVER raises. main.py calls the writers
only after cmd_prep / cmd_push have fully succeeded (post-commit), after the extras scan
saved and after an extras item uploaded; the file is not recorded in any rollback journal,
so it cannot change any command's result, rollback or resume behaviour. No subprocess, no
adb, no network here (the one device query lives in main._device_facts).
"""
import base64
import json
import math
import os
import re
import struct
import tempfile
from datetime import datetime, timedelta, timezone

import gpweb
import mvcommon
from mvcommon import cached_sha1

CAPTURE_SUFFIX = ".gpcapture.json"
CAPTURE_VERSION = 2
_NOTE = ("IMP-C25 identity capture: facts MediaVault recorded at prep, push and extras registration "
         "to find this object's exact Google Photos item later (docs/feature-fetch-datetime). "
         "Safe to keep; do not edit.")
_MKV_EPOCH = datetime(2001, 1, 1, tzinfo=timezone.utc)

# Matroska element ids (EBML ids keep their length-marker bits)
_EBML, _SEGMENT, _INFO, _CLUSTER, _DATE_UTC = 0x1A45DFA3, 0x18538067, 0x1549A966, 0x1F43B675, 0x4461
_TIMESTAMP_SCALE, _DURATION = 0x2AD7B1, 0x4489

# The per-chunk keys a v2 remote .mvmeta.json adds (OD-4 ruling; main.write_remote_mvmeta).
MVMETA_KEYS = ("sha1", "dedup_key", "date_utc", "uploaded_name")

# `date +%z` -> +HHMM (toybox) or +HH:MM; a tz database name such as Asia/Singapore, UTC, Etc/GMT+8.
_TZ_OFFSET_RE = re.compile(r"^([+-])(\d{2}):?(\d{2})$")
_TZ_NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_+\-]*(?:/[A-Za-z0-9_+\-]+)*$")


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _read_vint(buf, pos, keep_marker):
    """One EBML variable-length integer at buf[pos] -> (value, length)."""
    first = buf[pos]
    length, mask = 1, 0x80
    while length <= 8 and not first & mask:
        mask >>= 1
        length += 1
    if length > 8:
        raise ValueError("invalid EBML vint")
    value = first if keep_marker else first & (mask - 1)
    for i in range(1, length):
        value = (value << 8) | buf[pos + i]
    return value, length


def mkv_info(path, max_bytes=2 * 1024 * 1024):
    """{'date_utc', 'duration_s'} from the Matroska Segment Info: date_utc = its DateUTC as
    'YYYY-MM-DDTHH:MM:SSZ' (the date Google Photos shows for the file), duration_s = its
    Duration in seconds (TimestampScale applied, millisecond precision). Each is None when
    unknown (not Matroska, no such element, or Info beyond max_bytes). Header-only read in
    pure Python; never raises."""
    info = {"date_utc": None, "duration_s": None}
    try:
        with open(path, "rb") as fh:
            buf = fh.read(max_bytes)
        eid, n = _read_vint(buf, 0, True)
        if eid != _EBML:
            return info
        size, m = _read_vint(buf, n, False)
        pos = n + m + size
        eid, n = _read_vint(buf, pos, True)
        if eid != _SEGMENT:
            return info
        _, m = _read_vint(buf, pos + n, False)
        pos += n + m
        while pos + 2 < len(buf):
            eid, n = _read_vint(buf, pos, True)
            size, m = _read_vint(buf, pos + n, False)
            data = pos + n + m
            if eid == _INFO:
                p, end = data, min(data + size, len(buf))
                scale, duration = 1_000_000, None   # TimestampScale defaults to 1 ms (in ns)
                while p + 2 < end:
                    cid, cn = _read_vint(buf, p, True)
                    csize, cm = _read_vint(buf, p + cn, False)
                    cdata = p + cn + cm
                    if cdata + csize <= len(buf):
                        raw = buf[cdata:cdata + csize]
                        if cid == _DATE_UTC and csize == 8:
                            ns = int.from_bytes(raw, "big", signed=True)
                            when = _MKV_EPOCH + timedelta(microseconds=ns // 1000)
                            info["date_utc"] = when.strftime("%Y-%m-%dT%H:%M:%SZ")
                        elif cid == _TIMESTAMP_SCALE and 1 <= csize <= 8:
                            scale = int.from_bytes(raw, "big") or scale
                        elif cid == _DURATION and csize in (4, 8):
                            duration = struct.unpack(">f" if csize == 4 else ">d", raw)[0]
                    p = cdata + csize
                if duration is not None and math.isfinite(duration) and duration >= 0:
                    info["duration_s"] = round(duration * scale / 1e9, 3)
                return info
            if eid == _CLUSTER:  # Info always precedes the first cluster
                return info
            pos = data + size
        return info
    except Exception:
        return info


def mkv_date_utc(path, max_bytes=2 * 1024 * 1024):
    """The Matroska Segment-Info DateUTC as 'YYYY-MM-DDTHH:MM:SSZ' — the date Google Photos
    shows for the file — or None (see mkv_info). Never raises."""
    return mkv_info(path, max_bytes)["date_utc"]


def dedup_key(sha1_hex):
    """Google Photos' dedupKey for bytes whose SHA-1 is sha1_hex (RESEARCH.md F21), or None."""
    try:
        return base64.urlsafe_b64encode(bytes.fromhex(sha1_hex)).decode("ascii").rstrip("=") if sha1_hex else None
    except Exception:
        return None


def file_facts(path, sha256=None):
    """Identity facts for one local file: local_name, size_bytes, mtime_utc, sha256 (as given),
    sha1 + dedup_key (present only when the file was hashed by calculate_file_hash in this
    process — the same-pass side-channel), date_utc and duration_s (its Matroska header).
    Never raises."""
    facts = {"local_name": os.path.basename(path)}
    try:
        st = os.stat(path)
        facts["size_bytes"] = st.st_size
        facts["mtime_utc"] = datetime.fromtimestamp(st.st_mtime, timezone.utc).isoformat(
            timespec="seconds").replace("+00:00", "Z")
    except Exception:
        pass
    sha1 = cached_sha1(path)
    info = mkv_info(path)
    facts.update({"sha256": sha256, "sha1": sha1, "dedup_key": dedup_key(sha1),
                  "date_utc": info["date_utc"], "duration_s": info["duration_s"]})
    return facts


def approach_hint(facts):
    """The first P-8 fetch stage these facts make usable — 'hash' (the swbisb content-hash
    lookup needs the SHA-1), else 'taken_second' (needs the Matroska DateUTC — all chunks of
    a split share its second), else 'exact_name' (always possible). A push-time HINT for the
    locator until the object is confirmed (OD-6: the verified approach is recorded then)."""
    facts = facts if isinstance(facts, dict) else {}
    if facts.get("sha1"):
        return "hash"
    if facts.get("date_utc"):
        return "taken_second"
    return "exact_name"


def device_facts_from_output(text):
    """Parse the output of `getprop persist.sys.timezone; date +%z` (main._device_facts) into
    {'device_tz': 'Asia/Singapore', 'device_tz_offset_min': 480} — only the facts that parse:
    the timezone is the first line shaped like a tz database name, the offset the first
    '+HHMM' / '-HH:MM' line (at most 14:00). Never raises; anything else -> {}."""
    tz, offset = None, None
    try:
        for line in text.splitlines() if isinstance(text, str) else ():
            line = line.strip()
            m = _TZ_OFFSET_RE.match(line)
            if m:
                hours, minutes = int(m.group(2)), int(m.group(3))
                if offset is None and minutes < 60 and hours * 60 + minutes <= 14 * 60:
                    offset = (hours * 60 + minutes) * (-1 if m.group(1) == "-" else 1)
            elif tz is None and len(line) <= 64 and _TZ_NAME_RE.match(line):
                tz = line
    except Exception:
        return {}
    facts = {}
    if tz is not None:
        facts["device_tz"] = tz
    if offset is not None:
        facts["device_tz_offset_min"] = offset
    return facts


def capture_path(folder, short_id):
    return os.path.join(folder, f"{short_id}{CAPTURE_SUFFIX}")


def extras_label(title_id, group_rel, sub_rel):
    """The manual_id an extras item's capture records: '<title_id>::<group_rel>/<sub_rel>' —
    the label push_one_extra gives the item's remote .mvmeta.json too."""
    return f"{title_id}::{group_rel}/{sub_rel}"


def _known(folder, short_id):
    """What the capture file already records, per SHA-256 (mvcommon.read_capture: the prep
    record and every push, v1 or v2). Never raises."""
    try:
        return mvcommon.read_capture(folder, short_id)
    except Exception:
        return {}


def _complete(facts, known):
    """Fill `facts`' UNKNOWN byte facts — sha1 + dedup_key, date_utc, duration_s — from
    `known`, what the capture already recorded for the SAME bytes (same SHA-256). A known
    value is never replaced. Returns `facts`."""
    if isinstance(known, dict):
        if not facts.get("sha1") and known.get("sha1"):
            facts["sha1"] = known["sha1"]
            facts["dedup_key"] = known.get("dedup_key") or dedup_key(known["sha1"])
        for key in ("date_utc", "duration_s"):
            if facts.get(key) is None and known.get(key) is not None:
                facts[key] = known[key]
    return facts


def _update(folder, short_id, manual_id, mutate):
    """Read-modify-write the capture file atomically (temp file + os.replace).
    Returns the path, or None after printing one warning line. Never raises."""
    tmp = None
    try:
        path = capture_path(folder, short_id)
        doc = {}
        if os.path.exists(path):
            try:
                with open(path, encoding="utf-8") as fh:
                    doc = json.load(fh)
            except Exception:
                doc = {}  # torn/unreadable: rewritten fresh (it only holds re-derivable facts)
        doc["capture_version"] = CAPTURE_VERSION
        doc["manual_id"] = manual_id
        doc["short_id"] = short_id
        doc["note"] = _NOTE
        mutate(doc)
        doc["updated_at"] = _now()
        fd, tmp = tempfile.mkstemp(prefix=f".{short_id}.", suffix=".gpcapture.tmp", dir=folder)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(doc, fh, indent=1, ensure_ascii=False)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
        return path
    except Exception as e:
        try:
            if tmp and os.path.exists(tmp):
                os.remove(tmp)
        except Exception:
            pass
        try:
            print(f"   ⚠️  Identity capture not written ({type(e).__name__}: {e}) — prep/push unaffected.")
        except Exception:
            pass
        return None


def _write_prep(folder, manual_id, short_id, filepath, uploaded_name, sha256, tech_spec):
    """The `prep` record writer shared by capture_after_prep and capture_after_extras_scan. A
    re-capture of the same bytes keeps what the file already knew about them."""
    tech = tech_spec if isinstance(tech_spec, dict) else {}
    source = _complete(file_facts(filepath, sha256), _known(folder, short_id).get(sha256))

    def mutate(doc):
        doc["prep"] = {
            "captured_at": _now(),
            "source": source,
            "uploaded_name": uploaded_name,
            "tech": {k: tech.get(k) for k in ("duration_mins", "width_height", "resolution", "size_bytes")
                     if k in tech},
        }
    return _update(folder, short_id, manual_id, mutate)


def capture_after_prep(folder, manual_id, short_id, filepath, uploaded_name, sha256, tech_spec=None):
    """Record the prepped master's facts (called after cmd_prep committed). Never raises."""
    try:
        return _write_prep(folder, manual_id, short_id, filepath, uploaded_name, sha256, tech_spec)
    except Exception:
        return None


def capture_after_extras_scan(title_folder, title_id, group_rel, item):
    """Record ONE extras item's facts after the extras scan saved it (IMP-D19; PLAN Step 5c) —
    the extras analogue of capture_after_prep, in the same document shape, at
    <item dir>/<item short_id>.gpcapture.json with <item dir> = <title folder>/<group_rel>/
    <dirname(sub_rel)>: the item's own directory, beside its <short_id>.sha256, where
    mvcommon.read_capture looks. The SHA-1 comes from the scan's own hash pass. Pass the item
    AS STORED after the merge, and only when its stored hash is the scanned file's
    (main._extras_scan_merge_and_save decides). Never raises."""
    try:
        sub_rel = item.get("sub_rel") or item.get("filename") or ""
        path = os.path.join(title_folder, group_rel.replace("/", os.sep), sub_rel.replace("/", os.sep))
        short_id = item.get("short_id")
        uploaded_name = mvcommon.remote_upload_name(os.path.basename(path), short_id, "extra_whole")
        return _write_prep(os.path.dirname(path), extras_label(title_id, group_rel, sub_rel), short_id,
                           path, uploaded_name, item.get("hash"), item.get("tech_spec"))
    except Exception:
        return None


def snapshot_push_objects(paths, parts_dir, short_id, chunk_hashes, whole_sha256, holders, tech_spec=None):
    """Read-only facts for each file a push is about to upload — taken BEFORE the upload
    loop, because uploaded chunks are deleted locally. Mirrors the pushers' remote naming by
    sharing cmd_push's rule, mvcommon.in_parts_dir (IMP-C28): a file directly in the push's
    chunk dir `parts_dir` (cmd_push's `_parts` dir; push_one_extra the item's own chunk dir)
    is a chunk or a FLAC holder and keeps its own name; any other file uploads as
    '<name> [<short_id>]<ext>'. width/height come from `tech_spec` (the tech scan's 'WxH'
    width_height) for whole files and chunks, never for a FLAC holder. Never raises."""
    objs = []
    try:
        holder_hash = {h.get("holder_filename"): h.get("holder_hash") for h in (holders or []) if isinstance(h, dict)}
        frame = mvcommon._WIDTH_HEIGHT_RE.match(str(tech_spec.get("width_height") or "")) \
            if isinstance(tech_spec, dict) else None       # the reader's own rule (read_capture)
        width, height = (int(frame.group(1)), int(frame.group(2))) if frame else (None, None)
        for p in paths or []:
            name = os.path.basename(p)
            if not mvcommon.in_parts_dir(p, parts_dir):
                base, ext = os.path.splitext(name)
                role, uploaded, index, sha256 = "whole", f"{base} [{short_id}]{ext}", None, whole_sha256
            else:
                index = gpweb.chunk_index(name)
                if index is not None:
                    role, sha256 = "chunk", (chunk_hashes or {}).get(name)
                else:
                    role, sha256 = "holder", holder_hash.get(name)
                uploaded = name
            facts = file_facts(p, sha256)
            framed = role != "holder"
            facts.update({"role": role, "uploaded_name": uploaded, "index": index,
                          "width": width if framed else None, "height": height if framed else None})
            objs.append(facts)
    except Exception:
        pass
    return objs


def mvmeta_identity(folder, short_id, objects):
    """{sha256: {sha1, dedup_key, date_utc, uploaded_name}} — known values only — for the v2
    per-chunk fields of the remote .mvmeta.json (main.write_remote_mvmeta; OD-4): this push's
    snapshot `objects`, over what the capture file already records for the same bytes (the
    prep record's SHA-1; the chunks an earlier chunk_range push carried). Read-only; never
    raises ({} on any problem)."""
    try:
        identity = {}
        for sha256, facts in _known(folder, short_id).items():
            identity[sha256] = {k: facts[k] for k in MVMETA_KEYS if facts.get(k)}
        for obj in objects or []:
            sha256 = obj.get("sha256") if isinstance(obj, dict) else None
            if not isinstance(sha256, str) or not sha256:
                continue
            merged = identity.setdefault(sha256, {})
            merged.update((k, obj[k]) for k in MVMETA_KEYS if obj.get(k))
        return identity
    except Exception:
        return {}


def capture_after_push(folder, manual_id, short_id, objects, device_id=None, remote_dir=None, chunk_range=None,
                       device_facts=None):
    """Append this push's uploaded objects (called after cmd_push fully succeeded, or after an
    extras item uploaded). Each object's unknown byte facts are completed from what the file
    already records for the same bytes, then it gets its approach_hint; `device_facts`
    (main._device_facts) adds the device's timezone to the push. Never raises."""
    try:
        known = _known(folder, short_id)
        objs = []
        for obj in objects or []:
            if isinstance(obj, dict):
                sha256 = obj.get("sha256")
                o = _complete(dict(obj), known.get(sha256) if isinstance(sha256, str) else None)
                o["approach_hint"] = approach_hint(o)
                objs.append(o)
        device = device_facts if isinstance(device_facts, dict) else {}
        tz, offset = device.get("device_tz"), device.get("device_tz_offset_min")

        def mutate(doc):
            doc.setdefault("pushes", []).append({
                "captured_at": _now(),
                "device_id": device_id,
                "device_tz": tz if isinstance(tz, str) and tz else None,
                "device_tz_offset_min": offset if isinstance(offset, int) and not isinstance(offset, bool) else None,
                "remote_dir": remote_dir,
                "chunk_range": chunk_range,
                "complete": not chunk_range,
                "objects": objs,
            })
        return _update(folder, short_id, manual_id, mutate)
    except Exception:
        return None
