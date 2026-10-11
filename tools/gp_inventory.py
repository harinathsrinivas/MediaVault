"""Google Photos inventory crawler (IMP-C25) — READ-ONLY against Google Photos.

Records, for every item of one Google account's Photos library, what Google Photos holds: the exact uploaded
filename, the taken time (ms), the dedupKey (= urlsafe-base64 SHA-1 of the uploaded bytes), the timezone offset
(ms), the upload time (ms) and width x height; the rpc mode adds size, original quality, partial upload, archived
and duration. MediaVault maps library nodes to these items (map_photos) and uses them as each account's item
cache (check_photos) — docs/feature-fetch-datetime/PLAN.md Step 7.

It NEVER downloads, edits, deletes, shares or favourites anything, and never touches the MediaVault libraries.
Every file is append-only JSONL written with fsync, so a crash, Ctrl+C, reboot or Claude session limit loses
nothing: re-running resumes where it stopped.

Modes (--mode):
  rpc (default)  read-only internal RPCs through gprpc (its allowlist only): the lcxiM timeline lists every item
                 (500 per page, library + archive), the EzkLib recently-uploaded listing finds new ones first, and
                 EWgK9e batches add file name, size and quality. Minutes per account. When RPC is unavailable the
                 run falls back to the dom mode by itself (one WARNING line).
  dom            the browser crawl: scroll the timeline for every id, then open each new item's page for its info
                 panel + embedded record. Hours per account; archived items are not visible.

A run checks the Chrome profile before launching it (--port), takes the fetch session lock (refused while a fetch,
the profile warm-up or another crawl runs; kept fresh while the crawl makes progress; the crawl yields if another
command takes it over), stops at once when the profile is signed out, lists EVERY id of the account (the run log)
and then details only new or errored ids (the rpc mode also upgrades records from a dom crawl once, adding size,
quality and the exact file name).

Usage (run from the repo root; one crawl at a time — run the accounts one after another):
  python tools/gp_inventory.py tv --port 9225          # launch that profile's Chrome on its own debug port
  python tools/gp_inventory.py movies --port 9223
  python tools/gp_inventory.py anime --port 9224 --phase enumerate   # ids only: quick, and a sign-in check
  python tools/gp_inventory.py tv --attach 9222        # attach to an already-open Chrome (works in its own tab)
  python tools/gp_inventory.py tv --port 9225 --mode dom --phase details --max 3   # dom smoke
  python tools/gp_inventory.py all --status            # offline summary: no Chrome, no lock, writes nothing
Options: --out DIR (default D:\\MediaVault_gp_inventory) · --mode rpc|dom · --phase enumerate|details|all (the
rpc mode always lists the ids; --phase enumerate skips the details) · --max N (details: stop after N items)

Files per account in --out (the formats of the files crawled before 2026-09-28, extended):
  <account>.items.jsonl  one record per detailed item; the latest good line per photo_id wins. rpc records carry
                         "source": "rpc" and may add size_bytes, duration_ms, original_quality, partial_upload,
                         archived
  <account>.runs.jsonl   the run log: per run a {"run", "event": "start"} line, one {"photo_id", "seen_run"} line
                         per id seen, an {"event": "enumerated", "ids": N} line once the whole account was listed
                         and an {"event": "end", "result"} line; known ids absent from the latest complete
                         enumeration are possibly deleted
  <account>.tiles.jsonl  dom mode: every id + its tile label at first sighting
  <account>.log          the progress log
Exit codes: 0 done (or interrupted — progress kept) · 1 usage error or missing Chrome profile · 2 signed out ·
3 the fetch session lock is held (refused) or was taken over (the crawl yielded).
"""
import argparse
import contextlib
import json
import os
import random
import subprocess
import sys
import time
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import mainfetch  # noqa: E402  (CHROME_PROFILES, profile_preflight, check_session_alive — read at run time)
from mainfetch import PHOTOS_URL  # noqa: E402  (single source of truth)
import mvcommon  # noqa: E402  (fetch_session_lock + GP_INVENTORY_DIR — read module-qualified: tests redirect them)
import gpweb  # noqa: E402  (the ONE shared Google Photos parser module — IMP-C25 Step 2)
import gprpc  # noqa: E402  (the ONE read-only Google Photos RPC client — IMP-C25 Step 2b)

from selenium import webdriver  # noqa: E402
from selenium.webdriver.chrome.options import Options  # noqa: E402
from selenium.webdriver.common.by import By  # noqa: E402

CHROME_EXE = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
DEFAULT_OUT = r"D:\MediaVault_gp_inventory"  # == mvcommon.GP_INVENTORY_DIR, the folder a run really uses
ACCOUNTS = {"movies": "movies", "tv": "tv", "series": "tv", "anime": "anime", "others": "others"}

TILES_JS = gpweb.TILES_JS    # visible result/timeline tiles → [{href, label}]
SCROLL_JS = gpweb.SCROLL_JS  # scroll the virtualised grid → [scrollTop, scrollHeight]
PID_RE = gpweb.PHOTO_ID_RE

