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
CHROME_USER_DATA = r"C:\Media\Utils\ChromeProfile"
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
def init_driver():
    """Initializes the Chrome Driver (Manual Launch)."""
    print("   > 🤖 Launching Chrome manually on Debugging Port 9222...")

    CHROME_PATH = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
    if not os.path.exists(CHROME_PATH):
        CHROME_PATH = r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"

    cmd = [
        CHROME_PATH,
        f"--user-data-dir={CHROME_USER_DATA}",
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
#             CORE COMMANDS
# ==========================================

def cmd_fetch(manual_id):
    print(f"--- FETCHING FROM CLOUD: {manual_id} ---")
    library = load_library()
    if manual_id not in library: print("❌ ID not found."); return
    entry = library[manual_id]

    restore_folder = os.path.join(entry["folder_path"], RESTORE_DIR_NAME)

    # Initialize Selenium
    driver = None
    try:
        driver = init_driver()
        if not driver: return
    except Exception as e:
        print(f"❌ Failed to launch Chrome: {e}")
        return

    # =======================================================
    # PHASE 1: PRECISION MODE (Try specific filenames first)
    # =======================================================
    print("\n=== PHASE 1: PRECISION MODE (Specific Filenames) ===")

    files_to_download = []

    if entry.get("split_info") and entry["split_info"].get("is_split"):
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
            # Verify existing hash to be sure
            expected_hash = None
            if entry.get("split_info"):
                for c in entry["split_info"]["chunks"]:
                    if c["filename"] == fname: expected_hash = c["hash"]
            else:
                expected_hash = entry["hash"]

            if expected_hash and calculate_file_hash(target_path) == expected_hash:
                print(f"   > ✅ {fname} already exists and verified.")
                continue
            else:
                print(f"   > ⚠️ {fname} exists but is invalid. Redownloading...")
                os.remove(target_path)

        # Attempt Download (Index 0 for precision)
        success = automation_download_file(driver, queries, fname, restore_folder, target_index=0)

        # Verify immediately
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
                print("     ❌ Hash Mismatch after download.")

        if not verified:
            precision_failed = True

    # =======================================================
    # PHASE 2: BATCH FALLBACK (Blind Mode)
    # =======================================================
    # If Precision failed AND we have a search tag, try to fill gaps blindly.

    if precision_failed:
        if entry.get("split_info") and entry["split_info"].get("is_split") and entry.get("search_term"):
            print("\n=== PHASE 2: BATCH FALLBACK (Blind Search) ===")
            print(f"   > Precision search missed some files. Trying tag: '{entry['search_term']}'")

            chunks = entry["split_info"]["chunks"]
            total_chunks = len(chunks)

            # Blindly download top N results into temp files
            for i in range(total_chunks):
                # Check if we already have this specific chunk verified?
                # It's hard to map Index -> Chunk, so we just download all missing slots
                temp_name = f"fallback_{i}.mkv"

                # We download to a temp name first
                success = automation_download_file(
                    driver,
                    [entry['search_term']],
                    temp_name,
                    restore_folder,
                    target_index=i
                )

                if success:
                    t_path = os.path.join(restore_folder, temp_name)
                    f_hash = calculate_file_hash(t_path)

                    # Try to match this temp file to a missing chunk
                    matched_name = None
                    for c in chunks:
                        if c["hash"] == f_hash:
                            matched_name = c["filename"]
                            break

                    if matched_name:
                        final_path = os.path.join(restore_folder, matched_name)
                        if os.path.exists(final_path): os.remove(final_path)
                        os.rename(t_path, final_path)
                        print(f"     ✅  Identified & Recovered: {matched_name}")
                    else:
                        print("     ⚠️ Unknown file (Duplicate or wrong video). Deleting.")
                        try:
                            os.remove(t_path)
                        except:
                            pass

        else:
            print("\n   > ❌ Precision failed and no valid 'search_term' for batch fallback.")

    # Final Check
    print("\n--- FINAL STATUS ---")
    all_good = True
    if entry.get("split_info"):
        for c in entry["split_info"]["chunks"]:
            if not os.path.exists(os.path.join(restore_folder, c["filename"])):
                print(f"   ❌ Missing: {c['filename']}")
                all_good = False
    else:
        if not os.path.exists(os.path.join(restore_folder, entry["filename"])):
            all_good = False

    # Cleanup
    try:
        driver.close(); driver.quit()
    except:
        pass

    if all_good:
        print("\n✅ SUCCESS: All files fetched.")
        print(f"   Run: python main.py restore {manual_id}")
    else:
        print("\n❌ Fetch Incomplete.")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: fetch [id]")
        sys.exit(1)
    if sys.argv[1] == "fetch":
        cmd_fetch(sys.argv[2])