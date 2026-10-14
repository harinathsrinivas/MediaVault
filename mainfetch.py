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
import random
import contextlib
import urllib.parse
from datetime import datetime, timedelta, timezone
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
import gprpc  # [IMP-C25 Step 12] the ONE read-only Google Photos RPC client (it imports mainfetch lazily)
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
# [IMP-C25 Step 14 c] Every navigation of an automation session is bounded. Selenium's (W3C's) default page-load
# timeout is 300 s, and a live probe hung ~5 min in one driver.get (DECISIONS §1e). A page that does not load within
# this long raises Selenium's timeout, so the caller's own error path runs (a stage falls back, an attempt is retried)
# instead of the session hanging. The direct download lowers it for its own navigation (_bounded_get) and restores this
# value; check sessions set the same bound (CHECK_PAGE_LOAD_S).
FETCH_PAGE_LOAD_S = 60.0


def bound_page_loads(driver, seconds=FETCH_PAGE_LOAD_S):
    """[IMP-C25 Step 14 c] Bound every page load of a Selenium session to `seconds` (FETCH_PAGE_LOAD_S): a navigation
    that does not finish raises Selenium's timeout instead of waiting out its 300 s default. A driver without
    set_page_load_timeout (a test stand-in) is left as it is. Never raises; True when the bound was set."""
    setter = getattr(driver, "set_page_load_timeout", None)
    if not callable(setter):
        return False
    try:
        setter(seconds)
    except Exception:
        return False
    return True


def init_driver(profile_key="movies"):
    """Initializes the Chrome Driver with the selected profile.

    [IMP-C25 Step 14 c — DECISIONS §1e] The session's page loads are bounded (bound_page_loads) before its first
    navigation. An attach that fails — chromedriver-manager's online check, the attach itself, the Photos tab, or a
    Ctrl+C meanwhile — ends the Chrome this call launched (_end_launched_chrome), so it never stays behind on the debug
    port and the profile. A chrome.exe that handed over to a Chrome already running has exited at once: that Chrome
    is never touched."""

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
    chrome = None
    try:
        chrome = subprocess.Popen(cmd)  # [IMP-C25 Step 11 d] kept: the fetch journal records its pid
        print("     Waiting 3 seconds for Chrome to stabilize...")
        time.sleep(3)
    except Exception as e:
        print(f"❌ Failed to launch Chrome binary: {e}")
        _end_launched_chrome(chrome, user_data_dir)  # [IMP-C25 Step 14 c] (nothing when the launch itself failed)
        return None
    except BaseException:  # [IMP-C25 Step 14 c] Ctrl+C while Chrome settles: it must not stay behind
        _end_launched_chrome(chrome, user_data_dir)
        raise

    print("   > 🔗 Attaching Selenium to localhost:9222...")
    options = Options()
    options.add_experimental_option("debuggerAddress", "127.0.0.1:9222")

    try:
        service = Service(ChromeDriverManager().install())
        driver = webdriver.Chrome(service=service, options=options)
    except Exception as e:
        print(f"❌ Selenium Connection Error: {e}")
        _end_launched_chrome(chrome, user_data_dir)  # [IMP-C25 Step 14 c] e.g. chromedriver-manager's online check
        return None
    except BaseException:
        _end_launched_chrome(chrome, user_data_dir)
        raise
    bound_page_loads(driver)  # [IMP-C25 Step 14 c] before the first navigation (use_photos_tab may open Photos)

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
        _end_launched_chrome(chrome, user_data_dir)  # [IMP-C25 Step 14 c] ... unless this call launched it
        return None
    except BaseException:
        try:
            driver.quit()
        except Exception:
            pass
        _end_launched_chrome(chrome, user_data_dir)
        raise
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
        there is no such process, (None, None) when that cannot be told — never a guess.
        [IMP-C25 Step 14 c] `-ErrorAction Stop`: a query that FAILS (a WMI / CIM error) ends the
        script with exit code 1 — "cannot be told". Without it the error was non-terminating, $p
        stayed empty and the script printed GONE: a failed query read as "the process is gone"."""
        if os.name != "nt" or not mvcommon._is_int(pid) or pid <= 0:
            return None, None
        script = ("[Console]::OutputEncoding = [Text.Encoding]::UTF8; "
                  f"$p = Get-CimInstance Win32_Process -Filter 'ProcessId={int(pid)}' -ErrorAction Stop; "
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
    pid terminated, or None.

    [IMP-C25 Step 14 c — the shared close under heavy load] When the process table cannot answer
    (its PowerShell query timed out or failed: under load the close left a Chrome running twice that
    way, Step 12/C), or answers "gone" while the handle says the process runs, the open Popen handle
    is the proof (_launched_chrome_is_ours). A table that names ANOTHER profile, or a command line it
    cannot read, still means never. taskkill /T /F (the whole tree) first; the handle's own kill()
    when taskkill fails."""
    chrome = ctx.chrome or {}
    pid = chrome.get("pid")
    if chrome.get("launched") is not True or not mvcommon._is_int(pid):
        return None
    proc = chrome.get("proc")
    poll = getattr(proc, "poll", None)
    if callable(poll) and poll() is not None:
        return None  # it has exited already
    if not _launched_chrome_is_ours(pid, chrome.get("user_data_dir"), poll):
        return None  # never a Chrome that is not ours
    if PROCESS_TABLE.terminate(pid):
        return pid
    return pid if _kill_by_handle(proc) else None


def _launched_chrome_is_ours(pid, user_data_dir, poll):
    """[IMP-C25 Step 14 c] May the session's LAUNCHED Chrome `pid` be terminated? Yes when the process table names exactly
    its profile folder (_chrome_still_ours' rule) — or when the table cannot tell, or says it is gone, while the
    session's open Popen handle (`poll`) says it still runs: this run launched that very chrome.exe on that profile, and
    a pid cannot be reused while its handle is open. Never when the table names another profile, or answers alive with a
    command line it cannot read (Step 11's guard). One table query."""
    alive, cmdline = PROCESS_TABLE.query(pid)
    if alive is True:
        return (cmdline is not None and isinstance(user_data_dir, str) and bool(user_data_dir)
                and _cmdline_user_data_dir(cmdline) == os.path.normcase(os.path.abspath(user_data_dir)))
    return callable(poll) and poll() is None


def _handle_gone(proc, wait=None):
    """[IMP-C25 Step 14 c] Wait up to `wait` seconds (CHROME_GONE_WAIT_S) for the process behind a Popen handle to exit.
    True once it has; never raises."""
    try:
        proc.wait(timeout=CHROME_GONE_WAIT_S if wait is None else wait)
    except Exception:
        return False
    return True


def _kill_by_handle(proc, wait=None):
    """[IMP-C25 Step 14 c] End the process behind a Popen handle this run holds — its own TerminateProcess (the browser
    process; Chrome's helper processes exit with it) — and wait up to `wait` seconds (CHROME_GONE_WAIT_S) for it to be
    gone. True once it has exited; never raises."""
    try:
        proc.kill()
    except Exception:
        return False
    return _handle_gone(proc, wait)


def _end_launched_chrome(proc, user_data_dir):
    """[IMP-C25 Step 14 c — DECISIONS §1e] The Chrome init_driver launched when attaching to it failed (chromedriver-
    manager's online check, the attach, the Photos tab, a Ctrl+C): end it, so it never stays behind holding the debug
    port and the profile (the live probe of 2026-10-07 left one when the online check failed). Only that very process,
    and only while it still runs — its Popen handle (while it is open the pid cannot have been reused, and this call
    launched it on this profile). A chrome.exe that handed over to a Chrome already running has exited at once: that
    Chrome is never touched. taskkill /T /F (the whole tree), else the handle's kill(); once it is gone, its profile is
    marked cleanly exited (no "Restore pages?"). One line; never raises. -> True when it was ended."""
    poll = getattr(proc, "poll", None)
    if not callable(poll):
        return False
    pid = getattr(proc, "pid", None)
    try:
        if poll() is not None:
            return False  # exited at once: a hand-over to a Chrome that was already running
        if not (PROCESS_TABLE.terminate(pid) and _handle_gone(proc)) and not _kill_by_handle(proc):
            raise RuntimeError("it is still running")
    except Exception as e:
        print(f"   > ⚠️ The Chrome this launch started (pid {pid}) could not be ended ({type(e).__name__}) — close it "
              f"before the next fetch.")
        return False
    print(f"   > 🧹 Closed the Chrome this launch started (pid {pid}) — attaching to it failed.")
    mark_profile_clean_exit(user_data_dir, wait=CHROME_GONE_WAIT_S)
    return True


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
    [IMP-C25 Step 12 — the exact-item locator]
    locator    the locator's per-run state (_LocatorRun: the record cache both the
               RPC and the web-page paths feed, the listings, the drift verdict);
               created on first use (_locator_run)
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
        self.locator = None  # [IMP-C25 Step 12] _LocatorRun, created on first use

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


# ==========================================
#   IMP-C25 STEP 12 — THE EXACT-ITEM LOCATOR (candidate C: hybrid arbitration)
# ==========================================
# docs/feature-fetch-datetime/PLAN.md Step 12, P-5, P-7, P-8, P-9, P-20. locate_object() pins ONE uploaded
# object to its Google Photos item — read-only — by the evidence-ordered waterfall (P-8):
#   S0a photo_id (the learned one, then its alternates) · S0b content-hash lookup (swbisb, the object's SHA-1) ·
#   S1 taken second · S2 exact uploaded name · S3 title + short_id · S3u the user's search_override
# with the object's verified approach.best first. For a content-keyed object nothing pinned, the queries today's
# two attempts would type are previewed as well (OD-5: those attempts are skipped when every candidate they could
# click is proven another item). Candidate C — hybrid arbitration:
#   * every stage asks Google Photos' read-only internal calls first (gprpc, P-9) and, when one fails
#     (RpcUnavailable / RpcShapeError / RpcServerError), runs the SAME stage on the web pages — the search box,
#     the item page's embedded record (gpweb.page_record), its info panel: a per-stage fallback, so one failing
#     call never costs the run its RPC path;
#   * both paths feed ONE per-run record cache (ctx.locator): a season or a chunk set lists and opens each item at
#     most once (one taken-second lookup serves every chunk sharing the second; one swbisb batch an entry);
#   * the first object of a run that RPC locates is cross-checked against its item page's record — the drift
#     detector: a disagreement turns RPC off for the rest of the run (one warning line) and the object is located
#     again from the web pages, the primary source (P-7).
# A candidate counts only when its record PROVES it (P-5): its dedupKey is the object's content key (proof
# dedupkey) or — for an object without one — its file name is the exact uploaded name (proof filename; P-6
# normalized). Never a tile position. SHA-256 after the download stays the final authority (the harvester).

TRIGGER_CONFIRM_S = 20.0         # a requested download must show in the watched folder within this long
TRIGGER_POLL_S = 0.5             # ... looked at this often
DIRECT_DOWNLOAD_CONCURRENCY = 3  # P-20: at most this many direct downloads run at once
LOCATE_MAX_CANDIDATES = 20       # candidates one stage verifies — more: ambiguous, never guessed (not for a
                                 # content-keyed object's RPC listing: its records carry their dedupKeys, all judged
                                 # at no cost; the pages read bound it)
LOCATE_MAX_SCROLLS = 12          # result-grid scrolls per web-page search
LOCATE_SEARCH_PAGES = 2          # EzkLib pages per RPC search
SHORT_ID_QUERY_MIN = 6           # S3's last query is the bare short id only from this length on (shorter: too loose)
TIMELINE_PAGE_SIZE = 100         # S1: lcxiM items per page
TIMELINE_MAX_PAGES = 3           # S1: pages read while they stay inside the taken second
ITEM_WAIT_S = 12.0               # an item page: wait this long for its record (+ info panel) — the crawler's wait
ITEM_POLL_S = 0.4
VIEWER_SETTLE_S = 1.5            # an opened item takes Shift+D once its viewer is up (trigger_download waits 2 s)
DIRECT_PAGE_LOAD_S = 15.0        # the page-load bound for opening a minted link — a navigation that becomes a download
                                 # may never "load" (a live probe hung ~5 min on Selenium's 300 s default)
SELENIUM_PAGE_LOAD_S = 300.0     # Selenium's / W3C's default page-load timeout, restored when it cannot be read
DRIFT_CHECK_TRIES = 3            # inconclusive page cross-checks per run before RPC is used unchecked

# (approach — the P-1 vocabulary, stage — a learned record's approach.tried (mvcommon.GP_STAGES), label)
_LOCATE_STAGES = (("photo_id", "s0_photo_id", "S0a"), ("hash", "s0_hash", "S0b"),
                  ("taken_second", "s1_taken_second", "S1"), ("exact_name", "s2_exact_name", "S2"),
                  ("title_sid", "s3_title_sid", "S3"), ("user_query", "s3u_user_query", "S3u"))
_LEGACY_STAGE = ("legacy", "s4_legacy", "S4")
_STAGE_LABEL = {stage: label for _approach, stage, label in _LOCATE_STAGES + (_LEGACY_STAGE,)}
_RECORD_FIELDS = ("photo_id", "dedup_key", "taken_ms", "tz_offset_ms", "upload_ms", "width", "height", "filename")
_DRIFT_FIELDS = ("dedup_key", "taken_ms", "tz_offset_ms", "upload_ms")
_SHA1_HEX_RE = re.compile(r"^[0-9a-fA-F]{40}$")


class _LocatorRun:
    """The exact-item stages' state for ONE run (ctx.locator — _locator_run creates it on first use):

      records   {(account, photo_id): record} — the ONE record cache both paths feed: the item's facts
                (_RECORD_FIELDS + size_bytes), `src` = the paths that read it ({"rpc", "ui"}) and `by` = each
                path's own view (what the drift detector compares). The item page wins over an RPC answer
                (P-7) — except the file name: an exact RPC name beats the info panel's whitespace-collapsed one
      listings  {(account, path, key): {"ids", "complete"[, "labels"]}} — what one search, day, taken second
                or hash lookup listed (path rpc | ui | hash | second-rpc | second-ui)
      gone      {(account, photo_id)} — RPC AND the item page both said there is no such item (stale)
      wanted    {account: {sha1}} — the content keys of the entry being located: the first S0b of the entry
                sends them all in one swbisb batch
      rpc_off   None, or why RPC is off for the rest of the run ("drift")
      drift     None (not checked yet) · "ok" · "drift" · "unchecked" (DRIFT_CHECK_TRIES inconclusive looks)
      direct_off  None, or why the direct download (P-20) is off for the rest of the run: "other-bytes" — a link
                served other bytes than a content-verified item (P14: a transcode), so Shift+D fetches the rest
      told      the one-time notes already printed
      stats     {account: counters} — each account session's summary line (_locator_session_line)"""

    def __init__(self):
        self.records, self.listings, self.gone, self.wanted = {}, {}, set(), {}
        self.rpc_off = self.drift = self.direct_off = None
        self.drift_tries = 0
        self.told = set()
        self.stats = {}


def _locator_run(ctx):
    if getattr(ctx, "locator", None) is None:
        ctx.locator = _LocatorRun()
    return ctx.locator


def _session_stats(ctx):
    """The exact-item counters of the session's account (one Chrome session per account and run)."""
    return _locator_run(ctx).stats.setdefault(
        getattr(ctx, "account", None), {"objects": 0, "located": 0, "direct": 0, "shift_d": 0, "fallbacks": 0})


def _can_locate(driver):
    """True for a driver the exact-item stages can use: it navigates and runs page scripts (get, execute_script,
    execute_async_script) — every real Chrome driver. A stand-in without them (None, the recorders of the Step
    10/11 tests) keeps today's two attempts only, exactly as before Step 12."""
    return all(callable(getattr(driver, name, None)) for name in ("get", "execute_script", "execute_async_script"))


def _rpc_ok(driver, ctx):
    """RPC may be asked: the drift detector has not turned it off and gprpc's circuit breaker is closed."""
    return _locator_run(ctx).rpc_off is None and not gprpc.stats(driver)["breaker_open"]


def _note_fallback(ctx, error):
    """Count one failed RPC stage (candidate C falls back to the web pages); say so once per run — the
    exception's type only (gprpc messages carry no token or URL, but nothing needs more)."""
    run = _locator_run(ctx)
    _session_stats(ctx)["fallbacks"] += 1
    if "fallback" not in run.told:
        run.told.add("fallback")
        print(f"   > ⚠️ A Google Photos internal call failed ({type(error).__name__}) — that stage uses the web "
              f"pages instead.")


