"""IMP-C25 interim capture — identity facts for every object MediaVault uploads.

Why: Google Photos identifies an uploaded item by dedupKey = urlsafe-base64(SHA-1 of the
exact bytes) and dates it by the Matroska DateUTC in its header (docs/feature-fetch-datetime/
RESEARCH.md F1, F4, F21). Once a pushed master is replaced by a dummy those facts can no
longer be read locally, so prep and push record them as they happen.

Where: <folder>/<short_id>.gpcapture.json — beside the `uid` and `<short_id>.sha256`
sidecars cmd_prep already writes. The later IMP-C25 sweep reads these files to fill the
new identity fields of each library node.

Contract: every public function is best-effort and NEVER raises. main.py calls them only
after cmd_prep / cmd_push have fully succeeded (post-commit), and the file is not recorded
in the rollback journal, so it cannot change any command's result, rollback or resume
behaviour. No MediaVault command reads it yet. No subprocess, no adb, no network.
"""
import base64
import json
import os
import re
import tempfile
from datetime import datetime, timedelta, timezone

from mvcommon import cached_sha1

CAPTURE_SUFFIX = ".gpcapture.json"
CAPTURE_VERSION = 1
_NOTE = ("IMP-C25 interim capture: identity facts recorded at prep/push for the later Google "
         "Photos sweep (docs/feature-fetch-datetime). Safe to keep; no command reads it yet.")
_MKV_EPOCH = datetime(2001, 1, 1, tzinfo=timezone.utc)
_CHUNK_RE = re.compile(r"\.chunk\.(\d+)\.mkv$", re.IGNORECASE)

# Matroska element ids (EBML ids keep their length-marker bits)
_EBML, _SEGMENT, _INFO, _CLUSTER, _DATE_UTC = 0x1A45DFA3, 0x18538067, 0x1549A966, 0x1F43B675, 0x4461


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


def mkv_date_utc(path, max_bytes=2 * 1024 * 1024):
    """The Matroska Segment-Info DateUTC as 'YYYY-MM-DDTHH:MM:SSZ' — the date Google Photos
    shows for the file — or None (not Matroska, no DateUTC, or Info beyond max_bytes).
    Header-only read in pure Python; never raises."""
    try:
        with open(path, "rb") as fh:
            buf = fh.read(max_bytes)
        eid, n = _read_vint(buf, 0, True)
        if eid != _EBML:
            return None
        size, m = _read_vint(buf, n, False)
        pos = n + m + size
        eid, n = _read_vint(buf, pos, True)
        if eid != _SEGMENT:
            return None
        _, m = _read_vint(buf, pos + n, False)
        pos += n + m
        while pos + 2 < len(buf):
            eid, n = _read_vint(buf, pos, True)
            size, m = _read_vint(buf, pos + n, False)
            data = pos + n + m
            if eid == _INFO:
                p, end = data, min(data + size, len(buf))
                while p + 2 < end:
                    cid, cn = _read_vint(buf, p, True)
                    csize, cm = _read_vint(buf, p + cn, False)
                    cdata = p + cn + cm
                    if cid == _DATE_UTC and csize == 8 and cdata + 8 <= len(buf):
                        ns = int.from_bytes(buf[cdata:cdata + 8], "big", signed=True)
                        when = _MKV_EPOCH + timedelta(microseconds=ns // 1000)
                        return when.strftime("%Y-%m-%dT%H:%M:%SZ")
                    p = cdata + csize
                return None
            if eid == _CLUSTER:  # Info always precedes the first cluster
                return None
            pos = data + size
        return None
    except Exception:
        return None


def dedup_key(sha1_hex):
    """Google Photos' dedupKey for bytes whose SHA-1 is sha1_hex (RESEARCH.md F21), or None."""
    try:
        return base64.urlsafe_b64encode(bytes.fromhex(sha1_hex)).decode("ascii").rstrip("=") if sha1_hex else None
    except Exception:
        return None


def file_facts(path, sha256=None):
    """Identity facts for one local file. sha1/dedup_key are present only when the file was
    hashed by calculate_file_hash in this process (same-pass side-channel). Never raises."""
    facts = {"local_name": os.path.basename(path)}
    try:
        st = os.stat(path)
        facts["size_bytes"] = st.st_size
        facts["mtime_utc"] = datetime.fromtimestamp(st.st_mtime, timezone.utc).isoformat(
            timespec="seconds").replace("+00:00", "Z")
    except Exception:
        pass
    sha1 = cached_sha1(path)
    facts.update({"sha256": sha256, "sha1": sha1, "dedup_key": dedup_key(sha1), "date_utc": mkv_date_utc(path)})
    return facts


def capture_path(folder, short_id):
    return os.path.join(folder, f"{short_id}{CAPTURE_SUFFIX}")


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


def capture_after_prep(folder, manual_id, short_id, filepath, uploaded_name, sha256, tech_spec=None):
    """Record the prepped master's facts (called after cmd_prep committed). Never raises."""
    try:
        tech = tech_spec if isinstance(tech_spec, dict) else {}

        def mutate(doc):
            doc["prep"] = {
                "captured_at": _now(),
                "source": file_facts(filepath, sha256),
                "uploaded_name": uploaded_name,
                "tech": {k: tech.get(k) for k in ("duration_mins", "width_height", "resolution", "size_bytes")
                         if k in tech},
            }
        return _update(folder, short_id, manual_id, mutate)
    except Exception:
        return None


def snapshot_push_objects(paths, split_dir_name, short_id, chunk_hashes, whole_sha256, holders):
    """Read-only facts for each file cmd_push is about to upload — taken BEFORE the upload
    loop, because uploaded chunks are deleted locally. Mirrors cmd_push's remote naming:
    a file outside the split dir uploads as '<name> [<short_id>]<ext>'; a chunk or a FLAC
    holder keeps its own name. Never raises."""
    objs = []
    try:
        holder_hash = {h.get("holder_filename"): h.get("holder_hash") for h in (holders or []) if isinstance(h, dict)}
        for p in paths or []:
            name = os.path.basename(p)
            if split_dir_name not in p:
                base, ext = os.path.splitext(name)
                role, uploaded, index, sha256 = "whole", f"{base} [{short_id}]{ext}", None, whole_sha256
            else:
                m = _CHUNK_RE.search(name)
                if m:
                    role, index, sha256 = "chunk", int(m.group(1)), (chunk_hashes or {}).get(name)
                else:
                    role, index, sha256 = "holder", None, holder_hash.get(name)
                uploaded = name
            facts = file_facts(p, sha256)
            facts.update({"role": role, "uploaded_name": uploaded, "index": index})
            objs.append(facts)
    except Exception:
        pass
    return objs


def capture_after_push(folder, manual_id, short_id, objects, device_id=None, remote_dir=None, chunk_range=None):
    """Append this push's uploaded objects (called after cmd_push fully succeeded). Never raises."""
    try:
        def mutate(doc):
            doc.setdefault("pushes", []).append({
                "captured_at": _now(),
                "device_id": device_id,
                "remote_dir": remote_dir,
                "chunk_range": chunk_range,
                "complete": not chunk_range,
                "objects": objects or [],
            })
        return _update(folder, short_id, manual_id, mutate)
    except Exception:
        return None
