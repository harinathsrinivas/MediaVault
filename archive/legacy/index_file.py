import sys
import os
import json
import hashlib
import pathlib

# --- CONFIGURATION ---
DB_FILE = "media_library.json"

# We use this to determine the "Relative Path" for cleaner storage
# Adjust this if your root is different (e.g., just "C:\")
LIBRARY_ROOT = r"C:\Media"


def get_file_hash(filepath):
    """Calculates SHA256 hash of a large file efficiently."""
    sha256 = hashlib.sha256()
    try:
        with open(filepath, "rb") as f:
            while True:
                data = f.read(65536)  # Read in 64k chunks
                if not data:
                    break
                sha256.update(data)
        return sha256.hexdigest()
    except FileNotFoundError:
        return None


def load_db():
    if os.path.exists(DB_FILE):
        try:
            with open(DB_FILE, 'r') as f:
                return json.load(f)
        except json.JSONDecodeError:
            return {}
    return {}


def save_db(data):
    with open(DB_FILE, 'w') as f:
        json.dump(data, f, indent=4)


def determine_category(path_obj):
    """Simple logic to categorize based on folder names."""
    parts = path_obj.parts
    if "Movies" in parts:
        return "Movie"
    elif "Series" in parts:
        return "Series"
    else:
        return "Unknown"


def process_file(file_path_str):
    # 1. CLEAN UP PATH
    # Remove quotes if user copied as path
    file_path_str = file_path_str.strip('"').strip("'")
    path_obj = pathlib.Path(file_path_str)

    if not path_obj.exists():
        print(f"❌ Error: File not found: {file_path_str}")
        return

    print(f"📂 Processing: {path_obj.name}")
    print("   ⏳ Calculating Hash (this may take a moment for 4K files)...")

    # 2. GENERATE HASH & UID
    file_hash = get_file_hash(path_obj)
    if not file_hash:
        print("❌ Error reading file.")
        return

    # Create a short unique ID (first 12 chars of hash)
    uid = file_hash[:12]

    # 3. METADATA EXTRACTION
    category = determine_category(path_obj)

    # Try to get relative path (e.g., "Movies\English\Oceans...")
    # If file is not inside LIBRARY_ROOT, store full path
    try:
        relative_path = path_obj.relative_to(LIBRARY_ROOT)
        display_path = str(relative_path)
    except ValueError:
        display_path = str(path_obj)

    # 4. PREPARE ENTRY
    entry = {
        "uid": uid,
        "filename": path_obj.name,
        "category": category,
        "full_path": str(path_obj),
        "rel_path": display_path,
        "checksum_sha256": file_hash,
        "size_bytes": path_obj.stat().st_size,
        "status": "indexed"
    }

    # 5. UPDATE DATABASE
    db = load_db()

    # We use the full path as the key to avoid duplicates
    db[str(path_obj)] = entry
    save_db(db)

    print(f"✅ Success!")
    print(f"   UID: {uid}")
    print(f"   Category: {category}")
    print(f"   Checksum: {file_hash}")
    print(f"   Saved to {DB_FILE}")


if __name__ == "__main__":
    # Check if a file was dragged onto the script or passed as arg
    if len(sys.argv) > 1:
        input_file = sys.argv[1]
        process_file(input_file)
    else:
        # Manual input if run directly
        print("--- Media Indexer ---")
        user_input = input("Paste file path here: ")
        process_file(user_input)