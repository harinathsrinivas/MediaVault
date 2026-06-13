import os
import sys
import subprocess
import shutil
import time
import re
from datetime import datetime
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
# Shared library I/O + hashing now live in mvcommon.py (the single source of
# truth imported by both entry points). load_library is now the loud/strict
# version (sys.exit(1) on a corrupt library) — mainfetch's old silent-zero-
# entries behavior is intentionally removed.
from mvcommon import RESTORE_DIR_NAME, load_library, calculate_file_hash

# --- AUTOMATION CONFIG ---
CHROME_PROFILES = {
    "default": r"C:\Media\Utils\ChromeProfile",
    "tv": r"C:\Media\Utils\ChromeProfile_TV"
}
CHROME_PROFILE_NAME = "Default"
SYSTEM_DOWNLOADS_FOLDER = os.path.join(os.path.expanduser("~"), "Downloads")


# ==========================================
#      AUTOMATION LOGIC (SELENIUM)
# ==========================================
def init_driver(profile_key="default"):
    """Initializes the Chrome Driver with the selected profile."""

    user_data_dir = CHROME_PROFILES.get(profile_key, CHROME_PROFILES["default"])
    print(f"   > 🤖 Launching Chrome ({profile_key.upper()}) on Debug Port 9222...")
    print(f"   > Profile Path: {user_data_dir}")

    CHROME_PATH = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
    if not os.path.exists(CHROME_PATH):
        CHROME_PATH = r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"

    cmd = [
        CHROME_PATH,
        f"--user-data-dir={user_data_dir}",
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


def trigger_download(driver, query, index=0):
    """
    RAPID MODE: Navigates, Searches, Clicks, Triggers Download, Exits Player.
    Does NOT wait for file to finish. Returns True if trigger sent.
    """
    wait = WebDriverWait(driver, 10)

    print(f"   > ⚡ Triggering: '{query}' (Index: {index})")

    def _attempt():
        """One navigate→search→click→Shift+D→Esc pass. Returns True if the
        trigger was sent, False on a 0-thumbnail miss / index out of range.
        May raise on a Selenium fault (caught/retried by the caller below)."""
        driver.get("https://photos.google.com")
        wait.until(EC.presence_of_element_located((By.TAG_NAME, "body")))
        time.sleep(1.5)

        actions = webdriver.ActionChains(driver)
        actions.send_keys("/")
        actions.pause(0.3)
        actions.send_keys(query)
        actions.send_keys(Keys.ENTER)
        actions.perform()

        # Wait for search results
        time.sleep(3)

        # --- CLICK LOGIC ---
        all_thumbnails = []

        try:
            links = driver.find_elements(By.CSS_SELECTOR, "a[href*='./photo/']")
            for link in links:
                if link.is_displayed() and link.size['width'] > 50:
                    all_thumbnails.append(link)
        except:
            pass

        if not all_thumbnails:
            try:
                candidates = driver.find_elements(By.XPATH, "//div[contains(@style, 'background-image')]")
                for el in candidates:
                    if el.is_displayed() and el.size['width'] > 50:
                        all_thumbnails.append(el)
            except:
                pass

        if len(all_thumbnails) > index:
            target_el = all_thumbnails[index]
            driver.execute_script("arguments[0].click();", target_el)
        else:
            print(f"     ⚠️ Not found (Found {len(all_thumbnails)}).")
            return False

        # --- TRIGGER DOWNLOAD ---
        time.sleep(2)  # Wait for player to open
        print("     ⬇️  Sending Shift+D...")
        actions = webdriver.ActionChains(driver)
        actions.key_down(Keys.SHIFT).send_keys('d').key_up(Keys.SHIFT).perform()

        # --- EXIT PLAYER ---
        time.sleep(1)
        actions.send_keys(Keys.ESCAPE).perform()

        print("     🚀 Triggered.")
        return True

    # [IMP-C2] Retry the whole attempt ONCE after ~5s when the first pass either
    # returns False (0 thumbnails / index out of range) OR raises a Selenium
    # fault. The second pass's result is final; a second-attempt failure/error
    # yields False, preserving today's failure signal exactly (the function
    # still returns True/False with the same meaning). This explicit one-retry
    # block is intentionally NOT routed through mvcommon.retry(), which only
    # treats exceptions (not a False return) as retryable (Resolved Decision 5).
    try:
        first = _attempt()
        if first:
            return True
    except Exception as e:
        print(f"     ⚠️ Error: {e}")

    print("⏳ Retry 2/2 after 5s (no results / error)…")
    time.sleep(5)
    try:
        return _attempt()
    except Exception as e:
        print(f"     ⚠️ Error: {e}")
        return False


def wait_for_download(filename_snippet, timeout=300):
    # Kept for compatibility, but updated logic uses harvester_loop
    return None


def automation_download_file(driver, search_queries, filename_expected, dest_folder, target_index=0):
    # Kept for compatibility
    return False


# ==========================================
#             CORE LOGIC
# ==========================================

def fetch_single_entry(driver, entry):
    """
    Handles the fetch logic for a single library entry (Movie or Episode).
    Refactored to use PARALLEL TRIGGER + HARVESTER for large files.
    """
    print(f"\n🔹 PROCESSING: {entry['filename']} ({entry.get('short_id', 'N/A')})")

    restore_folder = os.path.join(entry["folder_path"], RESTORE_DIR_NAME)
    os.makedirs(restore_folder, exist_ok=True)

    # 1. Build Queue
    queue = []

    # Determine Fallback Search Term
    fallback_term = entry.get("search_term")
    if not fallback_term: fallback_term = entry["filename"]

    if entry.get("split_info") and entry["split_info"].get("is_split"):
        print(f"   > Detected Split File ({entry['split_info']['total_chunks']} chunks)")
        chunks = entry["split_info"]["chunks"]
        for i, chunk in enumerate(chunks):
            fname = chunk["filename"]
            if os.path.exists(os.path.join(restore_folder, fname)):
                # Optional: Verify existing hash here
                continue

            queue.append({
                "filename": fname,
                "hash": chunk["hash"],
                "dest": restore_folder,
                "specific_query": fname,
                "fallback_query": fallback_term,
                "fallback_index": i,
                "status": "pending"
            })
    else:
        fname = entry["filename"]
        if not os.path.exists(os.path.join(restore_folder, fname)):
            queue.append({
                "filename": fname,
                "hash": entry["hash"],
                "dest": restore_folder,
                "specific_query": fname,
                "fallback_query": fallback_term,
                "fallback_index": 0,
                "status": "pending"
            })

    if not queue:
        print("   ✅ All files already exist.")
        return

    # 2. Execute Queue (Smart Retry Loop)
    # Attempt 0: Precision Search
    # Attempt 1: Fallback Search (Blind)

    for attempt in range(2):
        pending = [i for i in queue if i["status"] == "pending"]
        if not pending: break

        print(f"\n   === ATTEMPT {attempt + 1} ({len(pending)} files) ===")

        # Trigger
        for item in pending:
            query = item["specific_query"] if attempt == 0 else item["fallback_query"]
            idx = 0 if attempt == 0 else item["fallback_index"]

            trigger_download(driver, query, idx)
            time.sleep(2)

        # Harvest
        print("   > Watching Downloads (Infinite wait if active)...")
        start_time = time.time()
        base_timeout = 300  # 5 mins initial timeout

        processed_files = set()

        while True:
            # Check Active Downloads
            active_downloads = [f for f in os.listdir(SYSTEM_DOWNLOADS_FOLDER) if f.endswith(".crdownload")]
            is_active = len(active_downloads) > 0

            if time.time() - start_time > base_timeout:
                if is_active:
                    print(f"   ⏳ Timeout reached, but {len(active_downloads)} files downloading. Extending wait...",
                          end="\r")
                    time.sleep(5)
                    continue  # Keep waiting
                else:
                    print("\n   ❌ Timeout (No active downloads).")
                    break  # Stop waiting

            # Check Completion
            if all(i["status"] == "done" for i in queue):
                break

            found_new = False
            for f in os.listdir(SYSTEM_DOWNLOADS_FOLDER):
                if f.endswith(".crdownload") or not (f.endswith(".mkv") or f.endswith(".mp4")): continue

                fpath = os.path.join(SYSTEM_DOWNLOADS_FOLDER, f)
                if fpath in processed_files: continue

                # Stability Check
                try:
                    if os.path.getsize(fpath) == 0: continue
                    time.sleep(0.5)
                except:
                    continue

                print(f"\n   > 🔎 Checking: {f}")
                fhash = calculate_file_hash(fpath)
                processed_files.add(fpath)

                # Match
                matched = next((i for i in queue if i["hash"] == fhash and i["status"] == "pending"), None)
                if matched:
                    dest = os.path.join(matched["dest"], matched["filename"])
                    if os.path.exists(dest): os.remove(dest)
                    shutil.move(fpath, dest)
                    print(f"     ✅ MOVED: {matched['filename']}")
                    matched["status"] = "done"
                    found_new = True
                else:
                    # Duplicate check
                    if any(i["hash"] == fhash for i in queue):
                        print("     ⚠️ Duplicate. Deleting.")
                        try:
                            os.remove(fpath)
                        except:
                            pass

            if not found_new:
                time.sleep(5)

    # Final Report
    if all(i["status"] == "done" for i in queue):
        print("\n   ✅ ENTRY COMPLETE.")
    else:
        print("\n   ❌ ENTRY INCOMPLETE.")


def _resolve_alias(lib, mid):
    """Mirror of main._resolve_alias — single-hop multi_ep_alias resolution."""
    entry = lib.get(mid)
    if entry is None:
        raise KeyError(mid)
    if entry.get("type") == "multi_ep_alias":
        primary_id = entry["alias_of"]
        primary_entry = lib.get(primary_id)
        if primary_entry is None:
            return (mid, entry)
        return (primary_id, primary_entry)
    return (mid, entry)


def resolve_targets(manual_id, ep_range=None):
    """Resolves a group ID into a list of individual entries."""
    lib = load_library()
    if manual_id not in lib: return []

    entry = lib[manual_id]

    if entry.get("type") == "season_map":
        print(f"   > 📂 Season Map detected. Resolving children...")
        children_ids = entry["children"]

        # Apply Episode Filter [UPDATED to handle .5]
        if ep_range:
            try:
                s, e = map(float, ep_range.split('-'))
                filtered = []
                for child_id in children_ids:
                    # Match 'e01' or 'x01' or 'e16.5' at end of string
                    m = re.search(r'[eE](\d+(?:\.\d+)?)$', child_id) or re.search(r'x(\d+(?:\.\d+)?)$', child_id)
                    if not m: m = re.search(r'(\d+(?:\.\d+)?)$', child_id)  # Anime

                    if m and s <= float(m.group(1)) <= e:
                        filtered.append(child_id)
                children_ids = filtered
                print(f"   > 🎯 Filtered to {len(children_ids)} episodes ({ep_range})")
            except:
                print("   > ⚠️ Invalid range format. Processing all.")

        # De-alias: resolve multi_ep_alias children to their primaries, dedup order-preserving.
        seen = set()
        resolved_ids = []
        for cid in children_ids:
            real_id, _ = _resolve_alias(lib, cid)
            if real_id not in seen:
                seen.add(real_id)
                resolved_ids.append(real_id)
        children_ids = resolved_ids

        target_entries = []
        for cid in children_ids:
            if cid in lib: target_entries.append(lib[cid])
        return target_entries

    else:
        # Single Movie or Episode — resolve alias if needed
        real_id, entry = _resolve_alias(lib, manual_id)
        return [entry]


def build_download_queue(entries):
    queue = []

    for entry in entries:
        restore_folder = os.path.join(entry["folder_path"], RESTORE_DIR_NAME)
        os.makedirs(restore_folder, exist_ok=True)

        fallback_term = entry.get("search_term")
        if not fallback_term: fallback_term = entry["filename"]

        if entry.get("split_info") and entry["split_info"].get("is_split"):
            chunks = entry["split_info"]["chunks"]
            for i, chunk in enumerate(chunks):
                fname = chunk["filename"]
                if os.path.exists(os.path.join(restore_folder, fname)): continue

                queue.append({
                    "filename": fname,
                    "hash": chunk["hash"],
                    "dest": restore_folder,
                    "specific_query": fname,
                    "fallback_query": fallback_term,
                    "fallback_index": i,
                    "status": "pending"
                })
        else:
            fname = entry["filename"]
            if os.path.exists(os.path.join(restore_folder, fname)): continue

            queue.append({
                "filename": fname,
                "hash": entry["hash"],
                "dest": restore_folder,
                "specific_query": fname,
                "fallback_query": fallback_term,
                "fallback_index": 0,
                "status": "pending"
            })

    return queue


def cmd_fetch_route(manual_id, ep_range=None):
    print(f"--- FETCH ROUTER: {manual_id} ---")

    # [UPDATED] Profile Selection Logic
    active_profile = "default"
    if manual_id.startswith("tv") or manual_id.startswith("ani"):
        print("   > 📺 TV Series detected: Using 'ChromeProfile_TV'")
        active_profile = "tv"
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


def parse_fetch_args(argv):
    """Pure parser for mainfetch CLI args. Takes full argv list, returns (mid, epr).
    Prints usage and sys.exit(1) on bad invocation — no Selenium/browser side effects."""
    if len(argv) < 3 or argv[1] != "fetch":
        print("Usage: fetch [id] [episodes] [range]")
        sys.exit(1)
    mid = argv[2]
    epr = None
    if len(argv) >= 5 and argv[3] == "episodes":
        epr = argv[4]
    return (mid, epr)


if __name__ == "__main__":
    mid, epr = parse_fetch_args(sys.argv)
    cmd_fetch_route(mid, epr)