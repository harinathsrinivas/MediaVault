import os
import json
import sys
import hashlib
import subprocess
import shutil
import time
from datetime import datetime
from pymediainfo import MediaInfo

# --- SELENIUM IMPORTS ---
# We wrap this in a try-block to ensure the user knows if dependencies are missing
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
# Path to your Chrome User Data (Found at chrome://version)
# IMPORTANT: Use double slashes \\ for Windows paths to avoid escape character errors
CHROME_USER_DATA = r"C:\Media\Utils\ChromeProfile"
CHROME_PROFILE_NAME = "Default"  # Usually "Default" or "Profile 1"
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
    """Loads the JSON library file safely."""
    if os.path.exists(LIBRARY_FILE):
        try:
            with open(LIBRARY_FILE, 'r') as f:
                return json.load(f)
        except json.JSONDecodeError:
            return {}
    return {}


def calculate_file_hash(filepath, block_size=65536):
    """Calculates SHA256 hash of a file for integrity verification."""
    print(f"     🔍 Verifying Download: {os.path.basename(filepath)}...", end="", flush=True)
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
def init_driver():
    """
    Initializes the Chrome Driver using the 'Manual Launch' strategy.
    Fixed: Prevents 'loading previous pages' loop by forcing a fresh start.
    """
    print("   > 🤖 Launching Chrome manually on Debugging Port 9222...")

    # 1. Locate Chrome Binary
    CHROME_PATH = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
    if not os.path.exists(CHROME_PATH):
        CHROME_PATH = r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"

    # 2. Command to Launch Chrome
    # FIXED FLAGS: Removed --restore-last-session, added about:blank
    cmd = [
        CHROME_PATH,
        f"--user-data-dir={CHROME_USER_DATA}",
        f"--profile-directory={CHROME_PROFILE_NAME}",
        "--remote-debugging-port=9222",
        "--disable-gpu",
        "--window-size=1920,1080",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-session-crashed-bubble",  # Suppresses the "Restore pages?" popup
        "about:blank"  # Forces Chrome to open a fast, empty white page
    ]

    try:
        subprocess.Popen(cmd)
        print("     Waiting 3 seconds for Chrome to stabilize...")
        time.sleep(3)
    except Exception as e:
        print(f"❌ Failed to launch Chrome binary: {e}")
        return None

    # 3. Connect Selenium
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
    """
    Waits for a file containing 'filename_snippet' to appear in the system Downloads folder.
    Ignores temporary .crdownload files.
    """
    print(f"     ⏳ Waiting for download matching: {filename_snippet}")
    start_time = time.time()

    while time.time() - start_time < timeout:
        # Check if file exists AND .crdownload (temp file) does NOT exist
        # We search for ANY file containing the snippet to handle slight Google naming changes (e.g. 'File (1).mkv')
        for f in os.listdir(SYSTEM_DOWNLOADS_FOLDER):
            if filename_snippet in f and f.endswith(".mkv") and ".crdownload" not in f:
                # Give it a second to finalize close and flush to disk
                time.sleep(2)
                return os.path.join(SYSTEM_DOWNLOADS_FOLDER, f)
        time.sleep(2)
    return None


