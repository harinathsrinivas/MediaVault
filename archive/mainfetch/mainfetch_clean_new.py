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
    This avoids 'DevToolsActivePort' errors by launching Chrome as a subprocess first.
    """
    print("   > 🤖 Launching Chrome manually on Debugging Port 9222...")

    # 1. Locate Chrome Binary
    # We check standard locations to ensure compatibility across different Windows setups
    CHROME_PATH = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
    if not os.path.exists(CHROME_PATH):
        # Fallback for 32-bit or alternative installs
        CHROME_PATH = r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"

    # 2. Command to Launch Chrome with Debugging Port Open
    # We use subprocess to launch it independently of Python.
    # --remote-debugging-port=9222: The key flag that lets Selenium attach later.
    # --user-data-dir: Uses your custom profile to persist login cookies.
    cmd = [
        CHROME_PATH,
        f"--user-data-dir={CHROME_USER_DATA}",
        f"--profile-directory={CHROME_PROFILE_NAME}",
        "--remote-debugging-port=9222",
        "--no-sandbox",
        "--disable-gpu",
        "--window-size=1920,1080",
        "--restore-last-session" # Helps prevent the 'Restore Pages' popup
    ]

    try:
        subprocess.Popen(cmd)
        print("     Waiting 3 seconds for Chrome to stabilize...")
        time.sleep(3)
    except Exception as e:
        print(f"❌ Failed to launch Chrome binary: {e}")
        return None

    # 3. Connect Selenium to the Existing Browser
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


def automation_download_file(driver, search_queries, filename_expected, dest_folder):
    """
    Tries to find and download a file using a list of search queries.
    Returns True if successful, False otherwise.
    """
    # Try each search query in the list until one returns a result
    for query in search_queries:
        print(f"   > ☁️  Searching Photos for: '{query}'")

        # 1. Navigate to Google Photos Search URL
        driver.get("https://photos.google.com/search")
        time.sleep(2)

        try:
            # 2. Perform Search Interaction
            actions = webdriver.ActionChains(driver)
            actions.send_keys("/")  # Press '/' to focus search box (Power user shortcut)
            actions.perform()
            time.sleep(1)
            actions.send_keys(query)
            actions.send_keys(Keys.ENTER)
            actions.perform()
            time.sleep(3)  # Wait for results to load

            # 3. Check for "No results"
            # Google Photos displays this text in the body if nothing matches
            body_text = driver.find_element(By.TAG_NAME, "body").text
            if "No results" in body_text:
                print(f"     ⚠️ No results for '{query}'. Trying next option...")
                continue

            # 4. Click the First Result
            wait = WebDriverWait(driver, 5)
            # This XPath looks for the first clickable item in the grid (usually containing a background image)
            first_result = wait.until(EC.element_to_be_clickable(
                (By.XPATH, "(//div[@role='button']//div[contains(@style, 'background-image')])[1]")))
            first_result.click()
            time.sleep(2)  # Animation wait

            # 5. Trigger Download (Shift + D)
            # We use the keyboard shortcut as it's more reliable than finding the 3-dot menu button
            actions = webdriver.ActionChains(driver)
            actions.key_down(Keys.SHIFT).send_keys('d').key_up(Keys.SHIFT).perform()

            # 6. Wait for file to appear in Downloads
            # We use strict matching on the first 10 chars to ensure we get the right file,
            # ignoring subsequent chars (like ' (1)')
            downloaded_path = wait_for_download(os.path.splitext(filename_expected)[0][:10])

            if downloaded_path:
                print("     ✅ Download complete.")
                # Move to destination
                os.makedirs(dest_folder, exist_ok=True)
                final_path = os.path.join(dest_folder, filename_expected)

                # Remove existing file if any (overwrite)
                if os.path.exists(final_path):
                    try: os.remove(final_path)
                    except: pass

                shutil.move(downloaded_path, final_path)
                print(f"     📦 Moved to: {dest_folder}")
                return True
            else:
                print("     ❌ Timeout waiting for download (or wrong file).")
                driver.back()  # Go back to grid for next attempt
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

    files_to_download = []  # List of tuples: (Filename, SearchQueries[])

    # 1. Determine what files we need (Split vs Standard)
    if entry.get("split_info") and entry["split_info"].get("is_split"):
        print(f"   > Detected Split Entry. Need to fetch {entry['split_info']['total_chunks']} chunks.")
        for chunk in entry["split_info"]["chunks"]:
            fname = chunk["filename"]
            # Split chunks rely on exact filenames as they are system generated (with UIDs)
            queries = [fname, os.path.splitext(fname)[0]]
            files_to_download.append((fname, queries))
    else:
        print(f"   > Detected Standard Entry.")
        fname = entry["filename"]
        short_id = entry["short_id"]

        # --- SMART QUERY GENERATION ---
        queries = []

        # Priority 1: Custom Search Term (Highest Priority)
        # This allows manual overrides for legacy files (e.g. "Fanaa 2006")
        if entry.get("search_term"):
            queries.append(entry["search_term"])

        # Priority 2: Name with UID (e.g., "Movie [a1b2c3].mkv")
        # This is the "Gold Standard" for new uploads
        name_no_ext = os.path.splitext(fname)[0]
        name_with_uid = f"{name_no_ext} [{short_id}].mkv"
        queries.append(name_with_uid)

        # Priority 3: Exact Filename (e.g., "Movie.mkv")
        queries.append(fname)

        # Priority 4: Simplified Name (e.g., "Movie 2006")
        # Fallback for messy filenames
        clean_name = name_no_ext.replace(".", " ").replace("_", " ").replace("-", " ")
        words = clean_name.split()
        simple_name = " ".join(words[:4]) if len(words) > 3 else clean_name
        queries.append(simple_name)

        # Remove duplicates while keeping order
        queries = list(dict.fromkeys(queries))

        files_to_download.append((fname, queries))

    restore_folder = os.path.join(entry["folder_path"], RESTORE_DIR_NAME)

    # 2. Initialize Selenium
    try:
        driver = init_driver()
        if not driver: return
    except Exception as e:
        print(f"❌ Failed to launch Chrome. Is it open? Close it and try again.\nError: {e}")
        return

    # 3. Download Loop
    all_success = True
    try:
        for fname, queries in files_to_download:
            # Check if already exists in restore
            if os.path.exists(os.path.join(restore_folder, fname)):
                print(f"   > ⏭️  Skipping {fname} (Already in restore folder)")
                continue

            success = automation_download_file(driver, queries, fname, restore_folder)

            # [ENHANCEMENT] Auto-Verify Hash Immediately
            # This ensures we don't end up with corrupt/partial downloads
            if success:
                target_path = os.path.join(restore_folder, fname)
                expected_hash = None

                # Retrieve expected hash from library
                if entry.get("split_info") and entry["split_info"].get("is_split"):
                    for c in entry["split_info"]["chunks"]:
                        if c["filename"] == fname: expected_hash = c["hash"]
                else:
                    expected_hash = entry["hash"]

                # Perform Hash Check
                if expected_hash:
                    actual_hash = calculate_file_hash(target_path)
                    if actual_hash == expected_hash:
                        print("     ✅ Hash Verified.")
                    else:
                        print("     ❌ Hash Mismatch! File might be corrupt or incomplete.")
                        all_success = False
            else:
                all_success = False
                break  # Stop if one fails

            time.sleep(2)  # Cooldown between downloads

    finally:
        print("   > Closing Browser Session...")
        # driver.quit() # Optional: Keep open for debugging

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