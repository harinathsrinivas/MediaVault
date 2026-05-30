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
from datetime import datetime
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
    human_readable_size, parse_size_str, retry,
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


def merge_video_files(chunk_paths, output_path):
    print(f"   > 🛠️  Merging {len(chunk_paths)} chunks...")
    # Syntax: mkvmerge -o output.mkv chunk1 +chunk2 +chunk3 ...
    cmd = [MKVMERGE_PATH, "-o", output_path]
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
#             CORE COMMANDS
# ==========================================

def cmd_prep(manual_id, filepath, parent_id=None):
    filepath = filepath.strip('"').strip("'")
    if not os.path.exists(filepath): print(f"❌ File not found: {filepath}"); return False

    # [NEW] Load library EARLY to check if we should skip
    library = load_library()

    if manual_id in library:
        entry = library[manual_id]
        if entry.get("uploaded") == True or entry.get("status") == "archived":
            print(f"   ⏭️  Skipping Prep: {manual_id} (Already marked as uploaded/archived).")
            return True

    # Secondary Safety Net: Just in case the JSON is out of sync but the file is clearly a dummy
    if os.path.getsize(filepath) < DUMMY_MAX_BYTES:
        print(f"   ⏭️  Skipping Prep: {manual_id} (Dummy file detected).")
        return True

    filename = os.path.basename(filepath);
    folder_path = os.path.dirname(filepath)

    print(f"--- PREPPING: {manual_id} ---")
    short_id = generate_short_id(manual_id)
    file_hash = calculate_file_hash(filepath)
    if not file_hash: return False  # Stop if hashing failed

    tech_specs = get_tech_specs(filepath)

    # Create Sidecar Files
    try:
        with open(os.path.join(folder_path, "uid"), 'w') as f:
            f.write(short_id)
        with open(os.path.join(folder_path, f"{short_id}.sha256"), 'w') as f:
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
        if parent_id not in library:
            print(f"   > 🗺️  Creating new Season Map for '{parent_id}'...")
            library[parent_id] = {
                "type": "season_map",
                "folder_path": folder_path,
                "total_episodes": 0,
                "children": []
            }

        # Add Child to Parent's list
        if manual_id not in library[parent_id]["children"]:
            library[parent_id]["children"].append(manual_id)
            library[parent_id]["children"].sort()
            library[parent_id]["total_episodes"] = len(library[parent_id]["children"])

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

    library[manual_id] = entry_data
    save_library(library)
    print(f"✅ Library Entry Created & Linked (Search Key: {default_search_term}).\n")
    return True


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

        # Strategy 1: Standard S01E01 (Works for TV and some Anime, handles .5)
        match = re.search(r"[sS]\d+[eE](\d+(?:\.\d+)?)", filename)
        if not match: match = re.search(r"\d+[xX](\d+(?:\.\d+)?)", filename)

        if match:
            ep_num = match.group(1)

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

            cmd_prep(full_id, full_path, parent_id=base_id)
            count += 1
        else:
            print(f"⚠️ Skipping {filename} (No episode number detected)")

    print(f"=== Batch Complete: Processed {count} episodes. ===")


def cmd_check(manual_id):
    print(f"--- CHECKING: {manual_id} ---")
    library = load_library()
    if manual_id not in library: print("❌ ID not found."); return
    entry = library[manual_id]

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


