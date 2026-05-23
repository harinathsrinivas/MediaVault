import os
import json
import sys
import hashlib
import subprocess
import shutil
import re
from datetime import datetime
from pymediainfo import MediaInfo

# ==========================================
#               CONFIGURATION
# ==========================================
LIBRARY_FILE = r'C:\Media\library.json'
LOCAL_ROOT = r"C:\Media"  # Your PC Root
REMOTE_ROOT = "/sdcard/Media"  # Your Pixel Root
MKVMERGE_PATH = r"C:\Program Files\MKVToolNix\mkvmerge.exe"

# Folder Naming Conventions
SPLIT_DIR_NAME = "_parts"  # Temp folder for chunks during push
CHECKSUM_DIR_NAME = "checksums"  # Permanent local folder for parity hashes
RESTORE_DIR_NAME = "restore"  # Folder where you dump downloaded files for restore
VIDEO_EXTENSIONS = ('.mkv', '.mp4', '.avi', '.mov')


# ==========================================
#               UTILITIES
# ==========================================
def load_library():
    if os.path.exists(LIBRARY_FILE):
        try:
            with open(LIBRARY_FILE, 'r') as f:
                return json.load(f)
        except json.JSONDecodeError:
            return {}
    return {}


def save_library(data):
    with open(LIBRARY_FILE, 'w') as f: json.dump(data, f, indent=4)


def generate_short_id(long_id):
    # Generates a stable 6-char hash for file naming
    hash_object = hashlib.md5(long_id.encode())
    return hash_object.hexdigest()[:6]


def calculate_file_hash(filepath, block_size=65536):
    print(f"   > 🔍 Hashing: {os.path.basename(filepath)}...", end="", flush=True)
    sha256 = hashlib.sha256()
    try:
        with open(filepath, 'rb') as f:
            for block in iter(lambda: f.read(block_size), b''): sha256.update(block)
        h = sha256.hexdigest()
        print(" Done.")
        return h
    except FileNotFoundError:
        print(" ❌ File not found.")
        return None
    except Exception as e:
        print(f" ❌ Error: {e}")
        return None


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
def split_video_file(input_path, output_dir, method, value_str):
    import math  # Needed for ceil calculation

    filename_base = os.path.splitext(os.path.basename(input_path))[0]
    output_pattern = os.path.join(output_dir, f"{filename_base}.chunk.%03d.mkv")

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
        #    e.g. 15GB / 2 = 7.5GB per chunk
        balanced_size_bytes = math.ceil(total_size_bytes / num_chunks)

        # 4. Convert back to MB for mkvmerge command (it handles "M" reliably)
        split_size_mb = int(balanced_size_bytes / (1024 * 1024))

        # Safety: Ensure we don't accidentally round down to 0 for tiny files
        if split_size_mb < 1: split_size_mb = 1

        split_arg = f"{split_size_mb}M"
        print(f"   > ⚖️  Balanced Split: {num_chunks} chunks of ~{split_size_mb}MB each.")

    elif method == "COUNT":
        # Existing logic for fixed count
        parts = int(val)
        if parts <= 0: return []
        total_size_mb = total_size_bytes / (1024 * 1024)
        approx_size_mb = int(total_size_mb / parts)
        split_arg = f"{approx_size_mb}M"
    else:
        return []

    # Command Execution
    cmd = [MKVMERGE_PATH, "-o", output_pattern, "--split", f"size:{split_arg}", input_path]
    try:
        subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL)
        chunks = sorted([os.path.join(output_dir, f) for f in os.listdir(output_dir) if f.endswith(".mkv")])
        print(f"   > Done. Generated {len(chunks)} parts.")
        return chunks
    except subprocess.CalledProcessError as e:
        print(f"❌ Error running mkvmerge: {e}");
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


# ==========================================
#             CORE COMMANDS
# ==========================================

