import os
import sys
import subprocess
import shutil
import time
import re
import socket
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
        # [IMP-C29] Downloads must ride TCP. Google serves them over HTTP/3 (QUIC)
        # when Chrome allows it, and a QUIC download fails for good after a stall
        # of about 8 s ("Failed - Network error"; a Photos download cannot resume).
        # Over TCP it survived a 60 s stall and ran 2-3x faster (measured live).
        "--disable-quic",
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
    locator). Step 12 locates each object and requests its download; an object it
    settles is marked 'done', one it may not hand to the legacy attempts (OD-5)
    'failed'. Every item still 'pending' afterwards gets today's attempts
    unchanged. Until Step 12 lands: nothing."""
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
    nothing new — then IMP-C29's note names the requested files that never came.

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
                              wrong item (another one, or one already fetched)"""
    print("   > Watching Downloads (Infinite wait if active)...")
    start_time = time.time()
    base_timeout = 300  # 5 mins initial timeout

    processed_files = set()  # this round's files ([IMP-B10] their hashes outlive it in ctx.hashed)
    saw_download = False  # [IMP-C29] was a .crdownload ever seen during this wait?
    unmatched = 0  # [IMP-C34] arrivals of this round that matched no pending item

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

        # Check Completion ([IMP-C25] nothing pending: an item a stage gave up on is 'failed')
        if not any(i["status"] == "pending" for i in queue):
            return {"timed_out": False, "saw_download": saw_download, "unmatched": unmatched}

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

            fhash, sha1, cached = _hash_download(f, fpath, ctx)
            processed_files.add(fpath)
            arrived = _arrived_this_session(f, fpath, ctx)

            # Match
            matched = next((i for i in queue if i["hash"] == fhash and i["status"] == "pending"), None)
            if matched:
                if cached:
                    print(f"\n   > 🔎 Checking: {f}")
                dest = os.path.join(matched["dest"], matched["filename"])
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
                # Duplicate check
                if any(i["hash"] == fhash for i in queue):
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
            if lost:
                print(_lost_download_note(lost, saw_download))
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


def _record_lost_twice(item, ctx):
    """[IMP-C34] One failed-list row (P-18, reason download-lost) for an object whose
    download was lost twice: run, account, manual_id, label, uploaded_name and the
    sha256 pin. True when written. Needs the session account and the owning id — a
    stand-alone call has neither and writes nothing; a failed write prints one
    warning line."""
    loc = item.get("locator") or {}
    if ctx.account not in mvcommon.GP_ACCOUNTS or not loc.get("manual_id"):
        return False
    sha256 = item.get("hash") if mvcommon._is_sha256(item.get("hash")) else None
    try:
        mvcommon.append_failed({"run": ctx.run_id, "account": ctx.account, "manual_id": loc["manual_id"],
                                "label": loc.get("label"), "sha256": sha256,
                                "uploaded_name": loc.get("uploaded_name"), "reason": "download-lost"})
    except (OSError, ValueError) as e:
        print(f"     ⚠️ Not recorded in the failed list ({type(e).__name__}: {e}).")
        return False
    return True


def _note_losses(queue, rnd, ctx):
    """[IMP-C34] After a harvest round (`rnd`, harvest_downloads' result): one LOSS
    for every pending object whose download was requested in that round and never
    came — counted only when the round ended on the timeout with nothing
    downloading and nothing that arrived went unmatched. A file that arrived and
    matched no pending object means a request fetched a WRONG item, which no
    re-request fixes (and which request it was cannot be told), so such a round
    counts no loss. A second loss ends the object for this run: status 'failed',
    one line, one failed-list row."""
    if not rnd.get("timed_out") or rnd.get("unmatched"):
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


def _lost_downloads_line(ctx):
    """[IMP-C34] The run's one closing line about lost downloads, or None when
    nothing was requested again and nothing was lost twice."""
    s = ctx.stats
    if not (s["retried"] or s["lost_twice"]):
        return None
    return (f"   > 🔁 Lost downloads: {s['retried']} requested once more ({s['recovered']} arrived); "
            f"{s['lost_twice']} lost twice — see the failed list.")


def _fetch_items(driver, queue, ctx):
    """Fetch the pending items of one entry in the running session: the exact-item
    stages, today's two legacy attempts, then IMP-C34's one retry."""
    # [IMP-C25] What is in Downloads before this session requests anything: no file
    # that was already there may teach the learned sidecar about this account.
    if ctx.downloads is None:
        ctx.downloads = _downloads_snapshot()

    # [IMP-C25 P-14] The exact-item stages first (a no-op until Step 12); today's two
    # attempts below run unchanged for whatever is still pending.
    if EXACT_LOCATOR_ENABLED:
        _run_locator_phase(driver, queue, ctx)

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

        # Harvest
        _note_losses(queue, harvest_downloads(queue, ctx), ctx)

    # [IMP-C34] A download lost and not requested since gets its one retry.
    _retry_lost_downloads(queue, ctx)


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
        _fetch_items(driver, mine, ctx)
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
    an extra's objects are owned by its TITLE id (None when not given)."""
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
    with fetch_session_lock(blocking=True):
        for n, (profile, group_targets, group_extras) in enumerate(groups):
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
                # Pass the selected profile
                driver = init_driver(profile)
                if not driver: return
                ctx.start_session(profile)

                for entry, entry_id in group_targets:
                    fetch_single_entry(driver, entry, temp_dir=temp_dir, entry_id=entry_id, ctx=ctx)

                # [IMP-D19 Step 5] Extras fetch through the SAME proven mechanism:
                # each synthetic extra entry stages into its own
                # <title folder_path>/<group_rel>/restore/ folder.
                if group_extras:
                    print(f"\n=== 📎 FETCHING {len(group_extras)} EXTRA(S) for {manual_id} ===")
                for ex in group_extras:
                    fetch_single_entry(driver, ex, ctx=ctx)

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

    lost_line = _lost_downloads_line(ctx)  # [IMP-C34] only when a download was lost
    if lost_line:
        print(f"\n{lost_line}")
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