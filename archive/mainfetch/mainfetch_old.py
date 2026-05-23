import os
import json
import sys
import hashlib
import subprocess
import shutil
import time
import re
from datetime import datetime
from pymediainfo import MediaInfo

# --- SELENIUM IMPORTS ---
try:
    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options
    from selenium.webdriver.chrome.service import Service
    from selenium.webdriver.common.by import By
    from selenium.webdriver.common.keys import Keys
    from selenium.webdriver.support.ui import WebDriverWait
    from selenium.webdriver.support import expected_conditions as EC
    from webdriver_manager.chrome import ChromeDriverManager
except ImportError:
    print("⚠️ Selenium not found. Install with: pip install selenium webdriver-manager")

# ==========================================
#               CONFIGURATION
# ==========================================
LIBRARY_FILE = r'C:\Media\library.json'
LOCAL_ROOT = r"C:\Media"
REMOTE_ROOT = "/sdcard/Media"
MKVMERGE_PATH = r"C:\Program Files\MKVToolNix\mkvmerge.exe"

# --- AUTOMATION CONFIG ---
# [UPDATED] Profile Paths
CHROME_PROFILE_DEFAULT = r"C:\Media\Utils\ChromeProfile"
CHROME_PROFILE_TV = r"C:\Media\Utils\ChromeProfile_TV"

CHROME_PROFILE_NAME = "Default"
SYSTEM_DOWNLOADS_FOLDER = os.path.join(os.path.expanduser("~"), "Downloads")

# Folder Naming Conventions
SPLIT_DIR_NAME = "_parts"
CHECKSUM_DIR_NAME = "checksums"
RESTORE_DIR_NAME = "restore"
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


def calculate_file_hash(filepath, block_size=65536):
    print(f"     🔍 Verifying: {os.path.basename(filepath)}...", end="", flush=True)
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


# ==========================================
#      AUTOMATION LOGIC (SELENIUM)
# ==========================================
def init_driver(profile_path):
    """Initializes the Chrome Driver with a specific profile."""
    print(f"   > 🤖 Launching Chrome (Debug Port 9222)...")
    print(f"   > Using Profile: {profile_path}")

    CHROME_PATH = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
    if not os.path.exists(CHROME_PATH):
        CHROME_PATH = r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"

    cmd = [
        CHROME_PATH,
        f"--user-data-dir={profile_path}",
        f"--profile-directory={CHROME_PROFILE_NAME}",
        "--remote-debugging-port=9222",
        "--disable-gpu",
        "--window-size=1920,1080",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-session-crashed-bubble",
        "about:blank"
    ]

    try:
        subprocess.Popen(cmd)
        print("     Waiting 3 seconds for Chrome to stabilize...")
        time.sleep(3)
    except Exception as e:
        print(f"❌ Failed to launch Chrome binary: {e}")
        return None

    print("   > 🔗 Attaching Selenium to localhost:9222...")
    options = Options()
    options.add_experimental_option("debuggerAddress", "127.0.0.1:9222")

    try:
        service = Service(ChromeDriverManager().install())
        driver = webdriver.Chrome(service=service, options=options)
        return driver
    except Exception as e:
        print(f"❌ Selenium Connection Error: {e}")
        return None


def wait_for_download(filename_snippet, timeout=300):
    """Waits for a file in Downloads folder."""
    print(f"     ⏳ Waiting for download matching: {filename_snippet}")
    start_time = time.time()

    while time.time() - start_time < timeout:
        for f in os.listdir(SYSTEM_DOWNLOADS_FOLDER):
            if filename_snippet in f and f.endswith(".mkv") and ".crdownload" not in f:
                time.sleep(2)
                return os.path.join(SYSTEM_DOWNLOADS_FOLDER, f)
        time.sleep(2)
    return None