def _content_key(loc):
    """The object's dedupKey — its content key, P-5's strongest proof — from the locator, or None."""
    key = gpweb.normalize_dedup_key(loc.get("dedup_key"))
    if key is None and isinstance(loc.get("sha1"), str) and _SHA1_HEX_RE.match(loc["sha1"]):
        key = gpweb.normalize_dedup_key(gpweb.dedup_key(loc["sha1"].lower()))
    return key


def _sha1_of(loc, key):
    """The object's SHA-1 hex (the swbisb lookup key): the locator's, else the one its dedupKey encodes."""
    sha1 = loc.get("sha1")
    if isinstance(sha1, str) and _SHA1_HEX_RE.match(sha1):
        return sha1.lower()
    return gpweb.dedup_key_to_sha1_hex(key) if key else None


def _seen(st, photo_id):
    if photo_id not in st["seen"]:
        st["seen"].append(photo_id)


def _remember(ctx, account, facts, src):
    """Fold one item's facts into the run's record cache (src rpc | ui); returns the cached record, or None for
    facts without a media key."""
    pid = facts.get("photo_id") if isinstance(facts, dict) else None
    if not (isinstance(pid, str) and gpweb.MEDIA_KEY_RE.match(pid)):
        return None
    held = _locator_run(ctx).records.setdefault((account, pid), {"photo_id": pid, "src": set(), "by": {}})
    view = held["by"].setdefault(src, {})
    for k in _RECORD_FIELDS[1:] + ("size_bytes",):
        value = gpweb.normalize_dedup_key(facts.get(k)) if k == "dedup_key" else facts.get(k)
        if value is None or (k == "filename" and not (isinstance(value, str) and value.strip())):
            continue
        view[k] = value
        if k == "filename":
            if held.get(k) is None or (src == "rpc" and held.get("_name_src") != "rpc"):
                held[k], held["_name_src"] = value, src
        elif src == "ui" or held.get(k) is None:
            held[k] = value
    held["src"].add(src)
    return held


def _verdict(rec, loc, key):
    """P-5 for one candidate record -> ("match", proof, name_normalized) when it IS the object — its dedupKey is
    the object's content key, or (no content key) its file name is the exact uploaded name (P-6 normalized
    counts); ("wrong", None, False) when the record proves another item; ("unknown", None, False) otherwise."""
    if key:
        held = gpweb.normalize_dedup_key(rec.get("dedup_key"))
        if held is None:
            return "unknown", None, False
        return ("match", "dedupkey", False) if held == key else ("wrong", None, False)
    name, got = loc.get("uploaded_name"), rec.get("filename")
    if not (name and got):
        return "unknown", None, False
    how = gpweb.names_match(got, name)
    return ("match", "filename", how == "normalized") if how else ("wrong", None, False)


def _judge(records, loc, key, listed=None, complete=True):
    """One stage's verdict over the records of its candidates (P-5) -> {outcome, n, pick, proof, normalized,
    alternates, rule}:
      exactly-one  one verified item — or several holding the SAME bytes (one dedupKey: one object): the
                   object's stored photo_id, else the oldest upload, is picked, the others are alternates
      ambiguous    candidates none of which is proven, or a listing longer than LOCATE_MAX_CANDIDATES — or, for
                   an object WITHOUT a content key, several name matches holding different bytes (a re-split
                   set uploaded twice under the same names): the NEWEST set (taken second, then upload time) is
                   picked (Step 9's rule) — rule "newest-set"; its SHA-256 still decides
      wrong-item   every candidate examined and proven to be another item
      not-found    no candidate
    listed = how many candidates the listing named (default: the records); complete = every one was read."""
    n = len(records) if listed is None else listed
    out = {"outcome": "not-found", "n": n, "pick": None, "proof": None, "normalized": False, "alternates": [],
           "rule": None}
    if not n:
        return out
    verdicts = [(rec, _verdict(rec, loc, key)) for rec in records]
    matches = [(rec, v) for rec, v in verdicts if v[0] == "match"]
    if matches:
        recs = [rec for rec, _v in matches]
        keys = {gpweb.normalize_dedup_key(rec.get("dedup_key")) for rec in recs}
        if len(recs) == 1 or (None not in keys and len(keys) == 1):
            stored = loc.get("photo_id")
            pick = min(recs, key=lambda r: (r["photo_id"] != stored, r.get("upload_ms") or 0, r["photo_id"]))
            out["outcome"] = "exactly-one"
        else:
            pick = max(recs, key=lambda r: (r.get("taken_ms") or 0, r.get("upload_ms") or 0, r["photo_id"]))
            out.update(outcome="ambiguous", rule="newest-set")
        verdict = next(v for rec, v in matches if rec is pick)
        out.update(pick=pick, proof=verdict[1], normalized=verdict[2],
                   alternates=[r["photo_id"] for r in recs if r is not pick])
    elif complete and len(records) == n and all(v[0] == "wrong" for _rec, v in verdicts):
        out["outcome"] = "wrong-item"
    else:
        out["outcome"] = "ambiguous"
    return out


def _stage_error(via):
    """A stage that could not run (neither path answered)."""
    return {"outcome": "error", "n": 0, "pick": None, "proof": None, "normalized": False, "alternates": [],
            "rule": None, "via": via, "complete": False}


# ------------------------------------------------------------------ the web-page path (approach A's DOM path)
class _SearchProblem(Exception):
    """A web-page search never showed its results (IMP-C29 / IMP-C35's diagnosis): the stage cannot run."""


def _body_text(driver):
    try:
        return driver.find_element(By.TAG_NAME, "body").text or ""
    except Exception:
        return ""


def _open_info_panel(driver):
    """Open the item page's info panel: its Info button, else the 'i' shortcut (tools/gp_inventory.py's
    sequence — a browser action, kept here because mainfetch must not import the crawler)."""
    for sel in ("[aria-label='Open info']", "[aria-label='Info']", "[aria-label='Show info']"):
        try:
            buttons = [e for e in driver.find_elements(By.CSS_SELECTOR, sel) if e.is_displayed()]
        except Exception:
            buttons = []
        if buttons:
            driver.execute_script("arguments[0].click();", buttons[0])
            return
    webdriver.ActionChains(driver).send_keys("i").perform()


def _page_facts(driver, photo_id, want_name, st):
    """The item page of `photo_id` — one page load, check_session_alive after it — read the crawler's way: its
    embedded record (gpweb.page_record, P-7's primary source) and, with want_name, the info panel's file name
    (gpweb.info_panel; the panel is opened once when closed, never toggled shut). None when the page shows no
    record of the item within ITEM_WAIT_S (a missing item, or a page that did not render)."""
    driver.get(f"{PHOTOS_URL}/photo/{photo_id}")
    check_session_alive(driver)
    st["pages"] += 1
    if want_name and hasattr(driver, "execute_cdp_cmd"):
        try:
            focus_page(driver)  # the panel renders only in a focused tab (RESEARCH F14)
        except Exception:
            pass
    record, name, opened = {}, None, False
    deadline = time.time() + ITEM_WAIT_S
    while True:
        if not record:
            record = gpweb.page_record(driver.page_source, photo_id)
        if record and want_name and not name:
            body = _body_text(driver)
            name = (gpweb.info_panel(body) or {}).get("filename")
            if not name and not opened and "Details" not in body:
                _open_info_panel(driver)  # display text as a hint only (P-7): it decides when to open, nothing else
                opened = True
                continue
        if (record and (name or not want_name)) or time.time() >= deadline:
            break
        time.sleep(ITEM_POLL_S)
    if not record:
        return None
    facts = dict(record)
    if name:
        facts["filename"] = name
    return facts


def _visible_result_links(driver):
    """The visible links to items on a search page — TILES_JS's rule (a[href*='photo/'], shown, wider than
    40 px) — without the home timeline's (_drop_timeline, IMP-C35)."""
    links = []
    for el in driver.find_elements(By.CSS_SELECTOR, "a[href*='photo/']"):
        try:
            if el.is_displayed() and el.size["width"] > 40:
                links.append(el)
        except Exception:  # a tile the grid recycled while it was read
            continue
    return _drop_timeline(driver, links)


def _ui_search(driver, query):
    """Type `query` into the Photos search box — trigger_download's keystrokes in a focused Photos tab — and wait
    for its results (IMP-C35). Raises _SearchProblem when they never show (IMP-C29's diagnosis)."""
    if hasattr(driver, "execute_cdp_cmd"):
        use_photos_tab(driver)  # focused, or no result grid renders (RESEARCH F14); re-arms downloads (P-12)
    driver.get(PHOTOS_URL)
    check_session_alive(driver)
    time.sleep(1.5)
    actions = webdriver.ActionChains(driver)
    actions.send_keys("/")
    actions.pause(0.3)
    actions.send_keys(query)
    actions.send_keys(Keys.ENTER)
    actions.perform()
    problem = _await_search_results(driver)
    if problem:
        raise _SearchProblem(problem)


def _ui_tiles(driver, query, cap=LOCATE_MAX_CANDIDATES):
    """The result tiles of one web-page search -> ([(photo_id, aria-label)], complete): grid order, ONE entry
    per item (live 2026-10-05: an item shows twice — the "most relevant" strip and its date section — and
    unrelated items come first), the grid scrolled up to LOCATE_MAX_SCROLLS times; complete = its end was reached.
    cap: stop once more than this many items are listed (a text search: every one would have to be verified);
    None reads the whole grid — a DAY listing, whose tiles are filtered to the taken second before any is a
    candidate (a release day holds several batches: RESEARCH F13, 38 items in four seconds). Nothing is clicked.
    Raises _SearchProblem."""
    _ui_search(driver, query)
    tiles, seen, last = [], set(), None
    for _ in range(LOCATE_MAX_SCROLLS + 1):
        for el in _visible_result_links(driver):
            pid = gpweb.photo_id_from_url(el.get_attribute("href"))
            if pid and pid not in seen:
                seen.add(pid)
                tiles.append((pid, el.get_attribute("aria-label")))
        if cap is not None and len(tiles) > cap:
            return tiles, False
        position = driver.execute_script(gpweb.SCROLL_JS)
        if position == last:
            return tiles, True
        last = position
        time.sleep(1.0)
    return tiles, False


# ------------------------------------------------------------------ listings and records (both paths, one cache)
def _text_ids(driver, query, path, ctx, account, st, cap=LOCATE_MAX_CANDIDATES):
    """The candidates one text search lists — RPC (EzkLib: what the search box shows) or the web page — cached per
    run and account: a query is searched once. cap: _ui_tiles' (None for a day listing)."""
    run = _locator_run(ctx)
    hit = run.listings.get((account, path, query))
    if hit is None:
        if path == "rpc":
            found = gprpc.search(driver, query, max_pages=LOCATE_SEARCH_PAGES)
            for rec in found["items"]:
                _remember(ctx, account, rec, "rpc")
            hit = {"ids": [r["photo_id"] for r in found["items"]], "complete": not found["truncated"]}
        else:
            tiles, complete = _ui_tiles(driver, query, cap)
            hit = {"ids": [pid for pid, _label in tiles], "labels": dict(tiles), "complete": complete}
        st["searches"] += 1
        run.listings[(account, path, query)] = hit
    return hit["ids"], hit["complete"]


def _rpc_second(driver, second, ctx, account):
    """S1 by RPC: the timeline (lcxiM) from the end of the taken second backwards — every item taken in that
    second, paged while the pages stay inside it."""
    ids, page_id = [], None
    for _ in range(TIMELINE_MAX_PAGES):
        page = gprpc.timeline_at(driver, second * 1000 + 999, TIMELINE_PAGE_SIZE, page_id)
        for rec in page["items"]:
            taken = rec.get("taken_ms")
            if not isinstance(taken, int) or taken // 1000 > second:
                continue
            if taken // 1000 < second:
                return ids, True
            _remember(ctx, account, rec, "rpc")
            ids.append(rec["photo_id"])
        page_id = page["next_page_id"]
        if page_id is None:
            return ids, True
    return ids, False


def _ui_second(driver, taken_ms, loc, ctx, account, st):
    """S1 on the web pages: the day search for the item's LOCAL day (RESEARCH F22 — the learned offset, else the
    pushing phone's, else UTC, then ±1 day), its tiles filtered to the taken second by their aria-label
    (gpweb.tile_label / same_second — a label that does not parse stays a candidate: P-7)."""
    learned = loc.get("tz_offset_ms") if isinstance(loc.get("tz_offset_ms"), int) else None
    for day in gpweb.local_days(taken_ms, learned, loc.get("device_tz_offset_min"), window=1):
        query = gpweb.day_query(day)
        ids, complete = _text_ids(driver, query, "ui", ctx, account, st, cap=None)  # the whole day, then the filter
        labels = _locator_run(ctx).listings[(account, "ui", query)].get("labels") or {}
        hits = [pid for pid in ids
                if (when := gpweb.tile_label(labels.get(pid), default_year=day.year)) is None
                or gpweb.same_second(when, taken_ms, learned)]
        if hits:
            return hits, complete
    return [], True


def _second_ids(driver, taken_ms, path, loc, ctx, account, st):
    """The candidates taken in the object's second (S1), cached per run: one lookup serves every chunk of a split
    and every object of a release batch sharing the second (RESEARCH F4, F13)."""
    run = _locator_run(ctx)
    second = taken_ms // 1000
    if path == "rpc":
        cache = (account, "second-rpc", second)
    else:
        offsets = (loc.get("tz_offset_ms"), loc.get("device_tz_offset_min"))
        cache = (account, "second-ui", (second,) + offsets)
    hit = run.listings.get(cache)
    if hit is None:
        if path == "rpc":
            ids, complete = _rpc_second(driver, second, ctx, account)
        else:
            ids, complete = _ui_second(driver, taken_ms, loc, ctx, account, st)
        hit = run.listings[cache] = {"ids": ids, "complete": complete}
    return hit["ids"], hit["complete"]


def _rpc_names(driver, photo_ids, ctx, account):
    """Fill cached records' file names from batch_info (EWgK9e — one call per ≤50 ids; proven live by the
    crawler)."""
    for pid, row in gprpc.batch_info(driver, photo_ids).items():
        if row and row.get("filename"):
            _remember(ctx, account, {"photo_id": pid, "filename": row["filename"],
                                     "size_bytes": row.get("size_bytes")}, "rpc")


def _rpc_item(driver, photo_id, ctx, account, key):
    """One item's record by RPC — the cache when it holds what verification needs, else item_info (VrseUb +
    fDcn4b), a missing file name completed by batch_info -> (record, gone): gone = RPC answered that this account
    has no such item. Raises gprpc.RpcUnavailable."""
    held = _locator_run(ctx).records.get((account, photo_id))
    if held and held.get("dedup_key") and (key or held.get("filename")):
        return held, False
    info = gprpc.item_info(driver, photo_id)
    if info is None:
        return None, True
    held = _remember(ctx, account, info, "rpc")
    if held is not None and not key and not held.get("filename"):
        _rpc_names(driver, [photo_id], ctx, account)
    return held, False


def _ui_item(driver, photo_id, ctx, account, st, key, fresh=False):
    """One item's record on the web pages — the cache when it already holds what verification needs (whichever
    path fed it), else its item page (_page_facts). fresh: always look at the page (a second opinion). None when
    the item is gone or its page shows no record."""
    run = _locator_run(ctx)
    if (account, photo_id) in run.gone:
        return None
    held = run.records.get((account, photo_id))
    if not fresh and held and held.get("dedup_key") and (key or held.get("filename")):
        return held
    facts = _page_facts(driver, photo_id, not key, st)
    return _remember(ctx, account, facts, "ui") if facts else None


def _collect(driver, sources, path, loc, ctx, account, st, cap=LOCATE_MAX_CANDIDATES):
    """The candidates of a stage's listings (`sources`: [("second", taken_ms)] or [("text", query), …]) -> (ids in
    listing order, complete, {photo_id: the text query that listed it}). More than `cap` (None: no cap): the first
    `cap`, incomplete."""
    ids, complete, origin = [], True, {}
    for kind, value in sources:
        if kind == "second":
            got, done = _second_ids(driver, value, path, loc, ctx, account, st)
        else:
            got, done = _text_ids(driver, value, path, ctx, account, st)
        complete = complete and done
        for pid in got:
            if pid not in origin and pid not in st["exclude"]:
                origin[pid] = value if kind == "text" else None
                ids.append(pid)
    if cap is not None and len(ids) > cap:
        ids, complete = ids[:cap], False
    for pid in ids:
        _seen(st, pid)
    return ids, complete, origin