LOCK_HEARTBEAT_S = 60                     # refresh the lock's mtime at most this often while the crawl progresses
TIMELINE_PAGE_SIZE = gprpc.MAX_PAGE_SIZE  # lcxiM items per page (500)
TIMELINE_MAX_PAGES = 2000                 # safety cap on one timeline pass (1,000,000 items)
RECENT_MAX_PAGES = 20                     # the recently-uploaded pass only needs the newest pages
DETAIL_BATCH = gprpc.MAX_BATCH_KEYS       # ids per EWgK9e request (50)
CHROME_START_WAIT_S = 6                   # a launched Chrome needs this long before Selenium can attach
LANDING_WAIT_S = 3                        # let a signed-out redirect happen before the landing check
EXIT_OK, EXIT_USAGE, EXIT_SIGNED_OUT, EXIT_LOCK = 0, 1, 2, 3
_HINT_KEYS = ("details_text", "tz_text", "time_text", "date_text")  # dom info-panel text an upgrade keeps


class LoggedOut(Exception):
    pass


class LockLost(Exception):
    """The fetch session lock is no longer this crawl's: another MediaVault command took it over."""


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def new_run_id():
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + os.urandom(3).hex()


# ================================================================== storage: append-only JSONL, fsync'd

def _ends_torn(path):
    """True when the file's last line has no newline — a hard kill cut a write short."""
    try:
        with open(path, "rb") as fh:
            fh.seek(0, os.SEEK_END)
            if fh.tell() == 0:
                return False
            fh.seek(-1, os.SEEK_END)
            return fh.read(1) != b"\n"
    except FileNotFoundError:
        return False


def append_jsonl_many(path, objs):
    """Append each object as ONE JSON line — text mode, so the platform newline exactly as every file crawled so
    far — with a single flush + fsync. A torn last line is terminated first, so it can never swallow a record."""
    text = "".join(json.dumps(obj, ensure_ascii=False) + "\n" for obj in objs)
    if not text:
        return
    if _ends_torn(path):
        text = "\n" + text
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(text)
        fh.flush()
        os.fsync(fh.fileno())


def append_jsonl(path, obj):
    append_jsonl_many(path, [obj])


def read_jsonl(path, problems=None):
    """Every JSON-object line of a JSONL file, in file order ([] when it does not exist). A line that is not a
    UTF-8 JSON object — e.g. a torn last line from a hard kill — is skipped (its item is simply redone) and, when
    `problems` is a list, reported there as {file, line, error}."""
    try:
        with open(path, "rb") as fh:
            data = fh.read()
    except FileNotFoundError:
        return []
    out = []
    for lineno, raw in enumerate(data.split(b"\n"), 1):
        raw = raw.strip()
        if not raw:
            continue
        try:
            obj = json.loads(raw.decode("utf-8-sig"))
        except ValueError:  # UnicodeDecodeError and JSONDecodeError are both ValueErrors
            obj = None
        if isinstance(obj, dict):
            out.append(obj)
        elif problems is not None:
            problems.append({"file": os.path.basename(path), "line": lineno,
                             "error": "not a JSON object line (torn write?)"})
    return out


def _read_bytes(path):
    try:
        with open(path, "rb") as fh:
            return fh.read()
    except OSError:
        return None


