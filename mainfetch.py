import os
import sys
import subprocess
import shutil
import time
import re
import socket
import hashlib
import errno
import json
import urllib.parse
from datetime import datetime, timezone
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
import gpweb  # [IMP-C25] the shared Google Photos name / hash helpers (stdlib only)
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

# [IMP-C25 P-14] Kill switch for the exact-item stages (the locator phase,
# docs/feature-fetch-datetime/PLAN.md Step 12). False runs today's two legacy
# attempts only — the fetch selection from before IMP-C25. Learning (the learned
# sidecar), the lost-download retry (IMP-C34) and the search-results wait
# (IMP-C35) stay on either way.
EXACT_LOCATOR_ENABLED = True


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
        "--hide-crash-restore-bubble",  # [IMP-C25 2026-10-08] Chrome 154 ignores the switch above
        # [IMP-C29] Downloads must ride TCP. Google serves them over HTTP/3 (QUIC)
        # when Chrome allows it, and a QUIC download fails for good after a stall
        # of about 8 s ("Failed - Network error"; a Photos download cannot resume).
        # Over TCP it survived a 60 s stall and ran 2-3x faster (measured live).
        "--disable-quic",
        "about:blank"
    ]

    # [IMP-C25 2026-10-08] A profile whose last Chrome did not record a clean exit would open with "Restore pages?".
    mark_profile_clean_exit(user_data_dir)
    try:
        chrome = subprocess.Popen(cmd)  # [IMP-C25 Step 11 d] kept: the fetch journal records its pid
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
    # [IMP-C25 Step 11 d] Additive: the Chrome this call launched and its profile, for the fetch journal and the
    # interruption cleanup (a chrome.exe that handed over to an already-running browser has exited by now).
    driver._mv_chrome_proc = chrome
    driver._mv_user_data_dir = user_data_dir
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


def profile_preflight(account):
    """[IMP-C25] Offline pre-flight for one Google account's automation Chrome
    profile (OD-10: the profiles are prerequisites). Run it BEFORE launching
    Chrome: pointed at a missing --user-data-dir, Chrome would silently create
    an EMPTY profile there and land signed out.

    Returns None when `account` is a CHROME_PROFILES key whose profile folder
    exists, else the one-line remediation to print INSTEAD of launching. It
    starts nothing and writes nothing. A profile that exists but is signed out
    is only visible after the launch — check_session_alive catches that.
    Shared by the inventory crawler (IMP-C25 Step 7) and, later, the
    per-account fetch/check sessions (Steps 10, 13, 13b, 14)."""
    user_data_dir = CHROME_PROFILES.get(account)
    if user_data_dir is None:
        return (f"unknown Google account {account!r} - expected one of: "
                f"{', '.join(CHROME_PROFILES)}")
    if not os.path.isdir(user_data_dir):
        return (f"the Chrome profile for '{account}' does not exist: {user_data_dir} - "
                f"create it (launch Chrome once with --user-data-dir={user_data_dir}), "
                f"sign in to photos.google.com, then re-run")
    return None


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
            _note_opened_tab(driver)  # [IMP-C25 Step 11 i] the fetch closes the tabs it opened when it ends
            driver.get(PHOTOS_URL)
            outcome = "new"
    focus_page(driver)
    _rearm_downloads(driver)  # [IMP-C25 P-12] a fetch entry's downloads go to its incoming dir from this tab too
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


# [IMP-C35] For 1-2 s after Enter the page already reads /search/ while it still
# SHOWS the home timeline, whose thumbnails the selectors match too, and results
# took 2-7 s to appear (IMP-C29, measured live). So a search waits for its results
# — a bounded poll that replaces the fixed 3 s sleep — and no timeline thumbnail
# is ever clicked on a search page.
SEARCH_RESULTS_WAIT_S = 10.0  # the longest a search may take to show its results
SEARCH_POLL_S = 0.5           # how often the page is looked at while waiting
# [live check 2026-10-05] Google Photos now shows a "Most relevant to your search"
# strip of loose matches ABOVE the date-grouped results; the legacy index-0 click
# took its first tile (The Expanse for a Silicon Valley filename). Its tiles are
# never clicked, and a search that shows ONLY the strip has no result: these many
# consecutive looks at it end the wait (live, the results render with the strip).
SEARCH_SUGGESTIONS_ONLY_LOOKS = 4
# The page scripts are the shared ones: gpweb.SEARCH_STATE_JS (results vs timeline
# links vs strip suggestions, "No results") and gpweb.TIMELINE_LINKS_JS (is an
# element a timeline link or a strip suggestion — not a result to click?).


def _await_search_results(driver):
    """[IMP-C35] Wait — a bounded poll, not a fixed sleep — for a keystroke
    search's results before any thumbnail is read.

    Ready (returns None) once the page is a photos.google.com /search/ page that
    shows result thumbnails and no timeline thumbnail, with the same number of
    results on two looks in a row (the grid has settled), or once Google Photos
    says it found nothing and the timeline is gone, or once it has shown only its
    "Most relevant to your search" suggestions for SEARCH_SUGGESTIONS_ONLY_LOOKS
    looks in a row (no result: the read finds nothing it may click — live check
    2026-10-05). After SEARCH_RESULTS_WAIT_S it
    reads whatever results are shown — but returns the problem line instead when
    the page never reached /search/ (_search_page_problem's diagnosis) or shows
    nothing but the home timeline: nothing may be clicked then. A driver that runs
    no page script (a test stand-in: execute_script returns nothing) is ready once
    the URL check passes, exactly as before this wait existed. Shared by every
    search path (trigger_download; IMP-C25 Step 12's UI stages)."""
    problem = state = None
    suggestions_only = 0  # consecutive looks showing only the "Most relevant" strip
    for poll in range(int(SEARCH_RESULTS_WAIT_S / SEARCH_POLL_S) + 1):
        if poll:
            time.sleep(SEARCH_POLL_S)
        problem = _search_page_problem(driver)
        if problem:
            continue  # not (yet) on a /search/ page
        try:
            looked = driver.execute_script(gpweb.SEARCH_STATE_JS)
        except Exception:
            continue  # the page was busy (still navigating): look again
        if not isinstance(looked, dict):
            return None  # a stand-in without page scripts: read the page as before
        previous, state = state, looked
        only = not state.get("timeline") and not state.get("results") and bool(state.get("relevant"))
        suggestions_only = suggestions_only + 1 if only else 0
        if state.get("timeline"):
            continue  # the home timeline is still shown
        if state.get("results"):
            if previous and not previous.get("timeline") and previous.get("results") == state["results"]:
                return None  # results shown, and settled
        elif state.get("empty"):
            return None  # "No results": a genuine miss — the read finds nothing
        elif suggestions_only >= SEARCH_SUGGESTIONS_ONLY_LOOKS:
            return None  # only loose "Most relevant" matches: a miss — the read finds nothing to click
    if problem:
        return problem
    if state and state.get("timeline") and not state.get("results"):
        return (f"Search results did not replace the home timeline within {SEARCH_RESULTS_WAIT_S:g} s; "
                f"nothing was clicked")
    return None


def _drop_timeline(driver, elements):
    """[IMP-C35] `elements` without every one that is — or sits inside — a link to
    an item that is not a search result to click: a home-timeline thumbnail, or a
    loose match in Google Photos' "Most relevant to your search" strip above the
    results (live check 2026-10-05); on a search page such an element is never
    clicked. One page-context call; a driver that runs no page script (a test
    stand-in) keeps them all. A failing call raises — a Selenium fault, which the
    IMP-C2 retry handles."""
    if not elements:
        return elements
    timeline = driver.execute_script(gpweb.TIMELINE_LINKS_JS, elements)
    if timeline is None:
        return elements
    return [el for el, is_timeline in zip(elements, timeline) if not is_timeline]


def trigger_download(driver, query, index=0):
    """
    RAPID MODE: Navigates, Searches, Clicks, Triggers Download, Exits Player.
    Does NOT wait for file to finish. Returns True if trigger sent.
    [IMP-C29] Every attempt runs in a focused photos.google.com tab, and a search
    that never reached Google Photos is reported as such, not as "Not found".
    [IMP-C35] It waits for the search's results (_await_search_results) instead of
    a fixed 3 s, and never clicks a home-timeline thumbnail (_drop_timeline).
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

        # Wait for search results ([IMP-C35] a bounded poll until they are shown;
        # [IMP-C29] results exist only on a photos.google.com /search/ page).
        problem = _await_search_results(driver)
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
        all_thumbnails = _drop_timeline(driver, all_thumbnails)  # [IMP-C35] search results only

        if not all_thumbnails:
            try:
                candidates = driver.find_elements(By.XPATH, "//div[contains(@style, 'background-image')]")
                for el in candidates:
                    if el.is_displayed() and el.size['width'] > 50:
                        all_thumbnails.append(el)
            except:
                pass
            all_thumbnails = _drop_timeline(driver, all_thumbnails)  # [IMP-C35] search results only

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


# ==========================================
#   IMP-C25 STEP 11 — DOWNLOAD HYGIENE (P-12)
# ==========================================
# docs/feature-fetch-datetime/PLAN.md P-12 + Step 11. Chrome downloads straight into
# each entry's own destination: <dest>\.mv_incoming\, <dest> = _fetch_restore_folder
# (<folder_path>\restore, or <tempdir>\<safe id>\restore). Everything in that folder is
# disposable: a file whose SHA-256 is an expected object's is renamed into <dest>, any
# other file is deleted, and the folder is removed when the entry ends — nothing ever
# lands in ~/Downloads. MediaVault deletes a file only when it sits DIRECTLY in a folder
# that is structurally an incoming folder (named .mv_incoming, or one level under a
# <drive>:\MV_incoming\ root) AND holds the INCOMING_MARKER file — or, in the ~/Downloads
# fallback (Chrome refused the redirect), when it appeared during this run, belongs to
# the entry's expected-name family and is not a file the run kept.
INCOMING_DIR_NAME = ".mv_incoming"         # <dest>\.mv_incoming\ — one entry's Chrome download folder
INCOMING_MARKER = ".mediavault_incoming"   # marker file: only a folder holding it is ever emptied
SHORT_INCOMING_ROOT = "MV_incoming"        # <drive>:\MV_incoming\<8-char hash>\ — the long-path fallback
INCOMING_PATH_LIMIT = 240                  # incoming path + file name + ".crdownload" longer than this -> short dir
DISK_HEADROOM_BYTES = 2 * 1024 ** 3        # free space an entry must leave on its destination volume
_CRDOWNLOAD = ".crdownload"                # Chrome's in-progress suffix ("<name>.crdownload", "Unconfirmed N.crdownload")
# The fetch journal (Step 11 d): one JSON file per run, ~/.mediavault/fetch_journal/<run_id>.json (a separate file —
# NOT the rollback journal .mediavault_txn.json). tests/conftest.py redirects it for every test.
FETCH_JOURNAL_DIR = os.path.join(mvcommon.MV_STATE_DIR, "fetch_journal")
_JOURNAL_VERSION = 1
_JOURNAL_DONE = ("clean", "recovered")     # a journal in these states needs nothing more
_TRIGGER_METHODS = ("direct", "shift_d", "legacy")


def _utc_now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _write_json_atomic(path, doc):
    """Write `doc` as `path` atomically: a sibling temp file, fsynced, then os.replace — a crash at
    any moment leaves either the previous complete file or the new one. Raises OSError."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, ensure_ascii=False, indent=1)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


def _object_state(item):
    """A queue item's journal state: moved (SHA-256-verified and renamed into place — the harvester
    does both at once) · failed · triggered (a download was requested) · located (an exact-item
    stage named its photo_id, IMP-C25 Step 12) · pending."""
    if item.get("status") == "done":
        return "moved"
    if item.get("status") == "failed":
        return "failed"
    if item.get("triggered") or item.get("_retrigger"):
        return "triggered"
    trigger = item.get("_trigger") if isinstance(item.get("_trigger"), dict) else {}
    record = trigger.get("record") if isinstance(trigger.get("record"), dict) else {}
    return "located" if gpweb.MEDIA_KEY_RE.match(str(record.get("photo_id") or "")) else "pending"


def _journal_object(item):
    """One object's journal record — whitelisted fields only, so nothing a stage keeps on the item
    (a minted download URL above all — P-20) can reach the file: label, sha256, state, the trigger
    method (direct | shift_d from the stage's item['_trigger'], else legacy once requested) and
    the photo_id the stage named (only a well-formed media key)."""
    loc = item.get("locator") or {}
    trigger = item.get("_trigger") if isinstance(item.get("_trigger"), dict) else {}
    record = trigger.get("record") if isinstance(trigger.get("record"), dict) else {}
    obj = {"label": loc.get("label") if isinstance(loc.get("label"), str) else None,
           "sha256": item.get("hash") if mvcommon._is_sha256(item.get("hash")) else None,
           "state": _object_state(item)}
    if trigger.get("method") in _TRIGGER_METHODS:
        obj["method"] = trigger["method"]
    elif item.get("_retrigger"):
        obj["method"] = "legacy"
    photo_id = record.get("photo_id")
    if isinstance(photo_id, str) and gpweb.MEDIA_KEY_RE.match(photo_id):
        obj["photo_id"] = photo_id
    return obj


class FetchJournal:
    """[IMP-C25 Step 11 d — P-12] One fetch run's crash record:
    FETCH_JOURNAL_DIR\\<run_id>.json, rewritten atomically (_write_json_atomic) whenever it changes,
    so after a crash or a closed terminal the next run's start-up cleanup knows what to clean:

      v, run_id, started_at, updated_at   (UTC 'YYYY-MM-DDTHH:MM:SSZ')
      pid          this mainfetch process — a "running" journal whose pid still runs is a live fetch
      state        running · clean (finished, nothing left behind) · pending (finished or
                   interrupted, something still to delete) · recovered (a later run cleaned it up)
      interrupted  the run ended on Ctrl+C or an error
      mode         the latest entry's download mode (incoming | fallback | legacy)
      sessions     [{account, chrome_pid, user_data_dir, launched, started_at, closed}] — each
                   account session's Chrome: launched True = this run started it (the only kind a
                   cleanup may terminate), False = it was already running (attached; chrome_pid is
                   then the browser on the debug port), None = unknown; closed = how the session
                   ended it (11 i): chrome · tabs · windows · terminated · open (absent: the run
                   died before closing — the start-up cleanup takes over)
      downloads_snapshot  {name: [size, mtime_ns]} of ~/Downloads when the run first used the
                   fallback (absent otherwise) — a name not in it appeared during the run
      entries      [{manual_id, label, account, dest, mode, incoming, names, cleaned, objects}] —
                   one per entry and session: its incoming folder (None outside the incoming
                   mode), its expected-name family, whether its folder was emptied and removed,
                   and per object {label, sha256, state, method, photo_id} (_journal_object)
      pending_delete  [path] — files Chrome held when they had to go
      recovered_by the run that cleaned it up
    Never a URL (P-20). A journal that cannot be written prints one warning; the fetch goes on."""

    def __init__(self, run_id):
        self.path = os.path.join(FETCH_JOURNAL_DIR, f"{run_id}.json")
        self.broken = False
        now = _utc_now()
        self.doc = {"v": _JOURNAL_VERSION, "run_id": run_id, "started_at": now, "updated_at": now,
                    "pid": os.getpid(), "state": "running", "interrupted": False, "mode": None,
                    "sessions": [], "entries": [], "pending_delete": []}
        self.save()

    def save(self):
        if self.broken:
            return
        self.doc["updated_at"] = _utc_now()
        try:
            _write_json_atomic(self.path, self.doc)
        except (OSError, TypeError, ValueError) as e:
            self.broken = True
            print(f"   > ⚠️ The fetch journal could not be written ({type(e).__name__}: {e}) — if this fetch is "
                  f"interrupted, its leftovers are not cleaned up automatically (`python main.py fetch_cleanup` "
                  f"lists them).")

    def add_session(self, account, chrome_pid, user_data_dir, launched=None):
        self.doc["sessions"].append({"account": account, "chrome_pid": chrome_pid if mvcommon._is_int(chrome_pid)
                                     else None, "user_data_dir": user_data_dir, "launched": launched,
                                     "started_at": _utc_now()})
        self.save()

    def end_session(self, closed):
        """[11 i] How the latest session's Chrome was left: chrome · tabs · windows · terminated · open."""
        if self.doc["sessions"]:
            self.doc["sessions"][-1]["closed"] = closed
            self.save()

    def add_entry(self, target, queue, account):
        """Record an entry before its first request; returns its record (update_objects / end_entry)."""
        loc = (queue[0].get("locator") or {}) if queue else {}
        record = {"manual_id": loc.get("manual_id"), "label": loc.get("label"), "account": account,
                  "dest": target["dest"], "mode": target["mode"], "incoming": target["incoming"],
                  "names": list(target["family"]), "cleaned": target["mode"] != "incoming",
                  "objects": [_journal_object(item) for item in queue]}
        self.doc["entries"].append(record)
        self.doc["mode"] = target["mode"]
        self.save()
        return record

    def update_objects(self, record, queue):
        objects = [_journal_object(item) for item in queue]
        if record is not None and objects != record["objects"]:
            record["objects"] = objects
            self.save()

    def set_snapshot(self, snapshot):
        self.doc["downloads_snapshot"] = {name: list(stamp) for name, stamp in snapshot.items()}
        self.save()

    def end_entry(self, record, cleaned, pending):
        if record is not None:
            record["cleaned"] = bool(cleaned)
        self.doc["pending_delete"] = list(pending)
        self.save()

    def finish(self, pending, interrupted):
        """The run is over: clean when every entry's folder was emptied and nothing is pending."""
        self.doc["pending_delete"] = list(pending)
        self.doc["interrupted"] = bool(interrupted)
        unclean = any(not e.get("cleaned") for e in self.doc["entries"])
        self.doc["state"] = "pending" if (pending or unclean) else "clean"
        self.save()


