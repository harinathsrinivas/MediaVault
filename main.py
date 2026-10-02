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
import copy
import csv
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
    MKVEXTRACT_PATH,
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
# [IMP-C25] identity capture (<folder>/<short_id>.gpcapture.json) — best-effort, called
# only after cmd_prep / cmd_push succeed, after the extras scan saved and after an extra
# uploaded; never raises, never journalled.
import gpcapture
# [IMP-C25] the shared Google Photos parsers (dedupKey helpers) for the apply engine — stdlib-only and
# already loaded by mvcommon, so this adds no import cost and no cycle.
import gpweb

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
# [IMP-C25] Extra phones the READ-ONLY device cross-check also lists, keyed by the DEVICE_ALIASES alias they
# serve (user ruling 2026-10-01, docs/feature-fetch-datetime/DECISIONS.md §1d: HT74D0202839 is an extra movies
# phone). Device-check only: pushes keep DEVICE_ALIASES' one serial per alias (resolve_device never reads this).
# device_check_aliases() merges both for device_inventory(). Edit it when the physical phones change.
DEVICE_CHECK_EXTRA_SERIALS = {"movies": ["HT74D0202839"]}

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
# OPTIONAL nested blocks add NO entry type and are in NO `required` set —
# `metadata`/`tech_spec`/`split_info`/`parent_id` on a leaf, and (IMP-D19) an
# `extras` block on a TITLE entry: the movie *leaf*, or the *season_map* for
# series/anime/others. Exactly like `split_info`, `extras` just nests on an
# existing entry and round-trips through save_library/load_library — it changes
# no `required`/`physical` set and adds no type. Shape (Card A2 — grouped per
# source folder): extras = {"groups": {"<group_rel>": {"added_date", "items":
# [ {filename, sub_rel, short_id, hash, status, uploaded, search_term,
# tech_spec, [split_info], [re_hashed]}, ... ]}}}, where group_rel is the extra
# folder's path relative to the title's folder_path and sub_rel is the file's
# path within that group (see scan_extras_folders / docs/feature-extras/DECISIONS.md).
# Item lifecycle (mirrors a leaf's): `status` walks local_ready -> onboarded
# (pushed) -> archived (dummied) -> restored_local (fetched back); `uploaded`
# flips True at push and stays True across replace/restore (a re-scan that
# finds CHANGED bytes resets the item to local_ready/uploaded=False). A split
# item's `split_info` is {is_split, method, val, total_chunks, chunks:
# [{filename, hash}, ...]} (written by push_one_extra), gaining
# merge_seed/merge_tool/rehashed_at + item-level `re_hashed`: True when the
# first restore blesses the merged hash (restore_one_extra) — the same
# split_info vocabulary a main-content leaf uses. Authoritative in-code spec:
# the EXTRAS DATA LAYER section below (grep "EXTRAS DATA LAYER").
#
# (IMP-C25) An optional `gphotos` block — the Google Photos identity of ONE
# uploaded object — nests on EACH object a push uploads (PLAN P-1): the
# unsplit leaf itself, each `split_info.chunks[i]`, each FLAC holder
# `split_info.carried_out_tracks[i]`, an unsplit extras item, and each
# `split_info.chunks[i]` of a split extras item. Never on a split entry itself
# (P-3: its chunks + holders were uploaded, not the whole file); a season_map
# holds blocks only inside its extras; a multi_ep_alias holds none. Like
# `extras` it adds NO entry type, is in NO `required` set, changes no
# `physical` flag, round-trips through save_library/load_library untouched,
# and the whole-library read commands ignore it. Shape v1 (only known facts,
# never null; times are UTC "...Z" strings, or epoch ms in taken_ms/upload_ms):
#   gphotos = {v: 1, sha256, sha1, dedup_key, size_bytes, width, height,
#     duration_s, date_utc, uploaded_name,
#     pushed: {at, device_id, device_tz, device_tz_offset_min},
#     accounts: {<movies|tv|anime|others>: {photo_id, taken_ms, tz_offset_ms,
#       upload_ms, dedup_key, gp_filename, width, height, verified_at,
#       verified_by, source, approach: {best, ok, failed, at, via},
#       alternates, original_quality, partial_upload, confirmed}},
#     home_account, search_override: {query, at}}
# - PIN (P-2): a block is trusted only while its `sha256` equals the owning
#   object's CURRENT hash (leaf `hash` / chunk `hash` / holder `holder_hash` /
#   extras item `hash`); an untrusted block (it describes other bytes, e.g. an
#   extras item re-mastered after its push) is ignored by readers and replaced
#   by the apply engine, the block's ONLY writer (P-10). A re-split replaces
#   split_info wholesale, so the old chunks' blocks go with it.
# - `sha1`/`dedup_key` = the content key Google Photos dedups on (dedup_key =
#   unpadded urlsafe base64 of the SHA-1); `date_utc` = the file's Matroska
#   DateUTC (the taken time Google Photos shows); `uploaded_name` = the exact
#   pushed name; `pushed` = the push's device, device timezone and time.
# - accounts[<account>] = the copy in that Google account: its permanent
#   `photo_id` + item facts; `verified_by` dedupkey|sha256|filename|manual and
#   `source` map|check|fetch|confirm|manual; `approach` = which fetch
#   approaches pinpointed it (`ok`, `best` = the first in waterfall order) or
#   `failed`, from photo_id|hash|taken_second|exact_name|title_sid|user_query|
#   legacy (`via` rpc|ui); `alternates` = photo ids of other items with the
#   same name (re-split duplicates); `original_quality` / `partial_upload` =
#   that copy's quality flags (a non-original or partial copy fails a
#   restore's SHA-256 check); `confirmed` = seen there with upload_ms AND a
#   verified identity (never from the push-time capture alone, P-11).
# - `home_account` = the learned account fetch routes to (P-4: mvconfig
#   fetch_account_overrides > home_account > id prefix); `search_override` =
#   the user's own search term for this object, from the failed list (OD-5).
# - Never a download URL (P-20: URLs expire and act as credentials).
# Authoritative in-code spec: mvcommon's "GOOGLE PHOTOS IDENTITY" section
# (new_gphotos_block / block_trusted / iter_uploaded_objects); decisions:
# docs/feature-fetch-datetime/DECISIONS.md.
# - `map_photos --repair split-info` (IMP-C25 Step 9 / IMP-C27 (a)) can write
#   a `split_info` back onto a leaf (or extras item) that lost it: {is_split,
#   total_chunks, chunks: [{filename, hash}]} — WITHOUT method / val, which
#   are unknown after the loss (every reader takes them with .get()).
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
# THIS registry AND the guard test's non-physical set; when you change a nested
# block's shape, update its canonical fixture there too (_extras_block /
# _gphotos_block).
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
# [IMP-C20] Matroska codec IDs mkvmerge refuses to `--split`. MEASURED, never
# guessed — every entry has a row in
# `docs/edge-case-unsplittable-tracks/CODEC-SPLIT-MATRIX.md`. Keep this list
# CONSERVATIVE: a false positive blocks a legitimate archive, whereas a miss now
# only costs the (clear, post-IMP-C19) mkvmerge error at split time. TrueHD is a
# suspected member but is deliberately absent — it has not been measured.
UNSPLITTABLE_CODEC_IDS = {"A_FLAC"}
# [FLAC-CARRYOUT] Codecs we can CARRY OUT (extract → store in a valid-video holder →
# re-add on restore) instead of refusing. FLAC is the one measured case: its bytes
# are preserved exactly (lossless, no conversion) via the opaque-payload container.
# A codec in UNSPLITTABLE_CODEC_IDS but NOT here is still refused with the runbook.
CARRY_OUT_CODEC_IDS = {"A_FLAC"}


def find_unsplittable_tracks(input_path):
    """[IMP-C20] Return [(track_id, codec_id, language), ...] for tracks mkvmerge
    cannot `--split`, via a header-only `mkvmerge -J` probe.

    Returns [] when the file is fine AND when identification fails for any reason
    (missing binary, unreadable file, unparseable JSON) — a probe failure must
    never block an archive on its own. The split itself still reports, now legibly.
    """
    try:
        # [get-encoding] mkvmerge -J emits valid UTF-8 (track names can be
        # non-ASCII, e.g. NFC-composed "Íslenska"). Without an explicit encoding,
        # Windows text=True defaults to cp1252 (strict), and the reader thread's
        # decode of a UTF-8 non-cp1252 byte (0x81) raises UnicodeDecodeError
        # OUTSIDE this try block — crashing the thread and degrading the probe.
        # encoding="utf-8" fixes that; errors="replace" guards any pathological
        # byte so the probe still degrades to [] per its never-block contract.
        r = subprocess.run([MKVMERGE_PATH, "-J", input_path],
                           capture_output=True, text=True,
                           encoding="utf-8", errors="replace", check=True)
        tracks = json.loads(r.stdout).get("tracks", [])
    except Exception:
        return []
    return [(t.get("id"), t["properties"].get("codec_id"),
             t["properties"].get("language") or "und")
            for t in tracks
            if t.get("properties", {}).get("codec_id") in UNSPLITTABLE_CODEC_IDS]


def refuse_if_unsplittable(input_path, label, carry_out_flac=False):
    """[IMP-C20] Shared gate: print the refusal and return True when `input_path`
    carries a track mkvmerge cannot split AND we will not carry it out. Callers
    abort on True.

    [FLAC-CARRYOUT] FLAC is carry-out-able, but ONLY on the paths that actually do
    the carry-out (`cmd_push`, the autopilot gates). `carry_out_flac=True` lets a
    FLAC track pass through (the caller handles the carry-out / proceeds to it);
    the DEFAULT (False) still refuses FLAC — protecting paths that split WITHOUT
    carrying out (e.g. `push_one_extra`). A codec that is unsplittable but NOT
    carry-out-able is refused regardless.

    Used both by cmd_push (immediately before the split) and by the autopilots
    BEFORE their prep leg — the prep leg is the expensive one (deep scan +
    whole-file hash), so refusing only at push time would still burn it."""
    skip = CARRY_OUT_CODEC_IDS if carry_out_flac else set()
    bad = [(tid, codec, lang) for (tid, codec, lang) in find_unsplittable_tracks(input_path)
           if codec not in skip]
    if not bad:
        return False
    print(f"❌ Cannot split {label} — mkvmerge cannot split these tracks:")
    for tid, codec, lang in bad:
        print(f"     • track {tid} ({codec}, {lang})")
    print("   Remux the track to a splittable codec first — WavPack is lossless")
    print("   and splits fine; dropping the track also works if you don't want it.")
    print("   Runbook: docs/edge-case-unsplittable-tracks/RUNBOOK-remux-before-split.md")
    return True


def _print_mkvmerge_failure(e):
    """[IMP-C19] Echo mkvmerge's own diagnosis after a non-zero exit.

    mkvmerge writes its `Error:`/`Warning:` lines to STDOUT, not stderr (stderr
    carries only hard crashes, e.g. the libfmt `format_error`). Discarding stdout
    is what reduced the 2026-08-24 FLAC-split failure to a bare `exit status 2`
    with no way to tell that track 4 was simply unsplittable — see
    `docs/edge-case-unsplittable-tracks/`. Prints the named Error/Warning lines,
    falling back to the tail of the output when mkvmerge died without one."""
    lines = []
    for stream in (e.stdout, e.stderr):
        if stream:
            if isinstance(stream, bytes):
                stream = stream.decode("utf-8", "replace")
            lines += [l.rstrip() for l in stream.splitlines() if l.strip()]
    named = [l for l in lines if l.startswith(("Error", "Warning"))]
    for line in (named or lines[-3:]):
        print(f"   > {line}")


def split_video_file(input_path, output_dir, method, value_str, file_id="", drop_track=None):
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
    # in the path is read as a
    # format field and mkvmerge dies with `fmt::format_error: argument not found`
    # (exit 3). This escape is LOAD-BEARING ON THE HAPPY PATH, not a legacy
    # safeguard: the canonical provider token is `{tmdb-<id>}` (decision D12), so
    # every stamped folder contains braces and every split of an archived title
    # passes through here. It has fired for real before — a `{tmdb-79660}` folder
    # once aborted an entire prep→push→replace. Pinned by
    # tests/test_split_brace_escape.py.
    # Escape them as `{{`/`}}` for the -o arg ONLY — mkvmerge renders them
    # back to single braces and writes to the real folder. (A plain merge -o is taken
    # literally and must NOT be escaped — see merge_video_files; verified mkvmerge v97.)
    # [FLAC-CARRYOUT] drop_track: exclude one audio track (by its container track
    # id) from the split so the remainder is splittable even when that single
    # track is a codec mkvmerge refuses to split (FLAC). None = byte-for-byte the
    # pre-existing argv (no --audio-tracks).
    mkv_out = output_pattern.replace("{", "{{").replace("}", "}}")
    cmd = [MKVMERGE_PATH, "-o", mkv_out, "--split", f"size:{split_arg}"]
    if drop_track is not None:
        cmd += ["--audio-tracks", f"!{drop_track}"]
    cmd.append(input_path)
    try:
        # [IMP-C19] Capture BOTH streams — mkvmerge reports its errors on stdout.
        subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        # [FLAC-CARRYOUT] Match ONLY the split's own chunk pattern (".chunk.NNN.mkv"),
        # not every ".mkv" in the dir — a carried-out FLAC holder also lives in the
        # same dir as "<base> [<short_id>].holder.mkv" and must NOT be returned as a
        # chunk (it is uploaded/hashed separately via carried_out_tracks).
        chunk_re = re.compile(r"\.chunk\.\d+\.mkv$")
        chunks = sorted([os.path.join(output_dir, f) for f in os.listdir(output_dir)
                         if chunk_re.search(f)])
        print(f"   > Done. Generated {len(chunks)} parts.")
        return chunks
    except subprocess.CalledProcessError as e:
        print(f"❌ Error running mkvmerge for splitting: {e}");
        _print_mkvmerge_failure(e)
        return []
    except FileNotFoundError:
        print(f"❌ Error: mkvmerge not found at {MKVMERGE_PATH}");
        return []


def _carryout_merge_argv(chunk_uids, carried, flac_fid):
    """[FLAC-CARRYOUT] Pure: build the `--track-order` string + per-FLAC flag
    options that re-insert a carried-out track at its ORIGINAL position with its
    ORIGINAL flags, GENERICALLY (driven by the recorded manifest, matched by UID —
    not by sample-specific indices).

    `chunk_uids`  : ordered list of the dropped-track chunk's track UIDs (FID 0).
    `carried`     : a carried_out_tracks record — must carry `uid`, `language`,
                    `default`, `forced`, `enabled`, `name`, and the full
                    `original_tracks` manifest (ordered, each with a `uid`).
    `flac_fid`    : the FID of the re-added audio file (= len(chunk_paths)).

    Returns (track_order_str, per_flac_opts). No I/O, no mutation."""
    manifest = carried.get("original_tracks") or []
    # map chunk track uid -> its index within FID 0 (mkvmerge renumbers, UID is stable)
    chunk_index = {uid: i for i, uid in enumerate(chunk_uids)}
    pairs = []
    for mt in manifest:
        muid = mt.get("uid")
        if muid is not None and str(muid) == str(carried.get("uid")):
            pairs.append(f"{flac_fid}:0")  # the carried track re-added from the FLAC file
        else:
            # a track that survives in the chunk — find its renumbered id by uid
            idx = chunk_index.get(str(muid)) if muid is not None else None
            if idx is None:
                # uid unknown: fall back to positional (last resort, should not happen
                # when the manifest + chunk agree)
                continue
            pairs.append(f"0:{idx}")
    track_order = ",".join(pairs) if pairs else None

    opts = []
    lang = carried.get("language")
    if lang:
        opts += ["--language", f"0:{lang}"]
    if carried.get("default") is True:
        opts += ["--default-track", "0:1"]
    else:
        opts += ["--default-track", "0:0"]
    if carried.get("forced") is True:
        opts += ["--forced-track", "0:1"]
    else:
        opts += ["--forced-track", "0:0"]
    if carried.get("name"):
        opts += ["--track-name", f"0:{carried['name']}"]
    return track_order, opts


def merge_video_files(chunk_paths, output_path, seed=None, carried=None, chunk_uids=None):
    print(f"   > 🛠️  Merging {len(chunk_paths)} chunks...")
    # Syntax: mkvmerge -o output.mkv chunk1 +chunk2 +chunk3 ...
    # When a seed is supplied, prepend the GLOBAL `--deterministic <seed>` option
    # (it must precede -o) so the merged container is byte-identical across runs
    # (mkvmerge v97.0, confirmed in the planning spike). seed=None keeps the argv
    # byte-for-byte identical to the original, non-deterministic merge.
    #
    # [SPLIT-SYNC] `--append-mode track` offsets each appended chunk by that TRACK's
    # own end timestamp, not the WHOLE file's (mkvmerge's default "file" mode uses the
    # highest timestamp across ALL tracks, which over-gaps when video/audio/subtitle
    # tracks end at different times — stretching the merged timeline vs the source).
    # Measured: default "file" stretched a 290s slice to 291.35s; "track" kept it at
    # 290.04s (identical to source). Without this, a carried-out FLAC (re-added at its
    # true length) drifts out of sync with the stretched video — the Black Panther
    # desync bug.
    #
    # [FLAC-CARRYOUT] carried: a carried_out_tracks record. When present, the
    # merge re-adds the extracted FLAC at its ORIGINAL position/flags (D-9) by
    # appending the .flac as an additional (non-appended) input (FID=len(chunks))
    # with `--track-order` + per-file flag options regenerated from the manifest.
    cmd = [MKVMERGE_PATH]
    if seed is not None:
        cmd += ["--deterministic", seed]
    cmd += ["--append-mode", "track"]
    if carried is not None:
        flac_path = carried.get("flac_path")
        track_order, opts = _carryout_merge_argv(
            chunk_uids or [], carried, len(chunk_paths))
        if track_order:
            cmd += ["--track-order", track_order]
    cmd += ["-o", output_path]
    cmd.append(chunk_paths[0])
    for chunk in chunk_paths[1:]:
        cmd.append(f"+{chunk}")
    if carried is not None:
        # per-file options must sit immediately before the FLAC input (D-9)
        cmd += opts
        cmd.append(carried["flac_path"])

    try:
        # [IMP-C19] Capture BOTH streams — a merge failure happens during restore,
        # when the chunks are the only copy, so the reason must not be discarded.
        subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        print("   > Merge Complete.")
        return True
    except subprocess.CalledProcessError as e:
        print(f"❌ Error merging: {e}");
        _print_mkvmerge_failure(e)
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
#   OPAQUE-PAYLOAD CONTAINER (FLAC carry-out; reusable for ISO later)
# ==========================================
# A Matroska file can carry arbitrary bytes as an ATTACHMENT, and Google Photos
# ingests a valid playable MKV as a normal video. So an opaque payload (a FLAC
# track, an .iso, …) can be "hidden" inside a short, valid video purely as an
# attachment, uploaded to the Pixel's Photos, and recovered byte-for-byte (the
# attachment round-trip is lossless — proven by md5). This is the SAME trick the
# 10 KB dummy already relies on, generalized to a payload of any size/type.
#
# The helpers below are payload-agnostic: bytes in -> attachment -> bytes out.
# `wrap_payload_in_container` builds a ~10 s H.264/AAC stub and attaches `payload`
# via `mkvmerge --attach-file`; `extract_payload_from_container` surfaces the
# payload back with `mkvextract attachments`. Neither inspects nor interprets the
# payload — the caller decides what those bytes mean.
#
# PURE wrt MediaVault: these touch no library, no journal, no rollback state. They
# are only invoked from cmd_push/cmd_restore (and, later, an ISO archival path).

def _build_container_stub(stub_path):
    """Write a ~10 s testsrc H.264+AAC stub (the same shape the user already
    uploaded to Google Photos). Returns True on success."""
    ffmpeg = resolve_ffmpeg()
    if not ffmpeg:
        print("❌ ffmpeg not found. Cannot build the payload container stub.")
        return False
    cmd = [
        ffmpeg,
        "-f", "lavfi", "-i", "testsrc=size=320x240:rate=24:duration=10",
        "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "veryfast",
        "-c:a", "aac", "-shortest",
        "-loglevel", "error", "-nostdin", "-y", stub_path,
    ]
    r = subprocess.run(cmd, capture_output=True, text=True)
    return r.returncode == 0 and os.path.exists(stub_path)


def wrap_payload_in_container(payload_path, out_path):
    """Wrap `payload_path` (any bytes) into a valid playable MKV as an attachment.
    Returns True on success. `out_path` is created (never overwritten by callers
    — callers remove any prior file first)."""
    stub_path = out_path + ".stub.mkv"
    try:
        if not _build_container_stub(stub_path):
            return False
        payload_name = os.path.basename(payload_path)
        cmd = [
            MKVMERGE_PATH, "-o", out_path,
            "--attachment-name", payload_name,
            "--attach-file", payload_path,
            stub_path,
        ]
        # mkvmerge reports errors on stdout (IMP-C19) — capture both.
        r = subprocess.run(cmd, capture_output=True, text=True,
                           encoding="utf-8", errors="replace")
        if r.returncode != 0 or not os.path.exists(out_path):
            _print_mkvmerge_failure(r)
            return False
        return True
    finally:
        if os.path.exists(stub_path):
            try:
                os.remove(stub_path)
            except Exception:
                pass


def _container_attachment_id(container_path):
    """Return the attachment id of the single payload attachment in `container_path`,
    or None. Uses mkvmerge -J (JSON, UTF-8-normalized like find_unsplittable_tracks)."""
    try:
        r = subprocess.run([MKVMERGE_PATH, "-J", container_path],
                           capture_output=True, text=True,
                           encoding="utf-8", errors="replace", check=True)
        data = json.loads(r.stdout)
    except Exception:
        return None
    atts = data.get("attachments", [])
    if len(atts) != 1:
        return None
    return atts[0].get("id")


def extract_payload_from_container(container_path, out_path):
    """Extract the single payload attachment out of a container back to `out_path`.
    Returns True on success (the recovered bytes are then byte-compared by callers)."""
    aid = _container_attachment_id(container_path)
    if aid is None:
        print("❌ Container has no single payload attachment to extract.")
        return False
    cmd = [MKVEXTRACT_PATH, "attachments", container_path, f"{aid}:{out_path}"]
    r = subprocess.run(cmd, capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    return r.returncode == 0 and os.path.exists(out_path)


def probe_track_manifest(input_path):
    """Return the ORDERED per-track manifest of `input_path`:
    a list of {index, type, codec_id, language, default, forced, enabled, name, uid}.
    The driver of generic track-order + flag reconstruction (D-9). Returns [] on
    any probe failure (mkvmerge -J is UTF-8; a failure must be handled upstream)."""
    try:
        r = subprocess.run([MKVMERGE_PATH, "-J", input_path],
                           capture_output=True, text=True,
                           encoding="utf-8", errors="replace", check=True)
        data = json.loads(r.stdout)
    except Exception:
        return []
    out = []
    for t in data.get("tracks", []):
        p = t.get("properties", {})
        out.append({
            "id": t.get("id"),
            "type": t.get("type"),
            "codec_id": p.get("codec_id"),
            "language": p.get("language") or "und",
            "default": p.get("default_track"),
            "forced": p.get("forced_track"),
            "enabled": p.get("enabled_track"),
            "name": p.get("track_name"),
            "uid": str(p.get("uid")) if p.get("uid") is not None else None,
        })
    return out


def _carry_out_flac_track(source_path, parts_dir, short_id, track_id, codec_id, language):
    """[FLAC-CARRYOUT] Carry one FLAC track out of `source_path` into a valid-video
    holder in `parts_dir`, returning the `carried_out_tracks` record (or None on any
    failure — the caller aborts the push cleanly). Does NOT convert or drop anything:
    the FLAC bytes are carried verbatim as a Matroska attachment and proven to
    round-trip byte-exact.

    Steps: (1) extract the FLAC with `ffmpeg -map 0:<track_id> -c copy` to `<base>
    [<short_id>].flac`; (2) wrap it via `wrap_payload_in_container` into `<base>
    [<short_id>].holder.mkv`; (3) verify the wrap round-trips byte-exact; (4) capture
    the full original track manifest for generic re-add. The transient `.flac` is
    removed after the holder is verified (the holder is the stored artifact)."""
    base = os.path.splitext(os.path.basename(source_path))[0]
    flac_path = os.path.join(parts_dir, f"{base} [{short_id}].flac")
    holder_path = os.path.join(parts_dir, f"{base} [{short_id}].holder.mkv")

    ffmpeg = resolve_ffmpeg()
    if not ffmpeg:
        print("   ❌ ffmpeg not found — cannot carry out the FLAC track.")
        return None

    # 1. extract FLAC (stream-copy, byte-exact)
    print(f"   > 🎧 Carrying out FLAC track {track_id} ({language})...")
    r = subprocess.run([ffmpeg, "-y", "-v", "error", "-i", source_path,
                        "-map", f"0:{track_id}", "-c", "copy", flac_path],
                       capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    if r.returncode != 0 or not os.path.exists(flac_path):
        print("   ❌ Failed to extract the FLAC track.")
        return None

    # 2. wrap into a valid-video holder
    if os.path.exists(holder_path):
        os.remove(holder_path)
    if not wrap_payload_in_container(flac_path, holder_path):
        print("   ❌ Failed to wrap the FLAC into a holder container.")
        if os.path.exists(flac_path):
            os.remove(flac_path)
        return None

    # 3. verify the wrap round-trips byte-exact before trusting it
    verify_flac = os.path.join(parts_dir, f"{base} [{short_id}].verify.flac")
    if extract_payload_from_container(holder_path, verify_flac) and \
            calculate_file_hash(verify_flac) == calculate_file_hash(flac_path):
        print("   > ✅ Holder verified byte-exact.")
    else:
        print("   ❌ Holder round-trip verification failed — aborting carry-out.")
        for p in (flac_path, holder_path, verify_flac):
            if os.path.exists(p):
                os.remove(p)
        return None
    if os.path.exists(verify_flac):
        os.remove(verify_flac)

    # 4. capture the full original manifest (generic re-add, D-9)
    manifest = probe_track_manifest(source_path)
    carried = {
        "track_id": track_id,
        "codec": codec_id,
        "language": language,
        "holder_filename": os.path.basename(holder_path),
        "holder_hash": calculate_file_hash(holder_path),
        "original_tracks": manifest,
    }
    # copy the carried track's own flags into the record for the merge builder
    own = next((m for m in (manifest or []) if m.get("id") == track_id), None)
    if own is not None:
        carried["position"] = (manifest or []).index(own)
        carried["default"] = own.get("default")
        carried["forced"] = own.get("forced")
        carried["enabled"] = own.get("enabled")
        carried["name"] = own.get("name")
        carried["uid"] = own.get("uid")
    else:
        carried["position"] = track_id
        carried["default"] = False
        carried["forced"] = False
        carried["enabled"] = True
        carried["name"] = None
        carried["uid"] = None

    # the transient FLAC extract is removed after the holder is verified
    if os.path.exists(flac_path):
        os.remove(flac_path)
    return carried


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

def cmd_prep(manual_id, filepath, parent_id=None, extras=None, extras_size=None):
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
        # [IMP-C25] Post-commit, best-effort, NOT journalled: record the master's identity
        # facts (sha1 from the same hash pass, Matroska DateUTC) for the Google Photos sweep.
        try:
            gpcapture.capture_after_prep(folder_path, manual_id, short_id, filepath,
                                         default_search_term, file_hash, tech_specs)
        except Exception:
            pass  # belt and braces: a capture problem must never turn a committed prep into a failure
        print(f"✅ Library Entry Created & Linked (Search Key: {default_search_term}).\n")
        # IMP-D19: scan+merge extras if provided (prep never uploads)
        if extras:
            _extras_scan_merge_and_save(library, manual_id, extras)
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
# `[tmdbid-<id>]` token on the SHOW/MOVIE folder (via cmd_rename_folder), and
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
    """True if a folder leaf name already carries a TMDB provider token in ANY
    recognized format — `{tmdb-…}`, `[tmdb-…]`, `[tmdbid-…]`, `[tmdbid=…]`, in
    any casing (idempotency guard — we stamp the token at most once per
    show/movie folder).

    A thin wrapper over the shared `mvcommon.has_tmdb_token` (IMP-U6), kept
    because this is the name every stamp site reads. CASE-INSENSITIVE since
    IMP-C23: Plex/Emby/Jellyfin treat the provider token case-insensitively and
    real folders in the wild use `{TMDB-69590}`; while this predicate was
    case-SENSITIVE such a folder read as "no token" and the next enrich/rename
    pass appended a SECOND one (`… {TMDB-69590} {tmdb-69590}`). It then had to be
    kept by hand in lockstep with the artwork-inheritance resolver's own copy of
    the regex; that copy is gone — both callers now go through this ONE shared
    implementation, so the two predicates cannot drift apart again. Called
    MODULE-QUALIFIED so a test that monkeypatches the helper is honoured (the
    binding-hazard note at `import mvcommon`)."""
    return mvcommon.has_tmdb_token(name)


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
        if cat == "other":
            # IMP-D18: Others/sports is not on TMDB/OMDb. Never enrich it — enriching
            # would mis-tag (wrong tmdb_id), rename the real Sports folder via a bogus
            # [tmdbid-…] token, and fetch wrong posters. One skip here covers
            # enrich_metadata / refresh_online / fetch_trivia (all gather via this fn).
            continue

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


def _write_nfo(folder, kind, title, year, tmdb_id, overview="", vote_average=None, api_key=None):
    """Write a Kodi/Jellyfin-compatible NFO file into *folder*.

    For a movie (kind="movie") writes ``movie.nfo`` with a ``<movie>`` root.
    For a show (kind="show") writes ``tvshow.nfo`` with a ``<tvshow>`` root.
    Uses stdlib xml.etree.ElementTree so all text is properly XML-escaped.

    Beyond the core title/year/plot/rating/uniqueid(tmdb) fields (unconditional,
    as before), an OPTIONAL richer element set is populated from TMDB when
    `api_key` is given (IMP-D22, Decision 4): a plain <tmdbid>; <imdbid> +
    <uniqueid type="imdb"> via the existing `_resolve_imdb_id`; <genre>;
    <runtime>; <premiered>; <studio> (movie: production companies via
    `_tmdb_company_names`; show: `_tmdb_network_names`); <director> — for a
    MOVIE, `_tmdb_directors_from_crew` off the movie credits payload's crew
    (job=='Director'); for a SHOW (kind != "movie"), `_tmdb_created_by_names`
    off the TV DETAILS payload's `created_by` array instead (IMP-D22 fix,
    2026-08-28) — TMDB carries a series' creators there, NOT as series-level
    crew with job=='Director' (that is always empty for a show), so sourcing
    a show's <director> from the credits payload silently wrote nothing; and
    <actor><name>…</name></actor> entries via
    `_tmdb_cast_names`. Every added element is OPTIONAL — omitted cleanly when
    `api_key` is falsy or TMDB has no value (a failed/None lookup omits the
    element rather than writing an empty one). `<tvdbid>` is NEVER emitted —
    no TVDB source exists in this codebase (Decision 1); a fabricated one would
    be exactly the silent-corruption class Decision 1 refuses.

    NEVER raises — any IO/permission failure, OR any TMDB-enrichment failure,
    is printed as a warning so the caller's enrich run is never blocked by an
    NFO write error. Overwrites an existing file (NFOs are regenerable
    metadata).
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
    ET.SubElement(root, "tmdbid").text = str(tmdb_id)

    # --- richer element set (IMP-D22/Decision 4) — every lookup best-effort,
    # every element OPTIONAL, this block NEVER blocks the base NFO write below.
    if api_key:
        try:
            imdb_id = _resolve_imdb_id(tmdb_id, kind, api_key)
            if imdb_id:
                ET.SubElement(root, "imdbid").text = imdb_id
                imdb_uid = ET.SubElement(root, "uniqueid")
                imdb_uid.set("type", "imdb")
                imdb_uid.text = imdb_id

            if kind == "movie":
                detail = _tmdb_get(f"{TMDB_API_ROOT}/movie/{tmdb_id}", {}, api_key)
                credits = _tmdb_get(f"{TMDB_API_ROOT}/movie/{tmdb_id}/credits", {}, api_key)
                date_key = "release_date"
            else:
                detail = _tmdb_get(f"{TMDB_API_ROOT}/tv/{tmdb_id}", {}, api_key)
                credits = _tmdb_get(f"{TMDB_API_ROOT}/tv/{tmdb_id}/credits", {}, api_key)
                date_key = "first_air_date"

            if isinstance(detail, dict):
                for genre in _tmdb_genre_names(detail.get("genres")):
                    ET.SubElement(root, "genre").text = genre

                if kind == "movie":
                    runtime = detail.get("runtime")
                else:
                    run_times = detail.get("episode_run_time")
                    runtime = run_times[0] if isinstance(run_times, list) and run_times else None
                if runtime is not None:
                    ET.SubElement(root, "runtime").text = str(runtime)

                premiered = detail.get(date_key)
                if premiered:
                    ET.SubElement(root, "premiered").text = premiered

                studios = (_tmdb_company_names(detail.get("production_companies"))
                           if kind == "movie" else _tmdb_network_names(detail.get("networks")))
                for studio in studios:
                    ET.SubElement(root, "studio").text = studio

            if kind == "movie":
                if isinstance(credits, dict):
                    for director in _tmdb_directors_from_crew(credits):
                        ET.SubElement(root, "director").text = director
            else:
                # SHOWS (IMP-D22 fix, 2026-08-28): TMDB carries a series'
                # creators in the DETAILS payload's `created_by` array, NOT as
                # series-level crew with job=='Director' (that is always empty
                # for a show) — sourcing from `credits` here silently wrote
                # nothing. `detail` is the same /tv/{id} payload fetched above.
                if isinstance(detail, dict):
                    for director in _tmdb_created_by_names(detail):
                        ET.SubElement(root, "director").text = director

            if isinstance(credits, dict):
                for cast_member in _tmdb_cast_names(credits):
                    actor_el = ET.SubElement(root, "actor")
                    ET.SubElement(actor_el, "name").text = cast_member["name"]
        except Exception as exc:
            # NEVER raises (contract preserved) — a lookup failure degrades to
            # the base NFO already built above.
            print(f"     ⚠️  NFO metadata enrichment failed ({exc}) — writing the base NFO.")

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
        # The stamped name is spelled ONCE (IMP-U6): the preview print below and
        # the real stamp in step 2 both use this exact string, so they cannot
        # drift, and the emitted format is a one-line change in mvcommon.
        new_name = f"{base_name} {mvcommon.CANONICAL_TMDB_TOKEN_FMT.format(id=tmdb_id)}"
        if will_stamp:
            print(f"     {'would stamp' if not apply else 'stamping'} folder token: "
                  f"{base_name} -> {new_name}")
        elif folder:
            # Deliberately format-agnostic: _has_tmdb_token accepts every
            # recognized spelling, so naming ONE would misreport a folder
            # carrying a legacy brace token — and base_name shows the real one.
            print(f"     folder already has a TMDB token — skip stamp ({base_name}).")
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

        # 2) stamp the canonical [tmdbid-…] token ONCE on the show/movie folder
        #    (`new_name`, built above; paths only — cmd_rename_folder is journaled
        #    + hash-safe; reused exactly as-is — only the STRING it is called with
        #    changed in IMP-U6, never its journal/PONR behaviour).
        if will_stamp:
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
                api_key=api_key,
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
GROQ_MODEL = "qwen/qwen3.8-27b"
# GROQ retires models without notice — `llama-3.3-70b-versatile` vanished and every
# distill silently returned nothing. When trivia stops producing facts, re-check this
# constant against GET https://api.groq.com/openai/v1/models before debugging anything else.
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


class _GroqRateLimited(Exception):
    """A transient GROQ 429. Raised only inside _groq_chat so retry() can back
    off; it never escapes the function."""
    pass


def _groq_chat(messages, api_key, max_tokens=GROQ_MAX_TOKENS):
    """POST a chat-completion to GROQ -> the assistant message content string, or
    None on any failure. The single requests seam groq_distill_trivia funnels
    through (tests patch this to inject a canned reply).

    The Mozilla User-Agent is REQUIRED (see GROQ_USER_AGENT). A 429 is retried
    with backoff — GROQ's free tier rate-limits partway through a long backfill,
    and without this a transient limit drops that title from the run for good.
    Every other failure returns None on the first try. NEVER raises — a network
    error / non-200 / bad JSON / a reply missing choices all return None."""
    def _post():
        resp = requests.post(
            GROQ_API_ROOT,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                "User-Agent": GROQ_USER_AGENT,
            },
            json={"model": GROQ_MODEL, "messages": messages, "max_tokens": max_tokens},
            timeout=60,
        )
        if resp.status_code == 429:
            raise _GroqRateLimited()
        return resp

    try:
        r = retry(
            _post,
            attempts=4,
            backoff=(5, 20, 60),
            retry_on=(_GroqRateLimited,),
            on_retry=lambda n, _e: print(f"   ⏳ GROQ rate-limited — backing off (attempt {n}/3)"),
        )
    except _GroqRateLimited:
        print("   ⚠️  GROQ returned status 429 (rate limit) — gave up after 4 attempts")
        return None
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

    # The new name is a LEAF name (e.g. "Dark [tmdbid-70523]"), not a full path:
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


# ==========================================
#   PROVIDER-TOKEN FORMAT MIGRATION (IMP-U6)
# ==========================================
# Migrates every on-disk folder still carrying the OLD tmdb token spelling
# (curly `{tmdb-…}`, or a non-canonical square `[tmdb-…]`/`[tmdbid=…]`/wrong
# casing) to the canonical `{tmdb-…}` (mvcommon.CANONICAL_TMDB_TOKEN_FMT).
# See cmd_migrate_provider_tokens below for the full design rationale.

def _old_style_tmdb_token(basename):
    """Return `basename`'s OLD-style TMDB provider token — a
    `mvcommon.find_provider_tokens` item — or None if it carries no tmdb token
    at all, or only the EXACT canonical one already.

    'Old-style' = ANY tmdb token whose matched text is not byte-identical to
    `mvcommon.CANONICAL_TMDB_TOKEN_FMT.format(id=<its own id>)` — e.g.
    `[tmdbid-…]`, `[tmdb-…]`, `[tmdbid=…]`, or the right shape in the wrong
    casing. A folder carrying ONLY a `[tvdbid-…]`/`[imdbid-…]` token (no tmdb
    token) is not old-style — there is nothing here for this migration to do.

    The test is deliberately a pure byte-comparison against the canonical
    render, with NO special-casing of bracket style. An earlier version also
    short-circuited on `bracket == "curly"`, encoding the then-true assumption
    that curly is never canonical. That clause never changed an outcome while
    canonical was square (a curly token's text cannot equal a square render),
    and it became actively wrong the moment canonical became curly: every
    already-migrated `{tmdb-…}` folder was reclassified as a candidate, the
    rename was refused as already-existing, and the command lost the
    idempotency its own contract promises. Compare against the constant and
    nothing else.

    find_provider_tokens returns tokens in left-to-right (span) order; the
    FIRST old-style tmdb token found is returned (a real folder name carries
    at most one tmdb token in practice)."""
    for token in mvcommon.find_provider_tokens(basename or ""):
        if token["provider"] != "tmdb":
            continue
        canonical = mvcommon.CANONICAL_TMDB_TOKEN_FMT.format(id=token["id"])
        if token["match"] != canonical:
            return token
    return None


def _apply_token_span(basename, token):
    """Replace ONLY `token`'s span in `basename` with the canonical
    `{tmdb-<id>}` render, leaving every other character — including a
    coexisting `[tvdbid-…]`/`[rartv]`/etc. bracketed tag — byte-identical.
    Never rebuilds the name from scratch (spans are exact/non-overlapping,
    verified by the mvcommon detection-contract tests)."""
    start, end = token["span"]
    canonical = mvcommon.CANONICAL_TMDB_TOKEN_FMT.format(id=token["id"])
    return basename[:start] + canonical + basename[end:]


def cmd_migrate_provider_tokens(arg=None, *flags):
    """Whole-library TMDB provider-token format migration (IMP-U6): every
    on-disk folder still carrying the OLD `{tmdb-…}`/`[tmdb-…]` spelling is
    renamed to the canonical `{tmdb-…}`.

    Usage: migrate_provider_tokens [id_or_prefix] [--apply] [--library movies|series|anime|others]
    DRY-RUN by default (prints every `OLD -> NEW` it WOULD rename plus a
    summary; writes nothing — no library mutation, no disk mutation, no report
    file). `--apply` performs the renames for real and writes a JSON audit
    report. Mirrors the existing `enrich_metadata`/`refresh_online` dry-run/
    --apply UX exactly — no new UX invented.

    WHY THIS COMMAND EXISTS — the ancestor gap: a prior, external, partial
    migration tool already rewrote the LEAF path segment of most library
    entries to `[tmdbid-…]`, but never climbed to a PARENT directory that no
    entry's `folder_path` names directly — e.g. a show folder
    `Friends (1994) {tmdb-1668}` whose every entry only points at a SEASON
    folder underneath it. This command's discovery walks UP from every
    physical entry's `folder_path` so exactly that shape is caught.

    ANCESTOR-AWARE DISCOVERY: for every physical entry in scope (`leaf` +
    `season_map`; `multi_ep_alias` carries no `folder_path` at all — the
    PR #21 crash class — always skipped), climb from its OWN `folder_path` up
    to (not past) LOCAL_ROOT, collecting every directory in the chain (its own
    folder AND every ancestor), deduplicated across the WHOLE library — one
    show folder is the ancestor of every one of its episodes and must be
    renamed exactly ONCE. This reuses the same test-basename-then-climb-to-
    dirname idiom `_ancestor_show_folder_image` uses for artwork inheritance,
    adapted to abspath/`_norm_path`/`_is_under` comparisons — the SAME path
    vocabulary `cmd_rename_folder`/`_collect_folder_descendants` already use
    (not realpath: a stored `folder_path` is never expected to hide a
    symlink, and matching cmd_rename_folder's own comparison idiom exactly is
    what makes the folder strings this function hands it valid).

    A collected directory is a migration CANDIDATE iff `_old_style_tmdb_token`
    finds an old-style tmdb token on its OWN basename (see that function).
    When renamed, only the tmdb token's own `span` is replaced
    (`_apply_token_span`) — any coexisting bracketed tag (`[tvdbid-…]`,
    `[rartv]`, a chunk short_id, …) is carried through byte-identical.

    DEEPEST-FIRST ORDERING (required, not an optimization): candidates are
    renamed in path-depth-DESCENDING order
    (`len(_norm_path(folder).split(os.sep))`). Renaming a shallower ancestor
    FIRST would move a deeper, not-yet-processed candidate out from under the
    path this function computed for it before the ancestor moved. Processing
    leaf-most-first guarantees every candidate is renamed while its OWN path
    is still what is on disk; `cmd_rename_folder`'s own cascade then re-points
    every already-renamed descendant (physically moved earlier in this same
    run) under the newly-renamed ancestor's new prefix. These two mechanisms
    only compose correctly in this order.

    RESUMABLE WITH NO NEW STATE: every rename goes through the EXISTING,
    already crash-safe `cmd_rename_folder` (IMP-D17: journalled + PONR +
    self-heal) — none of its journal/PONR/self-heal behaviour is touched or
    reimplemented here. A run interrupted after N of M candidates leaves N
    folders already at their canonical basename; a re-run's OWN discovery
    pass simply no longer recognizes them as candidates (their basename is
    already canonical) and continues from N+1. No journal of this command's
    own is added or needed — that is the entire resumability story.

    REPORT (only on --apply): `<LOCAL_ROOT>/migration_reports/
    token_format_<UTC ISO8601 timestamp>.json` (auto-`makedirs`, mirroring the
    existing `library_backups/` sibling-folder convention) shaped
    `{"scanned": N, "renamed": [{"id_or_note", "old_folder", "new_folder",
    "remote_bearing"}], "already_canonical": K, "errors": [...]}`.
    `"scanned"` is the count of distinct directories examined (own folder +
    every ancestor, deduplicated); `"already_canonical"` the count of those
    already carrying the exact canonical tmdb token. `remote_bearing` is True
    when the renamed directory (or ANY of its library descendants) has
    `uploaded` truthy or `status` in `("onboarded", "archived",
    "restored_local")` — it records which renames touched already-pushed
    content so the mapping is never lost; it does NOT attempt any phone-side
    fix (Open Decision #3, docs/feature-token-brackets/PLAN.md).

    A `cmd_rename_folder` PONR-crossed failure (`RollbackHardFail`) is caught
    per-candidate and the run CONTINUES — mirrors the existing "Decision 7"
    convention this codebase already uses at `_enrich_after_archive`'s caller
    (cmd_prep_push_rep_enrich): a post-PONR rename failure warns and
    continues rather than aborting, because the folder that already moved is
    unrelated to every other candidate. It is recorded in the report's
    `errors` list (with the printed `resume_cmd`) — never silently dropped.
    A pre-PONR refusal (`cmd_rename_folder` returning False — e.g. a target
    collision) is likewise recorded in `errors` and the run continues.
    """
    flist = list(flags)
    if arg and str(arg).startswith("--"):
        flist = [arg] + flist
        id_or_prefix = None
    else:
        id_or_prefix = arg or None

    apply = "--apply" in flist
    library_filter = None
    if "--library" in flist:
        i = flist.index("--library")
        if i + 1 < len(flist):
            library_filter = flist[i + 1].lower()

    # "others" (IMP-D18) predates `_gather_enrich_units`'s own prefix_map (which
    # only knows movies/series/anime), so this command keeps its own — mirrors
    # category_of_id's mov/tv/ani/else bucketing, spelled out for the 4 values
    # this command's LOCKED CLI surface accepts. Unlike enrich_metadata/
    # refresh_online (which silently ignore an unrecognized --library value —
    # falling back to "whole library"), an unrecognized value here is a hard
    # refusal: this command MUTATES real folders under --apply, and a mistyped
    # filter silently widening to the WHOLE library is exactly the kind of
    # surprise a destructive command must not allow.
    prefix_map = {"movies": "mov", "series": "tv", "anime": "ani", "others": "oth"}
    want_prefix = None
    if library_filter:
        want_prefix = prefix_map.get(library_filter)
        if want_prefix is None:
            print(f"❌ Unknown --library value '{library_filter}' (expected movies|series|anime|others).")
            return

    def _in_scope(mid):
        if want_prefix and not mid.startswith(want_prefix):
            return False
        if id_or_prefix and not (mid == id_or_prefix or mid.startswith(id_or_prefix)):
            return False
        return True

    library = load_library()

    # --- Ancestor-aware discovery: climb every in-scope PHYSICAL entry's
    # folder_path up to (not past) LOCAL_ROOT, collecting + deduping every
    # directory in the chain (own folder included) by its normalized path. ---
    root_norm = _norm_path(LOCAL_ROOT)
    walked_dirs = {}  # norm_path -> a real (abspath) form of that directory
    for mid, entry in library.items():
        if entry.get("type") == "multi_ep_alias":
            continue  # no folder_path on an alias by construction (PR #21 crash class)
        if not _in_scope(mid):
            continue
        fp = entry.get("folder_path")
        if not fp:
            continue
        current = os.path.abspath(fp)
        while True:
            norm = _norm_path(current)
            if not norm or norm == root_norm or not _is_under(norm, root_norm):
                break  # climbed up to (excluded) or stepped outside LOCAL_ROOT — stop
            walked_dirs.setdefault(norm, current)
            parent = os.path.dirname(current)
            if parent == current:
                break  # filesystem ceiling (drive root) — cannot climb further
            current = parent

    # --- Classify every collected directory: old-style candidate, already
    # canonical, or carries no tmdb token at all (untouched either way). ---
    candidates = []
    already_canonical = 0
    for real_dir in walked_dirs.values():
        basename = os.path.basename(real_dir)
        token = _old_style_tmdb_token(basename)
        if token is not None:
            new_basename = _apply_token_span(basename, token)
            new_dir = os.path.join(os.path.dirname(real_dir), new_basename)
            candidates.append({"old_dir": real_dir, "new_dir": new_dir, "new_name": new_basename})
        elif mvcommon.has_tmdb_token(basename):
            already_canonical += 1
        # else: no tmdb token at all — outside this migration's scope, skipped.

    # Deepest-first (REQUIRED — see docstring): sort by _norm_path depth,
    # descending, so every candidate is renamed while its own path is still
    # what is on disk.
    candidates.sort(key=lambda c: len(_norm_path(c["old_dir"]).split(os.sep)), reverse=True)

    # --- Pre-scan reporting facts (remote_bearing / id_or_note) from the ONE
    # library snapshot loaded above, entirely BEFORE any rename runs — purely
    # read-only, so it is unaffected by whatever order the renames execute in. ---
    for c in candidates:
        descendants = _collect_folder_descendants(library, c["old_dir"])
        c["remote_bearing"] = any(
            e.get("uploaded") or e.get("status") in ("onboarded", "archived", "restored_local")
            for _mid, e in descendants)
        own_ids = sorted(mid for mid, e in descendants
                          if _norm_path(e.get("folder_path")) == _norm_path(c["old_dir"]))
        if own_ids:
            c["id_or_note"] = (own_ids[0] if len(own_ids) == 1
                                else f"{own_ids[0]} +{len(own_ids) - 1} more (own folder)")
        else:
            c["id_or_note"] = f"ancestor of {len(descendants)} entr(y/ies) (no entry's own folder)"

    scanned = len(walked_dirs)
    mode = "APPLY" if apply else "DRY-RUN"
    print(f"=== MIGRATE PROVIDER TOKENS ({mode}) ===")
    if library_filter:
        print(f"   > library filter: {library_filter}")
    if id_or_prefix:
        print(f"   > scope: ids == or startswith '{id_or_prefix}'")
    print(f"   > {scanned} director(y/ies) scanned (own folder + ancestors, deduplicated).\n")

    for c in candidates:
        print(f"   {c['old_dir']} -> {c['new_dir']}")
        if c["remote_bearing"]:
            print(f"       ⚠️  remote-bearing (already-pushed/archived content under this folder).")

    if not apply:
        remote_bearing_n = sum(1 for c in candidates if c["remote_bearing"])
        print(f"\n=== DRY-RUN === scanned={scanned} would-rename={len(candidates)} "
              f"already-canonical={already_canonical} remote-bearing={remote_bearing_n}")
        print("   (dry-run: nothing was written — re-run with --apply to perform it.)")
        return

    # --- APPLY: execute each rename through the EXISTING, crash-safe
    # cmd_rename_folder — never reimplemented here (only the STRING it is
    # called with is ours; its journal/PONR/self-heal are untouched). ---
    renamed = []
    errors = []
    for c in candidates:
        try:
            ok = cmd_rename_folder(c["old_dir"], c["new_name"])
        except RollbackHardFail as hf:
            # "Decision 7"-style warn-and-continue (see docstring): the folder
            # already moved (PONR crossed) but is unrelated to every other
            # candidate — record it and keep migrating the rest of the
            # library rather than aborting the whole run over one torn folder.
            print(f"⚠️  IRREVERSIBLE — folder moved but the library rewrite failed: {hf.state} — {hf.reason}")
            print(f"   > To finish it: {hf.resume_cmd}")
            errors.append({"id_or_note": c["id_or_note"], "old_folder": c["old_dir"],
                            "new_folder": c["new_dir"], "remote_bearing": c["remote_bearing"],
                            "error": f"{hf.state}: {hf.reason}", "resume_cmd": hf.resume_cmd})
            continue
        if ok:
            renamed.append({"id_or_note": c["id_or_note"], "old_folder": c["old_dir"],
                             "new_folder": c["new_dir"], "remote_bearing": c["remote_bearing"]})
        else:
            errors.append({"id_or_note": c["id_or_note"], "old_folder": c["old_dir"],
                            "new_folder": c["new_dir"], "remote_bearing": c["remote_bearing"],
                            "error": "cmd_rename_folder declined (see console output above)"})

    report = {"scanned": scanned, "renamed": renamed, "already_canonical": already_canonical,
              "errors": errors}
    report_dir = os.path.join(LOCAL_ROOT, "migration_reports")
    os.makedirs(report_dir, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    report_path = os.path.join(report_dir, f"token_format_{ts}.json")
    # Same second-resolution collision guard `_preserve_leftover` already uses
    # for its own timestamped sibling (main.py, RollbackJournal): two --apply
    # runs within the same wall-clock second must never silently clobber the
    # earlier report.
    n = 1
    while os.path.exists(report_path):
        report_path = os.path.join(report_dir, f"token_format_{ts}-{n}.json")
        n += 1
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)

    print(f"\n=== APPLIED === scanned={scanned} renamed={len(renamed)} "
          f"already-canonical={already_canonical} errors={len(errors)}")
    print(f"   > report: {report_path}")


# ==========================================
#   SEASON FOLDER NORMALIZATION (IMP-U6, Steps 15-18)
# ==========================================
# Structural follow-up to the token-format flip above: season folders must be
# SELF-IDENTIFYING (each is uploaded to a phone individually) and must carry NO
# id (a verified hazard — Friends S01's own TMDB *season* id, 4573, is "Late
# Night with Conan O'Brien" as a *show* id; season and show ids share one
# numeric namespace and no media server reads a season-level token anyway).
# See cmd_normalize_season_folders below for the full design rationale.

# A season folder's basename embeds a season marker ANYWHERE ('S01',
# 'Season 01', 'Season_04', 's4', case-insensitive) — real season folders
# always do, whether user-organized ('Season 01') or release-named
# ('Peaky.Blinders.S01.2013…'); a FLAT show's own name never does ('Chernobyl
# (Miniseries) 2019 …'). See _season_folder_looks_season_shaped's docstring for
# why this is needed ALONGSIDE the sibling guard, not instead of it.
_SEASON_FOLDER_MARKER_RE = re.compile(r"(?i)(?:^|[\s._-])s(?:eason)?[\s._-]?0*\d{1,2}(?:[^0-9]|$)")

# Windows forbids these in a folder name (control chars included); `:` is
# handled separately (see _sanitize_folder_name_component) so it is NOT in
# this class.
_WIN_INVALID_FOLDER_CHARS_RE = re.compile(r'[<>"/\\|?*\x00-\x1f]')


def _sanitize_folder_name_component(name):
    """Make a raw TMDB show `name` safe as a Windows folder-name component.

    TMDB titles routinely carry characters Windows forbids in a folder name
    (`Star Wars: Andor` has a colon) that never show up on this codebase's
    OTHER folder-naming paths (release names, id-derived slugs — see
    suggest_target_folder) because those are never built from a raw external
    title. cmd_normalize_season_folders is the FIRST place a raw TMDB title
    becomes a folder name, so it is sanitized here, narrowly, rather than
    adding a general-purpose helper nothing else needs yet.

    `:` -> ` -` (a common "Show - Subtitle" release convention — keeps the
    subtitle readable instead of just dropping it). Every other Windows-
    reserved character (`<>"/\\|?*` + control chars) is replaced with a space
    and whitespace is collapsed. A trailing dot/space (also Windows-illegal)
    is stripped. Never returns an empty string (falls back to "Untitled",
    matching suggest_target_folder's own fallback)."""
    name = (name or "").strip()
    name = name.replace(":", " -")
    name = _WIN_INVALID_FOLDER_CHARS_RE.sub(" ", name)
    name = re.sub(r"\s+", " ", name).strip()
    name = name.rstrip(". ")  # Windows forbids a trailing dot/space
    return name or "Untitled"


def _season_folder_looks_season_shaped(basename):
    """True iff `basename` embeds a season marker ANYWHERE (see
    _SEASON_FOLDER_MARKER_RE) — a structural signal this folder is genuinely
    A SEASON of some larger show, not a flat show's own top-level identity.

    Deliberately looser than _show_folder_of's own 'basename EQUALS Season NN'
    check (that check would miss a real release-style season name like
    'Peaky.Blinders.S01.2013…' — see cmd_normalize_season_folders' docstring,
    "FLAT SHOWS"). A folder with NO marker is left alone (conservative —
    'refuse a borderline case rather than rename it'): this is the guard's
    SECOND, independent layer alongside the sibling-ownership check
    (_season_parent_is_exclusive_to_show), needed because a SOLO flat show (no
    OTHER tracked show currently sharing its parent) would otherwise slip past
    the sibling check alone — see that function's docstring for the concrete
    Tamil counter-example this closes."""
    return bool(_SEASON_FOLDER_MARKER_RE.search(basename or ""))


def _folder_tmdb_owner_ids(library, folder):
    """Distinct TMDB show ids evidenced for library-tracked content anywhere
    under `folder` (folder's own basename token included) — THE
    CATEGORY-FOLDER GUARD's ownership test (see
    _season_parent_is_exclusive_to_show).

    TMDB id, not the library key, is the true, stable show identity (defect 1
    — the original guard compared `_show_id_of` library keys instead, and
    MediaVault's library ids embed the SEASON's own air year, not the show's:
    Peaky Blinders S01..S06 are 6 DISTINCT `tv-en-<year>-peakyblinders-s0N`
    keys sharing ONE TMDB id, 60574. Comparing library keys made a show's own
    sibling seasons look like foreign titles and refused almost everything).

    Sources consulted (folder-name token AND metadata.tmdb_id — no TMDB
    network call is ever made here):
      - the folder's OWN basename (an already-tokened show/flat-show folder).
      - each tracked descendant's `metadata.tmdb_id` (written show-wide by a
        prior enrich_metadata run — the SHOW's id, never a season-specific
        one, onto every season_map/leaf of that show unit).
      - each tracked descendant's OWN `folder_path` basename token (the real,
        documented gap: 30 of 59 real season folders are the ONLY place their
        show's id currently survives — see _find_show_tmdb_id).

    Returns None when `folder` holds NO library-tracked content at all
    (untracked — the Mr.Robot S04 case: harmless, never a foreign owner, rides
    along for free when the parent is renamed). Otherwise returns the
    (possibly EMPTY) set of ids found as strings — an empty set means tracked
    content exists but no source above resolved an id anywhere: THE AMBIGUOUS
    case the guard must refuse on, never treat as "no conflict"."""
    descendants = _collect_folder_descendants(library, folder)
    if not descendants:
        return None
    ids = set()
    for t in mvcommon.find_provider_tokens(os.path.basename(folder)):
        if t["provider"] == "tmdb":
            ids.add(str(t["id"]))
    for mid, entry in descendants:
        meta_id = (entry.get("metadata") or {}).get("tmdb_id")
        if meta_id:
            ids.add(str(meta_id))
        fp = entry.get("folder_path")
        if fp:
            for t in mvcommon.find_provider_tokens(os.path.basename(fp)):
                if t["provider"] == "tmdb":
                    ids.add(str(t["id"]))
    return ids


def _season_parent_is_exclusive_to_show(library, parent, show_tmdb_id):
    """THE CATEGORY-FOLDER GUARD (see cmd_normalize_season_folders' docstring
    for the full rationale + real counter-examples: Classic, Tamil, Mr.Robot).
    Returns (True, None) iff `parent` is safe to treat as `show_tmdb_id`'s OWN
    show folder, else (False, <reason string>).

    THE EXACT RULE — TMDB-id-based, not library-key-based (defect 1; see
    _folder_tmdb_owner_ids for why the identity had to change). Every DIRECT
    SUBDIRECTORY of `parent` is classified by _folder_tmdb_owner_ids into ONE
    of three buckets:
      (a) untracked (no library entry lives under it) — e.g. Mr.Robot's
          un-prepped `Mr.Robot.S04.BluRay.2019.1080p.DTS` folder: harmless, it
          rides along for free when the parent is renamed, since
          cmd_rename_folder's os.rename moves the WHOLE directory regardless
          of what the library tracks. IGNORED.
      (b) tracked, and its discoverable TMDB id(s) are EXACTLY
          `{show_tmdb_id}` — another season of the SAME show, an extras
          subfolder of the SAME title, etc. FINE.
      (c) tracked, and EITHER a different TMDB id is found (a sibling
          belonging to a different show/title — `parent` is a CATEGORY folder,
          e.g. `Classic` holding Friends/Peaky Blinders/The Wire/Chernobyl's
          flat folder as four unrelated direct children — tokening it would
          rename the user's entire classics collection to one title's token)
          OR no TMDB id is discoverable at all (ambiguous — a wrong rename
          here is catastrophic, so this is ALSO refused, conservatively, even
          though no PROVEN conflict exists). Either way: REFUSED.

    Deliberately ONE hop, never climbed further — `parent` is always
    `os.path.dirname(<this season's own folder>)`, exactly the level
    cmd_normalize_season_folders' Phase A tokens. NOT climbing further is
    itself part of the guard's safety, not a simplification: climbing further
    is what would eventually reach a category/language folder (`Tamil`) that
    currently, coincidentally, holds only one show — "coincidentally only one
    occupant right now" must never be mistaken for "exclusively this show's
    own folder" (that is what _season_folder_looks_season_shaped additionally
    guards against, for the case where Tamil's one occupant is itself a solo
    flat show with nothing to conflict with here)."""
    if not os.path.isdir(parent):
        return False, f"parent folder not found on disk: {parent}"

    # STRUCTURAL GATE — a direct child of a CATEGORY_ROOT is a LANGUAGE folder
    # (Series/English, Series/Tamil, Movies/Hindi, Anime/Classic), never any one
    # show's own folder. This is checked FIRST because the sibling rule below
    # cannot see it: a language folder that currently happens to hold exactly one
    # show has no foreign sibling to trip on, so it looks "exclusive" by accident.
    #
    # Caught by the real-library dry run: `C:\Media\Series\Tamil` holds only
    # `Aindham Vedham (2024) S01 EP (01-08) …`, whose name DOES carry an S01
    # marker, so both the sibling rule and the season-marker gate passed it and
    # Phase A proposed renaming the user's Tamil language folder to
    # `Tamil {tmdb-274276}`. "Coincidentally one occupant today" is not
    # "exclusively this show's folder", and only structure can tell them apart —
    # depth cannot, because the tree is not uniform (Series/English/Classic/<Show>
    # is three levels, Series/Tamil/<Show> is two).
    #
    # A season sitting directly under a language folder is a FLAT show (one
    # folder is both show and season). Refusing here is exactly right: it is left
    # structurally untouched, and migrate_provider_tokens still fixes its id.
    _category_roots = {_norm_path(os.path.join(LOCAL_ROOT, sub))
                       for subs in CATEGORY_ROOTS.values() for sub in subs}
    if _norm_path(os.path.dirname(parent)) in _category_roots:
        return False, (f"parent is a language folder (direct child of a category root): "
                       f"{parent} — flat show, left structurally untouched")

    try:
        children = [e.path for e in os.scandir(parent) if e.is_dir()]
    except OSError as e:
        return False, f"could not list parent folder {parent}: {e}"
    show_id_str = str(show_tmdb_id)
    for child in children:
        ids = _folder_tmdb_owner_ids(library, child)
        if ids is None:
            continue  # untracked sibling — rides along for free (Mr.Robot S04 case)
        if not ids:
            return False, (f"'{os.path.basename(child)}' under '{os.path.basename(parent)}' has "
                            f"library-tracked content but no discoverable TMDB id (no folder "
                            f"token, no metadata.tmdb_id) — category-folder guard refuses it "
                            f"(ambiguous; run enrich_metadata first)")
        foreign = ids - {show_id_str}
        if foreign:
            return False, (f"'{os.path.basename(parent)}' holds another title's folder "
                            f"('{os.path.basename(child)}', TMDB id {sorted(foreign)[0]}) — "
                            f"category-folder guard refuses it")
    return True, None


def _find_show_tmdb_id(parent_basename, season_basename, season_entry):
    """The TMDB id for a season's show, tried in priority order, plus which
    source supplied it (for the report / dry-run print):

      1. The PARENT (show) folder's own existing token — the simplest,
         already-authoritative case (Phase A already done, e.g. on a re-run).
      2. The season folder's OWN token — the documented real gap this command
         exists to fix: 30 of 59 real season folders are the ONLY place their
         show's id survives (their show folder has none).
      3. metadata.tmdb_id on the season_map entry itself — written by a prior
         enrich_metadata run onto EVERY id of a show unit (season_maps
         included), even one where the folder-token stamp never landed.

    Returns (id, source_label), or (None, None) when no source has one. Never
    raises; never queries TMDB (id resolution is purely local/library-derived,
    so Phase A works even with no TMDB API key configured)."""
    for t in mvcommon.find_provider_tokens(parent_basename):
        if t["provider"] == "tmdb":
            return t["id"], "parent folder's existing token"
    for t in mvcommon.find_provider_tokens(season_basename):
        if t["provider"] == "tmdb":
            return t["id"], "season folder's own token"
    meta_id = (season_entry.get("metadata") or {}).get("tmdb_id")
    if meta_id:
        return meta_id, "metadata.tmdb_id"
    return None, None


def cmd_normalize_season_folders(arg=None, *flags):
    """Structural season-folder rename (IMP-U6 Steps 15-18): give each SHOW
    folder its TMDB token, then rename each SEASON folder underneath it to
    `<Show Name> Season <NN> (<season air year>)` — WITH NO ID ON THE SEASON.

    Usage: normalize_season_folders [id_or_prefix] [--apply]
    DRY-RUN by default (prints every `OLD -> NEW` it WOULD rename, both
    phases, plus a summary; writes nothing — no library mutation, no disk
    mutation, no report file). `--apply` performs it for real and writes a
    JSON audit report. No `--library` filter (unlike migrate_provider_tokens):
    this command only ever considers series/anime season_map entries, so a
    library filter would have nothing else to restrict.

    WHY: media-server folder names moved to a bare `{tmdb-<id>}` token on the
    SHOW folder (D11); the user separately asked for season folders to be
    SELF-IDENTIFYING (each is uploaded to a phone individually) — AND for
    season folders to carry NO id, because season and show ids collide in one
    numeric TMDB namespace (Friends S01's own TMDB *season* id, 4573, is
    "Late Night with Conan O'Brien" as a *show* id) and no media server reads
    a season-level token anyway, so writing one can only mislead a scanner.

    TWO PHASES, in this order (load-bearing — see "ORDERING" below):
      A — if a season's parent is a genuine, untokened SHOW folder (see THE
          CATEGORY-FOLDER GUARD), rename it to `<existing name>
          {tmdb-<id>}` (mvcommon.CANONICAL_TMDB_TOKEN_FMT — NEVER a hardcoded
          literal). The id is resolved via _find_show_tmdb_id (parent's own
          token -> season's own token -> season_map's metadata.tmdb_id).
      B — rename the season folder to `<Show Name> Season <NN> (<year>)`.
          Show Name + per-season air_date come from ONE cached
          `GET /tv/{tmdb_id}` call per show (_tmdb_get — the SAME TMDB
          plumbing/cache/api-key resolution every other TMDB command uses; no
          new HTTP path). Show Name is sanitized for Windows-illegal
          characters (_sanitize_folder_name_component) since this is the
          FIRST place in the codebase a raw external title becomes a folder
          name. A season with no `air_date` (or a failed /tv/{id} call) is
          SKIPPED, not given an invented year.

    THE CATEGORY-FOLDER GUARD (_season_parent_is_exclusive_to_show) — the most
    dangerous code in this command. A season's os.path.dirname() is ONLY ever
    treated as its show folder if EVERY direct subdirectory in it is either
    untracked by the library, or tracked EXCLUSIVELY by this SAME show's TMDB
    id (_folder_tmdb_owner_ids — TMDB id, not a library key: MediaVault's
    library ids embed the SEASON's own air year, so a show's own sibling
    seasons carry DIFFERENT library keys but share ONE TMDB id). A single
    sibling tracked under a DIFFERENT TMDB id — or tracked at all with no
    discoverable TMDB id anywhere (ambiguous) — fails the guard: that parent
    is a CATEGORY folder (`Classic` holds Friends/Peaky Blinders/The Wire/
    Chernobyl's flat folder as four unrelated direct children) and is left
    byte-identical. The guard checks EXACTLY ONE hop up, never further — see
    that function's docstring for why not-climbing is itself part of the
    safety (the Tamil counter-example: a language folder that currently,
    coincidentally, holds only one show). It runs PER GROUP (after that
    group's own TMDB id is resolved below), not per season, so every season
    of one show gets one consistent verdict.

    UNTRACKED SIBLING (Mr.Robot: 3 library-tracked seasons + one un-prepped
    `Mr.Robot.S04.BluRay.2019.1080p.DTS` directory, same show): the guard
    treats an untracked sibling as harmless and does NOT refuse it — it has no
    library entry to misattribute, and physically rides along for free when
    the parent is renamed. Refusing on ANY unknown directory (the naive
    version of this guard) would incorrectly refuse Mr.Robot itself.

    FLAT SHOWS (one folder is both show and season, e.g. `Chernobyl
    (Miniseries) 2019 … {tmdb-87108}` sitting directly in `Classic`) are
    left STRUCTURALLY UNTOUCHED — migrate_provider_tokens already fixes their
    id in place. A flat show sharing its category folder with other tracked
    shows is already excluded by the sibling-ownership guard above. A SOLO
    flat show (the only thing in its category folder right now, e.g. `Tamil`)
    would NOT be caught by sibling-ownership alone (nothing to conflict
    with) — so this command ALSO requires the season folder's own basename to
    embed a season marker (_season_folder_looks_season_shaped) before it is
    even considered a Phase A/B candidate. A flat show's own name never embeds
    one; a real season folder's name (even a release-style one) always does.
    This is a DELIBERATE addition beyond the literal "check every sibling"
    rule, adopted because it can only ever cause an EXTRA (safe) skip, never a
    wrongful rename — the "refuse a borderline case rather than rename it"
    instruction this guard was built to satisfy. (Flag for Step 18: if the
    real-library run finds a legitimate season folder skipped by this check,
    loosen the regex — never the sibling rule.)

    ORDERING — Phase A before Phase B, per show, NOT deepest-first: unlike
    cmd_migrate_provider_tokens's single flat deepest-first candidate list,
    this command's phases run SHALLOWEST-first per show. This is NOT about
    cmd_rename_folder's cascade (a directory rename moves whatever is inside
    it regardless of which level renamed first — a plain os.rename at either
    level composes correctly either way). It is about ID SOURCING:
    _find_show_tmdb_id can read an id off the season folder's OWN token — but
    Phase B's whole point is to STRIP that token. If a season's Phase B ran
    before its show's Phase A, the id would already be gone by the time Phase
    A went looking for it, and for the 30-of-59 shows whose ONLY surviving id
    is on the season folder, that id would be permanently lost. So: for a
    given show (grouped by the season's OWN computed parent — every season
    sharing that exact parent, e.g. Friends' S01/S02/…, is one group), Phase
    A's rename (if any) always completes BEFORE any of that group's Phase B
    renames begin. Between DIFFERENT shows, order is immaterial (independent
    subtrees); groups are processed in a stable, sorted order for
    reproducible dry-run output.

    IDEMPOTENT BY RE-DETECTION, no new state file (mirrors
    migrate_provider_tokens exactly): Phase A is skipped whenever the parent
    already carries ANY recognized tmdb token (mvcommon.has_tmdb_token);
    Phase B is skipped whenever the season folder's basename is ALREADY
    byte-identical to the computed `<Show> Season NN (year)` target. Re-
    detection for the CATEGORY-FOLDER GUARD itself additionally depends on
    every season's TMDB id staying DISCOVERABLE across runs — since Phase B's
    whole point is to remove the id from the season folder's name, `--apply`
    ALSO writes the group's resolved id to `metadata.tmdb_id` on every season
    in the group (the same field enrich_metadata already uses), so a season
    whose ONLY id source was its own now-stripped folder name is still
    identifiable — via metadata — on a later run.

    REPORT (only on --apply): `<LOCAL_ROOT>/migration_reports/
    season_folders_<UTC ISO8601 timestamp>.json` (same-second de-collision
    suffix as migrate_provider_tokens) shaped:
      {"scanned": N,                      # in-scope season_map entries considered
       "show_folders_tokened": [{"show_id", "old_folder", "new_folder", "id_source"}],
       "seasons_renamed": [{"season_id", "old_folder", "new_folder"}],
       "already_normalized": K,           # seasons already in final shape (no-op)
       "skipped": [{"season_id", "old_folder", "reason"}],   # guard/data-driven skips
       "errors": [...]}                   # cmd_rename_folder declines / RollbackHardFail
    A cmd_rename_folder PONR-crossed failure (RollbackHardFail) is caught and
    recorded in `errors` with the printed resume_cmd; the run CONTINUES with
    the next group ("Decision 7" convention, same as migrate_provider_tokens)
    — EXCEPT that a Phase A failure for a group aborts THAT group's Phase B
    (the parent may be in a torn state; re-run the printed resume_cmd, then
    re-run this command to pick up cleanly) — the aborted seasons are listed
    on the error record's `seasons_not_attempted`.

    SCOPE: only `type == "season_map"` entries under series/anime
    (category_of_id != "movies"/"other") are ever considered — mirrors
    cmd_enrich_metadata's own "IMP-D18: Others/sports is not on TMDB, never
    enrich it" exclusion (Others has no TMDB id to source from either).
    `multi_ep_alias` carries no folder_path and is never iterated directly
    here (only reached, harmlessly, as an owned descendant via
    _collect_folder_descendants, which already skips it)."""
    flist = list(flags)
    if arg and str(arg).startswith("--"):
        flist = [arg] + flist
        id_or_prefix = None
    else:
        id_or_prefix = arg or None
    apply = "--apply" in flist

    def _in_scope(mid):
        if id_or_prefix and not (mid == id_or_prefix or mid.startswith(id_or_prefix)):
            return False
        return True

    library = load_library()
    api_key = mvcommon.tmdb_api_key()

    # --- discovery: in-scope season_map entries (series/anime only). ---
    seasons = []
    for mid, entry in library.items():
        if entry.get("type") != "season_map":
            continue
        if category_of_id(mid) not in ("series", "anime"):
            continue
        if not entry.get("folder_path"):
            continue
        if not _in_scope(mid):
            continue
        seasons.append((mid, entry))
    seasons.sort(key=lambda t: t[0])
    scanned = len(seasons)

    # --- group by the season's OWN immediate parent (one Phase-A target per
    # parent; every season sharing that exact parent, e.g. a multi-season
    # show's S01/S02/…, is one group). The season-marker-shape gate is
    # evaluated per season, against the ORIGINAL on-disk tree, BEFORE any
    # rename in this run executes — so it never depends on this run's own
    # processing order. THE CATEGORY-FOLDER GUARD itself is evaluated per
    # GROUP, below (in the planning loop), AFTER that group's own TMDB id has
    # been resolved — it needs a real show identity to compare siblings
    # against (_folder_tmdb_owner_ids), and that identity can only be reliably
    # known once every season sharing this parent has had a chance to supply
    # it (the same season-to-season fallback the id-resolution step below
    # already does), not from any ONE season alone. ---
    groups = {}   # norm(parent) -> {"parent", "seasons": [(mid, entry, season_number, folder)]}
    skipped = []
    for mid, entry in seasons:
        folder = os.path.abspath(entry["folder_path"])
        season_number = _season_number_of(mid)
        if season_number is None:
            skipped.append({"season_id": mid, "old_folder": folder,
                             "reason": "could not parse a season number from this id"})
            continue
        basename = os.path.basename(folder)
        if not _season_folder_looks_season_shaped(basename):
            skipped.append({"season_id": mid, "old_folder": folder,
                             "reason": "folder name has no season marker (S01/Season 01/…) — "
                                       "treated as a possible flat show, left structurally alone"})
            continue
        parent = os.path.dirname(folder)
        g = groups.setdefault(_norm_path(parent), {"parent": parent, "seasons": []})
        g["seasons"].append((mid, entry, season_number, folder))

    # --- plan every rename (both phases), for EVERY group, regardless of
    # --apply — this is what makes the dry-run preview real (id + TMDB
    # name/year are actually resolved, not guessed) and is the SAME work
    # --apply needs, so it is done exactly once. ---
    mode = "APPLY" if apply else "DRY-RUN"
    print(f"=== NORMALIZE SEASON FOLDERS ({mode}) ===")
    if id_or_prefix:
        print(f"   > scope: ids == or startswith '{id_or_prefix}'")
    print(f"   > {scanned} season folder(s) in scope.\n")
    if not api_key:
        print("⚠️  No TMDB API key configured — Phase B (season renames) will be skipped for "
              "every show; only Phase A (show-folder tokening) can run.\n")

    plan = []
    already_normalized = 0

    for parent_norm in sorted(groups):
        g = groups[parent_norm]
        parent = g["parent"]
        g["seasons"].sort(key=lambda t: t[2])  # by season number
        parent_basename = os.path.basename(parent)

        # --- id resolution BEFORE any Phase B rename could strip it (load-bearing order — see docstring "ORDERING"). ---
        tmdb_id, id_source = None, None
        if mvcommon.has_tmdb_token(parent_basename):
            for t in mvcommon.find_provider_tokens(parent_basename):
                if t["provider"] == "tmdb":
                    tmdb_id, id_source = t["id"], "parent folder's existing token"
                    break
        if tmdb_id is None:
            for mid, entry, season_number, folder in g["seasons"]:
                tmdb_id, id_source = _find_show_tmdb_id(parent_basename, os.path.basename(folder), entry)
                if tmdb_id:
                    break
        if tmdb_id is None:
            for mid, entry, season_number, folder in g["seasons"]:
                skipped.append({"season_id": mid, "old_folder": folder,
                                 "reason": "no TMDB id known for this show (no token on the show "
                                           "or season folder, no metadata.tmdb_id) — has it been "
                                           "enriched? run enrich_metadata first"})
            continue

        # THE CATEGORY-FOLDER GUARD (_season_parent_is_exclusive_to_show) —
        # run HERE, per group, against the group's own just-resolved TMDB id
        # (defect 1 fix: TMDB id, not a library key, is the identity every
        # direct sibling of `parent` is compared against — see that
        # function's docstring + _folder_tmdb_owner_ids). A guard failure
        # skips the WHOLE group, so every season of one show gets one
        # consistent verdict rather than a per-season one.
        exclusive, reason = _season_parent_is_exclusive_to_show(library, parent, tmdb_id)
        if not exclusive:
            for mid, entry, season_number, folder in g["seasons"]:
                skipped.append({"season_id": mid, "old_folder": folder, "reason": reason})
            continue

        # Cosmetic, report-only label (the documented report's "show_id"
        # field) — the lowest-season-number entry's own library-style id.
        # The real identity used for the rename/guard above is `tmdb_id`,
        # already recorded verbatim in new_parent_basename/id_source below.
        show_id = _show_id_of(g["seasons"][0][0], g["seasons"][0][1], library)

        will_stamp = not mvcommon.has_tmdb_token(parent_basename)
        # One token per folder (user decision, 2026-09-22). A show folder carrying
        # a stale NON-tmdb provider token — the real cases are
        # `Dark (2017) [tvdbid-334824]` and `Fringe (2008) [tvdbid-82066]` — would
        # otherwise end up with two (`Dark (2017) [tvdbid-334824] {tmdb-70523}`).
        # Both ids name the same show so it is harmless, but cluttered, and the
        # tvdb one is dead weight: MediaVault is TMDB-for-everything and refuses
        # -tvdbid (IMP-D22), and Plex ignores the `tvdbid` spelling entirely
        # (verified on real servers, test row S8). Strip any non-tmdb token off
        # the basename before appending the canonical one, using each token's own
        # span so the rest of the name survives byte-identically.
        stamp_base = parent_basename
        if will_stamp:
            for tok in sorted(mvcommon.find_provider_tokens(stamp_base),
                              key=lambda t: t["span"][0], reverse=True):
                if tok["provider"] == "tmdb":
                    continue  # cannot happen under will_stamp; belt and braces
                s, e = tok["span"]
                stamp_base = (stamp_base[:s] + stamp_base[e:]).replace("  ", " ").strip()
        new_parent_basename = (f"{stamp_base} {mvcommon.CANONICAL_TMDB_TOKEN_FMT.format(id=tmdb_id)}"
                                if will_stamp else parent_basename)
        new_parent_path = os.path.join(os.path.dirname(parent), new_parent_basename)
        if will_stamp:
            print(f"   [show]   {parent} -> {new_parent_path}  (id source: {id_source})")

        season_plan = []
        if not api_key:
            for mid, entry, season_number, folder in g["seasons"]:
                skipped.append({"season_id": mid, "old_folder": folder,
                                 "reason": "no TMDB API key configured — season rename needs the "
                                           "show name/air year"})
        else:
            detail = _tmdb_get(f"{TMDB_API_ROOT}/tv/{tmdb_id}", {}, api_key)
            if not isinstance(detail, dict) or not detail.get("name"):
                for mid, entry, season_number, folder in g["seasons"]:
                    skipped.append({"season_id": mid, "old_folder": folder,
                                     "reason": f"TMDB GET /tv/{tmdb_id} failed or returned no name "
                                               f"— season(s) not renamed"})
            else:
                show_name = _sanitize_folder_name_component(detail.get("name"))
                year_by_season = {}
                for s in detail.get("seasons") or []:
                    if not isinstance(s, dict):
                        continue
                    sn = s.get("season_number")
                    air = s.get("air_date")
                    if isinstance(sn, int) and isinstance(air, str) and re.match(r"\d{4}", air):
                        year_by_season[sn] = air[:4]

                for mid, entry, season_number, folder in g["seasons"]:
                    year = year_by_season.get(season_number)
                    if not year:
                        skipped.append({"season_id": mid, "old_folder": folder,
                                         "reason": f"TMDB has no air_date for season {season_number} "
                                                   f"— not renamed (never inventing a year)"})
                        continue
                    new_season_name = f"{show_name} Season {season_number:02d} ({year})"
                    if os.path.basename(folder) == new_season_name:
                        already_normalized += 1
                        continue
                    new_folder_display = os.path.join(new_parent_path, new_season_name)
                    print(f"   [season] {folder} -> {new_folder_display}")
                    season_plan.append({"season_id": mid, "old_folder": folder,
                                         "new_name": new_season_name,
                                         "new_folder": new_folder_display})

        all_season_ids = [mid for mid, entry, season_number, folder in g["seasons"]]
        plan.append({"parent": parent, "show_id": show_id, "tmdb_id": tmdb_id, "will_stamp": will_stamp,
                     "new_parent_basename": new_parent_basename, "new_parent_path": new_parent_path,
                     "id_source": id_source, "seasons": season_plan,
                     "all_season_ids": all_season_ids})

    if not apply:
        would_token = sum(1 for p in plan if p["will_stamp"])
        would_rename = sum(len(p["seasons"]) for p in plan)
        print(f"\n=== DRY-RUN === scanned={scanned} show_folders_would_token={would_token} "
              f"seasons_would_rename={would_rename} already_normalized={already_normalized} "
              f"skipped={len(skipped)}")
        print("   (dry-run: nothing was written — re-run with --apply to perform it.)")
        return

    # --- APPLY: execute Phase A then Phase B, per group (load-bearing order —
    # see docstring "ORDERING"). Every rename goes through the EXISTING,
    # crash-safe cmd_rename_folder — never reimplemented here. ---
    show_folders_tokened = []
    seasons_renamed = []
    errors = []

    for p in plan:
        parent, show_id = p["parent"], p["show_id"]
        actual_parent = parent

        # Persist this show's TMDB id onto EVERY season_map entry in the group
        # (all_season_ids — the FULL group, not just season_plan/p["seasons"],
        # which only lists seasons THIS run is about to rename and would skip
        # one that is already in its final, id-free shape) as metadata.tmdb_id
        # — a DURABLE, folder-name-INDEPENDENT identity record (the same
        # field/semantics enrich_metadata already writes). Without this, a
        # season whose ONLY id source was its own folder name (the common
        # real-library case — see id_source) becomes UNIDENTIFIABLE to
        # _folder_tmdb_owner_ids the moment its name carries no id (whether
        # stripped by THIS run's Phase B or already gone before it), and a
        # FUTURE run's category-folder guard would then see it as "tracked
        # content, no discoverable TMDB id" and wrongly refuse the WHOLE show —
        # breaking the documented "idempotent by re-detection" guarantee.
        # Written regardless of this run's rename outcome (identity is already
        # confirmed once tmdb_id is resolved, independent of whether the
        # physical rename below succeeds) and BEFORE Phase A/B so a later
        # RollbackHardFail still leaves the id durably recorded for a retry.
        try:
            meta_tmdb_id = int(p["tmdb_id"])
        except (TypeError, ValueError):
            meta_tmdb_id = p["tmdb_id"]
        live = load_library()
        meta_changed = False
        for season_id in p["all_season_ids"]:
            ent = live.get(season_id)
            if ent is None:
                continue
            meta = ent.setdefault("metadata", {})
            if meta.get("tmdb_id") != meta_tmdb_id:
                meta["tmdb_id"] = meta_tmdb_id
                meta_changed = True
        if meta_changed:
            save_library(live)

        if p["will_stamp"]:
            try:
                ok = cmd_rename_folder(parent, p["new_parent_basename"])
            except RollbackHardFail as hf:
                print(f"⚠️  IRREVERSIBLE — show folder moved but the library rewrite failed: "
                      f"{hf.state} — {hf.reason}")
                print(f"   > To finish it: {hf.resume_cmd}")
                errors.append({"season_id": None, "show_id": show_id, "old_folder": parent,
                                "new_folder": p["new_parent_path"],
                                "error": f"{hf.state}: {hf.reason}", "resume_cmd": hf.resume_cmd,
                                "seasons_not_attempted": [s["season_id"] for s in p["seasons"]]})
                continue  # parent may be in a torn state — do not touch its seasons this run
            if not ok:
                errors.append({"season_id": None, "show_id": show_id, "old_folder": parent,
                                "new_folder": p["new_parent_path"],
                                "error": "cmd_rename_folder declined (see console output above)",
                                "seasons_not_attempted": [s["season_id"] for s in p["seasons"]]})
                continue
            show_folders_tokened.append({"show_id": show_id, "old_folder": parent,
                                          "new_folder": p["new_parent_path"], "id_source": p["id_source"]})
            actual_parent = p["new_parent_path"]

        for s in p["seasons"]:
            # Re-point onto the REAL, current parent: if Phase A renamed it
            # above, the season folder physically moved along (cmd_rename_
            # folder's own descendant cascade) — its OWN basename is
            # unchanged by that move, so a plain join is enough (a season
            # folder is always a direct child of its own parent, never
            # nested deeper).
            cur_folder = os.path.join(actual_parent, os.path.basename(s["old_folder"]))
            try:
                ok = cmd_rename_folder(cur_folder, s["new_name"])
            except RollbackHardFail as hf:
                print(f"⚠️  IRREVERSIBLE — season folder moved but the library rewrite failed: "
                      f"{hf.state} — {hf.reason}")
                print(f"   > To finish it: {hf.resume_cmd}")
                errors.append({"season_id": s["season_id"], "old_folder": cur_folder,
                                "new_folder": os.path.join(actual_parent, s["new_name"]),
                                "error": f"{hf.state}: {hf.reason}", "resume_cmd": hf.resume_cmd})
                continue
            if ok:
                seasons_renamed.append({"season_id": s["season_id"], "old_folder": cur_folder,
                                         "new_folder": os.path.join(actual_parent, s["new_name"])})
            else:
                errors.append({"season_id": s["season_id"], "old_folder": cur_folder,
                                "new_folder": os.path.join(actual_parent, s["new_name"]),
                                "error": "cmd_rename_folder declined (see console output above)"})

    report = {"scanned": scanned, "show_folders_tokened": show_folders_tokened,
              "seasons_renamed": seasons_renamed, "already_normalized": already_normalized,
              "skipped": skipped, "errors": errors}
    report_dir = os.path.join(LOCAL_ROOT, "migration_reports")
    os.makedirs(report_dir, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    report_path = os.path.join(report_dir, f"season_folders_{ts}.json")
    # Same-second collision guard as cmd_migrate_provider_tokens's own report.
    n = 1
    while os.path.exists(report_path):
        report_path = os.path.join(report_dir, f"season_folders_{ts}-{n}.json")
        n += 1
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)

    print(f"\n=== APPLIED === scanned={scanned} show_folders_tokened={len(show_folders_tokened)} "
          f"seasons_renamed={len(seasons_renamed)} already_normalized={already_normalized} "
          f"skipped={len(skipped)} errors={len(errors)}")
    print(f"   > report: {report_path}")


# ==========================================
#   EXTRAS DATA LAYER (Specials / Trailers / …)
# ==========================================
# IMP-D19 — an "extras" block is an OPTIONAL nested field on the TITLE entry (the
# season_map for series/anime/others, the movie leaf for movies) that carries
# extra videos — a Specials / Trailers / Behind-the-Scenes subfolder — so they
# run through the SAME push/replace/fetch/restore lifecycle as main content.
#
# Shape (Card A2 — GROUPED per source folder; docs/feature-extras/DECISIONS.md):
#   entry["extras"] = {"groups": {
#       "<group_rel>": {                    # extra folder path RELATIVE to the title folder
#           "added_date": "YYYY-MM-DD",     # e.g. "Specials", "Extra", nested "Bonus/Trailers"
#           "items": [ {filename, sub_rel, short_id, hash, status, uploaded,
#                       search_term, tech_spec, [split_info], [re_hashed]}, ...]}}}
# Each item mirrors a leaf's scan-derived fields. `sub_rel` is the file's path
# WITHIN its group (just the filename when the group is flat). The "groups"
# wrapper keeps an arbitrary folder name from ever colliding with a reserved key.
# An item's on-disk / restore path = <title folder_path>/<group_rel>/<sub_rel>.
# De-dup / additive identity = per group, by normalized `sub_rel`.

# Internal folders never scanned as extras content (the *_DIR_NAME values are the
# shared mvcommon constants; same idea as cmd_scan_unprepped's walk-excludes).
_EXTRAS_EXCLUDE_DIRS = {SPLIT_DIR_NAME, CHECKSUM_DIR_NAME, RESTORE_DIR_NAME,
                        ".git", ".idea", "__pycache__"}


def _extras_rel_posix(rel):
    """Normalize an os.path.relpath result to forward-slash form so a stored
    group_rel/sub_rel is portable and the short_id seed is deterministic. The
    realistic one-level-flat case (no separators) is unchanged; a nested spec
    gains '/' separators."""
    return rel.replace(os.sep, "/")


def _extras_item_paths(entry):
    """Yield (group_rel, on_disk_path, item) for every extras item nested on
    `entry`, across ALL groups (IMP-D19 Step 7 — the whole-library iterators'
    "known files" seam).

    on_disk_path = <title folder_path>/<group_rel>/<sub_rel>, composed with the
    SAME posix→os.sep idiom push_one_extra / replace_one_extra /
    restore_one_extra use, so every consumer agrees on one canonical location.

    Safe on EVERY entry type: reads only the optional `extras` block plus
    `folder_path` (present on both title shapes — season_map and movie leaf —
    per ENTRY_TYPE_KEYS) and yields nothing when either is absent (e.g. a
    multi_ep_alias). No leaf-only key is ever dereferenced."""
    extras = entry.get("extras")
    title_folder = entry.get("folder_path")
    if not extras or not title_folder:
        return
    for group_rel, group in (extras.get("groups") or {}).items():
        group_os = group_rel.replace("/", os.sep)
        for item in group.get("items", []):
            sub_os = (item.get("sub_rel") or "").replace("/", os.sep)
            yield group_rel, os.path.join(title_folder, group_os, sub_os), item


def _extras_title_id(library, mid):
    """Resolve `mid` to the id of the TITLE entry an extras block attaches to:

      - season_map (series/anime/others) -> itself (it IS the title)
      - episode leaf / multi_ep_alias     -> its parent_id season_map
      - movie leaf (no usable parent)     -> itself

    Returns the title id, or None if `mid` is absent or cannot resolve to a
    title. Uses _resolve_alias (defined later, in the push/group region) to
    follow a combined-episode alias one hop to its primary leaf first."""
    if mid not in library:
        return None
    real_id, entry = _resolve_alias(library, mid)
    if entry.get("type") == "multi_ep_alias":
        return None  # alias whose primary is missing — no resolvable title
    if entry.get("type") == "season_map":
        return real_id
    # Leaf: an episode (has a parent_id season_map) -> the parent; a movie -> itself.
    parent_id = entry.get("parent_id")
    if parent_id and parent_id in library:
        return parent_id
    return real_id


def scan_extras_folders(folder_specs, title_folder_path, title_id):
    """Recursively scan each extra folder for videos; return the scanned groups
    keyed by group_rel: {group_rel: {"items": [item, ...]}}.

    `folder_specs` is a LIST of folder paths (the caller has already `;`-split
    and flattened the repeated --extras flag); a single string is tolerated.
    Each folder spec becomes ONE group whose key is its path relative to the
    title folder (group_rel), and every VIDEO_EXTENSIONS file inside it is
    collected recursively as one item with sub_rel = the file's path within the
    group. Each item is hashed (calculate_file_hash) and tech-scanned
    (get_tech_specs); status="local_ready", uploaded=False. short_id is seeded
    from title_id + group_rel + sub_rel (NOT the bare filename) so the same
    basename in two groups never collides. PURE: no upload, no library/device
    I/O (the .endswith match also catches the real `.mkv.mkv` double extension)."""
    if isinstance(folder_specs, str):
        folder_specs = [folder_specs]

    scanned = {}
    for raw in folder_specs:
        spec = (raw or "").strip().strip('"').strip("'").strip()
        if not spec:
            continue
        folder = os.path.abspath(spec)
        if not os.path.isdir(folder):
            print(f"⚠️  Extras folder not found, skipping: {folder}")
            continue

        # group_rel = the folder's path relative to the title folder. Fall back
        # to its basename if it is not under the title folder (or on another
        # drive — os.path.relpath raises ValueError across Windows volumes).
        try:
            group_rel = os.path.relpath(folder, title_folder_path)
            if group_rel == os.pardir or group_rel.startswith(os.pardir + os.sep):
                group_rel = os.path.basename(folder)
        except ValueError:
            group_rel = os.path.basename(folder)
        group_rel = _extras_rel_posix(group_rel)

        # De-dup within this scan against any items the same group already
        # collected (e.g. the same folder passed twice, or a basename collision).
        prior = scanned.get(group_rel)
        seen_sub = {_extras_rel_posix(it["sub_rel"]) for it in prior["items"]} if prior else set()

        for root, dirs, files in os.walk(folder):
            dirs[:] = [d for d in dirs if d not in _EXTRAS_EXCLUDE_DIRS]
            for fn in sorted(files):
                if not fn.lower().endswith(VIDEO_EXTENSIONS):
                    continue
                file_path = os.path.join(root, fn)
                sub_rel = _extras_rel_posix(os.path.relpath(file_path, folder))
                if sub_rel in seen_sub:
                    continue
                seen_sub.add(sub_rel)

                short_id = generate_short_id(f"{title_id}::{group_rel}/{sub_rel}")
                name_no_ext, ext = os.path.splitext(fn)
                scanned.setdefault(group_rel, {"items": []})["items"].append({
                    "filename": fn,
                    "sub_rel": sub_rel,
                    "short_id": short_id,
                    "hash": calculate_file_hash(file_path),
                    "status": "local_ready",
                    "uploaded": False,
                    "search_term": f"{name_no_ext} [{short_id}]{ext}",
                    "tech_spec": get_tech_specs(file_path),
                })

    # Deterministic item order within every group (empty folders produce no group).
    for group in scanned.values():
        group["items"].sort(key=lambda it: it["sub_rel"])
    return scanned


# [IMP-D20] Local checksum-sidecar parity for extras. Main content gets a
# `uid` + `<short_id>.sha256` master sidecar at PREP time (cmd_prep, main.py
# ~1029-1041) and per-chunk `checksums/<chunk>.sha256` sidecars at PUSH time
# (cmd_push, main.py ~4500-4518) — the on-disk disaster-recovery record a
# library can be rebuilt from if library_*.json is lost. IMP-D19's lean
# push_one_extra deliberately shipped WITHOUT any of this (Step-3 bake-off,
# Candidate B); this closes that gap for extras, mirroring format/naming
# exactly. The chunk-sidecar half lives in push_one_extra (main.py, PUSH
# region); the master-sidecar half lives here (register/merge time).
def _extras_master_sidecar_path(title_folder, group_rel, item):
    """The on-disk path for one extras item's master `<short_id>.sha256`
    sidecar: `<title_folder>/<group_rel>/<dirname(sub_rel)>/<short_id>.sha256`
    — the item's OWN directory, composed exactly like `_extras_item_paths`
    (for the common flat-group case that's just the group folder itself, e.g.
    `.../Specials/<short_id>.sha256`)."""
    group_os = group_rel.replace("/", os.sep)
    sub_os = (item.get("sub_rel") or "").replace("/", os.sep)
    on_disk_path = os.path.join(title_folder, group_os, sub_os)
    return os.path.join(os.path.dirname(on_disk_path), f"{item.get('short_id')}.sha256")


def _write_extras_master_sidecar(title_folder, group_rel, item, overwrite=False):
    """Best-effort write of one extras item's master `<short_id>.sha256`
    sidecar — `f"{hash} *{filename}"`, no trailing newline, exactly cmd_prep's
    format (main.py:1041) — using the item's STORED `hash`/`filename` (it never
    re-hashes the file on disk). That is what makes this safe to call from the
    D19-B1 skip path (main.py ~3845-3851): the on-disk file there is a dummy,
    but the sidecar is written from the LIBRARY's stored (real) hash, so it
    never encodes the dummy's bytes.

    Deliberately writes NO `uid` file: cmd_prep's `uid` sidecar is
    FOLDER-scoped (one leaf owns its whole folder exclusively), but one extras
    GROUP folder (e.g. `Specials/`) holds MANY items sharing that folder —
    there is no single id to stamp a folder-level `uid` with, so extras skip
    it entirely (the per-item `<short_id>.sha256` is the only master sidecar).

    `overwrite=False` (the default, used by the back-fill call sites): skip
    silently if the sidecar already exists — IMP-D20's back-fill only repairs
    a MISSING sidecar; it never rewrites one already present, since every
    default-mode call site here is reached with a stored hash that has NOT
    changed since any prior write (cheaper than a content-compare, and the
    bytes would be identical anyway). `overwrite=True` (used only after a
    genuine re-master changes the stored hash) always (re)writes, because the
    short_id-keyed path is unchanged across a re-master, so a stale sidecar
    from the OLD hash would otherwise survive under the new one.

    Never raises — a sidecar failure must NEVER abort a scan/merge or a push,
    exactly cmd_prep's contract (main.py:1042-1043)."""
    item_hash = item.get("hash")
    filename = item.get("filename")
    short_id = item.get("short_id")
    if not title_folder or not item_hash or not filename or not short_id:
        return
    sha_path = _extras_master_sidecar_path(title_folder, group_rel, item)
    if not overwrite and os.path.exists(sha_path):
        return
    try:
        with open(sha_path, 'w') as f:
            f.write(f"{item_hash} *{filename}")
    except Exception as e:
        print(f"⚠️ Warning: Could not write extras sidecar file: {e}")


def merge_extras_into_title(library, title_id, scanned):
    """Additively / idempotently merge `scanned` (the output of
    scan_extras_folders) into the title entry's extras.groups:

      - a NEW group_rel  -> a new group, stamped with today's `added_date`;
      - an EXISTING group -> items merged by normalized sub_rel:
            * a new sub_rel is appended — UNLESS its file is dummy-sized
              (tech_spec.size_bytes < DUMMY_MAX_BYTES): such a file can never be
              pushed (push_one_extra refuses dummy uploads), so it is skipped
              with a warning instead of registered as a forever-pending item,
            * an unchanged one (same hash) is a no-op,
            * one whose hash CHANGED is updated and reset to the unblessed,
              needs-re-push state (uploaded=False, status="local_ready", any
              stale split_info / re_hashed dropped) — UNLESS the stored item is
              CLOUD-BEARING (uploaded truthy or status in onboarded/archived/
              restored_local) and the candidate is dummy-sized: that is a
              re-scan over the dummy that replace left on disk (IMP-D19 Step
              12c, D19-B1), and the item is kept exactly as stored, with a
              warning.

    So adding Specials then Trailers later equals adding both at once, and
    re-adding an unchanged group is a no-op — including over an ARCHIVED group,
    whose dummies on disk can no longer clobber the stored real hashes. Mutates
    and returns `library`."""
    entry = library.get(title_id)
    if entry is None:
        return library  # caller resolves title_id first; nothing to attach to

    # [IMP-D20] title_folder anchors every master-sidecar path this function
    # writes below (see _extras_master_sidecar_path). Both physical title
    # shapes (movie leaf, series/anime/others season_map) carry folder_path
    # per ENTRY_TYPE_KEYS.
    title_folder = entry.get("folder_path", "")

    groups = entry.setdefault("extras", {}).setdefault("groups", {})
    today = datetime.now().strftime("%Y-%m-%d")

    for group_rel, gdata in scanned.items():
        if group_rel not in groups:
            groups[group_rel] = {"added_date": today, "items": []}
        target = groups[group_rel]
        by_sub = {_extras_rel_posix(it["sub_rel"]): it for it in target["items"]}

        for item in gdata.get("items", []):
            key = _extras_rel_posix(item["sub_rel"])
            existing = by_sub.get(key)
            # [IMP-D19 Step 12c / D19-B1] Dummy-sized candidate detection. The
            # scan stamped tech_spec.size_bytes from the very file it hashed
            # (get_tech_specs stats it; the same threshold cmd_prep:1004 and
            # cmd_check use). None = size unknown (MediaInfo parse error).
            cand_size = (item.get("tech_spec") or {}).get("size_bytes")
            if existing is None:
                # Layer-1 NEW-item guard: never REGISTER a known dummy-sized
                # file (e.g. --extras pointed at a folder of reclaim dummies).
                # It could never be pushed anyway — push_one_extra refuses
                # dummy-sized uploads (layer 2) — so registering it would only
                # create a forever-failing pending item. An UNKNOWN size still
                # registers: layer 2's own os.path.getsize is authoritative at
                # push time, so a real file with a failed tech-scan stays usable.
                if cand_size is not None and cand_size < DUMMY_MAX_BYTES:
                    print(f"⚠️  Skipping extras file {group_rel}/{key}: dummy-sized "
                          f"({cand_size:,} B < {DUMMY_MAX_BYTES:,} B) — looks like a "
                          f"space-reclaim dummy, not registering it as an extra.")
                    continue
                target["items"].append(item)
                by_sub[key] = item
                # [IMP-D20] Master sidecar at register time (mirrors cmd_prep).
                _write_extras_master_sidecar(title_folder, group_rel, item)
            elif existing.get("hash") != item.get("hash"):
                # [IMP-D19 Step 12c / D19-B1] Layer-1 CLOUD-BEARING guard — the
                # extras analogue of cmd_prep's early-skip (main.py:984-1001).
                # When the stored item already asserts a cloud copy (uploaded
                # truthy OR status in the onboarded/archived/restored_local
                # family) and the re-scanned file is dummy-sized, this is a
                # re-scan OVER the dummy that the replace phase left on disk —
                # NOT a re-master. Resetting would swap the REAL hash/split_info
                # for the dummy's and the next push would upload the dummy OVER
                # the real cloud copy (unrecoverable: the real local bytes were
                # already reclaimed). Keep the item exactly as stored. An
                # UNKNOWN size is treated as dummy-sized here — fail-safe
                # toward no-clobber. A REAL-sized changed file (a genuine
                # re-master) still resets below, exactly as before.
                if (existing.get("uploaded") or existing.get("status") in
                        ("onboarded", "archived", "restored_local")) and \
                        (cand_size is None or cand_size < DUMMY_MAX_BYTES):
                    print(f"⚠️  Skipping extras re-scan of {group_rel}/{key}: already "
                          f"pushed/archived and the file on disk is dummy-sized — "
                          f"refusing to clobber the cloud-bearing item (kept as stored).")
                    # [IMP-D20] Back-fill even though the item is protected/kept
                    # exactly as stored: write-if-missing only, from the STORED
                    # (real) hash — never the dummy on disk — so a lost/never-
                    # written master sidecar for an already-archived extra is
                    # still repaired by a re-run, and the D19-B1 skip above
                    # cannot defeat it.
                    _write_extras_master_sidecar(title_folder, group_rel, existing)
                    continue
                # Bytes changed: refresh scan-derived fields and return the item
                # to the unblessed / needs-re-push state — its cloud copy and any
                # chunk hashes are now stale. Mirrors cmd_push clearing re_hashed
                # so a later split-restore re-blesses for the NEW bytes rather
                # than false-mismatching against a stale canonical hash.
                existing["hash"] = item.get("hash")
                existing["tech_spec"] = item.get("tech_spec")
                existing["uploaded"] = False
                existing["status"] = "local_ready"
                existing.pop("split_info", None)
                existing.pop("re_hashed", None)
                # [IMP-D20] Genuine re-master: the short_id-keyed sidecar path is
                # unchanged, so a prior sidecar (if any) now encodes the STALE
                # hash — force-refresh it to the new stored hash.
                _write_extras_master_sidecar(title_folder, group_rel, existing, overwrite=True)
            else:
                # [IMP-D20] identical hash -> idempotent no-op for the item's
                # DATA, but still back-fill a MISSING master sidecar (never
                # overwrites one already present) so a plain re-scan repairs a
                # sidecar an older (pre-IMP-D20) run never wrote.
                _write_extras_master_sidecar(title_folder, group_rel, existing)

        target["items"].sort(key=lambda it: it["sub_rel"])

    return library


def _extras_scan_merge_and_save(library, mid, extras):
    """Resolve `mid` to its title id, scan the extras folders, merge into the
    library, save, and print a concise summary.  Called by cmd_prep /
    cmd_prep_season / cmd_add_extras (prep-phase only — no upload)."""
    title_id = _extras_title_id(library, mid)
    if not title_id:
        print(f"⚠️  Could not resolve a title entry for extras (id: {mid}) — skipping.")
        return
    title_entry = library.get(title_id, {})
    title_folder = title_entry.get("folder_path", "")
    scanned = scan_extras_folders(extras, title_folder, title_id)
    if not scanned:
        print("⚠️  No video files found in any extras folder — nothing merged.")
        return
    merge_extras_into_title(library, title_id, scanned)
    save_library(library)
    # [IMP-C25] After the save — best-effort, never journalled (extras have no journal)
    # and never raising (cmd_prep calls this AFTER its journal committed): record the
    # identity facts of every item this scan registered or refreshed, the SHA-1 from
    # the scan's own hash pass. An item the merge did not take (a dummy-sized new file,
    # a cloud-bearing item kept as stored over its dummy) is skipped — its scanned
    # bytes are not the ones the library records.
    try:
        groups = library[title_id].get("extras", {}).get("groups", {})
        for group_rel, gdata in scanned.items():
            stored = {_extras_rel_posix(it.get("sub_rel", "")): it
                      for it in groups.get(group_rel, {}).get("items", [])}
            for item in gdata.get("items", []):
                kept = stored.get(_extras_rel_posix(item["sub_rel"]))
                if kept is not None and kept.get("hash") and kept.get("hash") == item.get("hash"):
                    gpcapture.capture_after_extras_scan(title_folder, title_id, group_rel, kept)
    except Exception:
        pass
    total = sum(len(g["items"]) for g in scanned.values())
    groups_str = ", ".join(
        f"{k} ({len(v['items'])} item{'s' if len(v['items']) != 1 else ''})"
        for k, v in scanned.items()
    )
    print(f"✅ Extras merged into {title_id}: {groups_str} ({total} total item{'s' if total != 1 else ''}).")


def cmd_prep_season(base_id, folder_path, extras=None, extras_size=None):
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

    # IMP-D19: scan+merge extras into the season_map after all episodes are prepped
    if extras:
        library = load_library()
        _extras_scan_merge_and_save(library, base_id, extras)


def _check_title_extras(library, manual_id):
    """IMP-D3: hash-verify a title's extras alongside cmd_check's own leaf check.
    Mirrors the D19 Step 4/6 precedent (replace_title_extras / restore_title_extras
    wired unconditionally off ANY leaf of a title, including an episode — checking
    one episode also checks the season's extras). `archived` items are on-disk
    DUMMIES: skipped, never hash-compared (a dummy failing against the real
    stored hash is NOT corruption). Silent when the title has no extras. Never
    raises; read-only."""
    title_id = _extras_title_id(library, manual_id)
    if not title_id or title_id not in library:
        return
    for group_rel, full_path, item in _extras_item_paths(library[title_id]):
        label = f"[extras: {title_id} / {group_rel}/{item.get('sub_rel')}]"
        if item.get("status") == "archived":
            continue  # on-disk is a dummy by design — not a corruption check
        if not os.path.exists(full_path):
            print(f"❌ {label} File missing!")
            continue
        actual_hash = calculate_file_hash(full_path)
        if actual_hash == item.get("hash"):
            print(f"✅ {label} PASS: Verified.")
        else:
            print(f"❌ {label} FAIL: Hash mismatch!")


def cmd_check(manual_id):
    print(f"--- CHECKING: {manual_id} ---")
    library = load_library()
    if manual_id not in library: print("❌ ID not found."); return
    real_id, entry = _resolve_alias(library, manual_id)
    if real_id != manual_id:
        print(f"ℹ️  {manual_id} is part of the combined file registered as {real_id} — operating on that.")
        manual_id = real_id

    file_path = os.path.join(entry['folder_path'], entry['filename'])
    if not os.path.exists(file_path):
        print("❌ File missing!")
    # Dumb check for dummy file
    elif os.path.getsize(file_path) < DUMMY_MAX_BYTES:
        print("⚠️ Dummy file detected (already archived). Skipping hash check.")
    else:
        actual_hash = calculate_file_hash(file_path)
        if actual_hash == entry['hash']:
            print("✅ PASS: Verified.\n")
        else:
            print("❌ FAIL: Hash mismatch!\n")

    # IMP-D3: also verify the title's extras (see _check_title_extras).
    _check_title_extras(library, manual_id)


# v2 (IMP-C25, OD-4 ruling): every v1 key unchanged + optional per-chunk identity keys
# (gpcapture.MVMETA_KEYS) — see write_remote_mvmeta.
MVMETA_SCHEMA_VERSION = 2


def write_remote_mvmeta(adb_base, remote_target_dir, manual_id, entry, identity=None):
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

    v2 (IMP-C25, OD-4 ruling): every v1 key is unchanged; each `chunks` item may
    also carry `sha1` + `dedup_key` (Google Photos' content key for those exact
    bytes), `date_utc` (their Matroska DateUTC, the time Google Photos shows) and
    `uploaded_name` (the exact name pushed) — only the ones known. `identity` maps
    a chunk's SHA-256 to those facts (gpcapture.mvmeta_identity); without it the
    v1 content is written under version 2.
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

        # [IMP-C25] v2: the known identity facts of each chunk's bytes (OD-4).
        if isinstance(identity, dict):
            for chunk in chunks:
                facts = identity.get(chunk["hash"]) if isinstance(chunk.get("hash"), str) else None
                if isinstance(facts, dict):
                    chunk.update((k, facts[k]) for k in gpcapture.MVMETA_KEYS if facts.get(k))

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


def _device_facts(adb_base):
    """[IMP-C25] The pushing device's timezone for the identity capture (PLAN Step
    5b): ONE best-effort, read-only query `getprop persist.sys.timezone; date +%z`
    (15 s timeout) through the push's own `adb_base`, so a pinned `-s <serial>`
    reaches it too. Returns {device_tz, device_tz_offset_min} with only the facts
    it could read (gpcapture.device_facts_from_output); ANY problem — no device, a
    timeout, a runner without stdout — gives {}. Called only after a push committed
    (cmd_push after journal.commit(); push_title_extras after an extra uploaded);
    never journalled, never raises."""
    try:
        result = subprocess.run(adb_base + ["shell", "getprop persist.sys.timezone; date +%z"],
                                capture_output=True, text=True, encoding="utf-8",
                                errors="replace", timeout=15)
        if getattr(result, "returncode", None) != 0:
            return {}
        return gpcapture.device_facts_from_output(getattr(result, "stdout", None))
    except Exception:
        return {}


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


# ==========================================
#   IMP-D19 Step 3 — EXTRAS UPLOAD PHASE (isolated, O-1 resumable per file)
# ==========================================
# Candidate B = ISOLATED DUPLICATION. push_one_extra re-implements ONLY the
# upload steps an extra needs — the .partial+rename+retry idiom, chunk hashing,
# the mvmeta sidecar, the status flips — WITHOUT cmd_push's leaf-specific
# complexity (no chunk_range, no eager re-hash, no temp_dir plumbing, and no
# RollbackJournal/PONR). cmd_push is left byte-for-byte untouched except for the
# one-line wire at its success marker, so the proven push/rollback path carries
# zero new blast radius. The .partial+rename+retry idiom now appears twice; that
# duplication is the deliberate, accepted tradeoff (Step 3 candidate B).
#
# ROLLBACK (E1, change-gate cleared): the extras upload is its OWN phase with NO
# point-of-no-return. The master extra file always survives, so a half-pushed
# extra is resumable on a plain re-run — exactly the O-1 contract cmd_push uses,
# but kept entirely separate from the main-content journal. Nothing here touches
# RollbackJournal, mark_point_of_no_return(), or the main O-1/O-2 split.


def push_one_extra(library, title_id, group_rel, item, extras_method, extras_val, device_id=None,
                   device_facts=None):
    """Upload ONE extra item to the device, mirroring cmd_push's per-file upload
    protocol but standalone and stripped to the essentials.

    On-disk path  = <title folder_path>/<group_rel>/<sub_rel>.
    Remote dir    = REMOTE_ROOT/relpath(<extra's folder>, LOCAL_ROOT) — mirrors
                    the Specials/Extra subfolder on the phone (same except->
                    basename fallback as cmd_push).
    Split         = extras_method/extras_val (already resolved by the caller; a
                    falsy method => whole-file push, the ('NONE', None) sentinel).

    Steps: optional split -> hash chunks + write each chunk's local
    `checksums/<chunk>.sha256` sidecar (IMP-D20, best-effort) -> persist chunk
    hashes into the item's `split_info` (BEFORE upload, so a resume has them)
    -> per-chunk `<final>.partial` upload + atomic `mv`, wrapped in
    mvcommon.retry -> delete the local chunk -> best-effort `write_remote_mvmeta`
    sidecar -> flip the item `uploaded=True`, `status="onboarded"`, and save ->
    [IMP-C25] best-effort identity capture at <extra folder>/<short_id>.gpcapture.json
    (`device_facts`: an optional zero-arg callable returning the device's timezone
    facts — push_title_extras passes its once-per-title memo of _device_facts).

    O-1 resumable per file: NO point-of-no-return. The per-item chunk dir lives
    at <extra folder>/<SPLIT_DIR_NAME>/<short_id> — nested under SPLIT_DIR_NAME so
    scan_extras_folders (which prunes SPLIT_DIR_NAME) never re-scans leftover
    chunks, and two extras sharing a folder never collide. A re-run with chunks
    still present resumes them instead of re-splitting; the master never leaves
    its path, so a failed item simply stays uploaded=False and is retried later.

    Mutates `item` in place and saves `library` on success. Returns True iff the
    extra fully uploaded. A dummy-sized (< DUMMY_MAX_BYTES) local file is REFUSED
    outright (IMP-D19 Step 12c / D19-B1 layer 2) — see the guard below."""
    title_entry = library.get(title_id) or {}
    title_folder = title_entry.get("folder_path", "")
    short_id = item.get("short_id", "")
    group_os = group_rel.replace("/", os.sep)
    sub_os = item.get("sub_rel", "").replace("/", os.sep)
    local_file_path = os.path.join(title_folder, group_os, sub_os)
    extra_folder = os.path.dirname(local_file_path)

    if not os.path.exists(local_file_path):
        print(f"     ❌ Extra missing on disk, skipping: {local_file_path}")
        return False

    # [IMP-D19 Step 12c / D19-B1] Layer-2 backstop for EVERY caller: never
    # upload a dummy-sized file as an extra. If a pending item's on-disk file is
    # a dummy, the real bytes were already reclaimed — uploading would overwrite
    # the REAL cloud copy with the dummy (unrecoverable). Same DUMMY_MAX_BYTES
    # sniff as cmd_prep/cmd_check. Accepted consequence: a genuinely tiny real
    # clip under the threshold cannot be pushed as an extra either (defense
    # against unrecoverable loss outranks a sub-200 KB clip). The refusal keeps
    # the missing-file semantics: this item stays uploaded=False, the batch
    # continues with the rest.
    local_size = os.path.getsize(local_file_path)
    if local_size < DUMMY_MAX_BYTES:
        print(f"     ❌ Refusing to push extra {item.get('filename')}: its on-disk file is "
              f"dummy-sized ({local_size:,} B). Files under DUMMY_MAX_BYTES "
              f"({DUMMY_MAX_BYTES:,} B = 200 KB) are treated as space-reclaim dummies "
              f"and are never uploaded as extras.")
        print(f"        > If the real file was reclaimed, recover it with: "
              f"fetch_restore {title_id} --fetchExtras")
        return False

    # Remote dir mirrors the extra's on-disk folder (reuse cmd_push's fallback).
    try:
        rel_path = os.path.relpath(extra_folder, LOCAL_ROOT)
    except Exception:
        rel_path = os.path.basename(extra_folder)
    remote_target_dir = f"{REMOTE_ROOT}/{rel_path}".replace("\\", "/")

    adb_base = ["adb", "-s", device_id] if device_id else ["adb"]
    safe_remote_dir = remote_target_dir.replace("'", "'\\''")
    try:
        subprocess.run(adb_base + ["shell", "mkdir", "-p", f"'{safe_remote_dir}'"], check=True)
    except Exception as e:
        print(f"     ❌ ADB connection failed for extra {item.get('filename')}: {e}")
        return False

    parts_dir = os.path.join(extra_folder, SPLIT_DIR_NAME, short_id)
    # [IMP-D21] Snapshot BEFORE any branch below runs — mirrors cmd_push's own
    # parts_preexisted (main.py ~4675), computed up front for the same reason:
    # a failed split must only clean up what THIS run created (see the D1 fix
    # below); a directory that already existed is left untouched.
    parts_preexisted = os.path.isdir(parts_dir)
    files_to_upload = []
    is_split = False

    # 1. RESUME — chunks for this item still on disk: re-push them (no re-split).
    if os.path.isdir(parts_dir) and any(f.endswith(".mkv") for f in os.listdir(parts_dir)):
        files_to_upload = sorted(
            os.path.join(parts_dir, f) for f in os.listdir(parts_dir) if f.endswith(".mkv"))
        is_split = True
        print(f"     > 🔄 Resuming {len(files_to_upload)} extra chunk(s) for {item.get('filename')}.")
    # 2. SPLIT — independent extras chunk size (size-under-target / no method => whole).
    elif _will_split(os.path.getsize(local_file_path), extras_method, extras_val):
        file_size = os.path.getsize(local_file_path)
        if not _free_space_ok(extra_folder, file_size, True, False):
            free, required, _short = _disk_shortfall(extra_folder, file_size, True, False)
            print(f"     ❌ Not enough free space to split extra {item.get('filename')} "
                  f"(need ~{human_readable_size(required)} in {extra_folder}, "
                  f"have {human_readable_size(free)}).")
            return False
        # [IMP-D21] UNSPLITTABLE-TRACK PRE-FLIGHT — mirrors cmd_push's own gate
        # (main.py ~4735) so extras are no longer the one push path that still
        # charges into a doomed split. Like the free-space check above, this is
        # READ-ONLY and runs BEFORE makedirs, so nothing exists yet to roll
        # back — a clean early return. MediaVault deliberately does NOT convert
        # or drop the track itself: that irreversible quality decision stays
        # the operator's to make. Label = title::group/sub_rel (the same
        # identifier write_remote_mvmeta uses below) so a season-batch run can
        # tell which extra, on which title, was refused.
        extra_label = f"{title_id}::{group_rel}/{item.get('sub_rel')}"
        if refuse_if_unsplittable(local_file_path, extra_label):
            return False
        os.makedirs(parts_dir, exist_ok=True)
        files_to_upload = split_video_file(local_file_path, parts_dir, extras_method, extras_val, file_id=short_id)
        if not files_to_upload:
            print(f"     ❌ Split failed for extra {item.get('filename')}.")
            # [IMP-D21 / D1] mkvmerge can die AFTER writing some chunks
            # (subprocess.CalledProcessError); split_video_file returns [] but
            # never deletes what it already wrote, and split_info is only ever
            # set below on SUCCESS. Left alone, the RESUME branch above would
            # treat those partial chunks as authoritative on the very next
            # run, upload them, and flip uploaded=True/status="onboarded" with
            # NO split_info — build_extras_entries then queues an unrecoverable
            # whole-file-by-hash fetch that does not exist in the cloud. Mirror
            # cmd_push's own parts_preexisted idiom (it journals
            # record_create_dir(parts_dir) and rolls back on this exact
            # failure): only remove what THIS run created. A directory that
            # already existed before this call is left untouched, conservative
            # to match cmd_push (which also never journals a pre-existing dir).
            if not parts_preexisted:
                try:
                    shutil.rmtree(parts_dir)
                except OSError:
                    pass
            return False
        is_split = True
        # [IMP-D20] Hash chunks + write each one's local checksum sidecar,
        # mirroring cmd_push's chunk-hash loop (main.py ~4512-4518) — the
        # sidecar write is wrapped (unlike cmd_push's) so a write failure can
        # NEVER abort the extras push (guardrail: sidecar writes are always
        # best-effort here).
        checksum_dir = os.path.join(extra_folder, CHECKSUM_DIR_NAME)
        os.makedirs(checksum_dir, exist_ok=True)
        chunk_metadata = []
        for p in files_to_upload:
            c_name = os.path.basename(p)
            c_hash = calculate_file_hash(p)
            chunk_metadata.append({"filename": c_name, "hash": c_hash})
            try:
                with open(os.path.join(checksum_dir, f"{c_name}.sha256"), 'w') as cf:
                    cf.write(f"{c_hash} *{c_name}")
            except Exception as e:
                print(f"⚠️ Warning: Could not write extras chunk sidecar: {e}")
        # Persist chunk hashes BEFORE upload so a resume already has them (O-1).
        item["split_info"] = {
            "is_split": True, "method": extras_method, "val": extras_val,
            "total_chunks": len(files_to_upload), "chunks": chunk_metadata,
        }
        save_library(library)
    # 3. WHOLE-FILE push.
    else:
        files_to_upload = [local_file_path]
        item.pop("split_info", None)

    # [IMP-C25] Read-only snapshot of what is about to be uploaded (chunks are deleted
    # locally as they upload); written to the item's capture file only after success.
    # parts_dir (not the bare _parts name) marks the chunks: this pusher names by is_split.
    try:
        _capture_objs = gpcapture.snapshot_push_objects(
            files_to_upload, parts_dir, short_id,
            {c.get("filename"): c.get("hash") for c in (item.get("split_info") or {}).get("chunks", [])
             if isinstance(c, dict)},
            item.get("hash"), [], item.get("tech_spec"))
    except Exception:
        _capture_objs = []

    # UPLOAD LOOP — the duplicated .partial+rename+retry idiom (deliberate, lean).
    all_success = True
    for f in files_to_upload:
        local_fname = os.path.basename(f)
        if is_split:
            remote_fname = local_fname  # chunk name already carries [short_id]
        else:
            name, ext = os.path.splitext(local_fname)
            remote_fname = f"{name} [{short_id}]{ext}"
        remote_full_path = f"{remote_target_dir}/{remote_fname}"
        remote_partial_path = remote_full_path + PARTIAL_SUFFIX
        safe_partial = remote_partial_path.replace("'", "'\\''")
        safe_final = remote_full_path.replace("'", "'\\''")
        print(f"     -> Pushing extra: {remote_fname}...", end=" ", flush=True)

        def _push_and_rename():
            subprocess.run(adb_base + ["push", "-p", f, remote_partial_path], check=True)
            subprocess.run(
                adb_base + ["shell", "mv", f"'{safe_partial}'", f"'{safe_final}'"],
                check=True,
            )

        def _cleanup_and_log(attempt, exc):
            print(f"⏳ Retry {attempt}/3 after {(1, 4, 16)[min(attempt - 1, 2)]}s (ADB push failed)…")
            subprocess.run(adb_base + ["shell", "rm", f"'{safe_partial}'"], check=False)

        try:
            retry(
                _push_and_rename,
                attempts=3,
                backoff=(1, 4, 16),
                retry_on=(subprocess.CalledProcessError,),
                on_retry=_cleanup_and_log,
            )
            print("✅")
        except subprocess.CalledProcessError:
            print("❌ FAIL (ADB Error)")
            all_success = False
            break
        except Exception as e:
            print(f"❌ FAIL ({e})")
            all_success = False
            break

        # Delete the local chunk after a successful upload+rename (resumable, NOT a PONR).
        if is_split and SPLIT_DIR_NAME in f:
            try:
                os.remove(f)
            except OSError:
                pass

    if not all_success:
        print(f"     ❌ Extra {item.get('filename')} left partially uploaded "
              f"(uploaded=False; re-run to resume).")
        return False

    # Best-effort: remove the now-empty per-item chunk dir.
    if is_split and os.path.isdir(parts_dir) and not os.listdir(parts_dir):
        try:
            os.rmdir(parts_dir)
        except OSError:
            pass

    # Disaster-recovery sidecar (best-effort — never fails the upload). Give it a
    # folder_path so the mvmeta records where the extra restores to.
    meta_entry = dict(item)
    meta_entry["folder_path"] = extra_folder
    try:
        _mvmeta_identity = gpcapture.mvmeta_identity(extra_folder, short_id, _capture_objs)
    except Exception:
        _mvmeta_identity = None
    write_remote_mvmeta(adb_base, remote_target_dir,
                        f"{title_id}::{group_rel}/{item.get('sub_rel')}", meta_entry,
                        identity=_mvmeta_identity)

    item["uploaded"] = True
    item["status"] = "onboarded"
    save_library(library)
    # [IMP-C25] After the success save — best-effort, never journalled (extras have no
    # journal): the item's identity capture, with the device's timezone facts.
    try:
        gpcapture.capture_after_push(extra_folder, gpcapture.extras_label(title_id, group_rel, item.get("sub_rel")),
                                     short_id, _capture_objs, device_id=device_id, remote_dir=remote_target_dir,
                                     device_facts=device_facts() if device_facts else None)
    except Exception:
        pass
    return True


def push_title_extras(library, title_id, extras_size, device_id=None, split_method=None, split_val=None):
    """Upload every not-yet-uploaded extra item on `title_id` (all groups).

    Independent chunk size (Card B):
      - extras_size given (a parse_extras_tokens tuple) WINS:
          ('NONE', None)                        -> whole-file push (no split)
          ('SIZE_MB'|'SIZE_GB'|'COUNT', val)    -> that split
      - extras_size is None -> INHERIT the command's main split
          (split_method/split_val); if the command has no main split either,
          extras are pushed whole-file.

    Each item is pushed via push_one_extra (O-1 resumable per file). One bad/
    missing extra does NOT block the rest — the item stays uploaded=False and is
    retried on the next run. Returns True iff every processed item uploaded."""
    if not title_id or title_id not in library:
        print(f"⚠️  Could not resolve a title entry for extras upload (id: {title_id}).")
        return False
    groups = library[title_id].get("extras", {}).get("groups", {})
    if not groups:
        return True  # title carries no extras — nothing to do

    if extras_size is None:
        e_method, e_val = split_method, split_val          # inherit the main split
    elif extras_size[0] == "NONE":
        e_method, e_val = None, None                       # explicit whole-file
    else:
        e_method, e_val = extras_size[0], extras_size[1]    # explicit split

    pending = [(g, it) for g in sorted(groups)
               for it in groups[g].get("items", []) if not it.get("uploaded")]
    if not pending:
        print(f"   > ✅ All extras already uploaded for {title_id}.")
        return True

    print(f"\n=== 📎 UPLOADING {len(pending)} EXTRA(S) for {title_id} ===")
    print(f"   > Extras split: {e_method} {e_val}" if (e_method and e_val)
          else "   > Extras: whole-file (no split)")

    # [IMP-C25] The device's timezone facts for the extras captures: ONE best-effort,
    # read-only query per title (same adb_base as the uploads), made lazily once the
    # first extra has uploaded — a run where nothing uploads never asks the device.
    adb_base = ["adb", "-s", device_id] if device_id else ["adb"]
    facts_memo = []

    def _device_facts_once():
        if not facts_memo:
            facts_memo.append(_device_facts(adb_base))
        return facts_memo[0]

    ok = True
    for group_rel, item in pending:
        if not push_one_extra(library, title_id, group_rel, item, e_method, e_val, device_id=device_id,
                              device_facts=_device_facts_once):
            ok = False
    print(f"=== ✅ EXTRAS UPLOAD COMPLETE for {title_id} ===" if ok
          else f"=== ⚠️  EXTRAS UPLOAD INCOMPLETE for {title_id} (re-run to resume) ===")
    return ok


def _push_title_extras_or_warn(library, title_id, requested, extras_size, device_id=None, split_method=None, split_val=None):
    """IMP-D3/A5: user-facing wrapper around push_title_extras for the THREE call
    sites where `requested` (the raw --extras folder value) is a genuine,
    explicit user request: cmd_push, cmd_push_group, and the already-archived-
    main branch of the `push` CLI dispatch. In all three, `requested` truthy
    means the user typed --extras on THIS invocation (verified: cmd_push /
    cmd_push_group's `extras` parameter is populated ONLY from that CLI flag —
    no autopilot or internal caller ever sets it).

    push_title_extras itself MUST stay a silent no-op on an extras-less title —
    cmd_prep_push_rep(_season) and cmd_add_extras call it directly (not through
    this wrapper) precisely because their OWN prep/scan leg already registers
    (or reports on) the folder, so a no-extras title there is either impossible
    or already explained; a warning there would be pure noise. But cmd_push /
    cmd_push_group treat `--extras` as a PURE BOOLEAN GATE and never scan the
    folder value at all (A5's root cause) — so on a never-registered folder,
    push_title_extras's silent no-op looks exactly like a normal successful
    push while archiving nothing. This wrapper closes that gap with a loud,
    remedy-naming warning, then still calls push_title_extras unchanged."""
    if requested:
        has_extras = bool(title_id and title_id in library
                           and library[title_id].get("extras", {}).get("groups"))
        if not has_extras:
            label = title_id or "this title"
            print(f"⚠️  --extras was given but {label} has no registered extras yet — nothing will be pushed.")
            print(f'   Register them first: add_extras {label} "<folders>"  (or prep / prep_season --extras "<folders>")')
    return push_title_extras(library, title_id, extras_size, device_id, split_method, split_val)


def cmd_push(manual_id, split_method=None, split_val=None, chunk_range=None, device_id=None, eager_rehash=False, temp_dir=None, extras=None, extras_size=None):
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
            # [IMP-C20] UNSPLITTABLE-TRACK PRE-FLIGHT. mkvmerge refuses to split a
            # file containing certain codecs (FLAC today) and fails the ENTIRE run.
            # [FLAC-CARRYOUT] FLAC is carry-out-able: extract it to a holder, split
            # the remainder with the track dropped, and re-add on restore — so it is
            # NOT refused. Only a carry-out-able unsplittable track that we cannot
            # handle (multiple FLAC tracks, or a non-FLAC unsplittable codec) is
            # refused here, naming the track and handing over the runbook.
            if refuse_if_unsplittable(local_file_path, manual_id, carry_out_flac=True):
                return False

            print(f"   > ✂️ Splitting...")
            # [ROLLBACK C] Journal the dir creations (this run only) BEFORE makedirs.
            if not parts_preexisted:
                journal.record_create_dir(parts_dir)
            if not checksum_preexisted:
                journal.record_create_dir(checksum_dir)
            os.makedirs(parts_dir, exist_ok=True)
            os.makedirs(checksum_dir, exist_ok=True)

            # Detect FLAC carry-out (single track only — conservative).
            flac_tracks = [(tid, codec, lang) for (tid, codec, lang)
                           in find_unsplittable_tracks(local_file_path)
                           if codec in CARRY_OUT_CODEC_IDS]
            if len(flac_tracks) > 1:
                print(f"❌ {manual_id} has {len(flac_tracks)} FLAC tracks — carrying out "
                      "multiple tracks is not yet supported. Remux one to a splittable "
                      "codec first (see the unsplittable-tracks runbook).")
                journal.rollback(library)
                return False
            carried_tracks = []
            drop_track = None
            if flac_tracks:
                track_id, codec, lang = flac_tracks[0]
                rec = _carry_out_flac_track(local_file_path, parts_dir, short_id,
                                            track_id, codec, lang)
                if rec is None:
                    print("❌ FLAC carry-out failed — nothing was pushed.")
                    journal.rollback(library)
                    return False
                # journal the holder as created-this-run so a pre-upload rollback removes it
                holder_local = os.path.join(parts_dir, rec["holder_filename"])
                journal.record_create_file(holder_local)
                carried_tracks = [rec]
                drop_track = track_id

            # [UPDATED] Pass short_id to attach UID to chunk names
            # [FLAC-CARRYOUT] drop_track excludes the carried FLAC so the remainder
            # splits. Only pass it when non-None so the non-FLAC argv is byte-identical
            # to today (a stubbed split_video_file in the tests takes no drop_track).
            split_kwargs = {"file_id": short_id}
            if drop_track is not None:
                split_kwargs["drop_track"] = drop_track
            files_to_upload_paths = split_video_file(local_file_path, parts_dir,
                                                     split_method, split_val, **split_kwargs)
            if not files_to_upload_paths:
                # [ROLLBACK C] Split failed pre-any-upload — replay journalled inverses.
                journal.rollback(library)
                return False  # Stop if split failed

            # Hash Chunks (chunks ONLY — the holder is tracked separately in
            # carried_out_tracks, never in `chunks`).
            for chunk_path in files_to_upload_paths:
                c_name = os.path.basename(chunk_path)
                c_hash = calculate_file_hash(chunk_path)
                chunk_metadata.append({"filename": c_name, "hash": c_hash})
                # Save sidecar
                with open(os.path.join(checksum_dir, f"{c_name}.sha256"), 'w') as f: f.write(f"{c_hash} *{c_name}")

            # [FLAC-CARRYOUT] the holder uploads alongside the chunks (its own hash is
            # already in carried_tracks[0].holder_hash; keep it OUT of `chunks`).
            if carried_tracks:
                files_to_upload_paths.append(os.path.join(
                    parts_dir, carried_tracks[0]["holder_filename"]))

            # Save split info to library IMMEDIATELY
            # [ROLLBACK SPEC] split_info is written HERE this run. A pre-any-upload
            # rollback pops it ONLY if split_info_existed was False at entry (a prior
            # interrupted push may have left it). _parts/ + checksums/ created just
            # above (@719-720) are likewise removed only if created-this-run.
            if not split_info_preexisted:
                journal.record_set_field(manual_id, "split_info", existed=False, prior=None)
            library[manual_id]["split_info"] = {
                "is_split": True, "method": split_method, "val": split_val,
                "total_chunks": len(chunk_metadata), "chunks": chunk_metadata
            }
            # [FLAC-CARRYOUT] record the carry-out so fetch/restore recover the FLAC
            # and re-add it. Additive to split_info (rides the same journalled
            # record_set_field above), so a pre-upload rollback reverts it cleanly.
            if carried_tracks:
                library[manual_id]["split_info"]["carried_out_tracks"] = carried_tracks
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
                if carried_tracks:
                    # [FLAC-CARRYOUT] Eager rehash is skipped when a track was carried
                    # out: the canonical hash must include the RE-ADDED FLAC, which the
                    # first-restore deterministic merge does correctly (and eagerly it
                    # would also wrongly mix the holder file into the chunk merge). So
                    # fall back to deferred — bless at first restore — instead of staging
                    # a WRONG canonical now. Mirrors the existing "eager did not produce
                    # a hash -> deferred" path.
                    print("   > ⚠️ FLAC carry-out: eager re-hash skipped (canonical will "
                          "be blessed at first restore, which re-adds the FLAC).")
                else:
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

    # [IMP-C25] Read-only snapshot of what is about to be uploaded (chunks are deleted
    # locally as they upload); written to the capture file only after a successful push.
    try:
        _capture_objs = gpcapture.snapshot_push_objects(
            files_to_upload_paths, SPLIT_DIR_NAME, short_id, _chunk_hashes, entry.get("hash"),
            library.get(manual_id, {}).get("split_info", {}).get("carried_out_tracks", []),
            entry.get("tech_spec"))
    except Exception:
        _capture_objs = []

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
            # [IMP-C25] Its v2 identity facts are a read-only lookup (never journalled).
            try:
                _mvmeta_identity = gpcapture.mvmeta_identity(local_folder, short_id, _capture_objs)
            except Exception:
                _mvmeta_identity = None
            write_remote_mvmeta(adb_base, remote_target_dir, manual_id, library[manual_id],
                                identity=_mvmeta_identity)
            library[manual_id]["uploaded"] = True
            library[manual_id]["status"] = "onboarded"
            save_library(library)
            # [ROLLBACK C] Clean success — discard the journal.
            journal.commit()
            # Warn-only post-condition (IMP-D4). Post-commit; does NOT affect rollback/PONR.
            _warn_if_entry_inconsistent(library[manual_id], manual_id)
            # [IMP-C25] Post-commit, best-effort, NOT journalled identity capture (the
            # device's timezone comes from one read-only adb query, made only now).
            try:
                gpcapture.capture_after_push(local_folder, manual_id, short_id, _capture_objs,
                                             device_id=device_id, remote_dir=remote_target_dir,
                                             device_facts=_device_facts(adb_base))
            except Exception:
                pass
            print("✅ SUCCESS.\n")
            if extras:
                _push_title_extras_or_warn(library, _extras_title_id(library, manual_id), extras, extras_size, device_id, split_method, split_val)
            return True
        else:
            journal.commit()
            # Warn-only post-condition (IMP-D4). Post-commit; does NOT affect rollback/PONR.
            _warn_if_entry_inconsistent(library[manual_id], manual_id)
            # [IMP-C25] Post-commit, best-effort, NOT journalled identity capture (the
            # device's timezone comes from one read-only adb query, made only now).
            try:
                gpcapture.capture_after_push(local_folder, manual_id, short_id, _capture_objs,
                                             device_id=device_id, remote_dir=remote_target_dir,
                                             chunk_range=chunk_range, device_facts=_device_facts(adb_base))
            except Exception:
                pass
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


def _parse_extras_size_unit(size_str):
    """Parse a compact size string like '9900mb' or '8gb' into the normalised
    (method, val) tuple used by extras_size.

      '9900mb'  ->  ('SIZE_MB', '9900')
      '8gb'     ->  ('SIZE_GB', '8')

    Returns None if the string does not match the expected pattern."""
    m = re.match(r'^(\d+(?:\.\d+)?)\s*(mb|gb)$', size_str.lower().strip())
    if not m:
        return None
    num = int(float(m.group(1)))
    unit = m.group(2)
    return ("SIZE_MB", str(num)) if unit == "mb" else ("SIZE_GB", str(num))


def parse_extras_tokens(tokens):
    """Parse ``--extras`` / ``-extras`` / ``--extras-size`` / ``-extras-size``
    from a flat token list.

    --extras <value>
        value may be semicolon-separated paths; the flag is repeatable; all
        paths are split on ``;``, stripped of surrounding quotes/whitespace,
        and collected in a flat ordered list.

    --extras-size <value>
        Accepted forms:
          'none'                -> ('NONE', None)
          '9900mb' / '8gb'      -> ('SIZE_MB','9900') / ('SIZE_GB','8')
          'SIZE_MB|SIZE_GB|COUNT <val>'  (triplet, consumes two tokens)

    Unknown tokens are silently skipped (matches the existing parsers).
    A value-taking flag with no following value prints an error and
    calls sys.exit(1).

    Returns: {'folders': [...], 'extras_size': <tuple or None>}

    Acceptance:
      parse_extras_tokens(['--extras','A;B','--extras','C',
                           '--extras-size','9900mb'])
      -> {'folders': ['A','B','C'], 'extras_size': ('SIZE_MB','9900')}
    """
    folders = []
    extras_size = None

    i = 0
    while i < len(tokens):
        tok = tokens[i]
        if tok in ("--extras", "-extras"):
            if i + 1 < len(tokens):
                raw = tokens[i + 1]
                for part in raw.split(";"):
                    part = part.strip().strip('"').strip("'").strip()
                    if part:
                        folders.append(part)
                i += 2
            else:
                print("❌ Error: Missing value for --extras")
                sys.exit(1)
        elif tok in ("--extras-size", "-extras-size"):
            if i + 1 < len(tokens):
                sv = tokens[i + 1]
                if sv.lower() == "none":
                    extras_size = ("NONE", None)
                    i += 2
                elif sv.upper() in ("SIZE_MB", "SIZE_GB", "COUNT"):
                    # triplet form: --extras-size SIZE_MB 9900
                    if i + 2 < len(tokens):
                        extras_size = (sv.upper(), tokens[i + 2])
                        i += 3
                    else:
                        print(f"❌ Error: Missing value for {sv}")
                        sys.exit(1)
                else:
                    result = _parse_extras_size_unit(sv)
                    if result is None:
                        print(f"❌ Error: Unrecognized --extras-size value: {sv!r}")
                        sys.exit(1)
                    extras_size = result
                    i += 2
            else:
                print("❌ Error: Missing value for --extras-size")
                sys.exit(1)
        else:
            i += 1

    return {"folders": folders, "extras_size": extras_size}


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


def cmd_push_group(group_id, split_method=None, split_val=None, episode_range=None, device_id=None, eager_rehash=False, temp_dir=None, extras=None, extras_size=None):
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

    # IMP-D19 Step 3: upload the season/title's extras after the episode loop.
    # Reload — each per-episode cmd_push saved its own library updates, so the
    # in-memory `library` above is stale; pushing extras off it would clobber
    # the just-saved episode statuses.
    if extras:
        library = load_library()
        ex_title_id = _extras_title_id(library, group_id) or (
            _extras_title_id(library, target_ids[0]) if target_ids else None)
        _push_title_extras_or_warn(library, ex_title_id, extras, extras_size, device_id, split_method, split_val)


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
        # IMP-D19 Step 4 (Card D1): dummy this title's uploaded extras too. The
        # driver never raises (it self-handles + catches RollbackHardFail), so this
        # one line cannot trip cmd_replace's except handlers above — the main
        # journal/PONR/contract is byte-for-byte unchanged (Card E1).
        replace_title_extras(library, _extras_title_id(library, manual_id))
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

    # IMP-D19 Step 4: explicit batch wire — dummy the title's extras after the
    # episode loop (mirrors how cmd_push_group wires push_title_extras). Reload
    # first: each per-episode cmd_replace saved its own updates and already
    # archived these extras on the first child, so the in-memory `library` above
    # is stale; the reload makes this an idempotent no-op (status=='archived'
    # short-circuits replace_one_extra) rather than a wasteful re-dummy.
    library = load_library()
    ex_title_id = _extras_title_id(library, group_id) or (
        _extras_title_id(library, target_ids[0]) if target_ids else None)
    replace_title_extras(library, ex_title_id)


# ==========================================
#   IMP-D19 Step 4 — EXTRAS REPLACE (DUMMY) PHASE  (per-file PONR; additive E1)
# ==========================================
# replace_one_extra mirrors cmd_replace's atomic two-rename dummy swap EXACTLY —
# the per-file point-of-no-return (mark_point_of_no_return fired right after the
# original leaves its path), the eager-canonical-hash promotion, and the
# RollbackHardFail(resume_cmd="fetch_restore <id> --fetchExtras") contract — but
# scoped to ONE nested extras item, with its journal living in the extra's OWN
# folder (e.g. …\Specials\). It REUSES the same RollbackJournal/PONR primitives as
# the main-content path; it does NOT modify cmd_replace, whose journal/PONR/
# contract stay byte-for-byte unchanged (Card E1 / ROLLBACK_MECHANISM.md §10).
def replace_one_extra(library, title_id, group_rel, item):
    """Replace ONE uploaded extra with a tiny video dummy to reclaim space — the
    per-file mirror of cmd_replace (Card D1 lifecycle, Card E1 additive rollback).

    The atomic two-rename swap, the per-file point-of-no-return
    (mark_point_of_no_return fired right after the original leaves its path), the
    eager-canonical-hash promotion, and the RollbackHardFail contract are
    IDENTICAL to cmd_replace; only the target is a nested extras `item` and the
    journal lives in the extra's OWN folder rather than a library leaf's. The
    EXISTING cmd_replace contract is byte-for-byte untouched — this REUSES the same
    RollbackJournal/PONR primitives, it does not modify the main-content path.

    On-disk / restore path = <title folder_path>/<group_rel>/<sub_rel>.

    Returns True on success (item -> status='archived', a dummy now on disk) or on
    an already-archived no-op; False on a pre-PONR failure or a skip. Raises
    RollbackHardFail on a post-PONR failure, naming the EXISTING
    `fetch_restore <title_id> --fetchExtras` resume command (it exists from Step 2;
    the bytes are safe in the cloud)."""
    title_entry = library.get(title_id) or {}
    title_folder = title_entry.get("folder_path", "")
    group_os = group_rel.replace("/", os.sep)
    sub_os = item.get("sub_rel", "").replace("/", os.sep)
    original = os.path.join(title_folder, group_os, sub_os)
    extra_folder = os.path.dirname(original)
    filename = item.get("filename") or os.path.basename(original)
    extra_id = f"{title_id}::{group_rel}/{item.get('sub_rel')}"

    if not item.get("uploaded", False):
        print(f"     ⚠️ Skipping extra {filename}: not uploaded.")
        return False
    if item.get("status") == "archived":
        return True  # already a dummy — idempotent no-op
    if not os.path.isdir(extra_folder):
        # Folder gone — cannot journal there. Skip (defensive; never crash the batch).
        print(f"     ⚠️ Skipping extra {filename}: its folder is missing ({extra_folder}).")
        return False

    ext = os.path.splitext(filename)[1]
    # The dummy temp is the ONLY pre-PONR artifact (mirrors cmd_replace); the
    # journal lives in the extra's own folder so `recover --scan` finds it there.
    tmp_path = original + ".dummy_tmp" + ext
    journal = RollbackJournal(extra_folder, extra_id)
    journal.record_create_file(tmp_path)
    if not make_video_dummy(tmp_path, ext):
        print(f"     ❌ extras replace aborted — could not create dummy for {filename}")
        journal.rollback(library)
        return False

    try:
        tobedeleted = original + ".tobedeleted"

        # STALE SWEEP: recover a prior interrupted run before touching anything.
        if os.path.exists(tobedeleted):
            try:
                if os.path.exists(original):
                    print(f"     ⚠️ Stale leftover — cleaning up {os.path.basename(tobedeleted)}")
                    os.remove(tobedeleted)
                else:
                    print(f"     ⚠️ Recovered interrupted replace: restoring original from {os.path.basename(tobedeleted)}")
                    os.rename(tobedeleted, original)
                    print(f"     ❌ extras replace aborted — original restored. Please retry.")
                    journal.rollback(library)  # pre-PONR — remove the dummy temp
                    return False
            except Exception as e:
                print(f"     ⚠️ Could not clean stale leftover: {e}")

        # Commit / point-of-no-return: rename original -> .tobedeleted.
        if os.path.exists(original):
            moved = False
            for attempt in range(3):
                try:
                    os.chmod(original, stat.S_IWRITE)
                    os.rename(original, tobedeleted)  # PONR: original leaves its path here
                    journal.mark_point_of_no_return()
                    moved = True
                    break
                except PermissionError:
                    print(f"     ⚠️ File busy or locked. Retrying... ({attempt + 1}/3)")
                    time.sleep(1)
                except Exception as e:
                    print(f"     ❌ Error removing extra file: {e}")
                    journal.rollback(library)  # still pre-PONR — reversible
                    return False
            if not moved:
                print(f"     ❌ PERMISSION DENIED: Could not replace extra {filename}")
                print("        > Close any players/Plex scanning this file and try again.")
                journal.rollback(library)  # pre-PONR — roll back the dummy temp
                return False

        # Make the dummy live, then drop the leftover (non-fatal).
        os.rename(tmp_path, original)
        if os.path.exists(tobedeleted):
            try:
                os.remove(tobedeleted)
            except Exception as e:
                print(f"     ⚠️ WARNING: Could not remove leftover {os.path.basename(tobedeleted)}: {e}.")

        # PROMOTE-AT-REPLACE (identical to cmd_replace's eager-canonical promotion).
        # A no-op for the lean extras push, which never stages a canonical_hash, but
        # kept structurally identical so the two replace paths can never drift.
        _split_info = item.get("split_info", {})
        _staged = _split_info.get("canonical_hash")
        if _staged and item.get("re_hashed") is not True:
            item["hash"] = _staged
            item["re_hashed"] = True
            item["split_info"]["rehashed_at"] = _rehashed_at()
            del item["split_info"]["canonical_hash"]
            print(f"     > 🧬 Promoted eager canonical hash to extra truth (re_hashed=True).")

        item["status"] = "archived"
        save_library(library)
        journal.commit()
        print(f"     ✅ Archived extra: {filename}")
        return True
    except RollbackHardFail:
        raise
    except Exception as e:
        if journal.crossed_ponr:
            # At/after PONR — hard-fail naming the EXISTING fetch_restore --fetchExtras.
            # The extra's journal (with its PONR marker) is left on disk for recovery.
            print(f"     ❌ IRREVERSIBLE: extras replace failed after the commit point for {filename}.")
            print(f"        > To recover the extras from the cloud: fetch_restore {title_id} --fetchExtras")
            raise RollbackHardFail(
                state=f"{extra_id} archived (original committed)",
                reason=f"extras replace failed past the point-of-no-return: {e}",
                resume_cmd=f"fetch_restore {title_id} --fetchExtras",
            )
        print(f"     ❌ extras replace failed (pre-commit): {e}")
        journal.rollback(library)
        return False


def replace_title_extras(library, title_id):
    """Replace (dummy) every uploaded-not-yet-archived extra on `title_id` — the
    per-title driver, mirroring how cmd_replace_group drives cmd_replace.

    A clean (silent) no-op when the title carries no extras or none are pending.
    One bad/missing extra does not block the rest. A post-PONR RollbackHardFail
    from replace_one_extra is caught HERE — exactly as cmd_prep_push_rep_season
    catches cmd_replace's — its resume command printed, and the batch stops.
    Crucially this driver NEVER raises (it only returns a bool), so a one-line
    `replace_title_extras(...)` wired inside cmd_replace's try block cannot trigger
    cmd_replace's except handlers: the main-content journal/PONR/contract stays
    byte-for-byte unchanged (Card E1). Returns True iff every processed extra
    archived (or there was nothing to do)."""
    if not title_id or title_id not in library:
        return True
    groups = library[title_id].get("extras", {}).get("groups", {})
    if not groups:
        return True  # title carries no extras — nothing to reclaim
    pending = [(g, it) for g in sorted(groups)
               for it in groups[g].get("items", [])
               if it.get("uploaded") and it.get("status") != "archived"]
    if not pending:
        return True  # nothing uploaded-but-unarchived — idempotent no-op (silent)

    print(f"\n=== 📎 ARCHIVING {len(pending)} EXTRA(S) for {title_id} (reclaiming space) ===")
    ok = True
    for group_rel, item in pending:
        try:
            if not replace_one_extra(library, title_id, group_rel, item):
                ok = False
        except RollbackHardFail as hf:
            print(f"\n❌ {hf.state} — {hf.reason}")
            print(f"   > To recover the extras: {hf.resume_cmd}")
            return False
    print(f"=== ✅ EXTRAS ARCHIVED for {title_id} ===" if ok
          else f"=== ⚠️  EXTRAS ARCHIVE INCOMPLETE for {title_id} (re-run to retry) ===")
    return ok


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

    IMP-D3: ADDITIVE extras pass — every entry's optional `extras` block (a
    season_map AND a movie leaf can both carry one) is walked via
    _extras_item_paths and each item is classified with the SAME
    _disk_shape/_status_disk_violation rules as a physical leaf (extras items
    share the identical status vocabulary). Reported separately, clearly
    labelled `[extras: ...]`, and DOES affect the True/False return (an extras
    mismatch is a real integrity mismatch). Silent when the library carries no
    extras at all.

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

    # ---- IMP-D3: ADDITIVE extras pass (read-only). Walks EVERY entry (not just
    # physical leaves — season_map titles carry the extras block too) for its
    # optional `extras` block and classifies each item with the SAME
    # _disk_shape/_status_disk_violation rules, reused verbatim (no parallel
    # classifier). Silent (zero rows) when the library has no extras at all. ----
    extras_scanned = 0
    extras_ok = 0
    extras_violations = []   # (title_id, group_rel, sub_rel, status, shape, size, full_path, category)
    extras_category_counts = {}
    for title_id, title_entry in library.items():
        if not isinstance(title_entry, dict):
            continue
        for group_rel, full_path, item in _extras_item_paths(title_entry):
            extras_scanned += 1
            shape = _disk_shape(full_path)
            status = item.get("status")
            try:
                size = os.path.getsize(full_path)
            except OSError:
                size = -1
            is_violation, category = _status_disk_violation(status, shape)
            if is_violation:
                extras_violations.append(
                    (title_id, group_rel, item.get("sub_rel"), status, shape, size, full_path, category))
                extras_category_counts[category] = extras_category_counts.get(category, 0) + 1
            else:
                extras_ok += 1

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

    # ---- IMP-D3: extras violation report (silent when extras_scanned == 0, i.e.
    # the library carries no extras at all — keeps extras-less output unchanged) ----
    if extras_scanned:
        if extras_violations:
            print(f"\n❌ {len(extras_violations)} EXTRAS INTEGRITY MISMATCH(ES):")
            for title_id, group_rel, sub_rel, status, shape, size, full_path, category in extras_violations:
                size_str = "missing" if size < 0 else human_readable_size(size)
                print(f"   • {title_id}  [extras: {group_rel}/{sub_rel}]")
                print(f"       status={status}  on-disk={shape} ({size_str})  [{category}]")
                print(f"       {full_path}")
        else:
            print("✅ No extras integrity mismatches found.")

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
    extras_counts_str = ", ".join(f"{k}={v}" for k, v in sorted(extras_category_counts.items()))
    print(
        f"verify_library: scanned {scanned}, OK {ok}, MISMATCH {len(violations)}"
        + (f" ({counts_str})" if counts_str else "")
        + (f" | possibly_dangling: {len(dangling)} (high={dangling_high}, low={dangling_low})"
           if dangling else "")
        + (f" | extras: scanned {extras_scanned}, OK {extras_ok}, MISMATCH {len(extras_violations)}"
           + (f" ({extras_counts_str})" if extras_counts_str else "")
           if extras_scanned else "")
    )

    # ---- Optional fix (IMP-D5 slice): regenerate archived+TEXT_DUMMY dummies ----
    # Reuse the EXISTING cmd_repair_dummies — it already targets exactly the
    # archived + (< DUMMY_MAX_BYTES) class and rewrites via make_video_dummy +
    # os.replace. Status mismatches are intentionally left for the human.
    # IMP-D3: cmd_repair_dummies is now ALSO extras-aware (its own extras pass),
    # so the SAME single call repairs a broken extras dummy too — no duplicate
    # repair logic here. The "(incl. N extras)" note below is appended ONLY
    # when N > 0, so the main-content-only wording stays byte-identical when
    # there are zero extras textdummies to fix.
    if fix_dummies:
        archived_textdummy = sum(
            1 for v in violations if v[5] == "archived_textdummy"
        )
        extras_archived_textdummy = sum(
            1 for v in extras_violations if v[-1] == "archived_textdummy"
        )
        if archived_textdummy or extras_archived_textdummy:
            total_textdummy = archived_textdummy + extras_archived_textdummy
            extras_note = f" (incl. {extras_archived_textdummy} extras)" if extras_archived_textdummy else ""
            print(
                f"\n🔧 fix_dummies: regenerating video dummies for "
                f"{total_textdummy} archived+TEXT_DUMMY entr(y/ies){extras_note} "
                f"via repair_dummies (status fields are NOT changed)..."
            )
            cmd_repair_dummies()
        else:
            print("\n🔧 fix_dummies: no archived+TEXT_DUMMY entries to regenerate.")

    return len(violations) == 0 and len(extras_violations) == 0


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

    # IMP-D3: extras equivalent. Extras attach to season_map AND movie-leaf
    # titles, so this walks EVERY entry (no season_map/multi_ep_alias skip here
    # — that skip above is for the MAIN-content leaf loop only) purely for the
    # optional `extras` block, and mirrors the main-content rules exactly per
    # item: missing -> report+skip (never fabricate a file), size >=
    # DUMMY_MAX_BYTES -> leave alone (never dummy a real file), non-video
    # extension -> skip, otherwise regenerate via the SAME make_video_dummy +
    # os.replace swap. Silent (no summary line) when the library has no
    # archived extras at all — keeps extras-less output unchanged.
    ex_scanned = ex_regenerated = ex_skipped = ex_missing = ex_failed = 0
    for entry_id, entry in library.items():
        if not isinstance(entry, dict):
            continue
        if prefix_filter and not entry_id.startswith(prefix_filter):
            continue
        for group_rel, current_path, item in _extras_item_paths(entry):
            if item.get("status") != "archived":
                continue

            ex_scanned += 1
            label = f"{entry_id}  [extras: {group_rel}/{item.get('sub_rel')}]"

            if not os.path.exists(current_path):
                print(f"⚠️ Missing: {label} -> {current_path}")
                ex_missing += 1
                continue

            if os.path.getsize(current_path) >= DUMMY_MAX_BYTES:
                ex_skipped += 1
                continue

            filename = item.get("filename") or os.path.basename(current_path)
            ext = os.path.splitext(filename)[1]
            if ext.lower() not in VIDEO_EXTENSIONS:
                ex_skipped += 1
                continue

            tmp_path = current_path + ".repair_tmp" + ext

            print(f"🔧 Regenerating dummy: {label} -> {current_path}")
            if not make_video_dummy(tmp_path, ext):
                print(f"❌ Failed to regenerate {current_path}")
                ex_failed += 1
                continue

            os.replace(tmp_path, current_path)
            ex_regenerated += 1

    if ex_scanned:
        print(f"✅ repair_dummies (extras) complete: scanned {ex_scanned}, regenerated {ex_regenerated}, "
              f"skipped {ex_skipped}, missing {ex_missing}, failed {ex_failed}")


# ==========================================
#             RESTORE COMMANDS
# ==========================================

def _verify_title_extras_restore(library, manual_id):
    """IMP-D3: dry-run hash-verify any STAGED files sitting in an extras item's
    own restore/ folder, mirroring cmd_verify_restore's A/B logic per item.

    Unlike the main leaf's targeted restore_folder (whose absence IS an error —
    the user asked to verify THAT id), most extras items normally have nothing
    staged at all, so an item with no restore/ folder or no staged file for it
    is SILENTLY skipped (not an error) — only items with something actually
    staged are reported. Read-only; never mutates; silent when the title has
    no extras."""
    title_id = _extras_title_id(library, manual_id)
    if not title_id or title_id not in library:
        return
    for group_rel, full_path, item in _extras_item_paths(library[title_id]):
        item_dir = os.path.dirname(full_path)
        restore_folder = os.path.join(item_dir, RESTORE_DIR_NAME)
        sub_rel = item.get("sub_rel", "")
        label = f"[extras: {title_id} / {group_rel}/{sub_rel}]"

        split_info = item.get("split_info")
        if split_info and split_info.get("is_split"):
            chunks = split_info.get("chunks", [])
            staged_paths = [os.path.join(restore_folder, c["filename"]) for c in chunks]
            if not any(os.path.exists(p) for p in staged_paths):
                continue  # nothing staged for this item — silent, not an error
            print(f"   > {label} Detected Split File ({len(chunks)} chunks).")
            all_pass = True
            for chunk, target_path in zip(chunks, staged_paths):
                expected_name = chunk["filename"]
                if not os.path.exists(target_path):
                    print(f"     ❌ MISSING: {expected_name}")
                    all_pass = False
                    continue
                if calculate_file_hash(target_path) == chunk["hash"]:
                    print(f"     ✅ Verified: {expected_name}")
                else:
                    print(f"     ❌ CORRUPT: {expected_name}")
                    all_pass = False
            if all_pass:
                print(f"   ✅ {label} SUCCESS: All chunks verified.")
            else:
                print(f"   ❌ {label} FAILURE: Missing or corrupt chunks.")
        else:
            target_path = os.path.join(restore_folder, item.get("filename", ""))
            if not os.path.exists(target_path):
                continue  # nothing staged for this item — silent, not an error
            print(f"   > {label} Detected Standard File.")
            if calculate_file_hash(target_path) == item.get("hash"):
                print(f"   ✅ {label} SUCCESS: Verified against Master Hash.")
            else:
                print(f"   ❌ {label} FAILURE: Hash mismatch.")


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

    # A. CHECK SPLIT FILES
    elif entry.get("split_info") and entry["split_info"].get("is_split"):
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
            print(f"❌ Error: File {entry['filename']} not found in restore folder.")
        else:
            actual_hash = calculate_file_hash(target_path)
            if actual_hash == entry['hash']:
                print(f"✅ SUCCESS: Verified against Master Hash.")
            else:
                print(f"❌ FAILURE: Hash mismatch.")

    # IMP-D3: also dry-run verify any staged extras for this title (mirrors
    # cmd_restore's unconditional title-extras wire — D19 Step 4/6 precedent).
    _verify_title_extras_restore(library, manual_id)


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


def cmd_restore(manual_id, temp_dir=None):
    print(f"--- RESTORING: {manual_id} ---")
    library = load_library()
    if manual_id not in library: print("❌ ID not found."); return False
    real_id, entry = _resolve_alias(library, manual_id)
    if real_id != manual_id:
        print(f"ℹ️  {manual_id} is part of the combined file registered as {real_id} — operating on that.")
        manual_id = real_id

    local_folder = entry['folder_path']
    # [FLAC-CARRYOUT/tempdir] The chunks (and any carried-out holder) live under
    # `restore/` beside the master, or — when a temp_dir is given — under
    # temp_dir/<safe-id>/restore so a big re-merge can read off a second volume.
    # The MERGED OUTPUT + final target always stay in local_folder (the merge would
    # otherwise transiently need chunks + merged output on one volume).
    restore_base, restore_err = _parts_base(local_folder, temp_dir, manual_id)
    if restore_err:
        print(f"❌ {restore_err}")
        return False
    restore_folder = os.path.join(restore_base, RESTORE_DIR_NAME)
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
        # [FLAC-CARRYOUT] Recover any carried-out tracks before the merge: verify the
        # holder (fetched to restore/ beside the chunks), extract the FLAC byte-exact,
        # and build the `carried` record so the merge re-adds it at its original
        # position/flags (D-9). All PRE-PONR and reproducible (re-fetchable).
        carried_out = entry["split_info"].get("carried_out_tracks", [])
        carried_arg = None
        chunk_uids = None
        flac_tmp_paths = []
        if carried_out:
            if len(carried_out) > 1:
                print("❌ Multiple carried-out tracks are not yet supported for restore.")
                return False
            rec = dict(carried_out[0])
            hname = rec.get("holder_filename")
            holder_path = os.path.join(restore_folder, hname)
            if not os.path.exists(holder_path):
                print(f"⏭️  Skipping {manual_id}: carried-out holder missing from restore folder.")
                return False
            # verify the holder hash before trusting it
            if calculate_file_hash(holder_path) != rec.get("holder_hash"):
                print(f"❌ Carry-out holder hash mismatch for {manual_id} — re-fetch required.")
                return False
            # extract the FLAC byte-exact out of the holder
            flac_tmp = os.path.join(restore_folder, f"{os.path.splitext(hname)[0]}.recovered.flac")
            if not extract_payload_from_container(holder_path, flac_tmp):
                print(f"❌ Could not extract the carried-out FLAC from the holder.")
                return False
            flac_tmp_paths.append(flac_tmp)
            rec["flac_path"] = flac_tmp
            # the chunk's track order (renumbered after the drop) for --track-order
            chunk_uids = [m.get("uid") for m in probe_track_manifest(chunk_paths_in_restore[0])]
            carried_arg = rec
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
        # [FLAC-CARRYOUT] the recovered FLAC temp is reproducible (re-extractable from
        # the holder) — journal it so a pre-PONR roolback removes it.
        for fp in flac_tmp_paths:
            journal.record_create_reproducible(fp)
        try:
            # [FLAC-CARRYOUT] pass carried/chunk_uids ONLY when a carry-out exists,
            # so the non-FLAC restore argv is byte-identical (test stubs take
            # merge_video_files(chunk_paths, output_path, seed=None) exactly).
            merge_kwargs = {"seed": seed}
            if carried_arg is not None:
                merge_kwargs["carried"] = carried_arg
                merge_kwargs["chunk_uids"] = chunk_uids
            merged_ok = merge_video_files(chunk_paths_in_restore, merge_tmp_path, **merge_kwargs)
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
            cleanup_paths = list(chunk_paths_in_restore)
            # [FLAC-CARRYOUT] the holder + recovered FLAC temp live in restore/ too
            cleanup_paths += [os.path.join(restore_folder, c["holder_filename"])
                              for c in carried_out if c.get("holder_filename")]
            cleanup_paths += flac_tmp_paths
            for p in cleanup_paths:
                try:
                    os.remove(p)
                except Exception as e:
                    print(f"     ⚠️ Warning: Could not delete {os.path.basename(p)}")
            if not os.listdir(restore_folder):
                try:
                    os.rmdir(restore_folder)
                except:
                    pass
            # [tempdir] remove the now-empty per-entry temp base (temp_dir/<safe-id>)
            # when chunks were redirected — mirrors cmd_push's post-push cleanup.
            if temp_dir and restore_base != local_folder:
                if os.path.isdir(restore_base) and not os.listdir(restore_base):
                    try:
                        os.rmdir(restore_base)
                    except OSError:
                        pass
            # ---------------

            print(f"✅ SUCCESS: {filename} restored & re-indexed.")
            # IMP-D19 Step 6: restore this title's fetched extras too (Card D1).
            # The driver never raises and is silent when nothing is staged (E1).
            restore_title_extras(library, _extras_title_id(library, manual_id))
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
        # IMP-D19 Step 6: restore this title's fetched extras too (Card D1).
        # The driver never raises and is silent when nothing is staged (E1).
        restore_title_extras(library, _extras_title_id(library, manual_id))
        return True


def cmd_restore_group(group_id, episode_range=None, temp_dir=None):
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
        if cmd_restore(mid, temp_dir=temp_dir):
            count += 1

    # IMP-D19 Step 6: explicit batch wire — restore the title's fetched extras
    # after the episode loop (mirrors the Step-4 replace wire in
    # cmd_replace_group). Reload first: each per-episode cmd_restore saved its
    # own updates (and the first successful child already consumed the staged
    # extras via its own success-path wire), so the in-memory `library` above is
    # stale; the reload makes this an idempotent silent no-op rather than a
    # double restore. It ALSO covers the extras-only case — every episode
    # restore skipped (nothing staged for main content), so cmd_restore's
    # success-path wire never fired, yet extras ARE staged.
    library = load_library()
    ex_title_id = _extras_title_id(library, group_id) or (
        _extras_title_id(library, target_ids[0]) if target_ids else None)
    restore_title_extras(library, ex_title_id)

    # [IMP-C18] On a 0-via-range run the warning above already explained the no-op;
    # skip the green "Complete" line so we don't celebrate restoring nothing.
    if not (empty_via_range and count == 0):
        print(f"\n=== Batch Restore Complete: {count} files restored. ===")
    return count


# ==========================================
#   IMP-D19 Step 6 — EXTRAS RESTORE PHASE  (merge-to-temp + verify; additive E1)
# ==========================================
# restore_one_extra mirrors cmd_restore — the IMP-R6 merge-to-temp + verify/
# bless + os.replace swap for a SPLIT extra (journal `create_reproducible` on the
# temp; PONR crossed only after the swap + save, right before the chunk delete),
# the C11 quarantine of a hash-mismatched file/chunk, and the standard
# single-move path for a WHOLE file — but scoped to ONE nested extras item, with
# its journal living in the extra's OWN folder (e.g. …\Specials\). It REUSES the
# same RollbackJournal / merge_video_files / bless_or_verify_merged_hash /
# quarantine_restore_file primitives as the main-content path; it does NOT
# modify cmd_restore, whose journal/PONR/quarantine contract stays byte-for-byte
# unchanged (Card E1 / ROLLBACK_MECHANISM.md §10).
#
# Staging convention (Step 5): fetch stages a whole-file extra as
# <extra folder>/restore/<item filename> and a split extra as its chunk files
# <extra folder>/restore/<split_info.chunks[i].filename>, where
# <extra folder> = os.path.dirname(<title folder_path>/<group_rel>/<sub_rel>) —
# for the flat real samples that is <title>\Specials\restore\. Because the
# staging folder lives INSIDE the extra's subfolder, a fetched extra's
# `Specials`/`Extra` subfolder exists again by restore time (Card D1 — the
# extra is recreated in place, where Plex/Jellyfin expect it).
def restore_one_extra(library, title_id, group_rel, item):
    """Restore ONE fetched extra from its restore/ staging folder back to
    <title folder_path>/<group_rel>/<sub_rel> — the per-file mirror of
    cmd_restore (Card D1 lifecycle, Card E1 additive rollback).

    SILENT no-op (returns False) when nothing is staged for this item, so the
    unconditional wires on the cmd_restore / cmd_restore_group /
    cmd_fetch_restore success paths add zero noise for unfetched extras.

    SPLIT item (IMP-R6 pattern, as cmd_restore's split path): every chunk is
    verified against split_info.chunks[i].hash (a bad chunk is quarantined like
    cmd_restore's — restore/quarantine/<name>.<ts> — and the archived dummy at
    the target is left UNTOUCHED); merge_video_files into a
    <target>.merge_tmp<ext> sibling (journalled as create_reproducible);
    bless_or_verify_merged_hash decides bless (first restore: the merged hash
    BECOMES the canonical item hash, re_hashed=True + merge_seed/merge_tool/
    rehashed_at stamped — so a later replace→fetch→restore cycle verifies) /
    ok / mismatch (loud alarm, pre-PONR rollback, chunks kept); os.replace onto
    the target ONLY after verify/bless passes; then status="restored_local",
    save, PONR + commit, best-effort chunk cleanup.

    WHOLE item (as cmd_restore's standard path — single move, no journal):
    verified against the item `hash` directly (mismatch -> same quarantine),
    then moved over the dummy; status="restored_local", save.

    `uploaded` stays True throughout (the cloud copy still exists), so a later
    replace cycle can re-dummy a restored_local extra. Mutates `item` in place
    and saves `library` on success. Returns True iff the extra was restored."""
    title_entry = library.get(title_id) or {}
    title_folder = title_entry.get("folder_path", "")
    group_os = group_rel.replace("/", os.sep)
    sub_os = item.get("sub_rel", "").replace("/", os.sep)
    target_path = os.path.join(title_folder, group_os, sub_os)
    extra_folder = os.path.dirname(target_path)
    restore_folder = os.path.join(extra_folder, RESTORE_DIR_NAME)
    filename = item.get("filename") or os.path.basename(target_path)
    extra_id = f"{title_id}::{group_rel}/{item.get('sub_rel')}"

    if not os.path.isdir(restore_folder):
        return False  # nothing fetched for this extra's folder — silent skip

    # A. SPLIT RESTORE (mirror of cmd_restore's split path, IMP-R6 pattern)
    if item.get("split_info") and item["split_info"].get("is_split"):
        chunks_meta = item["split_info"]["chunks"]
        chunk_paths = [os.path.join(restore_folder, c["filename"]) for c in chunks_meta]

        staged = [p for p in chunk_paths if os.path.exists(p)]
        if not staged:
            return False  # none of this item's chunks fetched — silent skip
        if len(staged) < len(chunk_paths):
            print(f"     ⏭️  Skipping extra {filename}: incomplete chunks in restore/.")
            return False

        # Pre-merge per-chunk hash verification (mkvmerge is lenient and would
        # silently fold a corrupt chunk into a bad merged file). Quarantine ONLY
        # the offending chunk(s) — clean chunks stay in restore/ for a targeted
        # re-fetch. Deliberately NO stale-target delete here (unlike the legacy
        # line in cmd_restore's split path): the extras merge goes to a
        # .merge_tmp sibling below, so target_path holds the archived DUMMY,
        # which no failure path may remove (IMP-R6: never zero bytes at the path).
        bad_chunks = []
        for c in chunks_meta:
            if calculate_file_hash(os.path.join(restore_folder, c["filename"])) != c["hash"]:
                bad_chunks.append(c["filename"])
        if bad_chunks:
            for chunk_filename in bad_chunks:
                try:
                    q = quarantine_restore_file(restore_folder, chunk_filename)
                    print(f"     ❌ Hash mismatch. Bad file quarantined at {q}. A fresh fetch will re-download.")
                except Exception:
                    print(f"     ❌ Error: Restore chunk hash mismatch ({chunk_filename})! Corrupt?")
            return False

        # Disk pre-check (pre-merge, pre-PONR — as cmd_restore's step 1c).
        try:
            merged_size = sum(os.path.getsize(p) for p in chunk_paths)
        except Exception:
            merged_size = item.get("tech_spec", {}).get("size_bytes", 0)
        if not _free_space_ok(extra_folder, merged_size, will_split=True, eager=False):
            free, required, _short = _disk_shortfall(
                extra_folder, merged_size, will_split=True, eager=False)
            print(f"     ❌ Not enough free space to re-merge extra {filename} "
                  f"(need ~{human_readable_size(required)} in {extra_folder}, "
                  f"have {human_readable_size(free)}). Chunks left in restore/.")
            return False

        # Deterministic merge into a TEMP sibling; the dummy at target_path is
        # only ever replaced by a verified-good file (IMP-R6). Seed choice
        # mirrors cmd_restore: reuse a previously-blessed seed, else the item's
        # short_id (extra_id is the stable last-resort fallback).
        seed = item["split_info"].get("merge_seed") or item.get("short_id") or extra_id
        merge_root, merge_ext = os.path.splitext(target_path)
        merge_tmp_path = f"{merge_root}.merge_tmp{merge_ext}"
        journal = RollbackJournal(extra_folder, extra_id)
        journal.record_create_reproducible(merge_tmp_path)
        try:
            try:
                merged_ok = merge_video_files(chunk_paths, merge_tmp_path, seed=seed)
            except Exception as e:
                print(f"     ❌ Merge crashed: {e}")
                merged_ok = False
            if not merged_ok:
                print(f"     ❌ Merge failed for extra {filename}. Chunks left in restore/ for re-merge.")
                journal.rollback(library)  # removes the temp; the dummy is untouched
                return False

            new_hash = calculate_file_hash(merge_tmp_path)
            # VERIFY-OR-BLESS — same pure helper, same three-way policy as
            # cmd_restore; all mutations and journal calls stay HERE.
            decision = bless_or_verify_merged_hash(item, new_hash)
            if decision == "mismatch":
                stored_tool = item.get("split_info", {}).get("merge_tool", "(unknown)")
                print(f"     🛑 RESTORE HASH MISMATCH — canonical re-hash verification FAILED for extra {extra_id}")
                print(f"        expected (stored canonical): {item.get('hash')}")
                print(f"        actual   (this re-merge)   : {new_hash}")
                print(f"        stored split_info.merge_tool: {stored_tool}; this run: {_current_merge_tool()}")
                print("        Possible corruption or mkvmerge version drift. Chunks kept in restore/ for re-fetch.")
                journal.rollback(library)
                return False
            if decision == "bless":
                item["hash"] = new_hash
                item["re_hashed"] = True
                item["split_info"]["merge_seed"] = seed
                item["split_info"]["merge_tool"] = _current_merge_tool()
                item["split_info"]["rehashed_at"] = _rehashed_at()
            # decision == "ok": already blessed and reproduced — leave untouched.

            # Verify/bless passed → atomically swap the verified-good merged
            # file into place (still pre-PONR; a failed swap keeps the dummy).
            try:
                os.replace(merge_tmp_path, target_path)
            except Exception as e:
                print(f"     ❌ Could not place the merged extra (target locked?): {e}")
                print("        Dummy left intact in place; chunks kept in restore/ for retry.")
                journal.rollback(library)
                return False

            item["status"] = "restored_local"
            save_library(library)
            # PONR placement mirrors cmd_restore: after the swap + save, right
            # before the chunk delete (which is no longer rollback-eligible).
            journal.mark_point_of_no_return()
            journal.commit()
        except Exception as e:
            print(f"     ❌ extras restore failed: {e}")
            if not journal.crossed_ponr:
                journal.rollback(library)  # removes the temp; the dummy is untouched
            return False

        for p in chunk_paths:
            try:
                os.remove(p)
            except Exception:
                print(f"     ⚠️ Warning: Could not delete {os.path.basename(p)}")
        if os.path.isdir(restore_folder) and not os.listdir(restore_folder):
            try:
                os.rmdir(restore_folder)
            except OSError:
                pass
        print(f"     ✅ Restored extra: {filename}")
        return True

    # B. STANDARD (whole-file) RESTORE (mirror of cmd_restore's standard path)
    source_path = os.path.join(restore_folder, filename)
    if not os.path.exists(source_path):
        return False  # not fetched — silent skip

    if calculate_file_hash(source_path) != item.get("hash"):
        try:
            q = quarantine_restore_file(restore_folder, filename)
            print(f"     ❌ Hash mismatch. Bad file quarantined at {q}. A fresh fetch will re-download.")
        except Exception:
            print(f"     ❌ Error: Restore file hash mismatch! Corrupt?")
        return False

    if os.path.exists(target_path):
        try:
            os.remove(target_path)
        except Exception:
            pass
    try:
        shutil.move(source_path, target_path)
    except Exception as e:
        print(f"     ❌ Error moving extra file: {e}")
        return False

    if os.path.isdir(restore_folder) and not os.listdir(restore_folder):
        try:
            os.rmdir(restore_folder)
        except OSError:
            pass

    item["status"] = "restored_local"
    save_library(library)
    print(f"     ✅ Restored extra: {filename}")
    return True


def restore_title_extras(library, title_id):
    """Restore every fetched extra on `title_id` (all groups) from its restore/
    staging folder — the per-title driver, mirroring how cmd_restore_group
    drives cmd_restore ("loop blindly — the per-file restore handles checks").

    Selection is purely disk-driven (the plan's "each extra with downloaded
    files in its restore/ folder"): every item is offered to restore_one_extra,
    which silently skips anything not staged — so this driver is a clean silent
    no-op when nothing was fetched, and the unconditional wires on the
    cmd_restore / cmd_restore_group / cmd_fetch_restore success paths cost
    nothing for extras-less titles. NEVER raises (restore_one_extra self-handles
    its failure paths; the per-item guard here backstops the unexpected, e.g. a
    Windows file lock during chunk hashing), so a one-line wire inside the host
    commands cannot trip their handlers — the main-content restore contract
    stays byte-for-byte unchanged (Card E1). Returns the number of extras
    actually restored."""
    if not title_id or title_id not in library:
        return 0
    groups = library[title_id].get("extras", {}).get("groups", {})
    if not groups:
        return 0  # title carries no extras — nothing to restore
    count = 0
    for group_rel in sorted(groups):
        for item in groups[group_rel].get("items", []):
            try:
                if restore_one_extra(library, title_id, group_rel, item):
                    count += 1
            except Exception as e:
                print(f"     ❌ extras restore failed for {item.get('filename')}: {e}")
    if count:
        print(f"=== ✅ {count} EXTRA(S) RESTORED for {title_id} ===")
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
        # IMP-D3: pending extras — checked BEFORE the season_map/multi_ep_alias
        # skip below, since extras attach to season_map AND movie-leaf titles
        # (an episode/alias never carries its own `extras` block, so this is a
        # no-op for those). Labelled `[extras]` and carries the group's on-disk
        # folder so the Tip section below can hint the correct
        # `push <id> --extras "<folder>"` instead of a bare `push <id>`.
        for group_rel, full_path, item in _extras_item_paths(entry):
            if not item.get("uploaded", False):
                size = item.get("tech_spec", {}).get("size_bytes", 0)
                group_folder = os.path.join(entry.get("folder_path", ""), group_rel.replace("/", os.sep))
                pending_items.append({
                    "id": mid,
                    "filename": f"[extras] {group_rel}/{item.get('sub_rel')}",
                    "size_bytes": size,
                    "extras_folder": group_folder,
                })

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
            # IMP-D3: an extras row's hint must be `push <id> --extras "<folder>"`
            # — a bare `push <id>` would silently no-op the extras (A5). Multiple
            # pending items from the SAME group collapse to one hint (the command
            # pushes every not-yet-uploaded item in that folder).
            printed_extras_groups = set()
            for item in selected_items:
                extras_folder = item.get("extras_folder")
                if extras_folder:
                    key = (item["id"], extras_folder)
                    if key in printed_extras_groups:
                        continue
                    printed_extras_groups.add(key)
                    print(f'   python main.py push {item["id"]} --extras "{extras_folder}"')
                else:
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
            # IMP-D19 Step 7: extras items ARE library content, nested on the
            # TITLE entry (season_map or movie leaf) — add their on-disk paths
            # BEFORE the physical-leaf type skip so extras attached to a
            # season_map are "known" too and never mis-flagged UNPREPPED.
            # Same normpath-lower normalization as the walk below.
            for _grp, ex_path, _item in _extras_item_paths(entry):
                known_paths.add(os.path.normpath(ex_path).lower())
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


def cmd_prep_push_rep(manual_id, filepath, split_method=None, split_val=None, device_id=None, eager_rehash=False, temp_dir=None, extras=None, extras_size=None):
    print(f"=== 🚀 AUTO-PILOT: PREP -> PUSH -> REPLACE for {manual_id} ===")

    # 1. PREP
    # IMP-D19 Step 12b: FORWARD --extras into the prep leg, so a brand-new title is
    # a true one-shot — cmd_prep does the scan+merge (register), and the push+replace
    # phase below then has an extras block to work on. Without this the trailing
    # push_title_extras/replace_title_extras pair silently no-ops on a fresh title.
    # [IMP-C20] Gate BEFORE prep, not just before the split. cmd_push refuses a
    # file mkvmerge cannot split, but by then STEP 1 has already deep-scanned and
    # whole-file hashed the master — 62 GB of wasted work on the incident file.
    # Only meaningful when a split was actually requested.
    # [FLAC-CARRYOUT] gate lets FLAC through — cmd_push (STEP 2) will carry it out.
    if split_method and split_val and refuse_if_unsplittable(filepath, manual_id, carry_out_flac=True):
        print("   Nothing was prepped — the library is untouched.")
        return

    print("\n>>> STEP 1: PREP")
    if not cmd_prep(manual_id, filepath, extras=extras, extras_size=extras_size):
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
    # IMP-D19 Step 3+4: upload THEN dummy the movie's extras (autopilot archives
    # them too). push_title_extras mutates `library` in place (uploaded=True), so
    # replace_title_extras on the same dict immediately sees the just-pushed items.
    if extras:
        library = load_library()
        push_title_extras(library, _extras_title_id(library, manual_id), extras_size, device_id, split_method, split_val)
        replace_title_extras(library, _extras_title_id(library, manual_id))


def cmd_prep_push_rep_season(base_id, folder_path, split_method=None, split_val=None, episode_range=None, device_id=None, eager_rehash=False, temp_dir=None, extras=None, extras_size=None):
    # [NEW] TV SERIES SEQUENTIAL AUTO-PILOT
    print(f"=== 📺 SEASON AUTO-PILOT (SEQUENTIAL): PREP -> PUSH -> REPLACE for {base_id} ===")

    # 1. PREP SEASON
    # IMP-D19 Step 12b: FORWARD --extras into the prep leg (same gap as the movie
    # autopilot). cmd_prep_season scans+merges ONCE, onto the season_map, after its
    # episode loop — the per-episode cmd_prep calls never receive `extras` — so this
    # run registers the folders exactly once and the extras phase below can upload
    # them.
    # [IMP-C20] Same pre-prep gate, across the whole season. cmd_prep_season
    # scans+hashes EVERY episode before the first push runs, so one unsplittable
    # episode would otherwise waste the entire season's prep. The enumeration
    # mirrors cmd_prep_season's exactly so the two cannot disagree.
    if split_method and split_val:
        blocked = False
        for fn in sorted([f for f in os.listdir(folder_path)
                          if f.lower().endswith(VIDEO_EXTENSIONS)]):
            if refuse_if_unsplittable(os.path.join(folder_path, fn), fn, carry_out_flac=True):
                blocked = True
        if blocked:
            print("   Nothing was prepped — the library is untouched.")
            return

    print("\n>>> STEP 1: PREP SEASON")
    cmd_prep_season(base_id, folder_path, extras=extras, extras_size=extras_size)

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
        (C1 not merged → no progress file).
        IMP-D4: also reproduces --extras/--extras-size when the run had them —
        the season's extras still need pushing after a resume, and the printed
        command must not silently drop them."""
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
        if extras:
            extras_joined = ";".join(extras)
            parts.append(f'--extras "{extras_joined}"')
        if extras_size:
            e_method, e_val = extras_size
            parts.append("--extras-size none" if e_method == "NONE" else f"--extras-size {e_method} {e_val}")
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
    # IMP-D19 Step 3+4: upload THEN dummy the season's extras (autopilot archives
    # them too). push_title_extras mutates `library` in place (uploaded=True), so
    # replace_title_extras on the same dict immediately sees the just-pushed items.
    if extras:
        library = load_library()
        push_title_extras(library, _extras_title_id(library, base_id), extras_size, device_id, split_method, split_val)
        replace_title_extras(library, _extras_title_id(library, base_id))


# ===========================================================================
#   cmd_prep_push_rep_enrich — movie autopilot + enrich (IMP-D22).
#
# Folds `cmd_enrich_metadata` into the prep->push->replace autopilot: archive
# the movie EXACTLY as `cmd_prep_push_rep` does, then (once the archive is
# confirmed `archived`) enrich it — preset a CLI-supplied `-tmdbid` via
# `cmd_set_tmdb`, resolve against TMDB, write metadata, and — after a ONE-TIME
# confirmation gate (Decision 2/3) — stamp the `[tmdbid-<id>]` folder token and
# download art. `-tvdbid` is refused outright (Decision 1 — TMDB-only; a TVDB
# id is a different numbering space and would fetch the wrong title).
#
# CANDIDATE A (this implementation): fully isolated from `cmd_enrich_metadata`
# — zero lines of it touched. `_enrich_after_archive` below duplicates ONLY
# the small resolve-waterfall + apply-loop its per-unit body already uses
# (main.py:~2486-2632), reusing every other primitive
# (`_gather_enrich_units`/`_resolve_unit(_by_id)`/`_unit_preset_tmdb_id`/
# `_exa_resolve_tmdb_id`/`_download_unit_images`/`_apply_episode_overviews`/
# `_write_nfo`/`cmd_rename_folder`) verbatim, and swaps the unconditional
# stamp for the confirm-gated one below.
# ===========================================================================

def _refuse_tvdbid():
    """Refuse a `-tvdbid` request outright (Decision 1, LOCKED 2026-08-28 —
    MediaVault is TMDB-only for movies/series/anime; no TVDB client exists,
    and a TVDB id is a DIFFERENT numbering space from a TMDB id, so writing it
    into metadata.tmdb_id would silently corrupt the entry / fetch the wrong
    title's artwork). Prints the exact wording BOTH new commands share (a
    stable substring the tests assert on) and returns False; the caller must
    return immediately WITHOUT calling `cmd_prep_push_rep(_season)` — nothing
    runs at all (no archive, no enrich)."""
    print("❌ -tvdbid is not supported: MediaVault is TMDB-only for movies, series, and anime\n"
          "   (no TVDB client exists, and a TVDB id is a DIFFERENT numbering space from a TMDB\n"
          "   id — writing it into metadata.tmdb_id would silently corrupt the entry / fetch the\n"
          "   wrong title's artwork). Look the title up on themoviedb.org and pass -tmdbid <id>.")
    return False


def _make_rename_confirm(choice, note=None):
    """Build the `confirm(old_folder, new_folder) -> bool` callable for the
    post-archive folder-rename gate (Decision 2 — resolve/prompt ONCE, right
    after the archive; Decision 3 — non-interactive never renames).

    `choice` is one of the literal strings:
      * "ask" (default) — prompt ONLY when `sys.stdin.isatty()` is True; a
        non-interactive session (any pytest run, the smoke suite, a cron/script
        invocation with no flag) defaults to NOT renaming.
      * "yes" (from CLI --yes) — auto-confirm, no prompt.
      * "no"  (from CLI --no-rename) — auto-decline, no prompt.

    `note` (optional) is an extra line printed after the rename summary (e.g. a
    season-specific caveat); the movie command passes `note=None`.

    This is the ONE new `input()` call in the entire codebase — reached ONLY
    when `choice == "ask"` AND `sys.stdin.isatty()` is True, so it can never
    hang a non-interactive run (this is load-bearing for the smoke gate)."""
    def _confirm(old_folder, new_folder):
        print(f'   > "{old_folder}" will be changed to "{new_folder}"')
        if note:
            print(f"   > {note}")
        if choice == "yes":
            print("     > auto-confirmed (--yes).")
            return True
        if choice == "no":
            print("     > auto-declined (--no-rename) — leaving the folder as-is.")
            return False
        if not sys.stdin.isatty():
            print("     > non-interactive session — defaulting to NOT renaming "
                  "(pass --yes to auto-confirm, or run rename_folder yourself later).")
            return False
        answer = input("     > Rename this folder now? [y/N]: ").strip().lower()
        return answer in ("y", "yes")
    return _confirm


def _enrich_after_archive(real_id, write_nfo, no_web, gate):
    """Candidate-A enrich leg for `cmd_prep_push_rep_enrich`: resolve + apply +
    confirm-gated stamp for the ONE enrich unit containing `real_id`, WITHOUT
    ever calling `cmd_enrich_metadata` (zero lines of it touched — IMP-D22).

    Mirrors `cmd_enrich_metadata`'s own per-unit resolve waterfall and apply
    block (main.py:~2486-2632) verbatim, scoped to a single unit instead of
    looping the whole library, and swaps its unconditional
    `if will_stamp: cmd_rename_folder(...)` for the confirm-gated
    `if gate(old, new): cmd_rename_folder(...)` below. Also independently
    replicates `cmd_enrich_metadata`'s own "no TMDB API key" guard — the
    caller must not be assumed to have checked it already.

    Prints only — never raises, EXCEPT that `cmd_rename_folder` can raise
    `RollbackHardFail` (a post-PONR failure); the caller wraps this call and
    turns that into a printed warning (Decision 7). Every other non-confident
    outcome (no key / no unit / TMDB error / none / ambiguous) is a plain
    warn-and-return, matching Decision 7's "enrich should just be not done,
    but the command can finish."."""
    api_key = mvcommon.tmdb_api_key()
    if not api_key:
        print("❌ No TMDB API key configured. Set tmdb.api_key in mvconfig.json "
              "(see mvconfig.example.json). Enrich skipped.")
        return

    library = load_library()
    # Scoped to real_id — a movie never has siblings sharing its exact id
    # (modulo the pre-existing startswith-prefix edge case _gather_enrich_units
    # itself already has; not this task's to fix), so exactly one unit is
    # expected. A collision (if it ever occurred) would just enrich the first.
    units = _gather_enrich_units(library, id_or_prefix=real_id)
    if not units:
        print(f"⚠️  enrich: no enrich unit found for {real_id} — skipped.")
        return
    unit = units[0]
    label = f"{unit['kind'].upper()} {unit['key']}"

    web_fallback = (not no_web) and bool(mvcommon.exa_api_key())
    preset_id = _unit_preset_tmdb_id(unit, library)
    # MIRRORS the defensive try/except wrapping this SAME resolve waterfall in
    # cmd_enrich_metadata (main.py:~2487-2507) — keep the two in sync. Adapted
    # to the single-unit context: warn-and-RETURN instead of `continue`, and no
    # n_skipped counter. An escaping resolver error here would abort a run whose
    # archive leg has ALREADY completed, which Decision 7 forbids.
    try:
        if preset_id:
            print(f"   ↳ {label}: using preset tmdb_id={preset_id} (manual) — fetching by id, no search.")
            res = _resolve_unit_by_id(unit, preset_id, api_key)
        else:
            res = _resolve_unit(unit, api_key)
            if web_fallback and res["status"] in ("none", "ambiguous"):
                exa_id = _exa_resolve_tmdb_id(unit["title"], unit["year"], unit["kind"])
                if exa_id is not None:
                    by_id = _resolve_unit_by_id(unit, exa_id, api_key)
                    if by_id.get("status") == "confident":
                        print(f"   ↳ {label}: resolved via web search: tmdb_id={exa_id}")
                        res = by_id
    except Exception as e:  # defensive — resolvers already swallow, but never crash the run
        print(f"⏭️  {label}: TMDB error — enrich skipped ({e}).")
        return

    if res["status"] == "error":
        print(f"⏭️  {label}: TMDB error — enrich skipped.")
        return
    if res["status"] == "none":
        print(f"❓ {label}: NO TMDB match for '{unit['title']}' — enrich skipped "
              f"(resolve with: python main.py set_tmdb {unit['key']} <tmdb_id>).")
        return
    if res["status"] == "ambiguous":
        print(f"❓ {label}: AMBIGUOUS '{unit['title']}' — {len(res['candidates'])} "
              f"candidate(s), enrich skipped (not writing).")
        return

    # --- confident: apply (mirrors cmd_enrich_metadata's apply block, main.py:~2572-2591) ---
    tmdb_id = res["tmdb_id"]
    tmdb_title = res.get("title")
    tmdb_year = res.get("year")
    tmdb_overview = res.get("overview")
    live = load_library()
    for eid in unit["ids"]:
        ent = live.get(eid)
        if ent is None:
            continue
        r_id, target = _resolve_alias(live, eid)
        meta = target.setdefault("metadata", {})
        meta["tmdb_id"] = tmdb_id
        if tmdb_title:
            cur = meta.get("title")
            if _title_is_id_shaped(cur, r_id) or cur == tmdb_title:
                meta["title"] = tmdb_title
        if tmdb_year is not None:
            meta["year"] = tmdb_year
        if tmdb_overview:
            meta["overview"] = tmdb_overview
    save_library(live)
    print(f"     wrote metadata.tmdb_id on {len(unit['ids'])} entr(y/ies).")

    # Confirm-gated stamp (Decision 2/3) REPLACES cmd_enrich_metadata's
    # unconditional `if will_stamp: cmd_rename_folder(...)`.
    folder = unit.get("folder")
    base_name = os.path.basename(os.path.normpath(folder)) if folder else ""
    will_stamp = bool(folder) and not _has_tmdb_token(base_name)
    if will_stamp:
        # Same canonical spelling cmd_enrich_metadata uses (IMP-U6) — this block
        # is a deliberate duplicate of its stamping logic (ARCHITECTURE §6.3a
        # standing sync obligation), so the two MUST emit the same format.
        new_name = f"{base_name} {mvcommon.CANONICAL_TMDB_TOKEN_FMT.format(id=tmdb_id)}"
        new_folder = os.path.join(os.path.dirname(os.path.normpath(folder)), new_name)
        if gate(folder, new_folder):
            ok = cmd_rename_folder(folder, new_name)  # may raise RollbackHardFail — caller catches
            if ok:
                unit = _retarget_unit_folders(unit, folder, new_folder)
                folder = new_folder
        else:
            print("     ⏭️  folder rename declined — run rename_folder later to add the token.")
    elif folder:
        # Format-agnostic wording, matching cmd_enrich_metadata's twin print.
        print(f"     folder already has a TMDB token — skip stamp ({base_name}).")

    image_base = _tmdb_image_base(api_key)
    n_images = _download_unit_images(unit, res, image_base, folder)
    print(f"     downloaded {n_images} image(s).")

    if unit["kind"] == "show":
        _apply_episode_overviews(unit, tmdb_id, api_key)

    if write_nfo and folder:
        _write_nfo(
            folder,
            kind=unit["kind"],
            title=res.get("title"),
            year=res.get("year"),
            tmdb_id=tmdb_id,
            overview=res.get("overview", ""),
            vote_average=res.get("vote_average"),
            api_key=api_key,
        )


def cmd_prep_push_rep_enrich(manual_id, filepath, split_method=None, split_val=None,
                               device_id=None, eager_rehash=False, temp_dir=None,
                               extras=None, extras_size=None,
                               tmdb_id=None, tvdb_id=None,
                               write_nfo=False,  # Decision 4 (2026-08-28, LOCKED): OFF by
                                                  # default — the NFO CONTENT enrichment
                                                  # below is independent of this default.
                               no_web=False,
                               rename_choice="ask"):
    """Autopilot: archive (prep -> push -> replace, EXACTLY `cmd_prep_push_rep`)
    THEN enrich the just-archived movie (IMP-D22). `tmdb_id` presets the TMDB id
    via `cmd_set_tmdb` (exactly as a user would type `set_tmdb <id> <tmdb_id>`)
    so no title search is needed; `tvdb_id` is REFUSED outright (Decision 1 —
    MediaVault is TMDB-only). The folder-rename token stamp is resolved and
    confirmed ONCE, immediately AFTER the archive completes (Decision 2), via
    `rename_choice`: "ask" (default — interactive prompt when
    `sys.stdin.isatty()`, else auto-"no"), "yes" (from CLI --yes), or "no"
    (from CLI --no-rename).

    Returns True once the archive itself completed (regardless of whether the
    enrich leg fully succeeded — warn-and-continue). Returns False if the
    archive did not complete (enrich is then skipped entirely) or if -tvdbid
    was supplied (refused before anything runs)."""
    if tvdb_id is not None:
        return _refuse_tvdbid()

    # --- archive phase: exactly cmd_prep_push_rep, every argument forwarded,
    # nothing added, nothing dropped. Its return value is NOT inspected (it
    # returns None on some paths today) — completion is checked below via the
    # library's post-run status instead.
    cmd_prep_push_rep(manual_id, filepath, split_method, split_val,
                       device_id=device_id, eager_rehash=eager_rehash, temp_dir=temp_dir,
                       extras=extras, extras_size=extras_size)

    library = load_library()
    if manual_id not in library:
        print(f"⚠️  prep did not create a library entry for {manual_id} — enrich skipped.")
        return False
    real_id, entry = _resolve_alias(library, manual_id)
    if entry.get("status") != "archived":
        print(f"⚠️  {real_id} is not archived yet (status={entry.get('status')!r}) — enrich skipped.")
        print(f"   > Once the archive finishes: enrich_metadata {real_id} --apply")
        print(f"   > (or simply re-run this same command).")
        return False

    # Preset the CLI-supplied tmdb_id EXACTLY as a user would type
    # `set_tmdb <id> <tmdb_id>` — no part of it is reimplemented here.
    if tmdb_id is not None:
        cmd_set_tmdb(real_id, tmdb_id)

    gate = _make_rename_confirm(rename_choice)
    try:
        _enrich_after_archive(real_id, write_nfo, no_web, gate)
    except RollbackHardFail as hf:
        # Decision 7: a post-PONR rename failure warns and continues — the
        # archive already succeeded, so this never re-raises nor returns False.
        print(f"⚠️  Enrich folder rename left incomplete: {hf.state} — {hf.reason}")
        print(f"   > To finish it: {hf.resume_cmd}")

    print("\n✅✅✅ AUTO-PILOT COMPLETE (archive + enrich).")
    return True


# ===========================================================================
#   cmd_prep_push_rep_season_enrich — series autopilot + enrich (IMP-D22 Step 2).
#
# Same shape as `cmd_prep_push_rep_enrich` above (archive first, enrich only
# once the archive is confirmed complete), built on the SAME Candidate-A
# mechanism (`_enrich_after_archive` reused verbatim — this is NOT a third
# copy of the enrich waterfall). Two things differ from the movie case
# (Decision 6, revised 2026-08-28 after a live-library audit):
#   1. "Archived" must be checked against THIS RUN'S actual targets — a
#      season run can be `episode_range`-scoped, so `_season_run_target_ids`
#      reconstructs the exact target-id list `cmd_prep_push_rep_season`
#      computes internally (that function does not expose it).
#   2. Enrich is scoped by `base_id` directly (NOT a derived show id) and the
#      folder-rename confirmation note is conditional on whether the season
#      folder and the show folder are actually different paths — see the two
#      "Why harder" findings in docs/feature-prep-push-rep-enrich/DECISIONS.md.
# ===========================================================================

def _season_run_target_ids(library, base_id, episode_range):
    """The ordered, de-aliased list of episode ids THIS season run targeted
    (whole season, or the `episode_range` subset) — or `[]` when `base_id` is
    missing from `library` or `episode_range` is an invalid string.

    Mirrors `cmd_prep_push_rep_season`'s own STEP 2 target-id derivation
    verbatim (main.py:~7362-7387, grep "^def cmd_prep_push_rep_season\\b" to
    re-locate) — keep in sync; that function does not expose this list so it
    must be reconstructed identically here. An invalid `episode_range` means
    `cmd_prep_push_rep_season` itself already printed "❌ Invalid range." and
    processed NOTHING, so this helper degrading to "nothing to check" (`[]`)
    is correct, not a new failure mode."""
    if base_id not in library:
        return []
    target_ids = library[base_id]["children"]

    if episode_range:
        try:
            start, end = map(float, episode_range.split('-'))
        except ValueError:
            return []
        filtered_ids = []
        for mid in target_ids:
            ep_num = episode_num_from_id(mid, base_id)
            if ep_num is not None and start <= ep_num <= end:
                filtered_ids.append(mid)
        target_ids = filtered_ids

    # De-alias: collapse multi_ep_alias ids to their primaries; dedup order-preserving.
    seen = set()
    dealiased = []
    for mid in target_ids:
        real_id, _ = _resolve_alias(library, mid)
        if real_id not in seen:
            seen.add(real_id)
            dealiased.append(real_id)
    return dealiased


def cmd_prep_push_rep_season_enrich(base_id, folder_path, split_method=None, split_val=None,
                                      episode_range=None, device_id=None, eager_rehash=False,
                                      temp_dir=None, extras=None, extras_size=None,
                                      tmdb_id=None, tvdb_id=None,
                                      write_nfo=False,  # Decision 4 (2026-08-28, LOCKED): OFF by
                                                         # default — same one-line-flip contract
                                                         # as `cmd_prep_push_rep_enrich`'s signature.
                                      no_web=False,
                                      rename_choice="ask"):
    """Season autopilot: archive (prep -> push -> replace, EXACTLY
    `cmd_prep_push_rep_season`, every argument forwarded unmodified) THEN
    enrich the season THIS RUN actually completed (IMP-D22 Step 2). `tmdb_id`
    presets the TMDB id via `cmd_set_tmdb` on the first episode LEAF under
    `base_id` (a season_map cannot itself carry `metadata.tmdb_id` —
    `cmd_set_tmdb` refuses a season_map container, so the preset lands on a
    child leaf instead); `tvdb_id` is REFUSED outright (Decision 1 —
    MediaVault is TMDB-only).

    `cmd_prep_push_rep_season` owns its OWN internal `_season_resume_cmd`
    closure, which prints the mid-loop-failure resume command exactly as
    today (rollback-adjacent per CLAUDE.md's change-gate) — this function
    does NOT touch, call, or reimplement it; a season archive failure's
    resume messaging is completely unchanged by this command's existence.

    Enrich is scoped DIRECTLY by `base_id` (Decision 6, revised 2026-08-28) —
    NOT a derived show id: the user's real per-season-year id convention
    ('tv-en-2022-peakyblinders-s06' vs '…-2019-…-s05') means every season
    already lands in its own separate `_gather_enrich_units` bucket, so
    deriving a show id buys nothing and would only risk reaching an unrelated
    sibling season's preset. Reuses `_enrich_after_archive` UNCHANGED (Step
    1's isolated enrich leg) by passing `base_id` in the same
    `id_or_prefix=<this unit's own id>` slot the movie command passes its own
    `real_id` — no enrich logic is duplicated a third time.

    The folder-rename confirmation note is CONDITIONAL ("Why harder" reason
    1): it names the parent-show-folder relationship only when the resolved
    unit folder actually DIFFERS from the season_map's own `folder_path` (the
    classic `<Show>/Season NN/` layout); for the dominant flat/root-level
    layout (season folder == show folder, 46 of the user's real shows) the
    note is omitted — printing it there would be factually wrong.

    Returns True once THIS RUN'S actual targets (`_season_run_target_ids`,
    range-scoped when `episode_range` is given) all show `status ==
    "archived"` — regardless of whether the enrich leg fully succeeded
    (warn-and-continue, Decision 7). Returns False if this run's targets
    never fully archived, or if -tvdbid was supplied (refused before
    anything runs)."""
    if tvdb_id is not None:
        return _refuse_tvdbid()

    # --- archive phase: exactly cmd_prep_push_rep_season, every argument
    # forwarded, matching the existing dispatcher's own call shape (positional
    # `episode_range` in the same slot). Its return value is not inspected —
    # completion is checked below via the library's post-run status, exactly
    # like the movie command does.
    cmd_prep_push_rep_season(base_id, folder_path, split_method, split_val,
                              episode_range, device_id=device_id, eager_rehash=eager_rehash,
                              temp_dir=temp_dir, extras=extras, extras_size=extras_size)

    library = load_library()
    target_ids = _season_run_target_ids(library, base_id, episode_range)
    if not target_ids:
        print("⚠️  nothing was archived this run — enrich skipped.")
        return False
    for tid in target_ids:
        status = library.get(tid, {}).get("status")
        if status != "archived":
            print(f"⚠️  {tid} is not archived yet (status={status!r}) — enrich skipped.")
            return False

    # Preset the CLI-supplied tmdb_id EXACTLY as a user would type
    # `set_tmdb <id> <tmdb_id>` — but `cmd_set_tmdb` refuses a season_map, so
    # land it on the FIRST child of base_id that resolves (via `_resolve_alias`)
    # to a real LEAF (has a `filename`), never on base_id itself.
    if tmdb_id is not None:
        children = library.get(base_id, {}).get("children", [])
        leaf_id = None
        for cid in children:
            try:
                real_cid, target = _resolve_alias(library, cid)
            except KeyError:
                continue
            if isinstance(target, dict) and "filename" in target:
                leaf_id = real_cid
                break
        if leaf_id:
            cmd_set_tmdb(leaf_id, tmdb_id)
        else:
            # Should not happen post-completion-check (target_ids was non-empty
            # above) — degrade gracefully instead of crashing; the normal
            # search waterfall inside _enrich_after_archive still runs.
            print(f"⚠️  {base_id} has no episode leaf to preset tmdb_id on — "
                  f"skipping preset (the normal search waterfall will still run).")

    # Confirmation gate note — CONDITIONAL ("Why harder" reason 1): only
    # mention the SHOW folder when it actually differs from the season's own
    # folder_path. This peeks at `_gather_enrich_units` here (a second,
    # read-only call — `_enrich_after_archive` below makes its own); both
    # calls are pure and side-effect-free, so the duplication is harmless.
    units = _gather_enrich_units(library, id_or_prefix=base_id)
    unit_folder = units[0].get("folder") if units else None
    season_folder = library.get(base_id, {}).get("folder_path")
    if unit_folder and season_folder and _norm_path(unit_folder) != _norm_path(season_folder):
        gate = _make_rename_confirm(
            rename_choice,
            note="(this is the SHOW folder — the parent of the season you just archived)")
    else:
        gate = _make_rename_confirm(rename_choice)

    try:
        _enrich_after_archive(base_id, write_nfo, no_web, gate)
    except RollbackHardFail as hf:
        # Decision 7: a post-PONR rename failure warns and continues — the
        # archive already succeeded, so this never re-raises nor returns False.
        print(f"⚠️  Enrich folder rename left incomplete: {hf.state} — {hf.reason}")
        print(f"   > To finish it: {hf.resume_cmd}")

    print("\n✅✅✅ AUTO-PILOT COMPLETE (archive + enrich).")
    return True


# ==========================================
#   GOOGLE PHOTOS IDENTITY — THE APPLY ENGINE (IMP-C25)
# ==========================================
# THE only writer of `gphotos` blocks (docs/feature-fetch-datetime/PLAN.md Step 6; P-10, OD-2 ruling).
# Every producer — the fetch (mainfetch stays library-read-only), map_photos, check_photos, the
# auto-confirm, resolve_photos — only APPENDS what it learned to the per-account learned sidecar
# (mvcommon.append_learned: the durable record that survives a lost library update, IMP-C24). This engine
# folds those records, plus each named object's capture facts (mvcommon.read_capture — the file's own
# facts at prep/push, P-11), into the library. An object is named by a record's manual_id (the owning
# library id — the TITLE id for extras; an alias resolves one hop to its primary) AND its current SHA-256
# (the P-2 pin). Per object:
#   * a TRUSTED block is updated; an absent or untrusted one (other bytes: re-mastered, re-split) is
#     replaced by a block pinned to the current hash; a block written by NEWER code (v > 1) is never
#     touched (conflict `block-version`);
#   * capture facts (sha1 / dedup_key, size_bytes, width / height, duration_s, date_utc, uploaded_name,
#     pushed) build a new block, and refresh a trusted one (they describe these very bytes) — except a
#     content key contradicting the block's own (conflict `capture-content-mismatch`: nothing folded);
#   * account records: mvcommon.apply_learned per account — P-5 proof ladder, alternates, approach
#     verdicts, dead photo ids, search_override; `confirmed` only from a verified map / check / fetch /
#     confirm record, never from a capture and never from a manual match alone. Its conflicts are
#     reported, never applied;
#   * home_account (P-4's learned layer) = mvcommon.home_account(...) whenever an account holds a verified
#     (or user-vouched) copy; otherwise the key is absent and routing falls back to the id prefix —
#     exactly what home_account() answers then. mvconfig overrides stay the caller's top layer;
#   * nothing but the `gphotos` key of the object's own node ever changes.
# A capture alone never creates a block: only objects some record names are touched. Records whose object
# no longer exists (re-split, re-mastered, re-prepped, unknown id) are ORPHANS — reported, never applied.
# Library I/O: a fresh load_library() per attempt; --apply saves ONCE, guarded by the OD-2 fingerprint
# (size, mtime_ns, SHA-256 of every library file, taken before the load and re-checked right before the
# save: changed -> reload + re-patch, which is idempotent; after GPHOTOS_APPLY_ATTEMPTS changes -> abort).
# No lock and no rollback journal (P-10); switch to mvcommon.save_library_atomic when IMP-C24 lands.

GPHOTOS_APPLY_ATTEMPTS = 3  # OD-2 ruling: the library changed under the engine this many times -> abort
_GP_BUSY = "library changed by another process — re-run when idle"
# The block-level facts a capture contributes (P-1 keys; read_capture's other facts — role, index,
# local_name, mtime_utc, approach_hint, remote_dir — are not block keys).
_GP_CAPTURE_KEYS = ("sha1", "dedup_key", "size_bytes", "width", "height", "duration_s", "date_utc",
                    "uploaded_name", "pushed")
_GP_SHA1_RE = re.compile(r"^[0-9a-fA-F]{40}$")
_GP_ABSENT = object()  # a node that has no `gphotos` key at all


def _gp_is_int(value):
    return isinstance(value, int) and not isinstance(value, bool)


def _gp_line(exc):
    """An exception as one line of text (a message may carry newlines)."""
    return " ".join(f"{type(exc).__name__}: {exc}".split())


def _library_fingerprint():
    """The OD-2 change detector: (size, mtime_ns, SHA-256) of every library file load_library reads — read
    through mvcommon's OWN LIBRARY_* bindings at call time (the binding hazard; main does not even import
    LIBRARY_OTHERS) — with None for a file that does not exist. Equal fingerprints = nobody wrote."""
    prints = []
    for path in (mvcommon.LIBRARY_MOVIES, mvcommon.LIBRARY_SERIES, mvcommon.LIBRARY_ANIME,
                 mvcommon.LIBRARY_OTHERS):
        try:
            with open(path, "rb") as fh:
                st = os.fstat(fh.fileno())
                data = fh.read()
        except FileNotFoundError:
            prints.append(None)
            continue
        prints.append((st.st_size, st.st_mtime_ns, hashlib.sha256(data).hexdigest()))
    return tuple(prints)


def _gp_content_key(block):
    """A block's content key — its canonical dedupKey, from dedup_key, else from sha1 — or None."""
    key = gpweb.normalize_dedup_key(block.get("dedup_key"))
    sha1 = block.get("sha1")
    if key is None and isinstance(sha1, str) and _GP_SHA1_RE.match(sha1):
        key = gpweb.dedup_key(sha1.lower())
    return key


def _gp_confirmed(block):
    """True when a block holds a confirmed account record (the rule mvcommon's cloud_state uses)."""
    accounts = block.get("accounts") if isinstance(block, dict) else None
    return isinstance(accounts, dict) and any(isinstance(r, dict) and r.get("confirmed") is True
                                              for r in accounts.values())


def _gp_fold_capture(block, sha256, facts):
    """Fold one object's capture facts into its block (P-11: the file's own facts, never proof of cloud
    presence). block None -> a new block built from them; a trusted block takes the capture's values (they
    describe these very bytes; `pushed` is the latest push) unless the capture's content key contradicts
    the block's. -> (block, conflict code or None); on a conflict the block comes back unchanged."""
    try:
        cap = mvcommon.new_gphotos_block(sha256, **{k: facts.get(k) for k in _GP_CAPTURE_KEYS})
    except ValueError:
        return block, "capture-invalid"
    if block is None:
        return cap, None
    held = _gp_content_key(block)
    if held and cap.get("dedup_key") and held != cap["dedup_key"]:
        return block, "capture-content-mismatch"
    merged = dict(block)
    merged.update((k, cap[k]) for k in _GP_CAPTURE_KEYS if k in cap)
    return mvcommon._ordered(merged, mvcommon._BLOCK_KEYS), None  # the writer's own P-1 key order


def _gp_with_home(block, manual_id, prefix_map, default):
    """Set P-4's learned home_account — mvcommon.home_account() computed WITHOUT the stored field, so it
    always follows the evidence — when an account holds a verified or user-vouched copy (verified_by set);
    otherwise remove the key: routing then falls back to the id prefix, which is what home_account() answers
    for such a block anyway. The result stays a pure function of the account records (re-applying the
    sidecar reproduces it). Keeps the P-1 key order."""
    rest = {k: v for k, v in block.items() if k != "home_account"}
    accounts = rest.get("accounts")
    learned = isinstance(accounts, dict) and any(
        account in mvcommon.GP_ACCOUNTS and isinstance(r, dict) and r.get("verified_by") in mvcommon.GP_PROOF_LADDER
        for account, r in accounts.items())
    if learned:
        rest["home_account"] = mvcommon.home_account(rest, manual_id, prefix_map, default)
    return mvcommon._ordered(rest, mvcommon._BLOCK_KEYS)


def _gp_fold_object(obj, raw, reds, capture, prefix_map, default, name=None):
    """Fold one object's reduced records ({account: reduced}), — capture=True — its capture facts and — name — the
    uploaded name a `map_photos --repair names` proved (it wins over the capture's) into its block. raw = the
    node's current `gphotos` value (_GP_ABSENT when there is none).
    -> (the new block, or None to leave the node untouched; action create | replace | update;
        [(account or None for block level, conflict code)])."""
    sha256 = obj["sha256"]
    if isinstance(raw, dict) and _gp_is_int(raw.get("v")) and raw["v"] > mvcommon.GPHOTOS_BLOCK_VERSION:
        return None, None, [(None, "block-version")]  # written by newer code: never overwritten
    trusted = mvcommon.block_trusted(raw, sha256)
    action = "update" if trusted else ("create" if raw is _GP_ABSENT else "replace")
    block = copy.deepcopy(raw) if trusted else None  # untrusted (P-2): ignored, and replaced below
    problems = []
    if capture and isinstance(obj.get("capture"), dict):
        block, problem = _gp_fold_capture(block, sha256, obj["capture"])
        if problem:
            problems.append((None, problem))
    if block is None:
        block = mvcommon.new_gphotos_block(sha256)
    if name:
        block = mvcommon._ordered({**block, "uploaded_name": name}, mvcommon._BLOCK_KEYS)
    for account in mvcommon.GP_ACCOUNTS:
        if account in reds:
            folded, _changed, problem = mvcommon.apply_learned(block, reds[account], account)
            if problem:
                problems.append((account, problem))
            else:
                block = folded
    block = _gp_with_home(block, obj["manual_id"], prefix_map, default)
    if trusted:
        return (None if block == raw else block), action, problems
    # A new block that learned nothing beyond its pin carries no fact: not worth a library write.
    return (block if set(block) - {"v", "sha256"} else None), action, problems


def _gp_flat(value, prefix=""):
    """{dotted path: leaf value} of nested dicts (lists are leaves); {} for a non-dict top level."""
    if not isinstance(value, dict):
        return {prefix: value} if prefix else {}
    out = {}
    for key, val in value.items():
        out.update(_gp_flat(val, f"{prefix}.{key}" if prefix else str(key)))
    return out


def _gp_diff(old, new):
    """{dotted path: [old value, new value]} for every leaf that differs (None = absent): the new block's
    paths in order, then the removed ones. A non-dict `old` (no block, a garbage value) diffs as empty."""
    before, after = _gp_flat(old), _gp_flat(new)
    paths = list(after) + [p for p in before if p not in after]
    return {p: [before.get(p), after.get(p)] for p in paths if before.get(p) != after.get(p)}


def _gp_resolve(library, manual_id):
    """(real id, entry) for a record's manual_id — a multi_ep_alias resolves ONE hop to its primary
    (_resolve_alias) — or (manual_id, None) when there is no such entry or the alias dangles."""
    if not isinstance(library.get(manual_id), dict):
        return manual_id, None
    try:
        real, entry = _resolve_alias(library, manual_id)
    except (KeyError, TypeError):  # a malformed alias entry
        return manual_id, None
    if not isinstance(entry, dict) or entry.get("type") == "multi_ep_alias":
        return manual_id, None
    return real, entry


def _gp_plan(library, reduced, named, capture, prefix_map, default, names=()):
    """One pass over a freshly loaded library: which nodes get which block, and the per-pass report fields.
    reduced = mvcommon.reduce_learned(...) {(account, sha256): reduced}; named = {(account, sha256): [every
    manual_id the records gave]} (reduce keeps only the last one; aliases and duplicates need them all); names =
    [{manual_id, sha256, uploaded_name}] — validated `--repair names` facts (gphotos_apply).
    -> ([(node, new block)], {objects, changed, unchanged, confirmed, conflicts, orphans})."""
    claims, owners = {}, {}
    for key, red in reduced.items():
        account, sha256 = key
        owners[key] = []
        for manual_id in named.get(key) or [red["manual_id"]]:
            real, entry = _gp_resolve(library, manual_id)
            if entry is None:
                continue
            if real not in owners[key]:
                owners[key].append(real)
            claims.setdefault(real, {}).setdefault(sha256, {})[account] = red
    renames, rename_owner = {}, {}
    for spec in names:
        real, entry = _gp_resolve(library, spec["manual_id"])
        rename_owner[(spec["manual_id"], spec["sha256"])] = real if entry is not None else None
        if entry is not None:
            claims.setdefault(real, {}).setdefault(spec["sha256"], {})
            renames[(real, spec["sha256"])] = spec["uploaded_name"]
    patches, changed, conflicts, matched, renamed = [], [], [], set(), set()
    objects = confirmed = 0
    for real in sorted(claims):
        wanted = claims[real]
        for obj in mvcommon.iter_entry_objects(real, library[real]):
            reds = wanted.get(obj["sha256"])
            if reds is None:
                continue
            name = renames.get((real, obj["sha256"]))
            objects += 1
            matched.update((account, obj["sha256"]) for account in reds)
            if name:
                renamed.add((real, obj["sha256"]))
            node = mvcommon.slot_owner(library, obj["slot"])
            raw = node.get("gphotos", _GP_ABSENT)
            block, action, problems = _gp_fold_object(obj, raw, reds, capture, prefix_map, default, name)
            where = {"manual_id": real, "label": obj["label"], "kind": obj["kind"], "sha256": obj["sha256"]}
            conflicts.extend(dict(where, account=account, code=code) for account, code in problems)
            if block is None:
                continue
            patches.append((node, block))
            confirmed += int(_gp_confirmed(block) and not (action == "update" and _gp_confirmed(raw)))
            changed.append(dict(where, action=action, fields=_gp_diff(raw, block)))
    orphans = [{"account": account, "manual_id": reduced[(account, sha256)]["manual_id"],
                "sha256": sha256, "label": reduced[(account, sha256)].get("label"),
                "reason": "no-object" if owners[(account, sha256)] else "no-entry"}
               for account, sha256 in reduced if (account, sha256) not in matched]
    orphans += [{"account": None, "manual_id": manual_id, "sha256": sha256, "label": None,  # a names fact, no account
                 "reason": "no-object" if real is not None else "no-entry"}
                for (manual_id, sha256), real in rename_owner.items() if (real, sha256) not in renamed]
    orphans.sort(key=lambda o: (o["manual_id"], o["account"] or "", o["sha256"]))
    return patches, {"objects": objects, "changed": changed, "unchanged": objects - len(changed),
                     "confirmed": confirmed, "conflicts": conflicts, "orphans": orphans}


def _gp_csv_edits(path):
    """How many rows of failed_objects.csv carry a user decision — valid or not: mvcommon.read_failed_csv's
    notion of an edit — or None when it cannot be read as a failed-objects CSV."""
    try:
        rows, errors = mvcommon.read_failed_csv(path)
    except (OSError, ValueError):
        return None
    return len(rows) + len(errors)


def _gp_refresh_failed_csv(library, report):
    """PLAN Step 6 "after every apply: export_failed_csv()": regenerate failed_objects.csv (P-18) from the
    applied library so objects confirmed now drop off — when a failed list (or its CSV) exists. A CSV that
    holds the user's edits (decision rows) — or that no longer reads as one — is left as is and reported as
    failed_csv {path, kept: True, edited_rows}: regenerating empties the editable columns, and the post-fetch
    merge must never wipe edits waiting to be imported (the importer regenerates it once it consumed them).
    A write failure (e.g. the CSV is open in Excel) prints one warning line; the apply's result stands."""
    csv_path = mvcommon.failed_csv_path()
    if not (os.path.exists(mvcommon.failed_path()) or os.path.exists(csv_path)):
        return
    if os.path.exists(csv_path):
        edits = _gp_csv_edits(csv_path)
        if edits != 0:
            report["failed_csv"] = {"path": csv_path, "kept": True, "edited_rows": edits}
            return
    try:
        report["failed_csv"] = mvcommon.export_failed_csv(library)
    except Exception as e:
        report["warning"] = (f"⚠️  failed_objects.csv not refreshed ({_gp_line(e)}) — close it if it is "
                             f"open (e.g. in Excel); the next apply refreshes it.")
        print(report["warning"])


def _gp_valid_names(names):
    """The valid `names` facts for gphotos_apply -> ([{manual_id, sha256, uploaded_name}], invalid count). A fact
    names an object (manual_id + its current 64-hex SHA-256) and a non-empty file name without a path separator."""
    valid, invalid = [], 0
    for spec in names or ():
        manual_id = spec.get("manual_id") if isinstance(spec, dict) else None
        sha256 = spec.get("sha256") if isinstance(spec, dict) else None
        name = spec.get("uploaded_name") if isinstance(spec, dict) else None
        if (isinstance(manual_id, str) and manual_id and mvcommon._is_sha256(sha256) and isinstance(name, str)
                and name.strip() and not any(c in name for c in "/\\\r\n\0")):
            valid.append({"manual_id": manual_id, "sha256": sha256, "uploaded_name": name})
        else:
            invalid += 1
    return valid, invalid


def gphotos_apply(records, capture=True, dry_run=True, names=None):
    """THE apply engine (PLAN Step 6): fold learned records (+ capture facts) into the library's `gphotos`
    blocks — the only code that writes them. See the section comment above for the per-object rules.

    records   iterable of learned records — complete P-10 lines (v, at, account, ...) exactly as
              mvcommon.read_learned() returns them or a run's results.jsonl holds them; invalid ones are
              counted and skipped. They are reduced TOGETHER (mvcommon.reduce_learned), so pass the whole
              sidecar plus any not-yet-appended run records for the complete picture (merge_learned_gphotos
              passes the sidecar). manual_id = the owning library id (the TITLE id for extras); an alias id
              resolves to its primary.
    capture   also fold each named object's capture facts (P-11).
    dry_run   True (the default): compute the report and write NOTHING — no library, no sidecar, no CSV.
              False: save the library ONCE when something changed (fingerprint-guarded, OD-2), then
              regenerate failed_objects.csv (see _gp_refresh_failed_csv).
    names     (PLAN Step 9, `map_photos --repair names`) [{manual_id, sha256, uploaded_name}]: the name an object
              was REALLY uploaded under (proven by the inventory) — stored as the block's `uploaded_name`, over the
              capture's; the object is named like a record names it (an alias resolves; the sha256 is the pin).
              Recomputed by the mapper from the inventory on every run, so not kept in a sidecar. Invalid facts
              count as `invalid`; facts whose object is gone are orphans with account None.

    With no valid record and no valid name the library is not even loaded. Never raises (a KeyboardInterrupt still
    interrupts): a failure prints ONE warning line and returns status "error". Prints nothing else —
    callers print their own summary from the report (and tell the user when failed_csv says `kept`).

    Returns the report:
      status      nothing-to-apply | dry-run | up-to-date | saved | aborted | error
      dry_run     as given
      records     valid records given · invalid: records skipped as not valid learned records (+ invalid names)
      objects     library objects the records name that exist (manual_id + current SHA-256)
      changed     [{manual_id, label, kind, sha256, action: create | replace | update,
                    fields: {dotted path: [old, new]}}] — the objects whose block changes (dry run: would
                    change; aborted / error: were NOT changed), in library order; None = absent; a
                    replaced block is diffed against the old one
      unchanged   named objects left as they are (up to date, nothing learned, or a block-version conflict)
      confirmed   changed objects that gain their first confirmed account record
      conflicts   [{manual_id, label, kind, sha256, account (None: block level), code}] — never applied;
                  codes: block-version, capture-invalid, capture-content-mismatch, and apply_learned's
                  (content-key-mismatch, dedup-key-mismatch, malformed-block, ...)
      orphans     [{account, manual_id, sha256, label, reason: no-entry | no-object}] — record groups whose
                  object no longer exists; never applied
      saved       True when the library was written
      attempts    load -> patch -> re-check attempts made (the OD-2 guard)
      failed_csv  export_failed_csv's result, or {path, kept: True, edited_rows} when an edited CSV was
                  left alone, or None
      warning     the warning line printed, or None
    """
    report = {"status": None, "dry_run": bool(dry_run), "records": 0, "invalid": 0, "objects": 0,
              "changed": [], "unchanged": 0, "confirmed": 0, "conflicts": [], "orphans": [],
              "saved": False, "attempts": 0, "failed_csv": None, "warning": None}
    saving = False
    try:
        valid = []
        for record in records or ():
            try:
                valid.append(mvcommon._normalize_learned(record, fill=False))  # reduce_learned's validator
            except ValueError:
                report["invalid"] += 1
        report["records"] = len(valid)
        renames, bad_names = _gp_valid_names(names)
        report["invalid"] += bad_names
        if not valid and not renames:
            report["status"] = "nothing-to-apply"  # nothing to fold: the library is not even read
            return report
        reduced = mvcommon.reduce_learned(valid)
        named = {}
        for record in valid:
            ids = named.setdefault((record["account"], record["sha256"]), [])
            if record["manual_id"] not in ids:
                ids.append(record["manual_id"])
        import mainfetch  # P-4's routing constants — their single source; importing it has no side effects
        prefix_map, default = mainfetch.ID_PREFIX_PROFILE, mainfetch.DEFAULT_PROFILE
        for attempt in range(1, GPHOTOS_APPLY_ATTEMPTS + 1):
            report["attempts"] = attempt
            before = None if dry_run else _library_fingerprint()
            library = load_library()  # fresh, immediately before patching
            patches, found = _gp_plan(library, reduced, named, capture, prefix_map, default, renames)
            report.update(found)
            if dry_run:
                report["status"] = "dry-run"
                return report
            if patches:
                if _library_fingerprint() != before:
                    continue  # another process wrote the library after our load: reload + re-patch
                for node, block in patches:
                    node["gphotos"] = block
                saving = True
                save_library(library)
                saving = False
                report["saved"] = True
            report["status"] = "saved" if patches else "up-to-date"
            _gp_refresh_failed_csv(library, report)
            return report
        report.update(status="aborted", warning=f"⚠️  Google Photos identity not applied: {_GP_BUSY}.")
        print(report["warning"])
        return report
    except (Exception, SystemExit) as e:  # SystemExit: load_library refuses a corrupt library file
        what = "the library could not be loaded" if isinstance(e, SystemExit) else _gp_line(e)
        tail = ("the library save was interrupted — re-run to finish it" if saving
                else "the library was not changed")
        report.update(status="error", warning=f"⚠️  Google Photos identity not applied ({what}) — {tail}.")
        try:
            print(report["warning"])
        except Exception:
            pass
        return report


def merge_learned_gphotos():
    """The post-fetch merge (PLAN Step 6): fold EVERY learned record — the sidecars the fetch appends to — into
    the library through the apply engine (capture facts included) and print one summary line when an object
    changed or a conflict stays unapplied (plus one note when the library changed but an edited
    failed_objects.csv was left alone). Idempotent: records already folded change nothing. With no learned
    record it reads and writes nothing and prints nothing. Never raises. Returns the engine's report, or
    None when nothing was applied."""
    try:
        records = mvcommon.read_learned()
    except Exception as e:  # an unreadable sidecar must never look like an empty one
        try:
            print(f"⚠️  Google Photos identity not merged (learned records unreadable: {_gp_line(e)}).")
        except Exception:
            pass
        return None
    if not records:
        return None
    report = gphotos_apply(records, capture=True, dry_run=False)
    try:
        parts = []
        if report["changed"]:
            parts.append(f"{len(report['changed'])} object(s) updated in the library")
        if report["confirmed"]:
            parts.append(f"{report['confirmed']} newly confirmed in Google Photos")
        if report["conflicts"]:
            parts.append(f"{len(report['conflicts'])} conflict(s) not applied")
        if parts and report["status"] in ("saved", "up-to-date"):
            print("   > Google Photos identity: " + ", ".join(parts) + ".")
        kept = report["failed_csv"]
        if report["saved"] and isinstance(kept, dict) and kept.get("kept"):
            if kept["edited_rows"] is None:
                print("   > failed_objects.csv no longer reads as the failed list — left as is.")
            else:
                print(f"   > failed_objects.csv holds {kept['edited_rows']} edited row(s) — left as is "
                      f"(it is regenerated once they are imported).")
    except Exception:
        pass
    return report


def cmd_dispatch_fetch(manual_id, episode_range=None, fetch_extras=False, temp_dir=None):
    # This keeps main.py clean but still lets you run "main.py fetch"
    cmd = ["python", MAINFETCH_SCRIPT, "fetch", manual_id]

    if episode_range:
        cmd.append("episodes")
        cmd.append(episode_range)
        print(f"   > 🚀 Dispatching Batch Fetch for episodes {episode_range}...")
    else:
        print(f"   > 🚀 Dispatching Fetch...")

    # IMP-D19 Step 5 (Card C — flag-only): forward --fetchExtras VERBATIM to the
    # mainfetch subprocess. There is NO prompt anywhere — the flag is the sole
    # gate. When it is absent the argv is byte-for-byte today's, so mainfetch
    # never queues extras (existing main-content fetch behavior is unchanged).
    if fetch_extras:
        cmd.append("--fetchExtras")
    # [tempdir] forward a temp volume to mainfetch so downloads land off the
    # media volume (mirrors push's tempdir redirect).
    if temp_dir:
        cmd += ["tempdir", temp_dir]

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

    # [IMP-C25] The child has exited: fold what it learned — the learned sidecar mainfetch appends to
    # (P-10) — into the library through the apply engine (PLAN Step 6). The argv and the streaming above
    # are unchanged; with nothing learned this reads and writes nothing and prints nothing; it never
    # raises. A Ctrl+C during the fetch skips it (the records wait in the sidecar for the next merge).
    merge_learned_gphotos()


def cmd_fetch_restore(manual_id, episode_range=None, fetch_extras=False, temp_dir=None):
    # [NEW] Automated Fetch -> Restore Pipeline
    print(f"=== 🔄 AUTO-PILOT: FETCH -> RESTORE for {manual_id} ===")

    # 1. FETCH
    cmd_dispatch_fetch(manual_id, episode_range, fetch_extras=fetch_extras, temp_dir=temp_dir)
    # IMP-D19 Step 5: fetch extras when fetch_extras (forwarded above to mainfetch)

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
        restored_count = cmd_restore_group(manual_id, episode_range, temp_dir=temp_dir)
    else:
        print(f"   > Single Item detected. Running Restore...")
        cmd_restore(manual_id, temp_dir=temp_dir)

    # IMP-D19 Step 6: place any fetched extras back into their subfolders —
    # only when --fetchExtras staged them (flag-only, Card C). Reload first: the
    # restore phase above saved its own library updates. This explicit wire
    # covers the extras-only run (nothing staged for main content, so the
    # per-episode success wires never fired); when the per-episode/group wires
    # already restored the extras it is an idempotent silent no-op.
    if fetch_extras:
        library = load_library()
        restore_title_extras(library, _extras_title_id(library, manual_id))

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


def cmd_add_extras(title_id, folders, extras_size=None, device_id=None, no_replace=False):
    """Attach extras to an existing / archived / local-only title entry.

    Syntax (from the CLI):
      add_extras <title_id> "<folders>" [--extras-size <v|none>]
                 [device <id_or_name>] [no-replace]

    Full one-shot lifecycle (Card D1) WITHOUT touching main content (so it is the
    safe path for an already-archived title whose main is a dummy):
      1. scan+merge the extras into the title entry and save;
      2. push the just-merged extras to the device (independent --extras-size);
      3. dummy them to reclaim space — UNLESS `no-replace` keeps them upload-only.
    """
    print(f"=== ADD EXTRAS: {title_id} ===")
    library = load_library()

    # Resolve the title id and validate it exists.
    real_title_id = _extras_title_id(library, title_id)
    if not real_title_id:
        print(f"❌ '{title_id}' not found in library or cannot be resolved to a title entry.")
        return

    # Scan+merge (prep-phase only — no upload in this step).
    _extras_scan_merge_and_save(library, real_title_id, folders)

    # Surface the stored intent for future steps.
    if extras_size:
        print(f"   > extras-size: {extras_size}")
    if device_id:
        print(f"   > device: {device_id}")
    if no_replace:
        print("   > no-replace: extras will be uploaded only (not dummied).")

    # IMP-D19 Step 3: push the just-merged extras (no main split to inherit, so
    # extras_size=None => whole-file unless --extras-size was given).
    push_title_extras(library, real_title_id, extras_size, device_id)
    # IMP-D19 Step 4 (Card D1): dummy them too to reclaim space — UNLESS the user
    # passed `no-replace` to keep the extras local (upload-only). push_title_extras
    # mutated `library` in place, so replace_title_extras sees the uploaded items.
    if not no_replace:
        replace_title_extras(library, real_title_id)


# ==========================================
#   GOOGLE PHOTOS IDENTITY — READ-ONLY DEVICE CROSS-CHECK (IMP-C25)
# ==========================================
# docs/feature-fetch-datetime/PLAN.md Step 8: no "missing from the cloud" verdict (Step 9's mapper, Step 13's
# check) before looking at the phones themselves. An object Google Photos does not hold may still sit on a
# Pixel waiting for its backup (on_device); may have been backed up and then removed from the phone — Google
# Photos' "Free up space" deletes the media but not the `.mvmeta.json` sidecar write_remote_mvmeta pushed next
# to it (JSON is not media: never backed up, never freed), so a lone sidecar marks an object that reached that
# phone and is gone from it (freed — possibly LOST when the cloud lacks it too); or may be on no phone at all
# (absent). Two functions; their docstrings define the return shapes Steps 9, 13 and 13c consume:
#   * device_inventory(aliases): every file under the Media root of each configured phone (default:
#     device_check_aliases() — DEVICE_ALIASES plus the check-only DEVICE_CHECK_EXTRA_SERIALS) — ONE `adb devices`,
#     then per attached phone ONE `find '/sdcard/Media/' -type f` (the trailing slash makes find follow the
#     /sdcard symlink; `/storage/emulated/0/Media/` is the fallback root) and `stat -c '%s %Y %n'` in batches
#     whose device command stays within DEVICE_STAT_BATCH_BYTES;
#   * classify_device_objects(library, inventory): each uploaded object (mvcommon.iter_uploaded_objects —
#     whole files, chunks, FLAC holders, extras items and chunks; all four categories) by its exact uploaded
#     name, with size agreement where a size is known.
# READ-ONLY by construction, pinned by tests: every adb argv must pass _dev_argv_readonly — an allowlist of
# exact shapes (`devices`; `shell find` / `shell stat` over the Media roots, every path ONE single-quoted
# word) — before it runs. Nothing else is ever issued: no push, pull, rm, mv, touch, mkdir, am or pm, and no
# shell operator outside quotes. Field lessons from the real Pixels: device output ends its lines with CRLF;
# `adb shell` reads the caller's stdin (in a loop it swallows it), so every call gets stdin=DEVNULL; names
# carry spaces, apostrophes and unicode, so paths reach the device shell single-quoted ('\'' for a quote —
# cmd_push's escaping) and output is decoded as UTF-8. An unplugged, offline or unauthorized phone, a missing
# adb binary, a timeout: reported per device, never raised. Nothing is printed and the library is only read.

DEVICE_LIST_ROOTS = (REMOTE_ROOT + "/", "/storage/emulated/0/Media/")  # find roots, in order (field lesson)
DEVICE_STAT_FORMAT = "%s %Y %n"   # size in bytes, mtime in epoch seconds, the path exactly as given
DEVICE_STAT_BATCH_BYTES = 3500    # UTF-8 bytes of ONE stat command: under legacy adb's 4 KiB packet limit
DEVICE_ADB_TIMEOUT_S = 30         # `adb devices` (it may have to start the adb server first)
DEVICE_FIND_TIMEOUT_S = 120
DEVICE_STAT_TIMEOUT_S = 60
_DEV_PLACEHOLDER_RE = re.compile(r"<[^<>]*>")      # a DEVICE_ALIASES '<NEW_PIXEL_SERIAL>'-style placeholder
_DEV_SERIAL_RE = re.compile(r"[A-Za-z0-9._:-]+")   # a USB, tcp (host:port) or mDNS adb serial
_DEV_QUOTED_RE = re.compile(r"'(?:[^']|'\\'')*'")  # ONE single-quoted device-shell word ('\'' = a quote)
_DEV_STAT_RE = re.compile(r"(\d+) (-?\d+) (/.*)")  # one `stat -c '%s %Y %n'` output line


class _DeviceCommandRefused(RuntimeError):
    """An adb argv outside the device cross-check's read-only allowlist — a bug; it is never run."""


def _dev_quote(path):
    """`path` as ONE single-quoted word for the device shell ('\\'' stands for a quote — cmd_push's escaping)."""
    return "'" + path.replace("'", "'\\''") + "'"


def _dev_unquote(word):
    """The literal text of a _DEV_QUOTED_RE word."""
    return word[1:-1].replace("'\\''", "'")


def _dev_statable(path):
    """True for a path a stat batch may carry: one line of text, strictly under a listing root."""
    return (isinstance(path, str) and not any(c in path for c in "\r\n\0")
            and any(path.startswith(root) and len(path) > len(root) for root in DEVICE_LIST_ROOTS))


def _dev_argv_readonly(argv):
    """THE read-only allowlist of the device cross-check: True only for these exact adb argv shapes —
         adb [-s <serial>] devices
         adb -s <serial> shell find '<root>' -type f             <root> in DEVICE_LIST_ROOTS
         adb -s <serial> shell stat -c '%s %Y %n' '<path>' ...   every <path> strictly under a root
    A shell command always names its phone (never "whichever one is attached"). adb joins the words after
    `shell` with spaces and hands them to the device shell, so each path must be ONE single-quoted word (a
    quote only as '\\'') — then the command holds no operator (; | & > < ` $( ...) outside quotes. Everything
    else is refused: push, pull, install, rm, mv, cp, touch, mkdir, am, pm, `find -delete` / `-exec`, an
    unquoted or out-of-root path, a malformed serial."""
    if not (isinstance(argv, (list, tuple)) and argv and all(isinstance(a, str) for a in argv)):
        return False
    if argv[0] != "adb":
        return False
    rest = list(argv[1:])
    pinned = rest[:1] == ["-s"]
    if pinned:
        if len(rest) < 2 or not _DEV_SERIAL_RE.fullmatch(rest[1]):
            return False
        rest = rest[2:]
    if rest == ["devices"]:
        return True
    if not pinned or rest[:1] != ["shell"]:
        return False
    cmd = rest[1:]
    if len(cmd) == 4 and cmd[0] == "find" and cmd[2:] == ["-type", "f"]:
        return cmd[1] in [_dev_quote(root) for root in DEVICE_LIST_ROOTS]
    if len(cmd) > 3 and cmd[:3] == ["stat", "-c", _dev_quote(DEVICE_STAT_FORMAT)]:
        return all(_DEV_QUOTED_RE.fullmatch(word) and _dev_statable(_dev_unquote(word)) for word in cmd[3:])
    return False


def _dev_text(data):
    """Captured adb output as text: bytes decoded as UTF-8 (an undecodable byte becomes U+FFFD); '' for none."""
    if isinstance(data, bytes):
        return data.decode("utf-8", errors="replace")
    return data if isinstance(data, str) else ""


def _dev_lines(text):
    """The non-empty lines of device output without their line-ending CRs (real Pixels end lines with CRLF).
    Split on '\\n' only: str.splitlines() would also split on unicode separators a file name may hold."""
    return [line.rstrip("\r") for line in text.split("\n") if line.rstrip("\r")]


def _dev_first_line(text):
    """The first non-blank line of `text`, stripped and cut to 200 characters (an error message), or None."""
    for line in _dev_lines(text):
        if line.strip():
            return line.strip()[:200]
    return None


def _dev_run(argv, timeout):
    """Run ONE allowlisted adb command -> (returncode, stdout text, stderr text). The allowlist check comes
    first: an argv off it raises _DeviceCommandRefused and never runs. stdin is DEVNULL (adb shell reads the
    caller's stdin — inside a loop it would swallow it); output is captured as bytes and decoded by _dev_text.
    OSError (no adb binary) and subprocess.TimeoutExpired propagate."""
    if not _dev_argv_readonly(argv):
        raise _DeviceCommandRefused(repr(argv)[:200])
    result = subprocess.run(list(argv), stdin=subprocess.DEVNULL, capture_output=True, timeout=timeout)
    return (getattr(result, "returncode", None), _dev_text(getattr(result, "stdout", None)),
            _dev_text(getattr(result, "stderr", None)))


def _dev_attached(text):
    """`adb devices` output -> {serial: state} in listing order (device, offline, unauthorized, authorizing,
    no permissions ...). The header line and the daemon's '* ...' lines carry no tab and are skipped."""
    attached = {}
    for line in _dev_lines(text):
        serial, tab, state = line.partition("\t")
        if tab and serial.strip():
            attached[serial.strip()] = state.strip()
    return attached


def _dev_batches(paths):
    """`paths` in order, packed into stat batches whose device command — `stat -c '%s %Y %n' '<p1>' '<p2>' ...`
    exactly as adb joins it — stays within DEVICE_STAT_BATCH_BYTES of UTF-8. A single path longer than that
    gets a batch of its own."""
    head = len(f"stat -c {_dev_quote(DEVICE_STAT_FORMAT)}".encode("utf-8"))
    batch, size = [], head
    for path in paths:
        cost = 1 + len(_dev_quote(path).encode("utf-8"))
        if batch and size + cost > DEVICE_STAT_BATCH_BYTES:
            yield batch
            batch, size = [], head
        batch.append(path)
        size += cost
    if batch:
        yield batch


def _dev_list(record):
    """List ONE attached phone into its device_inventory record: `find '<root>' -type f` over the first
    DEVICE_LIST_ROOTS root that lists any file, then size + mtime by batched stat (a failed batch only leaves
    its files' size/mtime unknown). When no root lists a file, the phone counts as empty only if EVERY root
    listed cleanly (exit 0): an error on any root makes it `error` — a locked or failing phone must never
    read as an empty one. Never raises."""
    base = ["adb", "-s", record["serial"], "shell"]
    try:
        problems, paths = [], []
        for root in DEVICE_LIST_ROOTS:
            code, out, err = _dev_run(base + ["find", _dev_quote(root), "-type", "f"], DEVICE_FIND_TIMEOUT_S)
            listed = {re.sub(r"/{2,}", "/", line) for line in _dev_lines(out)}
            paths = sorted(p for p in listed if p.startswith(root) and len(p) > len(root))
            problem = None if code == 0 else (_dev_first_line(err) or f"find exited with status {code}")
            if paths:
                record.update(status="ok" if problem is None else "partial", detail=problem, root=root)
                break
            if problem:
                problems.append(f"{root}: {problem}")
        else:
            if problems:
                record.update(status="error", detail="; ".join(problems))
            else:
                record.update(status="ok", root=DEVICE_LIST_ROOTS[0])  # every root listed, cleanly and empty
            return
        stats = {}
        for batch in _dev_batches([p for p in paths if _dev_statable(p)]):
            try:
                code, out, err = _dev_run(base + ["stat", "-c", _dev_quote(DEVICE_STAT_FORMAT)]
                                          + [_dev_quote(p) for p in batch], DEVICE_STAT_TIMEOUT_S)
            except (OSError, subprocess.SubprocessError):
                continue  # this batch keeps unknown sizes; the listing itself stands
            wanted = set(batch)
            for line in _dev_lines(out):
                m = _DEV_STAT_RE.fullmatch(line)
                if m and m.group(3) in wanted:
                    stats[m.group(3)] = (int(m.group(1)), int(m.group(2)))
        record["files"] = [{"path": p, "name": p.rsplit("/", 1)[1], "size": stats.get(p, (None, None))[0],
                            "mtime": stats.get(p, (None, None))[1]} for p in paths]
        record["stat_errors"] = sum(1 for p in paths if p not in stats)
    except _DeviceCommandRefused as e:
        record.update(status="error", detail=f"refused a non-read-only adb command (a bug): {e}", files=[])
    except Exception as e:
        record.update(status="error", detail=_gp_line(e), files=[])


def device_check_aliases():
    """{alias: serial} of every phone the device cross-check lists, both tables read at call time:
    DEVICE_ALIASES in order, then each DEVICE_CHECK_EXTRA_SERIALS serial as '<alias>#2', '<alias>#3', ... (the
    alias it serves; the next free number when a key is taken). A serial that is already named — by
    DEVICE_ALIASES or an earlier extra — is listed once; a bare string is taken as a one-serial list and a
    non-string serial is ignored. The check-only extras never reach resolve_device (DECISIONS.md §1d)."""
    merged = dict(DEVICE_ALIASES)
    named = {serial.strip() for serial in merged.values() if isinstance(serial, str)}
    for alias, serials in DEVICE_CHECK_EXTRA_SERIALS.items():
        number = 1
        for serial in [serials] if isinstance(serials, str) else serials:
            if not isinstance(serial, str) or serial.strip() in named:
                continue
            number += 1
            while f"{alias}#{number}" in merged:
                number += 1
            merged[f"{alias}#{number}"] = serial
            named.add(serial.strip())
    return merged


def device_inventory(aliases=None):
    """The read-only file inventory of the configured phones (PLAN Step 8 — see the section comment above).

    aliases   {alias: serial}; default device_check_aliases() — DEVICE_ALIASES plus the check-only
              DEVICE_CHECK_EXTRA_SERIALS (both read at call time). An alias may name a raw serial. A
              serial that is empty or a '<...>' placeholder (DEVICE_ALIASES' '<NEW_PIXEL_SERIAL>') is skipped:
              no adb command ever names it.

    Issues ONE `adb devices` (none when every alias is skipped), then, per attached phone, the find + stat
    commands of the section comment — only argvs _dev_argv_readonly allows. Never raises for anything a phone
    or adb does (a KeyboardInterrupt still interrupts), prints nothing and writes nothing, anywhere.

    Returns:
      complete      True only when at least one alias names a real serial and EVERY alias that is not a
                    placeholder was listed in full (status ok — an invalid serial counts as not listed). Only
                    then may "on no phone" be read as absent: a phone that was not listed could hold the object
                    (classify_device_objects answers `unchecked` instead).
      devices       {alias: record}, in `aliases` order; a record:
        alias, serial   as configured
        status          ok             listed in full
                        partial        listed, but find reported an error (a folder it could not read): its
                                       files are real evidence, its silence is not
                        skipped        a placeholder serial — reported as "skipped (placeholder)"
                        not-connected  `adb devices` does not list it (unplugged, attached to another PC ...)
                        offline        adb lists it offline (replug the cable / restart adb)
                        unauthorized   adb lists it unauthorized or authorizing (allow USB debugging on it)
                        unavailable    any other adb state (recovery, bootloader, sideload, no permissions ...)
                        adb-missing    the adb binary could not be started
                        error          anything else — a timeout, `adb devices` failing, find failing on every
                                       root, an invalid serial; `detail` says what
        detail          None when ok; otherwise the reason (adb's own first error line when there is one)
        root            the DEVICE_LIST_ROOTS root the files were listed from (normally '/sdcard/Media/'), else
                        None
        files           [{path, name, size, mtime}] sorted by path — path exactly as the phone listed it
                        (under `root`, so it works in any later read-only adb command), name its last part,
                        size in bytes and mtime in epoch seconds from stat (None when stat gave nothing). adb
                        push keeps the PC file's mtime (RESEARCH F3), so a `.mvmeta.json` sidecar's mtime is
                        its push time
        stat_errors     how many of `files` got no size / mtime
      unconfigured  [{serial, state}] — phones `adb devices` lists that `aliases` does not name (e.g. a Pixel
                    missing from DEVICE_ALIASES and DEVICE_CHECK_EXTRA_SERIALS): reported, never inventoried
    """
    aliases = device_check_aliases() if aliases is None else aliases
    devices, real = {}, []
    for alias, serial in aliases.items():
        record = {"alias": alias, "serial": serial, "status": None, "detail": None, "root": None, "files": [],
                  "stat_errors": 0}
        devices[alias] = record
        text = serial.strip() if isinstance(serial, str) else ""
        if not text or _DEV_PLACEHOLDER_RE.fullmatch(text):
            record.update(status="skipped", detail="placeholder")
        elif not _DEV_SERIAL_RE.fullmatch(serial):
            record.update(status="error", detail=f"not a valid adb serial: {serial!r}")
        else:
            real.append(record)
    result = {"complete": False, "devices": devices, "unconfigured": []}
    if not real:
        return result
    try:
        code, out, err = _dev_run(["adb", "devices"], DEVICE_ADB_TIMEOUT_S)
    except OSError as e:  # FileNotFoundError: no adb binary on PATH
        for record in real:
            record.update(status="adb-missing", detail=f"adb could not be started ({_gp_line(e)})")
        return result
    except Exception as e:  # a timeout, or anything else adb did
        for record in real:
            record.update(status="error", detail=f"adb devices failed ({_gp_line(e)})")
        return result
    if code != 0:
        why = _dev_first_line(err) or _dev_first_line(out) or f"exit status {code}"
        for record in real:
            record.update(status="error", detail=f"adb devices failed ({why})")
        return result
    attached = _dev_attached(out)
    named = {record["serial"] for record in devices.values()}
    result["unconfigured"] = [{"serial": serial, "state": state} for serial, state in attached.items()
                              if serial not in named]
    for record in real:
        state = attached.get(record["serial"])
        if state == "device":
            _dev_list(record)
        elif state is None:
            record.update(status="not-connected", detail="adb devices does not list it — connect the phone")
        elif state == "offline":
            record.update(status="offline", detail="adb lists it offline — replug the cable or restart adb")
        elif state in ("unauthorized", "authorizing"):
            record.update(status="unauthorized", detail=f"adb lists it {state} — allow USB debugging on the phone")
        else:
            record.update(status="unavailable", detail=f"adb state: {state}")
    # every configured phone that is not a placeholder — an invalid serial too — must have been listed in full
    result["complete"] = all(record["status"] in ("ok", "skipped") for record in devices.values())
    return result


DEVICE_STATES = ("on_device", "freed", "absent", "unchecked")  # classify_device_objects' verdicts, in order


def _dev_index(inventory):
    """Every listed file of a device_inventory result as a location {alias, serial, path, size, mtime}, indexed
    three ways: (by exact name, by PLAN P-6 normalized name, by the name a `.partial` upload leftover of an
    interrupted push would have become)."""
    exact, normalized, partial = {}, {}, {}
    devices = inventory.get("devices") if isinstance(inventory, dict) else None
    for alias, device in (devices or {}).items():
        if not isinstance(device, dict):
            continue
        for f in device.get("files") or ():
            name = f.get("name") if isinstance(f, dict) else None
            if not (isinstance(name, str) and name):
                continue
            where = {"alias": alias, "serial": device.get("serial"), "path": f.get("path"), "size": f.get("size"),
                     "mtime": f.get("mtime")}
            exact.setdefault(name, []).append(where)
            normalized.setdefault(gpweb.normalize_name(name), []).append(where)
            if name.endswith(PARTIAL_SUFFIX) and len(name) > len(PARTIAL_SUFFIX):
                partial.setdefault(name[:-len(PARTIAL_SUFFIX)], []).append(where)
    return exact, normalized, partial


def _dev_matches(index, name):
    """The locations of the file `name`: its exact matches, else (PLAN P-6) its normalized ones — each copied
    with name_match exact | normalized. [] for no name."""
    exact, normalized, _partial = index
    if not (isinstance(name, str) and name):
        return []
    if name in exact:
        return [dict(where, name_match="exact") for where in exact[name]]
    return [dict(where, name_match="normalized") for where in normalized.get(gpweb.normalize_name(name), ())]


def _dev_sidecar_name(uploaded_name, short_id):
    """The `.mvmeta.json` write_remote_mvmeta pushed next to an object: '<base> [<short_id>].mvmeta.json', where
    '<base> [<short_id>]' is the uploaded name up to its LAST ' [<short_id>]' tag — the tag cmd_push /
    push_one_extra add to a whole file and split_video_file / _carry_out_flac_track put before '.chunk.NNN.mkv'
    / '.holder.mkv' (so a split entry's chunks and holder share one sidecar). None when the name carries no tag
    of that short id (e.g. an old upload under an untagged name)."""
    if not (isinstance(uploaded_name, str) and isinstance(short_id, str) and short_id):
        return None
    tag = f" [{short_id}]"
    at = uploaded_name.rfind(tag)
    return None if at < 0 else uploaded_name[:at + len(tag)] + MVMETA_SUFFIX


def _dev_expected_size(library, obj):
    """(bytes, source) the object's upload must have: the trusted block's size_bytes, else the capture's (both
    describe exactly these bytes), else the tech_spec.size_bytes of the object's OWN node — its slot owner (prep
    and the extras scan stat the very file they hash). Only a whole file (the leaf) and a whole extras item (the
    item) have one: a chunk's or holder's own record carries none, and its entry's tech_spec sizes the whole
    file, never a chunk. (None, None) when unknown."""
    for source, facts in (("block", obj.get("block")), ("capture", obj.get("capture"))):
        size = facts.get("size_bytes") if isinstance(facts, dict) else None
        if _gp_is_int(size) and size > 0:
            return size, source
    owner = mvcommon.slot_owner(library, obj.get("slot"))
    tech = owner.get("tech_spec") if isinstance(owner, dict) else None
    size = tech.get("size_bytes") if isinstance(tech, dict) else None
    return (size, "tech_spec") if _gp_is_int(size) and size > 0 else (None, None)


def classify_device_objects(library, inventory, objects=None):
    """Classify each uploaded object by what the phones hold (PLAN Step 8 — see the section comment above).

    library    the library dict (load_library()); only read
    inventory  a device_inventory() result
    objects    the objects to classify — mvcommon.iter_uploaded_objects / iter_entry_objects dicts of THIS
               library (their slot resolves the owner) — default: every uploaded object of the library

    An object is looked up by its exact uploaded name (PLAN P-6: else its normalized name, flagged) anywhere
    under the Media root of every listed phone. Verdict, the first that applies:
      on_device  a file with its uploaded name whose size agrees — or cannot be compared (unknown on either
                 side): the object is still on a phone, awaiting (or already past) its backup
      freed      no such file, but the `.mvmeta.json` sidecar pushed with it is there: it reached that phone and
                 its media is gone from it ("Free up space" after a backup, or a manual delete). If the cloud
                 lacks it too it is possibly LOST (Step 9: missing-after-backup). Positive evidence, so it stands
                 even when another phone was not listed (only a second push to that phone could change it)
      absent     nothing on any phone — and every configured phone was listed in full (inventory complete)
      unchecked  nothing found, but "absent" cannot be claimed: a configured phone was not listed in full
                 (unplugged, offline, unauthorized, partial ...) or none is configured, or the object has no
                 uploaded name. A consumer must not call it missing (Step 9, Step 13)
    A same-named file whose size DISAGREES holds other bytes (e.g. a superseded re-split chunk): it never makes
    the object on_device and stays listed in `copies` with size_match False. Limits: a split set pushed only by
    chunk ranges has no sidecar (write_remote_mvmeta runs after a full push), so its freed chunks read absent /
    unchecked; an object uploaded under a name without its ' [<short_id>]' tag has no sidecar to find.

    Returns:
      complete            the inventory's `complete`
      incomplete_devices  {alias: status} of the configured phones not listed in full (placeholders excluded)
      counts              {state: n} over DEVICE_STATES
      objects             one record per object, in `objects` order:
        manual_id, category, kind, index, label, sha256, uploaded_name, slot   the object's identity
                          (iter_uploaded_objects' fields; (manual_id, label) or slot names it uniquely)
        state             on_device | freed | absent | unchecked
        expected_size     bytes its upload must have, or None; size_source: block | capture | tech_spec | None
        copies            [{alias, serial, path, size, mtime, name_match: exact | normalized,
                            size_match: True | False | None}] — every file carrying its uploaded name
        sidecars          [{alias, serial, path, size, mtime, name_match}] — its `.mvmeta.json` wherever found
                          (its mtime is the push time)
        partials          [{alias, serial, path, size, mtime}] — '<uploaded name>.partial' leftovers of an
                          interrupted push (never a copy)
    No adb, no output, no library write (building the default object list reads the capture files)."""
    index = _dev_index(inventory)
    complete = bool(isinstance(inventory, dict) and inventory.get("complete") is True)
    devices = inventory.get("devices") if isinstance(inventory, dict) else None
    incomplete = {alias: device.get("status") for alias, device in (devices or {}).items()
                  if isinstance(device, dict) and device.get("status") not in ("ok", "skipped")}
    counts = dict.fromkeys(DEVICE_STATES, 0)
    rows = []
    for obj in (mvcommon.iter_uploaded_objects(library) if objects is None else objects):
        name = obj.get("uploaded_name")
        expected, source = _dev_expected_size(library, obj)
        copies = [dict(where, size_match=(None if where["size"] is None or expected is None
                                          else where["size"] == expected))
                  for where in _dev_matches(index, name)]
        sidecars = _dev_matches(index, _dev_sidecar_name(name, obj.get("short_id")))
        partials = [dict(where) for where in index[2].get(name, ())] if isinstance(name, str) else []
        if any(copy_["size_match"] is not False for copy_ in copies):
            state = "on_device"
        elif sidecars:
            state = "freed"
        elif complete and isinstance(name, str) and name:
            state = "absent"
        else:
            state = "unchecked"
        counts[state] += 1
        rows.append({"manual_id": obj.get("manual_id"), "category": obj.get("category"), "kind": obj.get("kind"),
                     "index": obj.get("index"), "label": obj.get("label"), "sha256": obj.get("sha256"),
                     "uploaded_name": name, "slot": obj.get("slot"), "state": state, "expected_size": expected,
                     "size_source": source, "copies": copies, "sidecars": sidecars, "partials": partials})
    return {"complete": complete, "incomplete_devices": incomplete, "counts": counts, "objects": rows}


# ==========================================
#   GOOGLE PHOTOS IDENTITY — THE MAPPER: map_photos (IMP-C25)
# ==========================================
# docs/feature-fetch-datetime/PLAN.md Step 9: backfill the ARCHIVED library's Google Photos identity by mapping every
# uploaded object (mvcommon.iter_uploaded_objects: whole files, chunks, FLAC holders, extras; all four categories)
# to the crawled inventory — read through the crawler's own offline reader, tools/gp_inventory.inventory_state —
# then report, let the user resolve what cannot be proven, and apply. READ-ONLY until --apply: a dry run (the
# default) writes only its own run directory (<GPHOTOS_STATE_DIR>/map/<ts>, or --out) — never the library, the
# learned sidecars, the hints or the failed list.
#
# Per object:
#   1. freshness gate — a verdict needs every inventory it rests on to be FRESH: crawled, with a complete
#      enumeration at most MAP_INVENTORY_MAX_AGE_H old that is not older than the newest capture push of the
#      in-scope objects routed to that account; an "in no account" verdict also needs every crawled account fresh
#      and fully detailed. Otherwise the row is `stale-inventory` (no verdict) and the re-crawl command is printed.
#   2. match (PLAN P-6) — the exact uploaded name in the object's DEFAULT account (its id prefix), then the
#      normalized name; else the name in ANOTHER account (wrong-account); an object whose content key (SHA-1 =
#      dedupKey: trusted block or capture) is known is found by that key; then the `[short_id]`-tag `probable`.
#   3. cross-check — a known content key must equal the item's dedupKey, else `conflict` (never applied).
#   4. duplicates — same bytes: one item, the rest alternates; a re-split set: the chunks' items grouped by taken
#      second, the newest COMPLETE set chosen when no newer set exists; anything else `ambiguous`.
#   5. IMP-C27 detectors — split_info lost (--repair split-info), uploaded under the old untagged name (--repair
#      names), a partial / non-original upload, and for an object in NO account the read-only device check
#      (Step 8): awaiting-backup / missing-after-backup / missing-everywhere / device-check-incomplete.
# Outputs, --resolutions, --apply and --devices-only: see cmd_map_photos. Nothing here prints a P-13 web-progress
# marker; no photo id, dedupKey or URL is printed (they live in the run directory's files only — P-15).

MAP_INVENTORY_MAX_AGE_H = 24  # PLAN Step 9 (1): an enumeration older than this cannot back a verdict
MAP_REPAIRS = ("split-info", "names")
MAP_USAGE = ("map_photos [all|<id>|<prefix>] [--account movies|tv|anime|others] [--devices-only] "
             "[--resolutions <csv>] [--repair split-info|names] [--accept-probable] [--apply] [--out <dir>]")
_MP_FLAGS = {"--devices-only": "devices_only", "--accept-probable": "accept_probable", "--apply": "apply"}
_MP_VALUED = ("--account", "--resolutions", "--repair", "--out")
_MP_RECRAWL = "python tools/gp_inventory.py {account} --port <port>"
# The P-1 item facts an inventory record carries (the crawler's record format, dom and rpc).
_MP_ITEM_KEYS = ("photo_id", "filename", "taken_ms", "tz_offset_ms", "upload_ms", "dedup_key", "width", "height",
                 "size_bytes", "original_quality", "partial_upload", "archived")
_MP_TAG_RE = re.compile(r"\[([0-9a-f]{6})\]")                # a MediaVault short_id tag (generate_short_id: 6 hex)
_MP_HOLDER_RE = re.compile(r"\.holder\.mkv$", re.IGNORECASE)
_MP_CHUNK_KINDS = ("chunk", "extra_chunk")


def parse_map_photos_args(tokens):
    """The `map_photos` CLI tokens (after the verb) -> cmd_map_photos keyword arguments (MAP_USAGE). Raises
    ValueError naming the problem: an unknown option, a missing or invalid value, a second scope, or
    --devices-only combined with an option it does not take."""
    opts = {"scope": "all", "account": None, "devices_only": False, "resolutions": None, "repairs": (),
            "accept_probable": False, "apply": False, "out": None}
    tokens, scope, repairs, i = list(tokens), None, [], 0
    while i < len(tokens):
        token = tokens[i]
        if token in _MP_VALUED:
            if i + 1 >= len(tokens) or not tokens[i + 1].strip() or tokens[i + 1].startswith("--"):
                raise ValueError(f"{token} needs a value")
            value = tokens[i + 1].strip()
            i += 2
            if token == "--account":
                if value not in mvcommon.GP_ACCOUNTS:
                    raise ValueError(f"--account must be one of {', '.join(mvcommon.GP_ACCOUNTS)} (got {value!r})")
                opts["account"] = value
            elif token == "--repair":
                for part in (p.strip() for p in value.split(",")):
                    if part not in MAP_REPAIRS:
                        raise ValueError(f"--repair takes {' / '.join(MAP_REPAIRS)} (got {part!r})")
                    if part not in repairs:
                        repairs.append(part)
            else:
                opts["resolutions" if token == "--resolutions" else "out"] = value
        elif token in _MP_FLAGS:
            opts[_MP_FLAGS[token]] = True
            i += 1
        elif token.startswith("--"):
            raise ValueError(f"unknown option {token}")
        else:
            if scope is not None:
                raise ValueError(f"one scope only (got {scope!r} and {token!r})")
            scope = token.strip()
            i += 1
    opts["scope"] = scope or "all"
    opts["repairs"] = tuple(repairs)
    if opts["devices_only"] and (opts["apply"] or opts["resolutions"] or repairs or opts["accept_probable"]):
        raise ValueError("--devices-only takes only a scope, --account and --out")
    return opts


def _mp_when(value):
    """An ISO-8601 time — the crawler's '+00:00' form or a capture's 'Z' form — as an aware UTC datetime, or None."""
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        when = datetime.fromisoformat(value.strip())
    except ValueError:
        return None
    return (when if when.tzinfo else when.replace(tzinfo=timezone.utc)).astimezone(timezone.utc)


def _mp_iso(when):
    return when.strftime("%Y-%m-%dT%H:%M:%SZ") if when is not None else None


def _mp_inventory_states():
    """{account: tools/gp_inventory.inventory_state(account)} for every Google account, read from
    mvcommon.GP_INVENTORY_DIR at call time — the crawler's own offline reader (no Chrome, no lock, nothing
    written), never a private copy of its format."""
    from tools import gp_inventory  # lazy: the crawler module imports Selenium
    return {account: gp_inventory.inventory_state(account, mvcommon.GP_INVENTORY_DIR)
            for account in mvcommon.GP_ACCOUNTS}


def _mp_override(manual_id, overrides):
    """The mvconfig fetch_account_overrides account for an id — mainfetch.profile_for_id's rule: the longest
    matching key (an exact id beats any prefix of it) — or None."""
    best = max((key for key in overrides if manual_id.startswith(key)), key=len, default=None)
    return overrides[best] if best is not None else None


def _mp_route(obj, overrides, prefix_map, default):
    """An object's account layers (PLAN P-4): its default (id prefix), the mvconfig override, the learned home
    (the trusted block's home_account) and the routing account they give (override > home > default)."""
    base = mvcommon.home_account(None, obj["manual_id"], prefix_map, default)
    home = (obj.get("block") or {}).get("home_account")
    home = home if home in mvcommon.GP_ACCOUNTS else None
    override = _mp_override(obj["manual_id"], overrides)
    return {"default": base, "override": override, "home": home, "routing": override or home or base}


def _mp_freshness(states, objects, routes, now):
    """PLAN Step 9 (1): {account: freshness} — fresh when the account was crawled, has a complete enumeration
    (inventory_state's latest_complete: its start is the as-of time) at most MAP_INVENTORY_MAX_AGE_H old, and that
    as-of time is not older than the newest capture push of the in-scope objects ROUTED to the account (such a push
    may not be backed up yet). Also reported: `gaps` — known ids without a good record (errored, or listed and never
    detailed) — which blocks every "in no account" verdict, and the re-crawl command."""
    newest = {}
    for obj in objects:
        pushed = (obj.get("capture") or {}).get("pushed") or {}
        when = _mp_when(pushed.get("at"))
        account = routes[(obj["manual_id"], obj["label"])]["routing"]
        if when is not None and (account not in newest or when > newest[account]):
            newest[account] = when
    out = {}
    for account, state in states.items():
        crawled = bool(state["known_ids"] or state["runs"])
        latest = state["latest_complete"]
        as_of = _mp_when(latest["started_at"]) if latest else None
        age_h = (now - as_of).total_seconds() / 3600.0 if as_of is not None else None
        push = newest.get(account)
        if not crawled:
            reason = "not crawled yet"
        elif as_of is None:
            reason = "no complete enumeration yet"
        elif age_h > MAP_INVENTORY_MAX_AGE_H:
            reason = f"the enumeration is {age_h:.1f} h old (limit {MAP_INVENTORY_MAX_AGE_H} h)"
        elif push is not None and push > as_of:
            reason = f"a push at {_mp_iso(push)} is newer than the enumeration ({_mp_iso(as_of)})"
        else:
            reason = None
        gone = set(state["possibly_deleted"])
        gaps = sum(1 for pid in state["known_ids"] if pid not in state["detailed"] and pid not in gone)
        out[account] = {"account": account, "crawled": crawled, "fresh": reason is None, "reason": reason,
                        "as_of": _mp_iso(as_of), "age_h": age_h, "newest_push": _mp_iso(push),
                        "items": len(state["items"]), "gaps": gaps, "possibly_deleted": len(gone),
                        "recrawl": _MP_RECRAWL.format(account=account)}
    return out


def _mp_item(account, rec):
    """An inventory record as a mapper item: the account + the P-1 item facts it carries (None when unknown)."""
    item = {"account": account}
    item.update((key, rec.get(key)) for key in _MP_ITEM_KEYS)
    item["dedup_key"] = gpweb.normalize_dedup_key(item["dedup_key"])
    return item


def _mp_tag_key(name):
    """(kind, chunk number) of a file name for the `[short_id]`-tag index: ('chunk', NNN) for '….chunk.NNN.mkv',
    ('holder', None) for '….holder.mkv', else ('whole', None)."""
    number = gpweb.chunk_index(name)
    if number is not None:
        return "chunk", number
    return ("holder", None) if _MP_HOLDER_RE.search(name or "") else ("whole", None)


def _mp_index(states):
    """Per account, the live inventory items indexed for the ladder: by exact file name, by P-6 normalized name, and
    by MediaVault `[short_id]` tag ((tag, kind, chunk number) -> [items], the OD-14 probable match), and by dedupKey
    (the SHA-1 of the uploaded bytes — PLAN Step 9 (3): content proof); chunk and FLAC-holder items also by the
    normalized name they were cut from (`stems`: the IMP-C27 (a) split_info-lost detector). Items absent from
    the latest complete enumeration (possibly deleted) never match — they are kept by normalized name under
    `deleted` (reported: an item that was there and is gone). A record without a file name or with a crawl error is
    left out: its id counts in the freshness `gaps`, which withhold every "in no account" verdict."""
    index = {}
    for account, state in states.items():
        gone = set(state["possibly_deleted"])
        exact, normalized, tags, dedup, deleted, stems = {}, {}, {}, {}, {}, {}
        for pid in sorted(state["items"]):
            rec = state["items"][pid]
            item = _mp_item(account, rec)
            name = item["filename"] if isinstance(item["filename"], str) and item["filename"].strip() else None
            if not name:
                continue
            key = gpweb.normalize_name(name)
            if pid in gone:
                deleted.setdefault(key, []).append(item)
            elif not rec.get("error"):
                exact.setdefault(name, []).append(item)
                normalized.setdefault(key, []).append(item)
                kind, number = _mp_tag_key(name)
                for tag in dict.fromkeys(_MP_TAG_RE.findall(name)):
                    tags.setdefault((tag, kind, number), []).append(item)
                if item["dedup_key"]:
                    dedup.setdefault(item["dedup_key"], []).append(item)
                if kind != "whole":                    # a chunk or a holder: indexed by the name it was cut from
                    cut = name.lower().rfind(".chunk." if kind == "chunk" else ".holder.")
                    stems.setdefault((kind, gpweb.normalize_name(name[:cut])), []).append(item)
        index[account] = {"exact": exact, "normalized": normalized, "tags": tags, "dedup": dedup, "deleted": deleted,
                          "stems": stems}
    return index


def _mp_content_key(obj):
    """The object's content key — the dedupKey of its own bytes — from its trusted block, else its capture, or None."""
    block = obj.get("block")
    key = _gp_content_key(block) if isinstance(block, dict) else None
    return key or gpweb.normalize_dedup_key((obj.get("capture") or {}).get("dedup_key"))


def _mp_expected_local(obj):
    """The taken time Google Photos will show for an object, from its capture — the file's DateUTC in the pushing
    phone's timezone: 'YYYY-MM-DD HH:MM:SS GMT+08:00' (UTC when the offset is unknown) — or None (no capture)."""
    capture = obj.get("capture") or {}
    when = _mp_when(capture.get("date_utc"))
    if when is None:
        return None
    offset = (capture.get("pushed") or {}).get("device_tz_offset_min")
    if not isinstance(offset, int) or isinstance(offset, bool):
        return when.strftime("%Y-%m-%d %H:%M:%S UTC")
    local = when + timedelta(minutes=offset)
    sign = "-" if offset < 0 else "+"
    return local.strftime("%Y-%m-%d %H:%M:%S") + f" GMT{sign}{abs(offset) // 60:02d}:{abs(offset) % 60:02d}"


def _mp_row(obj, route):
    """The mapping.jsonl row of one object, before its verdict (status None)."""
    return {"manual_id": obj["manual_id"], "label": obj["label"], "kind": obj["kind"], "category": obj["category"],
            "sha256": obj["sha256"], "uploaded_name": obj["uploaded_name"], "cloud_state": obj["cloud_state"],
            "default_account": route["default"], "override": route["override"], "learned_home": route["home"],
            "routing": route["routing"], "account": route["default"], "status": None, "issue": None,
            "name_match": None, "proof": None, "item": None, "alternates": [], "copies": [], "candidates": [],
            "deleted": [], "override_check": None, "duplicate": None, "set": None, "device": None,
            "partial_set": False, "content_key": _mp_content_key(obj), "expected_taken_local": _mp_expected_local(obj),
            "reason": None, "user_decision": None, "note": None, "repair": None}


def _mp_names(index_account, name):
    """(items, name_match) for a name in one account's index: the exact matches, else the P-6 normalized ones."""
    if not (isinstance(name, str) and name):
        return [], None
    hits = index_account["exact"].get(name)
    if hits:
        return list(hits), "exact"
    hits = index_account["normalized"].get(gpweb.normalize_name(name))
    return (list(hits), "normalized") if hits else ([], None)


def _mp_key(obj):
    return obj["manual_id"], obj["label"]


def _mp_groups(objects):
    """The objects as matching groups, in object order: the chunks of ONE split — a leaf's, or one extras item's —
    are decided together (PLAN Step 9 (4): all chunks of a split share one taken second); every other object is a
    group of its own."""
    groups, splits = [], {}
    for obj in objects:
        if obj["kind"] in _MP_CHUNK_KINDS:
            key = (obj["manual_id"], obj["kind"], obj.get("group_rel"), obj.get("sub_rel"))
            if key not in splits:
                splits[key] = []
                groups.append(splits[key])
            splits[key].append(obj)
        else:
            groups.append([obj])
    return groups


def _mp_decision(kind, name_match, item=None, candidates=(), proof=None, alternates=(), duplicate=None,
                 chunk_set=None, note=None):
    """One account's answer for one object: mapped (item + proof), ambiguous / conflict (candidates)."""
    return {"kind": kind, "name_match": name_match, "item": item, "candidates": list(candidates), "proof": proof,
            "alternates": list(alternates), "duplicate": duplicate, "set": chunk_set, "note": note}


def _mp_pick(items):
    """The one item to record among items holding the SAME bytes: a complete, original-quality, unarchived copy
    first, then the earliest upload, then the photo id (deterministic) — the others become its alternates."""
    return sorted(items, key=lambda i: (i.get("partial_upload") is True, i.get("original_quality") is False,
                                        i.get("archived") is True,
                                        i["upload_ms"] if isinstance(i.get("upload_ms"), int) else float("inf"),
                                        i["photo_id"]))[0]


def _mp_by_content(obj, idx, content_key):
    """PLAN Step 9 (3): the decision for an object whose content key is known — the items holding its bytes
    (dedupKey) prove it (proof dedupkey; name_match exact / normalized, or `content` under another name); without
    one, same-named items hold OTHER bytes: conflict (never applied). None when neither exists."""
    named, how = _mp_names(idx, obj["uploaded_name"])
    hits = idx["dedup"].get(content_key, [])
    if hits:
        item = _mp_pick(hits)
        others = [i["photo_id"] for i in hits if i["photo_id"] != item["photo_id"]]
        match = gpweb.names_match(item["filename"], obj["uploaded_name"] or "") or "content"
        decision = _mp_decision("mapped", match, item=item, proof="dedupkey", alternates=others,
                                duplicate="same-bytes" if others else None)
        foreign = [i for i in named if i["dedup_key"] != content_key]
        if foreign:
            decision["note"] = (f"{len(foreign)} other item(s) carry this name with other bytes (a superseded "
                                f"upload?) — not recorded")
        return decision
    if named:
        return _mp_decision("conflict", how, candidates=named,
                            note=f"{len(named)} item(s) carry this name, none holds the object's bytes (dedupKey)")
    return None


def _mp_bytes(items):
    """`items` grouped by the bytes they hold (dedupKey; an item without one stands alone) -> [[items]], first
    appearance order."""
    groups = {}
    for item in items:
        groups.setdefault(item["dedup_key"] or ("photo", item["photo_id"]), []).append(item)
    return list(groups.values())


def _mp_second(item):
    """The taken second of an item (all chunks of one split share it — RESEARCH F4), or None."""
    taken = item.get("taken_ms")
    return taken // 1000 if isinstance(taken, int) and not isinstance(taken, bool) else None


def _mp_one_of(members, how, alternates=(), duplicate=None, chunk_set=None, note=None):
    """mapped: the picked item of one group of same-bytes items; the rest of the group + `alternates` (photo ids)."""
    item = _mp_pick(members)
    others = [i["photo_id"] for i in members if i["photo_id"] != item["photo_id"]] + list(alternates)
    return _mp_decision("mapped", how, item=item, proof="filename", alternates=others,
                        duplicate=duplicate or ("same-bytes" if len(members) > 1 else None), chunk_set=chunk_set,
                        note=note)


def _mp_by_name(items, how):
    """PLAN Step 9 (4), one object alone: one group of same bytes -> mapped (one item, the rest alternates); several
    groups of different bytes -> ambiguous."""
    groups = _mp_bytes(items)
    if len(groups) == 1:
        return _mp_one_of(groups[0], how)
    return _mp_decision("ambiguous", how, candidates=items,
                        note=f"{len(groups)} items with this name hold different bytes")


def _mp_chunk_set(loose, pins, size):
    """PLAN Step 9 (4), the name-matched chunks of ONE split in one account (`loose`: [(obj, items, name_match)], no
    content key) -> {key: decision}. All chunks of a split share its taken second (RESEARCH F4), so the candidates are
    grouped by second (one second = one upload of the split):
      * `pins` — the seconds of this split's content-PROVEN chunks: one pin decides the set (another second's items
        are other bytes: conflict); several pins: ambiguous;
      * no chunk name with different-bytes duplicates: each chunk maps to its own item (seconds that differ are
        only noted — never seen in the real library: 250 of 250 such sets sit on one second);
      * one second: each chunk by its own items (same bytes collapse; different bytes: ambiguous);
      * several seconds (the split was uploaded more than once): the newest COMPLETE set — every chunk of the split
        exactly once — when no newer upload exists; its chunks map, the other uploads' items become alternates. A
        newer incomplete upload (perhaps still backing up), or no complete one, leaves every chunk ambiguous."""
    per = {}
    for obj, items, _how in loose:
        by_second = {}
        for members in _mp_bytes(items):
            by_second.setdefault(_mp_second(_mp_pick(members)), []).append(members)
        per[_mp_key(obj)] = by_second
    seconds = set(pins) | {s for by_second in per.values() for s in by_second}
    out = {}
    if pins:
        for obj, items, how in loose:
            here = per[_mp_key(obj)].get(next(iter(pins))) if len(pins) == 1 else None
            if here and len(here) == 1:
                out[_mp_key(obj)] = _mp_one_of(here[0], how, note="the split's upload is pinned by a chunk proven "
                                                                  "by its bytes")
            elif len(pins) == 1 and not here:
                out[_mp_key(obj)] = _mp_decision("conflict", how, candidates=items,
                                                 note="only another upload of this split (another taken second) "
                                                      "carries this name — not the object's bytes")
            else:
                out[_mp_key(obj)] = _mp_decision("ambiguous", how, candidates=items,
                                                 note="this split's chunks cannot be told apart")
        return out
    if all(sum(len(groups) for groups in per[_mp_key(o)].values()) == 1 for o, _i, _h in loose):
        note = (f"this split's chunks carry {len(seconds)} different taken seconds (mixed uploads?)"
                if len(seconds) > 1 else None)
        return {_mp_key(obj): _mp_one_of(next(iter(per[_mp_key(obj)].values()))[0], how, note=note)
                for obj, _items, how in loose}
    if len(seconds) <= 1:
        return {_mp_key(obj): _mp_by_name(items, how) for obj, items, how in loose}
    ordered = sorted(seconds, key=lambda s: float("-inf") if s is None else s)
    complete = [s for s in ordered if len(loose) == size and all(len(per[_mp_key(o)].get(s, [])) == 1
                                                                  for o, _i, _h in loose)]
    if complete and complete[-1] == ordered[-1]:
        chosen = complete[-1]
        info = {"taken_s": chosen, "uploads": len(seconds), "chunks": size}
        for obj, items, how in loose:
            members = per[_mp_key(obj)][chosen][0]
            elsewhere = [i["photo_id"] for s, groups in per[_mp_key(obj)].items() if s != chosen
                         for g in groups for i in g]
            out[_mp_key(obj)] = _mp_one_of(members, how, alternates=elsewhere, duplicate="re-split",
                                           chunk_set=info)
        return out
    note = (f"a newer upload of this split is incomplete ({len(seconds)} uploads; maybe still backing up)"
            if complete else f"this split was uploaded {len(seconds)} times and no upload is complete")
    return {_mp_key(obj): _mp_decision("ambiguous", how, candidates=items, note=note) for obj, items, how in loose}


def _mp_decide(group, idx, keys):
    """ONE account's decision for each object of a matching group -> {(manual_id, label): decision or None}. An
    object with a known content key (keys) is decided by its bytes (_mp_by_content); any other by the items carrying
    its exact uploaded name, else its P-6 normalized name — alone (_mp_by_name), or, for a split's chunks, as one
    set (_mp_chunk_set). None: no item."""
    out, loose = {}, []
    for obj in group:
        key = _mp_key(obj)
        if keys.get(key):
            out[key] = _mp_by_content(obj, idx, keys[key])
            continue
        items, how = _mp_names(idx, obj["uploaded_name"])
        out[key] = None
        if items:
            loose.append((obj, items, how))
    if not loose:
        return out
    if group[0]["kind"] in _MP_CHUNK_KINDS:
        pins = {_mp_second(out[_mp_key(o)]["item"]) for o in group
                if keys.get(_mp_key(o)) and out[_mp_key(o)] and out[_mp_key(o)]["kind"] == "mapped"}
        out.update(_mp_chunk_set(loose, pins, len(group)))
    else:
        for obj, items, how in loose:
            out[_mp_key(obj)] = _mp_by_name(items, how)
    return out


def _mp_take(row, decision, account):
    """Copy one account's decision into the row: mapped -> mapped / mapped-normalized; ambiguous; conflict."""
    if decision["kind"] == "mapped":
        status = "mapped" if decision["name_match"] != "normalized" else "mapped-normalized"
    else:
        status = decision["kind"]
    row.update(status=status, account=account, name_match=decision["name_match"], proof=decision["proof"],
               item=dict(decision["item"]) if decision["item"] else None,
               candidates=[dict(c) for c in decision["candidates"]], alternates=list(decision["alternates"]),
               duplicate=decision["duplicate"], set=decision["set"])
    if decision["note"]:
        _mp_note(row, decision["note"])


def _mp_note(row, text):
    row["note"] = text if not row["note"] else f"{row['note']}; {text}"


def _mp_stale(row, account, reason):
    row.update(status="stale-inventory", account=row["default_account"])
    _mp_note(row, f"{account}: {reason}")


def _mp_preferred(row, accounts):
    """`accounts` with the row's override first, then its learned home, then GP_ACCOUNTS order."""
    order = [a for a in (row["override"], row["learned_home"]) if a] + list(mvcommon.GP_ACCOUNTS)
    return sorted(accounts, key=lambda a: order.index(a) if a in order else len(order))


def _mp_settle(row, obj, decisions, fresh, index, library):
    """The verdict of one object from every crawled account's decision (PLAN Step 9 (1)-(3)): its default account
    first (its normalized name there beats an exact name elsewhere — P-6 ladder order), then another account
    (wrong-account), then the `[short_id]`-tag probable; an object with a known content key is decided by its bytes
    wherever they are. An answer that rests on a stale inventory is refused (stale-inventory)."""
    key, home = _mp_key(obj), row["default_account"]
    if not fresh[home]["fresh"]:
        return _mp_stale(row, home, fresh[home]["reason"])
    if row["content_key"]:
        if _mp_settle_by_content(row, key, decisions, fresh):
            return
        return _mp_nowhere(row, obj, fresh, index, library)
    own = decisions[home][key]
    if own is not None:
        _mp_take(row, own, home)
        if row["item"] is not None:
            _mp_copies(row, key, decisions, fresh)
        return
    elsewhere = {a: d[key] for a, d in decisions.items() if a != home and d[key] is not None}
    if elsewhere:
        stale = [a for a in _mp_preferred(row, elsewhere) if not fresh[a]["fresh"]]
        if stale:
            return _mp_stale(row, stale[0], f"the name is in this account, whose inventory is not fresh "
                                            f"({fresh[stale[0]]['reason']})")
        accounts = _mp_preferred(row, elsewhere)
        mapped = [a for a in accounts if elsewhere[a]["kind"] == "mapped"]
        keys = {elsewhere[a]["item"]["dedup_key"] for a in mapped}
        if len(accounts) == 1 or (len(mapped) == len(accounts) and len(keys) == 1 and None not in keys):
            _mp_take(row, elsewhere[accounts[0]], accounts[0])
            if row["item"] is not None:
                row["status"] = "wrong-account"
                row["copies"] = [dict(elsewhere[a]["item"]) for a in accounts[1:]]
            return
        row.update(status="ambiguous", candidates=[dict(c) for a in accounts for c in (
            [elsewhere[a]["item"]] if elsewhere[a]["item"] else elsewhere[a]["candidates"])])
        return _mp_note(row, f"the name is in {len(accounts)} other accounts ({', '.join(accounts)}) with "
                             f"different bytes")
    return _mp_nowhere(row, obj, fresh, index, library)


def _mp_settle_by_content(row, key, decisions, fresh):
    """The verdict of a content-keyed object (PLAN Step 9 (3)) -> True when decided. Its bytes in the default account,
    else in another account (wrong-account; the same bytes in further accounts are recorded copies) — content proof
    outranks any same-named item; else a same-named item with other bytes -> conflict. False: found nowhere."""
    home = row["default_account"]
    order = [home] + _mp_preferred(row, [a for a in decisions if a != home])
    proven = [a for a in order if decisions[a][key] is not None and decisions[a][key]["kind"] == "mapped"]
    if proven:
        if not fresh[proven[0]]["fresh"]:
            _mp_stale(row, proven[0], f"its bytes are in this account, whose inventory is not fresh "
                                      f"({fresh[proven[0]]['reason']})")
            return True
        _mp_take(row, decisions[proven[0]][key], proven[0])
        if proven[0] != home:
            row["status"] = "wrong-account"
        row["copies"] = [dict(decisions[a][key]["item"]) for a in proven[1:] if fresh[a]["fresh"]]
        conflict = decisions[home][key]
        if proven[0] != home and conflict is not None:
            _mp_note(row, f"{home}: {conflict['note']}")
        if row["name_match"] == "content":
            _mp_note(row, f"its bytes were uploaded under another name ({row['item']['filename']}) — "
                          f"--repair names records it")
        return True
    conflicts = [a for a in order if decisions[a][key] is not None]
    if conflicts:
        if not fresh[conflicts[0]]["fresh"]:
            _mp_stale(row, conflicts[0], f"the name is in this account, whose inventory is not fresh "
                                         f"({fresh[conflicts[0]]['reason']})")
            return True
        _mp_take(row, decisions[conflicts[0]][key], conflicts[0])
        return True
    return False


def _mp_nowhere(row, obj, fresh, index, library):
    """An object found in no account by name or bytes: withheld while any crawled inventory is not fresh or not fully
    detailed; else, in order, the `[short_id]`-tag probable (never for a content-keyed object: those items hold other
    bytes), the IMP-C27 (a) split_info lost and (b) old untagged name of a whole file; else not-found (the device
    check decides), with any possibly-deleted same-named item as evidence."""
    blocked = [a for a in mvcommon.GP_ACCOUNTS if fresh[a]["crawled"] and not fresh[a]["fresh"]]
    gaps = [a for a in mvcommon.GP_ACCOUNTS if fresh[a]["crawled"] and fresh[a]["gaps"]]
    if blocked or gaps:
        a = (blocked or gaps)[0]
        why = fresh[a]["reason"] or f"{fresh[a]['gaps']:,} known id(s) without details — re-run the crawl"
        return _mp_stale(row, a, f"not in any account, but that cannot be told while this inventory is not "
                                 f"complete ({why})")
    if not row["uploaded_name"]:
        row.update(status="inconsistent", issue="no-uploaded-name")
        return _mp_note(row, "the library holds no name it was uploaded under (no short_id)")
    probable = None if row["content_key"] else _mp_probable(obj, row, index)
    if probable:
        account, items = probable
        row.update(status="probable", account=account, name_match="tag", candidates=[dict(i) for i in items])
        return
    accounts = [row["default_account"]] + [a for a in mvcommon.GP_ACCOUNTS if a != row["default_account"]]
    if obj["kind"] in ("whole", "extra_whole"):
        stem = gpweb.normalize_name(os.path.splitext(row["uploaded_name"])[0])
        for account in accounts:
            chunks = index[account]["stems"].get(("chunk", stem))
            if chunks:
                holders = index[account]["stems"].get(("holder", stem), [])
                row.update(status="inconsistent", issue="split-info-lost", account=account, name_match="chunks",
                           candidates=[dict(i) for i in chunks + holders])
                row["repair"] = _mp_split_plan(row, obj, chunks, holders, library)
                return _mp_note(row, "the library says one whole file, Google Photos holds its chunks "
                                     "(split_info lost)")
        if not row["content_key"]:
            untagged = _mp_untagged_name(obj)
            for account in accounts:
                items, how = _mp_names(index[account], untagged)
                if items:
                    groups = _mp_bytes(items)
                    if len(groups) > 1:
                        row.update(status="ambiguous", account=account, name_match="untagged",
                                   candidates=[dict(i) for i in items])
                        return _mp_note(row, f"{len(groups)} items carry its old untagged name with different bytes")
                    item = _mp_pick(groups[0])
                    row.update(status="inconsistent", issue="untagged-name", account=account, name_match="untagged",
                               proof="filename", item=dict(item),
                               alternates=[i["photo_id"] for i in groups[0] if i["photo_id"] != item["photo_id"]],
                               repair={"kind": "names", "uploaded_name": item["filename"]})
                    return _mp_note(row, f"uploaded under its old untagged name ({item['filename']}) — "
                                         f"--repair names records it")
    row["status"] = "not-found"
    gone = [dict(i) for a in mvcommon.GP_ACCOUNTS
            for i in index[a]["deleted"].get(gpweb.normalize_name(row["uploaded_name"] or ""), [])]
    if gone:
        row["deleted"] = gone
        _mp_note(row, "an item with this name was in Google Photos before and is absent from the latest "
                      "enumeration (deleted?)")


def _mp_untagged_name(obj):
    """The name a whole object had BEFORE pushes tagged it with ' [<short_id>]' (IMP-C27 (b)): the file's own name —
    a leaf's `filename`, an extras item's last sub_rel part (push_one_extra uploads that)."""
    if obj["kind"] == "extra_whole" and obj.get("sub_rel"):
        return os.path.basename(obj["sub_rel"].replace("/", os.sep)) or obj.get("filename")
    return obj.get("filename")


def _mp_object_folder(library, obj):
    """The local folder an object's push ran from — where cmd_push / push_one_extra keep `checksums/` — or None: a
    leaf's folder_path; an extras item's directory (<title folder>/<group_rel>/<dirname of sub_rel>)."""
    entry = library.get(obj["manual_id"]) if isinstance(library, dict) else None
    folder = entry.get("folder_path") if isinstance(entry, dict) else None
    if not (isinstance(folder, str) and folder):
        return None
    if obj["kind"] in ("extra_whole", "extra_chunk"):
        sub_rel = (obj.get("sub_rel") or "").replace("/", os.sep)
        return os.path.join(folder, (obj.get("group_rel") or "").replace("/", os.sep), os.path.dirname(sub_rel))
    return folder


def _mp_checksum(folder, chunk_name):
    """The SHA-256 cmd_push recorded for a chunk in <folder>/checksums/<chunk>.sha256 ('<hex64> *<name>'), or None
    when the sidecar is missing, unreadable, or names another file."""
    try:
        with open(os.path.join(folder, CHECKSUM_DIR_NAME, f"{chunk_name}.sha256"), encoding="utf-8") as fh:
            text = fh.read().strip()
    except (OSError, UnicodeDecodeError):
        return None
    m = re.fullmatch(r"([0-9a-fA-F]{64})\s+\*?(.+)", text)
    return m.group(1).lower() if m and m.group(2) == chunk_name else None


def _mp_split_plan(row, obj, chunks, holders, library):
    """--repair split-info (PLAN Step 9 (5a)) for a whole object whose chunks are in Google Photos: the split_info
    to write back, PROVEN or not. Provable only when the chunks were uploaded ONCE (one taken second, every chunk
    1..N exactly once, each under the name the push gave it), no FLAC holder was carried out (its track facts are
    gone), and every chunk's SHA-256 is in the local checksums/<chunk>.sha256 sidecar cmd_push wrote. Anything less
    stays a user decision. -> {kind, provable, why, account, slot, sha256, split_info, photo_ids}."""
    plan = {"kind": "split-info", "provable": False, "why": None, "manual_id": obj["manual_id"], "label": obj["label"],
            "account": row["account"], "slot": list(obj["slot"]), "sha256": obj["sha256"], "split_info": None,
            "photo_ids": [i["photo_id"] for i in chunks]}
    seconds = {_mp_second(i) for i in chunks}
    numbers = sorted(gpweb.chunk_index(i["filename"]) for i in chunks)
    prefix = os.path.splitext(row["uploaded_name"])[0] + ".chunk."
    folder = _mp_object_folder(library, obj)
    if len(seconds) > 1 or len(_mp_bytes(chunks)) != len(chunks):
        plan["why"] = (f"its chunks were uploaded more than once ({len(seconds)} taken seconds)" if len(seconds) > 1
                       else "its chunks were uploaded more than once (the same chunk twice)")
    elif numbers != list(range(1, len(chunks) + 1)):
        plan["why"] = f"the chunks in Google Photos are not 1..{len(chunks)} exactly once ({numbers})"
    elif holders:
        plan["why"] = "a FLAC holder was carried out — its track facts are not in the library any more"
    elif not all(i["filename"].startswith(prefix) for i in chunks):
        plan["why"] = "the chunk names in Google Photos differ from the names the push gave them"
    elif not obj["sha256"] or folder is None:
        plan["why"] = "the library entry has no hash or no folder"
    else:
        ordered = sorted(chunks, key=lambda i: gpweb.chunk_index(i["filename"]))
        hashes = [_mp_checksum(folder, i["filename"]) for i in ordered]
        missing = [gpweb.chunk_index(i["filename"]) for i, h in zip(ordered, hashes) if h is None]
        if missing:
            plan["why"] = f"no local checksum sidecar for chunk(s) {missing} (checksums/<chunk>.sha256)"
        else:
            plan.update(provable=True, split_info={
                "is_split": True, "total_chunks": len(ordered),
                "chunks": [{"filename": i["filename"], "hash": h} for i, h in zip(ordered, hashes)]})
    return plan


def _mp_write_split_info(plans):
    """--repair split-info --apply (PLAN Step 9 (5a)): write each PROVABLE plan's split_info into its whole-file node
    — guarded exactly like the apply engine (OD-2: a fresh load, the library fingerprint re-checked right before the
    ONE save, reload + re-patch up to GPHOTOS_APPLY_ATTEMPTS times) and only while the node still IS that whole file
    (the same hash, not split). Nothing else of the node changes (its `re_hashed` and `hash` stay: the first restore
    blesses or verifies the merge — bless_or_verify_merged_hash). No rollback journal (the gphotos-engine rule).
    Never raises (one warning line). -> {status: nothing-to-apply | saved | up-to-date | aborted | error, repaired,
    skipped: [{manual_id, label, why}], attempts, warning}."""
    report = {"status": None, "repaired": [], "skipped": [], "attempts": 0, "warning": None}
    plans = [p for p in plans if p.get("provable") and p.get("split_info")]
    if not plans:
        report["status"] = "nothing-to-apply"
        return report
    saving = False
    try:
        for attempt in range(1, GPHOTOS_APPLY_ATTEMPTS + 1):
            report["attempts"] = attempt
            before = _library_fingerprint()
            library = load_library()
            repaired, skipped = [], []
            for plan in plans:
                node = mvcommon.slot_owner(library, plan["slot"])
                split = node.get("split_info") if isinstance(node, dict) else None
                where = {"manual_id": plan["manual_id"], "label": plan["label"]}
                if node is None or node.get("hash") != plan["sha256"]:
                    skipped.append(dict(where, why="the object changed since it was mapped — re-run map_photos"))
                elif isinstance(split, dict) and split.get("is_split"):
                    skipped.append(dict(where, why="it is split already"))
                else:
                    node["split_info"] = copy.deepcopy(plan["split_info"])
                    repaired.append(dict(where, chunks=plan["split_info"]["total_chunks"]))
            if repaired and _library_fingerprint() != before:
                continue  # another process wrote the library after our load: reload + re-patch
            if repaired:
                saving = True
                save_library(library)
                saving = False
            report.update(status="saved" if repaired else "up-to-date", repaired=repaired, skipped=skipped)
            return report
        report.update(status="aborted", warning=f"⚠️  split_info not repaired: {_GP_BUSY}.")
        print(report["warning"])
        return report
    except (Exception, SystemExit) as e:
        what = "the library could not be loaded" if isinstance(e, SystemExit) else _gp_line(e)
        tail = "the library save was interrupted — re-run to finish it" if saving else "the library was not changed"
        report.update(status="error", warning=f"⚠️  split_info not repaired ({what}) — {tail}.")
        try:
            print(report["warning"])
        except Exception:
            pass
        return report


def _mp_copies(row, key, decisions, fresh):
    """Extra copies (PLAN P-4): the same bytes (same dedupKey) under the object's name in another FRESH account —
    recorded on apply; a same-named item with other bytes, or one in a stale inventory, is only noted."""
    for account in _mp_preferred(row, [a for a in decisions if a != row["account"]]):
        other = decisions[account][key]
        if other is None:
            continue
        item = other["item"]
        if (other["kind"] == "mapped" and fresh[account]["fresh"] and item["dedup_key"]
                and item["dedup_key"] == row["item"]["dedup_key"]):
            row["copies"].append(dict(item))
        elif not fresh[account]["fresh"]:
            _mp_note(row, f"a same-named item is also in {account} (inventory not fresh — not recorded)")
        else:
            _mp_note(row, f"a same-named item with other bytes is also in {account} (not recorded)")


def _mp_probable(obj, row, index):
    """OD-14: (account, items) of the items carrying the object's `[short_id]` tag — same kind, same chunk number —
    under ANOTHER name: the default account first, then the others. None without a short_id or a hit."""
    sid = obj.get("short_id")
    if not sid:
        return None
    kind, number = _mp_tag_key(row["uploaded_name"] or "")
    wanted = {"whole": "whole", "extra_whole": "whole", "holder": "holder"}.get(obj["kind"], "chunk")
    if wanted != kind:
        number = None
    for account in [row["default_account"]] + [a for a in mvcommon.GP_ACCOUNTS if a != row["default_account"]]:
        hits = index[account]["tags"].get((sid, wanted, number))
        if hits:
            return account, list(hits)
    return None


def _mp_override_check(row):
    """How the mvconfig override relates to where the item is: covers | differs | none (a wrong-account object no
    override routes) | None (nothing to check)."""
    if row["item"] is None:
        return None
    if row["override"]:
        return "covers" if row["override"] == row["account"] else "differs"
    return "none" if row["status"] == "wrong-account" else None


def _mp_quality(row):
    """IMP-C27 (e): an item flagged partial_upload, or not original quality, by the rpc inventory holds other bytes
    than the upload — a restore would fail its SHA-256 check. Its row becomes `inconsistent` (never applied)."""
    item = row["item"] or {}
    issue = ("partial-upload" if item.get("partial_upload") is True
             else "not-original-quality" if item.get("original_quality") is False else None)
    if issue is None:
        return
    if row["status"] in ("mapped", "mapped-normalized", "wrong-account"):
        row.update(status="inconsistent", issue=issue)
    _mp_note(row, f"Google Photos holds a {issue.replace('-', ' ')} copy — a restore would fail its SHA-256 check")


def _mp_match(objects, routes, index, fresh, library):
    """The verdict of every object (PLAN Step 9 (1)-(5)) -> rows in object order. Offline: objects in no account stay
    `not-found` here; _mp_device_check gives them their device verdict."""
    rows, by_key = [], {}
    for obj in objects:
        by_key[_mp_key(obj)] = _mp_row(obj, routes[_mp_key(obj)])
        rows.append(by_key[_mp_key(obj)])
    crawled = [a for a in mvcommon.GP_ACCOUNTS if fresh[a]["crawled"]]
    for group in _mp_groups(objects):
        keys = {_mp_key(obj): by_key[_mp_key(obj)]["content_key"] for obj in group}
        decisions = {a: _mp_decide(group, index[a], keys) for a in crawled}
        for a in mvcommon.GP_ACCOUNTS:
            decisions.setdefault(a, dict.fromkeys(map(_mp_key, group)))
        for obj in group:
            _mp_settle(by_key[_mp_key(obj)], obj, decisions, fresh, index, library)
    claims = {}
    for row in rows:                          # one old untagged name, several library objects: none can claim it
        if row["issue"] == "untagged-name":
            claims.setdefault((row["account"], row["item"]["photo_id"]), []).append(row)
    for claimants in claims.values():
        if len(claimants) > 1:
            for row in claimants:
                row.update(status="ambiguous", issue=None, proof=None, candidates=[row["item"]], item=None,
                           alternates=[], repair=None)
                _mp_note(row, f"{len(claimants)} library objects share this old untagged name")
    for row in rows:
        _mp_quality(row)
        row["override_check"] = _mp_override_check(row)
    return rows


_MP_DEVICE_STATUS = {"on_device": "awaiting-backup", "freed": "missing-after-backup", "absent": "missing-everywhere",
                     "unchecked": "device-check-incomplete"}   # Step 8's verdict -> the mapper's (never "missing" for
                                                               # `unchecked`: a configured phone was not listed)


def _mp_family(obj):
    """The objects one restore needs together — a split's chunks + its FLAC holders (a leaf's, or one extras item's)
    — share a family key; a whole file has none."""
    if obj["kind"] in ("whole", "extra_whole"):
        return None
    return obj["manual_id"], obj["kind"].startswith("extra"), obj.get("group_rel"), obj.get("sub_rel")


def _mp_device_check(library, objects, rows, cache=None):
    """PLAN Step 9 (5c/d) with Step 8: the READ-ONLY device check (device_inventory() over device_check_aliases()) for
    every object found in no account: on a phone -> awaiting-backup; only its .mvmeta.json sidecar -> missing-after-
    backup (possible cloud loss); on no phone of a complete check -> missing-everywhere; a configured phone not
    listed -> device-check-incomplete (never "missing"). Such an object of a split whose other members ARE in Google
    Photos is flagged partial_set (IMP-C27 (c)). -> the check's summary, or None when no object needed it (then no
    adb command runs). `cache` (a dict) keeps the phones' listing for a second pass of the same run."""
    todo = [r for r in rows if r["status"] == "not-found"]
    if not todo:
        return None
    objs = {_mp_key(o): o for o in objects}
    if cache is not None and "inventory" in cache:
        inventory = cache["inventory"]
    else:
        inventory = device_inventory()
        if cache is not None:
            cache["inventory"] = inventory
    result = classify_device_objects(library, inventory, objects=[objs[(r["manual_id"], r["label"])] for r in todo])
    devices = inventory.get("devices") or {}
    away = ", ".join(f"{alias} ({(devices.get(alias) or {}).get('serial')}) {status}"
                     for alias, status in result["incomplete_devices"].items())
    for row, verdict in zip(todo, result["objects"]):
        row["status"] = _MP_DEVICE_STATUS[verdict["state"]]
        row["device"] = {"state": verdict["state"], "expected_size": verdict["expected_size"],
                         "copies": [{k: c.get(k) for k in ("alias", "serial", "path", "size", "size_match")}
                                    for c in verdict["copies"]],
                         "sidecars": [{k: s.get(k) for k in ("alias", "serial", "path", "mtime")}
                                      for s in verdict["sidecars"]],
                         "partials": [{k: p.get(k) for k in ("alias", "serial", "path", "size")}
                                      for p in verdict["partials"]]}
        if verdict["state"] == "unchecked" and away:
            _mp_note(row, f"not listed: {away} — connect it and re-run")
    by_key, families = {(r["manual_id"], r["label"]): r for r in rows}, {}
    for obj in objects:
        if _mp_family(obj) is not None:
            families.setdefault(_mp_family(obj), []).append(by_key[_mp_key(obj)])
    lost = set(_MP_DEVICE_STATUS.values())
    for members in families.values():
        if any(m["status"] in lost for m in members) and any(
                m["status"] not in lost and m["status"] != "stale-inventory" for m in members):
            for m in members:
                if m["status"] in lost:
                    m["partial_set"] = True
    return {"complete": result["complete"], "incomplete": result["incomplete_devices"], "counts": result["counts"],
            "devices": devices, "unconfigured": inventory.get("unconfigured") or []}


MAP_UNMATCHED = ("superseded-split", "duplicate", "cross-account-copy", "mediavault-unknown", "non-mediavault",
                 "possibly-deleted", "errored")   # unmatched_<account>.csv categories, in report order


def _mp_unmatched(states, fresh, rows, objects):
    """PLAN Step 9 (6): every inventory item of a FRESH account that no object's verdict uses — categorized for the
    report, NEVER touched (stale cloud copies are never deleted) -> {account: [{category, item, owner, note}]}.
    Categories: superseded-split (a chunk with a library object's chunk name or `[short_id]` tag — another upload of
    it as a split), duplicate (another whole item with a library object's name or tag in its own account),
    cross-account-copy (the name of an object that lives in another account), mediavault-unknown (a
    `[short_id]`-tagged name no library object claims — a removed entry?), non-mediavault (any other upload),
    possibly-deleted (absent from the latest complete enumeration), errored (a record the crawler could not
    complete). Only meaningful for the WHOLE library: the caller runs it for `map_photos all` without --account."""
    used = {}
    for row in rows:
        mine = used.setdefault(row["account"], set())
        if row["item"]:
            mine.add(row["item"]["photo_id"])
        mine.update(row["alternates"])
        for item in row["copies"] + row["candidates"] + row["deleted"]:
            used.setdefault(item["account"], set()).add(item["photo_id"])
    by_name = {gpweb.normalize_name(r["uploaded_name"]): r for r in rows if r["uploaded_name"]}
    by_tag = {}
    for obj in objects:
        if obj.get("short_id"):
            by_tag.setdefault(obj["short_id"], obj)
    rows_by_key = {(r["manual_id"], r["label"]): r for r in rows}
    out = {}
    for account, state in states.items():
        if not fresh[account]["fresh"]:
            continue
        gone, found = set(state["possibly_deleted"]), []
        for pid in sorted(state["items"]):
            if pid in used.get(account, ()):
                continue
            rec = state["items"][pid]
            item = _mp_item(account, rec)
            name = item["filename"] if isinstance(item["filename"], str) else ""
            owner, note = by_name.get(gpweb.normalize_name(name)) if name else None, None
            tag_owner = next((by_tag[t] for t in _MP_TAG_RE.findall(name) if t in by_tag), None)
            if pid in gone:
                category = "possibly-deleted"
            elif rec.get("error") or not name.strip():
                category = "errored"
            elif owner is not None and owner["account"] != account:
                category, note = "cross-account-copy", f"the object lives in {owner['account']}"
            elif owner is not None:
                category = "superseded-split" if gpweb.chunk_index(name) is not None else "duplicate"
            elif tag_owner is not None:
                owner = rows_by_key.get(_mp_key(tag_owner))
                category = "superseded-split" if gpweb.chunk_index(name) is not None else "duplicate"
                note = "carries the object's [short_id] tag under another name"
            elif _MP_TAG_RE.search(name):
                category = "mediavault-unknown"
            else:
                category = "non-mediavault"
            found.append({"category": category, "item": item,
                          "owner": f"{owner['manual_id']} · {owner['label']}" if owner else None, "note": note})
        found.sort(key=lambda u: (MAP_UNMATCHED.index(u["category"]), u["item"]["filename"] or "",
                                  u["item"]["photo_id"]))
        out[account] = found
    return out


_MP_REASONS = {"probable": "probable", "ambiguous": "ambiguous", "conflict": "wrong-item",
               "inconsistent": "inconsistent", "missing-after-backup": "missing-after-backup",
               "missing-everywhere": "missing-everywhere",
               "device-check-incomplete": "not-found"}   # status -> P-18 failed-list reason (unresolved rows only)


def _mp_reasons(rows):
    """Set every row's P-18 failed-list `reason` — None for a resolved row (mapped, wrong-account), one awaiting its
    backup, or one without a verdict (stale-inventory); a lost member of a partially uploaded split: partial-set."""
    for row in rows:
        reason = _MP_REASONS.get(row["status"])
        if reason in ("missing-after-backup", "missing-everywhere") and row["partial_set"]:
            reason = "partial-set"
        row["reason"] = reason


def _mp_status_text(row):
    return (row["status"] + (f": {row['issue']}" if row["issue"] else "")
            + (" (partial set)" if row["partial_set"] else ""))


def _mp_failed_record(row, run):
    """The P-18 failed-list record of an unresolved row — validated by mvcommon (append_failed's own normalizer)."""
    outcome = {"ambiguous": "ambiguous", "conflict": "wrong-item"}.get(
        row["status"], "exactly-one" if row["issue"] in ("partial-upload", "not-original-quality") else "not-found")
    pool = row["candidates"] or ([row["item"]] if row["item"] else [])
    candidates = list(dict.fromkeys(c["photo_id"] for c in pool
                                    if isinstance(c.get("photo_id"), str) and gpweb.MEDIA_KEY_RE.match(c["photo_id"])))
    return mvcommon._normalize_failed({
        "run": run, "account": row["account"], "category": row["category"], "manual_id": row["manual_id"],
        "label": row["label"], "sha256": row["sha256"], "uploaded_name": row["uploaded_name"],
        "expected_taken_local": row["expected_taken_local"],
        "tried": [{"approach": "exact_name", "outcome": outcome, "n": len(pool)}],
        "candidates": candidates or None, "reason": row["reason"]})


def _mp_todo(rows, run):
    """[(failed record, resolutions_todo.csv row)] of every unresolved row the user has not decided yet (a `skip`
    stays) — the CSV row built by mvcommon's own failed_objects.csv row builder (the same 18 columns, P-18), its
    status column the mapper's precise verdict."""
    out = []
    for row in rows:
        if row["reason"] is None or row["user_decision"] not in (None, "skip"):
            continue
        record = _mp_failed_record(row, run)
        csv_row = mvcommon._failed_row(record, row)
        csv_row["status"] = _mp_status_text(row)
        out.append((record, csv_row))
    return out


def _mp_prior_decisions(objects):
    """{(manual_id, label): what} for the objects the user already resolved by hand — a manual photo_id or a search
    term in the trusted block, or a hint on record (hints.jsonl): they wait for `check_photos hinted` / the next
    fetch, not for a second decision. An unreadable hints file reads as none (the report says so)."""
    out, problem = {}, None
    for obj in objects:
        block = obj.get("block") or {}
        accounts = block.get("accounts") if isinstance(block.get("accounts"), dict) else {}
        if any(isinstance(r, dict) and r.get("verified_by") == "manual" and r.get("photo_id")
               for r in accounts.values()):
            out[_mp_key(obj)] = "url (recorded)"
        elif isinstance(block.get("search_override"), dict):
            out[_mp_key(obj)] = "search (recorded)"
    try:
        hints = mvcommon.read_hints()
    except OSError as e:
        hints, problem = [], f"⚠️  hints.jsonl could not be read ({_gp_line(e)}) — earlier decisions not shown"
    pins = {(o["manual_id"], o["sha256"]): _mp_key(o) for o in objects}
    for hint in hints:
        key = pins.get((hint["manual_id"], hint["sha256"]))
        if key is not None and key not in out:
            out[key] = f"{hint['kind']} (recorded)"
    return out, problem


def _mp_resolutions(path, library, objects):
    """--resolutions (PLAN Step 9 (8)): the user's decisions in a resolutions_todo.csv or failed_objects.csv (read by
    mvcommon.read_failed_csv — Excel-tolerant) for the objects in scope. Each row names its object by manual_id (an
    alias resolves) and its sha256 (the P-2 pin; else the `object` label):
      url          -> a learned record {photo_id, proof manual, source manual} (P-5: never confirms by itself) + a hint
      datetime     -> a hint {taken_ms, gmt_offset_min, taken_local}
      search       -> a learned record {search_override, source manual} (stage S3u; still verified at fetch/check)
      not_uploaded -> listed in the IMP-C27 report (the user's word that it never reached Google Photos)
      skip         -> stays on the failed list
    Every record is validated by mvcommon (the writers' own normalizers); nothing is written here. ->
    {path, decisions {key: decision}, learned, hints, counts {decision: n}, out_of_scope, errors [{line, manual_id,
    error}], fatal (the file could not be read at all)}."""
    out = {"path": path, "decisions": {}, "learned": [], "hints": [], "out_of_scope": 0, "errors": [], "fatal": None,
           "counts": dict.fromkeys(mvcommon.GP_DECISIONS, 0)}
    try:
        edits, errors = mvcommon.read_failed_csv(path)
    except (OSError, ValueError) as e:
        out["fatal"] = _gp_line(e)
        return out
    out["errors"].extend(errors)
    in_scope = {_mp_key(o) for o in objects}
    for edit in edits:
        real, entry = _gp_resolve(library, edit["manual_id"])
        objs = list(mvcommon.iter_entry_objects(real, entry)) if entry is not None else []
        if edit.get("sha256"):
            obj = next((o for o in objs if o["sha256"] == edit["sha256"]), None)
        else:
            obj = next((o for o in objs if o["label"] == edit.get("object")), None)
        problem = ("no such library id" if entry is None else
                   "its object is no longer in the library (re-split or re-mastered?) — make a new list"
                   if obj is None else "the library holds no SHA-256 for it" if not obj["sha256"] else None)
        if problem is None and _mp_key(obj) not in in_scope:
            out["out_of_scope"] += 1
            continue
        learned, hints = [], []
        if problem is None:
            base = {"account": edit["account"], "manual_id": real, "label": obj["label"], "sha256": obj["sha256"]}
            try:
                if edit["decision"] == "url":
                    learned.append(mvcommon._normalize_learned(
                        dict(base, photo_id=edit["photo_id"], proof="manual", source="manual"), edit["account"]))
                    hints.append(mvcommon._normalize_hint(dict(base, kind="url", photo_id=edit["photo_id"])))
                elif edit["decision"] == "datetime":
                    hints.append(mvcommon._normalize_hint(dict(
                        base, kind="datetime", taken_ms=edit["taken_ms"], gmt_offset_min=edit["gmt_offset_min"],
                        taken_local=edit["taken_local"])))
                elif edit["decision"] == "search":
                    learned.append(mvcommon._normalize_learned(
                        dict(base, source="manual", search_override=edit["search_term"]), edit["account"]))
            except ValueError as e:
                problem = str(e)
        if problem is not None:
            out["errors"].append({"line": edit["line"], "manual_id": edit["manual_id"], "error": problem})
            continue
        out["learned"].extend(learned)
        out["hints"].extend(hints)
        out["counts"][edit["decision"]] += 1
        out["decisions"][_mp_key(obj)] = edit["decision"]
    out["errors"].sort(key=lambda e: e["line"])
    return out


def _mp_repair_text(row):
    repair = row["repair"] or {}
    if repair.get("kind") == "split-info":
        return ("--repair split-info (proven)" if repair["provable"]
                else f"not provable — {repair['why']} (your decision)")
    if repair.get("kind") == "names":
        return f"--repair names records '{repair['uploaded_name']}'"
    return ""


def _mp_write_todo(run_dir, csv_rows):
    """resolutions_todo.csv into the run directory -> (path, note or None). An existing one that holds the user's
    edits (an --out directory re-used) is never overwritten: this run's list goes to resolutions_todo.new.csv."""
    path = os.path.join(run_dir, "resolutions_todo.csv")
    if os.path.exists(path) and _gp_csv_edits(path) != 0:
        kept = path
        path = os.path.join(run_dir, "resolutions_todo.new.csv")
        note = f"⚠️  {kept} holds your edits — kept; this run's list is {os.path.basename(path)}"
    else:
        note = None
    mvcommon.write_failed_csv(path, csv_rows)
    return path, note


def _mp_utc(ms):
    """Epoch milliseconds -> 'YYYY-MM-DD HH:MM:SS UTC', or '' when unknown."""
    if not isinstance(ms, int) or isinstance(ms, bool):
        return ""
    try:
        return datetime.fromtimestamp(ms / 1000, timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    except (OverflowError, OSError, ValueError):
        return ""


def _mp_write_csv(path, header, rows):
    """An Excel-friendly CSV (UTF-8 with BOM, CRLF) — the failed list's convention (P-18)."""
    with open(path, "w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.writer(fh, lineterminator="\r\n")
        writer.writerow(header)
        writer.writerows([["" if v is None else v for v in row] for row in rows])


def _mp_run_dir(out, now):
    """The run directory: --out as given, else <GPHOTOS_STATE_DIR>/map/<UTC timestamp> (read at call time)."""
    return out if out else os.path.join(mvcommon.GPHOTOS_STATE_DIR, "map", now.strftime("%Y%m%dT%H%M%SZ"))


def _mp_write_text(path, text):
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)


def _mp_write_jsonl(path, records):
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        for record in records:
            fh.write(json.dumps(record, ensure_ascii=False, sort_keys=False) + "\n")


MAP_STATES = ("mapped", "mapped-normalized", "wrong-account", "probable", "ambiguous", "conflict", "inconsistent",
              "awaiting-backup", "missing-after-backup", "missing-everywhere", "device-check-incomplete",
              "stale-inventory")   # every row's final status, in report order
_MP_LOSS = ("missing-after-backup", "missing-everywhere", "device-check-incomplete", "awaiting-backup")
_MP_LOSS_TEXT = {
    "missing-after-backup": "in no account and freed from a phone — only its .mvmeta.json sidecar is left "
                            "(possible cloud LOSS)",
    "missing-everywhere": "in no account and on no phone (possible LOSS)",
    "device-check-incomplete": "in no account; a configured phone was not listed — connect it and re-run",
    "awaiting-backup": "in no account but still on a phone — awaiting its backup (do NOT free it yet)",
}
_MP_INCONSISTENCY = ("inconsistent", "conflict") + _MP_LOSS     # the rows of inconsistencies.csv (IMP-C27)


def _mp_counts_line(counter):
    return " · ".join(f"{state} {n:,}" for state, n in counter.items() if n)


def _mp_device_hint(row):
    """One line of device evidence for a LOUD-block row."""
    device = row.get("device") or {}
    copies, sidecars = device.get("copies") or [], device.get("sidecars") or []
    if row["status"] == "awaiting-backup" and copies:
        return "on " + ", ".join(sorted({f"{c['alias']} ({c['serial']})" for c in copies}))
    if row["status"] == "missing-after-backup" and sidecars:
        return "sidecar only on " + ", ".join(sorted({f"{s['alias']} ({s['serial']})" for s in sidecars}))
    if row["status"] == "missing-everywhere":
        return "on no phone"
    return "a configured phone was not listed"


def _mp_device_lines(devices):
    lines = [f"Device check (read-only; DEVICE_ALIASES + DEVICE_CHECK_EXTRA_SERIALS): "
             f"{'complete' if devices['complete'] else 'INCOMPLETE — nothing is called missing-everywhere'}"]
    for alias, d in devices["devices"].items():
        what = (f"{len(d.get('files') or []):,} file(s)" if d.get("status") in ("ok", "partial") else
                d.get("detail") or "")
        lines.append(f"  {alias:<9} {str(d.get('serial')):<18} {d.get('status')} — {what}".rstrip(" —"))
    for phone in devices["unconfigured"]:
        lines.append(f"  ⚠️  attached but not configured: {phone['serial']} ({phone['state']}) — add it to "
                     f"DEVICE_ALIASES or DEVICE_CHECK_EXTRA_SERIALS to include it")
    return lines


def _mp_engine_line(report, verb):
    """One line for an apply-engine report (dry run: what WOULD change)."""
    if report.get("status") in ("error", "aborted"):
        return f"  ❌ library: {report.get('warning')}"
    return (f"  library ({report['status']}): {len(report['changed']):,} object(s) {verb} ({report['confirmed']:,} "
            f"newly confirmed) · conflict(s) not applied {len(report['conflicts']):,} · orphan(s) "
            f"{len(report['orphans']):,}")


def _mp_apply_lines(ctx):
    """The report's --apply / preview section -> lines."""
    lines, rows = [], ctx["rows"]
    plans = [r for r in rows if (r["repair"] or {}).get("kind") == "split-info"]
    if plans and "split-info" in ctx["repairs"] and not ctx["apply"]:
        lines.append("split_info repairs (--repair split-info, dry run):")
        lines.extend(f"  {r['manual_id']} · {r['label']} — " + (
            f"proven: {r['repair']['split_info']['total_chunks']} chunk(s) would be written"
            if r["repair"]["provable"] else f"not provable — {r['repair']['why']}") for r in plans)
    applied = ctx["applied"]
    if applied is not None:
        split = applied["split_info"]
        if split is not None:
            lines.append(f"split_info repairs: {split['status']} — repaired {len(split['repaired']):,}"
                         + "".join(f"; skipped {s['manual_id']} · {s['label']}: {s['why']}" for s in split["skipped"])
                         + (f" — {split['warning']}" if split["warning"] else ""))
        if applied["error"]:
            lines.append(applied["error"])
            return lines
        lines.append(f"Recorded: learned records {applied['learned'][0]:,} new ({applied['learned'][1]:,} already on "
                     f"record) · hints {applied['hints'][0]:,} new ({applied['hints'][1]:,}) · failed list "
                     f"{applied['failed'][0]:,} new ({applied['failed'][1]:,})")
        lines.append(_mp_engine_line(applied["engine"], "updated"))
        lines.extend(f"    conflict: {c['manual_id']} · {c['label']} · {c['account'] or 'block'} — {c['code']}"
                     for c in applied["engine"].get("conflicts") or [])
        csv_state = applied["failed_csv"]
        if csv_state is None:
            pass
        elif "warning" in csv_state:
            lines.append(csv_state["warning"])
        elif csv_state.get("kept") and csv_state["edited_rows"] is None:
            lines.append("  failed_objects.csv: kept — it no longer reads as the failed list (left as is)")
        elif csv_state.get("kept"):
            lines.append(f"  failed_objects.csv: kept — it holds {csv_state['edited_rows']} edited row(s); import them "
                         f"with --resolutions")
        else:
            lines.append(f"  failed_objects.csv: regenerated — {csv_state['rows']:,} row(s)")
        return lines
    preview = ctx["preview"]
    if preview is not None:
        lines.append("Apply preview (dry run — nothing written):")
        lines.append(preview["warning"] if preview.get("status") == "error" and "changed" not in preview
                     else _mp_engine_line(preview, "would change"))
        flags = ([f"--account {ctx['account']}"] if ctx["account"] else []) + [f"--repair {r}" for r in ctx["repairs"]]
        flags += ["--accept-probable"] if ctx["accept_probable"] else []
        resolution = ctx["resolution"]        # previewed above: the apply must import the same decisions
        flags += [f'--resolutions "{resolution["path"]}"'] if resolution and not resolution["fatal"] else []
        lines.append(f"Next: python main.py map_photos {ctx['scope']} {' '.join(flags + ['--apply'])}")
    return lines


def _mp_report(ctx):
    """The run's text report (printed and saved as summary.txt) -> lines. ctx: _mp_compute's pass + apply,
    repairs, accept_probable, applied, preview, files, notes, run_dir."""
    rows, fresh, account = ctx["rows"], ctx["fresh"], ctx["account"]
    lines = [f"=== MAP PHOTOS ({'APPLY' if ctx['apply'] else 'dry-run'}) — scope: {ctx['scope']}"
             f"{' · account ' + account if account else ''} — {len(rows):,} object(s) ===",
             f"Inventory ({mvcommon.GP_INVENTORY_DIR}):"]
    for info in fresh.values():
        if info["fresh"]:
            lines.append(f"  {info['account']:<7} fresh — complete enumeration as of {info['as_of']} "
                         f"({info['age_h']:.1f} h ago), {info['items']:,} items")
        else:
            lines.append(f"  {info['account']:<7} STALE — {info['reason']} — re-crawl: {info['recrawl']}")
    statuses = {state: sum(1 for r in rows if r["status"] == state) for state in MAP_STATES}
    lines.append(f"Verdicts: {_mp_counts_line(statuses) or 'none'}")
    groups = {}
    for row in rows:
        groups.setdefault(f"{row['category']}/{row['account']}", {}).setdefault(row["status"], 0)
        groups[f"{row['category']}/{row['account']}"][row["status"]] += 1
    lines.append("Per category/account:")
    for key in sorted(groups):
        lines.append(f"  {key}: {sum(groups[key].values()):,} — {_mp_counts_line(groups[key])}")
    wrong = [r for r in rows if r["status"] == "wrong-account"]
    if wrong:
        checks = {c: sum(1 for r in wrong if r["override_check"] == c) for c in ("covers", "none", "differs")}
        learned = sum(1 for r in wrong if r["learned_home"] == r["account"])
        lines.append(f"Wrong account (IMP-C26): {len(wrong):,} object(s) live in another account than their id "
                     f"prefix — mvconfig override covers {checks['covers']:,} · none {checks['none']:,} · "
                     f"override points elsewhere {checks['differs']:,} · learned home already {learned:,}")
    away = [r for r in rows if r["override_check"] == "differs"]
    if away:
        lines.append(f"⚠️  mvconfig fetch_account_overrides routes {len(away):,} object(s) away from the account "
                     f"that holds them:")
        lines.extend(f"     {r['manual_id']} · {r['label']} — override {r['override']}, item in {r['account']}"
                     for r in away)
    same = sum(1 for r in rows if r["duplicate"] == "same-bytes" and r["item"])
    resplit = [r for r in rows if r["duplicate"] == "re-split" and r["item"]]
    if same or resplit:
        lines.append(f"Duplicates resolved: same bytes {same:,} · re-split uploads {len(resplit):,} chunk(s) in "
                     f"{len({r['manual_id'] for r in resplit}):,} entr(y/ies) (the newest complete upload; the "
                     f"others recorded as alternates)")
    if statuses["probable"]:
        lines.append(f"Probable (OD-14 — only the [short_id] tag matches): {statuses['probable']:,} — accept them "
                     f"with --accept-probable, or decide row by row in resolutions_todo.csv")
    if statuses["ambiguous"] or statuses["conflict"]:
        lines.append(f"Needs your decision: ambiguous {statuses['ambiguous']:,} · conflict (the name holds other "
                     f"bytes) {statuses['conflict']:,}")
    issues = {}
    for row in rows:
        if row["status"] == "inconsistent":
            issues[row["issue"]] = issues.get(row["issue"], 0) + 1
    if issues:
        provable = sum(1 for r in rows if (r["repair"] or {}).get("kind") == "split-info"
                       and r["repair"]["provable"])
        lines.append(f"Inconsistent (IMP-C27): {_mp_counts_line(issues)}"
                     + (f" — split_info repairable from local checksums: {provable:,} (--repair split-info)"
                        if issues.get("split-info-lost") else ""))
    if ctx["devices"] is not None:
        lines.extend(_mp_device_lines(ctx["devices"]))
    loud = [r for r in rows if r["status"] in _MP_LOSS]
    if loud:
        partial = [r for r in loud if r["partial_set"]]
        lines.append(f"⚠️⚠️⚠️  NOT IN GOOGLE PHOTOS (IMP-C27): {len(loud):,} object(s) — the local copy of an archived "
                     f"entry is a dummy  ⚠️⚠️⚠️")
        for state in _MP_LOSS:
            n = sum(1 for r in loud if r["status"] == state)
            if n:
                lines.append(f"  {state}: {n:,} — {_MP_LOSS_TEXT[state]}")
        if partial:
            lines.append(f"  partial chunk sets: {len(partial):,} object(s) in "
                         f"{len({r['manual_id'] for r in partial}):,} entr(y/ies) — the rest of the set IS in Google "
                         f"Photos")
        for row in loud:
            lines.append(f"    {row['status']:<24} {row['manual_id']} · {row['label']}"
                         f"{' [partial set]' if row['partial_set'] else ''} — {_mp_device_hint(row)}")
    told = [r for r in rows if r["user_decision"] == "not_uploaded"]
    if told:
        lines.append(f"⚠️⚠️⚠️  YOU SAY NOT UPLOADED (IMP-C27): {len(told):,} object(s) — an archived entry keeps only "
                     f"a dummy locally  ⚠️⚠️⚠️")
        lines.extend(f"    {r['manual_id']} · {r['label']} (mapper verdict: {r['status']})" for r in told)
    resolution = ctx["resolution"]
    if resolution is not None and resolution["fatal"]:
        lines.append(f"❌ --resolutions {resolution['path']}: {resolution['fatal']} — nothing imported")
    elif resolution is not None:
        extra = ((f" · out of this run's scope {resolution['out_of_scope']:,}" if resolution["out_of_scope"] else "")
                 + (f" · invalid {len(resolution['errors']):,}" if resolution["errors"] else ""))
        lines.append(f"Resolutions ({resolution['path']}): {sum(resolution['counts'].values()):,} decision(s) — "
                     f"{_mp_counts_line(resolution['counts']) or 'none'}{extra}")
        lines.extend(f"  ❌ line {e['line']} · {e['manual_id'] or '?'} — {e['error']}" for e in resolution["errors"])
        if not ctx["apply"]:
            lines.append("  dry run — nothing recorded: add --apply to record them (url / datetime: "
                         "`check_photos hinted` verifies them before anything is confirmed)")
    if ctx["unmatched"] is None:
        lines.append("Unmatched inventory items: reported by `map_photos all` (without --account) only")
    else:
        parts = []
        for acc, found in ctx["unmatched"].items():
            kinds = {c: sum(1 for u in found if u["category"] == c) for c in MAP_UNMATCHED}
            parts.append(f"{acc} {len(found):,}" + (f" ({_mp_counts_line(kinds)})" if found else ""))
        lines.append("Unmatched inventory items (report only — never touched): "
                     + (" · ".join(parts) or "no fresh account"))
    if ctx["todo"]:
        reasons = {reason: sum(1 for record, _csv_row in ctx["todo"] if record["reason"] == reason)
                   for reason in mvcommon.GP_FAILED_REASONS}
        lines.append(f"To resolve: {len(ctx['todo']):,} row(s) in resolutions_todo.csv ({_mp_counts_line(reasons)}) "
                     f"— fill its decision column (url | datetime | search | not_uploaded | skip), then: "
                     f"python main.py map_photos {ctx['scope']} --resolutions <that csv>")
    lines.extend(_mp_apply_lines(ctx))
    lines.extend(ctx["notes"])
    lines.append(f"Outputs: {ctx['run_dir']} — {', '.join(ctx['files'])}")
    return lines


def _mp_record(row, item, proof, account, alternates=()):
    """The P-10 learned record (source map) that an --apply appends for one row and one item — validated by
    mvcommon's own normalizer (append_learned's). No approach verdict: matching a name in the inventory is not a
    Google Photos search, so it must not become the fetch's `approach.best`."""
    flags = {k: item.get(k) for k in ("original_quality", "partial_upload") if isinstance(item.get(k), bool)}
    return mvcommon._normalize_learned({
        "account": account, "manual_id": row["manual_id"], "label": row["label"], "sha256": row["sha256"],
        "photo_id": item["photo_id"], "taken_ms": item.get("taken_ms"), "tz_offset_ms": item.get("tz_offset_ms"),
        "upload_ms": item.get("upload_ms"), "dedup_key": item.get("dedup_key"), "gp_filename": item.get("filename"),
        "width": item.get("width"), "height": item.get("height"), "proof": proof, "source": "map",
        "alternates": list(alternates) or None, **flags}, account)


def _mp_learned(rows, repairs, accept_probable):
    """PLAN Step 9 (9): the learned records an --apply appends — for mapped / mapped-normalized / wrong-account
    rows (with their recorded same-bytes copies in other accounts), the old-untagged-name rows when --repair names
    is given, and — with --accept-probable — a probable row whose candidates are ONE item's bytes in one account
    (proof manual: it never confirms by itself, P-5). Rows without a verdict (stale-inventory) or without a SHA-256
    never record anything."""
    out = []
    for row in rows:
        if not row["sha256"]:
            continue
        item, proof, alternates = row["item"], row["proof"], row["alternates"]
        take = row["status"] in ("mapped", "mapped-normalized", "wrong-account")
        if row["issue"] == "untagged-name" and "names" in repairs:
            take = True
        if row["status"] == "probable" and accept_probable:
            groups = _mp_bytes(row["candidates"])
            if len(groups) == 1 and len({c["account"] for c in row["candidates"]}) == 1:
                item, proof, take = _mp_pick(groups[0]), "manual", True
                alternates = [c["photo_id"] for c in groups[0] if c["photo_id"] != item["photo_id"]]
        if not take or item is None:
            continue
        out.append(_mp_record(row, item, proof, row["account"] if row["status"] != "probable" else item["account"],
                              alternates))
        out.extend(_mp_record(row, copy_, proof, copy_["account"]) for copy_ in row["copies"])
    return out


def _mp_names_facts(rows):
    """--repair names: [{manual_id, sha256, uploaded_name}] — the name each object was REALLY uploaded under, when
    the inventory proves another than the library's: an old untagged name (IMP-C27 (b)), or the bytes found under
    another name (content proof)."""
    facts = []
    for row in rows:
        if not row["sha256"]:
            continue
        if row["issue"] == "untagged-name":
            facts.append({"manual_id": row["manual_id"], "sha256": row["sha256"],
                          "uploaded_name": row["repair"]["uploaded_name"]})
        elif row["name_match"] == "content" and row["item"] and row["status"] in ("mapped", "wrong-account"):
            facts.append({"manual_id": row["manual_id"], "sha256": row["sha256"],
                          "uploaded_name": row["item"]["filename"]})
    return facts


def _mp_fingerprint(record, drop=("v", "at")):
    return json.dumps({k: v for k, v in record.items() if k not in drop}, sort_keys=True, ensure_ascii=False)


def _mp_append_new(records, existing, append):
    """Append the records not on record yet (same facts, whatever their time) -> (appended, already recorded)."""
    seen = {_mp_fingerprint(r) for r in existing}
    added = known = 0
    for record in records:
        if _mp_fingerprint(record) in seen:
            known += 1
            continue
        append(record)
        seen.add(_mp_fingerprint(record))
        added += 1
    return added, known


def _mp_append_failed(records):
    """Append the failed-list rows whose object's newest row on the list says something else -> (appended, already
    listed): a re-run that learned nothing new adds nothing (P-18's list is append-only)."""
    newest = {}
    for rec in mvcommon.read_failed():
        newest[(rec["manual_id"], rec.get("sha256") or "label:" + rec.get("label", ""))] = rec
    added = known = 0
    for record in records:
        key = (record["manual_id"], record.get("sha256") or "label:" + record.get("label", ""))
        held = newest.get(key)
        if held is not None and _mp_fingerprint(held, ("at", "run")) == _mp_fingerprint(record, ("at", "run")):
            known += 1
            continue
        mvcommon.append_failed(record)
        newest[key] = record
        added += 1
    return added, known


def _mp_refresh_failed_csv(resolution):
    """P-18 after an --apply: regenerate failed_objects.csv from the failed list — unless it holds the user's edits
    that this run did NOT consume (it imported exactly that file with no invalid or out-of-scope row: then the edits
    are recorded and the regenerated list replaces them). -> export_failed_csv's result, {path, kept, edited_rows},
    {warning}, or None (no failed list)."""
    csv_path = mvcommon.failed_csv_path()
    if not (os.path.exists(mvcommon.failed_path()) or os.path.exists(csv_path)):
        return None
    consumed = False
    if resolution is not None and not resolution["fatal"] and os.path.exists(csv_path):
        try:
            consumed = (os.path.samefile(resolution["path"], csv_path) and not resolution["errors"]
                        and not resolution["out_of_scope"])
        except OSError:
            consumed = False
    if os.path.exists(csv_path) and not consumed:
        edits = _gp_csv_edits(csv_path)
        if edits != 0:
            return {"path": csv_path, "kept": True, "edited_rows": edits}
    try:
        return mvcommon.export_failed_csv(load_library())
    except (Exception, SystemExit) as e:
        return {"warning": f"⚠️  failed_objects.csv not refreshed ({_gp_line(e)}) — close it if it is open (e.g. in "
                           f"Excel); the next apply refreshes it."}


def _mp_preview(ctx, repairs, accept_probable):
    """A dry run's preview of --apply: the apply engine's dry-run report over the learned sidecar plus this run's
    records and name facts — nothing is written. None when there is nothing to apply."""
    records = _mp_learned(ctx["rows"], repairs, accept_probable)
    if ctx["resolution"]:
        records += ctx["resolution"]["learned"]
    names = _mp_names_facts(ctx["rows"]) if "names" in repairs else []
    if not records and not names:
        return None
    try:
        existing = mvcommon.read_learned()
    except OSError as e:
        return {"status": "error",
                "warning": f"⚠️  no apply preview: the learned sidecar is unreadable ({_gp_line(e)})"}
    return gphotos_apply(existing + records, capture=True, dry_run=True, names=names)


def _mp_apply(ctx, repairs, accept_probable, recompute):
    """PLAN Step 9 (9) --apply -> (summary, ctx). In order: the PROVEN split_info repairs (--repair split-info), then
    — when any was written — a fresh mapping pass, so the repaired entries' chunks map in this same run; the learned
    records (source map) and the user's resolution records appended to the learned sidecars (append_learned — the
    durable record that survives an IMP-C24 lost update), the hints, the failed-list rows for what stays unresolved
    (each only when not on record yet); then the apply engine over the whole sidecar (+ the --repair names facts),
    and failed_objects.csv per P-18. A write failure stops before the engine (one line; re-run --apply)."""
    summary = {"split_info": None, "learned": (0, 0), "hints": (0, 0), "failed": (0, 0), "engine": None,
               "failed_csv": None, "error": None}
    if "split-info" in repairs:
        plans = [r["repair"] for r in ctx["rows"] if (r["repair"] or {}).get("kind") == "split-info"
                 and r["repair"]["provable"]]
        summary["split_info"] = _mp_write_split_info(plans)
        if summary["split_info"]["repaired"]:
            ctx = recompute()
    records = _mp_learned(ctx["rows"], repairs, accept_probable)
    hints = []
    if ctx["resolution"] and not ctx["resolution"]["fatal"]:
        records += ctx["resolution"]["learned"]
        hints = ctx["resolution"]["hints"]
    try:
        summary["learned"] = _mp_append_new(records, mvcommon.read_learned(),
                                            lambda r: mvcommon.append_learned(r["account"], r))
        summary["hints"] = _mp_append_new(hints, mvcommon.read_hints(), mvcommon.append_hint)
        summary["failed"] = _mp_append_failed([record for record, _csv_row in ctx["todo"]])
        learned = mvcommon.read_learned()
    except (OSError, ValueError) as e:
        summary["error"] = (f"❌ map_photos --apply stopped: {_gp_line(e)} — the library was not changed; re-run "
                            f"--apply (records already appended are not appended twice)")
        return summary, ctx
    names = _mp_names_facts(ctx["rows"]) if "names" in repairs else []
    summary["engine"] = gphotos_apply(learned, capture=True, dry_run=False, names=names)
    summary["failed_csv"] = _mp_refresh_failed_csv(ctx["resolution"])
    return summary, ctx


def cmd_map_photos(scope="all", account=None, devices_only=False, resolutions=None, repairs=(),
                   accept_probable=False, apply=False, out=None):
    """`map_photos` (PLAN Step 9): map the uploaded library objects to the Google Photos inventory — the per-object
    rules are in the section comment above. A DRY RUN by default.

    scope             all (the whole library) | a library id | an id prefix (a season id selects its episodes; an
                      alias id maps its primary)
    account           only the objects ROUTED to that account (mvconfig override > learned home > id prefix)
    devices_only      only Step 8's read-only device report (devices.csv): no inventory, nothing recorded
    resolutions       a filled resolutions_todo.csv / failed_objects.csv: url -> a manual-proof learned record + a
                      hint, datetime -> a hint (both verified later by `check_photos hinted`), search -> the
                      object's search_override, not_uploaded -> the LOUD list, skip -> stays unresolved
    repairs           "split-info": write back a PROVEN lost split_info · "names": record the name an object was
                      really uploaded under (through the apply engine) — both only with apply
    accept_probable   also record single-item `[short_id]`-tag matches (proof manual: they never confirm)
    apply             record: learned sidecars (append_learned), hints, failed list, then the apply engine (the
                      only writer of `gphotos` blocks) over the whole sidecar; failed_objects.csv per P-18. Nothing
                      is appended twice. Refused (nothing recorded) when the resolutions file cannot be read.
    out               the run directory (default <GPHOTOS_STATE_DIR>/map/<UTC timestamp>), created first

    Row status (mapping.jsonl): mapped · mapped-normalized · wrong-account (these three are applied) · probable ·
    ambiguous · conflict · inconsistent (issue: split-info-lost | untagged-name | partial-upload |
    not-original-quality | no-uploaded-name) · awaiting-backup · missing-after-backup · missing-everywhere ·
    device-check-incomplete · stale-inventory (no verdict). Run directory: summary.txt (the printed report),
    mapping.jsonl, mapping.csv (its sheet for a reader: one row per object, the names side by side, no photo id),
    inconsistencies.csv, resolutions_todo.csv (the failed_objects.csv columns), unmatched_<account>.csv (`all`
    without account only); devices.csv for devices_only. A report file that cannot be written (open in Excel) ends
    the run with one line and status error — after an apply, what was recorded stays on record.

    -> {status: ok | error, run_dir, rows, fresh, devices, unmatched, todo (the failed-list records of the
    unresolved rows), applied (the --apply summary: split_info, learned / hints / failed (new, already on record),
    engine (gphotos_apply's report), failed_csv, error), preview (a dry run's engine report)}."""
    now = mvcommon._now_utc()
    library = load_library()
    if scope in (None, "", "all"):
        scope, ids = "all", None
    else:
        ids = sorted(mid for mid in library if isinstance(mid, str) and (mid == scope or mid.startswith(scope)))
        if not ids:
            print(f"❌ map_photos: no library id is or starts with {scope!r}.")
            return {"status": "error", "run_dir": None, "rows": [], "fresh": {}}
    run_dir = _mp_run_dir(out, now)
    try:                              # before anything is recorded: a bad --out must not surface after an --apply
        os.makedirs(run_dir, exist_ok=True)
    except OSError as e:
        print(f"❌ map_photos: cannot create the output directory {run_dir} ({_gp_line(e)}).")
        return {"status": "error", "run_dir": None, "rows": [], "fresh": {}}
    if devices_only:
        return _mp_devices_only(library, scope, ids, account, run_dir)
    cache = {}
    ctx = _mp_compute(library, scope, ids, account, resolutions, now, cache)
    applied = preview = None
    unread = bool(apply and ctx["resolution"] and ctx["resolution"]["fatal"])
    if unread:                        # a mistyped --resolutions path must not turn into a half-intended apply
        ctx["notes"].append("❌ --apply was NOT run: the --resolutions file could not be read — nothing was "
                            "recorded; fix the path and re-run")
    elif apply:
        applied, ctx = _mp_apply(ctx, repairs, accept_probable,
                                 lambda: _mp_compute(load_library(), scope, ids, account, resolutions, now, cache))
    else:
        preview = _mp_preview(ctx, repairs, accept_probable)
    ctx.update(apply=apply and not unread, repairs=tuple(repairs), accept_probable=accept_probable, applied=applied,
               preview=preview)
    try:
        result = _mp_finish(ctx, run_dir)
    except OSError as e:              # e.g. a report of a re-used --out is open in Excel: one line, never a traceback
        print(f"❌ map_photos: the reports could not be written to {run_dir} ({_gp_line(e)}) — close the file if it "
              f"is open (e.g. in Excel) or use another --out, and re-run"
              + (" (what --apply recorded stays on record; a re-run records nothing twice)." if applied is not None
                 else "."))
        return {"status": "error", "run_dir": run_dir, "rows": ctx["rows"], "fresh": ctx["fresh"]}
    if unread or (applied is not None and applied["error"]):
        result["status"] = "error"
    return result


def _mp_objects(library, ids, account):
    """The uploaded objects in scope (ids None: the whole library) whose ROUTING account is `account` (any when None)
    -> (objects, {(manual_id, label): route})."""
    import mainfetch  # P-4's routing constants and the validated mvconfig overrides — their single source
    overrides = mainfetch._account_overrides()
    prefix_map, default = mainfetch.ID_PREFIX_PROFILE, mainfetch.DEFAULT_PROFILE
    objects, routes = [], {}
    for obj in mvcommon.iter_uploaded_objects(library, ids):
        route = _mp_route(obj, overrides, prefix_map, default)
        if account is None or route["routing"] == account:
            objects.append(obj)
            routes[(obj["manual_id"], obj["label"])] = route
    return objects, routes


def _mp_compute(library, scope, ids, account, resolutions, now, cache):
    """One complete, offline (but for the read-only device check) mapping pass over a loaded library -> ctx: scope,
    account, library, objects, rows (verdicts, reasons, user decisions), fresh, devices, unmatched, resolution,
    todo, notes, run."""
    objects, routes = _mp_objects(library, ids, account)
    states = _mp_inventory_states()
    fresh = _mp_freshness(states, objects, routes, now)
    rows = _mp_match(objects, routes, _mp_index(states), fresh, library)
    devices = _mp_device_check(library, objects, rows, cache)
    unmatched = _mp_unmatched(states, fresh, rows, objects) if scope == "all" and account is None else None
    _mp_reasons(rows)
    decided, hint_problem = _mp_prior_decisions(objects)
    resolution = _mp_resolutions(resolutions, library, objects) if resolutions else None
    for row in rows:
        key = (row["manual_id"], row["label"])
        row["user_decision"] = (resolution["decisions"].get(key) if resolution else None) or decided.get(key)
    run = "map-" + now.strftime("%Y%m%dT%H%M%SZ")
    return {"scope": scope, "account": account, "library": library, "objects": objects, "rows": rows,
            "fresh": fresh, "devices": devices, "unmatched": unmatched, "resolution": resolution,
            "todo": _mp_todo(rows, run), "notes": [hint_problem], "run": run}


def _mp_devices_only(library, scope, ids, account, run_dir):
    """`map_photos --devices-only` (PLAN Step 9 (10), §M2): ONLY the read-only device check of Step 8 for the objects
    in scope — which are still on a phone (awaiting backup, or not freed yet) and which were freed (only the
    `.mvmeta.json` sidecar is left) — printed, plus devices.csv in the run directory. No inventory, no Google
    Photos, no library / sidecar / failed-list write."""
    objects, _routes = _mp_objects(library, ids, account)
    inventory = device_inventory()
    result = classify_device_objects(library, inventory, objects=objects)
    devices = {"complete": result["complete"], "incomplete": result["incomplete_devices"], "counts": result["counts"],
               "devices": inventory.get("devices") or {}, "unconfigured": inventory.get("unconfigured") or []}
    phones = lambda places: "; ".join(f"{p['alias']} ({p['serial']}) {p['path']}" for p in places)
    _mp_write_csv(os.path.join(run_dir, "devices.csv"),
                  ("manual_id", "object", "uploaded_name", "state", "copies", "size_match", "sidecars", "partials"),
                  [(o["manual_id"], o["label"], o["uploaded_name"], o["state"], phones(o["copies"]),
                    ", ".join(str(c["size_match"]) for c in o["copies"]), phones(o["sidecars"]), phones(o["partials"]))
                   for o in result["objects"]])
    lines = [f"=== MAP PHOTOS — device check only (read-only) — scope: {scope}"
             f"{' · account ' + account if account else ''} — {len(objects):,} object(s) ==="]
    lines.extend(_mp_device_lines(devices))
    lines.append(f"Verdicts: {_mp_counts_line(result['counts']) or 'none'}")
    on_phone = [o for o in result["objects"] if o["state"] == "on_device"]
    if on_phone:
        lines.append(f"On a phone ({len(on_phone):,}) — awaiting its backup, or backed up and not freed yet:")
        lines.extend(f"    {o['manual_id']} · {o['label']} — on "
                     + ", ".join(sorted({f"{c['alias']} ({c['serial']})" for c in o["copies"]
                                         if c["size_match"] is not False}))
                     for o in on_phone)
    if result["counts"]["freed"]:
        lines.append(f"Freed from a phone ({result['counts']['freed']:,}) — only the .mvmeta.json sidecar is left; "
                     f"Google Photos must hold them (map_photos tells whether it does) — listed in devices.csv")
    if result["counts"]["unchecked"]:
        lines.append(f"Unchecked ({result['counts']['unchecked']:,}) — a configured phone was not listed: "
                     + ", ".join(f"{a} ({(devices['devices'].get(a) or {}).get('serial')}) {s}"
                                 for a, s in result["incomplete_devices"].items()))
    lines.append(f"Outputs: {run_dir} — summary.txt, devices.csv")
    _mp_write_text(os.path.join(run_dir, "summary.txt"), "\n".join(lines) + "\n")
    print("\n".join(lines))
    return {"status": "ok", "run_dir": run_dir, "devices": devices, "objects": result["objects"]}


# mapping.csv — every object's verdict as a sheet for the 🚦 R1 reader: the wrong-account list (status, account,
# override_check), the near-matches (uploaded_name next to gp_filename) and the duplicates are filters on it. The
# items' photo ids and dedupKeys stay in mapping.jsonl.
_MP_CSV_COLUMNS = ("account", "category", "manual_id", "object", "uploaded_name", "status", "issue", "name_match",
                   "proof", "gp_filename", "taken_utc", "upload_utc", "duplicate", "alternates", "copies",
                   "default_account", "override", "override_check", "learned_home", "device", "partial_set",
                   "user_decision", "note")


def _mp_csv_row(row):
    """One mapping.csv row (_MP_CSV_COLUMNS): gp_filename / taken_utc / upload_utc describe the item the object maps
    to, `alternates` counts its other items (duplicates), `copies` names the other accounts holding the same bytes."""
    item = row["item"] or {}
    return (row["account"], row["category"], row["manual_id"], row["label"], row["uploaded_name"], row["status"],
            row["issue"], row["name_match"], row["proof"], item.get("filename"), _mp_utc(item.get("taken_ms")),
            _mp_utc(item.get("upload_ms")), row["duplicate"], len(row["alternates"]) or None,
            ", ".join(c["account"] for c in row["copies"]) or None, row["default_account"], row["override"],
            row["override_check"], row["learned_home"], (row["device"] or {}).get("state"),
            "yes" if row["partial_set"] else None, row["user_decision"], row["note"])


def _mp_finish(ctx, run_dir):
    """Write the run directory (mapping.jsonl, mapping.csv, inconsistencies.csv, resolutions_todo.csv,
    unmatched_<account>.csv, summary.txt), print the report -> the command's result. Raises OSError when a file
    cannot be written (cmd_map_photos turns it into one line)."""
    rows, todo, unmatched = ctx["rows"], ctx["todo"], ctx["unmatched"]
    files, notes = ["summary.txt", "mapping.jsonl", "mapping.csv", "inconsistencies.csv"], list(ctx["notes"])
    _mp_write_jsonl(os.path.join(run_dir, "mapping.jsonl"), rows)
    _mp_write_csv(os.path.join(run_dir, "mapping.csv"), _MP_CSV_COLUMNS, [_mp_csv_row(r) for r in rows])
    _mp_write_csv(os.path.join(run_dir, "inconsistencies.csv"),
                  ("account", "category", "manual_id", "object", "uploaded_name", "status", "issue", "partial_set",
                   "device", "repair", "note"),
                  [(r["account"], r["category"], r["manual_id"], r["label"], r["uploaded_name"], r["status"],
                    r["issue"] or ("user says not uploaded" if r["user_decision"] == "not_uploaded" else None),
                    "yes" if r["partial_set"] else "", (r["device"] or {}).get("state"), _mp_repair_text(r),
                    r["note"]) for r in rows
                   if r["status"] in _MP_INCONSISTENCY or r["user_decision"] == "not_uploaded"])
    todo_path, note = _mp_write_todo(run_dir, [csv_row for _record, csv_row in todo])
    files.append(os.path.basename(todo_path))
    notes.append(note)
    for acc, found in (unmatched or {}).items():
        files.append(f"unmatched_{acc}.csv")
        _mp_write_csv(os.path.join(run_dir, f"unmatched_{acc}.csv"),
                      ("category", "photo_id", "filename", "taken_utc", "upload_utc", "owner", "note"),
                      [(u["category"], u["item"]["photo_id"], u["item"]["filename"], _mp_utc(u["item"]["taken_ms"]),
                        _mp_utc(u["item"]["upload_ms"]), u["owner"], u["note"]) for u in found])
    lines = _mp_report(dict(ctx, files=files, notes=[n for n in notes if n], run_dir=run_dir))
    _mp_write_text(os.path.join(run_dir, "summary.txt"), "\n".join(lines) + "\n")
    print("\n".join(lines))
    return {"status": "ok", "run_dir": run_dir, "rows": rows, "fresh": ctx["fresh"], "devices": ctx["devices"],
            "unmatched": unmatched, "todo": [record for record, _csv_row in todo], "applied": ctx["applied"],
            "preview": ctx["preview"]}


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
    template — `{tmdb-0000000}` for EVERY category, movies and series/anime
    alike, since MediaVault is TMDB-for-everything and refuses `-tvdbid`
    (IMP-D22). This step does NO TMDB lookup; the braces hold an editable
    placeholder.
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

    # TMDB for every category, movies and series/anime alike. MediaVault is
    # TMDB-for-everything — `-tvdbid` is refused outright (IMP-D22, a different
    # id space) — so offering a `[tvdb-…]` placeholder invited the user to type
    # an id this tool can never resolve. Verified 2026-09-22 against real Plex,
    # Emby and Jellyfin installs: a `[tmdb-…]` token matches TV shows on all
    # three (test rows S2/S5/S7), so there is nothing to gain from a second
    # provider here.
    provider_tag = mvcommon.CANONICAL_TMDB_TOKEN_FMT.format(id="0000000")
    provider_field = "tmdb"

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

    # IMP-D19 Step 7: a library EXTRAS item (the work dict carries the marker;
    # `mid` is the TITLE id, `entry` the title entry). ONE uniform command
    # covers every reclaimable extras state: re-running add_extras re-scans
    # (idempotent no-op on unchanged bytes), pushes what is not yet uploaded,
    # and (re-)dummies uploaded/restored items. (`replace <title_id>` would
    # no-op on a season_map title, so it is NOT suggested here.)
    group_rel = item.get("extras_group_rel")
    if group_rel is not None and badge in _RECLAIMABLE_STATUS_BADGE.values():
        group_folder = os.path.join(
            (item.get("entry") or {}).get("folder_path", ""),
            group_rel.replace("/", os.sep))
        return f'python main.py add_extras {mid} "{group_folder}"'

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
    yield exactly ONE row. Library EXTRAS items (IMP-D19, nested on the title
    entry) are classified by their OWN item status with the same badge set —
    never UNPREPPED — and their rows suggest the `add_extras` resume command.

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

    # IMP-D19 Step 7: index library EXTRAS items too, so an extras file on disk
    # is classified by ITS item status instead of mis-flagged UNPREPPED. Extras
    # nest on the TITLE entry (season_map or movie leaf), so this iterates ALL
    # entries for the extras block ONLY — _extras_item_paths dereferences no
    # leaf-only key (season_map carries folder_path per ENTRY_TYPE_KEYS).
    # normpath-lower(title folder/group_rel/sub_rel) -> (title_id, title entry,
    # group_rel, composed path, item).
    extras_paths = {}
    for mid, entry in library.items():
        for group_rel, ex_path, ex_item in _extras_item_paths(entry):
            key = os.path.normpath(ex_path).lower()
            extras_paths[key] = (mid, entry, group_rel, ex_path, ex_item)

    items = []
    seen = set()  # normpath-lower keys already emitted — single anti-double-count source.

    def _add_item(norm_key, mid, entry, badge, path, size, extras_group_rel=None):
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
        if extras_group_rel is not None:
            # Extras marker (IMP-D19): lets suggest_next_command emit the
            # add_extras resume command. Internal only — the emitted row keeps
            # the exact same key set as every other row.
            work["extras_group_rel"] = extras_group_rel
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
                    ex = extras_paths.get(norm_key)
                    if ex is not None:
                        # A library EXTRAS item (IMP-D19 Step 7) — NOT unprepped.
                        # Badge by the ITEM's status: an extras item mirrors a
                        # leaf's status vocabulary (local_ready / onboarded /
                        # archived / restored_local, Card D1) and carries no
                        # "type" key, so the shared classifier applies verbatim
                        # (zero drift from how MAIN content classifies).
                        ex_mid, ex_entry, ex_group, _ex_path, ex_item = ex
                        badge = classify_entry_state(ex_item, on_disk_real)
                        if badge and badge != "ARCHIVED":
                            _add_item(norm_key, ex_mid, ex_entry, badge,
                                      full_path, size, extras_group_rel=ex_group)
                    # Unknown to the library. classify_entry_state(None, ...) is
                    # always "UNPREPPED", but per the State table UNPREPPED is a
                    # RECLAIMABLE row only when the file is real — a dummy/tiny
                    # unknown file occupies no reclaimable space, so skip it.
                    elif on_disk_real:
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

    # ---- PASS 2b: extras items whose file is still real (IMP-D19 Step 7) ----
    # The extras analog of PASS 2: catches reclaimable extras not already
    # emitted from the walk (e.g. a category root absent above). Same status
    # early-out as PASS 2; an absent/mis-composed path fails the stat and is
    # skipped. De-duped via `seen`.
    for norm_key, (ex_mid, ex_entry, ex_group, ex_path, ex_item) in extras_paths.items():
        if norm_key in seen:
            continue
        if ex_item.get("status") not in _RECLAIMABLE_STATUS_BADGE:
            continue
        try:
            size = os.path.getsize(ex_path)
        except OSError:
            continue
        on_disk_real = size >= DUMMY_MAX_BYTES
        badge = classify_entry_state(ex_item, on_disk_real)  # item mirrors leaf statuses
        if badge and badge != "ARCHIVED":
            _add_item(norm_key, ex_mid, ex_entry, badge, ex_path, size,
                      extras_group_rel=ex_group)

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
        # folder -> TMDB-token ancestor, first existing wins); short-circuit to False
        # when the entry has neither a folder_path nor a parent to inherit from, so
        # a folderless leaf never even enters the resolver on a large grid.
        has_anchor = bool(entry.get("folder_path")) or bool(entry.get("parent_id"))
        poster_available = bool(
            has_anchor and resolve_artwork_path(library, mid, kind="poster")
        )
        # backdrop_available: same cheap, LIVE on-disk check via the SAME resolver
        # the /api/media-image route uses, but for the FANART (backdrop) the hover
        # detail-window shows. fanart resolution walks own folder -> season folder ->
        # TMDB-token show folder (it has no per-episode rung), so an episode inherits the
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


def _tmdb_company_names(companies):
    """List of company `name` strings from a TMDB movie `production_companies`
    array (IMP-D22 NFO <studio>), dropping any malformed member. [] for a
    missing/empty/non-list value. Mirrors `_tmdb_genre_names`'s exact shape."""
    out = []
    for c in companies or []:
        if isinstance(c, dict):
            name = c.get("name")
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
    whose basename carries a TMDB provider token — the show folder, i.e. the
    Plex/Emby/Jellyfin convention rename_folder stamps onto a SHOW folder — and
    return that folder's vetted ``<kind>.jpg`` (or None). Stops at / never
    escapes LOCAL_ROOT (the walk halts once it climbs above the media root).
    READ-ONLY.

    Recognition goes through the shared ``mvcommon.has_tmdb_token`` (IMP-U6), so
    every format in the wild is found — ``{tmdb-…}``, ``[tmdb-…]``,
    ``[tmdbid-…]``, ``[tmdbid=…]``, any casing. It used to be a brace-only copy
    of the regex living here, which silently matched nothing on a library already
    migrated to the square form and broke this whole inheritance rung."""
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
        if mvcommon.has_tmdb_token(name or ""):
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
            ``folder_path``) whose name carries a TMDB provider token — the show
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

    # (iii) Walk UP to the nearest TMDB-token show folder and use its <kind>.jpg.
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
        print("  prep_push_rep_enrich [id] [filepath] [SIZE_GB/SIZE_MB/COUNT val] [device <id_or_name>] [rehash] [tempdir <path>] [-tmdbid <id>] [--yes|--no-rename] [--nfo] [--no-web]  — archive then TMDB-enrich; no id -> auto-resolve exactly like enrich_metadata")
        print("  prep_push_rep_season_enrich [id] [folder] [SIZE_GB/SIZE_MB/COUNT val] [episodes <range>] [device <id_or_name>] [rehash] [tempdir <path>] [-tmdbid <id>] [--yes|--no-rename] [--nfo] [--no-web]  — season autopilot, then show-centric enrich")
        print("  fetch_restore [id] [OPT: episodes 1-3] [tempdir <path>] [--fetchExtras]")  # [NEW]
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
        print("  restore [id] [tempdir <path>]")
        print("  restore_group [id] [tempdir <path>]")
        print("  sort")
        print("  fetch [id] [tempdir <path>]")
        print("  recover [id|folder]  (or: recover --scan)")
        print("  rename_folder [id|folder] \"<NewName {tmdb-12345}>\"  — rename a show/season folder + rewrite every descendant folder_path (crash-safe, no rehash)")
        print("  migrate_provider_tokens [id_or_prefix] [--apply] [--library movies|series|anime|others]  — migrate every folder still on an OLD token format ([tmdbid-…], [tmdb-…], [tmdbid=…], wrong casing) to canonical {tmdb-…}, ancestor-aware (dry-run by default; --apply writes a JSON report under migration_reports/)")
        print("  normalize_season_folders [id_or_prefix] [--apply]  — two-phase structural rename: Phase A gives the show folder its TMDB token, Phase B renames each season folder to '<Show Name> Season NN (air year)' with NO id on the season (dry-run by default; --apply writes a JSON report under migration_reports/)")
        print("  add_extras <title_id> \"<folders>\" [--extras-size <v|none>] [device <id>] [no-replace]  — attach extras (Specials/Trailers/BTS) to an existing title")
        print(f"  {MAP_USAGE}  — map the uploaded objects to the Google Photos inventory (dry-run by default; --apply writes)")
        print("  web [--port N] [--host H] [--no-browser] [--demo]  — Launch the local web operations console (Disk Reclaim view); --demo = SAFE build, all actions simulated")
        print("  token create [--label \"X\"] [--ttl 1h|8h|12h|1d|3d|7d|30d|never]  — Mint a web access token (default --ttl 7d)")
        print("  token list                                          — List minted web access tokens")
        print("  token revoke [id]                                   — Revoke a web access token by id")
        sys.exit(1)

    cmd = sys.argv[1]

    if cmd == "prep":
        if len(sys.argv) < 4:
            print("❌ Usage: prep [id] [path] [--extras <folders>] [--extras-size <v>]")
        else:
            _mid = sys.argv[2]
            _rest = sys.argv[3:]
            _fp_parts, _et = [], []
            _pi = 0
            while _pi < len(_rest):
                _a = _rest[_pi]
                if _a in ("--extras", "-extras", "--extras-size", "-extras-size"):
                    _et.append(_a)
                    _pi += 1
                    if _pi < len(_rest):
                        _sv2 = _rest[_pi]
                        _et.append(_sv2)
                        _pi += 1
                        if _a in ("--extras-size", "-extras-size") and _sv2.upper() in ("SIZE_MB", "SIZE_GB", "COUNT") and _pi < len(_rest):
                            _et.append(_rest[_pi])
                            _pi += 1
                else:
                    _fp_parts.append(_a)
                    _pi += 1
            _er = parse_extras_tokens(_et)
            cmd_prep(_mid, " ".join(_fp_parts), extras=_er["folders"] or None, extras_size=_er["extras_size"])

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
        _et_ppr = []

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
            elif arg in ("--extras", "-extras", "--extras-size", "-extras-size"):
                _et_ppr.append(arg)
                i += 1
                if i < len(rest):
                    _sv3 = rest[i]
                    _et_ppr.append(_sv3)
                    i += 1
                    if arg in ("--extras-size", "-extras-size") and _sv3.upper() in ("SIZE_MB", "SIZE_GB", "COUNT") and i < len(rest):
                        _et_ppr.append(rest[i])
                        i += 1
                continue
            filepath_parts.append(arg)
            i += 1

        filepath = " ".join(filepath_parts)
        _er_ppr = parse_extras_tokens(_et_ppr)
        cmd_prep_push_rep(mid, filepath, method, val, device_id=resolve_device(device_arg), eager_rehash=eager, temp_dir=tdir,
                          extras=_er_ppr["folders"] or None, extras_size=_er_ppr["extras_size"])

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
        _et_pprs = []

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
            elif arg in ("--extras", "-extras", "--extras-size", "-extras-size"):
                _et_pprs.append(arg)
                i += 1
                if i < len(args):
                    _sv4 = args[i]
                    _et_pprs.append(_sv4)
                    i += 1
                    if arg in ("--extras-size", "-extras-size") and _sv4.upper() in ("SIZE_MB", "SIZE_GB", "COUNT") and i < len(args):
                        _et_pprs.append(args[i])
                        i += 1
                continue
            folder_parts.append(arg)
            i += 1

        folder_path = " ".join(folder_parts)
        _er_pprs = parse_extras_tokens(_et_pprs)
        cmd_prep_push_rep_season(group_id, folder_path, method, val, ep_range, device_id=resolve_device(device_arg), eager_rehash=eager, temp_dir=tdir,
                                 extras=_er_pprs["folders"] or None, extras_size=_er_pprs["extras_size"])

    elif cmd == "prep_push_rep_enrich":
        # IMP-D22 — same token walk as `prep_push_rep` above (SIZE_*/COUNT, device,
        # rehash, tempdir, --extras/--extras-size), plus the enrich-only flags. The
        # existing block is deliberately untouched: this is a separate sibling.
        if len(sys.argv) < 4:
            print("❌ Usage: prep_push_rep_enrich [id] [filepath] [optional: SIZE_MB/COUNT val] [device <id_or_name>] [-tmdbid <id>] [--yes|--no-rename] [--nfo] [--no-web]")
            sys.exit(1)

        mid = sys.argv[2]
        rest = sys.argv[3:]

        method = None
        val = None
        device_arg = None
        eager = False
        tdir = None
        filepath_parts = []
        _et_ppre = []
        cli_tmdb_id = None
        cli_tvdb_id = None
        write_nfo_flag = False   # Decision 4: --nfo is OFF by default.
        no_web_flag = False
        rename_choice = "ask"    # Decision 3: --yes -> "yes", --no-rename -> "no".

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
            elif arg in ("--extras", "-extras", "--extras-size", "-extras-size"):
                _et_ppre.append(arg)
                i += 1
                if i < len(rest):
                    _sv8 = rest[i]
                    _et_ppre.append(_sv8)
                    i += 1
                    if arg in ("--extras-size", "-extras-size") and _sv8.upper() in ("SIZE_MB", "SIZE_GB", "COUNT") and i < len(rest):
                        _et_ppre.append(rest[i])
                        i += 1
                continue
            elif arg in ("-tmdbid", "--tmdbid"):
                if i + 1 < len(rest):
                    cli_tmdb_id = rest[i + 1]
                    i += 2
                    continue
            elif arg in ("-tvdbid", "--tvdbid"):
                # Decision 1: PARSED here, REFUSED at runtime by the command
                # itself (_refuse_tvdbid) — the parser never decides policy.
                if i + 1 < len(rest):
                    cli_tvdb_id = rest[i + 1]
                    i += 2
                    continue
            elif arg == "--yes":
                rename_choice = "yes"
                i += 1
                continue
            elif arg == "--no-rename":
                rename_choice = "no"
                i += 1
                continue
            elif arg == "--nfo":
                write_nfo_flag = True
                i += 1
                continue
            elif arg == "--no-web":
                no_web_flag = True
                i += 1
                continue
            filepath_parts.append(arg)
            i += 1

        filepath = " ".join(filepath_parts)
        _er_ppre = parse_extras_tokens(_et_ppre)
        cmd_prep_push_rep_enrich(mid, filepath, method, val, device_id=resolve_device(device_arg), eager_rehash=eager, temp_dir=tdir,
                                 extras=_er_ppre["folders"] or None, extras_size=_er_ppre["extras_size"],
                                 tmdb_id=cli_tmdb_id, tvdb_id=cli_tvdb_id,
                                 write_nfo=write_nfo_flag, no_web=no_web_flag, rename_choice=rename_choice)

    elif cmd == "prep_push_rep_season_enrich":
        # IMP-D22 — same token walk as `prep_push_rep_season` above (SIZE_*/COUNT,
        # episodes, device, rehash, tempdir, --extras/--extras-size), plus the
        # enrich-only flags. The existing block is deliberately untouched.
        if len(sys.argv) < 4:
            print("❌ Usage: prep_push_rep_season_enrich [id] [folder] [optional: SIZE..] [OPT: episodes <range>] [device <id_or_name>] [-tmdbid <id>] [--yes|--no-rename] [--nfo] [--no-web]")
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
        _et_pprse = []
        cli_tmdb_id = None
        cli_tvdb_id = None
        write_nfo_flag = False   # Decision 4: --nfo is OFF by default.
        no_web_flag = False
        rename_choice = "ask"    # Decision 3: --yes -> "yes", --no-rename -> "no".

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
            elif arg in ("--extras", "-extras", "--extras-size", "-extras-size"):
                _et_pprse.append(arg)
                i += 1
                if i < len(args):
                    _sv9 = args[i]
                    _et_pprse.append(_sv9)
                    i += 1
                    if arg in ("--extras-size", "-extras-size") and _sv9.upper() in ("SIZE_MB", "SIZE_GB", "COUNT") and i < len(args):
                        _et_pprse.append(args[i])
                        i += 1
                continue
            elif arg in ("-tmdbid", "--tmdbid"):
                if i + 1 < len(args):
                    cli_tmdb_id = args[i + 1]
                    i += 2
                    continue
            elif arg in ("-tvdbid", "--tvdbid"):
                # Decision 1: PARSED here, REFUSED at runtime by the command
                # itself (_refuse_tvdbid) — the parser never decides policy.
                if i + 1 < len(args):
                    cli_tvdb_id = args[i + 1]
                    i += 2
                    continue
            elif arg == "--yes":
                rename_choice = "yes"
                i += 1
                continue
            elif arg == "--no-rename":
                rename_choice = "no"
                i += 1
                continue
            elif arg == "--nfo":
                write_nfo_flag = True
                i += 1
                continue
            elif arg == "--no-web":
                no_web_flag = True
                i += 1
                continue
            folder_parts.append(arg)
            i += 1

        folder_path = " ".join(folder_parts)
        _er_pprse = parse_extras_tokens(_et_pprse)
        cmd_prep_push_rep_season_enrich(group_id, folder_path, method, val, ep_range, device_id=resolve_device(device_arg), eager_rehash=eager, temp_dir=tdir,
                                        extras=_er_pprse["folders"] or None, extras_size=_er_pprse["extras_size"],
                                        tmdb_id=cli_tmdb_id, tvdb_id=cli_tvdb_id,
                                        write_nfo=write_nfo_flag, no_web=no_web_flag, rename_choice=rename_choice)

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
        if len(sys.argv) < 4:
            print("❌ Usage: prep_season [base_id] [folder] [--extras <folders>] [--extras-size <v>]")
        else:
            _ps_id = sys.argv[2]
            _ps_rest = sys.argv[3:]
            _ps_fp, _ps_et = [], []
            _ps_i = 0
            while _ps_i < len(_ps_rest):
                _a = _ps_rest[_ps_i]
                if _a in ("--extras", "-extras", "--extras-size", "-extras-size"):
                    _ps_et.append(_a)
                    _ps_i += 1
                    if _ps_i < len(_ps_rest):
                        _sv5 = _ps_rest[_ps_i]
                        _ps_et.append(_sv5)
                        _ps_i += 1
                        if _a in ("--extras-size", "-extras-size") and _sv5.upper() in ("SIZE_MB", "SIZE_GB", "COUNT") and _ps_i < len(_ps_rest):
                            _ps_et.append(_ps_rest[_ps_i])
                            _ps_i += 1
                else:
                    _ps_fp.append(_a)
                    _ps_i += 1
            _er_ps = parse_extras_tokens(_ps_et)
            cmd_prep_season(_ps_id, " ".join(_ps_fp), extras=_er_ps["folders"] or None, extras_size=_er_ps["extras_size"])

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
        args = sys.argv[2:]
        tdir = None
        i = 0
        while i < len(args):
            if args[i] == "tempdir" and i + 1 < len(args):
                tdir = args[i + 1]; i += 2
            else:
                i += 1
        cmd_restore(args[0], temp_dir=tdir) if args else print("❌ Usage: restore [id] [tempdir <path>]")

    elif cmd == "restore_group":
        args = sys.argv[2:]
        tdir = None
        i = 0
        while i < len(args):
            if args[i] == "tempdir" and i + 1 < len(args):
                tdir = args[i + 1]; i += 2
            else:
                i += 1
        cmd_restore_group(args[0], temp_dir=tdir) if args else print("❌ Usage: restore_group [id] [tempdir <path>]")

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
        _et_push = []

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
            elif args[i] in ("--extras", "-extras", "--extras-size", "-extras-size"):
                _et_push.append(args[i])
                i += 1
                if i < len(args):
                    _sv6 = args[i]
                    _et_push.append(_sv6)
                    i += 1
                    if args[i - 2] in ("--extras-size", "-extras-size") and _sv6.upper() in ("SIZE_MB", "SIZE_GB", "COUNT") and i < len(args):
                        _et_push.append(args[i])
                        i += 1
            else:
                i += 1

        _er_push = parse_extras_tokens(_et_push)
        _extras_folders = _er_push["folders"] or None
        _extras_size_val = _er_push["extras_size"]
        _dev_push = resolve_device(dev)
        # IMP-D19 (Card D): on an ALREADY-ARCHIVED main, `push <id> --extras`
        # processes ONLY the extras — the dummied main must not be re-pushed.
        # Handled here (not in cmd_push) so cmd_push stays byte-for-byte intact.
        _skip_main = False
        if _extras_folders:
            _lib_probe = load_library()
            if mid in _lib_probe and _resolve_alias(_lib_probe, mid)[1].get("status") == "archived":
                print(f"ℹ️  {mid} main is archived — pushing extras only (main untouched).")
                _push_title_extras_or_warn(_lib_probe, _extras_title_id(_lib_probe, mid), _extras_folders,
                                            _extras_size_val, _dev_push, method, val)
                _skip_main = True
        if not _skip_main:
            cmd_push(mid, method, val, c_range, device_id=_dev_push, eager_rehash=eager, temp_dir=tdir,
                     extras=_extras_folders, extras_size=_extras_size_val)

    elif cmd == "push_group":
        _pgg_all = sys.argv[2:]
        _et_pgg, _other_pgg = [], []
        _pgg_i = 0
        while _pgg_i < len(_pgg_all):
            _a = _pgg_all[_pgg_i]
            if _a in ("--extras", "-extras", "--extras-size", "-extras-size"):
                _et_pgg.append(_a)
                _pgg_i += 1
                if _pgg_i < len(_pgg_all):
                    _sv7 = _pgg_all[_pgg_i]
                    _et_pgg.append(_sv7)
                    _pgg_i += 1
                    if _a in ("--extras-size", "-extras-size") and _sv7.upper() in ("SIZE_MB", "SIZE_GB", "COUNT") and _pgg_i < len(_pgg_all):
                        _et_pgg.append(_pgg_all[_pgg_i])
                        _pgg_i += 1
            else:
                _other_pgg.append(_a)
                _pgg_i += 1
        group_id, method, val, ep_range, dev, eager, tdir = parse_push_group_args(_other_pgg)
        _er_pgg = parse_extras_tokens(_et_pgg)
        cmd_push_group(group_id, method, val, ep_range, device_id=resolve_device(dev), eager_rehash=eager, temp_dir=tdir,
                       extras=_er_pgg["folders"] or None, extras_size=_er_pgg["extras_size"])

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
        # rename_folder <old_folder_or_id> "<NewName [tmdbid-12345]>"
        if len(sys.argv) >= 4:
            cmd_rename_folder(sys.argv[2], sys.argv[3])
        else:
            print("❌ Usage: rename_folder [id|folder] \"<NewName {tmdb-12345}>\"")

    elif cmd == "migrate_provider_tokens":
        # migrate_provider_tokens [id_or_prefix] [--apply] [--library movies|series|anime|others]
        # DRY-RUN by default; --apply performs the renames (through the existing,
        # crash-safe cmd_rename_folder) and writes a JSON audit report. Pass the
        # positional id/prefix (if any) plus all remaining tokens as flags so
        # cmd_migrate_provider_tokens parses --apply/--library itself.
        rest = sys.argv[2:]
        positional = rest[0] if (rest and not rest[0].startswith("--")) else None
        cmd_migrate_provider_tokens(positional, *rest)

    elif cmd == "normalize_season_folders":
        # normalize_season_folders [id_or_prefix] [--apply]
        # DRY-RUN by default; --apply performs the two-phase structural rename
        # (through the existing, crash-safe cmd_rename_folder) and writes a
        # JSON audit report. Pass the positional id/prefix (if any) plus all
        # remaining tokens as flags so cmd_normalize_season_folders parses
        # --apply itself (mirrors migrate_provider_tokens's own CLI wiring).
        rest = sys.argv[2:]
        positional = rest[0] if (rest and not rest[0].startswith("--")) else None
        cmd_normalize_season_folders(positional, *rest)

    elif cmd == "add_extras":
        # add_extras <title_id> "<folders>" [--extras-size <v|none>] [device <id_or_name>] [no-replace]
        if len(sys.argv) < 4:
            print("❌ Usage: add_extras <title_id> \"<folders>\" [--extras-size <v|none>] [device <id>] [no-replace]")
            sys.exit(1)
        _ae_title_id = sys.argv[2]
        # sys.argv[3] is the semicolon-separated folder list (positional)
        _ae_folders = [f.strip().strip('"').strip("'").strip() for f in sys.argv[3].split(";") if f.strip()]
        _ae_rest = sys.argv[4:]
        # Parse device, no-replace, and extras-size from remaining tokens
        _ae_dev = None
        _ae_no_replace = False
        _ae_et = []
        _ae_i = 0
        while _ae_i < len(_ae_rest):
            _a = _ae_rest[_ae_i]
            if _a == "device" and _ae_i + 1 < len(_ae_rest):
                _ae_dev = _ae_rest[_ae_i + 1]
                _ae_i += 2
            elif _a == "no-replace":
                _ae_no_replace = True
                _ae_i += 1
            elif _a in ("--extras-size", "-extras-size"):
                _ae_et.append(_a)
                _ae_i += 1
                if _ae_i < len(_ae_rest):
                    _sv_ae = _ae_rest[_ae_i]
                    _ae_et.append(_sv_ae)
                    _ae_i += 1
                    if _sv_ae.upper() in ("SIZE_MB", "SIZE_GB", "COUNT") and _ae_i < len(_ae_rest):
                        _ae_et.append(_ae_rest[_ae_i])
                        _ae_i += 1
            else:
                _ae_i += 1
        _er_ae = parse_extras_tokens(_ae_et)
        cmd_add_extras(_ae_title_id, _ae_folders, extras_size=_er_ae["extras_size"],
                       device_id=resolve_device(_ae_dev), no_replace=_ae_no_replace)

    elif cmd == "map_photos":
        # map_photos [all|<id>|<prefix>] [--account a] [--devices-only] [--resolutions <csv>]
        #            [--repair split-info|names] [--accept-probable] [--apply] [--out <dir>]  — DRY-RUN by default
        try:
            _mp_opts = parse_map_photos_args(sys.argv[2:])
        except ValueError as _mp_err:
            print(f"❌ map_photos: {_mp_err}")
            print(f"   Usage: {MAP_USAGE}")
            sys.exit(1)
        if cmd_map_photos(**_mp_opts).get("status") == "error":
            sys.exit(1)

    elif cmd == "fetch":
        if len(sys.argv) < 3:
            print("❌ Usage: fetch [id] [OPT: episodes 1-3] [tempdir <path>] [--fetchExtras]")
            sys.exit(1)

        mid = sys.argv[2]
        _fetch_tokens = sys.argv[3:]
        _fetch_extras = any(t in ("--fetchExtras", "--fetch-extras", "--extras", "--extra") for t in _fetch_tokens)
        # Scan tokens for "episodes <range>" and "tempdir <path>" regardless of position
        epr = None
        ftdir = None
        _ft_i = 0
        while _ft_i < len(_fetch_tokens):
            if _fetch_tokens[_ft_i] == "episodes" and _ft_i + 1 < len(_fetch_tokens):
                epr = _fetch_tokens[_ft_i + 1]
            elif _fetch_tokens[_ft_i] == "tempdir" and _ft_i + 1 < len(_fetch_tokens):
                ftdir = _fetch_tokens[_ft_i + 1]
            _ft_i += 1

        cmd_dispatch_fetch(mid, epr, fetch_extras=_fetch_extras, temp_dir=ftdir)

    elif cmd == "fetch_restore":
        # [NEW] Usage: fetch_restore [id] [OPT: episodes 1-3] [tempdir <path>] [--fetchExtras]
        if len(sys.argv) < 3:
            print("❌ Usage: fetch_restore [id] [OPT: episodes 1-3] [tempdir <path>] [--fetchExtras]")
            sys.exit(1)

        mid = sys.argv[2]
        _fr_tokens = sys.argv[3:]
        _fetch_extras = any(t in ("--fetchExtras", "--fetch-extras", "--extras", "--extra") for t in _fr_tokens)
        # Scan tokens for "episodes <range>" and "tempdir <path>" regardless of position
        epr = None
        frdir = None
        _fr_i = 0
        while _fr_i < len(_fr_tokens):
            if _fr_tokens[_fr_i] == "episodes" and _fr_i + 1 < len(_fr_tokens):
                epr = _fr_tokens[_fr_i + 1]
            elif _fr_tokens[_fr_i] == "tempdir" and _fr_i + 1 < len(_fr_tokens):
                frdir = _fr_tokens[_fr_i + 1]
            _fr_i += 1

        cmd_fetch_restore(mid, epr, fetch_extras=_fetch_extras, temp_dir=frdir)

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