def cmd_push(manual_id, split_method=None, split_val=None, chunk_range=None, device_id=None):
    print(f"--- PUSHING: {manual_id} ---")
    library = load_library()
    if manual_id not in library: print(f"❌ ID not found."); return False
    entry = library[manual_id]

    # PARENT AWARENESS INFO
    if "parent_id" in entry:
        print(f"   > ℹ️  Part of Season: {entry['parent_id']}")

    local_folder = entry['folder_path']
    filename = entry['filename']
    short_id = entry['short_id']  # Needed for tagging
    local_file_path = os.path.join(local_folder, filename)
    parts_dir = os.path.join(local_folder, SPLIT_DIR_NAME)
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
            print(f"   > ✂️ Splitting...")
            os.makedirs(parts_dir, exist_ok=True)
            os.makedirs(checksum_dir, exist_ok=True)

            # [UPDATED] Pass short_id to attach UID to chunk names
            files_to_upload_paths = split_video_file(local_file_path, parts_dir, split_method, split_val,
                                                     file_id=short_id)
            if not files_to_upload_paths: return False  # Stop if split failed

            # Hash Chunks
            for chunk_path in files_to_upload_paths:
                c_name = os.path.basename(chunk_path)
                c_hash = calculate_file_hash(chunk_path)
                chunk_metadata.append({"filename": c_name, "hash": c_hash})
                # Save sidecar
                with open(os.path.join(checksum_dir, f"{c_name}.sha256"), 'w') as f: f.write(f"{c_hash} *{c_name}")

            # Save split info to library IMMEDIATELY
            library[manual_id]["split_info"] = {
                "is_split": True, "method": split_method, "val": split_val,
                "total_chunks": len(files_to_upload_paths), "chunks": chunk_metadata
            }
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

            def _cleanup_and_log(attempt, exc):
                print(f"⏳ Retry {attempt}/3 after {(1, 4, 16)[min(attempt - 1, 2)]}s (ADB push failed)…")
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

            # DELETE LOCAL CHUNK after successful upload+rename.
            # The chunk is "done" only once renamed to its final name.
            # Safety: Only delete if it's inside the SPLIT_DIR_NAME folder
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

        # Only mark as 'onboarded' if we uploaded ALL chunks (no range filter)
        if not chunk_range:
            # Best-effort remote disaster-recovery sidecar. A sidecar miss must
            # NOT fail a fully-successful chunk upload, so its return is ignored.
            write_remote_mvmeta(adb_base, remote_target_dir, manual_id, library[manual_id])
            library[manual_id]["uploaded"] = True
            library[manual_id]["status"] = "onboarded"
            save_library(library)
            print("✅ SUCCESS.\n")
            return True
        else:
            print(f"✅ Partial Upload Complete (Chunks {chunk_range}).\n")
            return True
    else:
        print("❌ FAILED. Fix connection and re-run to resume.\n")
        return False


def cmd_push_group(group_id, split_method=None, split_val=None, episode_range=None, device_id=None):
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
                # Look for e01, e12.5, E01 etc at the end of the ID
                # Or for Anime: ani-series-01, ani-series-16.5
                match = re.search(r'[eE](\d+(?:\.\d+)?)$', mid)  # Standard
                if not match: match = re.search(r'(\d+(?:\.\d+)?)$', mid)  # Anime numbers

                if match:
                    ep_num = float(match.group(1))
                    if start <= ep_num <= end:
                        filtered_ids.append(mid)

            target_ids = filtered_ids

        except ValueError:
            print("❌ Invalid episode range format. Use '1-3'.")
            return

    if not target_ids: print("❌ No items found to push."); return
    print(f"   > Processing {len(target_ids)} items...\n")

    for mid in target_ids:
        if library[mid].get("uploaded") == True:
            print(f"⏭️  Skipping {mid} (Already uploaded)")
            continue
        cmd_push(mid, split_method, split_val, device_id=device_id)


def cmd_replace(manual_id):
    library = load_library()
    if manual_id not in library: return False
    entry = library[manual_id]

    if not entry.get("uploaded", False):
        print(f"⚠️ Skipping {manual_id}: Not marked as uploaded.")
        return False

    local_folder = entry['folder_path']
    filename = entry['filename']
    original = os.path.join(local_folder, filename)

    ext = os.path.splitext(filename)[1]
    tmp_path = original + ".dummy_tmp" + ext
    if not make_video_dummy(tmp_path, ext):
        print(f"❌ replace aborted — could not create video dummy for {filename}")
        return False

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
                os.rename(original, tobedeleted)  # ROLLBACK SEAM: original removed from its path here (atomic commit / point-of-no-return)
                moved = True
                break
            except PermissionError:
                print(f"     ⚠️ File busy or locked. Retrying... ({attempt + 1}/3)")
                time.sleep(1)
            except Exception as e:
                print(f"❌ Error removing file: {e}")
                return False

        if not moved:
            print(f"❌ PERMISSION DENIED: Could not delete {filename}")
            print("   > Close any players/Plex scanning this file and try again.")
            return False

    # Step 3: rename dummy temp -> original (dummy is now live)
    os.rename(tmp_path, original)

    # Step 4: delete the .tobedeleted leftover (non-fatal if it fails)
    if os.path.exists(tobedeleted):
        try:
            os.remove(tobedeleted)
        except Exception as e:
            print(f"     ⚠️ WARNING: Could not remove leftover {os.path.basename(tobedeleted)}: {e}. It will be cleaned on the next replace.")

    library[manual_id]["status"] = "archived"
    save_library(library)
    print(f"✅ Replaced/Archived: {manual_id}")
    return True