def _set_download_dir(driver, path):
    """[P-12] Point the attached Chrome's downloads at `path`: CDP Browser.setDownloadBehavior
    {behavior: allow, downloadPath, eventsEnabled: true}, else the older Page.setDownloadBehavior
    {behavior: allow, downloadPath}. True when Chrome accepted one of them, False when both raised
    (Chrome refused the redirect). Never raises.

    That an ATTACHED Chrome 154 honours it is unverified live (PLAN P6; 🚦 R2 §M6–M7). In the
    Chromium source the setting belongs to the DevTools session of the tab it was sent through and
    returns to Chrome's default when that session ends (the tab closes; Selenium quits) — so a fetch
    re-applies it on every Photos tab it works in (_rearm_downloads, from use_photos_tab)."""
    for command, params in (("Browser.setDownloadBehavior",
                             {"behavior": "allow", "downloadPath": path, "eventsEnabled": True}),
                            ("Page.setDownloadBehavior", {"behavior": "allow", "downloadPath": path})):
        try:
            driver.execute_cdp_cmd(command, params)
            return True
        except Exception:
            continue
    return False


def _rearm_downloads(driver):
    """Re-apply the download folder a fetch entry armed (driver._mv_download_dir) in the tab the
    driver is in now. use_photos_tab calls it for every tab it selects, so whatever the fetch
    requests next downloads into the entry's incoming dir. A no-op when nothing is armed (outside
    an entry, or in the ~/Downloads fallback); never raises."""
    path = getattr(driver, "_mv_download_dir", None)
    if path:
        _set_download_dir(driver, path)


def _note_opened_tab(driver):
    """[IMP-C25 Step 11 i] Remember the tab use_photos_tab just opened (driver._mv_opened_tabs):
    a fetch closes the tabs it opened when it ends. Never raises."""
    try:
        driver._mv_opened_tabs = list(getattr(driver, "_mv_opened_tabs", None) or []) + [driver.current_window_handle]
    except Exception:
        pass


def _family_key(name):
    """A file name as Windows compares it (case-insensitive)."""
    return os.path.normcase(name)


def _expected_names(queue):
    """The entry's expected-name family (P-12): the exact uploaded name of every queued object —
    the name Google Photos gives its download (a chunk or FLAC holder keeps its own, tagged name;
    a whole file is '<stem> [<short_id>]<ext>'). Sorted, without duplicates; an object whose
    uploaded name is unknown adds nothing."""
    return sorted({loc["uploaded_name"] for loc in (item.get("locator") or {} for item in queue)
                   if isinstance(loc.get("uploaded_name"), str) and loc["uploaded_name"]})


_UNIQUIFIED = re.compile(r"^(?P<stem>.*) \((?P<n>\d+)\)(?P<ext>\.[^.\\/]*)?$")   # Chrome's '<stem> (2)<ext>'


def _family_bases(name):
    """The base names (Windows case) the file `name` can belong to: itself without a trailing
    '.crdownload', and — for Chrome's ' (n)' variant ('<stem> (2)<ext>': Chrome numbers before
    the last extension) — the name without the number."""
    key = _family_key(name)
    if key.endswith(_CRDOWNLOAD):
        key = key[:-len(_CRDOWNLOAD)]
    bases = [key]
    numbered = _UNIQUIFIED.match(key)
    if numbered:
        bases.append(numbered.group("stem") + (numbered.group("ext") or ""))
    return bases


def _in_family(name, family):
    """True when the file `name` belongs to the expected-name family `family` (base names): one of
    them, Chrome's ' (n)' variant of one, or the '.crdownload' partial of either (_family_bases).
    Case-insensitive, like Windows names. 'Unconfirmed NNN.crdownload' (a partial Chrome has not
    named yet) never belongs: nothing ties it to an entry."""
    keys = {_family_key(base) for base in family}
    return any(base in keys for base in _family_bases(name))


def _short_incoming_root(dest):
    """<drive>:\\MV_incoming on the destination's own volume, or None when `dest` has no drive.
    (tests/conftest.py points it under the test's tmp_path.)"""
    drive = os.path.splitdrive(os.path.abspath(dest))[0]
    return os.path.join(drive + os.sep, SHORT_INCOMING_ROOT) if drive else None


def _incoming_location(dest, names):
    """Where the entry's downloads should land (P-12): <dest>\\.mv_incoming, unless that path plus
    the longest expected file name plus '.crdownload' exceeds INCOMING_PATH_LIMIT characters —
    then <drive>:\\MV_incoming\\<first 8 hex of SHA-1(dest)>, on the destination's own volume (so
    the verified file is still renamed into place, never copied); None when the destination has no
    drive (the caller then uses the ~/Downloads fallback). `names` = the file names the entry may
    download. Pure path arithmetic; creates nothing."""
    incoming = os.path.join(dest, INCOMING_DIR_NAME)
    longest = max((len(n) for n in names), default=0)
    if len(incoming) + 1 + longest + len(_CRDOWNLOAD) <= INCOMING_PATH_LIMIT:
        return incoming
    root = _short_incoming_root(dest)
    if root is None:
        return None
    digest = hashlib.sha1(os.path.normcase(os.path.abspath(dest)).encode("utf-8")).hexdigest()[:8]
    return os.path.join(root, digest)


def _make_incoming(path):
    """Create the incoming folder `path` and its INCOMING_MARKER file; True when both exist
    afterwards (an existing folder is reused), False when they cannot be created."""
    try:
        os.makedirs(path, exist_ok=True)
        marker = os.path.join(path, INCOMING_MARKER)
        if not os.path.isfile(marker):
            with open(marker, "w", encoding="utf-8") as fh:
                fh.write("MediaVault fetch download folder (IMP-C25 P-12): every file in it is disposable.\n")
        return True
    except OSError:
        return False


def _is_incoming_dir(path):
    """True only for a folder MediaVault may empty: it is named INCOMING_DIR_NAME, or sits one level
    under a SHORT_INCOMING_ROOT folder, and it holds the INCOMING_MARKER file. Every delete of the
    start-up cleanup, the interruption cleanup and fetch_cleanup asks this first."""
    if not isinstance(path, str) or not path:
        return False
    absolute = os.path.abspath(path)
    named = os.path.basename(absolute) == INCOMING_DIR_NAME
    short = os.path.basename(os.path.dirname(absolute)) == SHORT_INCOMING_ROOT
    return (named or short) and os.path.isfile(os.path.join(absolute, INCOMING_MARKER))


def _remaining_bytes(entry, owner_id, queue):
    """Best estimate of the bytes `queue` (one entry's objects still to fetch) will download: each
    object's recorded size (its trusted block's size_bytes, else its capture's — both describe
    exactly these bytes); without one, a whole file counts the entry's tech_spec.size_bytes and a
    chunk an equal share of it (a split's chunks add up to about the whole file); anything else
    (a FLAC holder) counts 0 — DISK_HEADROOM_BYTES covers it."""
    objects = _entry_objects(entry, owner_id) if queue else {}
    whole = (entry.get("tech_spec") or {}).get("size_bytes")
    whole = whole if mvcommon._is_int(whole) and whole > 0 else 0
    split = entry.get("split_info") if isinstance(entry.get("split_info"), dict) else {}
    chunks = len(split.get("chunks") or []) if split.get("is_split") else 0
    total = 0
    for item in queue:
        obj = objects.get(item["hash"]) or {}
        size = next((facts["size_bytes"] for facts in (obj.get("block"), obj.get("capture"))
                     if isinstance(facts, dict) and mvcommon._is_int(facts.get("size_bytes"))
                     and facts["size_bytes"] > 0), None)
        if size is None:
            kind = (item.get("locator") or {}).get("kind")
            size = whole if kind in ("whole", "extra_whole") else (
                whole // chunks if kind in ("chunk", "extra_chunk") and chunks else 0)
        total += size
    return total


def _disk_preflight(dest, need_bytes):
    """[P-12 rule 5] True when the destination volume holds the entry's remaining bytes plus
    DISK_HEADROOM_BYTES; prints one line either way (a refusal names the volume and the sizes, and
    nothing is downloaded for the entry). A volume whose free space cannot be read passes with a
    note — the download itself would then report the problem."""
    need = need_bytes + DISK_HEADROOM_BYTES
    volume = os.path.splitdrive(os.path.abspath(dest))[0] or dest
    size = mvcommon.human_readable_size
    try:
        free = shutil.disk_usage(dest).free
    except OSError as e:
        print(f"   > ⚠️ Could not read the free space on {volume} ({e}) — continuing.")
        return True
    sizes = f"{size(need)} needed ({size(need_bytes)} to download + {size(DISK_HEADROOM_BYTES)} headroom)"
    if free < need:
        print(f"   ❌ Not enough free space on {volume} for this entry: {sizes}, only {size(free)} free. "
              f"Nothing was downloaded — free some space, then re-run.")
        return False
    print(f"   > 💽 Disk space on {volume}: {sizes}, {size(free)} free.")
    return True


def _point_downloads(driver, path):
    """_set_download_dir, once more in a freshly selected Photos tab when the first try fails (the
    tab the driver was in may have closed). True when Chrome accepted the folder."""
    if _set_download_dir(driver, path):
        return True
    try:
        use_photos_tab(driver)
    except Exception:
        return False
    return _set_download_dir(driver, path)


def _remove_incoming(path):
    """Remove the incoming folder `path` when nothing but its marker is left in it (the marker goes
    first) — and, for a long-path folder, its MV_incoming root once that is empty too. True when
    the folder is gone afterwards. Never raises; touches nothing that _is_incoming_dir does not
    allow."""
    if not os.path.isdir(path):
        return True
    if not _is_incoming_dir(path):
        return False
    try:
        if os.listdir(path) != [INCOMING_MARKER]:
            return False
        os.remove(os.path.join(path, INCOMING_MARKER))
        os.rmdir(path)
    except OSError:
        return False
    parent = os.path.dirname(os.path.abspath(path))
    if os.path.basename(parent) == SHORT_INCOMING_ROOT:
        try:
            os.rmdir(parent)  # only succeeds when empty
        except OSError:
            pass
    return True


def _empty_incoming(path):
    """[P-12 rules 1–2] Delete every file directly in the incoming folder `path` except its marker —
    all of them disposable (unverified partials, surplus or wrong downloads). Returns (deleted
    [(name, size)], locked [paths Chrome still holds]). Does nothing unless _is_incoming_dir(path);
    never descends into a sub-folder (Chrome creates none); never raises."""
    deleted, locked = [], []
    if not _is_incoming_dir(path):
        return deleted, locked
    try:
        names = sorted(os.listdir(path))
    except OSError:
        return deleted, locked
    for name in names:
        full = os.path.join(path, name)
        if name == INCOMING_MARKER or not os.path.isfile(full):
            continue
        try:
            size = os.path.getsize(full)
        except OSError:
            size = 0
        if _delete_download(full):
            deleted.append((name, size))
        else:
            locked.append(full)
    return deleted, locked


def _listing(files):
    """'a.mkv (1.00 GB), b.mkv.crdownload (0 B)' for [(name, size)]."""
    return ", ".join(f"{name} ({mvcommon.human_readable_size(size)})" for name, size in files)


def _add_pending(ctx, paths):
    for path in paths:
        if path not in ctx.pending_delete:
            ctx.pending_delete.append(path)


def _prepare_downloads(driver, queue, ctx, entry=None, owner_id=None):
    """[IMP-C25 Step 11 a] Before the entry's first request: decide where its downloads land and
    point Chrome there (P-12). Sets ctx.target (FetchContext) with one of three modes:
      incoming  Chrome saves into the entry's incoming dir — <dest>\\.mv_incoming, or the short
                <drive>:\\MV_incoming\\<hash> when that path is too long for Chrome; the harvester
                watches only it, and ~/Downloads is neither read nor written
      fallback  ~/Downloads, as before IMP-C25, with P-12's delete rules — when Chrome refused the
                redirect (then for the rest of the run, with one warning line) or no incoming folder
                could be made for this entry
      legacy    a driver without Chrome's DevTools protocol (None or a test stand-in — never a real
                Chrome, which always has execute_cdp_cmd): today's ~/Downloads harvesting, unchanged
    With `entry` given, the disk pre-flight runs first (incoming and fallback modes): False when it
    refused the entry — nothing may be requested — else True. Entries run one at a time, so no
    download is running while the folder changes."""
    dest = queue[0]["dest"]
    family = _expected_names(queue)
    target = {"mode": "legacy", "dir": SYSTEM_DOWNLOADS_FOLDER, "dest": dest, "incoming": None,
              "family": family, "handled": set(), "foreign": set()}
    if not hasattr(driver, "execute_cdp_cmd"):
        ctx.target = target
        return True
    if entry is not None and not _disk_preflight(dest, _remaining_bytes(entry, owner_id, queue)):
        return False
    target["mode"] = "fallback"
    if not ctx.redirect_refused:
        incoming = _incoming_location(dest, family + [item["filename"] for item in queue])
        if incoming is not None and _make_incoming(incoming):
            # [11 c] The folder may still hold an earlier entry's leftovers (the episodes of a season share one
            # restore folder) or a crashed run's: disposable — deleted now; one Chrome still writes is never taken
            # for this entry's download (target["foreign"]) and is deleted later.
            swept, locked = _empty_incoming(incoming)
            if swept:
                print(f"   > 🧹 Deleted {len(swept)} leftover file(s) from {incoming}: {_listing(swept)}")
            _add_pending(ctx, locked)
            target["foreign"] = {_family_key(os.path.basename(p)) for p in locked}
            if _point_downloads(driver, incoming):
                target.update(mode="incoming", dir=incoming, incoming=incoming)
                driver._mv_download_dir = incoming   # re-applied on every Photos tab (use_photos_tab)
            else:
                _remove_incoming(incoming)
                ctx.redirect_refused = True
        else:
            print(f"   > ⚠️ No download folder could be made next to {dest} — this entry downloads to "
                  f"~/Downloads.")
            if not _point_downloads(driver, SYSTEM_DOWNLOADS_FOLDER):  # never into an earlier entry's folder
                ctx.redirect_refused = True
        if ctx.redirect_refused:
            print("   ⚠️ Chrome refused the download folder (CDP) — this run downloads to ~/Downloads as before; "
                  "only files that appear during it, belong to the entry and fail verification are deleted there.")
    if target["mode"] == "fallback" and ctx.run_downloads is None:
        snapshot = _downloads_snapshot()
        ctx.run_downloads = {_family_key(name): stamp for name, stamp in snapshot.items()}
        if ctx.journal is not None:
            ctx.journal.set_snapshot(snapshot)
    if ctx.journal is not None:  # [11 d] recorded before the entry's first request
        target["journal"] = ctx.journal.add_entry(target, queue, ctx.account)
    ctx.target = target
    return True


def _finish_downloads(driver, ctx):
    """[IMP-C25 Step 11 c] The end of an entry's part in this session (after IMP-C34's retry round,
    whose harvest is the last look): the driver is disarmed; the incoming folder must be empty —
    whatever is still in it (a partial of a broken transfer, a surplus download) is deleted and
    listed, and the folder removed; a file Chrome still holds (a download still running) goes to
    ctx.pending_delete and the folder stays until it is deleted (after Chrome closes, else at the
    next start-up). Called once per session for an entry split across accounts, so each session
    starts with an empty folder. Clears ctx.target."""
    target, ctx.target = ctx.target, None
    if getattr(driver, "_mv_download_dir", None):
        driver._mv_download_dir = None
    if not target:
        return
    cleaned = True
    if target["mode"] == "incoming":
        deleted, locked = _empty_incoming(target["incoming"])
        if deleted:
            print(f"   > 🧹 Deleted {len(deleted)} unfinished or surplus download(s): {_listing(deleted)}")
        if locked:
            _add_pending(ctx, locked)
            print(f"   > ⏳ {len(locked)} file(s) in {target['incoming']} are still in use by Chrome — deleted once "
                  f"Chrome has closed.")
        cleaned = not locked and _remove_incoming(target["incoming"])
    if ctx.journal is not None:
        ctx.journal.end_entry(target.get("journal"), cleaned, ctx.pending_delete)


class _ProcessTable:
    """[IMP-C25 Step 11 e] The OS process table, as far as the fetch cleanup needs it. Windows only
    (PowerShell's Win32_Process query, taskkill); elsewhere it can tell nothing and kills nothing.
    tests/conftest.py replaces PROCESS_TABLE for every test with an inert stand-in."""

    def query(self, pid):
        """(alive, command line) of `pid`: (True, text or None) while it runs, (False, None) when
        there is no such process, (None, None) when that cannot be told — never a guess."""
        if os.name != "nt" or not mvcommon._is_int(pid) or pid <= 0:
            return None, None
        script = ("[Console]::OutputEncoding = [Text.Encoding]::UTF8; "
                  f"$p = Get-CimInstance Win32_Process -Filter 'ProcessId={int(pid)}'; "
                  "if ($p) { 'ALIVE'; $p.CommandLine } else { 'GONE' }")
        try:
            run = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
                                 capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60)
        except (OSError, subprocess.SubprocessError):
            return None, None
        lines = (run.stdout or "").lstrip("﻿").splitlines()
        head = lines[0].strip() if lines else ""
        if run.returncode != 0 or head not in ("ALIVE", "GONE"):
            return None, None
        if head == "GONE":
            return False, None
        return True, "\n".join(lines[1:]).strip() or None

    def terminate(self, pid):
        """End `pid` and the processes it started (taskkill /T /F); True when taskkill succeeded."""
        if os.name != "nt" or not mvcommon._is_int(pid) or pid <= 0:
            return False
        try:
            run = subprocess.run(["taskkill", "/PID", str(int(pid)), "/T", "/F"], capture_output=True, timeout=60)
        except (OSError, subprocess.SubprocessError):
            return False
        return run.returncode == 0

    def listener(self, port):
        """The pid of the process listening on local TCP `port` (a Chrome's debug port: its browser
        process), or None when there is none or it cannot be told."""
        if os.name != "nt" or not mvcommon._is_int(port):
            return None
        script = (f"Get-NetTCPConnection -LocalPort {int(port)} -State Listen -ErrorAction SilentlyContinue | "
                  "Select-Object -First 1 -ExpandProperty OwningProcess")
        try:
            run = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
                                 capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60)
        except (OSError, subprocess.SubprocessError):
            return None
        text = (run.stdout or "").strip().lstrip("﻿")
        return int(text) if run.returncode == 0 and text.isdigit() and int(text) > 0 else None

    def chrome_command_lines(self):
        """[IMP-C25 2026-10-08] The command line of every running chrome.exe (None for one that cannot be
        read) — or None when the process table cannot be read at all: never a guess."""
        if os.name != "nt":
            return None
        script = ("[Console]::OutputEncoding = [Text.Encoding]::UTF8; "
                  "$ps = @(Get-CimInstance Win32_Process -Filter 'Name=''chrome.exe''' -ErrorAction Stop); 'OK'; "
                  r"foreach ($p in $ps) { if ($p.CommandLine) { 'CMD ' + ($p.CommandLine -replace '[\r\n]+', ' ') } "
                  "else { 'CMD?' } }")
        try:
            run = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
                                 capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60)
        except (OSError, subprocess.SubprocessError):
            return None
        lines = (run.stdout or "").lstrip("﻿").splitlines()
        if run.returncode != 0 or not lines or lines[0].strip() != "OK":
            return None
        return [line[4:].strip() or None for line in lines[1:] if line.startswith("CMD")]


