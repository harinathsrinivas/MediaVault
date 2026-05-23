import os
import json
import sys
import hashlib
from pymediainfo import MediaInfo

# --- CONFIGURATION ---
LIBRARY_FILE = 'library.json'


def generate_short_id(long_id):
    """Generates a deterministic 6-char UID from the unique ID string."""
    hash_object = hashlib.md5(long_id.encode())
    return hash_object.hexdigest()[:6]


def calculate_file_hash(filepath, block_size=65536):
    """Calculates SHA256 Hash."""
    print(f"   > Calculating SHA256 for: {os.path.basename(filepath)}")
    print("   > (Please wait, reading file...)")
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
    except OSError:
        print("   > ERROR: MediaInfo DLL not found. Install it or check PATH.")
        return {"error": "MediaInfo missing"}

    specs = {"resolution": "Unknown", "audio": "Unknown", "hdr": "SDR", "size_gb": 0.0}

    for track in media_info.tracks:
        if track.track_type == "Video":
            specs['resolution'] = f"{track.height}p" if track.height else "Unknown"
            if track.hdr_format:
                specs['hdr'] = track.hdr_format
            elif track.commercial_name and "HDR" in track.commercial_name:
                specs['hdr'] = track.commercial_name
        elif track.track_type == "Audio":
            fmt = track.commercial_name if track.commercial_name else track.format
            specs['audio'] = fmt

    specs['size_gb'] = round(os.path.getsize(filepath) / (1024 ** 3), 2)
    return specs


def cmd_prep(code, filepath):
    """
    MANUAL MODE: Generates UID, Hash, and Sidecar files ONLY.
    """
    if not os.path.exists(filepath):
        print(f"Error: File not found: {filepath}")
        return

    print(f"--- PREPPING: {code} ---")

    folder_path = os.path.dirname(filepath)
    filename = os.path.basename(filepath)

    # 1. Generate UID
    uid = generate_short_id(code)
    print(f"1. Generated UID: {uid}")

    # 2. Calculate Hash
    file_hash = calculate_file_hash(filepath)
    print(f"2. File Hash: {file_hash}")

    # 3. Get Tech Specs
    specs = get_tech_specs(filepath)
    print(f"3. Tech Specs: {specs}")

    # 4. Create Sidecar Files
    print("4. Creating sidecar files...")

    # Create 'uid' file
    uid_path = os.path.join(folder_path, "uid")
    with open(uid_path, 'w') as f:
        f.write(uid)

    # Create 'uid.sha256' file
    checksum_path = os.path.join(folder_path, f"{uid}.sha256")
    with open(checksum_path, 'w') as f:
        f.write(f"{file_hash} *{filename}")

    print(f"\nSUCCESS! Folder is ready.")
    print(f"Files created: 'uid' and '{uid}.sha256'")


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage: python main.py prep [id] [filepath]")
        sys.exit(1)

    if sys.argv[1] == "prep":
        # Join all remaining arguments to handle spaces in folder path comfortably
        # This is a hack so you don't ALWAYS need quotes if you forget them, 
        # but quotes are still safer.
        filepath = " ".join(sys.argv[3:])
        # If the user used quotes, the shell handles it, and sys.argv[3] is the whole path.
        # If the user didn't use quotes, sys.argv[3:] catches the broken pieces.
        # However, purely safe way:
        filepath = sys.argv[3]

        cmd_prep(sys.argv[2], filepath)