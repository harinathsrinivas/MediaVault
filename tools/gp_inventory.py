"""Google Photos inventory crawler (IMP-C25 research tool) — READ-ONLY against Google Photos.

Walks one Google account's whole Photos library and records, for every item, what the web UI
exposes: the exact uploaded filename (info panel), the displayed date/time/timezone, and the
item page's embedded record — taken time (ms), dedupKey (= urlsafe-base64 SHA-1 of the uploaded
bytes), timezone offset (ms), upload time (ms), width x height. MediaVault later maps library
nodes to these items by exact uploaded filename (docs/feature-fetch-datetime/RESEARCH.md).

It NEVER downloads, edits, deletes, shares or favourites anything, and never touches the
MediaVault libraries. Output is append-only JSONL written one line per item with fsync, so a
crash, Ctrl+C, reboot or Claude session limit loses nothing: re-running resumes where it stopped.

Usage (run from the repo root):
  python tools/gp_inventory.py tv --attach 9222        # attach to an already-open Chrome
  python tools/gp_inventory.py movies --port 9223      # launch that profile on its own port
  python tools/gp_inventory.py anime --port 9224
  python tools/gp_inventory.py tv --attach 9222 --phase details --max 3   # quick smoke
Options: --out DIR (default D:\\MediaVault_gp_inventory) · --phase enumerate|details|all

Files per account in --out: <account>.tiles.jsonl (phase 1: every item id + tile label),
<account>.items.jsonl (phase 2: one record per item), <account>.log.
Do not run a MediaVault fetch on the same account while this runs (same Chrome profile).
"""
import argparse
import json
import os
import random
import re
import subprocess
import sys
import time
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from mainfetch import CHROME_PROFILES, PHOTOS_URL  # noqa: E402  (single source of truth)

from selenium import webdriver  # noqa: E402
from selenium.webdriver.chrome.options import Options  # noqa: E402
from selenium.webdriver.common.by import By  # noqa: E402

CHROME_EXE = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
DEFAULT_OUT = r"D:\MediaVault_gp_inventory"
ACCOUNTS = {"movies": "movies", "tv": "tv", "series": "tv", "anime": "anime", "others": "others"}

TILES_JS = """
const out=[];
document.querySelectorAll('a[href*="photo/"]').forEach(a=>{ const r=a.getBoundingClientRect();
  if(a.checkVisibility() && r.width>40) out.push({href:a.getAttribute('href'), label:a.getAttribute('aria-label')}); });
return out;"""
SCROLL_JS = """
const els=[...document.querySelectorAll('*')].filter(e=>{const s=getComputedStyle(e);
  return (s.overflowY=='auto'||s.overflowY=='scroll')&&e.scrollHeight>e.clientHeight+50&&e.checkVisibility()});
els.sort((a,b)=>b.scrollHeight-a.scrollHeight);
const el=els[0]; if(!el){window.scrollBy(0,900);return [window.scrollY, document.body.scrollHeight];}
el.scrollTop+=Math.max(400, Math.floor(el.clientHeight*0.8)); return [el.scrollTop, el.scrollHeight];"""
PID_RE = re.compile(r"/photo/([^/?#]+)")
EXT_LINE_RE = re.compile(r"^[^\n/\\]{1,300}\.[A-Za-z0-9]{2,5}$")
TZ_RE = re.compile(r"^GMT(?:[+\-]\d{1,2}:\d{2})?$")
MP_RE = re.compile(r"^\d+(?:\.\d+)?\s?MP$")  # "2.1MP" resolution line, not a filename


class LoggedOut(Exception):
    pass


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def append_jsonl(path, obj):
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(obj, ensure_ascii=False) + "\n")
        fh.flush()
        os.fsync(fh.fileno())


def read_jsonl(path):
    out = []
    if os.path.exists(path):
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    try:
                        out.append(json.loads(line))
                    except json.JSONDecodeError:
                        pass  # a torn last line from a hard kill — ignored, the item is simply redone
    return out