PROCESS_TABLE = _ProcessTable()
_UNLOCK_WAIT_S = 10.0   # how long a cleanup waits for a terminated Chrome to let go of its files
_UNLOCK_POLL_S = 0.5
_CLOSE_WAIT_S = 10.0    # [11 i] how long a gracefully closed Chrome may take to exit before its pid is terminated
CHROME_GONE_WAIT_S = 10.0  # [2026-10-08] how long a closed or terminated Chrome may take to be gone from the process
                           # table before its profile is marked cleanly exited (mark_profile_clean_exit)
DOWNLOAD_STALL_S = 300  # [11 i] a partial download that has not grown for this long is not running any more
                        # (the harvester's base timeout)


def _cmdline_args(cmdline):
    """The arguments of a Windows command line: whitespace splits them outside double quotes; the
    quotes themselves are dropped."""
    args, current, quoted, started = [], [], False, False
    for ch in cmdline:
        if ch == '"':
            quoted, started = not quoted, True
        elif ch.isspace() and not quoted:
            if started:
                args.append("".join(current))
                current, started = [], False
        else:
            current.append(ch)
            started = True
    if started:
        args.append("".join(current))
    return args


def _cmdline_user_data_dir(cmdline):
    """The --user-data-dir a command line names (normalised for comparison), or None."""
    for arg in _cmdline_args(cmdline or ""):
        if arg.lower().startswith("--user-data-dir="):
            return os.path.normcase(os.path.abspath(arg[len("--user-data-dir="):]))
    return None


def _chrome_still_ours(pid, user_data_dir):
    """True only when `pid` still runs AND its command line names exactly `user_data_dir` — the
    automation Chrome a fetch launched on that profile, not a process that reused its pid."""
    if not mvcommon._is_int(pid) or not isinstance(user_data_dir, str) or not user_data_dir:
        return False
    alive, cmdline = PROCESS_TABLE.query(pid)
    return (alive is True and cmdline is not None
            and _cmdline_user_data_dir(cmdline) == os.path.normcase(os.path.abspath(user_data_dir)))


def _chrome_runs_on(user_data_dir):
    """[IMP-C25 2026-10-08] Whether a Chrome browser runs on the profile folder `user_data_dir`: True when the
    command line of a chrome.exe BROWSER process names exactly that folder (the browser is the only process that
    writes the profile's Preferences; its renderer, GPU and crashpad helpers carry --type= and may outlive it for
    a moment), False when none does, None when that cannot be told (the process table, or one chrome.exe's command
    line, cannot be read) — never a guess."""
    cmdlines = PROCESS_TABLE.chrome_command_lines()
    if cmdlines is None:
        return None
    target = os.path.normcase(os.path.abspath(user_data_dir))
    unreadable = False
    for cmdline in cmdlines:
        if cmdline is None:
            unreadable = True
        elif (_cmdline_user_data_dir(cmdline) == target
              and not any(arg.lower().startswith("--type=") for arg in _cmdline_args(cmdline))):
            return True
    return None if unreadable else False


def _read_preferences(path):
    """A Chrome profile's Preferences file -> (doc, None), or (None, why not) when it is missing, unreadable or
    not a JSON object (nor its 'profile')."""
    try:
        with open(path, "rb") as fh:
            raw = fh.read()
    except FileNotFoundError:
        return None, f"{path} does not exist"
    except OSError as e:
        return None, f"{path} could not be read ({e})"
    try:
        doc = json.loads(raw)
    except ValueError:  # not JSON, or not text at all (UnicodeDecodeError is a ValueError)
        return None, f"{path} is not valid JSON"
    if not isinstance(doc, dict) or not isinstance(doc.get("profile", {}), dict):
        return None, f"{path} is not a JSON object"
    return doc, None


def _unclean_exit_recorded(doc):
    """Whether Chrome would read this Preferences document as an exit that was not clean — what makes it ask
    "Restore pages?": profile.exit_type "Crashed" (Chrome 154 leaves it even after a graceful CDP Browser.close —
    seen live 2026-10-08, without any exited_cleanly key) or the older profile.exited_cleanly false."""
    profile = doc.get("profile", {})
    return profile.get("exit_type") == "Crashed" or profile.get("exited_cleanly") is False


