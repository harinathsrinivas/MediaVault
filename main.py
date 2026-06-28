import os
import json
import sys
import subprocess
import shutil
import re
import math
import time
import stat
import hashlib
import difflib
import tempfile
import webbrowser
import requests
from datetime import datetime, timezone, timedelta
from urllib.parse import urlparse
from pymediainfo import MediaInfo

# Ensure emoji/Unicode output works on Windows consoles
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
if hasattr(sys.stderr, 'reconfigure'):
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')

# ==========================================
#               CONFIGURATION
# ==========================================
# Shared library path/folder constants + helpers now live in mvcommon.py
# (the single source of truth imported by both main.py and mainfetch.py).
from mvcommon import (
    LIBRARY_MOVIES, LIBRARY_SERIES, LIBRARY_ANIME, LOCAL_ROOT, MKVMERGE_PATH,
    SPLIT_DIR_NAME, CHECKSUM_DIR_NAME, RESTORE_DIR_NAME, VIDEO_EXTENSIONS,
    load_library, save_library, generate_short_id, calculate_file_hash,
    human_readable_size, parse_size_str, retry, episode_num_from_id,
)
# Imported as a MODULE (not via `from ... import web_host`) on purpose: the web
# config getters + the token-store helpers must be called module-qualified
# (mvcommon.web_host(), mvcommon.list_tokens(), ...) so a test that monkeypatches
# them (or mvcommon.MVTOKENS_PATH) is honoured here — see the binding-hazard note
# in mvcommon's RUNTIME CONFIG section.
import mvcommon

REMOTE_ROOT = "/sdcard/Media"  # Your Pixel Root
FFMPEG_PATH = r"C:\Users\harin\AppData\Roaming\Emby-Server\system\ffmpeg.exe"
DUMMY_MAX_BYTES = 200_000
# Per-container dummy-encode recipe. PCM containers (.mkv/.avi) use silent
# anullsrc at 0.05 s — pcm_s16le produces ~10 KB regardless of audio content.
# AAC containers (.mp4/.mov) use a 440 Hz sine tone at 0.5 s — AAC compresses
# silence near-perfectly (~2 KB at any bitrate), so we drive it with a tone to
# give the encoder real entropy. The .mp4 recipe (sine + aac 64k + 0.5 s) was
# proven in Plex at 6,484 bytes.
DUMMY_RECIPE_BY_EXT = {
    ".mkv": {
        "audio_codec": "pcm_s16le",
        "audio_extra": [],
        "audio_source": "anullsrc=cl=stereo:r=44100",
        "duration": "0.05",
    },
    ".avi": {
        "audio_codec": "pcm_s16le",
        "audio_extra": [],
        "audio_source": "anullsrc=cl=stereo:r=44100",
        "duration": "0.05",
    },
    ".mp4": {
        "audio_codec": "aac",
        "audio_extra": ["-b:a", "64k"],
        "audio_source": "sine=frequency=440:sample_rate=44100",
        "duration": "0.5",
    },
    ".mov": {
        "audio_codec": "aac",
        "audio_extra": ["-b:a", "64k"],
        "audio_source": "sine=frequency=440:sample_rate=44100",
        "duration": "0.5",
    },
}
MAINFETCH_SCRIPT = "mainfetch.py"  # Name of the automation script

# Human-friendly aliases for the user's ADB devices. Maps alias -> serial.
# Edit this dict when the physical phones change.
DEVICE_ALIASES = {"movies": "FA69H0300200", "series": "FA75V0303405", "others": "<NEW_PIXEL_SERIAL>"}  # TODO(user): real Others Pixel serial — prerequisite

# Remote push reliability conventions (rclone "chunker"-style).
# AUTO-ROLLBACK SEAM: each chunk is uploaded to "<final>.partial" then atomically
# renamed; remnant "<chunk>.partial" files are the only thing a push rollback must
# `adb shell rm` (Google Photos never indexes a .partial as a complete chunk).
PARTIAL_SUFFIX = ".partial"
MVMETA_SUFFIX = ".mvmeta.json"  # Remote disaster-recovery sidecar mirroring split_info
# IMP-C8: post-push remote hash verification. Gated off here; config-file
# support (toggle without editing source) arrives with IMP-A5.
PUSH_VERIFY_REMOTE = False

# CATEGORY_ROOTS — THE single source of truth for "which on-disk subfolders under
# LOCAL_ROOT hold each content category's media". Every disk walker derives its
# roots from this ONE table (cmd_recover --scan, cmd_scan_unprepped,
# collect_reclaimable PASS 1) — no walker hardcodes folder names anymore.
# Insertion order is Movies -> Series -> Anime -> other, so the flattened walk
# order reproduces the historical Movies/Series/Anime sequence and appends Others.
# A category maps to a LIST of subdir names: the three original categories own one
# folder each; "other" (IMP-D18) may span many — Sports now; append "Documentary"
# here later as a sibling, a pure one-line data edit with NO walker code change.
#
# NOTE: build_tree does NOT consume this table. An oth- leaf must nest with its OWN
# subfolder (Sports/…) as a top folder under the Others bucket, so build_tree keeps
# resolving cat=="other" to LOCAL_ROOT itself (see _CATEGORY_ROOT_SUBDIR below).
CATEGORY_ROOTS = {
    "movies": ["Movies"],
    "series": ["Series"],
    "anime":  ["Anime"],
    "other":  ["Sports"],
}

# ENTRY_TYPE_KEYS — THE single source of truth for "what keys does each top-level
# library entry type have", and the seam future entry types extend (IMP-H3).
#
# A library_*.json maps an id -> entry dict. There are three top-level entry
# shapes (verified against every `"type": ...` write + `.get("type")` read in
# main.py / mainfetch.py):
#   - "leaf"           the implicit, no-`type` entry a prepped file produces
#                      (cmd_prep, main.py ~906): owns a physical file on disk
#                      (folder_path + filename), plus status/uploaded/hash/
#                      short_id/metadata/tech_spec, optional parent_id/split_info.
#                      A MISSING `type` key IS a leaf — leaves carry no `type`.
#   - "season_map"     a virtual TV-season container (cmd_prep, main.py ~881):
#                      type/folder_path/children/total_episodes. It has a
#                      folder_path but NO filename, so it owns no file of its own.
#   - "multi_ep_alias" a thin combined-episode alias (cmd_prep_season, main.py
#                      ~1080, IMP-E13/PR #21): ONLY {type, alias_of, parent_id}.
#                      No physical-file keys at all — dereferencing folder_path/
#                      filename on it is the PR #21 crash class.
#
# `required` = keys that distinguish the type and are always present; it is the
# minimal set, not the exhaustive set (leaves also carry hash/metadata/etc.).
# `physical` = "this entry owns a physical file on disk" (has folder_path AND
# filename). Only "leaf" is physical; season_map and multi_ep_alias are virtual,
# so any whole-library iterator that dereferences a physical-only key MUST first
# skip (or _resolve_alias) every non-physical type.
#
# This constant is documentation + a test seam (tests/test_entry_schema_guard.py
# enforces it). It is intentionally NOT wired into the cmd_* code paths — the
# guard test is the enforcement. When you add or change an entry type, update
# THIS registry AND the guard test's non-physical set.
ENTRY_TYPE_KEYS = {
    "leaf":           {"required": {"folder_path", "filename", "status"}, "physical": True},
    "season_map":     {"required": {"folder_path", "children"},           "physical": False},
    "multi_ep_alias": {"required": {"alias_of", "parent_id"},             "physical": False},
}


# ==========================================
#               UTILITIES
# ==========================================
# load_library, save_library, generate_short_id, calculate_file_hash,
# human_readable_size, and parse_size_str now live in mvcommon.py (imported above).
def resolve_device(device_arg):
    """Map a CLI device alias to an ADB serial. Returns None if arg is None.
    Unknown aliases pass through as-is so any raw serial works."""
    if device_arg is None:
        return None
    return DEVICE_ALIASES.get(device_arg, device_arg)


def get_tech_specs(filepath):
    print(f"   > Scanning Tech Specs (Deep Scan)...")
    try:
        media_info = MediaInfo.parse(filepath)
    except:
        return {"error": "Could not parse file"}

    specs = {
        "resolution": "Unknown", "width_height": "Unknown", "video_codec": "Unknown",
        "hdr": "SDR", "frame_rate": "Unknown", "audio": "Unknown",
        "audio_channels": "Unknown", "audio_language": "Unknown", "subtitles": [],
        "duration_mins": 0, "size_bytes": os.path.getsize(filepath)
    }

    for track in media_info.tracks:
        if track.track_type == "General" and track.duration:
            specs['duration_mins'] = int(track.duration / 60000)
        elif track.track_type == "Video":
            width, height = track.width, track.height
            specs['width_height'] = f"{width}x{height}"
            if width and width >= 3800:
                specs['resolution'] = "2160p"
            elif width and width >= 1900:
                specs['resolution'] = "1080p"
            elif width and width >= 1260:
                specs['resolution'] = "720p"
            else:
                specs['resolution'] = f"{height}p"
            if track.format: specs['video_codec'] = track.format
            if track.hdr_format:
                specs['hdr'] = track.hdr_format
            elif track.commercial_name and "HDR" in track.commercial_name:
                specs['hdr'] = track.commercial_name
        elif track.track_type == "Audio":
            if specs['audio'] == "Unknown":
                specs['audio'] = track.commercial_name if track.commercial_name else track.format
                specs['audio_channels'] = track.channel_s
                if track.language: specs['audio_language'] = track.language
        elif track.track_type == "Text":
            if track.language and track.language not in specs['subtitles']:
                specs['subtitles'].append(track.language)
    return specs


def parse_metadata_from_id(manual_id):
    parts = manual_id.split('-')
    meta = {"title": manual_id, "year": None, "genre": [], "added_date": datetime.now().strftime("%Y-%m-%d")}
    # Basic attempt to parse year if present
    for part in parts:
        if part.isdigit() and len(part) == 4:
            meta["year"] = int(part)
    return meta


# ==========================================
#         SPLIT & MERGE LOGIC
# ==========================================
def split_video_file(input_path, output_dir, method, value_str, file_id=""):
    import math  # Needed for ceil calculation

    filename_base = os.path.splitext(os.path.basename(input_path))[0]

    # [UPDATED] Attach UID to chunk names
    # Result: MovieName [1a2b3c].chunk.001.mkv
    id_tag = f" [{file_id}]" if file_id else ""
    output_pattern = os.path.join(output_dir, f"{filename_base}{id_tag}.chunk.%03d.mkv")

    split_arg = ""
    try:
        val = float(value_str)
        total_size_bytes = os.path.getsize(input_path)
    except ValueError:
        print(f"❌ Error: Value '{value_str}' is not a number.")
        return []

    # --- BALANCED SPLIT LOGIC ---
    if method in ["SIZE_MB", "SIZE_GB"]:
        # 1. Determine User's Limit in Bytes
        limit_bytes = 0
        if method == "SIZE_MB":
            limit_bytes = val * 1024 * 1024
        elif method == "SIZE_GB":
            limit_bytes = val * 1024 * 1024 * 1024

        # 2. Calculate Minimum Number of Chunks needed (Ceiling)
        #    e.g. 15GB file / 10GB limit = 1.5 -> requires 2 chunks
        num_chunks = math.ceil(total_size_bytes / limit_bytes)

        # 3. Calculate the Balanced Size per Chunk
        # [FIXED] Convert to MB first, take ceiling, and add a 10MB buffer.
        # MKVMerge cuts at keyframes. If we are too precise, keyframe drift leaves a tiny 3rd chunk.
        total_size_mb = total_size_bytes / (1024 * 1024)
        split_size_mb = int(math.ceil(total_size_mb / num_chunks)) + 10

        if split_size_mb < 1: split_size_mb = 1

        split_arg = f"{split_size_mb}M"
        print(f"   > ⚖️  Balanced Split: {num_chunks} chunks of ~{split_size_mb}MB each.")

    elif method == "COUNT":
        # Existing logic for fixed count
        parts = int(val)
        if parts <= 0: return []

        # [FIXED] Add small buffer to prevent keyframe drift leftover chunk
        total_size_mb = total_size_bytes / (1024 * 1024)
        approx_size_mb = int(math.ceil(total_size_mb / parts)) + 10

        # Safety for very small files
        if approx_size_mb < 1: approx_size_mb = 1

        split_arg = f"{approx_size_mb}M"
        print(f"   > ⚖️  Split by Count: {parts} chunks of ~{approx_size_mb}MB each.")

    else:
        return []

    # Command Execution.
    # mkvmerge v97 formats the --split output name via libfmt, so any literal `{`/`}`
    # in the path (e.g. a `{tmdb-12345}` Plex/Emby/Jellyfin folder token) is read as a
    # format field and mkvmerge dies with `fmt::format_error: argument not found`
    # (exit 3). Escape them as `{{`/`}}` for the -o arg ONLY — mkvmerge renders them
    # back to single braces and writes to the real folder. (A plain merge -o is taken
    # literally and must NOT be escaped — see merge_video_files; verified mkvmerge v97.)
    mkv_out = output_pattern.replace("{", "{{").replace("}", "}}")
    cmd = [MKVMERGE_PATH, "-o", mkv_out, "--split", f"size:{split_arg}", input_path]
    try:
        # Added stderr capture to output real error messages
        subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        chunks = sorted([os.path.join(output_dir, f) for f in os.listdir(output_dir) if f.endswith(".mkv")])
        print(f"   > Done. Generated {len(chunks)} parts.")
        return chunks
    except subprocess.CalledProcessError as e:
        print(f"❌ Error running mkvmerge for splitting: {e}");
        return []
    except FileNotFoundError:
        print(f"❌ Error: mkvmerge not found at {MKVMERGE_PATH}");
        return []


def merge_video_files(chunk_paths, output_path, seed=None):
    print(f"   > 🛠️  Merging {len(chunk_paths)} chunks...")
    # Syntax: mkvmerge -o output.mkv chunk1 +chunk2 +chunk3 ...
    # When a seed is supplied, prepend the GLOBAL `--deterministic <seed>` option
    # (it must precede -o) so the merged container is byte-identical across runs
    # (mkvmerge v97.0, confirmed in the planning spike). seed=None keeps the argv
    # byte-for-byte identical to the original, non-deterministic merge.
    cmd = [MKVMERGE_PATH]
    if seed is not None:
        cmd += ["--deterministic", seed]
    cmd += ["-o", output_path]
    cmd.append(chunk_paths[0])
    for chunk in chunk_paths[1:]:
        cmd.append(f"+{chunk}")

    try:
        subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL)
        print("   > Merge Complete.")
        return True
    except subprocess.CalledProcessError as e:
        print(f"❌ Error merging: {e}");
        return False
    except FileNotFoundError:
        print(f"❌ Error: mkvmerge not found.");
        return False


# --- Deterministic-rehash helpers (canonical whole-file hash for split files) ---
# These are pure/standalone helpers wired into cmd_restore / cmd_push / cmd_replace
# by later steps. The merge SEED is the entry's short_id (callers pass it); there
# is intentionally no seed-generator here.
def _rehashed_at():
    """Compact ISO-8601 UTC timestamp with a trailing 'Z' (e.g.
    '2026-06-07T14:03:22Z'). Stamped when the canonical hash is blessed."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _current_merge_tool():
    """Return the running mkvmerge version as 'mkvmerge vNN.N' (e.g.
    'mkvmerge v97.0'), parsed from `mkvmerge --version`. On ANY failure
    (binary missing, parse miss, non-zero exit) return 'mkvmerge (unknown)'.
    NEVER raises — it is only metadata for version-drift triage."""
    try:
        out = subprocess.run(
            [MKVMERGE_PATH, "--version"],
            capture_output=True, text=True,
        ).stdout or ""
        m = re.search(r"v\d+(?:\.\d+)+", out)
        if m:
            return f"mkvmerge {m.group(0)}"
    except Exception:
        pass
    return "mkvmerge (unknown)"


def bless_or_verify_merged_hash(entry, new_hash):
    """Decide what a split restore should do with the freshly-merged file's hash.

    PURE: reads ONLY `entry["re_hashed"]` / `entry["hash"]` and the supplied
    `new_hash`; performs NO mutation, NO journal call, NO I/O. The caller acts on
    the returned string:

      "bless"    — entry has never been canonically re-hashed
                   (`re_hashed` is not True). The merged hash becomes the new
                   canonical truth: the caller writes hash + the split_info
                   canonical fields, then proceeds across the PONR.
      "ok"       — entry was already blessed (`re_hashed is True`) AND the new
                   merge reproduced the stored canonical hash. The caller leaves
                   hash untouched and proceeds across the PONR.
      "mismatch" — entry was already blessed but the deterministic merge did NOT
                   reproduce the stored hash → corruption / tool drift. The caller
                   raises the alarm and rolls back PRE-PONR (chunks kept).

    Keeping the decision here — and every side effect in cmd_restore — makes the
    three-way policy trivially unit-testable in isolation (Step 9)."""
    if entry.get("re_hashed") is True:
        return "ok" if new_hash == entry.get("hash") else "mismatch"
    return "bless"


def _will_split(file_size, split_method, split_val):
    """Mirror cmd_push's `should_split` decision WITHOUT side effects, for the
    Step-4 disk pre-flight. SIZE_MB/SIZE_GB → True iff the file is at/over the
    target (a file smaller than the target is pushed whole, never split); COUNT
    → always True (cmd_push splits on COUNT regardless of size); no/empty
    split_method → False (a standard whole-file push). Pure; never raises."""
    if not split_method:
        return False
    if split_method == "SIZE_MB":
        return file_size >= float(split_val) * (1024 ** 2)
    if split_method == "SIZE_GB":
        return file_size >= float(split_val) * (1024 ** 3)
    if split_method == "COUNT":
        return True
    return False


def _required_extra_bytes(file_size, will_split, eager):
    """Extra on-disk bytes a push/restore would CREATE beyond the original:
    0 if the file won't be split, 2X (chunks + eager merge temp) for an eager
    split, else 1X (chunks only) for a deferred split."""
    if not will_split:
        return 0
    return 2 * file_size if eager else file_size


def _disk_buffer(need):
    """Safety head-room on top of `need` bytes: the larger of 1% of the need or
    a 2 GB floor. Zero when nothing extra is required."""
    if need == 0:
        return 0
    return max(int(0.01 * need), 2 * 1024 ** 3)


def _disk_shortfall(target_dir, file_size, will_split, eager):
    """Return (free, required, shortfall) bytes for messaging, where `required`
    already includes the buffer. `shortfall` is max(0, required - free).
    NEVER raises: if `target_dir` can't be stat'd (missing/invalid) free is
    reported as -1 (an impossible value) and the whole requirement is treated
    as short, so callers can both message AND fail the check."""
    need = _required_extra_bytes(file_size, will_split, eager)
    required = need + _disk_buffer(need)
    try:
        free = shutil.disk_usage(target_dir).free
    except Exception:
        return (-1, required, required)
    return (free, required, max(0, required - free))


def _free_space_ok(target_dir, file_size, will_split, eager):
    """True if `target_dir` has room for the bytes this op would create plus the
    buffer. A non-splitting op needs nothing extra → always True (the target dir
    is not even stat'd). NEVER raises (an unstattable dir → not ok)."""
    if _required_extra_bytes(file_size, will_split, eager) == 0:
        return True
    free, required, _short = _disk_shortfall(target_dir, file_size, will_split, eager)
    return free >= required


def _parts_base(local_folder, temp_dir, manual_id):
    """Directory that should hold the `_parts` chunk dir (and the eager merge
    temp). With no temp_dir → `local_folder`. With a temp_dir → a per-entry
    subdir `temp_dir/<filesystem-safe manual_id>` (NOT created here — the caller
    journals + mkdirs it). The `checksums/` sidecars and the RollbackJournal
    ALWAYS live in `local_folder` and are unaffected by this helper.

    Returns (base_dir, error): on success (path, None); on a bad temp_dir
    (missing / not a directory / not writable) (None, reason). It NEVER raises,
    so it composes with the never-raise disk helpers and lets callers hard-stop
    on `error` the same way other commands return a sentinel + message."""
    if not temp_dir:
        return (local_folder, None)
    if not os.path.isdir(temp_dir):
        return (None, f"temp dir does not exist or is not a directory: {temp_dir}")
    if not os.access(temp_dir, os.W_OK):
        return (None, f"temp dir is not writable: {temp_dir}")
    safe_id = re.sub(r"[^A-Za-z0-9._-]", "_", manual_id)
    return (os.path.join(temp_dir, safe_id), None)


def resolve_ffmpeg():
    if os.path.exists(FFMPEG_PATH):
        return FFMPEG_PATH
    found = shutil.which("ffmpeg")
    if found:
        return found
    return None


def make_video_dummy(output_path, extension):
    ffmpeg = resolve_ffmpeg()
    if not ffmpeg:
        print("❌ ffmpeg not found. Cannot generate video dummy. Install ffmpeg or check FFMPEG_PATH.")
        return False

    ext_lower = extension.lower()
    recipe = DUMMY_RECIPE_BY_EXT.get(ext_lower, DUMMY_RECIPE_BY_EXT[".mp4"])
    tmp_path = output_path + ".dummy_tmp" + extension

    print(f"   > 🎬 Generating dummy video: {os.path.basename(output_path)}")

    cmd = [
        ffmpeg,
        "-f", "lavfi", "-i", "color=c=black:s=128x72:r=24",
        "-f", "lavfi", "-i", recipe["audio_source"],
        "-c:v", "libx264", "-profile:v", "baseline", "-pix_fmt", "yuv420p",
        "-preset", "veryfast", "-b:v", "50k",
        "-c:a", recipe["audio_codec"], "-ac", "2", "-ar", "44100",
        *recipe["audio_extra"],
        "-t", recipe["duration"], "-shortest",
        "-loglevel", "error", "-nostdin", "-y", tmp_path,
    ]

    result = subprocess.run(cmd, capture_output=True, text=True)

    if result.returncode != 0 or not os.path.exists(tmp_path):
        tail = (result.stderr or "").strip().splitlines()[-5:]
        print("❌ ffmpeg failed to generate dummy video:")
        for line in tail:
            print(f"   {line}")
        return False

    os.replace(tmp_path, output_path)
    print(f"   ✅ Dummy video created: {os.path.basename(output_path)}")
    return True


# ==========================================
#   AUTO-ROLLBACK SPEC  (point-of-no-return + artifact map)
# ==========================================
# Authoritative implementation spec for the auto-rollback feature
# (feature/auto_rollback). This block is DESIGN-ONLY documentation — it adds NO
# behavior. Each Step-3 candidate implements its own rollback primitive against
# this contract; placement of that primitive is candidate-chosen (main.py is the
# default per DECISIONS.md N-4) so this spec is intentionally location-agnostic.
# Line refs below are verified against the current main.py (2026-05-31). See
# docs/feature-auto-rollback/PLAN.md + DECISIONS.md (D-1..D-9, O-1..O-3, N-1..N-6).
#
# --- INVARIANT (O-2) ---------------------------------------------------------
# The master/original video is the source of truth. As long as it exists on disk
# the operation is REVERSIBLE. The master is destroyed in exactly two places:
#   1. cmd_replace commit rename (os.rename(original -> .tobedeleted), line ~990)
#   2. cmd_restore split-path chunk delete (os.remove of merged chunks, ~1232-1234)
# These two — and only these two — are true points-of-no-return (PONR). Push is
# reversible/resumable (O-1): the master always survives a push failure.
#
# --- SNAPSHOT DATA SHAPE (D-6), captured per-id at command entry --------------
# A rollback snapshot records, for the target id (and its parent if any):
#   entry_existed        : bool   — was library[id] present at entry?
#   prior_status         : str|None — entry["status"] at entry (to revert)
#   prior_uploaded       : bool|None — entry["uploaded"] at entry (to revert)
#   parent_id            : str|None — resolved parent id (season_map), if any
#   parent_existed       : bool   — was library[parent] present at entry?
#   child_already_linked : bool   — was id already in parent["children"]?
#   split_info_existed   : bool   — did entry already carry "split_info"?
#   preexisting_paths    : set    — which of {uid, <short_id>.sha256, _parts/,
#                                   checksums/, restore/, target_path} existed at
#                                   entry. Rollback deletes only the set-DIFFERENCE
#                                   (created-this-run); pre-existing paths are NEVER
#                                   touched (resume-safe — see _parts/ resume @700).
# Rollback reverts the in-memory library dict to the snapshot, then save_library().
#
# --- INVERSE ACTION PER REVERSIBLE ARTIFACT (created-this-run only) -----------
#   library entry            -> del library[id]                 (only if not entry_existed)
#   entry["split_info"]      -> entry.pop("split_info", None)   (only if not split_info_existed)
#   entry["status"]/["uploaded"] -> restore prior_* values      (if entry_existed)
#   uid sidecar              -> os.remove(<folder>/uid)         (only if created-this-run)
#   <short_id>.sha256 sidecar-> os.remove                       (only if created-this-run)
#   _parts/ dir + contents   -> shutil.rmtree(parts_dir)        (only if created-this-run;
#                                NEVER if it pre-existed — that is the resume path @700-703)
#   checksums/ dir + contents-> shutil.rmtree(checksum_dir)     (only if created-this-run)
#   restore/ merged target   -> os.remove(target_path)          (reproducible from chunks;
#                                code already does this @1213-1217 on hash-mismatch)
#   parent child-link        -> parent["children"].remove(id);
#                               parent["total_episodes"] = len(children)  (if child added this run)
#   parent season_map (D-7)  -> del library[parent] ONLY IF (this run created the parent)
#                               AND (removing this child leaves 0 children); otherwise just
#                               unlink the child + recompute total_episodes.
# Windows file locks (Plex / Windows Search) can make os.remove/rmtree fail mid-
# rollback; the primitive must report PARTIAL rollback honestly, not claim success.
#
# --- PONR TOGGLE ------------------------------------------------------------
# The primitive exposes a "mark PONR crossed" toggle. Before it is set, a failure
# rolls back (above). After it is set, a failure is a STRUCTURED HARD-FAIL carrying
# (current state, why rollback is impossible, the EXISTING command that resumes/
# repairs it). No new command is invented — irreversible hard-fails name the
# existing `fetch_restore <id>` pipeline (N-2; the bytes live in the cloud).
#
# --- D-9 ---------------------------------------------------------------------
# The empty remote dir created by `adb shell mkdir` (push, @691) is left in place
# on rollback. NEVER `adb shell rmdir` it (harmless; a re-run reuses it; avoids a
# round-trip + a new failure surface mid-rollback).
#
# --- PER-COMMAND PONR SUMMARY (details in-line at each cmd_* below) -----------
#   cmd_prep    (@302) : fully reversible — NO PONR. Early-skips @311-318 create
#                        ZERO artifacts and MUST NOT roll back (return True).
#   cmd_push    (@654) : NO rollback PONR (O-1). Failure -> resume-message; leave the
#                        partial upload, entry stays local_ready/uploaded=False, print
#                        `push <id>`. Roll back this-run _parts/checksums/split_info
#                        ONLY if created-this-run AND failure is pre-any-upload.
#   cmd_replace (@943) : PONR = os.rename(original -> .tobedeleted) @990. Pre-PONR
#                        rollback = delete the dummy temp @957. At/after PONR =
#                        hard-fail naming `fetch_restore <id>`. C9 stale-sweep
#                        (@966-979) self-heals a torn crash on the next replace.
#   cmd_restore (@1167): split-path PONR = merged-chunk delete @1232-1234. Pre-PONR
#                        reuses C11 quarantine_restore_file (@1207) + the reproducible-
#                        output cleanup (@1213-1217). At/after PONR = hard-fail naming
#                        `fetch_restore <id>`. Standard path (@1248) is a single
#                        shutil.move — no torn window.
# ==========================================
#   ROLLBACK PRIMITIVE — Candidate C (on-disk operation journal)
# ==========================================
# Architecture C: each command opens a per-run JOURNAL file
# (`<folder>/.mediavault_txn.json`) and appends a record describing each intended
# mutation BEFORE performing it. On a reversible failure rollback() replays the
# recorded inverses LIFO. Crucially the journal is on DISK and fsync-flushed, so it
# survives a hard process kill — a later invocation can call recover_journal() to
# finish an interrupted rollback (the crash-survival edge the in-memory candidates
# cannot cover; see PLAN.md Judge-criterion 3). Crossing the PONR writes a
# `point_of_no_return` marker into the journal and thereafter a failure raises
# RollbackHardFail naming an existing command (N-2). On clean success the journal is
# deleted. Placement: main.py (N-4 default); no mvcommon change. See the
# AUTO-ROLLBACK SPEC block above for the authoritative contract.

TXN_JOURNAL_NAME = ".mediavault_txn.json"


class RollbackHardFail(Exception):
    """Raised when a command fails AT/AFTER its point-of-no-return. Carries the
    current state, why rollback is impossible, and the EXISTING command that
    resumes/repairs it (N-2 — never a new command)."""

    def __init__(self, state, reason, resume_cmd):
        self.state = state
        self.reason = reason
        self.resume_cmd = resume_cmd
        super().__init__(f"{state}: {reason} — resume with: {resume_cmd}")


class RollbackJournal:
    """A durable on-disk operation journal for ONE command operating on ONE id.

    Each record is one of a small fixed vocabulary of reversible operations, logged
    BEFORE the forward action so a crash mid-action still leaves an inverse on disk:

        journal = RollbackJournal(folder_path, manual_id)
        journal.record_create_file(uid_path)      # then write the file
        journal.record_create_dir(parts_dir)      # then makedirs
        journal.record_set_field(id, "split_info", prior=None, existed=False)  # then set it
        journal.record_create_entry(id)           # then library[id] = ...
        journal.record_link_child(parent, id, created_parent=True)  # then link
        ...
        journal.mark_point_of_no_return()         # writes a PONR marker; rollback now refused
        ...
        journal.rollback(library)                 # replay inverses LIFO, save, delete journal
        journal.commit()                          # clean success — delete the journal

    The library dict is passed into rollback()/recover so the in-memory revert and
    save_library() happen together (consistent with existing behavior). Reports
    partial rollback honestly when an inverse fails (e.g. a Windows file lock) and
    leaves the journal on disk so recovery can be retried.
    """

    def __init__(self, folder_path, manual_id):
        self.path = os.path.join(folder_path, TXN_JOURNAL_NAME)
        self.manual_id = manual_id
        self._records = []          # list of dict records (ordered = creation order)
        self.crossed_ponr = False
        # IMP-R7: handle a leftover journal from a CRASHED previous run BEFORE we
        # flush a fresh one over it. The user's reflex after a crash is to RE-RUN the
        # command, not to run `recover` — and the bare _flush() below would destroy
        # the crashed run's inverses, permanently orphaning its artifacts from
        # rollback. _handle_leftover is a no-op when there is no leftover (the common
        # case), so the normal path stays byte-for-byte identical.
        self._handle_leftover(folder_path)
        # Fresh journal per command run.
        self._flush()

    def _handle_leftover(self, folder_path):
        """IMP-R7 (option b): if a `.mediavault_txn.json` already exists here, the
        PREVIOUS run for this folder did not finish cleanly. Deal with it BEFORE the
        fresh _flush() overwrites it:

        - Pre-PONR leftover  → auto-run recover_journal() now (its pre-PONR replay is
          idempotent and restores the clean pre-command state), THEN continue. A
          crash→re-run therefore recovers the crashed run before starting the new one.
        - Post-PONR leftover → NOT auto-recoverable (that run committed irreversibly —
          O-2 territory). Preserve it under a timestamped name so its recovery info is
          not lost and the user can inspect / run `recover`, then continue.
        - No leftover (the overwhelmingly common case) → do nothing; the caller's
          _flush() then behaves exactly as before (fresh empty journal, no recovery).

        D-4 tension (the documented decision behind option b): this calls recovery code
        adjacent to a command invocation. It does NOT violate the
        recover-not-on-happy-path principle because a leftover journal means the
        previous run was NOT happy — so handling it is by definition OFF the happy path.
        recover_journal()'s own semantics are UNCHANGED; this only CALLS it earlier.
        Because every command does load_library() BEFORE opening its journal and
        save_library() at the end, recovery's library reverts only matter for the
        negligible save_library→commit window the journal design already treats as
        non-existent; in the dominant crash-before-save case nothing was persisted, so
        the host command's in-memory library matches the recovered on-disk state."""
        if not os.path.exists(self.path):
            return  # no leftover — byte-for-byte the old behavior
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            # An unreadable leftover may still be hand-recoverable — never destroy it.
            print(f"   > ⚠️ Found an unreadable leftover {TXN_JOURNAL_NAME}; preserving it before continuing.")
            self._preserve_leftover()
            return
        if data.get("crossed_ponr"):
            print(f"   > ⚠️ Found a leftover {TXN_JOURNAL_NAME} that crossed its "
                  "point-of-no-return; preserving it for inspection before continuing.")
            self._preserve_leftover()
            return
        # Pre-PONR: finish the interrupted rollback first (idempotent).
        print("   > 🔧 Found an interrupted run's journal — recovering it before continuing.")
        recover_journal(folder_path)
        # If recovery was only PARTIAL (e.g. a Windows file lock blocked an inverse),
        # recover_journal leaves the journal in place. Do NOT _flush() a fresh journal
        # over those surviving inverses — preserve them so they are not lost (the user
        # can retry `recover` later). On a full recovery the journal is already gone and
        # this is a no-op.
        if os.path.exists(self.path):
            print(f"   > ⚠️ Recovery was partial; preserving the remaining {TXN_JOURNAL_NAME} before continuing.")
            self._preserve_leftover()

    def _preserve_leftover(self):
        """Rename the leftover journal to a timestamped sibling
        (`.mediavault_txn.<ts>.json`) so the fresh _flush() cannot silently destroy it.
        Used for post-PONR, unreadable, and partial-recovery leftovers."""
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        root, ext = os.path.splitext(self.path)  # (<folder>/.mediavault_txn, .json)
        preserved = f"{root}.{ts}{ext}"
        n = 1
        while os.path.exists(preserved):
            preserved = f"{root}.{ts}-{n}{ext}"
            n += 1
        try:
            os.replace(self.path, preserved)
            print(f"   > 💾 Preserved leftover journal as {os.path.basename(preserved)}.")
        except Exception as e:
            print(f"   > ⚠️ Could not preserve leftover journal: {e}")

    def _flush(self):
        """Persist the journal to disk durably (write-temp + os.replace + fsync)."""
        data = {"manual_id": self.manual_id, "crossed_ponr": self.crossed_ponr,
                "records": self._records}
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, self.path)

    def _append(self, record):
        self._records.append(record)
        self._flush()

    # ---- record helpers: log the INTENT before the forward action ----
    def record_create_file(self, path):
        self._append({"op": "create_file", "path": path})

    def record_create_dir(self, path):
        self._append({"op": "create_dir", "path": path})

    def record_create_entry(self, manual_id):
        self._append({"op": "create_entry", "id": manual_id})

    def record_set_field(self, manual_id, field, existed, prior):
        self._append({"op": "set_field", "id": manual_id, "field": field,
                      "existed": existed, "prior": prior})

    def record_link_child(self, parent_id, child_id, created_parent):
        self._append({"op": "link_child", "parent": parent_id, "child": child_id,
                      "created_parent": created_parent})

    def record_create_reproducible(self, path):
        """A reproducible output (e.g. a merged restore target) — removable on
        rollback because it can be regenerated from inputs that still exist."""
        self._append({"op": "create_reproducible", "path": path})

    def mark_point_of_no_return(self):
        self.crossed_ponr = True
        self._flush()

    # ---- rollback / commit / recover ----
    def rollback(self, library):
        """Replay the journalled inverses LIFO, revert the in-memory library, save,
        and delete the journal. Returns True on a full rollback; False (journal kept)
        on a partial rollback so recover_journal() can retry."""
        if self.crossed_ponr:
            raise RuntimeError("rollback() called after point-of-no-return")
        ok = _replay_inverses(self._records, library)
        if ok:
            save_library(library)
            self._delete()
            print("✅ Rollback complete — back to exact pre-command state.")
            return True
        # Partial — keep the journal for recovery, but still persist the library
        # reverts that DID succeed.
        save_library(library)
        print("⚠️ PARTIAL ROLLBACK — some artifacts could not be removed (likely a "
              f"file lock). The journal {TXN_JOURNAL_NAME} was kept; rerun the command "
              "to retry recovery.")
        return False

    def commit(self):
        """Clean success — discard the journal (no rollback will be needed)."""
        self._delete()

    def _delete(self):
        for p in (self.path, self.path + ".tmp"):
            try:
                if os.path.exists(p):
                    os.remove(p)
            except Exception:
                pass


def _replay_inverses(records, library):
    """Apply the inverse of each journalled record LIFO. Returns True if every
    inverse succeeded. File-lock failures are collected and reported by the caller."""
    all_ok = True
    for rec in reversed(records):
        op = rec.get("op")
        try:
            if op in ("create_file", "create_reproducible"):
                p = rec["path"]
                if os.path.exists(p):
                    os.chmod(p, stat.S_IWRITE)
                    os.remove(p)
            elif op == "create_dir":
                p = rec["path"]
                if os.path.isdir(p):
                    shutil.rmtree(p)
            elif op == "create_entry":
                library.pop(rec["id"], None)
            elif op == "set_field":
                if rec["id"] in library:
                    if rec["existed"]:
                        library[rec["id"]][rec["field"]] = rec["prior"]
                    else:
                        library[rec["id"]].pop(rec["field"], None)
            elif op == "link_child":
                parent, child = rec["parent"], rec["child"]
                if parent in library:
                    kids = library[parent].get("children", [])
                    if child in kids:
                        kids.remove(child)
                    if rec["created_parent"] and not kids:
                        # D-7: this run created the parent and rollback empties it.
                        del library[parent]
                    else:
                        library[parent]["children"] = sorted(kids)
                        library[parent]["total_episodes"] = len(kids)
        except Exception as e:
            all_ok = False
            print(f"     ⚠️ Could not undo {op} ({rec}): {e}")
    return all_ok


def recover_journal(folder_path):
    """Crash-recovery entry point: if a `.mediavault_txn.json` survives from a
    killed run AND it never crossed its PONR, replay its inverses to finish the
    interrupted rollback. A journal that crossed the PONR is left in place (the
    command committed irreversibly — nothing to undo). Returns True if a recovery
    ran. Exposed for an explicit `recover` invocation; not called on the happy path
    so unrelated commands stay byte-identical (D-4)."""
    jpath = os.path.join(folder_path, TXN_JOURNAL_NAME)
    if not os.path.exists(jpath):
        return False
    try:
        with open(jpath, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return False
    if data.get("crossed_ponr"):
        print(f"   > ℹ️ Journal {TXN_JOURNAL_NAME} crossed its point-of-no-return — "
              "nothing to roll back; leaving it for inspection.")
        return False
    print(f"   > 🔧 Recovering interrupted rollback from {TXN_JOURNAL_NAME}...")
    library = load_library()
    ok = _replay_inverses(data.get("records", []), library)
    save_library(library)
    if ok:
        try:
            os.remove(jpath)
        except Exception:
            pass
        print("   > ✅ Recovery complete — pre-command state restored.")
    else:
        print("   > ⚠️ Recovery partial — journal kept for another retry.")
    return True


def cmd_recover(target=None, scan=False):
    # Wrapper around recover_journal: scan for stale journals or resolve one folder/id
    if scan:
        # Walk every category root (CATEGORY_ROOTS — single source of truth) for
        # stale journals; an oth- push/replace rollback leaves one under Sports.
        roots = [os.path.join(LOCAL_ROOT, d) for subs in CATEGORY_ROOTS.values() for d in subs]
        found = 0
        for root in roots:
            if not os.path.exists(root):
                continue
            for dirpath, dirs, files in os.walk(root):
                dirs[:] = [d for d in dirs if d not in [
                    SPLIT_DIR_NAME, CHECKSUM_DIR_NAME, RESTORE_DIR_NAME,
                    ".git", ".idea", "__pycache__", "Utils"
                ]]
                for fname in files:
                    if fname != TXN_JOURNAL_NAME:
                        continue
                    jpath = os.path.join(dirpath, fname)
                    try:
                        with open(jpath, "r", encoding="utf-8") as f:
                            data = json.load(f)
                    except Exception:
                        print(f"   ⚠️ Could not parse journal at {dirpath}")
                        continue
                    crossed = data.get("crossed_ponr", False)
                    count = len(data.get("records", []))
                    action = (
                        f'inspect manually (crossed point-of-no-return)'
                        if crossed else
                        f'recover "{dirpath}"'
                    )
                    print(f"   {dirpath}  crossed_ponr={crossed}  records={count}  → {action}")
                    found += 1
        print(f"   {found} journal(s) found.")
        return found
    else:
        if not target:
            print("❌ Usage: recover [id|folder]   (or: recover --scan)")
            return
        target = target.strip('"').strip("'")
        library = load_library()
        if target in library and library[target].get("folder_path"):
            folder = library[target]["folder_path"]
            print(f"> Resolved id '{target}' -> {folder}")
        else:
            folder = target
        if not os.path.isdir(folder):
            print(f"❌ No such media folder / unknown id: {target}")
            return
        result = recover_journal(folder)
        if result:
            print(f"✅ recover finished for {folder}")
        else:
            print(f"ℹ️ Nothing to recover at {folder} (no pre-PONR journal).")
        return result


# ==========================================
#             CORE COMMANDS
# ==========================================

def cmd_prep(manual_id, filepath, parent_id=None):
    filepath = filepath.strip('"').strip("'")
    if not os.path.exists(filepath): print(f"❌ File not found: {filepath}"); return False

    # [NEW] Load library EARLY to check if we should skip
    library = load_library()

    # [ROLLBACK SPEC] cmd_prep is FULLY REVERSIBLE — no PONR. A rollback removes
    # only this-run artifacts: the library entry (if not entry_existed), the uid +
    # <short_id>.sha256 sidecars (if created-this-run), and the parent child-link /
    # this-run-created parent season_map per D-7. See the module spec block above.
    if manual_id in library:
        entry = library[manual_id]
        if entry.get("type") == "multi_ep_alias":
            # Refuse to prep OVER an existing combined-episode alias — writing a leaf
            # entry here would clobber the alias and corrupt the alias chain.
            print(f"❌ {manual_id} is a combined-episode alias of {entry.get('alias_of')}; prep the primary instead.")
            return False
        if entry.get("uploaded") or entry.get("status") in ("onboarded", "archived", "restored_local"):
            # [ROLLBACK SPEC] Early-skip: returns True having created ZERO artifacts.
            # The wrapper MUST treat this as success and NEVER roll back. This is an
            # early-skip EXPANSION (strictly more conservative) — it does not touch
            # journal/rollback semantics (the journal never records uploaded/status).
            #
            # WHY skip ANY cloud-bearing state (uploaded truthy OR status in
            # onboarded/archived/restored_local): cmd_prep's wholesale rebuild below
            # writes status="local_ready"/uploaded=False. Re-prepping an entry that
            # already asserts a cloud copy would clobber that status back to
            # local_ready, stranding the cloud copy with nothing in the library
            # pointing at it — the dangling-entry bug class (e.g. battlestar
            # e11/e12/e13). cmd_prep_push_rep_season preps every episode before
            # checking `uploaded`, so it can hit this; refuse the clobber here.
            # A GENUINELY local entry (local_ready + falsy uploaded + a real file,
            # not yet pushed) does NOT match this guard and STILL preps normally.
            print(f"   ⏭️  Skipping Prep: {manual_id} (already pushed/archived — refusing to clobber cloud-bearing status to local_ready).")
            return True

    # Secondary Safety Net: Just in case the JSON is out of sync but the file is clearly a dummy
    if os.path.getsize(filepath) < DUMMY_MAX_BYTES:
        # [ROLLBACK SPEC] Early-skip: returns True having created ZERO artifacts.
        # The wrapper MUST treat this as success and NEVER roll back.
        print(f"   ⏭️  Skipping Prep: {manual_id} (Dummy file detected).")
        return True

    filename = os.path.basename(filepath);
    folder_path = os.path.dirname(filepath)

    print(f"--- PREPPING: {manual_id} ---")
    short_id = generate_short_id(manual_id)

    # [ROLLBACK C] cmd_prep has NO PONR. Open a durable journal; each intent is
    # logged to disk BEFORE the forward action, so even a hard kill leaves an
    # inverse on disk that recover_journal() can replay.
    journal = RollbackJournal(folder_path, manual_id)
    try:
        file_hash = calculate_file_hash(filepath)
        if not file_hash:
            journal.rollback(library)  # nothing logged yet — clean no-op + delete journal
            return False  # Stop if hashing failed

        tech_specs = get_tech_specs(filepath)

        # Create Sidecar Files
        uid_path = os.path.join(folder_path, "uid")
        sha_path = os.path.join(folder_path, f"{short_id}.sha256")
        uid_preexisted = os.path.exists(uid_path)
        sha_preexisted = os.path.exists(sha_path)
        try:
            if not uid_preexisted:
                journal.record_create_file(uid_path)
            with open(uid_path, 'w') as f:
                f.write(short_id)
            if not sha_preexisted:
                journal.record_create_file(sha_path)
            with open(sha_path, 'w') as f:
                f.write(f"{file_hash} *{filename}")
        except Exception as e:
            print(f"⚠️ Warning: Could not write sidecar files: {e}")

        # --- INTELLIGENT PARENT DETECTION & LINKING ---
        if not parent_id:
            # Standard TV S01E01 (now supporting .5 episodes)
            match = re.match(r"^(.*)[eE|xX](\d+(?:\.\d+)?)$", manual_id)
            if match:
                parent_id = match.group(1)
                print(f"   > 🔗 Auto-Link: Detected Parent '{parent_id}'")
            # [NEW] Anime Auto-Link (ani-name-01 -> ani-name) (now supporting .5 episodes)
            elif manual_id.startswith("ani-"):
                # Try to strip last numbers
                match_ani = re.match(r"^(ani-.*?)[\d\.]+$", manual_id)
                if match_ani:
                    parent_id = match_ani.group(1)
                    print(f"   > 🔗 Auto-Link (Anime): Detected Parent '{parent_id}'")

        if parent_id:
            # Create Parent Season Map if it doesn't exist
            created_parent = parent_id not in library
            if created_parent:
                print(f"   > 🗺️  Creating new Season Map for '{parent_id}'...")
                library[parent_id] = {
                    "type": "season_map",
                    "folder_path": folder_path,
                    "total_episodes": 0,
                    "children": []
                }

            # Add Child to Parent's list
            if manual_id not in library[parent_id]["children"]:
                # [ROLLBACK C] Journal the link (with whether this run made the parent)
                # so the inverse can apply D-7. Logged before the mutation.
                journal.record_link_child(parent_id, manual_id, created_parent)
                library[parent_id]["children"].append(manual_id)
                library[parent_id]["children"].sort()
                library[parent_id]["total_episodes"] = len(library[parent_id]["children"])
            elif created_parent:
                # Parent created but child already linked (unusual) — still record so
                # rollback removes the empty this-run parent.
                journal.record_link_child(parent_id, manual_id, created_parent)

        # [NEW] Generate Auto Search Term
        name_no_ext, ext = os.path.splitext(filename)
        default_search_term = f"{name_no_ext} [{short_id}]{ext}"

        # Create Entry
        entry_data = {
            "short_id": short_id,
            "filename": filename,
            "folder_path": folder_path,
            "status": "local_ready",
            "uploaded": False,
            "search_term": default_search_term,  # Store search term
            "hash": file_hash,
            "metadata": parse_metadata_from_id(manual_id),
            "tech_spec": tech_specs
        }

        if parent_id:
            entry_data["parent_id"] = parent_id

        if manual_id not in library:
            journal.record_create_entry(manual_id)
        library[manual_id] = entry_data
        save_library(library)
        # [ROLLBACK C] Clean success — discard the journal.
        journal.commit()
        print(f"✅ Library Entry Created & Linked (Search Key: {default_search_term}).\n")
        return True
    except Exception as e:
        # [ROLLBACK C] No PONR in prep — replay the journalled inverses.
        print(f"❌ Prep failed: {e}")
        journal.rollback(library)
        return False


def cmd_set_search(manual_id, search_term):
    # [NEW] Helper to manually update search terms for legacy files
    library = load_library()
    if manual_id not in library: print("❌ ID not found."); return

    print(f"--- UPDATING SEARCH TERM: {manual_id} ---")
    library[manual_id]["search_term"] = search_term
    save_library(library)
    print(f"✅ Updated search_term to: '{search_term}'\n")


def cmd_set_poster(manual_id, url):
    print(f"--- DOWNLOADING POSTER: {manual_id} ---")
    library = load_library()
    if manual_id not in library: print("❌ ID not found."); return
    entry = library[manual_id]

    # 1. Define Target Path
    folder_path = entry["folder_path"]
    poster_path = os.path.join(folder_path, "poster.jpg")

    print(f"   > URL: {url}")
    print(f"   > Target: {poster_path}")

    # 2. Download
    try:
        headers = {'User-Agent': 'Mozilla/5.0'}
        r = requests.get(url, headers=headers, stream=True)
        if r.status_code == 200:
            with open(poster_path, 'wb') as f:
                r.raw.decode_content = True
                shutil.copyfileobj(r.raw, f)
            print("✅ Poster downloaded successfully.")
        else:
            print(f"❌ Failed to download (Status: {r.status_code})")
    except Exception as e:
        print(f"❌ Error downloading poster: {e}")


def cmd_set_fanart(manual_id, url):
    print(f"--- DOWNLOADING FANART: {manual_id} ---")
    library = load_library()
    if manual_id not in library: print("❌ ID not found."); return
    entry = library[manual_id]

    # 1. Define Target Path
    folder_path = entry["folder_path"]
    fanart_path = os.path.join(folder_path, "fanart.jpg")

    print(f"   > URL: {url}")
    print(f"   > Target: {fanart_path}")

    # 2. Download
    try:
        headers = {'User-Agent': 'Mozilla/5.0'}
        r = requests.get(url, headers=headers, stream=True)
        if r.status_code == 200:
            with open(fanart_path, 'wb') as f:
                r.raw.decode_content = True
                shutil.copyfileobj(r.raw, f)
            print("✅ Fanart downloaded successfully.")
        else:
            print(f"❌ Failed to download (Status: {r.status_code})")
    except Exception as e:
        print(f"❌ Error downloading fanart: {e}")


def cmd_set_tmdb(manual_id, tmdb_id):
    # [IMP-E3/U3/D17] Manual override for the optional metadata.tmdb_id leaf field.
    # Pure zero-byte JSON edit (like set_search): no media touch, NO rehash.
    # Targets a LEAF entry — resolve a multi_ep_alias to its primary leaf so the
    # alias's 3-key shape is never mutated; a season_map is a virtual container, so
    # refuse it (tmdb_id lives on the leaf, not the container).
    print(f"--- SETTING TMDB ID: {manual_id} ---")
    library = load_library()
    if manual_id not in library: print("❌ ID not found."); return

    real_id, entry = _resolve_alias(library, manual_id)
    if entry.get("type") == "season_map":
        print("❌ set_tmdb targets a leaf entry, not a season_map container.")
        return

    # TMDB ids are integers; be lenient — store as int when all-digits, else as-is.
    value = int(tmdb_id) if str(tmdb_id).isdigit() else tmdb_id
    entry.setdefault("metadata", {})["tmdb_id"] = value
    save_library(library)
    target = f" (resolved to {real_id})" if real_id != manual_id else ""
    print(f"✅ Set metadata.tmdb_id = {value!r}{target}\n")


# ==========================================
#         TMDB LOCAL-FIRST ENRICH (IMP-E3 / U3 / D17, Phase 5 step 5.4)
# ==========================================
# `cmd_enrich_metadata` is a LOCAL-FIRST TMDB backfill: it reads the ids already
# in the library, asks TMDB (themoviedb.org) for the matching show/movie, and —
# only with --apply — writes `metadata.tmdb_id`, stamps the Plex/Emby/Jellyfin
# `{tmdb-<id>}` token on the SHOW/MOVIE folder (via cmd_rename_folder), and
# downloads poster.jpg / fanart.jpg (+ per-season posters) WITHOUT EVER fetching
# media bytes. It is SHOW-CENTRIC (user-confirmed design C): every season + every
# episode of one show resolves ONCE and the folder token is stamped ONCE.
#
# Locked behaviours (do not relax without the user):
#   * DRY-RUN by default; --apply is required to write anything.
#   * A local poster.jpg/fanart.jpg is NEVER overwritten (the user's art wins).
#   * Ambiguous matches are LISTED, never guessed/written.
#   * ZERO media fetches — only TMDB JSON + small JPGs + the JSON library edit.
#
# PRE-RESOLVED TMDB FACTS (baked in so an executor never has to browse):
#   * Image base URL: GET https://api.themoviedb.org/3/configuration ->
#     images.secure_base_url (currently https://image.tmdb.org/t/p/). Full image
#     URL = secure_base_url + <size> + file_path. Poster size w342 (card grid),
#     backdrop/fanart size w780. If /configuration fails we fall back to the
#     documented https://image.tmdb.org/t/p/ base.
#   * Movie search:  GET /3/search/movie?api_key=<KEY>&query=<title>&year=<year>
#   * TV search:     GET /3/search/tv?api_key=<KEY>&query=<title>&first_air_date_year=<year>
#   * TV season images: GET /3/tv/{series_id}/season/{season_number}/images
#     (poster_path is used; we read the season_number from each season id).
#   * A search result already carries poster_path / backdrop_path, so the show/
#     movie art needs no extra images call — only per-season art does.
#   * Auth is the v3 api_key query param (the mvconfig tmdb.api_key is a v3 key).
TMDB_API_ROOT = "https://api.themoviedb.org/3"
# Fallback image base when /configuration cannot be reached (the documented
# current value). The live secure_base_url from /configuration is preferred.
TMDB_IMAGE_BASE_FALLBACK = "https://image.tmdb.org/t/p/"
TMDB_POSTER_SIZE = "w342"    # card-grid poster size (PRE-RESOLVED above)
TMDB_BACKDROP_SIZE = "w780"  # fanart/backdrop size (PRE-RESOLVED above)
TMDB_STILL_SIZE = "w300"     # per-episode still size (16:9 landscape, PRE-RESOLVED)

# Idempotent on-disk cache of TMDB JSON responses, keyed by URL+params, so a
# re-run does not re-hit the API. Module-level (derived from mvcommon.MV_STATE_DIR)
# so a test can monkeypatch it to a temp dir and never touch the real home cache
# — same binding-hazard discipline as MVTOKENS_PATH.
TMDB_CACHE_DIR = os.path.join(mvcommon.MV_STATE_DIR, "cache", "metadata")


def _tmdb_normalize_title(s):
    """Lower-case, strip punctuation, collapse whitespace — for title matching.

    Used to decide a CONFIDENT vs AMBIGUOUS match: TMDB's result title is compared
    to the title we parsed from the id under this normalization so 'The Office' and
    'the office.' compare equal but distinct shows never collide by accident."""
    if not s:
        return ""
    s = re.sub(r"[^\w\s]", " ", str(s).lower())
    return re.sub(r"\s+", " ", s).strip()


def _tmdb_cache_key(url, params):
    """Stable filename for a cached GET (sha1 of url + sorted params)."""
    raw = url + "?" + "&".join(f"{k}={params[k]}" for k in sorted(params or {}))
    return hashlib.sha1(raw.encode("utf-8")).hexdigest() + ".json"


def _tmdb_get(url, params, api_key, _cache=True):
    """GET a TMDB endpoint and return parsed JSON (dict), or None on any failure.

    The api_key is injected as the ?api_key= query param (TMDB v3 auth). Responses
    are cached on disk under TMDB_CACHE_DIR keyed by url+params so re-runs are
    idempotent and don't re-hit the API. NEVER raises: a network error, a non-200,
    or a JSON-decode failure all return None so the caller skips that entry rather
    than crashing the whole backfill. The api_key is deliberately EXCLUDED from the
    cache key so the cache file never embeds the secret and stays stable if the key
    is rotated."""
    cache_path = os.path.join(TMDB_CACHE_DIR, _tmdb_cache_key(url, params))
    if _cache:
        try:
            with open(cache_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (OSError, json.JSONDecodeError):
            pass  # cache miss / unreadable -> fall through to a live fetch
    q = dict(params or {})
    q["api_key"] = api_key
    try:
        r = requests.get(url, params=q, headers={"User-Agent": "Mozilla/5.0"}, timeout=15)
    except Exception as e:
        print(f"   ⚠️  TMDB request failed ({url}): {e}")
        return None
    if r.status_code != 200:
        print(f"   ⚠️  TMDB returned status {r.status_code} for {url}")
        return None
    try:
        data = r.json()
    except Exception as e:
        print(f"   ⚠️  TMDB response was not valid JSON ({url}): {e}")
        return None
    if _cache:
        try:
            os.makedirs(TMDB_CACHE_DIR, exist_ok=True)
            with open(cache_path, "w", encoding="utf-8") as f:
                json.dump(data, f)
        except OSError:
            pass  # caching is best-effort; a write failure must not break enrich
    return data


# ===========================================================================
#   ONLINE METADATA — OMDb ratings/awards/box-office + the mvonline.json cache
#   (IMP-E16).
#
# The hover DOSSIER (GET /api/detail) shows the cross-aggregator ratings TMDB does
# NOT carry — IMDb / Rotten Tomatoes / Metacritic — plus Rated / Runtime / Awards /
# BoxOffice. The single source is OMDb (https://www.omdbapi.com), looked up by a
# title's IMDb id (`?i=tt…`, preferred) or by title+year (`?t=&y=`).
#
# COST MODEL — populated ONCE, read MANY times:
#   * `refresh_online` (cmd_refresh_online) walks the whole library, dedupes by
#     tmdb_id (each distinct title fetched once — episodes inherit their SHOW's
#     ratings), resolves each title's imdb_id via TMDB, calls omdb_fetch, and
#     stores the result in mvonline.json keyed by str(tmdb_id).
#   * tmdb_detail MERGES the cached entry into the dossier with NO live OMDb call,
#     so opening the hover preview stays fast and never blocks on the network.
#
# mvonline.json schema (atomic write: tempfile + os.replace — the token-store idiom):
#   {"<tmdb_id>": {"imdb_id": "tt…",
#                  "ratings": {"imdb": "8.8", "rotten_tomatoes": "87%",
#                              "metacritic": "74"},
#                  "rated": "PG-13", "runtime": "148 min",
#                  "awards": "Won 4 Oscars…", "boxoffice": "$292,587,330",
#                  "fetched_at": "<iso8601 UTC>"},
#    ...}
#
# BINDING HAZARD: ONLINE_CACHE_PATH / OMDB_CACHE_DIR are module-level so a test can
# monkeypatch them to a temp path and never touch the real repo-root mvonline.json
# or the real ~/.mediavault cache — same discipline as MVTOKENS_PATH / TMDB_CACHE_DIR.
# ===========================================================================

# Repo-root online-metadata cache (gitignored). Sits beside main.py so it is found
# regardless of CWD, mirroring mvcommon.MVTOKENS_PATH.
ONLINE_CACHE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "mvonline.json")

# Raw OMDb responses cached on disk (keyed by url+params) so a re-run is cheap —
# a sibling of TMDB_CACHE_DIR under the per-user state dir.
OMDB_CACHE_DIR = os.path.join(mvcommon.MV_STATE_DIR, "cache", "omdb")
OMDB_API_ROOT = "https://www.omdbapi.com/"

# How long a cached online-metadata entry is considered FRESH. refresh_online
# skips an entry fetched within this window unless --force is given (ratings/awards
# drift slowly; re-fetching daily would waste the OMDb quota).
ONLINE_FRESH_DAYS = 14

# OMDb's three rating Source names -> our stable, compact keys.
_OMDB_RATING_SOURCES = {
    "Internet Movie Database": "imdb",
    "Rotten Tomatoes": "rotten_tomatoes",
    "Metacritic": "metacritic",
}


def online_cache_load():
    """Load mvonline.json -> dict keyed by str(tmdb_id). {} when absent/malformed.

    Read fresh each call (the file is tiny) so a refresh_online write is visible to
    the very next tmdb_detail without a cache-clear dance — same no-in-memory-cache
    choice as the token store. A malformed file warns to stderr and degrades to {}
    so a corrupt cache never crashes the dossier or the refresh."""
    if not os.path.exists(ONLINE_CACHE_PATH):
        return {}
    try:
        with open(ONLINE_CACHE_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        print(
            f"⚠️  mvonline.json is malformed and will be ignored "
            f"(online ratings cache reset). Error: {e}",
            file=sys.stderr,
        )
        return {}
    return data if isinstance(data, dict) else {}


def online_cache_get(tmdb_id):
    """The cached online-metadata dict for a tmdb_id, or None when not cached.

    The key is always str(tmdb_id) so an int (TMDB's native type) and a stored
    string id resolve to the same entry. A non-dict stored value -> None."""
    if tmdb_id is None:
        return None
    entry = online_cache_load().get(str(tmdb_id))
    return entry if isinstance(entry, dict) else None


def online_cache_set(tmdb_id, data):
    """Upsert ``data`` under str(tmdb_id) and atomically persist mvonline.json.

    Atomic write (tempfile + os.replace, mirroring mvcommon._save_tokens) so a crash
    mid-write can never leave a half-written cache. Loads the current cache, merges
    the one key, and rewrites — refresh_online writes one title at a time so the
    cache is durable after every title (an interrupted run keeps everything fetched
    so far)."""
    cache = online_cache_load()
    cache[str(tmdb_id)] = data
    path = ONLINE_CACHE_PATH
    dir_name = os.path.dirname(path) or "."
    os.makedirs(dir_name, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=dir_name, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as tf:
            json.dump(cache, tf, indent=2)
        os.replace(tmp_path, path)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def _online_entry_is_fresh(entry, now=None):
    """True iff a cached online entry was fetched within ONLINE_FRESH_DAYS.

    A missing/unparseable fetched_at -> NOT fresh (re-fetch), so a legacy or
    hand-edited entry without a timestamp is refreshed rather than pinned stale.
    Uses mvcommon's iso parser + UTC clock so a naive timestamp never raises on a
    tz-mismatch."""
    if not isinstance(entry, dict):
        return False
    fetched = mvcommon._parse_iso(entry.get("fetched_at"))
    if fetched is None:
        return False
    now = now or mvcommon._now_utc()
    return (now - fetched) <= timedelta(days=ONLINE_FRESH_DAYS)


def _omdb_cache_key(params):
    """Stable filename for a cached OMDb GET (sha1 of sorted params, MINUS the
    apikey so the cache file never embeds the secret and survives a key rotation)."""
    safe = {k: v for k, v in (params or {}).items() if k != "apikey"}
    raw = "&".join(f"{k}={safe[k]}" for k in sorted(safe))
    return hashlib.sha1(raw.encode("utf-8")).hexdigest() + ".json"


def _omdb_get(params, api_key):
    """GET omdbapi.com with ``params`` (+ the apikey) -> parsed JSON dict, or None.

    NEVER raises: a network error, a non-200, a JSON-decode failure, or an OMDb
    ``{"Response":"False"}`` (e.g. "Movie not found!") all return None so the caller
    skips that title rather than crashing the whole refresh. Responses are cached on
    disk under OMDB_CACHE_DIR keyed by the params (apikey excluded) so a re-run is
    cheap. Mirrors _tmdb_get's cache + degrade idiom exactly."""
    cache_path = os.path.join(OMDB_CACHE_DIR, _omdb_cache_key(params))
    try:
        with open(cache_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        pass  # cache miss / unreadable -> live fetch
    q = dict(params or {})
    q["apikey"] = api_key
    try:
        r = requests.get(OMDB_API_ROOT, params=q, headers={"User-Agent": "Mozilla/5.0"}, timeout=15)
    except Exception as e:
        print(f"   ⚠️  OMDb request failed: {e}")
        return None
    if r.status_code != 200:
        print(f"   ⚠️  OMDb returned status {r.status_code}")
        return None
    try:
        data = r.json()
    except Exception as e:
        print(f"   ⚠️  OMDb response was not valid JSON: {e}")
        return None
    # OMDb signals "no such title" with Response:"False" (HTTP 200). Treat as a miss
    # — do NOT cache it, so adding the imdb_id later re-queries instead of pinning
    # the not-found.
    if isinstance(data, dict) and str(data.get("Response", "")).lower() == "false":
        return None
    if not isinstance(data, dict):
        return None
    try:
        os.makedirs(OMDB_CACHE_DIR, exist_ok=True)
        with open(cache_path, "w", encoding="utf-8") as f:
            json.dump(data, f)
    except OSError:
        pass  # caching is best-effort
    return data


def _omdb_parse_ratings(payload):
    """Map an OMDb payload's ``Ratings[]`` to {imdb, rotten_tomatoes, metacritic}.

    OMDb returns ``Ratings: [{"Source": "...", "Value": "..."}]`` with the three
    Source names in _OMDB_RATING_SOURCES. Values are NORMALIZED to the compact dossier
    form: IMDb '8.8/10' -> '8.8', Metacritic '74/100' -> '74', Rotten Tomatoes keeps
    its '87%' (the contract shape: {"imdb":"8.8","rotten_tomatoes":"87%",
    "metacritic":"74"}). Only the recognised sources are kept, and only with a
    non-empty/non-"N/A" Value. Returns a dict with 0–3 keys (omitting sources OMDb did
    not provide)."""
    out = {}
    for r in (payload or {}).get("Ratings") or []:
        if not isinstance(r, dict):
            continue
        key = _OMDB_RATING_SOURCES.get(r.get("Source"))
        if not key:
            continue
        val = _omdb_clean(r.get("Value"))
        if not val:
            continue
        # Strip the "/N" denominator for the score-style sources (IMDb x/10,
        # Metacritic x/100); RT is a "%" and is kept verbatim.
        if key in ("imdb", "metacritic") and "/" in val:
            val = val.split("/", 1)[0].strip()
        out[key] = val
    return out


def _omdb_clean(value):
    """Trim an OMDb scalar to a useful string, or "" for missing/"N/A".

    OMDb fills unknown fields with the literal string "N/A"; we drop those so the
    dossier omits a field rather than showing "N/A"."""
    if not isinstance(value, str):
        return ""
    v = value.strip()
    return "" if v.upper() == "N/A" else v


def omdb_fetch(imdb_id=None, title=None, year=None):
    """Fetch online metadata for one title from OMDb -> a dict, or None on failure.

    Looks up by ``imdb_id`` (``?i=tt…``, preferred — exact, no ambiguity) when given,
    else by ``title`` (+ optional ``year``) (``?t=&y=``). Returns:
        {"imdb_id": "tt…"|"",            # OMDb's imdbID echo (or "")
         "ratings": {"imdb": "8.8", "rotten_tomatoes": "87%", "metacritic": "74"},
         "rated": "PG-13", "runtime": "148 min",
         "awards": "Won 4 Oscars…", "boxoffice": "$292,587,330"}
    (any field OMDb did not provide is "" / the ratings sub-keys are omitted).

    NEVER raises and NEVER hits the network on a bad call: with neither imdb_id nor
    title it returns None immediately; any OMDb/parse failure (via _omdb_get) returns
    None. Does NOT add fetched_at — the caller (online_cache_set) stamps that so the
    timestamp reflects when it was STORED."""
    api_key = mvcommon.omdb_api_key()
    if not api_key:
        return None
    if imdb_id:
        params = {"i": imdb_id}
    elif title:
        params = {"t": title}
        if year:
            params["y"] = str(year)
    else:
        return None

    data = _omdb_get(params, api_key)
    if not isinstance(data, dict):
        return None
    return {
        "imdb_id": _omdb_clean(data.get("imdbID")),
        "ratings": _omdb_parse_ratings(data),
        "rated": _omdb_clean(data.get("Rated")),
        "runtime": _omdb_clean(data.get("Runtime")),
        "awards": _omdb_clean(data.get("Awards")),
        "boxoffice": _omdb_clean(data.get("BoxOffice")),
    }


def _tmdb_image_base(api_key):
    """Live images.secure_base_url from /configuration, or the documented
    fallback. Cached like any other GET, so it costs one call per process at most."""
    cfg = _tmdb_get(f"{TMDB_API_ROOT}/configuration", {}, api_key)
    if isinstance(cfg, dict):
        base = cfg.get("images", {}).get("secure_base_url")
        if isinstance(base, str) and base.startswith("http"):
            return base
    return TMDB_IMAGE_BASE_FALLBACK


def _download_to(url, dest_path):
    """Download `url` to `dest_path` (REUSES cmd_set_poster's requests idiom).

    Returns True on a 200 + written file, else False (NEVER raises). The
    LOCAL-ALWAYS-WINS check (skip if dest exists) is the CALLER's policy, not
    this helper's — this only fetches small JPGs. Uses r.content (whole small
    image) rather than streamed r.raw so it is trivially mockable in tests while
    hitting the SAME requests library cmd_set_poster uses."""
    try:
        r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=30)
    except Exception as e:
        print(f"   ⚠️  image download failed ({url}): {e}")
        return False
    if r.status_code != 200:
        print(f"   ⚠️  image download returned status {r.status_code} ({url})")
        return False
    try:
        os.makedirs(os.path.dirname(dest_path), exist_ok=True)
        with open(dest_path, "wb") as f:
            f.write(r.content)
        return True
    except OSError as e:
        print(f"   ⚠️  could not write image {dest_path}: {e}")
        return False


def _has_tmdb_token(name):
    """True if a folder leaf name already carries a `{tmdb-…}` token (idempotency
    guard — we stamp the token at most once per show/movie folder)."""
    return re.search(r"\{tmdb-[^}]+\}", name or "") is not None


_SEASON_ID_RE = re.compile(r"-s(\d+)$", re.IGNORECASE)


def _show_id_of(season_or_episode_id, entry, library):
    """Best-effort SHOW id for a series/anime id (strip the trailing season tag).

    A season_map id like 'tv-en-2009-bsg-s04' -> 'tv-en-2009-bsg'. An episode leaf
    resolves through its parent_id (the season_map) first. An id with no season
    suffix is its own show id. Used only to GROUP seasons of one show together."""
    sid = season_or_episode_id
    if entry.get("type") != "season_map":
        parent = entry.get("parent_id")
        if parent:
            sid = parent
    return _SEASON_ID_RE.sub("", sid)


def _season_number_of(season_id):
    """Integer season number parsed from a season_map id ('…-s04' -> 4), or None.
    Drives the per-season TMDB images call (/tv/{id}/season/{n}/images)."""
    m = _SEASON_ID_RE.search(season_id)
    return int(m.group(1)) if m else None


# A leaf id encodes its season+episode as a glued `-sNNeMM` marker (the TV/series
# canonical shape, e.g. `tv-en-2005-the-office-s01e01`). Anime leaves instead glue
# the episode onto the slug (`ani-en-2009-bsg19`) and carry the season on their
# parent_id, so the episode is recovered from the bare trailing digits.
_EPISODE_SE_RE = re.compile(r"-s(\d+)e(\d+)$", re.IGNORECASE)
_ANIME_EP_TAIL_RE = re.compile(r"(\d{1,4})$")


def _episode_se_of(leaf_id, entry):
    """(season_number, episode_number) for an EPISODE leaf, or None.

    Drives the per-episode stills call (/tv/{id}/season/{s}/episode/{e}/images).
    Two id shapes are understood (see ARCHITECTURE §6.2):
      * TV/series — the glued `-sNNeMM` marker on the id itself (primary path).
      * Anime — the season comes from the leaf's parent_id (`…-sNN`) and the
        episode from the bare trailing digits of the id (`ani-…-bsg19` -> 19).
    Returns None when neither shape yields BOTH numbers, so the caller warns-once
    and skips that one still (it falls back to the season poster at view time)."""
    if not leaf_id:
        return None
    m = _EPISODE_SE_RE.search(leaf_id)
    if m:
        return int(m.group(1)), int(m.group(2))
    # Anime fallback: season off the parent_id, episode off the trailing digits.
    parent = (entry or {}).get("parent_id")
    season = _season_number_of(parent) if parent else None
    if season is None:
        return None
    em = _ANIME_EP_TAIL_RE.search(leaf_id)
    if not em:
        return None
    return season, int(em.group(1))


def _pick_still_path(images):
    """file_path of the best still from a /episode/{e}/images payload, or None.

    Picks the highest vote_average still (the most-voted artwork), falling back to
    the FIRST still when no votes separate them. Guarded against a missing/empty
    `stills` list and non-dict members so a malformed payload yields None (skip)."""
    stills = (images or {}).get("stills") or []
    best = None
    best_vote = None
    for s in stills:
        if not isinstance(s, dict) or not s.get("file_path"):
            continue
        if best is None:  # first usable still = the fallback
            best = s
            best_vote = s.get("vote_average") or 0
            continue
        vote = s.get("vote_average") or 0
        if vote > best_vote:
            best, best_vote = s, vote
    return best.get("file_path") if best else None


def _episode_thumb_name(filename):
    """The Jellyfin/Kodi/Plex local episode-thumbnail name for an episode VIDEO
    file: `<basename>-thumb.jpg` (e.g. `Dark.S01E01.mkv` -> `Dark.S01E01-thumb.jpg`).
    Derived ONLY from the library entry's own stored `filename` — never client
    input. Returns None for a falsy filename."""
    if not filename:
        return None
    return os.path.splitext(filename)[0] + "-thumb.jpg"


def _season_episode_meta(season_details):
    """Map episode_number -> {"overview", "name"} from a SEASON DETAILS payload
    (GET /3/tv/{id}/season/{n}), or {} when the payload has no usable episodes.

    The season-details endpoint returns `episodes[]`, each carrying `episode_number`,
    `name` (the episode title, e.g. "Secrets") and `overview` (the episode synopsis).
    This is the ONE-call-per-season source enrich uses to backfill per-episode
    metadata.overview / metadata.episode_title.

    Defensive: a None/empty payload, a missing/empty `episodes` list, or a member
    with no integer episode_number all degrade to an empty/partial map so a failed
    season-details fetch never raises (the caller simply writes nothing for that
    season). Stores only the two fields enrich persists — overview + name."""
    out = {}
    for ep in (season_details or {}).get("episodes") or []:
        if not isinstance(ep, dict):
            continue
        num = ep.get("episode_number")
        if not isinstance(num, int):
            continue
        out[num] = {"overview": ep.get("overview") or "", "name": ep.get("name") or ""}
    return out


def _show_folder_of(season_folders):
    """The on-disk SHOW folder that the `{tmdb-…}` token is stamped onto, given the
    distinct season folders of one show.

    Layout assumption (Plex/Emby/Jellyfin standard, matched by the project's own
    fixtures): a series lives at `…/<Show>/Season NN/<episodes>`. So:
      * >=2 season folders -> the common ancestor IS the show folder (commonpath).
      * exactly 1 season folder whose basename looks like a season dir
        ('Season 04', 'S04', 'Season_4') -> climb to its PARENT (the show folder).
      * exactly 1 season folder that is NOT season-like (a flat show with episodes
        directly in `…/<Show>/`) -> that folder already IS the show folder.
    Returns an absolute path, or None if the input is empty/unusable."""
    folders = [os.path.abspath(f) for f in season_folders if f]
    if not folders:
        return None
    uniq = sorted(set(folders))
    if len(uniq) >= 2:
        try:
            return os.path.commonpath(uniq)
        except ValueError:
            return uniq[0]  # mixed drives (shouldn't happen) — degrade gracefully
    only = uniq[0]
    base = os.path.basename(only)
    if re.match(r"(?i)^season[\s_]*\d+$|^s\d+$", base):
        return os.path.dirname(only)
    return only


def _pick_tmdb_match(results, want_title, want_year, title_key, year_key):
    """Rank TMDB results by title-similarity + year + popularity, then decide
    CONFIDENT vs AMBIGUOUS. (Not exact-normalized-equality — that wrongly picked
    an obscure "Conjuring" over the real "The Conjuring".)

    Returns (status, payload):
      * ("confident", result_dict)  — a single clear best match.
      * ("ambiguous", candidates)   — list the top 3-5 {id, title, year} for the
                                       user to resolve with set_tmdb; write nothing.
      * ("none", [])                — TMDB returned nothing.

    Scoring per candidate (PROVEN against live TMDB):
      * norm(s)   = lowercase, alphanumerics ONLY (drops spaces/punct), so
                    'Battlestar Galactica' and a 'battlestargalactica' slug compare
                    as identical strings.
      * title_sim = difflib.SequenceMatcher(None, norm(result_title),
                    norm(want_title)).ratio()  — 1.0 is an exact (normalized) match.
      * pop       = popularity (fallback vote_count) — separates same-title remakes.
      * year_match= result year == want_year (a corroboration signal; for TV the
                    want_year is SOFT since the id year is often a later season).
    Rank: title_sim desc, then year_match desc, then pop desc.

    CONFIDENT iff the top candidate has title_sim >= 0.9 AND EITHER
      (a) it agrees on year (year_match), OR
      (b) it clearly beats #2 (title_sim lead >= 0.1) AND has meaningful
          popularity (pop >= 1.0) — so a lone obscure exact-title hit with ~0
          popularity does NOT auto-win.
    A near-tie at the top with no year to break it -> AMBIGUOUS (we never guess).
    """
    results = results or []
    if not results:
        return ("none", [])

    want_norm = re.sub(r"[^a-z0-9]", "", _tmdb_normalize_title(want_title))

    def _year_of(res):
        raw = res.get(year_key) or ""
        m = re.match(r"(\d{4})", str(raw))
        return int(m.group(1)) if m else None

    def _pop_of(res):
        pop = res.get("popularity")
        if isinstance(pop, (int, float)):
            return float(pop)
        vc = res.get("vote_count")
        return float(vc) if isinstance(vc, (int, float)) else 0.0

    def _sim_of(res):
        rnorm = re.sub(r"[^a-z0-9]", "", _tmdb_normalize_title(res.get(title_key)))
        return difflib.SequenceMatcher(None, rnorm, want_norm).ratio()

    def _cand(res):
        return {"id": res.get("id"), "title": res.get(title_key), "year": _year_of(res)}

    scored = []
    for r in results:
        sim = _sim_of(r)
        ym = (want_year is not None and _year_of(r) == want_year)
        scored.append((sim, ym, _pop_of(r), r))
    # Rank: title_sim desc, year_match desc (True>False), popularity desc.
    scored.sort(key=lambda t: (t[0], 1 if t[1] else 0, t[2]), reverse=True)

    top_sim, top_ym, top_pop, top_res = scored[0]
    second_sim = scored[1][0] if len(scored) > 1 else 0.0

    if top_sim >= 0.9:
        if top_ym:
            return ("confident", top_res)
        if (top_sim - second_sim) >= 0.1 and top_pop >= 1.0:
            return ("confident", top_res)
    # Not confident — list the strongest candidates (already similarity-ranked).
    return ("ambiguous", [_cand(t[3]) for t in scored[:5]])


def _enrich_title_year(any_id, entry):
    """(title, year) for an enrich unit, from metadata first then the id parse.

    Prefers an existing metadata.title/year (a human may have curated it) and
    falls back to parse_metadata_from_id. The id-derived title is the slug, which
    is a poor query, so when only the id is available we humanize the slug a bit
    (dashes -> spaces, drop the leading 'mov-/tv-/ani-' + lang + year tokens)."""
    meta = entry.get("metadata") or {}
    year = meta.get("year")
    title = meta.get("title")
    if not year:
        year = parse_metadata_from_id(any_id).get("year")
    # metadata.title is the RAW id in this codebase (parse_metadata_from_id sets
    # title=manual_id), and for a show the stored title is the *episode* id while
    # any_id is the season-stripped show id — so `title == any_id` misses it. Treat
    # any id-shaped title (mov-/tv-/ani- prefixed) as "not a real title" and humanize.
    if not title or title == any_id or str(title).startswith(("mov-", "tv-", "ani-")):
        title = _humanize_id_title(any_id)
    return title, year


def _title_is_id_shaped(title, entry_id):
    """True iff ``title`` is a placeholder/id-shaped title that enrich may safely
    REPLACE with a real TMDB title — i.e. NOT a human-curated one.

    parse_metadata_from_id seeds ``metadata.title`` to the RAW id, so the common
    placeholder cases are: missing/blank, the entry's own id, or any id-shaped
    string carrying a ``mov-/tv-/ani-`` category prefix (a leaf id stored on a
    season_map, etc.). Any OTHER non-empty string is treated as deliberately
    curated and is left untouched.

    (A title that already EQUALS the incoming TMDB title is handled by the caller
    as an idempotent no-op, so it is intentionally NOT classified here.)"""
    if not title:
        return True
    if entry_id is not None and title == entry_id:
        return True
    return str(title).startswith(("mov-", "tv-", "ani-"))


def _humanize_id_title(any_id):
    """Turn an id slug into a rough search title.

    'mov-en-2025-f1' -> 'f1'; 'tv-en-2009-bsg' -> 'bsg'. Strips a leading
    category token (mov/tv/ani), an optional 2-letter language, and a 4-digit
    year, then turns the remaining dashes into spaces. Conservative: if stripping
    leaves nothing, fall back to the raw dash-spaced id."""
    parts = any_id.split("-")
    if parts and parts[0] in ("mov", "tv", "ani"):
        parts = parts[1:]
    if parts and len(parts[0]) == 2 and parts[0].isalpha():
        parts = parts[1:]
    # Year position: strip an all-digit segment even if it's not exactly 4 digits
    # (real data has typo'd years like '20013' for 2013) so it never leaks into the
    # search query.
    if parts and parts[0].isdigit():
        parts = parts[1:]
    # Drop a trailing season/episode token (s06 / s06e01) so a show query is just the
    # title (e.g. 'tv-en-2022-peakyblinders-s06e01' -> 'peakyblinders').
    parts = [p for p in parts if not re.fullmatch(r"s\d{1,2}(e\d{1,3})?", p, re.IGNORECASE)]
    title = " ".join(parts).strip()
    return title or any_id.replace("-", " ")


def _gather_enrich_units(library, id_or_prefix=None, library_filter=None):
    """Group the library into SHOW-CENTRIC enrich units (the heart of design C).

    Returns a list of unit dicts, each:
      {"kind": "movie"|"show",
       "key":  <show_id or movie id>,        # stable display/group key
       "title": <best search title>,
       "year":  <int|None>,
       "ids":   [entry ids to receive tmdb_id],   # leaves (+ season_maps for shows)
       "seasons": {season_id: season_folder, ...}, # shows only (per-season art)
       "folder": <show/movie folder for the token stamp>}

    Iteration rules (whole-library — alias/season_map-safe per ENTRY_TYPE_KEYS):
      * SKIP `multi_ep_alias` entirely (virtual 3-key rows — no folder, no metadata
        of their own; their primary leaf carries the tmdb_id).
      * MOVIES (`mov-` leaf, no parent_id) -> one unit each.
      * SERIES/ANIME -> group every season_map + its episode leaves under the SHOW
        (id with the trailing -sNN stripped). One unit per show.

    `id_or_prefix` (optional) restricts to entries whose id == it or startswith it.
    `library_filter` (movies|series|anime) restricts by id prefix."""
    prefix_map = {"movies": "mov", "series": "tv", "anime": "ani"}
    want_prefix = prefix_map.get(library_filter) if library_filter else None

    def _in_scope(mid):
        if want_prefix and not mid.startswith(want_prefix):
            return False
        if id_or_prefix and not (mid == id_or_prefix or mid.startswith(id_or_prefix)):
            return False
        return True

    # --- shows: bucket every in-scope season_map (and its leaves) by show id ---
    shows = {}  # show_id -> {"ids": set, "seasons": {season_id: folder}, ...}
    movies = []

    for mid, entry in library.items():
        if entry.get("type") == "multi_ep_alias":
            continue  # virtual alias — never enriched directly (PR #21 crash class)
        etype = entry.get("type")
        cat = category_of_id(mid)

        if etype == "season_map":
            if not _in_scope(mid):
                continue
            show_id = _show_id_of(mid, entry, library)
            bucket = shows.setdefault(show_id, {"ids": set(), "seasons": {}})
            bucket["ids"].add(mid)
            bucket["seasons"][mid] = entry.get("folder_path")
            continue

        # leaf from here on (has folder_path/filename) — must be in scope.
        if not entry.get("folder_path"):
            continue
        if not _in_scope(mid):
            continue

        if cat == "movies" and not entry.get("parent_id"):
            movies.append((mid, entry))
        elif entry.get("parent_id"):
            # episode leaf of a show — attach to its show bucket.
            parent = entry["parent_id"]
            show_id = _SEASON_ID_RE.sub("", parent)
            bucket = shows.setdefault(show_id, {"ids": set(), "seasons": {}})
            bucket["ids"].add(mid)
            bucket["seasons"].setdefault(parent, entry.get("folder_path"))
        elif cat in ("series", "anime"):
            # a show-level leaf with no parent (rare) — treat as its own show.
            show_id = _SEASON_ID_RE.sub("", mid)
            bucket = shows.setdefault(show_id, {"ids": set(), "seasons": {}})
            bucket["ids"].add(mid)
            bucket["seasons"].setdefault(mid, entry.get("folder_path"))
        else:
            # uncategorized leaf — handle singly like a movie (best effort).
            movies.append((mid, entry))

    units = []

    for show_id, b in sorted(shows.items()):
        # Title/year from any representative entry (prefer a season_map's metadata).
        rep_id = next(iter(sorted(b["ids"])))
        rep_entry = library.get(rep_id, {})
        title, year = _enrich_title_year(show_id, rep_entry)
        folder = _show_folder_of(list(b["seasons"].values()))
        units.append({
            "kind": "show",
            "key": show_id,
            "title": title,
            "year": year,
            "ids": sorted(b["ids"]),
            "seasons": dict(b["seasons"]),
            "folder": folder,
        })

    for mid, entry in sorted(movies):
        title, year = _enrich_title_year(mid, entry)
        units.append({
            "kind": "movie",
            "key": mid,
            "title": title,
            "year": year,
            "ids": [mid],
            "seasons": {},
            "folder": entry.get("folder_path"),
        })

    return units


def _tmdb_query_variants(title):
    """Build a deduped list of search-query variants for a humanized title.

    A concatenated slug like 'battlestargalactica' returns 0 results from TMDB; the
    word-split 'battlestar galactica' returns the show. So we search BOTH:
      * the title as-is (already dash->space humanized, e.g. 'the thing'); and
      * a wordninja split of the space-removed form ('thething' -> 'the thing',
        'gameofthrones' -> 'game of thrones').
    wordninja mangles a few inputs ('peakyblinders' -> 'peak y blinders') and
    non-English ('baasha' stays 'baasha'); that is FINE — the raw variant is still
    searched, and a bad split just yields no extra match (the ranker then lists it
    AMBIGUOUS rather than mis-guessing). wordninja is OPTIONAL: if it is not
    installed we fall back to the raw variant only (graceful, no crash).

    Returns variants in priority order (raw first), case-insensitively deduped,
    empties dropped."""
    raw = (title or "").strip()
    variants = []
    seen = set()

    def _add(v):
        v = (v or "").strip()
        key = v.lower()
        if v and key not in seen:
            seen.add(key)
            variants.append(v)

    _add(raw)
    # wordninja on the SPACE-REMOVED form so concatenated slugs split well; a
    # multi-word raw title (already spaced) round-trips to itself and dedupes.
    joined = re.sub(r"\s+", "", raw)
    if joined:
        try:
            import wordninja
            _add(" ".join(wordninja.split(joined)))
        except Exception:
            pass  # wordninja absent or failed -> raw variant only (graceful)
    return variants


def _resolve_unit(unit, api_key):
    """Resolve ONE enrich unit against TMDB. Returns a dict:
      {"status": "confident"|"ambiguous"|"none"|"error",
       "tmdb_id": int|None, "poster_path": str|None, "backdrop_path": str|None,
       "title": str, "year": int|None,            # the matched values (confident)
       "candidates": [...]}                        # ambiguous list

    Searches EACH query variant (raw + wordninja-split) and UNIONs the results by
    tmdb id before ranking, so a show that only resolves under the split form is
    still found. TV/anime search is title-ONLY (no first_air_date_year — the id
    year is often a later season's air year and would wrongly filter the show out);
    the id year is kept only as a SOFT ranking signal. MOVIE search keeps &year=
    (release year is reliable).

    NEVER raises — any TMDB failure yields status "error" so the caller skips the
    unit and continues (additive-only; the library is never corrupted)."""
    title, year = unit["title"], unit["year"]
    if unit["kind"] == "movie":
        url = f"{TMDB_API_ROOT}/search/movie"
        base_params = {"year": year} if year else {}
        title_key, year_key = "title", "release_date"
    else:
        # TV/anime: NO first_air_date_year filter (id year may be a later season).
        url = f"{TMDB_API_ROOT}/search/tv"
        base_params = {}
        title_key, year_key = "name", "first_air_date"

    # Search every variant; union results by tmdb id (first occurrence wins).
    union = {}
    any_ok = False
    for q in _tmdb_query_variants(title):
        params = dict(base_params)
        params["query"] = q
        data = _tmdb_get(url, params, api_key)
        if data is None:
            continue  # this variant failed; try the others before giving up
        any_ok = True
        for r in (data.get("results") or []):
            rid = r.get("id")
            if rid is not None and rid not in union:
                union[rid] = r
    if not any_ok:
        # Every variant's request failed (network/non-200) -> a true error, skip.
        return {"status": "error", "candidates": []}

    results = list(union.values())
    status, payload = _pick_tmdb_match(results, title, year, title_key, year_key)
    if status == "confident":
        res = payload
        yr = None
        m = re.match(r"(\d{4})", str(res.get(year_key) or ""))
        if m:
            yr = int(m.group(1))
        return {
            "status": "confident",
            "tmdb_id": res.get("id"),
            "poster_path": res.get("poster_path"),
            "backdrop_path": res.get("backdrop_path"),
            "title": res.get(title_key),
            "year": yr,
            "overview": res.get("overview") or "",
            "vote_average": res.get("vote_average"),
            "candidates": [],
        }
    if status == "ambiguous":
        return {"status": "ambiguous", "candidates": payload}
    return {"status": "none", "candidates": []}


def _unit_preset_tmdb_id(unit, library):
    """The manually-set ``metadata.tmdb_id`` for an enrich unit, or None.

    A user runs `set_tmdb <id> <tmdb_id>` to paste a known id when the title
    search missed; that command writes ``metadata.tmdb_id`` onto a LEAF entry
    (it refuses a season_map container — see cmd_set_tmdb). So for a movie the
    preset lives on the movie leaf, and for a show on (one of) its episode
    leaves, never on the season_map. The unit dict does not carry the entries'
    metadata, so this looks the value up from the live library by the unit's
    ids — alias-resolving each so a multi_ep_alias reads its primary leaf.

    Returns the FIRST truthy tmdb_id found (preserving its stored type — int
    when set_tmdb saw all-digits, else the raw string), or None when no leaf of
    the unit carries one (the un-enriched case, which falls through to search)."""
    for eid in unit.get("ids", []):
        if library.get(eid) is None:
            continue
        try:
            _real_id, target = _resolve_alias(library, eid)
        except KeyError:
            continue
        if not isinstance(target, dict):
            continue
        preset = (target.get("metadata") or {}).get("tmdb_id")
        if preset:
            return preset
    return None


def _resolve_unit_by_id(unit, tmdb_id, api_key):
    """Resolve ONE enrich unit by a KNOWN tmdb_id — fetch the details directly,
    NO title search. Returns the SAME dict shape as _resolve_unit's confident
    branch (status/tmdb_id/poster_path/backdrop_path/title/year/overview/
    vote_average/candidates) so the confident-apply path treats it identically.

    The user explicitly chose this id (via set_tmdb), so we honour it: GET the
    movie or tv details (per unit["kind"]) and read the canonical fields off the
    details object —
      * movie  GET /3/movie/{id}: title, release_date, poster_path, backdrop_path
      * tv     GET /3/tv/{id}:     name,  first_air_date, poster_path, backdrop_path
    (the details object's poster_path/backdrop_path is enough, matching how
    _resolve_unit uses the search result's paths).

    On ANY fetch failure (network/404 -> _tmdb_get returns None) this returns
    status "error" so the caller SKIPS the unit and continues — it deliberately
    does NOT fall back to a title search (that would re-miss the very id the user
    pasted). NEVER raises."""
    if unit["kind"] == "movie":
        url = f"{TMDB_API_ROOT}/movie/{tmdb_id}"
        title_key, year_key = "title", "release_date"
    else:
        url = f"{TMDB_API_ROOT}/tv/{tmdb_id}"
        title_key, year_key = "name", "first_air_date"

    data = _tmdb_get(url, {}, api_key)
    if not isinstance(data, dict) or data.get("id") is None:
        # 404 / network / malformed payload -> skip (no search fallback).
        return {"status": "error", "candidates": []}

    yr = None
    m = re.match(r"(\d{4})", str(data.get(year_key) or ""))
    if m:
        yr = int(m.group(1))
    return {
        "status": "confident",
        "tmdb_id": data.get("id"),
        "poster_path": data.get("poster_path"),
        "backdrop_path": data.get("backdrop_path"),
        "title": data.get(title_key),
        "year": yr,
        "overview": data.get("overview") or "",
        "vote_average": data.get("vote_average"),
        "candidates": [],
    }


# EXA web-search fallback for enrich's TMDB resolution (IMP-E16/D5). When the TMDB
# title-search MISSES a concatenated/regional title (e.g. 'vaaranamaayiram'), ONE EXA
# search constrained to themoviedb.org returns the right detail page and the tmdb_id
# extracts cleanly from its URL. Cheap + rare: numResults=5, ONE POST, fired ONLY when
# the API search already failed — never on the happy path.
EXA_RESOLVE_NUM_RESULTS = 5
# Raw EXA-resolve responses cached on disk (keyed by the query, NOT the api key) so a
# re-run is idempotent + cheap — a sibling of TMDB_CACHE_DIR / OMDB_CACHE_DIR under the
# per-user state dir. The key never embeds the secret (mirrors _tmdb_get's cache).
EXA_CACHE_DIR = os.path.join(mvcommon.MV_STATE_DIR, "cache", "exa")
# A themoviedb.org detail URL carries the kind + id: …/movie/1003159 or …/tv/60574.
_TMDB_URL_RE = re.compile(r"themoviedb\.org/(movie|tv)/(\d+)")


def _exa_resolve_tmdb_id(title, year, kind):
    """Resolve a tmdb_id for a title the TMDB API title-search MISSED, via an EXA web
    search constrained to themoviedb.org. Returns the tmdb_id (int) or None.

    POSTs ONE EXA query ("<title> <year> site:themoviedb.org", numResults=5,
    includeDomains=[themoviedb.org]) and extracts (url_kind, tmdb_id) from each result
    URL via _TMDB_URL_RE. The unit kind picks the preferred URL kind — "movie" -> a
    /movie/ URL; a show/anime -> a /tv/ URL — and the FIRST same-kind hit wins. If no
    URL matches the wanted kind, the FIRST extracted id of the OTHER kind is accepted
    (best-effort). Returns None when there is no key/title, the request fails, or no
    URL yields an id.

    NEVER raises (mirrors exa_search_trivia: a missing key / network error / non-200 /
    bad payload all yield None) so the caller simply falls through to the existing
    ambiguous/none manual-review handling. The raw EXA response is cached on disk under
    EXA_CACHE_DIR keyed by the query so repeated runs are idempotent. The caller is
    responsible for the --no-web gate; this is also self-defensive (None without a key).

    NOTE: the returned id is a best-effort CANDIDATE — the caller MUST validate it via
    _resolve_unit_by_id (a real TMDB by-id details fetch) before writing anything, so an
    EXA mismatch is caught and never written as a guess (locked decision #6)."""
    api_key = mvcommon.exa_api_key()
    if not api_key or not title:
        return None
    year_str = f" {year}" if year else ""
    query = f"{title}{year_str} site:themoviedb.org"
    body = {
        "query": query,
        "numResults": EXA_RESOLVE_NUM_RESULTS,
        "includeDomains": ["themoviedb.org"],
    }

    # Disk cache (keyed by the query) — idempotent re-runs, the _tmdb_get idiom.
    cache_path = os.path.join(EXA_CACHE_DIR, _tmdb_cache_key(EXA_API_ROOT, {"query": query}))
    data = None
    try:
        with open(cache_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        pass  # cache miss / unreadable -> fall through to a live fetch

    if data is None:
        try:
            r = requests.post(
                EXA_API_ROOT,
                headers={"x-api-key": api_key, "Content-Type": "application/json"},
                json=body,
                timeout=30,
            )
        except Exception as e:
            print(f"   ⚠️  EXA resolve request failed: {e}")
            return None
        if r.status_code != 200:
            print(f"   ⚠️  EXA resolve returned status {r.status_code}")
            return None
        try:
            data = r.json()
        except Exception as e:
            print(f"   ⚠️  EXA resolve response was not valid JSON: {e}")
            return None
        try:
            os.makedirs(EXA_CACHE_DIR, exist_ok=True)
            with open(cache_path, "w", encoding="utf-8") as f:
                json.dump(data, f)
        except OSError:
            pass  # caching is best-effort; a write failure must not break enrich

    results = data.get("results") if isinstance(data, dict) else None
    if not isinstance(results, list):
        return None

    want_kind = "movie" if kind == "movie" else "tv"  # show/anime -> tv
    fallback_id = None
    for item in results:
        if not isinstance(item, dict):
            continue
        m = _TMDB_URL_RE.search(str(item.get("url") or ""))
        if not m:
            continue
        url_kind, tmdb_id = m.group(1), int(m.group(2))
        if url_kind == want_kind:
            return tmdb_id  # first same-kind hit wins (the strong signal)
        if fallback_id is None:
            fallback_id = tmdb_id  # remember the first other-kind id as a fallback
    return fallback_id


def _write_nfo(folder, kind, title, year, tmdb_id, overview="", vote_average=None):
    """Write a Kodi/Jellyfin-compatible NFO file into *folder*.

    For a movie (kind="movie") writes ``movie.nfo`` with a ``<movie>`` root.
    For a show (kind="show") writes ``tvshow.nfo`` with a ``<tvshow>`` root.
    Uses stdlib xml.etree.ElementTree so all text is properly XML-escaped.

    NEVER raises — any IO/permission failure is printed as a warning so the
    caller's enrich run is never blocked by an NFO write error.  Overwrites an
    existing file (NFOs are regenerable metadata).
    """
    import xml.etree.ElementTree as ET

    tag = "movie" if kind == "movie" else "tvshow"
    nfo_name = "movie.nfo" if kind == "movie" else "tvshow.nfo"

    root = ET.Element(tag)
    ET.SubElement(root, "title").text = title or ""
    ET.SubElement(root, "year").text = str(year) if year is not None else ""
    ET.SubElement(root, "plot").text = overview or ""
    if vote_average is not None:
        ET.SubElement(root, "rating").text = str(vote_average)
    uid_el = ET.SubElement(root, "uniqueid")
    uid_el.set("type", "tmdb")
    uid_el.set("default", "true")
    uid_el.text = str(tmdb_id)

    tree = ET.ElementTree(root)
    ET.indent(tree, space="  ")  # pretty-print (Python 3.9+)
    nfo_path = os.path.join(folder, nfo_name)
    try:
        with open(nfo_path, "w", encoding="utf-8") as fh:
            fh.write('<?xml version="1.0" encoding="UTF-8"?>\n')
            tree.write(fh, encoding="unicode", xml_declaration=False)
        print(f"     📄 wrote {nfo_name}")
    except Exception as exc:
        print(f"     ⚠️  NFO write failed ({nfo_path}): {exc} — enrich continues.")


def cmd_enrich_metadata(arg=None, *flags):
    """Local-first TMDB backfill (SHOW-CENTRIC, IMP-E3/U3/D17 — Phase 5 step 5.4).

    Usage: enrich_metadata [id_or_prefix] [--apply] [--library movies|series|anime]
            [--nfo] [--no-web]
    DRY-RUN by default (prints what WOULD happen, writes nothing). --apply performs
    it. --nfo (only honoured when combined with --apply) writes a Kodi/Jellyfin-
    compatible movie.nfo / tvshow.nfo alongside the poster on a confident match
    (IMP-U3 down-payment, step 5.8).

    --no-web disables the EXA web-search fallback (IMP-E16/D5). By default, when an
    EXA key is configured, a title the TMDB API title-search MISSES (none/ambiguous)
    is given ONE more chance: an EXA search constrained to themoviedb.org resolves a
    tmdb_id, which is then VALIDATED by a real by-id details fetch before anything is
    written (confident-only — never an unvalidated guess). --no-web keeps the pure
    TMDB-API behaviour. Any other flag is ignored.
    """
    # Fold a flag-shaped positional (e.g. a direct `cmd_enrich_metadata("--apply")`
    # with no id) into the flags list so --apply/--library are honoured no matter
    # which slot they arrive in. A non-flag positional is the id/prefix scope.
    flist = list(flags)
    if arg and str(arg).startswith("--"):
        flist = [arg] + flist
        id_or_prefix = None
    else:
        id_or_prefix = arg or None

    apply = "--apply" in flist
    write_nfo = "--nfo" in flist
    no_web = "--no-web" in flist
    library_filter = None
    if "--library" in flist:
        i = flist.index("--library")
        if i + 1 < len(flist):
            library_filter = flist[i + 1].lower()

    api_key = mvcommon.tmdb_api_key()
    if not api_key:
        print("❌ No TMDB API key configured. Set tmdb.api_key in mvconfig.json "
              "(see mvconfig.example.json). Nothing to do.")
        return

    # EXA web-search fallback (IMP-E16/D5): when the TMDB title-search misses a
    # concatenated/regional title, ONE EXA search constrained to themoviedb.org can
    # auto-resolve it (the found id is by-id VALIDATED before use). ON by default when
    # an EXA key is configured; --no-web disables it (pure TMDB-API behaviour).
    web_fallback = (not no_web) and bool(mvcommon.exa_api_key())

    library = load_library()
    units = _gather_enrich_units(library, id_or_prefix=id_or_prefix, library_filter=library_filter)

    mode = "APPLY" if apply else "DRY-RUN"
    print(f"=== ENRICH METADATA ({mode}) ===")
    if library_filter:
        print(f"   > library filter: {library_filter}")
    if id_or_prefix:
        print(f"   > scope: ids == or startswith '{id_or_prefix}'")
    if no_web:
        print("   > --no-web: EXA web-search fallback DISABLED (pure TMDB API).")
    elif web_fallback:
        print("   > web-search fallback ON (EXA) for titles the API search misses.")
    print(f"   > {len(units)} show/movie unit(s) to consider.\n")

    image_base = None  # resolved lazily on the first confident match (one call max)
    ambiguous = []     # collected and printed at the end
    n_matched = n_stamped = n_images = n_skipped = 0

    for unit in units:
        label = f"{unit['kind'].upper()} {unit['key']}"
        yr = f" ({unit['year']})" if unit["year"] else ""
        # ENRICH-BY-KNOWN-ID: if a leaf of this unit already carries a manually
        # set metadata.tmdb_id (via `set_tmdb`), honour it directly — fetch the
        # details BY THAT ID (no title search), so a user-pasted id gets the full
        # stamp + download treatment instead of being re-searched (and re-missed).
        preset_id = _unit_preset_tmdb_id(unit, library)
        try:
            if preset_id:
                print(f"   ↳ {label}: using preset tmdb_id={preset_id} (manual) — fetching by id, no search.")
                res = _resolve_unit_by_id(unit, preset_id, api_key)
            else:
                res = _resolve_unit(unit, api_key)
                # WATERFALL step (iii) — the TMDB title-search MISSED (none/ambiguous):
                # give the title ONE more chance via an EXA web search constrained to
                # themoviedb.org. A found id is VALIDATED by a real by-id details fetch
                # (confident result w/ real title/year/poster); ONLY a validated id is
                # used. EXA finding nothing — OR a failed by-id validation — falls
                # through to the EXISTING none/ambiguous manual handling, unchanged.
                # CONFIDENT-ONLY: an unvalidated guess is NEVER written.
                if web_fallback and res["status"] in ("none", "ambiguous"):
                    exa_id = _exa_resolve_tmdb_id(unit["title"], unit["year"], unit["kind"])
                    if exa_id is not None:
                        by_id = _resolve_unit_by_id(unit, exa_id, api_key)
                        if by_id.get("status") == "confident":
                            print(f"   ↳ {label}: resolved via web search: tmdb_id={exa_id}")
                            res = by_id
        except Exception as e:  # defensive — resolvers already swallow, but never crash the run
            print(f"⏭️  {label}: TMDB error, skipping ({e}).")
            n_skipped += 1
            continue

        if res["status"] == "error":
            print(f"⏭️  {label}: TMDB error, skipping (library untouched).")
            n_skipped += 1
            continue
        if res["status"] == "none":
            print(f"❓ {label}{yr}: NO TMDB match for '{unit['title']}' — listed for review.")
            ambiguous.append({"key": unit["key"], "title": unit["title"],
                              "year": unit["year"], "candidates": []})
            continue
        if res["status"] == "ambiguous":
            print(f"❓ {label}{yr}: AMBIGUOUS '{unit['title']}' — {len(res['candidates'])} candidates, NOT writing.")
            ambiguous.append({"key": unit["key"], "title": unit["title"],
                              "year": unit["year"], "candidates": res["candidates"]})
            continue

        # --- confident match ---
        tmdb_id = res["tmdb_id"]
        n_matched += 1
        n_imgs_unit = (1 if res.get("poster_path") else 0) + (1 if res.get("backdrop_path") else 0)
        # per-season posters + per-episode stills (shows only) each add one
        # potential image. An episode leaf is a unit id that carries a filename.
        season_imgs = len(unit.get("seasons", {})) if unit["kind"] == "show" else 0
        episode_stills = (
            sum(1 for i in unit.get("ids", []) if (library.get(i) or {}).get("filename"))
            if unit["kind"] == "show" else 0)
        print(f"✅ {label}{yr}: matched '{res['title']}'"
              f"{(' (' + str(res['year']) + ')') if res['year'] else ''} -> tmdb_id={tmdb_id}")

        folder = unit.get("folder")
        base_name = os.path.basename(os.path.normpath(folder)) if folder else ""
        will_stamp = bool(folder) and not _has_tmdb_token(base_name)
        if will_stamp:
            print(f"     {'would stamp' if not apply else 'stamping'} folder token: "
                  f"{base_name} -> {base_name} {{tmdb-{tmdb_id}}}")
        elif folder:
            print(f"     folder already has a {{tmdb-…}} token — skip stamp ({base_name}).")
        print(f"     {'would write' if not apply else 'writing'} metadata.tmdb_id on "
              f"{len(unit['ids'])} entr(y/ies).")
        print(f"     {'would download' if not apply else 'downloading'} up to "
              f"{n_imgs_unit + season_imgs + episode_stills} image(s) (poster/fanart"
              f"{'/season posters' if season_imgs else ''}"
              f"{'/episode stills' if episode_stills else ''}, local always wins).")

        if not apply:
            continue

        # ---- APPLY: write tmdb_id + real title/year (additive), stamp, download ----
        # 1) tmdb_id + real TITLE/YEAR (+ synopsis) on every leaf + season_map of the
        #    unit. The cards read metadata.title (title.js: a non-id title auto-shows),
        #    so the real TMDB title is filled here. ADDITIVE + IDEMPOTENT: title is
        #    replaced only when it is still id-shaped/blank (placeholder) — a
        #    human-curated title that differs from both the id AND the TMDB title is
        #    left intact. Re-running just rewrites the same TMDB title (no-op). year is
        #    filled when absent and refreshed when the TMDB year is known (the id year
        #    is often a season's air year, so the matched show year is preferred).
        #    overview = the TMDB synopsis (movie/show overview); for a SHOW this SEEDS
        #    every episode leaf with the show synopsis, which step 5 then refines into
        #    each episode's OWN synopsis (so an episode whose season-details fetch fails
        #    still degrades to the show synopsis rather than nothing). Only written when
        #    TMDB actually has one, so a no-overview match leaves metadata untouched.
        tmdb_title = res.get("title")
        tmdb_year = res.get("year")
        tmdb_overview = res.get("overview")
        live = load_library()
        for eid in unit["ids"]:
            ent = live.get(eid)
            if ent is None:
                continue
            real_id, target = _resolve_alias(live, eid)
            meta = target.setdefault("metadata", {})
            meta["tmdb_id"] = tmdb_id
            if tmdb_title:
                cur = meta.get("title")
                if _title_is_id_shaped(cur, real_id) or cur == tmdb_title:
                    meta["title"] = tmdb_title
            if tmdb_year is not None:
                meta["year"] = tmdb_year
            if tmdb_overview:
                meta["overview"] = tmdb_overview
        save_library(live)

        # 2) stamp the {tmdb-…} token ONCE on the show/movie folder (paths only —
        #    cmd_rename_folder is journaled + hash-safe; reused exactly as-is).
        if will_stamp:
            new_name = f"{base_name} {{tmdb-{tmdb_id}}}"
            ok = cmd_rename_folder(folder, new_name)
            if ok:
                n_stamped += 1
                # The folder moved; recompute season folders for the image step.
                new_folder = os.path.join(os.path.dirname(os.path.normpath(folder)), new_name)
                unit = _retarget_unit_folders(unit, folder, new_folder)
                folder = new_folder

        # 3) download images — LOCAL ALWAYS WINS (skip any that already exist).
        if image_base is None:
            image_base = _tmdb_image_base(api_key)
        n_images += _download_unit_images(unit, res, image_base, folder)

        # 3b) per-episode synopsis + title (SHOWS only) — ONE season-details call per
        #     season (cached) fills each episode leaf's metadata.overview (episode
        #     synopsis) + metadata.episode_title (the episode name). Refines the
        #     show-overview seeded in step 1; a failed season-details call degrades
        #     gracefully (episode keeps the show synopsis, no crash). Never fetches
        #     media. NOTE: this ADDS one cached GET /tv/{id}/season/{n} per season; the
        #     existing per-season-poster + per-episode-still image calls are unchanged
        #     (so the highest-vote still selection + the LOCAL-wins/error fall-backs all
        #     stay exactly as before).
        if unit["kind"] == "show":
            _apply_episode_overviews(unit, tmdb_id, api_key)

        # 4) NFO write — only when --nfo flag is set (IMP-U3 down-payment, step 5.8).
        if write_nfo and folder:
            _write_nfo(
                folder,
                kind=unit["kind"],
                title=res.get("title"),
                year=res.get("year"),
                tmdb_id=tmdb_id,
                overview=res.get("overview", ""),
                vote_average=res.get("vote_average"),
            )

    # --- ambiguous report (always printed; the user resolves via set_tmdb) ---
    if ambiguous:
        print(f"\n--- {len(ambiguous)} unit(s) NEED MANUAL CONFIRMATION (not written) ---")
        for a in ambiguous:
            yr = f" ({a['year']})" if a["year"] else ""
            print(f"  • {a['key']}{yr}  query='{a['title']}'")
            for c in a["candidates"]:
                cyr = f" ({c['year']})" if c.get("year") else ""
                print(f"       candidate: tmdb_id={c['id']}  \"{c['title']}\"{cyr}")
            print(f"       -> resolve with: python main.py set_tmdb {a['key']} <tmdb_id>")

    print(f"\n=== {'APPLIED' if apply else 'DRY-RUN'} === "
          f"matched={n_matched} stamped={n_stamped} images={n_images} "
          f"ambiguous={len(ambiguous)} skipped={n_skipped}")
    if not apply:
        print("   (dry-run: nothing was written — re-run with --apply to perform it.)")


# ===========================================================================
#   cmd_refresh_online — "refresh online metadata for all in one go" (IMP-E16).
#
# Walks the library, dedupes to DISTINCT TITLES (by tmdb_id), resolves each title's
# imdb_id via TMDB, fetches OMDb (IMDb/RT/Metacritic ratings + Rated/Runtime/Awards/
# BoxOffice), and writes the result into the gitignored mvonline.json cache that
# tmdb_detail merges into the hover dossier. NEVER mutates library_*.json — this
# writes ONLY mvonline.json (+ the on-disk OMDb/TMDB response caches).
#
# DEDUPE-BY-TMDB_ID: episodes inherit their SHOW's ratings (OMDb has no per-episode
# RT/Metacritic), and a show has ONE tmdb_id stamped on every leaf + season_map. So
# _gather_enrich_units already collapses a show's seasons/episodes into ONE unit;
# we additionally key by the unit's stored tmdb_id so two units that somehow share
# a tmdb_id are still fetched once. Movies are one unit each. Result: one OMDb call
# per distinct title, never one per episode.
#
# FRESHNESS: an entry fetched within ONLINE_FRESH_DAYS (~14d) is skipped (ratings
# drift slowly) unless --force re-fetches it.
# ===========================================================================

def _resolve_imdb_id(tmdb_id, kind, api_key):
    """The IMDb id ('tt…') for a TMDB id, via the SAME endpoints tmdb_detail uses.

    * movie (kind 'movie')  -> GET /3/movie/{id}, read ``imdb_id``.
    * tv/show (anything else)-> GET /3/tv/{id}/external_ids, read ``imdb_id``.
    Both calls funnel through the cached, None-on-failure _tmdb_get, so a network/
    non-200/bad-JSON failure just yields None (the caller reports no-imdb and skips).
    Returns a non-empty 'tt…' string or None."""
    if kind == "movie":
        data = _tmdb_get(f"{TMDB_API_ROOT}/movie/{tmdb_id}", {}, api_key)
    else:
        data = _tmdb_get(f"{TMDB_API_ROOT}/tv/{tmdb_id}/external_ids", {}, api_key)
    if isinstance(data, dict):
        imdb_id = data.get("imdb_id")
        if isinstance(imdb_id, str) and imdb_id.strip():
            return imdb_id.strip()
    return None


def cmd_refresh_online(arg=None, *flags):
    """Refresh online metadata (OMDb ratings/awards/box-office) for ALL titles.

    Usage: refresh_online [id_or_prefix] [--force] [--library movies|series|anime]

    Fetches IMDb/Rotten Tomatoes/Metacritic ratings + Rated/Runtime/Awards/BoxOffice
    for every DISTINCT title (deduped by tmdb_id; episodes inherit the show's
    ratings), caches them in mvonline.json, and the hover dossier reads that cache.
    Skips a title cached within ~14 days unless --force. NEVER writes library_*.json.

    Optional positional `id_or_prefix` restricts to ids ==/startswith it; --library
    restricts by category. Any other flag is ignored."""
    # Fold a flag-shaped positional (e.g. refresh_online("--force")) into the flags.
    flist = list(flags)
    if arg and str(arg).startswith("--"):
        flist = [arg] + flist
        id_or_prefix = None
    else:
        id_or_prefix = arg or None

    force = "--force" in flist
    library_filter = None
    if "--library" in flist:
        i = flist.index("--library")
        if i + 1 < len(flist):
            library_filter = flist[i + 1].lower()

    if not mvcommon.omdb_api_key():
        print("❌ No OMDb API key configured. Set omdb.api_key in mvconfig.json "
              "(see mvconfig.example.json). Nothing to do.")
        return
    api_key = mvcommon.tmdb_api_key()
    if not api_key:
        print("❌ No TMDB API key configured (needed to resolve each title's IMDb "
              "id). Set tmdb.api_key in mvconfig.json. Nothing to do.")
        return

    library = load_library()
    units = _gather_enrich_units(library, id_or_prefix=id_or_prefix, library_filter=library_filter)

    # Dedupe to DISTINCT tmdb_ids (insertion-ordered). A unit with no stored tmdb_id
    # has not been enriched yet -> counted as no-tmdb and skipped (refresh reads the
    # tmdb_id that enrich stamps; it never searches by title here).
    by_tmdb = {}      # tmdb_id (str) -> {"unit": unit, "kind": "movie"|"tv"}
    no_tmdb = 0
    for unit in units:
        tmdb_id = _unit_preset_tmdb_id(unit, library)
        if not tmdb_id:
            no_tmdb += 1
            continue
        key = str(tmdb_id)
        if key not in by_tmdb:
            by_tmdb[key] = {
                "unit": unit,
                "kind": "movie" if unit["kind"] == "movie" else "tv",
            }

    print("=== REFRESH ONLINE METADATA (OMDb) ===")
    if library_filter:
        print(f"   > library filter: {library_filter}")
    if id_or_prefix:
        print(f"   > scope: ids == or startswith '{id_or_prefix}'")
    print(f"   > {len(by_tmdb)} distinct title(s) with a tmdb_id"
          f"{f', {no_tmdb} without a tmdb_id (run enrich_metadata first)' if no_tmdb else ''}.")
    if force:
        print("   > --force: re-fetching even fresh entries.")
    print()

    n_fetched = n_cached = n_no_imdb = n_failed = 0
    total = len(by_tmdb)

    for idx, (key, info) in enumerate(by_tmdb.items(), start=1):
        unit = info["unit"]
        label = unit.get("title") or unit["key"]
        prefix = f"[{idx}/{total}] {label} ({key})"

        # Freshness skip (unless --force).
        cached = online_cache_get(key)
        if not force and _online_entry_is_fresh(cached):
            n_cached += 1
            print(f"{prefix} -> cached (fresh), skipping.")
            continue

        # Resolve the imdb_id via TMDB, then fetch OMDb by id.
        imdb_id = _resolve_imdb_id(key, info["kind"], api_key)
        data = omdb_fetch(imdb_id=imdb_id) if imdb_id else None
        if data is None and not imdb_id:
            n_no_imdb += 1
            print(f"{prefix} -> no IMDb id from TMDB, skipping.")
            continue
        if data is None:
            n_failed += 1
            print(f"{prefix} -> OMDb fetch failed ({imdb_id}), skipping.")
            continue

        # Stamp imdb_id (prefer OMDb's echo, else the resolved one) + fetched_at, then
        # persist. This writes ONLY mvonline.json — the library is never touched.
        data["imdb_id"] = data.get("imdb_id") or imdb_id or ""
        data["fetched_at"] = mvcommon._now_utc().isoformat()
        online_cache_set(key, data)
        n_fetched += 1
        print(f"{prefix} -> {_fmt_ratings(data.get('ratings') or {})}")

    print(f"\n=== DONE === fetched={n_fetched} cached-skip={n_cached} "
          f"no-imdb={n_no_imdb} failed={n_failed} no-tmdb={no_tmdb}")
    if not no_tmdb and total == 0:
        print("   (nothing to refresh — the matched scope had no titles with a tmdb_id.)")


def _fmt_ratings(ratings):
    """Compact one-line ratings summary for the live progress print, e.g.
    'IMDb 8.8 · RT 87% · MC 74'. '(no ratings)' when OMDb returned none."""
    parts = []
    if ratings.get("imdb"):
        parts.append(f"IMDb {ratings['imdb']}")
    if ratings.get("rotten_tomatoes"):
        parts.append(f"RT {ratings['rotten_tomatoes']}")
    if ratings.get("metacritic"):
        parts.append(f"MC {ratings['metacritic']}")
    return " · ".join(parts) if parts else "(no ratings)"


# ===========================================================================
#   TRIVIA BACKFILL — EXA web-search + GROQ distillation + the mvextra.json cache
#   (IMP-E16/A5).
#
# The hover DOSSIER (GET /api/detail) can show a few short, genuinely-interesting
# behind-the-scenes facts per title, each tagged with its [source]. The pipeline:
#   1. EXA (exa_search_trivia) web-searches "<title> <year> movie trivia behind the
#      scenes facts" and returns up to 4 page snippets, each with its source URL.
#   2. GROQ (groq_distill_trivia) reads those source-tagged snippets and distills
#      2-4 SHORT, standalone facts as STRICT JSON, attributing each to the most
#      likely source host (IMDb / ScreenRant / Reddit / Wikipedia / …).
#   3. fetch_trivia (cmd_fetch_trivia) runs that per DISTINCT title and caches the
#      result in the gitignored mvextra.json keyed by str(tmdb_id).
#   4. tmdb_detail MERGES the cached facts into the dossier with NO live call, so
#      opening the hover preview stays fast and never blocks on the network.
#
# COST MODEL — populated ONCE, read MANY times. EXA + GROQ are cheap; this is a
# one-time cached backfill, so numResults is kept small (4) and max_tokens modest
# (~300) to stay well under budget for a whole-library pass.
#
# ACCURACY: these facts are FLAVOR — they need not be perfectly accurate, but they
# are kept plausible and always sourced (every fact carries a `source`).
#
# mvextra.json schema (atomic write: tempfile + os.replace — the mvonline.json idiom):
#   {"<tmdb_id>": {"trivia": [{"text": "...", "source": "IMDb"}, ...],
#                  "fetched_at": "<iso8601 UTC>"},
#    ...}
#
# BINDING HAZARD: EXTRA_CACHE_PATH is module-level so a test can monkeypatch it to a
# temp path and never touch the real repo-root mvextra.json — same discipline as
# ONLINE_CACHE_PATH / MVTOKENS_PATH (the sandbox fixture redirects it).
# ===========================================================================

# Repo-root trivia cache (gitignored). Sits beside main.py so it is found
# regardless of CWD, mirroring ONLINE_CACHE_PATH.
EXTRA_CACHE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "mvextra.json")

EXA_API_ROOT = "https://api.exa.ai/search"
GROQ_API_ROOT = "https://api.groq.com/openai/v1/chat/completions"
GROQ_MODEL = "llama-3.3-70b-versatile"
# GROQ sits behind Cloudflare, which 403s (error 1010) a default python-requests
# User-Agent. A browser-ish UA is REQUIRED for every GROQ call.
GROQ_USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) MediaVault/1.0"

# Cost knobs — kept small/modest because this is a one-time cached backfill.
EXA_NUM_RESULTS = 4
GROQ_MAX_TOKENS = 300
# Trivia essentially never drifts; a 30-day freshness window means a re-run skips
# already-fetched titles unless --force.
TRIVIA_FRESH_DAYS = 30
# Be polite to EXA/GROQ between fetched titles in the bulk loop (seconds).
TRIVIA_THROTTLE_SECONDS = 0.4

# Common trivia-source hosts -> clean display names. A known host (or any subdomain
# of one) maps to the name; an unknown host degrades to its bare domain.
_TRIVIA_SOURCE_NAMES = {
    "imdb.com": "IMDb",
    "screenrant.com": "ScreenRant",
    "reddit.com": "Reddit",
    "wikipedia.org": "Wikipedia",
}


def _trivia_host_to_source(url):
    """Map a result URL (or bare host) to a clean trivia source name.

    imdb.com -> 'IMDb', screenrant.com -> 'ScreenRant', reddit.com -> 'Reddit',
    en.wikipedia.org -> 'Wikipedia' (a subdomain of a known host matches too). An
    unknown host -> its bare domain (leading 'www.' stripped); an empty/unparseable
    value -> 'web'."""
    if not isinstance(url, str) or not url.strip():
        return "web"
    candidate = url.strip()
    if "://" not in candidate:
        candidate = "http://" + candidate.lstrip("/")
    try:
        host = urlparse(candidate).netloc.lower()
    except Exception:
        return "web"
    # Drop any userinfo@ / :port that slipped into netloc.
    host = host.split("@")[-1].split(":")[0]
    if host.startswith("www."):
        host = host[4:]
    if not host:
        return "web"
    for known, name in _TRIVIA_SOURCE_NAMES.items():
        if host == known or host.endswith("." + known):
            return name
    return host


def _normalize_source(value, fallback="web"):
    """Coerce a GROQ-returned source (a clean name, a bare host, or a full URL) to a
    clean display source. A value that looks like a host/URL (has a '.' or '/') is
    mapped through _trivia_host_to_source; an already-clean word (e.g. 'IMDb') is
    kept verbatim; blank/None -> ``fallback``."""
    if not isinstance(value, str) or not value.strip():
        return fallback
    v = value.strip()
    if "." in v or "/" in v:
        return _trivia_host_to_source(v)
    return v


def extra_cache_load():
    """Load mvextra.json -> dict keyed by str(tmdb_id). {} when absent/malformed.

    Read fresh each call (the file is small) so a fetch_trivia write is visible to
    the very next tmdb_detail — the same no-in-memory-cache choice as the online /
    token stores. A malformed file warns to stderr and degrades to {} so a corrupt
    cache never crashes the dossier or the backfill."""
    if not os.path.exists(EXTRA_CACHE_PATH):
        return {}
    try:
        with open(EXTRA_CACHE_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        print(
            f"⚠️  mvextra.json is malformed and will be ignored "
            f"(trivia cache reset). Error: {e}",
            file=sys.stderr,
        )
        return {}
    return data if isinstance(data, dict) else {}


def extra_cache_get(tmdb_id):
    """The cached trivia/extra dict for a tmdb_id, or None when not cached.

    The key is always str(tmdb_id) so an int (TMDB's native type) and a stored
    string id resolve to the same entry. A non-dict stored value -> None."""
    if tmdb_id is None:
        return None
    entry = extra_cache_load().get(str(tmdb_id))
    return entry if isinstance(entry, dict) else None


def extra_cache_set(tmdb_id, data):
    """Upsert ``data`` under str(tmdb_id) and atomically persist mvextra.json.

    Atomic write (tempfile + os.replace, mirroring online_cache_set / _save_tokens)
    so a crash mid-write can never leave a half-written cache. fetch_trivia writes
    one title at a time, so the cache is durable after every title (an interrupted
    run keeps everything fetched so far)."""
    cache = extra_cache_load()
    cache[str(tmdb_id)] = data
    path = EXTRA_CACHE_PATH
    dir_name = os.path.dirname(path) or "."
    os.makedirs(dir_name, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=dir_name, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as tf:
            json.dump(cache, tf, indent=2)
        os.replace(tmp_path, path)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def _trivia_entry_is_fresh(entry, now=None):
    """True iff a cached trivia entry was fetched within TRIVIA_FRESH_DAYS.

    A missing/unparseable fetched_at -> NOT fresh (re-fetch). Mirrors
    _online_entry_is_fresh (mvcommon's iso parser + UTC clock so a naive timestamp
    never raises on a tz-mismatch)."""
    if not isinstance(entry, dict):
        return False
    fetched = mvcommon._parse_iso(entry.get("fetched_at"))
    if fetched is None:
        return False
    now = now or mvcommon._now_utc()
    return (now - fetched) <= timedelta(days=TRIVIA_FRESH_DAYS)


def exa_search_trivia(title, year):
    """Web-search a title's trivia via EXA -> a list of {title, url, text}, or [].

    POSTs https://api.exa.ai/search with the title+year trivia query and asks for
    EXA_NUM_RESULTS results, each with up to 800 chars of page text. The result
    ``url`` host is the SOURCE the distiller attributes facts to (imdb-trivia /
    ScreenRant / Reddit / Wikipedia / …). NEVER raises: a missing key, a network
    error, a non-200, or a bad/empty payload all return [] so the caller simply
    reports 'no web results' and moves on."""
    api_key = mvcommon.exa_api_key()
    if not api_key or not title:
        return []
    year_str = f" {year}" if year else ""
    body = {
        "query": f"{title}{year_str} movie trivia behind the scenes facts",
        "numResults": EXA_NUM_RESULTS,
        "contents": {"text": {"maxCharacters": 800}},
    }
    try:
        r = requests.post(
            EXA_API_ROOT,
            headers={"x-api-key": api_key, "Content-Type": "application/json"},
            json=body,
            timeout=30,
        )
    except Exception as e:
        print(f"   ⚠️  EXA request failed: {e}")
        return []
    if r.status_code != 200:
        print(f"   ⚠️  EXA returned status {r.status_code}")
        return []
    try:
        data = r.json()
    except Exception as e:
        print(f"   ⚠️  EXA response was not valid JSON: {e}")
        return []
    results = data.get("results") if isinstance(data, dict) else None
    if not isinstance(results, list):
        return []
    out = []
    for item in results:
        if not isinstance(item, dict):
            continue
        out.append({
            "title": item.get("title") or "",
            "url": item.get("url") or "",
            "text": item.get("text") or "",
        })
    return out


def _groq_chat(messages, api_key, max_tokens=GROQ_MAX_TOKENS):
    """POST a chat-completion to GROQ -> the assistant message content string, or
    None on any failure. The single requests seam groq_distill_trivia funnels
    through (tests patch this to inject a canned reply).

    The Mozilla User-Agent is REQUIRED (see GROQ_USER_AGENT). NEVER raises — a
    network error / non-200 / bad JSON / a reply missing choices all return None."""
    try:
        r = requests.post(
            GROQ_API_ROOT,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                "User-Agent": GROQ_USER_AGENT,
            },
            json={"model": GROQ_MODEL, "messages": messages, "max_tokens": max_tokens},
            timeout=60,
        )
    except Exception as e:
        print(f"   ⚠️  GROQ request failed: {e}")
        return None
    if r.status_code != 200:
        print(f"   ⚠️  GROQ returned status {r.status_code}")
        return None
    try:
        data = r.json()
        content = data["choices"][0]["message"]["content"]
    except (ValueError, KeyError, IndexError, TypeError) as e:
        print(f"   ⚠️  GROQ response was not in the expected shape: {e}")
        return None
    return content if isinstance(content, str) else None


def _build_trivia_prompt(title, year, snippets):
    """Build the GROQ chat messages: a strict-JSON system instruction + a user
    message carrying each EXA snippet tagged with its source host, asking for 2-4
    short, sourced, standalone facts attributed to the most likely provided source."""
    year_str = f" ({year})" if year else ""
    lines = []
    for s in snippets:
        src = _trivia_host_to_source(s.get("url"))
        text = " ".join((s.get("text") or "").split())
        if text:
            lines.append(f"[source: {src}] {text[:800]}")
    context = "\n\n".join(lines)
    system = (
        "You are a film/TV trivia curator. From the provided web snippets, extract "
        "2 to 4 SHORT, genuinely interesting, standalone trivia facts about the given "
        "title. Each fact must be at most about 160 characters, understandable on its "
        "own, and attributed to the most likely source among the snippet sources (use "
        "a clean name like IMDb, ScreenRant, Reddit, or Wikipedia). Return STRICT JSON "
        'ONLY: a JSON array of objects [{"text": "...", "source": "IMDb"}]. No prose, '
        "no markdown, no code fences."
    )
    user = (
        f"Title: {title}{year_str}\n\n"
        f"Web snippets (each tagged with its source):\n{context}\n\n"
        "Return the JSON array now."
    )
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def groq_distill_trivia(title, year, snippets):
    """Distill EXA trivia snippets into 2-4 short, source-tagged facts via GROQ.

    Returns a list of {"text": "...", "source": "IMDb"} (capped at 4), or [] on any
    failure (no key, no snippets, a GROQ error, or an unparseable reply with no
    salvageable lines). NEVER raises. The reply is parsed defensively by
    _parse_trivia_facts: the first JSON array of {text, source} objects is used; if
    that fails, the reply is split into lines each tagged source='web' (the
    documented graceful fallback). Every returned fact carries a non-empty source."""
    if not snippets:
        return []
    api_key = mvcommon.groq_api_key()
    if not api_key:
        return []
    content = _groq_chat(_build_trivia_prompt(title, year, snippets), api_key)
    if not content:
        return []
    return _parse_trivia_facts(content)[:4]


def _parse_trivia_facts(content):
    """Parse a GROQ reply string into a list of {text, source} facts.

    Primary path: extract the FIRST JSON array (``[ ... ]``) in the reply and read
    {text, source} objects from it, normalizing each source to a clean name. If
    there is no array, it fails to parse, or it yields no usable fact, fall back to
    splitting the reply into lines (stripping bullet/number markers), each tagged
    source='web'. Always returns a list (possibly empty); each fact has a non-empty
    text (clamped to 240 chars) and a non-empty source."""
    facts = []
    m = re.search(r"\[.*\]", content, re.DOTALL)
    if m:
        try:
            arr = json.loads(m.group(0))
        except (json.JSONDecodeError, ValueError):
            arr = None
        if isinstance(arr, list):
            for item in arr:
                if not isinstance(item, dict):
                    continue
                text = item.get("text")
                if not isinstance(text, str) or not text.strip():
                    continue
                facts.append({
                    "text": _clamp_trivia(text),
                    "source": _normalize_source(item.get("source")),
                })
            if facts:
                return facts
    # Fallback: line-split, tag source='web'.
    for raw in content.splitlines():
        line = raw.strip().lstrip("-*•").strip()
        line = re.sub(r"^\d+[.)]\s*", "", line)  # drop "1. " / "2) " list markers
        if len(line) < 12:
            continue
        facts.append({"text": _clamp_trivia(line), "source": "web"})
    return facts


def _clamp_trivia(text, limit=240):
    """Trim a fact to a sane stored length (the prompt asks for ~160; this is a hard
    safety cap). Collapses inner whitespace; appends '…' only when truncated."""
    t = " ".join(text.split())
    return t if len(t) <= limit else t[: limit - 1].rstrip() + "…"


# COST NOTE: EXA (4 results) + GROQ (~300 tokens) per title is cheap; this is a
# one-time cached backfill (the freshness window means a re-run mostly skips), so a
# whole-library pass stays well under budget.
def cmd_fetch_trivia(arg=None, *flags):
    """Fetch + cache 2-4 short, sourced TRIVIA facts per title (EXA + GROQ).

    Usage: fetch_trivia [id_or_prefix] [--force] [--library movies|series|anime]

    For every DISTINCT title (deduped by tmdb_id; episodes inherit the show's
    trivia), web-searches behind-the-scenes facts via EXA, distills 2-4 short facts
    via GROQ each tagged with its [source], and caches them in mvextra.json keyed by
    tmdb_id. The hover dossier reads that cache (never a live call). Skips a title
    cached within ~30 days unless --force. NEVER writes library_*.json.

    Optional positional `id_or_prefix` restricts to ids ==/startswith it; --library
    restricts by category. Any other flag is ignored."""
    # Fold a flag-shaped positional (e.g. fetch_trivia("--force")) into the flags.
    flist = list(flags)
    if arg and str(arg).startswith("--"):
        flist = [arg] + flist
        id_or_prefix = None
    else:
        id_or_prefix = arg or None

    force = "--force" in flist
    library_filter = None
    if "--library" in flist:
        i = flist.index("--library")
        if i + 1 < len(flist):
            library_filter = flist[i + 1].lower()

    if not mvcommon.exa_api_key():
        print("❌ No EXA API key configured. Set exa.api_key in mvconfig.json "
              "(see mvconfig.example.json). Nothing to do.")
        return
    if not mvcommon.groq_api_key():
        print("❌ No GROQ API key configured. Set groq.api_key in mvconfig.json "
              "(see mvconfig.example.json). Nothing to do.")
        return

    library = load_library()
    units = _gather_enrich_units(library, id_or_prefix=id_or_prefix, library_filter=library_filter)

    # Dedupe to DISTINCT tmdb_ids (insertion-ordered) — the cache key + the dossier
    # merge key. A unit with no stored tmdb_id has not been enriched yet -> counted
    # as no-tmdb and skipped (we never search by title to GUESS an id here). One
    # fetch per distinct TITLE; episodes of a show share the show's tmdb_id, so they
    # collapse to a single fetch and inherit the show's trivia.
    by_tmdb = {}
    no_tmdb = 0
    for unit in units:
        tmdb_id = _unit_preset_tmdb_id(unit, library)
        if not tmdb_id:
            no_tmdb += 1
            continue
        key = str(tmdb_id)
        if key not in by_tmdb:
            by_tmdb[key] = unit

    print("=== FETCH TRIVIA (EXA + GROQ) ===")
    if library_filter:
        print(f"   > library filter: {library_filter}")
    if id_or_prefix:
        print(f"   > scope: ids == or startswith '{id_or_prefix}'")
    print(f"   > {len(by_tmdb)} distinct title(s) with a tmdb_id"
          f"{f', {no_tmdb} without a tmdb_id (run enrich_metadata first)' if no_tmdb else ''}.")
    if force:
        print("   > --force: re-fetching even fresh entries.")
    print()

    n_fetched = n_cached = n_no_results = n_failed = 0
    total = len(by_tmdb)

    for idx, (key, unit) in enumerate(by_tmdb.items(), start=1):
        label = unit.get("title") or unit["key"]
        prefix = f"[{idx}/{total}] {label} ({key})"

        # Freshness skip (unless --force).
        cached = extra_cache_get(key)
        if not force and _trivia_entry_is_fresh(cached):
            n_cached += 1
            print(f"{prefix} -> cached (fresh), skipping.")
            continue

        title = unit.get("title") or label
        year = unit.get("year")

        # EXA web-search -> GROQ distill. no-results = EXA found nothing to distill;
        # failed = EXA had material but GROQ produced no usable fact (API/parse miss).
        snippets = exa_search_trivia(title, year)
        if not snippets:
            n_no_results += 1
            print(f"{prefix} -> no web results, skipping.")
            continue

        facts = groq_distill_trivia(title, year, snippets)
        if not facts:
            n_failed += 1
            print(f"{prefix} -> distill produced no facts, skipping.")
            continue

        # Writes ONLY mvextra.json — the library is never touched.
        extra_cache_set(key, {"trivia": facts, "fetched_at": mvcommon._now_utc().isoformat()})
        n_fetched += 1
        sources = ",".join(dict.fromkeys(f["source"] for f in facts))
        print(f"{prefix} -> {len(facts)} facts [{sources}]")

        # Be polite to EXA/GROQ between fetched titles (no wait after the last one).
        if idx < total:
            time.sleep(TRIVIA_THROTTLE_SECONDS)

    print(f"\n=== DONE === fetched={n_fetched} cached-skip={n_cached} "
          f"no-results={n_no_results} failed={n_failed} no-tmdb={no_tmdb}")
    if not no_tmdb and total == 0:
        print("   (nothing to fetch — the matched scope had no titles with a tmdb_id.)")


def _retarget_unit_folders(unit, old_folder, new_folder):
    """After the show folder is renamed, rewrite the unit's folder + season folder
    paths to the new location so the image step writes into the moved folders.
    Pure (returns a new dict); mirrors _rewrite_folder_path's prefix swap."""
    new = dict(unit)
    new["folder"] = new_folder
    new_seasons = {}
    for sid, sfolder in (unit.get("seasons") or {}).items():
        if sfolder:
            new_seasons[sid] = _rewrite_folder_path(sfolder, os.path.abspath(old_folder), os.path.abspath(new_folder))
        else:
            new_seasons[sid] = sfolder
    new["seasons"] = new_seasons
    return new


def _download_unit_images(unit, res, image_base, folder):
    """Download poster.jpg/fanart.jpg into the show/movie folder, plus per-season
    posters for a show — LOCAL ALWAYS WINS (any existing file is left untouched).
    Returns the count of images actually written. NEVER raises."""
    written = 0
    if not folder:
        return 0
    # Show/movie poster + fanart (the search result carries the paths directly).
    poster_path = res.get("poster_path")
    if poster_path:
        dest = os.path.join(folder, "poster.jpg")
        if os.path.exists(dest):
            print(f"     ⏭️  local poster.jpg present — kept (not overwritten).")
        elif _download_to(f"{image_base}{TMDB_POSTER_SIZE}{poster_path}", dest):
            print(f"     ⬇️  poster.jpg")
            written += 1
    backdrop_path = res.get("backdrop_path")
    if backdrop_path:
        dest = os.path.join(folder, "fanart.jpg")
        if os.path.exists(dest):
            print(f"     ⏭️  local fanart.jpg present — kept (not overwritten).")
        elif _download_to(f"{image_base}{TMDB_BACKDROP_SIZE}{backdrop_path}", dest):
            print(f"     ⬇️  fanart.jpg")
            written += 1

    if unit["kind"] != "show":
        return written

    # Per-season posters: /tv/{series_id}/season/{n}/images -> posters[0].file_path.
    series_id = res.get("tmdb_id")
    api_key = mvcommon.tmdb_api_key()
    for season_id, sfolder in (unit.get("seasons") or {}).items():
        if not sfolder:
            continue
        n = _season_number_of(season_id)
        if n is None:
            continue
        dest = os.path.join(sfolder, "poster.jpg")
        if os.path.exists(dest):
            print(f"     ⏭️  local season poster present — kept ({os.path.basename(sfolder)}).")
            continue
        simg = _tmdb_get(f"{TMDB_API_ROOT}/tv/{series_id}/season/{n}/images", {}, api_key)
        posters = (simg or {}).get("posters") or []
        fp = posters[0].get("file_path") if posters else None
        if fp and _download_to(f"{image_base}{TMDB_POSTER_SIZE}{fp}", dest):
            print(f"     ⬇️  season {n} poster.jpg ({os.path.basename(sfolder)})")
            written += 1

    # Per-episode stills: /tv/{id}/season/{s}/episode/{e}/images -> best still,
    # written as `<episode_video_basename>-thumb.jpg` next to the episode file.
    # LOCAL ALWAYS WINS (skip if the -thumb.jpg already exists). A failed/empty
    # call for one episode warns-once and is skipped (it falls back to the season
    # poster at view time) — it never crashes nor blocks the rest. Reads the LIVE
    # library so each leaf's folder_path reflects any rename done above.
    live = load_library()
    for eid in unit.get("ids", []):
        ent = live.get(eid)
        if ent is None:
            continue
        try:
            real_id, leaf = _resolve_alias(live, eid)
        except KeyError:
            continue
        if not isinstance(leaf, dict):
            continue
        fname = leaf.get("filename")
        efolder = leaf.get("folder_path")
        if not fname or not efolder:
            continue  # season_map / alias-without-primary — not an episode file
        se = _episode_se_of(real_id, leaf)
        if se is None:
            continue  # id shape gave no season+episode — skip (season poster covers it)
        s_no, e_no = se
        thumb = _episode_thumb_name(fname)
        dest = os.path.join(efolder, thumb)
        if os.path.exists(dest):
            print(f"     ⏭️  local episode still present — kept ({thumb}).")
            continue
        eimg = _tmdb_get(
            f"{TMDB_API_ROOT}/tv/{series_id}/season/{s_no}/episode/{e_no}/images",
            {}, api_key)
        fp = _pick_still_path(eimg)
        if not fp:
            print(f"     ⚠️  no episode still for S{s_no:02d}E{e_no:02d} — falls back to season poster.")
            continue
        if _download_to(f"{image_base}{TMDB_STILL_SIZE}{fp}", dest):
            print(f"     ⬇️  {thumb}")
            written += 1
    return written


def _apply_episode_overviews(unit, series_id, api_key):
    """Backfill per-episode `metadata.overview` + `metadata.episode_title` for a
    SHOW unit, using ONE SEASON DETAILS call per season (GET /3/tv/{id}/season/{n},
    cached), and persist the result. Returns the number of episode leaves updated.

    The season-details endpoint returns `episodes[]` each with `overview` + `name`,
    so a single call per season covers every episode of that season (far fewer API
    calls than one /images call per episode). Episodes are keyed by the
    (season, episode) numbers `_episode_se_of` parses from each leaf id, exactly as
    the per-episode still loop keys its /episode/{e}/images call — so the same leaves
    are reached either way.

    ADDITIVE + IDEMPOTENT: writes overview (episode synopsis) and episode_title (the
    episode `name`, e.g. "Secrets") onto each episode leaf's metadata; a re-run just
    rewrites the same values. The SHOW-level overview is written separately by the
    caller (so the season_map / movie leaf carry the show synopsis); this refines the
    episode leaves with their own synopsis + title.

    GRACEFUL DEGRADATION: a failed/empty season-details call (network/404 -> None, or
    a payload with no `episodes`) yields an empty lookup for that season — those
    episodes are simply left without an episode overview (they keep whatever the
    caller seeded) and the run continues. Reads the LIVE library so each leaf reflects
    any rename done earlier in the apply. Alias-safe (`_resolve_alias`); season_maps
    and aliases that don't resolve to an episode leaf are skipped. NEVER raises."""
    if unit["kind"] != "show":
        return 0

    live = load_library()
    season_cache = {}  # season_number -> {episode_number: {"overview","name"}}
    updated = 0
    changed = False
    for eid in unit.get("ids", []):
        ent = live.get(eid)
        if ent is None:
            continue
        try:
            real_id, leaf = _resolve_alias(live, eid)
        except KeyError:
            continue
        if not isinstance(leaf, dict):
            continue
        if not leaf.get("filename"):
            continue  # season_map / alias-without-primary — not an episode leaf
        se = _episode_se_of(real_id, leaf)
        if se is None:
            continue  # id shape gave no season+episode — no per-episode lookup key
        s_no, e_no = se
        if s_no not in season_cache:
            details = _tmdb_get(f"{TMDB_API_ROOT}/tv/{series_id}/season/{s_no}", {}, api_key)
            season_cache[s_no] = _season_episode_meta(details)
        epmeta = season_cache[s_no].get(e_no)
        if not epmeta:
            continue  # season-details failed/empty, or this episode is absent from it
        meta = leaf.setdefault("metadata", {})
        if epmeta["overview"]:
            meta["overview"] = epmeta["overview"]
        if epmeta["name"]:
            meta["episode_title"] = epmeta["name"]
        updated += 1
        changed = True

    if changed:
        save_library(live)
    return updated


def cmd_set_uploaded(manual_id):
    # [NEW] Helper to force 'uploaded' status for multi-part pushes
    print(f"--- FORCING UPLOAD STATUS: {manual_id} ---")
    library = load_library()
    if manual_id not in library: print("❌ ID not found."); return

    entry = library[manual_id]
    print(f"   > Current Status: {entry.get('status')} | Uploaded: {entry.get('uploaded')}")

    entry["uploaded"] = True
    entry["status"] = "onboarded"
    save_library(library)
    print(f"✅ Status forced to: onboarded | Uploaded: True")
    print(f"   You can now run: python main.py replace {manual_id}")


def _norm_path(p):
    """Normalised, case-folded absolute path for prefix comparison on Windows.

    Used by rename_folder's descendant scan so `C:\\Media\\Series\\Dark` and a
    child `c:/media/series/dark/Season 01` compare equal regardless of separators
    or case. Returns "" for a falsy input so missing folder_paths never match.
    """
    if not p:
        return ""
    return os.path.normcase(os.path.normpath(os.path.abspath(p)))


def _is_under(child_norm, parent_norm):
    """True if child_norm == parent_norm OR is nested strictly under it.

    Both args must already be _norm_path()'d. The trailing-os.sep guard prevents a
    sibling like `…\\Dark2` from matching the prefix `…\\Dark` (a plain startswith
    would wrongly match it)."""
    if not child_norm or not parent_norm:
        return False
    if child_norm == parent_norm:
        return True
    return child_norm.startswith(parent_norm + os.sep)


def _collect_folder_descendants(library, old_folder):
    """Whole-library iterator (PR#21 crash class): return the list of (id, entry)
    whose `folder_path` IS `old_folder` or lives UNDER it.

    Alias/season_map-safe by the SAME rule every whole-library loop must follow:
      - `multi_ep_alias` entries have NO `folder_path` (their 3-key schema is
        {type, alias_of, parent_id}) — verify the key is absent and SKIP them.
      - `season_map` entries DO carry a `folder_path` (= their first episode's
        folder) — include them so the container pointer is rewritten too.
      - `leaf` entries carry folder_path — include them.
    Entries with no `folder_path` key (incl. aliases) are skipped. Read-only — does
    not mutate the library."""
    old_norm = _norm_path(old_folder)
    out = []
    for mid, entry in library.items():
        if entry.get("type") == "multi_ep_alias":
            # No folder_path on an alias by construction — nothing to rewrite.
            continue
        fp = entry.get("folder_path")
        if not fp:
            continue
        if _is_under(_norm_path(fp), old_norm):
            out.append((mid, entry))
    return out


def cmd_rename_folder(old_folder_or_id, new_folder_name_or_token):
    """Crash-safe cascading folder rename (IMP-D17).

    Rename an on-disk SHOW/season folder (e.g. stamp a `{tmdb-12345}` token onto it)
    and rewrite `folder_path` for EVERY library entry under that folder — all
    seasons/episodes leaves AND the show's season_map container — atomically.

    HASH-SAFE: this only moves a directory + rewrites JSON `folder_path` strings. It
    NEVER re-hashes, re-splits, or re-uploads (the entry `hash` is over file bytes,
    not the path — ARCHITECTURE §7.4). The on-disk `uid` / `<short_id>.sha256`
    sidecars live inside the folder and MOVE with it (no content change), so it works
    identically on ARCHIVED folders (the files are tiny dummies — a directory rename
    moves dummies like any file; no special-casing).

    CRASH-SAFETY — mirrors cmd_replace's journal pattern WITHOUT changing the
    rollback contract (CLAUDE.md auto-rollback change-gate). It ADDS a new journaled
    operation; it does NOT alter the journal format/durability, the PONR semantics,
    `recover_journal()`, or the `RollbackHardFail` contract.

      - The journal is opened in the PARENT directory of the target folder. The
        parent does NOT move during the rename, so every existing journal method
        (`_flush`/`mark_point_of_no_return`/`commit`/`rollback`) and a later
        `recover_journal(<parent>)` stay valid across the directory rename. (A
        journal placed INSIDE the renamed folder would be carried to the new path,
        leaving `commit()`'s delete — which targets the old path — unable to clean
        it up.) The journal format/durability/API are untouched; only WHICH folder
        holds this command's journal differs (a per-command choice cmd_replace and
        cmd_restore already make independently).
      - Each descendant's folder_path rewrite is recorded as a standard `set_field`
        record (existed=True, prior=<old folder_path>) BEFORE the rewrite — exactly
        the vocabulary cmd_prep uses for `split_info`. Its inverse restores the prior
        path. NO new record `op` is invented.
      - PONR = the on-disk `os.rename(old → new)`, with `mark_point_of_no_return()`
        fired immediately after it (same semantics as cmd_replace's commit rename).
        A failure BEFORE the rename rolls back (the set_field inverses restore the
        old paths; the folder never moved). A failure AT/AFTER the rename leaves the
        crossed journal on disk; `recover_journal` correctly declines to auto-undo a
        crossed journal, and a RE-RUN self-heals the torn window (folder already at
        new, library still at old) — mirroring cmd_replace's C9 stale-sweep.
    """
    old_folder_or_id = old_folder_or_id.strip('"').strip("'")
    new_folder_name_or_token = new_folder_name_or_token.strip('"').strip("'")

    library = load_library()

    # --- Resolve the target folder: accept an id (-> its folder_path) or a path. ---
    if old_folder_or_id in library and library[old_folder_or_id].get("folder_path"):
        old_folder = os.path.abspath(library[old_folder_or_id]["folder_path"])
        print(f"> Resolved id '{old_folder_or_id}' -> {old_folder}")
    else:
        old_folder = os.path.abspath(old_folder_or_id)

    # The new name is a LEAF name (e.g. "Dark {tmdb-70523}"), not a full path:
    # keep the same parent dir, swap the leaf. Reject a name carrying a separator
    # (that would move the folder elsewhere — out of scope and a footgun).
    if os.sep in new_folder_name_or_token or (os.altsep and os.altsep in new_folder_name_or_token):
        print(f"❌ new name must be a bare folder name, not a path: {new_folder_name_or_token!r}")
        return False

    parent_dir = os.path.dirname(old_folder)
    new_folder = os.path.join(parent_dir, new_folder_name_or_token)

    print(f"=== RENAME FOLDER ===")
    print(f"   > FROM: {old_folder}")
    print(f"   > TO:   {new_folder}")

    # --- Find descendants up-front (used by both the happy path and the self-heal). ---
    descendants = _collect_folder_descendants(library, old_folder)

    # --- Forward self-heal for a prior torn run (rename committed, save did not). ---
    # If the OLD folder is gone but the NEW folder exists AND some entry still points
    # under OLD, the on-disk rename already happened in a previous (interrupted) run;
    # finish the JSON rewrite. Mirrors cmd_replace's C9 stale-sweep (forward repair of
    # a crossed-PONR torn window — recover_journal deliberately won't auto-undo it).
    if (not os.path.isdir(old_folder)) and os.path.isdir(new_folder) and descendants:
        print("   > ⚠️ Detected an interrupted prior rename (folder already moved). "
              "Completing the library pointer rewrite...")
        for mid, entry in descendants:
            entry["folder_path"] = _rewrite_folder_path(entry["folder_path"], old_folder, new_folder)
        save_library(library)
        # Clean up a crossed journal left by the interrupted run, if present.
        _jpath = os.path.join(parent_dir, TXN_JOURNAL_NAME)
        if os.path.exists(_jpath):
            try:
                os.remove(_jpath)
            except Exception:
                pass
        print(f"✅ Recovered interrupted rename — {len(descendants)} folder_path(s) rewritten to the new folder.")
        return True

    # --- Guards (clear refusals; no journal opened yet). ---
    if not os.path.isdir(old_folder):
        print(f"❌ No such folder (or unknown id): {old_folder_or_id}")
        return False
    if os.path.exists(new_folder):
        print(f"❌ Target already exists, refusing to overwrite: {new_folder}")
        return False
    if not descendants:
        print(f"❌ No library entries reference {old_folder} — nothing to rename.")
        return False

    print(f"   > {len(descendants)} library entr(y/ies) will be re-pointed:")
    for mid, _ in descendants:
        print(f"       - {mid}")

    # --- Crash-safe sequence (journal in the STABLE parent dir). ---
    journal = RollbackJournal(parent_dir, old_folder_or_id)
    # Record every folder_path rewrite BEFORE acting (journal-before-act). The
    # inverse (restore prior folder_path) is what recover replays on a pre-PONR crash.
    for mid, entry in descendants:
        journal.record_set_field(mid, "folder_path", existed=True, prior=entry.get("folder_path"))

    try:
        # PONR: the on-disk directory rename. BEFORE this line a failure rolls back
        # (no folder moved). AT/AFTER it the journal is crossed and not auto-undone.
        os.rename(old_folder, new_folder)  # ROLLBACK SEAM: directory moved here (atomic on one volume / point-of-no-return)
        journal.mark_point_of_no_return()

        # Post-PONR: rewrite the in-memory pointers + persist atomically (fsync +
        # os.replace via save_library). On a crash here, a re-run self-heals (above).
        for mid, entry in descendants:
            entry["folder_path"] = _rewrite_folder_path(entry["folder_path"], old_folder, new_folder)
        save_library(library)
        journal.commit()
        print(f"✅ Renamed folder + rewrote {len(descendants)} folder_path(s). No rehash (paths only).")
        return True
    except Exception as e:
        if journal.crossed_ponr:
            # At/after PONR — the directory already moved. Do NOT auto-undo (mirrors
            # cmd_replace). The crossed journal is left on disk; a re-run self-heals.
            print(f"❌ IRREVERSIBLE: folder renamed but the library rewrite failed: {e}")
            print(f"   > The folder is now at: {new_folder}")
            print(f"   > Re-run to finish the pointer rewrite: rename_folder \"{new_folder}\" \"{new_folder_name_or_token}\"")
            raise RollbackHardFail(
                state=f"{old_folder_or_id}: folder moved to {new_folder}",
                reason=f"folder_path rewrite failed past the rename point-of-no-return: {e}",
                resume_cmd=f'rename_folder "{new_folder}" "{new_folder_name_or_token}"',
            )
        print(f"❌ rename_folder failed (pre-rename): {e}")
        journal.rollback(library)
        return False


def _rewrite_folder_path(folder_path, old_folder, new_folder):
    """Replace the old-folder prefix of a single folder_path with the new-folder
    prefix, PRESERVING the original casing of any nested suffix.

    `old_folder`/`new_folder` are absolute. The entry's stored `folder_path` may be
    EXACTLY old_folder (the show/season folder itself) or nested under it (e.g. a
    `Season 01` subfolder). Matching is case/separator-insensitive (via _norm_path)
    so it works on Windows, but the SUFFIX is taken from the original abspath string
    so a subfolder's real casing (`Season 01`, not `season 01`) is retained."""
    abs_fp = os.path.abspath(folder_path)
    if _norm_path(folder_path) == _norm_path(old_folder):
        return new_folder
    # Nested: os.path.relpath matches the common prefix case-insensitively on Windows
    # but returns the tail with its ORIGINAL casing (verified: `Season 01`, not
    # `season 01`), so the subfolder's real name is preserved when joined onto new.
    tail = os.path.relpath(abs_fp, old_folder)
    return os.path.normpath(os.path.join(new_folder, tail))


def cmd_prep_season(base_id, folder_path):
    print(f"=== BATCH PREP: {base_id} ===")
    folder_path = folder_path.strip('"').strip("'")
    if not os.path.exists(folder_path): print("❌ Folder not found."); return

    files = sorted([f for f in os.listdir(folder_path) if f.lower().endswith(VIDEO_EXTENSIONS)])
    if not files: print("❌ No video files found."); return

    # [UPDATED] Anime Detection Logic
    is_anime = base_id.startswith("ani-")
    # IMP-D18: sports/Others — position-numbered; tournament-edition = season,
    # each half = an episode (the filenames carry no SxxExx / absolute-episode marker).
    is_other = base_id.startswith("oth")
    count = 0

    for idx, filename in enumerate(files, start=1):
        ep_num = None
        is_sxxexx_combined = False  # Track SxxExxExx combined-episode TV files

        if is_other:
            # IMP-D18: sports/Others files carry no SxxExx / absolute-episode number;
            # number by sorted-filename position (1-based), each half = one episode.
            # Name halves/periods so they sort in play order (First<Second, 1<2, Q1..Q4).
            ep_num = f"{idx:02d}"
        else:
            # Strategy 1: Standard S01E01 (Works for TV and some Anime, handles .5)
            match = re.search(r"[sS]\d+[eE](\d+)", filename)
            matched_sxxexx = bool(match)  # Strategy-1 matched via the SxxExx sub-regex
            if not match: match = re.search(r"\d+[xX](\d+(?:\.\d+)?)", filename)

            if match:
                ep_num = match.group(1)
                # Combined-episode detector: ONLY for the SxxExx (TV) branch, never the
                # \d+xYY anime fallback. Fires on 2+ consecutive E-numbers (S04E19E20).
                if matched_sxxexx:
                    combined = re.search(r"[sS]\d+(?:[eE]\d+){2,}", filename)
                    if combined:
                        is_sxxexx_combined = True
                        combined_eps = re.findall(r"[eE](\d+)", combined.group(0))

            # Strategy 2: Anime Absolute Numbering (001, 01, 135, handles .5)
            # Look for numbers at start or surrounded by delimiters
            elif is_anime:
                # Matches: "01.mkv", "001.mkv", "[Grp] 01 [Hash]", " - 01 - ", "16.5"
                # Excludes years 19xx/20xx
                match_ani = re.search(r"(?:^|[ ._\-\[\]])(\d{1,4}(?:\.\d+)?)(?:[ ._\-\[\]]|$|\.)", filename)
                if match_ani:
                    num_str = match_ani.group(1)
                    # Basic year filter
                    if not (len(num_str) == 4 and (num_str.startswith("19") or num_str.startswith("20"))):
                        ep_num = num_str

        if ep_num:
            # Format ID
            full_id = f"{base_id}e{ep_num}" if not is_anime else f"{base_id}{ep_num}"
            full_path = os.path.join(folder_path, filename)

            prepped = cmd_prep(full_id, full_path, parent_id=base_id)
            count += 1

            # Combined-episode aliases (SxxExxExx TV only): the FIRST E-number is the
            # primary (already prepped as full_id); the rest become thin alias entries
            # pointing at the same file. Guard on prep success AND the primary actually
            # existing in the library (a dummy/skip returns True but creates no entry).
            if is_sxxexx_combined and prepped:
                library = load_library()
                if full_id in library:
                    for s in combined_eps[1:]:
                        alias_id = f"{base_id}e{s}"
                        if alias_id not in library:
                            library[alias_id] = {
                                "type": "multi_ep_alias",
                                "alias_of": full_id,
                                "parent_id": base_id,
                            }
                            library[base_id]["children"].append(alias_id)
                            library[base_id]["children"].sort()
                            library[base_id]["total_episodes"] = len(library[base_id]["children"])
                    save_library(library)
        else:
            print(f"⚠️ Skipping {filename} (No episode number detected)")

    print(f"=== Batch Complete: Processed {count} episodes. ===")


def cmd_check(manual_id):
    print(f"--- CHECKING: {manual_id} ---")
    library = load_library()
    if manual_id not in library: print("❌ ID not found."); return
    real_id, entry = _resolve_alias(library, manual_id)
    if real_id != manual_id:
        print(f"ℹ️  {manual_id} is part of the combined file registered as {real_id} — operating on that.")
        manual_id = real_id

    file_path = os.path.join(entry['folder_path'], entry['filename'])
    if not os.path.exists(file_path): print("❌ File missing!"); return

    # Dumb check for dummy file
    if os.path.getsize(file_path) < DUMMY_MAX_BYTES:
        print("⚠️ Dummy file detected (already archived). Skipping hash check.")
        return

    actual_hash = calculate_file_hash(file_path)
    if actual_hash == entry['hash']:
        print("✅ PASS: Verified.\n")
    else:
        print("❌ FAIL: Hash mismatch!\n")


MVMETA_SCHEMA_VERSION = 1


def write_remote_mvmeta(adb_base, remote_target_dir, manual_id, entry):
    """Write a disaster-recovery `.mvmeta.json` sidecar next to the chunks on the phone.

    Mirrors the library entry (split_info + key metadata) so the local library can
    be rebuilt from the remote if `library_*.json` is ever lost. The sidecar is
    redundancy only -- the chunks are the source of truth -- so this is best-effort:
    on any failure it prints a greppable WARNING and returns False WITHOUT raising.
    The caller MUST ignore the return value for its own success contract.

    Sidecar name is UID-tagged `<base> [<short_id>].mvmeta.json` (collision-proof,
    matching the chunk naming convention). Written for non-split single-file uploads
    too -- in that case `chunks` is a 1-element list referencing the renamed remote
    `<name> [<short_id>]<ext>` name.
    """
    try:
        short_id = entry.get("short_id", "")
        filename = entry.get("filename", "")
        base_filename, ext = os.path.splitext(filename)

        split_info = entry.get("split_info")
        if split_info and split_info.get("is_split"):
            is_split = True
            method = split_info.get("method")
            val = split_info.get("val")
            total_chunks = split_info.get("total_chunks")
            chunks = [
                {"filename": c.get("filename"), "hash": c.get("hash")}
                for c in split_info.get("chunks", [])
            ]
        else:
            # Non-split single-file upload: one synthetic chunk referencing the
            # renamed remote name (`<name> [<short_id>]<ext>`), with the entry hash.
            is_split = False
            method = None
            val = None
            total_chunks = 1
            remote_name = f"{base_filename} [{short_id}]{ext}"
            chunks = [{"filename": remote_name, "hash": entry.get("hash")}]

        mvmeta = {
            "version": MVMETA_SCHEMA_VERSION,
            "manual_id": manual_id,
            "short_id": short_id,
            "base_filename": base_filename,
            "original_hash": entry.get("hash"),
            "is_split": is_split,
            "method": method,
            "val": val,
            "total_chunks": total_chunks,
            "chunks": chunks,
            "folder_path": entry.get("folder_path"),
            "remote_target_dir": remote_target_dir,
            "tech_spec": entry.get("tech_spec"),
            "metadata": entry.get("metadata"),
        }

        sidecar_name = f"{base_filename} [{short_id}]{MVMETA_SUFFIX}"
        remote_full_path = f"{remote_target_dir}/{sidecar_name}"

        fd, tmp_path = tempfile.mkstemp(suffix=MVMETA_SUFFIX)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(mvmeta, f, indent=2)
            subprocess.run(adb_base + ["push", "-p", tmp_path, remote_full_path], check=True)
        finally:
            try:
                os.remove(tmp_path)
            except Exception:
                pass  # best-effort temp cleanup; matches existing style

        print(f"   > 📝 mvmeta sidecar written: {sidecar_name}")
        return True
    except Exception as e:
        print(f"⚠️ mvmeta sidecar write failed (chunks are safe): {e}")
        return False


def _verify_chunk_hash(adb_base, remote_path, safe_path, expected_sha256):
    """Run `adb shell sha256sum` on the remote file; raise CalledProcessError on mismatch.

    On command-not-found / file-not-found (non-zero exit from the sha256sum call
    itself), print a warning and return WITHOUT raising — the push is kept alive
    (OD-2a, warn-and-skip). Similarly, empty OR garbled device stdout (a well-formed
    64-hex first token is required) is treated as a warn-and-skip — the push is kept
    alive rather than crashing with an IndexError or ValueError. A genuine hash mismatch
    (well-formed 64-hex token that differs from expected_sha256) raises
    subprocess.CalledProcessError so the surrounding IMP-C2 retry(retry_on=(CalledProcessError,))
    wrapper re-runs the whole push->mv->verify closure, and after exhaustion cmd_push
    returns False with the existing failure contract.
    """
    try:
        result = subprocess.run(
            adb_base + ["shell", "sha256sum", f"'{safe_path}'"],
            check=True, capture_output=True, text=True,
        )
    except subprocess.CalledProcessError:
        print(f"  ⚠️  sha256sum unavailable on device — remote verification skipped for {os.path.basename(remote_path)}")
        return
    # Format: "<hash>  <path>\n". Require a well-formed 64-hex first token;
    # empty OR garbled device stdout → warn-and-skip (keep the push alive).
    parts = result.stdout.split()
    first = parts[0] if parts else ""
    if not re.fullmatch(r"[0-9a-fA-F]{64}", first):
        print(f"  ⚠️  sha256sum produced no usable output on device — remote verification skipped for {os.path.basename(remote_path)}")
        return
    if first != expected_sha256:
        raise subprocess.CalledProcessError(
            1, f"hash mismatch for {os.path.basename(remote_path)}"
        )


def cmd_push(manual_id, split_method=None, split_val=None, chunk_range=None, device_id=None, eager_rehash=False, temp_dir=None):
    print(f"--- PUSHING: {manual_id} ---")
    library = load_library()
    if manual_id not in library: print(f"❌ ID not found."); return False
    real_id, entry = _resolve_alias(library, manual_id)
    if real_id != manual_id:
        print(f"ℹ️  {manual_id} is part of the combined file registered as {real_id} — operating on that.")
        manual_id = real_id

    # PARENT AWARENESS INFO
    if "parent_id" in entry:
        print(f"   > ℹ️  Part of Season: {entry['parent_id']}")

    local_folder = entry['folder_path']
    filename = entry['filename']
    short_id = entry['short_id']  # Needed for tagging
    local_file_path = os.path.join(local_folder, filename)
    # [SPLIT-HASH] Step 5: optionally redirect the _parts/ chunk dir (and the
    # eager merge temp) to another volume via temp_dir. base_dir is local_folder
    # when temp_dir is None (byte-for-byte today's behavior) or temp_dir/<safe-id>
    # otherwise. The checksums/ sidecars AND the RollbackJournal STAY in
    # local_folder regardless — only the big chunk artifacts move.
    base_dir, _tmperr = _parts_base(local_folder, temp_dir, manual_id)
    if _tmperr:
        print(f"❌ {_tmperr}")
        return False
    parts_dir = os.path.join(base_dir, SPLIT_DIR_NAME)
    checksum_dir = os.path.join(local_folder, CHECKSUM_DIR_NAME)

    if not os.path.exists(local_file_path): print(f"❌ Source file missing."); return False

    # Calculate Remote Path
    try:
        rel_path = os.path.relpath(local_folder, LOCAL_ROOT)
    except:
        rel_path = os.path.basename(local_folder)
    remote_target_dir = f"{REMOTE_ROOT}/{rel_path}".replace("\\", "/")

    print(f"   > Target: {remote_target_dir}")
    if device_id:
        print(f"   > Device: {device_id}")
    adb_base = ["adb", "-s", device_id] if device_id else ["adb"]

    # [FIX] Escape single quotes for ADB Shell
    # ' -> '\'' turns 'Sorcerer's' into 'Sorcerer'\''s' which is shell-safe
    safe_remote_dir = remote_target_dir.replace("'", "'\\''")

    try:
        # Use the safe, escaped path for mkdir
        subprocess.run(adb_base + ["shell", "mkdir", "-p", f"'{safe_remote_dir}'"], check=True)
    except Exception as e:
        print(f"❌ Error: ADB Connection Failed. {e}");
        return False

    files_to_upload_paths = []
    chunk_metadata = []

    # [ROLLBACK SPEC] cmd_push has NO rollback PONR (O-1). A push failure is
    # RESUMABLE: the master survives, so chunks can always be re-split/re-pushed.
    # On failure the wrapper emits the resume-message (leave the partial upload,
    # entry stays local_ready/uploaded=False, print `push <id>`) — NOT a rollback.
    # The chunk delete @858-862 below is resumable, NOT a PONR. The only artifacts
    # a push rollback may remove are this-run _parts/checksums/split_info, and ONLY
    # if created-this-run AND the failure is pre-any-upload. A pre-existing _parts/
    # (the resume branch just below) MUST NEVER be deleted.
    # [ROLLBACK C] Open a durable journal for the pre-upload window. Record which
    # split artifacts pre-existed so only this-run ones are ever journalled (a
    # resume _parts/ gets no create_dir record and is never removed).
    journal = RollbackJournal(local_folder, manual_id)
    parts_preexisted = os.path.exists(parts_dir)
    checksum_preexisted = os.path.exists(checksum_dir)
    split_info_preexisted = "split_info" in library.get(manual_id, {})
    any_upload_done = False
    # 1. CHECK FOR RESUME (Existing _parts folder)
    if os.path.exists(parts_dir) and os.listdir(parts_dir):
        files_to_upload_paths = sorted(
            [os.path.join(parts_dir, f) for f in os.listdir(parts_dir) if f.endswith(".mkv")])
        print(f"   > 🔄 Resuming {len(files_to_upload_paths)} chunks found in temp folder.")

    # 2. NEW SPLIT LOGIC
    elif split_method and split_val:
        # Simple size check: If splitting by Size, check if file is smaller than target
        should_split = True
        if split_method in ["SIZE_MB", "SIZE_GB"]:
            fsize_mb = os.path.getsize(local_file_path) / (1024 * 1024)
            target_mb = float(split_val) if split_method == "SIZE_MB" else float(split_val) * 1024
            if fsize_mb < target_mb:
                print(
                    f"   > File size ({fsize_mb:.0f}MB) is smaller than split limit ({target_mb:.0f}MB). Skipping split.")
                should_split = False

        if should_split:
            # [SPLIT-HASH] HARD DISK PRE-FLIGHT (Step 4). STOP before splitting if
            # local_folder can't hold what the split would create — never start and
            # fail mid-split. Deferred needs 1X (chunks), eager 2X (chunks + the
            # merge temp), plus a max(1%, 2GB) buffer. This is a READ-ONLY check
            # (shutil.disk_usage) that runs BEFORE makedirs/journal records below, so
            # nothing has been created and there is NOTHING to roll back — a clean
            # early return like the chunk_range "no chunks" guard. (Step 5 may
            # redirect the chunks to temp_dir — see the check_dir note below.) The
            # resume branch above never reaches here, so an existing _parts/ skips the check.
            file_size = os.path.getsize(local_file_path)
            # [SPLIT-HASH] Step 5: stat the volume the chunks will ACTUALLY land
            # on. base_dir = temp_dir/<safe-id> does NOT exist yet (makedirs runs
            # below), so stat'ing it would raise FileNotFoundError → a false
            # hard-stop. temp_dir is validated-existing by _parts_base and shares
            # base_dir's volume (identical free bytes); with no temp_dir,
            # check_dir == local_folder → byte-identical to today.
            check_dir = temp_dir if temp_dir else local_folder
            if not _free_space_ok(check_dir, file_size, True, eager_rehash):
                free, required, _short = _disk_shortfall(
                    check_dir, file_size, True, eager_rehash)
                print(f"❌ Not enough free space to split {manual_id}.")
                print(f"   Need ~{human_readable_size(required)} free in {check_dir} "
                      f"({'chunks + merge temp' if eager_rehash else 'chunks'} + buffer); "
                      f"only {human_readable_size(free)} available.")
                print("   Free up space, or pass a temp dir on another volume.")
                if eager_rehash:
                    print("   (Or drop the `rehash` token to halve the need — deferred re-hash uses 1X, not 2X.)")
                return False
            print(f"   > ✂️ Splitting...")
            # [ROLLBACK C] Journal the dir creations (this run only) BEFORE makedirs.
            if not parts_preexisted:
                journal.record_create_dir(parts_dir)
            if not checksum_preexisted:
                journal.record_create_dir(checksum_dir)
            os.makedirs(parts_dir, exist_ok=True)
            os.makedirs(checksum_dir, exist_ok=True)

            # [UPDATED] Pass short_id to attach UID to chunk names
            files_to_upload_paths = split_video_file(local_file_path, parts_dir, split_method, split_val,
                                                     file_id=short_id)
            if not files_to_upload_paths:
                # [ROLLBACK C] Split failed pre-any-upload — replay journalled inverses.
                journal.rollback(library)
                return False  # Stop if split failed

            # Hash Chunks
            for chunk_path in files_to_upload_paths:
                c_name = os.path.basename(chunk_path)
                c_hash = calculate_file_hash(chunk_path)
                chunk_metadata.append({"filename": c_name, "hash": c_hash})
                # Save sidecar
                with open(os.path.join(checksum_dir, f"{c_name}.sha256"), 'w') as f: f.write(f"{c_hash} *{c_name}")

            # Save split info to library IMMEDIATELY
            # [ROLLBACK SPEC] split_info is written HERE this run. A pre-any-upload
            # rollback pops it ONLY if split_info_existed was False at entry (a prior
            # interrupted push may have left it). _parts/ + checksums/ created just
            # above (@719-720) are likewise removed only if created-this-run.
            if not split_info_preexisted:
                journal.record_set_field(manual_id, "split_info", existed=False, prior=None)
            library[manual_id]["split_info"] = {
                "is_split": True, "method": split_method, "val": split_val,
                "total_chunks": len(files_to_upload_paths), "chunks": chunk_metadata
            }
            # [SPLIT-HASH] RE-SPLIT REHASH RESET (new-split branch ONLY; the resume
            # branch above must NOT reset). Fresh chunks were just produced and the
            # OLD split_info (which may have carried merge_seed/merge_tool/
            # rehashed_at/canonical_hash for a prior, now-stale chunk set) was
            # REPLACED by the dict above — so those canonical fields are naturally
            # dropped. Clearing re_hashed too means a re-push of an already-blessed
            # entry ends unblessed → the next split restore re-blesses for the NEW
            # chunks instead of false-alarming a hash mismatch. A brand-new entry
            # (re_hashed absent) just becomes explicitly False — a no-op.
            # ROLLBACK: this writes the SAFE (unblessed) state, so it needs no
            # journalling — a push rollback leaving re_hashed=False is correct.
            library[manual_id]["re_hashed"] = False

            # [SPLIT-HASH] EAGER bless-at-push. Only when requested AND a NEW split
            # happened this run. Produces the deterministic canonical hash NOW (so a
            # later split restore just verifies) by merging the just-created chunks
            # into a throwaway temp and storing the hash as a TRANSIENT
            # split_info["canonical_hash"] pending promotion at cmd_replace. This is
            # best-effort: ANY failure cleans up, warns, writes NO canonical, and
            # CONTINUES as deferred (re_hashed stays False) — never aborts an
            # otherwise-successful push. The eager temp lives in split_info only,
            # which is already journalled this-run for NEW entries (record_set_field
            # above); no new rollback-relevant state is introduced and the push
            # remains PONR-less (O-1).
            if eager_rehash:
                seed = entry.get("short_id") or manual_id
                base = os.path.splitext(filename)[0]
                # [SPLIT-HASH] Step 5: eager merge temp lives next to the chunks
                # under base_dir (== local_folder when no temp_dir).
                rehash_tmp = os.path.join(base_dir, f"{base}.rehash_tmp.mkv")
                try:
                    print(f"   > 🧬 Eager canonical re-hash: merging {len(files_to_upload_paths)} chunks (seed={seed})...")
                    merged_ok = merge_video_files(files_to_upload_paths, rehash_tmp, seed=seed)
                    canonical = calculate_file_hash(rehash_tmp) if merged_ok else None
                    if merged_ok and canonical:
                        library[manual_id]["split_info"]["merge_seed"] = seed
                        library[manual_id]["split_info"]["merge_tool"] = _current_merge_tool()
                        library[manual_id]["split_info"]["canonical_hash"] = canonical
                        print(f"   > 🧬 Eager canonical hash staged (promotes at replace): {canonical}")
                    else:
                        print("   ⚠️ Eager re-hash did not produce a hash — continuing as deferred (will bless at first restore).")
                except Exception as e:
                    print(f"   ⚠️ Eager re-hash failed ({e}) — continuing as deferred (will bless at first restore).")
                finally:
                    if os.path.exists(rehash_tmp):
                        try:
                            os.remove(rehash_tmp)
                        except Exception:
                            pass

            save_library(library)
        else:
            files_to_upload_paths = [local_file_path]
    else:
        # Standard Push
        files_to_upload_paths = [local_file_path]

    # [NEW] 2.5 FILTER CHUNKS BY RANGE (IF REQUESTED)
    # This must happen AFTER splitting/hashing but BEFORE uploading
    if chunk_range and files_to_upload_paths:
        try:
            start, end = map(int, chunk_range.split('-'))
            print(f"   > 🎯 Filter: Uploading chunks {start} to {end} only.")

            filtered_files = []
            for f in files_to_upload_paths:
                # Extract chunk number from filename: .chunk.001.mkv
                match = re.search(r'\.chunk\.(\d+)\.', os.path.basename(f))
                if match:
                    chunk_num = int(match.group(1))
                    if start <= chunk_num <= end:
                        filtered_files.append(f)
                else:
                    # Keep non-chunk files just in case
                    filtered_files.append(f)

            files_to_upload_paths = filtered_files
            if not files_to_upload_paths:
                print(f"   ⚠️ No chunks found in range {chunk_range}. (They might already be uploaded/deleted)")
                return False
        except ValueError:
            print("❌ Invalid chunk range format. Use '1-4'.")
            return False

    # [IMP-C8] Build expected per-chunk hashes (local_filename -> stored SHA-256)
    # for optional post-push remote verification. Covers three cases:
    #   - new split: chunk_metadata was just populated above
    #   - resume (pre-existing _parts/): hashes already in library split_info
    #   - single-file push: dict stays empty -> verification skipped (no stored hash)
    _chunk_hashes: dict = {}
    if chunk_metadata:
        _chunk_hashes = {c["filename"]: c["hash"] for c in chunk_metadata
                         if c.get("hash")}
    elif "split_info" in library.get(manual_id, {}):
        _chunk_hashes = {
            c["filename"]: c["hash"]
            for c in library[manual_id]["split_info"].get("chunks", [])
            if c.get("hash")
        }

    # 3. UPLOAD LOOP
    all_success = True
    for f in files_to_upload_paths:
        local_fname = os.path.basename(f)

        # [UPDATED] File Renaming Logic for Standard Files
        remote_fname = local_fname
        if SPLIT_DIR_NAME not in f:  # This is a standard file, not a chunk
            name, ext = os.path.splitext(local_fname)
            # RENAME ON REMOTE: "MovieName [uid].mkv"
            remote_fname = f"{name} [{short_id}]{ext}"

        print(f"     -> Pushing: {remote_fname}...", end=" ", flush=True)
        try:
            # We construct the full remote path with the NEW name
            remote_full_path = f"{remote_target_dir}/{remote_fname}"
            # [PARTIAL+RENAME] Upload to "<final>.partial" first, then atomically
            # rename to the final name only after the push succeeds. A mid-push
            # death leaves a ".partial" remnant Google Photos never indexes as a
            # complete chunk. Resume (Decision 1) re-pushes to ".partial", which
            # overwrites any stale partial; no remote ls is needed.
            remote_partial_path = remote_full_path + PARTIAL_SUFFIX
            # Escape single quotes for both paths exactly like the mkdir path
            # above ( ' -> '\'' ).
            safe_partial = remote_partial_path.replace("'", "'\\''")
            safe_final = remote_full_path.replace("'", "'\\''")

            # [IMP-C2] Wrap the push + atomic mv in mvcommon.retry() so a
            # transient ADB CalledProcessError (USB reseat, screen lock) gets up
            # to 3 attempts with 1/4/16s backoff + jitter. on_retry prints one
            # user-visible line and unconditionally rm's the stale ".partial"
            # remnant before each re-attempt so the re-push never collides.
            # retry() re-raises the last CalledProcessError after exhaustion, so
            # the surrounding except below fires identically to before C2.
            def _push_and_rename():
                subprocess.run(adb_base + ["push", "-p", f, remote_partial_path], check=True)
                subprocess.run(
                    adb_base + ["shell", "mv", f"'{safe_partial}'", f"'{safe_final}'"],
                    check=True,
                )
                # [IMP-C8] post-push remote hash verification (gated on PUSH_VERIFY_REMOTE).
                # When False (default) this is byte-for-byte identical to pre-C8 behaviour.
                # A mismatch raises CalledProcessError, which the surrounding retry()
                # wrapper treats as a transient failure and re-runs push->mv->verify.
                if PUSH_VERIFY_REMOTE:
                    expected = _chunk_hashes.get(local_fname)
                    if expected:
                        _verify_chunk_hash(adb_base, remote_full_path, safe_final, expected)

            def _cleanup_and_log(attempt, exc):
                print(f"⏳ Retry {attempt}/3 after {(1, 4, 16)[min(attempt - 1, 2)]}s (ADB push/verify failed)…")
                subprocess.run(
                    adb_base + ["shell", "rm", f"'{safe_partial}'"],
                    check=False,
                )

            retry(
                _push_and_rename,
                attempts=3,
                backoff=(1, 4, 16),
                retry_on=(subprocess.CalledProcessError,),
                on_retry=_cleanup_and_log,
            )
            print("✅")
            # [ROLLBACK C] A chunk reached the device — past the pre-upload window.
            # O-1: any later failure is the resume-message case, not a rollback.
            any_upload_done = True

            # DELETE LOCAL CHUNK after successful upload+rename.
            # The chunk is "done" only once renamed to its final name.
            # Safety: Only delete if it's inside the SPLIT_DIR_NAME folder
            # [ROLLBACK SPEC] This delete is NOT a PONR (O-1) — the deleted chunk is
            # reproducible from the surviving master via re-split. A push failure
            # after some chunks were uploaded+deleted is the resume-message case.
            if SPLIT_DIR_NAME in f:
                try:
                    os.remove(f);
                except:
                    pass

        except subprocess.CalledProcessError:
            print("❌ FAIL (ADB Error)");
            all_success = False;
            break
        except Exception as e:
            print(f"❌ FAIL ({e})");
            all_success = False;
            break

    if all_success:
        # Cleanup temp dir if empty
        if os.path.exists(parts_dir) and not os.listdir(parts_dir): os.rmdir(parts_dir)
        # [SPLIT-HASH] Step 5: when chunks were redirected to temp_dir, the
        # per-entry base_dir (temp_dir/<safe-id>) is now empty too — remove it so
        # we leave no scratch dir behind. ONLY when this run created it (guard on
        # not parts_preexisted): a pre-existing temp _parts/ must never be
        # removed, and base_dir != local_folder ensures local_folder is never
        # touched (temp_dir=None ⇒ base_dir == local_folder ⇒ skipped, identical
        # to today).
        if temp_dir and not parts_preexisted and base_dir != local_folder:
            if os.path.isdir(base_dir) and not os.listdir(base_dir):
                try:
                    os.rmdir(base_dir)
                except OSError:
                    pass

        # Only mark as 'onboarded' if we uploaded ALL chunks (no range filter)
        if not chunk_range:
            # Best-effort remote disaster-recovery sidecar. A sidecar miss must
            # NOT fail a fully-successful chunk upload, so its return is ignored.
            write_remote_mvmeta(adb_base, remote_target_dir, manual_id, library[manual_id])
            library[manual_id]["uploaded"] = True
            library[manual_id]["status"] = "onboarded"
            save_library(library)
            # [ROLLBACK C] Clean success — discard the journal.
            journal.commit()
            # Warn-only post-condition (IMP-D4). Post-commit; does NOT affect rollback/PONR.
            _warn_if_entry_inconsistent(library[manual_id], manual_id)
            print("✅ SUCCESS.\n")
            return True
        else:
            journal.commit()
            # Warn-only post-condition (IMP-D4). Post-commit; does NOT affect rollback/PONR.
            _warn_if_entry_inconsistent(library[manual_id], manual_id)
            print(f"✅ Partial Upload Complete (Chunks {chunk_range}).\n")
            return True
    else:
        # [ROLLBACK C] Push failed (O-1: NO PONR — the master survives).
        if any_upload_done:
            # Resume-message: leave the partial upload; keep local_ready/uploaded=False.
            # A chunk reached the device, so the journalled this-run artifacts are now
            # legitimately part of the resumable state — discard the journal, do NOT
            # roll back.
            journal.commit()
            print("❌ FAILED. Partial upload left in place (resumable).")
            print(f"   > Entry stays local_ready / uploaded=False. Resume with: push {manual_id}\n")
            return False
        else:
            # Pre-any-upload failure: replay the journalled inverses (this-run
            # _parts/checksums/split_info only — a resume _parts/ was never journalled).
            print("❌ FAILED before any chunk uploaded — rolling back this-run artifacts.")
            journal.rollback(library)
            print(f"   > Resume with: push {manual_id}\n")
            return False


def _resolve_alias(lib, mid):
    """Return (real_id, entry) resolving a multi_ep_alias one hop to its primary.

    - If mid is a multi_ep_alias, follow alias_of once and return the primary id + primary entry.
    - If the alias target is missing from lib, return (mid, alias_entry) so callers can detect/skip.
    - Otherwise return (mid, lib[mid]) unchanged.
    Single-hop only; aliases never point at other aliases by construction.
    """
    entry = lib.get(mid)
    if entry is None:
        raise KeyError(mid)
    if entry.get("type") == "multi_ep_alias":
        primary_id = entry["alias_of"]
        primary_entry = lib.get(primary_id)
        if primary_entry is None:
            return (mid, entry)
        return (primary_id, primary_entry)
    return (mid, entry)


def parse_push_group_args(args):
    """Parse the token list for the `push_group` command (= sys.argv[2:]).

    Pure: parses tokens and returns
    (group_id, method, val, ep_range, dev, eager, tdir), or prints a usage/
    error message and calls sys.exit(1) on bad input. Mirrors the `push`
    parser's missing-value fail-fast arms so a trailing value-keyword
    terminates instead of spinning the parse loop. Unknown/typo'd tokens are
    silently skipped (same behavior as `push`). Performs no device resolution
    or I/O beyond the print+exit on bad input.
    """
    if not args:
        print("❌ Usage: push_group [id] ...")
        sys.exit(1)

    group_id = args[0]
    method = None
    val = None
    ep_range = None
    dev = None
    eager = False
    tdir = None

    i = 1
    while i < len(args):
        if args[i] in ["SIZE_MB", "SIZE_GB", "COUNT"]:
            if i + 1 < len(args):
                method = args[i]
                val = args[i + 1]
                i += 2
            else:
                print("❌ Error: Missing value for split method.")
                sys.exit(1)
        elif args[i] == "episodes":
            if i + 1 < len(args):
                ep_range = args[i + 1]
                i += 2
            else:
                print("❌ Error: Missing value for episodes range.")
                sys.exit(1)
        elif args[i] == "device":
            if i + 1 < len(args):
                dev = args[i + 1]
                i += 2
            else:
                print("❌ Error: Missing value for device.")
                sys.exit(1)
        elif args[i] == "rehash":
            eager = True
            i += 1
        elif args[i] == "tempdir":
            if i + 1 < len(args):
                tdir = args[i + 1]
                i += 2
            else:
                print("❌ Error: Missing value for tempdir.")
                sys.exit(1)
        else:
            i += 1

    return (group_id, method, val, ep_range, dev, eager, tdir)


def cmd_push_group(group_id, split_method=None, split_val=None, episode_range=None, device_id=None, eager_rehash=False, temp_dir=None):
    print(f"=== BATCH PUSH GROUP: {group_id} ===")
    library = load_library()
    target_ids = []

    # 1. Season Map Mode
    if group_id in library and library[group_id].get("type") == "season_map":
        print(f"   > Identified Season Map. Loading children...")
        target_ids = library[group_id]["children"]
    else:
        # 2. Prefix Match Mode
        print(f"   > Searching prefix '{group_id}'...")
        target_ids = sorted([k for k in library.keys() if k.startswith(group_id) and k != group_id])

    # 3. Apply Episode Range Filter [UPDATED to handle .5]
    if episode_range:
        try:
            start, end = map(float, episode_range.split('-'))
            print(f"   > 🎯 Filter: Episodes {start} to {end} only.")
            pre_filter_count = len(target_ids)
            pre_filter_sample = target_ids[0] if target_ids else None
            filtered_ids = []

            for mid in target_ids:
                ep_num = episode_num_from_id(mid, group_id)
                if ep_num is not None and start <= ep_num <= end:
                    filtered_ids.append(mid)

            target_ids = filtered_ids

            # [IMP-C18] 0-match guard: a NON-EMPTY pre-filter list reduced to 0 by
            # the range is the silent-no-op signal — warn loudly (parsed range +
            # a sample child id) so the user sees WHY nothing matched. Continue;
            # the `if not target_ids` guard below stops cleanly with no banner.
            if pre_filter_count and not filtered_ids:
                print(f"⚠️ Range {episode_range} matched 0 of {pre_filter_count} "
                      f"episodes (e.g. id '{pre_filter_sample}'). Nothing to push — "
                      f"check the range vs the season's episode numbers.")

        except ValueError:
            print("❌ Invalid episode range format. Use '1-3'.")
            return

    # De-alias: collapse multi_ep_alias ids to their primaries; dedup order-preserving.
    seen = set()
    dealiased = []
    for mid in target_ids:
        real_id, _ = _resolve_alias(library, mid)
        if real_id not in seen:
            seen.add(real_id)
            dealiased.append(real_id)
    target_ids = dealiased

    if not target_ids: print("❌ No items found to push."); return
    print(f"   > Processing {len(target_ids)} items...\n")

    # [SPLIT-HASH] HARD DISK PRE-FLIGHT (Step 4). Items are pushed SEQUENTIALLY
    # with per-item _parts cleanup, so the PEAK disk use is the LARGEST single
    # item that will split, NOT the sum. Find that worst item once and check it
    # against its folder volume BEFORE processing ANY item — read-only, pre-
    # any-creation, nothing to roll back. (Each cmd_push still does its own guard
    # as defense-in-depth.)
    max_req = 0
    worst_mid = None
    worst_size = 0
    worst_dir = None
    for mid in target_ids:
        if library[mid].get("uploaded") == True:
            continue  # already uploaded → won't push/split
        f = os.path.join(library[mid]["folder_path"], library[mid]["filename"])
        if not os.path.exists(f):
            continue
        fsize = os.path.getsize(f)
        ws = _will_split(fsize, split_method, split_val)
        req = _required_extra_bytes(fsize, ws, eager_rehash)
        if req > max_req:
            max_req = req
            worst_mid = mid
            worst_size = fsize
            worst_dir = library[mid]["folder_path"]
    if max_req > 0:
        buffer = _disk_buffer(max_req)
        # [SPLIT-HASH] Step 5: when redirecting chunks to temp_dir, the peak load
        # lands on the temp volume, so stat THAT (validate it once like cmd_push
        # does). temp_dir=None ⇒ check_dir == worst_dir, unchanged from today.
        check_dir = worst_dir
        if temp_dir:
            _probe_base, _tmperr = _parts_base(worst_dir, temp_dir, "_probe")
            if _tmperr:
                print(f"❌ {_tmperr}")
                return
            check_dir = temp_dir
        try:
            free = shutil.disk_usage(check_dir).free
        except Exception:
            free = -1
        if free < max_req + buffer:
            print(f"❌ Not enough free space to push group {group_id}.")
            print(f"   Largest splitting item: {worst_mid} ({human_readable_size(worst_size)}).")
            print(f"   Need ~{human_readable_size(max_req + buffer)} free in {check_dir} "
                  f"({'chunks + merge temp' if eager_rehash else 'chunks'} + buffer); "
                  f"only {human_readable_size(free)} available.")
            print("   Free up space, or pass a temp dir on another volume.")
            if eager_rehash:
                print("   (Or drop the `rehash` token to halve the need — deferred re-hash uses 1X, not 2X.)")
            return

    for mid in target_ids:
        if library[mid].get("uploaded") == True:
            print(f"⏭️  Skipping {mid} (Already uploaded)")
            continue
        cmd_push(mid, split_method, split_val, device_id=device_id, eager_rehash=eager_rehash, temp_dir=temp_dir)


def cmd_replace(manual_id):
    library = load_library()
    if manual_id not in library:
        print(f"❌ Error: '{manual_id}' not found in library.")
        return False
    real_id, entry = _resolve_alias(library, manual_id)
    if real_id != manual_id:
        print(f"ℹ️  {manual_id} is part of the combined file registered as {real_id} — operating on that.")
        manual_id = real_id

    if not entry.get("uploaded", False):
        print(f"⚠️ Skipping {manual_id}: Not marked as uploaded.")
        return False

    local_folder = entry['folder_path']
    filename = entry['filename']
    original = os.path.join(local_folder, filename)

    ext = os.path.splitext(filename)[1]
    # [ROLLBACK SPEC] tmp_path (<original>.dummy_tmp<ext>) is the ONLY pre-PONR
    # artifact in cmd_replace. A pre-PONR failure rolls back by deleting tmp_path.
    tmp_path = original + ".dummy_tmp" + ext
    # [ROLLBACK C] Open the journal and log the dummy-temp creation BEFORE writing
    # it. A hard kill before the commit leaves this record on disk so recovery
    # removes the orphan dummy temp.
    journal = RollbackJournal(local_folder, manual_id)
    journal.record_create_file(tmp_path)
    if not make_video_dummy(tmp_path, ext):
        print(f"❌ replace aborted — could not create video dummy for {filename}")
        journal.rollback(library)
        return False

    try:
        # Swap Files (atomic two-rename pattern — original is never absent without a leftover present)
        tobedeleted = original + ".tobedeleted"

        # STALE SWEEP: recover from a prior interrupted run before touching anything
        if os.path.exists(tobedeleted):
            try:
                if os.path.exists(original):
                    # leftover is redundant — the real file is already in place
                    print(f"     ⚠️ Stale leftover from prior interrupted run — cleaning up {os.path.basename(tobedeleted)}")
                    os.remove(tobedeleted)
                else:
                    # crash happened between rename-1 and rename-2: master is at .tobedeleted — restore and abort
                    print(f"     ⚠️ Recovered interrupted replace: restoring original from {os.path.basename(tobedeleted)}")
                    os.rename(tobedeleted, original)
                    print(f"❌ replace aborted — original restored. Please retry.")
                    journal.rollback(library)  # pre-PONR — remove the dummy temp
                    return False
            except Exception as e:
                print(f"     ⚠️ Could not clean stale leftover: {e}")

        # Step 1 (done above): make_video_dummy wrote tmp_path

        # Step 2 (commit / point-of-no-return): rename original -> .tobedeleted
        if os.path.exists(original):
            moved = False
            for attempt in range(3):
                try:
                    # Force Write Permissions
                    os.chmod(original, stat.S_IWRITE)
                    # [ROLLBACK SPEC] PONR (O-2): the master leaves its path here. BEFORE
                    # this line a failure rolls back (delete tmp_path). AT/AFTER this line
                    # a failure is a structured HARD-FAIL naming `fetch_restore <id>` (the
                    # bytes are in the cloud). C9's stale-sweep above (@966-979) already
                    # self-heals a torn crash on the NEXT replace; the wrapper must not
                    # double-handle that — pre-PONR rollback only removes tmp_path.
                    os.rename(original, tobedeleted)  # ROLLBACK SEAM: original removed from its path here (atomic commit / point-of-no-return)
                    # [ROLLBACK C] PONR crossed — write the marker to the journal.
                    journal.mark_point_of_no_return()
                    moved = True
                    break
                except PermissionError:
                    print(f"     ⚠️ File busy or locked. Retrying... ({attempt + 1}/3)")
                    time.sleep(1)
                except Exception as e:
                    print(f"❌ Error removing file: {e}")
                    journal.rollback(library)  # still pre-PONR — reversible
                    return False

            if not moved:
                print(f"❌ PERMISSION DENIED: Could not delete {filename}")
                print("   > Close any players/Plex scanning this file and try again.")
                journal.rollback(library)  # pre-PONR — roll back the dummy temp
                return False

        # Step 3: rename dummy temp -> original (dummy is now live)
        os.rename(tmp_path, original)

        # Step 4: delete the .tobedeleted leftover (non-fatal if it fails)
        if os.path.exists(tobedeleted):
            try:
                os.remove(tobedeleted)
            except Exception as e:
                print(f"     ⚠️ WARNING: Could not remove leftover {os.path.basename(tobedeleted)}: {e}. It will be cleaned on the next replace.")

        # [SPLIT-HASH] PROMOTE-AT-REPLACE. If an eager push staged a transient
        # canonical hash (split_info["canonical_hash"]) and the entry is not yet
        # blessed, promote it to the entry's truth NOW: canonical -> hash,
        # re_hashed=True, stamp rehashed_at, and drop the transient field. No-op
        # for non-eager / non-split entries (no canonical_hash present) and for an
        # already-blessed entry (re_hashed already True). This runs AFTER the
        # replace PONR (os.rename(original -> .tobedeleted)); it only mutates
        # in-memory library fields the existing save_library below persists, so it
        # introduces no new rollback-relevant journalled state.
        _split_info = library[manual_id].get("split_info", {})
        _staged = _split_info.get("canonical_hash")
        if _staged and library[manual_id].get("re_hashed") is not True:
            library[manual_id]["hash"] = _staged
            library[manual_id]["re_hashed"] = True
            library[manual_id]["split_info"]["rehashed_at"] = _rehashed_at()
            del library[manual_id]["split_info"]["canonical_hash"]
            print(f"   > 🧬 Promoted eager canonical hash to entry truth (re_hashed=True).")

        library[manual_id]["status"] = "archived"
        save_library(library)
        # [ROLLBACK C] Clean success — discard the journal.
        journal.commit()
        # Warn-only post-condition (IMP-D4). Post-commit; does NOT affect rollback/PONR.
        _warn_if_entry_inconsistent(library[manual_id], manual_id)
        print(f"✅ Replaced/Archived: {manual_id}")
        return True
    except RollbackHardFail:
        raise
    except Exception as e:
        if journal.crossed_ponr:
            # [ROLLBACK C] At/after PONR — hard-fail naming the EXISTING fetch_restore.
            # The journal (with its PONR marker) is left on disk for inspection.
            print(f"❌ IRREVERSIBLE: replace failed after the commit point for {manual_id}.")
            print(f"   > The original is no longer in place (C9 stale-sweep recovers it next run).")
            print(f"   > To recover the file from the cloud: fetch_restore {manual_id}")
            raise RollbackHardFail(
                state=f"{manual_id} archived (original committed)",
                reason=f"replace failed past the point-of-no-return: {e}",
                resume_cmd=f"fetch_restore {manual_id}",
            )
        print(f"❌ replace failed (pre-commit): {e}")
        journal.rollback(library)
        return False


def cmd_replace_group(group_id):
    print(f"=== BATCH REPLACE GROUP: {group_id} ===")
    library = load_library()

    target_ids = []
    if group_id in library and library[group_id].get("type") == "season_map":
        target_ids = library[group_id]["children"]
    else:
        target_ids = sorted([k for k in library.keys() if k.startswith(group_id) and k != group_id])

    if not target_ids: print("❌ No items found."); return

    # De-alias: collapse multi_ep_alias ids to their primaries; dedup order-preserving.
    seen = set()
    dealiased = []
    for mid in target_ids:
        real_id, _ = _resolve_alias(library, mid)
        if real_id not in seen:
            seen.add(real_id)
            dealiased.append(real_id)
    target_ids = dealiased

    # [FIX] Removed User Confirmation Prompt to match Movie behavior
    print(f"   > Auto-replacing {len(target_ids)} items...")
    for mid in target_ids:
        cmd_replace(mid)


# ==========================================
#   LIBRARY ↔ DISK INTEGRITY (IMP-D4 / IMP-D5)
# ==========================================
# Shared classification used by BOTH the read-only audit (cmd_verify_library)
# and the warn-only pipeline post-condition (_warn_if_entry_inconsistent), so the
# two can never drift. The invariant: a physical leaf's library `status` must be
# consistent with the on-disk file's shape.
#
# Real-world bug this guards (the reason for IMP-D4): 107 entries whose status said
# `local_ready` but whose on-disk file was a 126-byte legacy TEXT stub — the real
# video had been lost to a stub, mislabeled, and was un-fetchable.

# Statuses that own a REAL file on disk (master still present locally).
_REAL_FILE_STATUSES = ("local_ready", "onboarded", "restored_local")
# How many bytes of a small file we sniff to detect a legacy TEXT stub.
_TEXT_DUMMY_SNIFF_BYTES = 220


def _disk_shape(full_path):
    """Classify the on-disk shape of a physical leaf's file. Pure read-only.

    Returns one of:
      "MISSING"     - no file at full_path.
      "REAL"        - size >= DUMMY_MAX_BYTES (a genuine master / restored file).
      "TEXT_DUMMY"  - a small file that is a LEGACY text stub: its first
                      ~220 bytes start with b"Original Hash" OR contain
                      b"Status: SPLIT" (the exact byte-signatures of the old
                      text-stub format that the IMP-D4 bug left behind).
      "VIDEO_DUMMY" - any other small (< DUMMY_MAX_BYTES) file — a valid
                      ffmpeg-generated video dummy (or a small binary stand-in).

    Size is checked first: a file at/above DUMMY_MAX_BYTES is REAL regardless of
    its leading bytes (the dummy/stub formats only exist in the small regime).
    """
    if not os.path.exists(full_path):
        return "MISSING"
    try:
        size = os.path.getsize(full_path)
    except OSError:
        return "MISSING"
    if size >= DUMMY_MAX_BYTES:
        return "REAL"
    try:
        with open(full_path, "rb") as fh:
            head = fh.read(_TEXT_DUMMY_SNIFF_BYTES)
    except OSError:
        # Unreadable small file — treat as a (non-text) dummy rather than crash.
        return "VIDEO_DUMMY"
    if head.startswith(b"Original Hash") or b"Status: SPLIT" in head:
        return "TEXT_DUMMY"
    return "VIDEO_DUMMY"


def _status_disk_violation(status, shape):
    """Compare a leaf's status to its on-disk shape against the invariant.

    Returns (is_violation: bool, category: str). `category` is a stable,
    summary-friendly label (e.g. "archived_textdummy", "local_ready_missing",
    "ok", "unchecked"). Only the four invariant statuses are checked; any other
    status returns (False, "unchecked") — the audit reports it as not-applicable,
    never as a violation.

    Rules:
      local_ready / onboarded / restored_local -> expect REAL on disk.
      archived                                 -> expect VIDEO_DUMMY on disk
                                                   (NOT TEXT_DUMMY, NOT REAL, NOT MISSING).
    """
    if status in _REAL_FILE_STATUSES:
        if shape == "REAL":
            return (False, "ok")
        # e.g. local_ready_dummy / local_ready_missing / onboarded_textdummy
        suffix = {
            "VIDEO_DUMMY": "dummy",
            "TEXT_DUMMY": "textdummy",
            "MISSING": "missing",
        }.get(shape, shape.lower())
        return (True, f"{status}_{suffix}")
    if status == "archived":
        if shape == "VIDEO_DUMMY":
            return (False, "ok")
        suffix = {
            "TEXT_DUMMY": "textdummy",
            "REAL": "real",
            "MISSING": "missing",
        }.get(shape, shape.lower())
        return (True, f"archived_{suffix}")
    return (False, "unchecked")


def _warn_if_entry_inconsistent(entry, manual_id):
    """Warn-only pipeline post-condition (IMP-D4).

    Classifies a physical leaf's on-disk shape vs its library status using the
    SAME rules as cmd_verify_library and, on a violation, prints a single loud
    WARNING line. This is a post-commit observability check ONLY:

      * ALWAYS returns None.
      * NEVER raises (any unexpected error is swallowed — an observability hook
        must never be able to fail a command that already succeeded).
      * NEVER calls save_library, NEVER touches the journal / rollback, NEVER
        changes control flow.

    Virtual entries (season_map / multi_ep_alias) own no file and are skipped.
    """
    try:
        if not isinstance(entry, dict):
            return None
        if entry.get("type") in ("season_map", "multi_ep_alias"):
            return None
        folder_path = entry.get("folder_path")
        filename = entry.get("filename")
        if not folder_path or not filename:
            return None
        status = entry.get("status")
        shape = _disk_shape(os.path.join(folder_path, filename))
        is_violation, _category = _status_disk_violation(status, shape)
        if is_violation:
            print(
                f"⚠️  INTEGRITY: {manual_id} status={status} but on-disk={shape} "
                f"— run 'python main.py verify_library'"
            )
    except Exception:
        # Observability must never break a committed command. Stay silent.
        return None
    return None


def _dangling_evidence(entry, manual_id):
    """Heuristic: does this leaf look like an in-cloud entry mislabelled local?

    A "possibly-dangling" entry is one whose library status asserts it is purely
    local (status == "local_ready", or status missing) and whose `uploaded` flag
    is falsy, YET there is on-disk / in-entry evidence that a cloud copy actually
    exists (the regression FIX 1 closes: re-prepping a cloud-bearing entry could
    have clobbered its status back to local_ready, orphaning the cloud copy).

    READ-ONLY: never mutates the entry or the filesystem; any filesystem error is
    swallowed (a heuristic audit must never crash on an unreadable folder).

    Returns:
      "high" — strong evidence a cloud copy exists:
                 * entry has truthy `split_info` (it was split for upload), OR
                 * a `checksums/` subfolder under folder_path holds a `*.sha256`
                   whose name embeds THIS entry's short_id (the chunk parity
                   sidecars cmd_push writes — matched by short_id so a shared
                   season `checksums/` is attributed to the RIGHT episode), OR
                 * a `*.mvmeta.json` in folder_path that references this id.
      "low"  — only `search_term` is present. cmd_prep sets search_term on EVERY
               prepped entry, so on its own it is weak (a prepped-but-never-pushed
               local entry also has it) — reported separately, low-confidence.
      None   — not a dangling candidate (no evidence, or status/uploaded do not
               match the local-but-in-cloud shape; the caller pre-checks those).
    """
    try:
        # --- HIGH: split_info on the entry ---
        if entry.get("split_info"):
            return "high"

        folder_path = entry.get("folder_path")
        short_id = entry.get("short_id")

        if folder_path and os.path.isdir(folder_path):
            # --- HIGH: a checksums/ chunk sidecar embedding this entry's short_id ---
            if short_id:
                checksum_dir = os.path.join(folder_path, CHECKSUM_DIR_NAME)
                if os.path.isdir(checksum_dir):
                    try:
                        for name in os.listdir(checksum_dir):
                            if name.endswith(".sha256") and short_id in name:
                                return "high"
                    except OSError:
                        pass

            # --- HIGH: a remote-recovery mvmeta sidecar referencing this id ---
            # write_remote_mvmeta tags the sidecar `<base> [<short_id>].mvmeta.json`;
            # it normally lands on the device, but if one ever sits locally next to
            # the master it is hard proof a push happened. Match by short_id (or the
            # manual_id) embedded in the filename.
            try:
                for name in os.listdir(folder_path):
                    if name.endswith(MVMETA_SUFFIX) and (
                        (short_id and short_id in name) or manual_id in name
                    ):
                        return "high"
            except OSError:
                pass

        # --- LOW: search_term only (weak — cmd_prep sets it on every entry) ---
        if entry.get("search_term"):
            return "low"
    except Exception:
        # A read-only heuristic must never break the audit.
        return None
    return None


def cmd_verify_library(fix_dummies=False):
    """READ-ONLY audit of the library status ↔ on-disk shape invariant (IMP-D4).

    Iterates every entry; skips virtual types (season_map / multi_ep_alias) BEFORE
    dereferencing folder_path/filename (the PR #21 crash class). For each physical
    leaf, classifies the on-disk shape via _disk_shape and flags any status/shape
    mismatch (see _status_disk_violation for the rules).

    Returns True if the library is clean (no violations), False if ANY violation
    was found. Never calls sys.exit — the boolean is the contract for tests / the
    pipeline / CI.

    fix_dummies=True (the IMP-D5 slice): after reporting, regenerate the proper
    video dummies for archived+TEXT_DUMMY entries by REUSING cmd_repair_dummies()
    (it already only touches archived entries whose on-disk file is < DUMMY_MAX_BYTES
    and rewrites via make_video_dummy + os.replace — we do NOT duplicate that logic).
    fix_dummies does NOT change any status/uploaded field; status mismatches are
    reported for a human to resolve.

    ADDITIVE possibly-dangling pass: over the SAME physical leaves, flag any entry
    that looks local (status local_ready or missing, uploaded falsy) yet shows
    evidence of a cloud copy (see _dangling_evidence: split_info / a checksums
    sidecar embedding its short_id / an mvmeta sidecar = HIGH; search_term-only =
    LOW). These are printed as ADVISORIES and counted in the summary, but — being a
    heuristic that needs Google-Photos confirmation, not a hard status↔disk
    mismatch — they DO NOT affect the True/False return (that stays driven solely
    by _status_disk_violation). Read-only; alias/season_map-safe.

    # TODO IMP-D4: also add orphan-parent / stale-season-map checks
    # TODO future --reconcile-dangling: after GP confirmation, set_uploaded the
    #   HIGH-confidence possibly_dangling entries (flip uploaded→True so they stop
    #   being re-preppable) — left manual here because it mutates and needs a human.
    """
    print("--- VERIFY LIBRARY (status ↔ disk integrity) ---")
    library = load_library()

    scanned = 0
    ok = 0
    violations = []          # list of (manual_id, status, shape, size, path, category)
    category_counts = {}
    dangling = []            # list of (manual_id, tier, full_path) — ADVISORY ONLY

    for manual_id, entry in library.items():
        # Skip virtual types BEFORE touching folder_path/filename (PR #21 crash class).
        if not isinstance(entry, dict):
            continue
        if entry.get("type") in ("season_map", "multi_ep_alias"):
            continue
        folder_path = entry.get("folder_path")
        filename = entry.get("filename")
        if not folder_path or not filename:
            # A physical leaf with no file keys is itself malformed, but the
            # orphan/structure checks are out of scope here (see TODO above).
            continue

        scanned += 1
        full_path = os.path.join(folder_path, filename)
        shape = _disk_shape(full_path)
        status = entry.get("status")
        try:
            size = os.path.getsize(full_path)
        except OSError:
            size = -1

        is_violation, category = _status_disk_violation(status, shape)
        if is_violation:
            violations.append((manual_id, status, shape, size, full_path, category))
            category_counts[category] = category_counts.get(category, 0) + 1
        else:
            ok += 1

        # ---- ADDITIVE: possibly-dangling detection (advisory, never affects return) ----
        # Candidate shape: looks purely local (local_ready or status missing) AND
        # not uploaded. Only then do we look for cloud evidence. archived/onboarded/
        # restored_local entries already assert a cloud copy correctly and are NOT
        # danglers (e.g. archived+uploaded=True is the intended end state).
        if (status == "local_ready" or status is None) and not entry.get("uploaded"):
            tier = _dangling_evidence(entry, manual_id)
            if tier:
                dangling.append((manual_id, tier, full_path))

    # ---- Per-violation report ----
    if violations:
        print(f"\n❌ {len(violations)} INTEGRITY MISMATCH(ES):")
        for manual_id, status, shape, size, full_path, category in violations:
            size_str = "missing" if size < 0 else human_readable_size(size)
            print(f"   • {manual_id}")
            print(f"       status={status}  on-disk={shape} ({size_str})  [{category}]")
            print(f"       {full_path}")
    else:
        print("✅ No integrity mismatches found.")

    # ---- Possibly-dangling advisory (heuristic — does NOT affect the return) ----
    dangling_high = sum(1 for d in dangling if d[1] == "high")
    dangling_low = sum(1 for d in dangling if d[1] == "low")
    if dangling:
        print(
            f"\n⚠️  POSSIBLY DANGLING (in-cloud but marked local/not-uploaded) — "
            f"{len(dangling)} (heuristic; confirm in Google Photos before reconciling):"
        )
        # HIGH first (strongest evidence), then LOW.
        for manual_id, tier, full_path in sorted(dangling, key=lambda d: (d[1] != "high", d[0])):
            print(f"   • {manual_id}  [{tier}]")
            print(f"       {full_path}")

    # ---- Summary line (stable, parseable) ----
    counts_str = ", ".join(f"{k}={v}" for k, v in sorted(category_counts.items()))
    print(
        f"verify_library: scanned {scanned}, OK {ok}, MISMATCH {len(violations)}"
        + (f" ({counts_str})" if counts_str else "")
        + (f" | possibly_dangling: {len(dangling)} (high={dangling_high}, low={dangling_low})"
           if dangling else "")
    )

    # ---- Optional fix (IMP-D5 slice): regenerate archived+TEXT_DUMMY dummies ----
    # Reuse the EXISTING cmd_repair_dummies — it already targets exactly the
    # archived + (< DUMMY_MAX_BYTES) class and rewrites via make_video_dummy +
    # os.replace. Status mismatches are intentionally left for the human.
    if fix_dummies:
        archived_textdummy = sum(
            1 for v in violations if v[5] == "archived_textdummy"
        )
        if archived_textdummy:
            print(
                f"\n🔧 fix_dummies: regenerating video dummies for "
                f"{archived_textdummy} archived+TEXT_DUMMY entr(y/ies) "
                f"via repair_dummies (status fields are NOT changed)..."
            )
            cmd_repair_dummies()
        else:
            print("\n🔧 fix_dummies: no archived+TEXT_DUMMY entries to regenerate.")

    return len(violations) == 0


def cmd_repair_dummies(prefix_filter=None):
    library = load_library()
    scanned = 0
    regenerated = 0
    skipped = 0
    missing = 0
    failed = 0

    for entry_id, entry in library.items():
        if entry.get("type") == "season_map":
            continue
        if entry.get("type") == "multi_ep_alias":
            continue
        if prefix_filter and not entry_id.startswith(prefix_filter):
            continue
        if entry.get("status") != "archived":
            continue

        scanned += 1
        current_path = os.path.join(entry['folder_path'], entry['filename'])

        if not os.path.exists(current_path):
            print(f"⚠️ Missing: {current_path}")
            missing += 1
            continue

        if os.path.getsize(current_path) >= DUMMY_MAX_BYTES:
            skipped += 1
            continue

        if os.path.splitext(entry['filename'])[1].lower() not in VIDEO_EXTENSIONS:
            skipped += 1
            continue

        ext = os.path.splitext(entry['filename'])[1]
        tmp_path = current_path + ".repair_tmp" + ext

        print(f"🔧 Regenerating dummy: {current_path}")
        if not make_video_dummy(tmp_path, ext):
            print(f"❌ Failed to regenerate {current_path}")
            failed += 1
            continue

        os.replace(tmp_path, current_path)
        regenerated += 1

    print(f"✅ repair_dummies complete: scanned {scanned}, regenerated {regenerated}, skipped {skipped}, missing {missing}, failed {failed}")


# ==========================================
#             RESTORE COMMANDS
# ==========================================

def cmd_verify_restore(manual_id):
    print(f"--- VERIFYING RESTORE (DRY RUN): {manual_id} ---")
    library = load_library()
    if manual_id not in library: print("❌ ID not found."); return
    real_id, entry = _resolve_alias(library, manual_id)
    if real_id != manual_id:
        print(f"ℹ️  {manual_id} is part of the combined file registered as {real_id} — operating on that.")
        manual_id = real_id

    # Auto-detect restore folder
    restore_folder = os.path.join(entry['folder_path'], RESTORE_DIR_NAME)

    if not os.path.exists(restore_folder):
        print(f"❌ Error: Restore folder missing at:\n   {restore_folder}")
        return

    # A. CHECK SPLIT FILES
    if entry.get("split_info") and entry["split_info"].get("is_split"):
        print(f"   > Detected Split File ({entry['split_info']['total_chunks']} chunks).")
        chunks = entry["split_info"]["chunks"]
        all_pass = True

        for chunk in chunks:
            expected_name = chunk["filename"]
            expected_hash = chunk["hash"]
            target_path = os.path.join(restore_folder, expected_name)

            if not os.path.exists(target_path):
                print(f"     ❌ MISSING: {expected_name}")
                all_pass = False
                continue

            actual_hash = calculate_file_hash(target_path)
            if actual_hash == expected_hash:
                print(f"     ✅ Verified: {expected_name}")
            else:
                print(f"     ❌ CORRUPT: {expected_name}")
                all_pass = False

        if all_pass:
            print("\n✅ SUCCESS: All chunks verified.")
        else:
            print("\n❌ FAILURE: Missing or corrupt chunks.")

    # B. CHECK STANDARD FILES
    else:
        print(f"   > Detected Standard File.")
        target_path = os.path.join(restore_folder, entry["filename"])

        if not os.path.exists(target_path):
            print(f"❌ Error: File {entry['filename']} not found in restore folder.");
            return

        actual_hash = calculate_file_hash(target_path)
        if actual_hash == entry['hash']:
            print(f"✅ SUCCESS: Verified against Master Hash.")
        else:
            print(f"❌ FAILURE: Hash mismatch.")


def quarantine_restore_file(restore_folder, filename):
    """Move a bad restore file into restore/quarantine/<filename>.<ISO-ts>.
    Returns the destination path. Single source of truth for 'where a bad
    restore file goes' — reused by auto-rollback's restore handling."""
    quarantine_dir = os.path.join(restore_folder, "quarantine")
    os.makedirs(quarantine_dir, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%dT%H%M%S")   # filesystem-safe ISO-ish
    dest = os.path.join(quarantine_dir, f"{filename}.{ts}")
    # collision guard: if same filename quarantined twice within one second,
    # append a counter so we never overwrite a prior quarantined copy.
    n = 1
    final = dest
    while os.path.exists(final):
        final = f"{dest}.{n}"
        n += 1
    shutil.move(src=os.path.join(restore_folder, filename), dst=final)
    return final


def cmd_restore(manual_id):
    print(f"--- RESTORING: {manual_id} ---")
    library = load_library()
    if manual_id not in library: print("❌ ID not found."); return False
    real_id, entry = _resolve_alias(library, manual_id)
    if real_id != manual_id:
        print(f"ℹ️  {manual_id} is part of the combined file registered as {real_id} — operating on that.")
        manual_id = real_id

    local_folder = entry['folder_path']
    restore_folder = os.path.join(local_folder, RESTORE_DIR_NAME)
    filename = entry['filename']
    target_path = os.path.join(local_folder, filename)

    if not os.path.exists(restore_folder):
        print(f"⏭️  Skipping {manual_id}: 'restore' folder missing.")
        return False

    # A. SPLIT RESTORE
    if entry.get("split_info") and entry["split_info"].get("is_split"):
        chunks_meta = entry["split_info"]["chunks"]
        chunk_files = [c['filename'] for c in chunks_meta]
        chunk_paths_in_restore = [os.path.join(restore_folder, c) for c in chunk_files]

        # 1. Verification of Existence
        if not all(os.path.exists(p) for p in chunk_paths_in_restore):
            print(f"⏭️  Skipping {manual_id}: Incomplete chunks in restore folder.")
            return False

        # 1b. Pre-merge per-chunk hash verification. mkvmerge is lenient and
        # would otherwise silently fold a corrupt chunk into a bad merged file,
        # so verify each chunk against its stored hash BEFORE merging.
        bad_chunks = []
        for c in chunks_meta:
            chunk_path = os.path.join(restore_folder, c['filename'])
            if calculate_file_hash(chunk_path) != c['hash']:
                bad_chunks.append(c['filename'])

        if bad_chunks:
            # [ROLLBACK SPEC] Pre-PONR reversible path. The wrapper REUSES this C11
            # quarantine + reproducible-output cleanup (@1213-1217) as the clean-state
            # behavior — it must NOT duplicate it. No merged target exists yet that
            # this run cannot reproduce from chunks.
            # Quarantine only the offending chunk(s); clean chunks stay in
            # restore/ so a targeted re-fetch refills just the bad ones.
            for chunk_filename in bad_chunks:
                try:
                    q = quarantine_restore_file(restore_folder, chunk_filename)
                    print(f"❌ Hash mismatch. Bad file quarantined at {q}. A fresh fetch will re-download.")
                except Exception:
                    print(f"❌ Error: Restore chunk hash mismatch ({chunk_filename})! Corrupt?")
            # Delete any stale partial merged output from a prior failed merge;
            # it is reproducible from chunks, so re-fetch + re-merge regenerates it.
            if os.path.exists(target_path):
                try:
                    os.remove(target_path)
                except Exception:
                    pass
            return False

        # 1c. RESTORE-SIDE DISK PRE-CHECK (pre-merge, pre-PONR). The merge writes
        # the reassembled file (~original size) into local_folder while the chunks
        # still sit in restore/, so this is a transient extra-bytes requirement.
        # Estimate the merged size from the actual on-disk chunk sizes (their sum
        # ~= the original whole-file size); fall back to the entry's recorded
        # size_bytes if a chunk can't be stat'd. Treat it as a deferred split
        # (will_split=True, eager=False) so the disk helper requires merged_size
        # + buffer. Insufficient room → hard-stop BEFORE the merge: nothing is
        # created, the chunks are untouched in restore/, nothing to roll back.
        try:
            merged_size = sum(os.path.getsize(p) for p in chunk_paths_in_restore)
        except Exception:
            merged_size = entry.get("tech_spec", {}).get("size_bytes", 0)
        if not _free_space_ok(local_folder, merged_size, will_split=True, eager=False):
            free, required, _short = _disk_shortfall(
                local_folder, merged_size, will_split=True, eager=False)
            print(f"❌ Not enough free space to re-merge {manual_id}.")
            print(f"   Need ~{human_readable_size(required)} free in {local_folder} "
                  f"(merged file + buffer); only {human_readable_size(free)} available.")
            print("   Chunks left untouched in restore/ — free space and retry.")
            return False

        # 2. Merge
        # SEED: the deterministic merge must use the SAME seed that produced (or
        # will produce) the canonical hash. Reuse a previously-stored seed if the
        # entry was already blessed; otherwise the entry's short_id is the seed
        # (manual_id is the stable fallback if an older entry lacks short_id — it
        # is the unique library key short_id is derived from, never None/empty).
        # Chosen/persisted BEFORE the merge so the canonical-producing merge below
        # uses exactly the stored value.
        seed = entry["split_info"].get("merge_seed") or entry.get("short_id") or manual_id
        # [ROLLBACK C + IMP-R6] The merge is PRE-PONR. To guarantee the archived
        # dummy at target_path is NEVER lost on a merge/verify failure, merge into a
        # TEMP sibling (<target>.merge_tmp<ext>) and atomically os.replace() it into
        # place ONLY after the merge SUCCEEDS and its hash is verified/blessed
        # (option a). The journal records the TEMP path as the reproducible output, so
        # any pre-swap failure removes the TEMP and leaves the dummy intact — the entry
        # stays archived AND a file still exists at target_path, so media servers do
        # not drop the title; it self-heals on retry. PONR placement is UNCHANGED — the
        # chunk delete still happens after the swap.
        merge_root, merge_ext = os.path.splitext(target_path)
        merge_tmp_path = f"{merge_root}.merge_tmp{merge_ext}"
        journal = RollbackJournal(local_folder, manual_id)
        journal.record_create_reproducible(merge_tmp_path)
        try:
            merged_ok = merge_video_files(chunk_paths_in_restore, merge_tmp_path, seed=seed)
        except Exception as e:
            print(f"❌ Merge crashed: {e}")
            merged_ok = False
        if not merged_ok:
            print(f"❌ Merge failed for {manual_id}. Chunks left in restore/ for re-merge.")
            journal.rollback(library)  # removes the temp; the dummy at target_path is untouched
            return False

        if merged_ok:
            print(f"   > 💾 Re-indexing Merged File (New Container)...")
            # [IMP-R6] Hash/verify the TEMP merge output; it is swapped onto
            # target_path only after this verify/bless gate passes.
            new_hash = calculate_file_hash(merge_tmp_path)

            # VERIFY-OR-BLESS. The pure helper returns the policy; ALL mutations,
            # save_library, journal calls, and I/O stay HERE so the seam stays
            # trivially unit-testable.
            decision = bless_or_verify_merged_hash(entry, new_hash)

            if decision == "mismatch":
                # Already-blessed entry whose deterministic re-merge did NOT
                # reproduce the stored canonical hash → corruption or tool drift.
                # LOUD, greppable alarm naming id + expected/actual + the stored
                # merge_tool for drift triage. Return PRE-PONR: reuse the SAME
                # reproducible-output rollback as the merge-fail branch above (the
                # merged target_path is reproducible, so removing it is correct),
                # and DO NOT cross the PONR or delete chunks — they stay in
                # restore/ for a re-fetch / re-merge.
                stored_tool = entry.get("split_info", {}).get("merge_tool", "(unknown)")
                print(f"🛑 RESTORE HASH MISMATCH — canonical re-hash verification FAILED for {manual_id}")
                print(f"   expected (stored canonical): {entry.get('hash')}")
                print(f"   actual   (this re-merge)   : {new_hash}")
                print(f"   stored split_info.merge_tool: {stored_tool}; this run: {_current_merge_tool()}")
                print("   Possible corruption or mkvmerge version drift. Chunks kept in restore/ for re-fetch.")
                journal.rollback(library)
                return False

            if decision == "bless":
                # First canonical bless: this merged hash BECOMES the truth.
                library[manual_id]["hash"] = new_hash
                library[manual_id]["re_hashed"] = True
                library[manual_id]["split_info"]["merge_seed"] = seed
                library[manual_id]["split_info"]["merge_tool"] = _current_merge_tool()
                library[manual_id]["split_info"]["rehashed_at"] = _rehashed_at()
            # decision == "ok": already-blessed and the re-merge reproduced the
            # canonical hash — leave hash/split_info untouched.

            # [IMP-R6] Verify/bless passed → atomically swap the verified-good merged
            # file into place. PRE-PONR: target_path's prior content (the archived
            # dummy) is only ever replaced by a verified-good, chunk-reproducible file,
            # and the chunks in restore/ stay the source of truth until the delete
            # below. A failed swap (e.g. a locked target) is reversible — drop the temp
            # and keep the dummy, so NO failure path ever leaves zero bytes at the path.
            try:
                os.replace(merge_tmp_path, target_path)
            except Exception as e:
                print(f"❌ Could not place the merged file (target locked?): {e}")
                print("   Dummy left intact in place; chunks kept in restore/ for retry.")
                journal.rollback(library)  # removes the temp; the dummy at target_path is untouched
                return False

            library[manual_id]["status"] = "restored_local"
            save_library(library)

            # --- CLEANUP ---
            # [ROLLBACK SPEC] PONR (O-2, split path): the merged chunks are deleted
            # from restore/ here. BEFORE this loop a restore failure is reversible
            # (the merged target_path is reproducible from chunks — safe to remove).
            # AT/AFTER this delete a failure is a structured HARD-FAIL naming
            # `fetch_restore <id>` (a re-merge now needs a re-fetch). The standard
            # path (@1248) is a single shutil.move with no torn window — no PONR.
            # [ROLLBACK C] Merge done + library saved → cross the PONR (writes the
            # marker) and discard the journal; the best-effort chunk delete below
            # is no longer rollback-eligible.
            journal.mark_point_of_no_return()
            journal.commit()
            # Warn-only post-condition (IMP-D4). Post-commit; does NOT affect rollback/PONR.
            _warn_if_entry_inconsistent(library[manual_id], manual_id)
            print("   > 🧹 Cleaning up chunks...")
            for p in chunk_paths_in_restore:
                try:
                    os.remove(p)
                except Exception as e:
                    print(f"     ⚠️ Warning: Could not delete {os.path.basename(p)}")
            if not os.listdir(restore_folder):
                try:
                    os.rmdir(restore_folder)
                except:
                    pass
            # ---------------

            print(f"✅ SUCCESS: {filename} restored & re-indexed.")
            return True

    # B. STANDARD RESTORE
    else:
        source_path = os.path.join(restore_folder, filename)
        if not os.path.exists(source_path):
            print(f"⏭️  Skipping {manual_id}: File not found in restore folder.")
            return False

        # 1. Verify Hash
        print("   > Verifying Hash before restore...")
        if calculate_file_hash(source_path) != entry['hash']:
            try:
                q = quarantine_restore_file(restore_folder, filename)
                print(f"❌ Hash mismatch. Bad file quarantined at {q}. A fresh fetch will re-download.")
            except Exception:
                # Fallback: if the move is blocked (e.g. Windows file lock),
                # never make restore worse than before — leave the file in place.
                print("❌ Error: Restore file hash mismatch! Corrupt?")
            return False

        # 2. Move File
        if os.path.exists(target_path):
            try:
                os.remove(target_path)
            except:
                pass

        try:
            shutil.move(source_path, target_path)
        except Exception as e:
            print(f"❌ Error moving file: {e}");
            return False

        # Cleanup restore folder if empty
        if os.path.exists(restore_folder) and not os.listdir(restore_folder):
            try:
                os.rmdir(restore_folder)
            except:
                pass

        library[manual_id]["status"] = "restored_local"
        save_library(library)
        # Warn-only post-condition (IMP-D4). Post-commit; does NOT affect rollback/PONR.
        _warn_if_entry_inconsistent(library[manual_id], manual_id)
        print(f"✅ SUCCESS: {filename} restored.")
        return True


def cmd_restore_group(group_id, episode_range=None):
    # [UPDATED] Added episode_range support and handling for .5
    print(f"=== BATCH RESTORE GROUP: {group_id} ===")
    library = load_library()

    target_ids = []
    if group_id in library and library[group_id].get("type") == "season_map":
        target_ids = library[group_id]["children"]
    else:
        target_ids = sorted([k for k in library.keys() if k.startswith(group_id) and k != group_id])

    # Filter Items if Range Provided
    empty_via_range = False  # [IMP-C18] set when a range nukes a non-empty list to 0
    if episode_range:
        try:
            start, end = map(float, episode_range.split('-'))
            pre_filter_count = len(target_ids)
            pre_filter_sample = target_ids[0] if target_ids else None
            filtered = []
            for mid in target_ids:
                ep = episode_num_from_id(mid, group_id)
                if ep is not None and start <= ep <= end:
                    filtered.append(mid)
            target_ids = filtered
            print(f"   > Filtered to {len(target_ids)} items (Episodes {episode_range}).")
            # [IMP-C18] 0-match guard: NON-EMPTY pre-filter list reduced to 0 by the
            # range is the silent-no-op signal. Flag it so the celebratory "Complete"
            # line below is replaced with a ⚠️ (continue normally, no exception).
            if pre_filter_count and not filtered:
                empty_via_range = True
                print(f"⚠️ Range {episode_range} matched 0 of {pre_filter_count} "
                      f"episodes (e.g. id '{pre_filter_sample}'). Nothing to restore — "
                      f"check the range vs the season's episode numbers.")
        except:
            print("   ⚠️ Invalid range. Processing all.")

    # De-alias: collapse multi_ep_alias ids to their primaries; dedup order-preserving.
    seen = set()
    dealiased = []
    for mid in target_ids:
        real_id, _ = _resolve_alias(library, mid)
        if real_id not in seen:
            seen.add(real_id)
            dealiased.append(real_id)
    target_ids = dealiased

    count = 0
    for mid in target_ids:
        # Loop blindly - the restore command handles checks
        if cmd_restore(mid):
            count += 1

    # [IMP-C18] On a 0-via-range run the warning above already explained the no-op;
    # skip the green "Complete" line so we don't celebrate restoring nothing.
    if not (empty_via_range and count == 0):
        print(f"\n=== Batch Restore Complete: {count} files restored. ===")
    return count


def cmd_sort():
    print("--- SORTING LIBRARY ---")
    library = load_library()
    if not library:
        print("❌ Library is empty or could not be loaded.")
        return

    # --- CONFIGURATION: LANGUAGE PRIORITY ---
    # Define the order here. Lower number = Higher priority.
    # IDs are split by '-': mov-LANG-YEAR-TITLE
    lang_priority_map = {
        'en': 1,  # English First
        'ta': 2,  # Tamil Second
        'hi': 3  # Hindi Third
    }

    def sort_key(item):
        key, entry = item
        parts = key.split('-')

        # 1. PARSE LANGUAGE (Index 1: mov-EN-...)
        lang = parts[1] if len(parts) > 1 else 'zz'
        # Default priority is 99 (bottom of list) if language not in map
        prio = lang_priority_map.get(lang, 99)

        # 2. PARSE YEAR (Index 2: mov-en-2025-...)
        try:
            year = int(parts[2])
        except (IndexError, ValueError):
            year = 0

        # 3. PARSE SIZE
        # We use .get() chains to avoid crashing on missing keys
        size = entry.get('tech_spec', {}).get('size_bytes', 0)

        # --- SORT LOGIC ---
        # 1. Language Priority (Ascending: 1 -> 2 -> 3)
        # 2. Year (Descending: 2025 -> 2000). We use negative for Desc.
        # 3. Size (Descending: Big -> Small). We use negative for Desc.
        return (prio, -year, -size)

    # Perform the Sort
    sorted_items = sorted(library.items(), key=sort_key)

    # Rebuild dictionary (Python 3.7+ preserves insertion order)
    sorted_library = {k: v for k, v in sorted_items}

    # Save
    save_library(sorted_library)
    print(f"✅ Library sorted ({len(sorted_library)} items).")
    print(f"   Order: English -> Tamil -> Hindi | Year (Newest) | Size (Largest)")


def cmd_local_status(limit_arg=None):
    print("--- LOCAL FILE STATUS ---")
    library = load_library()
    if not library: print("❌ Library empty."); return

    # Filter: Files that are NOT uploaded
    # We ignore "Season Maps" since they are virtual containers
    pending_items = []

    for mid, entry in library.items():
        if entry.get("type") in ("season_map", "multi_ep_alias"): continue

        # Condition: Uploaded is False (or missing)
        if not entry.get("uploaded", False):
            # Get Size
            size = entry.get("tech_spec", {}).get("size_bytes", 0)
            pending_items.append({
                "id": mid,
                "filename": entry.get("filename"),
                "size_bytes": size
            })

    if not pending_items:
        print("✅ No pending uploads. All files are synced.")
        return

    # Sort pending items by Size Descending (Biggest First)
    # This helps visual clarity and the Greedy Algo later
    pending_items.sort(key=lambda x: x["size_bytes"], reverse=True)

    limit_bytes = 0
    if limit_arg:
        limit_bytes = parse_size_str(limit_arg)
        if not limit_bytes:
            print(f"❌ Invalid size limit: {limit_arg}. Use format like '40gb' or '500mb'.")
            return
        print(f"🎯 Optimization Target: Fit into {limit_arg} ({human_readable_size(limit_bytes)})")
        print(f"   Strategy: Fill the bucket (Largest First) to maximize storage use.\n")

    # Display / Calculate
    total_pending_size = sum(x["size_bytes"] for x in pending_items)
    selected_items = []
    current_size = 0

    print(f"{'ID':<25} | {'Size':<10} | {'Filename'}")
    print("-" * 60)

    for item in pending_items:
        s_str = human_readable_size(item["size_bytes"])

        # LOGIC: Selection Algorithm (Greedy First Fit Descending)
        is_selected = False
        if limit_bytes > 0:
            if (current_size + item["size_bytes"]) <= limit_bytes:
                selected_items.append(item)
                current_size += item["size_bytes"]
                is_selected = True
                prefix = "✅ "  # Marks selected
            else:
                prefix = "   "  # Not selected
        else:
            prefix = ""  # No limit mode

        # Print row
        # If in limit mode, only print selected? Or all with markers?
        # User wants "check what all files are in Local", so print all.
        print(f"{prefix}{item['id']:<25} | {s_str:<10} | {item['filename'][:40]}...")

    print("-" * 60)
    print(f"Total Pending: {len(pending_items)} files ({human_readable_size(total_pending_size)})")

    if limit_bytes > 0:
        print(f"\n📦 [PROPOSED BATCH] Fits in {limit_arg}:")
        print(f"   Count: {len(selected_items)} files")
        print(f"   Size:  {human_readable_size(current_size)} / {human_readable_size(limit_bytes)}")
        print(f"   Utilization: {(current_size / limit_bytes) * 100:.1f}%")

        # Generate Command Hint
        if selected_items:
            print("\n💡 Tip: To push these, you can run:")
            for item in selected_items:
                print(f"   python main.py push {item['id']}")


def cmd_scan_unprepped():
    # [UPDATED] Scans the separate JSON files and their respective local root folders explicitly
    print("--- SCANNING FOR UNPREPPED FILES ---")

    # Define Categories to Scan — derived from CATEGORY_ROOTS (single source of
    # truth) so every category's folders come from ONE table. One
    # (display, lib_file, folder) triple per (category, subdir): the subdir IS the
    # display label, so Movies/Series/Anime print exactly as before and "other"'s
    # Sports folder prints as "Sports". lib_files is resolved HERE (call time), not
    # at module scope, so the conftest sandbox's monkeypatch of main.LIBRARY_* /
    # mvcommon.LIBRARY_OTHERS is honoured (binding hazard). LIBRARY_OTHERS is
    # mvcommon-only (main never imports it), hence read module-qualified.
    lib_files = {
        "movies": LIBRARY_MOVIES,
        "series": LIBRARY_SERIES,
        "anime":  LIBRARY_ANIME,
        "other":  mvcommon.LIBRARY_OTHERS,
    }
    categories = [
        (subdir, lib_files[cat], os.path.join(LOCAL_ROOT, subdir))
        for cat, subs in CATEGORY_ROOTS.items()
        for subdir in subs
    ]

    total_unprepped = 0

    for cat_name, lib_file, folder_path in categories:
        print(f"\n   > Scanning {cat_name} ({folder_path})...")

        # 1. Build Index of Known Files for this Category
        cat_lib = {}
        if os.path.exists(lib_file):
            try:
                with open(lib_file, 'r') as f:
                    cat_lib = json.load(f)
            except:
                pass

        known_paths = set()
        for entry in cat_lib.values():
            if entry.get("type") in ("season_map", "multi_ep_alias"): continue
            p = os.path.join(entry['folder_path'], entry['filename'])
            known_paths.add(os.path.normpath(p).lower())

        # 2. Walk Local Category Root
        unprepped = []
        if os.path.exists(folder_path):
            for root, dirs, files in os.walk(folder_path):
                # Exclude internal folders
                dirs[:] = [d for d in dirs if
                           d not in [SPLIT_DIR_NAME, CHECKSUM_DIR_NAME, RESTORE_DIR_NAME, ".git", ".idea",
                                     "__pycache__",
                                     "Utils"]]

                for f in files:
                    if f.lower().endswith(VIDEO_EXTENSIONS):
                        # Check for system files and chunks
                        if f.endswith(".temp_dummy"): continue
                        if ".chunk." in f: continue

                        full_path = os.path.join(root, f)
                        norm_path = os.path.normpath(full_path).lower()

                        # Check against known category paths
                        if norm_path not in known_paths:
                            try:
                                size = os.path.getsize(full_path)
                                unprepped.append({'path': full_path, 'size': size})
                            except OSError:
                                pass
        else:
            print(f"     ⚠️ Folder not found: {folder_path}")
            continue

        # 3. Sort and Display Results for Category
        if not unprepped:
            print(f"     ✅ No unprepped video files found for {cat_name}.")
        else:
            unprepped.sort(key=lambda x: x['size'], reverse=True)
            print(f"     ⚠️ Found {len(unprepped)} unprepped video files:")
            print(f"     {'Size':<12} | {'File Path'}")
            print("     " + "-" * 95)
            for item in unprepped:
                size_str = human_readable_size(item['size'])
                print(f"     {size_str:<12} | {item['path']}")
            total_unprepped += len(unprepped)

    # Final Overall Summary
    if total_unprepped > 0:
        print(f"\n⚠️ Total unprepped files across all categories: {total_unprepped}")
        print("💡 Tip: Use 'prep [id] [path]' or 'prep_season' to add them to your library.")
    else:
        print("\n✅ All libraries are completely in sync.")


def cmd_prep_push_rep(manual_id, filepath, split_method=None, split_val=None, device_id=None, eager_rehash=False, temp_dir=None):
    print(f"=== 🚀 AUTO-PILOT: PREP -> PUSH -> REPLACE for {manual_id} ===")

    # 1. PREP
    print("\n>>> STEP 1: PREP")
    if not cmd_prep(manual_id, filepath):
        print("❌ Auto-Pilot Aborted: Prep failed.")
        return

    # 2. PUSH
    print("\n>>> STEP 2: PUSH")
    # [ROLLBACK C] The ad-hoc cleanup block here (manual _parts rmtree + the old
    # revert-temp-files / push-manually messages) is REMOVED. The wrapped cmd_push
    # now owns failure handling via its on-disk journal: a pre-upload failure rolls
    # back this-run artifacts; a post-upload failure leaves the partial upload and
    # prints its own O-1 resume-message (`push <id>`). No second rollback mechanism
    # remains here.
    # We pass None for chunk_range as this atomic command implies full push
    if not cmd_push(manual_id, split_method, split_val, device_id=device_id, eager_rehash=eager_rehash, temp_dir=temp_dir):
        print("\n⚠️ Auto-Pilot Paused: Push did not complete (see the resume hint above).")
        return

    # 3. REPLACE
    print("\n>>> STEP 3: REPLACE")
    try:
        if not cmd_replace(manual_id):
            print("\n⚠️ Auto-Pilot Finished with Warning: Replace failed.")
            print("   > File is uploaded but still takes space locally.")
            print("   > Run 'replace' manually to archive.")
            return
    except RollbackHardFail as hf:
        print(f"\n❌ Auto-Pilot Stopped: {hf.state} — {hf.reason}")
        print(f"   > To recover: {hf.resume_cmd}")
        return

    print("\n✅✅✅ AUTO-PILOT COMPLETE: Movie is safely archived.")


def cmd_prep_push_rep_season(base_id, folder_path, split_method=None, split_val=None, episode_range=None, device_id=None, eager_rehash=False, temp_dir=None):
    # [NEW] TV SERIES SEQUENTIAL AUTO-PILOT
    print(f"=== 📺 SEASON AUTO-PILOT (SEQUENTIAL): PREP -> PUSH -> REPLACE for {base_id} ===")

    # 1. PREP SEASON
    print("\n>>> STEP 1: PREP SEASON")
    cmd_prep_season(base_id, folder_path)

    # 2. IDENTIFY EPISODES TO PROCESS
    library = load_library()
    if base_id not in library:
        print("❌ Prep failed. Base ID not found.")
        return

    target_ids = library[base_id]["children"]

    # Filter by range if needed [UPDATED to handle .5]
    if episode_range:
        try:
            start, end = map(float, episode_range.split('-'))
            filtered_ids = []
            for mid in target_ids:
                ep_num = episode_num_from_id(mid, base_id)
                if ep_num is not None and start <= ep_num <= end:
                    filtered_ids.append(mid)
            target_ids = filtered_ids
            print(f"   > Filtered to {len(target_ids)} episodes ({episode_range})")
        except ValueError:
            print("❌ Invalid range.")
            return

    # De-alias: collapse multi_ep_alias ids to their primaries; dedup order-preserving.
    seen = set()
    dealiased = []
    for mid in target_ids:
        real_id, _ = _resolve_alias(library, mid)
        if real_id not in seen:
            seen.add(real_id)
            dealiased.append(real_id)
    target_ids = dealiased

    # 3. LOOP PROCESS: PUSH -> REPLACE (One by One)
    print(f"\n>>> STEP 2 & 3: SEQUENTIAL PROCESSING ({len(target_ids)} items)")

    def _season_resume_cmd(failing_idx):
        """[ROLLBACK C] Reconstruct the resume command from the failing episode to
        the end of the (range-filtered) target_ids. Reproduces split_method/
        split_val/device_id/episode_range; handles .5 episodes. Messaging only
        (C1 not merged → no progress file)."""
        remaining = target_ids[failing_idx:]
        ep_nums = []
        for rid in remaining:
            real_id, _ = _resolve_alias(library, rid)
            ep = episode_num_from_id(real_id, base_id)
            if ep is not None:
                # Preserve original digit string (e.g. "02", "16.5") for the resume command.
                # Strip base_id prefix, then strip optional e/E/x/X separator — no regex needed.
                ep_str = real_id[len(base_id):] if real_id.startswith(base_id) else real_id
                ep_digits = ep_str[1:] if ep_str and ep_str[0].lower() in ('e', 'x') else ep_str
                ep_nums.append(ep_digits)
        parts = [f"prep_push_rep_season {base_id} \"{folder_path}\""]
        if split_method and split_val:
            parts.append(f"{split_method} {split_val}")
        if ep_nums:
            parts.append(f"episodes {ep_nums[0]}-{ep_nums[-1]}")
        if device_id:
            parts.append(f"device {device_id}")
        return " ".join(parts)

    # [SPLIT-HASH] HARD DISK PRE-FLIGHT (Step 4). Episodes run SEQUENTIALLY with
    # per-item _parts cleanup, so the PEAK disk use is the LARGEST single episode
    # that will split, NOT the sum. Find that worst episode and check it ONCE
    # against the season folder volume BEFORE processing ANY episode — read-only,
    # pre-any-creation, nothing to roll back. Already-uploaded episodes won't
    # push/split, so they're skipped. (Each cmd_push still guards itself as
    # defense-in-depth; this is the "don't even start" early failure.)
    max_req = 0
    worst_mid = None
    worst_size = 0
    for mid in target_ids:
        if library[mid].get("uploaded") == True:
            continue
        f = os.path.join(library[mid]["folder_path"], library[mid]["filename"])
        if not os.path.exists(f):
            continue
        fsize = os.path.getsize(f)
        ws = _will_split(fsize, split_method, split_val)
        req = _required_extra_bytes(fsize, ws, eager_rehash)
        if req > max_req:
            max_req = req
            worst_mid = mid
            worst_size = fsize
    if max_req > 0:
        buffer = _disk_buffer(max_req)
        # [SPLIT-HASH] Step 5: when redirecting chunks to temp_dir, the peak load
        # lands on the temp volume, so stat THAT (validate it once like cmd_push
        # does). temp_dir=None ⇒ check_dir == folder_path, unchanged from today.
        check_dir = folder_path
        if temp_dir:
            _probe_base, _tmperr = _parts_base(folder_path, temp_dir, "_probe")
            if _tmperr:
                print(f"\n❌ {_tmperr}")
                return
            check_dir = temp_dir
        try:
            free = shutil.disk_usage(check_dir).free
        except Exception:
            free = -1
        if free < max_req + buffer:
            print(f"\n❌ Not enough free space to process this season.")
            print(f"   Largest splitting episode: {worst_mid} ({human_readable_size(worst_size)}).")
            print(f"   Need ~{human_readable_size(max_req + buffer)} free in {check_dir} "
                  f"({'chunks + merge temp' if eager_rehash else 'chunks'} + buffer); "
                  f"only {human_readable_size(free)} available.")
            print("   Free up space, or pass a temp dir on another volume.")
            if eager_rehash:
                print("   (Or drop the `rehash` token to halve the need — deferred re-hash uses 1X, not 2X.)")
            return

    for idx, mid in enumerate(target_ids):
        entry = library[mid]
        if entry.get("uploaded") == True:
            # If already uploaded, just ensure replace runs
            print(f"\n[SKIP PUSH] {mid} is already uploaded. Checking Replace...")
            try:
                cmd_replace(mid)
            except RollbackHardFail as hf:
                print(f"❌ {mid}: {hf.state} — {hf.reason}")
                print(f"   > To recover this item: {hf.resume_cmd}")
                print(f"   > Resume the rest of the season: {_season_resume_cmd(idx)}")
                return
            continue

        print(f"\n---------------------------------------------------")
        print(f"⏩ PROCESSING: {mid}")
        print(f"---------------------------------------------------")

        # Use existing single-item logic (Re-using logic from prep_push_rep)
        path = os.path.join(entry['folder_path'], entry['filename'])

        # [ROLLBACK C] The old bare-break stop-the-season behavior is REPLACED.
        # Completed episodes stay; the in-flight item has already rolled itself
        # back (reversible) or hard-failed (irreversible) inside the wrapped
        # commands. On any stop we print the exact resume range from this episode.
        # We skip calling 'cmd_prep' again because we already did prep_season
        # Just call Push then Replace
        if cmd_push(mid, split_method, split_val, device_id=device_id, eager_rehash=eager_rehash, temp_dir=temp_dir):
            try:
                cmd_replace(mid)
            except RollbackHardFail as hf:
                print(f"\n❌ {mid}: {hf.state} — {hf.reason}")
                print(f"   > To recover this item: {hf.resume_cmd}")
                print(f"   > Resume the rest of the season: {_season_resume_cmd(idx)}")
                return
        else:
            print(f"\n⚠️ Stopping season auto-pilot at {mid} (push incomplete).")
            print(f"   > Completed episodes are intact.")
            print(f"   > Resume the rest of the season: {_season_resume_cmd(idx)}")
            return

    print("\n✅✅✅ SEASON AUTO-PILOT COMPLETE.")


def cmd_dispatch_fetch(manual_id, episode_range=None):
    # This keeps main.py clean but still lets you run "main.py fetch"
    cmd = ["python", MAINFETCH_SCRIPT, "fetch", manual_id]

    if episode_range:
        cmd.append("episodes")
        cmd.append(episode_range)
        print(f"   > 🚀 Dispatching Batch Fetch for episodes {episode_range}...")
    else:
        print(f"   > 🚀 Dispatching Fetch...")

    try:
        # Force the child's stdio to UTF-8 — a PIPEd child defaults to cp1252 on Windows and would crash printing mainfetch's emoji.
        child_env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
        # Stream the child's combined stdout/stderr line-by-line through the
        # CURRENT sys.stdout. A blocking run would inherit the OS-level fd and
        # bypass any in-process redirect_stdout (used by the web worker to tee
        # progress), so we PIPE + re-print each line via print() instead. This
        # keeps the live CLI terminal updated AND lets the worker's capture see
        # real download progress (e.g. "Detected Split File", "✅ MOVED").
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            encoding="utf-8",
            errors="replace",
            env=child_env,
        )
        try:
            for line in proc.stdout:
                # line already carries its newline; flush so the worker's tee
                # republishes promptly.
                print(line, end="", flush=True)
        finally:
            proc.wait()
    except Exception as e:
        print(f"❌ Error running fetch script: {e}")


def cmd_fetch_restore(manual_id, episode_range=None):
    # [NEW] Automated Fetch -> Restore Pipeline
    print(f"=== 🔄 AUTO-PILOT: FETCH -> RESTORE for {manual_id} ===")

    # 1. FETCH
    cmd_dispatch_fetch(manual_id, episode_range)

    # 2. DETECT & RESTORE
    print("\n>>> STARTING RESTORE PHASE...")
    library = load_library()
    if manual_id not in library:
        print("❌ ID not found in library. Cannot restore.")
        return

    entry = library[manual_id]

    is_season_map = entry.get("type") == "season_map"
    restored_count = None
    if is_season_map:
        # [UPDATED] Pass the range to restore_group
        print(f"   > Season Map detected. Running Batch Restore...")
        restored_count = cmd_restore_group(manual_id, episode_range)
    else:
        print(f"   > Single Item detected. Running Restore...")
        cmd_restore(manual_id)

    # [IMP-C18] Don't lie with a green banner over zero work: when a range was
    # supplied to a season_map and 0 items were restored (range selected nothing),
    # suppress the ✅✅✅ banner and print a ⚠️ summary instead. Exit code unchanged
    # (this function returns None throughout) — the run still "succeeds", it just
    # reports the truth. Single-item / no-range runs keep the original banner.
    if is_season_map and episode_range and restored_count == 0:
        print(f"\n⚠️ FETCH & RESTORE finished with 0 items "
              f"(range {episode_range} selected nothing).")
    else:
        print("\n✅✅✅ FETCH & RESTORE COMPLETE.")


# ==========================================
#      WEB CONSOLE READ-ONLY DATA LAYER
# ==========================================
# Five module-level PURE / READ-ONLY helpers behind the `web` operations
# console (IMP-E12). They classify what occupies local disk and what the user
# should run next to reclaim it. NONE of them mutate the library JSON or touch
# any media file — they only read the library (once, via load_library) and
# os.stat the on-disk files. The disk is the source of truth for "occupies
# space": a real file (size >= DUMMY_MAX_BYTES) is reclaimable; a dummy
# (size < DUMMY_MAX_BYTES) or absent file is not.
#
# CRASH-CLASS NOTE (IMP-C12 / PR #21): every library iteration below skips
# non-physical entry types ("season_map", "multi_ep_alias") BEFORE touching
# folder_path/filename — those keys do not exist on virtual rows.

# Statuses whose on-disk original (if still real) still occupies reclaimable
# space, mapped to the badge collect_reclaimable emits for a real file.
_RECLAIMABLE_STATUS_BADGE = {
    "local_ready":    "LOCAL_NOT_PUSHED",
    "onboarded":      "PUSHED_NOT_ARCHIVED",
    "restored_local": "RESTORED_REPLACE_AGAIN",
}

# Disk-walk exclusions — identical set to cmd_scan_unprepped (main.py:2613).
_RECLAIM_EXCLUDE_DIRS = [SPLIT_DIR_NAME, CHECKSUM_DIR_NAME, RESTORE_DIR_NAME,
                         ".git", ".idea", "__pycache__", "Utils"]

# Release-noise tokens stripped from a filename when guessing a manual id.
# Lowercase; a whole hyphen/space-delimited token equal to one of these (or a
# resolution / explicit season-episode token, handled separately) is dropped.
_RELEASE_NOISE_TOKENS = {
    "bluray", "blu", "ray", "brrip", "bdrip", "webrip", "web", "webdl",
    "hdrip", "dvdrip", "hdtv", "remux", "x264", "x265", "h264", "h265",
    "hevc", "avc", "xvid", "divx", "aac", "ac3", "dts", "ddp", "dd",
    "atmos", "truehd", "flac", "hdr", "hdr10", "sdr", "dv", "dolby",
    "vision", "10bit", "8bit", "yify", "yts", "rarbg", "psa", "ettv",
    "extended", "remastered", "proper", "repack", "internal", "limited",
    "uncut", "complete", "multi", "dual", "audio", "subbed", "dubbed",
    "esub", "esubs", "msubs",
}


def classify_entry_state(entry, on_disk_real):
    """Read-only classifier → reclaim badge for one (entry, on-disk-real) pair.

    Returns one of "UNPREPPED" | "LOCAL_NOT_PUSHED" | "PUSHED_NOT_ARCHIVED" |
    "RESTORED_REPLACE_AGAIN" | "ARCHIVED" | None.

    - entry is None  -> the on-disk file is unknown to the library -> "UNPREPPED".
    - Non-physical alias rows ("season_map"/"multi_ep_alias") own no reclaimable
      file -> None.
    - on_disk_real is True only when the file on disk is a real original
      (size >= DUMMY_MAX_BYTES). A dummy/absent file is never a reclaimable
      badge: archived-status -> "ARCHIVED" (excluded from items by the caller);
      anything else -> None.
    - The disk is the source of truth: a status that *should* still hold a
      real file but whose file is already a dummy returns None, not a badge.
    """
    if entry is None:
        return "UNPREPPED"

    if entry.get("type") in ("season_map", "multi_ep_alias"):
        return None

    status = entry.get("status")

    if not on_disk_real:
        # File is a dummy or absent: nothing reclaimable. Surface ARCHIVED only
        # so the caller can recognise (and exclude) it; everything else is None.
        return "ARCHIVED" if status == "archived" else None

    # File on disk is real -> reclaimable iff status says it is still a working
    # copy that has somewhere safe to be reclaimed to. A real file under an
    # "archived" status is an out-of-sync anomaly (the dummy was overwritten by
    # a real file) — not a clean reclaim, so it gets no badge.
    return _RECLAIMABLE_STATUS_BADGE.get(status)


def guess_manual_id(path):
    """Best-effort EDITABLE manual-id guess from a file path. NEVER raises.

    Produces a plausible canonical-shape id the user will edit before prepping
    (see ARCHITECTURE.md §6.2): mov-<lang2>-<year>-<slug>,
    tv-<lang2>-<year>-<slug>-sNNeMM, ani-<lang2>-<year>-<slug><EE>. A wrong
    guess is fine — this string is only ever an editable placeholder, never
    auto-prepped. Category (mov/tv/ani) is inferred from which root the path is
    under; default language is "en"; the year uses the same 4-digit-token rule
    as parse_metadata_from_id (main.py:178).
    """
    try:
        norm = os.path.normpath(path)
        low = norm.lower()
        sep = os.sep.lower()

        # Category prefix from the root the path lives under.
        if (sep + "series" + sep) in low or low.endswith(sep + "series"):
            prefix = "tv"
        elif (sep + "anime" + sep) in low or low.endswith(sep + "anime"):
            prefix = "ani"
        else:
            prefix = "mov"  # default / Movies

        base = os.path.basename(norm)
        stem = os.path.splitext(base)[0]
        if not stem:
            stem = base

        # Tokenise on any non-alphanumeric run; everything lowercased to ascii.
        raw_tokens = re.findall(r"[A-Za-z0-9]+", stem.lower())

        # Year: first standalone 4-digit token (matches parse_metadata_from_id).
        year = None
        for tok in raw_tokens:
            if tok.isdigit() and len(tok) == 4:
                year = tok
                break

        # Season/episode markers (best-effort, for tv/ani only).
        season = None
        episode = None
        m_se = re.search(r"s(\d{1,2})[ ._-]*e(\d{1,3})", stem.lower())
        if m_se:
            season, episode = m_se.group(1), m_se.group(2)
        else:
            m_x = re.search(r"(\d{1,2})x(\d{1,3})", stem.lower())
            if m_x:
                season, episode = m_x.group(1), m_x.group(2)

        # Build the title slug: drop year, release-noise, resolution and
        # season/episode tokens. A token is dropped if it is:
        #   - the year, or a "<digits>p" resolution token (e.g. 2160p/1080p),
        #   - a known release-noise word,
        #   - an sNNeMM / NNxMM marker.
        ep_token = None  # a bare numeric token we treat as the anime episode
        slug_parts = []
        for tok in raw_tokens:
            if tok == year:
                continue
            if re.fullmatch(r"\d{3,4}p", tok):  # resolution token
                continue
            if tok in _RELEASE_NOISE_TOKENS:
                continue
            if re.fullmatch(r"s\d{1,2}e\d{1,3}", tok):
                continue
            if re.fullmatch(r"\d{1,2}x\d{1,3}", tok):
                continue
            slug_parts.append(tok)

        # Anime episode: if no sNNeMM/NNxMM was found, treat a single trailing
        # bare-numeric slug token (1-3 digits) as the episode number and lift it
        # out of the slug (canonical anime shape appends it back as <EE>).
        if prefix == "ani" and episode is None and slug_parts:
            if re.fullmatch(r"\d{1,3}", slug_parts[-1]):
                ep_token = slug_parts[-1]
                episode = ep_token
                slug_parts = slug_parts[:-1]

        slug = "".join(slug_parts) if slug_parts else "untitled"

        yr = year if year else "0000"

        if prefix == "tv":
            if season and episode:
                return f"tv-en-{yr}-{slug}-s{int(season):02d}e{int(episode):02d}"
            return f"tv-en-{yr}-{slug}"
        if prefix == "ani":
            if episode:
                return f"ani-en-{yr}-{slug}{int(episode):02d}"
            return f"ani-en-{yr}-{slug}"
        return f"mov-en-{yr}-{slug}"
    except Exception:
        # The contract is "never raise" — fall back to a generic placeholder.
        return "mov-en-0000-untitled"


def suggest_target_folder(item):
    """Suggested destination folder for a reclaim item.

    Returns {folder, provider_tag, editable_provider_field, applies}.

    For an IN-LIBRARY item (existing leaf), folders are NEVER renamed: returns
    the entry's existing folder_path with applies=False (informational only).
    For a NEW (UNPREPPED) item, builds a leaf-folder name from the guessed
    Title/Year plus an EDITABLE provider-id placeholder per the provider-tag
    template (Movies -> {tmdb-…}, Series/Anime -> {tvdb-…}). This step does NO
    TMDB/TVDB lookup; the braces hold an editable placeholder.
    """
    entry = item.get("entry")
    if entry is not None:
        # Existing folder — informational, never renamed.
        return {
            "folder": entry.get("folder_path"),
            "provider_tag": None,
            "editable_provider_field": None,
            "applies": False,
        }

    # NEW item: derive category + Title/Year from the guessed id.
    mid = item.get("id") or guess_manual_id(item.get("path", ""))
    parts = mid.split("-")
    category = parts[0] if parts else "mov"

    year = None
    for part in parts:
        if part.isdigit() and len(part) == 4:
            year = part
            break

    # Title = the slug segment (index 3 in <cat>-<lang>-<year>-<slug>[-sNNeMM]),
    # title-cased for a human folder name. Fall back to the last segment for a
    # non-canonical id, and strip a trailing season/episode marker either way.
    slug = parts[3] if len(parts) > 3 else (parts[-1] if parts else mid)
    slug_title = re.sub(r"s\d{2}e\d{2}$", "", slug)   # tv glued marker
    if category == "ani":
        slug_title = re.sub(r"\d+$", "", slug_title)   # anime trailing <EE>
    title = slug_title.replace("_", " ").strip().title() or "Untitled"

    year_disp = f"({year})" if year else "(Year)"

    if category == "mov":
        provider_tag = "{tmdb-0000000}"
        provider_field = "tmdb"
    else:  # tv / ani -> series-style
        provider_tag = "{tvdb-000000}"
        provider_field = "tvdb"

    folder = f"{title} {year_disp} {provider_tag}"
    return {
        "folder": folder,
        "provider_tag": provider_tag,
        "editable_provider_field": provider_field,
        "applies": True,
    }


def suggest_next_command(item):
    """The EXACT command string to reclaim this item (State table, IMP-E12)."""
    badge = item.get("badge")
    mid = item.get("id", "")
    path = item.get("path", "")

    if badge == "UNPREPPED":
        return f'python main.py prep {mid} "{path}"'
    if badge == "LOCAL_NOT_PUSHED":
        return f"python main.py push {mid} SIZE_GB 8"
    if badge in ("PUSHED_NOT_ARCHIVED", "RESTORED_REPLACE_AGAIN"):
        return f"python main.py replace {mid}"
    # ARCHIVED / unknown -> no reclaim action.
    return ""


def category_of_id(mid):
    """Map a library id prefix to a media category (IMP-E14).

    Mirrors webui/server.py:_category_of exactly so the SPA's media-type tabs
    and the server's per-category summary agree on one bucketing rule:
    mov->movies, tv->series, ani->anime, everything else->other.
    """
    if mid.startswith("mov"):
        return "movies"
    if mid.startswith("tv"):
        return "series"
    if mid.startswith("ani"):
        return "anime"
    return "other"


def _classify_item(mid, entry):
    """Single-source-of-truth per-leaf classifier shared by the reclaim scan and
    the media-type inventory (IMP-E14). Strictly READ-ONLY.

    Given one (id, library-entry) pair, returns the facts BOTH /api/reclaim
    (collect_reclaimable) and /api/items (items_payload) need for a single
    PHYSICAL library leaf, or None when the entry owns no physical leaf:

      - None  -> a non-physical/virtual row (type season_map / multi_ep_alias)
                 or an entry missing folder_path/filename. The CRASH-CLASS guard
                 (IMP-C12 / PR #21): this skip happens BEFORE folder_path/filename
                 are dereferenced, so virtual rows never raise and never emit.
      - dict  -> {
            "id":           mid,
            "entry":        entry,          # passthrough for the caller's row builder
            "path":         os.path.join(folder_path, filename),
            "norm_key":     os.path.normpath(path).lower(),   # de-dupe key
            "file_present": bool,           # False when os.stat raised (absent/unreadable)
            "size_bytes":   int,            # 0 when file_present is False
            "on_disk_real": bool,           # size >= DUMMY_MAX_BYTES (False if absent)
            "state":        classify_entry_state(entry, on_disk_real),  # badge | "ARCHIVED" | None
        }

    The disk is the source of truth for state (classify_entry_state semantics):
    a real file is reclaimable per its status; a dummy/absent file is not. The
    caller decides inclusion policy — collect_reclaimable drops file_present=False
    and ARCHIVED/None states; items_payload keeps every physical leaf including
    ARCHIVED and absent-file rows.
    """
    if entry.get("type") in ("season_map", "multi_ep_alias"):
        return None
    fp = entry.get("folder_path")
    fn = entry.get("filename")
    if not fp or not fn:
        return None

    path = os.path.join(fp, fn)
    norm_key = os.path.normpath(path).lower()
    try:
        size = os.path.getsize(path)
        file_present = True
    except OSError:
        size = 0
        file_present = False
    on_disk_real = file_present and size >= DUMMY_MAX_BYTES
    return {
        "id": mid,
        "entry": entry,
        "path": path,
        "norm_key": norm_key,
        "file_present": file_present,
        "size_bytes": size,
        "on_disk_real": on_disk_real,
        "state": classify_entry_state(entry, on_disk_real),
    }


def collect_reclaimable():
    """Read-only scan of what occupies reclaimable local disk (IMP-E12).

    Loads the library once, walks the three category roots (same exclusions as
    cmd_scan_unprepped), and classifies every on-disk video as UNPREPPED /
    LOCAL_NOT_PUSHED / PUSHED_NOT_ARCHIVED / RESTORED_REPLACE_AGAIN. A second
    targeted pass over the library's physical leaves catches reclaimable
    entries whose on-disk file is still real but were not already produced by
    the walk. De-duped by normpath-lower so a library leaf and its on-disk file
    yield exactly ONE row.

    Returns {"items": [...], "total_reclaimable_bytes": N,
             "total_reclaimable_human": "..."}. Strictly READ-ONLY.
    """
    library = load_library()

    # Index physical leaves once: normpath-lower(folder_path/filename) -> (id, entry).
    # Non-physical types are skipped (no folder_path/filename) — IMP-C12 guard.
    known_paths = {}
    for mid, entry in library.items():
        if entry.get("type") in ("season_map", "multi_ep_alias"):
            continue
        fp = entry.get("folder_path")
        fn = entry.get("filename")
        if not fp or not fn:
            continue
        key = os.path.normpath(os.path.join(fp, fn)).lower()
        known_paths[key] = (mid, entry)

    items = []
    seen = set()  # normpath-lower keys already emitted — single anti-double-count source.

    def _add_item(norm_key, mid, entry, badge, path, size):
        if norm_key in seen:
            return
        seen.add(norm_key)
        guessed = entry is None  # only UNPREPPED ids are guessed
        work = {
            "id": mid,
            "badge": badge,
            "path": path,
            "size_bytes": size,
            "entry": entry,  # internal — used by suggest_* below, dropped from the row
        }
        row = {
            "id": mid,
            "badge": badge,
            "path": path,
            "size_bytes": size,
            "suggested_command": suggest_next_command(work),
            "suggested_folder": suggest_target_folder(work),
            "guessed": guessed,
        }
        items.append(row)

    # ---- PASS 1: disk-first walk of every category root ----
    # Roots derived from CATEGORY_ROOTS (single source of truth) — includes the
    # Others folder(s) (Sports) so UNPREPPED oth- files surface here. (PREPPED oth-
    # leaves already come through PASS 2 below via load_library.)
    categories = [os.path.join(LOCAL_ROOT, d) for subs in CATEGORY_ROOTS.values() for d in subs]
    for folder_path in categories:
        if not os.path.exists(folder_path):
            # Degrade gracefully like cmd_scan_unprepped (warn + continue).
            print(f"⚠️ Folder not found: {folder_path}")
            continue
        for root, dirs, files in os.walk(folder_path):
            dirs[:] = [d for d in dirs if d not in _RECLAIM_EXCLUDE_DIRS]
            for f in files:
                if not f.lower().endswith(VIDEO_EXTENSIONS):
                    continue
                if f.endswith(".temp_dummy"):
                    continue
                if ".chunk." in f:
                    continue
                full_path = os.path.join(root, f)
                norm_key = os.path.normpath(full_path).lower()
                try:
                    size = os.path.getsize(full_path)
                except OSError:
                    continue
                on_disk_real = size >= DUMMY_MAX_BYTES

                found = known_paths.get(norm_key)
                if found is None:
                    # Unknown to the library. classify_entry_state(None, ...) is
                    # always "UNPREPPED", but per the State table UNPREPPED is a
                    # RECLAIMABLE row only when the file is real — a dummy/tiny
                    # unknown file occupies no reclaimable space, so skip it.
                    if on_disk_real:
                        _add_item(norm_key, guess_manual_id(full_path), None,
                                  "UNPREPPED", full_path, size)
                else:
                    mid, entry = found
                    badge = classify_entry_state(entry, on_disk_real)
                    if badge and badge != "ARCHIVED":
                        _add_item(norm_key, mid, entry, badge, full_path, size)

    # ---- PASS 2: library physical leaves whose file is still real ----
    # Catches reclaimable entries (PUSHED_NOT_ARCHIVED / RESTORED_REPLACE_AGAIN /
    # LOCAL_NOT_PUSHED) not already emitted from the walk (e.g. a category root
    # absent above, or a path the walk skipped). De-duped via `seen`.
    for mid, entry in library.items():
        # Fast early-out on non-reclaimable statuses (archived/unknown/virtual):
        # cheaper than stat-ing, and keeps this pass focused on the three
        # reclaimable statuses. _classify_item below re-derives the same skip for
        # virtual types, so this is a pure optimisation, not a behaviour change.
        if entry.get("status") not in _RECLAIMABLE_STATUS_BADGE:
            continue
        info = _classify_item(mid, entry)  # shared single-source-of-truth classifier
        if info is None or not info["file_present"]:
            continue  # virtual/no physical leaf, or absent on disk -> nothing to reclaim
        if info["norm_key"] in seen:
            continue
        badge = info["state"]
        if badge and badge != "ARCHIVED":
            _add_item(info["norm_key"], mid, entry, badge, info["path"], info["size_bytes"])

    total_bytes = sum(it["size_bytes"] for it in items)
    return {
        "items": items,
        "total_reclaimable_bytes": total_bytes,
        "total_reclaimable_human": human_readable_size(total_bytes),
    }


# Tech-spec values that carry no signal and must be dropped from the compact UI
# `tech` dict (case-insensitive). MediaInfo writes "Unknown" for un-probed tracks
# and "SDR" for a plain non-HDR video; neither is worth a chip on the tile.
_TECH_EMPTY_VALUES = {"unknown", "sdr", "none", ""}


def _is_tech_empty(value):
    """True when a tech_spec value carries no UI signal (None / "" / "Unknown" /
    "SDR" / "None", case-insensitively). Numbers (e.g. audio_channels, duration)
    are always kept."""
    if value is None:
        return True
    if isinstance(value, str):
        return value.strip().lower() in _TECH_EMPTY_VALUES
    return False


def _normalize_hdr(hdr):
    """Collapse a verbose MediaInfo hdr_format to a short UI label.

    MediaInfo writes long forms like "Dolby Vision / SMPTE ST 2086" or
    "SMPTE ST 2086, HDR10 compatible"; the tile wants a single short token. We
    prefer "Dolby Vision" when present, else "HDR10"/"HDR10+"/"HLG" when the
    string names them, else the first slash/comma-delimited segment trimmed.
    Returns None for an empty/SDR value (so the chip is simply omitted)."""
    if _is_tech_empty(hdr):
        return None
    s = str(hdr).strip()
    low = s.lower()
    if "dolby vision" in low or "dovi" in low:
        return "Dolby Vision"
    if "hdr10+" in low or "hdr10 plus" in low:
        return "HDR10+"
    if "hdr10" in low:
        return "HDR10"
    if "hlg" in low:
        return "HLG"
    # Fall back to the first delimited segment (drops the "/ SMPTE ST 2086" tail).
    head = re.split(r"[\/,]", s, 1)[0].strip()
    return head or None


def _compact_tech(tech_spec):
    """Build the compact, UI-facing `tech` dict from a leaf's stored tech_spec.

    Read-only + None-safe: takes the raw tech_spec dict (or None) and returns a
    dict with ONLY the fields the tile/dossier renders — {resolution, hdr,
    video_codec, audio, audio_channels, duration_mins} — omitting any value that
    is empty/"Unknown"/"SDR" (via _is_tech_empty) and normalizing hdr to a short
    label. Returns None when nothing meaningful survives, so the row carries a
    clean null rather than an empty dict."""
    if not isinstance(tech_spec, dict):
        return None
    out = {}
    # String fields: keep only when they carry signal.
    for key in ("resolution", "video_codec", "audio"):
        val = tech_spec.get(key)
        if not _is_tech_empty(val):
            out[key] = val
    hdr = _normalize_hdr(tech_spec.get("hdr"))
    if hdr is not None:
        out["hdr"] = hdr
    # audio_channels: an int when probed, "Unknown" otherwise — keep ints only.
    ch = tech_spec.get("audio_channels")
    if isinstance(ch, int):
        out["audio_channels"] = ch
    # duration_mins: a positive int is meaningful; 0/Unknown is not.
    dur = tech_spec.get("duration_mins")
    if isinstance(dur, int) and dur > 0:
        out["duration_mins"] = dur
    return out or None


def items_payload():
    """Read-only inventory of EVERY physical library leaf for the media-type UI
    (IMP-E14). Strictly READ-ONLY — loads the library once via load_library() and
    only os.stat()s files; never mutates, saves, or touches media.

    Unlike collect_reclaimable (which is disk-anchored and deliberately drops
    ARCHIVED rows), this is LIBRARY-anchored and INCLUDES archived items — the
    media-type tabs must show the full picture, including what's already cold.

    Built on the same _classify_item() core collect_reclaimable's library pass
    uses, so the lifecycle `state` can never drift between /api/items and
    /api/reclaim. Virtual rows (season_map / multi_ep_alias) are skipped by
    _classify_item BEFORE folder_path/filename are dereferenced (PR #21 crash
    class) and are never emitted. De-duped by normpath-lower so a leaf surfaces
    exactly once even if two ids point at the same file.

    Returns {
        "items": [ {
            "id", "category", "state", "size_bytes", "path",
            "title", "year", "tmdb_id", "poster_available", "backdrop_available",
            "overview", "episode_title", "chunk_count",
            "actual_size_bytes", "tech", "release_name",
            "parent_id"  # only when the entry carries one
        }, ... ],
        "by_category": {"movies": N, "series": N, "anime": N, "other": N},
    }

    overview / episode_title come from the entry's metadata (enrich-written TMDB
    synopsis + episode name; null when absent); backdrop_available is a LIVE on-disk
    fanart check (the SAME resolver /api/media-image uses) so the hover detail-window
    requests a backdrop only when one will be served. All three are read-only.

    actual_size_bytes / tech / release_name surface the REAL fetched version info
    stored in the library (IMP-E16 B1) so an ARCHIVED tile — whose on-disk file is a
    tiny dummy (size_bytes ~= a few hundred bytes) — can still show the true file
    size + print under the title. They are read straight off the entry, .get()-guarded
    and null when absent:
      * actual_size_bytes = entry.tech_spec.size_bytes (the real fetched byte size).
      * tech              = a compact dict {resolution, hdr, video_codec, audio,
                            audio_channels, duration_mins} from tech_spec, dropping
                            "Unknown"/"SDR"/empty values and normalizing hdr to a
                            short label (e.g. "Dolby Vision / SMPTE ST 2086" ->
                            "Dolby Vision"); null when nothing meaningful survives.
      * release_name      = entry.filename (the full release name, carrying iMAX /
                            REMUX / DV-profile / source tokens the UI parses).
    size_bytes stays the ON-DISK size (the dummy's), so the tile can still flag a
    big local file; actual_size_bytes is the archived/real size shown beneath.

    state is the shared classify_entry_state badge when one applies
    (LOCAL_NOT_PUSHED / PUSHED_NOT_ARCHIVED / RESTORED_REPLACE_AGAIN / ARCHIVED);
    for a physical leaf whose disk state matches no reclaim badge (e.g. a
    local_ready entry whose on-disk file is already a dummy), it falls back to
    the entry's library status upper-cased so every row carries a non-null,
    meaningful lifecycle string. (UNPREPPED is reserved for unknown-to-library
    files, which this library-anchored builder never produces.)
    """
    library = load_library()

    items = []
    seen = set()  # normpath-lower keys already emitted — single de-dupe source.
    by_category = {"movies": 0, "series": 0, "anime": 0, "other": 0}

    for mid, entry in library.items():
        info = _classify_item(mid, entry)  # shared single-source-of-truth classifier
        if info is None:
            continue  # virtual row / no physical leaf — never emit a virtual row
        if info["norm_key"] in seen:
            continue
        seen.add(info["norm_key"])

        # state: shared badge if any, else the raw library status upper-cased so
        # the SPA always has a meaningful lifecycle string (never null).
        state = info["state"]
        if state is None:
            status = entry.get("status")
            state = status.upper() if status else "UNKNOWN"

        category = category_of_id(mid)
        metadata = entry.get("metadata") or {}
        # poster_available: a cheap truthy existence check via resolve_artwork_path
        # (Phase 5.7) — the SAME resolver /api/media-image uses, so the SPA only
        # requests a poster <img> when one will actually be served (no speculative
        # 404 per card). It is a few os.path checks per row (own folder -> season
        # folder -> {tmdb-…} ancestor, first existing wins); short-circuit to False
        # when the entry has neither a folder_path nor a parent to inherit from, so
        # a folderless leaf never even enters the resolver on a large grid.
        has_anchor = bool(entry.get("folder_path")) or bool(entry.get("parent_id"))
        poster_available = bool(
            has_anchor and resolve_artwork_path(library, mid, kind="poster")
        )
        # backdrop_available: same cheap, LIVE on-disk check via the SAME resolver
        # the /api/media-image route uses, but for the FANART (backdrop) the hover
        # detail-window shows. fanart resolution walks own folder -> season folder ->
        # {tmdb-…} show folder (it has no per-episode rung), so an episode inherits the
        # season/show backdrop. Gated on has_anchor + short-circuited like the poster
        # check so a folderless leaf never enters the resolver (a couple os.path stats
        # at most). Kept a real bool (JSON-friendly), never a path.
        backdrop_available = bool(
            has_anchor and resolve_artwork_path(library, mid, kind="fanart")
        )
        # Real fetched-version info (IMP-E16 B1) — stored in the library even when
        # the on-disk file is a tiny archived dummy. All .get()-guarded + None-safe.
        tech_spec = entry.get("tech_spec") or {}
        actual_size_bytes = tech_spec.get("size_bytes")
        tech = _compact_tech(tech_spec)
        release_name = entry.get("filename")
        row = {
            "id": mid,
            "category": category,
            "state": state,
            "size_bytes": info["size_bytes"],
            "path": info["path"],
            "title": metadata.get("title") or mid,
            "year": metadata.get("year"),
            "tmdb_id": metadata.get("tmdb_id"),
            "poster_available": poster_available,
            "backdrop_available": backdrop_available,
            "overview": metadata.get("overview"),
            "episode_title": metadata.get("episode_title"),
            "chunk_count": (entry.get("split_info") or {}).get("total_chunks") or 1,
            # The REAL fetched size + compact tech + full release filename (B1).
            "actual_size_bytes": actual_size_bytes,
            "tech": tech,
            "release_name": release_name,
        }
        if entry.get("parent_id") is not None:
            row["parent_id"] = entry["parent_id"]

        items.append(row)
        by_category[category] += 1

    return {"items": items, "by_category": by_category}


# ---------------------------------------------------------------------------
# Rich TMDB dossier for the hover-preview detail window (IMP-E16).
#
# tmdb_detail(library, mid) returns the RICH TMDB fields the SPA's hover dossier
# renders (rating, genres, runtime, tagline, cast, director/creators, IMDb link,
# …) for ONE library entry, or None when the entry has no metadata.tmdb_id.
#
# Design contract (the parallel frontend agent renders this exact dict):
#   * READ-ONLY + alias-safe + NEVER raises. The id ONLY indexes the library; the
#     tmdb_id comes from the STORED entry (metadata.tmdb_id), never the client, so
#     a crafted id is just a missing key -> None. _resolve_alias dereferences a
#     multi_ep_alias to its primary leaf. No library mutation, no media touch.
#   * Kind is derived from the id: `movie` (mov-…), `episode` (a tv-/ani- leaf
#     whose id parses to a season+episode via _episode_se_of), else `tv`
#     (show/season). The SAME tmdb_id is the SHOW id for an episode leaf (enrich
#     stamps the show id on every leaf, including episodes), and season/episode
#     come from the leaf id.
#   * GRACEFUL DEGRADATION: every TMDB call goes through _tmdb_get (on-disk cached
#     under TMDB_CACHE_DIR; returns None on network/non-200/bad-JSON — never
#     raises). The dict is built incrementally from what is computable with NO
#     network (tmdb_id / kind / tmdb_url), then enriched from each call that
#     succeeds. A failed sub-call (credits, external_ids, episode details) simply
#     contributes nothing — the caller still gets a partial dict, never a 500.
#     Because the cache is reused, repeat opens of the same entry are instant.
# ---------------------------------------------------------------------------

# Cast / director list caps (the contract truncates cast to 8, directors to 3).
_TMDB_DETAIL_CAST_MAX = 8
_TMDB_DETAIL_DIRECTORS_MAX = 3


def _tmdb_detail_kind(real_id, entry):
    """The detail `kind` for a (post-alias) library leaf: 'movie' | 'tv' | 'episode'.

    mov-… -> 'movie'. A tv-/ani- leaf whose id parses to a season+episode (via the
    canonical _episode_se_of parser, which understands both the glued `-sNNeMM`
    TV form and the anime parent_id+tail form) -> 'episode'. Anything else (a
    season_map / show-level id, or a leaf with no parseable episode) -> 'tv'."""
    if category_of_id(real_id) == "movies":
        return "movie"
    if _episode_se_of(real_id, entry) is not None:
        return "episode"
    return "tv"


def _tmdb_genre_names(genres):
    """List of genre `name` strings from a TMDB `genres` array, dropping any
    malformed member. [] for a missing/empty/non-list value."""
    out = []
    for g in genres or []:
        if isinstance(g, dict):
            name = g.get("name")
            if name:
                out.append(name)
    return out


def _tmdb_cast_names(credits, limit=_TMDB_DETAIL_CAST_MAX):
    """Top-`limit` cast entries as [{"name","character"}, …] from a TMDB credits
    payload's `cast` (already TMDB-ordered by billing). Skips members with no
    name; tolerates a missing `character`. [] for a missing/empty `cast`."""
    out = []
    for c in (credits or {}).get("cast") or []:
        if not isinstance(c, dict):
            continue
        name = c.get("name")
        if not name:
            continue
        out.append({"name": name, "character": c.get("character") or ""})
        if len(out) >= limit:
            break
    return out


def _tmdb_directors_from_crew(credits, limit=_TMDB_DETAIL_DIRECTORS_MAX):
    """Up-to-`limit` director names from a MOVIE credits payload's `crew`
    (job == 'Director'). Order-preserving, de-duped. [] when none."""
    out = []
    seen = set()
    for c in (credits or {}).get("crew") or []:
        if not isinstance(c, dict):
            continue
        if c.get("job") != "Director":
            continue
        name = c.get("name")
        if not name or name in seen:
            continue
        seen.add(name)
        out.append(name)
        if len(out) >= limit:
            break
    return out


def _tmdb_created_by_names(detail, limit=_TMDB_DETAIL_DIRECTORS_MAX):
    """Up-to-`limit` creator names from a TV detail payload's `created_by`
    ([{name,…}]). Order-preserving, de-duped. [] when none."""
    out = []
    seen = set()
    for c in detail.get("created_by") or []:
        if not isinstance(c, dict):
            continue
        name = c.get("name")
        if not name or name in seen:
            continue
        seen.add(name)
        out.append(name)
        if len(out) >= limit:
            break
    return out


def _tmdb_network_names(networks):
    """List of network `name` strings from a TV detail `networks` array. [] when
    missing/empty/malformed."""
    out = []
    for n in networks or []:
        if isinstance(n, dict):
            name = n.get("name")
            if name:
                out.append(name)
    return out


def _tmdb_detail_movie(out, tmdb_id, api_key):
    """Fill `out` (in place) with MOVIE detail. GET /3/movie/{id} for the core
    fields + /3/movie/{id}/credits for cast + crew Director(s). Each call is
    optional — a None response just leaves those fields unset."""
    detail = _tmdb_get(f"{TMDB_API_ROOT}/movie/{tmdb_id}", {}, api_key)
    if isinstance(detail, dict):
        _set_if(out, "title", detail.get("title"))
        _set_if(out, "year", _tmdb_year_of(detail.get("release_date")))
        _set_if(out, "tagline", detail.get("tagline"))
        _set_if(out, "overview", detail.get("overview"))
        _set_if(out, "rating", detail.get("vote_average"))
        _set_if(out, "vote_count", detail.get("vote_count"))
        _set_if(out, "runtime", detail.get("runtime"))
        _set_if(out, "release_date", detail.get("release_date"))
        _set_if(out, "status", detail.get("status"))
        _set_if(out, "homepage", detail.get("homepage"))
        genres = _tmdb_genre_names(detail.get("genres"))
        if genres:
            out["genres"] = genres
        _set_imdb(out, detail.get("imdb_id"))

    credits = _tmdb_get(f"{TMDB_API_ROOT}/movie/{tmdb_id}/credits", {}, api_key)
    if isinstance(credits, dict):
        cast = _tmdb_cast_names(credits)
        if cast:
            out["cast"] = cast
        directors = _tmdb_directors_from_crew(credits)
        if directors:
            out["directors"] = directors


def _tmdb_detail_tv(out, tmdb_id, api_key):
    """Fill `out` (in place) with TV-SHOW detail. GET /3/tv/{id} (core + show
    extras: seasons/episodes/networks/created_by) + /3/tv/{id}/external_ids (imdb)
    + /3/tv/{id}/credits (cast). Each call is optional."""
    detail = _tmdb_get(f"{TMDB_API_ROOT}/tv/{tmdb_id}", {}, api_key)
    if isinstance(detail, dict):
        _set_if(out, "title", detail.get("name"))
        _set_if(out, "year", _tmdb_year_of(detail.get("first_air_date")))
        _set_if(out, "tagline", detail.get("tagline"))
        _set_if(out, "overview", detail.get("overview"))
        _set_if(out, "rating", detail.get("vote_average"))
        _set_if(out, "vote_count", detail.get("vote_count"))
        # episode_run_time is a list; the first entry is the typical runtime.
        run_times = detail.get("episode_run_time")
        if isinstance(run_times, list) and run_times:
            _set_if(out, "runtime", run_times[0])
        _set_if(out, "release_date", detail.get("first_air_date"))
        _set_if(out, "status", detail.get("status"))
        _set_if(out, "homepage", detail.get("homepage"))
        genres = _tmdb_genre_names(detail.get("genres"))
        if genres:
            out["genres"] = genres
        # TV-only show extras.
        _set_if(out, "number_of_seasons", detail.get("number_of_seasons"))
        _set_if(out, "number_of_episodes", detail.get("number_of_episodes"))
        networks = _tmdb_network_names(detail.get("networks"))
        if networks:
            out["networks"] = networks
        created = _tmdb_created_by_names(detail)
        if created:
            out["directors"] = created

    ext = _tmdb_get(f"{TMDB_API_ROOT}/tv/{tmdb_id}/external_ids", {}, api_key)
    if isinstance(ext, dict):
        _set_imdb(out, ext.get("imdb_id"))

    credits = _tmdb_get(f"{TMDB_API_ROOT}/tv/{tmdb_id}/credits", {}, api_key)
    if isinstance(credits, dict):
        cast = _tmdb_cast_names(credits)
        if cast:
            out["cast"] = cast


def _tmdb_detail_episode(out, tmdb_id, season, episode, api_key):
    """Fill `out` (in place) with EPISODE detail. GET
    /3/tv/{id}/season/{s}/episode/{e} for the per-episode fields + the show's
    /3/tv/{id}/external_ids for the IMDb link. Each call is optional."""
    detail = _tmdb_get(
        f"{TMDB_API_ROOT}/tv/{tmdb_id}/season/{season}/episode/{episode}",
        {}, api_key,
    )
    if isinstance(detail, dict):
        _set_if(out, "episode_title", detail.get("name"))
        _set_if(out, "overview", detail.get("overview"))
        _set_if(out, "rating", detail.get("vote_average"))
        _set_if(out, "vote_count", detail.get("vote_count"))
        _set_if(out, "runtime", detail.get("runtime"))
        _set_if(out, "air_date", detail.get("air_date"))
        _set_if(out, "release_date", detail.get("air_date"))
        # Prefer TMDB's own numbers, falling back to the id-parsed ones.
        out["season_number"] = detail.get("season_number") if isinstance(
            detail.get("season_number"), int) else season
        out["episode_number"] = detail.get("episode_number") if isinstance(
            detail.get("episode_number"), int) else episode
    else:
        out["season_number"] = season
        out["episode_number"] = episode

    # IMDb link comes from the SHOW's external ids (episodes have no own imdb_id).
    ext = _tmdb_get(f"{TMDB_API_ROOT}/tv/{tmdb_id}/external_ids", {}, api_key)
    if isinstance(ext, dict):
        _set_imdb(out, ext.get("imdb_id"))


def _tmdb_year_of(date_str):
    """Leading 4-digit year from a TMDB date string ('2010-07-15' -> 2010), or
    None for a missing/un-parseable value."""
    m = re.match(r"(\d{4})", str(date_str or ""))
    return int(m.group(1)) if m else None


def _set_if(out, key, value):
    """Set out[key] = value only when value is meaningful: not None, not an empty
    string. (0 and 0.0 are kept — a 0-vote/0-runtime is still a real value the UI
    may want to show.)"""
    if value is None:
        return
    if isinstance(value, str) and value == "":
        return
    out[key] = value


def _set_imdb(out, imdb_id):
    """Record imdb_id + the derived imdb_url when imdb_id is a non-empty string.
    Idempotent (the show external_ids may be fetched once); a falsy id is a no-op
    so a movie/show with no IMDb mapping simply omits both fields."""
    if isinstance(imdb_id, str) and imdb_id:
        out["imdb_id"] = imdb_id
        out["imdb_url"] = f"https://www.imdb.com/title/{imdb_id}/"


def tmdb_detail(library, mid):
    """Rich TMDB dossier dict for library entry `mid`, or None when it has no
    metadata.tmdb_id (the ONLY None case — see the section header above).

    READ-ONLY, alias-safe, and NEVER raises: every TMDB call is the cached,
    None-on-failure _tmdb_get, and the dict is built up from what is computable
    offline (tmdb_id / kind / tmdb_url) so a partial/total fetch failure yields a
    partial dict (never a 500). The tmdb_id is read from the STORED entry, not the
    caller, so a crafted/unknown id is just a missing key -> None.
    """
    if not isinstance(library, dict):
        return None
    if library.get(mid) is None:
        return None
    try:
        real_id, entry = _resolve_alias(library, mid)
    except KeyError:
        return None
    if not isinstance(entry, dict):
        return None

    tmdb_id = (entry.get("metadata") or {}).get("tmdb_id")
    if not tmdb_id:
        return None

    kind = _tmdb_detail_kind(real_id, entry)
    api_key = mvcommon.tmdb_api_key()

    # The offline-computable core. tmdb_url uses movie vs tv (an episode is part of
    # a tv show, so it links to the show page) — derived purely from kind + id.
    url_kind = "movie" if kind == "movie" else "tv"
    out = {
        "tmdb_id": tmdb_id,
        "kind": kind,
        "tmdb_url": f"https://www.themoviedb.org/{url_kind}/{tmdb_id}",
    }
    # Seed title from the stored metadata so a fully-failed fetch still names the
    # entry; a successful TMDB call overwrites it with the canonical title.
    _set_if(out, "title", (entry.get("metadata") or {}).get("title"))

    # No API key -> nothing can be fetched from TMDB, but the offline core is still
    # useful (and the route returns it 200, never a misleading "no tmdb_id" 404). The
    # online-metadata MERGE below is independent of the TMDB key — a populated
    # mvonline.json still enriches the dossier even with no TMDB key configured.
    if api_key:
        if kind == "movie":
            _tmdb_detail_movie(out, tmdb_id, api_key)
        elif kind == "episode":
            se = _episode_se_of(real_id, entry)
            if se is not None:
                _tmdb_detail_episode(out, tmdb_id, se[0], se[1], api_key)
            else:
                # Defensive: kind said episode but the parse vanished — degrade to show.
                _tmdb_detail_tv(out, tmdb_id, api_key)
        else:
            _tmdb_detail_tv(out, tmdb_id, api_key)

    # MERGE the cached ONLINE metadata (OMDb ratings/awards/box-office), populated by
    # `refresh_online`. CACHE-ONLY — NEVER a live OMDb call here, so the hover dossier
    # stays fast and never blocks on the network. For an EPISODE, out["tmdb_id"] is
    # already the SHOW's tmdb_id (enrich stamps the show id on every leaf), so the
    # episode inherits the show's ratings. Only fields present in the cache are added.
    _merge_online_metadata(out, tmdb_id)

    # MERGE the cached TRIVIA (EXA+GROQ-distilled facts), populated by `fetch_trivia`.
    # Same CACHE-ONLY contract — never a live EXA/GROQ call in the request path. For
    # an episode this uses the SHOW's tmdb_id, so the episode inherits the show's
    # trivia. Adds `trivia` only when present + non-empty.
    _merge_trivia(out, tmdb_id)

    return out


def _merge_trivia(out, tmdb_id):
    """Merge the cached TRIVIA for ``tmdb_id`` into the detail dict ``out`` (in place).
    Adds ``trivia`` (a list of {text, source}) ONLY when present + non-empty in
    mvextra.json — so an absent/empty cache entry simply omits the field. NO live
    EXA/GROQ call (cache read only — fetch_trivia populates it), so the hover dossier
    stays fast. For an EPISODE, ``tmdb_id`` is already the SHOW's tmdb_id (enrich
    stamps the show id on every leaf), so the episode inherits the show's trivia.
    Never raises (extra_cache_get degrades a malformed cache to None)."""
    entry = extra_cache_get(tmdb_id)
    if not isinstance(entry, dict):
        return
    trivia = entry.get("trivia")
    if isinstance(trivia, list) and trivia:
        out["trivia"] = trivia


def _merge_online_metadata(out, tmdb_id):
    """Merge the cached online-metadata for ``tmdb_id`` into the detail dict ``out``
    (in place). Adds ``ratings`` (imdb/rt/metacritic map), ``rated``, ``awards`` and
    ``boxoffice`` ONLY when present + non-empty in mvonline.json — so an absent cache
    entry, or a partial one, simply omits those fields. NO live OMDb call (cache read
    only); never raises (online_cache_get degrades a malformed cache to None)."""
    cached = online_cache_get(tmdb_id)
    if not isinstance(cached, dict):
        return
    ratings = cached.get("ratings")
    if isinstance(ratings, dict) and ratings:
        out["ratings"] = ratings
    for src_key, out_key in (("rated", "rated"), ("awards", "awards"), ("boxoffice", "boxoffice")):
        _set_if(out, out_key, cached.get(src_key))


# ---------------------------------------------------------------------------
# Folder-tree payload for the web console grouped/folder view (IMP-E14 polish).
#
# build_tree() mirrors the on-disk folder hierarchy under each category root,
# built FROM the already-safe items_payload() leaves (inheriting the
# alias/season_map skip — PR #21 crash class — for free) PLUS the UNPREPPED disk
# files collect_reclaimable() surfaces, so the tree spans every lifecycle state.
# It is strictly READ-ONLY: it only os.scandir/os.stat()s directories
# (metadata-only, never reads media bytes) to compute real recursive folder sizes
# and detect a poster.jpg/fanart.jpg anywhere in a subtree.
# ---------------------------------------------------------------------------

# Folder-image filenames recognised for has_image / the folder-image route.
# Lower-cased; comparisons casefold the on-disk name.
_FOLDER_IMAGE_NAMES = ("poster.jpg", "fanart.jpg")

# The artwork "kinds" resolve_artwork_path accepts, each mapping to "<kind>.jpg".
_FOLDER_IMAGE_NAMES_KINDS = ("poster", "fanart")

# Map a category bucket to its on-disk root subfolder under LOCAL_ROOT, used by
# build_tree to nest leaves. "other" is intentionally ABSENT: .get("other") -> None
# makes build_tree resolve it to LOCAL_ROOT, so an oth- leaf nests with its OWN
# subfolder (Sports/…) as a top folder under the Others bucket (multi-folder safe).
# The disk WALKERS instead derive their roots from CATEGORY_ROOTS (see config).
_CATEGORY_ROOT_SUBDIR = {"movies": "Movies", "series": "Series", "anime": "Anime"}


def _scan_folder_meta(folder):
    """Return (size_bytes, has_image) for ``folder`` computed by ONE recursive
    os.scandir metadata walk of everything under it. size_bytes sums st_size of
    every file in the subtree; has_image is True if a poster.jpg/fanart.jpg (any
    case) exists in ``folder`` or any descendant. Metadata-only — never opens a
    file. Every os.scandir / stat is OSError-guarded (an unreadable dir/entry
    contributes 0 and is skipped rather than crashing the whole walk)."""
    total = 0
    has_image = False
    try:
        with os.scandir(folder) as it:
            entries = list(it)
    except OSError:
        return 0, False
    for entry in entries:
        try:
            is_dir = entry.is_dir(follow_symlinks=False)
        except OSError:
            continue
        if is_dir:
            sub_size, sub_img = _scan_folder_meta(entry.path)
            total += sub_size
            has_image = has_image or sub_img
        else:
            if entry.name.lower() in _FOLDER_IMAGE_NAMES:
                has_image = True
            try:
                total += entry.stat(follow_symlinks=False).st_size
            except OSError:
                continue
    return total, has_image


def _node_sort_name(node):
    """The case-insensitive name a tree node sorts by: a folder's ``name``, or a
    leaf's display title (falling back to its filename then id)."""
    if node["type"] == "folder":
        return (node.get("name") or "").casefold()
    return (node.get("title") or os.path.basename(node.get("path") or "")
            or node.get("id") or "").casefold()


def _sort_tree_children(children):
    """Sort a folder's children: sub-folders first, then leaves, each group by
    case-insensitive name. Mutates and returns the list."""
    children.sort(key=lambda n: (0 if n["type"] == "folder" else 1, _node_sort_name(n)))
    return children


def build_tree():
    """Return the folder HIERARCHY mirroring the on-disk structure under each
    category root, spanning ALL five lifecycle states (READ-ONLY).

    Shape::

        {"roots": {"movies": [<node>...], "series": [...],
                   "anime": [...], "other": [...]}}

    A FOLDER node:  {type:"folder", name, path, size_bytes, has_image, children}
      - size_bytes : REAL recursive folder size (os.scandir metadata walk) — it
                     already counts every on-disk file, including the unprepped
                     ones added as leaves below, so size logic is unchanged.
      - has_image  : poster.jpg/fanart.jpg present in the folder or any descendant.
      - children   : sub-folders first, then leaf nodes, each sorted by name.

    LEAF nodes come from TWO sources, so the tree spans every state:
      1. items_payload() physical library leaves — each row + {"type":"leaf"};
         these carry the four library states (LOCAL_NOT_PUSHED /
         PUSHED_NOT_ARCHIVED / RESTORED_REPLACE_AGAIN / ARCHIVED) in `state`.
      2. collect_reclaimable() UNPREPPED rows — on-disk video files NOT in the
         library (e.g. Sample.mkv, an Extras clip). Each becomes a leaf with
         `state:"UNPREPPED"`, nested under its own on-disk folder so it shows
         beside the library leaves it sits next to. EVERY leaf — from either
         source — carries a non-null `state`, so the grouped web view can be
         filtered by all five states.

    Alias/season_map-safe: items_payload() already skips virtual rows, and the
    UNPREPPED rows are plain disk files (no virtual concern). De-dup: a path that
    is both a library leaf and (somehow) an UNPREPPED row appears once — the
    library leaf wins (UNPREPPED rows are by definition not-in-library, so this
    is just a guard). Intermediate path segments become folder nodes nested by
    the leaf's REAL on-disk path relative to its category root.
    """
    payload = items_payload()
    library_leaves = payload["items"]

    # Build a single, ORDER-PRESERVING list of leaf specs — (category, leaf
    # on-disk path, leaf node) — so library leaves and unprepped disk files run
    # through ONE identical nesting pass below. Library leaves are added first so
    # that on the (by-definition impossible-for-UNPREPPED, but guarded) chance of
    # a path collision the library leaf wins.
    leaf_specs = []
    seen_leaf_keys = set()  # normpath-lower of every leaf path already added

    for leaf in library_leaves:
        leaf_node = dict(leaf)
        leaf_node["type"] = "leaf"
        key = os.path.normpath(leaf["path"]).lower()
        seen_leaf_keys.add(key)
        leaf_specs.append((leaf.get("category", "other"), leaf["path"], leaf_node))

    # Add the UNPREPPED disk files (files on disk, not in the library) as leaves
    # so the tree covers the fifth state. collect_reclaimable() is the same
    # read-only disk walk /api/reclaim uses; we take only its badge=="UNPREPPED"
    # rows. Each becomes a minimal leaf mirroring the items leaf fields the
    # frontend reads, plus the reclaim row's `guessed`/`suggested_command`.
    for row in collect_reclaimable()["items"]:
        if row.get("badge") != "UNPREPPED":
            continue
        key = os.path.normpath(row["path"]).lower()
        if key in seen_leaf_keys:
            continue  # de-dup guard: a library leaf already owns this path
        seen_leaf_keys.add(key)
        category = category_of_id(row["id"])
        leaf_node = {
            "type": "leaf",
            "id": row["id"],
            "category": category,
            "state": "UNPREPPED",
            "size_bytes": row["size_bytes"],
            "path": row["path"],
            "title": row["id"],  # no library metadata yet; mirror items' id-as-title
            "guessed": True,
            "suggested_command": row.get("suggested_command"),
            "poster_available": False,
            "chunk_count": 1,
        }
        leaf_specs.append((category, row["path"], leaf_node))

    roots = {"movies": [], "series": [], "anime": [], "other": []}
    # Per-category index: tuple(segment, ...) -> folder node, so repeated leaves
    # under the same folder reuse one node. The empty tuple is the category root
    # itself (leaves that sit directly in the category root attach there).
    folder_index = {cat: {} for cat in roots}
    # Memoize the metadata walk per absolute folder path so a folder visited for
    # several leaves is scanned once (single pass per real directory).
    meta_cache = {}

    def _meta(folder_path):
        cached = meta_cache.get(folder_path)
        if cached is None:
            cached = _scan_folder_meta(folder_path)
            meta_cache[folder_path] = cached
        return cached

    def _ensure_folder(cat, segments, abs_path):
        """Return (creating if needed) the folder node for ``segments`` under
        category ``cat``; ``abs_path`` is that folder's absolute on-disk path."""
        key = tuple(segments)
        existing = folder_index[cat].get(key)
        if existing is not None:
            return existing
        size, has_image = _meta(abs_path)
        node = {
            "type": "folder",
            "name": segments[-1],
            "path": abs_path,
            "size_bytes": size,
            "has_image": has_image,
            "children": [],
        }
        folder_index[cat][key] = node
        # Attach to its parent folder (or the category root for a top-level folder).
        parent_segments = segments[:-1]
        parent_abs = os.path.dirname(abs_path)
        if parent_segments:
            parent = _ensure_folder(cat, parent_segments, parent_abs)
            parent["children"].append(node)
        else:
            roots[cat].append(node)
        return node

    for cat, leaf_path, leaf_node in leaf_specs:
        if cat not in roots:
            cat = "other"
        folder = os.path.dirname(leaf_path)
        subdir = _CATEGORY_ROOT_SUBDIR.get(cat)
        cat_root = os.path.join(LOCAL_ROOT, subdir) if subdir else LOCAL_ROOT

        # Path segments of the leaf's FOLDER relative to the category root.
        try:
            rel = os.path.relpath(folder, cat_root)
        except ValueError:
            # Different drive (Windows) — cannot nest under the root; treat as flat.
            rel = ""
        if rel in ("", "."):
            segments = []
        elif rel.startswith(".."):
            # Folder is OUTSIDE the category root (e.g. an "other" leaf elsewhere).
            # Nest it directly under the category bucket as a single flat folder
            # named after its own folder, keyed by its absolute path so it stays
            # unique and never collides with in-root segments.
            segments = None
        else:
            segments = [s for s in rel.split(os.sep) if s and s != "."]

        if not segments:
            if segments is None:
                # Out-of-root: synthesise a single folder node for the leaf's own
                # directory so its real size/has_image still surface.
                key = ("\x00abs", folder)  # private key; never collides with seg tuples
                node = folder_index[cat].get(key)
                if node is None:
                    size, has_image = _meta(folder)
                    node = {
                        "type": "folder",
                        "name": os.path.basename(folder) or folder,
                        "path": folder,
                        "size_bytes": size,
                        "has_image": has_image,
                        "children": [],
                    }
                    folder_index[cat][key] = node
                    roots[cat].append(node)
                node["children"].append(leaf_node)
            else:
                # Leaf sits directly in the category root — attach at top level.
                roots[cat].append(leaf_node)
            continue

        parent = _ensure_folder(cat, segments, folder)
        parent["children"].append(leaf_node)

    # Final ordering: folders-first then leaves, by name, at every level.
    def _sort_recursive(node):
        if node["type"] != "folder":
            return
        _sort_tree_children(node["children"])
        for child in node["children"]:
            _sort_recursive(child)

    for cat, top in roots.items():
        _sort_tree_children(top)
        for child in top:
            _sort_recursive(child)

    return {"roots": roots}


def _is_within_local_root(abs_path):
    """True iff ``abs_path`` (a real path) resides under LOCAL_ROOT. Used by the
    folder-image + open-folder routes' path-traversal guard. Case-insensitive
    containment via os.path.commonpath on realpath-resolved paths."""
    try:
        root = os.path.realpath(LOCAL_ROOT)
        target = os.path.realpath(abs_path)
    except OSError:
        return False
    try:
        common = os.path.commonpath([os.path.normcase(root), os.path.normcase(target)])
    except ValueError:
        # Different drives / mixed absolute+relative -> not contained.
        return False
    return common == os.path.normcase(root)


def find_folder_image(folder):
    """Return the absolute path of a poster.jpg/fanart.jpg to serve for ``folder``
    (READ-ONLY), or None if none exists in the subtree.

    Preference order: poster.jpg then fanart.jpg in ``folder`` ITSELF; failing
    that, the FIRST poster.jpg/fanart.jpg found in any descendant folder (a
    breadth-ish DFS). The returned file is guaranteed to be one of the two
    recognised names AND under LOCAL_ROOT (callers still re-assert containment).
    Every os.scandir is OSError-guarded.
    """
    if not _is_within_local_root(folder):
        return None

    def _named_image_in(dir_path):
        """Return abs path of poster.jpg (preferred) or fanart.jpg directly in
        dir_path, else None. Casefold the on-disk name."""
        found = {}
        try:
            with os.scandir(dir_path) as it:
                for entry in it:
                    try:
                        if entry.is_file(follow_symlinks=False):
                            low = entry.name.lower()
                            if low in _FOLDER_IMAGE_NAMES:
                                found[low] = entry.path
                    except OSError:
                        continue
        except OSError:
            return None
        for name in _FOLDER_IMAGE_NAMES:  # poster.jpg before fanart.jpg
            if name in found:
                return found[name]
        return None

    # 1) The folder itself.
    direct = _named_image_in(folder)
    if direct:
        return direct

    # 2) First image found in any descendant (BFS, deterministic by sorted name).
    from collections import deque
    queue_dirs = deque([folder])
    while queue_dirs:
        current = queue_dirs.popleft()
        subdirs = []
        try:
            with os.scandir(current) as it:
                for entry in it:
                    try:
                        if entry.is_dir(follow_symlinks=False):
                            subdirs.append(entry.path)
                    except OSError:
                        continue
        except OSError:
            continue
        for sub in sorted(subdirs, key=str.casefold):
            hit = _named_image_in(sub)
            if hit:
                return hit
            queue_dirs.append(sub)
    return None


# A folder name carries a provider token like `{tmdb-70523}` / `{tvdb-12345}`
# (the Plex/Emby/Jellyfin convention rename_folder stamps on a SHOW folder). The
# season-inheritance resolver walks UP to the nearest ancestor whose basename
# matches this — i.e. the show folder — and uses ITS poster as the fallback.
_PROVIDER_TOKEN_RE = re.compile(r"\{tmdb-[^}]+\}", re.IGNORECASE)


def _kind_image_under_root(folder, kind):
    """Return the absolute path of ``<kind>.jpg`` (poster.jpg / fanart.jpg)
    sitting DIRECTLY in ``folder`` — but ONLY if it exists on disk AND the
    realpath-resolved file is under LOCAL_ROOT with the exact allowed basename.
    Otherwise None. The single security funnel every candidate flows through, so
    a returned path is ALWAYS a vetted poster/fanart inside the media root.

    ``folder`` is derived from a LIBRARY entry's stored ``folder_path`` (or its
    real on-disk ancestors), never from raw client input, so this re-assertion is
    defence-in-depth against a stored path that somehow escaped the root."""
    if not folder:
        return None
    candidate = os.path.join(folder, f"{kind}.jpg")
    try:
        if not os.path.isfile(candidate):
            return None
        real = os.path.realpath(candidate)
    except OSError:
        return None
    # Exact-name allow-list (case-insensitive on-disk name) AND under LOCAL_ROOT.
    if os.path.basename(real).lower() != f"{kind}.jpg":
        return None
    if not _is_within_local_root(real):
        return None
    return real


def _episode_still_under_root(folder, filename):
    """Return the absolute path of the per-episode still `<basename>-thumb.jpg`
    sitting DIRECTLY in ``folder`` — but ONLY if it exists on disk AND the
    realpath-resolved file is under LOCAL_ROOT with EXACTLY the basename derived
    from the episode's own stored ``filename`` (never client input). Otherwise
    None. The still-equivalent of ``_kind_image_under_root`` — the security funnel
    every per-episode candidate flows through.

    The expected basename is computed from ``filename`` via ``_episode_thumb_name``
    (``os.path.splitext(filename)[0] + "-thumb.jpg"``); the on-disk file is then
    matched case-insensitively against THAT exact name, so a sibling that merely
    ends in `-thumb.jpg` for a DIFFERENT episode is never served here."""
    if not folder or not filename:
        return None
    want = _episode_thumb_name(filename)
    if not want:
        return None
    candidate = os.path.join(folder, want)
    try:
        if not os.path.isfile(candidate):
            return None
        real = os.path.realpath(candidate)
    except OSError:
        return None
    if os.path.basename(real).lower() != want.lower():
        return None
    if not _is_within_local_root(real):
        return None
    return real


def _ancestor_show_folder_image(start_folder, kind):
    """Walk UP ``start_folder``'s real on-disk ancestors to the NEAREST ancestor
    whose basename carries a ``{tmdb-…}`` token (the show folder) and return that
    folder's vetted ``<kind>.jpg`` (or None). Stops at / never escapes LOCAL_ROOT
    (the walk halts once it climbs above the media root). READ-ONLY."""
    if not start_folder or not _is_within_local_root(start_folder):
        return None
    try:
        current = os.path.realpath(start_folder)
        root = os.path.realpath(LOCAL_ROOT)
    except OSError:
        return None
    # Climb until we exit the media root or hit the filesystem ceiling.
    while _is_within_local_root(current):
        name = os.path.basename(current)
        if _PROVIDER_TOKEN_RE.search(name or ""):
            hit = _kind_image_under_root(current, kind)
            if hit:
                return hit
            # The show folder was found but has no <kind>.jpg — there is no
            # higher show folder for this leaf, so stop (don't keep climbing past
            # the show into collection roots).
            return None
        parent = os.path.dirname(current)
        if parent == current:  # reached a drive/root — cannot climb further
            break
        current = parent
        if current == root or not _is_within_local_root(current):
            break
    return None


def resolve_artwork_path(library, mid, kind="poster"):
    """Resolve the absolute path of a leaf/season's artwork file (READ-ONLY,
    path-only — never opens or copies anything), or None (IMP-E3/U3/D17, Phase 5).

    ``kind`` selects which artwork (``"poster"`` -> poster.jpg, ``"fanart"`` ->
    fanart.jpg); any other value falls back to "poster".

    RESOLUTION ORDER — LOCAL ALWAYS WINS at each level (locked decision #8, "Dark"
    requirement). The first existing file wins:
      (i-still, poster ONLY) for an EPISODE leaf (owns a ``filename`` AND a
            season+episode id shape), the per-episode still
            ``<own folder>/<splitext(filename)[0]>-thumb.jpg`` — the
            Jellyfin/Kodi/Plex local episode-thumbnail name. fanart skips this rung
            (episodes have no per-episode fanart).
      (i)   the resolved entry's OWN ``folder_path`` ``<kind>.jpg``. For a season
            episode this folder IS the season folder, so a season-specific local
            poster naturally wins here. For a season_map it is the season folder.
      (ii)  else the entry's season container's folder ``<kind>.jpg`` — found via
            the leaf's ``parent_id`` -> the ``season_map`` entry's ``folder_path``.
      (iii) else the NEAREST ancestor folder (walking UP the entry's real
            ``folder_path``) whose name carries a ``{tmdb-…}`` token — the show
            folder — and its ``<kind>.jpg`` (so every episode inherits the show
            poster when nothing more specific exists).
    So an episode WITHOUT its own still falls back to the season poster, then the
    show poster — the per-episode waterfall the task pins.

    ALIAS / season_map SAFE (PR #21 crash class): ``mid`` is resolved one hop via
    ``_resolve_alias`` so a ``multi_ep_alias`` dereferences to its PRIMARY leaf's
    real folder; ``folder_path`` is NEVER read off a raw virtual entry. A
    ``season_map`` keeps its own ``folder_path`` (level i). Every ``.get`` /
    ``os.path`` call is guarded.

    SECURITY: every candidate is derived from the LIBRARY entry's stored paths
    (and their real ancestors), NEVER from client input beyond ``mid`` (which only
    indexes the dict) + ``kind`` (allow-listed). Each candidate passes through
    ``_kind_image_under_root`` / ``_episode_still_under_root`` / the ancestor walk,
    each of which returns ONLY a file named exactly poster.jpg/fanart.jpg OR the
    episode's own ``<basename>-thumb.jpg`` (basename taken from the entry's stored
    ``filename``), and only when it realpath-resolves UNDER LOCAL_ROOT. A crafted
    ``mid`` (``..``, an absolute path, etc.) cannot escape: it is just a missing
    dict key -> None.
    """
    if kind not in _FOLDER_IMAGE_NAMES_KINDS:
        kind = "poster"
    if not isinstance(library, dict):
        return None

    entry = library.get(mid)
    if entry is None:
        return None

    # One-hop alias resolve: a multi_ep_alias -> its primary leaf (which owns the
    # real folder). _resolve_alias raises only on a missing key, which we've ruled
    # out above; guard anyway so a malformed library never propagates.
    try:
        real_id, entry = _resolve_alias(library, mid)
    except KeyError:
        return None
    if not isinstance(entry, dict):
        return None

    # If resolution landed on a still-virtual row (an alias whose primary is
    # missing), it owns no folder of its own — fall back to its season below.
    own_folder = entry.get("folder_path") if entry.get("type") != "multi_ep_alias" else None

    # (i-still, poster ONLY) An EPISODE leaf — one that owns a `filename` AND whose
    # (post-alias) id parses to a season+episode — gets its per-episode still as the
    # FIRST candidate: `<own folder>/<splitext(filename)[0]>-thumb.jpg`. LOCAL wins
    # here exactly like the season-specific poster below; if the still is absent we
    # fall straight through to the season/show poster waterfall. fanart is unchanged
    # (episodes have no per-episode fanart). The candidate basename is derived from
    # the entry's OWN stored filename (never client input) and is vetted under
    # LOCAL_ROOT by _episode_still_under_root.
    if kind == "poster":
        filename = entry.get("filename")
        if filename and own_folder and _episode_se_of(real_id, entry) is not None:
            hit = _episode_still_under_root(own_folder, filename)
            if hit:
                return hit

    # (i) The entry's OWN folder — a season-specific local poster wins here.
    hit = _kind_image_under_root(own_folder, kind)
    if hit:
        return hit

    # (ii) The season container's folder. Find the parent season_map via parent_id
    # (leaf) and use ITS folder_path. Guard the lookup + the parent's shape.
    parent_id = entry.get("parent_id")
    if parent_id:
        parent = library.get(parent_id)
        if isinstance(parent, dict) and parent.get("type") == "season_map":
            hit = _kind_image_under_root(parent.get("folder_path"), kind)
            if hit:
                return hit

    # (iii) Walk UP to the nearest {tmdb-…} show folder and use its <kind>.jpg.
    # Anchor the walk at the most-specific folder we have for this entry.
    anchor = own_folder
    if not anchor and parent_id:
        parent = library.get(parent_id)
        if isinstance(parent, dict):
            anchor = parent.get("folder_path")
    hit = _ancestor_show_folder_image(anchor, kind)
    if hit:
        return hit

    return None


def cmd_web(host=None, port=None, open_browser=True, demo=False):
    """Launch the local web operations console (IMP-E12).

    host/port: when None, taken from mvconfig.json (mvcommon.web_host()/
    web_port()); an explicit --host/--port flag passes a value through and wins.

    SECURITY (IMP-E15): the console is gated by ADMIN-MINTED, EXPIRING tokens
    (see mvcommon's WEB ACCESS TOKENS section + webui.server), NOT a static
    secret. Binding 0.0.0.0 is SAFE by default: a remote peer needs a valid
    minted token, and the genuine-local admin (the owner's own browser) is always
    allowed with no token. So there is NO refuse-to-start guard — if no token is
    minted, remote simply cannot get in (locked), while the owner keeps working
    locally. The local browser is auto-opened at the plain 127.0.0.1 URL (no
    ?token= needed: genuine-local == admin). Mint share tokens with
    `python main.py token create` or the web Access panel.

    demo=True (the `--demo` flag) serves a SAFE review build where EVERY action
    is SIMULATED server-side — no real cmd_*/Selenium/library mutation ever runs
    (IMP-E14). The default (demo=False) is fully real and byte-unchanged."""
    # Resolve host/port: an explicit flag (non-None) overrides; else config.
    if host is None:
        host = mvcommon.web_host()
    if port is None:
        port = mvcommon.web_port()

    try:
        import uvicorn
        from webui.server import create_app
    except ImportError:
        print("❌ web requires fastapi+uvicorn — pip install -r requirements.txt")
        sys.exit(1)

    app = create_app(demo=demo)
    if demo:
        print("🟡 DEMO MODE (actions simulated) — no real command will run.")
    print(f"🌐 MediaVault web UI: http://{host}:{port}")
    token_count = len(mvcommon.list_tokens())
    if token_count:
        print(f"🔒 Access tokens: {token_count} minted — remote peers need a valid token.")
    else:
        print("🔓 No access tokens minted yet — remote is locked; mint one with "
              "`python main.py token create` or the web Access panel.")
    if open_browser:
        # Auto-open the LOCAL browser at the plain URL. The genuine-local admin
        # (the owner's own browser) is allowed with no token, so no ?token= is
        # embedded here; always open 127.0.0.1 even when bound to 0.0.0.0.
        local_url = f"http://127.0.0.1:{port}/"
        try:
            webbrowser.open(local_url)
        except Exception:
            pass
    uvicorn.run(app, host=host, port=port, log_level="warning")


# TTL keyword -> seconds. The CLI `--ttl` accepts these named windows; `never`
# means a non-expiring token (mint_token receives None).
_TOKEN_TTL_CHOICES = {
    "1h": 3600,
    "8h": 8 * 3600,
    "12h": 12 * 3600,
    "1d": 24 * 3600,
    "3d": 3 * 24 * 3600,
    "7d": 7 * 24 * 3600,
    "30d": 30 * 24 * 3600,
    "never": None,
}
_TOKEN_TTL_DEFAULT = "7d"


def _format_expiry(expires_at_iso):
    """Human label for an iso expiry string (or None -> 'never')."""
    return expires_at_iso if expires_at_iso else "never"


def cmd_token_create(label="", ttl=_TOKEN_TTL_DEFAULT):
    """Mint a new web access token and print it + a ready-to-share URL.

    label: free-text device/person label. ttl: one of _TOKEN_TTL_CHOICES keys
    (default 7d; 'never' = no expiry). The RAW token is printed ONCE here and is
    never stored or recoverable — only its sha256 is persisted."""
    if ttl not in _TOKEN_TTL_CHOICES:
        choices = ", ".join(_TOKEN_TTL_CHOICES)
        print(f"❌ Unknown --ttl {ttl!r}. Choose one of: {choices}")
        sys.exit(1)
    ttl_seconds = _TOKEN_TTL_CHOICES[ttl]
    token_id, raw, expires_at = mvcommon.mint_token(label, ttl_seconds)

    host = mvcommon.web_host()
    port = mvcommon.web_port()
    share_url = f"http://{host}:{port}/?token={raw}"

    print("✅ Minted web access token (copy it now — it is shown ONLY once):")
    print(f"   id:      {token_id}")
    if label:
        print(f"   label:   {label}")
    print(f"   expires: {_format_expiry(expires_at)}")
    print(f"   token:   {raw}")
    print(f"   share:   {share_url}")
    if host in ("0.0.0.0", "::"):
        print("   NOTE: host is 0.0.0.0 — replace it in the URL with your LAN or "
              "Tailscale IP for the device.")


def cmd_token_list():
    """Print all minted tokens (id, label, created, expires, EXPIRED?). Never
    prints any hash or raw token (none is stored)."""
    tokens = mvcommon.list_tokens()
    if not tokens:
        print("No web access tokens minted. Create one with: token create --label \"<name>\"")
        return
    print(f"{'ID':<10} {'LABEL':<18} {'CREATED':<22} {'EXPIRES':<22} EXPIRED?")
    for t in tokens:
        created = t.get("created_at") or "-"
        expires = _format_expiry(t.get("expires_at"))
        flag = "YES" if t.get("expired") else ""
        print(f"{(t.get('id') or '-'):<10} {(t.get('label') or ''):<18} "
              f"{created:<22} {expires:<22} {flag}")


def cmd_token_revoke(token_id):
    """Revoke (remove) a token by id. Idempotent: reports whether one existed."""
    removed = mvcommon.revoke_token(token_id)
    if removed:
        print(f"✅ Revoked token {token_id}.")
    else:
        print(f"⚠️  No token with id {token_id} (nothing to revoke).")


# ==========================================
#               MAIN EXECUTION
# ==========================================
if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage:")
        print("  prep [id] [filepath]")
        print("  prep_push_rep [id] [filepath] [optional: SIZE_GB/COUNT val] [device <id_or_name>] [rehash] [tempdir <path>]")
        print("  prep_push_rep_season [id] [folder] [optional: SIZE..] [OPT: episodes] [device <id_or_name>] [rehash] [tempdir <path>]")
        print("  fetch_restore [id] [OPT: episodes 1-3]")  # [NEW]
        print("  set_search [id] [term]")
        print("  set_poster [id] [url]")
        print("  set_fanart [id] [url]")
        print("  set_tmdb [id] [tmdb_id]")
        print("  refresh_online [id_or_prefix] [--force] [--library movies|series|anime]  — Fetch+cache OMDb IMDb/RT/Metacritic ratings + awards/box-office for every title (deduped by tmdb_id; reads into the hover dossier)")
        print("  fetch_trivia [id_or_prefix] [--force] [--library movies|series|anime]  — EXA+GROQ-distill 2-4 short, sourced trivia facts per title (deduped by tmdb_id) into the gitignored mvextra.json the hover dossier reads")
        print("  set_uploaded [id]")
        print("  prep_season [base_id] [folder]")
        print("  scan_unprepped")
        print("  check [id]")
        print("  local_status [opt: limit]")
        print("  push [id] [SIZE_GB/SIZE_MB] [val] [chunks 1-4] [device <id_or_name>] [rehash] [tempdir <path>]")
        print("  push_group [id] [SIZE_GB/SIZE_MB] [val] [episodes 1-3] [device <id_or_name>] [rehash] [tempdir <path>]")
        print("  replace [id]")
        print("  replace_group [id]")
        print("  repair_dummies [optional: id_prefix]")
        print("  verify_library [--fix-dummies]")
        print("  verify_restore [id]")
        print("  restore [id]")
        print("  restore_group [id]")
        print("  sort")
        print("  fetch [id]")
        print("  recover [id|folder]  (or: recover --scan)")
        print("  rename_folder [id|folder] \"<NewName {tmdb-12345}>\"  — rename a show/season folder + rewrite every descendant folder_path (crash-safe, no rehash)")
        print("  web [--port N] [--host H] [--no-browser] [--demo]  — Launch the local web operations console (Disk Reclaim view); --demo = SAFE build, all actions simulated")
        print("  token create [--label \"X\"] [--ttl 1h|8h|12h|1d|3d|7d|30d|never]  — Mint a web access token (default --ttl 7d)")
        print("  token list                                          — List minted web access tokens")
        print("  token revoke [id]                                   — Revoke a web access token by id")
        sys.exit(1)

    cmd = sys.argv[1]

    if cmd == "prep":
        if len(sys.argv) >= 4:
            cmd_prep(sys.argv[2], " ".join(sys.argv[3:]))
        else:
            print("❌ Usage: prep [id] [path]")

    elif cmd == "prep_push_rep":
        if len(sys.argv) < 4:
            print("❌ Usage: prep_push_rep [id] [filepath] [optional: SIZE_MB/COUNT val] [device <id_or_name>]")
            sys.exit(1)

        mid = sys.argv[2]
        rest = sys.argv[3:]

        method = None
        val = None
        device_arg = None
        eager = False
        tdir = None
        filepath_parts = []

        i = 0
        while i < len(rest):
            arg = rest[i]
            if arg in ["SIZE_MB", "SIZE_GB", "COUNT"]:
                if i + 1 < len(rest):
                    method = arg
                    val = rest[i + 1]
                    i += 2
                    continue
            elif arg == "device":
                if i + 1 < len(rest):
                    device_arg = rest[i + 1]
                    i += 2
                    continue
            elif arg == "rehash":
                eager = True
                i += 1
                continue
            elif arg == "tempdir":
                if i + 1 < len(rest):
                    tdir = rest[i + 1]
                    i += 2
                    continue
            filepath_parts.append(arg)
            i += 1

        filepath = " ".join(filepath_parts)
        cmd_prep_push_rep(mid, filepath, method, val, device_id=resolve_device(device_arg), eager_rehash=eager, temp_dir=tdir)

    elif cmd == "prep_push_rep_season":
        if len(sys.argv) < 4:
            print("❌ Usage: prep_push_rep_season [id] [folder] ...")
            sys.exit(1)

        group_id = sys.argv[2]
        args = sys.argv[3:]
        folder_parts = []
        method = None
        val = None
        ep_range = None
        device_arg = None
        eager = False
        tdir = None

        i = 0
        while i < len(args):
            arg = args[i]
            if arg in ["SIZE_MB", "SIZE_GB", "COUNT"]:
                if i + 1 < len(args):
                    method = arg
                    val = args[i + 1]
                    i += 2
                    continue
            elif arg == "episodes":
                if i + 1 < len(args):
                    ep_range = args[i + 1]
                    i += 2
                    continue
            elif arg == "device":
                if i + 1 < len(args):
                    device_arg = args[i + 1]
                    i += 2
                    continue
            elif arg == "rehash":
                eager = True
                i += 1
                continue
            elif arg == "tempdir":
                if i + 1 < len(args):
                    tdir = args[i + 1]
                    i += 2
                    continue
            folder_parts.append(arg)
            i += 1

        folder_path = " ".join(folder_parts)
        cmd_prep_push_rep_season(group_id, folder_path, method, val, ep_range, device_id=resolve_device(device_arg), eager_rehash=eager, temp_dir=tdir)

    elif cmd == "set_search":
        if len(sys.argv) >= 4:
            cmd_set_search(sys.argv[2], " ".join(sys.argv[3:]))
        else:
            print("❌ Usage: set_search [id] [term]")

    elif cmd == "set_poster":
        if len(sys.argv) >= 4:
            cmd_set_poster(sys.argv[2], sys.argv[3])
        else:
            print("❌ Usage: set_poster [id] [url]")

    elif cmd == "set_fanart":
        if len(sys.argv) >= 4:
            cmd_set_fanart(sys.argv[2], sys.argv[3])
        else:
            print("❌ Usage: set_fanart [id] [url]")

    elif cmd == "set_tmdb":
        if len(sys.argv) >= 4:
            cmd_set_tmdb(sys.argv[2], sys.argv[3])
        else:
            print("❌ Usage: set_tmdb [id] [tmdb_id]")

    elif cmd == "set_uploaded":
        cmd_set_uploaded(sys.argv[2])

    elif cmd == "enrich_metadata":
        # enrich_metadata [id_or_prefix] [--apply] [--library movies|series|anime]
        # DRY-RUN by default; --apply writes. Pass the positional id/prefix (if any)
        # plus all remaining tokens as flags so cmd_enrich_metadata parses --apply/
        # --library/--nfo itself.
        rest = sys.argv[2:]
        positional = rest[0] if (rest and not rest[0].startswith("--")) else None
        cmd_enrich_metadata(positional, *rest)

    elif cmd == "refresh_online":
        # refresh_online [id_or_prefix] [--force] [--library movies|series|anime]
        # Fetch+cache OMDb ratings/awards/box-office for every distinct title (deduped
        # by tmdb_id). Writes ONLY mvonline.json. Pass the positional id/prefix (if any)
        # plus all remaining tokens as flags so cmd_refresh_online parses them itself.
        rest = sys.argv[2:]
        positional = rest[0] if (rest and not rest[0].startswith("--")) else None
        cmd_refresh_online(positional, *rest)

    elif cmd == "fetch_trivia":
        # fetch_trivia [id_or_prefix] [--force] [--library movies|series|anime]
        # EXA web-search + GROQ-distill 2-4 sourced trivia facts for every distinct
        # title (deduped by tmdb_id). Writes ONLY mvextra.json. Pass the positional
        # id/prefix (if any) plus all remaining tokens as flags so cmd_fetch_trivia
        # parses them itself.
        rest = sys.argv[2:]
        positional = rest[0] if (rest and not rest[0].startswith("--")) else None
        cmd_fetch_trivia(positional, *rest)

    elif cmd == "prep_season":
        if len(sys.argv) >= 4:
            cmd_prep_season(sys.argv[2], " ".join(sys.argv[3:]))
        else:
            print("❌ Usage: prep_season [base_id] [folder]")

    elif cmd == "scan_unprepped":
        cmd_scan_unprepped()

    elif cmd == "check":
        cmd_check(sys.argv[2])

    elif cmd == "local_status":
        limit = sys.argv[2] if len(sys.argv) >= 3 else None
        cmd_local_status(limit)

    elif cmd == "replace":
        cmd_replace(sys.argv[2])

    elif cmd == "replace_group":
        cmd_replace_group(sys.argv[2])

    elif cmd == "repair_dummies":
        prefix = sys.argv[2] if len(sys.argv) > 2 else None
        cmd_repair_dummies(prefix)

    elif cmd == "verify_library":
        fix = "--fix-dummies" in sys.argv[2:]
        cmd_verify_library(fix_dummies=fix)

    elif cmd == "verify_restore":
        cmd_verify_restore(sys.argv[2])

    elif cmd == "restore":
        cmd_restore(sys.argv[2])

    elif cmd == "restore_group":
        cmd_restore_group(sys.argv[2])

    elif cmd == "push":
        args = sys.argv[2:]
        if not args:
            print("❌ Usage: push [id] ...")
            sys.exit(1)

        mid = args[0]
        method = None
        val = None
        c_range = None
        dev = None
        eager = False
        tdir = None

        i = 1
        while i < len(args):
            if args[i] in ["SIZE_MB", "SIZE_GB", "COUNT"]:
                if i + 1 < len(args):
                    method = args[i]
                    val = args[i + 1]
                    i += 2
                else:
                    print("❌ Error: Missing value for split method.")
                    sys.exit(1)
            elif args[i] == "chunks":
                if i + 1 < len(args):
                    c_range = args[i + 1]
                    i += 2
                else:
                    print("❌ Error: Missing value for chunks range.")
                    sys.exit(1)
            elif args[i] == "device":
                if i + 1 < len(args):
                    dev = args[i + 1]
                    i += 2
                else:
                    print("❌ Error: Missing value for device.")
                    sys.exit(1)
            elif args[i] == "rehash":
                eager = True
                i += 1
            elif args[i] == "tempdir":
                if i + 1 < len(args):
                    tdir = args[i + 1]
                    i += 2
                else:
                    print("❌ Error: Missing value for tempdir.")
                    sys.exit(1)
            else:
                i += 1

        cmd_push(mid, method, val, c_range, device_id=resolve_device(dev), eager_rehash=eager, temp_dir=tdir)

    elif cmd == "push_group":
        group_id, method, val, ep_range, dev, eager, tdir = parse_push_group_args(sys.argv[2:])
        cmd_push_group(group_id, method, val, ep_range, device_id=resolve_device(dev), eager_rehash=eager, temp_dir=tdir)

    elif cmd == "sort":
        cmd_sort()

    elif cmd == "recover":
        args = sys.argv[2:]
        if args and args[0] == "--scan":
            cmd_recover(scan=True)
        elif args:
            cmd_recover(" ".join(args))
        else:
            print("❌ Usage: recover [id|folder]   (or: recover --scan)")

    elif cmd == "rename_folder":
        # rename_folder <old_folder_or_id> "<NewName {tmdb-12345}>"
        if len(sys.argv) >= 4:
            cmd_rename_folder(sys.argv[2], sys.argv[3])
        else:
            print("❌ Usage: rename_folder [id|folder] \"<NewName {tmdb-12345}>\"")

    elif cmd == "fetch":
        if len(sys.argv) < 3:
            print("❌ Usage: fetch [id] [OPT: episodes 1-3]")
            sys.exit(1)

        mid = sys.argv[2]
        epr = None
        if len(sys.argv) >= 5 and sys.argv[3] == "episodes":
            epr = sys.argv[4]

        cmd_dispatch_fetch(mid, epr)

    elif cmd == "fetch_restore":
        # [NEW] Usage: fetch_restore [id] [OPT: episodes 1-3]
        if len(sys.argv) < 3:
            print("❌ Usage: fetch_restore [id] [OPT: episodes 1-3]")
            sys.exit(1)

        mid = sys.argv[2]
        epr = None
        if len(sys.argv) >= 5 and sys.argv[3] == "episodes":
            epr = sys.argv[4]

        cmd_fetch_restore(mid, epr)

    elif cmd == "token":
        # Web access token management (IMP-E15): create / list / revoke.
        sub = sys.argv[2] if len(sys.argv) >= 3 else None
        if sub == "create":
            rest = sys.argv[3:]
            label = ""
            ttl = _TOKEN_TTL_DEFAULT
            i = 0
            while i < len(rest):
                if rest[i] == "--label" and i + 1 < len(rest):
                    label = rest[i + 1]
                    i += 2
                elif rest[i] == "--ttl" and i + 1 < len(rest):
                    ttl = rest[i + 1]
                    i += 2
                else:
                    i += 1
            cmd_token_create(label=label, ttl=ttl)
        elif sub == "list":
            cmd_token_list()
        elif sub == "revoke":
            if len(sys.argv) < 4:
                print("❌ Usage: token revoke [id]")
                sys.exit(1)
            cmd_token_revoke(sys.argv[3])
        else:
            print("❌ Usage: token create [--label \"X\"] [--ttl 1h|8h|12h|1d|3d|7d|30d|never] | token list | token revoke [id]")
            sys.exit(1)

    elif cmd == "web":
        args = sys.argv[2:]
        # Default to None so cmd_web falls back to mvconfig.json; an explicit
        # --host/--port flag below overrides the config value.
        host = None
        port = None
        open_browser = True
        demo = False
        i = 0
        while i < len(args):
            if args[i] == "--host" and i + 1 < len(args):
                host = args[i + 1]
                i += 2
            elif args[i] == "--port" and i + 1 < len(args):
                try:
                    port = int(args[i + 1])
                except ValueError:
                    print("❌ --port must be an integer")
                    sys.exit(1)
                i += 2
            elif args[i] == "--no-browser":
                open_browser = False
                i += 1
            elif args[i] == "--demo":
                demo = True
                i += 1
            else:
                i += 1
        cmd_web(host=host, port=port, open_browser=open_browser, demo=demo)