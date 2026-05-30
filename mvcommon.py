import os
import json
import sys
import hashlib
import re
import tempfile

# ==========================================
#         SHARED CONFIGURATION
# ==========================================
# Single source of truth for the library path/folder constants imported by
# both main.py and mainfetch.py. Importing only stdlib keeps this module free
# of any import-cycle risk (it must NEVER import main or mainfetch).
LIBRARY_MOVIES = r'C:\Media\library_movies.json'
LIBRARY_SERIES = r'C:\Media\library_series.json'
LIBRARY_ANIME = r'C:\Media\library_anime.json'

LOCAL_ROOT = r"C:\Media"  # Your PC Root
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
    """Loads all three libraries and merges them into one dictionary."""
    data = {}

    for path in [LIBRARY_MOVIES, LIBRARY_SERIES, LIBRARY_ANIME]:
        if os.path.exists(path):
            try:
                with open(path, 'r') as f:
                    data.update(json.load(f))
            except Exception as e:
                print(f"❌ CRITICAL: Library file is corrupt and cannot be loaded: {path}")
                print(f"   Error: {e}")
                print("   Refusing to continue to prevent data loss. Restore the file from backup.")
                sys.exit(1)

    return data


def save_library(data):
    """Splits the merged dictionary back into 3 files based on prefix."""
    mov_data = {}
    tv_data = {}
    ani_data = {}

    for key, val in data.items():
        if key.startswith("mov"):
            mov_data[key] = val
        elif key.startswith("tv"):
            tv_data[key] = val
        elif key.startswith("ani"):
            ani_data[key] = val
        else:
            # Fallback for legacy/unknown keys -> Movies
            mov_data[key] = val

    # Atomic write: write to a temp file then os.replace() to prevent
    # partial-write corruption if the process is killed mid-save.
    for path, content in [(LIBRARY_MOVIES, mov_data), (LIBRARY_SERIES, tv_data), (LIBRARY_ANIME, ani_data)]:
        dir_name = os.path.dirname(path)
        fd, tmp_path = tempfile.mkstemp(dir=dir_name, suffix='.tmp')
        try:
            with os.fdopen(fd, 'w') as tf:
                json.dump(content, tf, indent=4)
            os.replace(tmp_path, path)
        except:
            os.unlink(tmp_path)
            raise


def generate_short_id(long_id):
    # Generates a stable 6-char hash for file naming
    hash_object = hashlib.md5(long_id.encode())
    return hash_object.hexdigest()[:6]


def calculate_file_hash(filepath, block_size=65536):
    try:
        total = os.path.getsize(filepath)
    except OSError:
        print(f"  ❌ File not found: {os.path.basename(filepath)}")
        return None
    sha256 = hashlib.sha256()
    done = 0
    bar_width = 24
    fname = os.path.basename(filepath)
    try:
        with open(filepath, 'rb') as f:
            for block in iter(lambda: f.read(block_size), b''):
                sha256.update(block)
                done += len(block)
                pct = done / total if total else 1.0
                filled = int(bar_width * pct)
                bar = '█' * filled + '░' * (bar_width - filled)
                size_str = f"{human_readable_size(done)} / {human_readable_size(total)}"
                print(f"\r  🔍 {fname}  [{bar}] {size_str} ", end='', flush=True)
        print()
        return sha256.hexdigest()
    except Exception as e:
        print(f"\n  ❌ Error hashing {fname}: {e}")
        return None


def human_readable_size(size_bytes):
    if not size_bytes: return "0 B"
    for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
        if size_bytes < 1024.0:
            return f"{size_bytes:.2f} {unit}"
        size_bytes /= 1024.0
    return f"{size_bytes:.2f} PB"


def parse_size_str(size_str):
    """Parses '40gb', '500mb' into bytes."""
    size_str = size_str.lower().strip()
    match = re.match(r"(\d+(\.\d+)?)\s*([kmgt]?b)", size_str)
    if not match: return None
    val = float(match.group(1))
    unit = match.group(3)

    multipliers = {'b': 1, 'kb': 1024, 'mb': 1024 ** 2, 'gb': 1024 ** 3, 'tb': 1024 ** 4}
    return int(val * multipliers.get(unit, 1))