def _write_preferences(path, doc):
    """Write `doc` as a Chrome Preferences file — compact UTF-8 JSON, as Chrome writes it — atomically: a sibling
    temp file, fsynced, then os.replace; a failure leaves the previous file as it was and no temp file. Returns
    None, or why it was not written. (Python's JSON round trip keeps every value: key order, integers of any size,
    floats, strings; a value JSON cannot carry — NaN, an unpaired surrogate — is refused, never written.)"""
    try:
        data = json.dumps(doc, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")
    except ValueError as e:  # UnicodeEncodeError is a ValueError
        return f"{path} holds a value that cannot be written back ({e})"
    tmp = path + ".mediavault-tmp"
    try:
        with open(tmp, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except OSError as e:
        try:
            os.remove(tmp)
        except OSError:
            pass
        return f"{path} could not be written ({e})"
    return None


def _mark_clean_exit(user_data_dir, profile, wait):
    """mark_profile_clean_exit's steps -> (status, why it was left as it was — or None)."""
    path = os.path.join(user_data_dir, profile, "Preferences")
    doc, why = _read_preferences(path)
    if why or not _unclean_exit_recorded(doc):
        return ("skipped", why) if why else ("clean", None)
    deadline = time.time() + wait
    running = _chrome_runs_on(user_data_dir)
    while running is True and time.time() < deadline:
        time.sleep(_UNLOCK_POLL_S)
        running = _chrome_runs_on(user_data_dir)
    if running is True:
        return "running", None
    if running is None:
        return "skipped", f"it cannot be told whether a Chrome still runs on {user_data_dir}"
    doc, why = _read_preferences(path)  # read afresh: no Chrome runs on the profile now, so this is its last word
    if why or not _unclean_exit_recorded(doc):
        return ("skipped", why) if why else ("clean", None)
    doc["profile"].update(exit_type="Normal", exited_cleanly=True)
    why = _write_preferences(path, doc)
    return ("skipped", why) if why else ("marked", None)


def mark_profile_clean_exit(user_data_dir, profile=CHROME_PROFILE_NAME, wait=0):
    """[IMP-C25 — user request 2026-10-08: "make sure to close the restore small box which opens in chrome. in
    right top."] Chrome opens with the bubble "Restore pages? Chrome didn't shut down correctly." when the
    profile's Preferences record an exit that was not clean (_unclean_exit_recorded): after a terminate, a Ctrl+C
    or a crash — and, on Chrome 154, even after a graceful CDP Browser.close (seen live 2026-10-08). Chrome 154
    ignores --disable-session-crashed-bubble. This sets profile.exit_type "Normal" and profile.exited_cleanly true
    in <user_data_dir>/<profile>/Preferences:
      * only when an unclean exit is recorded — a clean profile is read once and nothing else happens;
      * only while no Chrome runs on that user-data-dir (_chrome_runs_on): one still running is waited for up to
        `wait` seconds (a Chrome that was just closed or terminated) and then left alone, as is a profile whose
        Chrome cannot be told to be gone — a running Chrome writes Preferences itself;
      * atomically (_write_preferences), every other key kept as it was;
      * a missing, unreadable or non-object Preferences file is left as it is, with one line.
    No other file of the profile is touched, and it never raises (a launch or a cleanup never stops on it).
    Called before every launch of an automation Chrome (init_driver — the fetch and the profile warm-up; the
    crawler's launch_chrome) and, with `wait`, once a Chrome that a run closed or terminated is gone
    (_close_session, _end_run, _startup_cleanup; the crawler's _session).
    Returns "clean" (nothing to do) · "marked" · "running" · "skipped" (one line said why)."""
    try:
        status, why = _mark_clean_exit(user_data_dir, profile, wait)
    except Exception as e:  # never a reason to stop a launch or a cleanup
        status, why = "skipped", f"{type(e).__name__}: {e}"
    # Plain cp1252 text, no emoji: the crawler shares this helper, and its output is often redirected — on Windows a
    # pipe or a file is cp1252, where an emoji raises UnicodeEncodeError.
    if why:
        print(f"   > WARNING: Chrome profile not marked as closed cleanly — {why}.")
    elif status == "marked":
        print(f'   > Chrome profile marked as closed cleanly (no "Restore pages?"): {user_data_dir}')
    return status


def read_fetch_journals():
    """[(path, doc)] of every fetch journal in FETCH_JOURNAL_DIR, oldest first (run ids sort by
    time); doc is None for a file that does not read as a journal. Read-only."""
    try:
        names = sorted(n for n in os.listdir(FETCH_JOURNAL_DIR) if n.endswith(".json"))
    except OSError:
        return []
    journals = []
    for name in names:
        path = os.path.join(FETCH_JOURNAL_DIR, name)
        try:
            with open(path, encoding="utf-8") as fh:
                doc = json.load(fh)
        except (OSError, ValueError):
            doc = None
        journals.append((path, doc if isinstance(doc, dict) and doc.get("run_id") else None))
    return journals


def _journal_unfinished(doc):
    return isinstance(doc, dict) and doc.get("state") not in _JOURNAL_DONE


def _journal_is_live(doc):
    """True while the run that writes `doc` may still be going: a 'running' journal whose process
    still runs mainfetch, or cannot be told apart — then it is left alone. (A journal of this very
    process is not live: one process runs one fetch at a time.)"""
    if doc.get("state") != "running":
        return False
    pid = doc.get("pid")
    if not mvcommon._is_int(pid) or pid == os.getpid():
        return False
    alive, cmdline = PROCESS_TABLE.query(pid)
    if alive is None:
        return True
    return alive and (cmdline is None or "mainfetch" in cmdline.lower())


def _journal_files(doc):
    """What a journal's run may have left behind, by P-12's rules — (incoming folders, pending
    paths, run-new expected-family files in ~/Downloads). The fallback part needs the journal's
    ~/Downloads snapshot (without one nothing there is touched) and reads ~/Downloads only then."""
    folders, seen = [], set()
    for entry in doc.get("entries") or []:
        path = entry.get("incoming") if isinstance(entry, dict) else None
        if isinstance(path, str) and path and not entry.get("cleaned") and os.path.normcase(path) not in seen:
            seen.add(os.path.normcase(path))
            folders.append(path)
    pending = [p for p in doc.get("pending_delete") or [] if isinstance(p, str) and p]
    downloads = []
    snapshot = doc.get("downloads_snapshot")
    family = sorted({name for entry in doc.get("entries") or [] if isinstance(entry, dict)
                     and entry.get("mode") == "fallback" for name in entry.get("names") or [] if isinstance(name, str)})
    if isinstance(snapshot, dict) and family:
        before = {_family_key(name) for name in snapshot}
        try:
            names = sorted(os.listdir(SYSTEM_DOWNLOADS_FOLDER))
        except OSError:
            names = []
        downloads = [os.path.join(SYSTEM_DOWNLOADS_FOLDER, n) for n in names
                     if _family_key(n) not in before and _in_family(n, family)
                     and os.path.isfile(os.path.join(SYSTEM_DOWNLOADS_FOLDER, n))]
    return folders, pending, downloads


def _may_delete_pending(path, doc):
    """A journal's pending path may go only if it still lies in a marked incoming folder, or is
    a run-new expected-family file directly in ~/Downloads (a fallback run's)."""
    folder = os.path.dirname(os.path.abspath(path))
    if _is_incoming_dir(folder):
        return True
    snapshot = doc.get("downloads_snapshot")
    if os.path.normcase(folder) != os.path.normcase(os.path.abspath(SYSTEM_DOWNLOADS_FOLDER)) \
            or not isinstance(snapshot, dict):
        return False
    name = os.path.basename(path)
    family = [n for e in doc.get("entries") or [] if isinstance(e, dict) and e.get("mode") == "fallback"
              for n in e.get("names") or [] if isinstance(n, str)]
    return _family_key(name) not in {_family_key(n) for n in snapshot} and _in_family(name, family)


def _clean_journal_files(doc, wait=None):
    """Delete what `doc`'s run left (see _journal_files), waiting up to `wait` seconds (default
    _UNLOCK_WAIT_S) for files a just-terminated Chrome still holds. Returns (deleted [(path,
    size)], still held [path])."""
    folders, pending, downloads = _journal_files(doc)
    deleted, held = [], []
    deadline = time.time() + (_UNLOCK_WAIT_S if wait is None else wait)
    while True:
        held = []
        for folder in folders:
            gone, locked = _empty_incoming(folder)
            deleted += [(os.path.join(folder, name), size) for name, size in gone]
            held += locked
            if not locked:
                _remove_incoming(folder)
        for path in pending + downloads:
            allowed = path in downloads or _may_delete_pending(path, doc)   # downloads: vetted by _journal_files
            if not allowed or not os.path.isfile(path):
                continue
            try:
                size = os.path.getsize(path)
            except OSError:
                size = 0
            if _delete_download(path):
                deleted.append((path, size))
            else:
                held.append(path)
        pending = [p for p in pending if p in held]
        downloads = [p for p in downloads if p in held]
        if not held or time.time() >= deadline:
            return deleted, held
        time.sleep(_UNLOCK_POLL_S)


def _startup_cleanup(ctx):
    """[IMP-C25 Step 11 e — P-12 rule 3] Before a fetch run's first Chrome session (inside the
    fetch lock): finish what an interrupted earlier fetch left. For every unfinished journal
    (not clean / recovered) that is not a fetch still running:
      * the Chrome it LAUNCHED is terminated — only a recorded pid that still runs AND whose
        command line names that session's profile folder (_chrome_still_ours); a Chrome that
        run only attached to (launched False) is never touched; [2026-10-08] the profile of a
        terminated Chrome is then marked cleanly exited (mark_profile_clean_exit);
      * the leftovers of every incoming folder it names (marked folders only), its pending files,
        and — for a ~/Downloads fallback run — ONLY files that appeared during that run and belong
        to its expected-name family are deleted; emptied incoming folders are removed;
      * it is marked recovered (or stays pending, listing what Chrome still holds, for the next run).
    One summary line when something was deleted. An unreadable journal is reported and left alone.
    Never raises (a cleanup must not stop the fetch)."""
    total, size, terminated, profiles = 0, 0, [], []
    for path, doc in read_fetch_journals():
        if doc is None:
            print(f"   > ⚠️ An unreadable fetch journal was left alone: {os.path.basename(path)}")
            continue
        if doc.get("run_id") == ctx.run_id or not _journal_unfinished(doc):
            continue
        try:
            if _journal_is_live(doc):
                continue
            killed = []
            for session in doc.get("sessions") or []:
                if not isinstance(session, dict) or session.get("launched") is not True:
                    continue  # only a Chrome that run LAUNCHED — never one it attached to (it may hold user tabs)
                pid = session.get("chrome_pid")
                if _chrome_still_ours(pid, session.get("user_data_dir")) and PROCESS_TABLE.terminate(pid):
                    killed.append(pid)
                    profiles.append(session["user_data_dir"])
            terminated += killed
            # waiting for files to be let go only makes sense once a Chrome was terminated
            deleted, held = _clean_journal_files(doc, wait=None if killed else 0)
        except Exception as e:  # a cleanup problem must never stop the fetch
            print(f"   > ⚠️ Could not clean up after the interrupted fetch {doc.get('run_id')}: {e}")
            continue
        total += len(deleted)
        size += sum(s for _, s in deleted)
        doc["pending_delete"] = held
        doc["state"] = "pending" if held else "recovered"
        doc["recovered_by"] = ctx.run_id
        doc["updated_at"] = _utc_now()
        try:
            _write_json_atomic(path, doc)
        except OSError as e:
            print(f"   > ⚠️ Could not update the fetch journal {os.path.basename(path)}: {e}")
        if held:
            print(f"   > ⏳ {len(held)} leftover file(s) of an interrupted fetch are still in use — the next fetch "
                  f"tries again.")
    for pid in terminated:
        print(f"   > 🛑 Closed the Chrome an interrupted fetch left running (pid {pid}).")
    for user_data_dir in profiles:  # [2026-10-08] once it is gone, no Chrome opened on it asks "Restore pages?"
        mark_profile_clean_exit(user_data_dir, wait=CHROME_GONE_WAIT_S)
    if total:
        print(f"   > 🧹 Cleaned {total} leftover file(s) from an interrupted fetch ({size / 1024 ** 3:.1f} GB)")


# ------------------------------------------------------------------ fetch_cleanup (Step 11 g)
def _size_of(path):
    try:
        return os.path.getsize(path)
    except OSError:
        return 0


def _library_family(library):
    """{base name (Windows case): (manual_id, label)} — the expected-name family of every object the
    library believes is in Google Photos (mvcommon.iter_uploaded_objects: alias-safe, extras
    included): its exact uploaded name."""
    family = {}
    for obj in mvcommon.iter_uploaded_objects(library):
        name = obj.get("uploaded_name")
        if isinstance(name, str) and name:
            family.setdefault(_family_key(name), (obj["manual_id"], obj["label"]))
    return family


def _library_incoming_folders(library):
    """<folder>\\restore\\.mv_incoming for every library entry's folder (a season's episodes share
    one) and every uploaded extras item's folder. Alias-safe (a multi_ep_alias has no folder of its
    own). Pure path arithmetic."""
    folders = {}
    for manual_id, entry in library.items():
        if not isinstance(entry, dict) or entry.get("type") == "multi_ep_alias":
            continue
        for folder in [entry.get("folder_path")] + [ex["folder_path"] for ex in build_extras_entries(entry, manual_id)]:
            if isinstance(folder, str) and folder:
                path = os.path.join(folder, RESTORE_DIR_NAME, INCOMING_DIR_NAME)
                folders.setdefault(os.path.normcase(path), path)
    return list(folders.values())


def fetch_cleanup_plan(library, check_live=False):
    """[IMP-C25 Step 11 g] What `fetch_cleanup` would delete — read-only: it writes, creates and
    deletes nothing, and starts no process unless `check_live` (--apply asks whether a journaled
    fetch still runs). The dict:
      files     [{"path", "size", "where", "reason"}]:
                where "downloads" — a file directly in ~/Downloads whose name belongs to the
                expected-name family of a library object believed to be in Google Photos (its exact
                uploaded name, Chrome's ' (n)' variant, the '.crdownload' partial of either): the
                leftovers of fetches from before IMP-C25;
                where "incoming" — every file in a MARKED incoming folder (_is_incoming_dir): under the
                library's restore folders, named by an unfinished journal (a tempdir location), or
                under the <drive>:\\MV_incoming root of a drive they use
      folders   the marked incoming folders found (--apply removes each once it is empty)
      skipped   folders found where an incoming folder would be, WITHOUT the marker — never touched
      journals  [{"path", "doc"}] — unfinished fetch journals (not clean / recovered)
      running   run ids of journals whose fetch still runs (check_live): their folders are left out
      lock      the fetch lock file exists (a fetch may be running)"""
    family = _library_family(library)
    plan = {"files": [], "folders": [], "skipped": [], "journals": [], "running": [],
            "lock": os.path.exists(mvcommon.FETCH_SESSION_LOCK)}
    try:
        names = sorted(os.listdir(SYSTEM_DOWNLOADS_FOLDER))
    except OSError:
        names = []
    for name in names:
        path = os.path.join(SYSTEM_DOWNLOADS_FOLDER, name)
        owner = next((family[base] for base in _family_bases(name) if base in family), None)
        if owner and os.path.isfile(path):
            plan["files"].append({"path": path, "size": _size_of(path), "where": "downloads",
                                  "reason": f"named like {owner[0]} ({owner[1]})"})
    candidates, live = _library_incoming_folders(library), set()
    for path, doc in read_fetch_journals():
        if doc is None or not _journal_unfinished(doc):
            continue
        named = [e["incoming"] for e in doc.get("entries") or []
                 if isinstance(e, dict) and isinstance(e.get("incoming"), str) and e["incoming"]]
        if check_live and _journal_is_live(doc):
            plan["running"].append(doc.get("run_id"))
            live.update(os.path.normcase(os.path.abspath(p)) for p in named)
            continue
        plan["journals"].append({"path": path, "doc": doc})
        candidates += named
    roots = {}
    for path in candidates:
        root = _short_incoming_root(path)
        if root:
            roots.setdefault(os.path.normcase(root), root)
    for root in roots.values():
        try:
            candidates += [os.path.join(root, sub) for sub in sorted(os.listdir(root))]
        except OSError:
            continue
    seen = set()
    for folder in candidates:
        key = os.path.normcase(os.path.abspath(folder))
        if key in seen or key in live or not os.path.isdir(folder):
            continue
        seen.add(key)
        if not _is_incoming_dir(folder):
            plan["skipped"].append(folder)
            continue
        plan["folders"].append(folder)
        try:
            names = sorted(os.listdir(folder))
        except OSError:
            continue
        for name in names:
            path = os.path.join(folder, name)
            if name != INCOMING_MARKER and os.path.isfile(path):
                plan["files"].append({"path": path, "size": _size_of(path), "where": "incoming",
                                      "reason": "unfinished fetch download"})
    return plan


def _recheck(listed):
    """Why a listed file may NOT be deleted now (None: it may) — re-checked right before each delete."""
    path = listed["path"]
    if not os.path.isfile(path):
        return "already gone"
    if _size_of(path) != listed["size"]:
        return "changed since it was listed"
    folder = os.path.dirname(os.path.abspath(path))
    if listed["where"] == "incoming":
        return None if _is_incoming_dir(folder) else "its folder is no longer a MediaVault download folder"
    same = os.path.normcase(folder) == os.path.normcase(os.path.abspath(SYSTEM_DOWNLOADS_FOLDER))
    return None if same else "not in Downloads any more"


def fetch_cleanup_apply(plan):
    """[IMP-C25 Step 11 g] Delete exactly the plan's files — each re-checked right before (_recheck:
    still there with the size listed, still where the rule allows) — then remove each listed
    incoming folder left empty, and mark each listed journal recovered once none of its folders or
    pending files remains. Returns {"deleted": [(path, size)], "kept": [(path, why)],
    "folders_removed": int, "journals_recovered": int}."""
    deleted, kept = [], []
    for listed in plan["files"]:
        why = _recheck(listed)
        if why:
            kept.append((listed["path"], why))
        elif _delete_download(listed["path"]):
            deleted.append((listed["path"], listed["size"]))
        else:
            kept.append((listed["path"], "in use"))
    removed = sum(1 for folder in plan["folders"] if os.path.isdir(folder) and _remove_incoming(folder))
    recovered = 0
    for journal in plan["journals"]:
        doc = journal["doc"]
        folders = [e.get("incoming") for e in doc.get("entries") or [] if isinstance(e, dict) and e.get("incoming")]
        left = [p for p in folders + list(doc.get("pending_delete") or []) if isinstance(p, str) and os.path.exists(p)]
        if left:
            continue
        doc.update(state="recovered", recovered_by="fetch_cleanup", pending_delete=[], updated_at=_utc_now())
        try:
            _write_json_atomic(journal["path"], doc)
            recovered += 1
        except OSError as e:
            kept.append((journal["path"], f"journal not updated: {e}"))
    return {"deleted": deleted, "kept": kept, "folders_removed": removed, "journals_recovered": recovered}


def print_fetch_cleanup(plan, apply=False):
    """The fetch_cleanup listing: name / path, size and reason of every file, then the totals."""
    size = mvcommon.human_readable_size
    print("=== FETCH CLEANUP — " + ("--apply: deleting exactly the files below ===" if apply
                                   else "dry run: nothing is deleted ==="))
    if plan["lock"] and not apply:
        print("   > ⚠️ A fetch seems to be running (its lock file exists) — its download folders may be listed "
              "below; run --apply only after it has finished.")
    downloads = [f for f in plan["files"] if f["where"] == "downloads"]
    incoming = [f for f in plan["files"] if f["where"] == "incoming"]
    print(f"   ~/Downloads — files named like a library object's upload (leftovers of earlier fetches): "
          f"{len(downloads)}")
    for f in downloads:
        print(f"     {os.path.basename(f['path'])} · {size(f['size'])} · {f['reason']}")
    print(f"   MediaVault download folders (.mv_incoming) — unfinished fetch downloads: {len(incoming)} file(s) "
          f"in {len(plan['folders'])} folder(s)")
    for f in incoming:
        print(f"     {f['path']} · {size(f['size'])} · {f['reason']}")
    if plan["skipped"]:
        print(f"   Not touched — no MediaVault marker in: {len(plan['skipped'])} folder(s)")
        for folder in plan["skipped"]:
            print(f"     {folder}")
    if plan["running"]:
        print(f"   Left alone — a fetch still running: {', '.join(str(r) for r in plan['running'])}")
    print(f"   Unfinished fetch journals: {len(plan['journals'])}")
    for journal in plan["journals"]:
        doc = journal["doc"]
        folders = sum(1 for e in doc.get("entries") or [] if isinstance(e, dict) and e.get("incoming"))
        print(f"     {doc.get('run_id')} · {doc.get('state')} · started {doc.get('started_at')} · "
              f"{folders} download folder(s)")
    total = sum(f["size"] for f in plan["files"])
    print(f"   Total: {len(plan['files'])} file(s), {size(total)}.")
    if not apply and (plan["files"] or plan["folders"] or plan["journals"]):
        print("   Delete exactly these with: python main.py fetch_cleanup --apply")


def print_fetch_cleanup_result(result):
    size = mvcommon.human_readable_size
    for path, why in result["kept"]:
        print(f"     ⏭️ Kept {path} — {why}")
    print(f"✅ Deleted {len(result['deleted'])} file(s) ({size(sum(s for _, s in result['deleted']))}); removed "
          f"{result['folders_removed']} empty download folder(s); {result['journals_recovered']} journal(s) "
          f"marked recovered.")


def _journal_sync(ctx, queue):
    """[11 d] Bring the current entry's object states up to date in the fetch journal (a no-op without one)."""
    if ctx.journal is not None and ctx.target is not None:
        ctx.journal.update_objects(ctx.target.get("journal"), queue)


def _note_session(ctx, driver, account):
    """[11 d] An account's Chrome session is up: remember the Chrome init_driver launched (its Popen
    handle rides on the driver — additive; a stand-in has none) and journal its pid together with
    the profile folder it runs on (the start-up cleanup terminates only that pid, and only while
    its command line still names that folder).

    When that chrome.exe has already exited, it handed the profile to a Chrome that was running
    before: the run only ATTACHED to that browser (launched False — it may hold the user's own
    tabs, so nothing ever closes or terminates it). Its pid — the process listening on
    init_driver's debug port 9222, when its command line names this profile — is recorded for the
    journal only. A stand-in without a Popen handle: launched None (unknown — treated as attached)."""
    proc = getattr(driver, "_mv_chrome_proc", None)
    pid = getattr(proc, "pid", None)
    user_data_dir = getattr(driver, "_mv_user_data_dir", None) or CHROME_PROFILES.get(account)
    poll = getattr(proc, "poll", None)
    launched = None
    if callable(poll):
        launched = poll() is None
        if not launched:
            try:
                listener = PROCESS_TABLE.listener(9222)
                pid = listener if _chrome_still_ours(listener, user_data_dir) else None
            except Exception:  # bookkeeping only: never a reason to stop the fetch
                pid = None
            proc = None
    ctx.chrome = {"account": account, "proc": proc, "pid": pid if mvcommon._is_int(pid) else None,
                  "user_data_dir": user_data_dir, "launched": launched}
    if ctx.journal is not None:
        ctx.journal.add_session(account, ctx.chrome["pid"], user_data_dir, launched)


def _terminate_session_chrome(ctx):
    """Terminate the running session's Chrome (ctx.chrome) and every process it started — only a
    Chrome this run LAUNCHED that still runs (the Popen handle init_driver kept: while it is open
    the pid cannot be reused) AND whose command line still names that profile (_chrome_still_ours,
    the start-up cleanup's guard). A Chrome the run attached to is never terminated. Returns the
    pid terminated, or None."""
    chrome = ctx.chrome or {}
    pid = chrome.get("pid")
    if chrome.get("launched") is not True or not mvcommon._is_int(pid):
        return None
    poll = getattr(chrome.get("proc"), "poll", None)
    if callable(poll) and poll() is not None:
        return None  # it has exited already
    if not _chrome_still_ours(pid, chrome.get("user_data_dir")):
        return None  # never a Chrome that is not ours
    return pid if PROCESS_TABLE.terminate(pid) else None


def _release_leftovers(ctx, wait):
    """Delete what this run left behind that Chrome no longer holds (_clean_journal_files over the
    run's own journal, waiting up to `wait` seconds); keeps ctx.pending_delete, the entries'
    cleaned flags and the journal in step. Returns (deleted [(path, size)], still held)."""
    journal = ctx.journal
    journal.doc["pending_delete"] = list(ctx.pending_delete)
    deleted, held = _clean_journal_files(journal.doc, wait=wait)
    ctx.pending_delete = held
    for entry in journal.doc["entries"]:
        if entry.get("incoming") and not entry.get("cleaned") and not os.path.isdir(entry["incoming"]):
            entry["cleaned"] = True
    journal.doc["pending_delete"] = list(held)
    journal.save()
    return deleted, held


def _session_partials(ctx, account):
    """{path: size} of the partial downloads ('.crdownload') a session still has: in the incoming
    folders its entries left behind (a download still running when its entry ended) and — after
    a ~/Downloads fallback — the partials of its entries' expected-name family that appeared
    during the run."""
    entries = [e for e in (ctx.journal.doc["entries"] if ctx.journal is not None else [])
               if isinstance(e, dict) and e.get("account") == account]
    partials = {}
    folders = [e["incoming"] for e in entries if e.get("incoming") and not e.get("cleaned")]
    for folder in folders:
        if _is_incoming_dir(folder):
            for name in _watched_names(folder, "incoming"):
                if name.endswith(_CRDOWNLOAD):
                    partials[os.path.join(folder, name)] = _size_of(os.path.join(folder, name))
    family = [n for e in entries if e.get("mode") == "fallback" for n in e.get("names") or []]
    if family and ctx.run_downloads is not None:
        try:
            names = os.listdir(SYSTEM_DOWNLOADS_FOLDER)
        except OSError:
            names = []
        for name in names:
            if (name.endswith(_CRDOWNLOAD) and _family_key(name) not in ctx.run_downloads
                    and _in_family(name, family)):
                partials[os.path.join(SYSTEM_DOWNLOADS_FOLDER, name)] = _size_of(
                    os.path.join(SYSTEM_DOWNLOADS_FOLDER, name))
    return partials


def _wait_downloads_idle(ctx, account):
    """[11 i] Never cancel a running transfer: before a session's Chrome is closed, wait while one
    of its downloads still runs — a partial (_session_partials) seen growing within the last
    DOWNLOAD_STALL_S seconds (a partial first seen counts as growing). One line says so."""
    seen, told = {}, False
    while True:
        now = time.time()
        partials = _session_partials(ctx, account)
        for path, size in partials.items():
            if path not in seen or seen[path][0] != size:
                seen[path] = (size, now)
        running = [p for p in partials if now - seen[p][1] < DOWNLOAD_STALL_S]
        if not running:
            return
        if not told:
            print(f"   > ⏳ Waiting for {len(running)} download(s) of this fetch to finish before Chrome is closed...")
            told = True
        time.sleep(5)


def _close_run_tabs(driver):
    """[11 i] In a Chrome this run did not launch: close only the tabs the run opened — the ones
    use_photos_tab opened (driver._mv_opened_tabs) and an about:blank tab (init_driver's launch
    command opens one in a Chrome that was already running; a blank tab holds nothing of the
    user's). Every other tab stays. Returns how many were closed; never raises."""
    opened = set(getattr(driver, "_mv_opened_tabs", None) or [])
    try:
        handles = list(driver.window_handles)
    except Exception:
        return 0
    try:
        infos = {i.get("targetId"): i for i in driver.execute_cdp_cmd("Target.getTargets", {})["targetInfos"]}
    except Exception:
        infos = {}
    closed = 0
    for handle in handles:
        info = infos.get(handle) or {}
        if handle in opened or (info.get("type") == "page" and info.get("url") == "about:blank"):
            try:
                driver.switch_to.window(handle)
                driver.close()
                closed += 1
            except Exception:
                pass
    return closed


def _chrome_exited(chrome, wait):
    """True once the Chrome this run launched has exited (its Popen handle), waiting up to `wait`
    seconds."""
    poll = getattr(chrome.get("proc"), "poll", None)
    if not callable(poll):
        return False
    deadline = time.time() + wait
    while poll() is None:
        if time.time() >= deadline:
            return False
        time.sleep(_UNLOCK_POLL_S)
    return True


def _close_session(driver, ctx, last):
    """[IMP-C25 Step 11 i] The end of one account's session — every way out but an interruption
    (_end_run terminates the Chrome at once then). User requirement 2026-10-04: "make sure the
    fetch also closes the chrome and tabs properly before finishing the task" (PLAN Step 11 i):
      1. never cancel a running transfer: wait until no download of the session runs
         (_wait_downloads_idle);
      2. a Chrome this run LAUNCHED: closed gracefully — CDP Browser.close, sent only when the
         browser Selenium drives is that Chrome (the process on the debug port; else only this
         run's tabs are closed in the browser it drives) — and, still running _CLOSE_WAIT_S later,
         terminated (_terminate_session_chrome: only when its command line names the profile).
         The debug port and the profile are free; the next fetch starts a fresh Chrome;
      3. a Chrome the run only ATTACHED to (already running): after the last account only the
         tabs this run opened are closed (_close_run_tabs) and it stays open — it may hold the
         user's own tabs; between two accounts its windows are closed as before (IMP-C26: the
         next account needs the debug port);
      4. what Chrome held is deleted now (_release_leftovers);
      5. [2026-10-08] once a Chrome this session closed (2, or its windows in 3) is gone, its profile is
         marked cleanly exited (mark_profile_clean_exit) — also after a graceful close.
    One line says what was closed; the journal records it (sessions[].closed). A driver without
    Chrome's DevTools protocol (a test stand-in, never a real Chrome) keeps today's ending: its
    windows are closed between two accounts, nothing else, nothing printed."""
    if not hasattr(driver, "execute_cdp_cmd"):
        if not last:
            _close_browser_windows(driver)
        return
    chrome = ctx.chrome or {}
    account = chrome.get("account")
    _wait_downloads_idle(ctx, account)
    pid, terminated = chrome.get("pid"), None
    if chrome.get("launched") is True:
        on_port = PROCESS_TABLE.listener(9222) == pid
        sent = False
        if on_port:
            try:
                driver.execute_cdp_cmd("Browser.close", {})
                sent = True
            except Exception:
                _close_browser_windows(driver)
        else:
            _close_run_tabs(driver)  # the browser Selenium drives is not the one we launched
        exited = _chrome_exited(chrome, _CLOSE_WAIT_S)
        if not exited:
            terminated = _terminate_session_chrome(ctx)
        closed = "chrome" if exited else ("terminated" if terminated else "open")
        if closed == "open":
            line = f"⚠️ The Chrome this fetch started ('{account}') did not close — close it before the next fetch."
        else:
            how = ("closed" if sent else "closed (its windows)") if exited else f"terminated (pid {terminated})"
            line = f"🧹 Chrome ('{account}') {how} — the next fetch starts a fresh one."
    elif not last:
        _close_browser_windows(driver)
        closed, line = "windows", (f"🧹 Closed the windows of the Chrome this fetch attached to ('{account}') — "
                                   f"the next account needs its debug port.")
    else:
        count = _close_run_tabs(driver)
        closed, line = "tabs", (f"🧹 Closed {count} tab(s) this fetch opened — the Chrome it attached to "
                                f"('{account}', already running before) stays open.")
    print(f"   > {line}")
    if closed in ("chrome", "terminated", "windows"):
        # [2026-10-08] Chrome 154 leaves its profile "Crashed" even after a graceful Browser.close (seen live): once
        # the Chrome this session closed is gone, its profile is marked cleanly exited, so neither the next fetch
        # nor a Chrome the user opens on it asks "Restore pages?". A Chrome left open is never marked.
        mark_profile_clean_exit(chrome.get("user_data_dir"), wait=CHROME_GONE_WAIT_S)
    if ctx.journal is not None:
        ctx.journal.end_session(closed)
        if ctx.pending_delete or any(not e.get("cleaned") for e in ctx.journal.doc["entries"]):
            _release_leftovers(ctx, wait=_UNLOCK_WAIT_S if closed in ("chrome", "terminated") else 0)


def _end_run(ctx, interrupted=False):
    """[IMP-C25 Step 11 f — P-12] The fetch run is over — finished, stopped on a problem line, or
    interrupted (Ctrl+C or an error; the session's driver has already quit).
      interrupted  the Chrome this run launched is terminated (_terminate_session_chrome: 'Chrome
                   terminated', so its downloads stop — a CDP cancel would need download ids from an
                   event channel, which the attached Selenium session does not have: PLAN P6; a
                   Chrome it only attached to is never terminated; [2026-10-08] its profile is then
                   marked cleanly exited — mark_profile_clean_exit), then this run's leftovers are
                   deleted — the files in every incoming folder it left, its pending files and, after
                   a ~/Downloads fallback, the files that appeared during the run and belong to an
                   entry's expected-name family (_clean_journal_files);
      otherwise    each session has closed its Chrome already (_close_session, 11 i) after its
                   downloads ended — whatever is still left is deleted now if Chrome has let go of
                   it; nothing is cancelled or terminated here;
    then the journal records the end: clean, or pending with what is still held — the next run's
    start-up cleanup finishes it. A problem here never stops the run's own ending (one warning)."""
    journal = ctx.journal
    if journal is None:
        return
    try:
        leftovers = interrupted or ctx.pending_delete or any(not e.get("cleaned") for e in journal.doc["entries"])
        if leftovers:
            terminated = _terminate_session_chrome(ctx) if interrupted else None
            if interrupted and ctx.chrome:
                journal.end_session("terminated" if terminated else "open")
            # waiting for Chrome to let go only makes sense once it was terminated
            deleted, held = _release_leftovers(ctx, wait=None if terminated else 0)
            if terminated:
                print(f"   > 🛑 Chrome terminated (pid {terminated}) — its downloads stopped.")
                # [2026-10-08] once it is gone, no Chrome opened on its profile asks "Restore pages?"
                mark_profile_clean_exit(ctx.chrome.get("user_data_dir"), wait=CHROME_GONE_WAIT_S)
            if deleted:
                size = sum(s for _, s in deleted)
                what = "of this interrupted fetch" if interrupted else "this fetch left"
                print(f"   > 🧹 Cleaned {len(deleted)} leftover file(s) {what} ({size / 1024 ** 3:.1f} GB)")
            if held:
                print(f"   > ⏳ {len(held)} file(s) are still in use — the next fetch deletes them.")
    except Exception as e:  # the run must end — its journal stays pending for the next start-up cleanup
        print(f"   > ⚠️ Cleaning up after this fetch failed ({type(e).__name__}: {e}) — the next fetch tries again.")
    journal.finish(ctx.pending_delete, interrupted)


def _watched_names(folder, mode):
    """The names in the watched folder. ~/Downloads (legacy, fallback) is listed exactly as before
    (an error propagates); an incoming folder that has gone reads as empty."""
    if mode != "incoming":
        return os.listdir(folder)
    try:
        return os.listdir(folder)
    except OSError:
        return []


def _harvestable(name, mode, target):
    """Whether the harvester looks at the finished file `name`: legacy — a .mkv / .mp4, as before;
    incoming — any file but the marker (chunks, FLAC holders, extras of any extension) and but an
    earlier entry's leftover Chrome still held when this entry began, or what that partial became
    (target["foreign"] — never this entry's download; deleted when the entry ends); fallback — a
    .mkv / .mp4 or a name of the entry's expected-name family."""
    if mode == "incoming":
        key = _family_key(name)
        foreign = target.get("foreign") or ()
        return name != INCOMING_MARKER and key not in foreign and key + _CRDOWNLOAD not in foreign
    if name.endswith(".mkv") or name.endswith(".mp4"):
        return True
    return mode == "fallback" and _in_family(name, target["family"])


def _delete_download(path):
    """Delete one file; True when it is gone. Never raises (a file Chrome still writes stays)."""
    try:
        os.remove(path)
        return True
    except FileNotFoundError:
        return True
    except OSError:
        return False


def _drop_unmatched(name, path, duplicate, cached, target, ctx):
    """[P-12] A finished file that matched no pending object, in the incoming or the fallback mode.
    Incoming: everything in .mv_incoming is disposable — a copy of an object already fetched is
    deleted as before ('Duplicate. Deleting.'), any other file is a wrong item: deleted and listed
    (name + size). Fallback: the same, but ONLY for a file that appeared during this run and belongs
    to the entry's expected-name family; anything else stays untouched — a run-new one is listed
    when the run ends. Each file is reported once per entry; a deleted file's name is free again, so
    a later download under it (the same wrong item fetched once more) is a new file."""
    if path in target["handled"]:
        return
    target["handled"].add(path)
    try:
        size = os.path.getsize(path)
    except OSError:
        size = 0
    deletable = target["mode"] == "incoming"
    run_new = False
    if target["mode"] == "fallback":
        run_new = ctx.run_downloads is not None and _family_key(name) not in ctx.run_downloads
        deletable = run_new and _in_family(name, target["family"])
    gone = _delete_download(path) if deletable else False
    if gone:
        target["handled"].discard(path)  # [live check 2026-10-05] ATTEMPT 2 re-downloaded the same wrong item
    if deletable and not gone:
        _add_pending(ctx, [path])  # Chrome still holds it: deleted once Chrome has closed
    if duplicate:
        if cached:
            print(f"\n   > 🔎 Checking: {name}")
        if deletable:
            print("     ⚠️ Duplicate. Deleting.")
        else:
            print("     ⚠️ Duplicate — left in Downloads (not provably this fetch's download).")
    elif deletable:
        what = "deleted" if gone else "still in use, deleted once Chrome has closed"
        print(f"     🗑️ Wrong item downloaded ({what}): {name} ({mvcommon.human_readable_size(size)})")
    if not deletable and run_new:
        ctx.left[name] = size


def _move_into_place(path, dest):
    """[P-12] Rename a verified download from the incoming folder to <dest>\\<expected filename>,
    replacing an existing file as before (os.remove + move): one rename on the same volume. A
    cross-volume rename (not expected: the folder sits on the destination's volume) copies
    instead; any other error propagates, as it did."""
    try:
        os.replace(path, dest)
    except OSError as e:
        if e.errno != errno.EXDEV:
            raise
        if os.path.exists(dest):
            os.remove(dest)
        shutil.move(path, dest)


def _left_in_downloads_line(ctx):
    """[P-12] The run's closing list of files that appeared in ~/Downloads during it (the fallback)
    and are still there, untouched — not provably this fetch's — or None."""
    left = [(name, size) for name, size in ctx.left.items()
            if os.path.exists(os.path.join(SYSTEM_DOWNLOADS_FOLDER, name))]
    if not left:
        return None
    files = ", ".join(f"{name} ({mvcommon.human_readable_size(size)})" for name, size in left)
    return f"   > 📋 Left in Downloads (appeared during this fetch, not provably its own): {files}"


# ==========================================
#   IMP-C25 STEP 10 — THE FETCH RUN, ITS QUEUE AND LOCATORS
# ==========================================
# docs/feature-fetch-datetime/PLAN.md Step 10. One fetch run = one FetchContext,
# handed to every stage: fetch_single_entry -> build_fetch_queue (today's queue +
# a `locator` per object) -> the exact-item stages (_run_locator_phase, IMP-C25
# Step 12) -> today's two legacy attempts. Later steps extend the context (Step 11
# the fetch journal, Step 12 the RPC client and record cache, Step 14 --manual).
# mainfetch stays library-READ-only: what a fetch learns goes to the learned
# sidecar (mvcommon.append_learned) and the apply engine folds it in afterwards.

class FetchContext:
    """What one fetch run shares across its accounts, entries and stages (`ctx`).

    run_id     'fetch-<UTC yyyymmddThhmmssZ>-<pid>' — the run named in the failed
               list (P-18) and, later, the fetch journal (Step 11)
    manual     --manual was given (parse_fetch_options; acted on by Step 14)
    account    the Google account (CHROME_PROFILES key) of the Chrome session now
               running; None outside a session (a stand-alone fetch_single_entry)
    downloads  {name: (size, mtime_ns)} of the Downloads folder when this session's
               first entry began — the files this session did NOT fetch (None until
               then; start_session resets it)
    hashed     {path: ((size, mtime_ns), sha256, sha1)} — every Downloads file
               hashed this run, so none is hashed twice (IMP-B10)
    stats      what the run counts: learned = records appended to the learned
               sidecar; retried / recovered = lost downloads requested once more /
               that then arrived; lost_twice = objects whose download was lost
               twice (IMP-C34)
    plans      {_entry_key: {"accounts": [session order], "by_sha": {sha256:
               account}, "default": account}} — the entries whose objects live in
               more than one account (_account_groups): each of those sessions
               fetches only its own objects of the entry
    reasons    {account: {"override" | "learned" | "prefix"}} — why the batch's
               objects were routed to each account (the Switching line)
    [IMP-C25 Step 11 — download hygiene, P-12]
    target     where the CURRENT entry's downloads land — set by _prepare_downloads
               before its first request, cleared at its end (_finish_downloads):
               {"mode": "incoming" | "fallback" | "legacy", "dir": the folder the
               harvester watches, "dest", "incoming": the entry's incoming dir or
               None, "family": its expected-name family, ...}; None outside an entry
    redirect_refused  Chrome refused the CDP download redirect: every later entry of
               the run uses the ~/Downloads fallback
    run_downloads  {name (Windows case): (size, mtime_ns)} of ~/Downloads when the run
               first used the fallback — a name not in it appeared during the run
    left       {name: size} — files that appeared in ~/Downloads during the run (the
               fallback) and were neither moved nor deleted: listed when the run ends
    pending_delete  [path] — files the run had to delete but Chrome still held (a
               download still running when its entry ended): retried once Chrome is
               closed, else by the next run's start-up cleanup
    journal    the run's FetchJournal (cmd_fetch_route opens it); None for a
               stand-alone fetch_single_entry
    chrome     the running session's launched Chrome: {"account", "proc" (the Popen
               init_driver kept, or None), "pid", "user_data_dir"}; None outside one
    """

    def __init__(self, manual=False, run_id=None):
        self.run_id = run_id or f"fetch-{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}-{os.getpid()}"
        self.manual = bool(manual)
        self.account = None
        self.downloads = None
        self.hashed = {}
        self.stats = {"learned": 0, "retried": 0, "recovered": 0, "lost_twice": 0}
        self.plans = {}
        self.reasons = {}
        self.target = None
        self.redirect_refused = False
        self.run_downloads = None
        self.left = {}
        self.pending_delete = []
        self.journal = None
        self.chrome = None
        self.hinted = False  # the incoming-mode "Chrome ignored the folder?" hint was printed

    def start_session(self, account):
        """A Chrome session for `account` is up: the run now fetches in it."""
        self.account = account
        self.downloads = None  # snapshot again: an earlier session's files are not this one's

    def plan_for(self, entry, entry_id):
        """The entry's multi-account plan when THIS session is one of its accounts, else None (the session
        fetches the whole entry — today's flow)."""
        plan = self.plans.get(_entry_key(entry, entry_id))
        return plan if plan is not None and self.account in plan["accounts"] else None

    def switch_reason(self, account):
        """Why the batch switches to `account` — the Switching line's parenthesis (IMP-C26's text when the
        overrides split the batch)."""
        reasons = self.reasons.get(account, set())
        named = [text for key, text in (("override", "fetch_account_overrides"), ("learned", "learned account"))
                 if key in reasons]
        if named:
            return " + ".join(named)
        return "fetch_account_overrides" if _account_overrides() else "learned account"


def _entry_key(entry, entry_id):
    """The identity of one fetch entry across the account sessions of a run: its id (a synthetic extra: its title
    id) with its folder and file name."""
    return (entry_id or entry.get("manual_id"), entry.get("folder_path"), entry.get("filename"))


# The objects a library leaf uploads itself (a movie leaf's extras are fetched as
# entries of their own — build_extras_entries).
_OWN_OBJECT_KINDS = ("whole", "chunk", "holder")


def _entry_objects(entry, owner_id):
    """{sha256: object} for the objects ONE fetch entry uploads, as
    mvcommon.iter_entry_objects describes them (trusted block, capture facts,
    exact uploaded name, label): a library leaf's whole file, or its chunks and
    FLAC holders. For a build_extras_entries synthetic entry the same objects
    carry the title's extras naming ('extra_whole' / 'extra_chunk',
    'extra <group_rel>/<sub_rel>[ chunk n/N]'), exactly as the mapper and the
    apply engine name them. Reads the entry's capture file; never the library."""
    extra = entry.get("label") if "group_rel" in entry else None
    objects = {}
    for obj in mvcommon.iter_entry_objects(owner_id or "", entry):
        if obj["kind"] not in _OWN_OBJECT_KINDS:
            continue
        if extra and obj["kind"] == "whole":
            obj = dict(obj, kind="extra_whole", label=extra)
        elif extra and obj["kind"] == "chunk":
            obj = dict(obj, kind="extra_chunk", label=f"{extra} {obj['label']}")
        if obj["sha256"]:
            objects.setdefault(obj["sha256"], obj)
    return objects


def _ms_from_date_utc(date_utc):
    """A DateUTC 'YYYY-MM-DDTHH:MM:SSZ' — the time Google Photos dates the item by
    (RESEARCH F1/F20) — as epoch milliseconds, or None."""
    try:
        when = datetime.strptime(date_utc, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None
    return int(when.timestamp()) * 1000


def _locator(item, obj, owner_id, kind, index, label, short_id, account):
    """The `locator` of one queue item — what the exact-item stages (Step 12),
    check_photos (Step 13) and the learned sidecar need to know about the object
    — from its TRUSTED gphotos block, else its capture facts (P-2, P-11); a key
    whose fact is unknown is None (lists are empty). `account` = the account of
    the session that fetches it (cmd_fetch_route runs each object in its routed
    account); without a session, the routed account. The shape is documented on
    build_fetch_queue."""
    block = obj["block"] if obj else None
    capture = (obj.get("capture") if obj else None) or {}
    facts = block or {}
    routed, reason = _object_account(owner_id or "", block)
    account = account or routed
    record = mvcommon.account_record(block, account) or {}
    approach = record.get("approach") if isinstance(record.get("approach"), dict) else {}
    override = facts.get("search_override")
    search_override = override.get("query") if isinstance(override, dict) else None
    pushed = facts.get("pushed") or capture.get("pushed") or {}
    sha1 = facts.get("sha1") or capture.get("sha1")
    date_utc = facts.get("date_utc") or capture.get("date_utc")
    taken = record.get("taken_ms")
    short_id = (obj or {}).get("short_id") or short_id
    category = mvcommon.category_for_id(owner_id) if owner_id else None
    uploaded = (obj or {}).get("uploaded_name") or gpweb.exact_name_query(
        {"filename": item["filename"], "short_id": short_id})
    title_query = gpweb.title_short_id_query(item["filename"], short_id, category)
    return {
        "manual_id": owner_id, "kind": kind, "index": index, "label": label, "sha256": item["hash"],
        "category": category, "short_id": short_id, "filename": item["filename"], "uploaded_name": uploaded,
        "cloud_state": (obj or {}).get("cloud_state"),
        "sha1": sha1, "dedup_key": facts.get("dedup_key") or capture.get("dedup_key"),
        "hash_b64": gpweb.sha1_hex_to_std_b64(sha1) if sha1 else None,
        "date_utc": date_utc,
        "taken_ms": taken if isinstance(taken, int) else _ms_from_date_utc(date_utc),
        "tz_offset_ms": record.get("tz_offset_ms"),
        "device_tz_offset_min": pushed.get("device_tz_offset_min"),
        "account": account,
        "route": {"account": routed, "reason": reason},
        "photo_id": record.get("photo_id"),
        "alternates": list(record.get("alternates") or []),
        "approach_best": approach.get("best") or capture.get("approach_hint"),
        "search_override": search_override,
        "queries": {"s2": [uploaded] if uploaded else [],
                    "s3": [title_query] if title_query else [],
                    "s3u": [search_override] if search_override else [],
                    "s4": [[item["specific_query"], 0], [item["fallback_query"], item["fallback_index"]]]},
    }


def build_fetch_queue(entry, restore_folder, ctx=None, entry_id=None):
    """[IMP-C25 Step 10 b] The fetch queue of ONE entry: each uploaded object whose
    file is not in `restore_folder` yet, in today's order — a split entry's chunks,
    then its FLAC holders; otherwise the whole file.

    Each item carries today's keys and values, unchanged (pinned against a frozen
    copy of the old inline builder):
      filename, hash, dest, specific_query, fallback_query, fallback_index,
      status ('pending'; the stages set 'done' or 'failed')
    plus `locator` (_locator) for the exact-item stages:
      manual_id   the owning library id (entry_id; a synthetic extra names its
                  title in entry['manual_id']) — None when unknown
      kind, index, label   whole | chunk | holder | extra_whole | extra_chunk,
                  1-based position, 'whole' · 'chunk 2/3' · 'holder' ·
                  'extra Specials/BTS.mkv' (mvcommon's object naming)
      sha256      = the item's hash (the P-2 pin)
      category, short_id, filename, uploaded_name (the exact name in Google
                  Photos), cloud_state (mvcommon: confirmed | unverified |
                  uploaded | local)
      sha1, dedup_key, hash_b64   the content key (block, else capture); hash_b64
                  = the swbisb lookup form (standard base64, padded)
      date_utc, taken_ms, tz_offset_ms, device_tz_offset_min   the taken second
                  (learned record, else from DateUTC) and the offsets known
      account     the account whose session fetches it; route {account, reason}
                  = P-4's answer (override | learned | prefix)
      photo_id, alternates, approach_best   that account's learned record
                  (approach_best falls back to the capture's approach_hint)
      search_override   the user's failed-list search term (stage S3u) or None
      queries     {s2: [exact uploaded name], s3: [title + short_id],
                   s3u: [search_override], s4: [[query, index] x today's two
                   attempts]}
    entry_id = the resolved library id of a target. Reads the entry's capture
    file; never prints and never touches the library or the browser."""
    fallback_term = entry.get("search_term")
    if not fallback_term: fallback_term = entry["filename"]
    extra = "group_rel" in entry  # a build_extras_entries synthetic entry
    queue, names = [], []  # names: the (kind, index, label) used when no object matches

    if entry.get("split_info") and entry["split_info"].get("is_split"):
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
            names.append(("extra_chunk" if extra else "chunk", i + 1, f"chunk {i + 1}/{len(chunks)}"))

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
            names.append(("holder", None, "holder"))
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
            names.append(("extra_whole" if extra else "whole", None, "whole"))

    owner_id = entry_id or entry.get("manual_id")
    objects = _entry_objects(entry, owner_id) if queue else {}
    account = ctx.account if ctx is not None else None
    for item, (kind, index, label) in zip(queue, names):
        obj = objects.get(item["hash"])
        if obj:
            kind, index, label = obj["kind"], obj["index"], obj["label"]
        elif extra:
            label = entry["label"] if kind == "extra_whole" else f"{entry['label']} {label}"
        item["locator"] = _locator(item, obj, owner_id, kind, index, label, entry.get("short_id"), account)
    return queue


def _run_locator_phase(driver, queue, ctx):
    """[IMP-C25 Step 10 d — the seam IMP-C25 Step 12 fills] The exact-item stages,
    run before today's two legacy attempts when EXACT_LOCATOR_ENABLED (P-14).
    `queue` = the items this session fetches (build_fetch_queue, each with its
    locator; ctx.account = the session's account). Until Step 12 lands: nothing.

    The contract Step 12 fills it to:
      * request each download through _fire(item, fire) — fire() -> truthy when a
        download was requested — so IMP-C34's one retry covers it, then harvest
        with harvest_downloads(queue, ctx) and book losses with
        _note_losses(queue, <its result>, ctx);
      * name what it requested in item['_trigger'] = {'method': 'direct' |
        'shift_d' | …, 'record': the item's record (gpweb page / RPC shape:
        photo_id, dedup_key, taken_ms, tz_offset_ms, upload_ms, width, height,
        filename), 'approach': {tried, outcome, n, via} (a P-10 approach)}: a
        SHA-256 match then teaches the learned sidecar that photo_id
        (_learn_from_download — only when the record's dedupKey is the bytes');
      * leave an object it settled 'done', one it may not hand to the legacy
        attempts (OD-5) 'failed'; every item still 'pending' afterwards gets
        today's two attempts unchanged;
      * a round in which a wrong item arrived marks each object requested in it
        `_wrong` (_note_losses); one still pending after the legacy attempts is
        booked wrong-item (_record_wrong_items) — an object a stage gives up on
        itself is 'failed' with the stage's own failed-list row, never booked twice."""
    return None


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


def _wrong_item_note(names):
    """[IMP-C25 live check 2026-10-05] What the harvester says at a timeout when, instead of a requested file,
    ANOTHER item arrived (`names`; the lines above say what became of each — in the incoming folder a wrong item is
    deleted): the transfer worked and the search picked something else, so this is no lost download and IMP-C34
    does not request it again (that would download the same item once more)."""
    return f"     Not a broken transfer: another item arrived instead ({', '.join(names)}) — it is not requested again."


def _downloads_snapshot():
    """{name: (size, mtime_ns)} of every file in the Downloads folder now; {} when
    it cannot be listed (the harvest loop reports that itself)."""
    snapshot = {}
    try:
        names = os.listdir(SYSTEM_DOWNLOADS_FOLDER)
    except OSError:
        return snapshot
    for name in names:
        try:
            st = os.stat(os.path.join(SYSTEM_DOWNLOADS_FOLDER, name))
        except OSError:
            continue
        snapshot[name] = (st.st_size, st.st_mtime_ns)
    return snapshot


def _arrived_this_session(name, path, ctx):
    """True when the Downloads file `name` was not there — as it is now — when this
    session's snapshot was taken (ctx.downloads): this Chrome session fetched it,
    so what it proves can be credited to the session's account."""
    if ctx.downloads is None:
        return False
    before = ctx.downloads.get(name)
    if before is None:
        return True
    try:
        st = os.stat(path)
    except OSError:
        return False
    return before != (st.st_size, st.st_mtime_ns)


def _hash_download(name, path, ctx):
    """(sha256, sha1, cached) of one finished download, hashed at most once per run
    (IMP-B10): a file met before — same path, size and mtime — reuses ctx.hashed;
    a new one gets today's `🔎 Checking:` line and calculate_file_hash, and its
    SHA-1 (computed in the same read pass) is taken before the file can move."""
    try:
        st = os.stat(path)
        stamp = (st.st_size, st.st_mtime_ns)
    except OSError:
        stamp = None
    known = ctx.hashed.get(path)
    if stamp is not None and known is not None and known[0] == stamp:
        return known[1], known[2], True
    print(f"\n   > 🔎 Checking: {name}")
    fhash = calculate_file_hash(path)
    sha1 = mvcommon.cached_sha1(path) if fhash else None
    if fhash and stamp is not None:
        ctx.hashed[path] = (stamp, fhash, sha1)
    return fhash, sha1, False


def _learn_from_download(item, sha1, ctx):
    """[IMP-C25 Step 10 c] Learn-on-fetch (PLAN D4, P-10): append what a SHA-256-
    verified download proved to the learned sidecar of the session's account — the
    object (manual_id, sha256, label), proof sha256, source fetch, its content key
    (the dedupKey of the verified bytes' SHA-1) and — ONLY when an exact-item
    stage's trigger record (item['_trigger']['record'], IMP-C25 Step 12) names an
    item whose dedupKey is exactly that key — that item's photo_id and facts, with
    the stage's approach verdict when it gives one (item['_trigger']['approach']).
    The apply engine folds the record in after the run (main.merge_learned_gphotos).
    Silent; a failed write prints one warning line and the fetch goes on. Nothing is
    learned outside a session or for an object without an owning id."""
    loc = item.get("locator") or {}
    if ctx.account not in mvcommon.GP_ACCOUNTS or not loc.get("manual_id"):
        return
    key = gpweb.dedup_key(sha1) if sha1 else None
    record = {"manual_id": loc["manual_id"], "label": loc.get("label"), "sha256": item["hash"],
              "proof": "sha256", "source": "fetch", "dedup_key": key}
    trigger = item.get("_trigger") if isinstance(item.get("_trigger"), dict) else {}
    found = trigger.get("record") if isinstance(trigger.get("record"), dict) else {}
    if key and found.get("photo_id") and gpweb.dedup_keys_equal(found.get("dedup_key"), key):
        record["photo_id"] = found["photo_id"]
        for k in ("taken_ms", "tz_offset_ms", "upload_ms", "width", "height"):
            record[k] = found.get(k)
        record["gp_filename"] = found.get("gp_filename") or found.get("filename")
        if isinstance(trigger.get("approach"), dict):
            record["approach"] = trigger["approach"]
    try:
        mvcommon.append_learned(ctx.account, record)
    except (OSError, ValueError) as e:
        print(f"     ⚠️ Not recorded in the learned sidecar ({type(e).__name__}: {e}) — "
              f"the file itself is fetched.")
        return
    ctx.stats["learned"] += 1


def harvest_downloads(queue, ctx):
    """[IMP-C25 Step 10 c] One harvest round over the Downloads folder — today's
    loop, its timeouts, extensions and prints (IMP-C29: the timeout is decided
    after the scan): every finished .mkv / .mp4 is SHA-256-routed to a pending
    queue item and moved into its `dest` (`✅ MOVED:`); a copy of an item already
    fetched is deleted; anything else is left alone. Returns once no item is
    pending, or once the base timeout has passed with nothing downloading and
    nothing new — then IMP-C29's note names the requested files that never came
    (when a request fetched ANOTHER item instead, _wrong_item_note says that: the
    file came, the search picked the wrong item — live check 2026-10-05).

    Step 10 adds, silently:
      * IMP-B10 — every file is hashed at most once per run (_hash_download): one
        met in an earlier round or entry is re-checked against this queue without
        re-hashing, and named again only when it now matches or duplicates an item;
      * learn-on-fetch — a match that arrived during this Chrome session is
        recorded in the learned sidecar (_learn_from_download). A file that was
        already there may come from another account's session: it is still
        moved, but proves nothing about this account.
    Returns, for the caller's bookkeeping (IMP-C34):
      {"timed_out": bool,     the round ended on the timeout, nothing downloading
       "saw_download": bool,  a .crdownload was seen during the round (IMP-C29)
       "unmatched": int}      files that ARRIVED in this round (new to the session)
                              and matched no pending item — a request fetched a
                              wrong item (another one, or one already fetched)

    [IMP-C25 Step 11 — P-12] The folder it watches is the entry's (ctx.target, set by
    _prepare_downloads; none = legacy):
      incoming  only the entry's incoming dir — ~/Downloads is never read. Every
                finished file of any extension arrived in this session (a multi-
                account entry's folder is emptied when each session's part ends);
                a match is renamed into its `dest`, a copy of a fetched item and any
                other file are deleted (the other file listed with its size) — a
                deleted wrong item still counts in "unmatched". The Watching line
                names the folder.
      fallback  ~/Downloads as below, but a file is deleted ONLY when it appeared
                during this run, belongs to the entry's expected-name family and is
                not moved; other run-new files are listed when the run ends.
      legacy    ~/Downloads exactly as described above."""
    target = ctx.target or {"mode": "legacy", "dir": SYSTEM_DOWNLOADS_FOLDER}
    mode, folder = target["mode"], target["dir"]
    if mode == "incoming":
        print(f"   > Watching {folder} (Infinite wait if active)...")
    else:
        print("   > Watching Downloads (Infinite wait if active)...")
    start_time = time.time()
    base_timeout = 300  # 5 mins initial timeout

    processed_files = set()  # this round's files ([IMP-B10] their hashes outlive it in ctx.hashed)
    saw_download = False  # [IMP-C29] was a .crdownload ever seen during this wait?
    unmatched = 0  # [IMP-C34] arrivals of this round that matched no pending item
    wrong = []  # [live check 2026-10-05] their names: what the round's requests fetched instead

    while True:
        # Check Active Downloads
        active_downloads = [f for f in _watched_names(folder, mode) if f.endswith(".crdownload")]
        is_active = len(active_downloads) > 0
        saw_download = saw_download or is_active
        timed_out = time.time() - start_time > base_timeout

        if timed_out and is_active:
            print(f"   ⏳ Timeout reached, but {len(active_downloads)} files downloading. Extending wait...",
                  end="\r")
            time.sleep(5)
            continue  # Keep waiting

        # Check Completion ([IMP-C25] nothing pending: an item a stage gave up on is 'failed')
        if not any(i["status"] == "pending" for i in queue):
            return {"timed_out": False, "saw_download": saw_download, "unmatched": unmatched}

        found_new = False
        for f in _watched_names(folder, mode):
            if f.endswith(".crdownload") or not _harvestable(f, mode, target): continue

            fpath = os.path.join(folder, f)
            if fpath in processed_files: continue

            # Stability Check
            try:
                if os.path.getsize(fpath) == 0: continue
                time.sleep(0.5)
            except:
                continue

            fhash, sha1, cached = _hash_download(f, fpath, ctx)
            processed_files.add(fpath)
            # [IMP-C25 P-12] every file in the entry's incoming dir came from this session
            arrived = True if mode == "incoming" else _arrived_this_session(f, fpath, ctx)

            # Match
            matched = next((i for i in queue if i["hash"] == fhash and i["status"] == "pending"), None)
            if matched:
                if cached:
                    print(f"\n   > 🔎 Checking: {f}")
                dest = os.path.join(matched["dest"], matched["filename"])
                if mode == "incoming":
                    _move_into_place(fpath, dest)  # [IMP-C25 P-12] one rename on the destination's volume
                else:
                    if os.path.exists(dest): os.remove(dest)
                    shutil.move(fpath, dest)
                print(f"     ✅ MOVED: {matched['filename']}")
                matched["status"] = "done"
                found_new = True
                if arrived:
                    _learn_from_download(matched, sha1, ctx)
            else:
                if arrived and not cached:
                    unmatched += 1  # [IMP-C34] a request fetched a wrong item
                    wrong.append(f)
                duplicate = any(i["hash"] == fhash for i in queue)
                if mode != "legacy":
                    _drop_unmatched(f, fpath, duplicate, cached, target, ctx)  # [IMP-C25 P-12]
                # Duplicate check
                elif duplicate:
                    if cached:
                        print(f"\n   > 🔎 Checking: {f}")
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
            if lost and wrong:
                # [live check 2026-10-05] A request fetched ANOTHER item: it arrived, so it is no lost download.
                print(_wrong_item_note(wrong))
                lost = max(0, lost - len(wrong))
            if lost:
                print(_lost_download_note(lost, saw_download))
                if mode == "incoming" and not saw_download and not wrong and not ctx.hinted:
                    ctx.hinted = True  # [IMP-C25 P-12] the redirect is unverified live (PLAN P6): say where to look
                    print(f"     Chrome was told to save into {folder}. If chrome://downloads shows the file saved "
                          f"somewhere else, Chrome ignored that folder — `python main.py fetch_cleanup` lists "
                          f"such leftovers.")
            return {"timed_out": True, "saw_download": saw_download, "unmatched": unmatched}

        if not found_new:
            time.sleep(5)


# ==========================================
#   IMP-C34 — A LOST DOWNLOAD IS REQUESTED ONCE MORE
# ==========================================
# A Google Photos download cannot resume (no ETag / Last-Modified): a broken
# transfer shows "Failed - Network error" at 0 B and nothing arrives (IMP-C29).
# User ruling 2026-10-03 (DECISIONS.md §1e, PLAN Step 10 g): request such a
# download again ONCE per object per run — whole files, chunks, holders, extras —
# never while anything still downloads; print and count the retry; a second loss
# puts the object on the failed list (P-18). Every request goes through _fire, so
# the exact-item stages (IMP-C25 Step 12) get the same rule for their downloads.

def _fire(item, fire):
    """Request one object's download — fire() is truthy when a download was
    requested (trigger_download's contract) — and keep IMP-C34's books on the
    item: `triggered` (requested in this round, IMP-C29), `_retrigger` (how to
    request it again) and `_retriggered` (this request follows a lost download:
    the object's one re-request is used). Returns what fire() returned."""
    item["triggered"] = fire()
    if item["triggered"]:
        item["_retrigger"] = fire
        if item.get("_losses"):
            item["_retriggered"] = True
    return item["triggered"]


def _record_failed(item, ctx, reason, tried=None):
    """One failed-list row (P-18) for an object this run gives up on: run, account,
    manual_id, label, uploaded_name, the sha256 pin, `reason` and — when given —
    what was `tried` ([{approach, outcome, n}]). True when written. Needs the
    session account and the owning id — a stand-alone call has neither and writes
    nothing; a failed write prints one warning line."""
    loc = item.get("locator") or {}
    if ctx.account not in mvcommon.GP_ACCOUNTS or not loc.get("manual_id"):
        return False
    sha256 = item.get("hash") if mvcommon._is_sha256(item.get("hash")) else None
    try:
        mvcommon.append_failed({"run": ctx.run_id, "account": ctx.account, "manual_id": loc["manual_id"],
                                "label": loc.get("label"), "sha256": sha256,
                                "uploaded_name": loc.get("uploaded_name"), "tried": tried, "reason": reason})
    except (OSError, ValueError) as e:
        print(f"     ⚠️ Not recorded in the failed list ({type(e).__name__}: {e}).")
        return False
    return True


def _record_lost_twice(item, ctx):
    """[IMP-C34] The failed-list row (P-18, reason download-lost) of an object whose
    download was lost twice (_record_failed)."""
    return _record_failed(item, ctx, "download-lost")


def _note_losses(queue, rnd, ctx):
    """[IMP-C34] After a harvest round (`rnd`, harvest_downloads' result): one LOSS
    for every pending object whose download was requested in that round and never
    came — counted only when the round ended on the timeout with nothing
    downloading and nothing that arrived went unmatched. A file that arrived and
    matched no pending object means a request fetched a WRONG item, which no
    re-request fixes (and which request it was cannot be told), so such a round
    counts no loss: each object requested in it is marked `_wrong` instead (the
    rounds in which that happened; _record_wrong_items books an object that ends
    unfetched — live check 2026-10-05). A second loss ends the object for this
    run: status 'failed', one line, one failed-list row."""
    if rnd.get("unmatched"):
        for item in queue:
            if item["status"] == "pending" and item.get("triggered"):
                item["_wrong"] = item.get("_wrong", 0) + 1
        return
    if not rnd.get("timed_out"):
        return
    for item in queue:
        if item["status"] != "pending" or not item.get("triggered"):
            continue
        item["_losses"] = item.get("_losses", 0) + 1
        if item["_losses"] < 2:
            continue
        item["status"] = "failed"
        ctx.stats["lost_twice"] += 1
        listed = "added to the failed list; " if _record_lost_twice(item, ctx) else ""
        print(f"     ❌ Download lost twice: {item['filename']} — {listed}re-run the fetch to try again.")


def _retry_lost_downloads(queue, ctx):
    """[IMP-C34] Request again — ONCE — every download that was lost and has not been
    requested since (e.g. a whole file whose only finding attempt, ATTEMPT 2, lost
    its transfer; a chunk lost in ATTEMPT 1 was already requested again by ATTEMPT
    2). It repeats the request that started the lost download (`_retrigger`), so
    each retry is a full re-download. Runs after the last round, which ended with
    nothing downloading, then harvests one more round; a second loss ends the
    object (_note_losses)."""
    retry = [i for i in queue if i["status"] == "pending" and i.get("_losses") == 1
             and not i.get("_retriggered") and i.get("_retrigger")]
    if not retry:
        return
    print(f"\n   === RETRY ({len(retry)} file(s) whose download was lost — requested once more) ===")
    for item in queue:
        if item["status"] == "pending":
            item["triggered"] = False  # only this round's requests count
    for item in retry:
        ctx.stats["retried"] += 1
        _fire(item, item["_retrigger"])
        time.sleep(2)
    _note_losses(queue, harvest_downloads(queue, ctx), ctx)
    ctx.stats["recovered"] += sum(1 for i in retry if i["status"] == "done")


def _record_wrong_items(queue, ctx):
    """[IMP-C25 live check 2026-10-05] After every attempt (and IMP-C34's retry): an
    object still pending whose request fetched ANOTHER item (`_wrong`, booked by
    _note_losses) ends 'failed' — one line and one failed-list row (P-18, reason
    wrong-item; tried = the legacy attempts' outcome, n = the rounds that fetched a
    wrong item). Running the same search again would pick the same item; the object
    needs its exact item (the failed list)."""
    for item in queue:
        if item["status"] != "pending" or not item.get("_wrong"):
            continue
        item["status"] = "failed"
        tried = [{"approach": "legacy", "outcome": "wrong-item", "n": item["_wrong"]}]
        listed = "; added to the failed list (wrong-item)" if _record_failed(item, ctx, "wrong-item", tried) else ""
        print(f"     ❌ Not fetched: {item['filename']} — the search picked another item{listed}.")


def _lost_downloads_line(ctx):
    """[IMP-C34] The run's one closing line about lost downloads, or None when
    nothing was requested again and nothing was lost twice."""
    s = ctx.stats
    if not (s["retried"] or s["lost_twice"]):
        return None
    return (f"   > 🔁 Lost downloads: {s['retried']} requested once more ({s['recovered']} arrived); "
            f"{s['lost_twice']} lost twice — see the failed list.")


def _fetch_items(driver, queue, ctx, entry=None, owner_id=None):
    """Fetch the pending items of one entry in the running session: [IMP-C25 Step 11]
    first where its downloads land (_prepare_downloads — the disk pre-flight may refuse
    the entry: then nothing is requested), the exact-item stages, today's two legacy
    attempts, IMP-C34's one retry, the wrong-item booking (_record_wrong_items) and
    last the end of the entry's downloads (_finish_downloads). `entry` / `owner_id`
    size the pre-flight."""
    if not _prepare_downloads(driver, queue, ctx, entry, owner_id):
        return

    # [IMP-C25] What is in Downloads before this session requests anything: no file
    # that was already there may teach the learned sidecar about this account.
    # ([Step 11] Not in the incoming mode: every file there arrived in this session,
    # and ~/Downloads is never read.)
    if ctx.target["mode"] != "incoming" and ctx.downloads is None:
        ctx.downloads = _downloads_snapshot()

    # [IMP-C25 P-14] The exact-item stages first (a no-op until Step 12); today's two
    # attempts below run unchanged for whatever is still pending.
    if EXACT_LOCATOR_ENABLED:
        _run_locator_phase(driver, queue, ctx)
        _journal_sync(ctx, queue)  # [IMP-C25 Step 11 d] each object's state, as it changes

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
            # ([IMP-C34] _fire also keeps how to request it again.)
            _fire(item, lambda query=query, idx=idx: trigger_download(driver, query, idx))
            time.sleep(2)
        _journal_sync(ctx, queue)

        # Harvest
        _note_losses(queue, harvest_downloads(queue, ctx), ctx)
        _journal_sync(ctx, queue)

    # [IMP-C34] A download lost and not requested since gets its one retry.
    _retry_lost_downloads(queue, ctx)
    # [live check 2026-10-05] An object whose requests fetched only other items ends on the failed list.
    _record_wrong_items(queue, ctx)
    _journal_sync(ctx, queue)

    # [IMP-C25 Step 11] Only now — after the retry's harvest — the entry's downloads end.
    _finish_downloads(driver, ctx)


def fetch_single_entry(driver, entry, temp_dir=None, entry_id=None, ctx=None):
    """
    Handles the fetch logic for a single library entry (Movie or Episode).
    Refactored to use PARALLEL TRIGGER + HARVESTER for large files.
    [IMP-C25 Step 10] ctx = the run's FetchContext (cmd_fetch_route passes one;
    a stand-alone call gets a fresh one); the queue comes from build_fetch_queue.
    An entry whose objects live in several Google accounts (ctx.plans) is fetched
    in each of those sessions, each taking only its own objects: the first prints
    today's header (the web UI counts it), a later one a CONTINUING header; the
    last one gives the entry's verdict over every object, the others say what is
    left for the next session.
    """
    ctx = ctx if ctx is not None else FetchContext()
    plan = ctx.plan_for(entry, entry_id)
    first = plan is None or plan["accounts"][0] == ctx.account
    last = plan is None or plan["accounts"][-1] == ctx.account
    if first:
        print(f"\n🔹 PROCESSING: {entry['filename']} ({entry.get('short_id', 'N/A')})")
    else:
        print(f"\n🔸 CONTINUING: {entry['filename']} ({entry.get('short_id', 'N/A')}) — "
              f"its file(s) in the '{ctx.account}' account")

    restore_folder = _fetch_restore_folder(entry, temp_dir, entry_id)
    os.makedirs(restore_folder, exist_ok=True)

    # 1. Build Queue
    if first and entry.get("split_info") and entry["split_info"].get("is_split"):
        print(f"   > Detected Split File ({entry['split_info']['total_chunks']} chunks)")
    queue = build_fetch_queue(entry, restore_folder, ctx, entry_id=entry_id)
    mine = queue if plan is None else [i for i in queue
                                       if plan["by_sha"].get(i["hash"], plan["default"]) == ctx.account]
    if plan is not None and first:
        elsewhere = sum(1 for account in plan["by_sha"].values() if account != ctx.account)
        print(f"   > [Account] {elsewhere} of its {len(plan['by_sha'])} file(s) live in another account — "
              f"fetched in the {_session_names(plan['accounts'][1:])} session.")

    if mine:
        _fetch_items(driver, mine, ctx, entry, entry_id or entry.get("manual_id"))
    elif plan is None:
        print("   ✅ All files already exist.")
        return
    else:
        print("   ✅ Nothing left to fetch in this account.")

    # Final Report
    if not last:
        later = plan["accounts"][plan["accounts"].index(ctx.account) + 1:]
        left = len(queue) - len(mine)
        print(f"\n   ⏸️ {left} file(s) left for the {_session_names(later)} session.")
        return
    if all(i["status"] == "done" for i in queue):
        print("\n   ✅ ENTRY COMPLETE.")
    else:
        print("\n   ❌ ENTRY INCOMPLETE.")


def _session_names(accounts):
    """'tv' · 'tv' and 'anime' — account sessions named in fetch_single_entry's lines."""
    return " and ".join(f"'{account}'" for account in accounts)


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


def build_extras_entries(title_entry, title_id=None):
    """Flatten a title entry's extras.groups into synthetic leaf-shaped fetch
    entries — one per CLOUD-RESIDENT (uploaded=True) extra item — that
    fetch_single_entry / build_fetch_queue consume VERBATIM.

    Each synthetic entry's `folder_path` is the extra's own on-disk folder
    (os.path.dirname(<title folder_path>/<group_rel>/<sub_rel>)), so the proven
    fetch loop stages its download into <that folder>/restore/. A WHOLE-file
    extra carries the item `hash`; a SPLIT extra carries `split_info`
    (is_split / chunks[{filename, hash}]) so each chunk is queued by hash —
    identical to a split leaf. Items not yet uploaded are skipped (nothing to
    fetch in the cloud). PURE: no library / browser / device I/O.

    [IMP-C25 Step 10] Each entry also forwards what names its objects for the
    exact-item stages and the learned sidecar: the item's `gphotos` block (a
    split item's chunk blocks travel inside its split_info), `group_rel`,
    `sub_rel`, `label` ('extra <group_rel>/<sub_rel>', the label
    mvcommon.iter_entry_objects gives the item) and `manual_id` = title_id —
    an extra's objects are owned by its TITLE id (None when not given).
    [IMP-C25 Step 11] ... and the item's `tech_spec` (its size, for the
    per-entry disk pre-flight — P-12 rule 5)."""
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
                "gphotos": item.get("gphotos"),
                "tech_spec": item.get("tech_spec"),
                "manual_id": title_id,
                "group_rel": group_rel,
                "sub_rel": sub_rel,
                "label": f"extra {group_rel}/{sub_rel}",
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
    set.) [IMP-C25] Each entry names its title id (build_extras_entries)."""
    title_id, title_entry = _extras_title(load_library(), manual_id)
    return build_extras_entries(title_entry, title_id) if title_entry is not None else []


# [IMP-C26] Per-object account override. An archived object can live in a
# DIFFERENT Google account than its id prefix says (the 2026-09-25 inventory
# mapping found 31 X-Files episodes in the MOVIES account), and prefix routing
# alone can never fetch it. The gitignored mvconfig.json may therefore carry
#     "fetch_account_overrides": {"<exact manual id or id prefix>": "<account>"}
# where <account> is a CHROME_PROFILES key. profile_for_id consults it first;
# IMP-C25 adds the learned per-object account beneath it (_object_account). No key
# — the default — means today's prefix routing, unchanged.
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


def _override_account(manual_id):
    """[IMP-C26] The fetch_account_overrides account for an id — the longest
    matching key, so an exact id beats any prefix of it (keys are plain string
    prefixes) — or None when no key matches."""
    overrides = _account_overrides()
    best = max((key for key in overrides if manual_id.startswith(key)), key=len, default=None)
    return overrides[best] if best is not None else None


def _prefix_profile(manual_id):
    """Today's id-prefix routing: the first ID_PREFIX_PROFILE match, else DEFAULT_PROFILE."""
    for prefix, key in ID_PREFIX_PROFILE:
        if manual_id.startswith(prefix):
            return key
    return DEFAULT_PROFILE


def profile_for_id(manual_id):
    # [IMP-C26] An mvconfig.json fetch_account_overrides entry wins: the longest
    # matching key, so an exact id beats any prefix of it (keys are plain string
    # prefixes). Otherwise today's id-prefix routing, unchanged.
    return _override_account(manual_id) or _prefix_profile(manual_id)


def _object_account(owner_id, block):
    """[IMP-C25 P-4] The Google account one uploaded object is fetched in, and why:
    (account, "override" | "learned" | "prefix").

      1. the mvconfig.json fetch_account_overrides match for its owning id (the
         user's escape hatch, IMP-C26 — always wins);
      2. the account its TRUSTED gphotos block learned — mvcommon.home_account(),
         the account that holds a verified (or user-vouched) copy — when that is
         not simply the prefix default;
      3. the id prefix (profile_for_id without an override).
    owner_id = the owning library id (the TITLE id for an extra); block = the
    object's trusted block or None (mvcommon.iter_entry_objects gives only
    trusted blocks — P-2)."""
    override = _override_account(owner_id)
    if override:
        return override, "override"
    prefix = _prefix_profile(owner_id)
    if block is not None:
        home = mvcommon.home_account(block, owner_id, ID_PREFIX_PROFILE, DEFAULT_PROFILE)
        if home != prefix:
            return home, "learned"
    return prefix, "prefix"


def _route_entry(entry, owner_id):
    """[IMP-C25 P-4] Route every uploaded object of ONE fetch entry on its own
    (_object_account):
      default       the account of an object the library cannot name (no object
                    matches its hash): the owning id's override, else its prefix
      by_sha        {sha256: account} for every object the library names
      accounts      every account the entry's objects use, in object order
      reasons       {account: {"override" | "learned" | "prefix"}}
      contradicted  [(override account, verified account)] for each object whose
                    VERIFIED copy lives elsewhere than the override says"""
    owner_id = owner_id or ""
    default = _object_account(owner_id, None)
    route = {"default": default[0], "by_sha": {}, "accounts": [], "reasons": {}, "contradicted": []}
    for sha256, obj in _entry_objects(entry, owner_id).items():
        account, reason = _object_account(owner_id, obj["block"])
        route["by_sha"][sha256] = account
        if account not in route["accounts"]:
            route["accounts"].append(account)
        route["reasons"].setdefault(account, set()).add(reason)
        held = (obj["block"] or {}).get("accounts")
        verified = {a for a, r in (held if isinstance(held, dict) else {}).items()
                    if isinstance(r, dict) and r.get("verified_by") in mvcommon._VERIFIED_PROOFS}
        if reason == "override" and verified and account not in verified:
            route["contradicted"].append(
                (account, mvcommon.home_account(obj["block"], owner_id, ID_PREFIX_PROFILE, DEFAULT_PROFILE)))
    if not route["accounts"]:
        route["accounts"].append(default[0])
        route["reasons"][default[0]] = {default[1]}
    return route


def _account_groups(manual_id, active_profile, targets, target_ids, extra_entries, ctx=None):
    """[IMP-C26; IMP-C25 P-4] Split one fetch batch into per-account groups:
    [(profile, [(entry, entry_id), ...], [extra_entry, ...]), ...].

    Every uploaded OBJECT is routed on its own (_route_entry: the mvconfig
    fetch_account_overrides match > the account its trusted gphotos block learned
    > the id prefix). An entry joins the group of every account that holds one of
    its objects; an entry whose objects span accounts is recorded in ctx.plans, so
    each of those sessions fetches only its own objects (fetch_single_entry). The
    selector's own account (active_profile) comes first, then CHROME_PROFILES
    order; each group keeps the batch's original order. A target routes by its
    own resolved id (one without an id — never produced by resolve_target_ids,
    tolerated exactly like today's entry_id=None — routes with the selector); an
    extra by its TITLE id. An override that contradicts an object's verified
    account prints one warning line per entry; the override wins (P-4). With no
    valid override and no learned account that differs from the prefix, this is
    today's single session, so that path cannot change."""
    ctx = ctx if ctx is not None else FetchContext()
    pairs = [(entry, target_ids[i] if i < len(target_ids) else None)
             for i, entry in enumerate(targets)]
    title_id = None
    if extra_entries and not all(ex.get("manual_id") for ex in extra_entries):
        title_id = _extras_title(load_library(), manual_id)[0] or manual_id
    routed = [(entry, entry_id, False, _route_entry(entry, entry_id or manual_id)) for entry, entry_id in pairs]
    routed += [(ex, None, True, _route_entry(ex, ex.get("manual_id") or title_id)) for ex in extra_entries]
    learned = any("learned" in reasons for *_, route in routed for reasons in route["reasons"].values())
    if not _account_overrides() and not learned:
        return [(active_profile, pairs, list(extra_entries))]

    order = [active_profile] + [p for p in CHROME_PROFILES if p != active_profile]
    groups = {}
    for entry, entry_id, is_extra, route in routed:
        if route["contradicted"]:
            override, verified = route["contradicted"][0]
            owner = (entry.get("manual_id") if is_extra else entry_id) or manual_id
            print(f"   > ⚠️ override '{override}' for {owner} differs from the verified account '{verified}' "
                  f"({len(route['contradicted'])} file(s)) — the override wins.")
        accounts = sorted(route["accounts"], key=order.index)
        for account in accounts:
            members = groups.setdefault(account, ([], []))
            if is_extra:
                members[1].append(entry)
            else:
                members[0].append((entry, entry_id))
            ctx.reasons.setdefault(account, set()).update(route["reasons"][account])
        if len(accounts) > 1:
            ctx.plans[_entry_key(entry, entry_id)] = {"accounts": accounts, "by_sha": route["by_sha"],
                                                      "default": route["default"]}
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


def cmd_fetch_route(manual_id, ep_range=None, fetch_extras=False, temp_dir=None, manual=False):
    # [IMP-C25] manual = --manual (parse_fetch_options), carried in the run's FetchContext for Step 14.
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

    # [IMP-C26, IMP-C25 P-4] One Chrome session per Google account the batch's
    # objects live in: a single group (exactly today's session) unless
    # fetch_account_overrides or learned accounts move objects.
    ctx = FetchContext(manual=manual)  # [IMP-C25 Step 10] the run's shared state, handed to every entry
    groups = _account_groups(manual_id, active_profile, targets, target_ids, extra_entries, ctx)

    # [IMP-C17] Single-flight: only one interactive fetch batch may drive the
    # browser at a time (blocking=True polls then reclaims a stale/contended
    # lock — it never hard-blocks). Placed AFTER the no-targets guard so an
    # empty batch never touches the lock file. [IMP-C26] Held across EVERY
    # account group, so nothing else can take port 9222 between two sessions.
    interrupted = False  # [IMP-C25 Step 11] the run ended on Ctrl+C or an error
    stopped = False      # [IMP-C25 Step 11 i] the run stopped on a logged-out profile: no later session
    with fetch_session_lock(blocking=True):
        try:
            for n, (profile, group_targets, group_extras) in enumerate(groups):
                if interrupted:
                    break  # [IMP-C25 Step 11 i] stopped while the previous session was closing
                # Init Selenium ONCE per account group (one group = the whole batch)
                driver = None
                try:
                    if profile != active_profile:
                        print(f"   > [Account] Switching to profile '{profile}' for "
                              f"{len(group_targets) + len(group_extras)} item(s) "
                              f"({ctx.switch_reason(profile)})")
                    # [IMP-C25 OD-10] Never launch Chrome on a missing profile folder: it
                    # would come up as a new, empty, signed-out profile.
                    problem = profile_preflight(profile)
                    if problem:
                        print(f"❌ {problem[:1].upper()}{problem[1:]}")
                        return
                    if n and not _debug_port_free():
                        print(f"❌ Cannot switch to profile '{profile}': Chrome's debug port 9222 "
                              f"is still in use (the previous account's Chrome did not close). "
                              f"Close that Chrome window, then re-run — files already fetched "
                              f"stay in their restore folder and are skipped.")
                        return
                    if not n:
                        # [IMP-C25 Step 11 e] An interrupted earlier fetch is cleaned up first (P-12 rule 3),
                        # [11 d] and this run's journal exists before its first Chrome starts.
                        _startup_cleanup(ctx)
                        ctx.journal = FetchJournal(ctx.run_id)
                    # Pass the selected profile
                    driver = init_driver(profile)
                    if not driver: return
                    ctx.start_session(profile)
                    _note_session(ctx, driver, profile)

                    for entry, entry_id in group_targets:
                        fetch_single_entry(driver, entry, temp_dir=temp_dir, entry_id=entry_id, ctx=ctx)

                    # [IMP-D19 Step 5] Extras fetch through the SAME proven mechanism:
                    # each synthetic extra entry stages into its own
                    # <title folder_path>/<group_rel>/restore/ folder.
                    if group_extras:
                        print(f"\n=== 📎 FETCHING {len(group_extras)} EXTRA(S) for {manual_id} ===")
                    for ex in group_extras:
                        fetch_single_entry(driver, ex, ctx=ctx)

                except SessionExpiredError:
                    # [IMP-C6] One logged-out detection aborts the whole batch loudly
                    # (IMP-C26: naming the account group that hit it).
                    print(f"❌ Profile '{profile}' is logged out. Open Chrome with "
                          f"--user-data-dir={CHROME_PROFILES[profile]}, sign in to "
                          f"photos.google.com, then re-run.")
                    stopped = True
                    return
                except KeyboardInterrupt:
                    print("\n🛑 Stopped by user.")
                    interrupted = True
                    break
                except Exception as e:
                    print(f"\n❌ Critical Error: {e}")
                    interrupted = True
                    break
                finally:
                    if driver:
                        if not interrupted:
                            # [IMP-C25 Step 11 i] once its downloads have ended, the session closes the Chrome
                            # it launched (IMP-C26: between two accounts the windows of any other one)
                            try:
                                _close_session(driver, ctx, last=stopped or n + 1 == len(groups))
                            except KeyboardInterrupt:  # stopped while waiting for a download: as any Ctrl+C
                                print("\n🛑 Stopped by user.")
                                interrupted = True
                            except Exception as e:
                                print(f"   > ⚠️ Closing Chrome failed ({type(e).__name__}: {e}).")
                        try:
                            driver.quit()
                        except:
                            pass
        finally:
            _end_run(ctx, interrupted)  # [IMP-C25 Step 11] every way out: finished, stopped or interrupted

    lost_line = _lost_downloads_line(ctx)  # [IMP-C34] only when a download was lost
    if lost_line:
        print(f"\n{lost_line}")
    left_line = _left_in_downloads_line(ctx)  # [IMP-C25 P-12] only in the ~/Downloads fallback
    if left_line:
        print(left_line)
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


def parse_fetch_options(argv):
    """[IMP-C25 Step 10 f] The fetch options beyond parse_fetch_args' pinned
    4-tuple, from the same full argv: {"manual": bool} — `--manual` anywhere after
    the id (Step 14's manual mode; main.py forwards it only when the user gives it,
    the web UI never does). Pure; never exits — parse_fetch_args validates argv."""
    return {"manual": "--manual" in list(argv)[3:]}


def cli(argv):
    """`python mainfetch.py fetch <id> [episodes <range>] [tempdir <path>]
    [--fetchExtras] [--manual]` — parse argv, then run the fetch."""
    mid, epr, fetch_extras, temp_dir = parse_fetch_args(argv)
    options = parse_fetch_options(argv)
    cmd_fetch_route(mid, epr, fetch_extras, temp_dir=temp_dir, manual=options["manual"])


if __name__ == "__main__":
    cli(sys.argv)