def cmd_prep(manual_id, filepath, parent_id=None):
    filepath = filepath.strip('"').strip("'")
    if not os.path.exists(filepath): print(f"❌ File not found: {filepath}"); return
    filename = os.path.basename(filepath);
    folder_path = os.path.dirname(filepath)

    print(f"--- PREPPING: {manual_id} ---")
    short_id = generate_short_id(manual_id)
    file_hash = calculate_file_hash(filepath)
    if not file_hash: return  # Stop if hashing failed

    tech_specs = get_tech_specs(filepath)

    # Create Sidecar Files
    try:
        with open(os.path.join(folder_path, "uid"), 'w') as f:
            f.write(short_id)
        with open(os.path.join(folder_path, f"{short_id}.sha256"), 'w') as f:
            f.write(f"{file_hash} *{filename}")
    except Exception as e:
        print(f"⚠️ Warning: Could not write sidecar files: {e}")

    library = load_library()

    # --- INTELLIGENT PARENT DETECTION ---
    # If no parent_id passed, try to detect from ID pattern (e.g. ...e01)
    if not parent_id:
        match = re.match(r"^(.*)[eE|xX]\d+$", manual_id)
        if match:
            detected_parent = match.group(1)
            print(f"   > 🔗 Auto-Link: Detected Parent '{detected_parent}'")
            parent_id = detected_parent

            # Create Parent Season Map if missing
            if parent_id not in library:
                print(f"   > 🗺️  Creating new Season Map for '{parent_id}'...")
                library[parent_id] = {
                    "type": "season_map",
                    "folder_path": folder_path,  # Best guess
                    "total_episodes": 0,
                    "children": []
                }

            # Add Child to Parent's list
            if manual_id not in library[parent_id]["children"]:
                library[parent_id]["children"].append(manual_id)
                library[parent_id]["children"].sort()
                library[parent_id]["total_episodes"] = len(library[parent_id]["children"])

    # Create Entry
    entry_data = {
        "short_id": short_id,
        "filename": filename,
        "folder_path": folder_path,
        "status": "local_ready",
        "uploaded": False,
        "hash": file_hash,
        "metadata": parse_metadata_from_id(manual_id),
        "tech_spec": tech_specs
    }

    if parent_id:
        entry_data["parent_id"] = parent_id

    library[manual_id] = entry_data
    save_library(library)
    print("✅ Library Entry Created & Linked.\n")
    return manual_id


def cmd_prep_season(base_id, folder_path):
    print(f"=== BATCH PREP SEASON: {base_id} ===")
    folder_path = folder_path.strip('"').strip("'")
    if not os.path.exists(folder_path): print("❌ Folder not found."); return

    files = sorted([f for f in os.listdir(folder_path) if f.lower().endswith(VIDEO_EXTENSIONS)])
    if not files: print("❌ No video files found."); return

    count = 0
    for filename in files:
        # Regex for S01E01 or 1x01
        match = re.search(r"[sS]\d+[eE](\d+)", filename)
        if not match: match = re.search(r"\d+[xX](\d+)", filename)

        if match:
            ep_num = match.group(1)
            full_id = f"{base_id}e{ep_num}"
            full_path = os.path.join(folder_path, filename)
            # Pass base_id explicitly so we don't rely on guessing
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
    if os.path.getsize(file_path) < 1024:
        print("⚠️ Dummy file detected (already archived). Skipping hash check.")
        return

    actual_hash = calculate_file_hash(file_path)
    if actual_hash == entry['hash']:
        print("✅ PASS: Verified.\n")
    else:
        print("❌ FAIL: Hash mismatch!\n")


def cmd_push(manual_id, split_method=None, split_val=None):
    print(f"--- PUSHING: {manual_id} ---")
    library = load_library()
    if manual_id not in library: print(f"❌ ID not found."); return
    entry = library[manual_id]

    # PARENT AWARENESS INFO
    if "parent_id" in entry:
        print(f"   > ℹ️  Part of Season: {entry['parent_id']}")

    local_folder = entry['folder_path']
    filename = entry['filename']
    local_file_path = os.path.join(local_folder, filename)
    parts_dir = os.path.join(local_folder, SPLIT_DIR_NAME)
    checksum_dir = os.path.join(local_folder, CHECKSUM_DIR_NAME)

    if not os.path.exists(local_file_path): print(f"❌ Source file missing."); return

    # Calculate Remote Path
    try:
        rel_path = os.path.relpath(local_folder, LOCAL_ROOT)
    except:
        rel_path = os.path.basename(local_folder)
    remote_target_dir = f"{REMOTE_ROOT}/{rel_path}".replace("\\", "/")

    print(f"   > Target: {remote_target_dir}")
    try:
        subprocess.run(["adb", "shell", "mkdir", "-p", f"'{remote_target_dir}'"], check=True)
    except Exception as e:
        print(f"❌ Error: ADB Connection Failed. {e}");
        return

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

            files_to_upload_paths = split_video_file(local_file_path, parts_dir, split_method, split_val)
            if not files_to_upload_paths: return  # Stop if split failed

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

    # 3. UPLOAD LOOP
    all_success = True
    for f in files_to_upload_paths:
        fname = os.path.basename(f)
        print(f"     -> Pushing: {fname}...", end=" ", flush=True)
        try:
            subprocess.run(["adb", "push", "-p", f, remote_target_dir], check=True)
            print("✅")

            # DELETE LOCAL CHUNK after successful upload
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

        library[manual_id]["uploaded"] = True
        library[manual_id]["status"] = "onboarded"
        save_library(library)
        print("✅ SUCCESS.\n")
    else:
        print("❌ FAILED. Fix connection and re-run to resume.\n")


def cmd_push_group(group_id, split_method=None, split_val=None):
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

    if not target_ids: print("❌ No items found."); return
    print(f"   > Processing {len(target_ids)} items...\n")

    for mid in target_ids:
        if library[mid].get("uploaded") == True:
            print(f"⏭️  Skipping {mid} (Already uploaded)")
            continue
        cmd_push(mid, split_method, split_val)