def automation_download_file(driver, search_queries, filename_expected, dest_folder, target_index=0):
    """
    Tries to find and download a file using a list of search queries.
    Updated: Accepts 'target_index' to select specific results (e.g. 2nd video) for Batch Downloads.
    """
    wait = WebDriverWait(driver, 15)

    for query in search_queries:
        print(f"   > ☁️  Searching Photos for: '{query}' (Target: #{target_index + 1})")

        try:
            # 1. FIX: Navigate to the REAL Google Photos URL
            driver.get("https://photos.google.com")

            # Wait for body to ensure load
            wait.until(EC.presence_of_element_located((By.TAG_NAME, "body")))
            time.sleep(3)

            # 2. Perform Search using '/' Shortcut
            actions = webdriver.ActionChains(driver)
            actions.send_keys("/")
            actions.pause(0.5)
            actions.send_keys(query)
            actions.send_keys(Keys.ENTER)
            actions.perform()

            # Wait for results
            print("     ⏳ Waiting for results...")
            time.sleep(5)

            # --- CLICK LOGIC (GATHER ALL CANDIDATES) ---
            print("     🔍 Scanning for thumbnails...")
            all_thumbnails = []

            # METHOD A: Look for standard photo links (Universal)
            try:
                links = driver.find_elements(By.CSS_SELECTOR, "a[href*='./photo/']")
                for link in links:
                    if link.is_displayed() and link.size['width'] > 50:
                        all_thumbnails.append(link)
            except:
                pass

            # METHOD B: Visual Background Image (Fallback)
            if not all_thumbnails:
                try:
                    candidates = driver.find_elements(By.XPATH, "//div[contains(@style, 'background-image')]")
                    for el in candidates:
                        if el.is_displayed() and el.size['width'] > 50:
                            all_thumbnails.append(el)
                except:
                    pass

            # --- INDEX SELECTION ---
            if len(all_thumbnails) > target_index:
                target_el = all_thumbnails[target_index]
                print(f"     ✅ Found valid thumbnail at index {target_index}. Clicking...")
                driver.execute_script("arguments[0].click();", target_el)
            else:
                print(f"     ⚠️ Not enough results found (Found {len(all_thumbnails)}, needed index {target_index}).")
                continue

            # --- TRIGGER DOWNLOAD ---
            time.sleep(3)  # Wait for video to open
            print("     ⬇️  Triggering Download (Shift+D)...")

            actions = webdriver.ActionChains(driver)
            actions.key_down(Keys.SHIFT).send_keys('d').key_up(Keys.SHIFT).perform()

            # [NEW] STOP PLAYBACK / GO BACK
            # We wait 1 second for the download to register, then hit ESC to close the player
            time.sleep(1)
            print("     🔙 Exiting Video Player (Stopping Playback)...")
            actions.send_keys(Keys.ESCAPE).perform()

            # --- WAIT FOR FILE ---
            # Match first 5 chars
            downloaded_path = wait_for_download(filename_expected[:5])

            # Fallback for "Video.mkv" generic names
            if not downloaded_path:
                downloaded_path = wait_for_download("Video")

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
                print("     ❌ Timeout waiting for file (or Google showed 'Download' dialog).")
                time.sleep(1)

        except Exception as e:
            print(f"     ⚠️ Automation Error with '{query}': {e}")
            continue

    print("❌ Failed to find file with any search term.")
    return False


# ==========================================
#             CORE COMMANDS
# ==========================================

