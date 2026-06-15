import os
import json
import sys
import subprocess
import shutil
import re
import math
import time
import stat
import tempfile
import requests
from datetime import datetime, timezone
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
DEVICE_ALIASES = {"movies": "FA69H0300200", "series": "FA75V0303405"}

# Remote push reliability conventions (rclone "chunker"-style).
# AUTO-ROLLBACK SEAM: each chunk is uploaded to "<final>.partial" then atomically
# renamed; remnant "<chunk>.partial" files are the only thing a push rollback must
# `adb shell rm` (Google Photos never indexes a .partial as a complete chunk).
PARTIAL_SUFFIX = ".partial"
MVMETA_SUFFIX = ".mvmeta.json"  # Remote disaster-recovery sidecar mirroring split_info
# IMP-C8: post-push remote hash verification. Gated off here; config-file
# support (toggle without editing source) arrives with IMP-A5.
PUSH_VERIFY_REMOTE = False

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

    # Command Execution
    cmd = [MKVMERGE_PATH, "-o", output_pattern, "--split", f"size:{split_arg}", input_path]
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
        # Fresh journal per command run (a leftover from a crashed run is handled by
        # recover_journal(), not silently appended to).
        self._flush()

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
        roots = [os.path.join(LOCAL_ROOT, c) for c in ("Movies", "Series", "Anime")]
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
        if entry.get("uploaded") == True or entry.get("status") == "archived":
            # [ROLLBACK SPEC] Early-skip: returns True having created ZERO artifacts.
            # The wrapper MUST treat this as success and NEVER roll back.
            print(f"   ⏭️  Skipping Prep: {manual_id} (Already marked as uploaded/archived).")
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


def cmd_prep_season(base_id, folder_path):
    print(f"=== BATCH PREP: {base_id} ===")
    folder_path = folder_path.strip('"').strip("'")
    if not os.path.exists(folder_path): print("❌ Folder not found."); return

    files = sorted([f for f in os.listdir(folder_path) if f.lower().endswith(VIDEO_EXTENSIONS)])
    if not files: print("❌ No video files found."); return

    # [UPDATED] Anime Detection Logic
    is_anime = base_id.startswith("ani-")
    count = 0

    for filename in files:
        ep_num = None
        is_sxxexx_combined = False  # Track SxxExxExx combined-episode TV files

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
            print("✅ SUCCESS.\n")
            return True
        else:
            journal.commit()
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
            filtered_ids = []

            for mid in target_ids:
                ep_num = episode_num_from_id(mid, group_id)
                if ep_num is not None and start <= ep_num <= end:
                    filtered_ids.append(mid)

            target_ids = filtered_ids

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
        # [ROLLBACK C] The merge is PRE-PONR. Open a journal and log the
        # reproducible merged output before merging; a merge failure replays the
        # inverse (remove the reproducible target) and keeps chunks for a re-merge.
        journal = RollbackJournal(local_folder, manual_id)
        journal.record_create_reproducible(target_path)
        try:
            merged_ok = merge_video_files(chunk_paths_in_restore, target_path, seed=seed)
        except Exception as e:
            print(f"❌ Merge crashed: {e}")
            merged_ok = False
        if not merged_ok:
            print(f"❌ Merge failed for {manual_id}. Chunks left in restore/ for re-merge.")
            journal.rollback(library)
            return False

        if merged_ok:
            print(f"   > 💾 Re-indexing Merged File (New Container)...")
            new_hash = calculate_file_hash(target_path)

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
    if episode_range:
        try:
            start, end = map(float, episode_range.split('-'))
            filtered = []
            for mid in target_ids:
                ep = episode_num_from_id(mid, group_id)
                if ep is not None and start <= ep <= end:
                    filtered.append(mid)
            target_ids = filtered
            print(f"   > Filtered to {len(target_ids)} items (Episodes {episode_range}).")
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

    print(f"\n=== Batch Restore Complete: {count} files restored. ===")


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

    # Define Categories to Scan
    categories = [
        ("Movies", LIBRARY_MOVIES, os.path.join(LOCAL_ROOT, "Movies")),
        ("Series", LIBRARY_SERIES, os.path.join(LOCAL_ROOT, "Series")),
        ("Anime", LIBRARY_ANIME, os.path.join(LOCAL_ROOT, "Anime"))
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
        subprocess.run(cmd)
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

    if entry.get("type") == "season_map":
        # [UPDATED] Pass the range to restore_group
        print(f"   > Season Map detected. Running Batch Restore...")
        cmd_restore_group(manual_id, episode_range)
    else:
        print(f"   > Single Item detected. Running Restore...")
        cmd_restore(manual_id)

    print("\n✅✅✅ FETCH & RESTORE COMPLETE.")


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
        print("  verify_restore [id]")
        print("  restore [id]")
        print("  restore_group [id]")
        print("  sort")
        print("  fetch [id]")
        print("  recover [id|folder]  (or: recover --scan)")
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

    elif cmd == "set_uploaded":
        cmd_set_uploaded(sys.argv[2])

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