def cmd_replace(manual_id):
    library = load_library()
    if manual_id not in library: return
    entry = library[manual_id]

    if not entry.get("uploaded", False):
        print(f"⚠️ Skipping {manual_id}: Not marked as uploaded.")
        return

    local_folder = entry['folder_path']
    filename = entry['filename']
    original = os.path.join(local_folder, filename)
    dummy = os.path.join(local_folder, filename + ".temp_dummy")

    # Create Dummy
    try:
        with open(dummy, 'w') as f:
            f.write(f"Original Hash: {entry['hash']}\n")
            if "split_info" in entry: f.write("Status: SPLIT (Check filenames for .chunk.)\n")
    except Exception as e:
        print(f"❌ Error creating dummy: {e}");
        return

    # Swap Files
    if os.path.exists(original):
        try:
            os.remove(original)
        except PermissionError:
            print("❌ Permission Denied deleting file."); return

    os.rename(dummy, original)

    library[manual_id]["status"] = "archived"
    save_library(library)
    print(f"✅ Replaced/Archived: {manual_id}")


def cmd_replace_group(group_id):
    print(f"=== BATCH REPLACE GROUP: {group_id} ===")
    library = load_library()

    target_ids = []
    if group_id in library and library[group_id].get("type") == "season_map":
        target_ids = library[group_id]["children"]
    else:
        target_ids = sorted([k for k in library.keys() if k.startswith(group_id) and k != group_id])

    if not target_ids: print("❌ No items found."); return

    confirm = input(f"⚠️ This will DELETE local files for {len(target_ids)} items. Are you sure? (y/n): ")
    if confirm.lower() == 'y':
        for mid in target_ids: cmd_replace(mid)


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

        # 2. Merge
        if merge_video_files(chunk_paths_in_restore, target_path):
            print(f"   > 💾 Re-indexing Merged File (New Container)...")
            new_hash = calculate_file_hash(target_path)

            # Update Library
            library[manual_id]["hash"] = new_hash
            library[manual_id]["status"] = "restored_local"
            save_library(library)
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
            print("❌ Error: Restore file hash mismatch! Corrupt?");
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

        library[manual_id]["status"] = "restored_local"
        save_library(library)
        print(f"✅ SUCCESS: {filename} restored.")
        return True


def cmd_restore_group(group_id):
    print(f"=== BATCH RESTORE GROUP: {group_id} ===")
    library = load_library()

    target_ids = []
    if group_id in library and library[group_id].get("type") == "season_map":
        target_ids = library[group_id]["children"]
    else:
        target_ids = sorted([k for k in library.keys() if k.startswith(group_id) and k != group_id])

    count = 0
    for mid in target_ids:
        # Loop blindly - the restore command handles checks
        if cmd_restore(mid):
            count += 1

    print(f"\n=== Batch Restore Complete: {count} files restored. ===")


# ==========================================
#               MAIN EXECUTION
# ==========================================
if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage:")
        print("  prep [id] [filepath]")
        print("  prep_season [base_id] [folder]")
        print("  check [id]")
        print("  push [id] [SIZE_GB/SIZE_MB] [val]")
        print("  push_group [id] [SIZE_GB/SIZE_MB] [val]")
        print("  replace [id]")
        print("  replace_group [id]")
        print("  verify_restore [id]")
        print("  restore [id]")
        print("  restore_group [id]")
        sys.exit(1)

    cmd = sys.argv[1]

    if cmd == "prep":
        if len(sys.argv) >= 4:
            cmd_prep(sys.argv[2], " ".join(sys.argv[3:]))
        else:
            print("❌ Usage: prep [id] [path]")

    elif cmd == "prep_season":
        if len(sys.argv) >= 4:
            cmd_prep_season(sys.argv[2], " ".join(sys.argv[3:]))
        else:
            print("❌ Usage: prep_season [base_id] [folder]")

    elif cmd == "check":
        cmd_check(sys.argv[2])

    elif cmd == "replace":
        cmd_replace(sys.argv[2])

    elif cmd == "replace_group":
        cmd_replace_group(sys.argv[2])

    elif cmd == "verify_restore":
        cmd_verify_restore(sys.argv[2])

    elif cmd == "restore":
        cmd_restore(sys.argv[2])

    elif cmd == "restore_group":
        cmd_restore_group(sys.argv[2])

    elif cmd == "push":
        if len(sys.argv) >= 5:
            cmd_push(sys.argv[2], sys.argv[3], sys.argv[4])
        else:
            cmd_push(sys.argv[2])

    elif cmd == "push_group":
        if len(sys.argv) >= 5:
            cmd_push_group(sys.argv[2], sys.argv[3], sys.argv[4])
        else:
            cmd_push_group(sys.argv[2])