def cmd_fetch(manual_id):
    print(f"--- FETCHING FROM CLOUD: {manual_id} ---")
    library = load_library()
    if manual_id not in library: print("❌ ID not found."); return
    entry = library[manual_id]

    restore_folder = os.path.join(entry["folder_path"], RESTORE_DIR_NAME)

    # 1. Initialize Selenium
    try:
        driver = init_driver()
        if not driver: return
    except Exception as e:
        print(f"❌ Failed to launch Chrome. Is it open? Close it and try again.\nError: {e}")
        return

    # =======================================================
    # STRATEGY A: SPLIT BATCH MODE (Fastest for Split Files)
    # =======================================================
    # If a split file has a 'search_term', we search ONCE and download top N results.
    if entry.get("split_info") and entry["split_info"].get("is_split") and entry.get("search_term"):
        print(f"   > 🚀 SPLIT BATCH MODE: Using tag '{entry['search_term']}' to fetch all chunks.")

        chunks = entry["split_info"]["chunks"]
        total_chunks = len(chunks)
        temp_files = []
        all_download_success = True

        # Step 1: Download N files blindly
        for i in range(total_chunks):
            temp_name = f"temp_download_{i}.mkv"
            print(f"\n   > [Chunk {i + 1}/{total_chunks}] Fetching...")

            # Request the i-th result from the search page
            success = automation_download_file(
                driver,
                [entry['search_term']],
                temp_name,
                restore_folder,
                target_index=i
            )

            if success:
                temp_files.append(os.path.join(restore_folder, temp_name))
            else:
                print("❌ Failed to download a chunk. Batch mode aborted.")
                all_download_success = False
                break

        # Step 2: Identify & Rename by Hash
        if all_download_success:
            print("\n   > 🕵️  Identifying chunks by Hash...")
            matched_count = 0

            for t_path in temp_files:
                file_hash = calculate_file_hash(t_path)
                found_match = False

                for chunk_info in chunks:
                    if chunk_info["hash"] == file_hash:
                        # MATCH FOUND! Rename temp file to real chunk name
                        real_name = chunk_info["filename"]
                        final_path = os.path.join(restore_folder, real_name)

                        if os.path.exists(final_path): os.remove(final_path)
                        os.rename(t_path, final_path)

                        print(f"     ✅ Identified: {real_name}")
                        found_match = True
                        matched_count += 1
                        break

                if not found_match:
                    print(f"     ⚠️ Unknown file (Hash {file_hash[:8]}... not in library). Deleting.")
                    try:
                        os.remove(t_path)
                    except:
                        pass

            if matched_count == total_chunks:
                print("\n✅ All chunks downloaded and identified successfully.")
                print(f"   You can now run: python main.py restore {manual_id}")
                # Cleanup Driver
                try:
                    driver.close(); driver.quit()
                except:
                    pass
                return  # Exit successfully
            else:
                print(f"\n❌ Identification Failed. Falling back to Precision Mode...")

    # =======================================================
    # STRATEGY B: PRECISION MODE (Standard / Fallback)
    # =======================================================
    # Used for single files OR if Batch Mode failed/didn't apply.

    files_to_download = []

    if entry.get("split_info") and entry["split_info"].get("is_split"):
        print(f"   > Entering Precision Mode (Chunk-by-Chunk).")
        for chunk in entry["split_info"]["chunks"]:
            fname = chunk["filename"]
            queries = [fname, os.path.splitext(fname)[0]]
            if entry.get("search_term"): queries.append(entry["search_term"])
            files_to_download.append((fname, queries))
    else:
        print(f"   > Detected Standard Entry.")
        fname = entry["filename"]
        short_id = entry["short_id"]
        queries = []
        if entry.get("search_term"): queries.append(entry["search_term"])
        name_no_ext = os.path.splitext(fname)[0]
        queries.append(f"{name_no_ext} [{short_id}].mkv")
        queries.append(fname)
        queries = list(dict.fromkeys(queries))
        files_to_download.append((fname, queries))

    # Download Loop
    all_success = True
    try:
        for fname, queries in files_to_download:
            if os.path.exists(os.path.join(restore_folder, fname)):
                print(f"   > ⏭️  Skipping {fname} (Already in restore folder)")
                continue

            # Always target index 0 for precision searches
            success = automation_download_file(driver, queries, fname, restore_folder, target_index=0)

            if success:
                target_path = os.path.join(restore_folder, fname)
                expected_hash = None
                if entry.get("split_info"):
                    for c in entry["split_info"]["chunks"]:
                        if c["filename"] == fname: expected_hash = c["hash"]
                else:
                    expected_hash = entry["hash"]

                if expected_hash:
                    if calculate_file_hash(target_path) == expected_hash:
                        print("     ✅ Hash Verified.")
                    else:
                        print("     ❌ Hash Mismatch!")
                        all_success = False
            else:
                all_success = False
                break

            time.sleep(2)

    finally:
        print("   > Closing Browser Session...")
        if driver:
            try:
                driver.close()
                driver.quit()
            except:
                pass

    if all_success:
        print("\n✅ All files fetched and verified successfully.")
        print(f"   You can now run: python main.py restore {manual_id}")
    else:
        print("\n❌ Fetch incomplete.")


# ==========================================
#               MAIN EXECUTION
# ==========================================
if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: fetch [id]")
        sys.exit(1)

    cmd = sys.argv[1]

    if cmd == "fetch":
        cmd_fetch(sys.argv[2])