def automation_download_file(driver, search_queries, filename_expected, dest_folder, target_index=0):
    """
    Finds and downloads a file.
    Uses 'target_index' to select the 1st, 2nd, 3rd result etc.
    """
    wait = WebDriverWait(driver, 15)

    for query in search_queries:
        print(f"   > ☁️  Searching Photos for: '{query}' (Target: #{target_index + 1})")

        try:
            driver.get("https://photos.google.com")
            wait.until(EC.presence_of_element_located((By.TAG_NAME, "body")))
            time.sleep(3)

            actions = webdriver.ActionChains(driver)
            actions.send_keys("/")
            actions.pause(0.5)
            actions.send_keys(query)
            actions.send_keys(Keys.ENTER)
            actions.perform()

            print("     ⏳ Waiting for results...")
            time.sleep(5)

            # --- CLICK LOGIC ---
            print("     🔍 Scanning for thumbnails...")
            all_thumbnails = []

            # Method A: Standard Links
            try:
                links = driver.find_elements(By.CSS_SELECTOR, "a[href*='./photo/']")
                for link in links:
                    if link.is_displayed() and link.size['width'] > 50:
                        all_thumbnails.append(link)
            except:
                pass

            # Method B: Background Image Fallback
            if not all_thumbnails:
                try:
                    candidates = driver.find_elements(By.XPATH, "//div[contains(@style, 'background-image')]")
                    for el in candidates:
                        if el.is_displayed() and el.size['width'] > 50:
                            all_thumbnails.append(el)
                except:
                    pass

            # Index Selection
            if len(all_thumbnails) > target_index:
                target_el = all_thumbnails[target_index]
                print(f"     ✅ Found valid thumbnail at index {target_index}. Clicking...")
                driver.execute_script("arguments[0].click();", target_el)
            else:
                print(f"     ⚠️ Not enough results found (Found {len(all_thumbnails)}, needed index {target_index}).")
                continue

            # --- DOWNLOAD ---
            time.sleep(3)
            print("     ⬇️  Triggering Download (Shift+D)...")
            actions = webdriver.ActionChains(driver)
            actions.key_down(Keys.SHIFT).send_keys('d').key_up(Keys.SHIFT).perform()

            # Stop Playback
            time.sleep(1)
            print("     🔙 Exiting Video Player...")
            actions.send_keys(Keys.ESCAPE).perform()

            # Wait for file
            downloaded_path = wait_for_download(filename_expected[:5])
            if not downloaded_path: downloaded_path = wait_for_download("Video")

            if downloaded_path:
                print("     ✅ Download complete.")
                os.makedirs(dest_folder, exist_ok=True)
                final_path = os.path.join(dest_folder, filename_expected)
                if os.path.exists(final_path):
                    try:
                        os.remove(final_path)
                    except:
                        pass
                shutil.move(downloaded_path, final_path)
                print(f"     📦 Moved to: {dest_folder}")
                return True
            else:
                print("     ❌ Timeout waiting for file.")
                time.sleep(1)

        except Exception as e:
            print(f"     ⚠️ Automation Error with '{query}': {e}")
            continue

    return False


# ==========================================
#             CORE LOGIC
# ==========================================

def fetch_single_entry(driver, entry):
    """Handles the fetch logic for a single library entry (Movie or Episode)."""

    print(f"\n🔹 PROCESSING: {entry['filename']} ({entry.get('short_id', 'N/A')})")

    restore_folder = os.path.join(entry["folder_path"], RESTORE_DIR_NAME)
    os.makedirs(restore_folder, exist_ok=True)

    # =======================================================
    # PHASE 1: PRECISION MODE (Specific Filenames)
    # =======================================================
    files_to_download = []

    if entry.get("split_info") and entry["split_info"].get("is_split"):
        print(f"   > Detected Split File ({entry['split_info']['total_chunks']} chunks)")
        for chunk in entry["split_info"]["chunks"]:
            fname = chunk["filename"]
            queries = [fname, os.path.splitext(fname)[0]]
            files_to_download.append((fname, queries))
    else:
        fname = entry["filename"]
        short_id = entry["short_id"]
        queries = []
        if entry.get("search_term"): queries.append(entry["search_term"])
        queries.append(fname)
        queries.append(f"{os.path.splitext(fname)[0]} [{short_id}].mkv")
        files_to_download.append((fname, queries))

    precision_failed = False

    for fname, queries in files_to_download:
        target_path = os.path.join(restore_folder, fname)

        # Check if already done
        if os.path.exists(target_path):
            expected_hash = None
            if entry.get("split_info"):
                for c in entry["split_info"]["chunks"]:
                    if c["filename"] == fname: expected_hash = c["hash"]
            else:
                expected_hash = entry["hash"]

            if expected_hash and calculate_file_hash(target_path) == expected_hash:
                print(f"   > ✅ {fname} verified.")
                continue
            else:
                print(f"   > ⚠️ {fname} invalid/corrupt. Redownloading...")
                try:
                    os.remove(target_path)
                except:
                    pass

        # Attempt Download
        success = automation_download_file(driver, queries, fname, restore_folder, target_index=0)

        # Verify
        verified = False
        if success:
            expected_hash = None
            if entry.get("split_info"):
                for c in entry["split_info"]["chunks"]:
                    if c["filename"] == fname: expected_hash = c["hash"]
            else:
                expected_hash = entry["hash"]

            if expected_hash and calculate_file_hash(target_path) == expected_hash:
                print("     ✅ Hash Verified.")
                verified = True
            else:
                print("     ❌ Hash Mismatch.")

        if not verified:
            precision_failed = True

    # =======================================================
    # PHASE 2: BATCH FALLBACK (Blind Mode)
    # =======================================================
    if precision_failed:
        if entry.get("split_info") and entry["split_info"].get("is_split") and entry.get("search_term"):
            print("   > ⚠️ Precision failed. Trying Fallback Mode...")
            chunks = entry["split_info"]["chunks"]
            total_chunks = len(chunks)

            for i in range(total_chunks):
                temp_name = f"fallback_{i}.mkv"
                success = automation_download_file(
                    driver, [entry['search_term']], temp_name, restore_folder, target_index=i
                )

                if success:
                    t_path = os.path.join(restore_folder, temp_name)
                    f_hash = calculate_file_hash(t_path)

                    matched_name = None
                    for c in chunks:
                        if c["hash"] == f_hash:
                            matched_name = c["filename"]
                            break

                    if matched_name:
                        final_path = os.path.join(restore_folder, matched_name)
                        if os.path.exists(final_path): os.remove(final_path)
                        os.rename(t_path, final_path)
                        print(f"     ✅ Recovered: {matched_name}")
                    else:
                        print("     ⚠️ Unknown file. Deleting.")
                        try:
                            os.remove(t_path)
                        except:
                            pass
        else:
            print("   > ❌ Skipping fallback (No valid search term or not split).")

    return True