def _rpc_verifiable(driver, ids, ctx, account, key):
    """The listed candidates' records for verification by RPC: an object without a content key needs their file
    names (one batch_info call for those the cache lacks)."""
    records = _locator_run(ctx).records
    if not key:
        missing = [p for p in ids if (account, p) in records and not records[(account, p)].get("filename")]
        if missing:
            _rpc_names(driver, missing, ctx, account)
    return [records[(account, p)] for p in ids if (account, p) in records]


def _ui_verifiable(driver, ids, loc, ctx, account, st, key):
    """The listed candidates' records for verification on the web pages: each item page opened unless the cache
    already holds what is needed; a content proof ends the opening (the rest need not be read)."""
    found = []
    for pid in ids:
        rec = _ui_item(driver, pid, ctx, account, st, key)
        if rec is None:
            continue
        found.append(rec)
        if key and _verdict(rec, loc, key)[0] == "match":
            break
    return found


def _stage_listing(driver, loc, ctx, account, st, key, sources):
    """A listing stage — S1, S2, S3, S3u or the S4 preview: RPC lists and verifies first; when an RPC call fails
    the SAME stage runs on the web pages (candidate C's per-stage fallback), reusing whatever the record cache
    already holds. -> _judge's dict + via, complete and query (the text query that listed the pick)."""
    via = None
    if _rpc_ok(driver, ctx):
        try:
            # the records of an RPC listing carry their dedupKeys: a content-keyed object is judged on every one of
            # them at no cost (G12 pick 9 — a batch released in one second outnumbers the cap); names cost a lookup
            cap = None if key else LOCATE_MAX_CANDIDATES
            ids, complete, origin = _collect(driver, sources, "rpc", loc, ctx, account, st, cap)
            records = _rpc_verifiable(driver, ids, ctx, account, key)
            via = "rpc"
        except gprpc.RpcUnavailable as e:
            _note_fallback(ctx, e)
    if via is None:
        try:
            ids, complete, origin = _collect(driver, sources, "ui", loc, ctx, account, st)
            records = _ui_verifiable(driver, ids, loc, ctx, account, st, key)
            via = "ui"
        except SessionExpiredError:
            raise
        except Exception as e:  # the page path failed too (no results page, a browser fault): no verdict
            if "ui_error" not in _locator_run(ctx).told:
                _locator_run(ctx).told.add("ui_error")
                print(f"   > ⚠️ A Google Photos page could not be read ({type(e).__name__}) — that stage is skipped.")
            return _stage_error("ui")
    out = _judge(records, loc, key, listed=len(ids), complete=complete)
    out.update(via=via, complete=complete, query=origin.get(out["pick"]["photo_id"]) if out["pick"] else None)
    return out


# ------------------------------------------------------------------ the stages
def _stage_photo_id(driver, loc, ctx, account, st, key):
    """S0a — the learned photo_id of this account, then its alternates: each read by RPC item info, else its item
    page. An item RPC calls gone is looked at on its page too (arbitration): only when both agree is the photo_id
    stale (returned in `stale` — the fetch records it, a read-only caller only reports it)."""
    ids = [p for p in dict.fromkeys([loc.get("photo_id")] + list(loc.get("alternates") or []))
           if isinstance(p, str) and gpweb.MEDIA_KEY_RE.match(p) and p not in st["exclude"]][:LOCATE_MAX_CANDIDATES]
    if not ids or not (key or loc.get("uploaded_name")):
        return None
    records, stale, pages = [], [], st["pages"]
    for pid in ids:
        _seen(st, pid)
        rec, gone = None, False
        if _rpc_ok(driver, ctx):
            try:
                rec, gone = _rpc_item(driver, pid, ctx, account, key)
            except gprpc.RpcUnavailable as e:
                _note_fallback(ctx, e)
        if rec is None:
            rec = _ui_item(driver, pid, ctx, account, st, key, fresh=gone)
            if rec is None and gone:
                stale.append(pid)
                _locator_run(ctx).gone.add((account, pid))
        if rec is not None:
            records.append(rec)
            if _verdict(rec, loc, key)[0] == "match":
                break
    out = _judge(records, loc, key)
    out.update(via="ui" if st["pages"] > pages else "rpc", complete=True, stale=stale)
    return out


def _hash_ids(driver, sha1, ctx, account):
    """S0b's listing: the items holding exactly these bytes (swbisb). The first lookup of an entry asks for every
    content key the entry registered (`wanted`) in one batch (≤ gprpc.MAX_BATCH_KEYS); the answers are cached."""
    run = _locator_run(ctx)
    hit = run.listings.get((account, "hash", sha1))
    if hit is None:
        batch = [sha1] + sorted(h for h in run.wanted.get(account, ())
                                if h != sha1 and (account, "hash", h) not in run.listings)
        batch = batch[:gprpc.MAX_BATCH_KEYS]
        found = gprpc.hash_lookup(driver, batch)
        for digest in batch:
            recs = [r for r in (_remember(ctx, account, rec, "rpc") for rec in found.get(digest, [])) if r]
            run.listings[(account, "hash", digest)] = {"ids": [r["photo_id"] for r in recs], "complete": True}
        hit = run.listings[(account, "hash", sha1)]
    return hit["ids"]


def _stage_hash(driver, loc, ctx, account, st, key):
    """S0b — Google Photos' content-hash lookup (swbisb) with the object's SHA-1: the items holding exactly these
    bytes (their own dedupKey is the hash: content proof by construction). RPC only — the web pages have no
    equivalent, so without RPC the stage is an `error` and the waterfall goes on."""
    sha1 = _sha1_of(loc, key)
    if not sha1:
        return None
    if not _rpc_ok(driver, ctx):
        return _stage_error("rpc")
    try:
        ids = [p for p in _hash_ids(driver, sha1, ctx, account) if p not in st["exclude"]]
    except gprpc.RpcUnavailable as e:
        _note_fallback(ctx, e)
        return _stage_error("rpc")
    for pid in ids:
        _seen(st, pid)
    records = _locator_run(ctx).records
    out = _judge([records[(account, p)] for p in ids if (account, p) in records], loc, key, listed=len(ids))
    out.update(via="rpc", complete=True)
    return out


def _stage_second(driver, loc, ctx, account, st, key):
    """S1 — the items taken in the object's second (its learned taken_ms, else its DateUTC), each verified."""
    taken = loc.get("taken_ms")
    if not (isinstance(taken, int) and not isinstance(taken, bool)) or not (key or loc.get("uploaded_name")):
        return None
    return _stage_listing(driver, loc, ctx, account, st, key, [("second", taken)])


def _merge_stage(first, later):
    """One verdict for a stage that ran a second listing because its first pinned nothing: the later pick when there
    is one; else ambiguous beats wrong-item beats not-found, an `error` only when both were errors."""
    n = first["n"] + later["n"]
    if later["pick"] is not None:
        return dict(later, n=n)
    outcomes = {first["outcome"], later["outcome"]} - {"error"} or {"error"}
    outcome = next(o for o in ("ambiguous", "wrong-item", "not-found", "error") if o in outcomes)
    via = "ui" if "ui" in (first["via"], later["via"]) else "rpc"
    return dict(later, outcome=outcome, n=n, via=via, complete=first.get("complete") and later.get("complete"))


def _text_stage(kind):
    """S2 / S3 / S3u — the locator's query list `kind` searched, every candidate verified. S3 ends with the object's
    BARE short id (SHORT_ID_QUERY_MIN characters or more) when its title query pinned nothing — live 2026-10-07 (an
    anime episode): Google Photos answered "No results" for the exact uploaded name, the title and the plain name,
    yet listed exactly the item for its bare short id. A short id alone can also list many items (RESEARCH F16:
    1 / 3 / 133): every one is still verified on its record, and more than LOCATE_MAX_CANDIDATES is ambiguous."""
    def stage(driver, loc, ctx, account, st, key):
        if not (key or loc.get("uploaded_name")):
            return None
        queries = list(dict.fromkeys(q for q in ((loc.get("queries") or {}).get(kind) or [])
                                     if isinstance(q, str) and q.strip()))
        sid = loc.get("short_id").strip() if kind == "s3" and isinstance(loc.get("short_id"), str) else ""
        bare = [sid] if len(sid) >= SHORT_ID_QUERY_MIN and sid not in queries else []
        if not queries and not bare:
            return None
        out = _stage_listing(driver, loc, ctx, account, st, key, [("text", q) for q in queries]) if queries else None
        if bare and (out is None or out["pick"] is None):
            later = _stage_listing(driver, loc, ctx, account, st, key, [("text", bare[0])])
            out = later if out is None else _merge_stage(out, later)
        return out
    return stage


def _stage_legacy_preview(driver, loc, ctx, account, st, key):
    """OD-5, for a content-keyed object no stage pinned: the queries today's two attempts would type, listed and
    verified like any stage. A verified candidate is located (approach legacy, then fetched like any located
    item); when every candidate of every one of them is proven another item — or there is none — those attempts
    could only fetch wrong items: `skip` (the fetch marks the object failed, reason s4-skipped)."""
    queries = []
    for pair in (loc.get("queries") or {}).get("s4") or []:
        query = pair[0] if isinstance(pair, (list, tuple)) and pair else None
        if isinstance(query, str) and query.strip() and query not in queries:
            queries.append(query)
    if not queries:
        return None
    out = _stage_listing(driver, loc, ctx, account, st, key, [("text", q) for q in queries])
    out["skip"] = out["outcome"] in ("wrong-item", "not-found") and out.get("complete") is True
    return out


_STAGE_RUNS = {"photo_id": _stage_photo_id, "hash": _stage_hash, "taken_second": _stage_second,
               "exact_name": _text_stage("s2"), "title_sid": _text_stage("s3"), "user_query": _text_stage("s3u")}


def _stage_order(loc):
    """The waterfall (P-8) with the object's verified approach.best first."""
    best = loc.get("approach_best")
    return sorted(_LOCATE_STAGES, key=lambda s: s[0] != best)  # stable: the P-8 order otherwise


def _locate_result(found, per, per_via, stale, s4_skip, per_pick=None):
    """locate_object's dict before its counters (see its docstring)."""
    res = {"found": False, "photo_id": None, "record": None, "proof": None, "name_normalized": False, "stage": None,
           "approach": None, "via": None, "outcome": None, "n": 0, "rule": None, "alternates": [], "query": None,
           "per_approach": per, "per_approach_via": per_via, "per_approach_pick": dict(per_pick or {}),
           "stale": list(dict.fromkeys(stale)), "s4_skip": bool(s4_skip)}
    if found is not None:
        approach, stage, out = found
        pick = out["pick"]
        res.update(found=True, photo_id=pick["photo_id"], record={k: pick.get(k) for k in _RECORD_FIELDS},
                   proof=out["proof"], name_normalized=out["normalized"], stage=stage, approach=approach,
                   via=out["via"], outcome=out["outcome"], n=out["n"], rule=out["rule"],
                   alternates=list(out["alternates"]), query=out.get("query"), s4_skip=False)
    return res


def _waterfall(driver, loc, ctx, account, st, approaches):
    """Run the stages in order until one pins the object (approaches "all": every stage), then — for a
    content-keyed object nothing pinned — the legacy preview (OD-5)."""
    key = _content_key(loc)
    per, per_via, per_pick, stale, found, preview = {}, {}, {}, [], None, None
    for approach, stage, _label in _stage_order(loc):
        out = _STAGE_RUNS[approach](driver, loc, ctx, account, st, key)
        if out is None:
            continue  # the object gives this stage nothing to work with
        per[approach] = {"outcome": out["outcome"], "n": out["n"]}  # exactly the contract's shape (Step 13 reuses it)
        per_via[approach] = out["via"]
        per_pick[approach] = out["pick"]["photo_id"] if out["pick"] else None
        stale += out.get("stale") or []
        if out["pick"] is not None and found is None:
            found = (approach, stage, out)
            if approaches != "all":
                break
    if key and (found is None or approaches == "all"):
        preview = _stage_legacy_preview(driver, loc, ctx, account, st, key)
        if preview is not None:
            per[_LEGACY_STAGE[0]] = {"outcome": preview["outcome"], "n": preview["n"]}
            per_via[_LEGACY_STAGE[0]] = preview["via"]
            per_pick[_LEGACY_STAGE[0]] = preview["pick"]["photo_id"] if preview["pick"] else None
            if found is None and preview["pick"] is not None:
                found = (_LEGACY_STAGE[0], _LEGACY_STAGE[1], preview)
    return _locate_result(found, per, per_via, stale,
                          found is None and preview is not None and preview.get("skip"), per_pick)


def _arbitrate(driver, res, loc, ctx, account, st, approaches):
    """The drift detector (candidate C): ONCE per run, the first object RPC located is cross-checked against its
    item page's record (P-7: the primary source) — dedupKey, taken / upload time, timezone offset. They agree: RPC
    stays trusted for the run. They differ: one warning line, RPC off for the rest of the run, every RPC-fed cache
    entry dropped, and this object is located again from the web pages. A page that shows no record leaves the
    verdict open for the next object (DRIFT_CHECK_TRIES at most)."""
    run = _locator_run(ctx)
    held = run.records.get((account, res["photo_id"])) or {}
    rpc_view = (held.get("by") or {}).get("rpc")
    if run.drift is not None or run.rpc_off or not rpc_view:
        return res
    page_view = (held.get("by") or {}).get("ui")
    if page_view is None:
        facts = _page_facts(driver, res["photo_id"], False, st)
        if facts is None:
            run.drift_tries += 1
            if run.drift_tries >= DRIFT_CHECK_TRIES:
                run.drift = "unchecked"
            return res
        page_view = _remember(ctx, account, facts, "ui")["by"]["ui"]
    differ = [k for k in _DRIFT_FIELDS
              if rpc_view.get(k) is not None and page_view.get(k) is not None and rpc_view[k] != page_view[k]]
    if not differ:
        run.drift = "ok"
        return res
    run.drift = run.rpc_off = "drift"
    run.records.clear()
    run.listings.clear()
    print(f"   > ⚠️ Google Photos' internal answers disagree with the item page ({', '.join(differ)}) — RPC is off "
          f"for the rest of this run; the web pages decide.")
    return _waterfall(driver, loc, ctx, account, st, approaches)


def locate_object(driver, locator, ctx, approaches="best", exclude=()):
    """[IMP-C25 Step 12 — candidate C, hybrid arbitration] Find ONE uploaded object's Google Photos item in the
    session's account — READ-ONLY: it navigates the session's Photos tab, types searches and asks read-only RPCs
    (gprpc's allowlist); it never downloads, clicks a tile, presses Shift+D or writes anything — what it learns is
    the caller's to record (the fetch: _run_locator_phase; `mainfetch.py locate` prints it).

    locator = a build_fetch_queue item's `locator`; ctx = the run's FetchContext (ctx.account = the session's
    account; its locator state caches records across objects). approaches "best" stops at the first stage that
    pins the object; "all" evaluates every stage (check_photos --approaches all, PLAN Step 13). exclude = photo
    ids no stage may pick or count as a candidate — the items already requested for the object (P-20's fallback
    chain: the waterfall once more finds another verified copy, or nothing).

    Returns (PLAN Step 12's contract — Step 13 reuses it and per_approach unchanged):
      found            the object is pinned to one item (P-5 proof)
      photo_id         that item, or None
      record           its facts {photo_id, dedup_key, taken_ms, tz_offset_ms, upload_ms, width, height,
                       filename} (the fetch's item['_trigger']['record']), or None
      proof            dedupkey | filename (P-5) · name_normalized: the name matched only after P-6 normalizing
      stage, approach  the stage that pinned it (mvcommon.GP_STAGES key, e.g. s0_photo_id) and its P-1 approach
      via              rpc | ui — the path that pinned it
      candidates_seen  how many distinct items were examined (candidates: their photo_ids)
      pages_opened, searches, rpc_calls, elapsed (s)   what it cost
      per_approach     {approach: {outcome: exactly-one | ambiguous | not-found | wrong-item | error, n}} — EXACTLY
                       those two keys — for every stage that ran ("legacy": the OD-5 preview of today's queries);
                       each entry is a failed-list `tried` row as it stands ({approach} + it, P-18)
      per_approach_via {approach: rpc | ui} — the path each of those verdicts came by (a parallel map, so
                       per_approach keeps the contract's shape)
      per_approach_pick {approach: photo_id | None} — the item each of those stages picked, None when it picked
                       none (a parallel map like per_approach_via; check_photos' "approaches disagree" outlier —
                       PLAN Step 13, OD-7)
    plus outcome / n (the pinning stage's), rule ("newest-set" when a name matched several uploads with
    different bytes), alternates (other items holding the same bytes or name), query (the text query that listed
    the item — the Shift+D click path searches it), stale (learned photo_ids RPC AND the item page call gone),
    s4_skip (OD-5: every candidate today's attempts could click is proven another item).
    Raises SessionExpiredError (signed out) — a stage error is an outcome, not an exception."""
    started = time.time()
    loc = locator if isinstance(locator, dict) else {}
    account = getattr(ctx, "account", None) or loc.get("account")
    st = {"pages": 0, "searches": 0, "seen": [], "exclude": frozenset(exclude or ())}
    calls = 0
    if not _can_locate(driver):
        res = _locate_result(None, {}, {}, [], False)
    else:
        before = gprpc.stats(driver)["calls"]
        res = _waterfall(driver, loc, ctx, account, st, approaches)
        if res["found"]:
            res = _arbitrate(driver, res, loc, ctx, account, st, approaches)
        calls = gprpc.stats(driver)["calls"] - before
    res.update(candidates_seen=len(st["seen"]), candidates=list(st["seen"]), pages_opened=st["pages"],
               searches=st["searches"], rpc_calls=calls, elapsed=round(time.time() - started, 3))
    return res


