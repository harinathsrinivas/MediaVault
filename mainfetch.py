import os
import sys
import subprocess
import shutil
import time
import re
import socket
import urllib.parse
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
import mvcommon  # [IMP-C26] runtime config is read module-qualified (mvcommon's RUNTIME CONFIG binding-hazard note)
from mvcommon import RESTORE_DIR_NAME, load_library, calculate_file_hash, fetch_session_lock, episode_num_from_id

# --- AUTOMATION CONFIG ---
CHROME_PROFILES = {
    "movies": r"C:\Media\Utils\ChromeProfile",
    "tv":     r"C:\Media\Utils\ChromeProfile_TV",
    "anime":  r"C:\Media\Utils\ChromeProfile_Anime",
    "others": r"C:\Media\Utils\ChromeProfile_Others",
}
# Ordered id-prefix -> profile map. Most-specific prefix FIRST.
# IMP-A5 will source these two constants from mvconfig.json.
ID_PREFIX_PROFILE = [("ani", "anime"), ("tv", "tv"), ("mov", "movies"), ("oth", "others")]
DEFAULT_PROFILE = "movies"
CHROME_PROFILE_NAME = "Default"
SYSTEM_DOWNLOADS_FOLDER = os.path.join(os.path.expanduser("~"), "Downloads")


# ==========================================
#      AUTOMATION LOGIC (SELENIUM)
# ==========================================
def init_driver(profile_key="movies"):
    """Initializes the Chrome Driver with the selected profile."""

    user_data_dir = CHROME_PROFILES.get(profile_key, CHROME_PROFILES["movies"])
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
    except Exception as e:
        print(f"❌ Selenium Connection Error: {e}")
        return None

    # [IMP-C29] Attaching leaves Selenium on whichever target chromedriver picked.
    # On Chrome 154 that is the Gemini side panel, where every search went nowhere.
    try:
        outcome = use_photos_tab(driver)
    except Exception as e:
        print(f"❌ Could not open a photos.google.com tab in the attached Chrome: {e}")
        try:
            driver.quit()  # attach mode: ends the Selenium session only; Chrome stays open
        except Exception:
            pass
        return None
    print(f"   > 🗂️ {_TAB_NOTE[outcome]}")
    return driver


# The only signed-in host for the Photos web app. Google redirects an expired
# session to accounts.google.com, so a current_url that is not on photos.google.com
# means the profile is logged out.
PHOTOS_URL = "https://photos.google.com"


class SessionExpiredError(Exception):
    pass


def check_session_alive(driver, profile_key=None):
    """Side-effect-free login check: inspect driver.current_url (the CALLER must
    have navigated to PHOTOS_URL already — this does NOT call driver.get).

    Returns True if still on photos.google.com (www./locale subpaths tolerated).
    Raises SessionExpiredError if redirected to accounts.google.com or anywhere
    that is not a photos.google.com host. If reading current_url raises (a genuine
    Selenium fault), returns True so the existing retry/handle paths deal with it —
    the detector must not invent a logged-out failure from a browser glitch.
    """
    try:
        current_url = driver.current_url
    except Exception:
        return True

    host = (urllib.parse.urlparse(current_url).hostname or "").lower()
    if host.endswith("photos.google.com"):
        return True
    if host == "accounts.google.com" or "photos.google.com" not in host:
        raise SessionExpiredError(
            f"profile {profile_key!r} appears logged out (redirected to {host})"
        )
    return True


# [IMP-C29] Chrome 154 lists its Gemini side panel among Selenium's window handles
# (a `webview` on gemini.google.com/glic and an `other` on chrome://glic/), and
# chromedriver attached to that webview, so every fetch search was typed into
# Gemini. Fetch therefore works only in a target that is provably a normal tab:
# CDP type "page", https, on a photos.google.com host. Selenium's window handles
# are the CDP target ids (verified live with chromedriver 154), so a target is
# classified without switching into it.
_PHOTOS_SEARCH_PATH = re.compile(r"^(?:/u/\d+)?/search/")  # where a keystroke search lands
_TAB_NOTE = {"current": "Working in the open photos.google.com tab.",
             "existing": "Switched to the open photos.google.com tab.",
             "new": "Opened a new photos.google.com tab."}


def _is_photos_host(host):
    host = (host or "").lower()
    return host == "photos.google.com" or host.endswith(".photos.google.com")