def resolve_targets(manual_id, ep_range=None):
    """Resolves a group ID into a list of individual entries."""
    lib = load_library()
    if manual_id not in lib: return []

    entry = lib[manual_id]

    if entry.get("type") == "season_map":
        print(f"   > 📂 Season Map detected. Resolving children...")
        children_ids = entry["children"]

        # Apply Episode Filter
        if ep_range:
            try:
                s, e = map(int, ep_range.split('-'))
                filtered = []
                for child_id in children_ids:
                    # Match 'e01' or 'x01' at end of string
                    m = re.search(r'[eE](\d+)$', child_id) or re.search(r'x(\d+)$', child_id)
                    if m and s <= int(m.group(1)) <= e:
                        filtered.append(child_id)
                children_ids = filtered
                print(f"   > 🎯 Filtered to {len(children_ids)} episodes ({ep_range})")
            except:
                print("   > ⚠️ Invalid range format. Processing all.")

        target_entries = []
        for cid in children_ids:
            if cid in lib: target_entries.append(lib[cid])
        return target_entries

    else:
        # Single Movie or Episode
        return [entry]


def cmd_fetch_route(manual_id, ep_range=None):
    print(f"--- FETCH ROUTER: {manual_id} ---")

    # [UPDATED] Profile Selection Logic
    active_profile = CHROME_PROFILE_DEFAULT
    if manual_id.startswith("tv"):
        print("   > 📺 TV Series detected: Using 'ChromeProfile_TV'")
        active_profile = CHROME_PROFILE_TV
    else:
        print("   > 🎬 Movie (or other) detected: Using Default Profile")

    targets = resolve_targets(manual_id, ep_range)
    if not targets:
        print("❌ No valid targets found.")
        return

    print(f"   > 📋 Processing {len(targets)} items...")

    # Init Selenium ONCE for the whole batch
    driver = None
    try:
        # Pass the selected profile
        driver = init_driver(active_profile)
        if not driver: return

        for entry in targets:
            fetch_single_entry(driver, entry)

    except KeyboardInterrupt:
        print("\n🛑 Stopped by user.")
    except Exception as e:
        print(f"\n❌ Critical Error: {e}")
    finally:
        if driver:
            try:
                driver.quit()
            except:
                pass

    print("\n✅ Batch Processing Complete.")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: fetch [id] [episodes] [range]")
        sys.exit(1)

    # Arg parsing: fetch [id] [episodes 1-3]
    mid = sys.argv[2]
    epr = None
    if len(sys.argv) >= 5 and sys.argv[3] == "episodes":
        epr = sys.argv[4]

    cmd_fetch_route(mid, epr)