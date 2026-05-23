import json
import os

# ==========================================
#               CONFIGURATION
# ==========================================
ROOT_DIR = r"C:\Media"
SOURCE_FILE = os.path.join(ROOT_DIR, "library.json")

TARGET_MOVIES = os.path.join(ROOT_DIR, "library_movies.json")
TARGET_SERIES = os.path.join(ROOT_DIR, "library_series.json")
TARGET_ANIME = os.path.join(ROOT_DIR, "library_anime.json")


def migrate():
    print("=== 📦 LIBRARY MIGRATION TOOL ===")

    # 1. Load Source
    if not os.path.exists(SOURCE_FILE):
        print(f"❌ Error: Source file not found at {SOURCE_FILE}")
        return

    print(f"📂 Loading source: {SOURCE_FILE}")
    try:
        with open(SOURCE_FILE, 'r') as f:
            data = json.load(f)
    except json.JSONDecodeError:
        print("❌ Error: library.json is corrupt or invalid JSON.")
        return

    print(f"   > Found {len(data)} total entries.")

    # 2. Segregate Data
    movies = {}
    series = {}
    anime = {}

    for key, entry in data.items():
        if key.startswith("tv-"):
            series[key] = entry
        elif key.startswith("ani-"):
            anime[key] = entry
        else:
            # "mov-" and any legacy IDs go to Movies
            movies[key] = entry

    # 3. Write Target Files
    print("\n💾 Saving new files...")

    # Movies
    with open(TARGET_MOVIES, 'w') as f:
        json.dump(movies, f, indent=4)
    print(f"   ✅ Created library_movies.json ({len(movies)} items)")

    # Series
    with open(TARGET_SERIES, 'w') as f:
        json.dump(series, f, indent=4)
    print(f"   ✅ Created library_series.json ({len(series)} items)")

    # Anime
    with open(TARGET_ANIME, 'w') as f:
        json.dump(anime, f, indent=4)
    print(f"   ✅ Created library_anime.json ({len(anime)} items)")

    print("\n✨ Migration Complete. You can now use the updated main.py.")


if __name__ == "__main__":
    migrate()