def cmd_replace_group(group_id):
    print(f"=== BATCH REPLACE GROUP: {group_id} ===")
    library = load_library()

    target_ids = []
    if group_id in library and library[group_id].get("type") == "season_map":
        target_ids = library[group_id]["children"]
    else:
        target_ids = sorted([k for k in library.keys() if k.startswith(group_id) and k != group_id])

    if not target_ids: print("❌ No items found."); return

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

        os.remove(current_path)
        os.rename(tmp_path, current_path)
        regenerated += 1

    print(f"✅ repair_dummies complete: scanned {scanned}, regenerated {regenerated}, skipped {skipped}, missing {missing}, failed {failed}")


# ==========================================
#             RESTORE COMMANDS
# ==========================================

def cmd_verify_restore(manual_id):
    print(f"--- VERIFYING RESTORE (DRY RUN): {manual_id} ---")
    library = load_library()
    if manual_id not in library: print("❌ ID not found."); return
    entry = library[manual_id]

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
    entry = library[manual_id]

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

        # 2. Merge
        if merge_video_files(chunk_paths_in_restore, target_path):
            print(f"   > 💾 Re-indexing Merged File (New Container)...")
            new_hash = calculate_file_hash(target_path)

            # Update Library
            library[manual_id]["hash"] = new_hash
            library[manual_id]["status"] = "restored_local"
            save_library(library)

            # --- CLEANUP ---
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
                match = re.search(r'[eE](\d+(?:\.\d+)?)$', mid) or re.search(r'x(\d+(?:\.\d+)?)$', mid)
                if not match: match = re.search(r'(\d+(?:\.\d+)?)$', mid)  # Anime numbers

                if match:
                    ep = float(match.group(1))
                    if start <= ep <= end:
                        filtered.append(mid)
            target_ids = filtered
            print(f"   > Filtered to {len(target_ids)} items (Episodes {episode_range}).")
        except:
            print("   ⚠️ Invalid range. Processing all.")

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
        if entry.get("type") == "season_map": continue

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
            if entry.get("type") == "season_map": continue
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


def cmd_prep_push_rep(manual_id, filepath, split_method=None, split_val=None, device_id=None):
    print(f"=== 🚀 AUTO-PILOT: PREP -> PUSH -> REPLACE for {manual_id} ===")

    # 1. PREP
    print("\n>>> STEP 1: PREP")
    if not cmd_prep(manual_id, filepath):
        print("❌ Auto-Pilot Aborted: Prep failed.")
        return

    # 2. PUSH
    print("\n>>> STEP 2: PUSH")
    # We pass None for chunk_range as this atomic command implies full push
    if not cmd_push(manual_id, split_method, split_val, device_id=device_id):
        print("\n⚠️ Auto-Pilot Paused: Push failed.")
        print("   > Reverting temporary files to restore 'Prep' state...")

        # Cleanup logic: Remove _parts folder if it exists
        library = load_library()
        if manual_id in library:
            entry = library[manual_id]
            parts_dir = os.path.join(entry['folder_path'], SPLIT_DIR_NAME)
            if os.path.exists(parts_dir):
                try:
                    shutil.rmtree(parts_dir)
                    print("     ✅ Temp chunks cleaned up.")
                except Exception as e:
                    print(f"     ❌ Could not clean temp chunks: {e}")

        print("   > System is in 'local_ready' state. Fix the issue (e.g. ADB) and run 'push' manually.")
        return

    # 3. REPLACE
    print("\n>>> STEP 3: REPLACE")
    if not cmd_replace(manual_id):
        print("\n⚠️ Auto-Pilot Finished with Warning: Replace failed.")
        print("   > File is uploaded but still takes space locally.")
        print("   > Run 'replace' manually to archive.")
        return

    print("\n✅✅✅ AUTO-PILOT COMPLETE: Movie is safely archived.")


