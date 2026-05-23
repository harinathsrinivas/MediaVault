import os
import json
import sys
import hashlib
import subprocess
from datetime import datetime
from pymediainfo import MediaInfo

# --- CONFIGURATION ---
LIBRARY_FILE = r'C:\Media\library.json'
LOCAL_ROOT = r"C:\Media"  # Your PC Root
REMOTE_ROOT = "/sdcard/Media"  # Your Pixel Root


# --- UTILS ---
def load_library():
    if os.path.exists(LIBRARY_FILE):
        try:
            with open(LIBRARY_FILE, 'r') as f:
                return json.load(f)
        except json.JSONDecodeError:
            return {}
    return {}


def save_library(data):
    with open(LIBRARY_FILE, 'w') as f:
        json.dump(data, f, indent=4)


def generate_short_id(long_id):
    hash_object = hashlib.md5(long_id.encode())
    return hash_object.hexdigest()[:6]


def calculate_file_hash(filepath, block_size=65536):
    print(f"   > Calculating SHA256 for: {os.path.basename(filepath)}")
    sha256 = hashlib.sha256()
    try:
        with open(filepath, 'rb') as f:
            for block in iter(lambda: f.read(block_size), b''):
                sha256.update(block)
        return sha256.hexdigest()
    except FileNotFoundError:
        return None


def get_tech_specs(filepath):
    print(f"   > Scanning Tech Specs (Deep Scan)...")
    try:
        media_info = MediaInfo.parse(filepath)
    except Exception:
        return {"error": "Could not parse file"}

    specs = {
        "resolution": "Unknown",
        "width_height": "Unknown",
        "video_codec": "Unknown",
        "hdr": "SDR",
        "frame_rate": "Unknown",
        "audio": "Unknown",
        "audio_channels": "Unknown",
        "audio_language": "Unknown",
        "subtitles": [],
        "duration_mins": 0,
        "size_bytes": os.path.getsize(filepath)
    }

    for track in media_info.tracks:
        if track.track_type == "General":
            if track.duration:
                specs['duration_mins'] = int(track.duration / 60000)  # Convert ms to mins

        elif track.track_type == "Video":
            # --- RESOLUTION & DIMENSIONS ---
            width = track.width
            height = track.height
            specs['width_height'] = f"{width}x{height}"

            # Smart Resolution Label (Width-based)
            if width and width >= 3800:
                specs['resolution'] = "2160p"
            elif width and width >= 1900:
                specs['resolution'] = "1080p"
            elif width and width >= 1260:
                specs['resolution'] = "720p"
            else:
                specs['resolution'] = f"{height}p"

            # --- VIDEO DETAILS ---
            if track.format: specs['video_codec'] = track.format  # e.g., HEVC, AVC
            if track.frame_rate: specs['frame_rate'] = track.frame_rate

            # --- HDR LOGIC ---
            if track.hdr_format:
                specs['hdr'] = track.hdr_format
            elif track.commercial_name and "HDR" in track.commercial_name:
                specs['hdr'] = track.commercial_name

        elif track.track_type == "Audio":
            # Only capture the PRIMARY (first) audio track details
            if specs['audio'] == "Unknown":
                # Name (e.g., Dolby Digital Plus)
                specs['audio'] = track.commercial_name if track.commercial_name else track.format
                # Channels (e.g., 6 for 5.1, 8 for 7.1)
                specs['audio_channels'] = track.channel_s
                # Language
                if track.language: specs['audio_language'] = track.language

        elif track.track_type == "Text":
            # Collect ALL subtitle languages
            if track.language and track.language not in specs['subtitles']:
                specs['subtitles'].append(track.language)

    return specs


def parse_metadata_from_id(manual_id):
    """
    Parses metadata strictly from the ID structure: Type-Lang-Year-Title.
    Example: mov-en-2004-oceans12
    """
    # 1. Split the ID into components
    parts = manual_id.split('-')

    # Default metadata structure (fallback values)
    meta = {
        "title": manual_id,  # Fallback to full ID if parsing fails
        "year": None,
        "genre": [],
        "added_date": datetime.now().strftime("%Y-%m-%d")
    }

    # 2. Validation: Ensure we have at least 4 parts (Type, Lang, Year, Title)
    if len(parts) < 4:
        print(f"⚠️ Warning: ID '{manual_id}' is too short. Expected format: type-lang-year-title")
        return meta

    # 3. Extract Year (Strictly at Index 2)
    # structure: [0]mov - [1]en - [2]2004 - [3]title...
    year_part = parts[2]

    if year_part.isdigit() and len(year_part) == 4:
        meta["year"] = int(year_part)
    else:
        print(f"⚠️ Warning: Expected 4-digit year at position 3, but found '{year_part}'.")
        # We continue parsing even if year is bad, to at least get the title

    # 4. Extract Title (Everything after the Year)
    # We join parts[3:] to handle titles that contain hyphens (e.g., mission-impossible)
    raw_title = " ".join(parts[3:])

    if raw_title:
        meta["title"] = raw_title.replace(".", " ").title()  # Clean up dots if present
    else:
        print(f"⚠️ Warning: No title found in ID '{manual_id}'.")

    return meta

# --- COMMANDS ---