# ------------------------------------------------------------------ the triggers (P-20: (ii) direct, then (i) Shift+D)
def _watched_now(ctx):
    """The names in the folder the entry's downloads land in (ctx.target['dir'] — its incoming folder, or
    ~/Downloads in the fallback / legacy modes), without the incoming marker; unreadable: empty."""
    folder = (ctx.target or {}).get("dir") or SYSTEM_DOWNLOADS_FOLDER
    try:
        return set(os.listdir(folder)) - {INCOMING_MARKER}
    except OSError:
        return set()


def _download_names(item):
    """The names a located object's download may carry: its exact uploaded name and its found item's file name (an
    item uploaded under another name keeps that one)."""
    record = (item.get("_trigger") or {}).get("record") or {}
    names = ((item.get("locator") or {}).get("uploaded_name"), record.get("filename"))
    return [name for name in dict.fromkeys(names) if isinstance(name, str) and name]


def _download_started(ctx, before, others=(), seconds=TRIGGER_CONFIRM_S):
    """[P-12 / P-20] True once a download requested after `before` (the watched folder's names then) shows: a new
    partial ('<name>.crdownload', Chrome's 'Unconfirmed N.crdownload') or a new finished file whose partial was not
    there before (a small file finishes between two looks) — within `seconds` (0: one look). An earlier download that
    finishes meanwhile does not count: its partial was already there. Nor does a file of `others` — the names the
    OTHER downloads of the same window may carry (_download_names), their ' (n)' variants and partials included:
    Chrome may name or finish one of them while this request is confirmed (an 'Unconfirmed N.crdownload' names no
    object, so it counts)."""
    others = {_family_key(name) for name in others if isinstance(name, str) and name}
    deadline = time.time() + seconds
    while True:
        for name in _watched_now(ctx) - before:
            if others and any(base in others for base in _family_bases(name)):
                continue  # another object's download of this window
            if name.endswith(_CRDOWNLOAD) or name + _CRDOWNLOAD not in before:
                return True
        if time.time() >= deadline:
            return False
        time.sleep(TRIGGER_POLL_S)


def _bounded_get(driver, url, seconds):
    """driver.get(url) with the page-load timeout lowered to `seconds` for this one navigation, then restored (its
    previous value, else Selenium's default). A driver without set_page_load_timeout (a test stand-in) just gets.
    A timeout raises as Selenium raises it — the caller decides by the download folder, not by the page."""
    setter = getattr(driver, "set_page_load_timeout", None)
    if not callable(setter):
        driver.get(url)
        return
    try:
        previous = driver.timeouts.page_load
    except Exception:
        previous = None
    setter(seconds)
    try:
        driver.get(url)
    finally:
        try:
            setter(previous if isinstance(previous, (int, float)) and previous > 0 else SELENIUM_PAGE_LOAD_S)
        except Exception:
            pass


def _off_photos(driver):
    """True when the tab shows a page that is not Google Photos — a minted link that answered with a page of its own.
    An unreadable address counts as Google Photos (a browser glitch is no page answer). Only the host is read."""
    try:
        return not _is_photos_host(urllib.parse.urlparse(driver.current_url).hostname)
    except Exception:
        return False


def _back_on_photos(driver):
    """After a direct download: a link answered with a page (HTML) leaves the tab on it — put the tab back on
    Google Photos (check_session_alive after it). Only the host is read; the link's address is never kept (P-20)."""
    try:
        host = urllib.parse.urlparse(driver.current_url).hostname
    except Exception:
        host = None
    if not _is_photos_host(host):
        driver.get(PHOTOS_URL)
        check_session_alive(driver)


def _direct_trigger(driver, item, ctx, others=()):
    """(ii) P-20's direct download: a FRESH original-quality link for the located item — gprpc.download_url (item
    info's [7], else pLFTfd with that ONE key) — minted now and opened in the automation Chrome, whose downloads go
    to the entry's folder (_rearm_downloads right before). The link lives in this call only: never stored, printed,
    logged or journaled (gprpc's redacted DownloadUrl; reveal() only for driver.get). True once the download shows
    in the watched folder (_download_started — never one of `others`, the window's other downloads); False when RPC
    is off, no link was minted or nothing started (an HTML answer, a refused link) — the caller then tries (i),
    Shift+D. A link that answers with a page of its own (a sign-in or error page — or media Chrome would play) puts
    its address in the tab: the tab goes back to Google Photos AT ONCE and the folder gets one look, never a wait on
    that page (the address is a bearer credential while valid — P-20)."""
    if not _rpc_ok(driver, ctx):
        return False
    name = item["filename"]
    try:
        link = gprpc.download_url(driver, item["_located"]["photo_id"])
    except gprpc.RpcUnavailable as e:
        _note_fallback(ctx, e)
        link = None
    if link is None:
        print(f"     ↪️ {name}: no download link was minted — Shift+D next.")
        return False
    try:
        _rearm_downloads(driver)  # P-12: this tab saves into the entry's folder
        before = _watched_now(ctx)
        try:
            _bounded_get(driver, link.reveal(), DIRECT_PAGE_LOAD_S)
        except Exception:  # a navigation that becomes a download may end in an error or a timeout: the folder decides
            pass
        link = None
        page = _off_photos(driver)  # the link answered with a page of its own: no download comes from it
        if page:
            _back_on_photos(driver)  # at once: that page's address is the link itself (P-20)
        started = _download_started(ctx, before, others, 0 if page else TRIGGER_CONFIRM_S)
        _back_on_photos(driver)
    except SessionExpiredError:
        raise
    except Exception as e:  # the type only — a browser message could carry the page's address
        print(f"     ⚠️ {name}: the direct download failed ({type(e).__name__}) — Shift+D next.")
        return False
    if started:
        print(f"     ⬇️ {name}: direct download")
    elif page:
        print(f"     ↪️ {name}: the direct download did not start — its link answered with a web page — Shift+D next.")
    else:
        print(f"     ↪️ {name}: the direct download did not start within {TRIGGER_CONFIRM_S:g} s — Shift+D next.")
    return started


def _shows_item(driver, photo_id, key):
    """The open page IS the located item — checked right before every keystroke, so Shift+D never downloads anything
    else: the page's own address names photo_id (an item page also embeds its NEIGHBOURS' records, so a record in the
    source alone proves nothing about which item is open), and its embedded record of photo_id is there — with a
    content key, holding those bytes."""
    deadline = time.time() + ITEM_WAIT_S
    while True:
        try:
            shown = gpweb.photo_id_from_url(driver.current_url)
        except Exception:
            shown = None
        rec = gpweb.page_record(driver.page_source, photo_id) if shown == photo_id else {}
        if rec:
            return not key or gpweb.dedup_keys_equal(rec.get("dedup_key"), key)
        if time.time() >= deadline:
            return False
        time.sleep(ITEM_POLL_S)


def _on_item_page(driver, photo_id):
    """The open page is already photo_id's own item page — its address names the item and its record is in the page
    now (one instant look; _shows_item still checks both before any keystroke). A page the item's locate (or the
    drift check) opened is then reused for Shift+D instead of loaded again."""
    try:
        return (gpweb.photo_id_from_url(driver.current_url) == photo_id
                and bool(gpweb.page_record(driver.page_source, photo_id)))
    except Exception:
        return False


def _shift_d_here(driver, ctx, others=()):
    """Shift+D on the item page now shown (once its viewer is up); True once a download shows in the watched folder
    (never one of `others`, the window's other downloads)."""
    time.sleep(VIEWER_SETTLE_S)
    before = _watched_now(ctx)
    webdriver.ActionChains(driver).key_down(Keys.SHIFT).send_keys("d").key_up(Keys.SHIFT).perform()
    return _download_started(ctx, before, others)


def _open_from_results(driver, photo_id, query):
    """The click path: search `query` and open the item from its results by clicking the tile whose link names
    photo_id — never a position, never a timeline thumbnail (IMP-C35). True when it was opened."""
    try:
        _ui_search(driver, query)
    except _SearchProblem:
        return False
    last = None
    for _ in range(LOCATE_MAX_SCROLLS + 1):
        for el in _visible_result_links(driver):
            if gpweb.photo_id_from_url(el.get_attribute("href")) == photo_id:
                driver.execute_script("arguments[0].click();", el)
                check_session_alive(driver)
                return True
        position = driver.execute_script(gpweb.SCROLL_JS)
        if position == last:
            return False
        last = position
        time.sleep(1.0)
    return False


def _click_queries(item):
    """The searches that list the located item, best first: the one that found it, its exact uploaded name, its local
    day (the taken second's day search)."""
    res, loc = item["_located"], item.get("locator") or {}
    queries = [res.get("query"), loc.get("uploaded_name")]
    record = res.get("record") or {}
    if isinstance(record.get("taken_ms"), int):
        offset = record.get("tz_offset_ms") if isinstance(record.get("tz_offset_ms"), int) else None
        days = gpweb.local_days(record["taken_ms"], offset, loc.get("device_tz_offset_min"), window=0)
        queries.append(gpweb.day_query(days[0]) if days else None)
    return [q for q in dict.fromkeys(queries) if isinstance(q, str) and q.strip()]