def _is_photos_page(info):
    """True for the CDP TargetInfo of a normal tab (type "page") showing Google Photos."""
    if not info or info.get("type") != "page":
        return False
    url = urllib.parse.urlparse(info.get("url") or "")
    return url.scheme == "https" and _is_photos_host(url.hostname)


def _target_info(driver):
    """The CDP TargetInfo of the target the driver is in, or None if CDP cannot say."""
    try:
        return driver.execute_cdp_cmd("Target.getTargetInfo", {})["targetInfo"]
    except Exception:
        return None


def focus_page(driver):
    """[IMP-C29] Make the current tab act as the visible, focused tab even when
    Chrome is not the foreground window. Without this, a background Chrome renders
    the Photos tab as hidden, and its '/' shortcut never opens the search box: the
    query is lost and the page stays on '/' (verified live on Chrome 154)."""
    driver.execute_cdp_cmd("Page.bringToFront", {})
    driver.execute_cdp_cmd("Emulation.setFocusEmulationEnabled", {"enabled": True})


def use_photos_tab(driver):
    """[IMP-C29] Point `driver` at a normal photos.google.com tab, then focus it.

    Keeps the current tab if it already is one. Otherwise it switches to the
    first open one, or opens a new tab and navigates it to PHOTOS_URL. A webview,
    chrome://, devtools:// or Gemini (glic) target never qualifies. When CDP
    cannot classify the targets, no open tab is trusted and a new one is opened.
    Returns "current", "existing" or "new". A Selenium fault propagates (the
    browser is gone, or no tab could be opened)."""
    handles = list(driver.window_handles)
    try:
        current = driver.current_window_handle
    except Exception:  # the tab the driver was in has been closed
        current = None
    lost = current not in handles
    if lost:
        if not handles:
            raise RuntimeError("the attached Chrome has no open tab")
        driver.switch_to.window(handles[0])  # any live target, so CDP can answer
    if _is_photos_page(_target_info(driver)):
        outcome = "existing" if lost else "current"
    else:
        try:
            infos = {i.get("targetId"): i
                     for i in driver.execute_cdp_cmd("Target.getTargets", {})["targetInfos"]}
        except Exception:
            infos = {}
        photos_tabs = [h for h in handles if _is_photos_page(infos.get(h))]
        if photos_tabs:
            driver.switch_to.window(photos_tabs[0])
            outcome = "existing"
        else:
            driver.switch_to.new_window("tab")
            driver.get(PHOTOS_URL)
            outcome = "new"
    focus_page(driver)
    return outcome


def _search_page_problem(driver):
    """[IMP-C29] Why the page after a keystroke search is not a Google Photos
    search-results page, or None when it is.

    This separates "the search ran and found nothing" (Not found) from "the
    search never ran in Google Photos". Results only exist under
    photos.google.com/search/. Anywhere else, a count of 0 means nothing, and so
    does a count of the home timeline's thumbnails, which the selectors also
    match. An unreadable URL returns None: as in check_session_alive, a browser
    glitch is not turned into a diagnosis."""
    try:
        url = urllib.parse.urlparse(driver.current_url)
    except Exception:
        return None
    if not _is_photos_host(url.hostname):
        info = _target_info(driver)
        kind = f"{info['type']} " if info and info.get("type") else ""
        where = urllib.parse.urlunparse((url.scheme, url.netloc, url.path, "", "", ""))
        return f"Not a Google Photos page: the search went to {kind}{where}"
    if not _PHOTOS_SEARCH_PATH.match(url.path or "/"):
        return (f"Search did not run (or had not started yet): Google Photos is still on "
                f"{url.path or '/'}, not on a /search/ results page")
    return None