class Log:
    def __init__(self, path):
        self.path = path

    def __call__(self, msg):
        line = f"{datetime.now().strftime('%H:%M:%S')} {msg}"
        print(line, flush=True)
        with open(self.path, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")


def front(driver):
    driver.execute_cdp_cmd("Page.bringToFront", {})
    driver.execute_cdp_cmd("Emulation.setFocusEmulationEnabled", {"enabled": True})


def ensure_logged_in(driver):
    if "photos.google.com" not in (driver.current_url or ""):
        raise LoggedOut(f"not on photos.google.com (logged out?) -> {driver.current_url}")


def enumerate_tiles(driver, tiles_path, log):
    """Phase 1: scroll the main timeline top->bottom recording every item id + its tile label."""
    known = {t["photo_id"] for t in read_jsonl(tiles_path)}
    log(f"enumerate: {len(known)} ids already known")
    driver.get(PHOTOS_URL)
    time.sleep(4)
    ensure_logged_in(driver)
    front(driver)
    new, stagnant, last_top, loops = 0, 0, -1, 0
    while stagnant < 6:
        loops += 1
        before = new
        for t in driver.execute_script(TILES_JS):
            m = PID_RE.search(t.get("href") or "")
            if m and m.group(1) not in known:
                known.add(m.group(1))
                append_jsonl(tiles_path, {"photo_id": m.group(1), "label": t.get("label"), "seen_at": now_iso()})
                new += 1
        top, height = driver.execute_script(SCROLL_JS)
        time.sleep(1.4)
        stagnant = stagnant + 1 if (new == before and top == last_top) else 0
        last_top = top
        if loops % 25 == 0:
            log(f"enumerate: +{new} new ids so far ({len(known)} total)")
    log(f"enumerate: done — {new} new, {len(known)} total ids")
    return known


def parse_details(body):
    """Pull the info-panel fields out of the page text (the block after 'Details')."""
    i = body.find("\nDetails\n")
    if i < 0:
        i = body.find("Details")
        if i < 0:
            return None
    lines = [l.strip() for l in body[i:].split("\n")[1:40] if l.strip()]
    out = {"details_text": "\n".join(lines[:14])[:700]}
    for idx, l in enumerate(lines[:12]):
        if TZ_RE.match(l):
            out["tz_text"] = l
            if idx >= 1:
                out["time_text"] = lines[idx - 1]
            if idx >= 2:
                out["date_text"] = lines[idx - 2]
            break
    for l in lines[:14]:
        if EXT_LINE_RE.match(l) and not MP_RE.match(l) and not l.lower().startswith(("learn more", "original quality")):
            out["filename"] = l
            break
    return out


def item_record(page_source, pid):
    """The item page embeds [<pid>,[<thumb>,w,h,...],taken_ms,"dedupKey",tz_offset_ms,upload_ms,...]."""
    m = re.search(re.escape(pid) + r'",\["[^"]*",(\d+),(\d+),.*?\],(-?\d{10,14}),"([A-Za-z0-9_-]{27})",(-?\d+),(\d{12,14})',
                  page_source)
    if not m:
        return {}
    return {"width": int(m.group(1)), "height": int(m.group(2)), "taken_ms": int(m.group(3)),
            "dedup_key": m.group(4), "tz_offset_ms": int(m.group(5)), "upload_ms": int(m.group(6))}


def open_info_panel(driver):
    for sel in ("[aria-label='Open info']", "[aria-label='Info']", "[aria-label='Show info']"):
        els = [e for e in driver.find_elements(By.CSS_SELECTOR, sel) if e.is_displayed()]
        if els:
            driver.execute_script("arguments[0].click();", els[0])
            return
    webdriver.ActionChains(driver).send_keys("i").perform()


def crawl_item(driver, pid):
    driver.get(f"{PHOTOS_URL}/photo/{pid}")
    ensure_logged_in(driver)
    front(driver)
    details, clicked = None, False
    deadline = time.time() + 12
    while time.time() < deadline:
        time.sleep(0.4)
        body = driver.find_element(By.TAG_NAME, "body").text
        details = parse_details(body)
        if details and details.get("filename") and details.get("tz_text"):
            break
        if not clicked and "Details" not in body and time.time() > deadline - 9.5:
            open_info_panel(driver)  # panel genuinely closed (first item) — open it once, never toggle it shut
            clicked = True
    rec = item_record(driver.page_source, pid)
    out = {"photo_id": pid}
    out.update(details or {})
    out.update(rec)
    if not (details and details.get("filename")) or not rec:
        out["error"] = "incomplete: " + ", ".join(k for k, ok in (("details", bool(details and details.get("filename"))),
                                                                   ("record", bool(rec))) if not ok)
    return out


def crawl_details(driver, account, tiles_path, items_path, log, max_items=None):
    tiles = {t["photo_id"]: t for t in read_jsonl(tiles_path)}
    done = {r["photo_id"] for r in read_jsonl(items_path) if not r.get("error")}
    todo = [pid for pid in tiles if pid not in done]
    if max_items:
        todo = todo[:max_items]
    log(f"details: {len(done)} done, {len(todo)} to go (of {len(tiles)} ids)")
    t0, n = time.time(), 0
    for pid in todo:
        try:
            rec = crawl_item(driver, pid)
        except LoggedOut:
            raise
        except Exception as e:  # one bad item never stops the crawl; it is retried next run
            rec = {"photo_id": pid, "error": f"{type(e).__name__}: {str(e)[:160]}"}
        rec["tile_label"] = tiles[pid].get("label")
        rec["account"] = account
        rec["crawled_at"] = now_iso()
        append_jsonl(items_path, rec)
        n += 1
        if n % 25 == 0 or n == len(todo):
            rate = (time.time() - t0) / n
            log(f"details: {n}/{len(todo)} ({rate:.1f}s/item, ~{rate * (len(todo) - n) / 60:.0f} min left)"
                + (f" last error: {rec['error']}" if rec.get("error") else ""))
        time.sleep(random.uniform(0.3, 0.9))
    log(f"details: pass complete — {n} items processed this run")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("account", choices=sorted(ACCOUNTS))
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--attach", type=int, metavar="PORT", help="attach to an already-open Chrome on this debug port")
    g.add_argument("--port", type=int, help="launch this account's Chrome profile on this debug port (not 9222)")
    ap.add_argument("--out", default=DEFAULT_OUT)
    ap.add_argument("--phase", choices=("enumerate", "details", "all"), default="all")
    ap.add_argument("--max", type=int, default=None, help="details phase: stop after N items (smoke runs)")
    a = ap.parse_args()

    account = ACCOUNTS[a.account]
    profile_dir = CHROME_PROFILES[account]
    os.makedirs(a.out, exist_ok=True)
    tiles_path = os.path.join(a.out, f"{account}.tiles.jsonl")
    items_path = os.path.join(a.out, f"{account}.items.jsonl")
    log = Log(os.path.join(a.out, f"{account}.log"))
    log(f"=== gp_inventory {account} ({'attach %d' % a.attach if a.attach else 'launch %d' % a.port}) phase={a.phase}")

    proc = None
    if a.port:
        if a.port == 9222:
            sys.exit("Use a port other than 9222 when launching (mainfetch and the user's Chrome use 9222).")
        if not os.path.isdir(profile_dir):
            sys.exit(f"Chrome profile for '{account}' does not exist: {profile_dir} — create it and sign in first.")
        proc = subprocess.Popen([CHROME_EXE, f"--user-data-dir={profile_dir}", "--profile-directory=Default",
                                 f"--remote-debugging-port={a.port}", "--no-first-run", "--no-default-browser-check",
                                 "--disable-session-crashed-bubble", "--disable-backgrounding-occluded-windows",
                                 "--disable-renderer-backgrounding", "--disable-background-timer-throttling",
                                 "--window-size=1600,1000", "about:blank"])
        time.sleep(6)
    opts = Options()
    opts.add_experimental_option("debuggerAddress", f"127.0.0.1:{a.attach or a.port}")
    driver = webdriver.Chrome(options=opts)
    user_handles = list(driver.window_handles)
    try:
        if a.attach:
            driver.switch_to.new_window("tab")  # work in our own tab; the user's tabs are left alone
        front(driver)
        if a.phase in ("enumerate", "all"):
            enumerate_tiles(driver, tiles_path, log)
        if a.phase in ("details", "all"):
            crawl_details(driver, account, tiles_path, items_path, log, a.max)
    except LoggedOut as e:
        log(f"STOPPED: {e} — sign this profile in to photos.google.com, then re-run (progress is kept).")
        sys.exit(2)
    except KeyboardInterrupt:
        log("interrupted — progress is saved; re-run to resume.")
    finally:
        try:
            if a.attach and driver.current_window_handle not in user_handles:
                driver.close()  # close only our own tab
        except Exception:
            pass
        try:
            driver.service.stop()
        except Exception:
            pass
        if proc:
            proc.terminate()  # close only the Chrome this run launched
    log("=== done")


if __name__ == "__main__":
    main()