def cmd_prep_push_rep_season(base_id, folder_path, split_method=None, split_val=None, episode_range=None, device_id=None):
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
                # Strip the base_id to leave only the episode string (e.g. "e01" or "22.5")
                ep_str = mid.replace(base_id, "")
                match = re.search(r'^[eExX]?(\d+(?:\.\d+)?)$', ep_str)
                if match:
                    ep_num = float(match.group(1))
                    if start <= ep_num <= end:
                        filtered_ids.append(mid)
            target_ids = filtered_ids
            print(f"   > Filtered to {len(target_ids)} episodes ({episode_range})")
        except ValueError:
            print("❌ Invalid range.")
            return

    # 3. LOOP PROCESS: PUSH -> REPLACE (One by One)
    print(f"\n>>> STEP 2 & 3: SEQUENTIAL PROCESSING ({len(target_ids)} items)")

    for mid in target_ids:
        entry = library[mid]
        if entry.get("uploaded") == True:
            # If already uploaded, just ensure replace runs
            print(f"\n[SKIP PUSH] {mid} is already uploaded. Checking Replace...")
            cmd_replace(mid)
            continue

        print(f"\n---------------------------------------------------")
        print(f"⏩ PROCESSING: {mid}")
        print(f"---------------------------------------------------")

        # Use existing single-item logic (Re-using logic from prep_push_rep)
        path = os.path.join(entry['folder_path'], entry['filename'])

        # We skip calling 'cmd_prep' again because we already did prep_season
        # Just call Push then Replace
        if cmd_push(mid, split_method, split_val, device_id=device_id):
            cmd_replace(mid)
        else:
            print(f"❌ Failed to process {mid}. Stopping Auto-Pilot to prevent mess.")
            break

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
        print("  prep_push_rep [id] [filepath] [optional: SIZE_GB/COUNT val] [device <id_or_name>]")
        print("  prep_push_rep_season [id] [folder] [optional: SIZE..] [OPT: episodes] [device <id_or_name>]")
        print("  fetch_restore [id] [OPT: episodes 1-3]")  # [NEW]
        print("  set_search [id] [term]")
        print("  set_poster [id] [url]")
        print("  set_fanart [id] [url]")
        print("  set_uploaded [id]")
        print("  prep_season [base_id] [folder]")
        print("  scan_unprepped")
        print("  check [id]")
        print("  local_status [opt: limit]")
        print("  push [id] [SIZE_GB/SIZE_MB] [val] [chunks 1-4] [device <id_or_name>]")
        print("  push_group [id] [SIZE_GB/SIZE_MB] [val] [episodes 1-3] [device <id_or_name>]")
        print("  replace [id]")
        print("  replace_group [id]")
        print("  repair_dummies [optional: id_prefix]")
        print("  verify_restore [id]")
        print("  restore [id]")
        print("  restore_group [id]")
        print("  sort")
        print("  fetch [id]")
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
            filepath_parts.append(arg)
            i += 1

        filepath = " ".join(filepath_parts)
        cmd_prep_push_rep(mid, filepath, method, val, device_id=resolve_device(device_arg))

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
            folder_parts.append(arg)
            i += 1

        folder_path = " ".join(folder_parts)
        cmd_prep_push_rep_season(group_id, folder_path, method, val, ep_range, device_id=resolve_device(device_arg))

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
            else:
                i += 1

        cmd_push(mid, method, val, c_range, device_id=resolve_device(dev))

    elif cmd == "push_group":
        args = sys.argv[2:]
        if not args:
            print("❌ Usage: push_group [id] ...")
            sys.exit(1)

        group_id = args[0]
        method = None
        val = None
        ep_range = None
        dev = None

        i = 1
        while i < len(args):
            if args[i] in ["SIZE_MB", "SIZE_GB", "COUNT"]:
                if i + 1 < len(args):
                    method = args[i]
                    val = args[i + 1]
                    i += 2
            elif args[i] == "episodes":
                if i + 1 < len(args):
                    ep_range = args[i + 1]
                    i += 2
            elif args[i] == "device":
                if i + 1 < len(args):
                    dev = args[i + 1]
                    i += 2
            else:
                i += 1

        cmd_push_group(group_id, method, val, ep_range, device_id=resolve_device(dev))

    elif cmd == "sort":
        cmd_sort()

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