class Log:
    def __init__(self, path):
        self.path = path

    def __call__(self, msg):
        line = f"{datetime.now().strftime('%H:%M:%S')} {msg}"
        print(line, flush=True)
        with open(self.path, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")


# ================================================================== dom mode (the browser crawl)

def front(driver):
    driver.execute_cdp_cmd("Page.bringToFront", {})
    driver.execute_cdp_cmd("Emulation.setFocusEmulationEnabled", {"enabled": True})


def ensure_logged_in(driver):
    if "photos.google.com" not in (driver.current_url or ""):
        raise LoggedOut(f"not on photos.google.com (logged out?) -> {driver.current_url}")


def enumerate_tiles(driver, tiles_path, log, run=None):
    """Phase 1 (dom): scroll the main timeline top->bottom recording every item id + its tile label. With a crawl
    `run`, every id seen — not only new ones — also goes to the run log and the lock heartbeats."""
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
        visible = []
        for t in driver.execute_script(TILES_JS):
            m = PID_RE.search(t.get("href") or "")
            if not m:
                continue
            visible.append(m.group(1))
            if m.group(1) not in known:
                known.add(m.group(1))
                append_jsonl(tiles_path, {"photo_id": m.group(1), "label": t.get("label"), "seen_at": now_iso()})
                new += 1
        if run is not None:
            run.seen_many(visible)
            run.beat()
        top, height = driver.execute_script(SCROLL_JS)
        time.sleep(1.4)
        stagnant = stagnant + 1 if (new == before and top == last_top) else 0
        last_top = top
        if loops % 25 == 0:
            log(f"enumerate: +{new} new ids so far ({len(known)} total)")
    log(f"enumerate: done — {new} new, {len(known)} total ids")
    return known


def parse_details(body):
    """The info-panel fields (the block after 'Details') via gpweb.info_panel, in this file's record
    format: details_text, then tz_text / time_text / date_text / filename when found — or None."""
    panel = gpweb.info_panel(body)
    if panel is None:
        return None
    out = {"details_text": panel["details_text"]}
    for key in ("tz_text", "time_text", "date_text", "filename"):
        if panel[key] is not None:
            out[key] = panel[key]
    return out


def item_record(page_source, pid):
    """The record the item page embeds — [<pid>,[<thumb>,w,h,…],taken_ms,"dedupKey",tz_offset_ms,
    upload_ms,…] — via gpweb.page_record, without its photo_id key (this file's record format)."""
    return {k: v for k, v in gpweb.page_record(page_source, pid).items() if k != "photo_id"}


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


def crawl_details(driver, account, tiles_path, items_path, log, max_items=None, run=None, ids=None):
    """Phase 2 (dom): open every not-yet-detailed item's page and append its record. The ids come from the tiles
    file — or from `ids` (a crawl run passes the ids it enumerated); an id the tiles file does not hold gets no
    tile_label. With a crawl `run`, each record is counted and the lock heartbeats after every item."""
    tiles = {t["photo_id"]: t for t in read_jsonl(tiles_path)}
    done = {r["photo_id"] for r in read_jsonl(items_path) if not r.get("error")}
    universe = tiles if ids is None else ids
    todo = [pid for pid in universe if pid not in done]
    if max_items:
        todo = todo[:max_items]
    log(f"details: {len(done)} done, {len(todo)} to go (of {len(universe)} ids)")
    t0, n = time.time(), 0
    for pid in todo:
        try:
            rec = crawl_item(driver, pid)
        except LoggedOut:
            raise
        except Exception as e:  # one bad item never stops the crawl; it is retried next run
            rec = {"photo_id": pid, "error": f"{type(e).__name__}: {str(e)[:160]}"}
        if pid in tiles:
            rec["tile_label"] = tiles[pid].get("label")
        rec["account"] = account
        rec["crawled_at"] = now_iso()
        append_jsonl(items_path, rec)
        n += 1
        if run is not None:
            run.written(pid, ok=not rec.get("error"))
        if n % 25 == 0 or n == len(todo):
            rate = (time.time() - t0) / n
            log(f"details: {n}/{len(todo)} ({rate:.1f}s/item, ~{rate * (len(todo) - n) / 60:.0f} min left)"
                + (f" last error: {rec['error']}" if rec.get("error") else ""))
        if run is not None:
            run.beat()
        time.sleep(random.uniform(0.3, 0.9))
    log(f"details: pass complete — {n} items processed this run")


# ================================================================== the fetch session lock + its heartbeat

class Heartbeat:
    """Keeps this crawl's fetch session lock fresh — driven by PROGRESS, never by a background thread.

    The crawl calls beat() as it works (every timeline page, detail batch, scroll step and item page); at most
    every LOCK_HEARTBEAT_S (PLAN Step 7: every <= 10 min) that refreshes the lock file's mtime, so the stale-reclaim
    of mvcommon.fetch_session_lock (mtime older than 1 h — warm_profiles, a fetch) never pre-empts a long crawl.
    A crawl that stops making progress stops beating, so a hung crawl's lock goes stale and can be reclaimed: it
    is never held forever. Every refresh first checks that the lock still holds the line mvcommon wrote when this
    crawl took it; when another command took it over, beat() raises LockLost and the crawl yields."""

    def __init__(self, path, interval=None):
        self.path = path
        self.interval = LOCK_HEARTBEAT_S if interval is None else interval
        self.token = _read_bytes(path)
        self.last = time.time()
        self.beats = 0

    def beat(self):
        now = time.time()
        if now - self.last < self.interval:
            return
        self.last = now
        if not self.token or _read_bytes(self.path) != self.token:
            raise LockLost("the fetch session lock was taken over by another MediaVault command")
        try:
            os.utime(self.path, None)
        except OSError:
            raise LockLost("the fetch session lock could not be refreshed") from None
        self.beats += 1


@contextlib.contextmanager
def crawl_lock():
    """mvcommon.fetch_session_lock(blocking=False) held for one crawl -> its Heartbeat. Raises
    mvcommon.LockHeldError — before anything is launched — while a fetch, the profile warm-up or another crawl
    holds the lock."""
    with mvcommon.fetch_session_lock(blocking=False):
        yield Heartbeat(mvcommon.FETCH_SESSION_LOCK)


# ================================================================== the run log

class Run:
    """One crawl run's lines in <account>.runs.jsonl (PLAN Step 7 (3)):
      {"run", "event": "start", "at", "account", "mode", "phase"}
      {"photo_id", "seen_run"}                      one per id seen by the run's enumeration (every id, not only new)
      {"run", "event": "enumerated", "at", "mode", "ids"}   written once the WHOLE account was listed — the only
                                                    proof of a complete enumeration (ids = its seen-line count)
      {"run", "event": "end", "at", "result", "detailed", "upgraded", "errors", "mode", "new", …}
    The end line is written on every exit but a hard kill; readers never need it."""

    def __init__(self, path, account, mode, phase, heartbeat=None):
        self.path, self.account, self.mode, self.phase = path, account, mode, phase
        self.heartbeat = heartbeat
        self.id = new_run_id()
        self.seen, self.order = set(), []
        self.done = set()
        self.counts = {"detailed": 0, "upgraded": 0, "errors": 0}

    def start(self):
        append_jsonl(self.path, {"run": self.id, "event": "start", "at": now_iso(), "account": self.account,
                                 "mode": self.mode, "phase": self.phase})

    def seen_many(self, photo_ids):
        lines = []
        for pid in photo_ids:
            if pid not in self.seen:
                self.seen.add(pid)
                self.order.append(pid)
                lines.append({"photo_id": pid, "seen_run": self.id})
        append_jsonl_many(self.path, lines)

    def enumerated(self, mode):
        append_jsonl(self.path, {"run": self.id, "event": "enumerated", "at": now_iso(), "mode": mode,
                                 "ids": len(self.seen)})

    def written(self, pid, ok, upgraded=False):
        self.done.add(pid)
        self.counts["upgraded" if upgraded else "detailed" if ok else "errors"] += 1

    def end(self, result, **extra):
        append_jsonl(self.path, {"run": self.id, "event": "end", "at": now_iso(), "result": result,
                                 **self.counts, **extra})

    def beat(self):
        if self.heartbeat is not None:
            self.heartbeat.beat()


# ================================================================== the offline reader (--status, Steps 9 and 13)

_EARLIEST = datetime.min.replace(tzinfo=timezone.utc)


def _is_id(value):
    return isinstance(value, str) and bool(value)


def _parse_at(value):
    """An ISO-8601 time as now_iso() writes it -> an aware datetime (a naive one is taken as UTC), or None."""
    if not isinstance(value, str):
        return None
    try:
        dt = datetime.fromisoformat(value.strip())
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def inventory_paths(account, out_dir):
    return {kind: os.path.join(out_dir, f"{account}.{kind}.jsonl") for kind in ("tiles", "items", "runs")}


def inventory_state(account, out_dir=None):
    """The OFFLINE view of one account's inventory — reads the files only (no Chrome, no lock, nothing written).
    The reader for --status, map_photos (PLAN Step 9: freshness gate, possibly-deleted ids, quality/partial
    flags) and check_photos' account item cache (Step 13). -> dict:
      items             {photo_id: record} — the LATEST GOOD line per id (a later error line never hides an earlier
                        good one); an id with only error lines maps to its latest error line
      known_ids         every id ever recorded (tiles, items, run log), first-sighting order
      detailed          ids with a good record · errored: ids whose records are all errors
      runs              [{run, started_at, mode, phase, enumerated_at, enumeration_mode, ids, complete, ended_at,
                        result}] in log order
      latest_complete   {run, started_at, enumerated_at, mode, ids: frozenset} of the latest run whose
                        "enumerated" line is valid (its ids count == its seen lines), or None. started_at is the
                        as-of time of that enumeration: anything uploaded before it was listed.
      possibly_deleted  [ids recorded BEFORE latest_complete began but absent from it] — before = a seen line
                        earlier in the run log than its start line (exact, clock-free), or a tiles/items line
                        stamped earlier than its start (the files crawled before run logging); an item recorded
                        archived is not flagged after a dom enumeration (the dom timeline hides the archive);
                        [] without a complete enumeration
      problems          [{file, line, error}] for skipped lines (torn writes)
      out_dir, paths, account"""
    out_dir = out_dir or mvcommon.GP_INVENTORY_DIR
    paths = inventory_paths(account, out_dir)
    problems, known = [], {}  # known: photo_id -> None, in first-sighting order (tiles, items, run log)
    first_time, first_pos = {}, {}  # earliest tiles/items stamp (undated = earliest) · first seen line's position

    def stamped(pid, at):
        known.setdefault(pid, None)
        when = _parse_at(at) or _EARLIEST
        if pid not in first_time or when < first_time[pid]:
            first_time[pid] = when

    for tile in read_jsonl(paths["tiles"], problems):
        if _is_id(tile.get("photo_id")):
            stamped(tile["photo_id"], tile.get("seen_at"))
    good, bad = {}, {}
    for rec in read_jsonl(paths["items"], problems):
        if _is_id(rec.get("photo_id")):
            stamped(rec["photo_id"], rec.get("crawled_at"))
            (bad if rec.get("error") else good)[rec["photo_id"]] = rec

    run_lines = read_jsonl(paths["runs"], problems)
    runs, markers, marker_ids, start_pos, seen_by_run = {}, [], {}, {}, {}
    for pos, line in enumerate(run_lines):
        rid, pid = line.get("seen_run"), line.get("photo_id")
        if isinstance(rid, str) and _is_id(pid):
            known.setdefault(pid, None)
            first_pos.setdefault(pid, pos)
            seen_by_run.setdefault(rid, set()).add(pid)
            continue
        rid, event = line.get("run"), line.get("event")
        if not (isinstance(rid, str) and event in ("start", "enumerated", "end")):
            continue
        info = runs.setdefault(rid, {"run": rid, "started_at": None, "mode": None, "phase": None,
                                     "enumerated_at": None, "enumeration_mode": None, "ids": 0, "complete": False,
                                     "ended_at": None, "result": None})
        if event == "start":
            info.update(started_at=line.get("at"), mode=line.get("mode"), phase=line.get("phase"))
            start_pos.setdefault(rid, pos)
        elif event == "enumerated":
            info.update(enumerated_at=line.get("at"), enumeration_mode=line.get("mode"))
            marker_ids[rid] = line.get("ids")
            markers.append(rid)
        else:
            info.update(ended_at=line.get("at"), result=line.get("result"))
    for rid, info in runs.items():
        info["ids"] = len(seen_by_run.get(rid, ()))
        count = marker_ids.get(rid)
        info["complete"] = bool(rid in start_pos and _parse_at(info["started_at"]) and info["enumerated_at"]
                                and isinstance(count, int) and not isinstance(count, bool) and count == info["ids"])

    items = {pid: good[pid] if pid in good else bad[pid] for pid in known if pid in good or pid in bad}
    latest = next((rid for rid in reversed(markers) if runs[rid]["complete"]), None)
    latest_complete, possibly_deleted = None, []
    if latest is not None:
        info, ids = runs[latest], frozenset(seen_by_run.get(latest, ()))
        latest_complete = {"run": latest, "started_at": info["started_at"], "enumerated_at": info["enumerated_at"],
                           "mode": info["enumeration_mode"], "ids": ids}
        begin, start = start_pos[latest], _parse_at(info["started_at"])
        for pid in known:
            if pid in ids:
                continue
            if not (first_pos.get(pid, begin) < begin or first_time.get(pid, start) < start):
                continue  # first recorded after that enumeration began: new, not deleted
            if info["enumeration_mode"] == "dom" and items.get(pid, {}).get("archived") is True:
                continue  # the dom timeline cannot show archived items
            possibly_deleted.append(pid)
    return {"account": account, "out_dir": out_dir, "paths": paths, "items": items, "known_ids": list(known),
            "detailed": set(good), "errored": set(bad) - set(good), "runs": list(runs.values()),
            "latest_complete": latest_complete, "possibly_deleted": possibly_deleted, "problems": problems}


# ================================================================== rpc mode

def _mark_enumerated(run, mode, known, log):
    """Write the run's "enumerated" line — never for an enumeration that found nothing while ids are known: a
    grid that did not render or a broken listing must not make the whole inventory look deleted."""
    if not run.seen and known:
        log("WARNING: the enumeration found no items although the inventory knows some — NOT marked complete")
        return False
    run.enumerated(mode)
    log(f"enumeration complete ({mode}): {len(run.seen):,} ids")
    return True


def rpc_enumerate(driver, run, known, log):
    """The rpc enumeration: on an incremental run (ids known) the recently-uploaded listing (EzkLib) finds the
    new ids first; then the full lcxiM timeline pass (library + archive) lists EVERY id — it also catches what the
    listing missed and is what deletion detection relies on. Every id seen goes to the run log; the run is marked
    enumerated when the pass reached its last page and no record was unreadable.
    -> {photo_id: record (PLAN P3 fields + P12 flags)}. Raises gprpc.RpcUnavailable (the dom fallback)."""
    records = {}
    if known:
        recent = gprpc.recent_uploads(driver, known, max_pages=RECENT_MAX_PAGES)
        for rec in recent["items"]:
            records[rec["photo_id"]] = rec
        run.seen_many([rec["photo_id"] for rec in recent["items"]])
        run.beat()
        log(f"enumerate (rpc): recently uploaded — {len(recent['items']):,} new id(s) on {recent['pages']} page(s)")
    page_id, handed_out, pages, rejected, capped = None, set(), 0, 0, False
    while True:
        page = gprpc.timeline_at(driver, None, page_size=TIMELINE_PAGE_SIZE, page_id=page_id)
        pages += 1
        rejected += page["rejected"]
        for rec in page["items"]:
            records[rec["photo_id"]] = rec
        run.seen_many([rec["photo_id"] for rec in page["items"]])
        run.beat()
        page_id = page["next_page_id"]
        if page_id is None:
            break
        if page_id in handed_out:
            raise gprpc.RpcShapeError("lcxiM handed out the same page id twice — paging loop not followed")
        handed_out.add(page_id)
        if pages >= TIMELINE_MAX_PAGES:
            capped = True
            break
    new = sum(1 for pid in run.order if pid not in known)
    log(f"enumerate (rpc): {len(run.seen):,} ids ({new:,} new) in {pages} timeline page(s)")
    if rejected:
        log(f"WARNING: {rejected} timeline record(s) had an unexpected shape — this enumeration is NOT marked "
            f"complete (a dom crawl, --mode dom, can make a complete one)")
    elif capped:
        log(f"WARNING: the timeline pass stopped at {TIMELINE_MAX_PAGES} pages — this enumeration is NOT marked "
            f"complete")
    else:
        _mark_enumerated(run, "rpc", known, log)
    return records


def rpc_record(rec, info, account, legacy=None):
    """One items.jsonl record from the lcxiM record `rec` and the EWgK9e answer `info` (None when the answer left
    the id out): this file's record format and key order, only known facts, "source": "rpc". An upgrade of a dom
    record (`legacy`) keeps its info-panel text and tile label — display hints only (PLAN P-7)."""
    info = info or {}

    def pick(key):  # the P3 record first, EWgK9e when the record lacks it
        return rec.get(key) if rec.get(key) is not None else info.get(key)

    out = {"photo_id": rec["photo_id"]}
    for key in _HINT_KEYS:
        if legacy and key in legacy:
            out[key] = legacy[key]
    facts = (("filename", info.get("filename")), ("width", rec.get("width")), ("height", rec.get("height")),
             ("taken_ms", pick("taken_ms")), ("dedup_key", rec.get("dedup_key")),
             ("tz_offset_ms", pick("tz_offset_ms")), ("upload_ms", pick("upload_ms")),
             ("size_bytes", info.get("size_bytes")), ("duration_ms", rec.get("duration_ms")),
             ("original_quality", info.get("original_quality")), ("partial_upload", rec.get("partial_upload")),
             ("archived", rec.get("archived")))
    out.update((key, value) for key, value in facts if value is not None)
    if not info:
        out["error"] = "incomplete: batch info"
    else:
        missing = [key for key in ("filename", "taken_ms", "dedup_key") if key not in out]
        if missing:
            out["error"] = "incomplete: " + ", ".join(missing)
    if legacy and "tile_label" in legacy:
        out["tile_label"] = legacy["tile_label"]
    out["account"] = account
    out["crawled_at"] = now_iso()
    out["source"] = "rpc"
    return out


def rpc_details(driver, account, items_path, state, records, run, max_items, log):
    """Phase 2 (rpc): EWgK9e batches for the ids this run enumerated that have no good record (new or errored),
    then — once — for the ids whose good record came from a dom crawl, adding size, quality and the exact file
    name (a failed upgrade writes nothing: the dom record stays). One append + fsync per batch; the lock
    heartbeats after each batch."""
    items, detailed = state["items"], state["detailed"]
    todo = [pid for pid in run.order if pid in records and pid not in detailed]
    upgrade = [pid for pid in run.order if pid in records and pid in detailed and items[pid].get("source") != "rpc"]
    work = todo + upgrade
    if max_items:
        work = work[:max_items]
    log(f"details (rpc): {len(todo):,} new or errored, {len(upgrade):,} to upgrade from the dom crawl"
        + (f" — {len(work):,} this run (--max)" if len(work) < len(todo) + len(upgrade) else ""))
    for n, i in enumerate(range(0, len(work), DETAIL_BATCH), 1):
        chunk = work[i:i + DETAIL_BATCH]
        info = gprpc.batch_info(driver, chunk)
        lines, outcomes = [], []
        for pid in chunk:
            legacy = items[pid] if pid in detailed else None
            rec = rpc_record(records[pid], info.get(pid), account, legacy)
            if legacy is not None and "error" in rec:
                continue  # a failed upgrade never replaces the good dom record
            lines.append(rec)
            outcomes.append((pid, "error" not in rec, legacy is not None))
        append_jsonl_many(items_path, lines)
        for pid, ok, upgraded in outcomes:
            run.written(pid, ok, upgraded)
        run.beat()
        if n % 10 == 0 and i + DETAIL_BATCH < len(work):
            log(f"details (rpc): {i + len(chunk):,}/{len(work):,}")
    c = run.counts
    log(f"details (rpc): pass complete — {c['detailed']:,} detailed, {c['upgraded']:,} upgraded, "
        f"{c['errors']:,} errored")


# ================================================================== one crawl run

def _crawl_rpc(driver, account, paths, state, known, run, phase, max_items, log, extra):
    """rpc mode (it always enumerates — the timeline pass is cheap and yields the records the details need). On
    gprpc.RpcUnavailable: ONE warning line, then the dom crawl does what is left of this run. -> the mode used."""
    records = None
    try:
        records = rpc_enumerate(driver, run, known, log)
        if phase != "enumerate":
            rpc_details(driver, account, paths["items"], state, records, run, max_items, log)
        return "rpc"
    except gprpc.RpcUnavailable as e:
        log(f"WARNING: RPC unavailable ({e}) — falling back to the dom crawl for the rest of this run")
        extra.update(mode="dom", fallback=str(e))
    if records is None:
        enumerate_tiles(driver, paths["tiles"], log, run=run)
        _mark_enumerated(run, "dom", known, log)
    if phase != "enumerate":
        crawl_details(driver, account, paths["tiles"], paths["items"], log, max_items, run=run,
                      ids=[pid for pid in run.order if pid not in run.done])
    return "dom"


def _crawl_dom(driver, account, paths, state, known, run, phase, max_items, log):
    ids = None
    if phase != "details":
        enumerate_tiles(driver, paths["tiles"], log, run=run)
        _mark_enumerated(run, "dom", known, log)
        ids = list(run.order)
    if phase != "enumerate":
        if ids is None:  # details only: every known id the last complete enumeration did not flag
            gone = set(state["possibly_deleted"])
            ids = [pid for pid in state["known_ids"] if pid not in gone]
        crawl_details(driver, account, paths["tiles"], paths["items"], log, max_items, run=run, ids=ids)


def crawl(driver, account, out_dir, mode="rpc", phase="all", max_items=None, log=print, heartbeat=None):
    """One crawl run on an open, signed-in browser session (PLAN Step 7): the run log, the enumeration of every id
    and the details of new or errored ids. The end line is written on every exit but a hard kill.
    -> {result, detailed, upgraded, errors, mode, new, …} (the end line's fields)."""
    os.makedirs(out_dir, exist_ok=True)
    paths = inventory_paths(account, out_dir)
    state = inventory_state(account, out_dir)
    known = set(state["known_ids"])
    run = Run(paths["runs"], account, mode, phase, heartbeat)
    extra, result = {"mode": mode}, "failed"
    run.start()
    try:
        if mode == "rpc":
            extra["mode"] = _crawl_rpc(driver, account, paths, state, known, run, phase, max_items, log, extra)
        else:
            _crawl_dom(driver, account, paths, state, known, run, phase, max_items, log)
        result = "ok"
    except KeyboardInterrupt:
        result = "interrupted"
        raise
    except LockLost:
        result = "lock lost"
        raise
    except (LoggedOut, mainfetch.SessionExpiredError):
        result = "signed out"
        raise
    except BaseException as e:
        result = f"failed: {type(e).__name__}"
        raise
    finally:
        extra["new"] = sum(1 for pid in run.order if pid not in known)
        if mode == "rpc":
            extra["rpc_calls"] = gprpc.stats(driver)["calls"]
            log(gprpc.summary_line(driver))
        with contextlib.suppress(OSError):
            run.end(result, **extra)
    return {"result": result, **run.counts, **extra}


# ================================================================== --status (offline)

def _fmt_at(value):
    dt = _parse_at(value)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC") if dt else "?"


def _age(value, now):
    dt = _parse_at(value)
    if dt is None:
        return "?"
    seconds = max(0.0, (now - dt).total_seconds())
    if seconds < 3600:
        return f"{seconds / 60:.0f} min"
    if seconds < 2 * 86400:
        return f"{seconds / 3600:.1f} h"
    return f"{seconds / 86400:.1f} days"


def print_status(account, out_dir=None, now=None, out=print):
    """PLAN Step 7 (4): the OFFLINE summary of one account's inventory — counts only (no ids), read from the files:
    no Chrome, no lock, nothing written. -> the inventory_state it summarised."""
    out_dir = out_dir or mvcommon.GP_INVENTORY_DIR
    now = now or datetime.now(timezone.utc)
    s = inventory_state(account, out_dir)
    crawl_cmd = f"python tools/gp_inventory.py {account} --port <port>"
    out(f"{account}: Google Photos inventory in {out_dir}")
    if not s["known_ids"] and not s["runs"]:
        out(f"  no inventory yet — run: {crawl_cmd}")
        return s
    gone = set(s["possibly_deleted"])
    items, detailed = s["items"], s["detailed"]
    rpc = sum(1 for pid in detailed if items[pid].get("source") == "rpc")
    errored = [pid for pid in s["errored"] if pid not in gone]
    never = [pid for pid in s["known_ids"] if pid not in items and pid not in gone]
    good = [items[pid] for pid in detailed]
    out(f"  ids                {len(s['known_ids']):,}")
    out(f"  detailed           {len(detailed):,}  (rpc {rpc:,}, dom {len(detailed) - rpc:,})")
    out(f"  errored            {len(errored):,}" + ("  (retried by the next run)" if errored else ""))
    out(f"  not yet detailed   {len(never):,}")
    lc = s["latest_complete"]
    if lc:
        out(f"  enumeration        complete as of {_fmt_at(lc['started_at'])} — {lc['mode']}, {len(lc['ids']):,} "
            f"ids, {_age(lc['started_at'], now)} ago")
        out(f"  possibly deleted   {len(gone):,}" + ("  (known ids absent from that enumeration)" if gone else ""))
    else:
        out(f"  enumeration        none complete yet — run: {crawl_cmd}")
    out(f"  rpc flags          partial upload {sum(1 for r in good if r.get('partial_upload') is True):,}, "
        f"not original quality {sum(1 for r in good if r.get('original_quality') is False):,}, "
        f"archived {sum(1 for r in good if r.get('archived') is True):,}")
    if s["runs"]:
        last = s["runs"][-1]
        out(f"  latest run         {_fmt_at(last['started_at'])} {last['mode'] or '?'}/{last['phase'] or '?'} — "
            f"{last['result'] or 'no end line (interrupted, or still running)'}")
    if s["problems"]:
        out(f"  unreadable lines   {len(s['problems']):,}  (torn writes — skipped)")
    return s


# ================================================================== the browser session + CLI

def landing_check(driver, account):
    """PLAN Step 7 (2): open Google Photos and stop AT ONCE when the profile is signed out (Google shows its
    sign-in page or the product page instead) — before anything is logged to the run log."""
    driver.get(PHOTOS_URL)
    time.sleep(LANDING_WAIT_S)
    mainfetch.check_session_alive(driver, account)


def launch_chrome(profile_dir, port):
    """Launch one account's Chrome profile on its own debug port (never 9222) -> the process (at once: the caller
    waits CHROME_START_WAIT_S, so even a Ctrl+C during that wait closes this Chrome). [2026-10-08] No "Restore
    pages?" bubble: a profile whose last Chrome did not record a clean exit is marked cleanly exited first
    (mainfetch.mark_profile_clean_exit), and --hide-crash-restore-bubble (Chrome 154 ignores the older switch)."""
    mainfetch.mark_profile_clean_exit(profile_dir)
    return subprocess.Popen([CHROME_EXE, f"--user-data-dir={profile_dir}", "--profile-directory=Default",
                             f"--remote-debugging-port={port}", "--no-first-run", "--no-default-browser-check",
                             "--disable-session-crashed-bubble", "--hide-crash-restore-bubble",
                             "--disable-backgrounding-occluded-windows",
                             "--disable-renderer-backgrounding", "--disable-background-timer-throttling",
                             "--window-size=1600,1000", "about:blank"])


def connect(port):
    """Selenium, attached to the Chrome that listens on 127.0.0.1:<port>."""
    opts = Options()
    opts.add_experimental_option("debuggerAddress", f"127.0.0.1:{port}")
    return webdriver.Chrome(options=opts)


def _session(a, account, out_dir, log, heartbeat):
    attach = a.attach is not None
    proc, driver, user_handles = None, None, []
    try:
        if not attach:
            proc = launch_chrome(mainfetch.CHROME_PROFILES[account], a.port)
            time.sleep(CHROME_START_WAIT_S)
        driver = connect(a.attach if attach else a.port)
        user_handles = list(driver.window_handles)
        # Work in our own tab in BOTH modes. Attached, that leaves the user's tabs alone; launched, it
        # avoids chromedriver's initial target, which on Chrome 154 is the Gemini side panel (IMP-C29).
        driver.switch_to.new_window("tab")
        front(driver)
        landing_check(driver, account)
        crawl(driver, account, out_dir, a.mode, a.phase, a.max, log, heartbeat)
        return EXIT_OK
    except (LoggedOut, mainfetch.SessionExpiredError) as e:
        where = "" if attach else f" (Chrome --user-data-dir={mainfetch.CHROME_PROFILES[account]})"
        log(f"STOPPED: {e} — sign this profile in to photos.google.com{where}, then re-run (progress is kept).")
        return EXIT_SIGNED_OUT
    except LockLost as e:
        log(f"STOPPED: {e} — this crawl yields; progress is kept, re-run when that command has finished.")
        return EXIT_LOCK
    except KeyboardInterrupt:
        log("interrupted — progress is saved; re-run to resume.")
        return EXIT_OK
    finally:
        if driver is not None:
            try:
                if attach and driver.current_window_handle not in user_handles:
                    driver.close()  # close only our own tab
            except Exception:
                pass
            try:
                driver.service.stop()
            except Exception:
                pass
        if proc:
            proc.terminate()  # close only the Chrome this run launched
            # [2026-10-08] once it is gone, no Chrome opened on its profile asks "Restore pages?"
            mainfetch.mark_profile_clean_exit(mainfetch.CHROME_PROFILES[account], wait=mainfetch.CHROME_GONE_WAIT_S)


def _parser():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("account", choices=sorted(ACCOUNTS) + ["all"], help="the Google account ('all': --status only)")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--attach", type=int, metavar="PORT", help="attach to an already-open Chrome on this debug port")
    g.add_argument("--port", type=int, help="launch this account's Chrome profile on this debug port (not 9222)")
    g.add_argument("--status", action="store_true",
                   help="offline summary of the inventory files: no Chrome, no lock, writes nothing")
    ap.add_argument("--mode", choices=("rpc", "dom"), default="rpc",
                    help="rpc: read-only internal RPCs, minutes (default) · dom: the browser crawl, hours")
    ap.add_argument("--out", default=None, help=f"the inventory folder (default {DEFAULT_OUT})")
    ap.add_argument("--phase", choices=("enumerate", "details", "all"), default="all",
                    help="enumerate: list the ids only · details: detail known ids only (the rpc mode still lists "
                         "the ids first — it is cheap) · all (default)")
    ap.add_argument("--max", type=int, default=None, help="details: stop after N items (smoke runs)")
    return ap


def main(argv=None):
    ap = _parser()
    a = ap.parse_args(argv)
    if a.account == "all" and not a.status:
        ap.error("'all' works with --status only — crawl the accounts one after another")
    out_dir = a.out or mvcommon.GP_INVENTORY_DIR
    if a.status:
        for account in (list(mainfetch.CHROME_PROFILES) if a.account == "all" else [ACCOUNTS[a.account]]):
            print_status(account, out_dir)
        return EXIT_OK

    account = ACCOUNTS[a.account]
    os.makedirs(out_dir, exist_ok=True)
    log = Log(os.path.join(out_dir, f"{account}.log"))
    log(f"=== gp_inventory {account} ({'launch %d' % a.port if a.attach is None else 'attach %d' % a.attach}) "
        f"mode={a.mode} phase={a.phase}")
    try:
        if a.attach is None:
            if a.port == 9222:
                log("STOPPED: use a port other than 9222 when launching (mainfetch and the user's Chrome use 9222).")
                return EXIT_USAGE
            problem = mainfetch.profile_preflight(account)
            if problem:
                log(f"STOPPED: {problem} (nothing was launched).")
                return EXIT_USAGE
        try:
            with crawl_lock() as heartbeat:
                return _session(a, account, out_dir, log, heartbeat)
        except mvcommon.LockHeldError:
            log("REFUSED: the fetch session lock is held — a MediaVault fetch, the profile warm-up or another "
                "inventory crawl is using the browser. Nothing was launched; re-run when it has finished.")
            return EXIT_LOCK
    finally:
        log("=== done")


if __name__ == "__main__":
    sys.exit(main())