def trigger_download(driver, query, index=0):
    """
    RAPID MODE: Navigates, Searches, Clicks, Triggers Download, Exits Player.
    Does NOT wait for file to finish. Returns True if trigger sent.
    [IMP-C29] Every attempt runs in a focused photos.google.com tab, and a search
    that never reached Google Photos is reported as such, not as "Not found".
    """
    wait = WebDriverWait(driver, 10)

    print(f"   > ⚡ Triggering: '{query}' (Index: {index})")

    def _attempt():
        """One navigate→search→click→Shift+D→Esc pass. Returns True if the
        trigger was sent, False on a 0-thumbnail miss / index out of range, or
        when the search never ran in Google Photos.
        May raise on a Selenium fault (caught/retried by the caller below)."""
        # [IMP-C29] Re-assert the photos.google.com tab and focus before every
        # search. A download or the player's Esc can leave another tab in front,
        # or close ours, and a tab that is not in front never gets the '/'
        # shortcut. A stand-in driver without Chrome's DevTools protocol (no
        # execute_cdp_cmd) is used as it is.
        if hasattr(driver, "execute_cdp_cmd"):
            outcome = use_photos_tab(driver)
            if outcome != "current":
                print(f"     > 🗂️ {_TAB_NOTE[outcome]}")
        driver.get(PHOTOS_URL)
        wait.until(EC.presence_of_element_located((By.TAG_NAME, "body")))
        check_session_alive(driver)
        time.sleep(1.5)

        actions = webdriver.ActionChains(driver)
        actions.send_keys("/")
        actions.pause(0.3)
        actions.send_keys(query)
        actions.send_keys(Keys.ENTER)
        actions.perform()

        # Wait for search results
        time.sleep(3)

        # [IMP-C29] Results exist only on a photos.google.com /search/ page.
        problem = _search_page_problem(driver)
        if problem:
            print(f"     ❌ {problem} — this is not a 'Not found'.")
            return False

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
    # yields False, preserving today's failure signal exactly. This explicit
    # one-retry block is intentionally NOT routed through mvcommon.retry(), which
    # only treats exceptions (not a False return) as retryable (Resolved Dec. 5).
    # [IMP-C6] A SessionExpiredError (logged-out) is NEVER retried/swallowed —
    # the dedicated except arms re-raise it past the broad Exception arms so it
    # fails fast for cmd_fetch_route to handle.
    result = False
    try:
        if _attempt():
            result = True
    except SessionExpiredError:
        raise
    except Exception as e:
        print(f"     ⚠️ Error: {e}")

    if not result:
        print("⏳ Retry 2/2 after 5s (no results / error)…")
        time.sleep(5)
        try:
            result = bool(_attempt())
        except SessionExpiredError:
            raise
        except Exception as e:
            print(f"     ⚠️ Error: {e}")
            result = False

    # [IMP-C6] backstop: 3 consecutive 0-thumbnail results on the same driver
    # (batch) => the session is logged in but search is dead => fail loudly.
    if result:
        setattr(driver, "_mv_zero_streak", 0)
    else:
        streak = getattr(driver, "_mv_zero_streak", 0) + 1
        setattr(driver, "_mv_zero_streak", streak)
        if streak >= 3:
            raise SessionExpiredError(
                "3 consecutive 0-thumbnail results — session likely logged out or search is dead"
            )
    return result


def wait_for_download(filename_snippet, timeout=300):
    # Kept for compatibility, but updated logic uses harvester_loop
    return None


def automation_download_file(driver, search_queries, filename_expected, dest_folder, target_index=0):
    # Kept for compatibility
    return False


# ==========================================
#             CORE LOGIC
# ==========================================

def _fetch_restore_folder(entry, temp_dir, entry_id=None):
    """Where a fetched entry's files are staged. Defaults to the entry's own
    <folder_path>/restore, or temp_dir/<filesystem-safe manual_id>/restore when a
    temp volume is supplied (mirrors main.cmd_push's tempdir redirect, so fetch and
    restore agree on the off-volume location via the SAME manual_id)."""
    if not temp_dir:
        return os.path.join(entry.get("folder_path"), RESTORE_DIR_NAME)
    # safe_id must match main._parts_base(local_folder, temp_dir, manual_id): the
    # manual_id (library key), not filename/search_term.
    safe_id = re.sub(r"[^A-Za-z0-9._-]", "_", entry_id or entry.get("filename") or "entry")
    return os.path.join(temp_dir, safe_id, RESTORE_DIR_NAME)


def _lost_download_note(lost, saw_download):
    """[IMP-C29] What the harvester can honestly say about `lost` triggered files
    that never arrived. It sees only the Downloads folder, never Chrome's verdict.
    Google Photos downloads carry no ETag or Last-Modified, so Chrome cannot
    resume a broken one: it restarts from 0 B, then fails it."""
    if saw_download:
        return (f"     A download was in progress, but {lost} triggered file(s) never arrived. "
                f"If chrome://downloads lists it as failed (e.g. 'Failed - Network error'), the "
                f"transfer broke. Google Photos downloads cannot resume: re-run the fetch "
                f"(files already fetched are skipped).")
    return (f"     No download appeared for {lost} triggered file(s). If chrome://downloads lists "
            f"it as failed, the transfer broke at once: re-run the fetch. If it is not listed, "
            f"Shift+D started nothing.")