def cmd_prep(manual_id, filepath):
    filepath = filepath.strip('"').strip("'")
    if not os.path.exists(filepath):
        print(f"❌ Error: File not found: {filepath}")
        return

    filename = os.path.basename(filepath)
    folder_path = os.path.dirname(filepath)

    print(f"--- PREPPING: {manual_id} ---")

    # 1. IDs and Hash
    short_id = generate_short_id(manual_id)
    print(f"1. Short ID: {short_id}")
    file_hash = calculate_file_hash(filepath)

    # 2. Tech Specs
    tech_specs = get_tech_specs(filepath)

    # 3. Sidecars
    print("3. Creating sidecar files (Local Only)...")
    with open(os.path.join(folder_path, "uid"), 'w') as f: f.write(short_id)
    with open(os.path.join(folder_path, f"{short_id}.sha256"), 'w') as f: f.write(f"{file_hash} *{filename}")

    # 4. Update JSON
    library = load_library()
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
    library[manual_id] = entry_data
    save_library(library)
    print("\n✅ SUCCESS! Library updated.")


def cmd_check(manual_id):
    print(f"--- CHECKING INTEGRITY: {manual_id} ---")
    library = load_library()
    if manual_id not in library:
        print(f"❌ Error: ID '{manual_id}' not found.")
        return
    entry = library[manual_id]
    expected_hash = entry['hash']
    local_folder = entry['folder_path']
    filename = entry['filename']
    file_path = os.path.join(local_folder, filename)

    print(f"   > File: {filename}")
    if not os.path.exists(file_path):
        print("❌ Error: File missing! Cannot verify.")
        return

    if os.path.getsize(file_path) < 1024:
        print("⚠️ Warning: This file seems very small. Is it already a dummy?")

    print("   > Re-calculating hash from disk...")
    actual_hash = calculate_file_hash(file_path)

    if actual_hash == expected_hash:
        print("\n✅ PASS: File integrity verified.")
    else:
        print("\n❌ FAIL: Hashes DO NOT match!")


def cmd_push(manual_id):
    print(f"--- PUSHING: {manual_id} ---")
    library = load_library()
    if manual_id not in library:
        print(f"❌ Error: ID '{manual_id}' not found.")
        return
    entry = library[manual_id]
    local_folder = entry['folder_path']
    filename = entry['filename']
    local_file_path = os.path.join(local_folder, filename)
    if not os.path.exists(local_file_path):
        print(f"❌ Error: Source file missing at {local_file_path}")
        return
    try:
        subprocess.check_output(["adb", "devices"])
    except FileNotFoundError:
        print("❌ Error: ADB not found.")
        return
    try:
        rel_path = os.path.relpath(local_folder, LOCAL_ROOT)
    except ValueError:
        print("⚠️ Warning: Flattening structure.")
        rel_path = os.path.basename(local_folder)
    remote_target_dir = f"{REMOTE_ROOT}/{rel_path}".replace("\\", "/")
    print(f"   > Source: {filename}")
    print(f"   > Target: {remote_target_dir}")
    print("   > Creating remote directory...")
    subprocess.run(["adb", "shell", "mkdir", "-p", f"'{remote_target_dir}'"])
    print("   > 🚀 Starting Upload... (See progress below)")
    try:
        subprocess.run(["adb", "push", "-p", local_file_path, remote_target_dir], check=True)
        print("\n   > Upload Complete.")
        library[manual_id]["uploaded"] = True
        library[manual_id]["status"] = "onboarded"
        save_library(library)
        print("✅ SUCCESS! Movie file transferred.")
    except subprocess.CalledProcessError:
        print("\n❌ FAILED: ADB Push failed.")


def cmd_replace(manual_id):
    print(f"--- REPLACING LOCAL FILE: {manual_id} ---")
    library = load_library()
    if manual_id not in library:
        print(f"❌ Error: ID '{manual_id}' not found.")
        return

    entry = library[manual_id]
    if not entry.get("uploaded", False):
        print("⚠️ WARNING: This file is marked as NOT UPLOADED.")
        confirm = input("Are you sure you want to delete the local copy? (y/n): ")
        if confirm.lower() != 'y':
            print("Operation cancelled.")
            return

    local_folder = entry['folder_path']
    filename = entry['filename']
    original_file_path = os.path.join(local_folder, filename)
    temp_dummy_path = os.path.join(local_folder, filename + ".temp_dummy")

    print(f"   > Preparing dummy data...")
    try:
        with open(temp_dummy_path, 'w') as f:
            f.write(f"--- DUMMY FILE ---\n")
            f.write(f"Original Filename: {filename}\n")
            f.write(f"Original Hash: {entry['hash']}\n")
            f.write(f"Original Size: {entry['tech_spec']['size_bytes']} bytes\n")
            f.write(f"Replaced on: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
            f.write(f"NOTE: This file is a placeholder. The real file is on the Pixel.\n")
    except Exception as e:
        print(f"❌ Error creating temp file: {e}")
        return

    if os.path.exists(original_file_path):
        print(f"   > Deleting original file: {filename}")
        try:
            os.remove(original_file_path)
            print("   > Large file deleted.")
        except Exception as e:
            print(f"❌ Error deleting file: {e}")
            os.remove(temp_dummy_path)
            return
    else:
        print("   > Original file not found. Creating placeholder anyway.")

    try:
        os.rename(temp_dummy_path, original_file_path)
        print(f"   > Renamed placeholder to: {filename}")
    except Exception as e:
        print(f"❌ Error renaming dummy: {e}")
        return

    library[manual_id]["status"] = "archived"
    save_library(library)
    print("\n✅ SUCCESS! File replaced with dummy.")


# --- MAIN ---
if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python main.py [prep|check|push|replace] [id] [filepath]")
        sys.exit(1)
    cmd = sys.argv[1]
    if cmd == "prep":
        if len(sys.argv) < 4:
            print("Error: Missing args for prep.")
        else:
            cmd_prep(sys.argv[2], " ".join(sys.argv[3:]))
    elif cmd == "check":
        cmd_check(sys.argv[2])
    elif cmd == "push":
        cmd_push(sys.argv[2])
    elif cmd == "replace":
        cmd_replace(sys.argv[2])