def _shift_d_trigger(driver, item, ctx, others=()):
    """(i) the located item's page, focused, then Shift+D — the web app's own original download. When the keystroke
    starts nothing on a directly loaded page (unverified live: PLAN Step 12 / R2), the click path opens the item from
    search results and presses it there. The page must show the located item (_shows_item) before any keystroke; one
    already open on it (_on_item_page — e.g. the page its locate read) is used as it is, not loaded again.
    True once a download shows in the watched folder (never one of `others`, the window's other downloads)."""
    name = item["filename"]
    pid = item["_located"]["photo_id"]
    key = _content_key(item.get("locator") or {})
    try:
        if hasattr(driver, "execute_cdp_cmd"):
            use_photos_tab(driver)  # focused (keystrokes reach only a front tab), downloads re-armed here (P-12)
        if not _on_item_page(driver, pid):
            driver.get(f"{PHOTOS_URL}/photo/{pid}")
            check_session_alive(driver)
        if _shows_item(driver, pid, key) and _shift_d_here(driver, ctx, others):
            print(f"     ⬇️ {name}: Shift+D on its item page")
            return True
        for query in _click_queries(item):
            if (_open_from_results(driver, pid, query) and _shows_item(driver, pid, key)
                    and _shift_d_here(driver, ctx, others)):
                print(f"     ⬇️ {name}: Shift+D after opening it from its search results")
                return True
    except SessionExpiredError:
        raise
    except Exception as e:  # one object's page trouble never stops the entry: today's attempts follow
        print(f"     ⚠️ {name}: Shift+D failed ({type(e).__name__}).")
        return False
    print(f"     ↪️ {name}: Shift+D started no download.")
    return False


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
        itself is 'failed' with the stage's own failed-list row, never booked twice
        (Step 12: the stages' own booking, _record_unfetched, runs first).

    [IMP-C25 Step 12 — candidate C] What it does (nothing for a driver that cannot
    navigate or run page scripts — a stand-in: today's flow, byte for byte):
      1. locate every pending object (locate_object; the entry's content keys go
         in ONE swbisb batch; the run's record cache serves what earlier objects
         read). A located object gets item['_trigger'] (the journal says
         `located`); a learned photo_id RPC and its own page both call gone is
         recorded stale in the learned sidecar; a content-keyed object whose every
         candidate — those today's attempts would click included — is proven
         another item ends 'failed' with an s4-skipped row (OD-5).
      2. fetch the located objects, DIRECT_DOWNLOAD_CONCURRENCY at a time (P-20):
         (ii) the direct download for each, one harvest round; then (i) Shift+D
         (+ the click path) for each still pending — a link that started nothing,
         broke, or served other bytes (SHA-256 decides) — and one more round. A
         VERIFIED item's link that delivered other bytes is no wrong pick (P14: a
         transcode may be served): its `_wrong` mark is cleared, Shift+D follows.
         When Shift+D — the web app's own original — delivers other bytes as well,
         that mark is kept apart (`_other_bytes`): unless today's attempts still
         fetch the object, it ends `inconsistent` (P-18, IMP-C27).
      3. a located object none of whose requests fetched it is located ONCE more
         without that item (P-20's chain: Shift+D → the search waterfall) —
         another VERIFIED copy, requested the same way (_another_copy).
    Whatever is still pending gets today's attempts (S4) unchanged; one the stages
    examined that those attempts did not fetch either goes to the failed list after
    them — one row with the stages' evidence and any wrong pick of those attempts
    (_record_unfetched, from _fetch_items, before _record_wrong_items)."""
    if not _can_locate(driver):
        return None
    pending = [i for i in queue if i["status"] == "pending"]
    if not pending:
        return None
    run = _locator_run(ctx)
    account = ctx.account or (pending[0].get("locator") or {}).get("account")
    wanted = run.wanted.setdefault(account, set())
    for item in pending:
        loc = item.get("locator") or {}
        sha1 = _sha1_of(loc, _content_key(loc))
        if sha1:
            wanted.add(sha1)  # one swbisb batch for the entry (≤ gprpc.MAX_BATCH_KEYS)
    print(f"\n   === EXACT ITEM ({len(pending)} files) ===")
    located = [item for item in pending if _locate_item(driver, item, ctx)]
    _journal_sync(ctx, queue)
    for start in range(0, len(located), DIRECT_DOWNLOAD_CONCURRENCY):
        _fetch_window(driver, located[start:start + DIRECT_DOWNLOAD_CONCURRENCY], queue, ctx)
    _keep_other_bytes(located)
    # P-20's chain — Shift+D → the search waterfall (P-8): another verified copy before today's blind attempts
    again = [item for item in located if item["status"] == "pending" and _another_copy(driver, item, ctx)]
    if again:
        _journal_sync(ctx, queue)
    for start in range(0, len(again), DIRECT_DOWNLOAD_CONCURRENCY):
        _fetch_window(driver, again[start:start + DIRECT_DOWNLOAD_CONCURRENCY], queue, ctx)
    _keep_other_bytes(again)
    for item in located:
        if item["status"] == "pending":
            item["_trigger"]["method"] = None  # today's attempts take over (the journal then says legacy)
    return None


def _tried(res):
    """locate_object's per_approach as the failed list's `tried` ([{approach, outcome, n}], waterfall order)."""
    return [{"approach": a, "outcome": v["outcome"], "n": v["n"]} for a, v in res["per_approach"].items()]


def _tried_text(res):
    return " · ".join(f"{a} {v['outcome']}({v['n']})" for a, v in res["per_approach"].items()) or "nothing to search by"


def _expected_local(loc):
    """The time Google Photos shows for the object — its taken second in its own timezone, 'YYYY-MM-DD HH:MM:SS
    GMT+08:00' (the learned offset, else the pushing phone's; UTC when neither is known): map_photos' format of the
    failed list's expected_taken_local. None without a taken time."""
    taken = loc.get("taken_ms")
    if not (isinstance(taken, int) and not isinstance(taken, bool)):
        return None
    tz = loc.get("tz_offset_ms")
    offset = tz // 60000 if isinstance(tz, int) else loc.get("device_tz_offset_min")
    when = datetime.fromtimestamp(taken // 1000, timezone.utc)
    if not (isinstance(offset, int) and not isinstance(offset, bool)):
        return when.strftime("%Y-%m-%d %H:%M:%S UTC")
    sign = "-" if offset < 0 else "+"
    return ((when + timedelta(minutes=offset)).strftime("%Y-%m-%d %H:%M:%S")
            + f" GMT{sign}{abs(offset) // 60:02d}:{abs(offset) % 60:02d}")


def _record_stale(item, res, ctx):
    """A learned photo_id that RPC AND its own item page call gone (locate_object's `stale`) is recorded stale in the
    session account's learned sidecar (P-10: it supersedes every older record naming it; the reducer reads it as
    s0_photo_id not-found). Only the fetch records it — the read-only callers just report it."""
    loc = item.get("locator") or {}
    if ctx.account not in mvcommon.GP_ACCOUNTS or not loc.get("manual_id") or not mvcommon._is_sha256(item.get("hash")):
        return
    for photo_id in res.get("stale") or ():
        try:
            mvcommon.append_learned(ctx.account, {
                "manual_id": loc["manual_id"], "label": loc.get("label"), "sha256": item["hash"], "photo_id": photo_id,
                "source": "fetch", "stale": True,
                "approach": {"tried": "s0_photo_id", "outcome": "not-found", "n": 0, "via": "rpc"}})
        except (OSError, ValueError) as e:
            print(f"     ⚠️ Not recorded in the learned sidecar ({type(e).__name__}: {e}).")


def _trigger_of(res):
    """item['_trigger'] for a located object (_run_locator_phase's contract): the found item's record and the stage's
    verdict — what learn-on-fetch reads after a SHA-256 match; the method is set when a request goes out."""
    return {"method": None, "record": dict(res["record"]),
            "approach": {"tried": res["stage"], "outcome": res["outcome"], "n": res["n"], "via": res["via"]}}


def _another_copy(driver, item, ctx):
    """P-20's fallback chain after the located item's own requests fetched nothing verified: the search waterfall (P-8)
    once more, WITHOUT that item — another VERIFIED copy of the object (the same bytes uploaded twice; or, for an object
    without a content key, another upload under its exact name — a re-split set's older one: SHA-256 decides) before
    today's blind attempts (S4). One try per object, read-only like any locate (the run's record cache makes it
    cheap). True when one was found: it becomes the item requested (item['_located'] and ['_trigger']); the first find
    stays in item['_first_located'] — the failed list's evidence (_record_unfetched)."""
    name, first = item["filename"], item["_located"]
    try:  # nor the photo_ids its first locate found gone: asked again, they would only be recorded stale twice
        res = locate_object(driver, item.get("locator") or {}, ctx, exclude=[first["photo_id"], *first["stale"]])
    except (SessionExpiredError, gprpc.RpcRefused):
        raise
    except Exception as e:  # one object's trouble never stops the entry: today's attempts follow
        print(f"   > ⚠️ {name}: looking for another copy failed ({type(e).__name__}) — today's search follows.")
        return False
    _record_stale(item, res, ctx)
    if not res["found"]:
        print(f"   > 🔍 {name}: no other verified copy of it — today's search follows.")
        return False
    item["_first_located"], item["_located"], item["_trigger"] = first, res, _trigger_of(res)
    how = " (normalized name)" if res["name_normalized"] else ""
    print(f"   > 🎯 {name}: its item was not fetched — another verified copy: {_STAGE_LABEL[res['stage']]} "
          f"{res['approach']} · proof {res['proof']}{how} · via {res['via']}")
    return True


def _keep_other_bytes(items):
    """A located object still pending whose `_wrong` mark survived the locator's rounds — its VERIFIED item's own
    download (Shift+D; the direct round's mark is cleared) held other bytes — has it kept apart (`_other_bytes`) from
    whatever is requested next: P-18 `inconsistent` (_record_unfetched)."""
    for item in items:
        if item["status"] == "pending" and item.get("_wrong"):
            item["_other_bytes"] = item.get("_other_bytes", 0) + item.pop("_wrong")


def _locate_item(driver, item, ctx):
    """Step 1 of _run_locator_phase for one object; True when it was located (its _trigger is set)."""
    name, loc, stats = item["filename"], item.get("locator") or {}, _session_stats(ctx)
    try:
        res = locate_object(driver, loc, ctx)
    except (SessionExpiredError, gprpc.RpcRefused):
        raise  # signed out: the batch stops with its remediation · a refused rpcid: a bug, never swallowed
    except Exception as e:  # one object's trouble never stops the entry: today's attempts follow
        print(f"   > ⚠️ {name}: the exact-item search failed ({type(e).__name__}) — today's search follows.")
        return False
    item["_located"] = res
    stats["objects"] += 1
    _record_stale(item, res, ctx)
    if res["found"]:
        stats["located"] += 1
        item["_trigger"] = _trigger_of(res)
        how = " (normalized name)" if res["name_normalized"] else ""
        if res["rule"] == "newest-set":
            how += f" · the newest of {len(res['alternates']) + 1} same-name uploads"
        print(f"   > 🎯 {name}: {_STAGE_LABEL[res['stage']]} {res['approach']} · proof {res['proof']}{how} · "
              f"via {res['via']}")
        return True
    if res["s4_skip"]:
        item["status"] = "failed"
        listed = "; added to the failed list" if _record_failed(
            item, ctx, "s4-skipped", _tried(res), res["candidates"][:LOCATE_MAX_CANDIDATES], _expected_local(loc)) else ""
        print(f"   > ⏭️ {name}: every candidate is another item ({_tried_text(res)}) — today's search is skipped "
              f"(OD-5){listed}.")
    else:
        print(f"   > 🔍 {name}: not located ({_tried_text(res)}) — today's search follows.")
    return False


def _fetch_window(driver, window, queue, ctx):
    """Step 2 of _run_locator_phase for ≤ DIRECT_DOWNLOAD_CONCURRENCY located objects: (ii) direct, harvest; (i)
    Shift+D for what is still pending, harvest. Each round harvests only its own requests (+ the entry's fetched
    objects, so a duplicate is told apart) — an object not requested yet never holds a round up. A request's start
    is never confirmed by a download of the window's OTHER objects (_download_started's `others`)."""
    run = _locator_run(ctx)
    for method, trigger in (("direct", _direct_trigger), ("shift_d", _shift_d_trigger)):
        todo = [i for i in window if i["status"] == "pending"]
        if not todo or (method == "direct" and (run.direct_off or not _rpc_ok(driver, ctx))):
            continue
        for item in todo:
            item["_trigger"]["method"] = method
            own = {_family_key(name) for name in _download_names(item)}
            others = [name for other in window if other is not item for name in _download_names(other)
                      if _family_key(name) not in own]
            if _fire(item, lambda item=item, trigger=trigger, others=others: trigger(driver, item, ctx, others)):
                _session_stats(ctx)[method] += 1
        _journal_sync(ctx, queue)
        requested = {id(i) for i in todo if i["triggered"]}
        if not requested:
            continue
        rnd = [i for i in queue if i["status"] == "done" or id(i) in requested]
        _note_losses(rnd, harvest_downloads(rnd, ctx), ctx)
        if method == "direct":
            _after_direct_round(todo, run)
        _journal_sync(ctx, queue)


def _after_direct_round(todo, run):
    """A direct round whose download was not this object's bytes (the harvester deleted it; _note_losses marked
    `_wrong`): the item is VERIFIED, so the link served other bytes (P14: a transcode, or a partial) — no wrong pick.
    The mark is cleared and Shift+D follows; when the item's proof is its dedupKey the link is what failed, so direct
    downloads stay off for the rest of the run (one line — the harvester's timeout is paid once, not per object)."""
    for item in todo:
        if item["status"] != "pending" or not item.pop("_wrong", None):
            continue
        print(f"   > ↪️ {item['filename']}: its direct link served other bytes than the verified item — "
              f"Shift+D on its item page next.")
        if item["_located"]["proof"] == "dedupkey" and not run.direct_off:
            run.direct_off = "other-bytes"
            print("   > ⚠️ The direct download link served other bytes than a content-verified item (a transcode?) "
                  "— direct downloads are off for the rest of this run; Shift+D fetches the originals.")


def _locator_session_line(driver, ctx):
    """The line an account session that ran the exact-item stages ends with — its counts and gprpc's `RPC calls: N
    (errors E)` (P-9: one per browser session) — or None when the stages did not run in it (the kill switch, a
    stand-in driver: today's output, unchanged). Never a P-13 web marker."""
    run = getattr(ctx, "locator", None)
    stats = run.stats.get(ctx.account) if run is not None else None
    if not stats or not stats["objects"] or not _can_locate(driver):
        return None
    return (f"   > 🔎 Exact item ('{ctx.account}'): {stats['located']} of {stats['objects']} object(s) located · "
            f"{stats['direct']} direct download(s) · {stats['shift_d']} Shift+D · {gprpc.summary_line(driver)}")


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


def _record_failed(item, ctx, reason, tried=None, candidates=None, expected_taken_local=None):
    """One failed-list row (P-18) for an object this run gives up on: run, account,
    manual_id, label, uploaded_name, the sha256 pin, `reason` and — when given —
    what was `tried` ([{approach, outcome, n}]), the `candidates` seen (photo ids —
    resolve_photos shows them) and the `expected_taken_local` time (the exact-item
    stages, IMP-C25 Step 12). True when written. Needs the session account and the
    owning id — a stand-alone call has neither and writes nothing; a failed write
    prints one warning line."""
    loc = item.get("locator") or {}
    if ctx.account not in mvcommon.GP_ACCOUNTS or not loc.get("manual_id"):
        return False
    sha256 = item.get("hash") if mvcommon._is_sha256(item.get("hash")) else None
    try:
        mvcommon.append_failed({"run": ctx.run_id, "account": ctx.account, "manual_id": loc["manual_id"],
                                "label": loc.get("label"), "sha256": sha256,
                                "uploaded_name": loc.get("uploaded_name"),
                                "expected_taken_local": expected_taken_local, "tried": tried,
                                "candidates": candidates or None, "reason": reason})
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


def _record_unfetched(queue, ctx):
    """[IMP-C25 Step 12 — P-18] After today's attempts and IMP-C34's retry, BEFORE _record_wrong_items: an object the
    exact-item stages examined (item['_located']) that nothing fetched ends 'failed' with ONE failed-list row carrying
    their evidence — tried = every stage's verdict (of its first locate when another copy was tried:
    item['_first_located']), candidates = the items they saw (the items requested first), expected_taken_local — and
    the reason:
      inconsistent  it was LOCATED, but its verified item's own download held other bytes than the library's
                    (`_other_bytes`, kept apart by _run_locator_phase): the item IS the object — its content key or
                    its exact name proves it — yet what Google Photos serves is not its bytes (a storage-saver or
                    partial upload: IMP-C27), not "the search picked another item";
      wrong-item    today's attempts fetched another item for it (`_wrong`, booked by _note_losses);
      otherwise     no stage pinned it: ambiguous (a stage saw candidates it could not tell apart) · wrong-item (every
                    candidate proven another item) · not-found.
    What today's attempts fetched instead is folded into the row as legacy wrong-item(n), replacing the OD-5
    preview's verdict (what the attempts fetched is the stronger fact). A LOCATED object that simply ends unfetched
    gets no row (its item is known — a re-run fetches it); an object already 'failed' (lost twice, OD-5's skip) has
    its row. _record_wrong_items, which runs next, is left only the objects the stages never saw (the kill switch, a
    stand-in driver, a locate that raised): never booked twice. Without the exact-item stages no object carries
    `_located`: nothing happens."""
    for item in queue:
        res = item.get("_located")
        if not res or item["status"] != "pending":
            continue
        first = item.get("_first_located") or res
        wrong, other = item.get("_wrong"), res["found"] and item.get("_other_bytes")
        if res["found"] and not (wrong or other):
            continue  # its item is known: a re-run fetches it
        tried = _tried(first)
        if wrong:
            legacy = _LEGACY_STAGE[0]
            tried = [t for t in tried if t["approach"] != legacy] + [
                {"approach": legacy, "outcome": "wrong-item", "n": wrong}]
        if other:
            reason = "inconsistent"
        elif wrong:
            reason = "wrong-item"
        else:
            outcomes = [v["outcome"] for v in res["per_approach"].values()]
            reason = next((o for o in ("ambiguous", "wrong-item") if o in outcomes), "not-found")
        item["status"] = "failed"
        requested = [r["photo_id"] for r in (first, res) if r["found"]]
        candidates = list(dict.fromkeys(requested + first["candidates"] + res["candidates"]))
        listed = "; added to the failed list" if _record_failed(
            item, ctx, reason, tried, candidates[:LOCATE_MAX_CANDIDATES],
            _expected_local(item.get("locator") or {})) else ""
        why = ("its item was found, but what it serves held other bytes than the library's" if other
               else "its item was found but not fetched, and today's search then picked another item" if res["found"]
               else "no stage found its exact item, and today's search picked another item" if wrong
               else "no stage found its exact item")
        print(f"     ❌ Not fetched: {item['filename']} — {why} ({reason}){listed}.")


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
    # [IMP-C25 Step 12] An object the exact-item stages examined and nothing fetched ends on the failed list with ONE
    # row carrying their evidence (+ today's wrong pick) — before _record_wrong_items (P-18; without the stages: no-op).
    _record_unfetched(queue, ctx)
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
                        line = _locator_session_line(driver, ctx)  # [IMP-C25 Step 12] None without the stages
                        if line:
                            print(line)
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


# ==========================================
#   IMP-C25 STEP 12 — `python mainfetch.py locate` (read-only diagnostic)
# ==========================================
def parse_locate_args(argv):
    """`mainfetch.py locate <id> [episodes <range>] [--fetchExtras] [--probe-direct]` -> {manual_id, ep_range,
    fetch_extras, probe_direct} (the flags in any order after the id, the extras aliases as for fetch). Prints the
    usage and exits 1 on a bad invocation, like parse_fetch_args. Pure."""
    if len(argv) < 3 or argv[1] != "locate":
        print("Usage: locate [id] [episodes <range>] [--fetchExtras] [--probe-direct]")
        sys.exit(1)
    rest, ep_range, i = list(argv[3:]), None, 0
    while i < len(rest):
        if rest[i] == "episodes" and i + 1 < len(rest):
            ep_range = rest[i + 1]
            i += 2
            continue
        i += 1
    return {"manual_id": argv[2], "ep_range": ep_range,
            "fetch_extras": any(t in ("--fetchExtras", "--fetch-extras", "--extras", "--extra") for t in rest),
            "probe_direct": "--probe-direct" in rest}


def _locate_lines(item, res):
    """The terminal lines of one located (or not) object — ids and the item's ADDRESS are shown here only (P-15:
    never written to any file); never a download link."""
    label = (item.get("locator") or {}).get("label") or item["filename"]
    cost = (f"{res['elapsed']:.1f} s · pages {res['pages_opened']} · searches {res['searches']} · "
            f"RPC {res['rpc_calls']}")
    lines = []
    if res["found"]:
        how = " (normalized name)" if res["name_normalized"] else ""
        lines.append(f"   {label} · FOUND · {_STAGE_LABEL[res['stage']]} {res['approach']} · proof {res['proof']}{how}"
                     f" · via {res['via']} · {cost}")
        lines.append(f"     photo_id {res['photo_id']} · {PHOTOS_URL}/photo/{res['photo_id']}")
        if res["rule"] == "newest-set":
            lines.append(f"     the newest of {len(res['alternates']) + 1} uploads under this name (re-split set) — "
                         f"a fetch's SHA-256 decides")
        elif res["alternates"]:
            lines.append(f"     the same bytes also in: {', '.join(res['alternates'])}")
    else:
        lines.append(f"   {label} · NOT FOUND · {cost}")
    tail = " — a fetch would skip today's search (OD-5)" if res["s4_skip"] else ""
    lines.append(f"     tried: {_tried_text(res)}{tail}")
    if res["stale"]:
        lines.append(f"     stale (RPC and its page: gone): {', '.join(res['stale'])}")
    return lines


def _locate_entry(driver, entry, entry_id, ctx, probe_direct, tally):
    """Locate the objects one entry uploads in this session's account (a multi-account entry: only this account's —
    ctx.plans) and print them. Builds the queue against a folder that never exists, so every object is listed (PLAN
    Step 10's rule for locators without fetching); creates nothing."""
    import tempfile
    import uuid
    nowhere = os.path.join(tempfile.gettempdir(), f"mediavault-locate-{uuid.uuid4().hex}")  # never created
    plan = ctx.plan_for(entry, entry_id)
    queue = build_fetch_queue(entry, nowhere, ctx, entry_id=entry_id)
    mine = queue if plan is None else [i for i in queue
                                       if plan["by_sha"].get(i["hash"], plan["default"]) == ctx.account]
    print(f"\n🔸 {entry.get('filename')} ({entry.get('short_id', 'N/A')}) — {len(mine)} object(s) in '{ctx.account}'")
    wanted = _locator_run(ctx).wanted.setdefault(ctx.account, set())
    for item in mine:
        loc = item.get("locator") or {}
        sha1 = _sha1_of(loc, _content_key(loc))
        if sha1:
            wanted.add(sha1)
    for item in mine:
        res = locate_object(driver, item.get("locator") or {}, ctx)
        tally["objects"] += 1
        tally["found"] += bool(res["found"])
        for line in _locate_lines(item, res):
            print(line)
        if probe_direct and res["found"]:
            print(f"     direct link without the browser's cookies: {_probe_direct(driver, res['photo_id'])}")


def cmd_locate(manual_id, ep_range=None, fetch_extras=False, probe_direct=False):
    """[IMP-C25 Step 12] `python mainfetch.py locate <id> [episodes <r>] [--fetchExtras] [--probe-direct]` — a
    READ-ONLY diagnostic: where each uploaded object of the target lives in Google Photos, found by the fetch's own
    exact-item stages (locate_object, candidate C) in the account it is routed to (P-4) — per object the stage, proof,
    approach, path (rpc | ui), cost and its photo_id (this terminal only; nothing is written anywhere).

    It downloads nothing, presses no Shift+D, clicks no tile, writes no learned record, failed-list row, journal or
    library; it takes the fetch lock WITHOUT waiting (a running fetch owns Chrome's debug port and profile: locate
    refuses then) and closes the Chrome it opened (_close_session). --probe-direct also mints each found item's
    direct link (P-20) and asks, without the browser's cookies, whether it answers with media — a HEAD request
    only, its answer yes/no + content type; the link is never shown."""
    print(f"--- LOCATE: {manual_id} (read-only: nothing is downloaded or written) ---")
    active = profile_for_id(manual_id)
    print(f"   > [Account] Profile for {manual_id}: '{active}' ({CHROME_PROFILES.get(active, '?')})")
    targets = resolve_targets(manual_id, ep_range)
    target_ids = resolve_target_ids(manual_id, ep_range)
    extras = resolve_title_extras(manual_id) if fetch_extras else []
    if not targets and not extras:
        print("❌ No valid targets found.")
        return
    ctx = FetchContext(run_id=f"locate-{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}-{os.getpid()}")
    groups = _account_groups(manual_id, active, targets, target_ids, extras, ctx)
    tally = {"objects": 0, "found": 0}
    try:
        with fetch_session_lock(blocking=False):
            completed = _locate_sessions(groups, active, ctx, probe_direct, tally)
    except mvcommon.LockHeldError:
        print("❌ A fetch is running (the fetch session lock is held) — locate needs the same Chrome profile and "
              "port; run it again when that fetch has finished.")
        return
    if completed:
        print(f"\n✅ Located {tally['found']} of {tally['objects']} object(s). Nothing was downloaded or written.")


def _locate_sessions(groups, active, ctx, probe_direct, tally):
    """cmd_locate's account sessions — cmd_fetch_route's order, pre-flight and port guard; one Chrome per account,
    closed at its end — a session that stops early as the LAST one (no later session needs the debug port, so a
    Chrome it only attached to keeps its windows). True when every session ran to its end; False after a stop (its
    line says why)."""
    for n, (profile, group_targets, group_extras) in enumerate(groups):
        if profile != active:
            print(f"   > [Account] Switching to profile '{profile}' for {len(group_targets) + len(group_extras)} "
                  f"item(s)")
        problem = profile_preflight(profile)
        if problem:
            print(f"❌ {problem[:1].upper()}{problem[1:]}")
            return False
        if n and not _debug_port_free():
            print(f"❌ Cannot switch to profile '{profile}': Chrome's debug port 9222 is still in use.")
            return False
        driver = init_driver(profile)
        if not driver:
            return False
        ctx.start_session(profile)
        _note_session(ctx, driver, profile)
        completed = False
        try:
            for entry, entry_id in list(group_targets) + [(ex, None) for ex in group_extras]:
                _locate_entry(driver, entry, entry_id, ctx, probe_direct, tally)
            completed = True
        except SessionExpiredError:
            print(f"❌ Profile '{profile}' is logged out. Open Chrome with --user-data-dir={CHROME_PROFILES[profile]}, "
                  f"sign in to photos.google.com, then re-run.")
            return False
        except KeyboardInterrupt:
            print("\n🛑 Stopped by user.")
            return False
        finally:
            print(f"   > {gprpc.summary_line(driver)}")
            try:
                _close_session(driver, ctx, last=not completed or n + 1 == len(groups))
            except Exception as e:
                print(f"   > ⚠️ Closing Chrome failed ({type(e).__name__}: {e}).")
            try:
                driver.quit()
            except Exception:
                pass
    return True


PROBE_HEAD_TIMEOUT_S = 15.0   # --probe-direct: the cookie-less HEAD request's timeout
_MEDIA_TYPES = ("video/", "image/", "application/octet-stream")


def _head_without_cookies(url):
    """One HEAD request for `url` from Python — no cookie jar, no browser session, nothing downloaded — -> (HTTP
    status or None, content type or None, the failure's type name or None). Never raises; never puts the URL in what
    it returns (an exception is reduced to its type name)."""
    import urllib.error
    import urllib.request
    request = urllib.request.Request(url, method="HEAD", headers={"User-Agent": "MediaVault-locate"})
    try:
        with urllib.request.urlopen(request, timeout=PROBE_HEAD_TIMEOUT_S) as answer:
            ctype = (answer.headers.get("Content-Type") or "").split(";")[0].strip() or None
            return answer.status, ctype, None
    except urllib.error.HTTPError as e:
        return e.code, None, None
    except Exception as e:
        return None, None, type(e).__name__


def _probe_direct(driver, photo_id):
    """--probe-direct (PLAN Step 12, §M6; evidence for the resumable-transfer follow-up, Suggested next task #10):
    mint the item's direct link (P-20) and ask WITHOUT the browser's cookies whether it answers with media. HEAD
    only — nothing is downloaded; the link is never shown, logged or kept. -> 'yes (<content type>)' or 'no (<why>)'."""
    try:
        link = gprpc.download_url(driver, photo_id)
    except gprpc.RpcUnavailable as e:
        return f"no (no link: {type(e).__name__})"
    if link is None:
        return "no (no link was minted)"
    status, ctype, failure = _head_without_cookies(link.reveal())
    link = None
    if failure:
        return f"no ({failure})"
    if isinstance(status, int) and 200 <= status < 300 and ctype and ctype.lower().startswith(_MEDIA_TYPES):
        return f"yes ({ctype})"
    return f"no (HTTP {status}{', ' + ctype if ctype else ''})"


# ==========================================
#   IMP-C25 STEP 13 — `python mainfetch.py check` (check_photos' browser half)
# ==========================================
# docs/feature-fetch-datetime/PLAN.md Step 13. main.cmd_check_photos resolves a scope into a run directory
# (<GPHOTOS_STATE_DIR>/check/<ts>/run.json) and runs `python mainfetch.py check --run <dir> …` — cmd_check below:
#   * under the fetch session lock, taken WITHOUT waiting (a fetch, a crawl or the warm-up owns Chrome's debug port and
#     the profiles) and kept fresh by the crawler's progress heartbeat: an hours-long sweep is never reclaimed as stale,
#     and it yields when another command takes the lock over;
#   * one Chrome session per Google account (GP_ACCOUNTS order, round and round while work is left): each object is
#     checked first in the account it is routed to and — when that account answered not-found / wrong-item and it was
#     not a push of the last recent_days at that moment (Step 9b: not backed up yet) — in every other account that has
#     a profile (found there: an account mismatch; nowhere: "in no account", Step 9b's rule in main);
#   * every object through locate_object — the fetch's own exact-item engine (Step 12), READ-ONLY: no download is ever
#     requested (no link minted, no Shift+D, no tile clicked), and nothing is written but the run's results.jsonl /
#     progress.json and the fetch journal that records each Chrome session for crash cleanup (P-12 rule 3);
#   * paced, circuit-broken, bounded by --limit / --max-minutes, resumable (results.jsonl is appended and fsynced per
#     object) and Ctrl+C safe: every Chrome it launched is closed on every way out (_close_check_session).
# Every line printed here stays clear of the P-13 web markers; photo ids are never printed (they live in results.jsonl,
# the run directory, P-15).

CHECK_UI_PACE_S = 3.0          # PLAN Step 13 pacing: at least this long between two objects that used the web pages …
CHECK_RPC_PACE_S = 0.5         # … and this long after an RPC-only one (gprpc's own ≥0.5 s between calls also holds) …
CHECK_PACE_JITTER = 0.25       # … plus up to this fraction of it at random
CHECK_ERROR_BREAKER = 5        # this many objects in a row that could not be checked stop the run (it resumes later)
CHECK_PAGE_LOAD_S = 60.0       # a check session's page-load bound (Selenium's default would wait 300 s on a stuck page)
CHECK_BATCH = gprpc.MAX_BATCH_KEYS   # objects located together: one swbisb / EWgK9e batch warms their records (13.e)
EXIT_CHECK_OK, EXIT_CHECK_USAGE, EXIT_CHECK_LOCK = 0, 1, 3
_CHECK_UNCHECKABLE = ("object-changed", "nothing-to-search", "no-profile")   # errors that say nothing about Google
_check_jitter = random.uniform  # the pacing jitter (tests make it 0)


def parse_check_args(argv):
    """`mainfetch.py check --run <run dir> [--limit N] [--max-minutes M] [--pace S]` (main.py check_photos builds this
    command line) -> {run_dir, limit, max_minutes, pace}. Prints the usage and exits 1 on a bad invocation."""
    usage = "Usage: check --run <run dir> [--limit N] [--max-minutes M] [--pace S]   (run by: main.py check_photos)"
    opts = {"run_dir": None, "limit": None, "max_minutes": None, "pace": None}
    rest, i = list(argv[2:]), 0
    try:
        if len(argv) < 2 or argv[1] != "check":
            raise ValueError("not a check command line")
        while i < len(rest):
            token = rest[i]
            if token not in ("--run", "--limit", "--max-minutes", "--pace") or i + 1 >= len(rest):
                raise ValueError(token)
            value = rest[i + 1]
            i += 2
            if token == "--run":
                opts["run_dir"] = value
            elif token == "--limit":
                opts["limit"] = int(value)
                if opts["limit"] < 1:
                    raise ValueError(value)
            else:
                number = float(value)
                if not (number == number and abs(number) != float("inf")) or number < 0 or (
                        token == "--max-minutes" and number == 0):
                    raise ValueError(value)
                opts["max_minutes" if token == "--max-minutes" else "pace"] = number
        if not opts["run_dir"]:
            raise ValueError("--run")
    except ValueError:
        print(usage)
        sys.exit(EXIT_CHECK_USAGE)
    return opts


class _SafeStream:
    """The check child's stdout / stderr: a write the parent can no longer read (it stopped waiting after three Ctrl+C,
    or died) is dropped instead of raising — whatever happens, the child must still close the Chrome it launched."""

    def __init__(self, stream):
        self._stream = stream

    def write(self, text):
        try:
            return self._stream.write(text)
        except (OSError, ValueError):
            return len(text) if isinstance(text, str) else 0

    def flush(self):
        try:
            self._stream.flush()
        except (OSError, ValueError):
            pass

    def __getattr__(self, name):
        return getattr(self._stream, name)


@contextlib.contextmanager
def _check_lock():
    """The fetch session lock for one check run, taken WITHOUT waiting (mvcommon.LockHeldError while a fetch, a crawl or
    the profile warm-up holds it) -> the crawler's progress Heartbeat for it (tools/gp_inventory: the lock's mtime
    refreshed at most every LOCK_HEARTBEAT_S while the check makes progress, so mvcommon's stale-reclaim never
    pre-empts a long sweep; LockLost once another command took the lock over)."""
    from tools import gp_inventory  # the crawler's Heartbeat — lazy: that module imports Selenium
    with fetch_session_lock(blocking=False):
        yield gp_inventory.Heartbeat(mvcommon.FETCH_SESSION_LOCK)


def _check_key(obj):
    return obj.get("manual_id"), obj.get("label"), obj.get("sha256")


def _check_recent(pushed_at, checked_at, days):
    """Step 9b (main._cp_recent's rule): True when the push ('…Z') is no older than `days` at the moment the object's
    account was checked ('…Z'); False when a time or the window is unknown (no push time: not a recent push)."""
    pushed, checked = mvcommon._parse_at(pushed_at), mvcommon._parse_at(checked_at)
    if pushed is None or checked is None or not (isinstance(days, (int, float)) and not isinstance(days, bool)):
        return False
    return pushed >= checked - timedelta(days=days)


def _check_state(lines):
    """{object key: {"routed": its newest routed line, "cross": {account: newest line}, "found": a line that pinned it
    exactly-one}} from a run's results.jsonl lines (kind "check")."""
    state = {}
    for line in lines:
        if not isinstance(line, dict) or line.get("kind") != "check":
            continue
        st = state.setdefault(_check_key(line), {"routed": None, "cross": {}, "found": None})
        if line.get("pass") == "routed":
            st["routed"] = line
        elif isinstance(line.get("account"), str):
            st["cross"][line["account"]] = line
        if line.get("found") and line.get("outcome") == "exactly-one":
            st["found"] = line
    return state


class _CheckRun:
    """One `check` invocation over one run directory: the run (run.json), what its results already hold (state), the
    bounds (--limit counts the objects checked in their routed account, --max-minutes a deadline, --pace), and why it
    stopped early (`stop`: limit · max-minutes · interrupted · signed-out · lock-lost · breaker · chrome · port · error)."""

    def __init__(self, run_dir, doc, lines, limit, max_minutes, pace, invocation):
        self.dir, self.doc = run_dir, doc
        self.results_path = os.path.join(run_dir, "results.jsonl")
        self.objects = [o for o in doc.get("objects") or [] if isinstance(o, dict)
                        and o.get("account") in mvcommon.GP_ACCOUNTS and mvcommon._is_sha256(o.get("sha256"))]
        self.approaches = doc.get("approaches") if doc.get("approaches") in ("best", "all") else "best"
        self.recent_days = doc.get("recent_days")   # Step 9b's window (main's MAP_RECENT_DAYS)
        self.state = _check_state(lines)
        self.limit, self.pace, self.invocation, self.max_minutes = limit, pace, invocation, max_minutes
        self.deadline = time.time() + max_minutes * 60 if max_minutes else None
        self.checked = self.looked = self.errors_in_row = self.sessions = 0
        self.tried = set()          # (object key, account) attempted in this invocation — never twice
        self.unavailable = {}       # account -> why it cannot be used in this invocation
        self.stop = None
        self.library = None
        self.heartbeat = None
        self.lock_lost = Exception  # tools.gp_inventory.LockLost once the lock is held
        self.tally = {}             # this session's outcome counts

    def routed_todo(self, obj):
        st = self.state.get(_check_key(obj))
        return ((_check_key(obj), obj["account"]) not in self.tried
                and (st is None or st["routed"] is None or st["routed"].get("outcome") == "error"))

    def needs_elsewhere(self, obj):
        """True when an object is to be looked for in the other accounts: it is not pinned anywhere, its own account
        answered not-found or wrong-item (its name there holds other bytes) — not ambiguous: its name IS there — and,
        Step 9b, it was not a push of the last `recent_days` (run.json: main's MAP_RECENT_DAYS) when its own account
        was checked: such a push is simply not backed up yet (P-19: the auto-confirm opens only the accounts it needs).
        Measured at the check — exactly main's verdict rule — so a run resumed days later decides as its verdict does."""
        st = self.state.get(_check_key(obj))
        if not (st is not None and st["found"] is None and st["routed"] is not None
                and st["routed"].get("outcome") in ("not-found", "wrong-item")):
            return False
        return not _check_recent(obj.get("pushed_at"), st["routed"].get("at"), self.recent_days)

    def cross_todo(self, obj, account):
        if account == obj["account"] or (_check_key(obj), account) in self.tried or not self.needs_elsewhere(obj):
            return False
        prev = self.state[_check_key(obj)]["cross"].get(account)
        return prev is None or prev.get("outcome") == "error"

    def work(self, account):
        """(objects to check in their routed account `account`, objects to look for there from other accounts)."""
        if account in self.unavailable:
            return [], []
        routed = [o for o in self.objects if o["account"] == account and self.routed_todo(o)]
        if self.limit is not None:
            routed = routed[:max(0, self.limit - self.checked)]
        return routed, [o for o in self.objects if self.cross_todo(o, account)]

    def left(self):
        """Objects this run still owes a look (ignoring this invocation's bounds): no routed line yet, or — to be looked
        for elsewhere (needs_elsewhere) — no line yet from another account that can be checked."""
        out = []
        for obj in self.objects:
            st = self.state.get(_check_key(obj))
            if st is None or st["routed"] is None:
                out.append(obj)
            elif self.needs_elsewhere(obj) and any(a != obj["account"] and a not in self.unavailable
                                                   and a not in st["cross"] for a in mvcommon.GP_ACCOUNTS):
                out.append(obj)
        return out


def _check_read_json(path):
    try:
        with open(path, encoding="utf-8") as fh:
            doc = json.load(fh)
    except (OSError, ValueError):
        return None
    return doc if isinstance(doc, dict) else None


def _check_read_results(run_dir):
    def as_dict(raw):
        if not isinstance(raw, dict):
            raise ValueError("not a result line")
        return raw
    try:
        return mvcommon._read_jsonl(os.path.join(run_dir, "results.jsonl"), as_dict, None)
    except OSError:
        return []


def _check_progress(run, path, invocations, state, reason):
    """progress.json (atomic): v, run, state (running · complete · partial), reason (why it stopped early), pid,
    updated_at, invocations [{n, started_at, ended_at, pid, limit, max_minutes, pace, state, reason, checked,
    looked}]. A write failure prints one warning; the check goes on."""
    doc = {"v": 1, "run": run.doc.get("run"), "state": state, "reason": reason, "pid": os.getpid(),
           "updated_at": _utc_now(), "invocations": invocations}
    try:
        _write_json_atomic(path, doc)
    except (OSError, TypeError, ValueError) as e:
        print(f"   > ⚠️ progress.json could not be written ({type(e).__name__}: {e}) — the results are kept.")


def _check_hinted(loc, obj, account):
    """A hinted object's locator (the `hinted` scope — the user's resolutions, PLAN Step 9 (8)): in the hint's own
    account a url hint's photo_id is tried first (S0a; the learned photo_id becomes an alternate), a datetime hint gives
    the taken second and its offset (S1). The hint is evidence to VERIFY, never proof (P-5: manual): the item must still
    hold the object's content key, or its exact uploaded name."""
    hint = obj.get("hint")
    if not isinstance(hint, dict) or hint.get("account") != account:
        return loc
    loc = dict(loc)
    if hint.get("kind") == "url" and isinstance(hint.get("photo_id"), str):
        older = [p for p in [loc.get("photo_id")] + list(loc.get("alternates") or []) if p and p != hint["photo_id"]]
        loc.update(photo_id=hint["photo_id"], alternates=older, approach_best="photo_id")
    elif hint.get("kind") == "datetime" and mvcommon._is_int(hint.get("taken_ms")):
        loc.update(taken_ms=hint["taken_ms"], approach_best="taken_second")
        if mvcommon._is_int(hint.get("gmt_offset_min")):
            loc["tz_offset_ms"] = hint["gmt_offset_min"] * 60_000
    return loc


def _check_locators(run, ctx, objs):
    """{object key: (queue item, locator, learned record)} for `objs`, built exactly as the fetch builds them —
    build_fetch_queue against a folder that never exists, so every object is listed (cmd_locate's rule), an extra
    through build_extras_entries — in the session's account (ctx.account: its own learned record, which is also handed
    back for the RPC acceleration — the locator's shape is the fetch's, unchanged). An object the library no longer holds
    (re-split, re-mastered since the run began) is absent."""
    import tempfile
    import uuid
    nowhere = os.path.join(tempfile.gettempdir(), f"mediavault-check-{uuid.uuid4().hex}")  # never created
    wanted, out = {_check_key(o) for o in objs}, {}
    owners = dict.fromkeys((o["manual_id"], o["kind"] in ("extra_whole", "extra_chunk")) for o in objs)
    for manual_id, extra in owners:
        entry = run.library.get(manual_id) if isinstance(run.library, dict) else None
        if not isinstance(entry, dict) or entry.get("type") == "multi_ep_alias":
            continue
        entries = [(ex, None) for ex in build_extras_entries(entry, manual_id)] if extra else [(entry, manual_id)]
        for source, entry_id in entries:
            if source.get("type") == "season_map":
                continue  # a season holds no file of its own (its extras come through build_extras_entries)
            blocks = {sha: obj["block"] for sha, obj in _entry_objects(source, entry_id or manual_id).items()}
            for item in build_fetch_queue(source, nowhere, ctx, entry_id=entry_id):
                loc = item.get("locator") or {}
                key = (loc.get("manual_id"), loc.get("label"), item.get("hash"))
                if key in wanted:
                    out.setdefault(key, (item, loc, mvcommon.account_record(blocks.get(item.get("hash")), ctx.account)))
    return out


def _check_line(run, obj, loc, account, pass_name, res, error=None):
    """One results.jsonl line (kind "check") — the object, the account it was checked in (pass routed | cross) and
    locate_object's verdict: outcome (exactly-one · ambiguous · wrong-item · not-found · error), n, found, the pinning
    stage (stage, approach, via, proof, name_normalized, rule — newest-set: several same-name uploads), its item
    (photo_id, record, alternates), every stage's verdict (per_approach exactly {outcome, n} + per_approach_via /
    per_approach_pick), candidates, stale photo_ids, s4_skip, the object's content_key flag, date_utc and
    expected_taken_local (the failed list's), hint, cost. Never a URL (P-20). None values are left out."""
    line = {"v": 1, "kind": "check", "at": _utc_now(), "inv": run.invocation, "pass": pass_name, "account": account,
            "manual_id": obj["manual_id"], "label": obj["label"], "sha256": obj["sha256"]}
    if res is None:
        line.update(outcome="error", n=0, found=False, error=error or "error")
        return line
    per = res["per_approach"]
    found = bool(res["found"])
    if found:
        outcome, n = res["outcome"], res["n"]
    elif not per or all(v["outcome"] == "error" for v in per.values()):
        outcome, n = "error", 0
        line["error"] = "nothing-to-search" if not per else "stages-failed"
    else:
        outcomes = [v["outcome"] for v in per.values()]
        outcome, n = next((o for o in ("ambiguous", "wrong-item") if o in outcomes), "not-found"), res["candidates_seen"]
    line.update(outcome=outcome, n=n, found=found, stage=res["stage"], approach=res["approach"], via=res["via"],
                proof=res["proof"], name_normalized=res["name_normalized"] or None, rule=res["rule"],
                photo_id=res["photo_id"], record=res["record"], alternates=res["alternates"] or None,
                per_approach=per, per_approach_via=res["per_approach_via"],
                per_approach_pick=res.get("per_approach_pick") or {},
                candidates=res["candidates"][:LOCATE_MAX_CANDIDATES] or None, stale=res["stale"] or None,
                s4_skip=res["s4_skip"] or None, content_key=_content_key(loc) is not None,
                date_utc=loc.get("date_utc"), expected_taken_local=_expected_local(loc),
                hint=True if obj.get("hint") else None,
                cost={"elapsed": res["elapsed"], "pages": res["pages_opened"], "searches": res["searches"],
                      "rpc": res["rpc_calls"]})
    return {k: v for k, v in line.items() if v is not None}


def _check_text(line):
    """One printed result: the verdict, the pinning stage and proof, or what was tried — never a photo id (P-15)."""
    if line["outcome"] == "error":
        return f"not checked ({line.get('error', 'error')})"
    tried = " · ".join(f"{a} {v['outcome']}({v['n']})" for a, v in (line.get("per_approach") or {}).items())
    if not line.get("found"):
        return f"{line['outcome']} — tried: {tried or 'nothing to search by'}"
    how = " (normalized name)" if line.get("name_normalized") else ""
    more = f" · the newest of {len(line.get('alternates') or []) + 1} same-name uploads" \
        if line.get("rule") == "newest-set" else ""
    cost = line.get("cost") or {}
    return (f"{line['outcome']} · {_STAGE_LABEL.get(line.get('stage'), '?')} {line.get('approach')} · proof "
            f"{line.get('proof')}{how} · via {line.get('via')}{more} · {cost.get('elapsed', 0):.1f} s")


def _check_beat(run):
    """The lock heartbeat (at most every LOCK_HEARTBEAT_S): when another command took the lock over, the check yields."""
    try:
        run.heartbeat.beat()
    except run.lock_lost as e:
        print(f"   ❌ {e} — this check yields; re-run it when that command has finished (it continues where it "
              f"stopped).")
        run.stop = "lock-lost"


def _check_pace(run, res, started):
    """PLAN Step 13 pacing between two objects: --pace S, else CHECK_UI_PACE_S after an object whose check used the web
    pages and CHECK_RPC_PACE_S after an RPC-only one — counted from the object's start, plus a random jitter."""
    used_ui = res is not None and (res["pages_opened"] > 0 or "ui" in res["per_approach_via"].values())
    pace = run.pace if run.pace is not None else (CHECK_UI_PACE_S if used_ui else CHECK_RPC_PACE_S)
    if pace <= 0:
        return
    wait = pace + _check_jitter(0, CHECK_PACE_JITTER * pace) - (time.time() - started)
    if wait > 0:
        time.sleep(wait)


def _check_should_stop(run):
    """True when the invocation must end before the next object: a stop already decided, or --max-minutes reached."""
    if run.stop:
        return True
    if run.deadline is not None and time.time() >= run.deadline:
        print("   ⏸️ --max-minutes reached — the check stops here; re-run the same command to continue.")
        run.stop = "max-minutes"
        return True
    return False


def _check_record(run, obj, line):
    """Append one result line (fsynced: a crash keeps it), fold it into the run's state, and count it."""
    mvcommon._append_jsonl(run.results_path, line)
    st = run.state.setdefault(_check_key(obj), {"routed": None, "cross": {}, "found": None})
    if line["pass"] == "routed":
        st["routed"] = line
        run.checked += 1
    else:
        st["cross"][line["account"]] = line
    if line.get("found") and line["outcome"] == "exactly-one":
        st["found"] = line
    run.looked += 1
    run.tally[line["outcome"]] = run.tally.get(line["outcome"], 0) + 1


def _check_one(run, driver, ctx, obj, located, pass_name):
    """Check ONE object in the session's account (pass routed | cross): locate_object over its locator, the result
    appended to results.jsonl and printed, the heartbeat, the pacing and the error breaker. Raises
    SessionExpiredError (signed out), KeyboardInterrupt and gprpc.RpcRefused (a bug — never swallowed); any other
    trouble is this object's `error` line."""
    account = ctx.account
    run.tried.add((_check_key(obj), account))
    started, res = time.time(), None
    if located is None:
        line = _check_line(run, obj, {}, account, pass_name, None, "object-changed")
    else:
        loc = _check_hinted(located[1], obj, account)
        try:
            res = locate_object(driver, loc, ctx, approaches=run.approaches)
            error = None
        except (SessionExpiredError, KeyboardInterrupt, gprpc.RpcRefused):
            raise
        except Exception as e:  # one object's trouble never stops the run
            error = type(e).__name__
        line = _check_line(run, obj, loc, account, pass_name, res, error)
    _check_record(run, obj, line)
    where = f"↪ in '{account}': " if pass_name == "cross" else ""
    print(f"   🔎 {where}{obj['manual_id']} · {obj['label']} — {_check_text(line)}")
    counts = line["outcome"] == "error" and line.get("error") not in _CHECK_UNCHECKABLE
    run.errors_in_row = run.errors_in_row + 1 if counts else 0
    if run.errors_in_row >= CHECK_ERROR_BREAKER:
        print(f"   ❌ {CHECK_ERROR_BREAKER} objects in a row could not be checked — the check stops here (re-run to "
              f"continue; if it happens again, see whether Google Photos opens in the profile's Chrome).")
        run.stop = "breaker"
    _check_beat(run)
    if not run.stop:
        _check_pace(run, res, started)
    return line


_QUALITY_KEYS = ("original_quality", "partial_upload", "archived")


def _check_note_rpc(ctx, error):
    """One line per run when a batched call of the check failed (its objects are then asked one by one, or on the web
    pages — locate_object's own fallback)."""
    run = _locator_run(ctx)
    if "check-batch" not in run.told:
        run.told.add("check-batch")
        print(f"   > ⚠️ A batched Google Photos call failed ({type(error).__name__}) — the objects are looked up one by "
              f"one instead.")


def _check_remember_quality(known, rec, *keys):
    """Keep the quality facts (_QUALITY_KEYS) an answer gave about its item."""
    facts = known.setdefault(rec["photo_id"], {})
    for k in keys:
        if isinstance(rec.get(k), bool):
            facts[k] = rec[k]


def _check_prewarm(driver, ctx, account, entries):
    """[IMP-C25 Step 13 — RPC acceleration, OD-9 ruling] Before a batch of ≤ CHECK_BATCH objects is located (`entries`
    = [(locator, learned record of this account or None)]): warm the locator's OWN per-run record cache (the
    _LocatorRun that locate_object reads) with batched read-only calls, so S0a / S0b answer from that cache instead of
    one item-info call per object — "confirmed in hash_lookup batches of 50" (PLAN Step 13):
      * swbisb (gprpc.hash_lookup, ≤50 hashes per request) for every content key (capture / proven SHA-1) and every
        MAPPED cloud dedupKey — a learned record's dedup_key, what its item held when it was mapped: the answer tells
        whether that item still holds those bytes. Each answer is kept exactly as S0b keeps its own (_hash_ids);
      * EWgK9e (gprpc.batch_info) for the names of the items an object without a content key is verified by (its
        stored photo_id and alternates) and of every item a hash lookup returned — their quality on the way.
    The verdicts stay locate_object's (_verdict / _judge): a mapped cloud key is NEVER the object's content key (P-5:
    a legacy object's proof stays `filename` — IMP-E5 relies on dedupkey meaning OUR bytes). -> {photo_id: quality
    facts the answers carried}. A failing batch leaves the cache cold (locate_object then asks per object, falling back
    to the web pages itself); RPC off -> nothing is asked. Raises only SessionExpiredError and gprpc.RpcRefused."""
    known = {}
    if not entries or not _rpc_ok(driver, ctx):
        return known
    run = _locator_run(ctx)
    digests, named = [], []
    for loc, record in entries:
        key = _content_key(loc)
        sha1 = _sha1_of(loc, key)
        if not sha1:
            cloud = gpweb.normalize_dedup_key((record or {}).get("dedup_key")) if (record or {}).get("photo_id") else None
            sha1 = gpweb.dedup_key_to_sha1_hex(cloud) if cloud else None
            named += [p for p in [loc.get("photo_id")] + list(loc.get("alternates") or [])
                      if isinstance(p, str) and gpweb.MEDIA_KEY_RE.match(p)]
        if sha1 and sha1 not in digests and (account, "hash", sha1) not in run.listings:
            digests.append(sha1)
    try:
        if digests:
            found = gprpc.hash_lookup(driver, digests)
            for digest in digests:
                held = []
                for rec in found.get(digest, []):
                    cached = _remember(ctx, account, rec, "rpc")
                    if cached is not None:
                        held.append(cached["photo_id"])
                        _check_remember_quality(known, rec, "partial_upload", "archived")
                run.listings[(account, "hash", digest)] = {"ids": held, "complete": True}
                named += held
        named = [p for p in dict.fromkeys(named) if not (run.records.get((account, p)) or {}).get("filename")]
        if named:
            for pid, row in gprpc.batch_info(driver, named).items():
                if row and row.get("filename"):
                    _remember(ctx, account, {"photo_id": pid, "filename": row["filename"],
                                             "size_bytes": row.get("size_bytes")}, "rpc")
                    _check_remember_quality(known, row, "original_quality")
    except gprpc.RpcUnavailable as e:
        _check_note_rpc(ctx, e)
    return known


def _check_quality(run, driver, ctx, account, lines, known):
    """[IMP-C25 Step 13] The quality of the items this batch pinned (PLAN Step 13 outliers: "partial or
    non-original-quality uploads"; IMP-C27 (e): such a copy would fail a restore's SHA-256 check): original_quality
    from EWgK9e, partial_upload / archived from the item record (swbisb), each asked once per batch for the items the
    pre-warm did not already describe. One results.jsonl line (kind "quality") per pinned item: {account, photo_id,
    manual_id, label, sha256, original_quality, partial_upload, archived — the ones known}. RPC off or a failing call:
    the facts stay unknown (never a reason to doubt an item). Raises only SessionExpiredError and gprpc.RpcRefused."""
    pinned = [line for line in lines if line.get("found") and line.get("outcome") == "exactly-one"
              and isinstance(line.get("photo_id"), str)]
    if not pinned:
        return
    if _rpc_ok(driver, ctx):
        try:
            wants = [l["photo_id"] for l in pinned if "original_quality" not in known.get(l["photo_id"], {})]
            if wants:
                for pid, row in gprpc.batch_info(driver, list(dict.fromkeys(wants))).items():
                    if row:
                        _check_remember_quality(known, dict(row, photo_id=pid), "original_quality")
            wants = {}
            for l in pinned:
                key = gpweb.normalize_dedup_key((l.get("record") or {}).get("dedup_key"))
                if key and "partial_upload" not in known.get(l["photo_id"], {}):
                    wants[gpweb.dedup_key_to_sha1_hex(key)] = l["photo_id"]
            if wants:
                for digest, recs in gprpc.hash_lookup(driver, list(wants)).items():
                    for rec in recs:
                        if rec["photo_id"] == wants[digest]:
                            _check_remember_quality(known, rec, "partial_upload", "archived")
        except gprpc.RpcUnavailable as e:
            _check_note_rpc(ctx, e)
    for l in pinned:
        facts = {k: v for k, v in (known.get(l["photo_id"]) or {}).items() if k in _QUALITY_KEYS}
        if facts:
            mvcommon._append_jsonl(run.results_path, dict(
                {"v": 1, "kind": "quality", "at": _utc_now(), "account": account, "photo_id": l["photo_id"],
                 "manual_id": l["manual_id"], "label": l["label"], "sha256": l["sha256"]}, **facts))


def _check_bound_page_loads(driver):
    """A check session's page loads are bounded (CHECK_PAGE_LOAD_S): a stuck page fails that object's stage instead of
    holding a long sweep for Selenium's 300 s default. A driver without the setter (a stand-in) is left as it is."""
    setter = getattr(driver, "set_page_load_timeout", None)
    if callable(setter):
        try:
            setter(CHECK_PAGE_LOAD_S)
        except Exception:
            pass


def _close_check_session(driver, ctx, last):
    """[IMP-C25 Step 13] The end of one check session — on every way out (finished, stopped, an error, Ctrl+C): first
    the shared close of Step 11 i (_close_session: CDP Browser.close; the launched Chrome terminated after
    _CLOSE_WAIT_S when its command line names the profile; the profile marked cleanly exited), then the guarantee the
    shared close cannot give under heavy load — the orchestrator saw it leave the Chrome running twice (the process query
    timed out, so the pid could not be PROVEN ours): a Chrome this run LAUNCHED that still runs — its Popen handle is
    open and poll() says it runs, so the pid cannot have been reused — is killed through that handle, waited for, and
    its profile marked cleanly exited. A Chrome the check only attached to (launched False / None) is never killed.
    Never raises; a Ctrl+C during the shared close goes straight to the guarantee."""
    try:
        _close_session(driver, ctx, last)
    except KeyboardInterrupt:
        print("\n   🛑 Stopped while closing Chrome — ending the Chrome this check started.")
    except Exception as e:
        print(f"   > ⚠️ Closing Chrome failed ({type(e).__name__}: {e}).")
    chrome = ctx.chrome or {}
    proc = chrome.get("proc")
    poll = getattr(proc, "poll", None)
    if chrome.get("launched") is not True or not callable(poll):
        return
    try:
        if poll() is not None:
            return
        proc.kill()
        proc.wait(timeout=CHROME_GONE_WAIT_S)
    except Exception as e:
        print(f"   > ⚠️ The Chrome this check started ('{chrome.get('account')}', pid {chrome.get('pid')}) could not be "
              f"ended ({type(e).__name__}) — close it before the next fetch.")
        if ctx.journal is not None:
            ctx.journal.end_session("open")
        return
    print(f"   > 🧹 Chrome ('{chrome.get('account')}') ended (pid {chrome.get('pid')}) — the usual close left it running.")
    mark_profile_clean_exit(chrome.get("user_data_dir"), wait=CHROME_GONE_WAIT_S)
    if ctx.journal is not None:
        ctx.journal.end_session("terminated")


def _check_has_work(run, account):
    routed, cross = run.work(account)
    return bool(routed or cross)


@contextlib.contextmanager
def _ctrl_c_held():
    """[IMP-C25 Step 13] Ctrl+C held back while a check session launches its Chrome (init_driver — shared, unchanged):
    the new Chrome's Popen handle exists only inside that call, so an interrupt there would leave a Chrome running that
    nothing records or closes. -> a function telling whether Ctrl+C was pressed meanwhile; the session then stops right
    after the launch, through its own close (the Chrome it launched is closed or ended). Only the main thread may set a
    signal handler: elsewhere nothing is held."""
    import signal
    import threading
    if threading.current_thread() is not threading.main_thread():
        yield lambda: False
        return
    pressed = []
    previous = signal.signal(signal.SIGINT, lambda signum, frame: pressed.append(signum))
    try:
        yield lambda: bool(pressed)
    finally:
        signal.signal(signal.SIGINT, previous)


def _check_session(run, ctx, account):
    """One account's Chrome session: the objects routed to it (bounded by --limit), then the objects looked for there
    from other accounts; their locators built and their records warmed in CHECK_BATCH batches (13.e). The pre-flight,
    the port guard and the sign-out handling are cmd_fetch_route's; the session's Chrome is closed on every way out —
    a Ctrl+C during its launch included (_ctrl_c_held)."""
    routed, cross = run.work(account)
    problem = profile_preflight(account)
    if problem:
        print(f"\n   ❌ {problem[:1].upper()}{problem[1:]}")
        run.unavailable[account] = "no-profile"
        for obj in routed:
            run.tried.add((_check_key(obj), account))
            _check_record(run, obj, _check_line(run, obj, {}, account, "routed", None, "no-profile"))
        return
    if run.sessions and not _debug_port_free():
        print(f"❌ Cannot switch to profile '{account}': Chrome's debug port 9222 is still in use (the previous "
              f"account's Chrome did not close). Close that Chrome window, then re-run — the check continues where it "
              f"stopped.")
        run.stop = "port"
        return
    print(f"\n   === CHECK '{account}' — {len(routed)} object(s) routed here · {len(cross)} looked for here from "
          f"other accounts ===")
    run.sessions += 1
    ctx.chrome = None                     # never the previous session's Chrome
    with _ctrl_c_held() as pressed:       # the launch and its record (ctx.chrome, the journal) are never torn apart
        driver = init_driver(account)
        if driver:
            ctx.start_session(account)
            _note_session(ctx, driver, account)
    if not driver:
        run.unavailable[account] = "chrome"
        run.stop = "interrupted" if pressed() else "chrome"
        return
    run.tally = {}
    try:
        _check_bound_page_loads(driver)
        if pressed():
            raise KeyboardInterrupt       # pressed during the launch: stop now, through this session's close
        for pass_name, objs in (("routed", routed), ("cross", cross)):
            for start in range(0, len(objs), CHECK_BATCH):
                if _check_should_stop(run):
                    break
                chunk = objs[start:start + CHECK_BATCH]
                located = _check_locators(run, ctx, chunk)
                entries = []
                for obj in chunk:
                    hit = located.get(_check_key(obj))
                    if hit is not None:
                        entries.append((_check_hinted(hit[1], obj, account), hit[2]))
                known = _check_prewarm(driver, ctx, account, entries)
                lines = []
                for obj in chunk:
                    if _check_should_stop(run):
                        break
                    lines.append(_check_one(run, driver, ctx, obj, located.get(_check_key(obj)), pass_name))
                _check_quality(run, driver, ctx, account, lines, known)
    except SessionExpiredError:
        print(f"❌ Profile '{account}' is logged out. Open Chrome with --user-data-dir={CHROME_PROFILES[account]}, sign "
              f"in to photos.google.com, then re-run — the check continues where it stopped.")
        run.stop, run.unavailable[account] = "signed-out", "signed-out"
    except KeyboardInterrupt:
        print("\n🛑 Stopped by user — closing Chrome; re-run the same command to continue.")
        run.stop = "interrupted"
    except Exception as e:  # a bug or a browser fault: the run stops (resumable), Chrome is still closed
        print(f"❌ The check stopped: {type(e).__name__}: {e}")
        run.stop = "error"
    finally:
        counts = " · ".join(f"{o} {n}" for o, n in sorted(run.tally.items()))
        print(f"   > 🔎 Check ('{account}'): {sum(run.tally.values())} object(s){' — ' + counts if counts else ''} · "
              f"{gprpc.summary_line(driver)}")
        last = run.stop is not None or not any(_check_has_work(run, a) for a in mvcommon.GP_ACCOUNTS if a != account)
        _close_check_session(driver, ctx, last)
        try:
            driver.quit()
        except Exception:
            pass


def _check_sessions(run, ctx):
    """The account sessions, in GP_ACCOUNTS order. First every account whose objects await their check in it (their
    routed account) — round from the last session; each of those sessions also looks for what earlier ones did not pin.
    Then the remaining look-elsewhere work, from the first account again. Every session either checks something or
    makes its account unavailable, so the sessions end — each account at most twice."""
    order = list(mvcommon.GP_ACCOUNTS)
    last = -1
    while run.stop is None:
        routed = [a for a in order if run.work(a)[0]]
        if routed:
            account = next(a for a in order[last + 1:] + order[:last + 1] if a in routed)
        else:
            account = next((a for a in order if _check_has_work(run, a)), None)
            if account is None:
                return
        last = order.index(account)
        _check_session(run, ctx, account)


def cmd_check(run_dir, limit=None, max_minutes=None, pace=None):
    """[IMP-C25 Step 13] `python mainfetch.py check --run <run dir> [--limit N] [--max-minutes M] [--pace S]` — the
    browser half of `main.py check_photos` (see the section comment above). Reads run.json (main wrote it), continues
    from results.jsonl, appends one line per object look and rewrites progress.json; never writes the library, a learned
    sidecar, the failed list or anything else (main applies a run).
    -> exit code: EXIT_CHECK_OK the invocation ended (complete, or stopped early — progress.json says why) ·
    EXIT_CHECK_USAGE the run directory holds no readable run.json · EXIT_CHECK_LOCK the fetch session lock is held
    (nothing ran)."""
    doc = _check_read_json(os.path.join(run_dir, "run.json"))
    if doc is None or not isinstance(doc.get("objects"), list):
        print(f"❌ check: {run_dir} holds no readable run.json.")
        return EXIT_CHECK_USAGE
    progress_path = os.path.join(run_dir, "progress.json")
    invocations = list((_check_read_json(progress_path) or {}).get("invocations") or [])
    run = _CheckRun(run_dir, doc, _check_read_results(run_dir), limit, max_minutes, pace, len(invocations) + 1)
    try:
        with _check_lock() as heartbeat:
            from tools import gp_inventory  # LockLost (the Heartbeat's)
            run.heartbeat, run.lock_lost = heartbeat, gp_inventory.LockLost
            return _check_run(run, progress_path, invocations)
    except mvcommon.LockHeldError:
        print("❌ A fetch, an inventory crawl or the profile warm-up holds the fetch session lock — nothing was checked; "
              "re-run when it has finished.")
        return EXIT_CHECK_LOCK


def _check_run(run, progress_path, invocations):
    """cmd_check under the lock: the invocation's record, the start-up cleanup of an interrupted fetch (P-12 rule 3)
    and this run's fetch journal (each Chrome session recorded for crash cleanup), the sessions, then the end: the
    run's state (complete · partial with its reason) in progress.json and one closing line."""
    inv = {"n": run.invocation, "started_at": _utc_now(), "pid": os.getpid(), "limit": run.limit,
           "max_minutes": run.max_minutes, "pace": run.pace}
    invocations.append(inv)
    _check_progress(run, progress_path, invocations, "running", None)
    before = sum(1 for o in run.objects if not run.routed_todo(o))
    print(f"--- CHECK: {run.doc.get('run')} — {len(run.objects)} object(s) · approaches {run.approaches} · "
          f"download-free ---" + (f"\n   > {before} object(s) checked already — continuing." if before else ""))
    ctx = FetchContext(run_id=f"check-{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}-{os.getpid()}")
    try:
        _startup_cleanup(ctx)
        ctx.journal = FetchJournal(ctx.run_id)
        run.library = load_library()
        _check_sessions(run, ctx)
    except KeyboardInterrupt:  # between two sessions (a session handles its own)
        print("\n🛑 Stopped by user — re-run the same command to continue.")
        run.stop = "interrupted"
    finally:
        _end_run(ctx, run.stop == "interrupted")
        left = run.left()
        if run.stop:
            state, reason = "partial", run.stop
        elif left:
            state, reason = "partial", "limit" if run.limit is not None and run.checked >= run.limit else "incomplete"
        else:
            state, reason = "complete", None
        inv.update(ended_at=_utc_now(), state=state, reason=reason, checked=run.checked, looked=run.looked)
        _check_progress(run, progress_path, invocations, state, reason)
        verdict = "complete" if state == "complete" else f"stopped ({reason}) — {len(left)} object(s) left"
        print(f"--- CHECK: {run.looked} look(s) at {run.checked} object(s) in this invocation — {verdict} ---")
    return EXIT_CHECK_OK


def cli(argv):
    """`python mainfetch.py fetch <id> [episodes <range>] [tempdir <path>]
    [--fetchExtras] [--manual]` — parse argv, then run the fetch.
    [IMP-C25 Step 12] `python mainfetch.py locate <id> …` runs the read-only
    cmd_locate instead (parse_locate_args); every fetch argv is handled as before.
    [IMP-C25 Step 13] `python mainfetch.py check --run <dir> …` runs cmd_check (parse_check_args) and exits with its
    code; its output can never stop it from closing its Chrome (_SafeStream)."""
    if len(argv) > 1 and argv[1] == "check":
        options = parse_check_args(argv)
        sys.stdout, sys.stderr = _SafeStream(sys.stdout), _SafeStream(sys.stderr)
        sys.exit(cmd_check(**options))
    if len(argv) > 1 and argv[1] == "locate":
        cmd_locate(**parse_locate_args(argv))
        return
    mid, epr, fetch_extras, temp_dir = parse_fetch_args(argv)
    options = parse_fetch_options(argv)
    cmd_fetch_route(mid, epr, fetch_extras, temp_dir=temp_dir, manual=options["manual"])


if __name__ == "__main__":
    # [IMP-C25 Step 13] Run as a script, this module is `__main__`. gprpc imports `mainfetch` lazily (its signed-out
    # check) and so does tools/gp_inventory: without this line each would load a SECOND copy of this file, and a
    # SessionExpiredError raised by that copy (the RPC path's sign-out detection) is a class this script never
    # catches — a signed-out profile then surfaced as an unexpected error instead of the remediation line.
    sys.modules.setdefault("mainfetch", sys.modules[__name__])
    cli(sys.argv)