def fetch_single_entry(driver, entry, temp_dir=None, entry_id=None):
    """
    Handles the fetch logic for a single library entry (Movie or Episode).
    Refactored to use PARALLEL TRIGGER + HARVESTER for large files.
    """
    print(f"\n🔹 PROCESSING: {entry['filename']} ({entry.get('short_id', 'N/A')})")

    restore_folder = _fetch_restore_folder(entry, temp_dir, entry_id)
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

        # [FLAC-CARRYOUT] Each carried-out track's holder is fetched like a chunk:
        # same hash-routing, staged into restore/ beside the chunks. The holder is
        # NOT in `chunks` (it is in carried_out_tracks), so enqueue it explicitly.
        for ct in entry.get("split_info", {}).get("carried_out_tracks", []):
            hname = ct.get("holder_filename")
            hhash = ct.get("holder_hash")
            if not hname or not hhash:
                continue
            if os.path.exists(os.path.join(restore_folder, hname)):
                continue
            queue.append({
                "filename": hname,
                "hash": hhash,
                "dest": restore_folder,
                "specific_query": hname,
                "fallback_query": fallback_term,
                "fallback_index": 0,
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

            # [IMP-C29] Remember whether a download was requested for this file.
            item["triggered"] = trigger_download(driver, query, idx)
            time.sleep(2)

        # Harvest
        print("   > Watching Downloads (Infinite wait if active)...")
        start_time = time.time()
        base_timeout = 300  # 5 mins initial timeout

        processed_files = set()
        saw_download = False  # [IMP-C29] was a .crdownload ever seen during this wait?

        while True:
            # Check Active Downloads
            active_downloads = [f for f in os.listdir(SYSTEM_DOWNLOADS_FOLDER) if f.endswith(".crdownload")]
            is_active = len(active_downloads) > 0
            saw_download = saw_download or is_active
            timed_out = time.time() - start_time > base_timeout

            if timed_out and is_active:
                print(f"   ⏳ Timeout reached, but {len(active_downloads)} files downloading. Extending wait...",
                      end="\r")
                time.sleep(5)
                continue  # Keep waiting

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

            # [IMP-C29] Give up only AFTER looking. A download that outlives the base
            # timeout ends with nothing active, and its file must still be collected.
            # Giving up before the scan left it in Downloads: the next attempt then
            # downloaded it a second time, and the last attempt reported INCOMPLETE.
            if timed_out and not found_new:
                print("\n   ❌ Timeout (No active downloads).")
                lost = sum(1 for i in queue if i["status"] == "pending" and i.get("triggered"))
                if lost:
                    print(_lost_download_note(lost, saw_download))
                break  # Stop waiting

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
                pre_filter_count = len(children_ids)
                pre_filter_sample = children_ids[0] if children_ids else None
                filtered = []
                for child_id in children_ids:
                    ep = episode_num_from_id(child_id, manual_id)
                    if ep is not None and s <= ep <= e:
                        filtered.append(child_id)
                children_ids = filtered
                print(f"   > 🎯 Filtered to {len(children_ids)} episodes ({ep_range})")
                # [IMP-C18] 0-match guard: a NON-EMPTY children list reduced to 0 by
                # the range is the silent-no-op signal. Warn (range + sample child id)
                # so the user sees WHY 0 matched. This is DISTINCT from the logged-out
                # SessionExpiredError path; do not route through that remediation. The
                # empty-list return contract is unchanged (caller prints "No valid
                # targets found" downstream) — this only ADDS the diagnostic.
                if pre_filter_count and not filtered:
                    print(f"⚠️ Range {ep_range} matched 0 of {pre_filter_count} "
                          f"episodes (e.g. id '{pre_filter_sample}'). Nothing selected — "
                          f"check the range vs the season's episode numbers.")
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


def resolve_target_ids(manual_id, ep_range=None):
    """The RESOLVED library ids of resolve_targets's entries, in the SAME order —
    the `safe_id` source for the tempdir redirect (must match main._parts_base's
    manual_id). A season returns its de-aliased children ids; single returns the
    resolved [real_id]."""
    lib = load_library()
    if manual_id not in lib:
        return []
    entry = lib[manual_id]
    if entry.get("type") == "season_map":
        children_ids = list(entry["children"])
        if ep_range:
            try:
                s, e = map(float, ep_range.split('-'))
                children_ids = [cid for cid in children_ids
                                if (lambda n: n is not None and s <= n <= e)(
                                    episode_num_from_id(cid, manual_id))]
            except Exception:
                pass
        seen, resolved = set(), []
        for cid in children_ids:
            real_id, _ = _resolve_alias(lib, cid)
            if real_id not in seen:
                seen.add(real_id)
                resolved.append(real_id)
        return resolved
    real_id, _ = _resolve_alias(lib, manual_id)
    return [real_id]


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

            # [FLAC-CARRYOUT] enqueue each carried-out track's holder (same
            # hash-routing, staged into restore/ beside the chunks).
            for ct in entry.get("split_info", {}).get("carried_out_tracks", []):
                hname = ct.get("holder_filename")
                hhash = ct.get("holder_hash")
                if not hname or not hhash:
                    continue
                if os.path.exists(os.path.join(restore_folder, hname)):
                    continue
                queue.append({
                    "filename": hname,
                    "hash": hhash,
                    "dest": restore_folder,
                    "specific_query": hname,
                    "fallback_query": fallback_term,
                    "fallback_index": 0,
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


# ==========================================
#   IMP-D19 Step 5 — EXTRAS FETCH QUEUE (Card C, flag-only)
# ==========================================
# The extras block (Card A2) lives on the TITLE entry — the season_map for a
# series/anime/others id, the movie leaf for a movie — at
#   entry["extras"]["groups"][<group_rel>]["items"][i]
# with leaf-shaped fields {filename, sub_rel, hash, status, uploaded,
# search_term, [split_info], ...}; an item's on-disk path is
#   <title folder_path>/<group_rel>/<sub_rel>.
#
# We do NOT invent a new download mechanism: each cloud-resident extra is turned
# into a synthetic leaf-shaped entry and run through the SAME fetch_single_entry
# (build queue by hash -> trigger_download -> harvester -> hash-match -> move)
# that main content uses. Because fetch_single_entry stages into
# <entry folder_path>/restore/, a synthetic entry whose folder_path is the
# extra's on-disk folder stages into <title folder_path>/<group_rel>/restore/ —
# exactly the convention Step 6's extras restore reads from before placing the
# merged/verified file back at <title folder_path>/<group_rel>/<sub_rel>.


def build_extras_entries(title_entry):
    """Flatten a title entry's extras.groups into synthetic leaf-shaped fetch
    entries — one per CLOUD-RESIDENT (uploaded=True) extra item — that
    fetch_single_entry / build_download_queue consume VERBATIM.

    Each synthetic entry's `folder_path` is the extra's own on-disk folder
    (os.path.dirname(<title folder_path>/<group_rel>/<sub_rel>)), so the proven
    fetch loop stages its download into <that folder>/restore/. A WHOLE-file
    extra carries the item `hash`; a SPLIT extra carries `split_info`
    (is_split / chunks[{filename, hash}]) so each chunk is queued by hash —
    identical to a split leaf. Items not yet uploaded are skipped (nothing to
    fetch in the cloud). PURE: no library / browser / device I/O."""
    title_folder = title_entry.get("folder_path", "")
    groups = title_entry.get("extras", {}).get("groups", {})
    entries = []
    for group_rel in sorted(groups):
        group_os = group_rel.replace("/", os.sep)
        for item in groups[group_rel].get("items", []):
            if not item.get("uploaded"):
                continue  # not yet pushed -> not in the cloud -> nothing to fetch
            sub_rel = item.get("sub_rel") or item.get("filename", "")
            sub_os = sub_rel.replace("/", os.sep)
            extra_folder = os.path.dirname(os.path.join(title_folder, group_os, sub_os))
            entries.append({
                "filename": item.get("filename"),
                "folder_path": extra_folder,
                "hash": item.get("hash"),
                "search_term": item.get("search_term"),
                "short_id": item.get("short_id"),
                "split_info": item.get("split_info"),
            })
    return entries


def _extras_title(lib, manual_id):
    """(title_id, title_entry) that `manual_id`'s extras hang off, or (None, None).

    Mirrors main._extras_title_id: a season_map is its own title; an episode leaf
    uses its parent_id season_map; a movie leaf is its own title (multi_ep_alias
    whose primary is missing -> no title). The ONE copy of this rule in mainfetch,
    shared by resolve_title_extras (which extras to fetch) and cmd_fetch_route's
    account routing (IMP-C26: extras route by their title id)."""
    if manual_id not in lib:
        return None, None
    real_id, entry = _resolve_alias(lib, manual_id)
    if entry.get("type") == "multi_ep_alias":
        return None, None
    if entry.get("type") == "season_map":
        return real_id, entry
    parent_id = entry.get("parent_id")
    if parent_id and parent_id in lib:
        return parent_id, lib[parent_id]
    return real_id, entry


def resolve_title_extras(manual_id):
    """Resolve `manual_id` to its TITLE entry and return that title's
    cloud-resident extras as synthetic fetch entries (all groups), or [] if the
    id is absent or carries no fetchable extras.

    Title resolution (_extras_title) mirrors main._extras_title_id: a season_map
    is its own title; an episode leaf uses its parent_id season_map; a movie leaf
    is its own title (multi_ep_alias whose primary is missing -> no title). PURE
    read — only load_library; no browser / device side effects. (A second
    load_library here, after resolve_targets', is the deliberate cost of leaving
    resolve_targets byte-for-byte untouched; it only runs when --fetchExtras is
    set.)"""
    _title_id, title_entry = _extras_title(load_library(), manual_id)
    return build_extras_entries(title_entry) if title_entry is not None else []


# [IMP-C26] Per-object account override. An archived object can live in a
# DIFFERENT Google account than its id prefix says (the 2026-09-25 inventory
# mapping found 31 X-Files episodes in the MOVIES account), and prefix routing
# alone can never fetch it. The gitignored mvconfig.json may therefore carry
#     "fetch_account_overrides": {"<exact manual id or id prefix>": "<account>"}
# where <account> is a CHROME_PROFILES key. profile_for_id consults it first
# (IMP-C25 later adds the learned per-object account beneath it). No key — the
# default — means today's prefix routing, unchanged.
_OVERRIDES_CACHE = None  # (raw config value, validated dict) — see _account_overrides


def _account_overrides():
    """The validated fetch_account_overrides map {exact id or id prefix: account}.

    Read at call time through mvcommon._load_config() — module-qualified, per the
    binding-hazard rule in mvcommon's RUNTIME CONFIG section — and so cached per
    process like the config itself: each config object is validated once, which
    means an invalid entry prints ONE warning (stderr, like mvcommon's own config
    warnings) however often profile_for_id runs, and is then ignored. Invalid =
    an account that is not a CHROME_PROFILES key, or a blank key (it would match
    every id). A value that is not a JSON object warns once and is ignored."""
    global _OVERRIDES_CACHE
    raw = mvcommon._load_config().get("fetch_account_overrides", {})
    if _OVERRIDES_CACHE is not None and _OVERRIDES_CACHE[0] is raw:
        return _OVERRIDES_CACHE[1]
    valid = {}
    if not isinstance(raw, dict):
        print(f"⚠️  mvconfig.json: fetch_account_overrides must be an object "
              f"{{\"<id or id prefix>\": \"<account>\"}}, got {type(raw).__name__} — ignored.",
              file=sys.stderr)
    else:
        for key, account in raw.items():
            if not key.strip():
                print("⚠️  mvconfig.json: fetch_account_overrides has a blank key, "
                      "which would match every id — entry ignored.", file=sys.stderr)
            elif not (isinstance(account, str) and account in CHROME_PROFILES):
                print(f"⚠️  mvconfig.json: fetch_account_overrides entry {key!r}: {account!r} "
                      f"is not an account key ({', '.join(CHROME_PROFILES)}) — entry ignored.",
                      file=sys.stderr)
            else:
                valid[key] = account
    _OVERRIDES_CACHE = (raw, valid)
    return valid


def profile_for_id(manual_id):
    # [IMP-C26] An mvconfig.json fetch_account_overrides entry wins: the longest
    # matching key, so an exact id beats any prefix of it (keys are plain string
    # prefixes). Otherwise today's id-prefix routing, unchanged.
    overrides = _account_overrides()
    best = max((key for key in overrides if manual_id.startswith(key)), key=len, default=None)
    if best is not None:
        return overrides[best]
    for prefix, key in ID_PREFIX_PROFILE:
        if manual_id.startswith(prefix):
            return key
    return DEFAULT_PROFILE


def _account_groups(manual_id, active_profile, targets, target_ids, extra_entries):
    """[IMP-C26] Split one fetch batch into per-account groups:
    [(profile, [(entry, entry_id), ...], [extra_entry, ...]), ...].

    The selector's own account (active_profile) comes first, then the others in
    CHROME_PROFILES order; each group keeps the batch's original order. A target
    routes by its own resolved id (one without an id — never produced by
    resolve_target_ids, tolerated exactly like today's entry_id=None — routes
    with the selector); the extras route by their TITLE id (_extras_title). With
    no valid fetch_account_overrides this returns today's single session without
    routing anything per target, so the no-override path cannot change."""
    pairs = [(entry, target_ids[i] if i < len(target_ids) else None)
             for i, entry in enumerate(targets)]
    if not _account_overrides():
        return [(active_profile, pairs, list(extra_entries))]
    groups = {}
    for entry, entry_id in pairs:
        groups.setdefault(profile_for_id(entry_id or manual_id), ([], []))[0].append((entry, entry_id))
    if extra_entries:
        title_id = _extras_title(load_library(), manual_id)[0] or manual_id
        groups.setdefault(profile_for_id(title_id), ([], []))[1].extend(extra_entries)
    order = [active_profile] + [p for p in CHROME_PROFILES if p != active_profile]
    return [(p, groups[p][0], groups[p][1]) for p in sorted(groups, key=order.index)]


def _close_browser_windows(driver):
    """[IMP-C26] Close every window of the automation Chrome a batch is leaving
    for another account, so that browser exits and frees init_driver's fixed
    debug port 9222. In attach mode (debuggerAddress) driver.quit() ends only the
    Selenium session and can leave the browser running; the next init_driver's
    Chrome could then not bind 9222 and Selenium would attach to the PREVIOUS
    account's browser. Best-effort — _debug_port_free() checks the outcome before
    the next launch. Only called between two account groups, so a single-account
    batch still ends exactly as before (quit only)."""
    try:
        handles = list(driver.window_handles)
    except Exception:
        return
    for handle in handles:
        try:
            driver.switch_to.window(handle)
            driver.close()
        except Exception:
            pass


def _debug_port_free(port=9222, timeout=15.0):
    """[IMP-C26] True once nothing accepts connections on 127.0.0.1:<port> —
    init_driver's fixed Chrome debug port — polling for up to `timeout` seconds;
    False if something still listens. Gates the account switch inside one batch,
    so a still-running Chrome of the previous account can never be attached to
    by mistake (it would silently search the wrong account)."""
    deadline = time.time() + timeout
    while True:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=2):
                pass
        except OSError:
            return True
        if time.time() >= deadline:
            return False
        time.sleep(0.5)


def cmd_fetch_route(manual_id, ep_range=None, fetch_extras=False, temp_dir=None):
    print(f"--- FETCH ROUTER: {manual_id} ---")

    active_profile = profile_for_id(manual_id)
    print(f"   > [Account] Profile for {manual_id}: '{active_profile}' "
          f"({CHROME_PROFILES.get(active_profile, '?')})")

    targets = resolve_targets(manual_id, ep_range)
    target_ids = resolve_target_ids(manual_id, ep_range)
    # [IMP-D19 Step 5, Card C — flag-only] When --fetchExtras is set, ALSO fetch
    # the title's cloud-resident extras (ALL groups — the episode range filters
    # only episodes; extras are all-or-nothing). Absent flag => no extras query,
    # so this path is byte-for-byte today's behavior.
    extra_entries = resolve_title_extras(manual_id) if fetch_extras else []
    if not targets and not extra_entries:
        print("❌ No valid targets found.")
        return

    print(f"   > 📋 Processing {len(targets)} items...")
    if extra_entries:
        print(f"   > 📎 + {len(extra_entries)} extra(s) (--fetchExtras)")

    # [IMP-C26] One Chrome session per Google account the batch touches: a single
    # group (exactly today's session) unless fetch_account_overrides moves items.
    groups = _account_groups(manual_id, active_profile, targets, target_ids, extra_entries)

    # [IMP-C17] Single-flight: only one interactive fetch batch may drive the
    # browser at a time (blocking=True polls then reclaims a stale/contended
    # lock — it never hard-blocks). Placed AFTER the no-targets guard so an
    # empty batch never touches the lock file. [IMP-C26] Held across EVERY
    # account group, so nothing else can take port 9222 between two sessions.
    with fetch_session_lock(blocking=True):
        for n, (profile, group_targets, group_extras) in enumerate(groups):
            # Init Selenium ONCE per account group (one group = the whole batch)
            driver = None
            try:
                if profile != active_profile:
                    print(f"   > [Account] Switching to profile '{profile}' for "
                          f"{len(group_targets) + len(group_extras)} item(s) "
                          f"(fetch_account_overrides)")
                if n and not _debug_port_free():
                    print(f"❌ Cannot switch to profile '{profile}': Chrome's debug port 9222 "
                          f"is still in use (the previous account's Chrome did not close). "
                          f"Close that Chrome window, then re-run — files already fetched "
                          f"stay in their restore folder and are skipped.")
                    return
                # Pass the selected profile
                driver = init_driver(profile)
                if not driver: return

                for entry, entry_id in group_targets:
                    fetch_single_entry(driver, entry, temp_dir=temp_dir, entry_id=entry_id)

                # [IMP-D19 Step 5] Extras fetch through the SAME proven mechanism:
                # each synthetic extra entry stages into its own
                # <title folder_path>/<group_rel>/restore/ folder.
                if group_extras:
                    print(f"\n=== 📎 FETCHING {len(group_extras)} EXTRA(S) for {manual_id} ===")
                for ex in group_extras:
                    fetch_single_entry(driver, ex)

                if n + 1 < len(groups):
                    _close_browser_windows(driver)

            except SessionExpiredError:
                # [IMP-C6] One logged-out detection aborts the whole batch loudly
                # (IMP-C26: naming the account group that hit it).
                print(f"❌ Profile '{profile}' is logged out. Open Chrome with "
                      f"--user-data-dir={CHROME_PROFILES[profile]}, sign in to "
                      f"photos.google.com, then re-run.")
                return
            except KeyboardInterrupt:
                print("\n🛑 Stopped by user.")
                break
            except Exception as e:
                print(f"\n❌ Critical Error: {e}")
                break
            finally:
                if driver:
                    try:
                        driver.quit()
                    except:
                        pass

    print("\n✅ Batch Processing Complete.")


def parse_fetch_args(argv):
    """Pure parser for mainfetch CLI args. Takes full argv list, returns
    (mid, epr, fetch_extras, temp_dir). Prints usage and sys.exit(1) on bad
    invocation — no Selenium/browser side effects.

    [IMP-D19 Step 5, Card C — flag-only] `--fetchExtras` (aliases
    `--fetch-extras` / `--extras` / `--extra`) is a boolean flag forwarded
    verbatim from main.py; when set, cmd_fetch_route ALSO fetches the title's
    cloud-resident extras. There is no prompt — the flag is the sole gate, and
    its absence reproduces today's main-content-only fetch byte-for-byte. The
    flag, the `episodes <range>` pair, and `tempdir <path>` may appear in any
    order after the id."""
    if len(argv) < 3 or argv[1] != "fetch":
        print("Usage: fetch [id] [episodes] [range] [tempdir <path>] [--fetchExtras]")
        sys.exit(1)
    mid = argv[2]
    rest = argv[3:]
    epr = None
    temp_dir = None
    i = 0
    while i < len(rest):
        if rest[i] == "episodes" and i + 1 < len(rest):
            epr = rest[i + 1]
            i += 2
            continue
        if rest[i] == "tempdir" and i + 1 < len(rest):
            temp_dir = rest[i + 1]
            i += 2
            continue
        i += 1
    fetch_extras = any(
        t in ("--fetchExtras", "--fetch-extras", "--extras", "--extra") for t in rest
    )
    return (mid, epr, fetch_extras, temp_dir)


if __name__ == "__main__":
    mid, epr, fetch_extras, temp_dir = parse_fetch_args(sys.argv)
    cmd_fetch_route(mid, epr, fetch_extras, temp_dir=temp_dir)