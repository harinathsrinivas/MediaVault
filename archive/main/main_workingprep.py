import os
import json
import sys
import hashlib
from datetime import datetime
from pymediainfo import MediaInfo

# --- CONFIGURATION ---
LIBRARY_FILE = r'C:\Media\library.json'


def generate_short_id(long_id):
    """Generates a deterministic 6-char UID from the MANUAL ID string."""
    hash_object = hashlib.md5(long_id.encode())
    return hash_object.hexdigest()[:6]


def calculate_file_hash(filepath, block_size=65536):
    """Calculates SHA256 Hash of the file content."""
    print(f"   > Calculating SHA256 for: {os.path.basename(filepath)}")
    print("   > (Reading file...)")
    sha256 = hashlib.sha256()
    try:
        with open(filepath, 'rb') as f:
            for block in iter(lambda: f.read(block_size), b''):
                sha256.update(block)
        return sha256.hexdigest()
    except FileNotFoundError:
        return None


def get_tech_specs(filepath):
    """Extracts resolution, audio, and HDR info using MediaInfo."""
    print(f"   > Scanning Tech Specs...")
    try:
        media_info = MediaInfo.parse(filepath)
    except Exception as e:
        print(f"   > ERROR parsing MediaInfo: {e}")
        return {"resolution": "Unknown", "audio": "Unknown", "hdr": "SDR", "size_bytes": 0}

    specs = {
        "resolution": "Unknown",
        "hdr": "SDR",
        "audio": "Unknown",
        "size_bytes": os.path.getsize(filepath)
    }

    for track in media_info.tracks:
        if track.track_type == "Video":
            if track.height:
                specs['resolution'] = f"{track.height}p"

            if track.hdr_format:
                specs['hdr'] = track.hdr_format
            elif track.commercial_name and "HDR" in track.commercial_name:
                specs['hdr'] = track.commercial_name

        elif track.track_type == "Audio":
            # Grab the commercial name (e.g., "Dolby Digital Plus")
            fmt = track.commercial_name if track.commercial_name else track.format
            # Only set if currently unknown (prioritizes first track)
            if specs['audio'] == "Unknown":
                specs['audio'] = fmt

    return specs


def parse_metadata_from_id(manual_id):
    """Attempts to extract Year/Title from ID (e.g. mov-2004-en-oceans12)."""
    parts = manual_id.split('-')
    meta = {
        "title": manual_id,
        "year": None,
        "genre": [],
        "added_date": datetime.now().strftime("%Y-%m-%d")
    }

    # Basic parsing logic based on your naming convention
    # Assumes format: type-year-lang-name
    if len(parts) >= 4 and parts[1].isdigit():
        meta["year"] = int(parts[1])
        # Join the rest as title (e.g., oceans12)
        meta["title"] = " ".join(parts[3:]).title()

    return meta


def update_library(manual_id, entry_data):
    """Updates the central JSON library file."""
    data = {}

    # Load existing
    if os.path.exists(LIBRARY_FILE):
        try:
            with open(LIBRARY_FILE, 'r') as f:
                data = json.load(f)
        except json.JSONDecodeError:
            data = {}

    # Check if updating or new
    if manual_id in data:
        print(f"   > Updating existing entry: {manual_id}")
    else:
        print(f"   > Creating new entry: {manual_id}")

    # Write data
    data[manual_id] = entry_data

    # Save
    os.makedirs(os.path.dirname(LIBRARY_FILE), exist_ok=True)
    with open(LIBRARY_FILE, 'w') as f:
        json.dump(data, f, indent=4)
    print(f"   > Library saved: {LIBRARY_FILE}")


def cmd_prep(manual_id, filepath):
    # Remove quotes around path if present
    filepath = filepath.strip('"').strip("'")

    if not os.path.exists(filepath):
        print(f"❌ Error: File not found: {filepath}")
        return

    filename = os.path.basename(filepath)
    folder_path = os.path.dirname(filepath)

    print(f"--- PREPPING: {manual_id} ---")

    # 1. Generate Deterministic Short ID (from Manual ID)
    short_id = generate_short_id(manual_id)
    print(f"1. Short ID: {short_id} (derived from {manual_id})")

    # 2. Calculate File Hash
    file_hash = calculate_file_hash(filepath)

    # 3. Get Tech Specs
    tech_specs = get_tech_specs(filepath)

    # 4. Create Sidecar Files
    print("4. Creating sidecar files...")

    # 'uid' file
    uid_path = os.path.join(folder_path, "uid")
    with open(uid_path, 'w') as f:
        f.write(short_id)

    # 'short_id.sha256' file
    checksum_path = os.path.join(folder_path, f"{short_id}.sha256")
    with open(checksum_path, 'w') as f:
        f.write(f"{file_hash} *{filename}")

    # 5. Build JSON Object
    metadata = parse_metadata_from_id(manual_id)

    entry_data = {
        "short_id": short_id,
        "filename": filename,
        "folder_path": folder_path + "\\",  # Ensure trailing slash for Windows consistency
        "status": "local_ready",
        "uploaded": False,
        "hash": file_hash,
        "metadata": metadata,
        "tech_spec": tech_specs
    }

    # 6. Update JSON
    update_library(manual_id, entry_data)
    print("\nSUCCESS! Folder prepped and library updated.")


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage: python main.py prep [manual_id] [filepath]")
        sys.exit(1)

    command = sys.argv[1]

    if command == "prep":
        if len(sys.argv) < 4:
            print("Error: Missing arguments.")
            print("Usage: python main.py prep [manual_id] [filepath]")
        else:
            m_id = sys.argv[2]
            # Join remaining args to handle spaces in path safely
            f_path = " ".join(sys.argv[3:])
            cmd_prep(m_id, f_path)